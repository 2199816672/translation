#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建翻译工具的一键安装程序（端到端）。

用法：
    python build_installer.py                 # 重新打包 exe + 编译 setup.exe
    python build_installer.py --skip-exe     # 复用最近一次构建的程序目录，只编译安装包
    python build_installer.py --portable     # 额外生成免安装 zip
    python build_installer.py --version 1.3.0 --skip-exe   # 仅用于本地覆盖升级测试
    python build_installer.py --keep-old     # 保留旧版本产物（默认会自动清理）
    python build_installer.py --purge-staging  # 连本次构建暂存目录一起删

输出：
    dist/installer/TranslationTool-Setup-v<版本>.exe
    dist/installer/TranslationTool-Setup-v<版本>.exe.sha256
    dist/installer/TranslationTool-v<版本>-win64.zip（仅 --portable）

版本号唯一来源是 config.py 的 APP_VERSION，安装脚本通过 ISCC 的
/dMyAppVersion 注入，避免多处硬编码不同步。发版改版本号请用
`python bump_version.py <版本>`（会同步 README 标题与 iss 兜底值）。

构建成功（安装包、校验文件、可选 zip 全部生成）后会自动清理本地旧版本
产物，避免每个版本 170~250 MB 的安装包和数百 MB 的暂存目录一直累积。
当前版本产物一律保留，本次暂存目录默认保留供冻结版冒烟，旧版本在
GitHub Release 上仍有云端备份。需要保留本地旧包时加 --keep-old，或设
环境变量 TT_KEEP_OLD_BUILDS=1。清理失败不会影响构建结果。
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
    "screenshots", "models", "converted", "user_libs", "cache",
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
            # 绝不能带着用户数据继续打包：一旦它进了 [Files] 清单，覆盖升级
            # 会用包内默认值覆盖用户设置、卸载时还会把组件当程序文件删掉。
            raise RuntimeError(
                f"无法把运行时数据 {name} 移出打包源：{exc}\n"
                "请关闭正在运行的程序或检查文件占用后重新构建。"
            ) from exc
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


# ── 旧版本产物自动清理 ──────────────────────────────────────────────
# 只在「能解析出版本号」或「明确是构建中间目录」时才动手，手工放进去的
# 未知文件一律不碰。当前版本产物、installer 目录、dist 本身永远保留。
_VERSIONED_NAMES = (
    re.compile(r"^TranslationTool-Setup-v(.+)\.exe$"),
    re.compile(r"^TranslationTool-Setup-v(.+)\.exe\.sha256$"),
    re.compile(r"^TranslationTool-v(.+)-win64\.zip$"),
    re.compile(r"^release_notes_v(.+)\.md$"),
)
_OLD_PORTABLE = re.compile(r"^ScreenshotTranslator_v")
_STAGE_DIR = re.compile(r"^staging_\d{8}_\d{6}$")
_VERIFY_DIRS = frozenset(("batch_verify", "health_check",
                          "ui_check", "ui_verify"))
_STALE_LOGS = frozenset(("_upgrade_verify.log",))


def _version_of(name: str):
    """能解析出版本号才返回，否则返回 None（不认识的名字不清理）。"""
    for pattern in _VERSIONED_NAMES:
        match = pattern.match(name)
        if match:
            return match.group(1)
    return None


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for base, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def _leftovers_may_have_user_data(stage: Path) -> bool:
    """暂存目录被 sanitize_app_dir 挪出的数据里是否像有真实用户文件。

    冒烟测试只会留下几十字节的默认配置；出现 >1 KB 的文件就保守跳过整个
    暂存目录，交给用户自己确认，避免自动清理误删用户数据。
    """
    park = stage / "_runtime_leftovers"
    if not park.is_dir():
        return False
    try:
        return any(p.is_file() and p.stat().st_size > 1024
                   for p in park.rglob("*"))
    except OSError:
        return True


def _dir_has_runtime_data(app_dir: Path) -> bool:
    """程序目录里是否残留运行时生成的用户数据（配置/模型/组件/缓存）。

    dist/TranslationTool 可能被直接双击运行过，此时 exe 会在同目录写出
    这些数据；自动清理不能把它当纯构建中间目录整棵 rmtree。
    """
    try:
        return any((app_dir / name).exists() for name in RUNTIME_DATA)
    except OSError:
        return True


