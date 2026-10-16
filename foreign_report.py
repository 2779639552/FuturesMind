# -*- coding: utf-8 -*-
"""外文研报 → 中文编译 PDF（AgentSense「外文研报」tab 的服务端管线）。

【这个文件在项目里的角色】
用户上传一份外文（英文为主）研报 / 券商周报 / 机构月报 PDF，本模块把它编译成
一份可直接呈报领导的中文**横版 PPT(16:9) 两列** PDF：顶部色块 → 核心结论 → 结论与逻辑支撑 →
关键数据速查 → 内部关联解读 → 全文翻译 → 术语对照与编译说明。

【产物形态来自哪里】
交付结构与版式沿用已验证的技能包 `foreign-report-cn-translation`
（assets/foreign_report_pdf.css 是原技能包 CSS 的**字节级复制**，连同全部踩坑修法：
负左边距吃字、break-inside 大面积留白、封面负 margin 多出一页、跨页表头丢失等）。
本模块只负责"把 LLM 产出的结构化内容填进那套版式"，不改版式本身。

【管线阶段与进度权重】
    提取 0-8% → 分析 8-26%（4 路并行） → 翻译 26-88%（分块并行）
    → 排版 88-92% → 出图 92-99% → 完成 100%

【并发与外部约束】
· 4 路「分析」并行：互不依赖，都是"全文 + 各自视角"，可安全并发。
· 翻译分块并行：默认 3 线程。块内顺序由下标固定回收，保证输出绝不乱序。
· 所有 LLM 调用共用一个 llm 对象；invoke 失败按 1.5s/3s/4.5s 退避重试。

【诚实边界】
· 数字/判断/引述一律要求 LLM 严格取自原文，不得外推（见各 PROMPT 的硬性要求）。
· 任何一段翻译彻底失败时，**在正文原位留显式占位标记**并在封面后给出提示，
  绝不用"省略"掩盖——编译稿一旦静默缺段就失去备查价值。
"""
from __future__ import annotations

import base64
import html as _html
import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# ═══════════════════════════ 路径与常量 ═══════════════════════════

ROOT = Path(__file__).resolve().parent  # 【变量】项目根(D:\AgentSense\AgentSense)
DATA_DIR = ROOT / "data" / "foreign"  # 【变量】本功能全部落盘数据的根目录
UPLOAD_DIR = DATA_DIR / "uploads"  # 【变量】用户上传的原始 PDF
JOBS_DIR = DATA_DIR / "jobs"  # 【变量】任务状态 JSON(重启后仍可在页面看到历史任务)
OUT_DIR = DATA_DIR / "out"  # 【变量】每任务一个子目录: 生成的 PDF + 中间产物
CSS_PATH = ROOT / "assets" / "foreign_report_pdf.css"  # 【变量】技能包 CSS 副本

MAX_UPLOAD_BYTES = 60 * 1024 * 1024  # 【变量】单文件上传上限 60MB(外文机构报告含图常 10-30MB)
MAX_ANALYZE_CHARS = 90000  # 【变量】送入"分析"阶段的正文字符上限(超出截尾并记录,避免超上下文)
TRANSLATE_CHUNK_CHARS = 3200  # 【变量】单次翻译请求的原文长度(约合 1200-1500 输出 token,避免截断)
TRANSLATE_WORKERS = 3  # 【变量】翻译并发度(对齐外部账户并发余量)
# ── 【2026-10-14 提速】跨阶段的**全局** LLM 并发闸 ─────────────────────────
# 【为什么要它】改造后「分块笔记/四路归纳」与「全文翻译」**同时**跑：归纳最多 4 路、
#   翻译 3 路，若各自为政总并发会到 7 —— 超过外部账户配额(实测 5)必然被限流。
#   所以并发**不再靠"把每个池子调小"**（那样一并行就失控），而是所有真实调用
#   统一先过这把闸：无论多少个线程在跑，真正在飞的请求数不超过 LLM_CONCURRENCY。
LLM_CONCURRENCY = 5  # 【变量】外部 LLM 账户的并发上限(与 TRANSLATE_WORKERS 的关系见上)
_LLM_SEM = threading.BoundedSemaphore(LLM_CONCURRENCY)
MIN_PAGE_TEXT_CHARS = 200  # 【变量】全文文字量低于此值判定为扫描版(无文字层),直接报错要求 OCR
MAX_IMAGES = 14  # 【变量】最多收录多少张原文内嵌图(防图片型 PDF 产出巨型文件)
MIN_IMAGE_SIDE = 120  # 【变量】忽略任一边小于该像素的图(页眉 logo/项目符号)
MIN_IMAGE_BYTES = 6000  # 【变量】忽略小于该字节数的图(装饰性图形)

# ── 【2026-10-14 改版】出稿尺寸 = 横版 PPT(16:9) ──────────────────────────
# 【为什么换尺寸】用户要求改成横版 PPT 大小、便于直接嵌进汇报材料；标准 PPT
#   13.333in × 7.5in = 338.67mm × 190.5mm。
# ⚠ 改了尺寸就必须同步改 `_blank_page_audit` 的扫描范围（它原先写死 A4 的 y∈[34,795]，
#   横版页高只有约 540pt ⇒ 扫描区间会整体越界、`last` 恒为 0 ⇒ 留白自检**静默全绿**）。
PPT_W_MM = "338.67mm"
PPT_H_MM = "190.5mm"

# ── 【2026-10-13 新增】超长正文的"分层归纳"(仅对 chars > MAX_ANALYZE_CHARS 的文件启用) ──
# 【要解决的问题】改造前: 四路归纳(meta/logic/data/insight)各自把**同一份全文**塞进 prompt,
#   正文超 90,000 就被 _join_text 硬截尾 ⇒ 实测 13 个任务里 4 个超限(31%), 最坏 262,637
#   字符时归纳只覆盖前 34%, 而砍掉的恰好是中后部的预测表/平衡表(研究员最需要的部分)。
# 【改造思路】超限文件改走两级: 2a 分块笔记(Map, 原文只送一遍) → 2b 四路归纳共享压缩稿。
#   因为原文只送一遍, 总输入量反而比"四路各送 90,000"更低(实测 4 个超限文件合计 -37%)。
# 【为什么仅超限时启用】≤90,000 的文件完全走原路径, 与改造前逐字段等价(零回归);
#   分层里的压缩是**有损**的, 只让真正超限的文件承担这一代价。
MAP_CHUNK_CHARS = 80000  # 【变量】分块笔记的单块原文上限(262K→4 块; 留足余量给输出)
MAP_OVERLAP_CHARS = 2000  # 【变量】相邻块的重叠字符数(防跨块结论被切断, 按段落边界对齐)
MAX_MAP_CHUNKS = 8  # 【变量】分块数上限(≈64 万字符); 超出即降级为加权抽样, 防块数爆炸
NOTE_MAX_CHARS = 2500  # 【变量】单块笔记的字数上限(8 块=2 万, 远低于四路归纳上下文余量)
REDUCE_HEAD_CHARS = 3000  # 【变量】归纳阶段原样附带的原文首部长度(保封面标题/机构/期号/日期)
MAP_MAX_LEVELS = 2  # 【变量】压缩稿仍超限时的递归压缩上限(超过则降级抽样)

STAGE_LABELS = {
    "queued": "排队中",
    "extract": "解析原文",
    "analyze": "通读并归纳",
    "translate": "全文翻译",
    "compose": "排版编译稿",
    "render": "生成 PDF",
    "done": "已完成",
    "error": "失败",
}


# ═══════════════════════════ 通用工具 ═══════════════════════════


def _now() -> str:
    """当前本地时间字符串（与页面其它模块的时间口径一致）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_name(s: str, fallback: str = "report", limit: int = 60) -> str:
    """把任意文本压成安全的文件名片段（Windows 非法字符全部替换为 _）。"""
    t = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(s or "")).strip(" ._")
    t = re.sub(r"_{2,}", "_", t)
    if not t:
        t = fallback
    return t[:limit]


def _as_dict(v) -> dict:
    """LLM 结构防御：期望 dict 的位置即使拿到 str/list 也安全降级为空 dict。

    【为什么必须有】实测模型偶发把某个节（如 summary）直接回成一段文字而不是对象，
    不做归一化会让整个任务在排版阶段 AttributeError 崩掉。
    """
    return v if isinstance(v, dict) else {}


def _as_list(v) -> list:
    """LLM 结构防御：期望 list 的位置安全降级为空 list。"""
    return v if isinstance(v, list) else []


def _as_str_list(v) -> list[str]:
    """把要点类字段安全压成字符串列表。

    【关键逻辑】元素若被模型写成对象（如 {"text": "..."}），取其最长的字符串值，
    绝不让 Python 的 dict repr（`{'text': ...}`）漏进成稿。
    """
    out: list[str] = []
    for x in _as_list(v):
        if isinstance(x, dict):
            best = max((str(y) for y in x.values() if y), key=len, default="")
            if best.strip():
                out.append(best)
        elif str(x).strip():
            out.append(str(x))
    return out


def _rich(s, allow_br: bool = False) -> str:
    """把 LLM 产出的文本转成可安全嵌入 HTML 的富文本。

    【关键逻辑】先 html.escape 再只放行 **加粗** 与（可选）换行，因此 LLM 就算吐出
    `<script>` 也只会显示成字面文本；显式写的 `<br>` 在 allow_br 时被还原为真换行。
    """
    t = _html.escape(str(s or ""))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    if allow_br:
        t = t.replace("&lt;br&gt;", "<br>").replace("\n", "<br>")
    else:
        t = re.sub(r"[ \t]*\n[ \t]*", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _load_css() -> str:
    """读取技能包 CSS 副本；缺失时给出可诊断的错误而不是静默降级成无样式 PDF。"""
    try:
        return CSS_PATH.read_text(encoding="utf-8")
    except Exception as e:  # pragma: no cover - 只在部署缺文件时触发
        raise RuntimeError(f"样式文件缺失：{CSS_PATH}（{e}）") from e


# ═══════════════════════════ LLM 调用 ═══════════════════════════


def _make_llm():
    """按项目既定工厂建一个 LLM 对象（provider/model 从 DEFAULT_CONFIG 读，不硬编码）。"""
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.llm_clients import create_llm_client

    cfg = DEFAULT_CONFIG
    model = cfg.get("quick_think_llm") or cfg["deep_think_llm"]
    client = create_llm_client(cfg["llm_provider"], model, no_thinking=True)
    return client.get_llm()


def _repair_json_text(s: str) -> str:
    r"""把 LLM 产出的「几乎合法」的 JSON 修成合法：**补漏写的逗号** + 转义字符串里的裸换行
    + 去掉尾随逗号。

    【为什么要自己写】项目 venv 里**没有** json_repair / json5 / demjson3（2026-10-14 实测），
    而 LLM 输出 JSON 的语法错误里**「漏逗号」最常见**：实测任务 2c5e12096595 报
    ``JSONDecodeError: Expecting ',' delimiter: line 241 column 101 (char 17154)``
    ⇒ 5 块分块笔记里 1 块整块作废（压缩稿少覆盖第 16~28 页）。这类错误在结构上
    **完全可判定**，本地修比再花一次 LLM 调用去修更快也更省。

    【判据】一个**完整值**结束后（字符串闭合 / ``}`` / ``]`` / 数字 / true/false/null），
    若下一个非空白字符又是**值的开始**（``"`` / ``{`` / ``[`` / 数字 / true/false/null），
    说明两者之间漏了逗号，就地补上。
    注意 key 与 value 的分界：遇 ``:`` 置 ``prev=":"``，因此
    「key 字符串闭合 → ``:`` → value 字符串开始」不会被误补逗号；
    只有「value 结束 → 紧接下一个 key/value」才补。

    【刻意不做的事】不改单引号、不补缺失的引号 —— 这两类「修错」的风险高于收益
    （内容里的撇号会被误当成字符串定界符，反而把好数据改坏）。宁可抛错让上层重试。
    """
    out: list[str] = []
    i, n = 0, len(s)
    in_str = False
    esc = False
    prev = ""  # 上一个有意义 token: '' | '{' | '[' | ',' | ':' | 'v'(值已结束)

    while i < n:
        c = s[i]

        # ── 字符串内部：JSON 不允许裸换行/制表符，就地转义 ───────────────
        if in_str:
            if c == "\n":
                out.append("\\n")
            elif c == "\r":
                out.append("\\r")
            elif c == "\t":
                out.append("\\t")
            else:
                out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
                prev = "v"
            i += 1
            continue

        if c == '"':
            if prev == "v":      # ← 值结束后又来个字符串 ⇒ 漏逗号，补上
                out.append(",")
            out.append(c)
            in_str = True
            prev = '"'
            i += 1
            continue

        if c in " \t\r\n":
            out.append(c)
            i += 1
            continue

        if c == ":":
            out.append(c)
            prev = ":"
            i += 1
            continue

        if c == ",":
            out.append(c)
            prev = ","
            i += 1
            continue

        if c in "{[":
            if prev == "v":
                out.append(",")
            out.append(c)
            prev = c
            i += 1
            continue

        if c in "}]":
            out.append(c)
            prev = "v"
            i += 1
            continue

        tok = None
        for lit in ("true", "false", "null"):
            if s.startswith(lit, i):
                tok = lit
                break
        if tok:
            if prev == "v":
                out.append(",")
            out.append(tok)
            prev = "v"
            i += len(tok)
            continue

        if c.isdigit() or c == "-":
            j = i
            while j < n and (s[j].isdigit() or s[j] in "+-.eE"):
                j += 1
            if prev == "v":
                out.append(",")
            out.append(s[i:j])
            prev = "v"
            i = j
            continue

        out.append(c)
        i += 1

    return "".join(out)


def _json_from(text: str) -> dict:
    """从 LLM 响应里尽最大努力取出一个 JSON 对象。

    【为什么需要】模型常把 JSON 包在 ```json 围栏里，或前后带一句解释；
    偶尔还会漏写逗号、留尾随逗号、或在字符串里塞裸换行。
    四级兜底：**直解 → 截首尾大括号 → 去尾随逗号 → 状态机修复**。

    【第四级为什么必要】前三级都只是「切掉多余部分」，对**结构内的语法错误**
    （漏逗号）无能为力 —— 实测 2026-10-14 就有整块笔记因此作废（见 _repair_json_text）。
    """
    if not text:
        raise ValueError("响应为空")
    s = str(text).strip()
    s = re.sub(r"^```[a-zA-Z0-9_]*\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s).strip()

    # ① 直解
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("响应里找不到 JSON 对象")
    frag = s[i : j + 1]

    # ② 截首尾大括号再解
    try:
        obj = json.loads(frag)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # ③ 去尾随逗号
    try:
        obj = json.loads(re.sub(r",(\s*[}\]])", r"\1", frag))
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # ④ ★ 状态机修复（补漏逗号 + 转义裸换行）后再去一次尾随逗号
    src = _repair_json_text(frag)
    for cand in (src, re.sub(r",(\s*[}\]])", r"\1", src)):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue

    raise ValueError("响应不是合法 JSON")


