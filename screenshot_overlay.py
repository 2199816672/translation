#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PySide6 全屏截图覆盖层：区域选择 + 提取文字/翻译（类微信交互）。"""
import os
import time
import uuid

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


class _DragHandle(QFrame):
    """提取 / 译文小窗口的标题栏：按住即可拖动整个弹窗。

    弹窗是覆盖层的子控件，没有系统标题栏；只让标题栏响应拖动，
    文本区里仍能正常选中 / 编辑文字，不会一按就把窗口拖走。
    """

    def __init__(self, popup, parent=None):
        super().__init__(parent)
        self._popup = popup
        self._press_global = None
        self._popup_pos = None
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        self.setToolTip("按住这里可以拖动窗口")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # 必须用全局坐标做基准：弹窗会跟着鼠标一起移动，event.pos()
            # 的参考系每次都在变，拿它算位移会半速拖动、抖动甚至回跳。
            self._press_global = event.globalPosition().toPoint()
            self._popup_pos = QPoint(self._popup.pos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._press_global is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            delta = event.globalPosition().toPoint() - self._press_global
            nx = self._popup_pos.x() + delta.x()
            ny = self._popup_pos.y() + delta.y()
            parent = self._popup.parentWidget()
            if parent is not None:
                nx = max(0, min(nx, parent.width() - self._popup.width()))
                ny = max(0, min(ny, parent.height() - self._popup.height()))
            self._popup.move(nx, ny)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_global = None
        self._popup_pos = None
        super().mouseReleaseEvent(event)


class ScreenshotOverlay(QWidget):
    """全屏覆盖层：选区 → 菜单 → 提取(按词点选 + 浮动窗口) / 翻译(叠加译文)。"""

    screenshotTaken = Signal(str)
    imageCopied = Signal(str)       # 复制图片成功，参数是图片尺寸说明
    extractRequested = Signal(str)
    extractTranslateRequested = Signal(str)  # 提取窗口里选中的文字
    translateRequested = Signal(str)
    polishRequested = Signal(str, str)  # (kind, text)
    regionSelected = Signal(QRect)      # 实时翻译：选区（全局坐标）
    finished = Signal()

    def __init__(self, auto_translate=False, live_select=False, parent=None,
                 snap_enabled=True):
        super().__init__(parent)
        self._live_select = live_select
        # 窗口自动吸附：默认开，设置页可关；按住 Alt 可临时自由框选
        self._snap_enabled = bool(snap_enabled)
        self._snap_windows = []      # 浮层局部坐标的候选窗口 (l,t,r,b)
        self._hover_window = None
        self._snap_click_window = None
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
        # 「提取」识别到的视觉行位置，引擎不给词坐标时退化成淡蓝细框
        self._extract_blocks = []
        # 提取模式：原图上按词显示可点标签，选中后才进小窗口
        self._word_blocks = []          # [{text,x,y,w,h,row}]，0~1 归一化
        self._word_selected = set()
        self._word_hover = -1           # 当前悬停的词，用于轻量高亮
        self._word_drag_active = False
        self._word_drag_select = True
        self._word_user_edited = False
        self._setting_extract_text = False
        self._showing_trans = False
        self._popup_rect = QRect()
        self._popup_close = QRect()
        self._extract_popup = None
        self._extract_text_edit = None
        self._extract_hint = None
        self._extract_badge = None
        self._extract_trans_popup = None
        self._extract_trans_edit = None
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
        # 教学类提示（"拖动蓝点移动 · 拖边缘缩放"）1.2 秒根本来不及读完，
        # 用户还没试就没了，且之后不再出现。留足阅读 + 尝试的时间。
        self._status_timer.setInterval(4000)
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
        self._extract_blocks = []
        self._showing_trans = False
        self._edit_overlay = None
        self._popup_close = QRect()
        self._selecting = False
        self._clear_word_selection()
        self._hide_extract_popup()
        self._hide_trans_popup()

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
        self._load_snap_windows()

    # ── 窗口自动吸附 ──────────────────────────────────────────

    def _load_snap_windows(self):
        """枚举「当前屏幕上真实可见」的顶层窗口，换算成浮层局部坐标。"""
        self._snap_windows = []
        self._hover_window = None
        self._snap_click_window = None
        if not self._snap_enabled:
            return
        origin = self.geometry().topLeft()
        sx = self._scale or 1.0
        sy = self._scale_y or sx
        try:
            import window_snapper
            # GetWindowRect 是物理像素、浮层 geometry 是逻辑像素：把当前
            # 截图屏幕换算成物理矩形传下去。另一个显示器上的窗口、以及被
            # 上层窗口完全遮住的后台窗口都不会成为吸附目标。
            geo = self.geometry()
            screen_rect = (
                int(geo.left() * sx), int(geo.top() * sy),
                int((geo.left() + geo.width()) * sx),
                int((geo.top() + geo.height()) * sy))
            raw = window_snapper.enum_visible_windows(
                screen_rect=screen_rect)
        except Exception:
            raw = []
        if not raw:
            return
        bounds = self.rect()
        out = []
        for left, top, right, bottom, _title in raw:
            try:
                ql = int(round(left / sx)) - origin.x()
                qt = int(round(top / sy)) - origin.y()
                qr = int(round(right / sx)) - origin.x()
                qb = int(round(bottom / sy)) - origin.y()
                rect = QRect(ql, qt, qr - ql, qb - qt).intersected(bounds)
                if rect.width() >= 40 and rect.height() >= 30:
                    out.append(window_snapper.rect_tuple(rect)
                               + (_title,))
            except Exception:
                continue
        self._snap_windows = out

    @staticmethod
    def _alt_pressed():
        return bool(QApplication.queryKeyboardModifiers()
                    & Qt.KeyboardModifier.AltModifier)

    def _update_hover(self, pos):
        """未框选时高亮鼠标下的最内层窗口（Alt 临时关闭吸附）。"""
        active = (self._snap_enabled and self._crop_rect is None
                  and not self._selecting and not self._show_menu
                  and self._mode is None and not self._alt_pressed())
        hit = None
        if active and self._snap_windows:
            try:
                import window_snapper
                hit = window_snapper.window_at(
                    (pos.x(), pos.y()), self._snap_windows)
            except Exception:
                hit = None
        if hit != self._hover_window:
            self._hover_window = hit
            self.update()

    def _set_snap(self, enabled):
        self._snap_enabled = bool(enabled)
        if self._snap_enabled:
            self._load_snap_windows()
        else:
            self._snap_windows = []
            self._hover_window = None
        self._status_text = ("窗口吸附已开启：单击选中窗口，拖框自动贴边"
                             if self._snap_enabled else
                             "窗口吸附已关闭：自由框选")
        self._status_timer.start()
        self.update()

    def _draw_hover_window(self, p):
        """微信式悬停高亮：蓝色描边 + 单击选中提示。"""
        left, top, right, bottom, title = self._hover_window
        rect = QRect(left, top, right - left, bottom - top)
        p.setBrush(QColor(96, 165, 250, 26))
        p.setPen(QPen(QColor("#60a5fa"), 2))
        p.drawRect(rect)
        # 四角短标记，明确这是「整窗边界」而不是选区
        p.setPen(QPen(QColor("#93c5fd"), 3))
        length = 16
        for x, dx in ((rect.left(), 1), (rect.right(), -1)):
            for y, dy in ((rect.top(), 1), (rect.bottom(), -1)):
                p.drawLine(x, y, x + dx * length, y)
                p.drawLine(x, y, x, y + dy * length)
        hint = (f"{title} · {rect.width()}×{rect.height()}"
                if title else f"{rect.width()}×{rect.height()}")
        hint += "  |  单击选中整个窗口 · 按住 Alt 自由框选"
        font = QFont("Microsoft YaHei")
        font.setPixelSize(13)
        p.setFont(font)
        tw = p.fontMetrics().horizontalAdvance(hint)
        hx = max(4, min(self.width() - tw - 20, rect.left()))
        hy = rect.top() - 10 if rect.top() > 34 else rect.bottom() + 24
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(12, 14, 20, 210))
        p.drawRoundedRect(QRect(hx - 8, hy - 18, tw + 16, 26), 5, 5)
        p.setPen(QColor(Palette.TEXT))
        p.drawText(hx, hy, hint)

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

    def show_ocr_result(self, text, blocks=None, words=None):
        """提取完成：原图【按词】显示可点标签，选中的词才进小窗口。

        words 是归一化 (0~1) 的词坐标。默认一个都不选：用户点击或按住拖过
        原图上的词，把需要的文字挑进窗口；漏识别 / 错识别的地方可直接在
        窗口里手动补字。引擎不给词坐标时退回全文显示，仍可编辑。
        """
        self._status_text = ""
        # 上一轮可能还开着译文窗口，新一轮提取必须先收起，避免旧译文混进来
        self._hide_trans_popup()
        self._ocr_text = text
        self._ocr_paragraphs = self._split_paragraphs(text)
        self._extract_blocks = [dict(b) for b in (blocks or [])]
        # Windows OCR 原生词坐标没有 row；统一补齐，让「原图上的视觉行」
        # 与「小窗口里汇总出来的文字行」始终一致，也好按行画高亮带。
        from text_segment import assign_word_rows
        self._word_blocks = assign_word_rows(
            [dict(w) for w in (words or [])])
        self._word_selected = set()
        self._word_hover = -1
        self._word_user_edited = False
        self._word_drag_active = False
        self._trans_text = ""
        self._trans_paragraphs = []
        self._showing_trans = False
        self._mode = "extract"
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        # 有词块就等用户点选；没有词坐标的旧引擎直接给全文，仍可编辑
        self._show_extract_popup(
            "" if self._word_blocks else text)
        self.update()

    def show_extract_translation(self, text, error=""):
        """提取小窗口「翻译」的结果：显示在另一个可拖动窗口里。

        失败时只显示错误，绝不把原文塞进译文位置。
        """
        if self._mode != "extract":
            return
        if self._extract_trans_popup is None:
            self._create_trans_popup()
        self._setting_extract_text = True
        self._extract_trans_edit.setPlainText(
            (error or "翻译失败，未返回译文") if error else str(text or ""))
        self._setting_extract_text = False
        self._place_popup(self._extract_trans_popup, 380, 232,
                          prefer_below=True)
        self._extract_trans_popup.raise_()

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
        """后台识别/翻译失败：浮层上给可见提示，别让用户以为「点了没反应」。"""
        self._status_text = msg or "识别或翻译失败，可把选区框大一点再试"
        self._show_menu = True
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self._status_timer.start()
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

        if (not self._crop_rect and not self._selecting
                and self._hover_window is not None):
            self._draw_hover_window(p)
        if not self._crop_rect and (self._selecting or self._live_select):
            self._draw_selecting(p)
        elif self._crop_rect:
            self._draw_crop(p)
            # 只有「译文」状态才叠加覆盖层。切到「原文」直接去掉翻译、
            # 露出原始截图，不再把 OCR 提取文字画成一排小黑框；想看提取了
            # 什么文字，请用「提取」的识别文字小窗口（并附位置高亮）。
            if self._mode == "translate" and self._showing_trans:
                self._draw_trans_overlay(p)
            elif self._mode == "extract" and (
                    self._word_blocks or self._extract_blocks):
                # 提取：优先按词画可点标签；拿不到词坐标的引擎退回整行细框
                if self._word_blocks:
                    self._draw_word_chips(p)
                else:
                    self._draw_extract_highlights(p)

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
            hint = "拖动框选要实时翻译的区域 | 回车开始 | ESC/右键 取消"
        else:
            # 选完区才有工具栏，11 个功能此前在主界面一字未提
            hint = ("拖动框选 | 单击可选中整个窗口 · 拖框自动贴边 · "
                    "按住 Alt 自由框选 | 松开后：提取 / 翻译 / 编辑 / "
                    "复制 / 保存 | ESC/右键 取消")
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

        # 提取模式用与词高亮同色系的柔和蓝框：红框 + 一堆标记混在一起
        # 会显得很乱；其它模式保留原来的红色选区框。
        frame = (QColor(96, 165, 250, 165) if self._mode == "extract"
                 else QColor(239, 68, 68, 190))
        p.setPen(QPen(frame, 1))
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
        if text == "取消":
            btn.setToolTip("编辑标注时：放弃当前全部标注并返回（会先确认）\n"
                           "其它情况：关闭本次截图")
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
            # 已有识别文字时，提供「复制文字」一键拿译文/原文（翻译工具最高频动作），
            # 「复制图片」保留原选区图片复制，避免只能复制图片拿不到文字。
            if self._ocr_text:
                actions = ["复制文字", "复制图片", "保存", "提取", "翻译"]
            else:
                actions = ["复制", "保存", "提取", "翻译"]
            if self._mode == "translate":
                # 文案说清「切到什么」，不写成会被误认为重新翻译的「翻译」。
                # 只要已有译文结果就能切换，不依赖是否附带 OCR 原文。
                actions[-1] = "原文" if self._showing_trans else "译文"
            if self._trans_text.strip():
                actions.append("润色")
            actions.extend(["编辑", "取消"])
            for name in actions:
                btn = self._menu_button(
                    name, lambda n=name: self._do_action(n))
                if name in ("复制", "复制图片", "复制文字",
                            "翻译", "译文", "保存"):
                    btn.setProperty("kind", "primary")
                if name in ("原文", "译文"):
                    btn.setToolTip(
                        "在译文 / 原文之间切换；也可以直接点击选区内的译文区域")
                self._row1_layout.addWidget(btn)
            # 工具栏保持精简的一行：窗口吸附开关统一放在设置页，
            # 不再往小菜单里塞第二行（用户反馈那样太丑）。
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

    # ── 翻译叠加 / 提取位置高亮 ───────────────────────────────

    def _draw_extract_highlights(self, p):
        """提取模式：淡蓝细框标出「哪里识别到了文字」，不遮挡原图内容。

        只描边、不铺底色、不写字——文字内容由已有的识别文字小窗口展示。
        原文模式不再负责展示提取文字，这条路径把它接管过来。
        """
        cr = self._crop_rect
        if not cr or not self._extract_blocks:
            return
        p.save()
        p.setClipRect(cr)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(96, 165, 250, 140), 1))
        for b in self._extract_blocks:
            x = cr.left() + float(b.get("x", 0)) * cr.width()
            y = cr.top() + float(b.get("y", 0)) * cr.height()
            w = float(b.get("w", 0)) * cr.width()
            h = float(b.get("h", 0)) * cr.height()
            rect = QRect(int(x) - 1, int(y) - 1,
                         max(4, int(w) + 2), max(4, int(h) + 2))
            rect = rect.intersected(cr)
            if not rect.isEmpty():
                p.drawRoundedRect(rect, 3, 3)
        p.restore()

    # ── 提取：按词点选 ────────────────────────────────────────

    def _word_rect(self, word):
        """词块（0~1 归一化）→ 屏幕矩形；保证最小可点面积，方便小字点选。"""
        cr = self._crop_rect
        if not cr:
            return QRect()
        x = cr.left() + float(word.get("x", 0)) * cr.width()
        y = cr.top() + float(word.get("y", 0)) * cr.height()
        w = float(word.get("w", 0)) * cr.width()
        h = float(word.get("h", 0)) * cr.height()
        pad = 2
        rect = QRect(int(x) - pad, int(y) - pad,
                     max(14, int(w) + pad * 2),
                     max(14, int(h) + pad * 2))
        return rect.intersected(self.rect())

    def _word_at(self, pos):
        """命中测试：外扩后的词框可能轻微重叠，取包含该点的最小框。"""
        best, best_area = -1, None
        for i, word in enumerate(self._word_blocks):
            rect = self._word_rect(word)
            if rect.contains(pos):
                area = rect.width() * rect.height()
                if best_area is None or area < best_area:
                    best, best_area = i, area
        return best

    def _word_rows(self):
        """按视觉行整理 [(词索引, 词矩形)]，组内按 x 排序。

        行号由 text_segment.assign_word_rows 统一补齐，绘制端不再自己猜，
        保证「原图上的行」与「小窗口里汇总出来的行」是同一套。
        """
        rows = {}
        for i, word in enumerate(self._word_blocks):
            rect = self._word_rect(word)
            if rect.isEmpty():
                continue
            rows.setdefault(int(word.get("row", 0)), []).append((i, rect))
        out = []
        for row in sorted(rows):
            items = sorted(rows[row], key=lambda it: it[1].left())
            if items:
                out.append(items)
        return out

    @staticmethod
    def _united(rects):
        rect = QRect()
        for r in rects:
            rect = rect.united(r)
        return rect

    def _draw_word_chips(self, p):
        """提取模式：标出「哪些词可以点」，但不再用实心块盖住原图。

        旧实现每个词一个白底黑字标签：控制台这类密集文本动辄 80+ 个白块，
        互相重叠、词被截成 "Pyth…"，原图反而看不清。现在改成不写一个字
        的三层轻量提示：
          1. 每个视觉行一条极淡的圆角底带（N 行而不是 N 个方块）；
          2. 每个词底部一条细下划线，暗示可点的词边界；
          3. 连续选中的词合并成一段蓝色高亮，悬停词单独描边。
        命中仍按单个词的原始矩形，逐词点选 / 按住拖选完全不变；绘制
        clip 在选区内，OCR 坐标偶尔越界也不会把标记画到框外。
        """
        if not self._crop_rect or not self._word_blocks:
            return
        rows = self._word_rows()
        if not rows:
            return
        p.save()
        p.setClipRect(self._crop_rect)
        p.setPen(Qt.PenStyle.NoPen)
        # 词特别多时把下划线压淡一点，密密麻麻的短线也会显得吵；
        # 但至少要看得见，否则用户不知道哪里能点。
        line_alpha = 70 if len(self._word_blocks) > 60 else 115

        # 1) 行带：只铺一层极淡底色，不描边。行带把零散的词组织成行，
        #    又不会和词下划线叠成「上下两条平行线」显得像网格。
        for row in rows:
            band = self._united([r for _, r in row]).adjusted(-2, -2, 2, 2)
            p.setBrush(QColor(15, 23, 42, 42))
            p.drawRoundedRect(band, 4, 4)

        # 2) 未选中词的下划线；同时收集同行连续选中的词
        runs = []
        for row in rows:
            run = []
            for idx, r in row:
                if idx in self._word_selected:
                    run.append(r)
                    continue
                if run:
                    runs.append(run)
                    run = []
                p.setBrush(QColor(96, 165, 250, line_alpha))
                p.drawRect(QRect(r.left() + 1, r.bottom() - 1,
                                 max(2, r.width() - 2), 2))
            if run:
                runs.append(run)

        # 3) 连续选中的词合成一段高亮，不再散成一堆碎蓝块
        for run in runs:
            hi = self._united(run).adjusted(-1, -1, 1, 1)
            p.setBrush(QColor(59, 130, 246, 110))
            p.setPen(QPen(QColor(147, 197, 253, 215), 1))
            p.drawRoundedRect(hi, 3, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(147, 197, 253, 235))
            p.drawRect(QRect(hi.left() + 1, hi.bottom() - 2,
                             max(2, hi.width() - 2), 2))

        # 4) 悬停词：淡填充 + 亮描边，明确「这里可以点」
        if (0 <= self._word_hover < len(self._word_blocks)
                and self._word_hover not in self._word_selected):
            hr = self._word_rect(self._word_blocks[self._word_hover])
            if not hr.isEmpty():
                p.setBrush(QColor(96, 165, 250, 58))
                p.setPen(QPen(QColor(147, 197, 253, 190), 1))
                p.drawRoundedRect(hr.adjusted(-1, -1, 1, 1), 3, 3)
        p.restore()

    def _clear_word_selection(self):
        self._word_blocks = []
        self._word_selected = set()
        self._word_hover = -1
        self._word_drag_active = False
        self._word_drag_select = True
        self._word_user_edited = False

    def _selected_words_text(self):
        from text_segment import words_to_text
        return words_to_text(self._word_blocks, self._word_selected)

    def _apply_word_selection(self, index, select):
        """点击 / 按住滑动经过一个词：按起手时决定的「加」或「减」批量处理。"""
        if index < 0 or index >= len(self._word_blocks):
            return
        if (index in self._word_selected) == bool(select):
            return
        if select:
            self._word_selected.add(index)
        else:
            self._word_selected.discard(index)
        self._sync_extract_text()
        self.update()

    def _sync_extract_text(self):
        """把当前选中的词按原图行序汇总进小窗口。

        用户手动改过文字后不再覆盖，避免把补的字抹掉；点「全选 / 清空」
        会重置这一状态重新按词汇总。
        """
        if self._word_user_edited or self._extract_text_edit is None:
            return
        self._set_extract_text(self._selected_words_text())
        self._update_extract_hint()

    def _popup_contains(self, pos):
        """点在小窗口里时交给子控件处理（拖动 / 编辑 / 按钮）。"""
        for popup in (self._extract_popup, self._extract_trans_popup):
            if (popup is not None and popup.isVisible()
                    and popup.geometry().contains(pos)):
                return True
        return False

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
        """译文按【视觉行】原位贴回：每行一个小底框，字号各自自适应。

        分组已由 text_segment.group_blocks_fine 拆到「行」级别。旧实现挑
        一个「所有段落都放得下」的统一字号，一个小标签就把整屏压成 11px，
        还会把整段 bbox 撑成一个盖住半个窗口的大黑框。这里改为每块独立
        挑字号、底框最多两行，位置与原文字一一对应。
        """
        cr = self._crop_rect
        # 切到「原文」时 paintEvent 直接跳过整个覆盖层，这里只负责画译文，
        # 不再把 OCR 提取文字贴回原位置。
        key = "trans"
        pad = 6
        bleed = 2          # OCR bbox 常略小于字形，向外扩一点确保盖住
        right = min(self.width(), cr.right() + 1)
        bottom = min(self.height(), cr.bottom() + 1)
        # 只框住 "Portrait" 这类词时，原文行只有几十像素宽，译文硬塞进去
        # 会被截成「人像…」，看起来像「翻译不显示」。窄行允许译文框向右
        # 扩到可读宽度；设上限避免又变成盖住半个窗口的大黑框。
        min_readable_w = 150
        max_box_w = 300

        # 1) 原文行 bbox（相对裁剪图的 0~1 比例）→ 屏幕矩形
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

        # 2) 每块独立挑字号：字号跟随原文行高（保持原界面的视觉层级），
        #    译文放不下才逐级减小，互不拖累。
        font = QFont("Microsoft YaHei")
        font.setPixelSize(13)
        probe = QFontMetrics(font)
        items = []
        for b, (left, top, w, h) in zip(self._trans_blocks, rects):
            text = str(b.get(key) or "").strip()
            box_w = w
            if w < min_readable_w and text:
                natural = probe.horizontalAdvance(text) + pad * 2
                box_w = max(w, min(natural, max_box_w,
                                   max(24, self.width() - left)))
            start = max(11, min(20, int(h * 0.8)))
            sizes = list(range(start, 10, -1))
            chosen = None
            for size in sizes:
                font.setPixelSize(size)
                fm = QFontMetrics(font)
                max_w = max(24, box_w - pad * 2)
                lines = self._wrap_to_width(fm, text, max_w) or [""]
                need_h = len(lines) * fm.height() + pad * 2
                chosen = (QFont(font), QFontMetrics(font), lines, max_w, box_w)
                if need_h <= max(h, 22) or size == sizes[-1]:
                    break
            items.append(chosen)

        # 3) 先铺全部底框：高度取 max(原文行, 译文所需)，但最多两行，
        #    且允许越过选区底部（小选区也能完整显示译文），不会盖住下方控件。
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(12, 14, 20, 226))
        boxes = []
        for (left, top, w, h), (fnt, fm, lines, max_w, box_w) in zip(rects, items):
            line_h = fm.height()
            draw = list(lines[:2])
            if len(lines) > 2 and draw:
                last = draw[-1]
                while last and fm.horizontalAdvance(last + "…") > max_w:
                    last = last[:-1]
                draw[-1] = (last.rstrip("，,、 ") + "…") if last else "…"
            draw_h = min(max(h, len(draw) * line_h + pad * 2),
                         max(8, self.height() - top))
            boxes.append((left, top, draw_h, fnt, fm, draw, line_h))
            p.drawRoundedRect(QRect(left, top, box_w, draw_h), 5, 5)

        # 4) 逐块写译文，在各自的小框里垂直居中
        for left, top, draw_h, fnt, fm, draw, line_h in boxes:
            used = len(draw) * line_h
            ty = int(top + pad + fm.ascent()
                     + max(0, (draw_h - pad * 2 - used) // 2))
            p.setFont(fnt)
            p.setPen(QColor(Palette.TEXT))
            for line in draw:
                p.drawText(left + pad, ty, line)
                ty += line_h

    def _draw_trans_stacked(self, p):
        """没有位置信息时的回退：整段按段落顺序从选区左上角往下堆。"""
        cr = self._crop_rect
        # 原文状态不绘制叠加层；这里只负责译文的整段回退显示
        paragraphs = self._trans_paragraphs
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

    # ── 提取 / 译文小窗口（可拖动的真实 Widget）──────────────

    def _popup_style(self, name):
        return f"""
            QFrame#{name} {{
                background: {Palette.SURFACE};
                border: 1px solid {Palette.BORDER};
                border-radius: 12px;
            }}
            QTextEdit {{
                background: {Palette.BG_INPUT};
                color: {Palette.TEXT};
                border: 1px solid {Palette.BORDER_SOFT};
                border-radius: 8px;
                padding: 8px 10px;
                font-size: 13px;
                font-family: 'Microsoft YaHei';
            }}
            QTextEdit:focus {{ border: 1px solid {Palette.FOCUS}; }}
            QPushButton {{
                background: {Palette.SURFACE_HOVER};
                color: {Palette.TEXT};
                border: 1px solid {Palette.BORDER_SOFT};
                border-radius: 6px;
                padding: 4px 11px;
                font-size: 12px;
                font-family: 'Microsoft YaHei';
            }}
            QPushButton:hover {{
                border-color: {Palette.FOCUS}; color: {Palette.TEXT_STRONG};
            }}
            QPushButton#popupPrimary {{
                background: {Palette.PRIMARY};
                color: {Palette.PRIMARY_TEXT};
                border: 1px solid {Palette.PRIMARY};
                font-weight: 600;
            }}
            QPushButton#popupPrimary:hover {{
                background: {Palette.PRIMARY_HOVER};
                color: {Palette.PRIMARY_TEXT};
            }}
            QPushButton#popupClose {{
                background: transparent; border: none; padding: 0;
                color: {Palette.TEXT_MUTED}; font-size: 15px;
            }}
            QPushButton#popupClose:hover {{ color: {Palette.TEXT_STRONG}; }}
            QLabel {{
                background: transparent; border: none;
                color: {Palette.TEXT_STRONG};
                font-size: 13px; font-weight: 600;
                font-family: 'Microsoft YaHei';
            }}
            QLabel#popupHint {{
                color: {Palette.TEXT_MUTED}; font-size: 11px;
                font-weight: 400; padding: 1px 0 3px 0;
            }}
            QLabel#popupBadge {{
                color: {Palette.TEXT_MUTED};
                background: {Palette.SURFACE_HOVER};
                border: 1px solid {Palette.BORDER_SOFT};
                border-radius: 8px; padding: 1px 7px;
                font-size: 11px; font-weight: 400;
            }}
        """

    def _make_popup_header(self, popup, title, on_close, badge=None):
        handle = _DragHandle(popup)
        row = QHBoxLayout(handle)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        label = QLabel(title, handle)
        # 标题不吞鼠标事件，整个标题栏都能抓住拖动
        label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(label)
        if badge is not None:
            row.addWidget(badge)
        row.addStretch(1)
        close_btn = QPushButton("×", handle)
        close_btn.setObjectName("popupClose")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(on_close)
        row.addWidget(close_btn)
        return handle

    @staticmethod
    def _popup_button(label, slot):
        btn = QPushButton(label)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.clicked.connect(slot)
        return btn

    def _create_extract_popup(self):
        self._extract_popup = QFrame(self)
        self._extract_popup.setObjectName("extractPopup")
        self._extract_popup.setStyleSheet(self._popup_style("extractPopup"))
        layout = QVBoxLayout(self._extract_popup)
        layout.setContentsMargins(14, 11, 14, 12)
        layout.setSpacing(8)

        # 标题栏右侧的「已选 / 总数」徽标，选词进度一眼可见
        self._extract_badge = QLabel("", self._extract_popup)
        self._extract_badge.setObjectName("popupBadge")
        layout.addWidget(self._make_popup_header(
            self._extract_popup, "识别文字", self._on_extract_popup_close,
            badge=self._extract_badge))

        self._extract_hint = QLabel("", self._extract_popup)
        self._extract_hint.setObjectName("popupHint")
        self._extract_hint.setWordWrap(True)
        layout.addWidget(self._extract_hint)

        self._extract_text_edit = QTextEdit(self._extract_popup)
        # 可编辑：OCR 漏识别 / 认错字时用户能直接补上，不必重新框选
        self._extract_text_edit.setReadOnly(False)
        self._extract_text_edit.setMinimumSize(300, 100)
        self._extract_text_edit.setMaximumSize(460, 500)
        self._extract_text_edit.setWordWrapMode(
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._extract_text_edit.setPlaceholderText(
            "点击或拖过原图上带下划线的词，选中的文字会出现在这里；\n"
            "也可以直接输入，补上没识别到的内容")
        self._extract_text_edit.textChanged.connect(
            self._on_extract_text_changed)
        layout.addWidget(self._extract_text_edit, 1)

        # 按钮收成整齐一行：选择类在左，输出类与主操作在右
        row = QHBoxLayout()
        row.setSpacing(6)
        self._extract_all_btn = self._popup_button(
            "全选", self._on_extract_select_all)
        self._extract_clear_btn = self._popup_button(
            "清空", self._on_extract_clear)
        self._extract_copy_btn = self._popup_button(
            "复制", self._on_extract_copy)
        self._extract_save_btn = self._popup_button(
            "保存", self._on_extract_save)
        self._extract_translate_btn = self._popup_button(
            "翻译", self._on_extract_translate)
        self._extract_translate_btn.setObjectName("popupPrimary")
        row.addWidget(self._extract_all_btn)
        row.addWidget(self._extract_clear_btn)
        row.addStretch(1)
        row.addWidget(self._extract_copy_btn)
        row.addWidget(self._extract_save_btn)
        row.addWidget(self._extract_translate_btn)
        layout.addLayout(row)

    def _create_trans_popup(self):
        self._extract_trans_popup = QFrame(self)
        self._extract_trans_popup.setObjectName("extractTransPopup")
        self._extract_trans_popup.setStyleSheet(
            self._popup_style("extractTransPopup"))
        layout = QVBoxLayout(self._extract_trans_popup)
        layout.setContentsMargins(14, 11, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(self._make_popup_header(
            self._extract_trans_popup, "译文", self._hide_trans_popup))
        self._extract_trans_edit = QTextEdit(self._extract_trans_popup)
        self._extract_trans_edit.setReadOnly(False)
        self._extract_trans_edit.setMinimumSize(300, 100)
        self._extract_trans_edit.setMaximumSize(460, 500)
        self._extract_trans_edit.setWordWrapMode(
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._extract_trans_edit.setPlaceholderText("译文会显示在这里，可直接修改后复制")
        layout.addWidget(self._extract_trans_edit, 1)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._extract_trans_copy_btn = self._popup_button(
            "复制", self._on_extract_trans_copy)
        self._extract_trans_save_btn = self._popup_button(
            "保存", self._on_extract_trans_save)
        self._extract_trans_copy_btn.setObjectName("popupPrimary")
        row.addStretch(1)
        row.addWidget(self._extract_trans_copy_btn)
        row.addWidget(self._extract_trans_save_btn)
        layout.addLayout(row)

    def _flash_extract_btn(self, btn, text):
        """在弹窗按钮上就地显示短反馈并自动恢复。

        主浮层的 _status_text 是画在全屏覆盖层上的，提取弹窗是独立置顶
        窗口，用户看不到那里的提示；改为按钮自身换字最直观，也不会像
        _status_timer.stop() 那样让提示永久残留。
        """
        original = btn.text()
        btn.setText(text)
        btn.setEnabled(False)

        def _restore():
            try:
                btn.setText(original)
                btn.setEnabled(True)
            except RuntimeError:
                pass  # 弹窗已被销毁
        QTimer.singleShot(1200, _restore)

    def _set_extract_text(self, text):
        """程序写入小窗口：加标志，别被 textChanged 误判成用户手动编辑。"""
        if self._extract_text_edit is None:
            return
        self._setting_extract_text = True
        self._extract_text_edit.setPlainText(text or "")
        self._setting_extract_text = False

    def _on_extract_text_changed(self):
        if self._setting_extract_text:
            return
        self._word_user_edited = True
        self._update_extract_hint()

    def _update_extract_hint(self):
        """提示与标题栏徽标随选择状态刷新；文案保持简短，别撑高窗口。"""
        if self._extract_hint is None:
            return
        total = len(self._word_blocks)
        picked = len(self._word_selected)
        if self._extract_badge is not None:
            self._extract_badge.setText(
                f"{picked} / {total} 词" if total else "全文")
        if not total:
            self._extract_hint.setText(
                "当前引擎不支持按词点选，可直接编辑下方文字")
        elif self._word_user_edited:
            self._extract_hint.setText(
                "已手动编辑 · 点「全选 / 清空」可恢复按词汇总")
        elif not picked:
            self._extract_hint.setText(
                f"点选或拖过原图上带下划线的词 · 共 {total} 个 · Esc / × 退出")
        else:
            self._extract_hint.setText(
                f"已选 {picked} 个词 · 文字可直接修改 · Esc / × 退出")

    def _show_extract_popup(self, text):
        if self._extract_popup is None:
            self._create_extract_popup()
        self._set_extract_text(text)
        self._update_extract_hint()
        self._place_popup(self._extract_popup, 380, 278)

    def _place_popup(self, popup, w, h, prefer_below=False):
        """把小窗口摆在选区旁边；译文窗口默认排在提取窗口下方，可再拖动。"""
        cr = self._crop_rect or self.rect()
        px, py = cr.right() + 14, cr.top()
        if prefer_below and self._extract_popup is not None:
            base = self._extract_popup.geometry()
            px, py = base.left(), base.bottom() + 10
        if px + w > self.width():
            px = max(0, cr.left() - w - 14)
        if py + h > self.height():
            py = max(0, self.height() - h - 10)
        popup.setGeometry(max(0, px), max(0, py),
                          min(w, self.width()), min(h, self.height()))
        popup.show()
        popup.raise_()

    def _hide_extract_popup(self):
        if self._extract_popup:
            self._extract_popup.hide()
        self._hide_trans_popup()

    def _hide_trans_popup(self):
        if self._extract_trans_popup:
            self._extract_trans_popup.hide()

    def _on_extract_select_all(self):
        """一键拿全部：也是从「手动编辑」回到按词汇总的入口。"""
        self._word_user_edited = False
        if self._word_blocks:
            self._word_selected = set(range(len(self._word_blocks)))
            self._set_extract_text(self._selected_words_text())
        else:
            self._set_extract_text(self._ocr_text)
        self._update_extract_hint()
        self.update()

    def _on_extract_clear(self):
        self._word_user_edited = False
        self._word_selected.clear()
        self._set_extract_text("")
        self._update_extract_hint()
        self.update()

    def _on_extract_translate(self):
        text = (self._extract_text_edit.toPlainText().strip()
                if self._extract_text_edit is not None else "")
        if not text:
            self._flash_extract_btn(self._extract_translate_btn, "无内容")
            return
        # 先把译文窗口摆出来显示「正在翻译…」，主窗口异步回传结果
        self.show_extract_translation("正在翻译…")
        self.extractTranslateRequested.emit(text)

    def _on_extract_copy(self):
        """提取弹窗：一键复制识别文字到剪贴板。"""
        text = self._extract_text_edit.toPlainText()
        if not text.strip():
            self._flash_extract_btn(self._extract_copy_btn, "无内容")
            return
        QApplication.clipboard().setText(text)
        self._flash_extract_btn(self._extract_copy_btn, "已复制")

    def _on_extract_save(self):
        """提取弹窗：把识别文字保存到本地文件。"""
        text = self._extract_text_edit.toPlainText()
        if not text.strip():
            self._flash_extract_btn(self._extract_save_btn, "无内容")
            return
        path, chosen = QFileDialog.getSaveFileName(
            self, "保存识别文字", "识别文字.txt",
            "文本文件 (*.txt);;Markdown (*.md)")
        if not path:
            return
        # 用户手输文件名不带后缀时按所选格式补全
        if not os.path.splitext(path)[1]:
            path += '.md' if '*.md' in (chosen or '') else '.txt'
        try:
            with open(path, "w", encoding="utf-8-sig" if
                      path.lower().endswith(".txt") else "utf-8") as f:
                f.write(text)
            self._flash_extract_btn(self._extract_save_btn, "已保存")
        except Exception as e:
            self._flash_extract_btn(self._extract_save_btn, "保存失败")
            print(f"保存识别文字失败：{e}")

    def _on_extract_trans_copy(self):
        text = (self._extract_trans_edit.toPlainText()
                if self._extract_trans_edit is not None else "")
        if not text.strip():
            self._flash_extract_btn(self._extract_trans_copy_btn, "无内容")
            return
        QApplication.clipboard().setText(text)
        self._flash_extract_btn(self._extract_trans_copy_btn, "已复制")

    def _on_extract_trans_save(self):
        text = (self._extract_trans_edit.toPlainText()
                if self._extract_trans_edit is not None else "")
        if not text.strip():
            self._flash_extract_btn(self._extract_trans_save_btn, "无内容")
            return
        path, chosen = QFileDialog.getSaveFileName(
            self, "保存译文", "译文.txt",
            "文本文件 (*.txt);;Markdown (*.md)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += '.md' if '*.md' in (chosen or '') else '.txt'
        try:
            with open(path, "w", encoding="utf-8-sig" if
                      path.lower().endswith(".txt") else "utf-8") as f:
                f.write(text)
            self._flash_extract_btn(self._extract_trans_save_btn, "已保存")
        except Exception as e:
            self._flash_extract_btn(self._extract_trans_save_btn, "保存失败")
            print(f"保存译文失败：{e}")

    def _on_extract_popup_close(self):
        self._hide_extract_popup()
        # 关闭弹窗必须退出 extract 模式：否则 _crop_hit 一直返回空（手柄消失）、
        # mousePressEvent 末尾被 `if self._mode: return` 吞掉，选区再也调不动。
        self._mode = None
        self._extract_blocks = []
        self._clear_word_selection()
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

        # 提取 / 译文小窗口里的点击交给子控件自己处理（拖动 / 编辑 / 按钮）。
        # 旧实现「点弹窗外就关闭」会和新的按词点选冲突：用户要反复回到
        # 原图点词，因此退出只走 × / Esc / 右键。
        if self._mode == "extract" and self._popup_contains(pos):
            return

        # 原文状态没有画关闭 ×，别让上一次译文留下的热区误触发退出
        if (self._mode == "translate" and self._showing_trans
                and self._popup_close.contains(pos)):
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

        # 提取模式：点击原图上的词切换选中，按住拖过可一次多选 / 多选取消。
        # 起手那个词决定本次是「加」还是「减」，拖过的词都执行同一操作。
        if self._mode == "extract":
            if self._word_blocks and self._crop_rect:
                idx = self._word_at(pos)
                if idx >= 0:
                    self._word_drag_active = True
                    self._word_drag_select = idx not in self._word_selected
                    self._apply_word_selection(idx, self._word_drag_select)
                    return
            # 点偏一点 / 点选区外都不退出：用户要反复回原图点词，
            # 一点就重新框选会丢掉已选内容。退出统一走 × / Esc / 右键。
            return

        if self._show_menu:
            # 菜单按钮为子控件，点击由按钮自身处理；到达这里说明点在菜单外
            # 选区内部：翻译完成后点击即在「译文 → 原文 → 译文」间切换；
            # 选区外部：保持原有的重新框选。
            if self._edit_overlay is not None:
                return
            if self._crop_rect and self._crop_rect.contains(pos):
                # 旧实现在这里直接 return，导致 _toggle_trans 永远走不到，
                # 「点击切换原文」实际上点了没反应。
                if self._mode == "translate":
                    self._toggle_trans()
                return
            self._hide_extract_popup()
            self._reset_mode()
            self._show_menu = False
            self._crop_rect = None
            self._snap_click_window = self._hover_window
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

        self._snap_click_window = self._hover_window
        self._selecting = True
        self._start = pos
        self._end = pos
        self._crop_rect = None
        self._show_menu = False
        self.update()

    def _toggle_trans(self):
        """翻译模式下点击选区：译文 ↔ 原文。

        原文状态不绘制任何叠加层，直接露出原始截图；提取到的文字由
        「提取」的识别文字小窗口 + 位置高亮负责展示。
        """
        self._showing_trans = not self._showing_trans
        if not self._showing_trans:
            # 译文的关闭 × 已不再绘制，清掉热区避免点在原位置误退出
            self._popup_close = QRect()
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
        self._update_hover(pos)
        if self._crop_drag_mode:
            self._apply_crop_drag(pos - self._crop_drag_start_pos)
            return
        # 按住拖过词块：批量选中 / 批量取消，起手决定方向
        if self._word_drag_active:
            if self._word_hover != -1:
                self._word_hover = -1
                self.update()
            idx = self._word_at(pos)
            if idx >= 0:
                self._apply_word_selection(idx, self._word_drag_select)
            return
        if self._mode == "extract" and self._word_blocks:
            # 悬停在词上：手型光标 + 轻量描边，提示「这里可以点」
            over_popup = self._popup_contains(pos)
            in_crop = bool(self._crop_rect and self._crop_rect.contains(pos))
            idx = self._word_at(pos) if (in_crop and not over_popup) else -1
            if idx != self._word_hover:
                self._word_hover = idx
                self.update()
            if idx >= 0:
                self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            elif not over_popup:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
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
        if self._word_drag_active:
            self._word_drag_active = False
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
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
            snap_active = (self._snap_enabled and not self._alt_pressed())
            if rect.width() < 5 or rect.height() < 5:
                # 微信式：单击直接选中鼠标所在窗口，而不是报「选区太小」
                hit = self._snap_click_window if snap_active else None
                if hit:
                    left, top, right, bottom = hit[:4]
                    rect = QRect(left, top, right - left,
                                 bottom - top).intersected(self.rect())
                else:
                    self._status_text = "选区太小，请重新框选"
                    self.update()
                    self._status_timer.start()
                    return
            elif snap_active and self._snap_windows:
                # 手动拖框：四条边磁吸到最近的窗口边界
                try:
                    import window_snapper
                    snapped = window_snapper.snap_rect(
                        window_snapper.rect_tuple(rect), self._snap_windows)
                    rect = QRect(snapped[0], snapped[1],
                                 snapped[2] - snapped[0],
                                 snapped[3] - snapped[1]
                                 ).intersected(self.rect())
                except Exception:
                    pass
            self._snap_click_window = None
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
                path = self._save_ocr_image()
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
            elif self._edit_overlay is not None:
                # 编辑模式不设置 _mode，直接 _cancel() 会关掉整个浮层、
                # 丢掉全部标注；ESC 应该只退出编辑回到选区菜单。
                self._exit_edit_mode()
                self.update()
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
        self._extract_blocks = []
        self._clear_word_selection()
        self._showing_trans = False
        self._popup_close = QRect()
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
        self._clear_word_selection()
        # 框选完又点「取消」/ 右键 / ESC 时，别把这张图直接丢掉：存一份到
        # 主页「截图预览」，用户仍能用「复制截图」或「保存」继续处理。
        if (not self._live_select and self._crop_rect
                and self._raw_pixmap is not None):
            image = self._crop_image()
            if image is not None and not image.isNull():
                path = self._save_to_temp(image)
                if path:
                    self.screenshotTaken.emit(path)
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

        if name == "复制文字":
            # 翻译工具最高频动作是「拿译文」：译文优先，未翻译时回退原文
            text = (self._trans_text.strip() or self._ocr_text.strip())
            if not text:
                # 没有文字时别静默关掉浮层，用户会以为复制成功了
                self._status_text = "没有可复制的文字"
                self._status_timer.start()
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                self.update()
                return
            QApplication.clipboard().setText(text)
            self._hide_extract_popup()
            self.close()
            self.finished.emit()
        elif name in ("复制", "复制图片"):
            # 菜单在有识别文字时把「复制」改名成「复制图片」，两个名字必须都
            # 走这里；否则会落到末尾的 _cancel()，图片没复制还把截图作废了。
            image = self._crop_image()
            if not image or image.isNull():
                # 选区无效时别静默关掉浮层，否则用户以为复制成功了
                self._status_text = "截图区域无效，未能复制图片"
                self._status_timer.start()
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                self._show_menu = True
                self.update()
                return
            QApplication.clipboard().setPixmap(image)
            # 浮层马上就要关闭，自己在上面闪提示用户根本看不到；交给主窗口
            # 的状态栏 / 托盘气泡，跨窗口明确告知「可以直接去粘贴了」。
            self.imageCopied.emit(f"{image.width()}×{image.height()}")
            self._show_menu = False
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
                        if image.save(save_path):
                            self._status_text = (
                                f"已保存到 {os.path.basename(save_path)}")
                        else:
                            self._status_text = "保存失败，请检查路径或磁盘空间"
                        self._status_timer.start()
                else:
                    self._status_text = "截图区域无效，未能保存"
                    self._status_timer.start()
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                self._show_menu = True
                self.update()
        elif name == "提取":
            self._show_menu = False
            path = self._save_ocr_image()
            if path:
                self.extractRequested.emit(path)
            else:
                # 临时图保存失败时不能零动作：否则菜单被隐藏、光标停在沙漏，
                # 浮层变成点不动的死锁态。
                self._status_text = "截图保存失败，无法提取文字"
                self._status_timer.start()
                self._show_menu = True
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                self.update()
        elif name in ("翻译", "原文", "译文"):
            if self._mode == "translate":
                self._toggle_trans()
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            else:
                self._show_menu = False
                self._status_text = "正在翻译..."
                self.update()
                path = self._save_ocr_image()
                if path:
                    self.translateRequested.emit(path)
                else:
                    self._status_text = "截图保存失败，无法翻译"
                    self._status_timer.start()
                    self._show_menu = True
                    self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                    self.update()
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
            self._set_edit_cursor()
            self.update()
        elif name == "重做":
            if self._edit_redo:
                self._edit_undo.append((
                    self._edit_overlay.copy(),
                    self._clone_text_items(self._edit_text_items),
                    self._edit_selected_text_idx,
                ))
                self._restore_edit_snapshot(self._edit_redo.pop())
            self._set_edit_cursor()
            self.update()
        elif name == "取消":
            if self._edit_overlay is not None:
                # 有标注时「取消」= 全部丢弃且撤销栈一起清空，属于不可逆操作；
                # 与「关闭整个截图」共用同一个词，用户无法预判后果，先确认一次。
                if self._edit_undo or self._edit_text_items:
                    from PySide6.QtWidgets import QMessageBox
                    answer = QMessageBox.question(
                        self, "放弃标注？",
                        "将丢弃当前所有标注且无法撤销，确定要返回吗？",
                        QMessageBox.StandardButton.Yes
                        | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No)
                    if answer != QMessageBox.StandardButton.Yes:
                        self._set_edit_cursor()
                        self.update()
                        return
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

    # 小于这个尺寸的截图，Windows OCR / EasyOCR 常常一行都识别不出来
    # （只框 "Portrait" 这种词时用户以为「翻译不显示」，其实是没识别到）。
    _OCR_MIN_W = 240
    _OCR_MIN_H = 80

    def _save_ocr_image(self):
        """OCR 专用图：小选区先高质量放大，保存 / 复制 / 预览仍用原图。

        放大后 recognize_blocks 返回的坐标按放大图宽高归一化，贴回选区时
        仍是同一套 0~1 比例，译文位置不受影响。
        """
        image = self._crop_image()
        if image is None or image.isNull():
            return None
        w, h = image.width(), image.height()
        factor = 1.0
        if w < self._OCR_MIN_W or h < self._OCR_MIN_H:
            factor = min(6.0, max(
                self._OCR_MIN_W / max(1, w),
                self._OCR_MIN_H / max(1, h), 2.0))
        if factor > 1.0:
            image = image.scaled(
                int(w * factor), int(h * factor),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        return self._save_to_temp(image)


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
