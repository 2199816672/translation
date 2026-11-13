#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""体验优化回归：可配置快捷键 / 可取消任务 / 连接测试 / 模型缓存。

本机与 CI 都没有 PySide6、numpy、requests，UI 部分用源码契约防回潮；
零依赖的底层链路（hotkeys / cancelable / pip 子进程 / 缓存统计 / 服务探测 /
下载与请求取消）全部写真实行为用例。
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import batch_transcribe
import cancelable
import hotkeys
import ocr_recognizer
import speech_recognizer as asr
import subprocess_util
import user_messages


class LocalHotkeyTests(unittest.TestCase):
    """Ctrl+Enter 现在可配置、可禁用，并与全局快捷键一起做冲突检测。"""

    def test_default_disable_and_custom(self):
        self.assertEqual(
            hotkeys.get_local_hotkeys({}),
            {'translate_home': 'ctrl+enter'})
        self.assertEqual(
            hotkeys.get_local_hotkeys({'hotkey_local_translate': ''}),
            {'translate_home': ''}, '显式写空 = 用户主动禁用，不能被默认值覆盖')
        self.assertEqual(
            hotkeys.get_local_hotkeys({'hotkey_local_translate': 'Alt+Q'}),
            {'translate_home': 'alt+q'})

    def test_global_manager_never_sees_local_action(self):
        cfg = {'hotkey_local_translate': 'ctrl+enter', 'hotkey': 'ctrl+shift+a'}
        self.assertNotIn('translate_home', hotkeys.get_hotkeys(cfg))
        merged = hotkeys.all_hotkeys(cfg)
        self.assertIn('translate_home', merged)
        self.assertIn('screenshot', merged)
        self.assertEqual(hotkeys.config_key('translate_home'),
                         'hotkey_local_translate')
        self.assertEqual(hotkeys.action_of_config_key('hotkey_local_translate'),
                         'translate_home')

    def test_restore_defaults_merges_both_groups(self):
        defaults = hotkeys.default_hotkeys()
        self.assertEqual(defaults['translate_home'], 'ctrl+enter')
        self.assertEqual(defaults['screenshot'], '')
        self.assertEqual(len(defaults),
                         len(hotkeys.HOTKEY_ACTIONS)
                         + len(hotkeys.LOCAL_HOTKEY_ACTIONS))

    def test_conflicts_across_groups_and_reserved(self):
        merged = {'screenshot': 'ctrl+enter',
                  'translate_home': 'ctrl+enter',
                  'show_main': 'ctrl+f'}
        conflicts = dict(hotkeys.find_conflicts(merged))
        self.assertEqual(len(conflicts.get('ctrl+enter', [])), 2)
        reserved = hotkeys.reserved_conflicts(merged)
        self.assertEqual(len(reserved), 1)
        self.assertEqual(reserved[0][0], 'ctrl+f')
        self.assertEqual(reserved[0][1], '设置页搜索')
        self.assertNotIn('ctrl+shift+a', reserved)

    def test_qt_key_name_mapping(self):
        # Qt 的 "Enter" 是小键盘回车，主回车叫 "Return"；不映射会导致
        # 默认的 Ctrl+Enter 在普通键盘上按不动。
        self.assertEqual(hotkeys.qt_key_sequence_text('ctrl+enter'),
                         'ctrl+return')
        self.assertEqual(hotkeys.qt_key_sequence_text('CTRL + SHIFT + A'),
                         'ctrl+shift+a')
        self.assertEqual(hotkeys.display_hotkey('ctrl+enter'), 'Ctrl + Enter')
        self.assertEqual(hotkeys.display_hotkey('alt+q'), 'Alt + Q')


