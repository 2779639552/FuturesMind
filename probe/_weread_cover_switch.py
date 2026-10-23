# -*- coding: utf-8 -*-
"""把读书通道的「取清单」从已废弃的 /web/mp/articles 换成 /web/mp/cover。

【为什么必须换】2026-10-16 实测：/web/mp/articles 恒返 -2012/-2041（接口已废弃），
而 /web/mp/cover 正常可用（带 cookie），返回 {title, reviewId, pic, name, ...}。

【★ cover 只给最新一篇】返回体里没有往期列表（coverBoxInfo 只是配色信息）
⇒ 清单段从「逐页翻」退化为「每号 1 篇」。这是平台能力决定的，不是实现偷懒。

【★ 去重键不用改】采集器源码已有 `review_id = f"{bid}_{oid}"`
⇒ `originalId = reviewId 去掉 "{bookId}_" 前缀`
⇒ `key = "wxmp_" + originalId` 与旧链路**同构**，入库链路零改动。

【★ 跨格式兜底】旧链路存进 mp_seen.json 的是**旧格式** originalId（形如 WR15Nw…），
与新版（形如 6sbTBEdZjNgzAAhHNw~-2A）不同 ⇒ 同一篇文章不会命中 seen_keys。
⇒ 因此再加一层「(号名, 归一化标题)」去重（读 inbox.jsonl），把过渡期的重复挡住。

【时间】cover 不返回发布时间 ⇒ 从标题/正文猜日期；猜不到用采集日兜底，
且**不写进 create_time**（避免把水位线推高、污染增量判定）。
"""
from pathlib import Path

P = Path("wechat_weread_collector.py")
raw = P.read_bytes()
eol = "\r\n" if raw.count(b"\r\n") > 0 else "\n"
t = raw.decode("utf-8")
report: list[str] = []


def sub(old: str, new: str, tag: str, n: int = 1):
    global t
    o = old.replace("\n", eol)
    assert t.count(o) == n, ("★锚点未命中", tag, t.count(o))
    t = t.replace(o, new.replace("\n", eol), n)
    report.append("  ✔ %s" % tag)


# ── ① 新增：日期猜测 / 标题兜底去重 / fetch_latest ──────────────
NEW_FUNCS = '''# ============================================================ 最新一篇（cover）
# 【2026-10-16 平台变更】旧接口 /web/mp/articles 已废弃（恒返 -2012 / -2041），
#   改用 /web/mp/cover：带 cookie 可用，返回 {avatar,name,title,pic,reviewId,template}。
#   ⚠ 返回体里**没有往期列表**（coverBoxInfo 只是配色信息）⇒ 每号只能拿最新一篇。
_COVER_ERR_HINT = ("-2010", "-2012", "-2041")

_HTML_SP = None


def _norm_title(s: str) -> str:
    """标题归一化：去空白与常见标点、转小写 ⇒ 用于**跨格式**兜底去重。"""
    import re as _re
    return _re.sub(r"[\\s\\-—–_|｜:：·、,，.。()（）\\[\\]【】“”\\"'']+", "", str(s or "")).lower()


_TITLE_CACHE: set | None = None


def known_titles() -> set:
    """inbox 里已出现过的 (号名, 归一化标题)。

    【为什么需要它】`mp_seen.json` 里的 keys 是**旧格式** originalId
    （旧链路写的，形如 `WR15NwFYq2Rp…`），而新版是 `6sbTBEdZjNgzAAhHNw~-2A`
    这类不透明 id ⇒ 同一篇文章不会命中 seen_keys ⇒ 会在过渡期重复入库。
    故补一层标题去重，把这段窗口挡住。
    """
    global _TITLE_CACHE
    if _TITLE_CACHE is not None:
        return _TITLE_CACHE
    s: set = set()
    try:
        if F_INBOX.exists():
            with io.open(F_INBOX, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    s.add((str(e.get("account") or ""), _norm_title(e.get("title") or "")))
    except Exception:  # noqa: BLE001
        pass
    _TITLE_CACHE = s
    return s


def _guess_date(title: str, text: str = "") -> str:
    """从标题/正文首部猜发布日期 → 'YYYY-MM-DD'；猜不到返回 ''。

    cover **不返回发布时间**，所以只能猜。证据形态（本项目实测）：
      · 标题尾带 8 位数   恒力期货日报[20261016]
      · 标题带连字符日期  …利润修复[2026-10-12]
      · 正文开头中文日期  [2026年10月16日] / 2026年10月11日
    """
    import re as _re
    for src in (str(title or ""), str(text or "")[:400]):
        m = _re.search(r"(20\\d{2})[-/年](\\d{1,2})[-/月](\\d{1,2})", src)
        if m:
            y, mo, d = (int(x) for x in m.groups())
        else:
            m2 = _re.search(r"(?<![0-9])(20\\d{2})(\\d{2})(\\d{2})(?![0-9])", src)
            if not m2:
                continue
            y, mo, d = (int(x) for x in m2.groups())
        try:
            if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return "%04d-%02d-%02d" % (y, mo, d)
        except Exception:  # noqa: BLE001
            pass
    return ""


def fetch_latest(bid: str, retries: int = 4) -> list[dict]:
    """取该号**最新一篇**（/web/mp/cover）。返回扁平列表（0 或 1 项）。

    字段与 `expand_articles` **同构** ⇒ 下游（build_entry / collect_account）零改动。
    ⚠ cover 不返回 time/summary/read_num ⇒ 置 0/空（下游不许据此做增量判定）。
    """
    last_err = ""
    for attempt in range(retries):
        try:
            r = _get("/web/mp/cover", {"bookId": bid}, timeout=40)
            d = r.json()
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:100]
            time.sleep(0.6 + attempt * 0.5)
            continue

        code = d.get("errCode") or d.get("errcode")
        if code:
            if str(code) in _COVER_ERR_HINT:
                raise SystemExit(
                    "✘ cover 报错 errCode=%s %s\\n"
                    "   ⇒ cookie 的登录态已失效（跑 桌面「重登公众号读书通道.bat」）"
                    % (code, d.get("errMsg") or ""))
            last_err = "errCode=%s" % code
            time.sleep(0.8 + attempt * 0.7)
            continue

        rid = str(d.get("reviewId") or "")
        if not rid:
            last_err = "回包里没有 reviewId"
            time.sleep(0.8 + attempt * 0.7)
            continue

        # ★ 去重键推导：reviewId = "{bookId}_{originalId}"
        oid = rid.split(bid + "_", 1)[1] if (bid + "_") in rid else rid
        return [{
            "original_id": oid,
            "review_id": rid,
            "title": str(d.get("title") or ""),
            "summary": "",
            "time": 0,
            "read_num": 0,
            "like_num": 0,
            "pic_url": str(d.get("pic") or ""),
            "mp_name": str(d.get("name") or ""),
            "_via": "cover",
        }]

    say(f"      ⚠ cover 重试 {retries} 次仍失败：{last_err}")
    return []


'''
sub("# ============================================================ 正文\n_HTML_ENTS = ",
    NEW_FUNCS + "# ============================================================ 正文\n_HTML_ENTS = ",
    "① 新增 fetch_latest / known_titles / _guess_date / _norm_title")

