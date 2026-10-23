"""状态文件原子写护栏（2026-10-23 根因修复）。

【事故】10-22 18:30 与 10-23 08:30 连续两轮读书通道采集以**退出码 1** 失败，
`mp_collect_result.json` 停在 10-22 08:30（调度器读到旧 `new`，告警只写"退出码 1"）。
人工复跑：86 秒 / 26 篇 / 退出码 0 —— 数据源与凭据都正常。

【根因】`_save_json` 用 `io.open(p, "w")`，会把文件**先截断为 0 字节**再写。
  「续期写 cookies → 立刻读 cookies」这个路径上，只要另一个进程/线程在窗口内读到，
  就是空文件 ⇒ `json.loads` 抛 `JSONDecodeError: Expecting value: line 1 column 1`，
  而该异常未被捕获 ⇒ 退出码 1、结果文件不写。

【本文件守什么】
  1. 写入必须原子：读方要么看到旧的完整内容，要么看到新的完整内容；
  2. 写失败不许留 `.tmp` 垃圾；
  3. 读侧容忍半截文件（历史遗留/外部写入），重试后仍失败要给出**能指导动作**的报错，
     而不是裸 `JSONDecodeError`。
"""
from __future__ import annotations

import json

import pytest

import wechat_weread_collector as W


def test_save_json_is_atomic_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "x.json"
    W._save_json(p, {"a": 1, "b": "中文"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "b": "中文"}
    leftovers = [f.name for f in tmp_path.iterdir() if f.name != "x.json"]
    assert not leftovers, f"残留临时文件: {leftovers}"


def test_save_json_overwrites_atomically(tmp_path):
    """覆盖写：旧内容必须被完整替换，不能出现"前半段新、后半段旧"。"""
    p = tmp_path / "x.json"
    W._save_json(p, {"v": "old", "pad": "x" * 500})
    W._save_json(p, {"v": "new"})
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d == {"v": "new"}


def test_save_json_cleans_tmp_on_failure(tmp_path, monkeypatch):
    p = tmp_path / "x.json"

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(W.os, "replace", _boom)
    with pytest.raises(OSError):
        W._save_json(p, {"a": 1})
    leftovers = [f.name for f in tmp_path.iterdir()]
    assert not leftovers, f"写失败后留下了垃圾: {leftovers}"


def test_read_json_tolerant_retries_then_succeeds(tmp_path, monkeypatch):
    """模拟并发窗口：第一次读到空文件，第二次读到完整内容。"""
    p = tmp_path / "c.json"
    p.write_text('{"ok": 1}', encoding="utf-8")

    real = json.loads
    calls = {"n": 0}

    def _flaky(s, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise json.JSONDecodeError("Expecting value", s, 0)
        return real(s, *a, **k)

    monkeypatch.setattr(W.json, "loads", _flaky)
    assert W._read_json_tolerant(p, delay=0.01) == {"ok": 1}
    assert calls["n"] == 2, "没有重试就直接失败了"


def test_read_json_tolerant_gives_actionable_error(tmp_path, monkeypatch):
    """重试仍失败时，报错要能指导动作（删文件 + 重扫码），不能是裸 JSONDecodeError。"""
    p = tmp_path / "c.json"
    p.write_text("", encoding="utf-8")  # 空文件 = 典型的截断态
    with pytest.raises(SystemExit) as ei:
        W._read_json_tolerant(p, tries=2, delay=0.01)
    msg = str(ei.value)
    assert "读取失败" in msg
    assert "_weread_login.mjs" in msg, "报错没告诉人该跑什么命令"


def test_cookie_loader_uses_tolerant_read(tmp_path, monkeypatch):
    """cookie 是整轮采集的凭据，它的读取必须走带重试的通道。"""
    import inspect

    src = inspect.getsource(W._load_cookie_list)
    assert "_read_json_tolerant" in src, "cookie 读取退回了裸 json.loads"