def _msg_text(r) -> str:
    """从 LLM 响应取正文文本；**content 为空时回退 reasoning_content**。

    【为什么必须回退】思维模型（火山方舟/DeepSeek 的 thinking 模型、MiniMax M2.x 等）
    会把内容全塞进 ``reasoning_content`` 而把 ``content`` 留空。项目已在
    ``DeepSeekChatOpenAI._create_chat_result`` 里把该字段捕获进
    ``additional_kwargs``，但**此前没有任何调用点使用它** —— 于是"模型其实已经答了"
    被一律误判成"响应为空"（2026-09-29 外文研报「封面与核心结论」路实测：3 次重试
    全部拿到空 content 而任务置 error）。
    【顺带归一化】content 可能是 list（分块），需拼接；思维链可能混 ``<think>`` 标记，
    剥掉后再交给 JSON 解析器，避免"内容在但解析不出来"。
    """
    content = getattr(r, "content", r)
    if isinstance(content, list):
        content = "".join(str(x) for x in content)
    text = str(content or "").strip()
    if text:
        return text
    reas = (getattr(r, "additional_kwargs", None) or {}).get("reasoning_content")
    if reas:
        return re.sub(r"</?think>", "", str(reas)).strip()
    return ""


def _ask_json(llm, prompt: str, label: str, retries: int = 2) -> dict:
    """要一个 JSON 回来，失败按退避重试；连续失败抛 RuntimeError 交由任务置 error。"""
    last = ""
    for attempt in range(retries + 1):
        p = prompt
        if attempt:
            p = prompt + (
                "\n\n【重要】上一次没有给出可用的 JSON。这次**只输出 JSON 本体**："
                "不要代码块围栏、不要任何前后解释、不要省略号。"
            )
        try:
            with _LLM_SEM:  # 【并发闸】见 LLM_CONCURRENCY 注释
                r = llm.invoke(p)
            body = _msg_text(r)  # 【调用函数】取正文(含 reasoning_content 兜底)
            data = _json_from(body)
            if data:
                return data
            last = "JSON 为空对象"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:200]}"
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"[{label}] 归纳失败：{last}")


def _ask_text(llm, prompt: str, label: str, retries: int = 2) -> str:
    """要一段纯文本回来（翻译用）；连续失败抛 RuntimeError。"""
    last = ""
    for attempt in range(retries + 1):
        try:
            with _LLM_SEM:  # 【并发闸】见 LLM_CONCURRENCY 注释
                r = llm.invoke(prompt)
            body = _msg_text(r)  # 【调用函数】取正文(含 reasoning_content 兜底)
            body = re.sub(r"^```[a-zA-Z0-9_]*\s*", "", body)
            body = re.sub(r"\s*```\s*$", "", body).strip()
            if body:
                return body
            last = "响应为空"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:200]}"
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"[{label}] 失败：{last}")


# ═══════════════════════════ 阶段 1：提取原文 ═══════════════════════════


def extract_pdf(pdf_path: Path, img_dir: Path, include_images: bool = True) -> dict:
    """用 pymupdf 抽出逐页文字与内嵌图片。

    【返回】{"pages":[{"no":1,"text":...}], "images":[{"page":1,"mime":..,"data":bytes}],
             "n_pages":int, "chars":int}
    【异常】无文字层（扫描版）时抛 ValueError——OCR 不在本功能范围内，必须显式告知用户。
    """
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    pages: list[dict] = []
    for pno, page in enumerate(doc, 1):
        try:
            txt = page.get_text("text") or ""
        except Exception:
            txt = ""
        pages.append({"no": pno, "text": txt})

    total_chars = sum(len(p["text"]) for p in pages)
    if total_chars < MIN_PAGE_TEXT_CHARS:
        doc.close()
        raise ValueError(
            "这份 PDF 没有文字层（疑似扫描件/图片型），无法直接提取正文。"
            "请先用 OCR 工具转成带文字层的 PDF 后再上传。"
        )

    images: list[dict] = []
    if include_images:
        img_dir.mkdir(parents=True, exist_ok=True)
        seen: set[str] = set()
        for pno, page in enumerate(doc, 1):
            try:
                raw_list = page.get_images(full=True) or []
            except Exception:
                continue
            for item in raw_list:
                if len(images) >= MAX_IMAGES:
                    break
                xref = item[0]
                try:
                    info = doc.extract_image(xref)
                except Exception:
                    continue
                data = info.get("image") or b""
                w, h = int(info.get("width") or 0), int(info.get("height") or 0)
                if w < MIN_IMAGE_SIDE or h < MIN_IMAGE_SIDE or len(data) < MIN_IMAGE_BYTES:
                    continue
                key = f"{w}x{h}:{len(data)}"
                if key in seen:  # 同一张图在页眉/页脚重复出现，去重
                    continue
                seen.add(key)
                images.append({"page": pno, "mime": info.get("ext") or "png", "data": data})
            if len(images) >= MAX_IMAGES:
                break
    doc.close()

    return {"pages": pages, "images": images, "n_pages": len(pages), "chars": total_chars}


def _join_text(pages: list[dict], limit: int = MAX_ANALYZE_CHARS) -> tuple[str, bool]:
    """把逐页文字拼成分析用正文；超限截尾并返回是否被截断。"""
    parts, used = [], 0
    for p in pages:
        t = (p["text"] or "").strip()
        if not t:
            continue
        seg = f"\n\n［第 {p['no']} 页］\n{t}"
        if used + len(seg) > limit:
            parts.append(seg[: max(0, limit - used)])
            return "".join(parts), True
        parts.append(seg)
        used += len(seg)
    return "".join(parts), False


# ── 【2026-10-13 新增】超长正文的分层归纳: 分块工具 + 降级抽样 ──────────────

def _para_units(pages: list[dict]) -> list[dict]:
    """把逐页正文拆成 (页码, 段落) 单元，供分块与抽样共用。

    【为什么要独立成函数】分块笔记(_chunk_for_notes)与降级抽样(_sample_text)都要
      "按段落"处理 —— 段落是语义最小单元, 只有落在段落边界上才不会把一句话劈成两半。
    【返回】[{"page": 页码, "text": 段落}]，保持原文顺序。
    """
    units: list[dict] = []
    for p in pages:
        paras = [x.strip() for x in re.split(r"\n\s*\n", p.get("text") or "") if x.strip()]
        for para in paras:
            units.append({"page": p.get("no"), "text": para})
    return units


def _units_text(units: list[dict]) -> str:
    """把段落单元拼回正文(分隔口径与 _join_text 一致: 双换行)。"""
    return "\n\n".join(u["text"] for u in units)


def _render_units(units: list[dict], idxs: list[int]) -> str:
    """把指定段落按下标渲染成**带页码标记**的正文（页码变化时才插标记）。

    【为什么必须带页码】改造前 `_join_text` 给每页插「［第 N 页］」；分块笔记的 prompt
      要求每条结论标注页码（`page` 字段），若块文本里没有页码标记，模型只能编。
      分块与抽样都复用本函数，保证"页码可追溯"这一性质在所有路径上都不丢。
    """
    out: list[str] = []
    cur = None
    for i in idxs:
        pg = units[i]["page"]
        if pg != cur:
            out.append(f"［第 {pg} 页］")
            cur = pg
        out.append(units[i]["text"])
    return "\n\n".join(out)


