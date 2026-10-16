"""研报正文切块:纯函数,零第三方依赖。

策略(计划定稿):chunk_size=400 中文字符、overlap=60,对齐 bge-small-zh-v1.5
的 512 token 上限;切分优先级 段落(\\n\\n)→ 换行(\\n)→ 句子(。!?;)→ 硬切。

2026-09-15 增补两块能力(起因:用户实测"RAG 检索到的片段与总结模块的结论不一致"——
RAG 只给丁二烯产能表/BR 行情块,总结却给出"永安看空"):

  ① **正文行级清洗**(`clean_lines` + `block_passes`):尾部免责区截断、水印重复行折叠、
     免责/联系行删除、目录块与"只有坐标轴没有汉字"的图块挡在索引外。实测 chunk 数
     −47%(6524→3482),且把 417 那条排第一的免责声明续行块彻底清掉。
  ② **【研报结论】块**(`conclusion_text`):把 DB 已有的结构化结论字段拼成一条"结论块"
     置首入索引,与每日/单品种总结**同源**。结构层根因是两条链路读不同文本源:总结读
     `conclusion_md`/`structured_data`,RAG 只读 PDF 版面块(layout_text)。
"""

from __future__ import annotations

import os
import re
from collections import Counter

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

_CJK_RE = re.compile(r"[\u4e00-\u9fa5]")


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


# ══════════════════════════════════════════════════════════════════════════
# 正文行级清洗(2026-09-15)
# ══════════════════════════════════════════════════════════════════════════
# 【为什么】旧的块级过滤只看"数字/点线占比",拦不住两类最常见的噪声:
#   - 水印/页脚重复行:中信 402 的联系行("中信期货员工/曹张驰/18058991618")
#     在全文重复 **304 次**,每一条都带着明确的数字,嵌入分不低;
#   - 尾部免责声明:417 的免责续行块以 0.704 分排在"BR 观点"问题的**第一位**
#     (因为它重复了报告标题与免责模板语,与标题前缀高度相似)。
# 这两类都不含任何观点,却会把真正的"观点/策略"块挤出 top-k。

# 水印/页脚重复行:同一行在全文出现 ≥ 该次数即视为水印(中信 402 实测 304 次)
REPEAT_MIN = 3
# 免责/风险/版权/联系类整行(行级删除,精度优先)
# ⚠️ 这里**刻意不写裸"风险"**:"风险:丁二烯价格走强"之类是研报正文的常见句尾,
#    行级误删会直接吃掉正文(2026-09-15 实测:块级用"风险"计数误伤 80 块正文)。
BOILER_LINE = re.compile(
    r"免责声明|免责|风险提示|版权声明|版权|翻版|转载|商标|承担任何责任|自主决策"
    r"|市场有风险|投资需谨慎|投资有风险|不构成|投资建议|仅供参考"
    r"|未经.{0,8}许可|复制|THANKS|谢谢"
    r"|扫码|二维码|公众号|微信|APP|iPhone|Android|下载"
    r"|电话[:：]|邮箱[:：]|地址[:：]|邮编|客服"
    r"|员工|研究员$|从业资格|投资咨询资格|Z\d{7}|F\d{7}"
)
# 【尾部免责区】起始标记行:命中后,该行及其后内容整段丢弃。免责声明常跨多行,
# 逐行关键词匹配只能拦住第一行,续行会被当正文。
TAIL_MARK = re.compile(
    r"^\s*(?:【?\s*(?:风险提示及)?免责声明\s*】?|版权声明|分析师声明|法律声明"
    r"|谢谢(?:观看)?|THANKS|股票代码[:：]?\s*\d{6})\s*$",
    re.I,
)
# 块级免责密度:块内"免责/版权类行"占非空行 ≥ 该比例 → 整块丢(拦跨行的免责声明续块)。
# 【为什么用占比而不是关键词计数】版面提取把双栏压平后,免责框与正文可能落在同一块里
# (417 的免责续行块、485 的"许可/风险/免责"混排块),按"命中 ≥2 个关键词"计数会把
# 同一块里的正常分析文字一起丢掉 —— 2026-09-15 全库实测误伤 80 块(占 2.3%),
# 改成行占比后归零;纯免责块(整块都是模板语)仍然被拦。
BOILER_LINE_RATIO = 0.5
# 目录块:含"目录/Contents"且以短行罗列为主、无句末标点 → 只是一串章节标题
TOC_MARK = re.compile(r"目录|Contents|TABLE OF CONTENTS", re.I)
# 极短噪声行:1~2 字符且无标点(水印碎字如 "中")
TINY_LINE = re.compile(r"^[\u4e00-\u9fa5A-Za-z]{1,2}$")
# 纯数字/日期行:页码、日期、单值
NUM_LINE = re.compile(r"^[\d\s/.,:%\-+年月日()（）]+$")
# 图表块"有信息量"门槛:图题/图例/资料来源至少要有这么多汉字(低于此值的【图】块
# 其实只剩坐标轴数字,不该靠"【图】"标记豁免数字过滤)
FIG_MIN_CJK = 25


