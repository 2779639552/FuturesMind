# -*- coding: utf-8 -*-
"""把「隆众资讯」采集/入库接入 scheduler（一次性接线补丁）

【为什么要用脚本而不是直接编辑】项目铁律：
  · `scheduler.py` / `web_app.py` 是 **CRLF** 文件,必须 `newline=""` 读写;
    EOL 写错 ⇒ 锚点命中 0 ⇒ **静默不写入**（最难发现的失败形态）。
  · 锚点必须**先校验存在且唯一**再替换,并自动备份。

用法:
    python patch_scheduler_oilchem.py            # dry-run(只校验锚点)
    python patch_scheduler_oilchem.py --apply
"""
from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ═══════════════════════════════════════════════════════════════════════════
# scheduler.py —— 5 处补丁
# ═══════════════════════════════════════════════════════════════════════════
S_ANCHOR_1 = '''# 【常量】采集器与入库器共用的状态目录(水位线/账号注册表/inbox 都在这里)。
WECHAT_STATE_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "wechat")'''

S_NEW_1 = S_ANCHOR_1 + '''

# 【常量】隆众资讯(2026-10-14)的状态目录与采集/入库时点。
#   采集 08:20 / 17:10 —— 早读 07:30 已发布 + 当日聚焦收尾(约 16:22)后;**不占 LLM**。
#   入库 10:00 / 20:00 —— **刻意避开研报三源的 LLM 并发窗口**
#     (research 08:10 错峰成 htfc 08:25 / gtja 08:40 / dz 08:50,RAG 09:25,日报 09:45;
#      账户并发上限 5,研报三源已占 2~3 路)。20:00 同理避开 18:15~19:35。
OILCHEM_STATE_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "oilchem")
OILCHEM_COLLECT_TIMES = ["08:20", "17:10"]
OILCHEM_INGEST_TIMES = ["10:00", "20:00"]'''


S_ANCHOR_2 = '''    inserted, retried = _wechat_ingest_counts(out, _wechat_result("ingest"))
    _safe_alert(db, "wechat_ingest_done", "WeChat MP ingest done",
                f"公众号入库{tag} 完成：新入库 {inserted} 篇，重跑残留 {retried} 篇",
                severity="info")


def start_scheduler('''

