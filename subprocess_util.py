#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""子进程调用统一封装：解决 --windowed 打包后的黑框 / 编码 / pip 误启动。

* 所有子进程在 Windows 下带 CREATE_NO_WINDOW，避免点按钮闪出黑色控制台；
* text=True 统一用 UTF-8 + errors=replace，中文 pip / nvidia-smi 输出不会
  UnicodeDecodeError；
* 冻结（PyInstaller）后 ``sys.executable`` 是应用本体，绝不能拿它跑
  ``-m pip``——参数会被程序自己吞掉，甚至二次启动应用。这里对冻结态直接
  给出可操作的错误提示。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from app_paths import is_frozen

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def no_window_flags() -> int:
    return _NO_WINDOW


def _text_kwargs(kwargs: dict) -> dict:
    if kwargs.get("text") or kwargs.get("universal_newlines"):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    if sys.platform == "win32":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | _NO_WINDOW
    return kwargs


def run_quiet(cmd, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **_text_kwargs(kwargs))


def popen_quiet(cmd, **kwargs) -> subprocess.Popen:
    return subprocess.Popen(cmd, **_text_kwargs(kwargs))


def check_call_quiet(cmd, **kwargs) -> int:
    return subprocess.check_call(cmd, **_text_kwargs(kwargs))


FROZEN_PIP_HINT = (
    "当前是安装版，无法在线安装可选组件。\n"
    "安装包已内置常用功能；若确需该组件，请使用源码版运行，或下载带该组件"
    "的完整安装包。"
)


def pip_cmd(packages, extra_args=()):
    """构造 pip 命令；冻结态返回 (None, 提示文本)，调用方须直接返回失败。"""
    args = [str(p) for p in packages]
    if is_frozen():
        return None, FROZEN_PIP_HINT
    return [sys.executable, "-m", "pip", "install"] + list(extra_args) + args, ""


def python_launcher() -> str:
    """供未来需要调用外部脚本时使用的解释器路径（非冻结态即自身）。"""
    if not is_frozen():
        return sys.executable
    for name in ("python", "py"):
        found = shutil.which(name)
        if found:
            return found
    return os.environ.get("PYTHON", "python")
