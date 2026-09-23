## 目录结构

```
yt_dlp/extractor/
├── tiktok.py
└── tiktok_utils/
    ├── __init__.py
    ├── formats.py
    ├── docs/
    │   └── douyin-risk-control-research.md
    ├── tiktok/
    │   ├── __init__.py
    │   ├── websign.py
    │   ├── api.py
    │   └── LICENSE.Apache-2.0
    └── douyin/
        ├── __init__.py
        ├── constants.py
        ├── abogus.py
        ├── abogus_python.py
        ├── ac_signature.py
        ├── tokens.py
        ├── cookies.py
        ├── api.py
        ├── websign.py
        ├── mp4probe.py
        └── render_data.py

test/
├── test_tiktok_utils_websign.py          TikTok 签名器的离线向量测试
└── testdata/tiktok/websign_vectors.json  向量（Evil0ctal tests/unit/test_signing.py 的 TIKTOK_VECTORS + 浏览器抓包）
```

各模块职责如下：

```
tiktok_utils/formats.py
    TikTok / Douyin 共用的字节系 format 解析：
    - UrlKey 增强解析
    - vcodec 标准化
    - resolution / quality 归一化
    - PlayAddr / bitrateInfo / bitRateList 元信息提取

tiktok_utils/douyin/constants.py
    Douyin 固定 UA、host、默认 headers、embed_origin_api 的 open.douyin.com 来源 headers、__ac_signature 的 site 参数

tiktok_utils/douyin/abogus.py
    a_bogus 统一入口
    后续如果从 Python 算法切 JS 算法，只改这里的路由

tiktok_utils/douyin/abogus_python.py
    当前 Python 版 a_bogus + SM3 实现

tiktok_utils/douyin/ac_signature.py
    __ac_signature 生成与校验：
    - get_ac_signature
    - ac_signature_matches_nonce

tiktok_utils/douyin/tokens.py
    仅 Douyin 使用的本地 token：
    - generate_s_v_web_id
    - generate_ms_token

tiktok_utils/douyin/cookies.py
    Douyin Cookie 管理，允许依赖 InfoExtractor 实例：
    - ensure_douyin_visitor_cookies   ttwid / s_v_web_id / msToken，signed_web_api 与 ssr_render_data 都需要
    - ensure_douyin_ac_cookies        __ac_nonce / __ac_signature，仅 ssr_render_data 需要
    - get_douyin_uifid                webSign 用的 uifid：Cookie UIFID → UIFID_TEMP → yt-dlp 缓存
    - store_douyin_uifid / forget_douyin_uifid   缓存页面下发的 UIFID_TEMP / 作废被判无效的值
    - register_douyin_ttwid
    - fetch_douyin_home_cookies
    - clear_douyin_cookie
    - cookie_state_debug

tiktok_utils/douyin/api.py
    Douyin API 查询参数构造：
    - build_open_aweme_detail_query   embed_origin_api（免签名）
    - build_aweme_detail_query        signed_web_api
    - sign_aweme_detail_query
    - build_original_play_url         上传原片地址（/aweme/v1/play/?ratio=default）
    - response_snippet                失败原因日志用的响应体摘要

tiktok_utils/douyin/websign.py
    Argus 的 x-secsdk-web-signature（webSign）纯算实现：
    - canonicalize_query
    - sign_web_query                  返回签名后的 query，必须原样发送（不能再经 query= 重新编码）

tiktok_utils/douyin/mp4probe.py
    上传原片探测用的最小 MP4 / QuickTime 解析（不下载整个文件）：
    - parse_top_level                 文件头里的 brand 与顶层 box
    - parse_moov                      vcodec / acodec / 宽高 / fps / vbr / abr / dynamic_range

tiktok_utils/douyin/render_data.py
    Douyin ssr_render_data（精选页 SSR）：
    - make_jingxuan_url
    - extract_render_data_json
    - extract_video_detail

tiktok_utils/tiktok/websign.py
    TikTok 网页端 X-Dynosaur / X-Gnarly 签名的纯 Python 实现（仅标准库），TikTokIE 的 signed_web_api 渠道用：
    - sign_web_query                  输入有序 (key, value) 列表与 UA，返回签名后的完整 query，必须原样发送
    - sign / encode_query / seal / unseal 等原实现的函数原样保留，供离线向量测试与将来对照上游更新
    移植自 Evil0ctal/Douyin_TikTok_Download_API src/dtk/signing/native/tiktok_sign.py @ e02c0f3f5（Apache-2.0，
    许可证全文在同目录 LICENSE.Apache-2.0），文件头保留出处与许可证声明并注明改动；算法与常量未改。常量（ENV_CODE / UB_CODE / SDK_VERSION / SCM_VERSION）随 webmssdk 版本变，
    失效时的表现是 200 空 body + tt_orcas_res: 1

tiktok_utils/tiktok/api.py
    /api/item/detail/ 的参数与请求头：
    - build_item_detail_query         有序参数（aid=1988、device_platform=web_pc、region=US、user_is_login=false、itemId 等）
    - build_item_detail_url           签名后的完整 URL
    - build_item_detail_headers       UA（必须与签名用的一致）、Referer / Origin www.tiktok.com
```

