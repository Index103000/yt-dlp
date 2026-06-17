# yt_dlp/extractor/tiktok/douyin/abogus_python.py
from __future__ import annotations

import random
import time


class _ABogusPythonSigner:
    """
    Python 原生 a_bogus 实现。

    说明：
    - 该类只服务当前 Python 版算法；
    - 对外只暴露 generate_abogus_python；
    - 后续如果切换 JS 算法，不需要改这个文件，只改 abogus.py 入口。
    """

    _SM3_IV = [
        1937774191, 1226093241, 388252375, 3666478592,
        2842636476, 372324522, 3817729613, 2969243214,
    ]
    _SM3_T_J = [2043430169] * 16 + [2055708042] * 48

    _ABOGUS_CHAR = 'Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe'
    _ABOGUS_CHAR2 = 'ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe'
    _ABOGUS_UA_KEY = b'\x00\x01\x0e'

    _ABOGUS_BIG_ARRAY = [
        121, 243, 55, 234, 103, 36, 47, 228, 30, 231, 106, 6, 115, 95, 78, 101,
        250, 207, 198, 50, 139, 227, 220, 105, 97, 143, 34, 28, 194, 215, 18,
        100, 159, 160, 43, 8, 169, 217, 180, 120, 247, 45, 90, 11, 27, 197,
        46, 3, 84, 72, 5, 68, 62, 56, 221, 75, 144, 79, 73, 161, 178, 81,
        64, 187, 134, 117, 186, 118, 16, 241, 130, 71, 89, 147, 122, 129,
        65, 40, 88, 150, 110, 219, 199, 255, 181, 254, 48, 4, 195, 248,
        208, 32, 116, 167, 69, 201, 17, 124, 125, 104, 96, 83, 80, 127,
        236, 108, 154, 126, 204, 15, 20, 135, 112, 158, 13, 1, 188, 164,
        210, 237, 222, 98, 212, 77, 253, 42, 170, 202, 26, 22, 29, 182,
        251, 10, 173, 152, 58, 138, 54, 141, 185, 33, 157, 31, 252, 132,
        233, 235, 102, 196, 191, 223, 240, 148, 39, 123, 92, 82, 128, 109,
        57, 24, 38, 113, 209, 245, 2, 119, 153, 229, 189, 214, 230, 174,
        232, 63, 52, 205, 86, 140, 66, 175, 111, 171, 246, 133, 238, 193,
        99, 60, 74, 91, 225, 51, 76, 37, 145, 211, 166, 151, 213, 206,
        0, 200, 244, 176, 218, 44, 184, 172, 49, 216, 93, 168, 53, 21,
        183, 41, 67, 85, 224, 155, 226, 242, 87, 177, 146, 70, 190, 12,
        162, 19, 137, 114, 25, 165, 163, 192, 23, 59, 9, 94, 179, 107,
        35, 7, 142, 131, 239, 203, 149, 136, 61, 249, 14, 156,
    ]

    _ABOGUS_SORT_IDX = [
        18, 20, 52, 26, 30, 34, 58, 38, 40, 53, 42, 21, 27, 54, 55, 31,
        35, 57, 39, 41, 43, 22, 28, 32, 60, 36, 23, 29, 33, 37, 44, 45,
        59, 46, 47, 48, 49, 50, 24, 25, 65, 66, 70, 71,
    ]

    _ABOGUS_SORT_IDX_2 = [
        18, 20, 26, 30, 34, 38, 40, 42, 21, 27, 31, 35, 39, 41, 43, 22,
        28, 32, 36, 23, 29, 33, 37, 44, 45, 46, 47, 48, 49, 50, 24, 25,
        52, 53, 54, 55, 57, 58, 59, 60, 65, 66, 70, 71,
    ]

    @staticmethod
    def _sm3_rotl(x, n):
        return ((x << n) & 0xffffffff) | ((x >> (32 - n)) & 0xffffffff)

    @classmethod
    def _sm3_cf(cls, v_i, b_i):
        w = []

        for i in range(16):
            weight = 0x1000000
            data = 0
            for k in range(i * 4, (i + 1) * 4):
                data += b_i[k] * weight
                weight = int(weight / 0x100)
            w.append(data)

        for j in range(16, 68):
            p1_input = w[j - 16] ^ w[j - 9] ^ cls._sm3_rotl(w[j - 3], 15)
            w.append(
                (p1_input ^ cls._sm3_rotl(p1_input, 15) ^ cls._sm3_rotl(p1_input, 23))
                ^ cls._sm3_rotl(w[j - 13], 7)
                ^ w[j - 6]
            )

        w_1 = [w[j] ^ w[j + 4] for j in range(64)]

        a, b, c, d, e, f, g, h = v_i

        for j in range(64):
            ff = (a ^ b ^ c) if j < 16 else ((a & b) | (a & c) | (b & c))
            gg = (e ^ f ^ g) if j < 16 else ((e & f) | ((~e) & g))

            ss_1 = cls._sm3_rotl(
                (cls._sm3_rotl(a, 12) + e + cls._sm3_rotl(cls._SM3_T_J[j], j % 32))
                & 0xffffffff,
                7,
            )
            ss_2 = ss_1 ^ cls._sm3_rotl(a, 12)

            tt_1 = (ff + d + ss_2 + w_1[j]) & 0xffffffff
            tt_2 = (gg + h + ss_1 + w[j]) & 0xffffffff

            d, c, b, a = c, cls._sm3_rotl(b, 9), a, tt_1
            h, g, f, e = (
                g,
                cls._sm3_rotl(f, 19),
                e,
                (tt_2 ^ cls._sm3_rotl(tt_2, 9) ^ cls._sm3_rotl(tt_2, 17)) & 0xffffffff,
            )

        return [v_i[i] ^ [a, b, c, d, e, f, g, h][i] for i in range(8)]

    @classmethod
    def _sm3_hash(cls, msg):
        msg = list(msg)
        msg_len = len(msg)

        msg.append(0x80)

        reserve = (msg_len % 64) + 1
        range_end = 56 if reserve <= 56 else 120
        msg.extend([0x00] * (range_end - reserve))
        msg.extend([(msg_len * 8 >> (8 * (7 - i))) & 0xff for i in range(8)])

        group_count = len(msg) // 64
        blocks = [msg[i * 64:(i + 1) * 64] for i in range(group_count)]

        v = [cls._SM3_IV]
        for i in range(group_count):
            v.append(cls._sm3_cf(v[i], blocks[i]))

        return ''.join(f'{x:08x}' for x in v[-1])

    @classmethod
    def _sm3_to_array(cls, input_data):
        if isinstance(input_data, str):
            input_bytes = input_data.encode('utf-8')
        else:
            input_bytes = bytes(input_data)

        hex_result = cls._sm3_hash(list(input_bytes))
        return [int(hex_result[i:i + 2], 16) for i in range(0, len(hex_result), 2)]

    @classmethod
    def _rc4(cls, key, plaintext):
        s = list(range(256))
        j = 0

        for i in range(256):
            j = (j + s[i] + key[i % len(key)]) % 256
            s[i], s[j] = s[j], s[i]

        i = j = 0
        result = []

        for char in plaintext:
            i = (i + 1) % 256
            j = (j + s[i]) % 256
            s[i], s[j] = s[j], s[i]
            result.append(ord(char) ^ s[(s[i] + s[j]) % 256])

        return bytes(result)

    @classmethod
    def _transform(cls, bytes_list, big_array):
        big_array = list(big_array)
        bytes_str = ''.join(chr(i) for i in bytes_list)

        result = []
        index_b = big_array[1]
        initial_value = value_e = 0

        for index, char in enumerate(bytes_str):
            if index == 0:
                initial_value = big_array[index_b]
                sum_val = index_b + initial_value
                big_array[1] = initial_value
                big_array[index_b] = index_b
            else:
                sum_val = initial_value + value_e

            sum_val %= len(big_array)
            result.append(chr(ord(char) ^ big_array[sum_val]))

            value_e = big_array[(index + 2) % len(big_array)]
            sum_val = (index_b + value_e) % len(big_array)
            initial_value = big_array[sum_val]

            big_array[sum_val] = big_array[(index + 2) % len(big_array)]
            big_array[(index + 2) % len(big_array)] = initial_value
            index_b = sum_val

        return ''.join(result)

    @classmethod
    def _b64(cls, s, alphabet):
        binary = ''.join(f'{ord(c):08b}' for c in s)
        pad_len = (6 - len(binary) % 6) % 6

        binary += '0' * pad_len
        indices = [int(binary[i:i + 6], 2) for i in range(0, len(binary), 6)]

        return ''.join(alphabet[idx] for idx in indices) + '=' * (pad_len // 2)

    @classmethod
    def _encode(cls, s, alphabet):
        result = []

        for i in range(0, len(s), 3):
            if i + 2 < len(s):
                n = (ord(s[i]) << 16) | (ord(s[i + 1]) << 8) | ord(s[i + 2])
            elif i + 1 < len(s):
                n = (ord(s[i]) << 16) | (ord(s[i + 1]) << 8)
            else:
                n = ord(s[i]) << 16

            for j, k in zip(range(18, -1, -6), (0xFC0000, 0x03F000, 0x0FC0, 0x3F)):
                if j == 6 and i + 1 >= len(s):
                    break
                if j == 0 and i + 2 >= len(s):
                    break
                result.append(alphabet[(n & k) >> j])

        result.append('=' * ((4 - len(result) % 4) % 4))
        return ''.join(result)

    @classmethod
    def generate(cls, params, user_agent):
        inner_w = random.randint(1024, 1920)
        inner_h = random.randint(768, 1080)

        browser_fp = (
            f'{inner_w}|{inner_h}|'
            f'{inner_w + random.randint(24, 32)}|'
            f'{inner_h + random.randint(75, 90)}|'
            f'0|{random.choice([0, 30])}|0|0|'
            f'{random.randint(1024, 1920)}|{random.randint(768, 1080)}|'
            f'{random.randint(1280, 1920)}|{random.randint(800, 1080)}|'
            f'{inner_w}|{inner_h}|24|24|Win32'
        )

        start_time = int(time.time() * 1000)

        array1 = cls._sm3_to_array(cls._sm3_to_array(params + 'cus'))
        array2 = cls._sm3_to_array(cls._sm3_to_array('cus'))

        ua_enc = cls._rc4(cls._ABOGUS_UA_KEY, user_agent)
        array3 = cls._sm3_to_array(
            cls._b64(''.join(chr(b) for b in ua_enc), cls._ABOGUS_CHAR2))

        end_time = int(time.time() * 1000)

        ab = {
            8: 3,
            18: 44,
            66: 0,
            69: 0,
            70: 0,
            71: 0,

            20: (start_time >> 24) & 255,
            21: (start_time >> 16) & 255,
            22: (start_time >> 8) & 255,
            23: start_time & 255,
            24: int(start_time / 256 / 256 / 256 / 256) >> 0,
            25: int(start_time / 256 / 256 / 256 / 256 / 256) >> 0,

            26: 0,
            27: 0,
            28: 0,
            29: 0,
            30: 0,
            31: 1,
            32: 0,
            33: 0,
            34: 0,
            35: 0,
            36: 0,
            37: 14,

            38: array1[21],
            39: array1[22],
            40: array2[21],
            41: array2[22],
            42: array3[23],
            43: array3[24],

            44: (end_time >> 24) & 255,
            45: (end_time >> 16) & 255,
            46: (end_time >> 8) & 255,
            47: end_time & 255,
            48: 3,
            49: int(end_time / 256 / 256 / 256 / 256) >> 0,
            50: int(end_time / 256 / 256 / 256 / 256 / 256) >> 0,

            51: 0,
            52: 0,
            53: 0,
            54: 0,
            55: 0,
            56: 6383,
            57: 6383 & 255,
            58: (6383 >> 8) & 255,
            59: 0,
            60: 0,
            64: len(browser_fp),
            65: len(browser_fp),
        }

        sorted_vals = [ab.get(i, 0) for i in cls._ABOGUS_SORT_IDX]

        ab_xor = 0
        for idx in range(len(cls._ABOGUS_SORT_IDX_2) - 1):
            if idx == 0:
                ab_xor = ab.get(cls._ABOGUS_SORT_IDX_2[idx], 0)
            ab_xor ^= ab.get(cls._ABOGUS_SORT_IDX_2[idx + 1], 0)

        sorted_vals.extend([ord(c) for c in browser_fp])
        sorted_vals.append(ab_xor)

        rand_bytes = ''
        for _ in range(3):
            rd = int(random.random() * 10000)
            rand_bytes += (
                chr(((rd & 255) & 170) | 1)
                + chr(((rd & 255) & 85) | 2)
                + chr(((rd >> 8) & 170) | 5)
                + chr(((rd >> 8) & 85) | 40)
            )

        return cls._encode(
            rand_bytes + cls._transform(sorted_vals, cls._ABOGUS_BIG_ARRAY),
            cls._ABOGUS_CHAR)


def generate_abogus_python(params: str, user_agent: str) -> str:
    """
    Python 原生 a_bogus 生成入口。
    """
    return _ABogusPythonSigner.generate(params, user_agent)
