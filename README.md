# LAN Chat & File Sharing (Peer-to-Peer Console App)

Ứng dụng nhắn tin và chia sẻ tập tin ngang hàng (P2P) hoạt động trong mạng nội bộ (LAN/Wi-Fi), được xây dựng hoàn toàn bằng **thư viện chuẩn Python** (`socket`, `threading`) với giao diện dòng lệnh (Console) chống đè dòng nhập liệu.

## 1. Cấu trúc thư mục

```text
lan-chat-p2p/
├── chat.py           # Tệp khởi chạy chính (nhận tham số dòng lệnh)
├── server.py         # Phần Server: lắng nghe UDP discovery, tiếp nhận kết nối TCP (chat & nhận file)
├── client.py         # Phần Client: phát gói hiện diện UDP, giao diện Console, gửi tin nhắn & file
├── downloads/        # Thư mục tự động tạo để lưu các file nhận được
└── README.md         # Tài liệu hướng dẫn sử dụng và kiểm thử
```

## 2. Yêu cầu hệ thống

* **Python:** Phiên bản `3.8` trở lên.
* **Thư viện bên ngoài:** Không yêu cầu (sử dụng 100% thư viện chuẩn có sẵn của Python).
* **Hệ điều hành:** Hỗ trợ Windows (Command Prompt, PowerShell, Windows Terminal), Linux và macOS.

## 3. Hướng dẫn chạy chương trình

### Trường hợp A: Chạy trên 2 hoặc nhiều máy tính trong cùng mạng LAN / Wi-Fi

Đảm bảo tất cả các máy đều kết nối chung một mạng Wi-Fi hoặc Switch mạng nội bộ.

* **Trên Máy 1 (ví dụ tên `Quan`, cổng TCP `5000`):**
  ```bash
  python chat.py --name Quan --port 5000
  ```

* **Trên Máy 2 (ví dụ tên `Minh`, cổng TCP `6000`):**
  ```bash
  python chat.py --name Minh --port 6000
  ```

* **Trên Máy 3 (ví dụ tên `Huy`, cổng TCP `5000`):**
  ```bash
  python chat.py --name Huy --port 5000
  ```

> **Lưu ý về Tường lửa (Firewall):** Khi chạy lần đầu trên Windows, nếu hộp thoại **Windows Defender Firewall** xuất hiện, hãy tích chọn cả **Private networks** và **Public networks** rồi nhấn **Allow access** để chương trình có thể nhận gói tin UDP broadcast và kết nối TCP từ máy khác.

### Trường hợp B: Chạy kiểm thử nhiều cửa sổ trên cùng 1 máy tính

Bạn có thể mở nhiều cửa sổ Terminal trên cùng một máy tính để kiểm thử bằng cách cấp cho mỗi tiến trình một **tên** và một **cổng TCP khác nhau**:

* **Terminal 1:**
  ```bash
  python chat.py --name Quan --port 5001
  ```

* **Terminal 2:**
  ```bash
  python chat.py --name Minh --port 5002
  ```

* **Terminal 3:**
  ```bash
  python chat.py --name Huy --port 5003
  ```

> **Mẹo khi máy tính không kết nối mạng LAN/Wi-Fi:** Nếu bạn đang ngắt mạng hoàn toàn khi test trên 1 máy, hãy thêm tham số `--broadcast 127.255.255.255`:
> ```bash
> python chat.py --name Quan --port 5001 --broadcast 127.255.255.255
> ```

### Các tham số dòng lệnh hỗ trợ

| Tham số | Bắt buộc | Mặc định | Mô tả |
| :--- | :--- | :--- | :--- |
| `--name` | Có | - | Tên hiển thị của người dùng trong mạng LAN |
| `--port` | Không | `5000` | Cổng TCP lắng nghe kết nối chat và nhận file |
| `--udp-port` | Không | `9999` | Cổng UDP dùng chung để phát và nhận gói hiện diện |
| `--broadcast` | Không | `255.255.255.255` | Địa chỉ IP quảng bá mạng nội bộ |

## 4. Danh sách lệnh trên Console

Mọi dòng chữ không bắt đầu bằng dấu `/` sẽ được gửi trực tiếp dưới dạng tin nhắn cho đối tác đang trò chuyện. Các lệnh điều khiển bao gồm:

