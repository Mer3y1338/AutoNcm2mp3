"""程序主入口。

支持两种用法:
    1. 不带参数 -> 启动 GUI + 托盘
    2. ``--cli <file_or_dir> ...`` -> 命令行批量转换
"""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LOG_PATH, Config
from .converter import ConvertError, convert_one
from .watcher import NcmWatcher


def _setup_logging() -> None:
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_PATH, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
        handlers.append(file_handler)
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt=datefmt, handlers=handlers)


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="auto_ncm.cli",
        description="把 NCM 文件批量转换为 MP3/FLAC (命令行模式)",
    )
    parser.add_argument("paths", nargs="+", help="NCM 文件或包含 NCM 的目录")
    parser.add_argument("-o", "--output", help="输出目录 (默认与源同目录)")
    parser.add_argument("--force-mp3", action="store_true", help="把 FLAC 也强制转 MP3 (需 ffmpeg)")
    parser.add_argument("--bitrate", default="320k", help="MP3 比特率 (默认 320k)")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="转换成功后保留原 NCM (默认移到回收站)",
    )
    args = parser.parse_args(argv)

    cfg = Config()
    if args.output:
        cfg.output_dir = args.output
    cfg.force_mp3 = args.force_mp3
    cfg.mp3_bitrate = args.bitrate
    cfg.on_success = "keep" if args.keep else "recycle"

    files: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(p.rglob("*.ncm"))
        elif p.is_file() and p.suffix.lower() == ".ncm":
            files.append(p)
        else:
            print(f"忽略: {p}", file=sys.stderr)

    if not files:
        print("没有找到任何 NCM 文件")
        return 1

    ok = err = 0
    for f in files:
        try:
            r = convert_one(f, cfg)
            print(f"[OK]  {f.name}  ->  {r.dst.name}")
            ok += 1
        except ConvertError as e:
            print(f"[ERR] {f.name}: {e}", file=sys.stderr)
            err += 1
    print(f"完成: 成功 {ok}, 失败 {err}")
    return 0 if err == 0 else 2


def _set_app_user_model_id() -> None:
    """让任务栏显示我们自己的图标而不是 Python 默认图标。

    Windows 任务栏会按 AppUserModelID 进行图标分组，
    必须在任何 Tk 窗口创建之前调用。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AutoNcm2Mp3.App")
    except Exception:  # noqa: BLE001
        pass


def _show_already_running_dialog() -> None:
    """提示用户已经有实例在运行 (用 Win32 MessageBox, 无需 Tk)。"""
    if sys.platform != "win32":
        print("AutoNcm2Mp3 已经在运行。", file=sys.stderr)
        return
    try:
        import ctypes

        MB_OK = 0x00000000
        MB_ICONINFORMATION = 0x00000040
        MB_TOPMOST = 0x00040000
        ctypes.windll.user32.MessageBoxW(
            0,
            "AutoNcm2Mp3 已经在运行。\n已为您打开主窗口。",
            "AutoNcm2Mp3",
            MB_OK | MB_ICONINFORMATION | MB_TOPMOST,
        )
    except Exception:  # noqa: BLE001
        pass


def _gui(force_minimized: bool = False) -> int:
    # ---- 单实例锁: 必须在任何重型初始化之前 ----
    from .single_instance import SingleInstanceLock

    lock = SingleInstanceLock()
    if lock.already_running:
        # 让已有实例显示主窗口, 然后我们退出
        ok = lock.signal_existing()
        if not ok:
            # 兜底: 找不到信号窗口也提示一下
            logging.getLogger(__name__).warning("未能联系到已有实例")
        _show_already_running_dialog()
        return 0

    cfg = Config.load()
    # 同步注册表状态 (路径变化时自动更新启动项)
    try:
        from . import autostart as _autostart

        _autostart.sync(cfg.autostart)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("同步自启动失败: %s", exc)

    _set_app_user_model_id()

    watcher = NcmWatcher(cfg)
    # 延迟导入 GUI，使 CLI 模式无需 Tk/PIL/pystray
    from .tray import TrayApp

    # start_minimized: 用户偏好 OR 命令行 --minimized 强制
    start_minimized = bool(cfg.start_minimized) or force_minimized
    try:
        TrayApp(cfg, watcher, start_minimized=start_minimized).run()
    finally:
        lock.release()
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--cli":
        return _cli(argv[1:])
    # GUI 模式可以接受 --minimized (开机自启时由注册表命令带上)
    minimized = "--minimized" in argv
    return _gui(force_minimized=minimized)


if __name__ == "__main__":
    raise SystemExit(main())
