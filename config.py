"""
配置文件
"""
import os
import json
import shutil
import threading
from pathlib import Path

from app_paths import APP_DIR, config_path, screenshots_dir, resource_path

# 可写的用户数据目录。源码运行时是项目根目录；PyInstaller 打包后是 exe 同目录
# （只读安装目录时自动降级到 %LOCALAPPDATA%\TranslationTool）。
# 保留 BASE_DIR 这个名字是因为其它模块一直在 import 它。
BASE_DIR = str(APP_DIR)

# 截图保存目录（启动时自动创建，失败也不让程序崩）
SCREENSHOT_DIR = str(screenshots_dir())


def _coerce(value, default):
    """把配置里的值转换成默认值期望的类型。

    手工编辑 / 旧版本遗留的非法类型（数字写成 "abc"、布尔写成 null）不应该让
    界面构建或后台线程崩溃，转换失败一律回退默认值。
    """
    if value is None:
        return default
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "是")
        return bool(value)
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return default
    if isinstance(default, float):
        if isinstance(value, bool):
            return default
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        if result != result or result in (float("inf"), float("-inf")):
            return default
        return result
    if isinstance(default, str):
        if isinstance(value, str):
            return value
        if isinstance(value, (bool, dict, list)):
            return default
        return str(value)
    if isinstance(default, (list, tuple)):
        if value.__class__ is not default.__class__:
            return default.__class__(default)
        return value
    return value


def merged_settings(defaults: dict, cfg: dict = None) -> dict:
    """按默认值表合并磁盘配置，并对每个键做类型校验。"""
    raw = cfg if cfg is not None else load_user_config()
    merged = dict(defaults)
    for key, default in defaults.items():
        if key in raw:
            merged[key] = _coerce(raw[key], default)
    return merged

# OCR配置
OCR_LANGUAGES = ['ch_sim', 'en']  # 支持简体中文和英文
OCR_ENGINE = 'windows'  # 默认OCR引擎：windows 或 easy
SUPPORTED_OCR_ENGINES = {
    'windows': 'Windows 系统自带',
    'easy': 'EasyOCR (推荐，识别更准)',
}

# 翻译配置
TRANSLATE_SOURCE_LANG = 'auto'  # 自动检测源语言
TRANSLATE_TARGET_LANG = 'zh-CN'  # 目标语言：中文

# 支持的目标语言
SUPPORTED_TARGET_LANGS = {
    '中文': 'zh-CN',
    '中文(繁体)': 'zh-TW',
    '英文': 'en',
    '日文': 'ja',
    '韩文': 'ko',
    '法文': 'fr',
    '德文': 'de',
    '西班牙文': 'es',
    '俄文': 'ru',
}

# 支持的源语言
SUPPORTED_SOURCE_LANGS = {
    '自动检测': 'auto',
    '中文': 'zh-CN',
    '中文(繁体)': 'zh-TW',
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
    # 免费 — 无需密钥
    'Bing 微软 (免费)': 'bing_free',
    'MyMemory (免费)': 'mymemory_free',
    'Google (免费)': 'google_free',
    # 付费/需密钥
    'DeepL (需密钥)': 'deepl',
    '百度翻译 (需密钥)': 'baidu',
    '腾讯翻译 (需密钥)': 'tencent',
    '火山翻译 (需密钥)': 'volcengine',
}

# 免费API集合，用于判断是否需要密钥
FREE_APIS = {
    'bing_free', 'mymemory_free', 'google_free',
}

# 默认API设置
DEFAULT_TRANSLATION_API = 'bing_free'

# 免费 API 可选密钥配置键
FREE_API_KEY_FIELDS = {
    'bing_free': 'bing_azure_key',
    'mymemory_free': 'mymemory_api_key',
    'google_free': 'google_cloud_key',
}

# 语音识别默认设置
DEFAULT_ASR_SETTINGS = {
    'asr_vad_enabled': True,     # 人声检测（过滤静音/纯音乐片段）
    'asr_vad_threshold': 0.5,    # 人声检测阈值 0.1~0.9（越低越宽松）
    'asr_beam_size': 5,          # 解码宽度（1 快速 / 5 标准 / 10 精确）
    'asr_stream_enabled': True,  # 实时显示识别结果（边识别边出字幕）
    'asr_device': 'auto',        # 计算设备：auto / cpu / gpu
}

# AI 润色通道（用于把译文 / 字幕润色得更通顺）
AI_POLISH_PROVIDERS = {
    '免费公共通道 (无需密钥)': 'pollinations',
    'DeepSeek 网页版 (免费，需 Token)': 'deepseek_web',
    '豆包 网页版 (免费，需 Cookie)': 'doubao_web',
    '自定义 API (OpenAI 兼容)': 'custom',
}

# AI 润色默认设置
DEFAULT_AI_POLISH_SETTINGS = {
    'ai_polish_provider': 'pollinations',
    'ai_polish_deepseek_token': '',
    'ai_polish_doubao_cookie': '',
    'ai_polish_api_url': '',
    'ai_polish_api_key': '',
    'ai_polish_model': '',
}