| Lệnh | Chức năng | Ví dụ sử dụng |
| :--- | :--- | :--- |
| `/list` | Hiển thị danh sách người dùng đang trực tuyến (STT, tên, IP, trạng thái) | `/list` |
| `/chat <tên\|số>` | Mở hoặc chuyển sang trò chuyện với một người dùng (theo tên hoặc STT) | `/chat Minh` hoặc `/chat 1` |
| `<văn bản>` | Gửi tin nhắn văn bản tới đối tác hiện tại | `Chào Minh, sẵn sàng demo chưa?` |
| `/send <đường_dẫn>` | Gửi một tập tin tới đối tác hiện tại (chạy luồng riêng, có báo `%`) | `/send slides.pdf` |
| `/leave` | Đóng cuộc trò chuyện hiện tại | `/leave` |
| `/status <online\|away>` | Chuyển đổi trạng thái cá nhân giữa `online` và `away` | `/status away` |
| `/help` | Xem danh sách hướng dẫn các lệnh | `/help` |
| `/quit` | Đóng toàn bộ kết nối và thoát chương trình | `/quit` |

## 5. Đặc tả giao thức tầng ứng dụng

Chương trình tự định nghĩa các khung gói tin (Framing) phân tách bằng ký tự `|` và tính độ dài chính xác theo số byte UTF-8:

1. **Khám phá ngang hàng (UDP Port 9999 - phát mỗi 3 giây, timeout 10 giây):**
   ```text
   HELLO|<tên>|<cổng_TCP>|<trạng_thái>
   ```
   *Ví dụ:* `HELLO|Quan|5000|online`

2. **Định danh khi mở kết nối TCP mới:**
   ```text
   HI|<tên>|<cổng_TCP>|
   ```
   *Ví dụ:* `HI|Quan|5000|`

3. **Gửi tin nhắn văn bản (TCP):**
   ```text
   MSG|<số_byte_UTF8>|<nội_dung_tin_nhắn>
   ```
   *Ví dụ:* `MSG|35|Chào Minh, sẵn sàng demo chưa?`

4. **Truyền tập tin (mở một kết nối TCP riêng cho mỗi file):**
   ```text
   FILE|<tên_file>|<kích_thước_byte>|<dữ_liệu_nhị_phân>
   ```
   *Ví dụ:* `FILE|slides.pdf|2516582|<raw bytes>`

5. **Kết thúc cuộc trò chuyện (TCP):**
   ```text
   BYE|
   ```

## 6. Kịch bản kiểm thử và Bắt gói Wireshark

### Kịch bản kiểm thử chức năng & xử lý lỗi

1. **Khám phá & Trạng thái:** Mở chương trình trên 2 máy, kiểm tra thông báo `is online`, dùng `/status away` và `/list` để thấy trạng thái cập nhật ngay lập tức. Tắt 1 máy đột ngột và chờ `10 giây` để kiểm tra thông báo `is offline`.
2. **Chat đa peer & Tin chưa đọc:** Cho 3 người tham gia (Quan, Minh, Huy). Khi Minh đang chat với Huy, Quan gửi tin cho Minh -> màn hình Minh báo `(1 unread)` mà không làm mất chữ Minh đang gõ dở. Minh gõ `/chat Quan` để xem lại toàn bộ tin chưa đọc.
3. **Truyền file song song:** Dùng `/send <file_lớn>` và tiếp tục gõ tin nhắn chat trong lúc phần trăm tiến độ (`25%`, `50%`, `75%`, `100%`) đang chạy. Kiểm tra file hoàn chỉnh trong thư mục `downloads/`.
4. **Ngắt kết nối giữa chừng:** Ngắt Wi-Fi khi đang gửi file ở mức `50%` -> bên nhận tự động xóa file tạm `.part` bị lỗi và báo về giao diện mà không làm sập chương trình.

### Bộ lọc Wireshark phục vụ báo cáo

Khi chạy demo trên 2 máy thật, chọn card mạng Wi-Fi/Ethernet đang kết nối và dùng các bộ lọc sau:

* **Gói khám phá UDP:** `udp.port == 9999`
* **Quá trình bắt tay 3 bước TCP (SYN, SYN-ACK, ACK):** `tcp.port == 6000 && tcp.flags.syn == 1`
* **Luồng dữ liệu Chat & Truyền file:** Chuột phải vào gói TCP bất kỳ -> **Follow** -> **TCP Stream**
* **Tín hiệu đóng kết nối (FIN / RST):** `tcp.flags.fin == 1 || tcp.flags.reset == 1`