# Đọc CCCD gắn chip qua NFC USB

Ứng dụng dùng đầu đọc thẻ PC/SC (đã thử với ACS ACR122U trên Raspberry Pi) để xác thực BAC, đọc DG1 và trích ảnh DG2.

## Cài đặt trên Raspberry Pi OS

```bash
sudo apt update
sudo apt install pcscd libpcsclite-dev python3-dev python3-venv swig
sudo systemctl enable --now pcscd
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Nếu dự án nằm trong thư mục `~/p3_cccd`, chạy:

```bash
cd ~/p3_cccd
.venv/bin/python read_cccd.py
```

Đặt CCCD lên đầu đọc và giữ yên. Khi được hỏi, nhập MRZ key info gồm đúng 24 ký tự; nội dung nhập không hiện trên màn hình. Chuỗi này được ghép liền từ số giấy tờ và số kiểm tra, ngày sinh dạng `YYMMDD` và số kiểm tra, ngày hết hạn dạng `YYMMDD` và số kiểm tra trên vùng MRZ. Giữ nguyên ký tự `<` nếu có, bỏ khoảng trắng.

Khi đọc thành công, chương trình in dữ liệu DG1 và lưu ảnh chân dung thành `output/avatar.jpg` (hoặc `output/avatar.jp2`). Nếu chưa nhận diện được ảnh, dữ liệu DG2 thô được lưu tại `output/dg2.bin`. Thư mục đầu ra được bỏ qua bởi Git.

## Kiểm tra đầu đọc

```bash
.venv/bin/python -m diagnostics.reader_probe
```

Bài kiểm tra xác nhận đầu đọc, chọn được ứng dụng eMRTD và nhận phản hồi từ thẻ. Nó không xác thực BAC hay đọc dữ liệu cá nhân.

## Tự cập nhật Pi khi push lên GitHub

Workflow `.github/workflows/deploy-pi.yml` chạy trên Raspberry Pi mỗi khi có push vào nhánh `main`. Pi cần đang bật và runner cần đang hoạt động; không cần mở cổng trên router. Workflow đồng bộ mã vào `/home/khanh/p3_cccd`, giữ nguyên `.venv/` và `output/`.

1. Tạo **private repository** trên GitHub và đẩy dự án lên nhánh `main`.
2. Trên Pi, cài rsync: `sudo apt install rsync`.
3. Trong repo GitHub, vào **Settings → Actions → Runners → New self-hosted runner**, chọn Linux ARM64.
4. Trên Pi, chạy các lệnh GitHub hướng dẫn để tải runner và đăng ký nó cho repo. Cài runner thành systemd service để nó tự chạy sau khi Pi khởi động lại.
5. Đẩy commit mới lên `main`; GitHub Actions sẽ đồng bộ code sang Pi.

Runner chạy workflow ngay trên Pi, nên chỉ dùng với repo riêng tư và người có quyền push đáng tin cậy. Repo này chưa được tạo hoặc nối với GitHub; cần tạo repo và đăng ký runner trước khi tự cập nhật hoạt động.
