# -*- coding: utf-8 -*-
r"""/api/overlay 的 x 轴口径护栏（2026-10-13）。

## 背景
用户报「情绪得分 vs 价格走势」图里**价格曲线有截断**。根因不是取数缺陷，而是 x 轴口径：
原实现取「价格日期 ∪ 情绪日期」**并集**，情绪按自然日聚合（周末/假期也有人发帖），
于是非交易日也占 x 轴位置、其 close 为 None，前端价格序列 `connectNulls: false`
⇒ 价格线在这些点被切断。实测八品种，缺价日期 100% 是周末或法定假期，
**没有任何交易日缺价**（价格数据本身完整）。

## 现口径（用户三选一拍板"x 轴只留交易日"）
x 轴 = 窗口内交易日；落在非交易日的情绪**顺延归并到其后第一个交易日**，
同日多条按**帖子数加权**合并（多空比按计数求和后再相除，不对比率求平均）。

## 本文件守什么
  1. `_merge_sentiment_rows` 的加权口径与退化路径（纯函数，确定性）；
  2. 接口产出的每个点都必须有 close —— 这条直接对应"价格线不许断"这一用户诉求；
  3. x 轴不得出现周末/已知法定假期；
  4. 情绪守恒：归并只挪位置，**帖子总数与情绪日数不得增减**；
  5. 多空比不再是恒 0（daily_series 没有 bullish_ratio 字段，原写法 d.get(...,0) 恒 0）。

写断言的经验（与项目同纪律）：
  - **用真实接口取数**，不用桩：本项目踩过"桩里手写后端根本不返回的字段 ⇒ 全绿但线上错"
    （见 skill `agentsense-add-tab` 的"两种假阳性"）。这里恰好要验的就是真实数据分布。
  - 真实数据不存在时应 **skip 而不是 fail**（CI 无情绪数据时不该红）。
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 法定假期（2026 中秋 + 国庆），x 轴上绝不该出现
HOLIDAYS = {"2026-09-25", "2026-10-01", "2026-10-02",
            "2026-10-05", "2026-10-06", "2026-10-07"}
SENT_DIR = Path.home() / ".tradingagents" / "external_data"


def _weekday(ds: str) -> int:
    return date.fromisoformat(ds).weekday()          # 5=周六 6=周日


def _has_sentiment(sym: str) -> bool:
    p = SENT_DIR / f"{sym}_sentiment.json"
    if not p.exists():
        return False
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(j.get("data", {}).get("daily_series"))


@pytest.fixture(scope="module")
def client():
    os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"      # 仅本测试进程
    import web_app
    return web_app.app.test_client()


@pytest.fixture(scope="module")
def webapp():
    import web_app
    return web_app


# ─────────────────────────────────────────────────────────────
# 1. 纯函数：归并口径
# ─────────────────────────────────────────────────────────────
class TestMergeSentimentRows:
    def test_weighted_by_note_count(self, webapp):
        """加权：帖子多的那天影响更大（不能对均值再等权平均）。"""
        rows = [
            {"avg_score": 1.0, "simple_avg": 1.0, "total_notes": 9, "bull_count": 9, "bear_count": 0},
            {"avg_score": -1.0, "simple_avg": -1.0, "total_notes": 1, "bull_count": 0, "bear_count": 1},
        ]
        out = webapp._merge_sentiment_rows(rows)
        assert abs(out["avg_score"] - 0.8) < 1e-9, "应为 (1.0*9 + -1.0*1)/10 = 0.8，而非等权的 0.0"
        assert out["total_notes"] == 10
        assert abs(out["bullish_ratio"] - 0.9) < 1e-9

    def test_ratio_uses_counts_not_average_of_ratios(self, webapp):
        """多空比：先计数求和再相除；不做"比率的平均"。"""
        rows = [
            {"avg_score": 0, "simple_avg": 0, "total_notes": 100, "bull_count": 60, "bear_count": 40},
            {"avg_score": 0, "simple_avg": 0, "total_notes": 1, "bull_count": 0, "bear_count": 1},
        ]
        out = webapp._merge_sentiment_rows(rows)
        assert abs(out["bullish_ratio"] - 60 / 101) < 1e-9, "比率的平均会得到 0.30，是错的"
        assert abs(out["bearish_ratio"] - 41 / 101) < 1e-9

    def test_degenerate_zero_notes_falls_back_to_equal_weight(self, webapp):
        """缺 note_count 时退回等权，且不除零。"""
        rows = [
            {"avg_score": 0.5, "simple_avg": 0.5, "total_notes": 0, "bull_count": 0, "bear_count": 0},
            {"avg_score": -0.5, "simple_avg": -0.5, "total_notes": 0, "bull_count": 0, "bear_count": 0},
        ]
        out = webapp._merge_sentiment_rows(rows)
        assert abs(out["avg_score"] - 0.0) < 1e-9        # 等权平均
        assert out["total_notes"] == 0
        assert out["bullish_ratio"] == 0 and out["bearish_ratio"] == 0

    def test_single_row_passthrough(self, webapp):
        """只有一条时不改变原值（顺延未发生的情形）。"""
        out = webapp._merge_sentiment_rows(
            [{"avg_score": 0.375, "simple_avg": 0.25, "total_notes": 4,
              "bull_count": 3, "bear_count": 1}])
        assert abs(out["avg_score"] - 0.375) < 1e-9
        assert out["total_notes"] == 4
        assert abs(out["bullish_ratio"] - 0.75) < 1e-9


# ─────────────────────────────────────────────────────────────
# 2~5. 接口级：用真实路由 + 真实数据分布
# ─────────────────────────────────────────────────────────────
def _pick_variety(client):
    """挑一个既有行情又有情绪的品种；都没有就 skip 整个接口测试。"""
    for sym in ("RB", "TA", "M", "CF", "SC", "CU", "I", "J", "FU", "PG"):
        if not _has_sentiment(sym):
            continue
        r = client.get(f"/api/overlay/{sym}?days=180")
        if r.status_code == 200 and (r.get_json() or {}).get("overlay"):
            return sym, r.get_json()
    pytest.skip("无可用品种（缺情绪数据或接口不可用）")


@pytest.fixture(scope="module")
def overlay_payload(client):
    return _pick_variety(client)


class TestOverlayAxisIsTradingDays:
    def test_every_point_has_close(self, overlay_payload):
        """★ 本缺陷的核心判据：价格线不许有断点（每个 x 轴点都必须有收盘价）。"""
        sym, d = overlay_payload
        miss = [p["date"] for p in d["overlay"] if p.get("close") is None]
        assert not miss, f"{sym} 仍有 {len(miss)} 个点缺 close ⇒ 价格线会被切断: {miss[:8]}"

    def test_axis_has_no_weekend(self, overlay_payload):
        sym, d = overlay_payload
        bad = [p["date"] for p in d["overlay"] if _weekday(p["date"]) >= 5]
        assert not bad, f"{sym} x 轴出现周末: {bad[:8]}"

    def test_axis_has_no_holiday(self, overlay_payload):
        sym, d = overlay_payload
        bad = [p["date"] for p in d["overlay"] if p["date"] in HOLIDAYS]
        assert not bad, f"{sym} x 轴出现法定假期: {bad}"

    def test_meta_declares_mode(self, overlay_payload):
        sym, d = overlay_payload
        m = d["_meta"]
        assert m.get("xaxis_mode") == "trading_days", "meta 未声明交易日口径"
        assert m.get("trading_days") == len(d["overlay"]), "trading_days 与实际点数不符"

    def test_axis_equals_price_source_dates(self, webapp, client):
        """★ 最重要的一条不变量：x 轴日期集合必须**恰好等于**价格源的日期集合（窗口内）。

        这同时排除了两类缺陷：
          · 漏 —— x 轴比价格源少日期（真的丢数据）；
          · 多 —— x 轴塞进价格源没有的日期（旧并集口径的毛病，会产生无 close 的点）。
        用真实价格源比对（不硬编码日期表，否则窗口一滑动就过期）。
        """
        from datetime import datetime, timedelta

        from tradingagents.dataflows.commodity_futures import get_futures_price

        sym = "RB"
        days = 180
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        fetch_start = (datetime.now() - timedelta(
            days=days + webapp.ADAPTIVE_TREND_WINDOW + 5)).strftime("%Y-%m-%d")

        res = get_futures_price(sym, fetch_start, end_date)
        pts, _rolls, _method = webapp._adjusted_price_points(res, sym)
        src_dates = {p["date"] for p in pts if p["date"] >= start_date}
        if not src_dates:
            pytest.skip("价格源未返回窗口内数据（离线/数据源不可用）")

        r = client.get(f"/api/overlay/{sym}?days={days}")
        assert r.status_code == 200
        axis_dates = {p["date"] for p in r.get_json()["overlay"]}

        missing = sorted(src_dates - axis_dates)
        extra = sorted(axis_dates - src_dates)
        assert not missing, f"x 轴漏掉了价格源里的交易日: {missing[:8]}"
        assert not extra, f"x 轴多出价格源没有的日期（旧并集口径会这样）: {extra[:8]}"

    def test_sentiment_conserved(self, overlay_payload):
        """归并只挪位置：帖子总数与情绪日数都不得增减。"""
        sym, d = overlay_payload
        m = d["_meta"]
        raw = json.loads((SENT_DIR / f"{sym}_sentiment.json").read_text(encoding="utf-8"))
        rows = raw["data"]["daily_series"]

        total = (m["sentiment_days_on_trading_day"] + m["sentiment_days_carried"]
                 + m["sentiment_days_orphan"])
        assert total == len(rows), f"情绪日数不守恒: {total} vs 原始 {len(rows)}"

        ov_notes = sum(p.get("total_notes") or 0 for p in d["overlay"])
        raw_notes = sum(r.get("note_count") or 0 for r in rows)
        assert ov_notes == raw_notes, f"帖子总数不守恒: {ov_notes} vs {raw_notes}"

    def test_carried_days_actually_happen(self, overlay_payload):
        """确实发生了顺延（否则本护栏对"周末归并"这条等于没测）。"""
        sym, d = overlay_payload
        assert d["_meta"]["sentiment_days_carried"] > 0, (
            "当期数据里没有非交易日情绪，顺延路径未被覆盖 —— 请换一个跨周末的品种/窗口")
        assert d["_meta"]["sentiment_days_on_trading_day"] > 0

    def test_bullish_ratio_not_always_zero(self, overlay_payload):
        """daily_series 无 bullish_ratio 字段 ⇒ 原写法恒 0；现由计数现算。"""
        sym, d = overlay_payload
        sent = [p for p in d["overlay"] if p.get("avg_score") is not None]
        assert sent, f"{sym} 无情绪点"
        assert any((p.get("bullish_ratio") or 0) > 0 for p in sent), \
            "多空比全为 0 ⇒ 又回到 d.get('bullish_ratio', 0) 的恒 0 缺陷"


class TestOldUnionAxisWouldBreakIt:
    """把口径改回"并集"必须让上面那组断言变红 —— 用真实数据证明护栏非空转。"""

    def test_union_axis_reintroduces_missing_close(self, overlay_payload):
        """模拟旧口径：把非交易日情绪日也塞进 x 轴 ⇒ 必然出现无 close 的点。"""
        sym, d = overlay_payload
        axis = [p["date"] for p in d["overlay"]]
        raw = json.loads((SENT_DIR / f"{sym}_sentiment.json").read_text(encoding="utf-8"))
        sent_dates = [r["date"] for r in raw["data"]["daily_series"]]
        union = sorted(set(axis) | set(sent_dates))
        # 被顺延走的那些情绪日，在并集口径下就会成为"无 close 的点"
        carried = [x for x in union if x not in set(axis)]
        assert carried, "本用例需要至少一个非交易日情绪日才能证明旧口径的问题"
        assert any(_weekday(x) >= 5 or x in HOLIDAYS for x in carried), \
            "顺延来源应当含周末/假期"
