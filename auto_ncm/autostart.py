"""Windows 开机自启动管理。

通过写入注册表 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
实现开机启动，无需管理员权限，只影响当前用户。

优点对比 (我们选第 1 项):
    1. HKCU Run 注册表        - 无需管理员、卸载干净、对登录后启动 - 推荐
    2. 启动文件夹快捷方式     - 需要 pywin32 / 写 .lnk
    3. 计划任务 schtasks      - 需要管理员或额外权限处理

启动时附带 ``--minimized`` 参数，让程序自启时直接进托盘不打扰用户。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 注册表路径
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "AutoNcm2Mp3"


# ---------------------------------------------------------------------------
# 启动命令构造
# ---------------------------------------------------------------------------


def _python_executable() -> str:
    """在开发模式下用 ``pythonw.exe`` 启动，避免出现黑窗。"""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if pyw.is_file():
        return str(pyw)
    return str(exe)


def _build_command() -> str:
    """根据当前运行环境推导启动命令字符串 (含完整引号)。

    打包后:  ``"C:\\path\\AutoNcm2Mp3.exe" --minimized``
    开发态:  ``"C:\\Python\\pythonw.exe" "D:\\Code\\AutoNcm2mp3\\run.py" --minimized``
    """
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后
        exe = Path(sys.executable).resolve()
        return f'"{exe}" --minimized'

    # 开发模式: 找出 run.py
    repo_root = Path(__file__).resolve().parent.parent
    entry = repo_root / "run.py"
    if not entry.is_file():
        # 兜底: 用 -m auto_ncm
        return f'"{_python_executable()}" -m auto_ncm --minimized'
    return f'"{_python_executable()}" "{entry}" --minimized'


# ---------------------------------------------------------------------------
# 注册表操作
# ---------------------------------------------------------------------------


def is_supported() -> bool:
    """非 Windows 平台不支持本功能。"""
    return os.name == "nt"


def _open_winreg():
    if not is_supported():
        raise RuntimeError("非 Windows 平台不支持开机自启")
    import winreg  # 延迟导入，方便跨平台 import

    return winreg


def is_enabled() -> bool:
    """检查注册表里是否已经写入了启动项。"""
    if not is_supported():
        return False
    winreg = _open_winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("读取启动项失败: %s", exc)
        return False


def get_command() -> Optional[str]:
    """返回当前注册表里写入的命令字符串 (没有则 None)。"""
    if not is_supported():
        return None
    winreg = _open_winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return value
    except FileNotFoundError:
        return None
    except OSError:
        return None


def enable() -> str:
    """写入开机启动项，返回写入的命令。"""
    winreg = _open_winreg()
    cmd = _build_command()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
    logger.info("已写入开机启动项: %s", cmd)
    return cmd


def disable() -> None:
    """从注册表删除启动项 (不存在时静默跳过)。"""
    if not is_supported():
        return
    winreg = _open_winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
        logger.info("已移除开机启动项")
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("移除启动项失败: %s", exc)


def sync(enabled: bool) -> None:
    """根据用户偏好确保注册表状态正确。

    会处理三种边界:
        - 想启用但没写过       -> 直接写;
        - 想启用但路径已变化   -> 用最新路径覆盖 (避免老路径失效);
        - 想禁用但有残留       -> 移除。
    """
    if not is_supported():
        return
    if enabled:
        desired = _build_command()
        current = get_command()
        if current != desired:
            enable()
    else:
        if get_command() is not None:
            disable()


__all__ = [
    "APP_NAME",
    "is_supported",
    "is_enabled",
    "get_command",
    "enable",
    "disable",
    "sync",
]
