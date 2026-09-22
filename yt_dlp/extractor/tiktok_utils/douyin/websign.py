# yt_dlp/extractor/tiktok_utils/douyin/websign.py
"""
抖音 x-secsdk-web-signature（webSign）纯算实现。

Argus 安全网关对部分 IP 要求 aweme/v1/web/* 请求带有效 uifid 与该签名，判定顺序（实测）：
    不带 uifid                    -> 403 Uifid Not Found
    带 uifid、不带签名            -> 403 Signature Not Found
    uifid 无效（如随机 hex）      -> 403 Validate Error
    uifid 有效、签名错误          -> 403 Sign Invalid（argus_security_code: web_id_sign_invalid）
    uifid 有效、签名正确          -> 200

算法出处：cv-cat/DouYin_Spider utils/secsdk_web_sign.py（hook 安全 SDK 的 webSignUrl 与 CryptoJS.MD5 得到，
盐为 SDK 虚拟机里写死的常量）；media-parser 的 _sign_secsdk 与之相同，本实现与其逐字节一致。
"""
from __future__ import annotations

import hashlib
import time
import urllib.parse

WEBSIGN_SALT = 'A96D855A08C0A9707F8BEF0D9A527E4E'

# 与 JS encodeURIComponent 一致：Python quote 本身不编码 A-Za-z0-9_.-~，再加上 !*'()
_SAFE_CHARS = "!*'()"


def canonicalize_query(query_string):
    """
    按 SDK 的规则规范化 query：保持原顺序、不排序；key 只解码不重编码；
    value 先解码（'+' 变空格）再按 encodeURIComponent 重编码；无 '=' 的参数补成 'k='。
    """
    parts = []
    for pair in query_string.split('&'):
        if not pair:
            continue
        key, _, value = pair.partition('=')
        key = urllib.parse.unquote_plus(key, encoding='utf-8', errors='replace')
        value = urllib.parse.unquote_plus(value, encoding='utf-8', errors='replace')
        parts.append(f'{key}={urllib.parse.quote(value, safe=_SAFE_CHARS, encoding="utf-8")}')
    return '&'.join(parts)


def sign_web_query(query_string, uifid, timestamp=None):
    """
    返回签名后的 query 字符串（已含 uifid、timestamp、x-secsdk-web-signature），必须原样发送。

    服务端按收到的 query 重新规范化后校验，所以不能再把它交给会重新编码的函数（如 update_url_query），
    而是直接拼成完整 URL 请求。实测 timestamp 不校验时效，a_bogus 不是必需的。
    """
    timestamp = int(time.time()) if timestamp is None else int(timestamp)
    canonical = canonicalize_query(query_string)
    if 'uifid' not in (part.partition('=')[0] for part in canonical.split('&') if part):
        encoded_uifid = urllib.parse.quote(str(uifid), safe=_SAFE_CHARS, encoding='utf-8')
        canonical = f'{canonical}&uifid={encoded_uifid}' if canonical else f'uifid={encoded_uifid}'

    signed_query = f'{canonical}&timestamp={timestamp}'
    signature = hashlib.md5(f'{uifid}_{timestamp}_{WEBSIGN_SALT}_{signed_query}'.encode()).hexdigest()
    return f'{signed_query}&x-secsdk-web-signature={signature}'
