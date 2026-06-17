# yt_dlp/extractor/tiktok/douyin/abogus.py
from __future__ import annotations

from .abogus_python import generate_abogus_python


def generate_abogus(params: str, user_agent: str) -> str:
    """
    生成 Douyin a_bogus 的稳定入口。

    当前实现：
        Python 原生算法。

    后续可替换为：
        - 执行 JS 文件；
        - 调用 Node / Deno；
        - 调用远程/本地签名服务；
        - 按 extractor-arg 或环境变量切换不同实现。

    只要保持这个函数签名不变，DouyinIE 不需要跟着改。
    """
    return generate_abogus_python(params, user_agent)
