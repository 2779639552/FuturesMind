# -*- coding: utf-8 -*-
"""总览 v2 的两处字段修正（2026-10-14 实测发现）。

================================ 缺陷 1：置信度在阶段 A 虚高 ================================
实测：阶段 A 只拿到日线时 `resonance=1 / period_ready=1` ⇒ 分母用 `period_ready`
得 `1/1*60 = 60` + 技术分 40 = **100 分** —— 但真相是"4 个周期里只有 1 个有方向"。
**根因**：`denom = row.get("period_ready") or len(_OV_PERIODS)`。
当初写"按已就绪周期归一"是怕渐进过程中数字忽高忽低，但代价是**明明只测了 1 个周期
却报满置信度** —— 这比"数字会跳"严重得多（会误导用户把 1/4 共振当成强信号）。
**修法**：分母一律用**目标周期总数**。阶段 A 的 confidence 天然偏低（最多 15+40=55），
这是**诚实**的：多周期还没测，就不该给高置信度；阶段 B 补齐后自然升上去。

================================ 缺陷 2：liquidity 无默认值 ================================
流动性分档要等**阶段 A 全部跑完**才能按全池算（单品种看不到全池）。在那之前
`row["liquidity"]` 是缺键 ⇒ 前端拿到 `undefined` ⇒ 该列**空白**（不是"—"）。
**修法**：在 `_overview_base` 里给默认 `"—"`，前端任何时刻都有可渲染的值。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "quant_module.py"

A1 = '''    # 置信度: 共振占 60 分 + 日线技术分占 40 分 ⇒ 全周期共振且技术分满 = 100。
    # 周期未取全时按"已知周期"归一, 避免渐进过程中数字忽高忽低。
    denom = row.get("period_ready") or len(_OV_PERIODS)
    row["confidence"] = int(round(row["resonance"] / denom * 60 + abs(score) / 5 * 40))'''
N1 = '''    # 置信度: 共振占 60 分 + 日线技术分占 40 分 ⇒ 全周期共振且技术分满 = 100。
    # 【★ 分母必须是**目标周期总数**, 不能用"已就绪周期数"】实测踩过: 阶段 A 只有日线时
    #   resonance=1/period_ready=1 ⇒ 1/1*60+40 = **100 分**, 而真相是"4 个周期里只有
    #   1 个有方向" —— 会误导用户把 1/4 共振当强信号。用总数做分母后阶段 A 最多 15+40=55,
    #   阶段 B 补齐后自然升上去; 数字会跳是**可接受**的代价(诚实 > 平稳)。
    denom = len(_OV_PERIODS)
    row["confidence"] = int(round(row["resonance"] / denom * 60 + abs(score) / 5 * 40))'''

A2 = '''        "unit": meta.get("unit", ""), "main_contract": meta.get("main_contract", ""),
        "periods": {}, "periods_done": False,
    }'''
N2 = '''        "unit": meta.get("unit", ""), "main_contract": meta.get("main_contract", ""),
        # 【默认值必须在阶段 A 就给】流动性分档要等阶段 A 全跑完才能按全池算
        # (单品种看不到全池)。缺这个默认值时前端该列是 `undefined` ⇒ 显示空白。
        "liquidity": "—",
        "periods": {}, "periods_done": False,
    }'''


def main() -> int:
    raw = TARGET.read_bytes()
    if raw.count(b"\r\n") != 0:
        print("✘ quant_module.py 应为纯 LF，中止")
        return 2
    src = raw.decode("utf-8")

    print("=" * 74)
    print("总览字段修正：置信度分母 / liquidity 默认值")
    print("=" * 74)

    print("\n[1] 锚点预检")
    bad = []
    for tag, old in (("① 置信度分母", A1), ("② liquidity 默认值", A2)):
        n = src.count(old)
        print(f"    {'OK' if n == 1 else '✘'} {tag}: 命中 {n} 次（期望 1）")
        if n != 1:
            bad.append(tag)
    if bad:
        print(f"\n✘ 锚点异常：{bad} —— 已中止，文件未改动")
        return 3

    src = src.replace(A1, N1, 1).replace(A2, N2, 1)
    print("\n[2] 应用 ✔ ① ②")

    print("\n[3] 自检")
    checks = [
        ("分母改为周期总数", "denom = len(_OV_PERIODS)" in src),
        ("旧的 period_ready 分母已去除",
         'denom = row.get("period_ready") or len(_OV_PERIODS)' not in src),
        ("liquidity 默认值已加", '"liquidity": "—",' in src),
    ]
    bad2 = [nm for nm, ok in checks if not ok]
    for nm, ok in checks:
        print(f"    {'OK' if ok else '✘'} {nm}")
    if bad2:
        print(f"\n✘ 自检未过：{bad2} —— 已中止，文件未改动")
        return 4

    TARGET.write_text(src, encoding="utf-8", newline="")
    print(f"\n[4] 已写入 {len(raw)} → {TARGET.stat().st_size} bytes")
    print("\n✔ 修正完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
