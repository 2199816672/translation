"""
OCR文字识别模块 - 支持 Windows 系统自带 和 EasyOCR 双引擎
"""
import copy
import hashlib
import os
import subprocess
import sys
import threading
import warnings

from pathlib import Path

from subprocess_util import pip_cmd, pip_process
from result_cache import get_cache, make_key


def _silence_third_party_warnings():
    """只静默 EasyOCR / torch 在 CPU 上必定刷出的两条无害 UserWarning。

    这两条来自 torch 自身（量化 API 弃用提示、无 GPU 时 pin_memory 无效），
    既不是本程序报错，也不影响识别结果，但每次启动都会在控制台刷屏，
    容易让用户误以为出错了。其它 warning / 错误一律原样保留；
    设环境变量 TT_SHOW_LIB_NOTICES=1 可关闭过滤。
    """
    if os.environ.get("TT_SHOW_LIB_NOTICES") == "1":
        return
    warnings.filterwarnings(
        "ignore", message=r"torch\.quantize_per_tensor.*deprecated",
        category=UserWarning)
    warnings.filterwarnings(
        "ignore",
        message=r"'pin_memory' argument is set as true but no accelerator is found",
        category=UserWarning)


_silence_third_party_warnings()


def check_engine_available(engine):
    """检查指定OCR引擎是否可用"""
    # 设置页刚在线装完组件时，主进程的目录缓存可能还没刷新
    from app_paths import refresh_user_libs
    refresh_user_libs()
    if engine == 'windows':
        try:
            import winrt.windows.media.ocr
            return True
        except ImportError:
            return False
    elif engine == 'easy':
        try:
            import easyocr
            return True
        except ImportError:
            return False
    return False


def _imread_unicode(path):
    """cv2.imread 在 Windows 走 ANSI fopen，中文路径会直接返回 None。"""
    import cv2
    import numpy as np
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None


