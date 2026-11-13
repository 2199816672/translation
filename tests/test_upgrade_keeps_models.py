#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""覆盖升级 / 一键升级后，用户下载或导入的模型必须还在、无需重下。

这是项目里最容易被改坏的承诺：只要有人把 ``models`` 加进 ``[Files]`` 清单，
或补一句 ``[InstallDelete]`` / ``[UninstallDelete]``，用户几百 MB 的模型就会
在升级时被清空。这里用源码契约 + 一次真实行为测试把它锁住。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import app_paths
import build_installer
import ocr_models
import updater


def _src(relative):
    # installer.iss 必须保存为 UTF-8 BOM，用 utf-8-sig 才能同时读两种文件
    return (ROOT / relative).read_text(encoding='utf-8-sig')


class InstallerKeepsUserDataTests(unittest.TestCase):
    """安装程序只替换程序本体，绝不碰用户数据目录。"""

    def test_files_line_excludes_models_and_user_libs(self):
        iss = _src('installer.iss')
        lines = [line for line in iss.splitlines()
                 if line.startswith('Source:') and 'Excludes:' in line]
        self.assertEqual(len(lines), 1, '只能有一条程序文件安装规则')
        for pattern in ('models\\*', 'user_libs\\*', 'screenshots\\*',
                        'user_config.json'):
            self.assertIn(pattern, lines[0], f'Excludes 漏了 {pattern}')

    def test_no_delete_sections(self):
        """Inno Setup 默认不删未登记文件；加了删除段就会破坏这个前提。"""
        iss = _src('installer.iss')
        headers = [line.strip() for line in iss.splitlines()
                   if line.strip().startswith('[') and line.strip().endswith(']')]
        for section in ('[InstallDelete]', '[UninstallDelete]'):
            self.assertNotIn(section, headers,
                             f'{section} 会删掉用户下载的模型')

    def test_runtime_data_lists_stay_in_sync(self):
        self.assertEqual(set(app_paths.RUNTIME_DATA),
                         set(build_installer.RUNTIME_DATA))
        for name in ('models', 'user_libs', 'screenshots', 'user_config.json'):
            self.assertIn(name, app_paths.RUNTIME_DATA)

    def test_model_root_lives_in_user_data_dir(self):
        """模型不能落在 PyInstaller 的 _internal 里（升级会被整体替换）。"""
        expected = os.path.join(str(app_paths.app_dir()), 'models', 'ocr')
        self.assertEqual(os.path.abspath(ocr_models.MODEL_ROOT),
                         os.path.abspath(expected))
        self.assertIn('models', app_paths.RUNTIME_DATA)

    def test_legacy_easyocr_dir_is_still_resolved(self):
        """老版本默认下到用户家目录，升级后也要能被找到，不必重下。"""
        with tempfile.TemporaryDirectory() as tmp:
            own = os.path.join(tmp, 'own')
            legacy = os.path.join(tmp, 'legacy')
            os.makedirs(own)
            os.makedirs(legacy)
            for name in ('craft_mlt_25k.pth', 'en_g2.pth'):
                with open(os.path.join(legacy, name), 'wb') as f:
                    f.write(b'model-data')
            resolved = ocr_models.resolve_model_dir(
                ['en'], root=own, legacy=legacy)
            self.assertEqual(os.path.abspath(resolved),
                             os.path.abspath(legacy))

    def test_online_update_uses_the_same_installer(self):
        """应用内一键升级跑的是同一个 Inno Setup 包，排除规则一致。"""
        src = _src('updater.py')
        self.assertIn('/VERYSILENT', src)
        self.assertIn('/RESTARTAPP=1', src)

    def test_build_source_is_cleaned_of_models(self):
        """行为验证：打包源里的模型被挪走而非删除，新包不可能夹带模型。"""
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / 'TranslationTool'
            (app / 'models' / 'ocr').mkdir(parents=True)
            (app / 'user_libs').mkdir(parents=True)
            (app / 'models' / 'ocr' / 'craft_mlt_25k.pth').write_bytes(b'x' * 128)
            (app / 'user_libs' / 'easyocr.py').write_text(
                '# stub', encoding='utf-8')
            build_installer.sanitize_app_dir(app)
            self.assertFalse((app / 'models').exists())
            self.assertFalse((app / 'user_libs').exists())
            leftovers = list(
                (Path(tmp) / '_runtime_leftovers').rglob('craft_mlt_25k.pth'))
            self.assertTrue(leftovers, '模型必须被移走而不是删除')


class DownloadMenuHonestyTests(unittest.TestCase):
    """下载入口不能让用户误以为「网页下完就能用 / 升级会丢」。"""

    def test_menu_marks_unusable_engines(self):
        items = ocr_models.download_menu_items()
        self.assertEqual(len(items), len(ocr_models.DOWNLOAD_LINKS))
        kinds = [kind for _label, _url, kind in items]
        self.assertIn('usable', kinds)
        self.assertIn('reference', kinds)
        for label, url, kind in items:
            self.assertTrue(url.startswith('https://'), url)
            self.assertIn('✅' if kind == 'usable' else '📋', label)

    def test_legacy_link_shape_is_unchanged(self):
        """DOWNLOAD_LINKS 仍是 (label, url) 二元组，别破坏已有调用方。"""
        for label, url in ocr_models.DOWNLOAD_LINKS:
            self.assertIsInstance(label, str)
            self.assertIsInstance(url, str)

    def test_hints_promise_persistence(self):
        for text in (ocr_models.manual_download_hint(),
                     ocr_models.engine_catalog_text()):
            self.assertIn('升级', text)
            self.assertIn('无需重新下载', text)
        settings = _src('pages/settings_page.py')
        self.assertIn('升级软件不会删除，无需重新下载', settings)
        self.assertIn('_on_ocr_download_link', settings)
        self.assertIn('不会自动进入程序', settings)

    def test_readme_documents_persistence_and_import_step(self):
        readme = _src('README.md')
        self.assertIn('导入的模型', readme)
        self.assertIn('不会自动进入程序', readme)
        self.assertIn('解压到**同一个文件夹**覆盖', readme)


if __name__ == '__main__':
    unittest.main()
