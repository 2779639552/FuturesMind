"""采集「降级上报」的回归测试 (2026-09-18)。

被锁定的真实缺陷
----------------
【用户看到的现象】"情绪数据每次采集数这么少, 今天只有 200 多条"。

【根因】微博适配器的 `_check_login()` 自 2026-09-15 起就会调
  `m.weibo.cn/api/config` 自检登录态, 发现 `login=False`(游客态/已过期)时
  **确实打印了 logger.warning** —— 但这条警告:
    · 只进 logger, **不进** `collect_run_*.json` 运行报告;
    · 于是报告里 `status=ok`、`auth_failures=0`, 页面/告警一切正常;
    · 子进程的 stderr 还会被父进程的输出淹没, 没人会去翻。
  而它的真实后果是**静默的**: 第 1 页匿名可取(约 8 条), 第 2 页起返回
  `ok=-100 + passport signin`, `search()` 优雅 `break` —— 采集"成功",
  只是每个关键词被砍到 1 页。实测 24 个关键词里 18 个恰好 8 条(整数常数指纹),
  整轮命中量约少采 3/4, 且从 09-14 起连续几十轮无人发现。

【修复】新增一条与 status **正交**的 degraded 通道:
  适配器自检到"能跑但少采"时调 `note_degradation()` 登记 → BatchCollector 写进
  运行报告(degraded / degradations / degradation_codes) → scheduler 写一条
  带去重的 `collection_degraded_{platform}` 告警 → update_tracker 透传到
  /api/update_deltas → 前端在增量表对应单元格亮出「⚠ 降级」。

【本文件钉住的纪律(改动前请先读)】
  1. **降级绝不改 status、绝不改退出码** —— 采集确实拿到了数据(实测 106 条),
     把它报成 auth_failed 会与"拿到了数据"自相矛盾, 还会把整平台误判为颗粒无收;
  2. 降级**不得并入 failed** —— 同理, 否则"拿到了 90 条"和"平台失败"打起来;
  3. 降级是**持续状态**, 告警必须按平台+时间窗去重, 否则每轮刷屏 → 告警疲劳;
  4. 旧报告(无 degraded 键)一律按"未降级"处理, **不伪造**;
  5. 适配器登记的每个降级码都必须在 UI 有对应文案, 否则前端只能显示裸 code。

测试手法: 复用 test_batch_collect_channel_health 的桩(不需要 Playwright / 网络 /
真实登录态), 但**平台基类用的是真身** —— 被测对象正是基类上的 note_degradation,
若把它换成桩, 测试就变成自证。
"""

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

import pytest

from tests.test_batch_collect_channel_health import (
    _FakeAdapter,
    _items,
    _make_collector,
    _write_report as _write_run_report,
    bc,
)

_VALIDATE_DIR = Path(__file__).resolve().parents[1] / "data_collection" / "validate"
_TEMPLATE = Path(__file__).resolve().parents[1] / "web_template.html"


# ══════════════════════════════════════════════════════════════════
# 加载真实的 platforms 包(仅 base + weibo, 不拖 Playwright)
# ══════════════════════════════════════════════════════════════════


