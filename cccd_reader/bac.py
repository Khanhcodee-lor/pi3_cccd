"""Thiết lập Basic Access Control (BAC) với thẻ ICAO."""

import hmac
import os

from .crypto import (
    ZERO_IV,
    derive_bac_keys,
    derive_session_keys,
    retail_mac,
    triple_des_cbc_decrypt,
    triple_des_cbc_encrypt,
)
from .secure_messaging import SecureMessaging


def perform_bac(connection, mrz_key_info: str) -> SecureMessaging:
    """Xác thực BAC, kiểm tra phản hồi thẻ và trả về kênh an toàn."""
    mrz_key_info = mrz_key_info.strip().upper()
    if len(mrz_key_info) != 24 or any(
        char not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ<" for char in mrz_key_info
    ):
        raise ValueError("MRZ key info phải gồm đúng 24 ký tự ICAO")

    k_enc, k_mac = derive_bac_keys(mrz_key_info)

    rnd_icc, sw1, sw2 = connection.transmit([0x00, 0x84, 0x00, 0x00, 0x08])
    if (sw1, sw2) != (0x90, 0x00) or len(rnd_icc) != 8:
        raise RuntimeError(f"GET CHALLENGE thất bại: SW={sw1:02X}{sw2:02X}")
    rnd_icc = bytes(rnd_icc)

    rnd_ifd = os.urandom(8)
    k_ifd = os.urandom(16)
    plaintext = rnd_ifd + rnd_icc + k_ifd
    encrypted_ifd = triple_des_cbc_encrypt(k_enc, ZERO_IV, plaintext)
    mac_ifd = retail_mac(k_mac, encrypted_ifd)

    auth_data = encrypted_ifd + mac_ifd
    command = [0x00, 0x82, 0x00, 0x00, len(auth_data)] + list(auth_data) + [0x28]
    response, sw1, sw2 = connection.transmit(command)
    if (sw1, sw2) != (0x90, 0x00) or len(response) != 40:
        raise RuntimeError(f"EXTERNAL AUTHENTICATE thất bại: SW={sw1:02X}{sw2:02X}")

    encrypted_icc = bytes(response[:32])
    mac_icc = bytes(response[32:40])
    if not hmac.compare_digest(mac_icc, retail_mac(k_mac, encrypted_icc)):
        raise ValueError("MAC BAC do thẻ trả về không hợp lệ")

    plaintext_icc = triple_des_cbc_decrypt(k_enc, ZERO_IV, encrypted_icc)
    if plaintext_icc[:8] != rnd_icc or plaintext_icc[8:16] != rnd_ifd:
        raise ValueError("RND.ICC hoặc RND.IFD không khớp trong phản hồi BAC")

    k_icc = plaintext_icc[16:32]
    session_seed = bytes(left ^ right for left, right in zip(k_ifd, k_icc))
    ks_enc, ks_mac = derive_session_keys(session_seed)
    ssc = int.from_bytes(rnd_icc[-4:] + rnd_ifd[-4:], "big")
    return SecureMessaging(connection, ks_enc, ks_mac, ssc)
