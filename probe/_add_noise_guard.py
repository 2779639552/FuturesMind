# -*- coding: utf-8 -*-
"""写「内容噪声过滤」回归护栏 + 修掉 since 测试里的重复定义。

【顺带修的两处】
  ① `tests/test_wechat_ingest_since.py` 里 `test_state_dir_redirects_result_file`
     被**逐字节重复定义了两遍**（后者覆盖前者，前者是死代码 —— 上一轮我追加时
     没发现已有一条同名）。本次去重，并把它强化为"**真实结果文件不被改动**"
     （原来只断言"测试目录里出现了文件"，没断言"真实目录没被动过"，而后者才是
     那个 bug 的真正危害）。
  ② 新护栏把三条判据 + 两条实测事故 + 真实语料冻结名单全部锁住。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SINCE = ROOT / "tests" / "test_wechat_ingest_since.py"
GUARD = ROOT / "tests" / "test_wechat_ingest_noise.py"

MARKER = "def test_state_dir_redirects_result_file(state_dir):"

NEW_FUNC = '''def test_state_dir_redirects_result_file(state_dir):
    """★ `--state-dir` 必须连**结果文件**一起重定向,且**真实文件不得被动过**。

    【为什么单列一条】原先 `main()` 的 `global` 只列了
    `STATE_DIR, F_NEW, F_INBOX, F_COLLECT` —— **漏了 `F_RESULT`**，
    于是结果文件仍写到**真实** `~/.tradingagents/wechat/`。
    后果（2026-10-14 实测）：测试跑一次就把真实结果文件从
    「新入库 9 篇」改写成「2 篇 / dry_run=true」，而**调度器正是读这个文件报数**
    ⇒ 计数被静默污染。任何带 `--state-dir` 的测试/回填都会中招。

    ★ 两条判据缺一不可：
      (a) 测试目录里出现了结果文件（说明重定向生效）；
      (b) **真实结果文件的 md5 与内容没变**（说明没污染）。
    只做 (a) 是不够的 —— v1 版本的用例就只断言了 (a)。
    """
    real = Path.home() / ".tradingagents" / "wechat" / "wechat_ingest_result.json"
    before = real.read_bytes() if real.is_file() else None

    _run(state_dir, "--since", "2026-10-14")

    got = state_dir / "wechat_ingest_result.json"
    assert got.is_file(), (
        "--state-dir 未重定向结果文件 —— 它会写到真实 ~/.tradingagents/wechat/，"
        "污染调度器读到的计数"
    )
    payload = json.loads(got.read_text(encoding="utf-8"))
    assert payload.get("source") == "inbox"
    assert payload.get("dry_run") is True, "dry-run 运行应如实记录 dry_run"

    after = real.read_bytes() if real.is_file() else None
    assert after == before, (
        "★ 真实结果文件被本次测试改动了（md5 %s -> %s）—— "
        "`--state-dir` 必须把 F_RESULT 一起重定向"
        % (hashlib.md5(before).hexdigest()[:8] if before else None,
           hashlib.md5(after).hexdigest()[:8] if after else None)
    )

'''

GUARD_SRC = '''"""公众号入库「内容噪声过滤」回归护栏（2026-10-14 用户报障）。

【现象】恒力期货研究院 `【招聘】能化研究员`（正文 1162 字）被当研报入库。
        既有的长度关（`MIN_BODY_CHARS=200`）挡不住它 —— 正文确实很长（招聘启事
        信息量不小），必须**按内容**判。

【本文件锁死】
  A. 三类判据各自生效：recruit 招聘 / announce 公告通稿 / hollow 无实质内容；
  B. ★ 误伤红线：真研报**一条都不许被丢** —— 尤其 `铝合金企业调研：票荒倒逼减产…`
     （4782 字、含"调研"4 次）是**真研报**（有核心观点 + 调研汇总）。任何把
     "调研/走访"当噪声词的实现都会挂在 B 上。
  C. ★ 两条**实测事故**不得复发：
     · 免责剥离**不得用 `.*?` 跨段匹配** —— v1 原型据此把 4702 字的真研报
       `为什么今年铁矿9-1价差低位但难以大幅走扩` 吞成"实质 155 字"；
     · "实质字数"必须按**唯一片段**计量 —— 否则 `【建投能化|月度策略荟萃】会议预告`
       那篇（1024 字其实是同一段免责声明重复 1.5 遍）会度出 739 字假内容而漏判。
  D. 真实语料回归：拿线上 inbox 的真实文章跑一遍，按**冻结名单**逐条核对。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wechat_ingest import (
    MIN_SUBSTANCE_CHARS,
    noise_reason,
    strip_boilerplate,
    substance_len,
)

