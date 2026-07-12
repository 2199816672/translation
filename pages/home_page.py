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
from config import SUPPORTED_TARGET_LANGS, APP_VERSION


class HomePage(QWidget):
    screenshotRequested = Signal()
    fullscreenRequested = Signal()
    translateRequested = Signal(str)
    extractRequested = Signal()
    clearRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("homePage")
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
        title = QLabel("截图翻译", self)
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
        self.ocr_text.setReadOnly(True)
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

        # Bottom action bar
        action_bar = QHBoxLayout()
        action_bar.setSpacing(10)

        self.copy_src_btn = self._btn("复制原文", kind="ghost")
        self.copy_src_btn.clicked.connect(self._copy_source)
        action_bar.addWidget(self.copy_src_btn)

        self.copy_trans_btn = self._btn("复制译文", kind="primary")
        self.copy_trans_btn.clicked.connect(self._copy_translation)
        action_bar.addWidget(self.copy_trans_btn)

        action_bar.addStretch(1)

        self.clear_btn = self._btn("清空", kind="ghost")
        self.clear_btn.clicked.connect(self._on_clear)
        action_bar.addWidget(self.clear_btn)

        root.addLayout(action_bar)

    def _build_control_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        self.screenshot_btn = self._btn("区域截图", kind="primary")
        self.screenshot_btn.clicked.connect(self.screenshotRequested.emit)
        layout.addWidget(self.screenshot_btn)

        self.fullscreen_btn = self._btn("全屏截图")
        self.fullscreen_btn.clicked.connect(self.fullscreenRequested.emit)
        layout.addWidget(self.fullscreen_btn)

        layout.addSpacing(16)

        lang_label = QLabel("翻译到:", self)
        lang_label.setObjectName("fieldLabel")
        layout.addWidget(lang_label)

        self.lang_combo = QComboBox(self)
        self.lang_combo.addItems(list(SUPPORTED_TARGET_LANGS.keys()))
        self.lang_combo.setCurrentText("中文")
        self.lang_combo.setMinimumWidth(100)
        layout.addWidget(self.lang_combo)

        self.extract_btn = self._btn("提取文字")
        self.extract_btn.clicked.connect(self.extractRequested.emit)
        layout.addWidget(self.extract_btn)

        self.translate_btn = self._btn("翻译", kind="success")
        self.translate_btn.clicked.connect(self._on_translate)
        layout.addWidget(self.translate_btn)

        layout.addStretch(1)

        return card

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

    @Slot(str)
    def set_ocr_text(self, text):
        self.ocr_text_cache = text
        self.ocr_text.setPlainText(text)

    @Slot(str)
    def set_trans_text(self, text):
        self._trans_text_cache = text
        self.trans_text.setPlainText(text)

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
