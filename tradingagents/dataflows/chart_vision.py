"""图表视觉重述(2026-09-08 方案三补充):位图图表 → 视觉模型 → 可检索文字。

【为什么】Phase A(extract_layout_text)只覆盖矢量图表;约 32/76 份研报的图表是
整块位图,轴数字在像素里,文本提取拿不到。本模块把位图区域渲染成 PNG 交给视觉
模型重述成 2~3 句中文(主题/趋势/明确可读数值,读不清禁编造),追加到 layout_text
的「图表视觉重述」节,RAG 切块后可被检索。

【视觉后端】env 可插拔,默认本地 Ollama(零成本零开通):
  RAG_VISION_BACKEND=ollama   RAG_VISION_MODEL=qwen3-vl:4b(默认)
  RAG_VISION_BACKEND=ark      RAG_VISION_MODEL=doubao-seed-1-6-flash-250828
                              (需方舟控制台开通;openai 兼容 /api/v3)

【两个入口】
  - scripts/chart_describe.py:存量批量回填(CLI,幂等跳过已重述)。
  - describe_for_hook():新上传研报的自动钩子(web_app._process_research_report
    调用)—— 有图表数上限、后端不可达直接返回空,绝不拖垮研报主流程。
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SECTION_MARK = "图表视觉重述"  # layout_text 中重述节的判定标记(幂等跳过判断用)
# 钩子图表数上限:本地视觉 ~20s/张,上传后台线程要等它做完才转 done;
# 超限只重述前 N 张(幻灯片型研报 170 页全部重述要 1 小时,不可接受)
HOOK_MAX_CHARTS = int(os.environ.get("RAG_VISION_MAX_CHARTS", "8") or 8)
_DESCRIBE_PROMPT = (
    "这是期货研报中的一张图表。请用2-3句中文描述:"
    "①图表主题(轴标签/图例);②整体趋势与关键转折;③图中明确可读的关键数值。"
    "读不清的数值写「约」或区间,严禁编造。直接输出描述文字,不要标题。"
)


def _describe_ollama(png: bytes, model: str) -> str:
    import json
    import urllib.request

    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    body = {
        "model": model,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": _DESCRIBE_PROMPT,
            "images": [base64.b64encode(png).decode()],
        }],
    }
    req = urllib.request.Request(
        f"{base}/api/chat", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        d = json.loads(resp.read().decode("utf-8"))
    return (d.get("message", {}).get("content") or "").strip()


def _describe_ark(png: bytes, model: str) -> str:
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ.get("ARK_API_KEY", ""),
        base_url=os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
    )
    d = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(png).decode()}"}},
                {"type": "text", "text": _DESCRIBE_PROMPT},
            ],
        }],
    )
    return (d.choices[0].message.content or "").strip()


def describe_png(png: bytes) -> str:
    """按 env 选择后端重述单张图表 PNG;失败抛异常(调用方决定跳过/记录)。"""
    backend = os.environ.get("RAG_VISION_BACKEND", "ollama").lower()
    model = os.environ.get("RAG_VISION_MODEL", "qwen3-vl:4b")
    if backend == "ark":
        return _describe_ark(png, model)
    if backend != "ollama":
        raise ValueError(f"未知视觉后端: {backend}(支持 ollama/ark)")
    return _describe_ollama(png, model)


def ollama_available(timeout: float = 2.0) -> bool:
    """本地 Ollama 是否可达(钩子前置检查:不可达直接跳过,不让上传等 300s 超时)。"""
    import urllib.request

    try:
        base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        urllib.request.urlopen(f"{base}/api/tags", timeout=timeout).read()
        return True
    except Exception:
        return False


def build_layout_with_vision(
    path: Path,
    max_charts: int = 0,
    empty_marker: bool = True,
) -> str | None:
    """PDF → 版面提取 + 位图图表视觉重述,返回完整 layout_text。

    【参数】max_charts: 重述图表数上限(0=不限;钩子用 HOOK_MAX_CHARTS);
           empty_marker: 无可用重述时是否落"(图表视觉重述:无可用图表)"标记
           (批量脚本 True=幂等不再重跑;上传钩子 False=失败留白下次可补)。
    【返回】str:composed layout_text;无位图/无可用重述且 empty_marker=False 时 None。
    """
    from tradingagents.dataflows.pdf_layout import (
        bitmap_chart_regions,
        extract_layout_text,
        render_region_png,
    )

    regions = bitmap_chart_regions(path)
    if not regions:
        return None
    if max_charts and len(regions) > max_charts:
        regions = regions[:max_charts]
    descs: list[str] = []
    for rg in regions:
        png = render_region_png(path, rg["page"], rg["bbox"])
        if not png:
            continue
        try:
            text = describe_png(png)
        except Exception:
            logger.warning("图表重述失败 %s p%s", path, rg["page"], exc_info=True)
            continue
        if text:
            descs.append(text)
    layout = extract_layout_text(path)
    if descs:
        layout = layout.rstrip() + "\n\n" + "\n\n".join(f"【图】{d}" for d in descs)
        layout += f"\n\n(以上{len(descs)}条为{SECTION_MARK},由视觉模型基于图表位图生成,数值以原文为准)"
    elif empty_marker:
        layout += f"\n\n({SECTION_MARK}:无可用图表)"
    else:
        return None
    return layout


def describe_for_hook(path: Path) -> str:
    """web_app 上传钩子入口:后端可达且 PDF 有位图时返回重述版 layout_text,否则空串。

    【关键逻辑】绝不抛出、绝不长阻塞:Ollama 不可达 2s 内返回;图表数限
    HOOK_MAX_CHARTS;单图失败跳过。返回空串表示"本次没做重述"(layout_text
    保持纯版面提取结果,下次跑 scripts/chart_describe.py 可补)。
    """
    try:
        backend = os.environ.get("RAG_VISION_BACKEND", "ollama").lower()
        if backend == "ollama" and not ollama_available():
            logger.info("Ollama 不可达,跳过图表视觉重述 %s", path)
            return ""
        layout = build_layout_with_vision(path, max_charts=HOOK_MAX_CHARTS, empty_marker=False)
        return layout or ""
    except Exception:
        logger.warning("图表视觉重述钩子失败 %s", path, exc_info=True)
        return ""
