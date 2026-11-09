#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PySide6 全屏截图覆盖层：区域选择 + 提取文字/翻译（类微信交互）。"""
import os
import time
import uuid
import textwrap

from PySide6.QtCore import Qt, QRect, QRectF, QPoint, Signal, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QPixmap, QPen, QFont, QFontMetrics, QCursor, QTextOption,
    QPainterPath,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QFileDialog,
    QFrame, QTextEdit, QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
    QColorDialog, QSlider, QLineEdit,
)

from theme import Palette
from app_paths import screenshots_dir


def _new_capture_path():
    """每次截图用独立文件名，避免区域截图 / 全屏截图共用固定路径互相覆盖。

    路径统一走 app_paths：PyInstaller 打包后 __file__ 指向 _internal，写进去的
    截图会在升级 / 退出时丢失，只读安装目录还会直接 PermissionError。
    """
    d = str(screenshots_dir())
    os.makedirs(d, exist_ok=True)
    name = (f"capture_{time.strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:8]}.png")
    # 只保留最近 20 张临时截图，避免长期运行后目录堆积
    try:
        olds = sorted(
            (os.path.join(d, f) for f in os.listdir(d)
             if f.startswith("capture_") and f.endswith(".png")),
            key=os.path.getmtime)
        for path in olds[:-19]:
            try:
                os.remove(path)
            except OSError:
                pass
    except OSError:
        pass
    return os.path.join(d, name)


