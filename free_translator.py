"""
免费翻译引擎（可选配用户自己的 API 密钥）
- BingFreeTranslator: 逆向 cn.bing.com / 或用 Azure Cognitive Services 密钥
- MyMemoryFreeTranslator: api.mymemory.translated.net / 或用 API 密钥
- GoogleFreeTranslator: deep_translator / 或用 Google Cloud API 密钥
"""
import json
import re
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
    "auto": "auto-detect", "zh-CN": "zh-Hans", "en": "en",
    "ja": "ja", "ko": "ko", "fr": "fr", "de": "de", "es": "es", "ru": "ru",
}

_LANG_MAP_MYMEMORY = {
    "auto": "en", "zh-CN": "zh-CN", "en": "en",
    "ja": "ja", "ko": "ko", "fr": "fr", "de": "de", "es": "es", "ru": "ru",
}


def _split_text(text, max_len):
    parts, cur = [], ""
    for line in text.split("\n"):
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
            try:
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
                    results.append(chunk)
            except Exception:
                results.append(chunk)
        return "\n".join(results)

    def _translate_reverse(self, text, from_lang, to_lang):
        """逆向 cn.bing.com — 无需密钥"""
        bing_from = _LANG_MAP_BING.get(from_lang, "auto-detect")
        bing_to = _LANG_MAP_BING.get(to_lang, "zh-Hans")
        results = []
        for chunk in _split_text(text, self.MAX_LEN):
            try:
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
                if "translations" in body[0]:
                    results.append(body[0]["translations"][0]["text"])
                else:
                    results.append(chunk)
            except Exception:
                results.append(chunk)
        return "\n".join(results)


class MyMemoryFreeTranslator:
    """MyMemory 翻译 — 无需密钥，可选 API key / de 授权"""

    MAX_LEN = 500

    def __init__(self, api_key=None):
        self.api_key = api_key

    def translate(self, text, from_lang="auto", to_lang="zh-CN"):
        if not text or not text.strip():
            return ""
        src = _LANG_MAP_MYMEMORY.get(from_lang, "auto")
        results = []
        for chunk in _split_text(text, self.MAX_LEN):
            try:
                params = {
                    "q": chunk,
                    "langpair": f"{src}|{to_lang}",
                }
                if self.api_key:
                    params["de"] = self.api_key
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
                    results.append(chunk)
            except Exception:
                results.append(chunk)
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
            try:
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
                    results.append(data[0].get("translatedText", chunk))
                else:
                    results.append(chunk)
            except Exception:
                results.append(chunk)
        return "\n".join(results)

    def _translate_free(self, text, from_lang, to_lang):
        """免费接口 — 有 VPN 用 Google，被墙 fallback MyMemory"""
        try:
            from deep_translator import GoogleTranslator
            source = "auto" if from_lang == "auto" else from_lang
            translator = GoogleTranslator(source=source, target=to_lang)
            results = []
            for chunk in _split_text(text, self.MAX_LEN):
                try:
                    results.append(translator.translate(chunk))
                except Exception:
                    results.append(chunk)
            return "\n".join(results)
        except Exception:
            fallback = MyMemoryFreeTranslator()
            return fallback.translate(text, from_lang, to_lang)


_free_instances = {}


def get_free_translator(api_type, api_key=None):
    """获取免费翻译器实例。api_key 不为空时优先使用用户密钥。"""
    if api_type not in _free_instances:
        mapping = {
            "bing_free": BingFreeTranslator,
            "mymemory_free": MyMemoryFreeTranslator,
            "google_free": GoogleFreeTranslator,
        }
        cls = mapping.get(api_type, MyMemoryFreeTranslator)
        _free_instances[api_type] = cls(api_key=api_key)
    return _free_instances[api_type]


def invalidate_free_translator(api_type):
    """密钥变更时清除缓存实例"""
    _free_instances.pop(api_type, None)
