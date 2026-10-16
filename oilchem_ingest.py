# -*- coding: utf-8 -*-
"""隆众资讯文章 → AgentSense 研报库 入库 adapter

把 `oilchem_news_collect.py` 采集到的隆众资讯接进既有研报链路
(落 .md → research_reports 落库 → 后台 LLM 提取观点 → 聚合/RAG)。

================================ 复用而非复制 ================================
本脚本**不重写入库逻辑**,而是复用 `wechat_ingest.ingest_one` 这个已上线、已验证的实现
(它已处理了全部难缠情形:DB 判重的幂等口径、processing 残留复跑、终态读回、正文过短门禁)。

复用的关键点:
  · `org = "%s-%s" % (SOURCE_LABEL, acct)` 里的 `SOURCE_LABEL` 是**运行时读取的模块常量**
    ⇒ 在导入时把它改成 "隆众资讯" 即可,不必改动 wechat_ingest.py 一个字节;
  · `entry_account()` **优先返回 `e["account"]`** ⇒ 只要条目自带 account(采集器写的是
    内容类别),它就不会去查公众号注册表,完全绕开 wechat 特有逻辑;
  · `entry_key/entry_date/entry_url` 读的字段与我们的条目结构逐字一致。
⚠️ `SOURCE_LABEL` **只改一次**(模块级),绝不在并发路径里改 —— 否则多线程会互相覆盖。
⚠️ 这是刻意的耦合:若将来 wechat_ingest 把 SOURCE_LABEL 写死成字面量,本脚本需同步调整。

================================ 为什么读 jsonl 而不是 json ================================
与公众号同理:采集器的 oilchem_news.json **每轮覆盖写**,一旦入库失败/进程被杀,
那份 new 会被下轮覆盖 ⇒ 这批文章永远进不了库且无痕迹。
`oilchem_news.jsonl` 是**追加式**,再靠 DB 去重 ⇒ 入库幂等且可重跑。

================================ 去重口径 ================================
以采集器的稳定键 `key`(= 文章 slug)为准,**不用 url**;对应
`database.get_research_report_by_external_key(key, url)` —— 这是**通用外部源接口**,非公众号专用。

用法:
    python oilchem_ingest.py                          # 默认:扫 jsonl,入库未入库的
    python oilchem_ingest.py --dry-run                # 只看会做什么,不写库
    python oilchem_ingest.py --categories 早读,聚焦     # 只入指定类别(控 LLM 成本)
    python oilchem_ingest.py --categories all         # 全部可读类别
    python oilchem_ingest.py --limit 30 --workers 3   # 回填 + 并行
    python oilchem_ingest.py --list-categories        # 看当前库里有哪些类别及篇数

================================ 并发 ================================
单篇端到端约 5~12 分钟(两次串行 LLM + chroma 索引)。`--workers N` 用有界线程池,
上限 3 —— 留 2 路余量给同账户其他链路(LLM 账户并发配额 5,研报采集器已占 2~3 路)。
⚠️ 回填期间**不要重启 web 服务**:它启动时会把 processing/pending 行当孤儿回收,
   与正在跑的本进程抢同一篇(= 双花 LLM)。
"""
import argparse
import datetime as dt
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import wechat_ingest as wi  # 【复用】入库核心(见模块 docstring 的"复用而非复制")
import oilchem_pool as op   # 【复用】主体品种映射(采集器与本脚本共用)

# ── 【关键】一次性改写源标签,让 source 落成 "隆众资讯-<类别>" ──
# 必须在导入后、任何 ingest_one 调用前完成;只改这一次,不要在并发路径里改。
wi.SOURCE_LABEL = "隆众资讯"

SOURCE_LABEL = "隆众资讯"
DEFAULT_MAX_PER_RUN = 20    # 【常量】单轮上限(挡"积压上百篇一次性烧 LLM")
DEFAULT_CATEGORIES = ""     # 【常量】默认不限类别(采集器已过滤掉登录墙与短文)

