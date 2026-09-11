"""research_collector_dongzheng.py — 东证期货「繁微 Fiona」研报/观点自动接入

【模块角色】
  从繁微 Fiona MCP(report/viewpoint 端点,均匿名可调用)拉东证期货研报与观点,
  写入本机研报库并复用 web_app 的 _process_research_report LLM 链路 + RAG 索引,
  与发现报告(research_collector.py)/华泰天玑(research_collector_htfc.py)/
  国君云 API(research_collector_gtja.py)同架构。

  采集三类内容(研报为主,后两类 --dynamics/--views 开启):
    1. **研报(默认)**:report_search 按撰写日期区间拉列表 → report_get_url
       取带 token 的 PDF 直链下载 → 走与国君完全相同的管线(PDF 文本提取 +
       版面提取 + 位图视觉重述 + LLM 两步 + RAG 索引)。report_id 前缀文件名
       幂等;PDF 下载失败/过短降级用 summary 摘要落 .md(与国君同法)。
    2. 动态快评(--dynamics):viewpoint_search_dynamics,纯文本无 PDF →
       .md 入库,品种由 LLM 识别。
    3. 周期/年度观点(--views):viewpoint_search_views,product_code 前缀
       (M.DCE → M)命中 VARIETY_METADATA(61 品种)才入库,品种直接定。

  由两条路径触发:
    1. scheduler.py 每日定时子进程(research_times +40 分钟错峰,job 前缀
       research_dz_,告警前缀 dz_*)。
    2. web_app /api/research/collect 手动触发(source="dongzheng" 或 "all")。

  用法:
    python research_collector_dongzheng.py                     # 近 1 天研报
    python research_collector_dongzheng.py --days 3            # 回看 3 天
    python research_collector_dongzheng.py --dynamics --views  # 附带快评+观点
    python research_collector_dongzheng.py --dry-run           # 只打印候选,不写库

【与国君/华泰的差异】
  - MCP 端点匿名可调用,无 API key;配了 FIONA_MCP_TOKEN 会自动带上
    (rating_prediction/news 等 token-only 端点将来要用);
  - 研报按撰写日期(write_date)增量,seen 键 r{report_id};动态快评入库前
    无法预判品种 → 不支持 varieties 预过滤(与发现报告同);
  - 匿名口可能随时收紧为强制鉴权,token 逻辑保留。
"""

from __future__ import annotations

import argparse
import contextlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import requests  # 【调用包】研报 PDF 直链下载(带重试,比 urllib 稳)

SOURCE_ORG = "东证期货-繁微"  # 【变量】机构目录名(原件存放子目录)
SOURCE_LABEL = "东证期货-繁微MCP"  # 【变量】入库 source 标签
MIN_BODY_CHARS = 200  # 【变量】正文最小可见字符数(研报与国君同规;快评在动态路径内自判)
REQUEST_TIMEOUT = 120  # 【变量】PDF 下载超时(秒)
MAX_SEEN = 5000  # 【变量】状态文件 seen 列表上限(滚动丢弃最旧)
MAX_REPORTS_DEFAULT = 30  # 【变量】单次运行默认入库上限(LLM 用量保险丝)
PARALLEL_WORKERS = 2  # 【变量】并行线程数(刻意低于国君的 4:本采集器在 GTJA +40 分钟
#                      错峰启动,GTJA 4 线程可能仍在跑,2+4 ≤ 账户并发上限 5 不触发 429)
_STATE_LOCK = threading.Lock()  # 【变量】状态文件落盘互斥锁

_STATE_DIR = Path.home() / ".tradingagents"  # 【变量】状态目录根
STATE_FILE = _STATE_DIR / "dongzheng_collector_state.json"  # 【变量】{seen: [...], last_run}

FREQ_LABEL = {"weekly": "周度", "monthly": "月度", "yearly": "年度"}  # 【变量】freq → 中文标签
# 【变量】报告类型映射(type_name → 项目内 report_type 口径;其余留空由 LLM 自愈)
TYPE_MAP = {"日度报告": "日报", "综合晨报": "日报", "晨报": "日报", "周度报告": "周报"}
# 【变量】常见别名补映射(繁微 futures 枚举的 sname 用法,元数据 name/name_en 没覆盖的)
_ALIAS_NAMES = {"LLDPE": "L", "PVC": "V", "聚氯乙烯": "V", "沪胶": "RU", "20号胶": "NR"}


# ── 状态读写 ────────────────────────────────────────────────────────────

