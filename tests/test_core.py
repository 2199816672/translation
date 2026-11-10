#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心纯逻辑回归测试：不依赖 Qt、网络、OCR 模型或本机安装环境。

覆盖此前完全空白的业务逻辑层：文本分段/折行、字幕解析与互转、
配置容错、运行时路径解析。CI 里零第三方依赖即可运行。

    python -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app_paths  # noqa: E402
import config  # noqa: E402
import subtitle as sub  # noqa: E402
import text_segment as ts  # noqa: E402


class TextSegmentTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(ts.split_paragraphs(""), [])
        self.assertEqual(ts.split_paragraphs(None), [])
        self.assertEqual(ts.split_sentences(""), [])
        self.assertEqual(ts.chunk_text(""), [])
        self.assertEqual(ts.wrap_sentences("", 10, len), [])

    def test_merge_wrapped_lines_but_keep_blank_paragraphs(self):
        text = "First line\ncontinued here\n\nSecond paragraph."
        self.assertEqual(ts.split_paragraphs(text),
                         ["First line continued here", "Second paragraph."])

    def test_cjk_join_without_extra_space(self):
        self.assertEqual(ts.split_paragraphs("中文第一行\n第二行。"),
                         ["中文第一行第二行。"])

    def test_heading_starts_new_paragraph(self):
        self.assertEqual(ts.split_paragraphs("1. first item\n- second item"),
                         ["1. first item", "- second item"])

    def test_flatten_removes_linebreaks(self):
        flat = ts.flatten_text("line one\nline two\n\nnext para")
        self.assertNotIn("\n", flat)
        self.assertIn("line one", flat)
        self.assertIn("next para", flat)

    def test_split_sentences_basic(self):
        self.assertEqual(ts.split_sentences("Hello world. How are you? Fine!"),
                         ["Hello world.", "How are you?", "Fine!"])

    def test_split_sentences_keeps_abbreviations_and_decimals(self):
        self.assertEqual(ts.split_sentences("Mr. Smith paid 3.5 dollars. OK."),
                         ["Mr. Smith paid 3.5 dollars.", "OK."])

    def test_wrap_never_exceeds_width(self):
        lines = ts.wrap_by_width("hello world again and again", 10, len)
        self.assertTrue(lines)
        self.assertTrue(all(len(line) <= 10 for line in lines))
        self.assertEqual("".join(lines).replace(" ", ""),
                         "helloworldagainandagain")

    def test_wrap_preserves_blank_lines_and_hard_cuts_long_word(self):
        lines = ts.wrap_by_width("aaa\n\nbbb", 10, len)
        self.assertIn("", lines)
        long_lines = ts.wrap_by_width("abcdefghijklmnop", 5, len)
        self.assertTrue(all(len(line) <= 5 for line in long_lines))
        self.assertEqual("".join(long_lines), "abcdefghijklmnop")

    def test_chunk_text_respects_hard_limit(self):
        chunks = ts.chunk_text(("This is a sentence. " * 40).strip(), max_len=80)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 80 for chunk in chunks))
        self.assertEqual(ts.chunk_text("short"), ["short"])


class SubtitleTimestampTests(unittest.TestCase):
    def test_parse_supported_timestamp_forms(self):
        self.assertEqual(sub._parse_ts("00:01:02,500"), 62500)
        self.assertEqual(sub._parse_ts("01:02.5"), 62500)
        self.assertEqual(sub._parse_ts("0:00:01.20"), 1200)
        self.assertEqual(sub._parse_ts("00:00:00,000"), 0)

    def test_parse_invalid_timestamp_raises(self):
        with self.assertRaises(ValueError):
            sub._parse_ts("not-a-time")

    def test_format_roundtrip(self):
        self.assertEqual(sub._fmt_srt(62500), "00:01:02,500")
        self.assertEqual(sub._fmt_vtt(62500), "00:01:02.500")
        self.assertEqual(sub._fmt_ass(62500), "0:01:02.50")
        self.assertEqual(sub._fmt_lrc(62500), "01:02.50")
        self.assertEqual(sub._parse_ts(sub._fmt_srt(62500)), 62500)
        self.assertEqual(sub._parse_ts(sub._fmt_vtt(62500)), 62500)


class SubtitleFormatTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            {"start": 1000, "end": 2500, "text": "Hello"},
            {"start": 3000, "end": 4500, "text": "World\nagain"},
        ]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _parse(self, name, content):
        path = self.dir / name
        path.write_text(content, encoding="utf-8")
        return sub.parse_subtitle(str(path))

    def test_parse_srt(self):
        parsed = self._parse("a.srt",
                             "1\n00:00:01,000 --> 00:00:02,500\nHello\n\n"
                             "2\n00:00:03,000 --> 00:00:04,500\nWorld\n")
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0], {"start": 1000, "end": 2500, "text": "Hello"})
        self.assertEqual(parsed[1]["start"], 3000)

    def test_parse_vtt_strips_header(self):
        parsed = self._parse("a.vtt",
                             "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHello\n")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["text"], "Hello")

    def test_parse_lrc(self):
        parsed = self._parse("a.lrc", "[00:01.00]Hello\n[00:03.00]World\n")
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["start"], 1000)

    def test_serialize_contains_correct_timeline(self):
        srt = sub.serialize_subtitle(self.entries, ".srt")
        self.assertIn("00:00:01,000 --> 00:00:02,500", srt)
        self.assertIn("1\n", srt)
        self.assertIn("WEBVTT", sub.serialize_subtitle(self.entries, ".vtt"))
        self.assertIn("[00:01.00]Hello",
                      sub.serialize_subtitle(self.entries, ".lrc"))
        self.assertIn("Dialogue:", sub.serialize_subtitle(self.entries, ".ass"))
        self.assertIn("Dialogue:", sub.serialize_subtitle(self.entries, ".ssa"))

    def test_serialize_rejects_unknown_format(self):
        with self.assertRaises(ValueError):
            sub.serialize_subtitle(self.entries, ".txt")

    def test_srt_roundtrip_preserves_entries(self):
        text = sub.serialize_subtitle(self.entries, ".srt")
        parsed = self._parse("round.srt", text)
        self.assertEqual(len(parsed), len(self.entries))
        for before, after in zip(self.entries, parsed):
            self.assertEqual(before["start"], after["start"])
            self.assertEqual(before["end"], after["end"])
            self.assertEqual(before["text"], after["text"])

    def test_compose_export_modes(self):
        src = sub.compose_entries(self.entries, [], "source")
        self.assertEqual(src[0]["text"], "Hello")
        tgt = sub.compose_entries(self.entries, ["你好", "世界"], "target")
        self.assertEqual(tgt[0]["text"], "你好")
        # 缺译文时回退原文，不能出现空字幕
        partial = sub.compose_entries(self.entries, ["你好"], "target")
        self.assertEqual(partial[1]["text"], "World\nagain")
        both = sub.compose_entries(self.entries[:1], ["你好"], "both")
        self.assertIn("Hello", both[0]["text"])
        self.assertIn("你好", both[0]["text"])
        lrc_both = sub.compose_entries(self.entries[:1], ["你好"], "both", ".lrc")
        self.assertEqual(lrc_both[0]["text"], "Hello / 你好")

    def test_compose_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            sub.compose_entries(self.entries, [], "mixed")

    def test_plain_text_joins_multiline(self):
        self.assertEqual(sub.subtitle_to_plain_text(self.entries),
                         "Hello\nWorld again")


class ConfigRobustnessTests(unittest.TestCase):
    def test_coerce_bad_values_fall_back_to_default(self):
        self.assertEqual(config._coerce("abc", 5), 5)
        self.assertEqual(config._coerce("12", 5), 12)
        self.assertEqual(config._coerce(True, 5), 5)
        self.assertEqual(config._coerce(float("nan"), 0.5), 0.5)
        self.assertEqual(config._coerce(float("inf"), 0.5), 0.5)
        self.assertEqual(config._coerce({"a": 1}, "x"), "x")
        self.assertEqual(config._coerce("wrong", ["a"]), ["a"])

    def test_coerce_boolean_strings(self):
        self.assertTrue(config._coerce("yes", False))
        self.assertTrue(config._coerce("True", False))
        self.assertFalse(config._coerce("off", True))

    def test_merged_settings_ignores_unknown_keys(self):
        merged = config.merged_settings(
            {"a": 1, "b": True}, {"a": "7", "b": "off", "extra": 1})
        self.assertEqual(merged, {"a": 7, "b": False})

    def test_merged_settings_uses_defaults_for_missing(self):
        self.assertEqual(config.merged_settings({"a": 1, "b": 2}, {"a": 9}),
                         {"a": 9, "b": 2})

    def test_broken_config_is_backed_up_not_silently_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user_config.json"
            path.write_text('{"broken": ', encoding="utf-8")
            with mock.patch.object(config, "CONFIG_FILE", str(path)):
                self.assertEqual(config.load_user_config(), {})
            self.assertTrue(path.with_name("user_config.json.broken").is_file())
            self.assertEqual(path.read_text(encoding="utf-8"), '{"broken": ')

    def test_version_and_repo_constants_are_well_formed(self):
        self.assertRegex(config.APP_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertIn("/", config.GITHUB_REPO)


class AppPathTests(unittest.TestCase):
    def test_source_mode_is_not_frozen(self):
        self.assertFalse(app_paths.is_frozen())

    def test_runtime_data_is_separated_from_resources(self):
        self.assertIn("user_config.json", app_paths.RUNTIME_DATA)
        self.assertIn("screenshots", app_paths.RUNTIME_DATA)
        self.assertTrue(app_paths.resource_path("assets/app_icon.png").is_file())

    def test_user_data_paths_live_under_app_dir(self):
        root = app_paths.APP_DIR.resolve()
        self.assertEqual(app_paths.config_path().parent.resolve(), root)
        for path in (app_paths.screenshots_dir(), app_paths.models_dir()):
            self.assertTrue(path.is_dir())
            self.assertEqual(path.parent.resolve(), root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
