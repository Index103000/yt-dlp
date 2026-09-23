# 抖音 / TikTok 风控调研记录

- 调研时间：2026-09-21；2026-09-22 补充 Argus 签名实测、上传原片、短链与分享链接、与 media-parser 的对比，
  同日实现 web API 签名（改为第一级）、原片探测，并调研 TikTok 原片；当晚策略改名为 `signed_web_api` / `embed_origin_api` /
  `ssr_render_data`，原片改为 `original=true` 时才列出、按分辨率排序并默认探测；2026-09-23 修正 API 路径水印版 `download_addr` 的排序与宽高
- 适用分支：`tiktok`
- 对应代码：`yt_dlp/extractor/tiktok.py`（`DouyinIE`、`TikTokBaseIE._extract_web_formats`）、`yt_dlp/extractor/tiktok_utils/douyin/*`

本文记录实测结论和证据，以及调研时参考过的外部项目。风控随时会变，这里的结论都有时效性：
出问题时先按「3. 排查方法」复测，再对照本文判断是哪一层变了；外部项目的新方案见「4. 参考项目」。

------

## 1. 现状速览

`DouyinIE._real_extract` 默认依次尝试 `signed_web_api` → `embed_origin_api` → `ssr_render_data` 三级策略
（`--extractor-args "douyin:strategies=..."` 可调整顺序或只用其中几条），
除最后一级外每级失败都打印一条带原因的 WARNING（`Douyin <策略名> failed: <原因>`），全部失败时最终报错汇总各级原因。
API 响应带 `filter_reason`（视频不存在等）时直接报错，不再尝试后续策略。

| 策略 | 请求 | 依赖 | 海外 IP | 国内移动 | 国内电信 |
| --- | --- | --- | --- | --- | --- |
| 1. `signed_web_api` | `aweme/v1/web/aweme/detail/`，`www.douyin.com` 来源 + `a_bogus`；被 Argus 拦截时加 `uifid` + `x-secsdk-web-signature` | `ttwid`；拦截时要有效 `uifid` | ✅（拦截 IP 靠签名，2.1） | ✅ 不签名即可 | ✅ 靠签名（2026-09-22 实测：未签名被拦 → 精选页取 `UIFID_TEMP` → 签名 200）；未签名时 5/5 被拦 |
| 2. `embed_origin_api` | 同一接口，`Origin` 为 `https://open.douyin.com`，只带 `aweme_id` + `aid` | 无 | ✅ | ✅ | ✅ |
| 3. `ssr_render_data` | 精选页 `jingxuan?modal_id=` 服务端渲染（SSR）的 `RENDER_DATA` | `__ac_nonce` + `__ac_signature` | ✅ | ✅ | ✅ |

`signed_web_api` 排第一，是因为 `embed_origin_api` 利用的是官方嵌入播放器的 `Origin` 白名单（2.1），随时可能被堵；
`signed_web_api` 走的是网页正常接口，被拦截时按服务端的真实校验规则签名，不依赖这个口子。
代价是在拦截 IP 上第一个视频要 5 个请求（2.1），`embed_origin_api` 只要 1 个。

下文的叫法与策略名的对应：「web API」= `signed_web_api` 请求的网页端接口；正文里的 `embed_origin_api` 即 2.1「绕过方式：open.douyin.com 来源」；
「SSR 方案」= `ssr_render_data`；「精选页」指 `jingxuan?modal_id=` 页面本身，`ssr_render_data` 从它取 `RENDER_DATA`，
`signed_web_api` 被拦截时也从它取 `UIFID_TEMP`（同一次提取只请求一次）。
这三个策略在 2026-09-22 下午引入 `strategies` 参数时（提交 `ad9830bda`）叫 `web` / `open` / `webpage`，当晚改为现名，旧名现在会报错。

extractor-args（`--extractor-args "douyin:<键>=<值>"`）：

| 键 | 取值 | 作用 |
| --- | --- | --- |
| `strategies` | `signed_web_api`、`embed_origin_api`、`ssr_render_data` 的有序组合，默认三者按此顺序 | 调整顺序或只用其中几条；空值按默认，重复会去重，未知名字（含旧名）报错 |
| `original` | `false`（默认）/ `true` | 列出上传原片 `original`：按分辨率归档、排在同档转码档之上，因此成为默认选择，见 2.9 |
| `original_probe` | `true`（默认）/ `false` | 列出原片时先探测，补全编码、大小、容器并去掉「假原片」；关闭后若输出 info JSON（`--write-info-json` / `-j` / `-J`）会警告信息不全 |

Python API 里每个值都要写成字符串列表，如 `{'douyin': {'original': ['true']}}`；写成字符串会报错（yt-dlp 会把字符串拆成单个字符）。
值不区分大小写。

- 海外 IP：测试代理，出口为巴西住宅 IP，前后换过十几个 IP。
- 国内移动：Mac mini，江苏移动家宽，抖音节点 `CHN-JSlianyungang-AREACMCC5`。
- 国内电信：Windows 下载机，江苏徐州电信家宽，抖音节点 `CHN-JSlianyungang-CT5`。
- Argus 拦不拦按 IP 而定：同为巴西住宅 IP，有的被拦、有的不拦。国内两列各只有一台机器的数据，见「5. 待验证」。

下载视频时，转码档必须带 `Referer: https://www.douyin.com/`（2.4），上传原片则必须不带（2.9）。

- 支持的链接：`www.douyin.com/video/<id>`、任意路径上的 `?modal_id=<id>`、`iesdouyin.com` / `m.douyin.com` 的 `/share/video/<id>`、
  `v.douyin.com/<code>` 短链（见 2.10）。图集、音乐、合集等不支持。`webpage_url` 统一为 `https://www.douyin.com/video/<id>`。
- `original=true` 时额外列出上传原片 `original` 并默认选中它，见 2.9。
- 水印版在三条策略下都排在所有转码档之下（`preference=-2`），`-f worst` 选中它；格式 ID 在 API 两级是 `download_addr`
  （多镜像时 `download_addr-0/1/2`）、在 SSR 方案是 `download`，排除它要写 `[format_id!^=download]`，见 2.12。
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
另外 2 个上 web API 和不带签名的精简请求都正常返回；后续几轮分别为 6 个中 4 个、11 个中 4 个、14 个中 12 个、6 个中 5 个被拦
（出口也有土耳其、叙利亚、美国节点）。按什么判断（IP 信誉、ASN、概率）未知；同一拦截 IP 几分钟的测试窗口内拦截状态没有变化。

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
- `uifid` 的有效来源只有两种，都无法自己合成：
  - 浏览器里的 `UIFID` Cookie（320 位 hex，页面 JS 计算），`--cookies-from-browser` 会带来；
  - 服务端下发的 `UIFID_TEMP`（160 位 hex，偶见 192 位）：
    - www.douyin.com 的**任意真实页面**都会下发（精选页、`/video/<id>`、`/user/<sec_uid>`、带 `__ac` 的首页），挑战页不下发；
      短链解析时 HEAD `www.douyin.com/video/<id>`（返回 404）也会顺带下发，但它在拦截 IP 上是否有效未验证；
    - **请求里已带着 `UIFID_TEMP` 或 `UIFID`（哪怕是无效值）时，服务端不再下发**；
    - 不绑定 IP、UA、ttwid、视频，实测跨视频、跨 IP、跨 UA、至少约 24 小时可复用，Cookie 有效期到 2028 年；
    - 前 64 位由 UA 决定、后 96 位每个会话不同且与前缀绑定：保留前缀、后缀随机或拼接不同会话的前后缀都是 `Validate Error`
      （只改首位或末位 1 个字符仍能通过，但改中间就不行，只能原样使用下发的值）；
    - 同一响应还下发 `web_sign_token`（HS256 JWT，内容为 `{v, id=匿名用户 id, d, s, ts}`，不含 uifid），不参与签名。
  - 此前「独立复核在 4 个拦截 IP 中有 2 个取不到 `UIFID_TEMP`」，后来查明至少有一类原因：有的出口上首页不下发 `__ac_nonce`
    （响应头 `X-TT-System-Error: 3`），精选页于是返回挑战页；但挑战页本身会下发 `__ac_nonce`，按它重算签名再请求一次即可拿到真实页面。

**fork 的实现**（`DouyinIE._fetch_douyin_web_detail`、`tiktok_utils/douyin/websign.py`、`cookies.py` 的 `get/store/forget_douyin_uifid`）：

- `uifid` 依次取：cookiejar 的 `UIFID` → `UIFID_TEMP` → yt-dlp 缓存（`<cache-dir>/douyin/uifid.json`，`--no-cache-dir` 时不读不写）。
  有 `uifid` 就一律签名（未拦截的 IP 上签名请求同样 200，随机 uifid 加随机签名也不影响）；没有时先发不签名请求，
  未拦截网络上不多发请求。签名在 `a_bogus` 之后对最终 query 计算，URL 原样交给 `_download_webpage_handle`（不传 `query=`，避免重新编码）。
- 按 403 原文处理：`Uifid Not Found`（没有 uifid）→ 从精选页取 `UIFID_TEMP` 后签名重试；`Validate Error` → 该值记为无效，
  `UIFID_TEMP` / 缓存里的同值一并作废，换下一个候选值，候选用尽再从精选页取（取之前清掉 `UIFID_TEMP` 与已判无效的 `UIFID`，
  否则服务端不下发）；`Sign Invalid` / `Signature Not Found` / 空响应等不重试。每个候选值最多试一次、页面最多请求一次，必然终止。
- 精选页（`_download_douyin_webpage`）：本次提取内只请求一次，成功的响应留给 SSR 方案复用；网络失败不缓存；
  返回挑战页且下发了新 `__ac_nonce` 时，重算 `__ac_signature` 再请求一次；页面新下发的 `UIFID_TEMP` 写入缓存。
- 请求数（实测）：拦截 IP、空缓存、无 Cookie 的第一个视频 5 个（ttwid 注册、不签名 403、首页取 `__ac_nonce`、精选页、签名 200）；
  同一批次后续视频 1 个（签名）；新进程用缓存 2 个（ttwid、签名）；未拦截 IP 2 个（ttwid、不签名 200）。
- 签名请求里 `uifid` 在 query 中；cookiejar 里有 `UIFID_TEMP` / `UIFID` 时它们也会作为 Cookie 发出，实测无影响（只放 query 同样 200）。

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
- 这是利用一个未被纳入校验的来源，随时可能被堵，所以默认排在 web API 之后作兜底（2026-09-22 起；此前是第一级）。

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
  `X-Bogus` 在 f2 里有实现（`f2/utils/xbogus.py`，未验证是否仍可用），想保住这条兜底时可以考虑补上；主通道 web API 不受影响。

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

### 2.3 __ac_nonce / __ac_signature（byted_acrawler，SSR 方案）

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
- `_parse_aweme_video_app` 与 TikTok 共用，所以在 `DouyinIE._build_douyin_api_info` 里补 `http_headers: {'Referer': 'https://www.douyin.com/'}`。
- 上传原片走的冷存储节点规则相反（`v96-hcc` 带 Referer 即 403），原片格式单独覆盖为不带 Referer，见 2.9。
- 只 `-F` 列格式发现不了这类问题，要用 `--test` 实际下载。

### 2.5 视频不存在 / 被过滤

- 两个 API 都返回 `200`，`aweme_detail` 为 null，附 `filter_detail`：`{"filter_reason": "core_dep", "detail_msg": "", "notice": "", ...}`；
- SSR 方案有 `RENDER_DATA`，但 `app.videoDetail` 为 null；
- 所以 API 响应带 `filter_reason` 就直接报错 `Douyin video is unavailable (status_code=0, filter_reason=core_dep)`。
- iesdouyin 分享页（2.11）对不存在的作品返回 `item_list=[]`，`filter_list[0].filter_reason='SYSTEM_ITEM_NOT_EXIST'`。
- 实测到的另一个取值：`music_deleted_video`（6950251282489675042）；另有一个视频（7422307345595731236）经巴西代理返回
  `aweme_detail: null` 且 `filter_reason` 为空字符串，原因不明（此时不会触发「直接报错」，会继续尝试后续策略）。
- 其他 `filter_reason`（已删除、私密、地区限制等）尚未遇到实例；media-parser 的注释和文档提到
  `status_self_see`、`status_deleted`、`status_part_see`，但其仓库里没有样本，未核实。

### 2.6 SSR 方案的页面特征

- 只有精选页 `https://www.douyin.com/jingxuan?modal_id=<id>` 的 SSR 在 `RENDER_DATA` 中返回 `app.videoDetail`，其他页面没有；
- JS 挑战页：含 `byted_acrawler.sign`；
- 验证码中间页：`<title>验证码中间页</title>`，约 6KB；
- SSR 方案每档码率只取第一个镜像（镜像多了 yt-dlp 也不会自动换，没有实际好处）。

### 2.7 TikTok

- Web hydration 的顶层 `video.size` 描述的是无水印的 playAddr（与 `PlayAddrStruct.DataSize` 一致），
  带水印的 download 实测 665803 字节而 `video.size` 为 658136，所以 download 的兜底大小写 `filesize_approx`。
