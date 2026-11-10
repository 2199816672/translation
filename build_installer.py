#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建翻译工具的一键安装程序（端到端）。

用法：
    python build_installer.py                 # 重新打包 exe + 编译 setup.exe
    python build_installer.py --skip-exe     # 复用最近一次构建的程序目录，只编译安装包
    python build_installer.py --portable     # 额外生成免安装 zip
    python build_installer.py --version 1.3.0 --skip-exe   # 仅用于本地覆盖升级测试

输出：
    dist/installer/TranslationTool-Setup-v<版本>.exe
    dist/installer/TranslationTool-Setup-v<版本>.exe.sha256
    dist/installer/TranslationTool-v<版本>-win64.zip（仅 --portable）

版本号唯一来源是 config.py 的 APP_VERSION，安装脚本通过 ISCC 的
/dMyAppVersion 注入，避免多处硬编码不同步。
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_APP = ROOT / "dist" / "TranslationTool"
OUT_DIR = ROOT / "dist" / "installer"
ISS = ROOT / "installer.iss"
ISL = ROOT / "packaging" / "ChineseSimplified.isl"
APP_DIR_NAME = "TranslationTool"
SETUP_PREFIX = "TranslationTool-Setup-v"

# 程序运行时生成的用户数据。绝不能进入安装清单：一旦被登记，覆盖升级会用
# 包内默认配置覆盖用户设置（ignoreversion），卸载时还会被当作安装文件删掉。
RUNTIME_DATA = (
    "user_config.json", "user_config.json.broken", "user_config.json.tmp",
    "screenshots", "models", "converted",
)


