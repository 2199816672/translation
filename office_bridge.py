#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Office 文档转换桥：PDF ⇄ Word。

- PDF→Word 用 pdf2docx（纯 Python）；
- Word→PDF 优先 MS Word（docx2pdf COM），未装 Word 时回退 LibreOffice。

所有转换只在 tempfile 临时目录内产生中间文件，结果以 bytes 返回，
本模块不写用户路径。
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _find_soffice():
    """定位 LibreOffice soffice.exe，未装返回 None。"""
    exe = shutil.which('soffice') or shutil.which('soffice.exe')
    if exe:
        return exe
    for p in (
        r'C:\Program Files\LibreOffice\program\soffice.exe',
        r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
    ):
        if os.path.exists(p):
            return p
    return None


def pdf_to_docx_bytes(src, progress_cb=None):
    """PDF -> Word（.docx 字节）。扫描版 PDF 无法保证效果。"""
    try:
        from pdf2docx import Converter
    except ImportError:
        raise RuntimeError("请先安装依赖: pip install pdf2docx")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_out = os.path.join(tmp, 'out.docx')
        cv = Converter(src)
        try:
            cv.convert(tmp_out)
        finally:
            cv.close()
        if not os.path.exists(tmp_out):
            raise RuntimeError("PDF 转 Word 失败：未生成输出文件")
        return Path(tmp_out).read_bytes()


def docx_to_pdf_bytes(src, progress_cb=None):
    """Word -> PDF 字节。优先 MS Word，失败回退 LibreOffice。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_out = os.path.join(tmp, 'out.pdf')
        try:
            from docx2pdf import convert as word_convert
            word_convert(src, tmp_out)
            if os.path.exists(tmp_out):
                return Path(tmp_out).read_bytes()
        except ImportError:
            pass
        except Exception:
            pass  # 多半未安装 MS Word，走 LibreOffice 兜底

        exe = _find_soffice()
        if exe:
            in_copy = os.path.join(tmp, 'input.docx')
            shutil.copyfile(src, in_copy)
            try:
                subprocess.run(
                    [exe, '--headless', '--convert-to', 'pdf',
                     '--outdir', tmp, in_copy],
                    check=True, timeout=120,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                raise RuntimeError(f"LibreOffice 转换失败: {e}")
            produced = os.path.join(tmp, 'input.pdf')
            if os.path.exists(produced):
                return Path(produced).read_bytes()
    raise RuntimeError(
        "Word 转 PDF 需要本机安装 Microsoft Word 或 LibreOffice，"
        "检测到两者均不可用")
