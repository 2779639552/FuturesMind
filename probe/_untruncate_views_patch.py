# -*- coding: utf-8 -*-
"""去掉总表「一句话核心观点」「头寸建议」的字数硬截断（2026-10-16 用户报障）。

【用户报障】部分观点被截断（如 10.14 周度中的 LU 的一句话核心观点）。

【实测证据】`research_weekly/2026-10-11.md`（该周含 10-14）里 LU 行结尾:
    …综述：中性，LU2611区间5500~5750；利多：柴油上行支撑估值(逻…
                                                        ↑ 硬断在括号中间
——而且是**两层截断叠加**：每家 opinion 先 `[:40]`，多家拼接后再 `[:80]`。

【为什么原来要截】注释写的是"保持一句话观感"。但代价是**数据层就丢了信息**（落盘 md 也截），
  而这张表是表格(有列宽、能换行) ⇒ 截断纯粹是信息损失，不是布局必需。
  ⇒ 改为：**数据层保完整 + 显示层收敛**（前端默认 3 行 + 展开，见 web_template.html 侧改动）。

⚠ 清洗**必须保留**：
  · `\\n` → 空格：markdown 表格单元格里换行会把表**劈成两行**（整表崩坏）；
  · `|` → `/`：竖线是 markdown 单元格分隔符，不换会把**后面的列全部挤歪**。
"""
from pathlib import Path

P = Path("web_app.py")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "web_app.py 应为纯 CRLF"
t = raw.decode("utf-8")
report: list[str] = []


def sub(old: str, new: str, tag: str, n: int = 1):
    global t
    o = old.replace("\n", "\r\n")
    assert t.count(o) == n, ("★锚点未命中", tag, t.count(o))
    t = t.replace(o, new.replace("\n", "\r\n"), n)
    report.append("  ✔ %s" % tag)


# ── ① LLM 提炼的「一句话核心观点」不再截 ─────────────────────
sub('''        view = (llm_views.get(c) or "").strip()
        if view and view != "—" and len(view) > 60:  # LLM 观点超长时截断,保持"一句话"观感
            view = view[:60] + "…"''',
    '''        view = (llm_views.get(c) or "").strip()
        # 【2026-10-16 用户报障「观点被截断」】此处原先 `view[:60] + "…"`。
        # 去掉硬截断：截断让**数据层就丢信息**（落盘 md 也截、导出 PDF 也截），
        # 而这张表是表格(有列宽、td 能换行) ⇒ 截断纯属信息损失。
        # 改为「数据层完整 + 显示层收敛」：前端对长文本列默认 3 行 + 「展开全文」按钮
        # （见 web_template.html 的 _decorateViewTable）。''',
    "① 去掉 LLM 观点的 [:60] 截断")

# ── ② 回退路径：每家 opinion 不再截到 40 字 ─────────────────
sub('''                op = str(x.get("opinion") or "").strip().replace("\\n", " ").replace("|", "/")
                views_tmp.append(op[:40] if op else "—")
            view = " ; ".join(v for v in views_tmp if v != "—") or "—"
            if len(view) > 80:
                view = view[:80] + "…"''',
    '''                # ⚠ 清洗必须保留：\\n 会把 markdown 表格劈成两行；| 是单元格分隔符，
                #   不替换会让后面的列全部错位（这是"能删/不能删"的分界）。
                op = str(x.get("opinion") or "").strip().replace("\\n", " ").replace("|", "/")
                # 【2026-10-16】原先 `op[:40]` —— 每家机构只留 40 字(LU 那条就是被它先砍一刀)
                views_tmp.append(op if op else "—")
            view = " ; ".join(v for v in views_tmp if v != "—") or "—"
            # 【2026-10-16】原先再把拼接结果截到 80 字 ⇒ 与上面的 40 字形成**两层叠加**截断，
            #   实测把「利多：柴油上行支撑估值(逻辑…」硬断在括号中间。现不再截。''',
    "② 去掉回退路径的 op[:40] + view[:80] 两层截断")

# ── ③ LLM 头寸建议不再截 ───────────────────────────────────
sub('''        trade = (llm_trades.get(c) or "").strip()
        if trade and trade != "—" and len(trade) > 60:
            trade = trade[:60] + "…"''',
    '''        trade = (llm_trades.get(c) or "").strip()
        # 【2026-10-16】同 ①：去掉 [:60] 硬截断（头寸建议是操作依据，半句会误导）''',
    "③ 去掉 LLM 头寸建议的 [:60] 截断")

# ── ④ 回退路径的头寸拼接不再截 ─────────────────────────────
sub('''            trade = "，".join(trades) or "—"
            if len(trade) > 80:
                trade = trade[:80] + "…"''',
    '''            trade = "，".join(trades) or "—"
            # 【2026-10-16】去掉 [:80] 硬截断（同上：多家机构的建议归属必须完整可辨）''',
    "④ 去掉头寸拼接的 [:80] 截断")

# ── ⑤ 观点延续文案的两处截断 ───────────────────────────────
sub('''                # 取第一句(按句号/分号切),延续文案保持"一句话"观感
                first_sent = re.split(r"[。；;\\n]", op)[0].strip() if op else ""
                body_txt = (first_sent or str(prev.get("direction") or "中性"))[:45]''',
    '''                # 【2026-10-16】仍取第一句(那是**语义**切分，不是字数截断)，但**不再硬截 45 字**
                #   —— 原 [:45] 会把第一句也砍断（第一句长于 45 字时）。显示长度交给前端收敛。
                first_sent = re.split(r"[。；;\\n]", op)[0].strip() if op else ""
                body_txt = first_sent or str(prev.get("direction") or "中性")''',
    "⑤ 去掉观点延续摘要的 [:45] 截断")

sub('''                t = str(prev.get("trade") or "").strip().replace("|", "/")
                trade = (t[:60] + "…") if len(t) > 60 else (t or "—")''',
    '''                t = str(prev.get("trade") or "").strip().replace("|", "/")
                # 【2026-10-16】去掉 [:60] 硬截断
                trade = t or "—"''',
    "⑥ 去掉观点延续头寸的 [:60] 截断")

out = t.encode("utf-8")
assert out.count(b"\n") == out.count(b"\r\n"), "写回破坏了 CRLF"
P.write_bytes(out)
b = P.read_bytes()
print("=" * 74)
print("  web_app.py 落盘完成")
print("=" * 74)
print("\n".join(report))
print()
print("  %d → %d 字节；CRLF=%d bareLF=%d"
      % (len(raw), len(b), b.count(b"\r\n"), b.count(b"\n") - b.count(b"\r\n")))
