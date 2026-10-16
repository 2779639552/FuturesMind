# -*- coding: utf-8 -*-
"""给 wechat_ingest.py 加「内容噪声过滤」（2026-10-14 用户报障）。

现象：恒力期货研究院的 `【招聘】能化研究员` 被当研报入库（正文 1162 字，能过
      既有的 `MIN_BODY_CHARS=200` 长度关）。
要求：**按内容**筛掉这类，且**宁可留错不可删错**。

三类判据（全部"高精度词 + 组合条件"，实测在真实 inbox 101 个标题上
零误伤、恰好命中 5 条真噪声）：
  ① recruit  招聘：标题招聘词 **且** 正文招聘专用词
  ② announce 公告/通稿：标题含"本身就是公告性质"的词（邀请函/倒计时/成功举办/会议预告…）
  ③ hollow   无实质内容：去套话**且去重复段**后剩不下实质（整篇免责声明的 stub）

⚠️ 两条关键设计（都是实测踩出来的，别"优化"掉）：
  · 免责剥离**不得使用 `.*?` 跨段匹配** —— 在单行长文本上会把正文一起吞掉
    （v1 原型把 4702 字的真研报 `为什么今年铁矿9-1价差…` 判成"实质仅 155 字"）。
  · "实质字数"必须**按唯一片段计量**（重复出现的免责段不计）—— 否则
    `会议预告` 那篇 1024 字（同一段免责声明重复 1.5 遍）会度出 739 字假内容。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "wechat_ingest.py"

# ── ① 常量与纯函数：插在 MAX_WORKERS 常量之后 ─────────────────────────
ANCHOR_CONST = 'MAX_WORKERS = 3           # 【常量】硬上限:留 2 路余量给同账户其他链路(LLM 账户配额 5)\n'

BLOCK = '''

# ══════════════════════════════════════════════════════════════════════════
# 【内容噪声过滤】2026-10-14 用户报障：恒力期货研究院 `【招聘】能化研究员` 被当研报入库
#   （正文 1162 字，能过 MIN_BODY_CHARS 长度关）。本段按**内容**筛掉三类"非投研"文章。
#   设计原则：**宁可留错不可删错** —— 全部是"高精度词 + 组合条件"，不用裸词。
#   反例约束：`铝合金企业调研：票荒倒逼减产…`(4782 字) 含"调研"但是**真研报**
#             （有核心观点+调研汇总），任何把"调研/走访"当噪声词的做法都会误伤它。
# ══════════════════════════════════════════════════════════════════════════
# 招聘：标题词 **且** 正文词同时命中才判（防"从招聘数据看就业"这类真研究被误伤）
RECRUIT_TITLE = ("招聘", "诚聘", "招募", "招人", "求贤", "人才招聘", "社会招聘", "校园招聘")
RECRUIT_BODY = ("岗位职责", "任职要求", "任职资格", "简历", "应聘", "薪酬", "薪资",
                "福利待遇", "五险一金", "学历要求", "本科及以上", "硕士及以上",
                "工作地点", "招聘人数", "投递", "面试", "岗位要求", "职位描述")

# 公告与通稿：这些词**本身**就是公告/回顾的性质 ⇒ 标题命中即判（不必再看内容）
ANNOUNCE_TITLE = ("邀请函", "倒计时", "即将开幕", "隆重开幕", "即将举办", "即将召开",
                  "即将启幕", "启幕", "成功举办", "圆满举办", "圆满落幕", "盛大举行",
                  "盛大开幕", "直播预告", "路演预告", "会议预告", "活动预告")
# 活动词：可能出现在正经研报标题里（如"某论坛观点集锦"）⇒ 必须**正文通稿特征**确认
EVENT_TITLE = ("策略会", "培训会", "沙龙", "论坛", "峰会", "研讨会", "宣讲会", "发布会")
EVENT_BODY = ("与会嘉宾", "致辞", "会议现场", "圆桌", "闭门", "主办", "协办", "承办",
              "签到", "参会", "报名截止", "扫码报名", "直播链接", "敬请期待",
              "欢迎参加", "诚挚邀请", "座无虚席", "反响热烈", "出席并", "发表主旨演讲")

MIN_SUBSTANCE_CHARS = 150  # 【常量】"实质内容"最小字数(去套话+去重复段后)

# 免责声明/引导语 —— **只列固定短语**。⚠️ 严禁在此处使用 `.*?`：那会在单行长文本上
# 跨段吞掉正文（v1 原型据此把 4702 字真研报判成"实质 155 字"，属实测事故）。
_BOILERPLATE_PHRASES = (
    "点击上方关注", "点击蓝字", "点击上方", "点击", "关注我们", "欢迎订阅", "扫码关注",
    "重要提示", "免责声明", "风险提示", "符合证监会适当性管理规定", "证监会适当性管理",
    "证监许可", "期货交易咨询业务资格", "投资咨询业务资格", "版权所有", "违者必究",
    "— END —", "END", "来源：", "阅读全文", "了解更多资讯", "关注“", "”公众号",
    "本报告观点和信息仅供", "符合证监会适当性管理规定的期货交易者参考",
    "本平台暂时无法设置访问限制", "请勿点击查看或使用本报告任何信息",
    "对由此给您造成的不便表示诚挚歉意", "感谢您的理解与配合",
    "据此操作", "责任自负", "不构成投资建议", "仅供参考", "不因任何订阅",
    "准确性和完整性", "不作出任何保证", "不保证", "发生任何变更",
    "其它媒体平台接收", "翻版", "复制或刊发", "不得对本报告进行任何增删或修改",
    "不承担任何形式的责任", "报告完成日期", "作者：", "联系方式：",
    "从业资格证号", "交易咨询证号", "编辑：", "审核：", "风险因子：",
)
_BOILERPLATE_RE = re.compile(r"未经[^\\s]{0,6}许可")


def strip_boilerplate(text: str) -> str:
    """剥离免责声明/引导语(固定短语),返回剩余正文。

    ⚠️ 只做**固定短语**删除,不得用 `.*?` 跨段替换 —— 见上方常量处的事故说明。
    """
    out = text or ""
    for p in _BOILERPLATE_PHRASES:
        out = out.replace(p, " ")
    out = _BOILERPLATE_RE.sub(" ", out)
    out = re.sub(r"\\s+", " ", out)
    return out.strip()


def _seg_key(s: str) -> str:
    return re.sub(r"[\\s\\W_]+", "", s)


def substance_len(text: str) -> int:
    """实质内容字数 = 去套话后「**文内只出现一次**」的句子总字数。

    【为什么要按唯一片段计量】免责声明在文内会**重复出现**（`会议预告` 那篇 1024 字
    其实是同一段免责重复 1.5 遍）。若只做固定短语删除，会度出 739 字假内容 ⇒ 漏判。
    实测分布：真噪声 71 字 / 下一条真研报 240 字，**3.4 倍间隔**，阈值可靠。
    """
    s = strip_boilerplate(text)
    segs = re.split(r"[。！？；!?;]+", s)
    keys = [_seg_key(x) for x in segs]
    cnt = {}
    for k in keys:
        if len(k) >= 6:
            cnt[k] = cnt.get(k, 0) + 1
    total = 0
    for seg, k in zip(segs, keys):
        if len(k) < 6:
            total += len(seg)          # 残句/小标题:一律计入
        elif cnt.get(k, 0) == 1:
            total += len(seg)          # 只出现一次的才算实质
    return total


def noise_reason(title: str, text: str) -> str:
    """判断是否"内容非投研"。返回 '' 表示保留;否则返回原因(用于日志/统计)。

    三类：recruit 招聘 / announce 公告通稿 / hollow 无实质内容。
    """
    t, x = (title or ""), (text or "")
    if any(w in t for w in RECRUIT_TITLE):
        rb = [w for w in RECRUIT_BODY if w in x]
        if rb:
            return "recruit(标题招聘词+正文招聘词 %d 个: %s)" % (len(rb), ",".join(rb[:4]))
    for w in ANNOUNCE_TITLE:
        if w in t:
            return "announce(标题公告词: %s)" % w
    if any(w in t for w in EVENT_TITLE):
        eb = [w for w in EVENT_BODY if w in x]
        if len(eb) >= 3:
            return "event(标题活动词+正文通稿特征词 %d 个: %s)" % (len(eb), ",".join(eb[:4]))
    sub = substance_len(x)
    if sub < MIN_SUBSTANCE_CHARS:
        return "hollow(实质仅 %d 字, 全文 %d 字)" % (sub, len(x))
    return ""

'''

# ── ② ingest_one 的 docstring：补一条返回口径 ─────────────────────────
ANCHOR_DOC = (
    "            'short'   正文过短,跳过\n"
    "            'empty'   没正文(通常是采集时带了 --no-fetch),跳过\n"
    "            'nokey'   无去重键,跳过(无法保证幂等,宁可不动)\n"
)
DOC_NEW = (
    "            'short'   正文过短,跳过\n"
    "            'empty'   没正文(通常是采集时带了 --no-fetch),跳过\n"
    "            'noise'   内容非投研(招聘/活动公告通稿/整篇免责声明),跳过\n"
    "            'nokey'   无去重键,跳过(无法保证幂等,宁可不动)\n"
)

# ── ③ 正文长度关之后插入噪声关 ───────────────────────────────────────
ANCHOR_GATE = (
    "    if len(text) < MIN_BODY_CHARS:\n"
    "        print(\"    ! 正文过短(%d 字符 < %d),跳过: %s\" % (len(text), MIN_BODY_CHARS, title[:46]))\n"
    "        return \"short\"\n"
)
GATE_NEW = ANCHOR_GATE + (
    "\n"
    "    # 【内容噪声关】2026-10-14 用户报障:招聘/活动通稿被当研报入库。\n"
    "    #   放在\"长度关\"之后:长度关挡的是空壳(0~40 字的预告),本关挡的是\n"
    "    #   **正文够长但内容不是投研**的(如 1162 字的【招聘】)。\n"
    "    _noise = noise_reason(title, text)\n"
    "    if _noise:\n"
    "        print(\"    ! 内容非投研(%s),跳过: %s\" % (_noise, title[:46]))\n"
    "        return \"noise\"\n"
)

# ── ④ stats 增桶 ─────────────────────────────────────────────────────
ANCHOR_STATS = '    stats = {"ok": 0, "okerr": 0, "retry": 0, "retryerr": 0,\n             "dup": 0, "short": 0, "empty": 0, "nokey": 0, "err": 0}\n'
STATS_NEW = '    stats = {"ok": 0, "okerr": 0, "retry": 0, "retryerr": 0,\n             "dup": 0, "short": 0, "empty": 0, "nokey": 0, "noise": 0, "err": 0}\n'

# ── ⑤ 空结果分支 + 汇总文案 + 结果文件 ────────────────────────────────
ANCHOR_EMPTY = '            "skipped": 0, "failed": 0,\n'
EMPTY_NEW = '            "skipped": 0, "noise": 0, "failed": 0,\n'

ANCHOR_SUM = (
    '    print("【汇总】新入库 %d 篇（有效观点 %d 篇 / 池外或非投研 %d 篇，均已在库留痕）| "\n'
    '          "重跑 processing 残留 %d 篇（有效 %d 篇）| "\n'
    '          "跳过(已入库 %d / 无正文 %d / 正文过短 %d / 无键 %d) | 失败 %d"\n'
    '          % (inserted, usable, stats["okerr"] + stats["retryerr"],\n'
    '             retried, stats["retry"],\n'
    '             stats["dup"], stats["empty"], stats["short"], stats["nokey"], stats["err"]))\n'
)
SUM_NEW = (
    '    print("【汇总】新入库 %d 篇（有效观点 %d 篇 / 池外或非投研 %d 篇，均已在库留痕）| "\n'
    '          "重跑 processing 残留 %d 篇（有效 %d 篇）| "\n'
    '          "跳过(已入库 %d / 无正文 %d / 正文过短 %d / 内容噪声 %d / 无键 %d) | 失败 %d"\n'
    '          % (inserted, usable, stats["okerr"] + stats["retryerr"],\n'
    '             retried, stats["retry"],\n'
    '             stats["dup"], stats["empty"], stats["short"], stats["noise"],\n'
    '             stats["nokey"], stats["err"]))\n'
)

ANCHOR_RESULT = (
    '        "skipped": stats["dup"] + stats["empty"] + stats["short"] + stats["nokey"],\n'
    '        "failed": stats["err"],\n'
)
RESULT_NEW = (
    '        "skipped": (stats["dup"] + stats["empty"] + stats["short"]\n'
    '                    + stats["noise"] + stats["nokey"]),\n'
    '        "noise": stats["noise"],\n'
    '        "failed": stats["err"],\n'
)

EDITS = [
    ("① 常量与纯函数", ANCHOR_CONST, ANCHOR_CONST + BLOCK, 1),
    ("② docstring 返回口径", ANCHOR_DOC, DOC_NEW, 1),
    ("③ 正文长度关后插入噪声关", ANCHOR_GATE, GATE_NEW, 1),
    ("④ stats 增 noise 桶", ANCHOR_STATS, STATS_NEW, 1),
    ("⑤a 空结果分支", ANCHOR_EMPTY, EMPTY_NEW, 1),
    ("⑤b 汇总文案", ANCHOR_SUM, SUM_NEW, 1),
    ("⑤c 结果文件 skipped/noise", ANCHOR_RESULT, RESULT_NEW, 1),
]


def main() -> int:
    raw = TARGET.read_bytes()
    assert raw.count(b"\r\n") == 0, "wechat_ingest.py 应为 LF（实测非 LF，停止）"
    txt = raw.decode("utf-8")
    print("md5(前) =", hashlib.md5(raw).hexdigest())
    print()
    print("[1] 预检锚点唯一性")
    for tag, old, _new, want in EDITS:
        n = txt.count(old)
        print("    %-24s 命中 %d（期望 %d）%s" % (tag, n, want, "" if n == want else "  ✘"))
        assert n == want, "%s 锚点命中 %d（期望 %d）" % (tag, n, want)

    print()
    print("[2] 应用")
    for tag, old, new, _want in EDITS:
        txt = txt.replace(old, new, 1)
        print("    ✔ %s" % tag)

    TARGET.write_text(txt, encoding="utf-8", newline="")
    raw2 = TARGET.read_bytes()
    assert raw2.count(b"\r\n") == 0 and raw2.count(b"\n") > 0, "EOL 被破坏"
    print()
    print("[3] 自检")
    t2 = raw2.decode("utf-8")
    checks = [
        ("noise_reason 已定义", t2.count("def noise_reason(") == 1),
        ("substance_len 已定义", t2.count("def substance_len(") == 1),
        ("strip_boilerplate 已定义", t2.count("def strip_boilerplate(") == 1),
        ("噪声关已插入", t2.count('return "noise"') == 1),
        ("stats 有 noise 桶", t2.count('"noise": 0,') == 1),
        ("结果文件带 noise", t2.count('"noise": stats["noise"],') == 1),
        ("汇总文案含内容噪声", t2.count("内容噪声 %d") == 1),
        ("免责剥离无 .*? 跨段", "未经.*许可" not in t2),
        ("原长度关保留", t2.count('return "short"') == 1),
    ]
    ok = True
    for tag, v in checks:
        print("    %-24s %s" % (tag, "✔" if v else "✘"))
        ok = ok and v
    print()
    print("md5(后) =", hashlib.md5(raw2).hexdigest())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
