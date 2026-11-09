# -*- coding: utf-8 -*-
"""实时字幕核心：音频采集 → 静音切分 → 增量识别 → 异步翻译。

这个模块不含任何 Qt 代码，方便单独测试。所有耗时工作都在后台线程：

    采集线程  →  queue(100ms 音频块)
    识别线程  →  常驻 WhisperModel / 在线 API，按语句出字幕
    翻译线程  →  独立队列，网络慢也不会拖慢出原文

调用方通过 callbacks 把结果转成 Qt 信号（后台线程只 emit，不碰 UI）。

速度策略（实时字幕最关心的点）：
  * 模型只加载一次并常驻内存，绝不每段重新 WhisperModel(...)
  * 直接把 16kHz float32 数组喂给 faster-whisper，不落临时 wav
  * beam_size=1、temperature=0、关闭 VAD 与 condition_on_previous_text
  * 静音即切句，说话停顿立刻出字幕；最长片段兜底 2.5 秒
  * 纯静音片段直接丢弃，避免空转与 Whisper 幻听
"""

from __future__ import annotations

from subprocess_util import pip_cmd, run_quiet

import importlib.util
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import wave

import numpy as np

SAMPLE_RATE = 16000
_BLOCK_MS = 100
_BLOCK_FRAMES = SAMPLE_RATE * _BLOCK_MS // 1000
_MAX_QUEUE_BLOCKS = 200

# 静音判定与切句参数
_MIN_SPEECH_FRAMES = int(SAMPLE_RATE * 0.45)   # 太短的片段不送识别
_SILENCE_FRAMES = int(SAMPLE_RATE * 0.32)      # 停顿 320ms 视为一句结束


# ── 依赖 / 设备 ──────────────────────────────────────────────

def dependency_status():
    """返回各能力的可用性与给界面看的提示文字。"""
    mic = importlib.util.find_spec('sounddevice') is not None
    loopback = importlib.util.find_spec('soundcard') is not None
    local_asr = importlib.util.find_spec('faster_whisper') is not None
    parts = []
    if not mic:
        parts.append('缺少 sounddevice（麦克风采集）')
    if not loopback:
        parts.append('缺少 soundcard（电脑内部声音），可在设置里一键安装')
    if not local_asr:
        parts.append('缺少 faster-whisper（本地模型）')
    return {
        'mic': mic,
        'loopback': loopback,
        'local_asr': local_asr,
        'message': '；'.join(parts) if parts else '音频采集与本地识别组件齐全',
    }


def install_loopback_support(progress_cb=None):
    """安装 soundcard（WASAPI 内录支持）。返回 (ok, message)。"""
    mirrors = (
        'https://pypi.org/simple',
        'https://mirrors.aliyun.com/pypi/simple/',
        'https://pypi.tuna.tsinghua.edu.cn/simple',
    )
    last = ''
    for index in mirrors:
        cmd, hint = pip_cmd(
            ['soundcard'],
            ['-i', index, '--disable-pip-version-check'],
        )
        if cmd is None:
            return False, hint
        if progress_cb:
            progress_cb(f'正在安装内录组件…（{index.split("/")[2]}）')
        try:
            out = run_quiet(cmd, capture_output=True, text=True,
                            timeout=180)
            if out.returncode == 0:
                return True, 'soundcard 安装成功，重新打开下拉框即可看到扬声器设备'
            last = (out.stderr or out.stdout or '').strip()[-300:]
        except Exception as e:
            last = str(e)
    return False, f'自动安装失败：{last}\n可手动执行 pip install soundcard'


def _is_mix_name(name):
    low = str(name or '').lower()
    return any(k in low for k in (
        'stereo mix', '立体声混音', 'what u hear', 'loopback',
        'wave out mix', '混音'))


def list_devices(source):
    """列出可选音频设备：[{id, name, kind}]。

    mic      → sounddevice 的输入设备（过滤掉立体声混音）
    loopback → soundcard 的扬声器环回设备；未装 soundcard 时退化到
               系统已启用的「立体声混音」输入设备
    """
    devices = []
    try:
        import sounddevice as sd
        all_devs = list(sd.query_devices())
    except Exception:
        all_devs = []
    for i, dev in enumerate(all_devs):
        if int(dev.get('max_input_channels', 0) or 0) <= 0:
            continue
        name = str(dev.get('name', f'设备 {i}'))
        if source == 'mic':
            if not _is_mix_name(name):
                devices.append({'id': str(i), 'name': name, 'kind': 'mic'})
        elif _is_mix_name(name):
            devices.append({'id': f'mix:{i}',
                            'name': name + '（系统混音）',
                            'kind': 'stereo_mix'})
    if source == 'loopback':
        try:
            import soundcard as sc
            for spk in sc.all_speakers():
                devices.append({
                    'id': 'loopback:' + str(spk.id),
                    'name': str(spk.name) + '（系统内部声音）',
                    'kind': 'loopback',
                })
        except Exception:
            pass
    return devices


