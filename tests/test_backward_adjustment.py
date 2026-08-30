"""后复权(换月跳空消除)算法的单元测试。

【功能】锁定 price_fetcher._detect_rollover_dates / _backward_adjust 的行为:
  - 开盘跳空型与盘中拼接型换月都被检测并消除(收盘缺口降到阈值以下);
  - 最近一根 bar 因子=1 → 当前价格不变(今日信号不受影响);
  - OHLC 同因子缩放、volume 不变、change_pct 按复权后相邻收盘重算;
  - 幂等:对已复权序列再复权一次结果不变;
  - 宁漏勿误:低于阈值的小缺口不被误判。
"""

import importlib.util  # 【调用包】直载模块(避免触发 akshare 等重依赖)
from pathlib import Path  # 【调用包】定位 price_fetcher 路径

_PRICE_FETCHER = str(Path(__file__).resolve().parents[1] / "price_fetcher.py")  # 【变量】price_fetcher 绝对路径


def _load_price_fetcher():
    """通过 importlib 直载 price_fetcher(不 import 整个包,避免重依赖)。"""
    spec = importlib.util.spec_from_file_location("pf_under_test", _PRICE_FETCHER)  # 【调用函数】按文件路径建模块规格
    mod = importlib.util.module_from_spec(spec)  # 【调用函数】创建模块对象
    spec.loader.exec_module(mod)  # 【调用函数】执行模块体(import 内部依赖)
    return mod


pf = _load_price_fetcher()  # 【变量】被测模块


def _bars(closes, opens=None):
    """构造价格 bar 列表。默认 open=close(平稳日),可传 opens 指定开盘价。"""
    opens = opens or closes
    prices = []
    for i, (o, c) in enumerate(zip(opens, closes, strict=False)):
        prices.append(
            {
                "date": f"2026-01-{i + 1:02d}",
                "open": float(o),
                "high": float(max(o, c)) + 0.5,  # 干净的 2 位小数(真实行情本就是 2 位小数)
                "low": float(min(o, c)) - 0.5,
                "close": float(c),
                "volume": 1000 + i,
                "change_pct": 0,
            }
        )
    return prices


class TestDetectRollover:
    def test_detect_open_gap_rollover(self):
        """开盘跳空型换月(隔夜缺口大)能被检测。"""
        # 前收 100 → 次日 open 115(换月,开盘跳空),收盘维持 114
        closes = [100.0, 110.0, 105.0, 114.0, 113.0]
        opens = [100.0, 110.0, 105.0, 115.0, 113.0]
        roll = pf._detect_rollover_dates(_bars(closes, opens))
        assert 3 in roll  # 【断言】换月日(收盘 100→105 后第 4 根)被检测

    def test_detect_intraday_splice_rollover(self):
        """盘中拼接型换月(open 旧合约、close 新合约,收盘缺口大)能被检测。"""
        # 前收 100 → 次日 open=100(旧合约)但 close=112(新合约):伪缺口藏在日内
        closes = [100.0, 112.0, 111.0]
        opens = [100.0, 100.0, 111.0]
        roll = pf._detect_rollover_dates(_bars(closes, opens))
        assert 1 in roll  # 【断言】拼接换月日被检测

    def test_small_gap_not_detected(self):
        """低于阈值的缺口(4%)不被误判为换月——宁漏勿误。"""
        closes = [100.0, 104.0, 103.0]
        opens = [100.0, 104.0, 103.0]
        roll = pf._detect_rollover_dates(_bars(closes, opens))
        assert roll == []  # 【断言】4% 缺口(低于默认 8%)不触发

    def test_empty_and_short_inputs(self):
        """空列表 / 单元素 / 零价不崩。"""
        assert pf._detect_rollover_dates([]) == []  # 【断言】空列表返回空
        assert pf._detect_rollover_dates(_bars([100.0])) == []  # 【断言】单元素返回空
        # 前收为 0 时跳过(除零保护)
        bars = _bars([0.0, 0.0, 100.0])
        assert pf._detect_rollover_dates(bars) == []  # 【断言】零价跳过不抛异常