ROOT = Path(__file__).resolve().parent.parent
REAL_INBOX = Path.home() / ".tradingagents" / "wechat" / "wechat_inbox.jsonl"

# ── 一份"像研报"的正文（无重复句，全部会被计入实质内容）─────────────────
RESEARCH_BODY = (
    "核心观点：本周原油供应先行回暖，化工板块利润修复。"
    "华东港口原油库存环比下降三点二万吨至五十八点六万吨，去库斜率好于季节性。"
    "基差走强至每吨正一百二十元，近月合约呈现明显反向结构。"
    "供应端，PX开工率提升至百分之七十八，PTA加工费回落至每吨三百二十元。"
    "需求端，聚酯负荷维持高位，长丝库存天数降至八天。"
    "预计短期价格震荡偏强，策略上建议关注旺季去库斜率并维持逢低偏多思路。"
)
# 活动**通稿**正文：会议报道特征的词 ≥3 个
EVENT_BODY = (
    "本次会议由公司研究院主办，合作单位涵盖多家产业机构。"
    "会议现场座无虚席，与会嘉宾围绕产业周期展开讨论。"
    "公司领导出席并致辞，随后多位分析师发表主旨演讲。"
    "圆桌环节由首席分析师主持，闭门交流气氛热烈。"
)
# 整篇免责声明（**同一段重复**，是真实的"空壳文章"形态）
DISCLAIMER = (
    "重要提示：本报告观点和信息仅供符合证监会适当性管理规定的期货交易者参考。"
    "因本平台暂时无法设置访问限制，若您并非符合规定的交易者，为控制交易风险，"
    "请勿点击查看或使用本报告任何信息。对由此给您造成的不便表示诚挚歉意，"
    "感谢您的理解与配合！期货交易咨询业务资格：证监许可〔2011〕1461 号。"
)


# ════════════════════════════════════════════════════════════════════
# A. 招聘
# ════════════════════════════════════════════════════════════════════
RECRUIT_BODY = (
    "恒力期货研究院是恒力集团对大宗商品和金融市场的重要布局，背靠全球领先的"
    "能源、石化资源禀赋，深耕商品与衍生品的投资与研究。工作地点：上海。"
    "招聘岗位：芳烃聚酯研究员（1-2 人）。岗位职责如下："
    "1、负责相关品种基本面及产业链研究，建立并维护数据库；"
    "2、撰写日常研究报告与策略报告；3、协同路演开发产业机构客户。"
    "要求：1、硕士研究生及以上学历；2、化工、金融、经济相关专业；"
    "3、通过期货从业资格考试。招聘人数：1-2 人。请将简历投递至邮箱。"
)


def test_recruit_article_is_filtered():
    """★ 用户报障的那篇：标题有招聘词 + 正文有招聘专用词 ⇒ 必须过滤。"""
    why = noise_reason("【招聘】能化研究员", RECRUIT_BODY)
    assert why.startswith("recruit"), "招聘文章未被过滤: %r" % why


def test_recruit_title_alone_is_not_enough():
    """只有标题带"招聘"、正文是研究内容的，**不得**被过滤（防过度过滤）。

    `从招聘数据看服务业景气度` 这种真研究标题含"招聘"，若只看标题就会误伤。
    """
    assert noise_reason("从招聘数据看服务业景气度变化", RESEARCH_BODY) == ""


# ════════════════════════════════════════════════════════════════════
# B. 公告 / 通稿
# ════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("title", [
    "邀请函｜风起钱潮——2026股指期货秋季培训会",
    "倒计时1天丨第六届黑色“远洋论剑” 即将在唐山启幕！",
    "第六届物产中大黑色“远洋论剑”在唐山成功举办",
    "【建投能化|月度策略荟萃】会议预告---原油供应先行回暖，化工板块利润修复 2026年9月",
    "【直播预告】油脂油料月月谈",
])
def test_announce_titles_are_filtered(title):
    """★ 标题含"本身就是公告性质"的词 ⇒ 直接过滤（不看内容）。

    这些词的语义就是"公告/回顾"，正文再长也不是投研观点。
    """
    why = noise_reason(title, DISCLAIMER * 3)
    assert why.startswith("announce"), "%s 未被过滤: %r" % (title, why)


def test_event_title_requires_body_confirmation():
    """`论坛/策略会` 这类词可能出现在正经研报标题里 ⇒ 必须**正文通稿特征**确认。"""
    t = "某论坛嘉宾观点集锦"
    assert noise_reason(t, RESEARCH_BODY) == "", "正文是研报时不该仅凭标题过滤"
    assert noise_reason(t, EVENT_BODY).startswith("event"), "通稿正文应被识别"


