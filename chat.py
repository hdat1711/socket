import argparse

from peer_client import PeerClient
from peer_server import PeerServer, SharedState, enable_windows_ansi


def main():
    parser = argparse.ArgumentParser(description="LAN Chat & File Sharing (P2P Console)")
    parser.add_argument("--name", required=True, help="Tên hiển thị của bạn trong mạng LAN")
    parser.add_argument("--port", type=int, default=5000, help="Cổng TCP lắng nghe (mặc định: 5000)")
    parser.add_argument("--udp-port", type=int, default=9999, help="Cổng UDP khám phá (mặc định: 9999)")
    parser.add_argument(
        "--broadcast",
        default="255.255.255.255",
        help="Địa chỉ UDP broadcast (dùng 127.255.255.255 khi test offline 1 máy)",
    )
    args = parser.parse_args()

    enable_windows_ansi()
    state = SharedState(
        name=args.name,
        tcp_port=args.port,
        udp_port=args.udp_port,
        broadcast_ip=args.broadcast,
    )

    server = PeerServer(state)
    client = PeerClient(state, server)

    server.start()
    client.start_background_tasks()
    client.run_cli()


if __name__ == "__main__":
    main()