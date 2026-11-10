#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主页：截图控制、图片预览、OCR 文本、翻译结果。"""
import os
import threading

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from theme import Palette
from flow_layout import FlowLayout
from config import (SUPPORTED_TARGET_LANGS, SUPPORTED_SOURCE_LANGS,
                    APP_VERSION, SUBTITLE_AUDIO_SOURCES,
                    get_subtitle_settings, save_user_config)


class HomePage(QWidget):
    screenshotRequested = Signal()
    fullscreenRequested = Signal()
    liveTranslateRequested = Signal()
    subtitleRequested = Signal()
    translateRequested = Signal(str)
    polishRequested = Signal(str, str)  # (kind, text)
    extractRequested = Signal()
    clearRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("homePage")
        # 普通 QWidget 需要显式开启才会绘制 QSS 的 background
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._current_image_path = None
        self._ocr_text_cache = ""
        self._trans_text_cache = ""
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        # Header
        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("翻译", self)
        title.setObjectName("pageTitle")
        header.addWidget(title)
        ver = QLabel(f"v{APP_VERSION}", self)
        ver.setObjectName("caption")
        ver.setStyleSheet(f"color: {Palette.TEXT_MUTED}; font-size: 12px; border: none; background: transparent;")
        header.addWidget(ver)
        header.addStretch(1)
        self.status_dot = QLabel("●", self)
        self.status_dot.setStyleSheet(f"color: {Palette.STOPPED}; font-size: 14px;")
        self.status_label = QLabel("就绪", self)
        self.status_label.setObjectName("fieldLabel")
        header.addWidget(self.status_dot)
        header.addWidget(self.status_label)
        root.addLayout(header)

        # Control card
        root.addWidget(self._build_control_card())

        # Content area: image preview + text areas
        content_layout = QHBoxLayout()
        content_layout.setSpacing(14)

        # Left: image preview
        left_card = QFrame()
        left_card.setObjectName("card")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(8)
        left_title = QLabel("截图预览", self)
        left_title.setObjectName("sectionTitle")
        left_layout.addWidget(left_title)
        self.image_label = QLabel("等待截图...", self)
        self.image_label.setObjectName("imagePreview")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(320, 240)
        left_layout.addWidget(self.image_label, 1)
        content_layout.addWidget(left_card, 1)

        # Right: OCR + Translation text
        right_card = QFrame()
        right_card.setObjectName("card")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(8)

        ocr_title = QLabel("识别文本", self)
        ocr_title.setObjectName("sectionTitle")
        right_layout.addWidget(ocr_title)

        self.ocr_text = QTextEdit(self)
        self.ocr_text.setPlaceholderText("识别文本将显示在这里，你可以直接编辑...")
        self.ocr_text.setMinimumHeight(100)
        right_layout.addWidget(self.ocr_text, 1)

        trans_title = QLabel("翻译结果", self)
        trans_title.setObjectName("sectionTitle")
        right_layout.addWidget(trans_title)

        self.trans_text = QTextEdit(self)
        self.trans_text.setReadOnly(True)
        self.trans_text.setMinimumHeight(100)
        right_layout.addWidget(self.trans_text, 1)

        content_layout.addWidget(right_card, 1)
        root.addLayout(content_layout, 1)

        # Bottom action bar（窄窗口自动换行，按钮文字不会被裁）
        action_bar = FlowLayout(spacing=10, vspacing=8)

        self.copy_src_btn = self._btn("复制原文", kind="ghost")
        self.copy_src_btn.clicked.connect(self._copy_source)
        action_bar.addWidget(self.copy_src_btn)

        self.copy_trans_btn = self._btn("复制译文", kind="primary")
        self.copy_trans_btn.clicked.connect(self._copy_translation)
        action_bar.addWidget(self.copy_trans_btn)

        self.polish_btn = self._btn("AI 润色", kind="ghost")
        self.polish_btn.setToolTip(
            "用 AI 把译文改得更通顺（只改表达，不增删原意）\n"
            "润色通道在「设置 → AI 润色」中选择")
        self.polish_btn.clicked.connect(self._on_polish)
        action_bar.addWidget(self.polish_btn)

        source_label = QLabel("字幕音源:", self)
        source_label.setObjectName("fieldLabel")
        action_bar.addWidget(source_label)

        self.subtitle_source_combo = QComboBox(self)
        for label, key in SUBTITLE_AUDIO_SOURCES.items():
            self.subtitle_source_combo.addItem(label, key)
        current = str(get_subtitle_settings().get(
            'subtitle_audio_source') or 'mic')
        idx = self.subtitle_source_combo.findData(current)
        self.subtitle_source_combo.setCurrentIndex(max(0, idx))
        self.subtitle_source_combo.setMinimumWidth(104)
        self.subtitle_source_combo.setMaxVisibleItems(4)
        self.subtitle_source_combo.setToolTip(
            "麦克风：听你说话；电脑内部声音：听视频 / 音乐 / 会议声音\n"
            "内部声音需要 WASAPI 环回组件，设置页可一键安装")
        self.subtitle_source_combo.currentIndexChanged.connect(
            self._on_subtitle_source_changed)
        action_bar.addWidget(self.subtitle_source_combo)

        action_bar.addStretch(1)

        self.clear_btn = self._btn("清空", kind="ghost")
        self.clear_btn.clicked.connect(self._on_clear)
        action_bar.addWidget(self.clear_btn)

        root.addLayout(action_bar)

    def _build_control_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        # 单行 HBox 在导航展开后会把 10 个控件一起压扁（「域截 / 时翻」），
        # 换成流式布局：放不下就整行换行，按钮永远显示完整文字。
        layout = FlowLayout(card, spacing=8, vspacing=10)
        layout.setContentsMargins(18, 14, 18, 14)

        self.screenshot_btn = self._btn("区域截图", kind="primary")
        self.screenshot_btn.clicked.connect(self.screenshotRequested.emit)
        layout.addWidget(self.screenshot_btn)

        self.fullscreen_btn = self._btn("全屏截图")
        self.fullscreen_btn.clicked.connect(self.fullscreenRequested.emit)
        layout.addWidget(self.fullscreen_btn)

        self.live_btn = self._btn("实时翻译", kind="ghost")
        self.live_btn.setToolTip(
            "框选一块屏幕区域后持续自动识别并翻译，译文直接叠加在选区上\n"
            "适合看视频 / 直播 / 游戏；拖选区边缘缩放、拖控制条移动\n"
            "使用当前「翻译到」语言，不做 AI 润色以保证速度")
        self.live_btn.clicked.connect(self.liveTranslateRequested.emit)
        layout.addWidget(self.live_btn)

        self.subtitle_btn = self._btn("实时字幕", kind="ghost")
        self.subtitle_btn.setToolTip(
            "实时听写麦克风或电脑内部声音，直接在默认区域显示字幕框\n"
            "字幕框可拖动 / 四角缩放，位置会被记住；关掉字幕框后仍继续识别，\n"
            "结果同步显示在这页的「识别文本」和「翻译结果」里\n"
            "再次点这个按钮（运行中会变成「停止实时字幕」）即可彻底停止")
        self.subtitle_btn.clicked.connect(self.subtitleRequested.emit)
        layout.addWidget(self.subtitle_btn)

        layout.addSpacing(16)

        src_lang_label = QLabel("从:", self)
        src_lang_label.setObjectName("fieldLabel")
        layout.addWidget(src_lang_label)

        self.src_lang_combo = QComboBox(self)
        self.src_lang_combo.addItems(list(SUPPORTED_SOURCE_LANGS.keys()))
        self.src_lang_combo.setCurrentText("自动检测")
        self.src_lang_combo.setMinimumWidth(100)
        self.src_lang_combo.setMaxVisibleItems(6)  # 最多显示 6 项，超出滚动
        layout.addWidget(self.src_lang_combo)

        lang_label = QLabel("翻译到:", self)
        lang_label.setObjectName("fieldLabel")
        layout.addWidget(lang_label)

        self.lang_combo = QComboBox(self)
        self.lang_combo.addItems(list(SUPPORTED_TARGET_LANGS.keys()))
        self.lang_combo.setCurrentText("中文")
        self.lang_combo.setMinimumWidth(100)
        self.lang_combo.setMaxVisibleItems(6)  # 最多显示 6 项，超出滚动
        layout.addWidget(self.lang_combo)

        self.extract_btn = self._btn("提取文字")
        self.extract_btn.clicked.connect(self.extractRequested.emit)
        layout.addWidget(self.extract_btn)

        self.translate_btn = self._btn("翻译", kind="success")
        self.translate_btn.clicked.connect(self._on_translate)
        layout.addWidget(self.translate_btn)

        layout.addStretch(1)

        return card

    # ── 实时字幕快捷入口 ────────────────────────────────────

    def subtitle_audio_source(self):
        return str(self.subtitle_source_combo.currentData() or 'mic')

    def set_subtitle_running(self, running):
        """实时字幕运行中：主页按钮变成「停止」，状态一眼可见。"""
        self.subtitle_btn.setText("停止实时字幕" if running else "实时字幕")
        self.subtitle_btn.setProperty(
            "kind", "danger" if running else "ghost")
        # 改了动态属性要重新抛光样式
        self.subtitle_btn.style().unpolish(self.subtitle_btn)
        self.subtitle_btn.style().polish(self.subtitle_btn)

    # ── 实时字幕：同步显示到本页两个文本框 ──────────────────

    @staticmethod
    def _append_line(edit, text, limit=200):
        """追加一行并限制总行数，避免长时间听写把文本框撑爆。"""
        from PySide6.QtGui import QTextCursor
        text = str(text or '').strip()
        if not text:
            return
        cur = edit.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        if edit.toPlainText():
            cur.insertText("\n")
        cur.insertText(text)
        edit.setTextCursor(cur)
        doc = edit.document()
        extra = doc.blockCount() - limit
        if extra > 0:
            cur = edit.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.Start)
            cur.movePosition(QTextCursor.MoveOperation.Down,
                             QTextCursor.MoveMode.KeepAnchor, extra)
            cur.removeSelectedText()
        edit.ensureCursorVisible()

    def append_caption_source(self, text):
        self._append_line(self.ocr_text, text)

    def append_caption_translation(self, text):
        self._append_line(self.trans_text, text)

    def clear_caption_text(self):
        """新会话 / 清空字幕：只清字幕正文，不动其它控件。"""
        self.ocr_text.clear()
        self.trans_text.clear()

    def set_subtitle_audio_source(self, value):
        """设置页保存后同步这里，避免两处状态打架（不发信号、不重复写盘）。"""
        idx = self.subtitle_source_combo.findData(value)
        if idx < 0 or idx == self.subtitle_source_combo.currentIndex():
            return
        self.subtitle_source_combo.blockSignals(True)
        self.subtitle_source_combo.setCurrentIndex(idx)
        self.subtitle_source_combo.blockSignals(False)

    def _on_subtitle_source_changed(self):
        # 只写这一个键，绝不整份覆盖（会抹掉别的页面刚保存的密钥）
        save_user_config(
            {'subtitle_audio_source': self.subtitle_audio_source()})

    def _btn(self, text, kind=None, parent=None):
        b = QPushButton(text, parent or self)
        if kind:
            b.setProperty("kind", kind)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    @Slot(str)
    def set_status(self, text, color=None):
        self.status_label.setText(text)
        if color:
            self.status_dot.setStyleSheet(f"color: {color}; font-size: 14px;")

    @Slot(str)
    def set_image(self, image_path):
        if not image_path or not os.path.exists(image_path):
            return
        self._current_image_path = image_path
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            return
        preview_size = self.image_label.size()
        scaled = pixmap.scaled(
            preview_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口缩放后按比例重算预览图，避免停留在旧尺寸
        path = getattr(self, "_current_image_path", None)
        if path and os.path.exists(path):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.set_image(path))

    @Slot(str)
    def set_ocr_text(self, text):
        self._ocr_text_cache = text
        self.ocr_text.setPlainText(text)

    @Slot(str)
    def set_trans_text(self, text):
        self._trans_text_cache = text
        self.trans_text.setPlainText(text)
        self.polish_btn.setEnabled(True)
        self.polish_btn.setText("AI 润色")

    def _on_polish(self):
        text = self.trans_text.toPlainText().strip()
        if not text:
            self.set_status("没有可润色的译文", "#71717a")
            return
        self.polish_btn.setEnabled(False)
        self.polish_btn.setText("润色中…")
        self.set_status("AI 润色中…", "#eab308")
        self.polishRequested.emit("translation", text)

    @Slot(str)
    def set_polished_text(self, text):
        self._trans_text_cache = text
        self.trans_text.setPlainText(text)
        self.polish_btn.setEnabled(True)
        self.polish_btn.setText("AI 润色")
        self.set_status("AI 润色完成", "#4ade80")

    def finish_polish(self):
        self.polish_btn.setEnabled(True)
        self.polish_btn.setText("AI 润色")

    def _on_translate(self):
        text = self.ocr_text.toPlainText()
        if not text or not text.strip():
            self.set_status("没有可翻译的文本", "#71717a")
            return
        self.translateRequested.emit(text)

    @Slot()
    def _copy_source(self):
        text = self.ocr_text.toPlainText()
        if text:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self.set_status("已复制原文")

    @Slot()
    def _copy_translation(self):
        text = self.trans_text.toPlainText()
        if text:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self.set_status("已复制译文")

    @Slot()
    def _on_clear(self):
        self.ocr_text.clear()
        self.trans_text.clear()
        self.image_label.clear()
        self.image_label.setText("等待截图...")
        self._current_image_path = None
        self.set_status("已清空")
        self.clearRequested.emit()
