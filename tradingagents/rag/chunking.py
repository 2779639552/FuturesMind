"""研报正文切块:纯函数,零第三方依赖。

策略(计划定稿):chunk_size=400 中文字符、overlap=60,对齐 bge-small-zh-v1.5
的 512 token 上限;切分优先级 段落(\\n\\n)→ 换行(\\n)→ 句子(。!?;)→ 硬切。
"""

from __future__ import annotations

import re

CHUNK_SIZE = 400
OVERLAP = 60

# 入库截断阈值(web_app._process_research_report 里 text[:20000]),达到即视为被截断
TRUNCATED_AT = 20000

_SENTENCE_ENDERS = set("。!?;!？!；")
# 句子切分:保留句末标点在段内
_SENTENCE_RE = re.compile(r"[^。!?;!?；]*[。!?;!?；]|[^。!?;!?；]+$")
# 目录行特征:连续 3+ ASCII 点 / 2+ 中文省略号(如 "图表1：上海螺纹现货价格走势图.......12")
_DOT_RUN_RE = re.compile(r"(?:\.{3,}|…{2,})")
# 点线字符占比阈值:超过即判为目录/点线噪声 chunk,不入索引。
# 真机校准(2026-09-08,2740 chunk 全库分布):目录页 chunk 占比 0.87~0.94、
# 正文 chunk 几乎全为 0,0.2~0.5 之间仅 9 个且全是目录残余 —— 正文零误伤。
# 纯数据/图表坐标轴 chunk:PDF 图表提取后只剩数字序列(如 "0 500 1000 1500 2000"),
# 嵌入分高但无观点内容,还会把正文块挤出 top-k(2026-09-08 真机:原油问答 top 命中
# 全是图表轴数字,宏观正文块被挤出)。阈值校准:全库正文最高 0.31,图表轴/表格
# 最低 0.45,0.32~0.44 为空档。
TABLE_DIGIT_RATIO = 0.4
TOC_DOT_RATIO = 0.2
# 版面感知块标记(2026-09-08 方案三:pdf_layout 把图表/表格聚成【图】/【表】块)。
# 这类块天然数字占比高(轴/表体),但带图题/图例/资料来源,是真实信息 → 豁免
# 数字占比过滤(点线目录过滤仍然生效)。
_BLOCK_MARK_RE = re.compile(r"【[图表]】")


def _is_toc_noise(text: str) -> bool:
    """目录/点线噪声判定(连续点线占比超阈值)。"""
    dots = sum(len(m.group()) for m in _DOT_RUN_RE.finditer(text))
    return dots / len(text) >= TOC_DOT_RATIO


def _is_digit_noise(text: str) -> bool:
    """纯数字块判定(表格轴/表体残骸:数字占比超阈值)。"""
    digits = sum(ch.isdigit() for ch in text)
    return digits / len(text) >= TABLE_DIGIT_RATIO


def is_low_info(text: str) -> bool:
    """目录/点线噪声、纯数字表格 chunk 判定(2026-09-08 用户实测:最优命中常是
    研报目录页或图表坐标轴)。"""
    if not text or not text.strip():
        return True
    return _is_toc_noise(text) or _is_digit_noise(text)


def _split_long_segment(segment: str, chunk_size: int) -> list[str]:
    """把超过 chunk_size 的单段按下一级分隔符拆开。

    每级切分必须收缩(任一片段严格短于原段),否则降级下一策略 —— 防御
    "唯一分隔符在段尾、拼回后长度不变"的死循环(如 2000 字目录行只以 \\n 收尾)。
    """
    for sep in ("\n\n", "\n"):
        if sep in segment:
            parts = segment.split(sep)
            pieces = [p + sep if i < len(parts) - 1 else p for i, p in enumerate(parts)]
            if max(len(p) for p in pieces) < len(segment):
                return pieces
    sentences = _SENTENCE_RE.findall(segment)
    if len(sentences) > 1 and max(len(s) for s in sentences) < len(segment):
        return sentences
    # 无有效分隔符:硬切
    return [segment[i : i + chunk_size] for i in range(0, len(segment), chunk_size)]


