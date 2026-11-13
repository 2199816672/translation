#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用内检查更新与一键升级（纯逻辑层，不依赖 Qt，便于单元测试）。

链路：查询 GitHub latest release → 找到一键安装包资产 → 下载（可取消、带进度）
→ SHA256 校验 → 退出主程序后由安装程序静默覆盖升级并自动重启。

仓库是公开的，无需登录；本机若有 GitHub 凭据会自动带上以提高 API 限额。
HTTP 直连失败时回退到 gh CLI（部分网络环境下 gh 配置了代理可用）。
网络 / 下载必须在工作线程里调用，本模块不接触任何 Qt 控件。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import ipaddress
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from config import APP_VERSION, GITHUB_REPO
from subprocess_util import run_quiet
from user_messages import friendly_network_error

REPO = GITHUB_REPO
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_API = f"https://api.github.com/repos/{REPO}/releases/assets/{{id}}"
RELEASES_URL = f"https://github.com/{REPO}/releases"
USER_AGENT = "TranslationTool-Updater"
TOKEN_ENV_VARS = ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_ACCESS_TOKEN")

# 只认官方命名的一键安装包，避免误下便携 zip 或被改名的资产
# 用 \Z 而不是 $：Python 的 $ 允许匹配「末尾换行符之前」，带 \n 的资产名
# 会绕过白名单，随后在 Windows 上因文件名含控制字符导致写文件失败。
SETUP_NAME_RE = re.compile(
    r"^TranslationTool-Setup-v[0-9][0-9A-Za-z.\-]*\.exe\Z", re.I)
SHA256_RE = re.compile(r"\b([0-9a-f]{64})\b", re.I)
DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$", re.I)

