"""定位研报处理慢/卡在哪一步（零副作用：不写库、不调大模型、不落文件）。

【背景】本地批量导入并行化后，用户反馈「研报库只有一篇在处理」。
  实测库中多行同时处于 processing 且长时间不结束（399 超过 15 分钟、
  400/402/403 超过 5 分钟），而同类文件 401 只用了约 2 分钟。
  需要区分：是**并行没生效**，还是**单篇处理本身极慢/卡死**。

【做法】把 _process_research_report 的前置阶段拆开逐个计时，每段用独立线程 +
  超时保护，避免脚本自己被卡住。分段顺序与真实链路一致：
    1) _extract_report_text   —— PDF 抽文本（必要时走 OCR）
    2) extract_layout_text    —— 版面感知提取（逐页，疑似瓶颈）
    3) 小样 LLM 调用           —— 验证模型端连通性与延迟
    4) _llm_extract_structured —— 真实第一步（默认跳过，--with-llm 才跑）

【用法】venv\\Scripts\\python.exe scripts\\_diag_stage_timing.py [PDF路径] [--with-llm]
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_PDF = (
    Path.home() / ".tradingagents" / "research_reports" / "中信"
    / "【中信期货农业（板块策略）】持续炒作后降温，板块普显回落——周度策略报告20260913.pdf"
)

STAGE_TIMEOUT = 180  # 单段超过这个秒数就判「疑似卡住」，不再干等


def run_stage(name: str, fn, timeout: float = STAGE_TIMEOUT):
    """在独立线程里跑一段，超时即返回（脚本不会被卡住）。"""
    box: dict = {}

    def _wrap():
        t0 = time.time()
        try:
            box["value"] = fn()
            box["ok"] = True
        except Exception as e:  # noqa: BLE001
            box["error"] = f"{type(e).__name__}: {e}"
            box["ok"] = False
        box["t"] = time.time() - t0

    th = threading.Thread(target=_wrap, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        print(f"  [{name}] !!! 超过 {timeout:.0f}s 仍未返回 —— 疑似卡住", flush=True)
        return None
    print(f"  [{name}] {box['t']:.1f}s  ok={box.get('ok')}", flush=True)
    if box.get("error"):
        print(f"        error: {box['error']}", flush=True)
    val = box.get("value")
    if isinstance(val, str):
        print(f"        长度 {len(val)}", flush=True)
    return val


def main() -> int:
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else DEFAULT_PDF
    with_llm = "--with-llm" in sys.argv
    print(f"目标文件: {pdf}")
    print(f"存在: {pdf.exists()}  大小: {pdf.stat().st_size if pdf.exists() else 0:,} B")

    t_import = time.time()
    import web_app  # noqa: F401  【调用包】预热（与 ingest_local_pdfs 一致）
    print(f"\n[预热] import web_app: {time.time() - t_import:.1f}s\n")

    from web_app import _extract_report_text

    text = run_stage("1. _extract_report_text", lambda: _extract_report_text(str(pdf))[0])

    def _layout():
        from tradingagents.dataflows.pdf_layout import extract_layout_text

        return extract_layout_text(pdf)[:60000]

    run_stage("2. extract_layout_text", _layout)

    def _llm_smoke():
        from tradingagents.llm_clients.factory import create_llm_client
        from web_app import config

        client = create_llm_client(
            config["llm_provider"], config.get("quick_think_llm", config["deep_think_llm"])
        )
        llm = client.get_llm()
        r = llm.invoke("只回复两个字：收到")
        return getattr(r, "content", str(r))

    run_stage("3. LLM 小样调用", _llm_smoke, timeout=120)

    if with_llm and text and len(text) > 200:
        def _step1():
            from tradingagents.llm_clients.factory import create_llm_client
            from web_app import _llm_extract_structured, config

            client = create_llm_client(
                config["llm_provider"], config.get("quick_think_llm", config["deep_think_llm"])
            )
            return str(_llm_extract_structured(client.get_llm(), "", text)[:200])

        run_stage("4. _llm_extract_structured", _step1, timeout=300)

    print("\n完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
