# Đọc CCCD gắn chip qua NFC USB

Ứng dụng dùng đầu đọc thẻ PC/SC (đã thử với ACS ACR122U trên Raspberry Pi), xác thực BAC, đọc MRZ trong DG1 và trích ảnh DG2.

## Cài đặt trên Raspberry Pi OS

```bash
sudo apt update
sudo apt install pcscd libpcsclite-dev python3-dev python3-venv swig rsync
sudo systemctl enable --now pcscd
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Web server xem thông tin thẻ

Web app chạy trên Pi và dùng đầu đọc NFC đang cắm USB. Khi quét, trang hiển thị các trường có trong MRZ và ảnh chân dung nếu trình duyệt hỗ trợ định dạng ảnh trên thẻ. BAC yêu cầu MRZ key info gồm đúng 24 ký tự. Có thể nhập mã bằng tay, chụp bằng webcam USB cắm vào Pi, hoặc chọn ảnh mặt sau CCCD; OCR chạy trên Pi, kiểm tra ba check digit ICAO rồi điền mã vào ô để người dùng xem lại trước khi quét NFC. Ảnh webcam chỉ nằm tạm trong thư mục hệ thống khi xử lý và được xóa sau đó. Giữ nguyên ký tự `<`, bỏ khoảng trắng.

Cài Tesseract OCR và `fswebcam` để Pi đọc MRZ bằng webcam USB:

```bash
sudo apt install tesseract-ocr fswebcam
```

Mặc định ứng dụng chụp từ `/dev/video0`. Nếu service báo không có quyền, thêm user `khanh` vào nhóm `video` bằng `sudo usermod -aG video khanh`, sau đó khởi động lại Pi. Có thể chọn thiết bị khác bằng biến `CCCD_CAMERA_DEVICE` trong `~/.config/p3_cccd/web.env`.

Cài web server thành user service để nó tự chạy sau khi Pi khởi động:

```bash
cd ~/p3_cccd
mkdir -p ~/.config/p3_cccd
read -rsp "Mật khẩu trang web: " CCCD_WEB_PASSWORD; printf '\n'
printf 'CCCD_WEB_PASSWORD=%s\n' "$CCCD_WEB_PASSWORD" > ~/.config/p3_cccd/web.env
chmod 600 ~/.config/p3_cccd/web.env
unset CCCD_WEB_PASSWORD
sudo loginctl enable-linger khanh
mkdir -p ~/.config/systemd/user
cp deploy/cccd-web.service ~/.config/systemd/user/cccd-web.service
systemctl --user daemon-reload
systemctl --user enable --now cccd-web.service
systemctl --user status cccd-web.service
```

Mở `http://192.168.1.116:8000` trên thiết bị cùng mạng với Pi; đăng nhập với tên `cccd` và mật khẩu vừa tạo. Nếu IP Pi thay đổi, xem địa chỉ mới bằng `hostname -I`. Trang hiển thị dữ liệu cá nhân, nên chỉ dùng trên mạng tin cậy và không mở port này ra Internet. MRZ key info không được lưu vào trình duyệt hoặc log; ảnh trích xuất được lưu trong `output/avatar.jpg` hoặc `output/avatar.jp2`.

Sau khi service được cài, workflow triển khai sẽ tự khởi động lại web server mỗi lần đồng bộ code lên Pi.

## Đọc thẻ bằng dòng lệnh

```bash
cd ~/p3_cccd
.venv/bin/python read_cccd.py
```

Đặt CCCD lên đầu đọc và nhập MRZ key info khi được hỏi. Khi đọc thành công, chương trình in dữ liệu DG1 và lưu ảnh chân dung. Nếu chưa nhận diện được ảnh, dữ liệu DG2 thô được lưu tại `output/dg2.bin`. Thư mục `output/` được bỏ qua bởi Git.

## Kiểm tra đầu đọc

```bash
.venv/bin/python -m diagnostics.reader_probe
```

Bài kiểm tra xác nhận đầu đọc, chọn được ứng dụng eMRTD và nhận phản hồi từ thẻ. Nó không xác thực BAC hay đọc dữ liệu cá nhân.

## Tự cập nhật Pi khi push lên GitHub

Workflow `.github/workflows/deploy-pi.yml` chạy trên Raspberry Pi mỗi khi có push vào nhánh `main`. Pi cần đang bật và runner cần đang hoạt động; không cần mở cổng trên router. Workflow đồng bộ mã vào `/home/khanh/p3_cccd`, giữ nguyên `.venv/` và `output/`. Nếu user service `cccd-web` đã cài, workflow cũng khởi động lại web server sau khi đồng bộ.

1. Repo GitHub cần ở chế độ private.
2. Cài `rsync` trên Pi: `sudo apt install rsync`.
3. Đăng ký Pi làm self-hosted runner Linux ARM64 trong **Settings → Actions → Runners**.
4. Cài runner thành systemd service để nó tự chạy sau khi Pi khởi động lại.
5. Push commit lên `main`; GitHub Actions đồng bộ code sang Pi.

Runner chạy workflow ngay trên Pi, nên chỉ dùng với repo riêng tư và người có quyền push đáng tin cậy.
