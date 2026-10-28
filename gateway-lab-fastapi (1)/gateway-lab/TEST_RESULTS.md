# Kết quả kiểm thử

## Bản cập nhật cảnh báo email — 28/10/2026

**16/16 kiểm thử đạt trong 21.08 giây**: 8 kiểm thử HTTP thật được chạy lại và 8 kiểm thử email/cấu hình/API quản trị mới. Kiểm thử email dùng SMTP giả lập, chưa gửi thư thật và chưa xác nhận hộp thư nhận. Có một cảnh báo deprecation từ thư viện TestClient, không làm test thất bại.

Các kiểm tra mới: chống trùng và cooldown, giới hạn hàng đợi, retry khi SMTP lỗi, STARTTLS/SSL và nội dung thư, đọc cấu hình, khóa API quản trị, circuit OPEN kích hoạt cảnh báo, endpoint thử email khi email tắt.

Phần bên dưới ghi lại kiểm thử của bản gốc; các giới hạn Docker, trình duyệt, Windows và triển khai công khai vẫn còn áp dụng.

Ngày thực hiện: 28/10/2026.

Môi trường: Linux, Python 3.12.14; phiên bản thư viện được khóa trong requirements.txt.

## Kết quả: 8/8 kiểm thử tích hợp đạt

Lệnh: `python -m pytest tests -v`. Thời gian: 18.23 giây.

Mỗi test khởi chạy 4 process Uvicorn độc lập, trao đổi qua HTTP loopback thật. Không mock upstream.

| Kiểm thử | Kết quả |
|---|---|
| Phục vụ HTML, Swagger và chia request theo Round Robin | PASS |
| 15 request đồng thời: 6 mã 200, 9 mã 429; hết cửa sổ được gửi tiếp | PASS |
| 3 lỗi mỗi instance → OPEN; toàn bộ OPEN → 503; probe thành công → CLOSED | PASS |
| Probe HALF-OPEN thất bại → OPEN trở lại | PASS |
| Service phản hồi quá timeout → 504 | PASS |
| Tắt tiến trình service → 502, instance khác vẫn trả 200 | PASS |
| Thiếu khóa điều khiển → 401; dữ liệu sai → 422; ID không tồn tại → 404 | PASS |
| HALF-OPEN chỉ cho phép 1 probe đồng thời trên mỗi instance | PASS |

Các test dùng RATE_WINDOW=0.5, BREAKER_TIMEOUT=0.8 và UPSTREAM_TIMEOUT=0.2 giây để rút ngắn thời gian chạy.

## Kiểm tra thêm với cấu hình mặc định

- Chạy `python run.py` thành công: Gateway 8080 và service 9001–9003 đều trả health 200.
- Xác minh cửa sổ mặc định 3 giây.
- Gửi 7 request: 6 request đầu trả 200; request thứ 7 trả 429.
- Ba request đầu lần lượt đến order-service-1, order-service-2, order-service-3.
- Dừng launcher bằng SIGINT và thu hồi các tiến trình con.
- JavaScript frontend vượt qua `node --check`.

## Phạm vi chưa kiểm chứng

- Chưa chạy Docker Compose do môi trường không có Docker.
- Chưa kiểm thử tương tác hoặc hiển thị trực quan bằng trình duyệt; kiểm thử HTTP chỉ xác nhận frontend được phục vụ.
- Chưa chạy trực tiếp trên Windows; launcher dùng subprocess theo cách đa nền tảng và có lệnh Windows trong README.
- Chưa triển khai lên máy chủ công khai, chưa thử tải dài hạn hoặc nhiều Gateway replica.

Đây là bộ kiểm thử chức năng phục vụ đồ án, không phải chứng nhận hệ thống production.
