#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 10 轮收尾复查：任务登记泄漏、健康卡误判、模型导入谎报、截图异常兜底。

本机与 CI 可能没有 PySide6，纯逻辑模块直接跑真实行为；UI 侧用 AST 源码
契约守住「别被回退」。
"""
from __future__ import annotations

import ast
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app_paths
import diagnostics
import ocr_models


def _tree(filename):
    return ast.parse((ROOT / filename).read_text(encoding='utf-8'))


def _functions(filename):
    return {n.name: n for n in ast.walk(_tree(filename))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


class BatchRejectTests(unittest.TestCase):
    """停止后按 Esc 关闭批量窗口，不能留下幽灵任务登记。"""

    def test_reject_clears_task_outside_close_branch(self):
        fn = _functions('batch_dialog.py')['reject']
        # 只看函数体顶层的表达式：写在 if 里的 closeEvent 分支不算数
        top = [ast.unparse(n) for n in fn.body if isinstance(n, ast.Expr)]
        self.assertTrue(
            any("TASK_CENTER.finish('batch')" in s for s in top),
            'reject() 的 super().reject() 分支必须兜底清任务登记')
        self.assertTrue(any('super().reject()' in s for s in top))
        body = ast.unparse(fn)
        self.assertLess(body.index("TASK_CENTER.finish('batch')"),
                        body.index('super().reject()'),
                        '先清登记再关闭窗口')


class HealthIssueTests(unittest.TestCase):
    """「程序目录不可写」横幅要按真实降级结果判断，而不是 os.access。"""

    def _issues(self, frozen, data_dir):
        with mock.patch.object(app_paths, 'is_frozen', return_value=frozen), \
             mock.patch.object(app_paths, 'app_dir', return_value=data_dir):
            return diagnostics.health_issues()

    def test_degraded_data_dir_shows_banner(self):
        with tempfile.TemporaryDirectory() as d:
            issues = self._issues(True, pathlib.Path(d))
        self.assertIn('readonly', [i['kind'] for i in issues])

    def test_same_dir_no_false_banner(self):
        exe_dir = pathlib.Path(sys.executable).resolve().parent
        issues = self._issues(True, exe_dir)
        self.assertNotIn('readonly', [i['kind'] for i in issues])

    def test_source_run_never_shows_banner(self):
        with tempfile.TemporaryDirectory() as d:
            issues = self._issues(False, pathlib.Path(d))
        self.assertNotIn('readonly', [i['kind'] for i in issues])

    def test_no_unreliable_os_access(self):
        # 只看函数体 AST：说明注释里提到这个 API 是允许的
        body = ast.unparse(_functions('diagnostics.py')['health_issues'])
        self.assertNotIn('os.access', body,
                         'Windows 下 os.access(W_OK) 对目录几乎恒为 True')


class OcrImportTests(unittest.TestCase):
    """别把「文件本来就在这里」谎报成导入完成。"""

    def test_existing_single_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            model = root / 'zh_sim_g2.pth'
            model.write_bytes(b'abc')
            with self.assertRaises(RuntimeError) as cm:
                ocr_models.import_local_model(str(model), root=str(root))
            self.assertIn('已经在模型目录里', str(cm.exception))

    def test_model_root_itself_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / 'zh_sim_g2.pth').write_bytes(b'abc')
            with self.assertRaises(RuntimeError):
                ocr_models.import_local_model(str(root), root=str(root))

    def test_real_import_from_download_folder(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d) / 'models'
            src = pathlib.Path(d) / 'download'
            root.mkdir()
            src.mkdir()
            (src / 'zh_sim_g2.pth').write_bytes(b'abc')
            count, names = ocr_models.import_local_model(
                str(src), root=str(root))
            self.assertEqual(names, ['zh_sim_g2.pth'])
            self.assertEqual(count, 1)
            self.assertEqual((root / 'zh_sim_g2.pth').read_bytes(), b'abc')


class CaptureAbortTests(unittest.TestCase):
    """抓屏链路任何一环抛异常，主窗口都必须能被找回来。"""

    def setUp(self):
        self.funcs = _functions('main_window.py')

    def test_abort_helper_restores_window_and_notifies(self):
        body = ast.unparse(self.funcs['_abort_capture'])
        for token in ('_restore_capture_opacity()', 'showNormal()',
                      '_tray_notify', '_capture_pending = False',
                      '_live_pending = False'):
            self.assertIn(token, body)

    def test_all_capture_entrypoints_have_except_fallback(self):
        for name in ('_do_screenshot', '_do_live_select', '_do_fullscreen'):
            handlers = [n for n in ast.walk(self.funcs[name])
                        if isinstance(n, ast.ExceptHandler)]
            self.assertTrue(handlers, f'{name} 缺少异常兜底')
            self.assertTrue(
                any('_abort_capture' in ast.unparse(h) for h in handlers),
                f'{name} 异常时没有调用 _abort_capture')


if __name__ == '__main__':
    unittest.main()
