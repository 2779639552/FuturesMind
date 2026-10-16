"""渠道静默失效治理的回归测试 (2026-09-14)。

被锁定的真实缺陷
----------------
1) 【致命】`--no-detail`(max_detail=0) 时, 旧写法
   `detail_limit = max_detail if adapter.needs_detail_fetch else len(items)`
   对"需要详情深挖"的平台(zhihu/xhs)求值为 0 → 下面的 for 循环第一轮就 break
   → **search 明明成功, 整批条目却被丢光**, 且退出码是 0。
   表现: 知乎连续多日 0 条却"看起来跑成功了"(静默失效)。
   修复: max_detail<=0 只表示"不额外拉详情", 仍然用搜索返回的摘要入库。

2) 退出码恒为 0: 凭证失效/整批被拦截也 exit 0, 调用方无法据此告警。
   现在按结论给退出码(EXIT_AUTH_FAILED=3 / EXIT_BLOCKED=4 / EXIT_OK=0 ...)。

3) 无运行留痕: 现在每次运行都写 output/collect_run_{platform}_{ts}.json,
   并在 stdout 打一行 `COLLECT_RUN: {...}` 供调用方直接解析
   (文件名刻意不用 batch_ 前缀, 免得被聚合的 batch_*.jsonl 通配扫进去)。

测试手法: 用 importlib 加载 batch_collect.py, 并把 `ner` / `sentiment` / `platforms`
三个依赖替换成桩模块, 因此**不需要 Playwright / torch / 真实登录态**,
也不会发起任何网络请求。
"""

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

_VALIDATE_DIR = Path(__file__).resolve().parents[1] / "data_collection" / "validate"
# 默认测仓库副本; 可用 BC_UNDER_TEST 指向实际运行的副本
# (运行副本在 D:\AgentSense\思路2\validate, 由 path_utils.resolve_think2_dir 解析),
# 两份只差 douyin 平台的配置, 逻辑必须一致 —— 迁移/同步后可用它验证。
_BATCH_COLLECT = Path(os.environ.get("BC_UNDER_TEST", str(_VALIDATE_DIR / "batch_collect.py")))
_STUB_NAMES = ("ner", "sentiment", "platforms", "platforms.base")

# 可注入的适配器工厂: 桩 platforms.get_adapter 读它, 测试往里放假适配器
_ADAPTER_FACTORY: dict = {}


class _StubCredentialError(Exception):
    """与 platforms.base.CredentialError 同形(测试只需同类异常语义)。"""