class CancelableTests(unittest.TestCase):
    def test_without_event_is_plain_call(self):
        self.assertEqual(cancelable.run_cancelable(lambda: 42), 42)
        with self.assertRaises(ValueError):
            cancelable.run_cancelable(lambda: (_ for _ in ()).throw(
                ValueError('boom')))

    def test_already_cancelled(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(cancelable.CallCancelled):
            cancelable.run_cancelable(lambda: 1, event)

    def test_cancel_returns_immediately_invokes_hook(self):
        event = threading.Event()
        called = []

        def slow():
            time.sleep(4)
            return 'late'

        def stopper():
            time.sleep(0.1)
            event.set()

        threading.Thread(target=stopper, daemon=True).start()
        start = time.monotonic()
        with self.assertRaises(cancelable.CallCancelled):
            cancelable.run_cancelable(slow, event, poll=0.05,
                                      on_cancel=lambda: called.append(1))
        self.assertLess(time.monotonic() - start, 1.5,
                        '取消必须立即返回，不能等阻塞调用结束')
        self.assertEqual(called, [1])

    def test_exception_is_propagated(self):
        event = threading.Event()

        def fail():
            raise RuntimeError('real error')

        with self.assertRaisesRegex(RuntimeError, 'real error'):
            cancelable.run_cancelable(fail, event, poll=0.05)


class PipProcessCancelTests(unittest.TestCase):
    def test_cancel_kills_child_process(self):
        event = threading.Event()
        cmd = [sys.executable, '-c', 'import time; time.sleep(30)']
        start = time.monotonic()
        with subprocess_util.pip_process(
                cmd, cancel=event, timeout=60,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True) as proc:
            event.set()
            proc.stdout.read()
        self.assertLess(time.monotonic() - start, 5,
                        '点取消后 pip 子进程必须被 kill，不能等满超时')
        self.assertNotEqual(proc.returncode, 0)

    def test_ocr_install_honours_pre_cancelled_event(self):
        event = threading.Event()
        event.set()
        ok, msg = ocr_recognizer.install_engine_with_progress(
            'easy', cancel=event)
        self.assertFalse(ok)
        self.assertEqual(msg, subprocess_util.CANCELLED_TEXT)


class ModelCacheTests(unittest.TestCase):
    def test_counts_and_marks_incomplete_downloads(self):
        with tempfile.TemporaryDirectory() as root:
            complete = Path(root) / 'tiny'
            complete.mkdir()
            for name in asr.REQUIRED_MODEL_FILES:
                (complete / name).write_bytes(b'x' * 100)
            partial = Path(root) / 'large-v3-turbo'
            partial.mkdir()
            (partial / 'model.bin').write_bytes(b'y' * 50)
            (Path(root) / 'not_a_dir.txt').write_bytes(b'z' * 9)

            info = asr.model_cache_info(root)
            self.assertTrue(info['exists'])
            self.assertEqual(info['total_bytes'], 350)
            self.assertEqual(len(info['models']), 2)
            by_name = {m['name']: m for m in info['models']}
            self.assertTrue(by_name['tiny']['ready'])
            self.assertFalse(by_name['large-v3-turbo']['ready'])

            summary = asr.model_cache_summary(info)
            self.assertIn('2 个模型目录', summary)
            self.assertIn('1 个已就绪', summary)
            self.assertIn('1 个未完成下载', summary)

    def test_missing_root_is_reported_not_created(self):
        with tempfile.TemporaryDirectory() as parent:
            root = os.path.join(parent, 'never_created')
            info = asr.model_cache_info(root)
            self.assertFalse(info['exists'])
            self.assertFalse(os.path.exists(root))
            self.assertIn('尚无本地模型', asr.model_cache_summary(info))

    def test_human_size(self):
        self.assertEqual(asr.human_size(512), '512 B')
        self.assertEqual(asr.human_size(2048), '2 KB')
        self.assertEqual(asr.human_size(480 * 1048576), '480 MB')
        self.assertEqual(asr.human_size(1610612736), '1.50 GB')


class _FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _n=-1):
        return b'{"data": []}'

    def close(self):
        pass


