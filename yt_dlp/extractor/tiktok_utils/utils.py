# yt_dlp/extractor/tiktok_utils/utils.py
from __future__ import annotations


def response_snippet(text, limit=200):
    """
    把响应体压成一行并截断，用于失败原因日志（TikTok 与抖音共用）。
    """
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else f'{text[:limit]}...'
