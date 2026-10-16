"""测试头寸建议列:LLM 概括优先 + 回退带来源。"""
import sys

sys.path.insert(0, r"D:\AgentSense\AgentSense")
import web_app

llm_body_6col = """## 一、当日观点总表

| 品种 | 发行方 | 方向 | 置信度 | 一句话核心观点 | 头寸建议 |
|---|---|---|---|---|---|
| SC(原油) | 华泰/国泰 | 看多/中性 | 60%/50% | 地缘支撑与需求抑制博弈。 | 华泰建议轻仓做多而国泰建议观望 |
| M(豆粕) | 华泰 | 看多 | 75% | 出口改善支撑。 | 两家一致建议多配 10%~20% |

## 二、观点对比与冲突

- SC原油：华泰看多vs国泰中性，分歧在地缘权重。
"""

views, trades = web_app._parse_llm_views_table(llm_body_6col)
print("views:", views)
print("trades:", trades)
assert views["SC"] == "地缘支撑与需求抑制博弈。"
assert trades["SC"] == "华泰建议轻仓做多而国泰建议观望"
assert trades["M"] == "两家一致建议多配 10%~20%"

# 5 列旧格式
llm_body_5col = """## 一、当日观点总表

| 品种 | 发行方 | 方向 | 置信度 | 一句话核心观点 |
|---|---|---|---|---|
| CU(铜) | 国泰 | 中性 | 50% | 供需两淡。 |
"""
v5, t5 = web_app._parse_llm_views_table(llm_body_5col)
assert v5["CU"] == "供需两淡。" and "CU" not in t5
print("5 列旧格式兼容 OK")

# _build_daily_views_table: LLM 头寸优先
items = [
    {"variety": "SC(原油)", "source": "华泰期货", "direction": "看多", "confidence": "60%",
     "opinion": "地缘支撑", "trade": "轻仓做多", "report_id": 1, "title": "原油"},
    {"variety": "SC(原油)", "source": "国泰君安", "direction": "中性", "confidence": "50%",
     "opinion": "高位震荡", "trade": "观望", "report_id": 2, "title": "原油2"},
    {"variety": "M(豆粕)", "source": "华泰", "direction": "看多", "confidence": "75%",
     "opinion": "出口改善", "trade": "多配 10%~20%", "report_id": 3, "title": "豆粕"},
]
tbl = web_app._build_daily_views_table("2026-09-11", items, "", llm_views=views, llm_trades=trades)
sc = [l for l in tbl.split("\n") if l.startswith("| SC ")][0]
print("\nSC 行:", sc)
assert "华泰建议轻仓做多而国泰建议观望" in sc, "LLM 头寸未生效"
# M 品种 llm_trades 里有
m_row = [l for l in tbl.split("\n") if l.startswith("| M ")][0]
assert "多配 10%~20%" in m_row

# 回退:无 llm_trades 的品种(CU 不在 items;用新构造)
items2 = [dict(items[0]), dict(items[1])]  # 只有 SC
tbl2 = web_app._build_daily_views_table("2026-09-11", items2, "", llm_views={}, llm_trades={})
sc2 = [l for l in tbl2.split("\n") if l.startswith("| SC ")][0]
print("回退 SC 行:", sc2)
assert "华泰期货:" in sc2 and "国泰君安:" in sc2, "回退应带来源"
assert "；" not in sc2.split("|")[7], "回退不应含分号"

print("\n全部断言 PASS")