# ══════════════════════════════════════════════════════════════════════════
# 方案 B：入库后归属校验（★ 本项目接入宽覆盖源的必需一步）
#
# 【为什么不能只靠采集端门禁】实测(2026-10-14, 40 篇)：按"主体池外即拦"会拦掉 11 篇,
# 但其中 3 篇确有池内实质内容(DOTP→TA、磷酸铁→LC、丙酮→BZ)。
# 为省 LLM 丢真实内容是错误取舍 ⇒ 改为**入库后校验 + 改归属**：不丢内容,还能修正。
#
# 【拦的是哪两种缺陷】
#   ① 多品种行、首项是池外/上游 ⇒ `variety` 列(= codes[0])指错,
#      但 conclusion_md 里**已含正确的池内段落** ⇒ 改列即可,无需重跑 LLM;
#   ② LLM 返回空 varieties 走了关键词兜底,而隆众文章常把「橡胶/塑料」当下游需求方
#      顺口提及 ⇒ 兜底误判成 RU(实测基础油/磷酸/成品油/醚后各 3~5 次"橡胶")
#      ⇒ 这类主品种段落全是「未披露…无法判断」空转 ⇒ 标 skipped。
# ══════════════════════════════════════════════════════════════════════════
BOILER_RE = __import__("re").compile(r"未披露|无法判断|无法评估|均无")
CONCRETE_RE = __import__("re").compile(r"\d[\d,\.]*\s*(?:元/吨|元|万吨|吨|%|美元)")
BOILER_HARD = 9      # 空转词 >=9 ⇒ 确定空转
BOILER_SOFT = 7      # 空转词 >=7 且无具体数据 ⇒ 空转
_SECTION_RE = __import__("re").compile(r"##\s*([A-Z]{1,4})\s*结论")

VERIFY_STATS = {"keep": 0, "repoint": 0, "skip": 0, "preview": 0, "err": 0}


def _section(conclusion_md: str, code: str) -> str:
    """取 `## <code> 结论` 段落正文;无此段返回 ""。"""
    if not conclusion_md or not code:
        return ""
    m = __import__("re").search(
        r"##\s*" + __import__("re").escape(code) + r"\s*结论([\s\S]*?)(?=\n##\s*[A-Z]{1,4}\s*结论|\Z)",
        conclusion_md)
    return m.group(1) if m else ""


def _is_boilerplate(text: str) -> bool:
    """段落是否空转(通篇"未披露"且无具体数据)。"""
    if not text:
        return True
    b = len(BOILER_RE.findall(text))
    return b >= BOILER_HARD or (b >= BOILER_SOFT and not CONCRETE_RE.search(text))


def post_verify_attribution(report_id: int, title: str, dry_run: bool = False) -> str:
    """入库后校验归属并修正。返回 'keep' / 'repoint:XX' / 'skip' / 'err'。

    【判据】
      · 标题主体能映射到池内码,且 ≠ 当前主品种:
          该码段落**有实质内容** ⇒ 改归属(结论里本来就是多段,只是主品种指错);
          该码段落也空转 ⇒ 不动(避免改成一个同样空的归属)。
      · 标题主体判不出(**明确池外**)且当前主品种段落空转 ⇒ 标 skipped。
      · 其余保持。
    【为什么只在"目标段落有实质内容"时才改归属】否则会制造"归属改了但内容还是空的"假修复。
    """
    try:
        from database import get_db
        db = get_db()
        row = db.get_research_report(report_id) or {}
    except Exception as e:  # noqa: BLE001
        print("    ! 归属校验读取失败 id=%s: %s" % (report_id, e))
        return "err"

    if (row.get("status") or "") != "done":
        return "keep"

    cur = (row.get("variety") or "").strip().upper()
    concl = row.get("conclusion_md") or ""
    target = op.subject_code(title)

    if target and target != cur:
        if not _is_boilerplate(_section(concl, target)):
            if dry_run:
                print("    [DRY] 归属 %s → %s（目标段落有实质内容）" % (cur or "-", target))
                return "repoint"
            try:
                import json as _json
                sd = {}
                try:
                    sd = _json.loads(row.get("structured_data") or "{}")
                except Exception:
                    sd = {}
                # 池外码一并剔除,避免产生"不可达"的聚合项
                vs = [str(v.get("variety") or "").upper() for v in (sd.get("varieties") or [])]
                kept = [v for v in vs if v in op.POOL] or [target]
                if target not in kept:
                    kept.insert(0, target)
                sd["varieties"] = [v for v in (sd.get("varieties") or [])
                                   if str(v.get("variety") or "").upper() in set(kept)]
                db.update_research_report(report_id, variety=target,
                                          structured_data=_json.dumps(sd, ensure_ascii=False))
                print("    ~ 归属修正 %s → %s（标题主体判定）" % (cur or "-", target))
                return "repoint"
            except Exception as e:  # noqa: BLE001
                print("    ! 归属修正失败 id=%s: %s" % (report_id, e))
                return "err"
        return "keep"

    if not target and op.classify(title) == "out_of_pool":
        if _is_boilerplate(_section(concl, cur)):
            if dry_run:
                print("    [DRY] 主体池外且主品种段落空转 ⇒ 将标 skipped")
                return "skip"
            try:
                db.update_research_report(
                    report_id, status="skipped",
                    error="[归属校验 自动] 标题主体不在品种池内，且 %s 段落空转 ⇒ 无可用观点" % (cur or "-"))
                print("    ⊘ 主体池外且段落空转 ⇒ 已标 skipped")
                return "skip"
            except Exception as e:  # noqa: BLE001
                print("    ! 标记 skipped 失败 id=%s: %s" % (report_id, e))
                return "err"
    return "keep"

