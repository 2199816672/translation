"""
免费翻译引擎（可选配用户自己的 API 密钥）
- BingFreeTranslator: 逆向 cn.bing.com / 或用 Azure Cognitive Services 密钥
- MyMemoryFreeTranslator: api.mymemory.translated.net / 或用 API 密钥
- GoogleFreeTranslator: deep_translator / 或用 Google Cloud API 密钥

失败时直接抛出异常（由 translator.py 统一诊断），绝不把原文当作译文返回。
"""
import json
import re
import time
import requests


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_LANG_MAP_BING = {
    "auto": "auto-detect", "zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "en": "en",
    "ja": "ja", "ko": "ko", "fr": "fr", "de": "de", "es": "es", "ru": "ru",
}

_LANG_MAP_MYMEMORY = {
    "auto": "en", "zh-CN": "zh-CN", "zh-TW": "zh-TW", "en": "en",
    "ja": "ja", "ko": "ko", "fr": "fr", "de": "de", "es": "es", "ru": "ru",
}


def _split_text(text, max_len):
    parts, cur = [], ""
    for line in text.split("\n"):
        # 单行超长时按字符硬切分，否则 API 会拒绝请求
        while len(line) + (1 if cur else 0) > max_len:
            room = max_len - len(cur) - 1 if cur else max_len
            parts.append(cur + "\n" + line[:room] if cur else line[:room])
            line = line[room:]
            cur = ""
        if len(cur) + len(line) + 1 > max_len:
            if cur:
                parts.append(cur)
            cur = line
        else:
            cur = cur + "\n" + line if cur else line
    if cur:
        parts.append(cur)
    return parts or [text]