# ── 纯函数（便于断言） ────────────────────────────────────────

def rms(audio):
    """音频块均方根能量，用于静音判定。"""
    arr = np.asarray(audio, dtype=np.float32)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr * arr)))


def has_speech(audio, threshold=0.008):
    return rms(audio) >= max(0.0, float(threshold))


def trim_repeated_prefix(new_text, prev_text, min_overlap=5):
    """去掉与上一段尾部重复的前缀（相邻窗口/API 常见的重复词）。"""
    new_text = str(new_text or '').strip()
    prev_text = str(prev_text or '').strip()
    if not new_text or not prev_text:
        return new_text
    limit = min(len(new_text), len(prev_text), 80)
    for n in range(limit, min_overlap - 1, -1):
        if prev_text[-n:].lower() == new_text[:n].lower():
            return new_text[n:].lstrip(' ,.，。!！?？:：;；')
    return new_text


_TAG_RE = re.compile(
    r'[\[\(（【]\s*(?:music|applause|laughter|silence|noise|inaudible|'
    r'掌声|音乐|笑声|无声|噪音|听不清)\s*[\]\)）】]', re.IGNORECASE)


def clean_text(text):
    """复用音视频字幕的文本清理，并额外剔除行内幻觉标记。

    2 秒以内的短窗口比整段文件更容易让 Whisper 幻听出 [Music]、
    (Applause) 之类标记，这些不该出现在实时字幕里。
    """
    try:
        from speech_recognizer import clean_segment_text
        out = clean_segment_text(str(text or ''))
    except Exception:
        out = re.sub(r'\s+', ' ', str(text or '')).strip()
    out = _TAG_RE.sub('', out)
    out = re.sub(r'\s+', ' ', out).strip(' ,.，。!！')
    return out


def float_to_pcm16(audio):
    arr = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    return (arr * 32767.0).astype('<i2').tobytes()


def write_wav(path, audio):
    """把 16kHz 单声道 float32 写成临时 wav（在线 API 需要文件）。"""
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(float_to_pcm16(audio))
    return path


# ── 音频采集 ─────────────────────────────────────────────────

class AudioCapture:
    """把音频块放进 self.chunks，不认识 UI、不认识识别模型。"""

    def __init__(self, source='mic', device_id='', on_error=None):
        self.source = 'loopback' if source == 'loopback' else 'mic'
        self.device_id = str(device_id or '')
        self.on_error = on_error
        self.chunks = queue.Queue(maxsize=_MAX_QUEUE_BLOCKS)
        self._stop = threading.Event()
        self._thread = None
        self._stream = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _emit_error(self, msg):
        if self.on_error:
            try:
                self.on_error(msg)
            except Exception:
                pass

    def _push(self, data):
        arr = np.asarray(data, dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return
        if self.chunks.full():
            try:
                self.chunks.get_nowait()   # 识别跟不上时丢最旧的，保证实时性
            except queue.Empty:
                pass
        try:
            self.chunks.put_nowait(arr.copy())
        except queue.Full:
            pass

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name='caption-audio', daemon=True)
        self._thread.start()

    def _run(self):
        try:
            if self.device_id.startswith('loopback:'):
                self._run_soundcard()
            else:
                self._run_sounddevice()
        except Exception as e:
            self._emit_error(f'音频采集失败：{e}')

    def _device_arg(self):
        raw = self.device_id
        if raw.startswith('mix:'):
            raw = raw[4:]
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return raw

    def _run_sounddevice(self):
        import sounddevice as sd

        def callback(indata, frames, time_info, status):  # noqa: ARG001
            if self._stop.is_set():
                return
            self._push(indata[:, 0] if indata.ndim > 1 else indata)

        kwargs = dict(samplerate=SAMPLE_RATE, channels=1, dtype='float32',
                      blocksize=_BLOCK_FRAMES, callback=callback)
        device = self._device_arg()
        if device is not None:
            kwargs['device'] = device
        with sd.InputStream(**kwargs) as stream:
            self._stream = stream
            while not self._stop.is_set():
                self._stop.wait(0.1)
        self._stream = None

    def _run_soundcard(self):
        import soundcard as sc
        speaker_id = self.device_id.split(':', 1)[1]
        mic = sc.get_microphone(speaker_id, include_loopback=True)
        with mic.recorder(samplerate=SAMPLE_RATE, channels=1) as rec:
            while not self._stop.is_set():
                data = rec.record(numframes=_BLOCK_FRAMES)
                if self._stop.is_set():
                    break
                self._push(data)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        while not self.chunks.empty():
            try:
                self.chunks.get_nowait()
            except queue.Empty:
                break


