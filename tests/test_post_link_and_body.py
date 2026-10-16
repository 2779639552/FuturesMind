"""「采集帖子」模块: 雪球链接可跳转 + 抖音显示评论正文 —— 回归测试(2026-09-18)。

═══ 两个用户可见缺陷, 根因都在"口径", 不在采集能力 ═══

① 雪球帖子点了跳不过去
   雪球适配器落盘的 url 是**根相对路径**("/9551545711/399073978" 或
   "/S/SH600519/123456"), 全站六平台里只有它如此。前端把它写进 <a href> 后,
   浏览器按"本站域名下的路径"解析 => http://localhost:5000/9551545711/...
   => 必然 404。适配器里本来有兜底 `target or f"https://xueqiu.com/{sid}"`,
   但 `or` 只在 target **为空**时短路, 而 target 恰恰永远有值 —— 兜底形同虚设。

② 抖音卡片只显示视频题目, 看不到评论内容
   抖音评论型记录(note_type="comment", 占抖音 ~90%)的 title 字段装的是
   **所属视频标题**("[抖音评论] {视频标题}"), 真正的内容在 desc 里。
   原实现一律取 `title or desc`(title 优先) => 页面上看到的永远是视频题目。
   实测 desc 100% 有值, 所以这是展示层选错字段, 与采集无关。

═══ 为什么两个都要在展示层再兜一层 ═══
采集侧修好后, **存量 5000+ 条历史数据不会回溯重写**; 只修采集侧, 用户在当前
页面上看到的仍然全是打不开的链接 / 错的内容。所以 _absolutize_url 与
_split_post_text 这两个纯函数是"正交降级通道": 不假设上游已修。
下面 test_*_legacy_records_* 专门锁这一点 —— 它们喂进去的就是**未修形态**的数据。
"""

import json
import sys
from pathlib import Path

import pytest

import web_app
from web_app import _absolutize_url, _split_post_text


# ══════════════════════════════════════════════════════════════════
# 1) URL 补全: 纯函数边界
# ══════════════════════════════════════════════════════════════════

def test_xueqiu_two_segment_url_gets_domain():
    """雪球主流形态 /{uid}/{sid}(实测 5273/5849 条)。"""
    assert _absolutize_url("/9551545711/399073978", "xueqiu") == (
        "https://xueqiu.com/9551545711/399073978"
    )


def test_xueqiu_stock_page_url_gets_domain():
    """雪球股票页形态 /S/{symbol}/{sid}(实测 576 条) —— 补域名规则必须一视同仁。"""
    assert _absolutize_url("/S/SH600519/123456", "xueqiu") == (
        "https://xueqiu.com/S/SH600519/123456"
    )


def test_already_absolute_url_is_untouched():
    """其余五平台本来就是绝对 URL, 不得被二次拼接。"""
    for u in (
        "https://www.douyin.com/video/7680896821983765242",
        "https://xueqiu.com/9551545711/399073978",
        "http://example.com/a",
    ):
        assert _absolutize_url(u, "xueqiu") == u


def test_empty_url_passthrough():
    """空值返回空串(不是 None, 也不是拼出一截光秃秃的域名)。"""
    assert _absolutize_url("", "xueqiu") == ""
    assert _absolutize_url(None, "xueqiu") == ""


def test_unknown_platform_relative_url_not_guessed():
    """**保守分支**: 不认识的平台 + 相对路径 => 原样返回。

    宁可"点了不跳", 也不要猜一个域名把用户送到无关站点(比如把 weibo 的相对
    路径接到 xueqiu 上)。这条断言锁住"不猜"的取向。
    """
    assert _absolutize_url("/abc/1", "weibo") == "/abc/1"
    assert _absolutize_url("/abc/1", "") == "/abc/1"


def test_url_whitespace_and_slashes_normalized():
    assert _absolutize_url("  /1/2  ", "xueqiu") == "https://xueqiu.com/1/2"
    assert _absolutize_url("//1/2", "xueqiu") == "https://xueqiu.com/1/2"