def _load_state() -> dict:
    """读增量状态文件;无文件/损坏返回空 dict。"""
    if STATE_FILE.exists():
        try:
            import json

            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 损坏即视为首次运行
            pass
    return {}


def _save_state(state: dict):
    """写增量状态文件(目录不存在则创建)。"""
    import json

    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _sanitize_filename(name: str) -> str:
    """文件名安全化:非法字符替换为下划线,截断到 60 字符(与 gtja 同规)。"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return safe[:60] or "report"


def _variety_name_map() -> dict[str, str]:
    """中文/英文名 → 品种代码映射(来自 VARIETY_METADATA 全部品种 + 常用别名)。"""
    from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

    m: dict[str, str] = {}
    for code, meta in VARIETY_METADATA.items():
        for key in ("name", "name_en"):
            v = str(meta.get(key) or "").strip()
            if v:
                m.setdefault(v, code)
    m.update(_ALIAS_NAMES)
    return m


def report_variety_codes(row: dict) -> list[str]:
    """研报行的品种代码:product_names(如 ["铁矿石"])逐一映射到品种代码。

    【品种池】2026-09-09 起只保留 ACTIVE_VARIETIES(20 品种)内的代码;池外品种
    研报由 web_app._process_research_report 的池过滤兜底跳过(不烧 LLM)。
    """
    from tradingagents.dataflows.commodity_futures import (  # 【调用包】元数据 + 活跃池
        ACTIVE_VARIETIES,
    )

    name_map = _variety_name_map()
    codes: list[str] = []
    for name in row.get("product_names") or []:
        code = name_map.get(str(name).strip())
        if code and code in ACTIVE_VARIETIES and code not in codes:
            codes.append(code)
    return codes


def view_variety_code(row: dict) -> str:
    """观点行的品种代码:product_code 前缀(如 M.DCE → M)须命中品种元数据。

    【返回】代码(如 "M");不在 ACTIVE_VARIETIES(20 品种池,宏观/股指/外盘及
            池外商品不在内)返回空串(调用方跳过该条)。
    """
    from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES

    raw = str(row.get("product_code") or "")
    code = raw.split(".", 1)[0].strip().upper()
    return code if code in ACTIVE_VARIETIES else ""


def _authors_text(row: dict) -> str:
    """作者列表 → "张三(化工)、李四" 式单行;研报行 author 是纯字符串,
    动态快评 authors / 观点 view_participants 是 dict 列表。"""
    a = row.get("author") or row.get("authors") or row.get("view_participants")
    if isinstance(a, str) and a.strip():
        return a.strip()
    parts = []
    for x in a or []:
        if isinstance(x, dict) and x.get("user_name"):
            t = f"({x['title']})" if x.get("title") else ""
            parts.append(f"{x['user_name']}{t}")
    return "、".join(parts)


def _download_pdf(url: str, dest: Path) -> bool:
    """下载研报 PDF 直链(pdf_url 自带时效 token);失败删除半成品返回 False。"""
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200 and r.content[:5] == b"%PDF-":
            dest.write_bytes(r.content)
            return True
    except requests.RequestException:
        pass
    with contextlib.suppress(OSError):  # 【调用】半成品清理失败可忽略
        dest.unlink()
    return False


# ── 入库:研报(主源) ─────────────────────────────────────────────────────

def _ingest_report(row: dict) -> bool:
    """一份研报:PDF 下载 → 文本预检 → 入库 → LLM 处理(与国君 _ingest_one 同法)。

    【返回】True=入库并处理;False=跳过(幂等命中/下载失败且摘要过短)。
    【关键逻辑】1) report_id 前缀文件名 = 幂等键;
              2) PDF 下载失败或文本过短时降级用接口 summary 落 .md(与国君同);
              3) 品种取 product_names 映射的首个命中代码作主品种提示,
                 多品种识别仍由 LLM 第一步完成。
    """
    report_id = row.get("report_id")
    title = (row.get("title") or "").strip()
    if not report_id or not title:
        return False

    import tradingagents.dataflows.dongzheng_api as dz_api  # 【调用包】繁微 MCP 客户端
    from database import get_db
    from web_app import RESEARCH_UPLOAD_DIR, _extract_report_text, _process_research_report

    fname = f"dzrpt{report_id}_{_sanitize_filename(title)}.pdf"  # 幂等键
    file_path = RESEARCH_UPLOAD_DIR / SOURCE_ORG / fname
    db = get_db()
    existing = db.get_research_report_by_filename(fname)
    if existing and existing.get("status") != "processing":
        return False  # 幂等命中(与 seen 口径一致,静默)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_ok = False
    if not file_path.exists():
        try:
            url_info = dz_api.fetch_report_url(int(report_id))
            pdf_url = (url_info or {}).get("pdf_url") or ""
            # 【附件未必是 PDF】实测"数据周报(英文)"等类型的附件是 .xlsx;非 .pdf
            # 直链不下载,直接走摘要降级,省一次无效大文件传输
            if pdf_url.lower().split("?")[0].endswith(".pdf"):
                pdf_ok = _download_pdf(pdf_url, file_path)
        except Exception as e:  # noqa: BLE001 - URL 获取失败走摘要降级
            print(f"    ! {report_id} {title[:40]} PDF 地址获取失败: {str(e)[:80]}")
    else:
        pdf_ok = True
    if pdf_ok:
        text, _ocr = _extract_report_text(str(file_path))  # 【预检】空壳不入库
    else:
        text = ""
    if len(text.strip()) < MIN_BODY_CHARS:
        summary = (row.get("summary") or "").strip()
        if len(summary) < MIN_BODY_CHARS:
            print(f"    ! {report_id} {title[:40]} 正文过短(PDF {len(text.strip())} 字/"
                  f"摘要 {len(summary)} 字),跳过")
            return False
        fname = fname[:-4] + ".md"  # 降级 .md(幂等键同步切换)
        md_path = file_path.with_suffix(".md")
        md_path.write_text(
            f"# {title}\n"
            f"- 来源: {SOURCE_LABEL}\n"
            f"- 日期: {(row.get('write_date') or '')[:10]}\n\n"
            f"{summary}\n",
            encoding="utf-8",
        )
        file_path = md_path

    codes = report_variety_codes(row)
    type_name = (row.get("type_name") or "").strip()
    if existing:  # 【自愈】processing 残留:复用该行重跑处理
        report_id_db = existing["id"]
    else:
        report_id_db = db.insert_research_report(
            variety=codes[0] if codes else "",
            title=title,
            source=SOURCE_LABEL,
            filename=file_path.name,
            file_path=str(file_path),
            ingest_source="auto",  # 自动采集入库(数据仓库"研报库"徽标=自动)
            publish_date=(row.get("write_date") or "")[:10],  # 接口自带撰写日期
            report_type=TYPE_MAP.get(type_name, ""),  # 未知类型留空由 LLM 自愈
        )
    _process_research_report(report_id_db)  # 【调用函数】提取/版面/视觉重述/LLM/RAG
    print(f"    + dzrpt{report_id} {title[:50]} -> report_id={report_id_db}")
    return True


# ── 入库:动态快评(--dynamics) ──────────────────────────────────────────

def _ingest_dynamic(row: dict) -> bool:
    """一条动态快评 → .md 入库(纯文本无 PDF,品种留空由 LLM 识别)。"""
    source_id = row.get("source_id")
    title = (row.get("title") or "").strip()
    text = (row.get("text") or "").strip()
    if not source_id or not title or len(text) < MIN_BODY_CHARS:
        return False
    from database import get_db
    from web_app import RESEARCH_UPLOAD_DIR

    fname = f"dzdyn{source_id}_{_sanitize_filename(title)}.md"
    md_path = RESEARCH_UPLOAD_DIR / SOURCE_ORG / fname
    db = get_db()
    existing = db.get_research_report_by_filename(fname)
    if existing and existing.get("status") != "processing":
        return False
    head = [
        f"# {title}",
        f"- 来源: {SOURCE_LABEL}",
        f"- 日期: {(row.get('publish_time') or '')[:10] or f'{datetime.now():%Y-%m-%d}'}",
    ]
    if (row.get("catalogue") or "").strip():
        head.append(f"- 板块: {row['catalogue'].strip()}")
    authors = _authors_text(row)
    if authors:
        head.append(f"- 作者: {authors}")
    body = "\n".join(head) + "\n\n" + text + "\n"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(body, encoding="utf-8")
    if existing:  # 【自愈】processing 残留复用
        rid = existing["id"]
    else:
        rid = db.insert_research_report(
            variety="",
            title=title,
            source=SOURCE_LABEL,
            filename=md_path.name,
            file_path=str(md_path),
            ingest_source="auto",
            publish_date=(row.get("publish_time") or "")[:10],
        )
    from web_app import _process_research_report
    _process_research_report(rid)
    print(f"    + dzdyn{source_id} {title[:50]} -> report_id={rid}")
    return True


# ── 入库:周期/年度观点(--views) ────────────────────────────────────────

def _ingest_view(row: dict, freq: str) -> bool:
    """一条周期/年度观点 → .md 入库(product_code 前缀直接定品种)。"""
    view_id = row.get("view_id")
    product = (row.get("product_name") or "").strip()
    code = view_variety_code(row)
    if not view_id or not product or not code:
        return False  # 非商品期货品种(宏观/股指/外盘)不在品种元数据内,跳过
    from database import get_db
    from web_app import RESEARCH_UPLOAD_DIR, _process_research_report

    freq_label = FREQ_LABEL.get(freq, freq)
    pred = f"{row.get('prediction_start') or ''}~{row.get('prediction_end') or ''}"
    title = f"{product}：东证{freq_label}观点({pred})"
    fname = f"dzview{view_id}_{freq}.md"
    md_path = RESEARCH_UPLOAD_DIR / SOURCE_ORG / fname
    db = get_db()
    existing = db.get_research_report_by_filename(fname)
    if existing and existing.get("status") != "processing":
        return False
    authors = _authors_text(row)
    sections = [
        ("观点方向", (row.get("view_grade") or "").strip()),
        ("当前现状", (row.get("view_current_info") or "").strip()),
        ("未来展望", (row.get("view_forecast_info") or "").strip()),
        ("风险因素", (row.get("view_risk_info") or "").strip()),
    ]
    body_lines = [
        f"# {title}",
        f"- 来源: {SOURCE_LABEL}",
        f"- 日期: {datetime.now():%Y-%m-%d}",  # 接口不给观点发布时间,用采集日
    ]
    if authors:
        body_lines.append(f"- 研究员: {authors}")
    body_lines.append("")
    for name, content in sections:
        if content:
            body_lines += [f"## {name}", content, ""]
    body = "\n".join(body_lines).rstrip() + "\n"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(body, encoding="utf-8")
    if existing:
        rid = existing["id"]
    else:
        rid = db.insert_research_report(
            variety=code,
            title=title,
            source=SOURCE_LABEL,
            filename=md_path.name,
            file_path=str(md_path),
            ingest_source="auto",
            publish_date=datetime.now().strftime("%Y-%m-%d"),
        )
    _process_research_report(rid)
    print(f"    + dzview{view_id} {title[:50]} -> report_id={rid}")
    return True


# ── 编排 ────────────────────────────────────────────────────────────────

def ingest_recent(target_date: str | None = None, days: int = 1, dry_run: bool = False,
                  max_reports: int = MAX_REPORTS_DEFAULT, include_dynamics: bool = False,
                  include_views: bool = False,
                  view_freqs: tuple[str, ...] = ("weekly", "yearly")) -> dict:
    """接入 [target_date-days, target_date] 的东证研报(可选快评/观点)。

    【参数】target_date: 截止日期 YYYY-MM-DD(默认今天);days: 回看天数(默认 1);
            max_reports: 单次入库上限(三类合计);include_dynamics/include_views:
            附带动态快评/周期观点(观点为全量,首跑一次即可,后续靠 seen 幂等)。
    【返回】{"collected", "processed", "skipped", "errors", "total"}。
    【关键逻辑】1) report_search 拉区间研报 → seen 去重 → 逐篇下载入库;
              2) 单篇失败不中断;3) 无论成败 seen 逐条落盘(崩溃续跑);
              4) seen 键口径:r{report_id} / d{source_id} / v{view_id}_{freq},
                过滤与落盘必须同键(否则重复采集)。
    """
    import tradingagents.dataflows.dongzheng_api as dz_api  # 【调用包】繁微 MCP 客户端

    end_dt = datetime.strptime(target_date, "%Y-%m-%d") if target_date else datetime.now()
    start_dt = end_dt - timedelta(days=max(0, days))
    start, end = start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
    try:
        rows = dz_api.fetch_reports(start, end, limit=200)
    except Exception as e:  # noqa: BLE001 - 接口失败不崩溃,返回空统计
        print(f"[{SOURCE_ORG}] 研报列表拉取失败: {e}")
        rows = []
    print(f"[{SOURCE_ORG}] {start}~{end} 研报 {len(rows)} 篇")

    state = _load_state()
    seen = set(state.get("seen") or [])
    fresh: list[tuple[str, dict]] = []  # (seen键, 行/打包行)
    for r in rows:
        key = f"r{r.get('report_id')}"
        if key not in seen:
            fresh.append((key, {"__type__": "report", **r}))
    if include_dynamics:
        try:
            drows = dz_api.fetch_dynamics(start, end, limit=200)
        except Exception as e:  # noqa: BLE001
            print(f"[{SOURCE_ORG}] 动态快评拉取失败: {e}")
            drows = []
        nd = 0
        for r in drows:
            key = f"d{r.get('source_id')}"
            if key not in seen:
                fresh.append((key, {"__type__": "dynamic", **r}))
                nd += 1
        print(f"[{SOURCE_ORG}] 动态快评 {len(drows)} 条(新增 {nd} 条)")
    if include_views:
        for freq in view_freqs:
            try:
                vrows = dz_api.fetch_views(freq=freq, limit=1000)
            except Exception as e:  # noqa: BLE001
                print(f"[{SOURCE_ORG}] {freq} 观点拉取失败: {e}")
                vrows = []
            nv = 0
            for r in vrows:
                key = f"v{r.get('view_id')}_{freq}"
                if key not in seen and view_variety_code(r):
                    fresh.append((key, {"__type__": "view", "__freq__": freq, **r}))
                    nv += 1
            print(f"[{SOURCE_ORG}] {freq} 观点新增 {nv} 条")
    print(f"[{SOURCE_ORG}] 去 seen 后新增 {len(fresh)} 条")
    if len(fresh) > max_reports:
        print(f"    单次上限 {max_reports},本次只处理最新 {max_reports} 条(其余留待下次)")
        fresh = fresh[:max_reports]

    collected = len(fresh)
    processed = skipped = 0
    errors: list[str] = []
    new_seen = set(seen)

    def _persist_seen(key: str):
        with _STATE_LOCK:
            new_seen.add(key)
            state["seen"] = sorted(new_seen)[-MAX_SEEN:]
            state["last_run"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            _save_state(state)

    def _handle(item):
        """单条完整处理;入参 (seen键, 行),返回 (seen键, ok, err)。"""
        key, row = item
        try:
            kind = row.get("__type__")
            if kind == "report":
                ok = _ingest_report(row)
            elif kind == "dynamic":
                ok = _ingest_dynamic(row)
            else:
                ok = _ingest_view(row, row.get("__freq__") or "weekly")
            return key, ok, None
        except Exception as e:  # noqa: BLE001 - 单条失败:记录并继续
            return key, False, str(e)

    if dry_run:
        for _, row in fresh:
            kind = row.get("__type__")
            if kind == "report":
                print(f"  [DRY] rpt {row.get('report_id')} {(row.get('write_date') or '')[:10]} {(row.get('title') or '')[:60]}")
            elif kind == "dynamic":
                print(f"  [DRY] dyn {row.get('source_id')} {(row.get('publish_time') or '')[:10]} {(row.get('title') or '')[:60]}")
            else:
                print(f"  [DRY] view {row.get('view_id')} {row.get('product_name')} ({row.get('__freq__')})")
    else:
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
            for key, ok, err in pool.map(_handle, fresh):
                if err:
                    errors.append(f"{key}: {err}")
                    print(f"    ! {key} 处理异常: {err}")
                elif ok:
                    processed += 1
                else:
                    skipped += 1
                _persist_seen(key)

    print(f"Collected: {collected}")
    print(f"Processed: {processed}")
    return {"collected": collected, "processed": processed, "skipped": skipped,
            "errors": errors, "total": len(rows)}


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> int:
    """CLI 入口:--date/--days/--max-reports/--dynamics/--views/--dry-run/--reset-state。"""
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="东证期货繁微(Fiona)研报/观点自动接入(report+viewpoint MCP)")
    ap.add_argument("--date", default=None, help="截止日期 YYYY-MM-DD(默认今天)")
    ap.add_argument("--days", type=int, default=1, help="回看天数(默认 1)")
    ap.add_argument("--max-reports", type=int, default=MAX_REPORTS_DEFAULT, help="单次入库上限")
    ap.add_argument("--dynamics", action="store_true", help="附带动态快评(纯文本)")
    ap.add_argument("--views", action="store_true", help="附带周期/年度观点全量(首跑用)")
    ap.add_argument("--dry-run", action="store_true", help="只打印候选,不写库不下载")
    ap.add_argument("--reset-state", action="store_true", help="清 seen 状态(重跑)")
    args = ap.parse_args()

    if args.reset_state and STATE_FILE.exists():
        STATE_FILE.unlink()
        print("seen 状态已清空")

    t0 = time.time()
    try:
        ingest_recent(target_date=args.date, days=args.days, dry_run=args.dry_run,
                      max_reports=args.max_reports, include_dynamics=args.dynamics,
                      include_views=args.views)
    except Exception as e:  # noqa: BLE001 - 顶层兜底
        print(f"接入异常: {e}")
        return 1
    print(f"Took: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 【调用】Windows 控制台 UTF-8
    raise SystemExit(main())
