"""一次性脚本:对现有全部 *_price.json 做后复权,消除主力连续序列的换月跳空。

用法(在 AgentSense 根目录跑):
    venv/Scripts/python.exe scripts/backward_adjust_prices.py            # 写回(默认阈值 5%)
    venv/Scripts/python.exe scripts/backward_adjust_prices.py --dry-run  # 只预览,不写文件
    venv/Scripts/python.exe scripts/backward_adjust_prices.py --threshold=6  # 自定义换月判定阈值(%)

输出:
    - 每品种:检测到的换月日期、调整前/后最大单日缺口。
    - 换月点清单请人工核对一遍(外盘联动品种的隔夜大跳空可能被误判为换月)。

后复权语义:最近一根 bar 因子=1(当前价不变),历史段按换月点比例缩放成"复权价",
收益率真实、绝对价位不代表当时成交价。
"""

import contextlib  # 【调用包】suppress:GBK 防崩(替换 try/except/pass)
import json  # 【调用包】JSON 读写(价格文件)
import sys  # 【调用包】命令行参数读取与路径注入
from pathlib import Path  # 【调用包】路径对象化

# GBK 控制台防崩(Windows):打印中文/emoji 不抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(errors="replace")  # 【调用函数】改用替换字符编码,打印安全

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 【调用函数】把 AgentSense 根目录加入 import 路径

from path_utils import (  # noqa: E402  # 【调用包】跨模块路径解析(定位 trends 目录)
    resolve_think2_dir,
)
from price_fetcher import (  # noqa: E402  # 【调用包】后复权算法与默认阈值
    ROLLOVER_GAP_THRESHOLD_PCT,
    _backward_adjust,
    _load_rollover_calendar,
)


def _max_gap_pct(prices: list[dict]) -> float:
    """计算序列最大单日收盘到收盘跳空(%)。"""
    m = 0.0
    for i in range(1, len(prices)):
        prev = float(prices[i - 1]["close"])
        if prev <= 0:
            continue
        m = max(m, abs((float(prices[i]["close"]) / prev - 1.0) * 100.0))
    return round(m, 2)


def main() -> None:
    dry_run = "--dry-run" in sys.argv  # 【变量】预览模式(不写文件)
    threshold = ROLLOVER_GAP_THRESHOLD_PCT  # 【变量】换月判定阈值(%)
    for arg in sys.argv:
        if arg.startswith("--threshold="):
            threshold = float(arg.split("=", 1)[1])  # 【变量】用户指定的阈值

    trends = resolve_think2_dir() / "output" / "trends"  # 【变量】价格文件目录
    files = sorted(trends.glob("*_price.json"))  # 【变量】全部价格文件
    print(f"threshold={threshold}%  dry_run={dry_run}  files={len(files)}")
    calendar = _load_rollover_calendar()  # 【调用函数】真实换月日历(优先用,替代阈值启发式)

    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:  # 【调用函数】读价格文件(上下文管理器自动关闭)
                data = json.load(fh)  # 【调用函数】反序列化 JSON
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [SKIP] {f.name}: {e}")  # 【变量】坏文件跳过
            continue

        prices = data.get("prices", [])
        if len(prices) < 10:
            print(f"  [SKIP] {f.name}: only {len(prices)} bars")  # 【变量】数据太少跳过
            continue

        # 品种中文名 = 文件名去 _price(如 螺纹钢_price.json → 螺纹钢)
        variety = f.stem.replace("_price", "")  # 【变量】品种名(用于查真实换月日历)
        cal_dates = {r["date"] for r in (calendar.get(variety, {}) or {}).get("rollover_dates", [])}  # 【变量】该品种真实换月日

        before = _max_gap_pct(prices)  # 【变量】调整前最大单日缺口
        roll_idx = _backward_adjust(prices, threshold, calendar_dates=cal_dates or None)[1]  # 【调用函数】后复权(原地修改 prices)
        after = _max_gap_pct(prices)  # 【变量】调整后最大单日缺口
        roll_dates = [prices[i]["date"] for i in roll_idx]  # 【变量】换月日期清单

        # 只有真正发生了调整(有换月点或缺口变化)才写回元数据
        changed = bool(roll_idx)
        if not dry_run and changed:
            data["prices"] = prices
            data["adjusted"] = True
            data["adjust_method"] = "backward"
            data["rollover_method"] = "calendar" if cal_dates else "heuristic"
            if not cal_dates:
                data["rollover_threshold_pct"] = threshold
            data["rollover_dates"] = roll_dates
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)  # 【调用函数】写回(保留缩进,便于 diff)

        tag = "ADJUSTED" if changed else "unchanged"
        print(f"  [{tag}] {f.name}: max_gap {before}% -> {after}%"
              + (f"  rollover_dates={roll_dates}" if roll_dates else ""))

    print("done.")


if __name__ == "__main__":
    main()
