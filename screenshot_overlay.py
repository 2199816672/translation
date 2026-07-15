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
    QInputDialog, QColorDialog, QSlider, QLineEdit,
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
    ACTIONS = ["复制", "保存", "提取", "翻译", "编辑", "取消"]
    EDIT_ACTIONS = ["画笔", "擦除", "文字", "撤消", "保存", "取消"]
    PEN_ACTIONS = ["颜色", "返回"]
    ERASER_ACTIONS = ["返回"]
    TEXT_ACTIONS = ["颜色", "返回"]
    PEN_COLORS = ["黑", "红", "白", "蓝", "调色板", "返回"]
    TEXT_COLORS = ["黑", "红", "白", "蓝", "调色板", "返回"]
    COLORS = ["#ef4444", "#3b82f6", "#22c55e", "#eab308", "#000000", "#ffffff"]
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
        self._menu_row0_w = 0
        self._menu_row1_start = 0
        self._hover_idx = -1

        self._mode = None
        self._ocr_text = ""
        self._trans_text = ""
        self._ocr_paragraphs = []
        self._trans_paragraphs = []
        self._showing_trans = False
        self._popup_rect = QRect()
        self._popup_close = QRect()
        self._extract_popup = None
        self._extract_text_edit = None
        self._edit_widget = None
        self._edit_overlay = None
        self._edit_tool = "pen"
        self._edit_pen = False
        self._edit_drawing = False
        self._edit_last = QPoint()
        self._edit_color = QColor("#ef4444")
        self._edit_pen_w = 3
        self._edit_eraser_w = 15
        self._edit_undo = []
        self._edit_redo = []
        self._edit_menu_level = 0
        self._edit_text_items = []
        self._edit_text_input = None
        self._edit_selected_text_idx = -1
        self._edit_dragging_text = False
        self._edit_resize_corner = None
        self._edit_resize_start = QPoint()
        self._edit_drag_offset = QPoint()
        self._delete_text_btn_rect = QRect()
        self._edit_color_idx = 0
        self._size_slider = None
        self._size_label = None
        self._auto_translate = auto_translate
        self._status_text = ""

    def _ensure_size_slider(self):
        if self._size_slider is None:
            from PySide6.QtWidgets import QSlider
            self._size_slider = QSlider(Qt.Orientation.Horizontal, self)
            self._size_slider.setRange(1, 30)
            self._size_slider.setValue(self._edit_pen_w)
            self._size_slider.valueChanged.connect(self._on_size_changed)
            self._size_slider.setFixedWidth(100)
            self._size_slider.setStyleSheet("""
                QSlider { background: transparent; }
                QSlider::groove:horizontal { background: #3f3f46; height: 4px; border-radius: 2px; }
                QSlider::handle:horizontal { background: #e4e4e7; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
            """)
            self._size_label = QLabel(f"{self._edit_pen_w}px", self)
            self._size_label.setStyleSheet("color: #e4e4e7; font-size: 11px; background: transparent;")

    def _show_size_slider(self):
        self._ensure_size_slider()
        if self._size_slider and self._menu_row0_w:
            x = self._menu_x + self._menu_row0_w + 6
            y = self._menu_y + self.MENU_PAD + (self.BTN_H - 22) // 2
            self._size_slider.move(x, y)
            self._size_slider.show()
            self._size_label.move(x + 104, y - 2)
            self._size_label.show()

    def _hide_size_slider(self):
        if self._size_slider:
            self._size_slider.hide()
            self._size_label.hide()

    def _on_size_changed(self, val):
        self._edit_pen_w = val
        self._edit_eraser_w = val
        if self._size_label:
            self._size_label.setText(f"{val}px")

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
        self._status_text = ""
        self._ocr_text = text
        self._ocr_paragraphs = self._split_paragraphs(text)
        self._trans_text = ""
        self._trans_paragraphs = []
        self._showing_trans = False
        self._mode = "extract"
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self._show_extract_popup(text)

    def show_trans_result(self, text):
        self._trans_text = text
        self._trans_paragraphs = self._split_paragraphs(text)
        self._showing_trans = True
        self._mode = "translate"
        self._show_menu = True
        self._hover_idx = -1
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def show_trans_paragraphs(self, paragraphs, ocr_text=""):
        self._status_text = ""
        self._trans_paragraphs = paragraphs
        self._trans_text = "\n\n".join(paragraphs)
        if ocr_text:
            self._ocr_text = ocr_text
            self._ocr_paragraphs = self._split_paragraphs(ocr_text)
        self._showing_trans = True
        self._mode = "translate"
        self._show_menu = True
        self._hover_idx = -1
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    @staticmethod
    def _split_paragraphs(text):
        if not text or not text.strip():
            return []
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        return paras if paras else [text.strip()]

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
            if self._mode == "translate" and self._showing_trans:
                self._draw_trans_overlay(p)
            self._draw_menu(p)
        elif self._crop_rect and self._mode == "extract":
            self._draw_crop(p)
        elif self._crop_rect and self._mode == "translate":
            self._draw_crop(p)
            self._draw_trans_overlay(p)

        if self._status_text:
            self._draw_status(p)

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
        if self._edit_overlay is not None:
            scaled = self._edit_overlay.scaled(r.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(r, scaled)
            # Draw text items on top (not baked into overlay, for drag/resize)
            for i, item in enumerate(self._edit_text_items):
                ix = r.x() + item["x"] // self._scale
                iy = r.y() + item["y"] // self._scale
                font = QFont("Microsoft YaHei", item["font_size"])
                p.setFont(font)
                p.setPen(QPen(item["color"], 2))
                p.drawText(ix, iy, item["text"])
                # Selection border for selected text
                if i == self._edit_selected_text_idx:
                    local_rect = self._get_text_item_rect(item)
                    sel_rect = QRect(r.x() + local_rect.x(), r.y() + local_rect.y(),
                                     local_rect.width(), local_rect.height())
                    p.setPen(QPen(QColor(59, 130, 246, 180), 1, Qt.PenStyle.DashLine))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(sel_rect, 3, 3)
                    # Corner handles
                    cs = 8
                    hc = QColor(59, 130, 246)
                    for dx, dy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
                        cx = sel_rect.x() + dx * sel_rect.width() - cs
                        cy = sel_rect.y() + dy * sel_rect.height() - cs
                        p.fillRect(cx, cy, cs * 2, cs * 2, hc)
                    # Delete button at top-center
                    del_size = 14
                    dcx = sel_rect.center().x()
                    dcy = sel_rect.y() - del_size // 2 - 2
                    self._delete_text_btn_rect = QRect(dcx - del_size // 2, dcy, del_size, del_size)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(239, 68, 68))
                    p.drawRoundedRect(self._delete_text_btn_rect, 7, 7)
                    p.setPen(QPen(QColor(255, 255, 255), 2))
                    p.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
                    p.drawText(self._delete_text_btn_rect, Qt.AlignmentFlag.AlignCenter, "×")
        p.setPen(QPen(QColor("#ef4444"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)

    # ── 菜单 ─────────────────────────────────────────────────

    def _draw_menu(self, p):
        actions = self._current_actions()
        btn_count = len(actions)
        row0_w = btn_count * self.BTN_W + (btn_count - 1) * self.BTN_GAP + self.MENU_PAD * 2
        self._menu_row0_w = row0_w
        row_h = self.BTN_H + self.MENU_PAD * 2

        sub_actions = self._tool_actions()
        col_actions = self._color_actions()
        total_h = row_h
        if sub_actions:
            total_h += self.BTN_H + self.MENU_PAD * 2 + self.BTN_GAP
        if col_actions:
            total_h += self.BTN_H + self.MENU_PAD * 2 + self.BTN_GAP

        mx = self._crop_rect.center().x() - row0_w // 2
        my = self._crop_rect.bottom() + 10

        if my + total_h > self.height():
            my = self._crop_rect.top() - total_h - 10
        if my < 0:
            my = 10
            mx = self.width() // 2 - row0_w // 2
        if mx < 0:
            mx = 0
        if mx + row0_w > self.width():
            mx = self.width() - row0_w

        bg = QRect(mx, my, row0_w, total_h)
        self._menu_y = my
        self._menu_x = mx
        self._menu_h = total_h
        p.setPen(QPen(QColor(Palette.BORDER), 1))
        p.setBrush(QColor(22, 22, 24, 128))
        p.drawRoundedRect(bg, 10, 10)

        self._menu_buttons.clear()
        bx, by = mx + self.MENU_PAD, my + self.MENU_PAD
        color_hex = {"黑": "#000000", "红": "#ef4444", "白": "#ffffff", "蓝": "#3b82f6"}
        btn_colors = {"黑": "#000000", "红": "#ef4444", "白": "#ffffff", "蓝": "#3b82f6"}

        def _draw_btn(br, label, is_active=False, is_selected=False, is_hover=False, tooltip=""):
            if is_selected:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(34, 197, 94, 60))
                p.drawRoundedRect(br, 6, 6)
                p.setPen(QColor("#22c55e"))
            elif is_active:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(34, 197, 94, 60))
                p.drawRoundedRect(br, 6, 6)
                p.setPen(QColor("#22c55e"))
            elif is_hover:
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

        # Row 0: main menu
        for i, label in enumerate(actions):
            br = QRect(bx, by, self.BTN_W, self.BTN_H)
            self._menu_buttons.append(br)
            is_active = (self._edit_overlay is not None and self._edit_pen and (
                (label == "画笔" and self._edit_tool == "pen") or
                (label == "擦除" and self._edit_tool == "eraser") or
                (label == "文字" and self._edit_tool == "text")
            ))
            _draw_btn(br, label, is_active=is_active, is_hover=(i == self._hover_idx))
            bx += self.BTN_W + self.BTN_GAP

        self._menu_row1_start = len(actions)

        def _center_row(row_actions, y_offset):
            cnt = len(row_actions)
            w = cnt * self.BTN_W + (cnt - 1) * self.BTN_GAP + self.MENU_PAD * 2
            x0 = mx + (row0_w - w) // 2 + self.MENU_PAD
            y0 = my + y_offset
            return x0, y0

        def _draw_label_with_color(br, label):
            """Draw a button with a color dot next to its text."""
            p.setFont(QFont("Microsoft YaHei", 11))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, label)
            # color indicator dot below
            cname = label.replace("●", "")
            if cname in btn_colors:
                dot = QRect(br.center().x() - 4, br.bottom() - 10, 8, 8)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(btn_colors[cname]))
                p.drawRoundedRect(dot, 4, 4)

        # Row 1: tool options
        if sub_actions:
            bx2, by2 = _center_row(sub_actions, self.MENU_PAD + self.BTN_H + self.BTN_GAP)
            for i, label in enumerate(sub_actions):
                br = QRect(bx2, by2, self.BTN_W, self.BTN_H)
                self._menu_buttons.append(br)
                idx = self._menu_row1_start + i
                is_color_label = (label == "颜色")
                is_hover = (idx == self._hover_idx)
                if is_color_label:
                    # Show current color name
                    cname = {0xff4444: "红", 0: "黑", 0xffffff: "白", 0x3b82f6: "蓝"}.get(
                        self._edit_color.rgb() & 0xffffff, "红")
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(34, 197, 94, 60))
                    p.drawRoundedRect(br, 6, 6)
                    p.setPen(QColor("#22c55e"))
                    p.setFont(QFont("Microsoft YaHei", 11))
                    p.drawText(br, Qt.AlignmentFlag.AlignCenter, cname)
                else:
                    _draw_btn(br, label, is_hover=is_hover)
                bx2 += self.BTN_W + self.BTN_GAP

        self._menu_row2_start = self._menu_row1_start + (len(sub_actions) if sub_actions else 0)

        # Row 2: color picker
        if col_actions:
            bx3, by3 = _center_row(col_actions, self.MENU_PAD + (self.BTN_H + self.BTN_GAP) * 2)
            for i, label in enumerate(col_actions):
                br = QRect(bx3, by3, self.BTN_W, self.BTN_H)
                self._menu_buttons.append(br)
                idx = self._menu_row2_start + i
                cname = label
                is_sel = cname in btn_colors and QColor(btn_colors[cname]) == self._edit_color
                is_hover = (idx == self._hover_idx)
                if cname in btn_colors:
                    if is_sel:
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QColor(34, 197, 94, 60))
                        p.drawRoundedRect(br, 6, 6)
                        p.setPen(QColor("#22c55e"))
                    elif is_hover:
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
                    p.drawText(br, Qt.AlignmentFlag.AlignCenter, cname)
                else:
                    _draw_btn(br, label, is_hover=is_hover)
                bx3 += self.BTN_W + self.BTN_GAP

        # Position slider to right of menu if tool is active
        if self._edit_overlay is not None and self._edit_menu_level >= 1 and self._edit_tool in ("pen", "eraser"):
            self._show_size_slider()
        else:
            self._hide_size_slider()

    # ── 翻译叠加 ─────────────────────────────────────────────

    def _draw_trans_overlay(self, p):
        cr = self._crop_rect
        if self._showing_trans:
            paragraphs = self._trans_paragraphs
        else:
            paragraphs = self._ocr_paragraphs if self._ocr_paragraphs else (
                [self._ocr_text] if self._ocr_text else [])
        if not paragraphs:
            return

        font = QFont("Microsoft YaHei", 11)
        fm = QFontMetrics(font)
        line_h = fm.height()
        pad = 8
        gap = 6
        box_w = cr.width()

        total_h = 0
        para_layouts = []
        for para in paragraphs:
            lines = []
            for raw_line in para.split("\n"):
                if not raw_line.strip():
                    lines.append("")
                    continue
                wrapped = textwrap.wrap(raw_line, width=40) or [""]
                lines.extend(wrapped)
            text_h = len(lines) * line_h
            box_h = text_h + pad * 2
            para_layouts.append((lines, box_h))
            total_h += box_h + gap
        total_h -= gap

        max_h = max(cr.height(), self.height() * 0.6)
        if total_h > max_h:
            scale = max_h / total_h
            adjusted = []
            for lines, box_h in para_layouts:
                adjusted.append((lines, int(box_h * scale)))
            para_layouts = adjusted
            total_h = max_h

        y = cr.top()
        for lines, box_h in para_layouts:
            if y + box_h > self.height():
                break
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 180))
            p.drawRoundedRect(QRect(cr.left(), y, box_w, box_h), 6, 6)
            p.setPen(QColor(Palette.TEXT))
            p.setFont(font)
            ty = y + pad + fm.ascent()
            for line in lines:
                p.drawText(cr.left() + pad, ty, line)
                ty += line_h
            y += box_h + gap

        close_label = "×"
        cw = 20
        cr2 = QRect(cr.right() - cw - 4, cr.top() + 2, cw, 22)
        p.setPen(QColor(Palette.TEXT_MUTED))
        p.setFont(QFont("Microsoft YaHei", 12))
        p.drawText(cr2, Qt.AlignmentFlag.AlignCenter, close_label)
        self._popup_close = cr2

    def _draw_status(self, p):
        if not self._status_text:
            return
        font = QFont("Microsoft YaHei", 13)
        fm = QFontMetrics(font)
        text = self._status_text
        tw = fm.horizontalAdvance(text)
        pad = 14
        bw = tw + pad * 2
        bh = 40
        cr = self._crop_rect
        if not cr:
            return
        bx = cr.center().x() - bw // 2
        by = cr.center().y() - bh // 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 180))
        p.drawRoundedRect(QRect(bx, by, bw, bh), 10, 10)
        p.setPen(QColor(Palette.TEXT))
        p.setFont(font)
        p.drawText(QRect(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, text)

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
                if self._edit_text_input:
                    self._cancel_text_input()
                elif self._edit_overlay is not None:
                    pass  # edit mode: right-click does nothing
                elif self._mode:
                    self._reset_mode()
                else:
                    self._cancel()
            return

        pos = event.pos()

        # 编辑模式：绘制（仅在选区内部）
        if self._edit_overlay is not None and self._edit_pen:
            cr = self._crop_rect
            if cr and cr.contains(pos):
                local = QPoint(pos.x() - cr.x(), pos.y() - cr.y())
                s = self._scale
                lp = QPoint(int(local.x() * s), int(local.y() * s))
                if self._edit_tool == "text":
                    # Check delete button first
                    if self._edit_selected_text_idx >= 0 and self._delete_text_btn_rect.contains(pos):
                        self._edit_text_items.pop(self._edit_selected_text_idx)
                        self._edit_selected_text_idx = -1
                        self._edit_dragging_text = False
                        self._edit_resize_corner = None
                        self.update()
                        return
                    # Check if clicking on existing text item (select for move/resize)
                    hit = -1
                    cs = 8
                    # Check corner handles FIRST (extend outside text rect)
                    corner_hit = -1
                    corner_name = None
                    corner_pos = None
                    for i in reversed(range(len(self._edit_text_items))):
                        item = self._edit_text_items[i]
                        tr = self._get_text_item_rect(item)
                        for cname, (cdx, cdy) in [("tl", (0, 0)), ("tr", (1, 0)), ("bl", (0, 1)), ("br", (1, 1))]:
                            hr = QRect(tr.x() + cdx * tr.width() - cs, tr.y() + cdy * tr.height() - cs, cs*2, cs*2)
                            if hr.contains(local):
                                corner_hit = i
                                corner_name = cname
                                corner_pos = QPoint(local)
                                break
                        if corner_hit >= 0:
                            break
                    if corner_hit >= 0:
                        item = self._edit_text_items[corner_hit]
                        self._edit_selected_text_idx = corner_hit
                        self._edit_dragging_text = False
                        self._edit_resize_corner = corner_name
                        self._edit_resize_start = corner_pos
                        self._edit_resize_font_size = item["font_size"]
                    else:
                        # Check body rect
                        hit = -1
                        for i in reversed(range(len(self._edit_text_items))):
                            item = self._edit_text_items[i]
                            rect = self._get_text_item_rect(item)
                            if rect.contains(local):
                                hit = i
                                break
                        if hit >= 0:
                            self._edit_selected_text_idx = hit
                            self._edit_dragging_text = True
                            self._edit_resize_corner = None
                            self._edit_drag_offset = QPoint(
                                local.x() - self._edit_text_items[hit]["x"] // s,
                                local.y() - self._edit_text_items[hit]["y"] // s)
                        else:
                            self._edit_selected_text_idx = -1
                            self._edit_dragging_text = False
                            self._edit_resize_corner = None
                            self._delete_text_btn_rect = QRect()
                else:
                    self._edit_drawing = True
                    self._edit_undo.append(self._edit_overlay.copy())
                    self._edit_last = lp
                return

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

    def _set_edit_cursor(self):
        """Set custom cursor showing brush circle when over crop area in edit mode."""
        if self._edit_overlay is None or not self._edit_pen:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            return
        if self._edit_tool == "text":
            self.setCursor(QCursor(Qt.CursorShape.IBeamCursor))
            return
        w = self._edit_tool == "eraser" and self._edit_eraser_w or self._edit_pen_w
        d = max(w * 2 + 4, 10)
        pix = QPixmap(d, d)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = cy = d // 2
        p.setPen(QPen(QColor(255, 255, 255, 180), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPoint(cx, cy), w, w)
        # center crosshair
        p.setPen(QPen(QColor(255, 255, 255, 100), 1))
        p.drawLine(cx - 3, cy, cx + 3, cy)
        p.drawLine(cx, cy - 3, cx, cy + 3)
        p.end()
        self.setCursor(QCursor(pix, cx, cy))

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self._edit_overlay is not None and self._edit_pen:
            if self._edit_drawing:
                cr = self._crop_rect
                if cr and cr.contains(pos):
                    local = QPoint(pos.x() - cr.x(), pos.y() - cr.y())
                    s = self._scale
                    cur = QPoint(int(local.x() * s), int(local.y() * s))
                    p = QPainter(self._edit_overlay)
                    p.setRenderHint(QPainter.RenderHint.Antialiasing)
                    if self._edit_tool == "eraser":
                        pen = QPen(QColor(0, 0, 0, 0), self._edit_eraser_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                        p.setPen(pen)
                    else:
                        pen = QPen(self._edit_color, self._edit_pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                        p.setPen(pen)
                    p.drawLine(self._edit_last, cur)
                    p.end()
                    self._edit_last = cur
                    self.update()
                return
        # 文字模式下光标提示
        if self._edit_overlay is not None and self._edit_tool == "text":
            cr = self._crop_rect
            if cr and cr.contains(pos):
                local = QPoint(pos.x() - cr.x(), pos.y() - cr.y())
                cursor_set = False
                if self._edit_selected_text_idx >= 0:
                    tr = self._get_text_item_rect(self._edit_text_items[self._edit_selected_text_idx])
                    cs = 8
                    cursor_map = {
                        (0, 0): (QRect(tr.x()-cs, tr.y()-cs, cs*2, cs*2), Qt.CursorShape.SizeFDiagCursor),
                        (1, 0): (QRect(tr.x()+tr.width()-cs, tr.y()-cs, cs*2, cs*2), Qt.CursorShape.SizeBDiagCursor),
                        (0, 1): (QRect(tr.x()-cs, tr.y()+tr.height()-cs, cs*2, cs*2), Qt.CursorShape.SizeBDiagCursor),
                        (1, 1): (QRect(tr.x()+tr.width()-cs, tr.y()+tr.height()-cs, cs*2, cs*2), Qt.CursorShape.SizeFDiagCursor),
                    }
                    for (dx, dy), (hr, cur) in cursor_map.items():
                        if hr.contains(local):
                            self.setCursor(QCursor(cur))
                            cursor_set = True
                            break
                    if not cursor_set:
                        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
                        cursor_set = True
                if not cursor_set:
                    for item in reversed(self._edit_text_items):
                        if self._get_text_item_rect(item).contains(local):
                            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
                            cursor_set = True
                            break
                if not cursor_set:
                    self.setCursor(QCursor(Qt.CursorShape.IBeamCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        # 文本拖拽/缩放手柄
        if self._edit_selected_text_idx >= 0:
            cr = self._crop_rect
            if cr and cr.contains(pos):
                s = self._scale
                local = QPoint(pos.x() - cr.x(), pos.y() - cr.y())
                item = self._edit_text_items[self._edit_selected_text_idx]
                if self._edit_resize_corner:
                    dx = local.x() - self._edit_resize_start.x()
                    dy = local.y() - self._edit_resize_start.y()
                    corner_sign = {"tl": (-1, -1), "tr": (1, -1), "bl": (-1, 1), "br": (1, 1)}
                    sx, sy = corner_sign.get(self._edit_resize_corner, (1, 1))
                    change = int((sx * dx + sy * dy) / 3)
                    new_fs = max(8, min(72, self._edit_resize_font_size + change))
                    if new_fs != item["font_size"]:
                        old_fm = QFontMetrics(QFont("Microsoft YaHei", item["font_size"]))
                        old_tw = old_fm.horizontalAdvance(item["text"])
                        old_th = old_fm.height()
                        item["font_size"] = new_fs
                        new_fm = QFontMetrics(QFont("Microsoft YaHei", new_fs))
                        new_tw = new_fm.horizontalAdvance(item["text"])
                        new_th = new_fm.height()
                        tw_diff = new_tw - old_tw
                        th_diff = new_th - old_th
                        # Keep opposite corner fixed
                        if self._edit_resize_corner == "tl":
                            item["x"] -= int(tw_diff * s)
                        elif self._edit_resize_corner == "br":
                            item["y"] += int(th_diff * s)
                        elif self._edit_resize_corner == "bl":
                            item["x"] -= int(tw_diff * s)
                            item["y"] += int(th_diff * s)
                        # tr: anchor BL (baseline-left), no adjustment needed
                elif self._edit_dragging_text:
                    item["x"] = int((local.x() - self._edit_drag_offset.x()) * s)
                    item["y"] = int((local.y() - self._edit_drag_offset.y()) * s)
                self.update()
            return
        # 编辑模式下按钮高亮 + 光标
        if self._show_menu:
            if self._edit_overlay is not None and self._edit_pen:
                cr = self._crop_rect
                in_crop = cr and cr.contains(pos)
                self._set_edit_cursor() if in_crop else self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
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
        if self._edit_dragging_text:
            self._edit_dragging_text = False
        if self._edit_resize_corner:
            self._edit_resize_corner = None
        if self._edit_drawing:
            self._edit_drawing = False
            return
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
                self._status_text = "正在翻译..."
                self.update()
                path = self._save_to_temp()
                if path:
                    self.translateRequested.emit(path)
            else:
                self._show_menu = True
                self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._edit_overlay is not None and self._edit_pen and self._edit_tool == "text":
            cr = self._crop_rect
            if cr and cr.contains(event.pos()):
                local = QPoint(event.pos().x() - cr.x(), event.pos().y() - cr.y())
                for i in reversed(range(len(self._edit_text_items))):
                    item = self._edit_text_items[i]
                    tr = self._get_text_item_rect(item)
                    padded = tr.adjusted(-8, -8, 8, 8)
                    if padded.contains(local):
                        self._edit_selected_text_idx = -1
                        self._edit_dragging_text = False
                        self._edit_resize_corner = None
                        self._edit_text_items.pop(i)
                        self._start_text_input(local, item["text"], item["font_size"])
                        return
                self._start_text_input(local)

    def wheelEvent(self, event):
        if self._edit_overlay is not None and self._edit_tool == "text" and self._edit_text_items:
            cr = self._crop_rect
            pos = event.pos()
            if cr and cr.contains(pos):
                local = QPoint(pos.x() - cr.x(), pos.y() - cr.y())
                for item in reversed(self._edit_text_items):
                    if self._get_text_item_rect(item).contains(local):
                        fs = max(8, min(72, item["font_size"] + (event.angleDelta().y() // 120)))
                        item["font_size"] = fs
                        self.update()
                        break
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if self._edit_text_input:
            if event.key() == Qt.Key.Key_Escape:
                self._cancel_text_input()
            return
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
        self._exit_edit_mode()
        self._mode = None
        self._ocr_text = ""
        self._trans_text = ""
        self._ocr_paragraphs = []
        self._trans_paragraphs = []
        self._showing_trans = False
        self._status_text = ""
        self._show_menu = True
        self._hover_idx = -1
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def _exit_edit_mode(self):
        self._cancel_text_input()
        self._edit_overlay = None
        self._edit_pen = False
        self._edit_drawing = False
        self._edit_undo.clear()
        self._edit_redo.clear()
        self._edit_menu_level = 0
        self._edit_text_items.clear()
        self._edit_selected_text_idx = -1
        self._edit_dragging_text = False
        self._edit_resize_corner = None
        self._hide_size_slider()
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def _cancel(self):
        self._hide_extract_popup()
        if self._edit_widget:
            self._edit_widget.hide()
            self._edit_widget = None
        self.close()
        self.finished.emit()

    def _do_edit_save(self):
        if not self._edit_overlay:
            return
        cr = self._crop_rect
        crop_pix = self._raw_pixmap.copy(self._logical_to_pixmap(cr))
        painter = QPainter(crop_pix)
        painter.drawPixmap(0, 0, self._edit_overlay)
        painter.end()
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存截图", "标注.png",
            "PNG 图片 (*.png);;JPG 图片 (*.jpg)")
        if save_path:
            crop_pix.save(save_path)
        self._exit_edit_mode()
        self.update()

    def _start_text_input(self, local, initial_text="", font_size=18):
        """Create inline text input at the given position (relative to crop area top-left)."""
        if self._edit_text_input:
            self._commit_text_input()
        cr = self._crop_rect
        self._edit_text_pos = QPoint(local.x(), local.y())
        self._edit_text_font_size = font_size
        edit = QLineEdit(self)
        edit.setPlaceholderText("输入文字，回车确认")
        edit.setStyleSheet(f"""
            QLineEdit {{
                background: transparent; color: {self._edit_color.name()};
                border: 1px dashed rgba(255,255,255,180);
                font: {font_size}px 'Microsoft YaHei'; padding: 2px 4px;
            }}
        """)
        edit.setFixedSize(200, 32)
        if initial_text:
            edit.setText(initial_text)
            edit.selectAll()
        x = cr.x() + local.x()
        y = cr.y() + local.y()
        if x + 200 > self.width():
            x = self.width() - 210
        if y + 32 > self.height():
            y = self.height() - 42
        edit.move(x, y)
        edit.show()
        edit.setFocus()
        edit.editingFinished.connect(self._commit_text_input)
        self._edit_text_input = edit

    def _commit_text_input(self):
        if not self._edit_text_input:
            return
        text = self._edit_text_input.text().strip()
        pos = self._edit_text_pos
        self._edit_text_input.hide()
        self._edit_text_input.deleteLater()
        self._edit_text_input = None
        if text:
            s = self._scale
            item = {
                "text": text,
                "x": int(pos.x() * s),
                "y": int(pos.y() * s),
                "font_size": self._edit_text_font_size,
                "color": QColor(self._edit_color),
            }
            self._edit_text_items.append(item)
            self.update()

    def _cancel_text_input(self):
        if self._edit_text_input:
            self._edit_text_input.hide()
            self._edit_text_input.deleteLater()
        self._edit_text_input = None
        self._edit_text_font_size = 18

    def _draw_text_item(self, item):
        """Draw a single text item onto the edit overlay."""
        p = QPainter(self._edit_overlay)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(item["color"], 2))
        p.setFont(QFont("Microsoft YaHei", item["font_size"]))
        p.drawText(item["x"], item["y"], item["text"])
        p.end()

    def _get_text_item_rect(self, item):
        """Get bounding rect of a text item relative to crop area."""
        s = self._scale
        x = item["x"] // s
        y = item["y"] // s
        fm = QFontMetrics(QFont("Microsoft YaHei", item["font_size"]))
        tw = fm.horizontalAdvance(item["text"])
        th = fm.height()
        return QRect(x, y - th, tw + 4, th + 4)

    def _current_actions(self):
        if self._edit_overlay is not None:
            return list(self.EDIT_ACTIONS)
        actions = list(self.ACTIONS)
        if self._mode == "translate" and self._ocr_text:
            actions[3] = "原文" if self._showing_trans else "翻译"
        return actions

    def _tool_actions(self):
        if self._edit_menu_level < 1 or self._edit_overlay is None:
            return None
        if self._edit_tool == "eraser":
            return list(self.ERASER_ACTIONS)
        elif self._edit_tool == "text":
            return list(self.TEXT_ACTIONS)
        return list(self.PEN_ACTIONS)

    def _color_actions(self):
        if self._edit_menu_level < 2 or self._edit_overlay is None:
            return None
        if self._edit_tool == "text":
            return list(self.TEXT_COLORS)
        return list(self.PEN_COLORS)

    def _handle_action(self, idx):
        # 区分主菜单(row 0)、工具选项(row 1)、调色板(row 2)
        if idx < self._menu_row1_start:
            name = self._current_actions()[idx]
        elif idx < self._menu_row2_start:
            sub = self._tool_actions()
            if sub:
                name = sub[idx - self._menu_row1_start]
            else:
                return
        else:
            col = self._color_actions()
            if col:
                name = col[idx - self._menu_row2_start]
            else:
                return
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
            if self._edit_overlay is not None:
                self._do_edit_save()
            else:
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
        elif name == "翻译" or name == "原文":
            if self._mode == "translate" and self._ocr_text:
                self._showing_trans = not self._showing_trans
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                self.update()
            else:
                self._show_menu = False
                self._status_text = "正在翻译..."
                self.update()
                path = self._save_to_temp()
                if path:
                    self.translateRequested.emit(path)
        elif name == "编辑":
            self._show_menu = True
            cr = self._crop_rect
            if cr:
                crop_pix = self._raw_pixmap.copy(self._logical_to_pixmap(cr))
                self._edit_overlay = QPixmap(crop_pix.size())
                self._edit_overlay.fill(Qt.GlobalColor.transparent)
                self._edit_undo.clear()
                self._edit_redo.clear()
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self.update()
        elif name == "画笔":
            self._edit_tool = "pen"
            self._edit_pen = True
            self._edit_menu_level = 1
            self._show_size_slider()
            self._set_edit_cursor()
            self.update()
        elif name == "擦除":
            self._edit_tool = "eraser"
            self._edit_pen = True
            self._edit_menu_level = 1
            self._show_size_slider()
            self._set_edit_cursor()
            self.update()
        elif name == "文字":
            self._edit_tool = "text"
            self._edit_pen = True
            self._edit_menu_level = 1
            self._hide_size_slider()
            self.setCursor(QCursor(Qt.CursorShape.IBeamCursor))
            self.update()
        elif name == "颜色":
            self._edit_menu_level = 2
            self._hide_size_slider()
            self.update()
        elif name in ("黑", "红", "白", "蓝", "调色板"):
            color_map = {"黑": "#000000", "红": "#ef4444", "白": "#ffffff", "蓝": "#3b82f6"}
            if name == "调色板":
                c = QColorDialog.getColor(self._edit_color, self, "选择颜色")
                if c.isValid():
                    self._edit_color = c
            elif name in color_map:
                self._edit_color = QColor(color_map[name])
            self._edit_menu_level = 1
            if self._edit_tool in ("pen", "eraser"):
                self._show_size_slider()
            self.update()
        elif name == "返回":
            if self._edit_menu_level >= 2:
                self._edit_menu_level -= 1
                if self._edit_tool in ("pen", "eraser"):
                    self._show_size_slider()
            else:
                self._edit_menu_level = 0
                self._hide_size_slider()
            self.update()
        elif name == "撤消":
            if self._edit_undo:
                self._edit_redo.append(self._edit_overlay.copy())
                self._edit_overlay = self._edit_undo.pop()
            self.update()
        elif name == "取消":
            if self._edit_overlay is not None:
                self._exit_edit_mode()
                self.update()
            else:
                self._cancel()
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
