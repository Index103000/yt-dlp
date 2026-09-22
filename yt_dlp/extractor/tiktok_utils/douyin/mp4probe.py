# yt_dlp/extractor/tiktok_utils/douyin/mp4probe.py
"""
MP4 / QuickTime 的最小解析：不下载整个文件，读出上传原片的容器、编码、分辨率、帧率与码率。

上传原片（/aweme/v1/play/?ratio=default）实测都是 ftyp + wide/free + mdat 在前、moov 在文件尾，
所以先取文件头几 KB 得到 brand 与 mdat 范围，再用一次 Range 取 mdat 之后的 moov 解析。
"""
from __future__ import annotations

import struct

_CONTAINER_BOXES = {b'moov', b'trak', b'mdia', b'minf', b'stbl', b'edts'}

VCODECS = {'avc1': 'h264', 'avc3': 'h264', 'hvc1': 'h265', 'hev1': 'h265', 'av01': 'av01', 'vp09': 'vp9'}
ACODECS = {'mp4a': 'aac', 'ac-3': 'ac3', 'ec-3': 'eac3', 'Opus': 'opus', 'fLaC': 'flac', 'alac': 'alac'}

# colr(nclx / nclc) 的 transfer_characteristics：16 为 PQ（HDR10），18 为 HLG，其余按 SDR
_TRANSFER_DYNAMIC_RANGE = {16: 'HDR10', 18: 'HLG'}


def iter_boxes(data, start=0, end=None):
    """
    逐个产出 (类型, box 起点, 内容起点, box 终点)。

    box 终点可能超出 data（只取了文件头时的 mdat 就是这样），由调用方判断；扫描范围不超出 data，头部残缺或大小非法时停止。
    """
    end = len(data) if end is None else min(end, len(data))
    offset = start
    while offset + 8 <= end:
        size, box_type = struct.unpack('>I4s', data[offset:offset + 8])
        header = 8
        if size == 1:
            if offset + 16 > end:
                return
            size = struct.unpack('>Q', data[offset + 8:offset + 16])[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header:
            return
        yield box_type, offset, offset + header, offset + size
        offset += size


def parse_top_level(head):
    """
    解析文件头里的顶层 box，返回 (major_brand, [(类型, 起点, 终点), ...])；major_brand 取不到时为 None。
    """
    brand = None
    boxes = []
    for box_type, box_start, content_start, box_end in iter_boxes(head):
        if box_type == b'ftyp' and content_start + 4 <= len(head):
            brand = head[content_start:content_start + 4].decode('latin-1')
        boxes.append((box_type, box_start, box_end))
    return brand, boxes


def _parse_trak(data, start, end):
    track = {}

    def walk(walk_start, walk_end):
        for box_type, _, content, box_end in iter_boxes(data, walk_start, walk_end):
            if box_type in _CONTAINER_BOXES:
                walk(content, box_end)
            elif box_type == b'tkhd':
                version = data[content]
                # version/flags(4) + 时间与 track_id 等（v0 20 字节，v1 32 字节）+ reserved/layer/group/volume(16) 之后是矩阵
                matrix_offset = content + 4 + (20 if version == 0 else 32) + 16
                matrix = struct.unpack('>9i', data[matrix_offset:matrix_offset + 36])
                width, height = struct.unpack('>II', data[matrix_offset + 36:matrix_offset + 44])
                # 矩阵 a、d 都为 0 时是 90 / 270 度旋转，显示宽高互换
                rotated = matrix[0] == 0 and matrix[4] == 0
                track['display'] = (height >> 16, width >> 16) if rotated else (width >> 16, height >> 16)
            elif box_type == b'mdhd':
                if data[content] == 0:
                    track['timescale'], track['duration'] = struct.unpack('>II', data[content + 12:content + 20])
                else:
                    track['timescale'], track['duration'] = struct.unpack('>IQ', data[content + 20:content + 32])
            elif box_type == b'hdlr':
                # QuickTime 的 minf 里还有一个数据引用 hdlr（alis），只取 mdia 里先出现的那个
                track.setdefault('handler', data[content + 8:content + 12].decode('latin-1'))
            elif box_type == b'stsd':
                entry_start = content + 8
                entry_size = struct.unpack('>I', data[entry_start:entry_start + 4])[0]
                track['fourcc'] = data[entry_start + 4:entry_start + 8].decode('latin-1')
                if track.get('handler') == 'vide':
                    # VisualSampleEntry 固定字段共 78 字节，其后是 avcC / hvcC / colr 等子 box
                    for child_type, _, child_content, _ in iter_boxes(
                            data, entry_start + 8 + 78, entry_start + entry_size):
                        if child_type == b'colr' and data[child_content:child_content + 4] in (b'nclx', b'nclc'):
                            track['transfer'] = struct.unpack('>H', data[child_content + 6:child_content + 8])[0]
            elif box_type == b'stsz':
                sample_size, sample_count = struct.unpack('>II', data[content + 4:content + 12])
                track['samples'] = sample_count
                track['bytes'] = sample_size * sample_count if sample_size else sum(
                    struct.unpack(f'>{sample_count}I', data[content + 12:content + 12 + 4 * sample_count]))

    walk(start, end)
    return track


def parse_moov(data):
    """
    解析以 moov box 开头（或包含完整 moov 顶层 box）的数据，返回可直接并入 yt-dlp format 的字段：
    vcodec、acodec、width、height、fps、vbr、abr（kbps）、dynamic_range。
    """
    info = {}
    moov_complete = False
    for box_type, _, content, box_end in iter_boxes(data):
        if box_type != b'moov':
            continue
        moov_complete = box_end <= len(data)
        for trak_type, _, trak_content, trak_end in iter_boxes(data, content, box_end):
            if trak_type != b'trak':
                continue
            track = _parse_trak(data, trak_content, trak_end)
            seconds = track['duration'] / track['timescale'] if track.get('timescale') else 0
            if track.get('handler') == 'vide' and 'vcodec' not in info:
                width, height = track.get('display') or (None, None)
                info.update({
                    'vcodec': VCODECS.get(track.get('fourcc'), track.get('fourcc')),
                    'width': width or None,
                    'height': height or None,
                    'fps': round(track['samples'] / seconds, 3) if seconds and track.get('samples') else None,
                    'vbr': round(track['bytes'] * 8 / seconds / 1000) if seconds and track.get('bytes') else None,
                    # 只有 colr 给出 transfer 时才下结论；HDR 只写在码流 VUI 里、没有 colr 的文件无法判断
                    'dynamic_range': None if track.get('transfer') is None
                    else _TRANSFER_DYNAMIC_RANGE.get(track['transfer'], 'SDR'),
                })
            elif track.get('handler') == 'soun' and 'acodec' not in info:
                info.update({
                    'acodec': ACODECS.get(track.get('fourcc'), track.get('fourcc')),
                    'abr': round(track['bytes'] * 8 / seconds / 1000) if seconds and track.get('bytes') else None,
                })
    # moov 完整却没有音轨才是真的没有音频；数据被截断时音轨可能只是没读到
    if moov_complete and 'vcodec' in info and 'acodec' not in info:
        info['acodec'] = 'none'
    return {k: v for k, v in info.items() if v is not None}
