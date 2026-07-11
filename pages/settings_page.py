#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置页：翻译 API、密钥、快捷键。"""
import json
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from config import (
    CONFIG_FILE, DEFAULT_TRANSLATION_API, TRANSLATION_APIS,
    load_user_config, save_user_config,
)


class _Section(QFrame):
    """带标题的扁平分组卡片，内部用网格排列 标签 | 控件。"""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)
        heading = QLabel(title, self)
        heading.setObjectName("sectionTitle")
        outer.addWidget(heading)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(12)
        self.grid.setColumnStretch(1, 1)
        outer.addLayout(self.grid)
        self._row = 0

    def add(self, label, widget, tip=None):
        lbl = QLabel(label)
        lbl.setObjectName("fieldLabel")
        if tip:
            lbl.setToolTip(tip)
        self.grid.addWidget(lbl, self._row, 0,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.grid.addWidget(widget, self._row, 1, Qt.AlignmentFlag.AlignLeft)
        self._row += 1


class SettingsPage(QScrollArea):
    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsContainer")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._cfg = load_user_config()
        self._build_ui()

    def _build_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        title = QLabel("设置", self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        # ---- 翻译设置 ----
        trans_section = _Section("翻译设置")

        # API 选择
        self.api_combo = QComboBox()
        api_names = list(TRANSLATION_APIS.keys())
        self.api_combo.addItems(api_names)
        current_api = self._cfg.get("translation_api", DEFAULT_TRANSLATION_API)
        for name, value in TRANSLATION_APIS.items():
            if value == current_api:
                self.api_combo.setCurrentText(name)
                break
        trans_section.add("翻译 API:", self.api_combo)

        # DeepL API Key
        self.deepl_key = QLineEdit()
        self.deepl_key.setPlaceholderText("DeepL API 密钥（可选）")
        self.deepl_key.setText(self._cfg.get("deepl_api_key", ""))
        self.deepl_key.setMinimumWidth(300)
        trans_section.add("DeepL Key:", self.deepl_key,
                         tip="DeepL 翻译需要 API 密钥")

        # 百度 AppID
        self.baidu_appid = QLineEdit()
        self.baidu_appid.setPlaceholderText("百度翻译开放平台 AppID（可选）")
        self.baidu_appid.setText(self._cfg.get("baidu_appid", ""))
        self.baidu_appid.setMinimumWidth(300)
        trans_section.add("百度 AppID:", self.baidu_appid,
                         tip="百度翻译需要 AppID")

        # 百度 SecretKey
        self.baidu_secret = QLineEdit()
        self.baidu_secret.setPlaceholderText("百度翻译 SecretKey（可选）")
        self.baidu_secret.setText(self._cfg.get("baidu_secret", ""))
        self.baidu_secret.setMinimumWidth(300)
        trans_section.add("百度 Secret:", self.baidu_secret,
                         tip="百度翻译需要 SecretKey")

        layout.addWidget(trans_section)

        # ---- 常规设置 ----
        general_section = _Section("常规设置")

        self.hotkey_entry = QLineEdit()
        self.hotkey_entry.setText(self._cfg.get("hotkey", "ctrl+shift+a"))
        self.hotkey_entry.setMinimumWidth(200)
        general_section.add("截图快捷键:", self.hotkey_entry,
                           tip="格式: ctrl+shift+a（小写）")

        info = QLabel(
            "Google 翻译: 免费，无需密钥\n"
            "DeepL 翻译: 需要申请 API 密钥 (deepl.com/pro-api)\n"
            "百度翻译: 需要申请百度翻译开放平台账号\n"
            "MyMemory: 免费，无需密钥"
        )
        info.setObjectName("caption")
        info.setWordWrap(True)
        general_section.grid.addWidget(info, general_section._row, 0, 1, 2)

        layout.addWidget(general_section)

        # Save button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        save_btn = QPushButton("保存设置")
        save_btn.setProperty("kind", "primary")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._save)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        layout.addStretch(1)
        self.setWidget(container)

    def _save(self):
        api_name = self.api_combo.currentText()
        if api_name in TRANSLATION_APIS:
            self._cfg["translation_api"] = TRANSLATION_APIS[api_name]

        self._cfg["deepl_api_key"] = self.deepl_key.text().strip()
        self._cfg["baidu_appid"] = self.baidu_appid.text().strip()
        self._cfg["baidu_secret"] = self.baidu_secret.text().strip()
        self._cfg["hotkey"] = self.hotkey_entry.text().strip()

        if save_user_config(self._cfg):
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.success("保存成功", "设置已保存", duration=2000,
                          position=InfoBarPosition.TOP, parent=self)
            self.saved.emit()
        else:
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.error("保存失败", "无法写入配置文件", duration=3000,
                        position=InfoBarPosition.TOP, parent=self)

    def get_config(self):
        return self._cfg
