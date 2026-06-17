# yt_dlp/extractor/tiktok/douyin/constants.py
from __future__ import annotations


DOUYIN_WEBPAGE_HOST = 'https://www.douyin.com/'

DOUYIN_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0'
)

DOUYIN_DEFAULT_WEB_HEADERS = {
    'Referer': DOUYIN_WEBPAGE_HOST,
    'User-Agent': DOUYIN_USER_AGENT,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}
