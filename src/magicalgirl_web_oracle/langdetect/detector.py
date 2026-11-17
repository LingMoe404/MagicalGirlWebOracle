"""Tiny heuristic language / CN-internet-topic detector.

Zero-dependency: enough characters of CJK => zh; Korean/other detection is not
needed here. CN-topic keywords raise the 'china relevance' score that the
router uses to boost SearXNG Chinese engine groups.
"""

from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

_CN_TOPIC_KEYWORDS = [
    # Domestic internet / companies / products / policy / platforms
    "百度", "阿里", "腾讯", "字节", "抖音", "哔哩", "b站", "bilibili", "知乎",
    "微信", "公众号", "小红书", "夸克", "搜狗", "360", "京东", "淘宝", "拼多多",
    "华为", "小米", "大疆", "宁德时代", "比亚迪",
    "国产", "国内", "大陆", "中国", "北京", "上海", "深圳", "杭州", "广州",
    "政务", "政策", "工信部", "网信办", "发改委", "国务院",
    "微信公众号", "通义", "文心", "千问", "deepseek", "kimi", "智谱", "moonshot",
    "百度网盘", "开发票", "工信部备案",
]


def detect_language(text: str) -> str:
    """Very cheap heuristic: 'zh' if enough CJK chars, else 'en'."""
    if not text:
        return "en"
    cjk = len(_CJK_RE.findall(text))
    if cjk >= 2 and cjk >= 0.25 * max(1, len(text)):
        return "zh"
    if cjk >= 6:
        return "zh"
    return "en"


def china_relevance(text: str) -> float:
    """0..1 score of how much a query concerns the Chinese internet."""
    low = text.lower()
    if not low:
        return 0.0
    hits = sum(1 for kw in _CN_TOPIC_KEYWORDS if kw in low)
    lang = detect_language(text)
    score = min(1.0, hits * 0.35)
    if lang == "zh":
        score = max(score, 0.45)
    # Pure-English queries mentioning cn-specific words still count.
    if hits >= 2:
        score = max(score, 0.7)
    return round(min(1.0, score), 3)