def _chunk_for_notes(
    pages: list[dict],
    size: int = MAP_CHUNK_CHARS,
    overlap: int = MAP_OVERLAP_CHARS,
    max_chunks: int = MAX_MAP_CHUNKS,
) -> tuple[list[dict], bool]:
    """把逐页正文切成"分块笔记"用的块: 段落边界对齐 + 尾部重叠。

    【返回】(chunks, truncated)。
      chunks = [{"text", "pages", "u_from", "u_to"}]；
      truncated=True 表示块数触顶、后半段未纳入分块 → **调用方必须降级为加权抽样**。
    【关键逻辑】
      · 累积到接近 size 就收块 —— 只在**段落之间**落刀, 绝不从句子中间切；
      · 新块开头带回上一块尾部约 overlap 字符的段落, 免得跨块的结论被切断；
      · u_from/u_to 为块覆盖的段落下标区间, 供单测断言"全覆盖、无空隙"。
    【为什么记下标而不是字符偏移】下标与 _para_units 一一对应, 断言无需再算偏移,
      也不会因换行符口径差异(\\n vs \\r\\n)而假报空隙。
    """
    units = _para_units(pages)
    chunks: list[dict] = []
    buf: list[int] = []  # 【变量】当前块累积的段落下标
    buf_n = 0

    def _pack(idxs: list[int]) -> dict:
        pgs: list[int] = []
        for i in idxs:
            pg = units[i]["page"]
            if pg not in pgs:
                pgs.append(pg)
        return {
            "text": _render_units(units, idxs),  # 【调用函数】带页码标记, 供笔记标 page
            "pages": pgs,
            "u_from": idxs[0],
            "u_to": idxs[-1],
        }

    for i, u in enumerate(units):
        L = len(u["text"])
        if buf and buf_n + L + 2 > size:
            chunks.append(_pack(buf))
            if len(chunks) >= max_chunks:
                return chunks, True  # 触顶: 交调用方降级(不静默丢, 由 truncated 显式告知)
            # 重叠: 从刚收块的尾部回取足 overlap 的段落; 同时防止"超长单段"把重叠撑爆
            tail: list[int] = []
            tn = 0
            for j in reversed(buf):
                if tail and tn + len(units[j]["text"]) + 2 > size // 2:
                    break
                tail.insert(0, j)
                tn += len(units[j]["text"]) + 2
                if tn >= overlap:
                    break
            buf = tail
            buf_n = sum(len(units[j]["text"]) + 2 for j in buf)
        buf.append(i)
        buf_n += L + 2

    if buf:
        chunks.append(_pack(buf))
    return chunks, False


def _sample_text(
    pages: list[dict],
    limit: int = MAX_ANALYZE_CHARS,
    weights: tuple[float, float, float] = (3.0, 1.0, 2.0),
    zones: tuple[float, float, float] = (0.15, 0.70, 0.15),
    headroom: float = 0.85,
) -> tuple[str, bool]:
    """降级路径: 把超长正文压成 ≤limit 的**加权抽样**(头 3× / 正文 1× / 尾 2×)。

    【为什么需要这条兜底】分层归纳(B)正常时已覆盖全文; 但若块数触顶(MAX_MAP_CHUNKS)
      或分块笔记大面积失败, 必须有"不依赖额外 LLM 调用"的退路 —— 否则又会退回
      "截尾只覆盖前 34%"的老问题。
    【为什么加权而不均匀截取】机构研报的信息密度是非均匀的: 首页是结论摘要、
      中段是论证过程、尾段是预测表/平衡表/附录。改造前的截尾恰好丢掉尾部,
      而尾部预测表正是研究员最需要的。故 头 3× / 尾 2× / 正文 1×。
    【返回】(text, sampled)；sampled=True 表示发生抽样(已插省略标记, 便于事后核对)。

    【实现要点(踩过坑)】
      ① 用 Bresenham 式布点(每段累加 keep 比例, 满 1 就取)而不是固定跨步 ——
         固定跨步 `idxs[::stride]` 会**取不到末段**: 实测 30 段的尾部区步长 4 时
         命中 170,174,…,194,198 而**漏掉 199**, 而末段常是结论/预测表收尾。
      ② 每个区**强制锚定首尾两段**, 保证"开头结论"与"结尾预测表"必定在场。
      ③ 各区预算只占 limit 的 headroom(默认 85%), 给省略标记与取整留余量 ——
         否则总长会略微超出, 触发兜底硬截又把尾巴切掉(等于白改)。
      ④ 万一仍超限, 按 **正文 → 尾部 → 头部** 的顺序抽稀(正文信息密度最低),
         而不是像截尾那样一刀从尾部切。
    """
    units = _para_units(pages)
    full = _units_text(units)
    if not units or len(full) <= limit:
        return full, False

    total = sum(len(u["text"]) + 2 for u in units)
    # 按字符位置把段落划进 头/正文/尾 三区
    b0 = zones[0]
    b1 = zones[0] + zones[1]
    zone_of: list[int] = []
    acc = 0
    for u in units:
        pos = (acc + len(u["text"]) / 2) / max(1, total)
        zone_of.append(0 if pos < b0 else (1 if pos < b1 else 2))
        acc += len(u["text"]) + 2

    wsum = sum(weights) or 1.0
    pool = limit * max(0.1, min(1.0, headroom))  # 【变量】可分配预算(留余量给省略标记)
    budget = [pool * w / wsum for w in weights]

    picked: list[int] = []
    for zi in range(3):
        idxs = [i for i, z in enumerate(zone_of) if z == zi]
        if not idxs:
            continue
        zchars = sum(len(units[i]["text"]) + 2 for i in idxs)
        if zchars <= budget[zi]:
            picked.extend(idxs)  # 该区本就装得下 → 整区保留
            continue
        keep = budget[zi] / max(1.0, zchars)
        debt = 1.0  # 立即取首段
        for i in idxs:
            debt += keep
            if debt >= 1.0:
                picked.append(i)
                debt -= 1.0
        picked.append(idxs[-1])  # ★ 端点锚定: 保证尾部预测表/收尾结论不被漏掉

    picked = sorted(set(picked))

    def _assemble(idxs: list[int]) -> str:
        out: list[str] = []
        prev = -1
        cur_pg = None
        for i in idxs:
            if prev >= 0 and i > prev + 1:
                skipped = sum(len(units[j]["text"]) + 2 for j in range(prev + 1, i))
                out.append(f"［…略去原文 {i - prev - 1} 段（约 {skipped:,} 字符）…］")
            pg = units[i]["page"]
            if pg != cur_pg:
                out.append(f"［第 {pg} 页］")
                cur_pg = pg
            out.append(units[i]["text"])
            prev = i
        return "\n\n".join(out)

    text = _assemble(picked)
    # 仍超限: 按 正文(1) → 尾部(2) → 头部(0) 的优先级抽稀, 保护各区首尾锚点
    cur = set(picked)
    for sac in (1, 2, 0):
        if len(text) <= limit:
            break
        zidxs = [i for i in sorted(cur) if zone_of[i] == sac]
        protect = {zidxs[0], zidxs[-1]} if zidxs else set()
        while len(text) > limit:
            drop = [i for i in sorted(cur) if zone_of[i] == sac and i not in protect]
            if not drop:
                break
            cur -= set(drop[1::2])  # 隔一个丢一个 → 反复减半, 快速收敛
            text = _assemble(sorted(cur))
    return text, True


# ═══════════════════════════ 阶段 2：四路并行归纳 ═══════════════════════════

_BASE_RULES = """
【全局硬性要求 · 违反即返工】
1. 所有数字、判断、引述**严格来自原文**：不得外推、不得补充你自己的行业知识、
   不得修正或软化原报告的立场。原文说"略偏空"就不能写成"偏空"。
2. 原文含糊处用方括号加译者注，如「接近 950 美元/吨［原文 mid-US$900/t］」。
3. 机构名、专有名词、缩写首次出现时保留原文并括注中文；数字与单位原样照抄。
4. 只输出 JSON 本体：不要代码块围栏、不要任何前后解释文字。
"""

PROMPT_META = (
    _BASE_RULES
    + """
你是一位资深商品研究员，正在把一份外文研报编译成中文编译稿的**第 01 节（核心结论）与封面**。
请通读全文后输出如下 JSON：

{
  "cover": {
    "title_zh": "中文主标题，不要带'中文编译'之类前缀",
    "title_en": "原文标题，保留原语言",
    "org": "发行机构",
    "service": "所属服务/栏目，没有写空字符串",
    "issue": "期号与篇幅，如 '第 37 期（共 4 页）'，没有写空字符串",
    "pub_date": "发行日期，如 '2026 年 9 月 23 日'",
    "confidential": "机密等级标注，如 '机密 · 内部参考'，没有写空字符串",
    "headlines": ["三条主线的短句", "第二条", "第三条"],
    "website": "原文官网域名，没有写空字符串"
  },
  "summary": {
    "lead": "2-4 句总览：本报告聚焦哪几条主线、给出什么方向判断。用 **加粗** 强调关键结论。",
    "cards": [
      {"kk": "结论一", "kv": "不超过 14 字，可用<br>换行", "kd": "一句话依据，不超过 40 字"}
    ],
    "conclusions": [
      {"no": "01", "title": "结论标题，陈述句", "points": ["要点一，可用 **加粗**", "要点二"]}
    ]
  },
  "gloss": [{"en": "LPG", "zh": "液化石油气"}],
  "notes": {
    "scope": "编译范围一句话",
    "data": "数据口径一句话",
    "copyright": "版权与使用一句话"
  }
}

补充要求：
· cards 必须**恰好 3 个**；conclusions 2-4 条，每条 3-5 个要点，要点须含原文的关键数字或时点。
· gloss 10-18 条，覆盖全文出现的缩写与关键术语。
· notes.copyright 必须写明"原文版权归原机构所有，本编译稿仅供内部参考、请勿外传"。
"""
)

PROMPT_LOGIC = (
    _BASE_RULES
    + """
你是一位资深商品研究员，正在写中文编译稿的**第 02 节（结论与逻辑支撑）**。
任务：把原文的论述打散重组为「判断 → 支撑证据 → 推论」三段式。只输出 JSON：

{
  "logic": {
    "lead": "1-2 句说明本节按哪几个主题拆解",
    "topics": [
      {
        "name": "主题一　主题名（中文）",
        "sub": "／ 补充限定，可空字符串",
        "judgement": "一句话判断，必须是原文的明确判断，陈述句",
        "groups": [
          {"st": "支撑证据", "items": ["证据，含具体数字", "证据二"]},
          {"st": "推论", "items": ["推论一"]}
        ]
      }
    ]
  }
}

补充要求：
· topics 以 3 个为宜（原文结构清晰就按原文分），最多 5 个、最少 2 个。
· 每个 topic 的 groups 固定为「支撑证据」+「推论」两组；支撑证据 3-5 条，推论 2-3 条。
· name 用「主题一　xxx」这种形式（中文顿号位置用全角空格分隔），sub 写补充限定，
  例如 '／ 政策与航运'。
· 严禁把所有内容揉成一段；必须让中层读者能顺着证据链复现判断。
"""
)

PROMPT_DATA = (
    _BASE_RULES
    + """
你是一位资深商品研究员，正在写中文编译稿的**第 03 节（关键数据速查）**。
任务：把原文散落的关键数字按主题归组，做成可速查的三列表。只输出 JSON：

{
  "data": {
    "lead": "1 句说明分了几组、覆盖什么范围",
    "groups": [
      {
        "title": "一、价格与价差",
        "cols": ["指标", "数值", "说明"],
        "rows": [
          {"c": ["指标名", "数值（含单位/区间/比较符，原样照抄）", "说明"], "hi": true}
        ]
      }
    ]
  }
}

补充要求：
· groups 3-6 组，每组 3-10 行；cols 固定三列语义，允许把表头措辞改得更贴切但仍须三列。
· "hi": true 只给**本期新增或发生变化**的关键量（会自动加琥珀底纹），每组不超过 3 行。
· 原文没有数字的组不要凭空造；宁可少一组。
· 数值列禁止出现"上升/下降"这类定性词，必须是可核对的原文数值或明确区间。
"""
)

PROMPT_INSIGHT = (
    _BASE_RULES
    + """
你是一位本机构的商品研究员，正在写中文编译稿的**第 04 节（内部关联解读）**。
这一节是**编译方补充、非原文内容**，必须让读者一眼看出这是我们的延伸判断。只输出 JSON：

{
  "insight": {
    "intro": "1-2 句声明：本节为编译方基于原文所作的延伸，非原报告内容。",
    "items": [
      {"title": "一、小标题（结论式，如 '石脑油略偏空但有底 → PX 成本下方有支撑'）",
       "points": ["要点，说明原文哪条结论 → 对哪个品种的什么含义", "要点二"]}
    ],
    "foot": "一句免责：本节为定性判断，不构成投资建议。"
  }
}

本机构关注的品种池（映射时请用「代码（中文名）」书写）：
{WHITELIST}

补充要求：
· items 2-4 条，每条 2-4 个要点。
· **只写原文内容能支撑到的传导链条**，不硬凑：若原文与本机构品种池关联有限，
  items 就只给 1-2 条并如实说明"原文未直接讨论本机构品种，以下为间接传导"。
· 每条要点必须写清"原文依据 → 传导机制 → 对本机构品种的含义"三要素。
"""
)

