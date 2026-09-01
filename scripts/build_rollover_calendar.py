"""真实换月日历生成器:用新浪逐合约日线逐日判定主力合约,主力切换日=真实换月日。

为什么需要它:
    此前换月检测是启发式(收盘缺口超 8% 即判定换月)。它有两个固有盲区:
      1) 漏检:换月价差 <8% 的品种(螺纹钢 2.4%/铁矿 3.6%/豆系 3%)不被识别;
      2) 误判:把真实大涨(如涨停级 +8.9%)当换月,主动扭曲历史序列。
    本脚本换成"查日历":从新浪拉每个品种近 24 个月的全部具体合约日线,
    逐日取"成交量最大者"为主力(并列时取持仓量更大者),主力合约代码变化
    的那一天就是真实换月日——不靠猜,不设缺口阈值。

用法(在 AgentSense 根目录跑):
    venv/Scripts/python.exe scripts/build_rollover_calendar.py
    venv/Scripts/python.exe scripts/build_rollover_calendar.py --months=12   # 缩短时间跨度
    venv/Scripts/python.exe scripts/build_rollover_calendar.py --dry-run    # 只统计不写文件

输出:
    {思路2 validate}/output/trends/_rollover_calendar.json
    结构: { 品种名: {"main_per_day": {date: "JD2607"}, "rollover_dates": [切换日, ...]} }
    其中 rollover_dates 的每个元素: {"date": "2026-05-14", "from": "JD2606", "to": "JD2607"}。

数据源说明:
    新浪逐合约日线 futures_zh_daily_sina(symbol="JD2607") 返回 date/OHLCV/hold/settle。
    逐合约拉取约 7~8 分钟(52 品种 × 24 合约)。单品种拉取失败只跳过该合约,不中断整体。

关键实现细节(幽灵数据过滤):
    新浪对部分品种(郑商所 TA/MA/PF 等、上海国际能源 SC/INE)存在"合约代码复用":
    同一合约代码(如 SC2409)会在其真实存续期之外的年份也返回数据,但那些
    "幽灵段"成交量极小(<100 手,实测 max=64)。故主力判定时**忽略成交量 <100 手
    的合约**——既过滤幽灵数据,又不影响真实主力(真实主力成交量 >1000 手)。
    若某天所有合约成交量都 <100 手,跳过该天(不判定换月)。

注意事项:
    - 主连(如 JD0)历史可能超过 24 个月,但回测窗口是近 180 天,24 个月足够。
    - 无成交量的日子(节假日/停牌)不会出现在任何合约里,自动跳过。
    - 判定"主力"用成交量,并列用持仓量(与用户确认的方案)。
"""

import contextlib  # 【调用包】suppress:GBK 防崩(替换 try/except/pass)
import json  # 【调用包】JSON 读写(日历输出)
import sys  # 【调用包】命令行参数与路径注入
import time  # 【调用包】耗时统计
from datetime import datetime, timedelta  # 【调用包】日期计算(时间跨度)
from pathlib import Path  # 【调用包】路径对象化

MIN_ACTIVE_VOLUME = 100  # 【变量】真实活跃合约最低成交量(手);低于此视为幽灵数据,不参与主力判定
ROLL_WINDOW = 5  # 【变量】主力判定滚动窗口(交易日):用近 5 日累计成交量判定,平滑单日幽灵脉冲

# GBK 控制台防崩(Windows):打印中文/emoji 不抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(errors="replace")  # 【调用函数】改用替换字符编码,打印安全

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 【调用函数】AgentSense 根目录加入 import 路径
sys.path.insert(0, r"C:/Users/19168/Desktop/思路2/validate")  # 【调用函数】思路2 validate 目录(读 VARIETY_SYMBOLS)

from path_utils import (  # noqa: E402  # 【调用包】跨模块路径解析(定位 trends 目录)
    resolve_think2_dir,
)
from price_fetcher import (  # noqa: E402  # 【调用包】品种中文名 → 主连代码(52 品种)
    VARIETY_SYMBOLS,
)


def _contract_prefix(main_sym: str) -> str:
    """从主连代码推导合约前缀: 'JD0' -> 'JD', 'C0' -> 'C'。"""
    return main_sym[:-1]  # 【返回】去掉末尾的 '0'


def _enumerate_month_codes(prefix: str, months_back: int) -> list[str]:
    """枚举近 months_back 个月的全部合约代码,如 'JD' -> ['JD2601'...'JD2612']。

    【逻辑】从当前月往回推 months_back 个月,生成 YYMM 合约代码;
    取未来 3 个月(当前/下月/下下月主力常切换),其余为历史月份。
    """
    codes = []
    now = datetime.now()
    # 未来 3 个月(含当月)
    for offset in range(0, 4):
        d = now + timedelta(days=30 * offset)  # 【变量】近似月份推进(取当月/次月)
        codes.append(f"{prefix}{d.year % 100:02d}{d.month:02d}")  # 【调用函数】YYMM 格式合约代码
    # 历史 months_back 个月
    for offset in range(1, months_back + 1):
        d = now - timedelta(days=30 * offset)  # 【变量】近似月份回退
        codes.append(f"{prefix}{d.year % 100:02d}{d.month:02d}")  # 【调用函数】YYMM 格式合约代码
    # 去重保序
    return list(dict.fromkeys(codes))


