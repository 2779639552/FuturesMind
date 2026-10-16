# -*- coding: utf-8 -*-
"""补护栏（2026-10-14 用户两项要求）

① 隆众「多口径」归并 —— 追加到既有 `tests/test_research_source_merge.py`
   （该文件本就锁 `_normalize_source` + `_merge_same_source_items`，是同源归并的护栏，
   不另起炉灶）。
② 公众号入库「发布日过滤 `--since`」—— 新建 `tests/test_wechat_ingest_since.py`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T_MERGE = ROOT / "tests" / "test_research_source_merge.py"
T_SINCE = ROOT / "tests" / "test_wechat_ingest_since.py"

# ══════════════════════════════════════════════════════════════════
# ① 隆众归一：追加到参数化列表
# ══════════════════════════════════════════════════════════════════
M_OLD = '''    ("中信期货", "中信期货"),
    ("中信期货研究所", "中信期货"),
    # 不在规则内的机构原样返回
'''
M_NEW = '''    ("中信期货", "中信期货"),
    ("中信期货研究所", "中信期货"),
    # 【2026-10-14 扩充】隆众:同一品种在多个「口径」下重复出现(用户报"不要重复性录入")。
    #   成因:采集端只有一个源,`account`(类别)由**标题前缀**派生(`[隆众聚焦]`→聚焦…),
    #   入库拼成 `隆众资讯-<类别>` ⇒ 被当成多家机构 ⇒ 观点总表同品种出现多行。
    ("隆众资讯", "隆众资讯"),
    ("隆众资讯-聚焦", "隆众资讯"),
    ("隆众资讯-聚乙烯", "隆众资讯"),
    ("隆众资讯-成品油", "隆众资讯"),
    ("隆众资讯-早读", "隆众资讯"),
    # 不在规则内的机构原样返回
'''

M_APPEND = '''

def test_normalize_source_longzhong_not_over_merged():
    """隆众规则用**带「资讯」的完整键** ⇒ 裸「隆众」与其它含"隆众"的公司不被误并。

    ⚠️ 判据必须是「**被改成了** 隆众资讯、但输入本身不是隆众」，不能写成
    "结果里含『隆众』" —— 后者会把"原样返回的 `隆众XX其它公司`"也误报成归并
    （本轮自检时我自己踩过这个假报警）。
    """
    for raw in ("隆众", "隆众XX其它公司", "隆众能源"):
        got = web_app._normalize_source(raw)
        assert got == raw, "%s 被误并成 %s" % (raw, got)


def test_merge_longzhong_multi_kou_jing_becomes_one_row():
    """★ 用户 2026-10-14 报障：同一品种隆众有多个口径（隆众资讯 / 隆众资讯-聚焦），
    观点总表出现多行。归一后必须合并成**一条**，而不是各占一行。
    """
    recs = [
        _rec(rid=1, code="RU", source="隆众资讯", direction="看多"),
        _rec(rid=2, code="RU", source="隆众资讯-聚焦", direction="看多"),
    ]
    # 生产链路是先归一、再交给合并 helper（见 web_app 里 recs 的构造处）
    for r in recs:
        r["source"] = web_app._normalize_source(r["source"])
    out = web_app._merge_same_source_items(recs, flatten=True)
    assert len(out) == 1, "隆众多口径未合并成一条 —— 用户报的重复行仍在"
    assert out[0]["merged_count"] == 2, "merged_count 应为 2"
    assert len(out[0]["titles"]) == 2, "两篇标题都应保留(不丢内容)"
'''

# ══════════════════════════════════════════════════════════════════
# ② 新建 --since 护栏
# ══════════════════════════════════════════════════════════════════
SINCE_TEST = '''"""公众号入库「发布日过滤 `--since`」回归护栏（2026-10-14）。

【为什么要这条】入库器按 `create_time` **最旧优先** + `--limit 20`。微信读书通道
初始化时把 118 篇历史基线灌进 inbox(跨 2026-06-09~10-13) ⇒ 当天 13 篇被排到 6 轮之后,
用户报「研报里看不到今天新增的公众号观点」。`--since` 把过滤放在**去重/排序/限流之前**,
让当天的文章立刻拿到名额。

【本文件锁死】
  A. `--since` 确实生效，且过滤发生在限流之前（否则名额仍被历史吃掉）；
  B. 不传 `--since` 时**行为与历史完全一致**（不得影响其它调用方/回填链路）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "wechat_ingest.py"