S_NEW_2 = '''    inserted, retried = _wechat_ingest_counts(out, _wechat_result("ingest"))
    _safe_alert(db, "wechat_ingest_done", "WeChat MP ingest done",
                f"公众号入库{tag} 完成：新入库 {inserted} 篇，重跑残留 {retried} 篇",
                severity="info")


# ── 隆众资讯 (2026-10-14) ──────────────────────────────────────────────────
# 与公众号同构:采集(免登录 HTTP,**不占 LLM**)与入库(走 LLM)拆成两组 job。
# 【差异·必须限速】隆众对连续请求返回 429(实测连打 5~6 次即触发),
#   脚本内已内置 8s 间隔 + 退避;单轮最多 40 篇 × 8s ≈ 5 分钟,故超时给 30 分钟。
# 【复用】借 `_wechat_step` 跑子进程(它只把 name 用于日志文件名,与功能无关) ⇒
#   日志会落成 research_collect_wechat_oilchem_*.log —— 带 oilchem 字样,按名可搜到。
def _oilchem_result(name: str) -> dict:
    """读隆众子进程写的**机器可读结果文件**(比解析 stdout 可靠)。缺失/损坏返回 {}。

    【为什么】沿用公众号链路的实测结论:从服务进程启动子进程时,子进程 stdout 可能
    整段丢失(同一命令在交互式 shell 跑却正常) ⇒ 计数只认结果文件,不猜。
    【参数】name: "news"(采集) | "ingest"(入库)。
    """
    p = os.path.join(OILCHEM_STATE_DIR, "oilchem_%s_result.json" % name)
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _run_oilchem_collect(time_str: str = ""):
    """① 隆众资讯**采集**(免登录,不占 LLM)。

    【关键逻辑】读追加式归档(pending 靠 DB 去重) ⇒ 崩溃后下轮自动补;
    被登录墙拦掉的与正文过短的不进归档(脚本内已判),不烧 LLM。
    """
    db = get_db()
    tag = ("@" + time_str) if time_str else ""
    _safe_alert(db, "oilchem_collect_started", "OilChem news collect started",
                f"隆众资讯采集{tag} 开始（免登录 HTTP，限速 8s/篇）", severity="info")
    venv_py = os.path.join(os.path.dirname(sys.executable), "python")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env = _collector_env()
    env["AGENTSENSE_OILCHEM_DIR"] = OILCHEM_STATE_DIR

    out, err = _wechat_step(env, script_dir, venv_py, "oilchem_collect",
                            ["oilchem_news_collect.py", "--pages", "2"], 1800)
    if err:
        _safe_alert(db, "oilchem_collect_failed", "OilChem news collection failed",
                    f"隆众资讯采集{tag} 失败：{err}", severity="error")
        return
    r = _oilchem_result("news")
    _safe_alert(db, "oilchem_collect_done", "OilChem news collect done",
                f"隆众资讯采集{tag} 完成：新增可读 {r.get('collected', '?')} 篇 / "
                f"登录墙 {r.get('walled', '?')} / 失败 {r.get('failed', '?')}",
                severity="info")


def _run_oilchem_ingest(time_str: str = ""):
    """② 隆众资讯**入库**(走 LLM)。读归档,DB 去重,幂等可重跑。

    【关键】入库后会自动跑**归属校验**(`oilchem_ingest.post_verify_attribution`,
    不额外烧 LLM),修正两类归属缺陷:
      ① 多品种行首项是池外/上游 ⇒ 改归到标题主体对应的池内码;
      ② LLM 未输出品种、走关键词兜底误判(隆众常把「橡胶」当下游需求方提及) ⇒ 标 skipped。
    """
    db = get_db()
    tag = ("@" + time_str) if time_str else ""
    _safe_alert(db, "oilchem_ingest_started", "OilChem news ingest started",
                f"隆众资讯入库{tag} 开始（读归档，DB 去重，含归属校验）", severity="info")
    venv_py = os.path.join(os.path.dirname(sys.executable), "python")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env = _collector_env()
    env["AGENTSENSE_OILCHEM_DIR"] = OILCHEM_STATE_DIR

    out, err = _wechat_step(env, script_dir, venv_py, "oilchem_ingest",
                            ["oilchem_ingest.py", "--limit", "20"], 5400)
    if err:
        _safe_alert(db, "oilchem_ingest_failed", "OilChem news ingest failed",
                    f"隆众资讯入库{tag} 失败：{err}（采集已留档，下轮自动补入）",
                    severity="error")
        return
    r = _oilchem_result("ingest")
    _safe_alert(db, "oilchem_ingest_done", "OilChem news ingest done",
                f"隆众资讯入库{tag} 完成：新入库 {r.get('inserted', '?')} 篇"
                f"（有效观点 {r.get('usable', '?')} 篇）",
                severity="info")


def start_scheduler('''


S_ANCHOR_3 = '''def start_scheduler(schedule_times: list[str] = None, research_times: list[str] = None,
                    warrant_times: list[str] = None,
                    wechat_collect_times: list[str] = None,
                    wechat_ingest_times: list[str] = None,
                    archive_times: list[str] = None):'''

S_NEW_3 = '''def start_scheduler(schedule_times: list[str] = None, research_times: list[str] = None,
                    warrant_times: list[str] = None,
                    wechat_collect_times: list[str] = None,
                    wechat_ingest_times: list[str] = None,
                    archive_times: list[str] = None,
                    oilchem_collect_times: list[str] = None,
                    oilchem_ingest_times: list[str] = None):'''


S_ANCHOR_4 = '''            wechat_ingest_times: 公众号**入库**时点,默认 ["09:05","19:05"] ——
            刻意比采集晚 35 分钟,**避开研报三源的 LLM 并发窗口**(见 _WECHAT_INGEST_TIMES)。'''

