# -*- coding: utf-8 -*-
"""同步 tests/test_page_tools_variety_state.py 对本次三项改动的依赖。

【为什么要改】该文件里 5 处写死了「② 四源取数状态」这一**已被用户要求删除**的卡：
  1) GTJA_MERGED_TITLE 常量写的是 '<h3>③ 指标总览 / 走势图 / 明细'（编号已升为 ②）
  2) div 配平用例断言两张卡标题都在（四源卡已删）
  3) 变异用例注释"在四源卡前插悬空 </div>"（已无四源卡）
  4) GTJA_DEFAULT_OPEN_CFG 的说明注释（下标含义已刷新）
  5) test_gtja_default_open_indices_match_card_order 依赖 i_sources（四源卡位置）

【改法】保留意图、只换锚点；第 5 条顺带**加强**：改为按 `.card` 真实序列核对下标，
  这样将来再增删卡导致配置指错模块时会立刻红（原实现只比两个标题的先后）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

T = Path(__file__).resolve().parent.parent / "tests" / "test_page_tools_variety_state.py"

EDITS = []

# ── 1) 常量：③ → ② ────────────────────────────────────────────────
EDITS.append((
    "GTJA_MERGED_TITLE 编号",
    "GTJA_MERGED_TITLE = '<h3>③ 指标总览 / 走势图 / 明细'",
    "GTJA_MERGED_TITLE = '<h3>② 指标总览 / 走势图 / 明细'   # 【2026-10-14】原③ —— ② 四源取数状态卡删除后升为②",
))

# ── 2) div 配平用例：两张卡 → 一张卡 ────────────────────────────────
EDITS.append((
    "div 配平用例锚点",
    """    # 两张顶层卡必须仍在 tab 内(未被挤出)
    # 卖方数据块的两张卡必须仍在 warehouse 容器内（未被挤到 </main> 或其它 tab 下）
    for t in ('<h3>② 四源取数状态', '<h3>③ 指标总览 / 走势图 / 明细'):
        assert t in src[i:j], "%s 不在 #tab-warehouse 区间内 —— 卡片被挤出容器了" % t""",
    """    # 卖方数据块现仅剩「② 指标总览」一张卡（「② 四源取数状态」已按用户要求删除, 2026-10-14），
    # 它必须仍在 warehouse 容器内（未被挤到 </main> 或其它 tab 下）。
    assert GTJA_MERGED_TITLE in src[i:j], (
        "%s 不在 #tab-warehouse 区间内 —— 卡片被挤出容器了" % GTJA_MERGED_TITLE)""",
))

# ── 3) 变异用例注释 ────────────────────────────────────────────────
EDITS.append((
    "变异用例注释",
    '    # 在四源卡前插一个悬空 </div> (模拟"删卡漏删闭合")\n',
    '    # 在首张卡前插一个悬空 </div> (模拟"删卡漏删闭合")\n',
))

# ── 4) 默认展开配置的说明注释（配置值不变）────────────────────────
EDITS.append((
    "默认展开配置注释",
    """# 【2026-10-14】页面迁入 warehouse ⇒ 配置键同步改(否则配置永远命中不到,
#   变成静默死配置)。下标语义不变: warehouse 现在**第一块**就是卖方数据。
GTJA_DEFAULT_OPEN_CFG = "'warehouse': [0, 1],"
""",
    """# 【2026-10-14】页面迁入 warehouse ⇒ 配置键同步改(否则配置永远命中不到,
