#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ctrl+K 命令面板：把散在托盘 / 浮层 / 快捷键里的动作汇总到一个可搜索入口。

动作清单本身是零依赖的纯函数，UI 在函数内部按需创建；没有 PySide6 的
环境也能验证清单完整性和快捷键展示。
"""
from __future__ import annotations

# 没有全局热键、只存在于托盘 / 菜单里的动作
EXTRA_ACTIONS = (
    ('show_window', '显示主窗口', '把主窗口从托盘恢复出来'),
    ('caption_start', '实时字幕 / 显示字幕框', '启动听写并显示字幕框'),
    ('caption_hide', '隐藏字幕框（继续识别）', '只隐藏字幕框，识别继续'),
    ('caption_pause', '暂停 / 继续字幕', '暂停或恢复实时字幕'),
    ('caption_stop', '停止实时字幕', '彻底停止识别'),
    ('caption_clear', '清空当前字幕', '清空字幕框与主页残留文本'),
    ('live_stop', '停止实时翻译', '关闭屏幕实时翻译浮窗'),
    ('check_update', '检查更新', '在线检查是否有新版本'),
    ('open_settings', '打开设置', '切换到设置页'),
    ('copy_screenshot', '复制截图', '把预览图放进剪贴板'),
    ('diagnostics', '复制诊断信息', '导出版本、模块与最近错误'),
)


def collect_actions(cfg=None):
    """返回 ``[{key, label, desc, hotkey, scope}]``，供面板与测试共用。"""
    from hotkeys import (HOTKEY_ACTIONS, LOCAL_HOTKEY_ACTIONS,
                         display_hotkey, get_hotkeys, get_local_hotkeys)
    global_cfg = get_hotkeys(cfg) or {}
    local_cfg = get_local_hotkeys(cfg) or {}
    items = []
    for key, entry in HOTKEY_ACTIONS.items():
        label = entry[0]
        desc = entry[2] if len(entry) > 2 else ''
        items.append({
            'key': key, 'label': label, 'desc': desc,
            'hotkey': display_hotkey(global_cfg.get(key, '')),
            'scope': '全局',
        })
    for key, entry in LOCAL_HOTKEY_ACTIONS.items():
        label = entry[0]
        desc = entry[2] if len(entry) > 2 else ''
        items.append({
            'key': key, 'label': label, 'desc': desc,
            'hotkey': display_hotkey(local_cfg.get(key, '')),
            'scope': '窗口内',
        })
    for key, label, desc in EXTRA_ACTIONS:
        items.append({
            'key': key, 'label': label, 'desc': desc,
            'hotkey': '', 'scope': '菜单',
        })
    return items


def search(items, keyword):
    kw = str(keyword or '').strip().lower()
    if not kw:
        return list(items)
    return [item for item in items
            if kw in ' '.join((item['label'], item['desc'], item['hotkey'],
                               item['scope'], item['key'])).lower()]


def open_command_palette(parent=None, handlers=None):
    """弹出命令面板；返回被执行动作的 key（未执行则 None）。"""
    handlers = handlers or {}
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLineEdit,
                                  QListWidget, QListWidgetItem, QLabel)

    items = [item for item in collect_actions()
             if item['key'] in handlers]
    dlg = QDialog(parent)
    dlg.setWindowTitle("命令面板 · Ctrl+K")
    dlg.setMinimumWidth(560)
    dlg.setMinimumHeight(420)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)

    edit = QLineEdit(dlg)
    edit.setPlaceholderText("输入动作名称或快捷键搜索，回车执行，Esc 关闭")
    lay.addWidget(edit)
    hint = QLabel("↑↓ 选择 · 回车执行 · 双击执行", dlg)
    lay.addWidget(hint)
    listing = QListWidget(dlg)
    lay.addWidget(listing, 1)

    result = {'key': None}
    current = []

    def refresh(keyword):
        nonlocal current
        current = search(items, keyword)
        listing.clear()
        for item in current:
            hot = f"   [{item['hotkey']}]" if item['hotkey'] else ''
            text = f"{item['label']}{hot}  ·  {item['scope']}"
            row = QListWidgetItem(text)
            row.setToolTip(item['desc'])
            listing.addItem(row)
        if current:
            listing.setCurrentRow(0)

    def run_current():
        row = listing.currentRow()
        if not (0 <= row < len(current)):
            return
        result['key'] = current[row]['key']
        dlg.accept()

    edit.textChanged.connect(refresh)
    edit.returnPressed.connect(run_current)
    listing.itemActivated.connect(lambda _item: run_current())
    refresh('')
    edit.setFocus()
    dlg.exec()

    key = result['key']
    if key and key in handlers:
        try:
            handlers[key]()
        except Exception as exc:
            import diagnostics
            diagnostics.record('error', f"命令执行失败：{key}", str(exc),
                               '命令面板')
    return key
