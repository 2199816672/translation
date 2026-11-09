#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音视频字幕页：语音识别 → 字幕翻译 → 多格式导出。"""
import os
import threading

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPalette, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QTextEdit, QVBoxLayout, QWidget,
)

from theme import Palette
from config import (
    SUPPORTED_SOURCE_LANGS, SUPPORTED_TARGET_LANGS,
    load_user_config, save_user_config,
)
import speech_recognizer as asr
from format_convert import export_text
from subtitle import compose_entries

FMT_LABELS = {
    'srt': 'SRT 字幕',
    'vtt': 'VTT 字幕',
    'ass': 'ASS 字幕',
    'lrc': 'LRC 歌词',
}

MODE_LABELS = {
    'source': '只导出原文',
    'both': '原文 + 译文',
    'target': '只导出译文',
}

LOCAL_MODE = 'local'
API_MODE = 'api'


class _ElidedLabel(QLabel):
    """宽度不足时省略中间文字，避免把整行布局的 minimumSizeHint 撑大。"""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._full = text
        self._tooltip = None
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        self.setToolTip(text)

    def set_full_text(self, text, tooltip=None):
        self._full = text or ''
        self._tooltip = tooltip
        self._apply_elide()

    def _apply_elide(self):
        # 扣除左右内边距，避免省略号贴边或被裁
        m = self.contentsMargins()
        avail = max(0, self.width() - m.left() - m.right())
        shown = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideMiddle, avail)
        if shown != self.text():
            self.setText(shown)
        self.setToolTip(
            self._tooltip or (self._full if shown != self._full else ''))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()