这样后续替换点非常清晰：

| 变动                              | 修改位置                                              |
| --------------------------------- | ----------------------------------------------------- |
| `a_bogus` Python 算法换成 JS 执行 | `tiktok_utils/douyin/abogus.py` / 新增 `abogus_js.py` |
| `__ac_signature` 算法变化         | `tiktok_utils/douyin/ac_signature.py`                 |
| `ttwid` 注册逻辑变化              | `tiktok_utils/douyin/cookies.py`                      |
| webSign 盐或规范化规则变化        | `tiktok_utils/douyin/websign.py`                      |
| `uifid` 来源 / 缓存变化           | `tiktok_utils/douyin/cookies.py`                      |
| API 参数变化                      | `tiktok_utils/douyin/api.py`                          |
| RENDER_DATA 结构变化              | `tiktok_utils/douyin/render_data.py`                  |
| UrlKey 格式变化                   | `tiktok_utils/formats.py`                             |
| TikTok webmssdk 升版本（X-Dynosaur 常量失效） | `tiktok_utils/tiktok/websign.py`（对照 Evil0ctal 上游更新常量与字段表，再跑 `test/test_tiktok_utils_websign.py`） |
| TikTok `/api/item/detail/` 参数 / 请求头变化 | `tiktok_utils/tiktok/api.py`                          |

------

## 参考文档

- [docs/douyin-risk-control-research.md](docs/douyin-risk-control-research.md)：抖音 / TikTok 风控调研记录，
  包括各风控机制的实测结论、排查方法与诊断脚本、参考过的外部项目及其方案。

------

## DouyinIE 的三级策略

`DouyinIE._real_extract` 默认依次尝试 `signed_web_api` → `embed_origin_api` → `ssr_render_data`，
`--extractor-args "douyin:strategies=signed_web_api,embed_origin_api,ssr_render_data"` 可调整顺序或只用其中几条
（旧名 `web` / `open` / `webpage` 已废弃，会报错）。除最后一级外每级失败都打印 `Douyin <策略名> failed: <原因>`
（HTTP 状态码 + 响应体、空响应、`filter_detail` 等原始信息），全部失败时最终报错汇总各级原因。

