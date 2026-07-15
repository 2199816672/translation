#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档翻译页：拖拽/选择文档，提取文本并翻译。"""
import os
import threading

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from theme import Palette
from config import SUPPORTED_TARGET_LANGS, SUPPORTED_SOURCE_LANGS
from doc_parser import (
    SUPPORTED_EXTENSIONS, IMAGE_EXTENSIONS,
    is_supported, get_file_type, parse_file, format_file_size,
)


class DocPage(QWidget):
    translateRequested = Signal(str)
    _sig_parse_done = Signal(str)
    _sig_parse_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("docPage")
        self._current_file = None
        self._parsed_text = ""
        self._translated_text = ""
        self.setAcceptDrops(True)
        self._sig_parse_done.connect(self._on_parse_done)
        self._sig_parse_error.connect(self._on_parse_error)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        header = QHBoxLayout()
        title = QLabel("文档翻译", self)
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

        root.addWidget(self._build_top_bar())

        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("card")
        self.drop_zone.setMinimumHeight(120)
        self.drop_zone.setCursor(Qt.CursorShape.PointingHandCursor)
        drop_layout = QVBoxLayout(self.drop_zone)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label = QLabel("将文件拖拽到这里，或点击选择文件", self.drop_zone)
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label.setObjectName("sectionTitle")
        self.drop_label.setStyleSheet(f"color: {Palette.TEXT_MUTED}; border: none; background: transparent;")
        drop_layout.addWidget(self.drop_label)
        self.file_info_label = QLabel(
            "支持格式: TXT / PDF / Word / Excel / PPT / 图片",
            self.drop_zone,
        )
        self.file_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_info_label.setObjectName("caption")
        self.file_info_label.setStyleSheet(f"color: {Palette.TEXT_DISABLED}; border: none; background: transparent;")
        drop_layout.addWidget(self.file_info_label)
        self.drop_zone.mousePressEvent = lambda _: self._open_file_dialog()
        root.addWidget(self.drop_zone)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(14)

        left_card = QFrame()
        left_card.setObjectName("card")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(8)
        left_title = QLabel("提取文本", self)
        left_title.setObjectName("sectionTitle")
        left_layout.addWidget(left_title)
        self.source_text = QTextEdit(self)
        self.source_text.setPlaceholderText("提取文本将显示在这里，你可以直接编辑...")
        self.source_text.setMinimumHeight(120)
        left_layout.addWidget(self.source_text, 1)
        content_layout.addWidget(left_card, 1)

        right_card = QFrame()
        right_card.setObjectName("card")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(8)
        right_title = QLabel("翻译结果", self)
        right_title.setObjectName("sectionTitle")
        right_layout.addWidget(right_title)
        self.trans_text = QTextEdit(self)
        self.trans_text.setReadOnly(True)
        self.trans_text.setMinimumHeight(120)
        right_layout.addWidget(self.trans_text, 1)
        content_layout.addWidget(right_card, 1)

        root.addLayout(content_layout, 1)

        action_bar = QHBoxLayout()
        action_bar.setSpacing(10)

        self.translate_btn = self._btn("翻译", kind="primary")
        self.translate_btn.clicked.connect(self._on_translate)
        action_bar.addWidget(self.translate_btn)

        self.copy_src_btn = self._btn("复制原文")
        self.copy_src_btn.clicked.connect(self._copy_source)
        action_bar.addWidget(self.copy_src_btn)

        self.copy_btn = self._btn("复制译文")
        self.copy_btn.clicked.connect(self._copy_translation)
        action_bar.addWidget(self.copy_btn)

        self.export_btn = self._btn("导出结果")
        self.export_btn.clicked.connect(self._on_export)
        action_bar.addWidget(self.export_btn)

        action_bar.addStretch(1)

        self.clear_btn = self._btn("清空", kind="ghost")
        self.clear_btn.clicked.connect(self._on_clear)
        action_bar.addWidget(self.clear_btn)

        root.addLayout(action_bar)

    def _build_top_bar(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        self.select_btn = self._btn("选择文件", kind="primary")
        self.select_btn.clicked.connect(self._open_file_dialog)
        layout.addWidget(self.select_btn)

        layout.addSpacing(8)

        src_lang_label = QLabel("从:", self)
        src_lang_label.setObjectName("fieldLabel")
        layout.addWidget(src_lang_label)

        self.src_lang_combo = QComboBox(self)
        self.src_lang_combo.addItems(list(SUPPORTED_SOURCE_LANGS.keys()))
        self.src_lang_combo.setCurrentText("自动检测")
        self.src_lang_combo.setMinimumWidth(100)
        layout.addWidget(self.src_lang_combo)

        lang_label = QLabel("翻译到:", self)
        lang_label.setObjectName("fieldLabel")
        layout.addWidget(lang_label)

        self.lang_combo = QComboBox(self)
        self.lang_combo.addItems(list(SUPPORTED_TARGET_LANGS.keys()))
        self.lang_combo.setCurrentText("中文")
        self.lang_combo.setMinimumWidth(100)
        layout.addWidget(self.lang_combo)

        layout.addStretch(1)

        return card

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

    def _open_file_dialog(self):
        filters = ["所有支持格式 (*.txt *.pdf *.docx *.pptx *.xlsx *.png *.jpg *.jpeg *.bmp *.gif)"]
        for ext, desc in SUPPORTED_EXTENSIONS.items():
            filters.append(f"{desc} (*{ext})")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择文档", "", ";;".join(filters),
        )
        if path:
            self._load_file(path)

    def _load_file(self, path):
        if not os.path.exists(path):
            self.set_status("文件不存在", "#e5635f")
            return
        if not is_supported(path):
            self.set_status(f"不支持的格式: {os.path.splitext(path)[1]}", "#e5635f")
            return

        file_type = get_file_type(path)
        file_size = format_file_size(os.path.getsize(path))
        file_name = os.path.basename(path)
        self._current_file = path

        self.file_info_label.setText(f"{file_name}  |  {file_size}  |  {file_type}")
        self.file_info_label.setStyleSheet(f"color: {Palette.TEXT_MUTED}; border: none; background: transparent;")
        self.drop_label.setText("文件已加载，点击可更换")
        self.drop_label.setStyleSheet(f"color: {Palette.TEXT}; border: none; background: transparent;")

        self.set_status("正在提取文本...", "#eab308")

        def parse():
            try:
                text = parse_file(path)
                self._parsed_text = text
                self._sig_parse_done.emit(text)
            except Exception as e:
                self._sig_parse_error.emit(str(e))

        threading.Thread(target=parse, daemon=True).start()

    def _on_parse_done(self, text):
        self.source_text.setPlainText(text)
        char_count = len(text)
        self.set_status(f"提取完成 ({char_count} 字符)", "#4ade80")

    def _on_parse_error(self, msg):
        self.set_status(f"提取失败: {msg[:60]}", "#e5635f")

    def _on_translate(self):
        text = self.source_text.toPlainText()
        if not text or not text.strip():
            self.set_status("没有可翻译的文本", "#71717a")
            return
        self.set_status("正在翻译...", "#eab308")
        self.translateRequested.emit(text)

    def set_translated_text(self, text):
        self._translated_text = text
        self.trans_text.setPlainText(text)
        if text and text.strip():
            self.set_status("翻译完成", "#4ade80")
        else:
            self.set_status("翻译结果为空", "#eab308")

    def _copy_translation(self):
        text = self.trans_text.toPlainText()
        if text:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self.set_status("已复制译文", "#4ade80")

    def _copy_source(self):
        text = self.source_text.toPlainText()
        if text:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self.set_status("已复制原文", "#4ade80")

    def _on_export(self):
        text = self.trans_text.toPlainText()
        if not text or not text.strip():
            self.set_status("没有可导出的内容", "#71717a")
            return
        default_name = ""
        if self._current_file:
            base = os.path.splitext(os.path.basename(self._current_file))[0]
            default_name = f"{base}_翻译结果.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出翻译结果", default_name, "文本文件 (*.txt)",
        )
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    if self._current_file:
                        f.write(f"源文件: {os.path.basename(self._current_file)}\n")
                        f.write("=" * 50 + "\n\n")
                    f.write("【原文】\n")
                    f.write(self.source_text.toPlainText())
                    f.write("\n\n" + "=" * 50 + "\n\n")
                    f.write("【译文】\n")
                    f.write(text)
                self.set_status(f"已导出到: {os.path.basename(path)}", "#4ade80")
            except Exception as e:
                self.set_status(f"导出失败: {e}", "#e5635f")

    def _on_clear(self):
        self.source_text.clear()
        self.trans_text.clear()
        self._current_file = None
        self._parsed_text = ""
        self._translated_text = ""
        self.drop_label.setText("将文件拖拽到这里，或点击选择文件")
        self.drop_label.setStyleSheet(f"color: {Palette.TEXT_MUTED}; border: none; background: transparent;")
        self.file_info_label.setText(
            "支持格式: TXT / PDF / Word / Excel / PPT / 图片"
        )
        self.file_info_label.setStyleSheet(f"color: {Palette.TEXT_DISABLED}; border: none; background: transparent;")
        self.set_status("已清空", "#4ade80")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and is_supported(urls[0].toLocalFile()):
                event.acceptProposedAction()
                self.drop_zone.setStyleSheet(
                    f"#card {{ border: 2px dashed {Palette.PRIMARY}; background: {Palette.SURFACE_HOVER}; }}"
                )
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_zone.setStyleSheet("")

    def dropEvent(self, event: QDropEvent):
        self.drop_zone.setStyleSheet("")
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if is_supported(path):
                self._load_file(path)
            else:
                self.set_status(f"不支持的格式: {os.path.splitext(path)[1]}", "#e5635f")
