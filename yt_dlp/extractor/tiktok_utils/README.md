## 目录结构

```
yt_dlp/extractor/
├── tiktok.py
└── tiktok/
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
tiktok/formats.py
    TikTok / Douyin 共用的字节系 format 解析：
    - UrlKey 增强解析
    - vcodec 标准化
    - resolution / quality 归一化
    - PlayAddr / bitrateInfo / bitRateList 元信息提取

tiktok/douyin/constants.py
    Douyin 固定 UA、host、默认 headers、ttwid 注册 payload 等常量

tiktok/douyin/abogus.py
    a_bogus 统一入口
    后续如果从 Python 算法切 JS 算法，只改这里的路由

tiktok/douyin/abogus_python.py
    当前 Python 版 a_bogus + SM3 实现

tiktok/douyin/ac_signature.py
    __ac_signature 生成逻辑

tiktok/douyin/tokens.py
    仅 Douyin 使用的本地 token：
    - generate_s_v_web_id
    - generate_ms_token

tiktok/douyin/cookies.py
    Douyin Cookie 管理，允许依赖 InfoExtractor 实例：
    - ensure_douyin_cookies
    - register_douyin_ttwid
    - fetch_douyin_home_cookies
    - build_douyin_cookie_header
    - cookie_state_debug

tiktok/douyin/api.py
    Douyin API 查询参数构造：
    - build_aweme_detail_query
    - sign_aweme_detail_query

tiktok/douyin/render_data.py
    Douyin 页面方案：
    - make_jingxuan_url
    - extract_render_data_json
    - extract_videodetail
```

这样后续替换点非常清晰：

| 变动                              | 修改位置                                        |
| --------------------------------- | ----------------------------------------------- |
| `a_bogus` Python 算法换成 JS 执行 | `tiktok/douyin/abogus.py` / 新增 `abogus_js.py` |
| `__ac_signature` 算法变化         | `tiktok/douyin/ac_signature.py`                 |
| `ttwid` 注册逻辑变化              | `tiktok/douyin/cookies.py`                      |
| API 参数变化                      | `tiktok/douyin/api.py`                          |
| RENDER_DATA 结构变化              | `tiktok/douyin/render_data.py`                  |
| UrlKey 格式变化                   | `tiktok/formats.py`                             |

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



## a_bogus 入口要为后续 JS 方案预留

建议 `abogus.py` 只做统一入口，不放具体算法。

### `tiktok/douyin/abogus.py`

后续想换 JS，只需要新增：

```
tiktok/douyin/abogus_js.py
```

然后把入口改成：

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

### 

## 保留 `DouyinIE` 在 `tiktok.py` 

这点建议坚持。

不要做成：

```
yt_dlp/extractor/tiktok/douyin/ie.py
```

原因：

1. yt-dlp 官方当前就是 `TikTokIE`、`DouyinIE` 都在 `extractor/tiktok.py`。
2. 后续 rebase 官方时，`DouyinIE` 的 diff 更直观。
3. extractor 注册逻辑不需要调整。
4. 只把 helper 拆出去，不破坏官方文件组织方式。

最终效果应该是：

```
tiktok.py
    保留官方主体结构
    DouyinIE 只做编排和 yt-dlp extractor 交互

tiktok/douyin/*.py
    放可替换的算法、Cookie、参数、页面解析工具
```

------

## 