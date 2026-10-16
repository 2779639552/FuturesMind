# -*- coding: utf-8 -*-
r"""只读探针：确认指定公众号能否走「合集」通道，并摸清规模与活跃度。

⚠️ 本脚本**严格只读**：
   · 不写 wechat_accounts.json（不用 learn_from_url / reg_account）
   · 不写 wechat_seen.json、不落 wechat_inbox.jsonl
   只复用采集器的**解析函数**，把"能不能采、有多少篇、最近还发不发"先摸清楚，
   供用户核对号名后再正式登记。

【为什么必须先探针】零登录通道对**没建合集**的号完全无效（号级入口全要登录），
   而且用户给的链接可能指向**转载号**（号名会张冠李戴）⇒ 先打印真实号名让用户核对。

用法: venv/Scripts/python.exe probe/_wechat_probe_accounts.py
产物: probe/_wechat_probe_result.json
"""
import importlib.util
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "probe" / "_wechat_probe_result.json"

spec = importlib.util.spec_from_file_location(
    "wac_probe", ROOT / "wechat_album_collector.py")
wac = importlib.util.module_from_spec(spec)
sys.modules["wac_probe"] = wac
spec.loader.exec_module(wac)

# 用户的说法 → 链接（顺序与用户上一条消息一致）
TARGETS = [
    ("中信期货研究", "https://mp.weixin.qq.com/s/tBTKv-QOuBM5K9lSEsg9uQ"),
    ("混沌天成研究", "https://mp.weixin.qq.com/s/Lktb-yomW8HBeS6tBOQb7A"),
    ("中信建投期货研究", "https://mp.weixin.qq.com/s/ThbWuBNGj2Kq5CcoGTwpCw"),
]


def ts2s(v):
    """秒级时间戳 → 'YYYY-MM-DD'（失败返回原值）。"""
    try:
        return datetime.fromtimestamp(int(str(v)[:10])).strftime("%Y-%m-%d")
    except Exception:
        return None


def article_pubdate(body):
    """从文章页尽力取**本篇发布日期**（作为该号近期活跃度的一个参考点）。

    页面里时间戳的变量形态不稳定，故按候选顺序逐个试；全失败返回 None 不报错。
    """
    for pat in (r'var\s+ct\s*=\s*"(\d{10})"',
                r'"create_time"\s*:\s*"?(\d{10})',
                r'var\s+create_time\s*=\s*"(\d{10})"',
                r'publish_time\s*=\s*"(\d{10})"'):
        m = re.search(pat, body)
        if m:
            d = ts2s(m.group(1))
            if d:
                return d
    return None


results = []
for want, url in TARGETS:
    print("=" * 80)
    print("▸ 用户的说法: %s" % want)
    print("  url = %s" % url)
    rec = {"want": want, "url": url}

    try:
        body, how = wac.get_html(url)
    except Exception as e:
        print("  ✗ 取页异常: %s" % e)
        rec["error"] = "get_html: %s" % e
        results.append(rec)
        continue
    if not body:
        print("  ✗ 取不到页面（通道不通）")
        rec["error"] = "no_html"
        results.append(rec)
        continue

    print("  取页方式 = %s   len=%d" % (how, len(body)))
    if wac.is_verify_page(body):
        print("  ⚠ 命中安全验证页(限流) —— 结论不可信，建议稍后重跑")
        rec["verify_page"] = True

    tags = wac.albums_of_page(body)                       # [(合集名, album_id), ...]
    biz = wac.var_of(body, "biz")
    nick, alias = wac.parse_author(body)
    bare = sorted(set(re.findall(r'album_id["\']?\s*[:=]\s*["\']?(\d{8,})', body)))
    pub = article_pubdate(body)

    print("  实际号名 = %s" % (nick or "?"))
    print("  微信号   = %s" % (alias or "?"))
    print("  __biz    = %s" % (biz or "?"))
    print("  本篇发布 = %s" % (pub or "未能解析"))
    print("  正文所属合集(tags) = %s" % (tags or "无"))
    if bare:
        print("  页面裸 album_id = %s" % bare)
    if nick and nick != want:
        print("  ⚠ 号名与用户说法不一致: 用户说「%s」, 实际「%s」" % (want, nick))

    rec.update(biz=biz, nickname=nick, username=alias, pubdate=pub,
               tags=tags, bare_ids=bare)

    # 逐个合集拿「声明篇数」（廉价探针: count=1 只要 base_info）
    albums = []
    seen_ids = set()
    for tname, tid in (tags or []):
        seen_ids.add(str(tid))
        bi, err = wac.album_base_info(biz, tid)
        if err:
            print("    ✗ 合集 %s(%s) 探针失败: %s" % (tname, tid, err))
            albums.append({"album_id": tid, "title": tname, "error": err})
            continue
        cnt = bi.get("article_count")
        # 核对合集归属确实是这个号（防转载号张冠李戴）
        owner = bi.get("nickname") or ""
        ok_owner = (not owner) or (not nick) or (owner == nick)
        print("    · 《%s》 album_id=%s  声明篇数=%s  号名=%s %s"
              % (bi.get("title") or tname, tid, cnt, owner or "?",
                 "" if ok_owner else "⚠ 与文章页号名不符!"))
        albums.append({"album_id": str(tid), "title": bi.get("title") or tname,
                       "article_count": cnt, "owner": owner,
                       "desc": bi.get("desc"), "isupdating": bi.get("isupdating")})
        time.sleep(1.5)

    # 页面裸 album_id 里若有未出现在 tags 的，提示（可能是该号其它合集）
    extra = [i for i in bare if i not in seen_ids]
    if extra:
        print("  ℹ 另有未归属 tags 的 album_id: %s（本通道只认 tags，建议人工确认）" % extra)

    rec["albums"] = albums
    total = sum(int(a["article_count"]) for a in albums
                if str(a.get("article_count") or "").isdigit())
    rec["total_declared"] = total
    print("  ⇒ 已发现 %d 个合集，声明篇数合计 %d" % (len(albums), total))
    if not albums:
        print("  ✗ 未发现任何合集 ⇒ 该号**不适用**本通道（零登录拿不到文章清单）")
    results.append(rec)
    time.sleep(2.0)

# 汇总
print("=" * 80)
print("【汇总】")
for r in results:
    if r.get("error"):
        print("  ✗ %-12s 不可采: %s" % (r["want"], r["error"]))
    elif not r.get("albums"):
        print("  ✗ %-12s 无合集 ⇒ 通道不适用" % r["want"])
    else:
        print("  ✓ %-12s 号名「%s」 合集 %d 个 共 %d 篇  最近一篇(样本) %s"
              % (r["want"], r.get("nickname") or "?", len(r["albums"]),
                 r.get("total_declared") or 0, r.get("pubdate") or "?"))

OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
print("\n详细结果已写入: %s" % OUT.relative_to(ROOT))