def read_version() -> str:
    text = (ROOT / "config.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not match:
        raise RuntimeError("无法从 config.py 读取 APP_VERSION")
    return match.group(1)


def ensure_utf8_bom() -> None:
    """Inno Setup 需要 BOM 才能正确解析含中文的脚本 / 语言包。"""
    for path in (ISS, ISL):
        data = path.read_bytes()
        if not data.startswith(b"\xef\xbb\xbf"):
            path.write_bytes(b"\xef\xbb\xbf" + data)
            print(f"已为 {path.name} 补上 UTF-8 BOM", flush=True)


def latest_staging_app() -> Path:
    """找到最近一次构建出的程序目录，供 --skip-exe 复用。"""
    candidates = [
        p for p in ROOT.glob(f"dist/staging_*/{APP_DIR_NAME}")
        if (p / f"{APP_DIR_NAME}.exe").is_file()
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else DIST_APP


def find_iscc() -> str:
    env = os.environ.get("ISCC")
    if env and Path(env).is_file():
        return env
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return found
    candidates = []
    for var in ("LOCALAPPDATA", "ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "Programs" / "Inno Setup 6" / "ISCC.exe")
            candidates.append(Path(base) / "Inno Setup 6" / "ISCC.exe")
    candidates += [
        Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
        Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    raise RuntimeError(
        "未找到 Inno Setup 编译器 ISCC.exe。请先安装：\n"
        "  winget install --id JRSoftware.InnoSetup -e\n"
        "或设置环境变量 ISCC 指向 ISCC.exe。"
    )


def build_exe() -> Path:
    # 每次用全新的暂存目录，天然不会混入上次运行生成的 user_config.json /
    # screenshots，也不需要删除任何旧产物。
    stage = ROOT / "dist" / f"staging_{time.strftime('%Y%m%d_%H%M%S')}"
    stage.mkdir(parents=True, exist_ok=True)
    print(f"暂存目录: {stage}", flush=True)
    subprocess.run(
        [sys.executable, "build_exe.py",
         "--distpath", str(stage),
         "--workpath", str(stage / "work")],
        cwd=ROOT, check=True,
    )
    app_dir = stage / APP_DIR_NAME
    exe = app_dir / f"{APP_DIR_NAME}.exe"
    if not exe.is_file():
        raise RuntimeError(f"打包结束但没有找到 {exe}")
    print(f"程序目录: {app_dir}", flush=True)
    return app_dir


def sanitize_app_dir(app_dir: Path) -> None:
    """把源目录里残留的运行时数据挪到构建目录外，保证打包源是纯程序文件。

    暂存目录可能被直接双击运行过（exe 会在同目录生成配置和数据目录），
    ``--skip-exe`` 复用它时若不清干净，安装包就会夹带默认配置。用移动
    而不是删除，既保住数据，也避开受控环境下的批量删除限制。
    """
    park = app_dir.parent / "_runtime_leftovers"
    moved = []
    for name in RUNTIME_DATA:
        src = app_dir / name
        if not src.exists():
            continue
        stamp = time.strftime("%H%M%S")
        dst = park / f"{stamp}_{name}"
        suffix = 0
        while dst.exists():
            suffix += 1
            dst = park / f"{stamp}_{suffix}_{name}"
        try:
            park.mkdir(exist_ok=True)
            src.rename(dst)
            moved.append(name)
        except OSError as exc:
            print(f"警告：无法移出 {name}: {exc}", flush=True)
    if moved:
        print(f"已从打包源移出运行时数据: {', '.join(moved)}", flush=True)
    else:
        print("打包源干净（无运行时数据）", flush=True)


def build_setup(version: str, iscc: str, app_dir: Path) -> Path:
    sanitize_app_dir(app_dir)
    ensure_utf8_bom()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{SETUP_PREFIX}{version}"
    cmd = [
        iscc,
        f"/dMyAppVersion={version}",
        f"/dSourceDir={app_dir}",
        f"/O{OUT_DIR}",
        f"/F{name}",
        str(ISS),
    ]
    print("正在编译安装程序……", flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"ISCC 编译失败，退出码 {result.returncode}")
    setup = OUT_DIR / f"{name}.exe"
    if not setup.is_file():
        raise RuntimeError(f"编译结束但没有找到 {setup}")
    return setup


def write_sha256(setup: Path) -> Path:
    """生成可贴进 Release 说明的校验行（updater 会从说明里解析）。"""
    digest = hashlib.sha256()
    with open(setup, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    line = f"sha256:{digest.hexdigest()}  {setup.name}\n"
    out = setup.with_name(setup.name + ".sha256")
    out.write_text(line, encoding="utf-8")
    return out


def build_portable(version: str, app_dir: Path) -> Path:
    zip_path = OUT_DIR / f"TranslationTool-v{version}-win64.zip"
    print("正在生成免安装 zip（可能需要一两分钟）……", flush=True)
    shutil.make_archive(
        str(zip_path.with_suffix("")), "zip",
        root_dir=str(app_dir.parent), base_dir=app_dir.name,
    )
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="构建翻译工具安装程序")
    parser.add_argument("--skip-exe", action="store_true",
                        help="复用已有 dist/TranslationTool，跳过 PyInstaller")
    parser.add_argument("--portable", action="store_true",
                        help="额外生成免安装 zip")
    parser.add_argument("--version", default=None,
                        help="覆盖安装包版本号（仅供本地覆盖升级测试用）")
    args = parser.parse_args()

    source_version = read_version()
    if args.version and args.version != source_version:
        # --version 只注入 ISCC / 安装包文件名，不会改程序内的 APP_VERSION。
        # 若用它出正式包，装完程序仍自报旧版本，latest 恒比它新，用户会陷入
        # 「每次启动都提示升级、升完还是旧版本」的死循环。本地覆盖升级测试
        # 专供 --skip-exe，那里允许版本号不同。
        if not args.skip_exe:
            raise RuntimeError(
                f"--version {args.version} 与 config.py 的 APP_VERSION "
                f"{source_version} 不一致；正式构建请先改 config.py 再不带 "
                "--version 重跑（该参数仅供 --skip-exe 本地测试）")
        print(f"警告：仅本地测试，安装包版本 {args.version} 与程序内 "
              f"APP_VERSION {source_version} 不一致，切勿发布。", flush=True)
    version = args.version or source_version
    iscc = find_iscc()
    print(f"版本 v{version} | ISCC: {iscc}", flush=True)

    if args.skip_exe:
        app_dir = latest_staging_app()
        if not (app_dir / f"{APP_DIR_NAME}.exe").is_file():
            raise RuntimeError(
                "--skip-exe 但找不到可复用的程序目录，请先运行一次完整构建"
            )
        print(f"复用已有程序目录: {app_dir}", flush=True)
    else:
        app_dir = build_exe()

    setup = build_setup(version, iscc, app_dir)
    checksum = write_sha256(setup)
    print(f"安装包: {setup} ({setup.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"校验文件: {checksum}")
    print("发布 Release 时请把 .sha256 文件里的那一行贴进发布说明。", flush=True)

    if args.portable:
        zip_path = build_portable(version, app_dir)
        print(f"便携包: {zip_path} ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")

    print("构建完成。", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"构建失败: {exc}", file=sys.stderr)
        raise SystemExit(1)
