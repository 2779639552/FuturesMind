# -*- coding: utf-8 -*-
"""修护栏三处（变异自检暴露）：
  ① AST 检测漏了 `x[:N]` 形态 —— 它在 AST 里是 **`ast.Slice(upper=Constant)`**，
     不是裸 `Constant`（`x[N]` 才是）。⇒ M1/M2 存活。
  ② 清洗计数用 `>= 3` 太松（实际有 4 处）⇒ 改成"**凡是清 `|` 的地方必须同时清 `\\n`**"
     这个**不变量**（与处数无关，且能抓"新增字段又漏清换行"）。
  ③ 前端没护栏保证「展开」按钮**真的绑了 click**（M10 把 'click' 改成 'noop' 照样绿）
     ⇒ 加一条"必须注册 click 监听"。
"""
from pathlib import Path

Q = Path("tests/test_views_no_truncation.py")
s = Q.read_text(encoding="utf-8")
report: list[str] = []


def sub(old: str, new: str, tag: str, n: int = 1):
    global s
    assert s.count(old) == n, ("★锚点未命中", tag, s.count(old))
    s = s.replace(old, new, n)
    report.append("  ✔ %s" % tag)


# ── ① AST 检测支持 `x[:N]`（ast.Slice）────────────────────────
sub('''    import ast
    caps = []          # (行号, 目标表达式, 截断长度)
    tree = ast.parse(s)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        sl = node.slice
        if not (isinstance(sl, ast.Constant) and isinstance(sl.value, int)):
            continue
        tgt = ast.unparse(node.value) if hasattr(ast, "unparse") else ""
        low = tgt.lower()
        if any(k in low for k in ("view", "opinion", "payload", "trade", "op")):
            caps.append((node.lineno, tgt, sl.value))
    # 允许的例外：分段/拼接类截断（非"观点文本"）
    allowed = {"payload" if False else ""}
    del allowed
    bad = [c for c in caps if c[2] in (40, 45, 60, 80, 28000)]''',
    '''    import ast
    caps = []          # (行号, 目标表达式, 截断长度)
    tree = ast.parse(s)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        sl = node.slice
        # ★★ 两种形态都要认：
        #     `x[N]`    → slice 是 ast.Constant(int)
        #     `x[:N]`   → slice 是 **ast.Slice(upper=Constant(int))**
        #   ⚠ 只认 Constant 会**漏掉 `x[:N]` 这种最常见写法**（变异自检实测：
        #     把 `view[:60]` 加回去，护栏照样全绿 —— M1/M2 存活就是这个原因）。
        n_val = None
        if isinstance(sl, ast.Constant) and isinstance(sl.value, int):
            n_val = sl.value
        elif isinstance(sl, ast.Slice):
            up = sl.upper
            if isinstance(up, ast.Constant) and isinstance(up.value, int):
                n_val = up.value
        if n_val is None:
            continue
        tgt = ast.unparse(node.value) if hasattr(ast, "unparse") else ""
        low = tgt.lower()
        if any(k in low for k in ("view", "opinion", "payload", "trade", "op")):
            caps.append((node.lineno, tgt, n_val))
    bad = [c for c in caps if c[2] in (40, 45, 60, 80, 28000)]''',
    "① AST 检测支持 ast.Slice（x[:N]）")

# ── ② 清洗判据改成"不变量"（清 | 必清 \n）────────────────────
sub('''    # **trade 侧也必须清洗换行** —— 这两行原先是 .replace("|","/") 单项，缺 \\n 清洗（本轮补上）
    nt = body.count('replace("\\\\n", " ").replace("|", "/")')
    assert nt >= 3, "★ 仍有字段只清竖线不清换行（共 %d 处做了双重清洗）" % nt''',
    '''    # ★★ 判据用**不变量**而不是计数：**凡是清 `|` 的地方必须同时清 `\\n`**。
    #   【为什么不用"计数 >= N"】处数会随字段增删而变（本项目实测有 4 处，我一开始按 3 写
    #     ⇒ 删掉其中一处仍 >= 3 ⇒ 护栏放行，变异存活）。不变量判据与处数无关，
    #     还能抓住"以后新增字段又只清竖线"这种同型回归。
    offenders = [
        ln.strip()[:110] for ln in body.splitlines()
        if 'replace("|", "/")' in ln and 'replace("\\\\n", " ")' not in ln
    ]
    assert not offenders, (
        "★ 有字段只清竖线、不清换行（markdown 表会被劈成两行）：%s" % offenders
    )''',
    "② 清洗判据改为不变量（清 | 必清 \\n）")

# ── ③ 前端：必须真的绑了 click ─────────────────────────────
sub('''    assert "classList.toggle('open')" in body, "没有切换展开态"
    assert "clamp-toggle" in body, "没建按钮"''',
    '''    assert "classList.toggle('open')" in body, "没有切换展开态"
    assert "clamp-toggle" in body, "没建按钮"
    # ★ 必须**真的注册 click 监听** —— 只建按钮不绑事件 = 点了没反应
    #   （变异自检实测：把 'click' 改成 'noop' 时，上面几条断言照样绿 ⇒ 补这一条）
    assert "addEventListener('click'" in body, "★ 按钮没注册 click 监听（点了不会展开）"''',
    "③ 补「按钮必须绑 click」")

Q.write_text(s, encoding="utf-8", newline="")
print("\n".join(report))
