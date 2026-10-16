# -*- coding: utf-8 -*-
r"""天勤(TqSdk)行情数据源接入 回归护栏（2026-10-12）。

背景：项目原行情源为 AKShare(实时 futures_zh_realtime / 日线 futures_main_sina /
分钟 futures_zh_minute_sina)。新增天勤 TqSdk 作为**优先源**(日K/分钟K/实时行情),
照 gtja_api.py 的「可逆开关」范式接入:未配置账号 → 所有 fetch_* 返回 None →
调用方走原 AKShare 链路 → **行为与接入前完全一致**。

本文件验证(全部离线,不联网、不建 TqApi 连接):
  1) 可逆开关语义: 未配置 False / 配账号 True / TQ_ALLOW_ANON True;
  2) 品种代码 → 天勤主力连续合约映射(含 ZCE→CZCE 关键差异、INE 能源中心、
     inv_code 缺失品种的补丁、未知品种返回 None);
  3) 未配置时三个 fetch_* 一律返回 None(调用方据此降级);
  4) 异常兜底: tqsdk 不可用/建连失败时不抛异常(只置 last_error);
  5) 四处接入点的静态接线断言(上游确实调了 tq_source);
  6) 数据源标注字段(kline 接口返回 source、前端显示"天勤/新浪");
  7) EOL 铁律: web_app.py/commodity_futures.py/quant_module 等保持原行尾。

写断言的经验(与项目同纪律):
  - 环境变量用 monkeypatch 设/删,避免污染真实 .env;
  - 不真连天勤(测试要快、可独立跑),连接类行为用"未配置即返回 None"覆盖;
  - 静态断言只断"接线存在",不断具体实现文本。
"""
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

sys_path_ok = True
try:
    from tradingagents.dataflows import tq_source
except Exception:  # pragma: no cover
    sys_path_ok = False

pytestmark = pytest.mark.skipif(not sys_path_ok, reason="tq_source 未就绪")

TQ_ENVS = ("TQ_USER", "TQ_PASS", "TQ_ALLOW_ANON")


@pytest.fixture(autouse=True)
def _clean_tq_env(monkeypatch):
    """每个用例前清掉天勤环境变量(避免读到真实 .env 导致断言漂移)。"""
    for k in TQ_ENVS:
        monkeypatch.delenv(k, raising=False)
    yield


# ── 1. 可逆开关 ────────────────────────────────────────────────────────

class TestReversibleSwitch:
    def test_unconfigured_is_false(self):
        """未配账号且未开匿名 → False(调用方走原 AKShare 链路)。"""
        assert tq_source.configured() is False

    def test_configured_with_account(self, monkeypatch):
        monkeypatch.setenv("TQ_USER", "u")
        monkeypatch.setenv("TQ_PASS", "p")
        assert tq_source.configured() is True

    def test_partial_account_is_false(self, monkeypatch):
        """只配了 username 没配 password → 仍视为未配置(避免半配状态误连)。"""
        monkeypatch.setenv("TQ_USER", "u")
        assert tq_source.configured() is False

    def test_allow_anon(self, monkeypatch):
        monkeypatch.setenv("TQ_ALLOW_ANON", "1")
        assert tq_source.configured() is True

    def test_blank_values_are_false(self, monkeypatch):
        """空串/纯空格视为未配置(常见于 .env 留空占位)。"""
        monkeypatch.setenv("TQ_USER", "   ")
        monkeypatch.setenv("TQ_PASS", "")
        assert tq_source.configured() is False


# ── 2. 合约映射 ────────────────────────────────────────────────────────

class TestSymbolMapping:
    def test_shfe(self):
        assert tq_source.to_tq_symbol("RB") == "KQ.m@SHFE.rb"

    def test_zce_maps_to_czce(self):
        """项目元数据用 ZCE,天勤用 CZCE —— 这是最容易错的一处。"""
        assert tq_source.to_tq_symbol("TA") == "KQ.m@CZCE.TA"

    def test_ine_energy_center(self):
        """SC/LU/NR 在上海国际能源交易中心(INE),不是 SHFE。"""
        assert tq_source.to_tq_symbol("SC") == "KQ.m@INE.sc"
        assert tq_source.to_tq_symbol("LU") == "KQ.m@INE.lu"

    def test_gfex(self):
        assert tq_source.to_tq_symbol("LC") == "KQ.m@GFEX.lc"

    def test_symbol_patch_for_missing_inv_code(self):
        """项目元数据里 BZ/PS 的 inv_code 为空 → 走 _SYMBOL_PATCH 补丁。"""
        assert tq_source.to_tq_symbol("BZ") == "KQ.m@DCE.bz"
        assert tq_source.to_tq_symbol("PS") == "KQ.m@GFEX.ps"

    def test_unknown_returns_none(self):
        assert tq_source.to_tq_symbol("ZZZZ") is None
        assert tq_source.to_tq_symbol("") is None
        assert tq_source.to_tq_symbol(None) is None

    def test_all_pool_varieties_map(self):
        """21 品种池内品种应全部能映射出天勤符号(防漏配)。"""
        from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES

        missing = [c for c in sorted(ACTIVE_VARIETIES) if not tq_source.to_tq_symbol(c)]
        assert not missing, f"这些池内品种没有天勤合约映射: {missing}"


