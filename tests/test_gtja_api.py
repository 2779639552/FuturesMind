"""GTJA(国泰君安)提供者纯函数测试: 归一化映射 + 空→None + 观点 score 归一。

【网络】不触网: normalize_* 是纯函数;fetch_viewpoint 用 monkeypatch 替换
        configured()/_request()。因此无需配 GTJA key,CI/无网也稳定通过。
"""

import contextlib  # 【调用包】并发写缓存测试里吞掉"提前中断"的哨兵异常
import os  # 【调用包】比对缓存临时文件名

from tradingagents.dataflows import gtja_api  # 【调用包】被测模块


# ---------------------------------------------------------------------------
# normalize_basis_rows: 基差行 → commodity_futures 英文 schema
# ---------------------------------------------------------------------------
def test_normalize_basis_rows_maps_smm_and_aliases():
    """同一天多现货指数(SMM/长江)→ 保留 SMM;dominant_*/near_* 同值别名;date=YYYYMMDD int。"""
    rows = [
        {"reportDate": "2026-09-01", "spotPrice": 68000, "futuresPrice": 68200,
         "basisValue": -200, "basisPremiumRate": -0.00294, "contractCode": "cu2609",
         "spotIndexName": "长江有色"},
        {"reportDate": "2026-09-01", "spotPrice": 67950, "futuresPrice": 68200,
         "basisValue": -250, "basisPremiumRate": -0.00367, "contractCode": "cu2609",
         "spotIndexName": "SMM 1#电解铜"},
        {"reportDate": "2026-09-02", "spotPrice": 68100, "futuresPrice": 68300,
         "basisValue": -200, "basisPremiumRate": -0.00293, "contractCode": "cu2609",
         "spotIndexName": "SMM 1#电解铜"},
    ]
    df = gtja_api.normalize_basis_rows(rows)
    assert df is not None
    assert list(df.columns) == gtja_api._BASIS_COLUMNS
    assert len(df) == 2
    assert list(df["date"]) == [20260901, 20260902]          # 无横杠 int,升序
    assert list(df["spot_price"]) == [67950.0, 68100.0]      # 09-01 取 SMM 而非长江
    # 近月/主力两套同值别名(国君单一参考合约)
    assert df["dominant_contract"].equals(df["near_contract"])
    assert df["dom_basis"].equals(df["near_basis"])
    assert df["dom_basis_rate"].equals(df["near_basis_rate"])
    assert df.loc[0, "dom_basis"] == -250.0


def test_normalize_basis_rows_empty_or_all_invalid_returns_none():
    """无行 / 行缺 spotPrice → None(调用方回退 AKShare),绝不返回空壳 DataFrame。"""
    assert gtja_api.normalize_basis_rows([]) is None
    assert gtja_api.normalize_basis_rows([{"reportDate": "2026-09-01"}]) is None
    assert gtja_api.normalize_basis_rows([{"reportDate": "2026-09-01", "spotPrice": None}]) is None


# ---------------------------------------------------------------------------
# normalize_inventory_rows: 仓单行 → date/inventory/change
# ---------------------------------------------------------------------------
def test_normalize_inventory_rows_maps_and_diffs():
    """onWarrant(字符串)→inventory float;date 保留 YYYY-MM-DD;change=相邻差值。"""
    rows = [
        {"tradingDay": "2026-08-31", "onWarrant": "128000"},
        {"tradingDay": "2026-09-01", "onWarrant": "128500"},
        {"tradingDay": "2026-09-02", "onWarrant": "129662"},
    ]
    df = gtja_api.normalize_inventory_rows(rows)
    assert df is not None
    assert list(df["date"]) == ["2026-08-31", "2026-09-01", "2026-09-02"]
    assert list(df["inventory"]) == [128000.0, 128500.0, 129662.0]
    assert list(df["change"]) == [0.0, 500.0, 1162.0]


def test_normalize_inventory_rows_empty_returns_none():
    assert gtja_api.normalize_inventory_rows([]) is None
    assert gtja_api.normalize_inventory_rows([{"tradingDay": "2026-09-01", "onWarrant": "x"}]) is None


