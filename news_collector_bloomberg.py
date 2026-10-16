# -*- coding: utf-8 -*-
"""彭博公开新闻采集器(2026-09-22):Google News RSS 聚合 site:bloomberg.com/news。

【背景】彭博官网对无头客户端全量 403(Imperva 人机验证,curl/WebFetch 均被拦),
      付费墙+ToS 使"官网直采"不现实。唯一稳定、可定时、零人工的路径 =
      Google News RSS 聚合(实测本机可达,返回真实彭博文章)。
      ⚠️ Google News RSS ToS:仅限个人非商用(personal, non-commercial use)。

【查询词三要点(实测校准 2026-09-22)】
  1) 必须限定 `site:bloomberg.com/news` —— 否则 `site:bloomberg.com` 会命中
     Bloomberg LEI 法律实体登记页(全是公司登记,非新闻,噪声占 100%);
  2) 追加 `-LEI` 排除(双保险,采集器仍按 source/title 再滤一层);
  3) Google News RSS 的 `after:` 时间算符无效(会让查询返回 0 条)⇒ 时效靠
     客户端过滤(默认只留 7 天内,env NEWS_MAX_AGE_DAYS)。

【URL】新版 payload(CBMi 开头)内层加密,无法还原彭博原文 URL ⇒ 落 google_url
      (可点击,Google 会自动跳转彭博原文),去重键 = google_url(每条稳定)。

【约定】产物文件 news_collect_result.json(带 mtime 闸,走 scheduler._capture_run);
      幂等:dedup_key UNIQUE,INSERT OR IGNORE;品种打标只打 21 品种池(英文关键词)。

用法:
  python news_collector_bloomberg.py            # 全量采集并入库
  python news_collector_bloomberg.py --dry-run  # 只解析打印,不入库
  python news_collector_bloomberg.py --limit 15 # 每查询最多取 N 条
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

SOURCE_LABEL = "彭博-GoogleNews聚合"
# 兼容键:数据库里 url 列存 google 跳转链接;展示/点击用同一字段
GOOGLE_HOST = "news.google.com/rss/articles"

QUERIES: list[tuple[str, str]] = [
    ("commodities", 'site:bloomberg.com/news (commodities OR "crude oil" OR copper OR gold OR "iron ore" OR steel) -LEI'),
    ("energy", 'site:bloomberg.com/news (oil OR gas OR LNG OR OPEC OR energy) -LEI'),
    ("china_economy", 'site:bloomberg.com/news (china OR chinese OR yuan OR fed OR economy OR tariffs) -LEI'),
    ("metals_agri", 'site:bloomberg.com/news (aluminum OR nickel OR lithium OR rubber OR cotton OR sugar OR soybean OR hogs) -LEI'),
]
RSS_URL = "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 25
RETRIES = 2
MAX_AGE_DAYS = int(os.environ.get("NEWS_MAX_AGE_DAYS") or "7")

# 品种池英文关键词(与 ACTIVE_VARIETIES 同源;命中即打标,未命中留空)
# 【2026-09-30】池由 20 → 21,补入 P 棕榈油(palm oil)。
VARIETY_KEYWORDS: dict[str, list[str]] = {
    "BR": ["butadiene", "synthetic rubber", "butadiene rubber"],
    "BU": ["bitumen", "asphalt"],
    "BZ": ["benzene"],
    "CF": ["cotton"],
    "CJ": ["apples"],
    "EB": ["styrene"],
    "EG": ["monoethylene glycol", "ethylene glycol", "MEG"],
    "FU": ["fuel oil"],
    "LC": ["lithium carbonate", "lithium"],
    "LH": ["live hogs", "hog futures", "hogs"],
    "LU": ["low sulfur fuel oil", "LSFO"],
    "M": ["soybean meal", "soymeal"],
    "NR": ["TSR20", "tsr 20", "20号胶"],
    "P": ["palm oil", "crude palm oil", "CPO", "Malaysian palm"],
    "PG": ["LPG", "liquefied petroleum gas"],
    "PS": ["polystyrene"],
    "PX": ["paraxylene", "PX"],
    "RU": ["natural rubber", "rubber"],
    "SC": ["crude oil", "OPEC"],
    "SI": ["polysilicon", "silicon"],
    "TA": ["PTA", "purified terephthalic acid"],
}


def _fetch_rss(query: str) -> str | None:
    url = RSS_URL.format(q=urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last_err: Exception | None = None
    for _ in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2)
    print(f"    ! RSS 拉取失败: {last_err}")
    return None


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").replace("&amp;", "&").replace("&quot;", '"').strip()


def _is_lei_noise(source: str, title: str) -> bool:
    """Bloomberg LEI 法律实体登记噪声(source='Bloomberg LEI' 或标题含 'LEI')。"""
    if "lei" in (source or "").lower():
        return True
    return bool(re.search(r"\bLEI\b", title or ""))


def _age_ok(pub_date: str) -> bool:
    """RSS pubDate(如 'Wed, 12 Feb 2025 18:50:43 GMT')转日期判时效;解析失败放行。"""
    if not pub_date or MAX_AGE_DAYS <= 0:
        return True
    try:
        dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z")
    except ValueError:
        try:
            dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S GMT")
        except ValueError:
            return True
    delta = datetime.now() - dt
    return 0 <= delta.days <= MAX_AGE_DAYS  # 未来时间/过期都滤掉


def _tag_varieties(text: str) -> str:
    low = (text or "").lower()
    hits = [code for code, kws in VARIETY_KEYWORDS.items() if any(k.lower() in low for k in kws)]
    return ",".join(sorted(set(hits)))


def parse_rss(xml_text: str, max_per_query: int) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"    ! RSS 解析失败: {e}")
        return items
    for node in root.iter("item"):
        if max_per_query and len(items) >= max_per_query:
            break
        title = _strip_html(node.findtext("title") or "")
        source = _strip_html(node.findtext("source") or "") or "Bloomberg"
        link = (node.findtext("link") or "").strip()
        if not title or _is_lei_noise(source, title):
            continue
        pub_date = (node.findtext("pubDate") or "").strip()
        if not _age_ok(pub_date):
            continue
        summary = _strip_html(node.findtext("description") or "")
        full_text = f"{title} {summary}"
        items.append({
            "source": SOURCE_LABEL,
            "title": title,
            "url": link,            # google 跳转链接(可点击 → 彭博原文)
            "google_url": link,
            "published_at": pub_date,
            "summary": summary[:600],
            "varieties": _tag_varieties(full_text),
            "dedup_key": link,       # google payload 每条稳定 → 去重键
        })
    return items


def collect(max_per_query: int = 30, dry_run: bool = False) -> dict:
    """执行采集,返回统计。dry_run 只打印不入库。"""
    from database import get_db  # 【调用包】懒导入,避免模块加载重

    stats = {"fetched": 0, "inserted": 0, "dup": 0, "failed": 0,
             "per_query": {}, "age_days": MAX_AGE_DAYS, "at": ""}
    all_items: list[dict] = []
    db = get_db()
    for label, q in QUERIES:
        xml_text = _fetch_rss(q)
        if not xml_text:
            stats["failed"] += 1
            continue
        items = parse_rss(xml_text, max_per_query=max_per_query)
        stats["per_query"][label] = len(items)
        all_items.extend(items)
        time.sleep(1.2)  # 温和限速,避免 Google 侧限流
    stats["fetched"] = len(all_items)

    # 组内去重(同一篇文章可能出现在多个查询里)
    seen: set[str] = set()
    uniq: list[dict] = []
    for it in all_items:
        if it["dedup_key"] in seen:
            stats["dup"] += 1
            continue
        seen.add(it["dedup_key"])
        uniq.append(it)

    if dry_run:
        print(f"[dry-run] 拉取 {len(all_items)} 条,去重后 {len(uniq)} 条(未入库):")
        for it in uniq[:8]:
            print(f"  · {it['published_at'][:16]:<18} {it['title'][:56]}")
            print(f"    → {it['url'][:80]}  [品种: {it['varieties'] or '未命中'}]")
        stats["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return stats

    for it in uniq:
        rid = db.insert_news(it)
        if rid:
            stats["inserted"] += 1
        else:
            stats["dup"] += 1
    stats["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return stats


def main() -> int:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    limit = 30
    for i, a in enumerate(args):
        if a == "--limit" and i + 1 < len(args):
            try:
                limit = max(1, int(args[i + 1]))
            except ValueError:
                pass
    print(f"[news_collector_bloomberg] mode={'dry-run' if dry_run else 'collect'} "
          f"queries={len(QUERIES)} limit/query={limit} max_age_days={MAX_AGE_DAYS}", flush=True)
    stats = collect(max_per_query=limit, dry_run=dry_run)
    print(f"[完成] fetched={stats['fetched']} inserted={stats['inserted']} "
          f"dup={stats['dup']} failed={stats['failed']}")
    if not dry_run:
        out = "news_collect_result.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=1)
        print(f"[产物] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
