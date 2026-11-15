#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图时的窗口自动吸附（类似微信截图）。

用 Windows 原生 API 枚举【当前屏幕上真实可见】的顶层窗口，提供三件事：
- 只保留当前截图屏幕上的窗口，另一个显示器的窗口不参与；
- 按 Z 序剔除被上层窗口挡住的后台窗口（边界看不见，吸上去没意义）；
- 鼠标悬停时找出最内层窗口，单击即可整窗选中；手动拖框时把四条边
  吸到最近的窗口边界；非 Windows 平台 / 枚举失败时返回空列表。

返回的矩形都是「屏幕物理像素」的 (left, top, right, bottom)；
调用方（ScreenshotOverlay）负责按 DPI 缩放并换算成浮层局部坐标。
"""
from __future__ import annotations

import ctypes
import os

# wintypes 在非 Windows 上可能不可用；那里的 _user32() 会直接返回 None，
# 下面这些纯计算函数不依赖它，导入本模块不会让 Linux CI 挂掉。
if os.name == "nt":
    from ctypes import wintypes
else:  # pragma: no cover - 仅为了让跨平台测试能 import
    wintypes = None

__all__ = [
    "enum_visible_windows", "window_at", "snap_rect", "rect_tuple",
    "visible_ratio",
]

# 边界磁吸的触发距离（物理像素）
SNAP_THRESHOLD = 12
# 小于这个尺寸的窗口（提示条、悬浮球等）不参与吸附
MIN_WINDOW_W = 120
MIN_WINDOW_H = 80
# 被上层窗口挡到只剩这么点可见面积时不再吸附：屏幕上根本看不见它的边，
# 吸过去只会让用户莫名其妙（用户明确要求「后台窗口不必要吸附」）。
MIN_VISIBLE_RATIO = 0.15

_WS_EX_TOOLWINDOW = 0x00000080
_GWL_EXSTYLE = -20


def _user32():
    if os.name != "nt":
        return None
    try:
        dll = ctypes.WinDLL("user32", use_last_error=True)
        # EnumWindows 的第一个参数是回调指针，交给 ctypes 自动推断；
        # 显式写成 c_void_p 反而会让 WINFUNCTYPE 实例被拒。
        dll.EnumWindows.restype = wintypes.BOOL
        dll.IsWindowVisible.argtypes = [wintypes.HWND]
        dll.IsWindowVisible.restype = wintypes.BOOL
        dll.IsIconic.argtypes = [wintypes.HWND]
        dll.IsIconic.restype = wintypes.BOOL
        dll.GetWindowRect.argtypes = [wintypes.HWND,
                                      ctypes.POINTER(wintypes.RECT)]
        dll.GetWindowRect.restype = wintypes.BOOL
        dll.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        dll.GetWindowLongW.restype = ctypes.c_long
        dll.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        dll.GetWindowThreadProcessId.restype = wintypes.DWORD
        dll.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        dll.GetWindowTextLengthW.restype = ctypes.c_int
        dll.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                       ctypes.c_int]
        dll.GetWindowTextW.restype = ctypes.c_int
        return dll
    except Exception:
        return None


def clip_to_screen(rect, screen_rect):
    """把窗口矩形裁到当前屏幕内；中心不在该屏幕的窗口整体丢弃。

    rect / screen_rect 都是 (l, t, r, b) 的物理像素。多显示器下另一个屏幕
    上的窗口不该出现在当前截图的吸附候选里。
    """
    left, top, right, bottom = rect
    cx, cy = (left + right) / 2.0, (top + bottom) / 2.0
    sl, st, sr, sb = screen_rect
    if not (sl <= cx < sr and st <= cy < sb):
        return None
    cl, ct = max(left, sl), max(top, st)
    cr, cb = min(right, sr), min(bottom, sb)
    if cr <= cl or cb <= ct:
        return None
    return (cl, ct, cr, cb)


def visible_ratio(rect, above, samples=5):
    """网格采样估算 rect 未被 above（Z 序更靠前的窗口）遮挡的比例。

    纯计算、不依赖 Win32，方便跨平台回归测试。精确的矩形差集没必要：
    这里只需判断「这个窗口在屏幕上还看不看得见」，采样足够且开销可控。
    """
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        return 0.0
    blockers = [a for a in above if a[2] > a[0] and a[3] > a[1]]
    if not blockers:
        return 1.0
    total = visible = 0
    for i in range(samples):
        y = top + (bottom - top) * (i + 0.5) / samples
        for j in range(samples):
            x = left + (right - left) * (j + 0.5) / samples
            total += 1
            if not any(a[0] <= x < a[2] and a[1] <= y < a[3]
                       for a in blockers):
                visible += 1
    return visible / total if total else 0.0


def enum_visible_windows(exclude_pid=None, max_count=200,
                         screen_rect=None):
    """返回当前屏幕上【真实可见】的顶层窗口 [(l, t, r, b, title), ...]。

    - 排除隐藏 / 最小化 / 工具窗口 / 本进程窗口（截图浮层自己）；
    - screen_rect 为当前截图屏幕的物理像素矩形：中心不在该屏幕、或裁剪后
      过小的窗口一律丢弃，留下来的矩形会裁到屏幕内（不会吸到屏幕外）；
    - EnumWindows 天然按 Z 序从最顶层往下枚举，据此用网格采样剔除被上层
      窗口遮住的「后台窗口」——它们的边界在屏幕上看不见，吸附没有意义。
    """
    dll = _user32()
    if dll is None:
        return []
    if exclude_pid is None:
        exclude_pid = os.getpid()
    candidates = []
    limit = max(max_count * 3, 300)
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        try:
            if not dll.IsWindowVisible(hwnd) or dll.IsIconic(hwnd):
                return True
            if dll.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
                return True
            pid = wintypes.DWORD()
            dll.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == exclude_pid:
                return True
            rect = wintypes.RECT()
            if not dll.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            box = (rect.left, rect.top, rect.right, rect.bottom)
            if screen_rect is not None:
                box = clip_to_screen(box, screen_rect)
                if box is None:
                    return True
            w, h = box[2] - box[0], box[3] - box[1]
            if w < MIN_WINDOW_W or h < MIN_WINDOW_H:
                return True
            length = dll.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                dll.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
            candidates.append((box[0], box[1], box[2], box[3], title))
        except Exception:
            pass
        return len(candidates) < limit

    try:
        proc = callback_type(callback)
        dll.EnumWindows(proc, 0)
    except Exception:
        return []

    result = []
    for index, item in enumerate(candidates):
        above = [c[:4] for c in candidates[:index]]
        if visible_ratio(item[:4], above) < MIN_VISIBLE_RATIO:
            continue
        result.append(item)
        if len(result) >= max_count:
            break
    return result


def rect_tuple(qrect):
    """QRect / 任意带 left/top/right/bottom 的对象 → (l, t, r, b)。"""
    return (qrect.left(), qrect.top(), qrect.right(), qrect.bottom())


def window_at(point, windows):
    """返回包含 point 的最小窗口矩形（最内层优先），没有则 None。"""
    x, y = float(point[0]), float(point[1])
    hits = [w for w in windows
            if w[0] <= x < w[2] and w[1] <= y < w[3]]
    if not hits:
        return None
    return min(hits, key=lambda w: (w[2] - w[0]) * (w[3] - w[1]))


def _nearest_edge(value, edges, threshold):
    best, dist = value, threshold + 1
    for edge in edges:
        delta = abs(edge - value)
        if delta < dist:
            best, dist = edge, delta
    return best if dist <= threshold else value


def snap_rect(rect, windows, threshold=SNAP_THRESHOLD):
    """把选区四条边吸到最近的窗口边界；吸完仍保持合法尺寸。

    rect 是 (l, t, r, b)。窗口列表为空时原样返回。
    """
    if not windows:
        return rect
    left, top, right, bottom = rect
    edges_x, edges_y = [], []
    for w in windows:
        edges_x.extend((w[0], w[2]))
        edges_y.extend((w[1], w[3]))
    new_left = _nearest_edge(left, edges_x, threshold)
    new_right = _nearest_edge(right, edges_x, threshold)
    new_top = _nearest_edge(top, edges_y, threshold)
    new_bottom = _nearest_edge(bottom, edges_y, threshold)
    if new_right - new_left < 5 or new_bottom - new_top < 5:
        return rect
    return (new_left, new_top, new_right, new_bottom)
