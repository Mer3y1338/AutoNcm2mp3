"""资源文件路径解析。

兼容三种运行环境:
    1. 开发模式: ``python run.py`` 时，资源在仓库根目录；
    2. ``python -m auto_ncm`` 时，与 1 同；
    3. PyInstaller 打包后:
         - ``--onefile``: 资源被解压到 ``sys._MEIPASS``；
         - ``--onedir`` : 资源在 exe 同级目录。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    # PyInstaller onefile 解压目录
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    # 打包后的 exe 同级目录
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    # 仓库根目录 (开发模式)
    dirs.append(Path(__file__).resolve().parent.parent)
    # 包内目录 (备用)
    dirs.append(Path(__file__).resolve().parent)
    return dirs


def find_resource(name: str) -> Optional[Path]:
    """在所有候选目录里查找 ``name``，返回第一个存在的路径。"""
    for d in _candidate_dirs():
        p = d / name
        if p.is_file():
            return p
    return None


LOGO_PNG: Optional[Path] = find_resource("logo.png")
LOGO_ICO: Optional[Path] = find_resource("logo.ico")


__all__ = ["find_resource", "LOGO_PNG", "LOGO_ICO"]
