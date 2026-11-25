"""Lưu thông tin CCCD vào Cloud Firestore bằng Firebase Admin SDK."""

from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
import re
import threading
import uuid

_LOCK = threading.Lock()
_FIELDS = (
    "full_name",
    "document_number",
    "sex",
    "date_of_birth",
    "nationality",
    "expiry_date",
    "document_type",
    "issuing_country",
)


def _firebase_app():
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError as exc:
        raise RuntimeError("Chưa cài Firebase Admin SDK. Chạy pip install -r requirements.txt.") from exc

    with _LOCK:
        try:
            app = firebase_admin.get_app()
        except ValueError:
            credential_file = os.environ.get("FIREBASE_SERVICE_ACCOUNT") or os.environ.get(
                "GOOGLE_APPLICATION_CREDENTIALS"
            )
            if credential_file:
                path = Path(credential_file).expanduser()
                if not path.is_file():
                    raise RuntimeError(f"Không tìm thấy Firebase service account: {path}")
                try:
                    credential = credentials.Certificate(str(path))
                except Exception as exc:
                    raise RuntimeError(f"Không đọc được Firebase service account: {exc}") from exc
            else:
                raise RuntimeError(
                    "Chưa cấu hình Firebase. Đặt FIREBASE_SERVICE_ACCOUNT trỏ tới service account JSON."
                )

            options = {}
            project_id = os.environ.get("FIREBASE_PROJECT_ID")
            if project_id:
                options["projectId"] = project_id
            try:
                app = firebase_admin.initialize_app(credential, options)
            except Exception as exc:
                raise RuntimeError(f"Không khởi tạo được Firebase Admin: {exc}") from exc

    return app


def _firestore_client():
    try:
        from firebase_admin import firestore
    except ImportError as exc:
        raise RuntimeError("Chưa cài Firebase Admin SDK. Chạy pip install -r requirements.txt.") from exc
    return firestore.client(app=_firebase_app()), firestore


def _storage_bucket():
    try:
        from firebase_admin import storage
    except ImportError as exc:
        raise RuntimeError("Firebase Admin SDK chưa có hỗ trợ Cloud Storage.") from exc

    bucket_name = os.environ.get("FIREBASE_STORAGE_BUCKET", "").strip()
    if not bucket_name:
        raise RuntimeError("Chưa cấu hình FIREBASE_STORAGE_BUCKET trong web.env.")
    return storage.bucket(bucket_name, app=_firebase_app())


def save_cccd(fields: dict[str, str]) -> str:
    """Ghi các trường đọc được vào Firestore; không lưu MRZ thô hay ảnh khuôn mặt."""
    document_number = (fields.get("document_number") or "").strip().upper()
    if not document_number or document_number == "KHÔNG CÓ DỮ LIỆU":
        raise RuntimeError("CCCD không có số giấy tờ hợp lệ để lưu Firebase.")

    client, firestore = _firestore_client()
    collection = os.environ.get("FIRESTORE_COLLECTION", "cccd_cards")
    record_id = hashlib.sha256(document_number.encode("utf-8")).hexdigest()
    data = {name: fields.get(name, "") for name in _FIELDS}
    data["updated_at"] = firestore.SERVER_TIMESTAMP
    client.collection(collection).document(record_id).set(data, merge=True)
    return record_id


def ensure_firebase_ready() -> None:
    """Fail before reading a card when the SDK or service account is not configured."""
    _firestore_client()


def save_cccd_photo(record_id: str, photo_path: Path) -> None:
    """Tải ảnh DG2 lên bucket riêng tư và lưu đường dẫn trong document CCCD."""
    if not re.fullmatch(r"[0-9a-f]{64}", record_id):
        raise ValueError("Mã CCCD không hợp lệ.")
    path = Path(photo_path)
    if not path.is_file():
        raise RuntimeError("Không tìm thấy ảnh DG2 để tải lên Firebase.")
    content_type = mimetypes.guess_type(path.name)[0] or {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".jp2": "image/jp2",
    }.get(path.suffix.lower(), "application/octet-stream")
    if not content_type.startswith("image/"):
        raise RuntimeError("Định dạng ảnh DG2 không được hỗ trợ.")

    bucket = _storage_bucket()
    client, _firestore = _firestore_client()
    collection = os.environ.get("FIRESTORE_COLLECTION", "cccd_cards")
    document = client.collection(collection).document(record_id)
    previous = document.get().to_dict() or {}
    object_name = f"cccd_photos/{record_id}/{uuid.uuid4().hex}"
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(path), content_type=content_type)

    try:
        document.set({
            "photo_object": object_name,
            "photo_content_type": content_type,
        }, merge=True)
    except Exception:
        try:
            blob.delete()
        except Exception:
            pass
        raise
    previous_object = previous.get("photo_object")
    if isinstance(previous_object, str) and previous_object.startswith(f"cccd_photos/{record_id}/"):
        try:
            bucket.blob(previous_object).delete()
        except Exception:
            pass


def get_cccd_photo(record_id: str) -> tuple[bytes, str] | None:
    """Đọc ảnh DG2 đã lưu; không tạo URL tải công khai."""
    if not re.fullmatch(r"[0-9a-f]{64}", record_id):
        raise ValueError("Mã ảnh CCCD không hợp lệ.")
    client, _firestore = _firestore_client()
    collection = os.environ.get("FIRESTORE_COLLECTION", "cccd_cards")
    snapshot = client.collection(collection).document(record_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    object_name = data.get("photo_object")
    if not object_name:
        return None
    if not isinstance(object_name, str) or not re.fullmatch(
        rf"cccd_photos/{record_id}/[0-9a-f]{{32}}", object_name
    ):
        raise RuntimeError("Đường dẫn ảnh CCCD trong Firestore không hợp lệ.")

    blob = _storage_bucket().blob(object_name)
    if not blob.exists():
        return None
    return blob.download_as_bytes(), data.get("photo_content_type") or blob.content_type or "image/jpeg"


def list_cccd_page(cursor: str | None = None, page_size: int = 25) -> dict:
    """Lấy một trang CCCD đã lưu, mới cập nhật trước; cursor là document ID."""
    if not 1 <= page_size <= 100:
        raise ValueError("Số dòng mỗi trang phải nằm trong khoảng 1 đến 100.")
    if cursor and not re.fullmatch(r"[0-9a-f]{64}", cursor):
        raise ValueError("Cursor lịch sử không hợp lệ.")

    client, firestore = _firestore_client()
    collection = os.environ.get("FIRESTORE_COLLECTION", "cccd_cards")
    collection_ref = client.collection(collection)
    query = collection_ref.order_by("updated_at", direction=firestore.Query.DESCENDING)
    if cursor:
        snapshot = collection_ref.document(cursor).get()
        if not snapshot.exists:
            raise ValueError("Không tìm thấy vị trí tiếp theo. Hãy tải lại danh sách.")
        query = query.start_after(snapshot)

    snapshots = list(query.limit(page_size + 1).stream())
    has_more = len(snapshots) > page_size
    page = snapshots[:page_size]
    fields = (
        "full_name",
        "document_number",
        "sex",
        "date_of_birth",
        "nationality",
        "expiry_date",
        "document_type",
        "issuing_country",
    )
    records = []
    for snapshot in page:
        data = snapshot.to_dict() or {}
        updated_at = data.get("updated_at")
        records.append({
            **{name: data.get(name, "") for name in fields},
            "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else None,
            "photo_id": snapshot.id if data.get("photo_object") else None,
        })

    return {
        "items": records,
        "has_more": has_more,
        "next_cursor": page[-1].id if has_more and page else None,
    }
