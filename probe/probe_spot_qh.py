# -*- coding: utf-8 -*-
"""探针 5(关键): 验证 excess spot_price_qh 能否为 SC/LU/CJ/NR 取到现货历史序列。

spot_price_qh 返回 ['日期','期货收盘价','现货价格'], 可自算:
    basis      = 现货 - 期货
    basis_rate = (现货 - 期货) / 现货     # 与国君 dom_basis_rate 口径一致(已验证 PX)
"""
import warnings

import akshare as ak

warnings.filterwarnings("ignore")

TESTS = {
    "原油": "SC", "低硫燃料油": "LU", "燃料油": "FU", "红枣": "CJ",
    "对二甲苯": "PX", "纯苯": "BZ", "20号胶": "NR",
    "棉花": "CF", "乙二醇": "EG",   # 对照组(国君已有, 用于校验口径)
}


def main():
    rows = []
    for cn, code in TESTS.items():
        try:
            d = ak.spot_price_qh(symbol=cn)
            if d is None or d.empty:
                print(f"  [空] {cn}({code})")
                rows.append((code, cn, 0, "", ""))
                continue
            n = len(d)
            last = d.iloc[-1]
            cols = list(d.columns)
            print(f"  [OK] {cn}({code}): n={n} cols={cols}")
            print(f"       末行: {dict(last)}")
            # 自算基差率(用最后一行)
            try:
                sp = float(last[cols[-1]])
                fp = float(last[cols[1]])
                rate = (sp - fp) / sp if sp else None
                print(f"       自算 basis={sp - fp:.2f}  basis_rate={rate:.6f}")
                rows.append((code, cn, n, f"{sp - fp:.2f}", f"{rate:.6f}"))
            except Exception as e:  # noqa: BLE001
                print(f"       算基差失败: {e}")
                rows.append((code, cn, n, "?", "?"))
        except Exception as e:  # noqa: BLE001
            print(f"  [ERR] {cn}({code}): {type(e).__name__}: {str(e)[:120]}")
            rows.append((code, cn, 0, "ERR", ""))

    print("\n" + "=" * 74)
    print("汇总")
    print("=" * 74)
    print(f"  {'代码':<6}{'生意社名':<14}{'点数':<8}{'基差':<12}{'基差率'}")
    for r in rows:
        print(f"  {r[0]:<6}{r[1]:<14}{r[2]:<8}{r[3]:<12}{r[4]}")

    ok = [r[0] for r in rows if r[2] >= 10]
    print(f"\n  >>> spot_price_qh 可取到 >=10 点的: {ok}")
    print(f"  >>> 目标缺口品种覆盖情况: "
          f"{ {c: ('可补' if any(r[0] == c and r[2] >= 10 for r in rows) else '仍缺') for c in ['SC', 'LU', 'CJ', 'NR', 'PX', 'FU', 'BZ']} }")


if __name__ == "__main__":
    main()
