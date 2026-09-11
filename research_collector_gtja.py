"""research_collector_gtja.py — 国泰君安期货官方研报 API 自动接入(researchReportAttachmentQuery)

【模块角色】
  从国君 vip.gtjaqh.com 云 API(2026-09-04 开通)按发布日期区间拉研报列表,
  下载 PDF 附件直链(免鉴权 CDN),写入本机研报库并复用 web_app 的
  _process_research_report LLM 链路(内部 _extract_report_text 支持 PDF 文本层
  + OCR 降级),与发现报告(research_collector.py)/华泰天玑
  (research_collector_htfc.py)同架构。接口契约见 tradingagents/dataflows/
  gtja_api.py 的 fetch_research_reports()。

  由两条路径触发:
    1. scheduler.py 每日定时子进程(research_times,与 fxbaogao/HTFC 同时刻)。
    2. web_app /api/research/collect 手动触发(source="gtja" 或 "all")。

  用法:
    python research_collector_gtja.py                      # 近 2 天,默认目标品种
    python research_collector_gtja.py --date 2026-09-04    # 指定截止日
    python research_collector_gtja.py --days 7             # 回看 7 天
    python research_collector_gtja.py --varieties CU AL    # 指定品种代码
    python research_collector_gtja.py --all                # 不过滤品种(仍剔合集/英文晨报)
    python research_collector_gtja.py --dry-run            # 只打印候选,不写库不调 LLM

【过滤策略】(接口无服务端过滤参数,page/size 被忽略,一次返回区间全量)
  1) 排除合集类(tag 含"合集"/"周报合集"等,或标题含"合集")——内容与单品种报告重复;
  2) 排除全品种前瞻(标题含"期货行情前瞻",100+ tag 的大合集)与英文晨报
     (标题 Morning Insight 前缀)——无单品种增量价值;
  3) 默认只保留 infoTags 精确命中目标品种映射(TAG_TO_CODE)的研报(与 HTFC 同
     一组 21 目标品种),--all 放开;
  4) --max-reports 每次运行上限(默认 30),控制 LLM 用量(每份 ~3.5 分钟)。
  单品种周报照常采集(2026-09-07 起,原先被排除),按 is_weekly 打「周报」类型标。

【增量去重】
  状态文件 ~/.tradingagents/gtja_collector_state.json 记 seen infoId(逐篇落盘,
  崩溃续跑);入库幂等另以"研报库是否已有该文件名"为准(get_research_report_by_
  filename),processing 残留自愈——与 HTFC 同一套双层防重。
"""

from __future__ import annotations

import argparse
import re
import threading
import time
from concurrent.futures import (
    ThreadPoolExecutor,  # 【调用包】线程池(多篇并行处理,LLM 同步调用是大头)
)
from datetime import datetime, timedelta
from pathlib import Path

import requests  # 【调用包】PDF 附件直链下载

SOURCE_ORG = "国泰君安期货-API"  # 【变量】机构目录名(与 fxbaogao 的"国泰君安期货"区分,避免混源)
SOURCE_LABEL = "国泰君安期货-云API"  # 【变量】入库 source 标签
MIN_BODY_CHARS = 200  # 【变量】正文最小可见字符数(PDF 提取/摘要兜底,低于则跳过)
REQUEST_TIMEOUT = 30  # 【变量】列表/下载请求超时(秒)
SLEEP_BETWEEN = 0.5  # 【变量】逐篇处理间隔(秒,礼貌限速)
MAX_SEEN = 2000  # 【变量】状态文件 seen 列表上限(滚动丢弃最旧)
MAX_REPORTS_DEFAULT = 30  # 【变量】单次运行默认入库上限(LLM 用量保险丝)
PARALLEL_WORKERS = 4  # 【变量】并行处理线程数(LLM 账户并发上限 5,每篇同时只挂 1 个调用,4 并发安全)
_STATE_LOCK = threading.Lock()  # 【变量】状态文件落盘互斥锁(并行线程逐篇 persist)

# 【变量】目标品种代码:2026-09-09 起统一引用 ACTIVE_VARIETIES(20 品种池,与
# HTFC/全项目同一口径),不再各自手抄清单;国君不发池内部分品种(如 M/CF)时自然无数据。
from tradingagents.dataflows.commodity_futures import (  # noqa: E402  # 【调用包】活跃品种池(延迟顶层导入,采集器子进程 cwd=仓库根)
    ACTIVE_VARIETIES,
)

TARGET_VARIETIES = tuple(sorted(ACTIVE_VARIETIES))

