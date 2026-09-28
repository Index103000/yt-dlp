# yt_dlp/extractor/tiktok_utils/douyin/mediaprobe.py
"""
上传原片的容器识别与 Matroska / WebM 头部解析，以及 ffprobe 输出到 yt-dlp format 字段的换算。

抖音上传原片的容器随上传文件而定：多为 MP4 / QuickTime（交给 mp4probe），也有从 YouTube 等处下载后直接上传的
WebM（VP9 / AV1 + Opus）。WebM / MKV 的编码、分辨率、帧率都在文件头的 Tracks 里（实测 Lavf 封装的在前 500 字节内），
所以读文件头的那一次 Range 请求就够；Tracks 不在头部或被文件头截断时，给出它的偏移由调用方再取一次。
其他容器（FLV、AVI、TS 等）由调用方交给 ffprobe。

原片字节由上传者控制，这里的解析只读不信：越界、截断、超长字段都按「读不到」处理，不抛异常。
"""
from __future__ import annotations

from .mp4probe import parse_top_level

EBML_MAGIC = b'\x1a\x45\xdf\xa3'

# Matroska 元素 ID（保留长度标记位）
_ID_EBML = 0x1A45DFA3
_ID_DOCTYPE = 0x4282
_ID_SEGMENT = 0x18538067
_ID_SEEKHEAD = 0x114D9B74
_ID_SEEK = 0x4DBB
_ID_SEEKID = 0x53AB
_ID_SEEKPOSITION = 0x53AC
_ID_TRACKS = 0x1654AE6B
_ID_TRACKENTRY = 0xAE
_ID_TRACKTYPE = 0x83
_ID_CODECID = 0x86
_ID_DEFAULTDURATION = 0x23E383
_ID_VIDEO = 0xE0
_ID_PIXELWIDTH = 0xB0
_ID_PIXELHEIGHT = 0xBA
_ID_COLOUR = 0x55B0
_ID_TRANSFER = 0x55BA
_ID_CLUSTER = 0x1F43B675

# 只收常见编码；认不出的 CodecID（如 V_MS/VFW/FOURCC）不填 vcodec，调用方会交给 ffprobe
MATROSKA_VCODECS = {
    'V_VP9': 'vp9', 'V_VP8': 'vp8', 'V_AV1': 'av01',
    'V_MPEG4/ISO/AVC': 'h264', 'V_MPEGH/ISO/HEVC': 'h265', 'V_MPEG4/ISO/ASP': 'mp4v',
}
MATROSKA_ACODECS = {
    'A_OPUS': 'opus', 'A_VORBIS': 'vorbis', 'A_FLAC': 'flac', 'A_AC3': 'ac3', 'A_EAC3': 'eac3', 'A_MPEG/L3': 'mp3',
}

# transfer_characteristics（H.273）：16 为 PQ（HDR10），18 为 HLG，其余按 SDR；与 mp4probe 一致
_TRANSFER_DYNAMIC_RANGE = {16: 'HDR10', 18: 'HLG'}
FFPROBE_VCODECS = {'hevc': 'h265', 'h264': 'h264', 'av1': 'av01', 'vp9': 'vp9', 'vp8': 'vp8', 'mpeg4': 'mp4v'}
FFPROBE_TRANSFERS = {'smpte2084': 'HDR10', 'arib-std-b67': 'HLG'}
# ffprobe 的 format_name → 扩展名；mov,mp4,... 与 matroska,webm 另行按品牌 / 编码细分
FFPROBE_EXTS = {'flv': 'flv', 'avi': 'avi', 'mpegts': 'ts', 'asf': 'wmv', 'mpeg': 'mpg', 'ogg': 'ogv'}

# 没有 ftyp 的老式 QuickTime 以这些顶层 box 开头；只认白名单，免得 FLV / TS / AVI 的字节被当成 box
_QT_LEADING_BOXES = {b'moov', b'mdat', b'wide', b'free', b'skip', b'pnot'}


def sniff_container(head):
    """
    按文件头识别容器，返回 'mp4' / 'mov' / 'webm' / 'mkv'，认不出时返回 None。
    """
    if len(head) >= 12 and head[4:8] == b'ftyp':
        return 'mov' if head[8:12].startswith(b'qt') else 'mp4'
    if head[:4] == EBML_MAGIC:
        return 'webm' if parse_matroska(head).get('doctype') == 'webm' else 'mkv'
    brand, boxes = parse_top_level(head)
    if boxes and boxes[0][0] in _QT_LEADING_BOXES:
        return 'mp4' if brand and not brand.startswith('qt') else 'mov'
    return None


