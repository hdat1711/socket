import ctypes
import os
import socket
import sys
import threading
import time


def enable_windows_ansi():
    """Bật hỗ trợ mã xóa dòng ANSI (\\033[K) trên Windows Console."""
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass


def format_size(num_bytes):
    """Định dạng dung lượng file dễ đọc."""
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024.0 or unit == "GB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= 1024.0
    return f"{num_bytes} B"


def recv_exact(sock, num_bytes):
    """Đọc đủ chính xác num_bytes từ luồng TCP."""
    chunks = []
    remaining = num_bytes
    while remaining > 0:
        chunk = sock.recv(min(65536, remaining))
        if not chunk:
            raise ConnectionError("Kết nối đóng trước khi nhận đủ dữ liệu.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_until_delim(sock, delim=b"|", max_len=4096):
    """Đọc từng byte cho tới khi gặp ký tự phân tách header."""
    buf = bytearray()
    while True:
        ch = sock.recv(1)
        if not ch:
            if not buf:
                return None
            raise ConnectionError("Kết nối đóng giữa chừng khi đọc header.")
        if ch == delim:
            return buf.decode("utf-8", errors="replace")
        buf.extend(ch)
        if len(buf) > max_len:
            raise ValueError("Header vượt quá độ dài cho phép.")


class SharedState:
    """Trạng thái dùng chung giữa Server và Client, bảo vệ bằng Lock."""

    def __init__(self, name, tcp_port, udp_port=9999, broadcast_ip="255.255.255.255"):
        self.name = name
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.broadcast_ip = broadcast_ip
        self.status = "online"
        self.running = True

        self.lock = threading.Lock()
        self.screen_lock = threading.Lock()

        # peer_name -> {"ip": str, "tcp_port": int, "status": str, "last_seen": float}
        self.online_peers = {}
        # peer_name -> socket TCP đang mở cho chat
        self.chat_connections = {}
        # peer_name -> list[str] tin nhắn chưa đọc
        self.unread_messages = {}
        self.current_partner = None

        # Bộ đệm dòng nhập hiện tại để vẽ lại khi có tin nhắn đến
        self.input_buffer = []

    def safe_print(self, message):
        """Xóa dòng đang gõ, in thông báo mới, rồi vẽ lại prompt và chữ đang gõ."""
        with self.screen_lock:
            typed = "".join(self.input_buffer)
            sys.stdout.write(f"\r\033[K{message}\n> {typed}")
            sys.stdout.flush()

    def register_chat_socket(self, peer_name, sock):
        """Lưu kết nối chat cho peer; đóng kết nối cũ nếu bị trùng."""
        old_sock = None
        with self.lock:
            old_sock = self.chat_connections.get(peer_name)
            if old_sock is not None and old_sock is not sock:
                self.chat_connections[peer_name] = sock
            else:
                self.chat_connections[peer_name] = sock
                old_sock = None
        if old_sock:
            try:
                old_sock.close()
            except OSError:
                pass

    def remove_chat_socket(self, peer_name, sock=None):
        """Gỡ kết nối chat khi đóng hoặc mất kết nối."""
        was_current = False
        target = None
        with self.lock:
            existing = self.chat_connections.get(peer_name)
            if existing is not None and (sock is None or existing is sock):
                target = self.chat_connections.pop(peer_name)
                if self.current_partner == peer_name:
                    self.current_partner = None
                    was_current = True
        if target:
            try:
                target.close()
            except OSError:
                pass
        return was_current


class PeerServer:
    """Phần Server: lắng nghe khám phá UDP và tiếp nhận các kết nối TCP."""

    def __init__(self, state: SharedState):
        self.state = state
        self.udp_sock = None
        self.tcp_sock = None

    def start(self):
        threading.Thread(target=self._udp_listener_loop, daemon=True).start()
        threading.Thread(target=self._tcp_acceptor_loop, daemon=True).start()

    def stop(self):
        for sock in (self.udp_sock, self.tcp_sock):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

    def _udp_listener_loop(self):
        """Nhận gói HELLO qua UDP và xóa peer quá 10 giây không phản hồi."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("", self.state.udp_port))
        sock.settimeout(1.0)
        self.udp_sock = sock

        while self.state.running:
            try:
                data, addr = sock.recvfrom(1024)
                msg = data.decode("utf-8", errors="replace").strip()
                parts = msg.split("|")
                if len(parts) == 4 and parts[0] == "HELLO":
                    peer_name = parts[1].strip()
                    peer_tcp_port = int(parts[2])
                    peer_status = parts[3].strip()

                    # Bỏ qua gói tin do chính tiến trình này phát ra
                    if peer_name == self.state.name and peer_tcp_port == self.state.tcp_port:
                        continue

                    now = time.time()
                    is_new = False
                    with self.state.lock:
                        if peer_name not in self.state.online_peers:
                            is_new = True
                        self.state.online_peers[peer_name] = {
                            "ip": addr[0],
                            "tcp_port": peer_tcp_port,
                            "status": peer_status,
                            "last_seen": now,
                        }
                    if is_new:
                        self.state.safe_print(f"[*] {peer_name} is online")
            except socket.timeout:
                pass
            except OSError:
                if not self.state.running:
                    break
            except ValueError:
                pass

            # Kiểm tra timeout 10 giây
            now = time.time()
            offline_names = []
            with self.state.lock:
                for pname, info in list(self.state.online_peers.items()):
                    if now - info["last_seen"] > 10.0:
                        del self.state.online_peers[pname]
                        offline_names.append(pname)
            for pname in offline_names:
                self.state.safe_print(f"[*] {pname} is offline")

    def _tcp_acceptor_loop(self):
        """Lắng nghe kết nối TCP mới và tạo luồng nhận riêng cho từng kết nối."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.state.tcp_port))
        srv.listen(16)
        self.tcp_sock = srv

        while self.state.running:
            try:
                conn, addr = srv.accept()
                threading.Thread(
                    target=self.handle_connection,
                    args=(conn, addr, None, True),
                    daemon=True,
                ).start()
            except OSError:
                if not self.state.running:
                    break

    def handle_connection(self, conn, addr, known_peer=None, expect_hi=True):
        """Xử lý dữ liệu đến trên một kết nối TCP (chat hoặc nhận file)."""
        peer_name = known_peer
        is_chat_conn = not expect_hi
        try:
            if expect_hi:
                first_type = recv_until_delim(conn)
                if first_type != "HI":
                    raise ValueError("Gói mở đầu không hợp lệ (thiếu HI).")
                peer_name = recv_until_delim(conn)
                peer_port_str = recv_until_delim(conn)
                if not peer_name or not peer_port_str:
                    raise ValueError("Thiếu thông tin định danh trong gói HI.")
                peer_tcp_port = int(peer_port_str)
                with self.state.lock:
                    if peer_name not in self.state.online_peers:
                        self.state.online_peers[peer_name] = {
                            "ip": addr[0],
                            "tcp_port": peer_tcp_port,
                            "status": "online",
                            "last_seen": time.time(),
                        }

            while self.state.running:
                msg_type = recv_until_delim(conn)
                if msg_type is None:
                    # Đối tác đóng kết nối
                    if is_chat_conn and peer_name:
                        was_current = self.state.remove_chat_socket(peer_name, conn)
                        if was_current:
                            self.state.safe_print(f"[*] {peer_name} disconnected.")
                    break

                if msg_type == "MSG":
                    if not is_chat_conn and peer_name:
                        is_chat_conn = True
                        self.state.register_chat_socket(peer_name, conn)

                    length_str = recv_until_delim(conn)
                    length = int(length_str)
                    payload = recv_exact(conn, length).decode("utf-8", errors="replace")
                    self._on_chat_message(peer_name, payload)

                elif msg_type == "FILE":
                    raw_name = recv_until_delim(conn) or "received_file"
                    filename = os.path.basename(raw_name) or "received_file"
                    file_size = int(recv_until_delim(conn))
                    self._on_file_receive(conn, peer_name, filename, file_size)
                    break  # Mỗi file chạy trên một kết nối TCP riêng và đóng sau khi xong

                elif msg_type == "BYE":
                    if peer_name:
                        was_current = self.state.remove_chat_socket(peer_name, conn)
                        if was_current:
                            self.state.safe_print(f"[*] {peer_name} left the chat")
                        else:
                            self.state.safe_print(f"[*] {peer_name} closed the chat")
                    break

                else:
                    raise ValueError(f"Loại gói tin không hỗ trợ: {msg_type}")

        except Exception as exc:
            if is_chat_conn and peer_name:
                was_current = self.state.remove_chat_socket(peer_name, conn)
                if was_current and self.state.running:
                    self.state.safe_print(f"[*] Connection with {peer_name} lost ({exc}).")
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _on_chat_message(self, sender, text):
        """Hiển thị tin nhắn nếu đang chat với sender, ngược lại lưu thành chưa đọc."""
        unread_count = 0
        display_now = False
        auto_switched = False

        with self.state.lock:
            if self.state.current_partner == sender:
                display_now = True
            elif self.state.current_partner is None:
                # Đang rảnh: tự đặt người gọi làm đối tác hiện tại
                self.state.current_partner = sender
                display_now = True
                auto_switched = True
            else:
                self.state.unread_messages.setdefault(sender, []).append(text)
                unread_count = len(self.state.unread_messages[sender])

        if auto_switched:
            self.state.safe_print(f"[*] Connected with {sender}")
        if display_now:
            self.state.safe_print(f"[{sender}] {text}")
        else:
            self.state.safe_print(f"[*] New message from {sender} ({unread_count} unread)")

    def _on_file_receive(self, conn, sender, filename, file_size):
        """Nhận file qua luồng byte TCP, lưu tạm .part và xóa nếu đứt kết nối."""
        os.makedirs("downloads", exist_ok=True)
        final_path = os.path.join("downloads", filename)
        part_path = final_path + ".part"
        remaining = file_size

        try:
            with open(part_path, "wb") as f:
                while remaining > 0:
                    chunk = conn.recv(min(65536, remaining))
                    if not chunk:
                        raise ConnectionError("Mất kết nối giữa chừng khi nhận file.")
                    f.write(chunk)
                    remaining -= len(chunk)
            os.replace(part_path, final_path)
            self.state.safe_print(
                f"[*] Received '{filename}' ({format_size(file_size)}) from {sender} -> {final_path}"
            )
        except Exception as exc:
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
            if self.state.running:
                self.state.safe_print(
                    f"[*] File transfer from {sender} failed ({exc}); partial file deleted."
                )