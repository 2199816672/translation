"""
OCR文字识别模块
"""
import easyocr
import os
from PIL import Image
import numpy as np

# 设置EasyOCR模型下载目录
MODEL_DIR = os.path.join(os.path.expanduser("~"), '.EasyOCR', 'model')


def check_model_exists(languages=['ch_sim', 'en']):
    """检查模型文件是否已存在"""
    if not os.path.exists(MODEL_DIR):
        return False
    
    # 检测模型是必须的
    if not os.path.exists(os.path.join(MODEL_DIR, 'craft_mlt_25k.pth')):
        return False
    
    # EasyOCR实际使用的模型文件名映射
    lang_map = {
        'ch_sim': 'zh_sim_g2.pth',
        'en': 'en_g2.pth',
        'ja': 'ja_g2.pth',
        'ko': 'ko_g2.pth'
    }
    
    # 检查是否至少有一个识别模型存在（不是必须检查所有语言）
    has_recognizer = False
    for lang in languages:
        model_name = lang_map.get(lang, f"{lang}_g2.pth")
        if os.path.exists(os.path.join(MODEL_DIR, model_name)):
            has_recognizer = True
            break
    
    return has_recognizer


class OCRRecognizer:
    def __init__(self, languages=['ch_sim', 'en']):
        """
        初始化OCR识别器
        :param languages: 支持的语言列表，默认支持简体中文和英文
        """
        self.languages = languages
        self.reader = None
        self.model_exists = check_model_exists(languages)
        
        if self.model_exists:
            print("OCR模型已存在，跳过下载")
        else:
            print("正在初始化OCR模型，首次运行需要下载模型文件，请稍候...")

    def _init_reader(self):
        """延迟初始化reader"""
        if self.reader is None:
            if not self.model_exists:
                print("开始下载OCR模型文件（需要一些时间）...")
            
            self.reader = easyocr.Reader(self.languages, gpu=True, download_enabled=True)
            
            if not self.model_exists:
                print("OCR模型下载完成！")
                self.model_exists = True
        return self.reader

    def recognize(self, image_path):
        """
        识别图片中的文字
        :param image_path: 图片路径
        :return: 识别出的文字字符串
        """
        try:
            reader = self._init_reader()
            results = reader.readtext(image_path)
            text_list = [result[1] for result in results]
            full_text = '\n'.join(text_list)
            return full_text
        except Exception as e:
            msg = str(e)
            if "CUDA" in msg or "gpu" in msg.lower():
                raise RuntimeError(f"GPU加速不可用: {msg}\n原因: 显卡驱动可能需要更新，或显存不足")
            if "model" in msg.lower() or "download" in msg.lower():
                raise RuntimeError(f"OCR模型加载失败: {msg}\n原因: 模型文件可能损坏，请删除 ~/.EasyOCR/model 后重试")
            raise RuntimeError(f"OCR识别失败: {msg}")

    def recognize_with_positions(self, image_path):
        """
        识别图片中的文字并返回位置信息
        :param image_path: 图片路径
        :return: 包含文字和位置信息的列表 [(box, text, confidence), ...]
        """
        try:
            reader = self._init_reader()
            results = reader.readtext(image_path)
            return results
        except Exception as e:
            print(f"OCR识别失败: {e}")
            return []


# 创建全局OCR实例
_ocr_instance = None


def get_ocr_instance(languages=['ch_sim', 'en']):
    """获取OCR实例（单例模式）"""
    global _ocr_instance
    if _ocr_instance is None:
        _ocr_instance = OCRRecognizer(languages)
    return _ocr_instance