#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量音视频转录的纯逻辑层：路径规划、队列执行与结果汇总。

不依赖 Qt，耗时操作通过可注入的函数完成，因此可以离线单元测试。
UI 层见 batch_dialog.py。
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

from speech_recognizer import AUDIO_VIDEO_EXTS

PENDING = 'pending'
RUNNING = 'running'
DONE = 'done'
FAILED = 'failed'
SKIPPED = 'skipped'

STATUS_TEXT = {
    PENDING: '等待中',
    RUNNING: '识别中',
    DONE: '已完成',
    FAILED: '失败',
    SKIPPED: '已跳过',
}

# 三种导出内容始终由用户选择：source 完全不碰翻译接口，
# target / both 才需要译文。勾选翻译不会把模式改成双语。
EXPORT_MODES = ('source', 'target', 'both')
TRANSLATED_MODES = ('target', 'both')


def needs_translation(mode):
    """该导出内容是否需要译文。"""
    return str(mode) in TRANSLATED_MODES


def can_write_dir(folder):
    """Windows 下 os.access(W_OK) 不可靠，实际写一个探针文件才准确。"""
    try:
        fd, probe = tempfile.mkstemp(prefix='.tt_write_', dir=str(folder))
        os.close(fd)
        os.unlink(probe)
        return True
    except OSError:
        return False


class Cancelled(Exception):
    """用户主动停止批量任务。"""


def is_supported(path):
    """是否为受支持的音视频文件。"""
    return os.path.splitext(str(path or ''))[1].lower() in AUDIO_VIDEO_EXTS


def file_pattern():
    """文件对话框用的通配符串。"""
    return ' '.join('*' + ext for ext in sorted(AUDIO_VIDEO_EXTS))


def classify_paths(paths):
    """把用户选择/拖入的路径分成 (可处理, 被拒绝及原因)，并保持顺序去重。"""
    valid, rejected, seen = [], [], set()
    for raw in paths or []:
        path = os.path.abspath(str(raw))
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        if not is_supported(path):
            rejected.append((path, '格式不支持'))
        elif not os.path.isfile(path):
            rejected.append((path, '文件不存在'))
        else:
            valid.append(path)
    return valid, rejected


def output_dir_for(source_path, override=None):
    """未指定输出文件夹时，输出到源文件所在目录。"""
    if override:
        return os.path.abspath(str(override))
    return os.path.dirname(os.path.abspath(str(source_path)))


def output_filename(source_path, fmt):
    """每个源文件对应一份独立字幕，命名与单文件导出保持一致。"""
    stem = os.path.splitext(os.path.basename(str(source_path)))[0]
    return f"{stem}_字幕.{str(fmt).lower().lstrip('.')}"


@dataclass
class Job:
    path: str
    status: str = PENDING
    detail: str = ''
    output: str = ''
    lines: int = 0

    @property
    def name(self):
        return os.path.basename(self.path)


def summarize(jobs):
    """统计结果，并给出实际写出的文件路径列表。"""
    counts = {DONE: 0, FAILED: 0, SKIPPED: 0, PENDING: 0, RUNNING: 0}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
    return {
        'total': len(jobs),
        'done': counts[DONE],
        'failed': counts[FAILED],
        'skipped': counts[SKIPPED],
        'outputs': [j.output for j in jobs if j.status == DONE and j.output],
    }


