"""运行分析「模块可选」(2026-09-03):后端解析与阶段过滤 + 持久化跳过说明。

确定性纯逻辑测试:不联网、不调 LLM。重点覆盖:
· resolve_run_options —— include_sentiment auto/include/exclude + 辩论/综合/情景三布尔 + 强制规则;
· stages_for_run —— 未启用模块对应阶段从 PIPELINE_STAGES 正确剔除;
· _persist_analysis_report —— modules_note 非空时文件头写"本次运行跳过模块",空则不写。
"""

import pytest

import web_app


@pytest.fixture
def patch_sentiment_auto(monkeypatch):
    # auto 判定与真实数据目录无关,统一桩掉,保证结果确定。
    monkeypatch.setattr(web_app, "should_include_sentiment", lambda symbol: True)


@pytest.mark.unit
class TestAsBool:
    def test_truthy_and_falsy(self):
        assert web_app._as_bool(True, default=True) is True
        assert web_app._as_bool(False, default=True) is False
        assert web_app._as_bool(1, default=True) is True
        assert web_app._as_bool(0, default=True) is False

    def test_string_forms(self):
        for v in ("true", "TRUE", "1", "yes", "on"):
            assert web_app._as_bool(v, default=False) is True, v
        for v in ("false", "FALSE", "0", "no", "off"):
            assert web_app._as_bool(v, default=True) is False, v

    def test_unparsable_returns_default(self):
        assert web_app._as_bool("weird", default=True) is True
        assert web_app._as_bool(None, default=False) is False


@pytest.mark.unit
class TestResolveRunOptions:
    def test_defaults_full_when_no_module_fields(self, patch_sentiment_auto):
        opts = web_app.resolve_run_options("RB", {})
        assert opts == {
            "include_sentiment": True,
            "include_debate": True,
            "include_synthesis": True,
            "include_scenario": True,
        }

    def test_sentiment_exclude(self):
        opts = web_app.resolve_run_options("RB", {"include_sentiment": "exclude"})
        assert opts["include_sentiment"] is False
        assert opts["include_debate"] is True

    def test_sentiment_include_forces_true(self):
        opts = web_app.resolve_run_options("RB", {"include_sentiment": "include"})
        assert opts["include_sentiment"] is True

    def test_synthesis_off_forces_debate_and_scenario_off(self):
        opts = web_app.resolve_run_options(
            "RB",
            {"include_debate": True, "include_synthesis": False, "include_scenario": True},
        )
        assert opts["include_synthesis"] is False
        assert opts["include_debate"] is False  # 连锁关闭
        assert opts["include_scenario"] is False

    def test_scenario_off_keeps_debate(self):
        opts = web_app.resolve_run_options(
            "RB", {"include_debate": True, "include_scenario": False}
        )
        assert opts["include_debate"] is True
        assert opts["include_synthesis"] is True
        assert opts["include_scenario"] is False

    def test_string_booleans_accepted(self, patch_sentiment_auto):
        opts = web_app.resolve_run_options(
            "RB", {"include_debate": "false", "include_scenario": "false"}
        )
        assert opts["include_debate"] is False
        assert opts["include_scenario"] is False
        assert opts["include_synthesis"] is True


@pytest.mark.unit
class TestStagesForRun:
    def _ids(self, opts):
        return [s["id"] for s in web_app.stages_for_run(opts)]

    def test_full_includes_all_ten(self, patch_sentiment_auto):
        opts = web_app.resolve_run_options("RB", {})
        assert self._ids(opts) == [s["id"] for s in web_app.PIPELINE_STAGES]

    def test_no_sentiment_drops_sentiment_only(self):
        opts = web_app.resolve_run_options("RB", {"include_sentiment": "exclude"})
        ids = self._ids(opts)
        assert "sentiment" not in ids
        assert "moderator" in ids  # 辩论仍在
        assert "synthesis" in ids and "scenario" in ids

    def test_skip_debate_drops_four_debate_stages(self, patch_sentiment_auto):
        opts = web_app.resolve_run_options("RB", {"include_debate": False})
        ids = self._ids(opts)
        for sid in ("bull_opening", "bear_refute", "bull_rebuttal", "moderator"):
            assert sid not in ids
        assert "synthesis" in ids and "scenario" in ids

    def test_skip_scenario_drops_scenario_only(self, patch_sentiment_auto):
        opts = web_app.resolve_run_options("RB", {"include_scenario": False})
        ids = self._ids(opts)
        assert "scenario" not in ids
        assert "moderator" in ids and "synthesis" in ids

    def test_skip_synthesis_drops_to_analysts_only(self, patch_sentiment_auto):
        opts = web_app.resolve_run_options("RB", {"include_synthesis": False})
        ids = self._ids(opts)
        assert set(ids) == {"technical", "fundamental", "macro", "sentiment"}


@pytest.mark.unit
class TestPersistModulesNote:
    def test_note_written_when_modules_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(web_app, "REPORT_DIR", tmp_path)
        fpath = web_app._persist_analysis_report(
            "RB",
            "2026-09-03",
            {"technical_report": "t", "fundamental_report": "f"},
            elapsed=1,
            modules_note="辩论对抗、情景分析",
        )
        text = tmp_path.joinpath(fpath).read_text(encoding="utf-8")
        assert "> 本次运行跳过模块: 辩论对抗、情景分析" in text

    def test_no_note_when_full_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(web_app, "REPORT_DIR", tmp_path)
        fpath = web_app._persist_analysis_report(
            "RB",
            "2026-09-03",
            {"technical_report": "t"},
            elapsed=1,
            modules_note="",
        )
        text = tmp_path.joinpath(fpath).read_text(encoding="utf-8")
        assert "本次运行跳过模块" not in text
