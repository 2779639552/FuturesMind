"""聚合 JSON 孤儿清扫(sweep_orphan_reports)单元测试。

覆盖:孤儿条目被剔除且其余保留、删空文件被移除、无孤儿不写盘、
非 *_research.json 文件不受影响。全部文件隔离到 tmp_path。
"""

import json

import tradingagents.dataflows.research_data as rd


def _write(variety, reports):
    p = rd.RESEARCH_DIR / f"{variety}_research.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"variety": variety, "reports": reports}, ensure_ascii=False), encoding="utf-8")
    return p


def _read(variety):
    return json.loads((rd.RESEARCH_DIR / f"{variety}_research.json").read_text(encoding="utf-8"))


def test_sweep_removes_orphans_keeps_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    _write("RB", [{"id": 1, "title": "keep"}, {"id": 99, "title": "ghost"}])
    _write("CU", [{"id": 2, "title": "keep2"}, {"id": 98, "title": "ghost2"}])

    removed = rd.sweep_orphan_reports({1, 2})

    assert removed == 2
    assert [r["id"] for r in _read("RB")["reports"]] == [1]
    assert [r["id"] for r in _read("CU")["reports"]] == [2]


def test_sweep_removes_empty_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    p = _write("TA", [{"id": 99, "title": "ghost-only"}])

    removed = rd.sweep_orphan_reports({1})

    assert removed == 1
    assert not p.exists()


def test_sweep_no_orphans_no_rewrite(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    p = _write("MA", [{"id": 1, "title": "ok"}])
    before = p.read_text(encoding="utf-8")

    removed = rd.sweep_orphan_reports({1})

    assert removed == 0
    assert p.read_text(encoding="utf-8") == before


def test_sweep_ignores_other_json(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    other = tmp_path / "MA_sentiment.json"
    other.write_text(json.dumps({"reports": [{"id": 99}]}), encoding="utf-8")

    removed = rd.sweep_orphan_reports({1})

    assert removed == 0
    assert other.exists()
