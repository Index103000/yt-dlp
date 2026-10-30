#!/usr/bin/env python3
"""Offline regression tests for Douyin trace UIFID lookup and existing fallback strategies."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yt_dlp import YoutubeDL
from yt_dlp.extractor.tiktok import DouyinIE
from yt_dlp.utils import ExtractorError

VIDEO_ID = '6961737553342991651'
URL = f'https://www.douyin.com/video/{VIDEO_ID}'
DETAIL = {'aweme_id': VIDEO_ID}
MISSING = 'HTTP 403: Blocked by ArgusSecurityPlugin Uifid Not Found'


class Logger:
    def __init__(self):
        self.messages = []

    def debug(self, message):
        self.messages.append(message)

    info = warning = error = debug


class TestDouyinTraceUIFID(unittest.TestCase):
    def setUp(self):
        self.logger = Logger()
        self.ydl = self.enterContext(YoutubeDL({'quiet': True, 'cachedir': False, 'logger': self.logger}))
        self.ie = DouyinIE(self.ydl)
        self.enterContext(patch.object(YoutubeDL, 'urlopen', side_effect=AssertionError('unexpected network request')))
        self.enterContext(patch('yt_dlp.extractor.tiktok.ensure_douyin_visitor_cookies'))

    def patch(self, name, **kwargs):
        return self.enterContext(patch.object(self.ie, name, **kwargs))

    def home(self, value='trace-candidate', error=None, cookie=None):
        def respond(*args, **kwargs):
            self.assertEqual(args[0], 'https://www.douyin.com/')
            if error:
                raise error
            if cookie:
                self.ie._set_cookie('.douyin.com', 'UIFID_TEMP', cookie)
            return '<html></html>', SimpleNamespace(headers={'x-tt-trace-host': value} if value else {})
        return self.patch('_download_webpage_handle', side_effect=respond)

    def test_trace_success_skips_legacy_requests_and_does_not_persist(self):
        home = self.home()
        api = self.patch('_call_douyin_web_api', return_value=(DETAIL, None))
        legacy_page = self.patch('_obtain_douyin_uifid_temp', side_effect=AssertionError('legacy page not needed'))
        store = self.enterContext(patch.object(self.ydl.cache, 'store'))
        self.assertEqual(self.ie._fetch_douyin_web_detail(VIDEO_ID), (DETAIL, None))
        self.assertEqual(api.call_args.args[2:], ('trace-candidate', 'x-tt-trace-host'))
        home.assert_called_once()
        api.assert_called_once()
        legacy_page.assert_not_called()
        store.assert_not_called()
        self.assertFalse(list(self.ie.cookiejar))

    def test_existing_browser_cookie_skips_home_lookup(self):
        self.ie._set_cookie('.douyin.com', 'UIFID', 'browser-value')
        home = self.patch('_obtain_douyin_trace_uifid', side_effect=AssertionError('existing UIFID has priority'))
        api = self.patch('_call_douyin_web_api', return_value=(DETAIL, None))
        self.assertEqual(self.ie._fetch_douyin_web_detail(VIDEO_ID), (DETAIL, None))
        self.assertEqual(api.call_args.args[2:], ('browser-value', 'cookie UIFID'))
        home.assert_not_called()

    def test_existing_cache_skips_home_lookup(self):
        self.enterContext(patch.object(self.ydl.cache, 'load', return_value={'value': 'cached-value'}))
        home = self.patch('_obtain_douyin_trace_uifid', side_effect=AssertionError('cache has priority'))
        api = self.patch('_call_douyin_web_api', return_value=(DETAIL, None))
        self.assertEqual(self.ie._fetch_douyin_web_detail(VIDEO_ID), (DETAIL, None))
        self.assertEqual(api.call_args.args[2:], ('cached-value', 'cache'))
        home.assert_not_called()

    def check_legacy_after_home_failure(self, error=None, value=None):
        home = self.home(value=value, error=error)
        api = self.patch('_call_douyin_web_api', side_effect=[(None, MISSING), (DETAIL, None)])
        legacy_page = self.patch('_obtain_douyin_uifid_temp', return_value=('page-value', None))
        self.assertEqual(self.ie._fetch_douyin_web_detail(VIDEO_ID), (DETAIL, None))
        self.assertEqual([call.args[2] for call in api.call_args_list], [None, 'page-value'])
        home.assert_called_once()
        legacy_page.assert_called_once()

    def test_missing_header_keeps_original_unsigned_and_page_retry(self):
        self.check_legacy_after_home_failure()

    def test_empty_header_keeps_original_unsigned_and_page_retry(self):
        self.check_legacy_after_home_failure(value='   ')

    def test_home_network_error_keeps_original_flow(self):
        self.check_legacy_after_home_failure(error=ExtractorError('temporary network failure'))

    def test_rejected_trace_keeps_original_unsigned_and_page_retry(self):
        home = self.home()
        api = self.patch('_call_douyin_web_api', side_effect=[
            (None, 'HTTP 403: Validate Error'), (None, MISSING), (DETAIL, None)])
        legacy_page = self.patch('_obtain_douyin_uifid_temp', return_value=('page-value', None))
        self.assertEqual(self.ie._fetch_douyin_web_detail(VIDEO_ID), (DETAIL, None))
        self.assertEqual([call.args[2] for call in api.call_args_list], ['trace-candidate', None, 'page-value'])
        self.assertEqual(legacy_page.call_args.args, (VIDEO_ID, {'trace-candidate'}))
        home.assert_called_once()

    def test_rejected_trace_does_not_remove_real_cookie_set_by_home(self):
        self.home(cookie='real-home-value')
        api = self.patch('_call_douyin_web_api', side_effect=[(None, 'HTTP 403: Validate Error'), (DETAIL, None)])
        self.assertEqual(self.ie._fetch_douyin_web_detail(VIDEO_ID), (DETAIL, None))
        self.assertEqual([call.args[2] for call in api.call_args_list], ['trace-candidate', 'real-home-value'])
        self.assertEqual(self.ie._get_cookies(URL)['UIFID_TEMP'].value, 'real-home-value')

    def test_transient_trace_failure_keeps_same_valued_real_cookie(self):
        self.home(value='real-home-value', cookie='real-home-value')
        api = self.patch('_call_douyin_web_api', side_effect=[(None, 'HTTP 500: temporary network error'), (DETAIL, None)])
        legacy_page = self.patch('_obtain_douyin_uifid_temp', side_effect=AssertionError('real home cookie is available'))
        self.assertEqual(self.ie._fetch_douyin_web_detail(VIDEO_ID), (DETAIL, None))
        self.assertEqual([call.args[2:] for call in api.call_args_list], [
            ('real-home-value', 'x-tt-trace-host'), ('real-home-value', 'cookie UIFID_TEMP')])
        self.assertEqual(self.ie._get_cookies(URL)['UIFID_TEMP'].value, 'real-home-value')
        legacy_page.assert_not_called()

    def test_existing_invalid_cookie_still_uses_existing_source_retry(self):
        self.ie._set_cookie('.douyin.com', 'UIFID', 'invalid-browser')
        self.ie._set_cookie('.douyin.com', 'UIFID_TEMP', 'valid-temp')
        home = self.patch('_obtain_douyin_trace_uifid', side_effect=AssertionError('existing flow unchanged'))
        api = self.patch('_call_douyin_web_api', side_effect=[(None, 'HTTP 403: Validate Error'), (DETAIL, None)])
        self.assertEqual(self.ie._fetch_douyin_web_detail(VIDEO_ID), (DETAIL, None))
        self.assertEqual([call.args[2] for call in api.call_args_list], ['invalid-browser', 'valid-temp'])
        home.assert_not_called()

    def test_trace_unavailable_video_error_is_not_swallowed(self):
        self.home()
        self.patch('_call_douyin_web_api', side_effect=ExtractorError('Douyin video is unavailable', expected=True))
        legacy_page = self.patch('_obtain_douyin_uifid_temp', side_effect=AssertionError('must not retry unavailable video'))
        with self.assertRaisesRegex(ExtractorError, 'video is unavailable'):
            self.ie._fetch_douyin_web_detail(VIDEO_ID)
        legacy_page.assert_not_called()

    def test_trace_failure_preserves_embed_then_ssr_fallback(self):
        home = self.home()
        self.patch('_call_douyin_web_api', side_effect=[(None, 'HTTP 403: Validate Error'), (None, MISSING)])
        self.patch('_obtain_douyin_uifid_temp', return_value=(None, 'webpage has no UIFID_TEMP'))
        events = []

        def embed(*args):
            events.append('embed')
            return None, 'embed unavailable'

        info = {'id': VIDEO_ID, 'title': 'SSR result', 'formats': []}

        def ssr(*args):
            events.append('ssr')
            return info, None

        self.patch('_fetch_douyin_open_detail', side_effect=embed)
        self.patch('_extract_douyin_render_data', side_effect=ssr)
        self.assertEqual(self.ie._real_extract(URL)['id'], VIDEO_ID)
        self.assertEqual(events, ['embed', 'ssr'])
        home.assert_called_once()


if __name__ == '__main__':
    unittest.main()