# ── 【2026-10-13 新增】阶段 2a：分块笔记（超长正文的 Map 步） ──────────────
# 【为什么单独一步】超长正文无法一次送进归纳(上下文装不下)。与其四路各自截尾，
#   不如让原文**只过一遍**——每块抽成结构化笔记，再让四路归纳共享这份压缩稿。
#   这样总输入量反而更低（原文只送一遍），且覆盖全文而非前 34%。
# 【设计要求】笔记必须"可无损重建判断"：结论 + 依据 + 数字(带单位时点) + 术语 + 页码。
#   丢了页码就无法核对，丢了单位数字就会串（这是本项目反复踩过的坑）。
PROMPT_MAP = (
    _BASE_RULES
    + """
你正在把一份**超长外文研报**的其中一块（第 {IDX}/{TOTAL} 块）压缩成结构化笔记，
供后续"归纳"步骤使用。**笔记将是后续步骤唯一能看到的内容**，所以：

【铁律】
1. **凡原文出现的数字、单位、时点必须原样抄录**，一个不许四舍五入、不许换算。
   例："石脑油裂解价差 US$118/t（2026年9月）" 不能写成 "约 120 美元"。
2. 所有条目必须标注**页码**（原文该内容出现在第几页），便于后续核对。
3. 只记原文**明说**的判断；原文没说的不要补。原文说"略偏空"就记"略偏空"。
4. 输出 JSON 本体，不要代码块围栏、不要解释文字。

【输出格式】
{
  "findings": [
    {"point": "该块的结论/判断（陈述句）", "basis": "原文给出的依据（含关键数字）", "page": 3}
  ],
  "numbers": [
    {"item": "指标名称", "value": "数值+单位，原样", "period": "时点/期间", "page": 3}
  ],
  "terms": [{"en": "缩写或术语", "zh": "中文含义"}],
  "meta": {META_SPEC}
}

【补充要求】
· findings 5-12 条：覆盖本块**所有**主要判断，宁多勿漏（后续步骤靠它）。
· numbers **优先记关键指标**：价格、价差、库存、开工率、产量、消费量、预测值、目标价。
  表格里**逐行同构**的数据（如几十行不同油种的裂解价差）不必逐行抄，
  给出**范围与首尾示例**即可（例："裂解毛利区间 -3.4 ~ +27.0 美元/桶，Agbami 27.04、Arab Heavy -3.35"）。
  这样既留住信息量，也不会把笔记撑爆（实测逐行抄表会让笔记超过上下文预算）。
· terms 只收本块新出现的缩写/专有名词。
· 若本块文字与上一块有重叠，**只记本块新增的内容**，不要重复。
· 【篇幅硬约束】整份笔记（findings+numbers+terms+meta）请控制在 {NOTE_MAX} 字符以内。
  超长会被程序按重要性截断，反而丢失后续步骤需要的内容 —— 请主动取舍。
"""
)

PROMPT_TRANS = """把下面这段外文研报正文**完整翻译成简体中文**。

【翻译要求】
1. 逐句完整翻译，**不得删节、不得概括、不得合并段落、不得添加原文没有的内容**。
2. 保留原文的段落划分；小标题单独成行，并在行首加 `### ` 标记它的层级
   （原文一级标题用 `### `，二级用 `#### `），不要给标题加序号。
3. 数字、单位、日期、机构名、专有名词、缩写原样保留；缩写首次出现时括注中文。
4. 原文含糊处用方括号加译者注，如「接近 950 美元/吨［原文 mid-US$900/t］」。
5. **只输出译文**：不要任何前言、后语、总结、"以下是翻译"之类的话，
   也不要代码块围栏。

【原文正文开始】
{TEXT}
【原文正文结束】"""


def _translate_chunk(llm, text: str, idx: int, total: int) -> str:
    """翻译单个分块；失败抛 RuntimeError（由上层记录为显式占位，不静默丢弃）。"""
    prompt = PROMPT_TRANS.replace("{TEXT}", text)
    return _ask_text(llm, prompt, f"翻译第 {idx + 1}/{total} 段", retries=2)


def _chunk_pages(pages: list[dict], size: int = TRANSLATE_CHUNK_CHARS) -> list[dict]:
    """把逐页正文切成翻译分块，并记录每块的页码范围（供插图定位）。

    【关键逻辑】先按段落聚到接近 size，再整块合并——保证不会从句子中间切开。
    """
    chunks: list[dict] = []
    buf: list[str] = []
    buf_n = 0
    buf_pages: list[int] = []

    def flush():
        if buf:
            chunks.append({"text": "\n\n".join(buf), "pages": list(buf_pages)})
            buf.clear()
            buf_pages.clear()

    for p in pages:
        paras = [x.strip() for x in re.split(r"\n\s*\n", p["text"] or "") if x.strip()]
        for para in paras:
            if buf and buf_n + len(para) > size:
                flush()
                buf_n = 0
            buf.append(para)
            buf_n += len(para) + 2
            if p["no"] not in buf_pages:
                buf_pages.append(p["no"])
    flush()
    return chunks


# ── 【2026-10-13 新增】阶段 2a/2b：分块笔记(Map) 与笔记合并(Merge) ────────

# 【变量】首块额外要求的封面元信息字段（只有首页才有标题/机构/期号/日期）
_MAP_META_SPEC = (
    '"title": "原文标题（原语言）+ 中文译名", '
    '"org": "发行机构", "service": "所属服务/栏目", '
    '"issue": "期号与篇幅", "pub_date": "发行日期", "website": "官网域名"'
)
# 【变量】非首块不要求 meta 字段（首页信息不在这一块里，硬要会诱发编造）
_MAP_META_SPEC_TAIL = '"（本块不是首页，将 title/org/service/issue/pub_date/website 全部留空字符串）"'