# ── ② 标注旧接口已废弃 ────────────────────────────────────────
sub('''def fetch_articles(bid: str, offset: int, retries: int = 4) -> list[dict]:
    """取一页「群发」并展开成扁平文章列表（带重试）。

    ⚠️ 该接口偶发**静默空回包**（HTTP 200 但 reviews 为空）⇒ 必须退避重试，
       否则会静默漏掉整页（社区实测此坑）。
    """''',
    '''def fetch_articles(bid: str, offset: int, retries: int = 4) -> list[dict]:
    """【已废弃 · 2026-10-16】取一页「群发」—— 平台已停用该接口。

    ★ 实测：`/web/mp/articles` 恒返 `-2012`（10-14~10-16）→ `-2041`（10-16 起），
      第三方项目同样在 10-15 被逼改用 `/web/mp/content` 取正文
      ⇒ **不要再调用本函数**（保留仅供诊断与历史对照）。
      现行取清单走 `fetch_latest()`（`/web/mp/cover`）。

    保留原说明：该接口偶发**静默空回包**（HTTP 200 但 reviews 为空）⇒ 必须退避重试。
    """''',
    "② fetch_articles 标注已废弃")

# ── ③ collect_account 取清单段：翻页 → cover 单篇 ──────────────
sub('''    # 逐页拉，直到够数 / 碰到水位线 / 页空
    picked: list[dict] = []
    offset = 0
    pages = 0
    while pages < max_pages:
        arts = fetch_articles(bid, offset)
        if not arts:
            break
        pages += 1
        fresh = [a for a in arts if a["original_id"] not in seen_keys]
        if mode == "daily":
            fresh = [a for a in fresh if int(a.get("time") or 0) > last_time] or fresh[:0]
        picked.extend(fresh)
        say(f"   offset={offset:4d} 展开 {len(arts):3d} 篇 其中新 {len(fresh):3d} 篇")

        if mode != "daily" and len(picked) >= limit:
            picked = picked[:limit]
            break
        if mode == "daily" and not fresh and pages >= 2:
            # 连着两页没有新文 ⇒ 到底了（增量场景下的"入口即拦截"）
            break
        offset += PAGE_SIZE
        time.sleep(random.uniform(0.5, 1.1))

    say(f"   ⇒ 本轮取到 {len(picked)} 篇新文")''',
    '''    # 【2026-10-16 改造】旧接口 /web/mp/articles 已被平台废弃 ⇒ 改用 /web/mp/cover。
    #   ★ cover **一次只给最新一篇**（平台返回体里没有往期列表）⇒ 不再有"翻页"概念。
    #   ⇒ 覆盖能力：每轮每号 1 篇。若两次运行之间该号发了多篇，**只会拿到最新那篇**
    #     （缓解：提高运行频率 —— 去重键幂等，跑多密都不会重复入库）。
    picked: list[dict] = []
    known_t = known_titles()
    arts = fetch_latest(bid)
    for a in arts:
        if a["original_id"] in seen_keys:
            say(f"   最新一篇已采过（key 命中）: {a['title'][:46]}")
            continue
        # ★ 跨格式兜底：旧链路写进 seen 的是旧格式 id ⇒ 这里按标题再挡一道
        if (name, _norm_title(a["title"])) in known_t:
            say(f"   最新一篇已采过（标题命中·跨格式兜底）: {a['title'][:46]}")
            continue
        picked.append(a)
    if not arts:
        say("   ⚠ cover 没有返回文章（接口异常，或该号确实无内容）")
    else:
        say(f"   最新一篇: {arts[0]['title'][:52]}")

    say(f"   ⇒ 本轮取到 {len(picked)} 篇新文")''',
    "③ collect_account 取清单段 → cover 单篇")

