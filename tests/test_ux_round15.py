"""第 15 轮：吸附范围收敛 / 工具栏精简 / 点击切换原文 / 提取位置高亮。

用户反馈的四点：
1. 窗口吸附开关只放设置页，小菜单恢复一行（原第二行太丑）；
2. 吸附只认当前屏幕上真实可见的窗口，被挡住的后台窗口不参与；
3. 翻译后点击选区默认切到原文、再点回译文；原文直接去掉翻译覆盖，
   不再展示 OCR 提取文字；
4. 提取文字搬到「提取」：原图标出哪里提取了 + 已有的识别文字小窗口。
"""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _src(name):
    return (ROOT / name).read_text(encoding="utf-8")


class SnapScopeTests(unittest.TestCase):
    """纯计算函数不依赖 Win32 / PySide6，跨平台可验证。"""

    @classmethod
    def setUpClass(cls):
        import window_snapper as ws
        cls.ws = ws

    def test_clip_keeps_current_screen_window(self):
        self.assertEqual(
            self.ws.clip_to_screen((10, 10, 500, 400), (0, 0, 1920, 1080)),
            (10, 10, 500, 400))

    def test_clip_drops_other_screen_window(self):
        # 另一个显示器上的窗口（中心不在当前屏幕）整体丢弃
        self.assertIsNone(
            self.ws.clip_to_screen((2000, 10, 2500, 400),
                                   (0, 0, 1920, 1080)))

    def test_clip_clamps_partial_window(self):
        self.assertEqual(
            self.ws.clip_to_screen((-100, -50, 300, 400),
                                   (0, 0, 1920, 1080)),
            (0, 0, 300, 400))

    def test_visible_ratio_no_blocker(self):
        self.assertEqual(self.ws.visible_ratio((0, 0, 100, 100), []), 1.0)

    def test_visible_ratio_fully_covered(self):
        self.assertEqual(
            self.ws.visible_ratio((0, 0, 100, 100), [(-10, -10, 200, 200)]),
            0.0)

    def test_visible_ratio_half_covered(self):
        ratio = self.ws.visible_ratio((0, 0, 100, 100), [(50, 0, 150, 100)])
        self.assertGreater(ratio, 0.2)
        self.assertLess(ratio, 0.6)

    def test_fully_covered_window_below_threshold(self):
        ratio = self.ws.visible_ratio((0, 0, 100, 100), [(-10, -10, 200, 200)])
        self.assertLess(ratio, self.ws.MIN_VISIBLE_RATIO)

    def test_enum_accepts_screen_rect_and_filters_occluded(self):
        snapper = _src("window_snapper.py")
        self.assertIn("screen_rect", snapper)
        self.assertIn("clip_to_screen", snapper)
        self.assertIn("visible_ratio", snapper)
        self.assertIn("MIN_VISIBLE_RATIO", snapper)
        # 遮挡剔除必须真的用在枚举结果上，而不是只定义了个函数
        self.assertIn("if visible_ratio(item[:4], above) < MIN_VISIBLE_RATIO",
                      snapper)

    def test_overlay_passes_current_screen_rect(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn("enum_visible_windows(\n                screen_rect=screen_rect)",
                      overlay)
        self.assertIn("int(geo.left() * sx)", overlay)
        self.assertIn("int((geo.top() + geo.height()) * sy)", overlay)


class ToolbarSimplificationTests(unittest.TestCase):
    def test_no_snap_button_in_menu(self):
        overlay = _src("screenshot_overlay.py")
        self.assertNotIn('self._menu_button(\n                "窗口吸附"', overlay)
        self.assertNotIn("snap_btn", overlay)
        self.assertNotIn("_set_snap(b.isChecked())", overlay)

    def test_plain_menu_row_is_single_line(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn("工具栏保持精简的一行", overlay)
        self.assertIn("self._menu_row2.setVisible(False)", overlay)
        # 菜单状态签名不再因吸附开关变化而重建整排按钮
        state = (overlay.split("def _menu_state", 1)[1]
                 .split("return (", 1)[1].split("\n        )", 1)[0])
        self.assertIn("self._mode", state)      # 确认截到的是签名本体
        self.assertNotIn("_snap_enabled", state)

    def test_switch_is_only_in_settings(self):
        settings = _src("pages/settings_page.py")
        self.assertIn("snap_windows_check", settings)
        self.assertIn("开关只在本页设置，截图工具栏保持精简", settings)
        self.assertIn("只吸附当前屏幕上真实可见的窗口", settings)

    def test_readme_documents_scope_and_single_switch(self):
        readme = _src("README.md")
        self.assertIn("开关只在「设置 → 自动操作」里", readme)
        self.assertIn("被其它窗口挡住的后台窗口都不参与", readme)
        self.assertNotIn("工具栏与设置页均可关闭", readme)


class ClickToggleTests(unittest.TestCase):
    def test_original_state_draws_no_overlay(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn(
            'if self._mode == "translate" and self._showing_trans:',
            overlay)
        self.assertIn("切到「原文」直接去掉翻译", overlay)
        # 旧实现会在此分支无条件绘制叠加层
        self.assertNotIn('if self._mode == "translate":\n'
                         '                self._draw_trans_overlay(p)', overlay)

    def test_original_no_longer_draws_ocr_text(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn('key = "trans"', overlay)
        self.assertNotIn('key = "trans" if self._showing_trans else "text"',
                         overlay)
        self.assertIn("paragraphs = self._trans_paragraphs", overlay)
        self.assertNotIn("paragraphs = self._ocr_paragraphs if", overlay)

    def test_click_inside_selection_toggles(self):
        overlay = _src("screenshot_overlay.py")
        body = overlay.split("if self._show_menu:", 1)[1].split(
            "def _toggle_trans", 1)[0]
        self.assertIn('if self._crop_rect and self._crop_rect.contains(pos):',
                      body)
        self.assertIn('if self._mode == "translate":', body)
        self.assertIn("self._toggle_trans()", body)

    def test_toggle_clears_stale_close_hotspot(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn("self._popup_close = QRect()", overlay)
        self.assertIn(
            'if (self._mode == "translate" and self._showing_trans',
            overlay)

    def test_toggle_no_longer_requires_ocr_text(self):
        overlay = _src("screenshot_overlay.py")
        body = overlay.split("def _toggle_trans", 1)[1].split(
            "def _set_edit_cursor", 1)[0]
        self.assertNotIn("if self._ocr_text:", body)
        self.assertIn("self._showing_trans = not self._showing_trans", body)

    def test_menu_button_labels_are_unambiguous(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn(
            'actions[-1] = "原文" if self._showing_trans else "译文"', overlay)
        self.assertIn('elif name in ("翻译", "原文", "译文"):', overlay)
        self.assertIn("也可以直接点击选区内的译文区域", overlay)


class ExtractHighlightTests(unittest.TestCase):
    """第 16 轮把提取改成「按词点选」，旧的淡蓝细框降级为无词坐标时的兜底。"""

    def test_show_ocr_result_accepts_blocks_and_words(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn(
            "def show_ocr_result(self, text, blocks=None, words=None):",
            overlay)
        self.assertIn("self._extract_blocks = [dict(b) for b in (blocks or [])]",
                      overlay)
        # 第 18 轮：Windows OCR 原生词没有 row，入场时统一补齐视觉行号
        self.assertIn("self._word_blocks = assign_word_rows(", overlay)
        self.assertIn("[dict(w) for w in (words or [])]", overlay)
        self.assertIn("from text_segment import assign_word_rows", overlay)

    def test_highlight_is_drawn_in_extract_mode(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn("def _draw_extract_highlights(self, p):", overlay)
        self.assertIn('elif self._mode == "extract" and (', overlay)
        self.assertIn("self._draw_extract_highlights(p)", overlay)
        self.assertIn("self._draw_word_chips(p)", overlay)
        self.assertIn("p.setBrush(Qt.BrushStyle.NoBrush)", overlay)

    def test_highlight_state_is_cleaned(self):
        overlay = _src("screenshot_overlay.py")
        self.assertGreaterEqual(overlay.count("self._extract_blocks = []"), 3)

    def test_existing_extract_popup_still_present(self):
        overlay = _src("screenshot_overlay.py")
        self.assertIn("def _show_extract_popup(self, text):", overlay)
        self.assertIn("识别文字", overlay)
        # 有词坐标时初始不给全文，等用户点选
        self.assertIn('"" if self._word_blocks else text', overlay)

    def test_main_window_supplies_words_for_extract(self):
        main = _src("main_window.py")
        self.assertIn('target in ("overlay_translate", "overlay_extract")',
                      main)
        self.assertIn("self._overlay.show_ocr_result(", main)
        self.assertIn("self._overlay_words", main)
        self.assertIn("recognize_words", main)

    def test_core_contract_unchanged(self):
        """原文模式只是去掉覆盖，翻译失败仍必须留空、绝不冒充译文。"""
        main = _src("main_window.py")
        self.assertNotIn("results.append(para)", main)
        self.assertIn('results.append("")', main)


if __name__ == "__main__":
    unittest.main()