#   变成静默死配置)。
# 【2026-10-14 同日】「② 四源取数状态」卡按用户要求删除后, 下标含义刷新为:
#   0 = ① 查询控制, 1 = ② 指标总览(原③) —— 恰好仍是「卖方数据块的两张卡」, 故 [0,1] 不变。
#   ⚠ 判据见下方 test_gtja_default_open_indices_match_card_order（按 .card 真实序列核下标）。
GTJA_DEFAULT_OPEN_CFG = "'warehouse': [0, 1],"
""",
))

# ── 5) 断言里的举例措辞 ───────────────────────────────────────────
EDITS.append((
    "举例措辞去四源",
    "    #    （漏了它，点一次「共 N 项」就会把刚看到的四源状态又收回去）\n",
    "    #    （漏了它，点一次「共 N 项」就会把刚看到的模块又收回去）\n",
))

# ── 6) 用例重写（按 .card 真实序列核下标）──────────────────────────
OLD_FUNC = '''def test_gtja_default_open_indices_match_card_order():
    """配置写的是下标 [0, 1]，得确认它们**确实是**剩余两张卡（四源 + 指标总览）。

    否则（例如将来把某张卡前移）配置会静默指向别的模块 —— 页面照常渲染，
    只是展开的不是用户想看的那两块，肉眼很难发现。
    """
    src = _src()
    i_sources = src.find("<h3>② 四源取数状态")
    i_merged = src.find(GTJA_MERGED_TITLE)
    assert -1 not in (i_sources, i_merged), "国君页剩余两张卡标题不全（四源/指标总览）"
    assert i_sources < i_merged, (
        "卡序位置 %d/%d ⇒ 下标 0/1 不再对应 四源状态/指标总览，"
        "请同步修改 _SEC_OPEN_BY_TAB['gtja']" % (i_sources, i_merged)
    )
'''
NEW_FUNC = '''def test_gtja_default_open_indices_match_card_order():
    """配置的下标 [0, 1] 必须**真的**指向「① 查询控制 / ② 指标总览」这两张卡。

    【2026-10-14 重写】原用例断言「四源卡在指标总览之前」。四源卡已按用户要求删除 ⇒
    改为**按 .card 真实序列**核对下标：warehouse 页第 0 张 `.card` 是 ① 查询控制、
    第 1 张是 ② 指标总览（原③）。

    这比原实现更强：原来只比两个标题的先后，若将来在 card[0] 之前插入新卡，
    「序号整体后移」这件事原用例看不出来（两个标题的相对先后没变），
    而本用例会立刻红 —— 配置指错模块时页面照常渲染，肉眼极难发现。
    """
    import re as _re
    src = _src()
    i = src.find('<div id="tab-warehouse" class="tab">')
    assert i != -1, "找不到 #tab-warehouse"
    j = src.find("</main>", i)
    clean = _re.sub(r"<!--.*?-->", "", src[i:j], flags=_re.S)
    cards = [m.start() for m in _re.finditer(r'<div class="card"[^>]*>', clean)]
    assert len(cards) >= 2, (
        "warehouse 页 .card 少于 2 张（实测 %d）⇒ _SEC_OPEN_BY_TAB 的 [0,1] 已无意义，"
        "请同步修改配置" % len(cards)
    )
    seg0 = clean[cards[0]:cards[1]]
    seg1 = clean[cards[1]:cards[2] if len(cards) > 2 else len(clean)]
    assert "① 查询控制" in seg0, (
        "warehouse 页第 0 张 .card 不是「① 查询控制」⇒ 下标 0 指错了模块。实际内容: %s"
        % _re.sub(r"<[^>]+>", " ", seg0)[:80]
    )
    title = GTJA_MERGED_TITLE.replace("<h3>", "")   # 「② 指标总览 / 走势图 / 明细 」
    assert title in seg1, (
        "warehouse 页第 1 张 .card 不是「%s」⇒ _SEC_OPEN_BY_TAB['warehouse']=[0,1] 指错了模块。"
        "实际内容: %s" % (title, _re.sub(r"<[^>]+>", " ", seg1)[:80])
    )
'''
EDITS.append(("用例重写", OLD_FUNC, NEW_FUNC))


def main() -> int:
    raw = T.read_bytes()
    assert raw.count(b"\r\n") == 0, "tests/*.py 应为 LF"
    h0 = hashlib.md5(raw).hexdigest()
    s = raw.decode("utf-8")
    print("md5(前) =", h0)
    print()
    for tag, old, new in EDITS:
        n = s.count(old)
        assert n == 1, "%s 锚点命中 %d（期望 1）" % (tag, n)
        s = s.replace(old, new, 1)
        print("  ✔ %s" % tag)
    T.write_text(s, encoding="utf-8", newline="")
    b = T.read_bytes()
    assert b.count(b"\r\n") == 0, "EOL 被破坏"
    print()
    t = b.decode("utf-8")
    print("[自检]")
    checks = [
        ("GTJA_MERGED_TITLE 已是②", "GTJA_MERGED_TITLE = '<h3>② 指标总览" in t),
        ("用例按 .card 序列核对", "cards = [m.start() for m in _re.finditer" in t),
        ("配置值仍为 [0, 1]", '''GTJA_DEFAULT_OPEN_CFG = "'warehouse': [0, 1],"''' in t),
    ]
    ok = True
    for tag, v in checks:
        print("  %-26s %s" % (tag, "✔" if v else "✘"))
        ok = ok and v
    # 硬判据：不得再有任何断言依赖已删卡的标题
    # 只留「把它当**活代码/活断言**引用」的行；记录删除的注释是合理的（含"删除"字样即排除）
    leftover = [l for l in t.splitlines()
                if "② 四源取数状态" in l and "删除" not in l and "原为" not in l]
    print("  %-26s %s" % ("残留引用已删卡标题的行", leftover if leftover else "无 ✔"))
    ok = ok and not leftover
    print()
    print("md5(后) =", hashlib.md5(b).hexdigest())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
