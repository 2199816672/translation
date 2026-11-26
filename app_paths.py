#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径解析：源码运行 / PyInstaller 冻结 / 只读安装目录，三种情况统一。

PyInstaller 打包后 ``__file__`` 指向解压出来的 ``_internal`` 目录，用户配置、
截图、模型如果按 ``__file__`` 定位会写进程序内部（升级时被整个替换掉，甚至
在 Program Files 下直接 PermissionError）。这里把两类路径彻底分开：

* :func:`resource_path` —— 只读资源（图标 / svg / 内置数据），冻结后从
  ``sys._MEIPASS`` 读；
* :func:`app_dir` —— 可写的用户数据根目录（user_config.json / screenshots /
  models），冻结后落在 exe 同目录；安装到只读目录时自动降级到
  ``%LOCALAPPDATA%\\TranslationTool``。

安装程序（installer.iss）会把这些运行时数据排除在安装清单之外，因此覆盖
升级既不会覆盖用户配置、也不会删掉已安装的可选组件（user_libs），升级后
无需重新安装组件。
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

APP_DATA_NAME = "TranslationTool"

# 运行时生成的用户数据。installer.iss / build_installer.py 也各有一份同样的
# 名单用于排除，改动时记得同步。
RUNTIME_DATA = (
    "user_config.json", "user_config.json.tmp", "user_config.json.broken",
    "screenshots", "models", "converted", "user_libs", "cache",
)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _is_writable(directory: Path) -> bool:
    """Windows 下 os.access(W_OK) 不可靠，实际写一个探针文件才准确。

    只要「写得进去」就可判定可写；清理探针失败（杀软占用、受限环境的删除
    守卫等）不该反过来把目录判成不可写，更不能让异常冒到业务调用里。
    """
    probe = directory / f".tt_write_test_{os.getpid()}"
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
    except OSError:
        return False
    try:
        probe.unlink()
    except Exception:
        pass
    return True


def _user_data_root() -> Path:
    for env_name in ("LOCALAPPDATA", "APPDATA"):
        value = os.environ.get(env_name)
        if not value:
            continue
        path = Path(value) / APP_DATA_NAME
        try:
            path.mkdir(parents=True, exist_ok=True)
            if _is_writable(path):
                return path
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / APP_DATA_NAME


def _resolve_app_dir() -> Path:
    if not is_frozen():
        return Path(__file__).resolve().parent
    exe_dir = Path(sys.executable).resolve().parent
    if _is_writable(exe_dir):
        # 优先沿用旧版免安装包放在 exe 同目录的配置，用户升级后设置不丢。
        return exe_dir
    fallback = _user_data_root()
    logging.warning(
        "程序目录不可写，用户数据改存到 %s（原目录 %s 下的配置不会自动迁移）",
        fallback, exe_dir,
    )
    return fallback


APP_DIR = _resolve_app_dir()


def app_dir() -> Path:
    """可写的用户数据根目录。"""
    return APP_DIR


def resource_path(relative: str) -> Path:
    """只读内置资源路径。"""
    base = Path(getattr(sys, "_MEIPASS", APP_DIR))
    return base / relative


def config_path() -> Path:
    return APP_DIR / "user_config.json"


def screenshots_dir() -> Path:
    path = APP_DIR / "screenshots"
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError as exc:
        logging.warning("无法创建截图目录 %s: %s", path, exc)
    # 降级目录同样要真正创建，否则截图保存会静默失败
    fallback = _user_data_root() / "screenshots"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logging.warning("无法创建降级截图目录 %s: %s", fallback, exc)
    return fallback


def models_dir() -> Path:
    path = APP_DIR / "models"
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        pass
    fallback = _user_data_root() / "models"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logging.warning("无法创建降级模型目录 %s: %s", fallback, exc)
    return fallback


_USER_LIBS_DIR = None


def user_libs_dir() -> Path:
    """在线安装的可选组件目录（跨版本升级持久保留）。

    安装版（冻结态）不能写 Program Files / 程序目录里的包，所以 pip 一律
    ``--target`` 到这里；这个目录与 user_config.json 一样被安装程序排除，
    覆盖升级不会删除，组件装一次即可一直用。

    结果在进程内缓存：组件目录启动后不会变，而每次解析都要写 + 删一个
    探针文件，设置页刷新 / 引擎自检频繁调用时既浪费又容易被受限环境的
    删除守卫打断。
    """
    global _USER_LIBS_DIR
    if _USER_LIBS_DIR is not None:
        return _USER_LIBS_DIR
    path = APP_DIR / "user_libs"
    try:
        path.mkdir(parents=True, exist_ok=True)
        if _is_writable(path):
            _USER_LIBS_DIR = path
            return path
    except OSError:
        pass
    fallback = _user_data_root() / "user_libs"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logging.warning("无法创建组件目录 %s: %s", fallback, exc)
    _USER_LIBS_DIR = fallback
    return fallback


# os.add_dll_directory() 返回的对象一旦被 GC，注册的目录就失效，必须留引用。
_DLL_HANDLES = []
_DLL_DIRS = set()

_USER_LIB_DLL_SUBDIRS = (
    "torch/lib", "numpy.libs", "scipy.libs", "shapely.libs", "bin",
)


