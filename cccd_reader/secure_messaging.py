"""Mã hóa và giải mã APDU bằng Secure Messaging BAC/3DES."""

from __future__ import annotations

import hmac

from .crypto import (
    ZERO_IV,
    pad_iso7816,
    retail_mac,
    triple_des_cbc_decrypt,
    triple_des_cbc_encrypt,
    unpad_iso7816,
)


def _encode_tlv(tag: int, value: bytes) -> bytes:
    length = len(value)
    if length < 0x80:
        encoded_length = bytes([length])
    elif length <= 0xFF:
        encoded_length = bytes([0x81, length])
    elif length <= 0xFFFF:
        encoded_length = bytes([0x82, length >> 8, length & 0xFF])
    else:
        raise ValueError("TLV quá dài")
    return bytes([tag]) + encoded_length + value


def _read_tlv(data: bytes, offset: int) -> tuple[int, bytes, bytes, int]:
    start = offset
    if offset + 2 > len(data):
        raise ValueError("TLV bị thiếu header")

    tag = data[offset]
    offset += 1
    length_byte = data[offset]
    offset += 1

    if length_byte & 0x80:
        length_size = length_byte & 0x7F
        if length_size == 0 or offset + length_size > len(data):
            raise ValueError("Độ dài TLV không hợp lệ")
        length = int.from_bytes(data[offset : offset + length_size], "big")
        offset += length_size
    else:
        length = length_byte

    end = offset + length
    if end > len(data):
        raise ValueError("Dữ liệu TLV bị cắt cụt")
    return tag, data[offset:end], data[start:end], end


class SecureMessaging:
    """Kênh APDU bảo vệ bằng khóa phiên BAC."""

    def __init__(self, connection, ks_enc: bytes, ks_mac: bytes, ssc: int):
        self.connection = connection
        self.ks_enc = ks_enc
        self.ks_mac = ks_mac
        self.ssc = ssc

    def send_apdu(
        self,
        cla: int,
        ins: int,
        p1: int,
        p2: int,
        data: bytes = b"",
        le: int = 0,
    ) -> bytes:
        """Gửi APDU có DO87/DO97/DO8E và trả về dữ liệu đã giải mã."""
        if not 0 <= le <= 256:
            raise ValueError("Le phải nằm trong khoảng 0–256")

        self.ssc = (self.ssc + 1) & 0xFFFFFFFFFFFFFFFF
        ssc_bytes = self.ssc.to_bytes(8, "big")
        data_objects = bytearray()

        if data:
            padded = pad_iso7816(data)
            # Với 3DES sau BAC, IV là zero; SSC chỉ tham gia tính MAC.
            encrypted = triple_des_cbc_encrypt(self.ks_enc, ZERO_IV, padded)
            data_objects.extend(_encode_tlv(0x87, b"\x01" + encrypted))

        if le:
            data_objects.extend(_encode_tlv(0x97, bytes([le & 0xFF])))

        header = bytes([cla | 0x0C, ins, p1, p2])
        mac_input = ssc_bytes + pad_iso7816(header) + bytes(data_objects)
        data_objects.extend(_encode_tlv(0x8E, retail_mac(self.ks_mac, mac_input)))

        if len(data_objects) > 0xFF:
            raise ValueError("APDU vượt giới hạn short APDU")
        apdu = list(header) + [len(data_objects)] + list(data_objects) + [0x00]
        response, sw1, sw2 = self.connection.transmit(apdu)
        if (sw1, sw2) != (0x90, 0x00):
            raise RuntimeError(f"APDU Secure Messaging thất bại: SW={sw1:02X}{sw2:02X}")

        self.ssc = (self.ssc + 1) & 0xFFFFFFFFFFFFFFFF
        response_bytes = bytes(response)
        response_data = bytearray()
        received_mac = None
        inner_status = None
        offset = 0

        while offset < len(response_bytes):
            tag, value, encoded, offset = _read_tlv(response_bytes, offset)
            if tag == 0x87:
                if not value or value[0] != 0x01:
                    raise ValueError("DO87 không có padding indicator 01")
                plaintext = triple_des_cbc_decrypt(
                    self.ks_enc, ZERO_IV, value[1:]
                )
                response_data.extend(unpad_iso7816(plaintext))
            elif tag == 0x85:
                response_data.extend(value)
            elif tag == 0x99:
                if len(value) != 2:
                    raise ValueError("DO99 phải chứa đúng hai byte trạng thái")
                inner_status = int.from_bytes(value, "big")
            elif tag == 0x8E:
                if len(value) != 8:
                    raise ValueError("DO8E phải chứa MAC 8 byte")
                received_mac = value
            else:
                raise ValueError(f"Data Object Secure Messaging lạ: {tag:02X}")

        if received_mac is None:
            raise ValueError("Phản hồi thiếu DO8E")

        # SSC hiện tại là SSC dành cho phản hồi. MAC bao gồm các DO trước DO8E.
        # Thu thập lại nguyên văn các DO để giữ nguyên encoding BER của thẻ.
        mac_objects = bytearray()
        offset = 0
        while offset < len(response_bytes):
            tag, _value, encoded, next_offset = _read_tlv(response_bytes, offset)
            if tag == 0x8E:
                break
            mac_objects.extend(encoded)
            offset = next_offset

        response_mac_input = self.ssc.to_bytes(8, "big") + bytes(mac_objects)
        expected_mac = retail_mac(self.ks_mac, response_mac_input)
        if not hmac.compare_digest(received_mac, expected_mac):
            raise ValueError("MAC của phản hồi Secure Messaging không khớp")

        if inner_status is not None and inner_status != 0x9000:
            raise RuntimeError(f"Thẻ trả trạng thái {inner_status:04X}")

        return bytes(response_data)
