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
import re
import shutil
import subprocess
import sys
import threading
import time

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

# 用户主动取消时，各安装函数统一返回这条信息，界面据此显示「已取消」
# 而不是「安装失败」。
CANCELLED_TEXT = "已取消安装"


@contextlib.contextmanager
def pip_process(cmd, timeout=PIP_TIMEOUT, cancel=None, **kwargs):
    """启动 pip 并提供带超时、可中途取消的流式输出读取。

    Windows 下管道不支持 select，调用方阻塞在 ``proc.stdout.readline()`` 时
    光靠 ``finally`` 里的 terminate 是救不回来的。这里用看门狗每 0.2 秒检查
    取消标志 / 超时，必要时 ``kill`` 子进程：管道随之关闭，readline() 返回
    空串，读取循环自然退出。正常结束时由 ``finally`` 统一收回管道并等待。
    """
    proc = popen_quiet(cmd, **kwargs)
    done = threading.Event()

    def guard():
        deadline = time.monotonic() + max(1, timeout)
        while not done.wait(0.2):
            if proc.poll() is not None:
                return
            cancelled = cancel is not None and cancel.is_set()
            if cancelled or time.monotonic() >= deadline:
                try:
                    proc.kill()
                except OSError:
                    pass
                return

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


# 显式 ``-i`` 能覆盖用户 pip.ini 的 index-url，但「系统默认源」那一跳会
# 完整读回本机配置：实测不少机器把全局源设成清华，而清华
# /simple/easyocr/ 现在直接 403，于是用户看到的「最后一次错误输出」全是
# 失效镜像，还以为程序一直在用坏源。这里只列明确、实测可用的镜像，
# 全部带 ``-i``，并由 :func:`pip_env` 隔离本机失效的 pip 配置。
# 腾讯云同步快、国内稳定（实测 /simple/easyocr/ 200）为首选；PyPI 官方
# 兜底；华为云镜像同为国内可达（实测 200），作为第三个独立兜底。
# 阿里云与清华镜像均已从全部安装链路移除。
PIP_INDEXES = (
    ("腾讯云镜像", ("-i", "https://mirrors.cloud.tencent.com/pypi/simple/")),
    ("PyPI 官方", ("-i", "https://pypi.org/simple/")),
    ("华为云镜像", ("-i", "https://repo.huaweicloud.com/repository/pypi/simple/")),
)


def pip_env():
    """给 pip 子进程用的环境变量：隔离本机失效的 pip.ini / pip.conf。

    ``-i`` 只覆盖 index-url，配置里的 extra-index-url / no-index 仍会把失效
    源掺进解析；用户自己配的 PIP_INDEX_URL 环境变量也可能捣乱。这里把配置
    文件指向空设备、清掉会改源的几个变量，但**保留**代理 / 证书 / 缓存等
    环境变量，需要走代理的用户不受影响。
    """
    env = os.environ.copy()
    env["PIP_CONFIG_FILE"] = os.devnull
    for key in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
                "PIP_NO_INDEX", "PIP_FIND_LINKS"):
        env.pop(key, None)
    return env


# pip 把 Python traceback 原样打到日志里，最后那行异常最具体，例如
#   pip._vendor.distlib.DistlibException: Unable to locate finder for ...
# 而 pip 自己的汇总行只是没信息量的 "ERROR: Exception:"。先挑异常行，
# 否则「各源失败摘要」永远是同一句空话，用户和开发者都看不出真因。
_TRACEBACK_EXC = re.compile(r"^[\w.]+(?:Error|Exception):\s+\S")


def _failure_line(tail):
    """从 pip 尾部日志里挑最有信息的一行，别把进度条当失败原因。"""
    lines = [str(x).strip() for x in (tail or []) if str(x).strip()]
    for line in reversed(lines):
        if _TRACEBACK_EXC.match(line):
            return line[:180]
    for line in reversed(lines):
        low = line.lower()
        if ('error' in low or 'failed' in low or '403' in low
                or 'timed out' in low or 'no matching' in low):
            return line[:180]
    return (lines[-1][:180] if lines else "无输出")


