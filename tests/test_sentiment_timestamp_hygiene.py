"""情绪采集时间戳卫生的回归测试 (2026-09-17)。

被锁定的真实缺陷
----------------
xhs 采集靠 note_id 前 8 位 hex 反解发布时间(卡片 DOM 没有时间字段)。但小红书
网页版列表卡片的 `id` 属性并不总是 Mongo ObjectId,实测混入两种形态:

  ① `<uuid>#<13位毫秒>`   f894297b-7000-4433-87fe-c90c29a59871#1789461982382
  ② 裸 uuid              f894297b-7000-4433-87fe-c90c29a59871

旧实现只检查 `len(note_id) >= 8` 且"前 8 位能当 hex 解析" —— uuid 前缀
"f894297b" 完美满足,于是被当成 Unix 秒,**静默**算出 2102 年。952 条真实
xhs 记录里 33 条中招(年份散布 2013~2104)。

危害不是"少几条数据",而是**未来日期**:一旦建桶,该条会永远落在下游
"最近 N 天"窗口内并参与时效加权 —— 比直接丢弃更糟。

关于 `#` 后的毫秒为什么也不能用
------------------------------
实测 33 条该形态记录的毫秒值**全部等于其所属批次文件的时刻 +1~52 秒**,
每批一条、严格单调递增(随卡片处理顺序增长) —— 它是**卡片渲染/抓取时刻**,
不是发布时间。拿它当发布时间 = 把整批记录错误地归到采集当天,属于"看似合法
其实错误"的值,比留空更危险,故一律拒绝。

防线分工(两层缺一不可)
----------------------
  - 上游解码器**格式白名单**(xhs_scraper.decode_objectid_timestamp,
    只认 24 位纯 hex):唯一能拦住"落在合法区间内的垃圾时间戳"的一层 ——
    实测 4 条被算成 2020/2022/2024,区间闸全部放行;
  - data_cleaner **区间闸**:拦未来/过老,但拦不住区间内的错误值。

测试手法: importlib 加载真实文件 + 注入假 playwright,不需要浏览器/网络。
"""

import importlib.util
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_VALIDATE_DIR = Path(__file__).resolve().parents[1] / "data_collection" / "validate"

# 真实数据里抓到的完整样例(952 条中 33 条异常之一)
_REAL_CARD_ID = "f894297b-7000-4433-87fe-c90c29a59871#1789461982382"
_REAL_CARD_UUID = "f894297b-7000-4433-87fe-c90c29a59871"
_REAL_OBJECTID = "6a55fac30000000006036102"  # 正常 ObjectId,前 8 位 0x6a55fac3


