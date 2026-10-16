"""update_tracker.py — 采集「每次更新 各平台增量」的统计模块。

【它回答什么问题】
    "这次更新到底给我带来了多少新数据?" —— 按平台拆分, 按"更新轮次"归组,
    既看得见净增(new), 也看得见采集能力(raw)。

【两个口径(缺一不可)】
    · new(真实新增) —— 本次采到的帖子里, **此前任何一批都没出现过**的条数。
    · raw(采集命中) —— 本次实际抓到多少条(含与历史重复的旧帖)。

【为什么 new 必须跨全量历史去重】
    同一批帖子会在每天 08:00 / 18:00 的采集里反复命中: 实测 output 下 24441 行
    只对应 8791 条唯一帖 —— 也就是说 **72% 的命中是重复**。只看 raw 会让人以为
    "每天新增上千条", 实际净增常常只有几十条。这两个数必须同时摆出来, 否则
    用户无法判断"某天没涨"到底是采集坏了还是本来就没有新内容。

【数据来源(全部只读, 不在采集侧新增埋点)】
    1. output/batch_{平台}_{YYYYMMDD}_{HHMMSS}.jsonl
       每次平台采集的产物, 文件名自带平台与**启动时刻**; 行数 = 命中数,
       行内 note_id 用于跨批去重。
       ⚠️ 存在 0 字节文件 = 该次采集一条都没采到(登录失效/被拦/时间过滤后为空),
         它是**有效信号**而不是垃圾, 必须按"失败"呈现。
       ⚠️ 2026-07 的 3 个早期文件没有平台名(batch_{ts}.jsonl), 只能回退到行内 platform。
    2. output/collect_run_{平台}_{YYYYMMDD}_{HHMMSS}.json
       batch_collect.py 自 2026-09-14 起的运行报告(含 status / diagnosis / notes)。
       **只有它知道失败平台** —— 采集失败时压根不会产出 batch 文件, 只看文件名
       会把"被平台拦截"误判成"这次没安排这个平台", 两者对用户的行动含义完全相反。
    3. output/update_history.jsonl(本模块写入)
       一键更新 / 调度管道自己记的轮次留痕, 只用来把 source 精确标注成 手动/定时。
       纯靠时间推断会在"08:10 手动点了一次更新"时判错。
       它是**锦上添花**: 文件缺失(历史轮次)时自动退回时间规则, 绝不因此报错。

【"一次更新"是怎么界定的】
    把每个平台运行按启动时刻排序, 相邻两次间隔 > ROUND_GAP_MIN(45 分钟) 即切为
    新的一轮。实测依据: 轮内相邻运行最大间隔 17.5 分钟(6 平台顺序采集 + 单平台
    超时), 轮与轮之间最小间隔 216 分钟 —— 45 分钟落在两者之间, 留了足够余量。
    ⚠️ 密集手动测试(如 09-17 上午 08:52~09:54 连跑 18 次)会被合并成同一轮,
       这是该口径的已知边界; 这类轮次的 source 会标为 manual 并由前端注明时间跨度。
"""

import glob
import hashlib
import json
import os
import re
from datetime import datetime, timedelta

# ── 常量 ────────────────────────────────────────────────────────────────────

# 【常量】平台 → 显示名。前端列头与日志都用它,避免"eastmoney_guba"这类内部名外泄。
PLATFORM_LABELS = {
    "weibo": "微博",
    "zhihu": "知乎",
    "xhs": "小红书",
    "xueqiu": "雪球",
    "eastmoney_guba": "东财股吧",
    "douyin": "抖音",
}

# 【常量】相邻平台运行间隔超过它 → 判为新一轮更新(单位: 分钟)。取值依据见模块 docstring。
ROUND_GAP_MIN = 45