1. **`signed_web_api`**：网页端接口 `aweme/v1/web/aweme/detail/`，`www.douyin.com` 来源 + `a_bogus`
    - Cookie 只需要访客 Cookie，实测只带 `ttwid` 即可，缺失时返回 200 + 空响应体；`a_bogus` 目前不校验，保留以防重新校验；
    - 部分 IP 会被 ArgusSecurityPlugin 拦截（按 IP 而定，海外居多，国内电信也遇到过）：`403 ... Uifid Not Found`。
      此时带 `uifid` 并按纯算法生成 `x-secsdk-web-signature`（`websign.py`）重试；`uifid` 取浏览器 Cookie `UIFID`、
      `UIFID_TEMP` 或缓存，都没有时从精选页取服务端下发的 `UIFID_TEMP`（该页面响应留给 `ssr_render_data` 复用），并写入 yt-dlp 缓存；
    - 排第一是因为它不依赖 open.douyin.com 的白名单口子。
2. **`embed_origin_api`**：同一接口，`Origin` / `Referer` 伪装成官方嵌入播放器所在的 `https://open.douyin.com`
    - 只需 `aweme_id` + `aid=6383`，不需要 `a_bogus`、Cookie，1 个请求完成；
    - 服务端对这个 `Origin` 按白名单跳过 Argus 与 `ttwid` 要求；这是利用口子，随时可能被堵，所以只作兜底；
    - 返回的 `aweme_detail` 与 `signed_web_api` 一致，格式为其超集（多几档 540p H.265），最高画质相同。
3. **`ssr_render_data`**：精选页 `jingxuan?modal_id=` 服务端渲染（SSR）的 `RENDER_DATA`
    - 需要 `__ac_nonce` + `__ac_signature`，二者不匹配时服务端返回「验证码中间页」，缺失时返回 JS 挑战页；
    - 服务端对签名只校验 nonce 哈希和末 2 位校验位，不校验 UA、site、时间戳、环境常量；
    - nonce 有效期 30 分钟，签名却长期残留，所以 `ensure_douyin_ac_cookies` 按
      `ac_signature_matches_nonce` 判断是否重算，而不是看签名是否存在。

转码档的 `http_headers` 必须带 `Referer`，否则 douyinvod 的 v26-web 等节点返回 403。
视频不存在时，两个 API 都返回 200 + `filter_detail.filter_reason`（如 `core_dep`），页面的 `videoDetail` 为 null；
因此 API 响应带 `filter_reason` 时直接报错「Douyin video is unavailable」，不再尝试后续策略。

水印版在三条策略下都是 `preference=-2`，排在所有转码档之下，`-f worst` 选中它。API 两级的水印版由与 TikTok 共用的
`_parse_aweme_video_app` 生成，它把传入的 -2 覆盖成了 -1，宽高也是占位的 720 加推算值，导致 `-S res` / `-S size` 会选中水印版；
所以在 `_build_douyin_api_info` 里按 `has_watermark` 恢复 -2 并去掉宽高。格式 ID 在 API 两级是 `download_addr`
（多镜像时为 `download_addr-0/1/2`），在 `ssr_render_data` 是 `download`，排除水印版要写 `[format_id!^=download]`
（`!=download` 匹配不到 `download_addr`）。`format_sort_force=True` 加 `-S size` 时水印版常因体积最大被选中（force 让用户规则越过
`preference`），同样用这个筛选排除。详见调研文档 2.12。

### 上传原片 original

`--extractor-args "douyin:original=true"`（默认 `false`）时额外列出上传原片 `original`（`/aweme/v1/play/?ratio=default`，
未转码，实测码率为最高转码档的 2.8–10.3 倍，少数老视频会回退为转码档）：

- 排序：`quality` 取「短边不超过原片的转码档」中最大的 `quality` 再加 0.5（留 16px 余量），排在同档转码档之上，所以默认选择就是它；
  两条路径都成立（`ssr_render_data` 的转码档没有档位名，`quality` 按实际短边补上）。`-S` 等用户排序规则同样作用于它，`-f worst` 仍是水印版；
- 探测：默认先读文件头与 moov（`mp4probe.py`），补全编码、大小、fps、HDR、容器（mov / mp4），并去掉回退成转码档的「假原片」。
  代价：国内直连每个视频多约 0.2–0.6 秒，海外代理 10–20 秒。探测的第一个请求失败时原片降到转码档之下，不作为默认；
  `douyin:original_probe=false` 可关闭，此时信息不全，若输出 info JSON（`--write-info-json` / `-j` / `-J`）整次运行警告一次；
