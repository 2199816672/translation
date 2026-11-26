#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 20 轮回归：手动下载 2 个 OCR 模型即可离线识别 + pip 镜像走腾讯云。

用户实测场景：按「手动下载说明」把 craft_mlt_25k.pth + zh_sim_g2.pth
放进模型目录，设置页仍报「还缺 1 个模型文件」、识别也跑不起来——因为代码
硬性要求 ch_sim+en 三个文件，还为此打开 easyocr 联网下载 english_g2.pth。
本轮把「本地已有语言即可离线」和「镜像首选腾讯云」两条都锁死。
"""
import contextlib
import io
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ocr_models
import ocr_recognizer
import speech_recognizer as asr
import subprocess_util as su
import user_messages

TENCENT = "https://mirrors.cloud.tencent.com/pypi/simple/"
ALIYUN = "mirrors.aliyun.com"
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


class MinimalModelTests(unittest.TestCase):
    """「检测模型 + 中文」两个文件就必须判定为可用，不能逼用户下英文模型。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "models", "ocr")
        self.legacy = os.path.join(self._tmp.name, "legacy")
        self._patch = mock.patch.multiple(
            ocr_models, MODEL_ROOT=self.root,
            LEGACY_EASY_DIR=self.legacy)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _detect(self):
        return ocr_models.detect_local_models(
            root=self.root, legacy=self.legacy)

    def test_two_chinese_files_are_offline_ready(self):
        _write_models(self.root, [DETECTION, CHINESE])
        detect = self._detect()
        self.assertTrue(detect["ready"])
        self.assertTrue(detect["can_use_offline"])
        self.assertEqual(detect["missing"], [])
        self.assertEqual(detect["optional_missing"], [ENGLISH])
        self.assertEqual(detect["available_languages"], ["ch_sim"])
        self.assertEqual(detect["resolved_dir"], self.root)

    def test_summary_no_longer_says_missing_one_file(self):
        _write_models(self.root, [DETECTION, CHINESE])
        summary = ocr_models.model_cache_summary(
            ocr_models.model_cache_info(self.root))
        self.assertIn("已可离线识别（简体中文）", summary)
        self.assertIn("2 个模型文件", summary)
        self.assertNotIn("还缺", summary)

    def test_empty_dir_only_requires_two_files(self):
        detect = self._detect()
        self.assertFalse(detect["ready"])
        self.assertEqual(detect["missing"], [DETECTION, CHINESE])
        self.assertNotIn(ENGLISH, detect["missing"])
        self.assertEqual(detect["optional_missing"], [ENGLISH])

    def test_detection_only_requires_one_language_model(self):
        _write_models(self.root, [DETECTION])
        detect = self._detect()
        self.assertFalse(detect["ready"])
        self.assertEqual(detect["missing"], [CHINESE])

    def test_three_files_are_complete(self):
        _write_models(self.root, [DETECTION, CHINESE, ENGLISH])
        detect = self._detect()
        self.assertEqual(detect["available_languages"], ["ch_sim", "en"])
        self.assertEqual(detect["optional_missing"], [])
        self.assertIn(
            "模型齐全",
            ocr_models.model_cache_summary(
                ocr_models.model_cache_info(self.root)))

    def test_available_languages_need_detection_and_nonempty_model(self):
        _write_models(self.root, [CHINESE])
        self.assertEqual(
            ocr_models.available_languages(self.root), [])
        _write_models(self.root, [DETECTION, CHINESE, ENGLISH])
        self.assertEqual(
            ocr_models.available_languages(self.root), ["ch_sim", "en"])
        with open(os.path.join(self.root, ENGLISH), "wb") as fh:
            fh.write(b"")
        self.assertEqual(
            ocr_models.available_languages(self.root), ["ch_sim"])

    def test_reader_languages_use_only_local_models(self):
        self.assertEqual(ocr_models.reader_languages(), [])
        _write_models(self.root, [DETECTION, CHINESE])
        self.assertEqual(ocr_models.reader_languages(), ["ch_sim"])
        _write_models(self.root, [ENGLISH])
        self.assertEqual(ocr_models.reader_languages(), ["ch_sim", "en"])

    def test_manual_hint_states_two_files_are_enough(self):
        _write_models(self.root, [DETECTION, CHINESE])
        hint = ocr_models.manual_download_hint()
        self.assertIn("已可离线识别（简体中文）", hint)
        self.assertIn("2 个文件就够", hint)
        self.assertIn(DETECTION, hint)
        self.assertIn(CHINESE, hint)
        self.assertIn("可选补充：" + ENGLISH, hint)
        self.assertIn("引擎", hint)
        self.assertIn("不写死路径", hint)
        self.assertNotIn(":\\", hint)

    def test_persistence_promise_kept(self):
        hint = ocr_models.manual_download_hint()
        self.assertIn("升级", hint)
        self.assertIn("无需重新下载", hint)


