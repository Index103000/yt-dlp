"""
TikTok 网页端 X-Dynosaur / X-Gnarly 签名（yt_dlp/extractor/tiktok_utils/tiktok/websign.py）的离线向量校验。

向量来自 Evil0ctal/Douyin_TikTok_Download_API tests/unit/test_signing.py @ e02c0f3f5（Apache-2.0）：
用 Node 跑真 webmssdk 2.0.0.561 得到的签名，把其中的 key / 时间戳 / nonce 喂回来即可逐字节比较。
向量由桩环境生成（env_code 129 / ub_code 14），线上常量 65 / 8 由浏览器抓包（browser_capture）钉住。
"""
import hashlib
import json
import os
import random

import pytest

from yt_dlp.extractor.tiktok_utils.tiktok import websign
from yt_dlp.extractor.tiktok_utils.tiktok.api import (
    ITEM_DETAIL_API_URL,
    TIKTOK_WEB_USER_AGENT,
    build_item_detail_headers,
    build_item_detail_query,
    build_item_detail_url,
)

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testdata', 'tiktok', 'websign_vectors.json'),
          encoding='utf-8') as _f:
    _DATA = json.load(_f)

VECTORS = _DATA['vectors']
HARNESS_ENV_CODE = _DATA['harness_env_code']
HARNESS_UB_CODE = _DATA['harness_ub_code']
BROWSER_CAPTURE = _DATA['browser_capture']


def _dynosaur(vector):
    payload = websign.dynosaur_payload(
        vector['query'], vector['user_agent'],
        timestamp=vector['timestamp'], nonce=vector['nonce'], sequence=vector['sequence'],
        env_code=HARNESS_ENV_CODE, ub_code=HARNESS_UB_CODE)
    return websign.seal(payload, vector['dynosaur_key'])


def _gnarly(vector):
    sealed = f'{vector["query"]}&{websign.DYNOSAUR_PARAM}={vector["dynosaur"]}&{websign.MS_TOKEN_PARAM}={vector["ms_token"]}'
    payload = websign.gnarly_payload(
        sealed, vector['user_agent'],
        timestamp=vector['gnarly_timestamp'], nonce=vector['gnarly_nonce'], nonce2=vector['nonce2'],
        sequence=vector['gnarly_sequence'],
        # SDK 自己的输出顺序（跨进程不同，说明服务端按 key 解析；本实现默认升序），用它才能逐字节比较
        order=vector['gnarly_order'],
        env_code=HARNESS_ENV_CODE, ub_code=HARNESS_UB_CODE)
    return websign.seal(payload, vector['gnarly_key'])


def _tlv_keys(payload, lead):
    keys, offset = [], lead
    while offset + 3 <= len(payload):
        keys.append(payload[offset])
        offset += 3 + payload[offset + 2]
    return keys


@pytest.mark.parametrize('vector', VECTORS, ids=range(len(VECTORS)))
def test_reproduces_the_sdks_own_x_dynosaur(vector):
    assert _dynosaur(vector) == vector['dynosaur']


@pytest.mark.parametrize('vector', VECTORS, ids=range(len(VECTORS)))
def test_reproduces_the_sdks_own_x_gnarly(vector):
    assert _gnarly(vector) == vector['gnarly']


def test_shipped_constants_match_a_real_browser_capture():
    # 向量只能证明算法；ENV_CODE / UB_CODE 与版本常量要对着浏览器抓的真签名钉，错了服务端不报错只给 200 空 body
    payload, _ = websign.unseal(BROWSER_CAPTURE)
    fields = websign.unpack_payload(payload)
    decode = websign.decode_field
    assert int(decode(fields[0x26], websign.ENCODER_A)) == websign.ENV_CODE
    assert int(decode(fields[0x36], websign.ENCODER_A)) == websign.UB_CODE
    assert decode(fields[0x2A], websign.ENCODER_A) == websign.SDK_VERSION
    assert decode(fields[0x31], websign.ENCODER_A) == websign.SCM_VERSION
    assert int.from_bytes(fields[0x38], 'big') == websign.VM_STATE_HASH


def test_unseal_recovers_what_seal_produced():
    key = tuple(range(12))
    payload = websign.dynosaur_payload('aid=1988', 'UA', timestamp=7, nonce=9)
    recovered, recovered_key = websign.unseal(websign.seal(payload, key))
    assert recovered == payload
    assert recovered_key == key


def test_the_seal_covers_x_dynosaur_and_ms_token():
    vector = dict(VECTORS[0])
    vector['ms_token'] = 'not-the-token-that-was-signed'
    assert _gnarly(vector) != VECTORS[0]['gnarly']


