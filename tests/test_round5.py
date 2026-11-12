#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""二次体检 / 深度优化阶段的回归测试（离线、零第三方依赖、无需 PySide6）。

覆盖：xlsx 空行语义、OCR 单例语言与引擎缓存、批量翻译错误根因透传、
文本编码兜底、AI 润色批量对齐、Whisper 模型缓存释放。
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_polish
import batch_transcribe as bt
import convert_libs
import doc_parser
import ocr_recognizer
import speech_recognizer
import subtitle


class XlsxBlankLineTests(unittest.TestCase):
    """xlsx 转文本只在每个工作表后留一个空行，不能逐行插空行。"""

    def _fake_workbook(self):
        class _WS:
            def __init__(self, rows):
                self._rows = rows

            def iter_rows(self, values_only=True):
                return iter(self._rows)

        class _WB:
            sheetnames = ['Sheet1', 'Sheet2']
            closed = False

            def __getitem__(self, name):
                return _WS([('A1', 'B1'), ('A2', None)]) if name == 'Sheet1' \
                    else _WS([('C1',)])

            def close(self):
                self.closed = True

        return _WB()

    def test_blank_line_only_between_sheets(self):
        wb = self._fake_workbook()
        fake = types.ModuleType('openpyxl')
        fake.load_workbook = lambda *a, **kw: wb
        with mock.patch.dict(sys.modules, {'openpyxl': fake}):
            text = doc_parser.parse_file('dummy.xlsx')
        self.assertIn('[Sheet1]', text)
        self.assertIn('A1 | B1', text)
        self.assertIn('[Sheet2]', text)
        # 行与行之间不能插空行；只有工作表之间有一个空行（末尾空行
        # 收尾，不会在字符串里形成第二个 \n\n）
        self.assertIn('A1 | B1\nA2\n\n[Sheet2]', text)
        self.assertEqual(text.count('\n\n'), 1)
        self.assertTrue(wb.closed, '异常/正常路径都必须关闭 workbook')

    def test_workbook_closed_even_when_rows_raise(self):
        class _BadWB:
            sheetnames = ['S']

            def __getitem__(self, name):
                raise RuntimeError('corrupt sheet')

            def close(self):
                self.closed = True

        wb = _BadWB()
        fake = types.ModuleType('openpyxl')
        fake.load_workbook = lambda *a, **kw: wb
        with mock.patch.dict(sys.modules, {'openpyxl': fake}):
            with self.assertRaises(Exception):
                doc_parser.parse_file('bad.xlsx')
        self.assertTrue(getattr(wb, 'closed', False),
                        '解析异常时也必须释放 read_only 句柄')


class OcrSingletonTests(unittest.TestCase):
    """单例必须同时按引擎与语言缓存，且避免配置/fallback 不一致导致重建。"""

    def setUp(self):
        ocr_recognizer.reset_ocr_instance()

    def tearDown(self):
        ocr_recognizer.reset_ocr_instance()

    def test_different_languages_rebuild_instance(self):
        with mock.patch.object(ocr_recognizer, 'WindowsOCRRecognizer',
                               side_effect=lambda langs: object()) as ctor, \
             mock.patch.object(ocr_recognizer, 'check_engine_available',
                               return_value=True):
            a = ocr_recognizer.get_ocr_instance(['ch_sim', 'en'], engine='windows')
            b = ocr_recognizer.get_ocr_instance(['ch_sim', 'en'], engine='windows')
            self.assertIs(a, b, '相同引擎+语言必须复用')
            c = ocr_recognizer.get_ocr_instance(['ja', 'en'], engine='windows')
            self.assertIsNot(a, c, '语言变了必须重建，否则用户语言设置不生效')
            self.assertEqual(ctor.call_count, 2)

    def test_unspecified_engine_reuses_resolved_instance(self):
        """主窗口已用 fallback 引擎建好实例后，文档图片解析不得再读配置重建。"""
        calls = []

        def _fake_ctor(langs):
            calls.append(list(langs))
            return object()

        with mock.patch.object(ocr_recognizer, 'WindowsOCRRecognizer',
                               side_effect=_fake_ctor):
            # 模拟主窗口：配置里写的是 easy，但 easy 未安装 -> fallback windows
            cfg = {'ocr_engine': 'easy'}
            with mock.patch.object(ocr_recognizer, 'check_engine_available',
                                   side_effect=lambda e: e == 'windows'):
                doc_parser_first = None
                # 主窗口显式传入 fallback 后的引擎
                main = ocr_recognizer.get_ocr_instance(
                    ['ch_sim', 'en'], engine='windows')
                # 文档解析路径不传 engine，配置仍写着不可用的 easy
                with mock.patch('config.load_user_config', return_value=cfg):
                    doc_parser_first = ocr_recognizer.get_ocr_instance()
                self.assertIs(main, doc_parser_first,
                              '未指定引擎时应复用已解析好的单例，避免反复加载模型')
                self.assertEqual(len(calls), 1)

    def test_reset_clears_language_cache(self):
        with mock.patch.object(ocr_recognizer, 'WindowsOCRRecognizer',
                               side_effect=lambda langs: object()) as ctor:
            a = ocr_recognizer.get_ocr_instance(['ja'], engine='windows')
            ocr_recognizer.reset_ocr_instance()
            b = ocr_recognizer.get_ocr_instance(['ja'], engine='windows')
            self.assertIsNot(a, b)
            self.assertEqual(ctor.call_count, 2)


