# -*- coding: utf-8 -*-
"""实时字幕浮层：与实时截图翻译同款的可拖拽 / 四角缩放方框。

内容不是截图 OCR，而是麦克风或系统内部声音的实时识别结果。原文、译文
可以单独或双语滚动显示；控制条负责暂停、模式、字号、清空、复制、重选、
点击穿透和关闭。
"""

from PySide6.QtCore import Qt, QPoint, QRect, QRectF, QTimer, Signal, Slot
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication,
                           QPainter, QPen)
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from realtime_translate import (LiveControlBar, _BAR_GAP, _EDGE, _MIN_H,
                                _MIN_W)
from theme import Palette
from text_segment import wrap_by_width

MODES = ('both', 'translation', 'source')
MODE_LABELS = {'both': '双语', 'translation': '译文', 'source': '原文'}


def default_caption_region():
    """默认字幕框：屏幕下方居中、避开任务栏，用户可拖动 / 四角缩放。"""
    screen = QGuiApplication.primaryScreen()
    avail = (screen.availableGeometry() if screen is not None
             else QRect(0, 0, 1920, 1080))
    width = max(360, min(int(avail.width() * 0.72), 1100))
    height = max(_MIN_H, min(190, int(avail.height() * 0.22)))
    x = avail.left() + max(0, (avail.width() - width) // 2)
    y = avail.bottom() - height - max(40, int(avail.height() * 0.11))
    return QRect(x, max(avail.top(), y), width, height)


def resolve_caption_region(settings):
    """读取上次保存的位置；无效 / 跑出屏幕时回退到默认区域。"""
    raw = (settings or {}).get('subtitle_region') or []
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            x, y, w, h = (int(v) for v in raw)
        except (TypeError, ValueError):
            x = y = w = h = 0
        screen = QGuiApplication.primaryScreen()
        avail = (screen.availableGeometry() if screen is not None
                 else QRect(0, 0, 1920, 1080))
        if (w >= _MIN_W and h >= _MIN_H
                and w <= avail.width() * 1.5 and h <= avail.height() * 1.5
                and x >= avail.left() - w and x <= avail.right()
                and y >= avail.top() - h and y <= avail.bottom()):
            return QRect(x, y, w, h)
    return default_caption_region()


class CaptionControlBar(LiveControlBar):
    """在实时翻译控制条基础上加：模式 / 字号 / 清空 / 穿透。"""

    modeClicked = Signal()
    clearClicked = Signal()
    stopClicked = Signal()
    fontDelta = Signal(int)
    clickThroughToggled = Signal(bool)

    def __init__(self, target):
        super().__init__(target)
        # 父类「原文」二态按钮换成三态显示模式
        self.source_btn.hide()
        lay = self.layout()
        # 插在「复制 / 重选」之前：暂停 → 显示控制 → 编辑 → 窗口操作
        index = lay.indexOf(self.copy_btn)

        self.mode_btn = self._btn(MODE_LABELS['both'])
        self.mode_btn.setToolTip('切换字幕显示：双语 → 译文 → 原文')
        self.mode_btn.clicked.connect(self.modeClicked.emit)
        lay.insertWidget(index, self.mode_btn)

        self.font_minus_btn = self._btn('A-')
        self.font_minus_btn.setToolTip('缩小字幕')
        self.font_minus_btn.clicked.connect(lambda: self.fontDelta.emit(-2))
        lay.insertWidget(index + 1, self.font_minus_btn)

        self.font_plus_btn = self._btn('A+')
        self.font_plus_btn.setToolTip('放大字幕')
        self.font_plus_btn.clicked.connect(lambda: self.fontDelta.emit(2))
        lay.insertWidget(index + 2, self.font_plus_btn)

        self.clear_btn = self._btn('清空')
        self.clear_btn.setToolTip('清掉当前字幕（不影响识别）')
        self.clear_btn.clicked.connect(self.clearClicked.emit)
        lay.insertWidget(index + 3, self.clear_btn)

        self.through_btn = self._btn('穿透', checkable=True)
        self.through_btn.setToolTip(
            '开启后字幕框不拦截鼠标，可以照常操作下面的视频/网页；\n'
            '关闭后才能拖动边缘缩放选区')
        self.through_btn.toggled.connect(self.clickThroughToggled.emit)
        lay.insertWidget(index + 4, self.through_btn)

        # 不再让用户框选：「重选」改成复位到默认位置；「×」只隐藏字幕框，
        # 想彻底停止采集识别要点「停止」。
        self.reselect_btn.setText('复位')
        self.reselect_btn.setToolTip('把字幕框恢复到屏幕下方居中的默认位置')
        self.close_btn.setText('隐藏')
        self.close_btn.setToolTip(
            '只隐藏字幕框，识别继续，结果仍会显示在「翻译」页的识别文本里')
        self.stop_btn = self._btn('停止')
        self.stop_btn.setToolTip('彻底停止实时字幕（停止采集与识别）')
        self.stop_btn.clicked.connect(self.stopClicked.emit)
        lay.addWidget(self.stop_btn)
        self.adjustSize()

    def set_mode(self, mode):
        self.mode_btn.setText(MODE_LABELS.get(mode, '双语'))
        self.adjustSize()

    def set_click_through(self, checked):
        self.through_btn.blockSignals(True)
        self.through_btn.setChecked(checked)
        self.through_btn.blockSignals(False)


class CaptionWindow(QWidget):
    """置顶字幕方框。主线程只接收信号后 update()，不做任何耗时工作。"""

    closed = Signal()          # 真正销毁（仅停止实时字幕时）
    hidden = Signal()          # 只隐藏字幕框，识别继续
    stopRequested = Signal()   # 用户点控制条「停止」
    regionChanged = Signal(QRect)
    pausedChanged = Signal(bool)
    modeChanged = Signal(str)
    fontSizeChanged = Signal(int)
    clickThroughChanged = Signal(bool)
    cleared = Signal()

    def __init__(self, region, settings, parent=None):
        super().__init__(parent)
        cfg = dict(settings or {})
        self._region = QRect(region)
        self._mode = str(cfg.get('subtitle_display_mode') or 'both')
        if self._mode not in MODES:
            self._mode = 'both'
        self._font_size = max(12, min(48,
                                      int(cfg.get('subtitle_font_size') or 22)))
        self._max_lines = max(1, min(8,
                                     int(cfg.get('subtitle_max_lines') or 3)))
        self._bg_alpha = max(0, min(255,
                                    int(cfg.get('subtitle_bg_alpha') or 190)))
        self._click_through = bool(cfg.get('subtitle_click_through', False))

        self._lines = []            # [{seq, source, trans}]
        self._paused = False
        self._closed = False
        self._state_text = '正在启动…'
        self._state_color = Palette.PAUSED
        self._speed = 0.0
        self._resize_edge = ''
        self._resize_start_geo = QRect()
        self._resize_start_pos = QPoint()
        self._region_dirty = False
        self._moving = False
        self._move_start_geo = QRect()
        self._move_start_pos = QPoint()

        self.setWindowTitle('实时字幕')
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMouseTracking(True)
        self.setGeometry(self._region)

        self._bar = CaptionControlBar(self)
        self._bar.pauseClicked.connect(self._toggle_pause)
        self._bar.copyClicked.connect(self._copy_text)
        self._bar.reselectClicked.connect(self.reset_region)
        self._bar.closeClicked.connect(self.hide_overlay)
        self._bar.stopClicked.connect(self.stopRequested.emit)
        self._bar.modeClicked.connect(self._cycle_mode)
        self._bar.clearClicked.connect(self.clear_lines)
        self._bar.fontDelta.connect(self._change_font_size)
        self._bar.clickThroughToggled.connect(self._set_click_through)
        self._bar.set_mode(self._mode)
        self._bar.set_click_through(self._click_through)
        self._bar.set_status(self._state_text, self._state_color)
        self._bar.follow(self._region)
        self._apply_click_through()

    # ── 生命周期 ────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if not self._bar.isVisible():
            self._bar.show()
        self._bar.raise_()

    def closeEvent(self, event):
        self._closed = True
        if self._bar is not None:
            self._bar.close()
        super().closeEvent(event)
        self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide_overlay()
            return
        super().keyPressEvent(event)

    # ── 显示 / 隐藏：隐藏不等于停止识别 ─────────────────────

    def show_overlay(self):
        self.show()
        if self._bar is not None:
            self._bar.show()
            self._bar.raise_()

    def hide_overlay(self):
        """只隐藏方框，引擎继续跑，结果由主窗口写进主页文本框。"""
        if self._bar is not None:
            self._bar.hide()
        self.hide()
        self.hidden.emit()

    def region(self):
        return QRect(self._region)

    def snapshot(self):
        """供主窗口在隐藏期间记录状态（重新显示时无需重建窗口）。"""
        return {
            'lines': [dict(l) for l in self._lines],
            'mode': self._mode,
            'paused': self._paused,
            'font_size': self._font_size,
        }

    def is_paused(self):
        return bool(self._paused)

    def set_paused(self, paused):
        """托盘 / 快捷键也能暂停，状态与控制条保持同步。"""
        paused = bool(paused)
        if paused == self._paused:
            return
        self._paused = paused
        self._bar.set_paused(paused)
        self.pausedChanged.emit(paused)
        self._refresh_status()
        self.update()

    def reset_region(self):
        """「复位」：回到屏幕下方居中的默认位置（不再要求用户框选）。"""
        self._apply_region(default_caption_region())

    # ── 选区移动 / 缩放（与实时截图浮层同一套几何） ──────────

    def move_by(self, delta):
        if delta.isNull():
            return
        self._apply_region(self._region.translated(delta))

    def _apply_region(self, rect):
        rect = self._clamp_region(QRect(rect))
        if rect == self._region:
            return
        self._region = rect
        self._region_dirty = True
        self.setGeometry(rect)
        self._bar.follow(self._region)
        self._bar.raise_()
        self.update()

    def _clamp_region(self, rect):
        screen = (QGuiApplication.screenAt(rect.center())
                  or QGuiApplication.primaryScreen())
        if screen is None:
            return rect
        avail = screen.availableGeometry()
        width = max(_MIN_W, min(rect.width(), avail.width()))
        height = max(_MIN_H, min(rect.height(), avail.height()))
        x = max(avail.left(), min(rect.x(), avail.right() - width + 1))
        y = max(avail.top(), min(rect.y(), avail.bottom() - height + 1))
        return QRect(x, y, width, height)

    @staticmethod
    def _edge_at(pos, rect):
        left = pos.x() <= _EDGE
        right = pos.x() >= rect.width() - 1 - _EDGE
        top = pos.y() <= _EDGE
        bottom = pos.y() >= rect.height() - 1 - _EDGE
        edge = 't' if top else ('b' if bottom else '')
        if left:
            edge += 'l'
        elif right:
            edge += 'r'
        return edge

    @staticmethod
    def _cursor_for_edge(edge):
        if edge in ('tl', 'br'):
            return Qt.CursorShape.SizeFDiagCursor
        if edge in ('tr', 'bl'):
            return Qt.CursorShape.SizeBDiagCursor
        if edge in ('l', 'r'):
            return Qt.CursorShape.SizeHorCursor
        if edge in ('t', 'b'):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _handle_hit(self, pos):
        handle = QPoint(self.rect().center().x(), 0)
        return (QPoint(pos) - handle).manhattanLength() <= 14

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._click_through:
            return
        pos = event.position().toPoint()
        if self._handle_hit(pos):
            self._moving = True
            self._move_start_geo = QRect(self.geometry())
            self._move_start_pos = event.globalPosition().toPoint()
            event.accept()
            return
        edge = self._edge_at(pos, self.rect())
        if not edge:
            return
        self._resize_edge = edge
        self._resize_start_geo = QRect(self.geometry())
        self._resize_start_pos = event.globalPosition().toPoint()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._click_through:
            return
        if self._moving:
            delta = event.globalPosition().toPoint() - self._move_start_pos
            self._apply_region(self._move_start_geo.translated(delta))
            return
        if self._resize_edge:
            from resize_util import resize_rect
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            screen = (QGuiApplication.screenAt(self._resize_start_geo.center())
                      or QGuiApplication.primaryScreen())
            bounds = screen.availableGeometry() if screen else None
            self._apply_region(resize_rect(
                self._resize_start_geo, self._resize_edge, delta,
                min_w=_MIN_W, min_h=_MIN_H, bounds=bounds))
            return
        pos = event.position().toPoint()
        if self._handle_hit(pos):
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        else:
            self.setCursor(QCursor(
                self._cursor_for_edge(self._edge_at(pos, self.rect()))))

    def mouseReleaseEvent(self, event):
        self._resize_edge = ''
        self._moving = False
        if self._region_dirty:
            self._region_dirty = False
            self.regionChanged.emit(QRect(self._region))
        event.accept()

    # ── 数据（主线程槽） ────────────────────────────────────

    @Slot(int, str)
    def append_source(self, seq, text):
        text = str(text or '').strip()
        if not text:
            return
        self._lines.append({'seq': int(seq), 'source': text, 'trans': ''})
        if len(self._lines) > 30:
            self._lines = self._lines[-30:]
        self._refresh_status()
        self.update()

    @Slot(int, str)
    def set_translation(self, seq, text):
        for line in reversed(self._lines):
            if line['seq'] == int(seq):
                line['trans'] = str(text or '').strip()
                break
        self.update()

    def set_status(self, text, color=None):
        self._state_text = str(text or '')
        if color:
            self._state_color = color
        self._refresh_status()
        self.update()

    def set_speed(self, seconds):
        self._speed = max(0.0, float(seconds or 0))
        self._refresh_status()

    def _refresh_status(self):
        if self._paused:
            label = '已暂停'
        elif self._state_text:
            label = self._state_text
        else:
            label = '正在聆听…'
        if self._speed > 0:
            label += f' · {self._speed:.2f}s/段'
        self._bar.set_status(label, self._state_color)

    def clear_lines(self):
        self._lines = []
        self.cleared.emit()
        self.update()

    def display_text(self):
        out = []
        for line in self._lines[-self._max_lines:]:
            src, trans = line['source'], line['trans']
            if self._mode == 'source':
                out.append(src)
            elif self._mode == 'translation':
                out.append(trans or src)
            else:
                if trans:
                    out.append(trans)
                    out.append(src)
                else:
                    out.append(src)
        return '\n'.join(out)

    # ── 控制条动作 ──────────────────────────────────────────

    def _toggle_pause(self):
        self._paused = not self._paused
        self._bar.set_paused(self._paused)
        self._bar.set_status('已暂停' if self._paused else '正在聆听…',
                             Palette.PAUSED if self._paused
                             else Palette.RUNNING)
        self.pausedChanged.emit(self._paused)
        self.update()

    def _cycle_mode(self):
        self._mode = MODES[(MODES.index(self._mode) + 1) % len(MODES)]
        self._bar.set_mode(self._mode)
        self.modeChanged.emit(self._mode)
        self.update()

    def set_mode(self, mode):
        if mode in MODES:
            self._mode = mode
            self._bar.set_mode(mode)
            self.update()

    def apply_appearance(self, cfg):
        """设置页保存后即时同步字号 / 行数 / 底色，无需停止重开实时字幕。

        这些值原本只在构造时读一次，用户改完设置看到「保存成功」却毫无变化，
        会反复保存甚至以为设置坏了。
        """
        c = dict(cfg or {})

        def _int(key, default):
            try:
                return int(c.get(key, default))
            except (TypeError, ValueError):
                return default

        self._font_size = max(12, min(48, _int('subtitle_font_size', 22)))
        self._max_lines = max(1, min(8, _int('subtitle_max_lines', 3)))
        self._bg_alpha = max(0, min(255, _int('subtitle_bg_alpha', 190)))
        self.update()

    def _change_font_size(self, delta):
        self._font_size = max(12, min(48, self._font_size + int(delta)))
        self.fontSizeChanged.emit(self._font_size)
        self.update()

    def _set_click_through(self, checked):
        self._click_through = bool(checked)
        self._apply_click_through()
        self.clickThroughChanged.emit(self._click_through)
        self.update()

    def _apply_click_through(self):
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            self._click_through)

    def _copy_text(self):
        text = self.display_text()
        if not text:
            return
        QApplication.clipboard().setText(text)
        # 开了「点击穿透」后更容易误点，没有任何反馈时无法确认是否复制成功
        self._flash_state('已复制字幕文字')

    def _flash_state(self, text):
        """在状态位短显一条操作结果，不打断后续真正的状态更新。"""
        prev = self._state_text
        self._state_text = text
        self.update()

        def restore():
            # 期间若有新的状态文字进来，就不要把它覆盖回旧值
            if not self._closed and self._state_text == text:
                self._state_text = prev
                self.update()
        QTimer.singleShot(1500, restore)

    # ── 绘制 ────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(8, 8, 12, self._bg_alpha))
        painter.setPen(QPen(QColor(96, 165, 250, 110), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        self._draw_corner_brackets(painter)

        inner = QRectF(self.rect().adjusted(16, 12, -16, -16))
        if self._lines:
            self._draw_lines(painter, inner)
        else:
            font = QFont('Microsoft YaHei')
            font.setPixelSize(14)
            painter.setFont(font)
            painter.setPen(QColor(Palette.TEXT_MUTED))
            painter.drawText(inner, Qt.AlignmentFlag.AlignCenter,
                             self._state_text or '等待声音…')
        if self._paused:
            self._draw_badge(painter, '已暂停', Palette.PAUSED)
        elif self._click_through:
            self._draw_badge(painter, '点击穿透', Palette.ACCENT)
        painter.end()

    def _line_parts(self, line):
        src, trans = line['source'], line['trans']
        small = max(12, int(self._font_size * 0.62))
        if self._mode == 'source':
            return [(src, self._font_size, QColor(250, 250, 250))]
        if self._mode == 'translation':
            # 译文还没回来时先显示原文（稍暗），避免空白等待
            return [(trans or src, self._font_size,
                     QColor(250, 250, 250) if trans
                     else QColor(Palette.TEXT_MUTED))]
        parts = [(trans or src, self._font_size,
                  QColor(250, 250, 250) if trans
                  else QColor(Palette.TEXT_MUTED))]
        if trans and src:
            parts.append((src, small, QColor(Palette.TEXT_MUTED)))
        return parts

    def _draw_lines(self, painter, inner):
        gap = 8
        prepared = []
        for line in reversed(self._lines[-self._max_lines:]):
            blocks = []
            height = 0
            for text, size, color in self._line_parts(line):
                font = QFont('Microsoft YaHei')
                font.setPixelSize(size)
                fm = QFontMetrics(font)
                wrapped = wrap_by_width(
                    text, inner.width(), fm.horizontalAdvance) or ['']
                wrapped = wrapped[:2]
                if len(wrapped) == 2:
                    while (wrapped[-1]
                           and fm.horizontalAdvance(wrapped[-1] + '…')
                           > inner.width()):
                        wrapped[-1] = wrapped[-1][:-1]
                    wrapped[-1] += '…'
                blocks.append((wrapped, font, fm, color))
                height += len(wrapped) * fm.lineSpacing()
            prepared.append((blocks, height))
            if sum(h + gap for _, h in prepared) > inner.height():
                prepared.pop()
                break
        prepared.reverse()

        y = float(inner.bottom() - sum(h + gap for _, h in prepared) + gap)
        for blocks, _height in prepared:
            for wrapped, font, fm, color in blocks:
                painter.setFont(font)
                line_h = fm.lineSpacing()
                for text in wrapped:
                    rect = QRectF(inner.left(), y, inner.width(), line_h)
                    painter.setPen(QColor(0, 0, 0, 190))
                    painter.drawText(rect.translated(1, 1),
                                     Qt.AlignmentFlag.AlignLeft, text)
                    painter.setPen(color)
                    painter.drawText(rect, Qt.AlignmentFlag.AlignLeft, text)
                    y += line_h
            y += gap

    def _draw_corner_brackets(self, painter):
        length, margin = 16, 5
        r = self.rect()
        painter.setPen(QPen(QColor(96, 165, 250, 230), 2))
        pairs = (
            ((r.left() + margin, r.top() + margin),
             (r.left() + margin + length, r.top() + margin)),
            ((r.left() + margin, r.top() + margin),
             (r.left() + margin, r.top() + margin + length)),
            ((r.right() - margin - length, r.top() + margin),
             (r.right() - margin, r.top() + margin)),
            ((r.right() - margin, r.top() + margin),
             (r.right() - margin, r.top() + margin + length)),
            ((r.left() + margin, r.bottom() - margin),
             (r.left() + margin + length, r.bottom() - margin)),
            ((r.left() + margin, r.bottom() - margin - length),
             (r.left() + margin, r.bottom() - margin)),
            ((r.right() - margin - length, r.bottom() - margin),
             (r.right() - margin, r.bottom() - margin)),
            ((r.right() - margin, r.bottom() - margin - length),
             (r.right() - margin, r.bottom() - margin)),
        )
        for a, b in pairs:
            painter.drawLine(QPoint(*a), QPoint(*b))
        center = QPoint(r.center().x(), r.top())
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(QColor(96, 165, 250))
        painter.drawEllipse(center, 7, 7)
        painter.setPen(QPen(QColor(96, 165, 250), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, 10, 10)

    def _draw_badge(self, painter, text, color):
        font = QFont('Microsoft YaHei')
        font.setPixelSize(11)
        painter.setFont(font)
        fm = QFontMetrics(font)
        width = fm.horizontalAdvance(text) + 16
        height = fm.height() + 6
        rect = QRectF(self.width() - width - 8, 8, width, height)
        painter.setPen(QPen(QColor(color), 1))
        painter.setBrush(QColor(20, 20, 24, 220))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor(color))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
