#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 21 轮回归：OCR 模型「选了却用不上」与组件安装反复 403。

用户实测场景：
1. 本机 pip.ini 全局指向清华源（实测 /simple/easyocr/ 直接 403），
   安装链路最后一跳「系统默认源」把这份坏配置读回来，于是失败日志里
   全是清华 403，看起来像程序一直在用失效镜像；
2. 手动下好的 .pth 模型显示「模型已就绪」，同一页却写着「未安装
   EasyOCR」，因为前者只看模型文件、后者只看引擎组件，且提示语还宣称
   「将直接离线识别」；
3. 找不到「如何选择本地模型」的入口：只能选文件夹复制，不能多选散落
   的 .pth，也不能直接指向已有目录而不复制几百 MB。

本轮锁死：安装链路只用明确镜像 + 隔离本机 pip 配置；模型/引擎状态
组合判定并给零安装退路；自选模型目录与检测、统计、识别器同源。
"""
import contextlib
import io
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ocr_models
import speech_recognizer as asr
import subprocess_util as su
import user_messages

TENCENT = "https://mirrors.cloud.tencent.com/pypi/simple/"
HUAWEI = "https://repo.huaweicloud.com/repository/pypi/simple/"
DETECTION = "craft_mlt_25k.pth"
CHINESE = "zh_sim_g2.pth"
ENGLISH = "english_g2.pth"


def _src(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _write_models(directory, names, size=64):
    os.makedirs(directory, exist_ok=True)
    for name in names:
        with open(os.path.join(directory, name), "wb") as fh:
            fh.write(b"x" * size)
    return directory


class PipSourceTests(unittest.TestCase):
    """安装链路不再回落到用户坏 pip.ini，失败时逐源给出原因。"""

    def test_every_index_is_explicit_and_no_broken_mirror(self):
        self.assertEqual(su.PIP_INDEXES[0][0], "腾讯云镜像")
        names = [name for name, _ in su.PIP_INDEXES]
        self.assertNotIn("系统默认源", names)
        self.assertNotIn("默认配置", names)
        self.assertIn("PyPI 官方", names)
        for name, args in su.PIP_INDEXES:
            self.assertIn("-i", args, name)
            joined = " ".join(args)
            self.assertNotIn("tuna.tsinghua", joined, name)
            self.assertNotIn("aliyun", joined, name)
        self.assertIn(TENCENT, " ".join(su.PIP_INDEXES[0][1]))
        self.assertIn(HUAWEI, " ".join(dict(su.PIP_INDEXES)["华为云镜像"]))

    def test_pip_env_isolates_broken_global_config_but_keeps_proxy(self):
        broken = {
            "PIP_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple",
            "PIP_EXTRA_INDEX_URL": "https://mirrors.aliyun.com/pypi/simple/",
            "PIP_NO_INDEX": "1",
            "PIP_FIND_LINKS": "C:/broken",
            "HTTP_PROXY": "http://127.0.0.1:7890",
        }
        before = os.environ.get("PIP_INDEX_URL")
        with mock.patch.dict(os.environ, broken, clear=False):
            env = su.pip_env()
            # pip_env 只造副本，不改进程自身的环境
            self.assertEqual(os.environ.get("PIP_INDEX_URL"),
                             broken["PIP_INDEX_URL"])
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        for key in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
                    "PIP_NO_INDEX", "PIP_FIND_LINKS"):
            self.assertNotIn(key, env)
        self.assertEqual(env["HTTP_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(os.environ.get("PIP_INDEX_URL"), before)

    def test_install_passes_isolated_env_and_summarizes_each_source(self):
        class Proc:
            returncode = 1

            def __init__(self):
                self.stdout = io.StringIO(
                    "Looking in indexes: https://example.invalid/simple\n"
                    "ERROR: HTTP error 403 while getting example.invalid\n")

            def poll(self):
                return 1

        calls, seen = [], []

        def fake_cmd(_packages, extra_args=()):
            calls.append(list(extra_args))
            return ["pip", "install"], ""

        @contextlib.contextmanager
        def fake_process(_cmd, **kwargs):
            seen.append(kwargs)
            yield Proc()

        with mock.patch.object(su, "pip_cmd", side_effect=fake_cmd), \
             mock.patch.object(su, "pip_process", new=fake_process):
            ok, msg = su.pip_install_with_mirrors(["easyocr"])
        self.assertFalse(ok)
        self.assertEqual(len(seen), len(su.PIP_INDEXES))
        for kwargs in seen:
            self.assertEqual(kwargs.get("env", {}).get("PIP_CONFIG_FILE"),
                             os.devnull)
        self.assertEqual(sum(1 for a in calls if "-i" in a),
                         len(su.PIP_INDEXES))
        self.assertIn("--retries", " ".join(calls[1]))
        self.assertIn("已依次尝试", msg)
        self.assertIn("各源失败摘要", msg)
        for name, _ in su.PIP_INDEXES:
            self.assertIn(name + "：", msg)
        self.assertIn("403", msg)
        self.assertIn("Windows 系统自带", msg)
        self.assertNotIn("系统默认源", msg)
        self.assertNotIn("tuna.tsinghua.edu.cn", msg)

    def test_failure_line_prefers_error_over_progress(self):
        self.assertEqual(
            su._failure_line(["Downloading 12%", "ERROR: no space left"]),
            "ERROR: no space left")

    def test_all_install_chains_share_the_same_sources(self):
        self.assertEqual(
            [tuple(x) for x in asr._PIP_INDEXES],
            [tuple(x) for x in su.PIP_INDEXES])
        live = _src("live_caption.py")
        self.assertNotIn("tuna.tsinghua", live)
        self.assertIn(HUAWEI, live)
        speech = _src("speech_recognizer.py")
        self.assertNotIn("tuna.tsinghua", speech)
        self.assertNotIn("本机默认", speech)

    def test_403_hint_states_all_sources_failed_and_offers_fallback(self):
        text = user_messages.friendly_install_error(
            "ERROR: HTTP error 403 while getting "
            "https://pypi.tuna.tsinghua.edu.cn/packages/xx")
        for token in ("403", "换用其它源", "腾讯云", "重试安装",
                      "全部源都失败", "Windows 系统自带"):
            self.assertIn(token, text)


class CustomModelDirTests(unittest.TestCase):
    """「直接指定模型目录」后检测 / 统计 / 识别器必须读同一个目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.default_root = os.path.join(self._tmp.name, "default")
        self.legacy = os.path.join(self._tmp.name, "legacy")
        self.custom = os.path.join(self._tmp.name, "my_models")
        _write_models(self.custom, [DETECTION, CHINESE])
        _write_models(self.default_root, [DETECTION])
        _write_models(self.legacy, [ENGLISH])
        patch = mock.patch.multiple(
            ocr_models, MODEL_ROOT=self.default_root,
            LEGACY_EASY_DIR=self.legacy)
        patch.start()
        self.addCleanup(patch.stop)

    def _cfg(self):
        return {ocr_models.CUSTOM_MODEL_DIR_KEY: self.custom}

    def test_explicit_custom_dir_is_used_everywhere(self):
        self.assertEqual(ocr_models.resolve_model_dir(custom=self.custom),
                         self.custom)
        self.assertEqual(ocr_models.reader_languages(custom=self.custom),
                         ["ch_sim"])
        detect = ocr_models.detect_local_models(custom=self.custom)
        self.assertEqual(detect["resolved_dir"], self.custom)
        self.assertEqual(detect["custom_dir"], self.custom)
        self.assertTrue(detect["ready"])
        self.assertEqual(detect["available_languages"], ["ch_sim"])
        self.assertEqual(sorted(detect["files"]),
                         sorted([CHINESE, DETECTION]))
        self.assertEqual(detect["split_files"], [])
        summary = ocr_models.model_cache_summary(detect=detect)
        self.assertIn("已可离线识别", summary)
        self.assertNotIn("还缺", summary)

    def test_saved_custom_dir_takes_effect_at_runtime(self):
        with mock.patch("config.load_user_config", return_value=self._cfg()):
            self.assertEqual(ocr_models.custom_model_dir(), self.custom)
            self.assertEqual(ocr_models.active_model_dir(), self.custom)
            self.assertEqual(ocr_models.ensure_model_root(), self.custom)
            self.assertEqual(ocr_models.resolve_model_dir(), self.custom)
            self.assertEqual(ocr_models.reader_languages(), ["ch_sim"])
            detect = ocr_models.detect_local_models()
        self.assertEqual(detect["resolved_dir"], self.custom)
        self.assertEqual(detect["custom_dir"], self.custom)
        self.assertTrue(detect["ready"])

    def test_explicit_root_and_legacy_never_read_user_config(self):
        with mock.patch("config.load_user_config",
                        return_value=self._cfg()) as stub:
            detect = ocr_models.detect_local_models(
                root=self.default_root, legacy=self.legacy)
            stub.assert_not_called()
        self.assertEqual(detect["resolved_dir"], self.default_root)
        self.assertEqual(detect["custom_dir"], "")

    def test_invalid_custom_dir_falls_back_without_lying(self):
        empty = os.path.join(self._tmp.name, "empty")
        os.makedirs(empty, exist_ok=True)
        with mock.patch("config.load_user_config",
                        return_value={ocr_models.CUSTOM_MODEL_DIR_KEY: empty}):
            self.assertEqual(ocr_models.custom_model_dir(), "")
            detect = ocr_models.detect_local_models()
        self.assertEqual(detect["resolved_dir"], self.default_root)
        self.assertEqual(detect["custom_dir"], "")

    def test_import_goes_into_the_selected_dir_not_the_default(self):
        src = os.path.join(self._tmp.name, "download")
        _write_models(src, [ENGLISH])
        with mock.patch("config.load_user_config", return_value=self._cfg()):
            _count, names = ocr_models.import_local_model(src)
        self.assertIn(ENGLISH, names)
        self.assertTrue(os.path.isfile(os.path.join(self.custom, ENGLISH)))
        self.assertFalse(
            os.path.isfile(os.path.join(self.default_root, ENGLISH)))

    def test_manual_hint_documents_how_to_choose_local_models(self):
        hint = ocr_models.manual_download_hint()
        self.assertIn("如何选择本地模型", hint)
        self.assertIn("直接指定模型目录", hint)
        self.assertIn("不复制", hint)


