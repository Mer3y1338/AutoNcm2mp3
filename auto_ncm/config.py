"""配置管理。

配置以 JSON 形式持久化在 ``%APPDATA%/AutoNcm2Mp3/config.json``，
便于在多次启动间保留用户的下载路径与偏好设置。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _default_app_dir() -> Path:
    """获取程序的数据目录（Windows 下走 APPDATA）。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    path = Path(base) / "AutoNcm2Mp3"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _guess_default_download_dir() -> str:
    """猜测网易云的默认下载目录。"""
    candidates = [
        Path.home() / "Music" / "VipSongsDownload",
        Path.home() / "Music" / "CloudMusic",
        Path("D:/CloudMusic"),
        Path("D:/Music/CloudMusic"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return ""


CONFIG_PATH = _default_app_dir() / "config.json"
LOG_PATH = _default_app_dir() / "convert.log"


@dataclass
class Config:
    """用户可调节的设置项。"""

    download_dir: str = field(default_factory=_guess_default_download_dir)
    output_dir: str = ""  # 留空 = 与源文件同目录
    # 当 NCM 内部为 FLAC 时是否强制转成 MP3
    force_mp3: bool = False
    # FLAC -> MP3 的比特率 (k)
    mp3_bitrate: str = "320k"
    # 监控启用
    watch_enabled: bool = True
    # 转换成功后如何处理原 NCM:
    #   keep   -> 保留
    #   recycle-> 移到回收站 (推荐)
    #   delete -> 永久删除
    on_success: str = "recycle"
    # 启动时是否最小化到托盘
    start_minimized: bool = False
    # 开机自启 (写入 Windows 当前用户 Run 注册表)
    autostart: bool = False

    # ---- 持久化 -------------------------------------------------------

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.debug("配置已保存到 %s", path)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("配置文件损坏，使用默认值")
            return cls()
        # 过滤掉新版字段不存在的情况
        valid_keys = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**data)


__all__ = ["Config", "CONFIG_PATH", "LOG_PATH"]
