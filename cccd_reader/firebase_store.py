"""Lưu thông tin CCCD vào Cloud Firestore bằng Firebase Admin SDK."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import threading

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


def _firestore_client():
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
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

    return firestore.client(app=app), firestore


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
