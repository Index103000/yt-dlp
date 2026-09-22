## 目录结构

```
yt_dlp/extractor/
├── tiktok.py
└── tiktok_utils/
    ├── __init__.py
    ├── formats.py
    ├── docs/
    │   └── douyin-risk-control-research.md
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

## TikTok 网页路径的排序

TikTok 拿不到上传原片（`/aweme/v1/play` 已要求 `file_id` + 签名，`ratio` 被忽略），能拿到的最好文件就是 web 的最高档。
`_parse_aweme_video_web` 设 `'_format_sort_fields': ('quality', 'res', 'size', 'br')`：同分辨率下体积大的档优先，
不再因为编码排序先选中低码率 HEVC（hankgreen1 的 VMAF 由 92.09 提高到 98.58）。详见调研文档 2.7。

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