def _load_real_platforms():
    """把 validate/platforms 作为轻量包加载, 得到**真实**的 base 与 weibo_adapter。

    【为什么不用现成的 platforms 桩】channel_health 的桩把 `platforms.base`
      替换成了空壳类, 而本文件要测的 note_degradation 就在基类上 —— 用桩等于把
      被测对象抽掉。
    【为什么不直接 import platforms】`platforms/__init__.py` 会连带 import 小红书/
      知乎/雪球适配器(依赖 Playwright), 单测没必要付这个代价。
    【为什么包名带前缀】避免与 sys.modules 里的 "platforms"(别的测试的桩)撞名。
    """
    pkg_name = "_vdp_under_test"
    if pkg_name in sys.modules:
        return sys.modules[pkg_name + ".base"], sys.modules[pkg_name + ".weibo_adapter"]
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(_VALIDATE_DIR / "platforms")]
    sys.modules[pkg_name] = pkg
    for name in ("base", "weibo_adapter"):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{name}", _VALIDATE_DIR / "platforms" / f"{name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{name}"] = mod  # 必须先注册, 相对 import 才找得到
        spec.loader.exec_module(mod)
    return sys.modules[pkg_name + ".base"], sys.modules[pkg_name + ".weibo_adapter"]


_pbase, _pweibo = _load_real_platforms()
PlatformAdapter = _pbase.PlatformAdapter
WeiboAdapter = _pweibo.WeiboAdapter


class _DegMixin:
    """把**真实基类**的降级 API 挂到假适配器上。

    【为什么】write_report 的降级读取路径应当用真身验证; 直接给假适配器塞一个
    `degradations = [...]` 属性只能测出"字典拼装对不对", 测不出登记时的合并/计数。
    """

    note_degradation = PlatformAdapter.note_degradation
    degradations = PlatformAdapter.degradations


class _DegFakeAdapter(_DegMixin, _FakeAdapter):
    """既能被采集器驱动(继承 _FakeAdapter), 又带真实降级 API。"""


# ══════════════════════════════════════════════════════════════════
# A. 基类降级 API
# ══════════════════════════════════════════════════════════════════


class _Bare(PlatformAdapter):
    """最小可实例化的适配器(只为拿到 note_degradation / degradations 真身)。"""

    name, display_name = "bare", "裸适配器"

    def init(self):
        pass

    def search(self, keyword, count):
        return []

    def normalize(self, raw_item, detail, keyword):
        return None


def test_fresh_adapter_has_no_degradation():
    """新实例默认无降级 —— 证明懒初始化成立, 不依赖子类调 super().__init__()。"""
    assert _Bare().degradations == []


def test_note_degradation_merges_same_code_and_counts():
    """同一 code 重复登记必须**合并计数**, 而不是塞成 N 条。

    ⚠️ 这是"受影响关键词数"这一指标的来源: 微博每个翻页失败的词各调一次
    note_degradation, 合并后的 count 就等于受影响词数 —— 若改成并列 N 条,
    前端会渲染出 N 个重复的降级项, 反而看不出规模。
    """
    a = _Bare()
    a.note_degradation("auth_page2_blocked", "第 2 页被要求登录", impact="少采")
    a.note_degradation("auth_page2_blocked", "第 2 页被要求登录", impact="少采")
    a.note_degradation("auth_page2_blocked", "第 2 页被要求登录", impact="少采")

    degs = a.degradations
    assert len(degs) == 1, "同 code 应合并为一条"
    assert degs[0]["count"] == 3, "count 必须累加为 3"
    assert degs[0]["code"] == "auth_page2_blocked"


def test_note_degradation_keeps_distinct_codes_separate():
    a = _Bare()
    a.note_degradation("auth_degraded", "游客态")
    a.note_degradation("auth_check_unavailable", "自检失败")
    assert sorted(d["code"] for d in a.degradations) == ["auth_check_unavailable", "auth_degraded"]


def test_note_degradation_ignores_empty_code():
    """空 code 无意义且会污染前端映射表 -> 直接忽略。"""
    a = _Bare()
    a.note_degradation("", "无码")
    assert a.degradations == []


def test_degradations_returns_copy_so_callers_cannot_corrupt_state():
    a = _Bare()
    a.note_degradation("auth_degraded", "游客态")
    got = a.degradations
    got.append({"code": "injected"})  # 外部乱改
    assert len(a.degradations) == 1, "返回的列表必须是浅拷贝, 外部 append 不得污染内部"


def test_note_degradation_never_raises():
    """登记降级绝不能反过来搞崩采集 —— 传畸形参数也必须静默返回。"""
    a = _Bare()
    a.note_degradation(None, None)  # type: ignore[arg-type]
    a.note_degradation("x", "y", severity=None)  # severity 缺省应回落到 warning
    assert a.degradations[0]["severity"] == "warning"


# ══════════════════════════════════════════════════════════════════
# B. 微博适配器: 自检与翻页必须把失效登记成降级
# ══════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    """假的 requests.Session: 只回答 api/config 自检。"""

    def __init__(self, payload=None, exc=None):
        self.payload, self.exc = payload, exc
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        if self.exc:
            raise self.exc
        return _Resp(self.payload)


def _weibo_with_session(session):
    ad = WeiboAdapter()  # 真 __init__: 只置空字段, 不发请求
    ad._session = session
    return ad


def test_weibo_guest_state_is_registered_as_degradation():
    """★ 本次修复的核心用例。

    login=False 此前只打 logger.warning, 报告里 status 仍是 ok。现在必须登记降级,
    否则"每关键词只能取第 1 页"这件事在页面上永远不可见(实测因此漏了几十轮)。
    """
    ad = _weibo_with_session(_Session({"data": {"login": False}}))
    ad._check_login()

    degs = ad.degradations
    assert len(degs) == 1 and degs[0]["code"] == "auth_degraded"
    assert "login=False" in degs[0]["message"], "消息里要留下接口返回的现场证据"
    assert degs[0]["impact"], "必须说清少采了多少(用户看的就是这句)"
    assert "weibo_login.py" in degs[0]["fix_hint"], "必须给出可自助修复的下一步"


def test_weibo_logged_in_records_no_degradation():
    """登录有效时不得凭空造降级 —— 否则告警会被噪音淹没, 又回到没人看。"""
    ad = _weibo_with_session(_Session({"data": {"login": True}}))
    ad._check_login()
    assert ad.degradations == []
    assert ad._login_ok is True


def test_weibo_selfcheck_failure_is_also_a_degradation():
    """自检请求失败 = 我们**不知道**登录态是否有效, 这本身就该上报。

    因为失手时唯一剩下的信号是"每关键词恰好 8 条"这种间接指纹, 极难被发现。
    """
    ad = _weibo_with_session(_Session(exc=RuntimeError("connection reset")))
    ad._check_login()
    assert [d["code"] for d in ad.degradations] == ["auth_check_unavailable"]
    assert ad._login_ok is None


def _signin_payload():
    return {"ok": -100, "url": "https://passport.weibo.com/sso/signin?entry=search"}


def _page1_payload(n=8):
    return {"ok": 1}, [{"mblog": {"mid": f"m{i}", "text_raw": f"帖子{i}"}} for i in range(n)], 0


def test_weibo_page2_signin_is_registered_and_data_still_returned(monkeypatch):
    """★ 翻页被拒必须登记降级, 同时**第 1 页的数据照常返回**(优雅降级不能被破坏)。

    这才是这个缺陷最阴的地方: 采集"成功"、退出码 0、有数据 —— 所以必须靠
    正交的 degraded 通道把它暴露出来, 而不是让本轮失败。
    """
    monkeypatch.setattr(_pweibo, "MIN_DELAY", 0.0)
    monkeypatch.setattr(_pweibo, "MAX_DELAY", 0.0)
    ad = _weibo_with_session(_Session({"data": {"login": False}}))

    def fake_page(keyword, page):
        if page == 1:
            return _page1_payload(8)
        return _signin_payload(), [], 0

    ad._search_page = fake_page  # 实例属性遮蔽真实网络请求
    items = ad.search("沥青期货", count=30)
    assert len(items) == 8, "第 1 页数据必须照常返回(降级 ≠ 失败)"

    degs = ad.degradations
    assert len(degs) == 1 and degs[0]["code"] == "auth_page2_blocked"
    assert degs[0]["count"] == 1
    assert "沥青期货" in degs[0]["message"], "留下首个触发词, 便于人工复现"


def test_weibo_page2_signin_count_equals_affected_keyword_count(monkeypatch):
    """count 必须等于受影响的关键词数 —— 它是"这轮被砍了多少"的量化指标。"""
    monkeypatch.setattr(_pweibo, "MIN_DELAY", 0.0)
    monkeypatch.setattr(_pweibo, "MAX_DELAY", 0.0)
    ad = _weibo_with_session(_Session({"data": {"login": False}}))
    ad._search_page = lambda kw, page: (_page1_payload(8) if page == 1 else (_signin_payload(), [], 0))

    for kw in ("沥青期货", "原油期货", "燃料油"):
        ad.search(kw, count=30)

    degs = {d["code"]: d for d in ad.degradations}
    assert degs["auth_page2_blocked"]["count"] == 3


# ══════════════════════════════════════════════════════════════════
# C. 运行报告: 降级必须落盘, 但不得动 status
# ══════════════════════════════════════════════════════════════════


def test_write_report_carries_degradations(tmp_path, monkeypatch):
    adapter = _DegFakeAdapter(_items(3))
    adapter.note_degradation(
        "auth_degraded", "游客态", impact="每词只剩第 1 页", fix_hint="重新扫码"
    )
    collector = _make_collector(adapter, platform="weibo")
    path = _write_run_report(collector, monkeypatch, tmp_path, "ok", "采集到 3 条")

    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["degraded"] is True
    assert rec["degradation_codes"] == ["auth_degraded"]
    assert rec["degradations"][0]["impact"] == "每词只剩第 1 页"


def test_write_report_degradation_must_not_flip_status_or_exit_code(tmp_path, monkeypatch):
    """★ 纪律用例: 降级平台**没有失败**。

    微博实测既降级又采到 106 条。若把 status 改成 auth_failed, 就会与
    "确实拿到了数据"自相矛盾, 并把整平台误判为颗粒无收 —— 这正是当初决定
    "另开正交通道"而不是"复用一个失败状态"的原因。
    """
    adapter = _DegFakeAdapter(_items(3))
    adapter.note_degradation("auth_degraded", "游客态")
    collector = _make_collector(adapter, platform="weibo")
    # 必须造出"确实采到了数据"的现场: 微博游客态的真实形态是 notes=106、有命中关键词。
    # 若留着 0 条, _decide_status 会因"整轮零命中"判成 blocked —— 那是另一条规则,
    # 与本用例要钉的"降级不得改 status"无关, 会掩盖真正的被测行为。
    collector.total_notes = 3
    collector.stats.with_data_keywords = 1
    path = _write_run_report(collector, monkeypatch, tmp_path, "ok", "采集到 3 条")

    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["status"] == "ok"
    assert rec["auth_failures"] == 0

    # ⚠️ 不能只断言 collector.exit_code(初值本就是 EXIT_OK, 恒真等于没测):
    # 必须直接问判定函数 —— 它才是决定退出码与告警分支的地方。
    status, _diag, _fix, code = collector._decide_status()
    assert status == "ok", "降级不得把 status 抬成 auth_failed"
    assert code == bc.EXIT_OK, "降级不得改退出码, 否则调度会把它当故障处理"


def test_write_report_without_degradation_stays_clean(tmp_path, monkeypatch):
    """没有降级的平台不得被标成降级(避免为了"有字段"而凭空造)。"""
    collector = _make_collector(_DegFakeAdapter(_items(2)), platform="weibo")
    path = _write_run_report(collector, monkeypatch, tmp_path, "ok", "x")
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["degraded"] is False and rec["degradations"] == []


def test_write_report_survives_broken_degradations_property(tmp_path, monkeypatch):
    """降级读取抛异常时, 报告仍必须落盘(降级只是锦上添花, 不能拖垮主流程)。"""

    class _Broken(_FakeAdapter):
        @property
        def degradations(self):
            raise RuntimeError("boom")

    collector = _make_collector(_Broken(_items(1)), platform="weibo")
    path = _write_run_report(collector, monkeypatch, tmp_path, "ok", "x")
    assert path is not None, "降级读取失败不得导致报告写不出来"
    assert json.loads(path.read_text(encoding="utf-8"))["degraded"] is False


def test_run_report_stdout_line_contains_degradations(tmp_path, monkeypatch, capsys):
    """COLLECT_RUN 那一行也要带降级 —— scheduler 正是解析它来发告警的。"""
    adapter = _DegFakeAdapter(_items(2))
    adapter.note_degradation("auth_degraded", "游客态")
    collector = _make_collector(adapter, platform="weibo")
    _write_run_report(collector, monkeypatch, tmp_path, "ok", "x")

    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if bc.RUN_LINE_PREFIX in ln)
    rec = json.loads(line.split(bc.RUN_LINE_PREFIX, 1)[1].strip())
    assert rec["degradation_codes"] == ["auth_degraded"]