class ScreenshotOverlay(QWidget):
    """全屏覆盖层：选区 → 菜单 → 提取(浮动文字框) / 翻译(叠加译文，可切换)。"""

    screenshotTaken = Signal(str)
    extractRequested = Signal(str)
    translateRequested = Signal(str)
    polishRequested = Signal(str, str)  # (kind, text)
    regionSelected = Signal(QRect)      # 实时翻译：选区（全局坐标）
    finished = Signal()

    def __init__(self, auto_translate=False, live_select=False, parent=None):
        super().__init__(parent)
        self._live_select = live_select
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
        self._scale_y = 1.0

        self._crop_rect = None
        self._show_menu = False
        self._menu = None
        self._menu_sig = None
        self._menu_row1 = None
        self._menu_row2 = None
        self._row1_layout = None
        self._row2_layout = None

        self._mode = None
        self._ocr_text = ""
        self._trans_text = ""
        self._ocr_paragraphs = []
        self._trans_paragraphs = []
        # [{text, trans, x, y, w, h}]，x/y/w/h 是相对裁剪图的 0~1 比例
        self._trans_blocks = []
        self._showing_trans = False
        self._popup_rect = QRect()
        self._popup_close = QRect()
        self._extract_popup = None
        self._extract_text_edit = None
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
        self._edit_text_items = []
        self._edit_text_input = None
        self._edit_text_replace_idx = -1
        self._edit_hide_item_idx = -1
        self._text_box_active = False
        self._text_box_start = QPoint()
        self._text_box_end = QPoint()
        self._edit_selected_text_idx = -1
        self._edit_dragging_text = False
        self._edit_resize_corner = None
        self._edit_resize_font_size = 18
        self._edit_resize_start = QPoint()
        self._edit_drag_offset = QPoint()
        self._delete_text_btn_rect = QRect()
        self._edit_color_idx = 0
        self._edit_text_pos = QPoint()
        self._edit_text_font_size = 18
        self._size_slider = None
        self._size_label = None
        self._auto_translate = auto_translate
        # 选区拖拽 / 缩放（选定后可再调整）
        self._crop_drag_mode = ""
        self._crop_drag_start_rect = QRect()
        self._crop_drag_start_pos = QPoint()
        self._status_text = ""

        # 延迟同步菜单 / 清除提示用定时器（随控件销毁，避免关闭后的残留回调）
        self._menu_timer = QTimer(self)
        self._menu_timer.setSingleShot(True)
        self._menu_timer.setInterval(0)
        self._menu_timer.timeout.connect(self._sync_menu)
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.setInterval(1200)
        self._status_timer.timeout.connect(self._clear_status)

    # ── 截图启动 ──────────────────────────────────────────────

    def start_capture(self):
        # 多显示器：优先在鼠标所在屏幕上截图
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if not screen:
            return

        # 复用时先清掉上一轮残留状态，避免脏数据污染本轮结果（_live_select /
        # _auto_translate 等构造参数保留，供实时截屏模式继续生效）
        self._crop_rect = None
        self._mode = None
        self._show_menu = False
        self._ocr_text = ""
        self._trans_blocks = []
        self._showing_trans = False
        self._edit_overlay = None
        self._popup_close = QRect()
        self._selecting = False

        geo = screen.geometry()
        # grabWindow(0) 抓取的是该屏幕本身；不要传全局坐标（多显示器会错位）
        self._raw_pixmap = screen.grabWindow(0)

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
        """根据实际 pixmap 和 widget 尺寸计算缩放比（x/y 分别计算）。"""
        if self._raw_pixmap and self.width() > 0 and self.height() > 0:
            pw, ph = self._raw_pixmap.width(), self._raw_pixmap.height()
            ww, wh = self.width(), self.height()
            self._scale = pw / ww if ww > 0 else 1.0
            self._scale_y = ph / wh if wh > 0 else self._scale

    def _clear_status(self):
        if self._status_text:
            self._status_text = ""
            self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._calc_scale()

    # ── 选区调整（移动 / 缩放）──────────────────────────────

    HANDLE_R = 7          # 上边中心小圆点半径
    EDGE_MARGIN = 8       # 边缘缩放热区

    @classmethod
    def _edge_at(cls, pos, rect):
        """返回命中的边/角（l/r/t/b 组合），不在附近返回空串。"""
        near = rect.adjusted(-cls.EDGE_MARGIN, -cls.EDGE_MARGIN,
                             cls.EDGE_MARGIN, cls.EDGE_MARGIN)
        if not near.contains(pos):
            return ""
        # 先纵后横，与 resize_util 的四角命名 tl/tr/bl/br 保持一致
        edge = ""
        if abs(pos.y() - rect.top()) <= cls.EDGE_MARGIN:
            edge += "t"
        elif abs(pos.y() - rect.bottom()) <= cls.EDGE_MARGIN:
            edge += "b"
        if abs(pos.x() - rect.left()) <= cls.EDGE_MARGIN:
            edge += "l"
        elif abs(pos.x() - rect.right()) <= cls.EDGE_MARGIN:
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
        return Qt.CursorShape.SizeAllCursor

    def _crop_hit(self, pos):
        """命中测试：上边小圆点/内部=移动，边缘/四角=缩放。"""
        cr = self._crop_rect
        if cr is None or self._mode or self._edit_overlay is not None:
            return ""
        handle = QPoint(cr.center().x(), cr.top())
        if (QPoint(pos) - handle).manhattanLength() <= self.HANDLE_R + 5:
            return "move"
        edge = self._edge_at(pos, cr)
        if edge:
            return edge
        if cr.contains(pos):
            return "move"
        return ""

    def _apply_crop_drag(self, delta):
        """根据拖拽方向实时更新选区（四角等比、单边自由，限制在屏幕内）。"""
        from resize_util import resize_rect
        self._crop_rect = resize_rect(
            self._crop_drag_start_rect, self._crop_drag_mode, delta,
            min_w=20, min_h=20, bounds=self.rect())
        if self._menu is not None and self._menu.isVisible():
            self._position_menu()
        self.update()

    # ── 坐标转换 ──────────────────────────────────────────────

    def _logical_to_pixmap(self, rect):
        """逻辑像素矩形 → pixmap 像素矩形（x/y 分别缩放并裁剪到图内）。"""
        sx = self._scale or 1.0
        sy = self._scale_y or sx
        mapped = QRect(int(rect.x() * sx), int(rect.y() * sy),
                       int(rect.width() * sx), int(rect.height() * sy))
        if self._raw_pixmap:
            mapped = mapped.intersected(self._raw_pixmap.rect())
        return mapped

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
        self._trans_blocks = []
        self._trans_text = text
        self._trans_paragraphs = self._split_paragraphs(text)
        self._showing_trans = True
        self._mode = "translate"
        self._show_menu = True
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def show_trans_paragraphs(self, paragraphs, ocr_text=""):
        self._status_text = ""
        self._trans_blocks = []
        self._trans_paragraphs = paragraphs
        self._trans_text = "\n\n".join(paragraphs)
        if ocr_text:
            self._ocr_text = ocr_text
            self._ocr_paragraphs = self._split_paragraphs(ocr_text)
        self._showing_trans = True
        self._mode = "translate"
        self._show_menu = True
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def show_trans_blocks(self, blocks, ocr_text=""):
        """译文按 OCR 句子块的原位置逐块显示。

        blocks: [{text, trans, x, y, w, h}]，坐标为相对裁剪图的 0~1 比例。
        """
        self._status_text = ""
        self._trans_blocks = [dict(b) for b in (blocks or [])]
        self._trans_paragraphs = [
            str(b.get("trans", "")) for b in self._trans_blocks]
        self._trans_text = "\n\n".join(self._trans_paragraphs)
        if ocr_text:
            self._ocr_text = ocr_text
            self._ocr_paragraphs = self._split_paragraphs(ocr_text)
        self._showing_trans = True
        self._mode = "translate"
        self._show_menu = True
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def show_error(self, msg):
        """后台识别/翻译失败：清除等待提示并恢复菜单（错误详情在主窗口状态栏）。"""
        self._status_text = ""
        self._show_menu = True
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    @staticmethod
    def _split_paragraphs(text):
        """按自然段切分：合并被屏幕宽度拆开的硬换行（见 text_segment）。"""
        from text_segment import split_paragraphs
        if not text or not text.strip():
            return []
        paras = split_paragraphs(text)
        return paras if paras else [text.strip()]

    # ── 绘制 ─────────────────────────────────────────────────

    def paintEvent(self, event):
        if self._raw_pixmap is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        target = QRect(0, 0, self.width(), self.height())
        p.drawPixmap(target, self._raw_pixmap)

        if not self._crop_rect and (self._selecting or self._live_select):
            self._draw_selecting(p)
        elif self._crop_rect:
            self._draw_crop(p)
            # 翻译模式下无论显示「译文」还是「原文」都绘制叠加层；
            # _draw_trans_blocks / _draw_trans_stacked 内部已按 _showing_trans
            # 决定画译文还是原文，否则切回原文会什么都不显示。
            if self._mode == "translate":
                self._draw_trans_overlay(p)

        if self._status_text:
            self._draw_status(p)

        p.end()
        # 菜单是真实控件：状态变化后延迟同步，避免在绘制过程中重建
        self._menu_timer.start()

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
        hint_font = QFont("Microsoft YaHei")
        hint_font.setPixelSize(15)
        p.setFont(hint_font)
        if self._live_select:
            hint = "拖动框选要实时翻译的区域 | ESC/右键 取消"
        else:
            hint = "拖动鼠标选择区域 | ESC/右键 取消"
        tw = p.fontMetrics().horizontalAdvance(hint)
        hx = self.width() // 2 - tw // 2
        hy = self.height() - 50
        p.fillRect(hx - 12, hy - 20, tw + 24, 30, QColor(0, 0, 0, 160))
        p.drawText(hx, hy, hint)

    def _draw_crop(self, p):
        r = self._crop_rect
        # 选区外压暗、选区内保持原亮度：松手前后亮度一致，避免视觉跳变
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        path = QPainterPath()
        path.addRect(QRectF(0, 0, self.width(), self.height()))
        path.addRect(QRectF(r))
        p.drawPath(path)
        if self._edit_overlay is not None:
            scaled = self._edit_overlay.scaled(r.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(r, scaled)
            # Draw text items on top (not baked into overlay, for drag/resize)
            for i, item in enumerate(self._edit_text_items):
                if i == self._edit_hide_item_idx:
                    continue
                sx = self._scale or 1.0
                sy = self._scale_y or sx
                ix = r.x() + int(round(item["x"] / sx))
                iy = r.y() + int(round(item["y"] / sy))
                # 字号统一用像素；text 的 (x, y) 表示左上角，与输入框位置一致
                font = self._text_font(item["font_size"])
                fm = QFontMetrics(font)
                p.setFont(font)
                p.setPen(QPen(item["color"]))
                text_rect = QRect(
                    ix, iy, fm.horizontalAdvance(item["text"]) + 4,
                    fm.height() + 2)
                p.drawText(text_rect, Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignTop, item["text"])
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
                    del_font = QFont("Microsoft YaHei")
                    del_font.setPixelSize(12)
                    del_font.setBold(True)
                    p.setFont(del_font)
                    p.drawText(self._delete_text_btn_rect, Qt.AlignmentFlag.AlignCenter, "×")
        # 文字工具框选输入区域的预览
        if self._text_box_active:
            r0 = QRect(self._text_box_start, self._text_box_end).normalized()
            box = QRect(r.x() + r0.x(), r.y() + r0.y(),
                        r0.width(), r0.height())
            p.setPen(QPen(QColor(59, 130, 246, 200), 1,
                          Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(box)

        p.setPen(QPen(QColor("#ef4444"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)

        # 未进入编辑/结果状态时，选区可调整：四角小标记 + 上边中心移动圆点
        if self._edit_overlay is None and not self._mode:
            p.setPen(QPen(QColor(59, 130, 246, 210), 2))
            length, margin = 12, 4
            corners = [
                (r.left() + margin, r.top() + margin, 1, 1),
                (r.right() - margin, r.top() + margin, -1, 1),
                (r.left() + margin, r.bottom() - margin, 1, -1),
                (r.right() - margin, r.bottom() - margin, -1, -1),
            ]
            for cx, cy, dx, dy in corners:
                p.drawLine(cx, cy, cx + dx * length, cy)
                p.drawLine(cx, cy, cx, cy + dy * length)

            center = QPoint(r.center().x(), r.top())
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.setBrush(QColor(59, 130, 246))
            p.drawEllipse(center, self.HANDLE_R, self.HANDLE_R)
            p.setPen(QPen(QColor(59, 130, 246), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(center, self.HANDLE_R + 3, self.HANDLE_R + 3)

    # ── 浮动菜单（真实控件，替代手绘）────────────────────────

    MENU_COLORS = ["#ef4444", "#3b82f6", "#22c55e", "#eab308",
                   "#000000", "#ffffff"]

    def _ensure_menu(self):
        if self._menu is not None:
            return
        self._menu = QFrame(self)
        self._menu.setObjectName("overlayMenu")
        self._menu.setAttribute(
            Qt.WidgetAttribute.WA_NoMousePropagation, True)
        self._menu.setStyleSheet(
            "#overlayMenu { background: rgba(22, 22, 24, 240);"
            f" border: 1px solid {Palette.BORDER_STRONG};"
            " border-radius: 10px; }"
            "#overlayMenu QPushButton { padding: 5px 12px;"
            " min-height: 22px; font-size: 12px; border-radius: 6px; }"
            "#overlayMenu QPushButton:checked { background: #3b82f6;"
            " border-color: #3b82f6; color: #ffffff; }"
            "#overlayMenu QPushButton[kind=\"primary\"] { background: #fafafa;"
            " border-color: #fafafa; color: #0b0b0d; font-weight: 600; }"
            "#overlayMenu QPushButton[kind=\"primary\"]:hover {"
            " background: #e4e4e7; border-color: #e4e4e7; }"
            "#overlayMenu QLabel { color: #a1a1aa; background: transparent;"
            " border: none; }")
        outer = QVBoxLayout(self._menu)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        self._menu_row1 = QWidget(self._menu)
        self._row1_layout = QHBoxLayout(self._menu_row1)
        self._row1_layout.setContentsMargins(0, 0, 0, 0)
        self._row1_layout.setSpacing(6)
        outer.addWidget(self._menu_row1)

        self._menu_row2 = QWidget(self._menu)
        self._row2_layout = QHBoxLayout(self._menu_row2)
        self._row2_layout.setContentsMargins(0, 0, 0, 0)
        self._row2_layout.setSpacing(6)
        outer.addWidget(self._menu_row2)

    def _menu_state(self):
        """菜单状态签名：状态变化时重建，避免每帧重绘菜单。"""
        return (
            self._live_select,
            self._edit_overlay is not None,
            self._edit_tool,
            self._edit_color.name(),
            self._edit_pen_w,
            self._edit_eraser_w,
            self._edit_text_font_size,
            self._mode,
            self._showing_trans,
            bool(self._ocr_text),
            bool(self._trans_text.strip()),
            bool(self._show_menu and self._crop_rect),
        )

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _menu_button(self, text, on_click, checkable=False, checked=False):
        btn = QPushButton(text, self._menu)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        if checkable:
            btn.setCheckable(True)
            btn.setChecked(checked)
        # clicked 会携带 bool 参数，这里统一忽略，避免覆盖回调默认参数
        btn.clicked.connect(lambda _checked=False, cb=on_click: cb())
        return btn

    def _rebuild_menu(self):
        self._ensure_menu()
        self._clear_layout(self._row1_layout)
        self._clear_layout(self._row2_layout)
        self._size_slider = None
        self._size_label = None
        editing = self._edit_overlay is not None

        if self._live_select:
            # 实时翻译选区确认菜单：开始 / 重新框选 / 取消
            actions = ["开始实时翻译", "重新框选", "取消"]
            for name in actions:
                btn = self._menu_button(
                    name, lambda n=name: self._do_action(n))
                if name == "开始实时翻译":
                    btn.setProperty("kind", "primary")
                self._row1_layout.addWidget(btn)
            self._menu_row2.setVisible(False)
        elif editing:
            tool_map = {"画笔": "pen", "擦除": "eraser", "文字": "text"}
            for name, tool in tool_map.items():
                self._row1_layout.addWidget(self._menu_button(
                    name, lambda n=name: self._do_action(n),
                    checkable=True, checked=(self._edit_tool == tool)))
            for name in ("撤销", "重做", "保存", "取消"):
                self._row1_layout.addWidget(self._menu_button(
                    name, lambda n=name: self._do_action(n)))

            # 第二行：颜色色块 + 笔刷大小 / 字号
            for color in self.MENU_COLORS:
                swatch = QPushButton(self._menu)
                swatch.setFixedSize(22, 22)
                swatch.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                selected = QColor(color) == self._edit_color
                swatch.setStyleSheet(
                    "QPushButton { background: %s;"
                    " border: 2px solid %s; border-radius: 5px; }"
                    % (color, "#ffffff" if selected else "#3f3f46"))
                swatch.setToolTip(color)
                swatch.clicked.connect(
                    lambda _c=False, v=color: self._set_edit_color(v))
                self._row2_layout.addWidget(swatch)
            self._row2_layout.addWidget(self._menu_button(
                "调色板", self._pick_custom_color))
            self._row2_layout.addSpacing(8)

            size_label = QLabel("大小", self._menu_row2)
            size_label.setObjectName("caption")
            self._row2_layout.addWidget(size_label)
            slider = QSlider(Qt.Orientation.Horizontal, self._menu_row2)
            slider.setFixedWidth(110)
            if self._edit_tool == "eraser":
                slider.setRange(3, 40)
                slider.setValue(self._edit_eraser_w)
            elif self._edit_tool == "text":
                slider.setRange(8, 40)
                slider.setValue(self._edit_text_font_size)
            else:
                slider.setRange(1, 20)
                slider.setValue(self._edit_pen_w)
            self._size_slider = slider
            slider.valueChanged.connect(self._on_size_changed)
            self._row2_layout.addWidget(slider)
            self._size_label = QLabel(str(slider.value()), self._menu_row2)
            self._size_label.setMinimumWidth(24)
            self._row2_layout.addWidget(self._size_label)
            self._menu_row2.setVisible(True)
        else:
            actions = ["复制", "保存", "提取", "翻译"]
            if self._mode == "translate" and self._ocr_text:
                actions[3] = "原文" if self._showing_trans else "翻译"
            if self._trans_text.strip():
                actions.append("润色")
            actions.extend(["编辑", "取消"])
            for name in actions:
                btn = self._menu_button(
                    name, lambda n=name: self._do_action(n))
                if name in ("复制", "翻译", "保存"):
                    btn.setProperty("kind", "primary")
                self._row1_layout.addWidget(btn)
            self._menu_row2.setVisible(False)

        self._menu.adjustSize()
        self._position_menu()

    def _set_edit_color(self, color):
        self._edit_color = QColor(color)
        self.update()

    def _pick_custom_color(self):
        color = QColorDialog.getColor(self._edit_color, self, "选择颜色")
        if color.isValid():
            self._edit_color = color
            self.update()

    def _on_size_changed(self, val):
        if self._edit_tool == "eraser":
            self._edit_eraser_w = val
        elif self._edit_tool == "text":
            self._edit_text_font_size = val
        else:
            self._edit_pen_w = val
        if self._size_label is not None:
            self._size_label.setText(str(val))

    def _position_menu(self):
        if self._menu is None or not self._crop_rect:
            return
        self._menu.adjustSize()
        w, h = self._menu.width(), self._menu.height()
        cr = self._crop_rect
        x = cr.center().x() - w // 2
        y = cr.bottom() + 10
        if y + h > self.height():
            y = cr.top() - h - 10
        if y < 0:
            y = 10
        x = max(0, min(x, self.width() - w))
        self._menu.move(x, y)

    def _sync_menu(self):
        """按当前状态同步菜单控件（显示/隐藏/重建/定位）。"""
        if self._raw_pixmap is None:
            return
        self._ensure_menu()
        visible = bool(self._show_menu and self._crop_rect)
        if not visible:
            if self._menu.isVisible():
                self._menu.hide()
            self._menu_sig = None
            return
        state = self._menu_state()
        if state != self._menu_sig:
            self._menu_sig = state
            self._rebuild_menu()
        else:
            self._position_menu()
        self._menu.show()
        self._menu.raise_()

    # ── 翻译叠加 ─────────────────────────────────────────────

    @staticmethod
    def _wrap_to_width(fm, text, max_w):
        """按句切分 + 按像素宽度折行（见 text_segment.wrap_sentences）。"""
        from text_segment import wrap_sentences
        return wrap_sentences(text, max_w, fm.horizontalAdvance)

    def _draw_trans_overlay(self, p):
        if self._trans_blocks:
            self._draw_trans_blocks(p)
        else:
            self._draw_trans_stacked(p)
        self._draw_trans_close(p)

    def _draw_trans_close(self, p):
        cr = self._crop_rect
        cw = 20
        cr2 = QRect(cr.right() - cw - 4, cr.top() + 2, cw, 22)
        p.setPen(QColor(Palette.TEXT_MUTED))
        close_font = QFont("Microsoft YaHei")
        close_font.setPixelSize(14)
        p.setFont(close_font)
        p.drawText(cr2, Qt.AlignmentFlag.AlignCenter, "×")
        self._popup_close = cr2

    def _draw_trans_blocks(self, p):
        """译文按【视觉段落】原位整块覆盖。

        一个自然段 = 一个大底框，完整盖住整段原文 bbox；字号从大到小
        自适应，挑能放下整段译文的最大值，再把译文按行距均匀铺开，
        避免「一条小横幅 + 小字」或大框里空一大片。先铺全部底框再写字，
        保证原文不裸露，也不会让后画的框盖掉先写的译文。
        """
        cr = self._crop_rect
        key = "trans" if self._showing_trans else "text"
        pad = 10
        bleed = 3          # OCR bbox 常略小于字形，向外扩一点确保盖住
        right = min(self.width(), cr.right() + 1)
        bottom = min(self.height(), cr.bottom() + 1)

        # 1) 原文段落 bbox（相对裁剪图的 0~1 比例）→ 屏幕矩形
        rects = []
        for b in self._trans_blocks:
            ox = cr.left() + float(b.get("x", 0)) * cr.width()
            oy = cr.top() + float(b.get("y", 0)) * cr.height()
            ow = float(b.get("w", 0)) * cr.width()
            oh = float(b.get("h", 0)) * cr.height()
            left = max(0, min(int(ox - bleed), right - 24))
            top = max(0, min(int(oy - bleed), bottom - 8))
            w = max(24, min(int(ow + bleed * 2), right - left))
            h = max(8, min(int(oh + bleed * 2), bottom - top))
            rects.append((left, top, w, h))

        # 2) 字号从大到小：挑第一个「所有段落都放得下」的字号。
        #    整段 bbox 有 200px+ 高，通常能用 18~22px，不再被压成小字。
        font = QFont("Microsoft YaHei")
        chosen = None
        for size in (22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11):
            font.setPixelSize(size)
            fm = QFontMetrics(font)
            items = []
            fits_all = True
            for b, (left, top, w, h) in zip(self._trans_blocks, rects):
                max_w = max(24, w - pad * 2)
                lines = self._wrap_to_width(
                    fm, str(b.get(key) or "").strip(), max_w) or [""]
                need = len(lines) * fm.height() + pad * 2
                items.append((lines, need, max_w))
                if need > h:
                    fits_all = False
            chosen = (QFont(font), QFontMetrics(font), items)
            if fits_all:
                break
        font, fm, items = chosen
        line_h = fm.height()

        # 3) 先铺全部底框：框高 = max(原文 bbox, 译文所需)，所有块都画。
        #    绝不能比原文矮，否则段内的英文行会裸露。
        heights = [min(max(h, need), max(8, bottom - top))
                   for (_, top, _, h), (_, need, _) in zip(rects, items)]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 238))
        for (left, top, w, h), draw_h in zip(rects, heights):
            p.drawRoundedRect(QRect(left, top, w, draw_h), 6, 6)

        # 4) 逐块写译文：行距按可用高度铺开 + 整体居中，大框不显空旷
        for (left, top, w, h), (lines, need, max_w), draw_h in zip(
                rects, items, heights):
            keep = (len(lines) if need <= draw_h else
                    max(1, int((draw_h - pad * 2) // line_h)))
            draw = list(lines[:keep])
            if len(lines) > keep and draw:
                last = draw[-1]
                while last and fm.horizontalAdvance(last + "…") > max_w:
                    last = last[:-1]
                draw[-1] = (last.rstrip("，,、 ") + "…") if last else "…"
            step = int(min(line_h * 2.0,
                           max(line_h, (draw_h - pad * 2)
                               // max(1, len(draw)))))
            used = len(draw) * step
            ty = int(top + pad + fm.ascent()
                     + max(0, (draw_h - pad * 2 - used) // 2))
            p.setPen(QColor(Palette.TEXT))
            p.setFont(font)
            for line in draw:
                p.drawText(left + pad, ty, line)
                ty += step

    def _draw_trans_stacked(self, p):
        """没有位置信息时的回退：整段按段落顺序从选区左上角往下堆。"""
        cr = self._crop_rect
        if self._showing_trans:
            paragraphs = self._trans_paragraphs
        else:
            paragraphs = self._ocr_paragraphs if self._ocr_paragraphs else (
                [self._ocr_text] if self._ocr_text else [])
        if not paragraphs:
            return

        pad = 8
        gap = 6
        box_w = cr.width()
        max_w = max(40, box_w - pad * 2)
        max_h = max(cr.height(), self.height() * 0.6)

        font = QFont("Microsoft YaHei")
        layouts = []
        for size in (14, 13, 12):
            font.setPixelSize(size)
            fm = QFontMetrics(font)
            layouts = []
            total = 0
            for para in paragraphs:
                lines = self._wrap_to_width(fm, para, max_w)
                box_h = len(lines) * fm.height() + pad * 2
                layouts.append([lines, box_h])
                total += box_h + gap
            total = max(0, total - gap)
            if total <= max_h or size == 12:
                break

        fm = QFontMetrics(font)
        line_h = fm.height()
        total_h = sum(box_h + gap for _, box_h in layouts) - gap
        if total_h > max_h:
            layouts = self._truncate_layouts(layouts, max_h, line_h, pad)

        y = cr.top()
        for lines, box_h in layouts:
            if y >= self.height() - line_h:
                break
            box_h = min(box_h, self.height() - y)
            if box_h < line_h:
                break
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 180))
            p.drawRoundedRect(QRect(cr.left(), y, box_w, box_h), 6, 6)
            p.setPen(QColor(Palette.TEXT))
            p.setFont(font)
            ty = y + pad + fm.ascent()
            for line in lines:
                if ty > y + box_h - pad - fm.descent():
                    break
                p.drawText(cr.left() + pad, ty, line)
                ty += line_h
            y += box_h + gap

    @staticmethod
    def _truncate_layouts(layouts, max_h, line_h, pad):
        result = []
        used = 0
        for lines, box_h in layouts:
            remain_h = max_h - used
            if remain_h < line_h + pad * 2:
                break
            if box_h <= remain_h:
                result.append([lines, box_h])
                used += box_h + 6
                continue
            keep = max(1, int((remain_h - pad * 2) // line_h))
            cut = list(lines[:keep])
            if cut:
                cut[-1] = (cut[-1][:-1] + "…") if cut[-1] else "…"
            result.append([cut, len(cut) * line_h + pad * 2])
            break
        return result

    def _draw_status(self, p):
        if not self._status_text:
            return
        font = QFont("Microsoft YaHei")
        font.setPixelSize(15)
        fm = QFontMetrics(font)
        text = self._status_text
        tw = fm.horizontalAdvance(text)
        pad = 14
        bw = tw + pad * 2
        bh = 40
        # 无选区时（如"选区太小"提示）以整屏居中显示
        cr = self._crop_rect or QRect(0, 0, self.width(), self.height())
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
        self._extract_popup.setObjectName("extractPopup")
        self._extract_popup.setStyleSheet(f"""
            QFrame#extractPopup {{
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
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    # ── 编辑模式 ─────────────────────────────────────────────
    # （内联编辑：编辑操作直接画在 _edit_overlay 上，见下方鼠标事件）

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
                lp = QPoint(int(local.x() * self._scale),
                            int(local.y() * (self._scale_y or self._scale)))
                if self._edit_tool == "text":
                    # Check delete button first
                    if self._edit_selected_text_idx >= 0 and self._delete_text_btn_rect.contains(pos):
                        self._push_edit_undo()
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
                        self._push_edit_undo()
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
                            self._push_edit_undo()
                            self._edit_selected_text_idx = hit
                            self._edit_dragging_text = True
                            self._edit_resize_corner = None
                            self._edit_drag_offset = QPoint(
                                local.x() - int(round(
                                    self._edit_text_items[hit]["x"] / self._scale)),
                                local.y() - int(round(
                                    self._edit_text_items[hit]["y"]
                                    / (self._scale_y or self._scale))))
                        else:
                            # 空白处：框选一个区域作为文字输入框
                            self._edit_selected_text_idx = -1
                            self._edit_dragging_text = False
                            self._edit_resize_corner = None
                            self._delete_text_btn_rect = QRect()
                            self._text_box_active = True
                            self._text_box_start = QPoint(local)
                            self._text_box_end = QPoint(local)
                            self.update()
                else:
                    self._edit_drawing = True
                    self._push_edit_undo()
                    self._edit_last = lp
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

        # 已选定区域：先判断是否点在移动圆点 / 边缘缩放手柄上
        hit = self._crop_hit(pos)
        if hit:
            self._crop_drag_mode = hit
            self._crop_drag_start_rect = QRect(self._crop_rect)
            self._crop_drag_start_pos = QPoint(pos)
            self.setCursor(QCursor(self._cursor_for_edge(hit)))
            return

        if self._show_menu:
            # 菜单按钮为子控件，点击由按钮自身处理；到达这里说明点在菜单外
            # 选区内部保持菜单；选区外部则重新框选
            if self._edit_overlay is not None:
                return
            if self._crop_rect and self._crop_rect.contains(pos):
                return
            self._hide_extract_popup()
            self._reset_mode()
            self._show_menu = False
            self._crop_rect = None
            self._selecting = True
            self._start = pos
            self._end = pos
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            self.update()
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
        """翻译模式下点击选区，在译文 / 原文之间切换。

        （调用方已确保处于 translate 模式）需已识别到文字才允许切换。
        """
        if self._ocr_text:
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
        if self._crop_drag_mode:
            self._apply_crop_drag(pos - self._crop_drag_start_pos)
            return
        if self._text_box_active:
            cr = self._crop_rect
            if cr:
                local = QPoint(pos.x() - cr.x(), pos.y() - cr.y())
                local.setX(max(0, min(local.x(), cr.width())))
                local.setY(max(0, min(local.y(), cr.height())))
                self._text_box_end = local
                self.update()
            return
        if self._edit_overlay is not None and self._edit_pen:
            if self._edit_drawing:
                cr = self._crop_rect
                if cr and cr.contains(pos):
                    local = QPoint(pos.x() - cr.x(), pos.y() - cr.y())
                    cur = QPoint(int(local.x() * self._scale),
                                 int(local.y() * (self._scale_y or self._scale)))
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
                sy = self._scale_y or s
                local = QPoint(pos.x() - cr.x(), pos.y() - cr.y())
                item = self._edit_text_items[self._edit_selected_text_idx]
                if self._edit_resize_corner:
                    dx = local.x() - self._edit_resize_start.x()
                    dy = local.y() - self._edit_resize_start.y()
                    sign_x, sign_y = {
                        "tl": (-1, -1), "tr": (1, -1),
                        "bl": (-1, 1), "br": (1, 1),
                    }.get(self._edit_resize_corner, (1, 1))
                    change = int((sign_x * dx + sign_y * dy) / 3)
                    new_fs = max(8, min(72, self._edit_resize_font_size + change))
                    if new_fs != item["font_size"]:
                        old_fm = QFontMetrics(self._text_font(item["font_size"]))
                        old_tw = old_fm.horizontalAdvance(item["text"])
                        old_th = old_fm.height()
                        item["font_size"] = new_fs
                        new_fm = QFontMetrics(self._text_font(new_fs))
                        tw_diff = new_fm.horizontalAdvance(item["text"]) - old_tw
                        th_diff = new_fm.height() - old_th
                        # (x, y) 是左上角：拖哪个角就固定它的对角
                        if sign_x < 0:
                            item["x"] -= int(tw_diff * s)
                        if sign_y < 0:
                            item["y"] -= int(th_diff * sy)
                elif self._edit_dragging_text:
                    item["x"] = int((local.x() - self._edit_drag_offset.x()) * s)
                    item["y"] = int((local.y() - self._edit_drag_offset.y()) * sy)
                self.update()
            return
        # 菜单显示时根据位置更新编辑光标
        if self._show_menu:
            if self._edit_overlay is not None and self._edit_pen:
                cr = self._crop_rect
                if cr and cr.contains(pos):
                    self._set_edit_cursor()
                else:
                    self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            else:
                # 选区可调整：悬停时给出对应的缩放/移动光标
                hit = self._crop_hit(pos)
                if hit:
                    self.setCursor(QCursor(self._cursor_for_edge(hit)))
                else:
                    self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            return
        if self._selecting:
            self._end = pos
            self.update()

    def mouseReleaseEvent(self, event):
        if self._crop_drag_mode:
            self._crop_drag_mode = ""
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            self.update()
            return
        if self._text_box_active:
            self._text_box_active = False
            cr = self._crop_rect
            if cr is not None:
                end = event.pos()
                local_end = QPoint(
                    max(0, min(end.x() - cr.x(), cr.width())),
                    max(0, min(end.y() - cr.y(), cr.height())))
                start = self._text_box_start
                rect = QRect(start, local_end).normalized()
                origin = rect.topLeft()
                if rect.width() >= 30 and rect.height() >= 18:
                    # 框选区域作为输入框宽度；位置取规范化后的左上角
                    self._start_text_input(
                        origin, box_width=max(80, rect.width()))
                else:
                    self._start_text_input(origin)
            self.update()
            return
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
            rect = rect.intersected(self.rect())  # 拖出屏幕边界时自动裁剪
            if rect.width() < 5 or rect.height() < 5:
                self._status_text = "选区太小，请重新框选"
                self.update()
                self._status_timer.start()
                return
            self._crop_rect = rect
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

            if self._live_select:
                # 实时翻译：保留选区并弹出小菜单，由用户确认后再开始
                self._show_menu = True
                self._status_text = "拖动蓝点或选区内部移动 · 拖动边缘缩放"
                self._status_timer.start()
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                self.update()
                return

            if self._auto_translate:
                # 自动翻译：跳过菜单，直接翻译
                self._status_text = "正在翻译..."
                self.update()
                path = self._save_to_temp()
                if path:
                    self.translateRequested.emit(path)
            else:
                self._show_menu = True
                self._status_text = "拖动蓝点或选区内部移动 · 拖动边缘缩放"
                self._status_timer.start()
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
                        tr = self._get_text_item_rect(item)
                        # 不立即删除，编辑确认时替换；取消则保留原文字
                        self._edit_text_replace_idx = i
                        self._edit_hide_item_idx = i
                        self._start_text_input(
                            QPoint(tr.x(), tr.y()),
                            item["text"], item["font_size"])
                        return
                self._start_text_input(local)

    def wheelEvent(self, event):
        if self._edit_overlay is not None and self._edit_tool == "text" and self._edit_text_items:
            cr = self._crop_rect
            # Qt6 的 QWheelEvent 没有 pos()，统一走 position()
            pos = event.position().toPoint()
            if cr and cr.contains(pos):
                local = QPoint(pos.x() - cr.x(), pos.y() - cr.y())
                for item in reversed(self._edit_text_items):
                    if self._get_text_item_rect(item).contains(local):
                        fs = max(8, min(72, item["font_size"] + (event.angleDelta().y() // 120)))
                        if fs != item["font_size"]:
                            self._push_edit_undo()
                            item["font_size"] = fs
                            self.update()
                        break
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if self._edit_text_input:
            if event.key() == Qt.Key.Key_Escape:
                self._cancel_text_input()
            return
        if (self._live_select and self._show_menu and self._crop_rect
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            self._do_action("开始实时翻译")
            return
        if event.key() == Qt.Key.Key_Escape:
            if self._mode:
                self._reset_mode()
            else:
                self._cancel()

    # ── 状态管理 ──────────────────────────────────────────────

    def _reset_mode(self):
        self._hide_extract_popup()
        self._exit_edit_mode()
        self._mode = None
        self._ocr_text = ""
        self._trans_text = ""
        self._ocr_paragraphs = []
        self._trans_paragraphs = []
        self._trans_blocks = []
        self._showing_trans = False
        self._status_text = ""
        self._show_menu = True
        self._menu_sig = None
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def _exit_edit_mode(self):
        self._cancel_text_input()
        self._edit_overlay = None
        self._edit_pen = False
        self._edit_drawing = False
        self._edit_undo.clear()
        self._edit_redo.clear()
        self._edit_text_items.clear()
        self._edit_selected_text_idx = -1
        self._edit_dragging_text = False
        self._edit_resize_corner = None
        self._edit_text_replace_idx = -1
        self._edit_hide_item_idx = -1
        self._text_box_active = False
        self._menu_sig = None
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def _cancel(self):
        self._hide_extract_popup()
        self.close()
        self.finished.emit()

    def _do_edit_save(self):
        if not self._edit_overlay:
            return
        cr = self._crop_rect
        mapped = self._logical_to_pixmap(cr)
        if mapped.isEmpty():
            return
        crop_pix = self._raw_pixmap.copy(mapped)
        painter = QPainter(crop_pix)
        painter.drawPixmap(0, 0, self._edit_overlay)
        # 文字按逻辑坐标显示，导出时按图片像素比例重新绘制，避免丢字
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        sx = self._scale or 1.0
        sy = self._scale_y or sx
        # 非等比缩放时按几何平均取字号，避免单一轴向放大让文字与落点错配
        text_scale = (sx * sy) ** 0.5
        for i, item in enumerate(self._edit_text_items):
            if i == self._edit_hide_item_idx:
                continue
            font = QFont("Microsoft YaHei")
            font.setPixelSize(max(8, int(round(item["font_size"] * text_scale))))
            painter.setFont(font)
            painter.setPen(QPen(item["color"]))
            fm = QFontMetrics(font)
            painter.drawText(
                QRect(int(item["x"]), int(item["y"]),
                      fm.horizontalAdvance(item["text"]) + 4, fm.height() + 2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                item["text"])
        painter.end()
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存截图", "标注.png",
            "PNG 图片 (*.png);;JPG 图片 (*.jpg)")
        if save_path:
            crop_pix.save(save_path)
        self._exit_edit_mode()
        self.update()

    def _start_text_input(self, local, initial_text="", font_size=None,
                          box_width=None):
        """在选区内创建内联文字输入框（位置语义：左上角）。

        输入框的边框/内边距会让文字比框左上角缩进几像素，这里反向抵消，
        使「输入时看到的位置」与「确认后落字的位置」重合。
        """
        if self._edit_text_input:
            self._commit_text_input()
        cr = self._crop_rect
        if font_size is None:
            font_size = self._edit_text_font_size
        self._edit_text_pos = QPoint(local.x(), local.y())
        self._edit_text_font_size = font_size
        font = self._text_font(font_size)
        fm = QFontMetrics(font)
        width = max(80, int(box_width or 200))
        height = fm.height() + 8
        edit = QLineEdit(self)
        edit.setPlaceholderText("输入文字，回车确认")
        # 与叠加文字同为像素字号，保证输入框内字号即落字字号
        edit.setFont(font)
        edit.setStyleSheet(f"""
            QLineEdit {{
                background: transparent; color: {self._edit_color.name()};
                border: 1px dashed rgba(255,255,255,180);
                font-family: 'Microsoft YaHei';
                font-size: {max(8, int(font_size))}px;
                padding: 0px 2px;
            }}
        """)
        edit.setFixedSize(width, height)
        if initial_text:
            edit.setText(initial_text)
            edit.selectAll()
        # 1px 边框 + 2px 左右内边距；垂直方向 QLineEdit 居中绘制
        inset_x = 3
        inset_y = (height - fm.height()) // 2 + 1
        x = cr.x() + local.x() - inset_x
        y = cr.y() + local.y() - inset_y
        if x + width > self.width():
            x = max(0, self.width() - width - 10)
        if y + height > self.height():
            y = max(0, self.height() - height - 10)
        edit.move(max(0, x), max(0, y))
        edit.show()
        edit.setFocus()
        edit.editingFinished.connect(self._commit_text_input)
        self._edit_text_input = edit

    def _commit_text_input(self):
        edit = self._edit_text_input
        if edit is None:
            return
        text = edit.text().strip()
        pos = self._edit_text_pos
        replace_idx = self._edit_text_replace_idx
        # 先置空，避免 hide() 触发 editingFinished 造成递归二次提交
        self._edit_text_input = None
        self._edit_text_replace_idx = -1
        self._edit_hide_item_idx = -1
        edit.hide()
        edit.deleteLater()

        # 编辑已有文字：保留原位置/字号/颜色
        if 0 <= replace_idx < len(self._edit_text_items):
            if text:
                self._push_edit_undo()
                old = self._edit_text_items[replace_idx]
                self._edit_text_items[replace_idx] = {
                    "text": text,
                    "x": old["x"],
                    "y": old["y"],
                    "font_size": old["font_size"],
                    "color": QColor(old["color"]),
                }
            else:
                # 清空内容视为删除该文字
                self._push_edit_undo()
                self._edit_text_items.pop(replace_idx)
            self.update()
            return

        if text:
            self._push_edit_undo()
            s = self._scale
            sy = self._scale_y or s
            self._edit_text_items.append({
                "text": text,
                "x": int(pos.x() * s),
                "y": int(pos.y() * sy),
                "font_size": self._edit_text_font_size,
                "color": QColor(self._edit_color),
            })
            self.update()

    def _cancel_text_input(self):
        edit = self._edit_text_input
        self._edit_text_input = None
        if edit is not None:
            edit.hide()
            edit.deleteLater()
        self._edit_text_replace_idx = -1
        self._edit_hide_item_idx = -1
        self.update()

    @staticmethod
    def _clone_text_items(items):
        """复制文字条目（QColor 不能 deepcopy，手写克隆）。"""
        return [{
            "text": it["text"],
            "x": it["x"],
            "y": it["y"],
            "font_size": it["font_size"],
            "color": QColor(it["color"]),
        } for it in items]

    def _push_edit_undo(self):
        """保存一步可撤销状态（画笔层 + 文字条目 + 选中项）。"""
        self._edit_undo.append((
            self._edit_overlay.copy(),
            self._clone_text_items(self._edit_text_items),
            self._edit_selected_text_idx,
        ))
        self._edit_redo.clear()
        if len(self._edit_undo) > 50:
            self._edit_undo.pop(0)

    def _restore_edit_snapshot(self, snapshot):
        pixmap, items, selected = snapshot
        self._edit_overlay = pixmap.copy()
        self._edit_text_items = self._clone_text_items(items)
        self._edit_selected_text_idx = (
            selected if 0 <= selected < len(self._edit_text_items) else -1)

    @staticmethod
    def _text_font(font_size):
        """文字条目的字体：字号单位统一为像素（与输入框 CSS px 一致）。"""
        font = QFont("Microsoft YaHei")
        font.setPixelSize(max(8, int(font_size)))
        return font

    def _get_text_item_rect(self, item):
        """Get bounding rect of a text item relative to crop area."""
        sx = self._scale or 1.0
        sy = self._scale_y or sx
        x = int(item["x"] / sx)
        y = int(item["y"] / sy)
        fm = QFontMetrics(self._text_font(item["font_size"]))
        tw = fm.horizontalAdvance(item["text"])
        th = fm.height()
        # (x, y) 是左上角，与绘制 / 输入框保持一致
        return QRect(x, y, tw + 4, th + 4)

    def _do_action(self, name):
        """执行菜单动作（真实控件按钮直接按名称调用）。"""
        self._status_timer.stop()
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
        elif name in ("翻译", "原文"):
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
        elif name == "开始实时翻译":
            cr = self._crop_rect
            if cr:
                self._show_menu = False
                self.regionSelected.emit(
                    QRect(self.mapToGlobal(cr.topLeft()), cr.size()))
                self.close()
                self.finished.emit()
        elif name == "重新框选":
            self._crop_rect = None
            self._show_menu = False
            self._selecting = False
            self._status_text = ""
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            self.update()
        elif name == "润色":
            if self._trans_text.strip():
                self._status_text = "AI 润色中..."
                self.setCursor(QCursor(Qt.CursorShape.WaitCursor))
                self.update()
                self.polishRequested.emit("translation", self._trans_text)
        elif name == "编辑":
            self._show_menu = True
            cr = self._crop_rect
            if cr:
                mapped = self._logical_to_pixmap(cr)
                if not mapped.isEmpty():
                    crop_pix = self._raw_pixmap.copy(mapped)
                    self._edit_overlay = QPixmap(crop_pix.size())
                    self._edit_overlay.fill(Qt.GlobalColor.transparent)
                    self._edit_undo.clear()
                    self._edit_redo.clear()
            # 进入编辑后默认画笔，可直接开始标注
            self._edit_pen = True
            self._edit_tool = "pen"
            self._edit_text_replace_idx = -1
            self._edit_hide_item_idx = -1
            self._text_box_active = False
            self._set_edit_cursor()
            self.update()
        elif name in ("画笔", "擦除", "文字"):
            self._edit_tool = {"画笔": "pen", "擦除": "eraser",
                               "文字": "text"}[name]
            self._edit_pen = True
            self._set_edit_cursor()
            self.update()
        elif name == "撤销":
            if self._edit_undo:
                self._edit_redo.append((
                    self._edit_overlay.copy(),
                    self._clone_text_items(self._edit_text_items),
                    self._edit_selected_text_idx,
                ))
                self._restore_edit_snapshot(self._edit_undo.pop())
            self.update()
        elif name == "重做":
            if self._edit_redo:
                self._edit_undo.append((
                    self._edit_overlay.copy(),
                    self._clone_text_items(self._edit_text_items),
                    self._edit_selected_text_idx,
                ))
                self._restore_edit_snapshot(self._edit_redo.pop())
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
        mapped = self._logical_to_pixmap(self._crop_rect)
        if mapped.isEmpty():
            return None
        image = self._raw_pixmap.copy(mapped)
        return None if image.isNull() else image

    def _save_to_temp(self, image=None):
        if image is None:
            image = self._crop_image()
        if image is None or image.isNull():
            return None
        path = _new_capture_path()
        if not image.save(path):
            return None
        return path


class FullscreenCapture:
    """全屏截图（无需选区，取鼠标所在屏幕）。"""

    @staticmethod
    def capture():
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if not screen:
            return None
        # 多显示器下整屏抓取用 grabWindow(0) 即可；传虚拟桌面坐标会错位
        pixmap = screen.grabWindow(0)
        path = _new_capture_path()
        if not pixmap.save(path):
            return None
        QApplication.clipboard().setPixmap(pixmap)
        return path
