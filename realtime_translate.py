#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时翻译：译文直接叠加在框选区域上（像截图翻译一样），选区可拖动/缩放。

流程：
- 每 N 毫秒抓取一次选区 → OCR → 翻译；
- 抓图瞬间把浮层设为全透明、抓完立即恢复，保证抓到的始终是原始画面；
- 画面指纹去重、文本去重，避免无效请求；
- 抓图、OCR 都在后台线程，UI 不卡顿；
- 不做 AI 润色，保证刷新速度。

交互：
- 拖动边缘/四角缩放选区；
- 拖动控制条移动选区；
- 控制条显示刷新速度与上次耗时，提供暂停/继续、原文对照、复制、重选、关闭。
"""
import hashlib
import os
import threading
import time
import uuid
from datetime import datetime

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QWidget,
)

from config import SCREENSHOT_DIR
from text_segment import (
    blocks_to_text, flatten_text, group_blocks, normalize_groups,
    wrap_by_width, wrap_sentences,
)
from theme import Palette

_MAX_CHARS = 1500
_DEFAULT_INTERVAL_MS = 1200
_EDGE = 10          # 边缘缩放热区（像素）
_MIN_W = 90
_MIN_H = 60
_BAR_GAP = 8


class LiveControlBar(QWidget):
    """实时翻译控制条：速度显示 + 暂停 / 原文 / 复制 / 重选 / 关闭。"""

    pauseClicked = Signal()
    copyClicked = Signal()
    reselectClicked = Signal()
    closeClicked = Signal()
    sourceToggled = Signal(bool)

    def __init__(self, target):
        super().__init__(None)
        self._target = target
        self._dragging = False
        self._start = QPoint()

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        self.setToolTip("拖动这里移动选区")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(4)

        self.status_dot = QLabel("●", self)
        self.status_dot.setStyleSheet(
            f"color: {Palette.RUNNING}; font-size: 10px; background: transparent;")
        lay.addWidget(self.status_dot)

        self.status_label = QLabel("", self)
        self.status_label.setStyleSheet(
            f"color: {Palette.TEXT_MUTED}; font-size: 11px; background: transparent;")
        lay.addWidget(self.status_label)
        lay.addSpacing(2)

        self.pause_btn = self._btn("暂停")
        self.pause_btn.clicked.connect(self.pauseClicked.emit)
        lay.addWidget(self.pause_btn)

        self.source_btn = self._btn("原文", checkable=True)
        self.source_btn.toggled.connect(self.sourceToggled.emit)
        lay.addWidget(self.source_btn)

        self.copy_btn = self._btn("复制")
        self.copy_btn.clicked.connect(self.copyClicked.emit)
        lay.addWidget(self.copy_btn)

        self.reselect_btn = self._btn("重选")
        self.reselect_btn.clicked.connect(self.reselectClicked.emit)
        lay.addWidget(self.reselect_btn)

        self.close_btn = self._btn("×")
        self.close_btn.clicked.connect(self.closeClicked.emit)
        lay.addWidget(self.close_btn)

        self.adjustSize()

    def _btn(self, text, checkable=False):
        btn = QPushButton(text, self)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setCheckable(checkable)
        btn.setFixedHeight(24)
        btn.setStyleSheet(
            "QPushButton { background: transparent;"
            " border: 1px solid transparent; border-radius: 5px;"
            " color: #a1a1aa; padding: 0 8px; font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,255,255,22);"
            " color: #fafafa; }"
            "QPushButton:checked { background: rgba(59,130,246,200);"
            " color: #ffffff; }")
        return btn

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(Palette.BORDER_STRONG), 1))
        painter.setBrush(QColor(20, 20, 24, 242))
        painter.drawRoundedRect(rect, 10, 10)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            pos = event.globalPosition().toPoint()
            delta = pos - self._start
            self._start = pos
            self._target.move_by(delta)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False
        event.accept()

    # ── 状态 ────────────────────────────────────────────────

    def set_status(self, text, color=None):
        self.status_label.setText(text)
        if color:
            self.status_dot.setStyleSheet(
                f"color: {color}; font-size: 10px; background: transparent;")
        self.adjustSize()

    def set_paused(self, paused):
        self.pause_btn.setText("继续" if paused else "暂停")

    def set_source(self, checked):
        self.source_btn.blockSignals(True)
        self.source_btn.setChecked(checked)
        self.source_btn.blockSignals(False)

    def follow(self, region):
        """把控制条放在选区下方，放不下则上方，再不行就放到选区内部。"""
        self.adjustSize()
        screen = (QGuiApplication.screenAt(region.center())
                  or QGuiApplication.primaryScreen())
        avail = (screen.availableGeometry() if screen
                 else QRect(0, 0, 1920, 1080))
        w, h = self.width(), self.height()
        x = region.center().x() - w // 2
        y = region.bottom() + _BAR_GAP
        if y + h > avail.bottom() - 4:
            y = region.top() - h - _BAR_GAP
        if y < avail.top() + 4:
            # 上下都没有空间：贴选区内部顶部，抓图时会临时隐藏
            y = region.top() + 6
        x = max(avail.left() + 4, min(x, avail.right() - w - 4))
        y = max(avail.top() + 4, min(y, avail.bottom() - h - 4))
        self.move(x, y)


class LiveTranslateWindow(QWidget):
    """置顶实时翻译浮层：译文直接画在选区上。"""

    closed = Signal()
    reselectRequested = Signal()
    _sig_done = Signal(str, str, object)   # (原文, 译文, 带位置的句子块)
    _sig_error = Signal(str)

    def __init__(self, region, ocr, translator, interval_ms=None,
                 show_source=False, parent=None):
        super().__init__(parent)
        self._region = QRect(region)
        self._ocr = ocr
        self._translator = translator
        self._interval = max(600, int(interval_ms or _DEFAULT_INTERVAL_MS))
        self._show_source = bool(show_source)

        self._busy = False
        self._paused = False
        self._closed = False
        self._capturing = False
        self._resize_edge = ""
        self._resize_start_geo = QRect()
        self._resize_start_pos = QPoint()
        self._moving = False
        self._move_start_geo = QRect()
        self._move_start_pos = QPoint()

        self._source_text = ""
        self._translated = ""
        self._blocks = []            # 带位置的句子块（归一化坐标）
        self._last_blocks = []
        self._state_text = "正在识别…"
        self._empty_count = 0
        self._last_hash = None
        self._last_source = ""
        self._last_translation = ""
        self._last_error = ""
        self._request_started = 0.0
        self._last_duration = 0.0

        self._frame_path = os.path.join(
            SCREENSHOT_DIR, f"_live_frame_{uuid.uuid4().hex[:8]}.png")

        self.setWindowTitle("实时翻译")
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

        self._bar = LiveControlBar(self)
        self._bar.pauseClicked.connect(self._toggle_pause)
        self._bar.copyClicked.connect(self._copy_text)
        self._bar.reselectClicked.connect(self.reselectRequested.emit)
        self._bar.closeClicked.connect(self.close)
        self._bar.sourceToggled.connect(self._set_show_source)
        self._bar.set_paused(False)
        self._bar.set_source(self._show_source)
        self._bar.set_status(self._speed_text("正在识别…"),
                             Palette.PAUSED)
        self._bar.follow(self._region)

        self._timer = QTimer(self)
        self._timer.setInterval(self._interval)
        self._timer.timeout.connect(self._tick)

        self._sig_done.connect(self._on_done)
        self._sig_error.connect(self._on_error)

    # ── 生命周期 ────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if not self._bar.isVisible():
            self._bar.show()
        self._bar.raise_()
        if not self._timer.isActive():
            self._timer.start()
        QTimer.singleShot(120, self._tick)

    def closeEvent(self, event):
        self._timer.stop()
        self._closed = True
        if self._bar is not None:
            self._bar.close()
        try:
            if os.path.exists(self._frame_path):
                os.remove(self._frame_path)
        except OSError:
            pass
        super().closeEvent(event)
        self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    # ── 选区移动 / 缩放 ─────────────────────────────────────

    def move_by(self, delta):
        if delta.isNull():
            return
        self._apply_region(self._region.translated(delta))

    def _apply_region(self, rect):
        rect = self._clamp_region(QRect(rect))
        if rect == self._region and rect.size() == self._region.size():
            return
        self._region = rect
        self.setGeometry(rect)
        self._bar.follow(self._region)
        self._bar.raise_()
        self._last_hash = None
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
        # 先纵后横，与 resize_util 的四角命名 tl/tr/bl/br 保持一致
        edge = ""
        if top:
            edge += "t"
        elif bottom:
            edge += "b"
        if left:
            edge += "l"
        elif right:
            edge += "r"
        return edge

    @staticmethod
    def _cursor_for_edge(edge):
        if edge in ("tl", "br"):
            return Qt.CursorShape.SizeFDiagCursor
        if edge in ("tr", "bl"):
            return Qt.CursorShape.SizeBDiagCursor
        if edge in ("l", "r"):
            return Qt.CursorShape.SizeHorCursor
        if edge in ("t", "b"):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _handle_hit(self, pos):
        """上边框中心的小圆点：按住拖动整个选区。"""
        handle = QPoint(self.rect().center().x(), 0)
        return (QPoint(pos) - handle).manhattanLength() <= 14

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
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
        if self._moving:
            delta = event.globalPosition().toPoint() - self._move_start_pos
            self._apply_region(self._move_start_geo.translated(delta))
            return
        if self._resize_edge:
            # 四角等比、单边自由缩放（与截图覆盖层共用同一套几何计算）
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
            edge = self._edge_at(pos, self.rect())
            self.setCursor(QCursor(self._cursor_for_edge(edge)))

    def mouseReleaseEvent(self, event):
        self._resize_edge = ""
        self._moving = False
        event.accept()

    # ── 刷新流程 ────────────────────────────────────────────

    def _tick(self):
        if (self._closed or self._paused or self._busy or self._capturing
                or self._resize_edge or self._moving or not self.isVisible()):
            return
        if self._region.width() < 8 or self._region.height() < 8:
            return
        screen = (QGuiApplication.screenAt(self._region.center())
                  or QGuiApplication.primaryScreen())
        if screen is None:
            return
        pixmap = self._grab_region(screen)
        if pixmap is None or pixmap.isNull():
            return
        digest = self._frame_digest(pixmap)
        if digest is not None and digest == self._last_hash:
            return
        if not pixmap.save(self._frame_path, "PNG"):
            self._bar.set_status("截图保存失败", Palette.LOG_ERROR)
            return
        self._last_hash = digest
        self._busy = True
        self._request_started = time.monotonic()
        self._bar.set_status(self._speed_text("正在识别…"), Palette.PAUSED)
        threading.Thread(
            target=self._process, args=(self._frame_path,), daemon=True
        ).start()

    def _grab_region(self, screen):
        """抓取选区内容；抓图瞬间隐藏本浮层，保证拿到的是原始画面。"""
        geo = screen.geometry()
        rel_x = self._region.x() - geo.x()
        rel_y = self._region.y() - geo.y()
        if (rel_x < 0 or rel_y < 0
                or rel_x + self._region.width() > geo.width()
                or rel_y + self._region.height() > geo.height()):
            return None
        bar_overlap = self._bar.geometry().intersects(self._region)
        old_opacity = self.windowOpacity()
        bar_opacity = self._bar.windowOpacity()
        self._capturing = True
        self.setWindowOpacity(0.0)
        if bar_overlap:
            self._bar.setWindowOpacity(0.0)
        try:
            # grabWindow 使用设备像素坐标，高分屏需按 DPR 缩放，否则抓到的区域
            # 相对原画面缩小/错位（Windows 上尤其明显）。
            dpr = screen.devicePixelRatio()
            px = int(rel_x * dpr)
            py = int(rel_y * dpr)
            pw = int(self._region.width() * dpr)
            ph = int(self._region.height() * dpr)
            return screen.grabWindow(0, px, py, pw, ph)
        finally:
            self.setWindowOpacity(old_opacity or 1.0)
            if bar_overlap:
                self._bar.setWindowOpacity(bar_opacity or 1.0)
            self._capturing = False

    @staticmethod
    def _frame_digest(pixmap):
        """低分辨率指纹：画面没变化时跳过 OCR。"""
        try:
            image = pixmap.toImage().scaled(
                48, 48, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation)
            return hashlib.sha256(bytes(image.bits())).hexdigest()
        except Exception:
            return None

    def _process(self, path):
        """线程入口：任何逃逸异常都要发完成信号，否则 _busy 永久卡住。"""
        try:
            self._process_inner(path)
        except Exception as e:
            self._sig_error.emit(f"实时翻译异常：{e}")

    def _process_inner(self, path):
        # 优先取带坐标的行块；引擎不支持或失败时回退到纯文本 OCR
        blocks = []
        if hasattr(self._ocr, "recognize_blocks"):
            try:
                blocks = self._ocr.recognize_blocks(path) or []
            except Exception:
                blocks = []
        try:
            if blocks:
                text = blocks_to_text(blocks)
            else:
                text = self._ocr.recognize(path) or ""
        except Exception as e:
            self._sig_error.emit(f"识别失败：{e}")
            return
        text = "\n".join(
            line.strip() for line in text.splitlines() if line.strip())
        if len(text) > _MAX_CHARS:
            text = text[:_MAX_CHARS]
        if not text:
            self._sig_done.emit("", "", [])
            return
        if text == self._last_source and self._last_translation:
            self._sig_done.emit(text, self._last_translation, self._last_blocks)
            return
        positioned = []
        try:
            if blocks:
                img = QImage(path)
                groups = normalize_groups(
                    group_blocks(blocks), img.width(), img.height())
                results = []
                for g in groups:
                    src = str(g.get("text", ""))
                    try:
                        results.append(
                            self._translator.translate_paragraph(src) or src)
                    except Exception:
                        results.append(src)
                positioned = [dict(g, trans=r)
                              for g, r in zip(groups, results)]
                translated = "\n\n".join(results)
            else:
                # 没有位置信息：沿用整段压平翻译
                translated = self._translator.translate(flatten_text(text)) or ""
        except Exception as e:
            self._sig_error.emit(f"翻译失败：{e}")
            return
        self._sig_done.emit(text, translated, positioned)

    def _on_done(self, source, translated, blocks=None):
        self._busy = False
        if self._closed:
            return
        duration = (time.monotonic() - self._request_started
                    if self._request_started else 0.0)
        self._last_duration = duration
        if not source:
            self._empty_count += 1
            if self._empty_count >= 3:
                self._source_text = ""
                self._translated = ""
                self._blocks = []
                self.update()
            self._state_text = "未识别到文字"
            self._bar.set_status(self._speed_text("未识别到文字"),
                                 Palette.STOPPED)
            return
        self._empty_count = 0
        if source == self._last_source and translated == self._last_translation:
            self._bar.set_status(
                self._speed_text(f"上次 {duration:.1f}s · 未变化"),
                Palette.RUNNING)
            return
        self._last_source = source
        self._last_translation = translated
        self._last_blocks = blocks or []
        self._source_text = source
        self._translated = translated.strip() or source
        self._blocks = blocks or []
        self.update()
        self._bar.set_status(
            self._speed_text(f"上次 {duration:.1f}s"), Palette.RUNNING)

    def _on_error(self, msg):
        self._busy = False
        if self._closed:
            return
        if msg != self._last_error:
            self._last_error = msg
            self._bar.set_status(msg[:100], Palette.LOG_ERROR)
        self._state_text = "识别/翻译失败"
        self.update()
        # 画面不变也允许 3 秒后重试一次
        QTimer.singleShot(3000, self._retry_after_error)

    def _retry_after_error(self):
        if not self._closed:
            self._last_hash = None

    # ── 交互 ────────────────────────────────────────────────

    def _speed_text(self, tail):
        return f"刷新 {self._interval / 1000:.1f}s/次 · {tail}"

    def _toggle_pause(self):
        self._paused = not self._paused
        self._bar.set_paused(self._paused)
        if self._paused:
            self._state_text = "已暂停"
            self._bar.set_status("已暂停", Palette.PAUSED)
        else:
            self._last_hash = None
            self._bar.set_status(self._speed_text("正在识别…"),
                                 Palette.RUNNING)
            QTimer.singleShot(0, self._tick)
        self.update()

    def _set_show_source(self, checked):
        self._show_source = bool(checked)
        self.update()

    def _copy_text(self):
        text = self._translated or self._source_text
        if text:
            QApplication.clipboard().setText(text)
            self._bar.set_status(
                f"已复制 · {datetime.now().strftime('%H:%M:%S')}",
                Palette.RUNNING)

    # ── 绘制 ────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 有位置信息时逐块覆盖原文行，只留很淡的底色；否则保持整块遮罩
        if self._blocks and self._translated:
            painter.fillRect(self.rect(), QColor(8, 8, 12, 70))
        else:
            painter.fillRect(self.rect(), QColor(8, 8, 12, 168))
        painter.setPen(QPen(QColor(96, 165, 250, 110), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        self._draw_corner_brackets(painter)

        inner = QRectF(self.rect().adjusted(16, 14, -16, -14))
        if self._translated and self._blocks:
            self._draw_blocks(painter)
        elif self._translated:
            if self._show_source and self._source_text:
                source_font = QFont("Microsoft YaHei")
                source_font.setPixelSize(12)
                fm = QFontMetrics(source_font)
                source_h = min(inner.height() * 0.35,
                                fm.lineSpacing() * 3)
                source_rect = QRectF(
                    inner.left(), inner.top(), inner.width(), source_h)
                self._draw_text(
                    painter, source_rect, self._source_text, source_font,
                    QColor(Palette.TEXT_MUTED), sentences=False,
                    max_height=source_h)
                line_y = inner.top() + source_h + 4
                painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
                painter.drawLine(QPointF(inner.left(), line_y),
                                 QPointF(inner.right(), line_y))
                inner.setTop(line_y + 8)
            if inner.height() >= 12:
                font = QFont("Microsoft YaHei")
                size = max(13, min(20, int(self.width() / 24)))
                if len(self._translated) > 120:
                    size = max(12, size - 2)
                if len(self._translated) > 260:
                    size = max(12, size - 2)
                font.setPixelSize(size)
                self._draw_text(painter, inner, self._translated, font,
                                QColor(250, 250, 250))
        else:
            font = QFont("Microsoft YaHei")
            font.setPixelSize(14)
            self._draw_text(painter, inner, self._state_text, font,
                            QColor(Palette.TEXT_MUTED), center=True)

        if self._paused:
            self._draw_paused_badge(painter)
        painter.end()

    def _draw_blocks(self, painter):
        """译文按【视觉段落】原位整块覆盖；原文对照时每块顶部附一行原文小字。

        与截图覆盖层同口径：一个自然段一个底框，盖住整段原文 bbox，
        字号从大到小自适应，译文按行距铺开。先铺全部底框再写字，
        所有块都要画到，原文不会裸露。
        """
        pad = 8
        bleed = 3
        W, H = self.width(), self.height()

        # 1) 原文 bbox → 浮层坐标
        rects = []
        for b in self._blocks:
            ox = float(b.get("x", 0)) * W
            oy = float(b.get("y", 0)) * H
            ow = float(b.get("w", 0)) * W
            oh = float(b.get("h", 0)) * H
            left = max(0, min(int(ox - bleed), W - 24))
            top = max(0, min(int(oy - bleed), H - 8))
            w = max(24, min(int(ow + bleed * 2), W - left))
            h = max(8, min(int(oh + bleed * 2), H - top))
            rects.append((left, top, w, h))

        font = QFont("Microsoft YaHei")
        src_font = QFont("Microsoft YaHei")
        src_font.setPixelSize(11)
        src_fm = QFontMetrics(src_font)
        src_line_h = src_fm.lineSpacing()

        # 2) 字号从大到小：挑第一个「所有段落都放得下」的字号
        chosen = None
        for size in (20, 19, 18, 17, 16, 15, 14, 13, 12, 11):
            font.setPixelSize(size)
            fm = QFontMetrics(font)
            items = []
            fits_all = True
            for b, (left, top, w, h) in zip(self._blocks, rects):
                max_w = max(24, w - pad * 2)
                lines = wrap_sentences(
                    str(b.get("trans") or "").strip(),
                    max_w, fm.horizontalAdvance) or [""]
                src_lines = []
                if self._show_source:
                    all_src = [ln for ln in wrap_by_width(
                        str(b.get("text") or "").strip(),
                        max_w, src_fm.horizontalAdvance) if ln.strip()]
                    if all_src:
                        first = all_src[0].rstrip(" ,.")
                        # 整段原文很长，只取首行做对照并明确标注省略
                        src_lines = [first + ("…" if len(all_src) > 1 else "")]
                src_h = (len(src_lines) * src_line_h
                         + (4 if src_lines else 0))
                need = src_h + len(lines) * fm.lineSpacing() + pad * 2
                items.append((lines, src_lines, need, max_w))
                if need > h:
                    fits_all = False
            chosen = (QFont(font), QFontMetrics(font), items)
            if fits_all:
                break
        font, fm, items = chosen
        line_h = fm.lineSpacing()

        # 3) 先铺全部底框：框高 = max(原文 bbox, 译文所需)，原文不会露出
        heights = [min(max(h, need), max(8, H - top))
                   for (_, top, _, h), (_, _, need, _) in zip(rects, items)]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(8, 8, 12, 238))
        for (left, top, w, h), draw_h in zip(rects, heights):
            painter.drawRoundedRect(QRectF(left, top, w, draw_h), 5, 5)

        # 4) 逐块写文字：所有块都画，不 break
        for (left, top, w, h), (lines, src_lines, need, max_w), draw_h in zip(
                rects, items, heights):
            src_h = (len(src_lines) * src_line_h
                     + (4 if src_lines else 0))
            room = draw_h - pad * 2 - src_h
            keep = (len(lines) if need <= draw_h else
                    max(1, int(room // line_h)))
            draw = list(lines[:keep])
            if len(lines) > keep and draw:
                last = draw[-1]
                while last and fm.horizontalAdvance(last + "…") > max_w:
                    last = last[:-1]
                draw[-1] = (last.rstrip("，,、 ") + "…") if last else "…"
            # 行距按可用高度铺开 + 整体居中，避免大框里空一大片
            step = int(min(line_h * 2.0,
                           max(line_h, room // max(1, len(draw)))))
            used = len(draw) * step
            ty = float(top + pad + src_h
                       + max(0, (room - used) // 2))
            if src_lines:
                painter.setFont(src_font)
                painter.setPen(QColor(Palette.TEXT_MUTED))
                painter.drawText(
                    QRectF(left + pad, top + pad, max_w, src_line_h),
                    Qt.AlignmentFlag.AlignLeft, src_lines[0])
            painter.setFont(font)
            for line in draw:
                line_rect = QRectF(left + pad, ty, max_w, line_h)
                painter.setPen(QColor(0, 0, 0, 170))
                painter.drawText(line_rect.translated(1, 1),
                                 Qt.AlignmentFlag.AlignLeft, line)
                painter.setPen(QColor(250, 250, 250))
                painter.drawText(line_rect, Qt.AlignmentFlag.AlignLeft, line)
                ty += step

    def _draw_text(self, painter, rect, text, font, color, center=False,
                   sentences=True, max_height=None):
        """按句折行绘制：整句放得下就续排，放不下才换行。

        sentences=False 时只按宽度折行并保留原文换行（用于原文对照）。
        max_height 用于限制绘制高度（超出时末尾加省略号）。
        """
        from text_segment import wrap_by_width, wrap_sentences
        fm = QFontMetrics(font)
        if sentences:
            lines = wrap_sentences(text, rect.width(), fm.horizontalAdvance)
        else:
            lines = wrap_by_width(text, rect.width(), fm.horizontalAdvance)
        line_h = fm.lineSpacing()
        limit = rect.height() if max_height is None else min(
            rect.height(), max_height)
        max_lines = max(1, int(limit // line_h))
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            last = lines[-1]
            while last and fm.horizontalAdvance(last + "…") > rect.width():
                last = last[:-1]
            lines[-1] = last.rstrip("，,、 ") + "…"

        painter.setFont(font)
        align = (Qt.AlignmentFlag.AlignHCenter if center
                 else Qt.AlignmentFlag.AlignLeft)
        y = rect.top()
        for line in lines:
            line_rect = QRectF(rect.left(), y, rect.width(), line_h)
            painter.setPen(QColor(0, 0, 0, 170))
            painter.drawText(line_rect.translated(1, 1), align, line)
            painter.setPen(color)
            painter.drawText(line_rect, align, line)
            y += line_h

    def _draw_corner_brackets(self, painter):
        length = 16
        margin = 5
        rect = self.rect()
        painter.setPen(QPen(QColor(96, 165, 250, 230), 2))
        # 左上
        painter.drawLine(rect.left() + margin, rect.top() + margin,
                         rect.left() + margin + length, rect.top() + margin)
        painter.drawLine(rect.left() + margin, rect.top() + margin,
                         rect.left() + margin, rect.top() + margin + length)
        # 右上
        painter.drawLine(rect.right() - margin - length, rect.top() + margin,
                         rect.right() - margin, rect.top() + margin)
        painter.drawLine(rect.right() - margin, rect.top() + margin,
                         rect.right() - margin, rect.top() + margin + length)
        # 左下
        painter.drawLine(rect.left() + margin, rect.bottom() - margin,
                         rect.left() + margin + length, rect.bottom() - margin)
        painter.drawLine(rect.left() + margin, rect.bottom() - margin - length,
                         rect.left() + margin, rect.bottom() - margin)
        # 右下
        painter.drawLine(rect.right() - margin - length, rect.bottom() - margin,
                         rect.right() - margin, rect.bottom() - margin)
        painter.drawLine(rect.right() - margin, rect.bottom() - margin - length,
                         rect.right() - margin, rect.bottom() - margin)
        # 上边中心：拖动选区的小圆点
        center = QPoint(rect.center().x(), rect.top())
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(QColor(96, 165, 250))
        painter.drawEllipse(center, 7, 7)
        painter.setPen(QPen(QColor(96, 165, 250), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, 10, 10)

    def _draw_paused_badge(self, painter):
        text = "已暂停"
        font = QFont("Microsoft YaHei")
        font.setPixelSize(11)
        painter.setFont(font)
        fm = QFontMetrics(font)
        width = fm.horizontalAdvance(text) + 16
        height = fm.height() + 6
        rect = QRectF(self.width() - width - 8, 8, width, height)
        painter.setPen(QPen(QColor(234, 179, 8, 180), 1))
        painter.setBrush(QColor(30, 26, 10, 220))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor(Palette.PAUSED))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