def _map_notes(llm, chunks: list[dict], on_done=None) -> tuple[list[dict], list[str]]:
    """对每个分块取一份结构化笔记（Map 步，有界并发）。

    【返回】(notes, warnings)。notes = [{...该块的 findings/numbers/terms/meta...}]
      按块序排列；warnings 里点名失败的块（**不静默丢**，交由调用方决定是否降级）。
    【并发】沿用 TRANSLATE_WORKERS(3) 与"bounded 提交"写法 —— 一次只压 3 个在途请求,
      对齐外部账户并发余量；不可为了快而全量提交（本项目实测会被限流）。
    【异常】单块失败按 _ask_json 自带重试; 仍失败则记 warning 并跳过该块,
      不抛异常 —— 缺一块仍可归纳, 缺太多由调用方的失败率阈值决定降级。
    【on_done】进度回调 on_done(done, total)，供上层把 12→20 的进度条推起来。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    notes: list[dict] = []
    warnings: list[str] = []
    total = len(chunks)

    def _one(i: int) -> dict:
        meta_spec = _MAP_META_SPEC if i == 0 else _MAP_META_SPEC_TAIL
        prompt = (
            PROMPT_MAP.replace("{IDX}", str(i + 1))
            .replace("{TOTAL}", str(total))
            .replace("{META_SPEC}", meta_spec)
            .replace("{NOTE_MAX}", f"{NOTE_MAX_CHARS:,}")
            + "\n\n【本块原文开始】\n"
            + chunks[i]["text"]
            + "\n【本块原文结束】"
        )
        return _ask_json(llm, prompt, f"分块笔记 {i + 1}/{total}")

    done = 0
    with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as pool:
        futs = {pool.submit(_one, i): i for i in range(total)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                d = fut.result()
                d["_chunk"] = i
                d["_pages"] = chunks[i].get("pages") or []
                notes.append(d)
            except Exception as e:  # 【异常】单块失败不致命, 记名跳过
                warnings.append(
                    f"第 {i + 1}/{total} 块（原文第 {chunks[i].get('pages')} 页）笔记失败：{str(e)[:110]}"
                )
            done += 1
            if on_done:
                on_done(done, total)
    notes.sort(key=lambda n: n.get("_chunk", 0))
    return notes, warnings


# 【变量】笔记"分级压缩"阶梯：合并后若超长，逐级降档直到塞得下。
#   【为什么需要(2026-10-13 实测)】prompt 里"请控制在 N 字符内"只是**请求**，模型并不遵守 ——
#   实测 OPR 那份（表格几百行）的笔记合并后超过 90,000，被兜底钳制**从尾部切掉**，
#   等于把刚修好的"覆盖全文"又毁了。改成"结构化降档"后，丢失的是明细而非整段尾部。
#   降档顺序体现价值优先级：先舍"依据"（结论还在）→ 再限 findings → 最后限 numbers。
_NOTE_COMPACT_LADDER: tuple[dict, ...] = (
    {"drop_basis": False, "max_findings": None, "max_numbers": None},  # 0 全量
    {"drop_basis": True, "max_findings": None, "max_numbers": None},   # 1 舍依据
    {"drop_basis": True, "max_findings": 20, "max_numbers": 40},       # 2 限量
    {"drop_basis": True, "max_findings": 10, "max_numbers": 20},       # 3 再限
    {"drop_basis": True, "max_findings": 6, "max_numbers": 10},        # 4 极限
)


def _merge_notes(notes: list[dict], head_text: str = "", level: int = 0) -> str:
    """把各块笔记合并成一份"压缩稿"（Merge 步，纯文本、无 LLM 调用）。

    【为什么用纯文本而不是再让 LLM 汇总一遍】一是省一次调用与等待；二是**保真**——
      再汇总一遍等于又引入一次有损转换，数字可能被二次改写。这里只做机械合并与去重。
    【为什么附原文首部】封面元信息(标题/机构/期号/日期)最容易在压缩中丢失,
      原样附上 head_text（REDUCE_HEAD_CHARS 字符）让归纳路能直接读到, 不依赖笔记转述。
    【level】0=全量；>=1 按 _NOTE_COMPACT_LADDER 降档（舍依据 → 限条数）。
      由调用方逐级尝试直到塞进上下文预算，**绝不从尾部盲切**。
    【去重规则】numbers 按 (item, value, period) 去重; terms 按英文名去重;
      findings 按 point 前 24 字去重(表述近似的跨块重复只留一条)。
    """
    spec = _NOTE_COMPACT_LADDER[max(0, min(int(level), len(_NOTE_COMPACT_LADDER) - 1))]
    max_f = spec["max_findings"]
    max_n = spec["max_numbers"]

    parts: list[str] = []
    if head_text:
        parts.append(
            "【原文首部（原样摘录，用于识别标题/机构/期号/日期）】\n" + head_text
        )
    parts.append("【以下为原文分块笔记（按原文顺序，页码为该内容所在页）】")

    seen_f: set[str] = set()
    seen_n: set[str] = set()
    seen_t: set[str] = set()
    meta_acc: dict[str, str] = {}

    for n in notes:
        ci = n.get("_chunk", 0)
        pages = n.get("_pages") or []
        pg_hint = f"原文第 {pages[0]}-{pages[-1]} 页" if pages else ""
        parts.append(f"\n── 第 {ci + 1} 块 {pg_hint} ──")

        f_lines: list[str] = []
        for f in _as_list(n.get("findings")):
            f = _as_dict(f)
            pt = str(f.get("point") or "").strip()
            if not pt:
                continue
            key = re.sub(r"\s+", "", pt)[:24]
            if key in seen_f:
                continue
            seen_f.add(key)
            line = f"· {pt}"
            if not spec["drop_basis"]:
                basis = str(f.get("basis") or "").strip()
                if basis:
                    line += f" ｜依据：{basis}"
            page = f.get("page")
            if page not in (None, ""):
                line += f" ｜p.{page}"
            f_lines.append(line)
        if max_f is not None and len(f_lines) > max_f:
            f_lines = f_lines[:max_f]
        parts.extend(f_lines)

        nums = []
        for x in _as_list(n.get("numbers")):
            if max_n is not None and len(nums) >= max_n:
                break
            x = _as_dict(x)
            item = str(x.get("item") or "").strip()
            val = str(x.get("value") or "").strip()
            per = str(x.get("period") or "").strip()
            if not item or not val:
                continue
            k = f"{item}|{val}|{per}"
            if k in seen_n:
                continue
            seen_n.add(k)
            seg = f"{item}={val}"
            if per:
                seg += f"（{per}）"
            if x.get("page") not in (None, ""):
                seg += f" p.{x['page']}"
            nums.append(seg)
        if nums:
            parts.append("【本块数字】" + "；".join(nums))

        terms = []
        for t in _as_list(n.get("terms")):
            t = _as_dict(t)
            en = str(t.get("en") or "").strip()
            zh = str(t.get("zh") or "").strip()
            if not en or not zh or en.lower() in seen_t:
                continue
            seen_t.add(en.lower())
            terms.append(f"{en}={zh}")
        if terms:
            parts.append("【本块术语】" + "；".join(terms))

        m = _as_dict(n.get("meta"))
        for k in ("title", "org", "service", "issue", "pub_date", "website"):
            v = str(m.get(k) or "").strip()
            if v and not meta_acc.get(k):
                meta_acc[k] = v

    if meta_acc:
        parts.append("\n【封面元信息（从原文直接抄录，优先采用）】")
        for k, v in meta_acc.items():
            parts.append(f"{k}: {v}")

    return "\n".join(parts)


def _build_analysis_input(pages: list[dict], llm, on_map_progress=None) -> tuple[str, list[str], dict]:
    """决定"归纳阶段吃什么"：短文本原样、超长走分块笔记、兜底走加权抽样。

    【返回】(text, warnings, info)。info = {mode, chars, chunks, level, failed}
      mode: "full"(≤上限, 与改造前完全一致) | "map"(分层归纳) | "sample"(降级抽样)
    【决策树】
      ① 长度 ≤ MAX_ANALYZE_CHARS           → full：原样全文(零回归，不产生任何额外调用)
      ② 分块触顶(MAX_MAP_CHUNKS)           → sample：加权抽样(省掉注定不完整的 map 调用)
      ③ map 失败率 > 30%                   → sample：笔记残缺到无法代表全文，改抽样保底
      ④ 其余                                → map：分块笔记 → 合并成压缩稿
    【为什么失败率超阈值就降级】缺 1/8 块可以接受(告警点名即可)，缺 1/3 块的"压缩稿"
      会系统性偏向前几块，反而比抽样更失真 —— 抽样至少均匀覆盖全篇。
    【安全钳制】压缩稿理论上 ≤ 块数×NOTE_MAX_CHARS+首部, 但仍做一次兜底截断,
      保证任何情况下送进归纳的文本都不超过 MAX_ANALYZE_CHARS（避免又超上下文）。
    """
    # 【关键】full 路径必须复用 _join_text(传一个极大的 limit 关掉截断) —— 它会给每页插
    #   「［第 N 页］」标记, 与改造前逐字一致。若图省事用 _units_text, 页码标记会丢,
    #   归纳路就再也无法给出"结论出自第几页"(实测这是短文本路径的隐形回归)。
    full, _never = _join_text(pages, limit=1 << 30)
    warnings: list[str] = []

    if len(full) <= MAX_ANALYZE_CHARS:
        return full, warnings, {"mode": "full", "chars": len(full), "chunks": 0, "level": 0, "failed": 0}

    chunks, truncated = _chunk_for_notes(pages)
    if truncated:
        text, _ = _sample_text(pages)
        warnings.append(
            f"原文过长（超过 {MAX_MAP_CHUNKS} 块分块上限，约 {MAX_MAP_CHUNKS * MAP_CHUNK_CHARS:,} 字符），"
            f"已改用**加权抽样**覆盖全文（首页与尾部预测表优先，中段按比例抽取）；"
            "归纳依据为抽样片段而非全文。"
        )
        return text, warnings, {"mode": "sample", "chars": len(text), "chunks": 0, "level": 0, "failed": 0}

    notes, map_warnings = _map_notes(llm, chunks, on_done=on_map_progress)
    failed = len(map_warnings)
    if chunks and failed / len(chunks) > 0.3:
        text, _ = _sample_text(pages)
        warnings.extend(map_warnings)
        warnings.append(
            f"分块笔记失败 {failed}/{len(chunks)} 块（超过 30%），压缩稿已不足以代表全文，"
            "改用**加权抽样**覆盖全文；归纳依据为抽样片段。"
        )
        return text, warnings, {"mode": "sample", "chars": len(text), "chunks": len(chunks),
                                "level": 0, "failed": failed}

    warnings.extend(map_warnings)
    head = full[:REDUCE_HEAD_CHARS]

    # 【2026-10-13 实测修正】原先这里只渲染一次、超长就 `text[:MAX]` 盲切 —— 实测真实
    #   OPR 报告(表格几百行)的笔记合并后超过 90,000，盲切**正好又从尾部切掉**，
    #   把刚修好的"覆盖全文"毁掉。改为**逐级降档**：先舍"依据"、再限条数，
    #   丢的是明细粒度而不是整段尾部；降到最低档仍塞不下才退回抽样。
    text = ""
    used_level = 0
    for lvl in range(len(_NOTE_COMPACT_LADDER)):
        text = _merge_notes(notes, head_text=head, level=lvl)
        used_level = lvl
        if len(text) <= MAX_ANALYZE_CHARS:
            break

    if len(text) > MAX_ANALYZE_CHARS:
        text, _ = _sample_text(pages)
        warnings.append(
            "分块笔记压缩到最低档仍超出上下文预算，已改用**加权抽样**覆盖全文；"
            "归纳依据为抽样片段。"
        )
        return text, warnings, {"mode": "sample", "chars": len(text), "chunks": len(chunks),
                                "level": used_level, "failed": failed}

    if used_level:
        warnings.append(
            f"原文信息量很大，分块笔记已按第 {used_level} 级压缩"
            f"（{'省略了部分依据' if used_level == 1 else '精简了条目明细'}）；"
            "归纳覆盖全文，但细节颗粒度低于常规报告。"
        )
    return text, warnings, {"mode": "map", "chars": len(text), "chunks": len(chunks),
                            "level": used_level, "failed": failed, "notes": notes}


# ═══════════════════════════ 阶段 3：HTML 组装 ═══════════════════════════


def _cover_html(cover: dict, meta: dict) -> str:
    """封面：左右出血铺满内容区。

    ⚠ 上下**不能**用负 margin 追求四边出血——Chromium 按内容区高度分页，
      超出部分会被推到下一页（技能包实测凭空多出一页）。这里沿用 height:270mm + margin:0 -15mm。
    """
    headlines = _as_str_list(cover.get("headlines"))
    sub = "　·　".join(f"<b>{_rich(h)}</b>" for h in headlines[:3] if str(h).strip())
    # 日期用属性拼装而非 strftime 的 %-m/%#m —— 后者在 Windows/Linux 上语义不一致
    d = datetime.now()
    rows = []
    for k, v in (
        ("原文标题", cover.get("title_en") or meta.get("filename", "")),
        ("发行机构", cover.get("org") or "—"),
        ("所属服务", cover.get("service") or ""),
        ("发行日期", cover.get("pub_date") or "—"),
        ("期　　号", cover.get("issue") or f"共 {meta.get('n_pages', '?')} 页"),
        ("编译日期", f"{d.year} 年 {d.month} 月 {d.day} 日"),
    ):
        if str(v or "").strip():  # 原文没有的字段直接不占行，避免封面出现成排「—」
            rows.append((k, v))
    meta_html = "".join(f"<div><i>{k}</i>{_rich(v)}</div>" for k, v in rows)
    conf = _rich(cover.get("confidential") or "内部参考")
    site = _rich(cover.get("website") or "")
    return f"""
<section class="cover">
  <div class="brand">
    <span>原报告编译 · FOREIGN REPORT DIGEST</span>
    <span class="conf">{conf}</span>
  </div>
  <h1><em>中文编译 · 全文翻译与结论摘要</em>{_rich(cover.get('title_zh') or meta.get('filename', '外文研报'))}</h1>
  <div class="sub">{sub}</div>
  <div class="meta">{meta_html}</div>
  <div class="foot">
    <span>本件为原文中文编译稿，仅供内部研究参考，请勿外传</span>
    <span>{site}</span>
  </div>
</section>
"""


def _warn_banner_html(warnings: list[str]) -> str:
    """编译保真度提示（有翻译缺段/正文被截断时出现），放在封面后第一屏。"""
    if not warnings:
        return ""
    items = "".join(f"<li>{_rich(w)}</li>" for w in warnings)
    return f"""
<section class="pg">
  <h2 class="sec"><span class="no">!</span>编译保真度提示</h2>
  <div class="box warn">
    <div class="ch"><span class="n">注意</span><h3>本稿存在下列已知缺口，请知悉后再引用</h3></div>
    <ul>{items}</ul>
  </div>
</section>
"""


def _summary_html(summary: dict) -> str:
    """第 01 节：三栏摘要卡 + 每条结论的要点块 + 口径脚注。"""
    cards = [_as_dict(c) for c in _as_list(summary.get("cards"))][:3]
    while len(cards) < 3:  # 摘要卡固定三栏版式，缺项补空避免网格塌陷
        cards.append({"kk": "—", "kv": "—", "kd": ""})
    cards_html = "".join(
        # 【2026-10-14】`.kk` 也开 allow_br —— 模型常把"标题<br>限定"写在 kk 里，
        #   不开就原样显示成字面 `<br>`（实测截图上就是 `ADM：业绩超预期<br>指引或上调`）。
        f'<div class="k"><div class="kk">{_rich(c.get("kk") or "", allow_br=True)}</div>'
        f'<div class="kv">{_rich(c.get("kv") or "", allow_br=True)}</div>'
        f'<div class="kd">{_rich(c.get("kd") or "", allow_br=True)}</div></div>'
        for c in cards
    )

    blocks = []
    for i, c in enumerate(_as_list(summary.get("conclusions")), 1):
        c = _as_dict(c)
        no = _rich(c.get("no") or f"{i:02d}")
        pts = "".join(f"<li>{_rich(x)}</li>" for x in _as_str_list(c.get("points")))
        blocks.append(
            f'<div class="card key"><div class="ch"><span class="n">{no}</span>'
            f'<h3>{_rich(c.get("title") or "")}</h3></div><ul>{pts}</ul></div>'
        )

    return f"""
<section class="pg">
  <h2 class="sec"><span class="no">01</span>核心结论</h2>
  <p class="lead">{_rich(summary.get("lead") or "", allow_br=True)}</p>
  <div class="tldr">{cards_html}</div>
  {''.join(blocks)}
