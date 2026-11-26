#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OCR 引擎清单与本地模型管理（零依赖，可在无 PySide6 环境里直接测试）。

为什么要单独一层：EasyOCR 首次运行会自动联网下载模型，国内网络经常失败，
而用户手头可能已经有模型文件。这里把「模型放哪、缺哪些文件、怎么导入」
收敛成纯函数，设置页负责展示与交互，识别器只管读目录：

* :data:`MODEL_ROOT` 放在已纳入 ``RUNTIME_DATA`` 排除清单的 ``models/ocr``
  下，覆盖升级不会删掉用户手动放进去的模型；
* :func:`resolve_model_dir` 兼容老版本下载到 ``~/.EasyOCR/model`` 的模型，
  避免升级后被迫重新下载；
* :func:`import_local_model` 不改动用户原目录，只把文件复制进统一管理目录。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from app_paths import app_dir

MODEL_ROOT = os.path.join(str(app_dir()), "models", "ocr")
LEGACY_EASY_DIR = os.path.join(os.path.expanduser("~"), ".EasyOCR", "model")

# 用户「直接指定模型目录」时写进 user_config.json 的键。用户数据目录里的
# 配置在覆盖升级 / 一键升级后保留，所以自选目录也不会升级后失效。
CUSTOM_MODEL_DIR_KEY = "ocr_model_dir"
# 「本地模型」下拉里登记的用户模型目录（列表，升级后保留）。字幕那边把导入
# 的模型登记进 asr_imported_models，OCR 同理：导入过的目录要能在下拉里看到、
# 随时切回来，而不是一「恢复默认」就再也找不到。
KNOWN_MODEL_DIRS_KEY = "ocr_model_dirs"
AUTO_MODEL_KEY = "auto"          # 下拉「自动」项的 data（写配置时转为空串）
_MAX_KNOWN_DIRS = 12

_DETECTION_MODEL = "craft_mlt_25k.pth"
_MODEL_SUFFIXES = (".pth", ".json", ".yaml", ".yml", ".txt", ".zip")

# EasyOCR 官方识别模型文件名，逐项对照 easyocr/config.py 1.7.2 的
# recognition_models（gen1 / gen2）。以前整张表是按「语言代码 + _g2.pth」
# 猜出来的，几乎全是错的：英文官方叫 english_g2.pth、日文 japanese_g2.pth，
# 法/德/西/葡/意/越共用 latin_g2.pth、俄文用 cyrillic_g2.pth、繁中用
# chinese.pth。用户按官方模型页下好的文件因此被判成「还缺 1 个模型」，
# 识别器也不会启用对应语言 —— 这正是「明明下了模型却用不了」的根因。
# 注意 EasyOCR 的 standard 模式一次只加载一个识别模型：['ch_sim','en']
# 实际只用 zh_sim_g2.pth（本身含英文字母），english_g2.pth 仅纯英文时用。
_LANG_MODELS = {
    'ch_sim': 'zh_sim_g2.pth',
    'ch_tra': 'chinese.pth',
    'en': 'english_g2.pth',
    'ja': 'japanese_g2.pth',
    'ko': 'korean_g2.pth',
    'fr': 'latin_g2.pth',
    'de': 'latin_g2.pth',
    'es': 'latin_g2.pth',
    'ru': 'cyrillic_g2.pth',
    'pt': 'latin_g2.pth',
    'it': 'latin_g2.pth',
    'ar': 'arabic.pth',
    'th': 'thai.pth',
    'vi': 'latin_g2.pth',
}

# 历史版本说明里误导用户下载/命名的文件名 → 官方文件名。这些名字
# EasyOCR 根本不会去读，所以 **不能** 算「已就绪」，否则 Reader 会去
# 找官方名、找不到又因 download_enabled=False 直接失败。导入时自动
# 改名成官方名，用户不必重新下载几百 MB。
_MODEL_ALIASES = {
    'en_g2.pth': 'english_g2.pth',
    'zh_tra_g2.pth': 'chinese.pth',
    'ja_g2.pth': 'japanese_g2.pth',
    'ko_g2.pth': 'korean_g2.pth',
    'fr_g2.pth': 'latin_g2.pth',
    'de_g2.pth': 'latin_g2.pth',
    'es_g2.pth': 'latin_g2.pth',
    'pt_g2.pth': 'latin_g2.pth',
    'it_g2.pth': 'latin_g2.pth',
    'vi_g2.pth': 'latin_g2.pth',
    'ru_g2.pth': 'cyrillic_g2.pth',
    'ar_g2.pth': 'arabic.pth',
    'th_g2.pth': 'thai.pth',
}


def canonical_model_name(name):
    """把历史误命名的模型文件映射回 EasyOCR 官方文件名。"""
    base = os.path.basename(str(name or ''))
    return _MODEL_ALIASES.get(base, base)


def misnamed_model_files(files):
    """从 ``_model_files`` 结果里挑出「文件名不对、EasyOCR 读不到」的项。

    返回 ``[(旧名, 官方名), ...]``；0 字节的半成品不算，避免把下载中断
    的垃圾文件也说成「需要改名」。
    """
    return [(name, _MODEL_ALIASES[name])
            for name in sorted(files or {})
            if name in _MODEL_ALIASES and files.get(name)]

