#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集中式扁平主题（简约黑夜模式：近黑中性灰画布 + 克制的单色交互）。

设计原则：深色、低饱和、少用彩色。表面/控件全部走中性灰（zinc 系），
主按钮用近白色、选中/聚焦用中性高亮；仅日志的告警/错误与状态圆点保留
功能性颜色。1px 描边、统一圆角、无渐变阴影。
"""
from pathlib import Path

from app_paths import resource_path


class Palette:
    # 画布 / 表面（近黑中性灰，zinc 系）
    BG = "#0b0b0d"
    BG_INPUT = "#202027"
    SURFACE = "#161618"
    SURFACE_HOVER = "#202024"
    SELECTION = "#2e2e33"
    # 边框色提亮：卡片与输入框在深色背景上要能明显看出轮廓
    BORDER = "#4a4a55"
    BORDER_STRONG = "#676775"
    BORDER_SOFT = "#26262c"
    FOCUS = "#8a8a9a"
    # 文本
    TEXT = "#e4e4e7"
    TEXT_STRONG = "#fafafa"
    TEXT_MUTED = "#a1a1aa"
    TEXT_DISABLED = "#52525b"
    # 主按钮（近白，shadcn 风的高对比 primary）
    PRIMARY = "#fafafa"
    PRIMARY_HOVER = "#e4e4e7"
    PRIMARY_PRESSED = "#d4d4d8"
    PRIMARY_TEXT = "#0b0b0d"
    # 危险
    DANGER = "#b91c1c"
    DANGER_HOVER = "#dc2626"
    # qfluent 开关/导航高亮用的"主题色"
    THEME = "#d4d4d8"
    # 状态圆点
    RUNNING = "#4ade80"
    PAUSED = "#eab308"
    STOPPED = "#71717a"
    # 日志
    LOG_BG = "#0a0a0c"
    LOG_TEXT = "#a1a1aa"
    LOG_STATE = "#e4e4e7"
    LOG_WARNING = "#d4a72c"
    LOG_ERROR = "#e5635f"
    # 功能色
    ACCENT = "#60a5fa"


FONT_FAMILIES = [
    "Noto Sans SC", "Noto Sans",
    "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI",
]
_FONT = ", ".join(f"'{f}'" for f in FONT_FAMILIES) + ", sans-serif"


def build_stylesheet() -> str:
    p = Palette
    _assets = resource_path("assets")
    _check_svg = str(_assets / "check.svg").replace("\\", "/")
    _chevron_svg = str(_assets / "chevron-down.svg").replace("\\", "/")
    return f"""
* {{
    font-family: {_FONT};
    outline: none;
}}
QWidget {{
    color: {p.TEXT};
    font-size: 14px;
}}
QToolTip {{
    background: {p.SURFACE};
    color: {p.TEXT};
    border: 1px solid {p.BORDER};
    padding: 4px 8px;
}}

/* ---- 内容画布 ---- */
#homePage, #docPage, #settingsContainer, #avPage {{
    background: {p.BG};
}}
/* 滚动区自身透明，露出页面底色；不要写成实例级 stylesheet，
   否则会向下传播覆盖所有子控件的主题色 */
QScrollArea {{
    background: transparent;
    border: none;
}}

/* ---- 卡片 / 分组 ---- */
QFrame#card {{
    background: {p.SURFACE};
    border: 1px solid {p.BORDER};
    border-radius: 12px;
}}
QFrame#card:hover {{
    border-color: {p.BORDER_STRONG};
}}

/* ---- 设置页右侧导航栏 ---- */
QFrame#settingsNav {{
    background: {p.SURFACE};
    border: none;
    border-left: 1px solid {p.BORDER_SOFT};
}}
QLabel#navHeader {{
    color: {p.TEXT_MUTED};
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1px;
}}

/* ---- 文本层级 ---- */
QLabel#pageTitle {{ color: {p.TEXT_STRONG}; font-size: 22px; font-weight: 700; }}
QLabel#sectionTitle {{ color: {p.TEXT_STRONG}; font-size: 15px; font-weight: 600; }}
QLabel#fieldLabel {{ color: {p.TEXT}; font-size: 13px; }}
QLabel#statTitle {{ color: {p.TEXT_MUTED}; font-size: 12px; }}
QLabel#statValue {{ color: {p.TEXT_STRONG}; font-size: 26px; font-weight: 700; }}
QLabel#caption {{ color: {p.TEXT_MUTED}; font-size: 12px; }}
QLabel#recommend {{
    color: #93c5fd;
    font-size: 12px;
    background: rgba(96, 165, 250, 0.10);
    border: 1px solid rgba(96, 165, 250, 0.28);
    border-radius: 6px;
    padding: 4px 8px;
}}
QLabel#imagePreview {{
    background: {p.BG_INPUT};
    border: 1px dashed {p.BORDER};
    border-radius: 10px;
    color: {p.TEXT_MUTED};
    font-size: 14px;
}}