def _default_transcribe(path, settings, progress_cb, cancel):
    import speech_recognizer as asr

    def guarded(percent, text):
        # API 转录没有 cancel_event，靠进度回调在请求间隙也能及时停下
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if progress_cb:
            progress_cb(percent, text)

    if settings.get('mode') == 'api':
        try:
            return asr.transcribe_api(
                path, settings.get('api_base', ''), settings.get('api_key', ''),
                settings.get('api_model', ''),
                language=settings.get('src_lang', 'auto'),
                progress_cb=guarded, cancel_event=cancel)
        except Exception as exc:
            # 停止时底层可能抛 Cancelled / 网络中断 / socket 被关闭，
            # 统一归一成「用户已停止」，不要记成「识别失败」。
            if cancel is not None and cancel.is_set():
                raise Cancelled() from exc
            raise

    opts = settings.get('asr_opts') or {}
    return asr.transcribe_local(
        path, settings.get('model'), language=settings.get('src_lang', 'auto'),
        progress_cb=guarded, cancel_event=cancel,
        vad_filter=bool(opts.get('asr_vad_enabled', True)),
        vad_threshold=float(opts.get('asr_vad_threshold', 0.5)),
        beam_size=int(opts.get('asr_beam_size', 5)),
        segment_callback=None,
        device_preference=str(opts.get('asr_device', 'auto')))


