# yt_dlp/extractor/tiktok_utils/douyin/constants.py
from __future__ import annotations

DOUYIN_WEBPAGE_HOST = 'https://www.douyin.com/'

# __ac_signature 的 site 参数：浏览器传入的是去掉协议头的 location.href。
# __ac_nonce 由首页下发，对应浏览器在首页完成挑战，即 www.douyin.com/（已用浏览器实际签名反解核对）。
# 服务端目前不校验该字段，这里只是与浏览器保持一致。
DOUYIN_AC_SIGNATURE_SITE = DOUYIN_WEBPAGE_HOST.partition('://')[2]

DOUYIN_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0'
)

# 以 open.douyin.com 为来源请求 www.douyin.com 的 aweme/detail：不经过 Argus 的 uifid / 签名校验，
# 不需要 a_bogus 和 Cookie，海外 IP 也可用；返回的 aweme_detail 与带 a_bogus 的 Web API 一致（2026-09 实测）。
DOUYIN_OPEN_API_HEADERS = {
    'Origin': 'https://open.douyin.com',
    'Referer': 'https://open.douyin.com',
    'User-Agent': DOUYIN_USER_AGENT,
}

DOUYIN_DEFAULT_WEB_HEADERS = {
    'Referer': DOUYIN_WEBPAGE_HOST,
    'User-Agent': DOUYIN_USER_AGENT,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}
