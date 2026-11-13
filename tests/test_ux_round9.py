#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 9 轮：安装换源、OCR 模型管理、状态/任务/诊断总线、新想法 UI 契约。

本机与 CI 可能没有 PySide6，所以纯逻辑模块直接跑真实行为，UI 侧用源码
契约守住「别被回退」。
"""
from __future__ import annotations

import contextlib
import io
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ocr_models
import status_bus
import task_center
import diagnostics
import command_palette
import onboarding
import ui_theme
import ui_feedback
import subprocess_util as su
import user_messages


class FakePipProc:
    def __init__(self, returncode, lines=("Collecting demo\n",)):
        self.returncode = returncode
        self.stdout = io.StringIO(''.join(lines))

    def poll(self):
        return self.returncode


class PipMirrorTests(unittest.TestCase):
    """OCR 安装不再听命于失效的 pip.ini，403 后会自动换源。"""

    def _run(self, codes, cancel=None):
        procs = [FakePipProc(code) for code in codes]
        calls = []

        def fake_pip_cmd(packages, extra_args=()):
            calls.append((list(packages), list(extra_args)))
            return ['pip', 'install'] + list(packages) + list(extra_args), ''

        @contextlib.contextmanager
        def fake_pip_process(cmd, **kwargs):
            yield procs[len(calls) - 2] if len(calls) > 1 else procs[0]

        with mock.patch.object(su, 'pip_cmd', side_effect=fake_pip_cmd), \
             mock.patch.object(su, 'pip_process', new=fake_pip_process):
            return su.pip_install_with_mirrors(
                ['easyocr'], cancel=cancel, label='EasyOCR 引擎'), calls

    def test_explicit_index_overrides_bad_default(self):
        (ok, msg), calls = self._run([0])
        self.assertTrue(ok)
        self.assertIn('阿里云镜像', msg)
        # 第一次是 pip 可用性探测，第二次才是真正带 -i 的安装命令
        install = calls[1][1]
        self.assertIn('-i', install)
        self.assertIn('https://mirrors.aliyun.com/pypi/simple/', install)
        self.assertIn('--retries', install)

    def test_falls_back_to_next_mirror(self):
        (ok, msg), calls = self._run([1, 0])
        self.assertTrue(ok)
        self.assertIn('PyPI 官方', msg)
        self.assertGreaterEqual(len(calls), 3)

    def test_all_failed_gives_manual_command(self):
        (ok, msg), _calls = self._run([1, 1, 1])
        self.assertFalse(ok)
        self.assertIn('pip install easyocr', msg)
        self.assertIn('已依次尝试', msg)
        self.assertIn('https://pypi.org/simple', msg)

    def test_cancel_does_not_try_any_mirror(self):
        cancel = threading.Event()
        cancel.set()
        (ok, msg), calls = self._run([0], cancel=cancel)
        self.assertFalse(ok)
        self.assertEqual(msg, su.CANCELLED_TEXT)
        self.assertEqual(calls, [])

    def test_cancel_between_mirrors_stops_retry(self):
        cancel = threading.Event()

        def fake_pip_cmd(packages, extra_args=()):
            return ['pip'], ''

        @contextlib.contextmanager
        def fake_pip_process(cmd, **kwargs):
            cancel.set()          # 第一个源跑完时用户点了取消
            yield FakePipProc(1)

        with mock.patch.object(su, 'pip_cmd', side_effect=fake_pip_cmd), \
             mock.patch.object(su, 'pip_process', new=fake_pip_process):
            ok, msg = su.pip_install_with_mirrors(
                ['easyocr'], cancel=cancel)
        self.assertFalse(ok)
        self.assertEqual(msg, su.CANCELLED_TEXT)

    def test_403_has_actionable_chinese_hint(self):
        text = user_messages.friendly_install_error(
            '安装失败 (code 1): ERROR: HTTP error 403 while getting '
            'https://pypi.tuna.tsinghua.edu.cn/packages/xx')
        self.assertIn('403', text)
        self.assertIn('重试安装', text)
        self.assertIn('换用其它源', text)


class OcrModelTests(unittest.TestCase):
    def test_required_files_include_detection_and_languages(self):
        files = ocr_models.required_model_files(['ch_sim', 'en'])
        self.assertEqual(files[0], 'craft_mlt_25k.pth')
        self.assertIn('zh_sim_g2.pth', files)
        self.assertIn('en_g2.pth', files)

    def test_catalog_lists_all_engines_with_pros_and_cost(self):
        text = ocr_models.engine_catalog_text()
        for name in ('Windows 系统自带 OCR', 'EasyOCR', 'RapidOCR',
                     'PaddleOCR', 'Tesseract'):
            self.assertIn(name, text)
        self.assertIn('优势：', text)
        self.assertIn('代价：', text)
        self.assertIn('暂未内置', text)   # 不把规划项伪装成可用项

    def test_detect_and_import_from_manual_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'ocr')
            src = os.path.join(tmp, 'downloaded')
            os.makedirs(src)
            for name in ('craft_mlt_25k.pth', 'zh_sim_g2.pth', 'en_g2.pth'):
                with open(os.path.join(src, name), 'wb') as f:
                    f.write(b'model-data')

            before = ocr_models.detect_local_models(root=root, legacy=tmp + '/none')
            self.assertFalse(before['ready'])
            self.assertIn('craft_mlt_25k.pth', before['missing'])
            self.assertIn('手动下载', ocr_models.manual_download_hint())

            count, copied = ocr_models.import_local_model(src, root=root)
            self.assertEqual(count, 3)
            self.assertEqual(len(copied), 3)
            after = ocr_models.detect_local_models(root=root, legacy=tmp + '/none')
            self.assertTrue(after['ready'])
            self.assertEqual(after['missing'], [])
            self.assertTrue(ocr_models.models_complete(root))
            self.assertEqual(after['resolved_dir'], root)
            self.assertIn('3 个模型文件',
                          ocr_models.model_cache_summary(
                              ocr_models.model_cache_info(root)))

    def test_import_empty_folder_rejects_with_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as ctx:
                ocr_models.import_local_model(tmp, root=tmp + '/out')
            self.assertIn('.pth', str(ctx.exception))


class StatusBusTests(unittest.TestCase):
    def test_priority_and_fallback(self):
        seen = []
        bus = status_bus.StatusBus(
            emit=lambda t, c: seen.append((t, c)),
            idle_text='就绪', idle_color='#4ade80')
        bus.push('general', '已复制链接', '#71717a')
        bus.push('ocr_init', '正在准备 OCR 引擎…', '#eab308', priority=20)
        self.assertEqual(seen[-1][0], '正在准备 OCR 引擎…')
        bus.clear('ocr_init')
        self.assertEqual(seen[-1][0], '已复制链接')
        bus.clear('general')
        self.assertEqual(seen[-1][0], '就绪')

    def test_same_owner_overwrites_without_growing(self):
        bus = status_bus.StatusBus()
        bus.push('translate', '正在翻译…')
        bus.push('translate', '正在翻译（重试）…')
        self.assertEqual(len(bus.snapshot()), 1)
        self.assertIn('重试', bus.current()[0])

    def test_clear_other_owner_keeps_current(self):
        seen = []
        bus = status_bus.StatusBus(emit=lambda t, c: seen.append(t))
        bus.push('a', 'A', priority=5)
        bus.push('b', 'B')
        bus.clear('b')
        self.assertEqual(bus.current()[0], 'A')


class TaskCenterTests(unittest.TestCase):
    def test_start_update_finish_and_cancel_all(self):
        tc = task_center.TaskCenter()
        ev = threading.Event()
        tc.start('ocr', '安装 OCR 组件', cancelable=True, cancel=ev)
        tc.start('batch', '批量转录')
        tc.update('ocr', '正在下载依赖')
        self.assertEqual(tc.count(), 2)
        self.assertIn('安装 OCR 组件', tc.summary())
        self.assertTrue(tc.cancel('ocr'))
        self.assertTrue(ev.is_set())
        self.assertFalse(tc.cancel('batch'))       # 未登记可取消标志
        tc.finish('ocr')
        tc.finish('batch')
        self.assertEqual(tc.active(), [])

    def test_cancel_all_only_sets_cancelable(self):
        tc = task_center.TaskCenter()
        a, b = threading.Event(), threading.Event()
        tc.start('a', 'A', cancelable=True, cancel=a)
        tc.start('b', 'B', cancelable=True, cancel=b)
        tc.start('c', 'C')
        self.assertEqual(tc.cancel_all(), 2)
        self.assertTrue(a.is_set() and b.is_set())


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        diagnostics.clear()

    def test_report_has_version_modules_and_events(self):
        diagnostics.record('error', '安装组件失败', 'HTTP 403', 'OCR 安装')
        report = diagnostics.build_report()
        self.assertIn('诊断信息', report)
        self.assertIn('版本：v', report)
        self.assertIn('关键模块', report)
        self.assertIn('HTTP 403', report)
        self.assertEqual(diagnostics.error_count(), 1)
        # 不写任何密钥字段
        self.assertNotIn('api_key', report)

    def test_recent_is_bounded(self):
        for i in range(230):
            diagnostics.record('info', f'event {i}')
        self.assertLessEqual(len(diagnostics.recent(500)), 200)

    def test_health_issues_is_list(self):
        self.assertIsInstance(diagnostics.health_issues(), list)


class CommandPaletteTests(unittest.TestCase):
    def test_all_hotkey_actions_and_menu_actions_are_searchable(self):
        from hotkeys import HOTKEY_ACTIONS, LOCAL_HOTKEY_ACTIONS
        items = command_palette.collect_actions({})
        keys = {item['key'] for item in items}
        for key in HOTKEY_ACTIONS:
            self.assertIn(key, keys)
        for key in LOCAL_HOTKEY_ACTIONS:
            self.assertIn(key, keys)
        for key, _label, _desc in command_palette.EXTRA_ACTIONS:
            self.assertIn(key, keys)
        self.assertTrue(any('Ctrl' in item['hotkey'] for item in items))

    def test_search_matches_label_and_description(self):
        items = command_palette.collect_actions({})
        self.assertTrue(command_palette.search(items, '实时字幕'))
        self.assertTrue(command_palette.search(items, 'translate_home'))
        self.assertEqual(command_palette.search(items, '绝不存在的关键字'), [])


class OnboardingTests(unittest.TestCase):
    def test_fresh_install_only(self):
        self.assertTrue(onboarding.should_show({}))
        self.assertFalse(onboarding.should_show(
            {'first_run_shown': True}))
        self.assertFalse(onboarding.should_show(
            {'tray_hint_shown': True}))   # 老用户不打扰

    def test_recommended_hotkey_uses_config_key(self):
        self.assertEqual(onboarding.RECOMMENDED_HOTKEY,
                         {'hotkey': 'ctrl+alt+q'})
        self.assertEqual(len(onboarding.WELCOME_STEPS), 3)

    def test_ui_helpers_import_without_pyside(self):
        self.assertEqual(ui_theme.prevent_combo_elide(None), 0)
        self.assertEqual(ui_feedback.set_disabled_hint(None, 'x'), None)


class SourceContracts(unittest.TestCase):
    def _src(self, name):
        return (ROOT / name).read_text(encoding='utf-8')

    def test_capture_hides_window_cleanly(self):
        m = self._src('main_window.py')
        for token in ('CAPTURE_SETTLE_MS = 80', 'setWindowOpacity(0.0)',
                      '_restore_capture_opacity', 'QApplication.processEvents()',
                      '_hide_for_capture()'):
            self.assertIn(token, m)

    def test_cancel_keeps_screenshot_in_preview(self):
        s = self._src('screenshot_overlay.py')
        self.assertIn('not self._live_select and self._crop_rect', s)
        self.assertIn('self.screenshotTaken.emit(path)', s)

    def test_home_new_actions(self):
        h = self._src('pages/home_page.py')
        for token in ('复制截图', '_copy_image', '_on_save_clicked',
                      "picked.data()", '原文 + 译文', 'moreActionsRequested',
                      'view().setMinimumWidth(150)', '保存 ▾'):
            self.assertIn(token, h)

    def test_ocr_install_uses_mirrors(self):
        o = self._src('ocr_recognizer.py')
        self.assertIn('pip_install_with_mirrors', o)
        self.assertIn('ocr_models.resolve_model_dir', o)
        self.assertIn('download_enabled=not offline_ready', o)
        self.assertNotIn("安装失败 (code {proc.returncode})", o)

    def test_settings_ocr_model_management(self):
        s = self._src('pages/settings_page.py')
        for token in ('检测本地模型', '导入本地模型…', '打开模型文件夹',
                      '查看全部 OCR 方案对比', '手动下载说明',
                      'engine_catalog_text()', 'TASK_CENTER.start',
                      'ui_feedback.show_error_bar', 'QPlainTextEdit',
                      '_build_checklist_section', '_build_diagnostics_section',
                      "QShortcut(QKeySequence('Esc')"):
            self.assertIn(token, s)

    def test_main_window_new_ideas(self):
        m = self._src('main_window.py')
        for token in ('StatusBus(', 'TASK_CENTER', '_build_action_menu',
                      '_show_more_menu', '_open_command_palette',
                      '_maybe_show_welcome', '_enable_recommended_hotkey',
                      'Ctrl+K', 'prevent_combo_elide', '_open_task_center',
                      '还有任务在运行', '_home_ocr_busy',
                      "priority=20"):
            self.assertIn(token, m)

    def test_startup_splash_and_frozen_imports(self):
        self.assertIn('QSplashScreen', self._src('app.py'))
        self.assertIn('splash.finish(window)', self._src('app.py'))
        b = self._src('build_exe.py')
        for mod in ('ocr_models', 'diagnostics', 'status_bus',
                    'task_center', 'ui_feedback', 'onboarding',
                    'command_palette', 'ui_theme'):
            self.assertIn('"' + mod + '"', b)

    def test_batch_eta_and_task_registration(self):
        b = self._src('batch_dialog.py')
        for token in ('_format_eta', '_eta_text', '预计剩余',
                      "TASK_CENTER.start('batch'", 'TASK_CENTER.finish',
                      'prevent_combo_elide'):
            self.assertIn(token, b)

    def test_shortened_audio_source_label(self):
        c = self._src('config.py')
        self.assertIn("'电脑声音': 'loopback'", c)
        self.assertNotIn("'电脑内部声音': 'loopback'", c)


if __name__ == '__main__':
    unittest.main(verbosity=2)
