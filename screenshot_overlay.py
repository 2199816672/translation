#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PySide6 全屏截图覆盖层：区域选择 + 提取文字/翻译（类微信交互）。"""
import os
import textwrap

from PySide6.QtCore import Qt, QRect, QRectF, QPoint, QSize, Signal
from PySide6.QtGui import (
    QPainter, QColor, QPixmap, QPen, QFont, QFontMetrics, QCursor, QTextOption,
    QPainterPath,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QFileDialog,
    QFrame, QTextEdit, QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
    QInputDialog, QColorDialog, QSlider,
)

from theme import Palette


class EditWidget(QWidget):
    """截图编辑工具栏 + 画布：画笔、橡皮、文字、颜色、撤销/重做。"""
    TOOLBAR_H = 44
    COLORS = ["#ef4444", "#3b82f6", "#22c55e", "#eab308", "#000000", "#ffffff"]

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self._base = pixmap.copy()
        self._canvas = pixmap.copy()
        self._undo_stack = []
        self._redo_stack = []
        self._tool = "pen"
        self._color = QColor("#ef4444")
        self._pen_w = 3
        self._eraser_w = 15
        self._text_font_size = 14
        self._drawing = False
        self._last = QPoint()
        self._eraser_pos = None
        self._on_save_cb = None
        self._on_cancel_cb = None
        self._tool_btns = {}
        self._color_btns = []
        self._size_slider = None
        self._init_ui()

    def _init_ui(self):
        w, h = self._base.width(), self._base.height()
        self.setFixedSize(w, h + self.TOOLBAR_H)

        tb = QWidget(self)
        tb.setObjectName("editToolbar")
        tb.setFixedHeight(self.TOOLBAR_H)
        tb.setStyleSheet(f"""
            #editToolbar {{ background: #1a1a1a; border-bottom: 1px solid #333; }}
            QPushButton {{
                background: #2a2a2e; color: #e4e4e7; border: 1px solid #3f3f46;
                border-radius: 5px; padding: 4px 10px; font-size: 12px;
                font-family: 'Microsoft YaHei'; min-height: 22px;
            }}
            QPushButton:hover {{ background: #3f3f46; }}
            QPushButton:checked {{ background: #52525b; border-color: #71717a; color: #fafafa; }}
            #colorBtn {{ border: 1px solid #555; border-radius: 4px; min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px; padding: 0; }}
            QSlider {{ background: transparent; }}
            QSlider::groove:horizontal {{ background: #3f3f46; height: 4px; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: #e4e4e7; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
        """)
        tb_layout = QHBoxLayout(tb)
        tb_layout.setContentsMargins(8, 5, 8, 5)
        tb_layout.setSpacing(5)

        tools = [("画笔", "pen"), ("橡皮", "eraser"), ("文字", "text")]
        for name, tool in tools:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(tool == self._tool)
            btn.clicked.connect(lambda checked, t=tool: self._set_tool(t))
            tb_layout.addWidget(btn)
            self._tool_btns[tool] = btn

        # 动态尺寸标签 + 滑条（橡皮/文字共用位置）
        self._size_label = QLabel("3")
        self._size_label.setStyleSheet("color: #a1a1aa; border: none; background: transparent; font-size: 11px; min-width: 18px;")
        tb_layout.addWidget(self._size_label)

        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setRange(1, 50)
        self._size_slider.setValue(3)
        self._size_slider.setFixedWidth(80)
        self._size_slider.valueChanged.connect(self._on_size_changed)
        tb_layout.addWidget(self._size_slider)

        tb_layout.addSpacing(4)
        sep = QLabel("|")
        sep.setStyleSheet("color: #555; border: none; background: transparent;")
        tb_layout.addWidget(sep)
        tb_layout.addSpacing(4)

        for c in self.COLORS:
            btn = QPushButton()
            btn.setObjectName("colorBtn")
            btn.setFixedSize(20, 20)
            btn.setStyleSheet(f"background: {c}; border: 1px solid #555; border-radius: 4px;")
            btn.clicked.connect(lambda checked, color=c: self._set_color(color))
            tb_layout.addWidget(btn)
            self._color_btns.append((btn, c))

        custom_btn = QPushButton("…")
        custom_btn.setObjectName("colorBtn")
        custom_btn.setFixedSize(20, 20)
        custom_btn.setStyleSheet("background: #666; border: 1px solid #555; border-radius: 4px; font-size: 11px;")
        custom_btn.clicked.connect(self._pick_custom_color)
        tb_layout.addWidget(custom_btn)

        tb_layout.addStretch()

        undo_btn = QPushButton("撤销")
        undo_btn.clicked.connect(self.undo)
        tb_layout.addWidget(undo_btn)
        redo_btn = QPushButton("重做")
        redo_btn.clicked.connect(self.redo)
        tb_layout.addWidget(redo_btn)

        tb_layout.addSpacing(8)

        save_btn = QPushButton("保存")
        save_btn.setStyleSheet("QPushButton{background:#22c55e;color:#000;border:1px solid #22c55e;font-weight:600;}"
                              "QPushButton:hover{background:#16a34a;}")
        save_btn.clicked.connect(self._save)
        tb_layout.addWidget(save_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self._cancel)
        tb_layout.addWidget(cancel_btn)

        self._update_slider_for_tool()

    def _on_size_changed(self, val):
        if self._tool == "eraser":
            self._eraser_w = val
            self._size_label.setText(str(val))
        elif self._tool == "text":
            self._text_font_size = val
            self._size_label.setText(str(val))
        elif self._tool == "pen":
            self._pen_w = max(1, val // 3)
            self._size_label.setText(str(self._pen_w))

    def _update_slider_for_tool(self):
        if self._tool == "eraser":
            self._size_slider.setRange(5, 50)
            self._size_slider.setValue(self._eraser_w)
            self._size_label.setText(str(self._eraser_w))
        elif self._tool == "text":
            self._size_slider.setRange(10, 36)
            self._size_slider.setValue(self._text_font_size)
            self._size_label.setText(str(self._text_font_size))
        elif self._tool == "pen":
            self._size_slider.setRange(1, 15)
            self._size_slider.setValue(self._pen_w)
            self._size_label.setText(str(self._pen_w))

    def _set_tool(self, tool):
        self._tool = tool
        for t, btn in self._tool_btns.items():
            btn.setChecked(t == tool)
        self._update_slider_for_tool()
        if tool == "eraser":
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def _set_color(self, hex_color):
        self._color = QColor(hex_color)

    def _pick_custom_color(self):
        c = QColorDialog.getColor(self._color, self, "选择颜色")
        if c.isValid():
            self._color = c

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, self.TOOLBAR_H, self._canvas)
        # 橡皮光标圆圈
        if self._eraser_pos and self._tool == "eraser":
            r = self._eraser_w
            cx, cy = self._eraser_pos.x(), self._eraser_pos.y() + self.TOOLBAR_H
            p.setPen(QPen(QColor(255, 255, 255, 200), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        p.end()

    def mousePressEvent(self, event):
        if event.y() < self.TOOLBAR_H:
            return
        pos = QPoint(event.x(), event.y() - self.TOOLBAR_H)

        if self._tool == "text":
            text, ok = QInputDialog.getText(self, "输入文字", "请输入:")
            if ok and text:
                self._push_undo()
                p = QPainter(self._canvas)
                p.setPen(QPen(self._color))
                p.setFont(QFont("Microsoft YaHei", self._text_font_size))
                p.drawText(pos, text)
                p.end()
                self.update()
            return

        self._drawing = True
        self._last = pos
        self._push_undo()

        if self._tool == "eraser":
            img = self._base.toImage()
            ix = min(max(pos.x(), 0), self._base.width() - 1)
            iy = min(max(pos.y(), 0), self._base.height() - 1)
            c = QColor(img.pixel(ix, iy))
            p = QPainter(self._canvas)
            p.setPen(QPen(c, self._eraser_w, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPoint(pos)
            p.end()
            self.update()

    def mouseMoveEvent(self, event):
        pos = QPoint(event.x(), event.y() - self.TOOLBAR_H)
        # 橡皮光标追踪（即使未按下也绘制）
        if self._tool == "eraser":
            self._eraser_pos = pos
            self.update()
        if not self._drawing:
            return
        pos = QPoint(max(0, min(pos.x(), self._canvas.width() - 1)),
                      max(0, min(pos.y(), self._canvas.height() - 1)))

        p = QPainter(self._canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._tool == "pen":
            p.setPen(QPen(self._color, self._pen_w, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawLine(self._last, pos)
        elif self._tool == "eraser":
            img = self._base.toImage()
            ix = min(max(self._last.x(), 0), self._base.width() - 1)
            iy = min(max(self._last.y(), 0), self._base.height() - 1)
            c = QColor(img.pixel(ix, iy))
            p.setPen(QPen(c, self._eraser_w, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawLine(self._last, pos)
        p.end()
        self._last = pos
        self.update()

    def mouseReleaseEvent(self, event):
        self._drawing = False

    def leaveEvent(self, event):
        self._eraser_pos = None
        self.update()

    def _push_undo(self):
        self._undo_stack.append(self._canvas.copy())
        self._redo_stack.clear()
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)

    def undo(self):
        if self._undo_stack:
            self._redo_stack.append(self._canvas.copy())
            self._canvas = self._undo_stack.pop()
            self.update()

    def redo(self):
        if self._redo_stack:
            self._undo_stack.append(self._canvas.copy())
            self._canvas = self._redo_stack.pop()
            self.update()

    def _save(self):
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "temp_screenshot.png")
        self._canvas.save(path)
        if self._on_save_cb:
            self._on_save_cb(path)
        self.hide()

    def _cancel(self):
        if self._on_cancel_cb:
            self._on_cancel_cb()
        self.hide()


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

    def __init__(self, auto_translate=False, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
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
        self._extract_popup = None
        self._extract_text_edit = None
        self._edit_widget = None
        self._auto_translate = auto_translate

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
        self._show_menu = True
        self._hover_idx = -1
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
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
            if self._mode == "translate":
                self._draw_trans_overlay(p)
        elif self._crop_rect and self._mode == "extract":
            self._draw_crop(p)
        elif self._crop_rect and self._mode == "translate":
            self._draw_crop(p)
            self._draw_trans_overlay(p)

        p.end()

    def _draw_selecting(self, p):
        rect = QRect(self._start, self._end).normalized()

        # 用 QPainterPath 挖空选区——背景已由 paintEvent 画好，只需盖遮罩
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        path = QPainterPath()
        path.addRect(QRectF(0, 0, self.width(), self.height()))
        path.addRect(QRectF(rect))
        p.drawPath(path)

        # 选区边框
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

        # 下方放不下 → 尝试上方
        if my + total_h > self.height():
            my = self._crop_rect.top() - total_h - 10

        # 上方也放不下（全屏/超长选区）→ 屏幕顶部居中
        if my < 0:
            my = 10
            mx = self.width() // 2 - total_w // 2

        if mx < 0:
            mx = 0
        if mx + total_w > self.width():
            mx = self.width() - total_w

        bg = QRect(mx, my, total_w, total_h)
        p.setPen(QPen(QColor(Palette.BORDER), 1))
        p.setBrush(QColor(22, 22, 24, 128))
        p.drawRoundedRect(bg, 10, 10)

        self._menu_buttons.clear()
        bx, by = mx + self.MENU_PAD, my + self.MENU_PAD
        for i, label in enumerate(self.ACTIONS):
            br = QRect(bx, by, self.BTN_W, self.BTN_H)
            self._menu_buttons.append(br)
            if i == self._hover_idx:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(59, 130, 246, 80))
                p.drawRoundedRect(br, 6, 6)
                indicator = QRect(br.x(), br.y() + 4, 3, br.height() - 8)
                p.setBrush(QColor(59, 130, 246))
                p.drawRoundedRect(indicator, 2, 2)
                p.setPen(QColor("#3b82f6"))
            else:
                p.setPen(QColor(Palette.TEXT))
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
        self._extract_text_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
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

    # ── 编辑模式 ─────────────────────────────────────────────

    def _show_edit_widget(self):
        image = self._crop_image()
        if not image:
            return
        self._edit_widget = EditWidget(image, self)
        self._edit_widget._on_save_cb = self._on_edit_save
        self._edit_widget._on_cancel_cb = self._on_edit_cancel
        cr = self._crop_rect
        self._edit_widget.setGeometry(cr.x(), cr.y(), cr.width(),
                                     cr.height() + EditWidget.TOOLBAR_H)
        self._edit_widget.show()
        self._edit_widget.raise_()
        self._mode = "edit"

    def _on_edit_save(self, path):
        self._edit_widget = None
        self.close()
        if path:
            self.screenshotTaken.emit(path)
        self.finished.emit()

    def _on_edit_cancel(self):
        self._edit_widget = None
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

        # 编辑模式：点击编辑区外关闭
        if (self._mode == "edit" and self._edit_widget
                and self._edit_widget.isVisible()):
            return

        # 提取弹窗：点击弹窗外关闭并回到菜单
        if (self._mode == "extract" and self._extract_popup
                and self._extract_popup.isVisible()):
            if not self._extract_popup.geometry().contains(pos):
                self._on_extract_popup_close()
            return

        if self._mode and hasattr(self, "_popup_close") and self._popup_close.contains(pos):
            self._reset_mode()
            return

        if self._show_menu:
            for i, br in enumerate(self._menu_buttons):
                if br.contains(pos):
                    self._handle_action(i)
                    return
            return

        if self._mode == "translate" and self._crop_rect and self._crop_rect.contains(pos):
            self._toggle_trans()
            return

        if self._mode:
            return

        self._selecting = True
        self._start = pos
        self._end = pos
        self._crop_rect = None
        self._show_menu = False
        self.update()

    def _toggle_trans(self):
        """翻译模式下点击覆盖层，切换原文/译文。"""
        if self._mode == "translate" and self._ocr_text:
            self._showing_trans = not self._showing_trans
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
            self._hover_idx = -1
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

            if self._auto_translate:
                # 自动翻译：跳过菜单，直接翻译
                path = self._save_to_temp()
                if path:
                    self.translateRequested.emit(path)
            else:
                self._show_menu = True
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
        if self._edit_widget:
            self._edit_widget.hide()
            self._edit_widget = None
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
        if self._edit_widget:
            self._edit_widget.hide()
            self._edit_widget = None
        self.close()
        self.finished.emit()

    def _handle_action(self, idx):
        name = self.ACTIONS[idx]
        self.setCursor(QCursor(Qt.CursorShape.WaitCursor))

        if name == "复制":
            self._show_menu = False
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
            self._show_menu = False
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
            self._show_menu = False
            path = self._save_to_temp()
            if path:
                self.extractRequested.emit(path)
        elif name == "翻译":
            if self._mode == "translate" and self._ocr_text:
                # 已有翻译结果 → 直接切换，不重新调用API
                self._showing_trans = not self._showing_trans
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                self.update()
            else:
                # 首次翻译
                self._show_menu = False
                path = self._save_to_temp()
                if path:
                    self.translateRequested.emit(path)
        elif name == "编辑":
            self._show_menu = False
            self._show_edit_widget()
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
