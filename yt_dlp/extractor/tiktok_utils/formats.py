# yt_dlp/extractor/tiktok/formats.py
from __future__ import annotations

import re

from ...utils import (
    ExtractorError,
    filter_dict,
    int_or_none,
)


def normalize_bytedance_vcodec(codec):
    """
    标准化字节系视频编码字段。

    适用范围：
    - TikTok App API
    - TikTok Web hydration
    - Douyin Web API
    - Douyin SSR RENDER_DATA

    常见来源：
    - UrlKey: h264 / bytevc1 / bytevc2
    - CodecType: h264 / h265_hvc1
    - bitRateList: isH265 / codecType
    """
    if not codec:
        return None

    codec = str(codec).lower()

    if codec in ('h264', 'avc', 'avc1'):
        return 'h264'

    if codec in ('bytevc1', 'h265', 'h265_hvc1', 'hevc', 'hvc1'):
        return 'h265'

    # ByteDance 自有 H.266/VVC 相关格式，当前通常不可播放。
    if codec in ('bytevc2', 'h266', 'vvc'):
        return 'bytevc2'

    return codec


def normalize_bytedance_url_key_res(res):
    """
    归一化 UrlKey 中的清晰度档位。

    示例：
        360p   -> 360p
        540p   -> 540p
        576p   -> 576p
        1080p  -> 1080p
        2k     -> 1440p
        4k     -> 2160p
        8k     -> 4320p

    注意：
    - 这里只用于 format 展示和排序；
    - 不代表真实 width / height；
    - 真实 width / height 应优先取结构化字段。
    """
    if not res:
        return None

    res = str(res).lower()

    return {
        '2k': '1440p',
        '4k': '2160p',
        '8k': '4320p',
    }.get(res, res)


def quality_from_bytedance_url_key_res(res):
    """
    将 UrlKey 清晰度档位转换成可排序 quality。

    相比 qualities(('360p', '540p', '720p', '1080p'))，这里更通用：
    - 支持 576p；
    - 支持 1440p / 2160p；
    - 支持后续可能出现的 900p / 1200p 等。
    """
    normalized_res = normalize_bytedance_url_key_res(res)
    if not normalized_res:
        return -1

    mobj = re.match(r'(?i)^(\d+)p$', normalized_res)
    if not mobj:
        return -1

    return int_or_none(mobj.group(1)) or -1


def parse_bytedance_url_key(url_key):
    """
    解析 TikTok / Douyin UrlKey。

    UrlKey 示例：
        v090446c0000bdimjn89pog8ra75btp0_h264_720p_1258633
        v15044gf0000d61nue7og65sn6pteb4g_bytevc1_1080p_1511797
        v0200fg10000d74shpnog65o0572ntqg_bytevc1_4k_12564703

    返回：
        ({format metadata}, normalized_res)

    metadata 可能包含：
        format_id
        vcodec
        tbr
        quality

    重要：
    - 不在这里推导 width / height；
    - width / height 由调用方从 PlayAddr / bitRateList 等结构化字段读取；
    - 只有调用方明确允许 fallback 时，才可以用 res 推算宽高。
    """
    if not url_key:
        return {}, None

    mobj = re.search(
        r'v[^_]+_(?P<id>(?P<codec>[^_]+)_(?P<res>\d+(?:p|k))_(?P<bitrate>\d+))',
        str(url_key))

    if not mobj:
        return {}, None

    res = mobj.group('res')
    normalized_res = normalize_bytedance_url_key_res(res)

    return filter_dict({
        'format_id': mobj.group('id'),
        'vcodec': normalize_bytedance_vcodec(mobj.group('codec')),
        'tbr': int_or_none(mobj.group('bitrate'), scale=1000) or None,
        'quality': quality_from_bytedance_url_key_res(res),
    }), normalized_res


