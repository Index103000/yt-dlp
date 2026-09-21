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

DOUYIN_DEFAULT_WEB_HEADERS = {
    'Referer': DOUYIN_WEBPAGE_HOST,
    'User-Agent': DOUYIN_USER_AGENT,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}