class UiContractTests(unittest.TestCase):
    """设置页不再出现「模型已就绪」与「未安装」互相打架的文案。"""

    def test_status_combines_engine_and_models_with_fallback(self):
        src = _src("pages/settings_page.py")
        for token in ("模型文件 ≠ 引擎", "改用 Windows 自带 OCR",
                      "直接指定模型目录", "选择 .pth 文件（可多选）",
                      "恢复默认模型目录", "_switch_to_windows_ocr",
                      "ocr_use_windows_btn", "custom_dir",
                      "engine_ready", "detect_local_models()['ready']"):
            self.assertIn(token, src, token)
        self.assertNotIn("EasyOCR 将直接离线识别", src)
        self.assertIn("但 EasyOCR 引擎组件还没装好", src)

    def test_model_dir_buttons_use_the_active_dir(self):
        src = _src("pages/settings_page.py")
        self.assertIn("ocr_models.active_model_dir()", src)
        self.assertIn("detect_local_models()['resolved_dir']", src)
        self.assertIn("model_cache_info(detect['resolved_dir'])", src)

    def test_readme_explains_choice_and_isolated_pip_config(self):
        readme = _src("README.md")
        self.assertIn("如何选择本地模型", readme)
        self.assertIn("直接指定", readme)
        self.assertIn("pip.ini", readme)
        self.assertIn("腾讯云（首选）", readme)
        ocr_part = readme.split("## OCR 引擎")[1].split("## 翻译 API")[0]
        self.assertIn("pip.ini", ocr_part)
        self.assertNotIn("pypi.tuna.tsinghua.edu.cn", ocr_part)


