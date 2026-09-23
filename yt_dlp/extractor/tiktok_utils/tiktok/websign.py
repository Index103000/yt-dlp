# yt_dlp/extractor/tiktok_utils/tiktok/websign.py
#
# 移植自 Evil0ctal/Douyin_TikTok_Download_API（v5）：
#   仓库：https://github.com/Evil0ctal/Douyin_TikTok_Download_API
#   路径：src/dtk/signing/native/tiktok_sign.py
#   commit：e02c0f3f5c0cf70b2677498f09623e512a496c6a（2026-09-11）
#   许可证：Apache License, Version 2.0，全文见同目录 LICENSE.Apache-2.0；版权归该项目及其贡献者所有
#   （原仓库 LICENSE 为未填写署名的标准文本，原文件无版权行）。
#
# 按 Apache-2.0 第 4 条保留上述出处与许可证声明，并注明本文件相对原件的改动：
# 算法、常量、字段表均未改动；去掉了类型注解、把引号改成本仓库风格、删去与本仓库无关的 pick_ms_token / __all__ /
# 吉祥物注释，_random_key 里的 range(12) 改为 range(KEY_WORDS)（等价），原件的英文模块 docstring 改写为下面的中文摘要
# （其中关于来源与第三方实现的说明按原文保留），并增加 sign_web_query 包装函数。
#
# 原件关于来源的说明（保留）：Source: webmssdk 2.0.0.561 from lf16-tiktok-web.tiktokcdn-us.com. Everything below was
# read out of that bundle's own output. Two public implementations exist and NEITHER is used here: xvhuan/tiktok-web-params
# (MIT with an appended "learning and exchange purposes only" restriction; GitHub classifies it as NOASSERTION) and
# JoeanAmier/TikTokDownloader (GPL-3.0). This port is derived from the platform's own artifact instead; the xvhuan
# implementation was consulted only as a cross-check.
"""
TikTok 网页端 X-Dynosaur / X-Gnarly 签名的纯 Python 实现（仅标准库）。

TikTok 页面加载的 webmssdk（acrawler）会改写每个 API 请求的 URL，按固定顺序追加四个参数：

    <业务 query>&X-Dynosaur=<环境报告>&msToken=<会话 token>&X-Bogus=1&X-Gnarly=<请求封印>

本模块不依赖浏览器生成这四个参数。算法来自 webmssdk 2.0.0.561 的字节码 VM（原作者从 SDK 自身产物读出，
并用 Node 跑真 SDK 得到离线向量逐字节核对，见 test/test_tiktok_utils_websign.py）。

两个参数共用一个信封：
    payload    = TLV 条目，每条 [key][0x00][length][value...]
    key12      = 每次调用随机抽 12 个 uint32
    ciphertext = 类 ChaCha 密钥流与 payload（按 LE uint32）异或
    spliced    = 把 48 字节 key 插到 (sum(key bytes) + sum(ciphertext bytes)) % (len(ciphertext) + 1) 处
    signature  = custom_base64(b'\\x4b' + spliced)

签名自带 key，服务端因此能解密；也正因为每次 key 随机，两次独立签名不可能字节相等，
离线校验只能把向量里的 key / 时间戳 / nonce 喂回来再比较。

实测（本仓库调研文档 2.13）：/api/item/detail/ 只校验 X-Dynosaur（X-Gnarly 换成垃圾照常返回），
X-Dynosaur 里绑定的是 query 哈希与 User-Agent 哈希，所以【签名用的 UA 必须与请求头的 UA 一致】，
签名后的 query 也必须原样发送，不能再重新编码。
"""

import base64
import hashlib
import random
import time

MASK32 = 0xFFFFFFFF

# SDK 追加参数的顺序。顺序是平台的：X-Gnarly 封印的是含 X-Dynosaur 与 msToken 的 query，签名后重排就等于封印了平台收不到的字节
DYNOSAUR_PARAM = 'X-Dynosaur'
MS_TOKEN_PARAM = 'msToken'
BOGUS_PARAM = 'X-Bogus'
GNARLY_PARAM = 'X-Gnarly'

# HTTP 请求里 X-Bogus 就是字面量 '1'；16 位的 X-Bogus 只出现在 websocket 握手（SDK 的 frontierSign），网页 API 请求从不带它
BOGUS_VALUE = '1'

