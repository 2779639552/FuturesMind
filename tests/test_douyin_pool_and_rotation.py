"""抖音词池/成本闸 与 关键词轮转持久游标 的回归测试 (2026-09-17)。

被锁定的真实缺陷
----------------
1) 【系统性饿死】抖音词池只有 5 个词(3 品种 + 2 通用), 单个词耗时 240s、全局预算
   12min ⇒ 一轮最多 3 个词; 而轮转 offset 的刻度是**半天**(日期序号×2 + 上/下午),
   同一半天内多次运行 offset 完全相同 ⇒ 每轮都从 '沥青期货' 起跑, 池中
   `碳酸锂期货 / 期货交易 / 期货实盘` **从 2026-09-07 建池起一次都没采到过**。
   用户可见症状: "抖音今天的数据只有沥青及其相关品种"。
   修复: ① 词池扩到 25 词(与 20 品种池对齐); ② 单词语成本 240s → ~100s;
        ③ 轮转游标落盘(output/.rotation_{平台}.json), 按"本轮实际完成的词数"推进。

2) 【刻度错】旧算法把"半天"当成一轮, 比真实运行频率粗 —— 同日多次运行等于重复劳动,
   还让同一个词在短时间内被反复请求(给风控多送一次触发机会)。

测试手法: importlib 加载 batch_collect.py(桩掉 ner/sentiment/platforms) 与
douyin_adapter.py(桩掉 platforms.base) —— 不需要 Playwright、不联网、不落真实输出目录。
"""

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

_VALIDATE_DIR = Path(__file__).resolve().parents[1] / "data_collection" / "validate"
_BATCH_COLLECT = Path(
    os.environ.get("BC_UNDER_TEST", str(_VALIDATE_DIR / "batch_collect.py"))
)
_DOUYIN_ADAPTER = _VALIDATE_DIR / "platforms" / "douyin_adapter.py"
_STUB_NAMES = ("ner", "sentiment", "platforms", "platforms.base")


# ──────────────────────────────────────────────────────────────────────────
# 加载器: 把重依赖(Playwright/torch)换成桩, 只测纯逻辑
# ──────────────────────────────────────────────────────────────────────────


def _load_batch_collect(module_name: str = "bc_rotation_under_test"):
    saved = {name: sys.modules.get(name) for name in _STUB_NAMES}

    base = types.ModuleType("platforms.base")
    base.CredentialError = type("CredentialError", (Exception,), {})
    base.PlatformAdapter = type("PlatformAdapter", (), {})

    platforms = types.ModuleType("platforms")
    platforms.get_adapter = lambda name: None
    platforms.list_platforms = lambda: []

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

    spec = importlib.util.spec_from_file_location(module_name, _BATCH_COLLECT)
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


def _load_douyin_adapter(module_name: str = "platforms.douyin_adapter"):
    """按包路径加载抖音适配器(它用 `from .base import ...`, 必须挂在 platforms 包下)。"""
    saved = {name: sys.modules.get(name) for name in ("platforms", "platforms.base")}

    base = types.ModuleType("platforms.base")
    base.PlatformAdapter = type("PlatformAdapter", (), {})
    base.CredentialError = type("CredentialError", (Exception,), {})

    platforms = types.ModuleType("platforms")
    platforms.__path__ = [str(_VALIDATE_DIR / "platforms")]
    platforms.base = base

    sys.modules["platforms"] = platforms
    sys.modules["platforms.base"] = base

    spec = importlib.util.spec_from_file_location(module_name, _DOUYIN_ADAPTER)
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

# 21 个活跃品种(与 DEFAULT_KEYWORDS_XHS/EASTMONEY_GUBA 的品种池对齐;2026-09-30 加 P 棕榈油)
_ACTIVE_VARIETIES = (
    "原油", "PTA", "对二甲苯", "乙二醇", "燃料油", "低硫燃料油", "LPG",
    "橡胶", "20号胶", "丁二烯橡胶", "沥青", "纯苯", "苯乙烯",
    "豆粕", "棉花", "红枣", "生猪", "棕榈油",
    "碳酸锂", "多晶硅", "工业硅",
)


# ──────────────────────────────────────────────────────────────────────────
# ① 抖音词池: 从"试点 5 词"扩到 20 品种池
# ──────────────────────────────────────────────────────────────────────────


