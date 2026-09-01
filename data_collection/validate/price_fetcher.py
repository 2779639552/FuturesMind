"""
期货价格获取: akshare → 品种日线数据
输出: output/trends/{variety}_price.json
"""

import json  # 【调用包】读写 *_price.json 与读取情感索引 _index.json
import os  # 【调用包】检查情感索引文件是否存在
from pathlib import Path  # 【调用包】跨平台路径处理(OUTPUT_DIR)

OUTPUT_DIR = Path(__file__).parent / "output" / "trends"  # 【变量】价格数据输出目录

# 品种名 → akshare symbol
VARIETY_SYMBOLS = {  # 【变量】品种名→akshare 新浪主连合约代码(传给 ak.futures_main_sina)
    "螺纹钢": "RB0",
    "铁矿石": "I0",
    "焦炭": "J0",
    "焦煤": "JM0",
    "热卷": "HC0",
    "硅铁": "SF0",
    "锰硅": "SM0",
    "沪铜": "CU0",
    "沪铝": "AL0",
    "沪锌": "ZN0",
    "沪镍": "NI0",
    "沪铅": "PB0",
    "沪锡": "SN0",
    "黄金": "AU0",
    "白银": "AG0",
    "原油": "SC0",
    "PTA": "TA0",
    "甲醇": "MA0",
    "PVC": "V0",
    "PP": "PP0",
    "塑料": "L0",
    "橡胶": "RU0",
    "沥青": "BU0",
    "尿素": "UR0",
    "纯碱": "SA0",
    "玻璃": "FG0",
    "乙二醇": "EG0",
    "苯乙烯": "EB0",
    "短纤": "PF0",
    "豆粕": "M0",
    "豆油": "Y0",
    "棕榈油": "P0",
    "菜粕": "RM0",
    "菜油": "OI0",
    "白糖": "SR0",
    "棉花": "CF0",
    "玉米": "C0",
    "淀粉": "CS0",
    "鸡蛋": "JD0",
    "生猪": "LH0",
    "苹果": "AP0",
    "红枣": "CJ0",
    "花生": "PK0",
    "碳酸锂": "LC0",
    "工业硅": "SI0",
    "氧化铝": "AO0",
    # 2026-09-01 扩能化整组 12 品种(新增 4 个主连代码)
    "燃料油": "FU0",
    "低硫燃料油": "LU0",
    "20号胶": "NR0",
    "对二甲苯": "PX0",
}


# 【功能】获取所有(或有情绪数据的)品种价格日线, 标准化列名并计算涨跌幅, 落盘 *_price.json。
# 【参数】sentiment_index_path: 可选, _index.json 路径; 提供时只拉有情绪数据的品种。
# 【返回】{品种: {"prices": [...], "latest": {...}, "date_range": "..."}}; akshare 未安装时返回空 dict。
# 【关键】调 akshare 新浪主连接口; 列名按固定顺序重命名; 只保留最近180天。
def fetch_all(sentiment_index_path: str = None) -> dict:
    """获取所有有情绪数据的品种价格"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 读取情绪索引确定要拉哪些品种
    varieties = list(VARIETY_SYMBOLS.keys())  # 【变量】待拉取品种列表(默认全部)
    if sentiment_index_path and os.path.exists(sentiment_index_path):
        with open(sentiment_index_path, encoding="utf-8") as f:
            idx = json.load(f)  # 【调用函数】落盘读取: 情感汇总索引(据此确定品种子集)
        varieties = [v for v in idx if v in VARIETY_SYMBOLS]
        print(f"Fetching prices for {len(varieties)} varieties with sentiment data")
    else:
        print(f"Fetching prices for all {len(varieties)} varieties")

    try:
        import akshare as ak  # 【调用包】akshare: 新浪期货行情数据源(按需导入)
    except ImportError:
        print("ERROR: pip install akshare")
        return {}

    result = {}
    for i, variety in enumerate(varieties):
        symbol = VARIETY_SYMBOLS.get(variety)  # 【变量】该品种的 akshare 合约代码
        if not symbol:
            continue

        try:
            df = ak.futures_main_sina(symbol=symbol)  # 【调用函数】外部API(akshare): 拉取新浪主连日线行情
            # 标准化列名
            df.columns = ["date", "open", "high", "low", "close", "volume", "position", "settle"]
            # 只保留最近180天
            df = df.tail(180)
            # 转list
            prices = []  # 【变量】标准化后的价格记录列表(日期/开高低收/成交量)
            for _, row in df.iterrows():
                prices.append(
                    {
                        "date": str(row["date"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(row["volume"]),
                    }
                )

            # 计算涨跌幅
            for j in range(1, len(prices)):
                prev_close = prices[j - 1]["close"]
                if prev_close > 0:
                    prices[j]["change_pct"] = round(
                        (prices[j]["close"] - prev_close) / prev_close * 100, 2
                    )
                else:
                    prices[j]["change_pct"] = 0
            if prices:
                prices[0]["change_pct"] = 0

            latest = prices[-1] if prices else None  # 【变量】最新一条价格(供看板展示现价/涨跌)
            result[variety] = {
                "prices": prices,
                "latest": latest,
                "date_range": f"{prices[0]['date']} ~ {prices[-1]['date']}" if prices else "N/A",
            }

            # 写文件
            out_path = OUTPUT_DIR / f"{variety}_price.json"  # 【变量】品种价格输出路径
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result[variety], f, ensure_ascii=False)  # 【调用函数】落盘: 写该品种价格 JSON

            print(f"  [{i + 1}/{len(varieties)}] {variety}({symbol}): {len(prices)} days")

        except Exception as e:
            print(f"  [{i + 1}/{len(varieties)}] {variety}({symbol}): ERROR - {e}")

    print(f"\nSaved to {OUTPUT_DIR}")
    return result


if __name__ == "__main__":
    idx_path = str(OUTPUT_DIR / "_index.json") if (OUTPUT_DIR / "_index.json").exists() else None
    fetch_all(idx_path)
