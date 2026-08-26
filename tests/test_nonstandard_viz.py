"""非标数据可视化聚合纯函数测试(2026-08-26)。

覆盖 _build_variety_platform_graph(品种-平台-板块关系图) 与
_build_sentiment_sankey(平台→品种→多/空 桑基)两个纯函数:
节点/边构造、Top N 过滤、品种级情感优先与整帖情感退化路径、方向阈值。
不依赖文件系统(直接喂合成 records),也不触发 Flask app 运行。
"""

import pytest

from web_app import (
    _build_sentiment_sankey,
    _build_variety_platform_graph,
    _sent_dir,
)


def _rec(nid, platform, varieties, sentiment_score=0.0, variety_sentiments=None):
    """构造一条最小 batch 记录(字段与 batch_collect 输出对齐)。"""
    return {
        "note_id": nid,
        "platform": platform,
        "varieties": [
            {"name": v, "sector": f"sector-{v}", "exchange": "上期所", "matched": v}
            for v in varieties
        ],
        "sentiment_score": sentiment_score,
        "variety_sentiments": variety_sentiments,
    }


@pytest.mark.unit
class TestVarietyPlatformGraph:
    def test_nodes_and_links_shape(self):
        recs = [
            _rec("w1", "weibo", ["螺纹钢", "焦炭"], 0.2),
            _rec("w2", "weibo", ["螺纹钢"], -0.1),
            _rec("x1", "xhs", ["螺纹钢"], 0.0),
            _rec("z1", "zhihu", ["豆粕"], 0.3),
        ]
        g = _build_variety_platform_graph(recs)
        assert {n["type"] for n in g["nodes"]} == {"platform", "variety", "sector"}
        # 平台 / 品种 / 板块 节点集合
        plats = {n["name"] for n in g["nodes"] if n["type"] == "platform"}
        vars_ = {n["name"] for n in g["nodes"] if n["type"] == "variety"}
        secs = {n["name"] for n in g["nodes"] if n["type"] == "sector"}
        assert plats == {"weibo", "xhs", "zhihu"}
        assert vars_ == {"螺纹钢", "焦炭", "豆粕"}
        assert secs == {"sector-螺纹钢", "sector-焦炭", "sector-豆粕"}
        # 品种节点 value = 帖数
        posts = {n["name"]: n["value"] for n in g["nodes"] if n["type"] == "variety"}
        assert posts == {"螺纹钢": 3, "焦炭": 1, "豆粕": 1}
        # 边: 品种↔平台共现 4 条 + 品种→板块 3 条
        vp = [l for l in g["links"] if l["source"] in vars_ and l["target"] in plats]
        vs = [l for l in g["links"] if l["source"] in vars_ and l["target"] in secs]
        assert len(vp) == 4
        assert len(vs) == 3

    def test_link_values_platform_variety(self):
        recs = [
            _rec("w1", "weibo", ["螺纹钢"]),
            _rec("w2", "weibo", ["螺纹钢"]),
            _rec("x1", "xhs", ["螺纹钢"]),
        ]
        g = _build_variety_platform_graph(recs)
        vp = {
            (l["source"], l["target"]): l["value"]
            for l in g["links"]
            if l["target"] in {"weibo", "xhs"}
        }
        assert vp[("螺纹钢", "weibo")] == 2
        assert vp[("螺纹钢", "xhs")] == 1

    def test_top_varieties_limit(self):
        recs = [_rec(f"w{i}", "weibo", [f"v{i}"]) for i in range(5)]
        g = _build_variety_platform_graph(recs, top_varieties=3)
        vars_ = [n for n in g["nodes"] if n["type"] == "variety"]
        assert len(vars_) == 3
        # 品种→板块边也只保留 top 品种(target 为板块节点,共 top 个)
        sec_links = [l for l in g["links"] if l["target"].startswith("sector-")]
        assert len(sec_links) == 3

    def test_sector_null_falls_back_to_other(self):
        # sector 字段存在但为 null → 兜底"其他",不产出 None 节点
        recs = [
            {
                "note_id": "w1",
                "platform": "weibo",
                "varieties": [{"name": "螺纹钢", "sector": None, "exchange": "上期所"}],
                "sentiment_score": 0.0,
                "variety_sentiments": None,
            }
        ]
        g = _build_variety_platform_graph(recs)
        secs = [n for n in g["nodes"] if n["type"] == "sector"]
        assert len(secs) == 1
        assert secs[0]["name"] == "其他"
        assert all(n["name"] is not None for n in g["nodes"])

    def test_varieties_null_no_crash(self):
        # "varieties": null → 不崩,该记录只计平台、无品种/板块节点
        recs = [
            {"note_id": "n1", "platform": "weibo", "varieties": None, "sentiment_score": 0.0}
        ]
        g = _build_variety_platform_graph(recs)
        assert {n["type"] for n in g["nodes"]} == {"platform"}
        assert len(g["links"]) == 0

    def test_varieties_string_list_no_crash(self):
        # varieties 是字符串列表(非 dict)→ 非 dict 元素被跳过,不 AttributeError
        recs = [
            {
                "note_id": "n1", "platform": "weibo",
                "varieties": ["螺纹钢", "焦炭"], "sentiment_score": 0.0,
            }
        ]
        g = _build_variety_platform_graph(recs)
        assert [n for n in g["nodes"] if n["type"] == "variety"] == []


