"""采集器崩溃兜底护栏（2026-10-23）。

【它守什么】
2026-10-23 08:30 那轮读书通道以「退出码 1」失败（未捕获异常），但
`mp_collect_result.json` 仍是前一天的旧文件 ⇒ 调度器按 mtime 读到的 `new` 是旧值，
告警只能写"退出码 1"，**异常类型与堆栈全丢**，排查只能人工复跑。

同日下午人工复跑：86 秒 / 26 篇 / 退出码 0 —— 采集器本身没坏，坏的是"崩溃不留痕"。

所以本文件守三件事：
  1. 任何未捕获异常都必须写出一份 `crashed: true` 的结果文件（new=0）；
  2. 崩溃标记要与「接口被拒 articles_refused」区分开（两者处置完全不同）；
  3. 兜底自身绝不能再抛 —— 它已经是最后一道防线。

注意：**绝不能**让崩溃结果写进 dry-run 文件（调度器只读真实文件）。
"""
from __future__ import annotations

import json

import wechat_weread_collector as W


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_crash_result_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "WEREAD_DIR", tmp_path)
    monkeypatch.setattr(W, "F_RESULT", tmp_path / "mp_collect_result.json")

    try:
        raise ValueError("boom-model-field-missing")
    except ValueError as exc:
        W._write_crash_result(exc)

    p = tmp_path / "mp_collect_result.json"
    assert p.exists(), "崩溃后仍没写结果文件 —— 调度器又会读到上一轮的旧 new"
    d = _read(p)
    assert d["crashed"] is True
    assert d["new"] == 0 and d["content_ok"] == 0
    assert "ValueError" in d["error"] and "boom-model-field-missing" in d["error"]
    assert d["dry_run"] is False


def test_crash_reason_is_distinguishable_from_articles_refused(tmp_path, monkeypatch):
    """崩溃 vs 接口被拒：前者要人看堆栈，后者等自动恢复。混在一起处置就错了。"""
    monkeypatch.setattr(W, "WEREAD_DIR", tmp_path)
    monkeypatch.setattr(W, "F_RESULT", tmp_path / "mp_collect_result.json")

    try:
        raise RuntimeError("x")
    except RuntimeError as exc:
        W._write_crash_result(exc)

    reasons = _read(tmp_path / "mp_collect_result.json")["degraded"]["reasons"]
    assert reasons == ["crashed"]
    assert "articles_refused" not in reasons


def test_crash_writes_full_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "WEREAD_DIR", tmp_path)
    monkeypatch.setattr(W, "F_RESULT", tmp_path / "mp_collect_result.json")

    try:
        raise KeyError("node")
    except KeyError as exc:
        W._write_crash_result(exc)

    tb = (tmp_path / "mp_collect_result.crash.txt").read_text(encoding="utf-8")
    assert "Traceback" in tb and "KeyError" in tb, "没有堆栈，下次还是只能人工复跑"


def test_crash_does_not_touch_dryrun_file(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "WEREAD_DIR", tmp_path)
    monkeypatch.setattr(W, "F_RESULT", tmp_path / "mp_collect_result.json")
    dry = tmp_path / "mp_collect_result.dryrun.json"
    dry.write_text('{"new": 59}', encoding="utf-8")

    try:
        raise RuntimeError("x")
    except RuntimeError as exc:
        W._write_crash_result(exc)

    assert json.loads(dry.read_text(encoding="utf-8"))["new"] == 59, "崩溃兜底污染了 dry-run 文件"


def test_crash_writer_never_raises(tmp_path, monkeypatch):
    """最后一道防线：即使目标目录不可写，也不能再抛异常掩盖原始错误。"""
    monkeypatch.setattr(W, "WEREAD_DIR", tmp_path / "no" / "such" / "dir")
    monkeypatch.setattr(W, "F_RESULT", tmp_path / "no" / "such" / "dir" / "x.json")

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(W, "_save_json", _boom)
    try:
        raise ValueError("original")
    except ValueError as exc:
        W._write_crash_result(exc)  # 不该抛
