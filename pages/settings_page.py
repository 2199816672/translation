#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置页：翻译 API、密钥、AI 润色、OCR 引擎、快捷键与自动化。"""
import threading

from PySide6.QtCore import Qt, Signal, Slot, QTimer, QUrl
from PySide6.QtGui import (QColor, QDesktopServices, QKeySequence, QPalette,
                           QShortcut)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QProgressBar, QScrollArea,
    QPlainTextEdit, QSizePolicy, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from config import (
    AI_POLISH_PROVIDERS, CONFIG_FILE, DEFAULT_AI_POLISH_SETTINGS,
    DEFAULT_ASR_SETTINGS, DEFAULT_LIVE_SETTINGS, DEFAULT_TRANSLATION_API,
    DEFAULT_SUBTITLE_SETTINGS, SUBTITLE_AUDIO_SOURCES, SUBTITLE_ENGINES,
    SUBTITLE_DISPLAY_MODES,
    TRANSLATION_APIS, FREE_APIS, FREE_API_KEY_FIELDS, OCR_ENGINE,
    SUPPORTED_OCR_ENGINES, get_asr_settings, get_live_settings,
    get_subtitle_settings, load_user_config, save_user_config, APP_VERSION,
)
from widgets import HotKeyInput, FlatCheckBox
from theme import Palette
from hotkeys import (DEFAULT_HOTKEYS, HOTKEY_ACTIONS,
                     LOCAL_HOTKEY_ACTIONS, all_hotkeys, config_key,
                     default_hotkeys, display_hotkey, find_conflicts,
                     get_hotkeys, normalize, reserved_conflicts)
from user_messages import friendly_install_error, friendly_model_error
from task_center import TASK_CENTER
import ui_feedback


def _safe_int(cfg, key, default):
    """手改 / 旧版遗留的非法数值不能让设置页构建失败导致整个应用起不来。"""
    try:
        return int(float(cfg.get(key, default)))
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(cfg, key, default):
    try:
        value = float(cfg.get(key, default))
        return value if value == value else default
    except (TypeError, ValueError, OverflowError):
        return default


_SECRET_LABEL_WORDS = ('key', 'secret', 'token', 'cookie',
                       'appid', 'access id', '密钥')


def _is_secret_label(text):
    """按字段标签判断是否凭证输入，避免把 Region / API 地址也设成密文。"""
    low = str(text or '').lower()
    return any(word in low for word in _SECRET_LABEL_WORDS)


class _Section(QFrame):
    """带标题/副标题的设置分组卡片：标签列右对齐，控件列自适应宽度。"""

    def __init__(self, title, subtitle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._title_text = title or ''
        self._subtitle_text = subtitle or ''
        self._rows = []          # [(该行控件列表, 可搜索文本)]
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 20)
        outer.setSpacing(12)

        head = QVBoxLayout()
        head.setSpacing(2)
        heading = QLabel(title, self)
        heading.setObjectName("sectionTitle")
        head.addWidget(heading)
        if subtitle:
            sub = QLabel(subtitle, self)
            sub.setObjectName("caption")
            sub.setWordWrap(True)
            head.addWidget(sub)
            heading.setToolTip(subtitle)
            sub.setToolTip(subtitle)
        outer.addLayout(head)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(12)
        self.grid.setColumnMinimumWidth(0, 92)
        self.grid.setColumnStretch(1, 1)
        outer.addLayout(self.grid)
        self._row = 0

    @staticmethod
    def _search_text(widget):
        """收集一行的可搜索文本（不把用户输入的密钥纳入搜索）。"""
        parts = []
        if isinstance(widget, QLabel):
            parts.append(widget.text())
        if isinstance(widget, QPushButton):
            parts.append(widget.text())
        if isinstance(widget, QComboBox):
            parts.append(widget.currentText())
        if isinstance(widget, QLineEdit):
            parts.append(widget.placeholderText())
        try:
            parts.append(widget.toolTip())
        except Exception:
            pass
        for child in widget.findChildren(QWidget):
            if isinstance(child, QLabel):
                parts.append(child.text())
            elif isinstance(child, QPushButton):
                parts.append(child.text())
            elif isinstance(child, QComboBox):
                parts.append(child.currentText())
        return ' '.join(str(p) for p in parts if p)

    def add(self, label, widget, tip=None):
        """一行：标签 + 控件。"""
        lbl = QLabel(label)
        lbl.setObjectName("fieldLabel")
        if tip:
            lbl.setToolTip(tip)
            widget.setToolTip(tip)
        self.grid.addWidget(
            lbl, self._row, 0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if isinstance(widget, QLineEdit):
            widget.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.grid.addWidget(widget, self._row, 1)
        self._rows.append(([lbl, widget],
                           f"{label} {tip or ''} {self._search_text(widget)}"))
        self._row += 1

    def add_row(self, widget, span=2):
        """占满整行（如子面板、状态行、按钮行）。"""
        self.grid.addWidget(widget, self._row, 0, 1, span)
        self._rows.append(([widget], self._search_text(widget)))
        self._row += 1

    def add_caption(self, text):
        cap = QLabel(text, self)
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        self.grid.addWidget(cap, self._row, 0, 1, 2)
        self._rows.append(([cap], text))
        self._row += 1
        return cap

    def apply_filter(self, keyword):
        """按关键字过滤本分区的设置行，返回命中行数；空关键字恢复全部。"""
        kw = str(keyword or '').strip().lower()
        if not kw:
            for widgets, _ in self._rows:
                for w in widgets:
                    w.setVisible(True)
            self.setVisible(True)
            return len(self._rows)
        head_hit = kw in (
            f"{self._title_text} {self._subtitle_text}").lower()
        matched = 0
        for widgets, text in self._rows:
            hit = head_hit or kw in str(text).lower()
            for w in widgets:
                w.setVisible(hit)
            if hit:
                matched += 1
        self.setVisible(matched > 0)
        return matched


class _NavItem(QFrame):
    """右侧导航栏的一个条目：左侧重音条 + 标题，点击滚动到对应分区。"""

    clicked = Signal(int)

    def __init__(self, index, text, parent=None):
        super().__init__(parent)
        self._index = index
        self._active = False
        self._hover = False
        self.setObjectName("navItem")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._bar = QFrame(self)
        self._bar.setObjectName("navBar")
        self._bar.setFixedWidth(3)
        lay.addWidget(self._bar)

        self._label = QLabel(text, self)
        self._label.setObjectName("navLabel")
        self._label.setContentsMargins(16, 0, 16, 0)
        self._label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._label, 1)
        self._apply_style()

    def _apply_style(self):
        p = Palette
        if self._active:
            bg, fg, bar, weight = p.SELECTION, p.TEXT_STRONG, p.ACCENT, "600"
        elif self._hover:
            bg, fg, bar, weight = p.SURFACE_HOVER, p.TEXT, p.BORDER_SOFT, "400"
        else:
            bg, fg, bar, weight = "transparent", p.TEXT_MUTED, "transparent", "400"
        self.setStyleSheet(f"background: {bg};")
        self._label.setStyleSheet(f"color: {fg}; font-weight: {weight};")
        self._bar.setStyleSheet(f"background: {bar};")

    def enterEvent(self, event):
        self._hover = True
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._apply_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.clicked.emit(self._index)
        super().mousePressEvent(event)

    def set_active(self, active):
        if self._active == active:
            return
        self._active = active
        self._apply_style()


