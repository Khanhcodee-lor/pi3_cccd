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

Web app chạy trên Pi và dùng đầu đọc NFC đang cắm USB. Quy trình web tách thành ba màn hình: đọc mã MRZ, quét chip NFC và lưu Firebase, rồi xem thông tin thẻ và xác thực khuôn mặt. Khi quét, trang hiển thị các trường trong MRZ và ảnh chân dung nếu trình duyệt hỗ trợ định dạng ảnh trên thẻ. BAC yêu cầu MRZ key info gồm đúng 24 ký tự. Có thể nhập mã bằng tay, bật webcam USB cắm vào Pi để xem trước và tự nhận diện MRZ, hoặc chọn ảnh mặt sau CCCD. Webcam thử chế độ MJPEG 1280×720 trước, rồi tự dùng cấu hình mặc định của thiết bị nếu chế độ này không được hỗ trợ. OCR chạy trên Pi, kiểm tra ba check digit ICAO rồi điền mã vào ô để người dùng xem lại trước khi quét NFC. Camera tự tắt sau hai phút; ảnh webcam chỉ nằm tạm trong bộ nhớ khi xử lý. Giữ nguyên ký tự `<`, bỏ khoảng trắng.

Cài Tesseract OCR, `ffmpeg` để stream webcam và `fswebcam` cho chức năng chụp ảnh đơn:

```bash
sudo apt install tesseract-ocr ffmpeg fswebcam
```

Mặc định ứng dụng chụp từ `/dev/video0`. Nếu service báo không có quyền, thêm user chạy service (`khanh` trong ví dụ này) vào nhóm `video` bằng `sudo usermod -aG video khanh`, rồi khởi động lại Pi để user service nhận quyền mới. Có thể chọn thiết bị khác bằng biến `CCCD_CAMERA_DEVICE` trong `~/.config/p3_cccd/web.env`.

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

## Lưu CCCD lên Firebase và xác thực để mở cửa

Sau khi quét NFC thành công, server lưu các trường họ tên, số giấy tờ, giới tính, ngày sinh, quốc tịch, ngày hết hạn và loại giấy tờ vào **Cloud Firestore**; ảnh chân dung DG2 được tải lên **Cloud Storage for Firebase**. Firestore chỉ lưu đường dẫn nội bộ tới ảnh, không tạo URL công khai. MRZ thô không được tải lên Firebase. Ảnh vẫn được giữ cục bộ trong `output/` để đối chiếu khuôn mặt.

### Cấu hình Firebase

1. Tạo project Firebase và bật Cloud Firestore.
2. Trong Firebase Console, vào **Databases & Storage → Storage → Get started** để tạo bucket. Cloud Storage for Firebase hiện yêu cầu gói Blaze pay-as-you-go; mức dùng vượt phần miễn phí có thể bị tính phí. Xem [yêu cầu gói và bucket](https://firebase.google.com/docs/storage/faqs-storage-changes-announced-sept-2024?hl=vi).
3. Tạo service account cho Firebase Admin SDK, tải JSON key về Pi và giữ file này ngoài Git. Service account cần quyền đọc/ghi/xóa object trong bucket, ví dụ vai trò **Storage Object Admin**.
4. Cài thư viện trong virtualenv và tải hai model OpenCV Zoo (khoảng 37 MB):

```bash
cd ~/p3_cccd
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m cccd_reader.download_face_models
```

5. Thêm cấu hình vào `~/.config/p3_cccd/web.env`, đặt quyền file chỉ cho user Pi đọc:

```text
FIREBASE_SERVICE_ACCOUNT=/home/khanh/.config/p3_cccd/firebase-service-account.json
FIREBASE_PROJECT_ID=ten-project-firebase
FIREBASE_STORAGE_BUCKET=ten-project-firebase.firebasestorage.app
FIRESTORE_COLLECTION=cccd_cards
CCCD_FACE_THRESHOLD=0.363
CCCD_DOOR_GPIO_PIN=17
CCCD_DOOR_RELAY_ACTIVE_HIGH=false
CCCD_DOOR_UNLOCK_SECONDS=3
```

`FIREBASE_PROJECT_ID` là tùy chọn nếu JSON đã có project ID. Điền đúng tên bucket đang hiện trong Firebase Console, không ghi tiền tố `gs://`; bucket cũ có thể kết thúc bằng `.appspot.com`. Giữ bucket ở chế độ riêng tư; web Pi tải ảnh qua API dùng cùng cơ chế đăng nhập của trang. Nếu chưa đặt bucket, dữ liệu chữ vẫn lưu được và ảnh vẫn dùng được trên Pi để xác thực, nhưng ảnh sẽ chưa xuất hiện trong lịch sử Firebase. Chân relay dùng số BCM; ví dụ `17` là GPIO17. Giá trị mặc định `ACTIVE_HIGH=false` dành cho relay kích mức thấp. Đấu khóa qua module relay phù hợp, không nối tải khóa trực tiếp vào GPIO. Nếu relay của bạn kích mức cao, đổi thành `true`.

Ngưỡng `0.363` là điểm bắt đầu theo demo SFace của OpenCV Zoo; cần hiệu chỉnh bằng camera thực tế và dữ liệu có sự đồng ý trước khi dùng. Ngưỡng cao hơn chặt hơn nhưng có thể từ chối đúng người. Sau khi cấu hình, khởi động lại service:

```bash
chmod 600 ~/.config/p3_cccd/web.env
chmod 600 ~/.config/p3_cccd/firebase-service-account.json
systemctl --user restart cccd-web.service
```

### Luồng hoạt động

1. Đọc CCCD qua NFC và ghi dữ liệu vào Firestore. Nếu Firebase ghi lỗi, lượt quét báo lỗi và không thể xác thực/mở cửa.
2. Bật camera xác thực, nhìn thẳng vào camera rồi chọn **Xác thực và mở cửa**.
3. Server so ảnh khuôn mặt webcam với ảnh DG2 của thẻ vừa quét. Chỉ khi điểm vượt ngưỡng mới phát xung relay; mỗi lượt quét chỉ mở cửa một lần và hết hạn sau 5 phút.

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