# ══════════════════════════════════════════════════════════════════
# 2) 帖子文本拆分: 纯函数边界
# ══════════════════════════════════════════════════════════════════

def _dy_comment(title="[抖音评论] 9月的甲醇。 #甲醇 #甲醇期货", desc="周一甲醇必高开"):
    return {"platform": "douyin", "note_type": "comment", "title": title, "desc": desc}


def test_douyin_comment_body_is_the_comment_text():
    """核心: 评论型记录的展示主体必须是 desc(评论正文), 不是 title(视频标题)。"""
    body, parent = _split_post_text(_dy_comment())
    assert body == "周一甲醇必高开"
    assert parent == "[抖音评论] 9月的甲醇。 #甲醇 #甲醇期货"


def test_douyin_sub_comment_variant_prefix_also_detected():
    """mediacrawler 导入链路的前缀是 "[抖音评论·回复]"。

    判定以 note_type 为主、前缀为辅(取或), 所以前缀拼写变体不会漏 ——
    单看前缀的实现会在这里失败。
    """
    d = {"note_type": "comment", "title": "[抖音评论·回复] 9月的甲醇", "desc": "同意"}
    assert _split_post_text(d)[0] == "同意"


def test_comment_detected_by_prefix_when_note_type_missing():
    """没有 note_type 但有前缀的老记录同样识别(双判据的另一半)。"""
    d = {"title": "[抖音评论] 9月的甲醇", "desc": "正文"}
    assert _split_post_text(d)[0] == "正文"


def test_douyin_video_record_unchanged():
    """视频型记录 title 与 desc 本来就相同, 不得被改动。"""
    d = {"note_type": "video", "title": "9月的甲醇", "desc": "9月的甲醇"}
    assert _split_post_text(d) == ("9月的甲醇", "")


def test_other_platforms_unchanged():
    """雪球/知乎等非评论型记录沿用 title, 且不应冒出附注。"""
    d = {"note_type": "normal", "title": "螺纹钢 with AI", "desc": "螺纹钢期货可以理解为…"}
    assert _split_post_text(d) == ("螺纹钢 with AI", "")


def test_comment_with_empty_desc_falls_back_to_title():
    """desc 缺失时不能把卡片搞成空白 —— 回落到 title。"""
    d = {"note_type": "comment", "title": "[抖音评论] x", "desc": ""}
    assert _split_post_text(d) == ("[抖音评论] x", "")


def test_empty_title_falls_back_to_desc():
    assert _split_post_text({"note_type": "normal", "title": "", "desc": "只有正文"}) == (
        "只有正文", ""
    )


def test_all_empty_returns_empty_strings():
    """全空记录返回空串而非 None(前端拼接不会出现 "undefined")。"""
    assert _split_post_text({}) == ("", "")


def test_body_truncated_to_120_chars():
    """长度上限与旧实现一致(120), 避免卡片被超长评论撑爆。"""
    d = {"note_type": "comment", "title": "t", "desc": "字" * 500}
    assert len(_split_post_text(d)[0]) == 120


# ══════════════════════════════════════════════════════════════════
# 3) 端到端: 接口返回给前端的就是"可直接用"的字段
# ══════════════════════════════════════════════════════════════════

def _rec(note_id, platform, url, title="标题", desc="", note_type="normal", day=16):
    return {
        "note_id": note_id,
        "platform": platform,
        "publish_time": f"2026-09-{day:02d} 10:00:00",
        "title": title,
        "desc": desc,
        "note_type": note_type,
        "author_name": "测试作者",
        "author_fans": 1000,
        "sentiment": "neutral",
        "sentiment_score": 0.1,
        "varieties": [],
        "like_count": 5,
        "url": url,
    }


def _client(tmp_path, monkeypatch, records):
    out = tmp_path / "output"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "batch_mix_20260916_100000.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    monkeypatch.setattr(web_app, "THINK2_OUTPUT", out)
    return web_app.app.test_client()