- 原片跳转到的冷存储节点对带 `Referer` 的请求可能 403，所以它的 `http_headers` 为 `{}`。

链接除 `/video/<id>`、`?modal_id=` 外，还支持 `iesdouyin.com` / `m.douyin.com` 的 `/share/video/<id>` 与 `v.douyin.com` 短链；
`webpage_url` 统一为 `https://www.douyin.com/video/<id>`。

### extractor-args 配置示例（带注释）

Python API 写法（命令行等价写法见末尾）。每个值都必须是「字符串列表」，写成字符串会被拆成单个字符（`'true'` → `t`、`r`、`u`、`e`），
写成布尔值也会报错；两种写法都在发出任何请求之前报出可读错误。值不区分大小写。

```python
'extractor_args': {
    # 键名是小写 douyin，只对抖音生效，不影响 TikTok / YouTube 等其他 extractor
    'douyin': {

        # ── 提取策略，按顺序依次尝试，前一个失败才用下一个 ──────────────────────────────
        # 可选值（任意组合，顺序即尝试顺序；重复去重，空值按默认）：
        #   signed_web_api   网页端接口 aweme/v1/web/aweme/detail + a_bogus。被 Argus 拦截（403 Uifid Not Found）时
        #                    自动取 uifid（浏览器 Cookie UIFID → UIFID_TEMP → yt-dlp 缓存 → 请求精选页取新的）
        #                    并按 x-secsdk-web-signature 算法签名重试。不依赖任何白名单口子，所以排第一。
        #                    被拦的 IP 上第一个视频 5 个请求，之后每个 1 个；未被拦的 IP 2 个。
        #   embed_origin_api 同一接口，但 Origin 伪装成官方嵌入播放器的 open.douyin.com，服务端按白名单跳过校验。
        #                    1 个请求，但这是利用口子，随时可能被堵，只作兜底。
        #   ssr_render_data  精选页 jingxuan?modal_id= 服务端渲染出的 RENDER_DATA，依赖 __ac_nonce / __ac_signature。
        #                    格式档位比 API 少（约 12–15 档 vs 70 档），最高画质相同。
        # 默认：['signed_web_api', 'embed_origin_api', 'ssr_render_data']
        # 只写一个时，它失败就直接报错，不会退到其他策略；适合单独验证某条路径。
        # 旧名 web / open / webpage（2026-09-22 下午的版本）已废弃，传入会报错并列出新名字。
        # 每级失败打印 WARNING「Douyin <策略名> failed: <原因>」，全部失败时报错汇总各级原因；
        # 视频不存在（filter_reason）时直接报错，不再尝试后续策略。
        'strategies': ['signed_web_api', 'embed_origin_api', 'ssr_render_data'],

        # ── 是否列出上传原片 original ──────────────────────────────────────────────────
        # 可选值：'true' / 'false'。默认 'false'：格式列表里没有 original，行为与以前完全一致。
        # 'true'：额外列出 format_id='original'（/aweme/v1/play/?ratio=default），即作者上传的未转码文件：
        #   - 实测 17 个视频中 16 个是真原片，码率为最高转码档的 2.8–10.3 倍，分辨率、帧率不低于任何转码档；
        #   - 编码 / 容器随上传文件而定：多为 HEVC + MOV（.mov），也有 H.264 + MP4，偶有 HLG 10bit HDR；
        #   - 它按分辨率归档，排在同分辨率转码档之上，因此【成为默认选择】；-S 排序规则同样作用于它；
        #     -f worst 仍是水印版，不受影响；
        #   - 三条策略下都能拿到，与 strategies 的取值无关；
        #   - 老视频的原片可能已被清理，此时服务端悄悄返回转码档（17 个中 1 个），探测开启时会识别并去掉；
        #   - 原片的下载请求不带 Referer（冷存储节点 v96-hcc 带 Referer 会 403），转码档仍带，无需自己处理。
        # 后续上传 / 转码环节要能处理 .mov 和 HEVC。
        'original': ['true'],

        # ── 原片是否在下载前探测 ────────────────────────────────────────────────────────
        # 可选值：'true' / 'false'。默认 'true'。只在 original=true 时有意义。
        # 旧值 none / size / full 已废弃（原 full 即 true），传入会报错。
        # 'true'：多发 3 个 HTTP 请求（302 跳转、文件头 4KB、文件尾 moov），国内直连每个视频多约 0.2–0.6 秒，
        #   海外代理多约 10–20 秒。得到：
        #   - 精确 filesize、tbr（平均码率）、ext（mov / mp4）、vcodec、acodec、fps、vbr、abr、dynamic_range（SDR / HDR10 / HLG）；
        #   - 识别「假原片」（ratio=default 其实返回了转码档）并去掉，避免下错；
        #   - 探测的第一个请求失败时，原片保留但降到所有转码档之下、不作为默认，并打印 WARNING；
        #     只有 moov 读取失败时保留大小 / 码率 / 容器，缺编码与 fps，同样打印 WARNING。
        # 'false'：不多发请求，但只知道宽高：
        #   - vcodec 未知，best[vcodec!=none] 这类筛选会把原片排除（要放行需写 vcodec!=?none）；
        #   - -S vcodec / -S size 等规则对它无效；QuickTime 原片会被存成 .mp4；识别不了假原片；
        #   - 若同时输出 info.json（--write-info-json / -j / -J），整次运行打印一次 WARNING 提示元数据不全
        #     （设置了自定义 logger 时 yt-dlp 不去重，每个视频一次）。
        'original_probe': ['true'],
    },
},
```

