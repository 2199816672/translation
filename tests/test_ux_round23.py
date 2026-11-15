#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 23 轮发布前加固：切模型目录必须真的换识别器，状态不能谎报成功。

本轮发布前交叉审查查出 7 处问题，其中「设置页只清模块单例、主窗口 self.ocr
仍持有绑定旧目录的 Reader」会让上一轮的「选中即切换」核心功能实际不生效。
这里用零依赖的源码契约 + 行为测试把修复锁住，避免下次改动回退。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ocr_models
import user_messages

ROOT = Path(__file__).resolve().parents[1]


def _src(name):
    return (ROOT / name).read_text(encoding='utf-8')


def _write_models(directory):
    """在目录里放一套「只识别中文」所需的最小模型（非空文件）。"""
    for name in (ocr_models._DETECTION_MODEL, 'zh_sim_g2.pth'):
        (Path(directory) / name).write_bytes(b'model' * 256)
    return directory


class ActiveDirSameSourceTests(unittest.TestCase):
    """「打开文件夹 / 复制路径 / 导入目标」必须和识别器实际读取目录同源。"""

    def test_legacy_only_models_open_the_directory_reader_actually_reads(self):
        with tempfile.TemporaryDirectory() as legacy, \
                tempfile.TemporaryDirectory() as root:
            _write_models(legacy)
            with mock.patch.object(ocr_models, 'MODEL_ROOT', root), \
                    mock.patch.object(ocr_models, 'LEGACY_EASY_DIR', legacy), \
                    mock.patch.object(ocr_models, 'custom_model_dir',
                                      return_value=''):
                resolved = ocr_models.detect_local_models()['resolved_dir']
                self.assertEqual(resolved, legacy)
                self.assertEqual(ocr_models.active_model_dir(), resolved)
                self.assertEqual(ocr_models.ensure_model_root(), resolved)

    def test_custom_dir_still_wins(self):
        with tempfile.TemporaryDirectory() as custom, \
                tempfile.TemporaryDirectory() as legacy, \
                tempfile.TemporaryDirectory() as root:
            _write_models(custom)
            with mock.patch.object(ocr_models, 'MODEL_ROOT', root), \
                    mock.patch.object(ocr_models, 'LEGACY_EASY_DIR', legacy), \
                    mock.patch.object(ocr_models, 'custom_model_dir',
                                      return_value=custom):
                self.assertEqual(ocr_models.active_model_dir(), custom)
                self.assertEqual(
                    ocr_models.detect_local_models()['resolved_dir'], custom)

    def test_empty_legacy_falls_back_to_managed_root(self):
        with tempfile.TemporaryDirectory() as legacy, \
                tempfile.TemporaryDirectory() as root:
            with mock.patch.object(ocr_models, 'MODEL_ROOT', root), \
                    mock.patch.object(ocr_models, 'LEGACY_EASY_DIR', legacy), \
                    mock.patch.object(ocr_models, 'custom_model_dir',
                                      return_value=''):
                self.assertEqual(ocr_models.active_model_dir(), root)
                self.assertEqual(
                    ocr_models.detect_local_models()['resolved_dir'], root)