# ════════════════════════════════════════════════════════════════════
# C. 无实质内容
# ════════════════════════════════════════════════════════════════════
def test_disclaimer_only_stub_is_filtered():
    """整篇都是免责声明（同一段重复）⇒ 过滤。真实样本：`【建投能化|观数】EIA…`。"""
    why = noise_reason("【建投能化|观数】美国EIA周度原油产业链数据解读20261008",
                       DISCLAIMER * 2)
    assert why.startswith("hollow"), "空壳文章未被过滤: %r" % why


def test_short_but_real_note_is_kept():
    """★ 短但有实质内容的研报必须保留。

    真实边界样本：`财政政策展望：存量空间为主，供给高峰渐过`（265 字，
    有"3.18 万亿/1.40 万亿/0.67 万亿"等具体预测）—— 实质 240 字 > 阈值 150。
    这条锁住"阈值不能定太高"。
    """
    body = (
        "财政以存量政策为主，全年供给高峰渐过。四季度财政存量空间充足，"
        "预计出台大规模增量政策的可能性暂不高。供给方面，预计四季度政府债"
        "净融资剩余约三点一八万亿元，十月、十一月、十二月分别约一点四零万亿、"
        "一点一二万亿、零点六七万亿元。政府债净供给高峰渐过，十一月、十二月"
        "供给压力明显减弱。市场也可能提前交易明年长债供给减少。"
    )
    assert substance_len(body) > MIN_SUBSTANCE_CHARS, (
        "本用例样本的实质字数(%d)应高于阈值(%d)，否则它测不到边界"
        % (substance_len(body), MIN_SUBSTANCE_CHARS)
    )
    assert noise_reason("财政政策展望：存量空间为主，供给高峰渐过", body) == ""


# ════════════════════════════════════════════════════════════════════
# D. ★ 两条实测事故不得复发
# ════════════════════════════════════════════════════════════════════
def test_boilerplate_strip_must_not_swallow_body():
    """★★ v1 事故：免责剥离用了 `.*?` 跨段匹配 ⇒ 把整段正文一起吞掉。

    复现条件：**首尾各有一处免责短语、中间夹大段正文**（真实长文就是这个形态）。
    若用 `未经.*许可` 这类跨段正则，中间正文会被整段删掉 ⇒ 真研报被误判"无实质"。
    """
    body = "免责声明 " + RESEARCH_BODY + " 未经书面许可，不得转载。"
    rest = strip_boilerplate(body)
    assert "核心观点" in rest and "逢低偏多" in rest, (
        "免责剥离把正文吞掉了（跨段匹配事故复发）：剩余=%r" % rest[:80]
    )
    assert noise_reason("为什么今年铁矿9-1价差低位但难以大幅走扩", body) == "", (
        "带首尾免责短语的真研报被误判为噪声"
    )


def test_boilerplate_source_has_no_cross_segment_wildcard():
    """源码级兜底：免责剥离段里不得出现 `.*?`（跨段匹配）。"""
    src = (ROOT / "wechat_ingest.py").read_text(encoding="utf-8")
    start = src.index("_BOILERPLATE_PHRASES = (")
    end = src.index("def strip_boilerplate(")
    seg = src[start:end]
    assert ".*?" not in seg, "免责剥离段出现跨段匹配 `.*?` —— 会吞掉正文（见事故记录）"


def test_repeated_segments_not_counted_as_substance():
    """★ 重复出现的段子不得计入"实质内容"（否则重复免责段会造出假内容）。"""
    one = "本报告观点和信息仅供符合规定的交易者参考"
    once = one + "。" + RESEARCH_BODY
    many = (one + "。") * 6 + RESEARCH_BODY
    assert substance_len(many) < substance_len(once) + len(one) * 3, (
        "重复段被计入了实质内容：once=%d many=%d" % (substance_len(once), substance_len(many))
    )


def test_noise_gate_runs_after_length_gate():
    """噪声关必须排在长度关**之后**（长度关先挡 0~40 字的空壳预告）。"""
    src = (ROOT / "wechat_ingest.py").read_text(encoding="utf-8")
    assert src.index('return "short"') < src.index('return "noise"'), (
        "噪声关被放到了长度关之前 —— 会改变既有 short 口径"
    )


def test_stats_and_result_carry_noise_bucket():
    """统计口径：`noise` 必须计入 skipped，并在结果文件里单列（供调度器报数）。"""
    src = (ROOT / "wechat_ingest.py").read_text(encoding="utf-8")
    assert '"noise": 0, "err": 0}' in src, "stats 里没有 noise 桶"
    assert 'stats["noise"] + stats["nokey"]' in src, "skipped 未计入 noise"
    assert '"noise": stats["noise"],' in src, "结果文件未单列 noise"


