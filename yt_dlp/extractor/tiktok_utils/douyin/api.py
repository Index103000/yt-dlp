# yt_dlp/extractor/tiktok_utils/douyin/api.py
from __future__ import annotations

import urllib.parse

from .abogus import generate_abogus

AWEME_DETAIL_API_URL = 'https://www.douyin.com/aweme/v1/web/aweme/detail/'
PLAY_API_URL = 'https://www.iesdouyin.com/aweme/v1/play/'


def build_original_play_url(uri):
    """
    构造上传原片地址：/aweme/v1/play/ 的 ratio=default 返回未转码的上传文件。

    - uri 即 aweme_detail.video.play_addr.uri / RENDER_DATA videoDetail.video.uri；
    - 其他 ratio（1080p / 1440p / 2k / 4k）实测都落到同一个 1080p 转码档，只有 default 是原片；
    - 参数与 media-parser 的 _build_play_endpoint_url 一致（实测可用）。
    """
    return f'{PLAY_API_URL}?' + urllib.parse.urlencode({
        'video_id': uri,
        'ratio': 'default',
        'line': '0',
        'is_play_url': '1',
        'watermark': '0',
        'source': 'PackSourceEnum_PUBLISH',
    })


def build_open_aweme_detail_query(video_id):
    """
    构造以 open.douyin.com 为来源的 aweme/detail 参数。

    配合 DOUYIN_OPEN_API_HEADERS 使用时只需要这两个参数，不需要 a_bogus / Cookie。
    """
    return {
        'aweme_id': video_id,
        'aid': '6383',
    }


def build_aweme_detail_query(video_id):
    """
    构造 Douyin aweme/detail Web API 参数。

    说明：
    - 参数集中放在这里，后续 Douyin Web 参数变化时只改这个文件；
    - 不在 DouyinIE 中散落大量 query 字段。
    """
    return {
        'device_platform': 'webapp',
        'aid': '6383',
        'channel': 'channel_pc_web',
        'pc_client_type': '1',
        'version_code': '290100',
        'version_name': '29.1.0',
        'cookie_enabled': 'true',
        'screen_width': '1920',
        'screen_height': '1080',
        'browser_language': 'en-US',
        'browser_platform': 'Win32',
        'browser_name': 'Edge',
        'browser_version': '130.0.0.0',
        'browser_online': 'true',
        'engine_name': 'Blink',
        'engine_version': '130.0.0.0',
        'os_name': 'Windows',
        'os_version': '10',
        'cpu_core_num': '12',
        'device_memory': '8',
        'platform': 'PC',
        'downlink': '10',
        'effective_type': '4g',
        'round_trip_time': '0',
        'aweme_id': video_id,
    }


def response_snippet(text, limit=200):
    """
    把响应体压成一行并截断，用于失败原因日志。
    """
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else f'{text[:limit]}...'


def sign_aweme_detail_query(query, user_agent):
    """
    为 aweme/detail query 添加 a_bogus。

    注意：
    - 签名字符串使用当前 dict 的插入顺序；
    - 因此 build_aweme_detail_query 不要随意重排字段；
    - 后续如果签名规则变化，只改这里或 abogus.py。
    """
    signed_query = query.copy()
    query_string = '&'.join(f'{k}={v}' for k, v in signed_query.items())
    signed_query['a_bogus'] = generate_abogus(query_string, user_agent)
    return signed_query