@pytest.mark.unit
class TestSentimentSankey:
    def test_flow_three_layers(self):
        recs = [
            _rec("w1", "weibo", ["螺纹钢"], variety_sentiments=[{"variety": "螺纹钢", "score": 0.3}]),
            _rec("w2", "weibo", ["螺纹钢"], variety_sentiments=[{"variety": "螺纹钢", "score": -0.2}]),
            _rec("x1", "xhs", ["螺纹钢"], variety_sentiments=[{"variety": "螺纹钢", "score": 0.05}]),
        ]
        s = _build_sentiment_sankey(recs)
        names = [n["name"] for n in s["nodes"]]
        assert set(names) == {"weibo", "xhs", "螺纹钢", "看多", "看空", "中性"}
        idx = {n: i for i, n in enumerate(names)}

        def link_value(src, tgt):
            return sum(
                l["value"]
                for l in s["links"]
                if l["source"] == idx[src] and l["target"] == idx[tgt]
            )

        # 3 帖 × 每帖两段(平台→品种、品种→方向)= 6 条边
        assert len(s["links"]) == 6
        assert link_value("weibo", "螺纹钢") == 2
        assert link_value("xhs", "螺纹钢") == 1
        assert link_value("螺纹钢", "看多") == 1
        assert link_value("螺纹钢", "看空") == 1
        assert link_value("螺纹钢", "中性") == 1

    def test_fallback_to_post_sentiment(self):
        # 无 variety_sentiments → 退到整帖 sentiment_score 挂 varieties 首个品种
        recs = [_rec("w1", "weibo", ["螺纹钢", "焦炭"], sentiment_score=-0.5)]
        s = _build_sentiment_sankey(recs)
        names = [n["name"] for n in s["nodes"]]
        assert "看空" in names
        assert "焦炭" not in names  # 退路只取首个品种
        idx = {n: i for i, n in enumerate(names)}
        into_v = sum(
            l["value"] for l in s["links"] if l["target"] == idx["螺纹钢"]
        )
        assert into_v == 1

    def test_top_varieties_limit(self):
        recs = [
            _rec(f"w{i}", "weibo", [f"v{i}"], variety_sentiments=[{"variety": f"v{i}", "score": 0.2}])
            for i in range(5)
        ]
        s = _build_sentiment_sankey(recs, top_varieties=2)
        names = [n["name"] for n in s["nodes"]]
        varieties = [n for n in names if n.startswith("v")]
        assert len(varieties) == 2

    def test_sent_dir_threshold(self):
        assert _sent_dir(0.2) == "看多"
        assert _sent_dir(-0.2) == "看空"
        assert _sent_dir(0.05) == "中性"
        assert _sent_dir(0.1) == "中性"  # 边界: 严格大于 0.1 才算多

    def test_null_score_is_neutral(self):
        # variety_sentiments score 为 null → 计为中性,不抛 TypeError
        recs = [
            _rec(
                "w1",
                "weibo",
                ["螺纹钢"],
                variety_sentiments=[{"variety": "螺纹钢", "score": None}],
            )
        ]
        s = _build_sentiment_sankey(recs)
        names = [n["name"] for n in s["nodes"]]
        assert "中性" in names
        # 方向节点是固定三态(恒存在),但 score=null 的流量必须全进中性
        idx = {n: i for i, n in enumerate(names)}
        bull = sum(l["value"] for l in s["links"] if l["target"] == idx["看多"])
        bear = sum(l["value"] for l in s["links"] if l["target"] == idx["看空"])
        assert bull == 0 and bear == 0

    def test_varieties_null_sankey_no_crash(self):
        # "varieties": null → 退路不崩,无流量
        recs = [
            {"note_id": "n1", "platform": "weibo", "varieties": None, "sentiment_score": 0.3}
        ]
        s = _build_sentiment_sankey(recs)
        assert s["links"] == []

    def test_string_score_no_crash(self):
        # score 是字符串 → _sent_dir 数值化兜底,不 TypeError
        recs = [
            {
                "note_id": "n1", "platform": "weibo",
                "varieties": [{"name": "螺纹钢", "sector": "黑色系"}],
                "variety_sentiments": [{"variety": "螺纹钢", "score": "0.5"}],
            }
        ]
        s = _build_sentiment_sankey(recs)
        names = [n["name"] for n in s["nodes"]]
        idx = {n: i for i, n in enumerate(names)}
        bull = sum(l["value"] for l in s["links"] if l["target"] == idx["看多"])
        assert bull == 1