class DocAndHintTests(unittest.TestCase):
    """文案不能笼统说「导入后自动选中」，也不能无条件承诺零安装。"""

    def test_readme_distinguishes_copy_and_direct_specify(self):
        readme = _src('README.md')
        self.assertIn('①② 复制完立即生效', readme)
        self.assertIn('③ 指定后该目录会**立刻出现在下拉里并自动选中**', readme)
        self.assertIn('立即保存并重建识别器生效', readme)

    def test_manual_hint_distinguishes_copy_and_direct_specify(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(ocr_models, 'MODEL_ROOT', root), \
                    mock.patch.object(ocr_models, 'LEGACY_EASY_DIR', root), \
                    mock.patch.object(ocr_models, 'custom_model_dir',
                                      return_value=''):
                hint = ocr_models.manual_download_hint()
        self.assertIn('复制完就生效', hint)
        self.assertIn('指定后该目录会立刻出现在下拉里并自动选中', hint)
        self.assertIn('无需重启', hint)

    def test_settings_caption_does_not_promise_auto_select_for_copy(self):
        src = _src('pages/settings_page.py')
        self.assertIn('复制完立即生效', src)
        self.assertIn('指定后会立刻出现在下拉里', src)
        self.assertIn('立即重建生效、无需重启', src)


class EngineStatusTests(unittest.TestCase):
    """状态判定、失败提示与保存失败分支都不能自相矛盾。"""

    def test_403_for_windows_engine_does_not_loop_back_to_windows(self):
        text = user_messages.friendly_install_error(
            'ERROR: HTTP error 403 while getting https://example.invalid/x',
            engine='windows')
        self.assertIn('403', text)
        self.assertIn('重试安装', text)
        self.assertNotIn('改用「Windows 系统自带」', text)
        self.assertIn('这次装的就是 Windows 自带 OCR', text)

    def test_403_for_easy_engine_still_offers_fallback(self):
        text = user_messages.friendly_install_error(
            'ERROR: HTTP error 403 while getting https://example.invalid/x',
            engine='easy')
        for token in ('403', '换用其它源', '腾讯云', '重试安装',
                      '全部源都失败', 'Windows 系统自带'):
            self.assertIn(token, text)
        self.assertNotIn('零安装、开箱可用', text)

    def test_detect_models_uses_selected_engine(self):
        src = _src('pages/settings_page.py')
        detect = src.split('def _detect_ocr_models', 1)[1].split(
            'def _after_ocr_model_change', 1)[0]
        self.assertIn('engine = self._get_selected_engine_key()', detect)
        self.assertIn('check_engine_available(engine)', detect)
        self.assertIn("if engine != 'easy'", detect)
        self.assertNotIn("check_engine_available('easy')", detect)

    def test_switch_to_windows_ocr_checks_save_result_first(self):
        src = _src('pages/settings_page.py')
        body = src.split('def _switch_to_windows_ocr', 1)[1].split(
            'def _on_ocr_engine_changed', 1)[0]
        guard = body.index('if not save_user_config')
        ui_change = body.index('self.ocr_engine_combo.setCurrentText')
        success = body.index('InfoBar.success')
        self.assertLess(guard, ui_change)
        self.assertLess(guard, success)
        self.assertIn('win_ready', body)
        self.assertIn('"切换失败"', body)
        self.assertNotIn('零安装、开箱可用；以后装好', body)

    def test_failure_digest_does_not_promise_zero_install(self):
        src = _src('subprocess_util.py')
        self.assertIn('免下载模型', src)
        self.assertNotIn('OCR（零安装）', src)

    def test_install_result_passes_engine_to_friendly_hint(self):
        src = _src('pages/settings_page.py')
        self.assertIn('friendly_install_error(\n                msg, engine='
                      'self._get_selected_engine_key())', src)


class MainWindowContractTests(unittest.TestCase):
    """主窗口在模型目录变化时必须重建 self.ocr，不能只清模块单例。"""

    def test_settings_saved_compares_model_dir(self):
        src = _src('main_window.py')
        body = src.split('def _on_settings_saved', 1)[1].split(
            '# ── 快捷键', 1)[0]
        for token in ('old_model_dir', 'new_model_dir',
                      'old_model_dir != new_model_dir',
                      'self._init_ocr_async()'):
            self.assertIn(token, body, token)
        self.assertIn('if old_engine != new_engine or '
                      'old_model_dir != new_model_dir:', body)


try:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import Qt  # noqa: F401
    HAS_QT = True
except Exception:                                          # pragma: no cover
    HAS_QT = False


@unittest.skipUnless(HAS_QT, '需要 PySide6（Python 3.11 离屏环境）')
class MainWindowBehaviorTests(unittest.TestCase):
    """真实调用 _on_settings_saved，验证目录变化会触发识别器重建。"""

    def _run_saved(self, old_dir, new_dir,
                   old_engine='easy', new_engine='easy'):
        import main_window
        fake = mock.MagicMock()
        fake.cfg = {'ocr_engine': old_engine, 'ocr_model_dir': old_dir}
        new_cfg = {'ocr_engine': new_engine, 'ocr_model_dir': new_dir}
        with mock.patch.object(main_window, 'load_user_config',
                               return_value=dict(new_cfg)), \
                mock.patch('hotkeys.get_hotkeys', return_value={}):
            main_window.MainWindow._on_settings_saved(fake)
        return fake

    def test_model_dir_change_rebuilds_ocr(self):
        fake = self._run_saved('', r'C:\models\zh_sim')
        fake._init_ocr_async.assert_called_once_with()

    def test_same_model_dir_does_not_rebuild(self):
        fake = self._run_saved(r'C:\models\zh_sim', r'C:\models\zh_sim')
        fake._init_ocr_async.assert_not_called()

    def test_engine_change_still_rebuilds_ocr(self):
        fake = self._run_saved('', '', 'easy', 'windows')
        fake._init_ocr_async.assert_called_once_with()

    def test_reset_to_default_rebuilds_ocr(self):
        fake = self._run_saved(r'C:\models\custom', '')
        fake._init_ocr_async.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
