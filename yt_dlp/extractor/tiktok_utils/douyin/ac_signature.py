# yt_dlp/extractor/tiktok/douyin/ac_signature.py
from __future__ import annotations

import time


def get_ac_signature(site: str, nonce: str, user_agent: str, timestamp: int | None = None) -> str:
    """
    生成 Douyin __ac_signature。

    参数：
        site:
            一般为 www.douyin.com。

        nonce:
            __ac_nonce Cookie 的值。

        user_agent:
            当前请求使用的 User-Agent。生成签名和请求页面应保持一致。

        timestamp:
            可选 Unix 秒级时间戳。默认使用当前时间。

    返回：
        __ac_signature 字符串。
    """
    timestamp = int(time.time()) if timestamp is None else int(timestamp)

    def cal_one_str(one_str: str, orgi_iv: int) -> int:
        k = orgi_iv
        for char in one_str:
            k = ((k ^ ord(char)) * 65599) & 0xFFFFFFFF
        return k

    def cal_one_str_3(one_str: str, orgi_iv: int) -> int:
        k = orgi_iv
        for char in one_str:
            k = (k * 65599 + ord(char)) & 0xFFFFFFFF
        return k

    def get_one_chr(enc_chr_code: int) -> str:
        if enc_chr_code < 26:
            return chr(enc_chr_code + 65)
        if enc_chr_code < 52:
            return chr(enc_chr_code + 71)
        if enc_chr_code < 62:
            return chr(enc_chr_code - 4)
        return chr(enc_chr_code - 17)

    def enc_num_to_str(num: int) -> str:
        s = ''
        for i in range(24, -1, -6):
            s += get_one_chr((num >> i) & 63)
        return s

    sign_head = '_02B4Z6wo00f01'
    timestamp_s = str(timestamp)

    a = cal_one_str(site, cal_one_str(timestamp_s, 0)) % 65521

    bin_str = bin(timestamp ^ (a * 65521))[2:].zfill(32)
    b = int('10000000110000' + bin_str, 2)
    b_s = str(b)

    c = cal_one_str(b_s, 0)

    d = enc_num_to_str(b >> 2)
    e = (b // 4294967296) & 0xFFFFFFFF
    f = enc_num_to_str((b << 28) | (e >> 4))
    g = 582085784 ^ b
    h = enc_num_to_str((e << 26) | (g >> 6))
    i = get_one_chr(g & 63)

    j = (
        ((cal_one_str(user_agent, c) % 65521) << 16)
        | (cal_one_str(nonce, c) % 65521)
    )
    k = enc_num_to_str(j >> 2)
    l = enc_num_to_str((j << 28) | ((524576 ^ b) >> 4))
    m = enc_num_to_str(a)

    n = sign_head + d + f + h + i + k + l + m
    o = hex(cal_one_str_3(n, 0))[2:][-2:].zfill(2)

    return n + o
