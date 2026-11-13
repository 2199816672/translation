#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面文案 / 颜色语义常量（零依赖）。

以前成功绿、警告黄、错误红散落成十几处字面量，新加的提示很容易和既有
语义对不上。新代码统一从这里取，旧代码保持不动以免大范围回归。
"""
COLOR_OK = "#4ade80"
COLOR_WARN = "#eab308"
COLOR_ERR = "#e5635f"
COLOR_MUTED = "#71717a"
COLOR_INFO = "#3b82f6"

LEVEL_COLORS = {
    'success': COLOR_OK,
    'info': COLOR_INFO,
    'warning': COLOR_WARN,
    'error': COLOR_ERR,
    'muted': COLOR_MUTED,
}

# 提示条停留时长：错误必须留足阅读 + 点动作按钮的时间，成功可以短一些
DURATION_ERROR = 10000
DURATION_WARNING = 6000
DURATION_INFO = 4000
DURATION_SUCCESS = 3000


def prevent_combo_elide(root, min_popup=180):
    """统一修掉 QComboBox 下拉项被省略号截断的问题。

    Qt 的 popup 默认跟随 combo 自身宽度，控件一窄，「电脑声音」就显示
    成「电脑…声音」。让 popup 按内容自适应并给足下限，一次性覆盖整棵控件树
    （主窗口调用一次即可覆盖四个页面），返回处理过的下拉框数量。
    """
    try:
        from PySide6.QtWidgets import QComboBox
        combos = root.findChildren(QComboBox)
    except Exception:
        return 0
    count = 0
    for combo in combos:
        try:
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToContents)
            view = combo.view()
            if view is not None:
                view.setMinimumWidth(max(min_popup,
                                         combo.minimumWidth() or 0))
            count += 1
        except Exception:
            pass
    return count


def align_lang_row(*widgets, height=34):
    """让「从:」「翻译到:」这类标签与后面的下拉框严格同高、顶部对齐。

    QComboBox 受 QSS padding + 样式插件影响，实际高度会比 QLabel 高十几像素
    （实测 46 vs 32）；FlowLayout 虽然会做垂直居中，视觉上仍是「一个小标签
    配一个偏高的框」，文字基线看着不齐。QSS 的 max-height 对 QComboBox 不
    生效，所以在代码层显式统一高度。只作用于调用方传入的这一行控件，
    不会把设置页里其它下拉框也压扁。
    """
    try:
        for widget in widgets:
            if widget is not None:
                widget.setFixedHeight(height)
        return True
    except Exception:
        return False
