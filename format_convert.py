#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档格式转换调度层：常见格式互转 + 字幕互转。

输出目录由调用方显式传入，目标格式仅允许 CONVERSION_MAP 白名单内的取值；
具体转换实现在 convert_libs 与 office_bridge 中完成，本模块只负责
校验、循环写出，所有写入统一经过 _write_bytes 的目录包含校验。
"""
import os
from pathlib import Path

# 源扩展名 -> 可转换的目标格式（UI 下拉框联动用，同时作为目标白名单）
CONVERSION_MAP = {
    '.pdf': ['docx', 'png', 'jpg', 'txt'],
    '.docx': ['pdf', 'txt'],
    '.txt': ['docx', 'pdf'],
    '.png': ['pdf'], '.jpg': ['pdf'], '.jpeg': ['pdf'], '.bmp': ['pdf'],
    '.gif': ['pdf'],
    '.xlsx': ['csv'], '.csv': ['xlsx'],
    '.srt': ['vtt', 'ass', 'lrc', 'txt'],
    '.vtt': ['srt', 'ass', 'lrc', 'txt'],
    '.ass': ['srt', 'vtt', 'lrc', 'txt'],
    '.ssa': ['srt', 'vtt', 'lrc', 'txt'],
    '.lrc': ['srt', 'vtt', 'ass', 'txt'],
}


def _norm_dir(d):
    """目录规范化为绝对路径。"""
    return str(Path(str(d)).resolve())


def _out_path(out_dir, name):
    """构造输出路径：目录规范化 + 文件名强制 basename。"""
    clean = os.path.basename(str(name))
    return _norm_dir(out_dir) + os.sep + clean


def _unique_path(path):
    """输出路径已存在时自动加序号，避免覆盖。"""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem}_{n}{ext}"):
        n += 1
    return f"{stem}_{n}{ext}"


def _write_bytes(path, data, root, overwrite=False):
    """唯一的文件写出点：目标必须是输出根目录下的直接子文件。

    解析后父目录必须与输出根目录完全一致（不允许子目录或越界），
    且根目录必须存在；不跟随软链接。默认独占创建，避免并发导出互相覆盖。
    """
    target = Path(path).resolve()
    base = Path(root).resolve()
    if not base.is_dir():
        raise RuntimeError("输出目录不存在")
    if target.parent != base:
        raise RuntimeError("非法的输出路径：超出输出目录")
    if target.is_symlink():
        raise RuntimeError("非法的输出路径：不允许写入软链接")
    if overwrite and target.is_file():
        # 保存对话框里用户已确认「替换」，不再偷偷改成带序号的新文件
        target.write_bytes(data)
        return str(target)
    with open(target, 'xb') as f:
        f.write(data)
    return str(target)


def _write_with_retry(out_dir, name, data):
    """_unique_path 与写入之间可能被别的导出抢先创建，换序号重试。"""
    for _ in range(10):
        dst = _unique_path(_out_path(out_dir, name))
        try:
            return _write_bytes(dst, data, out_dir)
        except FileExistsError:
            continue
    raise RuntimeError("输出文件冲突，请稍后重试或换一个输出目录")


def convert_file(src, target, out_dir, progress_cb=None):
    """转换文件格式，返回输出文件路径列表（PDF→PNG 每页一个文件）。

    参数：
      src     — 源文件路径
      target  — 目标格式（无点扩展名，必须在 CONVERSION_MAP 白名单内）
      out_dir — 调用方显式指定的输出目录（本模块不自行推导）
    """
    src = os.path.abspath(src)
    if not os.path.exists(src):
        raise RuntimeError(f"文件不存在: {src}")
    ext = os.path.splitext(src)[1].lower()
    target = str(target).lower()
    if ext not in CONVERSION_MAP:
        raise RuntimeError(f"不支持的源格式: {ext}")
    if target not in CONVERSION_MAP[ext]:
        raise RuntimeError(f"{ext} 不能转换为 {target}")

    from convert_libs import convert_bytes
    items = convert_bytes(src, target, progress_cb)

    out_dir = _norm_dir(out_dir)
    return [_write_with_retry(out_dir, name, data) for name, data in items]


# 译文导出支持的目标格式（白名单）
EXPORT_TARGETS = ('txt', 'docx', 'pdf', 'srt', 'vtt', 'ass', 'lrc')


def export_text(text, entries, target, out_dir, stem, filename=None,
                progress_cb=None, overwrite=False):
    """导出译文到 out_dir，返回输出文件路径。

    参数：
      text      — 纯文本译文（txt/docx/pdf 使用）
      entries   — 已翻译的字幕条目（srt/vtt/ass 使用，保时间轴）
      target    — 目标格式，必须在 EXPORT_TARGETS 白名单内
      out_dir   — 调用方显式指定的输出目录
      stem      — 输出文件名主体（经字符白名单净化）
      filename  — 指定完整文件名时优先使用（仍强制 basename）
      overwrite — 保存对话框里用户已确认「替换」时为 True，直接覆盖
    """
    target = str(target).lower()
    if target not in EXPORT_TARGETS:
        raise RuntimeError(f"不支持的导出格式: {target}")

    from convert_libs import (entries_to_subtitle_bytes, safe_output_stem,
                              text_to_bytes)
    clean_stem = safe_output_stem(stem)
    out_dir = _norm_dir(out_dir)

    if target in ('srt', 'vtt', 'ass', 'lrc'):
        if not entries:
            raise RuntimeError("字幕格式导出需要带时间轴的条目")
        data = entries_to_subtitle_bytes(entries, target)
    else:
        data = text_to_bytes(text or '', target, progress_cb)

    if filename:
        name = os.path.basename(str(filename))
        # 扩展名必须与实际字节一致，否则「.txt」里装 docx 会打不开
        wanted_ext = '.' + target
        if os.path.splitext(name)[1].lower() != wanted_ext:
            name = os.path.splitext(name)[0] + wanted_ext
        if overwrite:
            return _write_bytes(_out_path(out_dir, name), data, out_dir,
                                overwrite=True)
    elif target == 'txt':
        name = clean_stem + '_译文.txt'
    elif target == 'docx':
        name = clean_stem + '_译文.docx'
    elif target == 'pdf':
        name = clean_stem + '_译文.pdf'
    elif target == 'srt':
        name = clean_stem + '_译文.srt'
    elif target == 'vtt':
        name = clean_stem + '_译文.vtt'
    elif target == 'lrc':
        name = clean_stem + '_译文.lrc'
    else:  # ass
        name = clean_stem + '_译文.ass'

    return _write_with_retry(out_dir, name, data)
