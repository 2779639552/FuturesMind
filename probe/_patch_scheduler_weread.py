# -*- coding: utf-8 -*-
"""给 scheduler.py 接上「微信读书通道」的公众号采集（2026-10-14）。

============================== 设计：只加一路，不加新时点 ==============================
现有 `_run_wechat_collect` 跑的是 **合集通道**（wechat_album_collector.py --daily），
产物追加进 `wechat_inbox.jsonl`；`_run_wechat_ingest` 再从这个 inbox 入库。
微信读书通道 **写同一个 inbox** ⇒ 只要在采集步骤里多跑一路，入库与调度**都不用动**。
所以本补丁：
  ① 加 `WEREAD_STATE_DIR` 常量（读书通道自己的状态：cookie/注册表/水位线）
  ② 加 `_wechat_weread_count()`（读 mp_collect_result.json 的机器可读计数）
  ③ 在 `_run_wechat_collect` 里**追加一次 `_wechat_step`** 跑读书通道，并把两路计数
     合并进同一条告警里（人一眼能看出"合集通道 N 篇 / 读书通道 M 篇"）

============================== 纪律 ==============================
· 备份先做（backup/scheduler_pre_weread_*.py）
· 所有锚点**先验命中数**，任一不为 1 就中止且**不写文件**
· CRLF 严格往返（本文件是纯 CRLF；内部按 \\n 处理，写回还原 \\r\\n，自检 bareLF==0）
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "scheduler.py"

# ── ① 新增常量 ─────────────────────────────────────────────────────────
A_CONST = '''# 【常量】采集器与入库器共用的状态目录(水位线/账号注册表/inbox 都在这里)。
WECHAT_STATE_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "wechat")'''

N_CONST = '''# 【常量】采集器与入库器共用的状态目录(水位线/账号注册表/inbox 都在这里)。
WECHAT_STATE_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "wechat")
# 【常量】「微信读书通道」的状态目录(cookie / 公众号注册表 / 水位线 / 结果文件)。
#   为什么单独一个目录：该通道的凭据是**网页登录 cookie**(wr_skey 等)，
#   与合集通道的"零登录"体系完全不同,分开存避免互相污染。
#   ⚠️ 但两者的 **inbox 是同一个**(见 wechat_weread_collector.F_INBOX) ⇒ 入库链路零改动。
WEREAD_STATE_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "weread")


def _wechat_weread_count(since: float) -> tuple:
    """(微信读书通道新发现篇数, 计数来源) —— 读采集器写的 mp_collect_result.json。

    与 `_wechat_collect_count` 同思路：**不信 stdout**(服务进程里会整段丢)，
    只认采集器自己落的机器可读结果文件；加 mtime 闸防"读到的却是上一轮旧数字"。
    """
    p = os.path.join(WEREAD_STATE_DIR, "mp_collect_result.json")
    try:
        if os.path.exists(p) and os.path.getmtime(p) >= since:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            return int(d.get("new") or 0), "file"
    except Exception:  # noqa: BLE001 - 读文件失败视为 0,不抛
        pass
    return 0, "none"'''

# ── ② 采集函数里追加读书通道 ───────────────────────────────────────────
A_BODY = '''    _since = datetime.now().timestamp()  # 【变量】本轮开始时刻(用于判定结果文件是否为本轮所写)
    out, err = _wechat_step(env, script_dir, venv_py, "collect",
                            ["wechat_album_collector.py", "--daily"], 1800)
    if err:
        _safe_alert(db, "wechat_collect_failed", "WeChat MP collection failed",
                    f"公众号采集{tag} 失败：{err}", severity="error")
        return
    collected, src = _wechat_collect_count(out, _since)
    _safe_alert(db, "wechat_collect_done", "WeChat MP collect done",
                f"公众号采集{tag} 完成：新发现 {collected} 篇（覆盖范围=已登记合集；"
                f"入库由 {_WECHAT_INGEST_TIMES} 的入库任务接手）",
                severity="info")'''

N_BODY = '''    _since = datetime.now().timestamp()  # 【变量】本轮开始时刻(用于判定结果文件是否为本轮所写)
    out, err = _wechat_step(env, script_dir, venv_py, "collect",
                            ["wechat_album_collector.py", "--daily"], 1800)
    if err:
        _safe_alert(db, "wechat_collect_failed", "WeChat MP collection failed",
                    f"公众号采集{tag} 失败：{err}", severity="error")
        return
    collected, src = _wechat_collect_count(out, _since)

    # ── 第二路：微信读书通道（2026-10-14 新增）─────────────────────────────
    # 【为什么需要】机构号（中信期货研究/混沌天成研究/物产中大…）**不建合集**，
    #   合集通道对它们完全无效（album_info_list 为空数组）。而微信读书收录了这些号，
    #   走 /web/mp/articles + /web/mp/content 可拿到文章清单与正文。
    # 【为什么不留新时点】它写的是**同一个 inbox** ⇒ 紧跟其后就能被 09:05/19:05 的
    #   入库任务消费，无需新增调度时点、也无需改入库链路。
    # 【失败不阻塞】读书通道挂了（cookie 过期等）只单独告警，**不影响合集通道的结论**。
    _w_since = datetime.now().timestamp()
    w_env = dict(env)
    w_env["AGENTSENSE_WEREAD_DIR"] = WEREAD_STATE_DIR
    w_out, w_err = _wechat_step(w_env, script_dir, venv_py, "weread",
                                ["wechat_weread_collector.py", "--daily"], 1800)
    w_collected, _w_src = _wechat_weread_count(_w_since)
    if w_err:
        _safe_alert(db, "wechat_weread_collect_failed", "WeChat MP (weread) collect failed",
                    f"读书通道采集{tag} 失败：{w_err}"
                    f"（多为 cookie 失效 → 重跑 probe/_weread_login.mjs；"
                    f"合集通道结论不受影响）", severity="warn")

    _safe_alert(db, "wechat_collect_done", "WeChat MP collect done",
                f"公众号采集{tag} 完成：合集通道新发现 {collected} 篇 / "
                f"读书通道新发现 {w_collected} 篇"
                + (f"（读书通道出错：{w_err[:80]}）" if w_err else "")
                + f"（入库由 {_WECHAT_INGEST_TIMES} 的入库任务接手）",
                severity="info")'''

REPL = [
    ("WEREAD_STATE_DIR 常量 + _wechat_weread_count", A_CONST, N_CONST),
    ("采集函数追加读书通道一路", A_BODY, N_BODY),
]


def main() -> int:
    raw = TARGET.read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf == 0 or lf != 0:
        print(f"✘ scheduler.py 非纯 CRLF (CRLF={crlf} bareLF={lf})，中止")
        return 2
    src = raw.decode("utf-8").replace("\r\n", "\n")

    print("=" * 74)
    print("补丁：scheduler.py 接入微信读书通道")
    print("=" * 74)

    # ── 步骤 1：全部锚点先验命中 ────────────────────────────────────
    print("\n[1] 锚点预检（任一不为 1 即中止，不写文件）")
    problems = []
    for tag, old, _new in REPL:
        n = src.count(old)
        flag = "OK" if n == 1 else "✘"
        print(f"    {flag} {tag}: 命中 {n} 次（期望 1）")
        if n != 1:
            problems.append(tag)
    if problems:
        print(f"\n✘ 锚点异常：{problems} —— 已中止，文件未改动")
        return 3

    # ── 步骤 2：应用 ────────────────────────────────────────────────
    print("\n[2] 应用替换")
    for tag, old, new in REPL:
        src = src.replace(old, new, 1)
        print(f"    ✔ {tag}")

    # ── 步骤 3：残留与到位自检 ──────────────────────────────────────
    print("\n[3] 自检")
    checks = [
        ("WEREAD_STATE_DIR 已定义", "WEREAD_STATE_DIR = os.path.join" in src, True),
        ("_wechat_weread_count 已定义", "def _wechat_weread_count(" in src, True),
        ("读书通道采集已接线", '"wechat_weread_collector.py", "--daily"' in src, True),
        ("AGENTSENSE_WEREAD_DIR 已传", 'w_env["AGENTSENSE_WEREAD_DIR"]' in src, True),
        ("旧的单路告警文本已替换",
         'f"公众号采集{tag} 完成：新发现 {collected} 篇（覆盖范围=已登记合集；"' in src, False),
        ("json/os 依赖可用（模块级已 import）", "import json" in src, True),
    ]
    bad = []
    for name, got, want in checks:
        ok = (got is want)
        print(f"    {'OK' if ok else '✘'} {name}（got={got}）")
        if not ok:
            bad.append(name)
    if bad:
        print(f"\n✘ 自检未过：{bad} —— 已中止，文件未改动")
        return 4

    # ── 步骤 4：写回（CRLF 往返）────────────────────────────────────
    out = src.replace("\n", "\r\n")
    TARGET.write_bytes(out.encode("utf-8"))
    chk = TARGET.read_bytes()
    c2 = chk.count(b"\r\n")
    l2 = chk.count(b"\n") - c2
    print(f"\n[4] 已写入 {len(raw)} → {len(chk)} bytes (+{len(chk) - len(raw)})")
    print(f"    行尾自检: CRLF={c2} bareLF={l2} => {'✔ 纯 CRLF' if l2 == 0 else '✘ 混入裸 LF'}")
    print("\n✔ 补丁完成")
    return 0 if l2 == 0 else 5


if __name__ == "__main__":
    sys.exit(main())
