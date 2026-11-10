#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键升级链路回归测试：全程用桩，不联网、不弹窗、不启动安装程序。

运行：
    python tests/test_upgrade.py
或（离屏 Qt 相关用例会自动启用）：
    QT_QPA_PLATFORM=offscreen python tests/test_upgrade.py
"""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import updater  # noqa: E402
import build_installer  # noqa: E402
import config  # noqa: E402
import app_paths  # noqa: E402
from subprocess_util import pip_cmd  # noqa: E402

SETUP = "TranslationTool-Setup-v1.5.0.exe"


def _release_payload(digest=True, asset=True, body=""):
    assets = []
    if asset:
        a = {
            "name": SETUP,
            "browser_download_url": f"https://github.com/2199816672/translation/releases/download/v1.5.0/{SETUP}",
            "size": 12345678,
            "id": 987654,
        }
        if digest:
            a["digest"] = "sha256:" + "a" * 64
        assets.append(a)
    return json.dumps({
        "tag_name": "v1.5.0", "name": "v1.5.0", "body": body,
        "html_url": "https://github.com/2199816672/translation/releases/tag/v1.5.0",
        "assets": assets,
    }).encode("utf-8")


class _FakeResponse(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class VersionTests(unittest.TestCase):
    def test_version_compare(self):
        self.assertTrue(updater.is_newer("1.4.1", "1.4.0"))
        self.assertTrue(updater.is_newer("v1.5", "1.4.9"))
        self.assertTrue(updater.is_newer("1.4.0.1", "1.4.0"))
        self.assertFalse(updater.is_newer("1.2.0", "1.4.0"))
        self.assertFalse(updater.is_newer("", "1.4.0"))
        self.assertEqual(updater.version_tuple("1.2"), (1, 2, 0, 0))

    def test_setup_name_whitelist(self):
        self.assertTrue(updater.SETUP_NAME_RE.match(SETUP))
        self.assertTrue(updater.SETUP_NAME_RE.match(
            "TranslationTool-Setup-v2.0.0-beta.1.exe"))
        for bad in ("TranslationTool-v1.5.0-win64.zip",
                    "evil-Setup-v1.5.0.exe", "../../setup.exe",
                    "TranslationTool-Setup-v1.5.0.exe.bat"):
            self.assertIsNone(updater.SETUP_NAME_RE.match(bad), bad)


class UrlSafetyTests(unittest.TestCase):
    def test_api_host_only(self):
        for good in ("https://api.github.com/repos/x/releases/latest",
                     "https://github.com/x/y/releases/download/v1/a.exe",
                     "https://objects.githubusercontent.com/abc"):
            updater._assert_safe_url(good)
        for bad in ("http://github.com/x", "https://evilgithub.com/x",
                    "https://github.com.evil.com/x", "https://127.0.0.1/x",
                    "file:///C:/windows/x"):
            with self.assertRaises(updater.UpdateError, msg=bad):
                updater._assert_safe_url(bad)

    def test_notes_sha256_fallback(self):
        body = f"## 更新\nsha256: {'b' * 64}  {SETUP}\n"
        self.assertEqual(updater.parse_sha256_from_notes(body, SETUP), "b" * 64)


class FetchReleaseTests(unittest.TestCase):
    def test_parse_asset_and_digest(self):
        opener = lambda req, timeout=None: _FakeResponse(_release_payload())
        with mock.patch.object(updater, "_validate_api_url"), \
             mock.patch.object(updater, "auth_headers", return_value={}):
            info = updater.fetch_latest_release(opener=opener)
        self.assertEqual(info.version, "1.5.0")
        self.assertTrue(info.has_installer)
        self.assertEqual(info.setup_size, 12345678)
        self.assertEqual(info.sha256, "a" * 64)
        self.assertEqual(info.setup_asset_id, 987654)

    def test_sha256_from_notes_when_no_digest(self):
        body = f"sha256: {'c' * 64}  {SETUP}"
        opener = lambda req, timeout=None: _FakeResponse(
            _release_payload(digest=False, body=body))
        with mock.patch.object(updater, "_validate_api_url"), \
             mock.patch.object(updater, "auth_headers", return_value={}):
            info = updater.fetch_latest_release(opener=opener)
        self.assertEqual(info.sha256, "c" * 64)

    def test_no_installer_is_not_error(self):
        opener = lambda req, timeout=None: _FakeResponse(
            _release_payload(asset=False))
        with mock.patch.object(updater, "_validate_api_url"), \
             mock.patch.object(updater, "auth_headers", return_value={}):
            info = updater.fetch_latest_release(opener=opener)
        self.assertFalse(info.has_installer)

    def test_gh_cli_fallback(self):
        payload = _release_payload().decode("utf-8")
        with mock.patch.object(updater, "_validate_api_url",
                               side_effect=updater.UpdateError("offline")), \
             mock.patch.object(updater, "_payload_via_gh", return_value=payload), \
             mock.patch.object(updater, "auth_headers", return_value={}):
            info = updater.fetch_latest_release()
        self.assertTrue(info.has_installer)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_download_atomic_and_progress(self):
        dest = self.root / SETUP
        data = b"setup-binary-content" * 100
        opener = lambda req, timeout=None: _FakeResponse(
            data, {"Content-Length": str(len(data))})
        progress = []
        with mock.patch.object(updater, "_assert_safe_url"):
            result = updater.download_file(
                "https://github.com/x/y.exe", dest,
                progress_cb=lambda d, t: progress.append((d, t)),
                opener=opener, chunk_size=64)
        self.assertEqual(result.read_bytes(), data)
        self.assertFalse(dest.with_name(dest.name + ".part").exists())
        self.assertEqual(progress[-1][1], len(data))

    def test_chunked_response_uses_release_size(self):
        dest = self.root / SETUP
        data = b"chunked-no-length" * 20
        opener = lambda req, timeout=None: _FakeResponse(data, {})
        progress = []
        with mock.patch.object(updater, "_assert_safe_url"):
            updater.download_file(
                "https://github.com/x/y.exe", dest,
                progress_cb=lambda d, t: progress.append((d, t)),
                opener=opener, chunk_size=64, expected_size=len(data))
        self.assertEqual(dest.read_bytes(), data)
        self.assertEqual(progress[-1][1], len(data))

    def test_cancel_after_last_chunk_leaves_no_installer(self):
        dest = self.root / SETUP
        cancel = threading.Event()
        data = b"x" * 128

        def on_progress(done, total):
            if done >= len(data):
                cancel.set()

        opener = lambda req, timeout=None: _FakeResponse(data, {})
        with mock.patch.object(updater, "_assert_safe_url"):
            with self.assertRaises(updater.UpdateCancelled):
                updater.download_file(
                    "https://github.com/x/y.exe", dest,
                    progress_cb=on_progress, cancel=cancel,
                    opener=opener, chunk_size=64)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name(dest.name + ".part").exists())

    def test_cancel_removes_part(self):
        dest = self.root / SETUP
        cancel = threading.Event()
        cancel.set()
        opener = lambda req, timeout=None: _FakeResponse(b"x" * 100)
        with mock.patch.object(updater, "_assert_safe_url"):
            with self.assertRaises(updater.UpdateCancelled):
                updater.download_file("https://github.com/x/y.exe", dest,
                                      cancel=cancel, opener=opener)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name(dest.name + ".part").exists())

    def test_verify_download(self):
        dest = self.root / SETUP
        dest.write_bytes(b"abc")
        expected = updater.file_sha256(dest)
        self.assertTrue(updater.verify_download(dest, expected.upper()))
        self.assertFalse(updater.verify_download(dest, "0" * 64))
        self.assertIsNone(updater.verify_download(dest, ""))

    def test_safe_download_path(self):
        info = updater.ReleaseInfo(version="1.5.0", tag="v1.5.0", name="",
                                   body="", page_url="", setup_name=SETUP)
        path = updater.safe_download_path(info)
        self.assertEqual(path.name, SETUP)
        info.setup_name = "../../evil.exe"
        with self.assertRaises(updater.UpdateError):
            updater.safe_download_path(info)


class InstallLaunchTests(unittest.TestCase):
    def test_launch_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup = Path(tmp) / SETUP
            setup.write_bytes(b"x")
            with mock.patch.object(updater.subprocess, "Popen") as popen, \
                 mock.patch.object(updater, "github_token", return_value=""):
                updater.launch_installer(setup, restart=True)
                args = popen.call_args[0][0]
            self.assertIn("/VERYSILENT", args)
            self.assertIn("/NORESTART", args)
            self.assertIn("/RESTARTAPP=1", args)

    def test_missing_setup(self):
        with self.assertRaises(updater.UpdateError):
            updater.launch_installer(Path("C:/definitely/missing/setup.exe"))

    def test_queue_consume(self):
        updater.queue_install("C:/tmp/setup.exe")
        self.assertEqual(updater.consume_pending_install(), "C:/tmp/setup.exe")
        self.assertIsNone(updater.consume_pending_install())


class ConfigAndPathTests(unittest.TestCase):
    def test_coerce_invalid_values(self):
        defaults = {"a": 5, "b": 0.5, "c": True, "d": "x", "e": []}
        raw = {"a": "abc", "b": "xyz", "c": None, "d": {"bad": 1}, "e": None}
        merged = config.merged_settings(defaults, raw)
        self.assertEqual(merged, defaults)

    def test_resource_and_data_dir_separated(self):
        self.assertTrue(app_paths.resource_path("assets/app_icon.png").is_file())
        self.assertEqual(Path(config.CONFIG_FILE).name, "user_config.json")
        self.assertEqual(Path(config.SCREENSHOT_DIR).name, "screenshots")

    def test_pip_cmd_not_frozen(self):
        cmd, hint = pip_cmd(["soundcard"])
        self.assertFalse(hint)
        self.assertIn("-m", cmd)
        self.assertIn("pip", cmd)

    def test_build_version_source(self):
        self.assertEqual(build_installer.read_version(), config.APP_VERSION)


class InstallerSanitizeTests(unittest.TestCase):
    def test_runtime_data_moved_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "TranslationTool"
            app.mkdir()
            (app / "TranslationTool.exe").write_bytes(b"exe")
            (app / "user_config.json").write_text("{}", encoding="utf-8")
            (app / "screenshots").mkdir()
            (app / "screenshots" / "a.png").write_bytes(b"png")
            build_installer.sanitize_app_dir(app)
            self.assertFalse((app / "user_config.json").exists())
            self.assertFalse((app / "screenshots").exists())
            self.assertTrue((app / "TranslationTool.exe").exists())
            self.assertTrue((Path(tmp) / "_runtime_leftovers").is_dir())


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"\n通过 {result.testsRun - len(result.failures) - len(result.errors)}"
          f"/{result.testsRun} 项")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
