#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断中心：全局错误总线 + 一键生成诊断信息（零依赖）。

以前报错只往状态栏写一行，会被下一条状态冲掉，也无法复制原始异常去求助。
这里在内存里保留最近 200 条事件，UI 侧可随时导出成一段文本：包含版本、
系统、Python、程序路径、关键模块可用性、最近错误。刻意不写入任何密钥、
不落盘用户数据，复制给用户自行决定要不要发出去。
"""
from __future__ import annotations

import collections
import datetime
import importlib.util
import os
import platform
import sys
import threading

_LOCK = threading.Lock()
_EVENTS = collections.deque(maxlen=200)
# 事件版本号：deque 到 200 条会饱和，UI 不能靠 len() 判断「有没有新消息」
_REVISION = 0

CHECKED_MODULES = (
    "PySide6", "qfluentwidgets", "requests", "numpy", "cv2", "PIL",
    "easyocr", "winrt", "faster_whisper", "ctranslate2", "av",
    "sounddevice", "soundcard", "openpyxl", "docx", "pptx", "fitz",
)


def record(kind, message, detail="", source=""):
    """登记一条事件；kind 一般用 info / warning / error。"""
    global _REVISION
    event = {
        'time': datetime.datetime.now().strftime('%H:%M:%S'),
        'kind': str(kind or 'info'),
        'message': str(message or ''),
        'detail': str(detail or '')[:4000],
        'source': str(source or ''),
    }
    with _LOCK:
        _EVENTS.append(event)
        _REVISION += 1
    return event


def revision():
    """当前事件版本号；设置页用它做实时刷新，避免每次都重绘整份报告。"""
    with _LOCK:
        return _REVISION


def recent(limit=40):
    with _LOCK:
        return [dict(e) for e in list(_EVENTS)[-max(1, int(limit)):]]


def clear():
    global _REVISION
    with _LOCK:
        _EVENTS.clear()
        _REVISION += 1


def error_count():
    with _LOCK:
        return sum(1 for e in _EVENTS if e['kind'] == 'error')


def module_status(names=None):
    """只查元数据，不 import（避免把重型依赖拉起来拖慢启动）。"""
    out = {}
    for name in (names or CHECKED_MODULES):
        try:
            out[name] = bool(importlib.util.find_spec(name))
        except (ImportError, ValueError):
            out[name] = False
    return out


def health_issues():
    """配置 / 目录层面的健康提示（供设置页顶部持久横幅使用）。"""
    issues = []
    try:
        from app_paths import APP_DIR, is_frozen, app_dir
        broken = os.path.join(str(APP_DIR), 'user_config.json.broken')
        if os.path.isfile(broken):
            issues.append({
                'kind': 'config_broken',
                'title': '检测到损坏的配置备份',
                'text': ('上次读取用户配置失败，原文件已备份为 '
                         'user_config.json.broken，程序正在使用默认设置。'
                         '可打开目录检查后手动恢复。'),
                'path': str(APP_DIR),
            })
        # app_dir() 是 _resolve_app_dir() 已经解析「可写」之后的目录（不可写
        # 时会降级到 LOCALAPPDATA），对它做 os.access(W_OK) 几乎恒为 True，
        # 横幅永远不会出现；而且 Windows 下 os.access(W_OK) 本就不可靠。
        # 真正的降级信号是「用户数据目录不在 exe 同目录」，与路径模块的实际
        # 决策保持一致。
        if is_frozen():
            from pathlib import Path
            exe_dir = Path(sys.executable).resolve().parent
            if exe_dir != Path(app_dir()).resolve():
                issues.append({
                    'kind': 'readonly',
                    'title': '程序目录不可写',
                    'text': '用户数据已降级存放到其它目录，升级时不会自动迁移。',
                    'path': str(app_dir()),
                })
    except Exception:
        pass
    return issues


def build_report(extra=None):
    """生成一段可复制的诊断文本；不附加任何密钥 / 配置内容。"""
    try:
        from config import APP_VERSION
        version = APP_VERSION
    except Exception:
        version = "未知"
    try:
        from app_paths import APP_DIR
        data_dir = str(APP_DIR)
    except Exception:
        data_dir = "未知"
    lines = [
        "翻译工具 · 诊断信息",
        f"时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"版本：v{version}",
        f"系统：{platform.platform()}",
        f"Python：{platform.python_version()} ({sys.executable})",
        f"冻结运行：{bool(getattr(sys, 'frozen', False))}",
        f"用户数据目录：{data_dir}（本机实际路径，每台电脑可能不同）",
        "",
        "关键模块：",
    ]
    status = module_status()
    lines.append("  " + "、".join(
        f"{name}{'✓' if ok else '✗'}" for name, ok in status.items()))
    if extra:
        lines += ["", "补充说明：", "  " + str(extra)[:500]]
    events = recent(30)
    lines += ["", f"最近事件（共 {len(_EVENTS)} 条，显示最新 {len(events)} 条）："]
    if not events:
        lines.append("  （暂无）")
    for e in events:
        lines.append(
            f"  [{e['time']}] {e['kind'].upper()} "
            f"{e['source'] + ' · ' if e['source'] else ''}{e['message']}")
        if e['detail']:
            lines.append("      " + e['detail'].replace('\n', ' ')[:300])
    return "\n".join(lines)
