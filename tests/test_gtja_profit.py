"""gtja_api 利润/国君库存数据集接入的回归护栏(2026-09-28)。

【背景】用户指出国君 API 平台上有"期货品种加工与进口利润"数据。此前靠猜 60+ 端点名
  全失败, 后改为扒平台前端 JS 找到官方端点 `processProfitData.query`(加工/进口利润)与
  `inventoryData.query`(国君自建库存)。两个端点都是"全量数据集"型: 服务端**只认
  page/size, 忽略一切筛选参数**, 故必须全量分页拉取 + 磁盘缓存 + 本地按品种过滤。

【本文件守什么】
  1. dataType 拆分口径(品种 code 必须小写匹配, 否则 TA/ta 大小写会漏) —— 最容易回归的点;
  2. 全量分页的收尾判据(本页不满 = 最后一页), 防止多打一轮请求或漏页;
  3. 磁盘缓存命中时不触网(这是"18s → 0.06s"的关键, 回归会让 QA 变慢 300 倍);
  4. 空/未配置/端点失败 → None(绝不让数据层异常冒泡到 QA 链路)。

【网络】完全不触网: monkeypatch 掉 _request; 缓存目录用 tmp_path 隔离(不污染用户家目录)。
"""

from __future__ import annotations

import pytest

from tradingagents.dataflows import gtja_api


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """隔离: 缓存目录指向 tmp_path, 并清掉模块级进程内缓存(否则用例间互相污染)。"""
    monkeypatch.setattr(gtja_api, "_PROFIT_CACHE_DIR", tmp_path / "profit")
    monkeypatch.setattr(gtja_api, "_GTJA_INV_CACHE_DIR", tmp_path / "inv")
    gtja_api._MEM_DATASET.clear()
    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    yield
    gtja_api._MEM_DATASET.clear()


def _profit_rows():
    """构造利润数据: ta 单口径 / eg 三口径(含中文细分) / 其它品种(应被过滤掉)。"""
    rows = []
    for i in range(30):
        day = f"2026-09-{i + 1:02d}"
        rows.append({"dataType": "ta_加工利润", "dataDate": day, "profitValue": 300.0 + i})
        rows.append({"dataType": "eg_加工利润_石脑油制", "dataDate": day, "profitValue": 600.0 + i})
        rows.append({"dataType": "eg_加工利润_合成气制", "dataDate": day, "profitValue": 2200.0 + i})
        rows.append({"dataType": "eg_进口利润", "dataDate": day, "profitValue": 750.0 + i})
        rows.append({"dataType": "rb_加工利润", "dataDate": day, "profitValue": 100.0 + i})
    return rows


# ── 1. dataType 拆分口径 ────────────────────────────────────────────────────
def test_split_data_type_lowercases_code_and_keeps_label():
    """`eg_加工利润_石脑油制` → ('eg', '加工利润_石脑油制')。"""
    assert gtja_api._split_data_type("eg_加工利润_石脑油制") == ("eg", "加工利润_石脑油制")
    assert gtja_api._split_data_type("TA_加工利润") == ("ta", "加工利润")
    # 无下划线(异常数据)不应崩, 返回 (原文小写, 空)
    assert gtja_api._split_data_type("weird") == ("", "weird")
    assert gtja_api._split_data_type("") == ("", "")


def test_fetch_profit_df_matches_code_case_insensitively(monkeypatch):
    """传 'EG' / 'eg' / 'Eg' 都应命中同一批(调用方习惯传大写, 数据里是小写)。"""
    monkeypatch.setattr(gtja_api, "_request", lambda ep, body: _profit_rows())
    for code in ("EG", "eg", "Eg"):
        df = gtja_api.fetch_profit_df(code)
        assert df is not None, code
        assert set(df["label"]) == {"加工利润_石脑油制", "加工利润_合成气制", "进口利润"}


def test_fetch_profit_df_filters_other_varieties(monkeypatch):
    """只回本品种: rb 的行不能混进 eg 的结果(prefix 匹配必须整体对齐, 不能子串)。"""
    monkeypatch.setattr(gtja_api, "_request", lambda ep, body: _profit_rows())
    df = gtja_api.fetch_profit_df("EG")
    assert df is not None
    assert all(r.startswith("eg_") for r in df["dtype"])


