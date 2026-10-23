"""当日观点总表：只发周报的机构要能"观点延续"进来（2026-10-21 用户报障护栏）。

【报障原话】「近几日的当日观点总表中也没有永安的」。
【根因】`_prev_variety_view` 在日报口径下显式排除周报/季报。永安每周日发周报、工作日
        不发日报 ⇒ 一周六天它连"观点延续"都拿不到，整行落到全 —。
【本文件守什么】
  1. ★ 当日无日报时回退到最近一期周报/季报，并打 carried=True；
  2. ★ 有日报时**仍然优先日报**，不许因为回退逻辑把周报顶上来（口径不能混）；
  3. ★ 周报/季报口径（rtype 非空）行为完全不变；
  4. 总表文案要写出"来源 + 类型"，否则用户看到"观点延续"仍不知道是谁的观点。
"""

from __future__ import annotations

import web_app


def _row(rid, code, source, pdate, rtype, direction="看空", conclusion="## 观点与依据\n偏弱。"):
    return {
        "id": rid, "variety": code, "varieties": code, "source": source,
        "publish_date": pdate, "report_type": rtype, "status": "done",
        "direction": direction, "confidence": 0.6,
        "conclusion_md": conclusion, "report_advice": "",
        "uploaded_at": pdate + " 10:00:00", "title": f"{source}{rtype or '报告'}",
    }


def test_daily_absence_falls_back_to_weekly():
    rows = [
        _row(1, "BR", "永安期货", "2026-10-18", "周报"),
        _row(2, "BR", "华泰", "2026-10-21", "日报", direction="中性"),
    ]
    prev = web_app._prev_variety_view("BR", rows, "2026-10-21")
    assert prev is not None, "只发周报的机构在日报口径下拿不到延续观点（本次报障）"
    assert prev["source"] == "永安期货"
    assert prev["report_type"] == "周报"
    assert prev["date"] == "2026-10-18"
    assert prev["carried"] is True, "回退展示必须打标记，前端/文案据此标明类型"


def test_daily_report_still_wins_over_older_weekly():
    """有日报时优先日报：不能因为加了回退把上周周报顶到当日观点上。"""
    rows = [
        _row(1, "BR", "永安期货", "2026-10-18", "周报"),
        _row(2, "BR", "华泰", "2026-10-21", "日报", direction="中性"),
    ]
    prev = web_app._prev_variety_view("BR", rows, "2026-10-22")   # 次日看：昨天有华泰日报
    assert prev["source"] == "华泰" and prev["report_type"] == "日报"
    assert prev["carried"] is False, "正常命中日报时不该标成回退"


def test_weekly_type_mode_unaffected():
    """周报口径（rtype=周报）仍只收周报，且 carried 恒 False。"""
    rows = [
        _row(1, "BR", "永安期货", "2026-10-18", "周报"),
        _row(2, "BR", "华泰", "2026-10-21", "日报", direction="中性"),
    ]
    prev = web_app._prev_variety_view("BR", rows, "2026-10-25", "周报")
    assert prev["report_type"] == "周报" and prev["source"] == "永安期货"
    assert prev["carried"] is False


def test_no_history_returns_none():
    assert web_app._prev_variety_view("BR", [], "2026-10-21") is None
    rows = [_row(1, "BR", "永安期货", "2026-10-18", "周报")]
    # 早于唯一一份研报 ⇒ 无历史，且回退也只能找到它自己之前的（没有）⇒ None
    assert web_app._prev_variety_view("BR", rows, "2026-10-18") is None


def test_daily_views_table_says_yesterday_when_latest_is_yesterday():
    """★ 用户 2026-10-21 补充：「当日没有研报时，要标明：延续昨日观点」。

    10-19 没有新研报，而永安最新一期是 10-18（昨天）⇒ 必须写"延续昨日观点"。
    """
    rows = [_row(1, "BR", "永安期货", "2026-10-18", "周报")]
    md = web_app._build_daily_views_table("2026-10-19", [], "", None, None, rows)
    # 【2026-10-21】改成"每家一行"后，延续标记落在**品种列**首行：BR（延续昨日观点）
    line = next(ln for ln in md.splitlines() if ln.startswith("| BR"))
    assert "延续昨日观点" in line, line
    assert "永安期货" in line