# ---------------------------------------------------------------------------
# fetch_viewpoint: score 归一 + 未配置/失败降级(2026-09-03 起只拉周度,晨报下线)
# ---------------------------------------------------------------------------
def test_fetch_viewpoint_coerces_string_score(monkeypatch):
    """周度接口偶发把 score 回成字符串"0" → 统一 int,前端强度/着色才稳定。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    fake = {"score": "0", "reason": "市场谨慎", "reportDate": "2026-09-03",
            "codeName": "铜", "code": "CU"}

    def fake_request(endpoint, body):
        return [dict(fake)]

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    res = gtja_api.fetch_viewpoint("CU")
    assert res["error"] is None
    assert res["weekly"]["score"] == 0 and isinstance(res["weekly"]["score"], int)
    assert "daily" not in res  # 晨报已下线,payload 只有 weekly


def test_fetch_viewpoint_not_configured(monkeypatch):
    """未配 key → weekly None + error 提示(绝不请求网络)。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: False)
    res = gtja_api.fetch_viewpoint("CU")
    assert res["weekly"] is None and res["error"]


def test_fetch_viewpoint_failure(monkeypatch):
    """周度端点失败 → weekly None + error 记首错(UI 可区分无信号/失败)。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: True)

    def fake_request(endpoint, body):
        raise gtja_api.GTJAError("周度接口不可用")

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    res = gtja_api.fetch_viewpoint("CU")
    assert res["weekly"] is None
    assert res["error"] and "周度接口不可用" in res["error"]


def test_fetch_viewpoint_picks_latest_frame_for_code(monkeypatch):
    """.query 列表端点忽略 code 过滤(2026-09-05 契约)→ 客户端按 code 筛最新帧;
    同品种多帧取 reportDate 最大者,score 字符串仍归一 int。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    frames = [
        {"code": "sc", "reportDate": "2026-08-30", "score": "1", "reason": "上一帧"},
        {"code": "M", "reportDate": "2026-09-06", "score": 0, "reason": "别人家"},
        {"code": "SC", "reportDate": "2026-08-23", "score": "-1", "reason": "更早"},
    ]

    def fake_request(endpoint, body):
        assert endpoint == gtja_api.EP_VIEW_WEEKLY
        assert "startReportDate" in body and "endReportDate" in body  # .query 必填区间
        return frames

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    res = gtja_api.fetch_viewpoint("SC")
    assert res["error"] is None
    assert res["weekly"]["reportDate"] == "2026-08-30"   # 大小写不敏感匹配 + 取最新
    assert res["weekly"]["score"] == 1 and isinstance(res["weekly"]["score"], int)


def test_fetch_viewpoint_no_frame_for_code(monkeypatch):
    """窗口内该品种无帧 → weekly None + error 说明(前端灰字,不是红错)。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    monkeypatch.setattr(gtja_api, "_request", lambda ep, body: [{"code": "A", "reportDate": "2026-09-06"}])
    res = gtja_api.fetch_viewpoint("CU")
    assert res["weekly"] is None and "CU" in res["error"]


# ---------------------------------------------------------------------------
# fetch_inventory_df: 显式区间覆盖 / 默认近 240 天窗口(2026-09-03 数据仓库数据集)
# ---------------------------------------------------------------------------
def test_fetch_inventory_df_defaults_to_240d_window(monkeypatch):
    """两端都空 → 请求体用 [今天-240天, 今天] 默认窗口(兼容 commodity_futures 下游)。"""
    from datetime import date, timedelta

    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    seen = {}

    def fake_request(endpoint, body, **kwargs):  # **kwargs 兼容 _request 的 timeout 形参(2026-09-28 加)
        seen["body"] = body
        return [{"tradingDay": "2026-09-01", "onWarrant": "100"}]

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    res = gtja_api.fetch_inventory_df("CU")
    assert res is not None
    b = seen["body"]
    assert b["code"] == "CU"
    assert b["endReportDate"] == date.today().strftime("%Y-%m-%d")
    assert b["startReportDate"] == (date.today() - timedelta(days=240)).strftime("%Y-%m-%d")


def test_fetch_inventory_df_explicit_range_passed_through(monkeypatch):
    """显式区间 → 请求体原样用该区间,不套默认窗口。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    seen = {}

    def fake_request(endpoint, body, **kwargs):  # **kwargs 兼容 _request 的 timeout 形参(2026-09-28 加)
        seen["body"] = body
        return [{"tradingDay": "2026-08-01", "onWarrant": "50"}]

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    res = gtja_api.fetch_inventory_df("RB", "2026-07-01", "2026-08-31")
    assert res is not None
    b = seen["body"]
    assert b["startReportDate"] == "2026-07-01" and b["endReportDate"] == "2026-08-31"