def _is_boilerplate_line(s: str) -> bool:
    return bool(BOILER_LINE.search(s))


def _is_tiny_noise(s: str) -> bool:
    return bool(TINY_LINE.match(s)) or bool(NUM_LINE.match(s))


def clean_lines(text: str) -> str:
    """行级清洗:尾部免责区截断 + 折叠重复行(水印) + 删免责/联系行 + 删极短噪声行。

    【作用范围】对 layout_text(优先源)或 extracted_text(回退源)统一使用,
    清洗后再切块 —— 切块前清洗才能保证"免责区不会跨块拼出来"。
    """
    if not text:
        return ""
    lines = text.split("\n")
    # 【尾部免责区】取最后一个标记行位置,其后整体丢弃(免责/致谢/尾页徽标都在文末)
    cut = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if TAIL_MARK.match(lines[i].strip()):
            cut = i
            break
    lines = lines[:cut]
    cnt = Counter(li.strip() for li in lines if li.strip())
    seen: set[str] = set()
    out: list[str] = []
    for li in lines:
        s = li.strip()
        if not s:
            out.append("")
            continue
        if cnt[s] >= REPEAT_MIN:  # 水印:整篇重复出现的行只留第一次
            if s in seen:
                continue
            seen.add(s)
        if _is_boilerplate_line(s) or _is_tiny_noise(s):
            continue
        out.append(li)
    return "\n".join(out)


def is_boiler_chunk(chunk: str) -> bool:
    """块级免责判定:免责/版权类行占该块非空行 ≥ BOILER_LINE_RATIO → 整块丢弃。

    拦的是"整块都是免责模板语、但逐行看每行都不完整命中 BOILER_LINE"的续行块
    (417 实测:该续行块以 0.704 分排在"BR 观点"问题第一位)。混排块(免责行 + 正文行)
    因占比不过半而保留,正文靠 `clean_lines` 的行级删除清掉免责行 —— 避免误伤。
    """
    lines = [li.strip() for li in chunk.split("\n") if li.strip()]
    if not lines:
        return False
    hit = sum(1 for li in lines if _is_boilerplate_line(li))
    return hit / len(lines) >= BOILER_LINE_RATIO


def is_toc_block(chunk: str) -> bool:
    """目录块判定:含"目录/Contents"、短行罗列且**全文无句末句号** → 一串章节标题,零内容。

    【阈值为何是这个】2026-09-15 用 300 篇实测对比过三档判据:
      - "无句号 + 短行占比 ≥0.6"(本版):拦下纯目录块,误伤含观点块 **0**;
      - "长行占比 <0.3"(放宽句号否决):多拦 16 块,但其中 **12 块含正常正文观点** ——
        封面/目录常与"综述"正文被版面提取压进同一块,一旦混排就属于正文,不该整块丢;
      - "长行占比 <0.15":误伤虽回到 0,但已不再多拦任何目录块(净增 3 块存活)。
    故保留句号否决:宁可漏掉混排目录块,也不能吃掉正文观点。
    【已知残留】417 的"封面+目录+综述"混排块仍以 0.808 分排在"合成橡胶供需格局"问题
    第一位(见 memory 待办:封面块首句常含"综述",语义上确实是该问题的答案之一,
    不算纯噪声,暂不动)。
    """
    if not TOC_MARK.search(chunk):
        return False
    if "。" in chunk:  # 出现完整句子 → 判为正文混排,不整块丢弃
        return False
    lines = [li.strip() for li in chunk.split("\n") if li.strip()]
    if not lines:
        return False
    short = sum(1 for li in lines if len(li) <= 20)
    return short / len(lines) >= 0.6


def fig_quality_ok(chunk: str) -> bool:
    """【图】/【表】块豁免数字过滤的前置条件:块内要有足够汉字(图题/图例/来源)。"""
    return len(_CJK_RE.findall(chunk)) >= FIG_MIN_CJK


def block_passes(chunk: str) -> bool:
    """chunk 是否值得入索引(行级清洗之后的第二道闸)。

    【判定链】目录点线 → 目录块 → 免责密度 → 数字占比(【图】/【表】块需带足够汉字才豁免)。
    【误伤自检】2026-09-15 全库 300 篇:被本函数拦下的块中含"策略/观点/结论/展望"语义的
    为 0(见 tests/test_rag_conclusion_block.py::test_no_opinion_block_is_dropped)。
    """
    if _is_toc_noise(chunk) or is_toc_block(chunk) or is_boiler_chunk(chunk):
        return False
    if _is_digit_noise(chunk):
        if not (_BLOCK_MARK_RE.search(chunk) and fig_quality_ok(chunk)):
            return False
    return True


