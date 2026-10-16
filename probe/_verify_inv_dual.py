"""库存双口径(社会库存 + 仓单) 只读验收 —— 2026-10-16。

【只读】本脚本**不改任何文件**, 可反复运行。
【判据分四层】
  A. 结构/契约: 函数定义恰好一份、锚点产物恰好一处、容器 id 一个没丢
  B. 旧写法已清除: 被替换掉的字符串必须**一处不剩**(防"改了一半")
  C. 规则矩阵(纯函数): _inv_dual 的五条分支 / _inv_ck_zero_warn / _inv_cross
  D. 真实数据抽查: 全池分布 + 端到端字段

用法: python probe/_verify_inv_dual.py
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def ck(cond, what, detail=""):
    (PASS if cond else FAIL).append(what)
    print("  %s %s%s" % ("OK " if cond else "★  ", what, ("  [%s]" % detail) if detail else ""))
    return bool(cond)


def read_lf(p):
    b = (ROOT / p).read_bytes()
    return b.decode("utf-8"), b


def main():
    print("=== A. 结构 / 契约 ===")
    qs, qb = read_lf("quant_module.py")
    fs, fb = read_lf("fund_module.py")
    hs, hb = read_lf("web_template.html")

    # A1 新增函数各恰好一份
    for fn in ("_inv_pct", "_inv_grade", "_inv_dir", "_inv_chg", "_fetch_gtja_inventory",
               "_inv_social_block", "_inv_ck_zero_warn", "_inv_ck_block", "_inv_dual",
               "_inv_cross"):
        ck(qs.count("\ndef %s(" % fn) == 1, "quant_module 定义 %s 恰好一份" % fn,
           "实测 %d" % qs.count("\ndef %s(" % fn))
    ck(qs.count("_GJA_INV_PREF = (") == 1, "口径优先级常量 _GJA_INV_PREF 恰好一份")

    # A2 模板产物各恰好一处(0=没改, >1=重复插入)
    for mark, who in (("const dualLine = (label, o)", "①卡库存块 dualLine"),
                      ("const invSub = dualLine(", "①卡 invSub 组装"),
                      ("const socLine = so.ok", "四象限 socLine"),
                      ("const crossLine = xc.mode", "四象限 crossLine"),
                      ("const invWord = inv.grade", "档位→方向词")):
        n = hs.count(mark)
        ck(n == 1, "模板 %s 恰好一处" % who, "实测 %d" % n)

    # A3 容器 id / 锚点 id 一个不丢(别的护栏按 id 断言)
    for i in ("f-inv-badge", "chart-f-inv", "f-cycle-body", "f-strategy-body",
              "f-strategy-score", "f-strategy-badge", "f-variety"):
        ck(('id="%s"' % i) in hs, "容器 id 保留: #%s" % i)

    # A4 EOL 纯净
    c2, b2 = hb.count(b"\r\n"), hb.count(b"\n") - hb.count(b"\r\n")
    ck(c2 > 0 and b2 == 0, "web_template.html 仍为纯 CRLF", "CRLF=%d bareLF=%d" % (c2, b2))
    for name, byt in (("quant_module.py", qb), ("fund_module.py", fb)):
        cc = byt.count(b"\r\n")
        ck(cc == 0, "%s 仍为纯 LF" % name, "CRLF=%d" % cc)

    # A5 div 配平(改的是 HTML/JS 混排, 配平错会静默把卡片挤出 tab)
    # 【★ 判据是"与基线差值 = 0", 不是"绝对值为 0"】模板里存在条件模板串生成的
    #   `<div>`(如 `${ok ? '<div>' : ''}`), 朴素正则统计**改动前就是 +7** —— 拿绝对
    #   值当判据会得到一条永远红的假护栏(本次实测踩到)。真正该守的不变量是:
    #   **本次改动不得改变这个差值**。
    def _depth(byts):
        t = byts.decode("utf-8")
        d, m0 = 0, 0
        for m in re.finditer(r"<div\b|</div>", t):
            d += 1 if m.group(0) == "<div" else -1
            m0 = min(m0, d)
        return d, m0

    import glob

    _bks = sorted(glob.glob(str(ROOT / "backup" / "web_template_pre_invdual_*.html")))
    if _bks:
        _b = Path(_bks[-1])
        d0, m0 = _depth(_b.read_bytes())
        d1, m1 = _depth(hb)
        ck(d1 == d0, "div 深度**差值**与改动前一致(配平未被打破)",
           "改动前 %+d → 改动后 %+d" % (d0, d1))
        ck(m1 >= m0, "div 最低深度未比改动前更差", "%d → %d" % (m0, m1))
    else:
        ck(True, "div 配平基线(无 backup, 跳过差值比对)")

    # A6 内联 JS 语法
    js = max(re.finditer(r"<script>(.*?)</script>", hs, re.S), key=lambda m: len(m.group(1)))
    tmp = ROOT / "probe" / "_verify_invdual_inline.js"
    tmp.write_text(js.group(1), encoding="utf-8")
    node = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    ck(node.returncode == 0, "内联 JS 通过 node --check", (node.stderr or "")[:120])

    print()
    print("=== B. 旧写法已清除(防改一半) ===")
    ck("库存(仓单分位)" not in hs, "①卡说明已不写'库存(仓单分位)'")
    ck("dim(true, inv.grade, `库存(仓单) 分位" not in hs, "①卡旧库存块已替换")
    ck("dim(false, '', '库存(仓单)'" not in hs, "①卡旧降级文案已替换")
    ck('"q30"' not in fs, "fund 旧 q30 阈值残留已清除", "实测 %d" % fs.count('"q30"'))
    ck('"q70"' not in fs, "fund 旧 q70 阈值残留已清除", "实测 %d" % fs.count('"q70"'))
    ck("def _strategy_verdict(inv_pos: str, prof_pos: str, basis_side: str)" in fs,
       "三指标映射签名未变(仍是三个档位入参)")

    print()
    print("=== C. 规则矩阵(纯函数) ===")
    import fund_module as f
    import quant_module as q

    # C1 分位/档位工具与 fund 同口径(防两份漂移)
    same = all(q._inv_grade(r) == f._grade(r)
               for r in [None, 0.0, 0.01, 0.34, 0.35, 0.36, 0.5, 0.64, 0.65, 0.66, 1.0])
    ck(same, "_inv_grade 与 fund._grade 逐值同口径")
    series = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    ck(q._inv_pct(series, 3.0) == f._pct_rank(series, 3.0), "_inv_pct 与 fund._pct_rank 同口径")
    ck(q._inv_pct([1.0, 2.0], 1.0) is None, "序列不足 5 点 → 分位 None(不硬算)")

    def ck_rows(vals):
        return [{"date": "2026-01-%02d" % (i + 1), "qty": v, "change": None}
                for i, v in enumerate(vals)]

    def dual_with(ck_vals, social):
        """用 monkeypatch 方式构造 social 块, 避免依赖真实数据。"""
        old = q._inv_social_block
        q._inv_social_block = lambda code: social
        try:
            return q._inv_dual("XX", ck_rows(ck_vals), 180, "test")
        finally:
            q._inv_social_block = old

    LOW, HIGH = ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 9)      # 末位高分位 → 高
    _lo = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    ck_high = _lo[:]                      # 末位=10 → 分位 1.0 → 高
    ck_low = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]   # 末位=1 → 分位 0.1 → 低
    # ⚠ 别凭"看着像中间"就写数据: [1,2,9,10,3,4,5,6,7,8] 的末位 8 → 分位 0.80 → **高**
    #   (本次第一版就这么写错了)。要"中"必须让末位的分位落在 (0.35, 0.65)。
    ck_mid = [1, 2, 3, 4, 5, 7, 8, 9, 10, 6]   # 末位=6 → 分位 0.60 → 中
    soc_low = {"ok": True, "grade": "低", "pct_rank": 0.1, "label": "社会库存",
               "latest": 1, "latest_date": "d", "count": 10, "dir": "降", "chg_pct": -3.0}
    soc_high = dict(soc_low, grade="高", pct_rank=0.9, dir="升", chg_pct=3.0)
    soc_mid = dict(soc_low, grade="中", pct_rank=0.5, dir="平", chg_pct=0.0)
    soc_none = {"ok": False, "error": "无数据"}

    d = dual_with(ck_low, soc_low)
    ck(d["grade"] == "低" and d["mode"] == "双口径共振", "规则① 同档低 → 低/共振", d["grade"] + "|" + d["mode"])
    d = dual_with(ck_high, soc_high)
    ck(d["grade"] == "高" and d["mode"] == "双口径共振", "规则② 同档高 → 高/共振", d["grade"] + "|" + d["mode"])
    d = dual_with(ck_high, soc_low)
    ck(d["grade"] == "矛盾" and "背离" in d["mode"], "规则③ 一低一高 → 矛盾/背离", d["grade"] + "|" + d["mode"])
    d = dual_with(ck_mid, soc_low)
    ck(d["grade"] == "中" and d["mode"] == "口径分歧", "规则④ 一方中性 → 中/分歧", d["grade"] + "|" + d["mode"])
    d = dual_with(ck_low, soc_none)
    ck(d["grade"] == "低" and d["mode"] == "仅仓单口径", "规则⑤ 只仓单 → 用仓单档", d["grade"] + "|" + d["mode"])
    ck("社会库存" in d["note"], "规则⑤ 的 note 点名缺的是社会库存")
    d = dual_with([], soc_low)
    ck(d["grade"] == "低" and d["mode"] == "仅社会库存口径", "规则⑥ 只社会 → 用社会档", d["grade"] + "|" + d["mode"])
    ck("仓单" in d["note"], "规则⑥ 的 note 点名缺的是仓单")
    d = dual_with([], soc_none)
    ck(d["grade"] == "不明" and d["mode"] == "无口径", "规则⑦ 两侧全无 → 不明/无口径", d["grade"] + "|" + d["mode"])
    ck(set(d["available"]) == set(), "两侧全无时 available 为空")

    # C3 矛盾**不进单边判定**(核心安全性质: 三个指标映射里只有 low_i/high_i 才出单边)
    vd = f._strategy_verdict("矛盾", "低", "贴水")
    ck("单边" not in vd.get("action", ""), "库存=矛盾时不产出单边动作", vd.get("action", ""))
    # 【★ 断言要写"性质"而不是"我猜的那个具体字符串"】第一版写 `== "未共振"`
    #   结果红了 —— 实际行为是正确的: 矛盾**不占票**(bull/bear 都不算), 剩下
    #   利润低+基差贴水 2 票 ⇒ "部分共振(2/3 偏多)"。要守的是"不出单边"这条性质。
    ck(vd.get("partial") is True, "库存=矛盾落入'部分共振'分支(非单边、非降级)",
       str(vd.get("partial")))
    vd2 = f._strategy_verdict("低", "低", "贴水")
    ck(vd2.get("action") == "单边做多", "库存=低仍可触发单边做多(未被误伤)", vd2.get("action", ""))

    # C4 仓单 0 值警示
    z = q._inv_ck_zero_warn(ck_rows([5, 4, 3, 0, 0, 0]))
    ck(z["warn"] is not None, "末位为 0 → 产出警示文本")
    ck(z["zero_streak"] == 3, "连续 0 天数正确", str(z["zero_streak"]))
    ck(z["last_nonzero"] == 3, "上次非零值正确", str(z["last_nonzero"]))
    ck(abs(z["zero_pct"] - 50.0) < 0.01, "历史 0 占比正确", str(z["zero_pct"]))
    ck(q._inv_ck_zero_warn(ck_rows([5, 4, 3, 2, 1, 9]))["warn"] is None, "末位非 0 → 无警示")
    ck(q._inv_ck_zero_warn([])["warn"] is None, "空序列不误报警示")
    # ★ 0 值**保留**参与分位(用户拍板), 不被剔除
    blk = q._inv_ck_block(ck_rows([5, 4, 3, 0, 0, 0]), 180, "s")
    ck(blk["latest"] == 0, "0 值原样保留在 latest(未被剔除)", str(blk["latest"]))
    ck(blk["count"] == 6, "0 值仍计入序列长度", str(blk["count"]))

    # C5 四象限交叉验证
    ck(q._inv_cross("升", soc_high)["mode"] == "双口径共振", "方向同向 → 共振")
    ck("背离" in q._inv_cross("升", soc_low)["mode"], "方向反向 → 背离")
    ck(q._inv_cross("升", soc_mid)["mode"] == "口径分歧", "一方持平 → 分歧")
    ck(q._inv_cross("升", soc_none)["mode"] == "仅仓单口径", "无社会方向 → 仅仓单")

    print()
    print("=== D. 真实数据 + 端到端字段 ===")
    from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES
    rows = []
    for code in sorted(ACTIVE_VARIETIES):
        inv = q._fetch_inventory(code, 180, batch=True)
        rows.append((code, q._inv_dual(code, (inv.get("rows") or []) if inv.get("ok") else [],
                                       180, inv.get("source", "") if inv.get("ok") else "")))
    with_social = sum(1 for _, d in rows if d["social"].get("ok"))
    ck(with_social >= 15, "全池有社会/工厂库存的品种 ≥15", "%d/21" % with_social)
    ck(all(d["grade"] in ("低", "中", "高", "矛盾", "不明") for _, d in rows),
       "全池综合档位都在合法取值内")
    ck(all(d["mode"] for _, d in rows), "每个品种都给出口径关系")
    bad_zero = [c for c, d in rows if d["ck"].get("latest") == 0 and not d["warn"]]
    ck(not bad_zero, "所有仓单归零的品种都带警示标签", ",".join(bad_zero) or "无归零")

    # D2 接口契约(直调视图函数, 用 flask test client + 关鉴权)
    import os
    os.environ.setdefault("AGENTSENSE_AUTH_DISABLE", "1")
    try:
        import web_app

        app = web_app.app
        app.config["TESTING"] = True
        with app.test_client() as cli:
            r = cli.get("/api/quant/inventory?variety=EB&days=180")
            ck(r.status_code == 200, "/api/quant/inventory 返回 200", str(r.status_code))
            j = r.get_json() or {}
            ck("inventory" in j and "cycle" in j, "④卡 原字段 inventory/cycle 保留")
            ck("social" in j and "composite" in j, "④卡 新增字段 social/composite")
            comp = j.get("composite") or {}
            ck(set(comp) >= {"grade", "mode", "note", "warn", "available"},
               "composite 字段齐全", ",".join(sorted(comp)))
            ck((j.get("cycle") or {}).get("social") is not None, "cycle 内含 social 交叉验证")
            ck((j.get("cycle") or {}).get("cross") is not None, "cycle 内含 cross")
            r2 = cli.get("/api/fund/strategy?variety=EB")
            ck(r2.status_code == 200, "/api/fund/strategy 返回 200", str(r2.status_code))
            j2 = r2.get_json() or {}
            inv = j2.get("inv") or {}
            ck("grade" in inv and "mode" in inv, "①卡 库存维度给综合档位与口径关系")
            ck(inv.get("social") is not None and inv.get("ck") is not None,
               "①卡 库存维度带双口径明细")
            ck(j2.get("verdict", {}).get("action") is not None, "①卡 三指标映射仍正常出结论")
    except Exception as e:  # noqa: BLE001
        ck(False, "接口契约检查执行完成", "%s: %s" % (type(e).__name__, e))

    print()
    print("=" * 62)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项:")
        for x in FAIL:
            print("  ★ " + x)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
