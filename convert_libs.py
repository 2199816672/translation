#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""格式转换的纯计算层：读取源文件，返回目标格式字节。

本模块不接收输出路径、不做任何文件写入，全部转换结果以 bytes 返回，
由上层 format_convert 统一校验并写入。
"""
import csv
import io
import os

# openpyxl / Pillow 只在真正处理 Excel、图片时导入：字幕与文本导出
# 不该因为这两个可选依赖缺失就整体不可用（源码运行尤其常见）。
from doc_parser import parse_file as _parse_source_text
from subtitle import parse_subtitle, serialize_subtitle, subtitle_to_plain_text

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}
SUB_EXTS = {'.srt', '.vtt', '.ass', '.ssa', '.lrc'}

# 输出文件名后缀（均为字面量，避免动态拼接扩展名）
_SUFFIX = {
    'docx': '_converted.docx',
    'pdf': '_converted.pdf',
    'txt': '_converted.txt',
    'csv': '_converted.csv',
    'xlsx': '_converted.xlsx',
    'srt': '_converted.srt',
    'vtt': '_converted.vtt',
    'ass': '_converted.ass',
    'lrc': '_converted.lrc',
}


def safe_output_stem(raw):
    """文件名主体净化：仅保留字母数字（含汉字）、下划线、短横线和空格。"""
    out = []
    for ch in str(raw):
        if ch.isalnum() or ch in ('_', '-', ' '):
            out.append(ch)
    return ''.join(out).strip() or 'output'


def _need(module, pip_name):
    """延迟导入，缺失时给出中文安装提示。"""
    try:
        return __import__(module)
    except ImportError:
        raise RuntimeError(f"请先安装依赖: pip install {pip_name}")


def read_text(path):
    """多编码读取文本文件（TXT 常见 UTF-8 / GBK）。"""
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'gb18030'):
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    # 兜底：仅当文件为纯 ASCII 时才用 latin-1 解码，避免把 GBK/Big5 等非常规编码
    # 静默解码成乱码（latin-1 能解码任何字节，但非 ASCII 字节会变成乱码）。
    try:
        with open(path, 'rb') as fb:
            return fb.read().decode('ascii')
    except Exception:
        pass
    raise RuntimeError("无法读取文件，请检查文件编码（支持 UTF-8 / GBK）")


def doc_text(src):
    """任意受支持文档 -> 纯文本（复用 doc_parser 的解析矩阵）。"""
    return _parse_source_text(src)


def subtitle_text(src):
    """字幕 -> 每条一行的纯文本。"""
    return subtitle_to_plain_text(parse_subtitle(src))


def subtitle_to_bytes(src, target):
    """字幕互转：返回目标字幕格式的 UTF-8 字节。"""
    entries = parse_subtitle(src)
    if not entries:
        raise RuntimeError("字幕文件没有可用的条目")
    return serialize_subtitle(entries, '.' + target).encode('utf-8')


def pdf_to_images_bytes(src, fmt='png', progress_cb=None):
    """PDF 每页转图片，返回 [(页码, 图片字节), ...]。fmt: png / jpg。"""
    try:
        import pymupdf as mupdf
    except ImportError:
        try:
            import fitz as mupdf
        except ImportError:
            mupdf = _need('pymupdf', 'PyMuPDF')
    try:
        doc = mupdf.open(src)
    except Exception as e:
        raise RuntimeError(f"无法打开 PDF（可能已加密或文件损坏）: {e}")
    pages = []
    try:
        if doc.needs_pass:
            raise RuntimeError("PDF 已加密，无法转换为图片")
        total = doc.page_count
        codec = 'png' if fmt == 'png' else 'jpeg'
        for i in range(total):
            pages.append((i + 1, doc[i].get_pixmap(dpi=150).tobytes(codec)))
            if progress_cb:
                progress_cb(i + 1, total)
    finally:
        doc.close()
    return pages


def txt_to_pdf_bytes(text, progress_cb=None):
    """纯文本转 PDF（注册 Windows 中文字体，自动换行分页）。"""
    _need('reportlab', 'reportlab')
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    font = None
    for fp in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf',
               r'C:\Windows\Fonts\simsun.ttc'):
        if os.path.exists(fp):
            try:
                pdfmetrics.registerFont(TTFont('CJKBody', fp, subfontIndex=0))
                font = 'CJKBody'
                break
            except Exception:
                continue
    if not font:
        raise RuntimeError("未找到可用的中文字体（msyh.ttc / simhei.ttf）")

    style = ParagraphStyle('body', fontName=font, fontSize=11,
                           leading=18, wordWrap='CJK')
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=54, rightMargin=54,
                            topMargin=54, bottomMargin=54)
    story = []
    for para in text.split('\n'):
        para = para.rstrip()
        if not para:
            if story:
                story.append(Spacer(1, 8))
            continue
        esc = (para.replace('&', '&amp;')
                   .replace('<', '&lt;').replace('>', '&gt;'))
        story.append(Paragraph(esc, style))
    if not story:
        story.append(Paragraph('（空文档）', style))
    doc.build(story)
    return buf.getvalue()


def images_to_pdf_bytes(paths, progress_cb=None):
    """图片转 PDF（透明背景铺白底）。"""
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("请先安装依赖: pip install Pillow")
    pages = []
    for p in paths:
        with Image.open(p) as src:
            if src.mode in ('RGBA', 'LA', 'P'):
                img = src.convert('RGBA')
                bg = Image.new('RGB', img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            else:
                img = src.convert('RGB')
            pages.append(img)
    if not pages:
        raise RuntimeError("没有可转换的图片")
    buf = io.BytesIO()
    pages[0].save(buf, format='PDF', save_all=True, append_images=pages[1:])
    return buf.getvalue()


def xlsx_to_csv_bytes(src, progress_cb=None):
    """Excel 第一个工作表 -> CSV（UTF-8 BOM，Excel 可直接打开）。"""
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("请先安装依赖: pip install openpyxl")
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    buf = io.StringIO()
    try:
        ws = wb[wb.sheetnames[0]]
        w = csv.writer(buf)
        for row in ws.iter_rows(values_only=True):
            w.writerow(['' if c is None else c for c in row])
    finally:
        wb.close()
    return buf.getvalue().encode('utf-8-sig')


def text_to_docx_bytes(text, progress_cb=None):
    """纯文本 -> Word（每行一个段落）。"""
    try:
        from docx import Document
    except ImportError:
        raise RuntimeError("请先安装依赖: pip install python-docx")
    doc = Document()
    for line in text.split('\n'):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def text_to_bytes(text, target, progress_cb=None):
    """译文文本 -> 目标格式字节（txt/docx/pdf）。"""
    if target == 'txt':
        # 中文 Windows 的记事本/Excel 会把无 BOM 的 UTF-8 当成 ANSI，
        # 加 BOM 后双击直接可读；字幕格式仍走无 BOM 的分支。
        return text.encode('utf-8-sig')
    if target == 'docx':
        return text_to_docx_bytes(text, progress_cb)
    if target == 'pdf':
        return txt_to_pdf_bytes(text, progress_cb)
    raise RuntimeError(f"不支持的目标格式: {target}")


def entries_to_subtitle_bytes(entries, target):
    """已翻译的字幕条目 -> 目标字幕格式字节。"""
    return serialize_subtitle(entries, '.' + target).encode('utf-8')


def csv_to_xlsx_bytes(src, progress_cb=None):
    """CSV -> Excel（自动尝试 UTF-8 / GBK）。"""
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("请先安装依赖: pip install openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ok = False
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            with open(src, 'r', encoding=enc, newline='') as f:
                for row in csv.reader(f):
                    ws.append(row)
            ok = True
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if not ok:
        raise RuntimeError("无法读取 CSV，请检查文件编码")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── 统一入口：按目标格式返回 (输出文件名, 字节) 列表 ──────────

def convert_bytes(src, target, progress_cb=None):
    """执行转换，返回 [(输出文件名, 字节), ...]，文件名已净化。

    目标格式合法性由调用方 format_convert 依据 CONVERSION_MAP 校验。
    """
    ext = os.path.splitext(src)[1].lower()
    raw_stem = os.path.splitext(os.path.basename(src))[0]
    stem = safe_output_stem(raw_stem)
    is_sub = ext in SUB_EXTS

    # 字幕互转
    if is_sub and target in ('srt', 'vtt', 'ass', 'lrc'):
        data = subtitle_to_bytes(src, target)
        return [(stem + _SUFFIX[target], data)]

    # 转纯文本
    if target == 'txt':
        text = subtitle_text(src) if is_sub else doc_text(src)
        return [(stem + _SUFFIX['txt'], text.encode('utf-8'))]

    # PDF -> Word
    if ext == '.pdf' and target == 'docx':
        from office_bridge import pdf_to_docx_bytes
        return [(stem + _SUFFIX['docx'],
                 pdf_to_docx_bytes(src, progress_cb))]

    # PDF -> 逐页图片（PNG / JPG）
    if ext == '.pdf' and target in ('png', 'jpg'):
        suffix = '.png' if target == 'png' else '.jpg'
        items = []
        for page_no, img in pdf_to_images_bytes(src, target, progress_cb):
            items.append((stem + '_page' + str(page_no) + suffix, img))
        return items

    # Word -> PDF
    if ext == '.docx' and target == 'pdf':
        from office_bridge import docx_to_pdf_bytes
        return [(stem + _SUFFIX['pdf'],
                 docx_to_pdf_bytes(src, progress_cb))]

    # TXT -> PDF
    if ext == '.txt' and target == 'pdf':
        return [(stem + _SUFFIX['pdf'],
                 txt_to_pdf_bytes(read_text(src), progress_cb))]

    # 图片 -> PDF
    if ext in IMAGE_EXTS and target == 'pdf':
        return [(stem + _SUFFIX['pdf'],
                 images_to_pdf_bytes([src], progress_cb))]

    # Excel -> CSV
    if ext == '.xlsx' and target == 'csv':
        return [(stem + _SUFFIX['csv'], xlsx_to_csv_bytes(src, progress_cb))]

    # CSV -> Excel
    if ext == '.csv' and target == 'xlsx':
        return [(stem + _SUFFIX['xlsx'], csv_to_xlsx_bytes(src, progress_cb))]

    raise RuntimeError(f"暂不支持 {ext} -> {target} 的转换")
