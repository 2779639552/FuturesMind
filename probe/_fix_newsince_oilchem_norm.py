# -*- coding: utf-8 -*-
"""三处改动（2026-10-14 用户拍板口径）

═══ 用户要求 ═══
①「我在研报里未曾看到今天新增的公众号观点，帮我落实下」
   + 选择：**今天优先 + 历史暂缓**
②「同品种隆众有多口径（隆众资讯 / 隆众资讯-聚焦），取最新版的即可，不要重复性分析录入」
   + 选择：**只归一机构名（不丢内容）**

═══ 根因与对策 ═══
① 入库器 `--limit 20` 是**按 create_time 最旧优先**处理；微信读书通道今天初始化时
   把 118 篇历史基线灌进 inbox ⇒ 当天 13 篇被排在后面，要 6 轮（数小时）才轮到。
   对策：给 `wechat_ingest.py` 加 `--since YYYY-MM-DD`（发布日过滤，在"最旧优先+限流"
   **之前**生效）；并让调度按"近 N 天窗口"调用 ⇒ **每天只处理新文**，历史不再堵路。

② 聚合层 `_merge_same_source_items` 本就按 **(品种, 归一后机构)** 合并成一条
   （分组键与显示键同源，见该函数 docstring）。但 `_SOURCE_NORMALIZE_RULES` 里**没有隆众**
   ⇒ `隆众资讯-聚焦` / `-聚乙烯` / `-成品油` 各自成"一家机构" ⇒ 同一品种出现多行。
   对策：规则表加一条 `("隆众资讯", "隆众资讯")` —— 子串匹配即可覆盖全部 `隆众资讯-*`
   变体，**无需**新增去重逻辑（这正是 entity-merge-dedup 的"只扩归一规则"路线）。

⚠️ 三文件 EOL 不同：web_app.py / scheduler.py = **CRLF**；wechat_ingest.py = **LF**。
   写回必须 `newline=""` 且保持各自 EOL，否则锚点全失效（本项目铁律）。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ══════════════════════════════════════════════════════════════════════
# ① web_app.py：机构名归一表加「隆众」
# ══════════════════════════════════════════════════════════════════════
WA = ROOT / "web_app.py"
WA_OLD = '    ("中信期货", "中信期货"),\r\n)'
WA_NEW = (
    '    ("中信期货", "中信期货"),\r\n'
    '    # 【同源归并 2026-10-14】隆众:同一品种在多个「口径」下重复出现,用户报"不要重复性录入"。\r\n'
    '    #   实测库里 `隆众资讯` / `隆众资讯-聚焦` / `隆众资讯-聚乙烯` / `隆众资讯-成品油` 被当成\r\n'
    '    #   4 家机构 ⇒ 同一品种在观点总表里出现多行。\r\n'
    '    #   成因:采集端只有一个源(热点资讯频道),`account`(类别)是从**标题前缀**派生的\r\n'
    '    #   (`[隆众聚焦]`→聚焦、`隆众能化早读`→早读),入库时被拼成 `隆众资讯-<类别>`。\r\n'
    '    #   对策:只加这一条归一规则即可 —— 聚合层 `_merge_same_source_items` 本就按\r\n'
    '    #   (品种, 归一后机构) 合并成一条,**不需要**新增去重逻辑(内容不丢)。\r\n'
    '    #   子串匹配 `隆众资讯` 覆盖全部 `隆众资讯-*` 变体;不误伤其它机构(全库无他者含此串)。\r\n'
    '    ("隆众资讯", "隆众资讯"),\r\n)'
)

# ══════════════════════════════════════════════════════════════════════
# ② wechat_ingest.py：加 --since 发布日过滤
# ══════════════════════════════════════════════════════════════════════
WI = ROOT / "wechat_ingest.py"
WI_ARG_OLD = '    ap.add_argument("--account", help="只入库某个号(按号名匹配)")\n'
WI_ARG_NEW = (
    '    ap.add_argument("--account", help="只入库某个号(按号名匹配)")\n'
    '    ap.add_argument("--since",\n'
    '                    help="只入库发布日期 >= 该日期(YYYY-MM-DD)的文章。用于「每日只处理新文」:"\n'
    '                         "本脚本按 create_time **最旧优先** 且 --limit 限流,若 inbox 积压了"\n'
    '                         "历史基线(实测微信读书初始化灌入 118 篇),当天新文会被排到数轮之后。\\n"\n'
    '                         "过滤发生在去重/排序/限流**之前**,故 --limit 的名额只给窗口内的文章。")\n'
)

WI_FILT_OLD = (
    '    entries = load_entries(args.source)\n'
    '    if args.account:\n'
    '        entries = [e for e in entries if entry_account(e) == args.account]\n'
)
WI_FILT_NEW = (
    '    entries = load_entries(args.source)\n'
    '    if args.account:\n'
    '        entries = [e for e in entries if entry_account(e) == args.account]\n'
    '    # 【2026-10-14】发布日过滤:**必须在去重/排序/限流之前** —— 否则 --limit 的 20 个\n'
    '    #   名额会被历史积压吃掉(实测当天 13 篇要等 6 轮才轮到)。\n'
    '    if args.since:\n'
    '        _since = str(args.since).strip()[:10]\n'
    '        _before = len(entries)\n'
    '        entries = [e for e in entries if (entry_date(e) or "") >= _since]\n'
    '        print("  --since %s 过滤: %d -> %d 条" % (_since, _before, len(entries)))\n'
)

# ══════════════════════════════════════════════════════════════════════
# ③ scheduler.py：调度按「近 N 天窗口」调用
# ══════════════════════════════════════════════════════════════════════
SC = ROOT / "scheduler.py"
SC_OLD = (
    '    out, err = _wechat_step(env, script_dir, venv_py, "ingest",\r\n'
    '                            ["wechat_ingest.py", "--source", "inbox"], 3600)\r\n'
)
SC_NEW = (
    '    # 【2026-10-14 用户口径:今天优先 + 历史暂缓】只处理"最近 N 天"发布的文章。\r\n'
    '    #   为什么必须加:入库器按 create_time **最旧优先** + --limit 20,若 inbox 里积压了\r\n'
    '    #   历史基线(实测微信读书通道初始化灌入 118 篇,跨 2026-06-09~10-13),\r\n'
    '    #   当天新文会被排在数轮之后 ⇒ 用户报"研报里看不到今天的公众号观点"。\r\n'
    '    #   窗口取"今天-1"而非"仅今天":文章可能是"昨晚发布、今早采集",只按当天会漏;\r\n'
    '    #   重复处理无害(DB 按 key/url 幂等跳过)。可用 WECHAT_INGEST_SINCE_DAYS 调整。\r\n'
    '    _since_days = int(os.getenv("WECHAT_INGEST_SINCE_DAYS") or 1)\r\n'
    '    _since = (datetime.now() - timedelta(days=_since_days)).strftime("%Y-%m-%d")\r\n'
    '    out, err = _wechat_step(env, script_dir, venv_py, "ingest",\r\n'
    '                            ["wechat_ingest.py", "--source", "inbox", "--since", _since], 3600)\r\n'
)


def eol_of(p: Path) -> str:
    b = p.read_bytes()
    crlf = b.count(b"\r\n")
    bare = b.count(b"\n") - crlf
    if crlf and not bare:
        return "CRLF"
    if bare and not crlf:
        return "LF"
    return "MIXED"


def main() -> int:
    jobs = [
        ("① 机构名归一加「隆众」", WA, WA_OLD, WA_NEW, 1),
        ("②a 入库器加 --since 参数", WI, WI_ARG_OLD, WI_ARG_NEW, 1),
        ("②b 入库器加发布日过滤", WI, WI_FILT_OLD, WI_FILT_NEW, 1),
        ("③ 调度按近 N 天窗口调用", SC, SC_OLD, SC_NEW, 1),
    ]

    # ── 锚点预检（全部命中才动手，避免半截补丁）──────────────────
    print("[1] 锚点预检")
    originals = {}
    ok = True
    for tag, path, old, new, want in jobs:
        if path not in originals:
            originals[path] = path.read_bytes()
        txt = originals[path].decode("utf-8")
        got = txt.count(old)
        flag = "✔" if got == want else "✘"
        print("    %s %-26s %-14s 命中 %d（期望 %d）" % (flag, tag, path.name, got, want))
        if got != want:
            ok = False
    if not ok:
        print("\n✘ 锚点不匹配 —— **未写入任何内容**")
        return 2

    # ── 备份 ────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    for path in originals:
        bak = ROOT / "backup" / ("%s_pre_newsince_%s%s" % (path.stem, ts, path.suffix))
        shutil.copy2(path, bak)
        print("\n[2] 备份 -> backup/%s" % bak.name)

    # ── 应用 ────────────────────────────────────────────────────
    print("\n[3] 应用")
    for tag, path, old, new, want in jobs:
        eol = eol_of(path)
        txt = path.read_bytes().decode("utf-8")  # 不转换换行,否则 CRLF 锚点失配
        assert txt.count(old) == want, "%s 锚点变了" % tag
        txt = txt.replace(old, new, 1)
        path.write_text(txt, encoding="utf-8", newline="")
        assert eol_of(path) == eol, "%s EOL 被破坏(%s -> %s)" % (path.name, eol, eol_of(path))
        print("    ✔ %-26s (%s, EOL 未变)" % (tag, eol))

    # ── 自检 ────────────────────────────────────────────────────
    print("\n[4] 自检")
    checks = [
        ("web_app 隆众规则", WA, '("隆众资讯", "隆众资讯"),', 1),
        ("web_app 规则表未破", WA, "_SOURCE_NORMALIZE_RULES", 1),
        ("ingest --since 参数", WI, 'ap.add_argument("--since",', 1),
        ("ingest 过滤逻辑", WI, 'if (entry_date(e) or "") >= _since', 1),
        ("scheduler 传 --since", SC, '"--source", "inbox", "--since", _since', 1),
        ("scheduler 窗口计算", SC, 'WECHAT_INGEST_SINCE_DAYS', 1),
    ]
    for tag, path, needle, want in checks:
        got = path.read_bytes().decode("utf-8").count(needle)
        print("    %s %-24s %d" % ("✔" if got == want else "✘", tag, got))
        assert got == want, "%s 期望 %d 实际 %d" % (tag, want, got)

    for path in originals:
        print("    %-16s 原 %s 新 %s" % (path.name,
              hashlib.md5(originals[path]).hexdigest()[:8],
              hashlib.md5(path.read_bytes()).hexdigest()[:8]))
    print("\n✔ 三处改动已落盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
