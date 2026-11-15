#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建成功后自动清理旧版本产物的行为契约。

自动删除最容易被改坏的两处：把当前版本一起删掉，或误删暂存目录里
sanitize_app_dir 挪出来的用户数据。这里用临时 dist 树做真实行为测试。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import build_installer as bi


CUR = '1.5.2'
OLD = ('1.4.0', '1.5.0', '1.5.1')
NEW_STAGE = 'staging_20261114_053554'
OLD_STAGE = 'staging_20261110_032217'


def _src(relative):
    return (ROOT / relative).read_text(encoding='utf-8-sig')


def _make_dist(root):
    """造一棵包含旧版本 / 当前版本 / 暂存目录 / 未知文件的 dist 树。"""
    installer = root / 'installer'
    installer.mkdir()
    for v in OLD + (CUR,):
        (installer / f'TranslationTool-Setup-v{v}.exe').write_bytes(b'x' * 100)
        (installer / f'TranslationTool-Setup-v{v}.exe.sha256').write_text('sha')
    for v in ('1.5.1', CUR):
        (installer / f'TranslationTool-v{v}-win64.zip').write_bytes(b'z' * 100)
    for v in OLD + (CUR,):
        (root / f'release_notes_v{v}.md').write_text('# notes')
    (root / 'ScreenshotTranslator_v1.2.0.zip').write_bytes(b'p' * 100)
    portable = root / 'ScreenshotTranslator_v1.2.0'
    portable.mkdir()
    (portable / 'app.exe').write_bytes(b'e' * 100)
    (root / '_upgrade_verify.log').write_text('log')
    # 不认识的文件必须原样保留，自动清理不能「清空整个 dist」
    (root / 'notes.txt').write_text('keep')
    (installer / 'README.txt').write_text('keep')

    for name in (OLD_STAGE, NEW_STAGE):
        stage = root / name
        (stage / 'TranslationTool').mkdir(parents=True)
        (stage / 'TranslationTool' / 'TranslationTool.exe').write_bytes(
            b'e' * 100)
    for name in ('batch_verify', 'health_check', 'ui_verify', 'ui_check'):
        (root / name).mkdir()
    return root / NEW_STAGE, root / OLD_STAGE