def test_daily_views_table_uses_explicit_date_when_not_yesterday():
    """最近一期不是昨天时不能写"昨日"（否则日期错 3 天，又会被当成 bug 报上来）。"""
    rows = [_row(1, "BR", "永安期货", "2026-10-18", "周报")]
    md = web_app._build_daily_views_table("2026-10-21", [], "", None, None, rows)
    line = next(ln for ln in md.splitlines() if ln.startswith("| BR"))
    assert "延续昨日观点" not in line, line
    assert "延续10-18" in line, line


def test_latest_source_views_picks_newest_regardless_of_type():
    """跨类型取最新：某公司先发周报、后发日报 ⇒ 取日报；只有周报的机构照样在列。"""
    rows = [
        _row(1, "BR", "永安期货", "2026-10-18", "周报"),
        _row(2, "BR", "华泰", "2026-10-17", "周报"),
        _row(3, "BR", "华泰", "2026-10-20", "日报", direction="中性"),
    ]
    got = {x["source"]: x for x in web_app._latest_source_views("BR", rows, "2026-10-21")}
    assert set(got) == {"永安期货", "华泰"}
    assert got["华泰"]["date"] == "2026-10-20" and got["华泰"]["direction"] == "中性"


def test_latest_source_views_excludes_today_senders():
    """当天已发日报的机构由 it_list 承载，不能被它自己更早的周报顶掉。"""
    rows = [_row(1, "BR", "华泰", "2026-10-21", "日报")]
    assert web_app._latest_source_views("BR", rows, "2026-10-21", {"华泰"}) == []


def test_latest_source_views_skips_pseudo_sources():
    """「上传」这类手工导入占位不是机构名，不进发行方列。"""
    rows = [_row(1, "BR", "上传", "2026-10-20", "")]
    assert web_app._latest_source_views("BR", rows, "2026-10-21") == []


def test_daily_views_table_without_history_stays_dash():
    """完全没有历史时保持全 —（不许编造）。"""
    md = web_app._build_daily_views_table("2026-10-21", [], "", None, None, [])
    line = next(ln for ln in md.splitlines() if ln.startswith("| BR |"))
    assert "观点延续" not in line


def _row_of(md: str, code: str = "BR") -> list[str]:
    line = next(ln for ln in md.splitlines() if ln.startswith(f"| {code} |"))
    return [c.strip() for c in line.strip("|").split("|")]


def test_opinion_segments_align_with_sources():
    """★ 用户 2026-10-21：「核心观点部分应该与前面的发行方一一对齐」。

    改成**每家机构单独一行**：同一行里的发行方 / 方向 / 置信度 / 核心观点必须属于同一家；
    品种/名称/研报数只在第一行出现，其余行留空。
    """
    rows = [
        _row(1, "BR", "华泰", "2026-10-21", "日报", direction="中性"),
        # 日报口径只收非周报行 ⇒ 三家都得是"日报"，否则 items 里只剩华泰
        _row(2, "BR", "永安期货", "2026-10-21", "日报"),
        _row(3, "BR", "国泰君安", "2026-10-21", "日报", direction="中性"),
    ]
    items = web_app._collect_daily_report_items(rows, "2026-10-21", "")
    md = web_app._build_daily_views_table("2026-10-21", items, "", None, None, rows)
    lines = md.splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith("| BR |"))
    block = [lines[i]]
    for l in lines[i + 1:]:
        if l.startswith("| ") and l.split("|")[1].strip():
            break          # 下一个品种行
        if l.startswith("|"):
            block.append(l)
    assert len(block) == 3, "三天内三家机构应各占一行: %r" % block
    for ln in block:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        assert cells[2], "发行方为空: %s" % ln
        assert cells[5], "核心观点为空: %s" % ln
    # 第一行才有品种/名称；日期列每行都有（各家的最新研报日）
    assert block[0].split("|")[1].strip() == "BR"
    assert block[1].split("|")[1].strip() == ""
    assert block[1].split("|")[8].strip() == "2026-10-21", "最新研报日期应逐行填写: %s" % block[1]