# 【状态目录】与采集器同一约定(见 oilchem_news_collect.STATE_DIR)
STATE_DIR = Path(os.path.expanduser((os.environ.get("AGENTSENSE_OILCHEM_DIR") or "").strip())
                 or (Path.home() / ".tradingagents" / "oilchem"))
F_INBOX = STATE_DIR / "oilchem_news.jsonl"
F_NEW = STATE_DIR / "oilchem_news.json"
F_RESULT = STATE_DIR / "oilchem_ingest_result.json"   # 【结果文件】调度器取数用


def _write_result(payload: dict) -> None:
    """把本轮统计写成机器可读 JSON,供调度器取数(而不是解析 stdout)。

    【为什么不能只靠 stdout】沿用 wechat_ingest 的实测结论:从服务进程启动本脚本时,
    子进程 stdout 可能整段丢失(同一条命令在交互式 shell 跑却正常)。⇒ 落文件当唯一
    事实来源;调度器在文件缺失时才退化解析 stdout。
    【为什么 tmp + os.replace】读方(调度器)与写方(本脚本)是两个进程,直接重写会被读到半截。
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = str(F_RESULT) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, F_RESULT)
    except Exception as e:  # noqa: BLE001
        print("    ! 结果文件写入失败(不影响本轮): %s" % e)


def _verify_entry(e: dict, dry_run: bool) -> str:
    """按外部去重键回查刚入库的行,再跑归属校验(ingest_one 只返回裁决串,不带 id)。

    【dry-run 行为】不查库、不写、也不预演"改归属"决策 —— 因为 LLM 尚未抽品种,
    无从知道"当前主品种是哪几个"。
    ⚠️ **不能**在 dry-run 下走查库分支:那时行还没插入,查不到 ⇒ 全部返回 'keep',
    输出会谎报"保持 N 条"（看着像校验通过,其实什么都没校验）。故单列 'preview'。
    """
    if dry_run:
        t = e.get("title") or ""
        print("    [DRY] 归属校验将以标题主体判定: 主体=%-6s 分类=%s"
              % (op.subject_code(t) or "-", op.classify(t)))
        return "preview"
    try:
        from database import get_db
        row = get_db().get_research_report_by_external_key(wi.entry_key(e), wi.entry_url(e))
        if not row:
            return "keep"
        return post_verify_attribution(int(row["id"]), e.get("title") or "", False)
    except Exception as ex:  # noqa: BLE001 - 校验失败不影响入库事实
        print("    ! 归属校验异常: %s" % str(ex)[:120])
        return "err"


def load_entries() -> list[dict]:
    """读追加式归档 jsonl(缺文件返回 [])。"""
    if not F_INBOX.exists():
        return []
    out = []
    for line in io.open(F_INBOX, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def entry_category(e: dict) -> str:
    """取条目的内容类别(采集器写 category;老条目退化按标题归类)。"""
    c = (e.get("category") or e.get("account") or "").strip()
    if c:
        return c
    return "资讯"


def list_categories(entries: list[dict]) -> None:
    """列出归档里各类别的篇数,便于决定 --categories 取值(不写库、不烧 LLM)。"""
    from collections import Counter
    cnt = Counter(entry_category(e) for e in entries)
    print("\n  归档内类别分布(%d 条):" % len(entries))
    for k, v in cnt.most_common():
        print("    %-12s %d" % (k, v))
    print("\n  用法: --categories %s  或 --categories all"
          % ",".join(k for k, _ in cnt.most_common()[:3]))


def main() -> int:
    ap = argparse.ArgumentParser(description="隆众资讯文章入库 AgentSense 研报库")
    ap.add_argument("--categories", default=DEFAULT_CATEGORIES,
                    help="只入库这些类别(逗号分隔;all 或留空=不限)")
    ap.add_argument("--limit", type=int, default=DEFAULT_MAX_PER_RUN,
                    help="单轮最多入库 N 篇,按时间**最旧优先**(0=不限;默认 %d)" % DEFAULT_MAX_PER_RUN)
    ap.add_argument("--dry-run", action="store_true", help="只判定不写库")
    ap.add_argument("--workers", type=int, default=None,
                    help="并行处理路数(默认 1=串行;上限 %d;env WECHAT_INGEST_WORKERS 可覆盖)"
                         % wi.MAX_WORKERS)
    ap.add_argument("--list-categories", action="store_true", help="只列类别分布,不入库")
    ap.add_argument("--report-subjects", action="store_true",
                    help="只按标题主体做品种归类报告(不写库不烧 LLM),看门禁/校验会怎么判")
    ap.add_argument("--no-verify", action="store_true",
                    help="关闭入库后归属校验(默认**开启**;校验不额外烧 LLM)")
    ap.add_argument("--state-dir", help="覆盖状态目录(默认 $AGENTSENSE_OILCHEM_DIR 或 ~/.tradingagents/oilchem)")
    args = ap.parse_args()

    if args.state_dir:
        global STATE_DIR, F_INBOX, F_NEW, F_RESULT
        STATE_DIR = Path(os.path.expanduser(args.state_dir))
        F_INBOX = STATE_DIR / "oilchem_news.jsonl"
        F_NEW = STATE_DIR / "oilchem_news.json"
        F_RESULT = STATE_DIR / "oilchem_ingest_result.json"

    print("=" * 78)
    print("【隆众资讯入库】目录=%s   时间=%s"
          % (STATE_DIR, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    entries = load_entries()

    if args.list_categories:
        list_categories(entries)
        return 0

    if args.report_subjects:
        rep = op.report([(e.get("title") or "") for e in entries])
        print("\n  池内品种 %d 个 | 分类统计: %s" % (rep["pool_size"], rep["counts"]))
        for k in ("in_pool", "adjacent", "out_of_pool"):
            print("\n  【%s】%d 条" % (k, len(rep["buckets"][k])))
            for t in rep["buckets"][k]:
                print("    %-6s %s" % (op.subject_code(t) or "-", t[:60]))
        print("\n  说明: out_of_pool = 明确判出池外主体（**仅供参考，不自动拦截**——"
              "实测误伤过 DOTP→TA / 磷酸铁→LC / 丙酮→BZ 这类有池内内容的文章）")
        return 0

    cats = {c.strip() for c in args.categories.split(",") if c.strip()}
    if cats and "all" not in {c.lower() for c in cats}:
        before = len(entries)
        entries = [e for e in entries if entry_category(e) in cats]
        print("  类别过滤 %s: %d → %d 条" % (sorted(cats), before, len(entries)))
    else:
        print("  类别过滤: 不限")

    # 去重(同一 key 可能出现多次) + 按时间升序(最旧优先,避免积压时新文永久饿死)
    uniq = {}
    for e in entries:
        k = wi.entry_key(e)
        if k:
            uniq[k] = e
    items = sorted(uniq.values(), key=lambda x: int(x.get("create_time") or 0))
    print("  读入 %d 条，去重后 %d 条" % (len(entries), len(items)))

    if not items:
        print("\nCollected: 0\nProcessed: 0")
        _write_result({"ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "dry_run": bool(args.dry_run), "collected": 0, "processed": 0,
                       "inserted": 0, "usable": 0, "retried": 0, "skipped": 0, "failed": 0})
        return 0

    if args.limit and len(items) > args.limit:
        print("  超出单轮上限 %d，本次只处理最旧的 %d 篇，其余留待下轮" % (args.limit, args.limit))
        items = items[:args.limit]

    stats = {"ok": 0, "okerr": 0, "retry": 0, "retryerr": 0,
             "dup": 0, "short": 0, "empty": 0, "nokey": 0, "err": 0}
    _KNOWN = set(stats)

    def _bump(v):
        stats[v if v in _KNOWN else "err"] += 1

    def _run_serial():
        for e in items:
            try:
                v = wi.ingest_one(e, dry_run=args.dry_run)
                _bump(v)
                if v in ("ok", "retry") and not args.no_verify:
                    # ⚠️ 必须把 args.dry_run 透传下去 —— 否则 `--dry-run` 只挡住了入库，
                    #    归属校验仍会**真写库**（空跑却改了状态/variety）。实测踩过：全 keep 才没暴露。
                    vr = _verify_entry(e, args.dry_run)
                    VERIFY_STATS[vr] = VERIFY_STATS.get(vr, 0) + 1
            except Exception as ex:  # noqa: BLE001 - 单篇失败不阻断整批
                stats["err"] += 1
                print("    ✗ 入库异常: %s | %s" % ((e.get("title") or "")[:40], str(ex)[:160]))

    workers = wi._resolve_workers(args.workers)
    warm = None
    if not args.dry_run and workers > 1:
        warm = wi._warm_up()   # 【复用】在调用线程里完成重导入(避免非主线程初始化)

    if args.dry_run or workers <= 1 or warm:
        if warm:
            print("  ! %s —— 本次回退为串行" % warm)
        _run_serial()
    else:
        print("  [并发] %d 篇 / %d 路并行(LLM 账户配额 5,本链路占 %d 路)"
              % (len(items), workers, workers))
        lock = threading.Lock()
        done = 0
        real_out = sys.stdout
        router = wi._ThreadRoutedStdout(real_out)   # 【复用】线程感知 stdout 路由
        sys.stdout = router

        def _work(e):
            t0 = time.time()
            buf = io.StringIO()
            router.buffer(buf)
            try:
                v = wi.ingest_one(e)
                if v in ("ok", "retry") and not args.no_verify:
                    vr = _verify_entry(e, False)   # 并发路径只在不 dry_run 时进入
                    VERIFY_STATS[vr] = VERIFY_STATS.get(vr, 0) + 1
                return e, v, None, time.time() - t0, buf.getvalue()
            except Exception as ex:  # noqa: BLE001
                return e, None, str(ex)[:160], time.time() - t0, buf.getvalue()
            finally:
                router.buffer(None)

        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="oc-ingest") as pool:
                futs = [pool.submit(_work, e) for e in items]
                for fut in as_completed(futs):
                    e, verdict, err, took, out = fut.result()
                    with lock:
                        done += 1
                        for line in out.splitlines():
                            print("  [%d/%d]%s" % (done, len(items), line))
                        if err:
                            stats["err"] += 1
                            print("  [%d/%d] ✗ %.0fs %s | %s"
                                  % (done, len(items), took, (e.get("title") or "")[:36], err))
                        else:
                            _bump(verdict)
                            print("  [%d/%d] %s %.0fs %s"
                                  % (done, len(items), verdict, took, (e.get("title") or "")[:36]))
        finally:
            sys.stdout = real_out

    inserted = stats["ok"] + stats["okerr"]
    retried = stats["retry"] + stats["retryerr"]
    usable = stats["ok"] + stats["retry"]

    print("")
    print("=" * 78)
    print("【汇总】新入库 %d 篇（有效观点 %d 篇 / 池外或非投研 %d 篇，均已在库留痕）| "
          "重跑 processing 残留 %d 篇（有效 %d 篇）| "
          "跳过(已入库 %d / 无正文 %d / 正文过短 %d / 无键 %d) | 失败 %d"
          % (inserted, usable, stats["okerr"] + stats["retryerr"],
             retried, stats["retry"],
             stats["dup"], stats["empty"], stats["short"], stats["nokey"], stats["err"]))
    print("Collected: %d" % len(items))
    print("Processed: %d" % (inserted + retried))
    if not args.no_verify:
        if args.dry_run:
            print("【归属校验】[DRY] 预演 %d 条（未写库；真实判定需 LLM 抽品种后才有依据）"
                  % VERIFY_STATS.get("preview", 0))
        else:
            print("【归属校验】保持 %d | 改归属 %d | 标 skipped %d%s"
                  % (VERIFY_STATS.get("keep", 0), VERIFY_STATS.get("repoint", 0),
                     VERIFY_STATS.get("skip", 0),
                     (" | 异常 %d" % VERIFY_STATS["err"]) if VERIFY_STATS.get("err") else ""))
    _write_result({
        "ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dry_run": bool(args.dry_run), "categories": args.categories or "all",
        "collected": len(items), "processed": inserted + retried,
        "inserted": inserted, "usable": usable, "retried": retried,
        "skipped": stats["dup"] + stats["empty"] + stats["short"] + stats["nokey"],
        "failed": stats["err"],
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
