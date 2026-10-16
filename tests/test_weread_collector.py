# -*- coding: utf-8 -*-
r"""公众号采集 · 微信读书通道 回归护栏（2026-10-14）。

背景（一句话）：机构号**不建合集**，「合集通道」对它们完全失效；但微信读书收录了
这些号 ⇒ 走 `/web/mp/articles` + `/web/mp/content` 这条私有接口可取到文章清单与正文。
本文件守住这条链路里**已经踩过的坑**与**与下游的契约**。

覆盖（全部离线，不联网）：

  A. 纯函数 `parse_content_html` —— 正文页解析
     ① ★ **原文链接必须取 `var msg_link`**：实测同一次群发的 5 篇，页面里那个
        `https://mp.weixin.qq.com/s/<token>` **全都一样**（模板固定链接），
        拿它会让 5 篇写成同一个 URL（2026-10-14 真实事故）。
     ② `&amp;` 必须还原成 `&`（否则 URL 带 HTML 实体，不可点）。
     ③ 正文优先 `content_noencode`（\\x0a\\x22 转义），回退 `id="js_content"`。

  B. 纯函数 `expand_articles` —— 清单回包的**两层结构**
     ★ 回包是 `{reviews[]}`，文章藏在 `reviews[i].subReviews[j].review.mpInfo`；
       **只读第一层会漏掉子文章**（实测 20 个群发 → 105 篇）。

  C. `build_entry` 的**下游字段契约**（wechat_ingest.py 需要哪几个字段）。

  D. ★ **集成不变量**：本通道的 inbox 必须与合集通道**同一个文件**
     —— 这是"改动零侵入下游"的全部依据；一旦被改成另一个路径，入库任务将静默收不到。

  E. 静态接线：scheduler 的采集步骤里确实跑了两路，且告警同时报两路计数。

  F. 路径回归：发现公众号必须用 `/web/shelf/sync`（`/shelf/sync` 在私有域下不是 JSON）。

  G. EOL 铁律：新采集器是 LF，scheduler.py 保持纯 CRLF。
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from wechat_weread_collector import (  # noqa: E402
        build_entry, expand_articles, parse_content_html,
    )
    _IMPORT_OK = True
except Exception:  # pragma: no cover
    _IMPORT_OK = False

pytestmark = pytest.mark.skipif(not _IMPORT_OK, reason="wechat_weread_collector 未就绪")

COLLECTOR = ROOT / "wechat_weread_collector.py"
SCHEDULER = ROOT / "scheduler.py"

# 一段够长的正文（> MIN_BODY_CHARS = 200），避免"正文过短"被判失败
LONG_BODY = "这是正文第一段，用于测试解析。" * 25


def _reader_src(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _code_only(src: str) -> str:
    """剥掉整行注释后再匹配。

    ⚠️ 教训（2026-10-14，本项目同一天踩过两次）：残留检查直接 `substr in src`
    会被**自己新写的注释**命中 —— 注释里往往照抄了被替换掉的旧写法，于是伪造出
    "还有残留"的假红。剥注释比"改注释措辞"更稳（后者下一个人写注释还会翻车）。
    """
    out = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


# ════════════════════════════════════════════════════════════
# A. parse_content_html —— 正文页解析
# ════════════════════════════════════════════════════════════
class TestParseContentHtml:
    def _page(self, *, with_noencode=True, with_msg_link=True):
        parts = ["<html><head><script>"]
        if with_msg_link:
            parts.append(
                'var msg_link = "https://mp.weixin.qq.com/s?__biz=MzAxNzk3MjE3NQ=='
                '&amp;mid=2247513047&amp;idx=3&amp;sn=071d859b3bcf92#rd";'
            )
        # ★ 这个短链在真实页面里**每篇都一样**，是模板固定链接（绝不能采用）
        parts.append('var _g_share = "https://mp.weixin.qq.com/s/_2kC-fXw7UjneZSrsC9CVQ";')
        parts.append("var msg_title = '【有色早评】铝月差走扩';")
        if with_noencode:
            parts.append(
                "window.cgiData = {content_noencode: '"
                + LONG_BODY.replace("'", r"\x27")
                + "'};"
            )
        parts.append("</script></head><body></body></html>")
        return "".join(parts)

    def test_msg_link_is_used_not_template_shortlink(self):
        """★ 核心回归：不能用页面里那个每篇都相同的短链。

        这是 2026-10-14 的真实事故 —— 5 篇不同文章被写成同一个 URL。
        """
        got = parse_content_html(self._page())
        assert "idx=3" in got["url"], f"未取到 msg_link：{got['url']}"
        assert "_2kC-fXw7UjneZSrsC9CVQ" not in got["url"], (
            "取到了模板里的固定短链 —— 会把整次群发的所有文章写成同一个 URL"
        )

    def test_url_html_entities_unescaped(self):
        """`&amp;` 必须还原成 `&`，否则链接不可用。"""
        got = parse_content_html(self._page())
        assert "&amp;" not in got["url"], f"URL 仍含 HTML 实体：{got['url']}"
        assert "__biz=MzAxNzk3MjE3NQ==" in got["url"]

    def test_content_noencode_extracted(self):
        got = parse_content_html(self._page())
        assert got["ok"] is True
        assert got["how"] == "content_noencode"
        assert got["chars"] >= 200
        assert "这是正文第一段" in got["text"]

    def test_escapes_decoded(self):
        """\\x0a 要变成真换行（否则正文挤成一坨）。"""
        got = parse_content_html(self._page())
        assert r"\x0a" not in got["text"]

    def test_title_extracted(self):
        got = parse_content_html(self._page())
        assert got["title"] == "【有色早评】铝月差走扩"

    def test_js_content_fallback(self):
        """没有 content_noencode 时回退到 #js_content。"""
        html = (
            '<html><script>var msg_link = "https://mp.weixin.qq.com/s?idx=1";</script>'
            '<div id="js_content"><p>' + ("回退正文。" * 80) + "</p></div>"
            "<div>tail</div></html>"
        )
        got = parse_content_html(html)
        assert got["how"] == "js_content"
        assert got["chars"] >= 200

    def test_too_short_body_marked_not_ok(self):
        """正文过短必须 ok=False（下游按 <200 字跳过，别把公告当研报）。"""
        html = (
            "<html><script>var msg_link = \"https://mp.weixin.qq.com/s?idx=1\";"
            "window.cgiData = {content_noencode: '太短了'};</script></html>"
        )
        got = parse_content_html(html)
        assert got["ok"] is False
        assert got["chars"] < 200


