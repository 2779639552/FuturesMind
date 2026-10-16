"""同源多篇研报合并(2026-09-14 用户需求)单元测试。

【用户现象】某个品种某一天,同一家研报源发出多篇相关研报,总结里就出现
「四个研报源、六个观点」,而不是"一家公司一个统一观点"。
【两个叠加成因】
  ① 分组键用**原始 source**:东证的三种写法(`东证期货` / `上海东证期货有限公司` /
     `东证期货-繁微MCP`)被当成三家 → 源数虚高;
  ② 同源多篇不去重、不合并,各占一行 → 观点数虚高。
【本文件锁死三件事】
  A. `_normalize_source` 的归并口径,以及**不误伤第三方机构**(中信建投期货等);
  B. `_merge_same_source_items` 的合并语义:方向多数票(平票取最新)、置信度均值、
     同篇双采真去重、titles 收录全部篇目、flatten 单行、metrics 按名留最新;
  C. 两条 prompt 的"禁止按篇罗列"口径 + `_detect_per_report_listing` 校验器。
"""

import pytest

import web_app


# ---------------------------------------------------------------------------
# 桩件
# ---------------------------------------------------------------------------
def _rec(rid=1, code="SC", source="华泰", rdate="2026-09-02", ts=None, direction="看多",
         conf=0.7, text=None, title=None, body=None, trade="", metrics=None, order=None):
    """一条"逐篇记录"(字段与两条 collect 链路喂给合并 helper 的一致)。

    ts 默认随 rid 递增 → rid 越大越"新"(模拟后采集≈后发布)。
    text 默认带 rid 后缀:避免测试里两条记录因为正文完全相同而被判成"同篇双采"
    (那会让合并结果只剩 1 条,掩盖真正要验的合并语义)。
    """
    return {
        "code": code,
        "source": source,
        "source_raw": source,
        "rdate": rdate,
        "ts": ts or f"2026-09-02 {rid:02d}:00:00",
        "rid": rid,
        "order": rid if order is None else order,
        "title": title or f"研报{rid}",
        "direction": direction,
        "conf_num": conf,
        "text": text if text is not None else f"要点{rid}",
        "body": body,
        "trade": trade,
        "metrics": metrics or [],
    }


# ---------------------------------------------------------------------------
# A. 发行方归一
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expect", [
    # 已在 2026-09-11 确认的三家
    ("国泰君安期货研究所", "国泰君安"),
    ("GTJA", "国泰君安"),
    ("华泰期货", "华泰"),
    ("东证期货", "东证"),
    ("上海东证期货有限公司", "东证"),
    ("东证期货-繁微MCP", "东证"),
    ("东方期货", "东证"),  # 用户确认:本系统数据里"东方期货"即东证的另一写法
    # 【2026-09-14 扩充】永安/中信的多写法
    ("永安期货", "永安期货"),
    ("永安期货研究中心", "永安期货"),
    ("永安期货研究中心能化团队", "永安期货"),
    ("中信期货", "中信期货"),
    ("中信期货研究所", "中信期货"),
    # 【2026-10-14 扩充】隆众:同一品种在多个「口径」下重复出现(用户报"不要重复性录入")。
    #   成因:采集端只有一个源,`account`(类别)由**标题前缀**派生(`[隆众聚焦]`→聚焦…),
    #   入库拼成 `隆众资讯-<类别>` ⇒ 被当成多家机构 ⇒ 观点总表同品种出现多行。
    ("隆众资讯", "隆众资讯"),
    ("隆众资讯-聚焦", "隆众资讯"),
    ("隆众资讯-聚乙烯", "隆众资讯"),
    ("隆众资讯-成品油", "隆众资讯"),
    ("隆众资讯-早读", "隆众资讯"),
    # 【2026-10-14 方案 B】公众号渠道 + 机构多写法收敛(用户选定:界面显示干净机构名)
    #   同一个号「混沌天成研究」曾裂成 3 种写法(公众号-… / …研究 / …研究院)。
    #   ⚠️`混沌天成研究院` **不含**子串 "混沌天成研究" ⇒ 键必须用 `混沌天成`。
    ("公众号-多空逻辑", "多空逻辑"),
    ("公众号-混沌天成研究", "混沌天成"),
    ("混沌天成研究", "混沌天成"),
    ("混沌天成研究院", "混沌天成"),
    ("恒力期货研究院", "恒力期货"),
    ("公众号-恒力期货研究院", "恒力期货"),
    ("紫金天风期货研究所", "紫金天风"),
    ("公众号-紫金天风期货研究所", "紫金天风"),
    ("物产中大期货研究", "物产中大"),
    ("公众号-物产中大期货研究", "物产中大"),
    ("中信建投期货研究", "中信建投"),
    ("公众号-中信建投期货研究", "中信建投"),
    # 不在规则内的机构原样返回
    ("未知机构", "未知机构"),
    ("", ""),
])
def test_normalize_source_rules(raw, expect):
    assert web_app._normalize_source(raw) == expect


