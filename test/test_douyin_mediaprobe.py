#!/usr/bin/env python3
"""抖音上传原片探测：容器识别、Matroska / MP4 解析、ffprobe 兜底。不联网，样本由 ffmpeg 生成（testsrc + sine，0.4 秒）。"""

import json
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yt_dlp import YoutubeDL
from yt_dlp.extractor.tiktok import DouyinIE
from yt_dlp.extractor.tiktok_utils.douyin.mediaprobe import (
    ffprobe_info,
    parse_matroska,
    parse_matroska_tracks,
    sniff_container,
)

TESTDATA = os.path.join(os.path.dirname(__file__), 'testdata', 'douyin')
CDN_URL = 'https://v3-cold.douyinvod.com/sig/expire/video/tos/cn/tos-cn-v-0000/oOriginalObject/'


def read_fixture(name):
    with open(os.path.join(TESTDATA, name), 'rb') as f:
        return f.read()


class FakeResponse:
    def __init__(self, blob, start, end):
        self.status = 206
        self.url = CDN_URL
        self.headers = {'Content-Range': f'bytes {start}-{end}/{len(blob)}'}
        self._data = blob[start:end + 1]

    def read(self, size=-1):
        return self._data if size < 0 else self._data[:size]

    def close(self):
        pass


class Logger:
    def __init__(self):
        self.warnings, self.messages = [], []

    def debug(self, msg):
        self.messages.append(msg)

    info = debug

    def warning(self, msg):
        self.warnings.append(msg)

    def error(self, msg):
        self.warnings.append(msg)


class TestContainerParsing(unittest.TestCase):
    def test_sniff_container(self):
        self.assertEqual(sniff_container(read_fixture('probe_vp9_opus.webm')), 'webm')
        self.assertEqual(sniff_container(read_fixture('probe_h264_aac.mkv')), 'mkv')
        self.assertEqual(sniff_container(read_fixture('probe_h264_aac_moov_end.mp4')), 'mp4')
        self.assertEqual(sniff_container(read_fixture('probe_hevc_aac.mov')), 'mov')
        self.assertIsNone(sniff_container(read_fixture('probe_h264_aac.flv')))
        self.assertIsNone(sniff_container(b''))

    def test_matroska_webm(self):
        self.assertEqual(parse_matroska(read_fixture('probe_vp9_opus.webm')[:4096]), {
            'doctype': 'webm', 'vcodec': 'vp9', 'width': 64, 'height': 36, 'fps': 25.0, 'acodec': 'opus',
        })

    def test_matroska_hdr10(self):
        info = parse_matroska(read_fixture('probe_vp9_hdr10.webm')[:4096])
        self.assertEqual(info['vcodec'], 'vp9')
        self.assertEqual(info['dynamic_range'], 'HDR10')

    def test_matroska_mkv(self):
        info = parse_matroska(read_fixture('probe_h264_aac.mkv')[:4096])
        self.assertEqual((info['doctype'], info['vcodec'], info['acodec'], info['width'], info['height']),
                         ('matroska', 'h264', 'aac', 64, 36))

    def test_matroska_tracks_outside_head(self):
        blob = read_fixture('probe_vp9_opus.webm')
        head_info = parse_matroska(blob[:120])
        self.assertEqual(head_info['doctype'], 'webm')
        self.assertNotIn('vcodec', head_info)
        offset = head_info['tracks_offset']
        self.assertEqual(parse_matroska_tracks(blob[offset:offset + 65536])['vcodec'], 'vp9')

    @staticmethod
    def ebml(element_id, payload):
        return bytes.fromhex(element_id) + b'\x01' + len(payload).to_bytes(7, 'big') + payload

    def crafted_webm(self, track_payload):
        ebml = self.ebml
        header = ebml('1a45dfa3', ebml('4282', b'webm'))
        tracks = ebml('1654ae6b', ebml('ae', track_payload))
        return header + ebml('18538067', tracks)

    def test_matroska_oversized_fields_do_not_crash(self):
        ebml = self.ebml
        track = (ebml('83', b'\x01') + ebml('86', b'V_VP9') + ebml('23e383', b'\xff' * 200)
                 + ebml('e0', ebml('b0', b'\x40' * 20) + ebml('ba', b'\x00\x24')))
        info = parse_matroska(self.crafted_webm(track))
        self.assertEqual(info['vcodec'], 'vp9')
        self.assertNotIn('fps', info)
        self.assertNotIn('width', info)
        self.assertEqual(info['height'], 36)
        self.assertEqual(sniff_container(self.crafted_webm(track)), 'webm')

    def test_matroska_unknown_codec_is_not_success(self):
        track = self.ebml('83', b'\x01') + self.ebml('86', b'V_MS/VFW/FOURCC')
        info = parse_matroska(self.crafted_webm(track))
        self.assertNotIn('vcodec', info)
        self.assertNotIn('acodec', info)

    def test_ffprobe_info_malformed_numbers(self):
        info = ffprobe_info({'format': {'format_name': 'nut'}, 'streams': [
            {'codec_type': 'video', 'codec_name': 'h264', 'width': 64, 'height': 36, 'tags': {'rotate': '1e999'},
             'avg_frame_rate': '1/0', 'bit_rate': 'N/A'}]})
        self.assertEqual((info['vcodec'], info['width'], info['height']), ('h264', 64, 36))
        self.assertNotIn('fps', info)
        self.assertNotIn('vbr', info)

    def test_matroska_garbage(self):
        self.assertEqual(parse_matroska(b'\x1a\x45\xdf\xa3\xff\xff'), {})
        self.assertEqual(parse_matroska_tracks(b'\x00' * 16), {})

    def test_ffprobe_info_mapping(self):
        data = {
            'format': {'format_name': 'mov,mp4,m4a,3gp,3g2,mj2', 'tags': {'major_brand': 'qt  '}},
            'streams': [
                {'codec_type': 'video', 'codec_name': 'hevc', 'width': 1920, 'height': 1080, 'avg_frame_rate': '60000/1001',
                 'bit_rate': '12000000', 'color_transfer': 'arib-std-b67',
                 'side_data_list': [{'side_data_type': 'Display Matrix', 'rotation': -90}]},
                {'codec_type': 'audio', 'codec_name': 'aac', 'bit_rate': '128000'},
            ],
        }
        self.assertEqual(ffprobe_info(data), {
            'ext': 'mov', 'vcodec': 'h265', 'width': 1080, 'height': 1920, 'fps': 59.94, 'vbr': 12000,
            'dynamic_range': 'HLG', 'acodec': 'aac', 'abr': 128,
        })
        self.assertEqual(ffprobe_info({'format': {'format_name': 'matroska,webm'}, 'streams': [
            {'codec_type': 'video', 'codec_name': 'vp9', 'width': 10, 'height': 10}]})['ext'], 'webm')
        self.assertEqual(ffprobe_info({'format': {'format_name': 'flv'}, 'streams': [
            {'codec_type': 'video', 'codec_name': 'h264'}]}), {'ext': 'flv', 'vcodec': 'h264', 'acodec': 'none'})

    @unittest.skipUnless(shutil.which('ffprobe'), 'ffprobe not installed')
    def test_ffprobe_info_real_flv(self):
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams',
             os.path.join(TESTDATA, 'probe_h264_aac.flv')], capture_output=True, text=True, check=True).stdout
        info = ffprobe_info(json.loads(out))
        self.assertEqual((info['ext'], info['vcodec'], info['acodec'], info['width'], info['height'], info['fps']),
                         ('flv', 'h264', 'aac', 64, 36, 25.0))


