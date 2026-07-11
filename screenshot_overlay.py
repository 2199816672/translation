#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PySide6 全屏截图覆盖层：区域选择 + 提取文字/翻译（类微信交互）。"""
import os
import textwrap

from PySide6.QtCore import Qt, QRect, QPoint, QSize, Signal
from PySide6.QtGui import (
    QPainter, QColor, QPixmap, QPen, QFont, QFontMetrics, QCursor,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QFileDialog,
    QFrame, QTextEdit, QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
)

from theme import Palette


class ScreenshotOverlay(QWidget):
    """全屏覆盖层：选区 → 菜单 → 提取(浮动文字框) / 翻译(叠加译文，可切换)。"""

    screenshotTaken = Signal(str)
    extractRequested = Signal(str)
    translateRequested = Signal(str)
    finished = Signal()

    BTN_W = 64
    BTN_H = 30
    BTN_GAP = 6
    MENU_PAD = 12
    ACTIONS = ["复制", "保存", "提取", "翻译", "取消"]
    POPUP_MAX_W = 360
    POPUP_PAD = 12
    POPUP_LINE_H = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

        self._start = QPoint()
        self._end = QPoint()
        self._selecting = False
        self._raw_pixmap = None
        self._scale = 1.0

        self._crop_rect = None
        self._show_menu = False
        self._menu_buttons = []
        self._hover_idx = -1

        self._mode = None
        self._ocr_text = ""
        self._trans_text = ""
        self._showing_trans = False
        self._popup_rect = QRect()
        self._popup_close = QRect()
        self._toggle_rect = QRect()
        self._extract_popup = None
        self._extract_text_edit = None

    # ── 截图启动 ──────────────────────────────────────────────

    def start_capture(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return

        geo = screen.geometry()
        self._raw_pixmap = screen.grabWindow(0, geo.x(), geo.y())

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setGeometry(geo)
        self.show()
        self.activateWindow()
        self.raise_()

        self._calc_scale()

    def _calc_scale(self):
        """根据实际 pixmap 和 widget 尺寸计算缩放比。"""
        if self._raw_pixmap and self.width() > 0 and self.height() > 0:
            pw, ph = self._raw_pixmap.width(), self._raw_pixmap.height()
            ww, wh = self.width(), self.height()
            self._scale = pw / ww if ww > 0 else 1.0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._calc_scale()

    # ── 坐标转换 ──────────────────────────────────────────────

    def _logical_to_pixmap(self, rect):
        """逻辑像素矩形 → pixmap 像素矩形。"""
        s = self._scale
        return QRect(int(rect.x() * s), int(rect.y() * s),
                     int(rect.width() * s), int(rect.height() * s))

    # ── 外部调用 ──────────────────────────────────────────────

    def show_ocr_result(self, text):
        self._ocr_text = text
        self._trans_text = ""
        self._showing_trans = False
        self._mode = "extract"
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self._show_extract_popup(text)

    def show_trans_result(self, text):
        self._trans_text = text
        self._showing_trans = True
        self._mode = "translate"
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.update()

    # ── 绘制 ─────────────────────────────────────────────────

    def paintEvent(self, event):
        if self._raw_pixmap is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        target = QRect(0, 0, self.width(), self.height())
        p.drawPixmap(target, self._raw_pixmap)

        if self._selecting and not self._crop_rect:
            self._draw_selecting(p)
        elif self._crop_rect and self._show_menu:
            self._draw_crop(p)
            self._draw_menu(p)
        elif self._crop_rect and self._mode == "extract":
            self._draw_crop(p)
        elif self._crop_rect and self._mode == "translate":
            self._draw_crop(p)
            self._draw_trans_overlay(p)

        p.end()

    def _draw_selecting(self, p):
        rect = QRect(self._start, self._end).normalized()

        p.setClipRect(rect)
        src = self._logical_to_pixmap(rect)
        p.drawPixmap(rect, self._raw_pixmap, src)
        p.setClipping(False)

        p.setPen(QPen(QColor("#ef4444"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)

        p.setPen(QColor(Palette.TEXT))
        p.setFont(QFont("Microsoft YaHei", 12))
        hint = "拖动鼠标选择区域 | ESC/右键 取消"
        tw = p.fontMetrics().horizontalAdvance(hint)
        hx = self.width() // 2 - tw // 2
        hy = self.height() - 50
        p.fillRect(hx - 12, hy - 20, tw + 24, 30, QColor(0, 0, 0, 160))
        p.drawText(hx, hy, hint)

    def _draw_crop(self, p):
        r = self._crop_rect
        src = self._logical_to_pixmap(r)
        p.drawPixmap(r, self._raw_pixmap, src)
        p.setPen(QPen(QColor("#ef4444"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)

    # ── 菜单 ─────────────────────────────────────────────────

    def _draw_menu(self, p):
        btn_count = len(self.ACTIONS)
        total_w = btn_count * self.BTN_W + (btn_count - 1) * self.BTN_GAP + self.MENU_PAD * 2
        total_h = self.BTN_H + self.MENU_PAD * 2

        mx = self._crop_rect.center().x() - total_w // 2
        my = self._crop_rect.bottom() + 10
        if my + total_h > self.height():
            my = self._crop_rect.top() - total_h - 10

        bg = QRect(mx, my, total_w, total_h)
        p.setPen(QPen(QColor(Palette.BORDER), 1))
        p.setBrush(QColor(Palette.SURFACE))
        p.drawRoundedRect(bg, 10, 10)

        self._menu_buttons.clear()
        bx, by = mx + self.MENU_PAD, my + self.MENU_PAD
        for i, label in enumerate(self.ACTIONS):
            br = QRect(bx, by, self.BTN_W, self.BTN_H)
            self._menu_buttons.append(br)
            if i == self._hover_idx:
                p.setBrush(QColor(Palette.SURFACE_HOVER))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(br, 6, 6)
            p.setPen(QColor(Palette.TEXT_STRONG) if i == 0 else QColor(Palette.TEXT))
            p.setFont(QFont("Microsoft YaHei", 11))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, label)
            bx += self.BTN_W + self.BTN_GAP

    # ── 翻译叠加 ─────────────────────────────────────────────

    def _draw_trans_overlay(self, p):
        cr = self._crop_rect
        display_text = self._trans_text if self._showing_trans else self._ocr_text
        if not display_text:
            return

        font = QFont("Microsoft YaHei", 11)
        fm = QFontMetrics(font)
        lines = []
        for raw_line in display_text.split("\n"):
            if not raw_line.strip():
                lines.append("")
                continue
            wrapped = textwrap.wrap(raw_line, width=40) or [""]
            lines.extend(wrapped)

        line_h = fm.height()
        text_h = len(lines) * line_h
        pad = 8
        box_h = text_h + pad * 2
        box_w = cr.width()

        if box_h < cr.height():
            box_y = cr.bottom() - box_h
        else:
            box_y = cr.top()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 180))
        p.drawRoundedRect(QRect(cr.left(), box_y, box_w, box_h), 6, 6)

        p.setPen(QColor(Palette.TEXT))
        p.setFont(font)
        ty = box_y + pad + fm.ascent()
        for line in lines:
            p.drawText(cr.left() + pad, ty, line)
            ty += line_h

        toggle_label = "查看原文" if self._showing_trans else "查看译文"
        tw = fm.horizontalAdvance(toggle_label)
        tr = QRect(cr.right() - tw - 14, cr.top() + 2, tw + 10, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Palette.SURFACE))
        p.drawRoundedRect(tr, 5, 5)
        p.setPen(QColor(Palette.TEXT))
        p.setFont(QFont("Microsoft YaHei", 9))
        p.drawText(tr, Qt.AlignmentFlag.AlignCenter, toggle_label)
        self._toggle_rect = tr

        close_label = "×"
        cw = 20
        cr2 = QRect(cr.right() - cw - 4, cr.top() + 2, cw, 22)
        p.setPen(QColor(Palette.TEXT_MUTED))
        p.setFont(QFont("Microsoft YaHei", 12))
        p.drawText(cr2, Qt.AlignmentFlag.AlignCenter, close_label)
        self._popup_close = cr2

    # ── 提取弹窗（真实 Widget）──────────────────────────────

    def _create_extract_popup(self):
        self._extract_popup = QFrame(self)
        self._extract_popup.setStyleSheet(f"""
            QFrame {{
                background: {Palette.SURFACE};
                border: 1px solid {Palette.BORDER};
                border-radius: 10px;
            }}
            QTextEdit {{
                background: {Palette.BG_INPUT};
                color: {Palette.TEXT};
                border: 1px solid {Palette.BORDER};
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
                font-family: 'Microsoft YaHei';
            }}
            QTextEdit:focus {{ border: 1px solid {Palette.FOCUS}; }}
            QPushButton {{
                background: transparent;
                color: {Palette.TEXT_MUTED};
                border: none;
                font-size: 14px;
                font-family: 'Microsoft YaHei';
            }}
            QPushButton:hover {{ color: {Palette.TEXT_STRONG}; }}
            QLabel {{
                background: transparent;
                border: none;
                color: {Palette.TEXT_STRONG};
                font-size: 13px;
                font-weight: 600;
                font-family: 'Microsoft YaHei';
            }}
        """)
        layout = QVBoxLayout(self._extract_popup)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("识别文字")
        header.addWidget(title)
        header.addStretch(1)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self._on_extract_popup_close)
        header.addWidget(close_btn)
        layout.addLayout(header)

        self._extract_text_edit = QTextEdit()
        self._extract_text_edit.setReadOnly(True)
        self._extract_text_edit.setMinimumSize(300, 120)
        self._extract_text_edit.setMaximumSize(420, 500)
        self._extract_text_edit.setWordWrapMode(QFontMetrics.WrapAtWordBoundaryOrOccurrence)
        layout.addWidget(self._extract_text_edit)

    def _show_extract_popup(self, text):
        if self._extract_popup is None:
            self._create_extract_popup()
        self._extract_text_edit.setPlainText(text)
        cr = self._crop_rect
        popup_w = min(400, max(300, self.width() // 4))
        lines = textwrap.wrap(text, width=45) or [""]
        popup_h = min(400, max(150, len(lines) * 22 + 60))
        px = cr.right() + 14
        py = cr.top()
        if px + popup_w > self.width():
            px = max(0, cr.left() - popup_w - 14)
        if py + popup_h > self.height():
            py = max(0, self.height() - popup_h - 10)
        if py < 0:
            py = 0
        self._extract_popup.setGeometry(px, py, popup_w, popup_h)
        self._extract_popup.show()
        self._extract_popup.raise_()

    def _hide_extract_popup(self):
        if self._extract_popup:
            self._extract_popup.hide()

    def _on_extract_popup_close(self):
        self._hide_extract_popup()
        self._show_menu = True
        self._hover_idx = -1
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    # ── 鼠标事件 ──────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            if event.button() == Qt.MouseButton.RightButton:
                if self._mode:
                    self._reset_mode()
                else:
                    self._cancel()
            return

        pos = event.pos()

        # 提取弹窗：点击弹窗外关闭并回到菜单
        if (self._mode == "extract" and self._extract_popup
                and self._extract_popup.isVisible()):
            if not self._extract_popup.geometry().contains(pos):
                self._on_extract_popup_close()
            return

        if self._mode and hasattr(self, "_popup_close") and self._popup_close.contains(pos):
            self._reset_mode()
            return

        if self._mode == "translate" and hasattr(self, "_toggle_rect") and self._toggle_rect.contains(pos):
            self._showing_trans = not self._showing_trans
            self.update()
            return

        if self._show_menu:
            for i, br in enumerate(self._menu_buttons):
                if br.contains(pos):
                    self._handle_action(i)
                    return
            return

        if self._mode:
            return

        self._selecting = True
        self._start = pos
        self._end = pos
        self._crop_rect = None
        self._show_menu = False
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self._show_menu:
            new_idx = -1
            for i, br in enumerate(self._menu_buttons):
                if br.contains(pos):
                    new_idx = i
                    break
            if new_idx != self._hover_idx:
                self._hover_idx = new_idx
                self.update()
            return
        if self._selecting:
            self._end = pos
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._selecting = False
            self._end = event.pos()
            rect = QRect(self._start, self._end).normalized()
            if rect.width() < 5 or rect.height() < 5:
                return
            self._crop_rect = rect
            self._show_menu = True
            self._hover_idx = -1
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self._mode:
                self._reset_mode()
            else:
                self._cancel()

    # ── 状态管理 ──────────────────────────────────────────────

    def _reset_mode(self):
        self._hide_extract_popup()
        self._mode = None
        self._ocr_text = ""
        self._trans_text = ""
        self._showing_trans = False
        self._show_menu = True
        self._hover_idx = -1
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def _cancel(self):
        self._hide_extract_popup()
        self.close()
        self.finished.emit()

    def _handle_action(self, idx):
        name = self.ACTIONS[idx]
        self._show_menu = False
        self.setCursor(QCursor(Qt.CursorShape.WaitCursor))

        if name == "复制":
            image = self._crop_image()
            if image:
                QApplication.clipboard().setPixmap(image)
            path = self._save_to_temp()
            self._hide_extract_popup()
            self.close()
            if path:
                self.screenshotTaken.emit(path)
            self.finished.emit()
        elif name == "保存":
            image = self._crop_image()
            if image:
                save_path, _ = QFileDialog.getSaveFileName(
                    self, "保存截图", "screenshot.png",
                    "PNG 图片 (*.png);;JPG 图片 (*.jpg)")
                if save_path:
                    image.save(save_path)
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self._show_menu = True
            self.update()
        elif name == "提取":
            path = self._save_to_temp()
            if path:
                self.extractRequested.emit(path)
        elif name == "翻译":
            path = self._save_to_temp()
            if path:
                self.translateRequested.emit(path)
        else:
            self._cancel()

    # ── 图片操作 ──────────────────────────────────────────────

    def _crop_image(self):
        if not self._crop_rect or not self._raw_pixmap:
            return None
        return self._raw_pixmap.copy(self._logical_to_pixmap(self._crop_rect))

    def _save_to_temp(self, image=None):
        if image is None:
            image = self._crop_image()
        if image is None:
            return None
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "temp_screenshot.png")
        image.save(path)
        return path

    def _close_and_emit(self, path):
        self.close()
        if path:
            self.screenshotTaken.emit(path)
        self.finished.emit()


class FullscreenCapture:
    """全屏截图（无需选区）。"""

    @staticmethod
    def capture():
        screen = QApplication.primaryScreen()
        if not screen:
            return None
        geo = screen.geometry()
        pixmap = screen.grabWindow(0, geo.x(), geo.y())
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "temp_screenshot.png")
        pixmap.save(path)
        QApplication.clipboard().setPixmap(pixmap)
        return path
