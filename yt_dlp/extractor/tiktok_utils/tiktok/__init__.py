# yt_dlp/extractor/tiktok_utils/tiktok/__init__.py
"""
TikTok-specific helper modules.

说明：
1. TikTokIE 仍位于 yt_dlp/extractor/tiktok.py，与上游相同；
2. 本包只放 signed_web_api 渠道（www.tiktok.com/api/item/detail/）的可替换实现：
   websign.py 是 X-Dynosaur / X-Gnarly 签名（移植自 Evil0ctal，Apache-2.0），api.py 是参数与请求头；
3. 网页 hydration 渠道（webpage_hydration）沿用上游 TikTokBaseIE 的代码，不在本包。
"""
