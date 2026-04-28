"""单实例机制 (Single Instance Lock)。

实现思路:
    1. 用 ``CreateMutexW`` 占住一个全局命名互斥体;
    2. 第二次启动时, ``GetLastError() == ERROR_ALREADY_EXISTS``,
       说明已经有一个实例在跑;
    3. 第二个实例通过 ``FindWindow`` 找到第一个实例的隐藏信号窗口,
       发一条 ``WM_USER`` 消息让它把主窗口显示出来;
    4. 第二个实例提示用户后退出。

跨平台: 非 Windows 平台 fallback 用 fcntl 文件锁, 但暂不实现激活逻辑
(此项目本来就只面向 Windows)。
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import wintypes
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 全局互斥体名 (要够独特, 避免和其他软件冲突)
MUTEX_NAME = "Global\\AutoNcm2Mp3-Mer3y-SingleInstance-Mutex-9F2C"
# 隐藏信号窗口的类名
SIGNAL_WINDOW_CLASS = "AutoNcm2Mp3SignalWindow_9F2C"
# 自定义消息: WM_USER + 0x42 -> 激活主实例
WM_ACTIVATE_APP = 0x0400 + 0x42  # WM_USER = 0x0400


# ---------------------------------------------------------------------------
# Win32 常量与原型
# ---------------------------------------------------------------------------

ERROR_ALREADY_EXISTS = 183
WS_OVERLAPPED = 0x00000000
HWND_MESSAGE = -3  # message-only window
WM_DESTROY = 0x0002


def _is_windows() -> bool:
    return os.name == "nt"


# ---------------------------------------------------------------------------
# 互斥体
# ---------------------------------------------------------------------------


class SingleInstanceLock:
    """占用一个命名互斥体, 析构时释放。

    用法::

        lock = SingleInstanceLock()
        if lock.already_running:
            lock.signal_existing()
            sys.exit(0)
        # 这里就是首个实例了
    """

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._handle: Optional[int] = None
        self._already_running = False

        if not _is_windows():
            logger.debug("非 Windows 平台, 跳过单实例锁")
            return

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # HANDLE CreateMutexW(SECURITY_ATTRIBUTES*, BOOL, LPCWSTR);
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        self._kernel32 = kernel32
        # 第一参数 NULL = 默认安全属性, 第二个 False = 不立即拥有
        handle = kernel32.CreateMutexW(None, False, name)
        last_err = ctypes.get_last_error()
        if not handle:
            logger.warning("CreateMutexW 失败, errno=%s", last_err)
            return

        self._handle = handle
        if last_err == ERROR_ALREADY_EXISTS:
            self._already_running = True
            logger.info("检测到已有实例在运行")

    @property
    def already_running(self) -> bool:
        return self._already_running

    def release(self) -> None:
        if self._handle and _is_windows():
            try:
                self._kernel32.CloseHandle(self._handle)
            except Exception:  # noqa: BLE001
                pass
            self._handle = None

    # 上下文管理 / 析构 都释放
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.release()

    def __del__(self):
        self.release()

    # ------------------------------------------------------------------
    # 通知已运行的实例: 把主窗口拉起来
    # ------------------------------------------------------------------

    def signal_existing(self) -> bool:
        """让已经在运行的实例显示主窗口。返回是否找到目标。"""
        if not _is_windows():
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL

        hwnd = user32.FindWindowW(SIGNAL_WINDOW_CLASS, None)
        if not hwnd:
            logger.warning("找不到现有实例的信号窗口")
            return False
        ok = user32.PostMessageW(hwnd, WM_ACTIVATE_APP, 0, 0)
        logger.info("已发送激活消息到 hwnd=%s ok=%s", hwnd, ok)
        return bool(ok)


# ---------------------------------------------------------------------------
# 隐藏的"信号窗口" - 接收 WM_ACTIVATE_APP 触发回调
# ---------------------------------------------------------------------------


class _SignalWindow:
    """注册一个 message-only 窗口监听激活消息。

    这个窗口在子线程的 message loop 里运行, 收到 WM_ACTIVATE_APP 时
    回调 ``on_activate``。回调会被 marshal 到 Tk 主线程 (使用 root.after)。
    """

    def __init__(self, on_activate: Callable[[], None]) -> None:
        self._on_activate = on_activate
        self._hwnd: Optional[int] = None
        self._wndproc_ref = None  # 防止 GC
        self._cls_atom = 0
        self._thread = None
        self._stop_requested = False

    # ---- Win32 套路 ----

    @staticmethod
    def _wndclass_struct():
        """声明 WNDCLASSW 结构体 (用 ctypes Structure)。"""
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM,
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        return WNDCLASSW, WNDPROC

    def start(self) -> None:
        if not _is_windows():
            return
        import threading

        # 注: 必须在子线程里跑, 因为我们的主线程要给 Tkinter 用
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        WNDCLASSW, WNDPROC = self._wndclass_struct()

        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
            wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = ctypes.c_long
        user32.GetMessageW.argtypes = [
            ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT,
        ]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.c_void_p]
        user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
        user32.DispatchMessageW.restype = ctypes.c_long
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.DestroyWindow.argtypes = [wintypes.HWND]

        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        # ---- 窗口过程 ----
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_ACTIVATE_APP:
                try:
                    self._on_activate()
                except Exception:  # noqa: BLE001
                    logger.exception("激活回调异常")
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc_ref = WNDPROC(wndproc)

        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = SIGNAL_WINDOW_CLASS

        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            err = ctypes.get_last_error()
            # 1410 = ERROR_CLASS_ALREADY_EXISTS, 兜底兼容
            if err != 1410:
                logger.warning("RegisterClassW 失败 err=%s", err)
                return
        self._cls_atom = atom

        # message-only 窗口: parent 设为 HWND_MESSAGE
        self._hwnd = user32.CreateWindowExW(
            0, SIGNAL_WINDOW_CLASS, "AutoNcm2Mp3 Signal", 0,
            0, 0, 0, 0, HWND_MESSAGE, None, wc.hInstance, None,
        )
        if not self._hwnd:
            err = ctypes.get_last_error()
            logger.warning("CreateWindowExW 失败 err=%s", err)
            return
        logger.debug("信号窗口已创建 hwnd=%s", self._hwnd)

        # 标准 message loop
        # MSG 结构体
        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt_x", wintypes.LONG),
                ("pt_y", wintypes.LONG),
            ]

        msg = MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:  # WM_QUIT 或 错误
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        logger.debug("信号窗口消息循环退出")

    def stop(self) -> None:
        if not _is_windows() or self._hwnd is None:
            return
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)
        except Exception:  # noqa: BLE001
            pass


def install_signal_window(on_activate: Callable[[], None]) -> _SignalWindow:
    """安装一个隐藏信号窗口, 收到激活请求时调 ``on_activate``。

    回调会在子线程里被调用, 调用方需要自己 marshal 到 GUI 线程。
    """
    sw = _SignalWindow(on_activate)
    sw.start()
    return sw


__all__ = [
    "SingleInstanceLock",
    "install_signal_window",
    "MUTEX_NAME",
    "SIGNAL_WINDOW_CLASS",
]