# 自定义 base64 字母表，与标准表按位置对应；两个参数共用，原样存在于 SDK 解码后的字符串表里
ALPHABET = 'u09tbS3UvgDEe6r-ZVMXzLpsAohTn7mdINQlW412GqBjfYiyk8JORCF5/xKHwacP'
_STANDARD = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
_TO_CUSTOM = str.maketrans(_STANDARD, ALPHABET)
_FROM_CUSTOM = str.maketrans(ALPHABET, _STANDARD)

# 信封第一个字节
ENVELOPE_TAG = 0x4B

# ChaCha 状态字 0..3：不是教科书的 expand 32-byte k，四个常量都能从线上 bundle 里 grep 到
CHACHA_INIT = (1196819126, 600974999, 3863347763, 1451689750)

# FNV-1a 32 位变体：非标准 offset basis，且每个字节多乘一次 33；两个常量都来自 bundle
FNV_OFFSET = 2166136260
FNV_PRIME = 16777619

# payload 携带的版本串（bundle 2.0.0.561）。它们是数据不是装饰：2.0.0.514 带的是 5.3.1 / 2.0.0.514 且字段集不同，
# SDK 升版本会让本实现失效（表现为 200 空 body + tt_orcas_res: 1），届时要重新对照上游更新
SDK_VERSION = '5.3.2'
SCM_VERSION = '2.0.0.561'

# payload 上报的环境指纹，取自真实浏览器。服务端只校验这两个值，错了也不报错：除 /api/post/item_list/ 外的端点
# 对签错的请求照常返回；item_list 返回 200 空 body + tt_orcas_res: 1。原作者早期用 Node 桩环境得到 129 / 14，
# 真实 Chrome 抓包是 65 / 8（离线向量是桩环境生成的，测试里按 129 / 14 复现；线上用 65 / 8）
ENV_CODE = 65
UB_CODE = 8

# X-Dynosaur 字段 0x38：跨 bundle 2.0.0.514 / 2.0.0.561 与各种环境扰动都不变，是 SDK 构建的属性而非机器的属性，
# 它是 SDK 内部某个 md5 的 hash_state，输入未能还原，所以固定为观测值
VM_STATE_HASH = 0xC46CE353

# Canvas 指纹：-1 表示「无 canvas」（没有 2d context 的页面上报的值）；给桩环境一个可用的 getContext('2d') 后 SDK 会输出真实哈希，
# 所以它是 payload 里最明显的机器人特征；若 TikTok 开始校验它，可换成按身份稳定的值
CANVAS_HASH = '-1'

# 另外三个环境字段桩环境产不出来，固定为 '0'；真实浏览器在 0x28 报 32 位哈希、0x32 报组件版本、0x33 报 md5
WEBGL_HASH = '0'
COMPONENT_VERSION = '0'
DEVICE_HASH = '0'

# SDK 认为自己所在页面的 location.host + location.pathname；没有页面的客户端如实报首页
PAGE = 'www.tiktok.com/'

# SDK 从 1 开始数自己的签名调用次数，并写进四个字段；每个身份只发一个请求的客户端报 1
CALL_SEQUENCE_START = 1

# 空 body 的 md5（GET 都是空 body），SDK 字符串表里的字面量
EMPTY_BODY_MD5 = 'd41d8cd98f00b204e9800998ecf8427e'

# SDK 的两个字节编码器，(xor_base, add_base, pre_xor, rot, post_add)：A 编码除校验和外的所有字段；B 编码校验和与三个标志位
ENCODER_A = (103, 1, None, 2, 1)
ENCODER_B = (102, 0, 165, 1, 0)

# 浏览器在 query 里唯一会转义的字符：Chrome 让括号、斜杠、冒号、逗号等原样通过；# 会开始 fragment 所以也在内
_MUST_ESCAPE = {' ': '%20', '"': '%22', '<': '%3C', '>': '%3E', '`': '%60', '#': '%23'}

# 每个信封里拼进去的 key 的字数（_random_key 抽的个数）
KEY_WORDS = 12


def encode_query(pairs):
    """
    把业务参数序列化成浏览器实际发送的形式。

    这【不是】urlencode：X-Dynosaur 字段 0x2E 是 query 的 hash_state，SDK 哈希的是浏览器自己规范化后的 URL
    而不是某个库编码后的。Chrome 把空格转成 %20、括号 / 斜杠 / 冒号原样保留，所以真实页面哈希的是
    browser_version=5.0%20(Windows)&root_referer=https://www.tiktok.com/。全部百分号编码会得到不同的字符串、
    不同的哈希，签名绑定的就是平台永远收不到的字节。
    /api/user/detail/ 校验这个绑定，/api/item/detail/ 与 /api/comment/list/ 不校验。
    哈希与发送用的是同一个字符串，所以整条链路只能有这一个编码器。
    """
    return '&'.join(f'{_escape(key)}={_escape(value)}' for key, value in pairs)


