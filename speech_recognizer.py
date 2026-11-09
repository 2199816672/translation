#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音识别（ASR）：模型目录管理、下载、本地模型导入、音视频转录。

识别后端：
- 本地模型：faster-whisper（CPU int8 / 有 CUDA 时自动用 GPU）
- 在线 API：OpenAI 兼容的 /audio/transcriptions 接口

安全约束（API 请求）：
- 仅允许 http/https；
- 请求前对最终 URL 解析主机，拒绝 localhost、环回、私有与保留地址；
- 禁止跟随重定向。
"""
import ipaddress
import json
import mmap
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

# 本地模型缓存目录（用户目录下，不写入项目）
from subprocess_util import pip_cmd, popen_quiet, run_quiet

MODEL_ROOT = os.path.join(os.path.expanduser("~"), ".cache", "trans_asr", "models")

# 可下载模型目录（faster-whisper 的 CTranslate2 版本）
# mb            — 实际下载体积（MB，来自 HF 仓库文件大小），推荐时用来判断磁盘空间
# min_ram_mb    — CPU int8 推理所需的最低可用内存
# min_vram_mb   — GPU float16 推理所需的显存
# cpu_ok        — 纯 CPU 上是否仍有实用速度（大模型在 CPU 上会慢到不可用）
# lang          — multi 多语言 / en 仅英文（.en 与 distil 系列不能识别中文）
# quality       — 相对精度 1~5，用于同级排序
MODEL_CATALOG = {
    'tiny': {
        'repo': 'Systran/faster-whisper-tiny',
        'size': '约 75 MB', 'mb': 75, 'desc': '最快，精度一般',
        'min_ram_mb': 512, 'min_vram_mb': 1000, 'cpu_ok': True,
        'lang': 'multi', 'quality': 1,
    },
    'base': {
        'repo': 'Systran/faster-whisper-base',
        'size': '约 145 MB', 'mb': 145, 'desc': '速度快，精度尚可',
        'min_ram_mb': 800, 'min_vram_mb': 1200, 'cpu_ok': True,
        'lang': 'multi', 'quality': 2,
    },
    'small': {
        'repo': 'Systran/faster-whisper-small',
        'size': '约 480 MB', 'mb': 480, 'desc': '推荐：速度与精度平衡',
        'min_ram_mb': 1600, 'min_vram_mb': 2000, 'cpu_ok': True,
        'lang': 'multi', 'quality': 3,
    },
    'medium': {
        'repo': 'Systran/faster-whisper-medium',
        'size': '约 1.5 GB', 'mb': 1500, 'desc': '精度高，速度较慢',
        'min_ram_mb': 3000, 'min_vram_mb': 4000, 'cpu_ok': False,
        'lang': 'multi', 'quality': 4,
    },
    'large-v3-turbo': {
        'repo': 'deepdml/faster-whisper-large-v3-turbo-ct2',
        'size': '约 1.6 GB', 'mb': 1600,
        'desc': '高精度且速度快，有独显时首选',
        'min_ram_mb': 3000, 'min_vram_mb': 3500, 'cpu_ok': False,
        'lang': 'multi', 'quality': 5,
    },
    'large-v3': {
        'repo': 'Systran/faster-whisper-large-v3',
        'size': '约 3.1 GB', 'mb': 3100, 'desc': '最高精度，需要较好硬件',
        'min_ram_mb': 5000, 'min_vram_mb': 7000, 'cpu_ok': False,
        'lang': 'multi', 'quality': 5,
    },
    # ── 以下为英文专用：英语内容更快更准，但不能识别中文等多语言 ──
    'tiny.en': {
        'repo': 'Systran/faster-whisper-tiny.en',
        'size': '约 75 MB', 'mb': 75, 'desc': '最快，仅英文',
        'min_ram_mb': 512, 'min_vram_mb': 1000, 'cpu_ok': True,
        'lang': 'en', 'quality': 1,
    },
    'base.en': {
        'repo': 'Systran/faster-whisper-base.en',
        'size': '约 145 MB', 'mb': 145, 'desc': '速度快，仅英文',
        'min_ram_mb': 800, 'min_vram_mb': 1200, 'cpu_ok': True,
        'lang': 'en', 'quality': 2,
    },
    'small.en': {
        'repo': 'Systran/faster-whisper-small.en',
        'size': '约 480 MB', 'mb': 480, 'desc': '速度与精度平衡，仅英文',
        'min_ram_mb': 1600, 'min_vram_mb': 2000, 'cpu_ok': True,
        'lang': 'en', 'quality': 3,
    },
    'medium.en': {
        'repo': 'Systran/faster-whisper-medium.en',
        'size': '约 1.5 GB', 'mb': 1500, 'desc': '精度高，仅英文',
        'min_ram_mb': 3000, 'min_vram_mb': 4000, 'cpu_ok': False,
        'lang': 'en', 'quality': 4,
    },
    'distil-small.en': {
        'repo': 'Systran/faster-distil-whisper-small.en',
        'size': '约 320 MB', 'mb': 320, 'desc': '蒸馏加速，仅英文',
        'min_ram_mb': 1200, 'min_vram_mb': 1600, 'cpu_ok': True,
        'lang': 'en', 'quality': 2,
    },
    'distil-medium.en': {
        'repo': 'Systran/faster-distil-whisper-medium.en',
        'size': '约 760 MB', 'mb': 760, 'desc': '蒸馏加速，仅英文',
        'min_ram_mb': 2000, 'min_vram_mb': 2400, 'cpu_ok': True,
        'lang': 'en', 'quality': 3,
    },
    'distil-large-v3': {
        'repo': 'Systran/faster-distil-whisper-large-v3',
        'size': '约 1.5 GB', 'mb': 1450,
        'desc': '蒸馏大模型，接近 large-v3 且快数倍，仅英文',
        'min_ram_mb': 3000, 'min_vram_mb': 3200, 'cpu_ok': False,
        'lang': 'en', 'quality': 5,
    },
}


def model_label(name):
    """下拉框显示文本：英文专用型号显式标注，避免误选后识别不了中文。"""
    info = MODEL_CATALOG.get(name)
    if not info:
        return f"{name}（本地导入）"
    tag = " · 仅英文" if info.get('lang') == 'en' else ""
    return f"{name}（{info['size']}）{tag}"

# 判断本地模型是否完整的必备文件
REQUIRED_MODEL_FILES = ('model.bin', 'config.json', 'tokenizer.json')

# 支持的音视频扩展名（解码由 faster-whisper 自带的 PyAV 完成，无需另装 ffmpeg）
AUDIO_VIDEO_EXTS = {
    '.mp3', '.wav', '.m4a', '.flac', '.ogg', '.aac', '.wma', '.opus',
    '.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.m4v', '.ts', '.wmv',
}

# 本项目语言码 -> Whisper 语言码
WHISPER_LANG_MAP = {
    'zh-CN': 'zh', 'zh-TW': 'zh', 'en': 'en', 'ja': 'ja', 'ko': 'ko',
    'fr': 'fr', 'de': 'de', 'es': 'es', 'ru': 'ru',
}

# Whisper 识别结果的语言码 -> 中文显示名
LANGUAGE_NAMES = {
    'zh': '中文', 'en': '英语', 'ja': '日语', 'ko': '韩语', 'fr': '法语',
    'de': '德语', 'es': '西班牙语', 'ru': '俄语', 'it': '意大利语',
    'pt': '葡萄牙语', 'yue': '粤语', 'ar': '阿拉伯语', 'hi': '印地语',
    'th': '泰语', 'vi': '越南语', 'nl': '荷兰语', 'pl': '波兰语',
    'tr': '土耳其语', 'uk': '乌克兰语',
}


def language_display(code):
    """Whisper 语言码 -> 中文名（未知时回退原码）。"""
    if not code:
        return ''
    return LANGUAGE_NAMES.get(str(code).lower(), str(code))


# ── 模型目录与可用性 ─────────────────────────────────────────

def model_dir(name):
    """下载模型的本地目录。"""
    return os.path.join(MODEL_ROOT, name)


def _model_complete(path):
    if not path or not os.path.isdir(path):
        return False
    for f in REQUIRED_MODEL_FILES:
        if not os.path.exists(os.path.join(path, f)):
            return False
    return True


def is_model_ready(name):
    """下载模型是否完整可用。"""
    return _model_complete(model_dir(name))


def imported_models():
    """配置中登记的本地导入模型 {名称: 路径}（实时读盘，导入后当次即可用）。"""
    from config import load_user_config
    models = load_user_config().get('asr_imported_models') or {}
    return models if isinstance(models, dict) else {}


def is_imported_model_ready(name):
    path = imported_models().get(name)
    return _model_complete(path)


def resolve_model_path(name):
    """返回 (模型目录, 是否导入模型)；不可用返回 (None, False)。"""
    if is_imported_model_ready(name):
        return imported_models().get(name), True
    if is_model_ready(name):
        return model_dir(name), False
    return None, False


def import_local_model(folder):
    """校验一个本地 faster-whisper 模型目录，返回登记名（目录名）。

    不复制文件，由调用方把 {名称: 路径} 写入用户配置。
    """
    folder = os.path.abspath(folder)
    if not _model_complete(folder):
        raise RuntimeError(
            "该目录不是完整的 faster-whisper 模型（需包含 "
            "model.bin / config.json / tokenizer.json）")
    return os.path.basename(os.path.normpath(folder)) or "local_model"


# ── 依赖检查与安装 ──────────────────────────────────────────

def check_dependencies():
    """检查 faster-whisper 是否可用，返回 (ok, message)。

    只查元数据、不执行 import：AVPage 在主线程构造时会调用它，
    真正 import faster_whisper 会连带加载 ctranslate2 / CUDA，卡住启动数秒。
    依赖残缺时由 transcribe_local() 捕获 ImportError 再提示安装。
    """
    import importlib.util
    missing = []
    for mod in ('faster_whisper', 'ctranslate2', 'av', 'tokenizers'):
        try:
            if importlib.util.find_spec(mod) is None:
                missing.append(mod)
        except (ImportError, ValueError):
            missing.append(mod)
    if missing:
        return False, ("未安装语音识别组件：" + ", ".join(missing))
    return True, ""


# 依次尝试的 pip 源（默认配置 → 阿里云 → PyPI 官方 → 清华）
_PIP_INDEXES = (
    ("默认配置", ()),
    ("阿里云镜像", ("-i", "https://mirrors.aliyun.com/pypi/simple/")),
    ("PyPI 官方", ("-i", "https://pypi.org/simple")),
    ("清华镜像", ("-i", "https://pypi.tuna.tsinghua.edu.cn/simple/")),
)

_PIP_PACKAGES = ("faster-whisper>=1.0.0", "zhconv>=1.4.3")


def _run_pip(extra_args, progress_cb=None):
    """执行一次 pip install，返回 (ok, 末尾日志)。"""
    cmd, hint = pip_cmd(
        _PIP_PACKAGES,
        ['--retries', '3', '--timeout', '60'] + list(extra_args),
    )
    if cmd is None:
        return False, hint
    try:
        proc = popen_quiet(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        tail = []
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                tail.append(line.strip())
                if progress_cb:
                    progress_cb(line)
        proc.wait()
        return proc.returncode == 0, '\n'.join(tail[-8:])
    except Exception as e:
        return False, f"执行失败: {e}"


def install_dependencies(progress_cb=None):
    """安装 faster-whisper：多镜像自动重试，全部失败时给出手动命令。

    国内镜像偶发 403（限流/同步问题）时，会自动切换阿里云 / PyPI 官方。
    """
    _, hint = pip_cmd(_PIP_PACKAGES)
    if hint:  # 冻结态：不做四个镜像的无谓重试
        return False, hint
    last_tail = ""
    total = len(_PIP_INDEXES)
    for i, (label, extra) in enumerate(_PIP_INDEXES, 1):
        if progress_cb:
            progress_cb(f"[{i}/{total}] 使用{label}安装 faster-whisper …")
        ok, last_tail = _run_pip(extra, progress_cb)
        if ok:
            return True, f"语音识别组件安装成功（{label}）"
    manual = (
        "安装失败（已自动尝试默认配置 / 阿里云 / PyPI 官方 / 清华镜像）。\n\n"
        "可在命令行手动安装：\n"
        "  pip install faster-whisper\n"
        "若镜像报 403，换官方源：\n"
        "  pip install faster-whisper -i https://pypi.org/simple\n\n"
        f"最后一次错误输出：\n{last_tail}"
    )
    return False, manual


# ── 模型下载（带进度）───────────────────────────────────────

class _ProgressTqdm(object):
    """把 tqdm 下载进度回调给 UI。"""

    callback = None

    @classmethod
    def make(cls, base_cls):
        class _Tqdm(base_cls):
            def update(self, n=1):
                super().update(n)
                cb = _ProgressTqdm.callback
                # 只回报字节级（大文件）进度，忽略"文件个数"等小计数条
                if cb and self.total and self.total >= 1_000_000:
                    try:
                        cb(int(self.n), int(self.total))
                    except Exception:
                        pass
        return _Tqdm


def download_model(name, on_progress=None):
    """下载指定模型（镜像优先、官方兜底），进度回调 on_progress(done, total)。"""
    if name not in MODEL_CATALOG:
        raise RuntimeError(f"未知模型: {name}")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise RuntimeError("请先安装依赖: pip install huggingface-hub")

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    repo = MODEL_CATALOG[name]['repo']
    target = model_dir(name)
    os.makedirs(target, exist_ok=True)

    try:
        from tqdm.auto import tqdm as _base_tqdm
        progress_cls = _ProgressTqdm.make(_base_tqdm)
    except Exception:
        progress_cls = None
    _ProgressTqdm.callback = on_progress

    endpoints = ["https://hf-mirror.com", "https://huggingface.co"]
    last_error = None
    for endpoint in endpoints:
        kwargs = {"repo_id": repo, "local_dir": target, "endpoint": endpoint}
        try:
            if on_progress:
                on_progress(0, 0)
            if progress_cls is not None:
                try:
                    snapshot_download(tqdm_class=progress_cls, **kwargs)
                except TypeError:
                    snapshot_download(**kwargs)
            else:
                snapshot_download(**kwargs)
            if is_model_ready(name):
                return
            last_error = RuntimeError("下载完成但模型文件不完整")
        except Exception as e:
            last_error = e
        finally:
            _ProgressTqdm.callback = None
    _ProgressTqdm.callback = None
    raise RuntimeError(f"模型下载失败: {last_error}")


# ── 音视频时长探测 ──────────────────────────────────────────

def probe_duration(path):
    """用 PyAV 读取音视频时长（秒）；失败返回 0。"""
    try:
        import av
    except ImportError:
        print("probe_duration: 未安装 PyAV，时长探测跳过")
        return 0.0
    try:
        with av.open(path) as container:
            if container.duration:
                return float(container.duration) / 1_000_000.0
    except Exception as e:
        # 只降级时长显示，但别把解码错误静默吞掉
        print(f"probe_duration: 读取时长失败 ({e})")
    return 0.0


# ── 本机硬件画像与模型推荐 ────────────────────────────────

_hardware_cache = None
_gpu_cache = None


def _memory_mb():
    """返回 (物理内存总量 MB, 当前可用 MB)，不引入 psutil。"""
    if sys.platform == 'win32':
        try:
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [
                    ('dwLength', ctypes.c_ulong),
                    ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
                ]

            st = _MemStatus()
            st.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullTotalPhys // 1048576, st.ullAvailPhys // 1048576
        except Exception:
            pass
    try:
        vals = {}
        with open('/proc/meminfo', 'r', encoding='utf-8') as fh:
            for line in fh:
                key, _, rest = line.partition(':')
                if key in ('MemTotal', 'MemAvailable'):
                    vals[key] = int(rest.strip().split()[0]) // 1024
        if 'MemTotal' in vals:
            return vals['MemTotal'], vals.get('MemAvailable', 0)
    except Exception:
        pass
    return 0, 0


def _cpu_name():
    if sys.platform == 'win32':
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r'HARDWARE\DESCRIPTION\System\CentralProcessor\0')
            name = winreg.QueryValueEx(key, 'ProcessorNameString')[0]
            winreg.CloseKey(key)
            return str(name).strip()
        except Exception:
            pass
    try:
        with open('/proc/cpuinfo', 'r', encoding='utf-8') as fh:
            for line in fh:
                if line.lower().startswith('model name'):
                    return line.partition(':')[2].strip()
    except Exception:
        pass
    return platform.processor() or '未知 CPU'


def _disk_free_mb():
    """模型缓存目录所在磁盘的可用空间（MB）。"""
    try:
        probe = os.path.abspath(MODEL_ROOT)
        while probe and not os.path.exists(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        return shutil.disk_usage(probe).free // 1048576
    except Exception:
        return 0


def _gpu_info():
    """返回 (has_cuda, name, vram_mb, error)。

    会 import ctranslate2 并触发 CUDA 初始化 + nvidia-smi，调用方应放后台线程。
    """
    try:
        import ctranslate2
        count = int(ctranslate2.get_cuda_device_count())
    except ImportError:
        return False, '', 0, '未安装 ctranslate2（faster-whisper 依赖）'
    except Exception as e:
        return False, '', 0, f'GPU 检测失败: {e}'
    if count <= 0:
        return False, '', 0, '未检测到可用的 NVIDIA GPU（需安装 CUDA 驱动）'
    name, vram = '', 0
    try:
        out = run_quiet(
            ['nvidia-smi', '--query-gpu=name,memory.total',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            parts = [s.strip() for s in out.stdout.splitlines()[0].split(',')]
            name = parts[0] if parts else ''
            if len(parts) > 1:
                vram = int(float(parts[1]))  # nounits 查询返回 MiB
    except Exception:
        pass
    return True, name or f'{count} 块 CUDA GPU', vram, ''


def detect_hardware(refresh=False):
    """采集本机硬件画像（结果缓存）。含 CUDA 初始化，务必在后台线程调用。"""
    global _hardware_cache
    if _hardware_cache is not None and not refresh:
        return dict(_hardware_cache)
    total, avail = _memory_mb()
    cuda, gpu_name, vram, gpu_error = _gpu_info()
    hw = {
        'cpu_name': _cpu_name(),
        'cpu_logical': os.cpu_count() or 1,
        'ram_total_mb': total,
        'ram_available_mb': avail,
        'has_cuda': cuda,
        'gpu_name': gpu_name,
        'gpu_vram_mb': vram,
        'gpu_error': gpu_error,
        'disk_free_mb': _disk_free_mb(),
        'platform': sys.platform,
    }
    _hardware_cache = dict(hw)
    return hw


def hardware_summary(hw):
    """多行硬件摘要，用于提示框 / tooltip。"""
    lines = [
        f"CPU：{hw.get('cpu_name') or '未知'}"
        f"（{hw.get('cpu_logical', 1)} 逻辑核）",
        f"内存：{hw.get('ram_total_mb', 0) / 1024:.1f} GB"
        f"（当前可用 {hw.get('ram_available_mb', 0) / 1024:.1f} GB）",
    ]
    if hw.get('has_cuda'):
        vram = hw.get('gpu_vram_mb') or 0
        lines.append(
            f"显卡：{hw.get('gpu_name')}"
            + (f"（显存 {vram / 1024:.1f} GB）" if vram else "")
            + " · 可用 CUDA 加速")
    else:
        lines.append(f"显卡：{hw.get('gpu_error') or '无可用 CUDA'} · 将使用 CPU")
    lines.append(f"模型盘剩余：{hw.get('disk_free_mb', 0) / 1024:.1f} GB")
    return '\n'.join(lines)


def recommend_model(hardware=None, english_only=False):
    """按本机硬件推荐一个模型。

    判据：GPU 按显存、CPU 按可用内存给预算；纯 CPU 排除慢到不可用的大模型；
    磁盘放不下则降级；同级按精度优先、体积小的优先。
    返回 {name, size, desc, reason, detail, hardware}。
    """
    hw = dict(hardware or detect_hardware())
    cuda = bool(hw.get('has_cuda'))
    vram = int(hw.get('gpu_vram_mb') or 0)
    ram_total = int(hw.get('ram_total_mb') or 0)
    ram_avail = int(hw.get('ram_available_mb') or 0)
    cores = int(hw.get('cpu_logical') or 1)
    disk = int(hw.get('disk_free_mb') or 0)

    # 预留一部分给系统和 CTranslate2 运行时开销
    if cuda and vram > 0:
        budget, need = int(vram * 0.85), 'min_vram_mb'
    else:
        # 按物理内存定档，不用瞬时可用内存：用户同时开着浏览器 / IDE 时
        # 可用内存会短暂偏低，不该因此把 16 GB 的机器降到 tiny / base。
        budget, need = max(512, int(ram_total * 0.35)), 'min_ram_mb'

    pool = [(n, i) for n, i in MODEL_CATALOG.items()
            if english_only or i.get('lang') != 'en']
    if not pool:
        pool = list(MODEL_CATALOG.items())

    affordable = [
        x for x in pool
        if x[1]['mb'] * 1.3 <= max(disk, 0) and x[1][need] <= budget
    ]
    if not affordable:
        affordable = sorted(pool, key=lambda x: x[1]['mb'])[:1]

    if not cuda:
        cpu_usable = [x for x in affordable if x[1].get('cpu_ok')]
        if cpu_usable:
            affordable = cpu_usable
        if cores <= 4:
            light = [x for x in affordable if x[1]['mb'] <= 150]
            if light:
                affordable = light

    affordable.sort(key=lambda x: (-x[1]['quality'], x[1]['mb']))
    name, info = affordable[0]

    if cuda:
        device = (f"{hw.get('gpu_name') or 'NVIDIA GPU'}"
                  + (f"（显存 {vram / 1024:.1f} GB）" if vram else ""))
        why = "有 GPU 加速，可以选精度更高的型号"
    else:
        device = "未检测到 NVIDIA GPU，将用 CPU 识别"
        why = (f"{cores} 逻辑核 / {ram_total / 1024:.1f} GB 内存，"
               "大模型在 CPU 上会慢到不可用")
    space = ""
    if disk and info['mb'] * 1.3 > disk:
        space = "；磁盘空间不足，建议先清理再下载"
    elif not cuda and ram_avail and ram_avail < 2048:
        space = "；当前可用内存较少，建议关闭其它程序"

    return {
        'name': name,
        'size': info['size'],
        'desc': info['desc'],
        'reason': f"推荐 {name}（{info['size']}）· {device}。{why}{space}",
        'detail': hardware_summary(hw),
        'hardware': hw,
    }


# ── GPU 检测与计算设备选择 ──────────────────────────────────


def detect_gpu(refresh=False):
    """检测可用的 CUDA GPU：返回 (available, description)。

    结果缓存；refresh=True 时重新检测（nvidia-smi 调用略慢）。
    """
    global _gpu_cache
    if _gpu_cache is not None and not refresh:
        return _gpu_cache
    ok, name, vram, error = _gpu_info()
    if ok:
        desc = f"已检测到 GPU：{name}"
        if vram:
            desc += f"（显存 {vram / 1024:.1f} GB）"
    else:
        desc = error or "未检测到可用的 NVIDIA GPU（需安装 CUDA 驱动）"
    _gpu_cache = (ok, desc)
    return _gpu_cache


def _resolve_device(preference='auto'):
    """根据用户偏好选择计算设备，返回 (device, compute_type, notice)。"""
    available, desc = detect_gpu()
    pref = str(preference or 'auto').lower()
    if pref == 'cpu':
        return 'cpu', 'int8', ''
    if pref == 'gpu':
        if available:
            return 'cuda', 'float16', ''
        return 'cpu', 'int8', f"{desc}，已改用 CPU 识别"
    # auto：有 GPU 用 GPU，否则 CPU
    if available:
        return 'cuda', 'float16', ''
    return 'cpu', 'int8', ''


# ── 本地模型转录 ────────────────────────────────────────────

# 纯标签/纯符号片段（如 [Music]、♪♪、（掌声）），对字幕没有意义
_TAG_ONLY = re.compile(r'^[\[\(（【<][^\]\)）】>]*[\]\)）】>]$')
_SYMBOL_ONLY = re.compile(r'^[\s♪♫♬\[\]\(\)（）【】<>.,，。!！?？~～\-—…、:：;；"\'`]*$')

# 源语言为简体时给 Whisper 的引导语（模型默认常输出繁体）
_ZH_SIMPLIFIED_PROMPT = "以下是普通话的句子，请使用简体中文输出。"


def clean_segment_text(text):
    """清理识别片段：去首尾空白，丢弃纯标签/纯符号（音乐标记等）。"""
    t = (text or '').strip()
    if not t:
        return ''
    if _TAG_ONLY.match(t) or _SYMBOL_ONLY.match(t):
        return ''
    return t


def normalize_chinese(text, language):
    """按所选源语言统一中文简繁：zh-CN -> 简体，zh-TW -> 繁体。"""
    if not text or language not in ('zh-CN', 'zh-TW'):
        return text
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            import zhconv
        target = 'zh-cn' if language == 'zh-CN' else 'zh-tw'
        return zhconv.convert(text, target)
    except Exception:
        # 未安装 zhconv 时仅依赖 initial_prompt 引导，不影响识别
        return text


# ── 共享 Whisper 模型缓存 ───────────────────────────────────
# 同一 (模型路径, 设备, 计算类型) 只实例化一次，避免每次转录都重建模型
# （重建一次常 >1s，且常驻占用数百 MB~GB 级内存）。并提供每模型锁，保证
# 并发调用同一模型时 transcribe 串行执行（faster-whisper / CTranslate2 的
# 模型实例非线程安全，并发调用会崩溃）。
_shared_whisper_models = {}
_shared_whisper_locks = {}
_shared_whisper_lock = threading.Lock()


def _get_whisper_model(path, device, compute):
    """获取（必要时创建）共享的 Whisper 模型，并返回该模型的互斥锁。"""
    key = (os.path.normcase(os.path.abspath(path)), device, compute)
    with _shared_whisper_lock:
        lock = _shared_whisper_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _shared_whisper_locks[key] = lock
        model = _shared_whisper_models.get(key)
        if model is None:
            from faster_whisper import WhisperModel
            model = WhisperModel(path, device=device, compute_type=compute)
            _shared_whisper_models[key] = model
    return model, lock


def _fmt_clock(seconds):
    """秒 -> mm:ss（用于进度显示）。"""
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def transcribe_local(audio_path, model_name, language='auto',
                     progress_cb=None, cancel_event=None,
                     vad_filter=True, vad_threshold=0.5, beam_size=5,
                     segment_callback=None, device_preference='auto'):
    """本地 faster-whisper 转录。

    参数：
      vad_filter        — 是否先做人声检测（歌曲/纯音乐建议关闭）
      vad_threshold     — 人声检测阈值 0.1~0.9（越低越宽松）
      beam_size         — 解码宽度（1 快速 / 5 标准 / 10 精确）
      segment_callback  — 每识别出一段立即回调（实时字幕），参数为条目 dict
      device_preference — auto / cpu / gpu；gpu 不可用时自动回退 CPU

    返回 {"segments": [...], "duration": 秒, "language": 码, "device": 设备}。
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("请先安装语音识别组件（faster-whisper）")

    path, _ = resolve_model_path(model_name)
    if not path:
        raise RuntimeError(f"模型 {model_name} 未就绪，请先下载或导入")

    # 计算设备：auto / cpu / gpu（不可用时自动回退 CPU 并提示）
    device, compute, device_notice = _resolve_device(device_preference)
    if device_notice and progress_cb:
        progress_cb(0, device_notice)

    # 共享模型缓存 + 每模型锁：避免每次重建、并串行化并发 transcribe
    try:
        model, model_lock = _get_whisper_model(path, device, compute)
    except Exception as e:
        if device == 'cuda':
            if progress_cb:
                progress_cb(0, f"GPU 初始化失败，已改用 CPU 识别（{str(e)[:60]}）")
            device, compute = 'cpu', 'int8'
            model, model_lock = _get_whisper_model(path, device, compute)
        else:
            raise
    lang = None if language in ('auto', '', None) else \
        WHISPER_LANG_MAP.get(language, language)

    threshold = max(0.1, min(0.9, float(vad_threshold)))
    beam = max(1, min(10, int(beam_size)))

    def _run(use_vad, progress_scale=1.0):
        # 共享模型非线程安全：同一模型的所有 transcribe 调用必须串行
        with model_lock:
            kwargs = {'language': lang, 'beam_size': beam,
                      'vad_filter': bool(use_vad)}
            if use_vad:
                kwargs['vad_parameters'] = {'threshold': threshold}
            # 目标为简体中文时用引导语压制繁体输出
            if language == 'zh-CN':
                kwargs['initial_prompt'] = _ZH_SIMPLIFIED_PROMPT
            segments_iter, info = model.transcribe(audio_path, **kwargs)
            duration = float(getattr(info, 'duration', 0) or 0)
            if duration <= 0:
                duration = probe_duration(audio_path)
            entries = []
            for seg in segments_iter:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("已取消识别")
                text = normalize_chinese(clean_segment_text(seg.text), language)
                if text:
                    entry = {
                        'start': int(seg.start * 1000),
                        'end': int(seg.end * 1000),
                        'text': text,
                    }
                    entries.append(entry)
                    # 实时字幕：每出一段立即通知界面
                    if segment_callback is not None:
                        try:
                            segment_callback(dict(entry))
                        except Exception:
                            pass
                if progress_cb and duration > 0:
                    processed = min(seg.end, duration)
                    remaining = max(0.0, duration - processed)
                    percent = int(min(98, processed / duration * 100
                                      * progress_scale))
                    progress_cb(
                        percent,
                        f"已识别 {_fmt_clock(processed)} / {_fmt_clock(duration)}"
                        f"（剩余 {_fmt_clock(remaining)}）· {len(entries)} 段")
            return entries, duration, getattr(info, 'language', '') or ''

    entries, duration, detected = _run(bool(vad_filter))
    # 人声检测可能把唱歌/纯音乐全部过滤掉：自动改用整段识别重试一次
    if not entries and vad_filter:
        if progress_cb:
            progress_cb(0, "人声检测未发现语音，正在改用整段识别…")
        entries, duration, detected = _run(False)
    if not entries:
        raise RuntimeError(
            "未识别到语音内容。\n"
            "若是歌曲或纯音乐，请到「设置 → 语音识别」关闭人声检测后重试；\n"
            "也可指定音频语言，或换用更大的模型（small / large-v3-turbo）。")

    if progress_cb:
        progress_cb(100, f"转录完成 · 共 {len(entries)} 段 · "
                         f"总时长 {_fmt_clock(duration)}")
    return {
        'segments': entries,
        'duration': duration,
        'language': detected,
        'device': device,
    }


