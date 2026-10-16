"""拉丁别名"三道闸"守卫(2026-09-18)。

【背景】期货品种代码别名多是 1~3 个拉丁字母, 天生容易撞进英文串和其他编码。
上一版只做了"ASCII 字母边界"(挡被字母包裹的情况), 拦不住:
  - **后跟数字**: `(SH601015)` 股票代码 -> SH 烧碱(存量 874 次); `MA5` 均线 -> MA 甲醇
  - **形态词/版本号**: `深V` -> PVC; `M头` -> 豆粕; `L.M.S` -> 塑料; `见P1` -> 棕榈油
  - **国标号/交易术语**: `GB/T4008` -> T 十年期国债; `做T`/`T+0` -> T 十年期国债
实测存量 3.1 万条帖子里这类误标共 2239 个, 且**用户能直接在页面看到** ——
"4季度招商轮船将赚的盘满钵满"这条讲原油的帖子被标成碳酸锂, 就是 `VLCC`(超大型
油轮) 里的 `LC` 撞上了碳酸锂合约代码。

本测试锁死四件事:
  1. 三类误标形态必须被拒(逐条对应真实误标案例);
  2. 合约代码位置 `M2601`/`SH2601`/`C1200` 必须**仍能**命中(不能矫枉过正);
  3. 真信号(中文别名 / 多字母别名 / 大小写不敏感 / 中文紧邻)不得被误伤;
  4. **结构性**: 两处匹配逻辑必须共用同一判定函数, 源码里不得再有内联边界判断
     —— 上轮就是"两处同逻辑只改了一处", 这类漂移必须由测试拦住。

范式与 test_ner_variety_dict.py 一致: 按路径直接加载 ner.py, 脱离包结构。
"""

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_VALIDATE = _REPO / "data_collection" / "validate"
_PROD_VALIDATE = _REPO.parent / "思路2" / "validate"