def _escape(text):
    """只转义浏览器必须转义的字符：可打印 ASCII 除 _MUST_ESCAPE 外原样通过；控制字符与非 ASCII 按 UTF-8 字节百分号编码"""
    out = []
    for ch in text:
        if ch in _MUST_ESCAPE:
            out.append(_MUST_ESCAPE[ch])
        elif ' ' < ch <= '~':
            out.append(ch)
        else:
            out.extend(f'%{byte:02X}' for byte in ch.encode('utf-8'))
    return ''.join(out)


def hash_state(text):
    """SDK 的 FNV-1a 变体：常规一轮后再乘 33"""
    value = FNV_OFFSET
    for byte in text.encode('utf-8'):
        step = ((value ^ byte) * FNV_PRIME) & MASK32
        value = (step + ((step * 32) & MASK32)) & MASK32
    return value


def encode_field(text, config):
    """
    一个 payload 字段：按位置打乱字节、补齐、末尾带长度。

    输出至少 6 字节，以 0x00 与原长度结尾，补齐字节为 (221 + index) & 255。补齐很重要：单字符字符串的第 1 字节
    永远是 0xDE，这使下面 X-Dynosaur 的校验和不论算的是什么都稳定。
    """
    xor_base, add_base, pre_xor, rotate, post_add = config
    size = max(len(text) + 2, 6)
    out = bytearray(size)
    for index, char in enumerate(text):
        value = (ord(char) ^ (xor_base + index)) & MASK32
        value = (value + add_base + (170 & index)) % 256
        if pre_xor is not None:
            value ^= pre_xor
        value = ((value << rotate) | (value >> (8 - rotate))) & 0xFF
        out[index] = ((value ^ 187) + post_add) % 256
    for index in range(len(text), size - 2):
        out[index] = (221 + index) & 0xFF
    out[size - 2] = 0
    out[size - 1] = len(text)
    return bytes(out)


def pack_payload(fields, order=None, *, lead_count=False):
    """
    把字段按平台解析的 TLV 形式排列。

    order 只用于复现抓到的签名：SDK 输出 X-Gnarly 的 16 个字段时顺序在进程内稳定、跨进程不同，
    说明服务端按 key 解析；本实现默认升序输出，测试用向量里记录的顺序做逐字节比较。
    """
    keys = sorted(fields) if order is None else list(order)
    body = b''.join(bytes((key, 0, len(fields[key]))) + fields[key] for key in keys)
    return bytes((len(keys),)) + body if lead_count else body


def _be(value, size):
    return int(value).to_bytes(size, 'big')


def mix_state(timestamp, nonce, env_code):
    """把两个 32 位值折成 16 位，再把环境码盖在高位"""
    folded = ((timestamp >> 16) ^ (nonce >> 16) ^ timestamp ^ nonce) & 0xFFFF
    return folded | (env_code << 16)


def fold_checksum(values, mode):
    """
    X-Gnarly 字段值的异或折叠，种子全 1。

    两种模式只在字符串的贡献上不同：模式 1 视为 0，模式 2 取其前 4 个 UTF-8 字节大端。
    种子是 SDK 输出的两个校验和都是普通折叠取反的原因。
    """
    accumulator = MASK32
    for value in values:
        if isinstance(value, str):
            number = 0 if mode == 1 else int.from_bytes(value.encode('utf-8')[:4], 'big')
        else:
            number = value
        accumulator ^= number & MASK32
    return accumulator & MASK32


def _quarter_round(state, a, b, c, d):
    state[a] = (state[a] + state[b]) & MASK32
    state[d] ^= state[a]
    state[d] = ((state[d] << 16) | (state[d] >> 16)) & MASK32
    state[c] = (state[c] + state[d]) & MASK32
    state[b] ^= state[c]
    state[b] = ((state[b] << 12) | (state[b] >> 20)) & MASK32
    state[a] = (state[a] + state[b]) & MASK32
    state[d] ^= state[a]
    state[d] = ((state[d] << 8) | (state[d] >> 24)) & MASK32
    state[c] = (state[c] + state[d]) & MASK32
    state[b] ^= state[c]
    state[b] = ((state[b] << 7) | (state[b] >> 25)) & MASK32


