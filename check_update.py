#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版本更新检测（兼容层）。

完整的检查 / 下载 / 一键升级逻辑已移到 :mod:`updater`。本文件保留旧接口，
供外部脚本与历史调用继续使用；界面代码请直接用 ``updater`` +
``update_dialog``，才能拿到安装包资产并一键升级。
"""
import queue
import threading

from config import APP_VERSION, BILIBILI_DYNAMIC_URL, GITHUB_REPO
from updater import (  # noqa: F401  (保持旧 import 可用)
    API_URL, RELEASES_URL, ReleaseInfo, UpdateError, UpdateCancelled,
    fetch_latest_release, is_newer, version_tuple, check_update as _check,
    installer_url, safe_download_path, download_file, verify_download,
    queue_install, launch_installer,
)


def _parse_version(v):
    """旧接口：把 'v1.4.0' 解析成 (1, 4, 0, 0)。"""
    return version_tuple(v)


def check_update():
    """同步检测，返回 dict（含 has_update / latest / url / error / info）。"""
    return _check()


class UpdateChecker:
    """线程安全的版本检测器，通过 queue + QTimer 回到主线程。

    相比旧版：timer 有 parent 不会在窗口销毁后残留，重复调用 check() 不会
    叠加线程 / 覆盖回调，callback 只会执行一次。
    """

    def __init__(self, parent=None):
        from PySide6.QtCore import QTimer
        self._q = queue.Queue()
        self._callback = None
        self._busy = False
        self._timer = QTimer(parent)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)

    def check(self, callback):
        """异步检测，callback(result) 在主线程调用。"""
        if self._busy:
            return False
        self._busy = True
        self._callback = callback
        self._timer.start()
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self):
        try:
            self._q.put(check_update())
        except Exception as exc:
            self._q.put({"has_update": False, "current": APP_VERSION,
                         "latest": "", "url": "", "error": f"检测失败：{exc}"})

    def _poll(self):
        try:
            result = self._q.get_nowait()
        except queue.Empty:
            return
        self._timer.stop()
        self._busy = False
        callback, self._callback = self._callback, None
        if callback:
            callback(result)
