# -*- coding: utf-8 -*-
"""修 `_qa_realtime_data_block` 的"总闸超时掀掉整块"缺陷（2026-10-14）。

================================ 症状 ================================
全量回归里 `tests/test_rag_ask_route.py` 两个用例报：
    TimeoutError: 1 (of 7) futures unfinished
（`concurrent/futures/_base.py:239`）—— 7 块并行取数里有 1 块没在总闸内完成，
**整个实时数据块被丢掉**，`/api/research/ask` 直接失败（单跑却全绿 ⇒ 与负载有关）。

================================ 根因（自相矛盾的语义）===============================
```python
futs = {ex.submit(f): name for name, f in funcs.items()}
for fut in concurrent.futures.as_completed(futs, timeout=15):   # ← 总闸
    try:
        blk = fut.result()
        ...
    except Exception:            # ← 单块失败只跳过（设计意图）
        logger.warning(...)
```
上面的 `except` 明确表示"单块失败不影响整体"，但 `as_completed(..., timeout=)` 抛出的
**TimeoutError 在 for 之外**，穿透出去把**已经拿到的其余 6 块一起丢掉** —— 与设计意图相反。
（附带：`with ThreadPoolExecutor` 退出时 `shutdown(wait=True)` 还会**阻塞等慢块**，
所以那个 15s 其实也没真正兜住延迟，只是"等完之后再抛"。）

================================ 触发背景 ================================
天勤链路修好后（见 `_fix_tq_logging_kwarg.py`），"技术面"块改为**真的走天勤**
（约 5s，且天勤建连被 `_connect_lock` 串行化）⇒ 7 块共用总闸时更容易触顶。
即：这个缺陷**本来就在**，只是天勤修好后被暴露出来。

================================ 修法 ================================
1. **超时不再掀整块**：`TimeoutError` 就地接住，收下**已完成**的块、记 warning 说明还差几块；
2. **总闸放宽到 25s**：让真走天勤的技术面块有充足预算（原 15s 是"天勤必失败"时代的取值）；
3. 超时后**立刻 `shutdown(wait=False)`**，不再傻等慢块（否则"超时"不兜延迟）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "web_app.py"

OLD = '''    import concurrent.futures

    funcs = {"价格": _prices, "基差": _gtja_basis, "库存": _gtja_inv, "观点": _gtja_view,
             "仓单": lambda: _qa_warrant_block(code),
             "技术面": lambda: _qa_technical_block(code, question),  # 【2026-09-22】按需触发
             "量化面": lambda: _qa_quant_block(code, question)}  # 【2026-09-22】按需触发(技能方法论)
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as ex:
        futs = {ex.submit(f): name for name, f in funcs.items()}
        for fut in concurrent.futures.as_completed(futs, timeout=15):
            name = futs[fut]
            try:
                blk = fut.result()
                if blk:
                    parts.append(blk)
            except Exception as e:  # noqa: BLE001  单块失败只跳过
                logger.warning("QA 实时数据块 %s 失败(variety=%s): %s", name, code, e)'''

NEW = '''    import concurrent.futures

    funcs = {"价格": _prices, "基差": _gtja_basis, "库存": _gtja_inv, "观点": _gtja_view,
             "仓单": lambda: _qa_warrant_block(code),
             "技术面": lambda: _qa_technical_block(code, question),  # 【2026-09-22】按需触发
             "量化面": lambda: _qa_quant_block(code, question)}  # 【2026-09-22】按需触发(技能方法论)
    # 【2026-10-14】总闸从 15s 放宽到 25s：天勤链路修好后「技术面」块改为**真的走天勤**
    #   （约 5s，且建连被 _connect_lock 串行化），15s 在负载高时容易触顶。
    _QA_BLOCK_DEADLINE = 25
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=7)
    futs = {ex.submit(f): name for name, f in funcs.items()}
    try:
        # 【★ 关键】超时**不再掀掉整块**：本块既定语义是"单块失败只跳过"（见下面的
        #   per-block except），而 `as_completed(timeout=)` 抛出的 TimeoutError 会穿透
        #   出去把**已拿到的其余块一起丢掉**，与设计意图相反 —— 实测表现为
        #   `TimeoutError: 1 (of 7) futures unfinished` 让 /api/research/ask 整体失败。
        #   故就地接住：收下已完成的块 + 记一条说明还差几块的 warning。
        for fut in concurrent.futures.as_completed(futs, timeout=_QA_BLOCK_DEADLINE):
            name = futs[fut]
            try:
                blk = fut.result()
                if blk:
                    parts.append(blk)
            except Exception as e:  # noqa: BLE001  单块失败只跳过
                logger.warning("QA 实时数据块 %s 失败(variety=%s): %s", name, code, e)
    except concurrent.futures.TimeoutError:
        _pending = [n for f, n in futs.items() if not f.done()]
        logger.warning("QA 实时数据块总闸超时(%ss)：已收集 %d 块，放弃未完成 %s(variety=%s)",
                       _QA_BLOCK_DEADLINE, len(parts), _pending, code)
    finally:
        # 不能等慢块：`with ThreadPoolExecutor` 退出时是 shutdown(wait=True)，
        # 会**阻塞到所有块做完**，"超时"实际上兜不住延迟（超时后还要再等一次）。
        ex.shutdown(wait=False)'''

REPL = [
    ("总闸超时不再掀整块 + 放宽到 25s", OLD, NEW),
]


def main() -> int:
    raw = TARGET.read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf == 0 or lf != 0:
        print(f"✘ web_app.py 应为纯 CRLF (CRLF={crlf} bareLF={lf})，中止")
        return 2
    src = raw.decode("utf-8").replace("\r\n", "\n")

    print("=" * 74)
    print("修 _qa_realtime_data_block：总闸超时不再丢掉整块实时数据")
    print("=" * 74)

    print("\n[1] 锚点预检")
    bad = []
    for tag, old, _n in REPL:
        n = src.count(old)
        print(f"    {'OK' if n == 1 else '✘'} {tag}: 命中 {n} 次（期望 1）")
        if n != 1:
            bad.append(tag)
    if bad:
        print(f"\n✘ 锚点异常：{bad} —— 已中止，文件未改动")
        return 3

    print("\n[2] 应用替换")
    for tag, old, new in REPL:
        src = src.replace(old, new, 1)
        print(f"    ✔ {tag}")

    print("\n[3] 自检")
    checks = [
        ("超时被就地接住", "except concurrent.futures.TimeoutError:" in src),
        ("结束时不再等慢块", "ex.shutdown(wait=False)" in src),
        ("总闸已放宽", "_QA_BLOCK_DEADLINE = 25" in src),
        ("旧的 with 写法已消失", "with concurrent.futures.ThreadPoolExecutor(max_workers=7) as ex:" not in src),
        ("单块 except 仍在", 'logger.warning("QA 实时数据块 %s 失败(variety=%s): %s", name, code, e)' in src),
        ("per-block 收集逻辑保留", "if blk:\n                    parts.append(blk)" in src),
    ]
    bad2 = [nm for nm, ok in checks if not ok]
    for nm, ok in checks:
        print(f"    {'OK' if ok else '✘'} {nm}")
    if bad2:
        print(f"\n✘ 自检未过：{bad2} —— 已中止，文件未改动")
        return 4

    TARGET.write_bytes(src.replace("\n", "\r\n").encode("utf-8"))
    chk = TARGET.read_bytes()
    l2 = chk.count(b"\n") - chk.count(b"\r\n")
    print(f"\n[4] 已写入 {len(raw)} → {len(chk)} bytes ({len(chk) - len(raw):+d})")
    print(f"    行尾: bareLF={l2} => {'✔ 纯 CRLF' if l2 == 0 else '✘'}")
    print("\n✔ 修复完成")
    return 0 if l2 == 0 else 5


if __name__ == "__main__":
    sys.exit(main())