</section>
"""


def _appendix_html(gloss: list, notes: dict) -> str:
    """附录：术语两栏对照 + 编译说明。

    【为什么单独成节】技能包实测——把「缩略语对照（两栏 grid）+ 编译说明」放在收尾，
    可一次把尾页空白从 83% 压到 30%；反之尾页会留大片空白。
    【为什么不用 class="pg"】强制另起一页会让"全文翻译末尾孤立的图表页"留下 60%+ 空白
    （实测第 9 页 66% 空白）；跟随正文排版流则图表与术语对照自然同页收尾。
    """
    n = notes if isinstance(notes, dict) else {}
    g = "".join(
        f'<div><b>{_rich(x.get("en") or "")}</b><i>{_rich(x.get("zh") or "")}</i></div>'
        for x in gloss
        if isinstance(x, dict) and str(x.get("en", "")).strip()
    )
    gloss_html = f'<h3 class="t">附：缩略语与关键术语对照</h3><div class="gloss">{g}</div>' if g else ""
    if not any(str(n.get(k, "")).strip() for k in ("scope", "data", "copyright")):
        return f"<section>{gloss_html}</section>" if gloss_html else ""
    return f"""
<section>
  {gloss_html}
  <div class="box">
    <div class="ch"><span class="n">编译说明</span><h3>范围与免责</h3></div>
    <ul>
      <li><b>编译范围</b>：{_rich(n.get("scope") or "—")}</li>
      <li><b>数据口径</b>：{_rich(n.get("data") or "—")}</li>
      <li><b>版权与使用</b>：{_rich(n.get("copyright") or "—")}</li>
    </ul>
  </div>
</section>
"""


def _logic_html(logic: dict, flow: bool = False) -> str:
    """第 02 节：按主题拆「判断 → 支撑证据 → 推论」。

    【flow 参数】flow=True 时本节不再强制另起一页（见 _run_job 的两遍渲染兜底）。
    """
    topics_html = []
    for t in _as_list(logic.get("topics")):
        t = _as_dict(t)
        groups = []
        for g in _as_list(t.get("groups")):
            g = _as_dict(g)
            items = "".join(f"<li>{_rich(x)}</li>" for x in _as_str_list(g.get("items")))
            if not items:
                continue
            groups.append(f'<h4 class="st">{_rich(g.get("st") or "")}</h4><ul>{items}</ul>')
        if not groups:
            continue
        sub = _rich(t.get("sub") or "")
        topics_html.append(
            f'<h3 class="t">{_rich(t.get("name") or "")} <span>{sub}</span></h3>'
            f'<div class="card"><div class="ch"><span class="n">判断</span>'
            f'<h3>{_rich(t.get("judgement") or "")}</h3></div>{"".join(groups)}</div>'
        )
    if not topics_html:
        return ""
    return f"""
<section{' class="pg"' if not flow else ""}>
  <h2 class="sec"><span class="no">02</span>结论与逻辑支撑</h2>
  <p class="lead">{_rich(logic.get("lead") or "", allow_br=True)}</p>
  {''.join(topics_html)}
</section>
"""


def _data_html(data: dict, flow: bool = False) -> str:
    """第 03 节：分组三列表（跨页自动重复表头，see CSS thead{display:table-header-group}）。"""
    groups_html = []
    for g in _as_list(data.get("groups")):
        g = _as_dict(g)
        cols = _as_str_list(g.get("cols"))[:3]
        while len(cols) < 3:
            cols.append("")
        rows = []
        for r in _as_list(g.get("rows")):
            r = _as_dict(r)
            cells = _as_str_list(r.get("c"))[:3]
            while len(cells) < 3:
                cells.append("")
            cls = ' class="hi"' if r.get("hi") else ""
            rows.append(
                f"<tr{cls}><td class=\"k\">{_rich(cells[0])}</td>"
                f'<td class="n">{_rich(cells[1])}</td><td class="s">{_rich(cells[2])}</td></tr>'
            )
        if not rows:
            continue
        head = "".join(f'<th style="width:{"31%" if i == 0 else ("23%" if i == 1 else "")}">{_rich(c)}</th>'
                       for i, c in enumerate(cols))
        groups_html.append(
            f'<h4 class="st">{_rich(g.get("title") or "")}</h4>'
            f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )
    if not groups_html:
        return ""
    return f"""
<section{' class="pg"' if not flow else ""}>
  <h2 class="sec"><span class="no">03</span>关键数据速查</h2>
  <p class="lead">{_rich(data.get("lead") or "", allow_br=True)}</p>
  {''.join(groups_html)}
</section>
"""


def _insight_html(insight: dict, flow: bool = False) -> str:
    """第 04 节：内部关联解读（显式标注非原文内容）。"""
    items_html = []
    for it in _as_list(insight.get("items")):
        it = _as_dict(it)
        pts = "".join(f"<li>{_rich(x)}</li>" for x in _as_str_list(it.get("points")))
        if not pts:
            continue
        items_html.append(
            f'<h3 class="t">{_rich(it.get("title") or "")}</h3><div class="card"><ul>{pts}</ul></div>'
        )
    if not items_html:
        return ""
    return f"""
<section{' class="pg"' if not flow else ""}>
  <h2 class="sec"><span class="no">04</span>内部关联解读</h2>
  <div class="box warn">
    <div class="ch"><span class="n">提示</span><h3>本节为编译方补充，非原报告内容</h3></div>
    <p style="margin-bottom:0">{_rich(insight.get("intro") or "以下为编译方基于原文所作的延伸解读，供内部讨论参考。")}</p>
  </div>
  {''.join(items_html)}
  <div class="foot-note">{_rich(insight.get("foot") or "本节为编译方定性判断，不构成投资建议。")}</div>
</section>
"""


def _img_tag(img: dict) -> str:
    """内嵌图为 base64 data URI——避免 Playwright 相对路径解析问题。"""
    mime = "image/png" if img["mime"] in ("png", "PNG") else (
        "image/jpeg" if img["mime"].lower() in ("jpg", "jpeg") else "image/png"
    )
    b64 = base64.b64encode(img["data"]).decode("ascii")
    return (
        f'<div class="fig"><img src="data:{mime};base64,{b64}" alt="原文图表">'
        f'<div class="cap">图：原文第 {img["page"]} 页内嵌图表</div></div>'
    )


def _trans_html(chunks: list[dict], images: list[dict]) -> str:
    """第 05 节：全文翻译，按原文顺序；图表插回其所在页对应的分块之后。"""
    body = []
    img_by_page: dict[int, list[dict]] = {}
    for im in images:
        img_by_page.setdefault(int(im["page"]), []).append(im)

    for i, c in enumerate(chunks):
        out = c.get("out") or ""
        lines = [x.strip() for x in out.split("\n")]
        buf: list[str] = []
        for ln in lines:
            if not ln:
                continue
            m = re.match(r"^(#{2,4})\s*(.+)$", ln)
            if m:
                if buf:
                    body.append(f"<p>{_rich(' '.join(buf))}</p>")
                    buf = []
                body.append(f'<h4 class="st">{_rich(m.group(2))}</h4>')
            elif ln.startswith(("---", "***", "___")):
                continue
            else:
                buf.append(ln)
        if buf:
            body.append(f"<p>{_rich(' '.join(buf))}</p>")
        for pno in c.get("pages") or []:
            for im in img_by_page.pop(pno, []):
                body.append(_img_tag(im))

    # 抽不出所在页归属的图（如页脚图/整页图）统一收在末尾
    leftover = [im for lst in img_by_page.values() for im in lst]
    if leftover:
        body.append('<h4 class="st">附：原文其余图表</h4>')
        body.extend(_img_tag(im) for im in leftover)

    failed = [c["idx"] + 1 for c in chunks if not (c.get("out") or "").strip()]
    fail_note = ""
    if failed:
        fail_note = (
            f'<div class="box warn"><div class="ch"><span class="n">注意</span>'
            f"<h3>以下段落翻译失败，已在原位留标记</h3></div><ul><li>"
            + "、".join(f"第 {n} 段" for n in failed)
            + "</li></ul></div>"
        )

    return f"""
<section class="pg">
  <h2 class="sec"><span class="no">05</span>全文翻译</h2>
  <p class="lead">以下为原报告正文的完整中文翻译，按原文顺序分段，未作删节。
    方括号内为译者补注，用于说明缩写或单位。</p>
  {fail_note}
  {''.join(body)}