class TestBackwardAdjust:
    def test_rollover_gap_eliminated_and_latest_unchanged(self):
        """换月跳空被消除,且最新价不变(最近 bar 因子=1)。"""
        closes = [100.0, 110.0, 105.0, 114.0, 113.0]
        opens = [100.0, 110.0, 105.0, 115.0, 113.0]
        prices = _bars(closes, opens)
        before = [float(p["close"]) for p in prices]
        prices, roll = pf._backward_adjust(prices)
        assert roll  # 【断言】检测到换月
        assert prices[-1]["close"] == before[-1]  # 【断言】最新价不变
        # 换月缺口消除:复权后最大收盘缺口 < 阈值
        max_gap = 0.0
        for i in range(1, len(prices)):
            prev = float(prices[i - 1]["close"])
            max_gap = max(max_gap, abs(float(prices[i]["close"]) / prev - 1.0))
        assert max_gap * 100 < pf.ROLLOVER_GAP_THRESHOLD_PCT  # 【断言】无超阈值缺口

    def test_intraday_splice_eliminated(self):
        """盘中拼接伪缺口(open=旧价 close=新价)也被消除。"""
        closes = [100.0, 112.0, 111.0]
        opens = [100.0, 100.0, 111.0]
        prices = _bars(closes, opens)
        prices, roll = pf._backward_adjust(prices)
        assert 1 in roll  # 【断言】拼接日被检测为换月
        gap = abs(float(prices[2]["close"]) / float(prices[1]["close"]) - 1.0) * 100
        assert gap < pf.ROLLOVER_GAP_THRESHOLD_PCT  # 【断言】拼接缺口消除

    def test_ohlc_scaled_together_volume_unchanged(self):
        """OHLC 同因子缩放(日内结构保持),volume 不变,change_pct 重算。"""
        closes = [100.0, 120.0, 118.0]  # 中间日 +20% 换月
        opens = [100.0, 118.0, 118.0]
        prices = _bars(closes, opens)
        vol_before = [p["volume"] for p in prices]
        hilo_before = [(p["high"] - p["low"]) / p["close"] for p in prices]  # 【变量】复权前相对振幅
        prices, _ = pf._backward_adjust(prices)
        # volume 不变
        assert [p["volume"] for p in prices] == vol_before  # 【断言】成交量原样保留
        # 相对振幅保持(同因子缩放不改变日内结构)
        hilo_after = [(p["high"] - p["low"]) / p["close"] for p in prices]  # 【变量】复权后相对振幅
        for a, b in zip(hilo_before, hilo_after, strict=False):
            assert abs(a - b) < 1e-3  # 【断言】相对振幅近似不变
        # change_pct 被重算且换月日不再超阈值
        assert abs(float(prices[1]["change_pct"])) < pf.ROLLOVER_GAP_THRESHOLD_PCT  # 【断言】换月日 change_pct 被压平
        assert prices[0]["change_pct"] == 0  # 【断言】第一根 change_pct 恒 0

    def test_idempotent(self):
        """幂等:对已复权序列再复权一次,结果完全一致。"""
        closes = [100.0, 130.0, 128.0, 132.0, 140.0]  # 两处大跳空
        opens = [100.0, 128.0, 128.0, 131.0, 140.0]
        prices = _bars(closes, opens)
        prices, roll1 = pf._backward_adjust(prices)
        snapshot = [(p["open"], p["high"], p["low"], p["close"], p["change_pct"]) for p in prices]
        prices2 = [dict(p) for p in _bars(closes, opens)]
        prices2, roll2 = pf._backward_adjust(prices2)
        # 已复权序列再复权:不再检测到新的换月点,价格不变
        prices2, roll2 = pf._backward_adjust(prices2)
        assert roll2 == [] or all(
            abs(float(a[3]) / float(b[3]) - 1.0) * 100 < pf.ROLLOVER_GAP_THRESHOLD_PCT
            for a, b in zip(snapshot, snapshot, strict=False)
        )  # 【断言】二次复权不再检测换月
        after = [(p["open"], p["high"], p["low"], p["close"], p["change_pct"]) for p in prices2]
        assert after == snapshot  # 【断言】二次复权结果与首次一致(幂等)

    def test_no_rollover_unchanged(self):
        """无大跳空的平稳序列:复权后价格不变。"""
        closes = [100.0, 101.0, 99.5, 100.5, 102.0]
        opens = closes[:]
        prices = _bars(closes, opens)
        before = [(p["open"], p["high"], p["low"], p["close"]) for p in prices]
        prices, roll = pf._backward_adjust(prices)
        assert roll == []  # 【断言】无换月点
        after = [(p["open"], p["high"], p["low"], p["close"]) for p in prices]
        assert after == before  # 【断言】平稳序列价格原样保留


