# yt_dlp/extractor/tiktok_utils/douyin/ac_signature.py
from __future__ import annotations

import string
import time

_SIGN_HEAD = '_02B4Z6wo00f01'
_SIGN_LENGTH = len(_SIGN_HEAD) + 33

# 每个字符编码 6 bit
_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits + '-.'

# 中间量 b 的高 14 位固定为该值，低 32 位为 timestamp ^ (site 哈希 * 65521)
_B_PREFIX = 0b10000000110000


def _hash_xor(s: str, seed: int) -> int:
    for char in s:
        seed = ((seed ^ ord(char)) * 65599) & 0xFFFFFFFF
    return seed


def _hash_mul(s: str, seed: int) -> int:
    for char in s:
        seed = (seed * 65599 + ord(char)) & 0xFFFFFFFF
    return seed


def _encode(num: int) -> str:
    """把 num 的低 30 位编码为 5 个字符。"""
    return ''.join(_ALPHABET[(num >> i) & 63] for i in range(24, -1, -6))


def _decode(chars: str) -> int:
    num = 0
    for char in chars:
        num = (num << 6) | _ALPHABET.index(char)
    return num


def _checksum(body: str) -> str:
    return f'{_hash_mul(body, 0):x}'[-2:].zfill(2)


def get_ac_signature(site: str, nonce: str, user_agent: str, timestamp: int | None = None) -> str:
    """
    生成 Douyin __ac_signature。

    参数：
        site:
            浏览器传入的是去掉协议头的 location.href，
            例如在首页完成挑战时为 www.douyin.com/。

        nonce:
            __ac_nonce Cookie 的值。

        user_agent:
            当前请求使用的 User-Agent。生成签名和请求页面应保持一致。

        timestamp:
            可选 Unix 秒级时间戳。默认使用当前时间。

    返回：
        __ac_signature 字符串。

    签名结构（_SIGN_HEAD 之后）：
        5+5 字符  中间量 b（含时间戳）
        5+1 字符  环境常量，浏览器每次现算，这里固定为一个旧值
        5+5 字符  UA 哈希 + nonce 哈希（以 b 的哈希为种子）
        5 字符    site 哈希
        2 字符    校验位
    """
    timestamp = int(time.time()) if timestamp is None else int(timestamp)

    a = _hash_xor(site, _hash_xor(str(timestamp), 0)) % 65521
    b = (_B_PREFIX << 32) | (timestamp ^ (a * 65521))
    c = _hash_xor(str(b), 0)
    e = (b >> 32) & 0xFFFFFFFF
    g = 582085784 ^ b
    j = ((_hash_xor(user_agent, c) % 65521) << 16) | (_hash_xor(nonce, c) % 65521)

    body = (
        _SIGN_HEAD
        + _encode(b >> 2)
        + _encode((b << 28) | (e >> 4))
        + _encode((e << 26) | (g >> 6))
        + _ALPHABET[g & 63]
        + _encode(j >> 2)
        + _encode((j << 28) | ((524576 ^ b) >> 4))
        + _encode(a))

    return body + _checksum(body)


def ac_signature_matches_nonce(signature: str, nonce: str) -> bool:
    """
    判断 __ac_signature 是否由该 __ac_nonce 生成。

    服务端对这一对 Cookie 实际校验的是：签名中的 nonce 哈希、末 2 位校验位；
    UA、site、时间戳、环境常量目前都不校验。这里复现前两项，
    用于判断已有签名能否继续使用（浏览器生成的签名同样适用）。
    """
    if len(signature) != _SIGN_LENGTH or not signature.startswith(_SIGN_HEAD):
        return False

    if _checksum(signature[:-2]) != signature[-2:]:
        return False

    fields = signature[len(_SIGN_HEAD):]
    try:
        b_low = ((_decode(fields[0:5]) << 2) & 0xFFFFFFFF) | ((_decode(fields[5:10]) >> 28) & 3)
        j = (_decode(fields[16:21]) << 2) | ((_decode(fields[21:26]) >> 28) & 3)
    except ValueError:  # 含编码表之外的字符
        return False

    c = _hash_xor(str((_B_PREFIX << 32) | b_low), 0)
    return (j & 0xFFFF) == _hash_xor(nonce, c) % 65521
