"""Tests for the Xueqiu adapter search URL (2026-08-25 re-evaluation).

Bug found in the daily-pipeline re-evaluation: the adapter hardcoded `sortId=1`
(relevance sort) in the search URL. Relevance sort returns old posts, which the
daily pipeline's `--since 7d` window then filters to 0 notes every run — the
platform silently contributed nothing. Empirically `sortId=2` (time sort) returns
same-day posts. This test locks the URL to time sort.

The adapter module is loaded directly (importlib) WITHOUT the real `platforms`
registry, and `_page.evaluate` is faked, so no browser is ever launched.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_ADAPTER_DIR = Path(__file__).resolve().parents[1] / "data_collection" / "validate" / "platforms"


def _load_adapter():
    # Stub the sibling `base` module inside a fake package so the adapter's
    # `from .base import PlatformAdapter` resolves without the real registry.
    base = types.ModuleType("xueqiu_platforms.base")
    base.PlatformAdapter = type("PlatformAdapter", (), {})
    pkg = types.ModuleType("xueqiu_platforms")
    pkg.__path__ = [str(_ADAPTER_DIR)]
    sys.modules["xueqiu_platforms"] = pkg
    sys.modules["xueqiu_platforms.base"] = base

    spec = importlib.util.spec_from_file_location(
        "xueqiu_platforms.xueqiu_adapter",
        _ADAPTER_DIR / "xueqiu_adapter.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["xueqiu_platforms.xueqiu_adapter"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_adapter()
XueqiuAdapter = mod.XueqiuAdapter


class _FakePage:
    """A fake Playwright page whose evaluate() records the JS and returns canned search JSON."""

    def __init__(self, items):
        self.calls = []
        self._items = items

    def evaluate(self, js):
        self.calls.append(js)
        return {"list": self._items}


def _recent_item(n):
    return {
        "id": n,
        "title": "螺纹钢",
        "description": "内容",
        "created_at": 1785000000000 + n,  # 毫秒时间戳(2026 年)
        "user": {"screen_name": "u", "id": 1},
        "like_count": 0,
        "reply_count": 0,
    }


def test_search_uses_time_sort_sortId2():
    """sortId=2 时间排序(相关性排序会让每日 --since 窗口采不到新帖 → 恒 0)."""
    adapter = XueqiuAdapter()
    page = _FakePage([_recent_item(i) for i in range(5)])
    adapter._page = page
    with patch.object(mod.time, "sleep"):
        items = adapter.search("螺纹钢", count=5)
    assert len(items) == 5
    assert "sortId=2" in page.calls[0]
    assert "sortId=1" not in page.calls[0]


def test_search_returns_items_with_id_for_batch_collect():
    """batch_collect.py:485 读 item.get('id') — search 返回的原始 item 必须带 id."""
    adapter = XueqiuAdapter()
    page = _FakePage([_recent_item(123)])
    adapter._page = page
    with patch.object(mod.time, "sleep"):
        items = adapter.search("螺纹钢", count=1)
    assert items[0]["id"] == 123


def test_search_normalize_roundtrip():
    """search → normalize 产出统一 Schema(note_id 带 xq: 前缀)."""
    adapter = XueqiuAdapter()
    page = _FakePage([_recent_item(456)])
    adapter._page = page
    with patch.object(mod.time, "sleep"):
        items = adapter.search("螺纹钢", count=1)
    note = adapter.normalize(items[0], None, "螺纹钢")
    assert note is not None
    assert note["platform"] == "xueqiu"
    assert note["note_id"] == "xq:456"
