#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自定义控件集合：HotKeyInput、FlatCheckBox。"""
from PySide6.QtCore import Qt, QRect, QRectF, QSize, Signal
from PySide6.QtGui import QKeySequence, QKeyEvent, QPainter, QColor, QPen
from PySide6.QtWidgets import QLineEdit, QCheckBox, QSizePolicy


class FlatCheckBox(QCheckBox):
    """带 √ 的扁平复选框，不依赖 QSS indicator。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # indicator 尺寸和位置
        size = 16
        y = (self.height() - size) // 2
        indicator = QRect(0, y, size, size)

        # 背景 + 边框
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#3b82f6"))
            p.drawRoundedRect(QRectF(indicator), 3.5, 3.5)
            # 画 √
            pen = QPen(QColor("#ffffff"), 2.0, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawLine(indicator.x() + 3, indicator.y() + 8,
                       indicator.x() + 7, indicator.y() + 12)
            p.drawLine(indicator.x() + 7, indicator.y() + 12,
                       indicator.x() + 13, indicator.y() + 4)
        else:
            p.setPen(QPen(QColor("#52525b"), 1))
            p.setBrush(QColor("#161618"))
            p.drawRoundedRect(QRectF(indicator), 3.5, 3.5)

        # 文字
        p.setPen(QColor("#e4e4e7"))
        p.setFont(self.font())
        text_x = indicator.width() + 8
        p.drawText(text_x, 0, self.width() - text_x, self.height(),
                   Qt.AlignmentFlag.AlignVCenter, self.text())
        p.end()

    def sizeHint(self):
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.text()) + 28
        return QSize(w, 28)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QKeyEvent
from PySide6.QtWidgets import QLineEdit

_MODIFIER_MAP = {
    Qt.Key.Key_Control: "ctrl",
    Qt.Key.Key_Shift: "shift",
    Qt.Key.Key_Alt: "alt",
    Qt.Key.Key_Meta: "win",
}

_SPECIAL_NAMES = {
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Escape: "esc",
    Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
}


class HotKeyInput(QLineEdit):
    """点击后捕获按键组合，显示如 Ctrl + Shift + A。"""

    hotkeyChanged = Signal(str)

    def __init__(self, initial="", parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self._keys = []
        self._modifiers = set()
        self._capturing = False
        self.setMinimumWidth(200)
        self.setPlaceholderText("点击录入快捷键…")
        self.setText(self._format(initial))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._capturing = True
            self._modifiers.clear()
            self._keys.clear()
            self.setText("请按下快捷键…")
            self.setStyleSheet(
                "QLineEdit { border: 1px solid #3b82f6; background: #1a1a2e; color: #e4e4e7; "
                "border-radius: 5px; padding: 4px 8px; font-size: 12px; }"
            )
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if not self._capturing:
            return super().keyPressEvent(event)

        key = event.key()
        text = event.text()

        # 记录修饰键
        if key in _MODIFIER_MAP:
            self._modifiers.add(_MODIFIER_MAP[key])
            self.setText(" + ".join(sorted(self._modifiers)) + " + …")
            return

        # 忽略单独按下修饰键
        if key in (0, Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        # 普通按键
        name = _SPECIAL_NAMES.get(key)
        if name is None:
            if text and text.isprintable():
                name = text.upper()
            else:
                name = QKeySequence(key).toString()

        combo = list(sorted(self._modifiers)) + [name.lower()]
        self._capturing = False
        self._keys = combo
        self._modifiers.clear()

        result = "+".join(combo)
        self.setText(self._format(result))
        self.setStyleSheet("")
        self.hotkeyChanged.emit(result)

    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)

    def value(self):
        return "+".join(self._keys) if self._keys else ""

    def setValue(self, combo):
        self._keys = combo.split("+") if combo else []
        self.setText(self._format(combo))

    def _format(self, combo):
        if not combo:
            return ""
        parts = combo.split("+")
        display = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            low = p.lower()
            if low in ("ctrl", "shift", "alt", "win", "meta"):
                display.append(p.capitalize())
            elif len(p) == 1:
                display.append(p.upper())
            else:
                display.append(p.capitalize())
        return " + ".join(display)