def _read_vint(data, pos, keep_marker):
    """EBML 变长整数，返回 (值, 长度)；越界或非法时抛 ValueError。"""
    if pos >= len(data):
        raise ValueError('truncated EBML data')
    first = data[pos]
    length = 1
    mask = 0x80
    while length <= 8 and not first & mask:
        mask >>= 1
        length += 1
    if length > 8 or pos + length > len(data):
        raise ValueError('invalid EBML vint')
    value = first if keep_marker else first & (mask - 1)
    for byte in data[pos + 1:pos + length]:
        value = value << 8 | byte
    return value, length


def _iter_elements(data, start, end):
    """
    逐个产出 (ID, 元素起点, 内容起点, 内容终点, 是否完整)。
    大小未知时内容终点取 data 末尾并停止；内容终点超出 data（只取了文件头时）或大小未知都算不完整。
    扫描不超出 data，头部残缺时停止。
    """
    pos = start
    end = min(end, len(data))
    while pos < end:
        try:
            element_id, id_length = _read_vint(data, pos, keep_marker=True)
            size, size_length = _read_vint(data, pos + id_length, keep_marker=False)
        except ValueError:
            return
        content = pos + id_length + size_length
        if size == (1 << (7 * size_length)) - 1:
            yield element_id, pos, content, len(data), False
            return
        yield element_id, pos, content, content + size, content + size <= len(data)
        pos = content + size


def _uint(data, start, end):
    # Matroska 的无符号整数最长 8 字节；更长的是畸形数据，按读不到处理，免得后续换算溢出
    return int.from_bytes(data[start:end], 'big') if end - start <= 8 else None


def _complete_children(data, start, end):
    """只产出完整的子元素：被截断的元素不读它的值，免得把残缺字节当成宽高 / 编码。"""
    for element_id, _, content, element_end, complete in _iter_elements(data, start, end):
        if complete:
            yield element_id, content, element_end


def _parse_track(data, start, end):
    track = {}
    for element_id, content, element_end in _complete_children(data, start, end):
        if element_id == _ID_TRACKTYPE:
            track['type'] = _uint(data, content, element_end)
        elif element_id == _ID_CODECID:
            track['codec'] = data[content:element_end].decode('latin-1').rstrip('\x00')
        elif element_id == _ID_DEFAULTDURATION:
            track['default_duration'] = _uint(data, content, element_end)
        elif element_id == _ID_VIDEO:
            for child_id, child_content, child_end in _complete_children(data, content, element_end):
                if child_id == _ID_PIXELWIDTH:
                    track['width'] = _uint(data, child_content, child_end)
                elif child_id == _ID_PIXELHEIGHT:
                    track['height'] = _uint(data, child_content, child_end)
                elif child_id == _ID_COLOUR:
                    for colour_id, colour_content, colour_end in _complete_children(data, child_content, child_end):
                        if colour_id == _ID_TRANSFER:
                            track['transfer'] = _uint(data, colour_content, colour_end)
    return track


def parse_matroska_tracks(data, start=0, end=None):
    """
    解析一个 Tracks 元素（data[start:] 以它开头），返回可直接并入 yt-dlp format 的字段：
    vcodec、acodec、width、height、fps、dynamic_range。Tracks 不完整时只返回完整的 TrackEntry 里读到的部分。
    """
    end = len(data) if end is None else end
    info = {}
    seen = set()
    complete = False
    for element_id, _, content, element_end, element_complete in _iter_elements(data, start, end):
        if element_id != _ID_TRACKS:
            break
        complete = element_complete
        for entry_id, entry_content, entry_end in _complete_children(data, content, element_end):
            if entry_id != _ID_TRACKENTRY:
                continue
            track = _parse_track(data, entry_content, entry_end)
            codec = track.get('codec') or ''
            if track.get('type') == 1 and 'video' not in seen:
                seen.add('video')
                default_duration = track.get('default_duration')
                info.update({
                    'vcodec': MATROSKA_VCODECS.get(codec),
                    'width': track.get('width'),
                    'height': track.get('height'),
                    'fps': round(1e9 / default_duration, 3) if default_duration else None,
                    'dynamic_range': None if track.get('transfer') is None
                    else _TRANSFER_DYNAMIC_RANGE.get(track['transfer'], 'SDR'),
                })
            elif track.get('type') == 2 and 'audio' not in seen:
                seen.add('audio')
                info['acodec'] = 'aac' if codec.startswith('A_AAC') else MATROSKA_ACODECS.get(codec)
        break
    # Tracks 完整、有已知编码的视频轨却没有音频轨，才是真的没有音频
    if complete and info.get('vcodec') and 'audio' not in seen:
        info['acodec'] = 'none'
    return {k: v for k, v in info.items() if v is not None}


