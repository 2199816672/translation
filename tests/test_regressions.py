#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全项目体检修复项的回归测试（零第三方依赖）。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app_paths
import batch_transcribe as bt
import convert_libs
import doc_parser
import format_convert
import subtitle


class SubtitleTextTests(unittest.TestCase):

    def test_br_becomes_real_newline(self):
        self.assertEqual(subtitle._clean_text('Hello<br>World'), 'Hello\nWorld')
        self.assertEqual(subtitle._clean_text('A<BR/>B'), 'A\nB')
        self.assertEqual(subtitle._clean_text('<i>Hi</i>'), 'Hi')

    def test_vtt_escapes_special_chars(self):
        text = subtitle.serialize_subtitle(
            [{'start': 0, 'end': 1000, 'text': 'A --> B & <b>C</b>'}],
            '.vtt')
        self.assertIn('A --&gt; B &amp; &lt;b&gt;C&lt;/b&gt;', text)
        # 正文里的 --> 不能变成第二个时间轴
        self.assertEqual(text.count('-->'), 1)

    def test_empty_entries_are_not_written(self):
        entries = [
            {'start': 0, 'end': 1000, 'text': ''},
            {'start': 1000, 'end': 2000, 'text': '内容'},
        ]
        for fmt in ('.srt', '.vtt', '.lrc', '.ass'):
            with self.subTest(fmt=fmt):
                out = subtitle.serialize_subtitle(entries, fmt)
                self.assertIn('内容', out)
                if fmt == '.lrc':
                    self.assertEqual(out.count('['), 1)
                if fmt == '.srt':
                    self.assertEqual(out.count('-->'), 1)
                if fmt == '.ass':
                    self.assertEqual(out.count('Dialogue:'), 1)


class ExportTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_txt_has_utf8_bom(self):
        self.assertTrue(
            convert_libs.text_to_bytes('中文', 'txt').startswith(b'\xef\xbb\xbf'))

    def test_export_corrects_wrong_extension(self):
        out = format_convert.export_text(
            '内容', None, 'txt', self.root, 'doc',
            filename=str(self.root / '手改.docx'))
        self.assertTrue(out.endswith('.txt'), out)
        self.assertEqual(Path(out).read_bytes(), '内容'.encode('utf-8-sig'))

    def test_confirmed_overwrite_really_overwrites(self):
        target = self.root / '译文.txt'
        target.write_bytes(b'old')
        out = format_convert.export_text(
            '内容', None, 'txt', self.root, 'doc',
            filename=str(target), overwrite=True)
        # Windows runner 的 TEMP 可能是 8.3 短名，同上比较同一文件
        self.assertTrue(Path(out).samefile(target),
                        f'{out} 与 {target} 不是同一文件')
        self.assertNotEqual(target.read_bytes(), b'old')

    def test_default_export_never_overwrites(self):
        first = format_convert.export_text('甲', None, 'txt', self.root, 'doc')
        second = format_convert.export_text('乙', None, 'txt', self.root, 'doc')
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_bytes(), '甲'.encode('utf-8-sig'))
        self.assertIn('_2', second)


class DocParserTests(unittest.TestCase):

    def test_txt_bom_is_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.txt'
            p.write_bytes('中文内容'.encode('utf-8-sig'))
            self.assertEqual(doc_parser._parse_txt(str(p)), '中文内容')

    def test_unknown_encoding_reports_instead_of_mojibake(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.txt'
            p.write_bytes(b'\xff\xfe\x00\x81\x82')
            with self.assertRaises(RuntimeError) as ctx:
                doc_parser._parse_txt(str(p))
            self.assertIn('编码', str(ctx.exception))


class PathTests(unittest.TestCase):

    def test_can_write_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(bt.can_write_dir(tmp))
            self.assertFalse(bt.can_write_dir(Path(tmp) / 'missing'))

    def test_fallback_dirs_are_really_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocker = root / 'denied'
            blocker.write_text('x')  # 是文件 -> APP_DIR 下 mkdir 必失败
            fallback = root / 'fallback'
            fallback.mkdir()
            with mock.patch.object(app_paths, 'APP_DIR', blocker), \
                    mock.patch.object(app_paths, '_user_data_root',
                                      return_value=fallback):
                shots = app_paths.screenshots_dir()
                models = app_paths.models_dir()
            self.assertEqual(shots, fallback / 'screenshots')
            self.assertEqual(models, fallback / 'models')
            self.assertTrue(shots.is_dir())
            self.assertTrue(models.is_dir())


class VttRoundTripTests(unittest.TestCase):

    def test_vtt_export_then_parse_restores_entities(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.vtt'
            original = [{'start': 0, 'end': 1000,
                         'text': 'AT&T <b>Hi</b>'}]
            p.write_text(subtitle.serialize_subtitle(original, '.vtt'),
                         encoding='utf-8')
            parsed = subtitle.parse_subtitle(str(p))
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0]['text'], 'AT&T Hi')


class OcrInstallTests(unittest.TestCase):

    def test_install_engine_returns_real_failure_detail(self):
        import ocr_recognizer
        with mock.patch.object(ocr_recognizer,
                               'install_engine_with_progress',
                               return_value=(False, '真实错误输出')) as fn:
            ok, msg = ocr_recognizer.install_engine('easy')
        self.assertFalse(ok)
        self.assertEqual(msg, '真实错误输出')
        self.assertTrue(fn.called)


if __name__ == '__main__':
    unittest.main(verbosity=2)