# 【常量】batch 文件与 collect_run 报告的时间戳匹配容差(秒)。
#   batch 文件名用"启动时刻", collect_run 的 started_at 也是启动时刻(实测差 3~8 秒),
#   但报告是收尾时才写的 —— 用 300 秒容差吸收收尾延迟与时钟抖动。
REPORT_MATCH_TOL_S = 300

# 【常量】判"定时采集"的时刻窗口(分钟): 调度 cron 是 08:00 / 18:00 两个整点。
#   实测调度轮第一个平台恰好落在整点(08:00:00 / 18:00:00), 故窗口取 ±10 分钟即可;
#   手动更新极少正好卡在这两个时刻的 10 分钟窗口内。
SCHEDULED_HOURS = {8: 10, 18: 10}  # 小时 → 允许的偏移分钟数

_BATCH_RE = re.compile(r"^batch_(.+)_(\d{8})_(\d{6})\.jsonl$")
_BATCH_LEGACY_RE = re.compile(r"^batch_(\d{8})_(\d{6})\.jsonl$")  # 2026-07 早期: 无平台名
_RUN_RE = re.compile(r"^collect_run_(.+)_(\d{8})_(\d{6})\.json$")

HISTORY_FILENAME = "update_history.jsonl"  # 【常量】轮次留痕文件(本模块写, 前端读)
CACHE_FILENAME = ".update_deltas_cache.json"  # 【常量】重建结果缓存(点开头, 不会被 batch_*.jsonl 扫到)


# ── 基础工具 ────────────────────────────────────────────────────────────────


def post_key(rec):
    """给出**跨批次稳定**的帖子身份键。

    【参数】rec: 一条批次记录(dict)。
    【返回】str: 去重键。
    【关键逻辑】优先 note_id;缺失时退回 url;再缺则用 (平台|标题|作者) 的 sha1。
            **绝不能退回空串** —— 早期 web_app 的 platform_summary 只用 note_id,
            字段缺失时所有记录会塌缩成同一个键, 去重后总量直接失真。
    """
    nid = rec.get("note_id")
    if nid:
        return str(nid)
    url = rec.get("url")
    if url:
        return "u:" + str(url)
    blob = "%s|%s|%s" % (rec.get("platform", ""), rec.get("title", ""), rec.get("author_name", ""))
    return "h:" + hashlib.sha1(blob.encode("utf-8", "ignore")).hexdigest()


def parse_batch_name(name):
    """从批次文件名解析 (平台, 启动时刻)。解析不出时返回 (None, None)。

    【参数】name: 文件名(不含目录)。
    【返回】(str|None, datetime|None)
    """
    m = _BATCH_RE.match(name)
    if m:
        try:
            dt = datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S")
        except ValueError:
            return m.group(1), None
        return m.group(1), dt
    m = _BATCH_LEGACY_RE.match(name)
    if m:
        try:
            return None, datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            return None, None
    return None, None


def _sched_source(dt):
    """按时刻判断轮次来源: 落在调度 cron 窗口内 → "scheduled", 否则 "manual"。

    【参数】dt: 轮次首个运行的启动时刻。
    【返回】"scheduled" | "manual"
    """
    if dt is None:
        return "manual"
    tol = SCHEDULED_HOURS.get(dt.hour)
    if tol is not None and dt.minute <= tol:
        return "scheduled"
    return "manual"


def _scan_file(path):
    """单次扫描一个批次文件, 同时拿到 去重键集合 / 行数 / 平台分布。

    【参数】path: 批次文件绝对路径。
    【返回】(set[str], int, dict[str,int]): (唯一键集合, 有效行数, 平台计数)
    【关键逻辑】只扫一遍: 24441 行/50MB 的全量重放要跑几秒, 扫两遍会直接翻倍。
    """
    ids = set()
    raw = 0
    plats = {}
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return ids, 0, plats
    with fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            raw += 1
            ids.add(post_key(rec))
            p = rec.get("platform") or "?"
            plats[p] = plats.get(p, 0) + 1
    return ids, raw, plats


