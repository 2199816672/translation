#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 25 轮：组件「装好了却显示还差引擎」的状态判定修复。

用户实测：内置安装进度条显示「安装完成」，回到设置页又变回黄色警告
「模型文件已就位，还差引擎」，反复不知道到底装没装成功。检查安装版
user_libs 后确认 easyocr 1.7.2 与 25 个依赖都已完整落盘，是**判定**错了：

* ``check_engine_available`` 只捕 ImportError，torch / scipy 的 DLL 加载
  失败抛 OSError 时被上层 ``except Exception`` 静默兜底成「未安装」；
* 判定只看「本进程能否 import」，不看「文件是否已经装好」；pip 在子进程
  里装完，主进程可能因为 DLL 半加载状态暂时导不进来，重启就好了；
* 进度条一看到 pip 输出 ``Successfully`` 就报「安装完成」，随后最终判定
  又把它覆盖成警告，制造「完成 → 又没装好」的落差；
* 冻结版只把 ``_internal`` 注册成 DLL 搜索目录，user_libs 下的
  ``torch/lib`` / ``*.libs`` 没注册，新装包本来就更容易导不进来。

这里把「文件落盘」与「本进程可导入」分开，并记录真实异常供诊断。
"""
from __future__ import annotations

import builtins
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app_paths
import ocr_recognizer

ROOT = Path(__file__).resolve().parents[1]


def _src(name):
    return (ROOT / name).read_text(encoding='utf-8')


def _fake_libs(root, *, dist=True, pkg=True, engine='easy'):
    """造一个最小的「组件已装好」目录。"""
    root = Path(root)
    if engine == 'easy':
        if pkg:
            (root / 'easyocr').mkdir(parents=True, exist_ok=True)
            (root / 'easyocr' / '__init__.py').write_text('', encoding='utf-8')
        if dist:
            (root / 'easyocr-1.7.2.dist-info').mkdir(parents=True, exist_ok=True)
            (root / 'easyocr-1.7.2.dist-info' / 'METADATA').write_text(
                'Name: easyocr\nVersion: 1.7.2\n', encoding='utf-8')
    else:
        if dist:
            (root / 'winrt-Windows.Media.Ocr-2.3.0.dist-info').mkdir(
                parents=True, exist_ok=True)
    return root


class EngineFilesInstalledTests(unittest.TestCase):
    """文件级判定必须与「本进程能否 import」彻底解耦。"""

    def test_easy_ready_when_pkg_and_dist_info_present(self):
        with tempfile.TemporaryDirectory() as d:
            _fake_libs(d)
            with mock.patch('app_paths.user_libs_dir', return_value=Path(d)):
                self.assertTrue(ocr_recognizer.engine_files_installed('easy'))

    def test_easy_false_without_dist_info(self):
        with tempfile.TemporaryDirectory() as d:
            _fake_libs(d, dist=False)
            with mock.patch('app_paths.user_libs_dir', return_value=Path(d)):
                self.assertFalse(ocr_recognizer.engine_files_installed('easy'))

    def test_easy_false_with_only_dist_info(self):
        with tempfile.TemporaryDirectory() as d:
            _fake_libs(d, pkg=False)
            with mock.patch('app_paths.user_libs_dir', return_value=Path(d)):
                self.assertFalse(ocr_recognizer.engine_files_installed('easy'))

    def test_easy_false_on_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch('app_paths.user_libs_dir', return_value=Path(d)):
                self.assertFalse(ocr_recognizer.engine_files_installed('easy'))

    def test_windows_marker(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch('app_paths.user_libs_dir', return_value=Path(d)):
                self.assertFalse(
                    ocr_recognizer.engine_files_installed('windows'))
            _fake_libs(d, engine='windows')
            with mock.patch('app_paths.user_libs_dir', return_value=Path(d)):
                self.assertTrue(
                    ocr_recognizer.engine_files_installed('windows'))

    def test_unknown_engine_is_false(self):
        with tempfile.TemporaryDirectory() as d:
            _fake_libs(d)
            with mock.patch('app_paths.user_libs_dir', return_value=Path(d)):
                self.assertFalse(
                    ocr_recognizer.engine_files_installed('nope'))


class CheckEngineDiagnosticsTests(unittest.TestCase):
    """自检失败必须留下真实原因，且不能只捕 ImportError。"""

    def test_non_import_error_is_recorded(self):
        real_import = builtins.__import__

        def failing(name, *args, **kwargs):
            if name == 'easyocr' or name.startswith('easyocr.'):
                raise OSError('[WinError 126] 找不到指定的模块')
            return real_import(name, *args, **kwargs)

        with mock.patch('builtins.__import__', side_effect=failing):
            self.assertFalse(ocr_recognizer.check_engine_available('easy'))
        detail = ocr_recognizer.engine_check_error('easy')
        self.assertIn('OSError', detail)
        self.assertIn('126', detail)

    def test_success_clears_previous_error(self):
        real_import = builtins.__import__

        def ok(name, *args, **kwargs):
            if name == 'easyocr':
                return mock.MagicMock()
            return real_import(name, *args, **kwargs)

        with mock.patch('builtins.__import__', side_effect=ok):
            self.assertTrue(ocr_recognizer.check_engine_available('easy'))
        self.assertEqual(ocr_recognizer.engine_check_error('easy'), '')

    def test_source_catches_all_exceptions(self):
        body = _src('ocr_recognizer.py')
        self.assertIn('except Exception as exc:', body)
        self.assertIn('_LAST_CHECK_ERROR[engine]', body)
        self.assertIn('def engine_files_installed', body)
        self.assertNotIn('except ImportError:', body)


class RefreshUserLibsTests(unittest.TestCase):
    """装完组件后，主进程必须真的能重新探测到那个目录和它的 DLL。"""

    def test_stale_none_importer_cache_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            key = os.path.normcase(str(d))
            old = sys.path_importer_cache.get(key, 'NOT_SET')
            sys.path_importer_cache[key] = None
            try:
                with mock.patch('app_paths.user_libs_dir', return_value=Path(d)):
                    app_paths.refresh_user_libs()
                self.assertNotIn(key, sys.path_importer_cache)
            finally:
                sys.path_importer_cache.pop(key, None)
                if old != 'NOT_SET':
                    sys.path_importer_cache[key] = old

    @unittest.skipUnless(os.name == 'nt', 'DLL 目录注册只在 Windows 需要')
    def test_dll_dirs_registered_and_handles_kept(self):
        with tempfile.TemporaryDirectory() as d:
            lib = Path(d) / 'torch' / 'lib'
            lib.mkdir(parents=True)
            with mock.patch('app_paths.user_libs_dir', return_value=Path(d)):
                app_paths.refresh_user_libs()
            self.assertIn(os.path.normcase(str(lib)), app_paths._DLL_DIRS)
            self.assertTrue(app_paths._DLL_HANDLES)

    def test_source_registers_dll_dirs_at_startup_too(self):
        body = _src('app_paths.py')
        self.assertIn('_register_user_lib_dll_dirs(path)', body)
        self.assertIn('torch/lib', body)
        self.assertIn('_DLL_HANDLES.append(handle)', body)


class SettingsStatusContractsTests(unittest.TestCase):
    """设置页状态文案与时序的源码契约。"""

    def setUp(self):
        self.body = _src('pages/settings_page.py')

    def test_status_uses_file_level_judgement(self):
        self.assertIn('engine_files_installed(engine)', self.body)
        self.assertIn('need_restart = bool(files_ready and not available)',
                      self.body)
        self.assertIn('return need_restart', self.body)

    def test_installed_but_not_importable_is_not_reported_as_missing(self):
        self.assertIn('组件已安装完成，点「重启程序」即可离线识别', self.body)
        self.assertIn('self.ocr_install_btn.setVisible(False)', self.body)
        self.assertIn('不用重装、不会重新下载模型', self.body)

    def test_other_load_errors_never_promise_success(self):
        # 「重启就好」只在 torch 单进程初始化那种错误下成立；其它加载失败
        # 必须如实显示错误并保留 Windows OCR 退路，不能变成新的谎报。
        self.assertIn("if 'once per process' in detail:", self.body)
        self.assertIn('组件已装好，但本次加载失败', self.body)
        self.assertIn('not (available or files_ready) or need_restart',
                      self.body)

    def test_progress_no_longer_claims_completion_early(self):
        # 「安装完成」只允许出现在最终结果回调里
        progress = self.body.split('def _update_install_progress')[1].split(
            'def _on_install_result')[0]
        self.assertNotIn('setFormat("安装完成")', progress)
        self.assertNotIn('安装完成 ✓', progress)
        result = self.body.split('def _on_install_result')[1]
        self.assertIn('setFormat("安装完成")', result[:1600])

    def test_result_shows_restart_guidance_when_needed(self):
        result = self.body.split('def _on_install_result')[1][:2200]
        self.assertIn('need_restart = self._refresh_ocr_status()', result)
        self.assertIn('组件已安装完成', result)
        self.assertIn('不用重新安装、不会重新下载模型', result)

    def test_detect_models_has_installed_but_needs_restart_branch(self):
        self.assertIn('elif files_ready:', self.body)
        self.assertIn('模型与组件都已就位', self.body)

    def test_restart_releases_single_instance_lock(self):
        self.assertIn('lock.unlock()', self.body)
        self.assertIn('QProcess.startDetached(sys.executable, args)', self.body)
        self.assertIn("a not in ('--smoke-test', '--tt-pip-worker')",
                      self.body)

    def test_restart_button_exists(self):
        self.assertIn('self.ocr_restart_btn = QPushButton("重启程序"',
                      self.body)
        self.assertIn('self.ocr_restart_btn.clicked.connect(self._restart_app)',
                      self.body)


def _qt_available():
    try:
        import PySide6  # noqa: F401
        import qfluentwidgets  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(_qt_available(), '需要 PySide6 / qfluentwidgets')
class RealSettingsStatusTests(unittest.TestCase):
    """Python 3.11 离屏真实调用 _refresh_ocr_status，验证三种状态。"""

    def _page(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from pages import settings_page
        page = mock.MagicMock()
        page._get_selected_engine_key.return_value = 'easy'
        return settings_page.SettingsPage._refresh_ocr_status, page

    def _run(self, *, available, files_ready, models_ready=True,
             detail='OSError: 126'):
        fn, page = self._page()
        with mock.patch('ocr_recognizer.check_engine_available',
                        return_value=available), \
             mock.patch('ocr_recognizer.engine_files_installed',
                        return_value=files_ready), \
             mock.patch('ocr_recognizer.engine_check_error',
                        return_value=detail), \
             mock.patch('ocr_models.detect_local_models',
                        return_value={'ready': models_ready}):
            need = fn(page)
        text = page.ocr_status_label.setText.call_args[0][0]
        return need, text, page

    def test_installed_but_not_importable_reports_success_and_restart(self):
        detail = ('ImportError: torch can only be initialized once per '
                  'process, so this module cannot be imported again')
        need, text, page = self._run(available=False, files_ready=True,
                                     detail=detail)
        self.assertTrue(need)
        self.assertIn('组件已安装完成', text)
        self.assertNotIn('还差引擎', text)
        page.ocr_restart_btn.setVisible.assert_called_with(True)
        page.ocr_install_btn.setVisible.assert_called_with(False)
        self.assertIn('once per process',
                      page.ocr_status_label.setToolTip.call_args[0][0])

    def test_installed_but_load_error_offers_fallback(self):
        need, text, page = self._run(
            available=False, files_ready=True,
            detail='AttributeError: _cufft_get_plan_cache_size')
        self.assertTrue(need)
        self.assertIn('加载失败', text)
        self.assertNotIn('即可离线识别', text)
        self.assertNotIn('还差引擎', text)
        page.ocr_use_windows_btn.setVisible.assert_called_with(True)
        page.ocr_install_btn.setVisible.assert_called_with(False)

    def test_truly_missing_still_warns(self):
        need, text, page = self._run(available=False, files_ready=False,
                                     detail='ModuleNotFoundError')
        self.assertFalse(need)
        self.assertIn('还差 EasyOCR 引擎组件', text)
        page.ocr_restart_btn.setVisible.assert_called_with(False)
        page.ocr_install_btn.setVisible.assert_called_with(True)

    def test_importable_reports_ready_without_restart(self):
        need, text, page = self._run(available=True, files_ready=True,
                                     detail='')
        self.assertFalse(need)
        self.assertIn('EasyOCR 引擎已安装', text)
        page.ocr_restart_btn.setVisible.assert_called_with(False)
        page.ocr_install_btn.setVisible.assert_called_with(False)


class FrozenBuildContractsTests(unittest.TestCase):
    """组件是运行时才装的，冻结版必须显式收进它们依赖的标准库模块。

    真机复现：冻结版带真实 user_libs 跑干净进程首次导入，报
    ``ModuleNotFoundError: No module named 'graphlib'``——重启也救不回来。
    """

    def test_hidden_imports_cover_runtime_only_stdlib(self):
        body = _src('build_exe.py')
        for name in ('"graphlib"', '"cProfile"', '"pstats"',
                     '"modulefinder"', '"pickletools"'):
            self.assertIn(name, body)
        self.assertIn('user_libs', body)

    def test_tkinter_stays_excluded(self):
        body = _src('build_exe.py')
        excludes = body.split('EXCLUDES = [')[1].split(']')[0]
        self.assertIn('"tkinter"', excludes)


if __name__ == '__main__':
    unittest.main()
