"""抖音搜索阶段"滚动优先"的回归测试 (2026-09-18)。

被锁定的真实缺陷
----------------
搜索阶段的动作顺序本来是「goto → **先干等 20s 等响应** → 等不到再滚 2 次」。
同会话对照实验(同 profile、只改一个变量)证明顺序是错的:

  · 先等 20s 再滚  → 20s 内零响应, 首个响应 +21.3s 才到(靠滚动才触发) → SEARCH 阶段 24.3s
  · goto 后立刻滚  → 首个响应 +0.0s / +4.4s 到                        → SEARCH 阶段 5.3s / 5.9s

即: **搜索响应由"滚动"触发, 干等不产生任何数据**。每词白花约 20s(占单词语成本约 28%),
一轮 6 词 = 白等 2 分钟; 生产侧交叉验证过单词语耗时是整齐常数(71/71/71/72/71/72s)。
修复 = 把"真触发器"(滚动)提到前面, 只保留一个**短**兜底等待。

测试手法
--------
用一个假 page 记录**动作序列**(goto / scroll / sleep 合并在同一条时间线上),
这样才能真正区分"先滚"与"先等"两种顺序 ——
若只断言"拿到了数据", 两种顺序都会通过, 等于没测到点子上。
不依赖 Playwright / 网络 / 真实登录态。
"""

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

_ADAPTER_DIR = Path(__file__).resolve().parents[1] / "data_collection" / "validate" / "platforms"
_SEARCH_FRAGMENT = "/aweme/v1/web/general/search/single/"


