# -*- coding: utf-8 -*-
"""把 _wechat_channels_status 的两个核心判据抽成模块级纯函数（可单测）。

【为什么】护栏必须能咬住「最近一轮」这个语义 —— 告警表里**只有失败才写记录**
（成功不写），所以"有没有告警"不能直接当状态，必须判"是不是本轮的"。
内联闭包没法从测试注入，故抽成模块级纯函数。

⚠ 本脚本一律用 chr(13)+chr(10) 构造 CRLF，**不写字面转义**，
   避免 bash heredoc / JSON 参数把 \n 吃掉（本轮实测踩过一次）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRITE = "--write" in sys.argv

CR = chr(13)
LF = chr(10)
EOL = CR + LF


def to_crlf(s: str) -> str:
    return s.replace(EOL, LF).replace(LF, EOL)


NEW_FUNCS_LF = '''def _wechat_alert_in_round(alert, base_ts: str) -> bool:
    """★ 某条告警是否属于「最近一轮」。

    【为什么单独抽出来】这是本巡检的**核心判据**：两条通道同轮触发，而告警表里
      **只有失败才写记录**（成功不写）⇒ "有没有告警"不能直接当状态，
      必须看它**是不是本轮的**。否则一次历史失败会把该通道永久标成"中断"。

    【判据】created_at 同为 UTC 字符串（CURRENT_TIMESTAMP），可直接字典序比较。
      base_ts 为空（还没跑过一轮）时，只要告警存在就算本轮
      —— 宁可多提醒一次，也不要把真问题漏掉。
    """
    if not alert:
        return False
    ts = str((alert or {}).get("created_at") or "")
    if not ts:
        return False
    return (not base_ts) or ts >= base_ts


def _wechat_channel_tri_state(failed: bool, new_count) -> str:
    """★ 通道三态：failed(中断) / empty(跑了但无新文) / ok。

    ⚠ 三者**不可混**：empty 是"内容源没新文"（换关键词/等更新），
      failed 是"通道本身坏了"（重登）—— 混成一个"没数据"会让用户**修错东西**
      （去重登一个没坏的账号，或反过来等一个不会自己好的通道）。
      new_count 为 None（没读到结果文件）时按 ok 处理，不误报。
    """
    if failed:
        return "failed"
    if new_count == 0:
        return "empty"
    return "ok"


'''

OLD_CLOSURE_LF = '''    def _this_round(rec) -> bool:
        """该告警是否属于"最近一轮"（created_at 同为 UTC 字符串，可直接比）。"""
        if not rec:
            return False
        ts = str(rec.get("created_at") or "")
        return bool(ts) and (not base or ts >= base)
'''

NEW_CLOSURE_LF = '''    def _this_round(rec) -> bool:
        return _wechat_alert_in_round(rec, base)
'''

OLD3_LF = '        "state": "failed" if alb_bad else ("empty" if alb_new == 0 else "ok"),\n'
NEW3_LF = '        "state": _wechat_channel_tri_state(alb_bad, alb_new),\n'

OLD4_LF = '        "state": "failed" if wr_bad else ("empty" if mp_rep.get("new") == 0 else "ok"),\n'
NEW4_LF = '        "state": _wechat_channel_tri_state(wr_bad, mp_rep.get("new")),\n'


def main() -> int:
    p = ROOT / "web_app.py"
    raw = p.read_bytes()
    assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "EOL 不是纯 CRLF"
    t = raw.decode("utf-8")

    edits = [
        ("A 插入两个纯函数",
         to_crlf('def _wechat_channels_status() -> dict:\n'),
         to_crlf(NEW_FUNCS_LF) + to_crlf('def _wechat_channels_status() -> dict:\n')),
        ("B _this_round 改为复用纯函数", to_crlf(OLD_CLOSURE_LF), to_crlf(NEW_CLOSURE_LF)),
        ("C 合集通道三态", to_crlf(OLD3_LF), to_crlf(NEW3_LF)),
        ("D 读书通道三态", to_crlf(OLD4_LF), to_crlf(NEW4_LF)),
    ]
    report = []
    for tag, old, new in edits:
        c = t.count(old)
        if c != 1:
            print("  ★ [%s] 锚点命中 %d 次（应为 1）" % (tag, c))
            return 2
        t = t.replace(old, new, 1)
        report.append("  ✓ %s" % tag)

    out = t.encode("utf-8")
    assert out.count(b"\n") == out.count(b"\r\n"), "写回破坏 EOL"
    assert "_wechat_alert_in_round" in t and "_wechat_channel_tri_state" in t
    if WRITE:
        p.write_bytes(out)
    print("=== %s ===" % ("落盘" if WRITE else "空跑"))
    print("\n".join(report))
    print("  %d → %d 字节" % (len(raw), len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
