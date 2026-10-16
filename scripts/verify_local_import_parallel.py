"""真实 PDF 并发抽取对照:验证 PDF 抽取在本地导入的多线程场景下是否线程安全。

【用法】venv\\Scripts\\python.exe scripts\\verify_local_import_parallel.py
        (可随时重跑,作为改抽取库/改并发度后的回归验证)

动机:本地导入并行化后,唯一**新增**的并发风险点是「PDF 抽取库的线程亲和性」
      (DB 走 threading.local+WAL、聚合 JSON 有 _FILE_LOCK、LLM 客户端早已被三家
      API 采集器的 4 线程池并发调用——这些都不是新风险)。
      PyMuPDF/pdfplumber 这类底层库在多线程下有已知隐患,必须实测。

做法:取真实研报 PDF,先串行抽一遍做基准,再用 4 线程并行抽一遍,逐份对比
      (文本长度 + SHA256)。完全无副作用:不写库、不调 LLM、不落文件。
      退出码 0 = 全部一致(安全);1 = 存在不一致(需给抽取加锁)。
"""
from __future__ import annotations

import hashlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, r"D:\AgentSense\AgentSense")

from web_app import _extract_pdf_text  # noqa: E402  【调用包】与本地导入同一抽取函数

PDF_ROOT = Path.home() / ".tradingagents" / "research_reports"


def pick_pdfs(n: int = 4, max_kb: int = 8000) -> list[Path]:
    """从研报库挑 n 份体积适中的真实 PDF(优先小文件,缩短实测时间)。"""
    cands = [p for p in PDF_ROOT.rglob("*.pdf") if p.stat().st_size < max_kb * 1024]
    cands.sort(key=lambda p: p.stat().st_size)
    return cands[:n]


def digest(text: str | None) -> tuple[int, str]:
    t = text or ""
    return len(t), hashlib.sha256(t.encode("utf-8", "replace")).hexdigest()[:16]


def main() -> int:
    pdfs = pick_pdfs()
    if len(pdfs) < 2:
        print("可用 PDF 不足,跳过实测")
        return 0
    print(f"样本 {len(pdfs)} 份真实 PDF:")
    for p in pdfs:
        print(f"  {p.stat().st_size / 1024:8.0f} KB  {p.name[:70]}")

    # ── 基准:串行抽取 ──────────────────────────────────────────────
    t0 = time.time()
    serial = {}
    for p in pdfs:
        try:
            serial[p.name] = digest(_extract_pdf_text(p))
        except Exception as e:  # noqa: BLE001
            serial[p.name] = ("ERR", repr(e)[:80])
    t_serial = time.time() - t0
    print(f"\n[串行] {t_serial:.2f}s")

    # ── 实测:4 线程并行抽取(与本地导入缺省 worker 数一致)───────────
    def _one(p: Path):
        try:
            return p.name, digest(_extract_pdf_text(p)), None
        except Exception as e:  # noqa: BLE001
            return p.name, ("ERR", repr(e)[:80]), repr(e)[:120]

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(_one, pdfs))
    t_par = time.time() - t0
    print(f"[并行4] {t_par:.2f}s   加速比 {t_serial / t_par:.2f}x")

    # ── 逐份比对:并行结果必须与串行完全一致 ────────────────────────
    print("\n一致性校验(并行 vs 串行):")
    bad = 0
    for name, d, err in rows:
        same = d == serial.get(name)
        if not same:
            bad += 1
        flag = "OK " if same else "差异"
        extra = f"  err={err}" if err else ""
        print(f"  {flag} {name[:52]}  len={d[0]}{extra}")
    print(f"\n结论: {len(rows) - bad}/{len(rows)} 份文本完全一致"
          + ("  → PDF 抽取多线程安全" if bad == 0 else f"  → {bad} 份不一致,需加锁!"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