def _default_translate(texts, settings, progress_cb, cancel):
    """用独立的翻译器实例，避免与主窗口正在使用的单例互相改语言。

    失败条目一律留空串并计数：绝不能把原文塞进译文列表，否则
    「只导出译文」会变成原文冒充译文、「原文 + 译文」会变成重复双语。
    返回 (results, failed, last_error)：last_error 保留首个真实异常，
    便于「全部失败」时向用户展示根因（密钥错误 / 网络超时等）。
    """
    from cancelable import CallCancelled, run_cancelable
    from translator import Translator

    translator = Translator(settings.get('src_lang', 'auto'),
                            settings.get('target_lang', 'zh-CN'))
    results, failed = [], 0
    total = len(texts)
    last_error = ''
    for i, text in enumerate(texts, 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        translated = ''
        try:
            # 单次翻译请求最长 15 秒；停止时不必干等它返回，
            # 取消的请求留在后台自生自灭，绝不能塞进译文列表。
            value = run_cancelable(lambda t=text: translator.translate(t),
                                   cancel, poll=0.1)
            if value is not None and str(value).strip():
                translated = str(value)
        except CallCancelled:
            raise Cancelled()
        except Exception as e:
            # 保留首个真实错误，便于全部失败时展示根因，而非只报「X 条失败」
            if not last_error:
                last_error = str(e)[:120]
        if translated:
            results.append(translated)
        else:
            failed += 1
            results.append('')
        if progress_cb and (i % 5 == 0 or i == total):
            progress_cb(f'正在翻译字幕 {i}/{total}', int(i * 100 / total))
    return results, failed, last_error


def _default_export(segments, translations, settings, source_path):
    from format_convert import export_text
    from subtitle import compose_entries

    fmt = str(settings.get('fmt', 'srt')).lower().lstrip('.')
    composed = compose_entries(segments, translations,
                               settings.get('export_mode', 'source'),
                               '.' + fmt)
    stem = os.path.splitext(os.path.basename(source_path))[0]
    out_dir = output_dir_for(source_path, settings.get('out_dir'))
    return export_text('', composed, fmt, out_dir, stem,
                       filename=output_filename(source_path, fmt))


class BatchRunner:
    """依次处理队列；单个文件失败不会中断，取消会跳过剩余项。

    resume=True 时跳过已完成项（重试失败项场景），保留它们的输出记录。
    only_rows 传入 0 基行号集合时只处理这些行（表格右键「重试此项」），
    其余行保持原状态，只推进界面总进度。
    """

    def __init__(self, jobs, settings, cancel=None,
                 transcribe=None, translate=None, export=None,
                 resume=False, only_rows=None):
        self.jobs = [j if isinstance(j, Job) else Job(str(j)) for j in jobs]
        self.settings = dict(settings)
        self.cancel = cancel
        self.resume = bool(resume)
        self.only_rows = (set(only_rows) if only_rows is not None else None)
        self.transcribe = transcribe or _default_transcribe
        self.translate = translate or _default_translate
        self.export = export or _default_export

    def _stopped(self):
        return self.cancel is not None and self.cancel.is_set()

    def run(self, on_start=None, on_progress=None, on_finish=None):
        total = len(self.jobs)
        for index, job in enumerate(self.jobs, 1):
            if self.only_rows is not None and (index - 1) not in self.only_rows:
                # 「重试此项」：其它行保持原状态与输出，只推进界面总进度
                if on_finish:
                    on_finish(index, total, job)
                continue
            if self.resume and job.status == DONE:
                # 已完成项保持原状态与输出路径，只推进 UI 总进度
                if on_finish:
                    on_finish(index, total, job)
                continue
            if self._stopped():
                job.status = SKIPPED
                job.detail = '已停止'
                if on_finish:
                    on_finish(index, total, job)
                continue

            job.status = RUNNING
            job.detail = ''
            job.output = ''
            if on_start:
                on_start(index, total, job)

            def progress(text, percent):
                if on_progress:
                    on_progress(index, total, job, percent, text)

            try:
                result = self.transcribe(job.path, self.settings,
                                         lambda p, t: progress(t, p),
                                         self.cancel) or {}
                segments = result.get('segments') or []
                if not segments:
                    raise RuntimeError('未识别到语音内容')
                if self._stopped():
                    raise Cancelled()

                mode = str(self.settings.get('export_mode', 'source'))
                want_translation = needs_translation(mode)
                translate_on = bool(self.settings.get('translate')) and want_translation
                translations, failed_trans = [], 0
                translate_error = ''
                if translate_on:
                    texts = [str(s.get('text', '')) for s in segments]
                    try:
                        out = self.translate(
                            texts, self.settings,
                            lambda t, p: progress(t, p), self.cancel)
                        if not isinstance(out, (tuple, list)) or len(out) not in (2, 3):
                            raise TypeError(
                                '翻译回调应返回 (译文列表, 失败数) 或 '
                                '(译文列表, 失败数, 错误原因)')
                        translations, failed_trans = out[0], out[1]
                        if len(out) == 3:
                            translate_error = str(out[2] or '')[:120]
                    except Cancelled:
                        raise
                    except Exception as e:
                        # 翻译引擎未配置 / 整体不可用时，仍保住已识别的字幕
                        translations, failed_trans = [], len(segments)
                        translate_error = str(e)[:80]

                has_translation = (translate_on
                                   and failed_trans < len(segments)
                                   and any(str(t or '').strip()
                                           for t in translations))
                # 没有可用译文，或「只导出译文」里混进了失败条目（会变成
                # 原文冒充译文），都退回只导出原文并在状态里写明原因。
                export_settings = self.settings
                fallback_reason = ''
                if want_translation and (not has_translation
                                         or (failed_trans and mode == 'target')):
                    export_settings = dict(self.settings)
                    export_settings['export_mode'] = 'source'
                    if not has_translation:
                        fallback_reason = ('翻译全部失败' if translate_on
                                           else '未开启翻译')
                    else:
                        fallback_reason = (f'翻译 {failed_trans}/'
                                           f'{len(segments)} 条失败')
                out = self.export(segments, translations,
                                  export_settings, job.path)
                job.status = DONE
                job.output = str(out)
                job.lines = len(segments)
                if fallback_reason:
                    job.detail = f'{fallback_reason}，已改为只导出原文'
                    if translate_error:
                        job.detail += f'（{translate_error}）'
                elif failed_trans:
                    job.detail = (f'翻译 {failed_trans}/{len(segments)} 条失败，'
                                  '失败条目只保留原文')
                    if translate_error:
                        job.detail += f'（{translate_error}）'
                else:
                    job.detail = f'{len(segments)} 条字幕'
            except Cancelled:
                job.status = SKIPPED
                job.detail = '已停止'
                if self.cancel is not None:
                    self.cancel.set()
            except Exception as e:
                job.status = FAILED
                job.detail = str(e)[:160] or type(e).__name__
            finally:
                if on_finish:
                    on_finish(index, total, job)

        return summarize(self.jobs)