def _keystream(state, rounds):
    """
    一个 64 字节块。这【不是】ChaCha20，差异是关键：

    rounds 数的是单轮且随数据变化（5..20），奇数轮在列轮后退出；退出时机错了会有的请求过、有的不过，
    看起来像限流而不是 bug。对角轮的第三组是 (2, 7, 12, 13) 而教科书是 (2, 7, 8, 13)，第 12 道出现两次，
    几乎肯定是原版的 bug，这里刻意复现。
    """
    working = list(state)
    done = 0
    while done < rounds:
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        done += 1
        if done >= rounds:
            break
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 12, 13)
        _quarter_round(working, 3, 4, 13, 14)
        done += 1
    return [(working[i] + state[i]) & MASK32 for i in range(16)]


def _crypt(key, rounds, payload):
    """
    把 payload 按小端 uint32 读出后与密钥流异或。

    计数器在状态字 12，只在一个【完整】16 字块之后递增，所以末尾的不完整块用的是已递增的状态。
    这里的 payload 都只有一块，但循环是 SDK 的。
    """
    state = [*CHACHA_INIT, *(word & MASK32 for word in key)]
    length = len(payload)
    word_count = (length + 3) // 4
    words = [
        int.from_bytes(payload[4 * i: 4 * i + 4].ljust(4, b'\0'), 'little')
        for i in range(word_count)
    ]
    offset = 0
    while offset + 16 < word_count:
        block = _keystream(state, rounds)
        state[12] = (state[12] + 1) & MASK32
        for i in range(16):
            words[offset + i] ^= block[i]
        offset += 16
    block = _keystream(state, rounds)
    for i in range(word_count - offset):
        words[offset + i] ^= block[i]
    return b''.join(word.to_bytes(4, 'little') for word in words)[:length]


def seal(payload, key):
    """加密、把 key 拼回去、编码。见模块说明。"""
    rounds = (sum(word & 0xF for word in key) & 0xF) + 5
    ciphertext = _crypt(key, rounds, payload)
    key_bytes = b''.join(int(word).to_bytes(4, 'little') for word in key)
    position = (sum(key_bytes) + sum(ciphertext)) % (len(ciphertext) + 1)
    spliced = ciphertext[:position] + key_bytes + ciphertext[position:]
    raw = bytes((ENVELOPE_TAG,)) + spliced
    return base64.b64encode(raw).decode('ascii').translate(_TO_CUSTOM)


def unseal(token):
    """
    从平台页面自己生成的签名里恢复 (payload, key)。

    信封把 key 藏在自己的密文里，位置由 key 与密文字节推出；服务端也得这样恢复，所以逐个位置试那条关系就能找到，
    且只有一个位置满足。这是本模块唯一诚实的 oracle：常量要对着浏览器抓包（而不是桩环境）来钉。
    """
    raw = base64.b64decode(token.translate(_FROM_CUSTOM))
    if not raw or raw[0] != ENVELOPE_TAG:
        raise ValueError('not a signature envelope')
    spliced = raw[1:]
    width = KEY_WORDS * 4
    for position in range(len(spliced) - width + 1):
        key_bytes = spliced[position: position + width]
        ciphertext = spliced[:position] + spliced[position + width:]
        if position != (sum(key_bytes) + sum(ciphertext)) % (len(ciphertext) + 1):
            continue
        key = tuple(
            int.from_bytes(key_bytes[4 * i: 4 * i + 4], 'little') for i in range(KEY_WORDS)
        )
        rounds = (sum(word & 0xF for word in key) & 0xF) + 5
        return _crypt(key, rounds, ciphertext), key
    raise ValueError('no splice position satisfies the key relation')


def unpack_payload(payload, *, lead_count=False):
    """把 payload 拆回 TLV 字段，pack_payload 的逆"""
    fields = {}
    offset = 1 if lead_count else 0
    while offset + 3 <= len(payload):
        key, _, length = payload[offset], payload[offset + 1], payload[offset + 2]
        fields[key] = payload[offset + 3: offset + 3 + length]
        offset += 3 + length
    return fields