def test_all_covering_sources_listed_with_carry():
    """★ 用户 2026-10-23 口径：「列出所有期货公司的观点，当日没发的延续该公司上一次观点」。

    10-21 华泰发了日报、永安 10-20 发过周报、国君 10-15 发过日报 ⇒ BR 块应有 3 行：
    华泰在前（当日，无标注），永安/国君在后（带「延续 MM-DD」）。
    （旧口径「只显示当日发研报的发行商」已被用户否掉，本用例替换原断言。）
    """
    rows = [
        _row(1, "BR", "华泰", "2026-10-21", "日报", direction="中性"),
        _row(2, "BR", "永安期货", "2026-10-20", "周报"),
        _row(3, "BR", "国泰君安", "2026-10-15", "日报", direction="看多"),
    ]
    items = web_app._collect_daily_report_items(rows, "2026-10-21", "")
    md = web_app._build_daily_views_table("2026-10-21", items, "", None, None, rows)
    block = _block_of(md)
    assert len(block) == 3, "覆盖过 BR 的三家都应列出: %r" % block
    srcs = [b.split("|")[3].strip() for b in block]
    assert srcs[0] == "华泰", "当日发研报的排最前: %r" % srcs
    # 发行方列只放机构名（前端定宽 100px + nowrap，带括号会撑破）
    assert srcs[1:] == ["永安期货", "国泰君安"], srcs
    # 【2026-10-23】观点列不再写延续前缀；时效改由「最新研报日期」列表达（每家一行各自有日期）
    ops = [b.split("|")[6].strip() for b in block]
    assert all("延续" not in o for o in ops), "观点列不该再带延续标: %r" % ops
    dates = [b.split("|")[8].strip() for b in block]
    assert dates == ["2026-10-21", "2026-10-20", "2026-10-15"], dates


def test_carried_rows_keep_their_own_trade():
    """延续行必须带该家自己的头寸，否则与当日行并列时看起来像「这家没给建议」。"""
    rows = [
        _row(1, "BR", "华泰", "2026-10-21", "日报", direction="中性"),
        _row(2, "BR", "永安期货", "2026-10-20", "周报",
             conclusion="## 观点与依据\n偏弱。\n\n## 交易要素与风险\n方向: 反弹做空; 头寸: 轻仓试空"),
    ]
    items = web_app._collect_daily_report_items(rows, "2026-10-21", "")
    md = web_app._build_daily_views_table("2026-10-21", items, "", None, None, rows)
    block = _block_of(md)
    yongan = next(b for b in block if "永安" in b)
    assert yongan.split("|")[7].strip() == "轻仓试空", "延续行头寸没取到该家自己的: %s" % yongan


def _block_of(md: str, code: str = "BR") -> list[str]:
    """取出该品种的整块（每家机构一行）；遇下一个品种行即止。"""
    lines = md.splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith(f"| {code} |") or l.startswith(f"| {code}（"))
    block = [lines[i]]
    for l in lines[i + 1:]:
        if l.startswith("| ") and l.split("|")[1].strip():
            break
        if l.startswith("|"):
            block.append(l)
    return block


def test_same_day_weekly_is_listed_not_carried():
    """★ 2026-10-21 报障：「但是我在1018的总结也没有看到」（永安 LC 锂产业链周报 10-18）。

    「当日有研报」必须是当日**任意类型**的研报：
    10-18 是周日，全库当天只有周报 ⇒ 旧实现用日报口径把四份 LC 周报全滤掉，
    items 空 → 走「延续」拿 10-16 的混沌天成，永安**在自己发布那一天**反而看不到。
    """
    rows = [
        _row(1, "BR", "永安期货", "2026-10-18", "周报"),
        _row(2, "BR", "华泰", "2026-10-18", "周报", direction="中性"),
    ]
    items = web_app._collect_daily_report_items(rows, "2026-10-18", "")   # 日报口径：空
    assert items == [], "日报口径本身不该收周报（LLM 正文的口径不能动）"
    md = web_app._build_daily_views_table("2026-10-18", items, "", None, None, rows)
    block = _block_of(md)
    srcs = [b.split("|")[3].strip() for b in block]
    assert "永安期货" in srcs, "当日发的周报没进总表（本次报障核心）: %r" % block
    assert "华泰" in srcs
    assert "延续" not in block[0].split("|")[1], "当日确实有研报，不该标延续: %s" % block[0]