与之相关的两个 yt-dlp 通用选项：

```python
# 缓存目录：signed_web_api 取到的 UIFID_TEMP 会存在 <cachedir>/douyin/uifid.json，下次运行直接复用
# （实测可复用至少 24 小时，跨视频、跨 IP）。cachedir=False（即 --no-cache-dir）则每次运行都重新取，
# 被拦的 IP 上每次多 3 个请求。
'cachedir': './yt_dlp_cache',

# 格式选择器：抖音 API 路径的水印版 format_id 是 download_addr / download_addr-N，
# [format_id!=download] 排除不掉它，要用 !^=（不以 download 开头），三条策略下都有效。
'format': 'bestvideo+bestaudio/best[vcodec!=none][format_id!^=download]',
```

命令行等价写法，多个键之间用 `;` 分隔：

```bash
yt-dlp --extractor-args "douyin:strategies=signed_web_api,embed_origin_api,ssr_render_data;original=true;original_probe=true" "https://www.douyin.com/video/<id>"
```

## TikTok：不改排序，档位缺失是服务端按地区下发

TikTok 拿不到上传原片（`/aweme/v1/play` 已要求 `file_id` + 签名，`ratio` 被忽略），能拿到的最好文件就是 web 的最高档。
格式排序走 yt-dlp 默认逻辑，fork 不设 `_format_sort_fields`（2026-09-22 曾加过「同分辨率优先体积大的档」，次日按用户要求撤回：
排序由用户在配置里用 `-S` 决定，extractor 不改默认选择）。