def _entry(key: str, date: str, title: str = "t") -> dict:
    """一条最小可入库条目（带 date → 会被 entry_date 取到）。"""
    return {
        "key": key,
        "date": date,
        "title": title,
        "account": "测试号",
        "url": "https://mp.weixin.qq.com/s/%s" % key,
        "text": "正" * 400,          # 过 MIN_BODY_CHARS(200)
        "create_time": 1700000000,   # 同一时间戳:排序不干扰本用例
    }


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """造一个隔离的状态目录：3 篇历史(6/9) + 2 篇今天(10/14)。"""
    d = tmp_path / "wechat"
    d.mkdir()
    rows = [
        _entry("k1", "2026-06-09", "历史1"),
        _entry("k2", "2026-06-10", "历史2"),
        _entry("k3", "2026-10-13", "昨天"),
        _entry("k4", "2026-10-14", "今天1"),
        _entry("k5", "2026-10-14", "今天2"),
    ]
    (d / "wechat_inbox.jsonl").write_text(
        "\\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\\n", encoding="utf-8")
    return d


def _run(state_dir: Path, *extra: str) -> str:
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--source", "inbox", "--dry-run",
         "--state-dir", str(state_dir), *extra],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180,
    )
    out = p.stdout + p.stderr
    assert p.returncode == 0, "脚本失败：\\n%s" % out[-800:]
    return out


def test_since_filters_to_window(state_dir):
    """`--since 2026-10-14` ⇒ 只留今天的 2 篇。"""
    out = _run(state_dir, "--since", "2026-10-14")
    assert "--since 2026-10-14 过滤: 5 -> 2 条" in out, out[-600:]
    assert "今天1" in out and "今天2" in out
    assert "历史1" not in out and "昨天" not in out


def test_since_is_applied_before_limit(state_dir):
    """★ 过滤必须在 `--limit` **之前**：否则限流名额仍被历史吃掉(这正是原缺陷)。"""
    # limit=2 且 since=今天 ⇒ 应拿到今天的 2 篇，而不是历史上最旧的 2 篇
    out = _run(state_dir, "--since", "2026-10-14", "--limit", "2")
    assert "今天1" in out, "限流名额被历史占用了 —— 过滤没有发生在限流之前"
    assert "历史1" not in out


def test_without_since_behaviour_unchanged(state_dir):
    """不传 `--since` ⇒ 与历史行为一致（最旧优先，5 篇全在窗口内）。"""
    out = _run(state_dir)
    assert "--since" not in out.replace("【公众号入库】", ""), "未传 --since 却出现了过滤日志"
    assert "5 条" in out, out[-500:]
    # 最旧优先：limit=2 ⇒ 命中两篇历史
    out2 = _run(state_dir, "--limit", "2")
    assert "历史1" in out2 and "历史2" in out2


def test_since_accepts_bare_date_prefix(state_dir):
    """传带时间的 ISO 串也能用（内部取前 10 位）。"""
    out = _run(state_dir, "--since", "2026-10-14T00:00:00")
    assert "5 -> 2 条" in out, out[-500:]
'''

MISSING = object()


def main() -> int:
    # ── ① 追加隆众用例 ──────────────────────────────────────────
    txt = T_MERGE.read_text(encoding="utf-8")
    if "隆众资讯-聚焦" in txt:
        print("⚠️ ① 隆众用例已存在，跳过")
    else:
        assert txt.count(M_OLD) == 1, "① 参数化列表锚点命中 %d" % txt.count(M_OLD)
        txt = txt.replace(M_OLD, M_NEW, 1)
        assert "def test_normalize_source_longzhong_not_over_merged" not in txt
        txt = txt.rstrip("\n") + "\n" + M_APPEND
        T_MERGE.write_text(txt, encoding="utf-8", newline="")
        print("✔ ① 隆众用例已追加到 test_research_source_merge.py")

    # ── ② 新建 --since 护栏 ─────────────────────────────────────
    if T_SINCE.exists():
        print("⚠️ ② %s 已存在，跳过" % T_SINCE.name)
    else:
        T_SINCE.write_text(SINCE_TEST, encoding="utf-8", newline="")
        print("✔ ② 新建 %s" % T_SINCE.name)

    # ── 自检 ────────────────────────────────────────────────────
    for f, needles in [
        (T_MERGE, ['("隆众资讯-聚焦", "隆众资讯")',
                   "def test_normalize_source_longzhong_not_over_merged",
                   "def test_merge_longzhong_multi_kou_jing_becomes_one_row"]),
        (T_SINCE, ['"--since", "2026-10-14"', "test_since_is_applied_before_limit"]),
    ]:
        t = f.read_text(encoding="utf-8")
        for n in needles:
            assert n in t, "%s 缺 %s" % (f.name, n)
        # EOL 铁律：tests/ 下应为 LF
        b = f.read_bytes()
        c = b.count(b"\r\n")
        print("    %-34s LF=%s (%d 行)" % (f.name, "✔" if c == 0 else "✘ CRLF", b.count(b"\n")))
        assert c == 0, "%s 应为 LF" % f.name
    print("\n✔ 护栏已就位")
    return 0


if __name__ == "__main__":
    sys.exit(main())