class TestProbeDouyinOriginal(unittest.TestCase):
    """驱动 DouyinIE._probe_douyin_original：假 CDN 按 Range 返回样本字节，统计请求数。"""

    def make_ie(self, blob, first_response_limit=None, params=None):
        logger = Logger()
        ydl = YoutubeDL({'quiet': True, 'logger': logger, **(params or {})})
        ie = DouyinIE(ydl)
        requests = []

        def fake_request_webpage(req, video_id, note=None, *args, **kwargs):
            start, end = map(int, re.fullmatch(r'bytes=(\d+)-(\d+)', req.headers['Range']).groups())
            end = min(end, len(blob) - 1)
            if not requests and first_response_limit:
                end = min(end, first_response_limit - 1)
            requests.append((note, start, end, dict(req.headers)))
            return FakeResponse(blob, start, end)

        ie._request_webpage = fake_request_webpage
        return ie, requests, logger

    def probe(self, ie):
        original = {'format_id': 'original', 'url': 'https://www.iesdouyin.com/aweme/v1/play/?video_id=v0', 'ext': 'mp4'}
        return ie._probe_douyin_original(original, [], '7688933470114024746', 400)

    def test_webm_in_head_needs_one_request(self):
        blob = read_fixture('probe_vp9_opus.webm')
        ie, requests, logger = self.make_ie(blob)
        original, reachable = self.probe(ie)
        self.assertTrue(reachable)
        self.assertEqual(len(requests), 1)
        self.assertNotIn('Referer', requests[0][3])
        for key, value in {'ext': 'webm', 'vcodec': 'vp9', 'acodec': 'opus', 'fps': 25.0, 'width': 64,
                           'height': 36, 'filesize': len(blob)}.items():
            self.assertEqual(original[key], value, key)
        self.assertEqual(logger.warnings, [])

    def test_webm_tracks_via_seekhead(self):
        blob = read_fixture('probe_vp9_opus.webm')
        ie, requests, logger = self.make_ie(blob, first_response_limit=120)
        original, _ = self.probe(ie)
        self.assertEqual(len(requests), 2)
        self.assertEqual((original['ext'], original['vcodec']), ('webm', 'vp9'))
        self.assertEqual(logger.warnings, [])

    def test_mkv(self):
        ie, _, logger = self.make_ie(read_fixture('probe_h264_aac.mkv'))
        original, _ = self.probe(ie)
        self.assertEqual((original['ext'], original['vcodec'], original['acodec']), ('mkv', 'h264', 'aac'))
        self.assertEqual(logger.warnings, [])

    def test_mp4_moov_at_end(self):
        ie, _, logger = self.make_ie(read_fixture('probe_h264_aac_moov_end.mp4'))
        original, _ = self.probe(ie)
        self.assertEqual((original['ext'], original['vcodec'], original['acodec'], original['width']),
                         ('mp4', 'h264', 'aac', 64))
        self.assertEqual(logger.warnings, [])

    def test_mov_hevc(self):
        ie, _, logger = self.make_ie(read_fixture('probe_hevc_aac.mov'))
        original, _ = self.probe(ie)
        self.assertEqual((original['ext'], original['vcodec'], original['acodec']), ('mov', 'h265', 'aac'))
        self.assertEqual(logger.warnings, [])

    def patch_ffprobe(self, available=True, stdout='', returncode=0):
        from yt_dlp.extractor import tiktok
        from yt_dlp.postprocessor import ffmpeg

        calls = []
        orig_available = ffmpeg.FFmpegPostProcessor.probe_available
        orig_executable = ffmpeg.FFmpegPostProcessor.probe_executable
        orig_run = tiktok.Popen.run
        ffmpeg.FFmpegPostProcessor.probe_available = property(lambda self: available)
        ffmpeg.FFmpegPostProcessor.probe_executable = property(lambda self: 'ffprobe')

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs.get('env')))
            return stdout, '', returncode
        tiktok.Popen.run = staticmethod(fake_run)

        def restore():
            ffmpeg.FFmpegPostProcessor.probe_available = orig_available
            ffmpeg.FFmpegPostProcessor.probe_executable = orig_executable
            tiktok.Popen.run = orig_run
        self.addCleanup(restore)
        return calls

    def test_quicktime_without_ftyp(self):
        ie, requests, logger = self.make_ie(read_fixture('probe_hevc_aac_noftyp.mov'))
        original, _ = self.probe(ie)
        self.assertEqual((original['ext'], original['vcodec'], original['acodec']), ('mov', 'h265', 'aac'))
        self.assertEqual(len(requests), 2)
        self.assertEqual(logger.warnings, [])

    def test_webm_tracks_cut_by_head_are_refetched(self):
        blob = read_fixture('probe_vp9_opus.webm')
        # Tracks 的 ID 也作为 SeekID 出现在前面的 SeekHead 里，取文件头里最后一次出现的才是 Tracks 元素本身
        tracks_start = blob.rindex(b'\x16\x54\xae\x6b', 0, 1024)
        self.assertEqual(parse_matroska(blob[:tracks_start + 30]).get('tracks_offset'), tracks_start)
        # 第一个响应在 Tracks 中间截断（视频 TrackEntry 不完整），不能读残缺字节，要从 Tracks 起点再取一次
        ie, requests, logger = self.make_ie(blob, first_response_limit=tracks_start + 30)
        original, _ = self.probe(ie)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1][1], tracks_start)
        self.assertEqual((original['vcodec'], original['acodec'], original['width'], original['height']),
                         ('vp9', 'opus', 64, 36))
        self.assertEqual(logger.warnings, [])

    def test_fragmented_mp4_fills_fps_quietly(self):
        # fMP4 的 moov 里没有样本表：编码已知、帧率未知，悄悄用 ffprobe 补一次，没有 ffprobe 也不告警
        self.patch_ffprobe(available=False)
        ie, _, logger = self.make_ie(read_fixture('probe_h264_aac_fragmented.mp4'))
        original, _ = self.probe(ie)
        self.assertEqual((original['ext'], original['vcodec'], original['acodec']), ('mp4', 'h264', 'aac'))
        self.assertNotIn('fps', original)
        self.assertEqual(logger.warnings, [])

    FLV_PROBE = json.dumps({'format': {'format_name': 'flv'}, 'streams': [
        {'codec_type': 'video', 'codec_name': 'h264', 'width': 64, 'height': 36, 'avg_frame_rate': '25/1'},
        {'codec_type': 'audio', 'codec_name': 'aac'}]})

    def test_unrecognized_container_uses_ffprobe(self):
        calls = self.patch_ffprobe(stdout=self.FLV_PROBE)
        ie, _, logger = self.make_ie(
            read_fixture('probe_h264_aac.flv'), params={'proxy': 'http://user:secret@127.0.0.1:8080'})
        original, _ = self.probe(ie)
        self.assertEqual((original['ext'], original['vcodec'], original['acodec'], original['fps']),
                         ('flv', 'h264', 'aac', 25.0))
        self.assertEqual(logger.warnings, [])
        cmd, env = calls[0]
        self.assertEqual(cmd[-1], CDN_URL)
        self.assertIn('-user_agent', cmd)
        self.assertNotIn('-referer', cmd)
        # 代理经环境变量传入，不出现在命令行里（ps 看得到）
        self.assertEqual(env['http_proxy'], 'http://user:secret@127.0.0.1:8080')
        self.assertFalse(any('secret' in arg for arg in cmd))

    def proxy_env(self, proxy, environ=None):
        calls = self.patch_ffprobe(stdout=self.FLV_PROBE)
        saved = {k: os.environ.get(k) for k in ('http_proxy', 'HTTP_PROXY', 'no_proxy', 'NO_PROXY')}
        for key in saved:
            os.environ.pop(key, None)
        os.environ.update(environ or {})

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)
        ie, _, logger = self.make_ie(read_fixture('probe_h264_aac.flv'), params={'proxy': proxy})
        original, _ = self.probe(ie)
        return calls, original, logger

    def test_proxy_scheme_normalized_for_ffmpeg(self):
        # ffmpeg 只认小写 http:// 前缀；大写的要改写，不带 scheme 的按 http 处理（与 yt-dlp 一致）
        for proxy, expected in (('HTTP://127.0.0.1:8080', 'http://127.0.0.1:8080'),
                                ('127.0.0.1:7890', 'http://127.0.0.1:7890')):
            with self.subTest(proxy=proxy):
                calls, original, _ = self.proxy_env(proxy)
                self.assertEqual(calls[-1][1]['http_proxy'], expected)
                self.assertEqual(original['vcodec'], 'h264')

    def test_https_proxy_skips_ffprobe(self):
        calls, original, logger = self.proxy_env('https://user:secret@127.0.0.1:8443')
        self.assertEqual(calls, [])
        self.assertNotIn('vcodec', original)
        self.assertIn('only supports plain http proxies, not https', logger.warnings[0])
        self.assertNotIn('secret', logger.warnings[0])

    def test_no_proxy_drops_environment_proxy(self):
        # --proxy "" 时 yt-dlp 直连，ffprobe 也不能去读环境里的 http_proxy
        calls, _, _ = self.proxy_env('', environ={'http_proxy': 'http://127.0.0.1:9', 'NO_PROXY': 'x'})
        env = calls[-1][1]
        self.assertNotIn('http_proxy', {k.lower() for k in env})
        self.assertNotIn('no_proxy', {k.lower() for k in env})

    def test_ffprobe_missing_warns_and_keeps_partial_info(self):
        self.patch_ffprobe(available=False)
        blob = read_fixture('probe_h264_aac.flv')
        ie, _, logger = self.make_ie(blob)
        original, reachable = self.probe(ie)
        self.assertTrue(reachable)
        self.assertEqual((original['ext'], original['filesize']), ('mp4', len(blob)))
        self.assertNotIn('vcodec', original)
        self.assertEqual(len(logger.warnings), 1)
        self.assertIn('unrecognized container', logger.warnings[0])
        self.assertIn('ffprobe not found', logger.warnings[0])

    def test_socks_proxy_skips_ffprobe(self):
        calls = self.patch_ffprobe(stdout=self.FLV_PROBE)
        ie, _, logger = self.make_ie(read_fixture('probe_h264_aac.flv'), params={'proxy': 'socks5://127.0.0.1:1080'})
        original, _ = self.probe(ie)
        self.assertEqual(calls, [])
        self.assertNotIn('vcodec', original)
        self.assertIn('only supports plain http proxies, not socks5', logger.warnings[0])

    def test_ffprobe_failure_is_reported(self):
        self.patch_ffprobe(stdout='', returncode=1)
        ie, _, logger = self.make_ie(read_fixture('probe_h264_aac.flv'))
        original, _ = self.probe(ie)
        self.assertNotIn('vcodec', original)
        self.assertIn('ffprobe exited with code 1', logger.warnings[0])


if __name__ == '__main__':
    unittest.main()