def _fetch_contract(symbol: str) -> list[dict]:
    """拉取单个合约的日线,返回 [{date, volume, open_interest}] 列表;失败返回空列表。"""
    try:
        import akshare as ak  # 【调用包】延迟导入(akshare 较重)
        df = ak.futures_zh_daily_sina(symbol=symbol)  # 【调用函数】新浪逐合约日线
        rows = []
        for _, r in df.iterrows():
            date_str = str(r["date"])  # 【变量】日期(统一转字符串)
            if not date_str or date_str == "None":
                continue
            rows.append({
                "date": date_str,
                "volume": int(r["volume"]) if r["volume"] else 0,  # 【变量】成交量
                "hold": int(r["hold"]) if r["hold"] else 0,  # 【变量】持仓量
            })
        return rows
    except Exception:
        return []  # 【返回】单合约拉取失败:跳过,不中断


def _build_calendar_for_variety(
    variety: str,
    main_sym: str,
    months_back: int,
    cache: dict,
) -> dict:
    """为单个品种构建换月日历。

    【返回】{"main_per_day": {date: "JD2607"}, "rollover_dates": [{date, from, to}, ...]}
    """
    prefix = _contract_prefix(main_sym)  # 【变量】合约前缀(主连代码去掉 0)
    contract_codes = _enumerate_month_codes(prefix, months_back)  # 【变量】枚举的全部合约代码

    # 拉取全部合约日线, 按 (date, symbol) 记录成交量/持仓量
    vol_by_day = {}  # 【变量】date -> {symbol: volume}
    hold_by_day = {}  # 【变量】date -> {symbol: hold}
    for code in contract_codes:
        if code in cache:
            rows = cache[code]  # 【变量】命中缓存
        else:
            rows = _fetch_contract(code)  # 【调用函数】拉取合约日线
            cache[code] = rows  # 【变量】写入缓存
        for r in rows:
            d = r["date"]
            vol_by_day.setdefault(d, {})[code] = r["volume"]
            hold_by_day.setdefault(d, {})[code] = r["hold"]

    # 逐日判定主力: 用"近 ROLL_WINDOW 日累计成交量 + 活跃天数"双条件, 排除幽灵脉冲
    #   幽灵脉冲: 合约某天成交量暴增(如 BU2503 在 2023-04-26 突然 462 手, 前后日 <10),
    #             但"成交量达标的天数"很少。真实主力几乎每个交易日都达标。
    sorted_days = sorted(vol_by_day.keys())  # 【变量】全部交易日(升序)
    window_vol = {}  # 【变量】date -> {symbol: 近 ROLL_WINDOW 日累计成交量}
    window_days = {}  # 【变量】date -> {symbol: 近 ROLL_WINDOW 日成交量达标天数}
    for i, d in enumerate(sorted_days):
        window = sorted_days[max(0, i - ROLL_WINDOW + 1): i + 1]  # 【变量】滚动窗口日期(含当日, 最多 ROLL_WINDOW 天)
        wv, wd_count = {}, {}  # 【变量】窗口内累计成交量 / 活跃天数
        for wd in window:
            for sym, v in vol_by_day.get(wd, {}).items():
                if v >= MIN_ACTIVE_VOLUME:  # 【条件】单日成交量低于门槛的忽略(过滤幽灵)
                    wv[sym] = wv.get(sym, 0) + v  # 【变量】累计窗口成交量
                    wd_count[sym] = wd_count.get(sym, 0) + 1  # 【变量】活跃天数 +1
        window_vol[d] = wv
        window_days[d] = wd_count

    main_per_day = {}  # 【变量】date -> 主力合约代码
    min_days = max(1, int(ROLL_WINDOW * 0.6))  # 【变量】活跃天数门槛(5日窗口至少3天)
    for d in sorted_days:
        vols = vol_by_day.get(d, {})  # 【变量】当日各合约成交量
        if not vols:
            continue  # 当日无成交: 跳过, 不判定换月
        # 候选合约 = "窗口内活跃天数达标"者(至少 MIN_ACTIVE_DAYS 天有真实成交),
        # 过滤掉只有 1-2 天幽灵脉冲、其余天量能为 0 的伪活跃合约。
        active = {s: v for s, v in vols.items() if window_days.get(d, {}).get(s, 0) >= min_days}  # 【变量】持续活跃合约
        if not active:
            continue  # 当日无持续活跃合约: 跳过
        # 主力 = 当日成交量最大者(不用窗口累计!否则会把换月日平滑延迟 1~2 天,
        # 如鸡蛋 5-14 当日 JD2607 量已反超 JD2606, 但窗口累计仍判 JD2606)。
        # 窗口累计只用于上面的活跃天数过滤(滤幽灵), 不用于排序。
        top_vol = max(active.values())  # 【变量】当日最大成交量
        top_symbols = [s for s, v in active.items() if v == top_vol]  # 【变量】成交量并列的合约
        if len(top_symbols) == 1:
            main_per_day[d] = top_symbols[0]  # 【变量】当日量唯一最大者即主力
        else:
            # 并列: 取持仓量更大者
            best = top_symbols[0]  # 【变量】初始取第一个
            for s in top_symbols[1:]:
                if hold_by_day.get(d, {}).get(s, 0) > hold_by_day.get(d, {}).get(best, 0):
                    best = s  # 【变量】持仓量更大者胜出
            main_per_day[d] = best

    # 主力切换日 = 主力代码变化的日期
    raw_rollover = []  # 【变量】原始换月日清单(可能含幽灵往返)
    sorted_days = sorted(main_per_day.keys())  # 【变量】日期升序
    prev_main = None
    for d in sorted_days:
        cur = main_per_day[d]  # 【变量】当日主力
        if prev_main and cur != prev_main:
            raw_rollover.append({"date": d, "from": prev_main, "to": cur})  # 【变量】换月记录
        prev_main = cur

    # 清理幽灵往返: 真实换月是"新主力持续", 不会 1-2 天内切回旧主力。
    # 若某次换月的"from"在 3 个自然日内又成为换月目标(往返切换), 判定为幽灵, 删除。
    rollover_dates = []  # 【变量】清理后的换月日清单
    for i, r in enumerate(raw_rollover):
        d = r["date"]  # 【变量】本次换月日期
        from_sym = r["from"]  # 【变量】旧主力
        # 在 3 天内的后续换月中, 若又切回到 from_sym, 则是幽灵往返, 剔除本次
        is_ghost = False  # 【变量】是否幽灵往返标记
        for j in range(i + 1, min(i + 5, len(raw_rollover))):  # 【循环】向后找最多 4 次换月(约3天)
            nxt = raw_rollover[j]  # 【变量】后续换月
            from datetime import datetime  # 【调用包】日期解析(跨天比较)

            d1 = datetime.strptime(d, "%Y-%m-%d")
            d2 = datetime.strptime(nxt["date"], "%Y-%m-%d")
            if (d2 - d1).days > 3:
                break  # 【退出】超过 3 天, 不再属于往返
            if nxt["to"] == from_sym:
                is_ghost = True  # 【变量】3天内又切回旧主力 → 幽灵往返
                break
        if not is_ghost:
            rollover_dates.append(r)  # 【变量】保留非幽灵换月

    return {"main_per_day": main_per_day, "rollover_dates": rollover_dates}


