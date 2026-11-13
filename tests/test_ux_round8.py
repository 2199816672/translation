#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 8 轮 UX 回归：上手路径、可恢复性、可发现性与长任务状态。

本机 / CI 没有 PySide6 与 numpy：UI 侧用源码契约防回潮；能零依赖验证的
链路（音频采集组件按音源选包、停止不是失败、空状态与指引文案）写真实用例。
"""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_live_caption():
    """注入 numpy 替身，加载需要它的 live_caption（零依赖环境）。"""
    sys.modules.pop('live_caption', None)
    with mock.patch.dict(sys.modules, {'numpy': mock.MagicMock()}):
        import live_caption
        return live_caption


class _FakeCtx:
    def __enter__(self):
        return types.SimpleNamespace(
            returncode=0,
            stdout=types.SimpleNamespace(read=lambda: 'ok'))

    def __exit__(self, *args):
        return False


class MicInstallTests(unittest.TestCase):
    """麦克风缺 sounddevice 时，设置页的「安装组件」必须真能装上它。"""

    def _install(self, **kwargs):
        lc = _load_live_caption()
        calls = []

        def fake_pip_cmd(packages, extra_args=()):
            calls.append(list(packages))
            return (['pip', 'install'] + list(packages), '')

        with mock.patch.object(lc, 'pip_cmd', side_effect=fake_pip_cmd), \
             mock.patch.object(lc, 'pip_process', return_value=_FakeCtx()):
            ok, msg = lc.install_loopback_support(**kwargs)
        sys.modules.pop('live_caption', None)
        return ok, msg, calls

    def test_sounddevice_can_be_installed(self):
        ok, msg, calls = self._install(packages=('sounddevice',))
        self.assertTrue(ok)
        self.assertEqual(calls, [['sounddevice']])
        self.assertIn('sounddevice', msg)

    def test_default_still_installs_soundcard(self):
        ok, msg, calls = self._install()
        self.assertTrue(ok)
        self.assertEqual(calls, [['soundcard']])
        self.assertIn('soundcard', msg)

    def test_pre_cancelled_does_not_start_pip(self):
        import threading
        event = threading.Event()
        event.set()
        ok, msg, calls = self._install(cancel=event)
        self.assertFalse(ok)
        self.assertEqual(calls, [])
        self.assertIn('已取消', msg)


class Round8SourceContracts(unittest.TestCase):
    def _src(self, name):
        return (ROOT / name).read_text(encoding='utf-8')

    def test_av_api_request_is_cancelable(self):
        av = self._src('pages/av_page.py')
        self.assertIn('cancel_event=self._cancel', av)
        self.assertIn('_sig_asr_stopped', av)
        self.assertIn('def _on_asr_stopped', av)
        self.assertIn('已停止识别', av)
        # 该文件没有 import Slot，给新槽加装饰器会在真实环境 NameError
        self.assertNotIn('@Slot()\n    def _on_asr_stopped', av)

    def test_api_progress_is_indeterminate(self):
        av = self._src('pages/av_page.py')
        self.assertIn('currentData() == API_MODE', av)
        self.assertIn('setPlaceholderText(', av)
        self.assertIn('请先点「开始识别」生成字幕', av)

    def test_batch_esc_is_guarded(self):
        d = self._src('batch_dialog.py')
        self.assertIn('def reject(self):', d)
        self.assertIn('self.close()  # 交给 closeEvent', d)
        self.assertIn('请先点「停止」，再关闭窗口', d)

    def test_mic_install_entry_is_real(self):
        s = self._src('pages/settings_page.py')
        self.assertIn("packages = ('sounddevice',)", s)
        self.assertIn('cancel=cancel, packages=packages', s)
        self.assertIn('点右侧「安装组件」', s)
        lc = self._src('live_caption.py')
        self.assertIn('packages=None', lc)
        self.assertIn('list(pkgs)', lc)
        m = self._src('main_window.py')
        self.assertNotIn('（pip install sounddevice）', m)
        self.assertIn('点「安装组件」在线安装', m)

    def test_guidance_names_real_buttons(self):
        m = self._src('main_window.py')
        for token in ('点「安装识别组件」', '点「下载模型」',
                      '设置 → OCR 设置', '已自动切换到',
                      '_on_av_polished_sources', 'AI 润色完成'):
            self.assertIn(token, m)
        self.assertIn('设置 → 语音识别设置',
                      self._src('speech_recognizer.py'))

    def test_onboarding_copy(self):
        h = self._src('pages/home_page.py')
        self.assertIn('点上方「区域截图」开始', h)
        self.assertIn('_translate_label', h)
        # 按钮文字必须保持紧凑：把 "Ctrl + Enter" 写进按钮会把控制卡
        # FlowLayout 撑到第二行（有真实 UI 布局回归测试）。快捷键改由
        # tooltip / 占位文案 / 命令面板展示。
        self.assertIn('self._translate_label = "翻译"', h)
        self.assertIn('快捷键 {label}', h)
        s = self._src('pages/settings_page.py')
        self.assertIn('开箱即用，默认无需填密钥', s)
        self.assertIn('Ctrl+F 聚焦', s)
        self.assertIn('托盘右键菜单触发', s)
        self.assertIn('松开后：提取 / 翻译 / 编辑标注',
                      self._src('screenshot_overlay.py'))

    def test_caption_error_dedup(self):
        lc = self._src('live_caption.py')
        self.assertIn('warned = False', lc)
        self.assertIn('if not warned:', lc)

    def test_tray_hint_lists_features(self):
        self.assertIn('开关 / 暂停 / ', self._src('main_window.py'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
