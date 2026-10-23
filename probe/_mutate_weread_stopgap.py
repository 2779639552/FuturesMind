# -*- coding: utf-8 -*-
"""变异自检：止血护栏能不能抓到回退。改坏 → 跑 → 复原 → 核 md5。

覆盖七种"很容易被改回去"的写法：
  ① 把 -2041 重新并进鉴权码；② 用 SystemExit 报错；③ 取消续期预算；
  ④ 去掉 cover 兜底保护；⑤ 降级标记提前落盘；⑥ dry-run 覆写真实结果；⑦ 覆盖式降级标记。
"""
import hashlib
import io
import subprocess
import sys

PY = "./venv/Scripts/python.exe"
SRC = "wechat_weread_collector.py"
TEST = "tests/test_weread_stopgap.py"

MUTS = [
    ("把 -2041 并回鉴权码（又会续期风暴）",
     "    if code in _SESSION_CODES and _auto_renew() and renew_session(quiet=True):",
     "    if code in _AUTH_CODES and _auto_renew() and renew_session(quiet=True):",
     "test_2041_does_not_trigger_renewal"),

    ("接口拒绝改回 SystemExit（整轮会被打断）",
     '                raise EndpointRefused(\n                    "列表接口拒绝 errCode=%s（会话有效，不是登录过期）" % code)',
     '                raise SystemExit("拒绝")',
     "test_no_systemexit_in_fetch_paths"),

    ("取消续期预算（每撞一次就续一次）",
     "        if _AUTO_RENEWS >= _MAX_AUTO_RENEWS:\n            return False",
     "        if False:\n            return False",
     "test_auto_renew_budget_is_one"),

    ("去掉 cover 兜底的 try（兜底失败又打断整轮）",
     "        try:\n            for a in fetch_latest(bid):",
     "        if True:\n            for a in fetch_latest(bid):",
     "test_collect_account_survives_cover_auth_error"),

    ("降级标记提前落盘（丢掉 +cover_failed）",
     '    acct["_degraded"] = degraded\n\n    say("   ⇒ 本轮取到',
     '    say("   ⇒ 本轮取到',
     "test_collect_account_survives_cover_auth_error"),

    ("dry-run 覆写真实结果文件",
     "    res_json = F_RESULT if not args.dry_run else F_RESULT.with_name(\"mp_collect_result.dryrun.json\")",
     "    res_json = F_RESULT",
     "test_dryrun_writes_separate_result_file"),

    ("降级标记改成覆盖式（丢失前一个原因）",
     '            degraded = (degraded + "+cover_failed") if degraded else "cover_failed"',
     '            degraded = "auth_failed"',
     "test_collect_account_survives_cover_auth_error"),
]


def md5(p):
    return hashlib.md5(io.open(p, "rb").read()).hexdigest()


def main() -> int:
    killed = 0
    for name, old, new, target in MUTS:
        orig = io.open(SRC, encoding="utf-8", newline="").read()
        h0 = md5(SRC)
        if orig.count(old) != 1:
            print("INVALID  %s  → 锚点命中 %d 次" % (name, orig.count(old)))
            continue
        try:
            io.open(SRC, "w", encoding="utf-8", newline="").write(orig.replace(old, new, 1))
            r = subprocess.run([PY, "-m", "pytest", TEST, "-q", "-k", target],
                               capture_output=True, text=True)
            out = (r.stdout or "") + (r.stderr or "")
            if "no tests ran" in out:
                print("INVALID  %s  → -k 没选中用例：%s" % (name, target))
                continue
            ok = r.returncode != 0
            print(("KILLED   " if ok else "SURVIVED ") + name)
            killed += 1 if ok else 0
        finally:
            io.open(SRC, "w", encoding="utf-8", newline="").write(orig)
        assert md5(SRC) == h0, "复原失败"
    print("复原 md5 一致；击杀 %d/%d" % (killed, len(MUTS)))
    return 0 if killed == len(MUTS) else 1


if __name__ == "__main__":
    sys.exit(main())
