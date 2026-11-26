#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 24 轮：内置安装「三个源全失败」真因 + 手动模型文件名。

用户实测反馈：① 安装版点「安装」一直报错，换镜像 / 改 pip.ini 都没用；
② 按 EasyOCR 官方页面下好了模型，程序仍说「可选再补 1 个文件」。

真因（均已实机复现，不是猜测）：
1. 冻结版 pip worker 为带 console_scripts 的包生成 Windows .exe 启动器时，
   distlib.resources.finder('pip._vendor.distlib') 因 PyiFrozenImporter 未注册
   而抛 DistlibException。用 --no-index 装本地 3KB wheel 也 100% 复现，
   和网络 / 镜像 / pip.ini 完全无关。
2. EasyOCR 官方英文模型叫 english_g2.pth，项目旧表写的是 en_g2.pth（整张
   语言表几乎都按「语言代码 + _g2.pth」猜错），用户下好的文件被完全忽略。
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ocr_models
import subprocess_util as su
import user_messages

ROOT = Path(__file__).resolve().parents[1]
DETECTION = 'craft_mlt_25k.pth'
CHINESE = 'zh_sim_g2.pth'
ENGLISH_OFFICIAL = 'english_g2.pth'
ENGLISH_WRONG = 'en_g2.pth'


def _src(name):
    return (ROOT / name).read_text(encoding='utf-8')


def _write(directory, name, payload=b'model-data'):
    (Path(directory) / name).write_bytes(payload)
    return name


class FrozenPipLauncherTests(unittest.TestCase):
    """冻结版 pip worker 的 distlib 启动器修复。"""

    def setUp(self):
        try:
            from pip._vendor.distlib import scripts as scripts
            import pip._vendor.distlib.resources as resources
            import pip._vendor.distlib as distlib_pkg
        except Exception as exc:                                  # pragma: no cover
            self.skipTest(f'pip._vendor.distlib 不可用：{exc}')
        self.scripts = scripts
        self.resources = resources
        self.distlib_pkg = distlib_pkg
        import main as app_main
        self.app_main = app_main
        self._orig_init = scripts.ScriptMaker.__init__
        self._had_flag = hasattr(scripts.ScriptMaker, '_tt_no_launcher')

    def tearDown(self):
        # 全局 monkeypatch 不能污染同进程其它测试
        if not self._had_flag:
            self.scripts.ScriptMaker.__init__ = self._orig_init
            if hasattr(self.scripts.ScriptMaker, '_tt_no_launcher'):
                del self.scripts.ScriptMaker._tt_no_launcher

    def test_worker_calls_patch_before_pip_main(self):
        src = _src('main.py')
        body = src.split('def _run_pip_worker', 1)[1].split(
            'if __name__ ==', 1)[0]
        self.assertIn('_patch_frozen_pip_launchers()', body)
        self.assertLess(body.index('_patch_frozen_pip_launchers()'),
                        body.index('pip_main(list(argv))'))

    def test_frozen_loader_really_breaks_distlib_finder(self):
        """证明这条异常确实来自「loader 类型未注册」，不是我们凭猜测改代码。"""
        pkg = self.distlib_pkg
        res = self.resources
        original = getattr(pkg, '__loader__', None)
        res._finder_cache.pop('pip._vendor.distlib', None)
        try:
            pkg.__loader__ = object()      # 模拟 PyiFrozenImporter：未注册
            with self.assertRaises(Exception) as ctx:
                res.finder('pip._vendor.distlib')
            self.assertIn('Unable to locate finder', str(ctx.exception))
        finally:
            if original is None:
                if hasattr(pkg, '__loader__'):
                    del pkg.__loader__
            else:
                pkg.__loader__ = original
            res._finder_cache.pop('pip._vendor.distlib', None)

    def test_patch_disables_launchers_and_make_writes_plain_script(self):
        """修好后 ScriptMaker 不再触碰缺失的 finder，且仍能写出脚本。"""
        if os.name != 'nt':                                       # pragma: no cover
            self.skipTest('启动器问题只在 Windows 出现')
        self.app_main._patch_frozen_pip_launchers()
        with tempfile.TemporaryDirectory() as d:
            maker = self.scripts.ScriptMaker(None, d)
            self.assertFalse(maker.add_launchers)
            with mock.patch.object(
                    self.scripts.ScriptMaker, '_get_launcher',
                    side_effect=AssertionError('不应再调用 _get_launcher')):
                names = maker.make('ttprobe = ttprobe:main')
            self.assertTrue(names)
            self.assertTrue(all(Path(n).is_file() for n in names), names)
            self.assertFalse(any(n.lower().endswith('.exe') for n in names),
                             names)

    def test_patch_is_idempotent(self):
        self.app_main._patch_frozen_pip_launchers()
        first = self.scripts.ScriptMaker.__init__
        self.app_main._patch_frozen_pip_launchers()
        self.assertIs(self.scripts.ScriptMaker.__init__, first)