def test_api_returns_absolute_url_for_legacy_xueqiu_records(tmp_path, monkeypatch):
    """端到端(存量形态): 落盘时是相对路径的雪球记录, 出接口时必须是绝对 URL。

    这条喂的就是**未修形态**的历史数据 —— 采集侧修好之后, 存量几万条仍然长这样,
    展示层必须自己收拾干净。
    """
    recs = [
        _rec("xq:1", "xueqiu", "/9551545711/399073978"),
        _rec("xq:2", "xueqiu", "/S/SH600519/123456"),
        _rec("wb:1", "weibo", "https://weibo.com/1/2"),
    ]
    c = _client(tmp_path, monkeypatch, recs)

    posts = c.get("/api/sentiment_posts?days=0").get_json()["posts"]
    by_id = {p["note_id"]: p for p in posts}

    assert by_id["xq:1"]["url"] == "https://xueqiu.com/9551545711/399073978"
    assert by_id["xq:2"]["url"] == "https://xueqiu.com/S/SH600519/123456"
    assert by_id["wb:1"]["url"] == "https://weibo.com/1/2", "绝对 URL 被改动了"
    assert all(p["url"].startswith("http") for p in posts), "接口仍漏出相对路径"


def test_api_douyin_comment_returns_comment_body(tmp_path, monkeypatch):
    """端到端: 抖音评论型记录的 title 是评论正文, parent_title 是所属视频。"""
    recs = [
        _rec("dy:1", "douyin", "https://www.douyin.com/video/123",
             title="[抖音评论] 9月的甲醇。 #甲醇", desc="周一甲醇必高开",
             note_type="comment"),
        _rec("dy:2", "douyin", "https://www.douyin.com/video/456",
             title="9月的甲醇", desc="9月的甲醇", note_type="video"),
    ]
    c = _client(tmp_path, monkeypatch, recs)

    by_id = {p["note_id"]: p for p in c.get("/api/sentiment_posts?days=0").get_json()["posts"]}

    assert by_id["dy:1"]["title"] == "周一甲醇必高开", "抖音卡片没显示评论正文"
    assert by_id["dy:1"]["parent_title"] == "[抖音评论] 9月的甲醇。 #甲醇", "视频标题丢失"
    # 视频型记录不受影响, 且不该冒出附注
    assert by_id["dy:2"]["title"] == "9月的甲醇"
    assert by_id["dy:2"]["parent_title"] == ""


def test_api_non_comment_platforms_have_no_parent_title(tmp_path, monkeypatch):
    """非评论型平台不应多出 parent_title(前端据此判断是否渲染附注)。"""
    recs = [_rec("xh:1", "xhs", "https://www.xiaohongshu.com/x", title="库存去化")]
    c = _client(tmp_path, monkeypatch, recs)

    p = c.get("/api/sentiment_posts?days=0").get_json()["posts"][0]
    assert p["parent_title"] == ""
    assert p["title"] == "库存去化"


def test_api_events_urls_absolutized(tmp_path, monkeypatch):
    """舆情事件卡片走的是另一条读链路, 但渲染同一个 url 字段 —— 同样必须补全。"""
    monkeypatch.setattr(web_app, "extract_events", lambda variety="", days=7: [
        {"date": "2026-09-16", "platform": "xueqiu", "title": "某事件",
         "url": "/9551545711/399073978", "categories": [], "varieties": []},
        {"date": "2026-09-16", "platform": "weibo", "title": "某事件2",
         "url": "https://weibo.com/1", "categories": [], "varieties": []},
    ])
    c = web_app.app.test_client()

    events = c.get("/api/analysis/events?days=7").get_json()
    assert events[0]["url"] == "https://xueqiu.com/9551545711/399073978"
    assert events[1]["url"] == "https://weibo.com/1"


# ══════════════════════════════════════════════════════════════════
# 4) 采集侧: 根治新数据 + 两副本同步纪律
# ══════════════════════════════════════════════════════════════════