def test_the_call_counter_is_in_the_payload():
    first, second = VECTORS[0], VECTORS[1]
    assert first['query'] == second['query']
    assert (first['sequence'], second['sequence']) == (1, 2)
    assert _dynosaur(first) != _dynosaur(dict(second, sequence=1))


def test_the_payloads_have_the_shape_the_platform_parses():
    vector = VECTORS[0]
    dynosaur = websign.dynosaur_payload(
        vector['query'], vector['user_agent'], timestamp=vector['timestamp'], nonce=vector['nonce'])
    # 25 个字段，key 0x20..0x38 升序，没有前导计数
    assert _tlv_keys(dynosaur, lead=0) == list(range(0x20, 0x39))
    gnarly = websign.gnarly_payload(
        'aid=1988&X-Dynosaur=x&msToken=', vector['user_agent'],
        timestamp=vector['timestamp'], nonce=vector['nonce'], nonce2=1)
    # 16 个字段跟在一个计数字节后，0x07 缺席
    assert gnarly[0] == 16
    assert _tlv_keys(gnarly, lead=1) == [*range(0x07), *range(0x08, 0x11)]
    assert len(gnarly) == 193


def test_encode_query_escapes_only_what_a_browser_does():
    pairs = [('browser_version', '5.0 (Macintosh)'), ('root_referer', 'https://www.tiktok.com/'), ('q', '中文 a#b"')]
    assert websign.encode_query(pairs) == (
        'browser_version=5.0%20(Macintosh)&root_referer=https://www.tiktok.com/&q=%E4%B8%AD%E6%96%87%20a%23b%22')


def test_sign_appends_the_four_parameters_in_the_platforms_order():
    pairs = [('aid', '1988'), ('itemId', '7474304960574786859')]
    ua = TIKTOK_WEB_USER_AGENT
    signed, params = websign.sign(pairs, ua, rng=random.Random(1))
    assert list(params) == ['X-Dynosaur', 'msToken', 'X-Bogus', 'X-Gnarly']
    assert signed == (
        f'aid=1988&itemId=7474304960574786859&X-Dynosaur={params["X-Dynosaur"]}'
        f'&msToken=&X-Bogus=1&X-Gnarly={params["X-Gnarly"]}')
    assert websign.sign_web_query(pairs, ua).startswith('aid=1988&itemId=7474304960574786859&X-Dynosaur=')
    # X-Dynosaur 绑定 query 与 UA 的哈希，并携带线上常量
    payload, _ = websign.unseal(params['X-Dynosaur'])
    fields = websign.unpack_payload(payload)
    assert int.from_bytes(fields[0x2E], 'big') == websign.hash_state('aid=1988&itemId=7474304960574786859')
    assert int.from_bytes(fields[0x30], 'big') == websign.hash_state(ua)
    assert int(websign.decode_field(fields[0x26], websign.ENCODER_A)) == websign.ENV_CODE
    # X-Gnarly 封印的是含 X-Dynosaur 与空 msToken 的 query
    gnarly_payload, _ = websign.unseal(params['X-Gnarly'])
    gnarly_fields = websign.unpack_payload(gnarly_payload, lead_count=True)
    sealed = f'aid=1988&itemId=7474304960574786859&X-Dynosaur={params["X-Dynosaur"]}&msToken='
    assert gnarly_fields[0x03].decode() == hashlib.md5(sealed.encode()).hexdigest()


def test_item_detail_request_is_signed_with_the_header_user_agent():
    video_id, device_id = '7474304960574786859', '7250000000000000001'
    url = build_item_detail_url(video_id, device_id)
    assert url.startswith(f'{ITEM_DETAIL_API_URL}?aid=1988&')
    query = url.partition('?')[2]
    business = websign.encode_query(build_item_detail_query(video_id, device_id))
    assert query.startswith(f'{business}&X-Dynosaur=')
    assert '&msToken=&X-Bogus=1&X-Gnarly=' in query
    assert f'&device_id={device_id}&' in query
    assert f'&itemId={video_id}&' in query
    assert '&user_is_login=false&' in query
    # 签名里的 UA 哈希必须等于请求头 UA 的哈希（X-Dynosaur 字段 0x30）
    dynosaur = query.partition('&X-Dynosaur=')[2].partition('&')[0]
    payload, _ = websign.unseal(dynosaur)
    headers = build_item_detail_headers()
    assert int.from_bytes(websign.unpack_payload(payload)[0x30], 'big') == websign.hash_state(headers['User-Agent'])
    assert headers['Referer'] == 'https://www.tiktok.com/'
    assert headers['Origin'] == 'https://www.tiktok.com'
