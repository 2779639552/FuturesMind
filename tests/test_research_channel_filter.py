# -*- coding: utf-8 -*-
"""研报库「公众号」通道标识与筛选（2026-10-19）。

背景：公众号入库的 source 会被 `_normalize_source()` 归一成机构名
（「公众号-混沌天成研究」→「混沌天成研究」），界面上**认不出**来源通道。
⇒ 用 filename 前缀 `wxmp_` 作为通道判据，并提供 channel 过滤。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.unit
def test_db_supports_filename_prefix_before_limit():
    """★ 前缀过滤必须进 SQL（先过滤再 LIMIT），不能在 Python 里事后筛。

    【为什么】列表默认 `limit=50`。若先取 50 条再过滤，公众号命中率低时会被整片挤掉，
    表现为「明明入库了却搜不到」（本项目 2026-09-29 在 report_type 上踩过同型）。
    """
    s = _read("database.py")
    assert "filename_prefix: str | None = None" in s, "缺少 filename_prefix 参数"
    assert 'where.append("filename LIKE ?")' in s, "没有生成 SQL 过滤条件"
    assert 'params.append(filename_prefix + "%")' in s, "前缀没有拼进参数（有注入风险/漏匹配）"
    # 位置关系：前缀条件必须写在 LIMIT 之前
    i_prefix = s.index('params.append(filename_prefix + "%")')
    i_limit = s.index('sql += " LIMIT ?"')
    assert i_prefix < i_limit, "★ 前缀过滤写在 LIMIT 之后 ⇒ 先截断再过滤，会漏"


@pytest.mark.unit
def test_api_maps_channel_wechat_to_prefix():
    """`channel=wechat` ⇒ filename_prefix=`wxmp_`；且**不复用** ingest_source。"""
    s = _read("web_app.py")
    i = s.index("def api_research(")
    body = s[i:s.index("\ndef ", i + 10)]
    assert 'request.args.get("channel")' in body, "接口没接 channel 参数"
    assert 'prefix = "wxmp_" if channel == "wechat" else None' in body, "channel 没映射到 wxmp_ 前缀"
    assert "filename_prefix=prefix" in body, "前缀没传给 DB 查询"


@pytest.mark.unit
def test_filename_prefix_filters_all_rows():
    """行为判据：带前缀查询返回的**每一条** filename 都必须以它开头。"""
    from database import get_db  # noqa: PLC0415

    db = get_db()
    rows = db.list_research_reports(limit=None, filename_prefix="wxmp_")
    if not rows:                       # 空库/尚未入库时不算失败（避免环境相关假红）
        pytest.skip("库里还没有公众号研报")
    bad = [r.get("filename") for r in rows if not str(r.get("filename") or "").startswith("wxmp_")]
    assert not bad, "前缀过滤漏了非公众号行：%s" % bad[:3]

    # 与直接 SQL 计数一致（防止 SQL 拼错却"看起来有结果"）
    con = sqlite3.connect(str(Path.home() / ".tradingagents" / "agentsense.db"))
    n = con.execute("select count(*) from research_reports where filename like 'wxmp_%'").fetchone()[0]
    assert len(rows) == n, "查询结果 %d 与 SQL 计数 %d 不一致" % (len(rows), n)


@pytest.mark.unit
def test_ui_has_wechat_option_and_passes_channel_not_ingest_source():
    """下拉要有「公众号」，且选中时发 channel，而不是塞进 ingest_source。"""
    h = _read("web_template.html")
    assert '<option value="__wechat">公众号（微信读书）</option>' in h, "缺少公众号筛选项"
    i = h.index("if (src === '__wechat')")
    seg = h[i:i + 260]
    assert "q.set('channel', 'wechat')" in seg, "没有发 channel 参数"
    assert "q.set('ingest_source', src)" in seg, "原有 ingest_source 通道应保留在 else 分支"


@pytest.mark.unit
def test_badge_uses_filename_not_normalized_source():
    """徽标必须按 filename 前缀判断。

    【为什么不能用 source】接口对它做了归一化（`公众号-混沌天成研究` → `混沌天成研究`），
    source 里根本不含「公众号」⇒ 按 source 判断会**永远不显示**。
    """
    h = _read("web_template.html")
    i = h.index("const chanBadge")
    seg = h[i:i + 420]
    assert "startsWith('wxmp_')" in seg, "徽标判据不是 filename 前缀"
    assert "rp.source" not in seg, "★ 用 source 判断通道 ⇒ 归一化后永远不命中"
    assert "公众号" in seg, "徽标文案缺失"
    assert "${chanBadge}" in h, "徽标定义了但没渲染进列表"
