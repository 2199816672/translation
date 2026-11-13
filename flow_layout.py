#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自适应工具栏布局：宽度不够自动换行，控件文字始终完整。

项目里很多卡片用单行 ``QHBoxLayout`` 排按钮。窗口变窄（尤其是 Fluent 导航
展开占掉约 296px）后，QHBoxLayout 会把所有控件一起压扁，中文按钮被裁成
「域截 / 屏截 / 时翻」这种半截文字，还全挤在一起。

FlowLayout 的做法是两阶段排版：

1. 先用「最小可读宽度」贪心分行：放得下就留在这一行，放不下整行换行；
2. 再把这一行的剩余宽度分回各控件，最多补到完整 sizeHint，还有富余就给
   ``addStretch``。所以宽屏外观与原来几乎一致（仍是一行），只有真的挤到
   会裁字时才变成两行 / 三行。

「最小可读宽度」等于控件完整尺寸 —— 按钮 / 标签 / 下拉框一个像素都不压
（实测 QSS 下窄 3~4px 中文就被裁掉半个字），只有可横向滚动的 QLineEdit
允许轻微压缩。QComboBox 的 sizeHint 由最长的语言名撑大，改用「当前文本
宽度 + 箭头余量」计算，因此不会被虚高尺寸挤走一整行。

额外兼容：``addWidget(w, stretch)`` / ``addSpacing`` / ``addStretch`` /
``setContentsMargins`` / ``setSpacing`` / ``setAlignment``；以冒号结尾的
QLabel 会与紧随其后的控件自动粘成不可拆单位，换行时不会出现「从:」留在
行尾、「自动检测」跑到下一行；``hasHeightForWidth`` 正确实现，卡片高度会
随换行行数自动增长，不会裁掉第二行。

