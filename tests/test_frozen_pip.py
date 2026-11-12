#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安装版在线安装可选组件 / 升级后免重装的回归测试（离线、不联网、无 GUI）。

覆盖：冻结态 pip 命令构造、pip worker 入口、user_libs 目录与 sys.path 注入、
安装清单排除名单三处同步、打包时收集 pip。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app_paths  # noqa: E402
import build_exe  # noqa: E402
import build_installer  # noqa: E402
import main  # noqa: E402
import subprocess_util  # noqa: E402


class FrozenPipCommandTests(unittest.TestCase):
    def test_source_mode_keeps_plain_pip(self):
        with mock.patch.object(subprocess_util, "is_frozen", return_value=False):
            cmd, hint = subprocess_util.pip_cmd(
                ["soundcard"], ["-i", "https://example.com/simple"])
        self.assertFalse(hint)
        self.assertEqual(cmd[:4], [sys.executable, "-m", "pip", "install"])
        self.assertEqual(cmd[-3:],
                         ["-i", "https://example.com/simple", "soundcard"])

    def test_frozen_mode_is_installable(self):
        """安装版不能再返回 (None, 禁止提示)，必须给出可执行命令。"""
        with tempfile.TemporaryDirectory() as tmp:
            libs = Path(tmp) / "user_libs"
            # --target 必须取自启动时固定的 USER_LIBS_DIR，不能每次重算
            with mock.patch.object(subprocess_util, "is_frozen", return_value=True), \
                 mock.patch.object(app_paths, "USER_LIBS_DIR", libs):
                cmd, hint = subprocess_util.pip_cmd(
                    ["easyocr"], ["--retries", "3"])
        self.assertFalse(hint, "安装版不应再给出无法安装的提示")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1], subprocess_util.PIP_WORKER_FLAG)
        self.assertIn("install", cmd)
        self.assertNotIn("-m", cmd, "冻结态绝不能拿 exe 跑 -m pip")
        self.assertIn("--target", cmd)
        self.assertEqual(cmd[cmd.index("--target") + 1], str(libs))
        self.assertNotIn(
            "--upgrade", cmd,
            "共享 --target + 多镜像重试下，--upgrade 会替换已装组件的文件")
        self.assertEqual(cmd[-3:], ["--retries", "3", "easyocr"])

    def test_target_lives_in_user_data_and_is_excluded_from_installer(self):
        """升级不重装的关键：组件目录是用户数据，且三处排除名单一致。"""
        self.assertIn("user_libs", app_paths.RUNTIME_DATA)
        self.assertIn("user_libs", build_installer.RUNTIME_DATA)
        iss = (ROOT / "installer.iss").read_text(encoding="utf-8-sig")
        self.assertIn(r"user_libs\*", iss)
        self.assertIn("user_libs", app_paths.user_libs_dir().name)

    def test_user_libs_dir_creates_writable_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(app_paths, "APP_DIR", Path(tmp)):
                path = app_paths.user_libs_dir()
            self.assertTrue(path.is_dir())
            self.assertEqual(path.name, "user_libs")

    def test_user_libs_is_appended_not_prepended(self):
        """内置包必须优先，否则 pip 顺带装的 numpy/cv2 会覆盖程序自带版本。"""
        value = str(app_paths.USER_LIBS_DIR)
        self.assertIn(value, sys.path)
        self.assertGreater(sys.path.index(value), 0)

    def test_build_bundles_pip_for_worker_mode(self):
        source = (ROOT / "build_exe.py").read_text(encoding="utf-8")
        self.assertIn('"--collect-all", "pip"', source)
        self.assertIn("pip._internal.cli.main", source)

    def test_refresh_makes_new_component_visible_in_same_process(self):
        """安装完成后的那次「已安装 ✓」检查必须能看到刚装好的包。"""
        import importlib.util
        name = "tt_probe_optional_pkg"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sys.path.append(str(root))
            try:
                self.assertIsNone(importlib.util.find_spec(name))
                (root / name).mkdir()
                (root / name / "__init__.py").write_text("", encoding="utf-8")
                app_paths.refresh_user_libs()
                self.assertIsNotNone(importlib.util.find_spec(name))
            finally:
                sys.path.remove(str(root))
                sys.path_importer_cache.pop(str(root), None)

    def test_engine_check_still_works_with_cache_refresh(self):
        import ocr_recognizer
        self.assertFalse(ocr_recognizer.check_engine_available("unknown"))


class PipWorkerEntryTests(unittest.TestCase):
    def test_only_install_subcommand_allowed(self):
        self.assertEqual(main._run_pip_worker([]), 2)
        self.assertEqual(main._run_pip_worker(["uninstall", "easyocr"]), 2)

    def test_worker_returns_pip_exit_code(self):
        try:
            import pip._internal.cli.main as pip_main_module
        except ImportError:
            self.skipTest("当前环境没有 pip")
        with mock.patch.object(pip_main_module, "main", return_value=7) as stub:
            code = main._run_pip_worker(
                ["install", "--target", "somewhere", "dummy-pkg"])
        self.assertEqual(code, 7)
        self.assertEqual(stub.call_args[0][0][0], "install")

    def test_worker_stdio_is_repaired_when_missing(self):
        """--windowed 冻结程序 sys.stdout 可能是 None，pip 输出不能被吞。"""
        with mock.patch.object(sys, "stdout", None), \
             mock.patch.object(sys, "stderr", None):
            main._prepare_worker_stdio()
            self.assertIsNotNone(sys.stdout)
            self.assertIsNotNone(sys.stderr)


if __name__ == "__main__":
    unittest.main()