# ── 3. 未配置时一律返回 None(降级契约) ─────────────────────────────────

class TestUnconfiguredReturnsNone:
    def test_daily_bars_none(self):
        assert tq_source.fetch_daily_bars("RB") is None

    def test_minute_bars_none(self):
        assert tq_source.fetch_minute_bars("RB", "5") is None

    def test_realtime_none(self):
        assert tq_source.fetch_realtime(["RB", "TA"]) is None


# ── 4. 异常兜底(配了但连不上 → 不抛,只记错) ────────────────────────────

class TestGracefulFailure:
    def test_unknown_symbol_no_raise(self, monkeypatch):
        """配了账号但品种无映射 → 返回 None + 记错,不抛异常。"""
        monkeypatch.setenv("TQ_USER", "u")
        monkeypatch.setenv("TQ_PASS", "p")
        assert tq_source.fetch_daily_bars("ZZZZ") is None
        assert tq_source.last_error()

    def test_bad_period_no_raise(self, monkeypatch):
        """不支持的分钟周期 → None + 记错(不抛)。"""
        monkeypatch.setenv("TQ_USER", "u")
        monkeypatch.setenv("TQ_PASS", "p")
        assert tq_source.fetch_minute_bars("RB", "999") is None
        assert tq_source.last_error()

    def test_connect_failure_no_raise(self, monkeypatch):
        """建连失败(账号无效/断网)→ 捕获为 None,不向调用方抛。

        用失效账号真实建连太慢且有副作用,故此处直接替换 _Session 模拟失败。
        """
        monkeypatch.setenv("TQ_USER", "bad")
        monkeypatch.setenv("TQ_PASS", "bad")

        class _Boom:
            def __init__(self):
                pass

            def __enter__(self):
                raise RuntimeError("模拟建连失败")

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(tq_source, "_Session", _Boom)
        assert tq_source.fetch_daily_bars("RB") is None
        assert "模拟建连失败" in (tq_source.last_error() or "")


# ── 5. 数据源标注 ──────────────────────────────────────────────────────

class TestSourceLabel:
    def test_mark_and_read(self):
        tq_source.mark_source("tq")
        assert tq_source.last_source() == "tq"
        tq_source.mark_source("akshare")
        assert tq_source.last_source() == "akshare"

    def test_default_unknown(self):
        # 新线程无记录 → unknown(不清 _tls,用新线程验证隔离)
        import threading

        box = {}

        def _run():
            box["v"] = tq_source.last_source()

        t = threading.Thread(target=_run)
        t.start()
        t.join()
        assert box["v"] == "unknown"


# ── 6. 四处接入点静态接线 ──────────────────────────────────────────────