def test_same_day_mixed_types_list_all_senders():
    """同一天既有日报又有周报：两家都算「当日有研报」，都要列。"""
    rows = [
        _row(1, "BR", "华泰", "2026-10-19", "日报", direction="中性"),
        _row(2, "BR", "永安期货", "2026-10-19", "周报"),
    ]
    items = web_app._collect_daily_report_items(rows, "2026-10-19", "")
    md = web_app._build_daily_views_table("2026-10-19", items, "", None, None, rows)
    srcs = [b.split("|")[3].strip() for b in _block_of(md)]
    assert srcs == ["华泰", "永安期货"], "当天发过研报的机构都要列: %r" % srcs


def test_no_report_of_any_type_still_carries():
    """当日任何类型都没有 → 仍然走延续（新口径不能把这条兜底弄丢）。"""
    rows = [_row(1, "BR", "永安期货", "2026-10-18", "周报")]
    items = web_app._collect_daily_report_items(rows, "2026-10-21", "")
    md = web_app._build_daily_views_table("2026-10-21", items, "", None, None, rows)
    line = _block_of(md)[0]
    assert "延续" in line.split("|")[1], line
    assert "永安期货" in "".join(_block_of(md))


def test_weekly_type_mode_still_only_weekly():
    """周报口径不受影响：即便当天有日报，也只用调用方传入的周报 items。"""
    rows = [
        _row(1, "BR", "华泰", "2026-10-18", "日报", direction="中性"),
        _row(2, "BR", "永安期货", "2026-10-18", "周报"),
    ]
    w_items = web_app._collect_daily_report_items(rows, "2026-10-18", "周报")
    md = web_app._build_daily_views_table("2026-10-18", w_items, "周报", None, None, rows)
    srcs = [b.split("|")[3].strip() for b in _block_of(md)]
    assert srcs == ["永安期货"], "周报口径不许把日报混进来: %r" % srcs


# ── 2026-10-23 新增：两周时效闸门 ──────────────────────────────────────────
# 用户口径：「如果某家期货公司两周内没有新观点，就不显示该公司的观点」。
# 边界取**自然日 14 天**：正好 14 天仍显示，15 天起隐藏（含当日无研报的兜底分支）。

def test_stale_source_hidden_after_two_weeks():
    """22 天前有过观点的机构，不再出现在总表（当日有其它机构发研报的场景）。"""
    rows = [
        _row(1, "BR", "华泰", "2026-10-23", "日报", direction="中性"),
        _row(2, "BR", "永安期货", "2026-10-01", "周报"),   # 22 天前
    ]
    items = web_app._collect_daily_report_items(rows, "2026-10-23", "")
    md = web_app._build_daily_views_table("2026-10-23", items, "", None, None, rows)
    srcs = [b.split("|")[3].strip() for b in _block_of(md)]
    assert srcs == ["华泰"], "超两周的机构不该出现: %r" % srcs


def test_two_week_boundary_inclusive():
    """正好 14 天仍算「两周内」；15 天起隐藏。"""
    rows = [
        _row(1, "BR", "华泰", "2026-10-23", "日报", direction="中性"),
        _row(2, "BR", "永安期货", "2026-10-09", "周报"),   # 整 14 天
        _row(3, "BR", "国泰君安", "2026-10-08", "日报"),   # 15 天
    ]
    items = web_app._collect_daily_report_items(rows, "2026-10-23", "")
    md = web_app._build_daily_views_table("2026-10-23", items, "", None, None, rows)
    srcs = [b.split("|")[3].strip() for b in _block_of(md)]
    assert "永安期货" in srcs, "正好 14 天应保留: %r" % srcs
    assert "国泰君安" not in srcs, "15 天应隐藏: %r" % srcs


def test_prev_variety_view_rejects_stale():
    """当日完全无研报时的兜底分支同样受两周限制，不许把一个月前的观点顶上来。"""
    rows = [_row(1, "BR", "永安期货", "2026-10-01", "周报")]
    assert web_app._prev_variety_view("BR", rows, "2026-10-23") is None


def test_latest_source_views_age_window():
    """按家取最新观点时，超窗口的机构直接不返回。"""
    rows = [
        _row(1, "BR", "永安期货", "2026-10-08", "周报"),   # 15 天
        _row(2, "BR", "华泰", "2026-10-15", "日报"),       # 8 天
    ]
    got = [x["source"] for x in web_app._latest_source_views("BR", rows, "2026-10-23")]
    assert got == ["华泰"], got