def test_fetch_profit_df_kind_filter(monkeypatch):
    """kind='进口' 只留进口口径; kind='加工' 只留加工口径。"""
    monkeypatch.setattr(gtja_api, "_request", lambda ep, body: _profit_rows())
    imp = gtja_api.fetch_profit_df("EG", kind="进口")
    assert imp is not None
    assert set(imp["label"]) == {"进口利润"}
    proc = gtja_api.fetch_profit_df("EG", kind="加工")
    assert proc is not None
    assert set(proc["label"]) == {"加工利润_石脑油制", "加工利润_合成气制"}


def test_fetch_profit_df_unknown_variety_returns_none(monkeypatch):
    """池内但无利润数据的品种 → None(调用方据此走降级, 不是空 DataFrame)。"""
    monkeypatch.setattr(gtja_api, "_request", lambda ep, body: _profit_rows())
    assert gtja_api.fetch_profit_df("SC") is None


def test_fetch_profit_df_empty_rows_returns_none(monkeypatch):
    """端点返回空 → None。"""
    monkeypatch.setattr(gtja_api, "_request", lambda ep, body: [])
    assert gtja_api.fetch_profit_df("EG") is None


def test_fetch_profit_df_not_configured(monkeypatch):
    """未配置密钥 → None(可逆开关: 行为退回接入前)。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: False)
    assert gtja_api.fetch_profit_df("EG") is None


def test_fetch_profit_df_endpoint_error_returns_none(monkeypatch):
    """端点抛 GTJAError → None(异常不上抛)。"""
    def boom(ep, body):
        raise gtja_api.GTJAError("接口返回错误:[300004] 接口不存在！")

    monkeypatch.setattr(gtja_api, "_request", boom)
    assert gtja_api.fetch_profit_df("EG") is None


# ── 2. 全量分页 ─────────────────────────────────────────────────────────────
def test_fetch_dataset_paginates_until_short_page(monkeypatch):
    """分页收尾判据: 返回不足一页(size)即停, 不多打一轮。"""
    page_size = gtja_api._PAGE_SIZE
    calls = []

    def fake_request(ep, body):
        calls.append(int(body["page"]))
        page = int(body["page"])
        if page == 1:
            return [{"i": i} for i in range(page_size)]  # 满页 → 继续
        if page == 2:
            return [{"i": i} for i in range(5)]  # 不满 → 收尾
        raise AssertionError("不应打到第 3 页")

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    rows = gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR)
    assert len(rows) == page_size + 5
    assert calls == [1, 2]


def test_fetch_dataset_stops_on_empty_page(monkeypatch):
    """首页就空 → 返回 [], 且不写缓存(避免把"服务端临时抽风"钉成 6h 缓存)。"""
    monkeypatch.setattr(gtja_api, "_request", lambda ep, body: [])
    rows = gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR)
    assert rows == []
    p = gtja_api._dataset_cache_path(gtja_api._PROFIT_CACHE_DIR, gtja_api.EP_PROFIT)
    assert not p.exists(), "空结果不应落盘(否则 6h 内一直读空)"


def test_fetch_dataset_respects_max_pages(monkeypatch):
    """服务端 total 异常(永远满页)时, 必须被 _MAX_PAGES 兜住, 不能死循环。"""
    n = [0]

    def fake_request(ep, body):
        n[0] += 1
        return [{"i": i} for i in range(gtja_api._PAGE_SIZE)]  # 永远满页

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    rows = gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR)
    assert n[0] == gtja_api._MAX_PAGES
    assert len(rows) == gtja_api._PAGE_SIZE * gtja_api._MAX_PAGES


# ── 3. 缓存 ─────────────────────────────────────────────────────────────────
def test_dataset_cache_hit_avoids_network(monkeypatch):
    """❗核心: 第二次取数必须命中缓存、**零请求**(线上 17.7s → 0.06s 就靠这条)。"""
    n = [0]

    def fake_request(ep, body):
        n[0] += 1
        return [{"dataType": "eg_进口利润", "dataDate": "2026-09-24", "profitValue": 1.0}]

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR)
    assert n[0] == 1
    gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR)
    assert n[0] == 1, "第二次不应再打网络"


def test_dataset_cache_survives_memory_clear(monkeypatch):
    """清掉进程内缓存后仍应命中磁盘(重启进程不必重拉)。"""
    n = [0]

    def fake_request(ep, body):
        n[0] += 1
        return [{"dataType": "ta_加工利润", "dataDate": "2026-09-24", "profitValue": 2.0}]

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR)
    gtja_api._MEM_DATASET.clear()  # 模拟进程重启
    rows = gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR)
    assert n[0] == 1
    assert len(rows) == 1


def test_dataset_cache_expires(monkeypatch):
    """超 TTL 应重拉。

    【为什么用 ttl=-1 而不是 ttl=0】ttl=0 的判据是 `(now - mtime) > 0`, 依赖"文件 mtime
    严格早于当前时间"; 在 Windows 上 mtime 精度可能让两者相等 → 判定为"未过期" → 测试
    偶发假绿(单跑过、全跑挂, 实测踩到)。传负数使不等式恒成立, 判据确定。
    """
    n = [0]

    def fake_request(ep, body):
        n[0] += 1
        return [{"dataType": "ta_加工利润", "dataDate": "2026-09-24", "profitValue": 3.0}]

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR, ttl=-1)
    gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR, ttl=-1)
    assert n[0] == 2, "ttl=-1(已过期)时磁盘缓存与内存缓存都应视为过期"


def test_force_bypasses_cache(monkeypatch):
    """force=True 绕过缓存(数据源更新后手动刷新用)。"""
    n = [0]

    def fake_request(ep, body):
        n[0] += 1
        return [{"dataType": "ta_加工利润", "dataDate": "2026-09-24", "profitValue": 4.0}]

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR)
    gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR, force=True)
    assert n[0] == 2


def test_corrupt_cache_falls_back_to_fetch(monkeypatch):
    """磁盘缓存损坏(半截 JSON)不能让取数失败, 应回落真实请求。"""
    p = gtja_api._dataset_cache_path(gtja_api._PROFIT_CACHE_DIR, gtja_api.EP_PROFIT)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"rows": [{"dataType": "ta_加工利润"', encoding="utf-8")  # 故意截断
    monkeypatch.setattr(
        gtja_api, "_request",
        lambda ep, body: [{"dataType": "ta_加工利润", "dataDate": "2026-09-24", "profitValue": 5.0}],
    )
    rows = gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR)
    assert len(rows) == 1


def test_clear_dataset_cache_removes_disk(monkeypatch):
    """clear_dataset_cache 应删掉磁盘缓存并清进程内, 返回删除文件数。"""
    monkeypatch.setattr(
        gtja_api, "_request",
        lambda ep, body: [{"dataType": "ta_加工利润", "dataDate": "2026-09-24", "profitValue": 6.0}],
    )
    gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR)
    assert gtja_api.clear_dataset_cache(gtja_api.EP_PROFIT) == 1
    assert gtja_api.clear_dataset_cache(gtja_api.EP_PROFIT) == 0  # 再清已无文件


# ── 4. 国君库存数据集 ───────────────────────────────────────────────────────
def test_fetch_gtja_inventory_df_labels_and_filter(monkeypatch):
    """国君库存三口径(期货/社会/工厂)归一 + kind 过滤。"""
    rows = []
    for i in range(25):
        day = f"2026-09-{i + 1:02d}"
        rows.append({"dataType": "eg_社会库存", "dataDate": day, "inventoryValue": 12.0 + i})
        rows.append({"dataType": "eg_期货库存", "dataDate": day, "inventoryValue": 500.0 + i})
        rows.append({"dataType": "rb_社会库存", "dataDate": day, "inventoryValue": 999.0})
    monkeypatch.setattr(gtja_api, "_request", lambda ep, body: rows)
    df = gtja_api.fetch_gtja_inventory_df("EG")
    assert df is not None
    assert set(df["label"]) == {"社会库存", "期货库存"}
    assert set(df.columns) == {"date", "dtype", "label", "inventory"}
    only_inv = gtja_api.fetch_gtja_inventory_df("EG", kind="期货")
    assert only_inv is not None
    assert set(only_inv["label"]) == {"期货库存"}


def test_fetch_gtja_inventory_df_uses_own_cache_dir(monkeypatch):
    """利润与库存各自缓存文件互不覆盖(两个端点的缓存目录不同)。"""
    monkeypatch.setattr(
        gtja_api, "_request",
        lambda ep, body: [{"dataType": "eg_社会库存", "dataDate": "2026-09-24", "inventoryValue": 1.0}],
    )
    gtja_api.fetch_gtja_inventory_df("EG")
    p_profit = gtja_api._dataset_cache_path(gtja_api._PROFIT_CACHE_DIR, gtja_api.EP_PROFIT)
    p_inv = gtja_api._dataset_cache_path(gtja_api._GTJA_INV_CACHE_DIR, gtja_api.EP_GTJA_INVENTORY)
    assert p_inv.exists()
    assert not p_profit.exists()
