#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量转录窗口的导出内容联动测试（离屏运行；未装 PySide6 时自动跳过）。

验证三种导出内容始终由用户选择，勾选「同时翻译」不会强制改成双语。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在任何 Qt 模块被导入之前设置，保证 CI/无显示器环境可跑。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:  # 只把「没装 Qt」当跳过理由
    HAS_QT = False

if HAS_QT:
    # 真实导入错误必须让测试失败，不能被伪装成「环境缺失」而跳过
    import batch_dialog


@unittest.skipUnless(HAS_QT, '未安装 PySide6，跳过 UI 联动测试')
class DialogExportModeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # 隔离用户配置：不读写真实的 user_config.json
        with mock.patch.object(batch_dialog, 'load_user_config',
                               return_value={}), \
                mock.patch.object(batch_dialog, 'save_user_config'):
            self.dlg = batch_dialog.BatchTranscribeDialog(
                {'fmt': 'srt', 'export_mode': 'source', 'summary': ''})
        save_patch = mock.patch.object(batch_dialog, 'save_user_config')
        save_patch.start()
        self.addCleanup(save_patch.stop)
        self.addCleanup(self.dlg.close)

    def _set_mode(self, mode):
        idx = self.dlg.mode_combo.findData(mode)
        self.assertGreaterEqual(idx, 0)
        self.dlg.mode_combo.setCurrentIndex(idx)

    def test_combo_offers_three_modes(self):
        modes = {self.dlg.mode_combo.itemData(i)
                 for i in range(self.dlg.mode_combo.count())}
        self.assertEqual(modes, {'source', 'target', 'both'})

    def test_translate_toggle_never_forces_both(self):
        self._set_mode('source')
        self.dlg.translate_check.setChecked(True)
        self.assertEqual(self.dlg.mode_combo.currentData(), 'source')
        snapshot = self.dlg._snapshot()
        self.assertEqual(snapshot['export_mode'], 'source')
        self.assertFalse(snapshot['translate'])
        self.assertIn('只导出原文', self.dlg.translate_hint.text())

    def test_target_mode_is_kept_and_enables_translation(self):
        self._set_mode('target')
        self.assertEqual(self.dlg.mode_combo.currentData(), 'target')
        self.assertTrue(self.dlg.translate_check.isChecked())
        snapshot = self.dlg._snapshot()
        self.assertEqual(snapshot['export_mode'], 'target')
        self.assertTrue(snapshot['translate'])
        self.assertIn('只导出译文', self.dlg.translate_hint.text())

    def test_both_mode_is_kept(self):
        self._set_mode('both')
        self.assertEqual(self.dlg.mode_combo.currentData(), 'both')
        self.assertTrue(self.dlg._snapshot()['translate'])
        self.assertIn('原文 + 译文', self.dlg.translate_hint.text())

    def test_retry_failed_keeps_completed_rows(self):
        import batch_transcribe as bt
        self.dlg.jobs = [
            bt.Job('a.mp3', status=bt.DONE, output='C:/out/a_字幕.srt'),
            bt.Job('b.mp4', status=bt.FAILED, detail='磁盘满'),
        ]
        self.dlg._refresh_table()
        with mock.patch.object(self.dlg, '_start') as start:
            self.dlg._retry_failed()
        self.assertTrue(start.called)
        self.assertEqual(len(self.dlg.jobs), 2)
        self.assertEqual(self.dlg.jobs[0].output, 'C:/out/a_字幕.srt')
        self.assertEqual(self.dlg.jobs[1].status, bt.PENDING)
        self.assertTrue(self.dlg._retry_mode)

    def test_stop_marks_window_closable(self):
        self.dlg._running = True
        self.dlg._stop()
        self.assertTrue(self.dlg._stopping)
        self.assertTrue(self.dlg._cancel.is_set())

    def test_preflight_blocks_translated_mode_without_translation(self):
        self._set_mode('both')
        self.dlg.translate_check.setChecked(False)
        error = self.dlg._preflight(self.dlg._snapshot())
        self.assertIsNotNone(error)
        self.assertIn('勾选', error)


if __name__ == '__main__':
    unittest.main(verbosity=2)
