# -*- coding: utf-8 -*-
"""变异自检：公众号通道状态巡检（2026-10-16）。

对每处关键实现做一次"反向破坏"，确认对应护栏**真的会红**；跑完核对 md5 复原。

⚠ 一律用 chr(13)+chr(10) 构造 CRLF，不写字面转义（避免 heredoc/JSON 吃掉反斜杠）。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / "venv" / "Scripts" / "python.exe")
APP = ROOT / "web_app.py"
TPL = ROOT / "web_template.html"

CR = chr(13)
LF = chr(10)
EOL = CR + LF


def to_crlf(s: str) -> str:
    return s.replace(EOL, LF).replace(LF, EOL)


T = "tests/test_wechat_channel_status.py"
TRI = T + "::test_tri_state_never_mixes_empty_with_failed"
ANC = T + "::test_alert_in_round_anchor_semantics"
RO = T + "::test_status_check_is_read_only"
WIR = T + "::test_frontend_container_and_wiring"
REN = T + "::test_frontend_renders_three_states_and_fix_hint"
EMP = T + "::test_frontend_empty_state_is_not_alarming"
VER = "tests/test_fund_xsection.py::test_frontend_sec_index_version_bumped"
SST = T + "::test_stale_alert_superseded_time_alignment"
SSC = T + "::test_both_channels_apply_supersede_check"

# (标签, 文件, 原串(LF 书写), 变异串(LF 书写), 应红的测试)
MUTS = [
    ("M1 三态: 把 empty 也算成 failed（用户会去重登没坏的账号）", APP,
     '    if new_count == 0:\n        return "empty"\n',
     '    if new_count == 0:\n        return "failed"\n', TRI),

    ("M2 锚点: 去掉时间比较（一次历史失败会永久标红）", APP,
     '    return (not base_ts) or ts >= base_ts\n',
     '    return bool(ts)\n', ANC),

    ("M3 锚点: 反向比较（把上一轮失败当本轮）", APP,
     '    return (not base_ts) or ts >= base_ts\n',
     '    return (not base_ts) or ts < base_ts\n', ANC),

    ("M4 只读纪律: 巡检里顺手跑一次采集（看一眼就污染生产）", APP,
     '    db = get_db()\n\n    def _latest(atype):\n',
     '    import subprocess as _sp\n    _sp.run(["echo", "collect"])\n    db = get_db()\n\n    def _latest(atype):\n', RO),

    # 【2026-10-16 换】原 M5 是"断开 loadResearch 里的接线"，而那处接线已按 E2E 的发现
    #   移到 loadWarehouse ⇒ 锚点失效。换成覆盖**位置断言**的变异：把巡检条挪到列表**之后**，
    #   与"研报从哪来"割裂（那条断言目前没有别的变异覆盖）。
    ("M5 容器挪到列表之后（与「研报从哪来」割裂）", TPL,
     ' <div id="res-ingest-status" style="display:none;margin:0 0 10px"></div>\n'
     ' <div style="font-size:0.8125rem;color:var(--text-secondary);line-height:1.8" id="res-list">加载中...</div>\n',
     ' <div style="font-size:0.8125rem;color:var(--text-secondary);line-height:1.8" id="res-list">加载中...</div>\n'
     ' <div id="res-ingest-status" style="display:none;margin:0 0 10px"></div>\n', WIR),

    ("M6 前端: 去掉修复命令渲染（知道坏了也没法修）", TPL,
     ' ${c.hint ? `<div style="margin-top:2px">修复：<code style="background:var(--bg-input);padding:1px 6px;border-radius:4px">${esc(c.hint)}</code></div>` : \'\'}\n',
     " \n", REN),

    ("M7 前端: 去掉「品种池拦截」解释（口径边界被当成通道故障）", TPL,
     " if (pb.count) {\n",
     " if (false && pb.count) {\n", REN),

    ("M8 前端: 把 empty 也渲染成红色告警（正常状态被当故障）", TPL,
     " if (empty.length) {\n parts.push(`<div style=\"font-size:0.8125rem;line-height:1.7;padding:6px 12px;border-radius:8px;background:rgba(245,158,11,.12);",
     " if (empty.length) {\n parts.push(`<div style=\"font-size:0.8125rem;line-height:1.7;padding:6px 12px;border-radius:8px;background:rgba(239,68,68,.12);", EMP),

    ("M9 版本号退回 30（结构变了却不重建索引）", TPL,
     "_SEC_IDX_VER: '31',", "_SEC_IDX_VER: '30',", VER),

    # ★ M10 挂错 tab：容器在 warehouse 页，却把调用挂到研报问答页 ⇒ 永不执行。
    #   第一版就是这么错的，而当时"方法存在 + 有一行调用"的静态断言全绿 ——
    #   是浏览器 E2E 抓到的。这里把这条教训固化成变异项。
    ("M10 接线挂错 tab（容器不在那 ⇒ 永不执行）", TPL,
     ' this._loadIngestStatus();' + chr(10) + ' await this.loadUserDatasets();',
     ' await this.loadUserDatasets();', WIR),
    # ── 2026-10-16 追加：陈旧失败告警的推翻判定 ──────────────────────
    ("M11 去掉 UTC→本地 +8 对齐（判定恒假 ⇒ 修复静默失效）", APP,
     '        a = _d.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S") + _d.timedelta(hours=8)\n',
     '        a = _d.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")\n', SST),

    ("M12 断开读书通道的推翻判定（历史告警永久标红）", APP,
     '    if wr_bad and _wechat_alert_superseded(wr_fail, mp_rep.get("ts")):\n',
     '    if False:\n', SSC),

    ("M13 合集通道不接推翻判定（只修一半）", APP,
     '    if alb_bad and _wechat_alert_superseded(\n',
     '    if False and _wechat_alert_superseded(\n', SSC),
]


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()[:12]


def run(test: str) -> bool:
    """True = 该护栏变红（= 变异被击杀）。"""
    r = subprocess.run([PY, "-m", "pytest", test, "-q", "--no-header",
                        "-p", "no:warnings", "--tb=no"],
                       cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    return ("failed" in out) or (r.returncode != 0)


def main() -> int:
    print("=== 基线：被检护栏应全绿 ===")
    ok = True
    for t in (TRI, ANC, RO, WIR, REN, EMP, VER):
        red = run(t)
        print("  %s %s" % ("★ 红" if red else "绿", t.split("::")[-1]))
        ok = ok and not red
    if not ok:
        print("\n★ 基线不是全绿，变异自检无意义")
        return 2

    killed = survived = skipped = 0
    print("\n=== 逐条变异 ===")
    for tag, path, old_lf, new_lf, test in MUTS:
        raw = path.read_bytes()
        before = md5(path)
        crlf = raw.count(b"\r\n") > 0
        t = raw.decode("utf-8")
        o = to_crlf(old_lf) if crlf else old_lf
        n = to_crlf(new_lf) if crlf else new_lf
        c = t.count(o)
        if c != 1 or o == n:
            print("  ⊘ 跳过 %s —— 锚点命中 %d 次（期望 1）" % (tag, c))
            skipped += 1
            continue
        path.write_bytes(t.replace(o, n, 1).encode("utf-8"))
        try:
            red = run(test)
        finally:
            path.write_bytes(raw)
        assert md5(path) == before, "★ 未复原: %s" % path
        if red:
            killed += 1
            print("  ✔ 击杀  %s   [%s]" % (tag, test.split("::")[-1]))
        else:
            survived += 1
            print("  ✗ 存活  %s   [%s] ⇒ 护栏有洞！" % (tag, test.split("::")[-1]))

    print("\n" + "=" * 66)
    print("  击杀 %d / 存活 %d / 跳过 %d   （md5 全部复原 ✔）" % (killed, survived, skipped))
    print("=" * 66)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
