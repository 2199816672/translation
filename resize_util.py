#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选区几何计算：移动、单边缩放、四角等比缩放。"""
from PySide6.QtCore import QPoint, QRect

_CORNERS = ("tl", "tr", "bl", "br")
# 兼容「先横后纵」的旧命名，避免四角静默退化成单边缩放
_CORNER_ALIASES = {"lt": "tl", "rt": "tr", "lb": "bl", "rb": "br"}


def clamp_rect(rect, bounds):
    """把矩形限制在 bounds 内（尺寸超过时先缩小）。"""
    if bounds is None:
        return rect
    rect = QRect(rect)
    if rect.width() > bounds.width():
        rect.setWidth(bounds.width())
    if rect.height() > bounds.height():
        rect.setHeight(bounds.height())
    x = max(bounds.left(), min(rect.x(), bounds.right() - rect.width() + 1))
    y = max(bounds.top(), min(rect.y(), bounds.bottom() - rect.height() + 1))
    rect.moveTo(x, y)
    return rect


def resize_rect(start, mode, delta, min_w=20, min_h=20, bounds=None):
    """根据拖拽方向计算新选区。

    - mode 为 移动：整体平移；
    - 四角（tl/tr/bl/br）：**等比缩放**，对角固定，宽高按同一比例变化；
    - 单边（l/r/t/b）：只改变一个方向。
    """
    start = QRect(start)
    mode = _CORNER_ALIASES.get(mode, mode)
    if mode == "move":
        return clamp_rect(start.translated(delta), bounds)

    if mode in _CORNERS:
        anchor = {
            "tl": start.bottomRight(),
            "tr": start.bottomLeft(),
            "bl": start.topRight(),
            "br": start.topLeft(),
        }[mode]
        corners = {
            "tl": start.topLeft(), "tr": start.topRight(),
            "bl": start.bottomLeft(), "br": start.bottomRight(),
        }
        target = corners[mode] + delta
        # 偏移量换算成「相对固定角的宽高」，不允许翻转（拖过对角时贴住最小值）
        base_w = max(1, start.width())
        base_h = max(1, start.height())
        if "l" in mode:
            want_w = anchor.x() - target.x() + 1
        else:
            want_w = target.x() - anchor.x() + 1
        if "t" in mode:
            want_h = anchor.y() - target.y() + 1
        else:
            want_h = target.y() - anchor.y() + 1
        want_w = max(min_w, want_w)
        want_h = max(min_h, want_h)

        scales = (want_w / base_w, want_h / base_h)
        # 以「相对原始尺寸变化更大」的轴定比例，非正方形选区拖动才跟手
        if abs(scales[0] - 1) >= abs(scales[1] - 1):
            scale = scales[0]
        else:
            scale = scales[1]
        # 等比下限：保证两个方向都满足最小尺寸
        scale = max(scale, min_w / base_w, min_h / base_h)
        if bounds is not None:
            avail_w = (anchor.x() - bounds.left() + 1
                       if "l" in mode else
                       bounds.right() - anchor.x() + 1)
            avail_h = (anchor.y() - bounds.top() + 1
                       if "t" in mode else
                       bounds.bottom() - anchor.y() + 1)
            # 边界是硬约束，收缩后不再抬回，否则会越界并移动固定角
            scale = min(scale, avail_w / base_w, avail_h / base_h)
        # 贴边时避免缩成 0 尺寸；min_w/min_h 已由上面的等比下限保证
        scale = max(scale, 1 / base_w, 1 / base_h)
        width = max(1, int(round(base_w * scale)))
        height = max(1, int(round(base_h * scale)))
        x = anchor.x() - width + 1 if "l" in mode else anchor.x()
        y = anchor.y() - height + 1 if "t" in mode else anchor.y()
        return clamp_rect(QRect(x, y, width, height), bounds)

    left, top = start.left(), start.top()
    right, bottom = start.right(), start.bottom()
    if "l" in mode:
        left += delta.x()
    if "r" in mode:
        right += delta.x()
    if "t" in mode:
        top += delta.y()
    if "b" in mode:
        bottom += delta.y()
    if right - left + 1 < min_w:
        if "l" in mode:
            left = right - min_w + 1
        else:
            right = left + min_w - 1
    if bottom - top + 1 < min_h:
        if "t" in mode:
            top = bottom - min_h + 1
        else:
            bottom = top + min_h - 1
    rect = QRect(QPoint(left, top), QPoint(right, bottom)).normalized()
    return clamp_rect(rect, bounds)