try:
    # 真实 PySide6 只在 Python 3.11 环境可用，默认解释器会自动跳过。
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    HAS_QT = True
except ImportError:  # pragma: no cover
    HAS_QT = False

FAKE_DETECT = {
    "ready": True, "resolved_dir": "X", "files": {}, "missing": [],
    "optional_missing": [], "available_languages": ["ch_sim"],
    "split_files": [], "legacy_files": {}, "own_files": {},
    "other_dir": "", "custom_dir": "", "custom_configured": "",
    "total_bytes": 0, "has_detection": True, "can_use_offline": True,
}


@unittest.skipUnless(HAS_QT, "真实 PySide6 仅在 Python 3.11 可用")
class SettingsSmokeTests(unittest.TestCase):
    """离屏真实构造：状态组合、检测提示、一键切换都能跑通。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _page(self):
        from pages.settings_page import SettingsPage
        with mock.patch("pages.settings_page.save_user_config",
                        return_value=True), \
             mock.patch.object(QMessageBox, "question",
                               return_value=QMessageBox.StandardButton.No), \
             mock.patch.object(QMessageBox, "information",
                               return_value=None):
            return SettingsPage()

    def test_status_combines_engine_and_models(self):
        page = self._page()
        self.assertTrue(hasattr(page, "ocr_use_windows_btn"))
        self.assertTrue(hasattr(page, "_ocr_import_btn"))
        with mock.patch("ocr_recognizer.check_engine_available",
                        return_value=False), \
             mock.patch("ocr_models.detect_local_models",
                        return_value=FAKE_DETECT):
            page._refresh_ocr_status()
            text = page.ocr_status_label.text()
            self.assertIn("还差 EasyOCR 引擎组件", text)
            self.assertIn("模型文件 ≠ 引擎", text)
            self.assertFalse(page.ocr_use_windows_btn.isHidden())
        with mock.patch("ocr_recognizer.check_engine_available",
                        return_value=True), \
             mock.patch("ocr_models.detect_local_models",
                        return_value=FAKE_DETECT):
            page._refresh_ocr_status()
            self.assertIn("引擎已安装", page.ocr_status_label.text())
            self.assertTrue(page.ocr_use_windows_btn.isHidden())

    def test_detect_local_models_warns_when_engine_missing(self):
        page = self._page()
        with mock.patch("qfluentwidgets.InfoBar.warning") as warn, \
             mock.patch("qfluentwidgets.InfoBar.success") as success, \
             mock.patch("ocr_recognizer.check_engine_available",
                        return_value=False), \
             mock.patch("ocr_models.detect_local_models",
                        return_value=FAKE_DETECT):
            page._detect_ocr_models()
        self.assertTrue(warn.called)
        self.assertFalse(success.called)

    def test_switch_to_windows_ocr_saves_immediately(self):
        page = self._page()
        with mock.patch("pages.settings_page.save_user_config",
                        return_value=True) as saved, \
             mock.patch("pages.settings_page.load_user_config",
                        return_value={}):
            page._switch_to_windows_ocr()
        self.assertEqual(page.ocr_engine_combo.currentText(),
                         "Windows 系统自带")
        saved.assert_called_once_with({"ocr_engine": "windows"})


if __name__ == "__main__":
    unittest.main()
