# -*- coding: utf-8 -*-
"""加一条判定：**旧的失败告警是否已被更晚的成功记录推翻**。

【为什么必须加】告警表**只在失败时**写记录 ⇒ 一条历史失败会让界面**一直**显示"中断"。
本项目实测（2026-10-16）：16:15 的失败告警把界面钉在「中断」，
而 17:03 已成功采到 4 篇 + 入库 2 篇 ⇒ 用户看到"还坏着"，会白跑一趟。
状态提示的价值在于"现在怎么样"，不在于"历史上失败过"。

【判据】result 文件的 `ts` **晚于**告警时间 ⇒ 此后又跑过且**没有产生新的失败告警**
⇒ 该条告警不代表当前状态。

⚠ **时间口径**（本项目反复踩的坑）：`alerts.created_at` 是 **UTC**（CURRENT_TIMESTAMP），
  而 result 的 `ts` 是**本地时间**（`datetime.now().isoformat()`）⇒ 必须 **+8 小时对齐**，
  否则"本地 17:03 vs UTC 16:15"会被算成更早，判定恒为假、这条修复静默失效。
"""
from pathlib import Path

P = Path("web_app.py")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "web_app.py 应为纯 CRLF"
t = raw.decode("utf-8")
report = []


def sub(old: str, new: str, tag: str, n: int = 1):
    global t
    o = old.replace("\n", "\r\n")
    assert t.count(o) == n, ("★锚点未命中", tag, t.count(o))
    t = t.replace(o, new.replace("\n", "\r\n"), n)
    report.append("  ✔ %s" % tag)


# ── ① 新增纯函数（可单测）────────────────────────────────────
FUNC = '''def _wechat_alert_superseded(alert, ok_ts: str) -> bool:
    """失败告警是否已被**更晚的采集记录**推翻。

    【为什么需要】告警表只在失败时写记录 ⇒ 一条历史失败会**一直**让界面显示"中断"，
      哪怕之后已经成功采集过（实测：16:15 的失败告警把界面钉在"中断"，
      而 17:03 已成功采到 4 篇 ⇒ 用户以为还坏着）。状态要看"现在"，不是"历史上失败过"。
    【判据】采集结果文件的 `ts` **晚于**告警时间 ⇒ 此后又跑过且**没产生新的失败告警**
      ⇒ 该告警不代表当前状态。
    ⚠ **时间口径**：`alerts.created_at` 是 **UTC**（CURRENT_TIMESTAMP），
      而 result 的 `ts` 是**本地时间** ⇒ 必须 +8 对齐，否则"本地 17:03 vs UTC 16:15"
      会被算成更早、判定恒为假（这条修复会静默失效）。
    """
    import datetime as _d
    if not alert or not ok_ts:
        return False
    ts = str((alert or {}).get("created_at") or "")
    if not ts:
        return False
    try:
        a = _d.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S") + _d.timedelta(hours=8)
        b = _d.datetime.strptime(str(ok_ts)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return False
    return b > a


def _file_mtime_iso(p) -> str:
    """文件最后修改时间（本地）→ ISO 字符串。给"只有文本、没有 ts 字段"的通道用。"""
    import datetime as _d
    try:
        return _d.datetime.fromtimestamp(_os.path.getmtime(p)).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return ""


def _wechat_channel_tri_state('''
sub("def _wechat_channel_tri_state(", FUNC, "① 新增 _wechat_alert_superseded / _file_mtime_iso")

# ── ② 合集通道：陈旧失败告警同样要能被推翻 ─────────────────────
sub('''    alb_fail = _latest("wechat_collect_failed")
    alb_bad = _this_round(alb_fail)''',
    '''    alb_fail = _latest("wechat_collect_failed")
    alb_bad = _this_round(alb_fail)
    # ★ 陈旧告警：该文本结果只有 mtime 没有 ts ⇒ 用 mtime 判"之后又跑过"
    if alb_bad and _wechat_alert_superseded(
            alb_fail, _file_mtime_iso(_os.path.join(W, "album_collect_result.txt"))):
        alb_bad = False''',
    "② 合集通道加推翻判定")

# ── ③ 读书通道 ───────────────────────────────────────────────
sub('''    wr_fail = _latest("wechat_weread_collect_failed")
    wr_bad = _this_round(wr_fail)''',
    '''    wr_fail = _latest("wechat_weread_collect_failed")
    wr_bad = _this_round(wr_fail)
    # ★ 陈旧告警：结果文件里的 ts 比告警更晚 ⇒ 之后已成功跑过（详见函数说明）
    if wr_bad and _wechat_alert_superseded(wr_fail, mp_rep.get("ts")):
        wr_bad = False''',
    "③ 读书通道加推翻判定")

out = t.encode("utf-8")
assert out.count(b"\n") == out.count(b"\r\n"), "写回破坏了 CRLF"
P.write_bytes(out)
b = P.read_bytes()
print("=" * 74)
print("  web_app.py 落盘完成")
print("=" * 74)
print("\n".join(report))
print()
print("  %d → %d 字节；CRLF=%d bareLF=%d"
      % (len(raw), len(b), b.count(b"\r\n"), b.count(b"\n") - b.count(b"\r\n")))