LANGUAGE_NAMES = {
    'ch_sim': '简体中文', 'ch_tra': '繁体中文', 'en': '英文',
    'ja': '日文', 'ko': '韩文', 'fr': '法文', 'de': '德文',
    'es': '西班牙文', 'ru': '俄文', 'pt': '葡萄牙文',
    'it': '意大利文', 'ar': '阿拉伯文', 'th': '泰文', 'vi': '越南文',
}


def language_label(code):
    """把语言代码变成给用户看的中文名。"""
    return LANGUAGE_NAMES.get(code, code)


def primary_language_model(languages=None):
    """「至少能识别一门语言」时推荐的那个语言模型文件名。"""
    langs = list(languages or ['ch_sim', 'en'])
    first = langs[0] if langs else 'ch_sim'
    return _LANG_MODELS.get(first, f"{first}_g2.pth")

# 方案对比：把「有哪些 OCR 模型/引擎」列全，也说清优势和代价。
#
# 安装包**只内置 Windows 系统自带 OCR**（占用 0 MB、零模型文件）——把
# EasyOCR / PaddleOCR 这类模型塞进安装包会让打包时间和体积都失控。其它
# 方案一律走「用户按需下载」，程序负责给入口和保底的手动导入通道：
#   builtin  = 安装包自带，开箱即用；
#   download = 程序提供在线安装 / 下载 / 导入入口，不随包分发；
#   planned  = 暂未实现，仅给官方下载地址作为选型参考，绝不冒充可选引擎。
ENGINE_CATALOG = (
    {
        'key': 'windows', 'name': 'Windows 系统自带 OCR', 'status': 'builtin',
        'pros': '开箱即用、零下载、启动最快、完全离线、占用 0 MB',
        'cost': '依赖系统已安装对应语言包；复杂排版 / 手写体精度一般',
        'best': '日常截图、界面文字、追求「装上就能用」',
        'size': '0 MB', 'offline': True,
        'how': '无需下载，安装完就能用', 'url': '',
    },
    {
        'key': 'easy', 'name': 'EasyOCR', 'status': 'download',
        'pros': '中英文与倾斜 / 艺术字更准、支持 80+ 语言、可完全离线',
        'cost': '首次需安装数百 MB 依赖并下载约 100 MB 模型；CPU 上较慢',
        'best': '截图文字较杂、需要更高识别率；网络不好可手动导入模型',
        'size': '约 100 MB 模型', 'offline': True,
        'how': '设置页点「安装」装引擎；模型会自动下载，也可手动只下'
               '检测模型 + 简体中文两个 .pth 后放进模型文件夹离线识别中文',
        'url': 'https://www.jaided.ai/easyocr/modelhub/',
    },
    {
        'key': 'rapid', 'name': 'RapidOCR（ONNXRuntime）', 'status': 'planned',
        'pros': '无需 torch、依赖最轻、CPU 速度快、模型仅十几 MB',
        'cost': '超小字 / 生僻语言略逊；当前版本尚未内置实现',
        'best': '低配机器、想要「轻量 + 离线」的备选方向',
        'size': '约 15 MB', 'offline': True,
        'how': '暂未内置，可从官方仓库自行获取模型与运行时',
        'url': 'https://github.com/RapidAI/RapidOCR',
    },
    {
        'key': 'paddle', 'name': 'PaddleOCR', 'status': 'planned',
        'pros': '中文、竖排文字、表格与弯曲文本表现最强，模型体系最完整',
        'cost': '需 paddlepaddle 运行时（数百 MB）、部署最重；尚未内置实现',
        'best': '文档扫描、票据、竖排中文等专业场景',
        'size': '数百 MB', 'offline': True,
        'how': '暂未内置，可从官方模型库自行下载',
        'url': 'https://github.com/PaddlePaddle/PaddleOCR',
    },
    {
        'key': 'tesseract', 'name': 'Tesseract', 'status': 'planned',
        'pros': '老牌开源、100+ 语言、社区语言包齐全、CPU 占用低',
        'cost': '需另装可执行文件 + traineddata 语言包；版面分析较弱',
        'best': '纯印刷体、多语种批量识别',
        'size': '按语言包', 'offline': True,
        'how': '暂未内置，可从官方语言包仓库自行下载',
        'url': 'https://github.com/tesseract-ocr/tessdata',
    },
)

# 设置页「下载 / 了解更多」菜单：只给官方地址，不代管任何模型文件。
DOWNLOAD_LINKS = (
    ("EasyOCR 模型下载页（官方）", "https://www.jaided.ai/easyocr/modelhub/"),
    ("EasyOCR 安装说明（GitHub）", "https://github.com/JaidedAI/EasyOCR"),
    ("RapidOCR 模型与运行时", "https://github.com/RapidAI/RapidOCR"),
    ("PaddleOCR 模型库", "https://github.com/PaddlePaddle/PaddleOCR"),
    ("Tesseract 语言包", "https://github.com/tesseract-ocr/tessdata"),
)

# 每一项在当前版本里到底能不能用起来。安装包只内置 Windows 自带 OCR：
# EasyOCR 走「按需安装 + 导入模型」可用；另外三个只是选型参考，程序并没有
# 实现对应引擎。用户可能为此下几百 MB，菜单里必须标清楚，别让人白下载。
_DOWNLOAD_LINK_KINDS = ('usable', 'usable', 'reference',
                        'reference', 'reference')


