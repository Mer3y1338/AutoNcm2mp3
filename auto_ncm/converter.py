"""把 NCM 文件转换为可直接播放的音频文件。

主要职责:
    - 调用 :mod:`ncm_decoder` 解出原始音频字节；
    - 根据用户配置写出 mp3/flac 文件，并把元数据/封面回写到标签里；
    - 当用户开启 ``force_mp3`` 时，对 FLAC 调用 ffmpeg 转码到 MP3；
    - 按 ``on_success`` 策略处理源 NCM 文件 (保留/回收站/永久删除)。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TIT2, TPE1
from mutagen.mp3 import MP3

from .config import Config
from .ncm_decoder import NcmDecodeError, NcmResult, decode_ncm

logger = logging.getLogger(__name__)


class ConvertError(Exception):
    """转换流程中的错误。"""


@dataclass
class ConvertResult:
    src: Path
    dst: Path
    fmt: str  # 输出格式
    title: str
    artists: str


# ---------------------------------------------------------------------------
# ffmpeg 探测
# ---------------------------------------------------------------------------


def _ffmpeg_path() -> Optional[str]:
    """返回可用的 ffmpeg 可执行文件路径，找不到时返回 None。"""
    # 1) 同目录下 (打包后) 自带的 ffmpeg
    bundled = Path(__file__).resolve().parent.parent / "ffmpeg" / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    # 2) PATH 中的 ffmpeg
    found = shutil.which("ffmpeg")
    return found


# ---------------------------------------------------------------------------
# 标签写入
# ---------------------------------------------------------------------------


def _meta_text(meta: dict) -> tuple[str, str]:
    """从 NCM meta JSON 中提取 ``(title, artists)``。"""
    title = (meta.get("musicName") or "").strip()
    raw_artists = meta.get("artist") or []
    if isinstance(raw_artists, list):
        names = []
        for a in raw_artists:
            if isinstance(a, (list, tuple)) and a:
                names.append(str(a[0]))
            elif isinstance(a, str):
                names.append(a)
        artists = "/".join(filter(None, names))
    else:
        artists = str(raw_artists)
    return title, artists


def _safe_filename(name: str) -> str:
    """去除 Windows 文件名非法字符。"""
    bad = '<>:"/\\|?*\0'
    cleaned = "".join("_" if c in bad else c for c in name).strip(" .")
    return cleaned or "unknown"


def _embed_mp3_tags(path: Path, result: NcmResult) -> None:
    title, artists = _meta_text(result.meta)
    album = (result.meta.get("album") or "").strip()
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    if title:
        tags.add(TIT2(encoding=3, text=title))
    if artists:
        tags.add(TPE1(encoding=3, text=artists))
    if album:
        tags.add(TALB(encoding=3, text=album))
    if result.cover:
        tags.add(
            APIC(
                encoding=3,
                mime=result.cover_mime,
                type=3,  # front cover
                desc="Cover",
                data=result.cover,
            )
        )
    tags.save(path)


def _embed_flac_tags(path: Path, result: NcmResult) -> None:
    title, artists = _meta_text(result.meta)
    album = (result.meta.get("album") or "").strip()
    audio = FLAC(path)
    if title:
        audio["title"] = title
    if artists:
        audio["artist"] = artists
    if album:
        audio["album"] = album
    if result.cover:
        pic = Picture()
        pic.data = result.cover
        pic.type = 3
        pic.mime = result.cover_mime
        audio.clear_pictures()
        audio.add_picture(pic)
    audio.save()


# ---------------------------------------------------------------------------
# 源文件清理
# ---------------------------------------------------------------------------


def _dispose_source(src: Path, policy: str) -> None:
    if policy == "keep":
        return
    if policy == "delete":
        try:
            src.unlink()
        except OSError as exc:
            logger.warning("删除 %s 失败: %s", src, exc)
        return
    # 默认: 移到回收站
    try:
        from send2trash import send2trash  # 延迟导入，CLI 场景可能未安装
        send2trash(str(src))
    except Exception as exc:  # noqa: BLE001
        logger.warning("回收站操作失败 (%s)，改为永久删除", exc)
        try:
            src.unlink()
        except OSError as exc2:
            logger.warning("删除 %s 失败: %s", src, exc2)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _build_output_path(src: Path, cfg: Config, target_ext: str) -> Path:
    """根据配置选择输出目录与文件名。"""
    if cfg.output_dir:
        out_dir = Path(cfg.output_dir)
    else:
        out_dir = src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_filename(src.stem)
    return out_dir / f"{stem}.{target_ext}"


def _flac_to_mp3(flac_path: Path, mp3_path: Path, bitrate: str) -> None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise ConvertError(
            "未找到 ffmpeg，无法把 FLAC 转为 MP3。\n"
            "解决办法: 关闭“强制转 MP3”选项，或把 ffmpeg.exe 放到程序目录的 ffmpeg 子目录中，"
            "也可以将 ffmpeg 加入系统 PATH。"
        )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(flac_path),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        "-map_metadata",
        "0",
        "-id3v2_version",
        "3",
        str(mp3_path),
    ]
    logger.debug("ffmpeg cmd: %s", cmd)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if proc.returncode != 0:
        raise ConvertError(
            f"ffmpeg 转码失败 (code={proc.returncode}): {proc.stderr.decode(errors='ignore')[:500]}"
        )


def convert_one(path: str | os.PathLike, cfg: Config) -> ConvertResult:
    """把单个 NCM 文件转换出来。"""
    src = Path(os.fspath(path))
    if not src.is_file():
        raise ConvertError(f"文件不存在: {src}")

    try:
        result = decode_ncm(src)
    except NcmDecodeError as exc:
        raise ConvertError(str(exc)) from exc

    title, artists = _meta_text(result.meta)
    target_ext = result.fmt
    intermediate_path: Optional[Path] = None

    # 计算目标路径
    final_ext = "mp3" if (cfg.force_mp3 and target_ext == "flac") else target_ext
    final_path = _build_output_path(src, cfg, final_ext)

    if cfg.force_mp3 and target_ext == "flac":
        # 先把 FLAC 写到临时文件再转 MP3
        intermediate_path = final_path.with_name(final_path.stem + ".tmp.flac")
        intermediate_path.write_bytes(result.audio)
        try:
            _embed_flac_tags(intermediate_path, result)
            _flac_to_mp3(intermediate_path, final_path, cfg.mp3_bitrate)
        finally:
            try:
                if intermediate_path.exists():
                    intermediate_path.unlink()
            except OSError:
                pass
        # ffmpeg -map_metadata 已经迁移了大部分标签，但封面在某些版本里需要重写
        try:
            _embed_mp3_tags(final_path, result)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MP3 标签补写失败 (可忽略): %s", exc)
    else:
        final_path.write_bytes(result.audio)
        try:
            if final_ext == "mp3":
                _embed_mp3_tags(final_path, result)
            else:
                _embed_flac_tags(final_path, result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("写入标签失败 (可忽略): %s", exc)

    _dispose_source(src, cfg.on_success)

    return ConvertResult(
        src=src,
        dst=final_path,
        fmt=final_ext,
        title=title or src.stem,
        artists=artists,
    )


__all__ = ["convert_one", "ConvertResult", "ConvertError"]
