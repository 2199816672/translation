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


def group_blocks_fine(blocks, max_gap_ratio=2.5):
    """按【视觉行】聚合成贴回位置用的显示组（截图覆盖层 / 实时浮层专用）。

    与 group_blocks 的区别：group_blocks 会按行距把整栏文字合并成一个
    「视觉段落」，bbox 横跨大半个屏幕，译文只能挤进一个大黑框、和原文字
    位置完全脱节；设置界面、表格、表单这类短标签尤其明显（一大坨译文 +
    盖住输入框）。这里每行单独成组，且同一行内只有水平间隙很小的词块才
    合并，左右分栏不会被连成一整块。

    每行一次翻译请求：界面上的标签 / 按钮 / 短句本来就是独立语义单元，
    逐行翻译比把整栏拼成一段更准确，译文也能一一贴回原位。
    """
    items, _gaps, _med_gap, med_h, _pitch = _visual_rows(blocks)
    if not items:
        return []
    items.sort(key=lambda b: (b["y"], b["x"]))

    # 1) y 中心接近的词块归到同一视觉行（EasyOCR 会给出词级块，
    #    Windows OCR 已经是行级块，这里对两者都成立）。
    rows = []
    for b in items:
        cy = b["y"] + b["h"] / 2.0
        placed = False
        for row in rows:
            rcy = sum(x["y"] + x["h"] / 2.0 for x in row) / len(row)
            if abs(cy - rcy) <= max(3.0, med_h * 0.6):
                row.append(b)
                placed = True
                break
        if not placed:
            rows.append([b])

    # 2) 同一行再按水平间隙切簇：左右分栏、相距很远的两块绝不连成一个框
    max_gap = max(24.0, med_h * float(max_gap_ratio))
    groups = []
    for row in rows:
        row.sort(key=lambda b: b["x"])
        clusters, cur = [], [row[0]]
        for b in row[1:]:
            prev_right = cur[-1]["x"] + cur[-1]["w"]
            if b["x"] - prev_right <= max_gap:
                cur.append(b)
            else:
                clusters.append(cur)
                cur = [b]
        clusters.append(cur)
        for cluster in clusters:
            x = min(b["x"] for b in cluster)
            y = min(b["y"] for b in cluster)
            x2 = max(b["x"] + b["w"] for b in cluster)
            y2 = max(b["y"] + b["h"] for b in cluster)
            text = ""
            for b in cluster:
                text = _join_lines(text, str(b["text"]).strip())
            if not text.strip():
                continue
            groups.append({"text": text, "x": x, "y": y,
                           "w": max(1.0, x2 - x), "h": max(1.0, y2 - y),
                           "lines": 1})
    groups.sort(key=lambda g: (g["y"], g["x"]))
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


# ── 词级坐标：截图「提取」按词点选用 ────────────────────────────
# Windows OCR 能直接给出 word 级 bounding_rect；EasyOCR 只给行级 bbox，
# 这里按「字符显示宽度」把行宽分摊给每个词，坐标是近似值但足够点选。
# 坐标系与输入一致（像素或 0~1 归一化都成立，分摊是线性的）。

_ASCII_INNER = "._-'/:"
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+")


def _display_weight(text):
    """CJK 按 2 个单位宽，ASCII / 标点按 1 个单位宽。"""
    return sum(2 if _is_cjk(ch) else 1 for ch in text)


def _tokenize_words(text, max_cjk=2):
    """返回 [(片段, 权重, 是否是可点的词)]。

    英文按单词（允许内部 . - ' 等，如 e-mail / 3.14 / don't），中文连续串
    按 max_cjk 个字一组；空格与标点只占宽度、不生成可点标签。
    """
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            tokens.append((ch, 1, False))
            i += 1
            continue
        if _is_cjk(ch):
            j = i
            while j < n and _is_cjk(text[j]):
                j += 1
            run = text[i:j]
            parts = [run[k:k + max_cjk]
                     for k in range(0, len(run), max(1, max_cjk))]
            # 末尾落单的一个字并回上一组，避免出现只有一个字的碎标签
            if len(parts) >= 2 and len(parts[-1]) == 1:
                parts[-2] += parts[-1]
                parts.pop()
            for part in parts:
                tokens.append((part, _display_weight(part), True))
            i = j
            continue
        if ch.isascii() and ch.isalnum():
            j = i
            while j < n and text[j].isascii() and (
                    text[j].isalnum() or text[j] in _ASCII_INNER):
                j += 1
            raw = text[i:j]
            word = raw.rstrip(_ASCII_INNER) or raw
            tokens.append((word, max(1, j - i), True))
            i = j
            continue
        tokens.append((ch, 1, False))
        i += 1
    return tokens


