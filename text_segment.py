#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文本分段：把 OCR / 文档的硬换行合并成自然段落，供翻译与叠加显示使用。

OCR 结果常把一句完整的话按屏幕宽度拆成多行，直接逐行翻译会把句子切碎。
这里按句末标点和行首特征判断换行是「硬换行」还是真正的分段：
- 上一行没有句末标点 → 视为同一句被屏幕折行，合并；
- 空行、行首是编号/项目符号 → 新段落；
- 段落过长时按句末标点切成小块，避免超出免费接口的长度限制。
"""
import re

_SENTENCE_END = "。！？!?…；;"
_CLOSING = "”’」』》）)]】\"'>"
_HEADING_RE = re.compile(
    r"^\s*(?:[-*•·]|\(?\d{1,2}[.、)）]"
    r"|[一二三四五六七八九十]{1,3}[、.．]"
    r"|第[一二三四五六七八九十百千\d]{1,4}[章节条部分])")
_CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _ends_sentence(text):
    stripped = text.rstrip()
    while stripped and stripped[-1] in _CLOSING:
        stripped = stripped[:-1]
    return bool(stripped) and stripped[-1] in _SENTENCE_END


def _starts_new_block(text):
    return bool(_HEADING_RE.match(text))


def _is_cjk(ch):
    return bool(_CJK_RE.match(ch))


def _join_lines(first, second):
    if not first:
        return second
    if not second:
        return first
    if _is_cjk(first[-1]) or _is_cjk(second[0]):
        return first + second
    return first + " " + second


def split_paragraphs(text, merge_lines=True):
    """切成自然段：空行分段；未结束的句子与下一行合并。"""
    if not text or not text.strip():
        return []
    paragraphs = []
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if current:
                paragraphs.append(current.strip())
                current = ""
            continue
        if not current:
            current = line
            continue
        if not merge_lines:
            paragraphs.append(current.strip())
            current = line
            continue
        if _starts_new_block(line) or _ends_sentence(current):
            paragraphs.append(current.strip())
            current = line
        else:
            current = _join_lines(current, line)
    if current.strip():
        paragraphs.append(current.strip())
    return [para for para in paragraphs if para]


def flatten_text(text):
    """压平成一行（实时翻译用：一次请求、上下文完整）。"""
    merged = ""
    for para in split_paragraphs(text):
        merged = _join_lines(merged, para)
    return merged


_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc",
    "fig", "no", "inc", "ltd", "co", "approx", "e.g", "i.e", "a.m", "p.m",
}


def _is_period_end(text, index):
    """英文句号是否真的结束一句（排除缩写、小数、省略号、引号内句号）。"""
    n = len(text)
    prev = text[index - 1] if index > 0 else ""
    nxt = text[index + 1] if index + 1 < n else ""
    if prev.isdigit() and nxt.isdigit():
        return False
    if nxt and not nxt.isspace() and nxt not in _CLOSING \
            and nxt not in _SENTENCE_END:
        return False
    if nxt == "." or prev == ".":
        return False
    start = index
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    word = text[start:index].strip().lower()
    return word not in _ABBREVIATIONS


def _ends_sentence_block(text):
    """OCR 行块是否已结束一句（含英文句号，排除缩写 / 小数 / 省略号）。

    _ends_sentence 只认中文标点和 !?；英文正文几乎全以 '.' 结尾，
    若用它分组会退化成「固定每 3 行切一刀」，把下一句硬并进上一组。
    """
    s = str(text or "").rstrip()
    while s and s[-1] in _CLOSING:
        s = s[:-1]
    if not s:
        return False
    if s[-1] in _SENTENCE_END:
        return True
    if s[-1] == ".":
        return _is_period_end(s, len(s) - 1)
    return False


def split_sentences(text):
    """按句末标点切句（保留标点，以及紧跟其后的右引号/括号）。"""
    text = (text or "").strip()
    if not text:
        return []
    sentences = []
    buffer = ""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        buffer += ch
        i += 1
        is_end = ch in _SENTENCE_END or (
            ch == "." and _is_period_end(text, i - 1))
        if not is_end:
            continue
        # 连续标点、右引号/括号都归入当前句
        while i < n and (text[i] in _SENTENCE_END or text[i] in _CLOSING):
            buffer += text[i]
            i += 1
        if buffer.strip():
            sentences.append(buffer.strip())
        buffer = ""
    if buffer.strip():
        sentences.append(buffer.strip())
    return sentences


_PUNCT_CHARS = set(".,;:!?'\"…、。，；：！？")
_CLOSING_CHARS = set(_CLOSING) | {")", "]", "}", "»"}
_OPENING_CHARS = set("([{<«「『（《“‘\"'")
_LATIN_RE = re.compile(r"[A-Za-z0-9\u00c0-\u024f\u0400-\u04ff]")


def _is_latin(ch):
    return bool(_LATIN_RE.match(ch))


def _tokenize(line):
    """切成折行单元：拉丁单词 / 单个 CJK 字符。

    标点并入相邻单元：句末标点跟前一个单元，行首左引号/左括号跟后一个单元。
    小数与英文缩写内部的点不当作词边界。
    """
    units = []
    i = 0
    n = len(line)
    prefix = ""
    while i < n:
        ch = line[i]
        if ch.isspace():
            j = i
            while j < n and line[j].isspace():
                j += 1
            if prefix:
                units.append(prefix)
                prefix = ""
            units.append(line[i:j])
            i = j
            continue
        # 左引号 / 左括号并入后一个单元，避免孤零零被挤到行尾
        if ch in _OPENING_CHARS and i + 1 < n and not line[i + 1].isspace():
            prefix += ch
            i += 1
            continue
        if _is_latin(ch):
            j = i + 1
            while j < n:
                c = line[j]
                if _is_latin(c) or c in "'’-":
                    j += 1
                    continue
                # 3.5 / e.g. 这类内部的点不打断单词
                if c == "." and j + 1 < n and _is_latin(line[j + 1]):
                    j += 1
                    continue
                break
        else:
            j = i + 1
        # 紧跟的标点 / 右引号归入本单元
        while j < n and (line[j] in _PUNCT_CHARS or line[j] in _CLOSING_CHARS):
            j += 1
        units.append(prefix + line[i:j])
        prefix = ""
        i = j
    if prefix:
        units.append(prefix)
    return units


def _fit_line(text, max_width, measure):
    """把超宽 token 按字数硬切直到能放下，返回 (首段, 余下)。"""
    cell = len(text)
    while cell > 1 and measure(text[:cell]) > max_width:
        cell -= 1
    return text[:cell], text[cell:]


def wrap_by_width(text, max_width, measure):
    """按像素宽度折行：优先在词/字边界断开，超长单词才硬切。

    measure 是一个接受字符串、返回显示宽度的函数（通常是
    QFontMetrics.horizontalAdvance）。保留原文的换行。
    """
    max_width = max(1, int(max_width))
    lines = []
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line:
            # 原文空行保留为段落间隔（首行除外）
            if lines:
                lines.append("")
            continue
        current = ""
        for token in _tokenize(line):
            if token.isspace():
                if current:
                    current += " "
                continue
            if not current:
                current = token
            elif measure(current + token) > max_width:
                lines.append(current.rstrip())
                current = token
            else:
                current += token
            # 单个 unit 比整行还宽：硬切，保证不溢出
            while len(current) > 1 and measure(current) > max_width:
                head, current = _fit_line(current, max_width, measure)
                lines.append(head.rstrip())
        if current.strip():
            lines.append(current.rstrip())
    return lines or [""]


def wrap_sentences(text, max_width, measure):
    """按句折行：整句能塞进当前行就接着排，放不下才换行。

    与直接按宽度折行相比，断点尽量落在句末，句尾不会孤零零留半个词。
    """
    text = (text or "").strip()
    if not text:
        return []
    max_width = max(1, int(max_width))
    sentences = split_sentences(text) or [text]
    lines = []
    current = ""
    for sentence in sentences:
        candidate = _join_lines(current, sentence) if current else sentence
        if current and measure(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        parts = wrap_by_width(sentence, max_width, measure)
        # 保留 wrap_by_width 产出的空行，别把原文段落间隔吞掉
        lines.extend(parts[:-1])
        current = parts[-1] if parts else ""
    if current.strip():
        lines.append(current.strip())
    return lines or [text]


def _nearest_word_boundary(buffer):
    """buffer 已达上限时，回退到最近一个词边界（空格）的切分点。

    主要用于英文 / 代码等带空格的文本，避免把单词从中间斩断；
    没有空格（如连续中文）则返回 0，交由硬切降级处理。
    """
    # 从靠近末尾处往回找空格，至少保留前半段，避免回退过多
    for i in range(len(buffer) - 2, max(0, len(buffer) // 2), -1):
        if buffer[i] == " ":
            return i + 1
    return 0


def chunk_text(text, max_len=1500):
    """过长文本切块：保证单块不超 max_len，且尽量在句末 / 词边界切。

    句子结束标点处断句能保留最完整的翻译上下文；若一段很长且缺少
    标点、逼近硬上限，则回退到最近词边界切，避免斩断单词导致语义破损。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    chunks = []
    buffer = ""
    for ch in text:
        buffer += ch
        if ch in _SENTENCE_END and len(buffer) >= max_len * 0.6:
            chunks.append(buffer.strip())
            buffer = ""
        elif len(buffer) >= max_len:
            cut = _nearest_word_boundary(buffer)
            if cut > 0:
                chunks.append(buffer[:cut].strip())
                buffer = buffer[cut:]
            else:
                chunks.append(buffer.strip())
                buffer = ""
    if buffer.strip():
        chunks.append(buffer.strip())
    return [chunk for chunk in chunks if chunk]


