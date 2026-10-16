"""Tests for the Weibo adapter's search parsing and throttle handling (2026-09-15).

背景（当日实测的三个真实缺陷，全部表现为"微博条数莫名很少/为零"）:

1. **card_group 内嵌卡漏采**: 微博移动端搜索把大部分帖子藏在 `card_type=11`
   的分组卡里。实测单页 9 个顶层卡中只有 2 个是顶层 `card_type=9` 帖子卡,
   另外 6 条嵌在 `card_type=11` 的 `card_group[0]` 内。
   只读顶层 ⇒ 每关键词 8 条只拿到 2 条(漏 75%),而且拿到的那 2 条恰好是
   置顶/旧帖,过不了 `--since` 时间过滤 ⇒ 整轮产出归零。

2. **"假成功空壳"限流**: 被限流时返回 `ok=1` + `cardlistInfo.total=0` +
   占位卡片(标题含"您可以尝试在全网内搜索"),HTTP 200、无任何错误码,
   与"该关键词真没内容"完全同形。实测按采集节奏连打 24 词随机吞 1 个,
   用 --turbo 快档时整轮 21%(5/24) 被吞。

3. **登录态失效辨识**: 第 1 页匿名可取,第 2 页起返回
   `{"ok": -100, "url": "...passport.weibo.com/sso/signin..."}`。
   注意 Cookie 里**有 SUB 不代表已登录**(游客态也下发 SUB)。

适配器模块用 importlib 直接加载,并 stub 掉同级 `base` 模块,不触发真实网络。
"""

import importlib.util
import sys
import types
from pathlib import Path

_ADAPTER_DIR = Path(__file__).resolve().parents[1] / "data_collection" / "validate" / "platforms"