S_NEW_4 = S_ANCHOR_4 + '''
            oilchem_collect_times: 隆众资讯**采集**时点,默认 ["08:20","17:10"](2026-10-14
            新增;免登录 HTTP,不占 LLM;08:20 收早读、17:10 收当日聚焦收尾)。
            oilchem_ingest_times: 隆众资讯**入库**时点,默认 ["10:00","20:00"] ——
            刻意避开研报三源窗口(08:25~09:45 / 18:15~19:35),见 OILCHEM_INGEST_TIMES。'''


S_ANCHOR_5 = '''    if archive_times is None:
        # 【变量】研报归档时点(2026-09-28): 09:20 —— 早间研报 LLM 并发窗口
        #   (08:10 华泰/08:25 国君/08:40 东证 + 公众号入库 09:05)结束后跑, 不抢 LLM。
        archive_times = ["09:20"]'''

S_NEW_5 = S_ANCHOR_5 + '''
    if oilchem_collect_times is None:
        # 【变量】隆众采集时点(不占 LLM):早读 07:30 后 / 当日聚焦收尾(约 16:22)后。
        oilchem_collect_times = list(OILCHEM_COLLECT_TIMES)
    if oilchem_ingest_times is None:
        # 【变量】隆众入库时点:**刻意避开研报三源窗口**(见 OILCHEM_INGEST_TIMES 注释)。
        oilchem_ingest_times = list(OILCHEM_INGEST_TIMES)'''


S_ANCHOR_6 = '''    for time_str in wechat_ingest_times:
        h, m = time_str.split(":")
        _scheduler.add_job(  # 【调用函数】注册公众号入库任务(读 inbox,DB 去重,幂等)
            _run_wechat_ingest,
            CronTrigger(hour=int(h), minute=int(m)),
            args=[time_str],
            id=f"wechat_ingest_{time_str}",
            name=f"WeChat MP ingest {time_str}",
            max_instances=1,
            misfire_grace_time=3600,
        )'''

S_NEW_6 = S_ANCHOR_6 + '''

    # 【2026-10-14】隆众资讯:采集(免登录,不占 LLM)与入库(走 LLM)同样拆两组 job。
    # max_instances=1:入库可能跑几十分钟,上一轮未完时 APScheduler 跳过本次而不叠加。
    # misfire_grace_time:采集 30 分钟 / 入库 60 分钟(与公众号同口径),服务短暂重启后仍会补跑。
    for time_str in oilchem_collect_times:
        h, m = time_str.split(":")
        _scheduler.add_job(  # 【调用函数】注册隆众资讯采集任务
            _run_oilchem_collect,
            CronTrigger(hour=int(h), minute=int(m)),
            args=[time_str],
            id=f"oilchem_collect_{time_str}",
            name=f"OilChem collect {time_str}",
            max_instances=1,
            misfire_grace_time=1800,
        )
    for time_str in oilchem_ingest_times:
        h, m = time_str.split(":")
        _scheduler.add_job(  # 【调用函数】注册隆众资讯入库任务(读归档,DB 去重,含归属校验)
            _run_oilchem_ingest,
            CronTrigger(hour=int(h), minute=int(m)),
            args=[time_str],
            id=f"oilchem_ingest_{time_str}",
            name=f"OilChem ingest {time_str}",
            max_instances=1,
            misfire_grace_time=3600,
        )'''


# ═══════════════════════════════════════════════════════════════════════════
# web_app.py —— 1 处补丁（主启动路径显式传参 + 启动日志）
# ═══════════════════════════════════════════════════════════════════════════
W_ANCHOR_1 = '''            archive_times=["09:20"],
        )
        print("Scheduler started: daily 08:00/18:00, research 08:10/18:00/22:00, "
              "warrant 08:00/16:40, wechat collect 08:30/18:30 + ingest 09:05/19:05")'''

