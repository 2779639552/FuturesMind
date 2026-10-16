"""ingest_local_pdfs.py — 把本机暂存的 PDF 研报批量录入研报库(一次性工具)

【模块角色】
  从本地目录(如 Desktop/project4/研报暂存)批量录入 PDF 研报,复用研报链路:
  PDF 复制到 RESEARCH_UPLOAD_DIR/{机构}/ → insert_research_report(variety 留空)
  → web_app._process_research_report(LLM 自动识别多品种 → 落库 + 按品种写
  external_data/{CODE}_research.json)。消费端(分析师/run_analysis 工具)零改动。

  典型用法(先只测用户点名的几个品种):
    python ingest_local_pdfs.py --dir "研报暂存/周报8.31" --org 永安期货 \
        --variety PG SC FU PX TA EG LC            # → 命中 LPG/原油/燃料油/聚酯/碳酸锂 5 份
    python ingest_local_pdfs.py --dir "研报暂存/20260831周报/20260831周报" --org 中信期货 \
        --variety SC PG PX TA EG FU LU            # → 命中 原油/LPG/PX-TA-EG/沥青低高硫燃油 4 份

【幂等】
  目标目录已有同名文件(同机构子目录下)则跳过 → 重跑不重复入库(不删已入库行)。
  正文 <200 字符(扫描件无 OCR/空文本)跳过不入库,与华泰/fxbaogao 正文阈值一致。

【品种过滤】
  --variety 传标准代码(可重复):按文件名命中该品种的中文/代码别名筛选。中信文件
  名形如「【中信期货板块（子栏目）】标题」,取【】内第一个括号段做代码探测(规避
  「板块策略」这类标题里也带品种词但实际是全板块的误命中);永安文件名形如「品种
  周报日期」,取「周报」前的段。不传 --variety 表示目录全部录入。

【并发(2026-09-14,两阶段)】
  原实现逐个文件串行处理——同一时间只有一篇研报在跑,批量导入一个目录要等很久。
  现拆成两个阶段(两阶段都并行):
    阶段一「登记」:并行(worker 数同上)**完全不调 LLM**(逐篇抽正文 + 幂等检查 +
      复制 + 建行 status=pending),把整批命中文件一次性登记进研报库排队。**研报库
      立刻显示整批(排队中),不会再出现"忙了半天只有一篇在处理"的观感**——旧实现
      是"worker 抓到第几个文件才建第几行",所以同一时刻列表里最多只有 worker 数
      那么几行,串行时更永远只有 1 行。
      【为什么登记也要并行】抽正文是纯 CPU,实测中信周报 0.7~4.6 MB 单篇偶发要
      数十秒,串行登记 10 篇就拉了 13 分钟 —— 期间研报库只多出寥寥数行,用户看到
      的就是"只有一篇在处理"。并行后整批落在 1 分钟内。
    阶段二「处理」:**线程池并行**跑 _process_research_report(LLM 识别品种 + 逐品种
      结论 + 写聚合 + RAG 索引),墙钟 ≈ 总工作量 / worker 数。
  缺省 4 个 worker(LLM 账户并发上限 5,留 1 路余地给定时采集/网页上传),可用
  --workers 或环境变量 LOCAL_IMPORT_WORKERS 调整。
  并发安全依赖既有前提:_process_research_report 早已被三家 API 采集器的线程池
  (PARALLEL_WORKERS=4/4/2)并发调用;DB 走 threading.local + WAL;聚合 JSON 读改写
  由 research_data._FILE_LOCK 互斥;新增的"目标存在→复制"窗口另用 copy_lock 互斥。

【审计日志】
  导入全程追加写 `local_import.log`(项目根,与 watchdog.log 同级):每篇的
  QUEUE/SKIP/ERROR、report_id、处理墙钟、批次汇总。web_app 没有 basicConfig,
  logger.info 默认被丢——没有这份日志时,导入出了什么事只能翻数据库倒推。
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)  # 【变量】模块日志器(并入 web_app/scheduler 既有配置)

# ── 审计日志(durable) ───────────────────────────────────────────────────
# 【为什么需要】web_app 从未调用 logging.basicConfig,根 logger 缺省 level=WARNING,
#   于是本模块的 logger.info(...) 会被直接丢弃:一次本地导入到底命中几篇、登记了
#   几篇 id、哪些是幂等跳过、每篇各花了多久——服务一重启(或 stdout 被守护脚本重定向
#   截断)就全没了,只能翻数据库倒推。2026-09-14 排查"批量导入看起来只处理一篇"时就
#   卡在这里。此处给本模块 logger 单独挂一个 FileHandler,只影响本地导入自身,
#   不碰 web_app/三家采集器的日志配置。
_JOURNAL_PATH = Path(__file__).resolve().parent / "local_import.log"  # 【变量】导入审计日志(可用 LOCAL_IMPORT_LOG 覆盖)
_journal_lock = threading.Lock()  # 【锁】挂 handler 只做一次
_journal_ready = False  # 【变量】是否已挂(进程内)


def _attach_journal() -> None:
    """给本模块 logger 挂文件 handler(进程内只挂一次)。

    【关键逻辑】失败一律静默吞掉:审计日志是"锦上添花",磁盘满/无权限时绝不能把
              导入本身搞崩。设 level=INFO 是必须的——否则 INFO 级事件仍被过滤。
              路径可用环境变量 LOCAL_IMPORT_LOG 覆盖(测试指向临时目录,不污染项目根)。
    """
    global _journal_ready
    if _journal_ready:
        return
    with _journal_lock:
        if _journal_ready:
            return
        try:
            path = Path(os.getenv("LOCAL_IMPORT_LOG") or _JOURNAL_PATH)
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            )
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            _journal_ready = True
        except Exception:  # 【异常】无权限/磁盘满:放弃审计,不影响导入
            pass


def _journal(message: str) -> None:
    """把一条导入事件追加到审计日志(未挂 handler 时静默丢弃)。"""
    if _journal_ready:
        logger.info(message)

# ── 常量 ────────────────────────────────────────────────────────────────

# 【变量】品种代码 → 文件名命中别名(大小写不敏感;探测段代码先转大写再子串匹配)
# FU/LU 共用「燃油」:低高硫燃油(LU/FU 合并报告)可同时命中两者。
CODE_ALIASES = {
    "PG": ("LPG", "液化石油气"),
    "SC": ("原油",),
    "FU": ("FU", "燃料油", "高硫", "燃油"),
    "LU": ("LU", "低硫", "燃油"),
    "LC": ("LC", "碳酸锂"),
    "PX": ("PX", "对二甲苯", "聚酯"),
    "TA": ("TA", "PTA", "精对苯二甲酸", "聚酯"),
    "EG": ("EG", "乙二醇", "聚酯"),
    "BU": ("BU", "沥青"),
    "MA": ("MA", "甲醇"),
    "FG": ("FG", "玻璃"),
    "SA": ("SA", "纯碱", "重碱"),
    "V": ("V", "PVC", "聚氯乙烯"),
    "PP": ("PP", "聚丙烯"),
    "L": ("L", "塑料", "聚乙烯"),
    "UR": ("UR", "尿素"),
    "PF": ("PF", "短纤", "聚酯"),
    "RU": ("RU", "天然橡胶", "橡胶"),
    "NR": ("NR", "20号胶", "20号"),
    "SH": ("SH", "烧碱"),  # 【2026-09-30 修正】原写"合成橡胶"——那是 BR 的别名(误标)
    "EB": ("EB", "苯乙烯", "纯苯"),
    # 【2026-09-30 补齐】池内品种此前整批缺失 ⇒ 这些品种的本地研报会被判"与品种无关"而丢弃。
    "M": ("M", "豆粕"),
    "CF": ("CF", "棉花", "郑棉"),
    "CJ": ("CJ", "红枣"),
    "LH": ("LH", "生猪", "猪"),
    "BR": ("BR", "丁二烯橡胶", "顺丁橡胶"),
    "BZ": ("BZ", "纯苯", "石油苯"),
    "PS": ("PS", "多晶硅", "硅料"),
    "SI": ("SI", "工业硅"),
    # 【2026-09-30 入池】棕榈油:中文报告文件名的常见写法都收进来
    "P": ("P", "棕榈油", "棕榈", "棕油", "马棕"),
}
MIN_BODY_CHARS = 200  # 【变量】正文最小字符数(去空白),低于则跳过不入库
RESERVED_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')  # 【变量】Windows 文件名保留字

# 【并发】本地导入并行 worker 数(2026-09-14 优化)
#   原实现逐个文件串行处理(同一时间只有一篇研报在跑),批量导入一个目录要等很久。
#   改为线程池并行:单篇的大头是 LLM 同步调用(每篇同时只挂 1 个调用),故
#   N 个 worker ≈ N 路并发 LLM 调用。LLM 账户并发上限为 5,故缺省取 4 —— 留 1 路
#   余量给其他链路(定时采集 / 网页上传),避免 429;上限也钳到 5。
#   可用环境变量 LOCAL_IMPORT_WORKERS 覆盖(内部钳制到 [1, MAX_WORKERS])。
DEFAULT_WORKERS = 4  # 【变量】缺省并行 worker 数
MAX_WORKERS = 5      # 【变量】worker 数上限(= LLM 账户并发上限,再高只会触发 429)
_NOOP_LOCK = threading.Lock()  # 【变量】单线程登记路径用的空锁(退化情形不留分支)


def _file_probe(stem: str) -> str:
    """从文件名取品种探测段。

    【关键逻辑】中信文件形如「【中信期货板块（子栏目）】标题」——取【】内第一个
    （）段(真正的品种/子栏目),避免标题里的品种词(如「原油和化工延续低库存」的
    板块策略)造成误命中;永安文件形如「品种周报日期」,取「周报」前的段;若文件名
    含【】但【】内无（()段落(如中英双语周度综述)则返回空——这类跨板块综述不由
    品种过滤选中。
    """
    if "【" in stem:  # 【中信期货…】结构:必须带（…）子栏目段才探测
        m = re.search(r"【[^】]*?（([^（）]*)）", stem)
        return m.group(1).strip() if m else ""
    return stem.split("周报", 1)[0].strip()


def _alias_in_probe(probe: str, alias: str) -> bool:
    """别名是否命中探测段。拉丁别名用词边界匹配(避免 FU 命中 English "Futures");
    中文别名用子串匹配。probe 与 alias 均为大写。
    """
    if alias.isascii() and alias.isalnum():
        return re.search(rf"\b{re.escape(alias)}\b", probe) is not None
    return alias in probe


def match_varieties(filename: str, requested: set[str]) -> list[str]:
    """按文件名判定文件命中哪些请求的品种代码(顺序保持 requested 传入顺序)。

    【返回】命中的代码列表;与任何请求代码都无关则返回空列表。
    """
    stem = Path(filename).stem
    probe = _file_probe(stem).upper()
    if not probe:
        return []
    hits = []
    for code in requested:
        if code in hits:
            continue
        aliases = CODE_ALIASES.get(code, ())
        if any(_alias_in_probe(probe, a.upper()) for a in aliases):
            hits.append(code)
    return hits


# ── 阶段一:单文件登记(串行、不调 LLM) ─────────────────────────────────

def _stage_local_pdf(org: str, pdf: Path, copy_lock: threading.Lock | None = None) -> tuple[int | None, str]:
    """把一份本地 PDF **登记**进研报库排队(只抽文本+复制+建行,不调 LLM)。

    【参数】copy_lock: 互斥"目标是否存在 → 复制"这段检查-写入窗口;并行登记时若
            两个源文件解析出同一个 safe_name,没有这把锁会双写同一目标文件。
    【返回】(report_id, msg):report_id 为 None 表示**未登记**(正文过短 / 幂等跳过),
            msg 为原因;成功时 msg 形如 "report_id=123"。
    【关键逻辑】1) 正文(去空白) < MIN_BODY_CHARS 直接跳过(扫描件无 OCR 等);
              2) 目标机构目录已有同名文件 → 幂等跳过(重跑不重复入库);
              3) 复制 PDF 原件到 RESEARCH_UPLOAD_DIR/{org}/ 后建行,status="pending"
              ——由阶段二的 worker 认领并推进到 processing/done/error。
    【为什么单独拆出来】这一步不调 LLM、每篇约 0.2s,放在登记阶段可以把**整批**
                      文件先登记进库,让研报库立刻看得到全貌(排队中);LLM 那段
                      耗时的大头才交给线程池并行。
    """
    # 懒导入:复用 web_app 的存储目录/PDF 提取链路(避免模块加载重)
    from database import get_db  # 【调用包】数据库实例(落 research_reports 表)
    from web_app import RESEARCH_UPLOAD_DIR, _extract_pdf_text  # 【调用包】存储目录 + PDF 抽文本

    text = (_extract_pdf_text(pdf) or "").strip()
    if len(text) < MIN_BODY_CHARS:
        return None, "正文过短/无可提取文本"

    safe_name = RESERVED_RE.sub("_", pdf.name) or pdf.stem  # 【变量】目标文件名(保留字字符替换)
    upload_dir = RESEARCH_UPLOAD_DIR / org  # 【变量】该机构子目录(与 fxbaogao 同规则)
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / safe_name
    _lock = copy_lock or _NOOP_LOCK  # 【变量】未传锁时用进程级空锁(单线程调用路径)
    with _lock:  # 【锁】exists→copy 窗口互斥,避免并行登记同名双写
        if target.exists():
            return None, "目标已存在(幂等跳过)"
        shutil.copy2(str(pdf), str(target))  # 【调用函数】复制 PDF 原件(保留时间戳)

    report_id = get_db().insert_research_report(
        variety="",
        title=pdf.stem,
        source=f"{org}-研报暂存",
        filename=pdf.name,
        file_path=str(target),
        status="pending",  # 【状态】已登记排队,等阶段二 worker 认领(区别于 processing=正在跑)
        ingest_source="auto",  # 【来源】本地批量导入(非网页手动上传,数据仓库徽标归"自动")
    )
    return report_id, f"report_id={report_id}"


# ── 阶段二:worker 认领后跑完整 LLM 链路 ────────────────────────────────

def _process_queued(report_id: int) -> None:
    """worker 侧入口:把已登记(pending)的研报跑完整处理链路。

    【调用链】web_app._process_research_report(report_id):重抽文本 → LLM 第一步识别
              品种/数据 → 第二步逐品种结论 → status=done + 写聚合 JSON → RAG 索引。
    【异常】_process_research_report 内部自带兜底(失败写 status=error + error 字段),
            不向外抛;这里再包一层只为防御性记录。
    """
    from web_app import _process_research_report  # 【调用包】web_app 后台处理链路

    _process_research_report(report_id)


# ── 批量入口 ────────────────────────────────────────────────────────────

def _resolve_workers(workers: int | None = None) -> int:
    """解析并钳制并行 worker 数:显式参数 > 环境变量 LOCAL_IMPORT_WORKERS > 缺省 4。

    【返回】落在 [1, MAX_WORKERS] 的整数。非法/缺失一律回退缺省,不抛错(导入不该
            因为一个环境变量写错就跑不起来)。
    """
    if workers is None:
        raw = (os.getenv("LOCAL_IMPORT_WORKERS") or "").strip()
        if raw:
            try:
                workers = int(raw)
            except ValueError:
                workers = None
    if not workers or workers < 1:
        workers = DEFAULT_WORKERS
    return min(int(workers), MAX_WORKERS)


def _warm_up_web_app() -> str | None:
    """在**调用线程**里先完成 web_app 的重导入,返回 None 表示成功、否则返回错误文本。

    【为什么必须预热】web_app 的模块级初始化较重(建 Flask app、拉起 RAG worker
              子进程、建上传目录)。原串行实现是在主线程里首次 import;改成线程池后
              若不预热,首个 worker 线程会承担这次非主线程初始化——部分库对主线程
              有依赖,存在隐患。故在开线程池之前先把这步做完。
    """
    try:
        from web_app import RESEARCH_UPLOAD_DIR  # noqa: F401  【调用包】仅预热导入
        return None
    except Exception as e:  # 【异常】预热失败:直接失败退出,不冒险进线程池
        return f"预热导入 web_app 失败: {e}"


def ingest_dir(pdf_dir: Path, org: str, requested: set[str], dry_run: bool,
               workers: int | None = None) -> dict:
    """录入目录下命中品种的 PDF(全部或 --variety 过滤)——**先全批登记,再多线程并行处理**。

    【参数】workers: 并行 worker 数;None = 取环境变量 LOCAL_IMPORT_WORKERS 或缺省 4。
    【返回】{"matched","imported","skipped","errors","hits"} 统计与逐文件命中详情。
           imported = **成功登记进库**的篇数(已在研报库排队,由 worker 继续处理);
           skipped  = 未登记篇数(正文过短 / 幂等跳过);errors = 登记阶段的异常。
    【关键逻辑】1) 先按文件名列出命中清单(单线程、纯字符串匹配,很快),再由调用方
              决定是否真正执行(小样测试:先跑 --dry-run 确认计数再真跑);
              2) 【阶段一·登记】串行跑完整批(抽正文 0.2s/篇 + 幂等 + 复制 + 建行
              status=pending),**不调 LLM** —— 研报库立刻显示全部命中文件在排队,
              根治"忙半天只看到一篇在处理";
              3) 【阶段二·处理】线程池并行跑 _process_research_report,单文件异常在
              worker 内兜住 → 不中断整批;4) 按**完成顺序**回报进度(长批量导入可见
              进展),故用 as_completed 而非 map;计数器只在调用线程累加,不需要额外
              锁,仅 print 加锁防交错。
    【注意】阶段二里 _process_research_report 内部自带兜底:单篇 LLM 失败会写
           status=error + error 字段(研报库显示"失败"),不会抛到这里,所以 errors
           通常只反映登记阶段的问题。
    """
    pdfs = sorted(pdf_dir.glob("*.pdf"), key=lambda p: p.name)
    hits = []  # 【变量】hits: [(Path, [命中的品种代码])]
    for p in pdfs:
        if requested:  # 指定品种:按文件名命中过滤
            codes = match_varieties(p.name, requested)
            if not codes:
                continue
        else:  # 未指定品种:目录全部
            codes = []
        hits.append((p, codes))

    matched = len(hits)  # 【变量】命中数(与逐条累加等价,提前取长度更直观)
    hit_names = [(p.name, c) for p, c in hits]

    if dry_run:  # 【保留】只列命中清单:不开线程、不写库、不调 LLM
        for p, codes in hits:
            print(f"  [DRY] {p.name}  [{','.join(codes)}]")
        return {"matched": matched, "imported": 0, "skipped": 0,
                "errors": [], "hits": hit_names}

    if not hits:  # 空目录/无命中:无需预热与开池
        return {"matched": 0, "imported": 0, "skipped": 0, "errors": [], "hits": []}

    warm_err = _warm_up_web_app()  # 【预热】必须在调用线程完成(见函数说明)
    if warm_err:
        return {"matched": matched, "imported": 0, "skipped": 0,
                "errors": [warm_err], "hits": hit_names}

    n_workers = _resolve_workers(workers)
    total = len(hits)
    print_lock = threading.Lock()  # 【变量】多线程 print 互斥(避免进度行互相插断)
    _attach_journal()  # 【审计】挂文件 handler(失败静默)

    # ── 阶段一·登记:并行跑完整批(不调 LLM) ────────────────────────────
    # 为什么先全批登记:旧实现是"worker 抓到第几个文件才建第几行",所以研报库同一
    # 时刻最多只有 worker 数那么几行(串行时永远只有 1 行),看着像"一条一条来"。
    # 登记不调 LLM,瓶颈只在"抽正文 + 复制"这类纯 CPU/IO —— 同样可以并行:
    #   2026-09-14 实测这批中信周报单篇 0.7~4.6 MB,串行抽正文偶发要数十秒,
    #   10 篇拉了 13 分钟才登记完;并行后整批落在 1 分钟内,研报库立刻见全貌。
    queue: list[tuple[int, Path, list[str]]] = []  # 【变量】queue: [(report_id, 源路径, 命中品种)]
    skipped = 0
    errors: list[str] = []
    copy_lock = threading.Lock()  # 【锁】exists→copy 窗口互斥(并行登记防同名双写)
    _journal(
        f"==== start org={org} dir={pdf_dir} matched={total} workers={n_workers} ===="
    )
    print(f"  [并发] 本地导入: {total} 个文件,先并行登记排队,再 {n_workers} 路并行处理")

    def _stage_one(item):
        """worker 侧:登记单个文件;异常在此收敛成 (err 文案),不炸整批。"""
        pos, (p, codes) = item
        try:
            rid, msg = _stage_local_pdf(org, p, copy_lock)
        except Exception as e:  # 【异常】登记失败:记录后继续登记其余文件
            return pos, p, codes, None, "", f"{e}"
        return pos, p, codes, rid, msg, ""

    stage_workers = max(1, min(n_workers, total))  # 【变量】登记并发(文件少时不空开线程)
    with ThreadPoolExecutor(max_workers=stage_workers, thread_name_prefix="localstage") as spool:
        # map 按传入顺序产出 → 进度行天然有序,和串行时的观感一致
        for pos, p, codes, rid, msg, err in spool.map(_stage_one, list(enumerate(hits, start=1))):
            tag = f"[{pos}/{total}]"
            if err:
                errors.append(f"{p.name}: {err}")
                print(f"  {tag} ! {p.name} [{','.join(codes)}] 登记异常: {err}")
                _journal(f"{tag} ERROR {p.name} :: {err}")
            elif rid is None:
                skipped += 1
                print(f"  {tag} - {p.name} [{','.join(codes)}] {msg}")
                _journal(f"{tag} SKIP {p.name} :: {msg}")
            else:
                queue.append((rid, p, codes))
                print(f"  {tag} + {p.name} [{','.join(codes)}] {msg} 已排队")
                _journal(f"{tag} QUEUE id={rid} {p.name}")

    registered = len(queue)
    imported = registered  # 【口径】imported = 成功登记进库(已在研报库排队)的篇数
    if not queue:  # 【保留】全批都跳过/失败:无需开池
        logger.info(
            "Local PDF import done (nothing queued): org=%s dir=%s matched=%d skipped=%d errors=%d",
            org, pdf_dir, matched, skipped, len(errors),
        )
        _journal(f"==== end (nothing queued) matched={matched} skipped={skipped} errors={len(errors)} ====")
        return {"matched": matched, "imported": 0, "skipped": skipped,
                "errors": errors, "hits": hit_names}

    # ── 阶段二·处理:线程池并行跑 LLM 链路 ────────────────────────────────
    print(f"  [并发] 登记完成: {registered} 篇排队中,{n_workers} 路并行处理中…")
    _journal(f"---- staged {registered} queued, {skipped} skipped, {len(errors)} errors ----")
    done = 0
    with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="localpdf") as pool:
        # 提交时刻一并记下:完成后能算出单篇墙钟(排查"一篇要多久"最直接的证据)
        future_map = {
            pool.submit(_process_queued, rid): (rid, p, codes, time.time())
            for rid, p, codes in queue
        }
        for fut in as_completed(future_map):  # 【关键】按完成顺序回报进度
            rid, p, codes, t_sub = future_map[fut]
            err = None
            try:
                fut.result()
            except Exception as e:  # 【异常】worker 抛出(极少,内部已兜底)
                err = str(e)
            took = time.time() - t_sub
            with print_lock:
                done += 1
                if err:
                    print(f"  [{done}/{registered}] ! {p.name} [{','.join(codes)}] 处理异常: {err}")
                else:
                    print(f"  [{done}/{registered}] ✓ {p.name} [{','.join(codes)}] 处理完成 (id={rid}, {took:.0f}s)")
            _journal(
                f"[done {done}/{registered}] id={rid} took={took:.0f}s "
                f"{'ERROR ' + err if err else 'OK'} {p.name}"
            )
            if err:
                errors.append(f"{p.name}: {err}")

    logger.info(
        "Local PDF import done: org=%s dir=%s workers=%d matched=%d imported=%d skipped=%d errors=%d",
        org, pdf_dir, n_workers, matched, imported, skipped, len(errors),
    )
    _journal(
        f"==== end matched={matched} imported={imported} skipped={skipped} errors={len(errors)} ===="
    )
    return {"matched": matched, "imported": imported, "skipped": skipped,
            "errors": errors, "hits": hit_names}


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> int:
    """CLI 入口:--dir 必填;--org 必填;--variety 可重复过滤;--dry-run 只列不写。"""
    ap = argparse.ArgumentParser(description="本机 PDF 研报批量录入(复用研报 LLM 链路,多线程并行)")
    ap.add_argument("--dir", required=True, help="PDF 源目录")
    ap.add_argument("--org", required=True, help="机构名(如 永安期货/中信期货),决定目标子目录")
    ap.add_argument("--variety", nargs="+", help="只录入命中这些品种代码的文件(空格分隔多个),缺省=目录全部")
    ap.add_argument("--dry-run", action="store_true", help="只列命中的 PDF,不复制不写库不调 LLM")
    ap.add_argument("--workers", type=int, default=None,
                    help=f"并行处理线程数(缺省 {DEFAULT_WORKERS},自动钳制到 [1, {MAX_WORKERS}];"
                         f"每篇同时只挂 1 个 LLM 调用,账户并发上限 5)")
    args = ap.parse_args()

    pdf_dir = Path(args.dir)
    if not pdf_dir.is_dir():
        print(f"目录不存在: {pdf_dir}")
        return 2
    requested = {c.upper() for c in (args.variety or [])}
    unknown = requested - set(CODE_ALIASES)
    if unknown:
        print(f"未支持的品种代码: {sorted(unknown)} (支持: {sorted(CODE_ALIASES)})")
        return 2

    t0 = time.time()
    res = ingest_dir(pdf_dir, args.org, requested, dry_run=args.dry_run, workers=args.workers)
    mode = "DRY-RUN" if args.dry_run else "IMPORT"
    print(f"[{args.org}] {mode} Matched: {res['matched']} / Imported: {res['imported']}"
          f" / Skipped: {res['skipped']} / Errors: {len(res['errors'])}")
    for e in res["errors"][:10]:
        print(f"  - {e}")
    print(f"Took: {time.time() - t0:.1f}s")
    return 0 if not res["errors"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 【调用】Windows 控制台 UTF-8
    raise SystemExit(main())
