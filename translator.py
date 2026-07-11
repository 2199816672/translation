"""
翻译功能模块 - 支持多种翻译API
"""
from deep_translator import DeeplTranslator, BaiduTranslator
from deep_translator.exceptions import RequestError
import sys
import os
import hashlib
import hmac
import time
import base64
import json
import requests
import socket

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import user_config, TRANSLATION_APIS, DEFAULT_TRANSLATION_API, FREE_APIS, FREE_API_KEY_FIELDS
from free_translator import get_free_translator, invalidate_free_translator


class TranslationError(Exception):
    """翻译异常，包含用户友好的错误信息"""
    def __init__(self, message, reason=""):
        super().__init__(message)
        self.reason = reason


def _diagnose_network_error(e):
    """诊断网络错误，返回用户友好的提示"""
    if isinstance(e, requests.exceptions.ConnectionError):
        return "无法连接到翻译服务器，请检查网络连接"
    if isinstance(e, requests.exceptions.Timeout):
        return "翻译请求超时，服务器可能繁忙或网络较慢"
    if isinstance(e, requests.exceptions.SSLError):
        return "SSL连接失败，可能被防火墙拦截"
    if isinstance(e, socket.timeout):
        return "网络超时，可能被防火墙拦截"
    if isinstance(e, requests.exceptions.HTTPError):
        code = e.response.status_code if e.response is not None else 0
        if code == 403:
            return "翻译服务拒绝访问(403)，可能被区域限制或IP被封禁"
        if code == 429:
            return "翻译请求过于频繁(429)，请稍后再试"
        if code >= 500:
            return f"翻译服务器出错({code})，请稍后再试"
        return f"HTTP错误 {code}"
    msg = str(e).lower()
    if "ssl" in msg or "certificate" in msg:
        return "SSL证书验证失败，可能被防火墙拦截"
    if "timeout" in msg:
        return "请求超时，可能被防火墙拦截"
    if "connection" in msg and ("refused" in msg or "reset" in msg):
        return "连接被拒绝，翻译服务可能在该区域不可用"
    if "name resolution" in msg or "dns" in msg:
        return "DNS解析失败，请检查网络连接"
    return ""


