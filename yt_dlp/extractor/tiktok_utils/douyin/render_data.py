# yt_dlp/extractor/tiktok/douyin/render_data.py
from __future__ import annotations

import urllib.parse


def make_jingxuan_url(video_id):
    """
    构造 Douyin 精选页 modal_id SSR URL。

    该页面通常可以通过 RENDER_DATA 返回 videoDetail。
    """
    return f'https://www.douyin.com/jingxuan?modal_id={video_id}'


def extract_render_data_json(ie, webpage, video_id):
    """
    从 Douyin HTML 中提取并解析 RENDER_DATA。

    这里接收 ie 实例，是为了复用 yt-dlp 的：
    - _search_regex
    - _parse_json
    - warning/error 行为
    """
    render_data_str = ie._search_regex(
        r'<script[^>]+id="RENDER_DATA"[^>]*>([^<]+)</script>',
        webpage,
        'render data',
        default=None)

    if not render_data_str:
        return None

    return ie._parse_json(
        urllib.parse.unquote(render_data_str),
        video_id,
        fatal=False)


def extract_video_detail(render_data):
    """
    从 RENDER_DATA JSON 中提取 videoDetail。
    """
    if not isinstance(render_data, dict):
        return None

    app_data = render_data.get('app') or {}
    video_detail = app_data.get('videoDetail')

    return video_detail if isinstance(video_detail, dict) else None