# ── 在线 API 转录 ───────────────────────────────────────────

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁止跟随重定向，避免跳转到非预期地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_api_url(url, allow_private=False):
    """校验 API 地址：仅 http/https；默认要求解析到公网地址。

    allow_private=True 供「免费本地服务」引擎使用：用户自己在本机/局域网
    跑 whisper.cpp server、speaches 等 OpenAI 兼容服务时，127.0.0.1 是合法
    目标。默认仍严格拒绝 localhost / 内网地址，避免 SSRF。
    """
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https'):
        raise RuntimeError("API 地址仅支持 http/https")
    host = (parts.hostname or '').strip().lower()
    if not host:
        raise RuntimeError("API 地址缺少主机名")
    if allow_private:
        return url
    if host == 'localhost' or host.endswith('.localhost'):
        raise RuntimeError("API 地址不允许使用 localhost")
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            raise RuntimeError("API 地址解析到内网/保留地址，已拒绝")
    return url


def validate_api_url(base_url, allow_private=False):
    """供界面提前校验 API 根地址，返回规范化地址。"""
    return _validate_api_url(
        str(base_url or '').strip().rstrip('/'), allow_private=allow_private)


class _MultipartBody:
    """multipart 请求体的磁盘暂存句柄。

    旧实现用 fh.read() 把整个音视频读进内存再拼 bytes，GB 级文件会直接
    MemoryError。这里把请求体写进临时文件并用 mmap 映射：urllib 会按内存页
    分块发送，Python 进程内存占用接近 0。用完必须 close()。
    """

    def __init__(self, fields, file_field, file_path):
        boundary = '----transASR' + uuid.uuid4().hex
        self.content_type = f'multipart/form-data; boundary={boundary}'
        self._tmp = None
        self._path = None
        self.body = None
        # Windows 上 TemporaryFile 带 DELETE_ON_CLOSE，无法 mmap；用真实临时文件
        fd, path = tempfile.mkstemp(prefix='trans_asr_', suffix='.mp')
        self._path = path
        tmp = os.fdopen(fd, 'w+b')
        try:
            for key, value in fields.items():
                tmp.write(
                    f'--{boundary}\r\n'
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                    f'{value}\r\n'.encode('utf-8'))
            raw_name = os.path.basename(file_path)
            safe_name = ''.join(
                ch for ch in raw_name if ch.isascii() and ch not in '"\\\r\n') \
                or 'audio.bin'
            tmp.write(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{safe_name}"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n'.encode('utf-8'))
            with open(file_path, 'rb') as fh:
                shutil.copyfileobj(fh, tmp, 1024 * 1024)
            tmp.write(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
            # 必须刷盘：带缓冲的文件对象若不 flush，mmap 看到的文件长度是 0
            tmp.flush()
            size = tmp.tell()
            self._tmp = tmp
            self.body = mmap.mmap(tmp.fileno(), size, access=mmap.ACCESS_READ)
        except Exception:
            tmp.close()
            try:
                os.unlink(path)
            except OSError:
                pass
            raise

    def close(self):
        if self.body is not None:
            try:
                self.body.close()
            except Exception:
                pass
            self.body = None
        if self._tmp is not None:
            try:
                self._tmp.close()
            except Exception:
                pass
            self._tmp = None
        if self._path:
            try:
                os.unlink(self._path)
            except OSError:
                pass
            self._path = None


def _build_multipart(fields, file_field, file_path):
    """构造 multipart/form-data，返回 (body, content_type, holder)。"""
    holder = _MultipartBody(fields, file_field, file_path)
    return holder.body, holder.content_type, holder


def transcribe_api(audio_path, base_url, api_key, model, language='auto',
                   progress_cb=None, allow_private=False):
    """OpenAI 兼容接口转录：POST {base}/audio/transcriptions。

    请求前对最终 URL 做协议 / 主机 / 解析 IP 校验，并禁止跟随重定向。
    allow_private=True 允许指向本机或局域网的自建免费识别服务。
    """
    base = str(base_url or '').strip().rstrip('/')
    url = base + '/audio/transcriptions'
    url = _validate_api_url(url, allow_private=allow_private)

    if not model or not str(model).strip():
        raise RuntimeError("请填写 API 模型名（如 whisper-1）")

    fields = {'model': str(model).strip(), 'response_format': 'verbose_json'}
    if language not in ('auto', '', None):
        fields['language'] = WHISPER_LANG_MAP.get(language, language)

    def _send(fmt_fields):
        body, content_type, holder = _build_multipart(
            fmt_fields, 'file', audio_path)
        try:
            headers = {'Content-Type': content_type}
            if api_key:
                headers['Authorization'] = 'Bearer ' + str(api_key).strip()
            req = urllib.request.Request(
                url, data=body, headers=headers, method='POST')
            opener = urllib.request.build_opener(_NoRedirectHandler())
            with opener.open(req, timeout=600) as resp:
                return resp.read()
        finally:
            holder.close()

    if progress_cb:
        try:
            size_mb = os.path.getsize(audio_path) / 1048576
        except OSError:
            size_mb = 0
        upload_msg = "正在上传音频…"
        if size_mb > 25:
            upload_msg = (
                f"正在上传 {size_mb:.0f} MB 文件…（多数在线接口限制 25 MB，"
                "文件过大建议改用本地模型或先提取音频）")
        progress_cb(5, upload_msg)
    try:
        raw = _send(fields)
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise RuntimeError(f"API 请求失败: HTTP {e.code}")
        # 部分服务不支持 verbose_json，退回普通 json
        fields = dict(fields, response_format='json')
        try:
            raw = _send(fields)
        except urllib.error.HTTPError as e2:
            raise RuntimeError(f"API 请求失败: HTTP {e2.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接 API: {e.reason}")

    body = json.loads(raw.decode('utf-8', errors='replace'))

    if progress_cb:
        progress_cb(90, "正在解析结果…")
    segments = []
    for s in body.get('segments') or []:
        text = normalize_chinese(
            str(s.get('text', '')).strip(), language)
        if not text:
            continue
        segments.append({
            'start': int(float(s.get('start', 0)) * 1000),
            'end': int(float(s.get('end', 0)) * 1000),
            'text': text,
        })
    duration = float(body.get('duration') or 0)
    if not segments and body.get('text'):
        if duration <= 0:
            duration = probe_duration(audio_path)
        segments = [{
            'start': 0,
            'end': int(duration * 1000),
            'text': normalize_chinese(str(body['text']).strip(), language),
        }]
    if not segments:
        raise RuntimeError("API 未返回识别结果")
    if progress_cb:
        progress_cb(100, "转录完成")
    return {
        'segments': segments,
        'duration': duration,
        'language': body.get('language', '') or '',
    }