# ══════════════════════════════════════════════════════════════════
# D. 增量聚合: 降级要透传到轮次/平台, 且不得并入 failed
# ══════════════════════════════════════════════════════════════════

import update_tracker as ut  # noqa: E402


def _rec(note_id, platform="weibo"):
    return {
        "note_id": note_id,
        "platform": platform,
        "title": "库存去化跟踪",
        "author_name": "测试作者",
        "publish_time": "2026-09-18 09:00:00",
    }


def _batch(out, platform, ts, records):
    p = out / f"batch_{platform}_{ts}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def _report(out, platform, ts, status="ok", notes=0, degradations=None, started_at=None):
    if started_at is None:
        started_at = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:]}"
    body = {
        "platform": platform,
        "status": status,
        "notes": notes,
        "diagnosis": "",
        "started_at": started_at,
        "ended_at": started_at,
    }
    if degradations is not None:  # 不传 = 模拟 2026-09-18 之前的旧报告(无该键)
        body["degraded"] = bool(degradations)
        body["degradations"] = degradations
    p = out / f"collect_run_{platform}_{ts}.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return p


_DEG = [{
    "code": "auth_degraded",
    "message": "游客态",
    "impact": "每词只剩第 1 页",
    "fix_hint": "重新扫码",
    "severity": "warning",
    "count": 1,
}]


