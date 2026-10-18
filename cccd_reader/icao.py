"""Các lệnh chọn ứng dụng, đọc EF và trích xuất ảnh ICAO LDS."""

from __future__ import annotations

from pathlib import Path

from .secure_messaging import SecureMessaging

EMRTD_AID = bytes.fromhex("A0000002471001")
DG1_FID = bytes.fromhex("0101")
DG2_FID = bytes.fromhex("0102")


def select_emrtd_application(connection) -> None:
    command = [0x00, 0xA4, 0x04, 0x0C, len(EMRTD_AID)]
    command.extend(EMRTD_AID)
    command.append(0x00)

    _response, sw1, sw2 = connection.transmit(command)
    if (sw1, sw2) != (0x90, 0x00):
        raise RuntimeError(f"SELECT eMRTD thất bại: SW={sw1:02X}{sw2:02X}")


def _tlv_total_size(header: bytes) -> int:
    if len(header) < 2:
        raise ValueError("Không đọc đủ header TLV của file")

    first_length = header[1]
    if first_length < 0x80:
        header_size = 2
        content_size = first_length
    else:
        length_size = first_length & 0x7F
        if length_size == 0 or length_size > 4 or len(header) < 2 + length_size:
            raise ValueError("Độ dài TLV của file không hợp lệ")
        header_size = 2 + length_size
        content_size = int.from_bytes(header[2:header_size], "big")
    return header_size + content_size


def read_binary_file(
    secure_messaging: SecureMessaging,
    file_id: bytes,
    chunk_size: int = 128,
    progress=None,
) -> bytes:
    """SELECT EF, đọc hết file bằng READ BINARY và trả về TLV nguyên bản."""
    if len(file_id) != 2:
        raise ValueError("File ID phải dài hai byte")
    if not 1 <= chunk_size <= 128:
        raise ValueError("Kích thước chunk phải nằm trong khoảng 1–128 byte")

    secure_messaging.send_apdu(0x00, 0xA4, 0x02, 0x0C, file_id)
    header = secure_messaging.send_apdu(0x00, 0xB0, 0x00, 0x00, le=8)
    file_size = _tlv_total_size(header)

    result = bytearray()
    while len(result) < file_size:
        offset = len(result)
        length = min(chunk_size, file_size - offset)
        chunk = secure_messaging.send_apdu(
            0x00,
            0xB0,
            (offset >> 8) & 0x7F,
            offset & 0xFF,
            le=length,
        )
        if not chunk:
            raise RuntimeError(f"READ BINARY trả dữ liệu rỗng tại offset {offset}")
        result.extend(chunk)
        if progress is not None:
            progress(len(result), file_size)

    return bytes(result[:file_size])


def extract_photo(dg2: bytes, output_dir: Path) -> Path | None:
    """Tìm ảnh JPEG/JPEG 2000 trong DG2 và ghi vào thư mục output."""
    output_dir.mkdir(parents=True, exist_ok=True)

    jpeg_start = dg2.find(b"\xFF\xD8\xFF")
    if jpeg_start >= 0:
        jpeg_end = dg2.rfind(b"\xFF\xD9")
        if jpeg_end >= jpeg_start:
            path = output_dir / "avatar.jpg"
            path.write_bytes(dg2[jpeg_start : jpeg_end + 2])
            return path

    jp2_signature = b"\x00\x00\x00\x0C\x6A\x50\x20\x20"
    jp2_start = dg2.find(jp2_signature)
    if jp2_start >= 0:
        path = output_dir / "avatar.jp2"
        path.write_bytes(dg2[jp2_start:])
        return path

    return None