def main() -> None:
    dry_run = "--dry-run" in sys.argv  # 【变量】预览模式(不写文件)
    months_back = 24  # 【变量】默认时间跨度(近 24 个月)
    for arg in sys.argv:
        if arg.startswith("--months="):
            months_back = int(arg.split("=", 1)[1])  # 【变量】用户指定的时间跨度

    trends = resolve_think2_dir() / "output" / "trends"  # 【变量】日历输出目录(与价格文件同目录)
    trends.mkdir(parents=True, exist_ok=True)

    # 只处理有价格文件的品种(40 个), 保证日历与价格一一对应
    price_files = [f.stem.replace("_price", "") for f in trends.glob("*_price.json")]  # 【变量】有价格文件的品种
    varieties = [(n, VARIETY_SYMBOLS[n]) for n in price_files if n in VARIETY_SYMBOLS]  # 【变量】品种名→主连代码

    print(f"months_back={months_back}  dry_run={dry_run}  varieties={len(varieties)}")
    t0 = time.time()  # 【变量】开始时间
    cache: dict = {}  # 【变量】合约日线缓存(跨品种复用同合约)
    calendar = {}  # 【变量】最终日历
    total_rollovers = 0  # 【变量】全部品种换月次数统计

    for i, (variety, main_sym) in enumerate(varieties):
        res = _build_calendar_for_variety(variety, main_sym, months_back, cache)  # 【调用函数】构建单品种日历
        ro = res["rollover_dates"]  # 【变量】该品种换月日
        total_rollovers += len(ro)  # 【变量】累加换月次数
        calendar[variety] = {
            "main_per_day": res["main_per_day"],  # 【变量】每日主力映射
            "rollover_dates": ro,  # 【变量】换月日清单
            "main_contract": main_sym,  # 【变量】主连代码(溯源)
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),  # 【变量】生成时间戳
        }
        print(f"  [{i + 1}/{len(varieties)}] {variety}({main_sym}): {len(ro)} 换月, "
              f"覆盖 {len(res['main_per_day'])} 个交易日")

    # 写文件
    if not dry_run:
        out_path = trends / "_rollover_calendar.json"  # 【变量】输出路径
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(calendar, f, ensure_ascii=False, indent=2)  # 【调用函数】写日历文件
        print(f"\nSaved to {out_path}")
    else:
        print("\n[dry-run] 未写文件")

    print(f"共 {total_rollovers} 个换月日, 耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