用户在业务中观察到的「前几天还能下到 1080p，最近只有 540p」是服务端下发的档位变少，与排序无关：
上游 [#15690](https://github.com/yt-dlp/yt-dlp/issues/15690) 记录了同类现象（非美国 IP 有时只给 `play` 一档或缺 `bytevc1_1080p`，
`--xff US` 或登录 Cookie 能拿回多档；现象是间歇性的），上游 PR [#15710](https://github.com/yt-dlp/yt-dlp/pull/15710)（未合并）
尝试默认带 `X-Forwarded-For: US` 失败再去掉。2026-09-23 对 yt-dlp 上游、gallery-dl、cobalt、Evil0ctal、JoeanAmier、f2、TikTok-Api、App 签名库等
做了跨项目调研：所有开源项目的数据源与档位上限都与 fork 相同，没有内置降档处理；排查顺序与落地分支见调研文档 2.13。
同日按用户要求接入了第二条数据渠道 `signed_web_api`（下一节）：即使档位相同，一条渠道被封时另一条可能仍可用。

------

## TikTokIE 的两条渠道

`TikTokIE._real_extract` 默认依次尝试 `webpage_hydration` → `signed_web_api`，
`--extractor-args "tiktok:strategies=webpage_hydration,signed_web_api"` 可调整顺序或只用其中一条（重复去重、空值按默认、未知名字在任何请求之前报错）。
除最后一级外每级失败都打印 `TikTok <策略名> failed: <原因>`，全部失败时报错 `Unable to extract TikTok video info. <策略名>: <原因>; ...`。
给了 `tiktok:app_info` / `device_id` 时仍先按上游逻辑尝试 App API（`_extract_aweme_app`），失败再走这两条，行为未改。
两条渠道返回同一结构的 `itemStruct`，都交给 `_parse_aweme_video_web` 解析，排序、默认选择相同。对比（2026-09-23 实测，细节与证据见调研文档 2.7.1）：

| | `webpage_hydration`（网页） | `signed_web_api`（接口） |
| --- | --- | --- |
| 数据来源 | `GET www.tiktok.com/@用户/video/<id>` 页面里的 `__UNIVERSAL_DATA_FOR_REHYDRATION__` | `GET www.tiktok.com/api/item/detail/?itemId=<id>&…` |
| 反爬手段 | curl_cffi TLS 伪装 + 随机垃圾请求头 + 纯 Python 解 WAF 挑战（上游维护） | X-Dynosaur / X-Gnarly 纯算签名（移植 Evil0ctal），UA 必须与签名一致；默认不做 TLS 伪装 |
| 前置条件 | 不需要 Cookie；依赖 curl_cffi（缺失时只警告） | 不需要 Cookie、不依赖 curl_cffi；`msToken` 留空，`device_id` 随机 19 位 |
| 取元数据的请求数 | 1 个；遇到 WAF 挑战页 2 个 | 1 个 |
| 视频档位 | 相同（4 个样本逐档一致，含只剩 540p 的那个） | 相同 |
| 下载地址 | 直连 `v16 / v19-webapp-prime`，每档 2 个镜像；下载要带页面下发的会话 Cookie（`tt_chain_token`），yt-dlp 自动带 | 直连地址一律 403，只用 `www.tiktok.com/aweme/v1/play` 镜像（302 到 CDN，不需要 Cookie）；每档 1 个地址 |
| 下载前额外请求 | 无 | 1 次探测（`__needs_testing`，镜像 302 + 206） |
| 格式列表 | 各档（`-0/-1` 后缀）+ `play` + 水印版 `download` + `audio` | 各档（无后缀）+ `audio`；无水印版 |
| 元数据 | 完整 itemStruct | 少 11–15 个键（`challenges`、`textExtra`、评论等）；标题、作者、时长、计数、缩略图一致，标签类字段可能缺 |
| 被封的方式 | TLS 指纹被封（上游 issue 17604）、挑战解不出、风控拦截页（`X-TT-System-Error: 3`）、可疑 IP 被 302 到登录页 | 签名常量随 SDK 版本失效（200 空 body + `tt_orcas_res: 1`）、验证信封（`statusCode 10000`）、镜像返回 HTML |
| 失效后怎么修 | 跟上游 | 对照 Evil0ctal 上游更新 `websign.py` 常量，跑 `test/test_tiktok_utils_websign.py` |
| 单次耗时（本机） | 页面约 1–1.6 秒（含伪装握手） | 接口约 0.8 秒 |

两条渠道同一出口：服务端按 IP 做的降档或封锁会一起中招。冗余针对的是「某条路径的反爬机制被改」，不是「IP 被针对」。

1. **`webpage_hydration`**：上游路径，视频页 `__UNIVERSAL_DATA_FOR_REHYDRATION__` 里的 `webapp.video-detail.itemInfo.itemStruct`
    - curl_cffi TLS 伪装（`impersonate=True`；没装 curl_cffi 时只警告一次并不伪装，上游 #17480 的经验是不伪装多半被拒）、随机垃圾头、
      纯 Python 解 WAF 挑战（`_wafchallengeid`），1–2 个请求；
    - 失败原因现在带响应特征：`Unexpected response from webpage request (HTTP 200, 612 bytes, blocked by risk control (X-TT-System-Error: 3))`
      表示被风控拦截页（不是视频不可用），`Unable to solve JS challenge (...)` / `Unable to extract universal data for rehydration (...)` 是挑战或页面结构问题；
      HTTP 错误原样透传（`Unable to download webpage: HTTP Error 403: Forbidden`）。这些都会回退到下一渠道。
2. **`signed_web_api`**：`GET www.tiktok.com/api/item/detail/?itemId=<id>&...&X-Dynosaur=...&msToken=&X-Bogus=1&X-Gnarly=...`
    - 签名由 `tiktok_utils/tiktok/websign.py` 纯算（移植自 Evil0ctal，Apache-2.0），无 Cookie、`msToken` 留空（不伪造）、
      `device_id` 用 `tiktok:device_id` 或与列表接口相同的随机 19 位数，1 个请求；
    - 默认不做 TLS 伪装、不依赖 curl_cffi（全局 `--impersonate` 时随之），与网页路径的封锁面（伪装目标被封、挑战页、拦截页）相互独立；
    - **下载地址只用 `www.tiktok.com/aweme/v1/play` 镜像**：接口返回的直连 `v16 / v19-webapp-prime` 地址在这条渠道下下载一律 403
      （带网页会话的 Cookie 也一样，实测），镜像 302 到 CDN 后无 Cookie 也能下；每档只保留镜像，没有镜像的水印版 `download` 不列出，
      音频不受影响。镜像 2024 年曾返回 HTML 页面（上游 issue 11034，网页渠道因此过滤它），所以标 `__needs_testing`，选中前探一次；
    - 端点只校验 X-Dynosaur，X-Gnarly 的正确性只能靠离线向量测试保证；签名常量随 webmssdk 版本变，失效表现是
      `HTTP 200 with empty body: signature or device rejected (tt_orcas_res=1)`；`verification required (statusCode 10000)` 是验证信封；
      两者都会回退到下一渠道；
    - 响应里 `DataSize` 是整数（页面是字符串），`formats.py` 统一用 `int_or_none`，不需要区分。

**不回退的情况**（服务端明确说不可用，两条渠道同义）：`statusCode` 10216 / 10222 需登录（`raise_login_required`）、10204 IP 被封
（`Your IP address is blocked from accessing this post`）、其他非 0 值 `Video not available, status code N`，以及 `statusCode` 0 但 `itemStruct`
没有 `video` 且 `isContentClassified`（敏感内容需登录，两条渠道同样判定）。
**会回退的情况**：`statusCode` 0 但没有 `itemStruct`（页面 / 接口结构问题）、`statusCode` 10000（验证信封）、视频页 302 到 `/login`
（多是对可疑 IP 的页面级风控，不是内容需登录，接口往往仍能返回）。

### extractor-args 配置示例（带注释）

Python API 写法（命令行等价写法见末尾）。与 douyin 一样，每个值都必须是「字符串列表」，写成字符串或布尔值会在发出任何请求之前报出可读错误；值不区分大小写。

```python
'extractor_args': {
    # 键名是小写 tiktok，只对 TikTokIE（单个视频）生效；用户页 / 合集等其他 TikTok extractor 仍只走它们自己的接口
    'tiktok': {

        # ── 提取策略，按顺序依次尝试，前一个失败才用下一个 ──────────────────────────────
        # 可选值（任意组合，顺序即尝试顺序；重复去重，空值按默认）：
        #   webpage_hydration  视频页 __UNIVERSAL_DATA_FOR_REHYDRATION__（上游路径）。curl_cffi TLS 伪装 + 随机头 + WAF 挑战求解，
        #                      1–2 个请求；依赖 curl_cffi 做 TLS 伪装（缺失时只警告、不伪装）。被拦截页 / 挑战失败 / HTTP 错误时打印具体原因并回退。
        #   signed_web_api     www.tiktok.com/api/item/detail/，X-Dynosaur / X-Gnarly 纯算签名，无 Cookie、msToken 为空，
        #                      1 个请求；不依赖 curl_cffi。签名常量随 TikTok SDK 版本失效时表现为 200 空 body（tt_orcas_res=1），回退。
        #                      下载地址只用 www.tiktok.com/aweme/v1/play 镜像（直连 CDN 地址在该渠道下 403），选中前多探 1 次；
        #                      水印版 download 在该渠道不列出。
        # 两条渠道的格式列表与默认选择相同；默认：['webpage_hydration', 'signed_web_api']。
        # 只写一个时，它失败就直接报错，不会退到另一条；适合单独验证某条路径。
        # 每级失败打印 WARNING「TikTok <策略名> failed: <原因>」，全部失败时报错汇总各级原因；
        # 需登录 / IP 被封 / 视频不存在时直接报错，不再尝试后续策略。
        # 上游原有的 app_info / device_id / api_hostname 等参数不变；给了 app_info 时仍先试 App API 再走这里的策略。
        'strategies': ['webpage_hydration', 'signed_web_api'],
    },
},
```

命令行等价写法：

```bash
yt-dlp --extractor-args "tiktok:strategies=webpage_hydration,signed_web_api" "https://www.tiktok.com/@<user>/video/<id>"
```

------

## Cookie 管理可以依赖 InfoExtractor

因为 Cookie 获取本质上要走 yt-dlp 的网络层，才能自动继承：

```
--proxy
--socket-timeout
--source-address
--impersonate
--sleep-requests
cookiejar
请求重试和日志逻辑
```

所以 `cookies.py` 可以设计成“接收 `ie` 实例”的 helper，而不是纯函数模块。

------

## a_bogus 入口要为后续 JS 方案预留

建议 `abogus.py` 只做统一入口，不放具体算法。

后续想换 JS，只需要新增 `tiktok_utils/douyin/abogus_js.py`，然后把入口改成：

```
from .abogus_js import generate_abogus_js

def generate_abogus(params, user_agent):
    return generate_abogus_js(params, user_agent)
```

或者做可配置：

```
def generate_abogus(params, user_agent, method='python'):
    if method == 'js':
        return generate_abogus_js(params, user_agent)
    return generate_abogus_python(params, user_agent)
```

但第一版建议先不要加配置项，避免复杂化。

------

## 保留 `DouyinIE` 在 `tiktok.py`

这点建议坚持，不要做成 `yt_dlp/extractor/tiktok_utils/douyin/ie.py`。

原因：

1. yt-dlp 官方当前就是 `TikTokIE`、`DouyinIE` 都在 `extractor/tiktok.py`。
2. 后续同步官方时，`DouyinIE` 的 diff 更直观。
3. extractor 注册逻辑不需要调整。
4. 只把 helper 拆出去，不破坏官方文件组织方式。

最终效果应该是：

```
tiktok.py
    保留官方主体结构
    DouyinIE 只做编排和 yt-dlp extractor 交互

tiktok_utils/douyin/*.py
    放可替换的算法、Cookie、参数、页面解析工具
```