class BatchTranslateErrorTests(unittest.TestCase):
    """批量翻译全部失败时必须把真实根因透传到任务详情。"""

    def test_last_error_is_first_real_exception(self):
        fake = types.ModuleType('translator')

        class FakeTranslator:
            def __init__(self, *a, **kw):
                pass

            def translate(self, text):
                raise RuntimeError('API key invalid 401')

        fake.Translator = FakeTranslator
        with mock.patch.dict(sys.modules, {'translator': fake}):
            results, failed, err = bt._default_translate(
                ['a', 'b'], {}, None, None)
        self.assertEqual(results, ['', ''])
        self.assertEqual(failed, 2)
        self.assertIn('401', err)

    def _runner(self, translate_result):
        return bt.BatchRunner(
            ['a.mp3'], {'translate': True, 'export_mode': 'both'},
            transcribe=lambda *a, **kw: {
                'segments': [{'start': 0, 'end': 1, 'text': 'hi'}]},
            translate=lambda *a, **kw: translate_result,
            export=lambda *a, **kw: 'out.srt')

    def test_runner_accepts_two_and_three_tuple(self):
        for out in ((['译A'], 0), (['译A'], 0, '')):
            with self.subTest(out=out):
                runner = self._runner(out)
                summary = runner.run()
                self.assertEqual(summary[bt.DONE], 1)
                self.assertEqual(runner.jobs[0].status, bt.DONE)

    def test_runner_rejects_malformed_translate_result(self):
        """自定义 translate 返回非法结构时给清晰错误，而非 ValueError 解包崩溃。

        注意构造器会把 None 当作「使用默认实现」，所以这里传一个确实会
        返回非法结构的可调用对象。
        """
        runner = self._runner('not-a-tuple')
        runner.run()
        job = runner.jobs[0]
        # 设计上要保住已识别字幕：退回只导出原文，但把根因写进详情
        self.assertEqual(job.status, bt.DONE)
        self.assertIn('只导出原文', job.detail)
        self.assertIn('译文列表', job.detail)

    def test_all_failed_translation_shows_root_cause(self):
        """整批翻译失败时详情里必须带真实根因，而不是只有「X 条失败」。"""
        runner = self._runner(([''], 1, 'API key invalid 401'))
        runner.run()
        self.assertIn('401', runner.jobs[0].detail)