# ════════════════════════════════════════════════════════════════════
# E. 真实语料回归（冻结名单）
# ════════════════════════════════════════════════════════════════════
MUST_DROP = {
    "【招聘】能化研究员",
    "第六届物产中大黑色“远洋论剑”在唐山成功举办",
    "【建投能化|月度策略荟萃】会议预告---原油供应先行回暖，化工板块利润修复 2026年9月",
    "【建投能化|观数】美国EIA周度原油产业链数据解读20260917",
    "【建投能化|观数】美国EIA周度原油产业链数据解读20261008",
}
MUST_KEEP = {
    "铝合金企业调研：票荒倒逼减产，合规重塑格局",
    "明天要开盘啦，假期外盘哪个品种表现突出？",
    "为什么今年铁矿9-1价差低位但难以大幅走扩",
    "从铜与铁矿比值变化探究背后定价驱动差异",
    "财政政策展望：存量空间为主，供给高峰渐过",
    "年报的跟踪与展望——半年度机构持仓的变动情况",
    "目前大宗商品的估值走到什么位置了？10-13",
    "恒力期货日报20261014",
    "【建投能化|月策合集】原油供应先行回暖，化工板块利润修复2026-10-12",
    "【建投能化|周报合集】海峡复航与地缘风险并行，能化节前震荡为主2026-09-28",
    "【建投能化|深度洞察】天然橡胶&20号胶：周期新篇章，定价新纪元20261006",
    "【建投能化|观数】KPLER全球周度原油库存解读20261012",
    "【半年度报告集锦】有色新能源板块",
    "【能化早评】胡塞威胁攻击沙特能源设施，原油继续震荡上行",
}


def test_real_inbox_frozen_list():
    """★ 拿**线上真实文章**跑一遍，按冻结名单核对（只读，不写任何东西）。

    ⚠️ 若冻结名单命中过少，说明 inbox 被轮换/清空 ⇒ **本用例已失效**，必须重取
    名单，而不是让它静默通过（所以下面断言命中数下限）。
    """
    if not REAL_INBOX.is_file():
        pytest.skip("线上 inbox 不存在（换机器/未采集），跳过真实语料核对")
    rows = [json.loads(l) for l in
            REAL_INBOX.read_text(encoding="utf-8").splitlines() if l.strip()]
    keep_hit = drop_hit = 0
    for r in rows:
        title = (r.get("title") or "").strip()
        text = (r.get("text") or "").strip()
        if len(text) < 200:          # 已被长度关拦住的（0~40 字预告）不参与
            continue
        why = noise_reason(title, text)
        if title in MUST_KEEP:
            keep_hit += 1
            assert not why, "★ 真研报被误伤: %s -> %s" % (title, why)
        if title in MUST_DROP:
            drop_hit += 1
            assert why, "★ 噪声漏网: %s" % title
    assert keep_hit >= 10, (
        "冻结保留名单只命中 %d 条(<10) —— inbox 可能已轮换，本用例失去意义，请重取名单"
        % keep_hit
    )
    assert drop_hit >= 3, "冻结过滤名单只命中 %d 条(<3) —— 同上" % drop_hit
'''


def main() -> int:
    # ── ① 修 since 测试的重复定义 ──────────────────────────────────
    raw = SINCE.read_bytes()
    assert raw.count(b"\r\n") == 0, "tests/*.py 应为 LF"
    s = raw.decode("utf-8")
    i1 = s.index(MARKER)
    i2 = s.index(MARKER, i1 + 1)
    nxt = s.find("\ndef ", i2)
    end = len(s) if nxt == -1 else nxt + 1
    print("[1] 重复定义: 第 1 处 @%d, 第 2 处 @%d（原文件 %d 字符）" % (i1, i2, len(s)))
    s = s[:i2] + s[end:]
    # 用强化版替换保留的那一份
    a = s.index(MARKER)
    b = len(s)
    s = s[:a] + NEW_FUNC
    SINCE.write_text(s, encoding="utf-8", newline="")
    t = SINCE.read_text(encoding="utf-8")
    assert t.count(MARKER) == 1, "仍有 %d 处重复定义" % t.count(MARKER)
    assert "真实结果文件被本次测试改动了" in t
    assert t.count("def test_") == len(set(
        l.split("(")[0] for l in t.splitlines() if l.startswith("def test_"))), "仍有重名用例"
    print("    ✔ 去重完成，用例数 =", t.count("def test_"))

    # ── ② 写新护栏 ─────────────────────────────────────────────────
    GUARD.write_text(GUARD_SRC, encoding="utf-8", newline="")
    g = GUARD.read_bytes()
    assert g.count(b"\r\n") == 0, "tests/*.py 应为 LF"
    print("[2] 新护栏已写:", GUARD.name, "(%d 字节)" % len(g))
    print("    md5 =", hashlib.md5(g).hexdigest()[:12])
    print("    用例数 =", GUARD_SRC.count("def test_"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
