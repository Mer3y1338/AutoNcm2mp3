"""NCM 文件解码核心。

实现参考:
    - https://github.com/anonymous5l/ncmdump
    - https://www.jianshu.com/p/ec5977ef383a

NCM 文件结构（从头到尾）:
    +-----------------------------------+
    | magic header   8 bytes  CTENFDAM |  --> 固定标识
    | reserved       2 bytes            |
    | rc4 key len    4 bytes (LE)       |
    | rc4 key        N bytes (AES加密) |  --> 解密后用于音频流 RC4
    | meta len       4 bytes (LE)       |
    | meta data      N bytes            |  --> base64 + AES, 解出 JSON
    | crc32          4 bytes            |
    | gap            5 bytes            |
    | image len      4 bytes (LE)       |
    | image data     N bytes            |  --> 专辑封面 (JPEG/PNG)
    | audio data     EOF                |  --> RC4 流加密
    +-----------------------------------+

模块只暴露一个核心函数 :func:`decode_ncm`，返回解码后的音频字节、
元数据字典、封面字节与原始格式名称（mp3 / flac）。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import struct
from dataclasses import dataclass, field
from typing import BinaryIO, Optional

from Crypto.Cipher import AES

logger = logging.getLogger(__name__)

# AES 解密所用的两把固定密钥（社区已逆向出来的常量）
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")

NCM_MAGIC = b"CTENFDAM"


class NcmDecodeError(Exception):
    """NCM 文件解析失败。"""


@dataclass
class NcmResult:
    """解码结果容器。"""

    audio: bytes
    meta: dict = field(default_factory=dict)
    cover: Optional[bytes] = None
    fmt: str = "mp3"  # 真实格式: mp3 / flac

    @property
    def cover_mime(self) -> str:
        if not self.cover:
            return ""
        if self.cover.startswith(b"\x89PNG"):
            return "image/png"
        return "image/jpeg"


def _unpad(data: bytes) -> bytes:
    """去除 PKCS#7 填充。"""
    pad = data[-1]
    if pad < 1 or pad > 16:
        # 数据可能没有填充，直接返回
        return data
    return data[:-pad]


def _aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return _unpad(cipher.decrypt(data))


def _read_struct(fp: BinaryIO, fmt: str):
    size = struct.calcsize(fmt)
    raw = fp.read(size)
    if len(raw) != size:
        raise NcmDecodeError("文件被截断，无法读取头信息")
    return struct.unpack(fmt, raw)


def _read_block(fp: BinaryIO) -> bytes:
    """读取一个 [4 bytes length][N bytes payload] 结构的数据块。"""
    (length,) = _read_struct(fp, "<I")
    if length == 0:
        return b""
    data = fp.read(length)
    if len(data) != length:
        raise NcmDecodeError("文件被截断，无法读取数据块")
    return data


def _decode_rc4_key(blob: bytes) -> bytes:
    """解密 RC4 主密钥并去掉前缀 ``neteasecloudmusic``。"""
    obfuscated = bytes(b ^ 0x64 for b in blob)
    decrypted = _aes_ecb_decrypt(CORE_KEY, obfuscated)
    # 解密结果以固定字符串开头
    if decrypted.startswith(b"neteasecloudmusic"):
        decrypted = decrypted[len(b"neteasecloudmusic") :]
    return decrypted


def _decode_meta(blob: bytes) -> dict:
    """解密 JSON 元数据。"""
    if not blob or len(blob) <= 22:
        return {}
    try:
        obfuscated = bytes(b ^ 0x63 for b in blob)
        # 跳过头部 22 字节: "163 key(Don't modify):"
        payload = base64.b64decode(obfuscated[22:])
        decrypted = _aes_ecb_decrypt(META_KEY, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("NCM 元数据解密失败: %s", exc)
        return {}
    # 解密结果以 "music:" 开头
    if decrypted.startswith(b"music:"):
        decrypted = decrypted[len(b"music:") :]
    try:
        return json.loads(decrypted.decode("utf-8", errors="ignore"))
    except json.JSONDecodeError:
        logger.warning("NCM 元数据 JSON 解析失败")
        return {}


def _build_keybox(rc4_key: bytes) -> bytes:
    """RC4-KSA + 一次 PRGA 步长，得到 256 字节的查表。

    NCM 的解密公式（参考 anonymous5l/ncmdump 与社区移植版）::

        out[i] = in[i] ^ keybox[i & 0xff]
        keybox[i] = S[(S[j] + S[(j + S[j]) & 0xff]) & 0xff]
        其中 j = (i + 1) & 0xff
    """
    # KSA
    box = bytearray(range(256))
    key_len = len(rc4_key)
    last = 0
    for i in range(256):
        swap = box[i]
        last = (swap + last + rc4_key[i % key_len]) & 0xFF
        box[i] = box[last]
        box[last] = swap

    # 把 PRGA 的一步运算预先打成 256 字节查表
    keybox = bytearray(256)
    for i in range(256):
        j = (i + 1) & 0xFF
        sj = box[j]
        sjj = box[(j + sj) & 0xFF]
        keybox[i] = box[(sj + sjj) & 0xFF]
    return bytes(keybox)


def _decrypt_audio(fp: BinaryIO, keybox: bytes, chunk: int = 0x8000) -> bytes:
    """流式解密剩余的音频数据 (XOR keybox)。"""
    out = bytearray()
    # 让每一块从字节偏移 0 开始，因此 keybox 索引就是块内位置 i & 0xff
    while True:
        block = fp.read(chunk)
        if not block:
            break
        decrypted = bytearray(len(block))
        for i, b in enumerate(block):
            decrypted[i] = b ^ keybox[i & 0xFF]
        out.extend(decrypted)
    return bytes(out)


def _detect_format(audio: bytes, meta: dict) -> str:
    """判断真实音频格式。"""
    fmt = (meta.get("format") or "").lower()
    if fmt in {"mp3", "flac"}:
        return fmt
    if audio[:4] == b"fLaC":
        return "flac"
    if audio[:3] == b"ID3" or (len(audio) > 1 and audio[0] == 0xFF and (audio[1] & 0xE0) == 0xE0):
        return "mp3"
    # 兜底
    return fmt or "mp3"


def decode_ncm(path: str | os.PathLike) -> NcmResult:
    """解码一个 NCM 文件。

    Args:
        path: NCM 文件路径。

    Returns:
        :class:`NcmResult` 包含原始音频字节、元数据、封面和真实格式。

    Raises:
        NcmDecodeError: 文件不是合法的 NCM 格式或已损坏。
    """
    path = os.fspath(path)
    with open(path, "rb") as fp:
        magic = fp.read(8)
        if magic != NCM_MAGIC:
            raise NcmDecodeError(f"不是合法的 NCM 文件: {path}")
        fp.seek(2, os.SEEK_CUR)  # 2 字节保留位

        rc4_blob = _read_block(fp)
        rc4_key = _decode_rc4_key(rc4_blob)
        keybox = _build_keybox(rc4_key)

        meta_blob = _read_block(fp)
        meta = _decode_meta(meta_blob)

        fp.seek(9, os.SEEK_CUR)  # crc32(4) + gap(5)

        cover = _read_block(fp) or None

        audio = _decrypt_audio(fp, keybox)

    fmt = _detect_format(audio, meta)
    return NcmResult(audio=audio, meta=meta, cover=cover, fmt=fmt)


__all__ = ["decode_ncm", "NcmResult", "NcmDecodeError"]
