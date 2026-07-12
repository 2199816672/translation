"""版本更新检测模块。"""
import json
import urllib.request
import urllib.error
import re
import threading

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


def _fetch_latest_release():
    """从 GitHub API 获取最新 release 信息，返回 (tag, url)。"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("tag_name", ""), data.get("html_url", "")


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
    except urllib.error.URLError:
        return {"has_update": False, "current": current, "latest": "",
                "url": "", "error": "网络连接失败，请检查网络"}
    except Exception as e:
        return {"has_update": False, "current": current, "latest": "",
                "url": "", "error": str(e)}


def check_update_async(callback):
    """
    异步检测更新，完成后在主线程回调 callback(result)。
    callback 签名: callback(result: dict)
    """
    def _worker():
        result = check_update()
        callback(result)
    threading.Thread(target=_worker, daemon=True).start()
