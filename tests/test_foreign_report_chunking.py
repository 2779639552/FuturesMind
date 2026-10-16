# -*- coding: utf-8 -*-
"""外文研报超长正文的"分层归纳"分块工具 —— 纯函数回归。

【背景】改造前 `_join_text` 对 >90,000 字符的正文硬截尾，实测 262,637 字符的
OPR 报告归纳只覆盖前 34%，而砍掉的恰是中后部预测表。改为"分块笔记 → 四路归纳"
两级流程（仅超限文件启用）。

本文件只测**不依赖 LLM 的纯函数**（可离线跑、零成本）：
1. `_chunk_for_notes`：段落边界对齐、尾部重叠、全覆盖无空隙、块数触顶要显式告知；
2. `_sample_text`：降级抽样必须 ≤ 上限、头尾都碰到、插入省略标记。

【判据写法说明】用"每段一个唯一哨兵串"来断言覆盖 —— 只要某段落入某块，其哨兵必然
出现在该块文本里。这比数字符偏移更抗换行口径差异（\\n vs \\r\\n）导致的假报。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import foreign_report as fr


def _doc(n_paras=60, para_chars=2000, per_page=3):
    """造一份分段可控的假文档：第 i 段含哨兵 ``SEN<i>``。"""
    pages = []
    buf = []
    pno = 1
    for i in range(n_paras):
        body = f"SEN{i}" + ("文" * max(1, para_chars - len(f"SEN{i}")))
        buf.append(body)
        if len(buf) >= per_page:
            pages.append({"no": pno, "text": "\n\n".join(buf)})
            buf = []
            pno += 1
    if buf:
        pages.append({"no": pno, "text": "\n\n".join(buf)})
    return pages


class TestChunkForNotes:
    def test_overlong_doc_splits_and_fully_covers(self):
        pages = _doc(n_paras=60, para_chars=2000)   # 约 120,000 字符
        chunks, truncated = fr._chunk_for_notes(pages, size=80000, overlap=2000)

        assert truncated is False, "60 段/12万字符不应触发块数上限"
        assert len(chunks) >= 2, f"12万字符必须切成多块，实际 {len(chunks)}"

        units = fr._para_units(pages)
        last = len(units) - 1
        # 全覆盖、无空隙：首块从 0 起，相邻块 u_from <= 上一块 u_to + 1，末块到 last
        assert chunks[0]["u_from"] == 0
        assert chunks[-1]["u_to"] == last
        for a, b in zip(chunks, chunks[1:]):
            assert b["u_from"] <= a["u_to"] + 1, (
                f"块间出现空隙: 上一块到 {a['u_to']}，下一块从 {b['u_from']} 开始"
            )

    def test_adjacent_chunks_overlap(self):
        """相邻块必须有实质重叠 —— 否则跨块的结论会被切断。"""
        pages = _doc(n_paras=60, para_chars=2000)
        chunks, _ = fr._chunk_for_notes(pages, size=80000, overlap=2000)
        a, b = chunks[0], chunks[1]
        assert b["u_from"] <= a["u_to"], "相邻块之间没有重叠段落"
        # 重叠段落确实同时出现在两块文本里
        units = fr._para_units(pages)
        shared = units[b["u_from"]]["text"]
        assert shared in a["text"] and shared in b["text"], "重叠段落未在相邻块中同时出现"

    def test_no_split_inside_paragraph(self):
        """绝不从段落中间切开：每块文本拆回"段落"都应是完整段落（页码标记不算段落）。"""
        pages = _doc(n_paras=30, para_chars=3000)
        chunks, _ = fr._chunk_for_notes(pages, size=20000, overlap=1000)
        from_units = {u["text"] for u in fr._para_units(pages)}
        for c in chunks:
            for para in c["text"].split("\n\n"):
                if para.startswith("［第 ") and para.endswith(" 页］"):
                    continue  # 页码标记是本函数插入的，不是原文段落
                assert para in from_units, f"块内出现被切断的段落片段: {para[:40]!r}"

    def test_chunks_carry_page_markers(self):
        """块文本必须带页码标记 —— 笔记要标 page，没标记模型只能编。"""
        pages = _doc(n_paras=30, para_chars=3000, per_page=3)
        chunks, _ = fr._chunk_for_notes(pages, size=20000, overlap=1000)
        for c in chunks:
            assert "［第 " in c["text"], "块文本缺少页码标记"

    def test_truncated_flag_when_exceeding_max_chunks(self):
        """块数触顶必须显式返回 truncated=True（由调用方降级抽样），不静默丢尾巴。"""
        pages = _doc(n_paras=200, para_chars=2000)   # 约 40 万字符
        chunks, truncated = fr._chunk_for_notes(pages, size=20000, overlap=1000, max_chunks=5)
        assert truncated is True, "超过 max_chunks 必须置 truncated"
        assert len(chunks) <= 5

    def test_short_doc_single_chunk(self):
        pages = _doc(n_paras=3, para_chars=500)
        chunks, truncated = fr._chunk_for_notes(pages, size=80000)
        assert truncated is False
        assert len(chunks) == 1

    def test_pages_recorded_for_provenance(self):
        """每块要带页码，供笔记标注"结论出自第几页"。"""
        pages = _doc(n_paras=60, para_chars=2000, per_page=3)
        chunks, _ = fr._chunk_for_notes(pages, size=80000, overlap=2000)
        for c in chunks:
            assert c["pages"], "块缺少页码归属"
            assert c["pages"] == sorted(c["pages"]), "页码应升序"


class TestSampleText:
    def test_short_doc_untouched(self):
        pages = _doc(n_paras=3, para_chars=300)
        text, sampled = fr._sample_text(pages, limit=90000)
        assert sampled is False
        assert text == fr._units_text(fr._para_units(pages)), "未超限不应改动正文"

    def test_respects_limit(self):
        pages = _doc(n_paras=200, para_chars=3000)   # 约 60 万字符
        text, sampled = fr._sample_text(pages, limit=90000)
        assert sampled is True
        assert len(text) <= 90000, f"抽样结果 {len(text)} 超过上限 90000"

    def test_head_and_tail_both_present(self):
        """★ 本改造的核心诉求：尾部（预测表所在）不得像"截尾"那样被整段丢掉。"""
        pages = _doc(n_paras=200, para_chars=3000)
        text, sampled = fr._sample_text(pages, limit=90000)
        assert sampled is True
        assert "SEN0" in text, "抽样丢了开头"
        assert "SEN199" in text, "抽样丢了结尾（改造前的截尾正是丢这里）"

    def test_middle_also_present(self):
        """正文区也必须被抽到 —— 不能只留头尾。

        【为什么不断言某个具体下标】Bresenham 布点按字符预算均匀落点，命中哪些下标
        取决于段落长度分布，钉死单一下标会变成脆测试。这里断言"正文区至少命中若干段"。
        """
        n = 200
        pages = _doc(n_paras=n, para_chars=3000)
        text, _ = fr._sample_text(pages, limit=90000)
        body_hits = [i for i in range(int(n * 0.2), int(n * 0.8)) if f"SEN{i}" in text]
        assert len(body_hits) >= 2, f"正文区几乎没抽到（命中 {len(body_hits)} 段）"

    def test_marks_elisions(self):
        """省略处要留标记，便于事后核对"哪一段被略过"。"""
        pages = _doc(n_paras=200, para_chars=3000)
        text, _ = fr._sample_text(pages, limit=90000)
        assert "略去原文" in text

    def test_weighted_head_and_tail_outweigh_body(self):
        """头 3× / 尾 2× / 正文 1×：头尾命中率应高于正文。"""
        n = 300
        pages = _doc(n_paras=n, para_chars=3000)
        text, _ = fr._sample_text(pages, limit=90000)
        hit_head = sum(1 for i in range(0, int(n * 0.15)) if f"SEN{i}" in text)
        hit_tail = sum(1 for i in range(int(n * 0.85), n) if f"SEN{i}" in text)
        hit_body = sum(1 for i in range(int(n * 0.2), int(n * 0.8)) if f"SEN{i}" in text)
        rate = lambda h, tot: h / max(1, tot)
        assert rate(hit_tail, int(n * 0.15)) > rate(hit_body, int(n * 0.6)), (
            f"尾部命中率未高于正文: 尾 {hit_tail}/{int(n*0.15)} vs 正文 {hit_body}/{int(n*0.6)}"
        )
        assert rate(hit_head, int(n * 0.15)) > rate(hit_body, int(n * 0.6)), (
            f"头部命中率未高于正文: 头 {hit_head}/{int(n*0.15)} vs 正文 {hit_body}/{int(n*0.6)}"
        )


class TestParaUnits:
    def test_splits_on_blank_line_and_keeps_page(self):
        pages = [{"no": 7, "text": "第一段\n\n第二段\n\n\n第三段"}]
        units = fr._para_units(pages)
        assert [u["text"] for u in units] == ["第一段", "第二段", "第三段"]
        assert all(u["page"] == 7 for u in units)

    def test_skips_blank_pages(self):
        pages = [{"no": 1, "text": "  \n\n "}, {"no": 2, "text": "有内容"}]
        units = fr._para_units(pages)
        assert len(units) == 1 and units[0]["page"] == 2


@pytest.mark.unit
def test_module_imports_and_constants_sane():
    """常量自洽性 —— 分块必须小于分析上限，否则"分层"反而先超限。"""
    assert 0 < fr.MAP_CHUNK_CHARS <= fr.MAX_ANALYZE_CHARS
    assert 0 < fr.MAP_OVERLAP_CHARS < fr.MAP_CHUNK_CHARS
    assert fr.MAX_MAP_CHUNKS >= 2
    assert fr.NOTE_MAX_CHARS > 0
    assert fr.MAX_MAP_CHUNKS * fr.NOTE_MAX_CHARS <= fr.MAX_ANALYZE_CHARS, (
        "压缩稿上限不得超过归纳上下文余量，否则 reduce 照样超限"
    )