# 实时翻译默认设置
DEFAULT_LIVE_SETTINGS = {
    'live_interval_ms': 1200,   # 刷新间隔（毫秒）
    'live_show_source': False,  # 浮窗默认是否显示原文
}

# 实时字幕：音频来源 / 识别引擎 / 显示模式
SUBTITLE_AUDIO_SOURCES = {
    '麦克风': 'mic',
    '电脑内部声音': 'loopback',
}
SUBTITLE_ENGINES = {
    '本地模型（免费下载 · 离线 · 最快）': 'local',
    '在线 API（自备密钥）': 'api',
    '免费本地服务（无需密钥）': 'local_service',
}
SUBTITLE_DISPLAY_MODES = {
    '原文 + 译文': 'both',
    '仅译文': 'translation',
    '仅原文': 'source',
}

# 实时字幕默认设置
DEFAULT_SUBTITLE_SETTINGS = {
    'subtitle_audio_source': 'mic',       # mic / loopback（默认麦克风，开箱即用）
    'subtitle_audio_device': '',          # 空 = 默认设备
    'subtitle_engine': 'local',           # local / api / local_service
    'subtitle_model': 'tiny',             # 实时默认最快的多语言模型
    'subtitle_api_url': '',
    'subtitle_api_key': '',
    'subtitle_api_model': 'whisper-1',
    'subtitle_language': 'auto',
    'subtitle_translate': True,
    'subtitle_target_lang': 'zh-CN',
    'subtitle_display_mode': 'both',
    'subtitle_window_ms': 2500,           # 最长片段（毫秒），遇停顿会提前切句
    'subtitle_silence_threshold': 0.008,  # 静音能量阈值，越小越灵敏
    'subtitle_font_size': 22,
    'subtitle_max_lines': 3,
    'subtitle_bg_alpha': 190,
    'subtitle_click_through': False,
    'subtitle_device': 'auto',            # 计算设备 auto / cpu / gpu
    # 字幕浮层位置 [x, y, w, h]，空 = 首次启动时自动放在屏幕下方居中。
    # 用户拖动 / 缩放后会写回，下次启动沿用。
    'subtitle_region': [],
}

# 用户配置保存路径
CONFIG_FILE = str(config_path())


def load_user_config():
    """加载用户配置；文件损坏时先备份，避免下次保存把真实配置永久覆盖。"""
    path = Path(CONFIG_FILE)
    if not path.exists():
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("配置根节点不是对象")
        return data
    except (json.JSONDecodeError, OSError, ValueError, UnicodeError) as exc:
        print(f"配置文件无法读取，已备份并使用默认值: {exc}")
        try:
            backup = path.with_name(path.name + ".broken")
            if backup.exists():
                backup.unlink()
            shutil.copy2(path, backup)
        except OSError:
            pass
        return {}

_config_lock = threading.Lock()


def save_user_config(config):
    """增量保存用户配置：读盘合并后原子写回，并原地刷新全局快照。

    调用方常持有启动时的整份快照，若直接整份覆盖会抹掉其它页面刚写入的键。
    使用临时文件 + os.replace 做原子替换，避免并发读写 / 崩溃时写坏文件；
    加全局锁防止多线程同时保存互相覆盖（如设置页保存与实时字幕保存撞车）。
    """
    global user_config
    path = Path(CONFIG_FILE).resolve()
    # 只要求写在同一数据目录内（防止调用方误传任意路径），不要求等于源码目录：
    # 安装版可能把数据目录降级到 %LOCALAPPDATA%。
    if path.parent != APP_DIR.resolve() or not path.parent.is_dir():
        raise RuntimeError("非法的配置路径")
    with _config_lock:
        try:
            merged = load_user_config()
            merged.update(config or {})
            tmp_path = path.with_name(path.name + ".tmp")
            tmp_path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding='utf-8')
            os.replace(tmp_path, path)  # 原子替换，避免半写文件损坏
            # 原地更新：让 `from config import user_config` 的既有引用同步看到新值
            user_config.clear()
            user_config.update(merged)
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False


def get_asr_settings():
    """读取语音识别设置（每次从磁盘读取，确保设置页修改实时生效）。"""
    return merged_settings(DEFAULT_ASR_SETTINGS)


def get_ai_polish_settings():
    """读取 AI 润色设置（每次从磁盘读取，设置保存后立即生效）。"""
    return merged_settings(DEFAULT_AI_POLISH_SETTINGS)


def get_live_settings():
    """读取实时翻译设置。"""
    return merged_settings(DEFAULT_LIVE_SETTINGS)


def get_subtitle_settings():
    """读取实时字幕设置（每次从磁盘读，设置页保存后立即生效）。"""
    return merged_settings(DEFAULT_SUBTITLE_SETTINGS)

# 加载保存的配置
user_config = load_user_config()

APP_VERSION = "1.5.1"
BILIBILI_DYNAMIC_URL = "https://space.bilibili.com/382966397/dynamic"
GITHUB_REPO = "2199816672/translation"

WINDOW_TITLE = "翻译工具"
WINDOW_SIZE = "900x700"
FONT_FAMILY = "Microsoft YaHei"
FONT_SIZE = 12