def decode_field(raw, config):
    """读回一个字符串字段，encode_field 的逆"""
    xor_base, add_base, pre_xor, rotate, post_add = config
    out = []
    for index in range(raw[-1]):
        value = ((raw[index] - post_add) % 256) ^ 187
        value = ((value >> rotate) | (value << (8 - rotate))) & 0xFF
        if pre_xor is not None:
            value ^= pre_xor
        value = (value - add_base - (170 & index)) % 256
        out.append(chr(value ^ (xor_base + index)))
    return ''.join(out)


def dynosaur_payload(query, user_agent, *, timestamp, nonce, sequence=CALL_SEQUENCE_START,
                     env_code=ENV_CODE, ub_code=UB_CODE):
    """
    环境报告：25 个 TLV 字段，key 0x20..0x38 升序。

    只有三个字段是某样东西的 hash_state、也只有它们把报告绑定到请求上：0x2B 空字符串（GET 无 body）、
    0x2E query（只有 query，不含路径）、0x30 User-Agent —— 所以签名必须用发送请求时的同一个 UA。
    """
    fields = {
        0x21: encode_field('1', ENCODER_B),
        0x22: encode_field('1', ENCODER_B),
        0x23: encode_field('0', ENCODER_A),
        0x24: encode_field(str(mix_state(timestamp, nonce, env_code)), ENCODER_A),
        0x25: encode_field(str(sequence), ENCODER_A),
        0x26: encode_field(str(env_code), ENCODER_A),
        0x27: encode_field(str(timestamp), ENCODER_A),
        0x28: encode_field(WEBGL_HASH, ENCODER_A),
        0x29: encode_field('0', ENCODER_A),
        0x2A: encode_field(SDK_VERSION, ENCODER_A),
        0x2B: _be(hash_state(''), 4),
        0x2C: encode_field(CANVAS_HASH, ENCODER_A),
        0x2D: encode_field('0', ENCODER_A),
        0x2E: _be(hash_state(query), 4),
        0x2F: encode_field(str(sequence), ENCODER_A),
        0x30: _be(hash_state(user_agent), 4),
        0x31: encode_field(SCM_VERSION, ENCODER_A),
        0x32: encode_field(COMPONENT_VERSION, ENCODER_A),
        0x33: encode_field(DEVICE_HASH, ENCODER_A),
        0x34: encode_field(str(nonce), ENCODER_A),
        0x35: encode_field(PAGE, ENCODER_A),
        0x36: encode_field(str(ub_code), ENCODER_A),
        0x37: encode_field('0', ENCODER_A),
        0x38: _be(VM_STATE_HASH, 4),
        # 先放占位，校验和要覆盖所有字段再算；任何单字符都行，因为单字符字段的第 1 字节是固定的补齐字节，SDK 用 '0'
        0x20: encode_field('0', ENCODER_A),
    }
    checksum = 0
    for key in sorted(fields):
        checksum ^= fields[key][1]
    fields[0x20] = encode_field(str(checksum), ENCODER_B)
    return pack_payload(fields)


def gnarly_fields(signed_query, user_agent, *, body=b'', timestamp, nonce, nonce2,
                  sequence=CALL_SEQUENCE_START, env_code=ENV_CODE, ub_code=UB_CODE):
    """
    请求封印的字段表：16 项，没有 0x07。

    signed_query 是已经追加了 &X-Dynosaur=...&msToken=... 的业务 query —— 即使 token 为空这个后缀也必须有，
    这使它成为对整个改写后 URL 的封印而不只是调用方参数。两个校验和按固定的逻辑顺序折叠（不是输出顺序）。
    """
    query_md5 = hashlib.md5(signed_query.encode('utf-8')).hexdigest()
    body_md5 = hashlib.md5(body).hexdigest()
    agent_md5 = hashlib.md5(user_agent.encode('utf-8')).hexdigest()
    mixed = mix_state(timestamp, nonce, env_code)
    covered = [
        0,
        env_code,
        ub_code,
        query_md5,
        body_md5,
        agent_md5,
        timestamp,
        0,
        nonce,
        SDK_VERSION,
        SCM_VERSION,
        CALL_SEQUENCE_START,
        sequence,
        sequence,
        mixed,
        nonce2,
    ]
    first = fold_checksum(covered, 2)
    second = fold_checksum([*covered, first], 1)
    return {
        0x00: _be(second, 4),
        0x01: _be(env_code, 2),
        0x02: _be(ub_code, 2),
        0x03: query_md5.encode('ascii'),
        0x04: body_md5.encode('ascii'),
        0x05: agent_md5.encode('ascii'),
        0x06: _be(timestamp, 4),
        0x08: _be(nonce, 4),
        0x09: SDK_VERSION.encode('ascii'),
        0x0A: SCM_VERSION.encode('ascii'),
        0x0B: _be(CALL_SEQUENCE_START, 2),
        0x0C: _be(sequence, 2),
        0x0D: _be(sequence, 2),
        0x0E: _be(mixed, 4),
        0x0F: _be(nonce2, 4),
        0x10: _be(first, 4),
    }