def download_menu_items():
    """下载菜单数据：``(显示文字, 官方地址, usable/reference)``。

    ``DOWNLOAD_LINKS`` 保持 ``(label, url)`` 二元组不变，避免影响已有调用方；
    这里只附加可用性标记。新增链接若忘了登记，默认按「仅参考」处理，宁可
    少承诺也不误导用户以为下载完就能用。
    """
    items = []
    for index, (label, url) in enumerate(DOWNLOAD_LINKS):
        kind = (_DOWNLOAD_LINK_KINDS[index]
                if index < len(_DOWNLOAD_LINK_KINDS) else 'reference')
        tag = '✅ 可导入使用 · ' if kind == 'usable' else '📋 仅选型参考 · '
        items.append((tag + label, url, kind))
    return items


def human_size(num):
    num = float(num or 0)
    if num >= 1024 ** 3:
        return f"{num / 1024 ** 3:.2f} GB"
    if num >= 1024 ** 2:
        return f"{num / 1024 ** 2:.0f} MB"
    if num >= 1024:
        return f"{num / 1024:.0f} KB"
    return f"{int(num)} B"


def required_model_files(languages=None):
    """当前语言组合需要哪些模型文件（检测模型 + 各语言识别模型）。"""
    langs = list(languages or ['ch_sim', 'en'])
    return [_DETECTION_MODEL] + [
        _LANG_MODELS.get(lang, f"{lang}_g2.pth") for lang in langs
    ]


def _model_files(directory):
    if not directory or not os.path.isdir(directory):
        return {}
    found = {}
    try:
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            if name.lower().endswith(_MODEL_SUFFIXES):
                try:
                    found[name] = os.path.getsize(path)
                except OSError:
                    found[name] = 0
    except OSError:
        pass
    return found


def _total_bytes(directory):
    total = 0
    if not directory or not os.path.isdir(directory):
        return total
    try:
        for base, _dirs, files in os.walk(directory):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(base, name))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def has_required_models(directory, languages=None):
    """目录里是否已有「检测模型 + 至少一个语言模型」且文件非空。"""
    found = _model_files(directory)
    if not found.get(_DETECTION_MODEL):
        return False
    langs = list(languages or ['ch_sim', 'en'])
    names = [_LANG_MODELS.get(lang, f"{lang}_g2.pth") for lang in langs]
    # 只要有一门语言模型齐备就能跑；缺的语言由 easyocr 按需下载
    return any(found.get(name) for name in names)


def models_complete(directory, languages=None):
    """检测模型 + 所选语言模型全部齐备：只有这时才能关掉联网下载。"""
    found = _model_files(directory)
    if not found.get(_DETECTION_MODEL):
        return False
    langs = list(languages or ['ch_sim', 'en'])
    return all(found.get(_LANG_MODELS.get(lang, f"{lang}_g2.pth"))
               for lang in langs)


def custom_model_dir(cfg=None):
    """用户在设置页「直接指定模型目录」选的路径；空串表示用默认目录。

    只认真实存在、且至少含一个模型文件的目录。配置被手工改坏 / 目录被删时
    自动回落默认目录，绝不让 OCR 因为指向空文件夹而整个挂掉。
    """
    try:
        if cfg is None:
            from config import load_user_config
            cfg = load_user_config()
        value = str((cfg or {}).get(CUSTOM_MODEL_DIR_KEY, "") or "").strip()
    except Exception:
        return ""
    if not value or not os.path.isdir(value) or not _model_files(value):
        return ""
    return value


def _effective_custom(custom, root=None, legacy=None):
    """解析「自选目录」参数，且不让真实用户配置污染显式传目录的测试调用。"""
    if custom is False:
        return ""
    if custom:
        path = str(custom)
        return path if os.path.isdir(path) and _model_files(path) else ""
    if root is not None or legacy is not None:
        return ""          # 测试 / 隔离调用：只认显式传进来的目录
    return custom_model_dir()


def active_model_dir(languages=None):
    """「打开模型文件夹 / 导入文件 / 复制路径」应指向的目录。

    必须和 ``detect_local_models()['resolved_dir']`` 同源：老用户的模型只
    在旧版 ``~/.EasyOCR/model``、统一目录为空时，识别器读旧版目录，而
    「打开文件夹」若指向空的统一目录，用户把 .pth 放进去会以为没生效；
    导入目标目录也同理，放进来的文件要落在真正被读取的地方。
    """
    return resolve_model_dir(languages)


def resolve_model_dir(languages=None, root=None, legacy=None, custom=None):
    """给 ``easyocr.Reader(model_storage_directory=...)`` 用的目录。

    优先级：用户直接指定的模型目录（无需复制文件） → 统一管理目录 →
    老版本默认目录 → 统一管理目录（新下载落这里）。
    ``root`` / ``legacy`` 主要供测试与检测函数传入自定义目录；显式传入时
    不读用户配置，保证测试隔离。
    """
    chosen = _effective_custom(custom, root, legacy)
    if chosen and has_required_models(chosen, languages):
        return chosen
    root = str(root or MODEL_ROOT)
    legacy = str(legacy or LEGACY_EASY_DIR)
    if has_required_models(root, languages):
        return root
    if has_required_models(legacy, languages):
        return legacy
    return root