class ServiceProbeTests(unittest.TestCase):
    @staticmethod
    def _http_error(code):
        import io
        return urllib.error.HTTPError(
            'https://example.com/v1/models', code, 'err', {},
            io.BytesIO(b'{"error":"bad"}'))

    def _probe(self, open_side_effect):
        opener = mock.MagicMock()
        opener.open.side_effect = open_side_effect
        with mock.patch.object(asr.urllib.request, 'build_opener',
                               return_value=opener), \
             mock.patch.object(asr, '_validate_api_url',
                               side_effect=lambda url, allow_private=False: url):
            return asr.test_api_connection('https://example.com/v1', 'key')

    def test_empty_address(self):
        ok, msg = asr.test_api_connection('  ', '')
        self.assertFalse(ok)
        self.assertIn('请先填写服务地址', msg)

    def test_ok(self):
        ok, msg = self._probe(lambda req, timeout=None: _FakeResponse(200))
        self.assertTrue(ok)
        self.assertIn('连接正常', msg)

    def test_bad_key(self):
        ok, msg = self._probe(lambda req, timeout=None: (_ for _ in ()).throw(
            self._http_error(401)))
        self.assertFalse(ok)
        self.assertIn('密钥无效', msg)

    def test_404_does_not_claim_service_works(self):
        # 端口通不等于识别服务可用，不能弹「测试成功」让用户白高兴
        ok, msg = self._probe(lambda req, timeout=None: (_ for _ in ()).throw(
            self._http_error(404)))
        self.assertFalse(ok)
        self.assertIn('服务已连通', msg)
        self.assertIn('无法确认识别能力', msg)

    def test_network_error_is_chinese(self):
        ok, msg = self._probe(lambda req, timeout=None: (_ for _ in ()).throw(
            TimeoutError('timed out')))
        self.assertFalse(ok)
        self.assertIn('超时', msg)
        self.assertNotIn('timed out', msg)

    def test_friendly_service_error_mappings(self):
        self.assertIn('DNS', user_messages.friendly_service_error(
            'getaddrinfo failed'))
        self.assertIn('无法连接', user_messages.friendly_service_error(
            'Connection refused 10061'))
        self.assertIn('密钥', user_messages.friendly_service_error(
            'HTTP 403 forbidden'))


