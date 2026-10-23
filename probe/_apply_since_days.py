# -*- coding: utf-8 -*-
"""给 fetch_and_ingest.py 加 --since-days：按"最近 N 天有归档的周目录"导入。

【为什么要这个开关】
用户要求「从本地导入研报的固定时间定为每星期一晚九点」—— 导入要从每日归档里
拆出来、改成周更一次。但现有导入入口读 `last_archive.json`，而 `read_weeks()`
里有 **24 小时新鲜度闸门**（超过 24h 判过期、不导入）。

周一 21:00 触发时，最近一次归档通常是周一 18:00（3 小时前，能过闸门），
**但如果那轮没抓到新邮件，last_archive 还停在上周同一时刻**（>24h）⇒ 直接空跑，
"每周一导入"就形同虚设。

所以加一个不依赖 last_archive 的入口：扫描 BASE 下形如 `YYYYMMDD-YYYYMMDD研报`
的目录，把**起始日距今 ≤ N 天**的全部纳入导入（ingest_local_pdfs 自带同名幂等，
重复扫到已导入的目录只会空转，不会重复入库）。
"""
from __future__ import annotations

import io
import os

P = os.path.join(r"D:\海晟研报\_scripts", "fetch_and_ingest.py")

s = io.open(P, "r", encoding="utf-8", newline="").read()
before = s


def rep(old: str, new: str, label: str):
    global s
    n = s.count(old)
    assert n == 1, "%s 命中 %d 次:\n%s" % (label, n, old[:120])
    s = s.replace(old, new, 1)


# ① import re
rep("""import json
import os
import subprocess""",
    """import json
import os
import re
import subprocess""",
    "import re")

# ② 周目录正则 + recent_weeks
rep('''INGEST_LOG = os.path.join(BASE, "_config", "ingest.log")  # 【变量】本脚本审计日志''',
    '''INGEST_LOG = os.path.join(BASE, "_config", "ingest.log")  # 【变量】本脚本审计日志

# 【常量】周目录命名：`20261019-1025研报`（起始日-结束日 MMDD + 研报）
WEEK_DIR_RE = re.compile(r"^(\\d{8})-(\\d{4})研报$")


def recent_weeks(days: int) -> list[str]:
    """扫描 BASE 下起始日距今 <= days 天的周目录（供 --since-days 用）。

    与 read_weeks() 的区别：这里**不看** last_archive.json 的 24h 新鲜度闸门。
    那个闸门是为"同日 12:00 归档 → 18:00 补导入"设计的；周更任务（每周一 21:00）
    用到它时，最近一次归档往往早已超过 24h，会被判过期直接空跑。

    返回目录名列表（如 "20261019-1025研报"），按名称升序（= 时间升序）。
    """
    today = datetime.now().date()
    out: list[str] = []
    try:
        names = os.listdir(BASE)
    except OSError as e:
        log("扫描周目录失败: %s" % e)
        return []
    for name in names:
        m = WEEK_DIR_RE.match(name)
        if not m:
            continue
        try:
            start = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        age = (today - start).days
        # -1 容错：目录起始日写成"明天"的情况（时区/手工命名）也算本次范围
        if -1 <= age <= days:
            out.append(name)
    out.sort()
    return out''',
    "recent_weeks")

# ③ 解析 --since-days
rep('''    cli_weeks: list[str] = []
    workers_cli: int | None = None
    for i, a in enumerate(argv):
        if a == "--weeks":
            cli_weeks = [x for x in argv[i + 1:] if not x.startswith("--")]
        elif a == "--workers" and i + 1 < len(argv):''',
    '''    cli_weeks: list[str] = []
    workers_cli: int | None = None
    since_days: int | None = None
    for i, a in enumerate(argv):
        if a == "--weeks":
            cli_weeks = [x for x in argv[i + 1:] if not x.startswith("--")]
        elif a == "--since-days" and i + 1 < len(argv):
            try:
                since_days = int(argv[i + 1])
            except ValueError:
                pass
        elif a == "--workers" and i + 1 < len(argv):''',
    "parse since-days")

# ④ 启动日志带上 since_days
rep('''    log("海晟研报 归档→导入 开始 (dry_run=%s, no_fetch=%s, skip_ingest=%s)"
        % (dry_run, no_fetch, skip_ingest))''',
    '''    log("海晟研报 归档→导入 开始 (dry_run=%s, no_fetch=%s, skip_ingest=%s, since_days=%s)"
        % (dry_run, no_fetch, skip_ingest, since_days))''',
    "start log")

# ⑤ 周目录来源优先级
rep('''    weeks = read_weeks(cli_weeks)
    if not weeks:''',
    '''    # 周目录来源优先级：命令行 --weeks > --since-days 扫描 > last_archive(带 24h 闸门)
    if cli_weeks:
        weeks = cli_weeks
    elif since_days is not None:
        weeks = recent_weeks(since_days)
        log("按 --since-days %d 扫描到 %d 个周目录: %s"
            % (since_days, len(weeks), "、".join(weeks) if weeks else "(无)"))
    else:
        weeks = read_weeks(None)
    if not weeks:''',
    "weeks source")

# ⑥ 用法说明补一行
rep('''  python fetch_and_ingest.py --weeks 20261012-1018研报 [20260928-1004研报 ...]''',
    '''  python fetch_and_ingest.py --weeks 20261012-1018研报 [20260928-1004研报 ...]
  python fetch_and_ingest.py --no-fetch --since-days 8
                                             # 只导入"最近 8 天有归档的周目录"
                                             # （周更任务用；不依赖 last_archive 的 24h 闸门）''',
    "usage")

assert s != before
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok, len", len(s))
