"""
OCR文字识别模块 - 支持 Windows 系统自带 和 EasyOCR 双引擎
"""
import os
import subprocess
import sys
import threading

from subprocess_util import pip_cmd, pip_process


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


def install_engine(engine):
    """兼容旧接口：复用带进度的实现，失败时能拿到真实错误输出。"""
    return install_engine_with_progress(engine)


def install_engine_with_progress(engine, on_progress=None):
    """通过 pip 安装指定 OCR 引擎，实时回调进度。"""
    packages = {
        'easy': 'easyocr',
        'windows': 'winrt-runtime winrt-Windows.Media.Ocr winrt-Windows.Globalization winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections',
    }
    pkg = packages.get(engine)
    if not pkg:
        return False, f"{engine} 无法安装"
    cmd, hint = pip_cmd(pkg.split())
    if cmd is None:
        return False, hint
    try:
        # pip_process 自带看门狗：网络/SSL 卡死时 kill 子进程，避免
        # readline() 永久阻塞让设置页停在「安装中…」。
        with pip_process(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True) as proc:
            last_lines = []
            while True:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if line:
                    last_lines.append(line.strip())
                    if on_progress:
                        on_progress(line)
        if proc.returncode == 0:
            return True, "安装成功"
        tail = '\n'.join(last_lines[-5:])
        return False, f"安装失败 (code {proc.returncode}): {tail}"
    except Exception as e:
        return False, f"安装失败: {e}"


# ── Windows 系统 OCR 后端 ─────────────────────────────────────

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
        self._engines = {}
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

    def recognize(self, image_path):
        engine = self._ensure_engine()
        try:
            import winrt.windows.graphics.imaging as wimg
            import winrt.windows.storage.streams as wss
            import cv2

            img = _imread_unicode(image_path)
            if img is None:
                raise RuntimeError(f"无法读取图片: {image_path}")
            h_img = img.shape[0]
            ret, buf = cv2.imencode(".png", img)
            data = buf.tobytes()
            stream = wss.InMemoryRandomAccessStream()
            stream.write_async(data).get()
            stream.seek(0)
            decoder = wimg.BitmapDecoder.create_async(stream).get()
            bitmap = decoder.get_software_bitmap_async().get()
            result = engine.recognize_async(bitmap).get()
            lines_with_y = []
            for line in result.lines:
                text = line.text.strip()
                if not text:
                    continue
                y = 0
                if line.words:
                    y = line.words[0].bounding_rect.y
                lines_with_y.append((text, y))
            if not lines_with_y:
                return ""
            lines_with_y.sort(key=lambda x: x[1])
            avg_h = max(1, (lines_with_y[-1][1] - lines_with_y[0][1]) / max(1, len(lines_with_y) - 1))
            parts = [lines_with_y[0][0]]
            for i in range(1, len(lines_with_y)):
                gap = lines_with_y[i][1] - lines_with_y[i-1][1]
                if gap > avg_h * 1.6:
                    parts.append("\n")
                parts.append(lines_with_y[i][0])
            return '\n'.join(parts)
        except Exception as e:
            raise RuntimeError(f"Windows OCR识别失败: {e}\n提示: 请在系统设置中安装对应语言包")

    def recognize_blocks(self, image_path):
        """返回带位置的行块 [{text,x,y,w,h}]，坐标为传入图片的原图像素。

        与 recognize() 并存：后者保持纯字符串接口，供主页 / 文档解析使用。
        """
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
            blocks = []
            for line in result.lines:
                text = (line.text or "").strip()
                if not text:
                    continue
                words = list(line.words or [])
                if words:
                    rects = [w.bounding_rect for w in words]
                    x = min(r.x for r in rects)
                    y = min(r.y for r in rects)
                    x2 = max(r.x + r.width for r in rects)
                    y2 = max(r.y + r.height for r in rects)
                else:
                    x = y = 0.0
                    x2 = y2 = 1.0
                blocks.append({"text": text, "x": float(x), "y": float(y),
                               "w": max(1.0, float(x2 - x)),
                               "h": max(1.0, float(y2 - y))})
            blocks.sort(key=lambda b: (b["y"], b["x"]))
            return blocks
        except Exception as e:
            raise RuntimeError(f"Windows OCR识别失败: {e}\n提示: 请在系统设置中安装对应语言包")


# ── EasyOCR 后端 ──────────────────────────────────────────────

EASY_MODEL_DIR = os.path.join(os.path.expanduser("~"), '.EasyOCR', 'model')


def _check_easy_model_exists(languages):
    """检查EasyOCR模型文件是否已存在"""
    if not os.path.exists(EASY_MODEL_DIR):
        return False
    if not os.path.exists(os.path.join(EASY_MODEL_DIR, 'craft_mlt_25k.pth')):
        return False
    lang_map = {
        'ch_sim': 'zh_sim_g2.pth',
        'en': 'en_g2.pth',
        'ja': 'ja_g2.pth',
        'ko': 'ko_g2.pth',
    }
    for lang in languages:
        model_name = lang_map.get(lang, f"{lang}_g2.pth")
        if os.path.exists(os.path.join(EASY_MODEL_DIR, model_name)):
            return True
    return False


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
            if not self.model_exists:
                print("EasyOCR: 开始下载模型文件...")
            self.reader = easyocr.Reader(self.languages, gpu=use_gpu,
                                          download_enabled=True)
            if not self.model_exists:
                print("EasyOCR: 模型下载完成！")
                self.model_exists = True
        return self.reader

    def recognize(self, image_path):
        try:
            reader = self._init_reader()
            img = _imread_unicode(image_path)
            if img is None:
                raise RuntimeError(f"无法读取图片: {image_path}")
            results = reader.readtext(img)
            if not results:
                return ""
            items = []
            for r in results:
                text = r[1].strip()
                if not text:
                    continue
                bbox = r[0]
                y = (bbox[0][1] + bbox[2][1]) / 2
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
        except Exception as e:
            msg = str(e)
            if "CUDA" in msg or "gpu" in msg.lower():
                raise RuntimeError(f"GPU加速不可用: {msg}\n原因: 显卡驱动可能需要更新，或显存不足")
            if "model" in msg.lower() or "download" in msg.lower():
                raise RuntimeError(f"OCR模型加载失败: {msg}\n原因: 模型文件可能损坏，请删除 ~/.EasyOCR/model 后重试")
            raise RuntimeError(f"EasyOCR识别失败: {msg}")

    def recognize_blocks(self, image_path):
        """返回带位置的文本块 [{text,x,y,w,h}]，坐标为传入图片的原图像素。"""
        try:
            reader = self._init_reader()
            img = _imread_unicode(image_path)
            if img is None:
                raise RuntimeError(f"无法读取图片: {image_path}")
            results = reader.readtext(img) or []
            blocks = []
            for r in results:
                text = (r[1] or "").strip()
                if not text:
                    continue
                pts = r[0]
                xs = [float(p[0]) for p in pts]
                ys = [float(p[1]) for p in pts]
                x, y = min(xs), min(ys)
                blocks.append({"text": text, "x": x, "y": y,
                               "w": max(1.0, max(xs) - x),
                               "h": max(1.0, max(ys) - y)})
            blocks.sort(key=lambda b: (b["y"], b["x"]))
            return blocks
        except Exception as e:
            raise RuntimeError(f"EasyOCR识别失败: {e}")


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
