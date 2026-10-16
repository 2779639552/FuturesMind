# -*- coding: utf-8 -*-
"""变异自检：证明 test_overlay_trading_days.py 的断言非永真。

带 md5 前后校验，确保源文件被完整还原（防"变异脚本污染源文件却自查已恢复"）。
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "web_app.py"
PYTEST = ROOT / "venv" / "Scripts" / "python.exe"
TARGET = "tests/test_overlay_trading_days.py"

MUTATIONS = [
    ("M1 x 轴退回「并集」口径（用户报的截断会复现）",
     ["window_dates = [d for d in px_dates if d >= start_date]  # 【变量】窗口内交易日(x 轴)",
      'point = {"date": date, "close": price_map[date]}'],
     ["window_dates = sorted(set([d for d in px_dates if d >= start_date] "
      "+ [k for k in sentiment_map if k >= start_date]))  # MUTANT",
      'point = {"date": date, "close": price_map.get(date)}']),
    ("M2 归并退回等权平均（丢弃帖子数权重）",
     ['        def _wavg(key):\n'
      '            return sum(float(r.get(key) or 0) * int(r.get("total_notes") or 0)\n'
      '                       for r in rows) / total'],
     ['        def _wavg(key):\n'
      '            return sum(float(r.get(key) or 0) for r in rows) / n']),
    ("M3 多空比退回恒 0（原缺陷）",
     ['        "bullish_ratio": (bull / total) if total else 0,'],
     ['        "bullish_ratio": 0,']),
    ("M4 非交易日情绪直接丢弃（不再顺延）",
     ['        merged.setdefault(_t, []).append(sentiment_map[_sd])'],
     ['        if _t == _sd:\n            merged.setdefault(_t, []).append(sentiment_map[_sd])']),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def main() -> int:
    orig = SRC.read_bytes()
    h0 = md5(orig)
    # ⚠️ web_app.py 是 CRLF：多行锚点若直接用 \n 写会命中 0 次
    # （单行锚点不受影响，所以这个坑只在多行变异上暴露）。统一在 \n 域内比较，写回时还原。
    orig_lf = orig.decode("utf-8").replace("\r\n", "\n")
    ok = True
    try:
        for name, olds, news in MUTATIONS:
            txt = orig_lf
            hit_ok = True
            for old, new in zip(olds, news):
                n = txt.count(old)
                if n != 1:
                    print(f"[SKIP] {name}: 锚点命中 {n} 次（预期 1）")
                    hit_ok = False
                    ok = False
                    break
                txt = txt.replace(old, new)
            if not hit_ok:
                continue
            SRC.write_bytes(txt.replace("\n", "\r\n").encode("utf-8"))
            proc = subprocess.run(
                [str(PYTEST), "-m", "pytest", TARGET, "-q", "--no-header"],
                capture_output=True, text=True, timeout=600, cwd=str(ROOT),
            )
            SRC.write_bytes(orig)          # 立刻还原
            tail = [l for l in proc.stdout.splitlines() if "passed" in l or "failed" in l or "error" in l]
            killed = proc.returncode != 0
            ok = ok and killed
            print(f"[{'KILLED' if killed else 'SURVIVED!!'}] {name}: {tail[-1] if tail else '(无摘要)'}")
    finally:
        SRC.write_bytes(orig)
    h1 = md5(SRC.read_bytes())
    print(f"\n源文件 md5: 变异前={h0} 还原后={h1}  {'✔ 一致' if h0 == h1 else '✘ 不一致!'}")
    print("结论:", "全部变异被守卫击杀 ✔" if (ok and h0 == h1) else "存在漏杀/文件被污染 ✘")
    return 0 if (ok and h0 == h1) else 1


if __name__ == "__main__":
    sys.exit(main())
