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
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

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
    "SH": ("SH", "合成橡胶"),
    "EB": ("EB", "苯乙烯", "纯苯"),
}
MIN_BODY_CHARS = 200  # 【变量】正文最小字符数(去空白),低于则跳过不入库
RESERVED_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')  # 【变量】Windows 文件名保留字


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


# ── 单文件入库(镜像 research_collector._ingest_one,输入换成本机 PDF) ────

def _ingest_local_pdf(org: str, pdf: Path) -> tuple[bool, str]:
    """把一份本地 PDF 录入研报库并触发后台 LLM 处理。

    【参数】org: 机构名(永安期货/中信期货等);pdf: PDF 源文件路径。
    【返回】(ok, msg):成功(含 LLM 处理已调用)返回 True;正文过短/入库失败返回 False。
    【关键逻辑】1) 文本 <200 字符跳过;2) 复制 PDF 到 RESEARCH_UPLOAD_DIR/{org}/;
              3) insert_research_report(variety 留空);4) _process_research_report。
    """
    # 懒导入:复用 web_app 的存储目录/PDF 提取/后台处理链路(避免模块加载重)
    from database import get_db  # 【调用包】数据库实例(落 research_reports 表)
    from web_app import (  # 【调用包】研报存储目录 + 文本提取 + 后台处理
        RESEARCH_UPLOAD_DIR,
        _extract_pdf_text,
        _process_research_report,
    )

    text = (_extract_pdf_text(pdf) or "").strip()
    if len(text) < MIN_BODY_CHARS:
        return False, "正文过短/无可提取文本"

    safe_name = RESERVED_RE.sub("_", pdf.name) or pdf.stem  # 【变量】目标文件名(保留字字符替换)
    upload_dir = RESEARCH_UPLOAD_DIR / org  # 【变量】该机构子目录(与 fxbaogao 同规则)
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / safe_name
    if target.exists():
        return False, "目标已存在(幂等跳过)"

    shutil.copy2(str(pdf), str(target))  # 【调用函数】复制 PDF 原件(保留时间戳)

    report_id = get_db().insert_research_report(
        variety="",
        title=pdf.stem,
        source=f"{org}-研报暂存",
        filename=pdf.name,
        file_path=str(target),
        ingest_source="auto",  # 【来源】本地批量导入(非网页手动上传,数据仓库徽标归"自动")
    )
    _process_research_report(report_id)  # 【调用函数】复用 web_app 后台处理(LLM 识别品种 → 落库 → 写聚合 JSON)
    return True, f"report_id={report_id}"


# ── 批量入口 ────────────────────────────────────────────────────────────

def ingest_dir(pdf_dir: Path, org: str, requested: set[str], dry_run: bool) -> dict:
    """录入目录下命中品种的 PDF(全部或 --variety 过滤)。

    【返回】{"matched","imported","skipped","errors","hits"} 统计与逐文件命中详情。
    【关键逻辑】单文件异常不中断(dry_run 只列命中不写库)。先列命中清单,再由调用方
              决定是否真正执行(小样测试:先跑 --dry-run 确认计数再真跑)。
    """
    pdfs = sorted(pdf_dir.glob("*.pdf"), key=lambda p: p.name)
    hits = []  # 【变量】hits: [(文件名, [命中的品种代码])]
    for p in pdfs:
        if requested:  # 指定品种:按文件名命中过滤
            codes = match_varieties(p.name, requested)
            if not codes:
                continue
        else:  # 未指定品种:目录全部
            codes = []
        hits.append((p, codes))

    matched = imported = skipped = 0
    errors: list[str] = []
    for p, codes in hits:
        matched += 1
        if dry_run:
            print(f"  [DRY] {p.name}  [{','.join(codes)}]")
            continue
        try:
            ok, msg = _ingest_local_pdf(org, p)
            print(f"  {'+' if ok else '!'} {p.name} [{','.join(codes)}] {msg}")
            if ok:
                imported += 1
            else:
                skipped += 1
        except Exception as e:  # 【异常】单文件失败:记录并继续,不拖垮整批
            errors.append(f"{p.name}: {e}")
            print(f"    ! {p.name} 处理异常: {e}")

    return {"matched": matched, "imported": imported, "skipped": skipped,
            "errors": errors, "hits": [(p.name, c) for p, c in hits]}


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> int:
    """CLI 入口:--dir 必填;--org 必填;--variety 可重复过滤;--dry-run 只列不写。"""
    ap = argparse.ArgumentParser(description="本机 PDF 研报批量录入(复用研报 LLM 链路)")
    ap.add_argument("--dir", required=True, help="PDF 源目录")
    ap.add_argument("--org", required=True, help="机构名(如 永安期货/中信期货),决定目标子目录")
    ap.add_argument("--variety", nargs="+", help="只录入命中这些品种代码的文件(空格分隔多个),缺省=目录全部")
    ap.add_argument("--dry-run", action="store_true", help="只列命中的 PDF,不复制不写库不调 LLM")
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
    res = ingest_dir(pdf_dir, args.org, requested, dry_run=args.dry_run)
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