def _load_batch_collect():
    """加载 batch_collect.py, 期间用桩模块顶替 ner/sentiment/platforms。

    加载完成后立刻把桩从 sys.modules 摘掉, 避免污染其他测试
    (batch_collect 的模块级全局已绑定好了桩对象, 不受影响)。
    """
    saved = {name: sys.modules.get(name) for name in _STUB_NAMES}

    base = types.ModuleType("platforms.base")
    base.CredentialError = _StubCredentialError
    base.PlatformAdapter = type("PlatformAdapter", (), {})

    platforms = types.ModuleType("platforms")
    platforms.get_adapter = lambda name: _ADAPTER_FACTORY[name]
    platforms.list_platforms = lambda: list(_ADAPTER_FACTORY)

    ner = types.ModuleType("ner")
    ner.FuturesNER = type("FuturesNER", (), {"__init__": lambda self, *a, **k: None})

    sentiment = types.ModuleType("sentiment")
    sentiment.SentimentAnalyzer = type(
        "SentimentAnalyzer", (), {"__init__": lambda self, *a, **k: None}
    )

    sys.modules["platforms"] = platforms
    sys.modules["platforms.base"] = base
    sys.modules["ner"] = ner
    sys.modules["sentiment"] = sentiment

    spec = importlib.util.spec_from_file_location("bc_channel_health_under_test", _BATCH_COLLECT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev
    return mod


bc = _load_batch_collect()


class _NullLimiter:
    """无延时限流器: 让测试不 sleep(生产中由自适应 RateLimiter 顶替)。

    turbo_mode / safe_mode 必须与真实 RateLimiter 同形 —— run() 会读它们来决定
    打印的模式名(TURBO/SAFE/FAST), 缺一个就 AttributeError。
    """

    turbo_mode = True
    safe_mode = False

    def wait(self):
        pass

    def report_success(self):
        pass

    def report_failure(self, is_rate_limit=False):
        pass

    def cooldown(self, seconds=10):
        pass


class _FakeAdapter:
    """最小可用的假适配器, 记录调用痕迹以断言"到底有没有真的去拉详情"。"""

    display_name = "知乎"

    def __init__(
        self,
        items,
        needs_detail_fetch=True,
        search_error=None,
        error_category="other",
        normalize_returns_none=False,
        init_error=None,
    ):
        self.items = list(items)
        self.needs_detail_fetch = needs_detail_fetch
        self.search_error = search_error
        self.error_category = error_category
        self.normalize_returns_none = normalize_returns_none
        self.init_error = init_error
        self.search_calls = []
        self.detail_calls = []

    def init(self):
        if self.init_error is not None:
            raise self.init_error

    def search(self, keyword, count):
        self.search_calls.append((keyword, count))
        if self.search_error is not None:
            raise self.search_error
        return list(self.items)

    def get_detail(self, item):
        self.detail_calls.append(item["id"])
        return {"content": f"detail-of-{item['id']}"}

    def normalize(self, item, detail, keyword):
        if self.normalize_returns_none:
            return None
        return {
            "platform": "zhihu",
            "note_id": f"zh:{item['id']}",
            "title": item.get("title", ""),
            "desc": (detail or {}).get("content", item.get("excerpt", "")),
            "like_count": 0,
            "comment_count": 0,
            "keyword": keyword,
        }

    def classify_error(self, e):
        return self.error_category


def _make_collector(adapter, platform="zhihu"):
    """构造采集器并把限流器换成无延时桩。"""
    _ADAPTER_FACTORY[platform] = adapter
    collector = bc.MultiPlatformCollector(platform=platform, turbo_mode=True)
    collector.limiter = _NullLimiter()
    return collector


def _items(n):
    return [{"id": str(i), "title": f"t{i}", "excerpt": f"e{i}"} for i in range(1, n + 1)]


# ══════════════════════════════════════════════════════════════════
# 缺陷 1: --no-detail 不得丢掉条目
# ══════════════════════════════════════════════════════════════════


def test_no_detail_keeps_items_for_detail_platform():
    """回归: needs_detail_fetch=True + max_detail=0(即 --no-detail) 必须照样入库。

    这正是知乎长期 0 条的根因 —— 修复前本用例返回空列表。
    """
    adapter = _FakeAdapter(_items(3), needs_detail_fetch=True)
    collector = _make_collector(adapter)

    notes = collector.collect_one_keyword("原油期货", count=10, max_detail=0)

    assert len(notes) == 3, "search 成功却被整批丢弃 → 静默失效回归"
    assert adapter.detail_calls == [], "--no-detail 不应发起任何详情请求"
    assert collector.stats.detail_fetched == 0
    assert collector.stats.with_data_keywords == 1
    # 无详情时用搜索结果里的摘要兜底入库, 不能是空正文
    assert notes[0]["desc"] == "e1"


def test_max_detail_still_limits_deep_dive():
    """max_detail>0 时语义不变: 只深挖前 N 条(不因为修复而把深挖也放开)。"""
    adapter = _FakeAdapter(_items(5), needs_detail_fetch=True)
    collector = _make_collector(adapter)

    notes = collector.collect_one_keyword("原油期货", count=10, max_detail=2)

    assert len(notes) == 2
    assert adapter.detail_calls == ["1", "2"]
    assert collector.stats.detail_fetched == 2


def test_platform_without_detail_keeps_all_items():
    """needs_detail_fetch=False(weibo/xueqiu/东财/抖音) → 全部条目入库, 不拉详情。"""
    adapter = _FakeAdapter(_items(4), needs_detail_fetch=False)
    collector = _make_collector(adapter, platform="weibo")

    notes = collector.collect_one_keyword("原油期货", count=10, max_detail=0)

    assert len(notes) == 4
    assert adapter.detail_calls == []


def test_search_ok_but_zero_notes_is_recorded():
    """搜索有结果却 0 条入库 → 必须写进逐关键词诊断(否则仍是静默失效)。"""
    adapter = _FakeAdapter(_items(3), normalize_returns_none=True)
    collector = _make_collector(adapter)

    notes = collector.collect_one_keyword("原油期货", count=10, max_detail=0)

    assert notes == []
    assert collector._last_kw["items"] == 3
    assert collector._last_kw["notes"] == 0
    assert collector._last_kw["error_class"] == "no_notes"


# ══════════════════════════════════════════════════════════════════
# 缺陷 2: 语义化退出码 / 状态判定
# ══════════════════════════════════════════════════════════════════


def test_exit_code_constants():
    assert (bc.EXIT_OK, bc.EXIT_AUTH_FAILED, bc.EXIT_BLOCKED, bc.EXIT_ERROR) == (0, 3, 4, 5)


def test_decide_status_ok():
    adapter = _FakeAdapter(_items(2))
    collector = _make_collector(adapter)
    collector.total_notes = 7
    collector.stats.with_data_keywords = 2

    status, _diag, _hint, code = collector._decide_status()

    assert (status, code) == ("ok", bc.EXIT_OK)


def test_decide_status_auth_failed():
    adapter = _FakeAdapter(_items(2))
    collector = _make_collector(adapter)
    collector.stats.auth_failures = 2

    status, diag, hint, code = collector._decide_status()

    assert status == "auth_failed"
    assert code == bc.EXIT_AUTH_FAILED
    assert hint, "auth_failed 必须给出重新登录的修复指引"


def test_decide_status_blocked_when_never_got_search_results():
    adapter = _FakeAdapter(_items(2))
    collector = _make_collector(adapter)
    collector.stats.search_failures = 3

    status, _diag, _hint, code = collector._decide_status()

    assert status == "blocked"
    assert code == bc.EXIT_BLOCKED


def test_decide_status_empty_is_not_a_failure():
    """搜索请求成功但确实没内容 → empty + 退出码 0, 不能误报为渠道故障。"""
    adapter = _FakeAdapter(_items(2))
    collector = _make_collector(adapter)
    collector.stats.with_data_keywords = 1

    status, _diag, _hint, code = collector._decide_status()

    assert status == "empty"
    assert code == bc.EXIT_OK


def test_non_auth_search_error_is_counted_as_search_failure():
    adapter = _FakeAdapter(_items(2), search_error=RuntimeError("boom"), error_category="other")
    collector = _make_collector(adapter)

    notes = collector.collect_one_keyword("原油期货", count=10, max_detail=0)

    assert notes == []
    assert collector.stats.search_failures == 1
    assert collector.stats.auth_failures == 0
    assert collector._last_kw["error_class"] == "other"


def test_auth_search_error_is_counted_as_auth_failure():
    adapter = _FakeAdapter(_items(2), search_error=RuntimeError("login"), error_category="auth")
    collector = _make_collector(adapter)

    collector.collect_one_keyword("原油期货", count=10, max_detail=0)

    assert collector.stats.auth_failures == 1
    assert collector.stats.search_failures == 0


# ══════════════════════════════════════════════════════════════════
# 缺陷 3: 运行留痕 (collect_run_*.json + COLLECT_RUN: 行)
# ══════════════════════════════════════════════════════════════════


def _write_report(collector, monkeypatch, tmp_path, status, diagnosis="", fix_hint=""):
    monkeypatch.setattr(bc, "OUTPUT_DIR", tmp_path)
    return collector.write_report(status, diagnosis, fix_hint)


def test_write_report_lands_as_collect_run_json(tmp_path, monkeypatch, capsys):
    adapter = _FakeAdapter(_items(2))
    collector = _make_collector(adapter)
    collector.total_notes = 5
    collector.stats.keywords_total = 2
    collector.stats.keywords_done = 2
    collector.stats.with_data_keywords = 2

    path = _write_report(collector, monkeypatch, tmp_path, "ok", "采集到 5 条")

    assert path is not None and path.exists()
    # 文件名必须用 collect_run_ 前缀, 否则会被 batch_*.jsonl 聚合通配扫进去
    assert path.name.startswith("collect_run_zhihu_")
    assert not path.name.startswith("batch_")

    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["platform"] == "zhihu"
    assert rec["status"] == "ok"
    assert rec["notes"] == 5
    assert rec["keywords_with_data"] == 2
    assert "per_keyword" in rec

    # 同时要在 stdout 打一行机器可读 JSON 供调用方解析
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.startswith(bc.RUN_LINE_PREFIX))
    assert json.loads(line[len(bc.RUN_LINE_PREFIX) :])["status"] == "ok"