def _load_ner():
    spec = importlib.util.spec_from_file_location("ner_gate_under_test", _VALIDATE / "ner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ner():
    return _load_ner().FuturesNER()


def names(ner, text):
    return {v["name"] for v in ner.extract(text)["varieties"]}


# ------------------------------------------------------------ 1. 误标必须被拒
# (文本, 不得出现的品种, 说明) —— 每条都对应存量数据里真实发生过的误标
FALSE_POSITIVES = [
    # 闸1: 被 ASCII 字母包裹(更长英文串的子串)
    ("中国进口原油在加速，VLCC海运繁忙", "碳酸锂", "VLCC(超大型油轮) 里的 LC"),
    ("陕西黑猫(SH601015) 今晚焦炭期货跌", "烧碱", "(SH601015) 股票代码前缀"),
    ("金十数据8月18日讯 (来自金十期货APP)", "PP", "APP 里的 PP"),
    ("石油一直涨价，为什么我买石油ETF跌", "5年期国债期货", "ETF 里的 TF"),
    ("MACD0上0下 可以拿来当顺势工具", "甲醇", "MACD 里的 MA"),
    ("参与#2026模拟炒股大赛#看WEB3行情", "苯乙烯", "WEB3 里的 EB"),
    ("牌号FeMn68Si18（锰含量≥65.0%）", "工业硅", "锰铁牌号里的 Si"),
    ("图片 v2-8faee4762a0bc4b8a2d9eb15479670d4", "苯乙烯", "URL 哈希里的 eb"),
    # 闸2: 后跟数字但不是合约(位数不符)
    ("机构MA5净空19,336手", "甲醇", "MA5 是均线, 不是 MA 合约"),
    ("天弘储能电池指数C 我加2000元", "玉米", "基金份额类别 C"),
    ("国投瑞银白银期货(LOF)C(OTCFUND|019005)", "玉米", "(LOF)C 基金后缀"),
    # 闸3a: 单字母不在合约位置
    ("深V让我们继续火热，但也要警惕", "PVC", "深V 是形状"),
    ("希望来个日内M头去做空", "豆粕", "M头 是技术形态"),
    ("本轮走势：小型W底筑底→转换M顶承压", "豆粕", "M顶 是技术形态"),
    ("他M这叫纯玩人品种", "豆粕", "脏话里的 M"),
    ("llm生成10Y国债期货技术分析报告", "豆油", "10Y 是期限"),
    ("花了3个小时才搞定的。（见P1）", "棕榈油", "P1 是页码"),
    ("L.M.S 4.0.5最新持仓", "塑料", "L.M.S 是系统名"),
    ("GB/T4008—2008牌号", "10年期国债期货", "国标号里的 T"),
    ("沿用这套分时做T逻辑，逐步做波段", "10年期国债期货", "做T 是交易术语"),
    ("期货可以多可以空，T+0，本身是非常好的工具", "10年期国债期货", "T+0 是交易机制"),
    ("香港歌手Gareth.T 汤令山推出单曲", "10年期国债期货", "歌手名里的 T"),
    ("ICE白糖期货跌1.49%。I CBOT大豆期货跌0.75%", "铁矿石", "英文单词 I"),
    ("货源充足，实则精准匹配难※J[送心]", "焦炭", "表情符里的 J"),
    ("关于Al brooks的pa，我最开始也不懂", "铝", "人名 Al Brooks"),
]


@pytest.mark.parametrize("text,forbidden,note", FALSE_POSITIVES)
def test_false_positive_rejected(ner, text, forbidden, note):
    got = names(ner, text)
    assert forbidden not in got, f"误标未拦住: {note} -> 标出了 {forbidden} (got={got})"


# ------------------------------------------------- 2. 合约代码位置必须仍能命中
CONTRACTS = [
    ("M2601 合约大涨", "豆粕", "豆粕合约"),
    ("C1200卖方期权有没有危险", "玉米", "玉米期权"),
    ("SH2601 烧碱合约", "烧碱", "烧碱合约"),
    ("AP610 苹果合约", "苹果", "苹果合约"),
    ("TA609 合约", "PTA", "PTA 合约"),
    ("T2603 国债合约", "10年期国债期货", "国债合约"),
    ("JM2601 焦煤", "焦煤", "焦煤合约"),
    ("FU2609 燃油", "燃料油", "燃料油合约"),
    ("SA2609 纯碱", "纯碱", "纯碱合约"),
    ("P2609 棕榈油", "棕榈油", "棕榈油合约"),
]


@pytest.mark.parametrize("text,want,note", CONTRACTS)
def test_contract_position_still_matches(ner, text, want, note):
    """矫枉过正的守卫: 收紧后真实的合约写法不能跟着一起丢。"""
    got = names(ner, text)
    assert want in got, f"{note} 被误拒了: {text!r} -> {got}"


# --------------------------------------------- 3. 真信号不得被误伤
TRUE_POSITIVES = [
    ("中国进口原油在加速", "原油", "中文别名"),
    ("跟pta，甲醇，玻璃期货", "PTA", "中文紧邻的小写 pta"),
    ("看多sc，做多原油", "原油", "中文紧邻的小写 sc"),
    ("lpg等原油板块期货", "LPG", "小写 lpg"),
    ("东证席位利用px来操纵市场", "对二甲苯", "小写 px"),
    ("OPEC 减产会改变原油供应", "原油", "OPEC -> 原油"),
    ("PP、PE 等下游烯烃成品行情", "PP", "英文缩写列举"),
    ("玻璃FG多空双加分歧", "玻璃", "玻璃+FG"),
    ("短纤、NR 国内商品期货早盘开盘", "20号胶", "NR -> 20号胶"),
    ("黄金和白银是最大误区 AU和AG价格相关", "黄金", "AU 双字母代码"),
    ("PVC，苯乙烯，聚丙烯，液化气", "PVC", "多品种列举"),
    ("铝土矿出口支撑散货船队", "氧化铝", "铝土矿 -> 氧化铝"),
    ("液化石油气现货走强", "LPG", "中文全称"),
    ("甲醇609(CZCE|MA610) 现货7500", "甲醇", "中文+管道符合约"),
]


@pytest.mark.parametrize("text,want,note", TRUE_POSITIVES)
def test_true_positive_kept(ner, text, want, note):
    got = names(ner, text)
    assert want in got, f"真信号被误伤: {note} 应标出 {want}, 实际 {got}"


# ------------------------------------------------- 4. 结构性: 逻辑只允许有一份
def _code_only() -> str:
    """剥掉整行注释后的源码。

    【为什么必须剥】说明性注释里会原样提到被测模式, 不剥会被自己的注释命中
    —— 源码文本断言的自指陷阱(本项目已踩过一次)。
    """
    src = (_VALIDATE / "ner.py").read_text(encoding="utf-8")
    return "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))


def test_two_matchers_share_one_predicate():
    """两处匹配(extract / extract_per_variety_context)必须共用同一判定函数。

    上轮教训: 两处同逻辑, 只改一处 => 一个函数修好了、另一个还在误标,
    且两边行为悄悄分叉。这里用"调用点计数"把结构锁死。
    """
    code = _code_only()
    call = "if not _latin_alias_ok(hay, idx, alias):"
    assert code.count(call) == 2, (
        f"应有两处调用同一判定函数, 实际 {code.count(call)} 处 —— "
        "新增匹配函数时漏接闸门, 或又有人把逻辑复制成了两份"
    )


def _function_body(code: str, header: str) -> str:
    """切出某个方法的函数体(到下一个顶层定义为止)。"""
    lines = code.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if header in l)
    except StopIteration:
        return ""
    out = []
    for l in lines[start + 1:]:
        if l.strip() and not l.startswith((" ", "\t")):
            break  # 顶层 def/class, 说明函数体结束
        out.append(l)
    return "\n".join(out)


