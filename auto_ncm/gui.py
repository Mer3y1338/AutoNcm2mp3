"""Windows 11 风格 GUI - 简洁版。

设计取舍 (针对前一版 Canvas 圆角的毛刺/卡死问题):
    - 卡片 / 按钮 / 输入框: 全部用 ``tk.Frame`` 直角 + 1px 浅灰边, 极简零毛刺
    - 开关 (Switch): 用 PIL 4x 离屏渲染再缩放, 出真正抗锯齿的圆角
    - 下拉框: 用 ``tk.Menu`` 系统原生菜单 (Win11 自动给抗锯齿圆角 + 动画)

代码组织:
    - Theme        : 调色板与几何尺寸
    - 自绘组件     : Card / FlatButton / FlatEntry / Switch / Dropdown
    - App          : 主窗口
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable, List, Optional

from . import autostart
from .config import LOG_PATH, Config
from .converter import ConvertResult
from .resources import LOGO_ICO, LOGO_PNG
from .watcher import NcmWatcher

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 主题
# ---------------------------------------------------------------------------


class Theme:
    """Windows 11 浅色调色板。"""

    # 背景层
    BG = "#f3f3f3"            # 窗口背景
    SURFACE = "#ffffff"       # 卡片
    SURFACE_ALT = "#fbfbfb"   # 输入框
    SURFACE_HOVER = "#f5f5f5"
    SURFACE_PRESS = "#ebebeb"

    # 边框
    BORDER = "#e5e5e5"
    BORDER_STRONG = "#d1d1d1"
    BORDER_FOCUS = "#0067c0"

    # 文字
    TEXT = "#1a1a1a"
    TEXT_DIM = "#5c5c5c"
    MUTED = "#8a8a8a"

    # 强调色 (Win11 蓝)
    ACCENT = "#0067c0"
    ACCENT_HOVER = "#1474d2"
    ACCENT_PRESS = "#0a5aa8"
    ACCENT_TEXT = "#ffffff"

    # 状态色
    OK = "#107c10"
    WARN = "#9d5d00"
    ERR = "#c42b1c"

    DOT_OK = "#107c10"
    DOT_OFF = "#bdbdbd"

    # 字体
    FONT_FAMILY = "Segoe UI Variable"
    FONT_FALLBACK = "Segoe UI"


ON_SUCCESS_LABELS = {
    "recycle": "移到回收站 (推荐)",
    "keep": "保留原文件",
    "delete": "永久删除",
}
ON_SUCCESS_REVERSE = {v: k for k, v in ON_SUCCESS_LABELS.items()}


def _resolve_font_family() -> str:
    available = set(tkfont.families())
    for name in (Theme.FONT_FAMILY, "Segoe UI Variable Display", Theme.FONT_FALLBACK,
                 "Microsoft YaHei UI", "Arial"):
        if name in available:
            return name
    return Theme.FONT_FALLBACK


# ---------------------------------------------------------------------------
# Card: Frame + 1px 边 + 内边距 (简洁直角, 不裁剪)
# ---------------------------------------------------------------------------


class Card(tk.Frame):
    """白色卡片。

    - 外层 Frame 用 ``highlightthickness=1`` 绘制 1px 边
    - 内部 ``self.body`` 是真正的内容容器, 已经预留 padding
    """

    def __init__(self, parent: tk.Widget, title: Optional[str] = None, *, padding: int = 16) -> None:
        super().__init__(
            parent, bg=Theme.SURFACE,
            highlightthickness=1,
            highlightbackground=Theme.BORDER,
            highlightcolor=Theme.BORDER,
            bd=0,
        )
        self._family = _resolve_font_family()
        if title:
            tk.Label(
                self, text=title, bg=Theme.SURFACE, fg=Theme.TEXT,
                font=(self._family, 12, "bold"),
                anchor="w",
            ).pack(fill="x", padx=padding, pady=(padding, 8))

        self.body = tk.Frame(self, bg=Theme.SURFACE)
        self.body.pack(fill="both", expand=True, padx=padding, pady=(0, padding))


# ---------------------------------------------------------------------------
# FlatButton: 普通 Label + 1px 边 (避免 Canvas 抗锯齿问题)
# ---------------------------------------------------------------------------


class FlatButton(tk.Frame):
    """扁平按钮。

    - ``kind="primary"``: 蓝底白字, 无边
    - ``kind="default"``: 白底深字 + 1px 灰边
    """

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        command: Optional[Callable[[], None]] = None,
        *,
        kind: str = "default",
        pad_x: int = 16,
        pad_y: int = 8,
    ) -> None:
        super().__init__(parent, bd=0, highlightthickness=0)
        self._kind = kind
        self._command = command
        self._family = _resolve_font_family()
        self._disabled = False

        if kind == "primary":
            self._bg = Theme.ACCENT
            self._bg_hover = Theme.ACCENT_HOVER
            self._bg_press = Theme.ACCENT_PRESS
            self._fg = Theme.ACCENT_TEXT
            self.configure(bg=self._bg)
        else:
            self._bg = Theme.SURFACE
            self._bg_hover = Theme.SURFACE_HOVER
            self._bg_press = Theme.SURFACE_PRESS
            self._fg = Theme.TEXT
            self.configure(
                bg=self._bg,
                highlightthickness=1,
                highlightbackground=Theme.BORDER_STRONG,
                highlightcolor=Theme.BORDER_STRONG,
            )

        self._label = tk.Label(
            self, text=text,
            bg=self._bg, fg=self._fg,
            padx=pad_x, pady=pad_y,
            font=(self._family, 10),
            cursor="hand2",
        )
        self._label.pack()

        for w in (self, self._label):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<Button-1>", self._on_press)
            w.bind("<ButtonRelease-1>", self._on_release)

    def _set_bg(self, color: str) -> None:
        self.configure(bg=color)
        self._label.configure(bg=color)

    def _on_enter(self, _evt) -> None:
        if not self._disabled:
            self._set_bg(self._bg_hover)

    def _on_leave(self, _evt) -> None:
        if not self._disabled:
            self._set_bg(self._bg)

    def _on_press(self, _evt) -> None:
        if not self._disabled:
            self._set_bg(self._bg_press)

    def _on_release(self, evt) -> None:
        if self._disabled:
            return
        # 只在松开时仍在按钮内才触发
        x, y = evt.x, evt.y
        in_widget = 0 <= x <= self.winfo_width() and 0 <= y <= self.winfo_height()
        self._set_bg(self._bg_hover if in_widget else self._bg)
        if in_widget and self._command:
            self._command()


# ---------------------------------------------------------------------------
# FlatEntry: tk.Entry 套 1px 边 Frame
# ---------------------------------------------------------------------------


class FlatEntry(tk.Frame):
    """带 1px 灰边的扁平输入框。focus 时边框变蓝。"""

    def __init__(
        self,
        parent: tk.Widget,
        textvariable: Optional[tk.StringVar] = None,
        *,
        height: int = 32,
    ) -> None:
        super().__init__(
            parent, bg=Theme.SURFACE_ALT,
            highlightthickness=1,
            highlightbackground=Theme.BORDER_STRONG,
            highlightcolor=Theme.BORDER_STRONG,
            bd=0, height=height,
        )
        self.pack_propagate(False)
        self._family = _resolve_font_family()

        self.entry = tk.Entry(
            self,
            textvariable=textvariable,
            relief="flat", bd=0,
            bg=Theme.SURFACE_ALT,
            fg=Theme.TEXT,
            insertbackground=Theme.TEXT,
            font=(self._family, 10),
            highlightthickness=0,
        )
        self.entry.pack(fill="both", expand=True, padx=10, pady=4)

        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)

    def _on_focus_in(self, _evt) -> None:
        self.configure(highlightbackground=Theme.BORDER_FOCUS,
                       highlightcolor=Theme.BORDER_FOCUS)

    def _on_focus_out(self, _evt) -> None:
        self.configure(highlightbackground=Theme.BORDER_STRONG,
                       highlightcolor=Theme.BORDER_STRONG)


# ---------------------------------------------------------------------------
# Switch: PIL 4x 离屏抗锯齿渲染 (零毛刺)
# ---------------------------------------------------------------------------


class Switch(tk.Frame):
    """Win11 风开关 + 标题/描述。

    实现细节:
        用 PIL 在 4 倍分辨率下绘制 track + thumb, 再用 LANCZOS 缩放回 1x,
        Tk 显示 PhotoImage。这样得到真正抗锯齿的圆角, 没有任何毛刺。
        渲染结果按 (on, hover) 状态缓存, 切换时只是换一张图。
    """

    SWITCH_W = 40
    SWITCH_H = 20
    THUMB_R_OFF = 6      # 关闭时 thumb 半径
    THUMB_R_ON = 7       # 打开时 thumb 半径
    SCALE = 4            # 4x 离屏抗锯齿

    # 类级缓存: 同样的状态只渲染一次
    _CACHE: dict[tuple, "object"] = {}

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        *,
        variable: Optional[tk.BooleanVar] = None,
        command: Optional[Callable[[], None]] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(parent, bg=Theme.SURFACE, bd=0, highlightthickness=0)
        self._family = _resolve_font_family()
        self._var = variable or tk.BooleanVar(value=False)
        self._command = command
        self._text = text
        self._desc = description
        self._hover = False

        # 左侧: switch image
        self._img_label = tk.Label(
            self, bg=Theme.SURFACE, cursor="hand2", bd=0,
        )
        self._img_label.pack(side="left", padx=(0, 12), pady=2)

        # 右侧: 文字
        right = tk.Frame(self, bg=Theme.SURFACE)
        right.pack(side="left", fill="x", expand=True)

        self._title_label = tk.Label(
            right, text=text, bg=Theme.SURFACE, fg=Theme.TEXT,
            font=(self._family, 10), anchor="w", cursor="hand2",
        )
        self._title_label.pack(fill="x", anchor="w")

        if description:
            self._desc_label = tk.Label(
                right, text=description, bg=Theme.SURFACE, fg=Theme.TEXT_DIM,
                font=(self._family, 9), anchor="w", cursor="hand2",
            )
            self._desc_label.pack(fill="x", anchor="w")
        else:
            self._desc_label = None

        # 整行点击 / 悬停
        for w in (self, self._img_label, self._title_label, right):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
        if self._desc_label is not None:
            self._desc_label.bind("<Button-1>", self._on_click)
            self._desc_label.bind("<Enter>", self._on_enter)
            self._desc_label.bind("<Leave>", self._on_leave)

        self._var.trace_add("write", lambda *_: self._render())
        self._render()

    # --- 鼠标 ---

    def _on_click(self, _evt) -> None:
        self._var.set(not self._var.get())
        if self._command:
            self._command()

    def _on_enter(self, _evt) -> None:
        if not self._hover:
            self._hover = True
            self._render()

    def _on_leave(self, evt) -> None:
        # 因为整个 Switch 上有多个子控件都绑了 <Leave>, 鼠标在子控件之间
        # 移动也会触发 <Leave>, 我们要判断是否真的离开了整个 Switch。
        x_root, y_root = evt.x_root, evt.y_root
        x1, y1 = self.winfo_rootx(), self.winfo_rooty()
        x2 = x1 + self.winfo_width()
        y2 = y1 + self.winfo_height()
        if x1 <= x_root < x2 and y1 <= y_root < y2:
            return
        if self._hover:
            self._hover = False
            self._render()

    # --- 渲染 ---

    def _render(self) -> None:
        on = bool(self._var.get())
        photo = self._get_image(on, self._hover)
        self._img_label.configure(image=photo)
        # 必须保引用避免被 GC
        self._img_label.image = photo  # type: ignore[attr-defined]

    @classmethod
    def _get_image(cls, on: bool, hover: bool):
        key = (on, hover)
        cached = cls._CACHE.get(key)
        if cached is not None:
            return cached
        try:
            from PIL import Image, ImageDraw, ImageTk
        except ImportError:
            return None  # 没装 PIL: img_label 会显示空白, 不影响功能

        s = cls.SCALE
        w = cls.SWITCH_W * s
        h = cls.SWITCH_H * s
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        # 颜色
        if on:
            track_fill = Theme.ACCENT_HOVER if hover else Theme.ACCENT
            track_outline = track_fill
            thumb_fill = "#ffffff"
            thumb_r = cls.THUMB_R_ON * s
        else:
            track_fill = Theme.SURFACE_HOVER if hover else Theme.SURFACE
            track_outline = Theme.BORDER_STRONG
            thumb_fill = Theme.TEXT_DIM
            thumb_r = cls.THUMB_R_OFF * s

        # track (胶囊形)
        track_radius = h // 2
        d.rounded_rectangle(
            (1, 1, w - 2, h - 2),
            radius=track_radius,
            fill=track_fill, outline=track_outline, width=max(1, s // 2),
        )
        # thumb
        margin = (h - thumb_r * 2) // 2
        if on:
            cx = w - margin - thumb_r
        else:
            cx = margin + thumb_r
        cy = h // 2
        d.ellipse(
            (cx - thumb_r, cy - thumb_r, cx + thumb_r, cy + thumb_r),
            fill=thumb_fill,
        )

        # 缩回 1x: LANCZOS 抗锯齿
        # (Pillow 10+ 把 Image.LANCZOS 移到了 Image.Resampling.LANCZOS)
        try:
            resample = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
        except AttributeError:
            resample = Image.LANCZOS  # type: ignore[attr-defined]
        img = img.resize((cls.SWITCH_W, cls.SWITCH_H), resample)
        photo = ImageTk.PhotoImage(img)
        cls._CACHE[key] = photo
        return photo


# ---------------------------------------------------------------------------
# Dropdown: 用 tk.Menu (Win11 自带抗锯齿 + 动画)
# ---------------------------------------------------------------------------


class Dropdown(tk.Frame):
    """点击弹出原生菜单的下拉框。

    Win11 / Win10 下 ``tk.Menu`` 走系统主题, 自带圆角 / 阴影 / 滑入动画,
    我们只画触发器外观, 把弹出交给系统。
    """

    def __init__(
        self,
        parent: tk.Widget,
        textvariable: tk.StringVar,
        values: Iterable[str],
        *,
        width: int = 200,
        height: int = 32,
    ) -> None:
        super().__init__(
            parent, bg=Theme.SURFACE_ALT,
            highlightthickness=1,
            highlightbackground=Theme.BORDER_STRONG,
            highlightcolor=Theme.BORDER_STRONG,
            bd=0, width=width, height=height,
        )
        self.pack_propagate(False)
        self._family = _resolve_font_family()
        self._var = textvariable
        self._values = list(values)
        self._hover = False

        # 主体: 文字 + chevron
        self._inner = tk.Frame(self, bg=Theme.SURFACE_ALT)
        self._inner.pack(fill="both", expand=True, padx=10, pady=0)

        self._label = tk.Label(
            self._inner, textvariable=self._var,
            bg=Theme.SURFACE_ALT, fg=Theme.TEXT,
            font=(self._family, 10), anchor="w",
            cursor="hand2",
        )
        self._label.pack(side="left", fill="x", expand=True)

        self._chevron = tk.Label(
            self._inner, text="\u25be",  # ▾
            bg=Theme.SURFACE_ALT, fg=Theme.TEXT_DIM,
            font=(self._family, 9), cursor="hand2",
        )
        self._chevron.pack(side="right")

        for w in (self, self._inner, self._label, self._chevron):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)

    # --- 视觉态 ---

    def _set_bg(self, color: str) -> None:
        self.configure(bg=color)
        self._inner.configure(bg=color)
        self._label.configure(bg=color)
        self._chevron.configure(bg=color)

    def _on_enter(self, _evt) -> None:
        if not self._hover:
            self._hover = True
            self._set_bg(Theme.SURFACE_HOVER)

    def _on_leave(self, evt) -> None:
        x_root, y_root = evt.x_root, evt.y_root
        x1, y1 = self.winfo_rootx(), self.winfo_rooty()
        x2 = x1 + self.winfo_width()
        y2 = y1 + self.winfo_height()
        if x1 <= x_root < x2 and y1 <= y_root < y2:
            return
        if self._hover:
            self._hover = False
            self._set_bg(Theme.SURFACE_ALT)

    # --- 弹出原生菜单 ---

    def _on_click(self, _evt) -> None:
        family = self._family
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=Theme.SURFACE,
            fg=Theme.TEXT,
            activebackground=Theme.ACCENT,
            activeforeground=Theme.ACCENT_TEXT,
            bd=0, relief="flat",
            font=(family, 10),
            # 让选中行更宽
            activeborderwidth=0,
        )
        for v in self._values:
            menu.add_command(
                label=v,
                command=lambda val=v: self._select(val),
            )
        # 弹在触发器正下方
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 2
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _select(self, value: str) -> None:
        self._var.set(value)


# ---------------------------------------------------------------------------
# 自绘细滚动条 (无箭头, 仅 thumb)
# ---------------------------------------------------------------------------


class TinyScrollbar(tk.Canvas):
    """简洁滚动条: 无箭头, 默认极细, 悬停时变粗。"""

    BAR_W = 12
    THUMB_W_NORMAL = 4
    THUMB_W_HOVER = 8

    def __init__(self, parent: tk.Widget, command: Callable[..., None], bg: str = Theme.SURFACE) -> None:
        super().__init__(
            parent, width=self.BAR_W, bg=bg,
            highlightthickness=0, borderwidth=0,
        )
        self._command = command
        self._first = 0.0
        self._last = 1.0
        self._hover = False
        self._dragging = False
        self._drag_offset = 0

        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set(self, first: float, last: float) -> None:
        try:
            self._first = float(first)
            self._last = float(last)
        except (TypeError, ValueError):
            return
        self._redraw()

    def _on_enter(self, _evt) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _evt) -> None:
        self._hover = False
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        h = self.winfo_height()
        if h <= 2:
            return
        if self._last - self._first >= 0.999:
            return
        thumb_y1 = self._first * h
        thumb_y2 = self._last * h
        thumb_w = self.THUMB_W_HOVER if (self._hover or self._dragging) else self.THUMB_W_NORMAL
        x_center = self.BAR_W / 2
        x1 = x_center - thumb_w / 2
        x2 = x_center + thumb_w / 2
        # 简单矩形 thumb (Tk 会让两端有轻微圆滑); 之前的圆角多边形会有毛刺
        self.create_rectangle(
            x1, thumb_y1 + 2, x2, thumb_y2 - 2,
            fill=Theme.MUTED, outline="",
        )

    def _y_to_first(self, y: float) -> float:
        h = self.winfo_height()
        if h <= 0:
            return 0.0
        size = self._last - self._first
        new_first = (y / h) - size / 2
        return max(0.0, min(1.0 - size, new_first))

    def _on_press(self, evt) -> None:
        h = self.winfo_height()
        thumb_y1 = self._first * h
        thumb_y2 = self._last * h
        if thumb_y1 <= evt.y <= thumb_y2:
            self._dragging = True
            self._drag_offset = evt.y - thumb_y1
        else:
            new_first = self._y_to_first(evt.y)
            self._command("moveto", new_first)
            self._dragging = True
            size = self._last - self._first
            self._drag_offset = size * h / 2
        self._redraw()

    def _on_drag(self, evt) -> None:
        if not self._dragging:
            return
        h = self.winfo_height()
        if h <= 0:
            return
        new_first_y = evt.y - self._drag_offset
        size = self._last - self._first
        new_first = max(0.0, min(1.0 - size, new_first_y / h))
        self._command("moveto", new_first)

    def _on_release(self, _evt) -> None:
        self._dragging = False
        self._redraw()


# ---------------------------------------------------------------------------
# 主应用
# ---------------------------------------------------------------------------


class App:
    def __init__(
        self,
        cfg: Config,
        watcher: NcmWatcher,
        on_quit: Optional[Callable[[], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.watcher = watcher
        self._on_quit = on_quit
        self._event_q: "queue.Queue[tuple]" = queue.Queue()
        self._success_count = 0
        self._error_count = 0
        self.watcher.callback = self._on_watcher_event

        self.root = tk.Tk()
        self.root.title("AutoNcm2Mp3")
        self.root.geometry("860x760")
        self.root.minsize(820, 720)
        self.root.configure(bg=Theme.BG)
        self._family = _resolve_font_family()

        self._apply_window_icon()
        self._init_ttk_style()
        self._build_ui()
        self._sync_from_cfg()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._drain_events)

    # ------------------------------------------------------------------
    # 主题 / ttk
    # ------------------------------------------------------------------

    def _init_ttk_style(self) -> None:
        try:
            tkfont.nametofont("TkDefaultFont").configure(family=self._family, size=10)
            tkfont.nametofont("TkTextFont").configure(family=self._family, size=10)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # 图标
    # ------------------------------------------------------------------

    def _apply_window_icon(self) -> None:
        if LOGO_ICO is not None:
            try:
                self.root.iconbitmap(default=str(LOGO_ICO))
                return
            except tk.TclError:
                pass
        if LOGO_PNG is not None:
            try:
                from PIL import Image, ImageTk

                img = Image.open(LOGO_PNG).convert("RGBA")
                img.thumbnail((256, 256))
                self._icon_photo = ImageTk.PhotoImage(img)
                self.root.iconphoto(True, self._icon_photo)
            except Exception as exc:  # noqa: BLE001
                logger.debug("窗口图标加载失败: %s", exc)

    # ------------------------------------------------------------------
    # 顶栏
    # ------------------------------------------------------------------

    def _build_header(self, parent: tk.Widget) -> None:
        header = tk.Frame(parent, bg=Theme.BG)
        header.pack(fill="x", padx=24, pady=(20, 8))

        left = tk.Frame(header, bg=Theme.BG)
        left.pack(side="left", fill="y")

        if LOGO_PNG is not None:
            try:
                from PIL import Image, ImageTk

                img = Image.open(LOGO_PNG).convert("RGBA")
                img.thumbnail((40, 40))
                self._header_logo = ImageTk.PhotoImage(img)
                tk.Label(left, image=self._header_logo, bg=Theme.BG).pack(side="left", padx=(0, 12))
            except Exception:  # noqa: BLE001
                pass

        text_box = tk.Frame(left, bg=Theme.BG)
        text_box.pack(side="left", fill="y")
        tk.Label(
            text_box, text="AutoNcm2Mp3",
            bg=Theme.BG, fg=Theme.TEXT,
            font=(self._family, 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            text_box, text="网易云 NCM 自动转换  ·  Mer3y 出品",
            bg=Theme.BG, fg=Theme.TEXT_DIM,
            font=(self._family, 9),
        ).pack(anchor="w")

        right = tk.Frame(header, bg=Theme.BG)
        right.pack(side="right", fill="y")

        self._status_dot = tk.Canvas(
            right, width=12, height=12, bg=Theme.BG,
            highlightthickness=0, borderwidth=0,
        )
        self._status_dot.pack(side="left", padx=(0, 6), pady=14)
        self._status_dot_id = self._status_dot.create_oval(
            2, 2, 10, 10, fill=Theme.DOT_OFF, outline="",
        )

        self.var_status_text = tk.StringVar(value="已停止")
        tk.Label(
            right, textvariable=self.var_status_text,
            bg=Theme.BG, fg=Theme.TEXT_DIM, font=(self._family, 9),
        ).pack(side="left")

    # ------------------------------------------------------------------
    # UI 主体
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_header(self.root)

        container = tk.Frame(self.root, bg=Theme.BG)
        container.pack(fill="both", expand=True, padx=24, pady=(4, 8))

        self._build_paths_card(container)
        self._build_options_card(container)
        self._build_actions_card(container)
        self._build_log_card(container)

        self._build_footer(self.root)

    # ----- 路径卡 ------------------------------------------------------

    def _build_paths_card(self, parent: tk.Widget) -> None:
        card = Card(parent, title="路径")
        card.pack(fill="x", pady=(0, 12))

        body = card.body
        body.columnconfigure(0, weight=1)

        tk.Label(
            body, text="网易云下载目录",
            bg=Theme.SURFACE, fg=Theme.TEXT_DIM,
            font=(self._family, 9), anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        self.var_download = tk.StringVar()
        FlatEntry(body, self.var_download).grid(
            row=1, column=0, sticky="ew", padx=(0, 8))
        FlatButton(body, "浏览", command=self._pick_download).grid(
            row=1, column=1, sticky="e")

        tk.Label(
            body, text="输出目录  ·  留空则与源文件同目录",
            bg=Theme.SURFACE, fg=Theme.TEXT_DIM,
            font=(self._family, 9), anchor="w",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(14, 4))

        self.var_output = tk.StringVar()
        FlatEntry(body, self.var_output).grid(
            row=3, column=0, sticky="ew", padx=(0, 8))
        FlatButton(body, "浏览", command=self._pick_output).grid(
            row=3, column=1, sticky="e")

    # ----- 设置卡 ------------------------------------------------------

    def _build_options_card(self, parent: tk.Widget) -> None:
        card = Card(parent, title="设置")
        card.pack(fill="x", pady=(0, 12))

        body = card.body
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        # ---- 行 0: 自动监控 / 开机自启 ----
        self.var_watch = tk.BooleanVar()
        Switch(
            body, "启用自动监控",
            description="下载完成后立即转换",
            variable=self.var_watch, command=self._toggle_watch,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 12))

        self.var_autostart = tk.BooleanVar()
        Switch(
            body, "开机自启",
            description="随系统启动并静默运行",
            variable=self.var_autostart, command=self._toggle_autostart,
        ).grid(row=0, column=1, sticky="ew", pady=(0, 12), padx=(20, 0))

        # ---- 行 1: 强制 MP3 / 启动后最小化 ----
        self.var_force_mp3 = tk.BooleanVar()
        Switch(
            body, "强制 FLAC 转 MP3",
            description="需要 ffmpeg 在 PATH 或同目录下",
            variable=self.var_force_mp3, command=self._save_cfg,
        ).grid(row=1, column=0, sticky="ew", pady=(0, 12))

        self.var_minimized = tk.BooleanVar()
        Switch(
            body, "启动后最小化到托盘",
            description="安静驻留, 不打扰当前工作",
            variable=self.var_minimized, command=self._save_cfg,
        ).grid(row=1, column=1, sticky="ew", pady=(0, 12), padx=(20, 0))

        # 分隔线
        sep = tk.Frame(body, bg=Theme.BORDER, height=1)
        sep.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 14))

        # ---- 行 3-4: 下拉框 ----
        tk.Label(
            body, text="MP3 比特率",
            bg=Theme.SURFACE, fg=Theme.TEXT_DIM, font=(self._family, 9),
            anchor="w",
        ).grid(row=3, column=0, sticky="w", pady=(0, 4))
        tk.Label(
            body, text="转换成功后",
            bg=Theme.SURFACE, fg=Theme.TEXT_DIM, font=(self._family, 9),
            anchor="w",
        ).grid(row=3, column=1, sticky="w", pady=(0, 4), padx=(20, 0))

        self.var_bitrate = tk.StringVar()
        Dropdown(
            body, self.var_bitrate,
            values=["128k", "192k", "256k", "320k"],
            width=180,
        ).grid(row=4, column=0, sticky="w")
        self.var_bitrate.trace_add("write", lambda *_: self._save_cfg())

        self.var_dispose = tk.StringVar()
        Dropdown(
            body, self.var_dispose,
            values=list(ON_SUCCESS_LABELS.values()),
            width=240,
        ).grid(row=4, column=1, sticky="w", padx=(20, 0))
        self.var_dispose.trace_add("write", lambda *_: self._save_cfg())

        if not autostart.is_supported():
            tk.Label(
                body,
                text="注: 当前系统不支持自启动 (仅 Windows 可用)",
                bg=Theme.SURFACE, fg=Theme.WARN, font=(self._family, 9),
            ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))

    # ----- 操作卡 ------------------------------------------------------

    def _build_actions_card(self, parent: tk.Widget) -> None:
        card = Card(parent, title="快捷操作")
        card.pack(fill="x", pady=(0, 12))

        body = card.body
        bar = tk.Frame(body, bg=Theme.SURFACE)
        bar.pack(fill="x")

        FlatButton(bar, "扫描已有 NCM", command=self._scan_existing, kind="primary").pack(side="left", padx=(0, 8))
        FlatButton(bar, "打开输出文件夹", command=self._open_output).pack(side="left", padx=(0, 8))
        FlatButton(bar, "清空日志", command=self._clear_log).pack(side="left")
        FlatButton(bar, "最小化到托盘", command=self._on_close).pack(side="right")

    # ----- 日志卡 ------------------------------------------------------

    def _build_log_card(self, parent: tk.Widget) -> None:
        card = Card(parent, title="转换日志")
        card.pack(fill="both", expand=True)

        body = card.body
        wrap = tk.Frame(
            body, bg=Theme.SURFACE_ALT,
            highlightthickness=1,
            highlightbackground=Theme.BORDER,
            highlightcolor=Theme.BORDER,
        )
        wrap.pack(fill="both", expand=True)

        self.txt_log = tk.Text(
            wrap,
            wrap="none",
            state="disabled",
            bg=Theme.SURFACE_ALT,
            fg=Theme.TEXT_DIM,
            insertbackground=Theme.TEXT,
            relief="flat",
            highlightthickness=0,
            font=("Consolas", 10),
            padx=12,
            pady=8,
        )
        self.txt_log.tag_config("ok", foreground=Theme.OK)
        self.txt_log.tag_config("err", foreground=Theme.ERR)
        self.txt_log.tag_config("info", foreground=Theme.ACCENT)
        self.txt_log.tag_config("ts", foreground=Theme.MUTED)

        scrollbar = TinyScrollbar(wrap, command=self.txt_log.yview, bg=Theme.SURFACE_ALT)
        self.txt_log.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y", padx=(0, 2), pady=2)
        self.txt_log.pack(side="left", fill="both", expand=True)

    # ----- 底部 (含 Mer3y 署名) ----------------------------------------

    def _build_footer(self, parent: tk.Widget) -> None:
        bar = tk.Frame(parent, bg=Theme.BG)
        bar.pack(fill="x", side="bottom", padx=24, pady=(0, 14))

        self.var_status = tk.StringVar(value="就绪")
        tk.Label(
            bar, textvariable=self.var_status,
            bg=Theme.BG, fg=Theme.TEXT_DIM,
            font=(self._family, 9),
        ).pack(side="left")

        signature = tk.Frame(bar, bg=Theme.BG)
        signature.pack(side="right")
        tk.Label(
            signature, text="Crafted by",
            bg=Theme.BG, fg=Theme.MUTED,
            font=(self._family, 9),
        ).pack(side="left", padx=(0, 4))
        tk.Label(
            signature, text="Mer3y",
            bg=Theme.BG, fg=Theme.ACCENT,
            font=(self._family, 10, "bold"),
        ).pack(side="left")

    # ------------------------------------------------------------------
    # 配置同步
    # ------------------------------------------------------------------

    def _sync_from_cfg(self) -> None:
        self.var_download.set(self.cfg.download_dir)
        self.var_output.set(self.cfg.output_dir)
        self.var_force_mp3.set(self.cfg.force_mp3)
        self.var_bitrate.set(self.cfg.mp3_bitrate)
        self.var_dispose.set(ON_SUCCESS_LABELS.get(self.cfg.on_success, ON_SUCCESS_LABELS["recycle"]))
        self.var_watch.set(self.cfg.watch_enabled)
        self.var_minimized.set(self.cfg.start_minimized)
        self.var_autostart.set(self.cfg.autostart and autostart.is_enabled())
        self._refresh_status()

    def _save_cfg(self) -> None:
        self.cfg.download_dir = self.var_download.get().strip()
        self.cfg.output_dir = self.var_output.get().strip()
        self.cfg.force_mp3 = bool(self.var_force_mp3.get())
        self.cfg.mp3_bitrate = self.var_bitrate.get() or "320k"
        self.cfg.on_success = ON_SUCCESS_REVERSE.get(self.var_dispose.get(), "recycle")
        self.cfg.watch_enabled = bool(self.var_watch.get())
        self.cfg.start_minimized = bool(self.var_minimized.get())
        self.cfg.autostart = bool(self.var_autostart.get())
        self.cfg.save()
        self._refresh_status()

    # ------------------------------------------------------------------
    # 按钮回调
    # ------------------------------------------------------------------

    def _pick_download(self) -> None:
        d = filedialog.askdirectory(
            title="选择网易云下载目录",
            initialdir=self.var_download.get() or os.path.expanduser("~"),
        )
        if d:
            self.var_download.set(d)
            self._save_cfg()
            self._restart_watch_if_needed()

    def _pick_output(self) -> None:
        d = filedialog.askdirectory(
            title="选择输出目录 (留空=与源同目录)",
            initialdir=self.var_output.get() or os.path.expanduser("~"),
        )
        if d:
            self.var_output.set(d)
            self._save_cfg()

    def _toggle_watch(self) -> None:
        self._save_cfg()
        if self.cfg.watch_enabled:
            if not self.cfg.download_dir or not Path(self.cfg.download_dir).is_dir():
                messagebox.showwarning("提示", "请先选择有效的下载目录")
                self.var_watch.set(False)
                self._save_cfg()
                return
            self.watcher.start()
            self._log("info", f"已开启监控: {self.cfg.download_dir}")
        else:
            self.watcher.stop()
            self._log("info", "已停止监控")
        self._refresh_status()

    def _toggle_autostart(self) -> None:
        if not autostart.is_supported():
            messagebox.showinfo("提示", "当前系统暂不支持开机自启 (仅 Windows 可用)")
            self.var_autostart.set(False)
            self._save_cfg()
            return
        try:
            autostart.sync(bool(self.var_autostart.get()))
        except Exception as exc:  # noqa: BLE001
            logger.exception("自启动设置失败")
            messagebox.showerror("失败", f"写入开机启动项失败:\n{exc}")
            self.var_autostart.set(autostart.is_enabled())
        self._save_cfg()
        self._log("info", "已写入开机启动项" if self.var_autostart.get() else "已移除开机启动项")

    def _restart_watch_if_needed(self) -> None:
        if self.cfg.watch_enabled:
            self.watcher.stop()
            self.watcher.start()

    def _scan_existing(self) -> None:
        if not self.cfg.download_dir or not Path(self.cfg.download_dir).is_dir():
            messagebox.showwarning("提示", "请先选择有效的下载目录")
            return

        def run() -> None:
            n = self.watcher.scan_existing()
            self._log("info", f"扫描完成，已加入队列 {n} 个文件")

        threading.Thread(target=run, daemon=True).start()

    def _open_output(self) -> None:
        target = self.cfg.output_dir or self.cfg.download_dir
        if target and Path(target).is_dir():
            try:
                os.startfile(target)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                messagebox.showwarning("提示", f"无法打开目录: {exc}")
        else:
            messagebox.showwarning("提示", "目录不存在")

    def _clear_log(self) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    # ------------------------------------------------------------------
    # 转换事件
    # ------------------------------------------------------------------

    def _on_watcher_event(self, stage, path, result=None, error=None) -> None:
        self._event_q.put((stage, path, result, error))

    def _drain_events(self) -> None:
        try:
            while True:
                stage, path, result, error = self._event_q.get_nowait()
                self._handle_event(stage, path, result, error)
        except queue.Empty:
            pass
        self.root.after(150, self._drain_events)

    def _handle_event(self, stage, path, result: Optional[ConvertResult], error) -> None:
        name = Path(path).name
        if stage == "start":
            self._log("info", f"→ {name}")
        elif stage == "success":
            self._success_count += 1
            who = result.title if result else name
            artists = f"  ·  {result.artists}" if result and result.artists else ""
            dst = result.dst.name if result else ""
            self._log("ok", f"✓ {who}{artists}    {dst}")
        elif stage == "error":
            self._error_count += 1
            self._log("err", f"✗ {name}    {error}")
        self._refresh_status()

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _log(self, tag: str, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", f"{ts}  ", "ts")
        self.txt_log.insert("end", msg + "\n", tag)
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _refresh_status(self) -> None:
        running = self.watcher.running
        if hasattr(self, "_status_dot"):
            color = Theme.DOT_OK if running else Theme.DOT_OFF
            self._status_dot.itemconfigure(self._status_dot_id, fill=color)
        if hasattr(self, "var_status_text"):
            self.var_status_text.set("监控中" if running else "已停止")
        if hasattr(self, "var_status"):
            self.var_status.set(
                f"成功 {self._success_count}  ·  失败 {self._error_count}    "
                f"日志文件: {LOG_PATH}"
            )

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        if self._on_quit is None:
            self.root.withdraw()
        else:
            self._on_quit()

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self) -> None:
        self.root.withdraw()

    def refresh_from_config(self) -> None:
        self._sync_from_cfg()

    def quit(self) -> None:
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def run_mainloop(self) -> None:
        self.root.mainloop()


__all__ = ["App", "Theme"]