def _dominant_platform(plats, fallback):
    """取平台分布里的众数;分布为空(0 字节文件)时回退到文件名平台。"""
    if plats:
        return max(plats.items(), key=lambda kv: kv[1])[0]
    return fallback or "?"


def list_batch_files(output_dir):
    """列出批次文件, 按 (时间戳, 文件名) 升序 —— 重放去重必须严格按时间顺序。"""
    files = glob.glob(os.path.join(str(output_dir), "batch_*.jsonl"))

    def sort_key(p):
        _, dt = parse_batch_name(os.path.basename(p))
        return (dt or datetime.min, os.path.basename(p))

    return sorted(files, key=sort_key)


def list_run_reports(output_dir):
    """列出 collect_run 运行报告文件(按时间升序)。"""
    files = glob.glob(os.path.join(str(output_dir), "collect_run_*.json"))
    return sorted(files, key=lambda p: os.path.basename(p))


# ── 运行报告索引 ────────────────────────────────────────────────────────────


def index_run_reports(output_dir):
    """读取所有 collect_run 报告, 附带解析后的启动时刻。

    【返回】list[dict]: 每项含 platform / status / notes / diagnosis / fix_hint /
           degraded / degradations / started_at / ended_at / _dt(datetime|None) / _file。
    """
    out = []
    for f in list_run_reports(output_dir):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        dt = None
        try:
            dt = datetime.strptime(str(d.get("started_at", "")), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            m = _RUN_RE.match(os.path.basename(f))
            if m:
                try:
                    dt = datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S")
                except ValueError:
                    dt = None
        out.append({
            "platform": d.get("platform") or "",
            "status": str(d.get("status") or ""),
            "notes": int(d.get("notes") or 0),
            "diagnosis": str(d.get("diagnosis") or ""),
            "fix_hint": str(d.get("fix_hint") or ""),
            # 【2026-09-18】降级证据: 采集成功但少采(如微博登录态失效→只能取第 1 页)。
            # 2026-09-18 之前的旧报告没有这两个键 → 按"未降级"处理, 不伪造。
            "degraded": bool(d.get("degraded")),
            "degradations": d.get("degradations") or [],
            "started_at": str(d.get("started_at") or ""),
            "ended_at": str(d.get("ended_at") or ""),
            "_dt": dt,
            "_file": os.path.basename(f),
        })
    return out


def _match_report(reports, platform, dt, used):
    """为一个批次文件找出对应的运行报告(同平台 + 启动时刻最接近)。

    【参数】reports: index_run_reports 的结果; platform: 批次文件名解析出的平台;
           dt: 批次文件启动时刻; used: 已被占用的报告下标集合(原地更新)。
    【返回】报告 dict 或 None。
    """
    if dt is None or not platform:
        return None
    best, best_gap = None, None
    for i, r in enumerate(reports):
        if i in used or r["platform"] != platform or r["_dt"] is None:
            continue
        gap = abs((r["_dt"] - dt).total_seconds())
        if gap <= REPORT_MATCH_TOL_S and (best_gap is None or gap < best_gap):
            best, best_gap = i, gap
    if best is None:
        return None
    used.add(best)
    return reports[best]


# ── 逐平台运行记录 ──────────────────────────────────────────────────────────


def build_runs(output_dir):
    """重放全部批次文件, 得到**每次平台运行**的原始/新增条数。

    【返回】list[dict], 每项:
           {ts, dt, platform, platform_label, raw, new, status, diagnosis, file}
    【关键逻辑】
           1. 按时间升序重放, 维护一个全局 seen 集合 —— 每条记录的 new 都是
              "相对此前所有批次"的净增, 同一轮内多平台互不影响(平台间 id 不重叠)。
           2. 匹配 collect_run 报告补 status; 匹配不到的(2026-09-14 之前的批次)
              status 留空, 前端会显示为"未留痕"而不是伪造一个 ok。
           3. **只有报告、没有批次的运行也要产出条目**(采集失败时不落 batch 文件),
              否则轮次里会整段缺掉失败平台 —— 而"哪个平台挂了"恰是用户最该看到的。
    """
    files = list_batch_files(output_dir)
    reports = index_run_reports(output_dir)
    used = set()
    seen = set()
    runs = []

    for path in files:
        name = os.path.basename(path)
        name_plat, dt = parse_batch_name(name)
        ids, raw, plats = _scan_file(path)
        new = len(ids - seen)
        seen |= ids
        rep = _match_report(reports, name_plat, dt, used)
        plat = _dominant_platform(plats, name_plat)
        # 行内平台优先, 但文件名平台更可信时(0 字节文件没有行内信息)用文件名
        if not plats and name_plat:
            plat = name_plat
        run_dt = dt or (rep["_dt"] if rep else None)
        runs.append({
            "ts": run_dt.strftime("%Y-%m-%d %H:%M:%S") if run_dt else "",
            "dt": run_dt,
            "platform": plat,
            "platform_label": PLATFORM_LABELS.get(plat, plat),
            "raw": raw,
            "new": new,
            "status": (rep or {}).get("status", ""),
            "diagnosis": (rep or {}).get("diagnosis", ""),
            "fix_hint": (rep or {}).get("fix_hint", ""),
            # 降级随 run 一起走: 否则"平台成功但少采"在下游无处可查
            "degraded": bool((rep or {}).get("degraded")),
            "degradations": (rep or {}).get("degradations") or [],
            "file": name,
        })

    # 有报告但没有批次文件的运行(采集失败/被拦/一条未采到) —— 补成 raw=0/new=0 的条目
    for i, r in enumerate(reports):
        if i in used:
            continue
        plat = r["platform"] or "?"
        runs.append({
            "ts": r["started_at"] or (r["_dt"].strftime("%Y-%m-%d %H:%M:%S") if r["_dt"] else ""),
            "dt": r["_dt"],
            "platform": plat,
            "platform_label": PLATFORM_LABELS.get(plat, plat),
            "raw": 0,
            "new": 0,
            "status": r["status"],
            "diagnosis": r["diagnosis"],
            "fix_hint": r["fix_hint"],
            "degraded": bool(r.get("degraded")),
            "degradations": r.get("degradations") or [],
            "file": "",
        })

    runs.sort(key=lambda r: (r["dt"] or datetime.min, r["platform"]))
    return runs


# ── 轮次聚类 ────────────────────────────────────────────────────────────────


def cluster_rounds(runs, gap_min=ROUND_GAP_MIN):
    """把逐平台运行按时间聚成"一次更新"。

    【参数】runs: build_runs 的结果; gap_min: 切分阈值(分钟)。
    【返回】list[dict]: 每轮 {start, end, start_dt, source, platforms, total_new, total_raw}

    【切分规则(两条, 满足任一即另起一轮)】
      ① 相邻平台运行的启动时刻间隔 > gap_min(45 分钟);
      ② **跨越 08:00 / 18:00 整点** —— 调度 cron 就在这两个整点触发。
    【为什么必须有规则②】实测 2026-09-15 下午用户连续调试平台(14:11~17:26 每隔几分钟
      重试一次 xhs/douyin), 期间**没有任何一段间隔超过 45 分钟**, 于是 18:00 的定时采集
      被并进了那条手动轮 —— 用户将无法区分"这次是定时跑的还是我点的"。调度时刻是
      **确定性**的, 直接拿它当切分点最可靠。
    """
    rounds = []
    cur = []
    for r in runs:
        if r["dt"] is None:
            continue
        if cur and _needs_split(cur[-1]["dt"], r["dt"], gap_min):
            rounds.append(_fold_round(cur))
            cur = []
        cur.append(r)
    if cur:
        rounds.append(_fold_round(cur))
    return rounds


def _needs_split(prev_dt, dt, gap_min):
    """判断两个相邻运行之间是否应另起一轮。"""
    if (dt - prev_dt).total_seconds() / 60.0 > gap_min:
        return True
    # 跨越调度整点(08:00 / 18:00) → 切分, 避免把手动轮与定时轮混成一条
    for hour in SCHEDULED_HOURS:
        boundary = prev_dt.replace(hour=hour, minute=0, second=0, microsecond=0)
        if boundary < prev_dt:
            boundary = boundary + timedelta(days=1)
        if prev_dt < boundary <= dt:
            return True
    return False


def _fold_round(rs):
    """把一轮内的多个运行折叠成一条轮次记录。

    【状态归并规则】同一平台在一轮里可能跑了好几次(失败重试很常见, 见 09-15 下午的
            xhs: auth_failed → blocked → error → ok)。
            · 只要**成功过一次**就算 ok —— 数据确实拿到了, 标成失败会与新增数自相矛盾;
            · 全程失败则取**最后一次**的状态(它最能代表"这个平台现在怎么了");
            · 空状态(2026-09-14 之前没有运行报告)不覆盖已拿到的状态。
    【降级归并规则】(2026-09-18)与 status **刻意不同** —— status 问"数据拿到没有",
            降级问"这个平台现在的能力是否被削"。
            · 因此取**最后一次运行**的降级(rs 已按时间升序, 顺序覆盖即得):
              早先降级、后来人工重登恢复 → 显示已恢复; 最后一次降级 → 必须亮出来。
            · 降级**不并入 failed**: 降级平台并没有失败, 混进去会与新增数自相矛盾。
    """
    per = {}
    for r in rs:
        p = r["platform"]
        slot = per.setdefault(p, {
            "new": 0, "raw": 0, "runs": 0, "status": "", "diagnosis": "",
            "degraded": False, "degradations": [],
        })
        slot["new"] += r["new"]
        slot["raw"] += r["raw"]
        st = r["status"]
        if slot["runs"] == 0:
            slot["status"] = st
            slot["diagnosis"] = r["diagnosis"]
        elif st and slot["status"] != "ok":
            slot["status"] = st
            slot["diagnosis"] = r["diagnosis"]
        # 降级的归并规则与 status **不同**: status 是"成功过一次就算 ok"(数据确实拿到了),
        # 而降级描述的是"现在这个平台的能力状态", 所以**最后一次运行说了算** ——
        # 若本轮早先降级、后来人工重登恢复, 最后一次没降级就应当显示已恢复;
        # 反之最后一次降级则必须亮出来。rs 已按时间升序, 顺序覆盖即得"最后为准"。
        slot["degraded"] = bool(r.get("degraded"))
        slot["degradations"] = list(r.get("degradations") or [])
        slot["runs"] += 1
    start_dt = min(r["dt"] for r in rs)
    end_dt = max(r["dt"] for r in rs)
    failed = {p: v["status"] for p, v in per.items() if v["status"] and v["status"] != "ok"}
    # 轮次级降级汇总: 前端摘要行用它一眼点出"哪个平台跑成了但少采了"。
    # ⚠️ 与 failed 分开表达: 降级平台**没有失败**, 混进 failed 会让"拿到了 90 条"
    #    与"平台失败"自相矛盾, 也会把真正的故障淹掉。
    degraded = {p: v["degradations"] for p, v in per.items() if v["degraded"]}
    return {
        "start": start_dt.strftime("%Y-%m-%d %H:%M"),
        "end": end_dt.strftime("%Y-%m-%d %H:%M"),
        "start_dt": start_dt,
        "source": _sched_source(start_dt),
        "platforms": per,
        "total_new": sum(v["new"] for v in per.values()),
        "total_raw": sum(v["raw"] for v in per.values()),
        "failed": failed,
        "degraded": degraded,
    }


# ── 轮次留痕(写入 / 读取) ────────────────────────────────────────────────────


def append_recorded_round(output_dir, rec):
    """把本轮更新追加到 update_history.jsonl。

    【参数】rec: {ts, source, platforms:[...], delta:{平台:新增}, raw:{平台:命中},
                  status:{平台:状态}} —— 缺字段容错。
    【返回】bool: 是否写入成功。
    【关键】只用于**标注来源**(手动/定时), 不参与增量计算 —— 增量永远从批次文件
           重放得出。因此写失败只是少一个标签, 绝不能让更新流水线因此中断。
    """
    try:
        path = os.path.join(str(output_dir), HISTORY_FILENAME)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read_recorded_rounds(output_dir):
    """读取 update_history.jsonl 里的轮次留痕(按时间升序)。"""
    path = os.path.join(str(output_dir), HISTORY_FILENAME)
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(d, dict) and d.get("ts"):
                    out.append(d)
    except OSError:
        return []
    out.sort(key=lambda d: d["ts"])
    return out


def _apply_recorded_source(rounds, recorded):
    """用留痕记录覆盖轮次的 source 与请求平台。

    【参数】rounds: cluster_rounds 的结果; recorded: read_recorded_rounds 的结果。
    【返回】None(原地修改)。
    【匹配规则】留痕里的 `started_ts`(流水线启动时刻, 新记录都有)优先, 容差 ±5 分钟;
           没有 started_ts 的旧记录退回用 `ts`(写入时刻, 通常是流水线收尾那一下),
           此时允许的时间跨度要放宽到 2 小时 —— 该轮次本身可能跑了很久。
    【关键】只改 source / 请求平台, **绝不改增量数字** —— 数字只认批次文件重放。
    """
    if not recorded:
        return
    for rd in rounds:
        for rec in recorded:
            raw = rec.get("started_ts") or rec.get("ts")
            try:
                rdt = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
            gap = (rdt - rd["start_dt"]).total_seconds()
            lo, hi = (-300, 300) if rec.get("started_ts") else (-300, 7200)
            if lo <= gap <= hi:
                rd["source"] = rec.get("source") or rd["source"]
                if rec.get("platforms"):
                    rd["requested"] = list(rec["platforms"])
                rd["recorded_ts"] = rec["ts"]
                break


# ── 对外主入口 ──────────────────────────────────────────────────────────────


def _signature(output_dir):
    """批次/报告文件的"内容指纹", 用于判断缓存是否仍然有效。"""
    parts = []
    for pat in ("batch_*.jsonl", "collect_run_*.json"):
        files = sorted(glob.glob(os.path.join(str(output_dir), pat)))
        total = 0
        mtime = 0.0
        for f in files:
            try:
                st = os.stat(f)
            except OSError:
                continue
            total += st.st_size
            mtime = max(mtime, st.st_mtime)
        parts.append("%s:%d:%d:%.3f" % (pat, len(files), total, mtime))
    return "|".join(parts)


def build_deltas(output_dir, use_cache=True):
    """组装「各平台增量」的完整数据集(带文件指纹缓存)。

    【参数】output_dir: 思路2 的 output 目录; use_cache: 是否允许读写重建缓存。
    【返回】dict:
       {"rounds": [...倒序, 最近在前...],
        "runs": [...倒序...],
        "platforms": [{"key","label","total_new","total_raw","updates"}...],
        "totals": {平台: 当前唯一帖累计},
        "generated_at": "YYYY-MM-DD HH:MM:SS",
        "signature": "..."}
    【关键逻辑】全量重放要读 50MB/2.4 万行(约 2~4 秒), 前端每次进情绪页都会拉这个
           接口 —— 必须缓存, 否则页面切换会明显卡顿。指纹变了(有新批次)才重算。
    """
    sig = _signature(output_dir)
    cache_path = os.path.join(str(output_dir), CACHE_FILENAME)
    if use_cache:
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if cached.get("signature") == sig and cached.get("data"):
                return cached["data"]
        except Exception:  # noqa: BLE001
            pass

    runs = build_runs(output_dir)
    rounds = cluster_rounds(runs)
    _apply_recorded_source(rounds, read_recorded_rounds(output_dir))

    # 各平台"当前累计唯一帖数": 逐平台维护 id 集合(同一帖只应归一个平台,
    #   但早期无平台名的文件靠行内 platform 兜底, 故用 set 合并而非相加)。
    per_ids = {}
    for f in list_batch_files(output_dir):
        name_plat, _ = parse_batch_name(os.path.basename(f))
        ids, _, plats = _scan_file(f)
        plat = _dominant_platform(plats, name_plat)
        per_ids.setdefault(plat, set()).update(ids)
    totals = {p: len(s) for p, s in per_ids.items()}

    agg = {}
    for rd in rounds:
        for p, v in rd["platforms"].items():
            slot = agg.setdefault(p, {
                "total_new": 0, "total_raw": 0, "updates": 0,
                "degraded_updates": 0, "degraded": False,
            })
            slot["total_new"] += v["new"]
            slot["total_raw"] += v["raw"]
            if v["raw"] or v["new"] or v["status"]:
                slot["updates"] += 1
            # 降级轮次数(用于平台列头显示"该平台长期处于降级态")
            if v.get("degraded"):
                slot["degraded_updates"] += 1
                slot["degraded"] = True

    def _round_out(rd):
        return {
            "start": rd["start"],
            "end": rd["end"],
            "source": rd.get("source", ""),
            "platforms": rd["platforms"],
            "total_new": rd["total_new"],
            "total_raw": rd["total_raw"],
            "failed": rd["failed"],
            "degraded": rd.get("degraded", {}),
        }

    def _run_out(r):
        return {
            "ts": r["ts"], "platform": r["platform"], "platform_label": r["platform_label"],
            "raw": r["raw"], "new": r["new"], "status": r["status"],
            "diagnosis": r["diagnosis"], "file": r["file"],
            "degraded": bool(r.get("degraded")),
            "degradations": r.get("degradations") or [],
        }

    data = {
        "rounds": [_round_out(rd) for rd in reversed(rounds)],
        "runs": [_run_out(r) for r in reversed(runs)],
        "platforms": [
            {"key": p, "label": PLATFORM_LABELS.get(p, p),
             **agg.get(p, {"total_new": 0, "total_raw": 0, "updates": 0,
                           "degraded_updates": 0, "degraded": False})}
            for p in sorted(set(list(agg) + list(totals)), key=lambda x: -agg.get(x, {}).get("total_new", 0))
        ],
        "totals": totals,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "signature": sig,
    }

    if use_cache:
        try:
            tmp = cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"signature": sig, "data": data}, fh, ensure_ascii=False)
            os.replace(tmp, cache_path)
        except OSError:
            pass
    return data


def scan_platform_ids(output_dir_or_files):
    """扫描批次文件, 返回 {平台: 唯一帖键集合} —— 供"更新前后差分"使用。

    【参数】output_dir_or_files: 目录(Path/str)或文件路径列表。
    【返回】dict[str, set[str]]
    """
    if isinstance(output_dir_or_files, (str, os.PathLike)):
        files = glob.glob(os.path.join(str(output_dir_or_files), "batch_*.jsonl"))
    else:
        files = list(output_dir_or_files)
    out = {}
    for f in files:
        name_plat, _ = parse_batch_name(os.path.basename(f))
        ids, _, plats = _scan_file(f)
        plat = _dominant_platform(plats, name_plat)
        out.setdefault(plat, set()).update(ids)
    return out


def delta_between(before, after):
    """对比更新前后的平台快照, 给出各平台新增唯一帖数。

    【参数】before / after: scan_platform_ids 的结果。
    【返回】dict[str, int]: {平台: 新增数}(只含 after 里出现过的平台)。
    """
    out = {}
    for p, ids in (after or {}).items():
        old = (before or {}).get(p, set())
        out[p] = len(ids - old)
    return out
