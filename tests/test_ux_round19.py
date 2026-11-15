#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.5.3 发布前体检修复的回归测试。

发布前交叉审查发现三处问题，这里全部锁死：
1. `_DragHandle` 用会随弹窗移动的局部坐标算位移 → 半速拖动 / 回跳；
2. `cleanup_old_builds` 会把 `dist/TranslationTool` 里真实用户数据整棵删掉；
3. 免费引擎原样回显原文时，生产翻译路径没有当成失败（译文 = 原文却无报错）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# 真实 PySide6 只在 Python 3.11 环境可用；离屏跑拖动行为测试
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import requests  # noqa: F401
except ImportError:  # pragma: no cover
    stub = types.ModuleType("requests")
    stub.exceptions = types.SimpleNamespace(
        ConnectionError=Exception, Timeout=Exception,
        SSLError=Exception, HTTPError=Exception)
    sys.modules.setdefault("requests", stub)

import build_installer as bi  # noqa: E402
import free_translator as ft  # noqa: E402

try:
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QWidget, QFrame
    from screenshot_overlay import _DragHandle
    HAS_QT = True
except ImportError:  # pragma: no cover - 默认解释器无 PySide6
    HAS_QT = False


def _src(relative):
    return (ROOT / relative).read_text(encoding="utf-8-sig")


class EchoGuardTests(unittest.TestCase):
    """原文回显判定：拦住真失败，别误伤合法同形译文。"""

    def test_echo_detection(self):
        cases = (
            ("Hello world", "Hello world", True),
            ("你好世界", "你好世界", True),
            ("这是一段需要翻译的中文", "这是一段需要翻译的中文", True),
            ("Hello", " Hello ", True),
            ("OK", "OK", False),
            ("A", "A", False),
            ("2024", "2024", False),
            ("!!!", "!!!", False),
            ("", "", False),
            ("Hello", "你好", False),
            ("Hello", "", False),
        )
        for text, result, expected in cases:
            with self.subTest(text=text, result=result):
                self.assertEqual(ft._echoed_source(text, result), expected)


class FailoverEchoTests(unittest.TestCase):
    """生产故障切换：回显原文的引擎必须算失败，绝不把原文交出去。"""

    def setUp(self):
        self._orig_getter = ft.get_free_translator
        ft._failed_until.clear()
        ft._failover_state["engine"] = None
        ft._failover_state["until"] = 0.0
        self.addCleanup(self._restore)
        self.addCleanup(ft._failed_until.clear)

    def _restore(self):
        ft.get_free_translator = self._orig_getter

    def test_echo_switches_to_next_engine(self):
        class Echo:
            def translate(self, text, *a, **kw):
                return text

        class Good:
            def translate(self, *a, **kw):
                return "译文"

        tried = []

        def getter(engine, api_key="", region=""):
            tried.append(engine)
            return Echo() if engine == "bing_free" else Good()

        ft.get_free_translator = getter
        out, engine = ft.translate_with_failover(
            "Hello world", "auto", "zh-CN", "bing_free")
        self.assertEqual(out, "译文")
        self.assertNotEqual(engine, "bing_free")
        self.assertIn("bing_free", ft.failed_engines())
        self.assertNotEqual(tried, ["bing_free"], "回显后必须继续尝试其它引擎")

    def test_all_echo_raises_and_never_returns_source(self):
        class Echo:
            def translate(self, text, *a, **kw):
                return text

        ft.get_free_translator = lambda engine, api_key="", region="": Echo()
        with self.assertRaises(RuntimeError) as ctx:
            ft.translate_with_failover(
                "Hello world", "auto", "zh-CN", "bing_free")
        self.assertIn("均不可用", str(ctx.exception))
        self.assertIn("原文", str(ctx.exception))

    def test_disabled_failover_echo_still_raises(self):
        class Echo:
            def translate(self, text, *a, **kw):
                return text

        ft.get_free_translator = lambda engine, api_key="", region="": Echo()
        with self.assertRaises(RuntimeError):
            ft.translate_with_failover(
                "Hello world", "auto", "zh-CN", "bing_free", enabled=False)


