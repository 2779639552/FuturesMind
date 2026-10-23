# -*- coding: utf-8 -*-
"""收尾三修：
  ① 【真 bug】trade 只清 `|` 没清 `\\n` ⇒ 含换行的头寸建议会把 markdown 表**劈成两行**；
  ② 【漏改】逐品种总结路径的 `payload[:28000]` 也是静默硬切 ⇒ 同样改成按预算装载；
  ③ 护栏自指：我的注释里写着 `max_len=150` ⇒ 判据改成只查**调用行**（排除注释）。
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


# ── ① trade 补 \n 清洗（两处）────────────────────────────────
sub('''            for x in it_list:
                t = str(x.get("trade") or "").strip().replace("|", "/")''',
    '''            for x in it_list:
                # 【2026-10-16 修】原先只清 `|`、**没清 `\\n`** —— 而 opinion 两样都清。
                # 不对称的后果：头寸建议含换行时，markdown 单元格里出现真换行
                # ⇒ **整张表被劈成两行**（列数错乱，且不报错）。
                # 这是老 bug（非本次引入），放开截断时一并补上。
                t = str(x.get("trade") or "").strip().replace("\\n", " ").replace("|", "/")''',
    "① trade（回退路径）补 \\n 清洗")

sub('''                t = str(prev.get("trade") or "").strip().replace("|", "/")''',
    '''                # 【2026-10-16 修】同上：补 \\n 清洗（原只清 `|`）
                t = str(prev.get("trade") or "").strip().replace("\\n", " ").replace("|", "/")''',
    "② trade（观点延续路径）补 \\n 清洗")

# ── ② 逐品种总结路径同样改按预算装载 ────────────────────────
sub('''        prompt += f"---\\n{_label}:{_start} ~ {_end}{_tail}\\n\\n{payload[:28000]}{metrics_block[:4000]}"
    else:
        prompt += f"---\\n研报日期:{date}\\n\\n{payload[:28000]}{metrics_block[:4000]}"''',
    '''        # 【2026-10-16】同日报路径：`payload[:28000]` 是**静默硬切**（会切半句、且不告知，
        #   本条路径按单品种取数、条数少，触发概率低但缺陷同型）⇒ 一并改为按预算装载。
        prompt += (f"---\\n{_label}:{_start} ~ {_end}{_tail}\\n\\n"
                   f"{_fit_prompt_payload(payload.split(chr(10) * 2))}{metrics_block[:4000]}")
    else:
        prompt += (f"---\\n研报日期:{date}\\n\\n"
                   f"{_fit_prompt_payload(payload.split(chr(10) * 2))}{metrics_block[:4000]}")''',
    "③ 逐品种路径 payload 硬切 → 按预算装载")

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
