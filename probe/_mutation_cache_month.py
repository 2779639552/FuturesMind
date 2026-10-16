"""本轮（总览缓存 + 按合约月重算）护栏的变异自检 —— 2026-10-16。

【安全】逐个变异 → 跑 → 立即从内存原字节还原 → md5 比对确认复原。
  ⚠ 不得与全量回归并发（会临时改写源文件）。
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
TESTS = ["tests/test_quant_overview_refresh.py", "tests/test_fund_xsection.py"]

QM = "quant_module.py"
FM = "fund_module.py"
TP = "web_template.html"

# (说明, 文件, 旧串, 新串, 期望命中数, 用哪个测试文件跑)
MUTATIONS = [
    # ── ① 总览缓存 ──
    ("M1 _ov_start 重算时清空旧 rows(退回老行为)", QM,
     '        _OV_STATE[key] = {"rows": dict(carry or {}), "done": has_old, "refreshing": True,\n'
     '                          "stale": has_old, "refreshed": 0, "err": "",\n'
     '                          "ts": st.get("ts", 0)}',
     '        _OV_STATE[key] = {"rows": {}, "done": False, "refreshing": True,\n'
     '                          "stale": has_old, "refreshed": 0, "err": "",\n'
     '                          "ts": st.get("ts", 0)}',
     1, 0),
    ("M2 worker 不接收 carry(carry 参数被忽略)", QM,
     "    rows: dict[str, dict] = dict(carry or {})   # 【变量】带上旧结果, 逐品种覆盖",
     "    rows: dict[str, dict] = {}",
     1, 0),
    ("M3 刷新期间 done 被写回 False(前端会切成进度条)", QM,
     '            st["done"] = bool(done or had_old)',
     '            st["done"] = done',
     1, 0),
    ("M4 cached 退回恒真(语义又变假)", QM,
     '"cached": bool(done and not meta["refreshing"] and not meta["stale"] and not err),',
     '"cached": bool(done and not err),',
     1, 0),
    ("M5 switchTab 离开时不停总览", TP,
     "if (t !== 'quant') this._stopQuantOverview();",
     "/* 已移除 */",
     1, 0),
    # ⚠ 锚点要带上下行: `} else if (d.refreshing) {` 在**两处**出现(排期分支 + 进度条分支)
    #   ⇒ 只给这一行会命中 2(变异脚本自身问题, 实测踩到)。
    ("M6 前端删掉「刷新中短轮询」分支", TP,
     "  } else if (d.refreshing) {\n   // ②【有旧结果 + 后台重算(2026-10-16)】",
     "  } else if (false) {\n   // ②【有旧结果 + 后台重算(2026-10-16)】",
     1, 0),
    ("M6b 进度条删掉「后台刷新中」分支", TP,
     "   } else if (d.refreshing) {\n    // ② 有旧结果 + 后台重算",
     "   } else if (false) {\n    // ② 有旧结果 + 后台重算",
     1, 0),
    ("M7 dates 退回块内声明(ReferenceError)", TP,
     "  const dates = all.map(r => r.date).filter(Boolean).sort();\n"
     "  const up = document.getElementById('q-ov-updated');\n"
     "  if (up) {\n"
     "   up.textContent = dates.length ? ('数据截至 ' + dates[dates.length - 1]) : '';\n"
     "  }",
     "  const up = document.getElementById('q-ov-updated');\n"
     "  if (up) {\n"
     "   const dates = all.map(r => r.date).filter(Boolean).sort();\n"
     "   up.textContent = dates.length ? ('数据截至 ' + dates[dates.length - 1]) : '';\n"
     "  }",
     1, 0),

    # ── ② 按合约月重算 ──
    ("M8 基差没用所选月价(换成固定值即失效)", FM,
     '    if spot is not None and price:\n        bs = round(float(spot) - float(price), 2)',
     '    if spot is not None and price:\n        bs = round(float(spot) - float(999999), 2)',
     1, 1),
    ("M9 分位数不再置空(用主力序列冒充)", FM,
     '    row["basis_pct"] = None\n    row["basis_pct_note"] = (',
     '    row["basis_pct"] = 0.5\n    row["basis_pct_note"] = (',
     1, 1),
    ("M10 二维四象票不再置空(口径混用)", FM,
     '            row["skill_vd"] = None\n            row["skill_vd_gap"] = (',
     '            row["skill_vd"] = "做多"\n            row["skill_vd_gap"] = (',
     1, 1),
    # ⚠ 原变异只换了 note 的**中间一句** —— 末句 "…口径, 未改变。" 仍在,
    #   护栏(查 "未改变") 自然还过 ⇒ 那是**坏的变异**, 不是假护栏。故改替换**末句**。
    ("M11 期限锚说明改成已覆盖原票", FM,
     'f"**整条曲线**（近月→远月）口径, 未改变。",',
     'f"（已按该月重算，原票已被覆盖）。",',
     1, 1),
    ("M12 月度行不再复用 _vr_ratio_of(自算比值)", FM,
     '            row["vr_ratio"] = _vr_ratio_of(lots, round(float(oi) / 2, 2))',
     '            row["vr_ratio"] = round(float(oi) / 2 / lots, 4)',
     1, 1),
    ("M13 库存族被月度重算覆盖(不依赖合约却被改写)", FM,
     '    row: dict[str, Any] = {"month_mode": m, "vr_months": months}',
     '    row: dict[str, Any] = {"month_mode": m, "vr_months": months, "inv_grade": "低"}',
     1, 1),
    ("M14 前端选月份不再触发重算", TP,
     "  await this._fRecomputeRow(code, month);",
     "  /* 不再重算 */",
     1, 1),
    ("M15 前端选空值不再还原", TP,
     "   this._fRestoreRow(code);",
     "   /* 不再还原 */",
     1, 1),
    ("M16 空选项不改名(语义仍是破折号)", TP,
     '<option value="">默认(主力)</option>',
     '<option value="">—</option>',
     1, 1),
    # ⚠ 用最短且唯一的子串做锚(整条 style 串带转义, 极易对不上 —— 实测命中 0)
    ("M17 月份口径不再做视觉标记", TP,
     "box-shadow:inset 3px 0 0 var(--amber);", "/* x */", 1, 1),
    ("M18 恢复默认不再用基线快照", TP,
     "this._fRkBase = JSON.parse(JSON.stringify(d));",
     "this._fRkBase = null;",
     1, 1),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def run(idx: int) -> tuple:
    r = subprocess.run([str(PY), "-m", "pytest", TESTS[idx], "-q", "--no-header",
                        "-p", "no:warnings"], cwd=str(ROOT), capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln or "error" in ln]
    # ★ 判定不能只认 "failed" —— 语法错会让 pytest 报 `errors during collection`
    bad = ("failed" in out) or ("error" in out)
    return bad, (tail[-1] if tail else out.strip()[-110:])


def main() -> int:
    files = [QM, FM, TP]
    originals = {f: (ROOT / f).read_bytes() for f in files}
    before = {f: md5(b) for f, b in originals.items()}
    t_before = [md5((ROOT / t).read_bytes()) for t in TESTS]
    print("=== 基线 ===")
    for i, t in enumerate(TESTS):
        bad, line = run(i)
        print("   %-46s %s" % (t, line))
        if bad:
            print("★ 基线就是红的 —— 先修基线")
            return 2

    killed, alive, skipped = [], [], []
    print()
    print("=== 逐个变异 ===")
    for tag, fname, old, new, want, idx in MUTATIONS:
        raw = originals[fname]
        crlf = raw.count(b"\r\n") > 0
        txt = raw.decode("utf-8").replace("\r\n", "\n")
        got = txt.count(old)
        if got != want or want == 0:
            print("    %-46s ★ 锚点命中 %d (期望 %d) —— 跳过(变异脚本自身问题)" % (tag, got, want))
            skipped.append(tag)
            continue
        data = txt.replace(old, new, want)
        data = data.replace("\n", "\r\n").encode("utf-8") if crlf else data.encode("utf-8")
        try:
            (ROOT / fname).write_bytes(data)
            bad, line = run(idx)
        finally:
            (ROOT / fname).write_bytes(raw)
        (killed if bad else alive).append(tag)
        print("    %-46s %s  %s" % (tag, "击杀 ✔" if bad else "★ 存活(假护栏!)", line[:46]))

    print()
    print("=== 复原自检 ===")
    ok = True
    for f in files:
        same = md5((ROOT / f).read_bytes()) == before[f]
        ok &= same
        print("    %-22s %s" % (f, "md5 一致 ✔" if same else "★ md5 变了!"))
    for i, t in enumerate(TESTS):
        same = md5((ROOT / t).read_bytes()) == t_before[i]
        ok &= same
        print("    %-46s %s" % (t, "md5 一致 ✔" if same else "★ 变了!"))

    print()
    print("=" * 62)
    print("击杀 %d / 存活 %d / 跳过 %d" % (len(killed), len(alive), len(skipped)))
    if alive:
        print("存活(需补强护栏):")
        for a in alive:
            print("  ★ " + a)
    if skipped:
        print("跳过(锚点没对上):")
        for a in skipped:
            print("  - " + a)
    if not ok:
        print("★★ 源文件未完全复原!")
    return 0 if (ok and not alive) else 1


if __name__ == "__main__":
    raise SystemExit(main())