class TencentTranslator:
    """腾讯云翻译 API（需要密钥）"""

    LANG_MAP = {
        'zh-CN': 'zh', 'en': 'en', 'ja': 'ja', 'ko': 'ko',
        'fr': 'fr', 'de': 'de', 'es': 'es', 'ru': 'ru',
    }

    def __init__(self, secret_id, secret_key):
        self.secret_id = secret_id
        self.secret_key = secret_key

    def translate(self, text):
        service = "tmt"
        action = "TextTranslate"
        version = "2018-03-21"
        region = "ap-guangzhou"
        host = "tmt.tencentcloudapi.com"
        url = f"https://{host}"
        timestamp = int(time.time())
        date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))

        payload = json.dumps({
            "SourceText": text,
            "Source": "auto",
            "Target": "zh",
            "ProjectId": 0,
        })
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        canonical_request = (
            f"POST\n/\n\nhost={host}\n"
            f"content-type:application/json\n"
            f"x-tc-action:{action.lower()}\n"
            f"\nhost;content-type;x-tc-action\n{payload_hash}"
        )
        credential_scope = f"{date}/{service}/tc3_request"
        string_to_sign = (
            f"TC3-HMAC-SHA256\n{timestamp}\n{credential_scope}\n"
            + hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        )

        def _hmac_sha256(key, msg):
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        secret_date = _hmac_sha256(("TC3" + self.secret_key).encode("utf-8"), date)
        secret_service = _hmac_sha256(secret_date, service)
        secret_signing = _hmac_sha256(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        authorization = (
            f"TC3-HMAC-SHA256 Credential={self.secret_id}/{credential_scope}, "
            f"SignedHeaders=host;content-type;x-tc-action, Signature={signature}"
        )

        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json",
            "Host": host,
            "X-TC-Action": action,
            "X-TC-Version": version,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Region": region,
        }

        resp = requests.post(url, data=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        if "Response" in body and "TargetText" in body["Response"]:
            return body["Response"]["TargetText"]
        if "Response" in body and "Error" in body["Response"]:
            raise Exception(body["Response"]["Error"].get("Message", "腾讯翻译错误"))
        raise Exception("腾讯翻译返回异常")


class VolcengineTranslator:
    """火山翻译 API（字节跳动，需要密钥）"""

    LANG_MAP = {
        'zh-CN': 'zh', 'en': 'en', 'ja': 'ja', 'ko': 'ko',
        'fr': 'fr', 'de': 'de', 'es': 'es', 'ru': 'ru',
    }

    def __init__(self, app_id, access_token):
        self.app_id = app_id
        self.access_token = access_token

    def translate(self, text):
        url = "https://api.volcengine.com/api/translate/v2/translate"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer; {self.access_token}",
        }
        payload = json.dumps({
            "app": {"appid": self.app_id, "token": "access_token"},
            "source": {"text_list": [text]},
            "target": {"language": "zh"},
        })
        resp = requests.post(url, data=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        if "data" in body and "translations" in body["data"]:
            translations = body["data"]["translations"]
            if translations:
                return translations[0].get("text", text)
        if "message" in body:
            raise Exception(f"火山翻译: {body['message']}")
        raise Exception("火山翻译返回异常")


class Translator:
    def __init__(self, source_lang='auto', target_lang='zh-CN', api_type=None):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.api_type = api_type or user_config.get('translation_api', DEFAULT_TRANSLATION_API)
        self.translator = None
        self._free_engine = None
        self._free_api_key = ''

        self.deepl_lang_map = {
            'zh-CN': 'ZH', 'en': 'EN-US', 'ja': 'JA', 'ko': 'KO',
            'fr': 'FR', 'de': 'DE', 'es': 'ES', 'ru': 'RU',
        }
        self.baidu_lang_map = {
            'zh-CN': 'zh', 'en': 'en', 'ja': 'jp', 'ko': 'kor',
            'fr': 'fra', 'de': 'de', 'es': 'spa', 'ru': 'ru',
        }

    def _init_translator(self):
        if self.translator is None:
            if self.api_type in FREE_APIS:
                # 获取用户可能填写的 API key
                key_field = FREE_API_KEY_FIELDS.get(self.api_type)
                user_key = user_config.get(key_field, '') if key_field else ''
                invalidate_free_translator(self.api_type)
                self._free_engine = self.api_type
                self._free_api_key = user_key
                self.translator = "FREE_ENGINE"
            elif self.api_type == 'deepl':
                target = self.deepl_lang_map.get(self.target_lang, 'ZH')
                api_key = user_config.get('deepl_api_key', '')
                if api_key:
                    self.translator = DeeplTranslator(api_key=api_key, target=target)
                else:
                    self._free_engine = 'bing_free'
                    self._free_api_key = ''
                    self.translator = "FREE_ENGINE"
            elif self.api_type == 'baidu':
                appid = user_config.get('baidu_appid', '')
                secret = user_config.get('baidu_secret', '')
                target = self.baidu_lang_map.get(self.target_lang, 'zh')
                if appid and secret:
                    self.translator = BaiduTranslator(api_key=appid, secret_key=secret, target=target)
                else:
                    self._free_engine = 'bing_free'
                    self._free_api_key = ''
                    self.translator = "FREE_ENGINE"
            elif self.api_type == 'tencent':
                secret_id = user_config.get('tencent_secret_id', '')
                secret_key = user_config.get('tencent_secret_key', '')
                if secret_id and secret_key:
                    self.translator = TencentTranslator(secret_id, secret_key)
                else:
                    self._free_engine = 'bing_free'
                    self._free_api_key = ''
                    self.translator = "FREE_ENGINE"
            elif self.api_type == 'volcengine':
                app_id = user_config.get('volcengine_app_id', '')
                access_token = user_config.get('volcengine_token', '')
                if app_id and access_token:
                    self.translator = VolcengineTranslator(app_id, access_token)
                else:
                    self._free_engine = 'bing_free'
                    self._free_api_key = ''
                    self.translator = "FREE_ENGINE"
            else:
                self._free_engine = 'bing_free'
                self._free_api_key = ''
                self.translator = "FREE_ENGINE"
        return self.translator

    def translate(self, text):
        if not text or not text.strip():
            return ""
        self._init_translator()

        if self._free_engine:
            return self._translate_free(text)

        return self._translate_paid(text)

    def _translate_free(self, text):
        free_t = get_free_translator(self._free_engine, api_key=self._free_api_key)
        if not free_t:
            return text
        paragraphs = text.split('\n')
        results = []
        for p in paragraphs:
            if p.strip():
                try:
                    results.append(free_t.translate(p, self.source_lang, self.target_lang))
                except TranslationError:
                    raise
                except Exception as e:
                    hint = _diagnose_network_error(e)
                    msg = f"翻译失败: {e}"
                    if hint:
                        msg += f"\n原因: {hint}"
                    raise TranslationError(msg, reason=hint or str(e))
            else:
                results.append("")
        return '\n'.join(results)

    def _translate_paid(self, text):
        paragraphs = text.split('\n')
        results = []
        for paragraph in paragraphs:
            if paragraph.strip():
                try:
                    results.append(self.translator.translate(paragraph))
                except RequestError as e:
                    hint = _diagnose_network_error(e)
                    msg = f"翻译请求失败: {e}"
                    if hint:
                        msg += f"\n原因: {hint}"
                    raise TranslationError(msg, reason=hint or str(e))
                except Exception as e:
                    hint = _diagnose_network_error(e)
                    msg = f"翻译错误: {e}"
                    if hint:
                        msg += f"\n原因: {hint}"
                    raise TranslationError(msg, reason=hint or str(e))
            else:
                results.append("")
        return '\n'.join(results)

    def set_target_lang(self, target_lang):
        self.target_lang = target_lang
        self.translator = None

    def set_source_lang(self, source_lang):
        self.source_lang = source_lang
        self.translator = None

    def set_api_type(self, api_type):
        self.api_type = api_type
        self.translator = None
        self._free_engine = None
        self._free_api_key = ''


_translator_instance = None


def get_translator_instance(source_lang='auto', target_lang='zh-CN', api_type=None):
    global _translator_instance
    if _translator_instance is None:
        _translator_instance = Translator(source_lang, target_lang, api_type)
    else:
        if api_type:
            _translator_instance.set_api_type(api_type)
    return _translator_instance
