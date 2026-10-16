# -*- coding: utf-8 -*-
"""探针 20: 测 100ppi 日频接口耗时, 决定缓存策略 + 确认 PX/FU/BZ 序列可用。"""
import time
import warnings

import akshare as ak

warnings.filterwarnings("ignore")

for days, tag in ((30, "30天"), (60, "60天"), (180, "180天")):
    from datetime import date, timedelta
    end = date(2026, 9, 29)
    start = end - timedelta(days=days)
    t0 = time.time()
    try:
        df = ak.futures_spot_price_daily(start_day=start.strftime("%Y%m%d"),
                                        end_day=end.strftime("%Y%m%d"))
        el = time.time() - t0
        cov = df.groupby("symbol").size().to_dict() if not df.empty else {}
        print(f"  {tag}: {el:.1f}s shape={df.shape} "
              f"PX={cov.get('PX', 0)} FU={cov.get('FU', 0)} BZ={cov.get('BZ', 0)} "
              f"CF={cov.get('CF', 0)}")
    except Exception as e:  # noqa: BLE001
        print(f"  {tag}: ERR {type(e).__name__}: {str(e)[:100]}")