# ── OCR 文本块：让译文能贴回各自原文所在的位置 ──────────────────

def _has_content(text):
    """过滤截图里的装饰行（点赞图标、分隔线、纯符号 / 零星数字）。

    至少要有 3 个字母、数字或汉字才算正文，避免把工具条上的 "0  √  ♡"
    当成一个需要翻译的段落。
    """
    return len(re.findall(r"[A-Za-z0-9\u3400-\u9fff]",
                          str(text or ""))) >= 3


def _visual_rows(blocks):
    """清洗 / 排序 OCR 行块，并统计行距基线。

    返回 (items, gaps, med_gap, med_h, pitch)。gaps[i] 是 items[i+1] 与
    上一行 bbox 底部之间的空白；pitch 是相邻行 y 的中位数（行距）。

    段落切分只依赖这些几何量，不看标点：按句末标点切会把一个自然段拆成
    好几块，译文被拆碎、字号被压小，逐块翻译还会丢失上下文。
    """
    items = []
    for b in (blocks or []):
        if not b:
            continue
        text = str(b.get("text", "")).strip()
        if not text or not _has_content(text):
            continue
        d = {"text": text}
        for key in ("x", "y", "w", "h"):
            d[key] = float(b.get(key, 0) or 0)
        d["h"] = max(1.0, d["h"])
        d["w"] = max(1.0, d["w"])
        items.append(d)
    if not items:
        return [], [], 0.0, 12.0, 20.0
    items.sort(key=lambda b: (b["y"], b["x"]))

    def median(seq):
        s = sorted(seq)
        return s[len(s) // 2] if s else 0.0

    med_h = median([b["h"] for b in items]) or 12.0
    pitch = (median([items[i]["y"] - items[i - 1]["y"]
                     for i in range(1, len(items))
                     if items[i]["y"] > items[i - 1]["y"]])
             or med_h * 1.5)
    gaps = [items[i]["y"] - (items[i - 1]["y"] + items[i - 1]["h"])
            for i in range(1, len(items))]
    med_gap = (median([g for g in gaps if g > 0])
               or max(1.0, med_h * 0.4))
    return items, gaps, med_gap, med_h, pitch


def _split_visual_rows(blocks, max_lines=18):
    """按行间距把 OCR 行切成视觉段落，返回 [[行块, ...], ...]。"""
    items, gaps, med_gap, med_h, pitch = _visual_rows(blocks)
    if not items:
        return []
    # 段落内行距稳定，段落之间（空行 / 标题上下）明显更大。
    # 阈值随本次 OCR 的实际行距自适应，不依赖固定像素值或 DPI。
    thresh = max(med_gap * 1.8, med_h * 0.55, pitch * 0.45, 5.0)
    rows, cur = [], []
    for i, b in enumerate(items):
        if cur:
            gap = gaps[i - 1] if i - 1 < len(gaps) else 0.0
            dy = b["y"] - cur[-1]["y"]
            overflow = (len(cur) >= max_lines
                        and _ends_sentence_block(str(cur[-1]["text"])))
            if gap > thresh or dy > pitch * 1.75 or overflow:
                rows.append(cur)
                cur = []
        cur.append(b)
    if cur:
        rows.append(cur)
    return rows


def blocks_to_text(blocks):
    """把带坐标的 OCR 行块还原成纯文本（供主页显示 / 缓存 / 复制）。

    段落之间插入空行，让 split_paragraphs 能还原出原来的自然段。
    """
    rows = _split_visual_rows(blocks)
    if not rows:
        return ""
    parts = []
    for i, row in enumerate(rows):
        if i:
            parts.append("")
        parts.extend(str(b["text"]).strip() for b in row)
    return "\n".join(parts)


def group_blocks(blocks, max_lines=None):
    """把 OCR 行块按【视觉段落】聚合成显示组。

    一个自然段（含它上方的短标题）= 一个组 = 一个矩形 + 一次翻译请求。
    原文 2 段就只会得到 2 块译文，不会被句末标点切成 4~6 块；整段一次
    翻译也保留了上下文，译文比逐句翻译通顺得多。

    max_lines 仅为兼容旧调用保留：超过该行数且遇句末才允许切分，
    防止一整屏无空行的超长文本撑爆单次翻译请求。
    """
    rows = _split_visual_rows(blocks, max_lines=max_lines or 18)
    if not rows:
        return []
    _, _, _, med_h, pitch = _visual_rows(blocks)

    # 短标题行（如 "Passage 1 — ..."）并入紧跟其后的正文段，否则会多出
    # 一个孤零零的小框，看起来像「又多了一段」。
    merged = []
    i = 0
    while i < len(rows):
        cur = rows[i]
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        head = cur[0]
        if (nxt and len(cur) == 1 and len(head["text"]) <= 100
                and abs(head["x"] - nxt[0]["x"]) <= max(40.0, med_h * 3.0)
                and nxt[0]["y"] - (head["y"] + head["h"]) <= pitch * 3.0):
            merged.append(cur + nxt)
            i += 2
        else:
            merged.append(cur)
            i += 1

    groups = []
    for row in merged:
        x = min(b["x"] for b in row)
        y = min(b["y"] for b in row)
        x2 = max(b["x"] + b["w"] for b in row)
        y2 = max(b["y"] + b["h"] for b in row)
        text = ""
        for b in row:
            text = _join_lines(text, str(b["text"]).strip())
        groups.append({"text": text, "x": x, "y": y,
                       "w": max(1.0, x2 - x), "h": max(1.0, y2 - y),
                       "lines": len(row)})
    return groups


def normalize_groups(groups, img_w, img_h):
    """把像素坐标换成相对裁剪图的 0~1 比例，绘制端乘选区宽高即可。

    归一化后截图覆盖层与实时浮层都不必各自处理 DPI / 缩放系数。
    """
    iw = max(1.0, float(img_w or 1))
    ih = max(1.0, float(img_h or 1))
    out = []
    for g in groups or []:
        b = dict(g)
        b["x"] = max(0.0, min(1.0, float(b.get("x", 0)) / iw))
        b["y"] = max(0.0, min(1.0, float(b.get("y", 0)) / ih))
        b["w"] = max(0.001, min(1.0 - b["x"], float(b.get("w", 1)) / iw))
        b["h"] = max(0.001, min(1.0 - b["y"], float(b.get("h", 1)) / ih))
        out.append(b)
    return out
