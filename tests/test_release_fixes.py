#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前复查修复项的回归测试。

零第三方依赖部分覆盖：单文件字幕翻译失败不冒充译文、导出模式降级、
安装包版本号防呆。UI 部分（需 PySide6）覆盖：识别完成后收起进度条、
FlowLayout 的 setAlignment 真正生效。
"""
import ast
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在任何 Qt 模块被导入之前设置，保证 CI/无显示器环境可跑。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import build_installer
import bump_version
import config
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


class VersionSyncTests(unittest.TestCase):
    """发版版本号：APP_VERSION 是唯一数据源，其余硬编码位置自动同步。"""

    def _copy_markers(self, root):
        for name in bump_version.TARGETS:
            shutil.copyfile(ROOT / name, root / name)

    def test_all_markers_match_config(self):
        versions = bump_version.read_versions(ROOT)
        self.assertEqual(versions['config.py'], config.APP_VERSION)
        self.assertEqual(set(versions.values()), {config.APP_VERSION})
        self.assertEqual(bump_version.check(ROOT), [])
        self.assertEqual(build_installer.read_version(), config.APP_VERSION)

    def test_apply_version_updates_every_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._copy_markers(root)
            changed = bump_version.apply_version('9.8.7', root)
            self.assertEqual(set(changed), set(bump_version.TARGETS))
            self.assertEqual(set(bump_version.read_versions(root).values()),
                             {'9.8.7'})
            self.assertEqual(bump_version.check(root), [])

    def test_apply_preserves_bom_and_other_bytes(self):
        """iss 的 UTF-8 BOM / CRLF 不能丢，且只改版本号那一段。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._copy_markers(root)
            before = {n: (root / n).read_bytes()
                      for n in bump_version.TARGETS}
            old = config.APP_VERSION.encode('ascii')
            bump_version.apply_version('9.8.7', root)
            for name, original in before.items():
                new = (root / name).read_bytes()
                self.assertEqual(new.startswith(b'\xef\xbb\xbf'),
                                 original.startswith(b'\xef\xbb\xbf'), name)
                self.assertEqual(new.replace(b'9.8.7', old, 1), original, name)

    def test_check_detects_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._copy_markers(root)
            readme = root / 'README.md'
            bom, text = bump_version._read(readme)
            text = text.replace('# 翻译工具 v' + config.APP_VERSION,
                                '# 翻译工具 v0.0.1', 1)
            readme.write_bytes(bom + text.encode('utf-8'))
            problems = bump_version.check(root)
            self.assertTrue(any('README.md' in p for p in problems), problems)

    def test_rejects_malformed_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ('1.5', 'v1.5.2', '1.5.2-beta', ''):
                with self.assertRaises(ValueError, msg=bad):
                    bump_version.apply_version(bad, tmp)