# ── 识别引擎 ─────────────────────────────────────────────────

class LiveCaptionEngine:
    """实时字幕的状态机。callbacks: on_segment / on_translation /
    on_status / on_error / on_speed。

    为便于离线测试，可注入 capture（需提供 chunks/start/stop）与
    transcriber(audio_float32) -> str。
    """

    def __init__(self, settings, callbacks=None, capture=None,
                 transcriber=None):
        self.cfg = dict(settings or {})
        self.cb = callbacks or {}
        self._capture = capture
        self._injected_transcriber = transcriber
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._asr_thread = None
        self._translate_thread = None
        self._translate_queue = queue.Queue(maxsize=200)
        self._model = None
        self._model_lock = None
        self._seq = 0

    # ── 对外 ──

    @property
    def running(self):
        return (self._asr_thread is not None
                and self._asr_thread.is_alive())

    def set_paused(self, paused):
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    @property
    def paused(self):
        return self._paused.is_set()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._paused.clear()
        self._seq = 0
        if self._capture is None:
            self._capture = AudioCapture(
                self.cfg.get('subtitle_audio_source', 'mic'),
                self.cfg.get('subtitle_audio_device', ''),
                on_error=self.cb.get('on_error'))
        self._capture.start()
        self._asr_thread = threading.Thread(
            target=self._asr_loop, name='caption-asr', daemon=True)
        self._asr_thread.start()
        if bool(self.cfg.get('subtitle_translate', True)):
            self._translate_thread = threading.Thread(
                target=self._translate_loop, name='caption-translate',
                daemon=True)
            self._translate_thread.start()

    def stop(self):
        self._stop.set()
        if self._capture is not None:
            try:
                self._capture.stop()
            except Exception:
                pass
        for t in (self._asr_thread, self._translate_thread):
            if t is not None:
                t.join(timeout=3.0)
        self._asr_thread = None
        self._translate_thread = None
        self._model = None
        self._model_lock = None

    # ── 回调 ──

    def _call(self, name, *args):
        fn = self.cb.get(name)
        if fn:
            try:
                fn(*args)
            except Exception:
                pass

    # ── 识别 ──

    def _language(self):
        lang = str(self.cfg.get('subtitle_language') or 'auto')
        if lang in ('auto', '', 'None'):
            return None
        from speech_recognizer import WHISPER_LANG_MAP
        return WHISPER_LANG_MAP.get(lang, lang)

    def _load_model(self):
        if self._injected_transcriber is not None:
            return None
        if str(self.cfg.get('subtitle_engine', 'local')) != 'local':
            return None
        import speech_recognizer as asr
        name = str(self.cfg.get('subtitle_model') or 'tiny')
        path, _ = asr.resolve_model_path(name)
        if not path:
            raise RuntimeError(
                f'模型 {name} 未就绪，请先在「音视频」页下载，或在设置里改用在线 API')
        device, compute, notice = asr._resolve_device(
            str(self.cfg.get('subtitle_device') or 'auto'))
        if notice:
            self._call('on_status', notice, '#eab308')
        self._call('on_status', f'正在加载 {name} 模型…', '#eab308')
        # 复用 speech_recognizer 的共享模型缓存：同一模型只实例化一次，
        # 频繁开关实时字幕不再反复重建（重建一次常 >1s 且占数百 MB 内存）。
        # 返回该模型的互斥锁，transcribe 时必须持锁，否则与音视频页共用同
        # 一实例时会因 CTranslate2 非线程安全而崩溃。
        try:
            model, lock = asr._get_whisper_model(path, device, compute)
        except Exception as e:
            # 与 transcribe_local 一致：GPU 初始化失败时回退 CPU 并重试
            if device == 'cuda':
                self._call('on_status',
                           f'GPU 初始化失败，已改用 CPU 识别（{str(e)[:60]}）',
                           '#eab308')
                device, compute = 'cpu', 'int8'
                model, lock = asr._get_whisper_model(path, device, compute)
            else:
                raise
        self._model = model
        self._model_lock = lock
        return self._model

    def _transcribe(self, audio):
        if self._injected_transcriber is not None:
            return self._injected_transcriber(audio)
        engine = str(self.cfg.get('subtitle_engine') or 'local')
        if engine == 'local':
            if self._model is None:
                return ''
            # CTranslate2 模型实例非线程安全：持该模型专属锁串行调用，
            # 即便与音视频页共享同一模型也不会并发崩溃
            with self._model_lock:
                segments, _info = self._model.transcribe(
                    audio,
                    language=self._language(),
                    beam_size=1,
                    temperature=0.0,
                    vad_filter=False,
                    condition_on_previous_text=False,
                    no_speech_threshold=0.6,
                )
            return clean_text(' '.join(s.text for s in segments))
        # 在线 API / 免费本地服务：短片段写临时 wav，用完即删
        import speech_recognizer as asr
        fd, path = tempfile.mkstemp(prefix='caption_', suffix='.wav')
        os.close(fd)
        try:
            write_wav(path, audio)
            result = asr.transcribe_api(
                path,
                str(self.cfg.get('subtitle_api_url') or '').strip(),
                str(self.cfg.get('subtitle_api_key') or '').strip(),
                str(self.cfg.get('subtitle_api_model') or 'whisper-1'),
                language=str(self.cfg.get('subtitle_language') or 'auto'),
                allow_private=(engine == 'local_service'))
            return clean_text(' '.join(
                str(s.get('text', '')) for s in result.get('segments', [])))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _asr_loop(self):
        """线程入口：逃逸异常必须回调 on_error，否则界面会一直卡在「运行中」。"""
        try:
            self._asr_loop_inner()
        except Exception as e:
            msg = f'实时字幕线程异常：{e}'
            self._call('on_error', msg)
            self._call('on_fatal', msg)

    def _asr_loop_inner(self):
        try:
            self._load_model()
        except Exception as e:
            msg = f'识别模型加载失败：{e}'
            self._call('on_error', msg)
            self._call('on_fatal', msg)
            return
        self._call('on_status', '正在聆听…', '#4ade80')

        try:
            window_ms = max(800, min(6000,
                                     int(self.cfg.get('subtitle_window_ms', 2500))))
            threshold = float(self.cfg.get('subtitle_silence_threshold', 0.008))
        except (TypeError, ValueError, OverflowError):
            window_ms, threshold = 2500, 0.008
        max_frames = int(SAMPLE_RATE * window_ms / 1000)
        buffer = np.zeros(0, dtype=np.float32)
        silence_frames = 0
        speech_seen = False
        last_text = ''
        chunks = self._capture.chunks

        while not self._stop.is_set():
            try:
                chunk = chunks.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._paused.is_set():
                buffer = np.zeros(0, dtype=np.float32)
                silence_frames = 0
                speech_seen = False
                continue

            speech = has_speech(chunk, threshold)
            if speech:
                speech_seen = True
                silence_frames = 0
            else:
                silence_frames += len(chunk)
            buffer = np.concatenate((buffer, chunk))

            # 长时间纯静音：只保留一点尾巴，避免内存与幻听
            if not speech_seen and len(buffer) > max_frames * 2:
                buffer = buffer[-_SILENCE_FRAMES:]
                continue

            flush = (len(buffer) >= max_frames
                     or (speech_seen and silence_frames >= _SILENCE_FRAMES))
            if not flush or len(buffer) < _MIN_SPEECH_FRAMES:
                continue

            audio = buffer
            buffer = np.zeros(0, dtype=np.float32)
            silence_frames = 0
            speech_seen = False
            try:
                import time
                t0 = time.perf_counter()
                text = self._transcribe(audio)
                cost = time.perf_counter() - t0
                self._call('on_speed', cost)
            except Exception as e:
                self._call('on_error', f'识别失败：{e}')
                continue
            text = trim_repeated_prefix(text, last_text)
            if not text or text == last_text:
                continue
            last_text = text
            self._seq += 1
            seq = self._seq
            self._call('on_segment', seq, text)
            if bool(self.cfg.get('subtitle_translate', True)):
                try:
                    self._translate_queue.put_nowait((seq, text))
                except queue.Full:
                    # 翻译跟不上识别速度时丢弃最旧未翻译项，避免队列无限堆积吃内存
                    try:
                        self._translate_queue.get_nowait()
                    except queue.Empty:
                        pass
                    self._translate_queue.put_nowait((seq, text))

    def _translate_loop(self):
        from translator import Translator
        try:
            translator = Translator(
                'auto', str(self.cfg.get('subtitle_target_lang') or 'zh-CN'))
        except Exception as e:
            self._call('on_error', f'翻译引擎初始化失败：{e}')
            return
        while not self._stop.is_set():
            try:
                seq, text = self._translate_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._stop.is_set():
                break
            try:
                translated = translator.translate(text) or text
            except Exception:
                translated = text       # 翻译失败保留原文，不丢字幕
            self._call('on_translation', seq, translated)
