"""Các phép mã hóa dùng cho BAC và Secure Messaging kiểu 3DES."""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, modes

try:
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
except ImportError:  # cryptography < 43
    from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES

BLOCK_SIZE = 8
ZERO_IV = bytes(BLOCK_SIZE)


def adjust_des_parity(data: bytes) -> bytes:
    """Đặt parity lẻ cho từng byte của khóa DES."""
    result = bytearray()
    for value in data:
        result.append(value if bin(value).count("1") % 2 else value ^ 1)
    return bytes(result)


def pad_iso7816(data: bytes) -> bytes:
    """Padding ISO/IEC 7816-4: 80 rồi các byte 00 đến hết block."""
    padded = data + b"\x80"
    return padded + bytes((-len(padded)) % BLOCK_SIZE)


def unpad_iso7816(data: bytes) -> bytes:
    """Gỡ padding ISO/IEC 7816-4; báo lỗi nếu padding không hợp lệ."""
    if not data or len(data) % BLOCK_SIZE:
        raise ValueError("Dữ liệu đã giải mã có độ dài không hợp lệ")

    index = len(data) - 1
    while index >= 0 and data[index] == 0:
        index -= 1
    if index < 0 or data[index] != 0x80:
        raise ValueError("Padding ISO 7816-4 không hợp lệ")
    return data[:index]


def _des3_key(key: bytes) -> bytes:
    if len(key) == 16:
        return key + key[:8]
    if len(key) == 24:
        return key
    raise ValueError("Khóa 3DES phải dài 16 hoặc 24 byte")


def triple_des_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    if len(iv) != BLOCK_SIZE or len(data) % BLOCK_SIZE:
        raise ValueError("IV phải dài 8 byte và dữ liệu phải chia hết cho 8")
    cipher = Cipher(
        TripleDES(_des3_key(key)),
        modes.CBC(iv),
    )
    encryptor = cipher.encryptor()
    return encryptor.update(data) + encryptor.finalize()


def triple_des_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    if len(iv) != BLOCK_SIZE or len(data) % BLOCK_SIZE:
        raise ValueError("IV phải dài 8 byte và dữ liệu phải chia hết cho 8")
    cipher = Cipher(
        TripleDES(_des3_key(key)),
        modes.CBC(iv),
    )
    decryptor = cipher.decryptor()
    return decryptor.update(data) + decryptor.finalize()


def retail_mac(key: bytes, data: bytes) -> bytes:
    """Tính ISO/IEC 9797-1 MAC Algorithm 3, padding method 2."""
    if len(key) != 16:
        raise ValueError("Khóa BAC MAC phải dài 16 byte")

    padded = pad_iso7816(data)
    k1, k2 = key[:8], key[8:]

    cipher = Cipher(
        TripleDES(k1 * 3),
        modes.CBC(ZERO_IV),
    )
    encryptor = cipher.encryptor()
    last_block = (encryptor.update(padded) + encryptor.finalize())[-BLOCK_SIZE:]

    cipher = Cipher(
        TripleDES(k2 * 3), modes.ECB()
    )
    decryptor = cipher.decryptor()
    middle = decryptor.update(last_block) + decryptor.finalize()

    cipher = Cipher(
        TripleDES(k1 * 3), modes.ECB()
    )
    encryptor = cipher.encryptor()
    return encryptor.update(middle) + encryptor.finalize()


def derive_bac_keys(mrz_key_info: str) -> tuple[bytes, bytes]:
    """Sinh khóa BAC từ chuỗi MRZ information."""
    try:
        mrz_bytes = mrz_key_info.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("MRZ key chỉ được chứa ký tự ASCII") from exc

    key_seed = hashlib.sha1(mrz_bytes).digest()[:16]
    return _derive_keys(key_seed)


def derive_session_keys(key_seed: bytes) -> tuple[bytes, bytes]:
    return _derive_keys(key_seed)


def _derive_keys(key_seed: bytes) -> tuple[bytes, bytes]:
    enc_seed = hashlib.sha1(key_seed + b"\x00\x00\x00\x01").digest()[:16]
    mac_seed = hashlib.sha1(key_seed + b"\x00\x00\x00\x02").digest()[:16]
    return adjust_des_parity(enc_seed), adjust_des_parity(mac_seed)
