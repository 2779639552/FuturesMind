# -*- coding: utf-8 -*-
"""变异自检：⑨ 基本面截面表的「后台缓存 + 定期刷新」与「气泡筛选」（2026-10-16）。

【判据】每个变异都必须让 `pytest tests/test_fund_xsection.py` 变红。
若全绿 ⇒ 该变异"存活" ⇒ 护栏有洞（或变异本身没构成缺陷）。

【纪律】
  · 每次变异后**从原始字节恢复**，跑完核对 md5；
  · **不得与全量回归并发**（本脚本会临时改写模板/后端）。
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / "venv" / "Scripts" / "python.exe")
FM = ROOT / "fund_module.py"
TPL = ROOT / "web_template.html"
JUNIT = ROOT / "probe" / "_mut_frk.xml"


def run() -> tuple[bool, str]:
    if JUNIT.exists():
        JUNIT.unlink()
    subprocess.run([PY, "-m", "pytest", "tests/test_fund_xsection.py", "-q",
                    "--no-header", "-p", "no:warnings", "--junitxml=%s" % JUNIT],
                   cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    n = 0
    if JUNIT.exists():
        try:
            t = ET.parse(JUNIT)
            s = t.getroot()
            s = s.find("testsuite") if s.tag == "testsuites" else s
            n = int(s.get("failures", 0)) + int(s.get("errors", 0))
        except Exception:  # noqa: BLE001
            n = 1
    else:
        n = 1
    return n > 0, ("pytest %d 红" % n if n else "全绿")


MUTATIONS = [
    # ── 后端：后台缓存 + stale-while-revalidate ──────────────────────────────
    ("M1 TTL 改 300（与前端 15 分钟不对齐）", FM,
     "_TTL_FUND_RANK = 900", "_TTL_FUND_RANK = 300", 1),
    ("M2 worker 不消费 carry（旧表瞬间被抹）", FM,
     "    rows: dict[str, dict] = dict(carry or {})   # 【变量】带上旧结果, 逐品种覆盖",
     "    rows: dict[str, dict] = {}", 1),
    ("M3 刷新途中 done 不带 had_old（回占位行）", FM,
     '            st["done"] = bool(done or had_old)', '            st["done"] = bool(done)', 1),
    # 【★ 上一版这条是**无效变异】**：把 `carry = ...` 改成 None 之后，紧跟着的
    #   "从持久缓存 seed carry" 分支会接手（测试环境磁盘上确实有该 key 的缓存）
    #   ⇒ 行为等价 ⇒ 当然击杀不了。改打真正生效的那处：建 state 时把 done 清掉。
    ("M4 _frk_start 建 state 时把 done 清掉", FM,
     '"done": bool(carry),', '"done": False,', 1),
    ("M5 _frk_start 不再从持久缓存 seed carry", FM,
     "        if carry is None:\n            # 进程内无旧结果 ⇒ 试试持久缓存（进程重启后仍能秒开）",
     "        if False:\n            # 进程内无旧结果 ⇒ 试试持久缓存（进程重启后仍能秒开）", 1),
    ("M6 cached 退回 not refresh（恒真）", FM,
     '        "cached": bool(done and not meta["refreshing"] and not meta["stale"] and not err),',
     '        "cached": not refresh,', 1),
    ("M7 _frk_start 去掉「在跑中不再起线程」", FM,
     "        if refreshing:\n            # 已有后台刷新在跑（显式 force 也不再起一个 —— 那一轮马上就会给出新数据）\n            return key",
     "        if False:\n            return key", 1),
    ("M8 _frk_start 去掉 TTL 新鲜判断（每次都重算）", FM,
     "        if fresh and not force:\n            return key                  # 结果还新鲜, 命中",
     "        if False:\n            return key", 1),
    # ── 前端：定期刷新 ──────────────────────────────────────────────────────
    ("M9 前端周期改 5 分钟（与后端不对齐）", TPL,
     "_FRK_REFRESH_MS: 15 * 60 * 1000,", "_FRK_REFRESH_MS: 5 * 60 * 1000,", 1),
    ("M10 切走 tab 不再停表", TPL,
     " if (t !== 'fund') this._stopFundRanking();", " if (false) this._stopFundRanking();", 1),
    ("M11 进明细不停表", TPL,
     " fOpenDetail(code) {\n"
     "  // 总览已不可见 ⇒ 停掉它的定期刷新（切回时 fBackOverview 会重新起）\n"
     "  this._stopFundRanking();",
     " fOpenDetail(code) {", 1),
    ("M12 返回总览不再重启拉取", TPL,
     "  this.loadFundRanking();\n  window.scrollTo({ top: 0, behavior: 'smooth' });",
     "  window.scrollTo({ top: 0, behavior: 'smooth' });", 1),
    ("M13 占位行去掉 had 守卫（闪白）", TPL,
     " if (!had) {\n  tbody.innerHTML = '<tr><td colspan=\"19\" style=\"padding:18px;color:var(--text-muted)\">'",
     " if (true) {\n  tbody.innerHTML = '<tr><td colspan=\"19\" style=\"padding:18px;color:var(--text-muted)\">'", 1),
    ("M14 去掉「有旧表+刷新中」分支", TPL,
     " } else if (d.refreshing) {\n"
     "  // ② 有旧结果 + 后台重算 ⇒ **旧表继续显示**，5s 短轮询等新数据逐个落地",
     " } else if (false) {\n"
     "  // ② 有旧结果 + 后台重算 ⇒ **旧表继续显示**，5s 短轮询等新数据逐个落地", 1),
    ("M15 刷新中也显示「下次刷新时刻」（过期信息）", TPL,
     "   if (d.done && !d.refreshing && this._frkNextAt) {", "   if (d.done && this._frkNextAt) {", 1),
    ("M16 基线快照刷新中也重建", TPL,
     " if (!d.refreshing || !this._fRkBase) this._fRkBase = JSON.parse(JSON.stringify(d));",
     " this._fRkBase = JSON.parse(JSON.stringify(d));", 1),
    # ── 前端：气泡筛选 ──────────────────────────────────────────────────────
    ("M17 维度筛选改回 <select>（去气泡）", TPL,
     "'<button class=\"btn btn-sm btn-outline f-rk-pill\" data-dim=\"'",
     "'<option class=\"f-rk-pill\" data-dim=\"'", 1),
    # ⚠ 锚点须跟着重建条件一起改：条件串里现在**多了 `dataset.settled` 这一段**，
    #   沿用旧串会命中 0（实测踩到）⇒ 直接打"整条条件行"。
    ("M18 气泡去掉签名守卫（轮询会重建 DOM）", TPL,
     " if (!built || (settled && (box.dataset.sig !== sig || box.dataset.settled !== '1'))) {",
     " if (true) {", 1),
    ("M19 渐进阶段也显示计数（给不准的数）", TPL,
     "   const pills = [mk('全部', '全部', settled ? rows.length : null)]",
     "   const pills = [mk('全部', '全部', rows.length)]", 1),
]


def main() -> int:
    orig = {p: p.read_bytes() for p in (FM, TPL)}
    md5_orig = {p: hashlib.md5(b).hexdigest() for p, b in orig.items()}

    print("=== 基线（应全绿）===")
    bad, info = run()
    if bad:
        print("  ★ 基线就是红的 ⇒ 先修基线：%s" % info)
        return 1
    print("  基线 %s ✓\n" % info)

    killed, skipped = 0, []
    print("=== 变异 ===")
    for tag, path, old, new, n in MUTATIONS:
        b0 = orig[path]
        eol = "\r\n" if b0.count(b"\r\n") else "\n"
        t = b0.decode("utf-8")
        o = old.replace("\n", eol)
        nn = new.replace("\n", eol)
        got = t.count(o)
        if got != n:
            print("  %-40s 锚点命中 %d(期望 %d) —— 跳过（变异脚本自身问题）" % (tag, got, n))
            skipped.append(tag)
            continue
        path.write_bytes(t.replace(o, nn, 1).encode("utf-8"))
        try:
            bad, info = run()
        finally:
            path.write_bytes(orig[path])
        if bad:
            killed += 1
            print("  ✔ 击杀  %-38s %s" % (tag, info))
        else:
            print("  ★ 存活  %-38s ← 护栏有洞或变异无效" % tag)

    drift = [str(p) for p, b in orig.items() if hashlib.md5(p.read_bytes()).hexdigest() != md5_orig[p]]
    for p, b in orig.items():
        p.write_bytes(b)
    total = len(MUTATIONS) - len(skipped)
    print("\n结果：%d/%d 击杀；跳过 %d；文件复原 %s"
          % (killed, total, len(skipped), "全部一致 ✓" if not drift else "★ 不一致: " + ",".join(drift)))
    if skipped:
        print("跳过的（请修变异脚本锚点）：" + "; ".join(skipped))
    if JUNIT.exists():
        JUNIT.unlink()
    return 0 if (killed == total and not drift and not skipped) else 1


if __name__ == "__main__":
    sys.exit(main())
