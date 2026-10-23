"""日线磁盘缓存护栏（2026-10-22）。

【背景】日线取数冷启动 5.4s（天勤短连接）/ 8.7s（失败回退 AKShare），而进程内缓存
只有 5 分钟 ⇒ 服务一重启、或隔天再分析，必付这笔钱，把 `analyst_views._compute`
顶到 16s，技术分析师（图里第一个节点）就得干等。

磁盘缓存能省掉它，但**必须只缓存"定稿"数据**：日线最后一根在盘中持续更新，那时
落盘拿到的是未完成 bar（收盘价不是最终值），拿去算 MA/RSI 就是错的。

所以本文件的护栏盯三件事：
  1. 交易时段（日盘 09:00–15:10 / 夜盘 21:00–23:30）**既不读也不写**磁盘；
  2. 非交易时段可读写，且 TTL / 数据日期 / 文件结构任何一项不对就拒绝；
  3. 任何异常都不能影响主链路（缓存坏了就当没有，去实取）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import pytest

import quant_module as Q


def _mk_bars(n: int = 40, end_date: str = "2026-10-21") -> list[dict]:
    end = datetime.strptime(end_date, "%Y-%m-%d")
    out = []
    for i in range(n):
        d = (end - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d")
        out.append(
            {
                "date": d,
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 100.0,
                "open_interest": 10.0,
            }
        )
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    """把缓存目录指到临时目录，并默认「非交易时段」。"""
    monkeypatch.setattr(Q, "_DISK_DIR", tmp_path)
    monkeypatch.setattr(Q, "_DISK_OFF", False)
    monkeypatch.setattr(Q, "_in_trading_window", lambda now=None: False)
    return tmp_path


# ---------------------------------------------------------------------------
# 1. 交易时段判断（时钟口径）
# ---------------------------------------------------------------------------
def test_trading_window_clock():
    assert Q._in_trading_window(datetime(2026, 10, 22, 10, 0)) is True, "周四日盘应视为会变"
    assert Q._in_trading_window(datetime(2026, 10, 22, 9, 0)) is True
    assert Q._in_trading_window(datetime(2026, 10, 22, 15, 0)) is True
    assert Q._in_trading_window(datetime(2026, 10, 22, 15, 30)) is False, "收盘 10 分钟后应已定稿"
    assert Q._in_trading_window(datetime(2026, 10, 22, 22, 0)) is True, "夜盘同样会改写当日 bar"
    assert Q._in_trading_window(datetime(2026, 10, 24, 10, 0)) is False, "周六不交易"
    assert Q._in_trading_window(datetime(2026, 10, 22, 8, 30)) is False, "盘前可用昨日定稿"


# ---------------------------------------------------------------------------
# 2. 交易时段：既不读也不写
# ---------------------------------------------------------------------------
def test_trading_window_blocks_read_and_write(env, monkeypatch):
    bars = _mk_bars()
    Q._disk_write("TA", 250, bars)
    assert (env / "TA_250.json").exists()

    monkeypatch.setattr(Q, "_in_trading_window", lambda now=None: True)
    assert Q._disk_read("TA", 250) is None, "交易时段必须拒绝磁盘缓存（最后一根未定稿）"

    monkeypatch.setattr(Q, "_DISK_DIR", env / "during")
    Q._disk_write("RB", 250, bars)
    assert not (env / "during").exists(), "交易时段落盘的 bar 是半根，不许写"


# ---------------------------------------------------------------------------
# 3. 正常读写往返
# ---------------------------------------------------------------------------
def test_write_then_read_roundtrip(env):
    bars = _mk_bars()
    Q._disk_write("TA", 250, bars)
    got = Q._disk_read("TA", 250)
    assert got == bars, "往返后内容必须完全一致（数值类型不能漂成字符串）"


# ---------------------------------------------------------------------------
# 4. 各种不该命中的情况
# ---------------------------------------------------------------------------
def test_expired_ttl_rejected(env):
    Q._disk_write("TA", 250, _mk_bars())
    p = env / "TA_250.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["saved_at"] = time.time() - (Q._DISK_TTL_HOURS + 1) * 3600
    p.write_text(json.dumps(d), encoding="utf-8")
    assert Q._disk_read("TA", 250) is None, "超过 TTL 必须拒绝（否则隔天还在用旧收盘价）"


def test_stale_last_bar_rejected(env):
    """TTL 挡的是写入时间；这一条挡的是数据本身旧（长假后第二天最容易踩）。"""
    Q._disk_write("TA", 250, _mk_bars(end_date="2026-09-01"))
    assert Q._disk_read("TA", 250) is None


def test_corrupt_file_ignored(env):
    (env / "TA_250.json").write_text("{not-json", encoding="utf-8")
    assert Q._disk_read("TA", 250) is None, "损坏文件必须当作没有，不能抛异常"


def test_code_mismatch_rejected(env):
    Q._disk_write("TA", 250, _mk_bars())
    (env / "TA_250.json").rename(env / "RB_250.json")
    assert Q._disk_read("RB", 250) is None, "文件内容标着 TA，不能当 RB 用"


def test_too_few_bars_rejected(env):
    """门槛是「不缓存空壳」，不是「必须够算指标」——只取最新价的调用方也要能命中。"""
    Q._disk_write("TA", 250, _mk_bars(n=3))
    assert Q._disk_read("TA", 250) is None, "1~3 根多是取数部分失败，不能当有效缓存"


def test_switch_off(env, monkeypatch):
    Q._disk_write("TA", 250, _mk_bars())
    monkeypatch.setattr(Q, "_DISK_OFF", True)
    assert Q._disk_read("TA", 250) is None, "QS_DISK_CACHE=0 必须彻底关掉"


# ---------------------------------------------------------------------------
# 5. 接入点：命中磁盘就不再打上游
# ---------------------------------------------------------------------------
def test_cached_skips_upstream_when_disk_hit(env, monkeypatch):
    bars = _mk_bars()
    Q._disk_write("TA", 250, bars)
    Q._CACHE.clear()

    calls = {"n": 0}

    def _boom(code, days):
        calls["n"] += 1
        return []

    monkeypatch.setattr(Q, "_load_daily_bars", _boom)
    got = Q._daily_bars_cached("TA", 250)
    assert got == bars
    assert calls["n"] == 0, "磁盘命中却仍打了上游 —— 缓存等于没接上"


def test_miss_then_write_on_fetch(env, monkeypatch):
    """非交易时段实取成功后要落盘，供下次（含重启后）命中。"""
    bars = _mk_bars()
    Q._CACHE.clear()
    monkeypatch.setattr(Q, "_load_daily_bars", lambda code, days: bars)

    assert Q._daily_bars_cached("TA", 250) == bars
    assert (env / "TA_250.json").exists(), "实取后没有落盘，下次仍要再拉一遍"
    Q._CACHE.clear()
    monkeypatch.setattr(Q, "_load_daily_bars", lambda code, days: [])
    assert Q._daily_bars_cached("TA", 250) == bars