_API_HOST = "api.github.com"
# GitHub release 资产会 302 到对象存储，这些域名都属于官方链路
_ALLOWED_HOSTS = {
    "api.github.com", "github.com", "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
_ALLOWED_SUFFIXES = (".github.com", ".githubusercontent.com")


class UpdateError(Exception):
    """检查更新 / 下载过程中可展示给用户的错误。"""


class UpdateCancelled(Exception):
    """用户主动取消下载。"""


def _assert_safe_url(url: str) -> None:
    """更新链路只允许 HTTPS + GitHub 官方域名，防止被劫持到任意地址。"""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UpdateError("非法的更新地址：仅允许 HTTPS")
    host = (parts.hostname or "").lower()
    allowed = host in _ALLOWED_HOSTS or any(
        host.endswith(suffix) for suffix in _ALLOWED_SUFFIXES)
    if not allowed:
        raise UpdateError(f"非法的更新地址：域名不在白名单（{host}）")


def _validate_api_url(url: str) -> None:
    """API 地址额外校验解析结果必须是公网 IP（防 DNS rebinding 指向内网）。"""
    _assert_safe_url(url)
    parts = urlsplit(url)
    if parts.hostname != _API_HOST:
        raise UpdateError("非法的更新检测地址：域名不在白名单")
    infos = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UpdateError("更新检测地址解析到非公网 IP，已拒绝")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """允许 GitHub 官方对象存储重定向，但逐跳校验目标；跨主机去掉认证头。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_safe_url(newurl)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and new.has_header("Authorization"):
            new.remove_header("Authorization")
        return new


_token_lock = threading.Lock()
_token_cache = None  # None = 尚未探测；"" = 已确认本机没有凭据


def _find_token() -> str:
    for name in TOKEN_ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    exe = shutil.which("gh")
    if exe:
        try:
            result = run_quiet([exe, "auth", "token"], capture_output=True,
                              text=True, timeout=6)
            if result.returncode == 0:
                return (result.stdout or "").strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def github_token(refresh: bool = False) -> str:
    """读取本机 GitHub 凭据；只在内存里缓存，绝不写日志或配置文件。"""
    global _token_cache
    with _token_lock:
        if refresh or _token_cache is None:
            _token_cache = _find_token()
        return _token_cache


def auth_headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@dataclass
class ReleaseInfo:
    version: str
    tag: str
    name: str
    body: str
    page_url: str
    setup_name: str = ""
    setup_url: str = ""
    setup_size: int = 0
    sha256: str = ""
    setup_asset_id: int = 0

    @property
    def has_installer(self) -> bool:
        return bool(self.setup_url and self.setup_name)


def version_tuple(version) -> tuple:
    """把版本号补齐到固定 4 段，(1,2) 与 (1,2,0) 才能正确比较。"""
    parts = re.findall(r"\d+", str(version or ""))[:4]
    parts += ["0"] * (4 - len(parts))
    return tuple(int(p) for p in parts)


def is_newer(remote: str, current: str) -> bool:
    return bool(remote) and version_tuple(remote) > version_tuple(current)


def parse_sha256_from_notes(body: str, asset_name: str) -> str:
    """兼容旧发布说明：从含资产名的行里提取 64 位十六进制校验值。"""
    needle = asset_name.lower()
    for line in str(body or "").splitlines():
        if needle in line.lower():
            match = SHA256_RE.search(line)
            if match:
                return match.group(1).lower()
    return ""


def _http_error(exc) -> str:
    code = getattr(exc, "code", None)
    if code == 404:
        return "找不到发布信息（HTTP 404），新版本可能尚未发布。"
    if code in (401, 403):
        return "GitHub 接口限流或凭据无效，请稍后重试。"
    if code:
        return f"更新服务器返回 HTTP {code}，请稍后重试或打开发布页手动下载。"
    return friendly_network_error(exc)


def _payload_via_gh() -> Optional[str]:
    """gh CLI 回退通道（国内网络下可能配了代理）。失败返回 None。"""
    exe = shutil.which("gh")
    if not exe:
        return None
    try:
        result = run_quiet(
            [exe, "api", f"repos/{REPO}/releases/latest"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and (result.stdout or "").strip():
            return result.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def fetch_latest_release(timeout: float = 10, api_url: str = API_URL,
                         opener=None, gh_payload=None) -> ReleaseInfo:
    """查询最新 Release。找不到安装包资产不算错误，界面会退回「打开发布页」。"""
    payload = gh_payload
    if payload is None:
        last_error = None
        for attempt in (0, 1):
            headers = auth_headers()
            req = urllib.request.Request(api_url, headers=headers)
            try:
                _validate_api_url(api_url)
                _open = opener or urllib.request.build_opener(
                    _SafeRedirectHandler()).open
                with _open(req, timeout=timeout) as resp:
                    payload = resp.read().decode("utf-8", "replace")
                break
            except urllib.error.HTTPError as exc:
                last_error = UpdateError(_http_error(exc))
                if attempt == 0 and exc.code in (401, 403) and headers.get("Authorization"):
                    github_token(refresh=True)
                    continue
                break
            except (UpdateError, urllib.error.URLError, OSError, TimeoutError) as exc:
                last_error = UpdateError(friendly_network_error(exc))
                break
        if payload is None:
            payload = _payload_via_gh()
        if payload is None:
            raise last_error or UpdateError("无法获取版本信息")

    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("不是 JSON 对象")
    except (ValueError, TypeError) as exc:
        raise UpdateError("更新信息格式异常") from exc

    tag = str(data.get("tag_name") or "")
    version = tag.lstrip("vV")
    info = ReleaseInfo(
        version=version, tag=tag,
        name=str(data.get("name") or f"v{version}"),
        body=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or RELEASES_URL),
    )

    for asset in (data.get("assets") or []):
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if not SETUP_NAME_RE.fullmatch(name):
            continue
        info.setup_name = name
        info.setup_url = str(asset.get("browser_download_url") or "")
        try:
            info.setup_size = int(asset.get("size") or 0)
        except (TypeError, ValueError):
            info.setup_size = 0
        try:
            info.setup_asset_id = int(asset.get("id") or 0)
        except (TypeError, ValueError):
            info.setup_asset_id = 0
        digest = str(asset.get("digest") or "")
        match = DIGEST_RE.match(digest)
        info.sha256 = (match.group(1).lower() if match
                       else parse_sha256_from_notes(info.body, name))
        break
    return info


def installer_url(info: ReleaseInfo) -> str:
    """有凭据时可走 API 资产端点（不受浏览器会话影响）。"""
    if info.setup_asset_id and github_token():
        return ASSET_API.format(id=info.setup_asset_id)
    return info.setup_url


def download_headers() -> dict:
    headers = {"User-Agent": USER_AGENT}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/octet-stream"
    return headers


def update_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "TranslationTool-Updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_download_path(info: ReleaseInfo) -> Path:
    """资产名来自远端，已经过白名单，这里再防一次路径穿越。"""
    name = Path(info.setup_name).name
    if not SETUP_NAME_RE.fullmatch(name):
        raise UpdateError("安装包文件名不合法，已取消下载")
    return update_dir() / name


def download_file(url: str, dest: Path, progress_cb: Optional[Callable] = None,
                  cancel: Optional[threading.Event] = None,
                  timeout: float = 30, chunk_size: int = 256 * 1024,
                  opener=None, headers=None,
                  expected_size: int = 0) -> Path:
    """下载到 .part 再原子替换，中途取消 / 失败不会留下半截文件。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    _assert_safe_url(url)
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    _open = opener or urllib.request.build_opener(_SafeRedirectHandler()).open

    try:
        with _open(req, timeout=timeout) as resp, open(part, "wb") as out:
            resp_headers = getattr(resp, "headers", {}) or {}
            try:
                total = int(resp_headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            if total <= 0:
                # 302 到对象存储常为 chunked / 无 Content-Length，
                # 用 Release API 里的 asset size 兜底，进度条才有百分比。
                total = int(expected_size or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateCancelled()
                block = resp.read(chunk_size)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if progress_cb is not None:
                    progress_cb(done, total)
        # 读完最后一个 chunk 到 replace 之间用户仍可能点了取消
        if cancel is not None and cancel.is_set():
            raise UpdateCancelled()
        os.replace(part, dest)
    except BaseException:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return dest


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_download(path: Path, expected: str) -> Optional[bool]:
    """返回 None 表示发布方没提供校验值（跳过校验）。"""
    if not expected:
        return None
    return file_sha256(path).lower() == str(expected).lower()


def check_update() -> dict:
    """兼容旧接口的同步检测，返回 dict；新的界面流程直接用 fetch_latest_release。"""
    result = {"has_update": False, "current": APP_VERSION, "latest": "",
              "url": "", "error": None, "info": None}
    try:
        info = fetch_latest_release()
        result.update(
            latest=info.tag or info.version,
            url=info.page_url,
            has_update=is_newer(info.version, APP_VERSION),
            info=info,
        )
    except Exception as exc:
        result["error"] = f"检测失败：{friendly_network_error(exc)}"
    return result


_pending_lock = threading.Lock()
_pending_install: Optional[str] = None


def queue_install(path) -> None:
    """登记待启动的安装包；由 app.py 在事件循环退出后再真正启动。"""
    global _pending_install
    with _pending_lock:
        _pending_install = str(path)


def consume_pending_install() -> Optional[str]:
    global _pending_install
    with _pending_lock:
        path, _pending_install = _pending_install, None
    return path


def launch_installer(path, restart: bool = True) -> None:
    """启动 Inno Setup 安装包。

    必须等旧主进程退出后再调用，否则 setup 的 CloseApplications 会和仍在
    运行的 exe 抢文件。/RESTARTAPP=1 由 installer.iss 检测，静默装完后
    自动拉起新版本。
    """
    path = Path(path)
    if not path.is_file():
        raise UpdateError(f"安装包不存在：{path}")
    args = [str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
    if restart:
        args.append("/RESTARTAPP=1")
    flags = 0
    if sys.platform == "win32":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    subprocess.Popen(
        args, cwd=str(path.parent), close_fds=True, creationflags=flags,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
