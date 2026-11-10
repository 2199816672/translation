#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用内升级的两个对话框：发现新版本 / 下载安装包。

对话框自身不碰网络，进度与结果由主窗口经 Qt 信号送进来；样式都带明确
选择器，避免 Qt 把规则传播给子控件。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QVBoxLayout,
)

from theme import Palette
from updater import ReleaseInfo

UPGRADE = 1
OPEN_PAGE = 2
LATER = 0


def _fmt_size(num: float) -> str:
    if num <= 0:
        return "0 MB"
    mb = num / 1024 / 1024
    return f"{mb:.1f} MB" if mb >= 0.1 else f"{num / 1024:.0f} KB"


def _note_summary(body: str, limit: int = 220) -> str:
    """把 Markdown 发布说明压成两三行短文本，跳过标题 / 空行 / 代码块。"""
    lines = []
    in_code = False
    for raw in str(body or "").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line:
            continue
        if line.startswith(("#", "|", ">")) or not line.strip("-* "):
            continue
        line = line.lstrip("-* ").strip()
        if line.startswith("**") and line.endswith("**"):
            continue
        lines.append(line)
        if len(lines) >= 3 or sum(len(x) for x in lines) >= limit:
            break
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _btn_style(kind: str) -> str:
    p = Palette
    if kind == "primary":
        return (f"QPushButton {{ background: {p.PRIMARY}; color: {p.PRIMARY_TEXT};"
                f" border: 1px solid {p.PRIMARY}; border-radius: 8px; padding: 0 18px;"
                " font-weight: 600; }"
                f"QPushButton:hover {{ background: {p.PRIMARY_HOVER}; }}")
    if kind == "success":
        return ("QPushButton { background: #15803d; color: #fafafa;"
                " border: 1px solid #22c55e; border-radius: 8px; padding: 0 18px;"
                " font-weight: 600; }"
                "QPushButton:hover { background: #16a34a; }")
    return (f"QPushButton {{ background: {p.SURFACE}; color: {p.TEXT};"
            f" border: 1px solid {p.BORDER}; border-radius: 8px; padding: 0 18px; }}"
            f"QPushButton:hover {{ background: {p.SURFACE_HOVER};"
            f" border-color: {p.BORDER_STRONG}; }}")


class _BaseDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        p = Palette
        self.setObjectName("updateDialog")
        self.setFixedWidth(500)
        self.setStyleSheet(f"""
QDialog#updateDialog {{ background-color: {p.BG}; }}
QFrame#card {{ background: {p.SURFACE}; border: 1px solid {p.BORDER};
               border-radius: 12px; }}
QLabel#dlgTitle {{ color: {p.TEXT_STRONG}; font-size: 19px; font-weight: 700; }}
QLabel#dlgStatus {{ color: {p.TEXT}; font-size: 13px; }}
QLabel#dlgError {{ color: {p.LOG_ERROR}; font-size: 13px; }}
QLabel#dlgHint {{ color: {p.ACCENT}; font-size: 12px; }}
QLabel#dlgCaption {{ color: {p.TEXT_MUTED}; font-size: 12px; }}
QLabel#dlgBody {{ color: {p.TEXT}; font-size: 13px; }}
QProgressBar {{ background: {p.BG_INPUT}; border: 1px solid {p.BORDER};
                border-radius: 6px; height: 10px; text-align: center;
                color: {p.TEXT}; }}
QProgressBar::chunk {{ background: {p.ACCENT}; border-radius: 5px; }}
""")

    @staticmethod
    def _card():
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        return card, layout

    @staticmethod
    def _label(text: str, name: str, parent=None) -> QLabel:
        lbl = QLabel(text, parent)
        lbl.setObjectName(name)
        lbl.setWordWrap(True)
        return lbl


