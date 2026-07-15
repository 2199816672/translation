"""
OCR文字识别模块 - 支持 Windows 系统自带 和 EasyOCR 双引擎
"""
import os
import subprocess
import sys

try:
    import torch
except (OSError, ModuleNotFoundError):
    pass


def check_engine_available(engine):
    """检查指定OCR引擎是否可用"""
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


def install_engine(engine):
    """通过pip安装指定OCR引擎"""
    packages = {
        'easy': 'easyocr',
        'windows': 'winrt-runtime winrt-Windows.Media.Ocr winrt-Windows.Globalization winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections',
    }
    pkg = packages.get(engine)
    if not pkg:
        return False, f"{engine} 无法安装"
    try:
        subprocess.check_call(
            [sys.executable, '-m', 'pip', 'install'] + pkg.split(),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        return True, "安装成功"
    except subprocess.CalledProcessError as e:
        return False, f"安装失败: {e.stderr.decode(errors='ignore')[-200:]}"


def install_engine_with_progress(engine, on_progress=None):
    """通过pip安装指定OCR引擎，实时回调进度"""
    packages = {
        'easy': 'easyocr',
        'windows': 'winrt-runtime winrt-Windows.Media.Ocr winrt-Windows.Globalization winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections',
    }
    pkg = packages.get(engine)
    if not pkg:
        return False, f"{engine} 无法安装"
    cmd = [sys.executable, '-m', 'pip', 'install'] + pkg.split()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
        )
        last_lines = []
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                last_lines.append(line.strip())
                if on_progress:
                    on_progress(line)
        proc.wait()
        if proc.returncode == 0:
            return True, "安装成功"
        else:
            tail = '\n'.join(last_lines[-5:])
            return False, f"安装失败 (code {proc.returncode}): {tail}"
    except Exception as e:
        return False, f"安装失败: {e}"


# ── Windows 系统 OCR 后端 ─────────────────────────────────────

class WindowsOCRRecognizer:
    def __init__(self, languages=None):
        if languages is None:
            languages = ['ch_sim', 'en']
        self.languages = languages
        self._engine = None
        self._init_engine()

    def _init_engine(self):
        import winrt.windows.media.ocr as wocr
        import winrt.windows.globalization as wgl
        self._wocr = wocr
        lang_code = 'zh-cn' if 'ch_sim' in self.languages else 'en'
        lang = wgl.Language(lang_code)
        self._engine = wocr.OcrEngine.try_create_from_language(lang)
        if not self._engine:
            self._engine = wocr.OcrEngine.try_create_from_language(wgl.Language('zh-cn'))
        if self._engine:
            print(f"Windows OCR 已初始化: {lang_code}")
        else:
            print("Windows OCR 初始化失败，语言可能不受支持")

    def recognize(self, image_path):
        if self._engine is None:
            raise RuntimeError("Windows OCR 引擎未初始化，请检查系统是否安装了对应语言包")
        try:
            import winrt.windows.graphics.imaging as wimg
            import winrt.windows.storage.streams as wss
            import cv2

            img = cv2.imread(str(image_path))
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
            result = self._engine.recognize_async(bitmap).get()
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
        self.model_exists = _check_easy_model_exists(languages)
        if self.model_exists:
            print("EasyOCR: 模型已存在，跳过下载")
        else:
            print("EasyOCR: 正在初始化模型，首次运行需要下载模型文件，请稍候...")

    def _init_reader(self):
        if self.reader is None:
            import easyocr
            if not self.model_exists:
                print("EasyOCR: 开始下载模型文件...")
            self.reader = easyocr.Reader(self.languages, gpu=True, download_enabled=True)
            if not self.model_exists:
                print("EasyOCR: 模型下载完成！")
                self.model_exists = True
        return self.reader

    def recognize(self, image_path):
        try:
            reader = self._init_reader()
            results = reader.readtext(image_path)
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


# ── 全局单例 ──────────────────────────────────────────────────

_ocr_instance = None
_current_engine = None


def get_ocr_instance(languages=None, engine=None):
    """获取OCR实例（单例模式，支持引擎切换）"""
    global _ocr_instance, _current_engine
    if languages is None:
        languages = ['ch_sim', 'en']
    if engine is None:
        from config import OCR_ENGINE
        engine = OCR_ENGINE
    if _ocr_instance is not None and _current_engine == engine:
        return _ocr_instance
    if engine == 'easy':
        _ocr_instance = EasyOCRRecognizer(languages)
    else:
        _ocr_instance = WindowsOCRRecognizer(languages)
    _current_engine = engine
    return _ocr_instance


def reset_ocr_instance():
    """重置OCR实例（切换引擎时调用）"""
    global _ocr_instance, _current_engine
    _ocr_instance = None
    _current_engine = None
