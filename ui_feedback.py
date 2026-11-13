#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一的用户反馈：错误提示条必须带可点动作，且能被复制去求助。

PySide6 相关 import 全部放在函数内部：这样本模块在没有 GUI 依赖的
CI / 零依赖测试里也能 import，纯逻辑（诊断总线、文案）可直接验证。
"""
from __future__ import annotations

import diagnostics
from ui_theme import DURATION_ERROR, DURATION_INFO


def record_error(title, text, detail="", source=""):
    """所有错误统一进诊断总线，用户随时能复制完整报告。"""
    return diagnostics.record(
        'error', f"{title}：{text}" if title else str(text),
        detail, source or title)


def copy_diagnostic_report(extra=None):
    """把诊断信息放进剪贴板；返回文本，方便调用方顺手显示状态。"""
    from PySide6.QtWidgets import QApplication
    text = diagnostics.build_report(extra)
    QApplication.clipboard().setText(text)
    return text


def show_error_bar(parent, title, text, actions=(), detail="",
                   duration=DURATION_ERROR, source=""):
    """错误 InfoBar：至少带「复制诊断」，调用方可追加「重试」等动作。

    ``actions`` 是 ``(按钮文字, 无参回调)`` 序列。按钮先关闭提示条再执行
    回调，避免回调里切换页面后提示条挂在已经隐藏的窗口上。
    """
    record_error(title, text, detail, source)
    from qfluentwidgets import InfoBar, InfoBarPosition
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton

    bar = InfoBar.error(
        title or "操作失败", text, duration=duration,
        position=InfoBarPosition.TOP, parent=parent)

    def add(label, callback):
        btn = QPushButton(label, bar)
        btn.setFixedHeight(28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)

        def run(_checked=False):
            try:
                bar.close()
            except Exception:
                pass
            try:
                callback()
            except Exception as exc:
                record_error(label, str(exc), source=source or title)

        btn.clicked.connect(run)
        bar.addWidget(btn)

    for label, callback in actions:
        add(label, callback)

    def copy_with_feedback():
        copy_diagnostic_report()
        show_info_bar(parent, "已复制",
                      "诊断信息已放进剪贴板，可直接粘贴给排查人员")

    add("复制诊断", copy_with_feedback)
    return bar


def show_info_bar(parent, title, text, duration=DURATION_INFO):
    from qfluentwidgets import InfoBar, InfoBarPosition
    diagnostics.record('info', f"{title}：{text}", source=title)
    return InfoBar.info(title, text, duration=duration,
                        position=InfoBarPosition.TOP, parent=parent)


def set_disabled_hint(widget, reason):
    """禁用态统一补上「为什么不能用 / 怎么才能用」的悬停说明。"""
    try:
        widget.setToolTip("" if widget.isEnabled() else str(reason))
    except Exception:
        pass
