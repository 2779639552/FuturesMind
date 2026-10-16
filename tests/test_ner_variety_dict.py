"""NER 品种词典修复守卫(2026-09-18)。

背景: 白名单 20 品种里有 5 个被 NER 词典弄哑(原油 41.7% / LPG·纯苯·多晶硅·
丁二烯橡胶 均 0%), 另有一批 2 字母别名因子串匹配长期误标(NASA→纯碱、
MACD→甲醇、石油ETF→5年期国债、APTANY→PTA、SHFE→烧碱)。本测试锁死修复成果:

  1. 5 个受损品种必须能标出; 多晶硅/工业硅、丁二烯橡胶/橡胶 不再互相吞并;
  2. 2 字母以上纯拉丁别名大小写不敏感(pta/sc/meg 可标), 且必须带
     **ASCII 字母边界**(汉字不算边界, 否则"跟pta"这类最普遍的中文紧邻写法会漏标);
  3. 单字母别名**不参与**小写化(它们同时是升/吨/米等计量单位缩写);
  4. 词典内无同名别名冲突(曾发生 SM 被苯乙烯抢走);
  5. 开发副本与生产副本(思路2/validate)文件一致。

范式与 test_energy_variety_coverage.py 一致: 按路径直接加载 ner.py, 脱离包结构。
"""

import hashlib
import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_VALIDATE = _REPO / "data_collection" / "validate"
_PROD_VALIDATE = _REPO.parent / "思路2" / "validate"


