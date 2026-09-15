"""backfill_fx_text.py — 回填已接入的 fxbaogao 研报全文(两阶段)。

【背景】旧 fetch_detail 抓 SSR 空壳只拿到"摘要"标签页;新 fetch_detail 走
_next/data JSON 拿 content 全文。本脚本把库里已接入的 fxbaogao 研报(文件名
以数字 docId 开头的 .md)重新抓全文。

【两阶段】
  --stage text   仅刷新 extracted_text + 重写 .md(无 LLM,快,直接解决"查看
                 原文"看到表面标签页的问题)。
  --stage llm    在 text 阶段之后,重跑 _process_research_report 刷新结构化
                 数据/结论 + 聚合 JSON(消耗 LLM,较慢)。
  --stage all    两阶段都做。

【识别】file_path 以 数字docId_ 开头且为 .md 即 fxbaogao 自动接入。
【使用】python backfill_fx_text.py --stage text
       python backfill_fx_text.py --stage all
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import research_collector as rc

# 断点续跑:记录已 LLM 重提取的 report id,被杀后可继续
_LLM_DONE = Path("_backfill_llm_done.json")

DB = sqlite3.connect(r"C:\Users\19168\.tradingagents\agentsense.db")
DB.row_factory = sqlite3.Row


def fx_reports():
    out = []
    for r in DB.execute("SELECT id, source, file_path FROM research_reports"):
        fp = r["file_path"] or ""
        fn = fp.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        if fn.endswith(".md") and re.match(r"^\d+_", fn):
            m = re.match(r"(\d+)_", fn)
            out.append((r["id"], int(m.group(1)), fp))
    return out


def stage_text():
    print(f"[text] fxbaogao reports to refresh: {len(fx_reports())}", flush=True)
    ok = skip = 0
    for rid, doc_id, fpath in fx_reports():
        d = rc.fetch_detail(doc_id)
        text = (d.get("text") or "").strip()
        if not text:
            print(f"  ! id={rid} docId={doc_id} 新抓文本为空(死链?),跳过", flush=True)
            skip += 1
            continue
        # 更新 DB 正文(表无独立日期列,报告日期仅写入 .md 文件头展示)
        DB.execute(
            "UPDATE research_reports SET extracted_text=? WHERE id=?",
            (text[:20000], rid),
        )
        DB.commit()
        # 重写 .md(前端"查看原文"直接读此文件)
        p = Path(fpath)
        if p.exists():
            md = (
                f"# {d.get('title') or p.stem}\n"
                f"- 来源: 发现报告\n"
                f"- 链接: {rc.BASE_URL}/detail/{doc_id}\n"
                f"- 日期: {d.get('date', '')}\n\n"
                f"{text}\n"
            )
            p.write_text(md, encoding="utf-8")
        print(f"  + id={rid} docId={doc_id} textLen={len(text)} title={d.get('title','')[:24]!r}", flush=True)
        ok += 1
        time.sleep(rc.SLEEP_BETWEEN)
    print(f"[text] Done. refreshed={ok} skipped={skip}", flush=True)
    return ok, skip


def stage_llm(workers=4):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from web_app import _process_research_report
    import tradingagents.dataflows.research_data as rd

    # 并发安全:聚合 JSON 按品种单文件,并发写会损坏 → 用全局锁串行化 upsert;
    # LLM 调用(慢)仍在多线程并行,仅最后的聚合写入被串行化,既提速又保安全。
    _ulock = threading.Lock()
    _orig = rd.upsert_research_report

    def _locked(variety, record):
        with _ulock:
            return _orig(variety, record)

    rd.upsert_research_report = _locked

    # 断点续跑:已完成的 id 记入状态文件,被杀后重跑跳过
    done = set()
    if _LLM_DONE.exists():
        try:
            done = set(json.loads(_LLM_DONE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            done = set()
    reps = [r for r in fx_reports() if r[0] not in done]
    print(f"[llm] re-processing {len(reps)} fxbaogao reports, workers={workers}, done={len(done)}...", flush=True)

    def work(rid, doc_id):
        try:
            _process_research_report(rid)
            with _ulock:  # 与聚合写入共用锁,保证状态文件写也串行安全
                done.add(rid)
                _LLM_DONE.write_text(json.dumps(sorted(done)), encoding="utf-8")
            print(f"  + id={rid} docId={doc_id} 重提取完成 ({len(done)}/28)", flush=True)
        except Exception as e:
            print(f"  ! id={rid} docId={doc_id} 异常: {e}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda r: work(r[0], r[1]), reps))
    print(f"[llm] Done. total_done={len(done)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["text", "llm", "all"], default="text")
    args = ap.parse_args()
    if args.stage in ("text", "all"):
        stage_text()
    if args.stage in ("llm", "all"):
        stage_llm()


if __name__ == "__main__":
    main()
