#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 18 轮：截图「提取」按词点选的视觉降噪 / 小窗口美化回归。

旧版每个识别词画一个白底黑字实心标签：控制台这类密集文本动辄 80+ 个
白块，互相重叠、词被省略号截断成 "Pyth…"，原图完全被盖住。这一轮把
标记改成「行带 + 词下划线 + 合并选中高亮 + 悬停描边」，并把识别文字 /
译文小窗口的按钮收成一行。交互（逐词点选、按住拖选、可编辑、可拖动）
必须原样保留。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from text_segment import assign_word_rows, words_to_text  # noqa: E402


def _src(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _method(src, signature):
    """取出方法体（到下一个方法定义为止），便于精确断言局部实现。

    _DragHandle 和 ScreenshotOverlay 都有 mouseMoveEvent，取最后一次出现
    才是覆盖层的方法；其它方法只出现一次，结果一致。
    """
    return src.split(signature)[-1].split("\n    def ", 1)[0]


class WordRowAssignment(unittest.TestCase):
    """Windows OCR 原生 words 没有 row，补齐后才能按行画带 / 按行汇总文字。"""

    def test_empty(self):
        self.assertEqual(assign_word_rows([]), [])

    def test_two_rows_and_unordered_input(self):
        words = [
            {"text": "World", "x": 60, "y": 0, "w": 40, "h": 14},
            {"text": "Hello", "x": 0, "y": 0, "w": 40, "h": 14},
            {"text": "第二行", "x": 0, "y": 40, "w": 40, "h": 14},
        ]
        out = assign_word_rows(words)
        self.assertIs(out, words)
        self.assertEqual([w["row"] for w in out], [0, 0, 1])
        self.assertEqual(words_to_text(out), "Hello World\n第二行")
        self.assertEqual(words_to_text(out, {0, 2}), "World\n第二行")

    def test_slight_baseline_jitter_stays_same_row(self):
        words = [
            {"text": "A", "x": 0, "y": 0, "w": 10, "h": 20},
            {"text": "B", "x": 20, "y": 3, "w": 10, "h": 20},
            {"text": "C", "x": 0, "y": 40, "w": 10, "h": 20},
        ]
        assign_word_rows(words)
        self.assertEqual([w["row"] for w in words], [0, 0, 1])
        self.assertEqual(words_to_text(words), "A B\nC")

    def test_normalized_coordinates(self):
        words = [
            {"text": "上", "x": 0.1, "y": 0.1, "w": 0.05, "h": 0.02},
            {"text": "右", "x": 0.2, "y": 0.1, "w": 0.05, "h": 0.02},
            {"text": "下", "x": 0.1, "y": 0.2, "w": 0.05, "h": 0.02},
        ]
        assign_word_rows(words)
        self.assertEqual([w["row"] for w in words], [0, 0, 1])
        self.assertEqual(words_to_text(words), "上右\n下")


class WordHighlightVisualContracts(unittest.TestCase):
    def setUp(self):
        self.src = _src("screenshot_overlay.py")
        self.draw = _method(self.src, "def _draw_word_chips(self, p):")

    def test_no_solid_chips_or_truncated_text(self):
        for token in ("ElideRight", "drawText", "fm.elidedText",
                      "255, 255, 255", "#1f2937", "QFont(\"Microsoft YaHei\")"):
            self.assertNotIn(token, self.draw, token)

    def test_row_band_underline_merged_selection_and_hover(self):
        for token in ("def _word_rows(self):", "assign_word_rows("):
            self.assertIn(token, self.src, token)
        for token in ("p.setClipRect(self._crop_rect)",
                      "p.drawRoundedRect(band, 4, 4)",
                      "line_alpha", "runs.append(run)",
                      "QColor(59, 130, 246, 110)",
                      "self._word_hover", "p.restore()"):
            self.assertIn(token, self.draw, token)

    def test_drawing_is_clipped_inside_selection(self):
        self.assertIn("p.setClipRect(self._crop_rect)", self.draw)
        highlights = _method(self.src, "def _draw_extract_highlights(self, p):")
        self.assertIn("p.setClipRect(cr)", highlights)
        self.assertIn("rect.intersected(cr)", highlights)

    def test_hit_test_and_drag_select_unchanged(self):
        for token in ("def _word_rect(self, word):", "def _word_at(self, pos):",
                      "def _apply_word_selection(self, index, select):",
                      "self._word_drag_active = True",
                      "self._word_drag_select = idx not in self._word_selected",
                      "self._apply_word_selection(idx, self._word_drag_select)",
                      "words_to_text(self._word_blocks, self._word_selected)"):
            self.assertIn(token, self.src, token)

    def test_hover_updates_on_mouse_move(self):
        body = _method(self.src, "def mouseMoveEvent(self, event):")
        self.assertIn("self._word_hover = idx", body)
        self.assertIn("PointingHandCursor", body)
        self.assertIn("not over_popup", body)

    def test_extract_frame_matches_highlight_palette(self):
        self.assertIn(
            'QColor(96, 165, 250, 165) if self._mode == "extract"', self.src)


class ExtractPopupVisualContracts(unittest.TestCase):
    def setUp(self):
        self.src = _src("screenshot_overlay.py")
        self.body = _method(self.src, "def _create_extract_popup(self):")

    def test_buttons_collected_into_one_row(self):
        self.assertEqual(self.body.count("QHBoxLayout()"), 1)
        self.assertNotIn("row1", self.body)
        self.assertNotIn("row2", self.body)
        for name in ("全选", "清空", "复制", "保存", "翻译"):
            self.assertIn(f'"{name}"', self.body, name)
        self.assertIn('setObjectName("popupPrimary")', self.body)

    def test_badge_shows_selection_progress(self):
        self.assertIn('setObjectName("popupBadge")', self.body)
        self.assertIn("badge=self._extract_badge", self.body)
        self.assertIn(
            "def _make_popup_header(self, popup, title, on_close, badge=None)",
            self.src)
        hint = _method(self.src, "def _update_extract_hint(self):")
        self.assertIn("_extract_badge.setText", hint)
        self.assertIn("{picked} / {total} 词", hint)

    def test_hint_and_placeholder_match_new_visual(self):
        self.assertIn("带下划线的词", self.body)
        hint = _method(self.src, "def _update_extract_hint(self):")
        self.assertIn("带下划线的词", hint)

    def test_styles_are_soft_and_consistent(self):
        style = _method(self.src, "def _popup_style(self, name):")
        for token in ("QPushButton#popupPrimary", "QLabel#popupBadge",
                      "border-radius: 12px", "padding: 8px 10px",
                      "Palette.TEXT_MUTED", "Palette.PRIMARY"):
            self.assertIn(token, style, token)
        # 提示文字不再用高饱和蓝色，整窗保持中性灰
        self.assertNotIn("Palette.ACCENT", style)

    def test_translation_popup_also_compact(self):
        body = _method(self.src, "def _create_trans_popup(self):")
        self.assertIn('setObjectName("popupPrimary")', body)
        self.assertIn("setPlaceholderText", body)
        self.assertIn("380, 232", self.src)
        self.assertIn("380, 278", self.src)


if __name__ == "__main__":
    unittest.main()
