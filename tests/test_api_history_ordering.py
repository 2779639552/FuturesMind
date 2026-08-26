"""/api/history must order reports by modification time, not by filename.

Regression test for worklog 2026-08-25: `sorted(..., reverse=True)` sorted the
Path objects by their string form (=> by filename => by symbol), so a newly
persisted report for an alphabetically-low symbol (AP) sorted below old reports
and dropped out of the top-20 — the history tab looked like it had "no recent
reports" even though `_persist_analysis_report` was writing files fine.
"""

import os

import pytest

import web_app


@pytest.mark.unit
class TestApiHistoryOrdering:
    def test_newest_report_first_regardless_of_symbol(self, tmp_path, monkeypatch):
        monkeypatch.setattr(web_app, "REPORT_DIR", tmp_path)
        tmp_path.mkdir(parents=True, exist_ok=True)
        web_app.app.config["TESTING"] = True

        # An OLD report whose filename sorts ABOVE any "AP" file (R > A) but
        # whose mtime is far in the past. Filename-sort would put it first;
        # mtime-sort must put the fresh report first.
        old = tmp_path / "commodity_RB_20250101_000000.md"
        old.write_text("# old\n## Technical Analysis\nold content", encoding="utf-8")
        os.utime(old, (1_000_000, 1_000_000))

        # A NEW report (current mtime) via the real persist path.
        new = web_app._persist_analysis_report(
            "AP",
            "2026-08-25",
            {
                "technical_report": "fresh",
                "investment_plan": "RATING: 看多 | CONFIDENCE: 高 | SCORE: 8",
            },
            elapsed=1,
        )

        c = web_app.app.test_client()
        hist = c.get("/api/history").get_json()
        names = [h["filename"] for h in hist]
        assert names, "history list is empty"
        assert names[0] == os.path.basename(new), f"newest report not first: {names[:3]}"

    def test_comparison_files_still_filtered(self, tmp_path, monkeypatch):
        monkeypatch.setattr(web_app, "REPORT_DIR", tmp_path)
        tmp_path.mkdir(parents=True, exist_ok=True)
        web_app.app.config["TESTING"] = True

        (tmp_path / "commodity_RB_20250101_000000_comparison.md").write_text(
            "stub", encoding="utf-8"
        )
        web_app._persist_analysis_report(
            "RB",
            "2026-08-25",
            {"technical_report": "x", "investment_plan": "RATING: 中性 | CONFIDENCE: 低 | SCORE: 5"},
            elapsed=1,
        )

        c = web_app.app.test_client()
        hist = c.get("/api/history").get_json()
        assert not any(n.endswith("_comparison.md") for n in [h["filename"] for h in hist])
