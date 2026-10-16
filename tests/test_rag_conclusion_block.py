"""RAG 结论块与降噪规则测试(纯函数,不依赖 chromadb / sentence-transformers)。

覆盖 2026-09-15 上线的两块能力:
  ① 正文行级清洗 + 块级门槛(clean_lines / block_passes / is_boiler_chunk / is_toc_block)
  ② 【研报结论】块(conclusion_text,四道闸门:影子校验/身份标注/自报字段并存/开关回滚)

【真机依据】用户反馈:问"永安期货对合成橡胶BR的观点和策略",RAG 检回的 417 片段
只有丁二烯产能表与 BR 行情块,没有任何方向性文字;而总结模块给出的是"永安看空"。
根因是 417 的"BR 看空"策略段落被 7 个【图】块挤出单篇 top-2(该块在篇内语义排名第 8)。
"""

from tradingagents.rag.chunking import (
    CONCLUSION_MARK,
    ENV_CONCLUSION_CHUNK,
    block_passes,
    build_chunks,
    clean_lines,
    conclusion_enabled,
    conclusion_text,
    is_boiler_chunk,
    is_toc_block,
)

# 与 417 同构的最小样本:方向/策略在 structured_data,结论小节在 conclusion_md
STRATEGY = (
    "当前价格下负反馈逐步出现,单边偏择机高空,短期也可观望待情绪回落;"
    "期现与月差均可考虑逢低正套;短期多天胶空合成,长期仍以做缩为主。"
)


def _row(**kw):
    row = {
        "id": 417,
        "title": "合成橡胶周报",
        "variety": "BR",
        "varieties": "BR,RU",
        "publish_date": "2026-09-13",
        "report_type": "周报",
        "source": "永安期货研究中心能化团队",
        "direction": "看空",
        "extracted_text": "",
        "layout_text": "",
    }
    row.update(kw)
    return row


def _sd(variety="BR", direction="看空", conf=0.8, adv=STRATEGY):
    return (
        '{"varieties": [{"variety": "%s", "direction": "%s", "confidence": %s,'
        ' "operation_advice": "%s"}]}' % (variety, direction, conf, adv)
    )


# ── ① 结论块:基本产出 ────────────────────────────────────────────────

def test_conclusion_block_has_mark_and_self_reported_direction(monkeypatch):
    """结论块必须带身份标注,并列出自报方向/置信度/策略(闸门②③)。"""
    monkeypatch.delenv(ENV_CONCLUSION_CHUNK, raising=False)
    row = _row(structured_data=_sd())
    block = conclusion_text(row, source_text="正文里没有任何数字")
    assert block.startswith(CONCLUSION_MARK)  # 闸门②
    assert "BR 方向:看空" in block.replace("：", ":")
    assert "置信度 0.8" in block
    assert "期现与月差均可考虑逢低正套" in block  # 闸门③ 原句并存


def test_conclusion_block_without_fields_returns_empty():
    """未处理完成/旧数据(无 structured_data 且无 conclusion_md)→ 不出结论块。"""
    assert conclusion_text(_row(), source_text="随便什么正文") == ""


def test_multi_variety_lists_each_code():
    row = _row(
        structured_data=(
            '{"varieties": [{"variety": "BR", "direction": "看空", "confidence": 0.8,'
            ' "operation_advice": ""}, {"variety": "RU", "direction": "看多",'
            ' "confidence": 0.55, "operation_advice": "短期多天胶空合成"}]}'
        )
    )
    block = conclusion_text(row, source_text="")
    assert "BR 方向:看空" in block.replace("：", ":")
    assert "RU 方向:看多" in block.replace("：", ":")
    assert "短期多天胶空合成" in block


def test_direction_missing_marks_undisclosed():
    row = _row(structured_data='{"varieties": [{"variety": "BR", "direction": ""}]}')
    assert "未披露" in conclusion_text(row, source_text="")


def test_broken_structured_data_does_not_raise():
    """structured_data 损坏/类型不对时按"无结构化字段"处理,不得抛异常。"""
    assert conclusion_text(_row(structured_data="{不是 json"), source_text="") == ""
    assert conclusion_text(_row(structured_data='["列表不是字典"]'), source_text="") == ""


# ── ① 结论块:闸门① 影子校验 ──────────────────────────────────────────

