"""版本更新检测模块。"""
import json
import subprocess
import urllib.request
import urllib.error
import re
import threading
import queue

from config import APP_VERSION, GITHUB_REPO, BILIBILI_DYNAMIC_URL


def _parse_version(v):
    """将 'v1.1.0' 或 '1.1.0' 解析为 (1, 1, 0)。"""
    v = v.strip().lstrip("vV")
    parts = v.split(".")
    result = []
    for p in parts:
        m = re.match(r"\d+", p)
        result.append(int(m.group()) if m else 0)
    while len(result) < 3:
        result.append(0)
    return tuple(result[:3])


def _fetch_via_gh():
    """通过 gh CLI 获取最新 release tag（国内可用，走 SSH）。"""
    r = subprocess.run(
        ["gh", "release", "view", "--repo", GITHUB_REPO, "--json", "tagName,url"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        return None, None
    data = json.loads(r.stdout)
    return data.get("tagName", ""), data.get("url", "")


def _fetch_via_http():
    """通过 HTTP 直连 GitHub API 获取（海外可用）。"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("tag_name", ""), data.get("html_url", "")


def _fetch_latest_release():
    """先尝试 gh CLI，失败再 HTTP。"""
    tag, url = _fetch_via_gh()
    if tag:
        return tag, url
    tag, url = _fetch_via_http()
    if tag:
        return tag, url
    raise RuntimeError("无法获取版本信息")


def check_update():
    """
    同步检测更新。
    返回 dict:
      {"has_update": bool, "current": str, "latest": str, "url": str, "error": str|None}
    """
    current = APP_VERSION
    try:
        latest, url = _fetch_latest_release()
        if not latest:
            return {"has_update": False, "current": current, "latest": "",
                    "url": "", "error": "无法获取版本信息"}
        cur_tuple = _parse_version(current)
        lat_tuple = _parse_version(latest)
        has_update = lat_tuple > cur_tuple
        return {"has_update": has_update, "current": current,
                "latest": latest, "url": url, "error": None}
    except Exception as e:
        return {"has_update": False, "current": current, "latest": "",
                "url": "", "error": f"检测失败：{e}"}


class UpdateChecker:
    """线程安全的版本检测器，通过 queue + QTimer 回到主线程。"""

    def __init__(self):
        from PySide6.QtCore import QTimer
        self._q = queue.Queue()
        self._callback = None
        self._timer = QTimer()
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)

    def check(self, callback):
        """异步检测，callback(result) 在主线程调用。"""
        self._callback = callback
        self._timer.start()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        result = check_update()
        self._q.put(result)

    def _poll(self):
        try:
            result = self._q.get_nowait()
        except queue.Empty:
            return
        self._timer.stop()
        if self._callback:
            self._callback(result)
            self._callback = None
