# -*- coding: utf-8 -*-
"""隆众资讯内容采集 —— 资讯入库链路的「采集」一步

接进既有研报链路: 采集(本脚本) → oilchem_ingest.py 入库 → research_reports + RAG。
镜像 wechat_album_collector.py / wechat_ingest.py 的分工与产物约定。

================================ 为什么不需要浏览器 ================================
隆众**资讯层是服务端渲染 + 免登录**的(与"现货价格/数据终端"层完全不同,那层要登录):
  · 列表: GET https://list.oilchem.net/1/32458/  (翻页 /N.html,3335 条 / 167 页)
  · 正文: GET https://www.oilchem.net/<slug>.html
实测纯 urllib 即可拿到完整正文 ⇒ **不用 playwright**,采集极轻量。
⚠️ 但隆众对连续请求会 429(实测连打 5~6 次即触发) ⇒ 必须限速(默认 8s)+ 退避重试。

================================ 与公众号链路的根本差异 ================================
公众号要处理"号/合集/签名 URL"的复杂性;隆众是**单一来源 + 稳定 slug**,因此:
  · 去重键 = 文章 slug(如 `26-1014-07-4fd110fd92eea5a7`,URL 里的内容哈希),
    **不用 url** —— 与公众号同理,只认稳定键;
  · `account` 字段 = 内容类别(早读/聚焦/成品油…),让 source 落成 `隆众资讯-早读` 这样可读的徽标。

================================ ★ 不是所有文章都公开(实测) ================================
抽样 6 篇: 5 篇公开、1 篇被登录墙拦(`[PA]` 这类单品种短资讯)。
⇒ **必须逐篇判定**,不能"列表能打开"就假定全可读。
判据 = 正文字符串命中 WALL_MARKERS(被墙页也是 HTTP 200,只看状态码会把受限内容当成功)。
被墙文章仍写入 seen 水位线(避免每轮重抓),但**不进 inbox**,不烧 LLM。

用法:
    python oilchem_news_collect.py                    # 默认:扫前 2 页,增量
    python oilchem_news_collect.py --pages 5          # 多扫几页
    python oilchem_news_collect.py --dry-run          # 只看会采什么,不写盘
    python oilchem_news_collect.py --urls <url>[,<url>...]   # 强制采指定文章(调试用)
    python oilchem_news_collect.py --state-dir <dir>  # 覆盖状态目录

产出(状态目录默认 ~/.tradingagents/oilchem,env AGENTSENSE_OILCHEM_DIR 可覆盖):
    oilchem_news.jsonl          追加式归档(崩溃安全,入库读它)
    oilchem_news.json           最近一轮的新文(会覆盖,仅供查看)
    oilchem_seen.json           水位线(已见 slug → 发布日期)
    oilchem_news_result.json    机器可读结果(供调度器取数)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")}

NEWS_LIST = "https://list.oilchem.net/1/32458"   # 【常量】热点资讯频道(聚合了早读/聚焦/品种资讯)
ART_BASE = "https://www.oilchem.net"             # 【常量】正文站点

# 【常量】登录墙判据。被墙页**仍是 HTTP 200**,所以只能靠正文关键词判定。
WALL_MARKERS = ("免费浏览权", "请您致电", "注册为会员", "开通会员")

MIN_BODY_CHARS = 200        # 【常量】正文最小字符数(与 wechat_ingest 同阈值,提前挡掉短讯)
MEMBER_MIN_CHARS = 200      # 【常量】"会员可见"的兜底阈值

STATE_DIR = Path(os.path.expanduser((os.environ.get("AGENTSENSE_OILCHEM_DIR") or "").strip())
                 or (Path.home() / ".tradingagents" / "oilchem"))
F_INBOX = STATE_DIR / "oilchem_news.jsonl"
F_NEW = STATE_DIR / "oilchem_news.json"
F_SEEN = STATE_DIR / "oilchem_seen.json"
F_RESULT = STATE_DIR / "oilchem_news_result.json"

_last_call = [0.0]   # 【变量】上次请求时刻(全局限速用)


# ── HTTP ───────────────────────────────────────────────────────────────
def fetch(url: str, delay: float = 8.0, retries: int = 3) -> str:
    """带全局限速 + 429 退避的 GET;失败返回 'ERR ...' 字符串(不抛)。

    【为什么必须限速+退避】实测连续 5~6 次请求即 429。若不退避,采集器会被限流成
    "静默 0 条"—— 日志干净、退出码 0,但什么都没采到(最难发现的失效形态)。
    """
    for attempt in range(retries):
        wait = delay - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        try:
            _last_call[0] = time.time()
            req = urllib.request.Request(url, headers=UA)
            return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                back = delay * (attempt + 2)
                print("    [429 限流] 退避 %.0fs 后重试 (%d/%d)" % (back, attempt + 1, retries), flush=True)
                time.sleep(back)
                continue
            return "ERR HTTP %s" % e.code
        except Exception as e:  # noqa: BLE001
            return "ERR " + str(e)[:140]
    return "ERR 429 持续限流"


# ── 解析 ───────────────────────────────────────────────────────────────
_SLUG_RE = re.compile(r"/(\d{2}-\d{4}-\d{2}-[0-9a-f]+)\.html")


def parse_list(html: str) -> list[tuple[str, str]]:
    """从列表页解析 [(slug, title)]。slug 是稳定去重键。"""
    pairs = re.findall(r'href=["\']([^"\']*?/\d{2}-\d{4}-\d{2}-[0-9a-f]+\.html)["\'][^>]*>([^<]{6,160})<',
                       html)
    out, seen = [], set()
    for url, title in pairs:
        m = _SLUG_RE.search(url)
        if not m:
            continue
        slug = m.group(1)
        title = unescape(title).strip()
        if slug not in seen and title:
            seen.add(slug)
            out.append((slug, title))
    return out


def categorize(title: str) -> str:
    """把标题归到内容类别(同时用作 account → source 徽标)。

    【为什么按标题判】隆众没有公开的分类字段,但标题自带强前缀约定:
      `隆众能化早读：…`        → 早读
      `[隆众聚焦]：…` / `[热点聚焦]：…` → 聚焦
      `[成品油]：…`            → 成品油(取方括号内)
      其它                     → 资讯
    """
    if "早读" in title:
        return "早读"
    if "聚焦" in title:
        return "聚焦"
    m = re.match(r"^\[([^\]\[]{1,12})\]", title)
    if m:
        return m.group(1).strip()
    if "周报" in title:
        return "周报"
    if "月报" in title:
        return "月报"
    return "资讯"


def strip_tags(html: str) -> str:
    html = re.sub(r"<script[\s\S]*?</script>", " ", html)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html)
    return unescape(re.sub(r"<[^>]+>", " ", html))


def extract_body(html: str) -> str:
    """抽取正文纯文本。容器: <div class="xq-content" id="content">(实测稳定)。"""
    m = (re.search(r'<div class="xq-content" id="content">([\s\S]*?)<div class="xq-next', html)
         or re.search(r'<div class="xq-content" id="content">([\s\S]{0,120000})', html))
    if not m:
        return ""
    txt = strip_tags(m.group(1))
    lines = [l.strip() for l in txt.split("\n") if l.strip()]
    out, buf = [], ""
    for l in lines:
        buf = (buf + " " + l).strip() if buf else l
        if len(buf) >= 40:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return "\n\n".join(out)


def extract_title(html: str) -> str:
    """从正文页抽标题(优先 <h2><strong>,退化 <title>)。

    【为什么以正文页为准而不是列表页】① 正文页才是权威来源;
    ② 被墙页面**照样带 <h2> 标题**(实测),于是墙内文章也能得到正确标题 ——
    这样"哪些内容被锁"这件事有可读的记录,而不是一串 slug。
    """
    m = re.search(r"<h2[^>]*>\s*<strong[^>]*>(.*?)</strong>", html, re.S)
    if not m:
        m = re.search(r"<h2[^>]*>(.*?)</h2>", html, re.S)
    if m:
        t = unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        if t:
            return t
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    if m:
        return unescape(m.group(1)).split("_")[0].strip()
    return ""


def extract_meta(html: str) -> tuple[str, int]:
    """抽取 (发布日期 YYYY-MM-DD, 发布时刻 epoch)。取不到返回 ("", 0)。"""
    m = re.search(r"发布时间：\s*(\d{4}-\d{2}-\d{2})\s*(\d{2}:\d{2})?", html)
    if not m:
        return "", 0
    d = m.group(1)
    ts = 0
    if m.group(2):
        try:
            ts = int(dt.datetime.strptime("%s %s" % (d, m.group(2)), "%Y-%m-%d %H:%M").timestamp())
        except Exception:
            ts = 0
    return d, ts


def is_walled(html: str) -> bool:
    return any(w in html for w in WALL_MARKERS)


# ── 状态 ───────────────────────────────────────────────────────────────
def load_seen() -> dict:
    try:
        d = json.loads(F_SEEN.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_seen(seen: dict, keep_days: int) -> None:
    """存水位线,并裁掉超过 keep_days 的老条目(防无限增长)。"""
    cutoff = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    pruned = {k: v for k, v in seen.items() if not v or str(v) >= cutoff}
    tmp = str(F_SEEN) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False)
    os.replace(tmp, F_SEEN)


def write_result(payload: dict) -> None:
    """原子写机器可读结果(读方是调度器,可能读到半截 ⇒ tmp + replace)。"""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = str(F_RESULT) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, F_RESULT)
    except Exception as e:  # noqa: BLE001 - 写结果失败绝不能影响主流程退出码
        print("    ! 结果文件写入失败(不影响本轮): %s" % e)


# ── 主流程 ─────────────────────────────────────────────────────────────
def collect_one(slug: str, title: str, delay: float, dry_run: bool) -> dict | None:
    """抓一篇正文 → 返回条目 dict;被墙/失败/过短返回 None(并打印原因)。

    【参数】title: 列表页给的标题(仅作兜底,正文页抽不到时才用)。
    【返回结构】与 wechat_ingest 的 entry 契约对齐:
        key / title / url / date / text / account / create_time / category
    """
    url = "%s/%s.html" % (ART_BASE, slug)
    html = fetch(url, delay)
    if html.startswith("ERR"):
        print("    ✗ 抓取失败 %s | %s" % (slug, html))
        return None
    title = extract_title(html) or title or slug
    if is_walled(html):
        print("    ⊘ 登录墙(不公开) | %s" % title[:52])
        return {"_wall": True, "key": slug, "title": title}
    body = extract_body(html)
    if len(body) < MIN_BODY_CHARS:
        print("    ⊘ 正文过短(%d 字) | %s" % (len(body), title[:52]))
        return {"_short": True, "key": slug, "title": title}
    date, ctime = extract_meta(html)
    cat = categorize(title)
    if dry_run:
        print("    + [DRY] %s | %s | %s | %d 字" % (date or "?", cat, title[:44], len(body)))
        return {"_dry": True, "key": slug, "title": title}
    e = {
        "key": slug,
        "title": title,
        "url": url,
        "date": date,
        "text": body,
        "account": cat,
        "category": cat,
        "create_time": ctime,
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    print("    + %s | %-6s | %-44s | %d 字" % (date or "?", cat, title[:44], len(body)))
    return e


def run(pages: int, delay: float, max_articles: int, dry_run: bool,
        urls: list[str], keep_days: int) -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    seen = load_seen()
    print("=" * 78)
    print("【隆众资讯采集】%s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("  状态目录: %s" % STATE_DIR)
    print("  已在库水位线: %d 条" % len(seen))

    stats = {"pages": 0, "listed": 0, "new": 0, "readable": 0,
             "walled": 0, "short": 0, "failed": 0, "skipped_seen": 0}

    # ── 候选收集 ──
    candidates: list[tuple[str, str]] = []
    if urls:
        for u in urls:
            m = _SLUG_RE.search(u)
            if m:
                candidates.append((m.group(1), ""))   # 标题留给正文页抽(见 extract_title)
            else:
                print("  ! 无法解析 slug,跳过: %s" % u)
    else:
        for page in range(1, pages + 1):
            lurl = NEWS_LIST + ("/" if page == 1 else "/%d.html" % page)
            html = fetch(lurl, delay)
            if html.startswith("ERR"):
                print("  ! 列表第 %d 页抓取失败: %s" % (page, html))
                stats["failed"] += 1
                break
            items = parse_list(html)
            stats["pages"] += 1
            stats["listed"] += len(items)
            fresh = [(s, t) for s, t in items if s not in seen]
            stats["skipped_seen"] += len(items) - len(fresh)
            print("  第 %d 页: %d 篇(新 %d)" % (page, len(items), len(fresh)))
            candidates.extend(fresh)
            if not fresh:
                # 【增量终止】本页全是已见 ⇒ 已越过水位线,后续页只会更旧
                print("  · 本页无新文,判定已到水位线,停止翻页")
                break

    if not candidates:
        print("\n  → 无新内容。")
        stats["new"] = 0
        _finish(stats, dry_run)
        return 0

    if max_articles and len(candidates) > max_articles:
        print("  · 新文 %d 篇超过单轮上限 %d,本轮只取最新的 %d 篇"
              % (len(candidates), max_articles, max_articles))
        candidates = candidates[:max_articles]

    # ── 逐篇抓正文 ──
    print("\n  抓取正文 %d 篇(限速 %.1fs/篇)……" % (len(candidates), delay))
    entries, dry_hits = [], []
    for i, (slug, title) in enumerate(candidates, 1):
        print("  [%d/%d] %s" % (i, len(candidates), title[:60]), flush=True)
        r = collect_one(slug, title, delay, dry_run)
        if r is None:
            stats["failed"] += 1
            continue
        if r.get("_wall"):
            stats["walled"] += 1
            seen[slug] = dt.date.today().isoformat()   # 记水位线,避免每轮重抓
            continue
        if r.get("_short"):
            stats["short"] += 1
            seen[slug] = dt.date.today().isoformat()
            continue
        if r.get("_dry"):
            dry_hits.append(slug)
            continue
        entries.append(r)
        seen[slug] = r.get("date") or dt.date.today().isoformat()

    stats["new"] = len(entries) + len(dry_hits) + stats["walled"] + stats["short"]
    stats["readable"] = len(entries) + len(dry_hits)

    if dry_run:
        print("\n  [DRY] 会写入 %d 篇;不落盘。" % len(dry_hits))
        _finish(stats, dry_run)
        return 0

    # ── 落盘:追加归档 + 最近一轮 + 水位线 ──
    if entries:
        with open(F_INBOX, "a", encoding="utf-8") as f:      # 【追加式】崩溃安全,入库读它
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp = str(F_NEW) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=1)
        os.replace(tmp, F_NEW)
    save_seen(seen, keep_days)

    print("\n  已追加 %d 篇 → %s" % (len(entries), F_INBOX.name))
    _finish(stats, dry_run)
    return 0


def _finish(stats: dict, dry_run: bool) -> None:
    print("")
    print("=" * 78)
    print("【汇总】扫 %d 页 / 列表 %d 篇 | 新增可读 %d 篇 | 登录墙 %d | 正文过短 %d | "
          "抓取失败 %d | 水位线跳过 %d"
          % (stats["pages"], stats["listed"], stats["readable"], stats["walled"],
             stats["short"], stats["failed"], stats["skipped_seen"]))
    print("Collected: %d" % stats["readable"])
    write_result({
        "ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dry_run": bool(dry_run),
        "pages": stats["pages"], "listed": stats["listed"],
        "collected": stats["readable"], "walled": stats["walled"],
        "short": stats["short"], "failed": stats["failed"],
        "skipped_seen": stats["skipped_seen"],
    })


def main() -> int:
    ap = argparse.ArgumentParser(description="隆众资讯内容采集(免登录,纯 HTTP)")
    ap.add_argument("--pages", type=int, default=2, help="扫描列表页数(默认 2)")
    ap.add_argument("--delay", type=float, default=8.0,
                    help="请求间隔秒数(默认 8;隆众会 429,勿调小)")
    ap.add_argument("--max-articles", type=int, default=40, help="单轮最多抓几篇(默认 40,0=不限)")
    ap.add_argument("--urls", default="", help="强制采指定文章 URL(逗号分隔,调试用)")
    ap.add_argument("--keep-days", type=int, default=120, help="水位线保留天数(默认 120)")
    ap.add_argument("--dry-run", action="store_true", help="只判定不落盘")
    ap.add_argument("--state-dir", help="覆盖状态目录")
    args = ap.parse_args()

    if args.state_dir:
        global STATE_DIR, F_INBOX, F_NEW, F_SEEN, F_RESULT
        STATE_DIR = Path(os.path.expanduser(args.state_dir))
        F_INBOX = STATE_DIR / "oilchem_news.jsonl"
        F_NEW = STATE_DIR / "oilchem_news.json"
        F_SEEN = STATE_DIR / "oilchem_seen.json"
        F_RESULT = STATE_DIR / "oilchem_news_result.json"

    urls = [u.strip() for u in args.urls.split(",") if u.strip()]
    return run(args.pages, args.delay, args.max_articles, args.dry_run, urls, args.keep_days)


if __name__ == "__main__":
    raise SystemExit(main())
