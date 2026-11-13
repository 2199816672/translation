#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""状态总线：给状态栏加 owner / 优先级 / 结束态回收（零依赖）。

以前每个后台链路都直接往同一行状态栏写文字，OCR 初始化完的一句「就绪」
会把「正在翻译…」盖掉，任务结束后又没人把过时的「正在…」清掉。这里把
状态变成「每个任务一条、互相不覆盖」：

* 优先级高的（长任务）先显示，结束后 ``clear(owner)`` 自动回落到下一条；
* 同一个 owner 重复 push 只覆盖自己那一条，不会越堆越多；
* 全部清空时回落到可配置的空闲文案，而不是留下最后一句「正在…」。
"""
from __future__ import annotations

import threading


class StatusBus:
    def __init__(self, emit=None, idle_text="就绪", idle_color="#4ade80"):
        self._emit = emit
        self._idle_text = idle_text
        self._idle_color = idle_color
        self._items = {}          # owner -> {text, color, priority, seq}
        self._seq = 0
        self._lock = threading.Lock()
        self._last = None

    def push(self, owner, text, color=None, priority=0):
        """写入 / 覆盖一个 owner 的状态。owner 为空时用 ``general``。"""
        owner = str(owner or 'general')
        text = str(text or '')
        color = color or '#71717a'
        with self._lock:
            self._seq += 1
            self._items[owner] = {
                'text': text, 'color': color,
                'priority': int(priority), 'seq': self._seq,
            }
            self._publish_locked()

    def clear(self, owner):
        """任务结束时回收；只在自己是当前显示者时才回落，避免抖动。"""
        with self._lock:
            self._items.pop(str(owner or 'general'), None)
            self._publish_locked()

    def clear_all(self):
        with self._lock:
            self._items.clear()
            self._publish_locked()

    def current(self):
        """返回当前应显示的 ``(text, color)``。"""
        with self._lock:
            return self._current_locked()

    def snapshot(self):
        with self._lock:
            return [
                {'owner': owner, **dict(item)}
                for owner, item in sorted(
                    self._items.items(),
                    key=lambda kv: (-kv[1]['priority'], -kv[1]['seq']))
            ]

    def _current_locked(self):
        if not self._items:
            return self._idle_text, self._idle_color
        item = max(self._items.values(),
                   key=lambda v: (v['priority'], v['seq']))
        return item['text'], item['color']

    def _publish_locked(self):
        cur = self._current_locked()
        if cur == self._last:
            return
        self._last = cur
        if self._emit is not None:
            try:
                self._emit(cur[0], cur[1])
            except Exception:
                # 状态栏失效不能反过来打断业务链路
                pass
