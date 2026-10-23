# -*- coding: utf-8 -*-
"""解耦「显示长度」与「prompt 预算」—— 观点显示完全不再截断。

【问题】`_merge_same_source_items` 的 `text_cap` **同时**管两件事：
  ① `items[i]["opinion"]` → 总表「一句话核心观点」列（**显示**）
  ② 同一份 opinion 还会被塞进 LLM prompt（**预算**，prompt 侧有 28000 限制）
  ⇒ 为保预算就得截显示（周报 21 行里 14 行带 `…`），为了显示就得超预算。**绑死了。**

【解法】把两者**分开取值**：
  · `items` 里存**完整** opinion（text_cap=0）⇒ 显示端拿到的是全量；
  · **prompt 组装时**才做 `[:320]` 材料精简 ⇒ 预算仍与改前相当（实测周报 17860 字符）。
  ⇒ 显示完整 ✓ + 预算不超 ✓，不必二选一。
  ★ 附带好处：某品种因预算被略过时，它的显示列会**回退到 items 的完整拼接**
    （总表逻辑是"LLM 提炼优先、缺失回退 items"），所以**略过 ≠ 显示缺失**。

⚠ trade 不参与 prompt（payload 只用 opinion）⇒ trade_cap 放开**零预算影响**。
"""
from pathlib import Path

P = Path("web_app.py")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "应为纯 CRLF"
t = raw.decode("utf-8")
report: list[str] = []


def sub(old: str, new: str, tag: str, n: int = 1):
    global t
    o = old.replace("\n", "\r\n")
    assert t.count(o) == n, ("★锚点未命中", tag, t.count(o))
    t = t.replace(o, new.replace("\n", "\r\n"), n)
    report.append("  ✔ %s" % tag)


# ── ① items 存完整文本（显示端不再截）────────────────────────
sub('''    # 【2026-10-16 预算约束】text_cap 决定进 LLM prompt 的材料长度：
    #   payload = join(f"【品种】机构…\\n要点: {opinion}")，且 prompt 侧有 `payload[:28000]`。
    #   取值依据：单篇 opinion 产出上限 306 字（max_len=360 实测）⇒ cap 必须 ≥ 306 才保住单篇；
    #   同时 21 品种 × 4 机构 × 320 = 26880 < 28000，留有余量。
    #   ⚠ 取 360 会到 30240 > 28000 ⇒ 后面的品种材料被切（原注释"200 字仍在 28000 以内"就是这个约束）。
    #   ⚠ trade_cap 不必同步收紧：payload 组装**只用 opinion、不用 trade**。
    for rec in _merge_same_source_items(recs, flatten=True, text_cap=320, trade_cap=240):''',
    '''    # 【2026-10-16 ★ 显示与预算解耦】原 `text_cap=200` 是**为 prompt 预算**设的
    #   （注释原文：21 品种 × 4 机构 × 200 字仍在 payload 28000 以内），
    #   但它同时把**总表显示**的观点也截了 ⇒ 为了省预算而牺牲显示。
    #   ⇒ 这里改为 **0（不限）**：items 存**完整**文本，只作**显示**用；
    #     进 prompt 的材料在组装处单独 `[:320]` 精简（见下方 payload 组装），预算与改前相当。
    #   ★ 附带好处：若某品种因预算被略过，总表会**回退到 items 的完整拼接**
    #     （总表逻辑 = LLM 提炼优先、缺失回退 items）⇒ 「略过」不等于「显示缺失」。
    #   ⚠ trade_cap 放开**零预算影响**：payload 组装只用 opinion、不用 trade。
    for rec in _merge_same_source_items(recs, flatten=True, text_cap=0, trade_cap=0):''',
    "① items 存完整文本（text_cap/trade_cap → 0）")

# ── ② prompt 侧改为按预算精简材料 ────────────────────────────
sub('''    payload = "\\n\\n".join(
        f"【{i['variety']}】{i['source']}{_titles_label(i)} 方向:{i['direction']}"
        f"(置信度 {i['confidence']})\\n要点: {i['opinion']}"
        for i in items
    )''',
    '''    # 【2026-10-16 ★ 材料精简放在这里做，而不是在 items 里截】
    #   同一份 opinion 既要"完整显示"又要"进 prompt"，两者长度需求不同：
    #     显示 ⇒ 越全越好（用户在读结论）；prompt ⇒ 有 28000 字符预算（见下方 _fit_prompt_payload）。
    #   放在这里 `[:320]` 精确复现原 text_cap=200 时期的 payload 量级（实测周报约 17860 字符），
    #   而 items 本身保持完整 ⇒ **显示与预算互不牵制**。
    _MAT_CAP = 320
    payload = "\\n\\n".join(
        f"【{i['variety']}】{i['source']}{_titles_label(i)} 方向:{i['direction']}"
        f"(置信度 {i['confidence']})\\n要点: {i['opinion'][:_MAT_CAP]}"
        + ("…" if len(i["opinion"]) > _MAT_CAP else "")
        for i in items
    )''',
    "② prompt 材料按预算精简 [:320]（显示端不受影响）")

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