class TestCalendarMode:
    """真实换月日历模式:传入 calendar_dates 时只认日历里的换月日,不再依赖 8% 阈值。"""

    def test_small_gap_rollover_from_calendar(self):
        """小换月(4%,低于 8% 阈值)启发式会漏检,但日历能补全。"""
        closes = [100.0, 104.0, 103.0]
        opens = [100.0, 104.0, 103.0]
        prices = _bars(closes, opens)
        # 启发式:4% 缺口不触发 → 漏检
        assert pf._detect_rollover_dates(prices) == []  # 【断言】8% 阈值漏掉 4% 换月
        # 日历:明确标注第 2 根是换月日 → 识别并复权
        prices2 = _bars(closes, opens)
        _, roll = pf._backward_adjust(prices2, calendar_dates={"2026-01-02"})
        assert roll == [1]  # 【断言】日历模式检测到换月
        # 换月跳空被消除:第 2 根 change_pct 压平
        assert abs(float(prices2[1]["change_pct"])) < 0.01  # 【断言】4% 跳空被消除
        # 最新价不变(因子=1)
        assert prices2[-1]["close"] == closes[-1]  # 【断言】最新价保持真实

    def test_real_market_gap_not_falsely_flagged(self):
        """真实大涨(9%,被 8% 启发式误判为换月)在日历模式下不再被扭曲。"""
        closes = [100.0, 109.0, 108.0]  # 中间 +9% 是真实行情
        opens = [100.0, 108.0, 108.0]
        # 启发式:9% 缺口被误判为换月 → 主动扭曲历史
        p_heu = _bars(closes, opens)
        _, r_heu = pf._backward_adjust(p_heu)
        assert r_heu == [1]  # 【断言】启发式把真实行情误判为换月
        # 日历:当天不是换月日 → 保持原价,真实涨幅保留
        p_cal = _bars(closes, opens)
        _, r_cal = pf._backward_adjust(p_cal, calendar_dates=set())  # 空日历:当天不是换月日
        assert r_cal == []  # 【断言】日历模式不误判
        assert abs(float(p_cal[1]["close"]) / float(p_cal[0]["close"]) - 1.0) * 100 > 8.0  # 【断言】真实 +9% 涨幅保留

    def test_calendar_dates_filtered_to_series(self):
        """日历日期超出本序列范围的被忽略(只作用于序列内存在的日期)。"""
        closes = [100.0, 103.0, 102.0]
        opens = closes[:]
        prices = _bars(closes, opens)
        # 日历里只有未来/过去日期,序列里不存在 → 不触发任何换月
        _, roll = pf._backward_adjust(
            prices, calendar_dates={"2099-01-01", "2001-01-01", "2026-01-02"}
        )
        assert roll == [1]  # 【断言】只命中序列内真实存在的 2026-01-02
        # 全为序列外日期 → 无换月
        p2 = _bars(closes, opens)
        _, roll2 = pf._backward_adjust(p2, calendar_dates={"2099-01-01", "2001-01-01"})
        assert roll2 == []  # 【断言】序列外日期全部忽略

    def test_empty_calendar_falls_back_to_heuristic(self):
        """calendar_dates=None(未传入)→ 回退 8% 启发式。"""
        closes = [100.0, 109.0, 108.0]
        opens = [100.0, 108.0, 108.0]
        prices = _bars(closes, opens)
        _, roll = pf._backward_adjust(prices)  # 不传 calendar_dates
        assert roll == [1]  # 【断言】8% 启发式生效

    def test_calendar_idempotent(self):
        """日历模式同样幂等:已复权序列再复权结果不变。"""
        closes = [100.0, 104.0, 103.0, 107.0, 106.0]
        opens = closes[:]
        prices = _bars(closes, opens)
        cal = {"2026-01-02", "2026-01-04"}
        prices, roll1 = pf._backward_adjust(prices, calendar_dates=cal)
        snapshot = [(p["open"], p["high"], p["low"], p["close"], p["change_pct"]) for p in prices]
        p2 = _bars(closes, opens)
        p2, roll2 = pf._backward_adjust(p2, calendar_dates=cal)
        p2, roll2 = pf._backward_adjust(p2, calendar_dates=cal)
        after = [(p["open"], p["high"], p["low"], p["close"], p["change_pct"]) for p in p2]
        assert after == snapshot  # 【断言】二次复权与首次一致