class UpdateDialog(_BaseDialog):
    """发现新版本：立即升级 / 打开发布页 / 稍后。"""

    def __init__(self, info: ReleaseInfo, current_version: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("发现新版本")
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        card, inner = self._card()
        inner.addWidget(self._label(f"翻译工具 v{info.version} 已发布", "dlgTitle"))
        size_text = f" · 安装包 {_fmt_size(info.setup_size)}" if info.setup_size else ""
        inner.addWidget(self._label(
            f"当前版本 v{current_version}{size_text}", "dlgCaption"))
        summary = _note_summary(info.body)
        if summary:
            inner.addWidget(self._label(summary, "dlgBody"))
        inner.addWidget(self._label(
            "一键升级会保留配置、密钥、模型与截图，无需先卸载；安装完成后自动重启。",
            "dlgHint"))
        root.addWidget(card)

        if not info.has_installer:
            root.addWidget(self._label(
                "该版本暂未提供一键安装包，请到发布页手动下载。", "dlgCaption"))

        row = QHBoxLayout()
        row.setSpacing(10)
        self.later_btn = QPushButton("稍后")
        self.later_btn.setStyleSheet(_btn_style("ghost"))
        self.later_btn.setMinimumHeight(36)
        self.later_btn.clicked.connect(lambda: self.done(LATER))
        row.addWidget(self.later_btn)

        self.page_btn = QPushButton("查看发布页")
        self.page_btn.setStyleSheet(_btn_style("ghost"))
        self.page_btn.setMinimumHeight(36)
        self.page_btn.clicked.connect(lambda: self.done(OPEN_PAGE))
        row.addWidget(self.page_btn)

        self.upgrade_btn = QPushButton("立即升级" if info.has_installer else "打开发布页")
        self.upgrade_btn.setStyleSheet(_btn_style("primary"))
        self.upgrade_btn.setMinimumHeight(36)
        self.upgrade_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upgrade_btn.clicked.connect(
            lambda: self.done(UPGRADE if info.has_installer else OPEN_PAGE))
        row.addWidget(self.upgrade_btn, 1)
        root.addLayout(row)


class DownloadDialog(_BaseDialog):
    """下载安装包：显示进度、可取消，完成后按钮变成「立即安装并重启」。"""

    def __init__(self, info: ReleaseInfo, cancel: threading.Event, parent=None):
        super().__init__(parent)
        self.setWindowTitle("正在下载更新")
        self.installer_path: Optional[Path] = None
        self._cancel = cancel
        self._ready = False
        self._failed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        card, inner = self._card()
        inner.addWidget(self._label(info.setup_name, "dlgTitle"))
        self.status = self._label("正在连接下载服务器……", "dlgStatus")
        inner.addWidget(self.status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # 未知大小时显示忙碌动画
        self.progress.setTextVisible(False)
        inner.addWidget(self.progress)

        self.detail = self._label("准备中", "dlgCaption")
        inner.addWidget(self.detail)
        inner.addWidget(self._label(
            "下载完成后自动校验 SHA256；安装时程序会退出并自动重启。", "dlgHint"))
        root.addWidget(card)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        self.cancel_btn = QPushButton("暂不安装")
        self.cancel_btn.setStyleSheet(_btn_style("ghost"))
        self.cancel_btn.setMinimumHeight(36)
        self.cancel_btn.setMinimumWidth(120)
        self.cancel_btn.clicked.connect(self.reject)
        self.cancel_btn.hide()
        row.addWidget(self.cancel_btn)

        self.primary_btn = QPushButton("取消下载")
        self.primary_btn.setStyleSheet(_btn_style("ghost"))
        self.primary_btn.setMinimumHeight(36)
        self.primary_btn.setMinimumWidth(150)
        self.primary_btn.setMaximumWidth(230)
        self.primary_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.primary_btn.clicked.connect(self._on_primary)
        row.addWidget(self.primary_btn)
        root.addLayout(row)

    def set_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(done * 100 / total)))
            self.detail.setText(f"{_fmt_size(done)} / {_fmt_size(total)}")
        else:
            self.detail.setText(
                f"已下载 {_fmt_size(done)} · 服务器未提供总大小")
        self.status.setText("正在下载安装包……")

    def set_done(self, path, verified: Optional[bool]) -> None:
        self.installer_path = Path(path)
        self._ready = True
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status.setText(
            "下载完成，SHA256 校验通过" if verified is True
            else "下载完成（发布方未提供校验值，已跳过校验）")
        self.status.setObjectName("dlgStatus")
        self.status.setStyleSheet("")
        self.detail.setText("点击下方按钮开始安装")
        self.primary_btn.setText("立即安装并重启")
        self.primary_btn.setStyleSheet(_btn_style("success"))
        self.cancel_btn.setText("暂不安装")
        self.cancel_btn.show()

    def show_error(self, message: str) -> None:
        self.status.setText(message)
        self.status.setObjectName("dlgError")
        self.status.setStyleSheet(
            f"QLabel#dlgError {{ color: {Palette.LOG_ERROR}; font-size: 13px; }}")
        self.detail.setText("下载未完成，可关闭后重试")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.primary_btn.setText("关闭")
        self.primary_btn.setStyleSheet(_btn_style("ghost"))
        self.cancel_btn.hide()
        self._ready = False
        self._failed = True

    def _on_primary(self) -> None:
        if self._failed:
            self.done(LATER)
        elif self._ready:
            self.done(UPGRADE)
        else:
            self.reject()

    def reject(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
        super().reject()
