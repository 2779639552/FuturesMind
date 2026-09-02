"""research_collector.py — 每日开盘前自动接入期货公司研报(发现报告 / 5 家统一源)

【模块角色】
  从发现报告(fxbaogao.com)机构页批量抓取 5 家期货公司(永安/中信/国泰君安/
  东证/华泰)的最新研报,写入本机研报库并复用 web_app._process_research_report
  的 LLM 提取链路(结构化数据 + 观点结论 → research_reports 表 + 按品种聚合
  JSON),让基本面/宏观分析师每天开盘前就能读到最新机构观点。

  由两条路径触发:
    1. scheduler.py 每日定时子进程(08:10 / 18:00,开盘前)。
    2. web_app /api/research/collect 手动触发(daemon 线程)。

【采集方式】
  发现报告是 Next.js SSR:机构页 `/archives/organization/<机构名>?page=N` 的
  列表项是 `<a title="标题" href="/detail/{id}">`,requests 直爬即可;详情页
  正文在 HTML 里可读(免登录),PDF 下载才要 VIP(不影响文本提取)。

【增量去重】
  报告 id 随新报告递增(同标题如"铁合金早报"每天重复,不能按标题去重),所以
  用状态文件 `~/.tradingagents/research_collector_state.json` 记每家机构的
  已见最大 id:只接入 id 更大的新报告。首次运行(无状态)只接入第 1 页(可用
  --max-per-org 限流),并把水位推进到该页最大 id——此后每天只接增量。

  用法:
    python research_collector.py                          # 5 家全部接入
    python research_collector.py --org 永安期货           # 只接一家
    python research_collector.py --dry-run                # 只打印不写库
    python research_collector.py --max-per-org 2          # 每家最多接 2 份(首日限流)
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote  # 【调用包】机构名 URL 编码

import requests  # 【调用包】HTTP 请求(fxbaogao 列表/详情抓取)

# ── 常量 ────────────────────────────────────────────────────────────────

BASE_URL = "https://www.fxbaogao.com"  # 【变量】发现报告站点根
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"  # 【变量】浏览器 UA(规避简单反爬)
ORGS = ["永安期货", "中信期货", "国泰君安期货", "东证期货", "华泰期货"]  # 【变量】接入的 5 家期货公司机构名(对应 fxbaogao 机构页)
REQUEST_TIMEOUT = 20  # 【变量】单次请求超时(秒)
SLEEP_BETWEEN = 0.6  # 【变量】请求间间隔(秒,礼貌限速)
MAX_RETRIES = 1  # 【变量】单次请求失败重试次数

_STATE_DIR = Path.home() / ".tradingagents"  # 【变量】状态/数据目录(与 RESEARCH_UPLOAD_DIR 同根)
STATE_FILE = _STATE_DIR / "research_collector_state.json"  # 【变量】增量水位状态文件:{机构: {max_id, last_run}}


# ── 状态读写 ────────────────────────────────────────────────────────────

def _load_state() -> dict:
    """读增量状态文件;无文件/损坏返回空 dict(视为首次运行)。"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict):
    """写增量状态文件(目录不存在则创建)。"""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ── fxbaogao 抓取 ───────────────────────────────────────────────────────

def _get(url: str) -> str | None:
    """带 UA + 重试的 GET,返回响应文本;彻底失败返回 None。"""
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200 and r.text:
                return r.text
        except requests.RequestException:
            pass
        if attempt < MAX_RETRIES:
            time.sleep(SLEEP_BETWEEN * 2)
    return None


def fetch_org_listing(org: str, page: int = 1) -> list[dict]:
    """抓取机构页第 page 页的报告列表。

    【参数】org: 机构名(如"永安期货");page: 页码(1 起)。
    【返回】[{id, title, url}] 按页面顺序(最新在前);失败返回空列表。
    【关键逻辑】列表项是 `<a title="标题" href="/detail/{id}">` 的锚点,
              正则提取 title 与 id;页尾有"下一页"等导航需排除。
    """
    url = f"{BASE_URL}/archives/organization/{quote(org)}?page={page}"
    html = _get(url)
    if not html:
        return []
    items: list[dict] = []
    # 只匹配真实报告条目:title 非空、href 指向 /detail/{id}
    for m in re.finditer(r'<a\s+[^>]*title="([^"]+)"[^>]*href="/detail/(\d+)"', html):
        title = m.group(1).strip()
        rid = int(m.group(2))
        if title and not re.match(r"^(上一页|下一页|第?\d+页)$", title):
            items.append({"id": rid, "title": title, "url": f"{BASE_URL}/detail/{rid}"})
    return items


