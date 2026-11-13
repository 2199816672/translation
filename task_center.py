#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""活跃任务登记：让「后台还在跑什么」可见（零依赖）。

设置页的安装 / 下载、音视频页的转录、实时字幕、批量转录、在线更新分散在
几个页面里，退出时用户无从判断还有没有任务在跑。这里只做登记与查询，不
碰 UI、不碰线程：各处任务开始时 ``start()``、结束时 ``finish()``，任务
中心 UI / 退出确认 / 托盘角标都从同一个地方读。
"""
from __future__ import annotations

import threading
import time


class TaskCenter:
    def __init__(self, on_change=None):
        self._tasks = {}
        self._lock = threading.Lock()
        self._on_change = on_change

    def start(self, key, name, cancelable=False, cancel=None, detail=""):
        """登记一个任务；同名 key 覆盖，避免重复点击留下两条。"""
        with self._lock:
            self._tasks[str(key)] = {
                'key': str(key), 'name': str(name),
                'cancelable': bool(cancelable), 'cancel': cancel,
                'detail': str(detail or ''), 'started': time.time(),
            }
        self._notify()

    def update(self, key, detail):
        with self._lock:
            task = self._tasks.get(str(key))
            if task is None:
                return
            task['detail'] = str(detail or '')
        self._notify()

    def finish(self, key):
        with self._lock:
            self._tasks.pop(str(key), None)
        self._notify()

    def cancel(self, key):
        """置位取消标志；返回是否真的找到一个可取消任务。"""
        with self._lock:
            task = self._tasks.get(str(key))
        if task is None or not task['cancelable']:
            return False
        event = task['cancel']
        if event is None:
            return False
        try:
            event.set()
            return True
        except Exception:
            return False

    def cancel_all(self):
        """退出前统一请求停止，返回成功置位的任务数。"""
        with self._lock:
            keys = [t['key'] for t in self._tasks.values()
                    if t['cancelable'] and t['cancel'] is not None]
        return sum(1 for key in keys if self.cancel(key))

    def active(self):
        with self._lock:
            return [dict(t) for t in sorted(
                self._tasks.values(), key=lambda t: t['started'])]

    def count(self):
        with self._lock:
            return len(self._tasks)

    def summary(self):
        tasks = self.active()
        if not tasks:
            return "当前没有后台任务"
        names = "、".join(t['name'] for t in tasks[:3])
        more = f" 等 {len(tasks)} 项" if len(tasks) > 3 else ""
        return f"进行中：{names}{more}"

    def _notify(self):
        if self._on_change is None:
            return
        try:
            self._on_change(self.active())
        except Exception:
            pass


TASK_CENTER = TaskCenter()
