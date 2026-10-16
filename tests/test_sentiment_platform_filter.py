"""「采集帖子」模块的分平台查看功能 —— 后端筛选语义回归。

背景(2026-09-17):
  页面新增平台筛选 chip(全部/微博/知乎/小红书/雪球/东财股吧/抖音)。
  关键设计是**筛选下推给后端**,而不是在前端把已返回的帖子按 platform 过滤。

为什么这条设计必须被测试钉住:
  `/api/sentiment_posts` 单次最多回 200 条,且这 200 条是**全局分层抽样**
  (每平台保底 200 // 平台数 条)。也就是说前端手里根本没有"某平台的完整列表"。
  若在前端过滤,切到抖音只能看到它被抽中的那 ~33 条(实际采到 877 条),
  用户会直接判成"这个平台没采到" —— 与 2026-09-15"抽样饿死弱源"是同一个坑,
  只不过这次由筛选器自己制造。
  所以下面 test_filter_gives_full_quota_to_a_rich_platform 是这份文件的核心:
  它断言"筛选某平台后拿到的条数**远多于**它在全局抽样里的份额"。
"""

import json
from pathlib import Path

import pytest

import web_app
from web_app import POSTS_API_LIMIT


# ══════════════════════════════════════════════════════════════════
# 夹具:把 THINK2_OUTPUT 指到临时目录,返回 test_client 工厂
# ══════════════════════════════════════════════════════════════════

PLATFORMS = ["weibo", "zhihu", "xhs", "xueqiu", "eastmoney_guba", "douyin"]


def _rec(note_id, platform, publish_time="2026-09-16 10:00:00", title="库存去化跟踪"):
    """造一条最小可用的 batch 记录(字段对齐采集端产物)。"""
    return {
        "note_id": note_id,
        "platform": platform,
        "publish_time": publish_time,
        "title": title,
        "desc": "",
        "author_name": "测试作者",
        "author_fans": 1000,
        "sentiment": "neutral",
        "sentiment_score": 0.1,
        "varieties": [],
        "like_count": 5,
        "url": "https://example.com/x",
    }


def _write_output(tmp_path, records, name="batch_xhs_20260917_090142.jsonl") -> Path:
    out = tmp_path / "output"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / name, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


@pytest.fixture()
def client(tmp_path, monkeypatch):
    def _make(records, name="batch_xhs_20260917_090142.jsonl"):
        out = _write_output(tmp_path, records, name)
        monkeypatch.setattr(web_app, "THINK2_OUTPUT", out)
        return web_app.app.test_client()
    return _make


def _corpus(per_platform=100, base_day=16):
    """六平台等量语料(每平台 per_platform 条,时间各不相同以便排序稳定)。"""
    recs = []
    for pi, plat in enumerate(PLATFORMS):
        for i in range(per_platform):
            recs.append(_rec(
                f"{plat}-{i}", plat,
                # 用 i 拉开分钟,保证同一平台内时间有序;跨平台用 pi 错开
                f"2026-09-{base_day:02d} {10 + pi:02d}:{i % 60:02d}:00",
            ))
    return recs


# ══════════════════════════════════════════════════════════════════
# 核心:筛选必须给该平台足额名额(不是"在抽样结果里挑几条")
# ══════════════════════════════════════════════════════════════════

def test_filter_gives_full_quota_to_a_rich_platform(client):
    """核心回归:选中某平台后,该平台拿到的条数应远多于它在全局抽样里的份额。

    六平台各 100 条 → 全局抽样每平台保底 200//6 = 33 条。
    若筛选走"前端过滤抽样结果",这里只会得到 33 条;走"后端筛选"则拿到全部 100 条。
    """
    c = client(_corpus(per_platform=100))

    all_meta = c.get(f"/api/sentiment_posts?days=0").get_json()
    share_in_sample = all_meta["_meta"]["platform_sampled"]["weibo"]

    one = c.get("/api/sentiment_posts?days=0&platform=weibo").get_json()

    assert share_in_sample <= 34, f"全局抽样里微博应只占保底名额,实际 {share_in_sample}"
    assert len(one["posts"]) == 100, "筛选微博应拿到它的全部 100 条(不足 200 上限)"
    assert len(one["posts"]) > share_in_sample, (
        "筛选后的条数没有超过它在全局抽样里的份额 —— 说明筛选退化成了前端过滤,"
        "弱源/富源都会被抽样名额卡死"
    )