def parse_matroska(data):
    """
    解析 Matroska / WebM 文件头（data 以 EBML 头开始），返回 dict：
    - doctype：'webm' / 'matroska'（取不到时缺省）；
    - 找到 Tracks 时并入 parse_matroska_tracks 的字段；
    - tracks_offset：Tracks 不在 data 内（取 SeekHead 给出的位置）或被 data 截断（取它的起点）时，
      为 Tracks 在文件中的绝对偏移，调用方应从这里再取一次完整的 Tracks。
    """
    result = {}
    for element_id, _, content, element_end, _ in _iter_elements(data, 0, len(data)):
        if element_id == _ID_EBML:
            for child_id, child_content, child_end in _complete_children(data, content, element_end):
                if child_id == _ID_DOCTYPE:
                    result['doctype'] = data[child_content:child_end].decode('latin-1').rstrip('\x00')
        elif element_id == _ID_SEGMENT:
            tracks_position = None
            for child_id, child_start, child_content, child_end, child_complete in _iter_elements(
                    data, content, element_end):
                if child_id == _ID_SEEKHEAD:
                    for seek_id, seek_content, seek_end in _complete_children(data, child_content, child_end):
                        if seek_id != _ID_SEEK:
                            continue
                        target, position = None, None
                        for field_id, field_content, field_end in _complete_children(data, seek_content, seek_end):
                            if field_id == _ID_SEEKID:
                                target = _uint(data, field_content, field_end)
                            elif field_id == _ID_SEEKPOSITION:
                                position = _uint(data, field_content, field_end)
                        if target == _ID_TRACKS and position is not None:
                            tracks_position = content + position
                elif child_id == _ID_TRACKS:
                    result.update(parse_matroska_tracks(data, child_start))
                    if not child_complete:
                        result['tracks_offset'] = child_start
                    return result
                elif child_id == _ID_CLUSTER:
                    break
            if tracks_position is not None:
                result['tracks_offset'] = tracks_position
            break
    return result


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def ffprobe_info(data):
    """
    把 ffprobe -show_format -show_streams 的 JSON 换算成 yt-dlp format 字段：
    ext、vcodec、acodec、width、height（按旋转换成显示宽高）、fps、vbr、abr（kbps）、dynamic_range。
    ffprobe 读的是上传者可控的文件，所有数值都按「可能畸形」处理。
    """
    streams = [s for s in data.get('streams') or [] if isinstance(s, dict)]
    video = next((s for s in streams if s.get('codec_type') == 'video'
                  and not (s.get('disposition') or {}).get('attached_pic')), None)
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    info = {}
    if video:
        width, height = _int_or_none(video.get('width')), _int_or_none(video.get('height'))
        rotation = (video.get('tags') or {}).get('rotate')
        for side_data in video.get('side_data_list') or []:
            if isinstance(side_data, dict) and 'rotation' in side_data:
                rotation = side_data['rotation']
        try:
            if width and height and int(float(rotation or 0)) % 180:
                width, height = height, width
        except (TypeError, ValueError, OverflowError):
            pass
        fps = None
        num, _, den = str(video.get('avg_frame_rate') or '').partition('/')
        num, den = _int_or_none(num), _int_or_none(den)
        if num and den:
            fps = round(num / den, 3) or None
        vbr = _int_or_none(video.get('bit_rate'))
        transfer = video.get('color_transfer')
        info.update({
            'vcodec': FFPROBE_VCODECS.get(video.get('codec_name'), video.get('codec_name')),
            'width': width,
            'height': height,
            'fps': fps,
            'vbr': round(vbr / 1000) if vbr else None,
            'dynamic_range': None if not transfer or transfer == 'unknown' else FFPROBE_TRANSFERS.get(transfer, 'SDR'),
        })
    if audio:
        abr = _int_or_none(audio.get('bit_rate'))
        info.update({
            'acodec': audio.get('codec_name'),
            'abr': round(abr / 1000) if abr else None,
        })
    elif video:
        info['acodec'] = 'none'

    fmt = data.get('format') or {}
    format_name = str(fmt.get('format_name') or '')
    if format_name.startswith('mov,mp4'):
        brand = str((fmt.get('tags') or {}).get('major_brand') or '')
        info['ext'] = 'mov' if brand.startswith('qt') else 'mp4'
    elif format_name.startswith('matroska'):
        webm_codecs = {'vp8', 'vp9', 'av01', 'opus', 'vorbis', 'none', None}
        info['ext'] = 'webm' if info.get('vcodec') in webm_codecs and info.get('acodec') in webm_codecs else 'mkv'
    else:
        info['ext'] = FFPROBE_EXTS.get(format_name.partition(',')[0])
    return {k: v for k, v in info.items() if v is not None}
