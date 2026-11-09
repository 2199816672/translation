#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置页：翻译 API、密钥、AI 润色、OCR 引擎、快捷键与自动化。"""
import threading

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QProgressBar, QScrollArea, QSizePolicy,
    QSlider, QStackedWidget, QVBoxLayout, QWidget,
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
from hotkeys import (DEFAULT_HOTKEYS, HOTKEY_ACTIONS, config_key,
                     find_conflicts, get_hotkeys, normalize)


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsContainer")
        # 普通 QWidget 需要显式开启才会绘制 QSS 的 background
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._cfg = load_user_config()
        # 保存基线：写盘时只提交相对它变化的键，避免覆盖其它页面刚写入的配置
        self._saved_cfg = dict(self._cfg)
        self._sig_install_progress.connect(self._update_install_progress)
        self._sig_install_result.connect(self._on_install_result)
        self._sig_polish_test.connect(self._on_polish_test_result)
        self._sig_caption_install.connect(self._on_caption_install_done)
        self._sig_caption_model.connect(self._on_caption_model_done)
        self._build_ui()

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
        subtitle = QLabel("翻译引擎、AI 润色、OCR 识别、启动方式与自动化选项", self)
        subtitle.setObjectName("caption")
        head.addWidget(subtitle)
        layout.addLayout(head)

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
                self._build_auto_section):
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
            "搜索设置：输入关键字即时筛选（如 字幕、快捷键、OCR、模型、密钥…）")
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

        # 固定底栏：保存按钮始终可见
        footer = QWidget(self)
        foot_pal = footer.palette()
        foot_pal.setColor(QPalette.ColorRole.Window, QColor(Palette.BG))
        footer.setPalette(foot_pal)
        footer.setAutoFillBackground(True)
        foot_row = QHBoxLayout(footer)
        foot_row.setContentsMargins(28, 10, 28, 16)
        foot_row.setSpacing(12)
        hint = QLabel("修改后点击右侧按钮保存生效", footer)
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
            "默认全部为空，不占用任何系统按键。点输入框后按下组合键即可录入；"
            "按 Delete / Backspace 或点「清除」可清空。全局生效，最小化到托盘也能用")
        keys = get_hotkeys(self._cfg)
        self._hotkey_inputs = {}
        for action, (label, _cfg_key, tip) in HOTKEY_ACTIONS.items():
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
            "冲突提示：同一个组合只能绑定一个功能；组合被其它软件占用时会提示"
            "注册失败，换一个即可。改完记得点「保存设置」。\n"
            "「恢复默认」会把这里全部清空。")
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

        section.add_caption(
            "免费：Bing 微软 · MyMemory · Google（均可选填自己的密钥）\n"
            "付费：DeepL · 百度 · 腾讯 · 火山（需自备密钥）")
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

    def _mk_edit(self, placeholder):
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMinimumWidth(320)
        edit.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        return edit

    def _build_key_page(self, tip, fields):
        """引擎密钥子页面：统一的右对齐标签 + 自适应输入框。"""
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
            "💡 Windows 自带：开箱即用，无需下载，速度快\n"
            "   EasyOCR：识别更准确，首次需下载约 100MB 模型")

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
        self.ocr_install_btn.setToolTip("通过 pip 安装所选 OCR 引擎，安装过程显示在下方的进度条")
        self.ocr_install_btn.clicked.connect(self._install_ocr_engine)
        self.ocr_install_btn.setVisible(False)
        install_layout.addWidget(self.ocr_install_btn)
        self.ocr_progress = QProgressBar()
        self.ocr_progress.setVisible(False)
        self.ocr_progress.setTextVisible(True)
        self.ocr_progress.setRange(0, 0)
        install_layout.addWidget(self.ocr_progress, 1)
        section.add_row(install_row)

        self.ocr_engine_combo.currentTextChanged.connect(self._on_ocr_engine_changed)
        self._refresh_ocr_status()
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
            "听写麦克风或电脑内部声音；关闭字幕框后识别继续，结果同步显示在「翻译」页的"
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
            tip="麦克风：听你说话；电脑内部声音：听视频 / 音乐 / 线上会议\n"
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
            self._download_subtitle_model)
        model_lay.addWidget(self.subtitle_download_btn)
        section.add_row(model_row)
        self._refresh_subtitle_model_status()

        self.subtitle_api_url = self._mk_edit(
            "https://api.example.com/v1 或 http://127.0.0.1:8000/v1")
        self.subtitle_api_url.setText(str(cfg.get('subtitle_api_url') or ''))
        section.add("服务地址:", self.subtitle_api_url,
                    tip="仅「在线 API / 免费本地服务」需要；填到 /v1 即可")

        self.subtitle_api_key = self._mk_edit("免费本地服务可留空")
        self.subtitle_api_key.setText(str(cfg.get('subtitle_api_key') or ''))
        self.subtitle_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        section.add("API 密钥:", self.subtitle_api_key)

        self.subtitle_api_model = self._mk_edit("whisper-1")
        self.subtitle_api_model.setText(
            str(cfg.get('subtitle_api_model') or 'whisper-1'))
        section.add("服务模型名:", self.subtitle_api_model)

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
                    tip="浮层控制条的 A- / A+ 也能临时调整")

        self.subtitle_lines_combo = QComboBox()
        for n in (2, 3, 4, 5):
            self.subtitle_lines_combo.addItem(f"{n} 条", n)
        _select(self.subtitle_lines_combo, cfg.get('subtitle_max_lines'))
        section.add("保留行数:", self.subtitle_lines_combo)

        self.subtitle_alpha_combo = QComboBox()
        for label, value in (("很淡", 110), ("偏淡", 150), ("标准", 190),
                             ("偏浓", 220), ("不透明", 250)):
            self.subtitle_alpha_combo.addItem(label, value)
        _select(self.subtitle_alpha_combo, cfg.get('subtitle_bg_alpha'))
        section.add("底色深浅:", self.subtitle_alpha_combo)

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
        self.subtitle_install_btn = QPushButton("安装内录组件")
        self.subtitle_install_btn.setProperty("kind", "ghost")
        self.subtitle_install_btn.setFixedWidth(120)
        self.subtitle_install_btn.clicked.connect(self._install_loopback)
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
                msg = "电脑内部声音：WASAPI 环回组件已就绪，可选具体扬声器"
            else:
                msg = ("电脑内部声音：缺少 soundcard，点右侧一键安装；"
                       "若系统已启用「立体声混音」也可直接在上方选择")
        else:
            msg = ("麦克风：sounddevice 已就绪" if deps['mic']
                   else "麦克风：缺少 sounddevice（pip install sounddevice）")
        self.subtitle_dep_label.setText(msg)
        self.subtitle_install_btn.setVisible(
            source == 'loopback' and not deps['loopback'])

    def _install_loopback(self):
        self.subtitle_install_btn.setEnabled(False)
        self.subtitle_dep_label.setText("正在安装 soundcard 内录组件…")

        def work():
            try:
                from live_caption import install_loopback_support
                ok, msg = install_loopback_support()
            except Exception as exc:
                # 任何逃逸异常都要发完成信号，否则「安装」按钮会永久禁用
                ok, msg = False, f"安装失败：{exc}"
            self._sig_caption_install.emit(ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def _on_caption_install_done(self, ok, msg):
        self.subtitle_install_btn.setEnabled(True)
        self.subtitle_dep_label.setText(("安装成功：" if ok else "") + msg)
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

    def _download_subtitle_model(self):
        name = str(self.subtitle_model_combo.currentData() or 'tiny')
        self.subtitle_download_btn.setEnabled(False)
        self.subtitle_model_status.setText(f"正在下载 {name} 模型…")

        def work():
            import speech_recognizer as asr
            try:
                asr.download_model(name)
                self._sig_caption_model.emit(
                    True, f"{name} 下载完成，可以开始实时字幕")
            except Exception as e:
                self._sig_caption_model.emit(False, f"下载失败：{e}")

        threading.Thread(target=work, daemon=True).start()

    def _on_caption_model_done(self, ok, msg):
        self.subtitle_download_btn.setEnabled(True)
        if ok:
            self._refresh_subtitle_model_status()
        else:
            self.subtitle_model_status.setText(msg)
            self.subtitle_download_btn.setVisible(True)

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

    def _install_ocr_engine(self):
        from ocr_recognizer import install_engine_with_progress
        engine = self._get_selected_engine_key()
        self.ocr_install_btn.setEnabled(False)
        self.ocr_install_btn.setText("安装中...")
        self.ocr_status_label.setText("正在安装，请稍候...")
        self.ocr_status_label.setStyleSheet("color: #eab308;")
        self.ocr_progress.setVisible(True)
        self.ocr_progress.setRange(0, 0)
        self.ocr_progress.setFormat("准备中...")

        import threading

        def on_progress(line):
            self._sig_install_progress.emit(line.strip())

        def do_install():
            try:
                ok, msg = install_engine_with_progress(engine, on_progress)
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
        self.ocr_install_btn.setEnabled(True)
        self.ocr_progress.setVisible(False)
        if ok:
            self.ocr_install_btn.setText("安装")
            self._refresh_ocr_status()
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.success("安装成功", "OCR 引擎已安装，保存设置后生效",
                            duration=3000, position=InfoBarPosition.TOP,
                            parent=self)
        else:
            self.ocr_install_btn.setText("重试安装")
            self.ocr_status_label.setText(f"安装失败: {msg[:100]}")
            self.ocr_status_label.setStyleSheet("color: #e5635f;")
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.error("安装失败", msg[:200], duration=5000,
                          position=InfoBarPosition.TOP, parent=self)

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
        conflicts = find_conflicts(get_hotkeys(self._cfg))
        if conflicts:
            detail = "\n".join(
                f"{combo.upper()} → {'、'.join(labels)}"
                for combo, labels in conflicts)
            QMessageBox.warning(
                self, "快捷键冲突",
                "以下组合被多个功能占用，只有第一个会生效：\n\n"
                + detail + "\n\n建议改成不同组合（设置仍会保存）。")
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
        ret = QMessageBox.question(
            self, "恢复默认设置",
            "将把本页所有设置恢复为默认值，包括：\n"
            "  · 清空已填写的 API 密钥 / Token / Cookie\n"
            "  · 清空全部快捷键（默认都不占用按键）\n"
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

        default_engine_name = SUPPORTED_OCR_ENGINES.get(OCR_ENGINE, "")
        if default_engine_name:
            self.ocr_engine_combo.setCurrentText(default_engine_name)

        # 默认快捷键全部为空；清空后保存也必须真的写回空值
        for action, entry in self._hotkey_inputs.items():
            entry.setValue(DEFAULT_HOTKEYS.get(action, ''))
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
