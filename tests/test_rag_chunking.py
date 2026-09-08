"""研报 RAG 切块纯函数测试(不依赖 chromadb / sentence-transformers)。"""

from tradingagents.rag.chunking import CHUNK_SIZE, build_chunks, is_low_info, split_text


def test_is_low_info_toc_vs_prose():
    # 真机样本:目录页点线占比 0.87~0.94,正文几乎为 0
    toc = "图表1：上海螺纹现货价格走势图" + "." * 300 + "12"
    assert is_low_info(toc) is True
    assert is_low_info("." * 400) is True
    assert is_low_info("") is True
    prose = "螺纹钢供需双弱,但库存持续去化,成本端支撑仍在,短期价格宽幅震荡。"
    assert is_low_info(prose) is False


def test_is_low_info_table_axis_chunk():
    """回归(2026-09-08 真机):PDF 图表提取后只剩坐标轴数字序列,嵌入分高但无内容,
    曾把原油问答的宏观正文块挤出 top-k。阈值 0.4:正文最高 0.31,图表轴最低 0.45。"""
    axis = "0 500 1000 1500 2000 2500 3000 3500 4000 4500 " * 10
    assert is_low_info(axis) is True
    table = "82.0 80.0 78.0 76.0 74.0 72.0 70.0 68.0 平衡表百万桶/日 " * 8
    assert is_low_info(table) is True
    # 带价格的正常正文(数字占比 ~0.1)不能误伤
    prose = (
        "螺纹钢现货价格3850元/吨,环比上涨30元,库存继续去化。华东多地贸易商反映"
        "成交尚可,下游按需采购,市场情绪整体偏稳,预计短期价格维持震荡运行格局。"
    )
    assert is_low_info(prose) is False


def test_build_chunks_filters_table_chunks():
    # 整篇只有图表轴数字:0 块入索引
    row = _row(extracted_text="0 500 1000 1500 2000 2500 3000 3500 4000 4500 " * 40)
    assert build_chunks(row) == []


def test_build_chunks_filters_toc_chunks():
    # 整篇只有目录:0 块入索引
    row = _row(extracted_text="一、当日观点总表" + "." * 350 + "1\n" + "二、观点对比" + "." * 350 + "2\n")
    assert build_chunks(row) == []
    # 混合文本:目录块被滤掉,正文块保留且编号连续
    mixed = "一、当日观点总表" + "." * 400 + "1\n\n" + "正文开始。" * 80
    chunks = build_chunks(_row(extracted_text=mixed))
    assert chunks
    assert all("当日观点总表" not in c["text"] for c in chunks)
    assert [c["metadata"]["chunk_index"] for c in chunks] == list(range(len(chunks)))


def test_empty_text_returns_no_chunks():
    assert split_text("") == []
    assert split_text("   \n\t ") == []


def test_short_text_single_chunk():
    text = "短文本,一段话。"
    assert split_text(text) == [text]


def test_chunk_size_invariant():
    text = "。".join(f"第{i}个句子内容" + "甲" * 40 for i in range(40))
    chunks = split_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    # 切块不丢内容(允许重叠,不允许缺口)
    assert all(c.strip() for c in chunks)


def test_adjacent_chunks_overlap():
    # 句长约 30 字符:60 字符重叠窗内必有句界,重叠区可稳定提取
    text = "".join(f"s{i}。" + "x" * 26 for i in range(30))
    chunks = split_text(text)
    assert len(chunks) > 1
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt[:10] in prev, "相邻 chunk 应共享重叠内容"


def test_hard_cut_no_separator_no_gap():
    text = "乙" * 1000  # 无任何分隔符
    chunks = split_text(text, overlap=0)
    assert len(chunks) == 3
    # 无重叠时拼接应完整还原原文
    assert "".join(chunks) == text
    # 默认 overlap:硬切段占满 chunk_size 时前两块放弃重叠,末块带尾部重叠
    chunks_ol = split_text(text)
    assert chunks_ol[0] == text[:400]
    assert chunks_ol[1] == text[400:800]
    assert chunks_ol[2].endswith(text[-200:])
    assert chunks_ol[2][:10] in chunks_ol[1], "末块应与上一块共享重叠内容"


def test_paragraph_boundary_priority():
    text = "A" * 300 + "\n\n" + "B" * 300
    chunks = split_text(text, overlap=0)
    assert len(chunks) == 2
    assert chunks == ["A" * 300, "B" * 300]
    # 默认 overlap:首块仍在段落边界切开,第二块主体是 B 段
    chunks_ol = split_text(text)
    assert chunks_ol[0] == "A" * 300
    assert chunks_ol[1].endswith("B" * 300)