def test_fetch_inventory_df_start_after_end_returns_none(monkeypatch):
    """start > end → 直接 None 且不发请求(底层与路由双重兜底,前端传反不炸)。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    called = []

    def fake_request(endpoint, body):
        called.append(body)
        return []

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    assert gtja_api.fetch_inventory_df("RB", "2026-09-02", "2026-09-01") is None
    assert called == []


# ══════════════════════════════════════════════════════════════════
# 数据集磁盘缓存:多进程并发写不得互撞(2026-09-30 修 WinError 32)
# ══════════════════════════════════════════════════════════════════


def test_dataset_cache_tmp_name_is_unique_per_writer(tmp_path):
    """临时文件名必须**每个写者唯一** —— 固定名是多进程并发写互撞的根因。

    旧实现用 `{endpoint}.tmp` 这个固定名:两个进程同时对同一端点刷新缓存时,
    A 先 os.replace 成功但句柄尚未完全释放,B 的 os.replace 就会撞上
    `[WinError 32] 另一个程序正在使用此文件`。
    (实测:6 进程 x 6 轮并发写,旧写法累计失败 28 次;修复后 0 次。)
    """
    # 拦截 os.replace,只观察本应是"临时文件名"的那一侧,不落真实文件
    seen = []

    def fake_replace(src, dst):
        seen.append((str(src), str(dst)))
        raise RuntimeError("stop here")  # 提前中断,只要拿到名字

    real_replace = gtja_api.os.replace
    gtja_api.os.replace = fake_replace
    try:
        with contextlib.suppress(RuntimeError):
            gtja_api._write_dataset_cache(tmp_path, "processProfitData.query", [{"a": 1}])
    finally:
        gtja_api.os.replace = real_replace

    assert seen, "应当走到 os.replace"
    src, dst = seen[0]
    # 目标名保持稳定(读取端只认它)
    assert dst.endswith("processProfitData_query.json")
    # 临时名必须与目标名不同,且不是一个"固定的"后缀 —— 关键:包含随机片段
    assert os.path.dirname(src) == os.path.dirname(dst)
    stem = os.path.basename(src)
    assert stem != os.path.basename(dst)
    # 用两次调用比对:临时名必须不同(唯一性),固定名写法在这里必挂
    seen.clear()
    gtja_api.os.replace = fake_replace
    try:
        with contextlib.suppress(RuntimeError):
            gtja_api._write_dataset_cache(tmp_path, "processProfitData.query", [{"a": 1}])
    finally:
        gtja_api.os.replace = real_replace
    assert seen and seen[0][0] != src, "两次写入的临时名必须不同(否则并发必撞)"


def test_dataset_cache_write_succeeds_when_target_exists(tmp_path):
    """目标文件已存在时仍要写得进去(爬虫/web/前端三方并发刷新的常态)。"""
    ep = "processProfitData.query"
    gtja_api._write_dataset_cache(tmp_path, ep, [{"round": 1}])
    gtja_api._write_dataset_cache(tmp_path, ep, [{"round": 2}, {"round": 3}])

    rows = gtja_api._read_dataset_cache(tmp_path, ep, ttl=10**6)
    assert rows == [{"round": 2}, {"round": 3}], "后写必须完整覆盖先写"

    # 不许留下任何 .tmp 垃圾
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_dataset_cache_tmp_is_isolated_between_two_concurrent_writers(tmp_path):
    """模拟"两个写者交叉进行":A 写完 tmp 停顿、B 再写自己的 tmp —— 两者不得同名。

    这是并发互撞的最小可复现骨架(不起真进程也能卡住回归):
    先让 A 把 tmp 落盘(不 replace),再让 B 用同样的载荷写一遍,
    旧写法下 B 会写进**同一个** tmp,新写法下必须是两个不同文件。
    """
    ep = "processProfitData.query"
    names = []

    real_replace = gtja_api.os.replace

    def capture_replace(src, dst):
        names.append(os.path.basename(str(src)))
        # 真的执行 replace,让流程走完
        return real_replace(src, dst)

    gtja_api.os.replace = capture_replace
    try:
        gtja_api._write_dataset_cache(tmp_path, ep, [{"who": "A"}])
        gtja_api._write_dataset_cache(tmp_path, ep, [{"who": "B"}])
    finally:
        gtja_api.os.replace = real_replace

    assert len(names) == 2
    assert names[0] != names[1], f"两个写者用了同一个临时文件: {names}"
