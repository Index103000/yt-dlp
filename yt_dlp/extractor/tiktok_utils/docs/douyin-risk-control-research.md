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
- 没看到按地区降码率：美国与巴西出口的 (GearName, DataSize, FileHash) 集合完全一致，只是 CDN 域名不同。
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
| tikwm、tikdownloader（tokcdn） | 第三方网站，不集成 | tikwm 的 HD 等于 web 最高档；tokcdn `_original` 时而像原片、时而只是更高转码档，随时间变化，来源不明（2.7） | 只作画质对照参考 |

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
4. **TikTok**：登录态 Cookie（`sid_tt` 等）下 web 是否给出更高的 1080 档；带真实 device_register 与 X-Gorgon 等签名的 App API
   能否拿到比 web 更高的档（web 最高 1080 档都叫 `adapt_lowest_1080_1`，暗示存在更高档）。两者都没测（2.7）。
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
