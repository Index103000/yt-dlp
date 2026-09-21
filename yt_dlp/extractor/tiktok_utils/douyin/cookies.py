# yt_dlp/extractor/tiktok/douyin/cookies.py
from __future__ import annotations

import json

from .ac_signature import get_ac_signature
from .constants import DOUYIN_WEBPAGE_HOST
from .tokens import generate_ms_token, generate_s_v_web_id


DOUYIN_COOKIE_PREFERRED_ORDER = [
    'ttwid',
    's_v_web_id',
    'msToken',
    '__ac_nonce',
    '__ac_signature',
    'passport_csrf_token',
    'passport_csrf_token_default',
    'sid_tt',
    'sessionid',
    'sessionid_ss',
    'uid_tt',
    'uid_tt_ss',
    'odin_tt',
]


def build_douyin_cookie_header(cookies) -> str:
    """
    从 yt-dlp cookie mapping 构造 Cookie Header。

    参数：
        cookies:
            通常来自 ie._get_cookies(DOUYIN_WEBPAGE_HOST)。

    说明：
    - 不打印 Cookie 原值；
    - 优先排列关键 Cookie，便于调试；
    - 兼容用户通过 --cookies / --cookies-from-browser 传入的 Cookie。
    """
    pairs = []
    used = set()

    for name in DOUYIN_COOKIE_PREFERRED_ORDER:
        cookie = cookies.get(name)
        if cookie and cookie.value:
            pairs.append(f'{name}={cookie.value}')
            used.add(name)

    for name, cookie in cookies.items():
        if name in used:
            continue
        if not cookie.value:
            continue
        pairs.append(f'{name}={cookie.value}')

    return '; '.join(pairs)


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


def fetch_douyin_home_cookies(ie, video_id, headers, note='Fetching Douyin home cookies'):
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
        headers=headers)


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


def ensure_douyin_cookies(ie, video_id, user_agent, headers=None):
    """
    准备 Douyin Web 匿名访问 Cookie。

    重点 Cookie：
    1. ttwid
        Douyin / ByteDance Web 访客标识。

    2. s_v_web_id
        Web 访客标识，本地生成。

    3. msToken
        Web 环境 token，本地随机生成。

    4. __ac_nonce
        通常由 www.douyin.com 首页响应 Set-Cookie 返回。

    5. __ac_signature
        基于 host + __ac_nonce + User-Agent 生成。

    注意：
    - 不覆盖用户传入的已有 Cookie；唯一例外是 __ac_signature：nonce 本次被换新时，旧签名必然失配，必须重算；
    - 获取 Cookie 和请求视频页/API 应尽量使用同一代理和 UA；
    - 所有网络请求都通过 yt-dlp 的 ie 实例发起。
    """
    headers = headers or {}

    base_headers = {
        'Referer': DOUYIN_WEBPAGE_HOST,
        'User-Agent': user_agent,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        **headers,
    }

    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    # 记录进入时的 nonce：后续任何一次首页请求都可能下发新 nonce，据此判断签名是否需要重算
    initial_ac_nonce = cookies.get('__ac_nonce')
    initial_ac_nonce = initial_ac_nonce and initial_ac_nonce.value

    if not cookies.get('ttwid'):
        register_douyin_ttwid(ie, video_id, user_agent)

    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    if not cookies.get('ttwid'):
        fetch_douyin_home_cookies(
            ie,
            video_id,
            base_headers,
            note='Fetching Douyin home cookies for ttwid')

    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    if not cookies.get('s_v_web_id'):
        ie._set_cookie('.douyin.com', 's_v_web_id', generate_s_v_web_id())

    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    if not cookies.get('msToken'):
        ie._set_cookie('.douyin.com', 'msToken', generate_ms_token())

    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    if not cookies.get('__ac_nonce'):
        fetch_douyin_home_cookies(
            ie,
            video_id,
            base_headers,
            note='Fetching Douyin __ac_nonce')

    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    ac_nonce = cookies.get('__ac_nonce')
    ac_signature = cookies.get('__ac_signature')

    # __ac_signature 与 __ac_nonce 一一绑定，失配时服务端返回「验证码中间页」。
    # nonce 只有 30 分钟有效期（Max-Age=1800），签名却会长期残留（本模块写入的不过期，浏览器写入的为 1 年），
    # 因此 --cookies-from-browser、跨次复用的 --cookies 文件、运行超过 30 分钟的批量任务，
    # 都会出现「新 nonce + 旧签名」。只要 nonce 在本次调用中变了，就丢弃旧签名重算。
    if ac_nonce and (not ac_signature or ac_nonce.value != initial_ac_nonce):
        try:
            signature = get_ac_signature(
                'www.douyin.com',
                ac_nonce.value,
                user_agent)
            if signature:
                clear_douyin_cookie(ie, '__ac_signature')
                ie._set_cookie('.douyin.com', '__ac_signature', signature)
        except Exception as e:
            ie.write_debug(f'Failed to generate Douyin __ac_signature: {e}')

    ie.write_debug(
        f'Douyin cookies prepared: {cookie_state_debug(ie._get_cookies(DOUYIN_WEBPAGE_HOST))}')