def test_shadow_validation_drops_borrowed_numbers():
    """数字溯不到本篇正文的小节整节丢弃 —— conclusion_md 生成时注入了**其他研报**的
    RAG 片段(_llm_opinion_conclusion 的 rag_ctx),会把别篇/别日期的数字写进来。"""
    md = "## 观点与依据\n因2026-09-14 炼厂开工率25.27% → 推演供需转弱 → 方向看空。"
    row = _row(structured_data='{"varieties": []}', conclusion_md=md)
    # 本篇正文里没有 25.27% / 09-14(那是别篇数字)→ 整节丢弃
    block = conclusion_text(row, source_text="本篇只讲顺丁橡胶,开工率 62%。")
    assert "炼厂开工率" not in block
    # 同一节,数字可溯回本篇 → 保留
    block2 = conclusion_text(row, source_text="2026-09-14 炼厂开工率25.27%,有所回落。")
    assert "炼厂开工率25.27%" in block2


def test_shadow_validation_normalizes_thousands_separator_and_fullwidth():
    """千分位/全角数字不算"溯不到":1,234 与 1234、全角 18300 与 18300 视为同数。"""
    row = _row(
        structured_data='{"varieties": []}',
        conclusion_md="## 多空要点\n综述:区间 18300~19600,仓单 1,234 手。",
    )
    block = conclusion_text(row, source_text="RU 主力【１８３００-19600】,仓单1234 手。")
    assert "18300~19600" in block


def test_section_without_numbers_passes():
    """纯推理小节(无 ≥3 位数字)不因影子校验被丢 —— 这正是"观点与依据"的常态。"""
    row = _row(
        structured_data='{"varieties": []}',
        conclusion_md="## 观点与依据\n因民营降负与利润倒挂 → 推演高价难传导 → 方向看空。",
    )
    assert "民营降负" in conclusion_text(row, source_text="完全无关的正文")


# ── ① 结论块:闸门④ 开关与长度 ────────────────────────────────────────

def test_env_switch_disables_conclusion_chunk(monkeypatch):
    """RAG_CONCLUSION_CHUNK=0 时不出结论块(重建索引即可整块回滚)。"""
    row = _row(structured_data=_sd(), layout_text="顺丁橡胶供需双弱。" * 30)
    monkeypatch.delenv(ENV_CONCLUSION_CHUNK, raising=False)
    assert conclusion_enabled() is True
    assert build_chunks(row)[0]["metadata"]["kind"] == "conclusion"
    monkeypatch.setenv(ENV_CONCLUSION_CHUNK, "0")
    assert conclusion_enabled() is False
    assert all(c["metadata"]["kind"] == "body" for c in build_chunks(row))


def test_conclusion_chunk_is_first_and_capped(monkeypatch):
    monkeypatch.delenv(ENV_CONCLUSION_CHUNK, raising=False)
    long_md = (
        "## 观点与依据\n" + "供需双弱与利润倒挂并存," * 12
        + "\n## 多空要点\n" + "利多:供给收缩;利空:需求受抑。" * 12
        + "\n## 交易要素与风险\n" + "方向:看空;头寸:轻仓逢高试空。" * 12
    )
    row = _row(structured_data=_sd(), conclusion_md=long_md, layout_text="正文" * 200)
    chunks = build_chunks(row)
    assert chunks[0]["metadata"]["kind"] == "conclusion"
    assert chunks[0]["metadata"]["chunk_index"] == 0
    assert chunks[0]["metadata"]["chunk_total"] == len(chunks)
    assert len(chunks[0]["text"]) <= 480
    assert chunks[0]["text"].startswith(CONCLUSION_MARK)  # 抬头行永远保留


def test_conclusion_chunk_embedding_text_has_title_prefix(monkeypatch):
    """embedding_text 仍带标题前缀(标题前缀显著提升小库召回)。"""
    monkeypatch.delenv(ENV_CONCLUSION_CHUNK, raising=False)
    chunks = build_chunks(_row(structured_data=_sd()))
    assert chunks[0]["embedding_text"].startswith("合成橡胶周报(BR 2026-09-13 周报):")


# ── ② 行级清洗 ────────────────────────────────────────────────────────

def test_clean_lines_folds_watermark_repeat():
    """水印/页脚重复行只留一次(中信 402 的联系行在全文重复 304 次)。

    两类要分清:命中免责/联系词表的重复行(含"员工"等)整行删净(残留 0 份);
    中性重复行(纯机构水印)保留首次出现(残留 1 份),避免把正常抬头删空。
    """
    boiler = "\n".join(["中信期货员工/曹张驰/18058991618"] * 5 + ["顺丁橡胶供需双弱。"])
    out = clean_lines(boiler)
    assert out.count("曹张驰") == 0
    assert "顺丁橡胶供需双弱。" in out
    neutral = "\n".join(["永安期货研究中心"] * 5 + ["顺丁橡胶供需双弱。"])
    assert clean_lines(neutral).count("永安期货研究中心") == 1


