# -*- coding: utf-8 -*-
"""
公众号「合集」通道采集器 v3 —— 零登录、零 cookie、零账号

⚠️ 双副本（2026-09-21 接入 AgentSense 后）：
   · 生产副本 = `D:\\AgentSense\\AgentSense\\wechat_album_collector.py`（调度器每日 08:30/18:30 调用）
   · 开发副本 = 本文件（WorkBuddy 项目 tools/ 下，供探测/调试）
   · 状态目录：生产用 `AGENTSENSE_WECHAT_DIR=%USERPROFILE%\\.tradingagents\\wechat`；开发默认=脚本同目录
   · **改任一份后必须同步另一份**（与本项目 `data_collection/validate` ↔ `思路2/validate` 同规矩）

支持两种模式：
  · 全量模式（v2 的能力）：拉某号所有已知合集的并集，建立基线
  · **每日增量模式（v3 新增）**：只在"入口"取新文，不做"先采全量再查重"

================================ 增量原理（全部实测确认）================================
合集接口 `appmsgalbum?action=getalbum` 的三个要害事实：

 ① **返回顺序是「升序」**（最旧在前，最新在最后一页）。
    ⇒ 想只看最新，**不能**靠"读第 1 页"，必须用游标。

 ② **游标 = 传上一篇已知最新文章的 (begin_msgid, begin_itemidx, begin_create_time)，
    接口返回「严格晚于它」的文章**（实测：游标=倒数第 3 篇 → 精确返回其后 2 篇）。
    三者必须**同时**传；只传 begin_create_time 会被忽略（退回第 1 页）。
    · 无新文 → 响应里**根本没有 article_list**，且 continue_flag=0  ⇒ 干净的"无更新"信号
    · 有新文 → 按前面那篇继续翻，直到 continue_flag != 1

 ③ **单篇陷阱（静默类型变化）**：游标后**恰好只剩 1 篇**时，`article_list` 不是数组，
    而是**那一篇文章的对象本身**。`len()` 会等于字段数（14），照常迭代会拿到一堆字段名。
    ⇒ 一律用 `norm_alist()` 归一化。v2 的 `for x in article_list` 在此处会误解析。

每日增量 = 每个合集 **2 个请求**（1 个查声明篇数 + 1 个游标取增量），无新文时 ~1s 完成。
比"全量拉 6 页再按 key 查重"省一个数量级，也顺带避免了重复烧 LLM。

================================ 追补旧文的自愈 ================================
游标只能发现"晚于水位线"的文章。若作者**事后把一篇老文章补进合集**，它落在中间，
游标看不见 ⇒ 用 `base_info.article_count`（声明总篇数，实测与实际完全一致）做守恒校验：
    声明篇数 != 上次篇数 + 本次新增  ⇒  触发一次全量重扫，把差额找出来
（同理可发现"作者把文章移出合集"）。

================================ 其它已知边界与坑 ================================
  · 只覆盖**被勾进某个合集**的文章，不等于该号全部文章
  · **零登录无法枚举"该号全部合集"** —— 四条实测证据（2026-09-21，逐条验过）：
      ① 文章页只内联**本篇所属的那一个**合集：`album_info_list` 数组长度=1、
         `public_tag_info.tags` 长度=1、整页唯一 album_id；渲染+滚动后依然只有 1 个，
         且客户端全程**不发**任何 album 相关请求（数据是内联的）。
      ② 点底部「合集 · 目录」只弹**本合集**文章浮层（走
         `appmsgalbum?action=paging&album_id=…&cur_msgid=…`，仍需 album_id），
         浮层内**无**"全部合集 / 切换合集"。
      ③ 号级入口全要登录：账号级列表 action 一律 `ret=-3 no session`；
         `publictag` 话题页要"在微信客户端打开"；`profile_ext?action=home` 要 session。
      ④ 14 篇跨两合集抽样：**0 篇多归属**，三处取值完全一致且各 1 项
         ⇒ "沿文章反查第三个合集"这条路也不通。
    ⇒ 新合集的**唯一发现方式**是拿一篇属于它的文章链接走 --from-url。
      `--audit` 只能"未发现"，**不能**"已排除"。
  · `count` **上限 20**（传 200 也只回 20）⇒ 全量靠翻页，增量靠游标
  · 文章 HTML 页在密集访问后会返回 `secitptpage/verify.html`（HTTP200 约 17KB、无 js_content）
    —— 那是**安全验证页，不是"文章不存在"**，检测到即自动切真实浏览器重取
  · 页面里的「使用完整服务」「已被删除」是**模板固有文本**（扫码引导 / "商品合集已被删除" toast），
    判正文只认 `id="js_content"`
  · `public_tag_info` 在页面出现多次（含 JS 表达式），只认 `public_tag_info\\s*:\\s*\\{`
    且块内含 `tag_name` 的那一处，否则会静默配错块

用法：
  # ① 学习（给该号任意一篇文章链接，登记它所属的合集）
  python wechat_album_collect.py --from-url "https://mp.weixin.qq.com/s/xxxx"

  # ② 每日增量追踪（核心）
  python wechat_album_collect.py --account "多空逻辑" --daily
  python wechat_album_collect.py --daily                 # 不指定则跑注册表里所有号
  python wechat_album_collect.py --daily --no-fetch      # 只列新文，不抓正文
  python wechat_album_collect.py --daily --baseline      # 首轮把全量当基线并列出（不抓正文）

  # ③ 全量重扫（建立/重建基线、追补校验）
  python wechat_album_collect.py --account "多空逻辑" --full

  # ④ 其它
  python wechat_album_collect.py --list
  python wechat_album_collect.py --account "多空逻辑" --add-album 3053094590460772356
  python wechat_album_collect.py --audit --sample 20        # 体检：抽样找未登记合集
"""
import argparse
import datetime
import html
import io
import json
import os
import random
import re
import sys
import time

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 【状态目录】默认=脚本所在目录;生产副本(AgentSense/wechat_album_collector.py)用环境变量
# AGENTSENSE_WECHAT_DIR 指向 ~/.tradingagents/wechat —— 与 AgentSense 既有的
# _STATE_DIR(Path.home()/".tradingagents") 约定一致,避免把状态/产物写进应用代码目录。
HERE = (os.path.expanduser((os.environ.get("AGENTSENSE_WECHAT_DIR") or "").strip())
        or os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(HERE, "wechat_accounts.json")   # 号 → 已知合集
STATE = os.path.join(HERE, "wechat_seen.json")          # 合集 → 水位线 + 已知 key 全集
OUT_TXT = os.path.join(HERE, "album_collect_result.txt")
OUT_FULL = os.path.join(HERE, "album_collect.json")
OUT_NEW = os.path.join(HERE, "wechat_new.json")         # 本次新增（含正文）
OUT_INBOX = os.path.join(HERE, "wechat_inbox.jsonl")    # 追加式，跨轮不丢失

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

_log = []
_browser = None


def say(msg=""):
    print(msg)
    _log.append(str(msg))


def dstr(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return "?"


def now_s():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ============================================================ 解析工具
def brace_block(s, i):
    depth = 0
    for j in range(i, min(len(s), i + 40000)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j]
    return ""


def bracket_block(s, i):
    """方括号配对（解析 `name = [ ... ]` 数组字面量用）"""
    depth = 0
    for j in range(i, min(len(s), i + 80000)):
        if s[j] == "[":
            depth += 1
        elif s[j] == "]":
            depth -= 1
            if depth == 0:
                return s[i + 1:j]
    return ""


def parse_tags(body):
    """精确解析 public_tag_info.tags -> [(合集名, album_id)]"""
    for m in re.finditer(r"public_tag_info\s*:\s*\{", body):
        blk = brace_block(body, m.end() - 1)
        if "tag_name" in blk:
            out = []
            for t in re.finditer(r"tag_name\s*:\s*'([^']*)'", blk):
                seg = blk[t.start():t.start() + 1200]
                am = re.search(r"album_id\s*:\s*'(\d+)'", seg)
                out.append((t.group(1), am.group(1) if am else "?"))
            return out
    return []


def parse_album_info_list(body):
    """文章页内联的 `album_info_list` 数组 -> [album_id]

    ⚠⚠ 名字有严重误导性：它**不是**"该号全部合集清单"。
      实测（2026-09-21，14 篇跨两合集抽样）：该数组**恒只含本篇所属的那一个合集**，
      长度 = 1，与 public_tag_info.tags 完全一致；页面也只有一个 album_id。
      ⇒ 不能靠它枚举合集。
    """
    for m in re.finditer(r"\balbum_info_list\s*=\s*\[", body):
        blk = bracket_block(body, m.end() - 1)
        if "albumId" in blk:
            return re.findall(r"albumId\s*:\s*'(\d+)'", blk)
    return []


def albums_of_page(body):
    """一篇文章的合集归属：tags + album_info_list 两路合并 -> [(合集名 or '?', album_id)]

    合并的目的是**不漏**：若将来某篇文章同时挂多个合集（跨合集桥梁），
    两路里任一路能发现它（实测目前两路取值完全一致、各 1 项）。
    """
    out, seen = [], set()
    for n_, i_ in parse_tags(body):
        if i_ and i_ not in seen:
            seen.add(i_)
            out.append((n_, i_))
    for i_ in parse_album_info_list(body):
        if i_ not in seen:
            seen.add(i_)
            out.append(("?", i_))
    return out


def var_of(body, name):
    m = (re.search(r'var\s+%s\s*=\s*"([^"]*)"' % name, body)
         or re.search(r"var\s+%s\s*=\s*'([^']*)'" % name, body)
         or re.search(r'\b%s\s*[:=]\s*"([^"]*)"' % name, body)
         or re.search(r"\b%s\s*[:=]\s*'([^']*)'" % name, body))
    return m.group(1) if m else ""


def parse_author(body):
    nick = var_of(body, "nickname")
    alias = var_of(body, "user_name")
    if not nick:
        m = re.search(r'id="js_name"[^>]*>\s*([^<]{1,40}?)\s*<', body)
        if m:
            nick = html.unescape(m.group(1)).strip()
    if not nick:
        m = re.search(r'data-nickname=\\?["\']([^"\'\\]{1,40})', body)
        if m:
            nick = html.unescape(m.group(1)).strip()
    if not alias:
        m = re.search(r'data-alias=\\?["\']([^"\'\\]{1,40})', body)
        if m:
            alias = m.group(1).strip()
    return nick, alias


def is_verify_page(body):
    return ("secitptpage/verify" in body
            or "PAGE_MID='mmbizwap:secitptpage/verify.html'" in body) \
        and 'id="js_content"' not in body


def extract_text(body):
    body_m = re.search(r'id="js_content"[^>]*>(.*?)</div>\s*<script', body, re.S) \
        or re.search(r'id="js_content"[^>]*>(.*?)</div>', body, re.S)
    if not body_m:
        return ""
    t = re.sub(r"<br\s*/?>", "\n", body_m.group(1))
    t = re.sub(r"</p>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t).strip()
    return t


# ============================================================ 接口层
def norm_alist(g):
    """把 article_list 归一化成 list[dict]

    ⚠ 静默类型陷阱：游标后只剩 1 篇时，微信返回的是**对象**而不是数组。
      len() 会等于字段数（14），直接迭代会拿到一堆字段名字符串。
    """
    a = (g or {}).get("article_list")
    if a is None:
        return []
    if isinstance(a, dict):
        return [a] if ("msgid" in a or "key" in a or "title" in a) else []
    if isinstance(a, list):
        return [x for x in a if isinstance(x, dict)]
    return []


def api_getalbum(biz, album_id, count=20, cursor=None, timeout=30):
    """调合集接口。cursor=(msgid, itemidx, create_time) 或 None。
    返回 (ret, g, err)"""
    u = ("https://mp.weixin.qq.com/mp/appmsgalbum?__biz=%s&action=getalbum"
         "&album_id=%s&count=%d&is_from_sn=1&f=json" % (biz, album_id, count))
    if cursor:
        u += "&begin_msgid=%s&begin_itemidx=%s&begin_create_time=%s" % cursor
    try:
        r = requests.get(u, headers=HEADERS, timeout=timeout)
    except Exception as e:
        return None, {}, "请求异常: %s" % str(e)[:70]
    try:
        j = json.loads(r.text)
    except Exception:
        return None, {}, "返回非 JSON（可能被限流）: %s" % r.text[:100]
    ret = (j.get("base_resp") or {}).get("ret")
    if ret not in (0, "0", None):
        return ret, {}, "ret=%s errmsg=%s" % (ret, (j.get("base_resp") or {}).get("errmsg"))
    return ret, j.get("getalbum_resp") or {}, ""


def album_base_info(biz, album_id):
    """廉价探针：声明总篇数 / 合集名 / 号名（实测 article_count 与实际完全一致）"""
    ret, g, err = api_getalbum(biz, album_id, count=1)
    if err:
        return {}, err
    return g.get("base_info") or {}, ""


def album_cursor_fetch(biz, album_id, cursor, max_pages=12, verbose=True,
                       sleep=2.5):
    """增量核心：从 cursor 之后取文章（升序）。返回 (items, pages, flags, err)"""
    items, pages, flags = [], 0, []
    cur = cursor
    while pages < max_pages:
        ret, g, err = api_getalbum(biz, album_id, count=20, cursor=cur)
        pages += 1
        if err:
            return items, pages, flags, err
        batch = norm_alist(g)
        cf = str(g.get("continue_flag"))
        flags.append(cf)
        if verbose:
            say("      第%d次: %d 篇  continue=%s  reverse_continue=%s"
                % (pages, len(batch), cf, g.get("reverse_continue_flag")))
        if not batch:
            break
        items.extend(batch)
        if cf != "1":
            break
        last = batch[-1]
        cur = (last.get("msgid"), last.get("itemidx", "1"), last.get("create_time"))
        time.sleep(sleep + random.uniform(0, 1.0))
    return items, pages, flags, ""


def album_full_walk(biz, album_id, max_pages=12, sleep=2.5, verbose=True):
    """全量：从第 1 页沿升序翻到底。返回 (meta, items)"""
    seen, order, begin, meta, pages = {}, [], None, {}, 0
    while pages < max_pages:
        ret, g, err = api_getalbum(biz, album_id, count=20, cursor=begin)
        pages += 1
        if err:
            if verbose:
                say("    第%d页失败：%s" % (pages, err))
            break
        if not meta:
            meta = g.get("base_info") or {}
        batch = norm_alist(g)
        new = 0
        for x in batch:
            k = x.get("key") or x.get("msgid")
            if k and k not in seen:
                seen[k] = x
                order.append(k)
                new += 1
        if verbose:
            say("    第%d页: %d 条  %s ~ %s  新增 %d  continue=%s"
                % (pages, len(batch),
                   dstr(batch[0].get("create_time")) if batch else "-",
                   dstr(batch[-1].get("create_time")) if batch else "-",
                   new, g.get("continue_flag")))
        if not batch or str(g.get("continue_flag")) != "1" or new == 0:
            break
        last = batch[-1]
        begin = (last.get("msgid"), last.get("itemidx", "1"), last.get("create_time"))
        time.sleep(sleep + random.uniform(0, 1.0))
    arts = [seen[k] for k in order]
    arts.sort(key=lambda x: int(x.get("create_time") or 0))
    for a in arts:
        a["_album_id"] = album_id
    return meta, arts


# ============================================================ 浏览器兜底
def browser_get_html(url, wait=3.0):
    global _browser
    from playwright.sync_api import sync_playwright
    if _browser is None:
        _pw = sync_playwright().start()
        try:
            _browser = _pw.chromium.launch(channel="msedge", headless=True)
        except Exception:
            _browser = _pw.chromium.launch(headless=True)
        _browser._pw = _pw
    ctx = _browser.new_context(user_agent=UA, locale="zh-CN",
                               viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            pg.wait_for_selector("#js_content", timeout=int(wait * 1000))
        except Exception:
            pass
        time.sleep(wait)
        return pg.content()
    finally:
        ctx.close()


def close_browser():
    global _browser
    if _browser is not None:
        try:
            _browser.close()
            _browser._pw.stop()
        except Exception:
            pass
        _browser = None


def get_html(url, allow_browser=True, verbose=True):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
        b = r.text
    except Exception as e:
        if not allow_browser:
            return "", "requests异常:%s" % str(e)[:60]
        b = ""
        if verbose:
            say("      requests 异常（%s），转浏览器" % str(e)[:50])
    if 'id="js_content"' in b:
        return b, "requests"
    if not allow_browser:
        return b, "requests(无js_content)"
    if verbose:
        say("      %s ⇒ 转真实浏览器重取"
            % ("命中安全验证页" if is_verify_page(b) else "requests 未拿到 js_content"))
    try:
        b2 = browser_get_html(url)
    except Exception as e:
        return b, "browser异常:%s" % str(e)[:60]
    return b2, "browser"


def fetch_text(url, verbose=True):
    b, how = get_html(url, verbose=False)
    d = {"ok": False, "how": how, "len": len(b or "")}
    if not b:
        d["err"] = "取页失败"
        return d
    if "该内容已被发布者删除" in b or "此内容因违规无法查看" in b:
        d["err"] = "内容已被删除或屏蔽"
        return d
    if is_verify_page(b):
        d["err"] = "安全验证页（两种方式都被拦）"
        return d
    t = extract_text(b)
    if not t:
        d["err"] = "未取到 js_content"
        return d
    d.update({"text": t, "chars": len(t), "ok": True, "tags": albums_of_page(b)})
    for name in ("biz", "mid", "idx"):
        d[name] = var_of(b, name)
    if d.get("biz") and d.get("mid"):
        d["perm_key"] = "%s_%s_%s" % (d["biz"], d["mid"], d["idx"])
    return d


# ============================================================ 注册表 / 状态
def _load(path, default):
    if os.path.exists(path):
        try:
            return json.load(io.open(path, encoding="utf-8"))
        except Exception:
            pass
    return default


def _save(path, obj):
    io.open(path, "w", encoding="utf-8").write(json.dumps(obj, ensure_ascii=False, indent=2))


def load_reg():
    return _load(REGISTRY, {"accounts": {}})


def save_reg(reg):
    _save(REGISTRY, reg)


def load_state():
    return _load(STATE, {"albums": {}})


def save_state(st):
    _save(STATE, st)


def reg_account(reg, biz, nickname, username, albums):
    a = reg["accounts"].get(biz) or {}
    a["biz"] = biz
    a.update({k: v for k, v in (("nickname", nickname), ("username", username)) if v})
    a.setdefault("albums", {})
    added = []
    for name, aid in albums:
        if aid and aid not in a["albums"]:
            a["albums"][aid] = {"title": name, "added_at": now_s()}
            added.append((name, aid))
        elif aid:
            a["albums"][aid]["title"] = name or a["albums"][aid].get("title")
    reg["accounts"][biz] = a
    return added


def resolve_accounts(reg, wanted):
    """把 --account（号名/微信号/biz/合集名）解析成 [(biz, acct)]；wanted 为空则返回全部"""
    if not reg["accounts"]:
        say("✗ 注册表为空，先用 --from-url 给一篇该号文章链接")
        sys.exit(2)
    if not wanted:
        return list(reg["accounts"].items())
    key = wanted.strip()
    if key in reg["accounts"]:
        return [(key, reg["accounts"][key])]
    for k, a in reg["accounts"].items():
        names = [a.get("nickname") or "", a.get("username") or ""]
        names += [(m or {}).get("title") or "" for m in a.get("albums", {}).values()]
        if key in [x for x in names if x]:
            return [(k, a)]
    say("✗ 注册表里找不到账号「%s」。现有：%s"
        % (wanted, [(a.get("nickname") or "?") + "/" + k for k, a in reg["accounts"].items()]))
    sys.exit(2)


# ============================================================ ① 学习
def learn_from_url(reg, url, verbose=True):
    b, how = get_html(url)
    if not b:
        return None
    if verbose:
        say("  取页方式 = %s   len=%d" % (how, len(b)))
        if is_verify_page(b):
            say("  ✗ 仍是安全验证页（requests 与浏览器都被拦，建议稍后重试）")
    tags = albums_of_page(b)
    biz = var_of(b, "biz")
    nick, alias = parse_author(b)
    ids = sorted(set(re.findall(r'album_id["\']?\s*[:=]\s*["\']?(\d{8,})', b)))
    if tags and str(tags[0][1]).isdigit() and (not nick or not alias):
        bi, _ = album_base_info(biz, tags[0][1])
        nick = nick or bi.get("nickname") or ""
        alias = alias or bi.get("username") or ""
    if verbose:
        say("  号名 = %s   微信号 = %s" % (nick or "?", alias or "?"))
        say("  __biz = %s" % (biz or "?"))
        say("  public_tag_info.tags = %s" % (tags or "无（该文没被勾进任何合集）"))
        if not tags and ids:
            say("  ⚠ 页面另有裸 album_id（未见于 tags）：%s" % ids)
    if not biz:
        return None
    added = reg_account(reg, biz, nick, alias,
                        tags or [("(未知合集名)", i) for i in ids[:1]])
    if added and verbose:
        for n_, i_ in added:
            say("  ✅ 新登记合集：%s  (%s)" % (n_, i_))
    return {"biz": biz, "nickname": nick, "username": alias, "tags": tags, "how": how}


# ============================================================ ② 每日增量
def _item_brief(x):
    return {"key": x.get("key") or x.get("msgid"),
            "msgid": x.get("msgid"), "itemidx": x.get("itemidx", "1"),
            "create_time": x.get("create_time"),
            "date": dstr(x.get("create_time")),
            "title": x.get("title"), "url": x.get("url")}


def run_daily(reg, st, args):
    accts = resolve_accounts(reg, args.account)
    say("=" * 78)
    say("【每日增量追踪】 账号数 %d   时间 %s" % (len(accts), now_s()))
    say("  每个合集 2 个请求（声明篇数探针 + 游标增量）；无更新时约 1 秒完成")
    all_new, notes = [], []

    for biz, acct in accts:
        nick = acct.get("nickname") or biz
        albums = acct.get("albums", {})
        say("")
        say("─" * 78)
        say("▸ 公众号《%s》   微信号 %s   已登记合集 %d 个：%s"
            % (nick, acct.get("username") or "?", len(albums),
               " / ".join((m.get("title") or k) for k, m in albums.items())))
        say("  ⚠ 覆盖范围 = 上述已登记合集。该号若有**未登记**的合集，其新文不在本次检测内；")
        say("    零登录无法枚举该号全部合集 ⇒ 新合集需人工给一篇属于它的文章链接（见 --from-url）")
        for aid, m in albums.items():
            title = m.get("title") or aid
            ast = st["albums"].get(aid) or {}
            say("")
            say("  《%s》 %s" % (title, aid))

            # --- 廉价探针：声明总篇数
            bi, err = album_base_info(biz, aid)
            if err:
                say("    ✗ 声明篇数探针失败：%s" % err)
                notes.append("%s《%s》探针失败" % (nick, title))
                time.sleep(2)
                continue
            declared = bi.get("article_count")
            declared = int(declared) if str(declared).isdigit() else None
            stored = ast.get("article_count")
            say("    声明篇数 = %s   上次记录 = %s   水位线 = %s"
                % (declared, stored,
                   (ast.get("newest") or {}).get("date") or "（无，需先建基线）"))
            time.sleep(2)

            watermark = ast.get("newest")
            known_keys = set(ast.get("keys") or [])

            if not watermark or not known_keys:
                # ---------- 首次：建立基线（全量走一遍，只登记不当作新增）----------
                say("    ⓘ 未建基线 → 全量扫描建立水位线（不记为新增）")
                meta, arts = album_full_walk(biz, aid, max_pages=args.max_pages)
                if not arts:
                    say("    ✗ 全量扫描失败")
                    notes.append("%s《%s》基线失败" % (nick, title))
                    continue
                keys = sorted([a.get("key") or a.get("msgid") for a in arts])
                st["albums"][aid] = {
                    "biz": biz, "nickname": nick, "album_title": title,
                    "article_count": int(meta.get("article_count") or len(arts)),
                    "keys": keys, "newest": _item_brief(arts[-1]),
                    "baseline_at": now_s(), "last_run": now_s(), "last_new": 0}
                say("    ✓ 基线已建立：%d 篇  %s ~ %s"
                    % (len(arts), dstr(arts[0].get("create_time")),
                       dstr(arts[-1].get("create_time"))))
                if args.baseline_as_new:
                    all_new.extend([dict(_item_brief(a), account=nick, album=title,
                                         source_url=a.get("url")) for a in arts])
                    say("    ⓘ --baseline-as-new：这 %d 篇已计入本次产出" % len(arts))
                continue

            # ---------- 增量：用水位线当游标 ----------
            cursor = (watermark.get("msgid"), watermark.get("itemidx", "1"),
                      watermark.get("create_time"))
            items, pages, flags, err = album_cursor_fetch(biz, aid, cursor,
                                                          max_pages=args.max_pages)
            if err:
                say("    ✗ 游标请求失败：%s" % err)
                notes.append("%s《%s》增量失败" % (nick, title))
                continue
            fresh = [x for x in items
                     if (x.get("key") or x.get("msgid")) not in known_keys]
            if not items and not fresh:
                say("    ✓ 无更新（游标后为空，continue=%s）" % (flags[-1] if flags else "?"))
            else:
                say("    ★ 游标后 %d 篇，其中未见过 %d 篇" % (len(items), len(fresh)))
                if items and not fresh:
                    say("      ⚠ 游标返回了内容，但全都在已见集合里 —— 游标可能没生效，"
                        "本次按『无新文』处理，请留意水位线")

            # ---------- 守恒校验：发现"追补旧文/移出文章"----------
            expected = (stored if isinstance(stored, int) else len(known_keys)) + len(fresh)
            deep_added = []
            if declared is not None and declared != expected:
                say("    ⚠ 守恒校验不符：声明 %d ≠ 上次 %s + 新增 %d = %d"
                    % (declared, stored, len(fresh), expected))
                say("      （多为作者事后把老文章补进合集，游标看不见）→ 触发全量重扫")
                meta2, arts2 = album_full_walk(biz, aid, max_pages=args.max_pages,
                                               verbose=False)
                if arts2:
                    have = set(a.get("key") or a.get("msgid") for a in fresh) | known_keys
                    deep_added = [a for a in arts2
                                  if (a.get("key") or a.get("msgid")) not in have]
                    say("      全量重扫 %d 篇 → 额外发现 %d 篇（含追补的旧文）"
                        % (len(arts2), len(deep_added)))
                    if not deep_added and declared != len(arts2):
                        say("      声明篇数(%d) 与实取(%d) 仍不符；实取为准"
                            % (declared, len(arts2)))
            else:
                if declared is not None:
                    say("    ✓ 守恒校验通过（声明 %d = 上次 %s + 新增 %d）"
                        % (declared, stored, len(fresh)))

            # 合并本轮全部新文
            merged_new, seen_k = [], set()
            for x in fresh + deep_added:
                k = x.get("key") or x.get("msgid")
                if k and k not in seen_k:
                    seen_k.add(k)
                    merged_new.append(x)
            merged_new.sort(key=lambda x: int(x.get("create_time") or 0))

            # 更新状态
            # ⚠ 必须 sorted：known_keys 是 set，list(set) 的顺序是哈希序，
            #   直接存会让状态文件每轮无意义抖动，且"最后 N 个 key"这种读法会错。
            new_keys = sorted(known_keys | seen_k)
            newest = watermark
            if merged_new:
                newest = _item_brief(merged_new[-1])
            st["albums"].setdefault(aid, {}).update({
                "biz": biz, "nickname": nick, "album_title": title,
                "article_count": declared if declared is not None else (len(new_keys)),
                "keys": new_keys, "newest": newest,
                "last_run": now_s(), "last_new": len(merged_new)})

            for x in merged_new:
                b_ = _item_brief(x)
                b_.update({"account": nick, "album": title, "source_url": x.get("url")})
                all_new.append(b_)

            # ---------- 抓正文（新文通常 0~3 篇，很轻）----------
            if merged_new and not args.no_fetch:
                say("    ▸ 抓新文正文 %d 篇" % len(merged_new))
                okc, newalb = 0, []
                for b_ in [x for x in all_new if x.get("account") == nick
                           and x.get("album") == title]:
                    d = fetch_text(b_.get("url", ""))
                    b_["chars"] = d.get("chars", 0)
                    b_["text_ok"] = bool(d.get("ok"))
                    b_["text"] = d.get("text", "")
                    b_["perm_key"] = d.get("perm_key") or b_.get("key")
                    if d.get("ok"):
                        okc += 1
                    for tn, tid in (d.get("tags") or []):
                        if tid not in albums and tid not in [x[1] for x in newalb]:
                            newalb.append((tn, tid))
                    say("      %s %s  %-44s %4d 字  [%s]"
                        % ("★" if d.get("ok") else "✗", b_.get("date"),
                           (b_.get("title") or "")[:44], b_.get("chars", 0), d.get("how")))
                    if not d.get("ok"):
                        say("          原因: %s" % d.get("err"))
                    time.sleep(random.uniform(5.0, 7.5))
                say("    正文成功率 %d/%d" % (okc, len(merged_new)))
                if newalb:
                    say("    🔎 发现**未登记**合集（下次自动带上）：")
                    for tn, tid in newalb:
                        say("        %s %s" % (tn, tid))
                    reg_account(reg, biz, "", "", newalb)
                    notes.append("%s 新合集已登记：%s" % (nick, [x[0] for x in newalb]))

    # ---------- 汇总 ----------
    say("")
    say("=" * 78)
    covered = sum(len(a.get("albums") or {}) for _, a in accts)
    say("【汇总】本次新增 %d 篇   （覆盖 %d 个已登记合集）" % (len(all_new), covered))
    for b_ in sorted(all_new, key=lambda x: -int(x.get("create_time") or 0)):
        say("   %s  [%s·%s]  %-40s %s字"
            % (b_.get("date"), b_.get("account"), b_.get("album"),
               (b_.get("title") or "")[:40], b_.get("chars") if b_.get("chars") else "-"))
    if notes:
        say("【注意】")
        for n_ in notes:
            say("   · %s" % n_)
    if not all_new:
        say("   （无新文 —— 这就是「入口即拦截」：没有任何重复数据进入下游）")

    _save(OUT_NEW, all_new)
    if all_new:
        with io.open(OUT_INBOX, "a", encoding="utf-8", newline="") as f:
            for b_ in all_new:
                f.write(json.dumps(b_, ensure_ascii=False) + "\n")
    io.open(OUT_TXT, "w", encoding="utf-8", newline="").write("\n".join(_log))
    say("")
    say("【产物】新增清单 %s\n        追加归档 %s\n        水位线   %s\n        运行日志 %s"
        % (OUT_NEW, OUT_INBOX, STATE, OUT_TXT))


# ============================================================ ③ 全量
def run_full(reg, st, args):
    accts = resolve_accounts(reg, args.account)
    for biz, acct in accts:
        nick = acct.get("nickname") or biz
        say("=" * 78)
        say("【全量】号名 = %s   已知合集 %d 个" % (nick, len(acct.get("albums", {}))))
        merged, per = {}, []
        for aid, m in (acct.get("albums") or {}).items():
            say("")
            say("  ▸ 合集《%s》 %s" % (m.get("title") or "?", aid))
            meta, arts = album_full_walk(biz, aid, max_pages=args.max_pages)
            if not arts:
                say("    ✗ 空或失败")
                continue
            m.update({"last_count": len(arts),
                      "last_title": meta.get("title") or m.get("title"),
                      "article_count": meta.get("article_count"),
                      "desc": meta.get("description"),
                      "read_count": meta.get("read_count"),
                      "last_scan": now_s()})
            per.append((meta.get("title") or m.get("title"), aid, arts))
            say("    合集名=%s  声明篇数=%s  实取=%d  %s ~ %s  阅读=%s"
                % (meta.get("title"), meta.get("article_count"), len(arts),
                   dstr(arts[0]["create_time"]), dstr(arts[-1]["create_time"]),
                   meta.get("read_count")))
            for a in arts:
                k = a.get("key") or a.get("msgid")
                if k not in merged:
                    merged[k] = dict(a)
                    merged[k]["_albums"] = [meta.get("title") or aid]
                else:
                    merged[k].setdefault("_albums", []).append(meta.get("title") or aid)
        if len(per) > 1:
            say("")
            say("【合集去重】")
            for i in range(len(per)):
                for j in range(i + 1, len(per)):
                    s1 = {a["key"] for a in per[i][2]}
                    s2 = {a["key"] for a in per[j][2]}
                    say("  %s ∩ %s = %d 篇" % (per[i][0], per[j][0], len(s1 & s2)))
        arts = sorted(merged.values(), key=lambda x: int(x.get("create_time") or 0))
        say("")
        say("【覆盖度】")
        coverage(arts)
        # 顺带刷新水位线
        for name, aid, aa in per:
            st["albums"][aid] = {
                "biz": biz, "nickname": nick, "album_title": name,
                "article_count": len(aa),
                "keys": sorted([a.get("key") or a.get("msgid") for a in aa]),
                "newest": _item_brief(aa[-1]), "last_run": now_s(),
                "baseline_at": (st["albums"].get(aid) or {}).get("baseline_at") or now_s()}
        n = min(args.fetch, len(arts))
        if n:
            say("")
            say("【抓正文】最近 %d 篇" % n)
            okc, newalb = 0, []
            for a in arts[-n:]:
                d = fetch_text(a.get("url", ""))
                a["chars"] = d.get("chars", 0)
                a["text"] = d.get("text", "")
                a["perm_key"] = d.get("perm_key") or a.get("key")
                if d.get("ok"):
                    okc += 1
                for tn, tid in (d.get("tags") or []):
                    if tid not in (acct.get("albums") or {}):
                        newalb.append((tn, tid))
                say("  %s %s  %-46s %4d 字  [%s]"
                    % ("★" if d.get("ok") else "✗", dstr(a["create_time"]),
                       (a.get("title") or "")[:46], a.get("chars", 0), d.get("how")))
                time.sleep(random.uniform(5.0, 7.5))
            say("  成功率 %d/%d" % (okc, n))
            if newalb:
                for tn, tid in newalb:
                    say("  🔎 未登记合集：%s %s" % (tn, tid))
                reg_account(reg, biz, "", "", newalb)
        payload = [{"title": a.get("title"), "date": dstr(a.get("create_time")),
                    "create_time": a.get("create_time"), "msgid": a.get("msgid"),
                    "itemidx": a.get("itemidx"), "key": a.get("key"),
                    "url": a.get("url"), "albums": a.get("_albums"),
                    "chars": a.get("chars"), "text": a.get("text", "")} for a in arts]
        _save(OUT_FULL, payload)
        io.open(OUT_TXT, "w", encoding="utf-8", newline="").write("\n".join(_log))
        say("")
        say("【产物】全量清单 %s（%d 篇）\n        水位线 %s" % (OUT_FULL, len(arts), STATE))


def coverage(arts):
    if not arts:
        return
    ts = [int(a.get("create_time") or 0) for a in arts if a.get("create_time")]
    say("  并集 %d 篇  %s ~ %s" % (len(arts), dstr(min(ts)), dstr(max(ts))))
    import collections
    c = collections.Counter(datetime.datetime.fromtimestamp(t).year for t in ts)
    say("  按年：" + "  ".join("%d:%d篇" % (y, c[y]) for y in sorted(c)))
    gaps, prev = [], None
    for t in sorted(ts):
        if prev is not None:
            dd = (t - prev) / 86400.0
            if dd > 30:
                gaps.append((dstr(prev), dstr(t), int(dd)))
        prev = t
    if gaps:
        say("  ⚠ 断档 >30 天 %d 处（可能藏着未登记的合集）：" % len(gaps))
        for g_ in gaps[:12]:
            say("      %s -> %s  (%d 天)" % g_)
    else:
        say("  ✓ 无 >30 天断档 —— 时间线连续，已登记合集很可能覆盖该号近期全部文章")


# ============================================================ ④ 合集覆盖审计
def run_audit(reg, st, args):
    """抽样扫描已采文章，检查是否存在**未登记**的合集

    ⚠ 诚实边界（实测得出，别把它当证明）：
      该号的实际形态是「一篇文章只属于一个合集」（14 篇跨两合集抽样，0 多归属，
      album_info_list / tags / 页面 album_id 三处取值一致且都为 1 项）。
      因此抽样**只在"某篇恰好同属两合集"时**才能发现新合集 ——
      本命令的结论只能是"未发现"，**不能**是"已排除"。
      真正可靠的发现方式是拿一篇属于新合集的文章链接走 --from-url。
    """
    accts = resolve_accounts(reg, args.account)
    known = {}
    for biz, acct in accts:
        for aid, m in (acct.get("albums") or {}).items():
            known[aid] = m.get("title") or aid

    pool = _load(OUT_FULL, [])
    if not pool:
        say("✗ 需要先跑一次 --full 生成 %s（审计样本取自该清单）"
            % os.path.basename(OUT_FULL))
        return
    pool = sorted(pool, key=lambda x: int(x.get("create_time") or 0))
    n, k = len(pool), min(args.sample, len(pool))
    idx = sorted(set(int(round(i * (n - 1) / max(k - 1, 1))) for i in range(k)))

    say("=" * 78)
    say("【合集覆盖审计】%s" % now_s())
    say("  账号：%s" % "、".join((a.get("nickname") or biz) for biz, a in accts))
    say("  已知合集 %d 个：%s" % (len(known), "、".join(known.values())))
    say("  样本：从 %s ~ %s 共 %d 篇里按时间等距取 %d 篇"
        % (pool[0].get("date"), pool[-1].get("date"), n, k))
    say("")

    unknown, multi, okc, seen_ids = {}, [], 0, {}
    for s_ in [pool[i] for i in idx]:
        d = fetch_text(s_.get("url", ""))
        if not d.get("ok"):
            say("   ✗ %s %-30s 失败：%s"
                % (s_.get("date"), (s_.get("title") or "")[:30], d.get("err")))
            time.sleep(random.uniform(5.0, 7.0))
            continue
        okc += 1
        alb = d.get("tags") or []
        ids = [i for _, i in alb]
        if len(ids) > 1:
            multi.append((s_.get("title"), alb))
        for n_, i_ in alb:
            seen_ids.setdefault(i_, n_)
            if i_ not in known:
                unknown[i_] = n_
        say("   %s [%s] %-26s -> %s%s"
            % (s_.get("date"), "/".join(s_.get("albums") or ["?"]),
               (s_.get("title") or "")[:26], ids or "无",
               "   ★多归属！" if len(ids) > 1 else ""))
        time.sleep(random.uniform(6.0, 8.0))

    say("")
    say("  取文成功 %d/%d 篇；多归属 %d 篇" % (okc, k, len(multi)))
    say("  抽到的 album_id：%s" % {i: n_ for i, n_ in seen_ids.items()})
    if unknown:
        say("  🔎 发现未登记合集：")
        for i_, n_ in unknown.items():
            say("      %s   %s" % (n_ or "?", i_))
        if len(accts) == 1:
            reg_account(reg, accts[0][0], "", "", list(unknown.items()))
            save_reg(reg)
            say("      ✅ 已自动登记（下次 --daily/--full 会带上）")
        else:
            say("      ⚠ 多账号，未自动登记；请用 --account 指定后再跑")
            for biz, acct in accts:
                reg_account(reg, biz, "", "", list(unknown.items()))
            save_reg(reg)
    else:
        say("  ✓ 未发现未登记合集")
    say("")
    say("  ⚠ 这不等于『已排除第三合集』：实测该号『一篇文章只属于一个合集』，")
    say("     抽样只有撞上跨合集文章才能发现新合集。要确定的发现，")
    say("     给一篇属于新合集的文章链接走 --from-url。")


# ============================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-url", help="该号某篇文章链接，用于学习 biz/合集")
    ap.add_argument("--account", help="号名 / 微信号 / __biz / 合集名（缺省=全部账号）")
    ap.add_argument("--add-album", help="手动登记 album_id（配合 --account）")
    ap.add_argument("--daily", action="store_true", help="★ 每日增量追踪（推荐）")
    ap.add_argument("--full", action="store_true", help="全量重扫（建/重建基线）")
    ap.add_argument("--list", action="store_true", help="打印注册表 + 水位线")
    ap.add_argument("--no-fetch", action="store_true", help="增量模式不抓正文，只列新文")
    ap.add_argument("--audit", action="store_true",
                    help="抽样审计有无**未登记**合集（需先跑过 --full）")
    ap.add_argument("--sample", type=int, default=20, help="--audit 抽样篇数（默认 20）")
    ap.add_argument("--baseline-as-new", action="store_true",
                    help="首轮建基线时把全量当成本次新增（用于首次导入）")
    ap.add_argument("--fetch", type=int, default=0, help="--full 时抓最近 N 篇正文")
    ap.add_argument("--max-pages", type=int, default=12, help="单合集最多翻页数")
    args = ap.parse_args()

    reg = load_reg()
    st = load_state()

    if args.list:
        for biz, a in reg["accounts"].items():
            say("=" * 76)
            say("号名 %s   微信号 %s" % (a.get("nickname"), a.get("username")))
            say("  __biz = %s" % biz)
            say("  已知合集 %d 个：" % len(a.get("albums", {})))
            for aid, m in a.get("albums", {}).items():
                s = st["albums"].get(aid) or {}
                nw = s.get("newest") or {}
                say("    · %-14s %s" % (m.get("title") or "?", aid))
                if s:
                    say("        水位线 %s《%s》 | 已见 %d 篇 | 声明 %s 篇 | 上次跑 %s（新增 %s）"
                        % (nw.get("date"), (nw.get("title") or "")[:22],
                           len(s.get("keys") or []), s.get("article_count"),
                           s.get("last_run"), s.get("last_new")))
                else:
                    say("        水位线：尚未建立")
        return

    if args.from_url:
        say("【① 学习文章】%s" % args.from_url)
        info = learn_from_url(reg, args.from_url)
        if not info:
            say("✗ 学习失败")
            sys.exit(2)
        save_reg(reg)
        say("  （已登记。接着跑 --account <号名> --daily 即开始追踪）")

    if args.add_album:
        if not args.account:
            if len(reg["accounts"]) != 1:
                say("✗ --add-album 需配合 --account（注册表有多个号）")
                sys.exit(2)
            biz_ = list(reg["accounts"])[0]
        else:
            biz_ = resolve_accounts(reg, args.account)[0][0]
        reg_account(reg, biz_, "", "", [("(手动登记)", args.add_album)])
        save_reg(reg)
        say("✅ 已登记 album_id = %s" % args.add_album)

    if args.daily:
        run_daily(reg, st, args)
        save_reg(reg)
        save_state(st)
    elif args.full:
        run_full(reg, st, args)
        save_reg(reg)
        save_state(st)
    elif args.audit:
        run_audit(reg, st, args)
        save_reg(reg)
    elif not args.from_url and not args.add_album and not args.list:
        ap.print_help()


if __name__ == "__main__":
    try:
        main()
    finally:
        close_browser()
