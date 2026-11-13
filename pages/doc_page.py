#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档处理页：文档/字幕的翻译与格式转换。"""
import os
import threading

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from theme import Palette
from flow_layout import FlowLayout
from config import SUPPORTED_TARGET_LANGS, SUPPORTED_SOURCE_LANGS
from doc_parser import (
    SUPPORTED_EXTENSIONS, SUBTITLE_EXTENSIONS,
    is_supported, get_file_type, parse_file, format_file_size,
)
from format_convert import CONVERSION_MAP, EXPORT_TARGETS, export_text
from subtitle import parse_subtitle

# 转换目标格式的显示名
FMT_LABELS = {
    'pdf': 'PDF',
    'docx': 'Word (docx)',
    'png': 'PNG 图片',
    'jpg': 'JPG 图片',
    'txt': 'TXT 文本',
    'csv': 'CSV',
    'xlsx': 'Excel (xlsx)',
    'srt': 'SRT 字幕',
    'vtt': 'VTT 字幕',
    'ass': 'ASS 字幕',
    'lrc': 'LRC 歌词',
}

FMT_FILTERS = {
    'pdf': 'PDF 文件 (*.pdf)',
    'docx': 'Word 文件 (*.docx)',
    'png': 'PNG 图片 (*.png)',
    'jpg': 'JPG 图片 (*.jpg)',
    'txt': '文本文件 (*.txt)',
    'csv': 'CSV 文件 (*.csv)',
    'xlsx': 'Excel 文件 (*.xlsx)',
    'srt': 'SRT 字幕 (*.srt)',
    'vtt': 'VTT 字幕 (*.vtt)',
    'ass': 'ASS 字幕 (*.ass)',
    'lrc': 'LRC 歌词 (*.lrc)',
}

# 源格式在"支持格式"帮助里的显示名（键为无点扩展名，合并同类后缀）
_LABEL_IMAGE = '图片 (png/jpg/bmp/gif)'
_LABEL_SUBTITLE = '字幕 (srt/vtt/ass/lrc)'
SRC_LABELS = dict(FMT_LABELS)
SRC_LABELS.update({k: _LABEL_IMAGE for k in ('png', 'jpg', 'jpeg', 'bmp', 'gif')})
SRC_LABELS.update({k: _LABEL_SUBTITLE for k in ('srt', 'vtt', 'ass', 'ssa', 'lrc')})

# 帮助对话框里合并展示的同组后缀（只保留第一个作为代表行）
_GROUPED_EXTS = {'.jpg', '.jpeg', '.bmp', '.gif', '.vtt', '.ass', '.ssa', '.lrc'}


