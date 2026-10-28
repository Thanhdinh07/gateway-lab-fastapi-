# Cảnh báo lỗi qua email

## Có gì mới?

Khi một instance có 3 lỗi liên tiếp (500, mất kết nối 502, hoặc timeout 504), Circuit Breaker mở và Gateway đưa một email vào hàng chờ gửi nền. Probe HALF-OPEN thất bại cũng kích hoạt cảnh báo, nhưng cùng một instance chỉ được xếp email tối đa một lần mỗi 300 giây mặc định. Một lỗi đơn lẻ, request 429 hoặc API cập nhật trạng thái trả 200 không gửi thư.

Email gồm tên service, thời gian UTC, request ID, mã HTTP và trạng thái Circuit OPEN. Không đưa nội dung đơn hàng hoặc thông tin SMTP vào email hay API trạng thái.

Mặc định email TẮT. Mã nguồn đã tích hợp; cần tài khoản SMTP và địa chỉ nhận của bạn mới gửi được thư thật.

## 1. Cập nhật mã nguồn

Dừng bản cũ bằng Ctrl+C. Giải nén bản mới vào thư mục riêng để tránh bỏ sót file. Thư mục `gateway-lab` bên trong chứa `run.py`, `requirements.txt` và `.env.example`.

Mở PowerShell ngay tại thư mục **có file run.py**. Nếu vẫn đang ở thư mục ngoài, chạy `cd .\gateway-lab` trước.

```powershell
Copy-Item .env.example .env
notepad .env
```

Không chạy Copy-Item lại nếu đã có `.env` chứa cấu hình thật; chỉnh trực tiếp file hiện có. Không lưu thành `.env.txt`.

## 2. Điền thông tin email

Ví dụ cho tài khoản Gmail có hỗ trợ App Password:

```dotenv
ALERT_EMAIL_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=dia-chi-gui@gmail.com
SMTP_PASSWORD=mat-khau-ung-dung-cua-ban
SMTP_FROM=dia-chi-gui@gmail.com
ALERT_EMAIL_TO=dia-chi-nhan@example.com
ALERT_COOLDOWN_SECONDS=300
CONTROL_KEY=local-demo-key
```

- SMTP_USERNAME và SMTP_FROM: cùng địa chỉ gửi.
- ALERT_EMAIL_TO: một địa chỉ nhận, có thể chính là email của bạn.
- SMTP_PASSWORD: mật khẩu ứng dụng do nhà cung cấp cấp; không dùng mật khẩu đăng nhập Gmail thông thường. Bật xác minh 2 bước nếu tài khoản yêu cầu. Một số tài khoản Google không có lựa chọn App Password; khi đó cần nhà cung cấp SMTP khác hoặc bổ sung OAuth, không tắt bảo mật tài khoản.
- Với Gmail, nhập App Password liền nhau, không kèm khoảng trắng phân nhóm.
- Nhà cung cấp khác: thay host, cổng, username, mật khẩu theo thông số của họ. Hỗ trợ `starttls` và `ssl`, không hỗ trợ SMTP truyền mật khẩu không mã hóa.
- Chỉ nhập mật khẩu trên máy của bạn. Không gửi `.env` vào chat hay đưa lên Git. `.env` không được đóng gói trong bản phát hành và không được chép vào Docker image.
- File `.env` hỗ trợ KEY=value và giá trị đặt trong dấu nháy; không hỗ trợ biến lồng nhau hoặc comment cuối dòng. Biến môi trường có sẵn được ưu tiên.

Tài liệu nhà cung cấp:
- App Password: https://support.google.com/mail/answer/185833
- Gmail SMTP: https://developers.google.com/workspace/gmail/imap/imap-smtp
- Python SMTP/TLS: https://docs.python.org/3.12/library/smtplib.html

## 3. Khởi động

Nếu `.venv` của Dĩnh vẫn ở thư mục ngoài, như lần chạy trước:

```powershell
..\.venv\Scripts\python.exe run.py
```

Nếu cài mới hoàn toàn, từ thư mục có `run.py`:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

Log `Email alerts: enabled` xác nhận đã bật cấu hình, **chưa chứng minh gửi được thư**. Sửa `.env` phải dừng và chạy lại.