class DownloadCancelTests(unittest.TestCase):
    def test_pre_cancelled_never_touches_network(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(asr.ModelDownloadCancelled):
            asr.download_model('tiny', cancel_event=event)

    def test_cancel_does_not_retry_other_mirror(self):
        calls = []
        event = threading.Event()

        def snapshot_download(**kwargs):
            calls.append(kwargs)
            event.set()
            return None

        fake_hub = types.ModuleType('huggingface_hub')
        fake_hub.snapshot_download = snapshot_download
        with tempfile.TemporaryDirectory() as root, \
             mock.patch.object(asr, 'model_dir',
                               return_value=os.path.join(root, 'tiny')), \
             mock.patch.dict(sys.modules,
                             {'huggingface_hub': fake_hub, 'tqdm.auto': None}):
            with self.assertRaises(asr.ModelDownloadCancelled):
                asr.download_model('tiny', cancel_event=event)
        self.assertEqual(len(calls), 1, '取消后不能再重试官方镜像')

    def test_progress_tqdm_raises_on_cancel(self):
        class Base:
            def __init__(self):
                self.total = 2_000_000
                self.n = 0

            def update(self, n=1):
                self.n += n

        cls = asr._ProgressTqdm.make(
            Base, callback=lambda done, total: None, cancel=threading.Event())
        cls().update(1)
        event = threading.Event()
        event.set()
        cls2 = asr._ProgressTqdm.make(
            Base, callback=lambda done, total: None, cancel=event)
        with self.assertRaises(asr.ModelDownloadCancelled):
            cls2().update(1)


class ApiRequestCancelTests(unittest.TestCase):
    def test_transcribe_api_cancel_returns_immediately(self):
        holder = mock.MagicMock()
        event = threading.Event()

        def slow_open(req, timeout=None):
            time.sleep(3)
            return _FakeResponse()

        opener = mock.MagicMock()
        opener.open.side_effect = slow_open

        def stopper():
            time.sleep(0.1)
            event.set()

        threading.Thread(target=stopper, daemon=True).start()
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(b'RIFF')
            path = f.name
        try:
            with mock.patch.object(asr, '_build_multipart',
                                   return_value=(b'body', 'multipart', holder)), \
                 mock.patch.object(asr, '_validate_api_url',
                                   side_effect=lambda url, allow_private=False: url), \
                 mock.patch.object(asr.urllib.request, 'build_opener',
                                   return_value=opener):
                start = time.monotonic()
                with self.assertRaises(cancelable.CallCancelled):
                    asr.transcribe_api(
                        path, 'https://example.com/v1', '', 'whisper-1',
                        cancel_event=event)
                self.assertLess(time.monotonic() - start, 1.5,
                                '停止不能等 600 秒的在线请求返回')
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


class BatchCancelTests(unittest.TestCase):
    def test_translate_cancel_never_fakes_translation(self):
        """停止时立即抛出 Cancelled，且绝不把原文塞进译文列表。"""
        fake = types.ModuleType('translator')

        class SlowTranslator:
            def __init__(self, *args, **kwargs):
                pass

            def translate(self, text):
                time.sleep(3)
                return '译文'

        fake.Translator = SlowTranslator
        event = threading.Event()

        def stopper():
            time.sleep(0.1)
            event.set()

        threading.Thread(target=stopper, daemon=True).start()
        with mock.patch.dict(sys.modules, {'translator': fake}):
            start = time.monotonic()
            with self.assertRaises(batch_transcribe.Cancelled):
                batch_transcribe._default_translate(
                    ['原文'], {}, None, event)
            self.assertLess(time.monotonic() - start, 1.5)

    def test_api_transcribe_cancel_is_normalized(self):
        """底层因取消抛出的任何异常，都要归一成 Cancelled（不是 FAILED）。"""
        fake = types.ModuleType('speech_recognizer')

        def fake_api(*args, **kwargs):
            raise cancelable.CallCancelled()

        fake.transcribe_api = fake_api
        event = threading.Event()
        event.set()
        with mock.patch.dict(sys.modules, {'speech_recognizer': fake}):
            with self.assertRaises(batch_transcribe.Cancelled):
                batch_transcribe._default_transcribe(
                    'a.mp3', {'mode': 'api'}, None, event)


class SourceContractTests(unittest.TestCase):
    """UI / 打包链路没有 PySide6，用源码契约防止改动被回退。"""

    def _src(self, name):
        return (ROOT / name).read_text(encoding='utf-8')

    def test_hotkey_and_shortcut_wiring(self):
        self.assertIn('LOCAL_HOTKEY_ACTIONS', self._src('hotkeys.py'))
        self.assertIn('def refresh_shortcuts', self._src('pages/home_page.py'))
        self.assertIn('self.home.refresh_shortcuts()',
                      self._src('main_window.py'))
        self.assertIn('all_hotkeys(self._cfg)',
                      self._src('pages/settings_page.py'))

    def test_cancel_wiring(self):
        worker = self._src('subprocess_util.py')
        self.assertIn('cancel=None', worker)
        self.assertIn('cancel.is_set()', worker)
        self.assertIn('proc.kill()', worker)
        self.assertIn('CANCELLED_TEXT', worker)
        self.assertIn('cancel=cancel', self._src('live_caption.py'))
        self.assertIn('cancel_event=self._stop', self._src('live_caption.py'))
        self.assertIn('cancel_event=cancel',
                      self._src('pages/settings_page.py'))
        self.assertIn('cancel_event=cancel',
                      self._src('pages/av_page.py'))
        self.assertIn('_task_toggle', self._src('pages/settings_page.py'))
        self.assertIn('cancel_running_tasks()', self._src('main_window.py'))

    def test_service_test_and_cache_wiring(self):
        settings = self._src('pages/settings_page.py')
        self.assertIn('test_translation', settings)
        self.assertIn('test_api_connection', settings)
        self.assertIn('model_cache_info', settings)
        self.assertIn('_copy_model_cache_path', settings)
        self.assertIn('def test_api_connection',
                      self._src('speech_recognizer.py'))
        self.assertIn('def test_translation', self._src('translator.py'))
        self.assertIn('config_override', self._src('translator.py'))
        self.assertIn('_REQUIRED_CREDENTIALS', self._src('translator.py'))

    def test_image_copy_feedback_wiring(self):
        self.assertIn('imageCopied = Signal(str)',
                      self._src('screenshot_overlay.py'))
        self.assertIn('imageCopied.emit', self._src('screenshot_overlay.py'))
        self.assertIn('imageCopied.connect', self._src('main_window.py'))
        self.assertIn('tray.showMessage', self._src('main_window.py'))

    def test_frozen_build_includes_new_module(self):
        self.assertIn('"cancelable"', self._src('build_exe.py'))

    def test_update_download_cancel_not_regressed(self):
        dialog = self._src('update_dialog.py')
        self.assertIn('取消下载', dialog)
        self.assertIn('_cancel.set()', dialog)


class ServiceProbeStrictTests(unittest.TestCase):
    # 「地址可达」不等于「识别服务可用」，测试连接不能误报成功

    def _probe_with(self, response):
        opener = mock.MagicMock()
        opener.open.return_value = response
        with mock.patch.object(asr.urllib.request, 'build_opener',
                               return_value=opener), \
             mock.patch.object(asr, '_validate_api_url',
                               side_effect=lambda url, allow_private=False: url):
            return asr.test_api_connection(
                'https://example.com/v1', '', allow_private=True)

    def test_plain_html_200_is_rejected(self):
        class HtmlResponse(_FakeResponse):
            def read(self, _n=-1):
                return b'<html><body>hello</body></html>'

        ok, msg = self._probe_with(HtmlResponse(200))
        self.assertFalse(ok)
        self.assertIn('不是标准的模型列表', msg)

    def test_models_list_response_is_accepted(self):
        class ListResponse(_FakeResponse):
            def read(self, _n=-1):
                return b'{"object":"list","data":[{"id":"whisper-1"}]}'

        ok, msg = self._probe_with(ListResponse(200))
        self.assertTrue(ok)
        self.assertIn('连接正常', msg)


class HotkeyAliasTests(unittest.TestCase):
    def test_win_alias_maps_to_meta(self):
        self.assertEqual(
            hotkeys.qt_key_sequence_text('win+ctrl+enter'),
            'meta+ctrl+return')

    def test_internal_conflict_uses_warning_not_error(self):
        errors, warnings = [], []
        backend = mock.MagicMock()
        backend.add_hotkey.return_value = 1
        mgr = hotkeys.HotkeyManager(
            lambda a: None, on_error=errors.append,
            on_warning=warnings.append, backend=backend)
        mgr.reload({'screenshot': 'ctrl+shift+a',
                    'fullscreen': 'ctrl+shift+a'})
        self.assertTrue(warnings, '配置撞车应走警告通道')
        self.assertEqual(errors, [], '不应误报「注册失败」')


class TranslationProbeTests(unittest.TestCase):
    # 本机没有 deep_translator / requests，注入替身后再加载模块

    def test_echoed_source_text_is_not_success(self):
        fake_dt = types.ModuleType('deep_translator')
        fake_dt.DeeplTranslator = mock.MagicMock()
        fake_dt.BaiduTranslator = mock.MagicMock()
        fake_exc = types.ModuleType('deep_translator.exceptions')
        fake_exc.RequestError = type('RequestError', (Exception,), {})
        fake_dt.exceptions = fake_exc
        mods = {'deep_translator': fake_dt,
                'deep_translator.exceptions': fake_exc,
                'requests': mock.MagicMock()}
        with mock.patch.dict(sys.modules, mods):
            sys.modules.pop('translator', None)
            import translator as tr
            fake_engine = mock.MagicMock()
            fake_engine.translate.return_value = 'Hello, world'
            with mock.patch.object(tr, 'Translator', return_value=fake_engine):
                ok, msg = tr.test_translation('bing_free', {})
        sys.modules.pop('translator', None)
        self.assertFalse(ok)
        self.assertIn('疑似未翻译', msg)


class CacheSummaryEdgeTests(unittest.TestCase):
    def test_zero_byte_empty_dir_is_not_a_partial_download(self):
        info = {'root': 'X', 'exists': True, 'total_bytes': 0,
                'models': [{'name': 'tiny', 'path': 'X/tiny',
                            'bytes': 0, 'ready': False}]}
        self.assertNotIn('未完成下载', asr.model_cache_summary(info))

    def test_real_partial_download_is_reported(self):
        info = {'root': 'X', 'exists': True, 'total_bytes': 1024,
                'models': [{'name': 'tiny', 'path': 'X/tiny',
                            'bytes': 1024, 'ready': False}]}
        self.assertIn('1 个未完成下载', asr.model_cache_summary(info))


class Round7SourceContracts(unittest.TestCase):
    # UI 侧没有 PySide6，用源码契约防止本轮修复被回退

    def _src(self, name):
        return (ROOT / name).read_text(encoding='utf-8')

    def test_cache_hint_uses_elided_label_api(self):
        av = self._src('pages/av_page.py')
        self.assertIn('set_full_text(', av)
        self.assertIn('downloading = getattr', av)
        self.assertIn('cancelled = (not ok)', av)
        self.assertIn("if getattr(self, '_download_cancel', None) is None:", av)

    def test_service_probe_strictness(self):
        s = self._src('speech_recognizer.py')
        self.assertIn('looks_like_models', s)
        self.assertIn('无法确认识别能力', s)
        self.assertIn("if not m.get('ready')", s)

    def test_translation_probe_and_global_cache(self):
        t = self._src('translator.py')
        self.assertIn('result == text', t)
        self.assertNotIn('invalidate_free_translator(self.api_type)', t)

    def test_home_shortcut_feedback(self):
        h = self._src('pages/home_page.py')
        self.assertIn("color or '#71717a'", h)
        self.assertIn('invalid = False', h)
        self.assertIn('无效，Qt 无法识别', h)

    def test_hotkey_warning_channel(self):
        hk = self._src('hotkeys.py')
        self.assertIn('on_warning=None', hk)
        self.assertIn("'win': 'meta'", hk)
        self.assertIn('self._on_warning(msg)', hk)

    def test_settings_round7(self):
        s = self._src('pages/settings_page.py')
        for token in ('show_hotkey_warning', 'if self._task_busy:',
                      '下载组件加载失败', 'and not ok',
                      'setMinimumWidth(120)', '请先结束当前操作',
                      'self.subtitle_engine_combo.setEnabled(False)',
                      '【多个动作用了同一组合】',
                      '网络慢时可能要等半分钟'):
            self.assertIn(token, s)

    def test_main_window_round7(self):
        m = self._src('main_window.py')
        for token in ('_capture_pending', '_tray_notify',
                      'cancelled_by_user', 'self.home._on_translate()',
                      'on_warning=self.settings.show_hotkey_warning'):
            self.assertIn(token, m)

    def test_dialogs_round7(self):
        self.assertIn('cancelled_by_user', self._src('update_dialog.py'))
        self.assertIn('当前文件会被跳过', self._src('batch_dialog.py'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
