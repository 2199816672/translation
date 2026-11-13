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

_DETECTION_MODEL = "craft_mlt_25k.pth"
_MODEL_SUFFIXES = (".pth", ".json", ".yaml", ".yml", ".txt", ".zip")

# EasyOCR 的语言模型文件名（与 easyocr 内部 recognizer 约定一致）
_LANG_MODELS = {
    'ch_sim': 'zh_sim_g2.pth',
    'ch_tra': 'zh_tra_g2.pth',
    'en': 'en_g2.pth',
    'ja': 'ja_g2.pth',
    'ko': 'ko_g2.pth',
    'fr': 'fr_g2.pth',
    'de': 'de_g2.pth',
    'es': 'es_g2.pth',
    'ru': 'ru_g2.pth',
    'pt': 'pt_g2.pth',
    'it': 'it_g2.pth',
    'ar': 'ar_g2.pth',
    'th': 'th_g2.pth',
    'vi': 'vi_g2.pth',
}

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
        'how': '设置页点「安装」装引擎；模型会自动下载，也可手动下载后'
               '点「导入本地模型」离线使用',
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


def resolve_model_dir(languages=None, root=None, legacy=None):
    """给 ``easyocr.Reader(model_storage_directory=...)`` 用的目录。

    优先级：用户导入目录 → 老版本默认目录 → 统一管理目录（新下载落这里）。
    ``root`` / ``legacy`` 主要供测试与检测函数传入自定义目录。
    """
    root = str(root or MODEL_ROOT)
    legacy = str(legacy or LEGACY_EASY_DIR)
    if has_required_models(root, languages):
        return root
    if has_required_models(legacy, languages):
        return legacy
    return root


def detect_local_models(languages=None, root=None, legacy=None):
    """只读检测：现在有哪些模型文件、缺哪些、能不能离线跑。"""
    root = str(root or MODEL_ROOT)
    legacy = str(legacy or LEGACY_EASY_DIR)
    own = _model_files(root)
    old = _model_files(legacy)
    merged = dict(old)
    merged.update(own)          # 统一管理目录优先
    needed = required_model_files(languages)
    resolved = resolve_model_dir(languages, root=root, legacy=legacy)
    # 缺什么按「识别器真正会读的那个目录」算：模型分散在两处时不能报
    # 「一个都不缺」却又判定无法离线识别。
    resolved_files = _model_files(resolved)
    missing = [name for name in needed if not resolved_files.get(name)]
    return {
        'root': root, 'legacy_root': legacy,
        'files': {k: v for k, v in sorted(merged.items())},
        'own_files': own, 'legacy_files': old,
        'needed': needed, 'missing': missing,
        'has_detection': bool(merged.get(_DETECTION_MODEL)),
        'ready': (models_complete(root, languages)
                  or models_complete(legacy, languages)),
        'can_use_offline': (has_required_models(root, languages)
                            or has_required_models(legacy, languages)),
        'resolved_dir': resolved,
        'total_bytes': _total_bytes(root) + _total_bytes(legacy),
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


def model_cache_summary(info=None, languages=None):
    info = info or model_cache_info()
    root = str(info.get('root') or '')
    files = info.get('models') or []
    if not files:
        return ("尚无本地 OCR 模型（可点「检测本地模型」或手动导入）"
                " · 目录 " + root)
    present = {m.get('name') for m in files}
    needed = required_model_files(languages)
    missing = [name for name in needed if name not in present]
    tail = ("，模型齐全可离线识别" if not missing
            else f"，还缺 {len(missing)} 个文件")
    return (f"共 {len(files)} 个模型文件，占用 "
            f"{human_size(info.get('total_bytes'))}{tail}")


def import_local_model(source, root=None):
    """把手动下载的模型目录 / 单个文件放进统一管理目录。

    不移动、不删除用户原文件；同名文件直接按所选目录覆盖。返回
    ``(模型目录现有文件数, 本次复制的文件名列表)``；目录里一个模型文件
    都没有时抛中文异常。
    """
    root = str(root or MODEL_ROOT)
    # Windows 路径大小写不敏感，但 abspath 不归一化大小写：用户选到
    # 「C:\Models」而 root 是「c:\models」时，字符串比较会误判成两个目录，
    # copy2 随后抛英文 SameFileError。统一 normcase 才能给出中文提示。
    root = os.path.normcase(os.path.abspath(root))
    src = os.path.normcase(os.path.abspath(str(source or '')))
    if not os.path.exists(src):
        raise RuntimeError("所选路径不存在，请重新选择")
    if os.path.isdir(src) and src == root:
        # 文件对话框默认就停在模型目录，用户直接点「确定」时别谎报导入成功
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
            "例如 craft_mlt_25k.pth、zh_sim_g2.pth、en_g2.pth")
    copied = []
    for path in candidates:
        name = os.path.basename(path)
        target = os.path.join(root, name)
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
        copied.append(name)
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
    names = "、".join(detect['missing']) if detect['missing'] else "（无）"
    return (
        "模型文件可从 EasyOCR 官方模型页 / GitHub Release 手动下载。\n"
        "放到哪里：点设置页「打开模型文件夹」直接定位，"
        "或点「复制路径」把目录粘进下载工具。\n"
        "每台电脑的目录都不一样，这里不写死路径，以按钮打开的为准。\n"
        "当前缺少：" + names + "\n"
        "必需：检测模型 craft_mlt_25k.pth + 至少一门语言模型"
        "（简体中文 zh_sim_g2.pth / 英文 en_g2.pth）。\n"
        "放好后点「检测本地模型」，识别时会自动优先用本地文件，不再联网下载。\n"
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
    """打开模型目录前先创建，返回绝对路径。"""
    Path(MODEL_ROOT).mkdir(parents=True, exist_ok=True)
    return MODEL_ROOT
