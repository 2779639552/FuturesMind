# -*- coding: utf-8 -*-
"""配套修：payload 预算从「静默硬切」改为「按条装载 + 显式标注」，
并把 text_cap 定到能保住单篇完整的值。

【为什么必须配套改】前一步把「观点提取」的上限从 150 放到 360（单篇产出 306 字），
  而 `text_cap` 决定了**进 LLM prompt 的材料长度**：
      payload = "\\n\\n".join(f"【品种】机构…\\n要点: {opinion}" for i in items)
      prompt += f"...\\n\\n{payload[:28000]}"        ← ★ 超预算就**从尾部硬切**
  原注释写明"21 品种 × 4 机构 × 200 字仍在 payload 28000 以内" —— 也就是说 200 是
  **按预算定出来的**，不是随手写的。盲目提到 360(21×4×360=30240) 会**超预算**
  ⇒ 排在后面的品种材料被切掉（而且是**静默**的，LLM 与用户都不知道）。
  ⇒ 修 A（放开观点）必须带修 B（保住预算），否则是"修一个、坏一个"。

【两处改动】
  ① `text_cap` 360 → 320：≥ 单篇产出上限 306（保住用户报的场景），
     且 21×4×320 = 26880 < 28000 留有余量。
     ⚠ trade_cap 不必同步收紧 —— payload 组装**只用 opinion、不用 trade**（见下），
     所以 trade 放宽到 240 不影响预算。
  ② `payload[:28000]` → 按条装载：装得下就装、装不下跳过，并在末尾**显式列出**被略过的品种。
     为什么不是"遇到装不下的就停"：那样一个长条目会把后面所有短条目一起挡掉；
     为什么必须标注：静默截断会让 LLM 以为"这些品种今天没研报"⇒ 可能编造或漏写。
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
    # ★ 锚点必须**从行首开始**（前一个字符是换行）—— 否则前导空格少于实际缩进时
    #   会**子串命中**，替换后行首残留空格、后续行缩进错乱（本轮就踩了一次）。
    assert t.count("\n" + o) == n or t.startswith(o), ("★锚点未含完整行首缩进（会子串命中）", tag)
    t = t.replace(o, new.replace("\n", "\r\n"), n)
    report.append("  ✔ %s" % tag)


# ── ① text_cap 回到预算安全值 ────────────────────────────────
sub('''    for rec in _merge_same_source_items(recs, flatten=True, text_cap=360, trade_cap=240):''',
    '''    # 【2026-10-16 预算约束】text_cap 决定进 LLM prompt 的材料长度：
    #   payload = join(f"【品种】机构…\\n要点: {opinion}")，且 prompt 侧有 `payload[:28000]`。
    #   取值依据：单篇 opinion 产出上限 306 字（max_len=360 实测）⇒ cap 必须 ≥ 306 才保住单篇；
    #   同时 21 品种 × 4 机构 × 320 = 26880 < 28000，留有余量。
    #   ⚠ 取 360 会到 30240 > 28000 ⇒ 后面的品种材料被切（原注释"200 字仍在 28000 以内"就是这个约束）。
    #   ⚠ trade_cap 不必同步收紧：payload 组装**只用 opinion、不用 trade**。
    for rec in _merge_same_source_items(recs, flatten=True, text_cap=320, trade_cap=240):''',
    "① text_cap 360 → 320（预算安全值，仍 ≥ 单篇 306）")

# ── ② payload 由"静默硬切"改为"按条装载 + 显式标注" ──────────
sub('''    if r_norm:
        _start, _end = _period_range(date, r_norm)
        _label = {"周报": "研报周区间(每周日起始)", "季报": "研报季度区间(该季首月 1 日起)"}[r_norm]
        prompt += f"---\\n{_label}:{_start} ~ {_end}\\n\\n{payload[:28000]}"
    else:
        prompt += f"---\\n研报日期:{date}\\n\\n{payload[:28000]}"''',
    '''    # 【2026-10-16】此处原为 `{payload[:28000]}` —— **静默从尾部硬切**：
    #   ① 会把某条材料切成半句（LLM 看到残缺要点）；
    #   ② 更糟的是**不告知**，LLM 以为后面的品种"今天没研报"⇒ 可能漏写甚至编造。
    #   ⇒ 改为按条装载：装得下就装、装不下跳过；被略过的品种**显式列出**。
    #   ⚠ 用"跳过"而不是"遇到装不下的就停"：后者会被一个长条目挡住后面所有短条目。
    _BUDGET = 28000

    def _fit_payload(blocks: list[str]) -> str:
        """按预算装载材料；被略过的**在末尾显式标注**（别让 LLM 以为那些品种没研报）。"""
        keep: list[str] = []
        used = 0
        dropped: list[str] = []
        for blk in blocks:
            if used + len(blk) + 2 > _BUDGET:
                head = blk.split("】", 1)[0].lstrip("【").strip()
                dropped.append(head or blk[:12])
                continue
            keep.append(blk)
            used += len(blk) + 2
        out = "\\n\\n".join(keep)
        if dropped:
            out += ("\\n\\n（注：以下品种的材料因超出长度预算未列入，请勿据此下结论：%s）"
                    % "、".join(sorted(set(dropped))))
        return out

    _payload_txt = _fit_payload(payload.split("\\n\\n"))
    if r_norm:
        _start, _end = _period_range(date, r_norm)
        _label = {"周报": "研报周区间(每周日起始)", "季报": "研报季度区间(该季首月 1 日起)"}[r_norm]
        prompt += f"---\\n{_label}:{_start} ~ {_end}\\n\\n{_payload_txt}"
    else:
        prompt += f"---\\n研报日期:{date}\\n\\n{_payload_txt}"''',
    "② payload 硬切 → 按条装载 + 显式标注被略过品种")

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
