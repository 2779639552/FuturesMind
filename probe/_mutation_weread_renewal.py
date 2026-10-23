# -*- coding: utf-8 -*-
"""变异自检：验证本轮护栏能否抓住「回退到旧行为」的各种改法。

每个变异：改源码 → 跑 tests/test_wechat_weread_cover.py → 必须**变红** → 复原 → 核对 md5。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

SRC = Path("wechat_weread_collector.py")
TEST = "tests/test_wechat_weread_cover.py"
ORIG = SRC.read_bytes()
ORIG_MD5 = hashlib.md5(ORIG).hexdigest()
PY = sys.executable

# (标题, 旧串, 新串)
MUTS = [
    ("M1 去掉 main 的续期调用（回到「从不续期」）",
     '    say("检查会话：")\n    if not renew_session():\n',
     '    say("检查会话：")\n    if False://n'),
    ("M2 续期后不写回 cookies.json（下次仍用旧密钥）",
     "    _persist_session_cookies()\n    return True",
     "    return True"),
    ("M3 _headers 又拼静态 Cookie 头（覆盖续期后的新密钥）",
     '            "Referer": BASE + "/"}',
     '            "Referer": BASE + "/", "Cookie": "wr_skey=stale"}'),
    ("M4 _get 不再按错误码自动续期重试",
     "    if code in _AUTH_CODES and renew_session(quiet=True):",
     "    if False and code in _AUTH_CODES:"),
    ("M5 清单回退成只取 cover（多篇枚举/回填全失效）",
     "        arts = fetch_articles(bid, offset=page * PAGE_SIZE)",
     "        arts = fetch_latest(bid)"),
    ("M6 daily 的时间闸失效（又把全部历史当新文）",
     "    cutoff = 0 if first_run else last_time",
     "    cutoff = 0"),
    ("M7 daily 没有数量上限",
     "    cap = limit if mode == \"backfill\" else (limit if first_run else max(limit, 60))",
     "    cap = 10 ** 9"),
    ("M8 恢复「只能取最新 1 篇」的旧文案",
     '        say("回填模式：每号最多取 %d 篇（列表通道 /web/mp/articles 已恢复翻页）"',
     '        say("⚠ 只能取最新 1 篇")  or say("回填模式：每号最多取 %d 篇（列表通道 /web/mp/articles 已恢复翻页）"'),
    ("M9 重新要求 wr_skey 才继续（把密钥过期误报成未登录）",
     '    if not {"wr_vid", "wr_rt"} <= names:',
     '    if "wr_skey" not in names:'),
]


def run_tests() -> tuple[bool, str]:
    r = subprocess.run([PY, "-m", "pytest", TEST, "-q", "--no-header", "-p", "no:warnings", "--tb=no"],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""


killed = survived = skipped = 0
try:
    for title, old, new in MUTS:
        txt = ORIG.decode("utf-8")
        if txt.count(old) != 1:
            print("  ⚠ 跳过（锚点命中 %d）：%s\n       old=%r" % (txt.count(old), title, old[:60]))
            skipped += 1
            continue
        SRC.write_bytes(txt.replace(old, new, 1).encode("utf-8"))
        ok, last = run_tests()
        if ok:
            print("  ✗ 存活：%s   [%s]" % (title, last))
            survived += 1
        else:
            print("  ✔ 击杀：%s   [%s]" % (title, last))
            killed += 1
finally:
    SRC.write_bytes(ORIG)
    now = hashlib.md5(SRC.read_bytes()).hexdigest()
    print("\n  md5 复原: %s" % ("✔ 一致" if now == ORIG_MD5 else "★不一致 %s != %s" % (now, ORIG_MD5)))

print("  合计：击杀 %d / 存活 %d / 跳过 %d" % (killed, survived, skipped))
sys.exit(1 if (survived or skipped) else 0)
