# yt_dlp/extractor/tiktok_utils/tiktok/api.py
"""
TikTok 网页端 /api/item/detail/ 的请求构造（TikTokIE 的 signed_web_api 渠道）。

- 参数集合以调研文档 2.13 的实测为准（Evil0ctal v5 base_params + itemId，加 user_is_login=false）：
  不需要用户 Cookie、msToken 为空、随机 19 位 device_id 即可返回 itemInfo.itemStruct，与页面 hydration 的
  (GearName, DataSize, FileHash) 一致；但返回的直连 CDN 地址（v16 / v19-webapp-prime）下载一律 403（带不带 Cookie 都一样），
  只有各档 UrlList 末尾的 www.tiktok.com/aweme/v1/play 镜像可下（302 到 CDN，无 Cookie 也 206），TikTokIE 只用镜像地址；
- device_id 缺失或 X-Dynosaur 无效时服务端返回 HTTP 200 空 body + 响应头 tt_orcas_res: 1；
- 参数顺序即签名顺序，签名后的 query 必须原样发送；请求头的 UA 必须与签名用的 UA 一致。
"""
from __future__ import annotations

from .websign import sign_web_query

TIKTOK_WEB_HOST = 'https://www.tiktok.com/'
ITEM_DETAIL_API_URL = 'https://www.tiktok.com/api/item/detail/'

# X-Dynosaur 含 UA 哈希，签名与请求头必须用同一个 UA。browser_platform / browser_version / os 参数与它对应（Mac + Chrome）；
# Chrome 146 与网页路径 curl_cffi 当前的伪装目标（chrome-146:macos）同代
TIKTOK_WEB_USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/146.0.0.0 Safari/537.36'
)


def build_item_detail_query(video_id, device_id):
    """
    返回有序的 (key, value) 列表；顺序即签名顺序，X-Dynosaur / msToken / X-Bogus / X-Gnarly 由签名器追加在末尾。

    browser_version 是 TikTok 截断后的 navigator.appVersion（版本号 + 平台首个单词），不是 UA 里的 Chrome 版本。
    """
    return [
        ('aid', '1988'),
        ('app_language', 'en'),
        ('app_name', 'tiktok_web'),
        ('browser_language', 'en-US'),
        ('browser_name', 'Mozilla'),
        ('browser_online', 'true'),
        ('browser_platform', 'MacIntel'),
        ('browser_version', '5.0 (Macintosh)'),
        ('channel', 'tiktok_web'),
        ('cookie_enabled', 'true'),
        ('device_id', str(device_id)),
        ('device_platform', 'web_pc'),
        ('focus_state', 'true'),
        ('from_page', 'user'),
        ('history_len', '4'),
        ('is_fullscreen', 'false'),
        ('is_page_visible', 'true'),
        ('language', 'en'),
        ('os', 'mac'),
        ('priority_region', 'US'),
        ('referer', ''),
        ('region', 'US'),
        ('root_referer', TIKTOK_WEB_HOST),
        ('screen_height', '1080'),
        ('screen_width', '1920'),
        ('tz_name', 'America/New_York'),
        ('user_is_login', 'false'),
        ('webcast_language', 'en'),
        ('itemId', str(video_id)),
    ]


def build_item_detail_url(video_id, device_id, user_agent=TIKTOK_WEB_USER_AGENT):
    """
    构造签名后的完整请求地址。返回值要原样交给 _download_webpage_handle / _download_json（不传 query=，避免重新编码）。
    """
    query = sign_web_query(build_item_detail_query(video_id, device_id), user_agent)
    return f'{ITEM_DETAIL_API_URL}?{query}'


def build_item_detail_headers(user_agent=TIKTOK_WEB_USER_AGENT):
    """
    请求头。Referer / Origin 与 Evil0ctal 的 DEFAULT_HEADERS 一致；UA 必须与 build_item_detail_url 的 user_agent 相同。
    """
    return {
        'User-Agent': user_agent,
        'Referer': TIKTOK_WEB_HOST,
        'Origin': TIKTOK_WEB_HOST.rstrip('/'),
        'Accept': 'application/json, text/plain, */*',
    }
