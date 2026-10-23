"""原子落盘与自愈读取护栏（2026-10-23）。

【背景】公众号读书通道连续两轮以"退出码 1"失败，根因是 `open(p,"w")` 先截断再写，
并发读拿到空文件 → `JSONDecodeError` 未捕获。这类缺陷**写方不报错、坏的是读方**，
所以既要原子写，也要能把已经坏掉的文件救回来。

【本文件守什么】
  1. 写必须原子，且**上一版**要留成 `.bak`（不是新版的副本）；
  2. 主文件坏了 → 自动回滚 `.bak` → 顺手修好主文件 → 坏文件留档当证据；
  3. `.bak` 也坏/不存在 → 返回 default，不抛到调用方；
  4. 自愈必须可观测（`on_heal` 回调），否则"悄悄修好"等于没发生；
  5. 不留 `.tmp` 垃圾。
"""
from __future__ import annotations

import json
import os

import pytest

from tradingagents import atomic_io as A


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def test_first_save_creates_no_bak(tmp_path):
    p = tmp_path / "s.json"
    A.save_json(p, {"v": 1})
    assert _read(p) == {"v": 1}
    assert not (tmp_path / "s.json.bak").exists(), "首次写入不该凭空造一个 .bak"
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]


def test_bak_holds_previous_version(tmp_path):
    """关键语义：.bak 必须是**上一版**。先写后备份的话，备份到的就是新内容，等于没有可回滚版本。"""
    p = tmp_path / "s.json"
    A.save_json(p, {"v": 1})
    A.save_json(p, {"v": 2})
    assert _read(p) == {"v": 2}
    assert _read(tmp_path / "s.json.bak") == {"v": 1}, ".bak 里不是上一版"


def test_no_tmp_left_behind(tmp_path):
    p = tmp_path / "s.json"
    A.save_json(p, {"a": 1})
    A.save_json(p, {"a": 2})
    leftovers = [f.name for f in tmp_path.iterdir() if f.name.endswith(".tmp")]
    assert not leftovers, f"残留临时文件: {leftovers}"


def test_write_failure_cleans_tmp(tmp_path, monkeypatch):
    p = tmp_path / "s.json"

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(A.os, "replace", _boom)
    with pytest.raises(OSError):
        A.save_json(p, {"a": 1})
    assert not [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]


# ---------------------------------------------------------------------------
# 自愈读取
# ---------------------------------------------------------------------------
def test_load_heals_from_bak(tmp_path):
    """主文件被截断成空文件（真实故障形态）→ 自动回滚上一版并修复主文件。"""
    p = tmp_path / "s.json"
    A.save_json(p, {"v": 1})
    A.save_json(p, {"v": 2})
    p.write_text("", encoding="utf-8")  # 模拟截断窗口留下的空文件

    healed = []
    got = A.load_json(p, on_heal=lambda path, why: healed.append(why))

    assert got == {"v": 1}, "没有回滚到上一版"
    assert _read(p) == {"v": 1}, "主文件没被修复，下次启动还会坏"
    assert healed and "JSONDecodeError" in healed[0], "自愈没有被观测到"
    corrupt = [f.name for f in tmp_path.iterdir() if ".corrupt-" in f.name]
    assert corrupt, "坏文件没留档，将来没法查根因"


def test_load_returns_default_when_both_corrupt(tmp_path):
    p = tmp_path / "s.json"
    A.save_json(p, {"v": 1})
    A.save_json(p, {"v": 2})
    p.write_text("{bad", encoding="utf-8")
    (tmp_path / "s.json.bak").write_text("{also-bad", encoding="utf-8")

    assert A.load_json(p, {"fallback": True}) == {"fallback": True}


def test_load_returns_default_when_missing(tmp_path):
    assert A.load_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}


def test_load_raises_without_default(tmp_path):
    with pytest.raises(FileNotFoundError):
        A.load_json(tmp_path / "nope.json")


def test_load_keeps_hard_half_json(tmp_path):
    """半截 JSON（写到一半被杀）同样要能回滚。"""
    p = tmp_path / "s.json"
    A.save_json(p, {"v": 1})
    A.save_json(p, {"v": 2})
    p.write_text('{"v": 2, "trunc', encoding="utf-8")  # 典型的半截

    assert A.load_json(p) == {"v": 1}


def test_heal_callback_failure_does_not_break_read(tmp_path):
    p = tmp_path / "s.json"
    A.save_json(p, {"v": 1})
    A.save_json(p, {"v": 2})
    p.write_text("", encoding="utf-8")

    def _boom(*a):
        raise RuntimeError("callback exploded")

    assert A.load_json(p, on_heal=_boom) == {"v": 1}


# ---------------------------------------------------------------------------
# 边界：给采集器用时的实际形态
# ---------------------------------------------------------------------------
def test_roundtrip_unicode_and_nested(tmp_path):
    p = tmp_path / "cookie.json"
    obj = {"cookies": [{"name": "wr_skey", "value": "中文值"}], "n": None}
    A.save_json(p, obj)
    assert A.load_json(p) == obj