# 【变量】国君 infoTags.tagName(精确 token)→ 品种代码。tags 是枚举 token
# (如"低硫燃料油"与"燃料油"是两个独立 tag),精确比对无子串误命中。
TAG_TO_CODE = {
    "PTA": "TA", "对苯二甲酸": "TA",
    "甲醇": "MA",
    "玻璃": "FG",
    "沥青": "BU", "石油沥青": "BU",
    "纯碱": "SA", "重碱": "SA",
    "苯乙烯": "EB",
    "纯苯": "BZ", "加氢苯": "BZ",
    "丁二烯橡胶": "BR", "顺丁橡胶": "BR",
    "PVC": "V", "聚氯乙烯": "V",
    "PP": "PP", "聚丙烯": "PP",
    "LLDPE": "L", "塑料": "L", "聚乙烯": "L",
    "MEG": "EG", "乙二醇": "EG",
    "对二甲苯": "PX",
    "尿素": "UR",
    "短纤": "PF", "瓶片": "PF",
    "原油": "SC",
    "低硫燃料油": "LU",
    "燃料油": "FU", "高硫燃料油": "FU",
    "橡胶": "RU", "天然橡胶": "RU", "沪胶": "RU",
    "20号胶": "NR",
    "烧碱": "SH",
    "碳酸锂": "LC",
    "多晶硅": "PS", "硅料": "PS",
    "工业硅": "SI",
    "豆粕": "M", "棉花": "CF",
    "红枣": "CJ",
    "生猪": "LH",
    "LPG": "PG", "液化石油气": "PG",
}

# 【变量】排除类 tag(合集/周报——内容与单品种日报重复或超出日度增量)
_EXCLUDE_TAGS = {"合集", "日报合集", "晨报合集_pdf", "周报合集", "专题合集"}
# 【变量】标题排除子串(全品种前瞻大合集;英文晨报单独按前缀判)
_EXCLUDE_TITLE_SUBSTR = ("合集", "期货行情前瞻")
_ENGLISH_PREFIX = "Morning Insight"

_STATE_DIR = Path.home() / ".tradingagents"  # 【变量】状态/数据目录根
STATE_FILE = _STATE_DIR / "gtja_collector_state.json"  # 【变量】状态文件:{seen: [...], last_run}


# ── 状态读写 ────────────────────────────────────────────────────────────

def _load_state() -> dict:
    """读增量状态文件;无文件/损坏返回空 dict。"""
    if STATE_FILE.exists():
        try:
            import json  # 【调用包】状态文件 JSON 编解码

            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 损坏即视为首次运行
            pass
    return {}


def _save_state(state: dict):
    """写增量状态文件(目录不存在则创建)。"""
    import json  # 【调用包】状态文件 JSON 编解码

    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 过滤(纯函数,便于单测) ─────────────────────────────────────────────

def _row_tags(row: dict) -> list[str]:
    """取一行研报的 tagName 列表(缺失/异常容错)。"""
    return [str(t.get("tagName") or "") for t in (row.get("infoTags") or []) if isinstance(t, dict)]


def is_weekly(row: dict) -> bool:
    """周报判定(tag 精确「周报」或标题以「周报」结尾),采集时打 report_type 用。"""
    return "周报" in _row_tags(row) or (row.get("title") or "").strip().endswith("周报")


def is_collection(row: dict) -> bool:
    """合集/前瞻/英文晨报判定(内容与单品种报告重复,无增量价值)。

    【注意】周报不再是排除类(2026-09-07 起):单品种周报照常采集,入库时由
            is_weekly 打「周报」类型标;仅合集类(含周报合集)仍排除。
    """
    title = (row.get("title") or "").strip()
    tags = set(_row_tags(row))
    if tags & _EXCLUDE_TAGS:
        return True
    if any(s in title for s in _EXCLUDE_TITLE_SUBSTR):
        return True
    return title.startswith(_ENGLISH_PREFIX)


def match_codes(row: dict, requested: set[str] | None = None) -> set[str]:
    """infoTags 精确命中目标品种映射,返回品种代码集合。

    【参数】requested: 限定品种代码子集(None=全部 21 目标品种)。
    【关键逻辑】只看 tag 精确匹配(国君 tag 是枚举 token,比标题子串匹配可靠,
              天然规避"合成橡胶含橡胶"式子串误命中)。
    """
    wanted = requested if requested is not None else set(TARGET_VARIETIES)
    hits = {TAG_TO_CODE[t] for t in _row_tags(row) if t in TAG_TO_CODE}
    return hits & wanted