def test_normalize_source_does_not_merge_third_parties():
    """带"期货"的完整键**不会把第三方机构并进"中信期货"**。

    ⚠️ 2026-10-14 修正：原断言写作 `中信建投期货 == 中信建投期货`（要求原样返回），
    但加了 `("中信建投期货", "中信建投")` 之后它被归一到 **`中信建投`** ——
    这是**正确**的（中信建投期货本来就是那个机构本身），原断言混淆了两件事：
      · 「不被误并进 **中信期货**」← 这才是要守的（且仍然成立）
      · 「原样返回」← 不是目标（机构自身的多写法本就该收敛）
    """
    # 关键不变量：不被并进"中信期货"（中信建投 ≠ 中信期货）
    assert web_app._normalize_source("中信建投期货") == "中信建投"
    assert web_app._normalize_source("中信建投期货研究") == "中信建投"
    assert web_app._normalize_source("中信建投证券") == "中信建投证券"  # 其它法人不并
    # 永安系：只并"期货"系，不并保险资管
    assert web_app._normalize_source("永安保险资管") == "永安保险资管"
    assert web_app._normalize_source("发现报告") == "发现报告"


def test_normalize_source_known_limitation_short_keys():
    """【已知局限,刻意钉住】短键「华泰」「东证」是子串匹配,会顺带命中同名集团下的
    其他机构(华泰联合证券 / 东证资管)。

    本库 done 研报里不存在这两种写法,且短键是 2026-09-11 用户确认的口径,故本轮不改;
    此处把行为钉住 —— 将来若收紧匹配规则(如要求后接「期货/研究所」),这条会立刻失败,
    提醒同步更新归一映射表与前端展示。
    """
    assert web_app._normalize_source("华泰联合证券") == "华泰"
    assert web_app._normalize_source("东证资管") == "东证"


# ---------------------------------------------------------------------------
# B. 合并语义
# ---------------------------------------------------------------------------
def test_merge_direction_majority_vote_and_mean_confidence():
    """方向=组内多数票;置信度=组内有值项均值(缺值不计入)。"""
    out = web_app._merge_same_source_items([
        _rec(rid=1, direction="看多", conf=0.8, text="要点一"),
        _rec(rid=2, direction="看多", conf=0.6, text="要点二"),
        _rec(rid=3, direction="看空", conf=0.4, text="要点三"),
    ])
    assert len(out) == 1
    m = out[0]
    assert m["direction"] == "看多"  # 2:1
    assert m["conf_num"] == pytest.approx((0.8 + 0.6 + 0.4) / 3)
    assert m["merged_count"] == 3 and m["dup_dropped"] == 0


def test_merge_missing_confidence_does_not_break_mean():
    """组内部分篇没有置信度 → 只用有值项求均值;全缺 → None(前端显示 —)。"""
    out = web_app._merge_same_source_items([
        _rec(rid=1, conf=0.6, text="A"),
        _rec(rid=2, conf=None, text="B"),
    ])
    assert out[0]["conf_num"] == pytest.approx(0.6)
    out2 = web_app._merge_same_source_items([
        _rec(rid=1, conf=None, text="A"),
        _rec(rid=2, conf=None, text="B"),
    ])
    assert out2[0]["conf_num"] is None


