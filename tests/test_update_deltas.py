"""「每次更新 各平台增量」统计的回归测试(2026-09-17)。

【用户要什么】在「数据更新」区块看到: 每次更新后, 各平台**分别新增了多少钱条**。

【为什么不能直接拿 collect_run 里的 notes】
    collect_run 的 notes 是"本次抓到多少条"(含重复)。实测 output 下 24441 行只对应
    8791 条唯一帖 —— **72% 的命中是重复**。同一批帖子每天 08:00/18:00 会被反复命中,
    只看命中数会让人以为"每天新增上千条", 实际净增常常只有几十条。
    所以本功能一律以"跨全量历史去重后的净增(new)"为准, 命中数(raw)并列展示做对照。

【本文件钉住的行为】
  1. 去重键的稳健性(note_id → url → 内容哈希; **绝不塌缩成空串**);
  2. 跨批次累积去重: 同一帖第二次出现必须计 0;
  3. 失败平台不能被吞掉: 采集失败时不产 batch 文件, 只有 collect_run 报告,
     若只扫文件会把"被平台拦截"误判成"这次没安排这个平台";
  4. "一次更新"的切分: 45 分钟间隔 + **跨 08:00/18:00 整点强制切开**
     (否则 09-15 那种连续调试会把 18:00 的定时采集混进手动轮, 用户分不清来源);
  5. 轮内同平台多次运行的状态归并(成功过一次就算 ok);
  6. 轮次留痕只改"来源标签", **不得改动任何增量数字**;
  7. 缓存: 文件指纹不变才命中, 新增批次必须重算;
  8. 自洽性: 各轮新增之和 == 各平台存量之和(无跨平台重复时)。
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import update_tracker as ut
import web_app


# ══════════════════════════════════════════════════════════════════
# 夹具: 在临时目录里造一套"批次文件 + 运行报告"
# ══════════════════════════════════════════════════════════════════

def _rec(note_id, platform="weibo", title="库存去化跟踪", url=""):
    """造一条最小批次记录(字段对齐采集端产物)。"""
    d = {
        "note_id": note_id,
        "platform": platform,
        "title": title,
        "author_name": "测试作者",
        "publish_time": "2026-09-16 10:00:00",
    }
    if url:
        d["url"] = url
    return d


def _write_batch(out: Path, platform, ts, records):
    """写一个批次文件。ts 形如 '20260917_091859';records=[] 会写出 **0 字节文件**。"""
    p = out / f"batch_{platform}_{ts}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def _write_report(out: Path, platform, ts, status="ok", notes=0, started_at=None, diagnosis=""):
    """写一个 collect_run 运行报告。started_at 缺省由 ts 推导。"""
    if started_at is None:
        dt = datetime.strptime(ts, "%Y%m%d_%H%M%S")
        started_at = dt.strftime("%Y-%m-%d %H:%M:%S")
    p = out / f"collect_run_{platform}_{ts}.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"platform": platform, "status": status, "notes": notes,
                   "diagnosis": diagnosis, "started_at": started_at,
                   "ended_at": started_at}, f, ensure_ascii=False)
    return p


@pytest.fixture()
def out(tmp_path):
    d = tmp_path / "output"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ══════════════════════════════════════════════════════════════════
# 1. 去重键
# ══════════════════════════════════════════════════════════════════

def test_post_key_prefers_note_id():
    assert ut.post_key({"note_id": "zh:1", "url": "https://x"}) == "zh:1"


def test_post_key_falls_back_to_url():
    assert ut.post_key({"note_id": "", "url": "https://x/1"}).startswith("u:")


def test_post_key_never_collapses_distinct_records():
    """核心:note_id 与 url 都缺失时, 两条**不同**记录必须得到**不同**的键。

    旧 web_app 的 platform_summary 用 `d.get("note_id","")` 做去重键 —— 字段缺失时
    所有记录都会变成同一个空串, 去重后总量直接塌缩成 1。这条测试就是这个坑的守卫。
    """
    a = ut.post_key({"platform": "weibo", "title": "螺纹钢库存", "author_name": "甲"})
    b = ut.post_key({"platform": "weibo", "title": "PTA 检修", "author_name": "乙"})
    assert a and b and a != b, "缺 id 的记录被塌缩成了同一个键 —— 去重后总量会失真"


def test_post_key_is_stable_across_calls():
    r = {"platform": "xhs", "title": "同一篇", "author_name": "同一个人"}
    assert ut.post_key(r) == ut.post_key(dict(r))


# ══════════════════════════════════════════════════════════════════
# 2. 文件名解析
# ══════════════════════════════════════════════════════════════════

def test_parse_batch_name_normal():
    plat, dt = ut.parse_batch_name("batch_weibo_20260917_091648.jsonl")
    assert plat == "weibo"
    assert dt == datetime(2026, 9, 17, 9, 16, 48)


def test_parse_batch_name_legacy_without_platform():
    """2026-07 早期文件没有平台名, 只能回退到行内 platform 字段。"""
    plat, dt = ut.parse_batch_name("batch_20260715_160642.jsonl")
    assert plat is None
    assert dt == datetime(2026, 7, 15, 16, 6, 42)


def test_parse_batch_name_rejects_junk():
    assert ut.parse_batch_name("notes.jsonl") == (None, None)
    assert ut.parse_batch_name("collect_run_weibo_20260917_091648.json") == (None, None)


# ══════════════════════════════════════════════════════════════════
# 3. 跨批次累积去重 —— 这是"真实新增"的地基
# ══════════════════════════════════════════════════════════════════

def test_runs_dedupe_across_batches(out):
    """同一帖子在第二批再次出现时, 新增必须计 0(它已经在上一次被计入过)。"""
    _write_batch(out, "weibo", "20260916_080000", [_rec("a"), _rec("b")])
    _write_batch(out, "weibo", "20260916_180000", [_rec("b"), _rec("c")])

    runs = ut.build_runs(out)

    assert [r["new"] for r in runs] == [2, 1], "第二批应只算 c 这一条新增"
    assert [r["raw"] for r in runs] == [2, 2], "命中数(raw)必须照实报 2 条"


def test_runs_dedupe_within_a_single_batch(out):
    """同一个文件内部的重复条目也只能算一次。"""
    _write_batch(out, "zhihu", "20260917_091859", [_rec("x"), _rec("x"), _rec("y")])

    runs = ut.build_runs(out)

    assert runs[0]["raw"] == 3, "行数是 3(采集确实抓了 3 条)"
    assert runs[0]["new"] == 2, "唯一键只有 2 个"


def test_runs_are_ordered_by_time(out):
    _write_batch(out, "weibo", "20260916_180000", [_rec("late")])
    _write_batch(out, "weibo", "20260916_080000", [_rec("early")])

    runs = ut.build_runs(out)

    assert [r["raw"] for r in runs] == [1, 1]
    assert runs[0]["ts"].endswith("08:00:00") and runs[1]["ts"].endswith("18:00:00"), \
        "必须按时间重放 —— 顺序错了增量会算到错误的轮次上"


def test_empty_batch_file_is_a_valid_run(out):
    """0 字节批次文件 = 该次采集一条都没采到, 它是**有效信号**不是垃圾。"""
    _write_batch(out, "xhs", "20260917_133418", [])
    _write_report(out, "xhs", "20260917_133418", status="blocked", notes=0)

    runs = ut.build_runs(out)

    assert len(runs) == 1
    assert runs[0]["raw"] == 0 and runs[0]["new"] == 0
    assert runs[0]["platform"] == "xhs", "空文件没有行内 platform, 必须回退用文件名平台"
    assert runs[0]["status"] == "blocked"


# ══════════════════════════════════════════════════════════════════
# 4. 失败平台不能被吞掉
# ══════════════════════════════════════════════════════════════════

def test_failed_platform_without_batch_file_still_appears(out):
    """核心:采集失败时不产 batch 文件, 只看文件会把"被拦截"误判成"没安排这个平台"。

    两者对用户的行动含义完全相反 —— 前者要重扫登录/等冷却, 后者啥也不用做。
    """
    _write_batch(out, "weibo", "20260916_180000", [_rec("a")])
    _write_report(out, "weibo", "20260916_180000", status="ok", notes=1)
    _write_report(out, "douyin", "20260916_182239", status="blocked", notes=0,
                  diagnosis="软限流: HTTP 200 + data 为空")

    runs = ut.build_runs(out)
    by_plat = {r["platform"]: r for r in runs}

    assert "douyin" in by_plat, "只有报告没有批次的失败平台被吞掉了"
    assert by_plat["douyin"]["status"] == "blocked"
    assert by_plat["douyin"]["new"] == 0
    assert "软限流" in by_plat["douyin"]["diagnosis"]


def test_report_is_matched_to_batch_despite_timestamp_offset(out):
    """批次文件名(启动时刻)与报告 started_at 通常差几秒 —— 必须能配上。"""
    _write_batch(out, "zhihu", "20260917_091859", [_rec("z")])
    _write_report(out, "zhihu", "20260917_091859",
                  started_at="2026-09-17 09:18:56", status="ok", notes=1)

    runs = ut.build_runs(out)

    assert runs[0]["status"] == "ok", "差 3 秒就配不上, 状态会整片丢失"


def test_batch_without_report_keeps_status_empty(out):
    """2026-09-14 之前的批次没有报告 → 状态留空(如实表达"未留痕"), 不伪造 ok。"""
    _write_batch(out, "weibo", "20260717_092522", [_rec("old")])

    runs = ut.build_runs(out)

    assert runs[0]["status"] == ""


# ══════════════════════════════════════════════════════════════════
# 5. 轮次切分
# ══════════════════════════════════════════════════════════════════

def _run(ts, platform="weibo", new=0, raw=0, status=""):
    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    return {"ts": ts, "dt": dt, "platform": platform, "platform_label": platform,
            "raw": raw, "new": new, "status": status, "diagnosis": "", "file": ""}


def test_rounds_split_on_large_gap():
    runs = [_run("2026-09-16 08:00:00"), _run("2026-09-16 08:10:00"),
            _run("2026-09-16 11:00:00")]  # 与上一次差 2h50m

    rounds = ut.cluster_rounds(runs)

    assert len(rounds) == 2
    assert rounds[0]["start"].endswith("08:00") and rounds[1]["start"].endswith("11:00")


def test_rounds_split_at_scheduled_boundary():
    """核心:两次运行只差 15 分钟(<45 分钟阈值), 但中间跨过了 18:00 整点。

    调度 cron 就在 08:00 / 18:00 触发。若不按整点切开, 09-15 那种"下午连续调试"
    (14:11~17:26 每隔几分钟重试一次, 没有任何 45 分钟以上的空档)会把 18:00 的
    **定时采集**并进手动轮 —— 用户将无法判断"这次是定时跑的还是我点的"。
    """
    runs = [_run("2026-09-15 17:50:00"), _run("2026-09-15 18:05:00")]

    rounds = ut.cluster_rounds(runs)

    assert len(rounds) == 2, "跨 18:00 整点却没有切开, 手动轮会吞掉定时轮"
    assert rounds[0]["source"] == "manual"
    assert rounds[1]["source"] == "scheduled", "18:00 那轮必须标成定时"


def test_rounds_split_at_morning_boundary():
    runs = [_run("2026-09-16 07:56:00"), _run("2026-09-16 08:05:00")]

    rounds = ut.cluster_rounds(runs)

    assert len(rounds) == 2
    assert rounds[1]["source"] == "scheduled"


def test_rounds_keep_a_normal_schedule_round_together():
    """一轮调度里 6 个平台顺序跑、最长间隔 17 分钟 → 必须留在同一轮。"""
    runs = [_run("2026-09-16 08:00:00", "weibo"), _run("2026-09-16 08:17:30", "zhihu"),
            _run("2026-09-16 08:19:00", "eastmoney_guba"), _run("2026-09-16 08:24:23", "xhs")]

    rounds = ut.cluster_rounds(runs)

    assert len(rounds) == 1, "正常的调度轮被切碎了"
    assert len(rounds[0]["platforms"]) == 4
    assert rounds[0]["source"] == "scheduled"


def test_round_source_manual_off_schedule():
    rounds = ut.cluster_rounds([_run("2026-09-17 13:31:00")])
    assert rounds[0]["source"] == "manual"


# ══════════════════════════════════════════════════════════════════
# 6. 轮内状态归并
# ══════════════════════════════════════════════════════════════════

def test_round_status_ok_if_succeeded_once():
    """xhs 在同轮里 auth_failed → ok:该平台整体算成功。

    否则会出现"平台标着失败、新增却有 254 条"的自相矛盾。
    """
    runs = [_run("2026-09-17 08:52:44", "xhs", status="auth_failed"),
            _run("2026-09-17 09:01:41", "xhs", status="ok", new=87, raw=87)]

    rounds = ut.cluster_rounds(runs)
    slot = rounds[0]["platforms"]["xhs"]

    assert slot["status"] == "ok"
    assert slot["new"] == 87
    assert slot["runs"] == 2, "该平台在本轮跑了 2 次, 次数要留痕"
    assert rounds[0]["failed"] == {}, "已经成功过的平台不该再进失败清单"


def test_round_status_takes_last_when_all_failed():
    """同轮内全程失败时取**最后一次**状态(它最能代表"这个平台现在怎么了")。

    ⚠️ 两次运行的间隔必须留在同一轮内(<45 分钟且不跨调度整点), 否则被切开会得到两轮,
       测的就不是"归并"而是"切分"了。
    """
    runs = [_run("2026-09-15 14:13:03", "zhihu", status="blocked"),
            _run("2026-09-15 14:20:00", "zhihu", status="auth_failed")]

    rounds = ut.cluster_rounds(runs)

    assert len(rounds) == 1, "用例前提: 两次运行应落在同一轮"
    assert rounds[0]["platforms"]["zhihu"]["status"] == "auth_failed", \
        "全程失败时应取最后一次状态(最能代表'这个平台现在怎么了')"


def test_round_lists_failed_platforms():
    runs = [_run("2026-09-16 08:00:00", "weibo", status="ok", new=71, raw=104),
            _run("2026-09-16 08:24:23", "xhs", status="blocked"),
            _run("2026-09-16 08:24:31", "douyin", status="blocked")]

    rounds = ut.cluster_rounds(runs)

    assert rounds[0]["failed"] == {"xhs": "blocked", "douyin": "blocked"}
    assert rounds[0]["total_new"] == 71


def test_round_aggregates_same_platform_runs():
    runs = [_run("2026-09-17 09:01:41", "xhs", new=12, raw=20, status="ok"),
            _run("2026-09-17 09:20:34", "xhs", new=30, raw=50, status="ok")]

    rounds = ut.cluster_rounds(runs)
    slot = rounds[0]["platforms"]["xhs"]

    assert slot["new"] == 42 and slot["raw"] == 70
    assert rounds[0]["total_new"] == 42


# ══════════════════════════════════════════════════════════════════
# 7. 更新前后差分(一键更新走这条)
# ══════════════════════════════════════════════════════════════════

def test_scan_platform_ids_groups_by_platform(out):
    _write_batch(out, "weibo", "20260916_080000", [_rec("a"), _rec("b")])
    _write_batch(out, "zhihu", "20260916_080100", [_rec("c", platform="zhihu")])

    ids = ut.scan_platform_ids(out)

    assert {k: len(v) for k, v in ids.items()} == {"weibo": 2, "zhihu": 1}


def test_delta_between_counts_only_new():
    before = {"weibo": {"a", "b"}, "zhihu": {"z"}}
    after = {"weibo": {"a", "b", "c", "d"}, "zhihu": {"z"}}

    assert ut.delta_between(before, after) == {"weibo": 2, "zhihu": 0}


def test_delta_between_handles_new_platform():
    before = {"weibo": {"a"}}
    after = {"weibo": {"a"}, "douyin": {"x", "y"}}

    assert ut.delta_between(before, after)["douyin"] == 2


def test_delta_between_tolerates_missing_baseline():
    """基线建立失败时返回全 0(调用方据此显示"增量不可用", 而不是崩溃)。"""
    assert ut.delta_between(None, {"weibo": {"a"}}) == {"weibo": 1}
    assert ut.delta_between({}, {}) == {}


# ══════════════════════════════════════════════════════════════════
# 8. 轮次留痕只改标签
# ══════════════════════════════════════════════════════════════════

def test_recorded_round_roundtrips(out):
    rec = {"ts": "2026-09-17 13:35:00", "started_ts": "2026-09-17 13:31:00",
           "source": "manual", "platforms": ["weibo", "zhihu"], "delta": {"weibo": 63}}
    assert ut.append_recorded_round(out, rec) is True

    got = ut.read_recorded_rounds(out)

    assert len(got) == 1 and got[0]["source"] == "manual"
    assert got[0]["platforms"] == ["weibo", "zhihu"]


def test_recorded_round_survives_garbage_lines(out):
    """留痕文件被写坏一行不能让整个接口挂掉。"""
    with open(out / ut.HISTORY_FILENAME, "a", encoding="utf-8") as f:
        f.write("{坏掉的 json\n")
        f.write(json.dumps({"ts": "2026-09-17 13:35:00", "source": "manual"}) + "\n")

    got = ut.read_recorded_rounds(out)

    assert len(got) == 1, "坏行应被跳过而不是抛异常"


def test_apply_recorded_source_overrides_label_only(out):
    """核心:留痕用于纠正来源标签,**绝不能改动增量数字**。

    数字只认批次文件重放 —— 若留痕能改数字, 一次误写就会让历史增量永久错位。
    """
    _write_batch(out, "weibo", "20260917_080500", [_rec("a"), _rec("b")])
    runs = ut.build_runs(out)
    rounds = ut.cluster_rounds(runs)  # 08:05 会被时间规则判成 scheduled
    assert rounds[0]["source"] == "scheduled"

    ut.append_recorded_round(out, {
        "ts": "2026-09-17 08:20:00", "started_ts": "2026-09-17 08:05:00",
        "source": "manual", "platforms": ["weibo"], "delta": {"weibo": 999},
    })
    ut._apply_recorded_source(rounds, ut.read_recorded_rounds(out))

    assert rounds[0]["source"] == "manual", "留痕没能纠正来源标签"
    assert rounds[0]["total_new"] == 2, "留痕把增量数字改掉了 —— 这是不允许的"
    assert rounds[0]["requested"] == ["weibo"]


def test_apply_recorded_source_ignores_far_away_record(out):
    """留痕与轮次时间差太远时不匹配, 避免把别的轮次标签串过来。"""
    rounds = ut.cluster_rounds([_run("2026-09-17 13:31:00")])
    ut._apply_recorded_source(rounds, [{"ts": "2026-09-16 03:00:00", "source": "scheduled"}])

    assert rounds[0]["source"] == "manual"


# ══════════════════════════════════════════════════════════════════
# 9. build_deltas: 汇总 + 缓存 + 自洽
# ══════════════════════════════════════════════════════════════════

def test_build_deltas_is_self_consistent(out):
    """各轮新增之和 == 各平台存量之和(构造数据无跨平台重复)。"""
    _write_batch(out, "weibo", "20260916_080000", [_rec("a"), _rec("b")])
    _write_batch(out, "zhihu", "20260916_080100", [_rec("c", platform="zhihu"), _rec("d", platform="zhihu")])
    _write_batch(out, "weibo", "20260916_180000", [_rec("b"), _rec("e")])

    d = ut.build_deltas(out)

    assert sum(r["total_new"] for r in d["rounds"]) == sum(d["totals"].values())
    assert d["totals"] == {"weibo": 3, "zhihu": 2}


def test_build_deltas_rounds_newest_first(out):
    _write_batch(out, "weibo", "20260916_080000", [_rec("a")])
    _write_batch(out, "weibo", "20260916_180000", [_rec("b")])

    d = ut.build_deltas(out)

    assert d["rounds"][0]["start"] > d["rounds"][1]["start"], "最近一次更新必须排在最前"


def test_build_deltas_platform_rollup(out):
    _write_batch(out, "weibo", "20260916_080000", [_rec("a"), _rec("b")])
    _write_batch(out, "weibo", "20260916_180000", [_rec("b"), _rec("c")])

    d = ut.build_deltas(out)
    weibo = [p for p in d["platforms"] if p["key"] == "weibo"][0]

    assert weibo["total_new"] == 3
    assert weibo["total_raw"] == 4, "命中数要累积原始行数(2+2)"
    assert weibo["label"] == "微博"


def test_cache_hits_until_new_batch_arrives(out):
    """缓存必须按"文件指纹"失效: 没有新批次就不重算, 有新批次必须重算。"""
    _write_batch(out, "weibo", "20260916_080000", [_rec("a")])
    ut.build_deltas(out)
    assert (out / ut.CACHE_FILENAME).exists()

    sig1 = ut.build_deltas(out)["signature"]
    d2 = ut.build_deltas(out)
    assert d2["signature"] == sig1 and len(d2["rounds"]) == 1

    _write_batch(out, "weibo", "20260916_180000", [_rec("b")])
    d3 = ut.build_deltas(out)

    assert len(d3["rounds"]) == 2, "新增批次后仍返回旧缓存 —— 更新完页面看不到新数据"
    assert d3["signature"] != sig1


def test_cache_can_be_bypassed(out):
    _write_batch(out, "weibo", "20260916_080000", [_rec("a")])
    ut.build_deltas(out)

    fresh = ut.build_deltas(out, use_cache=False)

    assert len(fresh["rounds"]) == 1, "绕过缓存应重新计算且结果不变"


def test_build_deltas_on_empty_dir(out):
    d = ut.build_deltas(out)

    assert d["rounds"] == [] and d["runs"] == [] and d["totals"] == {}


# ══════════════════════════════════════════════════════════════════
# 10. 接口层
# ══════════════════════════════════════════════════════════════════

@pytest.fixture()
def client(out, monkeypatch):
    monkeypatch.setattr(web_app, "THINK2_OUTPUT", out)
    return web_app.app.test_client()


def test_api_update_deltas_shape(out, client):
    _write_batch(out, "weibo", "20260916_080000", [_rec("a"), _rec("b")])
    _write_report(out, "weibo", "20260916_080000", status="ok", notes=2)

    r = client.get("/api/update_deltas")

    assert r.status_code == 200
    d = r.get_json()
    for key in ("rounds", "runs", "platforms", "totals", "round_total", "generated_at"):
        assert key in d, f"响应缺少 {key} 字段"
    rd = d["rounds"][0]
    assert rd["platforms"]["weibo"]["new"] == 2
    assert rd["source"] in ("manual", "scheduled")
    assert d["totals"]["weibo"] == 2


def test_api_update_deltas_limit(out, client):
    for i, hour in enumerate(["080000", "120000", "180000"]):
        _write_batch(out, "weibo", f"2026091{6}_{hour}", [_rec(f"n{i}")])

    d = client.get("/api/update_deltas?limit=2").get_json()

    assert len(d["rounds"]) == 2
    assert d["round_total"] == 3, "被截断的轮次数要如实报出来, 否则用户以为历史只有 2 次"


def test_api_update_deltas_missing_dir(monkeypatch):
    monkeypatch.setattr(web_app, "THINK2_OUTPUT", Path("Z:/definitely/not/here-9f3a"))
    c = web_app.app.test_client()

    d = c.get("/api/update_deltas").get_json()

    assert d["rounds"] == [] and d.get("error"), "目录不存在时应给出可读错误而不是 500"


def test_api_update_deltas_reports_failed_platform(out, client):
    """接口层也要能看到失败平台(用户据此决定要不要重扫登录)。"""
    _write_report(out, "xhs", "20260917_085250", status="auth_failed", notes=0,
                  diagnosis="登录态已失效")

    d = client.get("/api/update_deltas").get_json()
    rd = d["rounds"][0]

    assert rd["failed"] == {"xhs": "auth_failed"}
    assert rd["platforms"]["xhs"]["status"] == "auth_failed"
