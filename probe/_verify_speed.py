# -*- coding: utf-8 -*-
"""外文研报提速验证：并行 vs 串行，并核对**全局并发上限**真的生效。

【为什么要这个探针】"并行化"最容易做成的假象是：代码里多了个线程，
但要么被信号量串回去（等于没并行）、要么彻底放开把账户打到限流。
所以这里同时量三件事：
  ① 墙钟：并行必须显著短于串行（否则并行是假的）；
  ② 峰值并发：任何时刻在飞的 LLM 调用数 **不得超过 LLM_CONCURRENCY**（否则会被 429）；
  ③ 正确性：并行跑完后，每段译文/每块笔记都还在（不许丢）。

【怎么造"确定性的慢 LLM"】用固定 sleep 的假 LLM，延迟可控 ⇒ 耗时差异可归因，
不受真实网络抖动干扰。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import foreign_report as fr  # noqa: E402

CALL_S = 0.30          # 每次假 LLM 调用的耗时（秒）
N_MAP = 5              # 分块笔记块数
N_TRANS = 24           # 翻译段数

# ── 峰值并发计数器 ──────────────────────────────────────────────
_conc = 0
_peak = 0
_conc_lock = threading.Lock()


def _enter():
    global _conc, _peak
    with _conc_lock:
        _conc += 1
        _peak = max(_peak, _conc)


def _exit():
    global _conc
    with _conc_lock:
        _conc -= 1


class _FakeLLM:
    """固定延迟的假 LLM：JSON 请求返回笔记/归纳，文本请求返回译文。"""

    def invoke(self, prompt: str):
        time.sleep(CALL_S)
        _enter()
        try:
            time.sleep(CALL_S)
        finally:
            _exit()
        if "分块笔记" in prompt or '"findings"' in prompt or "请输出 JSON" in prompt:
            body = json.dumps({
                "findings": [{"t": "结论", "p": 1, "basis": "依据"}],
                "numbers": [{"k": "指标", "v": "1"}],
                "terms": [], "meta": {},
            }, ensure_ascii=False)
        elif "翻译成简体中文" in prompt:
            body = "这是译文。" * 20
        else:
            # 四路归纳：按 label 给不同但合法的 JSON
            body = json.dumps({
                "cover": {"title_zh": "测试标题", "org": "测试机构"},
                "summary": {"lead": "引言", "cards": [], "conclusions": []},
                "gloss": [], "notes": {},
                "logic": {"topics": []}, "data": {"groups": []},
                "insight": {"items": []},
            }, ensure_ascii=False)
        return type("R", (), {"content": body})()


def _mk_chunks(n: int) -> list[dict]:
    return [{"pages": [i + 1], "text": f"原文第 {i + 1} 段。" * 40, "idx": i, "out": ""}
            for i in range(n)]


def run(mode: str, llm) -> float:
    """mode='serial' 复刻改造前（先归纳后翻译）；mode='parallel' 复刻改造后。"""
    global _conc, _peak
    with _conc_lock:
        _conc = 0
        _peak = 0
    chunks = _mk_chunks(N_TRANS)
    t0 = time.time()

    def do_translate():
        with ThreadPoolExecutor(max_workers=fr.TRANSLATE_WORKERS) as pool:
            futs = {pool.submit(fr._translate_chunk, llm, c["text"], i, len(chunks)): i
                    for i, c in enumerate(chunks)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    chunks[i]["out"] = fut.result()
                except Exception:
                    chunks[i]["out"] = ""
                chunks[i]["idx"] = i

    def do_analyze():
        # 分块笔记（Map）
        notes = []
        with ThreadPoolExecutor(max_workers=fr.TRANSLATE_WORKERS) as pool:
            futs = [pool.submit(fr._ask_json, llm, '请输出 JSON {"findings":[]}', f"分块笔记 {i+1}")
                    for i in range(N_MAP)]
            for f in as_completed(futs):
                try:
                    notes.append(f.result())
                except Exception:
                    pass
        # 四路归纳（Reduce）
        tasks = {"meta": 1, "logic": 2, "data": 3, "insight": 4}
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futs = {pool.submit(fr._ask_json, llm, "请输出 JSON {}", k): k for k in tasks}
            for f in as_completed(futs):
                f.result()

    if mode == "serial":
        do_analyze()
        do_translate()
    else:
        tr = ThreadPoolExecutor(max_workers=1)
        fut = tr.submit(do_translate)
        do_analyze()
        fut.result()
        tr.shutdown(wait=False)

    wall = time.time() - t0
    ok = sum(1 for c in chunks if (c.get("out") or "").strip())
    print(f"  [{mode:8s}] 墙钟 {wall:6.2f}s  峰值并发 {_peak}  译文成功 {ok}/{N_TRANS}")
    return wall


def main() -> int:
    llm = _FakeLLM()
    print("=" * 74)
    print(f"提速验证（假 LLM，每次调用 {CALL_S}s；{N_MAP} 块笔记 + 4 路归纳 + {N_TRANS} 段翻译）")
    print(f"  账户并发上限 LLM_CONCURRENCY = {fr.LLM_CONCURRENCY}，翻译线程池 = {fr.TRANSLATE_WORKERS}")
    print("=" * 74)

    t_serial = run("serial", llm)
    t_para = run("parallel", llm)

    print()
    gain = t_serial / t_para if t_para else 0
    print(f"串行 {t_serial:.2f}s → 并行 {t_para:.2f}s  ⇒ 提速 {gain:.2f}×（省 {t_serial - t_para:.2f}s）")

    problems = []
    if not (gain > 1.15):
        problems.append(f"并行收益不足（仅 {gain:.2f}×）⇒ 并行可能没真正生效")
    if _peak > fr.LLM_CONCURRENCY:
        problems.append(f"峰值并发 {_peak} 超过上限 {fr.LLM_CONCURRENCY} ⇒ 会被限流")
    print(f"峰值并发 {_peak} ≤ 上限 {fr.LLM_CONCURRENCY} ✓" if _peak <= fr.LLM_CONCURRENCY
          else f"✘ 峰值并发 {_peak} 超限")
    print()
    if problems:
        for p in problems:
            print("✘", p)
        return 1
    print("✔ 并行生效、并发受控、结果无丢失")
    return 0


if __name__ == "__main__":
    sys.exit(main())
