"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.
"""

from __future__ import annotations  # 【调用包】延迟求值注解

import re  # 【调用包】正则:编译"Rating:"标签匹配与词级匹配模式

# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: tuple[str, ...] = (  # 【变量】规范的五档评级元组(最看多→最看空),全系统统一避免口径漂移
    "Buy",
    "Overweight",
    "Hold",
    "Underweight",
    "Sell",
)

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}  # 【变量】小写化评级集合,用于大小写无关的快速匹配

# Matches "Rating: X" / "rating - X" / "Rating: **X**" — tolerates markdown
# bold wrappers and either a colon or hyphen separator.
_RATING_LABEL_RE = re.compile(r"rating.*?[:\-][\s*]*(\w+)", re.IGNORECASE)  # 【变量】预编译"Rating: X"标签正则(容忍粗体星号与冒号/连字符分隔)


# 【功能】从散文文本中启发式提取五档评级(Buy/Overweight/Hold/Underweight/Sell)。
# 【参数】text: 待解析文本;default: 未命中任何评级时返回的默认值,默认 "Hold"。
# 【返回】Title 首字母大写化的评级字符串;未命中时返回 default。
# 【关键】两趟策略:先找显式"Rating: X"标签,再退化为全文首个五档评级词。
def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from prose text.

    Two-pass strategy:
    1. Look for an explicit "Rating: X" label (tolerant of markdown bold).
    2. Fall back to the first 5-tier rating word found anywhere in the text.

    Returns a Title-cased rating string, or ``default`` if no rating word appears.
    """
    for line in text.splitlines():
        m = _RATING_LABEL_RE.search(line)
        if m and m.group(1).lower() in _RATING_SET:
            return m.group(1).capitalize()

    for line in text.splitlines():
        for word in line.lower().split():
            clean = word.strip("*:.,")  # 【变量】去掉词首尾的粗体星号/冒号/逗号后,参与评级集合匹配
            if clean in _RATING_SET:
                return clean.capitalize()

    return default