def test_clean_lines_truncates_tail_disclaimer_region():
    """尾部免责区整段截断 —— 417 的免责续行块曾以 0.704 分排在该问题第一位。"""
    text = (
        "顺丁橡胶供需双弱,利润倒挂。\n"
        "【免责声明】\n"
        "本报告不构成投资建议,未经许可不得翻版、复制、转载。\n"
        "市场有风险,投资需谨慎。\n"
    )
    out = clean_lines(text)
    assert "顺丁橡胶供需双弱,利润倒挂。" in out
    assert "翻版" not in out and "投资需谨慎" not in out


def test_clean_lines_drops_boiler_and_tiny_lines():
    text = "顺丁供需双弱。\n扫码关注公众号\n中\n2026-09-13\n邮箱:abc@x.com\n"
    out = clean_lines(text)
    assert "顺丁供需双弱。" in out
    assert "扫码" not in out and "邮箱" not in out
    assert "2026-09-13" not in out  # 纯日期/页码行


def test_clean_lines_keeps_risk_sentence():
    """⚠️ 区分点:正文里的"风险:"句尾是研报常见写法,行级必须保留(误删即吃掉正文)。"""
    out = clean_lines("风险:丁二烯价格走强或民营装置集中复工。\n交易要素:轻仓逢高试空。")
    assert "丁二烯价格走强" in out


def test_clean_lines_on_empty_text():
    assert clean_lines("") == ""


# ── ② 块级门槛 ────────────────────────────────────────────────────────

def test_is_boiler_chunk_only_when_disclaimer_lines_dominate():
    """块级免责判定看"免责行占比"而非关键词计数:混排块必须保留(实测误伤 80 块)。"""
    mixed = (
        "合约持仓过大,成交过热的情况下并不出现趋势性下跌,单边价格处于高位震荡市。\n"
        "免责声明:本报告不构成投资建议。\n"
        "中国汽油出口利润较好,对芳烃估值形成支撑。\n"
    )
    assert is_boiler_chunk(mixed) is False  # 1/3 免责行 → 保留
    pure = "免责声明:本报告不构成投资建议。\n未经许可不得翻版、复制、转载。\n版权归本公司所有。\n"
    assert is_boiler_chunk(pure) is True


def test_is_toc_block_needs_no_sentence():
    """纯目录块丢弃;含完整句子的目录混排块保留(句号否决:宁可漏也不能吃正文)。"""
    toc = "www.yafco.com\n合成橡胶周报\nContents\n顺丁橡胶价格、价差与估值\n顺丁橡胶供需\n"
    assert is_toc_block(toc) is True
    mixed = toc + "整体评估:顺丁9月持平;丁二烯9月小累。\n"
    assert is_toc_block(mixed) is False


def test_block_passes_fig_gate():
    """【图】块豁免数字过滤的前提是块内汉字够多(否则只剩坐标轴数字)。"""
    axis_only = "【图】\n" + "0 500 1000 1500 2000 2500 3000 3500 4000 4500 5000 5500\n" * 8
    assert block_passes(axis_only) is False
    # 同样密度的数字,但带足量图题/图例/资料来源汉字 → 保留
    labeled = axis_only + "图1:顺丁橡胶与丁二烯价差走势(单位:元/吨)\n资料来源:Wind、永安期货研究中心\n" * 4
    assert block_passes(labeled) is True


def test_block_passes_keeps_plain_prose():
    assert block_passes("顺丁橡胶供需双弱,利润倒挂,预计短期价格维持震荡格局。") is True


def test_build_chunks_keeps_original_blocks(monkeypatch):
    """闸门④:结论块与原文块**并存** —— 原文里的策略原句不能因为出了结论块就消失。"""
    monkeypatch.delenv(ENV_CONCLUSION_CHUNK, raising=False)
    row = _row(structured_data=_sd(), layout_text="交易策略:" + STRATEGY + "。" * 3)
    chunks = build_chunks(row)
    kinds = [c["metadata"]["kind"] for c in chunks]
    assert kinds[0] == "conclusion"
    assert "body" in kinds
    assert any(
        "期现与月差均可考虑逢低正套" in c["text"]
        for c in chunks
        if c["metadata"]["kind"] == "body"
    )


def test_empty_text_and_no_conclusion_returns_no_chunks():
    row = _row(structured_data='{"varieties": []}')
    assert build_chunks(row) == []