def gnarly_payload(signed_query, user_agent, *, body=b'', timestamp, nonce, nonce2,
                   sequence=CALL_SEQUENCE_START, order=None, env_code=ENV_CODE, ub_code=UB_CODE):
    """gnarly_fields 打包在一个字节的字段计数之后"""
    fields = gnarly_fields(
        signed_query,
        user_agent,
        body=body,
        timestamp=timestamp,
        nonce=nonce,
        nonce2=nonce2,
        sequence=sequence,
        env_code=env_code,
        ub_code=ub_code,
    )
    return pack_payload(fields, order, lead_count=True)


def _clock_nonce():
    """微秒时钟取 32 位，SDK 就是这么做的（冻结 Date.now / performance.now / Math.random 后两个 nonce 都确定）"""
    return int(time.time() * 1_000_000) & MASK32


def _random_key(rng):
    return tuple(rng.getrandbits(32) for _ in range(KEY_WORDS))


def sign(pairs, user_agent, *, ms_token='', body=b'', timestamp=None, nonce=None, nonce2=None,
         sequence=CALL_SEQUENCE_START, rng=None, key=None, key2=None):
    """
    为一次 TikTok 网页 API 请求返回 (query, parameters)。

    pairs 是按平台顺序排列的业务 query，四个签名参数追加在其后、不交错。返回的 query 就是要发送的字节序列，
    重新编码会破坏封印。ms_token 原样写入（空也写），绝不伪造：TikTok 有 token 时校验、没有时放行，
    伪造的 token 比没有更糟（原作者实测：伪造 token → 0 字节 + tt_orcas_res: 1；本仓库调研未复现该条，不作判据）。
    """
    source = rng or random.Random()
    stamp = int(time.time()) if timestamp is None else int(timestamp)
    first_nonce = _clock_nonce() if nonce is None else int(nonce)
    second_nonce = (~_clock_nonce()) & MASK32 if nonce2 is None else int(nonce2)

    query = encode_query(pairs)
    dynosaur = seal(
        dynosaur_payload(query, user_agent, timestamp=stamp, nonce=first_nonce, sequence=sequence),
        key if key is not None else _random_key(source),
    )
    # 封印覆盖的是追加了 X-Dynosaur 与 msToken 之后的 query，所以这两个要先拼上再算 X-Gnarly；
    # 线上排在它们之间的 X-Bogus 不在封印内 —— 这个不对称是平台的
    sealed_query = f'{query}&{DYNOSAUR_PARAM}={dynosaur}&{MS_TOKEN_PARAM}={ms_token}'
    gnarly = seal(
        gnarly_payload(
            sealed_query,
            user_agent,
            body=body,
            timestamp=stamp,
            nonce=first_nonce,
            nonce2=second_nonce,
            sequence=sequence,
        ),
        key2 if key2 is not None else _random_key(source),
    )
    parameters = {
        DYNOSAUR_PARAM: dynosaur,
        MS_TOKEN_PARAM: ms_token,
        BOGUS_PARAM: BOGUS_VALUE,
        GNARLY_PARAM: gnarly,
    }
    # 原样输出、不百分号编码：字母表含 '/'、补齐是 '='，TikTok 自己的页面两者都不转义
    signed = f'{sealed_query}&{BOGUS_PARAM}={BOGUS_VALUE}&{GNARLY_PARAM}={gnarly}'
    return signed, parameters


def sign_web_query(pairs, user_agent, *, ms_token=''):
    """
    本仓库的入口：pairs 为有序 (key, value) 列表，返回签名后的完整 query 字符串（含 X-Dynosaur / msToken / X-Bogus / X-Gnarly）。

    调用方必须把它原样拼到 URL 上发送（不能再交给 query= 重新编码），并用同一个 user_agent 作请求头。
    """
    return sign(pairs, user_agent, ms_token=ms_token)[0]