Docker Compose đọc `.env` và chỉ chuyển thông tin SMTP vào container Gateway. Chạy `docker compose up --build` sau khi cấu hình. Các Order Service không cần thông tin SMTP.

## 4. Thử gửi một email

Mở http://127.0.0.1:8080/docs:

1. Chọn **POST /api/alerts/test → Try it out**.
2. Điền `x-control-key` bằng CONTROL_KEY trong `.env`.
3. Bấm Execute.
4. Mã **202**, `status: queued` nghĩa là đã xếp hàng, chưa chắc đã đến hộp thư.
5. Chọn **GET /api/alerts/status**, điền cùng khóa, Execute.
6. `smtp_accepted` tăng nghĩa là máy chủ SMTP đã nhận thư; kiểm tra cả Inbox và Spam của địa chỉ nhận. SMTP chấp nhận không bảo đảm thư tới Inbox.

Không có endpoint thay địa chỉ nhận từ trình duyệt; địa chỉ chỉ lấy từ cấu hình quản trị.

| Kết quả | Ý nghĩa / xử lý |
|---|---|
| 401 | Sai hoặc thiếu khóa điều khiển |
| 409 | Email chưa bật; sửa `.env`, chạy lại |
| 429 ở `/api/alerts/test` | Email thử đang chờ hoặc trong 5 phút giới hạn; đợi rồi thử lại |
| 503 ở `/api/alerts/test` | Hàng đợi đầy |
| `failed` tăng, `last_error=SMTPAuthenticationError` | Kiểm tra tài khoản và App Password |
| `last_error=TimeoutError`/`OSError` | Kiểm tra mạng, SMTP_HOST, cổng và firewall |
| `last_error=SSLCertVerificationError` | Kiểm tra cấu hình TLS/chứng chỉ; không tắt xác minh TLS |

Hệ thống thử tối đa 3 lần cho mỗi thư rồi ghi nhận thất bại; lỗi gửi email không làm request người dùng thất bại. Khi SMTP có phản hồi không rõ ràng, retry có thể tạo thư trùng; giới hạn 5 phút áp dụng cho việc xếp hàng theo từng service.

## 5. Thử cảnh báo thực tế

1. Gửi email thử thành công trước.
2. Bật “Gây lỗi” ở instance 1.
3. Gửi request chậm rãi để tránh rate limit; cần 3 request được chuyển đến instance 1 cùng thất bại. Vì Round Robin, thường cần khoảng 9 request tổng nếu tất cả instance ban đầu đều CLOSED.
4. Khi nhật ký xuất hiện `order-service-1: OPEN`, kiểm tra email cảnh báo.
5. Tắt gây lỗi, đặt delay về 100ms, đợi ít nhất 6 giây rồi gửi request để phục hồi.

Email thử có giới hạn riêng, không chặn cảnh báo service. Không tự gửi email phục hồi trong phiên bản này.

## Giới hạn khi dùng thực tế

- Đây là bổ sung cảnh báo email; chưa đưa ứng dụng lên Internet, chưa thêm database hoặc đăng nhập.
- Gateway chỉ phát hiện lỗi khi có request. Nếu toàn bộ máy chủ/Gateway dừng, chính nó không thể gửi cảnh báo; muốn phát hiện trường hợp đó cần dịch vụ giám sát chạy bên ngoài.
- Hàng đợi, giới hạn 5 phút và bộ đếm nằm trong bộ nhớ, mất khi khởi động lại; chỉ chạy một Gateway worker. Chưa có hàng đợi bền vững và bảo đảm giao thư.
- Cảnh báo được gửi tới hộp thư, có thể xem trên máy tính/điện thoại. Máy chủ chạy ứng dụng cần Internet; máy của người nhận không cần bật liên tục.
- Khi triển khai công khai, phải thêm HTTPS, xác thực/phân quyền; thay khóa mặc định, hạn chế API quản trị gây lỗi và API thử email. Bản đi kèm vẫn chỉ bind ra localhost.

## Kiểm thử đã làm

Bộ kiểm thử tự động dùng SMTP giả lập, không gửi thư cho người thật. Kiểm tra xếp hàng, chống trùng, TLS trước login, retry, bảo vệ API bằng khóa, cảnh báo khi circuit mở và chế độ tắt. Kết quả chi tiết trong TEST_RESULTS.md.