class EncodingFallbackTests(unittest.TestCase):
    """非常规编码不能被 latin-1 静默解成乱码；纯 ASCII 仍可读取。"""

    def _write(self, d, name, data):
        p = os.path.join(d, name)
        with open(p, 'wb') as f:
            f.write(data)
        return p

    def test_ascii_only_file_readable(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, 'a.txt', b'Hello 123\n')
            self.assertEqual(convert_libs.read_text(p).strip(), 'Hello 123')
            self.assertEqual(subtitle._read_text(p).strip(), 'Hello 123')

    def test_non_ascii_unknown_encoding_raises(self):
        # 0xFF 开头既不是合法 UTF-8，也不是 GBK，latin-1 能解但必然是乱码
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, 'bad.txt', b'\xff\xfe\x80\x81\x82 hello')
            for fn in (convert_libs.read_text, subtitle._read_text):
                with self.subTest(fn=fn.__name__):
                    with self.assertRaises(RuntimeError):
                        fn(p)

    def test_utf8_bom_and_gbk_still_work(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, 'bom.txt', '中文内容'.encode('utf-8-sig'))
            self.assertIn('中文内容', convert_libs.read_text(p))
            q = self._write(d, 'gbk.srt', '中文内容'.encode('gbk'))
            self.assertIn('中文内容', subtitle._read_text(q))


class AiPolishAlignmentTests(unittest.TestCase):
    """批处理无编号时不得按位置 zip（模型重排会静默错配）。"""

    def test_numbered_output_keeps_order(self):
        raw = '2. 第二句\n1. 第一句'
        out = ai_polish._parse_batch(raw, ['原文一', '原文二'])
        self.assertEqual(out, ['第一句', '第二句'])

    def test_unnumbered_output_returns_none(self):
        out = ai_polish._parse_batch('乙句\n甲句', ['原文一', '原文二'])
        self.assertIsNone(out, '无编号必须返回 None 让调用方逐条回退')

    def test_partial_numbering_returns_none(self):
        out = ai_polish._parse_batch('1. 只有一条', ['原文一', '原文二'])
        self.assertIsNone(out)

    def test_chunk_falls_back_to_single_when_unaligned(self):
        with mock.patch.object(ai_polish, '_request', return_value='乙\n甲'), \
             mock.patch.object(ai_polish, '_polish_single',
                               side_effect=lambda t, s: f'润色:{t}') as single:
            out = ai_polish._polish_chunk(['甲', '乙'], {})
        self.assertEqual(out, ['润色:甲', '润色:乙'])
        self.assertEqual(single.call_count, 2)


class WhisperCacheTests(unittest.TestCase):
    """模型缓存释放 API 不能污染其它条目，且能真正清空。"""

    def setUp(self):
        speech_recognizer.unload_all_whisper_models()

    def tearDown(self):
        speech_recognizer.unload_all_whisper_models()

    @staticmethod
    def _key(path, device, compute):
        return (os.path.normcase(os.path.abspath(path)), device, compute)

    def test_unload_by_device_only_removes_matching_entries(self):
        cache = speech_recognizer._shared_whisper_models
        key_gpu = self._key('model-a', 'cuda', 'float16')
        key_cpu = self._key('model-a', 'cpu', 'int8')
        key_other = self._key('model-b', 'cuda', 'float16')
        cache.update({key_gpu: object(), key_cpu: object(), key_other: object()})
        speech_recognizer.unload_whisper_model('model-a', device='cpu')
        self.assertIn(key_gpu, cache)
        self.assertNotIn(key_cpu, cache)
        self.assertIn(key_other, cache)

    def test_unload_with_path_none_still_filters_device(self):
        """path=None 时不能短路清空全部，device 过滤必须仍然生效。"""
        cache = speech_recognizer._shared_whisper_models
        key_cpu = self._key('m1', 'cpu', 'int8')
        key_gpu = self._key('m2', 'cuda', 'float16')
        cache.update({key_cpu: object(), key_gpu: object()})
        speech_recognizer.unload_whisper_model(device='cpu')
        self.assertNotIn(key_cpu, cache)
        self.assertIn(key_gpu, cache, '其它设备的模型不能被误删')

    def test_unload_all_clears_cache(self):
        cache = speech_recognizer._shared_whisper_models
        cache[self._key('m', 'cpu', 'int8')] = object()
        speech_recognizer.unload_all_whisper_models()
        self.assertEqual(len(cache), 0)


if __name__ == '__main__':
    unittest.main()
