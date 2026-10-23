# -*- coding: utf-8 -*-
"""变异自检：`analyst_views` 的护栏能不能抓到回退。改坏 → 跑 → 复原 → 核 md5。

【为什么每条变异都配一个**具体**测试】护栏最容易变成"永远绿"：
  断言写成"函数不抛异常"或"返回非空"，改坏了照样过。所以每条变异都必须让**某条**
  具体断言变红；跑完还要核对 `-k` 确实选中了用例（选不中时 pytest 也返回非 0，
  会被误记成"击杀"——这个坑在 L0 已经踩过一次）。
"""
import hashlib
import io
import subprocess
import sys

PY = "./venv/Scripts/python.exe"
VIEWS = "tradingagents/dataflows/analyst_views.py"
ANALYST = "tradingagents/agents/analysts/commodity_analysts.py"

MUTS = [
    (VIEWS, "技术视图不过滤轴（全部票都给技术面）",
     '    votes = [v for v in (snap.get("votes") or []) if v.get("axis") in TECH_AXES]',
     '    votes = list(snap.get("votes") or [])',
     "test_technical_view_only_price_axis"),

    (VIEWS, "基本面视图不过滤轴（全部票都给基本面）",
     '    votes = [v for v in (snap.get("votes") or []) if v.get("axis") in FUND_AXES]',
     '    votes = list(snap.get("votes") or [])',
     "test_fundamental_view_only_fund_axes"),

    (VIEWS, "技术视图不中性化票名（泄漏「库存」）",
     "        book = _TECH_BOOK_OVERRIDE.get(key, book)",
     "        pass",
     "test_technical_view_strips_inventory_wording"),

    (VIEWS, "孤儿判据不过滤方向票（把套利/对冲误报成缺口）",
     '           if v.get("axis") not in known and vs.sign(v.get("bias")) != 0]',
     '           if v.get("axis") not in known]',
     "test_spread_verdict_is_not_flagged_as_orphan"),

    (VIEWS, "方向票归不上轴却不再报（真缺口被吞）",
     '           if v.get("axis") not in known and vs.sign(v.get("bias")) != 0]',
     '           if False]',
     "test_direction_vote_with_composite_axis_is_still_flagged"),

    (VIEWS, "预热改回非阻塞（等于没预热）",
     "    _snapshot(symbol, blocking=True)",
     "    _snapshot(symbol)",
     "test_warm_blocks_until_ready"),

    (VIEWS, "冲突时不再给裁决纪律（硬规则回潮）",
     "    if conflict:",
     "    if False:",
     "test_reconcile_detects_conflict_and_forbids_hard_rule"),

    (ANALYST, "技术节点拿到的是基本面视图（分工失效）",
     "        tech_view = technical_view(symbol)",
     "        tech_view = fundamental_view(symbol)",
     "test_technical_analyst_gets_tech_view_only"),
]


def md5(path):
    return hashlib.md5(io.open(path, "rb").read()).hexdigest()


def main() -> int:
    killed = 0
    for path, name, old, new, target in MUTS:
        orig = io.open(path, encoding="utf-8", newline="").read()
        h0 = md5(path)
        if orig.count(old) != 1:
            print("INVALID  %s  → 锚点命中 %d 次" % (name, orig.count(old)))
            continue
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(orig.replace(old, new, 1))
            r = subprocess.run(
                [PY, "-m", "pytest", "tests/test_analyst_views.py", "-q", "-k", target],
                capture_output=True, text=True)
            out = (r.stdout or "") + (r.stderr or "")
            # ★ 只认 "no tests ran"。第一版还加了 `"deselected" in out and "passed" not in out`，
            #   结果**失败**的输出里也没有 "passed" 这个词 ⇒ 所有击杀都被误判成 INVALID。
            if "no tests ran" in out:
                print("INVALID  %s  → -k 没选中用例：%s" % (name, target))
                continue
            ok = r.returncode != 0
            print(("KILLED   " if ok else "SURVIVED ") + name)
            killed += 1 if ok else 0
        finally:
            io.open(path, "w", encoding="utf-8", newline="").write(orig)
        assert md5(path) == h0, "复原失败: " + path

    print("复原 md5 全部一致；击杀 %d/%d" % (killed, len(MUTS)))
    return 0 if killed == len(MUTS) else 1


if __name__ == "__main__":
    sys.exit(main())