def _load_adapter():
    """加载抖音适配器模块(加载**真实** base.py, 不给它手写空壳桩)。

    【为什么不 stub 基类】适配器会调用基类接口(`note_degradation` 等);
    手写的空壳基类每加一个接口就得同步补一次, 否则测试报的是"桩不忠实"的假红。
    真 base.py 只依赖 abc/typing; 会拉起 Playwright 的是同级 `__init__.py`,
    本函数不碰它 —— 所以仍然无网络、无浏览器。
    """
    pkg = types.ModuleType("douyin_platforms")
    pkg.__path__ = [str(_ADAPTER_DIR)]
    sys.modules["douyin_platforms"] = pkg

    spec_b = importlib.util.spec_from_file_location(
        "douyin_platforms.base", _ADAPTER_DIR / "base.py"
    )
    base = importlib.util.module_from_spec(spec_b)
    sys.modules["douyin_platforms.base"] = base  # 先注册, 适配器的相对 import 才找得到
    spec_b.loader.exec_module(base)

    spec = importlib.util.spec_from_file_location(
        "douyin_platforms.douyin_adapter", _ADAPTER_DIR / "douyin_adapter.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["douyin_platforms.douyin_adapter"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── 假 page: 记录动作序列, 可按"第几次滚动"触发响应 ──────────────────────


class _FakeResponse:
    def __init__(self, payload):
        self.url = "https://www.douyin.com" + _SEARCH_FRAGMENT + "?x=1"
        self._payload = payload

    def json(self):
        return self._payload


def _video(i: int) -> dict:
    return {
        "aweme_info": {
            "aweme_id": "v%d" % i,
            "desc": "标题%d" % i,
            "author": {"nickname": "a"},
            "statistics": {"digg_count": 1, "comment_count": 0},
            "create_time": 1700000000,
        }
    }


def _payload(n: int = 3) -> dict:
    return {"status_code": 0, "data": [_video(i) for i in range(n)]}


class _FakePage:
    """记录 `on/goto/scroll/remove` 的假 page; log 由测试传入以便与 sleep 合并排序。"""

    def __init__(self, log, respond_on_scroll=None, payload=None):
        self.log = log
        self.handlers = []
        self.respond_on_scroll = respond_on_scroll
        self.payload = payload if payload is not None else _payload()
        self.scrolls = 0

    def on(self, event, handler):
        self.log.append(("on", event))
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        self.log.append(("remove", event))

    def goto(self, url, **kwargs):
        self.log.append(("goto", url))

    def evaluate(self, js):
        self.scrolls += 1
        self.log.append(("scroll", self.scrolls))
        if self.respond_on_scroll == self.scrolls:
            self._fire()

    def title(self):
        return ""  # 非验证码中间页

    def _fire(self):
        payloads = self.payload if isinstance(self.payload, list) else [self.payload]
        for p in payloads:
            r = _FakeResponse(p)
            for h in list(self.handlers):
                h(r)


def _acts(log):
    """只保留动作类条目(丢掉 on/remove), 得到 goto/scroll/sleep 的时间线。"""
    return [a for a in log if a[0] in ("goto", "scroll", "sleep")]


def _setup(monkeypatch, respond_on_scroll=None, payload=None):
    """装配被测对象: 假 page + **虚拟时钟**。

    【为什么必须有虚拟时钟】只把 `time.sleep` 换成 no-op 是不够的 —— 兜底的
    `while time.time() < deadline: time.sleep(0.5)` 会变成**忙等**: 真实 8 秒里空转
    2860 万次(实测), 于是"请求的 sleep 时长之和"变成 1400 万秒这种荒谬值。
    所以 sleep 必须推进一个虚拟时钟, 且 `time.time` 读同一个时钟 ⇒ 循环次数确定、
    测试瞬时完成、断言的是"逻辑耗时"而不是"真实墙钟"。
    用 monkeypatch 注册 ⇒ 测试结束自动还原(不再污染同进程的其它测试)。
    """
    mod = _load_adapter()
    log = []
    clock = [1000.0]  # 【变量】虚拟时钟(秒)

    def _fake_sleep(s):
        log.append(("sleep", round(float(s), 3)))
        clock[0] += float(s)

    monkeypatch.setattr(mod.time, "sleep", _fake_sleep)
    monkeypatch.setattr(mod.time, "time", lambda: clock[0])
    ad = mod.DouyinAdapter()
    page = _FakePage(log, respond_on_scroll, payload)  # sleep 与页面动作同一条 log ⇒ 顺序可判
    ad._page = page
    return mod, ad, log, page


# ══════════════════════════════════════════════════════════════════
# 缺陷: "先干等 20s 再滚动" —— 顺序反了, 20s 是白等
# ══════════════════════════════════════════════════════════════════


def test_goto_is_immediately_followed_by_scroll_not_by_waiting(monkeypatch):
    """goto 之后第一个动作必须是滚动; 有响应时不得出现 0.5s 轮询等待(旧设计的干等)。"""
    mod, ad, log, page = _setup(monkeypatch, respond_on_scroll=1)
    videos = ad._search_videos("沥青期货")
    assert videos, "由滚动触发的响应必须被解析成视频列表"

    acts = _acts(log)
    i = next(k for k, a in enumerate(acts) if a[0] == "goto")
    assert acts[i + 1] == ("scroll", 1), (
        "goto 之后应先滚动(响应由滚动触发); 实际动作序列=%r" % (acts[i:i + 4],)
    )
    assert ("sleep", 0.5) not in acts, (
        "有响应时不应出现 0.5s 轮询等待 —— 那是旧'先等 20s'设计的指纹; 实际=%r" % (acts,)
    )


def test_no_20s_hard_wait_before_first_scroll(monkeypatch):
    """首屏响应就位时, 搜索阶段的总等待必须远小于旧的 20s 硬等。"""
    mod, ad, log, page = _setup(monkeypatch, respond_on_scroll=1)
    ad._search_videos("沥青期货")
    total_sleep = sum(a[1] for a in _acts(log) if a[0] == "sleep")
    assert total_sleep <= mod.SEARCH_SCROLLS * mod.SEARCH_SCROLL_INTERVAL, (
        "滚动期间已拿到响应时, 不应再付额外的兜底等待; 实测 %.1fs" % total_sleep
    )


def test_stops_scrolling_once_enough_responses_captured(monkeypatch):
    """捕获到 SEARCH_MAX_RESPONSES 份响应后应立即停手, 不把剩余滚动跑完。"""
    payloads = [_payload(1) for _ in range(6)]
    mod, ad, log, page = _setup(monkeypatch, respond_on_scroll=1, payload=payloads)
    ad._search_videos("沥青期货")
    assert page.scrolls == 1, "已够份数却继续滚动 = 白花时间; 实际滚动 %d 次" % page.scrolls


# ══════════════════════════════════════════════════════════════════
# 兜底必须还在: 真没响应时不能变成"滚一下就走"
# ══════════════════════════════════════════════════════════════════


def test_fallback_wait_still_used_when_no_response_at_all(monkeypatch):
    """滚完仍无响应 → 走兜底短等, 且最终按"假成功空数据"抛 DouyinBlocked。"""
    mod, ad, log, page = _setup(monkeypatch, respond_on_scroll=None)
    with pytest.raises(mod.DouyinBlocked):
        ad._search_videos("沥青期货")

    acts = _acts(log)
    assert ("sleep", mod.SEARCH_SCROLL_INTERVAL) in acts, "先滚动(触发响应)"
    assert ("sleep", 0.5) in acts, "滚动后仍无响应时必须走兜底短等"
    total_sleep = sum(a[1] for a in acts if a[0] == "sleep")
    ceiling = mod.SEARCH_SCROLLS * mod.SEARCH_SCROLL_INTERVAL + mod.SEARCH_FOLLOWUP_WAIT
    assert total_sleep <= ceiling + 0.001, (
        "兜底等待必须受 SEARCH_FOLLOWUP_WAIT 约束; 实测 %.1fs > 上限 %.1fs" % (total_sleep, ceiling)
    )
    assert total_sleep == pytest.approx(mod.SEARCH_SCROLLS * mod.SEARCH_SCROLL_INTERVAL
                                        + mod.SEARCH_FOLLOWUP_WAIT), (
        "无响应时必须用满兜底等待(否则会把『慢响应』误判成『被限流』); 实测 %.1fs" % total_sleep
    )


def test_soft_block_message_does_not_send_user_to_rescan(monkeypatch):
    """软限流不是凭证失效 —— 诊断文案必须继续明确"重新扫码无效"(别把用户引向错方向)。"""
    mod, ad, log, page = _setup(monkeypatch, respond_on_scroll=None)
    with pytest.raises(mod.DouyinBlocked) as ei:
        ad._search_videos("沥青期货")
    assert "重新扫码对软限流无效" in str(ei.value)


# ══════════════════════════════════════════════════════════════════
# 定量锁: 别把 20s 干等改回来
# ══════════════════════════════════════════════════════════════════


def test_worst_case_search_wait_beats_old_hard_wait():
    """最坏情况(滚完还没响应)也必须明显便宜于旧的 20s 硬等 —— 防止无声回退。"""
    mod = _load_adapter()
    worst = mod.SEARCH_SCROLLS * mod.SEARCH_SCROLL_INTERVAL + mod.SEARCH_FOLLOWUP_WAIT
    assert worst < 20, "最坏等待反而 >= 旧设计(20s), 等于没优化: %.1fs" % worst
    assert worst <= 13, "最坏等待应留足余量(实测路径 5.3~5.9s): %.1fs" % worst


def test_env_knobs_are_clamped(monkeypatch):
    """成本旋钮必须钳制: 手滑的 env 不该把采集变成"滚 999 次"。"""
    monkeypatch.setenv("AGENTSENSE_DY_SEARCH_SCROLLS", "999")
    assert _load_adapter().SEARCH_SCROLLS == 3, "越界应回落默认值, 而不是照单全收"

    monkeypatch.setenv("AGENTSENSE_DY_SEARCH_SCROLLS", "5")
    assert _load_adapter().SEARCH_SCROLLS == 5, "合法值应生效"

    monkeypatch.setenv("AGENTSENSE_DY_SEARCH_WAIT", "0")
    assert _load_adapter().SEARCH_FOLLOWUP_WAIT == 8, "越界应回落默认值"

    monkeypatch.delenv("AGENTSENSE_DY_SEARCH_SCROLLS", raising=False)
    monkeypatch.delenv("AGENTSENSE_DY_SEARCH_WAIT", raising=False)


def test_old_hard_wait_constant_is_gone():
    """旧常量 SEARCH_WAIT_SECONDS 不应以"没被引用但还留着"的形态残留(会让人以为还在等 20s)。"""
    mod = _load_adapter()
    assert not hasattr(mod, "SEARCH_WAIT_SECONDS")
