#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 26 轮回归：冻结版内置包只收了部分子模块，在线组件「装好了却用不了」。

真机复现（v1.5.6 冻结版 + 用户真实 user_libs，干净进程首次导入）：

    ImportError: cannot import name 'ImageEnhance' from 'PIL'
    → 补 ImageEnhance / ImageGrab 后变成 ImageStat
    → 补齐 Pillow 全部纯 Python 子模块后 easyocr_first_import=True

根因：PYZ 里的内置 PIL 抢先加载，user_libs 那份完整 Pillow 13 不在
``PIL.__path__`` 里，torchvision / skimage 要的子模块全找不到。
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _src(name):
    return (ROOT / name).read_text(encoding='utf-8')


class FrozenPillowContractsTests(unittest.TestCase):
    """发版冒烟必须能看见 easyocr_first_import，打包不能再漏子模块。"""

    def test_build_collects_all_pillow_submodules(self):
        body = _src('build_exe.py')
        self.assertIn('COLLECT_SUBMODULES', body)
        self.assertIn('"PIL"', body)
        self.assertIn('--collect-submodules', body)
        self.assertIn('for module in COLLECT_SUBMODULES', body)

    def test_stdlib_gap_fix_still_present(self):
        body = _src('build_exe.py')
        for name in ('graphlib', 'cProfile', 'pstats', 'modulefinder',
                     'pickletools'):
            self.assertIn('"%s"' % name, body)

    def test_smoke_log_records_first_import(self):
        body = _src('app.py')
        self.assertIn('easyocr_first_import=', body)
        self.assertIn('early_ocr', body)


class ComplementFrozenPackagesTests(unittest.TestCase):
    """真实调用 app_paths._complement_frozen_packages。"""

    def _fake_module(self, name='PIL'):
        fake = types.ModuleType(name)
        fake.__path__ = [os.path.join('_internal', name)]
        return fake

    def test_adds_user_libs_package_path_when_present(self):
        import app_paths
        with tempfile.TemporaryDirectory() as d:
            pkg = Path(d) / 'PIL'
            pkg.mkdir()
            (pkg / '__init__.py').write_text('', encoding='utf-8')
            (pkg / 'ImageStat.py').write_text('', encoding='utf-8')
            fake = self._fake_module()
            with mock.patch.dict(sys.modules, {'PIL': fake}):
                app_paths._complement_frozen_packages(Path(d))
            self.assertIn(str(pkg), fake.__path__)
            # 内置路径仍在最前：不会让新版 C 扩展覆盖内置版本
            self.assertEqual(fake.__path__[0],
                             os.path.join('_internal', 'PIL'))

    def test_idempotent(self):
        import app_paths
        with tempfile.TemporaryDirectory() as d:
            pkg = Path(d) / 'PIL'
            pkg.mkdir()
            (pkg / '__init__.py').write_text('', encoding='utf-8')
            fake = self._fake_module()
            with mock.patch.dict(sys.modules, {'PIL': fake}):
                app_paths._complement_frozen_packages(Path(d))
                app_paths._complement_frozen_packages(Path(d))
            self.assertEqual(fake.__path__.count(str(pkg)), 1)

    def test_missing_package_is_ignored(self):
        import app_paths
        with tempfile.TemporaryDirectory() as d:
            fake = self._fake_module()
            with mock.patch.dict(sys.modules, {'PIL': fake}):
                app_paths._complement_frozen_packages(Path(d))
            self.assertEqual(len(fake.__path__), 1)

    def test_immutable_path_does_not_crash(self):
        import app_paths
        with tempfile.TemporaryDirectory() as d:
            pkg = Path(d) / 'PIL'
            pkg.mkdir()
            (pkg / '__init__.py').write_text('', encoding='utf-8')
            fake = self._fake_module()
            fake.__path__ = ('_internal/PIL',)
            with mock.patch.dict(sys.modules, {'PIL': fake}):
                app_paths._complement_frozen_packages(Path(d))  # 不抛异常

    def test_enable_and_refresh_both_apply_complement(self):
        body = _src('app_paths.py')
        enable = body.split('def enable_user_libs()')[1].split(
            'def refresh_user_libs()')[0]
        refresh = body.split('def refresh_user_libs()')[1]
        self.assertIn('_complement_frozen_packages(path)', enable)
        self.assertIn('_complement_frozen_packages(path)', refresh)


class StatusTextContractsTests(unittest.TestCase):
    """非重启可解的错误不许再让用户一遍遍点「重启程序」。"""

    def test_non_restartable_error_does_not_suggest_restart(self):
        body = _src('pages/settings_page.py')
        self.assertNotIn('可点「重启程序」重试', body)
        self.assertIn("restart_helps = need_restart and 'once per process'",
                      body)
        self.assertIn('重启不一定能解决', body)

    def test_restart_button_only_for_once_per_process(self):
        body = _src('pages/settings_page.py')
        self.assertIn('self.ocr_restart_btn.setVisible(restart_helps)', body)


class RealStatusTextTests(unittest.TestCase):
    """Python 3.11 离屏真实调用设置页状态逻辑。"""

    def _page(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        try:
            from pages import settings_page
        except ImportError:
            self.skipTest('需要 PySide6 / qfluentwidgets')
        page = mock.MagicMock()
        page._get_selected_engine_key.return_value = 'easy'
        return settings_page.SettingsPage._refresh_ocr_status, page

    def _run(self, detail):
        fn, page = self._page()
        with mock.patch('ocr_recognizer.check_engine_available',
                        return_value=False), \
             mock.patch('ocr_recognizer.engine_files_installed',
                        return_value=True), \
             mock.patch('ocr_recognizer.engine_check_error',
                        return_value=detail), \
             mock.patch('ocr_models.detect_local_models',
                        return_value={'ready': True}):
            fn(page)
        return page.ocr_status_label.setText.call_args[0][0], page

    def test_pillow_import_error_hides_restart_button(self):
        text, page = self._run(
            "ImportError: cannot import name 'ImageStat' from 'PIL'")
        self.assertIn('加载失败', text)
        self.assertNotIn('可点「重启程序」重试', text)
        page.ocr_restart_btn.setVisible.assert_called_with(False)
        page.ocr_use_windows_btn.setVisible.assert_called_with(True)

    def test_once_per_process_still_shows_restart_button(self):
        text, page = self._run(
            'ImportError: torch can only be initialized once per process')
        self.assertIn('组件已安装完成', text)
        page.ocr_restart_btn.setVisible.assert_called_with(True)


if __name__ == '__main__':
    unittest.main()
