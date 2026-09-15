"""repair_extract.py — 修复存量研报的正文提取质量(两个独立成因)

【背景:两个问题、两套修法】
1. 天玑(华泰官方 ent.htfc.com)源——数字碎片化。
   成因:html_to_text 把每个标签都替换成换行,而天玑正文用
   <span data-type="num"> 把每个数字单独包一层,导致
   "动力煤775元/吨(+20)" 被拆成 4 行。
   修复:research_collector_htfc.html_to_text 改为块级标签换行、内联标签丢弃(已改)。
   存量:.md 是采集时写死的,需 --stage refetch 重新抓取正文覆盖写回。

2. PDF 源——按文本流碎成一行一 token。
   成因:PyMuPDF 默认 page.get_text() 按文本流输出,设计软件导出的研报每个词
   独立坐标 ⇒ 最严重者 6676 行 / 平均行长 5.4 字,extracted_text 的 20000 字
   上限被碎片吃光,真实内容被截断。
   修复:web_app._extract_pdf_text 改为按 block 分块 + y 坐标聚行(已改)。
   存量:正文在入库时现算,直接 --stage llm 重跑即可吃到新提取结果。

【用法】
  python repair_extract.py --scan                       # 只扫描报告,不改数据
  python repair_extract.py --stage refetch              # 重抓天玑碎片报告的正文
  python repair_extract.py --stage llm --ids 3,6,7,8    # 重跑指定 id 的 LLM 提取
  python repair_extract.py --stage llm --pdf            # 重跑全部 PDF 类报告

【注意】--stage llm 会真实调用 LLM(每篇约 1-2 分钟),默认 4 并发;
       进度写入 _repair_done.json,中断后重跑自动跳过已完成的 id。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DB_PATH = r"C:\Users\19168\.tradingagents\agentsense.db"
DONE_FILE = ROOT / "_repair_done.json"  # 【变量】断点续跑状态文件


def _load_env() -> None:
    """加载 .env(天玑 API 需要 HTFC_BASE_URL / HTFC_API_KEY),不回显密钥。"""
    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def pdf_report_ids() -> list[int]:
    """所有正文来自 PDF 的研报 id。"""
    db = _connect()
    return [r["id"] for r in db.execute(
        "SELECT id FROM research_reports WHERE file_path LIKE '%.pdf' ORDER BY id"
    )]


def tianji_report_ids() -> list[int]:
    """所有天玑源(文件名以 RE 开头的 .md)研报 id。"""
    db = _connect()
    out = []
    for r in db.execute("SELECT id, file_path FROM research_reports WHERE file_path LIKE '%.md'"):
        if Path(r["file_path"]).name.startswith("RE"):
            out.append(r["id"])
    return sorted(out)


def fragmented_ids(min_frag: int = 5) -> list[int]:
    """天玑报告中有数字碎片(独占一行的数字 ≥ min_frag 处)的 id。"""
    db = _connect()
    out = []
    for r in db.execute("SELECT id, file_path, extracted_text FROM research_reports"):
        name = Path(r["file_path"] or "").name
        if not (name.startswith("RE") and name.endswith(".md")):
            continue
        t = r["extracted_text"] or ""
        if len(re.findall(r"\n\d+(?:\.\d+)?\n", "\n" + t + "\n")) >= min_frag:
            out.append(r["id"])
    return sorted(out)


# ── stage: refetch(重抓天玑正文,覆盖 .md) ──────────────────────────────

def stage_refetch(target_date: str, ids: list[int] | None = None) -> int:
    """重新拉取天玑正文,用已修复的 html_to_text 覆盖写回 .md。

    【参数】target_date: 目标日期(YYYY-MM-DD);ids: 指定报告 id,None=全部天玑报告。
    【返回】成功刷新的篇数。
    """
    _load_env()
    import research_collector_htfc as rc  # 【调用模块】天玑采集器(已修复 html_to_text)
    import htfc_api  # 【调用包】天玑 API

    db = _connect()
    rows = db.execute(
        "SELECT id, title, file_path FROM research_reports WHERE file_path LIKE '%.md' ORDER BY id"
    ).fetchall()

    # 报告 id → articleId(来自文件名前缀 RE…)
    job: list[tuple[int, str, Path]] = []
    for r in rows:
        p = Path(r["file_path"])
        if not p.name.startswith("RE"):
            continue
        if ids is not None and r["id"] not in ids:
            continue
        article_id = p.name.split("_", 1)[0]
        job.append((r["id"], article_id, p))

    if not job:
        print("[refetch] 没有需要重抓的天玑报告")
        return 0

    items = rc.fetch_today_items(target_date)
    by_id = {it["id"]: it for it in items}
    print(f"[refetch] 当日日报 {len(items)} 篇,待刷新 {len(job)} 篇")

    ok = 0
    for rid, article_id, p in job:
        item = by_id.get(article_id)
        if not item:
            print(f"  ! id={rid} {article_id} 不在当日列表(可能已过期),跳过")
            continue
        try:
            detail = htfc_api.data_of(htfc_api.get_report_info(article_id, item["itemValue"]))
            body = rc.html_to_text(detail.get("content") or "").strip()
            if len(body) < rc.MIN_BODY_CHARS:
                print(f"  ! id={rid} {article_id} 正文过短({len(body)}字符),跳过")
                continue
            md = (
                f"# {item['title']}\n"
                f"- 来源: {rc.SOURCE_LABEL}\n"
                f"- 日期: {item.get('publishDateTime', '')}\n\n"
                f"{body}\n"
            )
            p.write_text(md, encoding="utf-8")
            frag = len(re.findall(r"\n\d+(?:\.\d+)?\n", "\n" + body + "\n"))
            print(f"  + id={rid} {article_id} 已重写 {len(body)} 字符,残余碎片 {frag} 处")
            ok += 1
        except Exception as e:
            print(f"  ! id={rid} {article_id} 异常: {e}")
    print(f"[refetch] 完成 {ok}/{len(job)}")
    return ok


# ── stage: llm(重跑结构化提取与结论) ───────────────────────────────────

def stage_llm(ids: list[int], workers: int = 4) -> int:
    """重跑 _process_research_report,刷新正文/结构化数据/结论/聚合 JSON。

    【参数】ids: 报告 id 列表;workers: 并发数。
    【返回】成功处理的篇数。
    【并发安全】聚合 JSON 按品种单文件写入,故用全局锁串行化 upsert_research_report。
    【断点续跑】每完成一篇写入 _repair_done.json,中断后重跑自动跳过。
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from web_app import _process_research_report  # 【调用模块】研报 LLM 处理
    import tradingagents.dataflows.research_data as rd  # 【调用模块】聚合 JSON

    ulock = threading.Lock()  # 【变量】聚合 JSON 写入锁
    original = rd.upsert_research_report

    def locked_upsert(variety: str, record: dict):
        with ulock:
            return original(variety, record)

    rd.upsert_research_report = locked_upsert

    done: set[int] = set()
    if DONE_FILE.is_file():
        try:
            done = set(json.loads(DONE_FILE.read_text(encoding="utf-8")))
        except Exception:
            done = set()

    todo = [i for i in ids if i not in done]
    print(f"[llm] 待处理 {len(todo)} 篇(已完成 {len(done)} 篇),并发 {workers}")

    def work(rid: int) -> None:
        try:
            _process_research_report(rid)
            with ulock:
                done.add(rid)
                DONE_FILE.write_text(json.dumps(sorted(done)), encoding="utf-8")
            print(f"  + id={rid} 完成 ({len(done)}/{len(ids)})", flush=True)
        except Exception as e:
            print(f"  ! id={rid} 异常: {e}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, todo))

    print(f"[llm] 完成 {len(done)}/{len(ids)}")
    return len(done)


def main() -> int:
    ap = argparse.ArgumentParser(description="修复存量研报正文提取质量")
    ap.add_argument("--scan", action="store_true", help="只扫描,不改数据")
    ap.add_argument("--stage", choices=("refetch", "llm"), help="执行阶段")
    ap.add_argument("--ids", help="逗号分隔的报告 id,仅处理这些")
    ap.add_argument("--pdf", action="store_true", help="--stage llm 时处理全部 PDF 类报告")
    ap.add_argument("--date", default=None, help="--stage refetch 的目标日期(默认今天)")
    ap.add_argument("--workers", type=int, default=4, help="--stage llm 并发数")
    args = ap.parse_args()

    ids = [int(x) for x in args.ids.split(",")] if args.ids else None

    if args.scan or args.stage is None:
        from datetime import date
        # 扫描:加载 .env 只为统计,不调用 API
        print("=== PDF 类报告 ===")
        print("  id:", pdf_report_ids())
        print("=== 天玑(RE*)报告 ===")
        print("  id:", tianji_report_ids())
        print("=== 天玑碎片报告(数字独占行 ≥5 处)===")
        print("  id:", fragmented_ids())
        print(f"=== 今日日期: {date.today():%Y-%m-%d} ===")
        print("\n(加 --stage refetch / --stage llm --ids ... 才会改数据)")
        return 0

    if args.stage == "refetch":
        from datetime import date
        stage_refetch(args.date or f"{date.today():%Y-%m-%d}", ids)
        return 0

    if args.stage == "llm":
        if args.pdf:
            target = pdf_report_ids()
        elif ids:
            target = ids
        else:
            print("错误:--stage llm 需要 --ids 或 --pdf")
            return 1
        stage_llm(target, workers=args.workers)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