# ══════════════════════════════════════════════════════════════════════════
# 【研报结论】块(2026-09-15;用户拍板的"四道闸门"方案)
# ══════════════════════════════════════════════════════════════════════════
# 闸门②身份标注:块首显式声明"系统归纳,非原文",让模型与用户一眼分清来源
CONCLUSION_MARK = "【研报结论·系统归纳｜非原文】"
# 闸门④回滚开关:置 0/false/off 后重建索引即回到"只有原文块"的旧行为
ENV_CONCLUSION_CHUNK = "RAG_CONCLUSION_CHUNK"
# conclusion_md 里要抽进结论块的小节与各自字数上限(方向/策略先于这些,故排后)
CONC_SECTIONS = (("观点与依据", 160), ("多空要点", 160), ("交易要素与风险", 120))
# 结论块总长上限:结论块要落在 embedding 的语义窗口内,方向/策略在最前
CONCLUSION_MAX = 480

# 影子校验:行内 ≥3 位数字必须能溯回本篇正文(日期/产能/价格都算)
_NUM3_RE = re.compile(r"\d{3,}")
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _norm_digits(s: str) -> str:
    """全角数字转半角 + 去掉数字千分位逗号 —— 让"1,234"与"1234"能对上。"""
    return re.sub(r"(?<=\d),(?=\d{3})", "", (s or "").translate(_FULLWIDTH_DIGITS))


def _numbers_traceable(line: str, corpus: str) -> bool:
    """闸门①影子校验:行内每个 ≥3 位数字都必须能在本篇正文里找到。

    【为什么】conclusion_md 是逐品种一次 LLM 调用生成的,提示词里**额外注入了其他
    研报的 RAG 片段**(web_app._llm_opinion_conclusion 的 rag_ctx),模型会把别篇/
    别日期的数字写进本篇小节。实测 worst case 是东证"繁微长图"系列:正文只有 ~290 字,
    结论却带着 20+ 个具体数字(345/393 篇命中)。影子校验把这类"借来的数字"整行挡掉。
    """
    if not corpus:
        return True
    return all(n in corpus for n in _NUM3_RE.findall(_norm_digits(line)))