W_NEW_1 = '''            archive_times=["09:20"],
            # 隆众资讯(2026-10-14 新增):采集 08:20/17:10(**免登录 HTTP,不占 LLM**);
            # 入库 10:00/20:00 —— 刻意避开研报三源窗口(08:25~09:45 / 18:15~19:35),
            # 账户并发上限 5,研报三源已占 2~3 路,入库逐篇走 LLM 必须错开。
            oilchem_collect_times=["08:20", "17:10"],
            oilchem_ingest_times=["10:00", "20:00"],
        )
        print("Scheduler started: daily 08:00/18:00, research 08:10/18:00/22:00, "
              "warrant 08:00/16:40, wechat collect 08:30/18:30 + ingest 09:05/19:05, "
              "oilchem collect 08:20/17:10 + ingest 10:00/20:00")'''


PATCHES = {
    "scheduler.py": [
        ("常量(状态目录+时点)", S_ANCHOR_1, S_NEW_1),
        ("两个 _run_oilchem_* 函数", S_ANCHOR_2, S_NEW_2),
        ("start_scheduler 签名", S_ANCHOR_3, S_NEW_3),
        ("参数文档", S_ANCHOR_4, S_NEW_4),
        ("默认值", S_ANCHOR_5, S_NEW_5),
        ("注册 job", S_ANCHOR_6, S_NEW_6),
    ],
    "web_app.py": [
        ("主启动路径传参 + 日志", W_ANCHOR_1, W_NEW_1),
    ],
}


def patch_file(fname: str, items: list, apply: bool) -> bool:
    """对单个文件应用补丁。

    【★ 关键·EOL 归一化】本项目 `scheduler.py` / `web_app.py` 是 **CRLF**，而本脚本
    （以及任何经工具生成的锚点字符串）是 **LF**。若直接拿 `newline=""` 读出的原文
    （含 `\\r\\n`）去比对 LF 锚点，**命中恒为 0** —— 这就是项目铁律说的
    「EOL 写错 ⇒ 锚点命中 0 ⇒ 静默不写入」。
    做法：读出后**归一化成 LF 比对**，写盘前**再转回原 EOL**，于是锚点怎么写都能命中，
    且原始换行风格被完整保留（自检 `裸LF==0`）。
    """
    path = ROOT / fname
    raw = open(path, "rb").read()
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    src = raw.decode("utf-8").replace("\r\n", "\n")   # ★ 归一化成 LF 供锚点比对
    ok = True
    print("\n── %s（原 EOL=%s）──" % (fname, "CRLF" if eol == b"\r\n" else "LF"))
    for label, anchor, new in items:
        n = src.count(anchor)
        if n != 1:
            print("  ✗ [%s] 锚点命中 %d 次（必须恰好 1 次）—— 跳过该处" % (label, n))
            ok = False
            continue
        print("  ✓ [%s] 锚点唯一" % label)
        src = src.replace(anchor, new, 1)
    if not ok:
        print("  ⇒ 有锚点未命中，本次**不写盘**（避免半套补丁）")
        return False
    if not apply:
        print("  （dry-run，未写盘）")
        return True

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + ".bak_oilchem_%s" % stamp)
    shutil.copy2(path, bak)
    out = src.replace("\n", eol.decode())            # ★ 写前转回原 EOL
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(out)

    b = open(path, "rb").read()
    crlf, lf = b.count(b"\r\n"), b.count(b"\n")
    bare = lf - crlf
    print("  已写入 | 备份: %s" % bak.name)
    print("  【EOL 自检】CRLF=%d 裸LF=%d ⇒ %s" % (crlf, bare, "PASS" if bare == 0 else "FAIL"))
    return bare == 0


def main() -> int:
    apply = "--apply" in sys.argv
    print("=" * 78)
    print("隆众资讯 → scheduler 接线补丁  %s" % ("【实际执行】" if apply else "【DRY-RUN】"))
    print("=" * 78)
    all_ok = True
    for fname, items in PATCHES.items():
        all_ok &= patch_file(fname, items, apply)
    print("\n" + ("全部补丁完成。" if all_ok else "有失败项，见上。"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
