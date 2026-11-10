#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自适应工具栏布局回归：窄窗口不许裁字、不许挤成一坨。

只依赖 PySide6；缺 Qt 时整文件跳过，保证零依赖解释器也能跑全量回归。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QFrame, QLabel, QPushButton,
    )
    from flow_layout import FlowLayout
    HAS_QT = True
except ImportError:  # 只跳过「缺依赖」，真实导入错误必须暴露
    HAS_QT = False

if HAS_QT:
    try:
        app = QApplication.instance() or QApplication([])
        # Windows 上固定中文字体，让「会不会裁字」的度量可复现
        font_file = r'C:\Windows\Fonts\msyh.ttc'
        if os.path.isfile(font_file):
            QFontDatabase.addApplicationFont(font_file)
            app.setFont(QFont('Microsoft YaHei', 10))
    except Exception as exc:  # 平台插件不可用（无显示器且无 offscreen）
        HAS_QT = False
        print(f'skip flow layout UI tests: {exc}', file=sys.stderr)


@unittest.skipUnless(HAS_QT, '需要 PySide6')
class FlowLayoutTests(unittest.TestCase):
    """基础排版：宽屏一行、窄屏换行、控件永远不被压扁。"""

    def _build(self, width, with_stretch=True):
        frame = QFrame()
        lay = FlowLayout(frame, spacing=12, vspacing=10)
        lay.setContentsMargins(18, 14, 18, 14)
        self.buttons = [QPushButton(t) for t in (
            '区域截图', '全屏截图', '实时翻译', '实时字幕',
            '提取文字', '翻译')]
        for b in self.buttons:
            lay.addWidget(b)
        self.label = QLabel('翻译到:')
        self.combo = QComboBox()
        self.combo.addItems(['中文', '英文', '日语'])
        self.combo.setMinimumWidth(100)
        lay.addWidget(self.label)
        lay.addWidget(self.combo)
        if with_stretch:
            lay.addStretch(1)
            self.tail = QPushButton('清空')
            lay.addWidget(self.tail)
        frame.resize(width, 400)
        frame.show()
        app.processEvents()
        return frame, lay

    def _assert_no_clipped_buttons(self, buttons):
        for b in buttons:
            # 按钮一个像素都不许压缩：QSS 下窄 3~4px 中文就会被裁半个字
            self.assertGreaterEqual(
                b.width(), b.sizeHint().width(),
                f'{b.text()} 被压到 {b.width()}（sizeHint {b.sizeHint().width()}）')
            text_w = b.fontMetrics().horizontalAdvance(b.text())
            self.assertGreaterEqual(b.width(), text_w + 16,
                                    f'{b.text()} 文字放不下')

    def test_wide_is_single_row(self):
        frame, lay = self._build(1100)
        self.assertEqual(len({b.y() for b in self.buttons}), 1)
        self.assertLess(lay.heightForWidth(1100), 80)
        self._assert_no_clipped_buttons(self.buttons)
        frame.close()

    def test_narrow_wraps_and_keeps_full_text(self):
        frame, lay = self._build(430)
        self.assertGreaterEqual(len({b.y() for b in self.buttons}), 2)
        self.assertGreater(lay.heightForWidth(430),
                           lay.heightForWidth(1100))
        self._assert_no_clipped_buttons(self.buttons)
        frame.close()

    def test_label_never_separated_from_combo(self):
        # 粘成一组后，标签与下拉框必须始终同一行且紧邻
        for width in (1100, 760, 520, 400, 330):
            frame, _ = self._build(width)
            self.assertLess(abs(self.label.y() - self.combo.y()), 14,
                            f'宽度 {width} 时标签与下拉框分离')
            self.assertGreater(self.combo.x(), self.label.x())
            self.assertLess(
                self.combo.x() - (self.label.x() + self.label.width()), 12)
            frame.close()

    def test_no_overlap_between_items(self):
        frame, lay = self._build(520)
        boxes = []
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item.widget() is None:
                continue
            boxes.append((item.geometry().x(), item.geometry().y(),
                          item.geometry().right(), item.geometry().bottom(),
                          item.widget()))
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                overlapped = (a[0] < b[2] and b[0] < a[2]
                              and a[1] < b[3] and b[1] < a[3])
                self.assertFalse(overlapped, '控件互相重叠')
        frame.close()

    def test_stretch_pushes_tail_right_only_when_there_is_room(self):
        frame, _ = self._build(1100)
        self.assertGreater(self.tail.x(), 900)   # 宽屏：清空仍在最右
        frame.close()
        frame, _ = self._build(430)
        self.assertGreaterEqual(self.tail.x(), 0)  # 窄屏：换行，不越界
        self.assertLess(self.tail.x() + self.tail.width(), 430)
        frame.close()

    def test_group_break_keeps_buttons_together(self):
        """addSpacing(16) 是语义断点：换行时整组走，不把单个按钮落单。"""
        frame = QFrame()
        lay = FlowLayout(frame, spacing=12, vspacing=10)
        lay.setContentsMargins(18, 14, 18, 14)
        first = [QPushButton(t) for t in ('区域截图', '全屏截图',
                                          '实时翻译', '实时字幕')]
        for b in first:
            lay.addWidget(b)
        lay.addSpacing(16)
        second = [QPushButton(t) for t in ('提取文字', '翻译')]
        for b in second:
            lay.addWidget(b)
        frame.resize(460, 400)   # 这个宽度必须换行，才能检验断点位置
        frame.show()
        app.processEvents()
        self.assertEqual(len({b.y() for b in first}), 1)
        self.assertEqual(len({b.y() for b in second}), 1)
        self.assertGreater(min(b.y() for b in second),
                           max(b.y() for b in first))
        self._assert_no_clipped_buttons(first + second)
        frame.close()

    def test_layout_interface(self):
        frame, lay = self._build(900)
        self.assertEqual(lay.count(), 10 if hasattr(self, 'tail') else 8)
        self.assertTrue(lay.hasHeightForWidth())
        self.assertIsNotNone(lay.itemAt(0))
        taken = lay.takeAt(0)
        self.assertIsNotNone(taken)
        self.assertEqual(lay.count(), (10 if hasattr(self, 'tail') else 8) - 1)
        frame.close()