</section>
"""


def _assemble_html(parts: dict, meta: dict) -> str:
    """把所有节拼成完整 HTML 文档（样式见 assets/foreign_report_pdf.css）。

    【2026-10-14 横版改版】顶部色块（`.cover`）之后的**第一个** `.pg` 会打上 `run-in`
    类 —— CSS 里 `.pg` 是 `break-before:page`（每节另起一页），若不管它，第一页就只剩
    色块、下半页全空，等于又把封面做成了整页（与"封面不要整个一页"的需求相反）。
    """
    body = "".join(
        parts[k] for k in ("warn", "cover", "summary", "logic", "data", "insight", "trans", "appendix")
    )
    i = body.find('class="cover"')
    if i >= 0:
        marker = '<section class="pg">'
        j = body.find(marker, i)
        if j >= 0:
            body = body[:j] + '<section class="pg run-in">' + body[j + len(marker):]
    title = _html.escape(f"{parts['title_zh']} · 中文编译")
    desc = _html.escape(f"{parts['org']} 研报中文编译：全文翻译 + 核心结论 + 逻辑支撑 + 关键数据")
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{title}</title>"
        '<meta name="author" content="AgentSense 外文研报编译">'
        f'<meta name="description" content="{desc}">'
        f"<style>{_load_css()}</style>"
        "</head><body>" + body + "</body></html>"
    )


# ═══════════════════════════ 阶段 4：渲染 PDF ═══════════════════════════


def _render_pdf(html_str: str, out_pdf: Path, footer_left: str, marks: list[tuple]) -> dict:
    """用 Playwright Chromium 把编译稿 HTML 打成**横版 PPT(16:9)** PDF，再用 pymupdf 补书签。

    【版式】2026-10-14 由 A4 纵版单列改为 **16:9 横版 + 两列**
      （对齐用户提供的汇报版式；PPT 标准 13.333in×7.5in = 338.67mm×190.5mm）。
    【关键逻辑】`page.pdf(outline=True)` 实测**不生成书签**，必须用 pymupdf 的 set_toc
    二次写入（技能包踩坑 #5）；页码靠 needle 逐页搜索定位，封面字距问题直接写死 p1。
    【返回】{"pages":int, "bookmarks":int, "footer":str}
    """
    from playwright.sync_api import sync_playwright

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    html_path = out_pdf.parent / "_compile.html"
    html_path.write_text(html_str, encoding="utf-8")

    # 页脚左右内边距要与 PDF 的左右 margin 一致（否则页码会贴边或错位）
    footer = (
        '<div style="width:100%;font-size:7pt;color:#6f7788;padding:0 10mm;'
        'display:flex;justify-content:space-between;font-family:\'Microsoft YaHei\',sans-serif;">'
        f"<span>{_html.escape(footer_left)}</span>"
        '<span>第 <span class="pageNumber"></span> / <span class="totalPages"></span> 页</span></div>'
    )

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(html_path.as_uri(), wait_until="networkidle")
            page.wait_for_timeout(400)
            page.pdf(
                path=str(out_pdf),
                width=PPT_W_MM,
                height=PPT_H_MM,
                print_background=True,
                outline=True,
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=footer,
                margin={"top": "7mm", "bottom": "11mm", "left": "10mm", "right": "10mm"},
            )
        finally:
            browser.close()

    import pymupdf

    doc = pymupdf.open(str(out_pdf))
    toc: list[list] = []
    for lvl, title, needle in marks:
        if needle is None:
            toc.append([lvl, title, 1])
            continue
        for i, pg in enumerate(doc):
            try:
                hit = needle in (pg.get_text() or "")
            except Exception:
                hit = False
            if hit:
                toc.append([lvl, title, i + 1])
                break
    n_pages = len(doc)
    if toc:
        doc.set_toc(toc)
        doc.saveIncr()
    doc.close()
    return {"pages": n_pages, "bookmarks": len(toc)}


def _blank_page_audit(out_pdf: Path) -> list[int]:
    """自检：统计每页**底部留白**占比（排除页眉/页脚带）。

    【返回】底部留白 > 55% 的页码列表（健康区间平均 ≤15%，只在真正异常时提示）。

    ★ 扫描范围按**页高比例**动态算，绝不写死像素 ——
      2026-10-14 版式从 A4 纵版（842pt 高）改成横版 PPT（190.5mm ≈ 540pt）后，
      原先写死的 `y∈[34,795]` 会整体越界：`range(34, min(795, h))` 在 h=540 时
      仍能跑，但分母 `(min(795,h)-34)` 与页脚带位置全错 ⇒ 判定失真。
      更极端的情形是页高小于 34（扫描为空）⇒ `last` 恒 0 ⇒ **所有页判为"不空白"**，
      自检静默失效。所以必须用比例。
    """
    try:
        import pymupdf

        doc = pymupdf.open(str(out_pdf))
        bad = []
        for i, pg in enumerate(doc, 1):
            pm = pg.get_pixmap(dpi=72)
            h, w, n, s = pm.height, pm.width, pm.n, pm.samples
            top = max(4, int(h * 0.045))        # 页眉带 ≈ 页高 4.5%
            bot = min(h - 1, int(h * 0.960))    # 页脚带 ≈ 页高 4%
            if bot <= top:
                continue
            last = 0
            for y in range(top, bot):
                if any(s[y * w * n + k] < 246 for k in range(0, w * n, n * 5)):
                    last = y
            if last and round((bot - last) * 100 / (bot - top)) > 55:
                bad.append(i)
        doc.close()
        return bad
    except Exception:
        return []


# ═══════════════════════════ 任务状态机 ═══════════════════════════

_JOB_LOCK = threading.Lock()  # 【变量】任务表读写锁(Flask 多线程 + 采集线程并发)
_JOBS: dict[str, dict] = {}  # 【变量】内存任务表 {job_id: job}


def _job_path(jid: str) -> Path:
    return JOBS_DIR / f"{jid}.json"


def _save_job(job: dict) -> None:
    """任务状态落盘（重启后页面仍能看到历史任务与产物路径）。"""
    try:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        _job_path(job["id"]).write_text(
            json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception:
        pass


def _log(job: dict, message: str, stage: str | None = None, progress: int | None = None) -> None:
    """记录一行阶段日志 + 推进进度，并立即落盘（前端轮询据此渲染）。"""
    with _JOB_LOCK:
        if stage:
            job["stage"] = stage
            job["stage_label"] = STAGE_LABELS.get(stage, stage)
        if progress is not None:
            # 【2026-10-14】进度必须**单调不减**：翻译与归纳现在并行推进，两边各自
            #   上报自己的百分比（27~88 与 12~26），直接赋值会让进度条来回跳。
            job["progress"] = max(int(job.get("progress") or 0), max(0, min(100, int(progress))))
        job["message"] = message
        job["log"].append(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
        job["log"] = job["log"][-60:]
        _save_job(job)


def get_job(jid: str) -> dict | None:
    """取单任务状态：内存优先，未命中则从盘上读（服务重启后的历史任务）。"""
    with _JOB_LOCK:
        if jid in _JOBS:
            return dict(_JOBS[jid])
    p = _job_path(jid)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def list_jobs(limit: int = 30) -> list[dict]:
    """列出近期任务（时间倒序），供页面历史列表展示。

    【返回字段与详情接口 /api/foreign/job/<id> 对齐】尤其是 has_pdf / out_name:
    前端靠 has_pdf 判断"编译稿是否已生成", 决定文章名是否做成可点链接。
    两条接口必须给同一套字段, 否则会出现"详情页能下载、列表里却没链接"的静默错位。
    【安全】不下发 out_pdf(服务器绝对路径)。
    """
    jobs: list[dict] = []
    with _JOB_LOCK:
        jobs.extend(dict(j) for j in _JOBS.values())
    seen = {j["id"] for j in jobs}
    if JOBS_DIR.exists():
        for p in sorted(JOBS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            jid = p.stem
            if jid in seen:
                continue
            try:
                jobs.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
            if len(jobs) > limit * 3:
                break
    jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)

    out = []
    for j in jobs[:limit]:
        item = {k: j.get(k) for k in (
            "id", "filename", "title", "org", "created_at", "finished_at",
            "status", "stage", "stage_label", "progress", "message", "error",
            "pages", "n_pages", "chars",
        )}
        item["n_pages"] = j.get("pages") or j.get("n_pages")
        # 【2026-09-30 修复】列表也必须带 has_pdf / out_name —— 它们是「编译产物是否
        #   存在」的唯一判据, 前端据此决定文章名能不能点、要不要给预览/下载链接。
        #   ★ 此前列表只回 out_pdf(服务器绝对路径) 而**不回 has_pdf**, 于是前端
        #     `if (j.has_pdf)` 恒为 falsy → 历史列表永远没有链接; 详情接口却因为有
        #     has_pdf 而正常。两条接口字段不一致 = 极难排查的静默失效。
        item["has_pdf"] = bool(j.get("out_pdf") and Path(j["out_pdf"]).exists())
        item["out_name"] = j.get("out_name") or ""
        # 【安全】out_pdf 是服务器绝对路径, 不下发前端(与 _foreign_public 的口径一致)。
        out.append(item)
    return out


def delete_job(jid: str) -> bool:
    """删除任务：内存记录 + 状态 JSON + 产物目录 + 上传的原始 PDF。

    【安全边界】原始 PDF 只在该路径确实位于 UPLOAD_DIR 之下时才删，
    避免 job JSON 被外部篡改后变成任意路径删除。
    """
    job = get_job(jid) or {}
    with _JOB_LOCK:
        _JOBS.pop(jid, None)
    ok = False
    p = _job_path(jid)
    if p.exists():
        with _suppress():
            p.unlink()
        ok = True
    d = OUT_DIR / jid
    if d.exists():
        with _suppress():
            shutil.rmtree(d, ignore_errors=True)
        ok = True
    src = job.get("file_path")
    if src:
        try:
            sp = Path(src).resolve()
            if sp.parent == UPLOAD_DIR.resolve() and sp.exists():
                sp.unlink()
                ok = True
        except Exception:
            pass
    return ok


class _suppress:
    """极简 contextlib.suppress 替身（避免为一个用途引入额外 import 语义）。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


# ═══════════════════════════ 主流程 ═══════════════════════════


def start_job(pdf_path: Path, original_name: str, options: dict) -> str:
    """登记一个编译任务并起后台线程，立即返回 job_id（前端拿它轮询进度）。"""
    jid = uuid.uuid4().hex[:12]
    job = {
        "id": jid,
        "filename": original_name,
        "file_path": str(pdf_path),
        "title": "",
        "org": "",
        "created_at": _now(),
        "started_at": "",
        "finished_at": "",
        "status": "running",
        "stage": "queued",
        "stage_label": STAGE_LABELS["queued"],
        "progress": 0,
        "message": "任务已入队",
        "error": "",
        "log": [],
        "out_pdf": "",
        "out_name": "",
        "pages": 0,
        "chars": 0,
        "chunks": 0,
        "options": options or {},
    }
    with _JOB_LOCK:
        _JOBS[jid] = job
        _save_job(job)
    threading.Thread(target=_run_job, args=(jid,), daemon=True, name=f"foreign-{jid}").start()
    return jid


def _whitelist_text() -> tuple[str, list[str]]:
    """取本平台品种白名单，拼成提示词用的「代码（中文名）」清单。"""
    try:
        from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES, VARIETY_METADATA

        order = [
            "SC", "TA", "PX", "EG", "FU", "LU", "PG", "M", "CF", "CJ",
            "LC", "RU", "NR", "BR", "LH", "BU", "BZ", "EB", "SI", "PS", "P",
        ]
        codes = [c for c in order if c in ACTIVE_VARIETIES]
        codes += [c for c in sorted(ACTIVE_VARIETIES) if c not in codes]
        lines = []
        for c in codes:
            name = (VARIETY_METADATA.get(c) or {}).get("name") or ""
            # 平台内部代码 EG 的对外标准名是 MEG（乙二醇），提示词里按对外口径给
            shown = "MEG" if c == "EG" else c
            lines.append(f"· {shown}（{name}）" if name else f"· {shown}")
        return "\n".join(lines), codes
    except Exception:
        return "· 原油、PTA、对二甲苯、乙二醇、燃料油、低硫燃料油、液化石油气、豆粕、棉花、红枣、碳酸锂、橡胶、20 号胶、丁二烯橡胶、生猪、沥青、苯乙烯、工业硅、多晶硅", []