class Round6BugFixTests(unittest.TestCase):
    """本轮全面检查修复项的回归防线。

    CI / 本机都没有 PySide6、numpy、requests、openpyxl，因此 UI 与引擎的
    行为无法直接跑；这里对零依赖部分写真实用例，对其余部分用 AST / 源码
    契约断言，确保修复不被后续改动抹掉。
    """

    def test_setup_name_whitelist_rejects_trailing_newline(self):
        import updater
        self.assertIsNotNone(updater.SETUP_NAME_RE.fullmatch(
            'TranslationTool-Setup-v1.5.2.exe'))
        for bad in ('TranslationTool-Setup-v1.5.2.exe\n',
                    'TranslationTool-Setup-v1.5.2.exe\r\n',
                    'TranslationTool-Setup-v1.5.2.exe.bat',
                    'evil-Setup-v1.5.2.exe'):
            with self.subTest(bad=bad):
                self.assertIsNone(updater.SETUP_NAME_RE.fullmatch(bad))
                # 带 \n 的资产名在 Windows 上写 .part 会直接 OSError
                with self.assertRaises(updater.UpdateError):
                    updater.safe_download_path(
                        types.SimpleNamespace(setup_name=bad))

    def test_txt_to_docx_is_actually_implemented(self):
        """白名单声明的组合必须有实现，不能再落到「暂不支持」。"""
        import convert_libs
        import format_convert
        self.assertIn('docx', format_convert.CONVERSION_MAP['.txt'])
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'note.txt'
            src.write_text('hello', encoding='utf-8')
            with mock.patch.object(convert_libs, 'read_text',
                                   return_value='hello'), \
                 mock.patch.object(convert_libs, 'text_to_docx_bytes',
                                   return_value=b'DOCX') as convert:
                items = convert_libs.convert_bytes(str(src), 'docx')
            self.assertEqual(len(items), 1)
            self.assertTrue(items[0][0].endswith('.docx'))  # 输出名带 _converted
            self.assertEqual(items[0][1], b'DOCX')
            convert.assert_called_once()

    def test_csv_encoding_fallback_never_duplicates_rows(self):
        """前 8KB 纯 ASCII、后段 GBK 时，编码回退不能把前段重复追加。"""
        import convert_libs
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'mixed.csv'
            head = ('\n'.join(f'r{i},value{i}' for i in range(1000))
                    + '\n').encode('ascii')
            self.assertGreater(len(head), 8192)  # 确保跨 TextIOWrapper 分块
            src.write_bytes(head + '最后一行,中文'.encode('gbk'))
            rows = convert_libs._read_csv_rows(str(src))
        self.assertEqual(len(rows), 1001)
        self.assertEqual(rows[0], ['r0', 'value0'])
        self.assertEqual(rows[-1], ['最后一行', '中文'])
        self.assertEqual(len({tuple(r) for r in rows}), 1001)

    def test_pending_install_failure_never_raises(self):
        """冻结态 windowed 下 stderr 可能为 None，提示逻辑自身不能抛异常。"""
        import app
        fake_ctypes = mock.MagicMock()
        with mock.patch.object(app.sys, 'platform', 'win32'), \
             mock.patch.dict(sys.modules, {'ctypes': fake_ctypes}), \
             mock.patch('builtins.open', side_effect=OSError('denied')):
            app._report_pending_install_failure(
                'C:/tmp/setup.exe', RuntimeError('boom'))
        # 绝不能真的弹模态框（CI 是 Windows runner，会挂住），但必须尝试过
        fake_ctypes.windll.user32.MessageBoxW.assert_called_once()

    def test_caption_start_guard_resets_in_finally(self):
        """启动检查的 4 个早退 return 必须都在 finally 的复位覆盖范围内。"""
        tree = ast.parse((ROOT / 'main_window.py').read_text(encoding='utf-8'))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == '_start_subtitle')
        tries = [n for n in fn.body if isinstance(n, ast.Try) and n.finalbody]
        self.assertTrue(tries, '_start_subtitle 缺少带 finally 的 try')
        reset = any(
            isinstance(sub, ast.Assign) and any(
                isinstance(t, ast.Attribute) and t.attr == '_caption_starting'
                for t in sub.targets)
            for node in tries[0].finalbody for sub in ast.walk(node))
        self.assertTrue(reset, 'finally 必须复位 _caption_starting')

    def test_whisper_iteration_stays_inside_model_lock(self):
        """生成器迭代（真正推理）必须在 with 锁内，不能只锁 transcribe()。"""
        tree = ast.parse((ROOT / 'live_caption.py').read_text(encoding='utf-8'))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == '_transcribe')
        withs = [n for n in ast.walk(fn) if isinstance(n, ast.With)]
        self.assertTrue(withs, '_transcribe 缺少模型锁')
        inside = [n for n in ast.walk(withs[0])
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == 'clean_text']
        self.assertTrue(inside, 'segments 迭代必须在锁内完成')

    def test_translation_failures_never_substitute_source(self):
        """译文失败回填原文是明确禁止的，防止四处修复回潮。"""
        banned = {
            'free_translator.py': ('.get("translatedText", chunk)',),
            'realtime_translate.py': ('translated.strip() or source',
                                      'results.append(src)'),
            'live_caption.py': ('translator.translate(text) or text',
                                'translated = text       # 翻译失败保留原文'),
        }
        for name, patterns in banned.items():
            src = (ROOT / name).read_text(encoding='utf-8')
            for pattern in patterns:
                with self.subTest(name=name, pattern=pattern):
                    self.assertNotIn(pattern, src)


