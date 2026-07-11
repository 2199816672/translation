#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置页：翻译 API、密钥、快捷键。"""
import json
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from config import (
    CONFIG_FILE, DEFAULT_TRANSLATION_API, TRANSLATION_APIS, FREE_APIS,
    FREE_API_KEY_FIELDS, load_user_config, save_user_config,
)
from widgets import HotKeyInput, FlatCheckBox


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
        self.api_combo.currentTextChanged.connect(self._on_api_changed)

        # API 特定参数 — 用单个容器按需切换
        self._api_container = QWidget()
        self._api_stack = QStackedWidget()
        self._api_container_layout = QVBoxLayout(self._api_container)
        self._api_container_layout.setContentsMargins(0, 0, 0, 0)
        self._api_container_layout.addWidget(self._api_stack)

        # Bing Free 页 — 可选 Azure 密钥
        bing_free_page = QWidget()
        bing_free_layout = QVBoxLayout(bing_free_page)
        bing_free_layout.setContentsMargins(0, 0, 0, 0)
        bing_free_tip = QLabel("可选：填写后使用自己的 Azure Cognitive Services 密钥，否则使用免费逆向接口")
        bing_free_tip.setObjectName("caption")
        bing_free_tip.setWordWrap(True)
        bing_free_layout.addWidget(bing_free_tip)
        bing_free_grid = QGridLayout()
        bing_free_grid.addWidget(QLabel("Azure Key:"), 0, 0,
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.bing_azure_key = QLineEdit()
        self.bing_azure_key.setPlaceholderText("Azure Cognitive Services 订阅密钥（可选）")
        self.bing_azure_key.setText(self._cfg.get("bing_azure_key", ""))
        self.bing_azure_key.setMinimumWidth(300)
        bing_free_grid.addWidget(self.bing_azure_key, 0, 1, Qt.AlignmentFlag.AlignLeft)
        bing_free_grid.addWidget(QLabel("Region:"), 1, 0,
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.bing_azure_region = QLineEdit()
        self.bing_azure_region.setPlaceholderText("区域，如 chinaeast2（可选，国内用户可不填）")
        self.bing_azure_region.setText(self._cfg.get("bing_azure_region", ""))
        self.bing_azure_region.setMinimumWidth(300)
        bing_free_grid.addWidget(self.bing_azure_region, 1, 1, Qt.AlignmentFlag.AlignLeft)
        bing_free_layout.addLayout(bing_free_grid)
        self._api_stack.addWidget(bing_free_page)

        # MyMemory Free 页 — 可选 API key
        mymemory_free_page = QWidget()
        mymemory_free_layout = QVBoxLayout(mymemory_free_page)
        mymemory_free_layout.setContentsMargins(0, 0, 0, 0)
        mymemory_free_tip = QLabel("可选：填写后提升每日请求额度（无密钥也可正常使用）")
        mymemory_free_tip.setObjectName("caption")
        mymemory_free_tip.setWordWrap(True)
        mymemory_free_layout.addWidget(mymemory_free_tip)
        mymemory_free_grid = QGridLayout()
        mymemory_free_grid.addWidget(QLabel("MyMemory Key:"), 0, 0,
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.mymemory_api_key = QLineEdit()
        self.mymemory_api_key.setPlaceholderText("mymemory.translated.net 授权密钥（可选）")
        self.mymemory_api_key.setText(self._cfg.get("mymemory_api_key", ""))
        self.mymemory_api_key.setMinimumWidth(300)
        mymemory_free_grid.addWidget(self.mymemory_api_key, 0, 1, Qt.AlignmentFlag.AlignLeft)
        mymemory_free_layout.addLayout(mymemory_free_grid)
        self._api_stack.addWidget(mymemory_free_page)

        # Google Free 页 — 可选 Cloud API key
        google_free_page = QWidget()
        google_free_layout = QVBoxLayout(google_free_page)
        google_free_layout.setContentsMargins(0, 0, 0, 0)
        google_free_tip = QLabel(
            "可选：填写后使用 Google Cloud Translation API，否则使用免费接口（需 VPN 访问 Google）"
        )
        google_free_tip.setObjectName("caption")
        google_free_tip.setWordWrap(True)
        google_free_layout.addWidget(google_free_tip)
        google_free_grid = QGridLayout()
        google_free_grid.addWidget(QLabel("Google Cloud Key:"), 0, 0,
                                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.google_cloud_key = QLineEdit()
        self.google_cloud_key.setPlaceholderText("Google Cloud Translation API 密钥（可选）")
        self.google_cloud_key.setText(self._cfg.get("google_cloud_key", ""))
        self.google_cloud_key.setMinimumWidth(300)
        google_free_grid.addWidget(self.google_cloud_key, 0, 1, Qt.AlignmentFlag.AlignLeft)
        google_free_layout.addLayout(google_free_grid)
        self._api_stack.addWidget(google_free_page)

        # DeepL 页
        dl_page = QWidget()
        dl_layout = QHBoxLayout(dl_page)
        dl_layout.setContentsMargins(0, 0, 0, 0)
        dl_layout.addWidget(QLabel("DeepL Key:"))
        self.deepl_key = QLineEdit()
        self.deepl_key.setPlaceholderText("DeepL API 密钥 (deepl.com/pro-api)")
        self.deepl_key.setText(self._cfg.get("deepl_api_key", ""))
        self.deepl_key.setMinimumWidth(300)
        dl_layout.addWidget(self.deepl_key)
        dl_layout.addStretch(1)
        self._api_stack.addWidget(dl_page)

        # 百度页
        baidu_page = QWidget()
        baidu_layout = QGridLayout(baidu_page)
        baidu_layout.setContentsMargins(0, 0, 0, 0)
        baidu_layout.addWidget(QLabel("百度 AppID:"), 0, 0,
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.baidu_appid = QLineEdit()
        self.baidu_appid.setPlaceholderText("百度翻译开放平台 AppID")
        self.baidu_appid.setText(self._cfg.get("baidu_appid", ""))
        self.baidu_appid.setMinimumWidth(300)
        baidu_layout.addWidget(self.baidu_appid, 0, 1, Qt.AlignmentFlag.AlignLeft)
        baidu_layout.addWidget(QLabel("百度 Secret:"), 1, 0,
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.baidu_secret = QLineEdit()
        self.baidu_secret.setPlaceholderText("百度翻译 SecretKey")
        self.baidu_secret.setText(self._cfg.get("baidu_secret", ""))
        self.baidu_secret.setMinimumWidth(300)
        baidu_layout.addWidget(self.baidu_secret, 1, 1, Qt.AlignmentFlag.AlignLeft)
        self._api_stack.addWidget(baidu_page)

        # 腾讯翻译页
        tencent_page = QWidget()
        tencent_layout = QGridLayout(tencent_page)
        tencent_layout.setContentsMargins(0, 0, 0, 0)
        tencent_layout.addWidget(QLabel("腾讯 SecretId:"), 0, 0,
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.tencent_secret_id = QLineEdit()
        self.tencent_secret_id.setPlaceholderText("腾讯云 API SecretId")
        self.tencent_secret_id.setText(self._cfg.get("tencent_secret_id", ""))
        self.tencent_secret_id.setMinimumWidth(300)
        tencent_layout.addWidget(self.tencent_secret_id, 0, 1, Qt.AlignmentFlag.AlignLeft)
        tencent_layout.addWidget(QLabel("腾讯 SecretKey:"), 1, 0,
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.tencent_secret_key = QLineEdit()
        self.tencent_secret_key.setPlaceholderText("腾讯云 API SecretKey")
        self.tencent_secret_key.setText(self._cfg.get("tencent_secret_key", ""))
        self.tencent_secret_key.setMinimumWidth(300)
        tencent_layout.addWidget(self.tencent_secret_key, 1, 1, Qt.AlignmentFlag.AlignLeft)
        self._api_stack.addWidget(tencent_page)

        # 火山翻译页
        volc_page = QWidget()
        volc_layout = QGridLayout(volc_page)
        volc_layout.setContentsMargins(0, 0, 0, 0)
        volc_layout.addWidget(QLabel("火山 AppID:"), 0, 0,
                              Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.volcengine_appid = QLineEdit()
        self.volcengine_appid.setPlaceholderText("火山翻译应用 AppID")
        self.volcengine_appid.setText(self._cfg.get("volcengine_app_id", ""))
        self.volcengine_appid.setMinimumWidth(300)
        volc_layout.addWidget(self.volcengine_appid, 0, 1, Qt.AlignmentFlag.AlignLeft)
        volc_layout.addWidget(QLabel("火山 Token:"), 1, 0,
                              Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.volcengine_token = QLineEdit()
        self.volcengine_token.setPlaceholderText("火山翻译 Access Token")
        self.volcengine_token.setText(self._cfg.get("volcengine_token", ""))
        self.volcengine_token.setMinimumWidth(300)
        volc_layout.addWidget(self.volcengine_token, 1, 1, Qt.AlignmentFlag.AlignLeft)
        self._api_stack.addWidget(volc_page)

        self._api_page_map = {
            "bing_free": 0, "mymemory_free": 1, "google_free": 2,
            "deepl": 3, "baidu": 4, "tencent": 5, "volcengine": 6,
        }
        self._api_container.setVisible(False)
        self._on_api_changed(self.api_combo.currentText())

        layout.addWidget(trans_section)
        layout.addWidget(self._api_container)

        # ---- 常规设置 ----
        general_section = _Section("常规设置")

        self.hotkey_entry = HotKeyInput(self._cfg.get("hotkey", "ctrl+shift+a"))
        self.hotkey_entry.setMinimumWidth(200)
        general_section.add("截图快捷键:", self.hotkey_entry,
                           tip="点击后按下你想设置的快捷键组合")

        self.minimize_to_tray_check = FlatCheckBox("关闭窗口时最小化到系统托盘")
        self.minimize_to_tray_check.setChecked(self._cfg.get("minimize_to_tray", True))
        general_section.add("关闭行为:", self.minimize_to_tray_check,
                           tip="点关闭按钮时隐藏到托盘，托盘右键可退出")

        self.autostart_check = FlatCheckBox("开机自动启动")
        try:
            from main_window import _get_autostart
            self.autostart_check.setChecked(_get_autostart())
        except Exception:
            self.autostart_check.setChecked(False)
        general_section.add("自启动:", self.autostart_check,
                           tip="开机后自动启动并最小化到托盘")

        info = QLabel(
            "免费翻译（可选填自己的 API 密钥）:\n"
            "  Bing 微软 — 国内可用，可填 Azure 密钥\n"
            "  MyMemory — 全球可用，可填授权密钥提升额度\n"
            "  Google — 需要 VPN，可填 Google Cloud 密钥\n\n"
            "付费翻译（需自备密钥）:\n"
            "  DeepL — deepl.com/pro-api 申请\n"
            "  百度翻译 — fanyi-api.com 申请\n"
            "  腾讯翻译 — console.cloud.tencent.com 申请\n"
            "  火山翻译 — console.volcengine.com 申请"
        )
        info.setObjectName("caption")
        info.setWordWrap(True)
        general_section.grid.addWidget(info, general_section._row, 0, 1, 2)

        layout.addWidget(general_section)

        # ---- 自动操作 ----
        copy_section = _Section("自动操作")

        self.auto_translate_check = FlatCheckBox("截图选区后自动翻译")
        self.auto_translate_check.setChecked(self._cfg.get("auto_translate", False))
        copy_section.add("自动翻译:", self.auto_translate_check,
                         tip="选区完成后直接翻译，点击译文可切换回原文")

        self.auto_copy_check = FlatCheckBox("截图识别后自动复制到剪贴板")
        self.auto_copy_check.setChecked(self._cfg.get("auto_copy", False))
        copy_section.add("自动复制:", self.auto_copy_check,
                         tip="开启后截图识别完成自动复制文本")

        self.auto_copy_combo = QComboBox()
        self.auto_copy_combo.addItems(["原文", "译文"])
        current_target = self._cfg.get("auto_copy_target", "source")
        self.auto_copy_combo.setCurrentText("原文" if current_target == "source" else "译文")
        self.auto_copy_combo.setMinimumWidth(120)
        copy_section.add("复制内容:", self.auto_copy_combo,
                         tip="选择自动复制原文还是译文")

        layout.addWidget(copy_section)

        # Save button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        self._save_btn = QPushButton("保存设置")
        self._save_btn.setProperty("kind", "primary")
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._save)
        btn_layout.addWidget(self._save_btn)
        layout.addLayout(btn_layout)

        layout.addStretch(1)
        self.setWidget(container)

    def _on_api_changed(self, api_name):
        api_value = TRANSLATION_APIS.get(api_name, "")
        if api_value in self._api_page_map:
            self._api_stack.setCurrentIndex(self._api_page_map[api_value])
            self._api_container.setVisible(True)
        else:
            self._api_container.setVisible(False)

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
        self._cfg["hotkey"] = self.hotkey_entry.value()
        self._cfg["auto_translate"] = self.auto_translate_check.isChecked()
        self._cfg["auto_copy"] = self.auto_copy_check.isChecked()
        self._cfg["auto_copy_target"] = "source" if self.auto_copy_combo.currentText() == "原文" else "translation"
        self._cfg["minimize_to_tray"] = self.minimize_to_tray_check.isChecked()

        # 开机自启动
        try:
            from main_window import _set_autostart
            _set_autostart(self.autostart_check.isChecked())
        except Exception:
            pass

        # 密钥变更时清除翻译器缓存，使新密钥立即生效
        try:
            from free_translator import invalidate_free_translator
            from translator import _translator_instance
            invalidate_free_translator("bing_free")
            invalidate_free_translator("mymemory_free")
            invalidate_free_translator("google_free")
            import translator as _mod
            _mod._translator_instance = None
        except Exception:
            pass

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
