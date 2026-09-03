"""机构(研报) vs 散户(社媒) 观点对比卡后端(web_app._build_group_compare)测试。

确定性纯算术:不联网、不调 LLM、不落真实库。研报目录(RESEARCH_DIR)与情绪数据目录
(_sentiment_dir)都隔离到 tmp_path,避免污染 ~/.tradingagents 真实数据。
"""

import json

import pytest

import tradingagents.dataflows.research_data as rd
import tradingagents.dataflows.sentiment_data as sdata
import web_app


@pytest.fixture
def iso_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    monkeypatch.setattr(sdata, "_sentiment_dir", lambda: tmp_path)
    rd._research_cache.clear()
    yield tmp_path
    rd._research_cache.clear()


def _write_research(path, reports):
    rd._research_cache.clear()
    rd._save_research(
        "RB",
        {"variety": "RB", "updated": "2026-09-03T00:00:00", "reports": reports},
    )


def _write_sentiment(path, bull=0.6, bear=0.2, neutral=0.2, posts=120):
    (path / "RB_sentiment.json").write_text(
        json.dumps(
            {
                "updated": "2026-09-03T00:00:00",
                "data": {
                    "social_sentiment": {
                        "total_posts_analyzed": posts,
                        "bullish_ratio": bull,
                        "bearish_ratio": bear,
                        "neutral_ratio": neutral,
                        "avg_score": 0.4,
                        "overall_sentiment_label": "偏多",
                        "trend_label": "回升",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _report(direction, title="研报", confidence=0.8):
    return {
        "id": 1,
        "title": title,
        "source": "测试来源",
        "uploaded_at": "2026-09-02",
        "direction": direction,
        "confidence": confidence,
        "conclusion": "结论摘要。",
    }


@pytest.mark.unit
class TestBuildGroupCompare:
    def test_both_unavailable_returns_false(self, iso_dirs):
        g = web_app._build_group_compare("RB")
        assert g == {"available": False}

    def test_resonance_when_institution_and_retail_both_bullish(self, iso_dirs):
        _write_research(iso_dirs, [_report("看多", "华泰看多"), _report("偏多", "东证看多")])
        _write_sentiment(iso_dirs, bull=0.6, bear=0.2, posts=120)
        g = web_app._build_group_compare("RB")
        assert g["available"] is True
        assert g["institution"]["present"] is True
        assert g["institution"]["count"] == 2
        assert g["institution"]["counts"] == {"bull": 2, "neutral": 0, "bear": 0}
        assert g["institution"]["net_dir"] == "看多"
        assert g["retail"]["present"] is True
        assert g["retail"]["dir"] == "看多"
        assert g["judgement"]["kind"] == "共振"
        assert "趋势相互强化" in g["judgement"]["detail"]

    def test_divergence_when_institution_bearish_retail_bullish(self, iso_dirs):
        _write_research(iso_dirs, [_report("看空", "空头研报")])
        _write_sentiment(iso_dirs, bull=0.6, bear=0.2, posts=80)
        g = web_app._build_group_compare("RB")
        assert g["judgement"]["kind"] == "背离"
        assert "方向相反" in g["judgement"]["detail"]
        assert g["institution"]["counts"]["bear"] == 1
        assert g["retail"]["dir"] == "看多"

    def test_differentiation_when_one_side_neutral(self, iso_dirs):
        # 机构看空 + 散户多空差 <5pp → 中性 → 分化
        _write_research(iso_dirs, [_report("看空")])
        _write_sentiment(iso_dirs, bull=0.52, bear=0.48, posts=40)
        g = web_app._build_group_compare("RB")
        assert g["retail"]["dir"] == "中性"
        assert g["judgement"]["kind"] == "分化"

    def test_one_sided_when_only_retail_present(self, iso_dirs):
        _write_sentiment(iso_dirs, bull=0.6, bear=0.2, posts=100)
        g = web_app._build_group_compare("RB")
        assert g["judgement"]["kind"] == "单边"
        assert "仅散户(社媒)" in g["judgement"]["detail"]
        assert g["institution"]["present"] is False

    def test_one_sided_when_only_institution_present(self, iso_dirs):
        _write_research(iso_dirs, [_report("看多")])
        g = web_app._build_group_compare("RB")
        assert g["judgement"]["kind"] == "单边"
        assert "仅机构(研报)" in g["judgement"]["detail"]
        assert g["retail"]["present"] is False

    def test_retail_items_capped_fields_shape(self, iso_dirs):
        reports = [_report("看空", f"研报{i}", confidence=0.5 + i * 0.1) for i in range(8)]
        _write_research(iso_dirs, reports)
        _write_sentiment(iso_dirs, bull=0.3, bear=0.6, posts=50)
        g = web_app._build_group_compare("RB")
        inst = g["institution"]
        assert inst["count"] == 8
        assert len(inst["items"]) == 6  # 只带前 6 条
        assert inst["conf_avg"]["bear"] is not None
        assert g["updated"]  # 研报有 updated → 非空


@pytest.mark.unit
class TestGroupCompareMarkdown:
    def test_markdown_table_rows(self, iso_dirs):
        _write_research(iso_dirs, [_report("看空", "空头研报")])
        _write_sentiment(iso_dirs, bull=0.6, bear=0.2, posts=80)
        md = web_app._group_compare_markdown(web_app._build_group_compare("RB"))
        assert md.startswith("**判定: 背离**")
        assert "| 群体 | 净方向 |" in md
        assert "| 机构(研报) |" in md
        assert "| 散户(社媒) |" in md
        assert "机构侧主要研报:" in md
        assert "空头研报" in md

    def test_markdown_unavailable_returns_empty(self):
        assert web_app._group_compare_markdown({"available": False}) == ""
        assert web_app._group_compare_markdown(None) == ""
