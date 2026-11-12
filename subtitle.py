#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕文件解析与生成：支持 SRT / VTT / ASS / SSA 互转。

统一内部表示：条目为 dict {"start": 毫秒int, "end": 毫秒int, "text": str}。
解析时剥离 ASS 覆盖标签（{\\...}）与 HTML 标签，\\N/<br> 转为真实换行。
"""
import os
import re

SUBTITLE_EXTS = {'.srt', '.vtt', '.ass', '.ssa', '.lrc'}
SUBTITLE_NAMES = {
    '.srt': 'SRT 字幕',
    '.vtt': 'WebVTT 字幕',
    '.ass': 'ASS 字幕',
    '.ssa': 'SSA 字幕',
    '.lrc': 'LRC 歌词',
}

# ── 时间转换 ─────────────────────────────────────────────────

_TS_FULL = re.compile(r'(?:(\d+):)?(\d+):(\d+)[,.](\d+)')  # [H:]M:S{,.}frac
_LRC_TS = re.compile(r'\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]')  # LRC 时间标签


def _parse_ts(ts):
    """'00:01:02,500' / '01:02.5' / '0:00:01.20' -> 毫秒。"""
    m = _TS_FULL.fullmatch(ts.strip())
    if not m:
        raise ValueError(f"无法解析时间戳: {ts}")
    h = int(m.group(1) or 0)
    mi, s, frac = int(m.group(2)), int(m.group(3)), m.group(4)
    ms = int(frac.ljust(3, '0')[:3])
    return ((h * 60 + mi) * 60 + s) * 1000 + ms


def _fmt_srt(ms):
    ms = max(0, int(ms))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, msc = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{msc:03d}"


def _fmt_vtt(ms):
    ms = max(0, int(ms))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, msc = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{msc:03d}"


def _fmt_ass(ms):
    ms = max(0, int(ms))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, cs = divmod(r, 1000)
    return f"{h}:{m:02d}:{s:02d}.{cs // 10:02d}"


def _fmt_lrc(ms):
    ms = max(0, int(ms))
    m, r = divmod(ms, 60000)
    s, cs = divmod(r, 1000)
    return f"{m:02d}:{s:02d}.{cs // 10:02d}"


# ── 文本清理 ─────────────────────────────────────────────────

_TAG_ASS = re.compile(r'\{\\[^}]*\}')      # {\an8}、{\i1} 等覆盖标签
_TAG_HTML = re.compile(r'</?[a-zA-Z][^>]*>')  # <i>、<v Roger> 等


def _clean_text(text):
    """剥掉 ASS/HTML 标签，统一换行。

    <br> 必须先换成换行再做通用标签剥离，否则会被 _TAG_HTML 一并删掉，
    两行字幕粘成一行。VTT 导出时转义过的 &amp; / &lt; 也要还原，
    否则「导出 VTT → 再导入」内容会变。
    """
    import html

    text = html.unescape(str(text))
    text = _TAG_ASS.sub('', text)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    text = _TAG_HTML.sub('', text)
    text = text.replace('\\N', '\n').replace('\\n', '\n')
    return text.strip()


# ── 读取 ─────────────────────────────────────────────────────

def _read_text(path):
    """多编码尝试读取（SRT 常见 GBK/UTF-8）。"""
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'gb18030'):
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    # 兜底：仅纯 ASCII 才用 latin-1，避免非常规编码被静默解码成乱码
    try:
        with open(path, 'rb') as fb:
            return fb.read().decode('ascii')
    except Exception:
        pass
    raise RuntimeError("无法读取字幕文件，请检查文件编码（支持 UTF-8 / GBK）")


def _detect_format(path, content):
    ext = os.path.splitext(path)[1].lower()
    if ext in SUBTITLE_EXTS:
        return ext
    head = content.lstrip()[:200].lower()
    if head.startswith('webvtt'):
        return '.vtt'
    if '[script info]' in head or 'dialogue:' in head.lower():
        return '.ass'
    if _LRC_TS.search(head):
        return '.lrc'
    return '.srt'  # 含 --> 的默认按 SRT 解析


def _parse_srt_vtt(content):
    entries = []
    blocks = re.split(r'\n\s*\n', content.strip())
    for block in blocks:
        lines = [ln for ln in block.split('\n')]
        if not lines:
            continue
        first = lines[0].strip()
        if first.upper().startswith(('WEBVTT', 'NOTE', 'STYLE', 'REGION')):
            continue
        # 找时间轴行
        ts_idx = next((i for i, ln in enumerate(lines) if '-->' in ln), -1)
        if ts_idx < 0:
            continue
        left, right = lines[ts_idx].split('-->', 1)
        # VTT 时间轴行可带 cue 设置（如 line:0 align:start）
        right = right.split()[0] if right.split() else right
        text = '\n'.join(ln.rstrip() for ln in lines[ts_idx + 1:]).strip()
        if not text:
            continue
        entries.append({
            'start': _parse_ts(left),
            'end': _parse_ts(right),
            'text': _clean_text(text),
        })
    entries.sort(key=lambda e: e['start'])
    return entries


def _parse_ass(content):
    # 找 [Events] 段的 Format 行确定列顺序
    cols = None
    lines = []
    in_events = False
    for raw in content.splitlines():
        ln = raw.strip()
        if ln.startswith('['):
            in_events = ln.lower() == '[events]'
            continue
        if not in_events or not ln:
            continue
        if ln.lower().startswith('format:'):
            cols = [c.strip().lower() for c in ln.split(':', 1)[1].split(',')]
            continue
        if ln.lower().startswith('dialogue:') and cols:
            lines.append(ln.split(':', 1)[1])
    if cols is None:
        raise RuntimeError("ASS 字幕缺少 [Events] Format 定义，无法解析")
    entries = []
    for body in lines:
        parts = body.split(',', len(cols) - 1)
        if len(parts) != len(cols):
            continue
        row = dict(zip(cols, [p.strip() for p in parts]))
        try:
            entries.append({
                'start': _parse_ts(row['start']),
                'end': _parse_ts(row['end']),
                'text': _clean_text(row['text']),
            })
        except (KeyError, ValueError):
            continue
    entries.sort(key=lambda e: e['start'])
    return entries


def _parse_lrc(content):
    """解析 LRC 歌词：一行可带多个时间标签；end 取下一行起始时间。"""
    raw = []
    for line in content.splitlines():
        stamps = _LRC_TS.findall(line)
        if not stamps:
            continue  # 元数据标签（[ti:] / [ar:] 等）与注释行跳过
        text = _clean_text(_LRC_TS.sub('', line).strip())
        if not text:
            continue
        for mm, ss, frac in stamps:
            ms = (int(mm) * 60 + int(ss)) * 1000
            if frac:
                if len(frac) >= 3:
                    ms += int(frac[:3])
                elif len(frac) == 2:
                    ms += int(frac) * 10
                else:
                    ms += int(frac) * 100
            raw.append({'start': ms, 'text': text})
    raw.sort(key=lambda e: e['start'])
    entries = []
    for i, e in enumerate(raw):
        if i + 1 < len(raw) and raw[i + 1]['start'] > e['start']:
            end = raw[i + 1]['start']
        else:
            end = e['start'] + 2500
        entries.append({'start': e['start'], 'end': end, 'text': e['text']})
    return entries


def parse_subtitle(path):
    """解析字幕文件 -> list[{"start": ms, "end": ms, "text": str}]。"""
    content = _read_text(path)
    fmt = _detect_format(path, content)
    if fmt in ('.ass', '.ssa'):
        return _parse_ass(content)
    if fmt == '.lrc':
        return _parse_lrc(content)
    return _parse_srt_vtt(content)


# ── 生成 ─────────────────────────────────────────────────────

_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,60,60,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _escape_vtt(text):
    """WebVTT cue 里 & < > 必须转义，否则 --> 会被当成时间轴、<b> 当标签。"""
    return (str(text).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;'))


def serialize_subtitle(entries, fmt):
    """把条目列表序列化为指定格式字符串。fmt ∈ {'.srt','.vtt','.ass','.ssa'}。"""
    fmt = fmt.lower()
    if fmt == '.ssa':
        fmt = '.ass'
    if fmt not in ('.srt', '.vtt', '.ass', '.lrc'):
        raise ValueError(f"不支持的字幕格式: {fmt}")

    # 空文本条目不写空 cue，否则导出再导入会丢条目（LRC 解析会跳过空行）
    items = [e for e in entries if str(e.get('text', '')).strip()]
    out = []
    if fmt == '.lrc':
        for e in items:
            text = e['text'].replace('\n', ' ').strip()
            # LRC 用 [mm:ss.xx] 做时间戳，正文里的方括号会干扰解析
            text = text.replace('[', '(').replace(']', ')')
            out.append(f"[{_fmt_lrc(e['start'])}]{text}")
        return '\n'.join(out) + '\n'

    if fmt == '.srt':
        for i, e in enumerate(items, 1):
            out.append(str(i))
            out.append(f"{_fmt_srt(e['start'])} --> {_fmt_srt(e['end'])}")
            out.append(e['text'])
            out.append('')
        return '\n'.join(out).rstrip('\n') + '\n'

    if fmt == '.vtt':
        out.append('WEBVTT')
        out.append('')
        for e in items:
            out.append(f"{_fmt_vtt(e['start'])} --> {_fmt_vtt(e['end'])}")
            out.append(_escape_vtt(e['text']))
            out.append('')
        return '\n'.join(out)

    # ASS
    out.append(_ASS_HEADER.rstrip('\n'))
    for e in items:
        text = e['text'].replace('\n', '\\N')
        out.append(
            f"Dialogue: 0,{_fmt_ass(e['start'])},{_fmt_ass(e['end'])},"
            f"Default,,0,0,0,,{text}"
        )
    return '\n'.join(out) + '\n'


def subtitle_to_plain_text(entries):
    """字幕条目 -> 每条一行的纯文本（供翻译/预览）。"""
    return '\n'.join(e['text'].replace('\n', ' ') for e in entries)


def compose_entries(entries, translations, mode, fmt='.srt'):
    """按导出模式组合字幕条目：mode ∈ {source, both, target}。

    - source：只导出原文
    - target：只导出译文（缺译文时回退原文）
    - both：原文+译文；LRC 单行格式用 "原文 / 译文"，其余格式换行显示
    """
    if mode not in ('source', 'both', 'target'):
        raise ValueError(f"未知的导出模式: {mode}")
    result = []
    trs = translations or []
    for i, e in enumerate(entries):
        orig = e['text']
        trans = trs[i] if i < len(trs) and trs[i] else ''
        if mode == 'target':
            text = trans or orig
        elif mode == 'both':
            if not trans:
                text = orig
            elif fmt == '.lrc':
                text = f"{orig.replace(chr(10), ' ')} / {trans.replace(chr(10), ' ')}"
            else:
                text = f"{orig}\n{trans}"
        else:
            text = orig
        result.append({
            'start': e['start'],
            'end': e['end'],
            'text': text,
        })
    return result


def translate_segments(texts, translate, on_progress=None):
    """逐条翻译，失败 / 空结果一律置空，绝不回退原文冒充译文。

    返回 ``(results, failed, last_error)``；results 与 texts 等长，失败项是
    空串。单文件与批量链路共用这条语义，避免「只导出译文」写出原文、
    「原文+译文」出现「原文 / 原文」重复双语。
    """
    results = []
    failed = 0
    last_error = ''
    total = len(texts)
    for i, text in enumerate(texts, 1):
        out = ''
        try:
            out = translate(text)
        except Exception as exc:  # 单条失败不能丢掉整份已识别的字幕
            last_error = str(exc)
        if not isinstance(out, str) or not out.strip():
            failed += 1
            results.append('')
        else:
            results.append(out)
        if on_progress is not None and (i % 5 == 0 or i == total):
            on_progress(i, total)
    return results, failed, last_error


def safe_export_mode(entry_count, translations, mode):
    """译文不完整时降级导出模式，返回 ``(实际模式, 缺失条数)``。

    - ``target``：只要有一条没译出来就整份退回 ``source``，否则会把原文
      当成译文写进字幕文件；
    - ``both``：缺译文的条目由 :func:`compose_entries` 只保留一行原文，
      不会产出「原文 / 原文」重复双语，无需整份降级；
    - ``source``：不依赖译文，原样返回。
    """
    if mode != 'target':
        return mode, 0
    trs = translations or []
    missing = sum(
        1 for i in range(entry_count)
        if i >= len(trs) or not str(trs[i] or '').strip())
    return ('source' if missing else 'target'), missing