class UXImprovementTests(unittest.TestCase):
    """体验优化回归：零依赖部分跑真实行为，UI 部分做源码契约断言。"""

    def test_friendly_errors_are_chinese_and_actionable(self):
        import user_messages as um
        cases = (
            (um.friendly_install_error, 'ERROR: Read timed out.', '超时'),
            (um.friendly_install_error, 'No space left on device', '磁盘'),
            (um.friendly_install_error, 'Permission denied', '权限'),
            (um.friendly_install_error, 'Could not find a version', '版本'),
            (um.friendly_model_error, 'ConnectionError: timed out', '超时'),
            (um.friendly_model_error, 'No space left on device', '磁盘'),
            (um.friendly_network_error, 'urlopen error [WinError 10060]', '超时'),
            (um.friendly_network_error, 'getaddrinfo failed', 'DNS'),
            (um.friendly_network_error, 'HTTP Error 403: rate limit', '限流'),
        )
        for fn, raw, keyword in cases:
            with self.subTest(fn=fn.__name__, raw=raw):
                text = fn(raw)
                self.assertIn(keyword, text)
                self.assertNotIn('Traceback', text)
                self.assertNotIn(raw, text)  # 不把英文原文回显到界面
        for fn in (um.friendly_install_error, um.friendly_model_error,
                   um.friendly_network_error):
            for raw in ('', None, 'weird <unnamed> failure'):
                with self.subTest(fn=fn.__name__, raw=raw):
                    self.assertTrue(fn(raw).strip())

    def test_missing_component_hides_pip_command_when_frozen(self):
        import convert_libs
        with mock.patch.object(sys, 'frozen', True, create=True):
            text = convert_libs.missing_component('Pillow')
        self.assertNotIn('pip install', text)
        self.assertIn('设置', text)
        with mock.patch.object(sys, 'frozen', False, create=True):
            text = convert_libs.missing_component('Pillow')
        self.assertIn('pip install Pillow', text)
        src = (ROOT / 'convert_libs.py').read_text(encoding='utf-8')
        self.assertNotIn('请先安装依赖: pip install', src)

    def test_network_error_text_reused_by_both_update_paths(self):
        import updater
        import user_messages
        self.assertIs(updater.friendly_network_error,
                      user_messages.friendly_network_error)
        for name in ('updater.py', 'check_update.py'):
            with self.subTest(file=name):
                src = (ROOT / name).read_text(encoding='utf-8')
                self.assertIn('friendly_network_error(exc)', src)

    def test_retry_one_row_keeps_other_failures(self):
        from batch_transcribe import BatchRunner, Job, DONE, FAILED, PENDING
        jobs = [Job('a.mp3'), Job('b.mp3'), Job('c.mp3')]
        jobs[0].status = PENDING
        jobs[1].status = DONE
        jobs[1].output = 'b.srt'
        jobs[2].status = FAILED
        jobs[2].detail = '原失败原因'
        started, finished = [], []
        runner = BatchRunner(
            jobs, {}, cancel=None,
            transcribe=lambda *a, **k: {'segments': []},
            only_rows={0})
        runner.run(on_start=lambda i, n, job: started.append(i),
                   on_finish=lambda i, n, job: finished.append(i))
        self.assertEqual(started, [1], '只应重跑右键选中的那一项')
        self.assertEqual(finished, [1, 2, 3], '其余行也要推进总进度')
        self.assertEqual(jobs[1].status, DONE)
        self.assertEqual(jobs[1].output, 'b.srt')
        self.assertEqual(jobs[2].status, FAILED)
        self.assertEqual(jobs[2].detail, '原失败原因')

    def test_ux_contracts_in_ui_sources(self):
        """本机 / CI 没有 PySide6，UI 改动用源码契约防回潮。"""
        checks = {
            'pages/home_page.py': (
                'def refresh_shortcuts', 'qt_key_sequence_text',
                'get_local_hotkeys', 'QShortcut(seq, self)',
                'set_translating', '_offer_undo_clear', '_restore_lang',
                'home_source_lang', '译文将显示在这里',
                'setWordWrap(True)'),
            'main_window.py': (
                'apply_appearance(self.cfg)', '_refresh_tray_actions',
                'tray_hint_shown', 'set_translating(False)',
                'self.home.refresh_shortcuts()', '_on_image_copied',
                'cancel_running_tasks()',
                'position=InfoBarPosition.TOP, parent=self)'),
            'subtitle_overlay.py': ('def apply_appearance', '_flash_state'),
            'batch_dialog.py': (
                '_on_table_menu', '_output_folders', 'empty_hint',
                'only_rows=only_rows'),
            'batch_transcribe.py': (
                'only_rows', '(index - 1) not in self.only_rows',
                'cancel_event=cancel', 'run_cancelable'),
            'screenshot_overlay.py': (
                'setInterval(4000)', '已保存到', '放弃标注？',
                'imageCopied.emit'),
            'pages/settings_page.py': (
                '_on_model_progress', 'on_progress=on_progress',
                'friendly_install_error(msg)', '_sig_model_progress',
                '_task_toggle', 'cancel_event=cancel',
                'test_api_connection', 'model_cache_info',
                'LOCAL_HOTKEY_ACTIONS', 'reserved_conflicts'),
            'pages/av_page.py': (
                '_download_cancel', 'cancel_event=cancel',
                'model_cache_summary'),
        }
        for name, patterns in checks.items():
            src = (ROOT / name).read_text(encoding='utf-8')
            for pattern in patterns:
                with self.subTest(file=name, pattern=pattern):
                    self.assertIn(pattern, src)


if __name__ == '__main__':
    unittest.main(verbosity=2)
