# 抖音 / TikTok 风控调研记录

- 调研时间：2026-09-21，2026-09-22 补充 Argus 签名实测、上传原片、短链与分享链接、与 media-parser 的对比
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
| 2. web API | 同一接口，`www.douyin.com` 来源 + `a_bogus` | `ttwid` | ⚠️ 按 IP，巴西 5 个中 3 个 403 Argus | ✅ 10/10 | ❌ 5/5 403 Argus |
| 3. webpage | 精选页 `jingxuan?modal_id=` 的 SSR `RENDER_DATA` | `__ac_nonce` + `__ac_signature` | ✅ | ✅ | ✅ |

- 海外 IP：测试代理，出口为巴西住宅 IP，前后换过十几个 IP。
- 国内移动：Mac mini，江苏移动家宽，抖音节点 `CHN-JSlianyungang-AREACMCC5`。
- 国内电信：Windows 下载机，江苏徐州电信家宽，抖音节点 `CHN-JSlianyungang-CT5`。
- Argus 拦不拦按 IP 而定：同为巴西住宅 IP，有的被拦、有的不拦。国内两列各只有一台机器的数据，见「5. 待验证」。

下载视频时，两个 API 策略的格式都必须带 `Referer`，见 2.4。

- 支持的链接：`www.douyin.com/video/<id>`、任意路径上的 `?modal_id=<id>`、`iesdouyin.com` / `m.douyin.com` 的 `/share/video/<id>`、
  `v.douyin.com/<code>` 短链（见 2.10）。图集、音乐、合集等不支持。`webpage_url` 统一为 `https://www.douyin.com/video/<id>`。
- 除转码档外，额外列出上传原片 `original`（不作为默认，`-f original` 选择），见 2.9。
- web API 在 Argus 拦截的 IP 上可以靠 `x-secsdk-web-signature` 通过（2.1 实测），fork 尚未实现。
- 另有 App feed 接口也能绕开 Argus，但实测最高只有 720p，未接入，见 2.8。

------

## 2. 机制详解

### 2.1 aweme/detail 与 Argus 安全网关

抖音在 `aweme/v1/web/aweme/detail/` 前面加了边缘安全插件 `ArgusSecurityPlugin`，被拦时返回 `403`，响应体是纯文本：

服务端的判定顺序（2026-09-22 在 6 个拦截 IP 上实测，另由独立复核在 2 个拦截 IP 上复现；文案即响应体原文）：

| 请求内容 | 响应 |
| --- | --- |
| 不带 `uifid`（只放 Cookie `UIFID` 也算不带） | `403 Blocked by ArgusSecurityPlugin Uifid Not Found` |
| 带 `uifid`（query 参数或 `uifid` 请求头都算），但没有签名 | `403 ... Signature Not Found` |
| `uifid` 无效（如随机 hex），无论签名对错 | `403 ... Validate Error` |
| `uifid` 有效，签名错误（随机 md5，或签名后改动任一 query 参数） | `403 ... Sign Invalid`，响应头 `argus_security_code: web_id_sign_invalid` |
| `uifid` 有效 + 签名正确 | ✅ 200 |

拦不拦按客户端 IP 而定，不是「海外一律拦」：同一时间换 5 个巴西住宅 IP，3 个被拦（`Uifid Not Found`），
另外 2 个上 web API 和不带签名的精简请求都正常返回；后续两轮分别为 6 个中 4 个、11 个中 4 个被拦（出口也有土耳其、美国节点）。
按什么判断（IP 信誉、ASN、概率）未知；同一拦截 IP 约 1 分钟的测试窗口内拦截状态没有变化。

**`x-secsdk-web-signature`（webSign）是纯算法，可以自己生成**：

```
明文 = f"{uifid}_{timestamp}_A96D855A08C0A9707F8BEF0D9A527E4E_{canonical_query}"
签名 = md5(明文) 的 32 位小写 hex，作为最后一个 query 参数 x-secsdk-web-signature
```

- `canonical_query`：保持原参数顺序、不排序；每个 value 先解码（`+` 变空格）再按 `encodeURIComponent` 重新编码
  （保留 `!*'()`），key 只解码不重编码；无 `=` 的参数补成 `k=`；query 里没有 `uifid` 就追加 `&uifid=<uifid>`，
  再追加 `&timestamp=<秒级时间戳>`，然后整体参与哈希。
