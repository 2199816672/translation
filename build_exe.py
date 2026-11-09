#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把项目打包成免安装的 Windows 程序目录（onedir）。

用法：
    python build_exe.py
    python build_exe.py --distpath dist/staging_xxx   # 构建安装程序时用独立暂存目录

输出：
    <distpath>/TranslationTool/TranslationTool.exe

说明：
- 用户数据（user_config.json / screenshots / models）一律放在 exe 同目录，
  不进入安装清单，覆盖升级不会丢；
- 体积很大的可选后端（torch / easyocr / CUDA 等）全部排除，OCR 默认使用
  Windows 系统自带引擎；faster-whisper、soundcard 等随包附带。
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "TranslationTool"

# 运行时由 importlib / 字符串动态导入的模块，必须显式告诉 PyInstaller
HIDDEN_IMPORTS = [
    # 项目自身模块（含设置页反向导入主窗口等动态路径）
    "app", "app_paths", "updater", "check_update", "update_dialog",
    "subprocess_util", "config", "theme", "widgets", "hotkeys",
    "ai_polish", "free_translator", "translator", "ocr_recognizer",
    "speech_recognizer", "live_caption", "realtime_translate",
    "screenshot_overlay", "subtitle_overlay", "subtitle", "text_segment",
    "format_convert", "doc_parser", "office_bridge", "convert_libs",
    "resize_util", "pages", "pages.home_page", "pages.doc_page",
    "pages.av_page", "pages.settings_page",
    # 第三方
    "keyboard", "winrt", "winrt.windows.media.ocr",
    "winrt.windows.globalization", "winrt.windows.graphics.imaging",
    "winrt.windows.storage.streams", "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "faster_whisper", "ctranslate2", "av", "huggingface_hub",
    "tokenizers", "sounddevice", "soundcard", "zhconv",
    "deep_translator", "requests", "PIL.Image", "cv2", "numpy",
    "pdfplumber", "pdfminer", "docx", "pptx", "fitz", "openpyxl",
]

# 体积以 GB 计的可选后端 / 开发依赖
EXCLUDES = [
    "torch", "torchvision", "torchaudio", "easyocr", "scipy", "pandas",
    "matplotlib", "numba", "llvmlite", "paddle", "paddleocr", "paddlex",
    "paddlepaddle", "openvino", "tensorrt", "triton", "tkinter", "pytest",
    "sphinx", "IPython", "notebook", "jupyter", "jupyter_client",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="打包翻译工具免安装程序目录")
    parser.add_argument("--distpath", default=None,
                        help="PyInstaller 输出父目录（构建安装程序时用独立暂存目录）")
    parser.add_argument("--workpath", default=None,
                        help="PyInstaller 中间产物目录")
    args = parser.parse_args()

    icon = ROOT / "assets" / "app_icon.ico"
    if not icon.is_file():
        print("缺少 assets/app_icon.ico；请先由 app_icon.png 生成")
        return 1

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", APP_NAME,
        "--icon", str(icon),
        "--add-data", "assets;assets",
        "--add-data", "pages;pages",
    ]
    for module in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", module]
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    # sounddevice 是单文件模块，collect-binaries 会告警，PyInstaller 的
    # hook 已能自动带上 PortAudio DLL。
    for module in ("ctranslate2", "av"):
        cmd += ["--collect-binaries", module]

    if args.distpath:
        cmd += ["--distpath", str(Path(args.distpath).resolve())]
    if args.workpath:
        cmd += ["--workpath", str(Path(args.workpath).resolve())]
    cmd.append("main.py")

    print("正在用 PyInstaller 打包，请稍候……", flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print("打包失败。", file=sys.stderr)
        return result.returncode

    dist_root = Path(args.distpath).resolve() if args.distpath else ROOT / "dist"
    exe = dist_root / APP_NAME / f"{APP_NAME}.exe"
    if not exe.is_file():
        print(f"打包结束但没有找到 {exe}", file=sys.stderr)
        return 1

    guide = ROOT / "packaging" / "使用说明.txt"
    if guide.is_file():
        shutil.copy2(guide, exe.parent / "使用说明.txt")
    print(f"打包完成：{exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
