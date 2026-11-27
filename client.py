import os
import select
import socket
import sys
import threading
import time

from server import PeerServer, SharedState, format_size

if os.name == "nt":
    import msvcrt
else:
    import termios
    import tty


class PeerClient:
    """Phần Client: phát hiện diện UDP, giao diện Console chống đè dòng và gửi chat/file."""

    def __init__(self, state: SharedState, server: PeerServer):
        self.state = state
        self.server = server
        self.broadcaster_sock = None

    def start_background_tasks(self):
        threading.Thread(target=self._udp_broadcaster_loop, daemon=True).start()

    def _create_broadcast_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        return sock

    def send_presence_now(self):
        """Gửi ngay một gói hiện diện UDP (dùng khi khởi động hoặc đổi /status)."""
        with self.state.lock:
            payload = f"HELLO|{self.state.name}|{self.state.tcp_port}|{self.state.status}".encode("utf-8")
        if self.broadcaster_sock is None:
            self.broadcaster_sock = self._create_broadcast_socket()
        try:
            self.broadcaster_sock.sendto(payload, (self.state.broadcast_ip, self.state.udp_port))
        except OSError:
            # Dự phòng khi máy không nối mạng LAN/Wi-Fi, thử broadcast loopback
            try:
                self.broadcaster_sock.sendto(payload, ("127.255.255.255", self.state.udp_port))
            except OSError:
                pass

    def _udp_broadcaster_loop(self):
        """Phát gói hiện diện mỗi 3 giây trên cổng UDP cố định."""
        self.broadcaster_sock = self._create_broadcast_socket()
        while self.state.running:
            self.send_presence_now()
            for _ in range(30):
                if not self.state.running:
                    break
                time.sleep(0.1)

    def run_cli(self):
        """Vòng lặp chính đọc bàn phím và thực thi lệnh người dùng."""
        self.state.safe_print(f"[*] Listening on TCP port {self.state.tcp_port}")
        self.state.safe_print(f"[*] Broadcasting presence on UDP port {self.state.udp_port}")
        self.state.safe_print("[*] Type /help for available commands.")

        while self.state.running:
            line = self._read_line_nonblocking_redraw()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            self._dispatch_input(line)

    def _read_line_nonblocking_redraw(self):
        """Đọc từng phím vào input_buffer để không mất chữ khi luồng khác in tin nhắn."""
        if os.name == "nt":
            while self.state.running:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        # Bỏ qua phím mũi tên / phím chức năng
                        if msvcrt.kbhit():
                            msvcrt.getwch()
                        continue
                    if ch == "\x03":  # Ctrl+C
                        self._cmd_quit()
                        return None
                    if ch in ("\r", "\n"):
                        with self.state.screen_lock:
                            line = "".join(self.state.input_buffer)
                            self.state.input_buffer.clear()
                            sys.stdout.write("\n> ")
                            sys.stdout.flush()
                        return line
                    if ch in ("\x08", "\x7f"):  # Backspace
                        with self.state.screen_lock:
                            if self.state.input_buffer:
                                self.state.input_buffer.pop()
                                typed = "".join(self.state.input_buffer)
                                sys.stdout.write(f"\r\033[K> {typed}")
                                sys.stdout.flush()
                        continue
                    if ch.isprintable():
                        with self.state.screen_lock:
                            self.state.input_buffer.append(ch)
                            sys.stdout.write(ch)
                            sys.stdout.flush()
                else:
                    time.sleep(0.01)
            return None
        else:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                while self.state.running:
                    rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if not rlist:
                        continue
                    ch = sys.stdin.read(1)
                    if not ch:
                        continue
                    if ch == "\x03":  # Ctrl+C
                        self._cmd_quit()
                        return None
                    if ch == "\x1b":  # Escape sequence (phím mũi tên)
                        select.select([sys.stdin], [], [], 0.01)
                        while select.select([sys.stdin], [], [], 0.0)[0]:
                            sys.stdin.read(1)
                        continue
                    if ch in ("\r", "\n"):
                        with self.state.screen_lock:
                            line = "".join(self.state.input_buffer)
                            self.state.input_buffer.clear()
                            sys.stdout.write("\n> ")
                            sys.stdout.flush()
                        return line
                    if ch in ("\x7f", "\x08"):  # Backspace
                        with self.state.screen_lock:
                            if self.state.input_buffer:
                                self.state.input_buffer.pop()
                                typed = "".join(self.state.input_buffer)
                                sys.stdout.write(f"\r\033[K> {typed}")
                                sys.stdout.flush()
                        continue
                    if ch.isprintable():
                        with self.state.screen_lock:
                            self.state.input_buffer.append(ch)
                            sys.stdout.write(ch)
                            sys.stdout.flush()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            return None

    def _dispatch_input(self, line):
        """Phân loại lệnh bắt đầu bằng '/' và văn bản chat thông thường."""
        if not line.startswith("/"):
            self._send_chat_message(line)
            return

        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/list":
            self._cmd_list()
        elif cmd == "/chat":
            self._cmd_chat(arg)
        elif cmd == "/send":
            self._cmd_send(arg)
        elif cmd == "/leave":
            self._cmd_leave()
        elif cmd == "/status":
            self._cmd_status(arg)
        elif cmd == "/help":
            self._cmd_help()
        elif cmd == "/quit":
            self._cmd_quit()
        else:
            self.state.safe_print(f"[*] Unknown command '{cmd}'. Type /help for commands.")

    def _get_sorted_peers(self):
        with self.state.lock:
            return sorted(self.state.online_peers.items(), key=lambda item: item[0].lower())

    def _cmd_list(self):
        peers = self._get_sorted_peers()
        if not peers:
            self.state.safe_print("[*] No other users online.")
            return
        lines = []
        for idx, (pname, info) in enumerate(peers, start=1):
            lines.append(f"  {idx}. {pname:<10} ({info['ip']})  {info['status']}")
        self.state.safe_print("\n".join(lines))

    def _cmd_chat(self, target):
        if not target:
            self.state.safe_print("[*] Usage: /chat <name|number>")
            return

        peers = self._get_sorted_peers()
        selected_name = None
        selected_info = None

        if target.isdigit():
            idx = int(target) - 1
            if 0 <= idx < len(peers):
                selected_name, selected_info = peers[idx]
        else:
            for pname, info in peers:
                if pname.lower() == target.lower():
                    selected_name, selected_info = pname, info
                    break

        if not selected_name:
            self.state.safe_print(f"[*] User '{target}' not found or offline.")
            return

        # Kiểm tra xem đã có kết nối chat mở sẵn với peer này chưa
        with self.state.lock:
            existing_sock = self.state.chat_connections.get(selected_name)

        if existing_sock is None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((selected_info["ip"], selected_info["tcp_port"]))
                sock.settimeout(None)
                hi_packet = f"HI|{self.state.name}|{self.state.tcp_port}|".encode("utf-8")
                sock.sendall(hi_packet)
                self.state.register_chat_socket(selected_name, sock)
                threading.Thread(
                    target=self.server.handle_connection,
                    args=(sock, (selected_info["ip"], selected_info["tcp_port"]), selected_name, False),
                    daemon=True,
                ).start()
            except Exception as exc:
                self.state.safe_print(f"[*] Could not connect to {selected_name}: {exc}")
                return

        with self.state.lock:
            self.state.current_partner = selected_name
            unread = self.state.unread_messages.pop(selected_name, [])

        if unread:
            self.state.safe_print(f"[*] Switched to {selected_name} — {len(unread)} unread:")
            for msg in unread:
                self.state.safe_print(f"[{selected_name}] {msg}")
        else:
            self.state.safe_print(
                f"[*] Connected to {selected_name} ({selected_info['ip']}:{selected_info['tcp_port']})"
            )

    def _send_chat_message(self, text):
        with self.state.lock:
            partner = self.state.current_partner
            sock = self.state.chat_connections.get(partner) if partner else None

        if not partner or sock is None:
            self.state.safe_print("[*] No active chat. Use /list and /chat <name|number> first.")
            return

        data = text.encode("utf-8")
        frame = f"MSG|{len(data)}|".encode("utf-8") + data
        try:
            sock.sendall(frame)
            self.state.safe_print(f"[{self.state.name}] {text}")
        except OSError as exc:
            self.state.remove_chat_socket(partner, sock)
            self.state.safe_print(f"[*] Failed to send to {partner}: {exc}")

    def _cmd_send(self, filepath):
        if not filepath:
            self.state.safe_print("[*] Usage: /send <file>")
            return

        with self.state.lock:
            partner = self.state.current_partner
            peer_info = dict(self.state.online_peers[partner]) if partner in self.state.online_peers else None

        if not partner:
            self.state.safe_print("[*] No active chat. Use /chat <name|number> first.")
            return
        if not peer_info:
            self.state.safe_print(f"[*] {partner} is offline.")
            return
        if not os.path.isfile(filepath):
            self.state.safe_print(f"[*] File not found: {filepath}")
            return

        threading.Thread(
            target=self._file_sender_worker,
            args=(partner, peer_info["ip"], peer_info["tcp_port"], filepath),
            daemon=True,
        ).start()

    def _file_sender_worker(self, partner, ip, tcp_port, filepath):
        """Gửi file trên một kết nối TCP và luồng độc lập để không chặn chat."""
        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(5.0)
            sock.connect((ip, tcp_port))
            sock.settimeout(None)

            hi_header = f"HI|{self.state.name}|{self.state.tcp_port}|".encode("utf-8")
            file_header = f"FILE|{filename}|{file_size}|".encode("utf-8")
            sock.sendall(hi_header + file_header)

            sent = 0
            next_milestone = 25
            human_size = format_size(file_size)

            if file_size == 0:
                self.state.safe_print(f"[*] Sending {filename} (0 B) ... 100% Done.")
                return

            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    sock.sendall(chunk)
                    sent += len(chunk)
                    percent = int((sent * 100) / file_size)
                    while next_milestone < 100 and percent >= next_milestone:
                        self.state.safe_print(
                            f"[*] Sending {filename} ({human_size}) ... {next_milestone}%"
                        )
                        next_milestone += 25

            self.state.safe_print(f"[*] Sending {filename} ({human_size}) ... 100% Done.")
        except Exception as exc:
            self.state.safe_print(f"[*] Error sending '{filename}' to {partner}: {exc}")
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _cmd_leave(self):
        with self.state.lock:
            partner = self.state.current_partner
            sock = self.state.chat_connections.get(partner) if partner else None

        if not partner:
            self.state.safe_print("[*] You are not in a chat.")
            return

        if sock:
            try:
                sock.sendall(b"BYE|")
            except OSError:
                pass
        self.state.remove_chat_socket(partner, sock)
        self.state.safe_print(f"[*] Chat with {partner} closed")

    def _cmd_status(self, new_status):
        new_status = new_status.lower()
        if new_status not in ("online", "away"):
            self.state.safe_print("[*] Usage: /status <online|away>")
            return
        with self.state.lock:
            self.state.status = new_status
        self.send_presence_now()
        self.state.safe_print(f"[*] Status changed to {new_status}")

    def _cmd_help(self):
        help_text = (
            "Commands:\n"
            "  /list                  Show online users\n"
            "  /chat <name|number>    Open or switch chat with a user\n"
            "  <any text>             Send message to current chat partner\n"
            "  /send <file>           Send a file to current chat partner\n"
            "  /leave                 Close the current chat\n"
            "  /status <online|away>  Change your status\n"
            "  /help                  Show all commands\n"
            "  /quit                  Exit the program"
        )
        self.state.safe_print(help_text)

    def _cmd_quit(self):
        if not self.state.running:
            return
        self.state.running = False
        with self.state.lock:
            conns = list(self.state.chat_connections.items())
            self.state.chat_connections.clear()
            self.state.current_partner = None

        for _, sock in conns:
            try:
                sock.sendall(b"BYE|")
                sock.close()
            except OSError:
                pass

        self.server.stop()
        if self.broadcaster_sock:
            try:
                self.broadcaster_sock.close()
            except OSError:
                pass
        with self.state.screen_lock:
            sys.stdout.write("\r\033[K[*] Goodbye!\n")
            sys.stdout.flush()