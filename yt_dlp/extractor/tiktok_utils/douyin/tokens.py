# yt_dlp/extractor/tiktok/douyin/tokens.py
from __future__ import annotations

import random
import string
import time


def generate_s_v_web_id() -> str:
    """
    生成 Douyin Web 访客标识 s_v_web_id。

    说明：
    - 这是非登录访客标识；
    - 目前仅 Douyin 使用，因此放在 douyin 子模块下。
    """
    base_str = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
    ms = int(time.time() * 1000)

    b36 = ''
    while ms > 0:
        r = ms % 36
        b36 = (str(r) if r < 10 else chr(ord('a') + r - 10)) + b36
        ms //= 36

    chars = [''] * 36
    chars[8] = chars[13] = chars[18] = chars[23] = '_'
    chars[14] = '4'

    for i in range(36):
        if chars[i]:
            continue
        n = int(random.random() * len(base_str))
        chars[i] = base_str[3 & n | 8 if i == 19 else n]

    return f'verify_{b36}_{"".join(chars)}'


def generate_ms_token(length: int = 182) -> str:
    """
    生成 Douyin Web 常见 msToken。

    说明：
    - 当前可视为匿名 Web 环境补充 Cookie；
    - 不是登录态；
    - 不是 a_bogus 或 __ac_signature 的核心算法。
    """
    alphabet = string.ascii_letters + string.digits + '-_'
    return ''.join(random.choice(alphabet) for _ in range(length))
