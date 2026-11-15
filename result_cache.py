#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用结果缓存：把重复的翻译 / AI 润色 / OCR 计算变成一次内存查询。

设计取舍
--------
* **只缓存成功结果**：调用方仅在拿到非空、非报错的结果后 put。异常 / 空译文
  一律不缓存，绝不把「失败」固化成几天内都返回原文（翻译契约）。
* **键只存摘要**：密钥、Token、Cookie 只参与 sha256，明文既不进内存键也不落盘。
* **内存 LRU + TTL + 容量上限**：命中 O(1)，不会像无限 dict 那样吃满内存；
  过期条目在读取和定期整理时清掉。
* **可选持久化**：翻译 / 润色结果跨会话复用（重启后同一句话秒回），原子写盘、
  攒够一定条数才落一次，避免每次请求都触发磁盘 IO；写盘失败绝不影响主流程。
* **线程安全**：截图翻译、批量任务、实时字幕可能同时读写，全部走 RLock。
* **可关闭**：设置页可随时关掉、改容量 / 保留天数、一键清空，立即生效无需重启。
"""
from __future__ import annotations

import atexit
import copy
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from app_paths import APP_DIR

CACHE_DIR_NAME = "cache"
CACHE_FORMAT_VERSION = 1
# 单条结果上限（字符）：OCR 行块 / 长段落可能较大，但不让缓存文件被一条撑爆
MAX_VALUE_CHARS = 60000
_FLUSH_EVERY = 16          # 攒够 N 次新增才落一次盘
_PRUNE_EVERY = 32          # 每 N 次新增做一次全量过期清理

_FALLBACK_SETTINGS = {
    "result_cache_enabled": True,
    "result_cache_max_entries": 500,
    "result_cache_ttl_days": 7,
    "result_cache_persist": True,
}


def cache_dir() -> Path:
    """缓存目录（用户数据目录内，升级覆盖时由安装程序排除，不会被删）。"""
    path = APP_DIR / CACHE_DIR_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def read_settings() -> dict:
    """读取缓存设置。延迟 import 避免与 config.py 形成导入环。"""
    try:
        from config import get_cache_settings
        return get_cache_settings()
    except Exception:
        return dict(_FALLBACK_SETTINGS)


def make_key(*parts) -> str:
    """把任意组合压成定长摘要。密钥等敏感值只进哈希，不出现在键里。"""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part if part is not None else "")
                      .encode("utf-8", "replace"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _safe_ns(namespace) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(namespace or "default"))


def _limit(value, cap=None) -> int:
    try:
        limit = max(1, int(value))
    except (TypeError, ValueError, OverflowError):
        limit = 500
    if cap:
        limit = min(limit, max(1, int(cap)))
    return limit


def _ttl(days) -> float:
    try:
        value = float(days)
    except (TypeError, ValueError, OverflowError):
        value = 7.0
    if value <= 0 or value != value:
        value = 7.0
    return value * 86400.0


class ResultCache:
    """一个命名空间的线程安全 LRU 缓存。"""

    def __init__(self, namespace, *, max_entries=None, ttl_days=None,
                 persistent=None, enabled=None):
        self.namespace = _safe_ns(namespace)
        self._lock = threading.RLock()
        self._data = {}          # key -> (写入时间戳, 值)
        self._hits = 0
        self._misses = 0
        self._dirty = 0
        self._puts = 0
        cfg = read_settings()
        self.enabled = bool(cfg.get("result_cache_enabled", True)
                            if enabled is None else enabled)
        self.persistent = bool(cfg.get("result_cache_persist", True)
                               if persistent is None else persistent)
        # 单个命名空间可以有更紧的上限（OCR 结果较大），设置页的全局值取小者
        self.max_entries_cap = max_entries
        self.max_entries = _limit(
            cfg.get("result_cache_max_entries", 500), max_entries)
        self.ttl_sec = _ttl(cfg.get("result_cache_ttl_days", 7)
                            if ttl_days is None else ttl_days)
        self._load()

    # ── 磁盘 ────────────────────────────────────────────────

    def _file_path(self) -> Path:
        return cache_dir() / f"{self.namespace}.json"

    def _load(self):
        path = self._file_path()
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(raw, dict)
                    or raw.get("version") != CACHE_FORMAT_VERSION):
                return
            entries = raw.get("entries")
            if not isinstance(entries, dict):
                return
            with self._lock:
                for key, item in entries.items():
                    if (not isinstance(key, str) or not isinstance(item, list)
                            or len(item) != 2):
                        continue
                    ts, value = item
                    if (not isinstance(ts, (int, float))
                            or not isinstance(value, (str, list, dict))):
                        continue
                    if self._expired(float(ts)):
                        continue
                    self._data[key] = (float(ts), value)
                while len(self._data) > self.max_entries:
                    self._data.pop(next(iter(self._data)), None)
        except (OSError, ValueError, UnicodeError, TypeError):
            # 缓存文件坏了就当没有，绝不因此让功能不可用
            pass

    def _delete_file_locked(self):
        try:
            self._file_path().unlink(missing_ok=True)
        except OSError:
            pass

    def _flush_locked(self):
        if not self.persistent:
            self._dirty = 0
            return
        try:
            path = self._file_path()
            payload = {
                "version": CACHE_FORMAT_VERSION,
                "entries": {k: [ts, v] for k, (ts, v) in self._data.items()},
            }
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, path)   # 原子替换，崩溃也不会留下半截文件
            self._dirty = 0
        except (OSError, TypeError, ValueError, UnicodeError):
            pass

    def flush(self):
        with self._lock:
            self._flush_locked()

    # ── 读写 ────────────────────────────────────────────────

    def _expired(self, ts) -> bool:
        return self.ttl_sec > 0 and (time.time() - ts) > self.ttl_sec

    def _prune_locked(self):
        for key in [k for k, (ts, _) in self._data.items()
                    if self._expired(ts)]:
            self._data.pop(key, None)

    def get(self, key):
        """命中返回值副本；未命中 / 过期 / 已关闭返回 None。"""
        with self._lock:
            self._misses += 1
            if not self.enabled or not key:
                return None
            item = self._data.get(key)
            if item is None:
                return None
            ts, value = item
            if self._expired(ts):
                self._data.pop(key, None)
                return None
            self._misses -= 1
            self._hits += 1
            # LRU：移到末尾
            self._data.pop(key, None)
            self._data[key] = (ts, value)
            return copy.deepcopy(value)

    def put(self, key, value) -> bool:
        """只接受成功结果；空值 / 过大值 / 已关闭时静默跳过。"""
        if not key:
            return False
        with self._lock:
            if not self.enabled:
                return False
        if value is None:
            return False
        if isinstance(value, str):
            if not value.strip() or len(value) > MAX_VALUE_CHARS:
                return False
        else:
            try:
                if (not isinstance(value, (list, dict))
                        or len(json.dumps(value, ensure_ascii=False))
                        > MAX_VALUE_CHARS):
                    return False
            except (TypeError, ValueError):
                return False
        with self._lock:
            self._data.pop(key, None)
            self._data[key] = (time.time(), copy.deepcopy(value))
            while len(self._data) > self.max_entries:
                self._data.pop(next(iter(self._data)), None)
            self._puts += 1
            self._dirty += 1
            if self._puts % _PRUNE_EVERY == 0:
                self._prune_locked()
            if self.persistent and self._dirty >= _FLUSH_EVERY:
                self._flush_locked()
        return True

    # ── 管理 ────────────────────────────────────────────────

    def configure(self, *, enabled=None, max_entries=None, ttl_days=None,
                  persistent=None):
        with self._lock:
            if enabled is not None:
                self.enabled = bool(enabled)
            if max_entries is not None:
                self.max_entries = _limit(max_entries, self.max_entries_cap)
            if ttl_days is not None:
                self.ttl_sec = _ttl(ttl_days)
            was_persistent = self.persistent
            if persistent is not None:
                self.persistent = bool(persistent)
            while len(self._data) > self.max_entries:
                self._data.pop(next(iter(self._data)), None)
            self._prune_locked()
            if was_persistent and not self.persistent:
                # 用户关掉「跨会话保存」：删掉已落盘文件，但不影响本次会话内存
                self._delete_file_locked()
                self._dirty = 0
            elif self.persistent and self._dirty:
                self._flush_locked()

    def clear(self):
        with self._lock:
            self._data.clear()
            self._dirty = 0
            self._delete_file_locked()

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "namespace": self.namespace,
                "enabled": self.enabled,
                "size": len(self._data),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": (self._hits / total) if total else 0.0,
                "persistent": self.persistent,
                "ttl_days": self.ttl_sec / 86400.0,
            }


# ── 全局注册表 ──────────────────────────────────────────────

_caches = {}
_caches_lock = threading.Lock()


def get_cache(namespace, **kwargs) -> ResultCache:
    ns = _safe_ns(namespace)
    with _caches_lock:
        cache = _caches.get(ns)
        if cache is None:
            cache = ResultCache(ns, **kwargs)
            _caches[ns] = cache
        return cache


def all_caches():
    with _caches_lock:
        return list(_caches.values())


def configure_all(**kwargs):
    for cache in all_caches():
        cache.configure(**kwargs)


def apply_settings():
    """设置页保存后调用：让运行中的缓存立刻按新配置工作，无需重启。"""
    cfg = read_settings()
    configure_all(
        enabled=cfg.get("result_cache_enabled", True),
        max_entries=cfg.get("result_cache_max_entries", 500),
        ttl_days=cfg.get("result_cache_ttl_days", 7),
        persistent=cfg.get("result_cache_persist", True),
    )


def total_stats() -> dict:
    caches = all_caches()
    stats = [c.stats() for c in caches]
    hits = sum(s["hits"] for s in stats)
    misses = sum(s["misses"] for s in stats)
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "requests": total,
        "hit_rate": (hits / total) if total else 0.0,
        "size": sum(s["size"] for s in stats),
        "enabled": all(s["enabled"] for s in stats) if stats else True,
        "namespaces": stats,
    }


def clear_all() -> int:
    """清空全部缓存（含上次会话落盘、本次尚未加载的命名空间文件）。"""
    caches = all_caches()
    removed = sum(c.stats()["size"] for c in caches)
    for cache in caches:
        cache.clear()
    # 只删自己格式的 json（及原子写残留的 .json.tmp），
    # 避免误删用户放进缓存目录的其它文件
    try:
        for path in cache_dir().glob("*.json*"):
            try:
                if path.name.endswith(".json.tmp"):
                    path.unlink(missing_ok=True)
                    continue
                raw = json.loads(path.read_text(encoding="utf-8"))
                if (isinstance(raw, dict)
                        and raw.get("version") == CACHE_FORMAT_VERSION):
                    path.unlink(missing_ok=True)
            except (OSError, ValueError, UnicodeError, TypeError):
                continue
    except OSError:
        pass
    return removed


def flush_all():
    for cache in all_caches():
        cache.flush()


def reset_for_tests():
    """测试专用：清空注册表（不碰磁盘文件）。"""
    with _caches_lock:
        _caches.clear()


atexit.register(flush_all)