@pytest.fixture()
def out(tmp_path):
    d = tmp_path / "output"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_update_tracker_propagates_degraded_to_round_and_platform(out):
    _batch(out, "weibo", "20260918_080100", [_rec("wb:1"), _rec("wb:2")])
    _report(out, "weibo", "20260918_080100", notes=2, degradations=_DEG)

    d = ut.build_deltas(out)
    rd = d["rounds"][0]
    slot = rd["platforms"]["weibo"]

    assert slot["degraded"] is True
    assert slot["degradations"][0]["code"] == "auth_degraded"
    assert slot["status"] == "ok", "降级平台的状态仍然是 ok"
    assert rd["degraded"]["weibo"][0]["code"] == "auth_degraded"
    assert rd["failed"] == {}, "★ 降级绝不能并入 failed: 它没失败, 只是少采"

    plat = next(p for p in d["platforms"] if p["key"] == "weibo")
    assert plat["degraded"] is True
    assert plat["degraded_updates"] == 1


def test_update_tracker_old_report_without_degraded_key_is_not_degraded(out):
    """2026-09-18 之前的报告没有 degraded 键 → 按"未降级"处理, **不伪造**。"""
    _batch(out, "weibo", "20260918_080100", [_rec("wb:1")])
    _report(out, "weibo", "20260918_080100", notes=1)  # 不传 degradations

    d = ut.build_deltas(out)
    assert d["rounds"][0]["platforms"]["weibo"]["degraded"] is False
    assert d["rounds"][0]["degraded"] == {}


