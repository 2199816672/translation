#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 14 轮：免费 AI 润色通道自动故障切换 + 通道扩充。

用户要求：免费 AI 润色也像免费翻译那样「挂了自动换其它免费通道」，
并尽可能多接几个免费通道。全部测试用离线桩，不发任何真实网络请求。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ai_polish as ap
import config


def _src(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


class PolishFailoverTests(unittest.TestCase):
    """首选挂掉要自动换；用户凭证绝不串到别的通道。"""

    def setUp(self):
        self.orig = dict(ap._PROVIDERS)
        self.behaviors = {}
        self.calls = []
        ap._PROVIDERS.clear()
        for name in list(ap.FREE_POLISH_ORDER) + ["custom"]:
            ap._PROVIDERS[name] = self._stub(name)
        ap.reset_polish_failover()
        ap._last_polish_provider["name"] = None
        self.addCleanup(self._restore)

    def _restore(self):
        ap._PROVIDERS.clear()
        ap._PROVIDERS.update(self.orig)
        ap.reset_polish_failover()
        ap._last_polish_provider["name"] = None

    def _stub(self, name):
        def fn(prompt, settings):
            self.calls.append((name, dict(settings)))
            behavior = self.behaviors.get(name)
            if behavior is not None:
                return behavior(prompt, settings)
            return "润色结果"
        return fn

    @staticmethod
    def _fail(msg="通道不可用"):
        def f(prompt, settings):
            raise ap.PolishError(msg)
        return f

    def test_preferred_used_first(self):
        text, engine = ap.polish_with_failover(
            "p", {"ai_polish_provider": "pollinations"},
            preferred="pollinations")
        self.assertEqual((text, engine), ("润色结果", "pollinations"))
        self.assertEqual(self.calls[0][0], "pollinations")

    def test_limited_public_switches_to_backup_endpoint(self):
        self.behaviors["pollinations"] = self._fail("触发限流（402）")
        _text, engine = ap.polish_with_failover(
            "p", {"ai_polish_provider": "pollinations"},
            preferred="pollinations")
        self.assertEqual(engine, "pollinations_text")
        self.assertEqual([c[0] for c in self.calls],
                         ["pollinations", "pollinations_text"])
        self.assertIn("pollinations", ap.failed_polish_providers())

    def test_configured_free_key_channel_has_priority(self):
        # 公共通道限流很频繁，有免费 Key 的官方服务应该优先于公共备用端点
        self.behaviors["pollinations"] = self._fail("402 限流")
        settings = {"ai_polish_provider": "pollinations",
                    "ai_polish_groq_key": "gsk_test"}
        _text, engine = ap.polish_with_failover(
            "p", settings, preferred="pollinations")
        self.assertEqual(engine, "free_quota:groq")
        self.assertEqual([c[0] for c in self.calls],
                         ["pollinations", "free_quota:groq"])

    def test_channels_without_credentials_are_skipped(self):
        self.behaviors["pollinations"] = self._fail("402 限流")
        ap.polish_with_failover("p", {}, preferred="pollinations")
        tried = [c[0] for c in self.calls]
        self.assertEqual(tried, ["pollinations", "pollinations_text"])
        self.assertTrue(all(not n.startswith("free_quota:") for n in tried))
        self.assertNotIn("deepseek_web", tried)
        self.assertNotIn("doubao_web", tried)

    def test_disabled_only_tries_preferred(self):
        self.behaviors["pollinations"] = self._fail("402 限流")
        with self.assertRaises(ap.PolishError):
            ap.polish_with_failover("p", {}, preferred="pollinations",
                                    enabled=False)
        self.assertEqual([c[0] for c in self.calls], ["pollinations"])

    def test_custom_never_switched(self):
        # 用户自有 API 可能是付费的，不能把文本自动转给别的通道
        self.behaviors["custom"] = self._fail("自有接口挂了")
        with self.assertRaises(ap.PolishError):
            ap.polish_with_failover(
                "p", {"ai_polish_provider": "custom"},
                preferred="custom", enabled=True)
        self.assertEqual([c[0] for c in self.calls], ["custom"])

    def test_sticky_prefers_known_good(self):
        ap.reset_polish_failover("pollinations_text")
        ap.polish_with_failover("p", {}, preferred="pollinations")
        self.assertEqual(self.calls[0][0], "pollinations_text")

    def test_reset_clears_sticky_and_failed(self):
        ap.reset_polish_failover("pollinations_text")
        ap._polish_failed_until["pollinations"] = 1e18
        self.assertIn("pollinations", ap.failed_polish_providers())
        ap.reset_polish_failover()
        self.assertIsNone(ap.current_polish_provider())
        self.assertEqual(ap.failed_polish_providers(), set())

    def test_all_free_channels_fail_raises(self):
        self.behaviors["pollinations"] = self._fail("402")
        self.behaviors["pollinations_text"] = self._fail("402")
        with self.assertRaises(ap.PolishError) as ctx:
            ap.polish_with_failover("p", {}, preferred="pollinations")
        self.assertIn("均不可用", str(ctx.exception))

    def test_failover_flag_does_not_mutate_caller_settings(self):
        settings = {"ai_polish_provider": "pollinations"}
        ap.polish_with_failover("p", settings, preferred="pollinations")
        self.assertNotIn("_in_failover", settings)
        self.assertTrue(any(s.get("_in_failover") for _, s in self.calls))

    def test_last_provider_is_recorded(self):
        self.behaviors["pollinations"] = self._fail("402")
        ap.polish_with_failover("p", {}, preferred="pollinations")
        self.assertEqual(ap.last_polish_provider(), "pollinations_text")
        self.assertEqual(ap.describe_provider(ap.last_polish_provider()),
                         "免费公共通道·备用端点")

    def test_free_quota_uses_only_its_own_key(self):
        seen = []

        def fake_post(url, headers, payload, timeout=90):
            seen.append((url, headers, payload))
            return json.dumps({"choices": [
                {"message": {"content": "润色结果"}}]},
                ensure_ascii=False)

        orig = ap._http_post
        ap._http_post = fake_post
        try:
            settings = {"ai_polish_groq_key": "GROQ_SECRET",
                        "ai_polish_zhipu_key": "ZHIPU_SECRET",
                        "ai_polish_free_model": ""}
            out = ap._request_free_quota("p", settings, "groq")
            self.assertEqual(out, "润色结果")
            self.assertIn("api.groq.com", seen[0][0])
            self.assertEqual(seen[0][1]["Authorization"],
                             "Bearer GROQ_SECRET")
            self.assertNotIn("ZHIPU_SECRET", json.dumps(seen[0][1]))
            self.assertEqual(seen[0][2]["model"],
                             config.FREE_POLISH_MODELS["groq"])
        finally:
            ap._http_post = orig

    def test_free_quota_without_key_fails_clearly(self):
        with self.assertRaises(ap.PolishError) as ctx:
            ap._request_free_quota("p", {}, "zhipu")
        self.assertIn("免费 API Key", str(ctx.exception))

    def test_resolve_provider_expands_free_quota(self):
        self.assertEqual(ap.resolve_provider(
            {"ai_polish_provider": "free_quota",
             "ai_polish_free_service": "zhipu"}), "free_quota:zhipu")
        self.assertEqual(ap.resolve_provider(
            {"ai_polish_provider": "free_quota",
             "ai_polish_free_service": "unknown"}), "free_quota:groq")
        self.assertEqual(ap.resolve_provider(
            {"ai_polish_provider": "custom"}), "custom")


class SourceContracts(unittest.TestCase):
    """源码级契约：把新通道与设置页接线锁住，防止以后被改回去。"""

    def test_eight_free_channels_stay_in_sync(self):
        self.assertEqual(len(ap.FREE_POLISH_ORDER), 8)
        for name in ap.FREE_POLISH_ORDER:
            self.assertIn(name, ap._PROVIDERS, name)
        self.assertEqual(set(config.FREE_POLISH_SERVICES),
                         {"groq", "openrouter", "zhipu", "siliconflow"})
        for service in config.FREE_POLISH_SERVICES:
            self.assertIn(f"free_quota:{service}", ap.FREE_POLISH_ORDER)
            self.assertIn(service, config.FREE_POLISH_ENDPOINTS)
            self.assertIn(service, config.FREE_POLISH_MODELS)
            self.assertIn(f"ai_polish_{service}_key",
                          config.DEFAULT_AI_POLISH_SETTINGS)

    def test_pollinations_backup_endpoint_is_real(self):
        src = _src("ai_polish.py")
        for token in ("def _http_get", "def _request_pollinations_text",
                      "text.pollinations.ai/", "allow_wait",
                      "_in_failover", "FAILED_POLISH_TTL = 90",
                      "_record_polish_switch", "def polish_with_failover"):
            self.assertIn(token, src, token)
        # 两个公共端点共享限流，文档必须说清不是独立配额
        self.assertIn("共享 IP 级限流", src)

    def test_failover_default_and_config(self):
        self.assertTrue(config.DEFAULT_AI_POLISH_FAILOVER)
        self.assertTrue(config.DEFAULT_AI_POLISH_SETTINGS["ai_polish_failover"])
        self.assertEqual(set(config.AI_POLISH_PROVIDERS.values()),
                         {"pollinations", "pollinations_text", "free_quota",
                          "deepseek_web", "doubao_web", "custom"})

    def test_settings_page_wiring(self):
        src = _src("pages/settings_page.py")
        for token in ("AI_POLISH_FREE_SERVICES", "FREE_POLISH_SERVICES",
                      "polish_free_service_combo", "polish_free_key",
                      "polish_free_model", "polish_failover_check",
                      "_store_current_free_key",
                      "_on_polish_free_service_changed",
                      "test_polish_with_provider",
                      "reset_polish_failover()",
                      "页面顺序必须与 AI_POLISH_PROVIDERS"):
            self.assertIn(token, src, token)
        self.assertIn("ai_polish_failover", src)

    def test_readme_documents_free_channels(self):
        src = _src("README.md")
        for token in ("免费额度 API", "Groq", "OpenRouter", "智谱",
                      "硅基流动", "AI 润色"):
            self.assertIn(token, src, token)


if __name__ == "__main__":
    unittest.main()