# ── ④ build_entry：date 兜底（cover 无时间）────────────────────
sub('''    ts = int(art.get("time") or 0)
    date = (_dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "")
    key = f"wxmp_{art['original_id']}"''',
    '''    ts = int(art.get("time") or 0)
    date = (_dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "")
    if not date:
        # cover 不返回发布时间 ⇒ 从标题/正文猜；猜不到用**采集日**兜底。
        # ⚠ 兜底值**不写进 create_time**（见下），否则会把水位线推高、污染增量判定。
        date = (_guess_date(art.get("title") or "", content.get("text") or "")
                or _dt.datetime.now().strftime("%Y-%m-%d"))
    key = f"wxmp_{art['original_id']}"''',
    "④ build_entry date 兜底")

# ── ⑤ --backfill 的能力说明 ──────────────────────────────────
sub('''    ap.add_argument("--backfill", type=int, metavar="N", help="每号回填最近 N 篇")''',
    '''    ap.add_argument("--backfill", type=int, metavar="N",
                    help="每号回填最近 N 篇（⚠ 平台已停用列表接口 ⇒ 实际只能取最新 1 篇）")''',
    "⑤ --backfill help 文案")

sub('''    mode = "backfill" if args.backfill else "daily"
    limit = args.backfill or 20''',
    '''    mode = "backfill" if args.backfill else "daily"
    limit = args.backfill or 20
    if args.backfill and args.backfill > 1:
        say("⚠ 平台已停用公众号列表接口（/web/mp/articles 恒返 -2041）⇒ 改用 /web/mp/cover，")
        say("   而它**每次只返回最新一篇** ⇒ 无法回填 N 篇，本次实际只取最新 1 篇。")
        say("   （需要历史文章时：用桌面「重登公众号读书通道.bat」扫码后尽快跑一轮，")
        say("     或对已入库的号改用免登录的「合集」通道。）")
        say("")''',
    "⑥ main 里 backfill 限制提示")

# ── ⑦ --list 增补通道现状 ────────────────────────────────────
sub('''        say(f"inbox : {F_INBOX}")
        return 0''',
    '''        say(f"inbox : {F_INBOX}")
        say("")
        say("取清单通道: /web/mp/cover（每号 1 篇；旧 /web/mp/articles 已被平台停用）")
        say("去重键    : wxmp_{reviewId 去掉 bookId 前缀}  +  (号名, 标题) 跨格式兜底")
        return 0''',
    "⑦ --list 增补通道说明")

# ── 落盘 ─────────────────────────────────────────────────────
out = t.encode("utf-8")
assert out.count(b"\n") == (out.count(b"\r\n") if eol == "\r\n" else 0) + (0 if eol == "\r\n" else out.count(b"\n"))
P.write_bytes(out)
print("=" * 74)
print("  落盘完成")
print("=" * 74)
print("\n".join(report))
b = P.read_bytes()
c = b.count(b"\r\n")
print()
print("  %d → %d 字节；EOL: CRLF=%d bareLF=%d（原为 %s）"
      % (len(raw), len(b), c, b.count(b"\n") - c, "CRLF" if eol == "\r\n" else "LF"))
