"""
翻译功能模块 - 支持多种翻译API
"""
from deep_translator import GoogleTranslator, DeeplTranslator, BaiduTranslator, MyMemoryTranslator
from deep_translator.exceptions import RequestError
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import user_config, TRANSLATION_APIS, DEFAULT_TRANSLATION_API


class Translator:
    def __init__(self, source_lang='auto', target_lang='zh-CN', api_type=None):
        """
        初始化翻译器
        :param source_lang: 源语言，'auto'表示自动检测
        :param target_lang: 目标语言
        :param api_type: 翻译API类型
        """
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.api_type = api_type or user_config.get('translation_api', DEFAULT_TRANSLATION_API)
        self.translator = None
        
        # DeepL API映射
        self.deepl_lang_map = {
            'zh-CN': 'ZH',
            'en': 'EN-US',
            'ja': 'JA',
            'ko': 'KO',
            'fr': 'FR',
            'de': 'DE',
            'es': 'ES',
            'ru': 'RU',
        }
        
        # MyMemory API映射
        self.mymemory_lang_map = {
            'zh-CN': 'zh-CN',
            'en': 'en',
            'ja': 'ja',
            'ko': 'ko',
            'fr': 'fr',
            'de': 'de',
            'es': 'es',
            'ru': 'ru',
        }
        
        # 百度翻译API映射
        self.baidu_lang_map = {
            'zh-CN': 'zh',
            'en': 'en',
            'ja': 'jp',
            'ko': 'kor',
            'fr': 'fra',
            'de': 'de',
            'es': 'spa',
            'ru': 'ru',
        }

    def _init_translator(self):
        """延迟初始化翻译器"""
        if self.translator is None:
            if self.api_type == 'google_free' or self.api_type == 'google':
                # Google翻译器
                source = 'auto' if self.source_lang == 'auto' else self.source_lang
                self.translator = GoogleTranslator(
                    source=source,
                    target=self.target_lang
                )
            elif self.api_type == 'deepl':
                # DeepL翻译器
                target = self.deepl_lang_map.get(self.target_lang, 'ZH')
                api_key = user_config.get('deepl_api_key', '')
                if api_key:
                    self.translator = DeeplTranslator(api_key=api_key, target=target)
                else:
                    print("DeepL API需要API密钥，请在设置中配置")
                    self.translator = GoogleTranslator(source='auto', target=self.target_lang)
            elif self.api_type == 'youdao':
                # 使用MyMemory作为替代（免费API）
                target = self.mymemory_lang_map.get(self.target_lang, 'zh-CN')
                self.translator = MyMemoryTranslator(source='auto', target=target)
            elif self.api_type == 'baidu':
                # 百度翻译
                appid = user_config.get('baidu_appid', '')
                secret = user_config.get('baidu_secret', '')
                target = self.baidu_lang_map.get(self.target_lang, 'zh')
                if appid and secret:
                    self.translator = BaiduTranslator(api_key=appid, secret_key=secret, target=target)
                else:
                    print("百度翻译需要AppID和SecretKey，请在设置中配置")
                    self.translator = GoogleTranslator(source='auto', target=self.target_lang)
            else:
                # 默认使用Google
                self.translator = GoogleTranslator(
                    source='auto',
                    target=self.target_lang
                )
        return self.translator

    def translate(self, text):
        """
        翻译文本
        :param text: 要翻译的文本
        :return: 翻译后的文本
        """
        if not text or not text.strip():
            return ""
        
        try:
            translator = self._init_translator()
            # 分段翻译（处理长文本）
            paragraphs = text.split('\n')
            translated_paragraphs = []
            
            for paragraph in paragraphs:
                if paragraph.strip():
                    try:
                        translated = translator.translate(paragraph)
                        translated_paragraphs.append(translated)
                    except RequestError as e:
                        print(f"翻译请求失败: {e}")
                        translated_paragraphs.append(paragraph)  # 保留原文
                    except Exception as e:
                        print(f"翻译错误: {e}")
                        translated_paragraphs.append(paragraph)
                else:
                    translated_paragraphs.append("")
            
            return '\n'.join(translated_paragraphs)
        except Exception as e:
            print(f"翻译失败: {e}")
            return text  # 返回原文

    def set_target_lang(self, target_lang):
        """设置目标语言"""
        self.target_lang = target_lang
        self.translator = None  # 重置翻译器

    def set_source_lang(self, source_lang):
        """设置源语言"""
        self.source_lang = source_lang
        self.translator = None  # 重置翻译器
    
    def set_api_type(self, api_type):
        """设置翻译API类型"""
        self.api_type = api_type
        self.translator = None  # 重置翻译器


# 创建全局翻译器实例
_translator_instance = None


def get_translator_instance(source_lang='auto', target_lang='zh-CN', api_type=None):
    """获取翻译器实例（单例模式）"""
    global _translator_instance
    if _translator_instance is None:
        _translator_instance = Translator(source_lang, target_lang, api_type)
    else:
        if api_type:
            _translator_instance.set_api_type(api_type)
    return _translator_instance