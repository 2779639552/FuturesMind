# -*- coding: utf-8 -*-
"""修「一句话核心观点」被第二节『分歧根源』覆盖（2026-10-19 实测发现）。

【现象】10-11 周报重生成后，观点列平均 21 字、中位 **6 字**，大量行是
        「数据口径不同」「关注权重不同」「时间尺度不同」。
【根因】`_parse_llm_views_table(body)` 收的是 **LLM 全文**，而第一节、第二节**都是表格**
        且首列都是品种代码：
            第一节 6 列：品种|发行方|方向|置信度|观点|头寸  → cells[-2]/[-1] 恰好正确
            第二节 5 列：品种|分歧状态|各家观点与逻辑|分歧焦点|分歧根源
                        → cells[-1] = **分歧根源**、len<6 ⇒ opinion=cells[-1] 走了 else 分支
        第二节出现在后面 ⇒ **逐品种覆盖**第一节的正确观点（且不报错，
        因为「数据口径不同」看起来也是句人话）。
【修法】① 只解析第一节（遇「二、」标题截断）；② 用**方向列**（cells[2]）做结构校验
        —— 第二节的 cells[2] 是「各家观点与逻辑」长文本，必然不含方向词 ⇒ 挡掉；
        ③ 观点/头寸改按**固定列序**取（cells[4]/cells[5]），不再靠 -2/-1 猜列数。
【EOL】web_app.py 为 CRLF。
"""
from pathlib import Path

P = Path("web_app.py")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "EOL 非纯 CRLF"
t = raw.decode("utf-8").replace("\r\n", "\n")

applied = []


def sub(old, new, label):
    global t
    n = t.count(old)
    assert n == 1, "★ [%s] 锚点命中 %d 次" % (label, n)
    t = t.replace(old, new, 1)
    applied.append(label)


OLD = '''    views: dict[str, str] = {}
    trades: dict[str, str] = {}
    if not body:
        return views, trades
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        m = _re.match(r"^([A-Z0-9]{1,6})(?:\\(|$)", cells[0].strip())
        if not m:
            continue
        code = m.group(1)
        # 跳过分隔行(|---|---|...)
        if all(set(c) <= {"-", ":", " "} for c in cells):
            continue
        if len(cells) >= 6:
            opinion, trade = cells[-2], cells[-1]
        else:
            opinion, trade = cells[-1], ""
        opinion = opinion.strip().replace("|", "/")
        trade = trade.strip().replace("|", "/")
        if opinion and opinion not in ("—", "-"):
            views[code] = opinion
        if trade and trade not in ("—", "-"):
            trades[code] = trade
    return views, trades'''

NEW = '''    views: dict[str, str] = {}
    trades: dict[str, str] = {}
    if not body:
        return views, trades

    # 【★ 2026-10-19 只解析第一节】本函数收的是 **LLM 全文**，而第一节、第二节都是表格、
    #   首列都是品种代码。原先不截断 ⇒ 第二节（5 列）的 `cells[-1]`（= 分歧根源，
    #   如「数据口径不同」）**逐品种覆盖**了第一节的正确观点，中位数掉到 6 字且不报错。
    #   ⇒ 遇「二、」标题即止（兼容 ## / ### / 无编号写法）。
    _m2 = _re.search(r"^\\s*#{1,4}\\s*二[、.．]", body, _re.M)
    seg = body[:_m2.start()] if _m2 else body

    # 方向列取值（宽松）：用于识别"这一行到底是不是第一节那 6 列结构"。
    #   第二节的 cells[2] 是「各家观点与逻辑」长文本 ⇒ 必然不匹配 ⇒ 被挡掉。
    def _looks_dir(x: str) -> bool:
        x = (x or "").strip()
        return bool(x) and len(x) <= 6 and any(k in x for k in ("多", "空", "中性", "震荡"))

    for line in seg.splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        m = _re.match(r"^([A-Z0-9]{1,6})(?:\\(|$)", cells[0].strip())
        if not m:
            continue
        code = m.group(1)
        # 跳过分隔行(|---|---|...)
        if all(set(c) <= {"-", ":", " "} for c in cells):
            continue
        # 【结构校验】方向列必须在 cells[2]（第一节：品种|发行方|方向|置信度|观点|头寸）。
        #   ⚠ 这不是"锦上添花"：少了它，第二节的行会以「分歧根源」冒充观点且**静默覆盖**。
        if not _looks_dir(cells[2]):
            continue
        # 【按固定列序取】原先 `cells[-2]/cells[-1]` 依赖"恰好 6 列"，列数一变就错位。
        opinion = cells[4] if len(cells) >= 5 else ""
        trade = cells[5] if len(cells) >= 6 else ""
        opinion = opinion.strip().replace("|", "/")
        trade = trade.strip().replace("|", "/")
        if opinion and opinion not in ("—", "-"):
            views[code] = opinion
        if trade and trade not in ("—", "-"):
            trades[code] = trade
    return views, trades'''

sub(OLD, NEW, "① 只解析第一节 + 方向列结构校验 + 固定列序")

out = t.replace("\n", "\r\n").encode("utf-8")
assert out.count(b"\n") == out.count(b"\r\n"), "出口 EOL 破坏"
P.write_bytes(out)
print("  已应用 %d 处：" % len(applied))
for x in applied:
    print("   ✔", x)