def available_languages(directory, languages=None):
    """目录里真正能离线识别的语言列表（检测模型 + 该语言模型都存在且非空）。

    旧逻辑把 ``['ch_sim', 'en']`` 当成硬性要求：用户按说明手动下了
    ``craft_mlt_25k.pth`` + ``zh_sim_g2.pth`` 两个文件，检测仍报「还缺
    1 个模型文件」，识别器还会为没下的 ``english_g2.pth`` 打开联网下载，国内
    网络一卡就整个 Reader 初始化失败。EasyOCR 只认 model_storage_directory
    里实际存在的文件，所以这里按目录里齐备的语言返回，识别器只启用它们。
    """
    found = _model_files(directory)
    if not found.get(_DETECTION_MODEL):
        return []
    langs = list(languages or ['ch_sim', 'en'])
    return [lang for lang in langs
            if found.get(_LANG_MODELS.get(lang, f"{lang}_g2.pth"))]


def reader_languages(languages=None, root=None, legacy=None, custom=None):
    """应传给 ``easyocr.Reader`` 的语言列表：只保留本地已齐备的语言。

    返回空列表表示本地一门语言都没有，调用方应回落到完整语言列表并允许
    easyocr 自动下载（首次在线安装路径不能断）。
    """
    langs = list(languages or ['ch_sim', 'en'])
    directory = resolve_model_dir(langs, root=root, legacy=legacy,
                                  custom=custom)
    return available_languages(directory, langs)


# ── 「本地模型」下拉：与字幕页「导入后立刻出现在下拉里」保持同一交互 ──

def _norm_dir(path):
    """Windows 路径大小写不敏感：比较目录时统一 abspath + normcase。"""
    return os.path.normcase(os.path.abspath(str(path or '')))


def choice_key(value):
    """把「自动 / 目录路径」归一化成下拉项可比较的 key。"""
    if not value or str(value) == AUTO_MODEL_KEY:
        return AUTO_MODEL_KEY
    return _norm_dir(value)


def custom_value_for_key(key):
    """下拉项 → ``ocr_model_dir`` 配置值；自动项写空串清除自定义目录。"""
    if not key or str(key) == AUTO_MODEL_KEY:
        return ""
    return os.path.abspath(str(key))