def test_merge_tie_takes_latest_report():
    """平票取**最新**一篇(kept 已按 ts 新→旧排序,winner 取首个)。"""
    out = web_app._merge_same_source_items([
        _rec(rid=1, direction="看空", ts="2026-09-02 08:00:00", text="早期"),
        _rec(rid=2, direction="看多", ts="2026-09-02 09:30:00", text="最新"),
    ])
    assert out[0]["direction"] == "看多"
    assert out[0]["report_id"] == 2


def test_merge_dedups_same_report_from_two_pipelines():
    """同一份报告被两条采集链路各收一遍 → 真去重(merged_count 仍为 1)。

    判据:标题指纹互相包含(链路改写标题会加机构前缀/日期),或正文高度相似。
    身份(ts/rid/title)用更新的那版,正文更完整的那版被吸收进来。
    """
    out = web_app._merge_same_source_items([
        _rec(rid=1, title="聚烯烃日报:成本支撑偏强", text="短"),
        _rec(rid=2, title="华泰期货聚烯烃日报20260902:成本支撑偏强", text="更完整的摘要"),
    ])
    assert len(out) == 1
    m = out[0]
    assert m["merged_count"] == 1 and m["dup_dropped"] == 1
    assert m["report_id"] == 2
    assert m["text"] == "更完整的摘要"  # 正文更完整的一版胜出
    assert [t["report_id"] for t in m["titles"]] == [2]


def test_merge_lists_all_titles_newest_first():
    """titles 收录该机构**全部**篇目(新→旧)——前端据此列多个原件链接。"""
    out = web_app._merge_same_source_items([
        _rec(rid=1, text="一篇"),
        _rec(rid=2, text="二篇"),
        _rec(rid=3, text="三篇"),
    ])
    assert [t["report_id"] for t in out[0]["titles"]] == [3, 2, 1]
    assert [t["title"] for t in out[0]["titles"]] == ["研报3", "研报2", "研报1"]
    assert out[0]["report_ids"] == [3, 2, 1]


def test_merge_flatten_makes_single_line_for_table_cell():
    """flatten=True:换行折成「；」,否则会撑破观点总表的 markdown 单元格。"""
    out = web_app._merge_same_source_items([
        _rec(rid=1, text="第一段\n第二段"),
        _rec(rid=2, text="第三段"),
    ], flatten=True)
    assert "\n" not in out[0]["text"]
    # 新→旧;每篇内部的换行折成「；」(所以第一段的"第一段/第二段"之间也是「；」)
    assert out[0]["text"] == "第三段；第一段；第二段"


def test_merge_text_keeps_paragraph_breaks_when_not_flatten():
    """flatten=False(单品种总结的材料):保留空行,便于 LLM 分辨多篇来源。"""
    out = web_app._merge_same_source_items([
        _rec(rid=1, text="第一篇结论"),
        _rec(rid=2, text="第二篇结论"),
    ])
    assert out[0]["text"] == "第二篇结论\n\n第一篇结论"


def test_merge_caps_text_at_boundary():
    """text_cap 给总结 prompt 的材料预算封顶;截断按句读边界并补省略号。"""
    out = web_app._merge_same_source_items([
        _rec(rid=1, text="甲" * 100 + "。"),
    ], text_cap=50)
    assert len(out[0]["text"]) <= 51 and out[0]["text"].endswith("…")


def test_merge_metrics_keeps_latest_value_per_label():
    """四指标按**指标名**去重,同一指标保留最新披露值(不重复占位)。"""
    out = web_app._merge_same_source_items([
        _rec(rid=1, metrics=[{"label": "基差", "value": "-50"}, {"label": "开工率", "value": "78%"}]),
        _rec(rid=2, metrics=[{"label": "基差", "value": "-30"}]),
    ])
    got = {m["label"]: m["value"] for m in out[0]["metrics"]}
    assert got == {"基差": "-30", "开工率": "78%"}


