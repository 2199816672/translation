#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""面向用户的错误文案（零依赖，UI / 更新 / 安装链路共用）。

原则：界面上只显示一句中文，且必须告诉用户下一步能做什么；
底层英文异常原文保留在 tooltip / 日志里，方便排查。
放在独立零依赖模块里，CI 与本机没有 PySide6 时也能直接跑单元测试。
"""


def friendly_install_error(msg, engine=None):
    """把 pip 原始英文日志压成一句中文可操作提示。

    ``engine`` 是本次正在安装的 OCR 引擎：装的就是 windows（winrt）时不能再
    把「改用 Windows 自带 OCR」当退路，否则用户被支使回刚失败的那条链路。
    """
    low = str(msg or '').lower()
    # 冻结版 pip 的 distlib 启动器异常：三个源会报同一个内部 Exception，
    # 既不是 403 也不是超时。必须先认出来，否则下面全按「检查网络」处理，
    # 用户会一直重试一个永远好不了的安装。
    if ('distlibexception' in low or 'unable to locate finder' in low
            or '_get_launcher' in low):
        return ("安装程序内部的组件安装器出错（pip 生成命令行启动器失败），"
                "与网络、镜像、pip.ini 都无关，换源或重试不会解决；"
                "请更新到修复版安装包。现在可先在设置页把 OCR 引擎切到"
                "「Windows 系统自带」正常使用（免下载模型，"
                "若提示缺 winrt 再点「安装 winrt」）。")
    # 403 最常见的原因是本机 pip.ini 把全局源设成了失效镜像（清华源
    # /simple/easyocr/ 实测直接 403）。安装链路已隔离这份配置并换用其它源
    # 重试（首选腾讯云镜像）；能走到这句说明全部源都失败了，要如实说结论
    # 并给出退路，别让用户以为还在自动恢复中。
    if ('403' in low or 'forbidden' in low
            or 'http error 403' in low):
        base = ("下载源返回 403（镜像限流或本机 pip 全局配置失效）；已忽略失效"
                "配置并自动换用其它源重试（首选腾讯云镜像），但本次全部源都"
                "失败了。请点「重试安装」")
        if engine == 'windows':
            return (base + "或稍后再试；这次装的就是 Windows 自带 OCR 所需的 "
                    "winrt 组件，请检查网络后重试，"
                    "把鼠标移到状态文字上可看各源失败原因。")
        return (base + "；装不上也能在设置页改用「Windows 系统自带」OCR"
                "（无需下载模型），把鼠标移到状态文字上可看各源失败原因。")
    if 'timed out' in low or 'timeout' in low or 'read timed out' in low:
        return "安装超时，通常是网络不稳定。请检查网络后点「重试安装」。"
    if 'no space left' in low or 'not enough space' in low or 'disk full' in low:
        return "磁盘空间不足，请清理出空间后重试。"
    if 'permission denied' in low or 'access is denied' in low:
        return "没有写入权限，请用普通用户重新启动程序后重试。"
    if ('could not find a version' in low or 'no matching distribution' in low
            or 'unsupported' in low):
        return "没有匹配当前系统的组件版本，请点「重试安装」或稍后再试。"
    if ('connection' in low or 'ssl' in low or 'certificate' in low
            or 'proxy' in low or 'resolve' in low or 'network' in low):
        return "无法连接下载源（网络 / 代理 / 证书问题），请检查网络后点「重试安装」。"
    return "安装未完成，请检查网络后点「重试安装」；把鼠标移到状态文字上可查看原始日志。"


def friendly_model_error(msg):
    """模型下载失败：给出中文原因和可操作下一步，原始异常只放 tooltip。"""
    low = str(msg or '').lower()
    if 'timed out' in low or 'timeout' in low or '10060' in low:
        return "下载超时，请检查网络 / 代理后点「下载所选模型」重试。"
    if ('connection' in low or 'ssl' in low or 'certificate' in low
            or 'proxy' in low or 'resolve' in low or 'network' in low):
        return "无法连接模型服务器，请检查网络后重试（会自动切换国内镜像）。"
    if 'no space left' in low or 'not enough space' in low:
        return "磁盘空间不足，大模型需要数 GB 空间，请清理后重试。"
    if 'huggingface-hub' in low:
        return "缺少模型下载组件，请在「设置」页在线安装后重试。"
    return "下载失败，请检查网络后点「下载所选模型」重试；把鼠标移到状态文字上可查看原始错误。"


def friendly_service_error(exc, what="服务") -> str:
    """把翻译 / 识别服务的底层英文异常压成一句中文可操作提示。"""
    low = str(exc or '').lower()
    if 'timed out' in low or 'timeout' in low or '10060' in low:
        return f"连接{what}超时，请检查网络 / 代理后重试"
    if ('getaddrinfo' in low or 'name or service not known' in low
            or '11001' in low or 'name resolution' in low or 'dns' in low):
        return f"找不到{what}地址，请检查服务地址或 DNS 设置"
    if ('refused' in low or '10061' in low or 'connection reset' in low
            or 'unreachable' in low):
        return f"无法连接{what}，请确认服务已启动、地址与端口正确"
    if 'ssl' in low or 'certificate' in low:
        return "证书校验失败，请检查系统时间或代理证书设置"
    if '401' in low or '403' in low or 'unauthorized' in low:
        return "密钥无效或权限不足，请检查填写的密钥"
    if '429' in low or 'rate limit' in low:
        return "服务限流，请稍后再试"
    return f"连接{what}失败，请检查地址、密钥与网络后重试"


def friendly_network_error(exc) -> str:
    """把底层英文网络异常转成用户能照做的中文短句。"""
    raw = str(exc or '')
    low = raw.lower()
    if 'timed out' in low or 'timeout' in low or '10060' in low:
        return ("连接更新服务器超时，多为网络不稳定或代理未配置。"
                "请检查网络后重试，也可打开发布页手动下载。")
    if ('10061' in low or 'refused' in low or 'getaddrinfo' in low
            or 'name or service not known' in low or '11001' in low
            or 'nodename nor servname' in low):
        return "无法解析或连接更新服务器，请检查网络 / DNS 后重试。"
    if 'ssl' in low or 'certificate' in low:
        return "证书校验失败，请检查系统时间与代理证书设置后重试。"
    if 'rate limit' in low or '403' in low or '401' in low:
        return "GitHub 接口限流，请稍后重试，或打开发布页手动下载。"
    if '404' in low:
        return "找不到发布信息（HTTP 404），新版本可能尚未发布。"
    return "检查更新失败，请稍后重试，或打开发布页查看最新版本。"