_ADAPTER_REL = "data_collection/validate/platforms/xueqiu_adapter.py"


def _xueqiu_adapter_module():
    """按真实包路径加载采集适配器(命名空间包, 各层无需 __init__.py)。"""
    root = Path(web_app.__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import data_collection.validate.platforms.xueqiu_adapter as m
    return m


def _raw_xueqiu(target, sid=399073978):
    """造一条最小可用的雪球 search() 原生 item。"""
    rec = {
        "id": sid,
        "title": "螺纹钢 with AI",
        "description": "螺纹钢期货可以理解为…",
        "created_at": 1751962503000,
        "user": {"screen_name": "测试", "followers_count": 1},
    }
    if target is not None:
        rec["target"] = target
    return rec


def test_adapter_normalize_returns_absolute_url_for_relative_target():
    """采集侧根治(行为测试): 根相对 target 必须落盘成绝对 URL。

    ⚠️ 这条刻意**不查源码字符串**, 而是真的调 normalize 看返回值 —— 原先的文本
    断言被 `if False and ...` 骗过了一次(字符串还在, 分支已死), 只有行为断言可靠。
    """
    m = _xueqiu_adapter_module()
    rec = m.XueqiuAdapter().normalize(_raw_xueqiu("/9551545711/399073978"), None, "螺纹钢")
    assert rec["url"] == "https://xueqiu.com/9551545711/399073978"


def test_adapter_normalize_handles_stock_page_target():
    """股票页形态 /S/{symbol}/{sid} 同样补域名。"""
    m = _xueqiu_adapter_module()
    rec = m.XueqiuAdapter().normalize(_raw_xueqiu("/S/SH600519/123456", sid=123456), None, "铁矿石")
    assert rec["url"] == "https://xueqiu.com/S/SH600519/123456"


def test_adapter_normalize_falls_back_when_target_missing():
    """target 缺失时用 id 兜底 —— 原实现里唯一真正会生效的分支。"""
    m = _xueqiu_adapter_module()
    rec = m.XueqiuAdapter().normalize(_raw_xueqiu(None), None, "螺纹钢")
    assert rec["url"] == "https://xueqiu.com/399073978"


def test_adapter_normalize_keeps_absolute_target():
    m = _xueqiu_adapter_module()
    rec = m.XueqiuAdapter().normalize(
        _raw_xueqiu("https://xueqiu.com/9551545711/1"), None, "螺纹钢"
    )
    assert rec["url"] == "https://xueqiu.com/9551545711/1"


def test_adapter_url_helper_boundaries():
    """纯函数直测(含边界), 与 normalize 层各管一段, 失败时便于定位是哪一层。"""
    f = _xueqiu_adapter_module()._absolute_xueqiu_url
    assert f("/1/2", "9") == "https://xueqiu.com/1/2"
    assert f("  /1/2  ", "9") == "https://xueqiu.com/1/2"
    assert f("//1/2", "9") == "https://xueqiu.com/1/2"
    assert f("", "9") == "https://xueqiu.com/9"
    assert f(None, "9") == "https://xueqiu.com/9"
    assert f("https://x.com/a", "9") == "https://x.com/a"
    assert f("http://x.com/a", "9") == "http://x.com/a"


def test_adapter_copies_identical():
    """两副本必须字节一致(源的 data_collection/validate 与运行时的 思路2/validate)。

    只改一份 = 下次采集或本地验证时行为不一致, 是这套双副本结构最容易踩的坑。
    """
    root = Path(web_app.__file__).resolve().parent
    dev = root / _ADAPTER_REL
    prod = root.parent / "思路2" / "validate" / "platforms" / "xueqiu_adapter.py"
    if not prod.exists():
        pytest.skip("运行副本不存在(非双副本环境)")
    assert dev.read_bytes() == prod.read_bytes(), (
        "xueqiu_adapter.py 两副本不一致 —— 改完必须同步到运行副本"
    )