- 出处：[cv-cat/DouYin_Spider](https://github.com/cv-cat/DouYin_Spider) `utils/secsdk_web_sign.py`（2026-08-30）。
  作者 hook 了安全 SDK `runtime_bundler_34.js` 中的 `webSignUrl` 与 `CryptoJS.MD5`，盐为 SDK 虚拟机常量池里写死的值，
  称用 5 条真实抓包逐字节命中；amagi、media-parser 都移植自它。media-parser 的文档写「按键名排序」，与其代码不符（代码不排序）。
- 实测细节：不需要 `a_bogus`（先算 `a_bogus` 再签名也能过）；`timestamp` 不查时效（365 天前的也能过）；
  最小可行形态是沿用 fork 现有 query + Edge UA + 首页 Referer，Cookie 只带 `ttwid`，`uifid` 只放 query，再追加 `timestamp` 和签名；
  yt-dlp 的 `Request` / `update_url_query` 不会改写已签名的 URL。
- `uifid` 的有效来源只有两种：
  - 浏览器里的 `UIFID` Cookie（320 位 hex，页面 JS 计算）；实测改首位或末位 1 个字符仍通过，保留前 64 位、其余换成随机值则 `Validate Error`；
  - 服务端下发的 `UIFID_TEMP`（160 位 hex，偶见 192 位）：只在通过 `__ac` 挑战后的精选页真实页面响应里下发（首页与挑战页都不下发），
    同一响应还下发一个 JWT 形态的 `web_sign_token`，用途未知。`UIFID_TEMP` 不绑定 IP、至少约 2 小时内可复用（单样本）；
    但独立复核在 4 个拦截 IP 中有 2 个取不到它，原因未查明。
- fork 目前**没有实现** webSign：open API 已能覆盖所有网络。若 open API 失效，可以在 web API 上实现它，
  `uifid` 优先取 `--cookies-from-browser` 带来的 `UIFID`，其次取页面方案顺带拿到的 `UIFID_TEMP`（缓存供批量任务复用）。

`UIFID` 相关 Cookie（在内置浏览器中观察）：

- `UIFID_TEMP`：见上；
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

**为什么 open.douyin.com 不受限**

open.douyin.com 是抖音开放平台，官方视频嵌入播放器 `https://open.douyin.com/player/video?vid=<id>` 就在这个域名下，
第三方网站用 iframe 嵌入抖音视频时加载的就是它。在浏览器里打开该播放器，它自己发出的正是这个请求：

```
https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=<id>&aid=6383&msToken=&X-Bogus=DFSz...&_signature=_02B4Z6wo00001-...
```

参数只有 `aweme_id` + `aid`，签名是老一代的 `X-Bogus` 和 `_signature`（`_signature` 前缀为 `_02B4Z6wo00001`，
与 `__ac_signature` 的 `_02B4Z6wo00f01` 相近，推测同源），`msToken` 为空，没有 `uifid`、webSign、`a_bogus`。
服务端要让嵌入播放器在任何网站里都能用，就必须对「来自 open.douyin.com 的请求」放宽校验。

决定放行的是 `Origin` 请求头，不是 `Referer`、参数或签名。实测（精简参数指只带 `aweme_id` + `aid`；国内直连与一个未被 Argus 拦截的海外 IP 结果相同；
在被拦截的 IP 上，下表中非 open.douyin.com 来源的请求变为 `403 Uifid Not Found`，open.douyin.com 来源仍然成功）：

| 请求头 | 参数 | 结果 | 响应 `Access-Control-Allow-Origin` |
| --- | --- | --- | --- |
| `Origin` + `Referer` 均为 open.douyin.com | 精简 | ✅ | `https://open.douyin.com` |
| 只有 `Origin: https://open.douyin.com` | 精简 | ✅ | `https://open.douyin.com` |
| `Origin` 为 open.douyin.com | 网页全套参数，带或不带 `a_bogus` | ✅ | `https://open.douyin.com` |
| 只有 `Referer: https://open.douyin.com`（带不带路径都一样） | 精简 | 200 空 | 无 |
| `Origin: https://www.douyin.com` | 精简 | 200 空（按网页规则，缺 `ttwid`） | `https://www.douyin.com` |
| `Origin` / `Referer` 为 example.com | 精简 | 403（非白名单来源） | 无 |

- 服务端对 `Origin` 做白名单：open.douyin.com 走开放平台的宽松策略（跳过 Argus 与 `ttwid` 要求），
  www.douyin.com 走网页策略，其他来源直接 403；响应里的 `Access-Control-Allow-Origin` 证实了这个白名单。
- 服务端敢信任 `Origin`，是因为浏览器里的页面脚本无法伪造它；非浏览器客户端可以随意设置，这就是可利用之处。
- 如果将来对 open.douyin.com 来源也开始校验签名，嵌入播放器带的是 `X-Bogus` + `_signature`：
  `X-Bogus` 在 f2 里有实现（`f2/utils/xbogus.py`，未验证是否仍可用），比 webSign 容易得多，可以优先考虑补上。

### 2.2 a_bogus 与访客 Cookie（web API）

- web API 实测只依赖 `ttwid`，**`a_bogus` 目前不校验**（国内直连）：

  | Cookie | 参数 | 结果 |
  | --- | --- | --- |
  | `ttwid` | 精简 / 网页全套，不带 `a_bogus` | ✅ |
  | `ttwid` | 网页全套 + 乱写的 `a_bogus` | ✅ |
  | `ttwid` | 网页全套 + 正确的 `a_bogus` | ✅ |
  | 无 | 网页全套 + 正确的 `a_bogus` | 200 空 |
  | 无 | 精简，不带 `a_bogus` | 200 空 |

  所以 `HTTP 200 with empty body` 是缺 `ttwid` 造成的。仍然保留 `a_bogus`，以防服务端重新开始校验。
  `s_v_web_id`、`msToken` 由本地生成，保留是为了更像浏览器。
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
- iesdouyin 分享页（2.11）对不存在的作品返回 `item_list=[]`，`filter_list[0].filter_reason='SYSTEM_ITEM_NOT_EXIST'`。
- 其他 `filter_reason`（已删除、私密、地区限制等）尚未遇到实例；media-parser 的注释和文档提到
  `status_self_see`、`status_deleted`、`status_part_see`，但其仓库里没有样本，未核实。

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

2026-09-22 按 media-parser 原样复现（国内直连，IPv4 / IPv6 两个出口结果相同）：

- feed 的 `bit_rate` 全部是 H.265（`is_h265=1`），media-parser 的「H.264 优先」落空，它最终交给用户的是 **HEVC 1280x720**
  （809 为 8.5MB）；feed 自带的 `play_addr_h264` 更低（809 为 1024x576）；
- `video.width/height` 写着 2560x1440，但 feed 不提供 1080p 以上的转码档；
- 耗时 2.2–2.4 秒（media-parser 文档称约 200ms），离它设置的 4 秒超时只剩不到 2 秒；
- `play_addr.uri` 与 open API 相同，所以 2.9 的 `ratio=default` 原片 feed 也能拿到，但 open API 同样能，feed 没有独有优势。

限制：

- **画质最高 720p**，档位也少得多，远不如 open API 和页面方案（都能拿到 2K）；
- App 协议的多数接口要求 `X-Gorgon` / `X-Argus` 等签名，这个接口目前不查，随时可能补上；
- 图集类作品不支持（media-parser 让图集改走网页接口）；
- 未在海外、电信网络下测过。

所以它只适合做最后兜底，排在页面方案之后。

### 2.9 上传原片 ratio=default（已接入为 original 格式）

`/aweme/v1/play/?video_id=<uri>&ratio=<ratio>`（`www.iesdouyin.com` 与 `aweme.snssdk.com` 结果相同）按 `ratio` 返回不同文件：

| `ratio` | 实际得到 |
| --- | --- |
| `1080p`、`1440p`、`2k`、`2160p`、`4k` | 全部落到同一个 1080p 转码档（即 open API 的 `normal_1080_0`，H.264 1920x1080） |
| `default` | **上传原片**，未经转码 |

原片实测（`uri` 取 `aweme_detail.video.play_addr.uri` 或 RENDER_DATA `videoDetail.video.uri`，两者相同）：

| 视频 | 原片 | 同视频最高转码档 |
| --- | --- | --- |
| 7686096925641338809 | H.264 2560x1440 60fps，21.3 Mbps，191.6MB（MP4） | HEVC 2560x1440，2.65 Mbps，23.6MB |
| 7686432847778982833 | HEVC 2560x1440 60fps，14.7 Mbps，122MB（QuickTime） | HEVC 2560x1440，1.78 Mbps，14.6MB |
| 6961737553342991651（老视频） | HEVC 1080x1920 30fps，6.9 Mbps，17.3MB（QuickTime，iPhone 直传） | H.264 1080x1920 |

- 编码与容器随上传文件而定（3 个样本中 2 个是 QuickTime），`moov` 都在文件尾（不是 faststart）；
- 抽帧目视都没有抖音 logo 或抖音号水印（只看了首帧与少量中间帧，片尾未查）；
- 直连与海外代理都能下载（Range 返回 206，总大小一致，头尾字节 md5 相同），海外代理下很慢；
- 实现：`DouyinIE._build_douyin_original_format` 生成 `format_id='original'`，宽高取 `video.width/height`（显示方向，竖屏为 1080x1920），
  `vcodec` / `acodec` / 大小未知；`quality=0` 且 `preference` 与所在路径的转码档持平（API 为 -1，页面方案为 -2），
  因此默认选择仍是最高转码档，`-f worst` 仍是水印版 download，只有 `-f original` 会选中它。
  最初用 `preference=-2` 时 API 路径的 `-f worst` 选中了体积最大的原片，已修正。
- 图文作品的 `play_addr.uri` 是配乐 mp3 的完整 URL，不是视频 ID；作品带 `images`、或 `uri` 含 `://` / 以 `.mp3` 结尾时不生成原片
  （最初版本会拼出无效地址并被默认选中，下载报 `Did not get any data blocks`，已修正）。

### 2.10 短链与分享链接（已接入）

- 短链跳转链：`v.douyin.com/<code>/` 302 → `www.iesdouyin.com/share/video/<id>/?region=CN&mid=...&u_code=...&did=...`
  302（桌面 UA）→ `www.douyin.com/video/<id>?previous_page=app_code_link`；
  最后一跳对 HEAD 返回 404、对 GET 返回 200，所以解析时放行 404 / 405，只取最终地址，不下载页面；
- 失效或错误的短码 302 到 `https://www.douyin.com` 首页；指向西瓜视频的短链跳到 `www.iesdouyin.com/xg/video/<id>/`；
  图集指向 `/share/note/`、`/share/slides/`：这些都给出明确报错；
- 分享口令里短链后面常粘着 `/3.05` 之类的尾巴，只取短码重新拼接；
- 分享链接带着分享者参数（`u_code`、`did`、`iid`、`share_sign`、`utm_*`），会进 `--embed-metadata` 的 `purl`/`comment`，
  所以 `webpage_url` 统一为 `https://www.douyin.com/video/<id>`；`_match_id` 覆写为返回 `id` / `modal_id` / `share_id`，
  分享链接可在联网前被 `--download-archive` 跳过（短链必须联网才知道 ID）。

### 2.11 iesdouyin 分享页 SSR（未接入，仅实测）

`https://www.iesdouyin.com/share/video/<id>` 是 App 分享出去的页面（media-parser 的第 2 顺位通道）：

- **必须用移动 UA**（Android、iPhone 都可以）：桌面 UA 会 302 到 `www.douyin.com/video/<id>?previous_page=app_code_link`，落到 JS 挑战页；
- 移动 UA 下**不需要 `__ac` 签名、`a_bogus`、`msToken`**，页面直接内嵌 `_ROUTER_DATA` → `videoInfoRes.item_list`；
- **但必须带服务端签发的有效 `ttwid`**：不带 Cookie、带伪造签名段的 `ttwid` 都只拿到没有 `videoInfoRes` 的空壳（约 32KB）；
  一个 2024-08 签发的旧 `ttwid` 仍然有效（说明校验签名而非新鲜度，单样本）；分享页本身对无 Cookie 请求约 5/7 次会下发 `ttwid`；
- 数据少：`bit_rate` 为 null、没有 `download_addr`，`play_addr` 只有一条 `playwm ... ratio=720p`（H.264 1280x720，抽帧未见抖音 logo）；
  但带着 `uri`，可以用 2.9 的 `ratio=default` 拿原片；
- 响应里有 `is_oversea` 字段（国内为 0），海外表现未测；
- 结论：门槛比精选页低（免 `__ac`），数据比精选页少（没有码率阶梯），可作为页面方案失效时的备选。

------

## 3. 排查方法

### 3.1 日志原因对照

| 日志 | 含义 |
| --- | --- |
| `Douyin open API failed: HTTP 403: Blocked by ArgusSecurityPlugin ...` | open.douyin.com 来源也被纳入 Argus 校验，绕过方式失效 |
| `Douyin web API failed: HTTP 403: Blocked by ArgusSecurityPlugin Uifid Not Found` | 当前 IP 被 Argus 拦截（按 IP 而定，海外、国内都有） |
| `Douyin web API failed: HTTP 200 with empty body (ttwid missing or not accepted)` | `ttwid` 缺失或无效（`a_bogus` 目前不校验） |
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

- 测试时优先走测试代理（出口多为巴西），避免本机 IP 被标记；web API 在代理 IP 上多半被拦（按 IP 而定），
  要稳定测 web API 用国内直连（移动网络）。
  要单独测 web API 或页面方案，把前面的策略 monkeypatch 成失败：
  `DouyinIE._fetch_douyin_open_detail = lambda self, vid: (None, 'forced off')`。
- 同一视频两次请求返回的格式数会波动（web API 69↔78、页面 12↔15、TikTok 的 audio 格式时有时无），
  新旧代码对比要背靠背跑，且只比两边共有格式的元数据。
- 用 `--test` 实际下载（只下 10KB），只列格式发现不了下载阶段的 403。
- 代理每个请求 4–6 秒，页面方案一个场景要几十秒，批量用例放后台跑，Python 加 `-u` 避免超时丢输出。
- 签名类算法重构时，用固定输入生成基准输出做逐字回归（`__ac_signature` 用了 204 组）。
- 验证 ECS 选节点时注意：`dns.alidns.com` 带 `edns_client_subnet` 查到的是另一层 CDN
  （`via` 形如 `...jswuxi-ct53-bm.Creative`），在那里连 open API 都返回空，不能代表实际访问的节点。
- **确认跑的是源码**：Mac 的 `.venv` 的 site-packages 里另装有一份旧版 `yt_dlp`，在仓库根目录以外执行
  `.venv/bin/python -m yt_dlp` 会悄悄用旧代码。验证时 cd 到仓库根目录或设 `PYTHONPATH`，以 `-v` 日志出现 `[debug] Git HEAD:` 为准。
  Windows 下载机同理：它从 `.venv\Lib\site-packages` 导入已安装的 yt_dlp，更新代码后要重新安装才生效。
- 格式选择改动（如新增格式）要回归 `-f worst` / `wv*` 等，不只看默认选择：可以把保存的 `aweme_detail` / 精选页 HTML
  喂给 `_real_extract`（monkeypatch 网络函数）离线比对新旧选择结果。

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
| media-parser 抖音解析器 | [docs/parsers/douyin.md](https://github.com/ucmao/media-parser/blob/main/docs/parsers/douyin.md)、`src/parsers/douyin_parser.py`（对比时为 `0b751170a`） | 通道：App feed → iesdouyin 分享页 SSR → web API（配置了 `UIFID` 才签 webSign）→ SSR；覆盖图集（取 `url_list[-1]` 无水印原图）、LivePhoto（称只有 web API 下发 `images[i].video`）、音乐、合集、放映厅 / 短剧、AI 字幕（`cla_info.caption_infos`）、短链与分享口令解析；Cookie 清洗（剔除 `bd_ticket_guard*`、`__security*`、`fpk*` 等，称可防 `Signature Not Found`；称普通作品带 `verify_` 开头的 `s_v_web_id` 会 403，未核实）。实测它给用户的视频是 HEVC 720p（2.8）；文档有几处与代码不符（排序、放映厅接口） | 扩展图集、LivePhoto、音乐、合集，或排查 Cookie 相关 403 时 |
| DouYin_Spider | [cv-cat/DouYin_Spider](https://github.com/cv-cat/DouYin_Spider) `utils/secsdk_web_sign.py` | `x-secsdk-web-signature` 纯算实现的出处（2026-08-30），附逆向路径、规范化规则、受保护接口清单（`aweme/detail`、`aweme/post`、`aweme/favorite`、`mix/aweme`、`tab/feed` 等） | 实现 webSign，或它失效时 |
| nous-app PR #2360 | [iocrazy/nous-app#2360](https://github.com/iocrazy/nous-app/pull/2360) | 实现 Argus webSign：`x-secsdk-web-signature`，Node 子进程跑 `websign_env.js` / `websign_runtime.js`，先 `a_bogus` 再 webSign，输入为带 `a_bogus` 的 URL、时间戳、`uifid`（实际上纯算即可，见 2.1 与 DouYin_Spider） | 纯算失效、需要跑真实 SDK 时 |
| amagi PR #188 | [ikenxuan/amagi#188](https://github.com/ikenxuan/amagi/pull/188) | ① 免鉴权接口：`iesdouyin.com/web/api/v2/user/info/`（用户名转 `sec_uid`）、`/v2/music/info/`、`/v2/music/list/aweme/`、`api.amemv.com/aweme/v1/im/resources/emoji/`；② `webid` 与会话 Cookie 不匹配时服务端静默返回 200 空响应，改为读响应头 `cookie_ttwidinfo_webid` 按 `ttwid` 缓存；③ `x-secsdk-web-signature` 为 32 位小写 hex、放在 query 中，只对 SDK 策略表中的部分路径生效，必须是最后一步；④ Argus 拦截时重新生成 `msToken` / `verifyFp` / `a_bogus` 线性退避重试最多 5 次 | 扩展到用户主页、音乐等接口，或遇到莫名 200 空响应时 |
| douyin-downloader | [jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader) | 说明 Argus 对非浏览器请求返回 `Uifid Not Found`，并称 webSign 只能在真实页面内生成（已被 DouYin_Spider 的纯算实现与我们的实测推翻）；主页批量翻页被拦时用 Playwright 启动真实浏览器滚动采集（默认有头，需手动过验证码） | 需要浏览器兜底方案时 |
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

   后来发现同为巴西住宅 IP 也是有的拦、有的不拦（2.1），更支持「按客户端 IP 判断」，但尚未排除节点因素。

   区分方法：在电信机器上 `nslookup www.douyin.com` 拿到它实际访问的节点 IP，
   从移动网络用 `curl --resolve www.douyin.com:443:<IP>` 把 web API 请求发到该节点（先确认 `via` 是 `CHN-...-CT5-...` 这类节点）。
   也被拦就是按节点，能过就是按客户端 IP。
2. **open API 被堵后的下一步**：页面方案能拿到 2K，仍是首选兜底；
   要恢复高画质 API 通道，在 web API 上实现 webSign（2.1 已实测可行，纯算、工作量小），
   `uifid` 取浏览器 Cookie 里的 `UIFID` 或页面方案顺带拿到的 `UIFID_TEMP`；待查：`UIFID_TEMP` 取不到的原因、复用期限、
   盐常量的长期稳定性。iesdouyin 分享页（2.11）可作为免 `__ac` 的页面备选；App feed 实测最高 720p，只作最后兜底（2.8）。
3. **其他 `filter_reason`**：遇到已删除、私密、地区限制的视频时，补充对应的 `filter_reason` 取值和页面表现。
4. **fork 尚不支持的内容**：图集（`/note/`、`/slides/`）、LivePhoto、音乐、合集、放映厅 / 短剧。
   open API 对图集是否返回 `images` 与 LivePhoto 的 `images[i].video` 未测，可用 media-parser 样本清单里的真实作品 ID 测。
5. **疑点（只读代码，未实测）**：
   - 字幕：`DouyinIE` 沿用 TikTok 的 `cla_info` 字段名（`lang` / `Format`），可能与抖音实际字段不符；
     找不到字幕且有作者名时会用 TikTok 的 `_create_url` 去请求 tiktok.com 页面（只在 `--write-subs` / `--list-subs` 时触发）；
   - fork 自己生成 `verify_` 开头的 `s_v_web_id`，与 media-parser「普通作品带它会 403」的说法相反，需要 A/B；
   - 用户 `--cookies-from-browser` 带入的 `bd_ticket_guard*` 等字段是否影响 web API（media-parser 称会触发 `Signature Not Found`）。
6. **原片**：`ratio=default` 是否总是原片、长期是否可用，只在 3 个视频上验证过；ext 固定为 mp4，而原片可能是 QuickTime 容器。
4. **`webid`**：目前两个 API 策略都不带 `webid`；将来若加上，注意 amagi 发现的「`webid` 与会话不匹配则 200 空响应」。