def pip_install_with_mirrors(packages, on_progress=None, cancel=None,
                             on_attempt=None, extra_args=None,
                             label="组件", indexes=None,
                             timeout=PIP_TIMEOUT):
    """依次用多个 pip 源安装组件，返回 ``(ok, message)``。

    * 每个源都显式带 ``-i``，并通过 ``pip_env()`` 忽略本机失效镜像配置；
    * ``--retries/--timeout`` 放宽到能下完 torch 这类大依赖，也不会干等；
    * ``cancel`` 置位立即收手，不会再尝试下一个源；
    * 全部失败时给出「每个源各自为什么失败」的摘要，而不是只把最后一次
      （常常来自用户 pip.ini 的 403）甩给用户；UI 把完整日志放 tooltip。
    """
    import subprocess
    if cancel is not None and cancel.is_set():
        return False, CANCELLED_TEXT
    packages = [str(p) for p in packages]
    probe, hint = pip_cmd(packages)
    if probe is None:                    # pip 不可用时不做无谓的多源重试
        return False, hint
    indexes = tuple(indexes if indexes is not None else PIP_INDEXES)
    base_args = list(extra_args) if extra_args else [
        '--retries', '3', '--timeout', '60']
    last = ""
    failures = []
    env = pip_env()
    total = len(indexes)
    for i, (name, extra) in enumerate(indexes, 1):
        if cancel is not None and cancel.is_set():
            return False, CANCELLED_TEXT
        if on_attempt is not None:
            try:
                on_attempt(i, total, name)
            except Exception:
                pass
        cmd, hint = pip_cmd(packages, base_args + list(extra))
        if cmd is None:
            return False, hint
        try:
            with pip_process(cmd, cancel=cancel, timeout=timeout,
                             env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True) as proc:
                tail = []
                while True:
                    line = proc.stdout.readline()
                    if not line and proc.poll() is not None:
                        break
                    if line:
                        tail.append(line.rstrip())
                        if on_progress is not None:
                            try:
                                on_progress(line)
                            except Exception:
                                pass
            last = '\n'.join(tail[-8:])
            # 已经装成功就如实报成功：用户在收尾瞬间点了取消，不能把装好
            # 的组件显示成「已取消安装」（设置页也有同样的守卫）。
            if proc.returncode == 0:
                return True, f"{label}安装成功（{name}）"
            failures.append(f"  {name}：{_failure_line(tail)}")
            if cancel is not None and cancel.is_set():
                return False, CANCELLED_TEXT
        except Exception as exc:
            last = f"执行失败: {exc}"
            failures.append(f"  {name}：{last}")
    tried = " / ".join(name for name, _ in indexes)
    manual = " ".join(packages)
    joined = "\n".join(failures).lower()
    if "unable to locate finder" in joined or "distlibexception" in joined:
        # 冻结版 pip 的 distlib 启动器异常：三个源会报完全相同的内部错误，
        # 旧文案让用户一直「检查网络 / 重试安装」，永远好不了。
        diagnosis = (
            "⚠ 各源报的是同一个安装程序内部错误（pip 生成命令行启动器失败），"
            "与网络、镜像、pip.ini 都无关，反复「重试安装」不会变好。"
            "请更新到修复版安装包；当前可先用「Windows 系统自带」OCR。\n")
    elif (total > 1 and
          len({f.split("：", 1)[-1].strip() for f in failures}) == 1):
        diagnosis = (
            "⚠ 各源报错完全相同，通常不是镜像或网络问题，"
            "请把下面的错误输出反馈给开发者。\n")
    else:
        diagnosis = ("本机 pip 全局配置已自动忽略，不需要手动改 pip.ini；"
                     "请点「重试安装」再试一次。\n")
    return False, (
        f"安装失败（已依次尝试：{tried}，共 {total} 个源均失败）。\n"
        + diagnosis
        + "安装版请勿用系统 Python 的 pip 手动安装：组件会装到别处，程序读不到。\n"
        "源码版可在命令行手动执行：\n"
        f"  pip install {manual} "
        "-i https://mirrors.cloud.tencent.com/pypi/simple/\n"
        f"  pip install {manual} -i https://pypi.org/simple/\n"
        "装不上也不影响主流程：OCR 可先用「Windows 系统自带」方案（免下载模型，\n"
        "若提示缺 winrt 组件再点「安装 winrt」），其它组件稍后重试。\n\n"
        "各源失败摘要：\n" + "\n".join(failures) + "\n\n"
        f"最后一次错误输出：\n{last}")


def python_launcher() -> str:
    """供未来需要调用外部脚本时使用的解释器路径（非冻结态即自身）。"""
    if not is_frozen():
        return sys.executable
    for name in ("python", "py"):
        found = shutil.which(name)
        if found:
            return found
    return os.environ.get("PYTHON", "python")