class CleanupLegacyAppTests(unittest.TestCase):
    """便携版目录被直接运行过时，自动清理必须保住里面的用户数据。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dist = Path(self.tmp.name) / "dist"
        self.dist.mkdir()
        self.dist.joinpath("installer").mkdir()

    def _legacy(self):
        app = self.dist / "TranslationTool"
        app.mkdir()
        (app / "TranslationTool.exe").write_bytes(b"e" * 100)
        return app

    def test_plain_program_dir_is_cleaned(self):
        app = self._legacy()
        result = bi.cleanup_old_builds("1.5.3", dist_root=self.dist)
        self.assertFalse(app.exists())
        self.assertTrue(any(x[1] == "TranslationTool" for x in result["deleted"]))

    def test_user_config_blocks_deletion(self):
        app = self._legacy()
        (app / "user_config.json").write_text('{"theme": "dark"}')
        result = bi.cleanup_old_builds("1.5.3", dist_root=self.dist)
        self.assertTrue(app.is_dir())
        self.assertTrue((app / "user_config.json").is_file())
        self.assertTrue(any("用户数据" in e for e in result["errors"]))

    def test_every_runtime_data_name_blocks_deletion(self):
        for name in ("user_config.json.broken", "screenshots", "models",
                     "converted", "user_libs", "cache"):
            with self.subTest(name=name):
                app = self._legacy()
                target = app / name
                if name.startswith("user_config"):
                    target.write_text("x")
                else:
                    target.mkdir()
                bi.cleanup_old_builds("1.5.3", dist_root=self.dist)
                self.assertTrue(app.is_dir(), f"{name} 未能阻止误删")
                app.rename(self.dist / ("done_" + name))


@unittest.skipUnless(HAS_QT, "需要 PySide6（Python 3.11 离屏环境）")
class DragHandleBehaviourTests(unittest.TestCase):
    """真实 QMouseEvent：拖动位移必须跟鼠标一致，且中途不回跳。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _event(kind, global_pos, local_pos, buttons):
        return QMouseEvent(
            kind, QPointF(*local_pos), QPointF(*global_pos),
            Qt.MouseButton.LeftButton, buttons,
            Qt.KeyboardModifier.NoModifier)

    def test_drag_tracks_mouse_without_jumping_back(self):
        parent = QWidget()
        parent.resize(800, 600)
        popup = QFrame(parent)
        popup.setGeometry(100, 100, 300, 200)
        handle = _DragHandle(popup)
        handle.setGeometry(0, 0, 300, 24)

        left = Qt.MouseButton.LeftButton
        no_button = Qt.MouseButton.NoButton
        # 按下：全局 (150,110)，弹窗 (100,100) → 局部 (50,10)
        handle.mousePressEvent(self._event(
            QEvent.Type.MouseButtonPress, (150, 110), (50, 10), left))
        # 第一次移动：弹窗尚未移动 → 局部 (90,40)
        handle.mouseMoveEvent(self._event(
            QEvent.Type.MouseMove, (190, 140), (90, 40), left))
        self.assertEqual((popup.x(), popup.y()), (140, 130))
        # 第二次移动：弹窗已在 (140,130)，局部坐标「漂移」到 (70,30)。
        # 旧实现用局部坐标会算回 (120,120)，这里必须继续跟到 (160,150)。
        handle.mouseMoveEvent(self._event(
            QEvent.Type.MouseMove, (210, 160), (70, 30), left))
        self.assertEqual((popup.x(), popup.y()), (160, 150))
        handle.mouseReleaseEvent(self._event(
            QEvent.Type.MouseButtonRelease, (210, 160), (50, 30), no_button))

    def test_second_drag_rebases_and_clamps_to_parent(self):
        parent = QWidget()
        parent.resize(800, 600)
        popup = QFrame(parent)
        popup.setGeometry(100, 100, 300, 200)
        handle = _DragHandle(popup)
        handle.setGeometry(0, 0, 300, 24)
        left = Qt.MouseButton.LeftButton
        no_button = Qt.MouseButton.NoButton

        handle.mousePressEvent(self._event(
            QEvent.Type.MouseButtonPress, (200, 200), (100, 100), left))
        handle.mouseMoveEvent(self._event(
            QEvent.Type.MouseMove, (230, 220), (130, 120), left))
        self.assertEqual((popup.x(), popup.y()), (130, 120))
        handle.mouseReleaseEvent(self._event(
            QEvent.Type.MouseButtonRelease, (230, 220), (100, 100), no_button))

        # 换一个起点再拖：基准必须重新取，不能被上一次污染
        handle.mousePressEvent(self._event(
            QEvent.Type.MouseButtonPress, (400, 300), (270, 180), left))
        handle.mouseMoveEvent(self._event(
            QEvent.Type.MouseMove, (900, 900), (770, 780), left))
        self.assertEqual((popup.x(), popup.y()), (500, 400),
                         "应 clamp 在父窗口内")
        handle.mouseReleaseEvent(self._event(
            QEvent.Type.MouseButtonRelease, (900, 900), (400, 300), no_button))


class FixContracts(unittest.TestCase):
    """零依赖的源码契约，防止以后被改回去。"""

    def test_drag_handle_uses_global_position(self):
        body = _src("screenshot_overlay.py").split("class _DragHandle", 1)[1]
        body = body.split("\nclass ", 1)[0]
        self.assertIn("event.globalPosition().toPoint()", body)
        self.assertIn("self._press_global", body)
        self.assertNotIn("event.pos() - self._press_pos", body)

    def test_cleanup_checks_legacy_app_for_user_data(self):
        src = _src("build_installer.py")
        self.assertIn("def _dir_has_runtime_data(app_dir: Path) -> bool:", src)
        self.assertIn("if is_legacy_app and _dir_has_runtime_data(entry):", src)

    def test_failover_rejects_echo(self):
        src = _src("free_translator.py")
        self.assertIn("def _echoed_source(text, result):", src)
        self.assertIn("if _echoed_source(text, result):", src)
        self.assertIn('raise RuntimeError("原样返回原文，疑似未翻译")', src)


if __name__ == "__main__":
    unittest.main()