def get_bytedance_addr_url_key(addr):
    """
    从不同字节系地址结构中读取 UrlKey。

    常见字段：
        UrlKey
        url_key
        urlKey
    """
    if not isinstance(addr, dict):
        return None

    return addr.get('UrlKey') or addr.get('url_key') or addr.get('urlKey')


def get_bytedance_addr_width_height(addr):
    """
    从地址结构中读取真实 width / height。

    注意：
    - 不从 UrlKey 推算；
    - 不 fallback 到顶层 video.width / video.height；
    - 顶层宽高只能由具体调用方按上下文决定是否使用。
    """
    if not isinstance(addr, dict):
        return None, None

    width = int_or_none(addr.get('Width') or addr.get('width'))
    height = int_or_none(addr.get('Height') or addr.get('height'))

    if width and height:
        return width, height

    return None, None


def get_bytedance_addr_filesize(addr):
    """
    从地址结构中读取文件大小。

    常见字段：
        DataSize
        data_size
        dataSize
    """
    if not isinstance(addr, dict):
        return None

    return int_or_none(
        addr.get('DataSize')
        or addr.get('data_size')
        or addr.get('dataSize'))


def infer_bytedance_dimension_from_res(res, ratio):
    """
    根据 UrlKey 清晰度档位兜底推算宽高。

    这是最后兜底逻辑，不应优先使用。

    原因：
    - TikTok / Douyin 的 720p / 540p 不一定代表真实视频高度；
    - 有些 720p 档实际是 576x1024；
    - 有些 540p 档实际也是 576x1024；
    - 所以只有结构化宽高缺失时才允许使用。
    """
    dimension = int_or_none(res[:-1]) if res else None
    if not dimension:
        return None, None

    # 字节系常见情况：540p 档实际更接近 576 宽度档。
    if dimension == 540:
        dimension = 576

    if ratio and ratio < 1:
        # 竖屏：把 dimension 作为宽度档位兜底估算。
        height = int(dimension / ratio)
        return dimension, height - (height % 2)

    if ratio:
        # 横屏：把 dimension 作为高度档位兜底估算。
        width = int(dimension * ratio)
        return width + (width % 2), dimension

    return None, None


def build_bytedance_addr_meta(addr, *, ratio=None, allow_res_fallback=False):
    """
    从字节系地址结构中提取通用 format metadata。

    适用结构：
    - TikTok PlayAddr / PlayAddrStruct / DownloadAddrStruct
    - TikTok App API play_addr / download_addr
    - Douyin Web API play_addr / download_addr
    - Douyin SSR 中可能带 UrlKey 的 playAddr 结构

    规则：
    - UrlKey 只补 format_id / vcodec / tbr / quality；
    - width / height 优先结构化字段；
    - 只有 allow_res_fallback=True 时，才允许从 UrlKey 推算宽高。
    """
    if not isinstance(addr, dict):
        return {}

    parsed_meta, res = parse_bytedance_url_key(get_bytedance_addr_url_key(addr))

    width, height = get_bytedance_addr_width_height(addr)

    if (not width or not height) and allow_res_fallback and ratio:
        width, height = infer_bytedance_dimension_from_res(res, ratio)

    return filter_dict({
        **parsed_meta,
        'filesize': get_bytedance_addr_filesize(addr),
        'width': width,
        'height': height,
    })


def parse_bytedance_video_extra(video_extra, parse_json_func=None):
    """
    解析 ByteDance VideoExtra 字段。

    VideoExtra 可能是：
    - dict
    - JSON 字符串
    - 空值或未知类型

    目前主要关注：
    - audio_bit_rate：通常是 bps，需要转换为 Kbps。
    """
    if not video_extra:
        return {}

    if isinstance(video_extra, dict):
        return video_extra

    if not isinstance(video_extra, str) or not parse_json_func:
        return {}

    try:
        return parse_json_func(video_extra, None, fatal=False) or {}
    except ExtractorError:
        return {}


