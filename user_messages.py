#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""面向用户的错误文案（零依赖，UI / 更新 / 安装链路共用）。

原则：界面上只显示一句中文，且必须告诉用户下一步能做什么；
底层英文异常原文保留在 tooltip / 日志里，方便排查。
放在独立零依赖模块里，CI 与本机没有 PySide6 时也能直接跑单元测试。
"""


def friendly_install_error(msg):
    """把 pip 原始英文日志压成一句中文可操作提示。"""
    low = str(msg or '').lower()
    # 国内镜像常见 403（限流 / 同步未完成）。安装链路已自动换源重试，
    # 这里要说明「已经换过源」而不是笼统地让用户检查网络。
    if ('403' in low or 'forbidden' in low
            or 'http error 403' in low):
        return ("下载源返回 403（镜像限流或同步异常），已自动换用其它源重试；"
                "若仍失败，请点「重试安装」，或按状态文字悬停提示里的命令"
                "改用 PyPI 官方源手动安装。")
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
