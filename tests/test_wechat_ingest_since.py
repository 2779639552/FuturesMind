"""公众号入库「发布日过滤 `--since`」回归护栏（2026-10-14）。

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
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return d


def _run(state_dir: Path, *extra: str) -> str:
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--source", "inbox", "--dry-run",
         "--state-dir", str(state_dir), *extra],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180,
    )
    out = p.stdout + p.stderr
    assert p.returncode == 0, "脚本失败：\n%s" % out[-800:]
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


def test_state_dir_redirects_result_file(state_dir):
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