def test_no_inlined_boundary_logic_left():
    """两个匹配循环里不得再出现内联的字符判定 —— 闸门逻辑必须收在 _latin_alias_ok 里。

    ⚠️ 这里刻意**不查具体变量名**(如 before/after)。首版写成
      `assert "before.isascii()" not in code`, 变异测试把内联判断的变量
      改名为 `_b0` 就绕过去了(护栏报绿) —— 断言绑定了实现细节。
      改成"按函数体范围查 .isascii()", 无论变量叫什么名字都拦得住。
    """
    code = _code_only()
    loops = ("def extract(self", "def extract_per_variety_context(self")
    for header in loops:
        body = _function_body(code, header)
        assert body, f"没定位到 {header} 的函数体 —— 测试本身失效了"
        assert ".isascii()" not in body, (
            f"{header} 的匹配循环里出现内联字符判定 —— 闸门逻辑被复制回循环里了, "
            "两处必然再次漂移; 应当只调用 _latin_alias_ok()"
        )


def test_gate_helper_is_module_level_pure_function():
    """闸门必须是模块级纯函数(无 self), 这样两处调用行为才天然一致。"""
    mod = _load_ner()
    assert callable(getattr(mod, "_latin_alias_ok", None)), "闸门函数不存在"
    src = _code_only()
    assert "def _latin_alias_ok(hay: str, idx: int, alias: str) -> bool:" in src


# ------------------------------------------------- 5. 两副本一致
def test_dev_and_prod_copies_identical():
    dev = (_VALIDATE / "ner.py").read_bytes()
    prod = (_PROD_VALIDATE / "ner.py")
    if not prod.exists():
        pytest.skip("运行副本不存在(非双副本环境)")
    assert dev == prod.read_bytes(), "ner.py 两副本不一致 —— 改完必须同步到运行副本"