def _load_ner():
    """按路径加载 ner.py(独立模块, 不依赖 data_collection 是否为包)。"""
    spec = importlib.util.spec_from_file_location("ner_under_test", _VALIDATE / "ner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ner():
    return _load_ner().FuturesNER()


def names(ner, text):
    """文本 → 标出的标准品种名集合"""
    return {v["name"] for v in ner.extract(text)["varieties"]}


# ---------------------------------------------------------------- 1. 受损品种

RECOVERED = [
    ("原油裸词", "原油今日大涨", "原油"),
    ("原油+期货", "原油期货跌超3%", "原油"),
    ("LPG 缩写", "LPG期货今日大涨", "LPG"),
    ("液化石油气", "液化石油气现货走强", "LPG"),
    ("PG 合约", "PG2501 主力合约", "LPG"),
    ("纯苯", "纯苯期货跌超2%", "纯苯"),
    ("BZ 合约", "BZ2611 持仓增加", "纯苯"),
    ("多晶硅", "多晶硅期货主力合约涨停", "多晶硅"),
    ("硅料(多晶硅俗称)", "硅料价格自低点反弹", "多晶硅"),
    ("丁二烯橡胶", "丁二烯橡胶期货", "丁二烯橡胶"),
    ("顺丁橡胶(俗称)", "顺丁橡胶现货报价", "丁二烯橡胶"),
    ("BR 合约", "BR2501 走强", "丁二烯橡胶"),
]


@pytest.mark.parametrize(("case", "text", "want"), RECOVERED)
def test_recovered_whitelist_varieties(ner, case, text, want):
    """5 个曾 0~41.7% 标注率的白名单品种必须能标出。"""
    assert want in names(ner, text), f"{case}: {text!r} 未标出 {want}"


def test_poly_silicon_no_longer_merged_into_industrial_silicon(ner):
    """多晶硅与工业硅是广期所两个独立品种, 不许再互相吞并。"""
    got = names(ner, "多晶硅期货主力合约涨停")
    assert got == {"多晶硅"}, f"多晶硅被串成: {got}"
    assert "工业硅" not in got
    assert names(ner, "工业硅期货") == {"工业硅"}


def test_butadiene_rubber_no_longer_merged_into_rubber(ner):
    """丁二烯橡胶(BR)是独立品种, 不许再被"橡胶"(RU)吞掉。"""
    got = names(ner, "丁二烯橡胶期货")
    assert got == {"丁二烯橡胶"}, f"丁二烯橡胶被串成: {got}"
    assert "橡胶" not in got
    assert names(ner, "天然橡胶期货") == {"橡胶"}


def test_sm_alias_belongs_to_manganese_silicon(ner):
    """SM 是锰硅(郑商所)代码; 曾误挂苯乙烯(大商所 EB)导致后者抢注。"""
    got = names(ner, "SM2501 锰硅")
    assert "锰硅" in got
    assert "苯乙烯" not in got, "SM 又被苯乙烯抢走了"


# ------------------------------------------------- 2. 大小写不敏感 + 字母边界

def test_latin_aliases_case_insensitive(ner):
    """小写写法(股吧/雪球大量存在)必须命中。"""
    assert "PTA" in names(ner, "跟pta，甲醇，玻璃期货")
    assert "原油" in names(ner, "sc2501 大涨")
    assert "乙二醇" in names(ner, "meg 现货走强")
    assert "沥青" in names(ner, "bu2610 拉涨")


BOUNDARY_NEGATIVE = [
    ("NASA 里的 SA", "并且在NASA的卫星图上出现了烟雾", "纯碱"),
    ("MACD 里的 MA", "均线怎么走，MACD有没有背离", "甲醇"),
    ("石油ETF 里的 TF", "为什么我买石油ETF跌？", "5年期国债期货"),
    ("APTANY 里的 PTA", "旗下拥有 SUNNY、APTANY 等轮胎品牌", "PTA"),
    ("BPTA 里的 PTA", "BPTA网校更新昨天第二天的高清训练视频", "PTA"),
    ("WEB3 里的 EB", "期货杠杆再高它也高不过WEB3啊", "苯乙烯"),
    ("CRB 指数里的 RB", "他还提到CRB期货价格指数", "螺纹钢"),
    ("CSI 公司里的 SI", "菲还介绍了CSI公司（商品指数）", "工业硅"),
    ("OPECX 里的 OPEC", "OPECX组织发布声明", "原油"),
    ("SINA 里的 SI", "sina 财经新闻", "工业硅"),
    ("vision 里的 SI", "vision 报告发布", "工业硅"),
    ("SHFE 里的 SH", "沥青2610(SHFE|bu2610) 走强", "烧碱"),
]


@pytest.mark.parametrize(("case", "text", "unwanted"), BOUNDARY_NEGATIVE)
def test_latin_alias_needs_ascii_letter_boundary(ner, case, text, unwanted):
    """拉丁别名前后不得紧邻 ASCII 字母, 否则英文串里的子串会大面积误标。"""
    got = names(ner, text)
    assert unwanted not in got, f"{case}: {text!r} 误标为 {unwanted}({sorted(got)})"


def test_chinese_adjacent_latin_still_matches(ner):
    """汉字不是 ASCII 字母, 不得当作边界把"跟pta"这类写法拒掉。"""
    assert "PTA" in names(ner, "跟pta")
    assert "原油" in names(ner, "看多sc")
    # 数字同理不构成边界冲突(RB2501 里的 RB 要能命中)
    assert "螺纹钢" in names(ner, "RB2501 涨停")


SINGLE_LETTER_NEGATIVE = [
    ("m2 增速", "m2 增速回落", "豆粕"),
    ("10t 现货", "10t 现货升水", "10年期国债期货"),
    ("500l 容量", "500l 容量规格", "塑料"),
    ("v2 版本", "v2 版本上线", "PVC"),
]


@pytest.mark.parametrize(("case", "text", "unwanted"), SINGLE_LETTER_NEGATIVE)
def test_single_letter_aliases_not_casefolded(ner, case, text, unwanted):
    """单字母别名是升/吨/米等计量单位缩写, 一旦小写化会大面积误标。"""
    got = names(ner, text)
    assert unwanted not in got, f"{case}: {text!r} 误标为 {unwanted}({sorted(got)})"


# ---------------------------------------------------------------- 3. 词典体检

def test_no_duplicate_alias_across_varieties():
    """同一别名不得挂到多个品种(写覆盖语义下后者取胜, 曾弄丢 SM→锰硅)。"""
    kb = _load_ner().VARIETY_KB
    owner = {}
    dup = []
    for std, info in kb.items():
        for a in info["aliases"]:
            if a in owner:
                dup.append(f"{a!r}: {owner[a]} vs {std}")
            owner[a] = std
    assert not dup, "词典存在同名别名冲突: " + "; ".join(dup)


def test_contract_patterns_cover_new_varieties(ner):
    """新入池品种的合约代码要能被 contracts 通道识别。"""
    got = {c["code"]: c["variety"] for c in ner.extract("PG2501 BR2501 BZ2501 PS2501")["contracts"]}
    assert got.get("PG2501") == "LPG"
    assert got.get("BR2501") == "丁二烯橡胶"
    assert got.get("BZ2501") == "纯苯"
    assert got.get("PS2501") == "多晶硅"


def test_dev_and_prod_copies_byte_identical():
    """开发副本与生产副本(思路2/validate)必须逐字节一致。"""
    dev = _VALIDATE / "ner.py"
    prod = _PROD_VALIDATE / "ner.py"
    if not prod.exists():  # 生产副本不在本机时跳过
        pytest.skip("生产副本不存在")
    d = hashlib.md5(dev.read_bytes()).hexdigest()
    p = hashlib.md5(prod.read_bytes()).hexdigest()
    assert d == p, f"两副本不一致: dev={d} prod={p}"