@unittest.skipUnless(HAS_QT, '需要 PySide6')
class HomePageLayoutTests(unittest.TestCase):
    """真实主页：模拟「导航收起 / 展开」两种内容区宽度。"""

    def setUp(self):
        from theme import build_stylesheet
        from pages.home_page import HomePage
        app.setStyleSheet(build_stylesheet())
        self.page = HomePage()
        self.page.show()
        app.processEvents()
        self.buttons = [
            self.page.screenshot_btn, self.page.fullscreen_btn,
            self.page.live_btn, self.page.subtitle_btn,
            self.page.extract_btn, self.page.translate_btn,
        ]
        self.card = self.page.screenshot_btn.parentWidget()

    def tearDown(self):
        self.page.close()

    def _resize(self, width):
        self.page.resize(width, 810)
        app.processEvents()

    def _rows(self):
        return len({b.y() for b in self.buttons})

    def test_normal_window_stays_one_row(self):
        # 1016 窗口 - 折叠导航 48 ≈ 968 内容区
        self._resize(968)
        self.assertEqual(self._rows(), 1)
        self.assertLess(self.card.height(), 80)
        for b in self.buttons:
            self.assertGreaterEqual(b.width(), b.sizeHint().width())
        # 「自动检测」四个字必须完整，不能被 QComboBox elide 成「自动」
        src = self.page.src_lang_combo
        need = src.fontMetrics().horizontalAdvance(src.currentText()) + 44
        self.assertGreaterEqual(src.width(), need)

    def test_expanded_nav_wraps_without_clipping(self):
        # 1016 窗口 - 展开导航 296 ≈ 720 内容区（用户截图里的挤压场景）
        self._resize(720)
        self.assertGreaterEqual(self._rows(), 2)
        self.assertGreater(self.card.height(), 80)
        for b in self.buttons:
            self.assertGreaterEqual(b.width(), b.sizeHint().width(),
                                    f'{b.text()} 仍被裁字')
        # 「从:」「翻译到:」与各自下拉框不能分离（换行时同进同退）
        labels = {l.text(): l for l in self.page.findChildren(QLabel)}
        for text, combo in (('从:', self.page.src_lang_combo),
                            ('翻译到:', self.page.lang_combo)):
            label = labels.get(text)
            self.assertIsNotNone(label, f'缺少标签 {text}')
            self.assertLess(abs(label.y() - combo.y()), 14,
                            f'{text} 与下拉框被拆到不同行')
            self.assertGreater(combo.x(), label.x())
            need = combo.fontMetrics().horizontalAdvance(
                combo.currentText()) + 44
            self.assertGreaterEqual(combo.width(), need,
                                    f'{text} 的下拉框文字放不下')

    def test_very_narrow_still_usable(self):
        for width in (640, 540, 460):
            self._resize(width)
            self.assertGreaterEqual(self._rows(), 2)
            for b in self.buttons:
                self.assertGreaterEqual(b.width(),
                                        b.sizeHint().width())
            self.assertGreaterEqual(self.page.clear_btn.width(), 40)


@unittest.skipUnless(HAS_QT, '需要 PySide6')
class OtherPagesSmokeTests(unittest.TestCase):
    """文档页 / 音视频页换成流式布局后仍可正常构造与缩放。"""

    def test_pages_resize(self):
        from pages.doc_page import DocPage
        from pages.av_page import AVPage
        for cls in (DocPage, AVPage):
            page = cls()
            page.show()
            for width in (1016, 720, 640):
                page.resize(width, 810)
                app.processEvents()
            self.assertGreater(page.width(), 0)
            page.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
