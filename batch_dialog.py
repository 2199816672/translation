#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量转录对话框：多选音视频 → 依次识别 → 每个文件单独输出字幕。

默认输出到各源文件所在目录；也可以指定一个统一文件夹。
"""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt, Signal, QUrl, Slot
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QProgressBar, QPushButton, QRadioButton, QSizePolicy, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from theme import Palette
from config import load_user_config, save_user_config
from batch_transcribe import (
    BatchRunner, Job, can_write_dir, classify_paths, file_pattern,
    is_supported, needs_translation, output_dir_for,
    DONE, FAILED, PENDING, RUNNING, SKIPPED, STATUS_TEXT,
)

FMT_LABELS = {
    'srt': 'SRT 字幕', 'vtt': 'VTT 字幕',
    'ass': 'ASS 字幕', 'lrc': 'LRC 歌词',
}
# 三种导出内容始终由用户决定，勾选「同时翻译」不会覆盖这个选择。
MODE_LABELS = {
    'source': '只导出原文',
    'target': '只导出译文',
    'both': '原文 + 译文',
}

OK_COLOR = '#4ade80'
WARN_COLOR = '#eab308'
ERR_COLOR = '#e5635f'
MUTED_COLOR = '#71717a'


class BatchTranscribeDialog(QDialog):
    """独立的批量任务窗口；后台线程只发信号，UI 全部在主线程更新。"""

    _sig_started = Signal(int, int, str)
    _sig_progress = Signal(int, int, str, int, str)
    _sig_finished = Signal(int, str, str, str)
    _sig_all_done = Signal(str)

    def __init__(self, settings, parent=None, initial_paths=None):
        super().__init__(parent)
        self.setObjectName('batchDialog')
        self.setWindowTitle('批量转录')
        self.resize(880, 680)
        self.setMinimumSize(720, 560)
        self.setAcceptDrops(True)

        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(Palette.BG))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        self.settings = dict(settings)
        self.jobs = []
        self.out_dir = None
        self._running = False
        self._stopping = False
        self._retry_mode = False
        self._cancel = threading.Event()
        self._thread = None
        self._last_summary = ''
        cfg = load_user_config()
        self._last_dir = (self.settings.get('last_dir')
                          or cfg.get('asr_last_dir') or '')
        self._last_out_dir = cfg.get('batch_last_out_dir') or ''

        self._sig_started.connect(self._on_started)
        self._sig_progress.connect(self._on_progress)
        self._sig_finished.connect(self._on_finished)
        self._sig_all_done.connect(self._on_all_done)

        self._build_ui()
        if initial_paths:
            self.add_paths(initial_paths, quiet=True)

    # ── UI ──────────────────────────────────────────────────

    def _btn(self, text, kind=None):
        b = QPushButton(text, self)
        if kind:
            b.setProperty('kind', kind)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _card(self):
        f = QFrame(self)
        f.setObjectName('card')
        return f

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(10)

        title = QLabel('批量转录', self)
        title.setObjectName('pageTitle')
        root.addWidget(title)
        hint = QLabel(
            '一次选多个音视频文件，按顺序逐个识别；每个文件单独生成一份字幕，'
            '单个文件失败不会影响后面的文件。', self)
        hint.setObjectName('caption')
        hint.setWordWrap(True)
        root.addWidget(hint)

        # 文件列表
        file_card = self._card()
        fv = QVBoxLayout(file_card)
        fv.setContentsMargins(14, 12, 14, 12)
        fv.setSpacing(8)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.add_files_btn = self._btn('添加文件', 'primary')
        self.add_files_btn.clicked.connect(self._on_add_files)
        self.add_folder_btn = self._btn('添加文件夹')
        self.add_folder_btn.clicked.connect(self._on_add_folder)
        self.remove_btn = self._btn('移除选中')
        self.remove_btn.clicked.connect(self._on_remove)
        self.up_btn = self._btn('上移')
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn = self._btn('下移')
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.clear_btn = self._btn('清空')
        self.clear_btn.clicked.connect(self._on_clear)
        for b in (self.add_files_btn, self.add_folder_btn, self.remove_btn,
                  self.up_btn, self.down_btn, self.clear_btn):
            toolbar.addWidget(b)
        toolbar.addStretch(1)
        self.count_label = QLabel('共 0 个文件', self)
        self.count_label.setObjectName('caption')
        toolbar.addWidget(self.count_label)
        fv.addLayout(toolbar)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(['文件', '大小', '状态'])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setMinimumHeight(190)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setStyleSheet(f'''
            QTableWidget {{ background: {Palette.SURFACE}; color: {Palette.TEXT};
                border: 1px solid {Palette.BORDER}; border-radius: 8px;
                gridline-color: {Palette.BORDER_SOFT}; }}
            QTableWidget::item {{ padding: 5px 6px; }}
            QTableWidget::item:selected {{ background: {Palette.SURFACE_HOVER};
                color: {Palette.TEXT}; }}
            QHeaderView::section {{ background: {Palette.SURFACE};
                color: {Palette.TEXT_MUTED}; border: none;
                border-bottom: 1px solid {Palette.BORDER_SOFT}; padding: 6px; }}
        ''')
        fv.addWidget(self.table, 1)
        root.addWidget(file_card, 1)

        # 输出位置
        out_card = self._card()
        ov = QVBoxLayout(out_card)
        ov.setContentsMargins(14, 12, 14, 12)
        ov.setSpacing(8)
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        self.follow_radio = QRadioButton('与源文件同目录（默认）', self)
        self.follow_radio.setChecked(True)
        self.follow_radio.toggled.connect(self._on_out_mode_changed)
        out_row.addWidget(self.follow_radio)
        self.custom_radio = QRadioButton('统一输出到：', self)
        self.custom_radio.toggled.connect(self._on_out_mode_changed)
        out_row.addWidget(self.custom_radio)
        self.out_label = QLabel('未选择 · 各文件保存到自己所在目录', self)
        self.out_label.setObjectName('caption')
        self.out_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                    QSizePolicy.Policy.Preferred)
        self.out_label.setToolTip('未选择文件夹时，每个字幕文件保存在对应音视频文件旁边')
        out_row.addWidget(self.out_label, 1)
        self.choose_dir_btn = self._btn('选择文件夹…')
        self.choose_dir_btn.clicked.connect(self._on_choose_dir)
        out_row.addWidget(self.choose_dir_btn)
        self.clear_dir_btn = self._btn('恢复默认')
        self.clear_dir_btn.clicked.connect(self._on_clear_dir)
        out_row.addWidget(self.clear_dir_btn)
        ov.addLayout(out_row)
        self.out_hint = QLabel(
            '同名文件不会覆盖，会自动加序号（如 视频_字幕_2.srt）。', self)
        self.out_hint.setObjectName('caption')
        ov.addWidget(self.out_hint)
        root.addWidget(out_card)

        # 本次批量选项
        opt_card = self._card()
        opv = QVBoxLayout(opt_card)
        opv.setContentsMargins(14, 12, 14, 12)
        opv.setSpacing(8)
        opt_row = QHBoxLayout()
        opt_row.setSpacing(8)
        opt_row.addWidget(QLabel('字幕格式:', self))
        self.fmt_combo = QComboBox(self)
        for key, label in FMT_LABELS.items():
            self.fmt_combo.addItem(label, key)
        idx = self.fmt_combo.findData(self.settings.get('fmt', 'srt'))
        self.fmt_combo.setCurrentIndex(max(0, idx))
        self.fmt_combo.setMinimumWidth(130)
        opt_row.addWidget(self.fmt_combo)
        opt_row.addSpacing(10)
        opt_row.addWidget(QLabel('导出内容:', self))
        self.mode_combo = QComboBox(self)
        for key, label in MODE_LABELS.items():
            self.mode_combo.addItem(label, key)
        idx = self.mode_combo.findData(self.settings.get('export_mode', 'source'))
        self.mode_combo.setCurrentIndex(max(0, idx))
        self.mode_combo.setMinimumWidth(140)
        self.mode_combo.setToolTip(
            '只导出原文：不调用翻译接口\n'
            '只导出译文：逐条翻译后只保留译文\n'
            '原文 + 译文：逐条翻译后双语对照')
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        opt_row.addWidget(self.mode_combo)
        opt_row.addStretch(1)
        opt_note = QLabel('仅对本次批量生效', self)
        opt_note.setObjectName('caption')
        opt_row.addWidget(opt_note)
        opv.addLayout(opt_row)

        trans_row = QHBoxLayout()
        trans_row.setSpacing(8)
        self.translate_check = QCheckBox('同时翻译字幕到目标语言', self)
        self.translate_check.setChecked(
            bool(load_user_config().get('batch_translate', False)))
        self.translate_check.toggled.connect(self._on_translate_toggled)
        trans_row.addWidget(self.translate_check)
        self.cfg_label = QLabel(self.settings.get('summary', ''), self)
        self.cfg_label.setObjectName('caption')
        self.cfg_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                     QSizePolicy.Policy.Preferred)
        trans_row.addWidget(self.cfg_label, 1)
        opv.addLayout(trans_row)
        self.translate_hint = QLabel('', self)
        self.translate_hint.setObjectName('caption')
        self.translate_hint.setWordWrap(True)
        opv.addWidget(self.translate_hint)
        self._update_translate_hint()
        root.addWidget(opt_card)

        # 进度
        self.status_label = QLabel('准备就绪', self)
        self.status_label.setObjectName('fieldLabel')
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.total_progress = QProgressBar(self)
        self.total_progress.setRange(0, 100)
        self.total_progress.setValue(0)
        self.total_progress.setFormat('总进度 %p%')
        root.addWidget(self.total_progress)
        self.file_progress = QProgressBar(self)
        self.file_progress.setRange(0, 100)
        self.file_progress.setValue(0)
        self.file_progress.setFormat('当前文件 %p%')
        root.addWidget(self.file_progress)

        # 底部按钮
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.start_btn = self._btn('开始批量转录', 'primary')
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = self._btn('停止')
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        self.retry_btn = self._btn('重试失败项')
        self.retry_btn.clicked.connect(self._retry_failed)
        self.retry_btn.setEnabled(False)
        self.open_dir_btn = self._btn('打开输出文件夹')
        self.open_dir_btn.clicked.connect(self._open_output_dir)
        self.open_dir_btn.setEnabled(False)
        self.copy_btn = self._btn('复制输出路径')
        self.copy_btn.clicked.connect(self._copy_outputs)
        self.copy_btn.setEnabled(False)
        self.close_btn = self._btn('关闭')
        self.close_btn.clicked.connect(self.close)
        for b in (self.start_btn, self.stop_btn, self.retry_btn,
                  self.open_dir_btn, self.copy_btn):
            bottom.addWidget(b)
        bottom.addStretch(1)
        bottom.addWidget(self.close_btn)
        root.addLayout(bottom)
        self._refresh_table()

    # ── 文件列表 ────────────────────────────────────────────

    def add_paths(self, paths, quiet=False):
        """增量添加；过滤重复、不支持与不存在的路径。"""
        valid, rejected = classify_paths(paths)
        existing = {os.path.normcase(j.path) for j in self.jobs}
        added, dup = 0, 0
        for path in valid:
            if os.path.normcase(path) in existing:
                dup += 1
                continue
            self.jobs.append(Job(path))
            existing.add(os.path.normcase(path))
            added += 1
        self._refresh_table()
        if not quiet and (added or dup or rejected):
            parts = [f'已添加 {added} 个文件']
            if dup:
                parts.append(f'跳过重复 {dup} 个')
            if rejected:
                parts.append(f'忽略 {len(rejected)} 个（'
                             + '、'.join(sorted({r for _, r in rejected})) + '）')
            self.status_label.setText(' · '.join(parts))
        return added

    def _refresh_table(self):
        self.table.setRowCount(len(self.jobs))
        for row, job in enumerate(self.jobs):
            name = QTableWidgetItem(job.name)
            name.setToolTip(job.path)
            try:
                size_mb = os.path.getsize(job.path) / (1024 * 1024)
                size_text = f'{size_mb:.1f} MB'
            except OSError:
                size_text = '—'
            status = job.status
            detail = job.detail
            if status == DONE and job.output:
                count = f'{job.lines} 条 · ' if job.lines else ''
                detail = count + os.path.basename(job.output)
            if detail and status not in (PENDING, RUNNING):
                text = f'{STATUS_TEXT.get(status, status)} · {detail}'
            else:
                text = STATUS_TEXT.get(status, status)
            item = QTableWidgetItem(text)
            item.setToolTip(job.output or detail or job.path)
            if status == DONE:
                item.setForeground(QColor(OK_COLOR))
            elif status == FAILED:
                item.setForeground(QColor(ERR_COLOR))
            elif status == RUNNING:
                item.setForeground(QColor(WARN_COLOR))
            else:
                item.setForeground(QColor(Palette.TEXT_MUTED))
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(size_text))
            self.table.setItem(row, 2, item)
        self.count_label.setText(f'共 {len(self.jobs)} 个文件')

    def _on_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择多个音视频文件', self._last_dir,
            f'音视频文件 ({file_pattern()});;所有文件 (*)')
        if paths:
            self._last_dir = os.path.dirname(paths[0])
            save_user_config({'asr_last_dir': self._last_dir})
            self.add_paths(paths)

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, '选择包含音视频文件的文件夹', self._last_dir)
        if not folder:
            return
        self._last_dir = folder
        save_user_config({'asr_last_dir': folder})
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
            paths = [e.path for e in entries
                     if e.is_file() and is_supported(e.path)]
        except OSError as e:
            self.status_label.setText(f'读取文件夹失败：{e}')
            return
        if not paths:
            self.status_label.setText('该文件夹内没有受支持的音视频文件')
            return
        self.add_paths(paths)

    def _on_remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()},
                      reverse=True)
        for row in rows:
            if 0 <= row < len(self.jobs):
                self.jobs.pop(row)
        self._refresh_table()

    def _on_clear(self):
        self.jobs.clear()
        self._refresh_table()
        self.status_label.setText('列表已清空')

    def _move(self, delta):
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < len(self.jobs)):
            return
        self.jobs[row], self.jobs[target] = self.jobs[target], self.jobs[row]
        self._refresh_table()
        self.table.selectRow(target)

    # ── 输出目录 ────────────────────────────────────────────

    def _on_out_mode_changed(self, checked):
        if checked and self.custom_radio.isChecked() and not self.out_dir:
            self._on_choose_dir()

    def _on_choose_dir(self):
        folder = QFileDialog.getExistingDirectory(
            self, '选择字幕输出文件夹', self._last_out_dir or self._last_dir)
        if not folder:
            if self.custom_radio.isChecked() and not self.out_dir:
                self.follow_radio.setChecked(True)
            return
        self.out_dir = folder
        self._last_out_dir = folder
        save_user_config({'batch_last_out_dir': folder})
        self.custom_radio.setChecked(True)
        self.out_label.setText(folder)
        self.out_label.setToolTip(folder)
        self.out_hint.setText('所有字幕统一保存到该文件夹；同名文件自动加序号，不会覆盖。')

    def _on_clear_dir(self):
        self.out_dir = None
        self.follow_radio.setChecked(True)
        self.out_label.setText('未选择 · 各文件保存到自己所在目录')
        self.out_label.setToolTip('未选择文件夹时，每个字幕文件保存在对应音视频文件旁边')
        self.out_hint.setText('同名文件不会覆盖，会自动加序号（如 视频_字幕_2.srt）。')

    # ── 选项 ────────────────────────────────────────────────

    COST_NOTE = ('逐条调用翻译接口，文件多或字幕长时耗时较久，'
                 '可能产生 API 费用；')

    def _update_translate_hint(self):
        """只解释「当前组合会产出什么」，不替用户改导出内容。"""
        mode = self.mode_combo.currentData()
        checked = self.translate_check.isChecked()
        if mode == 'source':
            text = ('当前「只导出原文」：只转录不调用翻译接口。'
                    if not checked else
                    '当前「只导出原文」：已勾选翻译，但原文模式不会调用翻译接口，'
                    '不会产生 API 费用；需要译文请切换导出内容。')
        elif not checked:
            text = ('当前导出内容需要译文，请先勾选「同时翻译字幕」，'
                    '或改选「只导出原文」。')
        elif mode == 'target':
            text = ('当前「只导出译文」：' + self.COST_NOTE
                    + '字幕里只保留译文。')
        else:
            text = ('当前「原文 + 译文」：' + self.COST_NOTE
                    + '生成双语对照字幕。')
        if checked and mode != 'source':
            text += '批量任务使用独立翻译器实例，不会和主窗口手动翻译互相改语言。'
        self.translate_hint.setText(text)
        if mode != 'source' and not checked:
            self.translate_hint.setStyleSheet(f'color: {WARN_COLOR};')
        else:
            self.translate_hint.setStyleSheet('')

    def _on_mode_changed(self, _index=None):
        # 三种模式始终由用户选择；需要译文时只补上翻译开关，绝不改模式。
        mode = self.mode_combo.currentData()
        if needs_translation(mode) and not self.translate_check.isChecked():
            self.translate_check.blockSignals(True)
            self.translate_check.setChecked(True)
            self.translate_check.blockSignals(False)
            save_user_config({'batch_translate': True})
        self._update_translate_hint()

    def _on_translate_toggled(self, checked):
        save_user_config({'batch_translate': bool(checked)})
        self._update_translate_hint()

    # ── 拖放 ────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData() else []
        if any(is_supported(u.toLocalFile()) or os.path.isdir(u.toLocalFile())
               for u in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if os.path.isdir(local):
                try:
                    paths.extend(e.path for e in os.scandir(local)
                                 if e.is_file() and is_supported(e.path))
                except OSError:
                    pass
            elif local:
                paths.append(local)
        if paths:
            self.add_paths(paths)
            self.status_label.setText(
                f'已拖入 {len(paths)} 个项目，共 {len(self.jobs)} 个文件待处理')
        event.acceptProposedAction()

    # ── 任务执行 ────────────────────────────────────────────

    def _snapshot(self):
        settings = dict(self.settings)
        settings['fmt'] = self.fmt_combo.currentData()
        mode = self.mode_combo.currentData()
        settings['export_mode'] = mode
        # 只导出原文时，即便勾着翻译也不调用接口；三种模式都按用户所选生效。
        settings['translate'] = (self.translate_check.isChecked()
                                 and needs_translation(mode))
        settings['out_dir'] = self.out_dir if self.custom_radio.isChecked() else None
        return settings

    def _preflight(self, settings):
        """开始前把会白跑一趟的问题一次拦下。"""
        if (settings.get('export_mode') in ('target', 'both')
                and not settings.get('translate')):
            return ('导出内容需要译文，请勾选「同时翻译字幕」，'
                    '或改选「只导出原文」')
        if not self.jobs:
            return '请先添加音视频文件'
        if self._running:
            return '已有批量任务在运行'
        import speech_recognizer as asr
        if settings.get('mode') == 'local':
            ok, _ = asr.check_dependencies()
            if not ok:
                return '识别组件未安装，请先回到音视频页安装 faster-whisper'
            name = settings.get('model')
            if not name or not (asr.is_model_ready(name)
                                or asr.is_imported_model_ready(name)):
                return '识别模型未就绪，请先下载或导入模型'
        else:
            try:
                asr.validate_api_url(settings.get('api_base', ''))
            except Exception as e:
                return str(e)
            if not str(settings.get('api_model') or '').strip():
                return '请先在音视频页填写 API 模型名'
        targets = {output_dir_for(j.path, settings.get('out_dir'))
                   for j in self.jobs}
        for folder in targets:
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                return f'无法创建输出文件夹：{folder}'
            if not can_write_dir(folder):
                return f'输出文件夹不可写：{folder}'
        return None

    def _confirm_large_api_files(self, settings):
        """在线 API 多数限制 25 MB，提前提醒而不是跑到一半才连着失败。"""
        if settings.get('mode') != 'api':
            return True
        limit = 25 * 1024 * 1024
        big = []
        for job in self.jobs:
            try:
                if os.path.getsize(job.path) > limit:
                    big.append(job.name)
            except OSError:
                pass
        if not big:
            return True
        names = '、'.join(big[:3]) + ('…' if len(big) > 3 else '')
        answer = QMessageBox.question(
            self, '大文件提示',
            f'有 {len(big)} 个文件超过 25 MB（{names}）。\n'
            '多数在线转录接口会拒绝超大文件，这些文件可能被标记为失败，'
            '但不会影响其它文件。建议改用本地模型或先提取音频。\n\n仍要继续吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _start(self):
        settings = self._snapshot()
        error = self._preflight(settings)
        if error:
            QMessageBox.warning(self, '无法开始', error)
            return
        if not self._confirm_large_api_files(settings):
            return
        if self.jobs and all(j.status == DONE for j in self.jobs):
            answer = QMessageBox.question(
                self, '重新处理？',
                '列表中的文件上次已全部完成。再次开始会重新识别一遍，'
                '并生成带序号的新字幕文件（不会覆盖旧文件）。\n\n要继续吗？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._cancel = threading.Event()
        self._running = True
        self._stopping = False
        retry_mode = self._retry_mode
        self._retry_mode = False
        self._last_summary = ''
        self._set_editing_enabled(False)
        self.total_progress.setRange(0, len(self.jobs))
        self.total_progress.setValue(0)
        self.total_progress.setFormat('总进度 %p%')
        self.file_progress.setValue(0)
        self.status_label.setText('正在开始批量转录…')
        # 重试失败项时保留已完成项的输出路径，只重置需要重跑的项
        reset_status = ((FAILED, SKIPPED) if retry_mode
                        else (FAILED, SKIPPED, DONE))
        for job in self.jobs:
            if job.status in reset_status:
                job.status = PENDING
                job.detail = ''
                job.output = ''
                job.lines = 0
        self._refresh_table()

        runner = BatchRunner(self.jobs, settings, cancel=self._cancel,
                             resume=retry_mode)

        # 窗口在「已请求停止」后允许关闭，先缓存信号对象，避免迟到信号
        # 打到已销毁的窗口上抛 RuntimeError。
        sig_started = self._sig_started
        sig_progress = self._sig_progress
        sig_finished = self._sig_finished
        sig_all_done = self._sig_all_done

        def send(signal, *args):
            try:
                signal.emit(*args)
                return True
            except RuntimeError:
                return False

        def worker():
            try:
                summary = runner.run(
                    on_start=lambda i, n, job:
                        send(sig_started, i, n, job.name),
                    on_progress=lambda i, n, job, p, t:
                        send(sig_progress, i, n, job.name, p, t),
                    on_finish=lambda i, n, job:
                        send(sig_finished, i - 1, job.status,
                             job.detail, job.output))
                parts = [f"完成 {summary['done']}/{summary['total']}"]
                if summary['failed']:
                    parts.append(f"失败 {summary['failed']}")
                if summary['skipped']:
                    parts.append(f"跳过 {summary['skipped']}")
                send(sig_all_done, ' · '.join(parts))
            except Exception as e:
                send(sig_all_done, f'批量任务异常：{e}')

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def _stop(self):
        if not self._running:
            return
        self._cancel.set()
        self._stopping = True
        self.stop_btn.setEnabled(False)
        # 在线 API 的单次请求可能较久，此时不该把窗口一起锁死
        self.status_label.setText(
            '已请求停止，当前文件处理完后结束；此时可以关闭窗口，'
            '已生成的字幕会保留。')

    def _retry_failed(self):
        failed = [j for j in self.jobs if j.status == FAILED]
        if not failed:
            return
        # 保留已完成的行与输出路径，重试结束后「复制全部输出路径」仍然完整
        for job in failed:
            job.status = PENDING
            job.detail = ''
            job.output = ''
            job.lines = 0
        self._retry_mode = True
        self._refresh_table()
        self.status_label.setText(f'准备重试 {len(failed)} 个失败项…')
        self._start()

    def _set_editing_enabled(self, enabled):
        for widget in (self.add_files_btn, self.add_folder_btn, self.remove_btn,
                       self.up_btn, self.down_btn, self.clear_btn,
                       self.follow_radio, self.custom_radio, self.choose_dir_btn,
                       self.clear_dir_btn, self.fmt_combo, self.mode_combo,
                       self.translate_check, self.start_btn, self.retry_btn):
            widget.setEnabled(enabled)
        self.stop_btn.setEnabled(not enabled)
        self.table.setEnabled(enabled)

    @Slot(int, int, str)
    def _on_started(self, index, total, name):
        self.file_progress.setRange(0, 100)
        self.file_progress.setValue(0)
        self.status_label.setText(f'[{index}/{total}] 正在识别：{name}')

    @Slot(int, int, str, int, str)
    def _on_progress(self, index, total, name, percent, text):
        if 0 <= percent <= 100:
            self.file_progress.setRange(0, 100)
            self.file_progress.setValue(percent)
        self.status_label.setText(
            f'[{index}/{total}] {name} · {text} {percent}%')

    @Slot(int, str, str, str)
    def _on_finished(self, row, status, detail, output):
        if 0 <= row < len(self.jobs):
            job = self.jobs[row]
            job.status = status
            job.detail = detail
            job.output = output
        self.total_progress.setValue(min(row + 1, len(self.jobs)))
        self._refresh_table()

    @Slot(str)
    def _on_all_done(self, text):
        self._running = False
        self._last_summary = text
        self._set_editing_enabled(True)
        self.stop_btn.setEnabled(False)
        self.file_progress.setValue(100)
        self.status_label.setText(text)
        failed = sum(1 for j in self.jobs if j.status == FAILED)
        skipped = sum(1 for j in self.jobs if j.status == SKIPPED)
        self.retry_btn.setEnabled(failed > 0)
        outputs = [j.output for j in self.jobs
                   if j.status == DONE and j.output]
        self.open_dir_btn.setEnabled(bool(outputs) or bool(self.out_dir))
        self.copy_btn.setEnabled(bool(outputs))
        if failed or skipped:
            self.status_label.setStyleSheet(f'color: {WARN_COLOR};')
        else:
            self.status_label.setStyleSheet(f'color: {OK_COLOR};')

    # ── 结果操作 ────────────────────────────────────────────

    def _output_folder(self):
        if self.custom_radio.isChecked() and self.out_dir:
            return self.out_dir
        for job in self.jobs:
            if job.status == DONE and job.output:
                return os.path.dirname(job.output)
        return None

    def _open_output_dir(self):
        folder = self._output_folder()
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _copy_outputs(self):
        paths = [j.output for j in self.jobs if j.status == DONE and j.output]
        if paths:
            QApplication.clipboard().setText('\n'.join(paths))
            self.status_label.setText(f'已复制 {len(paths)} 个字幕文件路径')

    def closeEvent(self, event):
        if self._running and not self._stopping:
            QMessageBox.information(
                self, '任务进行中',
                '批量转录还没结束，请先点「停止」，等当前文件处理完再关闭窗口。')
            event.ignore()
            return
        # 已请求停止：状态栏已经说明「可以关闭窗口」，直接放行，
        # 不再弹模态框打断用户（离屏/自动化场景也不会被弹窗阻塞）。
        super().closeEvent(event)