def test_write_report_survives_unwritable_dir(tmp_path, monkeypatch):
    """报告写盘失败只 warning, 绝不能把采集本身搞崩。"""
    adapter = _FakeAdapter(_items(2))
    collector = _make_collector(adapter)
    bad = tmp_path / "afile"
    bad.write_text("not a dir", encoding="utf-8")
    monkeypatch.setattr(bc, "OUTPUT_DIR", bad)

    assert collector.write_report("ok", "x") is None


def test_init_api_credential_error_writes_report_and_exits_3(tmp_path, monkeypatch, capsys):
    """凭证失效必须留痕 + 退出码 3, 而不是旧实现的 exit(1) 且零痕迹。"""
    adapter = _FakeAdapter(_items(2), init_error=_StubCredentialError("登录态已过期"))
    collector = _make_collector(adapter)
    monkeypatch.setattr(bc, "OUTPUT_DIR", tmp_path)

    with pytest.raises(SystemExit) as exc:
        collector.init_api()

    assert exc.value.code == bc.EXIT_AUTH_FAILED
    reports = list(tmp_path.glob("collect_run_zhihu_*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text(encoding="utf-8"))["status"] == "auth_failed"
    assert capsys.readouterr().out.strip(), "凭证失效必须在 stdout 给出可读提示"