def _image_digest(image_path):
    """按文件内容算指纹：同一张图（哪怕路径不同）只识别一次。

    分块读避免把几十 MB 的截图整个读进内存；哈希耗时远小于一次 OCR 推理。
    """
    try:
        digest = hashlib.sha256()
        with Path(str(image_path)).open("rb") as fh:
            for block in iter(lambda: fh.read(256 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return ""


def _cached_scan(recognizer, image_path, compute, kind):
    """引擎扫描结果缓存：recognize / recognize_blocks / recognize_words 共用。

    截图翻译里同一张图常被依次问「文本 / 行坐标 / 词坐标」，实时翻译还会
    反复抓到相同画面；缓存后这些重复调用都不再触发 winrt / EasyOCR 推理。
    值只存纯 Python 结构，跨线程安全（winrt 的 COM 对象本身不能跨线程）。
    """
    digest = _image_digest(image_path)
    cache = get_cache("ocr", max_entries=150)
    key = (make_key("o1", type(recognizer).__name__, kind,
                    tuple(getattr(recognizer, "languages", []) or []), digest)
           if digest else None)
    if key is not None:
        hit = cache.get(key)
        if hit is not None:
            return copy.deepcopy(hit)
    value = compute()
    if key is not None and value is not None:
        cache.put(key, value)
    return copy.deepcopy(value)


def install_engine(engine):
    """兼容旧接口：复用带进度的实现，失败时能拿到真实错误输出。"""
    return install_engine_with_progress(engine)


def install_engine_with_progress(engine, on_progress=None, cancel=None):
    """通过 pip 安装指定 OCR 引擎，实时回调进度。

    ``cancel`` 是可选的 ``threading.Event``：用户在设置页点「取消安装」时
    置位，看门狗随即 kill 掉 pip 子进程，读取循环立刻退出并返回「已取消」，
    不会留下一个装到一半却仍显示「安装中」的界面。
    """
    from subprocess_util import (CANCELLED_TEXT, pip_cmd,
                                 pip_install_with_mirrors)
    packages = {
        'easy': 'easyocr',
        'windows': 'winrt-runtime winrt-Windows.Media.Ocr winrt-Windows.Globalization winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections',
    }
    pkg = packages.get(engine)
    if not pkg:
        return False, f"{engine} 无法安装"
    if cancel is not None and cancel.is_set():
        return False, CANCELLED_TEXT
    cmd, hint = pip_cmd(pkg.split())
    if cmd is None:
        return False, hint

    def on_attempt(i, total, name):
        # 让用户看见「正在换源」，而不是 403 之后长时间黑屏
        if on_progress:
            try:
                on_progress(f"[{i}/{total}] 正在使用{name}安装…\n")
            except Exception:
                pass

    label = 'winrt OCR 组件' if engine == 'windows' else 'EasyOCR 引擎'
    # 以前不指定 -i，完全听用户 pip.ini 的；清华 / 其它失效镜像返回 403
    # 时安装必然失败。这里显式换源重试，失败也给出可照做的手动命令。
    return pip_install_with_mirrors(
        pkg.split(), on_progress=on_progress, cancel=cancel,
        on_attempt=on_attempt, label=label)


# ── Windows 系统 OCR 后端 ─────────────────────────────────────

class _ThreadLocalEngineCache:
    """按线程缓存 winrt OcrEngine：线程退出即自动释放 COM 引擎。

    实时翻译浮层每帧都新建一次性线程，用 {threading.get_ident(): engine}
    缓存会无限累积句柄；Windows 线程 ID 还会复用，复用时会命中上一个已销毁
    线程留下的、COM 单元已失效的引擎。threading.local 两个问题都不存在。
    这里刻意保留 dict 的 get / __setitem__ 接口，_ensure_engine 无需改动。
    """

    def __init__(self):
        self._local = threading.local()

    def get(self, _tid, default=None):
        return getattr(self._local, 'engine', default)

    def __setitem__(self, _tid, engine):
        self._local.engine = engine


class WindowsOCRRecognizer:
    # 用户所选语言（PaddleOCR 命名）→ Windows BCP-47 语言码
    _LANG_TO_BCP47 = {
        'ch_sim': 'zh-cn', 'ch_tra': 'zh-hant', 'en': 'en', 'ja': 'ja',
        'ko': 'ko', 'fr': 'fr', 'de': 'de', 'es': 'es', 'ru': 'ru',
        'pt': 'pt', 'it': 'it', 'ar': 'ar', 'th': 'th', 'vi': 'vi',
    }

    def __init__(self, languages=None):
        if languages is None:
            languages = ['ch_sim', 'en']
        self.languages = languages
        # winrt 的 OcrEngine 绑定创建它的线程的 COM 单元，跨线程调用会触发封送错误。
        # 因此不在构造时创建，改为按调用线程惰性创建并缓存（见 _ensure_engine）。
        self._lang_code = ''
        self._lang_codes = []
        self._engines = _ThreadLocalEngineCache()
        self._compute_codes()

    def _compute_codes(self):
        # 按用户所选语言，逐一尝试创建对应语言的引擎（日/韩/法等不再退化成英文）
        codes = []
        for lang in self.languages or ['ch_sim', 'en']:
            code = self._LANG_TO_BCP47.get(lang)
            if code and code not in codes:
                codes.append(code)
        if not codes:
            codes = ['en', 'zh-cn']
        self._lang_codes = codes

    def _ensure_engine(self):
        """按调用线程惰性创建并缓存 OcrEngine，保证「在哪用就在哪建」，
        规避 winrt 跨线程 COM 封送报错。"""
        import winrt.windows.media.ocr as wocr
        import winrt.windows.globalization as wgl
        tid = threading.get_ident()
        engine = self._engines.get(tid)
        if engine is not None:
            return engine
        chosen = None
        for code in self._lang_codes:
            eng = wocr.OcrEngine.try_create_from_language(wgl.Language(code))
            if eng:
                engine = eng
                chosen = code
                break
        # 兜底：连英文/中文都没有（极少见）再尝试一次英文
        if engine is None:
            eng = wocr.OcrEngine.try_create_from_language(wgl.Language('en'))
            if eng:
                engine = eng
                chosen = 'en'
        if engine is None:
            raise RuntimeError(
                "Windows OCR 引擎创建失败，请检查系统是否安装了对应语言包")
        self._engines[tid] = engine
        self._lang_code = chosen
        return engine

    # 三个对外接口（纯文本 / 行块 / 词块）共用一次 winrt 识别结果。
    # 归一化成纯 Python 结构后既能缓存，也避免 winrt COM 对象跨线程封送。
    _WORD_KEYS = ("text", "x", "y", "w", "h")

    def _scan_lines(self, image_path):
        """真正跑一次 winrt OCR，返回 [{text, words:[{...,x2,y2}]}]。"""
        engine = self._ensure_engine()
        try:
            import winrt.windows.graphics.imaging as wimg
            import winrt.windows.storage.streams as wss
            import cv2

            img = _imread_unicode(image_path)
            if img is None:
                raise RuntimeError(f"无法读取图片: {image_path}")
            ret, buf = cv2.imencode(".png", img)
            stream = wss.InMemoryRandomAccessStream()
            stream.write_async(buf.tobytes()).get()
            stream.seek(0)
            decoder = wimg.BitmapDecoder.create_async(stream).get()
            bitmap = decoder.get_software_bitmap_async().get()
            result = engine.recognize_async(bitmap).get()
            lines = []
            for line in result.lines:
                text = (line.text or "").strip()
                if not text:
                    continue
                words = []
                for word in (line.words or []):
                    word_text = (word.text or "").strip()
                    if not word_text:
                        continue
                    r = word.bounding_rect
                    x, y = float(r.x), float(r.y)
                    width, height = float(r.width), float(r.height)
                    words.append({
                        "text": word_text, "x": x, "y": y,
                        "w": max(1.0, width), "h": max(1.0, height),
                        "x2": x + width, "y2": y + height,
                    })
                lines.append({"text": text, "words": words})
            return lines
        except Exception as e:
            raise RuntimeError(f"Windows OCR识别失败: {e}\n提示: 请在系统设置中安装对应语言包")

    def _lines(self, image_path):
        return _cached_scan(self, image_path,
                            lambda: self._scan_lines(image_path), "lines")

    @classmethod
    def _word_blocks(cls, words):
        return [{k: w[k] for k in cls._WORD_KEYS} for w in words]

    def recognize(self, image_path):
        """纯文本接口，供主页 / 文档解析使用。"""
        lines_with_y = [
            (line["text"], line["words"][0]["y"] if line["words"] else 0.0)
            for line in self._lines(image_path)
        ]
        if not lines_with_y:
            return ""
        lines_with_y.sort(key=lambda x: x[1])
        avg_h = max(1, (lines_with_y[-1][1] - lines_with_y[0][1])
                    / max(1, len(lines_with_y) - 1))
        parts = [lines_with_y[0][0]]
        for i in range(1, len(lines_with_y)):
            gap = lines_with_y[i][1] - lines_with_y[i-1][1]
            if gap > avg_h * 1.6:
                parts.append("\n")
            parts.append(lines_with_y[i][0])
        return '\n'.join(parts)

    def recognize_blocks(self, image_path):
        """返回带位置的行块 [{text,x,y,w,h,words}]，坐标为原图像素。"""
        blocks = []
        for line in self._lines(image_path):
            words = line.get("words") or []
            if words:
                x = min(w["x"] for w in words)
                y = min(w["y"] for w in words)
                x2 = max(w["x2"] for w in words)
                y2 = max(w["y2"] for w in words)
            else:
                x = y = 0.0
                x2 = y2 = 1.0
            blocks.append({
                "text": line["text"], "x": float(x), "y": float(y),
                "w": max(1.0, float(x2 - x)),
                "h": max(1.0, float(y2 - y)),
                # 原生词坐标随行块一起返回，提取按词点选时不必再跑一次 OCR
                "words": self._word_blocks(words),
            })
        blocks.sort(key=lambda b: (b["y"], b["x"]))
        return blocks

    def recognize_words(self, image_path, blocks=None):
        """返回【词级】块 [{text,x,y,w,h}]，坐标为传入图片的原图像素。

        传入同一次识别的 blocks 时直接复用其中的 words 字段；没有词坐标的
        行块原样返回，由 text_segment.blocks_to_words 统一再切词。
        """
        if blocks is not None:
            out = []
            for b in blocks:
                inner = b.get("words") or []
                if inner:
                    out.extend(dict(w) for w in inner)
                else:
                    out.append(b)
            return out
        words = []
        for line in self._lines(image_path):
            words.extend(self._word_blocks(line.get("words") or []))
        if not words:
            return self.recognize_blocks(image_path)
        words.sort(key=lambda b: (b["y"], b["x"]))
        return words


# ── EasyOCR 后端 ──────────────────────────────────────────────

EASY_MODEL_DIR = os.path.join(os.path.expanduser("~"), '.EasyOCR', 'model')


def _check_easy_model_exists(languages):
    """EasyOCR 模型是否已存在（含用户手动导入与老版本默认目录）。"""
    import ocr_models
    directory = ocr_models.resolve_model_dir(languages)
    return ocr_models.has_required_models(directory, languages)


class EasyOCRRecognizer:
    def __init__(self, languages=None):
        if languages is None:
            languages = ['ch_sim', 'en']
        self.languages = languages
        self.reader = None
        self._reader_lock = threading.Lock()
        self.model_exists = _check_easy_model_exists(languages)
        if self.model_exists:
            print("EasyOCR: 模型已存在，跳过下载")
        else:
            print("EasyOCR: 正在初始化模型，首次运行需要下载模型文件，请稍候...")

    def _init_reader(self):
        if self.reader is not None:
            return self.reader
        # 多线程并发首次识别时，避免各自创建 Reader（重复模型下载 / 写同一目录冲突）
        with self._reader_lock:
            if self.reader is not None:
                return self.reader
            import easyocr
            # 仅在有 CUDA 时启用 GPU，否则无显卡机器会直接报错
            try:
                import torch
                use_gpu = torch.cuda.is_available()
            except Exception:
                use_gpu = False
            import ocr_models
            model_dir = ocr_models.resolve_model_dir(self.languages)
            # 手动下载 / 导入的模型齐全时彻底关掉联网下载，国内网络不会再
            # 卡在 easyocr 自动拉模型上；缺文件时仍保留自动下载兜底。
            offline_ready = ocr_models.models_complete(
                model_dir, self.languages)
            if not self.model_exists:
                print("EasyOCR: 开始下载模型文件...")
            try:
                self.reader = easyocr.Reader(
                    self.languages, gpu=use_gpu,
                    model_storage_directory=model_dir,
                    download_enabled=not offline_ready)
            except TypeError:
                # 极旧版 easyocr 不接受这两个参数，退回原行为
                self.reader = easyocr.Reader(self.languages, gpu=use_gpu)
            if not self.model_exists:
                print("EasyOCR: 模型下载完成！")
                self.model_exists = True
        return self.reader

    def _scan_results(self, image_path):
        """真正跑一次 EasyOCR，转成可缓存的纯 Python 结构。

        recognize()（纯文本）与 recognize_blocks()（行坐标）共用这一次推理，
        以前两个接口各跑一遍 readtext，是截图翻译里最贵的一处重复计算。
        """
        try:
            reader = self._init_reader()
            img = _imread_unicode(image_path)
            if img is None:
                raise RuntimeError(f"无法读取图片: {image_path}")
            results = reader.readtext(img) or []
            return [{"text": r[1],
                     "pts": [[float(p[0]), float(p[1])] for p in r[0]]}
                    for r in results]
        except Exception as e:
            msg = str(e)
            if "CUDA" in msg or "gpu" in msg.lower():
                raise RuntimeError(f"GPU加速不可用: {msg}\n原因: 显卡驱动可能需要更新，或显存不足")
            if "model" in msg.lower() or "download" in msg.lower():
                raise RuntimeError(f"OCR模型加载失败: {msg}\n原因: 模型文件可能损坏，请删除 ~/.EasyOCR/model 后重试")
            raise RuntimeError(f"EasyOCR识别失败: {msg}")

    def _results(self, image_path):
        return _cached_scan(self, image_path,
                            lambda: self._scan_results(image_path), "results")

    def recognize(self, image_path):
        items = []
        for r in self._results(image_path):
            text = (r["text"] or "").strip()
            if not text:
                continue
            pts = r["pts"]
            y = (pts[0][1] + pts[2][1]) / 2
            items.append((text, y))
        if not items:
            return ""
        items.sort(key=lambda x: x[1])
        avg_h = max(1, (items[-1][1] - items[0][1]) / max(1, len(items) - 1))
        parts = [items[0][0]]
        for i in range(1, len(items)):
            gap = items[i][1] - items[i-1][1]
            if gap > avg_h * 1.6:
                parts.append("\n")
            parts.append(items[i][0])
        return '\n'.join(parts)

    def recognize_blocks(self, image_path):
        """返回带位置的文本块 [{text,x,y,w,h}]，坐标为传入图片的原图像素。"""
        blocks = []
        for r in self._results(image_path):
            text = (r["text"] or "").strip()
            if not text:
                continue
            pts = r["pts"]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x, y = min(xs), min(ys)
            blocks.append({"text": text, "x": x, "y": y,
                           "w": max(1.0, max(xs) - x),
                           "h": max(1.0, max(ys) - y)})
        blocks.sort(key=lambda b: (b["y"], b["x"]))
        return blocks

    def recognize_words(self, image_path, blocks=None):
        """EasyOCR 只给行级 bbox，词级坐标由 text_segment.blocks_to_words
        按显示宽度分摊；这里保持接口与其它引擎一致，返回原始行块。
        传入 blocks 时直接复用，不重复跑一次 readtext。
        """
        if blocks is not None:
            return list(blocks)
        return self.recognize_blocks(image_path)


# ── 全局单例 ──────────────────────────────────────────────────

_ocr_instance = None
_current_engine = None
_current_languages = None


def get_ocr_instance(languages=None, engine=None):
    """获取OCR实例（单例模式，支持引擎 / 语言切换）"""
    global _ocr_instance, _current_engine, _current_languages
    if languages is None:
        languages = ['ch_sim', 'en']
    languages = list(languages) or ['ch_sim', 'en']
    if engine is None:
        # 主窗口已按「配置 + 可用性 fallback」解析过引擎并建好实例，直接复用：
        # 若这里重新读配置，配置里那个未安装的引擎会让单例被反复重建
        # （EasyOCR 每次都要重新加载模型）。设置页切换引擎会 reset 单例。
        if _ocr_instance is not None and _current_languages == languages:
            return _ocr_instance
        from config import OCR_ENGINE
        # 尚无实例时才遵循用户在「设置 → OCR 引擎」里的选择，而不是写死常量
        try:
            from config import load_user_config
            engine = str(load_user_config().get('ocr_engine') or OCR_ENGINE)
        except Exception:
            engine = OCR_ENGINE
        if not check_engine_available(engine):
            for fallback in ('windows', 'easy'):
                if fallback != engine and check_engine_available(fallback):
                    engine = fallback
                    break
    # 语言也必须参与缓存判定：主窗口传用户选的 OCR_LANGUAGES，文档图片解析
    # 用默认语言，只看 engine 会让两个调用方共用一个语言不符的实例。
    if (_ocr_instance is not None and _current_engine == engine
            and _current_languages == languages):
        return _ocr_instance
    if engine == 'easy':
        _ocr_instance = EasyOCRRecognizer(languages)
    else:
        _ocr_instance = WindowsOCRRecognizer(languages)
    _current_engine = engine
    _current_languages = languages
    return _ocr_instance


def reset_ocr_instance():
    """重置OCR实例（切换引擎 / 语言时调用）"""
    global _ocr_instance, _current_engine, _current_languages
    _ocr_instance = None
    _current_engine = None
    _current_languages = None
