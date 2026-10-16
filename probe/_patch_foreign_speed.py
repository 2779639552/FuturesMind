# -*- coding: utf-8 -*-
"""外文研报提速补丁（2026-10-14）。

================================ 实测耗时分布 ================================
以任务 97f6245ebb00（76.5K 字符 / 22 段翻译）为例，全程 79s：
    extract      1s
    analyze(4路) 13s   ← 与翻译可并行
    translate    38s
    render+自检  28s   ← 实际渲染了 **2 次**
以 342K 字符的大任务为例：翻译 ~100s、map+reduce ~120s+、渲染 2 次 ~43s。

================================ 三处改动 ================================
① **翻译与归纳并行**：翻译只依赖原文分块，不依赖任何归纳结果，改造前却是串行。
   把 `chunks` 与翻译线程提前到归纳之前启动（大任务能省掉整段 map+reduce 的等待）。
② **全局并发闸**：归纳 4 路 + 翻译 3 路同时跑 = 7，超账户配额(5) 必被限流。
   并发不再靠"各池各自设小"，而是所有真实 LLM 调用统一过 `_LLM_SEM`。
③ **渲染不再有第 3 次**：原逻辑里"第 2 遍不更好"时会**再渲一遍第 1 版**（纯浪费，
   与第 1 版逐字节相同）。改为保存第 1 版字节、需要时还原。
   附带：新横版两列下第 1 遍就 0 空白页（已实测），第 2 遍基本不会触发。

⚠️ 纪律：所有锚点先验命中数，任一不为 1 即**中止且不写文件**；CRLF 严格往返。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "foreign_report.py"

# ═══════════ ① 并发闸常量 ═══════════
A_SEM = '''TRANSLATE_WORKERS = 3  # 【变量】翻译并发度(对齐外部账户并发余量)'''
N_SEM = '''TRANSLATE_WORKERS = 3  # 【变量】翻译并发度(对齐外部账户并发余量)
# ── 【2026-10-14 提速】跨阶段的**全局** LLM 并发闸 ─────────────────────────
# 【为什么要它】改造后「分块笔记/四路归纳」与「全文翻译」**同时**跑：归纳最多 4 路、
#   翻译 3 路，若各自为政总并发会到 7 —— 超过外部账户配额(实测 5)必然被限流。
#   所以并发**不再靠"把每个池子调小"**（那样一并行就失控），而是所有真实调用
#   统一先过这把闸：无论多少个线程在跑，真正在飞的请求数不超过 LLM_CONCURRENCY。
LLM_CONCURRENCY = 5  # 【变量】外部 LLM 账户的并发上限(与 TRANSLATE_WORKERS 的关系见上)
_LLM_SEM = threading.BoundedSemaphore(LLM_CONCURRENCY)'''

# ═══════════ ② 在 _ask_json / _ask_text 里过闸 ═══════════
A_JSON = '''            r = llm.invoke(p)
            body = _msg_text(r)  # 【调用函数】取正文(含 reasoning_content 兜底)
            data = _json_from(body)'''
N_JSON = '''            with _LLM_SEM:  # 【并发闸】见 LLM_CONCURRENCY 注释
                r = llm.invoke(p)
            body = _msg_text(r)  # 【调用函数】取正文(含 reasoning_content 兜底)
            data = _json_from(body)'''

A_TEXT = '''            r = llm.invoke(prompt)
            body = _msg_text(r)  # 【调用函数】取正文(含 reasoning_content 兜底)
            body = re.sub(r"^```[a-zA-Z0-9_]*\\s*", "", body)'''
N_TEXT = '''            with _LLM_SEM:  # 【并发闸】见 LLM_CONCURRENCY 注释
                r = llm.invoke(prompt)
            body = _msg_text(r)  # 【调用函数】取正文(含 reasoning_content 兜底)
            body = re.sub(r"^```[a-zA-Z0-9_]*\\s*", "", body)'''

# ═══════════ ③ 把翻译提到归纳之前并行启动 ═══════════
A_START = '''        # ── 阶段 2：四路并行归纳 ─────────────────────────
        _log(job, "正在通读全文并归纳结论、逻辑、数据、内部解读（4 路并行）…", "analyze", 12)
        llm = _make_llm()'''
N_START = '''        # ── 阶段 2/3 **并行**：全文翻译 与 通读归纳 同时进行 ─────────────
        # 【2026-10-14 提速】翻译只依赖**原文分块**，与归纳结果毫无依赖关系，改造前却
        #   是串行（先 4 路归纳、再分块翻译）⇒ 归纳那 13s~120s+ 完全白等。
        #   这里把翻译提前到归纳之前启动，两段耗时取 max 而非 sum。
        #   ⚠️ 并行后总并发会超过账户配额，靠 `_LLM_SEM` 全局闸兜住（见其注释）。
        llm = _make_llm()

        chunks = _chunk_pages(data["pages"])
        job["chunks"] = len(chunks)
        _log(job, f"开始全文翻译：共 {len(chunks)} 段，{TRANSLATE_WORKERS} 路并行"
                  f"（与通读归纳同时进行）…", "translate", 27)

        done_n = 0
        lock = threading.Lock()

        def _do_translate() -> None:
            """分块并行翻译（在独立线程里跑，与主线程的归纳并行）。"""
            nonlocal done_n
            with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as pool:
                futs = {pool.submit(_translate_chunk, llm, c["text"], i, len(chunks)): i
                        for i, c in enumerate(chunks)}
                for fut in as_completed(futs):
                    i = futs[fut]
                    try:
                        out = fut.result()
                    except Exception as e:  # noqa: BLE001
                        out = ""
                        warnings.append(
                            f"全文翻译的第 {i + 1} 段（原文第 {chunks[i]['pages']} 页）"
                            f"翻译失败：{str(e)[:120]}")
                    chunks[i]["idx"] = i
                    chunks[i]["out"] = out
                    with lock:
                        done_n += 1
                        pct = 27 + int(61 * done_n / max(1, len(chunks)))
                    _log(job, f"翻译进度 {done_n}/{len(chunks)} 段", "translate", pct)

        _tr_pool = ThreadPoolExecutor(max_workers=1)
        _tr_fut = _tr_pool.submit(_do_translate)

        _log(job, "正在通读全文并归纳结论、逻辑、数据、内部解读（4 路并行）…", "analyze", 12)'''

# ═══════════ ④ 删掉原地串行的翻译块，换成"等它跑完" ═══════════
A_TRANS = '''        # ── 阶段 3：分块并行翻译 ─────────────────────────
        chunks = _chunk_pages(data["pages"])
        job["chunks"] = len(chunks)
        _log(job, f"开始全文翻译：共 {len(chunks)} 段，{TRANSLATE_WORKERS} 路并行…", "translate", 27)

        done_n = 0
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as pool:
            futs = {pool.submit(_translate_chunk, llm, c["text"], i, len(chunks)): i
                    for i, c in enumerate(chunks)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    out = fut.result()
                except Exception as e:
                    out = ""
                    warnings.append(f"全文翻译的第 {i + 1} 段（原文第 {chunks[i]['pages']} 页）翻译失败：{str(e)[:120]}")
                chunks[i]["idx"] = i
                chunks[i]["out"] = out
                with lock:
                    done_n += 1
                    pct = 27 + int(61 * done_n / max(1, len(chunks)))
                _log(job, f"翻译进度 {done_n}/{len(chunks)} 段", "translate", pct)
        chunks.sort(key=lambda c: c["idx"])
        n_fail = sum(1 for c in chunks if not (c.get("out") or "").strip())
        _log(job, f"翻译完成：{len(chunks) - n_fail}/{len(chunks)} 段成功", "translate", 88)'''
N_TRANS = '''        # ── 阶段 3 收尾：等并行翻译跑完 ─────────────────────────
        try:
            _tr_fut.result()
        except Exception as e:  # noqa: BLE001
            warnings.append(f"全文翻译整体异常：{str(e)[:160]}")
        finally:
            _tr_pool.shutdown(wait=False)
        chunks.sort(key=lambda c: c["idx"])
        n_fail = sum(1 for c in chunks if not (c.get("out") or "").strip())
        _log(job, f"翻译完成：{len(chunks) - n_fail}/{len(chunks)} 段成功", "translate", 88)'''

# ═══════════ ⑤ 渲染不再有第 3 次 ═══════════
A_RENDER = '''        _log(job, "正在渲染横版 PDF（16:9 两列）并写入书签…", "render", 93)
        info = _render_pdf(build_html(False), out_pdf, footer_left, marks)
        bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]
        if bad:'''
N_RENDER = '''        _log(job, "正在渲染横版 PDF（16:9 两列）并写入书签…", "render", 93)
        info = _render_pdf(build_html(False), out_pdf, footer_left, marks)
        # 【2026-10-14】把第 1 版的字节留一份：若第 2 版并不更好就要回退，
        #   而**回退靠还原字节、不再重渲一遍**（旧逻辑在这里又渲染了一次与第 1 版逐字节
        #   相同的 PDF —— 单次渲染实测 6~21s，纯浪费）。
        _first_pdf_bytes = out_pdf.read_bytes() if out_pdf.exists() else b""
        bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]
        if bad:'''

A_ROLLBACK = '''                else:
                    # 回滚到第 1 版（默认版式）
                    info = _render_pdf(build_html(False), out_pdf, footer_left, marks)
                    bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]'''
N_ROLLBACK = '''                else:
                    # 回滚到第 1 版：**还原字节**（不再重渲，省一次 6~21s 的渲染）
                    if _first_pdf_bytes:
                        out_pdf.write_bytes(_first_pdf_bytes)
                        bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]
                    else:  # 极端兜底：第 1 版字节没拿到才重渲
                        info = _render_pdf(build_html(False), out_pdf, footer_left, marks)
                        bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]'''

A_ROLLBACK2 = '''            except Exception as e:
                _log(job, f"续排尝试失败，保留默认版式：{str(e)[:120]}", "render", None)
                info = _render_pdf(build_html(False), out_pdf, footer_left, marks)
                bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]'''
N_ROLLBACK2 = '''            except Exception as e:
                _log(job, f"续排尝试失败，保留默认版式：{str(e)[:120]}", "render", None)
                if _first_pdf_bytes:
                    out_pdf.write_bytes(_first_pdf_bytes)
                    bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]
                else:
                    info = _render_pdf(build_html(False), out_pdf, footer_left, marks)
                    bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]'''

# ═══════════ ⑥ 进度单调（并行后两个来源会来回写进度）═══════════
A_PROG = '''        if progress is not None:
            job["progress"] = max(0, min(100, int(progress)))'''
N_PROG = '''        if progress is not None:
            # 【2026-10-14】进度必须**单调不减**：翻译与归纳现在并行推进，两边各自
            #   上报自己的百分比（27~88 与 12~26），直接赋值会让进度条来回跳。
            job["progress"] = max(int(job.get("progress") or 0), max(0, min(100, int(progress))))'''

REPL = [
    ("① 并发闸常量", A_SEM, N_SEM),
    ("② 归纳调用过闸", A_JSON, N_JSON),
    ("③ 翻译调用过闸", A_TEXT, N_TEXT),
    ("④ 翻译提前并行启动", A_START, N_START),
    ("⑤ 删除串行翻译块", A_TRANS, N_TRANS),
    ("⑥ 保留第 1 版字节", A_RENDER, N_RENDER),
    ("⑦ 回退改为还原字节", A_ROLLBACK, N_ROLLBACK),
    ("⑧ 异常回退也还原字节", A_ROLLBACK2, N_ROLLBACK2),
    ("⑨ 进度单调不减", A_PROG, N_PROG),
]


def main() -> int:
    raw = TARGET.read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf != 0 or lf == 0:
        print(f"✘ foreign_report.py 应为纯 LF (CRLF={crlf} bareLF={lf})，中止")
        return 2
    src = raw.decode("utf-8")

    print("=" * 74)
    print("补丁：外文研报提速（翻译∥归纳 + 全局并发闸 + 去三渲）")
    print("=" * 74)

    print("\n[1] 锚点预检（任一不为 1 即中止，不写文件）")
    bad_anchor = []
    for tag, old, _new in REPL:
        n = src.count(old)
        print(f"    {'OK' if n == 1 else '✘'} {tag}: 命中 {n} 次（期望 1）")
        if n != 1:
            bad_anchor.append(tag)
    if bad_anchor:
        print(f"\n✘ 锚点异常：{bad_anchor} —— 已中止，文件未改动")
        return 3

    print("\n[2] 应用替换")
    for tag, old, new in REPL:
        src = src.replace(old, new, 1)
        print(f"    ✔ {tag}")

    print("\n[3] 自检")
    checks = [
        ("_LLM_SEM 已定义", "threading.BoundedSemaphore(LLM_CONCURRENCY)" in src, True),
        ("_ask_json 过闸", 'with _LLM_SEM:  # 【并发闸】见 LLM_CONCURRENCY 注释' in src, True),
        ("翻译线程已提前启动", "def _do_translate() -> None:" in src, True),
        ("旧的串行翻译块已移除", "        # ── 阶段 3：分块并行翻译 ─────────────────────────" in src, False),
        ("等待翻译的 join 已就位", "            _tr_fut.result()" in src, True),
        ("第 1 版字节已保留", "_first_pdf_bytes = out_pdf.read_bytes()" in src, True),
        ("回退不再重渲", "out_pdf.write_bytes(_first_pdf_bytes)" in src, True),
        ("进度单调", 'max(int(job.get("progress") or 0)' in src, True),
        ("_translate_chunk 仍有定义", "def _translate_chunk(" in src, True),
    ]
    problems = []
    for name, got, want in checks:
        ok = (got is want)
        print(f"    {'OK' if ok else '✘'} {name}")
        if not ok:
            problems.append(name)
    if problems:
        print(f"\n✘ 自检未过：{problems} —— 已中止，文件未改动")
        return 4

    TARGET.write_text(src, encoding="utf-8", newline="")
    chk = TARGET.read_bytes()
    c2 = chk.count(b"\r\n")
    l2 = chk.count(b"\n") - c2
    print(f"\n[4] 已写入 {len(raw)} → {len(chk)} bytes (+{len(chk) - len(raw)})")
    print(f"    行尾: CRLF={c2} bareLF={l2} => {'✔ 纯 LF' if c2 == 0 else '✘ 混入 CRLF'}")
    print("\n✔ 补丁完成")
    return 0 if c2 == 0 else 5


if __name__ == "__main__":
    sys.exit(main())
