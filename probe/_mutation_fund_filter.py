# -*- coding: utf-8 -*-
"""变异自检：⑨ 基本面截面表的「筛选 / 排序 / 列显隐」（2026-10-16）。

【判据】每个变异都必须让**下面两层中至少一层变红**：
  · `pytest tests/test_fund_xsection.py`（后端口径同源 + 前端结构契约）
  · `node probe/_verify_ffilter_rules.mjs`（前端筛选/排序规则的确定性行为验证）
若两层都绿 ⇒ 该变异"存活" ⇒ 说明护栏有洞（或变异本身没构成缺陷）。

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
NODE = "C:/Users/lenovo/.workbuddy/binaries/node/versions/22.22.2-3/node.exe"
TPL = ROOT / "web_template.html"
FM = ROOT / "fund_module.py"
JUNIT = ROOT / "probe" / "_mut_fund_filter.xml"


def run(noise_out: bool = False) -> tuple[bool, str]:
    """返回 (是否有红, 摘要)。"""
    if JUNIT.exists():
        JUNIT.unlink()
    p1 = subprocess.run([PY, "-m", "pytest", "tests/test_fund_xsection.py", "-q",
                         "--no-header", "-p", "no:warnings",
                         "--junitxml=%s" % JUNIT],
                        cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    n_bad = 0
    if JUNIT.exists():
        try:
            t = ET.parse(JUNIT)
            s = t.getroot()
            s = s.find("testsuite") if s.tag == "testsuites" else s
            n_bad = int(s.get("failures", 0)) + int(s.get("errors", 0))
        except Exception:  # noqa: BLE001
            n_bad = 1
    else:
        n_bad = 1 if "failed" in (p1.stdout or "") else 0
    p2 = subprocess.run([NODE, "probe/_verify_ffilter_rules.mjs"],
                        cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    rules_ok = p2.returncode == 0
    bad = (n_bad > 0) or (not rules_ok)
    which = []
    if n_bad:
        which.append("pytest %d 红" % n_bad)
    if not rules_ok:
        m = re.search(r"PASS (\d+) / FAIL (\d+)", p2.stdout or "")
        which.append("规则复核 %s" % (m.group(0) if m else "崩了"))
    return bad, ("; ".join(which) if which else "两层全绿")


FM_SECTOR_OLD = '"sector_bucket": q._sector_bucket(_sector_cn)}'
FM_SECTOR_NEW = '"sector_bucket": _sector_cn.split("(")[0]}'

FM_POOLS_OLD = '''    _pools = sorted({_q()._sector_bucket(_meta(c).get("sector_cn") or "") for c in codes})'''
FM_POOLS_NEW = '''    _pools = ["能化", "农产品", "能源", "新能源", "谷物油脂"]'''

MUTATIONS = [
    # ── 后端口径同源 ──────────────────────────────────────────────────────
    ("M1 板块自己取括号前缀(不走 quant 归并)", FM,
     FM_SECTOR_OLD, FM_SECTOR_NEW, 1),
    ("M2 pools 改成硬编码列表", FM,
     FM_POOLS_OLD, FM_POOLS_NEW, 1),
    # ── 前端结构契约 ─────────────────────────────────────────────────────
    ("M3 th 的 data-col 写错一个(列序错位)", TPL,
     '<th data-col="basis_rate"', '<th data-col="soc_chg4"', 1),
    ("M4 排序下拉加回表里没有的 chg_pct", TPL,
     '<option value="net" selected>多空票净额</option>',
     '<option value="net" selected>多空票净额</option>\n   <option value="chg_pct">当日涨跌 %</option>', 1),
    ("M5 库存档位排序改前端硬编码映射", TPL,
     '  const x = r.inv_rank;',
     '  const x = ({ \'低\': 1, \'中\': 0, \'高\': -1 })[r.inv_grade];', 1),
    ("M6 统计条改回按筛选后的 rows 算", TPL,
     '   const c = f => all.filter(f).length;',
     '   const c = f => rows.filter(f).length;', 1),
    ("M7 列显隐去掉展开行兜底规则", TPL,
     "rules.push('#f-rk-table tbody tr[id^=\"f-rk-exp-\"] td { display: table-cell !important; }');",
     "rules.push('');", 1),
    # ⚠ 锚点须含前一行：`this._fRkSyncSortCtl();\n this._renderFundRanking();\n},` 在
    #   `_fRkClickHead` 里也出现（命中 2 ⇒ 跳过，实测踩到）。
    ("M8 排序掉头又去请求接口(fetch)", TPL,
     " this._fRkSort = { key: (m && m.value) || 'net', order: (o && o.value) || 'desc' };\n"
     " this._fRkSyncSortCtl();",
     " fetch('/api/fund/ranking');\n"
     " this._fRkSort = { key: (m && m.value) || 'net', order: (o && o.value) || 'desc' };\n"
     " this._fRkSyncSortCtl();", 1),
    ("M9 short 改回 this.(对象字面量陷阱)", TPL,
     'short: v => App._fRkShortVd(v)', 'short: v => this._fRkShortVd(v)', 1),
    ("M10 fixed 列也可被隐藏", TPL,
     ' if (col && col.fixed) return false;', ' if (false) return false;', 1),
    # ── 前端行为（由规则复核抓）─────────────────────────────────────────
    ("M11 板块筛选条件删掉", TPL,
     " if (this._fRkFilter.pool !== '全部品种' && r.sector_bucket !== this._fRkFilter.pool) return false;",
     " if (false) return false;", 1),
    ("M12 排序不再按方向反转", TPL,
     "  return (ka.v > kb.v ? 1 : -1) * (desc ? -1 : 1);",
     "  return (ka.v > kb.v ? 1 : -1);", 1),
    # 【★ 上一版这条是**无效变异**，不是护栏漏洞】它打在 `_fRkNum` 上，而数值列的空值
    #   在 `_fRkSortVal` 里走的是**另一条硬编码分支**(`v === null …` 先被处理) ⇒ 行为没变。
    #   ⇒ 改打真正生效的两条：空值分支 + 票的未知值分支（都由规则复核的行为断言抓）。
    ("M13 数值列空值不再恒最后", TPL,
     " if (v === null || v === undefined || v === '') return { g: 1, v: 0 };",
     " if (v === null || v === undefined || v === '') return { g: 0, v: 0 };", 1),
    ("M13b 票的未知值不再恒最后", TPL,
     " return (s in map) ? { g: 0, v: map[s] } : { g: 1, v: 0 };",
     " return (s in map) ? { g: 0, v: map[s] } : { g: 0, v: 0 };", 1),
]


def main() -> int:
    orig = {p: p.read_bytes() for p in (TPL, FM)}
    md5_orig = {p: hashlib.md5(b).hexdigest() for p, b in orig.items()}

    print("=== 基线（应全绿）===")
    bad, info = run()
    if bad:
        print("  ★ 基线就是红的 ⇒ 先修基线，否则无法判定变异：%s" % info)
        return 1
    print("  基线 %s ✓\n" % info)

    killed = 0
    skipped = []
    print("=== 变异 ===")
    for tag, path, old, new, n in MUTATIONS:
        b0 = orig[path]
        eol = b"\r\n" if b0.count(b"\r\n") else b"\n"
        t = b0.decode("utf-8")
        o = old.replace("\n", eol.decode())
        nn = new.replace("\n", eol.decode())
        got = t.count(o)
        if got != n:
            print("  %-40s 锚点命中 %d(期望 %d) —— 跳过（变异脚本自身问题）" % (tag, got, n))
            skipped.append(tag)
            continue
        path.write_bytes(t.replace(o, nn, 1).encode("utf-8"))
        try:
            bad, info = run()
        finally:
            path.write_bytes(orig[path])          # 立刻恢复
        if bad:
            killed += 1
            print("  ✔ 击杀  %-38s %s" % (tag, info))
        else:
            print("  ★ 存活  %-38s ← 护栏有洞或变异无效" % tag)

    print()
    bad_md5 = [str(p) for p, b in orig.items() if hashlib.md5(p.read_bytes()).hexdigest() != md5_orig[p]]
    for p, b in orig.items():
        p.write_bytes(b)
    total = len(MUTATIONS) - len(skipped)
    print("结果：%d/%d 击杀；跳过 %d；文件复原 %s"
          % (killed, total, len(skipped), "全部一致 ✓" if not bad_md5 else "★ 不一致: " + ",".join(bad_md5)))
    if skipped:
        print("跳过的（请修变异脚本锚点）：" + "; ".join(skipped))
    if JUNIT.exists():
        JUNIT.unlink()
    return 0 if (killed == total and not bad_md5 and not skipped) else 1


if __name__ == "__main__":
    sys.exit(main())
