"""
翻译工具 - 主程序
功能：截图 / 实时翻译 / 实时字幕 -> OCR 或语音识别 -> 翻译
"""
import sys

from app import run

if __name__ == "__main__":
    sys.exit(run())