def test_update_tracker_last_run_wins_for_degradation(out):
    """同一轮同平台跑了两次: 早先降级、后来恢复 → 应显示**已恢复**。

    ⚠️ 这是与 status 刻意不同的归并规则: status 问"数据拿到没有"(成功过一次就算成功),
    降级问"这个平台现在的能力是否被削" —— 后者必须看**最后一次**。
    """
    _batch(out, "weibo", "20260918_080100", [_rec("wb:1")])
    _report(out, "weibo", "20260918_080100", notes=1, degradations=_DEG)
    _batch(out, "weibo", "20260918_081000", [_rec("wb:2")])
    _report(out, "weibo", "20260918_081000", notes=1, degradations=[])

    d = ut.build_deltas(out)
    rounds = [r for r in d["rounds"] if "weibo" in r["platforms"]]
    assert len(rounds) == 1, "10 分钟间隔应聚成同一轮"
    assert rounds[0]["platforms"]["weibo"]["runs"] == 2
    assert rounds[0]["platforms"]["weibo"]["degraded"] is False, "最后一次已恢复 → 不应再报降级"


# ══════════════════════════════════════════════════════════════════
# E. 调度告警: 每平台一条, 且按时间窗去重(避免告警疲劳)
# ══════════════════════════════════════════════════════════════════