def _run_job(jid: str) -> None:
    """后台任务主体：提取 → 四路归纳 → 分块翻译 → 排版 → 出 PDF。

    【错误处理】任何未捕获异常都会把任务置为 error 并把原因写进 job["error"]，
    **绝不静默失败**——前端会直接把它显示成红色失败卡。
    """
    with _JOB_LOCK:
        job = _JOBS.get(jid)
    if job is None:
        return

    job["started_at"] = _now()
    warnings: list[str] = []
    try:
        pdf_path = Path(job["file_path"])
        out_dir = OUT_DIR / jid
        out_dir.mkdir(parents=True, exist_ok=True)
        options = job.get("options") or {}
        include_images = bool(options.get("include_images", True))
        include_insight = bool(options.get("include_insight", True))

        # ── 阶段 1：提取 ────────────────────────────────
        _log(job, "正在解析原文 PDF（文字层 + 内嵌图表）…", "extract", 3)
        data = extract_pdf(pdf_path, out_dir / "img", include_images=include_images)
        job["n_pages"] = data["n_pages"]
        job["chars"] = data["chars"]
        n_img = len(data["images"])
        _log(job, f"解析完成：{data['n_pages']} 页、{data['chars']:,} 字符、{n_img} 张内嵌图", "extract", 8)

        # 【2026-10-13 移除】原先此处用 _join_text 截尾并告警"归纳只覆盖前 90,000 字符"。
        #   现改由 _build_analysis_input 统一决策(短文本原样 / 超长分块笔记 / 兜底抽样),
        #   告警文案也随之由它按实际走的路径如实生成 —— 留着旧文案会与新行为矛盾。
        #   （_join_text 仍保留给其它调用点/兼容, 见其定义处）

        # ── 阶段 2/3 **并行**：全文翻译 与 通读归纳 同时进行 ─────────────
        # 【2026-10-14 提速】翻译只依赖**原文分块**，与归纳结果毫无依赖关系，改造前却
        #   是串行（先 4 路归纳、再分块翻译）⇒ 归纳那 13s~120s+ 完全白等。
        #   这里把翻译提前到归纳之前启动，两段耗时取 max 而非 sum。
        #   ⚠️ 并行后总并发会超过账户配额，靠 `_LLM_SEM` 全局闸兜住（见其注释）。
        llm = _make_llm()

        chunks = _chunk_pages(data["pages"])
        job["chunks"] = len(chunks)
        _log(job, f"开始全文翻译：共 {len(chunks)} 段，{TRANSLATE_WORKERS} 路并行"
                  f"（与通读归纳同时进行）…", "translate", 27)

        done_n = 0
        lock = threading.Lock()

        def _do_translate() -> None:
            """分块并行翻译（在独立线程里跑，与主线程的归纳并行）。"""
            nonlocal done_n
            with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as pool:
                futs = {pool.submit(_translate_chunk, llm, c["text"], i, len(chunks)): i
                        for i, c in enumerate(chunks)}
                for fut in as_completed(futs):
                    i = futs[fut]
                    try:
                        out = fut.result()
                    except Exception as e:  # noqa: BLE001
                        out = ""
                        warnings.append(
                            f"全文翻译的第 {i + 1} 段（原文第 {chunks[i]['pages']} 页）"
                            f"翻译失败：{str(e)[:120]}")
                    chunks[i]["idx"] = i
                    chunks[i]["out"] = out
                    with lock:
                        done_n += 1
                        pct = 27 + int(61 * done_n / max(1, len(chunks)))
                    _log(job, f"翻译进度 {done_n}/{len(chunks)} 段", "translate", pct)

        _tr_pool = ThreadPoolExecutor(max_workers=1)
        _tr_fut = _tr_pool.submit(_do_translate)

        _log(job, "正在通读全文并归纳结论、逻辑、数据、内部解读（4 路并行）…", "analyze", 12)

        # 【2026-10-13】超长正文改走"分块笔记 → 四路归纳共享压缩稿"。
        #   为什么不在归纳里直接四路各切一块：那样原文要被送 4 遍, 调用 4N 次;
        #   原文只过一遍(Map)再共享压缩稿, 总输入反而更低, 且覆盖全文而非前 34%。
        #   短文本走 full 路径, 与原行为逐字段一致(不产生任何额外调用)。
        def _on_map_progress(done, total):
            _log(job, f"分块笔记 {done}/{total} 块", "analyze", 12 + int(8 * done / max(1, total)))

        analysis_text, in_warns, in_info = _build_analysis_input(
            data["pages"], llm, on_map_progress=_on_map_progress
        )
        warnings.extend(in_warns)
        job["analyze_mode"] = in_info.get("mode")
        if in_info.get("mode") == "map":
            # 【审计】把分块笔记与压缩稿落盘到 out/<jid>/，便于事后核对"归纳到底看了什么"。
            #   写失败不影响主流程(审计产物是辅助信息, 不该让它中断出稿)。
            try:
                (out_dir / "analysis_notes.json").write_text(
                    json.dumps(
                        {"chunks": in_info["chunks"], "failed": in_info["failed"],
                         "notes_chars": in_info["chars"], "notes": in_info.get("notes") or []},
                        ensure_ascii=False, indent=1,
                    ),
                    encoding="utf-8",
                )
                (out_dir / "analysis_input.txt").write_text(analysis_text, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            _log(
                job,
                f"原文超长，已分 {in_info['chunks']} 块压缩为摘要"
                f"（{in_info['chars']:,} 字符），四路归纳将基于全文摘要进行…",
                "analyze", 20,
            )
        elif in_info.get("mode") == "sample":
            _log(job, "原文超长且分块受限，已改用加权抽样覆盖全文…", "analyze", 20)

        wl_text, _codes = _whitelist_text()
        insight_prompt = PROMPT_INSIGHT.replace("{WHITELIST}", wl_text)

        tasks = {
            "meta": (PROMPT_META, analysis_text, "封面与核心结论"),
            "logic": (PROMPT_LOGIC, analysis_text, "结论与逻辑支撑"),
            "data": (PROMPT_DATA, analysis_text, "关键数据速查"),
        }
        if include_insight:
            tasks["insight"] = (insight_prompt, analysis_text, "内部关联解读")

        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futs = {
                pool.submit(_ask_json, llm, prompt + "\n\n【原文正文】\n" + text, label): key
                for key, (prompt, text, label) in tasks.items()
            }
            for fut in as_completed(futs):
                key = futs[fut]
                try:
                    results[key] = fut.result()
                    _log(job, f"归纳完成：{tasks[key][2]}", "analyze", None)
                except Exception as e:
                    # 单路失败降级：核心结论是硬需求，其余允许缺省（页面会提示）
                    if key == "meta":
                        raise
                    warnings.append(f"「{tasks[key][2]}」归纳失败（{str(e)[:120]}），本节在编译稿中缺省。")
                    _log(job, f"归纳失败：{tasks[key][2]} → {str(e)[:100]}", "analyze", None)

        # 【结构归一化】LLM 偶发把某节回成字符串/列表而非对象，这里统一压回正确类型；
        # 不做这一步，一个字段变形就会让整个任务在排版阶段崩掉（实测发生过）。
        _meta = _as_dict(results.get("meta"))
        cover = _as_dict(_meta.get("cover"))
        summary = _as_dict(_meta.get("summary"))
        gloss = [_as_dict(g) for g in _as_list(_meta.get("gloss"))]
        notes = _as_dict(_meta.get("notes"))
        logic = _as_dict(_as_dict(results.get("logic")).get("logic"))
        data_sec = _as_dict(_as_dict(results.get("data")).get("data"))
        insight = _as_dict(_as_dict(results.get("insight")).get("insight"))

        job["title"] = str(cover.get("title_zh") or "").strip()
        job["org"] = str(cover.get("org") or "").strip()
        _log(job, f"通读完成：《{job['title'] or job['filename']}》（{job['org'] or '机构未识别'}）", "analyze", 26)

        # ── 阶段 3 收尾：等并行翻译跑完 ─────────────────────────
        try:
            _tr_fut.result()
        except Exception as e:  # noqa: BLE001
            warnings.append(f"全文翻译整体异常：{str(e)[:160]}")
        finally:
            _tr_pool.shutdown(wait=False)
        chunks.sort(key=lambda c: c["idx"])
        n_fail = sum(1 for c in chunks if not (c.get("out") or "").strip())
        _log(job, f"翻译完成：{len(chunks) - n_fail}/{len(chunks)} 段成功", "translate", 88)

        # ── 阶段 4：排版与出图 ───────────────────────────
        _log(job, "正在按编译稿版式排版…", "compose", 90)
        date_s = datetime.now().strftime("%Y-%m-%d")
        title_zh = str(cover.get("title_zh") or "").strip() or _safe_name(pdf_path.stem, "外文研报")
        org = str(cover.get("org") or "").strip() or "外文机构"
        out_name = f"{date_s}_{_safe_name(org, '机构', 20)}_{_safe_name(title_zh, '研报', 40)}_中文编译.pdf"

        def build_html(flow: bool) -> str:
            """按"章节是否另起一页"组装完整 HTML（flow=True 时 02/03/04 节跟随上文续排）。"""
            return _assemble_html({
                "title_zh": title_zh,
                "org": org,
                "warn": _warn_banner_html(warnings),
                "cover": _cover_html(cover, {"filename": pdf_path.name, "n_pages": data["n_pages"]}),
                "summary": _summary_html(summary),
                "logic": _logic_html(logic, flow),
                "data": _data_html(data_sec, flow),
                "insight": _insight_html(insight, flow) if include_insight else "",
                "trans": _trans_html(chunks, data["images"]),
                "appendix": _appendix_html(gloss, notes),
            }, {"n_pages": data["n_pages"]})

        # 书签按"实际存在的节"动态生成——缺省节不占位，避免 pymupdf 静默丢书签。
        # needle 一律用完整标题片段（技能包踩坑 #5：太短会误匹配到别的页）。
        marks: list[tuple] = [(1, "封面", None)]
        if warnings:
            marks.append((1, "编译保真度提示", "编译保真度提示"))
        marks.append((1, "01　核心结论", "核心结论"))
        if logic.get("topics"):
            marks.append((1, "02　结论与逻辑支撑", "结论与逻辑支撑"))
            for t in logic["topics"]:
                nm = str(t.get("name") or "").strip()
                short = nm.split("　")[-1].strip() if "　" in nm else nm
                if len(short) >= 4:
                    marks.append((2, nm, short[:28]))
        if data_sec.get("groups"):
            marks.append((1, "03　关键数据速查", "关键数据速查"))
        if include_insight and insight.get("items"):
            marks.append((1, "04　内部关联解读", "内部关联解读"))
        marks.append((1, "05　全文翻译", "以下为原报告正文的完整中文翻译"))
        if gloss:
            marks.append((1, "附　术语对照与编译说明", "缩略语与关键术语对照"))

        out_pdf = out_dir / out_name
        footer_left = f"原报告《{title_zh}》· {org} · 中文编译 · 仅供内部参考"

        # 【两遍渲染兜底】第 1 遍按技能包默认（顶层章节各自另起一页）。
        # 短报告容易出现"上一节只溢出一两行 + 下一节强制换页"的大面积空白页
        # （实测 4 页原文 → 第 4 页 90% 空白）。此时第 2 遍让 02/03/04 节改为续排再渲染，
        # 两次自检取"异常空白页更少"的那一版——只多花一次 ~2s 的渲染，仅在需要时触发。
        _log(job, "正在渲染横版 PDF（16:9 两列）并写入书签…", "render", 93)
        info = _render_pdf(build_html(False), out_pdf, footer_left, marks)
        # 【2026-10-14】把第 1 版的字节留一份：若第 2 版并不更好就要回退，
        #   而**回退靠还原字节、不再重渲一遍**（旧逻辑在这里又渲染了一次与第 1 版逐字节
        #   相同的 PDF —— 单次渲染实测 6~21s，纯浪费）。
        _first_pdf_bytes = out_pdf.read_bytes() if out_pdf.exists() else b""
        bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]
        if bad:
            _log(job, f"检测到 {len(bad)} 页大面积空白（第 {'、'.join(map(str, bad[:6]))} 页），尝试改用续排版式…",
                 "render", None)
            try:
                info2 = _render_pdf(build_html(True), out_pdf, footer_left, marks)
                bad2 = [p for p in _blank_page_audit(out_pdf) if p < info2["pages"]]
                if len(bad2) < len(bad):
                    info, bad = info2, bad2
                    _log(job, f"续排版式更优：异常空白页 {len(bad)} 页", "render", None)
                else:
                    # 回滚到第 1 版：**还原字节**（不再重渲，省一次 6~21s 的渲染）
                    if _first_pdf_bytes:
                        out_pdf.write_bytes(_first_pdf_bytes)
                        bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]
                    else:  # 极端兜底：第 1 版字节没拿到才重渲
                        info = _render_pdf(build_html(False), out_pdf, footer_left, marks)
                        bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]
            except Exception as e:
                _log(job, f"续排尝试失败，保留默认版式：{str(e)[:120]}", "render", None)
                if _first_pdf_bytes:
                    out_pdf.write_bytes(_first_pdf_bytes)
                    bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]
                else:
                    info = _render_pdf(build_html(False), out_pdf, footer_left, marks)
                    bad = [p for p in _blank_page_audit(out_pdf) if p < info["pages"]]
        job["pages"] = info["pages"]
        _log(job, f"PDF 已生成：{info['pages']} 页、{info['bookmarks']} 条书签", "render", 97)

        if bad:
            warnings.append("以下页面底部留白偏大（超过 55%），建议人工复核排版：第 "
                            + "、".join(str(b) for b in bad[:12]) + " 页。")

        with _JOB_LOCK:
            job["status"] = "done"
            job["stage"] = "done"
            job["stage_label"] = STAGE_LABELS["done"]
            job["progress"] = 100
            job["out_pdf"] = str(out_pdf)
            job["out_name"] = out_name
            job["finished_at"] = _now()
            job["warnings"] = warnings
            job["message"] = (
                f"编译完成：{info['pages']} 页 · 原文 {data['n_pages']} 页 · "
                f"{info['bookmarks']} 条书签" + (f" · {len(warnings)} 条提示" if warnings else "")
            )
            job["log"].append(f"{datetime.now().strftime('%H:%M:%S')}  {job['message']}")
            _save_job(job)

    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        with _JOB_LOCK:
            job["status"] = "error"
            job["stage"] = "error"
            job["stage_label"] = STAGE_LABELS["error"]
            job["error"] = detail
            job["finished_at"] = _now()
            job["warnings"] = warnings
            job["message"] = f"编译失败：{str(e)[:200]}"
            job["log"].append(f"{datetime.now().strftime('%H:%M:%S')}  {detail}")
            job["log"].append(traceback.format_exc()[-1500:])
            _save_job(job)
