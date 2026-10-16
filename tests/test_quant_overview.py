# -*- coding: utf-8 -*-
r"""技术面量化页「全品种总览 → 单品种详情」回归护栏（2026-10-14）。

============================== 用户需求 ==============================
「技术面量化的页面你直接用这个，得先有个所有品种的总览，然后点进去可以看单品种的详细情况。」

============================== 本文件守什么 ==============================
A. **后端纯函数**（离线可测，不联网）：板块归并 / 周期方向判定（含 ADX 闸门与
   非饱和的强度口径）/ 派生字段（共振·置信度）/ 关键信号文案。
B. **接口契约**：路由存在、返回字段完备、待选品种不被静默替换、单品种失败留空行。
C. **前端结构**：双视图容器 + JS 方法 + 接线；原 7 个模块**一个都不能少**（复用不重写）。
D. **语义铁律**：颜色/方向映射（多=红、空=绿）与 ADX 闸门不能反向。
E. EOL：quant_module.py=LF / web_template.html=CRLF。

============================== 三个真实教训（写进断言的依据）==============================
1. **置信度曾虚高**：分母用"已就绪周期数"⇒ 阶段 A 只有日线时 `1/1*60+40 = 100 分`，
   而真相是 4 个周期里只测了 1 个。**分母必须是目标周期总数**。
2. **趋势强度曾饱和**：只用 `votes/3*99` 时所有干净趋势恒为 ±99（无信息量）；
   乘 ADX 质量系数后才有区分度。
3. **点中的品种曾被静默替换**：品种不在下拉里时回退成"默认品种"⇒ 用户看错标的
   且无任何提示。现改为"补 option 并选它"。
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import quant_module as qm
    _IMPORT_OK = True
except Exception:  # pragma: no cover
    _IMPORT_OK = False

pytestmark = pytest.mark.skipif(not _IMPORT_OK, reason="quant_module 未就绪")

TPL = ROOT / "web_template.html"
QM = ROOT / "quant_module.py"


def _tpl() -> str:
    return TPL.read_text(encoding="utf-8", errors="replace")


def _code_only(src: str) -> str:
    """剥掉整行注释 —— 避免断言命中注释（本项目高频假绿来源）。"""
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


# ════════════════════════════════════════════════════════════
# A. 纯函数
# ════════════════════════════════════════════════════════════
class TestSectorBucket:
    def test_exact_overrides(self):
        # 括号里的限定改变归属：燃料/能源属「能源」而非「能化」
        assert qm._sector_bucket("能化(燃料)") == "能源"
        assert qm._sector_bucket("能化(能源)") == "能源"
        assert qm._sector_bucket("有色(新能源)") == "新能源"
        assert qm._sector_bucket("农产品(油脂)") == "谷物油脂"

    def test_prefix_fallback(self):
        # 前缀兜底保证扩池新增子板块时不漏（这就是不逐项硬编码的意义）
        assert qm._sector_bucket("能化(化工)") == "能化"
        assert qm._sector_bucket("能化(橡胶)") == "能化"
        assert qm._sector_bucket("能化(芳烃)") == "能化"      # 未来新增也要能落位
        assert qm._sector_bucket("有色(基本金属)") == "有色"
        assert qm._sector_bucket("农产品(生鲜)") == "农产品"

    def test_empty_and_unknown(self):
        assert qm._sector_bucket("") == "其他"
        assert qm._sector_bucket(None) == "其他"
        assert qm._sector_bucket("股指") == "股指"          # 未知就原样返回，不硬套

    def test_whole_pool_maps_without_leftover(self):
        """全池每个品种都必须能归到一个粗板块（没有"其他"漏网）。"""
        buckets = {qm._sector_bucket(qm._meta(c).get("sector_cn") or "") for c in qm._pool()}
        assert "其他" not in buckets, f"有品种没归到板块: {buckets}"


def _bars(closes, *, vol=1):
    return [{"date": f"d{i}", "open": c, "high": c * 1.001, "low": c * 0.999,
             "close": c, "volume": vol} for i, c in enumerate(closes)]


class TestPeriodDirection:
    def test_insufficient_data_is_flat(self):
        for bad in ([], _bars([100] * 10), _bars([100] * 34)):
            r = qm._period_direction(bad)
            assert r["dir"] == "flat" and r["bars"] < 35

    def test_uptrend_is_up(self):
        r = qm._period_direction(_bars([100 + i for i in range(80)]))
        assert r["dir"] == "up" and r["label"] == "多"
        assert r["strength"] > 0

    def test_downtrend_is_down(self):
        r = qm._period_direction(_bars([200 - i for i in range(80)]))
        assert r["dir"] == "down" and r["label"] == "空"
        assert r["strength"] < 0

    def test_flat_market_is_neutral(self):
        """ADX 闸门：横盘必须判「震」而不是硬给方向。"""
        r = qm._period_direction(_bars([100 + (0.1 if i % 2 else -0.1) for i in range(80)]))
        assert r["dir"] == "flat", f"横盘被判成 {r['dir']}（ADX 闸门失效？adx={r['adx']}）"

    def test_strength_not_saturated(self):
        """★ 强度必须**体现 ADX 质量**，不能一律 ±99。

        【为什么用「缓涨+噪声」这条序列】实测（2026-10-14）：
          陡涨/缓涨/极缓涨（无噪声）→ ADX 都是 100 ⇒ 无论口径如何 strength 都是 ±99，
          **测不出"是否饱和"**（第一版断言就是这样被 M3 变异骗过、存活）。
          而 `100 + i*0.3 + (±0.8)` 这条 → **ADX 26 ⇒ 饱和口径 99 / 正确口径 51**，
          才真正区分得开。
        """
        weak_trend = _bars([100 + i * 0.3 + (0.8 if i % 3 else -0.8) for i in range(80)])
        r = qm._period_direction(weak_trend)
        assert r["dir"] == "up", f"样本方向应为 up（实得 {r['dir']}）—— 样本本身不对"
        assert r["adx"] is not None and r["adx"] < 50, (
            f"该样本 ADX 应显著小于 50 才有甄别力（实得 {r['adx']}）"
        )
        assert abs(r["strength"]) < 99, (
            f"ADX={r['adx']:.0f} 却给出饱和强度 {r['strength']} ⇒ 强度口径丢掉了 ADX 质量项"
            "（退化成了 votes/3*99）"
        )
        # 与"无噪声陡涨"对比：ADX 更高 ⇒ 强度应更大（同向）
        strong = qm._period_direction(_bars([100 + i * 2 for i in range(80)]))
        assert strong["dir"] == "up"
        assert abs(strong["strength"]) > abs(r["strength"]), (
            "强趋势的强度未高于弱趋势 ⇒ ADX 质量项没起作用"
        )

    def test_strength_range_and_sign(self):
        """强度取值范围与符号一致性。"""
        up = qm._period_direction(_bars([100 + i * 2 for i in range(80)]))
        dn = qm._period_direction(_bars([200 - i * 2 for i in range(80)]))
        assert up["dir"] == "up" and up["strength"] > 0
        assert dn["dir"] == "down" and dn["strength"] < 0
        for r in (up, dn):
            assert -99 <= r["strength"] <= 99


class TestFinalize:
    def _row(self, periods, score=0):
        return {"periods": periods, "score": score}

    def test_resonance_counts_dominant_side(self):
        per = {"day": {"dir": "up", "bars": 100}, "60": {"dir": "up", "bars": 100},
               "15": {"dir": "down", "bars": 100}, "5": {"dir": "up", "bars": 100}}
        r = self._row(per, 1)
        qm._ov_finalize(r, total=4)
        assert r["resonance"] == 3 and r["resonance_dir"] == "up"
        assert r["period_ready"] == 4

    def test_confidence_denominator_is_target_total(self):
        """★ 核心回归：分母必须是**目标周期总数**。

        第一版用"已就绪周期数"做分母 ⇒ 阶段 A 只有日线时 1/1*60+40 = **100 分**，
        而真相是 4 个周期里只测了 1 个 —— 会把 1/4 共振误报成满置信度。
        """
        per = {"day": {"dir": "up", "bars": 100}}
        r = self._row(per, 5)   # 技术分满
        qm._ov_finalize(r, total=4)
        assert r["period_ready"] == 1
        assert r["confidence"] <= 55, (
            f"只测了 1/4 个周期就给 {r['confidence']} 分 ⇒ 分母又用成『已就绪周期数』了"
        )
        # 全周期共振 + 技术分满 才允许 100
        full = {"day": {"dir": "up", "bars": 1}, "60": {"dir": "up", "bars": 1},
                "15": {"dir": "up", "bars": 1}, "5": {"dir": "up", "bars": 1}}
        r2 = self._row(full, 5)
        qm._ov_finalize(r2, total=4)
        assert r2["confidence"] == 100

    def test_verdict_uses_score_until_periods_complete(self):
        """周期没取全时按技术分给结论，不要用偏低的共振数硬判"中"。"""
        per = {"day": {"dir": "up", "bars": 100}}
        r = self._row(per, 3)
        qm._ov_finalize(r, total=4)
        assert r["verdict"] == "多"

    def test_state_tracks_day_direction(self):
        for d, want in (("up", "偏多"), ("down", "偏空"), ("flat", "震荡")):
            r = self._row({"day": {"dir": d, "bars": 100}}, 0)
            qm._ov_finalize(r, total=4)
            assert r["state"] == want

    def test_finalize_is_idempotent(self):
        """两阶段渐进会反复调用 ⇒ 必须幂等（同输入同输出）。"""
        per = {"day": {"dir": "up", "bars": 1}, "60": {"dir": "up", "bars": 1}}
        r = self._row(per, 2)
        qm._ov_finalize(r, total=4)
        snap = {k: r[k] for k in ("resonance", "confidence", "verdict", "state", "key_signal")}
        for _ in range(3):
            qm._ov_finalize(r, total=4)
        assert {k: r[k] for k in snap} == snap


class TestKeySignal:
    def test_mentions_action_and_strength(self):
        r = {"periods": {"day": {"dir": "down", "strength": -42, "adx": 30, "bars": 1}},
             "ma_state": "空头排列", "rsi14": 50}
        s = qm._ov_key_signal(r)
        assert "趋势强度" in s and "-42" in s
        assert "做空" in s, f"下跌趋势未给出做空建议: {s}"
        assert "空头排列" in s

    def test_flat_says_no_chase(self):
        r = {"periods": {"day": {"dir": "flat", "strength": 0, "adx": 12, "bars": 1}},
             "ma_state": "均线纠缠", "rsi14": 50}
        s = qm._ov_key_signal(r)
        assert "无趋势" in s or "<20" in s
        assert "不追单边" in s

    def test_rsi_extremes_flagged(self):
        base = {"periods": {"day": {"dir": "up", "strength": 50, "adx": 30, "bars": 1}},
                "ma_state": "多头排列"}
        assert "超买" in qm._ov_key_signal({**base, "rsi14": 78})
        assert "超卖" in qm._ov_key_signal({**base, "rsi14": 22})

    def test_never_raises_on_empty(self):
        assert isinstance(qm._ov_key_signal({}), str)


class TestLiquidityGrade:
    def test_three_tiers_and_order(self):
        oi = {f"C{i}": (i + 1) * 1000.0 for i in range(9)}
        g = qm._liquidity_grade(oi)
        assert set(g.values()) == {"低", "中", "高"}
        assert g["C0"] == "低" and g["C8"] == "高"

    def test_empty_is_empty(self):
        assert qm._liquidity_grade({}) == {}
        assert qm._liquidity_grade({"A": None}) == {}


class TestFallbackRow:
    def test_row_shape_stable(self):
        r = qm._ov_fallback_row("TA", "boom")
        for k in ("variety", "name", "sector", "periods", "state", "verdict",
                  "resonance", "period_total", "confidence", "key_signal", "error"):
            assert k in r, f"兜底行缺字段 {k}"
        assert r["period_ready"] == 0 and r["confidence"] == 0


# ════════════════════════════════════════════════════════════
# B. 接口契约
# ════════════════════════════════════════════════════════════
@pytest.fixture(autouse=True)
def _no_overview_bg_thread(monkeypatch):
    """★ 禁止本文件用例真起「总览后台计算」线程。

    【为什么必须禁】接口用例会走 `_ov_start` ⇒ 起 **daemon 线程**做取数。
    测试结束后线程仍在跑，后果有两条（2026-10-14 实测）：
      ① 解释器关闭时刷屏 `总览后台计算异常: cannot schedule new futures after
         interpreter shutdown` + 一堆 `Task was destroyed but it is pending!`
         —— 看着像真故障，实际是测试卫生问题；
      ② ★ 更要紧：该线程此刻可能正停在 `tq_source` 的**日志隔离窗口**内
         （它会临时替换 logger 类、事后再还原），于是**污染其它测试对 logger
         身份的断言**。实测与 `tests/test_tq_source.py` 同跑时
         `TestLoggingHijackIsolated::test_module_logger_identity_survives` 变红，
         而两文件**各自单跑都全绿**（典型顺序污染）。

    【为什么可以禁】本文件的接口用例只断言「字段契约 / 首屏不阻塞 / 周期口径 /
    板块过滤」，**都不依赖后台线程真的跑完**（`done=False` 本来就是首屏的预期态）。
    """
    import threading as _th

    class _NoStart(_th.Thread):
        def start(self):          # 只构造、不真起线程
            return None

    monkeypatch.setattr(_th, "Thread", _NoStart)
    yield
    # 清掉本文件留下的总览状态：避免不同 varieties 的 key 累积、且不留副作用给别的测试
    qm._OV_STATE.clear()


class TestApiContract:
    @pytest.fixture(scope="class")
    def client(self):
        import os
        os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"
        import web_app
        return web_app.app.test_client()

    def test_route_registered(self, client):
        """路由真的注册了（401 ≠ 路由不存在，故关鉴权后再打）。"""
        r = client.get("/api/quant/overview?varieties=TA")
        assert r.status_code == 200
        assert (r.get_json() or {}).get("ok") is True

    def test_returns_progress_fields(self, client):
        d = client.get("/api/quant/overview?varieties=TA").get_json()
        for k in ("ready", "total", "done", "pools", "periods", "rows"):
            assert k in d, f"缺字段 {k}"
        assert isinstance(d["pools"], list) and d["pools"][0] == "全部品种"
        assert d["total"] >= 1

    def test_responds_immediately_without_waiting(self, client):
        """★ 首屏必须立刻返回（不能同步算 153s）—— 这是两阶段渐进的全部意义。"""
        import time
        t = time.time()
        r = client.get("/api/quant/overview?varieties=TA")
        el = time.time() - t
        assert r.status_code == 200
        assert el < 20, f"首次请求耗时 {el:.1f}s ⇒ 又变回同步计算了"

    def test_periods_are_four(self, client):
        d = client.get("/api/quant/overview?varieties=TA").get_json()
        keys = [p["key"] for p in d["periods"]]
        assert keys == ["day", "60", "15", "5"], f"周期口径变了: {keys}"

    def test_sector_filter(self, client):
        d = client.get("/api/quant/overview?varieties=TA,M,SC").get_json()
        pool = d["pools"][1]
        d2 = client.get(f"/api/quant/overview?varieties=TA,M,SC&sector={pool}").get_json()
        assert d2["count"] <= d["total"]
        for r0 in d2["rows"]:
            assert r0["sector"] == pool

    def test_unknown_variety_falls_back_to_pool(self):
        """未知品种应回退成整池 —— 只验**逻辑**，不真拉全池。

        ⚠️ 不能直接打 `?varieties=NOPE`：那会回退成 21 品种并**真的**拉起后台
        取数线程（每个用例一次 ⇒ 变异自检连跑 13 轮时把进程压到被 SIGTERM）。
        这里断源码里的回退写法 + 用一个小样本验证"池外被过滤"。
        """
        src = QM.read_text(encoding="utf-8")
        code = _code_only(src)
        assert "codes = [c for c in codes if c in pool] or pool" in code, \
            "未知/池外品种的回退逻辑丢了"
        # 小样本验证：池外码被过滤掉，只剩池内的
        import os
        os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"
        import web_app
        c = web_app.app.test_client()
        d = c.get("/api/quant/overview?varieties=TA,NOPE,M").get_json()
        assert d["total"] == 2, f"池外码未被过滤：total={d['total']}"


# ════════════════════════════════════════════════════════════
# C. 前端结构
# ════════════════════════════════════════════════════════════
class TestFrontendStructure:
    def test_two_view_containers(self):
        h = _tpl()
        assert 'id="q-overview-view"' in h
        assert 'id="q-detail-view"' in h

    def test_js_methods_present(self):
        h = _tpl()
        for m in ("loadQuantOverview(force)", "qOpenDetail(code)", "qBackOverview()",
                  "_renderQuantOverview()", "_stopQuantOverview()", "_qovBuildPills(",
                  "_qovRowHtml("):
            assert m in h, f"缺方法 {m}"

    def test_switchtab_enters_overview(self):
        h = _tpl()
        assert "if (t === 'quant') {" in h
        assert "else this.loadQuantOverview();" in h, "switchTab 没把总览作为默认视图"

    def test_detail_view_wraps_original_modules(self):
        """★ 原有 7 个模块必须**一个不少**地留在详情里（复用而非重写）。"""
        h = _tpl()
        for cid in ("tab-quant-tools", "chart-q-kline", "q-tech-body", "q-signal-body",
                    "q-bt-stats", "q-rank-table", "q-verdict-body", "q-cmp-corr"):
            assert f'id="{cid}"' in h, f"原有模块容器 {cid} 丢了"
        # 且它们都在 #q-detail-view 内部
        i = h.index('id="q-detail-view"')
        j = h.index('id="q-overview-view"')
        assert i > j, "详情视图应排在总览之后"

    def test_pending_variety_respected(self):
        """★ 点中的品种不许被静默替换（第一版回退成默认品种 ⇒ 用户看错标的）。"""
        h = _tpl()
        assert "_qovPendingSym" in h
        assert "if (want && !valid(want)) {" in _code_only(h.replace("//", "#")), \
            "缺「点中品种不在下拉里就补 option」的分支"
        assert "sel.value = want ||" in h, "未优先采用点中的品种"

    def test_row_click_wired(self):
        h = _tpl()
        assert "App.qOpenDetail(" in h, "表格行没绑点击"
        assert "App.qBackOverview()" in h, "缺返回总览入口"

    def test_table_columns_match_design(self):
        h = _tpl()
        blk = h[h.index('id="q-ov-table"'):]
        blk = blk[: blk.index("</table>")]
        for col in ("品种", "板块", "最新价", "涨跌幅", "日线", "60分", "15分", "5分",
                    "状态", "共振", "结论", "置信度", "流动性", "关键信号"):
            assert col in blk, f"表头缺列 {col}"

    def test_colors_follow_china_convention(self):
        """★ 语义铁律：多=红、空=绿（与涨跌同向）。反向即为严重缺陷。"""
        code = _tpl()
        blk = code[code.index("_qovRowHtml("):]
        blk = blk[: blk.index("qOpenDetail(")]
        assert "r.verdict === '多' ? 'var(--red)'" in blk
        assert "r.verdict === '空' ? 'var(--green)'" in blk
        assert "chg >= 0 ? 'var(--red)' : 'var(--green)'" in blk

    def test_eschtml_used_for_dynamic_content(self):
        """XSS 防线只有这一道：所有动态文本必须过 _escHtml。"""
        code = _tpl()
        # ⚠️ 切片必须精确到函数体：第一版从 `_qovRowHtml(` 切到 `qOpenDetail(`
        #    会跨过若干方法，断言实际落在别的函数上（假红）。
        i = code.index("_qovRowHtml(r) {")
        j = code.index("// 【功能】点总览行", i)
        blk = code[i:j]
        assert "_escHtml" in blk, "_qovRowHtml 里没用 _escHtml"
        assert "e(r.variety)" in blk and "e(r.key_signal)" in blk, \
            "动态值没经 e(...) 包装（XSS 防线缺失）"


# ════════════════════════════════════════════════════════════
# D. EOL 铁律
# ════════════════════════════════════════════════════════════
class TestEol:
    @pytest.mark.parametrize("rel,expect_crlf", [
        ("quant_module.py", False),
        ("web_template.html", True),
    ])
    def test_eol(self, rel, expect_crlf):
        b = (ROOT / rel).read_bytes()
        crlf = b.count(b"\r\n")
        bare = b.count(b"\n") - crlf
        if expect_crlf:
            assert bare == 0, f"{rel} 出现 {bare} 个裸 LF（应为纯 CRLF）"
        else:
            assert crlf == 0, f"{rel} 出现 {crlf} 个 CRLF（应为纯 LF）"