def _segments(text: str, chunk_size: int) -> list[str]:
    """层级切分到每个片段 ≤ chunk_size。"""
    if len(text) <= chunk_size:
        return [text] if text else []
    out: list[str] = []
    for piece in _split_long_segment(text, chunk_size):
        out.extend(_segments(piece, chunk_size))
    return out


def _tail_overlap(text: str, overlap: int) -> str:
    """取上一 chunk 尾部作为下一 chunk 的重叠前缀,尽量从句界起头。"""
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    for i, ch in enumerate(tail):
        if ch in _SENTENCE_ENDERS or ch == "\n":
            remainder = tail[i + 1 :]
            if remainder.strip():
                return remainder
    return tail


def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[str]:
    """把文本切成 ≤ chunk_size 的块,相邻块之间带约 overlap 字符的尾部重叠。

    空文本(纯空白)返回 []。
    """
    if not text or not text.strip():
        return []
    segments = _segments(text, chunk_size)
    chunks: list[str] = []
    cur = ""
    for seg in segments:
        if cur and len(cur) + len(seg) > chunk_size:
            chunks.append(cur)
            tail = _tail_overlap(cur, overlap)
            # 重叠前缀 + 新片段仍可能超限(片段接近满长):放弃重叠
            if len(tail) + len(seg) > chunk_size:
                tail = ""
            cur = tail
        cur += seg
    if cur.strip():
        chunks.append(cur)
    return [c.strip() for c in chunks]


def build_chunks(row: dict, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[dict]:
    """把研报行(含 extracted_text / layout_text)切块并组装 embedding 文本与 Chroma metadata。

    row 需要的键:id/title/variety/varieties/publish_date/report_type/source/
    direction/extracted_text/layout_text(缺失按空处理,空正文返回 [])。

    【文本源优先级】layout_text(版面感知提取,图表聚成【图】/【表】块)非空则优先,
    否则回退 extracted_text —— 后者有 20000 截断且图表被撕成轴数字块。
    TRUNCATED_AT 只对 extracted_text 路径生效(layout_text 无该截断)。

    返回元素:{id, text, embedding_text, metadata}。
    embedding_text = 标题前缀 + 正文块(标题前缀显著提升小库召回,零成本)。
    """
    report_id = int(row["id"])
    layout = row.get("layout_text") or ""
    if layout.strip():
        text = layout
        truncated = False  # layout_text 无 20000 截断(web_app 落库上限 60000,不达阈值语义)
    else:
        text = row.get("extracted_text") or ""
        truncated = len(text) >= TRUNCATED_AT
    title = (row.get("title") or "").strip()
    variety = (row.get("variety") or "MULTI").strip() or "MULTI"
    publish_date = (row.get("publish_date") or "").strip()
    report_type = (row.get("report_type") or "").strip()
    source = (row.get("source") or "").strip()
    direction = (row.get("direction") or "").strip()
    varieties = (row.get("varieties") or "").strip()

    prefix = f"{title}({variety} {publish_date} {report_type}):"
    # 目录/点线噪声 chunk 直接不索引(详见 is_low_info);先过滤再编号,chunk_total=净块数。
    # 【图】/【表】块豁免数字占比过滤(块内轴/表体数字多是常态,信息在图题/图例里)。
    chunks = [
        c
        for c in split_text(text, chunk_size, overlap)
        if not (_is_toc_noise(c) or (_is_digit_noise(c) and not _BLOCK_MARK_RE.search(c)))
    ]
    total = len(chunks)
    return [
        {
            "id": f"{report_id}:{i}",
            "text": chunk,
            "embedding_text": f"{prefix}\n{chunk}",
            "metadata": {
                "report_id": report_id,
                "chunk_index": i,
                "chunk_total": total,
                "variety": variety,
                "varieties": varieties,
                "publish_date": publish_date,
                "report_type": report_type,
                "title": title,
                "source": source,
                "direction": direction,
                "text_truncated": truncated,
            },
        }
        for i, chunk in enumerate(chunks)
    ]