- `TikTokIE._TESTS` 中多数视频锁区（`Your IP address is blocked from accessing this post`，换巴西 IP 也一样）；
  当时可访问的样本：`pokemonlife22/7059698374567611694`、`hankgreen1/7047596209028074758`，`tatemcrae/7107337212743830830` 仅国内直连（经本机代理）可访问。

**TikTok 拿不到原片**（2026-09-22 调研，4 个可访问视频 + 1 个锁区视频，美国与巴西出口）：

- TikTok 曾有与抖音相同的取法：上游 [yt-dlp#4138](https://github.com/yt-dlp/yt-dlp/issues/4138) 中 2022-06-24 有人指出
  `/aweme/v1/play` 的关键参数是 `ratio=default`；到 2022-08-17 已被堵上：该接口必须带 `file_id`（直接对应某个转码文件）与 `signaturev3`。
  实测只带 `video_id=<uri>&ratio=default`（www.tiktok.com、api16-normal-c-useast1a、api22-normal-c-alisg 等）一律
  `404 {"status_code":5,"status_msg":"Invalid parameters"}`；带 web 签名时 `ratio` 被忽略，返回的仍是 `file_id` 那一档。
- App API 路径（`_extract_aweme_app`，需 `app_info` / `device_id` extractor-arg）现在返回 200 空 body（multi/aweme/detail）或 429（feed），
  失败后回退网页，行为正确。要恢复需真实 device_register 与 X-Gorgon / X-Khronos / X-Argus / X-Ladon 签名，且能否拿到更高档未经证实。
- 网页侧其他接口（`/api/item/detail`、`/player/api/v1/items`、移动 UA 页面、`/embed/v2`）档位都不比 web hydration 多。
- 没看到按地区降码率：美国与巴西出口的 (GearName, DataSize, FileHash) 集合完全一致，只是 CDN 域名不同
  （「美国出口」即本机经 fake-ip 的出口，见 3.3；2026-09-23 的跨项目调研与 XFF 对照见 2.13）。
- 第三方：tikwm 的 `hdplay` / `hd_size` 等于 yt-dlp web 最高档；tikdownloader 的「MP4 HD」（tokcdn `…_original.mp4`）
  有时像上传原片（QuickTime、moov 在尾、码率高出数倍）、有时只是更高的转码档、有时与 web 档相同，同一视频还会随时间变化，
  来源不明且需把链接交给第三方，不集成。web 最高 1080 档都叫 `adapt_lowest_1080_1`，App 端可能有更高档，未证实。
- 默认排序的一个观察（**不改代码**）：yt-dlp 默认同分辨率下按编码优先选 HEVC，而 web 的 HEVC 档码率常只有同分辨率 H.264 档的 1/3。
  hankgreen1 576x1024：默认 HEVC 668k 的 VMAF 92.09，H.264 1895k 为 98.58（以 tokcdn 高码率文件为参考）。
  2026-09-22 曾在 `_parse_aweme_video_web` 设 `'_format_sort_fields': ('quality', 'res', 'size', 'br')`，次日按用户要求撤回：
  排序走 yt-dlp 默认逻辑，由用户在配置里用 `-S`（如 `-S res,size`）决定，extractor 不改默认选择。
- **用户业务中的真实问题（2026-09-23 提出，待复现）**：同一批 TikTok 视频前几天还能下到 1080p，最近只有 540p，即服务端下发的档位变少。
  上游 [#15690](https://github.com/yt-dlp/yt-dlp/issues/15690)（2026-01-26，仍 open）记录了同类现象：非美国 IP（英国、日本代理、其他）
  有时只给 `play` 一档、或缺 `bytevc1_1080p`，且每次运行结果不同；`--xff US` 后多档稳定出现；一位报告者带自己的 Cookie 时不论 IP 都能稳定
  拿到 1080p；`--xff US` 偶发 503 与验证码页；三天后几位报告者都不能复现，说明是间歇性的服务端策略。
  上游 PR [#15710](https://github.com/yt-dlp/yt-dlp/pull/15710)（bashonly，2026-01-27，未合并，作者自认写法不满意）默认带 `X-Forwarded-For` 美国地址，
  失败再去掉重试。本 fork 尚未移植；要先用业务机的样本（视频 ID + 当时的 info.json + 出口 IP）复现，再决定用 `--xff US`、Cookie 还是移植 #15710。
  本文 2.7 开头「美国与巴西出口档位一致」是 2026-09-22 对 4 个视频的一次观察，与 #15690 的间歇性并不矛盾。

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

| 视频 | 返回 | 目标位置 | `bit_rate` 档数 | 最高画质 | embed_origin_api 对照 |
| --- | --- | --- | --- | --- | --- |
| 7686096925641338809 | 7 个视频 | 第 1 个 | 7 | 1280x720 | 29 档，最高 2560x1440 |
| 6961737553342991651（老视频） | 7 个视频 | 第 1 个 | 2 | 720x1280 | — |

2026-09-22 按 media-parser 原样复现（国内直连，IPv4 / IPv6 两个出口结果相同）：

- feed 的 `bit_rate` 全部是 H.265（`is_h265=1`），media-parser 的「H.264 优先」落空，它最终交给用户的是 **HEVC 1280x720**
  （809 为 8.5MB）；feed 自带的 `play_addr_h264` 更低（809 为 1024x576）；
- `video.width/height` 写着 2560x1440，但 feed 不提供 1080p 以上的转码档；
- 耗时 2.2–2.4 秒（media-parser 文档称约 200ms），离它设置的 4 秒超时只剩不到 2 秒；
- `play_addr.uri` 与 embed_origin_api 相同（8/8），所以 2.9 的 `ratio=default` 原片 feed 也能拿到，但 web API、embed_origin_api、SSR 方案同样能，feed 没有独有优势。

限制：

- **画质最高 720p**，档位也少得多，远不如 embed_origin_api 和 SSR 方案（都能拿到 2K）；
- App 协议的多数接口要求 `X-Gorgon` / `X-Argus` 等签名，这个接口目前不查，随时可能补上；
- 图集类作品不支持（media-parser 让图集改走网页接口）；
- 未在海外、电信网络下测过。

所以它只适合做最后兜底，排在 SSR 方案之后。

### 2.9 上传原片 ratio=default（已接入为 original 格式）

`/aweme/v1/play/?video_id=<uri>&ratio=<ratio>`（`www.iesdouyin.com` 与 `aweme.snssdk.com` 结果相同）按 `ratio` 返回不同文件：

| `ratio` | 实际得到 |
| --- | --- |
| `1080p`、`1440p`、`2k`、`2160p`、`4k` | 落到同一个转码档：有 1080 档时为 `normal_1080_0`（H.264 1920x1080），没有时更低（6982497745948921092 落到 540 档） |
| `default` | **上传原片**，未经转码；原片已被清理的老视频会悄悄返回最高转码档（见下） |

原片是否最好（2026-09-22，17 个可用视频 + 复核补测 4K；`uri` 取 `aweme_detail.video.play_addr.uri` 或 RENDER_DATA `videoDetail.video.uri`，
与 App feed、iesdouyin 分享页给出的也相同，所以原片不依赖 embed_origin_api）：

- 16/17 是真正的上传原片：显示分辨率与 fps 都不低于任何转码档（2 个 2021 年老视频的原片分辨率严格更高），
  平均码率是码率最高转码档的 2.8–10.3 倍（中位数约 6.15）；没有发现转码档分辨率或 fps 高于原片（没看到超分 / 插帧档）。
  4K 也一样：7684232876221431226 原片 HEVC 2160x3840、46.2 Mbps，是最高转码档 `adapt_lowest_4_1`（5.2 Mbps）的 8.85 倍。
- 1/17 回退：6982497745948921092（2021 年）`ratio=default` 返回的就是 `normal_720_0`（对象 ID 与大小都相同），画质不比最高转码档差，只是名不副实。
  2021 年的 4 个视频里 1 个回退，今年的视频 13/13 都拿到原片；回退的规律不清楚。
- 编码与容器随上传文件而定：HEVC 12 / H.264 4；QuickTime 13 / isom 3；1 个是 HEVC Main 10 HLG 的 10bit HDR（其转码档全是 SDR）。
  `moov` 都在文件尾。CDN 返回的 Content-Type 不可靠（qt 文件会返回 `video/mp4` 甚至 `application/octet-stream`）。
- 原片多带剪辑器标签，其中 4 个标了 `te_is_reencode=1`，推测是 App 导出时重新编码过的文件，不是相机原始文件（未核实）；无论如何都是服务端能拿到的最高画质。
- 抽帧目视都没有抖音 logo 或抖音号水印（只看了少量帧，片尾未查）。
- 下载前能知道什么：`aweme_detail` 里没有任何字段给出原片的码率、编码、大小、fps；`video.width/height` 在 16/16 个真原片上等于原片显示分辨率
  （回退视频上不对）；`video.duration` 配合文件大小估算码率，误差不超过 0.3%。`is_source_HDR`、`ratio`、`format` 都预测不了原片。

**Referer**：原片由 play 端点 302 到 `colds` / `hcc` / `coldx` / `colda` / `cold` 等冷存储节点（每次调度不同）。
2026-09-22 对 6961737553342991651 连续请求：带 `Referer: https://www.douyin.com/` 时落到 `v96-hcc` 的 4/4 次都是 403（响应长 564 字节，
换 `Referer: https://www.iesdouyin.com/`、不带 Range 同样 403），不带 Referer 时 `v96-hcc` 5/5 为 206；其余节点带不带都是 206。
复核时另测了 36 次不带 Referer（覆盖 10 个节点）全部成功，但那次没有调度到 `v96-hcc`。所以原片格式设 `http_headers={}`
覆盖掉 info 级 Referer（yt-dlp 的 `_calc_headers(ChainMap(fmt, info))` 以格式级为准，`--load-info-json` 与外部下载器同样生效；
用户显式 `--referer` / `--add-headers` 仍会加上）。

**实现**（`DouyinIE._add_douyin_original_format`、`_douyin_original_quality`、`_probe_douyin_original`、`tiktok_utils/douyin/mp4probe.py`）：

- 默认不列出；`--extractor-args "douyin:original=true"` 时列出 `format_id='original'`，宽高先取 `video.width/height`、ext 为 mp4。
- 排序：`preference` 与转码档相同（-1）；`quality` 按分辨率归档，取「短边不超过原片的转码档」里最大的 `quality` 再加 0.5
  （转码档的 `quality` 来自 UrlKey 档位名，如 540p → 540；档位名不一定等于实际短边，1024x576 标 540p、320x240 标 360p，所以按实际宽高归档）。
  于是原片排在同分辨率转码档之上、更高档位之下，默认选择就是它（真原片分辨率不低于任何转码档，实际总在最前）；
  `-S` 等用户排序规则排在 `quality` 之前，同样作用于原片；`-f worst` 仍是水印版 download。
  SSR 方案的 `bitRateList` 没有 UrlKey，转码档的 `quality` 按实际短边补上（与 yt-dlp 的 `res` 同值，转码档之间的顺序不变），原片同样按档位归档。
  归档比较留 16px 余量，免得奇数边取整或探测宽高有 1–2px 出入时掉一整档。
- 探测（`original_probe`，默认开）：先发 1 个 `Range: bytes=0-4095`（跟随 302，约 2 个 HTTP 请求），得到精确 `filesize`、
  `tbr`（大小 × 8 / 时长）、`ext`（brand 为 `qt` 时 `mov`）；跳转后的对象 ID 与某转码档相同、或大小等于某档 `data_size` 即判为回退，
  去掉 original（打印 `The original upload is no longer available ...`）。再发 1 个 Range 取文件尾的 moov，纯 Python 解析出
  `vcodec`、`acodec`、`fps`（平均帧率）、`vbr`、`abr`、`dynamic_range`（colr 的 transfer：16 为 HDR10、18 为 HLG；没有 colr 时不下结论）与显示宽高。
- 探测代价（直连 6 个视频、海外代理 3 个）：国内直连整体 0.18–0.61s（旧 `size` 模式只取文件头为 0.17–0.45s）；
  海外代理 9.8–20.0s，大头在第一个请求（302 + CDN 首包，8.2–16.9s），moov 一步只占 1.1–1.9s（复用连接）。
  moov 在 ≤90 秒的样本里为 7–116KB，20 分钟的视频为 1.2MB。两步差别很小，所以不再分 `size` / `full` 两档，开探测就读全。
- 探测失败：第一个请求失败时保留未探测的原片，但 `quality` 降为 0（转码档之下），不作为默认选择，需要时 `-f original`；
  moov 一步失败时保留第一步的结果。两种情况都打印 WARNING，因为 info JSON 里原片的编码等信息会缺。
- `original_probe=false`：不多发请求，但编码、大小、容器未知，识别不了「假原片」，QuickTime 原片会存成 `.mp4`，
  `-S vcodec` / `-S size` 等规则对原片无效；此时若输出 info JSON（`--write-info-json` / `-j` / `-J`；`--embed-info-json` 不在检查范围内），
  整次运行打印一次 WARNING（设置了自定义 logger 时 yt-dlp 不去重，每个视频一次）。
  另外 `best[vcodec!=none]` 这类筛选会排除编码未知的原片（格式筛选里未知值不满足 `!=`，要写 `vcodec!=?none` 才放行）。
- 修正记录：最初 `preference=-2` 使 API 路径的 `-f worst` 选中体积最大的原片；最初未排除图文作品（其 `play_addr.uri` 是配乐 mp3 的完整 URL），
  拼出无效地址并被默认选中；最初继承了 info 级 Referer，调度到 `v96-hcc` 时下载 403。均已修正。

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
- 结论：门槛比精选页低（免 `__ac`），数据比精选页少（没有码率阶梯），可作为 SSR 方案失效时的备选。

### 2.12 水印版 download_addr 的排序（API 路径，2026-09-23 修正）

`signed_web_api` / `embed_origin_api` 的格式由 `_parse_aweme_video_app` 生成（与 TikTok 共用，本地跟踪的上游 master `c7fb478`〔2026-09-16〕代码相同），
其中 `video.download_addr` 是水印版（`format_note` 为 `Download video, watermarked`）。这段代码有两个问题：

- **preference 被覆盖**：调用处传入 `'preference': -2 if has_watermark else -1`，但 `extract_addr` 在展开 `**add_meta` 之后又写了
  `'preference': -100 if is_bytevc2 else -1`，水印版实际为 -1，与转码档同级。默认排序下它没有 `quality`（没有 UrlKey），仍排在最后，
  所以默认选择和 `-f worst` 看不出问题；但 `-S res`、`-S size` 等用户规则排在 `preference` 之后、`quality` 之前，会选中它。
- **宽高是假的**：17 个样本的 `download_addr.width/height` 全是 720x720，与视频实际分辨率（320x240 到 2560x1440）无关；
  代码取宽 720、高按视频宽高比推算（原注释已说明 height 不对），于是 320x240 的 7684908169365914597 被标成 720x540，
  1440x2560 的被标成 720x1280。水印版文件的真实分辨率没有下载核实。

17 个样本里的其他事实：

- `video.has_watermark` 与 `download_addr` 地址里的 `watermark=1` / `0` 17/17 一致。唯一为 `false` 的 7686096925641338809 是 `watermark=0`，
  `data_size` 与 `play_addr` / `normal_1080_0` 完全相同，是无水印转码。
- 16 个水印版里有 7 个的 `data_size` 恰好等于某个 H.264 转码档（`normal_540_0`、`normal_720_0`，7684908169365914597 为 360p 的
  `play_addr_h264`），两者关系未下载核实，不据此推宽高。
- 9 个视频的水印版比所有转码档都大（7686432847778982833：21.9MB，最大转码档 `normal_1080_0` 16.4MB；7615828879340552036：170.8MB 对 157.6MB）。

修正（`DouyinIE._build_douyin_api_info`，不改共用的 `extract_addr`）：`format_id == 'download_addr'` 的格式去掉宽高，
`has_watermark` 为真时设 `preference=-2`（与 SSR 方案、TikTok 网页路径的 `download` 一致），无水印的保持 -1。
TikTok 走 App API 的路径（`_extract_aweme_app`，以及 Sound / Effect / Tag 列表）用的是同一段代码，理论上同样受影响；
其中 `_extract_aweme_app` 目前不可用（2.7），列表类未测，所以没改共用代码。

回归（离线：17 个视频 × 原片 4 种状态〔不列 / 探测 / 不探测 / 探测首请求失败〕× `-S`〔无 / `res` / `+size` / `size` / `vcodec:h264`〕
× `format_sort_force` 开关，共 680 组）：

| 场景 | 改动前 | 改动后 |
| --- | --- | --- |
| 默认排序：默认选择、`b`、`bv*`、`wv*`、`-f worst`（force 开关都一样） | — | 全部不变；`-f worst` 各 68/68 仍是 `download_addr`（7686096925641338809 的是无水印版） |
| `-S res`（force 开关都一样） | 7684908169365914597 选中水印版（标 720x540） | 选 `bytevc1_360p`（320x240）；列原片且探测成功或不探测时选原片 |
| `-S size`（force 关） | 9 个视频选中水印版 `download_addr-2`（如 7686432847778982833 的 21.9MB） | 选最大的转码档（该视频为 16.4MB 的 `normal_1080_0`）；原片探测成功时仍选原片 |
| `-S +size`、`-S vcodec:h264` 的默认选择 | — | 不变 |
| 有用户 `-S` 时（force 关）的 `-f worst` / `wv*` | 按该规则排最后的格式（转码档，或列出时的原片） | 水印版（`preference` 排在用户规则之前），与 SSR 方案一致 |
| `format_sort_force=True` + `-S res` 的 `-f worst` / `wv*` | 最低分辨率的转码档 | 水印版（宽高未知，按最低） |
| `format_sort_force=True` + `-S size` | 上述 9 个视频选中水印版 | **不变**：force 让用户规则越过 `preference`，而水印版确实最大；不篡改 `filesize` 就无法避免，要排除请加 `[format_id!^=download]` |
| `has_watermark=false` 的 7686096925641338809 | — | 全部不变 |

格式筛选：`[format_id!=download]` 只排除 SSR 方案与 TikTok 网页路径的 `download`，匹配不到 API 两级的 `download_addr` / `download_addr-N`
（680 组里 479 组的 `worst[format_id!=download]` 仍选中它）；`[format_id!^=download]` 在各条路径下都能排除（680/680）。

### 2.13 TikTok 下载方案跨项目调研（2026-09-23）

> 方法：4 个调研组（上游 / 通用下载器 / App API 与签名库 / 失效报告与第三方）各自读代码、issue、文档并做少量实测；每组两条关键结论由独立复核组重做一遍。复核不成立的结论在文中明确写「不成立」而不是删掉。
> 所有事实后的括号是来源：仓库路径 / issue 或 PR 号 / 实测记录（scratchpad `mp_work7/<组>/`）。

**先说实测环境的一个硬限定**：本次所有「本机直连」请求都经本机 Clash Verge 的 fake-ip 透明代理出去，出口 179.255.101.208
（ipinfo：Los Angeles / US / AS906 DMIT；TikTok 页面 `biz-context.geoCity.City=Los Angeles`、`vgeo=VGeo-US`、`idc=useast8`、
`app-context.region=US`）。`--proxy ""` 只关掉 yt-dlp 自己的代理设置，绕不过系统级 fake-ip（复核 verify_xff：`dig +short www.tiktok.com` → 198.18.1.91，
进程 `verge-mihomo`）。也就是说，**本次没有一次请求是从电信出口发出的，TikTok 眼里全部是美国 IP**；下文凡写「未在业务机复现」都源于此。
（appapi 复核记录把该出口写作「巴西 IP」，与 ipinfo 和 TikTok 页面字段不符，以后两者为准。）

#### 2.13.0 一句话结论

- 所有能读到代码的开源项目（yt-dlp、gallery-dl、cobalt、TikTok-Api、Evil0ctal v5、JoeanAmier、f2）拿 TikTok 单视频都走
  `__UNIVERSAL_DATA_FOR_REHYDRATION__` 或同源的 `/api/item/detail/`，档位上限与 fork 相同（web 最高 `adapt_lowest_1080_1`），
  没有一个项目内置针对「只给低档 / 只给 play 一档」的处理（各组读码结论 + 本机 3 样本 9 次页面/API 对照，(GearName, DataSize, FileHash) 完全一致）。
- 上游唯一相关代码是 draft PR #15710（默认 `--xff US` 失败回退），代码停在 2026-01-27，对当前 master 3/7 hunk 冲突（复核 verify_upstream）。
- App API 路线在开源侧没有可用实现：无签名请求 3/3 返回 200 空 body + `tt_orcas_res: 1`，v4 feed 429（实测）；公开签名库要么作者自认过时、要么 issue 里空响应无人回复；仍能用的签名都在付费托管服务里。
- 「带登录 Cookie 稳定 1080p」与「tokcdn 高码率来自 App 阶梯高档」两条流传较广的说法，复核均**不成立**（见 2.13.2、2.13.4）。
- 用户业务机的 1080p→540p 现象在本机（美国出口）不可复现；下一步必须拿业务机的页面 dump 与出口信息按 2.13.2.3 的顺序排查，再决定落地方式。

#### 2.13.1 对照表

| 项目 | 最近活动 | 端点 | 签名 / 前置条件 | 画质档（1080p / 无水印 / 原片） | 地区降档处理 | 当前可用性 | 对 fork 的价值 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| yt-dlp 上游 `TikTokIE`（master c7fb478d2，2026-09-16） | tiktok.py 最后 4 个提交都在 2026-08-18/19（#17452 随机头、#17460 hvc1 档、#17459 share URL、#17480 伪装）；`git log c7fb478d2..yt-dlp/master -- tiktok.py` 为空（复核） | 网页 `/@{user}/video/{id}` 的 `__UNIVERSAL_DATA_FOR_REHYDRATION__` → `webapp.video-detail.itemInfo.itemStruct`；App `multi/aweme/detail` 只在给 `app_info` / `device_id` 时尝试 | 无 msToken / X-Bogus / X-Gorgon / X-Argus；三件套：curl_cffi `impersonate=True`、`_generate_blockbuster_headers()` 随机 2–8 个垃圾头（common.py:3966，#17452）、纯 Python sha256 穷举解 WAF 挑战写 `_wafchallengeid` Cookie；App 侧 `X-Argus: ''` | 只解析 `video.bitrateInfo[]`（QUALITIES 360/540/720/1080，540→576 硬映射）、`playAddr`→`play`、`downloadAddr`→水印 `download`、`music.playUrl`；路径以 `/media-video-hvc1/` 结尾的档标 `acodec: none` + `__needs_testing`（#17460）；无原片、无 1080 以上 | 无：未设 `_GEO_COUNTRIES`；用户手动 `--xff` 经框架层 `_initialize_geo_bypass` 对 TikTok 生效（tiktok.py 未禁用 `_GEO_BYPASS`，复核） | 可用；#17604（open）curl_cffi ≥0.16.2 的 chrome-150 目标被整体封；#17393（open）偶发拿不到 hydration，`--xff US` 无效；#13134（open）App API 空响应 | fork 已含 2026-08 全部 TikTok 改动（fork tiktok.py:313-323、682-685）；上游没有可移植的降档修复 |
| yt-dlp PR #15710「Improve consistency of formats via XFF header」（bashonly） | 唯一 commit 8198ae4bd 2026-01-27；2026-08-16 的 updatedAt 只是被打 `site:tiktok` 标签；state OPEN、isDraft、mergeable=CONFLICTING（复核） | 同上游 | 同上游 | 目的：非美 IP 稳定拿到 `bytevc1_1080p` | `_real_extract` 先 `_initialize_geo_bypass({'countries': ['US']})`，`for first_attempt in (True, False)`：status 落在 `_EXPECTED_ERRORS`（-2 验证码页 / 503 / 10101 SysErrPanic）就去掉 XFF 重试 | 未合并；作者自述「Work in progress!」「I strongly dislike ...」；diff 对当前 master 3/7 hunk 失败（challenge/503 部分），`_real_extract` 两个 hunk 可偏移应用（复核） | 唯一针对该现象的代码；只能手工重放两个 hunk |
| yt-dlp 被拒 / 自闭 PR（#15644 embed+oEmbed 兜底、#17437 Referer、#17599 轮换伪装目标、#17548 item_list 重试） | 2026-01-24 关 / 08-18 关（被 #17452 取代）/ 09-01 当天被 NO AI 政策机器人关 / 08-27 open 未审 | #15644：`/embed/v2/{id}`（`__FRONTITY_CONNECT_STATE__`）+ `/oembed?url=` + 3 次重试 | #17437 只加 `Referer`；#17599 只换 curl_cffi 目标 | #15644 未给档位证据；其余不涉及 | 无（#15644 的重试针对随机拦截） | 均未合并 | `x-tt-system-error: 3` + 约 600 字节页面 = 被风控拦截（#15644 正文、#17393 抓包），可作判据；embed 档位不多（fork 2.7 已实测） |
| gallery-dl（mikf） | 1.32.13 2026-09-19；tiktok.py 最后 2026-02-28 | 同网页 hydration；列表 `www.tiktok.com/api/{endpoint}/` 硬编码 aid=1988、region=US、随机 device_id、随机 verifyFp | 同源 JS 挑战求解；requests + Firefox UA，无 TLS 伪装、无随机头 | `bitrateInfo` 按 Width×Height 像素数排序取第一个 URL，不看码率 / 编码（#9328） | 无；10231 只提示「Try downloading with a VPN/proxy」 | 单帖可用；`item_list` 2026-08-30 起失效（#9718 open）；hvc1 无声未处理（#9764 open） | 无；列表接口参数集合可用来核对 `TikTokUserIE._build_web_query` |
| cobalt（imputnet） | 仓库 2026-04-06；tiktok.js 2025-07-05 | `/@i/video/{id}` 页面 hydration | 无签名；`new Cookie({})` 空对象只回写 Set-Cookie；Chrome/138 UA | 默认顶层 `playAddr`（本次样本 = normal_540_0 h264）；`allowH265` 取 `bitrateInfo.find(h265)` 第一个（不按分辨率） | 无 | 本机可用；#1505（open）数据中心 IP 被挑战，PR #1514 换非 Mozilla UA 未合并；#586（open）码率低于 App | 无。旁证：2023-02～2024-05-21 曾用 App feed（`api22-normal-c-alisg.tiktokv.com/aweme/v1/feed/` + iOS UA + `tiktokDeviceInfo`），2024-05-16 失效（#497）后 PR #503 改网页，未再回头（复核修正） |
| Evil0ctal/Douyin_TikTok_Download_API v5 | 5.1.1 2026-09-23；`tiktok_sign.py` 2026-09-11；v5 从空分支重写（#735） | 全 web：`www.tiktok.com/api/item/detail/`、`/api/post/item_list/`、`/api/user/detail/` 等；无 App API（仓库 grep X-Gorgon/X-Argus/device_register 0 命中） | 纯 Python（仅标准库）X-Dynosaur + msToken + `X-Bogus=1` + X-Gnarly，从 webmssdk 2.0.0.561 字节码 VM 读出；`device_id` 必带（随机 19 位即可，缺则 200 空 + `tt_orcas_res: 1`）；msToken 只用真值否则留空 | `bitrateInfo` 每档转 VideoStream，不选档；宽高一律取顶层 `video.width/height` | identity 绑定代理，tz / language 按代理 GeoIP 对齐；query `region` / `priority_region` 默认 US；文档「Region-locked content → Try an identity on a proxy in a different country」 | 可用（本机复核：无 Cookie、空 msToken、随机 device_id，3 样本 3/3 拿到完整 bitrateInfo） | 第二数据源候选；风控判据（`tt_orcas_res`、200 空、statusCode 10000） |
| Evil0ctal v4（冻结分支） | 分支最后提交 2025-10-12；`crawlers/tiktok/app` 最后 2024-11-17 | `api22-normal-c-alisg.tiktokv.com/aweme/v1/feed/`，写死 iid / device_id / musical_ly 300904，`x-ladon: "Hello From Evil0ctal!"` | 无签名 | 若可用返回 App `bit_rate` 列表 | 无 | 本机 3 次（原配方 / cobalt 旧配方 / api16 域名）均 429 `ratelimit triggered`（Server: TLB） | 否定 App 无签名路径；2024-05 死、2024-09-14 靠任意 `x-ladon` 复活、现又 429，说明 feed 校验强度在变 |
| JoeanAmier/TikTokDownloader | 473c90ff70 2026-09-16；5.8（2026-09-13）「新增 X-Gnarly」 | `/api/item/detail/?itemId=`；备选 tikwm `hd=1` 取 `hdplay` | 改编 Evil0ctal 签名器（文件头声明，GPL-3.0）；默认 `device_id: ""`、`user_is_login: "true"`、`region: US` + `tz_name: Asia/Shanghai` 写死；要求 `cookie_tiktok`，缺 msToken 只 warning | `bitrateInfo` 按 (max(宽,高), Bitrate, DataSize) 升序取最后一个 | 无；仅 `proxy_tiktok` 开关 | 作者 2026-04-20「TikTok 平台功能已失效」（#733），2026-09-08 仍有失效报告；5.8 后无新失效 issue；本机用其 `tiktok_sign.py` 无 Cookie 可用 | 517 行单文件签名器已验证可用；许可证以 Evil0ctal 原件（Apache-2.0）为准 |
| f2（Johnserf-Seed，TikTok 模块） | main 2025-10-12；开发分支 v0.0.1.8-pw3 2026-09-12 | `/api/item/detail/` 等 web API | X-Bogus 纯算，无 X-Gnarly；msToken 由 `mssdk-sg.tiktok.com/web/common` 取，校验长度 148（main）/ 152（pw3）；必须带 Cookie（`kwargs["cookie"]`） | 不选档，只下载 `video_playAddr` | 只写 query `region: SG` / `priority_region` | 不可用：端点现返 168 位（复核 3 种 UA 一致）→ main 仅 `import f2.apps.tiktok.utils` 即抛 `APIResponseError`，pw3 建任何带 msToken 的请求模型即抛 | 仅 msToken / ttwid / odin_tt / device_id 四个免签名获取端点可参考 |
| TikTok-Api（davidteather） | 2026-08-24（仅链接）；v7.3.3 2026-04-01 | Playwright 真浏览器内 `/api/*`；`video.info()` 页面 hydration | 页面内真 SDK `byted_acrawler.frontierSign` 只出 X-Bogus | `bytes()` 只取 `downloadAddr or playAddr`，不读 bitrateInfo | `region: US` 写死；代理轮换 | 列表因缺 X-Gnarly 返回 200 空（#1311，2026-07-18 open 无回复） | 无 |
| descargarbot/tiktok-video-scraper-mobile v2 / v3 | 2026-08-19；ids.json 2025-07-25 | `api16-normal-c-useast1a.tiktokv.com/aweme/v1/multi/aweme/detail/`（与 fork `_extract_aweme_app` 同路线） | v2：`x-argus: ""` + 1389 对真实 iid / device_id 池；v3：SignerPy | 只取 `bit_rate[0]` | 无 | v2：随机 3 对 3/3 → 200、0 字节、`tt_orcas_res: 1`；v3 自标「NO LONGER WORKING」 | 证明换别人注册过的真实设备也无用，服务端查签名 |
| App 签名库群：SignerPy、iqbalmh18/tiktok-signer、tr4cex/TikTok-Encryption、7452323/douyin-sign、code-root/tiktok-android-signing-toolkit、GTACAT、aionotaio、hoanglong、huuhuybn/X-Gorgon | 2025-11-09 / 2026-08-08 / 2026-03-23 / 2026-07-09 / 2026-03-24 / 2025-07-22 / 2026-05-17 / 2026-08-25 / 2026-03-29 | 各自示例：`aweme/v1/feed/`、`aweme/v2/feed/`（protobuf）、`multi/aweme/detail`、`device_register` | 本地纯算 X-Gorgon / X-Khronos / X-Argus / X-Ladon / X-SS-STUB / TTEncrypt | 均无画质对比 | 无 | SignerPy 作者 2026-08-18「the problem in SignerPy is old」；iqbalmh18 修复 10 天后 #4「All request are empty response」无回复；tr4cex 全部 issue 是失效报告；douyin-sign 每日 CI「Automatic APK download not implemented」连续失败；code-root / GTACAT 无第三方验证；hoanglong README 自认「does not prove Douyin's servers accept」；huuhuybn 自称 X-Gorgon 0404 已被拒 | 不投入 |
| 托管签名：molkex/tiktok-private-api、Loukious/TikTokStreamKeyGenerator | 2026-09-14 / 2026-09-07 | App `aweme/v1/aweme/detail`、`multi/aweme/detail`、webcast 等 | RapidAPI 付费签名，本地无算法（「does not explain or expose the signing algorithms」） | 未见档位处理 | 未见 | 可用但闭源付费 | 与「不把链接交给第三方」冲突，不集成 |
| vagvalas/tiktok-hd-research | 2026-06-03 后无提交 | 分析 web vs App；`device_register` PoC 返回 `device_id: 0` | SignerPy | 记录 tokcdn 93.7 MB / 7.03 Mbps vs web 13.25 MB（alex_selekos） | 无；todo「HD gear may be region/login-gated — verify」 | BLOCKED（需有效 iid） | 「更高档在 App 阶梯」只是推测，复核不成立（2.13.4） |
| 黑盒服务：tokcdn（tikdownloader「MP4 HD」）、tikwm、snapcdn.app、musicaldown | cobalt#586 2026-07-26 称 tikwm originalDownloader 仍可用；#4138 最后评论 2026-09-07 | 未知（#4138 猜测走移动端 API、多家回源 snapcdn.app） | 未知 | tikwm `play` / `hdplay` / `wmplay` = web `normal_540_0` / `adapt_lowest_1080_1` / `download`（size 3895234 / hd_size 3435167 字节级一致，文件 key 相同）；tokcdn 同一视频时而上传原片（willsmith：4K QuickTime、TE 编辑器标签）时而更高转码档（alex：32 MB isom HEVC 2.2 Mbps），6 月的 93.7 MB 版本已不可得 | 未知 | 在线 | 只作画质对照，不集成 |
| xvhuan/tiktok-web-params | 2026-08-14「pure-computation TikTok web signing parameters (Dynosaur/Gnarly/X-Bogus)」 | — | JS 纯算，license NOASSERTION | — | — | 未测 | Evil0ctal 签名器的第二个对照实现 |
| streamlink TikTok 直播插件（旁证） | #6911 2026-04-28 开、2026-09-04 关（PR #7077） | `api-live/user/room?aid=1988` 的 `streamData` / `hevcStreamData` | 无 | 2026-04-28 起 web 只给 720p；`hevcStreamData` 是否含原始档按地区（瑞士 / 日本有，另一报告者多为空）；Cookie 无效 | 无 | — | 2026 年 TikTok「按出口地区决定是否下发高档」的实证，与 #15690 同构 |
| SysAdminDoc/hushfeed（App 46.2.3 补丁，旁证） | #3 2026-09-14 开、09-16 关 | App 内 `Video#getBitRate` gear 列表 | — | 每视频 2–3 档，命名与 web 相同（adapt_lower_720_1 / adapt_540_1 / 1080 档） | — | — | App 播放列表里看不到更高档（反向旁证） |

#### 2.13.2 用户 1080p→540p 问题

##### 2.13.2.1 各来源对现象的解释

| 来源 | 说法 | 复核结论 |
| --- | --- | --- |
| yt-dlp#15690（2026-01-26，open，标 cant-reproduce） | 英国 IP 只给 `play` 一档且每次画质不同；日本代理 / `--xff JP/CA/UK` 缺 `bytevc1_1080p`；`--xff US` 后多档稳定；美国 IP 正常 | 「间歇性服务端策略」成立：三位报告者 2026-01-29 都不能复现；本机（美国出口）今日 3 样本 9 次全档。「按 IP 地区」只有该 issue 一份证据，2026-06 后各项目无新报告（reports 组跨仓库搜索） |
| yt-dlp#15690 fireattack 2026-01-27 | 带自己的 Cookie 时不论 `--xff` 都稳定拿到 1080p | **不成立**（作为可依赖的结论）：一人一视频一次口头观察，无 -F 输出、无对照，两天后他本人不带 Cookie 也不再缺档；线程内无人交叉验证，维护者的修复方向是 XFF；#14172 带 Firefox Cookie 的 willsmith 最高也只是 `bytevc1_1080p_2058372`，与今日匿名相同（复核 verify_cookie1080） |
| yt-dlp#15891（2026-02，已关） | 带 Cookie 时服务端换一套 `bytevc1_1080p` 档，URL 以 `/media-video-hvc1/` 结尾且无音频 | 现象真实但**与 Cookie 无因果**：#16622（加拿大，无 Cookie，换 VPN 无效）同样命中，bashonly「Looks like #15891 except without cookies」；同一人刷新 Cookie 后又变有声；上游 06ab8f6a（#17460）已把该路径标 `acodec: none` + `__needs_testing`，fork tiktok.py:682-685 已有同样代码 |
| yt-dlp#17393（2026-08-08，open，巴西） | 偶发拿不到 universal data，`--xff US` 无效，换 5G 网络可过 | 是「被拦截页」问题（`X-TT-System-Error: 3`），不是档位变少；说明 XFF 不是万能钥匙 |
| yt-dlp#17604（2026-09-01，open） | 「The result also depends on the IP address」；chrome-150 伪装目标被封 | 可用性问题；作为诊断项：降档前先看 `Impersonation target` 与 curl_cffi 版本 |
| yt-dlp#13134（2025-05，open） | App API 被签名保护后第三方全退到 web 档：「(1080x1920) 13,8MB vs ... (576x1024) 2,7MB」 | 用户观察未验证；说明 App 端曾下发比 web 高的档，但与用户当前「web 路径本身降档」不同类 |
| yt-dlp#16532（2026-04-20，open，0 评论） | 上游把所有 540p 硬映射为 576 宽，部分视频实际 540x960 | 成立（代码 `if dimension == 540: dimension = 576`）；业务机报告的「540p」分辨率数字可能本身不准，要看 `UrlKey` / `PlayAddr.Width/Height` |
| Evil0ctal v5 文档 `14-troubleshooting.md` | 「The platform answers about the post differently depending on where the request came out. Try an identity on a proxy in a different country」；identity 的 tz / language 与代理 GeoIP 对齐 | 无实测数据，只是运维建议；与 #15690 方向一致但更完整（把 region 参数、tz_name、出口三者对齐） |
| cobalt PR #1514 | 数据中心 IP 上含 `Mozilla/5.0` 的 UA 常被挑战，换任意其他 UA 即可 | 不同问题（能否拿到页面），与「能下但只有 540p」无关 |
| streamlink#6911 | 直播 web 2026-04-28 起只 720p，`hevcStreamData` 按地区不同，Cookie 无效 | 旁证：同年 TikTok 确实按出口地区决定是否下发高档；接口不同，不能直接推到点播 |
| hushfeed#3 | App 46.2.3 播放器每视频只收 2–3 档且命名同 web | 反向旁证：App 播放列表里没有更高档 |
| TikHub 博客（WebSearch 摘要称「按连接速度下发 540p/360p」） | — | 页面 522 / 超时，原文未读到，**不作依据** |
| Evil0ctal v4 #735「Tiktok USA」 | 正文只有「OK I know you know」 | 无内容 |

##### 2.13.2.2 缓解手段汇总

| 手段 | 来源 | 复核结论 | 前提 / 代价 / 风险 |
| --- | --- | --- | --- |
| 手动 `--xff US` | #15690（SeBunderthesea「Yes - with both videos」）、README `--xff` | 框架层对 TikTok 生效（`common.py:742-754` 显式国家码无视 extractor 是否 geo-bypassable；tiktok.py 未禁用 `_GEO_BYPASS`）。XFF 头确被 TikTok 边缘层读取：`--xff GB` 稳定把 `vgeo` VGeo-US→VGeo-EU、`idc` useast8→no1a、`clusterRegion` TTP→EU_TTP、domains 切 `.eu`，但 `app-context.region` 仍 US、`bitrateInfo` 不变（复核 verify_xff）。本机出口本身是美国，所以「直连 == --xff US」是同义反复，**CN 出口上 XFF US 能否恢复 1080 未验证** | 副作用：503、status 10101 SysErrPanic、真验证码页（PR #15710 正文）；fireattack「I occasionally got HTTP 503 when using `--xff US`」；#17393 报告者 `--xff US` 无效 |
| 移植 PR #15710（默认 XFF US + 失败回退） | PR #15710 | 逻辑约 60 行；对当前 master 3/7 hunk 失败（`_solve_challenge_and_set_cookies` 改名、`get_webpage` 加 headers/impersonate），`_real_extract` 内 XFF + 重试两个 hunk 可偏移应用（复核 verify_upstream） | 作者自认不满意；建议做成 extractor-arg 而非默认；随机头 + XFF 会让指纹更独特（#17437 讨论的逻辑） |
| 登录 Cookie | #15690 fireattack | **不成立**（未验证假设，见 2.13.2.1） | 账号风险；hvc1 无声档与 Cookie 无关且 fork 已处理；带 Cookie 的 1080 档码率不比匿名高（#14172） |
| 换出口地区 / 代理 | Evil0ctal 文档；gallery-dl 10231 提示；#17393「5G 可过」 | 无对照实测 | 业务机可做 A/B；Evil0ctal 强调同一 identity 换出口会被当不同会话 |
| 第二数据源 `/api/item/detail/`（X-Dynosaur / X-Gnarly 签名） | Evil0ctal v5、JoeanAmier | **成立**：无 Cookie、空 msToken、随机 19 位 device_id 即可拿完整 `bitrateInfo`；同一出口同一时段与页面 hydration 的 (GearName, DataSize, FileHash) 完全一致（3 样本 + XFF 对照，复核 verify_joean / verify_appapi）。因此它是否能缓解降档，取决于业务机上「页面路径降档而 API 不降」是否成立，**未验** | 该端点只校验 X-Dynosaur（X-Gnarly 换垃圾照常返回）；`ENV_CODE=65 / UB_CODE=8` 这类常量随 SDK 变；itemStruct 比页面少 11–15 个键；`DataSize` 页面是字符串、API 是整数；JoeanAmier 2026-01～09 因签名失效整体不可用的历史说明它比页面更依赖签名时效 |
| 重试 | #15644（3 次重试）、Evil0ctal 熔断 | 针对随机拦截页，不是针对档位；#15690 称同一 IP 每次结果不同，理论上多次取并集可能有效，但无证据 | 请求数上升，触发风控 |
| App API（`_extract_aweme_app` / descargarbot / Evil0ctal v4） | fork 2.7 | **不成立**：无签名 200 空 + `tt_orcas_res: 1`（3/3）、feed 429（3/3）；换真实设备池同样空 | 无开源签名可用 |
| 换伪装目标 / curl_cffi 版本 / UA | #17604、cobalt #1514 | 解决「拿不到页面」，不是降档 | 诊断项 |
| region / priority_region / tz_name 与出口对齐 | Evil0ctal identity 设计 | JoeanAmier 写死 `region=US` + `tz_name=Asia/Shanghai` 的矛盾参数也能拿全档（本机），说明 detail 端点不校验一致性；对档位的影响未测 | 低成本 A/B |

##### 2.13.2.3 建议的排查顺序（在业务机上做）

前提：业务机（电信出口，按业务实际是否经代理）、出现 540p 的那批视频 ID、`--ignore-config --proxy "" --no-cookies -v`，每步保留原始输出。

1. **先分型，不要先试方案。** `yt-dlp -v -F --write-pages <url>`，看：
   - `bitrateInfo` 是否为空只剩 `play`（#15690 型，quality 随机）还是 `bitrateInfo` 只下发到 540 档（另一型）；两型缓解方式不同；
   - 各档 `UrlKey` / `GearName` 与 `PlayAddr.Width/Height`，别只看 -F 的 RESOLUTION 列（#16532 的 540→576 硬映射）；
   - 响应头有无 `X-TT-System-Error: 3`（拦截页）、`tt_orcas_res`（签名 / 设备拒绝）；
   - `[debug] [TikTok] Impersonation target:` 与 `curl_cffi` 版本（#17604 chrome-150 被封）；
   - 页面 `biz-context.geoCity` / `vgeo` / `idc` / `app-context.region`，确认 TikTok 看到的出口地区（fake-ip 类透明代理会让「直连」名不副实，见本节开头）。
2. **同一分钟内** 跑 直连 / `--xff US` / `--xff GB` 各 1 次，比较档位集合；至少重复 3 轮看间歇性。
   预期三种结果：(a) XFF US 稳定恢复 1080 → 走 2.13.2.4 的 XFF 开关；(b) 无差异 → XFF 不是解法；(c) 503 / 10101 / 验证码 → XFF 有副作用，记录频率。
3. 用 `mp_work7/downloaders/joean/repro2.py`（改 VID，同目录 `tiktok_sign.py`，只发 1 个请求）或 `mp_work7/verify_joean/api_detail.py` 对同一视频跑 `/api/item/detail/`，比较 `bitrateInfo` 是否同样降档。
   只有「页面降、API 不降」时第二数据源才有意义。
4. 换出口（业务机有无其他线路 / 代理地区）做 A/B。
5. 若前面都无结论，再用**测试账号**（不用业务账号）的 Cookie 做交替对照各 ≥10 次；对每个 1080 档看 URL path 是否 `/media-video-hvc1/`、`-j` 里 `acodec` 是否 `none`，下载后 `ffprobe` 确认音频流。
6. 把 1–5 的原始输出（info.json、页面 dump、响应头、出口 IP）留档，回填本文与文档 5.4。

##### 2.13.2.4 在 fork 里的落地方式（按排查结果分支）

- **结果 (a)，XFF US 有效**：在 `TikTokIE._real_extract` 加 opt-in extractor-arg（如 `tiktok:xff=US`），命中时
  `self._initialize_geo_bypass({'countries': [cc]})` + `for first_attempt in (True, False)` 循环，status 落在 {-2 验证码页, 503, 10101} 时
  `self._x_forwarded_for_ip = None` 重试并 `to_screen` 提示（参照 #15710 hunk #6 / #7，可偏移应用；challenge / 503 相关 hunk 要按 fork 现有
  `_solve_challenge_and_set_cookies` / `get_webpage(headers=, impersonate=)` 手工改写）。**不默认开启**：#15710 自己列的三个副作用，加上 fork 已带随机头 + 伪装，
  再叠 XFF 会让指纹更独特（#17437 讨论）。业务机配置里显式写开关即可。
- **结果「页面降、API 不降」**：引入 Evil0ctal `src/dtk/signing/native/tiktok_sign.py`（Apache-2.0，仅标准库；JoeanAmier 的改编版是 GPL-3.0，不要用它）作第二路径，
  页面路径失败或档位不含 1080 时再请求 `/api/item/detail/`。注意：`DataSize` 类型（`int_or_none` 兜两种）、少掉的键（challenges / textExtra / comments 等，取 formats 够用）、
  UA 必须与签名时一致（X-Dynosaur 含 UA 哈希）、只校验 X-Dynosaur 所以 X-Gnarly 正确性只能靠离线向量（Evil0ctal `tests/unit/test_signing.py` TIKTOK_VECTORS）。
- **无论哪种结果都值得做的零风险项**：
  - 诊断日志：网页响应带 `X-TT-System-Error: 3` 时打「被风控拦截（不是视频不可用）」；`/api/item/detail/` 路径若引入，200 空 body + `tt_orcas_res: 1` 打「设备 / 签名被拒」；
  - 文档 3.1 加 TikTok 行（上述两条 + `Impersonation target` 检查项）；文档 2.7 补 06ab8f6a（hvc1 无声档已处理）与 #16532（540→576 硬映射）说明。
- **风险清单**：XFF → 503 / 10101 / 验证码页、指纹更独特；Cookie → 账号风控、且无证据有效；第二路径 → 签名常量时效、请求数翻倍；
  任何默认行为改动都会影响业务机的稳定流水线，先开关后默认。

#### 2.13.3 其他项目里值得借鉴的方案（按可行性排序）

1. **风控响应判据（零成本，立即可做）**
   - `X-TT-System-Error: 3` + 约 600 字节页面 = 被拦截而非视频不可用（yt-dlp PR #15644 正文、#17393 抓包）；
   - HTTP 200 空 body + `tt_orcas_res: 1` = device_id 缺失或 X-Dynosaur 无效（Evil0ctal `params.py` 注释 2026-09-08 实测；本机复核 B / D / G / H 四组复现）；
     注意 Evil0ctal 记录的「伪造 msToken → 0 字节」本机未复现（146 位随机 msToken 仍返回完整数据），不要写进判据；
   - body 级 `statusCode 10000` = 验证信封；body 前 4KB 出现 `verify_center` / `captcha` / `tiktok-verify-page`（Evil0ctal `14-troubleshooting.md`）；
   - `Impersonation target` + curl_cffi 版本（#17604）；`UrlKey` 与 `PlayAddr.Width/Height` 核对（#16532）。
   前提：无。代价：几行日志。
2. **XFF US 可选开关 + 失败回退**（PR #15710 的 `_real_extract` 两个 hunk）。前提：业务机 2.13.2.3 第 2 步出现结果 (a)。代价：手工重放；副作用见上。
3. **`/api/item/detail/` 第二数据源 + Evil0ctal 纯 Python 签名器**。前提：业务机第 3 步显示 API 不随页面降档。代价：引入约 750 行签名代码、常量随 SDK 更新失效（JoeanAmier 整个 2026 上半年因此不可用）、只校验 X-Dynosaur。
   许可证：Evil0ctal Apache-2.0；xvhuan JS 版 license NOASSERTION；JoeanAmier GPL-3.0。
4. **参数与出口对齐**（Evil0ctal identity 思路）：`region` / `priority_region` / `tz_name` / `browser_language` 与出口 GeoIP、XFF 国家一致后再对比档位。前提：业务机能改这些参数（fork 网页路径不带它们，只有列表接口 `_build_web_query` 带）。代价：低；证据：无（只是设计原则）。
5. **免签名的会话参数获取端点**（f2 `conf.yaml`）：msToken `POST mssdk-sg.tiktok.com/web/common`（返回长度取决于 query 里带的 msToken：不带 / 144 → 144，148 / 152 / 168 → 168，复核 verify_f2）、ttwid `POST www.tiktok.com/ttwid/check/`、odin_tt `GET passport/web/auth/config`、device_id 取首页 `webapp.app-context.wid`。前提：只在走会话 / Cookie 路线时需要。代价：每个视频多 1–4 个请求。
6. **列表接口参数核对**（gallery-dl `_build_api_request_url`：aid=1988、region=US、verifyFp `verify_` + 7 位 hex、device_id 7.25e18–7.325e18）。用途：核对 fork `TikTokUserIE` 参数是否过期。前提：无。
7. **Cookie A/B 的实验设计**（仅实验，不是方案）：TikTok-Api 是唯一天然带真实浏览器 Cookie 的实现，可作 A/B 工具；结论必须来自业务机对照。

#### 2.13.4 已排除 / 不建议

- **App API 无签名路径**（fork `_extract_aweme_app`、descargarbot v2、Evil0ctal v4、cobalt 2023–2024 旧方案）：`multi/aweme/detail` 200 空 + `tt_orcas_res: 1`（随机 3 对真实 iid / device_id 3/3），
  `feed` 429 `ratelimit triggered`（3 种配方 3/3）。cobalt 与 Evil0ctal v4 都是在 feed 失效后放弃，不是「没做过」（复核修正：cobalt PR #503 2024-05-21、#497；Evil0ctal 46561948 2024-09-14 靠 `x-ladon` 复活过一次）。
  fork 的 `_extract_aweme_app` 保留为兜底即可，不再投入。
- **App 签名库**：SignerPy（作者 2026-08-18 自认 old，issues 关闭）、iqbalmh18（修复后 #4 空响应无回复、作者 2026-07 曾声明停维护）、tr4cex（issue 全是「不工作」）、douyin-sign（README 承诺的 APK 自动更新不存在，CI 连续失败）、
  code-root / GTACAT（无第三方验证，半年以上无更新；GTACAT 源码内嵌作者登录 Cookie）、aionotaio（需 unidbg）、hoanglong（PoC，自认未证明服务端接受）、huuhuybn（自称 X-Gorgon 0404 被拒）。
  没有一个给出「当前可用」的证据，且签名常量随 App 版本数周一换（#13134 stefanodvx：「TikTok frequently updates its signature system」）。
- **托管签名服务**（molkex、Loukious 的 RapidAPI）：算法不公开、付费，把请求交给第三方。
- **黑盒 HD 服务**（tokcdn / tikdownloader、tikwm originalDownloader、snapcdn.app、musicaldown）：来源不可查证；tokcdn 同一视频内容随时间变化（willsmith 今天是 4K QuickTime 上传原片，alex 今天是 32 MB 转码档，6 月的 93.7 MB 版本已不可得，复核 verify_tokcdn）；
  `whois tokcdn.com` 2025-05-28 注册、信息隐藏。不集成。
- **tikwm `hd=1`**：`play` / `hdplay` / `wmplay` 与 web `normal_540_0` / `adapt_lowest_1080_1` / `download` 字节级一致（size 3895234 / hd_size 3435167 / wm_size 3970507），只是 CDN 域换成 tiktokcdn-us.com；没有更高档（reports 实测，与文档 2.7 一致）。
- **「tokcdn 7 Mbps 文件来自 App bit_rate 阶梯的高档而非上传原片」**（tiktok-hd-research todo.md 推测）：**不成立**。来源自己标 BLOCKED；yt-dlp#7109（2023-06）的 App feed 原文显示阶梯可含 `original_1080_0`（`quality_type: 10000`、`file_id == video_id`、8.3 Mbps）即原片本身作为阶梯一项，「阶梯高档 vs 原片」不是二选一；
  实测 willsmith 的 tokcdn 文件是 2160x3840 QuickTime、`TEEditor` 导出标签、moov 在尾、25 Mbps，分辨率超出任何转码档；已知所有 web 阶梯 1080 档码率 0.67–2.6 Mbps（4 份 GitHub 样本 + 本机 9 份 dump），7 Mbps 与抖音 `ratio=default` 原片实测（6.2–7.0 Mbps）一致。
  对决策的含义：签名 App API 方向若成功，拿到的更可能是原片项（前提是 TikTok 仍下发 `original_*`，而 #7109 下 2025-07 已报失效、2026-07 无人回应），不应以「阶梯里有 93.7 MB 转码档」为投入前提。
- **登录 Cookie 作为主缓解手段**：**不成立**（2.13.2.1）。只保留为实验项。
- **默认强制 XFF 且无回退**：503 / 10101 / 验证码页（PR #15710）。
- **gallery-dl / cobalt / TikTok-Api 的取法**：数据源与 fork 相同，反爬更弱（无 TLS 伪装、无随机头），选档更粗（像素 / 第一个 h265 / 单一 playAddr）。
- **f2 TikTok 模块**：msToken 长度校验 148 / 152 与端点现状 168 不符，两分支开箱即坏（复核 holds）；且 X-Bogus 已被 X-Gnarly 取代（TikTok-Api #1311、f2 #384）。
- **#15644 的 `/embed/v2` + oEmbed 兜底**：档位不比 hydration 多（fork 2.7 实测），上游以「doesn't actually fix anything」关闭；**#17437 Referer**、**#17599 轮换伪装目标**：只是换指纹，bashonly 预计会很快再被封。
- **「XFF 被 TikTok 忽略」**：也不成立——`--xff GB` 稳定改变 `vgeo` / `idc` / `clusterRegion` / domains（复核 verify_xff），只是本机出口已是美国无法测 CN→US 方向。
- **TikHub 博客「按连接速度下发 540p」**：原文未读到，不采信。
- **Reddit（r/youtubedl）**：WebFetch / search.json 均 403，本次未覆盖。

##### 2.13.5 参考项目（本次新增，格式同第 4 节）

| 项目 | 链接 | 可借鉴的方案 | 什么时候回来看 |
| --- | --- | --- | --- |
| yt-dlp 上游 #17452 / #17460 / #17480 | [#17452](https://github.com/yt-dlp/yt-dlp/pull/17452)、[#17460](https://github.com/yt-dlp/yt-dlp/pull/17460)、[#17480](https://github.com/yt-dlp/yt-dlp/pull/17480) | 2026-08 上游 TikTok 三件套：`_generate_blockbuster_headers` 随机头（common.py:3966）、`/media-video-hvc1/` 档标 `acodec: none` + `__needs_testing`（关闭 #16622 / #17293 / #17372，#15891 引用关闭）、重新实现 impersonation；fork 已手工重放 | 上游再改 TikTok 反爬时对照；hvc1 档有声 / 404 规律变化时 |
| yt-dlp 上游 #17604 / #17393 | [#17604](https://github.com/yt-dlp/yt-dlp/issues/17604)、[#17393](https://github.com/yt-dlp/yt-dlp/issues/17393) | curl_cffi ≥0.16.2 的 chrome-150 目标被封（降级 0.16.0 或换 UA 可绕），「结果也取决于 IP」；拦截页 `X-TT-System-Error: 3`，`--xff US` 无效、换 5G 可过 | 业务机出现「Unexpected response from webpage request」而非降档时 |
| yt-dlp 上游 #13134 | [#13134](https://github.com/yt-dlp/yt-dlp/issues/13134) | App API 2025-05 起需完整签名，第三方全部退到 web 档（1080x1920 13.8 MB → 576x1024 2.7 MB 的用户观察）；SignerPy 作者 2026-08-18 承认库已过时 | 评估 App API 方向时 |
| yt-dlp 上游 #7109 | [#7109](https://github.com/yt-dlp/yt-dlp/issues/7109) | 2023-06 App feed 原文：`bit_rate` 阶梯含 `original_1080_0`（`quality_type: 10000`、`file_id == video_id`、8.3 Mbps h264），即原片作为阶梯一项；2025-07 报失效 | 若签名 App API 恢复，检查是否仍下发 `original_*` / `quality_type 10000` |
| yt-dlp 上游 #16532 | [#16532](https://github.com/yt-dlp/yt-dlp/issues/16532) | 540p 档硬映射为 576 宽，部分视频实际 540x960；核对档位要看 `UrlKey` / `PlayAddr.Width/Height` | 分析业务机「540p」报告时 |
| yt-dlp 上游 #15891 / #16622 / #15642 | [#15891](https://github.com/yt-dlp/yt-dlp/issues/15891)、[#16622](https://github.com/yt-dlp/yt-dlp/issues/16622)、[#15642](https://github.com/yt-dlp/yt-dlp/issues/15642) | `/media-video-hvc1/` 无音频 1080 档：带与不带 Cookie 都会命中、间歇变体；ffprobe 只见 `hevc (hvc1)` + `Bento4 Video Handler` | 发现 1080 档无声时 |
| yt-dlp 上游 PR #15644 / #17437 / #17599 / #17548 | [#15644](https://github.com/yt-dlp/yt-dlp/pull/15644)、[#17437](https://github.com/yt-dlp/yt-dlp/pull/17437)、[#17599](https://github.com/yt-dlp/yt-dlp/pull/17599)、[#17548](https://github.com/yt-dlp/yt-dlp/pull/17548) | 被拒 / 自闭的绕拦截方案：embed+oEmbed 兜底、Referer、轮换伪装目标、item_list 重试；`x-tt-system-error: 3` 判据出处 | 网页被拦而非降档时 |
| yt-dlp 上游 #17211 / #14172 | [#17211](https://github.com/yt-dlp/yt-dlp/issues/17211)、[#14172](https://github.com/yt-dlp/yt-dlp/issues/14172) | tokcdn `_original.mp4` 16 MB vs yt-dlp <6 MB；tikwm originalDownloader 对 willsmith 给 4K；均被并入 #4138 | 原片对照 |
| gallery-dl | [mikf/gallery-dl](https://github.com/mikf/gallery-dl) `gallery_dl/extractor/tiktok.py`（19a64031b）、[#9328](https://github.com/mikf/gallery-dl/issues/9328)、[#9718](https://github.com/mikf/gallery-dl/issues/9718)、[#9764](https://github.com/mikf/gallery-dl/issues/9764) | 同源 hydration + JS 挑战；列表接口参数集合（aid=1988 / region=US / 随机 verifyFp / device_id 范围）；「画质不是最好」= 按像素排序的客户端差异 | 核对 `TikTokUserIE` 列表参数；看 item_list 失效是否波及 fork |
| cobalt | [imputnet/cobalt](https://github.com/imputnet/cobalt) `api/src/processing/services/tiktok.js`（2ac0436f7）、[PR #503](https://github.com/imputnet/cobalt/pull/503)、[#497](https://github.com/imputnet/cobalt/issues/497)、[#586](https://github.com/imputnet/cobalt/issues/586)、[PR #1514](https://github.com/imputnet/cobalt/pull/1514) | 2024-05 从 App feed 退到网页的历史；数据中心 IP 上含 `Mozilla/5.0` 的 UA 被挑战、换 UA 即可；#586 记录 App 内下载码率高于 web、tikwm originalDownloader 2026-07 仍可用 | 网页被挑战（非降档）时；原片对照 |
| Evil0ctal/Douyin_TikTok_Download_API v5 | [仓库](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)：`src/dtk/signing/native/tiktok_sign.py`（e02c0f3f5）、`src/dtk/platforms/tiktok/params.py`、commit e3a1b7138d、`documents/en/06-identities-and-proxies.md`、`14-troubleshooting.md`、`tests/unit/test_signing.py` | 纯 Python X-Dynosaur / X-Gnarly（Apache-2.0，仅标准库，附 Node 真 SDK 离线向量）；`device_id` 必带、随机 19 位即可；msToken 空可用、不伪造；`tt_orcas_res` / 200 空 / statusCode 10000 风控分类；identity 与代理 GeoIP 对齐 | 需要 `/api/item/detail/` 第二路径时；`/api/item/detail/` 200 空时对照判据；SDK 常量（ENV_CODE / UB_CODE / SCM 版本）变化时 |
| JoeanAmier/TikTokDownloader | [仓库](https://github.com/JoeanAmier/TikTokDownloader) `src/encrypt/tiktok_sign.py`、`src/interface/template.py`、[#733](https://github.com/JoeanAmier/TikTokDownloader/issues/733)、[#744](https://github.com/JoeanAmier/TikTokDownloader/issues/744)、[#558](https://github.com/JoeanAmier/TikTokDownloader/issues/558) | Evil0ctal 签名器的单文件改编版（GPL-3.0）；选档按 (max 边, 码率, 大小)；作者「原画接口都不会开源」；写死 region=US + tz Asia/Shanghai 也能拿全档 | 签名器失效时看它是否先修；作为 GPL 版不直接引用 |
| xvhuan/tiktok-web-params | [仓库](https://github.com/xvhuan/tiktok-web-params)（3fe11ef855，2026-08-14） | JS 纯算 Dynosaur / Gnarly / X-Bogus，license NOASSERTION | Evil0ctal 签名器有疑问时的第二对照 |
| f2（TikTok 模块） | [Johnserf-Seed/f2](https://github.com/Johnserf-Seed/f2) `f2/apps/tiktok/utils.py`、`f2/conf/conf.yaml`、[#384](https://github.com/Johnserf-Seed/f2/issues/384)、[#407](https://github.com/Johnserf-Seed/f2/issues/407) | msToken / ttwid / odin_tt / device_id 四个免签名获取端点；msToken 长度校验 148 / 152 已与端点（168）脱节 | 需要会话参数端点时；第 4 节 f2 行只记 ABogus，此为补充 |
| TikTok-Api | [davidteather/TikTok-Api](https://github.com/davidteather/TikTok-Api) `TikTokApi/tiktok.py`、`api/video.py`、[#1311](https://github.com/davidteather/TikTok-Api/issues/1311) | 真浏览器会话拿 msToken / Cookie；列表因缺 X-Gnarly 200 空 | 做 Cookie A/B 实验时 |
| descargarbot/tiktok-video-scraper-mobile | [仓库](https://github.com/descargarbot/tiktok-video-scraper-mobile)（v2 / v3、ids.json） | 与 fork `_extract_aweme_app` 同路线的无签名 + 真实设备池方案，现 200 空；v3 SignerPy 自标失效 | App API 方向复活时 |
| App 签名库群 | [is-L7N/SignerPy](https://github.com/is-L7N/SignerPy)、[iqbalmh18/tiktok-signer](https://github.com/iqbalmh18/tiktok-signer)（PR #2、#4）、[tr4cex/TikTok-Encryption](https://github.com/tr4cex/TikTok-Encryption)、[7452323/douyin-sign](https://github.com/7452323/douyin-sign)、[code-root/tiktok-android-signing-toolkit](https://github.com/code-root/tiktok-android-signing-toolkit)、[GTACAT/Tiktok-Mobile-Api-Scraper](https://github.com/GTACAT/Tiktok-Mobile-Api-Scraper)、[aionotaio/tiktok-reverse-api](https://github.com/aionotaio/tiktok-reverse-api)、[hoanglongggggggggg/DOUYIN-40.2.0…](https://github.com/hoanglongggggggggg/DOUYIN-40.2.0_metasec-ml-vmp_reverse-engineering)、[huuhuybn/X-Gorgon](https://github.com/huuhuybn/X-Gorgon) | X-Gorgon / X-Khronos / X-Argus / X-Ladon / TTEncrypt 的算法结构与 2026-03 v44 头部集合（code-root）；iqbalmh18 PR #2 的两个失效根因（x-ss-req-ticket 时间戳不一致 → 200 空；TTEncrypt 未 gzip → `device_id: 0`） | 只在决定投入 App 签名时；目前全部没有「当前可用」证据 |
| 托管签名 | [molkex/tiktok-private-api](https://github.com/molkex/tiktok-private-api)、[Loukious/TikTokStreamKeyGenerator](https://github.com/Loukious/TikTokStreamKeyGenerator) | 证明 2026-09 仍有人在服务端维护有效 App 签名，但开源侧拿不到 | 不集成，只作「签名仍可做」的旁证 |
| streamlink #6911 | [streamlink/streamlink#6911](https://github.com/streamlink/streamlink/issues/6911)、PR #7077 | 直播 web 2026-04-28 起只 720p，`hevcStreamData` 按地区 / 会话不同，Cookie 无效 | 点播降档与直播是否同一策略的对照 |
| hushfeed #3 | [SysAdminDoc/hushfeed#3](https://github.com/SysAdminDoc/hushfeed/issues/3) | App 46.2.3 播放器每视频只收 2–3 档、命名同 web | 「App 端有更高档」的反向旁证 |

#### 2.13.6 未解决的问题

1. **业务机现象分型**：「只有 540p」是 #15690 型（`bitrateInfo` 为空、只剩 `play`）还是 `bitrateInfo` 只到 540 档？需要当时的 info.json / `--write-pages` 页面才能对号入座；两个上游 issue 表现相反（#15690 `--xff US` 有效，#17393 无效）。
2. **XFF US 在 CN 出口的效果**：本机出口已是美国，实验只证明 US→EU 方向不降档、XFF 头被边缘层读取；CN→US 方向未测。PR #15710 三个副作用在电信 + 代理出口下的发生率也未知，移植前需 ≥20 次统计。
3. **两条路径是否同时降档**：`/api/item/detail/` 与页面 hydration 在本机 9 次全一致；业务机上若同时降档，第二数据源无用。
4. **业务机的 curl_cffi 版本与伪装目标**：若走 chrome-150，会先出现「Unexpected response」而非降档（#17604），两者要分开。
5. **Cookie 效果**：#15690 的说法未经任何人验证；#14172 反例；业务机若测需用测试账号，并检查 hvc1 无声档。
6. **App 端是否仍下发 `original_*` / `quality_type 10000`**：#7109 2023-06 有，2025-07 报失效；tokcdn 同一视频从原片级退化为转码档说明第三方拿原片也不稳定。没有开源项目做过带完整签名的对比。
7. **Evil0ctal 的三条实测互相矛盾之处**：「伪造 msToken → 0 字节」本机未复现；「无 Cookie 可用」是本次实测而非项目承诺（JoeanAmier 默认 device_id 空且要求 cookie_tiktok）；服务端策略可随时变。
8. **JoeanAmier 5.8 在空 `device_id` 下是否真能工作**：本机复现用的是随机 19 位；Evil0ctal 称缺 device_id 即 200 空。
9. **region / priority_region / tz_name 参数对档位是否有影响、与 XFF 是否叠加**：未测。
10. **f2 msToken 长度是否与出口地区有关**：只有美国出口一个样本，标「未验证」。
11. **`--xff US` 那次多返回 `music.playUrl`**：复跑直连也缺，属服务端间歇缺字段，与 XFF 无关（复核），但只跑了两轮。
12. **直播 720p（streamlink#6911）与点播降档是否同一套策略**：无证据。
13. **Reddit 用户报告**：未覆盖。


#### 2.13.7 本次实测环境与请求记录

- fork：`/Users/liukang/code/project/yt-dlp/index103000/yt-dlp` 分支 `tiktok` @ 0481db651（yt-dlp 2026.08.19），curl_cffi 0.16.3，伪装目标 `chrome-146:macos-26`；所有运行 `--ignore-config --proxy "" --no-cookies`，每次 -F / -j 只发 1 个网页请求，未触发 JS 挑战二次请求。
- 出口：见本节开头（fake-ip 透明代理，TikTok 判定美国）。样本：willsmith/7474304960574786859、alex_selekos/7645243669045382422（两者有 `adapt_lowest_1080_1`）、hankgreen1/7047596209028074758（原生 576x1024，最高 540 档，不是降档）。
- 请求计数（TikTok 及字节域名，全部无 Cookie）：调研组 5 + 4 + 6 + 3，复核组 11 + 9 + 3 + 11 + 10 + 5 + 4，合计 71 次，单个 agent 均 ≤12。
  第三方服务：tikwm 3 次（其中 1 次 403）、tikdownloader 2 次、tokcdn 4 个 Range 请求（ffprobe seek，verify_tokcdn 组超出 4 次上限 2 次，已在其记录中披露）、tikhub.eu 3 次连接失败（0 字节）。
  未下载 tokcdn 完整文件；GitHub 只经 gh CLI 只读；未提 issue / PR / 评论，未 star / fork；未改仓库文件、未 commit / push（仅 `git fetch yt-dlp master`）。
- 原始输出：`mp_work7/{upstream,downloaders,appapi,reports}/` 与 `mp_work7/verify_*/`（-F / -j 输出、页面 dump、API 响应、脚本 `api_detail.py` / `repro2.py` / `probe_mstoken.py` / `verify_detail.py`）。

------

## 3. 排查方法

### 3.1 日志原因对照

| 日志 | 含义 |
| --- | --- |
| `Douyin embed_origin_api failed: HTTP 403: Blocked by ArgusSecurityPlugin ...` | open.douyin.com 来源也被纳入 Argus 校验，绕过方式失效 |
| `Douyin signed_web_api failed: HTTP 403: ... Uifid Not Found; unable to obtain a usable UIFID_TEMP from the webpage (<原因>)` | 当前 IP 被 Argus 拦截，且精选页没下发 `UIFID_TEMP`；括号里是页面失败原因（网络错误、挑战页 / 验证码页特征等）；可用 `--cookies-from-browser` 带入浏览器的 `UIFID` |
| `Douyin signed_web_api failed: HTTP 403: ... Validate Error; the UIFID_TEMP issued by the webpage was rejected as well` | 连页面刚下发的 `UIFID_TEMP` 也被判无效：uifid 的校验规则变了 |
| `Douyin signed_web_api failed: HTTP 403: ... Sign Invalid` | uifid 有效但签名不对：webSign 的盐或规范化规则变了，对照 DouYin_Spider 更新 `websign.py` |
| `Douyin signed_web_api failed: HTTP 403: ... Signature Not Found` | 带了 uifid 却没收到签名：检查 URL 是否被改写、签名参数名是否变了 |
| `Douyin signed_web_api failed: HTTP 403: Blocked by ArgusSecurityPlugin Uifid Not Found` | 当前 IP 被 Argus 拦截（按 IP 而定，海外、国内都有）。未签名时会自动取 `UIFID_TEMP` 签名重试，不会停在这一步；停在这里说明签名请求也报它，即服务端不再从 query 读 `uifid` |
| `Douyin signed_web_api failed: HTTP 200 with empty body (ttwid missing or not accepted)` | `ttwid` 缺失或无效（`a_bogus` 目前不校验） |
| `[debug] Douyin signed_web_api: signing with uifid from <来源>` / `uifid from <来源> was rejected (...)` | 签名用的 uifid 来自 `cookie UIFID` / `cookie UIFID_TEMP` / `cache` / `webpage UIFID_TEMP`；被判无效的会自动换下一个 |
| `Unable to obtain Douyin ttwid cookie` | ttwid 注册接口与首页都没下发 ttwid |
| `Douyin video is unavailable (... filter_reason=...)` | 视频不存在 / 被过滤 |
| `Unable to fetch Douyin home page for __ac_nonce: ...` | 首页请求失败（网络 / 代理） |
| `Douyin home page did not issue __ac_nonce` | 首页没下发 nonce，页面请求多半拿到 JS 挑战页 |
| `ssr_render_data: got the anti-bot JS challenge page` | `__ac_*` 缺失或不被接受 |
| `ssr_render_data: got the captcha page (验证码中间页)` | 签名与 nonce 不匹配，或触发风控 |
| `ssr_render_data: RENDER_DATA has no videoDetail` | 页面结构变化，或视频不可用 |
| `<id>: The original upload is no longer available (ratio=default returned a transcoded format); not listing the original format` | 探测识别出原片回退为转码档，已去掉 `original` |
| `WARNING: ... Unable to probe the Douyin original upload (...); listing it unprobed below the transcodes, ...` | 探测的第一个请求失败：原片保留但降到转码档之下，不作为默认；下载 `-f original` 时若 403，先看落到的 CDN 节点与请求是否带了 Referer（2.9） |
| `WARNING: ... Unable to read the codec info of the Douyin original upload ...` / `Unexpected moov range ...` | moov 没读到，原片仍是默认选择，但 info JSON 缺编码与 fps |
| `WARNING: [Douyin] Douyin original format is listed without probing (original_probe=false) ...` | `original=true` + `original_probe=false` 且输出 info JSON，整次运行打印一次 |
| `Unknown Douyin strategies: 'web' (available: ...)` / `Unknown Douyin original_probe value: 'size'; write douyin:original_probe=true ...` / `Douyin extractor arg ... must be a list of strings` | 配置写错：旧策略名 `web` / `open` / `webpage`、`original_probe` 的旧取值 `none` / `size` / `full`（现为 `true` / `false`，原 `full` 即 `true`）、Python API 把值写成字符串或布尔值；在任何请求之前报出 |
| `Douyin short link is invalid or expired` / `points to an unsupported page` | 短码失效，或短链指向图集、西瓜视频等（2.10） |

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

- 测试时优先走测试代理（出口多为巴西），避免本机 IP 被标记；代理 IP 多半被 Argus 拦截（按 IP 而定），正好用来测签名路径，
  要测不签名路径用国内直连（移动网络）。
  要单独测某个策略，用 `--extractor-args "douyin:strategies=signed_web_api"`（或 `embed_origin_api` / `ssr_render_data`），不必再 monkeypatch。
- 测试代理的 IP 随用户名里的会话段（`sn-<随机值>`）切换，同一会话段约 10 分钟内也可能换 IP；
  判断拦截状态时每轮都用新会话段，并以 `x-response-cinfo`（3.2）确认抖音看到的 IP。
- `-v` 的 `Command-line config` 会原样打印 `--proxy` 的用户名和密码，日志要贴出去之前先过滤。
- webSign 会把 `UIFID_TEMP` 写进 yt-dlp 缓存并在下次运行复用：测「空缓存第一个视频」这类场景时用 `--cache-dir <临时目录>` 或
  `--no-cache-dir` 隔离，否则请求数和路径与预期不同。
- 同一视频两次请求返回的格式数会波动（web API 69↔78、页面 12↔15、TikTok 的 audio 格式时有时无），
  新旧代码对比要背靠背跑，且只比两边共有格式的元数据。
- **Mac 上「直连」TikTok 其实是美国出口**：本机 Clash Verge 的 fake-ip 透明代理接管了 `*.tiktok.com`（`dig +short www.tiktok.com` → `198.18.x.x`，
  进程 `verge-mihomo`），出口为 Los Angeles / AS906 DMIT，TikTok 页面 `app-context.region=US`；`--proxy ""` 只关掉 yt-dlp 自己的代理，绕不过它。
  `www.douyin.com` 不在规则内，仍是江苏移动直连。所以本机对 TikTok 的所有实测都是「美国出口」，要测中国出口只能在业务机上做（2.13.2.3）。
- **commit message 里不要写 `#12345`**：本仓库是 yt-dlp/yt-dlp 的 GitHub fork，fork 没有自己的 issue，`#N`（以及 `yt-dlp/yt-dlp#N`、完整 URL）都会被
  GitHub 解析成上游的 issue / PR，并在上游页面时间线留下「referenced」反链（2026-09-22/23 的两个提交已经出现在上游 PR 15710 的时间线上，对维护者可见）。
  写成「上游 issue 15690」「PR 15710」这类不带 `#` 的形式，完整链接只放在文档里。
- 用 `--test` 实际下载（只下 10KB），只列格式发现不了下载阶段的 403。
- 代理每个请求 4–6 秒，SSR 方案一个场景要几十秒，批量用例放后台跑，Python 加 `-u` 避免超时丢输出。
- 签名类算法重构时，用固定输入生成基准输出做逐字回归（`__ac_signature` 用了 204 组）。
- 验证 ECS 选节点时注意：`dns.alidns.com` 带 `edns_client_subnet` 查到的是另一层 CDN
  （`via` 形如 `...jswuxi-ct53-bm.Creative`），在那里连 embed_origin_api 都返回空，不能代表实际访问的节点。
- **确认跑的是源码**：Mac 的 `.venv` 的 site-packages 里另装有一份旧版 `yt_dlp`，在仓库根目录以外执行
  `.venv/bin/python -m yt_dlp` 会悄悄用旧代码。验证时 cd 到仓库根目录或设 `PYTHONPATH`，以 `-v` 日志出现 `[debug] Git HEAD:` 为准。
  Windows 下载机同理：它从 `.venv\Lib\site-packages` 导入已安装的 yt_dlp，更新代码后要重新安装才生效。
- 格式选择改动（如新增格式）要回归 `-f worst` / `wv*` 等，不只看默认选择：可以把保存的 `aweme_detail` / 精选页 HTML
  喂给 `_real_extract`（monkeypatch 网络函数）离线比对新旧选择结果。
  还要带上 `-S res` / `-S size` 与 `format_sort_force=True`：用户 `-S` 排在 `preference` 之后、`quality` 之前（force 时越过 `preference`），
  2.12 那类 `preference` 或宽高错误在默认排序下看不出来。

------

## 4. 参考项目

| 项目 | 链接 | 可借鉴的方案 | 什么时候回来看 |
| --- | --- | --- | --- |
| yt-dlp 上游 PR #16182 | [yt-dlp/yt-dlp#16182](https://github.com/yt-dlp/yt-dlp/pull/16182) | 本 fork `a_bogus` + SM3 + `ttwid` 注册 + `s_v_web_id` 的来源（未合并） | `a_bogus` 失效时追溯原始实现 |
| yt-dlp 上游 issue #9667 | [yt-dlp/yt-dlp#9667](https://github.com/yt-dlp/yt-dlp/issues/9667) | 上游 Douyin「Fresh cookies needed」问题的主 issue（仍 open） | 看上游是否有官方修复 |
| f2 | [Johnserf-Seed/f2](https://github.com/Johnserf-Seed/f2) | 多平台下载器，`ABogus` 算法原始出处（Apache 2.0） | `a_bogus` 算法版本更新 |
| gmssl | PR 中链接 [duanhongyi/gmssl](https://github.com/duanhongyi/gmssl)，现为 [py-gmssl/py-gmssl](https://github.com/py-gmssl/py-gmssl)（GitHub 显示 MIT） | SM3 纯 Python 实现 | SM3 实现有疑问时对照 |
| Diana PR #612 | [SuInk/Diana#612](https://github.com/SuInk/Diana/pull/612) | 三级回退：① open.douyin.com 来源免签名 detail；② App feed 接口 `api5-normal-c-hl.amemv.com` / `aweme.snssdk.com` 的 `/aweme/v1/feed/`，`aid=1128` + 安卓 App UA，按 `aweme_id` 过滤（我们实测最高 720p，见 2.8）；③ 老的 Cookie + `uifid` + `a_bogus`（仍 403） | embed_origin_api 与 SSR 方案都失效时，App feed 可作最后兜底 |
| nonebot-plugin-parser-lite PR #311 / #312 | [#311](https://github.com/sokoko-org/nonebot-plugin-parser-lite/pull/311)、[#312](https://github.com/sokoko-org/nonebot-plugin-parser-lite/pull/312) | 同样改用 open.douyin.com 来源，只带 `aweme_id` + `aid`，不再发 `ttwid` 和浏览器 / 设备参数 | 同上 |
| media-parser Issue #15 | [ucmao/media-parser#15](https://github.com/ucmao/media-parser/issues/15) | 根因分析：Argus 对缺少真实浏览器 `UIFID` 的匿名请求按概率拦截（报告拦截率 40%–50%）；主通道改用移动端 feed 协议 `api5-normal-c-hl.amemv.com`（称 0% 403、覆盖 95% 以上普通视频；我们实测最高 720p，见 2.8），图集走 Web API + 指数退避 | 需要 App 协议方案、或评估国内拦截概率时 |
| media-parser 抖音解析器 | [docs/parsers/douyin.md](https://github.com/ucmao/media-parser/blob/main/docs/parsers/douyin.md)、`src/parsers/douyin_parser.py`（对比时为 `0b751170a`） | 通道：App feed → iesdouyin 分享页 SSR → web API（配置了 `UIFID` 才签 webSign）→ SSR；覆盖图集（取 `url_list[-1]` 无水印原图）、LivePhoto（称只有 web API 下发 `images[i].video`）、音乐、合集、放映厅 / 短剧、AI 字幕（`cla_info.caption_infos`）、短链与分享口令解析；Cookie 清洗（剔除 `bd_ticket_guard*`、`__security*`、`fpk*` 等，称可防 `Signature Not Found`；称普通作品带 `verify_` 开头的 `s_v_web_id` 会 403，未核实）。实测它给用户的视频是 HEVC 720p（2.8）；文档有几处与代码不符（排序、放映厅接口） | 扩展图集、LivePhoto、音乐、合集，或排查 Cookie 相关 403 时 |
| DouYin_Spider | [cv-cat/DouYin_Spider](https://github.com/cv-cat/DouYin_Spider) `utils/secsdk_web_sign.py` | `x-secsdk-web-signature` 纯算实现的出处（2026-08-30），附逆向路径、规范化规则、受保护接口清单（`aweme/detail`、`aweme/post`、`aweme/favorite`、`mix/aweme`、`tab/feed` 等） | 实现 webSign，或它失效时 |
| nous-app PR #2360 | [iocrazy/nous-app#2360](https://github.com/iocrazy/nous-app/pull/2360) | 实现 Argus webSign：`x-secsdk-web-signature`，Node 子进程跑 `websign_env.js` / `websign_runtime.js`，先 `a_bogus` 再 webSign，输入为带 `a_bogus` 的 URL、时间戳、`uifid`（实际上纯算即可，见 2.1 与 DouYin_Spider） | 纯算失效、需要跑真实 SDK 时 |
| amagi PR #188 | [ikenxuan/amagi#188](https://github.com/ikenxuan/amagi/pull/188) | ① 免鉴权接口：`iesdouyin.com/web/api/v2/user/info/`（用户名转 `sec_uid`）、`/v2/music/info/`、`/v2/music/list/aweme/`、`api.amemv.com/aweme/v1/im/resources/emoji/`；② `webid` 与会话 Cookie 不匹配时服务端静默返回 200 空响应，改为读响应头 `cookie_ttwidinfo_webid` 按 `ttwid` 缓存；③ `x-secsdk-web-signature` 为 32 位小写 hex、放在 query 中，只对 SDK 策略表中的部分路径生效，必须是最后一步；④ Argus 拦截时重新生成 `msToken` / `verifyFp` / `a_bogus` 线性退避重试最多 5 次 | 扩展到用户主页、音乐等接口，或遇到莫名 200 空响应时 |
| douyin-downloader | [jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader) | 说明 Argus 对非浏览器请求返回 `Uifid Not Found`，并称 webSign 只能在真实页面内生成（已被 DouYin_Spider 的纯算实现与我们的实测推翻）；主页批量翻页被拦时用 Playwright 启动真实浏览器滚动采集（默认有头，需手动过验证码） | 需要浏览器兜底方案时 |
| douyinie Issue #125 | [monet88/douyinie#125](https://github.com/monet88/douyinie/issues/125) | 汇总：`aweme/detail`、`aweme/post` 被 Argus 确定性拦截，应视为风控而非瞬时错误、不要盲目重试；源头为 douyin-downloader 2026-09-14 的提交 `47f4eef` | 了解 Argus 覆盖范围时 |
| yt-dlp 上游 issue #4138 | [yt-dlp/yt-dlp#4138](https://github.com/yt-dlp/yt-dlp/issues/4138) | TikTok 原片取法的历史：2022-06 `/aweme/v1/play` 的 `ratio=default` 还能取原片，2022-08 起要求 `file_id`（2.7） | TikTok 原片有新方案时 |
| yt-dlp 上游 #15690 | [yt-dlp/yt-dlp#15690](https://github.com/yt-dlp/yt-dlp/issues/15690) | TikTok 画质讨论，有人称带登录 Cookie 能稳定拿到 1080p（未说码率更高，未验证） | 测登录态 TikTok 画质时 |
| yt-dlp 上游 PR #15710 | [yt-dlp/yt-dlp#15710](https://github.com/yt-dlp/yt-dlp/pull/15710) | TikTok 先带 `X-Forwarded-For` 美国地址，失败再去掉重试（未合并） | TikTok 只剩一档或缺 1080 档时 |
| tiktok-hd-research | [vagvalas/tiktok-hd-research](https://github.com/vagvalas/tiktok-hd-research) | TikTok 高画质来源调研（todo.md 2026-06-03 认为「更高档存在但不下发给 web」），记录了第三方 tokcdn 的历史大小 | TikTok 原片 / 更高档调研 |
| tikwm、tikdownloader（tokcdn） | 第三方网站，不集成 | tikwm 的 HD 等于 web 最高档；tokcdn `_original` 时而像原片、时而只是更高转码档，随时间变化，来源不明（2.7、2.13.4） | 只作画质对照参考 |

TikTok 方向 2026-09-23 跨项目调研新增的参考项目（yt-dlp 上游各 issue / PR、gallery-dl、cobalt、Evil0ctal v5、JoeanAmier、f2、TikTok-Api、App 签名库群、streamlink 等）见 2.13.5，格式相同。

延伸阅读（a_bogus 逆向分析文章，未细看）：
[a_bogus技术交流（知乎）](https://zhuanlan.zhihu.com/p/661885504)、
[某音 a_bogus-1.0.1.19-fix.01 jsvmp 参数分析（知乎）](https://zhuanlan.zhihu.com/p/1923424071007310647)、
[抖音 a_bogus 算法还原大赏（博客园）](https://www.cnblogs.com/steed4ever/p/17688627.html)。

------

## 5. 待验证 / 后续方向

1. **电信被拦的原因**：国内移动 10/10 通过、电信 5/5 被拦，各只有一台机器的数据。
   同为巴西住宅 IP 也是有的拦、有的不拦（2.1），所以更可能是按客户端 IP 判断（电信那台是下载流水线机器，请求频繁），
   但尚未排除按边缘节点或运营商分批开启。现在 web API 在拦截 IP 上靠签名也能用，这一条只影响请求数，不再影响可用性。

   区分方法：在电信机器上 `nslookup www.douyin.com` 拿到它实际访问的节点 IP，
   从移动网络用 `curl --resolve www.douyin.com:443:<IP>` 把 web API 请求发到该节点（先确认 `via` 是 `CHN-...-CT5-...` 这类节点）。
   也被拦就是按节点，能过就是按客户端 IP。
2. **webSign 的长期稳定性**：盐常量来自安全 SDK 的虚拟机常量池，SDK 更新就可能变（表现为 `Sign Invalid`，3.1）；
   `UIFID_TEMP` 的复用期限只测到约 24 小时。可考虑的改进：
   - 短链解析时 HEAD `www.douyin.com/video/<id>` 也会下发 `UIFID_TEMP`，若在拦截 IP 上有效，可省掉精选页那个请求（未验证）；
   - embed_origin_api 被堵后，SSR 方案（2K）与 iesdouyin 分享页（2.11，免 `__ac`）仍可兜底；App feed 实测最高 720p，只作最后兜底（2.8）。
3. **原片**：
   - `v96-hcc` 带 Referer 即 403 只在 1 个视频的一次连续请求里观察到（2.9），复核时没再调度到该节点；
     反方向风险也未排除：若 `ratio=default` 某天跳到 `v26-web` 这类必须带 Referer 的节点，不带 Referer 会 403。
     可考虑下载 / 探测遇到 403 时换带 Referer 的请求重试一次；
   - 回退为转码档的规律不清楚（2021 年视频 4 个中 1 个），`te_is_reencode`、`transType`、`isFastImport` 等剪辑器标签的含义未核实；
   - 旋转：`mp4probe` 按 `tkhd` 矩阵换算显示宽高，ffmpeg 合成的 90° 样本结果正确，但抖音原片样本里没有带旋转矩阵的，真实文件未验证；
   - 海外出口的原片被调度到 `v5-dy-ov-experiment.zjcdn.com`，曾出现一次 TLS 建连失败（换出口后成功），偶发性未量化。
4. **TikTok**：业务机「1080p→540p」的分型与缓解（XFF US、`/api/item/detail/` 第二路径、换出口）只能在业务机上按 2.13.2.3 排查，本机出口是美国测不了；
   完整的未解决清单见 2.13.6。登录 Cookie「稳定 1080p」的说法复核不成立（2.13.2.1），只保留为实验项；签名 App API 在开源侧没有可用实现（2.13.4），
   若将来恢复，先看是否仍下发 `original_*` / `quality_type 10000`（上游 issue 7109）。
5. **其他 `filter_reason`**：遇到已删除、私密、地区限制的视频时，补充对应的取值和页面表现；
   7422307345595731236 在巴西代理下返回空 `filter_reason`，未用国内直连复测（2.5）。
6. **fork 尚不支持的内容**：图集（`/note/`、`/slides/`）、LivePhoto、音乐、合集、放映厅 / 短剧。
   embed_origin_api 对图集是否返回 `images` 与 LivePhoto 的 `images[i].video` 未测，可用 media-parser 样本清单里的真实作品 ID 测。
7. **疑点（只读代码，未实测）**：
   - 字幕：`DouyinIE` 沿用 TikTok 的 `cla_info` 字段名（`lang` / `Format`），可能与抖音实际字段不符；
     找不到字幕且有作者名时会用 TikTok 的 `_create_url` 去请求 tiktok.com 页面（只在 `--write-subs` / `--list-subs` 时触发）；
   - fork 自己生成 `verify_` 开头的 `s_v_web_id`，与 media-parser「普通作品带它会 403」的说法相反，需要 A/B；
   - 用户 `--cookies-from-browser` 带入的 `bd_ticket_guard*` 等字段是否影响 web API（media-parser 称会触发 `Signature Not Found`）。
8. **`webid`**：目前两个 API 策略都不带 `webid`；将来若加上，注意 amagi 发现的「`webid` 与会话不匹配则 200 空响应」。
