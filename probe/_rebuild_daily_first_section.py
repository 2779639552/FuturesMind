# -*- coding: utf-8 -*-
"""重建每日总结的**第一节「当日观点总表」**（不重跑 LLM，二~四节原样保留）。

为什么要单独做：总表是**生成时**由代码写进 md 的，改完 `_prev_variety_view` 不会影响
已经落盘的近几日总结 —— 用户打开还是看不到永安。

做法：读旧 md 第一节表格，把「一句话核心观点」「头寸建议」两列原样取出来当 llm_views/
     llm_trades 传回生成器（保证 LLM 提炼的措辞不变），只有发行方/方向/延续文案按新逻辑重算。
     这样既零 LLM 成本、又不会让历史总结的文字漂移。

用法：python probe/_rebuild_daily_first_section.py [--write]
       默认 dry-run，只打印变化；--write 才落盘（会先备份到 backup/）。
"""
from __future__ import annotations

import io
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import web_app as W  # noqa: E402

DATES = ("2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21")
DAILY = Path.home() / ".tradingagents" / "research_daily"
BACKUP = Path(r"D:\AgentSense\AgentSense\backup")


def _parse_first_section(md: str):
    """返回 (第一节标题行, 表格区, 二节起点) 与 code→(观点, 头寸)。"""
    m = re.search(r"(## 一、[^\n]*\n)(.*?)(?=\n## 二、)", md, re.S)
    if not m:
        return None, None, None, {}, {}
    block = m.group(2)
    views, trades = {}, {}
    for line in block.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 8:
            continue
        code = cells[0].upper()
        if not re.fullmatch(r"[A-Z]{1,3}", code):
            continue
        v = cells[5]
        # 【2026-10-21】观点列现在可能已是"每家一段（机构: 观点 ; 机构: 观点）"的代码产物，
        #   原样回灌会变成 "华泰: 华泰: …"（二次加前缀）或把多家的句子挂给单一机构。
        #   ⇒ 多段直接丢弃（让生成器按 items 重算）；单段剥掉前缀。
        if " ; " in v or "（延续" in v:
            continue
        v = re.sub(r"^[^:：]{2,12}[:：]\s*", "", v.strip())
        views[code] = v
        trades[code] = cells[6]
    return m.group(1), block, m.start(), views, trades


def main() -> int:
    write = "--write" in sys.argv
    db = W.get_db()
    rows = db.list_reports_for_aggregate() if hasattr(db, "list_reports_for_aggregate") else db.list_research_reports(limit=5000)

    for date in DATES:
        p = DAILY / f"{date}.md"
        if not p.exists():
            print(f"{date}: 文件不存在，跳过")
            continue
        md = io.open(p, encoding="utf-8").read()
        head, block, start, views, trades = _parse_first_section(md)
        if head is None:
            print(f"{date}: 找不到第一节（结构变化？），跳过")
            continue

        items = W._collect_daily_report_items(rows, date, "")
        new_block_full = W._build_daily_views_table(date, items, "", views, trades, rows)
        new_body = new_block_full.split("\n", 1)[1] if "\n" in new_block_full else ""

        # 变化摘要：把旧/新的 BR 行都打印出来
        def line_of(text: str, code: str):
            for ln in text.splitlines():
                if ln.startswith(f"| {code} |"):
                    return ln
            return "(无该品种行)"

        changed = []
        old_lines = [ln for ln in block.splitlines() if ln.startswith("| ")]
        new_lines = [ln for ln in new_body.splitlines() if ln.startswith("| ")]
        for o, n in zip(old_lines, new_lines):
            if o != n:
                changed.append((o, n))

        print(f"\n=== {date}: 条目 {len(items)}，变化行 {len(changed)} ===")
        br_o, br_n = line_of(block, "BR"), line_of(new_body, "BR")
        if br_o != br_n:
            print("  BR 旧:", br_o[:160])
            print("  BR 新:", br_n[:160])

        if not write:
            continue

        BACKUP.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, BACKUP / f"{date}.md.bak_before_prev_fallback")
        out = md[:start] + new_block_full + "\n" + md[start + len(head) + len(block):]
        io.open(p, "w", encoding="utf-8", newline="").write(out)
        print("  已写回（备份 backup/%s.md.bak_before_prev_fallback）" % date)

    if not write:
        print("\n(dry-run：未写盘；加 --write 才落盘)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