def fetch_detail(report_id: int) -> dict:
    """抓取详情页正文与发布日期。

    【参数】report_id: 报告 id。
    【返回】{"title", "date", "text"}。text 为去除 script/style/标签后的
            可见文本(含报告正文与少量导航噪音,交由 LLM 提取容错)。
    【关键逻辑】先剥 <script>/<style>,再以换行替换标签、折叠空行;正文从
              报告标题处开始,截到"免责声明/会员中心/Copyright"等页脚。
    """
    url = f"{BASE_URL}/detail/{report_id}"
    html = _get(url) or ""
    html = re.sub(r"(?is)<script.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?</style>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "\n", html)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = ""
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    if m:
        title = m.group(1).strip()
    # 发布日期:详情页正文中首个 20xx-xx-xx / 20xx年xx月xx日
    date = ""
    for cand in re.findall(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})日?", text):
        try:
            date = f"{int(cand[0]):04d}-{int(cand[1]):02d}-{int(cand[2]):02d}"
            break
        except ValueError:
            continue
    # 正文裁剪:从标题出现处起(找不到就从全文起),到页脚关键词止
    body_start = 0
    if title:
        for i, ln in enumerate(lines):
            if title.split("-")[0][:6] and title[:6] in ln:
                body_start = i
                break
    footers = ("免责声明", "关于我们", "会员中心", "Copyright", "版权所有", "温馨提示")
    body_end = len(lines)
    for i in range(body_start + 1, len(lines)):
        if any(f in lines[i] for f in footers):
            body_end = i
            break
    text = "\n".join(lines[body_start:body_end]).strip()
    return {"title": title, "date": date, "text": text}


# ── 入库(写文件 + 落库 + 触发 LLM 处理) ───────────────────────────────