def test_filter_returns_only_that_platform(client):
    c = client(_corpus(per_platform=40))

    d = c.get("/api/sentiment_posts?days=0&platform=zhihu").get_json()

    assert d["posts"], "知乎不该为空"
    assert {p["platform"] for p in d["posts"]} == {"zhihu"}, "筛选后混进了别的平台"


def test_filter_caps_at_api_limit_and_reports_scope_total(client):
    """平台条数超过 200 时:返回量卡在上限,但 scope_total 报真实总数。"""
    c = client(_corpus(per_platform=250))  # 250 > POSTS_API_LIMIT,才会触发截断

    d = c.get("/api/sentiment_posts?days=0&platform=xueqiu").get_json()

    assert len(d["posts"]) == POSTS_API_LIMIT, "单次返回量必须遵守前后端契约上限"
    assert d["_meta"]["scope_total"] == 250, "scope_total 应报该平台窗口内真实条数(供'250 条中展示 200 条'文案)"
    assert d["_meta"]["platform_counts"]["xueqiu"] == 250
    assert d["_meta"]["platform_sampled"]["xueqiu"] == POSTS_API_LIMIT


# ══════════════════════════════════════════════════════════════════
# 元信息口径:计数与时间范围是"全平台"的,不随筛选变
# ══════════════════════════════════════════════════════════════════

def test_platform_counts_unaffected_by_selection(client):
    """各平台计数必须在选中某平台后**保持不变**,否则 chip 上的数字会消失/变形。"""
    c = client(_corpus(per_platform=30))

    all_counts = c.get("/api/sentiment_posts?days=0").get_json()["_meta"]["platform_counts"]
    one_counts = c.get("/api/sentiment_posts?days=0&platform=douyin").get_json()["_meta"]["platform_counts"]

    assert all_counts == one_counts
    assert set(all_counts) == set(PLATFORMS)


def test_time_range_and_total_are_global_under_selection(client):
    """时间范围与 total_posts 描述的是整批数据,不因筛选某平台而缩小。"""
    c = client(_corpus(per_platform=10))

    d = c.get("/api/sentiment_posts?days=0&platform=weibo").get_json()

    assert d["_meta"]["total_posts"] == 60, "total_posts 应为六平台合计"
    assert d["_meta"]["posts_start"] == "2026-09-16"
    assert d["_meta"]["platform"] == "weibo", "必须回显当前筛选,前端据此点亮 chip"


def test_platform_echo_is_empty_when_not_filtering(client):
    c = client(_corpus(per_platform=5))

    meta = c.get("/api/sentiment_posts?days=0").get_json()["_meta"]

    assert meta["platform"] == ""
    assert meta["scope_total"] == meta["total_posts"], "不筛选时 scope_total 应等于全量"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_platform_means_all(client, blank):
    c = client(_corpus(per_platform=20))

    d = c.get(f"/api/sentiment_posts?days=0&platform={blank}").get_json()

    assert d["_meta"]["platform"] == ""
    assert len({p["platform"] for p in d["posts"]}) > 1, "空/空白 platform 应等同'全部'"


# ══════════════════════════════════════════════════════════════════
# 边界:未知平台、窗口、日期卫生在筛选下依然成立
# ══════════════════════════════════════════════════════════════════

