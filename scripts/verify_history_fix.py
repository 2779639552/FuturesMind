"""临时验证脚本:复现 Web 分析落盘修复的写盘→列表→章节解析全链路。

不调用 LLM,用假 final_state 直接调 web_app._persist_analysis_report,
再把 REPORT_DIR 指到临时目录,用 Flask test client 验证:
  1. /api/history 能列出新落盘的报告(不再停在 CLI 时代);
  2. *_comparison.md 被过滤,不占历史列表;
  3. /api/report/<file> 能把 CLI 风格标题(Debate Moderator Summary 等)解析出章节。
运行: cd AgentSense && venv/Scripts/python.exe scripts/verify_history_fix.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web_app  # noqa: E402

# 把报告目录重定向到临时目录,避免污染真实 ~/.tradingagents/logs
tmp = Path(tempfile.mkdtemp(prefix="verify_history_"))
web_app.REPORT_DIR = tmp
web_app.app.config["TESTING"] = True

fake_final = {
    "technical_report": "BIAS: 看多 | CONFIDENCE: 中\n均线多头排列,价格站上 20 日线。",
    "fundamental_report": "BIAS: 看多 | CONFIDENCE: 中\n基差走强,库存去化加速。",
    "macro_report": "BIAS: 中性 | CONFIDENCE: 低\nPMI 回升,宏观温和。",
    "sentiment_report": "BIAS: 偏多 | CONFIDENCE: 中\n社交情绪偏积极。",
    "discussion_summary": "## 辩论裁决\n**Winner**: bull\n### 共识点\n双方都认可库存拐点。",
    "investment_plan": "RATING: 看多 | CONFIDENCE: 高 | SCORE: 8\n建议多单持有。",
    "scenario_analysis": "乐观情景:目标价突破 3500。",
}

# ── 1. 落盘(Web 路径此前从不写盘,本次验证补上) ─────────────────────────
fpath = web_app._persist_analysis_report("RB", "2026-08-14", fake_final, elapsed=123)
assert fpath.exists(), "落盘文件未生成"
print("[1] 落盘 OK:", fpath.name)

# 放一个 _comparison.md 占位文件,验证过滤
comp = tmp / "commodity_RB_20260720_000000_comparison.md"
comp.write_text("comparison stub", encoding="utf-8")

# ── 2. /api/history ─────────────────────────────────────────────────────
c = web_app.app.test_client()
r = c.get("/api/history")
hist = r.get_json()
names = [h["filename"] for h in hist]
assert names, "历史列表为空"
assert fpath.name in names, f"新落盘报告未出现在历史列表: {names[:5]}"
assert not any(n.endswith("_comparison.md") for n in names), "comparison 文件未被过滤"
assert names[0] == fpath.name, "最新报告未排在最前"
print("[2] /api/history OK —— 新报告已出现且排最前,comparison 已过滤,共", len(names), "条")
print("    最新 3 条:", names[:3])

# ── 3. /api/report 章节解析(含 CLI 风格后缀标题) ────────────────────────
r = c.get("/api/report/" + fpath.name)
d = r.get_json()
secs = d.get("sections", {})
assert "Technical Analysis" in secs, "缺少 Technical Analysis 章节"
assert "Fundamental Analysis" in secs, "缺少 Fundamental Analysis 章节"
assert "Macro/News Analysis" in secs, "缺少 Macro/News Analysis 章节"
assert "Sentiment Analysis" in secs, "缺少 Sentiment Analysis 章节"
assert "Debate Moderator" in secs, "Debate Moderator(后缀 Summary)未解析出章节"
assert "Synthesis" in secs, "Synthesis(后缀 & Recommendation)未解析出章节"
assert "Scenario" in secs, "Scenario 章节未解析出"
assert d.get("rating") and d["rating"]["score"] == 8, "RATING 未从投资计划中提取"
print("[3] /api/report OK —— 7 个章节全部解析,rating =", d["rating"])

# 清理临时目录
import shutil

shutil.rmtree(tmp)
print("\nALL PASS ✅")
