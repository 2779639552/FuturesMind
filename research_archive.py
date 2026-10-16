"""研报归档增量任务(2026-09-28): 每日定时把"新过期(>14天)"研报总结化归档。

【为什么需要】09-24 已落地归档方案 B(每日总结裁剪卡片), 但只能手动跑 tools 脚本;
每天都有新的超过 14 天的 done 研报, 不自动归档会积压且 RAG 里原文块不清理。

【三步(增量)】
  0. 缺失日期烧 LLM: 对归档日期内"有 done 研报但无 research_daily/{date}.md"的日期
     调 web_app._generate_daily_summary(date) 生成每日总结(方案 B 的缺失日统一生成);
  1. build: 按 (日期×品种) 生成总结(每日总结裁剪 + conclusion 兜底), upsert 覆盖
     —— build 只查 status='done' 的旧行, 已归档(summarized)行不重复处理 ⇒ 天然增量;
  2. index --since=今天: 只把今天新建的组进 RAG(chunk id sum_{key}_{i} 唯一);
  3. soft-delete --since=今天: 只软删今天新建组覆盖的原研报行(done→summarized)。

【实现】复用 tools/ 下的归档脚本逻辑(import, 不复制), tools 目录路径集中在此一处。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── tools 归档脚本目录(集中一处, 换机/迁移时只需改这里) ──
_TOOLS_DIR = r"C:\Users\lenovo\WorkBuddy\2026-09-07-16-38-57\tools"
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from database import get_db  # noqa: E402

_DAILY_DIR = Path(os.path.expanduser("~/.tradingagents/research_daily"))


def _ensure_missing_daily_summaries(days_back: int = 14, dry_run: bool = False) -> list[str]:
    """归档日期内"有 done 研报但无每日总结文件"的日期 → 调 _generate_daily_summary 生成。

    【方案 B 缺失日统一生成】有研报却没总结文件(如某日用户没打开过每日总结卡),
    归档会退化成 conclusion 兜底; 这里先补烧一次 LLM 生成全天总结, 后续 build 走
    每日总结裁剪(质量更高)。失败不中断(降级 conclusion 兜底)。
    【返回】已生成的日期列表。
    """
    import sqlite3

    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    con = sqlite3.connect(os.path.expanduser("~/.tradingagents/agentsense.db"), timeout=30)
    dates = {
        str(r[0])[:10]
        for r in con.execute(
            "SELECT DISTINCT publish_date FROM research_reports "
            "WHERE status='done' AND publish_date<? AND publish_date!=''",
            (cutoff,),
        )
    }
    made = []
    for d in sorted(dates):
        if (d >= cutoff) or not (d < cutoff):
            pass
        f = _DAILY_DIR / f"{d}.md"
        if f.is_file():
            continue
        if dry_run:
            continue
        try:
            from web_app import _generate_daily_summary  # 延迟 import 防循环
            r = _generate_daily_summary(d)
            if r.get("ok"):
                made.append(d)
        except Exception:  # noqa: BLE001  单日失败不中断, 降级 conclusion 兜底
            continue
    return made


def archive_new(days_back: int = 14, dry_run: bool = False) -> dict:
    """执行一次增量归档(三步)。返回统计 dict。"""
    import build_research_summaries as _build
    import index_research_summaries as _index

    today = datetime.now().strftime("%Y-%m-%d")

    # 0) 缺失每日总结 → 烧 LLM 补齐
    filled = _ensure_missing_daily_summaries(days_back=days_back, dry_run=dry_run)

    # 1) build(天然增量: 只处理 status='done' 的旧行)
    build_st = _build.build_all(days_back=days_back, dry_run=dry_run)

    # 2) index 增量(只处理今天新建组) + 3) soft-delete 增量
    index_st = _index.index_all(dry_run=dry_run, rebuild=False, since=today)
    soft_st = _index.soft_delete_originals(dry_run=dry_run, since=today)

    return {
        "cutoff": build_st.get("cutoff"),
        "missing_daily_filled": filled,
        "build": build_st,
        "index": index_st,
        "soft_delete": soft_st,
    }


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="研报归档增量任务")
    ap.add_argument("--days-back", type=int, default=14)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(json.dumps(archive_new(days_back=args.days_back, dry_run=args.dry_run), ensure_ascii=False))
