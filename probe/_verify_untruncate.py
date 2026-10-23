# -*- coding: utf-8 -*-
"""验证：总表不再截断，且关键清洗仍在。

【为什么用构造数据而不是真数据】这是**确定性**判据：
  · 判"有没有被截断"只需要一个足够长的输入 ⇒ 构造即可，比"等真数据里恰好有长观点"可靠；
  · 判"清洗有没有丢"更需要构造（真数据里未必恰好含 `|` 与换行）。
真数据的端到端在最后单独跑一次（看 LU 行的真实完整内容）。
"""
import importlib
import os
import sys
from pathlib import Path

os.environ.setdefault("AGENTSENSE_AUTH_DISABLE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
wa = importlib.import_module("web_app")

PASS = 0
FAIL = 0


def ck(ok, label, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  ✔ %s%s" % (label, ("  [%s]" % extra) if extra else ""))
    else:
        FAIL += 1
        print("  ★ FAIL %s%s" % (label, ("  [%s]" % extra) if extra else ""))


LONG = ("综述：LU价差多配看多，单边震荡。；"
        "利空：外盘低硫基差走弱(逻辑：外盘现货转弱、新加坡库存回升)；"
        "利多：柴油上行支撑估值(逻辑：柴油裂解价差高位、炼厂降负)；"
        "风险提示：油价大幅波动与地缘事件可能使判断失效。" * 2)   # 约 200+ 字

ITEMS = [{
    "variety": "LU(低硫燃料油)",
    "source": "永安期货",
    "direction": "看多",
    "confidence": "58%",
    "opinion": LONG,
    "trade": "区间震荡；逢低分批；" * 12,     # 也超长
    "merged_count": 1,
}]

print("=" * 76)
print("  总表不再截断 · 确定性验证")
print("=" * 76)
print("  构造 opinion 长度: %d 字；trade 长度: %d 字"
      % (len(LONG), len(ITEMS[0]["trade"])))
print()

# ── ① 回退路径（LLM 观点缺失 ⇒ 走 items 拼接）
md = wa._build_daily_views_table("2026-10-11", ITEMS, "周报", llm_views={}, llm_trades={}, all_rows=[])
row = next((ln for ln in md.split("\n") if ln.startswith("| LU")), "")
print("  LU 行长度: %d 字符" % len(row))
ck(bool(row), "总表里生成了 LU 行")
ck("…" not in row, "★ 行内**没有**省略号（不再截断）", "含…" if "…" in row else "无…")
ck(LONG[:120] in row, "★ 长观点的**前 120 字完整保留**")
ck(LONG[-40:] in row, "★★ 长观点的**结尾也在**（没被砍掉）")
ck(len(row) > 200, "行长度 > 200 字符（证明确实没截）", str(len(row)))

# ── ② 清洗仍在（这是"能删/不能删"的分界）
DANGER = ("第一段\n第二段"          # 换行：不换成空格会把 markdown 表劈成两行
          "含竖线|的文本"           # 竖线：markdown 单元格分隔符
          "结尾")
md2 = wa._build_daily_views_table(
    "2026-10-11",
    [{"variety": "TA(PTA)", "source": "X", "direction": "看多", "confidence": "60%",
      "opinion": DANGER, "trade": DANGER, "merged_count": 1}],
    "周报", llm_views={}, llm_trades={}, all_rows=[])
row2 = next((ln for ln in md2.split("\n") if ln.startswith("| TA")), "")
print()
print("  TA 行: %s" % row2[:160])
# 行内换行必须已被清掉（否则这一行会被劈开，表格列数错乱）
ck("\n" not in row2, "★ 行内无换行（否则 markdown 表会被劈开）")
ck(row2.count("|") == 10, "★ 表格列数正确（9 列 = 10 个竖线）",
  "竖线 %d 个" % row2.count("|"))
ck("含竖线/的文本" in row2, "★ 内容里的竖线被替换成 /（否则后续列全错位）")

# ── ③ LLM 路径（llm_views 很长时也不截）
md3 = wa._build_daily_views_table("2026-10-11", ITEMS, "周报",
                                 llm_views={"LU": LONG}, llm_trades={"LU": "很长" * 40},
                                 all_rows=[])
row3 = next((ln for ln in md3.split("\n") if ln.startswith("| LU")), "")
ck("…" not in row3, "★ LLM 路径同样不截断")
ck(LONG[-40:] in row3, "★ LLM 观点结尾完整")
ck(("很长" * 40) in row3, "★ LLM 头寸建议完整")

# ── ④ 缺失值仍显示 —（不能被"不截断"连带改坏）
md4 = wa._build_daily_views_table("2026-10-11",
                                  [{"variety": "M(豆粕)", "source": "X", "direction": "中性",
                                    "confidence": "—", "opinion": "", "trade": "",
                                    "merged_count": 1}],
                                  "周报", llm_views={}, llm_trades={}, all_rows=[])
row4 = next((ln for ln in md4.split("\n") if ln.startswith("| M ")), "")
print()
print("  M 行: %s" % row4[:140])
ck(row4.count("|") == 10, "★ 空观点时列数仍正确")
ck("—" in row4, "★ 空值仍是「—」（没变成空串/null）")

print()
print("=" * 76)
print("  PASS %d / FAIL %d" % (PASS, FAIL))
print("=" * 76)
sys.exit(1 if FAIL else 0)