class FailureMessageTests(unittest.TestCase):
    """失败摘要与面向用户的提示不能再把内部错误说成「检查网络」。"""

    def test_failure_line_prefers_traceback_exception(self):
        tail = [
            '  File "pip\\_vendor\\distlib\\scripts.py", line 400',
            "pip._vendor.distlib.DistlibException: Unable to locate finder "
            "for 'pip._vendor.distlib'",
            'ERROR: Exception:',
        ]
        line = su._failure_line(tail)
        self.assertIn('DistlibException', line)
        self.assertIn('Unable to locate finder', line)

    def test_friendly_hint_names_the_real_cause(self):
        msg = ("安装失败（已依次尝试：腾讯云镜像 / PyPI 官方 / 华为云镜像，"
               "共 3 个源均失败）。\n各源失败摘要：\n"
               "  腾讯云镜像：pip._vendor.distlib.DistlibException: "
               "Unable to locate finder for 'pip._vendor.distlib'")
        text = user_messages.friendly_install_error(msg)
        self.assertIn('与网络、镜像、pip.ini 都无关', text)
        self.assertNotIn('检查网络', text)
        self.assertIn('Windows 系统自带', text)

    def test_403_hint_still_offers_mirror_fallback(self):
        text = user_messages.friendly_install_error(
            'ERROR: HTTP error 403 while getting https://example.invalid/x')
        for token in ('403', '换用其它源', '腾讯云', '重试安装',
                      '全部源都失败', 'Windows 系统自带'):
            self.assertIn(token, text)

    def test_all_sources_same_internal_error_gives_actionable_summary(self):
        lines = [
            'ERROR: Exception:\n',
            "pip._vendor.distlib.DistlibException: Unable to locate finder "
            "for 'pip._vendor.distlib'\n",
        ]

        class _Proc:
            returncode = 1

            def __init__(self):
                self._lines = iter(lines)
                self.stdout = self

            def readline(self):
                try:
                    return next(self._lines)
                except StopIteration:
                    return ''

            def poll(self):
                return 1

        @contextlib.contextmanager
        def _fake_pip(cmd, **kwargs):
            yield _Proc()

        with mock.patch.object(su, 'pip_process', new=_fake_pip):
            ok, msg = su.pip_install_with_mirrors(['ttprobe'])
        self.assertFalse(ok)
        self.assertIn('与网络、镜像、pip.ini 都无关', msg)
        self.assertIn('DistlibException', msg)
        self.assertNotIn('请点「重试安装」再试一次。\n安装版', msg)


