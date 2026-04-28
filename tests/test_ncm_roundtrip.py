"""验证 NCM 解码逻辑的端到端测试。

构造一个合法的 NCM 文件后再用 :func:`decode_ncm` 解出来，
比对原始音频与元数据是否一致。
运行: ``python -m pytest tests`` 或 ``python tests/test_ncm_roundtrip.py``。
"""
from __future__ import annotations

import base64
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

# 让 tests 目录可以独立运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Crypto.Cipher import AES  # noqa: E402

from auto_ncm.ncm_decoder import (  # noqa: E402
    CORE_KEY,
    META_KEY,
    NCM_MAGIC,
    _build_keybox,
    decode_ncm,
)


def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    return AES.new(key, AES.MODE_ECB).encrypt(_pkcs7_pad(data))


def _build_ncm(
    audio_payload: bytes,
    rc4_key: bytes,
    meta: dict,
    cover: bytes = b"",
) -> bytes:
    """根据 NCM 协议反向构造一个文件字节流。"""
    # 1) RC4 主密钥块: AES_ECB(CORE_KEY, "neteasecloudmusic"+rc4_key) -> XOR 0x64
    rc4_full = b"neteasecloudmusic" + rc4_key
    enc_rc4 = _aes_ecb_encrypt(CORE_KEY, rc4_full)
    rc4_blob = bytes(b ^ 0x64 for b in enc_rc4)

    # 2) 元数据块: AES_ECB(META_KEY, "music:"+json) -> base64 -> 前缀 22B -> XOR 0x63
    meta_json = b"music:" + json.dumps(meta).encode("utf-8")
    enc_meta = _aes_ecb_encrypt(META_KEY, meta_json)
    b64 = base64.b64encode(enc_meta)
    pre = b"163 key(Don't modify):"  # 长度 22
    assert len(pre) == 22
    meta_blob = bytes(b ^ 0x63 for b in (pre + b64))

    # 3) 用 RC4 keybox 加密音频
    keybox = _build_keybox(rc4_key)
    enc_audio = bytearray(len(audio_payload))
    for i, b in enumerate(audio_payload):
        enc_audio[i] = b ^ keybox[i & 0xFF]

    out = io.BytesIO()
    out.write(NCM_MAGIC)
    out.write(b"\x00\x00")  # 2 reserved
    out.write(struct.pack("<I", len(rc4_blob)))
    out.write(rc4_blob)
    out.write(struct.pack("<I", len(meta_blob)))
    out.write(meta_blob)
    out.write(b"\x00" * 4)  # crc32
    out.write(b"\x00" * 5)  # gap
    out.write(struct.pack("<I", len(cover)))
    out.write(cover)
    out.write(bytes(enc_audio))
    return out.getvalue()


class NcmRoundTripTest(unittest.TestCase):
    def test_decode_ncm_recovers_original_audio_and_meta(self):
        # 用一个伪造的 MP3 头，让格式检测能识别为 mp3
        audio = b"ID3\x04\x00" + os.urandom(2048)
        rc4_key = os.urandom(64)
        meta = {
            "musicName": "测试歌曲",
            "artist": [["周杰伦", 1001], ["林俊杰", 1002]],
            "album": "测试专辑",
            "format": "mp3",
        }
        cover = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        ncm_bytes = _build_ncm(audio, rc4_key, meta, cover)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "demo.ncm"
            path.write_bytes(ncm_bytes)
            result = decode_ncm(path)

        self.assertEqual(result.audio, audio, "音频字节解码后应与原始一致")
        self.assertEqual(result.meta["musicName"], "测试歌曲")
        self.assertEqual(result.meta["album"], "测试专辑")
        self.assertEqual(result.fmt, "mp3")
        self.assertEqual(result.cover, cover)
        self.assertEqual(result.cover_mime, "image/png")

    def test_decode_ncm_detects_flac(self):
        audio = b"fLaC" + os.urandom(4096)
        rc4_key = os.urandom(48)
        meta = {"musicName": "Untitled", "artist": [], "album": "", "format": "flac"}
        ncm_bytes = _build_ncm(audio, rc4_key, meta)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "demo.ncm"
            path.write_bytes(ncm_bytes)
            result = decode_ncm(path)

        self.assertEqual(result.audio[:4], b"fLaC")
        self.assertEqual(result.fmt, "flac")


if __name__ == "__main__":
    unittest.main()