class AVPage(QWidget):
    """音视频字幕：识别、翻译、导出。"""

    # 请求主窗口用现有翻译器翻译字幕文本
    translateRequested = Signal(list)
    # (kind, lines)：kind 为 source / translation
    polishRequested = Signal(str, list)
    # 内部后台任务回传
    _sig_install_progress = Signal(str)
    _sig_install_done = Signal(bool, str)
    _sig_download_progress = Signal(int, int)
    _sig_download_done = Signal(bool, str)
    _sig_asr_progress = Signal(int, str)
    _sig_asr_segment = Signal(dict)
    _sig_asr_done = Signal(dict)
    _sig_asr_error = Signal(str)
    _sig_export_done = Signal(str)
    _sig_export_error = Signal(str)
    _sig_hw_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("avPage")
        # 普通 QWidget 需要显式开启才能绘制 QSS 里的 background
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.cfg = load_user_config()
        self._file_path = None
        self._segments = []
        self._translations = []
        self._info = {}
        self._cancel = threading.Event()
        self._busy = False
        self._last_out_dir = None
        self._src_code = 'auto'
        self._stream_count = 0
        self._export_mode = self.cfg.get('asr_export_mode', 'source')
        self._hw = None
        self._recommendation = None
        # 首次使用、且从未下载过模型时，等硬件检测结果回来自动选推荐项
        self._auto_select_recommend = (
            not self.cfg.get('asr_model') and not self._any_model_ready())

        self._sig_hw_ready.connect(self._on_hw_ready)
        self._sig_install_progress.connect(self._on_install_progress)
        self._sig_install_done.connect(self._on_install_done)
        self._sig_download_progress.connect(self._on_download_progress)
        self._sig_download_done.connect(self._on_download_done)
        self._sig_asr_progress.connect(self._on_asr_progress)
        self._sig_asr_segment.connect(self._on_asr_segment)
        self._sig_asr_done.connect(self._on_asr_done)
        self._sig_asr_error.connect(self._on_asr_error)
        self._sig_export_done.connect(self._on_export_done)
        self._sig_export_error.connect(self._on_export_error)
        self._build_ui()
        self._on_mode_changed()
        self._reload_models(self.cfg.get('asr_model') or 'small')
        self._refresh_model_status()
        # 硬件探测会 import ctranslate2 / 调 nvidia-smi，放后台避免卡住启动
        threading.Thread(target=self._probe_hardware, daemon=True).start()

    # ── UI ──────────────────────────────────────────────────

    def _build_ui(self):
        # 页面内容比窗口可用高度更高（约 680px），必须可滚动，
        # 否则底部两张结果卡片会被容器硬裁掉、下边框消失。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("avScroll")
        # 窄窗口时允许横向滚动，绝不把内容硬裁掉
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 注意：不要在 QScrollArea 上调用 setStyleSheet("background:...")，
        # 实例级样式表会向下传播到所有子控件，把卡片 / 下拉框的主题色全部覆盖掉。
        # 背景统一交给全局 QSS 中的 #avPage / QScrollArea 规则。
        container = QWidget()
        # QScrollArea 的 viewport 默认按系统调色板（浅色）填充，
        # 卡片之间的空隙会露白；这里让内容容器自己铺上主题底色。
        container.setAutoFillBackground(True)
        c_pal = container.palette()
        c_pal.setColor(QPalette.ColorRole.Window, QColor(Palette.BG))
        container.setPalette(c_pal)
        root = QVBoxLayout(container)
        root.setContentsMargins(28, 20, 28, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("音视频字幕", self)
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.status_dot = QLabel("●", self)
        self.status_dot.setStyleSheet(f"color: {Palette.STOPPED}; font-size: 14px;")
        self.status_label = QLabel("等待文件", self)
        self.status_label.setObjectName("fieldLabel")
        header.addWidget(self.status_dot)
        header.addWidget(self.status_label)
        root.addLayout(header)

        root.addWidget(self._build_file_card())
        root.addWidget(self._build_model_card())
        root.addWidget(self._build_export_card())

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.start_btn = self._btn("开始识别", kind="primary")
        self.start_btn.clicked.connect(self._on_start)
        action_row.addWidget(self.start_btn)
        self.translate_btn = self._btn("翻译字幕", kind="success")
        self.translate_btn.clicked.connect(self._on_translate)
        self.translate_btn.setEnabled(False)
        action_row.addWidget(self.translate_btn)
        self.stop_btn = self._btn("停止")
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        action_row.addWidget(self.stop_btn)
        action_row.addStretch(1)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFixedWidth(220)
        self.progress.setVisible(False)
        action_row.addWidget(self.progress)
        root.addLayout(action_row)

        preview = QHBoxLayout()
        preview.setSpacing(14)
        orig_card, self.orig_text, self.polish_src_btn = self._text_card(
            "识别结果", "AI 润色原文")
        trans_card, self.trans_text, self.polish_trans_btn = self._text_card(
            "译文", "AI 润色译文")
        self.polish_src_btn.clicked.connect(lambda: self._on_polish("source"))
        self.polish_trans_btn.clicked.connect(
            lambda: self._on_polish("translation"))
        self.polish_src_btn.setEnabled(False)
        self.polish_trans_btn.setEnabled(False)
        preview.addWidget(orig_card, 1)
        preview.addWidget(trans_card, 1)
        root.addLayout(preview, 1)

        scroll.setWidget(container)
        outer.addWidget(scroll, 1)

    def _build_file_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        row = QHBoxLayout(card)
        row.setContentsMargins(18, 14, 18, 14)
        row.setSpacing(12)

        self.select_btn = self._btn("选择音视频文件", kind="primary")
        self.select_btn.clicked.connect(self._open_file_dialog)
        row.addWidget(self.select_btn)

        self.file_label = _ElidedLabel(
            "支持 MP3 / WAV / M4A / FLAC / MP4 / MKV / MOV 等 · "
            "音频语言可选「自动检测」", self)
        self.file_label.setObjectName("caption")
        self.file_label.setStyleSheet(
            f"color: {Palette.TEXT_MUTED}; border: none; background: transparent;")
        row.addWidget(self.file_label, 1)

        src_label = QLabel("音频语言:", self)
        src_label.setObjectName("fieldLabel")
        src_label.setToolTip("语音识别的语言，同时也是翻译的源语言")
        row.addWidget(src_label)
        self.src_lang_combo = QComboBox(self)
        self.src_lang_combo.addItems(list(SUPPORTED_SOURCE_LANGS.keys()))
        self.src_lang_combo.setCurrentText("自动检测")
        # 右侧 34px 留给下拉箭头，宽度不足会把「自动检测」裁成「自动检」
        self.src_lang_combo.setMinimumWidth(130)
        self.src_lang_combo.setMaxVisibleItems(6)
        self.src_lang_combo.setToolTip(
            "「自动检测」由模型自动判断语音语言；\n"
            "指定具体语言可提高识别准确率。\n"
            "该语言同时作为字幕翻译的源语言。")
        row.addWidget(self.src_lang_combo)

        dst_label = QLabel("翻译到:", self)
        dst_label.setObjectName("fieldLabel")
        dst_label.setToolTip("字幕翻译的目标语言")
        row.addWidget(dst_label)
        self.lang_combo = QComboBox(self)
        self.lang_combo.addItems(list(SUPPORTED_TARGET_LANGS.keys()))
        self.lang_combo.setCurrentText("中文")
        self.lang_combo.setMinimumWidth(110)
        self.lang_combo.setMaxVisibleItems(6)
        self.lang_combo.setToolTip("字幕翻译的目标语言（识别后点「翻译字幕」）")
        row.addWidget(self.lang_combo)
        return card

    def _build_model_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        col = QVBoxLayout(card)
        col.setContentsMargins(18, 14, 18, 14)
        col.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("识别模型", self)
        title.setObjectName("sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.model_status = QLabel("", self)
        self.model_status.setObjectName("caption")
        head.addWidget(self.model_status)
        col.addLayout(head)

        # 智能推荐行：硬件检测在后台完成，结果回来后填入文字
        rec_row = QHBoxLayout()
        rec_row.setSpacing(8)
        # 用可省略标签：推荐理由很长时不撑宽整行、也不因换行把卡片顶高
        self.rec_label = _ElidedLabel(
            "正在检测本机配置，稍后给出模型推荐…", self)
        self.rec_label.setObjectName("recommend")
        rec_row.addWidget(self.rec_label, 1)
        self.rec_apply_btn = self._btn("选用推荐", kind="ghost", parent=card)
        self.rec_apply_btn.setVisible(False)
        self.rec_apply_btn.clicked.connect(self._on_apply_recommendation)
        rec_row.addWidget(self.rec_apply_btn)
        self.rec_detail_btn = self._btn("硬件详情", kind="ghost", parent=card)
        self.rec_detail_btn.setVisible(False)
        self.rec_detail_btn.clicked.connect(self._on_show_hardware)
        rec_row.addWidget(self.rec_detail_btn)
        col.addLayout(rec_row)

        mode_row = QHBoxLayout()
        mode_label = QLabel("识别方式:", self)
        mode_label.setObjectName("fieldLabel")
        mode_row.addWidget(mode_label)
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("本地模型（免费，需下载）", LOCAL_MODE)
        self.mode_combo.addItem("在线 API（自有密钥）", API_MODE)
        current_mode = self.cfg.get('asr_mode', LOCAL_MODE)
        self.mode_combo.setCurrentIndex(
            max(0, self.mode_combo.findData(current_mode)))
        self.mode_combo.setMinimumContentsLength(18)
        self.mode_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch(1)
        col.addLayout(mode_row)

        # 本地模型面板
        self.local_panel = QWidget(self)
        local_row = QHBoxLayout(self.local_panel)
        local_row.setContentsMargins(0, 0, 0, 0)
        local_row.setSpacing(10)
        model_label = QLabel("模型:", self)
        model_label.setObjectName("fieldLabel")
        local_row.addWidget(model_label)
        local_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.model_combo = QComboBox(self)
        self.model_combo.setMinimumWidth(210)
        # 新增「· 仅英文」等长条目，按固定字符数算宽度，避免最长项把整行撑宽
        self.model_combo.setMinimumContentsLength(20)
        self.model_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMaxVisibleItems(10)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        local_row.addWidget(self.model_combo)
        self.download_btn = self._btn("下载模型")
        self.download_btn.clicked.connect(self._on_download)
        local_row.addWidget(self.download_btn)
        self.import_btn = self._btn("导入本地模型…")
        self.import_btn.clicked.connect(self._on_import_model)
        local_row.addWidget(self.import_btn)
        self.open_models_btn = self._btn("打开模型目录", kind="ghost")
        self.open_models_btn.clicked.connect(self._open_model_dir)
        self.open_models_btn.setToolTip(
            f"模型下载位置：{asr.MODEL_ROOT}")
        local_row.addWidget(self.open_models_btn)
        local_row.addStretch(1)
        self.install_btn = self._btn("安装识别组件")
        self.install_btn.clicked.connect(self._on_install_deps)
        local_row.addWidget(self.install_btn)
        col.addWidget(self.local_panel)

        self.model_hint = QLabel("", self)
        self.model_hint.setObjectName("caption")
        self.model_hint.setWordWrap(True)
        col.addWidget(self.model_hint)

        # 在线 API 面板
        self.api_panel = QWidget(self)
        api_grid = QHBoxLayout(self.api_panel)
        api_grid.setContentsMargins(0, 0, 0, 0)
        api_grid.setSpacing(10)
        self.api_base = QLineEdit(self)
        self.api_base.setPlaceholderText("API 地址，如 https://api.openai.com/v1")
        self.api_base.setText(self.cfg.get('asr_api_base', ''))
        self.api_base.setMinimumWidth(240)
        api_grid.addWidget(self.api_base)
        self.api_key = QLineEdit(self)
        self.api_key.setPlaceholderText("API 密钥")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setText(self.cfg.get('asr_api_key', ''))
        self.api_key.setMinimumWidth(160)
        api_grid.addWidget(self.api_key)
        self.api_model = QLineEdit(self)
        self.api_model.setPlaceholderText("模型名，如 whisper-1")
        self.api_model.setText(self.cfg.get('asr_api_model', 'whisper-1'))
        self.api_model.setMinimumWidth(150)
        api_grid.addWidget(self.api_model)
        # 在线 API 配置失焦即写盘，避免重启后丢失（密钥尤其敏感）
        self.api_base.editingFinished.connect(
            lambda: self._save_cfg(asr_api_base=self.api_base.text().strip()))
        self.api_key.editingFinished.connect(
            lambda: self._save_cfg(asr_api_key=self.api_key.text().strip()))
        self.api_model.editingFinished.connect(
            lambda: self._save_cfg(asr_api_model=self.api_model.text().strip()))
        api_grid.addStretch(1)
        col.addWidget(self.api_panel)

        self.api_hint = QLabel(
            "在线 API 需为公网地址（拒绝 localhost / 内网），"
            "兼容 OpenAI /audio/transcriptions 接口", self)
        self.api_hint.setObjectName("caption")
        self.api_hint.setWordWrap(True)
        col.addWidget(self.api_hint)

        # 下载进度
        self.dl_progress = QProgressBar(self)
        self.dl_progress.setRange(0, 100)
        self.dl_progress.setValue(0)
        self.dl_progress.setTextVisible(True)
        self.dl_progress.setVisible(False)
        col.addWidget(self.dl_progress)
        self.dl_label = QLabel("", self)
        self.dl_label.setObjectName("caption")
        self.dl_label.setVisible(False)
        col.addWidget(self.dl_label)
        return card

    def _build_export_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(8)

        fmt_label = QLabel("字幕格式:", self)
        fmt_label.setObjectName("fieldLabel")
        row.addWidget(fmt_label)
        self.fmt_combo = QComboBox(self)
        for key, label in FMT_LABELS.items():
            self.fmt_combo.addItem(label, key)
        self.fmt_combo.setMinimumWidth(120)
        idx = self.fmt_combo.findData(self.cfg.get('asr_export_fmt', 'srt'))
        self.fmt_combo.setCurrentIndex(max(0, idx))
        self.fmt_combo.currentIndexChanged.connect(self._on_fmt_changed)
        row.addWidget(self.fmt_combo)

        mode_label = QLabel("导出内容:", self)
        mode_label.setObjectName("fieldLabel")
        row.addWidget(mode_label)

        segment = QFrame(self)
        segment.setObjectName("segmented")
        seg_row = QHBoxLayout(segment)
        seg_row.setContentsMargins(3, 3, 3, 3)
        seg_row.setSpacing(2)
        self.mode_buttons = {}
        for key, label in MODE_LABELS.items():
            btn = QPushButton(label, self)
            btn.setObjectName("segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c, k=key: self._on_export_mode(k))
            seg_row.addWidget(btn)
            self.mode_buttons[key] = btn
        self.mode_buttons[self._export_mode].setChecked(True)
        row.addWidget(segment)

        row.addStretch(1)

        self.export_btn = self._btn("导出字幕", kind="primary")
        self.export_btn.clicked.connect(self._on_export)
        self.export_btn.setEnabled(False)
        row.addWidget(self.export_btn)

        self.open_dir_btn = self._btn("打开输出文件夹", kind="ghost")
        self.open_dir_btn.clicked.connect(self._open_output_dir)
        self.open_dir_btn.setEnabled(False)
        row.addWidget(self.open_dir_btn)
        return card

    def _text_card(self, title_text, polish_text):
        card = QFrame(self)
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel(title_text, self)
        title.setObjectName("sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        polish_btn = self._btn(polish_text, kind="ghost", parent=card)
        polish_btn.setToolTip(
            "用 AI 润色这段话（只改表达，不增删原意）\n"
            "润色通道在「设置 → AI 润色」中选择")
        head.addWidget(polish_btn)
        lay.addLayout(head)
        edit = QTextEdit(card)
        edit.setReadOnly(True)
        edit.setMinimumHeight(110)
        # 垂直方向忽略 QTextEdit 偏大的默认 sizeHint，避免把滚动容器撑到
        # 比窗口还高；剩余高度仍由 stretch 分配给它。
        edit.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Ignored)
        lay.addWidget(edit, 1)
        return card, edit, polish_btn

    def _btn(self, text, kind=None, parent=None):
        b = QPushButton(text, parent or self)
        if kind:
            b.setProperty("kind", kind)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def set_status(self, text, color=None):
        self.status_label.setText(text)
        if color:
            self.status_dot.setStyleSheet(f"color: {color}; font-size: 14px;")

    # ── 文件 ────────────────────────────────────────────────

    def _open_file_dialog(self):
        patterns = ' '.join('*' + ext for ext in sorted(asr.AUDIO_VIDEO_EXTS))
        path, _ = QFileDialog.getOpenFileName(
            self, "选择音视频文件", "",
            f"音视频文件 ({patterns});;所有文件 (*)")
        if path:
            self._load_file(path)

    def _load_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext not in asr.AUDIO_VIDEO_EXTS:
            self.set_status(f"不支持的音视频格式: {ext}", "#e5635f")
            return
        self._file_path = path
        self._segments = []
        self._translations = []
        self._info = {}
        self.orig_text.clear()
        self.trans_text.clear()
        self.export_btn.setEnabled(False)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        self.file_label.set_full_text(
            f"{os.path.basename(path)}  |  {size_mb:.1f} MB")
        self.file_label.setStyleSheet(
            f"color: {Palette.TEXT}; border: none; background: transparent;")
        # 加载文件后刷新按钮状态（否则「开始识别」仍保持禁用）
        self._refresh_model_status()
        self.set_status("文件已加载，请确认模型后开始识别", "#4ade80")

    # ── 模型 ────────────────────────────────────────────────

    def _reload_models(self, select=None):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        last_lang = None
        for name, info in asr.MODEL_CATALOG.items():
            lang = info.get('lang', 'multi')
            if last_lang and lang != last_lang:
                self.model_combo.insertSeparator(self.model_combo.count())
            self.model_combo.addItem(asr.model_label(name), name)
            last_lang = lang
        imported = asr.imported_models()
        if imported:
            self.model_combo.insertSeparator(self.model_combo.count())
            for name in imported:
                if name not in asr.MODEL_CATALOG:
                    self.model_combo.addItem(asr.model_label(name), name)
        if select:
            idx = self.model_combo.findData(select)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
                # 分隔项没有 data，落上去时顺延到下一个真实项
                if self.model_combo.currentData() is None:
                    self.model_combo.setCurrentIndex(idx + 1)
        self.model_combo.blockSignals(False)
        self._on_model_changed()

    def _current_model(self):
        return self.model_combo.currentData()

    def _on_mode_changed(self, _index=None):
        mode = self.mode_combo.currentData()
        self.local_panel.setVisible(mode == LOCAL_MODE)
        self.api_panel.setVisible(mode == API_MODE)
        self.api_hint.setVisible(mode == API_MODE)
        self._save_cfg(asr_mode=mode)
        self._refresh_model_status()

    def _on_model_changed(self, _index=None):
        name = self._current_model()
        if not name:
            return
        self._save_cfg(asr_model=name)
        info = asr.MODEL_CATALOG.get(name)
        path, imported = asr.resolve_model_path(name)
        if imported and path:
            self.model_hint.setText(f"已导入本地模型：{path}")
        elif info:
            self.model_hint.setText(
                f"{info['desc']} · 下载大小 {info['size']}")
        else:
            self.model_hint.setText("")
        self._refresh_model_status()
        self._update_recommend_button()

    def _refresh_model_status(self):
        mode = self.mode_combo.currentData()
        if mode == API_MODE:
            self.model_status.setText("在线 API 模式")
            self.model_status.setStyleSheet(f"color: {Palette.TEXT_MUTED};")
            self.install_btn.setVisible(False)
            deps_ok = True
        else:
            deps_ok, _msg = asr.check_dependencies()
            self.install_btn.setVisible(not deps_ok)
            name = self._current_model()
            ready = bool(name) and (asr.is_model_ready(name)
                                    or asr.is_imported_model_ready(name))
            if not deps_ok:
                self.model_status.setText("未安装识别组件")
                self.model_status.setStyleSheet("color: #e5635f;")
                self.download_btn.setEnabled(False)
                self.download_btn.setText("下载模型")
            elif ready:
                imported = asr.is_imported_model_ready(name)
                self.model_status.setText("模型已就绪 ✓")
                self.model_status.setStyleSheet("color: #4ade80;")
                # 已下载/已导入的模型禁止重复下载
                self.download_btn.setEnabled(False)
                self.download_btn.setText("已导入" if imported else "已下载")
                self.download_btn.setToolTip(
                    "模型已存在，无需重复下载；可点「打开模型目录」查看")
            else:
                self.model_status.setText("模型未下载")
                self.model_status.setStyleSheet("color: #eab308;")
                self.download_btn.setEnabled(bool(name))
                self.download_btn.setText("下载模型")
                self.download_btn.setToolTip("")
        self.start_btn.setEnabled(
            self._file_path is not None and deps_ok and not self._busy)

    # ── 智能推荐 ────────────────────────────────────────────

    @staticmethod
    def _any_model_ready():
        if any(asr.is_model_ready(n) for n in asr.MODEL_CATALOG):
            return True
        return any(asr.is_imported_model_ready(n)
                   for n in asr.imported_models())

    def _probe_hardware(self):
        try:
            hw = asr.detect_hardware()
            rec = asr.recommend_model(hw)
        except Exception as e:
            hw, rec = None, None
            print(f"硬件检测失败: {e}")
        self._sig_hw_ready.emit((hw, rec))

    def _on_hw_ready(self, payload):
        hw, rec = payload
        if not rec:
            self.rec_label.set_full_text(
                "硬件检测未完成，可在下拉框中自行选择模型")
            return
        self._hw = hw
        self._recommendation = rec
        self.rec_label.set_full_text(rec['reason'], rec['detail'])
        self.rec_detail_btn.setVisible(True)
        self.rec_apply_btn.setVisible(True)
        if self._auto_select_recommend:
            self._auto_select_recommend = False
            self._reload_models(rec['name'])
        self._update_recommend_button()

    def _update_recommend_button(self):
        rec = self._recommendation
        if not rec:
            self.rec_apply_btn.setVisible(False)
            return
        self.rec_apply_btn.setVisible(True)
        if self._current_model() == rec['name']:
            self.rec_apply_btn.setText("已选用推荐")
            self.rec_apply_btn.setEnabled(False)
            self.rec_apply_btn.setToolTip(rec['reason'])
        else:
            self.rec_apply_btn.setText(f"选用 {rec['name']}")
            self.rec_apply_btn.setEnabled(True)
            self.rec_apply_btn.setToolTip(
                f"切换到推荐模型 {rec['name']}（{rec['size']}）\n"
                f"{rec['desc']}")

    def _on_apply_recommendation(self):
        rec = self._recommendation
        if not rec:
            return
        self._reload_models(rec['name'])
        if asr.is_model_ready(rec['name']):
            self.set_status(
                f"已切换到推荐模型 {rec['name']}（已就绪）", "#4ade80")
        else:
            self.set_status(
                f"已选中 {rec['name']}（{rec['size']}），"
                "点「下载模型」即可下载", "#eab308")

    def _on_show_hardware(self):
        rec = self._recommendation
        if not rec:
            return
        QMessageBox.information(
            self, "本机配置与模型推荐",
            rec['detail'] + "\n\n" + rec['reason'] +
            "\n\n说明：带 .en / distil 的型号只支持英文语音；"
            "中文或多语言内容请使用多语言型号。")

    # ── 依赖安装 / 模型下载 / 导入 ──────────────────────────

    def _on_install_deps(self):
        self.install_btn.setEnabled(False)
        self.install_btn.setText("安装中…")
        self.set_status("正在安装语音识别组件…", "#eab308")

        def run():
            try:
                ok, msg = asr.install_dependencies(
                    lambda line: self._sig_install_progress.emit(line))
            except Exception as exc:
                # 保证「安装识别组件」按钮一定能恢复，不会卡死在「安装中…」
                ok, msg = False, f"安装失败：{exc}"
            self._sig_install_done.emit(ok, msg)

        threading.Thread(target=run, daemon=True).start()

    def _on_install_progress(self, line):
        text = (line or '').strip()
        if text:
            self.set_status(f"安装中: {text[:80]}", "#eab308")

    def _on_install_done(self, ok, msg):
        self.install_btn.setEnabled(True)
        self.install_btn.setText("安装识别组件")
        if ok:
            self.set_status("识别组件安装完成", "#4ade80")
        else:
            self.set_status("安装失败（已自动尝试多个镜像）", "#e5635f")
            QMessageBox.warning(self, "安装失败", msg)
        self._refresh_model_status()

    def _on_download(self):
        name = self._current_model()
        if not name:
            return
        if asr.is_model_ready(name):
            self.set_status(f"模型 {name} 已下载，无需重复下载", "#71717a")
            return
        if asr.is_imported_model_ready(name):
            self.set_status(f"模型 {name} 为本地导入，无需下载", "#71717a")
            return
        deps_ok, _ = asr.check_dependencies()
        if not deps_ok:
            self.set_status("请先安装识别组件", "#eab308")
            return
        self.download_btn.setEnabled(False)
        self.model_combo.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.dl_progress.setVisible(True)
        self.dl_progress.setRange(0, 0)  # 未知总量时先走不确定动画
        self.dl_progress.setFormat("准备下载…")
        self.dl_label.setVisible(True)
        self.dl_label.setText(f"正在下载模型 {name} …")
        self.set_status(f"正在下载模型 {name} …", "#eab308")

        def run():
            try:
                asr.download_model(
                    name,
                    lambda done, total: self._sig_download_progress.emit(
                        done, total))
                self._sig_download_done.emit(True, name)
            except Exception as e:
                self._sig_download_done.emit(False, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_download_progress(self, done, total):
        if total > 0:
            percent = int(done * 100 / total)
            self.dl_progress.setRange(0, 100)
            self.dl_progress.setValue(percent)
            self.dl_label.setText(
                f"下载中 {percent}% ({done / 1048576:.1f} / "
                f"{total / 1048576:.1f} MB)")
        else:
            self.dl_progress.setRange(0, 0)
            self.dl_label.setText("正在下载…")

    def _on_download_done(self, ok, msg):
        self.model_combo.setEnabled(True)
        self.import_btn.setEnabled(True)
        self.dl_progress.setVisible(False)
        self.dl_label.setVisible(False)
        if ok:
            self.set_status(f"模型 {msg} 下载完成", "#4ade80")
        else:
            self.set_status(f"下载失败: {msg[:100]}", "#e5635f")
        self._refresh_model_status()

    def _on_import_model(self):
        folder = QFileDialog.getExistingDirectory(self, "选择 faster-whisper 模型目录")
        if not folder:
            return
        try:
            name = asr.import_local_model(folder)
        except Exception as e:
            self.set_status(f"导入失败: {e}", "#e5635f")
            return
        imported = dict(self.cfg.get('asr_imported_models') or {})
        imported[name] = os.path.abspath(folder)
        self.cfg['asr_imported_models'] = imported
        # 只写变化的键，避免把设置页刚保存的密钥 / 选项整份覆盖掉
        save_user_config({'asr_imported_models': imported})
        self.cfg = load_user_config()
        self._reload_models(name)
        self.set_status(f"已导入本地模型 {name}", "#4ade80")

    # ── 识别 ────────────────────────────────────────────────

    def _on_start(self):
        if not self._file_path:
            self.set_status("请先选择音视频文件", "#71717a")
            return
        mode = self.mode_combo.currentData()
        if mode == LOCAL_MODE:
            deps_ok, _ = asr.check_dependencies()
            if not deps_ok:
                self.set_status("请先安装识别组件（faster-whisper）", "#e5635f")
                return
            name = self._current_model()
            if not name or not (asr.is_model_ready(name)
                                or asr.is_imported_model_ready(name)):
                self.set_status("模型未就绪，请先下载或导入模型", "#e5635f")
                return
            model_name = name
        else:
            base = self.api_base.text().strip()
            try:
                asr.validate_api_url(base)
            except Exception as e:
                self.set_status(str(e), "#e5635f")
                return
            if not self.api_model.text().strip():
                self.set_status("请填写 API 模型名", "#e5635f")
                return
            model_name = self.api_model.text().strip()

        # 语音识别选项（设置页可调：人声检测、阈值、解码宽度、实时字幕）
        from config import get_asr_settings
        asr_opts = get_asr_settings()

        self._cancel.clear()
        self._busy = True
        self._stream_count = 0
        self._sync_buttons()
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.progress.setFormat("正在准备…")
        self.set_status("正在识别音频…", "#eab308")
        self.orig_text.clear()
        self.trans_text.clear()
        self._translations = []
        self.export_btn.setEnabled(False)
        stream_enabled = bool(asr_opts.get('asr_stream_enabled', True))

        src_text = self.src_lang_combo.currentText()
        src_code = SUPPORTED_SOURCE_LANGS.get(src_text, 'auto')
        self._src_code = src_code
        path = self._file_path
        api_base = self.api_base.text().strip()
        api_key = self.api_key.text().strip()
        api_model = self.api_model.text().strip()

        def run():
            try:
                def on_progress(percent, text):
                    self._sig_asr_progress.emit(int(percent), str(text))

                if mode == LOCAL_MODE:
                    result = asr.transcribe_local(
                        path, model_name, language=src_code,
                        progress_cb=on_progress, cancel_event=self._cancel,
                        vad_filter=bool(asr_opts.get('asr_vad_enabled', True)),
                        vad_threshold=float(
                            asr_opts.get('asr_vad_threshold', 0.5)),
                        beam_size=int(asr_opts.get('asr_beam_size', 5)),
                        segment_callback=(
                            (lambda seg: self._sig_asr_segment.emit(seg))
                            if stream_enabled else None),
                        device_preference=str(
                            asr_opts.get('asr_device', 'auto')))
                else:
                    result = asr.transcribe_api(
                        path, api_base, api_key, api_model,
                        language=src_code, progress_cb=on_progress)
                self._sig_asr_done.emit(result)
            except Exception as e:
                self._sig_asr_error.emit(str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_stop(self):
        self._cancel.set()
        self.set_status("正在停止识别…", "#eab308")

    def _on_asr_progress(self, percent, text):
        if 0 <= percent <= 100:
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
        self.progress.setFormat(f"{text} {percent}%")
        self.set_status(text, "#eab308")

    def _on_asr_segment(self, seg):
        """实时字幕：每识别出一段立即追加到预览区（设置页可关闭）。"""
        if not self._busy:
            return
        self._stream_count += 1
        line = f"[{self._fmt_ts(seg.get('start', 0))}] {seg.get('text', '')}"
        edit = self.orig_text
        from PySide6.QtGui import QTextCursor
        cur = edit.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        if edit.toPlainText():
            cur.insertText("\n")
        cur.insertText(line)
        edit.setTextCursor(cur)
        # 限制总行数，避免长时间识别把文本框撑爆
        limit = 200
        extra = edit.document().blockCount() - limit
        if extra > 0:
            cur = edit.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.Start)
            cur.movePosition(QTextCursor.MoveOperation.Down,
                             QTextCursor.MoveMode.KeepAnchor, extra)
            cur.removeSelectedText()
        edit.ensureCursorVisible()

    def _on_asr_done(self, result):
        self._busy = False
        self._sync_buttons()
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("识别完成")
        self._info = result
        self._segments = result.get('segments') or []
        self._translations = []
        self.orig_text.setPlainText(self._format_preview(self._segments))
        self.trans_text.clear()
        self.export_btn.setEnabled(bool(self._segments))
        self.translate_btn.setEnabled(bool(self._segments))
        self._sync_buttons()
        lang = asr.language_display(result.get('language'))
        suffix = ""
        if lang:
            label = ("自动检测" if self._src_code in ('auto', '')
                     else "语言")
            suffix = f"（{label}：{lang}）"
        device = result.get('device', '')
        device_tag = ""
        if device == 'cuda':
            device_tag = " · GPU 加速"
        elif device == 'cpu':
            device_tag = " · CPU"
        self.set_status(
            f"识别完成：{len(self._segments)} 条字幕{suffix}{device_tag}",
            "#4ade80")

    def _on_asr_error(self, msg):
        self._busy = False
        self._sync_buttons()
        self.progress.setVisible(False)
        self.set_status(f"识别失败: {msg[:120]}", "#e5635f")

    def _format_preview(self, segments):
        lines = []
        for seg in segments:
            lines.append(f"[{self._fmt_ts(seg['start'])}] {seg['text']}")
        return '\n'.join(lines)

    @staticmethod
    def _fmt_ts(ms):
        ms = max(0, int(ms))
        m, r = divmod(ms, 60000)
        s = r // 1000
        return f"{m:02d}:{s:02d}"

    # ── 翻译 ────────────────────────────────────────────────

    def _on_translate(self):
        if not self._segments:
            self.set_status("请先识别音频生成字幕", "#71717a")
            return
        self.set_status("正在翻译字幕…", "#eab308")
        self.translate_btn.setEnabled(False)
        self.translateRequested.emit([s['text'] for s in self._segments])

    def set_trans_progress(self, done, total):
        self.set_status(f"正在翻译字幕 ({done}/{total})…", "#eab308")

    def set_translations(self, translations):
        self.translate_btn.setEnabled(True)
        self._translations = list(translations or [])
        self._refresh_trans_preview()
        self._sync_buttons()
        if translations:
            self.set_status(f"翻译完成：{len(translations)} 条", "#4ade80")
        else:
            self.set_status("翻译结果为空", "#eab308")

    def _refresh_trans_preview(self):
        lines = []
        for i, seg in enumerate(self._segments):
            tr = self._translations[i] if i < len(self._translations) else ''
            lines.append(f"[{self._fmt_ts(seg['start'])}] {tr}")
        self.trans_text.setPlainText('\n'.join(lines))

    # ── AI 润色 ─────────────────────────────────────────────

    def _on_polish(self, kind):
        if kind == "source":
            if not self._segments:
                self.set_status("请先识别音频生成字幕", "#71717a")
                return
            lines = [s['text'] for s in self._segments]
            btn, label = self.polish_src_btn, "AI 润色原文"
        else:
            if not self._translations:
                self.set_status("请先翻译字幕，再润色译文", "#71717a")
                return
            lines = list(self._translations)
            btn, label = self.polish_trans_btn, "AI 润色译文"
        btn.setEnabled(False)
        btn.setText("润色中…")
        self.set_status("AI 润色中…", "#eab308")
        self.polishRequested.emit(kind, lines)

    def set_polished_sources(self, texts):
        if len(texts) != len(self._segments):
            self.finish_polish("source")
            self.set_status("润色结果条数不一致，已保留原文", "#e5635f")
            return
        for seg, text in zip(self._segments, texts):
            if text and text.strip():
                seg['text'] = text
        # 原文变了，旧译文不再对应，清空并提示重新翻译
        self._translations = []
        self.trans_text.clear()
        self.orig_text.setPlainText(self._format_preview(self._segments))
        self.finish_polish("source")
        self._sync_buttons()
        self.set_status(
            f"原文润色完成：{len(self._segments)} 条（译文已清空，请重新翻译）",
            "#4ade80")

    def set_polished_translations(self, texts):
        if len(texts) != len(self._translations):
            self.finish_polish("translation")
            self.set_status("润色结果条数不一致，已保留原译文", "#e5635f")
            return
        self._translations = list(texts)
        self._refresh_trans_preview()
        self.finish_polish("translation")
        self._sync_buttons()
        self.set_status(f"译文润色完成：{len(texts)} 条", "#4ade80")

    def finish_polish(self, kind=None):
        if kind in (None, "source"):
            self.polish_src_btn.setEnabled(bool(self._segments))
            self.polish_src_btn.setText("AI 润色原文")
        if kind in (None, "translation"):
            self.polish_trans_btn.setEnabled(bool(self._translations))
            self.polish_trans_btn.setText("AI 润色译文")

    def set_polish_progress(self, done, total):
        self.set_status(f"AI 润色中 ({done}/{total})…", "#eab308")

    # ── 导出 ────────────────────────────────────────────────

    def _on_fmt_changed(self, _index=None):
        self._save_cfg(asr_export_fmt=self.fmt_combo.currentData())

    def _on_export_mode(self, mode):
        self._export_mode = mode
        for key, btn in self.mode_buttons.items():
            btn.setChecked(key == mode)
        self._save_cfg(asr_export_mode=mode)

    def _on_export(self):
        if not self._segments or not self._file_path:
            self.set_status("没有可导出的字幕", "#71717a")
            return
        fmt = self.fmt_combo.currentData()
        if self._export_mode in ('both', 'target') and not self._translations:
            self.set_status("请先点「翻译字幕」再导出该模式", "#eab308")
            return
        composed = compose_entries(
            self._segments, self._translations, self._export_mode, '.' + fmt)
        out_dir = os.path.dirname(self._file_path)
        stem = os.path.splitext(os.path.basename(self._file_path))[0]
        filename = f"{stem}_字幕.{fmt}"

        self.set_status("正在导出字幕…", "#eab308")
        self.export_btn.setEnabled(False)

        def run():
            try:
                out = export_text('', composed, fmt, out_dir, stem,
                                  filename=filename)
                self._sig_export_done.emit(out)
            except Exception as e:
                self._sig_export_error.emit(str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_export_done(self, path):
        self.export_btn.setEnabled(True)
        self._last_out_dir = os.path.dirname(path)
        self.open_dir_btn.setEnabled(True)
        self.set_status(f"已导出: {os.path.basename(path)}", "#4ade80")

    def _on_export_error(self, msg):
        self.export_btn.setEnabled(True)
        self.set_status(f"导出失败: {msg[:120]}", "#e5635f")

    def _open_output_dir(self):
        d = self._last_out_dir or (
            os.path.dirname(self._file_path) if self._file_path else None)
        if d and os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _open_model_dir(self):
        """打开模型下载目录（不存在则先创建）。"""
        os.makedirs(asr.MODEL_ROOT, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(asr.MODEL_ROOT))

    # ── 杂项 ────────────────────────────────────────────────

    def _sync_buttons(self):
        self.start_btn.setEnabled(
            self._file_path is not None and not self._busy)
        self.stop_btn.setEnabled(self._busy)
        self.select_btn.setEnabled(not self._busy)
        self.translate_btn.setEnabled(
            not self._busy and bool(self._segments))
        self.polish_src_btn.setEnabled(
            not self._busy and bool(self._segments))
        self.polish_trans_btn.setEnabled(
            not self._busy and bool(self._translations))
        self.export_btn.setEnabled(
            not self._busy and bool(self._segments))

    def _save_cfg(self, **kwargs):
        # 只提交相对本地快照变化的键：整份写回会用旧快照覆盖设置页刚存的密钥
        changed = {k: v for k, v in kwargs.items()
                   if self.cfg.get(k) != v}
        if not changed:
            return
        self.cfg.update(changed)
        save_user_config(changed)