def _load_adapter():
    """加载 weibo 适配器模块。

    【为什么改成加载**真实** base.py 而不是 stub 一个空壳】
    适配器现在会调用基类上的 `note_degradation()`（登录态失效时登记降级，
    2026-09-18），空壳基类缺这个接口 → `AttributeError`。那是**桩不忠实**，
    不是适配器的错；继续手写桩的话，基类每加一个接口都得同步补一次。
    直接加载真 base.py 是安全的：base 只依赖 abc/typing；
    会拉起平台注册表的是同级 `__init__.py`（它 import 小红书/知乎/雪球 → Playwright），
    本函数不碰它，所以仍然不触发真实网络、不依赖浏览器。
    """
    pkg = types.ModuleType("weibo_platforms")
    pkg.__path__ = [str(_ADAPTER_DIR)]
    sys.modules["weibo_platforms"] = pkg

    spec_b = importlib.util.spec_from_file_location(
        "weibo_platforms.base", _ADAPTER_DIR / "base.py"
    )
    base = importlib.util.module_from_spec(spec_b)
    sys.modules["weibo_platforms.base"] = base  # 先注册, 适配器的相对 import 才找得到
    spec_b.loader.exec_module(base)

    spec = importlib.util.spec_from_file_location(
        "weibo_platforms.weibo_adapter",
        _ADAPTER_DIR / "weibo_adapter.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["weibo_platforms.weibo_adapter"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── 响应构造工具 ──────────────────────────────────────────────────────────


def _mblog(mid: str, text: str = "内容") -> dict:
    return {
        "mid": mid,
        "text": text,
        "text_raw": text,
        "created_at": "Tue Sep 15 10:00:00 +0800 2026",
        "user": {"id": 1, "screen_name": "tester", "followers_count": "1.2万"},
        "reposts_count": 1,
        "comments_count": 2,
        "attitudes_count": 3,
        "pics": [],
    }


def _search_response(cards: list, total: int = 500) -> dict:
    return {"ok": 1, "data": {"cardlistInfo": {"total": total}, "cards": cards}}


def _top_level_only_page() -> dict:
    """模拟"只读顶层 card_type=9"能拿到的 2 条（修复前的实际产出）。"""
    return _search_response([{"card_type": 9, "mblog": _mblog("a")},
                             {"card_type": 9, "mblog": _mblog("b")}])


def _grouped_page() -> dict:
    """模拟真实一页: 2 条顶层 + 6 条藏在 card_type=11 的 card_group 内。"""
    cards = [
        {"card_type": 11, "card_group": [{"card_type": 3}, {"card_type": 4}]},
        {"card_type": 9, "mblog": _mblog("top1")},
        {"card_type": 9, "mblog": _mblog("top2")},
    ]
    for i in range(6):
        cards.append({
            "card_type": 11,
            "card_group": [{"card_type": 9, "mblog": _mblog(f"grp{i}")}],
        })
    return _search_response(cards)


def _placeholder_page() -> dict:
    """模拟限流空壳: ok=1 但 total=0 且只有占位卡。"""
    return _search_response(
        [{"card_type": 4}, {"card_type": 4, "title": "您可以尝试在全网内搜索"},
         {"card_type": 11, "card_group": []}],
        total=0,
    )


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """按预设序列返回响应；序列耗尽后一直返回最后一个。"""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        payload = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        return _Resp(payload)


# ── 缺陷 1: card_group 递归展平 ───────────────────────────────────────────


def test_iter_mblogs_flattens_nested_card_group():
    mod = _load_adapter()
    page = _grouped_page()
    cards = page["data"]["cards"]

    # 只数顶层 card_type=9 会得到 2 条（旧行为）
    top_only = [c for c in cards if c.get("card_type") == 9 and c.get("mblog")]
    assert len(top_only) == 2

    # 递归展平后应拿到全部 8 条
    assert len(mod._iter_mblogs(cards)) == 8


def test_search_collects_all_eight_posts_not_just_two():
    """回归锁: 修复前每关键词只拿 2 条,修复后应拿满 8 条。"""
    mod = _load_adapter()
    adapter = mod.WeiboAdapter()
    # 第 1 页给 8 条；第 2 页起返回登录失效(匿名态)以终止翻页
    adapter._session = _FakeSession([
        _grouped_page(),
        {"ok": -100, "url": "https://passport.weibo.com/sso/signin?entry=wapsso"},
    ])

    items = adapter.search("沥青", 30)

    assert len(items) == 8
    assert {i["mid"] for i in items} == {f"grp{i}" for i in range(6)} | {"top1", "top2"}


# ── 缺陷 2: 假成功空壳识别 + 冷却重试 ─────────────────────────────────────


def test_empty_placeholder_detected_but_real_and_signin_not():
    mod = _load_adapter()
    ph = _placeholder_page()

    assert mod._is_empty_placeholder(ph, ph["data"]["cards"]) is True

    real = _grouped_page()
    assert mod._is_empty_placeholder(real, real["data"]["cards"]) is False

    signin = {"ok": -100, "url": "https://passport.weibo.com/sso/signin"}
    assert mod._is_empty_placeholder(signin, []) is False


def test_empty_placeholder_triggers_cooldown_retry_and_recovers(monkeypatch):
    """空壳 → 冷却后重试 → 恢复真实数据；且确认真实 sleep 被调用。"""
    mod = _load_adapter()
    # 把冷却窗口缩短，避免测试真的等 28 秒
    monkeypatch.setattr(mod, "_EMPTY_RETRY_WAITS", (0.01, 0.02))

    adapter = mod.WeiboAdapter()
    adapter._session = _FakeSession([_placeholder_page(), _placeholder_page(), _grouped_page()])

    data, cards, retries = adapter._search_page("豆粕", 1)

    assert retries == 2, "命中空壳后应重试 2 次"
    assert adapter._session.calls == 3, "共应发起 3 次请求(1 原始 + 2 重试)"
    assert len(mod._iter_mblogs(cards)) == 8, "重试后应恢复真实数据"


def test_giving_up_after_all_retries_does_not_break(monkeypatch):
    mod = _load_adapter()
    # 用 monkeypatch 而非直接赋值，避免污染同进程内后续测试的冷却窗口
    monkeypatch.setattr(mod, "_EMPTY_RETRY_WAITS", (0.01, 0.02))
    adapter = mod.WeiboAdapter()
    adapter._session = _FakeSession([_placeholder_page()])  # 永远空壳

    data, cards, retries = adapter._search_page("豆粕", 1)

    assert retries == 2
    assert mod._iter_mblogs(cards) == [], "一直空壳时应空手返回而非抛异常"


# ── 缺陷 3: 登录态失效辨识 ────────────────────────────────────────────────


def test_signin_response_recognised():
    mod = _load_adapter()
    assert mod._is_signin_response(
        {"ok": -100, "url": "https://passport.weibo.com/sso/signin?entry=wapsso"}
    ) is True
    assert mod._is_signin_response({"ok": -100, "url": "https://m.weibo.cn/"}) is False
    assert mod._is_signin_response({"ok": 1, "data": {}}) is False


def test_search_stops_on_signin_without_crashing():
    mod = _load_adapter()
    adapter = mod.WeiboAdapter()
    adapter._session = _FakeSession([
        {"ok": -100, "url": "https://passport.weibo.com/sso/signin"},
    ])

    assert adapter.search("原油", 30) == []