def _sanitize_filename(name: str) -> str:
    """文件名安全化:非法字符替换为下划线,截断到 60 字符。"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return safe[:60] or "report"


def _ingest_one(org: str, item: dict, detail: dict) -> bool:
    """把一份研报写入本地文件 + 落库 + 触发后台 LLM 处理。

    【参数】org: 机构名;item: 列表项 {id,title,url};detail: 详情 {title,date,text}。
    【返回】bool: 成功(含 LLM 处理已启动)返回 True;正文为空/入库失败返回 False。
    【关键逻辑】1) 正文写 RESEARCH_UPLOAD_DIR/{org}/{id}_{标题}.md;
              2) insert_research_report(variety 留空由 LLM 识别)拿 report_id;
              3) 调 web_app._process_research_report 后台处理(懒 import,复用
                 既有 LLM 提取 + 按品种 upsert 全链路)。
    """
    text = (detail.get("text") or "").strip()
    if not text:
        print(f"    ! {item['id']} {item['title']} 正文为空,跳过")
        return False

    # 懒导入:复用 web_app 的存储目录与后台处理链路(避免模块加载重);get_db 落库。
    from database import get_db  # 【调用包】数据库实例(落 research_reports 表)
    from web_app import (  # 【调用包】研报存储目录 + 后台处理函数
        RESEARCH_UPLOAD_DIR,
        _process_research_report,
    )

    upload_dir = RESEARCH_UPLOAD_DIR / org  # 【变量】该机构子目录
    upload_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{item['id']}_{_sanitize_filename(item['title'])}.md"  # 【变量】本地文件名(带报告 id,便于定位)
    file_path = upload_dir / fname
    md = (
        f"# {item['title']}\n"
        f"- 来源: 发现报告-{org}\n"
        f"- 链接: {item['url']}\n"
        f"- 日期: {detail.get('date', '')}\n\n"
        f"{text}\n"
    )
    file_path.write_text(md, encoding="utf-8")

    report_id = get_db().insert_research_report(
        variety="",
        title=item["title"],
        source=f"发现报告-{org}",
        filename=file_path.name,
        file_path=str(file_path),
    )
    _process_research_report(report_id)  # 【调用函数】复用 web_app 后台处理(LLM 提取 → 落库 → 写聚合 JSON)
    print(f"    + {item['id']} {item['title']} -> report_id={report_id}")
    return True


def ingest_org(org: str, dry_run: bool = False, max_per_org: int | None = None) -> tuple[int, int, list[str]]:
    """接入单个机构的最新研报。

    【参数】org: 机构名;dry_run: 只打印不写库;max_per_org: 首日限流(只对
            首次运行生效)。
    【返回】(collected, processed, errors):新报告数 / 成功处理数 / 错误列表。
    【关键逻辑】1) 抓第 1 页列表;2) 无状态=首次运行→处理前 max_per_org 条;
              3) 有状态→只处理 id 大于水位的新报告;4) 处理完把水位推进到
              本页最大 id(失败报告不阻塞水位推进,DB 留 error 行可手动重传);
              5) 单条失败不中断整家机构。
    """
    listing = fetch_org_listing(org, page=1)
    if not listing:
        return 0, 0, [f"{org}: 列表抓取失败/为空"]
    page_max = max(item["id"] for item in listing)

    state = _load_state()
    prev = state.get(org, {}).get("max_id", 0)
    has_state = org in state

    if not has_state:
        candidates = listing
        if max_per_org and len(candidates) > max_per_org:
            candidates = candidates[:max_per_org]
        print(f"[{org}] 首次接入,本页 {len(listing)} 条,本次处理 {len(candidates)} 条")
    else:
        candidates = [it for it in listing if it["id"] > prev]
        print(f"[{org}] 水位 {prev} -> 本页最大 {page_max},新增 {len(candidates)} 条")

    collected = processed = 0
    errors: list[str] = []
    for item in candidates:
        try:
            detail = fetch_detail(item["id"])
            if dry_run:
                print(f"  [DRY] {item['id']} {item['title']} ({detail.get('date', '无日期')})")
                collected += 1
                continue
            ok = _ingest_one(org, item, detail)
            collected += 1
            if ok:
                processed += 1
        except Exception as e:  # 【异常】单条失败:记录并继续,不拖垮整家机构
            errors.append(f"{org}/{item['id']}: {e}")
            print(f"    ! {item['id']} 处理异常: {e}")
        time.sleep(SLEEP_BETWEEN)

    if not dry_run:
        state[org] = {"max_id": page_max, "last_run": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
        _save_state(state)
    return collected, processed, errors


def ingest_all(dry_run: bool = False, max_per_org: int | None = None) -> dict:
    """接入全部 5 家机构,聚合结果。

    【返回】{"orgs": N, "collected": M, "processed": K, "errors": [..], "dry_run": bool}。
    【关键逻辑】单家失败(列表抓取异常)不影响其他家,记入 errors。
    """
    total_c, total_p, total_e = 0, 0, []
    for org in ORGS:
        try:
            c, p, errs = ingest_org(org, dry_run=dry_run, max_per_org=max_per_org)
            total_c += c
            total_p += p
            total_e.extend(errs)
        except Exception as e:  # 【异常】整家机构异常:记录并继续下一家
            total_e.append(f"{org}: {e}")
            print(f"[{org}] 接入异常: {e}")
    print(f"Collected: {total_c}")
    print(f"Processed: {total_p}")
    if total_e:
        print(f"Errors: {len(total_e)}")
        for e in total_e[:10]:
            print(f"  - {e}")
    return {"orgs": len(ORGS), "collected": total_c, "processed": total_p, "errors": total_e, "dry_run": dry_run}


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> int:
    """CLI 入口:--org 可重复;--dry-run 只打印;--max-per-org 限流首日。"""
    ap = argparse.ArgumentParser(description="每日开盘前自动接入期货公司研报(发现报告 5 家统一源)")
    ap.add_argument("--org", action="append", choices=ORGS, help="只接入指定机构(可重复),缺省全部 5 家")
    ap.add_argument("--dry-run", action="store_true", help="只打印候选报告,不写库不调用 LLM")
    ap.add_argument("--max-per-org", type=int, default=None, help="首次接入时每家最多处理份数(默认全部第 1 页)")
    args = ap.parse_args()

    t0 = time.time()
    if args.org:
        # 指定机构:逐家接入并累加统计(与 ingest_all 同样的输出格式)
        total_c = total_p = 0
        for org in args.org:
            try:
                c, p, _errs = ingest_org(org, dry_run=args.dry_run, max_per_org=args.max_per_org)
                total_c += c
                total_p += p
            except Exception as e:  # 【异常】顶层兜底:单家异常不影响退出码语义
                print(f"[{org}] 接入异常: {e}")
        print(f"Collected: {total_c}")
        print(f"Processed: {total_p}")
    else:
        # 全部 5 家:走 ingest_all(内部已打印 Collected/Processed 汇总)
        ingest_all(dry_run=args.dry_run, max_per_org=args.max_per_org)
    print(f"Took: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
