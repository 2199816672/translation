#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 17 轮：适当使用缓存提升速度与性能。

覆盖四条热点（翻译 / AI 润色 / OCR / 配置读取）与通用缓存本身，重点锁死
这些边界：失败与空结果绝不缓存、原文回显不固化、密钥不串用也不落盘、
测试连接永远真实发请求、容量 / TTL / 线程安全、设置可关可清、升级保留。
"""
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import contextmanager
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# translator / free_translator 顶层需要 requests、deep_translator；本机 CI 可能
# 没装，用最小桩代替。本轮所有引擎调用都被 mock，不会发任何真实网络请求。
try:
    import requests  # noqa: F401
except ImportError:  # pragma: no cover
    _req_stub = types.ModuleType("requests")
    _req_stub.exceptions = types.SimpleNamespace(
        ConnectionError=Exception, Timeout=Exception,
        SSLError=Exception, HTTPError=Exception)
    sys.modules.setdefault("requests", _req_stub)


def _load_translator():
    fake_dt = types.ModuleType("deep_translator")
    fake_dt.DeeplTranslator = mock.MagicMock()
    fake_dt.BaiduTranslator = mock.MagicMock()
    fake_exc = types.ModuleType("deep_translator.exceptions")
    fake_exc.RequestError = type("RequestError", (Exception,), {})
    fake_dt.exceptions = fake_exc
    with mock.patch.dict(sys.modules, {
            "deep_translator": fake_dt,
            "deep_translator.exceptions": fake_exc}):
        sys.modules.pop("translator", None)
        import translator
        return translator


def _src(name):
    return (ROOT / name).read_text(encoding="utf-8")


@contextmanager
def isolated_cache(persist=False):
    """把缓存目录与设置隔离到临时目录，避免测试写到真实用户数据目录。"""
    sys.path.insert(0, str(ROOT))
    import result_cache

    cfg = {
        "result_cache_enabled": True,
        "result_cache_max_entries": 500,
        "result_cache_ttl_days": 7,
        "result_cache_persist": persist,
    }
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(result_cache, "cache_dir",
                               return_value=pathlib.Path(tmp)), \
             mock.patch.object(result_cache, "read_settings",
                               side_effect=lambda: dict(cfg)):
            result_cache.reset_for_tests()
            try:
                yield result_cache, pathlib.Path(tmp), cfg
            finally:
                result_cache.reset_for_tests()


class ResultCacheTests(unittest.TestCase):
    """通用缓存：LRU / TTL / 统计 / 持久化 / 线程安全。"""

    def test_hit_miss_and_stats(self):
        with isolated_cache() as (rc, tmp, cfg):
            cache = rc.ResultCache("unit")
            self.assertIsNone(cache.get("k"))
            self.assertTrue(cache.put("k", "值"))
            self.assertEqual(cache.get("k"), "值")
            stats = cache.stats()
            self.assertEqual((stats["hits"], stats["misses"]), (1, 1))
            self.assertAlmostEqual(stats["hit_rate"], 0.5)
            self.assertEqual(stats["size"], 1)
            self.assertTrue(stats["enabled"])

    def test_disabled_cache_stops_reading_and_writing(self):
        with isolated_cache() as (rc, tmp, cfg):
            cache = rc.ResultCache("unit")
            cache.put("k", "值")
            cache.configure(enabled=False)
            self.assertIsNone(cache.get("k"))
            self.assertFalse(cache.put("k2", "值2"))
            self.assertFalse(cache.stats()["enabled"])

    def test_empty_and_oversized_values_are_never_cached(self):
        with isolated_cache() as (rc, tmp, cfg):
            cache = rc.ResultCache("unit")
            self.assertFalse(cache.put("a", ""))
            self.assertFalse(cache.put("b", "   "))
            self.assertFalse(cache.put("c", None))
            self.assertFalse(cache.put("d", "x" * (rc.MAX_VALUE_CHARS + 1)))
            self.assertEqual(cache.stats()["size"], 0)

    def test_lru_evicts_oldest(self):
        with isolated_cache() as (rc, tmp, cfg):
            cache = rc.ResultCache("unit", max_entries=2)
            cache.put("a", "A")
            cache.put("b", "B")
            cache.get("a")               # a 变最近使用
            cache.put("c", "C")
            self.assertIsNone(cache.get("b"))
            self.assertEqual(cache.get("a"), "A")
            self.assertEqual(cache.get("c"), "C")

    def test_ttl_expiry(self):
        with isolated_cache() as (rc, tmp, cfg):
            cache = rc.ResultCache("unit", ttl_days=7)
            cache.put("k", "值")
            # 直接把时间戳拨到过期，避免测试真的 sleep
            with cache._lock:
                cache._data["k"] = (time.time() - 8 * 86400, "值")
            self.assertIsNone(cache.get("k"))
            self.assertEqual(cache.stats()["size"], 0)

    def test_get_returns_copy_not_shared_reference(self):
        with isolated_cache() as (rc, tmp, cfg):
            cache = rc.ResultCache("unit")
            cache.put("k", {"rows": [1, 2]})
            first = cache.get("k")
            first["rows"].append(3)
            self.assertEqual(cache.get("k")["rows"], [1, 2])

    def test_persist_roundtrip_and_no_plaintext_secrets(self):
        with isolated_cache(persist=True) as (rc, tmp, cfg):
            cache = rc.ResultCache("unit")
            secret = "sk-THIS_MUST_NOT_LEAK"
            key = rc.make_key("engine", secret, "Hello")
            self.assertNotIn(secret, key)
            self.assertEqual(len(key), 64)
            self.assertNotEqual(rc.make_key("engine", "other", "Hello"), key)
            cache.put(key, "你好")
            cache.flush()
            path = tmp / "unit.json"
            self.assertTrue(path.is_file())
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn(secret, raw)
            loaded = rc.ResultCache("unit")
            self.assertEqual(loaded.get(key), "你好")

    def test_turning_off_persist_removes_the_file(self):
        with isolated_cache(persist=True) as (rc, tmp, cfg):
            cache = rc.ResultCache("unit")
            cache.put("k", "值")
            cache.flush()
            self.assertTrue((tmp / "unit.json").is_file())
            cache.configure(persistent=False)
            self.assertFalse((tmp / "unit.json").exists())
            self.assertEqual(cache.get("k"), "值", "内存缓存本次会话仍可用")

    def test_clear_all_only_removes_our_own_files(self):
        with isolated_cache(persist=True) as (rc, tmp, cfg):
            cache = rc.get_cache("unit")
            cache.put("k", "值")
            cache.flush()
            (tmp / "keep.json").write_text('{"user": "data"}', encoding="utf-8")
            (tmp / "unit.json.tmp").write_text("{}", encoding="utf-8")
            removed = rc.clear_all()
            self.assertEqual(removed, 1)
            self.assertTrue((tmp / "keep.json").is_file())
            self.assertFalse((tmp / "unit.json").exists())
            self.assertFalse((tmp / "unit.json.tmp").exists())
            self.assertIsNone(cache.get("k"))

    def test_apply_settings_takes_effect_without_restart(self):
        with isolated_cache() as (rc, tmp, cfg):
            cache = rc.get_cache("unit")
            cache.put("k", "值")
            cfg["result_cache_enabled"] = False
            cfg["result_cache_max_entries"] = 200
            cfg["result_cache_ttl_days"] = 1
            rc.apply_settings()
            self.assertIsNone(cache.get("k"))
            self.assertFalse(cache.stats()["enabled"])
            self.assertEqual(cache.stats()["ttl_days"], 1)

    def test_concurrent_put_get_is_safe_and_bounded(self):
        with isolated_cache() as (rc, tmp, cfg):
            cache = rc.ResultCache("unit", max_entries=64)
            errors = []

            def worker(n):
                try:
                    for i in range(40):
                        cache.put(f"k{n}-{i}", f"v{n}-{i}")
                        cache.get(f"k{n % 3}-{i}")
                except Exception as exc:      # pragma: no cover
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(n,))
                       for n in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            self.assertLessEqual(cache.stats()["size"], 64)


class TranslationCacheTests(unittest.TestCase):
    """译文缓存：加速重复内容，但绝不固化失败 / 原文。"""

    def setUp(self):
        self.tr = _load_translator()

    def test_repeated_text_only_calls_engine_once(self):
        with isolated_cache() as (rc, tmp, cfg):
            with mock.patch.object(self.tr, "user_config", {}), \
                 mock.patch.object(self.tr, "translate_with_failover",
                                   return_value=("你好", "bing_free")) as call:
                eng = self.tr.Translator('auto', 'zh-CN', 'bing_free')
                self.assertEqual(eng.translate("Hello"), "你好")
                self.assertEqual(eng.translate("Hello"), "你好")
                self.assertEqual(call.call_count, 1)
                self.assertEqual(rc.get_cache("translation").stats()["hits"], 1)

    def test_lang_engine_and_credentials_partition_the_cache(self):
        with isolated_cache() as (rc, tmp, cfg):
            with mock.patch.object(self.tr, "translate_with_failover",
                                   return_value=("你好", "bing_free")) as call:
                engines = [
                    self.tr.Translator('auto', 'zh-CN', 'bing_free',
                                       config_override={'bing_azure_key': 'K1'}),
                    self.tr.Translator('auto', 'zh-CN', 'bing_free',
                                       config_override={'bing_azure_key': 'K2'}),
                    self.tr.Translator('auto', 'en', 'bing_free',
                                       config_override={'bing_azure_key': 'K1'}),
                    self.tr.Translator('auto', 'zh-CN', 'mymemory_free',
                                       config_override={}),
                ]
                for eng in engines:
                    eng.translate("Hello")
                self.assertEqual(call.call_count, 4)

    def test_failure_empty_and_echo_are_not_cached(self):
        scenarios = [
            ("失败", RuntimeError("接口挂了"), True),
            ("空译文", ("", "bing_free"), False),
            ("原文回显", ("Hello", "bing_free"), False),
        ]
        for label, outcome, raises in scenarios:
            with self.subTest(label=label), isolated_cache() as (rc, tmp, cfg):
                with mock.patch.object(self.tr, "user_config", {}), \
                     mock.patch.object(self.tr, "translate_with_failover",
                                       side_effect=[outcome,
                                                    ("你好", "bing_free")]) as call:
                    eng = self.tr.Translator('auto', 'zh-CN', 'bing_free')
                    if raises:
                        with self.assertRaises(self.tr.TranslationError):
                            eng.translate("Hello")
                    else:
                        eng.translate("Hello")
                    self.assertEqual(eng.translate("Hello"), "你好")
                    self.assertEqual(call.call_count, 2,
                                     f"{label} 不该被缓存")

    def test_connection_test_never_reads_cache(self):
        with isolated_cache() as (rc, tmp, cfg):
            with mock.patch.object(self.tr, "translate_with_failover",
                                   return_value=("你好", "bing_free")) as call:
                for _ in range(2):
                    ok, _msg = self.tr.test_translation(
                        api_type="bing_free", keys={})
                    self.assertTrue(ok)
                self.assertEqual(call.call_count, 2)


class PolishCacheTests(unittest.TestCase):
    """润色缓存：按行命中 + 批内去重，测试润色仍然真实请求。"""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import ai_polish
        self.ap = ai_polish

    def _settings(self, **extra):
        base = {"ai_polish_provider": "pollinations",
                "ai_polish_failover": False}
        base.update(extra)
        return base

    def test_duplicate_lines_requested_once_then_all_hit(self):
        with isolated_cache() as (rc, tmp, cfg):
            fake = mock.Mock(side_effect=lambda texts, settings:
                             ["润色:" + t for t in texts])
            with mock.patch.object(self.ap, "_polish_chunk", fake):
                out = self.ap.polish_lines(
                    ["A", "A", "B"], settings=self._settings())
                self.assertEqual(out, ["润色:A", "润色:A", "润色:B"])
                self.assertEqual(fake.call_count, 1)
                self.assertEqual(fake.call_args[0][0], ["A", "B"])
                out2 = self.ap.polish_lines(
                    ["A", "B"], settings=self._settings())
                self.assertEqual(out2, ["润色:A", "润色:B"])
                self.assertEqual(fake.call_count, 1)

    def test_progress_still_counts_every_line(self):
        with isolated_cache() as (rc, tmp, cfg):
            with mock.patch.object(self.ap, "_polish_chunk",
                                   side_effect=lambda t, s: ["P:" + x for x in t]):
                seen = []
                self.ap.polish_lines(["A", "A", "B"],
                                     settings=self._settings(),
                                     progress_cb=lambda d, t: seen.append((d, t)))
                self.assertEqual(seen[-1], (3, 3))

    def test_empty_polish_keeps_original_and_is_not_cached(self):
        with isolated_cache() as (rc, tmp, cfg):
            fake = mock.Mock(return_value=[""])
            with mock.patch.object(self.ap, "_polish_chunk", fake):
                self.assertEqual(
                    self.ap.polish_lines(["A"], settings=self._settings()),
                    ["A"])
                self.ap.polish_lines(["A"], settings=self._settings())
                self.assertEqual(fake.call_count, 2)

    def test_different_credentials_do_not_share_result(self):
        with isolated_cache() as (rc, tmp, cfg):
            fake = mock.Mock(side_effect=lambda t, s: ["P:" + x for x in t])
            with mock.patch.object(self.ap, "_polish_chunk", fake):
                self.ap.polish_lines(
                    ["A"], settings=self._settings(ai_polish_api_key="K1"))
                self.ap.polish_lines(
                    ["A"], settings=self._settings(ai_polish_api_key="K2"))
                self.assertEqual(fake.call_count, 2)

    def test_polish_test_bypasses_cache(self):
        with isolated_cache() as (rc, tmp, cfg):
            fake = mock.Mock(side_effect=lambda t, s: ["润色完成"])
            with mock.patch.object(self.ap, "_polish_chunk", fake):
                self.ap.test_polish(self._settings())
                self.ap.test_polish(self._settings())
                self.assertEqual(fake.call_count, 2)


class OCRCacheTests(unittest.TestCase):
    """OCR 缓存：一张图只跑一次推理，两个对外接口共用。"""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import ocr_recognizer
        self.ocr = ocr_recognizer

    def _easy(self, readtext_result):
        inst = self.ocr.EasyOCRRecognizer.__new__(
            self.ocr.EasyOCRRecognizer)
        inst.languages = ["en"]
        inst.reader = mock.Mock()
        inst.reader.readtext.return_value = readtext_result
        return inst

    def test_easy_text_and_blocks_share_one_readtext(self):
        with isolated_cache() as (rc, tmp, cfg):
            img = tmp / "frame.png"
            img.write_bytes(b"same-image-bytes")
            inst = self._easy([
                ([[0, 0], [50, 0], [50, 20], [0, 20]], "Hello", 0.9),
                ([[0, 40], [60, 40], [60, 60], [0, 60]], "World", 0.8),
            ])
            with mock.patch.object(self.ocr, "_imread_unicode",
                                   return_value=object()):
                text = inst.recognize(str(img))
                blocks = inst.recognize_blocks(str(img))
                words = inst.recognize_words(str(img))
            self.assertEqual(inst.reader.readtext.call_count, 1)
            self.assertIn("Hello", text)
            self.assertIn("World", text)
            self.assertEqual([b["text"] for b in blocks], ["Hello", "World"])
            self.assertEqual([w["text"] for w in words], ["Hello", "World"])

    def test_different_image_is_recognized_again(self):
        with isolated_cache() as (rc, tmp, cfg):
            a, b = tmp / "a.png", tmp / "b.png"
            a.write_bytes(b"image-a")
            b.write_bytes(b"image-b")
            inst = self._easy([
                ([[0, 0], [10, 0], [10, 10], [0, 10]], "Hi", 0.9)])
            with mock.patch.object(self.ocr, "_imread_unicode",
                                   return_value=object()):
                inst.recognize(str(a))
                inst.recognize(str(b))
            self.assertEqual(inst.reader.readtext.call_count, 2)

    def test_disabled_cache_preserves_original_behaviour(self):
        with isolated_cache() as (rc, tmp, cfg):
            cfg["result_cache_enabled"] = False
            img = tmp / "a.png"
            img.write_bytes(b"image-a")
            inst = self._easy([
                ([[0, 0], [10, 0], [10, 10], [0, 10]], "Hi", 0.9)])
            with mock.patch.object(self.ocr, "_imread_unicode",
                                   return_value=object()):
                inst.recognize(str(img))
                inst.recognize_blocks(str(img))
            self.assertEqual(inst.reader.readtext.call_count, 2)


class ConfigSnapshotTests(unittest.TestCase):
    """配置读取缓存：省掉重复磁盘 IO，但设置修改必须立刻可见。"""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import config
        self.config = config

    def test_external_edit_and_save_are_visible_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "user_config.json"
            global_cfg = {}
            with mock.patch.object(self.config, "CONFIG_FILE", str(path)), \
                 mock.patch.object(self.config, "APP_DIR", pathlib.Path(tmp)), \
                 mock.patch.object(self.config, "user_config", global_cfg):
                self.config.invalidate_config_snapshot()
                self.assertEqual(self.config.load_user_config(), {})
                path.write_text('{"x": 1}', encoding="utf-8")
                self.assertEqual(self.config.load_user_config(), {"x": 1})
                path.write_text('{"x": 2, "y": 3}', encoding="utf-8")
                self.assertEqual(self.config.load_user_config(),
                                 {"x": 2, "y": 3})
                self.assertTrue(self.config.save_user_config({"w": 4}))
                self.assertEqual(self.config.load_user_config(),
                                 {"x": 2, "y": 3, "w": 4})

    def test_returned_dict_is_a_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "user_config.json"
            path.write_text('{"x": 1}', encoding="utf-8")
            with mock.patch.object(self.config, "CONFIG_FILE", str(path)):
                self.config.invalidate_config_snapshot()
                got = self.config.load_user_config()
                got["injected"] = True
                self.assertNotIn("injected", self.config.load_user_config())

    def test_unchanged_file_is_not_reopened(self):
        import builtins
        real_open = builtins.open
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "user_config.json"
            path.write_text('{"x": 1}', encoding="utf-8")
            with mock.patch.object(self.config, "CONFIG_FILE", str(path)):
                self.config.invalidate_config_snapshot()
                with mock.patch("builtins.open", wraps=real_open) as opener:
                    for _ in range(5):
                        self.assertEqual(self.config.load_user_config(),
                                         {"x": 1})
                self.assertEqual(opener.call_count, 1,
                                 "文件没变时不应反复开文件 / 解析 JSON")

    def test_cache_settings_merge_defaults(self):
        import config
        defaults = config.DEFAULT_CACHE_SETTINGS
        self.assertEqual(set(defaults), {
            "result_cache_enabled", "result_cache_max_entries",
            "result_cache_ttl_days", "result_cache_persist"})
        self.assertTrue(defaults["result_cache_enabled"])
        merged = config.merged_settings(defaults, {"result_cache_enabled": False})
        self.assertFalse(merged["result_cache_enabled"])


class CacheWiringContracts(unittest.TestCase):
    """源码契约：接线、设置页、安装包排除、文档，防止后续改动悄悄拆掉。"""

    def test_translator_wiring(self):
        src = _src("translator.py")
        self.assertIn("from result_cache import get_cache, make_key", src)
        self.assertIn('get_cache("translation")', src)
        self.assertIn("use_cache=False", src)
        self.assertIn("result.strip() != text.strip()", src)
        self.assertIn("_CACHE_CREDENTIAL_KEYS", src)

    def test_polish_wiring(self):
        src = _src("ai_polish.py")
        self.assertIn("from result_cache import get_cache, make_key", src)
        self.assertIn('get_cache("polish")', src)
        self.assertIn("use_cache=False", src)
        self.assertIn("pending.setdefault(text, []).append(idx)", src)

    def test_ocr_wiring(self):
        src = _src("ocr_recognizer.py")
        self.assertIn("_cached_scan(", src)
        self.assertIn("_image_digest(", src)
        self.assertIn("def _lines(self, image_path):", src)
        self.assertIn("def _results(self, image_path):", src)
        self.assertEqual(src.count("recognize_async(bitmap).get()"), 1,
                         "Windows OCR 只应有一处实际识别")
        self.assertEqual(src.count("reader.readtext(img)"), 1,
                         "EasyOCR 只应有一处实际推理")

    def test_settings_page_exposes_all_controls(self):
        src = _src("pages/settings_page.py")
        self.assertIn("self._build_cache_section,", src)
        self.assertIn("def _build_cache_section(self)", src)
        for name in ("cache_enabled_check", "cache_entries_combo",
                     "cache_ttl_combo", "cache_persist_check",
                     "cache_stats_label"):
            self.assertIn(name, src)
        self.assertIn("result_cache.apply_settings()", src)
        self.assertIn("result_cache.clear_all()", src)
        self.assertIn("result_cache.cache_dir()", src)
        # 说明文案不写死本机绝对路径
        self.assertNotIn(r"C:\Users", src)

    def test_settings_save_and_restore_cover_cache_keys(self):
        src = _src("pages/settings_page.py")
        save = src.split("    def _save(self):", 1)[1].split(
            "    def _restore_defaults", 1)[0]
        restore = src.split("    def _restore_defaults", 1)[1]
        for key in ("result_cache_enabled", "result_cache_max_entries",
                    "result_cache_ttl_days", "result_cache_persist"):
            self.assertIn(key, save)
        self.assertIn("DEFAULT_CACHE_SETTINGS", restore)
        self.assertIn("cache_entries_combo.setCurrentIndex", restore)

    def test_runtime_data_and_installer_exclude_cache(self):
        import app_paths
        import build_installer
        self.assertIn("cache", app_paths.RUNTIME_DATA)
        self.assertIn("cache", build_installer.RUNTIME_DATA)
        iss = _src("installer.iss")
        self.assertIn("cache\\*", iss)
        self.assertNotIn("[InstallDelete]", iss)
        self.assertNotIn("[UninstallDelete]", iss)
        self.assertIn("cache/", _src(".gitignore"))
        self.assertIn('"result_cache"', _src("build_exe.py"))

    def test_readme_documents_cache(self):
        readme = _src("README.md")
        self.assertIn("## 性能与结果缓存", readme)
        self.assertIn("只缓存成功结果", readme)
        self.assertIn("覆盖升级", readme)

    def test_cache_module_never_writes_absolute_path_in_ui_text(self):
        src = _src("result_cache.py")
        self.assertIn('CACHE_DIR_NAME = "cache"', src)
        self.assertIn("atexit.register(flush_all)", src)
        self.assertIn("os.replace(tmp, path)", src)


if __name__ == "__main__":
    unittest.main()