class SettingsPage(QWidget):
    saved = Signal()
    _sig_install_progress = Signal(str)
    _sig_install_result = Signal(bool, str)
    _sig_polish_test = Signal(bool, str)
    _sig_caption_install = Signal(bool, str)
    _sig_caption_model = Signal(bool, str)
    _sig_model_progress = Signal(int, int)
    _sig_translation_test = Signal(bool, str)
    _sig_subtitle_test = Signal(bool, str)

    # 同一个按钮兼任「开始」与「取消」：进行中点它就中断，避免用户被
    # 一个 100MB 的下载 / 数百 MB 的 pip 安装锁住几分钟。
    _TASK_IDLE_TEXT = {
        'ocr': '安装',
        'loopback': '安装组件',
        'model': '下载所选模型',
    }
    _TASK_NAMES = {
        'ocr': '安装 OCR 组件',
        'loopback': '安装音频采集组件',
        'model': '下载字幕模型',
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsContainer")
        # 普通 QWidget 需要显式开启才会绘制 QSS 的 background
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._cfg = load_user_config()
        # 保存基线：写盘时只提交相对它变化的键，避免覆盖其它页面刚写入的配置
        self._saved_cfg = dict(self._cfg)
        self._task_cancel = {}      # key -> threading.Event
        self._task_busy = set()     # 正在跑的 key
        self._task_buttons = {}     # key -> 按钮
        self._sig_install_progress.connect(self._update_install_progress)
        self._sig_install_result.connect(self._on_install_result)
        self._sig_polish_test.connect(self._on_polish_test_result)
        self._sig_caption_install.connect(self._on_caption_install_done)
        self._sig_caption_model.connect(self._on_caption_model_done)
        self._sig_model_progress.connect(self._on_model_progress)
        self._sig_translation_test.connect(self._on_translation_test_result)
        self._sig_subtitle_test.connect(self._on_subtitle_test_result)
        self._build_ui()

    # ── 可取消的安装 / 下载 ──────────────────────────────────

    def _task_toggle(self, key, start_fn, busy_text="取消安装"):
        """同一个按钮：空闲时开始任务，运行中再点则立即取消。"""
        btn = self._task_buttons.get(key)
        if btn is None:
            return
        if key in self._task_busy:
            event = self._task_cancel.get(key)
            if event is not None:
                event.set()
            btn.setEnabled(False)
            btn.setText("正在取消…")
            return
        event = threading.Event()
        self._task_cancel[key] = event
        self._task_busy.add(key)
        TASK_CENTER.start(key, self._TASK_NAMES.get(key, key),
                          cancelable=True, cancel=event, detail="进行中")
        btn.setText(busy_text)
        btn.setToolTip("点这里立即中断当前操作；已下载的文件会保留，下次自动续传")
        try:
            start_fn(event)
        except Exception as exc:
            # 启动就失败也要把按钮还原，否则永久停在「取消安装」
            self._task_finish(key)
            QMessageBox.warning(self, "无法开始", str(exc))

    def _task_was_cancelled(self, key):
        event = self._task_cancel.get(key)
        return bool(event is not None and event.is_set())

    def _task_finish(self, key, idle_text=None):
        """收尾并还原按钮，返回这次是否由用户取消。"""
        TASK_CENTER.finish(key)
        cancelled = self._task_was_cancelled(key)
        self._task_busy.discard(key)
        self._task_cancel.pop(key, None)
        btn = self._task_buttons.get(key)
        if btn is not None:
            btn.setEnabled(True)
            btn.setText(idle_text or self._TASK_IDLE_TEXT.get(key, '重试'))
            btn.setToolTip("")
        return cancelled

    def cancel_running_tasks(self):
        """程序退出前调用：中断仍在跑的安装 / 下载，别留下 pip 子进程。"""
        for event in list(self._task_cancel.values()):
            event.set()
        TASK_CENTER.cancel_all()

    # ── 构建界面 ────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 可滚动内容区
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setObjectName("settingsScroll")
        # 窄窗口允许横向滚动，避免内容被硬裁
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 不要在这里调 setStyleSheet：实例级样式表会传播到所有子控件，
        # 把卡片 / 输入框 / 按钮的主题背景色全部覆盖成默认浅色。
        container = QWidget()
        container.setAutoFillBackground(True)
        c_pal = container.palette()
        c_pal.setColor(QPalette.ColorRole.Window, QColor(Palette.BG))
        container.setPalette(c_pal)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(16)

        head = QVBoxLayout()
        head.setSpacing(2)
        title = QLabel("设置", self)
        title.setObjectName("pageTitle")
        head.addWidget(title)
        subtitle = QLabel(
            "开箱即用，默认无需填密钥 · 翻译引擎、AI 润色、OCR、字幕、"
            "快捷键与启动选项", self)
        subtitle.setObjectName("caption")
        head.addWidget(subtitle)
        layout.addLayout(head)

        # 配置健康卡：配置损坏 / 数据目录降级时持久显示，不被滚动冲掉
        self._health_bar = QFrame()
        self._health_bar.setObjectName("card")
        self._health_bar.setVisible(False)
        health_lay = QHBoxLayout(self._health_bar)
        health_lay.setContentsMargins(14, 10, 14, 10)
        health_lay.setSpacing(10)
        self._health_label = QLabel()
        self._health_label.setWordWrap(True)
        self._health_label.setStyleSheet("color: #eab308;")
        health_lay.addWidget(self._health_label, 1)
        health_btn = QPushButton("打开目录", self._health_bar)
        health_btn.setProperty("kind", "ghost")
        health_btn.clicked.connect(self._open_health_dir)
        health_lay.addWidget(health_btn)
        layout.addWidget(self._health_bar)
        self._refresh_health()

        self._sections = []
        for build in (
                self._build_translation_section,
                self._build_polish_section,
                self._build_ocr_section,
                self._build_asr_section,
                self._build_live_section,
                self._build_subtitle_section,
                self._build_hotkey_section,
                self._build_general_section,
                self._build_auto_section,
                self._build_checklist_section,
                self._build_diagnostics_section):
            section = build()
            self._sections.append(section)
            layout.addWidget(section)
        layout.addStretch(1)
        self._scroll.setWidget(container)

        # 主内容列（搜索条 + 滚动区 + 底栏），右侧再拼一个导航栏
        main_col = QWidget(self)
        main_col.setObjectName("settingsMain")
        main_layout = QVBoxLayout(main_col)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 固定在页面顶部的搜索条：滚到哪里都能直接找设置
        search_bar = QWidget(self)
        search_bar.setObjectName("settingsSearchBar")
        # 用 palette 而非实例级 stylesheet：后者会向下传播洗掉子控件主题色
        bar_pal = search_bar.palette()
        bar_pal.setColor(QPalette.ColorRole.Window, QColor(Palette.BG))
        search_bar.setPalette(bar_pal)
        search_bar.setAutoFillBackground(True)
        search_row = QHBoxLayout(search_bar)
        search_row.setContentsMargins(28, 10, 28, 4)
        search_row.setSpacing(10)
        self._search_edit = QLineEdit(search_bar)
        self._search_edit.setObjectName("settingsSearch")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setPlaceholderText(
            "搜索设置：Ctrl+F 聚焦 · 输入关键字即时筛选"
            "（如 字幕、快捷键、OCR、模型、密钥…）")
        self._search_edit.setMinimumHeight(32)
        self._search_edit.textChanged.connect(self._apply_search)
        search_row.addWidget(self._search_edit, 1)
        self._search_hint = QLabel("共 %d 个设置分区" % len(self._sections),
                                   search_bar)
        self._search_hint.setObjectName("caption")
        search_row.addWidget(self._search_hint)
        main_layout.addWidget(search_bar)
        main_layout.addWidget(self._scroll, 1)
        # Ctrl+F 直接聚焦搜索框
        QShortcut(QKeySequence('Ctrl+F'), self).activated.connect(
            self.focus_search)
        # 搜索到一半想退出：Esc 直接清空，不用手动删字符
        QShortcut(QKeySequence('Esc'), self._search_edit).activated.connect(
            self._search_edit.clear)

        # 固定底栏：保存按钮始终可见
        footer = QWidget(self)
        foot_pal = footer.palette()
        foot_pal.setColor(QPalette.ColorRole.Window, QColor(Palette.BG))
        footer.setPalette(foot_pal)
        footer.setAutoFillBackground(True)
        foot_row = QHBoxLayout(footer)
        foot_row.setContentsMargins(28, 10, 28, 16)
        foot_row.setSpacing(12)
        hint = QLabel("修改后点右侧「保存设置」生效 · 字幕外观即时生效", footer)
        hint.setObjectName("caption")
        foot_row.addWidget(hint)
        foot_row.addStretch(1)
        self._reset_btn = QPushButton("恢复默认", footer)
        self._reset_btn.setProperty("kind", "ghost")
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.setToolTip("把本页所有设置恢复为默认值（点击保存后生效）")
        self._reset_btn.clicked.connect(self._restore_defaults)
        foot_row.addWidget(self._reset_btn)
        self._save_btn = QPushButton("保存设置", footer)
        self._save_btn.setProperty("kind", "primary")
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.setToolTip("保存全部设置（快捷键、翻译引擎、OCR、语音识别等）")
        self._save_btn.clicked.connect(self._save)
        foot_row.addWidget(self._save_btn)
        main_layout.addWidget(footer)

        # 右侧导航栏：目录式跳转 + 滚动高亮
        self._nav = self._build_nav()
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(main_col, 1)
        body.addWidget(self._nav, 0)
        outer.addLayout(body, 1)

        self._scroll.verticalScrollBar().valueChanged.connect(
            self._on_scroll_changed)
        self._set_active_nav(0)

    def focus_search(self):
        self._search_edit.setFocus()
        self._search_edit.selectAll()

    # ── 右侧导航栏 ───────────────────────────────────────────

    def _build_nav(self):
        """目录式导航：列出所有分区，点击滚动到对应位置。"""
        nav = QFrame(self)
        nav.setObjectName("settingsNav")
        nav.setFixedWidth(196)
        lay = QVBoxLayout(nav)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QLabel("设置导航", nav)
        header.setObjectName("navHeader")
        header.setContentsMargins(16, 16, 16, 10)
        lay.addWidget(header)

        self._nav_items = []
        for i, section in enumerate(self._sections):
            item = _NavItem(i, section._title_text or f"分区 {i + 1}", nav)
            item.clicked.connect(self._on_nav_clicked)
            lay.addWidget(item)
            self._nav_items.append(item)
        lay.addStretch(1)
        return nav

    @Slot(int)
    def _on_nav_clicked(self, index):
        if not (0 <= index < len(self._sections)):
            return
        # 若正在搜索，先清空搜索让目标分区显示出来，再滚动过去
        if self._search_edit.text().strip():
            self._search_edit.clear()
        section = self._sections[index]
        sb = self._scroll.verticalScrollBar()

        # 立即高亮被点击项：若目标分区已在可视区（ensureWidgetVisible 不触发
        # 滚动），valueChanged 不会回调，不主动设置高亮就会停留在旧项上
        self._set_active_nav(index)

        def _reveal():
            self._scroll.ensureWidgetVisible(section)
            w = self._scroll.widget()
            if w is not None and w.layout() is not None:
                w.layout().activate()
            sb.setValue(max(0, section.pos().y() - 12))
            # 滚动后再次确认高亮（valueChanged 可能因值未变而不触发）
            self._set_active_nav(index)

        QTimer.singleShot(0, _reveal)

    @Slot(int)
    def _on_scroll_changed(self, value):
        sections = getattr(self, "_sections", [])
        nav_items = getattr(self, "_nav_items", [])
        if not nav_items:
            return
        sb = self._scroll.verticalScrollBar()
        visible = [i for i, s in enumerate(sections)
                   if s.isVisible()
                   and i < len(nav_items) and nav_items[i].isVisible()]
        if not visible:
            return
        # 当前视口顶部所在分区：最后一个顶部已滚过的分区
        # 容差取 16px：点击跳转时会把分区顶贴到视口顶部（含 12px 余量），
        # 若只看 value+1 会把「上一分区」误判为当前，导致高亮错位
        best = visible[0]
        for i in visible:
            if sections[i].pos().y() <= value + 16:
                best = i
        # 已滚到底：矮分区顶不到顶部，强制高亮最后一个可见分区
        if sb.value() >= sb.maximum():
            best = visible[-1]
        self._set_active_nav(best)

    def _set_active_nav(self, index):
        for i, item in enumerate(getattr(self, "_nav_items", [])):
            item.set_active(i == index)

    @Slot(str)
    def _apply_search(self, keyword):
        """按关键字过滤设置行；命中分区自动滚到可视区域。"""
        kw = str(keyword or '').strip()
        total = 0
        shown = 0
        first = None
        for i, section in enumerate(self._sections):
            # 不能靠 isVisible()：非当前导航页的控件在 Qt 里永远是 False
            matched = section.apply_filter(kw)
            if matched > 0:
                shown += 1
                total += matched
                if first is None:
                    first = section
            # 导航栏条目与分区可见性保持同步（搜索时隐藏无匹配的分区）
            if hasattr(self, "_nav_items") and i < len(self._nav_items):
                self._nav_items[i].setVisible(matched > 0)
        if not kw:
            self._search_hint.setText(f"共 {shown} 个设置分区")
        elif first is not None:
            self._search_hint.setText(f"找到 {total} 项 · {shown} 个分区")

            def _reveal(section=first):
                # ensureWidgetVisible 对「比视口还高」的分区会滚到中部，
                # 导致分区标题看不见；再强制对齐到分区顶部。
                self._scroll.ensureWidgetVisible(section)
                lay = self._scroll.widget().layout()
                if lay is not None:
                    lay.activate()
                self._scroll.verticalScrollBar().setValue(
                    max(0, section.pos().y() - 12))

            QTimer.singleShot(0, _reveal)
        else:
            self._search_hint.setText("没有匹配的设置，换个关键词试试")
        self._search_hint.setStyleSheet(
            f"color: {'#eab308' if kw and first is None else Palette.TEXT_MUTED};"
            "border: none; background: transparent;")

    # ── 快捷键 ──────────────────────────────────────────────

    def _build_hotkey_section(self) -> _Section:
        section = _Section(
            "快捷键",
            "分两类：全局（默认全部为空，不占用系统按键，最小化到托盘也能用）、"
            "窗口内（只在本程序有焦点时生效，默认就有组合键）。"
            "点输入框后按下组合键即可录入；按 Delete / Backspace 或点「清除」可清空")
        keys = all_hotkeys(self._cfg)
        self._hotkey_inputs = {}

        def add_group(title, actions):
            section.add_caption("── " + title + " ──")
            for action, (label, _cfg_key, tip) in actions.items():
                row = QWidget()
                lay = QHBoxLayout(row)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(8)
                entry = HotKeyInput(keys.get(action) or '', row)
                entry.setMinimumWidth(200)
                clear_btn = QPushButton("清除", row)
                clear_btn.setProperty("kind", "ghost")
                clear_btn.setFixedWidth(68)
                clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                clear_btn.setToolTip("清空这个快捷键（不占用任何按键）")
                clear_btn.clicked.connect(entry.clear_hotkey)
                lay.addWidget(entry, 1)
                lay.addWidget(clear_btn)
                self._hotkey_inputs[action] = entry
                section.add(label + ":", row, tip=tip)

        section.add_caption(
            "不设快捷键也能用：截图、字幕启停 / 暂停 / 清空等动作都可在"
            "托盘右键菜单触发；这里设置的是可选的快捷组合")
        add_group("全局快捷键 · 任何界面都能触发", HOTKEY_ACTIONS)
        add_group("窗口内快捷键 · 只在本程序窗口内触发",
                  LOCAL_HOTKEY_ACTIONS)
        section.add_caption(
            "冲突提示：同一个组合只能绑定一个功能；Ctrl+F、Ctrl+A、Ctrl+C 等是"
            "程序内固定组合，设成它们会与搜索 / 全选 / 复制打架。组合被其它软件"
            "占用时会提示注册失败，换一个即可。\n"
            "改完点「保存设置」：全局快捷键会重新注册，窗口内快捷键立即生效，都无需重启。\n"
            "「恢复默认」会清空全部全局快捷键，并把窗口内快捷键恢复为默认组合。")
        return section

    # ── 翻译设置 ────────────────────────────────────────────

    def _build_translation_section(self) -> _Section:
        section = _Section("翻译设置", "选择翻译引擎；免费引擎可不填密钥")

        self.api_combo = QComboBox()
        self.api_combo.addItems(list(TRANSLATION_APIS.keys()))
        current_api = self._cfg.get("translation_api", DEFAULT_TRANSLATION_API)
        for name, value in TRANSLATION_APIS.items():
            if value == current_api:
                self.api_combo.setCurrentText(name)
                break
        section.add("翻译 API:", self.api_combo,
                    tip="选择翻译引擎；免费引擎可不填密钥。\n"
                        "切换后会显示对应的密钥输入框。")
        self.api_combo.currentTextChanged.connect(self._on_api_changed)

        # 各引擎密钥输入
        self.bing_azure_key = self._mk_edit(
            "Azure Cognitive Services 订阅密钥（可选）")
        self.bing_azure_key.setText(self._cfg.get("bing_azure_key", ""))
        self.bing_azure_region = self._mk_edit(
            "区域，如 chinaeast2（可选，国内用户可不填）")
        self.bing_azure_region.setText(self._cfg.get("bing_azure_region", ""))
        self.mymemory_api_key = self._mk_edit(
            "mymemory.translated.net 授权密钥（可选）")
        self.mymemory_api_key.setText(self._cfg.get("mymemory_api_key", ""))
        self.google_cloud_key = self._mk_edit(
            "Google Cloud Translation API 密钥（可选）")
        self.google_cloud_key.setText(self._cfg.get("google_cloud_key", ""))
        self.deepl_key = self._mk_edit("DeepL API 密钥 (deepl.com/pro-api)")
        self.deepl_key.setText(self._cfg.get("deepl_api_key", ""))
        self.baidu_appid = self._mk_edit("百度翻译开放平台 AppID")
        self.baidu_appid.setText(self._cfg.get("baidu_appid", ""))
        self.baidu_secret = self._mk_edit("百度翻译 SecretKey")
        self.baidu_secret.setText(self._cfg.get("baidu_secret", ""))
        self.tencent_secret_id = self._mk_edit("腾讯云 API SecretId")
        self.tencent_secret_id.setText(self._cfg.get("tencent_secret_id", ""))
        self.tencent_secret_key = self._mk_edit("腾讯云 API SecretKey")
        self.tencent_secret_key.setText(self._cfg.get("tencent_secret_key", ""))
        self.volcengine_appid = self._mk_edit("火山翻译应用 AppID")
        self.volcengine_appid.setText(self._cfg.get("volcengine_appid", ""))
        # 兼容旧配置键名
        if not self.volcengine_appid.text():
            self.volcengine_appid.setText(self._cfg.get("volcengine_app_id", ""))
        self.volcengine_token = self._mk_edit("火山翻译 Access Token")
        self.volcengine_token.setText(self._cfg.get("volcengine_token", ""))

        self._api_stack = QStackedWidget()
        self._api_container = QWidget()
        api_layout = QVBoxLayout(self._api_container)
        api_layout.setContentsMargins(0, 4, 0, 0)
        api_layout.addWidget(self._api_stack)

        bing_page = self._build_key_page(
            "可选：填写后使用自己的 Azure 密钥，否则使用免费逆向接口",
            [("Azure Key:", self.bing_azure_key,
              "可选：Azure 认知服务订阅密钥，填写后走官方 Azure 接口"),
             ("Region:", self.bing_azure_region,
              "Azure 资源区域（如 chinaeast2），部分密钥必填")])
        self._api_stack.addWidget(bing_page)

        mymemory_page = self._build_key_page(
            "可选：填写后提升每日请求额度（无密钥也可正常使用）",
            [("MyMemory Key:", self.mymemory_api_key,
              "可选：MyMemory 授权密钥，用于提升每日翻译额度")])
        self._api_stack.addWidget(mymemory_page)

        google_page = self._build_key_page(
            "可选：填写后使用 Google Cloud API，否则使用免费接口（需 VPN）",
            [("Google Cloud Key:", self.google_cloud_key,
              "可选：Google Cloud Translation API 密钥")])
        self._api_stack.addWidget(google_page)

        deepl_page = self._build_key_page(
            "DeepL 需付费 API 密钥（deepl.com/pro-api 申请）",
            [("DeepL Key:", self.deepl_key,
              "DeepL 付费 API 密钥；免费版密钥以 :fx 结尾")])
        self._api_stack.addWidget(deepl_page)

        baidu_page = self._build_key_page(
            "百度翻译开放平台申请 AppID 与 SecretKey",
            [("百度 AppID:", self.baidu_appid,
              "百度翻译开放平台的应用 ID（fanyi-api.com）"),
             ("百度 Secret:", self.baidu_secret,
              "百度翻译开放平台的应用密钥")])
        self._api_stack.addWidget(baidu_page)

        tencent_page = self._build_key_page(
            "腾讯云控制台申请 SecretId 与 SecretKey",
            [("腾讯 SecretId:", self.tencent_secret_id,
              "腾讯云访问管理的 API 密钥 ID"),
             ("腾讯 SecretKey:", self.tencent_secret_key,
              "腾讯云访问管理的 API 密钥")])
        self._api_stack.addWidget(tencent_page)

        volc_page = self._build_key_page(
            "火山引擎控制台申请 AppID 与 Access Token",
            [("火山 AppID:", self.volcengine_appid,
              "火山翻译应用的 AppID"),
             ("火山 Token:", self.volcengine_token,
              "火山翻译的 Access Token")])
        self._api_stack.addWidget(volc_page)

        self._api_page_map = {
            "bing_free": 0, "mymemory_free": 1, "google_free": 2,
            "deepl": 3, "baidu": 4, "tencent": 5, "volcengine": 6,
        }
        self._api_container.setVisible(False)
        self._on_api_changed(self.api_combo.currentText())
        section.add_row(self._api_container)

        test_row = QWidget()
        test_lay = QHBoxLayout(test_row)
        test_lay.setContentsMargins(0, 0, 0, 0)
        test_lay.setSpacing(10)
        self.translation_test_btn = QPushButton("测试翻译连接", test_row)
        self.translation_test_btn.setProperty("kind", "ghost")
        self.translation_test_btn.setFixedWidth(130)
        self.translation_test_btn.setCursor(
            Qt.CursorShape.PointingHandCursor)
        self.translation_test_btn.setToolTip(
            "用上面当前填写的引擎和密钥翻译一句短文本，验证是否可用")
        self.translation_test_btn.clicked.connect(self._test_translation)
        test_lay.addWidget(self.translation_test_btn)
        self.translation_test_label = QLabel("", test_row)
        self.translation_test_label.setObjectName("caption")
        self.translation_test_label.setWordWrap(True)
        test_lay.addWidget(self.translation_test_label, 1)
        section.add_row(test_row)

        section.add_caption(
            "免费：Bing 微软 · MyMemory · Google（均可选填自己的密钥）\n"
            "付费：DeepL · 百度 · 腾讯 · 火山（需自备密钥）\n"
            "「测试翻译连接」直接用当前输入框里的值，不必先点保存；"
            "只翻译一句短文本，不会写入配置")
        return section

    # ── AI 润色设置 ─────────────────────────────────────────

    def _build_polish_section(self) -> _Section:
        section = _Section(
            "AI 润色",
            "用大模型把译文 / 字幕改得更通顺；只改表达，不增删原意")

        self.polish_provider_combo = QComboBox()
        for name, value in AI_POLISH_PROVIDERS.items():
            self.polish_provider_combo.addItem(name, value)
        current = str(self._cfg.get("ai_polish_provider", "pollinations"))
        idx = self.polish_provider_combo.findData(current)
        self.polish_provider_combo.setCurrentIndex(max(0, idx))
        self.polish_provider_combo.setMinimumWidth(260)
        section.add(
            "润色通道:", self.polish_provider_combo,
            tip="「免费公共通道」开箱可用；\n"
                "DeepSeek / 豆包网页版免费，但需要浏览器登录后的 Token / Cookie；\n"
                "也可以填自己的 OpenAI 兼容接口。")

        self.polish_deepseek_token = self._mk_edit("DeepSeek userToken")
        self.polish_deepseek_token.setText(
            self._cfg.get("ai_polish_deepseek_token", ""))
        self.polish_doubao_cookie = self._mk_edit("豆包 sessionid Cookie")
        self.polish_doubao_cookie.setText(
            self._cfg.get("ai_polish_doubao_cookie", ""))
        self.polish_api_url = self._mk_edit(
            "API 地址，如 https://api.deepseek.com/v1")
        self.polish_api_url.setText(self._cfg.get("ai_polish_api_url", ""))
        self.polish_api_key = self._mk_edit("API Key")
        self.polish_api_key.setText(self._cfg.get("ai_polish_api_key", ""))
        self.polish_model = self._mk_edit(
            "模型名，如 deepseek-chat / gpt-4o-mini")
        self.polish_model.setText(self._cfg.get("ai_polish_model", ""))

        self._polish_stack = QStackedWidget()
        pollinations_page = QWidget()
        pollinations_layout = QVBoxLayout(pollinations_page)
        pollinations_layout.setContentsMargins(0, 8, 0, 3)
        pollinations_layout.addWidget(self._polish_caption(
            "无需配置，直接可用。公共免费通道有频率限制；"
            "稿件量大或要求更高质量时，建议使用下面自己的 API。"))
        pollinations_layout.addStretch(1)
        self._polish_stack.addWidget(pollinations_page)

        deepseek_page = self._build_key_page(
            "获取 Token：浏览器登录 chat.deepseek.com → F12 → "
            "Application/Storage → Local Storage → 复制 userToken",
            [("Token:", self.polish_deepseek_token,
              "DeepSeek 网页版登录凭证（免费，无需充值 API）")])
        self._polish_stack.addWidget(deepseek_page)

        doubao_page = self._build_key_page(
            "获取 Cookie：浏览器登录 doubao.com → F12 → "
            "Application → Cookies → 复制 sessionid",
            [("Cookie:", self.polish_doubao_cookie,
              "豆包网页版登录凭证（免费，无需充值 API）")])
        self._polish_stack.addWidget(doubao_page)

        custom_page = self._build_key_page(
            "支持任意 OpenAI 兼容接口（DeepSeek / 通义 / Kimi / 本地 Ollama 等）",
            [("API 地址:", self.polish_api_url,
              "接口根地址，程序会自动补 /chat/completions；仅允许公网地址"),
             ("API Key:", self.polish_api_key,
              "自备密钥；本地无鉴权接口可留空"),
             ("模型:", self.polish_model,
              "如 deepseek-chat、gpt-4o-mini、qwen-plus")])
        self._polish_stack.addWidget(custom_page)

        section.add_row(self._polish_stack)
        self.polish_provider_combo.currentIndexChanged.connect(
            self._on_polish_provider_changed)
        self._on_polish_provider_changed()

        test_row = QWidget(self)
        test_layout = QHBoxLayout(test_row)
        test_layout.setContentsMargins(0, 4, 0, 0)
        test_layout.setSpacing(12)
        self.polish_test_btn = QPushButton("测试润色")
        self.polish_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.polish_test_btn.setToolTip("用当前通道润色一句示例文本，验证配置是否可用")
        self.polish_test_btn.clicked.connect(self._test_polish)
        test_layout.addWidget(self.polish_test_btn)
        self.polish_test_label = QLabel("")
        self.polish_test_label.setObjectName("caption")
        self.polish_test_label.setWordWrap(True)
        test_layout.addWidget(self.polish_test_label, 1)
        section.add_row(test_row)

        section.add_caption(
            "润色只修正错别字、语病、标点和不顺的表达，不改变原意、不增删内容；\n"
            "程序会清洗模型返回的说明文字，字幕批量润色还会校验条数，"
            "条数不一致自动逐条重试。\n"
            "音视频页可先「润色原文」再翻译，翻译后再「润色译文」，两次把关更准确。")
        return section

    def _polish_caption(self, text):
        label = QLabel(text)
        label.setObjectName("caption")
        label.setWordWrap(True)
        return label

    def _on_polish_provider_changed(self, _index=None):
        if hasattr(self, "_polish_stack"):
            self._polish_stack.setCurrentIndex(
                self.polish_provider_combo.currentIndex())

    def _collect_polish_settings(self):
        return {
            "ai_polish_provider": str(
                self.polish_provider_combo.currentData() or "pollinations"),
            "ai_polish_deepseek_token": self.polish_deepseek_token.text().strip(),
            "ai_polish_doubao_cookie": self.polish_doubao_cookie.text().strip(),
            "ai_polish_api_url": self.polish_api_url.text().strip(),
            "ai_polish_api_key": self.polish_api_key.text().strip(),
            "ai_polish_model": self.polish_model.text().strip(),
        }

    def _test_polish(self):
        settings = self._collect_polish_settings()
        provider_name = self.polish_provider_combo.currentText()
        self.polish_test_btn.setEnabled(False)
        self.polish_test_btn.setText("测试中…")
        self.polish_test_label.setStyleSheet(
            f"color: {Palette.TEXT_MUTED};")
        self.polish_test_label.setText(f"正在测试：{provider_name}")

        def run():
            try:
                from ai_polish import test_polish
                out = test_polish(settings)
                self._sig_polish_test.emit(True, out)
            except Exception as e:
                self._sig_polish_test.emit(False, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_polish_test_result(self, ok, msg):
        self.polish_test_btn.setEnabled(True)
        self.polish_test_btn.setText("测试润色")
        from qfluentwidgets import InfoBar, InfoBarPosition
        if ok:
            self.polish_test_label.setText(f"✅ 可用：{msg[:60]}")
            self.polish_test_label.setStyleSheet("color: #4ade80;")
            InfoBar.success("润色通道可用", msg[:120], duration=3000,
                            position=InfoBarPosition.TOP, parent=self)
        else:
            self.polish_test_label.setText(f"⚠ {msg[:120]}")
            self.polish_test_label.setStyleSheet("color: #e5635f;")
            InfoBar.error("润色失败", msg[:240], duration=6000,
                          position=InfoBarPosition.TOP, parent=self)

    # ── 连接测试 ────────────────────────────────────────────

    def _test_translation(self):
        """用当前输入框里的引擎 / 密钥试翻一句，不必先点保存。"""
        api_name = self.api_combo.currentText()
        api_type = TRANSLATION_APIS.get(api_name, DEFAULT_TRANSLATION_API)
        keys = {
            'translation_api': api_type,
            'bing_azure_key': self.bing_azure_key.text().strip(),
            'bing_azure_region': self.bing_azure_region.text().strip(),
            'mymemory_api_key': self.mymemory_api_key.text().strip(),
            'google_cloud_key': self.google_cloud_key.text().strip(),
            'deepl_api_key': self.deepl_key.text().strip(),
            'baidu_appid': self.baidu_appid.text().strip(),
            'baidu_secret': self.baidu_secret.text().strip(),
            'tencent_secret_id': self.tencent_secret_id.text().strip(),
            'tencent_secret_key': self.tencent_secret_key.text().strip(),
            'volcengine_app_id': self.volcengine_appid.text().strip(),
            'volcengine_token': self.volcengine_token.text().strip(),
        }
        self.translation_test_btn.setEnabled(False)
        self.translation_test_btn.setText("测试中…")
        # 测试中锁定引擎下拉：切换会触发 _on_api_changed 清掉「正在测试」
        # 文案，用户也分不清结果对应哪个引擎。
        self.api_combo.setEnabled(False)
        self.translation_test_label.setStyleSheet("")
        self.translation_test_label.setText(
            f"正在测试：{api_name}（网络慢时可能要等半分钟）")

        def run():
            try:
                from translator import test_translation
                ok, msg = test_translation(api_type, keys)
                # 结果里带上引擎名，切换引擎后仍能分辨是哪一次测试
                msg = f"[{api_name}] {msg}"
            except Exception as exc:
                ok, msg = False, f"测试组件加载失败：{exc}"
            self._sig_translation_test.emit(ok, msg)

        threading.Thread(target=run, daemon=True).start()

    def _on_translation_test_result(self, ok, msg):
        self.api_combo.setEnabled(True)
        self.translation_test_btn.setEnabled(True)
        self.translation_test_btn.setText("测试翻译连接")
        self.translation_test_label.setText(("✅ " if ok else "⚠ ") + msg)
        self.translation_test_label.setStyleSheet(
            "color: #4ade80;" if ok else "color: #e5635f;")
        from qfluentwidgets import InfoBar, InfoBarPosition
        (InfoBar.success if ok else InfoBar.warning)(
            "翻译服务可用" if ok else "翻译服务不可用", msg,
            duration=3500 if ok else 6000,
            position=InfoBarPosition.TOP, parent=self)

    def _test_subtitle_service(self):
        """只探测识别服务连通性 / 密钥，不上传音频、不写配置。"""
        engine = str(self.subtitle_engine_combo.currentData() or 'local')
        if engine == 'local':
            self.subtitle_test_label.setText(
                "当前用本地模型，无需测试服务连接")
            return
        url = self.subtitle_api_url.text().strip()
        if not url:
            self.subtitle_test_label.setText("⚠ 请先填写服务地址")
            self.subtitle_test_label.setStyleSheet("color: #e5635f;")
            return
        key = self.subtitle_api_key.text().strip()
        self.subtitle_test_btn.setEnabled(False)
        self.subtitle_test_btn.setText("测试中…")
        # 测试中锁定识别引擎：切换会触发 _refresh_subtitle_model_status，
        # 它会把「测试中…」按钮重新启用，用户就能发起第二次测试。
        self.subtitle_engine_combo.setEnabled(False)
        self.subtitle_test_label.setStyleSheet("")
        self.subtitle_test_label.setText("正在连接识别服务…（最多 6 秒）")

        def run():
            try:
                import speech_recognizer as asr
                ok, msg = asr.test_api_connection(
                    url, key, allow_private=(engine == 'local_service'))
            except Exception as exc:
                ok, msg = False, f"测试失败：{exc}"
            self._sig_subtitle_test.emit(ok, msg)

        threading.Thread(target=run, daemon=True).start()

    def _on_subtitle_test_result(self, ok, msg):
        self.subtitle_engine_combo.setEnabled(True)
        self.subtitle_test_btn.setEnabled(True)
        self.subtitle_test_btn.setText("测试连接")
        self.subtitle_test_label.setText(("✅ " if ok else "⚠ ") + msg)
        self.subtitle_test_label.setStyleSheet(
            "color: #4ade80;" if ok else "color: #e5635f;")
        from qfluentwidgets import InfoBar, InfoBarPosition
        (InfoBar.success if ok else InfoBar.warning)(
            "识别服务可用" if ok else "识别服务不可用", msg,
            duration=3500 if ok else 6000,
            position=InfoBarPosition.TOP, parent=self)

    def _mk_edit(self, placeholder):
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMinimumWidth(320)
        edit.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        return edit

    def _build_key_page(self, tip, fields):
        """引擎密钥子页面：统一的右对齐标签 + 自适应输入框。

        Key / Secret / Token / Cookie 类字段默认密文显示，并提供一个
        「显示密钥」开关，避免录屏或旁人经过时泄露凭证。
        """
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 8, 0, 3)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        grid.setColumnMinimumWidth(0, 108)
        grid.setColumnStretch(1, 1)

        row = 0
        if tip:
            t = QLabel(tip, page)
            t.setObjectName("caption")
            t.setWordWrap(True)
            grid.addWidget(t, row, 0, 1, 2)
            row += 1

        secret_edits = [item[1] for item in fields
                        if isinstance(item[1], QLineEdit)
                        and _is_secret_label(item[0])]
        if secret_edits:
            for edit in secret_edits:
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            show_check = QCheckBox("显示密钥", page)
            show_check.setToolTip(
                "勾选后临时明文显示本页密钥；配置仍按原值保存，不受显隐影响")
            show_check.toggled.connect(
                lambda checked, edits=tuple(secret_edits): [
                    e.setEchoMode(QLineEdit.EchoMode.Normal if checked
                                  else QLineEdit.EchoMode.Password)
                    for e in edits])
            grid.addWidget(show_check, row, 0, 1, 2)
            row += 1

        for item in fields:
            text, widget = item[0], item[1]
            field_tip = item[2] if len(item) > 2 else ""
            lbl = QLabel(text, page)
            lbl.setObjectName("fieldLabel")
            if field_tip:
                lbl.setToolTip(field_tip)
                widget.setToolTip(field_tip)
            grid.addWidget(
                lbl, row, 0,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(widget, row, 1)
            row += 1
        return page

    # ── 上手检查 / 诊断 ─────────────────────────────────────

    def _build_checklist_section(self) -> _Section:
        section = _Section(
            "上手检查",
            "一眼看清哪些开箱可用、哪些要先装组件或下载模型；不用挨个页签试")
        self.checklist_label = QLabel()
        self.checklist_label.setObjectName("caption")
        self.checklist_label.setWordWrap(True)
        section.add_row(self.checklist_label)
        row = QWidget(self)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        btn = QPushButton("重新检测", row)
        btn.setProperty("kind", "ghost")
        btn.setToolTip("重新检查 OCR 引擎、OCR 模型、字幕模型与快捷键状态")
        btn.clicked.connect(lambda: self._refresh_checklist(show_feedback=True))
        lay.addWidget(btn)
        advice = QPushButton("查看建议", row)
        advice.setProperty("kind", "ghost")
        advice.setToolTip("把还没就绪的项目整理成一步一步的操作清单")
        advice.clicked.connect(self._show_checklist_advice)
        lay.addWidget(advice)
        lay.addStretch(1)
        section.add_row(row)
        self._refresh_checklist()
        return section

    def _refresh_checklist(self, show_feedback=False):
        """只读体检：明确标出「开箱可用」与「需配置 / 需下载」。"""
        import ocr_models
        import diagnostics
        import ui_theme
        from ocr_recognizer import check_engine_available
        try:
            win_ocr = check_engine_available('windows')
        except Exception:
            win_ocr = False
        try:
            easy_ocr = check_engine_available('easy')
        except Exception:
            easy_ocr = False
        try:
            import speech_recognizer as asr
            model_ready = any(asr.is_model_ready(n) for n in
                              ('tiny', 'base', 'small'))
        except Exception:
            model_ready = False
        try:
            detect = ocr_models.detect_local_models()
            ocr_models_ready = bool(detect['ready'])
        except Exception:
            ocr_models_ready = False
        try:
            from hotkeys import get_hotkeys
            has_hotkey = any(bool(v) for v in get_hotkeys().values())
        except Exception:
            has_hotkey = False
        rows = [
            (win_ocr, "截图文字识别",
             "开箱可用（Windows 自带 OCR）" if win_ocr
             else "需安装 winrt 组件，或改用 EasyOCR"),
            (True, "翻译", "开箱可用（默认免费引擎，无需密钥）"),
            (model_ready, "实时字幕 / 音视频识别",
             "本地模型已就绪，完全离线可用" if model_ready
             else "需先下载或导入语音模型（几十 MB ~ 数 GB）"),
            (easy_ocr or ocr_models_ready, "高精度 OCR",
             "EasyOCR 已可用" if easy_ocr
             else ("本地 EasyOCR 模型已齐全，装引擎后即可离线识别"
                   if ocr_models_ready else
                   "可选：安装 EasyOCR，或手动下载模型导入")),
            (has_hotkey, "全局快捷键",
             "已设置" if has_hotkey
             else "未设置（不影响使用）；可在「快捷键」分区或首次引导里启用"),
        ]
        import datetime
        ready = sum(1 for ok, _, _ in rows if ok)
        optional = len(rows) - ready
        stamp = datetime.datetime.now().strftime('%H:%M:%S')
        text = (f"最近检测 {stamp} · {ready} 项就绪 / {optional} 项可选增强\n"
                + "\n".join(
                    ("✅ " if ok else "⚠  ") + f"{name}：{desc}"
                    for ok, name, desc in rows))
        self.checklist_label.setText(text)
        self.checklist_label.setToolTip(
            "带 ⚠ 的是可选增强，不影响「截图 → 识别 → 翻译」主流程；"
            "点「重新检测」可在装完组件后刷新")
        self._checklist_rows = rows
        diagnostics.record('info', '已完成一次上手检查', source='上手检查')
        if show_feedback:
            # 以前点「重新检测」只改一行文字，结果没变化时用户完全看不出
            # 按钮有没有生效；给一条明确反馈并指出下一步在哪里。
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.success(
                "检测完成",
                f"{ready} 项开箱可用，{optional} 项为可选增强；"
                "点「查看建议」可看具体该怎么做",
                duration=4000, position=InfoBarPosition.TOP, parent=self)

    def _show_checklist_advice(self):
        rows = getattr(self, '_checklist_rows', None) or []
        todo = [f"• {name}：{desc}" for ok, name, desc in rows if not ok]
        if not todo:
            QMessageBox.information(
                self, "上手检查 · 全部就绪",
                "所有检查项都已就绪，可以直接开始使用。\n\n"
                "主流程：点「区域截图」框选 → 自动识别文字 → 点「翻译」。")
            return
        QMessageBox.information(
            self, "上手检查 · 接下来可以做什么",
            "下面这些都是可选增强，不影响「截图 → 识别 → 翻译」主流程，"
            "按需处理即可：\n\n" + "\n".join(todo)
            + "\n\n操作位置：设置页「OCR 设置」「语音识别设置」"
              "「快捷键」分区，以及音视频页的「下载模型」。")

    def _build_diagnostics_section(self) -> _Section:
        section = _Section(
            "诊断与帮助",
            "运行记录实时更新，最新消息出现在末尾并自动滚动到可见；"
            "复制出去的信息不含任何密钥")
        self.diag_view = QPlainTextEdit()
        self.diag_view.setReadOnly(True)
        self.diag_view.setMaximumHeight(190)
        self.diag_view.setPlaceholderText("暂无诊断信息")
        section.add_row(self.diag_view)
        row = QWidget(self)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        for text, slot in (
                ("复制诊断信息", self._copy_diagnostics),
                ("立即刷新", lambda: self._refresh_diagnostics(force=True)),
                ("清空错误记录", self._clear_diagnostics)):
            btn = QPushButton(text, row)
            btn.setProperty("kind", "ghost")
            btn.clicked.connect(slot)
            lay.addWidget(btn)
        lay.addStretch(1)
        section.add_row(row)
        self._diag_revision = -1
        self._refresh_diagnostics(force=True)
        # 以前只能手动点「刷新」，而且 setPlainText 会把视图弹回顶部，
        # 最新一条错误反而看不见。按事件版本号轮询，有变化才重绘。
        self._diag_timer = QTimer(self)
        self._diag_timer.setInterval(1200)
        self._diag_timer.timeout.connect(self._poll_diagnostics)
        self._diag_timer.start()
        return section

    def _set_diag_text(self, text):
        """替换报告内容后把光标移到末尾，保证最新消息可见。"""
        from PySide6.QtGui import QTextCursor
        self.diag_view.setPlainText(text)
        cursor = self.diag_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.diag_view.setTextCursor(cursor)
        self.diag_view.ensureCursorVisible()
        # 设置页还没显示时 ensureCursorVisible() 不会真正推动滚动条，
        # 显式置底才能保证「刷新后第一眼就看到最新一条」。
        bar = self.diag_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _poll_diagnostics(self):
        import diagnostics
        if diagnostics.revision() != self._diag_revision:
            self._refresh_diagnostics()

    def _refresh_diagnostics(self, force=False):
        import diagnostics
        rev = diagnostics.revision()
        if not force and rev == self._diag_revision:
            return
        self._set_diag_text(diagnostics.build_report())
        self._diag_revision = rev

    def _copy_diagnostics(self):
        import ui_feedback
        from qfluentwidgets import InfoBar, InfoBarPosition
        text = ui_feedback.copy_diagnostic_report()
        self._set_diag_text(text)
        InfoBar.success("已复制", "诊断信息已放进剪贴板，可直接粘贴给排查人员",
                        duration=3000, position=InfoBarPosition.TOP,
                        parent=self)

    def _clear_diagnostics(self):
        import diagnostics
        diagnostics.clear()
        self._refresh_diagnostics(force=True)

    def _refresh_health(self):
        """配置损坏 / 数据目录降级时，设置页顶部保留一条持久横幅。"""
        import diagnostics
        issues = diagnostics.health_issues()
        bar = getattr(self, '_health_bar', None)
        if bar is None:
            return
        if not issues:
            bar.setVisible(False)
            return
        issue = issues[0]
        self._health_label.setText(
            f"⚠  {issue['title']}：{issue['text']}")
        self._health_path = issue.get('path') or ''
        bar.setVisible(True)

    def _open_health_dir(self):
        path = getattr(self, '_health_path', '')
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ── OCR 本地模型（手动下载保底）─────────────────────────

    def _refresh_ocr_models(self):
        import ocr_models
        import ui_theme
        detect = ocr_models.detect_local_models()
        cache = ocr_models.model_cache_info()
        if cache['models']:
            summary = ocr_models.model_cache_summary(cache)
        elif detect['legacy_files']:
            # 老版本把模型下在 ~/.EasyOCR/model；统一目录还是空的，别一边
            # 说「还缺 1 个文件」一边又说「尚无本地模型」，自相矛盾。
            summary = (f"旧版目录 ~/.EasyOCR/model 已有 "
                       f"{len(detect['legacy_files'])} 个模型文件"
                       f"（{ocr_models.human_size(detect['total_bytes'])}）")
        else:
            summary = "尚无本地 OCR 模型（可点「检测本地模型」或手动导入）"
        # 单一前缀 + 单一后缀，避免「尚未检测到… · 尚无本地模型…」重复，
        # 也别出现「没有模型 · 旧版目录已有 N 个」这种自相矛盾。
        if detect['ready']:
            prefix, color = "✅ 本地模型齐全，识别时完全不联网 · ", ui_theme.COLOR_OK
        elif detect['can_use_offline']:
            prefix = (f"⚠ 可用但还缺 {len(detect['missing'])} 个文件 · ")
            color = ui_theme.COLOR_WARN
        elif detect['legacy_files']:
            prefix, color = "⚠ 旧版目录有模型但不完整 · ", ui_theme.COLOR_WARN
        else:
            prefix, color = "", ui_theme.COLOR_MUTED
        text = prefix + summary
        self.ocr_model_label.setText(text)
        self.ocr_model_label.setStyleSheet(f"color: {color};")
        self.ocr_model_label.setToolTip(
            "目录：点「打开模型文件夹」查看，或点「复制路径」取得本机实际目录"
            "（每台电脑不同，说明里不写死路径）\n"
            "缺少：" + ("、".join(detect['missing']) or "无") + "\n"
            + ocr_models.manual_download_hint())
        return detect

    def _detect_ocr_models(self):
        import ui_feedback
        from qfluentwidgets import InfoBar, InfoBarPosition
        detect = self._refresh_ocr_models()
        if detect['ready']:
            InfoBar.success("模型已就绪",
                            "本地 OCR 模型齐全，EasyOCR 将直接使用，不再联网下载",
                            duration=4000, position=InfoBarPosition.TOP,
                            parent=self)
        else:
            hint = (f"还缺 {len(detect['missing'])} 个模型文件"
                    if detect['missing'] else
                    "模型分散在两个目录，建议导入到统一目录")
            ui_feedback.show_info_bar(
                self, "检测结果",
                hint + "，可点「手动下载说明」查看文件名与放置目录",
                duration=6000)

    def _import_ocr_model(self):
        import ocr_models
        from PySide6.QtWidgets import QFileDialog
        from qfluentwidgets import InfoBar, InfoBarPosition
        folder = QFileDialog.getExistingDirectory(
            self, "选择包含 EasyOCR 模型文件（.pth）的目录",
            ocr_models.MODEL_ROOT)
        if not folder:
            return
        try:
            count, names = ocr_models.import_local_model(folder)
        except Exception as exc:
            ui_feedback.show_error_bar(
                self, "导入失败", str(exc),
                actions=[("查看说明", self._show_ocr_manual_hint)],
                source="OCR 模型导入")
            return
        self._refresh_ocr_models()
        InfoBar.success(
            "导入完成",
            f"模型目录现有 {count} 个文件"
            + (f"（新增 {len(names)} 个）" if names else "，文件已就位"),
            duration=4000, position=InfoBarPosition.TOP, parent=self)

    def _open_ocr_model_dir(self):
        import ocr_models
        path = ocr_models.ensure_model_root()
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self._refresh_ocr_models()

    def _copy_ocr_model_path(self):
        import ocr_models
        from qfluentwidgets import InfoBar, InfoBarPosition
        QApplication.clipboard().setText(ocr_models.MODEL_ROOT)
        self.ocr_model_label.setToolTip(
            "已复制路径：" + ocr_models.MODEL_ROOT)
        InfoBar.success("已复制", "OCR 模型目录路径已放进剪贴板",
                        duration=3000, position=InfoBarPosition.TOP,
                        parent=self)

    def _show_ocr_catalog(self):
        import ocr_models
        box = QMessageBox(self)
        box.setWindowTitle("OCR 方案对比 · 每种模型的优势与代价")
        box.setText("安装包只内置 Windows 系统自带 OCR（0 MB 模型文件）："
                    "开箱即用、零下载，打包体积和时间都不受影响。\n"
                    "EasyOCR 等其它方案按需下载，不随包分发；"
                    "点下方「Show Details…」展开每种方案的优势、代价与获取方式。")
        box.setDetailedText(ocr_models.engine_catalog_text())
        box.setIcon(QMessageBox.Icon.Information)
        box.exec()

    def _show_ocr_manual_hint(self):
        import ocr_models
        box = QMessageBox(self)
        box.setWindowTitle("手动下载 OCR 模型（网络不好时的保底）")
        box.setText("把模型文件放进模型文件夹后点「检测本地模型」，"
                    "识别会优先用本地文件，不再联网。\n"
                    "目录请用「打开模型文件夹」按钮定位："
                    "每台电脑都不一样，说明里不写死路径。")
        box.setDetailedText(ocr_models.manual_download_hint())
        box.setIcon(QMessageBox.Icon.Information)
        box.exec()

    def _show_ocr_download_menu(self):
        """其它 OCR 模型 / 引擎的官方获取入口。

        安装包里一个模型文件都不放（打包体积和时间才可控），所以这里只给
        官方下载页；EasyOCR 的引擎安装与模型自动/手动下载都在本分区完成。
        网页下载的文件不会自动进入模型目录，且三个「选型参考」方案当前没有
        实现，所以每项标出可用性，点开后再补一条可操作的提示。
        """
        import ocr_models
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        for label, url, kind in ocr_models.download_menu_items():
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, u=url, k=kind:
                    self._on_ocr_download_link(u, k))
        menu.addSeparator()
        for text in (
                "✅ 下载完请点「导入本地模型」，或拖进「打开模型文件夹」",
                "📋 当前版本未内置该引擎，仅供了解，下载后还不能使用",
                "放进模型目录后，升级软件不会删除，无需重新下载"):
            note = menu.addAction(text)
            note.setEnabled(False)
        btn = getattr(self, '_ocr_download_btn', None)
        if btn is not None:
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_ocr_download_link(self, url, kind):
        """打开官方页后说明「下载 ≠ 已装好」，别让人白等或白下载。"""
        QDesktopServices.openUrl(QUrl(url))
        if kind == 'usable':
            ui_feedback.show_info_bar(
                self, "已打开下载页",
                "文件会保存在浏览器下载目录，不会自动进入程序。下载完请点"
                "「导入本地模型」，或把文件拖进「打开模型文件夹」；放进模型"
                "目录后升级仍会保留，无需重下。",
                duration=9000)
        else:
            ui_feedback.show_info_bar(
                self, "已打开介绍页",
                "这是选型参考：当前版本尚未内置该引擎，下载的模型暂时无法在"
                "程序中使用。现在可用的是 Windows 自带 OCR，以及按需安装的"
                " EasyOCR。",
                duration=9000)

    # ── OCR 设置 ────────────────────────────────────────────

    def _build_ocr_section(self) -> _Section:
        section = _Section("OCR 设置", "截图与图片的文字识别引擎")

        self.ocr_engine_combo = QComboBox()
        self.ocr_engine_combo.addItems(list(SUPPORTED_OCR_ENGINES.values()))
        current_engine = self._cfg.get("ocr_engine", OCR_ENGINE)
        for engine_key, engine_name in SUPPORTED_OCR_ENGINES.items():
            if engine_key == current_engine:
                self.ocr_engine_combo.setCurrentText(engine_name)
                break
        section.add("OCR 引擎:", self.ocr_engine_combo,
                    tip="选择文字识别引擎，切换后需保存生效")

        section.add_caption(
            "💡 安装包只内置 Windows 系统自带 OCR：开箱即用、零下载、"
            "零模型文件，所以打包快、体积小。\n"
            "   EasyOCR 等其它方案按需下载（不随包分发）：选中引擎后点「安装」，"
            "模型会自动下载；装完无需重启。\n"
            "   安装中按钮会变成「取消安装」，可随时中断，"
            "已下载的文件下次自动续传。\n"
            "   网络不好时可点「手动下载说明」自己下模型，"
            "再点「导入本地模型」即可离线识别。\n"
            "   模型与已装组件都放在用户数据目录，覆盖升级 / 一键升级后"
            "原样保留，不用重新下载或重装。")

        self.ocr_status_label = QLabel()
        self.ocr_status_label.setObjectName("caption")
        self.ocr_status_label.setWordWrap(True)
        section.add_row(self.ocr_status_label)

        install_row = QWidget(self)
        install_layout = QHBoxLayout(install_row)
        install_layout.setContentsMargins(0, 0, 0, 0)
        install_layout.setSpacing(12)
        self.ocr_install_btn = QPushButton("安装")
        self.ocr_install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ocr_install_btn.setToolTip(
            "通过 pip 安装所选 OCR 引擎；安装中点它可以取消")
        self.ocr_install_btn.clicked.connect(
            lambda: self._task_toggle(
                'ocr', self._install_ocr_engine, "取消安装"))
        self.ocr_install_btn.setVisible(False)
        self._task_buttons['ocr'] = self.ocr_install_btn
        install_layout.addWidget(self.ocr_install_btn)
        self.ocr_progress = QProgressBar()
        self.ocr_progress.setVisible(False)
        self.ocr_progress.setTextVisible(True)
        self.ocr_progress.setRange(0, 0)
        install_layout.addWidget(self.ocr_progress, 1)
        section.add_row(install_row)

        # ── 本地 OCR 模型：检测 / 导入 / 打开文件夹 / 复制路径 ──
        model_row = QWidget(self)
        model_lay = QHBoxLayout(model_row)
        model_lay.setContentsMargins(0, 0, 0, 0)
        model_lay.setSpacing(8)
        for text, slot, tip in (
                ("检测本地模型", self._detect_ocr_models,
                 "看看本机已有哪些 .pth 模型、还缺哪些文件"),
                ("导入本地模型…", self._import_ocr_model,
                 "选择手动下载的模型目录，复制进统一管理目录，不改动原文件"),
                ("打开模型文件夹", self._open_ocr_model_dir,
                 "打开模型存放目录，可直接把文件拖进去"),
                ("复制路径", self._copy_ocr_model_path,
                 "复制模型目录路径，方便在下载工具里粘贴")):
            btn = QPushButton(text, model_row)
            btn.setProperty("kind", "ghost")
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            model_lay.addWidget(btn)
        model_lay.addStretch(1)
        section.add_row(model_row)

        self.ocr_model_label = QLabel()
        self.ocr_model_label.setObjectName("caption")
        self.ocr_model_label.setWordWrap(True)
        self.ocr_model_label.setMinimumWidth(120)
        section.add_row(self.ocr_model_label)

        catalog_row = QWidget(self)
        catalog_lay = QHBoxLayout(catalog_row)
        catalog_lay.setContentsMargins(0, 0, 0, 0)
        catalog_lay.setSpacing(8)
        catalog_btn = QPushButton("查看全部 OCR 方案对比", catalog_row)
        catalog_btn.setProperty("kind", "ghost")
        catalog_btn.setToolTip(
            "列出 Windows OCR / EasyOCR / RapidOCR / PaddleOCR / Tesseract "
            "各自的优势、代价与适用场景")
        catalog_btn.clicked.connect(self._show_ocr_catalog)
        catalog_lay.addWidget(catalog_btn)
        manual_btn = QPushButton("手动下载说明", catalog_row)
        manual_btn.setProperty("kind", "ghost")
        manual_btn.setToolTip("手动下载保底：需要哪些文件、怎么定位目录")
        manual_btn.clicked.connect(self._show_ocr_manual_hint)
        catalog_lay.addWidget(manual_btn)
        download_btn = QPushButton("下载 / 了解更多 ▾", catalog_row)
        download_btn.setProperty("kind", "ghost")
        download_btn.setToolTip(
            "打开各方案的官方下载页（菜单里标了能否在当前版本使用）；"
            "网页下载的文件需点「导入本地模型」才会被程序使用，"
            "导入后升级会保留")
        download_btn.clicked.connect(self._show_ocr_download_menu)
        catalog_lay.addWidget(download_btn)
        self._ocr_download_btn = download_btn
        catalog_lay.addStretch(1)
        section.add_row(catalog_row)

        self.ocr_engine_combo.currentTextChanged.connect(self._on_ocr_engine_changed)
        self._refresh_ocr_status()
        self._refresh_ocr_models()
        return section

    # ── 语音识别设置 ────────────────────────────────────────

    def _build_asr_section(self) -> _Section:
        section = _Section(
            "语音识别设置",
            "「音视频」页的语音识别选项；歌曲 / 纯音乐建议关闭人声检测")

        asr_cfg = get_asr_settings()

        self.asr_vad_check = FlatCheckBox("启用静音 / 纯音乐过滤（人声检测）")
        self.asr_vad_check.setChecked(
            bool(asr_cfg.get("asr_vad_enabled", True)))
        section.add(
            "人声检测:", self.asr_vad_check,
            tip="开启后先检测人声再识别，可跳过静音片段；\n"
                "唱歌或纯音乐容易被误判为非人声，建议关闭。\n"
                "若检测不到人声，程序会自动改用整段识别重试一次。")

        slider_row = QWidget(self)
        slider_layout = QHBoxLayout(slider_row)
        slider_layout.setContentsMargins(0, 0, 0, 0)
        slider_layout.setSpacing(12)
        self.asr_threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.asr_threshold_slider.setRange(10, 90)
        self.asr_threshold_slider.setFixedWidth(220)
        self.asr_threshold_slider.setValue(
            int(_safe_float(asr_cfg, "asr_vad_threshold", 0.5) * 100))
        slider_layout.addWidget(self.asr_threshold_slider)
        self.asr_threshold_label = QLabel(
            f"{self.asr_threshold_slider.value() / 100:.2f}")
        self.asr_threshold_label.setObjectName("caption")
        self.asr_threshold_label.setMinimumWidth(34)
        slider_layout.addWidget(self.asr_threshold_label)
        slider_layout.addStretch(1)
        self.asr_threshold_slider.valueChanged.connect(
            lambda v: self.asr_threshold_label.setText(f"{v / 100:.2f}"))
        section.add(
            "检测阈值:", slider_row,
            tip="人声判定阈值：数值越低越宽松（更容易判为人声，适合歌曲），\n"
                "越高越严格（适合嘈杂环境）。默认 0.50。")

        self.asr_beam_combo = QComboBox()
        for label, value in (("快速（beam 1）", 1), ("标准（beam 5）", 5),
                             ("精确（beam 10）", 10)):
            self.asr_beam_combo.addItem(label, value)
        idx = self.asr_beam_combo.findData(
            _safe_int(asr_cfg, "asr_beam_size", 5))
        self.asr_beam_combo.setCurrentIndex(max(0, idx))
        self.asr_beam_combo.setMinimumWidth(160)
        section.add(
            "识别精度:", self.asr_beam_combo,
            tip="解码宽度：数值越大越准确但越慢；标准（beam 5）适合大多数场景。")

        self.asr_stream_check = FlatCheckBox("实时显示识别结果（边识别边出字幕）")
        self.asr_stream_check.setChecked(
            bool(asr_cfg.get("asr_stream_enabled", True)))
        section.add(
            "实时字幕:", self.asr_stream_check,
            tip="开启后识别过程中每出一段字幕就立即显示在预览区；\n"
                "关闭则识别完成后一次性显示（在线 API 模式不支持实时）。")

        # 计算设备（GPU 加速）
        self.asr_device_combo = QComboBox()
        for label, value in (("自动（推荐）", "auto"),
                             ("仅 CPU（兼容性最好）", "cpu"),
                             ("GPU 加速（需 NVIDIA 显卡）", "gpu")):
            self.asr_device_combo.addItem(label, value)
        dev_idx = self.asr_device_combo.findData(
            str(asr_cfg.get("asr_device", "auto")))
        self.asr_device_combo.setCurrentIndex(max(0, dev_idx))
        self.asr_device_combo.setMinimumWidth(200)
        section.add(
            "计算设备:", self.asr_device_combo,
            tip="GPU 可显著提升识别速度（质量主要由模型大小决定）；\n"
                "选择 GPU 但本机不可用时会自动回退 CPU 并提示。")

        device_row = QWidget(self)
        device_layout = QHBoxLayout(device_row)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(12)
        self.gpu_status_label = QLabel("")
        self.gpu_status_label.setObjectName("caption")
        self.gpu_status_label.setWordWrap(True)
        device_layout.addWidget(self.gpu_status_label, 1)
        self.gpu_detect_btn = QPushButton("检测 GPU")
        self.gpu_detect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gpu_detect_btn.setToolTip("重新检测本机是否有可用的 NVIDIA GPU")
        self.gpu_detect_btn.clicked.connect(lambda: self._refresh_gpu_status(refresh=True))
        device_layout.addWidget(self.gpu_detect_btn)
        section.add_row(device_row)
        self._refresh_gpu_status()

        section.add_caption(
            "中文歌识别建议：关闭人声检测 + 音频语言选「中文」+ 使用 small 以上模型\n"
            "简繁输出：选「中文」自动输出简体，选「中文(繁体)」自动输出繁体")
        return section

    # ── 实时翻译设置 ────────────────────────────────────────

    def _build_live_section(self) -> _Section:
        section = _Section(
            "实时翻译",
            "主页「实时翻译」按钮：框选屏幕区域后持续自动识别并翻译")

        live_cfg = get_live_settings()

        self.live_interval_combo = QComboBox()
        for label, value in (("快（0.8 秒）", 800), ("标准（1.2 秒）", 1200),
                             ("省资源（2 秒）", 2000), ("很省（3 秒）", 3000)):
            self.live_interval_combo.addItem(label, value)
        idx = self.live_interval_combo.findData(
            _safe_int(live_cfg, "live_interval_ms", 1200))
        self.live_interval_combo.setCurrentIndex(max(0, idx))
        self.live_interval_combo.setMinimumWidth(170)
        section.add(
            "刷新间隔:", self.live_interval_combo,
            tip="画面没变化时会自动跳过识别，不会白白消耗资源；\n"
                "间隔越小越跟手，但 CPU / 翻译请求也更频繁。")

        self.live_source_check = FlatCheckBox("选区上默认同时显示原文")
        self.live_source_check.setChecked(
            bool(live_cfg.get("live_show_source", False)))
        section.add(
            "显示原文:", self.live_source_check,
            tip="开启后译文上方会多一行小字原文，方便对照；\n"
                "控制条上的「原文」按钮也能随时切换。")

        section.add_caption(
            "使用方法：主页点「实时翻译」→ 框选区域 → 点「开始实时翻译」（或按回车）；\n"
            "译文直接叠加在选区上；拖选区边缘/四角缩放，拖下方控制条移动，控制条可暂停 / 复制 / 重选 / 关闭。")
        return section

    # ── 实时字幕设置 ────────────────────────────────────────

    def _build_subtitle_section(self) -> _Section:
        import speech_recognizer as asr
        section = _Section(
            "实时字幕",
            "主页「实时字幕」按钮：直接在屏幕下方打开字幕框（无需框选，可拖动/缩放，位置会记住），"
            "听写麦克风或电脑声音；关闭字幕框后识别继续，结果同步显示在「翻译」页的"
            "识别文本 / 翻译结果，直到点「停止实时字幕」。翻译使用主页「翻译到」的语言")
        cfg = get_subtitle_settings()

        def _select(combo, value, fallback=0):
            idx = combo.findData(value)
            combo.setCurrentIndex(idx if idx >= 0 else fallback)

        self.subtitle_source_combo = QComboBox()
        for label, key in SUBTITLE_AUDIO_SOURCES.items():
            self.subtitle_source_combo.addItem(label, key)
        _select(self.subtitle_source_combo, cfg.get('subtitle_audio_source'))
        self.subtitle_source_combo.setMinimumWidth(180)
        self.subtitle_source_combo.currentIndexChanged.connect(
            self._refresh_subtitle_devices)
        section.add(
            "音频来源:", self.subtitle_source_combo,
            tip="麦克风：听你说话；电脑声音：听视频 / 音乐 / 线上会议\n"
                "主页底部动作条也能快速切换这一项")

        device_row = QWidget()
        device_lay = QHBoxLayout(device_row)
        device_lay.setContentsMargins(0, 0, 0, 0)
        device_lay.setSpacing(8)
        self.subtitle_device_combo = QComboBox()
        self.subtitle_device_combo.setMinimumWidth(280)
        device_lay.addWidget(self.subtitle_device_combo, 1)
        refresh_btn = QPushButton("刷新设备")
        refresh_btn.setProperty("kind", "ghost")
        refresh_btn.setFixedWidth(92)
        refresh_btn.clicked.connect(self._refresh_subtitle_devices)
        device_lay.addWidget(refresh_btn)
        section.add("采集设备:", device_row,
                    tip="默认设备通常就够用；有多块声卡 / 多个扬声器时手动指定")

        self.subtitle_engine_combo = QComboBox()
        for label, key in SUBTITLE_ENGINES.items():
            self.subtitle_engine_combo.addItem(label, key)
        _select(self.subtitle_engine_combo, cfg.get('subtitle_engine'))
        self.subtitle_engine_combo.setMinimumWidth(280)
        self.subtitle_engine_combo.currentIndexChanged.connect(
            self._refresh_subtitle_model_status)
        section.add(
            "识别引擎:", self.subtitle_engine_combo,
            tip="本地模型：免费下载、离线可用、延迟最低（实时推荐 tiny / base）\n"
                "在线 API：自备密钥的 OpenAI 兼容 /audio/transcriptions 服务\n"
                "免费本地服务：本机或局域网自建的 whisper.cpp / speaches 等，无需密钥")

        self.subtitle_model_combo = QComboBox()
        for name, info in asr.MODEL_CATALOG.items():
            fast = float(info.get('mb') or 99999) <= 200
            label = f"{name}（{info.get('size', '')}）"
            if fast:
                label += " · 实时推荐"
            if info.get('lang') == 'en':
                label += " · 仅英文"
            self.subtitle_model_combo.addItem(label, name)
        _select(self.subtitle_model_combo, cfg.get('subtitle_model'), 0)
        self.subtitle_model_combo.setMinimumWidth(280)
        self.subtitle_model_combo.setMaxVisibleItems(10)
        self.subtitle_model_combo.currentIndexChanged.connect(
            self._refresh_subtitle_model_status)
        section.add(
            "本地模型:", self.subtitle_model_combo,
            tip="实时场景模型只加载一次并常驻内存；tiny / base 在纯 CPU 上也能实时\n"
                "大模型在 CPU 上会明显延迟，建议只在有 NVIDIA 显卡时选")

        model_row = QWidget()
        model_lay = QHBoxLayout(model_row)
        model_lay.setContentsMargins(0, 0, 0, 0)
        model_lay.setSpacing(10)
        self.subtitle_model_status = QLabel("")
        self.subtitle_model_status.setObjectName("caption")
        self.subtitle_model_status.setWordWrap(True)
        model_lay.addWidget(self.subtitle_model_status, 1)
        self.subtitle_download_btn = QPushButton("下载所选模型")
        self.subtitle_download_btn.setProperty("kind", "ghost")
        self.subtitle_download_btn.setFixedWidth(120)
        self.subtitle_download_btn.clicked.connect(
            lambda: self._task_toggle(
                'model', self._download_subtitle_model, "取消下载"))
        model_lay.addWidget(self.subtitle_download_btn)
        self._task_buttons['model'] = self.subtitle_download_btn
        section.add_row(model_row)

        # 模型缓存：占用空间 / 路径 / 一键打开与复制，避免「不知道下到哪、
        # 也不知道占了多少磁盘」，取消下载留下的半成品也会如实标出来。
        cache_row = QWidget()
        cache_lay = QHBoxLayout(cache_row)
        cache_lay.setContentsMargins(0, 0, 0, 0)
        cache_lay.setSpacing(8)
        self.model_cache_label = QLabel("", cache_row)
        self.model_cache_label.setObjectName("caption")
        self.model_cache_label.setWordWrap(True)
        # 三个固定宽按钮合计 252px，极窄窗口下给文字留个下限，让横向
        # 滚动接管，而不是把长路径挤成竖排。
        self.model_cache_label.setMinimumWidth(120)
        self.model_cache_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        cache_lay.addWidget(self.model_cache_label, 1)
        self.model_cache_open_btn = QPushButton("打开目录", cache_row)
        self.model_cache_copy_btn = QPushButton("复制路径", cache_row)
        self.model_cache_refresh_btn = QPushButton("刷新", cache_row)
        for btn in (self.model_cache_open_btn, self.model_cache_copy_btn,
                    self.model_cache_refresh_btn):
            btn.setProperty("kind", "ghost")
            btn.setFixedWidth(84)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cache_lay.addWidget(btn)
        self.model_cache_open_btn.clicked.connect(self._open_model_cache)
        self.model_cache_copy_btn.clicked.connect(
            self._copy_model_cache_path)
        self.model_cache_refresh_btn.clicked.connect(
            self._refresh_model_cache)
        section.add_row(cache_row)
        self._refresh_model_cache()
        self._refresh_subtitle_model_status()

        self.subtitle_api_url = self._mk_edit(
            "https://api.example.com/v1 或 http://127.0.0.1:8000/v1")
        self.subtitle_api_url.setText(str(cfg.get('subtitle_api_url') or ''))
        section.add("服务地址:", self.subtitle_api_url,
                    tip="仅「在线 API / 免费本地服务」需要；填到 /v1 即可")

        self.subtitle_api_key = self._mk_edit("免费本地服务可留空")
        self.subtitle_api_key.setText(str(cfg.get('subtitle_api_key') or ''))
        self.subtitle_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        section.add("API 密钥:", self.subtitle_api_key,
                    tip="从服务方控制台获取；免费本地服务可留空。\n"
                        "保存后建议启动一次实时字幕验证是否可用")

        self.subtitle_api_model = self._mk_edit("whisper-1")
        self.subtitle_api_model.setText(
            str(cfg.get('subtitle_api_model') or 'whisper-1'))
        section.add("服务模型名:", self.subtitle_api_model,
                    tip="填服务方文档里的模型 ID，例如 whisper-1；\n"
                        "填错会在启动实时字幕时提示「模型不存在」")

        svc_row = QWidget()
        svc_lay = QHBoxLayout(svc_row)
        svc_lay.setContentsMargins(0, 0, 0, 0)
        svc_lay.setSpacing(10)
        self.subtitle_test_btn = QPushButton("测试连接", svc_row)
        self.subtitle_test_btn.setProperty("kind", "ghost")
        self.subtitle_test_btn.setFixedWidth(110)
        self.subtitle_test_btn.setCursor(
            Qt.CursorShape.PointingHandCursor)
        self.subtitle_test_btn.setToolTip(
            "只探测服务地址与密钥是否可用（不上传音频、不写配置）")
        self.subtitle_test_btn.clicked.connect(self._test_subtitle_service)
        svc_lay.addWidget(self.subtitle_test_btn)
        self.subtitle_test_label = QLabel("", svc_row)
        self.subtitle_test_label.setObjectName("caption")
        self.subtitle_test_label.setWordWrap(True)
        svc_lay.addWidget(self.subtitle_test_label, 1)
        section.add_row(svc_row)
        section.add_caption(
            "选「在线 API / 免费本地服务」时，填完地址就能点「测试连接」："
            "最多等 6 秒，会告诉你是地址不通、密钥不对还是服务正常；"
            "不必先保存，也不会上传任何音频。")

        self.subtitle_lang_combo = QComboBox()
        self.subtitle_lang_combo.addItem("自动检测", "auto")
        for code, name in asr.LANGUAGE_NAMES.items():
            self.subtitle_lang_combo.addItem(name, code)
        _select(self.subtitle_lang_combo, cfg.get('subtitle_language'))
        section.add("识别语言:", self.subtitle_lang_combo,
                    tip="明确语言比自动检测更快也更准")

        self.subtitle_translate_check = FlatCheckBox("同时翻译字幕")
        self.subtitle_translate_check.setChecked(
            bool(cfg.get('subtitle_translate', True)))
        section.add("翻译字幕:", self.subtitle_translate_check,
                    tip="翻译走独立线程，网络慢也不会拖慢原文出现")

        self.subtitle_mode_combo = QComboBox()
        for label, key in SUBTITLE_DISPLAY_MODES.items():
            self.subtitle_mode_combo.addItem(label, key)
        _select(self.subtitle_mode_combo, cfg.get('subtitle_display_mode'))
        section.add("字幕显示:", self.subtitle_mode_combo,
                    tip="浮层控制条和托盘菜单也能随时切换，无需重启识别")

        self.subtitle_window_combo = QComboBox()
        for label, value in (("跟手（1.5 秒）", 1500),
                             ("标准（2.5 秒）", 2500),
                             ("稳（4 秒）", 4000)):
            self.subtitle_window_combo.addItem(label, value)
        _select(self.subtitle_window_combo, cfg.get('subtitle_window_ms'))
        section.add("最长片段:", self.subtitle_window_combo,
                    tip="说话停顿会立刻切句出字幕；这里只是没停顿时强制出字幕的上限")

        self.subtitle_font_combo = QComboBox()
        for size in (16, 18, 20, 22, 26, 30, 36):
            self.subtitle_font_combo.addItem(f"{size} px", size)
        _select(self.subtitle_font_combo, cfg.get('subtitle_font_size'))
        section.add("字幕字号:", self.subtitle_font_combo,
                    tip="浮层控制条的 A- / A+ 也能临时调整；\n"
                        "在这里保存会立即同步到已打开的字幕框")

        self.subtitle_lines_combo = QComboBox()
        for n in (2, 3, 4, 5):
            self.subtitle_lines_combo.addItem(f"{n} 条", n)
        _select(self.subtitle_lines_combo, cfg.get('subtitle_max_lines'))
        section.add("保留行数:", self.subtitle_lines_combo,
                    tip="字幕框最多同时显示几条；保存后立即生效")

        self.subtitle_alpha_combo = QComboBox()
        for label, value in (("很淡", 110), ("偏淡", 150), ("标准", 190),
                             ("偏浓", 220), ("不透明", 250)):
            self.subtitle_alpha_combo.addItem(label, value)
        _select(self.subtitle_alpha_combo, cfg.get('subtitle_bg_alpha'))
        section.add("底色深浅:", self.subtitle_alpha_combo,
                    tip="调淡可少遮挡视频画面；保存后立即生效")

        self.subtitle_through_check = FlatCheckBox("鼠标穿透字幕框")
        self.subtitle_through_check.setChecked(
            bool(cfg.get('subtitle_click_through', False)))
        section.add("点击穿透:", self.subtitle_through_check,
                    tip="开启后字幕不挡鼠标，可以照常操作下面的视频/网页；\n"
                        "关闭后才能拖边缘缩放选区")

        dep_row = QWidget()
        dep_lay = QHBoxLayout(dep_row)
        dep_lay.setContentsMargins(0, 0, 0, 0)
        dep_lay.setSpacing(10)
        self.subtitle_dep_label = QLabel("")
        self.subtitle_dep_label.setObjectName("caption")
        self.subtitle_dep_label.setWordWrap(True)
        dep_lay.addWidget(self.subtitle_dep_label, 1)
        self.subtitle_install_btn = QPushButton("安装组件")
        self.subtitle_install_btn.setProperty("kind", "ghost")
        self.subtitle_install_btn.setFixedWidth(120)
        self.subtitle_install_btn.setToolTip(
            "安装当前音源缺少的采集组件；安装中点它可以取消")
        self.subtitle_install_btn.clicked.connect(
            lambda: self._task_toggle(
                'loopback', self._install_loopback, "取消安装"))
        self._task_buttons['loopback'] = self.subtitle_install_btn
        dep_lay.addWidget(self.subtitle_install_btn)
        section.add_row(dep_row)
        self._refresh_subtitle_devices(
            select=cfg.get('subtitle_audio_device', ''))
        # 所有控件都建好后再刷一次，确保 API 输入框的启用态与引擎一致
        self._refresh_subtitle_model_status()

        section.add_caption(
            "使用方法：主页选好音源 → 点「实时字幕」→ 直接开始说话或播放视频。\n"
            "字幕框可拖动 / 四角缩放，控制条「复位」回到默认位置；关掉字幕框（或 Esc）只是隐藏，"
            "识别继续并把原文/译文写到主页，点「停止」或再点主页按钮才真正结束。\n"
            "速度：本地模型常驻内存 + beam_size=1 + 静音即切句，CPU 上 tiny/base 可做到接近实时。")
        return section

    def _refresh_subtitle_devices(self, _index=None, select=None):
        """按当前音源重新枚举设备，并刷新内录组件状态。"""
        from live_caption import dependency_status, list_devices
        source = str(self.subtitle_source_combo.currentData() or 'mic')
        current = (select if select is not None
                   else self.subtitle_device_combo.currentData())
        self.subtitle_device_combo.blockSignals(True)
        self.subtitle_device_combo.clear()
        self.subtitle_device_combo.addItem("默认设备", "")
        try:
            for dev in list_devices(source):
                self.subtitle_device_combo.addItem(dev['name'], dev['id'])
        except Exception as e:
            self.subtitle_device_combo.addItem(f"设备枚举失败: {e}", "")
        if current:
            idx = self.subtitle_device_combo.findData(current)
            if idx >= 0:
                self.subtitle_device_combo.setCurrentIndex(idx)
        self.subtitle_device_combo.blockSignals(False)

        deps = dependency_status()
        if source == 'loopback':
            if deps['loopback']:
                msg = "电脑声音：WASAPI 环回组件已就绪，可选具体扬声器"
            else:
                msg = ("电脑声音：缺少 soundcard，点右侧一键安装；"
                       "若系统已启用「立体声混音」也可直接在上方选择")
        else:
            msg = ("麦克风：sounddevice 已就绪" if deps['mic']
                   else "麦克风：缺少 sounddevice，点右侧「安装组件」即可在线安装")
        self.subtitle_dep_label.setText(msg)
        # 麦克风缺 sounddevice 时同样要有可点的安装入口，否则提示是死路
        self.subtitle_install_btn.setVisible(
            not (deps['loopback'] if source == 'loopback' else deps['mic']))

    def _install_loopback(self, cancel=None):
        # 麦克风与内录缺的是不同的包，按当前音源选择要装的组件
        from live_caption import dependency_status
        source = str(self.subtitle_source_combo.currentData() or '')
        mic_mode = (source != 'loopback'
                    and not dependency_status()['mic'])
        packages = ('sounddevice',) if mic_mode else ('soundcard',)
        label = ("麦克风采集组件 sounddevice" if mic_mode
                 else "soundcard 内录组件")
        self.subtitle_dep_label.setText(
            f"正在安装 {label}…（可点右侧按钮取消）")
        self.subtitle_dep_label.setToolTip("")
        self.subtitle_dep_label.setStyleSheet("")

        def work():
            try:
                from live_caption import install_loopback_support
                ok, msg = install_loopback_support(
                    cancel=cancel, packages=packages)
            except Exception as exc:
                # 任何逃逸异常都要发完成信号，否则「安装」按钮会永久禁用
                ok, msg = False, f"安装失败：{exc}"
            self._sig_caption_install.emit(ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def _on_caption_install_done(self, ok, msg):
        cancelled = self._task_finish('loopback') and not ok
        from qfluentwidgets import InfoBar, InfoBarPosition
        if cancelled:
            self.subtitle_dep_label.setText(
                "已取消安装，可重新点「安装组件」")
            self.subtitle_dep_label.setToolTip("")
            self.subtitle_dep_label.setStyleSheet("color: #eab308;")
            InfoBar.info("已取消", "内录组件安装已中断", duration=3000,
                         position=InfoBarPosition.TOP, parent=self)
        else:
            # 与 OCR 组件一致：失败要有醒目颜色和弹窗，不能只显示一行小字
            short = msg if ok else friendly_install_error(msg)
            self.subtitle_dep_label.setText(("安装成功：" if ok else "") + short)
            self.subtitle_dep_label.setToolTip(str(msg))
            self.subtitle_dep_label.setStyleSheet(
                "color: #4ade80;" if ok else "color: #e5635f;")
            (InfoBar.success if ok else InfoBar.error)(
                "安装成功" if ok else "安装失败", short,
                duration=4000 if ok else 6000,
                position=InfoBarPosition.TOP, parent=self)
        self._refresh_subtitle_devices()

    def _refresh_subtitle_model_status(self, _index=None):
        import speech_recognizer as asr
        name = str(self.subtitle_model_combo.currentData() or 'tiny')
        try:
            ready = bool(asr.resolve_model_path(name)[0])
        except Exception:
            ready = False
        engine = str(self.subtitle_engine_combo.currentData() or 'local')
        if engine != 'local':
            self.subtitle_model_status.setText(
                "当前使用在线识别服务，本地模型不会被加载")
            self.subtitle_download_btn.setVisible(False)
        elif ready:
            self.subtitle_model_status.setText(
                f"{name} 已就绪 · 离线免费使用 · 实时推荐")
            self.subtitle_download_btn.setVisible(False)
        else:
            self.subtitle_model_status.setText(
                f"{name} 尚未下载，点右侧按钮下载后即可离线实时识别")
            self.subtitle_download_btn.setVisible(True)
        # 本地模型模式用不到服务地址 / 密钥 / 模型名，置灰避免误以为必填
        for edit in (getattr(self, 'subtitle_api_url', None),
                     getattr(self, 'subtitle_api_key', None),
                     getattr(self, 'subtitle_api_model', None)):
            if edit is not None:
                edit.setEnabled(engine != 'local')
        if hasattr(self, 'subtitle_test_label'):
            # 换了引擎，上一次的「✅ 可用」不再适用于当前引擎
            self.subtitle_test_label.clear()
            self.subtitle_test_label.setStyleSheet("")
        if hasattr(self, 'subtitle_test_btn'):
            self.subtitle_test_btn.setEnabled(engine != 'local')
            self.subtitle_test_btn.setToolTip(
                "只探测服务地址与密钥是否可用（不上传音频、不写配置）"
                if engine != 'local'
                else "当前用本地模型，不需要测试服务连接；"
                     "可在「翻译设置」里点「测试翻译连接」")

    def _download_subtitle_model(self, cancel=None):
        name = str(self.subtitle_model_combo.currentData() or 'tiny')
        self._downloading_model = name
        # 下载中锁定引擎 / 模型下拉：切换会触发 _refresh_subtitle_model_status
        # 把下载按钮隐藏，用户就找不到「取消下载」了。
        self.subtitle_model_combo.setEnabled(False)
        self.subtitle_engine_combo.setEnabled(False)
        self.subtitle_model_status.setToolTip("")
        self.subtitle_model_status.setStyleSheet("")
        self.subtitle_model_status.setText(
            f"正在下载 {name} 模型…（可点右侧按钮取消）")

        def work():
            # import / 回调构造失败也必须发完成信号，否则按钮和两个下拉框
            # 会永远停在「正在取消…」的禁用状态。
            try:
                import speech_recognizer as asr
                import time
            except Exception as e:
                self._sig_caption_model.emit(False, f"下载组件加载失败：{e}")
                return
            last = [0.0]

            def on_progress(done, total):
                # 底层是字节级回调，不节流会把 UI 事件队列冲爆
                now = time.monotonic()
                if (now - last[0] < 0.2 and done and total
                        and done < total):
                    return
                last[0] = now
                if cancel is not None and cancel.is_set():
                    return
                self._sig_model_progress.emit(int(done), int(total))

            try:
                asr.download_model(name, on_progress=on_progress,
                                   cancel_event=cancel)
                self._sig_caption_model.emit(
                    True, f"{name} 下载完成，可以开始实时字幕")
            except asr.ModelDownloadCancelled:
                self._sig_caption_model.emit(False, "已取消下载")
            except Exception as e:
                self._sig_caption_model.emit(False, f"下载失败：{e}")

        threading.Thread(target=work, daemon=True).start()

    @Slot(int, int)
    def _on_model_progress(self, done, total):
        # 下载已结束后忽略迟到回调，别把「下载完成」覆盖掉。
        # 不能用按钮 isEnabled() 判断：下载中按钮必须保持可点才能取消。
        if 'model' not in self._task_busy:
            return
        name = getattr(self, '_downloading_model', '') or '模型'
        if total > 0:
            pct = min(100, int(done) * 100 // int(total))
            self.subtitle_model_status.setText(
                f"正在下载 {name} · {done / 1048576:.0f} / "
                f"{total / 1048576:.0f} MB（{pct}%）· 可点右侧取消")
        else:
            self.subtitle_model_status.setText(
                f"正在下载 {name}…（正在连接镜像，请稍候）")

    def _on_caption_model_done(self, ok, msg):
        cancelled = self._task_finish('model') and not ok
        self.subtitle_model_combo.setEnabled(True)
        self.subtitle_engine_combo.setEnabled(True)
        self._refresh_model_cache()
        if cancelled or (not ok and msg == "已取消下载"):
            self.subtitle_model_status.setText(
                "已取消下载 · 未完成的文件已保留，下次点下载会自动续传")
            self.subtitle_model_status.setToolTip("")
            self.subtitle_model_status.setStyleSheet("color: #eab308;")
            self.subtitle_download_btn.setVisible(True)
            return
        if ok:
            self.subtitle_model_status.setToolTip("")
            self.subtitle_model_status.setStyleSheet("")
            self._refresh_subtitle_model_status()
        else:
            # 英文 HF 异常原文只放 tooltip，界面给中文原因 + 可操作下一步
            self.subtitle_model_status.setText(friendly_model_error(msg))
            self.subtitle_model_status.setToolTip(str(msg))
            self.subtitle_model_status.setStyleSheet("color: #e5635f;")
            self.subtitle_download_btn.setVisible(True)

    # ── 模型缓存 ────────────────────────────────────────────

    def _refresh_model_cache(self):
        """只读统计模型缓存占用（只读文件元数据，不读内容）。"""
        import speech_recognizer as asr
        info = asr.model_cache_info()
        self.model_cache_label.setText(asr.model_cache_summary(info))
        lines = [
            f"· {m['name']}  {asr.human_size(m['bytes'])}  "
            f"{'可用' if m['ready'] else '未完成下载（可续传）'}"
            for m in info.get('models', [])]
        self.model_cache_label.setToolTip(
            "本机模型缓存目录（每台电脑不同，请用「打开目录」按钮定位）："
            + str(info['root']) + "\n"
            + ("\n".join(lines) if lines else "（暂无模型）"))
        self.model_cache_open_btn.setEnabled(True)

    def _open_model_cache(self):
        import os
        import speech_recognizer as asr
        root = str(asr.model_cache_info()['root'])
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "无法打开目录", str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(root))
        self._refresh_model_cache()

    def _copy_model_cache_path(self):
        import speech_recognizer as asr
        QApplication.clipboard().setText(
            str(asr.model_cache_info()['root']))
        from qfluentwidgets import InfoBar, InfoBarPosition
        InfoBar.success("已复制", "模型缓存目录路径已复制到剪贴板",
                        duration=2500, position=InfoBarPosition.TOP,
                        parent=self)

    def _refresh_gpu_status(self, refresh=False):
        """更新 GPU 检测结果文字（供用户判断能否开 GPU 加速）。"""
        if not hasattr(self, "gpu_status_label"):
            return
        try:
            import speech_recognizer as asr
            available, desc = asr.detect_gpu(refresh=refresh)
        except Exception as e:
            available, desc = False, f"检测失败: {e}"
        if available:
            self.gpu_status_label.setText(
                f"✅ {desc}，选择「GPU 加速」可明显提升识别速度")
            self.gpu_status_label.setStyleSheet("color: #4ade80;")
        else:
            self.gpu_status_label.setText(
                f"⚠ {desc}，将使用 CPU 识别（速度较慢但兼容性好）")
            self.gpu_status_label.setStyleSheet("color: #eab308;")

    # ── 常规设置 ────────────────────────────────────────────

    def _build_general_section(self) -> _Section:
        section = _Section("常规设置", "启动方式与更新检测")

        self.minimize_to_tray_check = FlatCheckBox("关闭窗口时最小化到系统托盘")
        self.minimize_to_tray_check.setChecked(
            self._cfg.get("minimize_to_tray", True))
        section.add("关闭行为:", self.minimize_to_tray_check,
                    tip="点关闭按钮时隐藏到托盘，托盘右键可退出")

        self.autostart_check = FlatCheckBox("开机自动启动")
        try:
            from main_window import _get_autostart
            self.autostart_check.setChecked(_get_autostart())
        except Exception:
            self.autostart_check.setChecked(False)
        section.add("自启动:", self.autostart_check,
                    tip="开机后自动启动并最小化到托盘")

        self.auto_check_update_check = FlatCheckBox("启动时自动检测更新")
        self.auto_check_update_check.setChecked(
            self._cfg.get("auto_check_update", True))
        section.add("自动更新检测:", self.auto_check_update_check,
                    tip="每次启动时自动检查是否有新版本")

        self.check_update_btn = QPushButton("检查更新")
        self.check_update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_update_btn.clicked.connect(self._check_update)
        section.add("手动检测:", self.check_update_btn,
                    tip="手动检查是否有新版本；发现新版可直接一键升级")

        version_label = QLabel(f"v{APP_VERSION}")
        version_label.setObjectName("caption")
        section.add("当前版本:", version_label,
                    tip="安装新版后这里的版本号会随之变化")
        return section

    # ── 自动操作 ────────────────────────────────────────────

    def _build_auto_section(self) -> _Section:
        section = _Section("自动操作", "截图识别完成后的自动处理")

        self.auto_translate_check = FlatCheckBox("截图选区后自动翻译")
        self.auto_translate_check.setChecked(
            self._cfg.get("auto_translate", False))
        section.add("自动翻译:", self.auto_translate_check,
                    tip="选区完成后直接翻译，点击译文可切换回原文")

        self.auto_copy_check = FlatCheckBox("截图识别后自动复制到剪贴板")
        self.auto_copy_check.setChecked(self._cfg.get("auto_copy", False))
        section.add("自动复制:", self.auto_copy_check,
                    tip="开启后截图识别完成自动复制文本")

        self.auto_copy_combo = QComboBox()
        self.auto_copy_combo.addItems(["原文", "译文"])
        current_target = self._cfg.get("auto_copy_target", "source")
        self.auto_copy_combo.setCurrentText(
            "原文" if current_target == "source" else "译文")
        self.auto_copy_combo.setMinimumWidth(120)
        section.add("复制内容:", self.auto_copy_combo,
                    tip="选择自动复制原文还是译文")
        return section

    # ── 逻辑（保持原有行为）────────────────────────────────

    def _on_api_changed(self, api_name):
        if hasattr(self, 'translation_test_label'):
            # 换了引擎，旧的「✅ 可用」已经失效，别留着误导
            self.translation_test_label.clear()
            self.translation_test_label.setStyleSheet("")
        api_value = TRANSLATION_APIS.get(api_name, "")
        if api_value in self._api_page_map:
            self._api_stack.setCurrentIndex(self._api_page_map[api_value])
            self._api_container.setVisible(True)
        else:
            self._api_container.setVisible(False)

    def _get_selected_engine_key(self):
        display_name = self.ocr_engine_combo.currentText()
        for key, name in SUPPORTED_OCR_ENGINES.items():
            if name == display_name:
                return key
        return OCR_ENGINE

    def _refresh_ocr_status(self):
        from ocr_recognizer import check_engine_available
        engine = self._get_selected_engine_key()
        if check_engine_available(engine):
            self.ocr_status_label.setText("已安装 ✓")
            self.ocr_status_label.setStyleSheet("color: #4ade80;")
            self.ocr_install_btn.setVisible(False)
        elif engine == 'windows':
            self.ocr_status_label.setText(
                "需要安装 winrt 包，或检查系统是否已安装对应语言包")
            self.ocr_status_label.setStyleSheet("color: #eab308;")
            self.ocr_install_btn.setVisible(True)
            self.ocr_install_btn.setText("安装 winrt")
        else:
            engine_name = SUPPORTED_OCR_ENGINES.get(engine, engine)
            self.ocr_status_label.setText(
                f"未安装 {engine_name}，请先安装再使用")
            self.ocr_status_label.setStyleSheet("color: #e5635f;")
            self.ocr_install_btn.setVisible(True)
            self.ocr_install_btn.setText("安装")

    def _on_ocr_engine_changed(self, _text):
        self._refresh_ocr_status()

    def _install_ocr_engine(self, cancel=None):
        from ocr_recognizer import install_engine_with_progress
        engine = self._get_selected_engine_key()
        # 安装中锁定引擎下拉：切换会刷新状态并把「取消安装」按钮覆盖掉
        self.ocr_engine_combo.setEnabled(False)
        self.ocr_status_label.setText("正在安装，请稍候…（可点左侧按钮取消）")
        self.ocr_status_label.setToolTip("")
        self.ocr_status_label.setStyleSheet("color: #eab308;")
        self.ocr_progress.setVisible(True)
        self.ocr_progress.setRange(0, 0)
        self.ocr_progress.setFormat("准备中...")

        import threading

        def on_progress(line):
            self._sig_install_progress.emit(line.strip())

        def do_install():
            try:
                ok, msg = install_engine_with_progress(
                    engine, on_progress, cancel=cancel)
            except Exception as exc:
                # 线程里抛出未捕获异常会让进度条与按钮永远停在「安装中」
                ok, msg = False, f"安装失败：{exc}"
            self._sig_install_result.emit(ok, msg)

        threading.Thread(target=do_install, daemon=True).start()

    def _update_install_progress(self, line):
        line = line.strip()
        if not line:
            return
        if "Downloading" in line or "下载" in line:
            self.ocr_progress.setRange(0, 0)
            self.ocr_status_label.setText(f"正在下载: {line[:80]}")
        elif "Installing" in line or "安装" in line or "Installing collected" in line:
            self.ocr_progress.setRange(0, 0)
            self.ocr_status_label.setText(f"正在安装: {line[:80]}")
        elif "Collecting" in line or "Downloading" in line:
            self.ocr_progress.setRange(0, 0)
            self.ocr_status_label.setText(f"正在下载依赖: {line[:80]}")
        elif "Successfully" in line:
            self.ocr_progress.setRange(0, 100)
            self.ocr_progress.setValue(100)
            self.ocr_progress.setFormat("安装完成")
            self.ocr_status_label.setText("安装完成 ✓")
            self.ocr_status_label.setStyleSheet("color: #4ade80;")
        elif "ERROR" in line or "Failed" in line:
            self.ocr_progress.setRange(0, 100)
            self.ocr_progress.setValue(0)
            self.ocr_status_label.setText(f"错误: {line[:100]}")
            self.ocr_status_label.setStyleSheet("color: #e5635f;")
        else:
            self.ocr_status_label.setText(line[:100])

    def _on_install_result(self, ok, msg):
        # 底层已经成功时，即使用户在收尾瞬间又点了一下按钮置位了取消，
        # 也要如实显示「安装成功」。
        cancelled = self._task_finish(
            'ocr', "安装" if ok else "重试安装") and not ok
        self.ocr_engine_combo.setEnabled(True)
        self.ocr_progress.setVisible(False)
        from qfluentwidgets import InfoBar, InfoBarPosition
        if cancelled:
            self.ocr_install_btn.setText("重新安装")
            self.ocr_status_label.setText(
                "已取消安装 · 可重新点按钮安装，已下载的文件会自动续传")
            self.ocr_status_label.setToolTip("")
            self.ocr_status_label.setStyleSheet("color: #eab308;")
            InfoBar.info("已取消", "OCR 组件安装已中断，可随时继续",
                         duration=3000, position=InfoBarPosition.TOP,
                         parent=self)
            return
        if ok:
            self._refresh_ocr_status()
            InfoBar.success("安装成功", "OCR 引擎已安装，可直接切换使用；升级后无需重装",
                            duration=3000, position=InfoBarPosition.TOP,
                            parent=self)
        else:
            # pip 英文原始日志不要再直接抛给桌面用户：主界面只留一句
            # 中文可操作提示，原始日志放进 tooltip / 悬停可见。
            friendly = friendly_install_error(msg)
            self.ocr_status_label.setText(friendly)
            self.ocr_status_label.setToolTip(str(msg)[:2000])
            self.ocr_status_label.setStyleSheet("color: #e5635f;")
            # 错误不能只留一行会被冲掉的状态：给「重试」和「复制诊断」
            # 两个可点动作，并留足 10 秒阅读时间。
            ui_feedback.show_error_bar(
                self, "安装失败", friendly,
                actions=[("重试", lambda: self.ocr_install_btn.click())],
                detail=str(msg)[:4000], source="OCR 组件安装")

    def _save(self):
        api_name = self.api_combo.currentText()
        if api_name in TRANSLATION_APIS:
            new_api = TRANSLATION_APIS[api_name]
            if self._cfg.get("translation_api") != new_api:
                self._cfg["translation_api"] = new_api

        self._cfg["bing_azure_key"] = self.bing_azure_key.text().strip()
        self._cfg["bing_azure_region"] = self.bing_azure_region.text().strip()
        self._cfg["mymemory_api_key"] = self.mymemory_api_key.text().strip()
        self._cfg["google_cloud_key"] = self.google_cloud_key.text().strip()
        self._cfg["deepl_api_key"] = self.deepl_key.text().strip()
        self._cfg["baidu_appid"] = self.baidu_appid.text().strip()
        self._cfg["baidu_secret"] = self.baidu_secret.text().strip()
        self._cfg["tencent_secret_id"] = self.tencent_secret_id.text().strip()
        self._cfg["tencent_secret_key"] = self.tencent_secret_key.text().strip()
        self._cfg["volcengine_app_id"] = self.volcengine_appid.text().strip()
        self._cfg["volcengine_token"] = self.volcengine_token.text().strip()

        # AI 润色通道与凭证
        self._cfg.update(self._collect_polish_settings())
        # 快捷键：空字符串 = 不占用按键，必须允许保存「清空」
        for action, entry in self._hotkey_inputs.items():
            self._cfg[config_key(action)] = normalize(entry.value())
        merged = all_hotkeys(self._cfg)
        conflicts = find_conflicts(merged)
        reserved = reserved_conflicts(merged)
        if conflicts or reserved:
            parts = []
            if conflicts:
                parts.append(
                    "【多个动作用了同一组合】按一次键会触发多个动作：\n"
                    + "\n".join(
                        f"  {display_hotkey(combo)} → {'、'.join(labels)}"
                        for combo, labels in conflicts))
            if reserved:
                parts.append(
                    "【与程序内固定操作冲突】这些组合被搜索 / 全选 / 复制等"
                    "占用，设了也不会生效：\n"
                    + "\n".join(
                        f"  {display_hotkey(combo)} → 已被「{use}」占用"
                        f"（{'、'.join(labels)}）"
                        for combo, use, labels in reserved))
            QMessageBox.warning(
                self, "快捷键冲突",
                "\n\n".join(parts)
                + "\n\n建议改成不同组合（设置仍会保存，改好后自动重新注册）。")
        self._cfg["auto_translate"] = self.auto_translate_check.isChecked()
        self._cfg["auto_copy"] = self.auto_copy_check.isChecked()
        self._cfg["auto_copy_target"] = (
            "source" if self.auto_copy_combo.currentText() == "原文"
            else "translation")
        self._cfg["minimize_to_tray"] = self.minimize_to_tray_check.isChecked()
        self._cfg["auto_check_update"] = self.auto_check_update_check.isChecked()

        # 语音识别选项
        self._cfg["asr_vad_enabled"] = self.asr_vad_check.isChecked()
        self._cfg["asr_vad_threshold"] = round(
            self.asr_threshold_slider.value() / 100.0, 2)
        self._cfg["asr_beam_size"] = int(
            self.asr_beam_combo.currentData() or 5)
        self._cfg["asr_stream_enabled"] = self.asr_stream_check.isChecked()
        self._cfg["asr_device"] = str(
            self.asr_device_combo.currentData() or 'auto')

        # 实时翻译选项
        self._cfg["live_interval_ms"] = int(
            self.live_interval_combo.currentData() or 1200)
        self._cfg["live_show_source"] = self.live_source_check.isChecked()

        # 实时字幕选项
        self._cfg["subtitle_audio_source"] = str(
            self.subtitle_source_combo.currentData() or 'mic')
        self._cfg["subtitle_audio_device"] = str(
            self.subtitle_device_combo.currentData() or '')
        self._cfg["subtitle_engine"] = str(
            self.subtitle_engine_combo.currentData() or 'local')
        self._cfg["subtitle_model"] = str(
            self.subtitle_model_combo.currentData() or 'tiny')
        self._cfg["subtitle_api_url"] = self.subtitle_api_url.text().strip()
        self._cfg["subtitle_api_key"] = self.subtitle_api_key.text().strip()
        self._cfg["subtitle_api_model"] = (
            self.subtitle_api_model.text().strip() or 'whisper-1')
        self._cfg["subtitle_language"] = str(
            self.subtitle_lang_combo.currentData() or 'auto')
        self._cfg["subtitle_translate"] = (
            self.subtitle_translate_check.isChecked())
        self._cfg["subtitle_display_mode"] = str(
            self.subtitle_mode_combo.currentData() or 'both')
        self._cfg["subtitle_window_ms"] = int(
            self.subtitle_window_combo.currentData() or 2500)
        self._cfg["subtitle_font_size"] = int(
            self.subtitle_font_combo.currentData() or 22)
        self._cfg["subtitle_max_lines"] = int(
            self.subtitle_lines_combo.currentData() or 3)
        self._cfg["subtitle_bg_alpha"] = int(
            self.subtitle_alpha_combo.currentData() or 190)
        self._cfg["subtitle_click_through"] = (
            self.subtitle_through_check.isChecked())

        # OCR 引擎
        old_engine = self._cfg.get("ocr_engine", OCR_ENGINE)
        new_engine = self._get_selected_engine_key()
        if old_engine != new_engine:
            self._cfg["ocr_engine"] = new_engine
            try:
                from ocr_recognizer import reset_ocr_instance
                reset_ocr_instance()
            except Exception:
                pass

        # 开机自启动：注册表写失败要让用户看见，不能悄悄弹「保存成功」
        autostart_ok = True
        try:
            from main_window import _set_autostart
            autostart_ok = _set_autostart(self.autostart_check.isChecked())
        except Exception:
            autostart_ok = False

        # 密钥变更时清除免费引擎缓存；翻译器实例由主窗口在 saved 信号里重建
        try:
            from free_translator import invalidate_free_translator
            invalidate_free_translator("bing_free")
            invalidate_free_translator("mymemory_free")
            invalidate_free_translator("google_free")
        except Exception:
            pass

        # 只提交本页实际变化的键，避免整份覆盖抹掉音视频页刚保存的设置
        patch = {k: v for k, v in self._cfg.items()
                 if self._saved_cfg.get(k) != v}
        if save_user_config(patch):
            self._cfg = load_user_config()
            self._saved_cfg = dict(self._cfg)
            from qfluentwidgets import InfoBar, InfoBarPosition
            if not autostart_ok:
                InfoBar.warning(
                    "设置已保存，但开机自启未生效",
                    "写入注册表失败，请以普通用户身份重试或检查安全软件拦截",
                    duration=6000, position=InfoBarPosition.TOP, parent=self)
            else:
                InfoBar.success("保存成功", "设置已保存", duration=2000,
                                position=InfoBarPosition.TOP, parent=self)
            self.saved.emit()
        else:
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.error("保存失败", "无法写入配置文件", duration=3000,
                          position=InfoBarPosition.TOP, parent=self)

    def _restore_defaults(self):
        """把本页所有设置恢复为默认值（需点保存后生效）。"""
        if self._task_busy:
            # 恢复默认会重设各 combo，即使控件被禁用也会发
            # currentIndexChanged，可能把「取消下载」按钮隐藏掉。
            QMessageBox.information(
                self, "请先结束当前操作",
                "正在安装组件或下载模型，请先完成或点按钮取消，"
                "再恢复默认设置。")
            return
        ret = QMessageBox.question(
            self, "恢复默认设置",
            "将把本页所有设置恢复为默认值，包括：\n"
            "  · 清空已填写的 API 密钥 / Token / Cookie\n"
            "  · 清空全部全局快捷键；窗口内快捷键恢复为默认组合\n"
            "  · 重置实时字幕、OCR、翻译引擎等选项\n\n"
            "需要再点一次「保存设置」才会写入配置文件。确定吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return

        default_api_name = next(
            (name for name, value in TRANSLATION_APIS.items()
             if value == DEFAULT_TRANSLATION_API), None)
        if default_api_name:
            self.api_combo.setCurrentText(default_api_name)
        for edit in (self.bing_azure_key, self.bing_azure_region,
                     self.mymemory_api_key, self.google_cloud_key,
                     self.deepl_key, self.baidu_appid, self.baidu_secret,
                     self.tencent_secret_id, self.tencent_secret_key,
                     self.volcengine_appid, self.volcengine_token):
            edit.clear()

        default_polish = str(
            DEFAULT_AI_POLISH_SETTINGS.get("ai_polish_provider", "pollinations"))
        polish_idx = self.polish_provider_combo.findData(default_polish)
        self.polish_provider_combo.setCurrentIndex(max(0, polish_idx))
        for edit in (self.polish_deepseek_token, self.polish_doubao_cookie,
                     self.polish_api_url, self.polish_api_key,
                     self.polish_model):
            edit.clear()
        self.polish_test_label.clear()
        self.translation_test_label.clear()
        self.translation_test_label.setStyleSheet("")
        self.subtitle_test_label.clear()
        self.subtitle_test_label.setStyleSheet("")

        default_engine_name = SUPPORTED_OCR_ENGINES.get(OCR_ENGINE, "")
        if default_engine_name:
            self.ocr_engine_combo.setCurrentText(default_engine_name)

        # 全局清空、窗口内恢复默认组合；保存时必须真的把值写回配置
        defaults = default_hotkeys()
        for action, entry in self._hotkey_inputs.items():
            entry.setValue(defaults.get(action, ''))
        self.minimize_to_tray_check.setChecked(True)
        self.autostart_check.setChecked(False)
        self.auto_check_update_check.setChecked(True)
        self.auto_translate_check.setChecked(False)
        self.auto_copy_check.setChecked(False)
        self.auto_copy_combo.setCurrentText("原文")

        self.asr_vad_check.setChecked(
            bool(DEFAULT_ASR_SETTINGS["asr_vad_enabled"]))
        self.asr_threshold_slider.setValue(
            int(float(DEFAULT_ASR_SETTINGS["asr_vad_threshold"]) * 100))
        beam_idx = self.asr_beam_combo.findData(
            int(DEFAULT_ASR_SETTINGS["asr_beam_size"]))
        self.asr_beam_combo.setCurrentIndex(max(0, beam_idx))
        self.asr_stream_check.setChecked(
            bool(DEFAULT_ASR_SETTINGS["asr_stream_enabled"]))
        device_idx = self.asr_device_combo.findData(
            str(DEFAULT_ASR_SETTINGS["asr_device"]))
        self.asr_device_combo.setCurrentIndex(max(0, device_idx))

        live_idx = self.live_interval_combo.findData(
            int(DEFAULT_LIVE_SETTINGS["live_interval_ms"]))
        self.live_interval_combo.setCurrentIndex(max(0, live_idx))
        self.live_source_check.setChecked(
            bool(DEFAULT_LIVE_SETTINGS["live_show_source"]))

        d = DEFAULT_SUBTITLE_SETTINGS
        for combo, key in (
                (self.subtitle_source_combo, 'subtitle_audio_source'),
                (self.subtitle_engine_combo, 'subtitle_engine'),
                (self.subtitle_model_combo, 'subtitle_model'),
                (self.subtitle_lang_combo, 'subtitle_language'),
                (self.subtitle_mode_combo, 'subtitle_display_mode'),
                (self.subtitle_window_combo, 'subtitle_window_ms'),
                (self.subtitle_font_combo, 'subtitle_font_size'),
                (self.subtitle_lines_combo, 'subtitle_max_lines'),
                (self.subtitle_alpha_combo, 'subtitle_bg_alpha')):
            idx = combo.findData(d[key])
            combo.setCurrentIndex(max(0, idx))
        self.subtitle_translate_check.setChecked(bool(d['subtitle_translate']))
        self.subtitle_through_check.setChecked(
            bool(d['subtitle_click_through']))
        self.subtitle_api_url.clear()
        self.subtitle_api_key.clear()
        self.subtitle_api_model.setText('whisper-1')
        self._refresh_subtitle_devices(select='')

        from qfluentwidgets import InfoBar, InfoBarPosition
        InfoBar.success("已恢复默认", "点击「保存设置」后生效", duration=3000,
                        position=InfoBarPosition.TOP, parent=self)

    def get_config(self):
        return self._cfg

    def show_hotkey_warning(self, msg):
        """配置里快捷键撞车时用警告色，别和「被其它程序占用」混为一谈。"""
        from qfluentwidgets import InfoBar, InfoBarPosition
        InfoBar.warning(
            "部分快捷键未生效",
            f"{msg}\n改好组合后会自动重新注册，无需重启程序",
            duration=6000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

    def show_hotkey_error(self, msg):
        """快捷键注册失败时显示错误提示。"""
        from qfluentwidgets import InfoBar, InfoBarPosition
        InfoBar.error(
            "快捷键注册失败",
            f"{msg}\n快捷键可能被其他程序占用，请换一个组合",
            duration=5000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

    def _check_update(self):
        """手动检查更新：委托主窗口，统一走「下载 → 校验 → 一键升级」流程。"""
        from qfluentwidgets import InfoBar, InfoBarPosition

        win = self.window()
        check = getattr(win, "check_update", None)
        if not callable(check):
            InfoBar.warning("无法检查更新", "主窗口不可用，请重启程序后重试",
                            duration=4000, position=InfoBarPosition.TOP,
                            parent=self)
            return

        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText("检测中...")
        # timer 有 parent，页面销毁后不会再回调到已删除的 C++ 对象
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._restore_check_update_btn)
        timer.start(2500)

        if not check(silent=False):
            InfoBar.info("正在检测", "上一次检测尚未完成，请稍候再试",
                         duration=3000, position=InfoBarPosition.TOP,
                         parent=self)

    def _restore_check_update_btn(self):
        self.check_update_btn.setEnabled(True)
        self.check_update_btn.setText("检查更新")
