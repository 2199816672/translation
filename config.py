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
    'Google GTX (免费)': 'google_gtx_free',
    'LibreTranslate (免费)': 'libretranslate_free',
    '有道翻译 (免费)': 'youdao_free',
    # 付费/需密钥
    'DeepL (需密钥)': 'deepl',
    '百度翻译 (需密钥)': 'baidu',
    '腾讯翻译 (需密钥)': 'tencent',
    '火山翻译 (需密钥)': 'volcengine',
}

# 免费API集合，用于判断是否需要密钥
FREE_APIS = {
    'bing_free', 'mymemory_free', 'google_free', 'google_gtx_free',
    'libretranslate_free', 'youdao_free',
}

# 默认API设置
DEFAULT_TRANSLATION_API = 'bing_free'

# 免费接口挂掉时自动切换其它免费引擎（可在设置页关闭）
DEFAULT_FREE_API_FAILOVER = True

# 截图时自动吸附窗口边界（悬停高亮 / 单击整窗选中 / 拖框贴边）
DEFAULT_SNAP_WINDOWS = True

# 免费 API 可选密钥配置键（None = 该引擎没有密钥项）
FREE_API_KEY_FIELDS = {
    'bing_free': 'bing_azure_key',
    'mymemory_free': 'mymemory_api_key',
    'google_free': 'google_cloud_key',
    'libretranslate_free': None,
    'google_gtx_free': None,
    'youdao_free': None,
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
    '免费公共通道·备用端点 (无需密钥)': 'pollinations_text',
    '免费额度 API (需免费 Key)': 'free_quota',
    'DeepSeek 网页版 (免费，需 Token)': 'deepseek_web',
    '豆包 网页版 (免费，需 Cookie)': 'doubao_web',
    '自定义 API (OpenAI 兼容)': 'custom',
}

# 免费额度服务：官方注册即得免费 Key，均为 OpenAI 兼容 /chat/completions
AI_POLISH_FREE_SERVICES = {
    'Groq (免费额度，速度快)': 'groq',
    'OpenRouter 免费模型': 'openrouter',
    '智谱 GLM-4-Flash (国内免费)': 'zhipu',
    '硅基流动 免费模型 (国内)': 'siliconflow',
}

FREE_POLISH_SERVICES = ('groq', 'openrouter', 'zhipu', 'siliconflow')

# 免费通道默认模型（用户可在设置页覆盖）
FREE_POLISH_MODELS = {
    'groq': 'llama-3.1-8b-instant',
    'openrouter': 'meta-llama/llama-3.2-3b-instruct:free',
    'zhipu': 'glm-4-flash',
    'siliconflow': 'Qwen/Qwen2.5-7B-Instruct',
}

FREE_POLISH_ENDPOINTS = {
    'groq': 'https://api.groq.com/openai/v1/chat/completions',
    'openrouter': 'https://openrouter.ai/api/v1/chat/completions',
    'zhipu': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
    'siliconflow': 'https://api.siliconflow.cn/v1/chat/completions',
}

DEFAULT_AI_POLISH_FAILOVER = True

# AI 润色默认设置
DEFAULT_AI_POLISH_SETTINGS = {
    'ai_polish_provider': 'pollinations',
    'ai_polish_failover': True,
    'ai_polish_deepseek_token': '',
    'ai_polish_doubao_cookie': '',
    'ai_polish_api_url': '',
    'ai_polish_api_key': '',
    'ai_polish_model': '',
    'ai_polish_free_service': 'groq',
    'ai_polish_free_model': '',
    'ai_polish_groq_key': '',
    'ai_polish_openrouter_key': '',
    'ai_polish_zhipu_key': '',
    'ai_polish_siliconflow_key': '',
}

# 结果缓存默认设置（性能）：重复内容不再重复走网络 / OCR 模型
DEFAULT_CACHE_SETTINGS = {
    'result_cache_enabled': True,       # 总开关
    'result_cache_max_entries': 500,    # 每个命名空间最多缓存条数
    'result_cache_ttl_days': 7,         # 保留天数，过期自动重算
    'result_cache_persist': True,       # 跨会话保存（重启后仍秒回）
}