class ReaderOfflineTests(unittest.TestCase):
    """真实调用 _init_reader：只把本地齐备的语言交给 easyocr，不联网。"""

    def _init(self, names, languages=None, legacy_reader=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = _write_models(os.path.join(tmp, "ocr"), names)
            legacy = os.path.join(tmp, "legacy")
            calls = []
            fake = types.ModuleType("easyocr")

            if legacy_reader:
                class Reader:
                    def __init__(self, langs, gpu=False):
                        calls.append((list(langs),
                                      {"legacy_fallback": True, "gpu": gpu}))
            else:
                class Reader:
                    def __init__(self, langs, **kwargs):
                        calls.append((list(langs), kwargs))

            fake.Reader = Reader
            with mock.patch.multiple(
                    ocr_models, MODEL_ROOT=root,
                    LEGACY_EASY_DIR=legacy), \
                 mock.patch.dict(sys.modules,
                                 {"easyocr": fake, "torch": None}):
                rec = ocr_recognizer.EasyOCRRecognizer(
                    languages or ["ch_sim", "en"])
                model_exists = rec.model_exists
                rec._init_reader()
            return calls, root, model_exists

    def test_two_files_reader_is_chinese_only_and_offline(self):
        calls, root, model_exists = self._init([DETECTION, CHINESE])
        self.assertTrue(model_exists)
        self.assertEqual(len(calls), 1)
        langs, kwargs = calls[0]
        self.assertEqual(langs, ["ch_sim"])
        self.assertIs(kwargs.get("download_enabled"), False)
        self.assertEqual(kwargs.get("model_storage_directory"), root)
        self.assertIs(kwargs.get("gpu"), False)
        self.assertNotIn("en", langs)

    def test_all_languages_when_models_complete(self):
        calls, _root, _exists = self._init(
            [DETECTION, CHINESE, ENGLISH])
        self.assertEqual(calls[0][0], ["ch_sim", "en"])
        self.assertIs(calls[0][1].get("download_enabled"), False)

    def test_no_model_keeps_online_download_fallback(self):
        calls, _root, model_exists = self._init([])
        self.assertFalse(model_exists)
        self.assertEqual(calls[0][0], ["ch_sim", "en"])
        self.assertIs(calls[0][1].get("download_enabled"), True)

    def test_old_easyocr_fallback_still_uses_local_languages(self):
        calls, _root, _exists = self._init(
            [DETECTION, CHINESE], legacy_reader=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ["ch_sim"])
        self.assertTrue(calls[0][1].get("legacy_fallback"))

    def test_model_error_hint_does_not_ask_to_delete_models(self):
        src = _src("ocr_recognizer.py")
        self.assertNotIn("请删除 ~/.EasyOCR/model 后重试", src)
        self.assertIn("不要删除已经下好的模型", src)
        self.assertIn("检测本地模型", src)


class MirrorTests(unittest.TestCase):
    """所有 pip 安装链路：腾讯云优先，阿里云彻底移除。"""

    def test_subprocess_util_primary_is_tencent(self):
        self.assertEqual(su.PIP_INDEXES[0][0], "腾讯云镜像")
        self.assertIn(TENCENT, su.PIP_INDEXES[0][1])
        for _name, args in su.PIP_INDEXES:
            self.assertNotIn(ALIYUN, " ".join(args))

    def test_failed_install_manual_command_prefers_tencent(self):
        class Proc:
            returncode = 1

            def __init__(self):
                self.stdout = io.StringIO("")

            def poll(self):
                return 1

        @contextlib.contextmanager
        def fake_process(_cmd, **_kwargs):
            yield Proc()

        with mock.patch.object(su, "pip_cmd",
                               return_value=(["pip"], "")), \
             mock.patch.object(su, "pip_process", new=fake_process):
            ok, msg = su.pip_install_with_mirrors(["easyocr"])
        self.assertFalse(ok)
        self.assertIn(TENCENT, msg)
        self.assertIn("腾讯云镜像", msg)
        self.assertNotIn(ALIYUN, msg)

    def test_speech_install_sources_and_message(self):
        self.assertEqual(asr._PIP_INDEXES[0][0], "腾讯云镜像")
        for _label, args in asr._PIP_INDEXES:
            self.assertNotIn(ALIYUN, " ".join(args))
        with mock.patch.object(asr, "pip_cmd",
                               return_value=(["pip"], "")), \
             mock.patch.object(asr, "_run_pip",
                               return_value=(False, "err")):
            ok, msg = asr.install_dependencies()
        self.assertFalse(ok)
        self.assertIn("腾讯云", msg)
        self.assertIn(TENCENT, msg)
        self.assertNotIn("阿里云", msg)

    def test_live_caption_sources(self):
        src = _src("live_caption.py")
        self.assertIn(TENCENT, src)
        self.assertNotIn(ALIYUN, src)
        self.assertLess(src.index(TENCENT), src.index("pypi.org/simple"))

    def test_403_hint_mentions_tencent(self):
        text = user_messages.friendly_install_error(
            "ERROR: HTTP error 403 while getting " + ALIYUN)
        self.assertIn("403", text)
        self.assertIn("换用其它源", text)
        self.assertIn("腾讯云", text)

    def test_no_aliyun_url_left_in_runtime_sources(self):
        for name in ("subprocess_util.py", "speech_recognizer.py",
                     "live_caption.py", "ocr_recognizer.py",
                     "user_messages.py"):
            self.assertNotIn(ALIYUN, _src(name), name)

    def test_readme_documents_tencent_and_two_file_rule(self):
        readme = _src("README.md")
        self.assertIn("腾讯云（首选）", readme)
        self.assertNotIn(ALIYUN, readme)
        self.assertIn("只识别中文只需 2 个文件", readme)
        self.assertIn("模型文件不等于引擎", readme)


class UiContractTests(unittest.TestCase):
    """设置页不再用「还缺 1 个文件」吓唬已下好模型的用户。"""

    def test_settings_shows_offline_ready_and_optional(self):
        settings = _src("pages/settings_page.py")
        self.assertIn("已可离线识别", settings)
        self.assertIn("可选再补", settings)
        self.assertIn("必需缺少", settings)
        self.assertIn("可选补充", settings)
        self.assertIn("resolved_dir", settings)
        self.assertNotIn("⚠ 可用但还缺", settings)
        self.assertNotIn("还缺 {len(detect['missing'])} 个模型文件",
                         settings)

    def test_settings_hint_mentions_two_files_and_engine(self):
        settings = _src("pages/settings_page.py")
        self.assertIn("两个 .pth 文件", settings)
        self.assertIn("请先点「安装」装好 EasyOCR 引擎", settings)

    def test_recognizer_uses_available_languages(self):
        src = _src("ocr_recognizer.py")
        self.assertIn("ocr_models.available_languages(", src)
        self.assertIn("download_enabled = not bool(usable)", src)
        self.assertIn("reader_langs = usable or self.languages", src)

    def test_checklist_flags_missing_engine(self):
        """模型齐、引擎没装好时不能打勾，要直接说「还差组件」。"""
        settings = _src("pages/settings_page.py")
        self.assertIn("但 EasyOCR 引擎组件还没装好", settings)
        self.assertIn("if easy_ocr and ocr_models_ready", settings)
        self.assertNotIn('(easy_ocr or ocr_models_ready, "高精度 OCR"',
                         settings)


class SplitModelDirTests(unittest.TestCase):
    """v1.5.4 发布前加固：统计/检测同源、空文件不谎报、分散两处要提示合并。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "models", "ocr")
        self.legacy = os.path.join(self._tmp.name, "legacy")
        self._patch = mock.patch.multiple(
            ocr_models, MODEL_ROOT=self.root,
            LEGACY_EASY_DIR=self.legacy)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _detect(self, root=None, legacy=None):
        return ocr_models.detect_local_models(
            root=root or self.root, legacy=legacy or self.legacy)

    def _summary(self, detect):
        return ocr_models.model_cache_summary(
            ocr_models.model_cache_info(self.root), detect=detect)

    def test_zero_byte_model_is_not_reported_as_complete(self):
        _write_models(self.root, [CHINESE, ENGLISH])
        with open(os.path.join(self.root, DETECTION), "wb") as fh:
            fh.write(b"")           # 下载中断留下的 0 字节文件
        summary = ocr_models.model_cache_summary(
            ocr_models.model_cache_info(self.root))
        self.assertNotIn("模型齐全", summary)
        self.assertNotIn("已可离线识别", summary)
        self.assertIn("还缺", summary)
        self.assertFalse(self._detect()["ready"])

    def test_summary_uses_detect_when_models_live_in_legacy(self):
        # 统一目录只剩一个残留文件，识别器真正读的是旧目录且已可用。
        _write_models(self.legacy, [DETECTION, CHINESE, ENGLISH])
        _write_models(self.root, [ENGLISH])
        detect = self._detect()
        self.assertTrue(detect["ready"])
        self.assertEqual(detect["resolved_dir"], self.legacy)
        summary = self._summary(detect)
        self.assertIn("已可离线识别", summary)
        self.assertIn("3 个模型文件", summary)
        self.assertNotIn("还缺", summary)

    def test_split_dirs_detects_extra_models_in_legacy(self):
        _write_models(self.root, [DETECTION, CHINESE])
        _write_models(self.legacy, [ENGLISH])
        detect = self._detect()
        self.assertTrue(detect["ready"])
        self.assertTrue(detect["split_dirs"])
        self.assertEqual(detect["split_files"], [ENGLISH])
        self.assertEqual(detect["other_dir"], self.legacy)
        hint = ocr_models.manual_download_hint()
        self.assertIn("另一目录还有", hint)
        self.assertIn(ENGLISH, hint)
        self.assertIn("不用重新下载", hint)
        self.assertNotIn("可选补充：" + ENGLISH, hint)

    def test_split_dirs_detects_extra_models_in_root(self):
        _write_models(self.legacy, [DETECTION, CHINESE])
        _write_models(self.root, [ENGLISH])
        detect = self._detect()
        self.assertEqual(detect["resolved_dir"], self.legacy)
        self.assertTrue(detect["split_dirs"])
        self.assertEqual(detect["split_files"], [ENGLISH])
        self.assertEqual(detect["other_dir"], self.root)

    def test_no_split_for_empty_or_same_directory(self):
        self.assertFalse(self._detect()["split_dirs"])
        _write_models(self.root, [DETECTION, CHINESE])
        self.assertFalse(self._detect()["split_dirs"])
        self.assertFalse(
            self._detect(root=self.root, legacy=self.root)["split_dirs"])

    def test_zero_byte_extra_file_does_not_trigger_split(self):
        _write_models(self.root, [DETECTION, CHINESE])
        _write_models(self.legacy, [ENGLISH], size=0)
        self.assertFalse(self._detect()["split_dirs"])

    def test_status_suffix_matches_detect(self):
        self.assertIn("还缺", ocr_models.model_status_suffix(
            {"available_languages": [], "missing": [DETECTION, CHINESE],
             "optional_missing": [ENGLISH]}))
        self.assertIn("已可离线识别（简体中文）",
                      ocr_models.model_status_suffix(
                          {"available_languages": ["ch_sim"], "missing": [],
                           "optional_missing": [ENGLISH]}))

    def test_settings_page_wires_split_hint(self):
        settings = _src("pages/settings_page.py")
        self.assertIn("model_cache_summary(cache, detect=detect)", settings)
        self.assertIn("split_files = detect.get('split_files') or []", settings)
        self.assertIn("另一目录还有", settings)
        self.assertIn("导入本地模型", settings)
        self.assertIn("detect.get('other_dir', '')", settings)
        # 「模型分散」提示不能再挂在 missing 为空的不可达分支上
        detector = settings.split("def _detect_ocr_models(self):", 1)[1]
        detector = detector.split("\n    def ", 1)[0]
        self.assertIn("if split_files:", detector)
        self.assertIn("模型分散在两个目录", detector)


if __name__ == "__main__":
    unittest.main()