class DocPage(QWidget):
    translateRequested = Signal(str)
    polishRequested = Signal(str, str)  # (kind, text)
    clearRequested = Signal()
    _sig_parse_done = Signal(str)
    _sig_parse_error = Signal(str)
    _sig_convert_done = Signal(list)
    _sig_convert_error = Signal(str)
    _sig_export_done = Signal(str)
    _sig_export_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("docPage")
        # 普通 QWidget 需要显式开启才会绘制 QSS 的 background
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._current_file = None
        self._parsed_text = ""
        self._translated_text = ""
        self._last_trans_src = ""
        self.subtitle_entries = None
        self.translated_subtitle_entries = None
        self._pending_export_fmt = None
        self._last_out_dir = None
        self._last_outputs = []
        self._exporting = False
        self.setAcceptDrops(True)
        self._sig_parse_done.connect(self._on_parse_done)
        self._sig_parse_error.connect(self._on_parse_error)
        self._sig_convert_done.connect(self._on_convert_done)
        self._sig_convert_error.connect(self._on_convert_error)
        self._sig_export_done.connect(self._on_export_done)
        self._sig_export_error.connect(self._on_export_error)
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("文档处理", self)
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
        root.addWidget(self._build_convert_bar())

        # 常驻的"支持哪些互转"说明；加载文件后自动切换为当前文件的可用目标
        self.fmt_hint = QLabel(self._hint_default(), self)
        self.fmt_hint.setObjectName("caption")
        self.fmt_hint.setWordWrap(True)
        root.addWidget(self.fmt_hint)

        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("card")
        self.drop_zone.setMinimumHeight(96)
        self.drop_zone.setCursor(Qt.CursorShape.PointingHandCursor)
        drop_layout = QVBoxLayout(self.drop_zone)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label = QLabel("将文件拖拽到这里，或点击选择文件", self.drop_zone)
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label.setObjectName("sectionTitle")
        self.drop_label.setStyleSheet(
            f"color: {Palette.TEXT_MUTED}; border: none; background: transparent;")
        drop_layout.addWidget(self.drop_label)
        self.file_info_label = QLabel(
            "支持: TXT / PDF / Word / Excel / PPT / 图片 / 字幕(SRT·VTT·ASS·LRC)",
            self.drop_zone,
        )
        self.file_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_info_label.setObjectName("caption")
        self.file_info_label.setStyleSheet(
            f"color: {Palette.TEXT_DISABLED}; border: none; background: transparent;")
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
        self.source_text.setMinimumHeight(110)
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
        self.trans_text.setMinimumHeight(110)
        right_layout.addWidget(self.trans_text, 1)
        content_layout.addWidget(right_card, 1)

        root.addLayout(content_layout, 1)

        # 窄窗口自动换行，按钮文字完整（导航展开后不会再挤成一坨）
        action_bar = FlowLayout(spacing=10, vspacing=8)

        self.translate_btn = self._btn("翻译", kind="primary")
        self.translate_btn.clicked.connect(self._on_translate)
        action_bar.addWidget(self.translate_btn)

        self.polish_btn = self._btn("AI 润色", kind="ghost")
        self.polish_btn.setToolTip(
            "用 AI 把译文改得更通顺（只改表达，不增删原意）\n"
            "字幕会逐条润色并保留时间轴；通道在「设置 → AI 润色」选择")
        self.polish_btn.clicked.connect(self._on_polish)
        action_bar.addWidget(self.polish_btn)

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

        self.open_dir_btn = self._btn("打开输出文件夹", kind="ghost")
        self.open_dir_btn.clicked.connect(self._open_output_dir)
        self.open_dir_btn.setEnabled(False)
        action_bar.addWidget(self.open_dir_btn)

        self.clear_btn = self._btn("清空", kind="ghost")
        self.clear_btn.clicked.connect(self._on_clear)
        action_bar.addWidget(self.clear_btn)

        root.addLayout(action_bar)

    def _build_top_bar(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = FlowLayout(card, spacing=12, vspacing=10)
        layout.setContentsMargins(18, 14, 18, 14)

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
        import ui_theme
        ui_theme.align_lang_row(src_lang_label, self.src_lang_combo,
                                lang_label, self.lang_combo)

        layout.addStretch(1)
        return card

    def _build_convert_bar(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = FlowLayout(card, spacing=12, vspacing=10)
        layout.setContentsMargins(18, 14, 18, 14)

        fmt_label = QLabel("转换为:", self)
        fmt_label.setObjectName("fieldLabel")
        layout.addWidget(fmt_label)

        self.convert_combo = QComboBox(self)
        self.convert_combo.setMinimumWidth(140)
        self.convert_combo.setEnabled(False)
        layout.addWidget(self.convert_combo)

        self.convert_btn = self._btn("转换")
        self.convert_btn.clicked.connect(self._on_convert)
        self.convert_btn.setEnabled(False)
        layout.addWidget(self.convert_btn)

        self.translate_convert_btn = self._btn("翻译并转换", kind="success")
        self.translate_convert_btn.clicked.connect(self._on_translate_and_convert)
        self.translate_convert_btn.setEnabled(False)
        layout.addWidget(self.translate_convert_btn)

        layout.addStretch(1)

        self.help_btn = self._btn("支持格式", kind="ghost")
        self.help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.help_btn.clicked.connect(self._show_format_help)
        layout.addWidget(self.help_btn)
        return card

    @staticmethod
    def _hint_default():
        """未加载文件时的常驻说明：完整列出所有支持的互转。"""
        return ("支持互转：PDF → Word / PNG / JPG / TXT　·　"
                "Word (docx) → PDF / TXT　·　TXT → Word / PDF　·　"
                "图片 (png/jpg/bmp/gif) → PDF　·　Excel (xlsx) → CSV　·　"
                "CSV → Excel　·　字幕 (srt/vtt/ass/lrc) → "
                "SRT / VTT / ASS / LRC / TXT（字幕互转与翻译均保留时间轴）")

    @staticmethod
    def _format_help_html():
        """生成"支持格式"帮助的完整矩阵 HTML（由转换矩阵动态生成）。"""
        export_names = "、".join(
            FMT_LABELS.get(t, t.upper()) for t in EXPORT_TARGETS)
        html = ['<table cellspacing="0" cellpadding="7">']
        for ext, targets in CONVERSION_MAP.items():
            if ext in _GROUPED_EXTS:
                continue
            key = ext.lstrip('.').lower()
            src = SRC_LABELS.get(key, key.upper())
            dst = "、".join(FMT_LABELS.get(t, t.upper()) for t in targets)
            html.append(
                f'<tr><td style="color:{Palette.TEXT_STRONG}; '
                f'white-space:nowrap;">{src}</td>'
                f'<td style="color:{Palette.ACCENT}; padding:0 10px;">→</td>'
                f'<td style="color:{Palette.TEXT};">{dst}</td></tr>')
        html.append('</table>')
        html.append(
            f'<p style="color:{Palette.TEXT_MUTED}; margin-top:10px; '
            f'line-height:1.6;">'
            f'译文可导出为：{export_names}<br>'
            '· Word → PDF 需本机安装 Microsoft Word 或 LibreOffice<br>'
            '· PDF → Word 由 pdf2docx 完成，扫描版（图片型）PDF 不支持<br>'
            '· Excel → CSV 仅导出第一个工作表<br>'
            '· PDF → 图片/文本 支持多页；字幕互转与翻译均保留时间轴'
            '</p>')
        return "".join(html)

    def _show_format_help(self):
        """弹出完整的格式互转矩阵（由 CONVERSION_MAP 动态生成，不会过时）。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("支持的格式互转")
        dlg.setMinimumWidth(520)
        dlg.setStyleSheet(
            f"QDialog {{ background: {Palette.SURFACE}; }}"
            f"QLabel {{ color: {Palette.TEXT}; }}")

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)

        title = QLabel("支持的格式互转", dlg)
        title.setObjectName("pageTitle")
        lay.addWidget(title)

        table = QLabel(self._format_help_html(), dlg)
        table.setTextFormat(Qt.TextFormat.RichText)
        table.setWordWrap(True)
        lay.addWidget(table)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok_btn = self._btn("知道了", kind="primary", parent=dlg)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)
        dlg.exec()

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

    # ── 文件加载 ────────────────────────────────────────────

    def _open_file_dialog(self):
        filters = ["所有支持格式 (*.txt *.pdf *.docx *.pptx *.xlsx "
                   "*.png *.jpg *.jpeg *.bmp *.gif *.srt *.vtt *.ass *.ssa)"]
        for ext, desc in SUPPORTED_EXTENSIONS.items():
            filters.append(f"{desc} (*{ext})")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择文档或字幕", "", ";;".join(filters))
        if path:
            self._load_file(path)

    def _load_file(self, path):
        if not os.path.exists(path):
            self.set_status("文件不存在", "#e5635f")
            return
        if not is_supported(path):
            self.set_status(f"不支持的格式: {os.path.splitext(path)[1]}", "#e5635f")
            return

        ext = os.path.splitext(path)[1].lower()
        file_type = get_file_type(path)
        file_size = format_file_size(os.path.getsize(path))
        file_name = os.path.basename(path)
        self._current_file = path
        self.subtitle_entries = None
        self.translated_subtitle_entries = None
        self._translated_text = ""
        self._last_trans_src = ""
        self.trans_text.clear()

        self.file_info_label.setText(f"{file_name}  |  {file_size}  |  {file_type}")
        self.file_info_label.setStyleSheet(
            f"color: {Palette.TEXT_MUTED}; border: none; background: transparent;")
        self.drop_label.setText("文件已加载，点击可更换")
        self.drop_label.setStyleSheet(
            f"color: {Palette.TEXT}; border: none; background: transparent;")

        self._update_convert_targets(ext)
        self.set_status("正在提取文本...", "#eab308")

        is_subtitle = ext in SUBTITLE_EXTENSIONS

        def parse():
            try:
                text = parse_file(path)
                entries = None
                if is_subtitle:
                    entries = parse_subtitle(path)
                    self.subtitle_entries = entries
                self._sig_parse_done.emit(text)
            except Exception as e:
                self._sig_parse_error.emit(str(e))

        threading.Thread(target=parse, daemon=True).start()

    def _update_convert_targets(self, ext):
        fmts = CONVERSION_MAP.get(ext, [])
        self.convert_combo.clear()
        for f in fmts:
            self.convert_combo.addItem(FMT_LABELS.get(f, f.upper()), f)
        has = bool(fmts)
        self.convert_combo.setEnabled(has)
        self.convert_btn.setEnabled(has)
        self.translate_convert_btn.setEnabled(has)

        # 常驻说明切换为"当前文件可转换为…"
        if fmts:
            key = ext.lstrip('.').lower()
            src_name = SRC_LABELS.get(key, FMT_LABELS.get(key, key.upper()))
            names = " / ".join(FMT_LABELS.get(f, f.upper()) for f in fmts)
            self.fmt_hint.setText(
                f"当前文件 {src_name} 可转换为：{names}　—　"
                "点「转换」直接转原文件；点「翻译并转换」先翻译再导出译文")
        else:
            self.fmt_hint.setText("该文件格式暂不支持转换，仍可翻译或提取文本")

    def _on_parse_done(self, text):
        self._parsed_text = text
        self.source_text.setPlainText(text)
        count = len(text)
        if self.subtitle_entries is not None:
            self.set_status(
                f"提取完成（{len(self.subtitle_entries)} 条字幕，{count} 字符）",
                "#4ade80")
        else:
            self.set_status(f"提取完成 ({count} 字符)", "#4ade80")

    def _on_parse_error(self, msg):
        self.set_status(f"提取失败: {msg[:80]}", "#e5635f")

    # ── 格式转换 ────────────────────────────────────────────

    def _on_convert(self):
        if not self._current_file or self.convert_combo.currentData() is None:
            return
        fmt = self.convert_combo.currentData()
        out_dir = os.path.dirname(self._current_file)
        self._set_busy(True)
        self.set_status(f"正在转换，请稍候...", "#eab308")

        def run():
            try:
                from format_convert import convert_file
                outs = convert_file(self._current_file, fmt, out_dir)
                self._sig_convert_done.emit(outs)
            except Exception as e:
                self._sig_convert_error.emit(str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_convert_done(self, outs):
        self._set_busy(False)
        self._last_outputs = outs
        if outs:
            self._last_out_dir = os.path.dirname(outs[0])
            self.open_dir_btn.setEnabled(True)
            names = "、".join(os.path.basename(p) for p in outs[:3])
            more = f" 等 {len(outs)} 个文件" if len(outs) > 3 else ""
            self.set_status(f"转换完成: {names}{more}", "#4ade80")
        else:
            self.set_status("转换完成但未生成文件", "#eab308")

    def _on_convert_error(self, msg):
        self._set_busy(False)
        self.set_status(f"转换失败: {msg[:120]}", "#e5635f")

    # ── 翻译 ────────────────────────────────────────────────

    def _on_translate(self):
        self._pending_export_fmt = None
        self._start_translation()

    def _on_translate_and_convert(self):
        fmt = self.convert_combo.currentData()
        if not fmt:
            return
        if fmt not in EXPORT_TARGETS:
            self.set_status(
                f"{FMT_LABELS.get(fmt, fmt)} 不支持导出译文，"
                "请选择 TXT / Word / PDF / 字幕格式", "#eab308")
            return
        text = self.source_text.toPlainText()
        if not text or not text.strip():
            self.set_status("没有可翻译的文本", "#71717a")
            return
        self._pending_export_fmt = fmt
        if (self.trans_text.toPlainText().strip()
                and self._last_trans_src == text):
            # 已有当前文本的译文，直接导出
            self._do_export(fmt)
            return
        self._start_translation()

    def _start_translation(self):
        text = self.source_text.toPlainText()
        if not text or not text.strip():
            self.set_status("没有可翻译的文本", "#71717a")
            return
        self._last_trans_src = text
        self.set_status("正在翻译...", "#eab308")
        self.translateRequested.emit(text)

    def set_sub_progress(self, done, total):
        self.set_status(f"正在逐条翻译字幕 ({done}/{total})...", "#eab308")

    def _on_polish(self):
        text = self.trans_text.toPlainText().strip()
        if not text:
            self.set_status("没有可润色的译文，请先翻译", "#71717a")
            return
        self.polish_btn.setEnabled(False)
        self.polish_btn.setText("润色中…")
        self.set_status("AI 润色中…", "#eab308")
        self.polishRequested.emit("translation", text)

    def set_polished_text(self, text):
        self._translated_text = text
        self.trans_text.setPlainText(text)
        self.polish_btn.setEnabled(True)
        self.polish_btn.setText("AI 润色")
        self.set_status("AI 润色完成", "#4ade80")
        self._maybe_export()

    def finish_polish(self):
        self.polish_btn.setEnabled(True)
        self.polish_btn.setText("AI 润色")

    def set_translated_text(self, text):
        self._translated_text = text
        self.trans_text.setPlainText(text)
        if text and text.strip():
            self.set_status("翻译完成", "#4ade80")
        else:
            self.set_status("翻译结果为空", "#eab308")
        self._maybe_export()

    def set_translated_subtitle(self, entries):
        self.translated_subtitle_entries = entries
        text = "\n".join(e['text'].replace('\n', ' ') for e in entries)
        self._translated_text = text
        self.trans_text.setPlainText(text)
        self.polish_btn.setEnabled(True)
        self.polish_btn.setText("AI 润色")
        self.set_status(f"字幕翻译完成（{len(entries)} 条，已保留时间轴）", "#4ade80")
        self._maybe_export()

    def _maybe_export(self):
        fmt = self._pending_export_fmt
        if not fmt:
            return
        self._pending_export_fmt = None
        self._do_export(fmt)

    # ── 导出译文 ────────────────────────────────────────────

    def _current_export_fmt(self):
        fmt = self.convert_combo.currentData()
        if fmt in EXPORT_TARGETS:
            return fmt
        if self.subtitle_entries is not None:
            return 'srt'
        return 'txt'

    def _export_entries(self):
        if self.subtitle_entries is not None:
            return self.translated_subtitle_entries
        return None

    def _do_export(self, fmt, filename=None):
        if not self._current_file:
            return
        # 连点导出会起多个后台线程写同一目录，最后一条状态还会盖掉失败提示
        if self._exporting:
            self.set_status("已有导出任务在进行，请稍候", "#eab308")
            return
        self._exporting = True
        self.export_btn.setEnabled(False)
        out_dir = (os.path.dirname(self._current_file)
                   if filename is None else os.path.dirname(filename))
        stem = os.path.splitext(os.path.basename(self._current_file))[0]
        text = self.trans_text.toPlainText()
        entries = self._export_entries()

        self.set_status("正在导出译文...", "#eab308")

        def run():
            try:
                out = export_text(text, entries, fmt, out_dir, stem,
                                  filename=filename,
                                  overwrite=filename is not None)
                self._sig_export_done.emit(out)
            except Exception as e:
                self._sig_export_error.emit(str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_export_done(self, path):
        self._exporting = False
        self.export_btn.setEnabled(True)
        self._last_out_dir = os.path.dirname(path)
        self.open_dir_btn.setEnabled(True)
        self.set_status(
            f"已导出: {path}（可点「打开输出文件夹」查看）", "#4ade80")

    def _on_export_error(self, msg):
        self._exporting = False
        self.export_btn.setEnabled(True)
        self.set_status(f"导出失败: {msg[:120]}", "#e5635f")

    def _on_export(self):
        if not self.trans_text.toPlainText().strip():
            self.set_status("没有可导出的译文", "#71717a")
            return
        fmt = self._current_export_fmt()
        default_name = ""
        if self._current_file:
            base = os.path.splitext(os.path.basename(self._current_file))[0]
            default_name = f"{base}_译文.{fmt}"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出译文", default_name,
            FMT_FILTERS.get(fmt, "文本文件 (*.txt)"))
        if path:
            self._do_export(fmt, filename=path)

    # ── 其他操作 ────────────────────────────────────────────

    def _set_busy(self, busy):
        has_targets = self.convert_combo.count() > 0
        self.select_btn.setEnabled(not busy)
        self.translate_btn.setEnabled(not busy)
        self.export_btn.setEnabled(not busy)
        self.convert_btn.setEnabled(not busy and has_targets)
        self.translate_convert_btn.setEnabled(not busy and has_targets)

    def _open_output_dir(self):
        d = self._last_out_dir
        if not d and self._current_file:
            d = os.path.dirname(self._current_file)
        if d and os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

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

    def _on_clear(self):
        self.source_text.clear()
        self.trans_text.clear()
        self._current_file = None
        self._parsed_text = ""
        self._translated_text = ""
        self._last_trans_src = ""
        self.subtitle_entries = None
        self.translated_subtitle_entries = None
        self._pending_export_fmt = None
        self._last_outputs = []
        self.convert_combo.clear()
        self.convert_combo.setEnabled(False)
        self.convert_btn.setEnabled(False)
        self.translate_convert_btn.setEnabled(False)
        self.open_dir_btn.setEnabled(False)
        self.drop_label.setText("将文件拖拽到这里，或点击选择文件")
        self.drop_label.setStyleSheet(
            f"color: {Palette.TEXT_MUTED}; border: none; background: transparent;")
        self.file_info_label.setText(
            "支持: TXT / PDF / Word / Excel / PPT / 图片 / 字幕(SRT·VTT·ASS·LRC)")
        self.file_info_label.setStyleSheet(
            f"color: {Palette.TEXT_DISABLED}; border: none; background: transparent;")
        self.fmt_hint.setText(self._hint_default())
        self.set_status("已清空", "#4ade80")
        self.clearRequested.emit()

    # ── 拖拽 ────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and is_supported(urls[0].toLocalFile()):
                event.acceptProposedAction()
                self.drop_zone.setStyleSheet(
                    f"#card {{ border: 2px dashed {Palette.PRIMARY}; "
                    f"background: {Palette.SURFACE_HOVER}; }}"
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
                self.set_status(
                    f"不支持的格式: {os.path.splitext(path)[1]}", "#e5635f")
