# -*- coding: utf-8 -*-
"""把「微信读书通道」的修复提示改成**两条路都给出**（桌面一键入口 + 命令行）。

【为什么改】用户习惯桌面 bat 一键入口（本项目已有 `relogin_weibo_xhs.bat` 先例，
本轮也交付了 `重登公众号读书通道.bat`），只给一行 node 命令不够顺手。
⚠ 必须保留 `_weread_login.mjs` 字样 —— probe/_e2e_channel_status.mjs 断言了它。
"""
from pathlib import Path

P = Path("web_app.py")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "web_app.py 应为纯 CRLF"
t = raw.decode("utf-8")

OLD = '''        "hint": "node probe/_weread_login.mjs" if wr_bad else "",
'''
NEW = '''        # 两条路都给：桌面一键入口（用户习惯） + 命令行（可只验证不扫码）
        "hint": ("桌面「重登公众号读书通道.bat」 或 node probe/_weread_login.mjs"
                 "（后者加 --check-only 可只验登录态）") if wr_bad else "",
'''

o = OLD.replace("\n", "\r\n")
n = NEW.replace("\n", "\r\n")
assert t.count(o) == 1, ("锚点命中", t.count(o))
t = t.replace(o, n, 1)

out = t.encode("utf-8")
assert out.count(b"\n") == out.count(b"\r\n"), "写回破坏了 CRLF"
P.write_bytes(out)
b = P.read_bytes()
print("  ✔ hint 已更新；%d → %d 字节；CRLF=%d bareLF=%d"
      % (len(raw), len(b), b.count(b"\r\n"), b.count(b"\n") - b.count(b"\r\n")))
print("  ✔ 仍保留 _weread_login.mjs（E2E 断言需要）:", "_weread_login.mjs" in t)
print("  ✔ 加了桌面入口:", "重登公众号读书通道.bat" in t)
