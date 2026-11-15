#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 22 轮：OCR 本地模型下拉，与字幕页「导入后立刻出现在下拉里」同款交互。

用户诉求：导入本地 OCR 模型后，模型下拉里应该马上能看到它，并且能随时
切换。这里锁住三件事：
1. 下拉数据源列出自动 / 默认 / 旧版 / 用户导入目录，含就绪状态与动态路径；
2. 导入或「直接指定目录」会把目录登记进配置，切换写回 ``ocr_model_dir``
   后识别器真正读取的就是那个目录；
3. 设置页切换立即保存 + 重建识别器，导入流程会刷新下拉。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ocr_models

DETECTION = "craft_mlt_25k.pth"
CHINESE = "zh_sim_g2.pth"
ENGLISH = "en_g2.pth"


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _write_models(directory, names):
    os.makedirs(directory, exist_ok=True)
    for name in names:
        Path(directory, name).write_bytes(b"m" * 2048)


def _choice(key, label, tip, directory):
    return {'key': key, 'label': label, 'tip': tip, 'dir': directory,
            'ready': True, 'exists': True, 'languages': ['ch_sim'],
            'files': 2, 'source': 'imported'}


class ModelChoiceTests(unittest.TestCase):
    """下拉数据源是纯函数：不依赖 PySide6，也不写用户配置。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = self._tmp.name
        self.root = os.path.join(base, "ocr")
        self.legacy = os.path.join(base, "legacy")
        self.imported = os.path.join(base, "my_easyocr_models")
        _write_models(self.root, [DETECTION, CHINESE])
        _write_models(self.legacy, [DETECTION, CHINESE])
        _write_models(self.imported, [DETECTION, CHINESE, ENGLISH])

    def tearDown(self):
        self._tmp.cleanup()

    def _choices(self, cfg=None):
        return ocr_models.model_choices(
            cfg=cfg or {}, root=self.root, legacy=self.legacy)

    def test_auto_and_default_choices_always_present(self):
        choices = self._choices()
        self.assertEqual(choices[0]['key'], ocr_models.AUTO_MODEL_KEY)
        self.assertIn("自动", choices[0]['label'])
        self.assertEqual(choices[1]['key'], self.root)
        self.assertTrue(choices[1]['ready'])
        self.assertIn("简体中文", choices[1]['label'])
        self.assertTrue(choices[0]['current'])
        # 提示里是运行时目录，不是写死在说明里的绝对路径
        self.assertIn(self.root, choices[1]['tip'])

    def test_imported_dir_appears_in_dropdown_and_is_selected(self):
        ok, dirs = ocr_models.register_model_dir(
            self.imported, cfg={}, save=False)
        self.assertTrue(ok)
        self.assertEqual(dirs, [self.imported])
        cfg = {ocr_models.KNOWN_MODEL_DIRS_KEY: dirs,
               ocr_models.CUSTOM_MODEL_DIR_KEY: self.imported}
        choices = self._choices(cfg)
        matched = [c for c in choices
                   if ocr_models.choice_key(c['key'])
                   == ocr_models.choice_key(self.imported)]
        self.assertEqual(len(matched), 1)
        item = matched[0]
        self.assertEqual(item['source'], 'imported')
        self.assertTrue(item['ready'])
        self.assertTrue(item['current'])
        self.assertIn("简体中文", item['label'])
        self.assertIn(self.imported, item['tip'])

    def test_switching_to_a_choice_changes_the_dir_reader_uses(self):
        value = ocr_models.custom_value_for_key(self.imported)
        self.assertEqual(value, self.imported)
        with mock.patch('config.load_user_config',
                        return_value={ocr_models.CUSTOM_MODEL_DIR_KEY: value}):
            self.assertEqual(ocr_models.resolve_model_dir(), value)
            self.assertEqual(ocr_models.reader_languages(),
                             ['ch_sim', 'en'])
        self.assertEqual(
            ocr_models.custom_value_for_key(ocr_models.AUTO_MODEL_KEY), "")
        self.assertEqual(ocr_models.choice_key(""),
                         ocr_models.AUTO_MODEL_KEY)

    def test_custom_dir_not_registered_still_shows_in_dropdown(self):
        # 用户手工改配置 / 老版本留下的自定义目录，不能因为没登记就藏起来
        cfg = {ocr_models.CUSTOM_MODEL_DIR_KEY: self.imported}
        choices = self._choices(cfg)
        self.assertTrue(any(
            ocr_models.choice_key(c['key'])
            == ocr_models.choice_key(self.imported) for c in choices))
        self.assertTrue(next(c for c in choices if c['current'])['current'])

    def test_known_dirs_dedup_and_tolerate_bad_config(self):
        cfg = {ocr_models.KNOWN_MODEL_DIRS_KEY: [
            self.imported, self.imported.upper(), "", None]}
        self.assertEqual(ocr_models.known_model_dirs(cfg), [self.imported])
        self.assertEqual(ocr_models.known_model_dirs(
            {ocr_models.KNOWN_MODEL_DIRS_KEY: "one-string"}),
            [os.path.abspath("one-string")])
        self.assertEqual(ocr_models.known_model_dirs(
            {ocr_models.KNOWN_MODEL_DIRS_KEY: 123}), [])

    def test_default_and_legacy_dirs_are_not_registered_twice(self):
        with mock.patch.object(ocr_models, 'MODEL_ROOT', self.root), \
             mock.patch.object(ocr_models, 'LEGACY_EASY_DIR', self.legacy):
            ok, dirs = ocr_models.register_model_dir(
                self.root, cfg={}, save=False)
            self.assertTrue(ok)
            self.assertEqual(dirs, [])

    def test_missing_dir_is_not_registered_and_is_marked_in_dropdown(self):
        gone = os.path.join(self._tmp.name, "gone")
        ok, _dirs = ocr_models.register_model_dir(gone, cfg={}, save=False)
        self.assertFalse(ok)
        choices = self._choices(
            {ocr_models.KNOWN_MODEL_DIRS_KEY: [gone]})
        item = next(c for c in choices
                    if ocr_models.choice_key(c['key'])
                    == ocr_models.choice_key(gone))
        self.assertIn("目录已失效", item['label'])
        self.assertFalse(item['ready'])

    def test_register_writes_only_the_known_dirs_key(self):
        with mock.patch('config.save_user_config', return_value=True) as saved:
            ok, dirs = ocr_models.register_model_dir(self.imported, cfg={})
        self.assertTrue(ok)
        saved.assert_called_once_with(
            {ocr_models.KNOWN_MODEL_DIRS_KEY: dirs})

    def test_manual_hint_explains_the_dropdown(self):
        hint = ocr_models.manual_download_hint()
        self.assertIn("如何选择本地模型", hint)
        self.assertIn("「本地模型」下拉", hint)
        self.assertIn("立刻出现在下拉里", hint)
        self.assertIn("直接指定模型目录", hint)
        self.assertIn("不复制", hint)
        self.assertNotIn("不需要手动挑模型", hint)


class UiContractTests(unittest.TestCase):
    """设置页接线与文案：导入后刷新下拉，切换立即生效。"""

    def test_settings_page_wires_model_dropdown(self):
        src = _src("pages/settings_page.py")
        for token in ('"本地模型:"', 'self.ocr_model_combo = QComboBox()',
                      '_on_ocr_model_selected', '_reload_ocr_model_combo',
                      'ocr_models.model_choices()',
                      'ocr_models.register_model_dir',
                      '_reload_ocr_model_combo(select=select)',
                      '_after_ocr_model_change(select=folder)',
                      'KNOWN_MODEL_DIRS_KEY: dirs',
                      'reset_ocr_instance()',
                      'CUSTOM_MODEL_DIR_KEY: value'):
            self.assertIn(token, src, token)
        self.assertNotIn("不用手动挑模型", src)
        self.assertNotIn("不用手动挑；", src)

    def test_readme_documents_dropdown_switch(self):
        readme = _src("README.md")
        self.assertIn("如何选择本地模型（和字幕页一样有下拉）", readme)
        self.assertIn("立刻出现在下拉里并自动选中", readme)
        self.assertNotIn("程序不需要手动挑模型", readme)


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
    HAS_QT = True
except ImportError:  # pragma: no cover
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "真实 PySide6 仅在 Python 3.11 可用")
class SettingsSmokeTests(unittest.TestCase):
    """离屏真实构造：导入后下拉出现、切换写配置并重建识别器。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _page(self):
        from pages.settings_page import SettingsPage
        with mock.patch("pages.settings_page.save_user_config",
                        return_value=True), \
             mock.patch.object(QMessageBox, "question",
                               return_value=QMessageBox.StandardButton.No), \
             mock.patch.object(QMessageBox, "information",
                               return_value=None):
            return SettingsPage()

    def test_reload_shows_imported_dir_and_selects_it(self):
        page = self._page()
        self.assertTrue(hasattr(page, "ocr_model_combo"))
        with tempfile.TemporaryDirectory() as d:
            _write_models(d, [DETECTION, CHINESE])
            choices = [
                _choice(ocr_models.AUTO_MODEL_KEY, "自动（推荐）", "自动", d),
                _choice(d, "📌 my_models · 简体中文 ✓", "目录：" + d, d),
            ]
            with mock.patch('ocr_models.model_choices',
                            return_value=choices):
                page._reload_ocr_model_combo(select=d)
            combo = page.ocr_model_combo
            self.assertEqual(combo.count(), 2)
            idx = combo.findData(d)
            self.assertGreaterEqual(idx, 0)
            self.assertEqual(combo.currentIndex(), idx)
            self.assertEqual(combo.currentData(), d)
            self.assertIn(d, str(combo.itemData(
                idx, Qt.ItemDataRole.ToolTipRole)))

    def test_selecting_dir_saves_immediately_and_resets_reader(self):
        page = self._page()
        with tempfile.TemporaryDirectory() as d:
            _write_models(d, [DETECTION, CHINESE])
            choices = [
                _choice(ocr_models.AUTO_MODEL_KEY, "自动（推荐）", "自动", d),
                _choice(d, "📌 my_models", "目录：" + d, d),
            ]
            with mock.patch('ocr_models.model_choices',
                            return_value=choices), \
                 mock.patch('ocr_models.custom_model_dir',
                            return_value=''), \
                 mock.patch('ocr_models.current_choice_key',
                            return_value=ocr_models.AUTO_MODEL_KEY), \
                 mock.patch('pages.settings_page.save_user_config',
                            return_value=True) as saved, \
                 mock.patch('pages.settings_page.load_user_config',
                            return_value={}), \
                 mock.patch('ocr_recognizer.reset_ocr_instance') as reset, \
                 mock.patch.object(page, '_after_ocr_model_change') as after, \
                 mock.patch('qfluentwidgets.InfoBar.success'):
                page._reload_ocr_model_combo(
                    select=ocr_models.AUTO_MODEL_KEY)
                page.ocr_model_combo.setCurrentIndex(
                    page.ocr_model_combo.findData(d))
            saved.assert_called_once_with(
                {ocr_models.CUSTOM_MODEL_DIR_KEY: d})
            reset.assert_called_once()
            after.assert_called_once()
            self.assertEqual(after.call_args.kwargs.get('select'), d)

    def test_import_folder_registers_dir_for_the_dropdown(self):
        page = self._page()
        with tempfile.TemporaryDirectory() as d, \
             mock.patch('PySide6.QtWidgets.QFileDialog.getExistingDirectory',
                        return_value=d), \
             mock.patch('ocr_models.active_model_dir', return_value=d), \
             mock.patch('ocr_models.import_local_model',
                        return_value=(2, [CHINESE])), \
             mock.patch('ocr_models.has_required_models',
                        return_value=True), \
             mock.patch('ocr_models.register_model_dir',
                        return_value=(True, [d])) as register, \
             mock.patch.object(page, '_after_ocr_model_change') as after, \
             mock.patch('qfluentwidgets.InfoBar.success'):
            page._import_ocr_model_dir()
            register.assert_called_once_with(d)
            after.assert_called_once()

    def test_import_files_from_same_folder_registers_source_dir(self):
        page = self._page()
        with tempfile.TemporaryDirectory() as d:
            paths = [os.path.join(d, DETECTION), os.path.join(d, CHINESE)]
            with mock.patch(
                    'PySide6.QtWidgets.QFileDialog.getOpenFileNames',
                    return_value=(paths, '')), \
                 mock.patch('ocr_models.active_model_dir',
                            return_value=d), \
                 mock.patch('ocr_models.import_local_model',
                            return_value=(2, [CHINESE])), \
                 mock.patch('ocr_models.has_required_models',
                            return_value=True), \
                 mock.patch('ocr_models.register_model_dir',
                            return_value=(True, [d])) as register, \
                 mock.patch.object(page, '_after_ocr_model_change'), \
                 mock.patch('qfluentwidgets.InfoBar.success'):
                page._import_ocr_model_files()
                register.assert_called_once_with(d)


if __name__ == "__main__":
    unittest.main()
