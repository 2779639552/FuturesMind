"""发布日期卫生回归测试(2026-09-17)。

【用户看到的现象】前端「社交舆情」页的"数据覆盖 X ~ Y"显示到 **2104 年**,
帖子列表里也出现 `2104-09-07` 这类发布时间。

【根因两层】
  ① 采集端:xhs 列表卡片 id 为 UUID 形态时(`f894297b-7000-4433-...#1789461982382`),
     旧的 `decode_objectid_timestamp` 只查"前 8 位能否当 hex 解析",于是把 UUID 前缀
     当成 Unix 秒,静默算出 2102 / 2104 年(实测 952 条中 33 条跨 2013~2104)。
  ② 展示端:web_app 是**直接读原始 batch_*.jsonl** 渲染页面的 —— 它绕过了
     `trend_aggregator` → `data_cleaner.clean_records` 那条清洗链路。因此出现
     "**聚合产物(趋势/情绪 JSON)是干净的,页面却是脏的**"这种割裂。
     采集端解码器已修,但存量 jsonl 的脏值不会自己消失 ⇒ 展示层必须自己兜住。

【本文件覆盖】展示层的**字段级**卫生(与采集侧 `clean_records` 的**记录级**丢弃互补):
  - `_sane_publish_date` 的判定规则(格式 + 区间);
  - `/api/sentiment_posts` 是否真的把不可信日期挡在 `posts_start/posts_end`
    与每条帖子的 `time` 之外,同时**不丢弃帖子内容**。

【已知边界 · 别误以为这道闸万能】它只拦**区间外**(2104 这类)。区间**内**的假日期
(如 UUID 前缀恰好编码出 2020/2022/2024 —— 实测 4 条)看起来就是合法过去日期,
区间闸无从分辨,**只能靠采集端解码器的格式白名单拦**。两层缺一不可。
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

import web_app
from web_app import _sane_publish_date


# ══════════════════════════════════════════════════════════════════
# 纯函数:_sane_publish_date 的判定规则
# ══════════════════════════════════════════════════════════════════

NOW = datetime(2026, 9, 17, 12, 0, 0)  # 【变量】测试参照时刻(避免用例随真实日期漂移)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-09-16 10:00:00", "2026-09-16"),  # 正常过去日期
        ("2026-09-17", "2026-09-17"),  # 只有日期
        ("2015-01-01 00:00:00", "2015-01-01"),  # 下界本身(含)
        ("2026-09-18 03:00:00", "2026-09-18"),  # 明天(容 1 天时区偏移,含)
    ],
)
def test_sane_date_accepts_plausible_values(raw, expected):
    """格式合法且落在区间内 → 归一为 YYYY-MM-DD。"""
    assert _sane_publish_date(raw, now=NOW) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "2104-09-07 07:07:56",  # 实测脏值(UUID 前缀 "f894297b" 被当 Unix 秒)
        "2102-02-27 12:58:35",  # 实测脏值
        "2031-12-16 22:33:15",  # 实测脏值(离得近,更像"真的")
        "2084-09-03 13:40:44",  # 实测脏值
        "2014-12-31 23:59:59",  # 早于下界 1 天
        "2012-10-27 21:27:49",  # 早于下界
        "2026-09-19 00:00:00",  # 晚于明天(超出容差)—— "未来日期永远落在最近 N 天窗口内"
    ],
)
def test_sane_date_rejects_out_of_range(raw):
    """区间外(未来 / 远古)→ 判为不可信,返回空串。

    ⚠️ 未来日期尤其要挡:它会**永远**满足"最近 N 天"的过滤条件,
    若放行就会一直挂在页面上,比丢一条更糟。
    """
    assert _sane_publish_date(raw, now=NOW) == ""


@pytest.mark.parametrize(
    "raw",
    [
        "",  # 空
        None,  # None(字段缺失)
        "2026-09",  # 长度不足
        "2026/09/17 10:00:00",  # 分隔符不对(斜杠)
        "20260917",  # 无分隔符
        "not-a-date",  # 纯文本
        "昨天",  # 中文
        "   ",  # 空白
    ],
)
def test_sane_date_rejects_malformed(raw):
    """格式不符 → 空串(**绝不能把原始垃圾串原样透出**)。"""
    assert _sane_publish_date(raw, now=NOW) == ""


def test_sane_date_documents_known_blind_spot():
    """已知盲区:区间**内**的假日期拦不住 —— 记录了这是有意的边界,不是漏改。

    UUID 前缀 `61cfd1c0` 恰好编码出 2022-01-01:格式合法、落在 [2015, 今天] 内,
    展示层无从判断真假(它看起来就是一条 2022 年的帖子)。这一层必须由采集端
    解码器的**格式白名单**负责 —— 参见 tests/test_sentiment_timestamp_hygiene.py。
    如果哪天有人把采集端的格式闸删掉、指望展示层兜住,这条用例会提醒他这里兜不住。
    """
    assert _sane_publish_date("2022-01-01 00:00:00", now=NOW) == "2022-01-01"


# ══════════════════════════════════════════════════════════════════
# 接口端到端:/api/sentiment_posts 不得让脏日期到达前端
# ══════════════════════════════════════════════════════════════════

BATCH_NAME = "batch_xhs_20260917_090142.jsonl"  # 【变量】批次文件名(格式与采集端一致)


def _rec(note_id, platform, publish_time, title="某品种库存去化跟踪"):
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


def _write_output(tmp_path, records, name=BATCH_NAME) -> Path:
    """把记录写进 tmp_path/output/<批次文件>,返回该 output 目录。"""
    out = tmp_path / "output"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / name, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """把 web_app 的 THINK2_OUTPUT 指到临时目录,返回 test_client 工厂。"""
    def _make(records, name=BATCH_NAME):
        out = _write_output(tmp_path, records, name)
        monkeypatch.setattr(web_app, "THINK2_OUTPUT", out)
        return web_app.app.test_client()
    return _make


def test_sentiment_posts_keeps_2047_out_of_range_and_out_of_meta(client):
    """核心回归:2104 那条帖子 —— 内容保留、时间置空,且不得成为"数据覆盖"上界。"""
    c = client([
        _rec("good-1", "xueqiu", "2026-09-16 10:00:00", title="正常帖子"),
        _rec("bad-uuid", "xhs", "2104-09-07 07:07:56", title="UUID 卡片空壳"),
    ])

    data = c.get("/api/sentiment_posts").get_json()

    assert data["_meta"]["posts_end"] == "2026-09-16", "页面'数据覆盖'上界被脏值拉到了未来"
    assert data["_meta"]["posts_start"] == "2026-09-16"
    times = {p["note_id"]: p["time"] for p in data["posts"]}
    assert times["good-1"] == "2026-09-16 10:00", "正常帖子的时间(含时分)应保持原样"
    assert times["bad-uuid"] == "", "不可信日期必须置空,绝不能透出 2104"


def test_sentiment_posts_does_not_drop_content_on_bad_date(client):
    """日期不可信只影响"时间",不该顺手丢掉内容 —— 丢弃是聚合层的职责,不是展示层。"""
    c = client([
        _rec("good-1", "xueqiu", "2026-09-16 10:00:00"),
        _rec("bad-1", "xhs", "2104-09-07 07:07:56", title="有标题的脏日期帖"),
    ])

    data = c.get("/api/sentiment_posts").get_json()

    assert data["_meta"]["total_posts"] == 2, "脏日期帖应按'日期未知'保留,而不是被丢掉"
    assert {p["note_id"] for p in data["posts"]} == {"good-1", "bad-1"}
    bad = next(p for p in data["posts"] if p["note_id"] == "bad-1")
    assert bad["title"] == "有标题的脏日期帖"


def test_sentiment_posts_survives_all_dates_untrustworthy(client):
    """全部日期都不可信时:posts 照常返回,时间范围字段缺席(前端自然不显示范围)。"""
    c = client([
        _rec("bad-1", "xhs", "2104-09-07 07:07:56"),
        _rec("bad-2", "douyin", ""),
        _rec("bad-3", "zhihu", "not-a-date"),
    ])

    data = c.get("/api/sentiment_posts").get_json()

    # 【2026-09-17 调整】原断言是 `_meta == {}`。现改为精确断言"时间范围字段缺席":
    #   platform_counts / total_posts 等描述**采集产出**的字段现在常驻(平台筛选器
    #   要靠它渲染,而"今天各平台采了多少条"与"日期是否可信"是两件事),
    #   用整体相等会把这两件事绑死。本条用例真正要守的是"别报出 posts_start/posts_end"。
    assert "posts_start" not in data["_meta"], "没有任何可信日期时不该报出 posts_start/posts_end"
    assert "posts_end" not in data["_meta"]
    assert len(data["posts"]) == 3
    assert all(p["time"] == "" for p in data["posts"])


def test_sentiment_posts_cutoff_still_filters_old_but_keeps_unknown(client):
    """窗口过滤:可信的旧帖子被剔除;**日期未知**的保留(不知道日期,不能当'过期'处理)。"""
    c = client([
        _rec("fresh", "xueqiu", "2026-09-16 10:00:00"),
        _rec("old", "weibo", "2026-01-01 09:00:00"),
        _rec("unknown", "zhihu", "2104-09-07 07:07:56"),
    ])

    data = c.get("/api/sentiment_posts?since=2026-09-01").get_json()

    got = {p["note_id"] for p in data["posts"]}
    assert "fresh" in got
    assert "old" not in got, "窗口下界之外的可信帖子应被剔除"
    assert "unknown" in got, "日期未知的帖子不该被当成过期而剔除"


def test_sentiment_posts_sorts_unknown_dates_last(client):
    """时间未知的排到最后(空串排序天然靠后)——避免它们挤掉有时效性的帖子。"""
    c = client([
        _rec("unknown", "zhihu", "2104-09-07 07:07:56"),
        _rec("newest", "xueqiu", "2026-09-16 10:00:00"),
        _rec("older", "weibo", "2026-09-10 08:00:00"),
    ])

    data = c.get("/api/sentiment_posts").get_json()

    assert [p["note_id"] for p in data["posts"]] == ["newest", "older", "unknown"]


def test_meta_range_describes_window_not_all_dates_ever_seen(client):
    """「数据覆盖」的上下界必须描述**当前窗口内**的集合,不能混入已被窗口剔除的帖子。

    回归:all_times 曾在窗口过滤**之前** append,于是带 days/since 时 posts_start
    仍是全库最早的日期 —— 与同一份 meta 里窗口内的 total_posts 口径打架
    (线上实测 `days=30` 报 posts_start=2016-05-12、total_posts=4674,而返回的
    200 条里没有一条早于一个月前)。posts_end 一直看不出来,只是因为未来日期
    已被 _sane_publish_date 先行拦掉。
    """
    c = client([
        _rec("ancient", "xueqiu", "2016-05-12 09:00:00"),
        _rec("fresh", "weibo", "2026-09-16 10:00:00"),
    ])

    meta = c.get("/api/sentiment_posts?since=2026-09-01").get_json()["_meta"]

    assert meta["posts_start"] == "2026-09-16", "起点被窗口之外的帖子拉到了 2016"
    assert meta["posts_end"] == "2026-09-16"
    assert meta["total_posts"] == 1, "total_posts 是窗口内口径,posts_start 必须跟它一致"
    assert meta["platform_counts"] == {"weibo": 1}, "各平台计数也应是窗口内口径"
