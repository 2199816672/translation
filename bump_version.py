#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发版改版本号的唯一入口：一次同步所有还写着具体版本号的位置。

用法：
    python bump_version.py 1.5.2     # 统一改成 1.5.2
    python bump_version.py --check   # 只校验一致性，不改文件（CI 会跑）
    python bump_version.py           # 查看当前版本与一致性状态

背景：
    程序运行时与安装包构建本来就只有一个数据源 —— config.py 的
    APP_VERSION：界面显示、更新检查都 import 它；build_installer.py 用
    read_version() 读它，再经 ISCC 的 /dMyAppVersion 注入 installer.iss，
    安装包版本 / 文件名 / SHA256 全部随之派生。
    但 README 标题、installer.iss 里手工编译用的 #ifndef 兜底值仍是手写的，
    漏改就会出现「文档写 1.5.1、实际是 1.5.2」。本脚本把这两处一起改掉，
    并由 --check + CI 兜底。

    唯一不归本脚本管的是 GitHub Release 的 tag / 标题（仓库外操作），
    发布时用相同的 v<版本> 即可。
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEMVER = re.compile(r"\d+\.\d+\.\d+")

# find: 提取当前版本号（第 1 组）；sub/repl: 原地替换版本号，不动其它字节。
TARGETS = {
    "config.py": {
        "find": r'(?m)^APP_VERSION\s*=\s*"([^"]+)"',
        "sub": r'(?m)^(APP_VERSION\s*=\s*")[^"]*(")',
        "repl": r'\g<1>{ver}\g<2>',
    },
    "README.md": {
        "find": r"(?m)^# 翻译工具 v([^\s\r\n]+)",
        "sub": r"(?m)^(# 翻译工具 v)[^\s\r\n]+",
        "repl": r"\g<1>{ver}",
    },
    "installer.iss": {
        "find": r'(?m)^[ \t]*#define MyAppVersion "([^"]+)"',
        "sub": r'(?m)^([ \t]*#define MyAppVersion ")[^"]*(")',
        "repl": r"\g<1>{ver}\g<2>",
    },
}


def _read(path: Path):
    """返回 (BOM, 文本)。installer.iss 必须保留 UTF-8 BOM 与 CRLF。"""
    raw = path.read_bytes()
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    return bom, raw[len(bom):].decode("utf-8")


def read_versions(root=ROOT) -> dict:
    """读出各文件里写死的版本号；找不到标记则为 None。"""
    root = Path(root)
    result = {}
    for name, spec in TARGETS.items():
        path = root / name
        if not path.is_file():
            result[name] = None
            continue
        _, text = _read(path)
        match = re.search(spec["find"], text)
        result[name] = match.group(1) if match else None
    return result


def check(root=ROOT) -> list:
    """返回不一致 / 缺失的描述列表，空列表代表全部同步。"""
    versions = read_versions(root)
    base = versions.get("config.py")
    problems = []
    if not base:
        problems.append("config.py：找不到 APP_VERSION")
    elif not SEMVER.fullmatch(base):
        problems.append("config.py：APP_VERSION 格式不合法：%r" % base)
    for name, value in versions.items():
        if value is None:
            if name != "config.py":
                problems.append("%s：找不到版本号标记" % name)
        elif base and value != base:
            problems.append("%s 写的是 v%s，与 APP_VERSION v%s 不一致"
                            % (name, value, base))
    return problems


def apply_version(version: str, root=ROOT) -> list:
    """把所有标记改成 version，返回实际被改动的文件名列表。"""
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise ValueError("版本号必须是 x.y.z 形式：%r" % (version,))
    root = Path(root)
    changed = []
    for name, spec in TARGETS.items():
        path = root / name
        if not path.is_file():
            raise FileNotFoundError("缺少文件：%s" % path)
        bom, text = _read(path)
        new_text, count = re.subn(
            spec["sub"], spec["repl"].format(ver=version), text, count=1)
        if count != 1:
            raise RuntimeError("%s：找不到可替换的版本号标记" % name)
        if new_text != text:
            path.write_bytes(bom + new_text.encode("utf-8"))
            changed.append(name)
    problems = check(root)
    if problems:
        raise RuntimeError("改完后一致性校验仍未通过：" + "; ".join(problems))
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="同步发版版本号")
    parser.add_argument("version", nargs="?", help="新版本号，如 1.5.2")
    parser.add_argument("--check", action="store_true",
                        help="只校验一致性，不改文件")
    parser.add_argument("--root", default=str(ROOT),
                        help="项目根目录（测试用）")
    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.check:
        problems = check(root)
        if problems:
            for item in problems:
                print("[FAIL] " + item, flush=True)
            print("请运行 python bump_version.py <版本号> 同步", flush=True)
            return 1
        print("[OK] 版本一致：v%s（%d 处标记）"
              % (read_versions(root)["config.py"], len(TARGETS)), flush=True)
        return 0

    if args.version is None:
        versions = read_versions(root)
        print("当前 APP_VERSION：v%s" % versions["config.py"], flush=True)
        for name, value in versions.items():
            print("  %s: v%s" % (name, value), flush=True)
        problems = check(root)
        if problems:
            for item in problems:
                print("[FAIL] " + item, flush=True)
            return 1
        print("[OK] 所有位置已同步", flush=True)
        return 0

    try:
        changed = apply_version(args.version, root)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print("[FAIL] " + str(exc), flush=True)
        return 1

    if changed:
        for name in changed:
            print("已更新 " + name, flush=True)
    else:
        print("所有位置已是该版本，无需改动", flush=True)
    print("\n下一步：", flush=True)
    print("  1. python build_installer.py   # 打包，版本自动取 APP_VERSION", flush=True)
    print("  2. python bump_version.py --check 复核后再提交", flush=True)
    print("  3. GitHub Release 的 tag / 标题用 v%s" % args.version, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
