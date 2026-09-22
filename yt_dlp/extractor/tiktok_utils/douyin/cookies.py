# yt_dlp/extractor/tiktok_utils/douyin/cookies.py
from __future__ import annotations

import json
import time

from .ac_signature import ac_signature_matches_nonce, get_ac_signature
from .constants import DOUYIN_AC_SIGNATURE_SITE, DOUYIN_DEFAULT_WEB_HEADERS, DOUYIN_WEBPAGE_HOST
from .tokens import generate_ms_token, generate_s_v_web_id

# signed_web_api 签名用的 uifid 在 yt-dlp 缓存里的位置（--no-cache-dir 时不读不写）
UIFID_CACHE_SECTION = 'douyin'
UIFID_CACHE_KEY = 'uifid'


def get_douyin_uifid(ie, exclude=()):
    """
    取 signed_web_api 签名（x-secsdk-web-signature）用的 uifid，返回 (uifid, 来源)；都没有时返回 (None, None)。

    有效的 uifid 只有两种：浏览器页面 JS 算出的 UIFID（--cookies-from-browser 会带来），
    以及服务端在真实页面响应里下发的 UIFID_TEMP。后者不绑定 IP / UA / ttwid / 视频，实测至少 24 小时可复用，
    所以本次运行取得后也存进 yt-dlp 缓存，供下次运行使用。exclude 里是本次已被服务端判为无效的值。
    """
    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    for name in ('UIFID', 'UIFID_TEMP'):
        cookie = cookies.get(name)
        if cookie and cookie.value and cookie.value not in exclude:
            return cookie.value, f'cookie {name}'

    cached = ie.cache.load(UIFID_CACHE_SECTION, UIFID_CACHE_KEY)
    value = cached.get('value') if isinstance(cached, dict) else None
    if isinstance(value, str) and value and value not in exclude:
        return value, 'cache'
    return None, None


def store_douyin_uifid(ie, uifid):
    ie.cache.store(UIFID_CACHE_SECTION, UIFID_CACHE_KEY, {'value': uifid, 'fetched_at': int(time.time())})


def forget_douyin_uifid(ie, uifid):
    """
    UIFID_TEMP / 缓存里的 uifid 被判无效（Validate Error）时调用：清掉 cookiejar 里的 UIFID_TEMP 与缓存里的同一个值。

    必须清掉 cookiejar 里的：请求只要带着 UIFID_TEMP 或 UIFID（哪怕是无效值），服务端就不会下发新的 UIFID_TEMP。
    浏览器带来的 UIFID 不在这里删，由调用方通过 exclude 跳过，要取新值时再清。
    """
    clear_douyin_cookie(ie, 'UIFID_TEMP')
    cached = ie.cache.load(UIFID_CACHE_SECTION, UIFID_CACHE_KEY)
    if isinstance(cached, dict) and cached.get('value') == uifid:
        ie.cache.store(UIFID_CACHE_SECTION, UIFID_CACHE_KEY, {})


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


def fetch_douyin_home_cookies(ie, video_id, user_agent, purpose):
    """
    请求 Douyin 首页，让服务端下发基础 Cookie。

    主要用于：
    - __ac_nonce
    - ttwid fallback
    - 其他服务端 Set-Cookie

    请求失败时打印 warning（不中断），否则后续只会看到「拿到 JS 挑战页」之类的表象。返回请求是否成功。
    """
    return ie._download_webpage(
        DOUYIN_WEBPAGE_HOST,
        video_id,
        note=f'Fetching Douyin home page for {purpose}',
        errnote=f'Unable to fetch Douyin home page for {purpose}',
        fatal=False,
        headers={
            **DOUYIN_DEFAULT_WEB_HEADERS,
            'User-Agent': user_agent,
        }) is not False


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
    准备 Douyin Web 匿名访客 Cookie，signed_web_api 只需要这些（ssr_render_data 另需 __ac Cookie）。

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
        fetch_douyin_home_cookies(ie, video_id, user_agent, 'ttwid')

    cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    if not cookies.get('ttwid'):
        ie.report_warning('Unable to obtain Douyin ttwid cookie; the aweme/detail web API will likely return an empty response', video_id)
    if not cookies.get('s_v_web_id'):
        ie._set_cookie('.douyin.com', 's_v_web_id', generate_s_v_web_id())
    if not cookies.get('msToken'):
        ie._set_cookie('.douyin.com', 'msToken', generate_ms_token())

    ie.write_debug(
        f'Douyin visitor cookies prepared: {cookie_state_debug(ie._get_cookies(DOUYIN_WEBPAGE_HOST))}')


def ensure_douyin_ac_cookies(ie, video_id, user_agent):
    """
    准备 ssr_render_data（精选页 SSR RENDER_DATA）额外需要的 __ac_nonce / __ac_signature。

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
        home_fetched = fetch_douyin_home_cookies(ie, video_id, user_agent, '__ac_nonce')
        cookies = ie._get_cookies(DOUYIN_WEBPAGE_HOST)
    else:
        home_fetched = False

    ac_nonce = cookies.get('__ac_nonce')
    ac_signature = cookies.get('__ac_signature')

    if not ac_nonce:
        # 首页请求本身失败时 fetch_douyin_home_cookies 已经打印过原因，这里只报「请求成功却没下发」
        if home_fetched:
            ie.report_warning(
                'Douyin home page did not issue __ac_nonce; the jingxuan page request will likely get the anti-bot challenge page',
                video_id)
    elif not (ac_signature and ac_signature_matches_nonce(ac_signature.value, ac_nonce.value)):
        clear_douyin_cookie(ie, '__ac_signature')
        ie._set_cookie(
            '.douyin.com', '__ac_signature',
            get_ac_signature(DOUYIN_AC_SIGNATURE_SITE, ac_nonce.value, user_agent))

    ie.write_debug(
        f'Douyin __ac cookies prepared: {cookie_state_debug(ie._get_cookies(DOUYIN_WEBPAGE_HOST))}')
