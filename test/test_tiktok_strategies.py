#!/usr/bin/env python3
"""TikTokIE 两条渠道（webpage_hydration / signed_web_api）的离线回退测试：不联网，monkeypatch 网络方法。"""

import json
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yt_dlp import YoutubeDL
from yt_dlp.extractor.tiktok import TikTokIE
from yt_dlp.utils import DownloadError, ExtractorError

FIXTURE = os.path.join(os.path.dirname(__file__), 'testdata', 'tiktok', 'item_struct_7474304960574786859.json')
VIDEO_ID = '7474304960574786859'
URL = f'https://www.tiktok.com/@willsmith/video/{VIDEO_ID}'
API_URL = 'https://www.tiktok.com/api/item/detail/'
CLASSIFIED = 'This post may not be comfortable for some audiences'


class Logger:
    def __init__(self):
        self.warnings, self.errors = [], []

    def debug(self, msg):
        pass

    info = debug

    def warning(self, msg):
        self.warnings.append(msg)

    def error(self, msg):
        self.errors.append(msg)


class FakeUrlh:
    def __init__(self, status=200, headers=None, url=API_URL):
        self.status, self.headers, self.url = status, headers or {}, url


def no_network(self, *args, **kwargs):
    raise AssertionError(f'unexpected network request: {args[:1]}')


class TestTikTokStrategies(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE, encoding='utf-8') as f:
            self.item = json.load(f)['itemStruct']
        self.calls = []
        self.logger = Logger()
        self.patches = [(YoutubeDL, 'urlopen', YoutubeDL.urlopen)]
        YoutubeDL.urlopen = no_network

    def tearDown(self):
        for owner, name, original in self.patches:
            setattr(owner, name, original)

    def patch(self, name, func):
        self.patches.append((TikTokIE, name, getattr(TikTokIE, name)))
        setattr(TikTokIE, name, func)

    def use_webpage(self, result=None, error=None):
        def fake(ie, url, video_id, fatal=True):
            self.calls.append('webpage')
            if error:
                raise error
            return result if result is not None else (self.item, 0)
        self.patch('_extract_web_data_and_status', fake)

    def use_api(self, body=None, status=200, headers=None):
        item = self.item if body is None else None
        payload = json.dumps({'statusCode': 0, 'itemInfo': {'itemStruct': item}}) if body is None else body

        def fake_handle(ie, url, video_id, *args, **kwargs):
            self.calls.append('api')
            assert url.startswith(API_URL), url
            return payload, FakeUrlh(status, headers)
        self.patch('_download_webpage_handle', fake_handle)

    def extract(self, strategies=None):
        opts = {'quiet': True, 'logger': self.logger, 'check_formats': False,
                'extractor_args': {'tiktok': {'strategies': strategies or []}}}
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(URL, download=False)

    def test_webpage_success_skips_api(self):
        self.use_webpage()
        self.use_api()
        info = self.extract()
        self.assertEqual(info['id'], VIDEO_ID)
        self.assertEqual(self.calls, ['webpage'])
        self.assertEqual(self.logger.warnings, [])

    def test_webpage_blocked_falls_back_to_api(self):
        self.use_webpage(error=ExtractorError(
            'Unexpected response from webpage request (HTTP 200, 612 bytes, blocked by risk control (X-TT-System-Error: 3))'))
        self.use_api()
        info = self.extract()
        self.assertEqual(info['id'], VIDEO_ID)
        self.assertGreater(len(info['formats']), 0)
        self.assertEqual(self.calls, ['webpage', 'api'])
        self.assertTrue(any('TikTok webpage_hydration failed: Unexpected response' in w for w in self.logger.warnings))

    def test_login_redirect_falls_back_to_api(self):
        self.use_webpage(error=ExtractorError(
            'TikTok is requiring login for access to this content. Use --cookies', expected=True))
        self.use_api()
        info = self.extract()
        self.assertEqual(info['id'], VIDEO_ID)
        self.assertEqual(self.calls, ['webpage', 'api'])

    def test_api_formats_use_play_mirror_only(self):
        self.use_api()
        info = self.extract(['signed_web_api'])
        self.assertEqual(self.calls, ['api'])
        video = [f for f in info['formats'] if f.get('vcodec') != 'none']
        self.assertTrue(video)
        for f in video:
            self.assertEqual(urllib.parse.urlparse(f['url']).hostname, 'www.tiktok.com', f['format_id'])
            self.assertTrue(f.get('__needs_testing'), f['format_id'])
        self.assertEqual(sorted({f['format_id'].rsplit('-', 1)[0] for f in video}), ['adapt_lowest_1080_1', 'normal_540_0'])

    def test_webpage_formats_drop_play_mirror(self):
        self.use_webpage()
        self.use_api()
        info = self.extract()
        hosts = {urllib.parse.urlparse(f['url']).hostname for f in info['formats'] if f.get('vcodec') != 'none'}
        self.assertNotIn('www.tiktok.com', hosts)
        self.assertTrue(hosts)

    def test_api_content_classified_requires_login_without_fallback(self):
        self.use_webpage()
        classified = {'id': VIDEO_ID, 'isContentClassified': True, 'ContentClassificationReason': 209007}
        self.use_api(body=json.dumps({'statusCode': 0, 'itemInfo': {'itemStruct': classified}}))
        with self.assertRaises(DownloadError):
            self.extract(['signed_web_api', 'webpage_hydration'])
        self.assertEqual(self.calls, ['api'])
        self.assertTrue(any(CLASSIFIED in e for e in self.logger.errors), self.logger.errors)

    def test_api_status_10204_raises_without_fallback(self):
        self.use_webpage()
        self.use_api(body=json.dumps({'statusCode': 10204}))
        with self.assertRaises(DownloadError):
            self.extract(['signed_web_api', 'webpage_hydration'])
        self.assertEqual(self.calls, ['api'])
        self.assertTrue(any('Your IP address is blocked' in e for e in self.logger.errors), self.logger.errors)

    def test_api_rejected_signature_reason(self):
        self.use_api(body='', headers={'tt_orcas_res': '1'})
        with self.assertRaises(DownloadError):
            self.extract(['signed_web_api'])
        self.assertTrue(any('signature or device rejected (tt_orcas_res=1)' in e for e in self.logger.errors), self.logger.errors)

    def test_unknown_strategy_before_any_request(self):
        self.use_webpage()
        self.use_api()
        with self.assertRaises(DownloadError):
            self.extract(['foo'])
        self.assertEqual(self.calls, [])
        self.assertTrue(any("Unknown TikTok strategies: 'foo'" in e for e in self.logger.errors), self.logger.errors)


if __name__ == '__main__':
    unittest.main()