def split_line_to_words(block, max_cjk=2):
    """把一个行块切成词块，按显示宽度比例分摊 x / w，y / h 保持不变。"""
    text = str(block.get("text") or "").strip()
    if not text:
        return []
    x = float(block.get("x", 0) or 0)
    y = float(block.get("y", 0) or 0)
    w = max(0.001, float(block.get("w", 1) or 1))
    h = max(0.001, float(block.get("h", 1) or 1))
    tokens = _tokenize_words(text, max_cjk)
    total = sum(t[1] for t in tokens) or 1
    out = []
    cum = 0.0
    for token, weight, is_word in tokens:
        if is_word and token.strip():
            out.append({
                "text": token,
                "x": x + w * cum / total,
                "y": y,
                "w": max(w * 0.005, w * weight / total),
                "h": h,
            })
        cum += weight
    return out


def blocks_to_words(blocks, max_cjk=2):
    """把任意引擎的行块统一成可点选的词块，并标出所属视觉行 row。"""
    out = []
    for b in blocks or []:
        words = split_line_to_words(b, max_cjk)
        if not words:
            continue
        for word in words:
            word["row"] = 0
            out.append(word)
    out.sort(key=lambda b: (b["y"], b["x"]))
    row = -1
    last_cy = None
    for word in out:
        cy = word["y"] + word["h"] / 2.0
        # 不同坐标系（像素 / 0~1）都成立：同行 y 中心几乎相同
        tol = max(1e-6, word["h"] * 0.6)
        if last_cy is None or abs(cy - last_cy) > tol:
            row += 1
        word["row"] = row
        last_cy = cy
    return out


def words_to_text(words, selected=None):
    """按行拼接词块文本；CJK 相邻不加空格，英文之间加空格。

    selected 为索引集合时只拼接被选中的词；None 表示全部。
    """
    rows = {}
    for i, word in enumerate(words or []):
        if selected is not None and i not in selected:
            continue
        rows.setdefault(int(word.get("row", 0)), []).append(
            (float(word.get("x", 0)), str(word.get("text", ""))))
    lines = []
    for row in sorted(rows):
        parts = sorted(rows[row])
        line = ""
        for _x, text in parts:
            if not text:
                continue
            line = _join_lines(line, text) if line else text
        if line:
            lines.append(line)
    return "\n".join(lines)


def assign_word_rows(words):
    """给词块补上视觉行号 ``row``（原地修改并返回同一列表）。

    Windows OCR 的原生 words 不带 row；没有它 words_to_text 会把所有词
    拼成一行，浮层也没法按行画高亮带。这里按 y 中心聚类兜底，算法与
    blocks_to_words 一致，保证「原图上的行」和「小窗口里汇总的行」相同。
    坐标系为像素或 0~1 归一化都成立（阈值随词高缩放）。
    """
    if not words:
        return words
    order = sorted(range(len(words)), key=lambda i: (
        float(words[i].get("y", 0)), float(words[i].get("x", 0))))
    row = -1
    last_cy = None
    for i in order:
        word = words[i]
        h = max(1e-6, float(word.get("h", 0) or 0))
        cy = float(word.get("y", 0) or 0) + h / 2.0
        tol = max(1e-6, h * 0.6)
        if last_cy is None or abs(cy - last_cy) > tol:
            row += 1
            last_cy = cy
        else:
            last_cy = (last_cy + cy) / 2.0
        word["row"] = row
    return words