def test_merge_groups_separately_by_code_and_source():
    """分组键 =(品种代码, 归一后发行方):不同品种/不同机构各自成条。"""
    out = web_app._merge_same_source_items([
        _rec(rid=1, code="SC", source="华泰", text="A"),
        _rec(rid=2, code="SC", source="东证", text="B"),
        _rec(rid=3, code="TA", source="华泰", text="C"),
    ])
    assert [(r["code"], r["source"]) for r in out] == [("SC", "华泰"), ("SC", "东证"), ("TA", "华泰")]


def test_merge_preserves_original_order_by_first_appearance():
    """输出顺序按组内**最早出现**的那篇(合并不能打乱观点总表的品种/机构顺序)。"""
    out = web_app._merge_same_source_items([
        _rec(rid=1, order=0, source="华泰", text="A"),
        _rec(rid=2, order=1, source="东证", text="B"),
        _rec(rid=3, order=2, source="华泰", text="C"),  # 华泰第二篇(排在前面的组)
    ])
    assert [r["source"] for r in out] == ["华泰", "东证"]


# ---------------------------------------------------------------------------
# C. 校验器 + prompt 口径
# ---------------------------------------------------------------------------
def test_detect_per_report_listing_flags_per_report_wording():
    assert web_app._detect_per_report_listing(
        "华泰两篇研报分别认为供应偏紧与需求走弱。国泰君安认为震荡。",
        ["华泰", "国泰君安"],
    ) == ["华泰"]


def test_detect_per_report_listing_ignores_merged_wording():
    """正文里写「已合并」是**正确**表述,不该报警。"""
    assert web_app._detect_per_report_listing(
        "华泰两篇已合并为一条观点:供应偏紧。国泰君安认为震荡。",
        ["华泰", "国泰君安"],
    ) == []


def test_detect_per_report_listing_ignores_cross_firm_disagreement():
    """跨机构分歧(「两家机构观点不一,华泰看多,国泰君安看空」)是正常表述,不该报警。"""
    assert web_app._detect_per_report_listing(
        "两家机构观点不一,华泰看多,国泰君安看空。", ["华泰", "国泰君安"],
    ) == []


def test_detect_per_report_listing_skips_unknown_source():
    assert web_app._detect_per_report_listing("未知两篇分别看多。", ["未知", ""]) == []


def _capture_daily_prompt(monkeypatch, rows, llm_body=None, **call_kwargs):
    """打桩 LLM 并捕获 prompt;返回 (生成结果, prompt, 落盘内容)。"""
    captured: dict[str, str] = {}
    body = llm_body or "## 一、当日观点总表\n| 表 |\n\n## 二、观点对比与冲突\n华泰认为供应偏紧。"
    monkeypatch.setattr(web_app, "_rag_context_for_variety", lambda *a, **k: "")
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB(rows))

    class _FakeClient:
        def get_llm(self):
            class _L:
                def invoke(self, prompt):
                    captured["prompt"] = prompt

                    class _R:
                        content = body
                    return _R()
            return _L()

    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeClient())
    out = web_app._generate_daily_summary(**call_kwargs)
    return out, captured.get("prompt", "")


class _FakeDB:
    """最小 DB 桩:list_research_reports 返回预置行。"""

    def __init__(self, rows):
        self._rows = rows

    def list_research_reports(self, variety=None, limit=50, ingest_source=None):
        return [dict(r) for r in self._rows]


def _db_row(rid=1, direction="看多", conclusion="", source="华泰期货", title=None):
    return {
        "id": rid,
        "title": title or f"研报{rid}",
        "source": source,
        "variety": "SC",
        "varieties": "SC",
        "uploaded_at": f"2026-09-02 {rid:02d}:00:00",
        "publish_date": "",
        "report_type": "",
        "status": "done",
        "direction": direction,
        "confidence": 0.7,
        "conclusion_md": conclusion,
    }


_CONC = (
    "## 供需格局\n供应收紧,港口去库。\n\n"
    "## 交易要素与风险\n方向:看多;形态与区间:—;头寸:—;头寸范围:—;风险:—\n"
)
_CONC2 = (
    "## 库存与需求\n下游补库,现货升水走扩。\n\n"
    "## 交易要素与风险\n方向:看多;形态与区间:—;头寸:—;头寸范围:—;风险:—\n"
)


