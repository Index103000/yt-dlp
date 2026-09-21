## 目录结构

```
yt_dlp/extractor/
├── tiktok.py
└── tiktok_utils/
    ├── __init__.py
    ├── formats.py
    └── douyin/
        ├── __init__.py
        ├── constants.py
        ├── abogus.py
        ├── abogus_python.py
        ├── ac_signature.py
        ├── tokens.py
        ├── cookies.py
        ├── api.py
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
    Douyin 固定 UA、host、默认 headers、open API 来源 headers、__ac_signature 的 site 参数

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
    - ensure_douyin_visitor_cookies   ttwid / s_v_web_id / msToken，两条链路都需要
    - ensure_douyin_ac_cookies        __ac_nonce / __ac_signature，仅页面方案需要
    - register_douyin_ttwid
    - fetch_douyin_home_cookies
    - clear_douyin_cookie
    - cookie_state_debug

tiktok_utils/douyin/api.py
    Douyin API 查询参数构造：
    - build_open_aweme_detail_query   open API（免签名）
    - build_aweme_detail_query        web API
    - sign_aweme_detail_query
    - response_snippet                失败原因日志用的响应体摘要

tiktok_utils/douyin/render_data.py
    Douyin 页面方案：
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
| API 参数变化                      | `tiktok_utils/douyin/api.py`                          |
| RENDER_DATA 结构变化              | `tiktok_utils/douyin/render_data.py`                  |
| UrlKey 格式变化                   | `tiktok_utils/formats.py`                             |

------

## DouyinIE 的三级策略

`DouyinIE._real_extract` 依次尝试，每一级失败都打印一条带原因的 WARNING
（HTTP 状态码 + 响应体、空响应、`filter_detail` 等原始信息），全部失败时最终报错汇总三级原因。

1. **open API**：`aweme/v1/web/aweme/detail/`，`Origin` / `Referer` 为 `https://open.douyin.com`
    - 只需 `aweme_id` + `aid=6383`，不需要 `a_bogus`、Cookie，1 个请求完成；
    - 不经过 Argus 的 uifid / 签名校验，海外 IP 与国内 IP 均可用；
    - 返回的 `aweme_detail` 与带 `a_bogus` 的 web API 一致，格式为其超集（多几档 540p H.265），最高画质相同。
2. **web API**：同一接口，`www.douyin.com` 来源 + `a_bogus`
    - Cookie 只需要访客 Cookie，实测只带 `ttwid` 即可；`a_bogus` 或 `ttwid` 不被接受时返回 200 + 空响应体；
    - 海外 IP 被拦截：`403 Blocked by ArgusSecurityPlugin Uifid Not Found`，
      补上 `uifid` query 参数后变为 `Signature Not Found`（还需要页面内 SDK 生成的 `x-secsdk-web-signature`）。
3. **webpage**：精选页 `jingxuan?modal_id=` 的 SSR `RENDER_DATA`
    - 需要 `__ac_nonce` + `__ac_signature`，二者不匹配时服务端返回「验证码中间页」，缺失时返回 JS 挑战页；
    - 服务端对签名只校验 nonce 哈希和末 2 位校验位，不校验 UA、site、时间戳、环境常量；
    - nonce 有效期 30 分钟，签名却长期残留，所以 `ensure_douyin_ac_cookies` 按
      `ac_signature_matches_nonce` 判断是否重算，而不是看签名是否存在。

两个 API 策略的格式都需要 `http_headers` 带 `Referer`，否则 douyinvod 的 v26-web 等节点返回 403。
视频不存在时，两个 API 都返回 200 + `filter_detail.filter_reason=core_dep`，页面的 `videoDetail` 为 null。

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
