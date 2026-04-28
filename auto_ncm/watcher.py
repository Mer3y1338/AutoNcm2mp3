"""下载目录监控。

使用 :mod:`watchdog` 监听网易云下载目录。
注意：网易云客户端在写入时通常先生成 ``.ncm.uctmp`` 临时文件，
完成后再重命名为 ``.ncm``，因此我们同时监听 *创建* 与 *重命名* 事件，
并在处理前确认文件大小稳定，避免读到半截文件。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import (
    FileCreatedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from .config import Config
from .converter import ConvertError, ConvertResult, convert_one

logger = logging.getLogger(__name__)


# 上层 GUI 用的回调签名
ProgressCallback = Callable[[str, str, Optional[ConvertResult], Optional[Exception]], None]
"""``(stage, path, result, error)``

stage:
    - ``"start"``     : 即将开始处理
    - ``"success"``   : 成功
    - ``"error"``     : 失败
"""


def _is_ncm(path: str) -> bool:
    return path.lower().endswith(".ncm")


def _wait_until_stable(path: Path, timeout: float = 30.0, poll: float = 0.6) -> bool:
    """等待文件大小稳定 (写入完成)。"""
    last = -1
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last and size > 0:
            return True
        last = size
        time.sleep(poll)
    logger.warning("等待文件稳定超时: %s", path)
    return False


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "NcmWatcher") -> None:
        super().__init__()
        self._watcher = watcher

    def on_created(self, event):
        if event.is_directory:
            return
        if isinstance(event, FileCreatedEvent) and _is_ncm(event.src_path):
            self._watcher.submit(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        if isinstance(event, FileMovedEvent) and _is_ncm(event.dest_path):
            self._watcher.submit(event.dest_path)


class NcmWatcher:
    """对外暴露的监控器封装。"""

    def __init__(self, cfg: Config, callback: Optional[ProgressCallback] = None) -> None:
        self.cfg = cfg
        self.callback = callback
        self._observer: Optional[Observer] = None
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ncm-conv")
        self._inflight: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 任务提交
    # ------------------------------------------------------------------

    def submit(self, path: str) -> None:
        """提交一个 NCM 文件到转换队列 (内部去重)。"""
        norm = os.path.normcase(os.path.abspath(path))
        with self._lock:
            if norm in self._inflight:
                return
            self._inflight.add(norm)
        self._executor.submit(self._run_one, path, norm)

    def scan_existing(self) -> int:
        """扫描下载目录里已有的 NCM 文件并提交。"""
        if not self.cfg.download_dir:
            return 0
        root = Path(self.cfg.download_dir)
        if not root.is_dir():
            return 0
        count = 0
        for p in root.rglob("*.ncm"):
            self.submit(str(p))
            count += 1
        return count

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _emit(self, stage, path, result=None, error=None):
        if self.callback:
            try:
                self.callback(stage, path, result, error)
            except Exception:  # noqa: BLE001
                logger.exception("回调异常")

    def _run_one(self, path: str, norm_key: str) -> None:
        try:
            self._emit("start", path)
            p = Path(path)
            if not p.exists():
                # 可能是临时事件，文件已经被重命名走
                return
            if not _wait_until_stable(p):
                self._emit("error", path, error=RuntimeError("文件长时间未写入完成"))
                return
            try:
                result = convert_one(p, self.cfg)
            except ConvertError as exc:
                logger.warning("转换失败 %s: %s", path, exc)
                self._emit("error", path, error=exc)
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("转换异常 %s", path)
                self._emit("error", path, error=exc)
                return
            logger.info("转换成功: %s -> %s", path, result.dst)
            self._emit("success", path, result=result)
        finally:
            with self._lock:
                self._inflight.discard(norm_key)

    # ------------------------------------------------------------------
    # 启停
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._observer is not None:
            return
        if not self.cfg.download_dir or not Path(self.cfg.download_dir).is_dir():
            logger.warning("下载目录无效，监控未启动: %r", self.cfg.download_dir)
            return
        observer = Observer()
        observer.schedule(_Handler(self), self.cfg.download_dir, recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer
        logger.info("已开始监控: %s", self.cfg.download_dir)

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=3)
            self._observer = None
            logger.info("监控已停止")

    def shutdown(self) -> None:
        """彻底关闭 (用于程序退出)。"""
        self.stop()
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)  # py3.9+
        except TypeError:
            self._executor.shutdown(wait=False)

    @property
    def running(self) -> bool:
        return self._observer is not None


__all__ = ["NcmWatcher", "ProgressCallback"]