def build_tiktok_bitrate_meta(bitrate_info, *, ratio=None, parse_json_func=None):
    """
    从 TikTok Web hydration 的 video.bitrateInfo[] 构造 format metadata。

    这类结构通常包含：
    - PlayAddr.UrlKey
    - PlayAddr.Width / Height
    - PlayAddr.DataSize
    - Bitrate
    - BitrateFPS
    - CodecType
    - GearName
    - VideoExtra
    """
    if not isinstance(bitrate_info, dict):
        return {}

    play_addr = bitrate_info.get('PlayAddr') or {}

    meta = build_bytedance_addr_meta(
        play_addr,
        ratio=ratio,
        allow_res_fallback=True)

    video_extra = parse_bytedance_video_extra(
        bitrate_info.get('VideoExtra'),
        parse_json_func=parse_json_func)

    vcodec = normalize_bytedance_vcodec(
        bitrate_info.get('CodecType')
        or bitrate_info.get('codec_type')
        or meta.get('vcodec'))

    tbr = (
        int_or_none(bitrate_info.get('Bitrate') or bitrate_info.get('bit_rate'), scale=1000)
        or meta.get('tbr'))

    fps = int_or_none(
        bitrate_info.get('BitrateFPS')
        or bitrate_info.get('FPS')
        or bitrate_info.get('fps'))

    abr = int_or_none(video_extra.get('audio_bit_rate'), scale=1000)

    format_id = (
        bitrate_info.get('GearName')
        or bitrate_info.get('gear_name')
        or meta.get('format_id'))

    meta.update(filter_dict({
        'format_id': format_id,
        'tbr': tbr,
        'fps': fps,
        'vcodec': vcodec,
        'acodec': 'aac',
        'abr': abr,
        'format_note': bitrate_info.get('GearName') or bitrate_info.get('gear_name'),
    }))

    return filter_dict(meta)


def build_douyin_web_bitrate_meta(bitrate_info):
    """
    从 Douyin SSR RENDER_DATA 的 video.bitRateList[] 构造 format metadata。

    常见结构：
        bitRateList[].gearName
        bitRateList[].bitRate
        bitRateList[].width
        bitRateList[].height
        bitRateList[].dataSize
        bitRateList[].isH265
        bitRateList[].fps
        bitRateList[].playAddr

    如果结构中存在 UrlKey，也会统一复用增强版 UrlKey 解析。
    """
    if not isinstance(bitrate_info, dict):
        return {}

    play_addr = bitrate_info.get('playAddr') or {}
    addr_meta = build_bytedance_addr_meta(play_addr, allow_res_fallback=False)

    top_level_url_key_meta, _ = parse_bytedance_url_key(
        bitrate_info.get('urlKey')
        or bitrate_info.get('UrlKey')
        or bitrate_info.get('url_key'))

    meta = {
        **top_level_url_key_meta,
        **addr_meta,
    }

    structured_width = int_or_none(bitrate_info.get('width'))
    structured_height = int_or_none(bitrate_info.get('height'))
    structured_tbr = int_or_none(bitrate_info.get('bitRate'), scale=1000)
    structured_filesize = int_or_none(bitrate_info.get('dataSize'))

    meta.update(filter_dict({
        # 结构化字段优先，不被 UrlKey 推断值覆盖。
        'width': structured_width or meta.get('width'),
        'height': structured_height or meta.get('height'),
        'tbr': structured_tbr or meta.get('tbr'),
        'filesize': structured_filesize or meta.get('filesize'),
        'vcodec': (
            normalize_bytedance_vcodec(bitrate_info.get('codecType'))
            or ('h265' if bitrate_info.get('isH265') else None)
            or meta.get('vcodec')),
        'acodec': 'aac',
        'format_id': (
            bitrate_info.get('gearName')
            or bitrate_info.get('format_id')
            or meta.get('format_id')),
        'format_note': bitrate_info.get('gearName'),
        'fps': int_or_none(bitrate_info.get('fps')),
    }))

    return filter_dict(meta)
