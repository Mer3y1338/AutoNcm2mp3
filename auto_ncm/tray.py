"""系统托盘 (使用 pystray + Pillow)。

托盘菜单:
    - 打开窗口
    - 暂停 / 恢复监控
    - 一键扫描
    - 退出
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import pystray
from PIL import Image, ImageDraw

from .config import Config
from .gui import App
from .resources import LOGO_PNG
from .single_instance import install_signal_window
from .watcher import NcmWatcher

logger = logging.getLogger(__name__)


def _fallback_icon() -> Image.Image:
    """logo.png 不可用时的兜底图标 (蓝底音符)。"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, size - 4, size - 4), fill=(33, 150, 243, 255))
    d.ellipse((20, 36, 32, 48), fill=(255, 255, 255, 255))
    d.rectangle((30, 16, 34, 42), fill=(255, 255, 255, 255))
    d.polygon([(34, 16), (48, 12), (48, 22), (34, 26)], fill=(255, 255, 255, 255))
    return img


def _make_icon_image() -> Image.Image:
    """优先用根目录的 ``logo.png``，找不到时生成一个内置图标。"""
    if LOGO_PNG is not None:
        try:
            img = Image.open(LOGO_PNG).convert("RGBA")
            # pystray 在 Windows 系统托盘默认期望 16x16 / 32x32 的小图，
            # 但传更大的图它内部会自己缩放，不会失真。
            return img
        except Exception as exc:  # noqa: BLE001
            logger.warning("加载 logo.png 失败，使用内置图标: %s", exc)
    return _fallback_icon()


class TrayApp:
    """把 GUI 和监控器粘合成一个完整的托盘应用。"""

    def __init__(
        self,
        cfg: Config,
        watcher: NcmWatcher,
        *,
        start_minimized: bool = False,
    ) -> None:
        self.cfg = cfg
        self.watcher = watcher
        self.app = App(cfg, watcher, on_quit=self._on_window_close)
        self._icon: Optional[pystray.Icon] = None
        self._icon_thread: Optional[threading.Thread] = None
        self._stopping = False
        self._start_minimized = start_minimized

    # ------------------------------------------------------------------
    # 菜单回调
    # ------------------------------------------------------------------

    def _show_window(self, *_):
        self.app.root.after(0, self.app.show)

    def _toggle_watch(self, *_):
        if self.watcher.running:
            self.watcher.stop()
            self.cfg.watch_enabled = False
        else:
            self.watcher.start()
            self.cfg.watch_enabled = True
        self.cfg.save()
        # 让 GUI 也同步状态
        self.app.root.after(0, self.app.refresh_from_config)
        if self._icon:
            self._icon.update_menu()

    def _scan(self, *_):
        threading.Thread(target=self.watcher.scan_existing, daemon=True).start()

    def _quit(self, *_):
        self._stopping = True
        if self._icon:
            self._icon.stop()
        self.watcher.shutdown()
        self.app.root.after(0, self.app.quit)

    def _on_window_close(self):
        # 用户按 X: 隐藏到托盘 (而不是退出)
        self.app.hide()

    # ------------------------------------------------------------------
    # 托盘菜单
    # ------------------------------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("打开窗口", self._show_window, default=True),
            pystray.MenuItem(
                lambda _: "暂停监控" if self.watcher.running else "开启监控",
                self._toggle_watch,
            ),
            pystray.MenuItem("扫描已有 NCM", self._scan),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )

    def _start_icon(self) -> None:
        self._icon = pystray.Icon(
            "AutoNcm2Mp3",
            icon=_make_icon_image(),
            title="AutoNcm2Mp3",
            menu=self._build_menu(),
        )
        # pystray 需要在自己的线程里跑事件循环
        self._icon_thread = threading.Thread(target=self._icon.run, daemon=True)
        self._icon_thread.start()

    # ------------------------------------------------------------------
    # 启动
    # ------------------------------------------------------------------

    def _on_external_activate(self) -> None:
        """收到第二个实例的激活请求 (来自单实例信号窗口的子线程)。

        必须 marshal 到 Tk 主线程, 不能直接操作 Tk 控件。
        """
        try:
            self.app.root.after(0, self.app.show)
        except Exception:  # noqa: BLE001
            logger.exception("激活主窗口失败")

    def run(self) -> None:
        self._start_icon()
        # 安装单实例激活信号窗口
        signal_win = install_signal_window(self._on_external_activate)
        if self.cfg.watch_enabled:
            self.watcher.start()
        if self._start_minimized:
            self.app.hide()
        try:
            self.app.run_mainloop()
        finally:
            self._stopping = True
            try:
                signal_win.stop()
            except Exception:  # noqa: BLE001
                pass
            self.watcher.shutdown()
            if self._icon:
                self._icon.stop()


__all__ = ["TrayApp"]