# 实时翻译默认设置
DEFAULT_LIVE_SETTINGS = {
    'live_interval_ms': 1200,   # 刷新间隔（毫秒）
    'live_show_source': False,  # 浮窗默认是否显示原文
}

# 实时字幕：音频来源 / 识别引擎 / 显示模式
SUBTITLE_AUDIO_SOURCES = {
    '麦克风': 'mic',
    '电脑声音': 'loopback',
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


# 实时翻译 / 字幕 / 批量任务会频繁读设置，以前每次都要开文件 + 解析 JSON。
# 按 (mtime_ns, size) 做快照：文件没变直接给内存副本；本程序或外部进程改写
# 配置后 stamp 立即变化，设置页保存依然「立刻生效」，不会读到过期值。
_config_snapshot = {"path": None, "stamp": None, "data": None}
_config_snapshot_lock = threading.Lock()


def _config_stamp(path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _refresh_config_snapshot(path, data):
    with _config_snapshot_lock:
        _config_snapshot.update({
            "path": str(path.resolve()),
            "stamp": _config_stamp(path),
            "data": dict(data),
        })


def invalidate_config_snapshot():
    """外部可能绕过 save_user_config 改了配置时，强制下次重新读盘。"""
    with _config_snapshot_lock:
        _config_snapshot["stamp"] = None
        _config_snapshot["data"] = None


def load_user_config():
    """加载用户配置；文件损坏时先备份，避免下次保存把真实配置永久覆盖。

    返回的是副本，调用方原地修改不会污染快照（有不少页面会直接改返回值）。
    """
    path = Path(CONFIG_FILE).resolve()
    stamp = _config_stamp(path)
    with _config_snapshot_lock:
        snap = _config_snapshot
        if (snap["path"] == str(path) and snap["stamp"] == stamp
                and snap["data"] is not None):
            return dict(snap["data"])
    if not path.exists():
        _refresh_config_snapshot(path, {})
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("配置根节点不是对象")
        _refresh_config_snapshot(path, data)
        return dict(data)
    except (json.JSONDecodeError, OSError, ValueError, UnicodeError) as exc:
        # 损坏文件不进缓存：用户手工修好后会立刻读到新内容
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
            # 落盘后立刻刷新快照：下一次 load_user_config 读到的一定是新值，
            # 也不会被同一秒内 mtime 精度问题坑到。
            _refresh_config_snapshot(path, merged)
            # 原地更新：让 `from config import user_config` 的既有引用同步看到新值
            user_config.clear()
            user_config.update(merged)
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False


def get_asr_settings():
    """读取语音识别设置（配置变更自动刷新，设置页修改实时生效）。"""
    return merged_settings(DEFAULT_ASR_SETTINGS)


def get_ai_polish_settings():
    """读取 AI 润色设置（配置变更自动刷新，设置保存后立即生效）。"""
    return merged_settings(DEFAULT_AI_POLISH_SETTINGS)


def get_live_settings():
    """读取实时翻译设置。"""
    return merged_settings(DEFAULT_LIVE_SETTINGS)


def get_subtitle_settings():
    """读取实时字幕设置（配置变更自动刷新，保存后立即生效）。"""
    return merged_settings(DEFAULT_SUBTITLE_SETTINGS)


def get_cache_settings():
    """读取结果缓存设置（设置页保存后立即生效，无需重启）。"""
    return merged_settings(DEFAULT_CACHE_SETTINGS)

# 加载保存的配置
user_config = load_user_config()

APP_VERSION = "1.5.5"
BILIBILI_DYNAMIC_URL = "https://space.bilibili.com/382966397/dynamic"
GITHUB_REPO = "2199816672/translation"

WINDOW_TITLE = "翻译工具"
WINDOW_SIZE = "900x700"
FONT_FAMILY = "Microsoft YaHei"
FONT_SIZE = 12