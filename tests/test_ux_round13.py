#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 13 轮：翻译覆盖排版 / 小选区 / 窗口吸附 / 免费接口故障切换。

用户实测反馈：译文挤在一个盖住半个窗口的大黑框里；只框一个小词时
翻译不显示；希望像微信截图那样自动吸附窗口；免费接口挂了要自动换。
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# free_translator 顶层只 import requests；CI 若缺这个包，用最小桩代替，
# 故障切换测试全部用假实例，不发起任何真实网络请求。
try:
    import requests  # noqa: F401
except ImportError:  # pragma: no cover
    stub = types.ModuleType("requests")
    stub.exceptions = types.SimpleNamespace(
        ConnectionError=Exception, Timeout=Exception,
        SSLError=Exception, HTTPError=Exception)
    sys.modules.setdefault("requests", stub)

import config
import free_translator as ft
import text_segment
import window_snapper as ws


def _src(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def _settings_rows():
    """模拟设置界面：左右两栏 + 标签/输入框，正是用户截图里的排版。"""
    return [
        {"text": "Verify Configuration", "x": 30, "y": 180, "w": 200, "h": 24},
        {"text": "AVD Name", "x": 30, "y": 230, "w": 80, "h": 18},
        {"text": "Copy_of_Pixel_3a", "x": 120, "y": 230, "w": 300, "h": 20},
        {"text": "AVD Name", "x": 540, "y": 230, "w": 100, "h": 20},
        {"text": "The name of this AVD", "x": 540, "y": 270, "w": 220, "h": 18},
        {"text": "Preferred ABI", "x": 30, "y": 300, "w": 100, "h": 18},
        {"text": "Optional", "x": 140, "y": 300, "w": 60, "h": 18},
    ]


class FineGroupingTests(unittest.TestCase):
    """译文必须逐行贴回，不能把整栏合成一个巨型 bbox。"""

    def test_no_giant_box(self):
        groups = text_segment.group_blocks_fine(_settings_rows())
        self.assertGreaterEqual(len(groups), 5, "一行/一簇至少一组")
        for g in groups:
            self.assertLess(g["h"], 40, "不能跨多行合并成大黑框")
            self.assertLess(g["w"], 420, "不能横跨左右两栏")
            self.assertEqual(g["lines"], 1)

    def test_left_and_right_columns_stay_separate(self):
        groups = text_segment.group_blocks_fine(_settings_rows())
        right = [g for g in groups if g["x"] > 500]
        self.assertTrue(right, "右栏文字应独立成组")
        self.assertTrue(all(g["x"] < 500 or g["x"] > 500
                            for g in groups))
        # 同一视觉行里，左栏输入框与右栏标签之间有大空隙，必须拆开
        self.assertTrue(any(g["x"] < 100 for g in groups))

    def test_same_line_close_words_are_merged(self):
        groups = text_segment.group_blocks_fine(_settings_rows())
        merged = [g for g in groups if "Preferred" in g["text"]]
        self.assertEqual(len(merged), 1)
        self.assertIn("Optional", merged[0]["text"])

    def test_bbox_stays_inside_source(self):
        rows = _settings_rows()
        for g in text_segment.group_blocks_fine(rows):
            self.assertGreaterEqual(g["x"], 30)
            self.assertGreaterEqual(g["y"], 180)
            self.assertLessEqual(g["y"] + g["h"], 320)

    def test_noise_and_empty(self):
        self.assertEqual(text_segment.group_blocks_fine([]), [])
        self.assertEqual(text_segment.group_blocks_fine(
            [{"text": "0  √  ♡", "x": 0, "y": 0, "w": 40, "h": 20}]), [])

    def test_sorted_by_position(self):
        groups = text_segment.group_blocks_fine(_settings_rows())
        keys = [(g["y"], g["x"]) for g in groups]
        self.assertEqual(keys, sorted(keys))

    def test_old_paragraph_grouping_still_available(self):
        # 主页纯文本 OCR 仍走自然段，不要误改 blocks_to_text 的行为
        rows = _settings_rows()
        self.assertIn("Verify Configuration",
                      text_segment.blocks_to_text(rows))
        self.assertTrue(text_segment.group_blocks(rows))


class WindowSnapTests(unittest.TestCase):
    """窗口枚举是 Windows 专用，纯计算函数在任何平台都要可用。"""

    def test_window_at_prefers_innermost(self):
        wins = [(0, 0, 1000, 1000, "desktop"),
                (100, 100, 500, 500, "app"),
                (150, 150, 300, 300, "dialog")]
        self.assertEqual(ws.window_at((200, 200), wins)[4], "dialog")
        self.assertEqual(ws.window_at((600, 600), wins)[4], "desktop")
        self.assertIsNone(ws.window_at((2000, 2000), wins))

    def test_snap_to_nearest_edges(self):
        wins = [(100, 100, 900, 800, "w")]
        self.assertEqual(ws.snap_rect((98, 102, 898, 802), wins),
                         (100, 100, 900, 800))

    def test_outside_threshold_unchanged(self):
        wins = [(100, 100, 900, 800, "w")]
        rect = (50, 50, 850, 750)
        self.assertEqual(ws.snap_rect(rect, wins, 12), rect)

    def test_no_windows_is_noop(self):
        rect = (10, 10, 200, 100)
        self.assertEqual(ws.snap_rect(rect, []), rect)
        self.assertEqual(ws.window_at((5, 5), []), None)

    def test_snap_never_produces_invalid_rect(self):
        wins = [(0, 0, 30, 30, "tiny")]
        rect = (28, 28, 100, 100)
        result = ws.snap_rect(rect, wins, 20)
        self.assertGreaterEqual(result[2] - result[0], 5)
        self.assertGreaterEqual(result[3] - result[1], 5)

    def test_enumeration_shape(self):
        found = ws.enum_visible_windows(exclude_pid=os.getpid())
        self.assertIsInstance(found, list)
        for item in found:
            self.assertEqual(len(item), 5)
            self.assertLess(item[0], item[2])
            self.assertLess(item[1], item[3])


class FreeEngineFailoverTests(unittest.TestCase):
    """免费接口挂掉要自动换，且绝不能拿原文冒充译文。"""

    def setUp(self):
        self.orig = dict(ft._free_instances)
        self.orig_getter = ft.get_free_translator
        ft._free_instances.clear()
        ft.reset_failover()

        # 所有引擎默认离线桩：切换顺序调整后，测试绝不能真的发网络请求
        class Unavailable:
            def translate(self, *a, **kw):
                raise RuntimeError("离线测试桩：不发起网络请求")

        for name in ft.FREE_ENGINE_ORDER:
            ft._free_instances[f"{name}::"] = Unavailable()
        self.addCleanup(self._restore)

    def _restore(self):
        ft._free_instances.clear()
        ft._free_instances.update(self.orig)
        ft.get_free_translator = self.orig_getter
        ft.reset_failover()

    def test_preferred_engine_used_first(self):
        calls = []

        class Good:
            def translate(self, text, from_lang, to_lang):
                calls.append(text)
                return "译文"

        ft._free_instances["bing_free::"] = Good()
        text, engine = ft.translate_with_failover(
            "Hello", "auto", "zh-CN", "bing_free")
        self.assertEqual((text, engine), ("译文", "bing_free"))
        self.assertEqual(calls, ["Hello"])

    def test_switch_when_preferred_fails(self):
        switched = []

        class Bad:
            def translate(self, *a, **kw):
                raise RuntimeError("503")

        class Good:
            def translate(self, *a, **kw):
                return "译文"

        ft._free_instances["bing_free::"] = Bad()
        ft._free_instances["google_gtx_free::"] = Good()
        text, engine = ft.translate_with_failover(
            "Hello", "auto", "zh-CN", "bing_free",
            on_switch=lambda a, b: switched.append((a, b)))
        self.assertEqual((text, engine), ("译文", "google_gtx_free"))
        self.assertEqual(switched, [("bing_free", "google_gtx_free")])
        self.assertEqual(ft.current_free_engine(), "google_gtx_free")

    def test_sticky_prefers_known_good(self):
        ft._failover_state["engine"] = "google_gtx_free"
        ft._failover_state["until"] = 1e18

        class Good:
            def translate(self, *a, **kw):
                return "译文"

        ft._free_instances["google_gtx_free::"] = Good()
        _text, engine = ft.translate_with_failover(
            "Hello", "auto", "zh-CN", "bing_free")
        self.assertEqual(engine, "google_gtx_free")

    def test_all_engines_fail_raises(self):
        class Bad:
            def translate(self, *a, **kw):
                raise RuntimeError("down")

        for name in ft.FREE_ENGINE_ORDER:
            ft._free_instances[f"{name}::"] = Bad()
        with self.assertRaises(RuntimeError) as ctx:
            ft.translate_with_failover(
                "Hello", "auto", "zh-CN", "bing_free")
        self.assertIn("均不可用", str(ctx.exception))

    def test_disabled_only_tries_preferred(self):
        calls = []

        class Bad:
            def translate(self, *a, **kw):
                raise RuntimeError("down")

        class Good:
            def translate(self, *a, **kw):
                calls.append(1)
                return "译文"

        ft._free_instances["bing_free::"] = Bad()
        ft._free_instances["google_gtx_free::"] = Good()
        with self.assertRaises(RuntimeError):
            ft.translate_with_failover(
                "Hello", "auto", "zh-CN", "bing_free", enabled=False)
        self.assertEqual(calls, [], "关闭自动切换后不应尝试其它引擎")

    def test_user_key_never_forwarded_to_backup(self):
        calls = []

        class Bad:
            def translate(self, *a, **kw):
                raise RuntimeError("down")

        class Good:
            def translate(self, *a, **kw):
                return "译文"

        def fake_getter(engine, api_key=None, region=None):
            calls.append((engine, api_key, region))
            return Bad() if engine == "bing_free" else Good()

        ft.get_free_translator = fake_getter
        ft.translate_with_failover(
            "Hello", "auto", "zh-CN", "bing_free", api_key="SECRET")
        self.assertEqual(calls[0], ("bing_free", "SECRET", ""))
        self.assertEqual(calls[1][1], "", "备用引擎不能拿到首选引擎的密钥")

    def test_six_free_engines_stay_in_sync(self):
        self.assertEqual(set(config.FREE_APIS), set(ft.FREE_ENGINE_ORDER))
        self.assertEqual(len(config.FREE_APIS), 6)
        for name in config.FREE_APIS:
            self.assertIn(name, config.FREE_API_KEY_FIELDS)
            self.assertIn(name, config.TRANSLATION_APIS.values())

    def test_reset_clears_sticky(self):
        ft.reset_failover("mymemory_free")
        self.assertEqual(ft.current_free_engine(), "mymemory_free")
        ft.reset_failover()
        self.assertIsNone(ft.current_free_engine())

    def test_reliable_engines_come_first(self):
        # 实测：Google 域名 / LibreTranslate 公共实例 / 有道网页接口当前都
        # 不可用，先撞它们会让自动切换空转几十秒。
        self.assertEqual(ft.FREE_ENGINE_ORDER[:2],
                         ("bing_free", "mymemory_free"))

    def test_disabled_ignores_sticky_engine(self):
        # 旧实现先把粘性备用引擎顶到 pool[0]，关闭自动切换后反而去试备用
        ft.reset_failover("mymemory_free")
        calls = []

        def fake_getter(engine, api_key=None, region=None):
            class Good:
                def translate(self, *a, **kw):
                    calls.append(engine)
                    return "译文"
            return Good()

        ft.get_free_translator = fake_getter
        _text, engine = ft.translate_with_failover(
            "Hello", "auto", "zh-CN", "bing_free", enabled=False)
        self.assertEqual(engine, "bing_free")
        self.assertEqual(calls, ["bing_free"],
                         "关闭自动切换后必须只试用户选中的引擎")

    def test_failed_engine_is_skipped_next_time(self):
        tried = []

        def fake_getter(engine, api_key=None, region=None):
            class T:
                def translate(self, *a, **kw):
                    tried.append(engine)
                    if engine == "google_gtx_free":
                        return "译文"
                    raise RuntimeError("down")
            return T()

        ft.get_free_translator = fake_getter
        _text, engine = ft.translate_with_failover(
            "Hello", "auto", "zh-CN", "bing_free")
        self.assertEqual(engine, "google_gtx_free")
        self.assertIn("bing_free", ft.failed_engines())
        self.assertNotIn("google_gtx_free", ft.failed_engines())

        # 清掉粘性，模拟用户改了设置后的下一次翻译：失败过的备用不再重复撞
        ft._failover_state["engine"] = None
        ft._failover_state["until"] = 0.0
        tried.clear()
        ft.translate_with_failover("Hello", "auto", "zh-CN", "bing_free")
        self.assertEqual(tried, ["bing_free", "google_gtx_free"])

    def test_reset_failover_clears_failed_cache(self):
        ft._failed_until["youdao_free"] = 1e18
        self.assertIn("youdao_free", ft.failed_engines())
        ft.reset_failover()
        self.assertEqual(ft.failed_engines(), set())


class SourceContracts(unittest.TestCase):
    """源码级契约：把上面四条修复的接线锁住，防止以后被改回去。"""

    def test_fine_grouping_is_wired_to_both_overlays(self):
        main = _src("main_window.py")
        live = _src("realtime_translate.py")
        self.assertIn("def group_blocks_fine", _src("text_segment.py"))
        self.assertIn("group_blocks_fine(blocks)", main)
        self.assertIn("group_blocks_fine(blocks)", live)
        self.assertNotIn("group_blocks(blocks)", main)
        self.assertNotIn("group_blocks(blocks)", live)

    def test_no_unified_font_size(self):
        # 统一字号会被一个小标签拖累整屏，且容易产生巨型底框
        self.assertNotIn("fits_all", _src("screenshot_overlay.py"))
        self.assertNotIn("fits_all", _src("realtime_translate.py"))

    def test_small_selection_upscaled_for_ocr(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn("def _save_ocr_image", overlay)
        self.assertIn("_OCR_MIN_W = 240", overlay)
        self.assertGreaterEqual(overlay.count("_save_ocr_image()"), 3,
                                "提取 / 翻译 / 自动翻译三条路径都要走放大图")
        self.assertIn("_save_to_temp()", overlay, "复制/预览仍用原图")
        self.assertIn("self.height() - top", overlay,
                      "小选区译文允许越过选区底部显示")
        self.assertIn("把选区框大一点", _src("main_window.py"))

    def test_narrow_row_expands_to_readable_width(self):
        # 实测：只框住 "Portrait" 时译文虽已画出，但被压在 70px 宽的框里
        # 截成「人像…」，看起来仍像「翻译不显示」。窄行必须向右扩到可读
        # 宽度，同时设上限，别又退回盖住半个窗口的大黑框。
        overlay = _src("screenshot_overlay.py")
        self.assertIn("min_readable_w = 150", overlay)
        self.assertIn("max_box_w = 300", overlay)
        self.assertIn("probe.horizontalAdvance(text)", overlay)
        self.assertIn("p.drawRoundedRect(QRect(left, top, box_w, draw_h)",
                      overlay, "底框宽度要用扩展后的 box_w，不是原文 bbox 的 w")

    def test_never_use_source_as_translation(self):
        main = _src("main_window.py")
        self.assertNotIn("results.append(para)", main)
        self.assertIn('results.append("")', main)
        self.assertIn("已留空", main)

    def test_window_snap_wiring(self):
        overlay = _src("screenshot_overlay.py")
        for token in ("snap_enabled", "_load_snap_windows",
                      "_update_hover", "_draw_hover_window",
                      "_set_snap", "窗口吸附", "snap_rect",
                      "_snap_click_window"):
            self.assertIn(token, overlay, token)
        self.assertIn("snap_windows", _src("main_window.py"))
        self.assertIn("window_snapper", _src("build_exe.py"))
        self.assertIn("snap_windows_check", _src("pages/settings_page.py"))

    def test_failover_wiring(self):
        trans = _src("translator.py")
        for token in ("translate_with_failover", "free_api_failover",
                      "DEFAULT_FREE_API_FAILOVER"):
            self.assertIn(token, trans, token)
        free = _src("free_translator.py")
        for token in ("GoogleGTXFreeTranslator",
                      "LibreTranslateFreeTranslator",
                      "YoudaoFreeTranslator", "FREE_ENGINE_ORDER",
                      "def translate_with_failover", "FAILED_ENGINE_TTL = 300",
                      "def failed_engines", "_record_switch"):
            self.assertIn(token, free, token)
        self.assertEqual(free.count("timeout=5"), 3,
                         "境外 / 公共接口必须快速失败，别让切换链空转")
        settings = _src("pages/settings_page.py")
        self.assertIn("free_api_failover_check", settings)
        self.assertIn("reset_failover()", settings)
        self.assertIn("刚失败的接口 5 分钟内不再重复尝试", settings)


if __name__ == "__main__":
    unittest.main()