def conclusion_enabled() -> bool:
    """结论块总开关(闸门④);默认开,env RAG_CONCLUSION_CHUNK=0 关。"""
    v = (os.environ.get(ENV_CONCLUSION_CHUNK) or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _md_section(md: str, name: str, cap: int) -> str:
    """从 conclusion_md 抽 `## 小节名` 正文,压平换行并截断到 cap 字。"""
    m = re.search(rf"^##\s*{name}\s*\n(.*?)(?=\n##\s|\Z)", md or "", re.S | re.M)
    return re.sub(r"\s*\n\s*", " ", m.group(1)).strip()[:cap] if m else ""


def conclusion_text(row: dict, source_text: str = "") -> str:
    """拼「研报结论」块(与每日/单品种总结**同源**);返回 "" 表示本篇不出结论块。

    【为什么需要】总结链路读 conclusion_md / structured_data,RAG 只读 PDF 版面块 ——
    实测 417(永安研究中心能化团队《合成橡胶周报》2026-09-13)的"BR 看空"策略段落被
    7 个【图】块挤出单篇 top-2(该块在篇内语义排名第 8),RAG 因此"查得到图表、查不到
    结论",与总结模块给出的"永安看空"自相矛盾。

    【四道闸门】(2026-09-15 用户拍板)
      ① 影子校验 `_numbers_traceable`:conclusion_md 派生行的 ≥3 位数字必须溯回本篇
         正文,否则整行丢弃。实测丢弃 5.3%(222/4152 行)、0 篇被清空,417 的 14 行全过。
      ② 身份标注 `CONCLUSION_MARK`:块首标"系统归纳|非原文",提示词另加说明。
      ③ 自报字段并存:structured_data 的 direction/confidence/operation_advice 逐品种
         列出 —— 这一路**不经 RAG**(`_llm_extract_structured` 无 rag_ctx 注入),
         可与闸门①互证,也直接回答"系统认为这个品种是什么方向"。
      ④ 原文块照旧并存(仅降噪)+ env `RAG_CONCLUSION_CHUNK=0` 可整块回滚。

    【参数】source_text:本篇原文(extracted_text + layout_text 拼接),影子校验语料。
    【返回】块文本(≤ CONCLUSION_MAX 字符,方向/策略在最前);字段不足返回 ""。
    """
    import json

    try:
        sd = json.loads(row.get("structured_data") or "{}")
    except (ValueError, TypeError):
        sd = {}
    if not isinstance(sd, dict):
        sd = {}
    vs = sd.get("varieties") if isinstance(sd.get("varieties"), list) else []
    md = row.get("conclusion_md") or ""

    # 抬头行:字段全部来自 DB(非 LLM 生成),不参与影子校验
    head = "{}{}《{}》{} {}".format(
        CONCLUSION_MARK,
        (row.get("source") or "").strip(),
        (row.get("title") or "").strip(),
        (row.get("publish_date") or "").strip(),
        (row.get("report_type") or "").strip(),
    ).strip()
    lines = [head]

    for v in vs:  # 闸门③:研报自报字段(该路不经 RAG,可作互证)
        if not isinstance(v, dict):
            continue
        code = (v.get("variety") or "").strip()
        if not code:
            continue
        seg = f"{code} 方向：{(v.get('direction') or '').strip() or '未披露'}"
        conf = v.get("confidence")
        if conf is not None:
            seg += f"（置信度 {conf}）"
        adv = (v.get("operation_advice") or "").strip()
        if adv:
            seg += f"；策略：{adv}"
        lines.append(seg)

    corpus = _norm_digits(source_text)  # 闸门①:本篇原文(已归一)做影子校验语料
    for name, cap in CONC_SECTIONS:
        sec = _md_section(md, name, cap)
        if sec and _numbers_traceable(sec, corpus):
            lines.append(f"{name}：{sec}")

    if len(lines) <= 1:  # 只有抬头 = 无内容(未处理完成/旧数据)
        return ""
    text = "\n".join(lines)
    if len(text) < 40:
        return ""
    # 控制块长:超长按行丢弃尾部(方向/策略在最前,优先保住)
    while len(text) > CONCLUSION_MAX and len(lines) > 2:
        lines.pop()
        text = "\n".join(lines)
    return text


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
    """把研报行(含 extracted_text / layout_text / structured_data / conclusion_md)
    切块并组装 embedding 文本与 Chroma metadata。

    row 需要的键:id/title/variety/varieties/publish_date/report_type/source/
    direction/extracted_text/layout_text(缺失按空处理,空正文且无结论块返回 [])。
    可选键:structured_data / conclusion_md —— 有值时额外产出一条【研报结论】块(置首),
    见 `conclusion_text`;缺值时行为与历史版本一致。

    【文本源优先级】layout_text(版面感知提取,图表聚成【图】/【表】块)非空则优先,
    否则回退 extracted_text —— 后者有 20000 截断且图表被撕成轴数字块。
    TRUNCATED_AT 只对 extracted_text 路径生效(layout_text 无该截断)。

    【处理顺序】原文 → `clean_lines` 行级清洗 → `split_text` → `block_passes` 过滤;
    再在**最前面**插入【研报结论】块(chunk_index=0,便于按分数排序时天然靠前)。

    返回元素:{id, text, embedding_text, metadata}(metadata.kind = "conclusion"/"body")。
    embedding_text = 标题前缀 + 块文本(标题前缀显著提升小库召回,零成本)。
    """
    report_id = int(row["id"])
    layout = row.get("layout_text") or ""
    if layout.strip():
        raw = layout
        truncated = False  # layout_text 无 20000 截断(web_app 落库上限 60000,不达阈值语义)
    else:
        raw = row.get("extracted_text") or ""
        truncated = len(raw) >= TRUNCATED_AT
    title = (row.get("title") or "").strip()
    variety = (row.get("variety") or "MULTI").strip() or "MULTI"
    publish_date = (row.get("publish_date") or "").strip()
    report_type = (row.get("report_type") or "").strip()
    source = (row.get("source") or "").strip()
    direction = (row.get("direction") or "").strip()
    varieties = (row.get("varieties") or "").strip()

    prefix = f"{title}({variety} {publish_date} {report_type}):"
    # 目录/点线噪声 chunk 直接不索引(详见 is_low_info);先过滤再编号,chunk_total=净块数。
    # 前置 clean_lines:水印重复行/尾部免责区必须在切块前清掉,否则会跨块拼进正文。
    body_chunks = [
        c for c in split_text(clean_lines(raw), chunk_size, overlap) if block_passes(c)
    ]
    # 【研报结论】块置首(闸门②③④);env RAG_CONCLUSION_CHUNK=0 时整块不出。
    texts = list(body_chunks)
    kinds = ["body"] * len(texts)
    if conclusion_enabled():
        concl = conclusion_text(
            row, source_text=f"{row.get('extracted_text') or ''}\n{layout}"
        )
        if concl:
            texts.insert(0, concl)
            kinds.insert(0, "conclusion")
    total = len(texts)
    return [
        {
            "id": f"{report_id}:{i}",
            "text": chunk,
            "embedding_text": f"{prefix}\n{chunk}",
            "metadata": {
                "report_id": report_id,
                "chunk_index": i,
                "chunk_total": total,
                "kind": kinds[i],
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
        for i, chunk in enumerate(texts)
    ]