class BingFreeTranslator:
    """必应翻译 — 逆向 cn.bing.com，可选 Azure 密钥"""

    MAX_LEN = 5000

    def __init__(self, api_key=None, region=None):
        self.api_key = api_key
        self.region = region
        self._has_key = bool(api_key)

        if not self._has_key:
            self.session = requests.Session()
            self.session.headers.update(_HEADERS)
            self._ig = ""
            self._iid = ""
            self._key = 0
            self._token = ""
            self._subdomain = "cn"
            self._init()

    def _init(self):
        try:
            resp = self.session.get(
                "https://cn.bing.com/translator", timeout=15, allow_redirects=True,
            )
            body = resp.text
            m = re.search(r'IG:"([^"]+)"', body)
            if m:
                self._ig = m.group(1)
            m2 = re.search(r'data-iid="([^"]+)"', body)
            if m2:
                self._iid = m2.group(1)
            m3 = re.search(r'params_AbusePreventionHelper\s?=\s?([^\]]+\])', body)
            if m3:
                data = json.loads(m3.group(1))
                self._key, self._token = data[0], data[1]
        except Exception:
            pass

    def translate(self, text, from_lang="auto", to_lang="zh-CN"):
        if not text or not text.strip():
            return ""
        if self._has_key:
            return self._translate_azure(text, from_lang, to_lang)
        return self._translate_reverse(text, from_lang, to_lang)

    def _translate_azure(self, text, from_lang, to_lang):
        """Azure Cognitive Services — 用户自己的密钥"""
        from_lang_code = from_lang if from_lang != "auto" else ""
        url = (
            "https://api.cognitiveservices.azure.com/translate"
            f"?api-version=3.0&from={from_lang_code}&to={to_lang}"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Type": "application/json",
        }
        if self.region:
            headers["Ocp-Apim-Subscription-Region"] = self.region
        results = []
        for chunk in _split_text(text, self.MAX_LEN):
            resp = requests.post(
                url, headers=headers,
                json=[{"Text": chunk}],
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
            if body and "translations" in body[0]:
                results.append(body[0]["translations"][0]["text"])
            else:
                raise RuntimeError("Azure 翻译返回异常")
        return "\n".join(results)

    def _translate_reverse(self, text, from_lang, to_lang):
        """逆向 cn.bing.com — 无需密钥"""
        bing_from = _LANG_MAP_BING.get(from_lang, "auto-detect")
        bing_to = _LANG_MAP_BING.get(to_lang, "zh-Hans")
        results = []
        for chunk in _split_text(text, self.MAX_LEN):
            url = (
                f"https://{self._subdomain}.bing.com/ttranslatev3?isVertical=1"
                f"&IG={self._ig}&IID={self._iid}"
            )
            resp = self.session.post(
                url,
                data={
                    "fromLang": bing_from, "to": bing_to,
                    "text": chunk,
                    "token": self._token, "key": self._key,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"https://{self._subdomain}.bing.com/translator",
                },
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
            if body and "translations" in body[0]:
                results.append(body[0]["translations"][0]["text"])
            else:
                raise RuntimeError("必应翻译返回异常，可能是接口临时不可用")
        return "\n".join(results)


class MyMemoryFreeTranslator:
    """MyMemory 翻译 — 无需密钥，可选 API key / de 授权"""

    MAX_LEN = 500

    def __init__(self, api_key=None):
        self.api_key = api_key

    def translate(self, text, from_lang="auto", to_lang="zh-CN"):
        if not text or not text.strip():
            return ""
        src = _LANG_MAP_MYMEMORY.get(from_lang, "en")
        results = []
        for chunk in _split_text(text, self.MAX_LEN):
            params = {
                "q": chunk,
                "langpair": f"{src}|{to_lang}",
            }
            if self.api_key:
                params["key"] = self.api_key
            resp = requests.get(
                "https://api.mymemory.translated.net/get",
                params=params, timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
            translated = body.get("responseData", {}).get("translatedText", "")
            if translated:
                results.append(translated)
            else:
                raise RuntimeError("MyMemory 返回空结果")
        return "\n".join(results)


class GoogleFreeTranslator:
    """Google 翻译 — 有 VPN 用 Google，被墙 fallback MyMemory；可选 Google Cloud API 密钥"""

    MAX_LEN = 5000

    def __init__(self, api_key=None):
        self.api_key = api_key

    def translate(self, text, from_lang="auto", to_lang="zh-CN"):
        if not text or not text.strip():
            return ""
        if self.api_key:
            return self._translate_cloud(text, from_lang, to_lang)
        return self._translate_free(text, from_lang, to_lang)

    def _translate_cloud(self, text, from_lang, to_lang):
        """Google Cloud Translation API v2 — 用户自己的密钥"""
        source = from_lang if from_lang != "auto" else ""
        url = "https://translation.googleapis.com/language/translate/v2"
        results = []
        for chunk in _split_text(text, self.MAX_LEN):
            resp = requests.post(
                url,
                params={"key": self.api_key},
                json={"q": chunk, "target": to_lang, "source": source} if source
                     else {"q": chunk, "target": to_lang},
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data", {}).get("translations", [])
            if data:
                # 缺 translatedText 时不能拿原文冒充译文
                translated = str(data[0].get("translatedText") or "").strip()
                if not translated:
                    raise RuntimeError("Google Cloud 翻译未返回译文")
                results.append(translated)
            else:
                raise RuntimeError("Google Cloud 翻译返回空结果")
        return "\n".join(results)

    def _translate_free(self, text, from_lang, to_lang):
        """免费接口 — 有 VPN 用 Google，被墙 fallback MyMemory"""
        try:
            from deep_translator import GoogleTranslator
            source = "auto" if from_lang == "auto" else from_lang
            translator = GoogleTranslator(source=source, target=to_lang)
            results = []
            for chunk in _split_text(text, self.MAX_LEN):
                results.append(translator.translate(chunk))
            return "\n".join(results)
        except Exception:
            fallback = MyMemoryFreeTranslator()
            return fallback.translate(text, from_lang, to_lang)


class GoogleGTXFreeTranslator:
    """Google translate_a/single 免费接口（纯 requests，不依赖第三方包）。"""

    MAX_LEN = 1800
    LANG_MAP = {"auto": "auto", "zh-CN": "zh-CN", "zh-TW": "zh-TW",
                "en": "en", "ja": "ja", "ko": "ko", "fr": "fr",
                "de": "de", "es": "es", "ru": "ru"}

    def __init__(self, api_key=None):
        self.api_key = api_key

    def translate(self, text, from_lang="auto", to_lang="zh-CN"):
        if not text or not text.strip():
            return ""
        results = []
        for chunk in _split_text(text, self.MAX_LEN):
            resp = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx",
                        "sl": self.LANG_MAP.get(from_lang, "auto"),
                        "tl": self.LANG_MAP.get(to_lang, "zh-CN"),
                        "dt": "t", "q": chunk},
                # 国内多数网络到 Google 直接超时，快速失败交给切换链
                headers=_HEADERS, timeout=5)
            resp.raise_for_status()
            body = resp.json()
            rows = body[0] if body and isinstance(body[0], list) else []
            pieces = [str(row[0]) for row in rows if row and row[0]]
            if not pieces:
                raise RuntimeError("Google 免费接口返回空结果")
            results.append("".join(pieces))
        return "\n".join(results)


class LibreTranslateFreeTranslator:
    """LibreTranslate 公共实例；实例常挂，多个地址依次尝试。"""

    MAX_LEN = 1000
    ENDPOINTS = (
        "https://libretranslate.de/translate",
        "https://translate.terraprint.co/translate",
    )

    def __init__(self, api_key=None):
        self.api_key = api_key

    def translate(self, text, from_lang="auto", to_lang="zh-CN"):
        if not text or not text.strip():
            return ""
        src = "auto" if from_lang == "auto" else from_lang.split("-")[0]
        tgt = to_lang.split("-")[0]
        results = []
        for chunk in _split_text(text, self.MAX_LEN):
            last_error = None
            for url in self.ENDPOINTS:
                try:
                    payload = {"q": chunk, "source": src,
                               "target": tgt, "format": "text"}
                    if self.api_key:
                        payload["api_key"] = self.api_key
                    resp = requests.post(url, data=payload,
                                         headers=_HEADERS, timeout=5)
                    resp.raise_for_status()
                    translated = (resp.json() or {}).get("translatedText", "")
                    if not translated:
                        raise RuntimeError("返回空结果")
                    results.append(translated)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
            if last_error is not None:
                raise RuntimeError(
                    f"LibreTranslate 公共实例不可用: {last_error}")
        return "\n".join(results)


class YoudaoFreeTranslator:
    """有道翻译网页接口（无需密钥，尽力而为，失败由切换链跳过）。"""

    MAX_LEN = 1000
    LANG_MAP = {"zh-CN": "zh-CHS", "zh-TW": "zh-CHT", "en": "en",
                "ja": "ja", "ko": "ko", "fr": "fr", "de": "de",
                "es": "es", "ru": "ru"}

    def __init__(self, api_key=None):
        self.api_key = api_key

    def translate(self, text, from_lang="auto", to_lang="zh-CN"):
        if not text or not text.strip():
            return ""
        results = []
        for chunk in _split_text(text, self.MAX_LEN):
            data = {
                "i": chunk,
                "from": "AUTO" if from_lang == "auto" else from_lang,
                "to": self.LANG_MAP.get(to_lang, "AUTO"),
                "doctype": "json", "version": "2.1",
                "keyfrom": "fanyi.web", "ue": "utf-8",
                "action": "FY_BY_CLICKBUTTON", "typoResult": "false",
            }
            resp = requests.post("https://fanyi.youdao.com/translate",
                                 data=data, headers=_HEADERS, timeout=5)
            resp.raise_for_status()
            rows = (resp.json() or {}).get("translateResult") or []
            pieces = [str(item.get("tgt") or "")
                      for group in rows if isinstance(group, list)
                      for item in group if isinstance(item, dict)]
            translated = "".join(pieces).strip()
            if not translated:
                raise RuntimeError("有道翻译返回空结果")
            results.append(translated)
        return "\n".join(results)


_free_instances = {}


def get_free_translator(api_type, api_key=None, region=None):
    """获取免费翻译器实例。api_key / region 不为空时优先使用用户配置。"""
    cache_key = f"{api_type}:{api_key}:{region}"
    if cache_key not in _free_instances:
        mapping = {
            "bing_free": BingFreeTranslator,
            "mymemory_free": MyMemoryFreeTranslator,
            "google_free": GoogleFreeTranslator,
            "google_gtx_free": GoogleGTXFreeTranslator,
            "libretranslate_free": LibreTranslateFreeTranslator,
            "youdao_free": YoudaoFreeTranslator,
        }
        cls = mapping.get(api_type, MyMemoryFreeTranslator)
        if api_type == "bing_free":
            _free_instances[cache_key] = cls(api_key=api_key, region=region)
        else:
            _free_instances[cache_key] = cls(api_key=api_key)
    return _free_instances[cache_key]


def invalidate_free_translator(api_type):
    """密钥/区域变更时清除缓存实例"""
    for key in [k for k in _free_instances if k.split(":", 1)[0] == api_type]:
        _free_instances.pop(key, None)


# ── 免费接口自动故障切换 ──────────────────────────────────────
# 顺序按「国内实测可用概率 + 响应速度」排，用户选中的引擎永远排第一。
# Bing / MyMemory 稳定可用；Google 系列在国内常被墙，公共 LibreTranslate /
# 有道网页接口已多次被风控，放在后面并快速失败，别让切换链空转半分钟。
FREE_ENGINE_ORDER = (
    "bing_free", "mymemory_free", "google_free",
    "google_gtx_free", "libretranslate_free", "youdao_free",
)
FAILOVER_STICKY_SEC = 180
FAILED_ENGINE_TTL = 300        # 刚失败的引擎 5 分钟内不再重复撞
_failover_state = {"engine": None, "until": 0.0}
_failed_until = {}             # engine -> monotonic deadline


def free_engine_order():
    return FREE_ENGINE_ORDER


def current_free_engine():
    """当前实际生效的引擎（设置页展示用）；没有切换记录时返回 None。"""
    engine = _failover_state.get("engine")
    if engine and time.monotonic() < _failover_state.get("until", 0.0):
        return engine
    return None


def failed_engines():
    """近期失败、暂时跳过的引擎集合（诊断 / 测试可读取）。"""
    now = time.monotonic()
    return {name for name, until in _failed_until.items() if until > now}


def reset_failover(engine=None):
    """用户改首选引擎 / 关闭自动切换时清掉粘性与失败缓存。"""
    _failover_state["engine"] = engine
    _failover_state["until"] = (
        time.monotonic() + FAILOVER_STICKY_SEC if engine else 0.0)
    _failed_until.clear()


def _record_switch(old, new):
    """自动切换写进诊断中心，用户在「诊断与帮助」里能看到发生了什么。"""
    try:
        from diagnostics import record
        record("info", f"免费接口自动切换：{old} → {new}",
               "所选接口不可用，已改用其它免费引擎", "translation")
    except Exception:
        pass


def _echoed_source(text, result):
    """引擎是否把输入原样回显（免费接口降级 / 密钥失效的典型表现）。

    与 translator.test_translation 的判定保持一致：返回原文不算翻译成功，
    否则用户会拿到「译文 = 原文」却毫无报错。纯数字、纯标点、OK / A
    这类极短同形词本来就可能译成自己，不拦，避免误伤。
    """
    src = str(text or "").strip()
    out = str(result or "").strip()
    if not out or out != src:
        return False
    cjk = len(re.findall(r"[\u3400-\u9fff]", out))
    latin = len(re.findall(r"[A-Za-z]", out))
    return cjk >= 2 or latin >= 3


def translate_with_failover(text, from_lang, to_lang, preferred,
                            api_key="", region="", enabled=True,
                            on_switch=None):
    """按 preferred → 其它免费引擎依次尝试，返回 (译文, 实际引擎)。

    全部失败才抛异常；任何情况下都不得把原文当译文返回。切换成功后
    短暂记住可用引擎，避免每次请求都先撞一遍已经挂掉的首选。
    """
    if not enabled:
        # 关闭自动切换时只试用户选中的引擎；不能因为粘性缓存把备用顶到前面
        pool = [preferred]
    else:
        candidates = [e for e in FREE_ENGINE_ORDER if e != preferred]
        bad = failed_engines()
        fresh = [e for e in candidates if e not in bad]
        # 全都刚失败过也要留一个再试，否则网络恢复后永远不重试
        if not fresh and candidates:
            fresh = candidates[:1]
        pool = [preferred] + fresh
        sticky = current_free_engine()
        if sticky and sticky in pool:
            pool = [sticky] + [e for e in pool if e != sticky]
    errors = []
    for engine in pool:
        try:
            # 用户填的密钥只对它所属引擎生效，绝不转发给其它服务
            use_key = api_key if engine == preferred else ""
            use_region = region if engine == preferred else ""
            instance = get_free_translator(
                engine, api_key=use_key, region=use_region)
            result = instance.translate(text, from_lang, to_lang)
            if not str(result or "").strip():
                raise RuntimeError("返回空结果")
            # 降级回显原文不算成功：换下一个引擎，绝不把原文当译文交出去
            if _echoed_source(text, result):
                raise RuntimeError("原样返回原文，疑似未翻译")
            _failed_until.pop(engine, None)
            if engine != preferred:
                _failover_state["engine"] = engine
                _failover_state["until"] = (
                    time.monotonic() + FAILOVER_STICKY_SEC)
                if on_switch:
                    try:
                        on_switch(preferred, engine)
                    except Exception:
                        pass
                else:
                    _record_switch(preferred, engine)
            return result, engine
        except Exception as exc:
            _failed_until[engine] = time.monotonic() + FAILED_ENGINE_TTL
            errors.append(f"{engine}: {exc}")
    raise RuntimeError("所有免费翻译接口均不可用：" + "；".join(errors[-3:]))
