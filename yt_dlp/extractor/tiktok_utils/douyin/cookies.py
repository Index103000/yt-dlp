# yt_dlp/extractor/tiktok_utils/douyin/cookies.py
from __future__ import annotations

import json

from .ac_signature import ac_signature_matches_nonce, get_ac_signature
from .constants import DOUYIN_AC_SIGNATURE_SITE, DOUYIN_DEFAULT_WEB_HEADERS, DOUYIN_WEBPAGE_HOST
from .tokens import generate_ms_token, generate_s_v_web_id


def cookie_state_debug(cookies) -> str:
    """
    返回安全的 Cookie 状态调试信息。

    注意：
    - 不返回 Cookie 原值；
    - 只返回是否存在，避免日志泄漏。
    """
    return (
        f'ttwid={bool(cookies.get("ttwid"))}, '
        f's_v_web_id={bool(cookies.get("s_v_web_id"))}, '
        f'msToken={bool(cookies.get("msToken"))}, '
        f'__ac_nonce={bool(cookies.get("__ac_nonce"))}, '
        f'__ac_signature={bool(cookies.get("__ac_signature"))}'
    )


def register_douyin_ttwid(ie, video_id, user_agent):
    """
    通过 ByteDance ttwid 注册接口获取 ttwid。

    这里故意接收 ie 实例，而不是使用 requests：
    - 遵循 yt-dlp 代理配置；
    - 遵循 yt-dlp 超时配置；
    - 使用 yt-dlp cookiejar；
    - 使用 yt-dlp 网络层日志和错误处理。
    """
    payload = {
        'region': 'cn',
        'aid': 6383,
        'needFid': False,
        'service': 'www.douyin.com',
        'migrate_info': {
            'ticket': '',
            'source': 'node',
        },
        'cbUrlProtocol': 'https',
        'union': True,
    }

    res = ie._download_webpage_handle(
        'https://ttwid.bytedance.com/ttwid/union/register/',
        video_id,
        'Registering Douyin ttwid cookie',
        'Unable to register Douyin ttwid cookie',
        fatal=False,
        data=json.dumps(payload, separators=(',', ':')).encode(),
        headers={
            'User-Agent': user_agent,
            'Content-Type': 'application/json',
            'Referer': DOUYIN_WEBPAGE_HOST,
        })

    if not res:
        return

    _, urlh = res
    set_cookie = urlh.headers.get('Set-Cookie') or ''

    ttwid = ie._search_regex(
        r'(?:^|,\s*|;\s*)ttwid=([^;,\s]+)',
        set_cookie,
        'ttwid',
        default=None)

    if ttwid:
        ie._set_cookie('.douyin.com', 'ttwid', ttwid)
        ie.write_debug('Douyin ttwid registered successfully')
    else:
        ie.write_debug('Douyin ttwid was not found in Set-Cookie')


def fetch_douyin_home_cookies(ie, video_id, user_agent, note='Fetching Douyin home cookies'):
    """
    请求 Douyin 首页，让服务端下发基础 Cookie。

    主要用于：
    - __ac_nonce
    - ttwid fallback
    - 其他服务端 Set-Cookie
    """
    ie._download_webpage(
        DOUYIN_WEBPAGE_HOST,
        video_id,
        note=note,
        errnote=False,
        fatal=False,
        headers={
            **DOUYIN_DEFAULT_WEB_HEADERS,
            'User-Agent': user_agent,
        })


def clear_douyin_cookie(ie, name):
    """
    删除 cookiejar 中 douyin.com 及其子域下所有名为 name 的 Cookie。

    浏览器导入的 Cookie 多为 host-only（www.douyin.com），本模块写入的是 .douyin.com；
    只覆盖其中一个会导致请求里同名 Cookie 并存。
    """
    for cookie in list(ie.cookiejar):
        domain = cookie.domain.lstrip('.')
        if cookie.name == name and (domain == 'douyin.com' or domain.endswith('.douyin.com')):
            ie.cookiejar.clear(cookie.domain, cookie.path, cookie.name)


def ensure_douyin_visitor_cookies(ie, video_id, user_agent):
    """
    准备 Douyin Web 匿名访客 Cookie，aweme/detail API 方案只需要这些。

    1. ttwid
        Douyin / ByteDance Web 访客标识。实测 API 只带 ttwid 即可返回 aweme_detail，缺失则为空响应。

    2. s_v_web_id
        Web 访客标识，本地生成。

    3. msToken
        Web 环境 token，本地随机生成。

    注意：
    - 不覆盖用户传入的已有 Cookie；
    - 获取 Cookie 和请求视频页/API 应尽量使用同一代理和 UA；
    - 所有网络请求都通过 yt-dlp 的 ie 实例发起。
    """
    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    if not cookies.get('ttwid'):
        register_douyin_ttwid(ie, video_id, user_agent)

    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    if not cookies.get('ttwid'):
        fetch_douyin_home_cookies(ie, video_id, user_agent, note='Fetching Douyin home cookies for ttwid')

    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    if not cookies.get('s_v_web_id'):
        ie._set_cookie('.douyin.com', 's_v_web_id', generate_s_v_web_id())
    if not cookies.get('msToken'):
        ie._set_cookie('.douyin.com', 'msToken', generate_ms_token())

    ie.write_debug(
        f'Douyin visitor cookies prepared: {cookie_state_debug(ie._get_cookies(DOUYIN_WEBPAGE_HOST))}')


def ensure_douyin_ac_cookies(ie, video_id, user_agent):
    """
    准备页面方案（SSR RENDER_DATA）额外需要的 __ac_nonce / __ac_signature。

    1. __ac_nonce
        由 www.douyin.com 首页响应 Set-Cookie 下发，有效期 30 分钟（Max-Age=1800）。

    2. __ac_signature
        由 nonce 计算，二者一一绑定，失配时服务端返回「验证码中间页」。

    签名比 nonce 活得久（本模块写入的不过期，浏览器写入的为 1 年），
    --cookies-from-browser、跨次复用的 --cookies 文件、运行超过 30 分钟的批量任务都会出现
    「新 nonce + 旧签名」。因此不看签名是否存在，而是校验它是否属于当前 nonce，
    属于则保留（包括浏览器生成的签名），否则重算。
    """
    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    if not cookies.get('__ac_nonce'):
        fetch_douyin_home_cookies(ie, video_id, user_agent, note='Fetching Douyin __ac_nonce')
        cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)

    ac_nonce = cookies.get('__ac_nonce')
    ac_signature = cookies.get('__ac_signature')

    if not ac_nonce:
        ie.write_debug('Douyin __ac_nonce was not issued')
    elif not (ac_signature and ac_signature_matches_nonce(ac_signature.value, ac_nonce.value)):
        clear_douyin_cookie(ie, '__ac_signature')
        ie._set_cookie(
            '.douyin.com', '__ac_signature',
            get_ac_signature(DOUYIN_AC_SIGNATURE_SITE, ac_nonce.value, user_agent))

    ie.write_debug(
        f'Douyin __ac cookies prepared: {cookie_state_debug(ie._get_cookies(DOUYIN_WEBPAGE_HOST))}')