def cleanup_old_builds(version: str, dist_root=None, keep_staging=None,
                       purge_staging: bool = False,
                       dry_run: bool = False) -> dict:
    """构建成功后清理旧版本产物，返回 deleted / freed / errors / kept_staging。

    - 只删版本 != version 的带版本产物，以及明确的构建中间目录；
    - keep_staging 是本次构建的暂存目录，默认保留（冻结版冒烟要用）；
    - 暂存目录含疑似用户数据时跳过并写进 errors；
    - dry_run=True 只报告不删除，供测试与人工确认。
    """
    dist = Path(dist_root or ROOT / "dist").resolve()
    result = {"deleted": [], "freed": 0, "errors": [], "kept_staging": None}
    if not dist.is_dir():
        return result
    keep = Path(keep_staging).resolve() if keep_staging else None

    def remove(path: Path, label: str) -> None:
        try:
            target = path.resolve()
            if target == dist or dist not in target.parents:
                result["errors"].append(f"拒绝越界删除: {path}")
                return
            if not path.exists():
                return
            size = _tree_size(path)
            if not dry_run:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            result["deleted"].append((label, path.name, size))
            result["freed"] += size
        except Exception as exc:
            result["errors"].append(f"{path.name}: {exc}")

    installer = dist / "installer"
    if installer.is_dir():
        for entry in sorted(installer.iterdir()):
            found = _version_of(entry.name)
            if found is not None and found != version:
                remove(entry, "旧版本安装包")

    for entry in sorted(dist.iterdir()):
        if entry.name == "installer":
            continue
        if entry.is_file():
            found = _version_of(entry.name)
            if found is not None and found != version:
                remove(entry, "旧版本文件")
            elif _OLD_PORTABLE.match(entry.name) or entry.name in _STALE_LOGS:
                remove(entry, "旧便携包/验证日志")
        elif entry.is_dir() and not entry.is_symlink():
            if _OLD_PORTABLE.match(entry.name):
                remove(entry, "旧便携版目录")
                continue
            is_legacy_app = (entry.name == APP_DIR_NAME
                             and (entry / f"{APP_DIR_NAME}.exe").is_file())
            is_stage = bool(_STAGE_DIR.match(entry.name))
            is_verify = entry.name in _VERIFY_DIRS
            if not (is_legacy_app or is_stage or is_verify):
                continue
            # --skip-exe 在没有任何 staging 时会直接复用 dist/TranslationTool，
            # 此时 keep 就是 dist 本身，别把刚用作构建源的目录删掉。
            if is_legacy_app and keep == dist:
                continue
            # 便携版目录被直接运行过就会带真实用户数据，宁可留着不删
            if is_legacy_app and _dir_has_runtime_data(entry):
                result["errors"].append(
                    f"跳过 {entry.name}：目录内含运行时用户数据，请手动确认")
                continue
            if is_stage and not purge_staging and keep is not None \
                    and entry.resolve() == keep:
                result["kept_staging"] = entry.name
                continue
            if is_stage and _leftovers_may_have_user_data(entry):
                result["errors"].append(
                    f"跳过 {entry.name}：_runtime_leftovers 可能含用户数据")
                continue
            remove(entry, "构建暂存/验证目录")
    return result


def print_cleanup_summary(result: dict, purge_staging: bool = False) -> None:
    deleted = result["deleted"]
    if not deleted and not result["errors"]:
        print("旧版本清理：没有需要清理的旧产物。", flush=True)
        return
    print(f"旧版本清理：删除 {len(deleted)} 项，释放 "
          f"{result['freed'] / 1024 / 1024:.1f} MB", flush=True)
    for _label, name, size in deleted[:15]:
        print(f"  - {name} ({size / 1024 / 1024:.1f} MB)", flush=True)
    if len(deleted) > 15:
        print(f"  - ……另有 {len(deleted) - 15} 项", flush=True)
    if result["kept_staging"] and not purge_staging:
        print(f"  保留本次暂存目录 {result['kept_staging']}"
              "（冻结版冒烟用；--purge-staging 可一并删除）", flush=True)
    for err in result["errors"]:
        print(f"  警告：{err}", file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建翻译工具安装程序")
    parser.add_argument("--skip-exe", action="store_true",
                        help="复用已有 dist/TranslationTool，跳过 PyInstaller")
    parser.add_argument("--portable", action="store_true",
                        help="额外生成免安装 zip")
    parser.add_argument("--version", default=None,
                        help="覆盖安装包版本号（仅供本地覆盖升级测试用）")
    parser.add_argument("--keep-old", action="store_true",
                        help="保留本地旧版本产物（默认构建成功后自动清理）")
    parser.add_argument("--purge-staging", action="store_true",
                        help="连本次构建的暂存目录一起删除（默认保留供冒烟）")
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

    # 构建全部成功才清理：失败时旧版本仍可作为回滚。
    # --version 与 APP_VERSION 不一致属于本地覆盖升级测试，产物版本是假的，
    # 清理会误删真正当前版本的安装包，必须跳过。
    should_cleanup = (
        not args.keep_old
        and os.environ.get("TT_KEEP_OLD_BUILDS") != "1"
        and version == source_version
    )
    if should_cleanup:
        try:
            summary = cleanup_old_builds(
                version, keep_staging=app_dir.parent,
                purge_staging=args.purge_staging)
            print_cleanup_summary(summary, args.purge_staging)
        except Exception as exc:
            # 清理只是省空间，绝不能把已经成功的构建判成失败
            print(f"警告：旧版本清理未完成（不影响本次产物）：{exc}",
                  file=sys.stderr, flush=True)
    else:
        print("已跳过旧版本清理（--keep-old / 本地测试构建）。", flush=True)

    print("构建完成。", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"构建失败: {exc}", file=sys.stderr)
        raise SystemExit(1)
