# -*- coding: utf-8 -*-
"""更正 `probe/_weread_session_check.py`：登录态判据改用**不受风控影响**的接口。

═══ 为什么要改（2026-10-16 实测翻车）═══
原脚本只打 `/web/mp/articles`，按 `-2012` 得出「**会话超时 → 需重新扫码**」——
**这个结论是错的**：同一份 cookie 打 `/web/shelf/sync` 是 **HTTP 200 / 411 本书 / 无 errCode**
⇒ **登录态有效**，问题在 mp 那个接口上（先 -2012、后 -2041 风控/废弃）。

⇒ 修正为**三点体检**，并**分开下结论**：
    ① `/web/shelf/sync`   —— 登录态判据（外部资料称不受 mp 那层验证码影响）
    ② `/web/mp/articles`  —— 旧清单接口（已实测 -2041，疑似废弃）
    ③ `/web/mp/cover`     —— 替代接口（已验证可用，返回最新一篇 + reviewId）
  判据优先级：**① 定"会话有没有事"，②/③ 定"接口能不能用"** ——
  绝不能用 ② 的错误码去推断 ① 的状态（这正是本轮踩的坑）。
"""
from __future__ import annotations

import json
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "probe" / "_weread_session_check.py"
src = P.read_text(encoding="utf-8")
report = []

# ── A. 在 step2_probe 之前插入"登录态判据"函数 ──────────────────
A = "def step2_probe(cks: list[dict]) -> dict:\n"
assert src.count(A) == 1, ("A", src.count(A))
NEWF = '''def step1b_session_verdict(cks: list[dict]) -> dict:
    """★ 登录态判据：用 **/web/shelf/sync**（不受 mp 那层风控验证码影响）。

    【为什么单列】原脚本只看 /web/mp/articles 的错误码 → 把 **接口层** 的问题
      （-2041 风控/废弃）误判成 **会话层** 的问题（"会话超时，去重新扫码"）。
      实测同一份 cookie：shelf/sync 正常（411 本书），mp/articles 却是 -2041。
    ⇒ 判"会话有没有事"必须用一个**干净的、不受目标接口风控影响**的接口。
    """
    import requests
    hdr = "; ".join("%s=%s" % (c["name"], c["value"]) for c in cks)
    r = requests.get(BASE + "/web/shelf/sync", timeout=45,
                     headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                              "Referer": BASE + "/", "Cookie": hdr})
    body = {}
    try:
        body = r.json() or {}
    except Exception:
        pass
    code = body.get("errCode", body.get("errcode"))
    books = body.get("books") or []
    ok = (code is None or code == 0) and bool(books)
    return {"ok": ok, "code": code, "books": len(books), "status": r.status_code}


'''
src = src.replace(A, NEWF + A, 1)
report.append("  ✓ A 新增 step1b_session_verdict（登录态判据）")

# ── B. 主流程：先判会话、再判接口，分开下结论 ────────────────────
B = """    inv = step1_inventory()
    res = step2_probe(inv["cookies"])"""
assert src.count(B) == 1, ("B", src.count(B))
NB = """    inv = step1_inventory()
    sess = step1b_session_verdict(inv["cookies"])
    print()
    print("=" * 74)
    print("①b ★ 登录态判据（/web/shelf/sync —— 不受 mp 那层风控影响）")
    print("=" * 74)
    print("  HTTP %s   errCode=%s   books=%d" % (sess["status"], sess["code"], sess["books"]))
    if sess["ok"]:
        print("  ✔ **会话有效** ⇒ 若下面 mp 接口报错，那是**接口层**问题，不是会话问题！")
    else:
        print("  ✘ 会话无效/未登录 ⇒ 需要重新扫码（node probe/_weread_login.mjs）")
    res = step2_probe(inv["cookies"])"""
src = src.replace(B, NB, 1)
report.append("  ✓ B 主流程先判会话、再判接口")

# ── C. 结论段：把"接口报错"与"会话失效"分开 ─────────────────────
C = '''    print("=" * 74)
    print("结论：%s" % {"ok": "会话正常", "dead": "会话超时 → 需重新扫码",
                     "bad": "凭据无效 → 查/换 cookies.json",
                     "unknown": "返回异常 → 看上面原文"}[res["verdict"]])
    print("=" * 74)
    return 0 if res["verdict"] == "ok" else 1'''
assert src.count(C) == 1, ("C", src.count(C))
NC = '''    print("=" * 74)
    if not sess["ok"]:
        print("结论：**会话无效** → 需重新扫码（node probe/_weread_login.mjs）")
    elif res["verdict"] != "ok":
        print("结论：**会话有效，但 mp 接口不可用**（errCode=%s）" % res["errCode"])
        print("  ⇒ 不是"要天天扫码"，是**接口层**问题。旧清单接口 /web/mp/articles 已实测")
        print("     返 -2041（风控/废弃）⇒ 应改用 /web/mp/cover + /web/mp/content：")
        print("     probe/_weread_cover_probe.py / probe/_weread_content_analyze.py")
    else:
        print("结论：会话正常、接口可用")
    print("=" * 74)
    return 0 if (sess["ok"] and res["verdict"] == "ok") else 1'''
src = src.replace(C, NC, 1)
report.append("  ✓ C 结论段分开报会话/接口")

# ── D. 顶部文档补一句更正说明 ───────────────────────────────────
D = '⚠ **探测自我污染纪律**：本接口虽不限制单次频率，但**频繁轮询会被判异常**。'
assert src.count(D) == 1, ("D", src.count(D))
ND = (D + '''
  ⚠ **【2026-10-16 更正】判"会话有没有事"看 `/web/shelf/sync`，别用 mp 接口的错误码**：
    实测同一份 cookie，shelf/sync 正常（411 本书）而 mp/articles 返 -2041
    ⇒ 用后者推前者会把**接口层**问题误判成**会话层**问题（本脚本第一版就这么错的）。''')
src = src.replace(D, ND, 1)
report.append("  ✓ D 顶部补更正说明")

P.write_text(src, encoding="utf-8", newline="")
print("\n".join(report))
print("  → %s" % P)
