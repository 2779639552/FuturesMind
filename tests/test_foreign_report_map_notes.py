# -*- coding: utf-8 -*-
"""外文研报超长正文的分块笔记(Map) + 笔记合并(Merge) —— 用假 LLM 回归。

【本文件要防的假绿】
最典型的假修复是"分了块，但归纳时只吃了第一块"——代码看着有循环、测试也可能通过。
因此核心断言用**哨兵串**：每块原文各埋一个唯一 token，要求**每一块的哨兵都出现在
最终送给归纳的文本里**。只处理第一块时，后面 N-1 个哨兵必然缺失，断言变红。

其余断言：
· 短文本必须走 full 路径且**不产生任何 LLM 调用**（这是"零回归"的硬指标）；
· 分块触顶、笔记大面积失败时，必须降级为抽样而不是报错/静默截尾；
· 合并要去重（跨块重复的 findings/numbers 只留一条）。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import foreign_report as fr


class _Msg:
    def __init__(self, content):
        self.content = content
        self.additional_kwargs = {}


class _LLM:
    """假 LLM：从 prompt 里抽出所有 SEN<下标> 哨兵，回一份引用它们的 JSON 笔记。

    【为什么要"引用哨兵"】这样合并后的压缩稿里能否出现全部哨兵，就直接反映了
    "每一块是否都被真的读过并贡献了内容"，而不只是"代码跑过了循环"。
    """

    def __init__(self, fail_patterns=()):
        self.prompts = []
        self.calls = 0
        self.fail_patterns = tuple(fail_patterns)

    def invoke(self, prompt, config=None, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        for pat in self.fail_patterns:
            if pat in prompt:
                raise RuntimeError("模拟失败")
        sens = sorted(set(re.findall(r"SEN\d+", prompt)), key=lambda s: int(s[3:]))
        # 每块只回最常见的最近一个哨兵，模拟"笔记覆盖了本块内容"
        key = sens[-1] if sens else "NONE"
        payload = {
            "findings": [{"point": f"本块结论引用 {key}", "basis": f"依据 {key}", "page": 1}],
            "numbers": [{"item": f"指标{key}", "value": "100 美元/吨", "period": "2026年9月", "page": 1}],
            "terms": [{"en": f"TERM{key}", "zh": "术语"}],
            "meta": {"title": "标题", "org": "机构", "service": "", "issue": "",
                     "pub_date": "2026-09-01", "website": "example.com"},
        }
        return _Msg(json.dumps(payload, ensure_ascii=False))


def _doc(n_paras=60, para_chars=2000, per_page=3):
    pages, buf, pno = [], [], 1
    for i in range(n_paras):
        buf.append(f"SEN{i}" + ("文" * max(1, para_chars - len(f"SEN{i}"))))
        if len(buf) >= per_page:
            pages.append({"no": pno, "text": "\n\n".join(buf)})
            buf, pno = [], pno + 1
    if buf:
        pages.append({"no": pno, "text": "\n\n".join(buf)})
    return pages


@pytest.mark.unit
class TestBuildAnalysisInput:
    def test_short_doc_uses_full_and_makes_no_llm_call(self):
        """短文本必须零调用、且与改造前的 _join_text 输出**逐字一致**（零回归硬指标）。"""
        pages = _doc(n_paras=6, para_chars=1000)
        llm = _LLM()
        text, warns, info = fr._build_analysis_input(pages, llm)
        assert info["mode"] == "full"
        assert llm.calls == 0, "短文本不应产生任何 LLM 调用"
        assert not warns
        # ★ 逐字等价：改造前送进归纳的就是 _join_text 的结果（含「［第 N 页］」标记）
        assert text == fr._join_text(pages, limit=1 << 30)[0], "短文本路径输出与改造前不一致"
        assert "［第 1 页］" in text, "页码标记丢失（归纳将无法给出结论出处）"

    def test_long_doc_uses_map_and_covers_every_chunk(self):
        """★ 核心断言：每一块的哨兵都要出现在压缩稿里（防"只读了第一块"）。"""
        pages = _doc(n_paras=60, para_chars=2000)     # 约 12 万字符 → 2~3 块
        llm = _LLM()
        text, warns, info = fr._build_analysis_input(pages, llm)

        assert info["mode"] == "map", f"超限应走 map，实际 {info['mode']}"
        assert info["chunks"] >= 2

        chunks, _ = fr._chunk_for_notes(pages)
        # 每块最后一个哨兵（假 LLM 就是引用它作为"读过了"的凭证）
        for i, c in enumerate(chunks):
            units = fr._para_units(pages)
            sentinel = f"SEN{c['u_to']}"
            assert sentinel in text, f"第 {i+1} 块（哨兵 {sentinel}）未出现在压缩稿中"

    def test_map_input_within_analyze_limit(self):
        pages = _doc(n_paras=60, para_chars=2000)
        llm = _LLM()
        text, _, _ = fr._build_analysis_input(pages, llm)
        assert len(text) <= fr.MAX_ANALYZE_CHARS

    def test_map_includes_original_head_for_cover_meta(self):
        """原文首部原样附带 —— 否则封面标题/机构容易被压缩丢掉。"""
        pages = _doc(n_paras=60, para_chars=2000)
        llm = _LLM()
        text, _, _ = fr._build_analysis_input(pages, llm)
        assert "SEN0" in text
        assert "原文首部" in text

    def test_truncated_chunks_degrade_to_sample(self):
        """块数触顶必须降级抽样，且不浪费一次注定不完整的 map 调用。"""
        pages = _doc(n_paras=400, para_chars=2000)    # 约 80 万字符
        llm = _LLM()
        text, warns, info = fr._build_analysis_input(pages, llm)
        assert info["mode"] == "sample", f"应降级抽样，实际 {info['mode']}"
        assert llm.calls == 0, "分块触顶时不应发起 map 调用"
        assert any("抽样" in w for w in warns)
        assert len(text) <= fr.MAX_ANALYZE_CHARS
        # 抽样也必须碰到尾部（不是截尾）
        assert "SEN399" in text, "降级抽样丢了结尾 —— 又退回改造前的截尾行为"

    def test_high_failure_rate_degrades_to_sample(self):
        """笔记大面积失败 → 降级抽样，并如实告警。"""
        pages = _doc(n_paras=60, para_chars=2000)
        chunks, _ = fr._chunk_for_notes(pages)
        assert len(chunks) >= 2
        # 让除第一块外的所有块都失败 → 失败率 > 30%
        llm = _LLM(fail_patterns=tuple(
            f"SEN{c['u_to']}" for c in chunks[1:]
        ))
        text, warns, info = fr._build_analysis_input(pages, llm)
        assert info["mode"] == "sample", f"失败率过高应降级，实际 {info['mode']}"
        assert info["failed"] >= 1
        assert any("失败" in w for w in warns)

    def test_partial_failure_still_uses_map_and_reports(self):
        """缺一块（<30%）仍走 map，但必须在告警里点名缺了哪一块。"""
        pages = _doc(n_paras=200, para_chars=2000)    # 约 40 万字符 → 5 块
        chunks, truncated = fr._chunk_for_notes(pages)
        assert not truncated and len(chunks) >= 4
        llm = _LLM(fail_patterns=(f"SEN{chunks[-1]['u_to']}",))
        text, warns, info = fr._build_analysis_input(pages, llm)
        assert info["mode"] == "map"
        assert info["failed"] == 1
        assert any("笔记失败" in w for w in warns)


class _VerboseLLM(_LLM):
    """假 LLM 的"啰嗦版"：笔记体量远超上下文预算，用来逼出分级压缩路径。

    【为什么必须测这个】2026-10-13 实测真实 OPR 报告（裂解价差表几百行）时，
    笔记合并后超过 90,000，原来的兜底是 `text[:MAX]` —— **正好又从尾部切掉**，
    把刚修好的"覆盖全文"毁掉。本类就是要复现那种"笔记爆量"的场景。
    """

    def invoke(self, prompt, config=None, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        sens = sorted(set(re.findall(r"SEN\d+", prompt)), key=lambda s: int(s[3:]))
        key = sens[-1] if sens else "NONE"
        findings = [
            {"point": f"结论{i}（引自 {key}）", "basis": "依据" + ("详" * 200), "page": 1}
            for i in range(150)
        ]
        findings[0]["point"] = f"{key} 的结论"      # 哨兵放在首条：限量时也能留下
        numbers = [
            {"item": f"指标{key}_{i}", "value": f"{i}.5 美元/桶", "period": "2026年9月", "page": 1}
            for i in range(300)
        ]
        return _Msg(json.dumps({"findings": findings, "numbers": numbers, "terms": [],
                                "meta": {}}, ensure_ascii=False))


@pytest.mark.unit
class TestCompactionLadder:
    def test_ladder_shortens_monotonically(self):
        notes = [{"_chunk": 0, "_pages": [1],
                  "findings": [{"point": f"结论{i}", "basis": "依据" + "详" * 200, "page": 1}
                               for i in range(100)],
                  "numbers": [{"item": f"指标{i}", "value": f"{i}.5", "period": "9月", "page": 1}
                              for i in range(200)]}]
        lengths = [len(fr._merge_notes(notes, level=l)) for l in range(len(fr._NOTE_COMPACT_LADDER))]
        assert lengths[0] > lengths[1] > lengths[-1], f"降档未单调变短: {lengths}"
        assert lengths[-1] < lengths[0] / 2, "最低档压缩力度不足"

    def test_verbose_notes_do_not_lose_tail(self):
        """★ 核心防守：笔记爆量时必须降档压缩，**不能从尾部盲切**。

        判据：最后一个块（对应原文最尾部）的哨兵必须仍在最终文本里。
        若退回 `text[:MAX]` 盲切，末块内容会被整段丢弃，本用例变红。
        """
        pages = _doc(n_paras=200, para_chars=2000)   # 约 40 万字符 → 5 块
        llm = _VerboseLLM()
        text, warns, info = fr._build_analysis_input(pages, llm)

        assert info["mode"] == "map", f"笔记爆量不该降级成抽样，实际 {info['mode']}"
        assert len(text) <= fr.MAX_ANALYZE_CHARS, "压缩稿仍超上下文预算"
        assert info["level"] > 0, "笔记爆量应触发降档压缩"

        chunks, truncated = fr._chunk_for_notes(pages)
        assert not truncated and len(chunks) >= 3
        last_sentinel = f"SEN{chunks[-1]['u_to']}"
        assert last_sentinel in text, (
            f"末块哨兵 {last_sentinel} 丢失 —— 压缩时从尾部盲切了（这正是要防的回归）"
        )
        assert any("压缩" in w for w in warns), "降档压缩未在告警中如实说明"

    def test_no_llm_call_for_short_doc_still_holds(self):
        """加了降档逻辑后，短文本路径仍必须是零调用。"""
        pages = _doc(n_paras=6, para_chars=1000)
        llm = _VerboseLLM()
        text, _, info = fr._build_analysis_input(pages, llm)
        assert info["mode"] == "full"
        assert llm.calls == 0


@pytest.mark.unit
class TestMergeNotes:
    def test_dedup_findings_numbers_terms(self):
        notes = [
            {"_chunk": 0, "_pages": [1], "findings": [{"point": "结论甲", "basis": "b", "page": 1}],
             "numbers": [{"item": "价差", "value": "100", "period": "9月", "page": 1}],
             "terms": [{"en": "LPG", "zh": "液化石油气"}]},
            {"_chunk": 1, "_pages": [2], "findings": [{"point": "结论甲", "basis": "b2", "page": 2}],
             "numbers": [{"item": "价差", "value": "100", "period": "9月", "page": 2}],
             "terms": [{"en": "lpg", "zh": "液化石油气"}]},
        ]
        text = fr._merge_notes(notes)
        assert text.count("结论甲") == 1, "跨块重复的 findings 未去重"
        assert text.count("价差=100") == 1, "重复数字未去重"
        assert text.lower().count("lpg=") == 1, "重复术语未去重"

    def test_keeps_provenance(self):
        notes = [{"_chunk": 3, "_pages": [7, 8],
                  "findings": [{"point": "结论", "basis": "依据", "page": 8}]}]
        text = fr._merge_notes(notes)
        assert "第 4 块" in text and "7-8" in text, "块号/页码溯源丢失"
        assert "p.8" in text, "结论缺页码"

    def test_blank_entries_skipped(self):
        """空条目要跳过，不能写出 "指标=" 这类残缺行。"""
        notes = [{"_chunk": 0, "_pages": [1],
                  "findings": [{"point": "", "basis": "x"}],
                  "numbers": [{"item": "", "value": "1"}, {"item": "a", "value": ""}],
                  "terms": [{"en": "", "zh": "x"}]}]
        text = fr._merge_notes(notes)
        assert "=" not in text.replace("──", ""), f"出现了残缺条目: {text!r}"