# ════════════════════════════════════════════════════════════
# B. expand_articles —— 清单回包的两层结构
# ════════════════════════════════════════════════════════════
def _payload():
    """仿真实回包：一次「群发」里含 3 篇文章。"""
    def sub(i, title):
        oid = f"WR{1000 + i}abcdefghij"
        rid = f"MP_WXS_3869147758_{oid}"
        return {
            "reviewId": rid,
            "review": {
                "reviewId": rid, "userVid": 10003, "type": 16,
                "bookId": "", "belongBookId": "MP_WXS_3869147758",
                "mpRank": i,
                "mpInfo": {
                    "originalId": oid, "title": title,
                    "content": "股指、贵金属", "time": 1791937992 + i,
                    "readNum": 300 + i, "likeNum": 1,
                    "pic_url": "https://mmbiz.qpic.cn/x.jpg",
                    "mp_name": "混沌天成研究",
                },
            },
        }
    return {
        "reviews": [
            {"createTime": 1791937992, "subCount": 3,
             "subReviews": [sub(1, "宏观早评"), sub(2, "黑色早评"), sub(3, "有色早评")]},
            {"createTime": 1791900000, "subCount": 1,
             "subReviews": [sub(4, "能化早评")]},
        ],
        "clearAll": 1,
        "synckey": 1791944919,
    }


class TestExpandArticles:
    def test_subreviews_are_expanded(self):
        """★ 核心回归：必须展开第二层，否则会漏掉子文章。"""
        got = expand_articles(_payload(), "MP_WXS_3869147758")
        assert len(got) == 4, f"应展开出 4 篇（3+1），实得 {len(got)} ⇒ 可能只读了第一层"

    def test_fields_mapped(self):
        got = expand_articles(_payload(), "MP_WXS_3869147758")
        a = got[0]
        assert a["title"] == "宏观早评"
        assert a["mp_name"] == "混沌天成研究"
        assert a["time"] == 1791937993
        assert a["original_id"].startswith("WR")
        assert a["review_id"].startswith("MP_WXS_3869147758_")
        assert a["read_num"] == 301

    def test_review_id_falls_back_to_bid_plus_oid(self):
        """回包缺 reviewId 时要能兜底拼出来（否则取正文会失败）。"""
        p = _payload()
        p["reviews"][0]["subReviews"][0]["review"].pop("reviewId")
        p["reviews"][0]["subReviews"][0].pop("reviewId")
        got = expand_articles(p, "MP_WXS_3869147758")
        assert got[0]["review_id"] == \
            f"MP_WXS_3869147758_{got[0]['original_id']}"

    def test_empty_payload(self):
        assert expand_articles({}, "MP_WXS_x") == []
        assert expand_articles({"reviews": []}, "MP_WXS_x") == []

    def test_push_time_used_when_article_time_missing(self):
        p = _payload()
        p["reviews"][0]["subReviews"][0]["review"]["mpInfo"].pop("time")
        got = expand_articles(p, "MP_WXS_3869147758")
        assert got[0]["time"] == 1791937992


