"""Đọc các trường thông tin cơ bản từ MRZ trong DG1."""

from __future__ import annotations

from datetime import date


def _read_tlv_length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise ValueError("DG1 thiếu độ dài MRZ")
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    count = first & 0x7F
    if count == 0 or count > 4 or offset + 1 + count > len(data):
        raise ValueError("Độ dài MRZ trong DG1 không hợp lệ")
    start = offset + 1
    return int.from_bytes(data[start : start + count], "big"), start + count


def extract_mrz(dg1: bytes) -> list[str]:
    """Trích MRZ từ tag 5F1F của DG1 và chia thành các dòng ICAO."""
    tag_offset = dg1.find(b"\x5f\x1f")
    if tag_offset >= 0:
        length, value_offset = _read_tlv_length(dg1, tag_offset + 2)
        raw = dg1[value_offset : value_offset + length]
    else:
        # Một số đầu đọc/trình điều khiển có thể trả phần giá trị TLV đã được gỡ.
        raw = bytes(value for value in dg1 if value in b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ<")

    text = raw.decode("ascii", errors="ignore").upper()
    text = "".join(char for char in text if char in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ<")
    if len(text) == 90:
        width = 30
    elif len(text) == 88:
        width = 44
    elif len(text) == 72:
        width = 36
    else:
        raise ValueError(f"Không nhận diện được độ dài MRZ trong DG1 ({len(text)} ký tự)")
    return [text[index : index + width] for index in range(0, len(text), width)]


def _clean(value: str) -> str:
    return " ".join(part for part in value.strip("<").split("<") if part)


def _format_date(value: str) -> str:
    if len(value) != 6 or not value.isdigit():
        return value
    year_part = int(value[:2])
    current_year = date.today().year % 100
    year = 1900 + year_part if year_part > current_year else 2000 + year_part
    return f"{value[4:6]}/{value[2:4]}/{year:04d}"


def parse_mrz(dg1: bytes) -> tuple[list[str], dict[str, str]]:
    """Trả về các dòng MRZ và những trường dùng để hiển thị trên trang web."""
    lines = extract_mrz(dg1)
    if len(lines) == 3 and len(lines[0]) == 30:
        first, second, name_line = lines
        document_type = _clean(first[:2])
        issuer = _clean(first[2:5])
        document_number = _clean(first[5:14])
        birth = second[:6]
        sex = second[7:8]
        expiry = second[8:14]
        nationality = _clean(second[15:18])
        name = name_line
    elif len(lines) == 2 and len(lines[0]) in (36, 44):
        first, second = lines
        document_type = _clean(first[:2])
        issuer = _clean(first[2:5])
        document_number = _clean(first[5:14])
        nationality = _clean(second[10:13])
        birth = second[13:19]
        sex = second[20:21]
        expiry = second[21:27]
        name = second
    else:
        raise ValueError("Bố cục MRZ trong DG1 không hợp lệ")

    family, _separator, given = name.partition("<<")
    full_name = " ".join(part for part in (_clean(family), _clean(given)) if part)

    sex_label = {"M": "Nam", "F": "Nữ", "X": "Khác"}.get(sex, "Không rõ")
    return lines, {
        "full_name": full_name or "Không có dữ liệu",
        "document_number": document_number or "Không có dữ liệu",
        "date_of_birth": _format_date(birth),
        "sex": sex_label,
        "nationality": nationality or "Không có dữ liệu",
        "expiry_date": _format_date(expiry),
        "document_type": document_type or "Không có dữ liệu",
        "issuing_country": issuer or "Không có dữ liệu",
        "mrz": "\n".join(lines),
    }