def test_douyin_pool_is_no_longer_a_pilot_pool():
    """旧池只有 5 词(3 品种 + 2 通用), 这是"抖音只有沥青"的根因之一。"""
    assert len(bc.DEFAULT_KEYWORDS_DOUYIN) >= 25, bc.DEFAULT_KEYWORDS_DOUYIN


def test_douyin_pool_covers_every_active_variety():
    pool = bc.DEFAULT_KEYWORDS_DOUYIN
    missing = [v for v in _ACTIVE_VARIETIES if not any(v in kw for kw in pool)]
    assert not missing, f"抖音词池缺少品种: {missing}"


def test_douyin_pool_keeps_the_starved_keywords():
    """这三个词是"被饿死"的实证对象(从建池起一次都没采到), 不能借扩容之名删掉。"""
    for kw in ("碳酸锂期货", "期货交易", "期货实盘"):
        assert kw in bc.DEFAULT_KEYWORDS_DOUYIN, kw


def test_douyin_pool_has_no_duplicates():
    pool = bc.DEFAULT_KEYWORDS_DOUYIN
    assert len(pool) == len(set(pool)), "词池出现重复词(会浪费本轮预算)"


# ──────────────────────────────────────────────────────────────────────────
# ② 轮转游标: 按"本轮实际完成的词数"推进, 失败不推进(自愈)
# ──────────────────────────────────────────────────────────────────────────


def test_next_rotation_offset_advances_by_completed_words():
    assert bc.next_rotation_offset(0, 6, 25) == 6
    assert bc.next_rotation_offset(20, 6, 25) == 1  # 回绕
    assert bc.next_rotation_offset(0, 25, 25) == 0


def test_next_rotation_offset_holds_when_nothing_ran():
    """被风控熔断/凭证失效导致 0 词完成时**不推进** —— 下一轮重试而不是跳过。"""
    assert bc.next_rotation_offset(7, 0, 25) == 7


def test_next_rotation_offset_survives_degenerate_inputs():
    assert bc.next_rotation_offset(-3, -5, 25) == 0
    assert bc.next_rotation_offset(4, 2, 0) == 0  # total=0 不能除零


def test_rotation_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    assert bc.write_rotation_state(path, offset=6, run_seq=1, total=25, last_done=6)

    state = bc.read_rotation_state(path)
    assert state is not None
    assert state["offset"] == 6 and state["run_seq"] == 1
    assert state["total"] == 25 and state["last_done"] == 6
    assert state["updated_at"], "必须留时间戳(排查'游标为何停在这'时唯一线索)"


def test_rotation_state_missing_or_corrupt_falls_back(tmp_path):
    """缺失/损坏一律返回 None(调用方回退到旧的半天派生法), 绝不抛错。"""
    assert bc.read_rotation_state(tmp_path / "nope.json") is None

    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert bc.read_rotation_state(broken) is None

    wrong_type = tmp_path / "wrong.json"
    wrong_type.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert bc.read_rotation_state(wrong_type) is None


def test_rotation_state_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "state.json"
    bc.write_rotation_state(path, offset=1, run_seq=1, total=25, last_done=1)
    bc.write_rotation_state(path, offset=2, run_seq=2, total=25, last_done=1)

    assert bc.read_rotation_state(path)["offset"] == 2
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert not leftovers, f"临时文件没清干净: {leftovers}"


def test_rotation_state_path_is_per_platform():
    a = bc.rotation_state_path("douyin", Path("out"))
    b = bc.rotation_state_path("xueqiu", Path("out"))
    assert a != b
    assert a.name == ".rotation_douyin.json"
    assert not a.name.startswith("batch_"), "不能撞上聚合用的 batch_*.jsonl 通配"


def test_rotation_state_write_failure_is_non_fatal(tmp_path):
    """落盘失败只影响下轮起点, 绝不影响已采集的数据 ⇒ 返回 False 而不是抛错。"""
    target = tmp_path / "missing_dir" / "state.json"
    assert bc.write_rotation_state(target, offset=1, run_seq=1, total=25, last_done=1) is False


def test_legacy_day_derived_offset_still_available():
    """回退路径必须还在(游标缺失时行为与升级前一致)。"""
    offset = bc.keyword_rotation_offset(520, 6, 25)
    assert 0 <= offset < 25


