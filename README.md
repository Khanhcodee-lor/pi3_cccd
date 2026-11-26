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

Web app chạy trên Pi và dùng đầu đọc NFC đang cắm USB. Thẻ đã đăng ký được nhận diện qua mã NFC; Pi tra MRZ đã mã hóa trong Firebase rồi đọc chip và xác thực khuôn mặt. Thẻ mới cần đăng ký một lần bằng ảnh mặt sau chụp từ điện thoại hoặc nhập MRZ thủ công. OCR chạy trên Pi và kiểm tra check digit ICAO; ảnh điện thoại chỉ được xử lý tạm thời. BAC yêu cầu MRZ key info gồm đúng 24 ký tự; giữ nguyên ký tự `<`, bỏ khoảng trắng.

Cài Picamera2 cho Camera Module v2, Tesseract OCR và công cụ webcam USB:

```bash
sudo apt install python3-picamera2 tesseract-ocr ffmpeg fswebcam
```

Camera CSI dùng Picamera2 mặc định. Với webcam USB, thêm `CCCD_CAMERA_BACKEND=v4l2` và tùy chọn `CCCD_CAMERA_DEVICE=/dev/video0` vào `~/.config/p3_cccd/web.env`. Nếu service báo thiếu quyền truy cập thiết bị, thêm user chạy service (`khanh` trong ví dụ này) vào nhóm `video` bằng `sudo usermod -aG video khanh`, rồi khởi động lại Pi để user service nhận quyền mới.

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

Mở `http://192.168.1.116:8000` trên thiết bị cùng mạng với Pi; đăng nhập với tên `cccd` và mật khẩu vừa tạo. Nếu IP Pi thay đổi, xem địa chỉ mới bằng `hostname -I`. Trang hiển thị dữ liệu cá nhân, nên chỉ dùng trên mạng tin cậy và không mở port này ra Internet. MRZ key info không được lưu trong trình duyệt hoặc log. Firebase lưu MRZ ở dạng mã hóa; khóa giải mã chỉ nằm trên Pi tại `~/.config/p3_cccd/mrz-encryption.key`. Hãy sao lưu an toàn tệp khóa này; mất khóa thì cần đăng ký lại MRZ cho các thẻ. Ảnh trích xuất được lưu trong `output/avatar.jpg` hoặc `output/avatar.jp2`.

Sau khi service được cài, workflow triển khai sẽ tự khởi động lại web server mỗi lần đồng bộ code lên Pi.

## Lưu CCCD lên Firebase và xác thực để mở cửa

Sau khi quét NFC thành công, server lưu các trường họ tên, số giấy tờ, giới tính, ngày sinh, quốc tịch, ngày hết hạn và loại giấy tờ vào **Cloud Firestore**; ảnh chân dung DG2 được tải lên **Cloud Storage for Firebase**. Firestore chỉ lưu đường dẫn nội bộ tới ảnh, không tạo URL công khai. Khi đăng ký thẻ, Firestore lưu mã NFC đã băm và MRZ đã mã hóa trong collection `cccd_card_access`; MRZ dạng chữ thường không được gửi lên Firebase. Khóa AES-GCM được tạo riêng trên Pi và giữ ngoài repo. Ảnh vẫn được giữ cục bộ trong `output/` để đối chiếu khuôn mặt.

### Cấu hình Firebase