def _load(name: str, filename: str):
    """从 data_collection/validate 加载单个模块(不污染全局 sys.path)。"""
    spec = importlib.util.spec_from_file_location(name, _VALIDATE_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _install_fake_playwright() -> None:
    """xhs_scraper 顶层 `from playwright.sync_api import ...`;注入假模块,
    使本测试在任何机器上都能跑(无需装 playwright / 浏览器)。"""
    if "playwright.sync_api" in sys.modules:
        return
    pkg = types.ModuleType("playwright")
    api = types.ModuleType("playwright.sync_api")
    api.Browser = object
    api.Page = object
    api.TimeoutError = type("TimeoutError", (Exception,), {})
    api.sync_playwright = lambda *a, **k: None
    pkg.sync_api = api
    sys.modules["playwright"] = pkg
    sys.modules["playwright.sync_api"] = api


@pytest.fixture(scope="module")
def xhs():
    _install_fake_playwright()
    return _load("xhs_scraper_under_test", "xhs_scraper.py")


@pytest.fixture(scope="module")
def cleaner():
    return _load("data_cleaner_under_test", "data_cleaner.py")


def _objectid_for(year: int, month: int = 1, day: int = 1) -> str:
    """造一个前 8 位编码指定时间的**格式合法** ObjectId(24 位纯 hex)。

    用它区分"格式闸"和"区间闸":格式没问题、只是时间离谱。
    """
    ts = int(datetime(year, month, day, 12, 0, 0).timestamp())
    return format(ts, "08x") + "0" * 16


# ── A. 解码器:格式白名单 ────────────────────────────────────────────────


class TestDecoderFormatWhitelist:
    def test_standard_objectid_decodes_real_publish_date(self, xhs):
        """正常 ObjectId 仍要能解码(别把功能改没了)。"""
        out = xhs.decode_objectid_timestamp(_REAL_OBJECTID)
        assert out is not None
        assert out[:10] == "2026-07-14"

    def test_real_card_id_is_rejected(self, xhs):
        """真实复合 id:# 后的毫秒是渲染时刻,uuid 也不是 ObjectId → 拒绝。"""
        assert xhs.decode_objectid_timestamp(_REAL_CARD_ID) is None

    def test_bare_uuid_is_rejected(self, xhs):
        """裸 uuid:旧实现把前缀 f894297b 当 Unix 秒 → 静默产出 2102 年。
        这条是本缺陷的最小复现。"""
        assert xhs.decode_objectid_timestamp(_REAL_CARD_UUID) is None

    @pytest.mark.parametrize(
        "ms",
        [
            1789461982382,  # 实测值
            4102444800000,  # 2100-01-01,若被当发布时间就是未来
            1700000000000,
        ],
    )
    def test_card_id_suffix_never_becomes_publish_date(self, xhs, ms):
        """无论 # 后的毫秒是多少,都不得被当作发布时间。"""
        assert xhs.decode_objectid_timestamp(f"550e8400-e29b-41d4-a716-446655440000#{ms}") is None

    def test_uuid_prefix_encoding_in_range_date_is_rejected(self, xhs):
        """★ 最隐蔽的一类,也是格式白名单**不可省**的证明。

        构造一个 uuid,其前 8 位恰好编码出"区间内的合法过去日期"(2022-01-01)
        —— 区间闸会放行它(2022 落在 [2015-01-01, 今天] 内),只有"24 位纯 hex"
        的格式白名单能拦。实测数据里穿透区间闸的正是这 4 条(2020/2022/2024)。
        若有人删掉格式闸,本测试会红,而其它用例全绿。
        """
        ts = int(datetime(2022, 1, 1, 12, 0, 0).timestamp())
        uuid_like = f"{format(ts, '08x')}-0000-0000-0000-000000000000"
        assert len(uuid_like) == 36
        assert xhs.decode_objectid_timestamp(uuid_like) is None

    @pytest.mark.parametrize(
        "note_id",
        [
            "",
            None,
            "abc",
            "1234567",  # 短于 8 位
            "zzzzzzzzzzzzzzzzzzzzzzzz",  # 24 位但非 hex
            "12345678901234567890123",  # 23 位
            "550e8400e29b41d4a716446655440000",  # 32 位无连字符 uuid
        ],
    )
    def test_malformed_ids_rejected(self, xhs, note_id):
        assert xhs.decode_objectid_timestamp(note_id) is None


# ── B. 解码器:区间闸(格式合法但时间离谱)────────────────────────────────


class TestDecoderRangeGuard:
    def test_too_old_objectid_rejected(self, xhs):
        """2014 年:早于 2015-01-01 下限 → 拒绝。"""
        assert xhs.decode_objectid_timestamp(_objectid_for(2014)) is None

    def test_future_objectid_rejected(self, xhs):
        """2099 年:未来日期 → 拒绝(这正是 uuid 前缀算出的那类值)。"""
        assert xhs.decode_objectid_timestamp(_objectid_for(2099)) is None

    def test_recent_objectid_still_accepted(self, xhs):
        """边界内必须放行,别把闸做太紧。"""
        out = xhs.decode_objectid_timestamp(_objectid_for(2026, 9, 1))
        assert out is not None and out.startswith("2026-09-01")


# ── C. 防线分工:区间闸拦不住"区间内的垃圾"────────────────────────────


class TestCleanerIntervalGuard:
    """可执行文档:为什么两层都要有。"""

    def _rec(self, publish_time: str) -> dict:
        return {
            "publish_time": publish_time,
            "title": "螺纹钢日报",  # 非空,免得被"空内容"规则拦掉而混淆断言
            "desc": "",
            "content": "",
            "sentiment_score": 0.5,
            "author_fans": 10,
            "like_count": 1,
            "comment_count": 0,
        }

    def test_future_date_dropped(self, cleaner):
        future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d 10:00:00")
        assert cleaner.clean_records([self._rec(future)], verbose=False) == []

    def test_ancient_date_dropped(self, cleaner):
        assert cleaner.clean_records([self._rec("2010-01-01 10:00:00")], verbose=False) == []

    def test_in_range_garbage_date_passes_interval_guard(self, cleaner):
        """区间内的垃圾日期(2022-03-05,由 uuid 前缀错算而来)会被区间闸**放行**
        —— 所以上游解码器的格式白名单不可省。若哪天有人删掉解码器那道闸,
        这条会一直绿、但真实数据会重新混入 2020/2022/2024 这类假日期。"""
        kept = cleaner.clean_records([self._rec("2022-03-05 10:00:00")], verbose=False)
        assert len(kept) == 1, "区间闸本就不该拦区间内的日期(它只负责区间外)"

    def test_upstream_decoder_catches_what_interval_guard_misses(self, xhs, cleaner):
        """把两层接起来:同一个 note_id —— 区间闸放行的假日期,解码器拒绝。"""
        assert xhs.decode_objectid_timestamp(_REAL_CARD_UUID) is None