class OfficialModelNameTests(unittest.TestCase):
    """模型文件名必须和 EasyOCR 官方完全一致。"""

    def test_required_files_use_official_names(self):
        files = ocr_models.required_model_files()
        self.assertEqual(files[0], DETECTION)
        self.assertIn(CHINESE, files)
        self.assertIn(ENGLISH_OFFICIAL, files)
        self.assertNotIn(ENGLISH_WRONG, files)

    def test_table_matches_installed_easyocr_config(self):
        """有 easyocr 的环境直接拿官方 config.py 交叉校验整张表。"""
        try:
            spec = importlib.util.find_spec('easyocr')
        except Exception:
            spec = None
        if not spec or not spec.submodule_search_locations:
            self.skipTest('本机未安装 easyocr，跳过官方配置交叉校验')
        cfg = Path(list(spec.submodule_search_locations)[0]) / 'config.py'
        if not cfg.is_file():                                     # pragma: no cover
            self.skipTest('找不到 easyocr/config.py')
        official = set(re.findall(r"'filename':\s*'([^']+)'",
                                  cfg.read_text(encoding='utf-8')))
        self.assertIn(DETECTION, official)
        for lang, name in ocr_models._LANG_MODELS.items():
            self.assertIn(name, official, f'{lang} 的模型文件名 {name} 不存在')

    def test_official_english_file_is_recognized_as_complete(self):
        with tempfile.TemporaryDirectory() as d:
            for name in (DETECTION, CHINESE, ENGLISH_OFFICIAL):
                _write(d, name)
            info = ocr_models.detect_local_models(root=d, legacy=d)
            self.assertEqual(info['available_languages'], ['ch_sim', 'en'])
            self.assertEqual(info['optional_missing'], [])
            self.assertEqual(info['misnamed_files'], [])
            self.assertTrue(info['ready'])
            self.assertIn('模型齐全', ocr_models.model_status_suffix(info))

    def test_wrong_name_is_reported_but_never_counted_as_ready(self):
        """旧名不能算可用，否则 Reader 会去找官方名并因禁止联网而失败。"""
        with tempfile.TemporaryDirectory() as d:
            for name in (DETECTION, CHINESE, ENGLISH_WRONG):
                _write(d, name)
            info = ocr_models.detect_local_models(root=d, legacy=d)
            self.assertEqual(info['available_languages'], ['ch_sim'])
            self.assertIn((ENGLISH_WRONG, ENGLISH_OFFICIAL),
                          info['misnamed_files'])
            self.assertIn('文件名不符合官方命名',
                          ocr_models.model_status_suffix(info))

    def test_import_renames_wrong_name_to_official(self):
        with tempfile.TemporaryDirectory() as src, \
                tempfile.TemporaryDirectory() as dst:
            _write(src, ENGLISH_WRONG)
            count, copied = ocr_models.import_local_model(src, root=dst)
            self.assertEqual(count, 1)
            self.assertIn(ENGLISH_OFFICIAL, copied)
            self.assertTrue((Path(dst) / ENGLISH_OFFICIAL).is_file())
            self.assertFalse((Path(dst) / ENGLISH_WRONG).exists())

    def test_import_from_active_dir_renames_aliases_in_place(self):
        """老用户模型在旧版目录：导入目标就是源目录，改名提示必须点得动。"""
        with tempfile.TemporaryDirectory() as d:
            for name in (DETECTION, CHINESE):
                _write(d, name)
            _write(d, ENGLISH_WRONG, b'legacy')
            count, copied = ocr_models.import_local_model(d, root=d)
            self.assertIn(ENGLISH_OFFICIAL, copied)
            self.assertEqual((Path(d) / ENGLISH_OFFICIAL).read_bytes(),
                             b'legacy')
            # 不删用户原文件，只补一份官方名副本
            self.assertTrue((Path(d) / ENGLISH_WRONG).exists())
            self.assertEqual(count, 4)
            info = ocr_models.detect_local_models(root=d, legacy=d)
            self.assertEqual(info['available_languages'], ['ch_sim', 'en'])

    def test_official_name_wins_over_alias_in_same_source(self):
        with tempfile.TemporaryDirectory() as src, \
                tempfile.TemporaryDirectory() as dst:
            _write(src, ENGLISH_WRONG, b'wrong')
            _write(src, ENGLISH_OFFICIAL, b'official')
            _count, copied = ocr_models.import_local_model(src, root=dst)
            self.assertIn(ENGLISH_OFFICIAL, copied)
            self.assertEqual((Path(dst) / ENGLISH_OFFICIAL).read_bytes(),
                             b'official')

    def test_hints_and_readme_use_official_name(self):
        hint = ocr_models.manual_download_hint()
        self.assertIn('english_g2.pth', hint)
        self.assertIn('会自动改名成官方名', hint)
        readme = _src('README.md')
        self.assertIn('`english_g2.pth`', readme)
        self.assertIn('文件名必须和 EasyOCR 官方完全一致', readme)

    def test_settings_page_warns_about_misnamed_files(self):
        src = _src('pages/settings_page.py')
        self.assertIn("misnamed_tip", src)
        self.assertIn("detect.get('misnamed_files')", src)
        self.assertIn('导入时自动改名', src)


if __name__ == '__main__':
    unittest.main()