# ════════════════════════════════════════════════════════════
# C. build_entry —— 下游字段契约
# ════════════════════════════════════════════════════════════
class TestBuildEntryContract:
    """下游 wechat_ingest.py 只读这几个字段；缺一个就静默跳过或标错来源。"""

    def _entry(self):
        art = expand_articles(_payload(), "MP_WXS_3869147758")[0]
        content = {"ok": True, "text": LONG_BODY, "url": "https://mp.weixin.qq.com/s?idx=1",
                   "title": "", "chars": len(LONG_BODY), "how": "content_noencode", "err": ""}
        return build_entry("混沌天成研究", art, content, "MP_WXS_3869147758")

    @pytest.mark.parametrize("field", ["key", "perm_key", "account", "title",
                                       "date", "url", "text"])
    def test_required_fields_present(self, field):
        assert self._entry().get(field), f"下游依赖字段 `{field}` 缺失/为空"

    def test_account_is_the_label_source(self):
        """ingest 用 `account` 直接写 source='公众号-<号名>'，别塞成合集名。"""
        assert self._entry()["account"] == "混沌天成研究"

    def test_key_stable_and_unique_per_article(self):
        arts = expand_articles(_payload(), "MP_WXS_3869147758")
        c = {"ok": True, "text": LONG_BODY, "url": "", "title": "", "chars": 1,
             "how": "", "err": ""}
        keys = [build_entry("x", a, c, "MP_WXS_3869147758")["key"] for a in arts]
        assert len(set(keys)) == len(keys), "同一号内 key 必须唯一（幂等去重的前提）"
        assert keys[0] == f"wxmp_{arts[0]['original_id']}"

    def test_date_is_yyyy_mm_dd(self):
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", self._entry()["date"])

    def test_text_ok_false_when_content_failed(self):
        art = expand_articles(_payload(), "MP_WXS_3869147758")[0]
        bad = {"ok": False, "text": "", "url": "", "title": "", "chars": 0,
               "how": "", "err": "x"}
        assert build_entry("x", art, bad, "MP_WXS_3869147758")["text_ok"] is False


# ════════════════════════════════════════════════════════════
# D. ★ 集成不变量 —— inbox 必须与合集通道同一个文件
# ════════════════════════════════════════════════════════════
class TestInboxIsShared:
    """这是整套"零侵入下游"设计的**全部依据**，必须钉死。"""

    def test_collector_inbox_path_matches_ingest(self):
        """采集器写入的 inbox 路径 == 入库器读取的 inbox 路径。"""
        src = _reader_src(COLLECTOR)
        assert "wechat_inbox.jsonl" in src
        # 采集器：WECHAT_DIR / "wechat_inbox.jsonl"
        assert 'F_INBOX = WECHAT_DIR / "wechat_inbox.jsonl"' in src, \
            "采集器不再写合集通道那个 inbox ⇒ 下游入库会静默收不到"
        # 入库器：STATE_DIR / "wechat_inbox.jsonl"
        ing = _reader_src(ROOT / "wechat_ingest.py")
        assert 'F_INBOX = STATE_DIR / "wechat_inbox.jsonl"' in ing

    def test_both_use_same_default_state_dir(self):
        """两边默认目录都必须是 ~/.tradingagents/wechat（否则读写的不是同一份）。"""
        a = _reader_src(COLLECTOR)
        b = _reader_src(ROOT / "wechat_ingest.py")
        assert "AGENTSENSE_WECHAT_DIR" in a
        assert "AGENTSENSE_WECHAT_DIR" in b
        for s in (a, b):
            assert "tradingagents" in s and "wechat" in s


