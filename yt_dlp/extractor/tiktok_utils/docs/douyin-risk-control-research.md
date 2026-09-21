# 抖音 / TikTok 风控调研记录

- 调研时间：2026-09-21
- 适用分支：`tiktok`
- 对应代码：`yt_dlp/extractor/tiktok.py`（`DouyinIE`、`TikTokBaseIE._extract_web_formats`）、`yt_dlp/extractor/tiktok_utils/douyin/*`

本文记录实测结论和证据，以及调研时参考过的外部项目。风控随时会变，这里的结论都有时效性：
出问题时先按「3. 排查方法」复测，再对照本文判断是哪一层变了；外部项目的新方案见「4. 参考项目」。

------

## 1. 现状速览

`DouyinIE._real_extract` 依次尝试三级策略，每级失败都打印一条带原因的 WARNING，全部失败时最终报错汇总三级原因。
API 响应带 `filter_reason`（视频不存在等）时直接报错，不再尝试后续策略。

| 策略 | 请求 | 依赖 | 海外 IP | 国内移动 | 国内电信 |
| --- | --- | --- | --- | --- | --- |
| 1. open API | `aweme/v1/web/aweme/detail/`，`Origin` / `Referer` 为 `https://open.douyin.com`，只带 `aweme_id` + `aid` | 无 | ✅ | ✅ | ✅ |
| 2. web API | 同一接口，`www.douyin.com` 来源 + `a_bogus` | `ttwid` | ❌ 403 Argus | ✅ 10/10 | ❌ 5/5 403 Argus |
| 3. webpage | 精选页 `jingxuan?modal_id=` 的 SSR `RENDER_DATA` | `__ac_nonce` + `__ac_signature` | ✅ | ✅ | ✅ |

- 海外 IP：测试代理，出口为巴西住宅 IP，换过 4 个 IP。
- 国内移动：Mac mini，江苏移动家宽，抖音节点 `CHN-JSlianyungang-AREACMCC5`。
- 国内电信：Windows 下载机，江苏徐州电信家宽，抖音节点 `CHN-JSlianyungang-CT5`。
- 国内两列各只有一台机器的数据，「电信被拦」的原因尚未确定，见「5. 待验证」。

下载视频时，两个 API 策略的格式都必须带 `Referer`，见 2.4。

另有 App feed 接口也能绕开 Argus，但实测最高只有 720p，未接入，见 2.8。

------

## 2. 机制详解

### 2.1 aweme/detail 与 Argus 安全网关

抖音在 `aweme/v1/web/aweme/detail/` 前面加了边缘安全插件 `ArgusSecurityPlugin`，被拦时返回 `403`，响应体是纯文本：

| 请求内容 | 响应 |
| --- | --- |
| 不带 `uifid` query 参数 | `403 Blocked by ArgusSecurityPlugin Uifid Not Found` |
| 带 `uifid` 参数（任意值，包括随机 320 位 hex、浏览器真实的 UIFID） | `403 Blocked by ArgusSecurityPlugin Signature Not Found` |
| 只在 Cookie 里带 `UIFID`，不带 query 参数 | 仍是 `Uifid Not Found`，说明它看的是 query 参数 |

`Signature Not Found` 缺的是 `x-secsdk-web-signature`（外部项目称为 webSign），只有真实抖音页面里的安全 SDK（JSVMP）能生成，
生成顺序是先 `a_bogus` 再 webSign，输入包括完整 URL、时间戳、`uifid`（见 4. 参考项目中的 nous-app、amagi）。

以下尝试都没能绕过 `Signature Not Found`：

- 补齐浏览器请求中的 `msToken`、`verifyFp` / `fp`（取值即 `s_v_web_id`）、`webid`、新版本号等 query 参数；
- 用服务端下发的 `UIFID_TEMP`、浏览器的 `UIFID`、随机值分别作 Cookie 和 query 参数。

`UIFID` 相关 Cookie（在内置浏览器中观察）：

- `UIFID_TEMP`：160 位 hex，由服务端在页面响应的 `Set-Cookie` 下发；
- `UIFID`：320 位 hex，由页面 JS 计算，与 `UIFID_TEMP` 只有前 64 位相同；
- 页面发出的 `aweme/v1/web/*` 请求 query 中带 `uifid=<UIFID>`、`webid`、`verifyFp`、`fp`、`msToken`、`a_bogus`。