class TestWiring:
    def _read(self, rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8", errors="replace")

    def test_daily_kline_wired(self):
        src = self._read("tradingagents/dataflows/commodity_futures.py")
        assert "tq_source" in src and "_fetch_daily_via_tq" in src

    def test_minute_kline_wired(self):
        src = self._read("web_app.py")
        i = src.find("def _kline_minute")
        assert i > 0
        seg = src[i:i + 2000]
        assert "tq_source" in seg and "fetch_minute_bars" in seg
        assert "futures_zh_minute_sina" in seg, "必须保留 AKShare 降级路径"

    def test_realtime_wired(self):
        src = self._read("price_fetcher.py")
        i = src.find("def fetch_realtime_prices")
        seg = src[i:i + 3000]
        assert "tq_source" in seg and "fetch_realtime" in seg

    def test_price_file_update_wired(self):
        src = self._read("price_fetcher.py")
        i = src.find("def _update_single_price")
        seg = src[i:i + 3000]
        assert "tq_source" in seg and "fetch_daily_bars" in seg
        assert "futures_zh_daily_sina" in seg, "必须保留 AKShare 降级路径"

    def test_kline_api_exposes_source(self):
        src = self._read("quant_module.py")
        i = src.find("def api_quant_kline")
        seg = src[i:i + 3000]
        assert '"source"' in seg and "last_source" in seg

    def test_frontend_shows_source(self):
        src = self._read("web_template.html")
        assert "d.source === 'tq'" in src
        assert "天勤" in src and "新浪" in src

    def test_env_has_tq_placeholders(self):
        p = ROOT / ".env"
        if not p.exists():
            pytest.skip(".env 不在版本库/未生成")
        d = p.read_text(encoding="utf-8", errors="replace")
        for k in ("TQ_USER=", "TQ_PASS=", "TQ_ALLOW_ANON=", "TQ_TIMEOUT="):
            assert k in d, f".env 缺配置位 {k}"


# ── 7. EOL 铁律 ────────────────────────────────────────────────────────

class TestEOL:
    @pytest.mark.parametrize("rel,expect_crlf", [
        ("web_app.py", True),
        ("web_template.html", True),
        ("tradingagents/dataflows/commodity_futures.py", True),
        ("quant_module.py", False),
        ("price_fetcher.py", False),
        ("tradingagents/dataflows/tq_source.py", False),
    ])
    def test_eol_unchanged(self, rel, expect_crlf):
        b = (ROOT / rel).read_bytes()
        crlf = b.count(b"\r\n")
        bare = b.count(b"\n") - crlf
        if expect_crlf:
            assert bare == 0, f"{rel} 出现 {bare} 个裸 LF(应为纯 CRLF)"
        else:
            assert crlf == 0, f"{rel} 出现 {crlf} 个 CRLF(应为纯 LF)"


# ── 8. 第三方库对 stdlib logging 的全局劫持（2026-10-13） ────────────────

class TestLoggingHijackIsolated:
    """tqsdk 依赖的 shinny_structlog 在 **import 时无条件**重写 logging：

        logging.setLoggerClass(ShinnyLogger)
        logging.Logger.root    = ShinnyLogger(WARNING)
        logging.Logger.manager = logging.Manager(logging.Logger.root)   # ← 注册表换成新空表
        logging.getLogger      = getLogger

    其中"换 Logger.manager"最毒：注册表 dict 被换新 ⇒ **此前所有模块级
    `logger = logging.getLogger(__name__)` 全部孤儿化** ⇒ 同名再 getLogger 拿到
    **另一个对象** ⇒ 本项目按名字取 logger 的断言（unittest 的 assertLogs）收不到
    任何记录。实测症状：tests/test_rag_ask_route.py 一调 /api/research/ask（链路懒
    导入到 tqsdk）就触发，之后 tests/test_vendor_routing.py 的 assertLogs 用例
    **单跑全绿、跟在全量里必红**，报 "no logs of level WARNING or higher triggered"。

    本类守住：tq_source 在导入 tqsdk 时必须把 logging 全局状态还原。
    """

    @staticmethod
    def _snap():
        import logging

        return (logging.getLoggerClass(), logging.Logger.root,
                logging.Logger.manager, logging.getLogger)

    @staticmethod
    def _restore(snap):
        import logging

        cls, root, mgr, get_logger = snap
        logging.setLoggerClass(cls)
        logging.Logger.root = root
        logging.Logger.manager = mgr
        logging.getLogger = get_logger

    @classmethod
    def _hijack_like_shinny(cls):
        """复刻 shinny_structlog import 时的四步改写。

        【为什么用复刻而不是真导入】真导入 tqsdk 只在**本会话第一次**触发劫持，
        之后 shinny_structlog 已在 sys.modules 里，再调用不会重跑 ⇒ 断言会**假绿**
        （被前一个用例导入过就永远测不出来）。复刻四步与导入顺序无关，恒可复现。
        """
        import logging

        class _ShinnyStyleLogger(logging.Logger):
            pass

        logging.setLoggerClass(_ShinnyStyleLogger)
        logging.Logger.root = _ShinnyStyleLogger(logging.WARNING)
        logging.Logger.root.name = "root"
        logging.Logger.manager = logging.Manager(logging.Logger.root)
        logging.getLogger = lambda name=None: (
            logging.Logger.manager.getLogger(name) if name else logging.Logger.root
        )

    def test_globals_restored(self):
        import tradingagents.dataflows.tq_source as tq

        snap = self._snap()
        try:
            tq._run_isolated_from_logging_hijack(self._hijack_like_shinny)
            assert self._snap() == snap, "导入后 logging 全局状态未还原"
        finally:
            # 兜底：断言失败也不能让劫持态外溢到后续用例
            self._restore(snap)

    def test_module_logger_identity_survives(self):
        """真正的症状守卫：劫持一趟后，模块级 logger 必须仍与 getLogger(同名) 同对象。"""
        import logging

        import tradingagents.dataflows.interface as iface
        import tradingagents.dataflows.tq_source as tq

        snap = self._snap()
        try:
            tq._run_isolated_from_logging_hijack(self._hijack_like_shinny)
            assert iface.logger is logging.getLogger("tradingagents.dataflows.interface"), (
                "interface.logger 被孤儿化 ⇒ 任何按名字取 logger 的 assertLogs 都会收不到记录"
            )
        finally:
            self._restore(snap)

    def test_import_site_is_guarded(self):
        """接线断言：tqsdk 的导入必须包在隔离调用里（否则重构容易漏掉）。"""
        src = (ROOT / "tradingagents/dataflows/tq_source.py").read_text(encoding="utf-8")
        assert "_run_isolated_from_logging_hijack(_connect)" in src, \
            "_Session 必须用隔离函数包住 tqsdk 导入"
        assert src.count("from tqsdk import") == 1, \
            "tqsdk 只应有一个导入点，且必须位于 _connect 内（隔离调用内）"