/* ---- 按钮 ---- */
QPushButton {{
    background: {p.SURFACE};
    color: {p.TEXT};
    border: 1px solid {p.BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 14px;
}}
QPushButton:hover {{ background: {p.SURFACE_HOVER}; border-color: {p.BORDER_STRONG}; }}
QPushButton:pressed {{ background: {p.BG}; }}
QPushButton:disabled {{ color: {p.TEXT_DISABLED}; background: {p.SURFACE}; border-color: {p.BORDER_SOFT}; }}

QPushButton[kind="primary"] {{ background: {p.PRIMARY}; border: 1px solid {p.PRIMARY}; color: {p.PRIMARY_TEXT}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {p.PRIMARY_HOVER}; border-color: {p.PRIMARY_HOVER}; }}
QPushButton[kind="primary"]:pressed {{ background: {p.PRIMARY_PRESSED}; border-color: {p.PRIMARY_PRESSED}; }}
QPushButton[kind="primary"]:disabled {{ background: {p.SELECTION}; border-color: {p.SELECTION}; color: {p.TEXT_DISABLED}; }}

QPushButton[kind="danger"] {{ background: {p.DANGER}; border: 1px solid {p.DANGER}; color: #ffffff; font-weight: 600; }}
QPushButton[kind="danger"]:hover {{ background: {p.DANGER_HOVER}; border-color: {p.DANGER_HOVER}; }}

QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid {p.BORDER}; }}
QPushButton[kind="ghost"]:hover {{ background: {p.SURFACE}; border-color: {p.BORDER_STRONG}; }}

QPushButton[kind="success"] {{ background: {p.RUNNING}; border: 1px solid {p.RUNNING}; color: {p.PRIMARY_TEXT}; font-weight: 600; }}
QPushButton[kind="success"]:hover {{ background: #22c55e; border-color: #22c55e; }}

/* ---- 输入框 ---- */
QLineEdit {{
    background: {p.BG_INPUT};
    color: {p.TEXT};
    border: 1px solid {p.BORDER_STRONG};
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: {p.SELECTION};
    selection-color: {p.TEXT_STRONG};
}}
QLineEdit:hover {{ border-color: {p.BORDER_STRONG}; }}
QLineEdit:focus {{ border: 1px solid {p.FOCUS}; }}
QLineEdit:disabled {{ color: {p.TEXT_DISABLED}; }}

/* ---- 下拉框 ---- */
/* 右侧 padding 给箭头留位，避免长文本压到箭头下；与按钮统一高度 */
QComboBox {{
    background: {p.BG_INPUT};
    color: {p.TEXT};
    border: 1px solid {p.BORDER_STRONG};
    border-radius: 8px;
    padding: 6px 34px 6px 10px;
    font-size: 14px;
}}
QComboBox:hover {{ border-color: {p.FOCUS}; }}
QComboBox:focus, QComboBox:on {{ border-color: {p.FOCUS}; }}
QComboBox:disabled {{
    color: {p.TEXT_DISABLED};
    border-color: {p.BORDER_SOFT};
    background: {p.SURFACE};
}}
QComboBox::drop-down {{
    subcontrol-origin: border;
    subcontrol-position: center right;
    border: none;
    background: transparent;
    width: 28px;
}}
/* Qt 的 QSS 不支持用 transparent 边框画三角形，会渲染成实心白块；改用 SVG。
   靠右定位交给 ::drop-down，这里只画图标。切勿再给 ::down-arrow 补任何带
   伪状态的重复规则（例如 :disabled::down-arrow）：Qt 会把那条 image 当作
   第二个箭头画在框正中央，且正常状态下也会出现「中间大 V + 右侧小 v」。
   disabled 状态会继承本规则的 image，本来就不需要另写。 */
QComboBox::down-arrow {{
    image: url({_chevron_svg});
    width: 12px;
    height: 12px;
    border: none;
}}
QComboBox:on {{
    border: 1px solid {p.FOCUS};
    background: {p.BG_INPUT};
}}
QComboBox QFrame {{
    background: {p.SURFACE};
    border: 1px solid {p.BORDER};
}}
QComboBox QAbstractScrollArea {{
    background: {p.SURFACE};
    border: none;
}}
QComboBox QScrollBar {{
    background: {p.SURFACE};
}}
QComboBox QAbstractItemView {{
    background: {p.SURFACE};
    color: {p.TEXT};
    border: 1px solid {p.BORDER};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {p.SELECTION};
    selection-color: {p.TEXT_STRONG};
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    background: {p.SURFACE};
    color: {p.TEXT};
    padding: 5px 8px;
    border: none;
}}
QComboBox QAbstractItemView::item:selected {{
    background: {p.SELECTION};
    color: {p.TEXT_STRONG};
}}
QComboBox QTreeView {{
    background: {p.SURFACE};
    color: {p.TEXT};
    border: none;
}}
QComboBox QTreeView::item {{
    background: {p.SURFACE};
    color: {p.TEXT};
}}
QComboBox QTreeView::item:selected {{
    background: {p.SELECTION};
    color: {p.TEXT_STRONG};
}}

/* ---- 文本编辑区 ---- */
QTextEdit {{
    background: {p.BG_INPUT};
    color: {p.TEXT};
    border: 1px solid {p.BORDER_STRONG};
    border-radius: 8px;
    padding: 8px;
    selection-background-color: {p.SELECTION};
    selection-color: {p.TEXT_STRONG};
    font-size: 13px;
}}
QTextEdit:focus {{ border: 1px solid {p.FOCUS}; }}

/* ---- 进度条 ---- */
QProgressBar {{
    background: {p.BG_INPUT};
    border: 1px solid {p.BORDER};
    border-radius: 6px;
    height: 8px;
    text-align: center;
    color: {p.TEXT_MUTED};
    font-size: 11px;
}}
QProgressBar::chunk {{
    background: {p.PRIMARY};
    border-radius: 5px;
}}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p.BORDER_STRONG}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.BORDER}; border-radius: 5px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {p.BORDER_STRONG}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---- 分段按钮组 ---- */
QFrame#segmented {{
    background: {p.BG_INPUT};
    border: 1px solid {p.BORDER};
    border-radius: 9px;
}}
QPushButton#segment {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px 12px;
    color: {p.TEXT_MUTED};
    font-size: 13px;
    min-height: 22px;
}}
QPushButton#segment:hover {{ color: {p.TEXT}; }}
QPushButton#segment:checked {{ background: {p.SELECTION}; color: {p.TEXT_STRONG}; font-weight: 600; }}

