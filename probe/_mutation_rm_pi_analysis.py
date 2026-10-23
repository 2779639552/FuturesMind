# -*- coding: utf-8 -*-
"""变异自检：删「价格-库存关联分析」小模块（2026-10-16）。

对每处重要实现做一次"反向破坏"，确认对应护栏**真的会红**；
跑完逐条核对 md5 复原。护栏若被"同形代码"骗过（本项目踩过多次）就会在这里暴露。

★ 为什么要"范围限定"：本轮 M2/M3/M4 的锚点（` if (body) body.innerHTML = '加载中...';` 等）
  在模板里**多处同形**（实测命中 5/5/3 次 —— 别的方法也这么写）⇒ 全局替换会打偏、
  变异"看着生效其实改错了地方"。故这几条限定在 `loadFundAnalysis` 方法体内替换。

被检护栏：
  T1 tests/test_page_tools_variety_state.py::test_trim3_content_decisions
  T2 tests/test_page_tools_variety_state.py::test_fund_tab_div_balance
  T3 tests/test_fund_xsection.py::test_frontend_sec_index_version_bumped
  T4 tests/test_multi_logic_relocation.py::test_sec_index_version_bumped
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / "venv" / "Scripts" / "python.exe")
TPL = ROOT / "web_template.html"

TESTS = {
    "T1": "tests/test_page_tools_variety_state.py::test_trim3_content_decisions",
    "T2": "tests/test_page_tools_variety_state.py::test_fund_tab_div_balance",
    "T3": "tests/test_fund_xsection.py::test_frontend_sec_index_version_bumped",
    "T4": "tests/test_multi_logic_relocation.py::test_sec_index_version_bumped",
}

# (标签, 原串, 变异串, 应红的测试 key, 期望命中次数, 限定方法名或 None)
MUTS: list[tuple[str, str, str, str, int, str | None]] = [
    ("M1 把「价格-库存关联分析」小模块加回来（用户要求删的）",
     " <!-- 【2026-10-16 用户要求删除】此处原为「价格-库存关联分析」小节\n",
     " <div style=\"font-size:0.8125rem;font-weight:500;margin:16px 0 8px\">\n"
     " 价格-库存关联分析 <span class=\"badge badge-info\" id=\"f-analysis-badge\"></span>\n"
     " </div>\n"
     " <div style=\"font-size:0.8125rem;color:var(--text-secondary);line-height:1.8\" id=\"f-analysis-body\">加载中...</div>\n"
     " <!-- 【2026-10-16 用户要求删除】此处原为「价格-库存关联分析」小节\n",
     "T1", 1, None),

    ("M2 去掉「加载中」那处 null 守卫（裸赋值）",
     " if (body) body.innerHTML = '加载中...';\n",
     " body.innerHTML = '加载中...';\n", "T1", 1, "loadFundAnalysis"),

    ("M3 去掉「加载失败」那处 null 守卫（裸赋值）",
     " if (body) body.innerHTML = `<span style=\"color:var(--red)\">加载失败: ${e.message}</span>`;\n",
     " body.innerHTML = `<span style=\"color:var(--red)\">加载失败: ${e.message}</span>`;\n",
     "T1", 1, "loadFundAnalysis"),

    ("M4 去掉结果渲染的块级守卫（if (body) { → if (true) {）",
     " if (body) {\n body.innerHTML = `\n",
     " if (true) {\n body.innerHTML = `\n", "T1", 1, "loadFundAnalysis"),

    ("M5 去掉徽标的 null 守卫",
     " if (badge) badge.textContent = `${meta.name || sym}",
     " badge.textContent = `${meta.name || sym}", "T1", 1, None),

    ("M6 把 #f-research 的渲染整行删掉（删小模块时的连带伤害）",
     " this._renderResearchSection(d, 'f'); ",
     " void 0; ", "T1", 1, None),

    ("M7 _SEC_IDX_VER 退回 29（结构变了却不重建索引）",
     "_SEC_IDX_VER: '30',\n",
     "_SEC_IDX_VER: '29',\n", "T3", 1, None),

    ("M8 #tab-fund 多塞一个悬空 </div>（配平被破坏）",
     '<div id="tab-fund" class="tab">',
     '<div id="tab-fund" class="tab"></div>', "T2", 1, None),
]


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()[:12]


def method_span(t: str, name: str) -> tuple[int, int]:
    """按花括号配平取方法体 [start, end)。★ 必须用正则定位**定义**（异步方法头含 async 前缀）。"""
    m = re.search(r"\n[ \t]*(?:async[ \t]+)?" + re.escape(name) + r"\s*\(", t)
    if not m:
        raise AssertionError("找不到方法定义: " + name)
    b = t.index("{", m.end())
    d, k = 0, b
    while k < len(t):
        ch = t[k]
        if ch == "{":
            d += 1
        elif ch == "}":
            d -= 1
            if d == 0:
                return b + 1, k
        k += 1
    raise AssertionError("未配平: " + name)


def run(test_key: str) -> bool:
    """True = 该护栏变红了（= 变异被击杀）。"""
    r = subprocess.run([PY, "-m", "pytest", TESTS[test_key],
                        "-q", "--no-header", "-p", "no:warnings", "--tb=no"],
                       cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    return ("failed" in out) or (r.returncode != 0)


def apply_mut(t: str, old: str, new: str, n: int, scope: str | None) -> tuple[str, int, str]:
    """返回 (新文本, 实际命中数, 说明)。scope 非空时只在该方法体内替换。"""
    if scope is None:
        return t.replace(old, new, n), t.count(old), "全局"
    a, b = method_span(t, scope)
    body = t[a:b]
    c = body.count(old)
    if c != n:
        return t, c, scope
    return t[:a] + body.replace(old, new, n) + t[b:], c, scope


def main() -> int:
    print("=== 基线：被检护栏应全绿 ===")
    base_ok = True
    for k in TESTS:
        bad = run(k)
        print("  %s %s" % ("★ 红" if bad else "绿", TESTS[k]))
        base_ok = base_ok and not bad
    if not base_ok:
        print("\n★ 基线不是全绿，变异自检无意义 —— 先修基线")
        return 2

    killed = survived = skipped = 0
    print("\n=== 逐条变异 ===")
    raw = TPL.read_bytes()
    before = md5(TPL)
    assert raw.count(b"\r\n") > 0, "模板应为 CRLF"
    for tag, old, new, tkey, n, scope in MUTS:
        t = raw.decode("utf-8")
        o = old.replace("\n", "\r\n")
        nn = new.replace("\n", "\r\n")
        mutated, c, where = apply_mut(t, o, nn, n, scope)
        if c != n or mutated == t:
            print("  ⊘ 跳过 %s —— 锚点命中 %d（期望 %d）[%s]" % (tag, c, n, where))
            skipped += 1
            continue
        TPL.write_bytes(mutated.encode("utf-8"))
        try:
            red = run(tkey)
        finally:
            TPL.write_bytes(raw)
        assert md5(TPL) == before, "★ 未复原 web_template.html"
        if red:
            killed += 1
            print("  ✔ 击杀  %s   [%s / %s]" % (tag, tkey, where))
        else:
            survived += 1
            print("  ✗ 存活  %s   [%s / %s] ⇒ 护栏有洞！" % (tag, tkey, where))

    print("\n" + "=" * 62)
    print("  击杀 %d / 存活 %d / 跳过 %d   （md5 全部复原 ✔）"
          % (killed, survived, skipped))
    print("=" * 62)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