**绕过方式：open.douyin.com 来源**（来自 4. 参考项目中的 Diana、nonebot-plugin-parser-lite）

```
GET https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=<id>&aid=6383
Origin: https://open.douyin.com
Referer: https://open.douyin.com
```

- 不需要 `a_bogus`、Cookie，1 个请求完成；
- 实测：海外 4 个 IP × 4 个视频 16/16 成功，国内移动、电信均成功；
- 返回的 `aweme_detail` 与 web API 一致，解析出的格式是 web API 的超集（某视频多 8 档 540p H.265），最高画质相同；
- 这是利用一个未被纳入校验的来源，随时可能被堵，所以 web API 和 webpage 两级保留作兜底。

### 2.2 a_bogus 与访客 Cookie（web API）

- web API 实测只依赖 `ttwid`：不带任何 Cookie 返回 200 空响应，只带 `ttwid` 即返回 `aweme_detail`；
  `s_v_web_id`、`msToken` 由本地生成，保留是为了更像浏览器。
- `a_bogus` 缺失或不被接受时同样是 200 空响应（`HTTP 200 with empty body`）。
- `a_bogus` 按 query 字典插入顺序拼接签名，`build_aweme_detail_query` 不能随意重排字段。
- 算法来源：上游 [yt-dlp#16182](https://github.com/yt-dlp/yt-dlp/pull/16182)（未合并，2026-03-10 关闭），
  PR 说明中 ABogus 移植自 [f2](https://github.com/Johnserf-Seed/f2)（Apache 2.0），SM3 移植自 gmssl。
- 回归测试：`a_bogus` 依赖随机数和当前时间，固定 `random.seed` 并 mock `time.time` 后对比输出。

### 2.3 __ac_nonce / __ac_signature（byted_acrawler，页面方案）

流程：

1. 首次请求任意页面返回约 72KB 的 JSVMP 挑战页，同时 `Set-Cookie: __ac_nonce=...; Max-Age=1800`；
2. 页面 JS 执行 `window.byted_acrawler.sign("", __ac_nonce)` 得到签名，写入 `__ac_signature`（有效期 1 年），然后 `location.reload()`；
3. 带着这一对 Cookie 再请求，返回约 1.1–1.3MB 的真实页面（含 `RENDER_DATA`）。

`__ac_nonce`：`0` + 8 位 hex Unix 时间戳 + 随机 hex，有效期 30 分钟。

`__ac_signature`：共 47 字符，结构如下（编码表 `A-Z a-z 0-9 - .`，每字符 6 bit）：

| 位置 | 内容 |
| --- | --- |
| 前 14 字符 | 固定前缀 `_02B4Z6wo00f01` |
| 5 + 5 字符 | 中间量 b：`timestamp ^ (site 哈希 * 65521)`，高 14 位固定 |
| 5 + 1 字符 | 环境常量：浏览器每次现算，Python 实现固定为 `582085784` |
| 5 + 5 字符 | UA 哈希（高 16 位）+ nonce 哈希（低 16 位），均以 b 的哈希为种子 |
| 5 字符 | site 哈希 |
| 末 2 字符 | 校验位（前 45 字符的乘法哈希） |

- site 输入是去掉协议头的 `location.href`：在首页完成挑战时为 `www.douyin.com/`，在精选页为 `www.douyin.com/jingxuan?modal_id=...`
  （用两个浏览器实际签名反解核对）。给定相同输入，Python 实现只有环境常量（以及随之变化的校验位）与浏览器不同，其余字段逐字一致。
- 算法来源：fork 提交 `4d50bfb94` 引入，出处未在提交记录中注明。

服务端校验项（实测）：

| 带的 Cookie | 结果 |
| --- | --- |
| 都不带 / 只带签名 | JS 挑战页 |
| 只带 nonce / nonce + 乱写签名 / nonce 与签名错配 / 校验位被改 | 验证码中间页 |
| 本地算出的一对 | ✅ |
| 3 个月前的一对，换了 UA | ✅ |
| site 乱填（`foo.example`）算出的一对 | ✅ |

即只校验「签名中的 nonce 哈希」和「末 2 位校验位」，不校验 UA、site、时间戳、环境常量。

生命周期陷阱：nonce 30 分钟过期，签名却长期残留（本地写入的不过期，浏览器写入的 1 年），
所以 `--cookies-from-browser`、复用的 `--cookies` 文件、运行超过 30 分钟的批量任务都会出现「新 nonce + 旧签名」→ 验证码中间页。
现在 `ensure_douyin_ac_cookies` 用 `ac_signature_matches_nonce` 复现服务端的两项校验，签名不属于当前 nonce 就清掉各域上的旧签名后重算；
浏览器导入的签名在 `www.douyin.com`（host-only），本地写入的在 `.douyin.com`，只覆盖一个会导致同名 Cookie 并存。

### 2.4 视频 CDN 的 Referer

- `v26-web.douyinvod.com` 等节点校验 `Referer`，缺失返回 `403`，响应头 `X-CCDN-FORBID-CODE: 020200`；
  `aweme/v1/play` 镜像会跳转到这类节点；`v11-weba.douyinvod.com` 不校验。
- 同画质多个镜像时 yt-dlp 默认选最后一个（`-2`），恰好是 v26-web。
- `_parse_aweme_video_app` 与 TikTok 共用，所以在 `DouyinIE._real_extract` 里补 `http_headers: {'Referer': 'https://www.douyin.com/'}`。
- 只 `-F` 列格式发现不了这类问题，要用 `--test` 实际下载。

### 2.5 视频不存在 / 被过滤

- 两个 API 都返回 `200`，`aweme_detail` 为 null，附 `filter_detail`：`{"filter_reason": "core_dep", "detail_msg": "", "notice": "", ...}`；
- 页面方案有 `RENDER_DATA`，但 `app.videoDetail` 为 null；
- 所以 API 响应带 `filter_reason` 就直接报错 `Douyin video is unavailable (status_code=0, filter_reason=core_dep)`。
- 其他 `filter_reason`（已删除、私密、地区限制等）尚未遇到实例。

### 2.6 页面方案的页面特征

- 只有精选页 `https://www.douyin.com/jingxuan?modal_id=<id>` 的 SSR 在 `RENDER_DATA` 中返回 `app.videoDetail`，其他页面没有；
- JS 挑战页：含 `byted_acrawler.sign`；
- 验证码中间页：`<title>验证码中间页</title>`，约 6KB；
- 页面方案每档码率只取第一个镜像（镜像多了 yt-dlp 也不会自动换，没有实际好处）。

### 2.7 TikTok

- Web hydration 的顶层 `video.size` 描述的是无水印的 playAddr（与 `PlayAddrStruct.DataSize` 一致），
  带水印的 download 实测 665803 字节而 `video.size` 为 658136，所以 download 的兜底大小写 `filesize_approx`。
- `TikTokIE._TESTS` 中多数视频锁区（`Your IP address is blocked from accessing this post`，换巴西 IP 也一样）；
  当时可访问的样本：`pokemonlife22/7059698374567611694`、`hankgreen1/7047596209028074758`，`tatemcrae/7107337212743830830` 仅国内直连（经本机代理）可访问。

### 2.8 App feed（未接入，仅实测）

抖音手机 App 的推荐流接口，即 App 里上下刷视频时拉取视频列表的接口（方案来自 4. 参考项目中的 Diana、media-parser）：

```
GET https://api5-normal-c-hl.amemv.com/aweme/v1/feed/?aweme_id=<id>&aid=1128
GET https://aweme.snssdk.com/aweme/v1/feed/?aweme_id=<id>&aid=1128        （备用域名）
User-Agent: com.ss.android.ugc.aweme/300904 (Linux; U; Android 12; zh_CN; SM-G9730; Build/SQ3A.220705.004; Cronet/TTNetVersion:2e47e0ce 2022-05-19)
```

- 走 App 的接口域名，不经过网页 `www.douyin.com` 的 Argus；`aid=1128` 是 App 的应用 ID（网页为 6383）；不需要 Cookie 和签名；
- 返回 `aweme_list`：带 `aweme_id` 时目标视频排在第一个，后面混入推荐视频，要按 `aweme_id` 挑，不能直接取第一个。

实测（2026-09-21，国内移动直连，两个域名结果相同）：

| 视频 | 返回 | 目标位置 | `bit_rate` 档数 | 最高画质 | open API 对照 |
| --- | --- | --- | --- | --- | --- |
| 7686096925641338809 | 7 个视频 | 第 1 个 | 7 | 1280x720 | 29 档，最高 2560x1440 |
| 6961737553342991651（老视频） | 7 个视频 | 第 1 个 | 2 | 720x1280 | — |

限制：

- **画质最高 720p**，档位也少得多，远不如 open API 和页面方案（都能拿到 2K）；
- App 协议的多数接口要求 `X-Gorgon` / `X-Argus` 等签名，这个接口目前不查，随时可能补上；
- 图集类作品不支持（media-parser 让图集改走网页接口）；
- 未在海外、电信网络下测过。

所以它只适合做最后兜底，排在页面方案之后。

------

## 3. 排查方法

### 3.1 日志原因对照

| 日志 | 含义 |
| --- | --- |
| `Douyin open API failed: HTTP 403: Blocked by ArgusSecurityPlugin ...` | open.douyin.com 来源也被纳入 Argus 校验，绕过方式失效 |
| `Douyin web API failed: HTTP 403: Blocked by ArgusSecurityPlugin Uifid Not Found` | 当前网络下 web API 被 Argus 拦截（海外 IP、国内部分网络） |
| `Douyin web API failed: HTTP 200 with empty body (a_bogus or ttwid not accepted)` | `a_bogus` 失效或 `ttwid` 缺失 |
| `Unable to obtain Douyin ttwid cookie` | ttwid 注册接口与首页都没下发 ttwid |
| `Douyin video is unavailable (... filter_reason=...)` | 视频不存在 / 被过滤 |
| `Unable to fetch Douyin home page for __ac_nonce: ...` | 首页请求失败（网络 / 代理） |
| `Douyin home page did not issue __ac_nonce` | 首页没下发 nonce，页面请求多半拿到 JS 挑战页 |
| `webpage: got the anti-bot JS challenge page` | `__ac_*` 缺失或不被接受 |
| `webpage: got the captcha page (验证码中间页)` | 签名与 nonce 不匹配，或触发风控 |
| `webpage: RENDER_DATA has no videoDetail` | 页面结构变化，或视频不可用 |

### 3.2 判断抖音看到的出口 IP

本机常有分流（代理规则、TUN），`myip.ipip.net`、`ipinfo.io` 看到的 IP 不代表访问抖音的线路。
实测 Mac 上两者都显示美国，而抖音看到的是江苏移动。以抖音 CDN 的响应头为准：

- 视频 CDN（douyinvod）响应头 `x-response-cinfo`：CDN 看到的客户端 IP；
- `www.douyin.com` 响应头 `via`：分配到的边缘节点，如 `CHN-JSlianyungang-CT5-CACHE7`（电信）、`CHN-JSlianyungang-AREACMCC5-CACHE22`（移动）。

诊断脚本（用 fork 所在 venv 的 Python 运行，约 12 个请求）：

```python
import json

import yt_dlp
from yt_dlp.extractor.tiktok_utils.douyin.api import (
    AWEME_DETAIL_API_URL,
    build_aweme_detail_query,
    build_open_aweme_detail_query,
    sign_aweme_detail_query,
)
from yt_dlp.extractor.tiktok_utils.douyin.constants import DOUYIN_OPEN_API_HEADERS
from yt_dlp.extractor.tiktok_utils.douyin.constants import DOUYIN_USER_AGENT as UA
from yt_dlp.extractor.tiktok_utils.douyin.cookies import ensure_douyin_visitor_cookies
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import update_url_query

VID = '7686432847778982833'
ydl = yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True})

for name, url in (('国内服务看到的 IP', 'https://myip.ipip.net'), ('国外服务看到的 IP', 'https://ipinfo.io/ip')):
    try:
        print(f'{name}: {ydl.urlopen(url).read().decode().strip()}')
    except Exception as e:
        print(f'{name}: {type(e).__name__} {e}')

home = ydl.urlopen(Request('https://www.douyin.com/', headers={'User-Agent': UA}))
print('抖音 CDN 节点 (via):', home.headers.get('via'))

# 抖音视频 CDN 响应头 x-response-cinfo = CDN 看到的客户端 IP，这才是抖音实际看到的出口
detail = json.loads(ydl.urlopen(Request(
    update_url_query(AWEME_DETAIL_API_URL, build_open_aweme_detail_query(VID)), headers=DOUYIN_OPEN_API_HEADERS)).read())
play_url = next(u for u in detail['aweme_detail']['video']['play_addr']['url_list'] if 'douyinvod' in u)
video = ydl.urlopen(Request(play_url, headers={'User-Agent': UA, 'Referer': 'https://www.douyin.com/', 'Range': 'bytes=0-0'}))
print('抖音视频 CDN 看到的客户端 IP (x-response-cinfo):', video.headers.get('x-response-cinfo'), '| 节点:', video.headers.get('via'))

for i in range(5):
    ydl = yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True})
    ie = ydl.get_info_extractor('Douyin')
    ensure_douyin_visitor_cookies(ie, VID, UA)
    url = update_url_query(AWEME_DETAIL_API_URL, sign_aweme_detail_query(build_aweme_detail_query(VID), UA))
    try:
        body = ydl.urlopen(Request(url, headers={'User-Agent': UA, 'Referer': 'https://www.douyin.com/'})).read().decode()
        print(f'web API #{i + 1}:', 'OK' if '"aweme_detail":{' in body else f'200 len={len(body)}')
    except HTTPError as e:
        print(f'web API #{i + 1}: HTTP {e.status} {e.response.read().decode()[:60]}')
```

### 3.3 测试注意事项

- 测试时优先走测试代理（出口多为巴西），避免本机 IP 被标记；但 web API 在海外必被拦，只能国内直连测。
  要单独测 web API 或页面方案，把前面的策略 monkeypatch 成失败：
  `DouyinIE._fetch_douyin_open_detail = lambda self, vid: (None, 'forced off')`。
- 同一视频两次请求返回的格式数会波动（web API 69↔78、页面 12↔15、TikTok 的 audio 格式时有时无），
  新旧代码对比要背靠背跑，且只比两边共有格式的元数据。
- 用 `--test` 实际下载（只下 10KB），只列格式发现不了下载阶段的 403。
- 代理每个请求 4–6 秒，页面方案一个场景要几十秒，批量用例放后台跑，Python 加 `-u` 避免超时丢输出。
- 签名类算法重构时，用固定输入生成基准输出做逐字回归（`__ac_signature` 用了 204 组）。
- 验证 ECS 选节点时注意：`dns.alidns.com` 带 `edns_client_subnet` 查到的是另一层 CDN
  （`via` 形如 `...jswuxi-ct53-bm.Creative`），在那里连 open API 都返回空，不能代表实际访问的节点。

------

## 4. 参考项目

| 项目 | 链接 | 可借鉴的方案 | 什么时候回来看 |
| --- | --- | --- | --- |
| yt-dlp 上游 PR #16182 | [yt-dlp/yt-dlp#16182](https://github.com/yt-dlp/yt-dlp/pull/16182) | 本 fork `a_bogus` + SM3 + `ttwid` 注册 + `s_v_web_id` 的来源（未合并） | `a_bogus` 失效时追溯原始实现 |
| yt-dlp 上游 issue #9667 | [yt-dlp/yt-dlp#9667](https://github.com/yt-dlp/yt-dlp/issues/9667) | 上游 Douyin「Fresh cookies needed」问题的主 issue（仍 open） | 看上游是否有官方修复 |
| f2 | [Johnserf-Seed/f2](https://github.com/Johnserf-Seed/f2) | 多平台下载器，`ABogus` 算法原始出处（Apache 2.0） | `a_bogus` 算法版本更新 |
| gmssl | PR 中链接 [duanhongyi/gmssl](https://github.com/duanhongyi/gmssl)，现为 [py-gmssl/py-gmssl](https://github.com/py-gmssl/py-gmssl)（GitHub 显示 MIT） | SM3 纯 Python 实现 | SM3 实现有疑问时对照 |
| Diana PR #612 | [SuInk/Diana#612](https://github.com/SuInk/Diana/pull/612) | 三级回退：① open.douyin.com 来源免签名 detail；② App feed 接口 `api5-normal-c-hl.amemv.com` / `aweme.snssdk.com` 的 `/aweme/v1/feed/`，`aid=1128` + 安卓 App UA，按 `aweme_id` 过滤（我们实测最高 720p，见 2.8）；③ 老的 Cookie + `uifid` + `a_bogus`（仍 403） | open API 与页面方案都失效时，App feed 可作最后兜底 |
| nonebot-plugin-parser-lite PR #311 / #312 | [#311](https://github.com/sokoko-org/nonebot-plugin-parser-lite/pull/311)、[#312](https://github.com/sokoko-org/nonebot-plugin-parser-lite/pull/312) | 同样改用 open.douyin.com 来源，只带 `aweme_id` + `aid`，不再发 `ttwid` 和浏览器 / 设备参数 | 同上 |
| media-parser Issue #15 | [ucmao/media-parser#15](https://github.com/ucmao/media-parser/issues/15) | 根因分析：Argus 对缺少真实浏览器 `UIFID` 的匿名请求按概率拦截（报告拦截率 40%–50%）；主通道改用移动端 feed 协议 `api5-normal-c-hl.amemv.com`（称 0% 403、覆盖 95% 以上普通视频；我们实测最高 720p，见 2.8），图集走 Web API + 指数退避 | 需要 App 协议方案、或评估国内拦截概率时 |
| nous-app PR #2360 | [iocrazy/nous-app#2360](https://github.com/iocrazy/nous-app/pull/2360) | 实现 Argus webSign：`x-secsdk-web-signature`，Node 子进程跑 `websign_env.js` / `websign_runtime.js`，先 `a_bogus` 再 webSign，输入为带 `a_bogus` 的 URL、时间戳、`uifid` | 决定自己实现 webSign 时 |
| amagi PR #188 | [ikenxuan/amagi#188](https://github.com/ikenxuan/amagi/pull/188) | ① 免鉴权接口：`iesdouyin.com/web/api/v2/user/info/`（用户名转 `sec_uid`）、`/v2/music/info/`、`/v2/music/list/aweme/`、`api.amemv.com/aweme/v1/im/resources/emoji/`；② `webid` 与会话 Cookie 不匹配时服务端静默返回 200 空响应，改为读响应头 `cookie_ttwidinfo_webid` 按 `ttwid` 缓存；③ `x-secsdk-web-signature` 为 32 位小写 hex、放在 query 中，只对 SDK 策略表中的部分路径生效，必须是最后一步；④ Argus 拦截时重新生成 `msToken` / `verifyFp` / `a_bogus` 线性退避重试最多 5 次 | 扩展到用户主页、音乐等接口，或遇到莫名 200 空响应时 |
| douyin-downloader | [jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader) | 说明 Argus 对非浏览器请求返回 `Uifid Not Found`、webSign 只能在真实页面内生成；主页批量翻页被拦时用 Playwright 启动真实浏览器滚动采集（默认有头，需手动过验证码） | 需要浏览器兜底方案时 |
| douyinie Issue #125 | [monet88/douyinie#125](https://github.com/monet88/douyinie/issues/125) | 汇总：`aweme/detail`、`aweme/post` 被 Argus 确定性拦截，应视为风控而非瞬时错误、不要盲目重试；源头为 douyin-downloader 2026-09-14 的提交 `47f4eef` | 了解 Argus 覆盖范围时 |

延伸阅读（a_bogus 逆向分析文章，未细看）：
[a_bogus技术交流（知乎）](https://zhuanlan.zhihu.com/p/661885504)、
[某音 a_bogus-1.0.1.19-fix.01 jsvmp 参数分析（知乎）](https://zhuanlan.zhihu.com/p/1923424071007310647)、
[抖音 a_bogus 算法还原大赏（博客园）](https://www.cnblogs.com/steed4ever/p/17688627.html)。

------

## 5. 待验证 / 后续方向

1. **电信被拦的原因**：国内移动 10/10 通过、电信 5/5 被拦，各只有一台机器的数据。两种解释：
   - 按边缘节点或运营商分批开启 Argus；
   - 按 IP 信誉：电信那台是下载流水线机器，请求频繁，IP 可能被标记。

   区分方法：在电信机器上 `nslookup www.douyin.com` 拿到它实际访问的节点 IP，
   从移动网络用 `curl --resolve www.douyin.com:443:<IP>` 把 web API 请求发到该节点（先确认 `via` 是 `CHN-...-CT5-...` 这类节点）。
   也被拦就是按节点，能过就是按客户端 IP。
2. **open API 被堵后的下一步**：页面方案能拿到 2K，仍是首选兜底；
   要找新的高画质 API 通道，只能实现 webSign（nous-app 的 Node 方案，工作量大、需跟随 SDK 更新）；
   App feed 实测最高 720p，只在页面方案也失效时作最后兜底（见 2.8）。
3. **其他 `filter_reason`**：遇到已删除、私密、地区限制的视频时，补充对应的 `filter_reason` 取值和页面表现。
4. **`webid`**：目前两个 API 策略都不带 `webid`；将来若加上，注意 amagi 发现的「`webid` 与会话不匹配则 200 空响应」。