def test_unknown_platform_returns_empty_but_keeps_chips_renderable(client):
    """选了没有数据的平台:列表为空,但计数与回显仍在 —— 前端才能正常渲染并给出空态提示。"""
    c = client(_corpus(per_platform=10))

    d = c.get("/api/sentiment_posts?days=0&platform=bilibili").get_json()

    assert d["posts"] == []
    assert d["_meta"]["platform_counts"], "空结果也必须带计数,否则筛选器整块消失、无法切回去"
    assert d["_meta"]["platform"] == "bilibili"
    assert d["_meta"]["scope_total"] == 0


def test_window_filter_applies_within_selected_platform(client):
    c = client([
        _rec("new", "weibo", "2026-09-16 10:00:00"),
        _rec("old", "weibo", "2026-01-01 09:00:00"),
        _rec("other", "zhihu", "2026-09-16 10:00:00"),
    ])

    d = c.get("/api/sentiment_posts?since=2026-09-01&platform=weibo").get_json()

    assert {p["note_id"] for p in d["posts"]} == {"new"}
    assert d["_meta"]["scope_total"] == 1, "scope_total 也应是窗口内口径"


def test_untrustworthy_date_survives_platform_filter(client):
    """日期卫生与平台筛选正交:2104 那条仍应保留内容、时间置空。"""
    c = client([
        _rec("ok", "xhs", "2026-09-16 10:00:00"),
        _rec("bad", "xhs", "2104-09-07 07:07:56", title="UUID 卡片空壳"),
    ])

    d = c.get("/api/sentiment_posts?days=0&platform=xhs").get_json()

    times = {p["note_id"]: p["time"] for p in d["posts"]}
    assert times["bad"] == "", "筛选链路也必须走日期卫生,不能让 2104 透出"
    assert d["_meta"]["posts_end"] == "2026-09-16"


def test_dedupe_across_batch_files_within_platform(client):
    """同一 note_id 出现在多个批次文件里只算一次 —— 筛选平台时同样成立。"""
    c = client([_rec("dup-1", "weibo", "2026-09-16 10:00:00")], name="batch_weibo_20260916_100000.jsonl")
    out = web_app.THINK2_OUTPUT
    with open(out / "batch_weibo_20260917_100000.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(_rec("dup-1", "weibo", "2026-09-16 10:00:00"), ensure_ascii=False) + "\n")
        f.write(json.dumps(_rec("dup-2", "weibo", "2026-09-16 11:00:00"), ensure_ascii=False) + "\n")

    d = c.get("/api/sentiment_posts?days=0&platform=weibo").get_json()

    assert d["_meta"]["scope_total"] == 2
    assert len(d["posts"]) == 2


# ══════════════════════════════════════════════════════════════════
# 计数常驻(原实现把它藏在 `if all_times:` 里 → 全无可信日期时连计数一起消失)
# ══════════════════════════════════════════════════════════════════

def test_platform_counts_present_even_when_no_date_is_trustworthy(client):
    """一条可信日期都没有时,时间范围字段缺席(前端显示'时间未知'),
    但 platform_counts / total_posts 必须还在 —— 它们描述的是采集产出,与日期可信度无关。"""
    c = client([
        _rec("bad-1", "xhs", "2104-09-07 07:07:56"),
        _rec("bad-2", "douyin", ""),
        _rec("bad-3", "zhihu", "not-a-date"),
    ])

    meta = c.get("/api/sentiment_posts?days=0").get_json()["_meta"]

    assert "posts_start" not in meta, "没有任何可信日期时不该报出 posts_start"
    assert "posts_end" not in meta
    assert meta["platform_counts"] == {"xhs": 1, "douyin": 1, "zhihu": 1}
    assert meta["total_posts"] == 3
    # 平台筛选在这种数据上照样要能工作
    d = c.get("/api/sentiment_posts?days=0&platform=douyin").get_json()
    assert {p["note_id"] for p in d["posts"]} == {"bad-2"}
    assert d["_meta"]["platform"] == "douyin"
