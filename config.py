"""
配置文件
"""
import os
import json

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 截图保存目录
SCREENSHOT_DIR = os.path.join(BASE_DIR, "screenshots")
if not os.path.exists(SCREENSHOT_DIR):
    os.makedirs(SCREENSHOT_DIR)

# OCR配置
OCR_LANGUAGES = ['ch_sim', 'en']  # 支持简体中文和英文

# 翻译配置
TRANSLATE_SOURCE_LANG = 'auto'  # 自动检测源语言
TRANSLATE_TARGET_LANG = 'zh-CN'  # 目标语言：中文

# 支持的目标语言
SUPPORTED_TARGET_LANGS = {
    '中文': 'zh-CN',
    '英文': 'en',
    '日文': 'ja',
    '韩文': 'ko',
    '法文': 'fr',
    '德文': 'de',
    '西班牙文': 'es',
    '俄文': 'ru',
}

# 翻译API配置
TRANSLATION_APIS = {
    'Google (免费)': 'google_free',
    'Google': 'google',
    'DeepL': 'deepl',
    'MyMemory (免费)': 'youdao',
    '百度翻译': 'baidu',
}

# 默认API设置
DEFAULT_TRANSLATION_API = 'google_free'

# 用户配置保存路径
CONFIG_FILE = os.path.join(BASE_DIR, "user_config.json")

def load_user_config():
    """加载用户配置"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_user_config(config):
    """保存用户配置"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存配置失败: {e}")
        return False

# 加载保存的配置
user_config = load_user_config()

# GUI配置
WINDOW_TITLE = "截图翻译工具"
WINDOW_SIZE = "900x700"
FONT_FAMILY = "Microsoft YaHei"
FONT_SIZE = 12