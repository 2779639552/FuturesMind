"""Unit tests for the Eastmoney Guba adapter (2026-08-25).

The adapter module is loaded directly (importlib) WITHOUT the platform registry
`platforms/__init__.py`, which would pull heavy deps (Playwright browser
adapters, Spider_XHS). We stub the sibling `base` module so the adapter's
relative import resolves, and never call `init()`, so Playwright itself is
never imported.

Covered: JSONP shell stripping, HTML/contract-tag cleaning, guba time parsing,
normalize → UNIFIED_SCHEMA_FIELDS mapping, and search() paging/retry/dedup with
a fake APIRequestContext. The `id` alias (critical for batch_collect.py:420,
which reads `item["id"] or item["mid"]`) is asserted explicitly.
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
    # `from .base import ...` resolves without importing the real registry.
    base = types.ModuleType("eastmoney_guba_platforms.base")
    base.CredentialError = type("CredentialError", (Exception,), {})
    base.PlatformAdapter = type("PlatformAdapter", (), {})
    base.FIELD_MAPPING_TABLE = {"eastmoney_guba": {}}
    pkg = types.ModuleType("eastmoney_guba_platforms")
    pkg.__path__ = [str(_ADAPTER_DIR)]
    sys.modules["eastmoney_guba_platforms"] = pkg
    sys.modules["eastmoney_guba_platforms.base"] = base

    spec = importlib.util.spec_from_file_location(
        "eastmoney_guba_platforms.eastmoney_guba_adapter",
        _ADAPTER_DIR / "eastmoney_guba_adapter.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eastmoney_guba_platforms.eastmoney_guba_adapter"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_adapter()
EastmoneyGubaAdapter = mod.EastmoneyGubaAdapter

_EMPTY_BODY = 'jsonpCallback({"result":{"gubaArticleWeb":[]}})'
_ONE_POST = (
    'jsonpCallback({"result":{"gubaArticleWeb":['
    '{"id":"9","title":"<em>螺纹钢</em>期货","content":"看多 $RB2610$",'
    '"createTime":"2026-08-25 09:00:00","url":"http://guba.eastmoney.com/p/9"}]}})'
)


class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def text(self):
        return self._body


class _FakeAPI:
    """A fake Playwright APIRequestContext that returns canned responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.get_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append(url)
        status, body = self._responses.pop(0)
        return _FakeResp(status, body)


# ---------------------------------------------------------------------------
# _strip_jsonp
# ---------------------------------------------------------------------------

def test_strip_jsonp_roundtrip():
    assert mod._strip_jsonp('jsonpCallback({"a":1})') == {"a": 1}


def test_strip_jsonp_with_trailing_semicolon():
    assert mod._strip_jsonp('jsonpCallback({"a":1});') == {"a": 1}


def test_strip_jsonp_non_jsonp_returns_none():
    assert mod._strip_jsonp("not a jsonp response") is None
    assert mod._strip_jsonp("") is None


# ---------------------------------------------------------------------------
# _clean_html
# ---------------------------------------------------------------------------

def test_clean_html_strips_tags_and_contract_wrappers():
    assert mod._clean_html("<em>螺纹</em> $RB2610$&nbsp;&amp;") == "螺纹 RB2610 &"


# ---------------------------------------------------------------------------
# _parse_guba_time
# ---------------------------------------------------------------------------

def test_parse_guba_time_full():
    assert mod._parse_guba_time("2026-08-25 10:00:00") == "2026-08-25 10:00:00"


def test_parse_guba_time_no_year_uses_current_year():
    from datetime import datetime

    out = mod._parse_guba_time("08-25 10:00")
    assert out.startswith(str(datetime.now().year) + "-08-25 10:00:00")


def test_parse_guba_time_unix_timestamp():
    from datetime import datetime

    assert mod._parse_guba_time(1780000000) == datetime.fromtimestamp(1780000000).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def test_parse_guba_time_invalid_returns_empty():
    assert mod._parse_guba_time(None) == ""
    assert mod._parse_guba_time("not a date") == ""


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

def test_normalize_maps_unified_schema():
    raw = {
        "post_id": "123",
        "title": "<em>螺纹钢</em>期货",
        "content": "看多 $RB2610$",
        "publish_time": "2026-08-25 10:00:00",
        "url": "http://guba.eastmoney.com/p/123",
    }
    note = EastmoneyGubaAdapter().normalize(raw, None, "螺纹钢")
    assert note["platform"] == "eastmoney_guba"
    assert note["note_id"] == "emg:123"
    assert note["title"] == "螺纹钢期货"
    assert note["desc"] == "看多 RB2610"
    assert note["author_name"] == "unknown"     # 搜索响应无作者字段
    assert note["like_count"] == 0 and note["comment_count"] == 0
    assert note["publish_time"] == "2026-08-25 10:00:00"
    assert note["url"] == "https://guba.eastmoney.com/p/123"  # http → https
    assert note["keyword"] == "螺纹钢"


def test_normalize_drops_missing_post_id():
    assert EastmoneyGubaAdapter().normalize({}, None, "螺纹钢") is None


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def test_search_returns_items_with_id_alias():
    adapter = EastmoneyGubaAdapter()
    adapter._api = _FakeAPI([(200, _ONE_POST)])
    with patch.object(mod.time, "sleep"), patch.object(mod.random, "uniform", return_value=0.0):
        items = adapter.search("螺纹钢", count=1)
    assert len(items) == 1
    # batch_collect.py:420 reads item["id"] or item["mid"] — the alias is critical
    assert items[0]["id"] == "9"
    assert items[0]["post_id"] == "9"


def test_search_uninitialized_returns_empty():
    assert EastmoneyGubaAdapter().search("螺纹钢", count=5) == []


def test_search_retries_empty_page_then_succeeds():
    # WAF degradation returns HTTP 200 + empty gubaArticleWeb → retry with backoff
    adapter = EastmoneyGubaAdapter()
    adapter._api = _FakeAPI([(200, _EMPTY_BODY), (200, _ONE_POST)])
    with patch.object(mod.time, "sleep") as mock_sleep, patch.object(
        mod.random, "uniform", return_value=0.0
    ):
        items = adapter.search("螺纹钢", count=1)
    assert len(items) == 1
    assert len(adapter._api.get_calls) == 2  # first empty, second has the post
    mock_sleep.assert_called()          # the 6s backoff happened


def test_search_http_error_breaks_with_no_items():
    adapter = EastmoneyGubaAdapter()
    adapter._api = _FakeAPI([(403, "forbidden"), (403, "forbidden")])
    with patch.object(mod.time, "sleep"), patch.object(mod.random, "uniform", return_value=0.0):
        items = adapter.search("螺纹钢", count=5)
    assert items == []


def test_search_dedups_repeated_ids_across_pages():
    body = (
        'jsonpCallback({"result":{"gubaArticleWeb":['
        '{"id":"1","title":"a","content":"x","createTime":"2026-08-25 09:00:00"},'
        '{"id":"2","title":"b","content":"y","createTime":"2026-08-25 08:00:00"}]}})'
    )
    adapter = EastmoneyGubaAdapter()
    # page 1 returns 1&2, page 2 returns the same 1&2 → dedup yields 2, then page stops
    adapter._api = _FakeAPI([(200, body), (200, body)])
    with patch.object(mod.time, "sleep"), patch.object(mod.random, "uniform", return_value=0.0):
        items = adapter.search("螺纹钢", count=10)
    assert len(items) == 2
    assert {i["id"] for i in items} == {"1", "2"}
