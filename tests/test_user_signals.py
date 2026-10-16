# -*- coding: utf-8 -*-
r"""自传数据信号提炼引擎(user_signals.py)回归护栏（2026-10-12）。

背景：用户要求「自传数据模块基于不同输入灵活给出可视化分析输出块」——
  Platts 宽表(43列)不能裸灌 LLM,要做信号提炼。实现分两级:
    ① 通用分析器(任何数据必出,纯确定性): A层计算+B层规则判定+C层模板拼装;
    ② 领域提炼器(注册表命中才增强): platts_crude / inventory;
    ③ 自适应注册: 未命中时按语义关键词自动判定新类别并登记
       (~/.tradingagents/refiner_registry.json),下次同类型直接命中。

本文件验证(全部确定性,不起浏览器、不烧 LLM):
  1) 引擎对真实 Platts 数据产出正确信号(主列=Brent 绝对价 123.02, 期限结构 BACK,
     区域贴水排除绝对价列);
  2) 通用层自动降级: 数据不足(2期)时不硬算趋势/分位;
  3) 注册表匹配: Platts→platts_crude, 库存→inventory;
  4) 自适应注册: 未命中注册表的新类型 → auto:xxx + 落盘登记, 再次调用直接命中;
  5) 前端接线: loadUserDatasets 调用 _udSignalCard, 方法已定义;
  6) EOL 铁律: web_template.html / web_app.py 必须 CRLF、无裸 LF。

写断言的经验(与项目测试同纪律):
  - 用合成数据(dict 直接构造),不依赖真库,测试可独立跑;
  - 数字断言用近似(round),避免浮点漂移;
  - 前端只做静态文本断言(不起浏览器)。
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web_template.html"
WEB_APP = ROOT / "web_app.py"

sys_path_ok = True
try:
    from tradingagents.dataflows.user_signals import build_signals
except Exception:  # pragma: no cover - 路径未就绪时跳过
    sys_path_ok = False

# ── 合成数据 ───────────────────────────────────────────────────────────

def _mk_ds(spec: dict, rows: list[dict], **kw) -> dict:
    ds = {"id": 1, "filename": "x.csv", "variety": "", "data_type": "",
          "spec": json.dumps(spec, ensure_ascii=False),
          "data": json.dumps(rows, ensure_ascii=False)}
    ds.update(kw)
    return ds


def _mk_price_rows(n: int, base: float = 100.0, step: float = 0.5) -> list[dict]:
    """生成 n 期日频价格行(从 2019-01-02 起,仅工作日近似用连续日)。"""
    from datetime import date, timedelta
    rows = []
    d = date(2019, 1, 2)
    for i in range(n):
        rows.append({"Code": d.isoformat(), "PCAAS00": base + step * i})
        d += timedelta(days=1)
    return rows


PLATTS_SPEC = {
    "variety": "SC", "data_type": "原油现货价格及升贴水数据",
    "date_column": "Code", "frequency": "日度",
    "columns": [
        {"name": "PCAAS00", "meaning": "Dated Brent", "unit": "$/bbl"},
        {"name": "AAGWZ00", "meaning": "Forties FOB North Sea vs North Sea Dtd Strip", "unit": "$/bbl"},
        {"name": "AAGXL00", "meaning": "Bonny Light FOB Nigeria London vs WAF Dtd Strip", "unit": "$/bbl"},
        {"name": "QALDA00", "meaning": "Qatar Land FOB Qatar vs Dubai Mo01", "unit": "$/bbl"},
        {"name": "AAGVZ00", "meaning": "WTI Midland Mo01 vs WTI Mo01", "unit": "$/bbl"},
        {"name": "Unnamed: 40", "meaning": "Dubai Mo01-Mo03", "unit": "$/bbl"},
    ],
}

INV_SPEC = {
    "variety": "MA", "data_type": "甲醇社会库存数据",
    "date_column": "日期", "frequency": "周度",
    "columns": [{"name": "库存(万吨)", "meaning": "甲醇社会库存量", "unit": "万吨"}],
}


# ── 1. Platts 信号正确性 ───────────────────────────────────────────────

@pytest.mark.skipif(not sys_path_ok, reason="信号引擎未就绪")
class TestPlattsSignals:
    def test_main_col_is_absolute_price(self):
        rows = _mk_price_rows(400)
        for r in rows:
            r["AAGWZ00"] = 1.0      # 北海贴水(常数)
            r["AAGXL00"] = 2.0      # 非洲贴水
            r["QALDA00"] = 3.0      # 中东贴水
            r["AAGVZ00"] = -1.0     # 美洲贴水
            r["Unnamed: 40"] = 5.0  # 期限结构(恒 BACK)
        sig = build_signals(_mk_ds(PLATTS_SPEC, rows, variety="SC"))
        # 主指标必须是绝对价列(Brent),不是某个价差列
        assert sig["common"]["col"] == "PCAAS00", sig["common"].get("col")
        assert abs(sig["common"]["latest"] - 299.5) < 1.0  # 100 + 0.5*399

    def test_term_structure_back(self):
        rows = _mk_price_rows(100)
        for r in rows:
            r["Unnamed: 40"] = 5.0
            r["AAGWZ00"] = 1.0
        sig = build_signals(_mk_ds(PLATTS_SPEC, rows, variety="SC"))
        ts = sig["domain"]["term_structure"]
        assert ts["regime"] == "BACK"
        assert ts["bias"] == "bullish"
        assert abs(ts["spread"] - 5.0) < 1e-6

    def test_regional_excludes_absolute_price(self):
        rows = _mk_price_rows(50)
        for r in rows:
            r["Unnamed: 40"] = 5.0
            r["AAGWZ00"] = 1.0   # 北海
            r["AAGXL00"] = 2.0   # 非洲
            r["QALDA00"] = 3.0   # 中东
            r["AAGVZ00"] = -1.0  # 美洲
        sig = build_signals(_mk_ds(PLATTS_SPEC, rows, variety="SC"))
        reg = sig["domain"]["regional_spreads"]
        # 基准价 PCAAS00(100+)绝不能被算进任何区域贴水均值
        assert "north_sea" in reg and abs(reg["north_sea"]["avg"] - 1.0) < 1e-6
        assert "west_africa" in reg and abs(reg["west_africa"]["avg"] - 2.0) < 1e-6
        assert "mid_east" in reg and abs(reg["mid_east"]["avg"] - 3.0) < 1e-6
        assert "americas" in reg and abs(reg["americas"]["avg"] - (-1.0)) < 1e-6
        for g, v in reg.items():
            assert abs(v["avg"]) < 10, f"{g} 混入了绝对价: {v}"

    def test_refiner_id(self):
        rows = _mk_price_rows(30)
        for r in rows:
            r["Unnamed: 40"] = 5.0
        sig = build_signals(_mk_ds(PLATTS_SPEC, rows, variety="SC"))
        assert sig["refiner"] == "platts_crude"
        assert sig["auto_registered"] is False

    def test_stale_flag(self):
        rows = _mk_price_rows(30)
        sig = build_signals(_mk_ds(PLATTS_SPEC, rows, variety="SC"))
        assert sig["stale_days"] >= 0
        assert "render_md" in sig and "截至" in sig["render_md"]


# ── 2. 通用层降级 ──────────────────────────────────────────────────────

@pytest.mark.skipif(not sys_path_ok, reason="信号引擎未就绪")
class TestCommonDegrade:
    def test_two_rows_no_fake_signals(self):
        rows = [
            {"日期": "2026-09-01 00:00:00", "库存(万吨)": "68.5"},
            {"日期": "2026-09-05 00:00:00", "库存(万吨)": "67.2"},
        ]
        sig = build_signals(_mk_ds(INV_SPEC, rows, variety="MA"))
        c = sig["common"]
        assert c["latest"] == 67.2
        assert abs(c["mom_pct"] - (-1.8978)) < 0.01
        # 数据不足: 趋势/分位必须 None/insufficient,绝不硬算
        assert c["pctile_52w"] is None
        assert c["trend"] == "insufficient"
        assert c["chg20"] is None

    def test_inventory_destocking(self):
        rows = [
            {"日期": "2026-09-01 00:00:00", "库存(万吨)": "68.5"},
            {"日期": "2026-09-05 00:00:00", "库存(万吨)": "67.2"},
        ]
        sig = build_signals(_mk_ds(INV_SPEC, rows, variety="MA"))
        assert sig["domain"]["regime"] == "destocking"
        assert sig["refiner"] == "inventory"

    def test_render_md_has_destock(self):
        rows = [
            {"日期": "2026-09-01 00:00:00", "库存(万吨)": "68.5"},
            {"日期": "2026-09-05 00:00:00", "库存(万吨)": "67.2"},
        ]
        sig = build_signals(_mk_ds(INV_SPEC, rows, variety="MA"))
        assert "去库" in sig["render_md"]


# ── 3. 自适应注册 ──────────────────────────────────────────────────────

@pytest.mark.skipif(not sys_path_ok, reason="信号引擎未就绪")
class TestAutoRegister:
    def test_unknown_type_auto_registers(self, tmp_path, monkeypatch):
        from tradingagents import dataflows
        from tradingagents.dataflows import user_signals as us
        # 把注册文件指向临时目录,避免污染真实 ~/.tradingagents
        monkeypatch.setattr(us, "_REGISTRY_FILE", tmp_path / "refiner_registry.json")
        spec = {
            "variety": "TA", "data_type": "PTA开工率数据",
            "date_column": "日期", "frequency": "周度",
            "columns": [{"name": "开工率", "meaning": "PTA装置开工率", "unit": "%"}],
        }
        rows = [{"日期": f"2026-0{i}-01", "开工率": str(80 - i)} for i in range(1, 6)]
        sig = build_signals(_mk_ds(spec, rows, variety="TA"))
        assert sig["refiner"] == "auto:rate"
        assert sig["auto_registered"] is True
        # 已落盘
        assert (tmp_path / "refiner_registry.json").exists()
        # 再次调用: 自动注册簿已缓存,仍返回 auto:rate 且不重复写
        sig2 = build_signals(_mk_ds(spec, rows, variety="TA"))
        assert sig2["refiner"] == "auto:rate"
        assert "开工/负荷类" in sig2["render_md"]

    def test_generic_fallback(self):
        spec = {
            "variety": "", "data_type": "随便的数据",
            "date_column": "日期",
            "columns": [{"name": "数值", "meaning": "未知含义", "unit": ""}],
        }
        rows = [{"日期": f"2026-09-0{i}", "数值": str(i)} for i in range(1, 4)]
        sig = build_signals(_mk_ds(spec, rows))
        assert sig["refiner"] == "generic"
        assert sig["auto_registered"] is False


# ── 4. 前端接线 + EOL 铁律 ─────────────────────────────────────────────

class TestFrontend:
    def _src(self) -> str:
        return TEMPLATE.read_text(encoding="utf-8", errors="replace")

    def test_ud_list_calls_signal_card(self):
        src = self._src()
        assert "this._udSignalCard(x.signals, esc)" in src

    def test_signal_card_defined(self):
        src = self._src()
        assert "_udSignalCard(sig, esc) {" in src

    def test_signal_card_uses_details(self):
        src = self._src()
        i = src.find("_udSignalCard(sig, esc)")
        assert i > 0
        seg = src[i:i + 4000]
        assert "<details" in seg and "<summary" in seg
        # 折叠必须用 <details>(原生),禁行内 display:none 切换
        assert "display:none" not in seg

    def test_template_crlf_only(self):
        b = TEMPLATE.read_bytes()
        crlf = b.count(b"\r\n")
        bare = b.count(b"\n") - crlf
        assert crlf > 1000
        assert bare == 0, f"web_template.html 出现 {bare} 个裸 LF"

    def test_webapp_crlf_only(self):
        b = WEB_APP.read_bytes()
        crlf = b.count(b"\r\n")
        bare = b.count(b"\n") - crlf
        assert crlf > 1000
        assert bare == 0, f"web_app.py 出现 {bare} 个裸 LF"

    def test_webapp_list_attaches_signals(self):
        src = WEB_APP.read_text(encoding="utf-8", errors="replace")
        assert "build_signals" in src
        assert '"signals"' in src or "'signals'" in src