from scheduler import _maybe_alert_degraded  # noqa: E402


class _FakeDb:
    def __init__(self):
        self.alerts = []

    def create_alert(self, alert_type, title, message="", variety="", severity="info", data=None):
        self.alerts.append({
            "alert_type": alert_type, "title": title,
            "message": message, "severity": severity,
        })
        return len(self.alerts)

    def get_latest_alert(self, alert_type, hours=None):
        for a in reversed(self.alerts):
            if a["alert_type"] == alert_type:
                return a
        return None


def test_degraded_alert_emitted_with_warning_severity():
    db = _FakeDb()
    _maybe_alert_degraded(db, "weibo", _DEG)

    assert len(db.alerts) == 1
    a = db.alerts[0]
    assert a["alert_type"] == "collection_degraded_weibo"
    assert a["severity"] == "warning", "降级不是 error: 采集确实成功了"
    assert "auth_degraded" in a["title"]
    assert "重新扫码" in a["message"], "告警里必须带上可执行的修复动作"


def test_degraded_alert_is_suppressed_within_window():
    """★ 降级是**持续状态**, 不去重会每天 2 条刷满告警表 → 告警疲劳 → 又没人看。"""
    db = _FakeDb()
    for _ in range(5):
        _maybe_alert_degraded(db, "weibo", _DEG)
    assert len(db.alerts) == 1, "20h 窗口内同一平台只报一次"


def test_degraded_alert_dedup_key_includes_platform():
    """两个平台同时降级时, 后者不得被前者抑制(否则只是把静默换了个平台)。"""
    db = _FakeDb()
    _maybe_alert_degraded(db, "weibo", _DEG)
    _maybe_alert_degraded(db, "xhs", _DEG)
    assert {a["alert_type"] for a in db.alerts} == {
        "collection_degraded_weibo", "collection_degraded_xhs",
    }


def test_degraded_alert_noop_on_empty_or_broken_input():
    """没有降级 / 输入畸形时不得写告警(空告警同样是噪音)。"""
    db = _FakeDb()
    _maybe_alert_degraded(db, "weibo", [])
    _maybe_alert_degraded(db, "weibo", None)
    _maybe_alert_degraded(db, "weibo", ["不是dict"])
    assert db.alerts == []


def test_degraded_alert_survives_db_failure():
    """告警写入失败绝不能影响采集主流程(历史事故: create_alert 抛错炸掉整个任务)。"""

    class _Boom:
        def get_latest_alert(self, *a, **k):
            raise RuntimeError("db down")

        def create_alert(self, *a, **k):
            raise RuntimeError("db down")

    _maybe_alert_degraded(_Boom(), "weibo", _DEG)  # 不抛即通过