def _sanitize_filename(name: str) -> str:
    """文件名安全化:非法字符替换为下划线,截断到 60 字符。"""
    import re  # 【调用包】非法字符替换

    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return safe[:60] or "report"


def _html_to_text(html: str) -> str:
    """HTML → 可见文本(剥标签,换行替换 <br>/<li>,折叠空行)。"""
    import re  # 【调用包】标签剥离

    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)</?(p|div|li|tr|br|h\d)[^>]*>", "\n", html)
    text = re.sub(r"<[^>]+>", "\n", html)
    return "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())


def _pdf_attachment(row: dict) -> dict | None:
    """取首个 pdf 类型附件 {name,address};无则 None。"""
    for att in row.get("attachments") or []:
        if isinstance(att, dict) and (att.get("type") or "").lower() == "pdf" and att.get("address"):
            return att
    return None


SUPERSEDE_TITLE_DAYS = 3  # 【变量】同名日报跨日去重窗口(天)


def _norm_title(title: str) -> str:
    """标题归一(去全部空白),跨日同名日报判定用。"""
    return re.sub(r"\s+", "", title or "")


def _supersede_same_title(db, title: str) -> None:
    """删除近 SUPERSEDE_TITLE_DAYS 天内同归一化标题的旧研报(删旧迎新)。

    【为什么】国君日报标题不带日期且观点不变则连日同名(如《尿素：区间运行》),
              采集窗口跨 2 天会把昨日版+今日版都拉回来 —— infoId 不同导致
              seen/文件名两层防重都不命中,两版同时入库。日报只有最新版有价值
              → 删旧行 + 原件 + 聚合 JSON 记录(复用 web_app._delete_research_report_full)。
    """
    from web_app import (
        _delete_research_report_full,  # 【调用包】全链路删除(懒导入复用 web_app 链路)
    )

    norm = _norm_title(title)
    since = (datetime.now() - timedelta(days=SUPERSEDE_TITLE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        old_rows = db.list_research_reports_since(since)
    except Exception as exc:
        print(f"    ! 同名去重查询失败({exc}),保留旧版")
        return
    for r in old_rows:
        if _norm_title(r.get("title") or "") != norm:
            continue
        if _delete_research_report_full(r["id"]):
            print(f"    ~ 同名日报旧版 #{r['id']}《{(r.get('title') or '')[:40]}》已删旧迎新")


def _download_pdf(url: str, dest: Path) -> bool:
    """下载 PDF 直链到本地(免鉴权 CDN);失败删除半成品返回 False。"""
    try:
        r = requests.get(url, timeout=120)
        if r.status_code == 200 and r.content[:5] == b"%PDF-":
            dest.write_bytes(r.content)
            return True
    except requests.RequestException:
        pass
    return False


# ── 入库 ────────────────────────────────────────────────────────────────

def _ingest_one(row: dict, dry_run: bool = False) -> bool:
    """把一份研报(下载 PDF)写入研报库并触发 LLM 处理。

    【返回】True=入库并处理;False=跳过(正文过短/幂等命中/下载失败)。
    【关键逻辑】1) PDF 附件下载到 RESEARCH_UPLOAD_DIR/{SOURCE_ORG}/{infoId}_{标题}.pdf;
              2) _extract_report_text 试提文本(与 _process_research_report 同一
                 提取器,PDF 文本层 + OCR 降级)——过短且无摘要兜底则跳过;
              3) insert_research_report 后交 _process_research_report(内部会再
                 次提取+LLM,这里只是预检,避免空壳文件入库);
              4) 幂等以"研报库是否已有该文件名"为准,processing 残留自愈;
              5) 新入库前按归一化标题做近 3 天跨日同名去重(删旧迎新)。
    """
    info_id = str(row.get("infoId") or "").strip()
    title = (row.get("title") or "").strip()
    if not info_id or not title:
        return False

    from database import get_db  # 【调用包】数据库实例(懒导入,复用 web_app 链路)
    from web_app import (  # 【调用包】存储目录 + 文本提取 + 后台 LLM 处理
        RESEARCH_UPLOAD_DIR,
        _extract_report_text,
        _process_research_report,
    )

    fname = f"{info_id}_{_sanitize_filename(title)}.pdf"  # 【变量】文件名(infoId 前缀=幂等键)
    file_path = RESEARCH_UPLOAD_DIR / SOURCE_ORG / fname
    file_path.parent.mkdir(parents=True, exist_ok=True)

    db = get_db()
    existing = db.get_research_report_by_filename(fname)
    if existing and existing.get("status") != "processing":
        print(f"    = {info_id} {title[:50]} 已在研报库(status={existing['status']}),跳过(幂等)")
        return False

    att = _pdf_attachment(row)
    if att and not file_path.exists() and not _download_pdf(att["address"], file_path):
        print(f"    ! {info_id} {title[:50]} PDF 下载失败,跳过")
        return False

    if att:
        text, _ocr = _extract_report_text(str(file_path))  # 【预检】同一提取器,空壳不入库
    else:
        text = ""
    if len(text.strip()) < MIN_BODY_CHARS:
        # 降级:用接口 summary 摘要兜底写 .md(PDF 是扫描件或缺失时仍有可用正文)
        summary_text = _html_to_text(row.get("summary") or "")
        if len(summary_text) < MIN_BODY_CHARS:
            print(f"    ! {info_id} {title[:50]} 正文过短(PDF {len(text.strip())} 字/摘要 "
                  f"{len(summary_text)} 字,<{MIN_BODY_CHARS}),跳过")
            return False
        fname = fname[:-4] + ".md"  # 【变量】换 .md 文件名(幂等键同步切换)
        md_path = file_path.with_suffix(".md")
        md_path.write_text(
            f"# {title}\n"
            f"- 来源: {SOURCE_LABEL}\n"
            f"- 日期: {(row.get('publishTime') or '')[:10]}\n\n"
            f"{summary_text}\n",
            encoding="utf-8",
        )
        file_path = md_path

    if existing:  # 【自愈】processing 残留:复用该行重跑处理
        report_id = existing["id"]
        print(f"    ~ {info_id} {title[:50]} 上次卡在 processing,复用该行重跑处理")
    else:
        _supersede_same_title(db, title)  # 【跨日同名去重】删旧迎新,防同名日报两版并存
        report_id = db.insert_research_report(
            variety="",
            title=title,
            source=SOURCE_LABEL,
            filename=file_path.name,
            file_path=str(file_path),
            ingest_source="auto",  # 【来源】国君云 API 自动采集入库(数据仓库"研报库"徽标=自动)
            publish_date=(row.get("publishTime") or "")[:10],  # 【发布日期】接口自带真实发布日(回看窗口会跨日,入库≠发布)
            # 【类型】tag 精确「周报」→ 周报;否则 tag 有「日报」→ 日报;都没有留空(LLM 自愈)
            report_type="周报" if is_weekly(row) else ("日报" if "日报" in _row_tags(row) else ""),
        )
    _process_research_report(report_id)  # 【调用函数】LLM 提取 → 落库 → 写聚合 JSON
    print(f"    + {info_id} {title[:50]} -> report_id={report_id}")
    return True


def ingest_recent(target_date: str | None = None, days: int = 1, dry_run: bool = False,
                  requested: set[str] | None = None, include_all: bool = False,
                  max_reports: int = MAX_REPORTS_DEFAULT) -> dict:
    """接入 [target_date-days, target_date] 区间内的国君研报。

    【参数】target_date: 截止日期 YYYY-MM-DD(默认今天);days: 回看天数(默认 1,
            即覆盖隔夜发布的研报);requested: 限定品种代码;include_all: 不过滤
            品种(仍剔合集/英文晨报);max_reports: 单次入库上限。
    【返回】{"collected", "processed", "skipped", "errors", "total"}。
    【关键逻辑】1) fetch_research_reports 拉区间全量(服务端无过滤,客户端筛);
              2) 排合集/英文晨报 → 品种命中 → seen/上限;3) 逐篇下载入库 + LLM,
              单篇失败不中断;4) 无论成败都进 seen 且逐篇落盘(崩溃续跑)。
    """
    import tradingagents.dataflows.gtja_api as gtja_api  # 【调用包】国君云 API 客户端

    end = datetime.strptime(target_date, "%Y-%m-%d") if target_date else datetime.now()
    start = end - timedelta(days=max(0, days))
    rows = gtja_api.fetch_research_reports(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if not rows:
        print(f"[{SOURCE_ORG}] {start:%Y-%m-%d}~{end:%Y-%m-%d} 无研报(接口空/失败)")
        return {"collected": 0, "processed": 0, "skipped": 0, "errors": ["GTJA 列表为空/失败"], "total": 0}

    chosen: list[tuple[dict, set[str]]] = []
    for row in rows:
        if is_collection(row):
            continue
        codes = match_codes(row, requested=None if include_all else requested)
        if not include_all and not codes:
            continue
        chosen.append((row, codes))

    state = _load_state()
    seen = set(state.get("seen") or [])
    fresh: list[tuple[dict, set[str]]] = []
    run_titles: set[tuple[str, str]] = set()  # 【变量】同日同标题去重(国君同日重发会换 infoId)
    for row, codes in chosen:
        key = ((row.get("title") or "").strip(), (row.get("publishTime") or "")[:10])
        if str(row.get("infoId")) in seen or key in run_titles:
            continue
        run_titles.add(key)
        fresh.append((row, codes))
    print(f"[{SOURCE_ORG}] {start:%Y-%m-%d}~{end:%Y-%m-%d} 共 {len(rows)} 篇,筛选后 "
          f"{len(chosen)} 篇(品种命中={sorted({c for _, cs in chosen for c in cs})}),"
          f"去 seen 后新增 {len(fresh)} 篇")
    if len(fresh) > max_reports:
        print(f"    单次上限 {max_reports},本次只处理最新 {max_reports} 篇(其余留待下次)")
        fresh = fresh[:max_reports]

    collected = len(fresh)
    processed = skipped = 0
    errors: list[str] = []
    new_seen = set(seen)

    if dry_run:
        for row, codes in fresh:
            print(f"  [DRY] {row.get('infoId')} [{'.'.join(sorted(codes)) or '—'}] {row['title'][:60]}")
    else:
        # 【崩溃续跑】逐篇落盘状态(线程安全):进程中断/被杀也不丢已处理进度
        def _persist_seen(info_id):
            with _STATE_LOCK:
                new_seen.add(info_id)
                state["seen"] = sorted(new_seen)[-MAX_SEEN:]
                state["last_run"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                _save_state(state)

        def _handle(row):
            """单篇完整处理:下载 PDF → 入库 + LLM;返回 (info_id, ok, err)。"""
            info_id = str(row.get("infoId"))
            try:
                return info_id, _ingest_one(row), None
            except Exception as e:  # 【异常】单篇失败:记录并继续,不拖垮整批
                return info_id, False, str(e)

        # 【并行】每篇大头是 LLM 同步调用(~2-3 分钟),3 并发把 30 篇从 ~1.5h 压到 ~30min
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
            for info_id, ok, err in pool.map(_handle, [r for r, _ in fresh]):
                if err:
                    errors.append(f"{info_id}: {err}")
                    print(f"    ! {info_id} 处理异常: {err}")
                elif ok:
                    processed += 1
                else:
                    skipped += 1
                _persist_seen(info_id)

    print(f"Collected: {collected}")
    print(f"Processed: {processed}")
    return {"collected": collected, "processed": processed, "skipped": skipped,
            "errors": errors, "total": len(rows)}


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> int:
    """CLI 入口:--date/--days/--varieties/--all/--max-reports/--dry-run/--reset-state。"""
    try:
        from dotenv import load_dotenv  # 【调用包】加载 .env(密钥在环境变量)
        load_dotenv(override=False)
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="国泰君安期货官方研报 API 自动接入(researchReportAttachmentQuery)")
    ap.add_argument("--date", default=None, help="截止日期 YYYY-MM-DD(默认今天)")
    ap.add_argument("--days", type=int, default=1, help="回看天数(默认 1,覆盖隔夜发布)")
    ap.add_argument("--varieties", nargs="+", help="只接这些品种代码(缺省=21 目标品种)")
    ap.add_argument("--all", action="store_true", help="不过滤品种(仍剔合集/英文晨报)")
    ap.add_argument("--max-reports", type=int, default=MAX_REPORTS_DEFAULT, help="单次入库上限")
    ap.add_argument("--dry-run", action="store_true", help="只打印候选,不写库不调 LLM")
    ap.add_argument("--reset-state", action="store_true", help="清 seen 状态(重跑同日)")
    args = ap.parse_args()

    requested = None
    if args.varieties:
        requested = {v.upper() for v in args.varieties}
        unknown = requested - set(TARGET_VARIETIES)
        if unknown and not args.all:
            print(f"不在目标品种内: {sorted(unknown)} (目标: {' '.join(TARGET_VARIETIES)};或加 --all)")
            return 2

    t0 = time.time()
    try:
        ingest_recent(target_date=args.date, days=args.days, dry_run=args.dry_run,
                      requested=requested, include_all=args.all, max_reports=args.max_reports)
    except Exception as e:  # 【异常】顶层兜底:接口异常不崩溃,打印后以非零退出
        print(f"接入异常: {e}")
        return 1
    print(f"Took: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
