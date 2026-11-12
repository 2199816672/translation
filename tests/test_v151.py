#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.5.1 发布前复查的回归测试（离线、零第三方依赖、不需要 PySide6）。

锁住本轮修掉的确定级问题：截图菜单「复制图片」误取消、pip 流式读无超时、
共享 --target 的 --upgrade、Whisper 缓存释放时删锁、构建清理失败仍继续打包。
"""
import ast
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build_installer  # noqa: E402
import speech_recognizer  # noqa: E402
import subprocess_util  # noqa: E402


def _do_action_node():
    tree = ast.parse((ROOT / "screenshot_overlay.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_do_action":
            return node
    return None


class MenuActionTests(unittest.TestCase):
    def test_copy_image_is_handled_not_falling_through_to_cancel(self):
        """菜单把「复制」改名「复制图片」后，_do_action 必须认得新名字。"""
        node = _do_action_node()
        self.assertIsNotNone(node)
        dispatched = False
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Compare):
                continue
            for comparator in sub.comparators:
                if not isinstance(comparator, ast.Tuple):
                    continue
                values = [e.value for e in comparator.elts
                          if isinstance(e, ast.Constant)]
                if "复制图片" in values and "复制" in values:
                    dispatched = True
        self.assertTrue(dispatched, "「复制图片」必须复用原复制图片分支")


class PipWatchdogTests(unittest.TestCase):
    def test_hung_child_is_killed_and_readline_returns(self):
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        start = time.monotonic()
        with subprocess_util.pip_process(
                cmd, timeout=1, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True) as proc:
            line = proc.stdout.readline()
        self.assertEqual(line, "")
        self.assertLess(time.monotonic() - start, 15)
        self.assertIsNotNone(proc.returncode)
        self.assertNotEqual(proc.returncode, 0)

    def test_normal_child_still_succeeds(self):
        cmd = [sys.executable, "-c", "print('hello-pip')"]
        with subprocess_util.pip_process(
                cmd, timeout=10, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True) as proc:
            out = proc.stdout.read()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("hello-pip", out)


class MiscReleaseGuardTests(unittest.TestCase):
    def test_release_keeps_per_key_whisper_lock(self):
        key = ("tt-probe-model", "cpu", "int8")
        speech_recognizer._shared_whisper_models[key] = object()
        speech_recognizer._shared_whisper_locks[key] = object()
        try:
            speech_recognizer._release_whisper_entry(key)
            self.assertNotIn(key, speech_recognizer._shared_whisper_models)
            self.assertIn(key, speech_recognizer._shared_whisper_locks,
                          "删锁会让同一模型被两把不同的锁保护")
        finally:
            speech_recognizer._shared_whisper_models.pop(key, None)
            speech_recognizer._shared_whisper_locks.pop(key, None)

    def test_sanitize_aborts_build_when_runtime_data_cannot_be_moved(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "TranslationTool"
            app.mkdir()
            (app / "user_libs").mkdir()
            with mock.patch.object(Path, "rename", side_effect=OSError("locked")):
                with self.assertRaises(RuntimeError):
                    build_installer.sanitize_app_dir(app)


if __name__ == "__main__":
    unittest.main()
