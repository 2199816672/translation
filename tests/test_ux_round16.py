#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 16 轮：提取改为「按词点选 + 可拖动小窗口 + 独立译文窗口」。

设计动机：OCR 经常漏识别 / 认错字，直接把全部识别结果塞进小窗口既不好
核对也不好复制。现在原图按词显示可点标签，用户点击 / 按住滑动多选，
选中的词才汇总进可编辑、可拖动的小窗口，还能一键在独立窗口里翻译。

同时验证：启动日志里的第三方提示不是错误，且被精确静默而非全局屏蔽。
"""
import importlib.util
import io
import os
import pathlib
import sys
import unittest
import warnings
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _src(name):
    return (ROOT / name).read_text(encoding="utf-8")


class WordSplitTests(unittest.TestCase):
    """词级坐标：真实函数行为，不依赖 PySide6 / OCR 引擎。"""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import text_segment
        self.ts = text_segment

    def test_english_words_get_proportional_boxes(self):
        block = {"text": "Portrait Device Setup", "x": 100, "y": 20,
                 "w": 300, "h": 24}
        words = self.ts.split_line_to_words(block)
        self.assertEqual([w["text"] for w in words],
                         ["Portrait", "Device", "Setup"])
        self.assertAlmostEqual(words[0]["x"], 100.0, places=1)
        # 三个词的宽度之和不超过原行宽，且都在行内
        total = sum(w["w"] for w in words)
        self.assertLessEqual(total, 300.0)
        self.assertLessEqual(words[-1]["x"] + words[-1]["w"], 400.0)
        self.assertTrue(all(w["h"] == 24 for w in words))

    def test_cjk_run_is_split_into_small_chips(self):
        block = {"text": "这是一个非常好用的翻译工具", "x": 0, "y": 0,
                 "w": 280, "h": 24}
        words = self.ts.split_line_to_words(block)
        self.assertGreaterEqual(len(words), 5)
        self.assertTrue(all(len(w["text"]) <= 3 for w in words))
        self.assertGreater(words[1]["x"], words[0]["x"])

    def test_punctuation_and_spaces_do_not_become_chips(self):
        block = {"text": "OK, 保存 / Cancel!", "x": 0, "y": 0,
                 "w": 200, "h": 20}
        words = self.ts.split_line_to_words(block)
        self.assertEqual([w["text"] for w in words],
                         ["OK", "保存", "Cancel"])

    def test_normalized_coordinates_also_work(self):
        """词坐标会先被 normalize_groups 转成 0~1，分摊必须仍然成立。"""
        block = {"text": "Hello World", "x": 0.1, "y": 0.2,
                 "w": 0.4, "h": 0.05}
        words = self.ts.split_line_to_words(block)
        self.assertEqual(len(words), 2)
        self.assertTrue(all(0 < w["x"] < 1 and 0 < w["w"] < 1
                            for w in words))

    def test_blocks_to_words_assigns_visual_rows(self):
        blocks = [
            {"text": "Hello World", "x": 0, "y": 0, "w": 100, "h": 20},
            {"text": "Second Line", "x": 0, "y": 40, "w": 100, "h": 20},
        ]
        words = self.ts.blocks_to_words(blocks)
        self.assertEqual(len(words), 4)
        rows = {w["row"] for w in words}
        self.assertEqual(rows, {0, 1})

    def test_words_to_text_respects_selection_and_line_order(self):
        words = [
            {"text": "Hello", "x": 0, "y": 0, "w": 10, "h": 10, "row": 0},
            {"text": "World", "x": 20, "y": 0, "w": 10, "h": 10, "row": 0},
            {"text": "第二行", "x": 0, "y": 30, "w": 10, "h": 10, "row": 1},
        ]
        self.assertEqual(self.ts.words_to_text(words), "Hello World\n第二行")
        self.assertEqual(self.ts.words_to_text(words, {1, 2}),
                         "World\n第二行")
        self.assertEqual(self.ts.words_to_text(words, set()), "")

    def test_cjk_adjacent_words_join_without_space(self):
        words = [
            {"text": "这是", "x": 0, "y": 0, "w": 10, "h": 10, "row": 0},
            {"text": "一个", "x": 20, "y": 0, "w": 10, "h": 10, "row": 0},
        ]
        self.assertEqual(self.ts.words_to_text(words), "这是一个")


class OCRWordAPITests(unittest.TestCase):
    """引擎侧：一次识别同时给行文本与词坐标，绝不重复跑 OCR。"""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import ocr_recognizer
        self.ocr = ocr_recognizer

    def test_both_engines_expose_recognize_words(self):
        src = _src("ocr_recognizer.py")
        self.assertIn("def recognize_words(self, image_path, blocks=None):",
                      src)
        self.assertEqual(src.count("def recognize_words("), 2)

    def test_windows_reuses_native_word_boxes(self):
        inst = self.ocr.WindowsOCRRecognizer.__new__(
            self.ocr.WindowsOCRRecognizer)
        blocks = [{
            "text": "Hello world", "x": 0, "y": 0, "w": 100, "h": 20,
            "words": [
                {"text": "Hello", "x": 0, "y": 0, "w": 40, "h": 20},
                {"text": "world", "x": 45, "y": 0, "w": 45, "h": 20},
            ],
        }, {
            "text": "无词坐标", "x": 0, "y": 30, "w": 100, "h": 20,
            "words": [],
        }]
        out = inst.recognize_words("not-used.png", blocks=blocks)
        self.assertEqual([w["text"] for w in out],
                         ["Hello", "world", "无词坐标"])
        self.assertEqual(out[0]["w"], 40)

    def test_easy_reuses_blocks_without_second_readtext(self):
        inst = self.ocr.EasyOCRRecognizer.__new__(self.ocr.EasyOCRRecognizer)
        with mock.patch.object(inst, "recognize_blocks",
                               side_effect=AssertionError("不应重复识别")):
            out = inst.recognize_words(
                "not-used.png",
                blocks=[{"text": "Hi", "x": 0, "y": 0, "w": 10, "h": 10}])
        self.assertEqual(len(out), 1)

    def test_windows_blocks_carry_native_words(self):
        src = _src("ocr_recognizer.py")
        # 一次识别归一化成行 + 原生词坐标，三个对外接口共用这份结果
        self.assertIn('"words": self._word_blocks(words)', src)
        self.assertIn("def _scan_lines(self, image_path):", src)
        self.assertIn("def _lines(self, image_path):", src)
        self.assertIn("_cached_scan(", src)

    def test_windows_one_scan_serves_text_blocks_and_words(self):
        """同一张图依次要文本 / 行块 / 词块，只跑一次 winrt 识别。"""
        import tempfile
        from pathlib import Path
        import result_cache

        lines = [{
            "text": "Hello world",
            "words": [
                {"text": "Hello", "x": 0.0, "y": 0.0, "w": 40.0, "h": 20.0,
                 "x2": 40.0, "y2": 20.0},
                {"text": "world", "x": 45.0, "y": 0.0, "w": 45.0, "h": 20.0,
                 "x2": 90.0, "y2": 20.0},
            ],
        }]
        inst = self.ocr.WindowsOCRRecognizer.__new__(
            self.ocr.WindowsOCRRecognizer)
        inst.languages = ["en"]
        calls = []

        def fake_scan(path):
            calls.append(path)
            return lines

        inst._scan_lines = fake_scan
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "frame.png"
            img.write_bytes(b"fake-image-content")
            with mock.patch.object(result_cache, "cache_dir",
                                   return_value=Path(tmp)):
                result_cache.reset_for_tests()
                self.addCleanup(result_cache.reset_for_tests)
                text = inst.recognize(str(img))
                blocks = inst.recognize_blocks(str(img))
                words = inst.recognize_words(str(img))
        self.assertEqual(len(calls), 1, "同一张图不应重复跑 OCR")
        self.assertEqual(text, "Hello world")
        self.assertEqual(blocks[0]["text"], "Hello world")
        self.assertEqual([w["text"] for w in blocks[0]["words"]],
                         ["Hello", "world"])
        self.assertEqual([w["text"] for w in words], ["Hello", "world"])
        self.assertNotIn("x2", words[0], "内部字段不应泄漏到词块")


class MainWindowWiringTests(unittest.TestCase):
    def test_extract_path_builds_words_from_same_ocr_pass(self):
        main = _src("main_window.py")
        self.assertIn("recognize_words(", main)
        self.assertIn("image_path, blocks=blocks", main)
        self.assertIn("blocks_to_words(raw_words or blocks)", main)
        self.assertIn("self._sig_ocr_words.emit(words)", main)
        self.assertIn("self._overlay_words = words or []", main)
        self.assertIn("self._overlay.show_ocr_result(", main)
        self.assertIn("self._overlay_words)", main)

    def test_extract_translate_signal_is_connected(self):
        main = _src("main_window.py")
        self.assertIn("extractTranslateRequested.connect(", main)
        self.assertIn("def _on_overlay_extract_translate(self, text):", main)
        self.assertIn("_sig_overlay_extract_trans_done.connect(", main)
        self.assertIn("def _on_overlay_extract_trans_done(", main)

    def test_extract_translation_never_fakes_translation(self):
        main = _src("main_window.py")
        body = main.split("def _on_overlay_extract_translate", 1)[1].split(
            "def _on_overlay_extract_trans_done", 1)[0]
        self.assertIn("translate_paragraph(text)", body)
        self.assertIn('emit("", f"翻译失败：{e}")', body)
        self.assertNotIn("emit(text,", body)


class OverlayInteractionContracts(unittest.TestCase):
    def test_word_chip_drawing_and_hit_test(self):
        overlay = _src("screenshot_overlay.py")
        for token in ("def _word_rect(self, word):",
                      "def _word_at(self, pos):",
                      "def _word_rows(self):",
                      "def _draw_word_chips(self, p):",
                      "def _apply_word_selection(self, index, select):",
                      "def _selected_words_text(self):",
                      "assign_word_rows(",
                      "p.setClipRect(self._crop_rect)"):
            self.assertIn(token, overlay, token)
        # 旧版每个词一个白底黑字实心标签，文字还会被省略号截断，密集
        # 文本下满屏白块；第 18 轮改成轻量高亮后不再写字 / 不铺白底。
        body = overlay.split("def _draw_word_chips(self, p):", 1)[1].split(
            "\n    def ", 1)[0]
        self.assertNotIn("ElideRight", body)
        self.assertNotIn("drawText", body)
        self.assertNotIn("255, 255, 255", body)

    def test_click_and_drag_multi_select(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn("self._word_drag_active = True", overlay)
        self.assertIn("self._word_drag_select = idx not in self._word_selected",
                      overlay)
        self.assertIn("self._apply_word_selection(idx, self._word_drag_select)",
                      overlay)
        self.assertIn("if self._word_drag_active:", overlay)

    def test_popups_are_draggable(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn("class _DragHandle(QFrame):", overlay)
        self.assertIn(
            "def _make_popup_header(self, popup, title, on_close, badge=None):",
            overlay)
        self.assertIn("self._popup.move(nx, ny)", overlay)
        self.assertIn("WA_TransparentForMouseEvents", overlay)
        self.assertIn("def _place_popup(self, popup, w, h, prefer_below=False):",
                      overlay)

    def test_extract_window_is_editable_and_has_selection_controls(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn("self._extract_text_edit.setReadOnly(False)", overlay)
        self.assertIn('"全选", self._on_extract_select_all', overlay)
        self.assertIn('"清空", self._on_extract_clear', overlay)
        self.assertIn('"翻译", self._on_extract_translate', overlay)
        self.assertIn("def _on_extract_text_changed(self):", overlay)
        self.assertIn("self._word_user_edited = True", overlay)
        self.assertIn("def _update_extract_hint(self):", overlay)

    def test_separate_translation_window(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn("extractTranslateRequested = Signal(str)", overlay)
        self.assertIn("def _create_trans_popup(self):", overlay)
        self.assertIn("def show_extract_translation(self, text, error=\"\"):",
                      overlay)
        self.assertIn("def _hide_trans_popup(self):", overlay)
        self.assertIn("正在翻译…", overlay)
        self.assertIn("self.extractTranslateRequested.emit(text)", overlay)

    def test_no_word_coordinates_falls_back_to_full_text(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn('"" if self._word_blocks else text', overlay)
        self.assertIn("self._draw_extract_highlights(p)", overlay)
        self.assertIn("当前引擎不支持按词点选", overlay)

    def test_click_outside_popup_no_longer_closes_it(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn("if self._mode == \"extract\" and self._popup_contains(pos):",
                      overlay)
        self.assertIn("退出统一走 × / Esc / 右键", overlay)
        self.assertNotIn("self._on_extract_popup_close()\n            return",
                         overlay)

    def test_selection_state_is_cleared_on_exit(self):
        overlay = _src("screenshot_overlay.py")
        self.assertGreaterEqual(
            overlay.count("self._clear_word_selection()"), 3)
        self.assertIn("def _clear_word_selection(self):", overlay)


class StartupNoiseTests(unittest.TestCase):
    """这些日志都不是报错；过滤必须精确，不能全局屏蔽 warnings。"""

    def test_torch_warnings_are_filtered_by_exact_message(self):
        src = _src("ocr_recognizer.py")
        self.assertIn("torch\\.quantize_per_tensor.*deprecated", src)
        self.assertIn("'pin_memory' argument is set as true", src)
        self.assertIn("category=UserWarning", src)
        self.assertIn("TT_SHOW_LIB_NOTICES", src)
        self.assertNotIn('warnings.simplefilter("ignore")', src)

    def test_only_those_two_warnings_are_silenced(self):
        sys.path.insert(0, str(ROOT))
        import ocr_recognizer as ocr
        with mock.patch.dict(os.environ, {"TT_SHOW_LIB_NOTICES": ""}):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                ocr._silence_third_party_warnings()
                warnings.warn(
                    "torch.quantize_per_tensor and others are deprecated "
                    "and will be removed", UserWarning)
                warnings.warn(
                    "'pin_memory' argument is set as true but no accelerator "
                    "is found, then device pinned memory won't be used.",
                    UserWarning)
                warnings.warn("真正的错误要照常显示", UserWarning)
        self.assertEqual(len(caught), 1)
        self.assertIn("真正的错误", str(caught[0].message))

    def test_env_switch_keeps_notices(self):
        sys.path.insert(0, str(ROOT))
        import ocr_recognizer as ocr
        with mock.patch.dict(os.environ, {"TT_SHOW_LIB_NOTICES": "1"}):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                ocr._silence_third_party_warnings()
                warnings.warn(
                    "torch.quantize_per_tensor and others are deprecated",
                    UserWarning)
        self.assertEqual(len(caught), 1)

    def test_qfluent_ad_is_filtered_and_other_output_kept(self):
        spec = importlib.util.spec_from_file_location(
            "_tt_main_entry", str(ROOT / "main.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        buf = io.StringIO()
        origin = sys.stdout
        try:
            sys.stdout = buf
            with mock.patch.dict(os.environ, {"TT_SHOW_LIB_NOTICES": ""}):
                module._install_third_party_notice_filter()
            sys.stdout.write("normal line\n")
            # 真实 print(ALERT) 是「正文」+「单独换行」两次 write
            sys.stdout.write(
                "📢 Tips: QFluentWidgets Pro is now released. Click ...")
            sys.stdout.write("\n")
            sys.stdout.write("another line\n")
        finally:
            sys.stdout = origin
        self.assertEqual(buf.getvalue(), "normal line\nanother line\n")

    def test_notice_filter_does_not_swallow_other_blank_lines(self):
        spec = importlib.util.spec_from_file_location(
            "_tt_main_entry2", str(ROOT / "main.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        buf = io.StringIO()
        origin = sys.stdout
        try:
            sys.stdout = buf
            with mock.patch.dict(os.environ, {"TT_SHOW_LIB_NOTICES": ""}):
                module._install_third_party_notice_filter()
            sys.stdout.write("📢 Tips: QFluentWidgets Pro is now released")
            sys.stdout.write("\n")
            sys.stdout.write("\n")   # 用户自己的空行要保留
            sys.stdout.write("kept\n")
        finally:
            sys.stdout = origin
        self.assertEqual(buf.getvalue(), "\nkept\n")


if __name__ == "__main__":
    unittest.main()