def known_model_dirs(cfg=None):
    """用户导入 / 指定过的模型目录（去重、保序、绝对路径）。

    配置里可能是旧版留下的单个字符串，也可能是被手工改坏的任意类型；这里
    一律安全收敛成列表，绝不让设置页构造失败。
    """
    try:
        if cfg is None:
            from config import load_user_config
            cfg = load_user_config()
        raw = (cfg or {}).get(KNOWN_MODEL_DIRS_KEY) or []
    except Exception:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    result, seen = [], set()
    for item in raw:
        text = str(item or '').strip()
        if not text:
            continue
        key = _norm_dir(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(os.path.abspath(text))
    return result[:_MAX_KNOWN_DIRS]


def register_model_dir(path, cfg=None, save=True):
    """把导入 / 指定的目录登记进「本地模型」下拉，返回 ``(ok, dirs)``。

    默认目录 / 旧版目录本来就是固定项，不重复登记；最近导入的排最前。
    ``save=False`` 供测试与只读调用使用，不写用户配置。
    """
    raw = str(path or '').strip()
    target = os.path.abspath(raw) if raw else ''
    dirs = known_model_dirs(cfg)
    if not raw or not os.path.isdir(target):
        return False, dirs
    if _norm_dir(target) in (_norm_dir(MODEL_ROOT), _norm_dir(LEGACY_EASY_DIR)):
        return True, dirs
    dirs = [d for d in dirs if _norm_dir(d) != _norm_dir(target)]
    dirs.insert(0, target)
    dirs = dirs[:_MAX_KNOWN_DIRS]
    if save:
        try:
            from config import save_user_config
            if not save_user_config({KNOWN_MODEL_DIRS_KEY: dirs}):
                return False, dirs
        except Exception:
            return False, dirs
    return True, dirs


def current_choice_key(cfg=None):
    """当前应选中哪个下拉项：有效自定义目录，或「自动」。"""
    return custom_model_dir(cfg) or AUTO_MODEL_KEY


def _model_choice(key, directory, title, source, languages):
    """单个下拉项：标题、文件数、能识别的语言、是否就绪、动态路径提示。"""
    directory = os.path.abspath(str(directory))
    exists = os.path.isdir(directory)
    files = _model_files(directory) if exists else {}
    usable = available_languages(directory, languages) if exists else []
    if not exists:
        tail = "⚠ 目录已失效"
    elif usable:
        tail = (f"{len(files)} 个文件 · "
                + "、".join(language_label(lang) for lang in usable) + " ✓")
    elif files:
        tail = f"{len(files)} 个文件 · ⚠ 不完整"
    else:
        tail = "暂无模型文件"
    tip = (title + "\n读取目录（本机实际路径，请用按钮定位）：" + directory
           + f"\n模型文件 {len(files)} 个 · "
           + (("可离线识别：" + "、".join(language_label(lang) for lang in usable))
              if usable else "尚不能离线识别（缺必需文件）"))
    return {
        'key': key, 'dir': directory, 'label': f"{title} · {tail}",
        'tip': tip, 'source': source, 'ready': bool(usable),
        'exists': exists, 'languages': usable, 'files': len(files),
    }


def model_choices(cfg=None, languages=None, root=None, legacy=None):
    """「本地模型」下拉的数据源（纯函数，与字幕页 ``_reload_models`` 同理）。

    固定项：自动（按优先级解析出的目录）、默认模型文件夹、旧版目录；
    动态项：用户导入 / 直接指定过的目录。每项都能直接作为
    ``ocr_model_dir`` 写入，选中即切换识别器读取的模型目录。
    ``root`` / ``legacy`` 显式传入时不读用户配置，方便测试隔离。
    """
    if cfg is None:
        if root is None and legacy is None:
            try:
                from config import load_user_config
                cfg = load_user_config()
            except Exception:
                cfg = {}
        else:
            cfg = {}
    cfg = cfg or {}
    langs = list(languages or ['ch_sim', 'en'])
    root = os.path.abspath(str(root or MODEL_ROOT))
    legacy = os.path.abspath(str(legacy or LEGACY_EASY_DIR))
    auto_dir = resolve_model_dir(langs, root=root, legacy=legacy,
                                 custom=False)
    # 默认目录为空、模型在旧版目录时，说明「自动」为什么会显示模型已就绪，
    # 免得用户以为自动项和下面「默认模型文件夹」自相矛盾。
    auto_title = ("自动（推荐）" if _norm_dir(auto_dir) == _norm_dir(root)
                  else "自动（推荐 · 当前读到旧版目录）")
    entries = [
        _model_choice(AUTO_MODEL_KEY, auto_dir, auto_title, "auto", langs),
        _model_choice(root, root, "默认模型文件夹", "default", langs),
    ]
    if _model_files(legacy):
        entries.append(
            _model_choice(legacy, legacy, "旧版目录（.EasyOCR）", "legacy",
                          langs))
    for directory in known_model_dirs(cfg):
        norm = _norm_dir(directory)
        if norm in (_norm_dir(root), _norm_dir(legacy)):
            continue       # 固定项已覆盖，不重复登记
        if any(choice_key(e['key']) == norm for e in entries):
            continue
        entries.append(_model_choice(
            directory, directory, "📌 " + os.path.basename(directory),
            "imported", langs))
    # 配置里手写的目录、或上一版保存的自定义目录，即使没登记过也要出现，
    # 否则下拉会把用户当前正在用的模型「藏起来」。
    custom = custom_model_dir(cfg)
    if custom and not any(choice_key(e['key']) == choice_key(custom)
                          for e in entries):
        entries.append(_model_choice(
            custom, custom, "📌 " + os.path.basename(custom),
            "current", langs))
    current = choice_key(current_choice_key(cfg))
    for entry in entries:
        entry['current'] = choice_key(entry['key']) == current
    return entries


def detect_local_models(languages=None, root=None, legacy=None, custom=None):
    """只读检测：现在有哪些模型文件、必需还缺哪些、能识别哪些语言。

    ``missing`` 只列「阻止离线识别」的必需文件（检测模型 + 至少一门语言
    模型）；已经能识别中文时，缺的英文模型放进 ``optional_missing``，
    UI 不再误报「还缺 1 个模型文件」而让用户以为白下了。
    用户「直接指定模型目录」后，统计、缺件判定与识别器读取目录必须同源于
    那个目录，否则会出现「自选目录已能用，界面却按默认目录报缺文件」。
    """
    configured = _effective_custom(custom, root, legacy)
    root = str(root or MODEL_ROOT)
    legacy = str(legacy or LEGACY_EASY_DIR)
    resolved = resolve_model_dir(languages, root=root, legacy=legacy,
                                 custom=configured if configured else False)
    custom_active = bool(
        configured and
        os.path.normcase(os.path.abspath(resolved))
        == os.path.normcase(os.path.abspath(configured)))
    own = _model_files(resolved) if custom_active else _model_files(root)
    old = _model_files(legacy)
    # 用户明确指定了目录时，统计只反映那个目录：把默认/旧版目录里的文件
    # 一起算进来会出现「共 3 个文件，占用…」却和自选目录内容对不上。
    merged = dict(own) if custom_active else dict(old)
    if not custom_active:
        merged.update(own)      # 统一管理目录优先
    needed = required_model_files(languages)
    # 缺什么按「识别器真正会读的那个目录」算：模型分散在两处时不能报
    # 「一个都不缺」却又判定无法离线识别。
    resolved_files = _model_files(resolved)
    usable = available_languages(resolved, languages)
    all_missing = [name for name in needed if not resolved_files.get(name)]
    if usable:
        missing = []
        optional_missing = all_missing
    else:
        # 一个语言模型都没有时，只要求用户补「检测模型 + 一门语言」这两个，
        # 不把可选的英文模型也算成必需，说明与实际要求必须一致。
        minimal = [_DETECTION_MODEL, primary_language_model(languages)]
        missing = [name for name in minimal
                   if not resolved_files.get(name)]
        optional_missing = [name for name in all_missing
                            if name not in missing]
    # easyocr.Reader 只读一个 model_storage_directory。另一个目录里若还有
    # 有效模型（典型：旧版 ~/.EasyOCR/model 里留了 english_g2.pth），它会
    # 被静默忽略，用户却以为自己下过了。这里显式标记，让设置页引导合并
    # 而不是显示「可选再补」把人支使去重下。
    # 另有一类「下了但文件名不对」：历史说明误导用户用 en_g2.pth 这种
    # 官方根本不读的名字。它们不能算成缺文件让用户重下，要提示一键改名。
    misnamed = misnamed_model_files(resolved_files)
    if custom_active:
        # 用户明确指定了目录：其它目录里的同类文件不该再提示「未合并」，
        # 否则会误导他去把默认目录也塞满一份。
        other_dir, other_files = '', {}
    elif (os.path.normcase(os.path.abspath(root))
            == os.path.normcase(os.path.abspath(legacy))):
        other_dir, other_files = '', {}
    elif resolved == root:
        other_dir, other_files = legacy, old
    else:
        other_dir, other_files = root, own
    split_files = [name for name, size in sorted(other_files.items())
                   if size and not resolved_files.get(name)]
    return {
        'root': root, 'legacy_root': legacy,
        'files': {k: v for k, v in sorted(merged.items())},
        'own_files': own, 'legacy_files': old,
        'needed': needed, 'missing': missing,
        'optional_missing': optional_missing,
        'available_languages': usable,
        'has_detection': bool(merged.get(_DETECTION_MODEL)),
        'ready': bool(usable),
        'can_use_offline': bool(usable),
        'resolved_dir': resolved,
        'custom_dir': resolved if custom_active else '',
        'custom_configured': configured,
        'other_dir': other_dir,
        'split_dirs': bool(split_files),
        'split_files': split_files,
        'misnamed_files': misnamed,
        'total_bytes': (_total_bytes(resolved) if custom_active
                        else _total_bytes(root) + _total_bytes(legacy)),
    }


def model_cache_info(root=None):
    """缓存占用统计（只读），结构与语音模型保持一致便于 UI 复用。"""
    root = str(root or MODEL_ROOT)
    files = _model_files(root)
    info = {
        'root': root, 'exists': os.path.isdir(root),
        'total_bytes': sum(files.values()),
        'models': [{'name': name, 'path': os.path.join(root, name),
                    'bytes': size, 'ready': size > 0}
                   for name, size in sorted(files.items())],
    }
    return info


def model_status_suffix(detect):
    """状态后缀：设置页与统计文案共用同一份 detect 结论，避免自相矛盾。

    以前统计只看统一管理目录、检测却按识别器真正读取的目录算，会出现
    「✅ 已可离线识别 · 还缺 2 个文件」这种同框打架的提示。
    """
    usable = detect.get('available_languages') or []
    misnamed = detect.get('misnamed_files') or []
    bad_name_tip = (f"，⚠ {len(misnamed)} 个模型文件名不符合官方命名"
                    if misnamed else "")
    if usable:
        names = "、".join(language_label(lang) for lang in usable)
        optional = detect.get('optional_missing') or []
        return (f"，已可离线识别（{names}）"
                + (f"，可选再补 {len(optional)} 个文件支持其它语言"
                   if optional else "，模型齐全")
                + bad_name_tip)
    missing = detect.get('missing') or []
    return ((f"，还缺 {len(missing)} 个必需文件" if missing
             else "，模型文件不完整，暂不能离线识别")
            + bad_name_tip)


def model_cache_summary(info=None, languages=None, detect=None):
    """模型统计文案；传入 detect 时以识别器真实读取目录的结论为准。

    ``detect`` 为空时按 info 里的 ``ready``（文件非空）自行判定，0 字节的
    半成品模型不会再被当成「模型齐全可离线识别」。
    """
    info = info or model_cache_info()
    root = str(info.get('root') or '')
    files = info.get('models') or []
    if not files and not (detect and detect.get('files')):
        return ("尚无本地 OCR 模型（可点「检测本地模型」或手动导入）"
                " · 目录 " + root)
    if detect is not None:
        count = len(detect.get('files') or {})
        total = detect.get('total_bytes', info.get('total_bytes'))
        tail = model_status_suffix(detect)
    else:
        count = len(files)
        total = info.get('total_bytes')
        # 只用非空文件判定；文件名存在但 0 字节（下载中断）等于没有。
        valid = {m.get('name') for m in files if m.get('ready')}
        needed = required_model_files(languages)
        langs = list(languages or ['ch_sim', 'en'])
        usable = ([lang for lang in langs
                   if _LANG_MODELS.get(lang, f"{lang}_g2.pth") in valid]
                  if _DETECTION_MODEL in valid else [])
        minimal = [_DETECTION_MODEL, primary_language_model(languages)]
        fake_detect = {
            'available_languages': usable,
            'optional_missing': ([n for n in needed if n not in valid]
                                 if usable else
                                 [n for n in needed
                                  if n not in valid and n not in minimal]),
            'missing': ([] if usable else
                        [n for n in minimal if n not in valid]),
        }
        tail = model_status_suffix(fake_detect)
    return (f"共 {count} 个模型文件，占用 "
            f"{human_size(total)}{tail}")


def _copy_misnamed_to_official(directory):
    """把目录里历史错名的模型复制一份成官方名，绝不删除用户原文件。

    老用户的模型常在 ``~/.EasyOCR/model``，而「打开 / 导入目标」与识别器
    读取目录同源后，这个目录就是 root 本身：不能「复制进自己」，但设置页
    又提示用户「点导入即可自动改名」。这里就地补一份官方名副本，让那条
    提示真的点得动。官方名已有有效文件时跳过，避免覆盖正确模型。
    """
    found = _model_files(directory)
    copied = []
    for old, new in misnamed_model_files(found):
        if found.get(new):
            continue
        try:
            shutil.copy2(os.path.join(directory, old),
                         os.path.join(directory, new))
        except (OSError, shutil.SameFileError):
            continue
        copied.append(new)
    return copied


def import_local_model(source, root=None):
    """把手动下载的模型目录 / 单个文件放进统一管理目录。

    不移动、不删除用户原文件；同名文件直接按所选目录覆盖。返回
    ``(模型目录现有文件数, 本次复制的文件名列表)``；目录里一个模型文件
    都没有时抛中文异常。
    """
    # 用户「直接指定模型目录」后，导入的新文件也要进那个目录，否则导入完
    # 检测仍按自选目录算，会看起来像「导入了却没生效」。
    root = str(root or active_model_dir())
    # Windows 路径大小写不敏感，但 abspath 不归一化大小写：用户选到
    # 「C:\Models」而 root 是「c:\models」时，字符串比较会误判成两个目录，
    # copy2 随后抛英文 SameFileError。统一 normcase 才能给出中文提示。
    root = os.path.normcase(os.path.abspath(root))
    src = os.path.normcase(os.path.abspath(str(source or '')))
    if not os.path.exists(src):
        raise RuntimeError("所选路径不存在，请重新选择")
    if os.path.isdir(src) and src == root:
        # 文件对话框默认就停在模型目录。老用户的模型若正好在旧版目录
        # （它本身就是当前读取目录），这里还要顺手把历史错名补成官方名，
        # 否则设置页「点导入自动改名」的提示点了只会报错。
        renamed = _copy_misnamed_to_official(root)
        if renamed:
            return len(_model_files(root)), sorted(renamed)
        # 用户直接点「确定」时别谎报导入成功
        raise RuntimeError(
            "所选的就是模型目录本身，文件已经在里面了，无需导入。"
            "请选择手动下载模型所在的另一个文件夹")
    os.makedirs(root, exist_ok=True)
    if os.path.isdir(src):
        candidates = [
            os.path.join(src, name) for name in os.listdir(src)
            if name.lower().endswith(_MODEL_SUFFIXES)
            and os.path.isfile(os.path.join(src, name))
        ]
    elif src.lower().endswith(_MODEL_SUFFIXES):
        candidates = [src]
    else:
        candidates = []
    if not candidates:
        raise RuntimeError(
            "所选目录里没有模型文件。EasyOCR 模型是 .pth 文件，"
            "例如 craft_mlt_25k.pth、zh_sim_g2.pth、english_g2.pth")
    # 历史说明里误导用户用过的错名（en_g2.pth 等）EasyOCR 根本不读：
    # 导入时直接改成官方名，用户不用重新下载。同一官方名同时存在旧名和
    # 官方名两份时优先官方名，别让旧的半成品覆盖正确文件。
    grouped = {}
    for path in candidates:
        target_name = canonical_model_name(os.path.basename(path))
        if (target_name not in grouped
                or os.path.basename(path) == target_name):
            grouped[target_name] = path
    copied = []
    for target_name, path in grouped.items():
        target = os.path.join(root, target_name)
        if (os.path.normcase(os.path.abspath(path))
                == os.path.normcase(os.path.abspath(target))):
            continue        # 用户直接选了模型目录本身 / 里面已有的文件
        # 同名文件按所选目录版本覆盖（不比较 mtime：手动导入通常就是要换掉）
        try:
            shutil.copy2(path, target)
        except shutil.SameFileError:
            # 符号链接 / 大小写变体仍指向同一文件，跳过即可，交给下面的
            # `if not copied` 给统一的中文提示，别把英文异常漏给用户。
            continue
        copied.append(target_name)
    if not copied:
        # 例如选了模型目录本身（路径大小写不同时绕过了上面的 src == root），
        # 或选了目录里已有的单个模型文件：所有候选都被跳过，不能谎报导入完成。
        raise RuntimeError(
            "所选文件已经在模型目录里，没有需要导入的新文件。"
            "请选择手动下载模型所在的另一个文件夹")
    existing = _model_files(root)
    return len(existing), sorted(copied)


def manual_download_hint(languages=None):
    """手动下载保底：说清缺哪些文件、怎么定位目录，但不写死任何路径。

    每台电脑的用户数据目录都不一样（源码运行 / 安装版 / 只读目录降级），
    说明里写绝对路径只会误导。真正的目录交给「打开模型文件夹 /
    复制路径」两个按钮，用户按自己电脑上的实际位置操作。
    """
    detect = detect_local_models(languages)
    langs = list(languages or ['ch_sim', 'en'])
    first = langs[0] if langs else 'ch_sim'
    primary = primary_language_model(languages)
    extra = [name for name in required_model_files(languages)[1:]
             if name != primary]
    split_files = detect.get('split_files') or []
    if detect['ready']:
        names = "、".join(language_label(lang)
                         for lang in detect['available_languages'])
        if split_files:
            # 另一个目录里有现成模型却没被读到：千万别让用户再去重下一遍。
            status = (f"已可离线识别（{names}）；但另一目录还有 "
                      + "、".join(split_files[:4])
                      + " 没被读到。请点「导入本地模型」合并到统一目录，"
                        "合并后这些语言会一并启用，不用重新下载。")
        else:
            status = (f"已可离线识别（{names}），下面「可选」的文件不影响"
                      "中文识别，程序不会为没下的语言联网。")
    else:
        status = ("当前还不能离线识别，请补齐下面「最少必需」的文件"
                  + ("；另一目录还有 " + "、".join(split_files[:4])
                     + "，可点「导入本地模型」合并，别重复下载"
                     if split_files else "") + "。")
    missing = "、".join(detect['missing']) or "（无）"
    optional = ("另一目录已有：" + "、".join(split_files[:4]) + "（合并后启用）"
                if split_files else
                "、".join(detect['optional_missing']) or "（无）")
    return (
        "模型文件可从 EasyOCR 官方模型页 / GitHub Release 手动下载。\n"
        "放到哪里：点设置页「打开模型文件夹」直接定位，"
        "或点「复制路径」把目录粘进下载工具。\n"
        "每台电脑的目录都不一样，这里不写死路径，以按钮打开的为准。\n"
        "如何选择本地模型：设置页「本地模型」下拉会列出默认模型文件夹、"
        "旧版目录和你导入过的每一个模型目录，并显示各自的文件数与可识别语言；"
        "选中即切换识别器读取的目录，立即生效、无需重启。\n"
        "点「导入本地模型…」选文件夹 / 多个 .pth 文件复制进当前读取目录"
        "（复制完就生效，来源目录若本身是一套完整模型也会加进下拉供切换）；"
        "也可「直接指定模型目录」使用已有文件夹（不复制、不额外占空间），"
        "指定后该目录会立刻出现在下拉里并自动选中；"
        "菜单里的「恢复默认模型目录」只切回默认，不会丢已导入的目录。\n"
        + ("当前使用的是你指定的模型目录。\n" if detect.get('custom_dir')
           else "当前使用默认模型目录。\n")
        + "当前状态：" + status + "\n"
        "必需缺少：" + missing + "\n"
        "最少必需（只识别中文，2 个文件就够）：\n"
        f"  · {_DETECTION_MODEL}（文字检测）\n"
        f"  · {primary}（{language_label(first)}识别）\n"
        "可选（要识别中英混排 / 纯英文再下）：\n"
        "  · " + ("、".join(extra) or "（无）") + "\n"
        "可选补充：" + optional + "\n"
        "⚠ 文件名必须和 EasyOCR 官方完全一致：英文是 english_g2.pth、"
        "日文 japanese_g2.pth、法/德/西/葡/意共用 latin_g2.pth；"
        "旧版说明里写过的 en_g2.pth 这类名字官方根本不读。"
        "点「导入本地模型…」会自动改名成官方名，不用重新下载。\n"
        + ("检测到文件名不对：" + "、".join(
            f"{old} → {new}"
            for old, new in (detect.get('misnamed_files') or [])[:4]) + "\n"
           if detect.get('misnamed_files') else "")
        + "放好后点「检测本地模型」，识别时只启用本地已有的语言并完全离线，"
        "不再联网下载。\n"
        "若点「识别」提示缺少组件：请先在上方点「安装」把 EasyOCR 引擎"
        "（pip 组件）装好，模型文件不等于引擎，两者都要有。\n"
        "模型放在用户数据目录里，不在安装清单中：以后覆盖升级或应用内一键"
        "升级都不会删除，无需重新下载；卸载程序也不会清理它。"
    )


def engine_catalog_text():
    """设置页展示用的方案对比文字（含每个模型的优势、代价与获取方式）。"""
    tags = {
        'builtin': '✅ 内置 · 零下载',
        'download': '⬇ 按需下载 · 不随安装包分发',
        'planned': '📋 选型参考（暂未内置）',
    }
    lines = [
        "安装包只内置 Windows 系统自带 OCR（0 MB 模型文件）。"
        "其余方案都不随包分发，按需下载，避免安装包变大、打包变慢。",
        "下载好并放进模型目录的文件属于用户数据，不在安装清单内："
        "以后覆盖升级、应用内一键升级都会保留，无需重新下载。",
        "",
    ]
    for item in ENGINE_CATALOG:
        lines.append(
            f"{tags.get(item['status'], '📋 选型参考')} {item['name']}"
            f"｜{item['size']}"
            f"{' · 完全离线' if item['offline'] else ''}\n"
            f"    优势：{item['pros']}\n"
            f"    代价：{item['cost']}\n"
            f"    适合：{item['best']}\n"
            f"    获取：{item['how']}"
            + (f"\n    官方地址：{item['url']}" if item['url'] else ''))
    return "\n".join(lines)


def ensure_model_root():
    """打开模型目录前先创建，返回绝对路径；自选目录时直接打开它。"""
    active = active_model_dir()
    Path(active).mkdir(parents=True, exist_ok=True)
    return active
