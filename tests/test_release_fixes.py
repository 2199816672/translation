#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前复查修复项的回归测试。

零第三方依赖部分覆盖：单文件字幕翻译失败不冒充译文、导出模式降级、
安装包版本号防呆。UI 部分（需 PySide6）覆盖：识别完成后收起进度条、
FlowLayout 的 setAlignment 真正生效。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在任何 Qt 模块被导入之前设置，保证 CI/无显示器环境可跑。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import build_installer
import subtitle

try:
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:  # 只把「没装 Qt」当跳过理由
    HAS_QT = False

if HAS_QT:
    # 真实导入错误必须让测试失败，不能被伪装成「环境缺失」而跳过
    import flow_layout
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame, QPushButton
    from pages.av_page import AVPage


class TranslationSafetyTests(unittest.TestCase):
    """单文件字幕翻译：失败项必须置空，不能把原文塞回译文列表。"""

    def test_exception_marks_empty_instead_of_source(self):
        texts = ['hello', 'world', 'again']

        def fake(text):
            if text == 'world':
                raise RuntimeError('网络错误')
            return '译文_' + text

        results, failed, err = subtitle.translate_segments(texts, fake)
        self.assertEqual(results, ['译文_hello', '', '译文_again'])
        self.assertEqual(failed, 1)
        self.assertIn('网络错误', err)

    def test_empty_none_and_blank_all_count_as_failed(self):
        values = {'a': '', 'b': None, 'c': '   ', 'd': '译d'}
        results, failed, _ = subtitle.translate_segments(
            list(values), lambda t: values[t])
        self.assertEqual(results, ['', '', '', '译d'])
        self.assertEqual(failed, 3)

    def test_all_failed_keeps_positions(self):
        def fake(_text):
            raise ValueError('配额用尽')

        results, failed, err = subtitle.translate_segments(['a', 'b'], fake)
        self.assertEqual(results, ['', ''])
        self.assertEqual(failed, 2)
        self.assertIn('配额用尽', err)

    def test_progress_reports_every_five_and_last(self):
        seen = []
        subtitle.translate_segments(
            ['x'] * 12, lambda t: 'ok', lambda i, n: seen.append((i, n)))
        self.assertEqual(seen, [(5, 12), (10, 12), (12, 12)])

    def test_target_mode_falls_back_when_translation_incomplete(self):
        self.assertEqual(
            subtitle.safe_export_mode(2, ['译1', '译2'], 'target'),
            ('target', 0))
        self.assertEqual(
            subtitle.safe_export_mode(2, ['译1', ''], 'target'),
            ('source', 1))
        self.assertEqual(
            subtitle.safe_export_mode(2, ['译1'], 'target'),
            ('source', 1))
        # both 模式逐条只保留一行原文，无需整份降级
        self.assertEqual(
            subtitle.safe_export_mode(2, ['译1', ''], 'both'), ('both', 0))
        self.assertEqual(
            subtitle.safe_export_mode(2, [], 'source'), ('source', 0))

    def test_real_srt_never_masquerades_source_as_target(self):
        entries = [
            {'start': 0, 'end': 1000, 'text': 'hello'},
            {'start': 1000, 'end': 2000, 'text': 'world'},
        ]
        trans, failed, _ = subtitle.translate_segments(
            [e['text'] for e in entries],
            lambda t: '译文_' + t if t == 'hello' else '')
        self.assertEqual(failed, 1)
        mode, missing = subtitle.safe_export_mode(
            len(entries), trans, 'target')
        self.assertEqual((mode, missing), ('source', 1))
        srt = subtitle.serialize_subtitle(
            subtitle.compose_entries(entries, trans, mode, '.srt'), '.srt')
        self.assertIn('hello', srt)
        self.assertIn('world', srt)
        self.assertNotIn('译文_', srt)  # 整份退回原文，不混半份译文

        both = subtitle.serialize_subtitle(
            subtitle.compose_entries(entries, trans, 'both', '.srt'), '.srt')
        self.assertIn('hello\n译文_hello', both)
        self.assertNotIn('world\nworld', both)  # 不产出「原文 / 原文」


class InstallerVersionGuardTests(unittest.TestCase):
    """--version 只改安装包名，不改程序内 APP_VERSION，正式构建必须拒绝。"""

    def test_full_build_rejects_version_mismatch(self):
        argv = ['build_installer.py', '--version', '1.4.0']
        with mock.patch.object(build_installer, 'read_version',
                               return_value='1.5.0'), \
             mock.patch.object(sys, 'argv', argv):
            with self.assertRaisesRegex(RuntimeError, '不一致'):
                build_installer.main()

    def test_skip_exe_still_allows_local_override(self):
        argv = ['build_installer.py', '--version', '1.3.0', '--skip-exe']
        with mock.patch.object(build_installer, 'read_version',
                               return_value='1.5.0'), \
             mock.patch.object(build_installer, 'find_iscc',
                               return_value='iscc'), \
             mock.patch.object(build_installer, 'latest_staging_app',
                               return_value=Path('/nonexistent')), \
             mock.patch.object(sys, 'argv', argv):
            # 版本检查没有拦住它，是被「找不到可复用目录」挡下的
            with self.assertRaisesRegex(RuntimeError, '找不到可复用'):
                build_installer.main()


@unittest.skipUnless(HAS_QT, '未安装 PySide6，跳过发布前 UI 回归')
class UiReleaseFixTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_asr_done_hides_progress_bar(self):
        page = AVPage()
        page.progress.setVisible(True)
        page._on_asr_done({
            'segments': [{'start': 0, 'end': 1000, 'text': 'hi'}],
            'language': '',
        })
        self.assertFalse(page.progress.isVisible())
        page.close()

    def test_set_alignment_is_applied(self):
        frame = QFrame()
        lay = flow_layout.FlowLayout(frame, spacing=6)
        short = QPushButton('短')
        tall = QPushButton('高')
        tall.setFixedHeight(60)
        lay.addWidget(short)
        lay.addWidget(tall)
        frame.resize(400, 120)
        frame.show()
        self.app.processEvents()
        center_y = short.y()

        lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        frame.resize(401, 120)
        self.app.processEvents()
        top_y = short.y()
        self.assertLess(top_y, center_y)

        lay.setAlignment(Qt.AlignmentFlag.AlignBottom)
        frame.resize(402, 120)
        self.app.processEvents()
        self.assertGreater(short.y(), top_y)
        frame.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
