#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次启动的一次性 3 步引导。

只在「全新安装、还没有任何使用痕迹」时显示一次，老用户升级不会被打扰。
刻意不预置全局热键占用系统按键：推荐组合由用户主动点按钮启用，且明确
写着可以随时在设置里改，避免和其它软件撞车。
"""
from __future__ import annotations

WELCOME_STEPS = (
    ("① 开箱即用，不用先配置",
     "默认用 Windows 自带 OCR + 免费翻译引擎，无需填密钥、无需下载模型。"
     "点主页「区域截图」，框选屏幕上的文字就能识别并翻译。"),
    ("② 不止截图",
     "左侧还有「全屏截图 / 实时翻译 / 实时字幕 / 音视频 / 文档处理」。"
     "托盘右键图标或全局热键可以不打开主窗口直接截图；"
     "截图即使点了取消，也会保留在预览里，可随时「复制截图」。"),
    ("③ 几乎所有习惯都能改",
     "「设置 → 快捷键」可改全局热键和主页 Ctrl+Enter，并会自动检查冲突；"
     "翻译引擎、字幕音源、OCR 模型、保存形式也都在这里。"),
)

# 推荐但不擅自占用的组合（用户明确点「启用」才写入配置）
# 键是 user_config 里的配置键（hotkeys.get_hotkeys 读的就是它），不是动作名
RECOMMENDED_HOTKEY = {'hotkey': 'ctrl+alt+q'}
RECOMMENDED_LABEL = "Ctrl + Alt + Q（区域截图）"

_LEGACY_KEYS = (
    'tray_hint_shown', 'ocr_engine', 'translation_api',
    'home_target_lang', 'hotkey', 'hotkeys_v2_migrated',
)


def should_show(cfg=None):
    """全新安装才显示；老用户配置里已有痕迹就直接跳过。"""
    if cfg is None:
        from config import load_user_config
        cfg = load_user_config()
    if not isinstance(cfg, dict):
        return True
    if cfg.get('first_run_shown'):
        return False
    return not any(key in cfg for key in _LEGACY_KEYS)


def mark_shown():
    from config import save_user_config
    save_user_config({'first_run_shown': True})


def show_welcome(parent=None, on_enable_hotkey=None, on_open_settings=None):
    """显示引导，返回是否真的显示过。PySide6 在函数内导入，便于零依赖测试。"""
    if not should_show():
        return False
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                  QLabel, QPushButton, QCheckBox)

    dlg = QDialog(parent)
    dlg.setWindowTitle("欢迎使用翻译工具 · 只需看一次")
    dlg.setMinimumWidth(520)
    dlg.setModal(True)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(26, 22, 26, 20)
    lay.setSpacing(14)

    head = QLabel("三步上手", dlg)
    head.setStyleSheet("font-size: 20px; font-weight: 600;")
    lay.addWidget(head)
    sub = QLabel("这个提示只会出现这一次；随时可在设置里找到下面的功能。", dlg)
    sub.setWordWrap(True)
    lay.addWidget(sub)

    for title, body in WELCOME_STEPS:
        block = QLabel(f"<b>{title}</b><br>{body}", dlg)
        block.setWordWrap(True)
        block.setTextFormat(Qt.TextFormat.RichText)
        block.setStyleSheet(
            "padding: 10px 12px; border: 1px solid #3f3f46;"
            "border-radius: 8px;")
        lay.addWidget(block)

    enable = QCheckBox(f"同时启用推荐截图热键 {RECOMMENDED_LABEL}", dlg)
    enable.setToolTip(
        "默认不启用任何全局热键，避免和其它软件冲突。"
        "启用后可在「设置 → 快捷键」随时改成别的组合，也能清空。")
    lay.addWidget(enable)

    row = QHBoxLayout()
    row.addStretch(1)
    settings_btn = QPushButton("打开设置", dlg)
    start_btn = QPushButton("开始使用", dlg)
    start_btn.setDefault(True)
    row.addWidget(settings_btn)
    row.addWidget(start_btn)
    lay.addLayout(row)

    def open_settings():
        mark_shown()
        dlg.accept()
        if on_open_settings is not None:
            try:
                on_open_settings()
            except Exception:
                pass

    def start():
        mark_shown()
        if enable.isChecked() and on_enable_hotkey is not None:
            try:
                on_enable_hotkey(dict(RECOMMENDED_HOTKEY))
            except Exception:
                pass
        dlg.accept()

    settings_btn.clicked.connect(open_settings)
    start_btn.clicked.connect(start)
    dlg.exec()
    # Esc / 标题栏 X 走 reject()，上面两个按钮不会触发；这里无条件标记，
    # 否则「关掉引导」反而导致每次启动都再弹一次。
    mark_shown()
    return True