1. Tạo project Firebase và bật Cloud Firestore.
2. Trong Firebase Console, vào **Databases & Storage → Storage → Get started** để tạo bucket. Cloud Storage for Firebase hiện yêu cầu gói Blaze pay-as-you-go; mức dùng vượt phần miễn phí có thể bị tính phí. Xem [yêu cầu gói và bucket](https://firebase.google.com/docs/storage/faqs-storage-changes-announced-sept-2024?hl=vi).
3. Tạo service account cho Firebase Admin SDK, tải JSON key về Pi và giữ file này ngoài Git. Service account cần quyền đọc/ghi/xóa object trong bucket, ví dụ vai trò **Storage Object Admin**.
4. Cài thư viện trong virtualenv:

```bash
cd ~/p3_cccd
.venv/bin/pip install -r requirements.txt
```

Khi web server khởi động, ứng dụng tự kiểm tra, tải nếu thiếu và nạp sẵn hai model nhận diện khuôn mặt vào bộ nhớ. Lần tải đầu cần Pi có Internet; các file được giữ trong `models/` và không bị xóa khi deploy code. Có thể chủ động tải trước bằng `.venv/bin/python -m cccd_reader.download_face_models`.

5. Thêm cấu hình vào `~/.config/p3_cccd/web.env`, đặt quyền file chỉ cho user Pi đọc:

```text
FIREBASE_SERVICE_ACCOUNT=/home/khanh/.config/p3_cccd/firebase-service-account.json
FIREBASE_PROJECT_ID=ten-project-firebase
FIREBASE_STORAGE_BUCKET=cccd-pi3.firebasestorage.app
FIRESTORE_COLLECTION=cccd_cards
CCCD_ACCESS_COLLECTION=cccd_card_access
CCCD_FACE_THRESHOLD=0.363
CCCD_DOOR_ACTUATOR=servo
CCCD_DOOR_GPIO_PIN=18
CCCD_DOOR_SERVO_CLOSED_ANGLE=0
CCCD_DOOR_SERVO_OPEN_ANGLE=90
CCCD_DOOR_UNLOCK_SECONDS=3
# Dùng biến này nếu điều khiển relay thay vì servo:
# CCCD_DOOR_ACTUATOR=relay
# CCCD_DOOR_RELAY_ACTIVE_HIGH=false
```

`FIREBASE_PROJECT_ID` là tùy chọn nếu JSON đã có project ID. Mặc định ứng dụng dùng bucket `cccd-pi3.firebasestorage.app`; có thể đổi bằng `FIREBASE_STORAGE_BUCKET` trong `web.env`. Nếu nhập dạng `gs://bucket-name`, ứng dụng tự bỏ tiền tố `gs://`. Giữ bucket ở chế độ riêng tư; web Pi tải ảnh qua API dùng cùng cơ chế đăng nhập của trang. Nếu Firebase Storage chưa được tạo hoặc service account thiếu quyền ghi object, ảnh chưa xuất hiện trong lịch sử Firebase. Với servo, `CCCD_DOOR_GPIO_PIN` là chân tín hiệu theo số BCM; ví dụ BCM18 là chân vật lý 12. Servo quay tới `OPEN_ANGLE`, giữ số giây trong `CCCD_DOOR_UNLOCK_SECONDS`, rồi quay về `CLOSED_ANGLE`. Chỉnh hai góc theo cơ cấu cửa của bạn. Dùng nguồn phù hợp cho servo và nối chung GND với Raspberry Pi. Nếu dùng relay thay servo, đặt `CCCD_DOOR_ACTUATOR=relay` và cấu hình mức kích `CCCD_DOOR_RELAY_ACTIVE_HIGH`.

Khóa MRZ được tạo tự động ở lần đăng ký thẻ đầu tiên; mặc định là `/home/khanh/.config/p3_cccd/mrz-encryption.key`. Tệp có quyền `0600`, không đưa vào Git hay đồng bộ deploy. Giữ nguyên khóa khi cập nhật code; nếu chuyển sang Pi khác, cần chuyển khóa an toàn cùng cấu hình service account.

Ngưỡng `0.363` là điểm bắt đầu theo demo SFace của OpenCV Zoo; cần hiệu chỉnh bằng camera thực tế và dữ liệu có sự đồng ý trước khi dùng. Ngưỡng cao hơn chặt hơn nhưng có thể từ chối đúng người. Sau khi cấu hình, khởi động lại service:

```bash
chmod 600 ~/.config/p3_cccd/web.env
chmod 600 ~/.config/p3_cccd/firebase-service-account.json
systemctl --user restart cccd-web.service
```

### Luồng hoạt động

1. Mở trang web và đưa thẻ đã đăng ký vào đầu đọc. Pi đọc mã NFC, tìm MRZ đã mã hóa trong Firebase, rồi dùng MRZ đó xác thực BAC.
2. Với thẻ mới, trang báo cần đăng ký. Mở cùng địa chỉ web trên điện thoại, chụp/chọn ảnh rõ mặt sau CCCD; Pi đọc MRZ, đọc chip và lưu liên kết mã NFC–MRZ mã hóa. Nếu đầu đọc không lấy được UID của CCCD, giao diện báo rõ rằng không thể tự nhận lại thẻ ở lần sau.
3. Pi lưu thông tin và ảnh DG2, sau đó tự bật camera xác thực khuôn mặt. Khi xác thực xong, đầu đọc tự quay lại chờ thẻ tiếp theo. Mỗi lượt quét chỉ mở cửa một lần và hết hạn sau 5 phút.

Đây là bản đối chiếu khuôn mặt 1:1, chưa có kiểm tra người thật/chống giả mạo; ảnh hoặc video phát lại có thể đánh lừa hệ thống. Không dùng nó làm lớp bảo vệ duy nhất cho khu vực cần an ninh cao. Web server hiện dùng HTTP và Basic Auth, vì vậy chỉ truy cập qua mạng tin cậy, không mở trực tiếp ra Internet.

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
