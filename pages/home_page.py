#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主页：截图控制、图片预览、OCR 文本、翻译结果。"""
import os
import threading
from datetime import datetime

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from theme import Palette
from flow_layout import FlowLayout
from hotkeys import (display_hotkey, get_local_hotkeys,
                     qt_key_sequence_text)
from config import (SUPPORTED_TARGET_LANGS, SUPPORTED_SOURCE_LANGS,
                    APP_VERSION, SUBTITLE_AUDIO_SOURCES,
                    get_subtitle_settings, load_user_config,
                    save_user_config)


class HomePage(QWidget):
    # 快捷键可在设置页改，占位文案里的组合键必须跟着一起变
    _OCR_PLACEHOLDER = (
        "可直接粘贴文字后点「翻译」（快捷键 {hotkey}）；\n"
        "也可以点上方「区域截图」从屏幕取字，识别结果会填到这里")
    screenshotRequested = Signal()
    fullscreenRequested = Signal()
    liveTranslateRequested = Signal()
    subtitleRequested = Signal()
    translateRequested = Signal(str)
    polishRequested = Signal(str, str)  # (kind, text)
    extractRequested = Signal()
    clearRequested = Signal()
    moreActionsRequested = Signal()   # 「更多动作 ▾」镜像托盘菜单

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("homePage")
        # 普通 QWidget 需要显式开启才会绘制 QSS 的 background
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._current_image_path = None
        self._ocr_text_cache = ""
        self._trans_text_cache = ""
        self._translate_shortcut = None
        self._translate_label = "翻译"
        self._build_ui()
        self.refresh_shortcuts()

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
        # 长提示（缺组件、怎么解决）原本会被单行标签裁掉后半句：
        # 允许换行并限宽，同时把全文放进悬停提示，窄窗口下也读得完整。
        self.status_label.setWordWrap(True)
        self.status_label.setMaximumWidth(420)
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
        self.image_label = QLabel("等待截图…\n点上方「区域截图」开始，或把图片拖进来", self)
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
        self.ocr_text.setPlaceholderText(
            self._OCR_PLACEHOLDER.format(hotkey="Ctrl + Enter"))
        self.ocr_text.setMinimumHeight(100)
        right_layout.addWidget(self.ocr_text, 1)

        trans_title = QLabel("翻译结果", self)
        trans_title.setObjectName("sectionTitle")
        right_layout.addWidget(trans_title)

        self.trans_text = QTextEdit(self)
        self.trans_text.setReadOnly(True)
        self.trans_text.setMinimumHeight(100)
        self.trans_text.setPlaceholderText(
            "译文将显示在这里 · 可 Ctrl+A 全选后复制，或点下方「复制译文」")
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

        self.copy_image_btn = self._btn("复制截图", kind="ghost")
        self.copy_image_btn.setToolTip(
            "把「截图预览」里的图片按原尺寸复制到剪贴板，可直接粘贴到聊天框 / 文档\n"
            "区域截图即使点了取消 / 右键取消，也会保留在预览里")
        self.copy_image_btn.clicked.connect(self._copy_image)
        action_bar.addWidget(self.copy_image_btn)

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
        self.subtitle_source_combo.setMinimumWidth(132)
        # 下拉列表宽度默认跟随 combo，104px 会把「电脑声音」挤成「电脑…声音」。
        # 让 popup 按内容自适应并给足下限，两个音源名都能完整显示。
        self.subtitle_source_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.subtitle_source_combo.view().setMinimumWidth(150)
        self.subtitle_source_combo.setMaxVisibleItems(4)
        self.subtitle_source_combo.setToolTip(
            "麦克风：听你说话；电脑声音：听视频 / 音乐 / 会议声音\n"
            "内部声音需要 WASAPI 环回组件，设置页可一键安装")
        self.subtitle_source_combo.currentIndexChanged.connect(
            self._on_subtitle_source_changed)
        action_bar.addWidget(self.subtitle_source_combo)

        action_bar.addStretch(1)

        # 放在底部动作条而不是控制卡 FlowLayout：控制卡在正常窗口宽度下
        # 必须保持一行（有专门的布局回归测试），这里由 stretch 吸收宽度。
        # Windows 常把托盘图标折叠，字幕暂停 / 清空 / 双语模式等动作就
        # 完全不可达；主界面给一个同款菜单作为始终可见的入口。
        self.more_btn = self._btn("更多动作 ▾", kind="ghost")
        self.more_btn.setToolTip(
            "托盘右键菜单里的动作这里也能点：截图、字幕启停 / 暂停 / 清空、"
            "双语模式、任务中心、命令面板、检查更新\n"
            "也可按 Ctrl+K 搜索并执行任意动作")
        self.more_btn.clicked.connect(self.moreActionsRequested.emit)
        action_bar.addWidget(self.more_btn)

        self.save_btn = self._btn("保存 ▾", kind="ghost")
        self.save_btn.setToolTip(
            "保存识别 / 翻译结果（.txt / .md）\n"
            "点开后可选择：仅译文、仅原文、原文 + 译文")
        self.save_btn.clicked.connect(self._on_save_clicked)
        action_bar.addWidget(self.save_btn)

        self.clear_btn = self._btn("清空", kind="ghost")
        self.clear_btn.setToolTip(
            "清空原文、译文与截图预览；清空后可在提示条上点「撤销」找回")
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
            "实时听写麦克风或电脑声音，直接在默认区域显示字幕框\n"
            "字幕框可拖动 / 四角缩放，位置会被记住；关掉字幕框后仍继续识别，\n"
            "结果同步显示在这页的「识别文本」和「翻译结果」里\n"
            "再次点这个按钮（运行中会变成「停止实时字幕」）即可彻底停止")
        self.subtitle_btn.clicked.connect(self.subtitleRequested.emit)
        layout.addWidget(self.subtitle_btn)

        layout.addSpacing(16)

        src_lang_label = QLabel("从:", self)
        src_lang_label.setObjectName("fieldLabel")
        layout.addWidget(src_lang_label)

        cfg = load_user_config()
        self.src_lang_combo = QComboBox(self)
        self.src_lang_combo.addItems(list(SUPPORTED_SOURCE_LANGS.keys()))
        self._restore_lang(self.src_lang_combo,
                           cfg.get('home_source_lang'), '自动检测')
        self.src_lang_combo.setMinimumWidth(100)
        self.src_lang_combo.setMaxVisibleItems(6)  # 最多显示 6 项，超出滚动
        layout.addWidget(self.src_lang_combo)

        lang_label = QLabel("翻译到:", self)
        lang_label.setObjectName("fieldLabel")
        layout.addWidget(lang_label)

        self.lang_combo = QComboBox(self)
        self.lang_combo.addItems(list(SUPPORTED_TARGET_LANGS.keys()))
        self._restore_lang(self.lang_combo,
                           cfg.get('home_target_lang'), '中文')
        self.lang_combo.setMinimumWidth(100)
        self.lang_combo.setMaxVisibleItems(6)  # 最多显示 6 项，超出滚动
        layout.addWidget(self.lang_combo)
        # 记住上次选择：只在用户主动改动后写盘，配置是增量合并，
        # 不会抹掉设置页刚保存的密钥。
        self.src_lang_combo.currentIndexChanged.connect(
            self._on_home_lang_changed)
        self.lang_combo.currentIndexChanged.connect(
            self._on_home_lang_changed)
        # 标签与下拉框同高：QSS 的 max-height 对 QComboBox 不生效，
        # 只靠 FlowLayout 居中会留下「小标签 + 偏高的框」。
        import ui_theme
        ui_theme.align_lang_row(src_lang_label, self.src_lang_combo,
                                lang_label, self.lang_combo)

        self.extract_btn = self._btn("提取文字")
        self.extract_btn.clicked.connect(self.extractRequested.emit)
        layout.addWidget(self.extract_btn)

        self.translate_btn = self._btn(self._translate_label, kind="success")
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
        self.status_label.setToolTip(str(text or ""))
        # 不传颜色时也要把状态点复位成中性色，否则上一次翻译失败的红点
        # 会一直留着，配「已复制译文」这种成功文案非常误导。
        self.status_dot.setStyleSheet(
            f"color: {color or '#71717a'}; font-size: 14px;")

    def set_translating(self, running):
        """翻译期间禁用按钮并显示状态，避免用户以为没点上而重复触发。"""
        self.translate_btn.setEnabled(not running)
        self.translate_btn.setText(
            "翻译中…" if running else self._translate_label)

    def refresh_shortcuts(self):
        """按设置页保存的组合重建窗口内快捷键，保存后立即生效无需重启。

        组合键来自 hotkeys.py 的统一配置，和全局快捷键共用录入控件与冲突
        检测；清空输入框 = 不注册任何 QShortcut（不占用按键）。
        """
        combo = get_local_hotkeys().get('translate_home', '')
        label = display_hotkey(combo)
        if self._translate_shortcut is not None:
            self._translate_shortcut.setEnabled(False)
            self._translate_shortcut.deleteLater()
            self._translate_shortcut = None
        invalid = False
        if combo:
            seq = QKeySequence(qt_key_sequence_text(combo))
            if not seq.isEmpty():
                self._translate_shortcut = QShortcut(seq, self)
                self._translate_shortcut.activated.connect(self._on_translate)
            else:
                # 配置被手改成 Qt 解析不了的组合时，别静默失效还显示「已生效」
                invalid = True
        if hasattr(self, 'translate_btn'):
            if invalid:
                self.translate_btn.setToolTip(
                    f"快捷键 {label} 无效，Qt 无法识别，"
                    "请在「设置 → 快捷键」里重新录入")
            else:
                self.translate_btn.setToolTip(
                    f"翻译「识别文本」里的内容（快捷键 {label}）" if combo
                    else "翻译「识别文本」里的内容（未设置快捷键，"
                         "可在「设置 → 快捷键」里自定义）")
        if invalid:
            self.set_status(
                f"窗口内快捷键 {label} 无效，请在设置里重新录入", "#eab308")
        if hasattr(self, 'ocr_text'):
            self.ocr_text.setPlaceholderText(self._OCR_PLACEHOLDER.format(
                hotkey=(label if combo else "工具栏「翻译」按钮")))
        # 按钮文字保持紧凑：控制卡在正常窗口宽度下必须挤得进一行（有专门的
        # FlowLayout 回归测试），把 "Ctrl + Enter" 写进按钮会把它撑到第二行。
        # 当前组合仍能在 tooltip、识别文本占位文案、设置页和 Ctrl+K 命令
        # 面板里看到，四处可发现，不必牺牲布局。
        self._translate_label = "翻译"
        if hasattr(self, 'translate_btn') and self.translate_btn.isEnabled():
            self.translate_btn.setText(self._translate_label)

    @staticmethod
    def _restore_lang(combo, saved, default):
        """配置里可能是旧版 / 手改的非法值，找不到就回落到默认项。"""
        idx = combo.findText(str(saved or ''))
        combo.setCurrentIndex(idx if idx >= 0
                              else max(0, combo.findText(default)))

    @Slot(int)
    def _on_home_lang_changed(self, _index=0):
        combo = getattr(self, 'lang_combo', None)
        if combo is None:
            return  # 构造期间另一个下拉还没建好
        save_user_config({
            'home_source_lang': self.src_lang_combo.currentText(),
            'home_target_lang': combo.currentText(),
        })

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
        # 长文本 / 网络慢时按钮毫无变化，用户会以为没点上而连点几下，
        # 起多个后台线程让译文和提示来回闪。与「AI 润色」保持一致。
        self.set_translating(True)
        self.set_status("正在翻译…", "#eab308")
        self.translateRequested.emit(text)

    @Slot()
    def _copy_source(self):
        text = self.ocr_text.toPlainText()
        if text:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self.set_status("已复制原文")
        else:
            self.set_status("没有可复制的原文", "#71717a")

    @Slot()
    def _copy_translation(self):
        text = self.trans_text.toPlainText()
        if text:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self.set_status("已复制译文")
        else:
            self.set_status("没有可复制的译文", "#71717a")

    @Slot()
    def _copy_image(self):
        """把当前预览图按原尺寸放进剪贴板。"""
        path = getattr(self, "_current_image_path", None)
        if not path or not os.path.exists(path):
            self.set_status(
                "还没有截图，可先点「区域截图」或「全屏截图」", "#71717a")
            return
        from PySide6.QtWidgets import QApplication
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.set_status("截图文件无法读取，请重新截图", "#e5635f")
            return
        QApplication.clipboard().setPixmap(pixmap)
        self.set_status(
            f"已复制截图（{pixmap.width()}×{pixmap.height()}）", "#4ade80")

    @Slot()
    def _on_clear(self):
        src = self.ocr_text.toPlainText()
        trans = self.trans_text.toPlainText()
        image_path = self._current_image_path
        if not (src or trans or image_path):
            self.set_status("没有可清空的内容", "#71717a")
            return
        self.ocr_text.clear()
        self.trans_text.clear()
        self.image_label.clear()
        self.image_label.setText("等待截图…\n点上方「区域截图」开始，或把图片拖进来")
        self._current_image_path = None
        self.set_status("已清空")
        self.clearRequested.emit()
        self._offer_undo_clear(src, trans, image_path)

    def _offer_undo_clear(self, src, trans, image_path):
        """清空保持可逆：给一次撤销机会，而不是弹确认打断高频操作。"""
        try:
            from qfluentwidgets import InfoBar, InfoBarPosition
            from PySide6.QtWidgets import QPushButton
            bar = InfoBar.success(
                title="已清空",
                content="原文、译文与截图预览已清空，可点右侧「撤销」找回。",
                duration=8000, position=InfoBarPosition.TOP, parent=self)
            btn = QPushButton("撤销", bar)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

            def undo():
                self.ocr_text.setPlainText(src)
                self.trans_text.setPlainText(trans)
                self._ocr_text_cache = src
                self._trans_text_cache = trans
                self._current_image_path = image_path
                if image_path and os.path.exists(image_path):
                    self.set_image(image_path)
                else:
                    self.image_label.clear()
                    self.image_label.setText("等待截图…\n点上方「区域截图」开始，或把图片拖进来")
                self.set_status("已撤销清空", "#4ade80")
                try:
                    bar.close()
                except Exception:
                    pass

            btn.clicked.connect(undo)
            bar.addWidget(btn)
        except Exception:
            # 提示条不可用时清空本身依旧有效，只是少了撤销入口
            pass

    @Slot()
    def _on_save_clicked(self):
        """先选「保存哪种形式」，再选文件；与批量转录三种导出模式一致。"""
        src = self.ocr_text.toPlainText()
        trans = self.trans_text.toPlainText()
        if not src.strip() and not trans.strip():
            self.set_status("没有可保存的内容", "#71717a")
            return
        from PySide6.QtWidgets import QMenu
        from PySide6.QtCore import QPoint
        menu = QMenu(self)
        items = (
            ("仅译文", 'trans', bool(trans.strip())),
            ("仅原文", 'src', bool(src.strip())),
            ("原文 + 译文", 'both', bool(src.strip() or trans.strip())),
        )
        for text, mode, enabled in items:
            action = menu.addAction(text)
            action.setData(mode)
            action.setEnabled(enabled)
            action.setToolTip({
                'trans': "文件里只写翻译结果",
                'src': "文件里只写识别原文",
                'both': "文件里按「【原文】/【译文】」分段写两段",
            }[mode])
        picked = menu.exec(
            self.save_btn.mapToGlobal(QPoint(0, self.save_btn.height())))
        if picked is None:
            return
        self._save_result(picked.data(), src, trans)

    def _save_result(self, mode, src, trans):
        """按用户选择的形式写文件。"""
        from PySide6.QtWidgets import QFileDialog
        suffix = {'trans': '译文', 'src': '原文', 'both': '原文译文'}[mode]
        default_name = (
            f"翻译结果_{suffix}_{datetime.now():%Y%m%d_%H%M%S}.txt")
        path, chosen = QFileDialog.getSaveFileName(
            self, f"保存{suffix}", default_name,
            "文本文件 (*.txt);;Markdown (*.md)")
        if not path:
            return
        # 用户手输文件名不带后缀时按所选格式补全，避免存出无扩展名文件
        if not os.path.splitext(path)[1]:
            path += '.md' if '*.md' in (chosen or '') else '.txt'
        if mode == 'src':
            body = src
        elif mode == 'trans':
            body = trans
        else:
            parts = []
            if src.strip():
                parts.append("【原文】\n" + src)
            if trans.strip():
                parts.append("【译文】\n" + trans)
            body = "\n\n".join(parts)
        try:
            # .txt 带 UTF-8 BOM：Windows 记事本 / Excel 打开中文才不乱码，
            # 与项目其它文本导出（convert_libs）保持一致；.md 用纯 UTF-8。
            enc = "utf-8-sig" if path.lower().endswith(".txt") else "utf-8"
            with open(path, "w", encoding=enc) as f:
                f.write(body)
                if not body.endswith("\n"):
                    f.write("\n")
            self.set_status(
                f"已保存{suffix}到 {os.path.basename(path)}", "#4ade80")
        except Exception as e:
            self.set_status(f"保存失败：{e}", "#e5635f")