# ──────────────────────────────────────────────────────────────────────────
# ③ 抖音单词语成本闸: 240s → ~100s
# ──────────────────────────────────────────────────────────────────────────


def test_douyin_cost_knobs_are_reduced():
    mod = _load_douyin_adapter("platforms.douyin_adapter_knobs")
    # 旧值 6 视频 × 15 页 ≈ 240s/词; 新值 4 × 8 ≈ 100s/词
    assert mod.MAX_COMMENT_VIDEOS == 4
    assert mod.COMMENT_SCROLLS == 8
    assert mod.EMPTY_SCROLL_LIMIT == 3


def test_douyin_env_overrides_are_honored_and_clamped(monkeypatch):
    monkeypatch.setenv("AGENTSENSE_DY_MAX_VIDEOS", "2")
    monkeypatch.setenv("AGENTSENSE_DY_COMMENT_SCROLLS", "12")
    monkeypatch.setenv("AGENTSENSE_DY_EMPTY_SCROLLS", "abc")  # 非法 → 回落默认
    mod = _load_douyin_adapter("platforms.douyin_adapter_env")

    assert mod.MAX_COMMENT_VIDEOS == 2
    assert mod.COMMENT_SCROLLS == 12
    assert mod.EMPTY_SCROLL_LIMIT == 3


class _FakeResponse:
    def __init__(self, payload):
        self.url = "https://www.douyin.com/aweme/v1/web/comment/list/?a=1"
        self._payload = payload

    def json(self):
        return self._payload


class _FakePage:
    """最小页面桩: 记录滚动次数, 可选地在每次滚动时"加载出新一页评论"。"""

    def __init__(self, emit_comments: bool = False):
        self.scrolls = 0
        self._emit = emit_comments
        self._handler = None
        self._n = 0

    def on(self, event, handler):
        self._handler = handler

    def remove_listener(self, event, handler):
        self._handler = None

    def goto(self, *a, **k):
        return None

    def title(self):
        return "抖音"

    def evaluate(self, *a, **k):
        self.scrolls += 1
        if self._emit and self._handler is not None:
            self._n += 1
            self._handler(_FakeResponse({"comments": [{
                "cid": f"c{self._n}",
                "text": f"第{self._n}条评论内容",
                "user": {"nickname": "user"},
                "digg_count": 1,
                "create_time": 1700000000,
            }]}))


def _scrape(mod, monkeypatch, emit_comments: bool):
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)  # 不真的等 2s/3.5s
    adapter = mod.DouyinAdapter()
    page = _FakePage(emit_comments=emit_comments)
    adapter._page = page
    comments = adapter._scrape_comments({"href": "https://x/video/1", "aweme_id": "1"}, "kw")
    return page, comments


def test_comment_scroll_gives_up_early_when_no_new_comments(monkeypatch):
    """评论稀疏的视频不该白等满 8 页 —— 连续 3 次滚不出新评论就收尾。"""
    mod = _load_douyin_adapter("platforms.douyin_adapter_early")
    page, comments = _scrape(mod, monkeypatch, emit_comments=False)

    assert comments == []
    assert page.scrolls == mod.EMPTY_SCROLL_LIMIT, page.scrolls


def test_comment_scroll_keeps_going_while_comments_arrive(monkeypatch):
    """只要每滚一页都有新评论, 就必须滚满上限(不能把提前收尾做成普遍降级)。"""
    mod = _load_douyin_adapter("platforms.douyin_adapter_full")
    page, comments = _scrape(mod, monkeypatch, emit_comments=True)

    assert page.scrolls == mod.COMMENT_SCROLLS, page.scrolls
    assert len(comments) == mod.COMMENT_SCROLLS


# ──────────────────────────────────────────────────────────────────────────
# ④ 端到端接线: main() 真的"读游标 → 轮转 → 跑完按实际完成词数推进游标"
#    (前三条只测了纯函数; 若 main() 没接上, 这些函数再对也没用)
# ──────────────────────────────────────────────────────────────────────────