def _register_user_lib_dll_dirs(path: Path) -> None:
    """让冻结版能加载 user_libs 里带原生 DLL 的包（torch / scipy 等）。

    Python 3.8+ 不再用 PATH 搜 DLL，PyInstaller 冻结版又只把 ``_internal``
    注册为 DLL 搜索目录。组件装进 user_libs 后，``import scipy`` / ``import
    torch`` 会抛 ``OSError: [WinError 126]``，进而让 ``import easyocr`` 失败
    —— 表现就是「明明装完了，状态却说还差引擎」。torch 自己会注册一部分，
    但 scipy / numpy / shapely 的 ``*.libs`` 不会，必须在这里补齐。
    """
    if os.name != 'nt' or not hasattr(os, 'add_dll_directory'):
        return
    for sub in _USER_LIB_DLL_SUBDIRS:
        d = path / sub
        if not d.is_dir():
            continue
        key = os.path.normcase(str(d))
        if key in _DLL_DIRS:
            continue
        try:
            handle = os.add_dll_directory(str(d))
        except OSError:
            continue
        _DLL_DIRS.add(key)
        if handle is not None:
            _DLL_HANDLES.append(handle)


# 冻结版内置包只收了部分子模块，允许 user_libs 里的同名包作为「补充搜索
# 路径」。只列纯 Python 为主的包：带 C 扩展的 numpy / cv2 一旦混版会崩，
# 不能这样补（且它们的内置版本本来就是给主程序用的）。
_COMPLEMENT_PACKAGES = ("PIL",)


def _complement_frozen_packages(path: Path) -> None:
    """把 user_libs 里的同名包追加到内置包的 ``__path__`` 末尾。

    PyInstaller 的 hook 通常只收程序入口用到的子模块：实测内置 Pillow 收了
    81 个，却漏了 ``ImageEnhance`` / ``ImageStat`` / ``ImageGrab``；而在线装
    进 user_libs 的 torchvision / skimage 恰好要 import 它们。``PIL`` 是普通
    包且已在 PYZ 里加载，``sys.path.append(user_libs)`` 不会合并
    ``PIL.__path__``，用户那份完整 Pillow 13 根本看不见，于是报
    ``ImportError: cannot import name 'ImageStat' from 'PIL'``——组件明明装
    好了，界面却说加载失败。

    追加到 **末尾**：内置版本仍优先，只有内置确实没有的子模块才回落到
    user_libs，不会让新版 C 扩展覆盖内置版本。``refresh_user_libs()`` 在
    在线安装完成后会再调用一次，覆盖「启动时这个包还不存在」的情况。
    """
    import importlib
    for name in _COMPLEMENT_PACKAGES:
        candidate = path / name
        if not (candidate / "__init__.py").is_file():
            continue
        try:
            module = importlib.import_module(name)
        except Exception:
            continue
        search_path = getattr(module, "__path__", None)
        if search_path is None:
            continue
        entry = str(candidate)
        try:
            if entry not in search_path:
                search_path.append(entry)
        except (AttributeError, TypeError):
            # 冻结版偶尔给的是不可变 path；不阻断启动
            pass


def enable_user_libs() -> Path:
    """把可选组件目录加入模块搜索路径，让已安装组件在本次启动即可用。

    只 **append** 不 insert：程序内置的 numpy / cv2 / requests 等必须保持
    优先，否则 pip 顺带装进来的依赖会覆盖内置版本导致主程序异常；而 winrt-*
    是 PEP 420 namespace package，多个路径会自动合并，追加也能被找到。
    """
    path = user_libs_dir()
    value = str(path)
    if value not in sys.path:
        sys.path.append(value)
    _register_user_lib_dll_dirs(path)
    _complement_frozen_packages(path)
    return path


# 模块被任何业务代码 import 前就生效（config / subprocess_util / app 都会
# 先导入本模块），保证 check_engine_available() 能看到上次装好的组件。
# 提前把目录建出来还有个关键原因：sys.path 里指向「不存在目录」的路径会被
# sys.path_importer_cache 永久记成 None，之后再装组件也 import 不到。
USER_LIBS_DIR = enable_user_libs()


def refresh_user_libs() -> None:
    """在线安装完组件后调用，让**本次运行**立刻能看到新装的包。

    pip 跑在子进程里，主进程的 FileFinder 缓存了目录 mtime；不失效缓存的
    话，设置页安装成功后的那次「已安装 ✓」检查可能仍然判定为未安装。
    另外还有两个容易被漏掉的坑：

    * 程序启动时 user_libs 尚不存在的话，``sys.path_importer_cache`` 会把
      这个路径永久记成 ``None``；后来装好组件也不会重新探测。
    * 新装包附带的原生 DLL 目录当时还不存在，启动时没注册过。
    """
    import importlib
    importlib.invalidate_caches()
    path = user_libs_dir()
    value = os.path.normcase(str(path))
    for key in list(sys.path_importer_cache):
        if key and os.path.normcase(str(key)) == value:
            sys.path_importer_cache.pop(key, None)
    _register_user_lib_dll_dirs(path)
    # 点「安装」后 user_libs/PIL 这类包才出现，此时要再补一次 __path__，
    # 否则本次运行仍然只能看到内置那份不完整的 Pillow。
    _complement_frozen_packages(path)