/* ---- 复选框 ---- */
QCheckBox {{
    color: {p.TEXT};
    spacing: 8px;
    font-size: 14px;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 1px solid {p.BORDER_STRONG};
    border-radius: 4px;
    background: {p.BG_INPUT};
}}
QCheckBox::indicator:hover {{ border-color: {p.FOCUS}; }}
QCheckBox::indicator:checked {{
    background: {p.PRIMARY};
    border-color: {p.PRIMARY};
    image: url({_check_svg});
}}
QCheckBox::indicator:checked:hover {{
    background: {p.PRIMARY_HOVER};
    border-color: {p.PRIMARY_HOVER};
    image: url({_check_svg});
}}
"""


def install_combo_wheel_filter(app) -> None:
    """全局禁用「鼠标滚轮改变下拉框选项」——滚动页面时极易误触。

    用应用级事件过滤器统一处理，覆盖所有页面 / 浮层里的 QComboBox；
    下拉列表展开时仍允许滚轮浏览选项，并把外层滚动区的滚动保留下来。
    """
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import (QAbstractScrollArea, QApplication,
                                  QComboBox)

    class _ComboWheelFilter(QObject):
        @staticmethod
        def _combo_of(obj):
            cur = obj
            while cur is not None:
                if isinstance(cur, QComboBox):
                    return cur
                cur = cur.parent()
            return None

        @staticmethod
        def _scroll_of(widget):
            cur = widget.parent()
            while cur is not None:
                if isinstance(cur, QAbstractScrollArea):
                    return cur
                cur = cur.parent()
            return None

        def eventFilter(self, obj, event):
            if event.type() != QEvent.Type.Wheel:
                return False
            combo = self._combo_of(obj)
            if combo is None or not combo.isEnabled():
                return False
            try:
                if combo.view() is not None and combo.view().isVisible():
                    return False    # 展开的列表要能滚
            except Exception:
                pass
            # 滚轮仍然可以滚动外层页面，只是不能改选项
            scroll = self._scroll_of(combo)
            if scroll is not None:
                try:
                    vp = scroll.viewport()
                    sb = scroll.verticalScrollBar()
                    if vp is not None and sb is not None and sb.isVisible():
                        QApplication.sendEvent(vp, event)
                except Exception:
                    pass
            return True             # 吞掉，绝不让 QComboBox 改值

    if getattr(app, '_combo_wheel_filter', None) is None:
        filt = _ComboWheelFilter(app)
        app.installEventFilter(filt)
        app._combo_wheel_filter = filt   # 保持引用，避免被 GC


def apply_theme(app, window=None) -> None:
    """设置默认字体、Fluent 暗色 + 中性主题色，套用扁平 QSS。"""
    from PySide6.QtGui import QFont
    from qfluentwidgets import Theme, setTheme, setThemeColor

    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    app.setFont(font)

    setTheme(Theme.DARK)
    setThemeColor(Palette.THEME)
    app.setStyleSheet(build_stylesheet())
    install_combo_wheel_filter(app)
    if window is not None:
        try:
            window.setMicaEffectEnabled(False)
        except Exception:
            pass