class _FakeCollector:
    """替身采集器: 不联网, 只记录"本轮实际跑了哪些词"并回报完成词数。"""

    last_keywords: list = []

    def __init__(self, *a, **k):
        self.output_file = "<fake>"
        self.exit_code = 0
        self.stats = types.SimpleNamespace(keywords_done=0, detail_fetched=0)

    def run(self, keywords, **kwargs):
        type(self).last_keywords = list(keywords)
        self.stats.keywords_done = int(os.environ.get("FAKE_KW_DONE", "0"))
        return []

    def write_report(self, *a, **k):
        pass


def _run_main(mod, monkeypatch, tmp_path, extra_argv=(), done=0):
    """在不联网、不落真实输出目录的前提下跑一次 main()。"""
    monkeypatch.setattr(mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(mod, "MultiPlatformCollector", _FakeCollector)
    # argparse 的 choices=list_platforms() 是在 parse 时求值的 → 桩里也要给平台名
    monkeypatch.setattr(mod, "list_platforms", lambda: ["douyin"])
    monkeypatch.setenv("FAKE_KW_DONE", str(done))
    stub = types.ModuleType("platforms")
    stub.ADAPTER_DISPLAY_NAMES = {}
    stub.get_adapter = lambda name: types.SimpleNamespace(needs_detail_fetch=False)
    monkeypatch.setitem(sys.modules, "platforms", stub)
    monkeypatch.setattr(sys, "argv", [
        "batch_collect.py", "--platform", "douyin", "--max-keywords", "6",
        "--rotate-keywords", "--no-detail", "--per-kw", "5", *extra_argv,
    ])
    with pytest.raises(SystemExit):
        mod.main()


def test_main_creates_cursor_on_first_run_and_advances_by_done(tmp_path, monkeypatch):
    """首跑: 无游标 → 回退派生法 → 跑完按"实际完成 6 个词"把游标推到 (起点+6)。"""
    mod = _load_batch_collect("bc_main_cursor_1")
    pool = mod.DEFAULT_KEYWORDS_DOUYIN
    total = len(pool)

    _run_main(mod, monkeypatch, tmp_path, done=6)
    start_idx = pool.index(_FakeCollector.last_keywords[0])  # 本轮起点(派生法给出)

    cursor = mod.read_rotation_state(mod.rotation_state_path("douyin", tmp_path))
    assert cursor is not None, "main() 没有落盘游标 —— 轮转仍是'半天刻度'"
    assert cursor["offset"] == (start_idx + 6) % total
    assert cursor["run_seq"] == 1
    assert cursor["last_done"] == 6


def test_main_resumes_from_cursor_so_same_day_runs_complement(tmp_path, monkeypatch):
    """同一天第二次运行必须从游标处**接着跑**, 而不是又从头 6 个词开始重采。"""
    mod = _load_batch_collect("bc_main_cursor_2")
    pool = mod.DEFAULT_KEYWORDS_DOUYIN
    total = len(pool)

    _run_main(mod, monkeypatch, tmp_path, done=6)
    start_idx = pool.index(_FakeCollector.last_keywords[0])

    _run_main(mod, monkeypatch, tmp_path, done=4)
    second_start = _FakeCollector.last_keywords[0]

    assert second_start == pool[(start_idx + 6) % total], (
        f"第二轮没有接着跑: 期望 {pool[(start_idx + 6) % total]}, 实际 {second_start}"
    )
    cursor = mod.read_rotation_state(mod.rotation_state_path("douyin", tmp_path))
    assert cursor["offset"] == (start_idx + 10) % total and cursor["run_seq"] == 2


def test_main_does_not_advance_cursor_when_nothing_completed(tmp_path, monkeypatch):
    """被风控熔断(0 词完成)时游标原地不动 —— 下一轮重试同一批词, 而不是跳过。"""
    mod = _load_batch_collect("bc_main_cursor_3")
    _run_main(mod, monkeypatch, tmp_path, done=0)
    once = mod.read_rotation_state(mod.rotation_state_path("douyin", tmp_path))

    _run_main(mod, monkeypatch, tmp_path, done=0)
    twice = mod.read_rotation_state(mod.rotation_state_path("douyin", tmp_path))

    assert once["offset"] == twice["offset"], "失败也推进游标 ⇒ 被限流的词会被永久跳过"
    assert twice["run_seq"] == 2, "轮数仍要计数(用于排查)"