def test_daily_prompt_forbids_per_report_listing(tmp_path, monkeypatch):
    """每日/周度总结 prompt:删掉旧口径「方向/置信度仍逐份对应列出」,
    改为"材料已按机构合并、一家一个观点、禁止按篇罗列",且材料抬头列出全部篇目。"""
    monkeypatch.setattr(web_app, "RESEARCH_DAILY_DIR", tmp_path / "d")
    monkeypatch.setattr(web_app, "RESEARCH_WEEKLY_DIR", tmp_path / "w")
    out, prompt = _capture_daily_prompt(
        monkeypatch,
        [_db_row(rid=1, conclusion=_CONC), _db_row(rid=2, conclusion=_CONC2)],
        date="2026-09-02",
    )
    assert out["ok"]
    # 新口径在
    assert "同源已合并" in prompt
    assert "一家一个、数量完全相等" in prompt
    assert "严禁出现「某机构两篇分别…」" in prompt
    # 旧口径已删除
    assert "方向/置信度仍逐份对应列出" not in prompt
    # 材料里同一机构只有一块,并标注"2 篇已合并"
    assert "(同机构 2 篇已合并)" in prompt
    assert prompt.count("】华泰《研报2》《研报1》") == 1
    # LLM 正文没按篇罗列 → 无告警
    assert out["merge_alert"] == []


def test_daily_summary_alert_when_llm_lists_per_report(tmp_path, monkeypatch):
    """LLM 若仍按篇罗列同一机构 → 落盘照旧,但返回值带 merge_alert(前端可提示重跑)。"""
    monkeypatch.setattr(web_app, "RESEARCH_DAILY_DIR", tmp_path / "d")
    monkeypatch.setattr(web_app, "RESEARCH_WEEKLY_DIR", tmp_path / "w")
    monkeypatch.setattr(web_app, "_rag_context_for_variety", lambda *a, **k: "")
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB([
        _db_row(rid=1, conclusion=_CONC), _db_row(rid=2, conclusion=_CONC2),
    ]))

    class _FakeClient:
        def get_llm(self):
            class _L:
                def invoke(self, prompt):
                    class _R:
                        content = "## 二、观点对比与冲突\n华泰两篇研报分别看多与看空。"
                    return _R()
            return _L()

    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeClient())
    out = web_app._generate_daily_summary("2026-09-02")
    assert out["ok"] and out["merge_alert"] == ["华泰"]
    assert web_app._research_daily_path("2026-09-02").is_file()  # 告警不影响落盘


def test_variety_prompt_forbids_per_report_listing(tmp_path, monkeypatch):
    """单品种总结:材料按机构一块 + prompt 要求"按机构归纳统一观点,禁止按篇复述"。"""
    monkeypatch.setattr(web_app, "RESEARCH_VARIETY_DIR", tmp_path / "v")
    monkeypatch.setattr(web_app, "_rag_context_for_variety", lambda *a, **k: "")
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB([
        _db_row(rid=1, conclusion=_CONC), _db_row(rid=2, conclusion=_CONC2),
    ]))
    captured: dict[str, str] = {}

    class _FakeClient:
        def get_llm(self):
            class _L:
                def invoke(self, prompt):
                    captured["prompt"] = prompt

                    class _R:
                        content = "## 多空驱动与逻辑链\n供应收紧。"
                    return _R()
            return _L()

    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeClient())
    out = web_app._generate_variety_summary("2026-09-02", "SC", force=True)
    assert out["ok"]
    p = captured["prompt"]
    assert "同源已合并 2026-09-14" in p
    assert "严禁按篇逐份复述" in p
    assert "【华泰《研报2》《研报1》(同机构 2 篇已合并)】" in p
    assert out["merge_alert"] == []


def test_normalize_source_longzhong_not_over_merged():
    """隆众规则用**带「资讯」的完整键** ⇒ 裸「隆众」与其它含"隆众"的公司不被误并。

    ⚠️ 判据必须是「**被改成了** 隆众资讯、但输入本身不是隆众」，不能写成
    "结果里含『隆众』" —— 后者会把"原样返回的 `隆众XX其它公司`"也误报成归并
    （本轮自检时我自己踩过这个假报警）。
    """
    for raw in ("隆众", "隆众XX其它公司", "隆众能源"):
        got = web_app._normalize_source(raw)
        assert got == raw, "%s 被误并成 %s" % (raw, got)


