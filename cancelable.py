#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「不可中断的长阻塞调用」包装成可以立刻取消的调用（零依赖）。

在线转录接口的单次请求 timeout 长达 600 秒，用户点「停止」后如果干等
请求返回，界面会像卡死一样。Python 无法安全杀死线程，因此这里换个思路：

* 把阻塞调用放进守护线程，调用方轮询 ``cancel_event``；
* 一旦取消，立刻抛 :class:`CallCancelled` 把控制权还给 UI，不 join 线程；
* ``on_cancel`` 可选，用于关闭底层 socket / response，让后台线程尽早退出；
* 后台线程是 daemon，请求自然结束后自行回收，不会再碰任何 UI 状态。

这样「停止」是即时生效的；唯一代价是那一个已经发出的请求可能在后台
继续占用网络，但不会再阻塞用户，也不会污染后续任务的结果。
"""
from __future__ import annotations

import queue
import threading


class CallCancelled(Exception):
    """调用被用户主动取消，调用方应按「已停止」而不是「失败」处理。"""


def run_cancelable(fn, cancel_event=None, poll=0.15, on_cancel=None):
    """执行 ``fn()``；``cancel_event`` 被置位时立即抛 :class:`CallCancelled`。

    没有 cancel_event 时等价于直接调用 ``fn()``，不引入任何线程开销。
    ``fn`` 内部抛出的异常原样透传给调用方。
    """
    if cancel_event is None:
        return fn()
    if cancel_event.is_set():
        raise CallCancelled("已停止")

    box = queue.Queue(maxsize=1)

    def worker():
        try:
            box.put((True, fn()))
        except Exception as exc:      # 异常要送回调用线程，绝不能吞掉
            box.put((False, exc))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    while True:
        try:
            ok, payload = box.get(timeout=poll)
            break
        except queue.Empty:
            if cancel_event.is_set():
                if on_cancel is not None:
                    try:
                        on_cancel()
                    except Exception:
                        pass
                raise CallCancelled("已停止")

    if ok:
        return payload
    raise payload
