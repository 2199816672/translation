#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 12 轮：QComboBox 文字裁切、Qt 日志钩子生命周期、导入边界、发版细节。

「自动检测」被裁成「自动检」是真实用户反馈了两轮的问题，前一轮只修了
高度对齐没抓到根因。这里用真实 PySide6 的 subControlRect 把文字区宽度
锁住，防止以后有人调 QSS padding 又把它改回去。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import bump_version
import ocr_models


def _src(relative):
    raw = (ROOT / relative).read_bytes()
    for enc in ('utf-8-sig', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


class ComboTextClipTests(unittest.TestCase):
    """文字区 = 控件宽 − 左padding − 右padding − drop-down宽 − 边框。"""

    def test_qss_does_not_reserve_arrow_space_twice(self):
        css = _src('theme.py')
        self.assertIn('padding: 6px 8px 6px 10px;', css)
        self.assertNotIn('padding: 6px 34px 6px 10px;', css)
        block = css.split('QComboBox::drop-down')[1].split('}')[0]
        self.assertIn('width: 20px;', block)
        self.assertNotIn('width: 28px;', block)
        self.assertIn('扣了两次', css)

    def test_real_editfield_fits_longest_item(self):
        """真实 PySide6：文字区必须放得下最长候选项。无 PySide6 时跳过。"""
        try:
            os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
            from PySide6.QtWidgets import (QApplication, QStyle,
                                           QStyleOptionComboBox)
        except ImportError:
            self.skipTest('PySide6 不可用')
        app = QApplication.instance() or QApplication([])
        import theme
        app.setStyleSheet(theme.build_stylesheet())
        from pages.home_page import HomePage
        h = HomePage()
        h.resize(968, 700)
        h.show()
        app.processEvents()
        try:
            c = h.src_lang_combo
            opt = QStyleOptionComboBox()
            c.initStyleOption(opt)
            rect = c.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox, opt,
                QStyle.SubControl.SC_ComboBoxEditField, c)
            fm = c.fontMetrics()
            longest = max(fm.horizontalAdvance(c.itemText(i))
                          for i in range(c.count()))
            self.assertGreaterEqual(
                rect.width(), longest,
                f'文字区 {rect.width()}px 放不下最长项 {longest}px，'
                '「自动检测」会被裁掉半边')
        finally:
            h.close()

    def test_flow_layout_keeps_conservative_extra(self):
        """为省几十像素收窄 combo 会把刚修好的裁字问题带回来。"""
        src = _src('flow_layout.py')
        self.assertIn('_COMBO_EXTRA = 68', src)
        self.assertIn('裁掉半个字', src)
        self.assertIn('10 + 8 + 20 + 2 = 40px', src)


class QtLogFilterLifecycleTests(unittest.TestCase):
    def test_handler_is_uninstalled_on_every_exit_path(self):
        src = _src('app.py')
        self.assertIn('import atexit', src)
        self.assertIn('def _uninstall_qt_log_filter', src)
        self.assertIn('qInstallMessageHandler(None)', src)
        self.assertIn('atexit.register(_uninstall_qt_log_filter)', src)
        self.assertIn('_uninstall_qt_log_filter()', src)
        self.assertIn('global _qt_message_handler, _qt_filter_installed', src)

    def test_uninstall_is_idempotent(self):
        """重复调用（exec 返回 + atexit）不能抛异常。"""
        import app as appmod
        self.assertTrue(callable(appmod._uninstall_qt_log_filter))
        appmod._uninstall_qt_log_filter()
        appmod._uninstall_qt_log_filter()


class OcrImportEdgeTests(unittest.TestCase):
    def test_same_file_is_skipped_with_chinese_hint(self):
        """目录本身 / 已有文件不能谎报导入完成，更不能漏英文 SameFileError。"""
        self.assertIn('except shutil.SameFileError', _src('ocr_models.py'))
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'Models')
            os.makedirs(root)
            name = 'craft_mlt_25k.pth'
            with open(os.path.join(root, name), 'wb') as f:
                f.write(b'model-data')
            # 目录本身（含大小写变体）走第一个分支
            with self.assertRaises(RuntimeError) as ctx:
                ocr_models.import_local_model(root.swapcase(), root=root)
            self.assertIn('无需导入', str(ctx.exception))
            # 目录里已有的单个文件：normcase 比较后跳过，给统一中文提示
            with self.assertRaises(RuntimeError) as ctx:
                ocr_models.import_local_model(
                    os.path.join(root, name).swapcase(), root=root)
            self.assertIn('已经在模型目录里', str(ctx.exception))


class BumpVersionTests(unittest.TestCase):
    def test_readme_title_suffix_is_not_swallowed(self):
        """标题后面若加副标题，bump 只能改版本号，不能吞掉整行。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'config.py').write_text(
                'APP_VERSION = "1.5.1"\n', encoding='utf-8')
            (root / 'README.md').write_text(
                '# 翻译工具 v1.5.1 — 截图翻译\n正文\n', encoding='utf-8')
            (root / 'installer.iss').write_text(
                '#define MyAppVersion "1.5.1"\n', encoding='utf-8')
            bump_version.apply_version('1.5.2', root=root)
            title = (root / 'README.md').read_text(
                encoding='utf-8').splitlines()[0]
            self.assertEqual(title, '# 翻译工具 v1.5.2 — 截图翻译')
            self.assertEqual(bump_version.check(root), [])


class WordingTests(unittest.TestCase):
    def test_no_legacy_audio_label_in_visible_text(self):
        for rel in ('README.md', 'requirements.txt',
                    'packaging/使用说明.txt'):
            self.assertNotIn('电脑内部声音', _src(rel), rel)

    def test_av_page_no_absolute_path_tooltip(self):
        src = _src('pages/av_page.py')
        self.assertNotIn('f"模型下载位置：{asr.MODEL_ROOT}"', src)
        self.assertIn('每台电脑位置不同', src)


if __name__ == '__main__':
    unittest.main()