class CleanupBehaviourTests(unittest.TestCase):
    """真实文件树：只删旧版本和中间产物。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dist = Path(self.tmp.name) / 'dist'
        self.dist.mkdir()
        self.new_stage, self.old_stage = _make_dist(self.dist)

    def _clean(self, **kwargs):
        return bi.cleanup_old_builds(CUR, dist_root=self.dist, **kwargs)

    def test_current_version_products_never_deleted(self):
        self._clean(keep_staging=self.new_stage)
        installer = self.dist / 'installer'
        for name in (
                f'TranslationTool-Setup-v{CUR}.exe',
                f'TranslationTool-Setup-v{CUR}.exe.sha256',
                f'TranslationTool-v{CUR}-win64.zip',
                f'release_notes_v{CUR}.md'):
            target = installer / name if not name.startswith('release') \
                else self.dist / name
            self.assertTrue(target.is_file(), f'当前版本产物被误删: {name}')

    def test_all_old_version_products_deleted(self):
        self._clean(keep_staging=self.new_stage)
        for v in OLD:
            self.assertFalse(
                (self.dist / 'installer' /
                 f'TranslationTool-Setup-v{v}.exe').exists())
            self.assertFalse(
                (self.dist / 'installer' /
                 f'TranslationTool-Setup-v{v}.exe.sha256').exists())
            self.assertFalse(
                (self.dist / f'release_notes_v{v}.md').exists())
        self.assertFalse(
            (self.dist / 'installer' /
             'TranslationTool-v1.5.1-win64.zip').exists())
        self.assertFalse(
            (self.dist / 'ScreenshotTranslator_v1.2.0.zip').exists())
        self.assertFalse(
            (self.dist / 'ScreenshotTranslator_v1.2.0').exists())
        self.assertFalse((self.dist / '_upgrade_verify.log').exists())

    def test_unknown_files_are_untouched(self):
        self._clean(keep_staging=self.new_stage)
        self.assertTrue((self.dist / 'notes.txt').is_file())
        self.assertTrue((self.dist / 'installer' / 'README.txt').is_file())
        self.assertTrue((self.dist / 'installer').is_dir())

    def test_keeps_current_staging_and_removes_older_ones(self):
        result = self._clean(keep_staging=self.new_stage)
        self.assertTrue(self.new_stage.is_dir())
        self.assertFalse(self.old_stage.exists())
        self.assertEqual(result['kept_staging'], NEW_STAGE)
        for name in ('batch_verify', 'health_check', 'ui_verify', 'ui_check'):
            self.assertFalse((self.dist / name).exists(), name)

    def test_purge_staging_removes_every_stage(self):
        bi.cleanup_old_builds(CUR, dist_root=self.dist,
                              keep_staging=self.new_stage,
                              purge_staging=True)
        self.assertFalse(self.new_stage.exists())
        self.assertFalse(self.old_stage.exists())

    def test_no_keep_staging_removes_all_stages(self):
        self._clean()
        self.assertFalse(self.new_stage.exists())
        self.assertFalse(self.old_stage.exists())

    def test_dry_run_changes_nothing(self):
        before = sorted(str(p.relative_to(self.dist))
                        for p in self.dist.rglob('*'))
        result = bi.cleanup_old_builds(CUR, dist_root=self.dist,
                                       keep_staging=self.new_stage,
                                       dry_run=True)
        after = sorted(str(p.relative_to(self.dist))
                       for p in self.dist.rglob('*'))
        self.assertEqual(before, after)
        self.assertTrue(result['deleted'], 'dry-run 也应给出待删清单')
        self.assertGreater(result['freed'], 0)

    def test_freed_bytes_match_deleted_files(self):
        result = self._clean(keep_staging=self.new_stage)
        self.assertGreater(result['freed'], 0)
        self.assertEqual(result['freed'], sum(x[2] for x in result['deleted']))
        self.assertEqual(result['errors'], [])

    def test_staging_with_user_data_is_skipped(self):
        park = self.old_stage / '_runtime_leftovers'
        park.mkdir()
        (park / '030000_user_config.json').write_bytes(b'x' * 2048)
        result = self._clean(keep_staging=self.new_stage)
        self.assertTrue(self.old_stage.is_dir(),
                        '疑似用户数据时必须保守跳过')
        self.assertTrue(any('用户数据' in e for e in result['errors']))

    def test_small_smoke_test_config_does_not_block_cleanup(self):
        park = self.old_stage / '_runtime_leftovers'
        park.mkdir()
        (park / '030000_user_config.json').write_text(
            '{"asr_mode": "local", "asr_model": "small"}')
        self._clean(keep_staging=self.new_stage)
        self.assertFalse(self.old_stage.exists())

    def test_outside_dist_is_never_touched(self):
        outside = Path(self.tmp.name) / 'TranslationTool-Setup-v1.5.0.exe'
        outside.write_bytes(b'do not touch')
        self._clean(keep_staging=self.new_stage)
        self.assertTrue(outside.is_file())

    def test_missing_dist_is_harmless(self):
        result = bi.cleanup_old_builds(
            CUR, dist_root=Path(self.tmp.name) / 'nope')
        self.assertEqual(result['deleted'], [])
        self.assertEqual(result['freed'], 0)


class VersionParsingTests(unittest.TestCase):

    def test_known_names(self):
        cases = (
            ('TranslationTool-Setup-v1.5.0.exe', '1.5.0'),
            ('TranslationTool-Setup-v1.5.0.exe.sha256', '1.5.0'),
            ('TranslationTool-v1.5.2-win64.zip', '1.5.2'),
            ('release_notes_v1.5.1.md', '1.5.1'),
        )
        for name, expected in cases:
            self.assertEqual(bi._version_of(name), expected, name)

    def test_unknown_names_return_none(self):
        for name in ('notes.txt', 'README.txt', '_upgrade_verify.log',
                     'installer', 'TranslationTool.exe'):
            self.assertIsNone(bi._version_of(name), name)


class MainWiringContracts(unittest.TestCase):
    """清理必须只在构建成功之后、非本地测试场景下发生，且不能阻断构建。"""

    def setUp(self):
        self.main = _src('build_installer.py')

    def test_cleanup_is_called_after_all_products(self):
        body = self.main
        i_zip = body.index('zip_path = build_portable')
        i_clean = body.index('should_cleanup =')
        i_done = body.index('print("构建完成。')
        self.assertLess(i_zip, i_clean)
        self.assertLess(i_clean, i_done)

    def test_guards(self):
        for token in ('not args.keep_old', 'TT_KEEP_OLD_BUILDS',
                      'version == source_version', '--purge-staging',
                      '--keep-old'):
            self.assertIn(token, self.main, token)

    def test_cleanup_failure_cannot_fail_the_build(self):
        self.assertIn('清理未完成（不影响本次产物）', self.main)
        self.assertIn('return 0', self.main)

    def test_docstring_documents_default_and_escape_hatch(self):
        for token in ('--keep-old', '--purge-staging',
                      'TT_KEEP_OLD_BUILDS=1', 'GitHub Release'):
            self.assertIn(token, self.main, token)


if __name__ == '__main__':
    unittest.main()
