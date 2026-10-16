#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量 force 重跑「受同源合并修复影响的」每日/周度总结。

【为什么要跑】2026-09-14 修复了「同一机构同日同品种多篇研报被拆成多个观点」
的问题(详见 web_app._merge_same_source_items)。但**已生成的 .md 是缓存的**
(`path.is_file() and not force` 直接复用),不 force 重跑就还是旧的
「4 个源 / 6 个观点」形态。

【默认行为】自动扫描:对每个已存在的总结文件,用当前(修复后的)采集逻辑重算一遍,
只要出现「同源合并条」或「同篇双采被剔除」,就判定为受影响并加入重跑队列。

【用法】
    python scripts/regen_affected_summaries.py                # 自动扫描 + 重跑
    python scripts/regen_affected_summaries.py --dry-run      # 只列出,不重跑
    python scripts/regen_affected_summaries.py --only 2026-09-13,2026-09-11
    python scripts/regen_affected_summaries.py --gap 20       # 每份间隔 20s(默认 15s)

【注意】
  · 需 web 服务已在 5000 端口运行(脚本启动前会自检)。
  · 每份总结 1 次 LLM 调用;间隔默认 15s 是为避开 LLM 账户频率限制。
  · 单份失败不中断,脚本继续跑下一份,结尾汇总成功/失败。
  · ⚠️ 客户端超时 ≠ 生成失败:周报(材料多的)实测要 8~15 分钟,超过 --timeout 时
    客户端会断连,但**服务端仍在跑且最终会落盘**。脚本超时后会复查文件 mtime,
    已刷新就按成功计(标注"客户端超时,但服务端已落盘"),不会误报进失败清单。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:5000"
GEN_URL = f"{BASE}/api/research/daily/generate"


# ---------------------------------------------------------------- 扫描受影响日期
def scan_affected() -> list[tuple[str, str]]:
    """返回 [(date, rtype), ...] —— 当前逻辑下会发生同源合并/同篇剔除的已生成总结。"""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    import web_app as W  # noqa: E402  延迟导入:必须在 sys.path 补根目录之后

    rows = W.get_db().list_research_reports(limit=500)
    out: list[tuple[str, str]] = []
    for rtype, directory in (("", W.RESEARCH_DAILY_DIR), ("周报", W.RESEARCH_WEEKLY_DIR)):
        p = pathlib.Path(directory)
        if not p.is_dir():
            continue
        for f in sorted(p.glob("????-??-??.md")):
            date = f.stem
            try:
                items = W._collect_daily_report_items(rows, date, rtype)
            except Exception as exc:  # 单日扫描失败不影响整体
                print(f"  [跳过] {date}({rtype or '日报'}) 扫描失败: {exc}")
                continue
            merged = sum(1 for it in items if (it.get("merged_count") or 1) > 1)
            dupdrop = sum(int(it.get("dup_dropped") or 0) for it in items)
            if merged or dupdrop:
                out.append((date, rtype))
                print(f"  [命中] {date} {rtype or '日报':<4} 合并条={merged} 同篇剔除={dupdrop}")
    return out


# ---------------------------------------------------------------- 单份重跑
def _summary_path(date: str, rtype: str) -> pathlib.Path:
    """总结文件落盘位置(与 web_app 的 RESEARCH_DAILY_DIR / RESEARCH_WEEKLY_DIR 一致)。"""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    import web_app as W  # noqa: E402

    base = W.RESEARCH_WEEKLY_DIR if (rtype or "").strip() == "周报" else W.RESEARCH_DAILY_DIR
    return pathlib.Path(base) / f"{date}.md"


def regen_one(date: str, rtype: str, timeout: int = 900) -> tuple[bool, str]:
    """force 重跑一份总结。

    【注意:客户端超时 ≠ 生成失败】周报材料多时实测单次要 8~15 分钟,超过客户端
    timeout 时 urllib 抛 TimeoutError,但**服务端仍在跑且最终会落盘**。所以超时后
    复查文件 mtime:若已刷新,按「服务端完成(客户端没等到)」算成功,避免误报。
    """
    fpath = _summary_path(date, rtype)
    mtime_before = fpath.stat().st_mtime if fpath.exists() else 0.0

    body = json.dumps({"date": date, "report_type": rtype, "force": True}).encode("utf-8")
    req = urllib.request.Request(GEN_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        el = time.time() - t0
        # 超时/断连:复查落盘 —— 服务端可能已经写好了
        if fpath.exists() and fpath.stat().st_mtime > mtime_before:
            return True, f"{el:.0f}s(客户端{type(exc).__name__},但服务端已落盘)"
        return False, f"{type(exc).__name__}: {exc}"
    el = time.time() - t0
    if not out.get("ok"):
        return False, f"服务端返回 ok=False: {str(out.get('error'))[:80]}"
    alert = out.get("merge_alert")
    note = f"{el:.0f}s 材料{out.get('reports')}条"
    if alert:
        note += f"  ⚠ merge_alert={alert}"
    return True, note


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="批量 force 重跑受同源合并影响的总结")
    ap.add_argument("--dry-run", action="store_true", help="只列出受影响的,不重跑")
    ap.add_argument("--only", default="", help="只重跑这些日期,逗号分隔(跳过自动扫描)")
    ap.add_argument("--gap", type=int, default=15, help="每份之间的间隔秒数(默认 15)")
    ap.add_argument("--timeout", type=int, default=600,
                    help="单份客户端等待秒数(默认 600)。超时不代表失败:服务端仍在跑, "
                         "脚本会复查文件是否落盘;周报材料多时实测 8~15 分钟。")
    args = ap.parse_args()

    # 服务自检
    try:
        with urllib.request.urlopen(f"{BASE}/", timeout=8):
            pass
    except Exception:
        print(f"✗ web 服务未就绪({BASE}),请先启动服务再重跑。")
        return 1

    if args.only:
        # 手工指定:日期不带类型时,按「该文件在哪个目录存在」判断
        targets: list[tuple[str, str]] = []
        for raw in args.only.split(","):
            d = raw.strip()
            if not d:
                continue
            targets.append((d, "周报") if d.endswith("W") else (d, ""))
        targets = [(d.rstrip("W"), r) for d, r in targets]
    else:
        print("=== 扫描受影响的已生成总结 ===")
        targets = scan_affected()

    if not targets:
        print("没有需要重跑的总结(全部已是合并口径)。")
        return 0

    print(f"\n=== 待重跑 {len(targets)} 份 ===")
    for d, r in targets:
        print(f"  {d}  {r or '日报'}")

    if args.dry_run:
        print("\n[dry-run] 未实际重跑。")
        return 0

    print()
    ok_n, fail = 0, []
    for i, (d, r) in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {d} {r or '日报'} ... ", end="", flush=True)
        ok, note = regen_one(d, r, timeout=args.timeout)
        print("OK " if ok else "失败", note)
        if ok:
            ok_n += 1
        else:
            fail.append((d, r, note))
        if i < len(targets) and args.gap > 0:
            time.sleep(args.gap)

    print(f"\n=== 完成: 成功 {ok_n} / 共 {len(targets)} ===")
    if fail:
        print("失败清单(可单独重跑,或等 LLM 限额恢复后重试):")
        for d, r, note in fail:
            print(f"  {d} {r or '日报'}  {note}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
