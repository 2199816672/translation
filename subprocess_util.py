#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""子进程调用统一封装：解决 --windowed 打包后的黑框 / 编码 / pip 误启动。

* 所有子进程在 Windows 下带 CREATE_NO_WINDOW，避免点按钮闪出黑色控制台；
* text=True 统一用 UTF-8 + errors=replace，中文 pip / nvidia-smi 输出不会
  UnicodeDecodeError；
* 冻结（PyInstaller）后 ``sys.executable`` 是应用本体，绝不能拿它跑
  ``-m pip``——参数会被程序自己吞掉，甚至二次启动应用。这里改用同一个 exe
  的 ``--tt-pip-worker`` 子进程模式（见 main.py），既不需要用户自己装
  Python，也保证装进来的 C 扩展与主程序是同一套 Python ABI。
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import threading

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


# 在线装 EasyOCR 会连带下载 torch 等大依赖，留足时间；但绝不允许无限挂起。
PIP_TIMEOUT = 900


@contextlib.contextmanager
def pip_process(cmd, timeout=PIP_TIMEOUT, **kwargs):
    """启动 pip 并提供带超时的流式输出读取。

    Windows 下管道不支持 select，调用方阻塞在 ``proc.stdout.readline()`` 时
    光靠 ``finally`` 里的 terminate 是救不回来的。这里用看门狗在超时后
    ``kill`` 子进程：管道随之关闭，readline() 返回空串，读取循环自然退出。
    正常结束时由 ``finally`` 统一收回管道并等待子进程。
    """
    proc = popen_quiet(cmd, **kwargs)
    done = threading.Event()

    def guard():
        if not done.wait(timeout) and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    watcher = threading.Thread(target=guard, daemon=True)
    watcher.start()
    try:
        yield proc
    finally:
        done.set()
        stream = getattr(proc, "stdout", None)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# 冻结态专用：让应用本体以「pip worker」模式启动（见 main.py）。
PIP_WORKER_FLAG = "--tt-pip-worker"


def pip_cmd(packages, extra_args=()):
    """构造 ``pip install`` 命令，返回 ``(cmd, hint)``；hint 非空表示无法安装。

    * 源码版：``python -m pip install ...``，沿用用户自己的环境；
    * 安装版：``TranslationTool.exe --tt-pip-worker install ...``，并统一
      ``--target`` 到用户数据目录 :func:`app_paths.user_libs_dir`。该目录被
      安装程序排除在清单外，覆盖升级不会删除，因此组件只需装一次。

    绝不退回「找系统 python 来跑 pip」：系统解释器的版本/位数可能与程序内置
    解释器不同，装出来的 numpy / torch / cv2 等扩展在本程序里根本 import 不了。
    """
    args = [str(p) for p in packages]
    if is_frozen():
        # 必须用启动时就固定下来的目录：pip_cmd 每次重算 user_libs_dir() 时，
        # 若磁盘/权限状态变化可能降级到另一目录，造成「装到 A、sys.path 指 B」。
        from app_paths import USER_LIBS_DIR
        # 刻意不加 --upgrade：所有组件共用一个 --target，且调用方会做多镜像
        # 重试；pip 的 --target --upgrade 会替换目标目录里已有的包文件，可能
        # 把先装好的其它组件/依赖弄成半套状态。首次安装本就无需 upgrade。
        cmd = [sys.executable, PIP_WORKER_FLAG, "install",
               "--disable-pip-version-check",
               "--no-warn-script-location",  # --target 会生成脚本目录，不必警告
               "--target", str(USER_LIBS_DIR)]
        return cmd + list(extra_args) + args, ""
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