def test_trailing_newline_dotted_line_no_recursion():
    """回归(2026-09-08 真机回填):2000 字目录行唯一分隔符 \\n 在段尾,拼回后
    长度不变,曾致 _segments 无限递归(39 份研报回填失败)。"""
    text = "标题。" + "." * 2040 + "24\n" + "正文开始。"
    chunks = split_text(text)
    assert chunks
    assert all(len(c) <= CHUNK_SIZE for c in chunks)


def test_sentence_boundary_priority():
    text = "".join(f"第{i}句。" + "丙" * 26 for i in range(30))
    chunks = split_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    # 按句界切:chunk 以句末标点收尾(末块可能是句中残段)
    assert all(c.endswith("。") for c in chunks[:-1])
    # 首块从句首开始
    assert chunks[0].startswith("第0句。")


def _row(**overrides):
    row = {
        "id": 42,
        "title": "螺纹钢日报",
        "variety": "RB",
        "varieties": "RB,HC",
        "publish_date": "2026-09-07",
        "report_type": "日报",
        "source": "华泰期货",
        "direction": "看多",
        "extracted_text": "丁" * 1000,
    }
    row.update(overrides)
    return row


def test_build_chunks_metadata_and_ids():
    chunks = build_chunks(_row())
    assert len(chunks) == 3
    first = chunks[0]
    assert first["id"] == "42:0"
    assert first["metadata"]["report_id"] == 42
    assert first["metadata"]["chunk_index"] == 0
    assert first["metadata"]["chunk_total"] == 3
    assert first["metadata"]["variety"] == "RB"
    assert first["metadata"]["varieties"] == "RB,HC"
    assert first["metadata"]["publish_date"] == "2026-09-07"
    assert first["metadata"]["report_type"] == "日报"
    assert first["metadata"]["direction"] == "看多"
    assert first["metadata"]["text_truncated"] is False
    # embedding 文本带标题前缀(小库召回关键)
    assert first["embedding_text"].startswith("螺纹钢日报(RB 2026-09-07 日报):")
    assert first["embedding_text"].endswith(first["text"])


def test_build_chunks_truncated_flag():
    chunks = build_chunks(_row(extracted_text="丁" * 20000))
    assert chunks[0]["metadata"]["text_truncated"] is True


def test_build_chunks_empty_text():
    assert build_chunks(_row(extracted_text="")) == []


def test_build_chunks_defaults_for_blank_fields():
    chunks = build_chunks(_row(variety="", title="", publish_date=None, direction=None))
    meta = chunks[0]["metadata"]
    assert meta["variety"] == "MULTI"
    assert meta["publish_date"] == ""
    assert meta["direction"] == ""
    assert meta["title"] == ""


def test_build_chunks_prefers_layout_text():
    """2026-09-08 方案三:layout_text(版面感知,含【图】块)非空则优先于 extracted_text。"""
    layout = "【图】图1：镍矿产量\n2024  2025  2026\n资料来源：SMM\n\n" + "正文开始。" * 60
    chunks = build_chunks(_row(layout_text=layout, extracted_text="旧口径正文" * 500))
    assert chunks
    assert any("【图】图1：镍矿产量" in c["text"] for c in chunks)
    assert all("旧口径正文" not in c["text"] for c in chunks)


def test_build_chunks_fig_block_exempt_from_digit_filter():
    """【图】/【表】块豁免数字占比过滤:轴数字多但带图题/资料来源,是真实信息。
    点线目录过滤仍然生效(图块内出现目录点线的概率为零)。"""
    layout = "【图】图1：镍矿产量\n0 500 1000 1500 2000 2500 3000\n资料来源：SMM\n" + "正文。"
    chunks = build_chunks(_row(layout_text=layout, extracted_text=""))
    assert chunks
    assert any("【图】图1：镍矿产量" in c["text"] for c in chunks)


def test_build_chunks_layout_fallback_to_extracted():
    # layout_text 为空/空白 → 回退 extracted_text(老 .md 研报与提取失败回退口径)
    chunks = build_chunks(_row(layout_text="", extracted_text="丁" * 1000))
    assert chunks and all("丁" in c["text"] for c in chunks)
    assert build_chunks(_row(layout_text="   ", extracted_text="丁" * 1000))
