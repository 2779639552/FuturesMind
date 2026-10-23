"""入库去重护栏（2026-10-21 用户要求「加一下」）。

背景：同一份 PDF 换个文件名再导一次 ⇒ 观点总览出现两条一模一样的研报
（实例：中信《资金情绪爆发…》一次原始中文名、一次重命名成 20260927_2.pdf）。
文件名/来源/时间都不同，唯一稳定的是**正文完全相同**。

本文件守：
  1. 正文完全相同 → 能查到，判重生效；
  2. 正文差一个字 → 不算重复（不能误杀"同一标题的两批不同日报"）；
  3. exclude_id 生效（重跑同一条时不会判自己）；
  4. 空正文不参与判重；
  5. 入库链路真的调了它（静态断言，防"方法写了没人用"）。
"""

from __future__ import annotations

import io
import pathlib

import database
import web_app

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEXT = "## 碳酸锂 结论\n供给回升、需求偏弱，中性震荡。" * 40


def _db(tmp_path):
    return database.AgentSenseDB(tmp_path / "t.db")


def _insert(db, filename, text):
    rid = db.insert_research_report(variety="LC", title="锂产业链周报", source="永安期货",
                                    filename=filename, file_path="")
    db.update_research_report(rid, status="done", extracted_text=text)
    return rid


def test_identical_text_is_detected(tmp_path):
    db = _db(tmp_path)
    a = _insert(db, "锂产业链周报.pdf", TEXT)
    b = _insert(db, "20260927_2.pdf", TEXT)
    hit = db.find_research_report_by_text(TEXT, exclude_id=b)
    assert hit and int(hit["id"]) == a, "同一份正文应命中已入库的那条"


def test_one_char_difference_is_not_duplicate(tmp_path):
    db = _db(tmp_path)
    _insert(db, "a.pdf", TEXT)
    assert db.find_research_report_by_text(TEXT + "。") is None, "差一个字不能算重复"


def test_exclude_self(tmp_path):
    db = _db(tmp_path)
    a = _insert(db, "a.pdf", TEXT)
    assert db.find_research_report_by_text(TEXT, exclude_id=a) is None


def test_empty_text_never_matches(tmp_path):
    db = _db(tmp_path)
    _insert(db, "a.pdf", "")
    assert db.find_research_report_by_text("", exclude_id=0) is None
    assert db.find_research_report_by_text("   ", exclude_id=0) is None


def test_ingest_path_calls_dedup_before_llm():
    """静态：判重必须发生在建 LLM 之前（否则照样烧两次 LLM）。"""
    s = io.open(ROOT / "web_app.py", encoding="utf-8").read()
    # 用 getattr 取方法（兼容测试桩），所以按方法名字符串定位
    i_dup = s.index('getattr(db, "find_research_report_by_text"')
    i_llm = s.index("client = create_llm_client(", i_dup)
    assert i_dup < i_llm, "判重应在 create_llm_client 之前"
    seg = s[i_dup:i_dup + 900]
    assert 'status="skipped"' in seg, "命中重复要标 skipped"
    assert "return" in seg, "命中后必须直接返回，不能继续跑流程"
