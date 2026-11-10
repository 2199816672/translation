#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量转录纯逻辑回归测试（离线、零第三方依赖）。"""
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import batch_transcribe as bt


def _segs(n=2):
    return [{'start': i * 1000, 'end': (i + 1) * 1000,
             'text': f'line {i}'} for i in range(n)]


class PathPlanningTests(unittest.TestCase):

    def test_output_dir_defaults_to_source_dir(self):
        src = os.path.join('C:', os.sep, 'media', 'sub', 'a.mp3')
        self.assertEqual(bt.output_dir_for(src, None),
                         os.path.dirname(os.path.abspath(src)))

    def test_output_dir_override_is_absolute(self):
        src = os.path.join('C:', os.sep, 'media', 'a.mp3')
        self.assertEqual(bt.output_dir_for(src, 'out'),
                         os.path.abspath('out'))

    def test_output_filename_keeps_stem_and_format(self):
        self.assertEqual(bt.output_filename('C:/media/讲座 01.MP4', '.srt'),
                         '讲座 01_字幕.srt')
        self.assertEqual(bt.output_filename('a.mkv', 'VTT'), 'a_字幕.vtt')

    def test_is_supported(self):
        self.assertTrue(bt.is_supported('x.MP4'))
        self.assertTrue(bt.is_supported('x.mkv'))
        self.assertFalse(bt.is_supported('x.txt'))
        self.assertFalse(bt.is_supported(''))

    def test_classify_paths_dedup_and_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / 'a.mp3'
            good.write_bytes(b'x')
            txt = root / 'b.txt'
            txt.write_bytes(b'x')
            valid, rejected = bt.classify_paths(
                [str(good), str(good).upper(), str(txt),
                 str(root / 'missing.mp4')])
            self.assertEqual(len(valid), 1)
            self.assertEqual(valid[0], str(good))
            reasons = {r for _, r in rejected}
            self.assertEqual(reasons, {'格式不支持', '文件不存在'})

    def test_file_pattern_covers_all_exts(self):
        pat = bt.file_pattern()
        self.assertIn('*.mp4', pat)
        self.assertIn('*.mp3', pat)


class ExportModeTests(unittest.TestCase):

    def test_three_modes_exist(self):
        self.assertEqual(set(bt.EXPORT_MODES), {'source', 'target', 'both'})
        self.assertEqual(len(bt.EXPORT_MODES), 3)

    def test_only_translated_modes_need_translation(self):
        self.assertFalse(bt.needs_translation('source'))
        self.assertTrue(bt.needs_translation('target'))
        self.assertTrue(bt.needs_translation('both'))
        self.assertFalse(bt.needs_translation(None))


class RunnerTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = []
        for name in ('a.mp3', 'b.mp4', 'c.wav'):
            path = self.root / name
            path.write_bytes(b'fake-media')
            self.paths.append(str(path))
        self.calls = []
        self.settings = {
            'mode': 'local', 'model': 'small', 'src_lang': 'auto',
            'target_lang': 'zh-CN', 'fmt': 'srt', 'export_mode': 'source',
            'translate': False, 'out_dir': None, 'asr_opts': {},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _transcribe(self, path, settings, progress_cb, cancel):
        self.calls.append(os.path.basename(path))
        progress_cb(50, '识别中')
        return {'segments': _segs()}

    def _export(self, segments, translations, settings, source):
        return str(Path(bt.output_dir_for(source, settings.get('out_dir')))
                   / bt.output_filename(source, settings.get('fmt', 'srt')))

    def test_order_and_all_done(self):
        runner = bt.BatchRunner(self.paths, self.settings,
                                transcribe=self._transcribe,
                                export=self._export)
        summary = runner.run()
        self.assertEqual(self.calls, ['a.mp3', 'b.mp4', 'c.wav'])
        self.assertEqual((summary['done'], summary['failed']), (3, 0))
        self.assertEqual(len(summary['outputs']), 3)
        self.assertTrue(all(j.status == bt.DONE for j in runner.jobs))

    def test_single_failure_does_not_stop_queue(self):
        def transcribe(path, settings, progress_cb, cancel):
            self.calls.append(os.path.basename(path))
            if path.endswith('b.mp4'):
                raise RuntimeError('解码失败')
            return {'segments': _segs()}

        runner = bt.BatchRunner(self.paths, self.settings,
                                transcribe=transcribe, export=self._export)
        summary = runner.run()
        self.assertEqual(self.calls, ['a.mp3', 'b.mp4', 'c.wav'])
        self.assertEqual((summary['done'], summary['failed']), (2, 1))
        self.assertIn('解码失败', runner.jobs[1].detail)

    def test_no_speech_is_failed_not_written(self):
        runner = bt.BatchRunner(
            [self.paths[0]], self.settings,
            transcribe=lambda *a: {'segments': []}, export=self._export)
        summary = runner.run()
        self.assertEqual(summary['failed'], 1)
        self.assertIn('未识别到语音内容', runner.jobs[0].detail)

    def test_cancel_skips_remaining(self):
        cancel = threading.Event()

        def transcribe(path, settings, progress_cb, cancel_event):
            self.calls.append(os.path.basename(path))
            if cancel_event.is_set():
                raise bt.Cancelled()
            return {'segments': _segs()}

        runner = bt.BatchRunner(self.paths, self.settings, cancel=cancel,
                                transcribe=transcribe, export=self._export)

        def on_start(index, total, job):
            if index == 2:
                cancel.set()

        summary = runner.run(on_start=on_start)
        self.assertEqual(self.calls, ['a.mp3', 'b.mp4'])
        self.assertEqual(summary['done'], 1)
        self.assertEqual(summary['skipped'], 2)

    def test_translate_only_when_needed(self):
        translated = []

        def translate(texts, settings, progress_cb, cancel):
            translated.append(list(texts))
            progress_cb('翻译中', 100)
            return ['译文'] * len(texts), 0

        settings = dict(self.settings, translate=True, export_mode='both')
        runner = bt.BatchRunner([self.paths[0]], settings,
                                transcribe=self._transcribe,
                                translate=translate, export=self._export)
        runner.run()
        self.assertEqual(len(translated), 1)
        self.assertEqual(translated[0], ['line 0', 'line 1'])

        translated.clear()
        runner = bt.BatchRunner([self.paths[0]], self.settings,
                                transcribe=self._transcribe,
                                translate=translate, export=self._export)
        runner.run()
        self.assertEqual(translated, [])

    def test_translate_failure_falls_back_and_still_exports(self):
        def translate(texts, settings, progress_cb, cancel):
            return list(texts), len(texts)

        modes = []

        def export(segments, translations, settings, source):
            modes.append(settings['export_mode'])
            return self._export(segments, translations, settings, source)

        settings = dict(self.settings, translate=True, export_mode='target')
        runner = bt.BatchRunner([self.paths[0]], settings,
                                transcribe=self._transcribe,
                                translate=translate, export=export)
        summary = runner.run()
        self.assertEqual(summary['done'], 1)
        self.assertIn('翻译全部失败', runner.jobs[0].detail)
        # 全失败时不产出「原文 / 原文」的重复双语，自动退回只导出原文
        self.assertEqual(modes, ['source'])

    def test_partial_translate_failure_keeps_export_mode(self):
        def translate(texts, settings, progress_cb, cancel):
            return list(texts), 1

        modes = []

        def export(segments, translations, settings, source):
            modes.append(settings['export_mode'])
            return self._export(segments, translations, settings, source)

        settings = dict(self.settings, translate=True, export_mode='both')
        runner = bt.BatchRunner([self.paths[0]], settings,
                                transcribe=self._transcribe,
                                translate=translate, export=export)
        runner.run()
        self.assertEqual(modes, ['both'])
        self.assertIn('翻译 1/2 条失败', runner.jobs[0].detail)

    def test_three_export_modes_are_all_honored(self):
        """勾选翻译也不改用户选的模式：原文 / 译文 / 原文+译文 各自生效。"""
        for mode, should_translate in (('source', False),
                                       ('target', True),
                                       ('both', True)):
            with self.subTest(mode=mode):
                translated, exported = [], []

                def translate(texts, settings, progress_cb, cancel):
                    translated.append(list(texts))
                    return [f'译文{i}' for i in range(len(texts))], 0

                def export(segments, translations, settings, source):
                    exported.append((settings['export_mode'],
                                     list(translations)))
                    return self._export(segments, translations,
                                        settings, source)

                settings = dict(self.settings, translate=True,
                                export_mode=mode)
                runner = bt.BatchRunner([self.paths[0]], settings,
                                        transcribe=self._transcribe,
                                        translate=translate, export=export)
                self.assertEqual(runner.run()['done'], 1)
                self.assertEqual(bool(translated), should_translate)
                self.assertEqual(exported[0][0], mode)
                self.assertEqual(len(exported[0][1]),
                                 2 if should_translate else 0)
                self.assertIn('条字幕', runner.jobs[0].detail)

    def test_target_without_translation_flag_falls_back(self):
        """没开翻译却选了译文模式：退回原文，绝不把原文冒充译文。"""
        for mode in ('target', 'both'):
            with self.subTest(mode=mode):
                translated, modes = [], []

                def translate(*args):
                    translated.append(True)
                    return ['译文'] * 2, 0

                def export(segments, translations, settings, source):
                    modes.append(settings['export_mode'])
                    return self._export(segments, translations,
                                        settings, source)

                settings = dict(self.settings, translate=False,
                                export_mode=mode)
                runner = bt.BatchRunner([self.paths[0]], settings,
                                        transcribe=self._transcribe,
                                        translate=translate, export=export)
                self.assertEqual(runner.run()['done'], 1)
                self.assertEqual(translated, [])
                self.assertEqual(modes, ['source'])
                self.assertIn('未开启翻译', runner.jobs[0].detail)

    def test_both_all_translate_failed_falls_back(self):
        def translate(texts, settings, progress_cb, cancel):
            return list(texts), len(texts)

        modes = []

        def export(segments, translations, settings, source):
            modes.append(settings['export_mode'])
            return self._export(segments, translations, settings, source)

        settings = dict(self.settings, translate=True, export_mode='both')
        runner = bt.BatchRunner([self.paths[0]], settings,
                                transcribe=self._transcribe,
                                translate=translate, export=export)
        self.assertEqual(runner.run()['done'], 1)
        self.assertEqual(modes, ['source'])
        self.assertIn('翻译全部失败', runner.jobs[0].detail)

    def test_translator_init_failure_still_exports_source(self):
        """翻译引擎整体不可用时不丢掉已识别的字幕。"""
        def translate(*args):
            raise RuntimeError('翻译引擎未配置')

        modes = []

        def export(segments, translations, settings, source):
            modes.append(settings['export_mode'])
            return self._export(segments, translations, settings, source)

        settings = dict(self.settings, translate=True, export_mode='both')
        runner = bt.BatchRunner([self.paths[0]], settings,
                                transcribe=self._transcribe,
                                translate=translate, export=export)
        summary = runner.run()
        self.assertEqual(summary['done'], 1)
        self.assertEqual(modes, ['source'])
        self.assertIn('翻译全部失败', runner.jobs[0].detail)
        self.assertIn('翻译引擎未配置', runner.jobs[0].detail)

    def test_callbacks_report_progress(self):
        events = []
        runner = bt.BatchRunner([self.paths[0]], self.settings,
                                transcribe=self._transcribe,
                                export=self._export)
        runner.run(on_start=lambda i, n, j: events.append(('start', i, n)),
                   on_progress=lambda i, n, j, p, t:
                       events.append(('progress', p, t)),
                   on_finish=lambda i, n, j: events.append(('finish', i, n)))
        self.assertIn(('start', 1, 1), events)
        self.assertIn(('progress', 50, '识别中'), events)
        self.assertIn(('finish', 1, 1), events)

    def test_unexpected_export_failure_is_recorded(self):
        def export(*args):
            raise OSError('磁盘已满')

        runner = bt.BatchRunner(self.paths, self.settings,
                                transcribe=self._transcribe, export=export)
        summary = runner.run()
        self.assertEqual(summary['failed'], 3)
        self.assertTrue(all('磁盘已满' in j.detail for j in runner.jobs))


class TranslationSafetyTests(unittest.TestCase):
    """真实翻译路径绝不产出「原文冒充译文 / 原文-原文」字幕。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = []
        for name in ('a.mp3', 'b.mp4'):
            p = self.root / name
            p.write_bytes(b'fake-media')
            self.paths.append(str(p))
        self.settings = {
            'mode': 'local', 'model': 'small', 'src_lang': 'auto',
            'target_lang': 'zh-CN', 'fmt': 'srt', 'export_mode': 'source',
            'translate': False, 'out_dir': None, 'asr_opts': {},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _transcribe(self, path, settings, progress_cb, cancel):
        progress_cb(50, '识别中')
        return {'segments': _segs()}

    def test_empty_or_failed_translation_is_blank(self):
        fake = types.ModuleType('translator')

        class FakeTranslator:
            def __init__(self, *args):
                pass

            def translate(self, text):
                if text == '原文A':
                    return '译文A'
                if text == '原文B':
                    return ''
                raise RuntimeError('限流')

        fake.Translator = FakeTranslator
        with mock.patch.dict(sys.modules, {'translator': fake}):
            results, failed = bt._default_translate(
                ['原文A', '原文B', '原文C'],
                {'src_lang': 'auto', 'target_lang': 'zh-CN'}, None, None)
        self.assertEqual(results, ['译文A', '', ''])
        self.assertEqual(failed, 2)

    def test_whitespace_only_translation_counts_as_failure(self):
        fake = types.ModuleType('translator')

        class FakeTranslator:
            def __init__(self, *args):
                pass

            def translate(self, text):
                return '   '

        fake.Translator = FakeTranslator
        with mock.patch.dict(sys.modules, {'translator': fake}):
            results, failed = bt._default_translate(['原文'], {}, None, None)
        self.assertEqual(results, [''])
        self.assertEqual(failed, 1)

    def test_target_partial_failure_falls_back_to_source(self):
        def translate(texts, settings, progress_cb, cancel):
            return ['译文0', ''], 1

        modes = []

        def export(segments, translations, settings, source):
            modes.append(settings['export_mode'])
            return str(self.root / 'out.srt')

        settings = dict(self.settings, translate=True, export_mode='target')
        runner = bt.BatchRunner([self.paths[0]], settings,
                                transcribe=self._transcribe,
                                translate=translate, export=export)
        self.assertEqual(runner.run()['done'], 1)
        self.assertEqual(modes, ['source'])
        self.assertIn('已改为只导出原文', runner.jobs[0].detail)

    def test_both_partial_failure_never_duplicates_source(self):
        def translate(texts, settings, progress_cb, cancel):
            return ['译文0', ''], 1

        settings = dict(self.settings, translate=True,
                        export_mode='both', fmt='srt')
        runner = bt.BatchRunner([self.paths[0]], settings,
                                transcribe=self._transcribe,
                                translate=translate)
        text = Path(runner.run()['outputs'][0]).read_text(
            encoding='utf-8').replace('\r\n', '\n')
        self.assertIn('line 0\n译文0', text)
        self.assertIn('line 1', text)
        self.assertNotIn('line 1\nline 1', text)

    def test_resume_skips_completed_and_keeps_outputs(self):
        jobs = [
            bt.Job(self.paths[0], status=bt.DONE,
                   output=str(self.root / 'a_字幕.srt')),
            bt.Job(self.paths[1], status=bt.FAILED, detail='磁盘满'),
        ]
        calls, rows = [], []

        def transcribe(path, settings, progress_cb, cancel):
            calls.append(os.path.basename(path))
            return {'segments': _segs()}

        def export(segments, translations, settings, source):
            return str(self.root / (os.path.basename(source) + '.srt'))

        runner = bt.BatchRunner(jobs, self.settings, resume=True,
                                transcribe=transcribe, export=export)
        summary = runner.run(on_finish=lambda i, n, j: rows.append(i))
        self.assertEqual(calls, ['b.mp4'])
        self.assertEqual(summary['done'], 2)
        self.assertEqual(len(summary['outputs']), 2)
        self.assertIn(str(self.root / 'a_字幕.srt'), summary['outputs'])
        self.assertEqual(rows, [1, 2])


class RealExportTests(unittest.TestCase):
    """用真实的字幕序列化 + 导出，验证输出目录与防覆盖。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / 'source' / '讲座.mp4'
        self.src.parent.mkdir(parents=True, exist_ok=True)
        self.src.write_bytes(b'fake-media')

    def tearDown(self):
        self.tmp.cleanup()

    def _runner(self, settings):
        return bt.BatchRunner(
            [str(self.src)], settings,
            transcribe=lambda *a: {'segments': _segs()})

    def test_default_output_beside_source(self):
        settings = {'fmt': 'srt', 'export_mode': 'source',
                    'out_dir': None, 'translate': False}
        summary = self._runner(settings).run()
        self.assertEqual(summary['done'], 1)
        out = Path(summary['outputs'][0])
        self.assertEqual(out.parent, self.src.parent)
        self.assertEqual(out.name, '讲座_字幕.srt')
        self.assertIn('line 0', out.read_text(encoding='utf-8'))

    def test_custom_output_dir(self):
        target = self.root / 'out'
        target.mkdir()
        settings = {'fmt': 'vtt', 'export_mode': 'source',
                    'out_dir': str(target), 'translate': False}
        summary = self._runner(settings).run()
        self.assertEqual(Path(summary['outputs'][0]).parent.resolve(),
                         target.resolve())

    def test_three_modes_produce_different_subtitles(self):
        """真实导出：原文 / 译文 / 双语字幕内容互不相同。"""
        def translate(texts, settings, progress_cb, cancel):
            return ['译文零', '译文一'], 0

        cases = (
            ('source', ['line 0', 'line 1'], ['译文零']),
            ('target', ['译文零', '译文一'], ['line 0']),
            ('both', ['line 0', '译文零'], []),
        )
        for mode, present, absent in cases:
            with self.subTest(mode=mode):
                out_dir = self.root / ('out_' + mode)
                out_dir.mkdir()
                settings = {'fmt': 'srt', 'export_mode': mode,
                            'out_dir': str(out_dir), 'translate': True}
                runner = bt.BatchRunner(
                    [str(self.src)], settings,
                    transcribe=lambda *a: {'segments': _segs()},
                    translate=translate)
                summary = runner.run()
                text = Path(summary['outputs'][0]).read_text(encoding='utf-8')
                for piece in present:
                    self.assertIn(piece, text)
                for piece in absent:
                    self.assertNotIn(piece, text)

    def test_existing_file_is_not_overwritten(self):
        settings = {'fmt': 'srt', 'export_mode': 'source',
                    'out_dir': None, 'translate': False}
        first = Path(self._runner(settings).run()['outputs'][0])
        before = first.read_bytes()
        second = Path(self._runner(settings).run()['outputs'][0])
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), before)
        self.assertTrue(second.is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