用法：把 ``QHBoxLayout(card)`` 换成 ``FlowLayout(card)`` 即可，其余
addWidget 调用一行都不用改。
"""
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import (
    QComboBox, QLabel, QLayout, QLineEdit, QSizePolicy, QSpacerItem,
)

# 输入框内部可横向滚动，轻微压缩不会丢内容；其他控件一律不压：
# QPushButton / QLabel 在 QSS 下哪怕只窄 3~4px，中文就会被裁掉半个字。
_INPUT_SHRINK = 8
# QComboBox 的文字区要扣掉左右 padding、::drop-down 箭头位与边框
# （theme.py 现在是 10 + 8 + 20 + 2 = 40px），再留余量给高 DPI 缩放
# 和更长的候选项。它的 sizeHint 由「最长的候选项」决定，往往远大于
# 当前显示文本；用当前文本算宽度才既能显示完整、又不白白占掉一整行。
# 余量刻意留得保守：为省几十像素把「自动检测」又裁掉半个字不划算。
_COMBO_EXTRA = 68


class _Entry:
    """布局里的一项（控件 / 间隔 / 弹簧）。"""

    __slots__ = ('item', 'stretch', 'spacer')

    def __init__(self, item, stretch=0, spacer=False):
        self.item = item
        self.stretch = stretch
        self.spacer = spacer


def _item_hi(item):
    """单个控件的理想宽度：完整显示、不裁字。"""
    widget = item.widget() if hasattr(item, 'widget') else None
    hi = max(item.sizeHint().width(), item.minimumSize().width())
    if isinstance(widget, QComboBox):
        text = widget.fontMetrics().horizontalAdvance(widget.currentText())
        return max(item.minimumSize().width(),
                   min(hi, text + _COMBO_EXTRA))
    return hi


def _item_lo(item):
    """单个控件的最小允许宽度。

    按钮、标签、下拉框都不压缩 —— 实测 QSS 下压 3~4px 就会把中文裁成
    「自动检」「是取文字」。只有 QLineEdit 内部可滚动，允许轻微压缩。
    """
    widget = item.widget() if hasattr(item, 'widget') else None
    if isinstance(widget, QLineEdit):
        return max(1, _item_hi(item) - _INPUT_SHRINK)
    return _item_hi(item)


class _Unit:
    """一个不可拆的排版单位：单个控件，或「标签 + 紧随其后的控件」。"""

    def __init__(self, items, gap=0, stretch=0, spacer=False):
        self.items = list(items)
        self.gap = gap
        self.stretch = stretch
        self.spacer = spacer

    def width(self):
        return (sum(_item_hi(i) for i in self.items)
                + self.gap * (len(self.items) - 1))

    def lo_width(self):
        return min(sum(_item_lo(i) for i in self.items)
                   + self.gap * (len(self.items) - 1),
                   self.width())

    def height(self):
        return max((i.sizeHint().height() for i in self.items), default=0)

    def widths_for(self, width):
        """把分配给本单位的宽度拆给内部每个控件。"""
        gaps = self.gap * (len(self.items) - 1)
        avail = max(0, width - gaps)
        lo = [_item_lo(i) for i in self.items]
        hi = [_item_hi(i) for i in self.items]
        if avail >= sum(hi):
            out = list(hi)
            out[0] += avail - sum(hi)      # 弹簧多出来的宽度给第一个
            return out
        if avail <= sum(lo):
            # 理论上分行阶段已保证放得下；极窄窗口兜底按比例压缩
            total = sum(lo) or 1
            return [max(1, int(v * avail / total)) for v in lo]
        deficit = [h - l for h, l in zip(hi, lo)]
        total_deficit = sum(deficit)
        extra = avail - sum(lo)
        return [l + (extra * d // total_deficit if total_deficit else 0)
                for l, d in zip(lo, deficit)]

    def place(self, x, y, width, line_height, apply=True, align=None):
        if not apply:
            return
        flags = int(align or 0)
        cx = x
        for item, w in zip(self.items, self.widths_for(width)):
            if self.spacer:
                item.setGeometry(QRect(cx, y, w, 0))
            else:
                h = item.sizeHint().height()
                if flags & int(Qt.AlignmentFlag.AlignBottom):
                    iy = y + max(0, line_height - h)
                elif flags & int(Qt.AlignmentFlag.AlignTop):
                    iy = y
                else:  # 默认 / AlignVCenter：行内垂直居中
                    iy = y + max(0, (line_height - h) // 2)
                item.setGeometry(QRect(cx, iy, w, h))
            cx += w + self.gap


def _is_field_label(widget):
    """「从:」「翻译到:」「字幕格式:」这类标签，必须跟后面的控件同进同退。"""
    return (isinstance(widget, QLabel)
            and widget.text().strip().endswith((':', '：')))


class FlowLayout(QLayout):
    """窄窗口自动换行、永不裁字的工具栏布局。"""

    def __init__(self, parent=None, spacing=None, vspacing=None):
        super().__init__(parent)
        self._entries = []
        self._vspacing = -1
        self._align = None
        if spacing is not None:
            self.setSpacing(spacing)
        if vspacing is not None:
            self.setVerticalSpacing(vspacing)

    # ── QLayout 必须实现的接口 ──────────────────────────────

    def addItem(self, item):
        self._entries.append(_Entry(item))
        self.invalidate()

    def count(self):
        return len(self._entries)

    def itemAt(self, index):
        if 0 <= index < len(self._entries):
            return self._entries[index].item
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._entries):
            entry = self._entries.pop(index)
            self.invalidate()
            return entry.item
        return None

    def expandingDirections(self):
        # 宽度由父布局给，高度靠 heightForWidth 算；不主动向父布局要空间
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do(QRect(0, 0, max(1, width), 0), False)

    def minimumSize(self):
        m = self.contentsMargins()
        size = QSize(0, 0)
        for entry in self._entries:
            size = size.expandedTo(entry.item.minimumSize())
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def sizeHint(self):
        # 与 Qt 官方 FlowLayout 示例一致；实际高度由 heightForWidth 决定
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, True)

    # ── 兼容 QHBoxLayout 的常用方法 ─────────────────────────

    def addWidget(self, widget, stretch=0, alignment=None):
        super().addWidget(widget)
        if self._entries:
            self._entries[-1].stretch = int(stretch or 0)
        if alignment is not None:
            self.setAlignment(widget, alignment)
        self.invalidate()

    def addLayout(self, layout, stretch=0):
        self.addItem(layout)
        self._entries[-1].stretch = int(stretch or 0)

    def addSpacing(self, size):
        self.addItem(QSpacerItem(int(size), 0,
                                 QSizePolicy.Policy.Fixed,
                                 QSizePolicy.Policy.Minimum))

    def addStretch(self, stretch=1):
        entry = _Entry(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding,
                        QSizePolicy.Policy.Minimum),
            stretch=int(stretch or 1), spacer=True)
        self._entries.append(entry)
        self.invalidate()

    def setAlignment(self, *args):
        # QBoxLayout 允许 setAlignment(Qt.Alignment)，QLayout 允许
        # setAlignment(widget, Qt.Alignment)；两种调用都要能接住。
        if len(args) == 1:
            self._align = args[0]
            self.invalidate()
            return None
        try:
            return super().setAlignment(*args)
        except (TypeError, AttributeError):
            return None

    def verticalSpacing(self):
        v = self._vspacing
        return v if v >= 0 else self._spacing()

    def setVerticalSpacing(self, value):
        self._vspacing = int(value)
        self.invalidate()

    # ── 排版 ───────────────────────────────────────────────

    def _spacing(self):
        s = self.spacing()
        return s if s >= 0 else 8

    def _hspacing(self):
        return self._spacing()

    def _vspacing_value(self):
        return self.verticalSpacing()

    def _units(self, hspace):
        """把标签与紧随其后的控件粘成不可拆单位。"""
        units = []
        i = 0
        entries = self._entries
        while i < len(entries):
            cur = entries[i]
            widget = cur.item.widget() if hasattr(cur.item, 'widget') else None
            if (not cur.spacer and not cur.stretch
                    and _is_field_label(widget) and i + 1 < len(entries)):
                nxt = entries[i + 1]
                nxt_widget = (nxt.item.widget()
                              if hasattr(nxt.item, 'widget') else None)
                if not nxt.spacer and not nxt.stretch and nxt_widget is not None:
                    units.append(_Unit([cur.item, nxt.item],
                                       gap=min(6, hspace)))
                    i += 2
                    continue
            # addSpacing 走的是 addItem，也要识别成间隔项：它是「分组断点」
            spacer = cur.spacer or isinstance(cur.item, QSpacerItem)
            units.append(_Unit([cur.item], stretch=cur.stretch,
                               spacer=spacer))
            i += 1
        return units

    def _do(self, rect, apply):
        m = self.contentsMargins()
        hs = self._hspacing()
        vs = self._vspacing_value()
        left = rect.x() + m.left()
        right = rect.x() + rect.width() - m.right()
        top = rect.y() + m.top()
        usable = max(1, right - left)
        units = self._units(hs)

        # 阶段 1：按最小可读宽度贪心分行
        rows = []                  # [(units, y, line_height), ...]
        cur = []
        y, line_h = top, 0

        def used_width(items):
            return (sum(u.lo_width() for u in items)
                    + hs * len(items))

        for idx, unit in enumerate(units):
            # 换行后行首不要再留一段空隙
            if unit.spacer and not unit.stretch and not cur:
                continue
            # 语义断点：工具栏里 addSpacing(16) 这类「分组间隔」后面若已放
            # 不下剩余控件，就在这里整组换行，而不是把「从: 自动检测」单独
            # 挂在上一行末尾。宽屏时剩余控件放得下，断点不生效。
            if (unit.spacer and not unit.stretch
                    and unit.lo_width() >= 12 and cur
                    and used_width(cur) + used_width(units[idx + 1:]) > usable):
                rows.append((cur, y, line_h))
                cur = []
                y += line_h + vs
                line_h = 0
                continue
            lo = unit.lo_width()
            if cur and used_width(cur) + lo > usable:
                rows.append((cur, y, line_h))
                cur = []
                y += line_h + vs
                line_h = 0
            cur.append(unit)
            line_h = max(line_h, unit.height())
        rows.append((cur, y, line_h))
        height = y + line_h + m.bottom() - rect.y()

        if apply:
            # 阶段 2：把每行的剩余宽度分回控件，富余给弹簧
            for row, ly, lh in rows:
                if not row:
                    continue
                los = [u.lo_width() for u in row]
                his = [u.width() for u in row]
                free = max(0, usable - (sum(los) + hs * (len(row) - 1)))
                deficits = [max(0, h - l) for h, l in zip(his, los)]
                total_deficit = sum(deficits)
                give = min(free, total_deficit)
                widths = [l + (give * d // total_deficit
                               if total_deficit else 0)
                          for l, d in zip(los, deficits)]
                leftover = free - give
                stretches = [i for i, u in enumerate(row) if u.stretch]
                if leftover and stretches:
                    share = leftover // len(stretches)
                    for i in stretches:
                        widths[i] += share
                x = left
                for unit, w in zip(row, widths):
                    unit.place(x, ly, w, lh, True, self._align)
                    x += w + hs
        return max(0, height)