# ══════════════════════════════════════════════════════════════════
# F. 前后端契约: 渲染器读的字段必须由后端真实提供
# ══════════════════════════════════════════════════════════════════


def _template() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def test_frontend_reads_only_fields_backend_provides(out):
    """防漂移: 前端读 `v.degradations` / `p.degraded` / `p.degraded_updates`,

    这三者必须真的出现在后端响应里, 否则页面会渲染出 undefined/空标记 ——
    典型症状是"降级了但页面一片安静", 与本次要修的缺陷同形。
    """
    _batch(out, "weibo", "20260918_080100", [_rec("wb:1")])
    _report(out, "weibo", "20260918_080100", notes=1, degradations=_DEG)
    d = ut.build_deltas(out)

    slot = d["rounds"][0]["platforms"]["weibo"]
    plat = next(p for p in d["platforms"] if p["key"] == "weibo")
    for field in ("degraded", "degradations"):
        assert field in slot, f"前端读 slot.{field}, 后端必须提供"
    for field in ("degraded", "degraded_updates"):
        assert field in plat, f"前端读 platform.{field}, 后端必须提供"

    src = _template()
    assert "v.degradations" in src, "单元格降级标记依赖 v.degradations"
    assert "p.degraded" in src and "degraded_updates" in src, "摘要行依赖平台汇总的降级标志"


def test_every_registered_degradation_code_has_ui_text():
    """★ 契约: 适配器里 note_degradation 登记的**每个** code 都要有中文文案。

    否则前端只能显示裸 code(如 `auth_page2_blocked`), 用户看不懂 ——
    等于这次修复只做了一半。新增降级码时本用例会提醒你同步 UI。
    """
    codes = set()
    for f in sorted((_VALIDATE_DIR / "platforms").glob("*.py")):
        codes |= set(re.findall(r'note_degradation\(\s*["\']([a-z0-9_]+)["\']', f.read_text(encoding="utf-8")))
    assert codes, "没有扫描到任何降级码, 说明扫描逻辑或实现跑偏了"

    block = re.search(r"_deltaDegradeText:\s*\{(.*?)\n  \},", _template(), re.S)
    assert block, "模板里找不到 _deltaDegradeText 映射表"
    mapped = set(re.findall(r"([a-z0-9_]+)\s*:", block.group(1)))

    missing = codes - mapped
    assert not missing, f"这些降级码在 UI 里没有文案(会显示成裸 code): {sorted(missing)}"


def test_warn_ink_token_defined_for_both_themes():
    """★ 降级提示色的**对比度**也是正确性的一部分。

    `--amber`(#fbbf24)只在深色主题定义过, 亮色主题下等于白底上的浅黄 ——
    若直接复用它, 降级提示在亮色模式下几乎读不出来, 而"看不见"正是本次要修的缺陷。
    故单开 --warn-ink 并两主题各给一值; 本用例锁死这一点。
    """
    src = _template()
    assert src.count("--warn-ink:") >= 2, "--warn-ink 必须在 :root 与 [data-theme=light] 各定义一次"

    root_val = re.search(r":root\s*\{.*?--warn-ink:\s*([^;]+);", src, re.S)
    light_block = re.search(r'\[data-theme="light"\]\s*\{(.*?)\n\}', src, re.S)
    assert root_val and light_block, "主题令牌块定位失败"
    light_val = re.search(r"--warn-ink:\s*([^;]+);", light_block.group(1))
    assert light_val, "亮色主题缺少 --warn-ink 覆盖"
    assert root_val.group(1).strip() != light_val.group(1).strip(), "两主题取值必须不同, 否则等于没覆盖"
    assert ".delta-deg" in src and "var(--warn-ink)" in src, "降级标记必须用该令牌着色"
