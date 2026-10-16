# -*- coding: utf-8 -*-
"""方案 B：只扩机构名归一表，收敛「同一机构的多种写法/渠道前缀」（2026-10-14 用户拍板）

═══ 用户诉求 ═══
「同品种隆众有多口径…不要重复性分析录入」+ 本次发现的「公众号渠道标签被 LLM 发行方覆盖」
⇒ 用户选择**方案 B：只加归一规则（界面显示干净机构名）**，不改 `source` 写入逻辑。

═══ 为什么只加规则就够 ═══
聚合层 `_merge_same_source_items` 本就按 **(品种代码, 归一后发行方)** 合并成一条
（分组键与显示键同源），且前端观点表格在**读时**调 `_normalize_source`
⇒ 加规则即**立刻生效、历史行一并覆盖**，零 DB 改动、零 LLM、零重跑。

═══ 实测缺口（全库扫描，见扫描输出）═══
  公众号-多空逻辑(13) / 公众号-混沌天成研究(2) + 混沌天成研究(2) + 混沌天成研究院(1)
  ← 同一个号「混沌天成研究」在库里裂成 **3 种写法**
  恒力期货研究院(2) / 紫金天风期货研究所 / 物产中大期货研究 / 中信建投期货研究
  ← 微信读书通道已接入这 6 个号，**未来还会继续入库** ⇒ 预先加规则，避免再裂
  （`中信期货研究` 已被既有 `("中信期货", …)` 覆盖；`中信建投期货研究` **不含**子串
   "中信期货"（中-信-建-投-期-货 ≠ 中-信-期-货）⇒ 必须单独加，且不会互相误伤。）

═══ 刻意**不**归一的三类（语义不同，合并会改变含义）═══
  ① `本地导入-研报暂存`(91) / `永安-研报暂存`(63) / `中信-研报暂存`(18)
     —— 这是**本地导入的暂存目录**，不是"机构已确认"的正式来源；
  ② `发现报告`(7) —— **聚合平台**（其 `发现报告-东证期货` 已被既有规则归到东证）；
  ③ `上传`(2) / `(NULL)`(1) —— 兜底值。
  已核对：上述三类**均不含**本次新增的任何键 ⇒ 不会被误伤。

⚠️ 键要取**够短以覆盖全部变体、又够长以不误伤**：
   `混沌天成研究院` **不含**子串 "混沌天成研究"（是 …研究院）⇒ 必须用 `混沌天成` 才能覆盖。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WA = ROOT / "web_app.py"

OLD = '    ("隆众资讯", "隆众资讯"),\r\n)'
NEW = (
    '    ("隆众资讯", "隆众资讯"),\r\n'
    '    # 【同源归并 2026-10-14 方案 B】公众号渠道 + 机构多写法收敛。\r\n'
    '    #   用户报「不要重复性分析录入」并选定**方案 B:只加归一规则、界面显示干净机构名**\r\n'
    '    #   （不改 source 写入逻辑 —— 那条会影响所有通道,含手动上传 PDF 的发行方自动识别）。\r\n'
    '    #   为什么只加规则就够:聚合层 `_merge_same_source_items` 本就按\r\n'
    '    #   (品种, 归一后发行方) 合并;前端观点表格在读时调 `_normalize_source`\r\n'
    '    #   ⇒ 加规则**立刻生效且历史行一并覆盖**,零 DB 改动/零 LLM/零重跑。\r\n'
    '    #   实测缺口:同一个号「混沌天成研究」在库里裂成 3 种写法\r\n'
    '    #   (公众号-混沌天成研究 / 混沌天成研究 / 混沌天成研究院)。\r\n'
    '    #   ⚠️键取法:`混沌天成研究院` **不含**子串 "混沌天成研究"(是 …研究院)\r\n'
    '    #     ⇒ 必须用 `混沌天成` 才能一次覆盖全部变体。\r\n'
    '    #   以下 6 个号已由微信读书通道接入,**未来仍会持续入库** ⇒ 预先归一避免再裂。\r\n'
    '    ("混沌天成", "混沌天成"),\r\n'
    '    ("恒力期货", "恒力期货"),\r\n'
    '    ("紫金天风", "紫金天风"),\r\n'
    '    ("物产中大", "物产中大"),\r\n'
    '    ("中信建投期货", "中信建投"),  # 用带"期货"的完整键:不误伤"中信建投证券"等其它法人\r\n'
    '    ("多空逻辑", "多空逻辑"),\r\n'
    '    # ⚠️刻意**不**归一的(语义不同,合并会改变含义):\r\n'
    '    #   `本地导入-研报暂存`/`永安-研报暂存`/`中信-研报暂存` = 本地导入暂存目录,\r\n'
    '    #     不是"机构已确认"的正式来源;`发现报告` = 聚合平台;\r\n'
    '    #   `上传`/空 = 兜底值。已核对它们均不含上述任何键 ⇒ 不会被误伤。\r\n'
    ')'
)

# 误伤自检：这些必须**原样返回**（不被并进任何新标准名）
MUST_KEEP = [
    "本地导入-研报暂存", "永安-研报暂存", "中信-研报暂存", "发现报告",
    "上传", "宝城期货", "瑞达期货股份有限公司",
    "中信建投证券", "紫金矿业", "物产环能",
]
# 归一自检：这些必须**落到期望标准名**
MUST_MAP = {
    "公众号-多空逻辑": "多空逻辑",
    "公众号-混沌天成研究": "混沌天成",
    "混沌天成研究": "混沌天成",
    "混沌天成研究院": "混沌天成",
    "恒力期货研究院": "恒力期货",
    "公众号-恒力期货研究院": "恒力期货",
    "紫金天风期货研究所": "紫金天风",
    "公众号-紫金天风期货研究所": "紫金天风",
    "物产中大期货研究": "物产中大",
    "公众号-物产中大期货研究": "物产中大",
    "中信建投期货研究": "中信建投",
    "公众号-中信建投期货研究": "中信建投",
    # 既有规则不得被破坏
    "中信期货研究": "中信期货",
    "东证期货-繁微MCP": "东证",
    "永安期货研究中心": "永安期货",
    "隆众资讯-聚焦": "隆众资讯",
}


def main() -> int:
    raw = WA.read_bytes()
    assert raw.count(b"\r\n") > 0 and raw.count(b"\n") - raw.count(b"\r\n") == 0, "web_app.py 应为 CRLF"
    txt = raw.decode("utf-8")          # ★ 裸字节解码：read_text 会做换行转换致 CRLF 锚点失配
    h0 = hashlib.md5(raw).hexdigest()

    if '"混沌天成", "混沌天成"' in txt:
        print("⚠️ 已包含本次规则，跳过")
        return 0

    n = txt.count(OLD)
    print("[1] 锚点预检: 命中 %d 次（期望 1）" % n)
    assert n == 1, "锚点不匹配 —— 未写入"

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    bak = ROOT / "backup" / ("web_app_pre_source_norm_b_%s.py" % ts)
    shutil.copy2(WA, bak)
    print("[2] 备份 -> backup/%s" % bak.name)

    WA.write_text(txt.replace(OLD, NEW, 1), encoding="utf-8", newline="")
    print("[3] 已写入")

    # ── 自检（import 真模块验证，而不是文本断言）────────────────
    print("[4] 行为自检")
    sys.path.insert(0, str(ROOT))
    try:
        from dotenv import load_dotenv
        load_dotenv(str(ROOT / ".env"))
    except Exception:
        pass
    import importlib
    import web_app
    importlib.reload(web_app)
    ns = web_app._normalize_source

    bad = 0
    for raw_name, want in MUST_MAP.items():
        got = ns(raw_name)
        ok = got == want
        bad += 0 if ok else 1
        print("    %s %-26s -> %-12s（期望 %s）" % ("✔" if ok else "✘", raw_name, got, want))
    print("    --- 误伤自检（须原样返回）---")
    for raw_name in MUST_KEEP:
        got = ns(raw_name)
        ok = got == raw_name
        bad += 0 if ok else 1
        print("    %s %-24s -> %s" % ("✔" if ok else "✘", raw_name, got))
    assert bad == 0, "自检失败 %d 项" % bad

    nb = WA.read_bytes()
    assert nb.count(b"\r\n") == raw.count(b"\r\n") + NEW.count("\r\n") - OLD.count("\r\n")
    assert nb.count(b"\n") - nb.count(b"\r\n") == 0, "EOL 被破坏"
    print("    ✔ 规则表完好；EOL 仍 CRLF；md5 %s -> %s" % (h0[:8], hashlib.md5(nb).hexdigest()[:8]))
    print("\n✔ 方案 B 已落盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
