#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 11 轮：Qt 字体噪声、语言行样式、OCR 只内置一种 / 按需下载、
上手检查反馈、诊断实时刷新、说明不写死绝对路径。

纯逻辑直接跑真实行为；UI 侧在零依赖解释器里用源码 / AST 契约守住。
"""
from __future__ import annotations

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import diagnostics
import ocr_models


def _src(filename):
    return (ROOT / filename).read_text(encoding='utf-8')


def _functions(filename):
    return {n.name: n for n in ast.walk(ast.parse(_src(filename)))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


class DiagnosticsRevisionTests(unittest.TestCase):
    """实时刷新靠版本号判断，不能靠 deque 长度（到 200 会饱和）。"""

    def test_record_and_clear_bump_revision(self):
        before = diagnostics.revision()
        diagnostics.record('info', 'round11 探针', source='test')
        after = diagnostics.revision()
        self.assertEqual(after, before + 1)
        diagnostics.clear()
        self.assertEqual(diagnostics.revision(), after + 1)

    def test_revision_survives_deque_overflow(self):
        before = diagnostics.revision()
        for i in range(210):
            diagnostics.record('info', f'填充 {i}', source='test')
        self.assertEqual(diagnostics.revision(), before + 210)


class OcrPackagingTests(unittest.TestCase):
    """安装包只内置 Windows 自带 OCR，其它方案按需下载。"""

    def test_only_windows_engine_is_builtin(self):
        builtin = [i['key'] for i in ocr_models.ENGINE_CATALOG
                   if i['status'] == 'builtin']
        self.assertEqual(builtin, ['windows'])

    def test_easyocr_is_download_not_bundled(self):
        easy = next(i for i in ocr_models.ENGINE_CATALOG if i['key'] == 'easy')
        self.assertEqual(easy['status'], 'download')
        self.assertTrue(easy['url'].startswith('https://'))

    def test_catalog_explains_bundling_and_gives_links(self):
        text = ocr_models.engine_catalog_text()
        for token in ('只内置', '不随包分发', '按需下载', '官方地址',
                      '暂未内置', '获取：'):
            self.assertIn(token, text)

    def test_download_links_are_safe_https(self):
        self.assertTrue(ocr_models.DOWNLOAD_LINKS)
        for label, url in ocr_models.DOWNLOAD_LINKS:
            self.assertTrue(label.strip())
            self.assertTrue(url.startswith('https://'), url)

    def test_packaging_never_collects_models(self):
        src = _src('build_exe.py')
        self.assertNotIn('"--add-data", "models', src)
        self.assertIn('models / screenshots / user_libs', src)
        iss = _src('installer.iss')
        self.assertIn('models\\*', iss)


class NoAbsolutePathHintTests(unittest.TestCase):
    """说明文案不能写死本机目录；动态目录交给按钮。"""

    def test_manual_hint_has_no_absolute_path(self):
        hint = ocr_models.manual_download_hint()
        self.assertNotIn(str(ocr_models.MODEL_ROOT), hint)
        self.assertNotIn(':\\', hint)
        self.assertIn('打开模型文件夹', hint)
        self.assertIn('不写死路径', hint)

    def test_manual_hint_function_does_not_interpolate_root(self):
        body = ast.unparse(_functions('ocr_models.py')['manual_download_hint'])
        self.assertNotIn("detect['root']", body)
        self.assertNotIn("detect.get('root')", body)

    def test_ocr_tooltip_points_to_buttons(self):
        src = _src('pages/settings_page.py')
        self.assertIn('点「打开模型文件夹」查看', src)
        self.assertNotIn('"目录：" + detect[\'root\']', src)

    def test_diagnostic_path_is_marked_as_machine_specific(self):
        self.assertIn('每台电脑可能不同', _src('diagnostics.py'))


class UiSourceContracts(unittest.TestCase):
    """UI 侧零依赖环境用源码契约防回退。"""

    def test_qt_font_warning_filter_is_precise(self):
        src = _src('app.py')
        body = ast.unparse(_functions('app.py')['_install_qt_log_filter'])
        self.assertIn("'QFont::setPointSize' in message", body)
        self.assertIn("'Point size <= 0' in message", body)
        self.assertIn('qInstallMessageHandler', body)
        self.assertIn('_install_qt_log_filter()', src)
        # 只过滤这一条，其它 Qt 日志要原样转发，不能整个吞掉
        self.assertIn('stream.write', body)
        self.assertIn('TT_SHOW_QT_WARNINGS', body)

    def test_language_row_label_and_combo_share_size(self):
        css = _src('theme.py')
        self.assertIn('QLabel#fieldLabel', css)
        self.assertIn('font-size: 13px;', css)
        util = _src('ui_theme.py')
        self.assertIn('def align_lang_row', util)
        self.assertIn('setFixedHeight', util)
        for page in ('pages/home_page.py', 'pages/doc_page.py',
                     'pages/av_page.py'):
            self.assertIn('align_lang_row(', _src(page),
                          f'{page} 的语言选择行也要对齐')

    def test_checklist_button_gives_visible_feedback(self):
        src = _src('pages/settings_page.py')
        self.assertIn('show_feedback=True', src)
        body = ast.unparse(_functions('pages/settings_page.py')[
            '_refresh_checklist'])
        self.assertIn('InfoBar.success', body)
        self.assertIn('最近检测', body)
        self.assertIn('_show_checklist_advice', src)

    def test_checklist_advice_opens_a_message_box(self):
        body = ast.unparse(_functions('pages/settings_page.py')[
            '_show_checklist_advice'])
        self.assertIn('QMessageBox.information', body)

    def test_diagnostics_polls_and_scrolls_to_latest(self):
        src = _src('pages/settings_page.py')
        body = ast.unparse(_functions('pages/settings_page.py')[
            '_build_diagnostics_section'])
        self.assertIn('QTimer', body)
        self.assertIn('setInterval', body)
        self.assertIn('_poll_diagnostics', body)
        self.assertIn('diagnostics.revision()', src)
        setter = ast.unparse(_functions('pages/settings_page.py')[
            '_set_diag_text'])
        self.assertIn('QTextCursor.MoveOperation.End', setter)
        self.assertIn('ensureCursorVisible', setter)
        self.assertIn('bar.setValue(bar.maximum())', setter)
        # 手动刷新也要走同一条滚动路径
        self.assertIn('_set_diag_text(text)', src)


if __name__ == '__main__':
    unittest.main()