# ════════════════════════════════════════════════════════════
# E./F. 静态接线与路径回归
# ════════════════════════════════════════════════════════════
class TestWiring:
    def test_scheduler_runs_both_channels(self):
        src = _reader_src(SCHEDULER)
        assert "wechat_album_collector.py" in src, "合集通道被弄丢了"
        assert "wechat_weread_collector.py" in src, "读书通道未接入调度"
        assert '["wechat_weread_collector.py", "--daily"]' in src

    def test_scheduler_passes_weread_state_dir(self):
        src = _reader_src(SCHEDULER)
        assert "WEREAD_STATE_DIR" in src
        assert 'w_env["AGENTSENSE_WEREAD_DIR"]' in src, "未把读书通道状态目录传进子进程"

    def test_scheduler_alert_reports_both_counts(self):
        """告警要能一眼看出是哪个通道的新增，别只报一路。

        ⚠️ **必须把断言限定在告警块内**（2026-10-14 变异测试抓到的漏洞）：
        一开始写的是全文件 `in src`，结果 `"读书通道新发现"` 这个词在
        `_wechat_weread_count` 的 **docstring** 里也出现了一次 ⇒ 把告警里那半句
        删掉后断言**依然绿**（变异存活）。这是"断言命中注释"的同类坑：
        凡在源码里找一句话，先确认它**唯一**，否则要限定上下文。
        """
        src = _reader_src(SCHEDULER)
        i = src.index('"wechat_collect_done"')
        blk = src[i:i + 900]
        assert "合集通道新发现" in blk, "完成告警里没报合集通道计数"
        assert "读书通道新发现" in blk, "完成告警里没报读书通道计数"
        assert "wechat_collect_done" in blk

    def test_scheduler_weread_failure_is_isolated(self):
        """读书通道挂掉（cookie 过期）不能拖垮合集通道的结论 —— 用的是 warn 不是 return。"""
        src = _reader_src(SCHEDULER)
        assert "wechat_weread_collect_failed" in src
        # 读书通道失败后不能提前 return（那会让完成告警整条丢失）
        blk = src[src.index("_w_since = datetime.now().timestamp()"):]
        blk = blk[:blk.index("_safe_alert(db, \"wechat_collect_done\"")]
        assert "return" not in _code_only(blk), \
            "读书通道失败分支里出现了 return ⇒ 会吞掉后面的完成告警"

    def test_weread_count_reads_result_file_not_stdout(self):
        """计数只认结果文件（服务进程里 stdout 会整段丢，本项目已有前车之鉴）。"""
        src = _reader_src(SCHEDULER)
        blk = src[src.index("def _wechat_weread_count("):]
        blk = blk[:blk.index("def _wechat_step")]
        assert "mp_collect_result.json" in blk
        assert "getmtime" in blk, "缺 mtime 闸 ⇒ 可能读到上一轮的旧数字"

    def test_discover_uses_web_shelf_sync(self):
        """★ 路径回归：`/shelf/sync` 在私有域下不是 JSON（要 /web/shelf/sync）。"""
        src = _reader_src(COLLECTOR)
        assert '"/web/shelf/sync"' in src
        code = _code_only(src)
        assert '{"/shelf/sync"' not in code.replace('"/web/shelf/sync"', ""), \
            "仍有裸 /shelf/sync 调用（私有域下会拿到 HTML/报错）"

    def test_articles_endpoint_and_review_id_param(self):
        src = _reader_src(COLLECTOR)
        assert '"/web/mp/articles"' in src
        assert '"/web/mp/content"' in src
        assert '"reviewId": review_id' in src, "取正文的参数名必须是 reviewId"

    def test_empty_body_retry_exists(self):
        """接口偶发静默空回包 ⇒ 必须退避重试，否则会静默漏整页。"""
        src = _reader_src(COLLECTOR)
        blk = src[src.index("def fetch_articles("):]
        blk = blk[:blk.index("def parse_content_html")]
        assert "空回包" in blk and "retries" in blk


# ════════════════════════════════════════════════════════════
# G. EOL 铁律
# ════════════════════════════════════════════════════════════
class TestEol:
    @pytest.mark.parametrize("rel,expect_crlf", [
        ("wechat_weread_collector.py", False),
        ("wechat_album_collector.py", False),
        ("wechat_ingest.py", False),
        ("scheduler.py", True),
    ])
    def test_eol(self, rel, expect_crlf):
        b = (ROOT / rel).read_bytes()
        crlf = b.count(b"\r\n")
        bare = b.count(b"\n") - crlf
        if expect_crlf:
            assert bare == 0, f"{rel} 出现 {bare} 个裸 LF（应为纯 CRLF）"
        else:
            assert crlf == 0, f"{rel} 出现 {crlf} 个 CRLF（应为纯 LF）"


# ════════════════════════════════════════════════════════════
# H. 采集器文件存在性（防误删）
# ════════════════════════════════════════════════════════════
def test_collector_file_exists():
    assert COLLECTOR.exists(), "采集器被删了 ⇒ 调度里的读书通道会每轮报错"


def test_result_json_keys_used_by_scheduler():
    """采集器写的结果文件里必须有 `new` 键（scheduler 读它取计数）。"""
    src = _reader_src(COLLECTOR)
    assert '"new": len(entries)' in src or '"new":' in src
    sch = _reader_src(SCHEDULER)
    assert 'd.get("new")' in sch
