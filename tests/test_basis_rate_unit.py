"""基差率的量纲与分母护栏（2026-10-23）。

【为什么需要】同一个「基差率」在本项目里出现了**三种形态**，其中两处分母不同：

| 位置 | 值（TA 实测） | 量纲 | 分母 |
|---|---|---|---|
| `quant_module._fetch_basis().basis_rate` | 0.0914 | **小数** | **期货价**（国君 `dom_basis_rate` 原值） |
| `quant_module._fetch_basis().basis_rate_pct` | 9.14 | 百分数 | 期货价 |
| `analysis_bundle` 卡片 `gtja.basis.rate_spot` | 8.37 | 百分数 | **现货价**（自算 `basis/spot*100`） |

⇒ 谁把 0.0914 当百分数，差 100 倍；谁把 9.14 当现货分母，差 0.77 个百分点。
两者都是"看起来差不多"的静默错误 —— 没有异常，只是数错了。

【本文件守什么】
  1. `basis_rate` 必须是小数（绝对值 < 1），出现 >1 说明有百分数混进了这个字段；
  2. `basis_rate_pct` 必须严格等于 `basis_rate × 100`；
  3. `basis_rate_pct` 的分母是**期货价**，不是现货价 —— 断言它接近 `basis/futures*100`，
     并且**明显不等于** `basis/spot*100`（后者是 analysis_bundle 的口径）。
"""
from __future__ import annotations

import pytest

import quant_module as q


# ---------------------------------------------------------------------------
# 纯函数层（不联网）：直接验 `_basis_row` 的量纲与分母
# ---------------------------------------------------------------------------
def _rec(**over):
    base = {
        "date": "2026-10-22",
        "spot_price": 7048.0,
        "dominant_contract_price": 6458.0,
        "dom_basis": 590.0,
        "dom_basis_rate": 0.0914,   # 国君原值：小数、期货分母
        "dominant_contract": "TA2611",
    }
    base.update(over)
    return base


def test_basis_row_keeps_decimal_and_adds_pct():
    row = q._basis_row(_rec())
    assert row["basis_rate"] == pytest.approx(0.0914)
    assert row["basis_rate_pct"] == pytest.approx(9.14), "百分数字段没有 ×100"


def test_basis_row_pct_denominator_is_futures():
    row = q._basis_row(_rec())
    fut = row["basis"] / row["futures_price"] * 100
    spot = row["basis"] / row["spot_price"] * 100
    assert row["basis_rate_pct"] == pytest.approx(fut, abs=0.05), "接口口径应是期货分母"
    assert abs(row["basis_rate_pct"] - spot) > 0.5, "与现货分母算出了同一个数，口径没分开"


def test_basis_row_handles_missing_rate():
    row = q._basis_row(_rec(dom_basis_rate=None))
    assert row["basis_rate"] is None and row["basis_rate_pct"] is None


# ---------------------------------------------------------------------------
# 真实数据层（联网，取不到就跳过）
# ---------------------------------------------------------------------------
def _rows(sym: str):
    b = q._fetch_basis(sym, 60)
    if not b.get("ok") or not b.get("rows"):
        pytest.skip(f"{sym} 无基差数据（数据源未覆盖或取数失败）")
    return b["rows"]


def test_basis_rate_is_decimal_not_percent():
    for sym in ("TA", "RB"):
        r = _rows(sym)[-1]
        if r.get("basis_rate") is None:
            continue
        assert abs(r["basis_rate"]) < 1, (
            f"{sym} 的 basis_rate={r['basis_rate']} 超过 1 —— 百分数混进了小数位"
        )


def test_basis_rate_pct_is_exactly_hundred_times():
    for sym in ("TA", "RB"):
        r = _rows(sym)[-1]
        if r.get("basis_rate") is None or r.get("basis_rate_pct") is None:
            continue
        assert r["basis_rate_pct"] == pytest.approx(r["basis_rate"] * 100, abs=0.01)


def test_basis_rate_pct_uses_futures_denominator():
    """接口口径分母是**期货价**；现货分母是另一套（analysis_bundle 在用）。"""
    for sym in ("TA", "RB"):
        r = _rows(sym)[-1]
        if not (r.get("basis") is not None and r.get("spot_price") and r.get("futures_price")):
            continue
        if r.get("basis_rate_pct") is None:
            continue
        fut_pct = r["basis"] / r["futures_price"] * 100
        spot_pct = r["basis"] / r["spot_price"] * 100
        assert r["basis_rate_pct"] == pytest.approx(fut_pct, abs=0.05), (
            f"{sym} 的 basis_rate_pct={r['basis_rate_pct']} 不等于基差/期货={fut_pct:.2f}"
        )
        # 两个分母确实不同（同一绝对值，分母不同 ⇒ 数值必然有差）
        assert abs(fut_pct - spot_pct) > 1e-6, (
            f"{sym} 期货分母与现货分母算出了同一个数，口径没真正分开"
        )