def test_merge_longzhong_multi_kou_jing_becomes_one_row():
    """★ 用户 2026-10-14 报障：同一品种隆众有多个口径（隆众资讯 / 隆众资讯-聚焦），
    观点总表出现多行。归一后必须合并成**一条**，而不是各占一行。
    """
    recs = [
        _rec(rid=1, code="RU", source="隆众资讯", direction="看多"),
        _rec(rid=2, code="RU", source="隆众资讯-聚焦", direction="看多"),
    ]
    # 生产链路是先归一、再交给合并 helper（见 web_app 里 recs 的构造处）
    for r in recs:
        r["source"] = web_app._normalize_source(r["source"])
    out = web_app._merge_same_source_items(recs, flatten=True)
    assert len(out) == 1, "隆众多口径未合并成一条 —— 用户报的重复行仍在"
    assert out[0]["merged_count"] == 2, "merged_count 应为 2"
    assert len(out[0]["titles"]) == 2, "两篇标题都应保留(不丢内容)"


def test_normalize_source_keeps_staging_dirs_and_aggregators():
    """★ `-研报暂存` / `发现报告` / `上传` 必须**原样返回**。

    它们是**本地导入暂存目录**与**聚合平台**,不是"机构已确认"的正式来源;
    并进机构名会改变语义(暂存 ≠ 已确认),而且三者合计 172 行、影响面最大。
    """
    for raw in ("本地导入-研报暂存", "永安-研报暂存", "中信-研报暂存",
                "发现报告", "上传"):
        assert web_app._normalize_source(raw) == raw, "%s 被误并了" % raw


def test_normalize_source_gongzhonghao_strips_channel_prefix():
    """★ 用户 2026-10-14 方案 B：公众号渠道前缀要收敛到干净机构名。

    渠道信息**不丢** —— 它仍保留在 `source_raw`（前端 tooltip 用），
    只是 `source` 列显示为机构名。
    """
    assert web_app._normalize_source("公众号-多空逻辑") == "多空逻辑"
    assert web_app._normalize_source("公众号-混沌天成研究") == "混沌天成"
    # 幂等:已归一的再入一次不变
    assert web_app._normalize_source("混沌天成") == "混沌天成"


def test_normalize_source_chaos_tiancheng_variants_all_collapse():
    """★ 同一号 `混沌天成` 系的全部写法必须收敛成**一个**标准名（含未来变体）。

    ⚠️ 本条修正过一次**我自己的事实错误**：我原先以为 `混沌天成研究院` 不含子串
    "混沌天成研究"，据此论证"必须用短键"。实测该假设**是错的** ——
    `混沌天成研究院` = `混沌天成研究` + "院" ⇒ **包含**该子串，两种键都能命中。
    现在选短键 `混沌天成` 的理由改为：**它还能覆盖 `混沌天成期货` 等未来变体**，
    而长键不能。这条断言把"全部变体归到同一名"钉住，与键长无关。
    """
    variants = ["混沌天成研究", "混沌天成研究院", "混沌天成期货",
                "公众号-混沌天成研究", "混沌天成"]
    names = {web_app._normalize_source(v) for v in variants}
    assert names == {"混沌天成"}, "混沌天成系未收敛到单一标准名：%s" % names


def test_merge_gongzhonghao_variants_become_one_row():
    """★ 端到端语义：同一号的不同写法，经归一后必须合并成**一条**观点。"""
    recs = [
        _rec(rid=1, code="SC", source="公众号-混沌天成研究", direction="看多"),
        _rec(rid=2, code="SC", source="混沌天成研究院", direction="看多"),
    ]
    for r in recs:
        r["source"] = web_app._normalize_source(r["source"])
    out = web_app._merge_same_source_items(recs, flatten=True)
    assert len(out) == 1, "同一号的不同写法未合并 —— 用户报的重复行仍在"
    assert out[0]["merged_count"] == 2
    assert len(out[0]["titles"]) == 2, "两篇标题都应保留(不丢内容)"
