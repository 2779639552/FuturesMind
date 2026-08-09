"""
多平台情绪权重回测 v2 (全局统一 + 品种级 + 信号对比)
====================================================
v2 改进:
  - 多 horizon (1d / 3d / 5d) 评估不同周期预测力
  - 信号对比: simple_avg (不加权) vs avg_score (作者加权) vs weighted_score (平台+作者)
  - 品种级回测: 每个品种独立评估，不再一刀切
  - 网格搜索: 方向准确率 vs Pearson r 的最优混合比例
  - 统计显著性: min_points 门槛 + 样本量标注

使用: python backtest_weights.py
      python backtest_weights.py --horizons 1 3 5
      python backtest_weights.py --grid-search
"""

import argparse
import json
import math
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

TRENDS_DIR = Path(__file__).parent / "output" / "trends"

DEFAULT_MIN_POINTS = 10
DEFAULT_HORIZONS = [1, 3, 5]
GRID_SEARCH_STEPS = 11  # 0.0, 0.1, ..., 1.0


# ============================================================
# 数据加载
# ============================================================


def load_sentiment(variety: str) -> dict | None:
    path = TRENDS_DIR / f"{variety}_sentiment.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_price(variety: str) -> dict | None:
    path = TRENDS_DIR / f"{variety}_price.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 统计工具
# ============================================================


def pearson_r(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y, strict=True))
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))
    if std_x == 0 or std_y == 0:
        return 0.0
    return cov / (std_x * std_y)


def softmax(scores: dict[str, float], temperature: float = 1.0) -> dict[str, float]:
    if not scores:
        return {}
    items = list(scores.items())
    exp_sum = sum(math.exp(s / temperature) for _, s in items)
    if exp_sum == 0:
        n = len(items)
        return {p: 1.0 / n for p, _ in items}
    return {p: math.exp(s / temperature) / exp_sum for p, s in items}


def compute_accuracy(signals: list[float], changes: list[float]) -> dict:
    """计算方向准确率和 Pearson r"""
    n = len(signals)
    if n == 0:
        return {"direction_accuracy": 0.5, "pearson_r": 0.0, "data_points": 0}
    correct = sum(1 for s, c in zip(signals, changes, strict=True) if (s > 0) == (c > 0))
    return {
        "direction_accuracy": round(correct / n, 4),
        "pearson_r": round(pearson_r(signals, changes), 4),
        "data_points": n,
    }


# ============================================================
# 信号提取: 从 series 中提取三种情绪信号
# ============================================================


def extract_signals(series: list[dict], price_map: dict[str, float], horizon: int) -> dict:
    """对单个品种提取三种情绪信号 vs 未来价格变化的配对。

    Returns:
        {
            "simple":    (signals[], changes[])   # 不加权简单平均
            "author":    (signals[], changes[])   # 作者加权 avg_score
            "platform_weighted": {plat: (signals[], changes[])}  # 每平台
            "combined_weighted": (signals[], changes[])  # 平台加权合成 (如果有)
        }
    """
    all_dates = sorted(set(list(price_map.keys()) + [s["date"] for s in series]))
    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    result = {
        "simple": {"signals": [], "changes": []},
        "author": {"signals": [], "changes": []},
        "platform": defaultdict(lambda: {"signals": [], "changes": []}),
        "combined": {"signals": [], "changes": []},
    }

    for entry in series:
        date = entry["date"]
        t_idx = date_to_idx.get(date)
        if t_idx is None or t_idx + horizon >= len(all_dates):
            continue
        future_date = all_dates[t_idx + horizon]
        fp = price_map.get(future_date)
        cp = price_map.get(date)
        if fp is None or cp is None or cp == 0:
            continue
        price_change = (fp - cp) / cp

        # 信号1: simple_avg (不加权)
        sa = entry.get("simple_avg", 0)
        if sa != 0:
            result["simple"]["signals"].append(sa)
            result["simple"]["changes"].append(price_change)

        # 信号2: avg_score (作者加权)
        aa = entry.get("avg_score", 0)
        if aa != 0:
            result["author"]["signals"].append(aa)
            result["author"]["changes"].append(price_change)

        # 信号3: 分平台
        ps = entry.get("platform_scores", {})
        for plat, pdata in ps.items():
            pavg = pdata.get("avg_score", 0)
            if pavg != 0:
                result["platform"][plat]["signals"].append(pavg)
                result["platform"][plat]["changes"].append(price_change)

        # 信号4: 平台加权合成 (如果可用)
        ws = entry.get("weighted_score")
        if ws is not None and ws != 0:
            result["combined"]["signals"].append(ws)
            result["combined"]["changes"].append(price_change)

    return result


# ============================================================
# 全局回测 (所有品种池化)
# ============================================================


def compute_global_backtest(
    varieties: list[str],
    horizons: list[int],
    min_points: int,
    dir_weight: float = 0.8,
) -> dict:
    """全局池化回测。

    Returns per-horizon results for: platforms, simple_avg, author_avg, combined.
    """
    results = {}

    for horizon in horizons:
        pool_platforms = defaultdict(lambda: {"signals": [], "changes": []})
        pool_simple = {"signals": [], "changes": []}
        pool_author = {"signals": [], "changes": []}

        total_pairs = 0
        n_varieties_with_data = 0

        for variety in varieties:
            sent_data = load_sentiment(variety)
            price_data = load_price(variety)
            if not sent_data or not price_data:
                continue
            series = sent_data.get("series", [])
            prices = price_data.get("prices", [])
            if not series or not prices:
                continue

            price_map = {str(p["date"])[:10]: float(p["close"]) for p in prices}
            signals = extract_signals(series, price_map, horizon)
            n_varieties_with_data += 1

            # 池化: 平台
            for plat, pdata in signals["platform"].items():
                pool_platforms[plat]["signals"].extend(pdata["signals"])
                pool_platforms[plat]["changes"].extend(pdata["changes"])
                total_pairs += len(pdata["signals"])

            # 池化: simple
            pool_simple["signals"].extend(signals["simple"]["signals"])
            pool_simple["changes"].extend(signals["simple"]["changes"])

            # 池化: author
            pool_author["signals"].extend(signals["author"]["signals"])
            pool_author["changes"].extend(signals["author"]["changes"])

        # 平台级评估
        platform_metrics = {}
        platform_scores_for_softmax = {}
        for plat in pool_platforms:
            m = compute_accuracy(
                pool_platforms[plat]["signals"],
                pool_platforms[plat]["changes"],
            )
            platform_metrics[plat] = m
            if m["data_points"] >= min_points:
                score = m["direction_accuracy"] * dir_weight + abs(m["pearson_r"]) * (
                    1 - dir_weight
                )
                platform_scores_for_softmax[plat] = score

        # Softmax → 平台权重
        if len(platform_scores_for_softmax) >= 2:
            platform_weights = softmax(platform_scores_for_softmax, temperature=0.25)
            weight_source = "global_backtest"
        elif len(platform_scores_for_softmax) == 1:
            only = list(platform_scores_for_softmax.keys())[0]
            platform_weights = {only: 1.0}
            weight_source = "single_platform"
        else:
            n = len(pool_platforms)
            platform_weights = {p: 1.0 / max(n, 1) for p in pool_platforms} if n > 0 else {}
            weight_source = "fallback_equal"

        # 信号对比: simple vs author
        simple_metrics = compute_accuracy(pool_simple["signals"], pool_simple["changes"])
        author_metrics = compute_accuracy(pool_author["signals"], pool_author["changes"])

        results[f"h{horizon}"] = {
            "horizon_days": horizon,
            "n_varieties": n_varieties_with_data,
            "total_pairs": total_pairs,
            "platform_weights": {p: round(w, 4) for p, w in platform_weights.items()},
            "platform_metrics": platform_metrics,
            "weight_source": weight_source,
            "dir_weight": dir_weight,
            "signal_comparison": {
                "simple_avg": simple_metrics,
                "author_weighted": author_metrics,
                "winner": "author_weighted"
                if author_metrics.get("direction_accuracy", 0)
                > simple_metrics.get("direction_accuracy", 0)
                else "simple_avg",
            },
        }

    # 找出最佳 horizon
    best_h = None
    best_acc = 0
    for h_key, h_data in results.items():
        acc = h_data["signal_comparison"]["author_weighted"].get("direction_accuracy", 0)
        if acc > best_acc:
            best_acc = acc
            best_h = h_key

    return {
        "results_by_horizon": results,
        "best_horizon": best_h,
        "best_accuracy": best_acc,
    }


# ============================================================
# 品种级回测 (每个品种独立)
# ============================================================


def compute_variety_backtests(
    varieties: list[str],
    horizons: list[int],
    min_points: int,
) -> dict:
    """每个品种独立回测，返回品种级预测力排行。"""
    variety_results = {}

    for variety in varieties:
        sent_data = load_sentiment(variety)
        price_data = load_price(variety)
        if not sent_data or not price_data:
            continue
        series = sent_data.get("series", [])
        prices = price_data.get("prices", [])
        if not series or not prices:
            continue

        price_map = {str(p["date"])[:10]: float(p["close"]) for p in prices}
        vresult = {"name": variety, "horizons": {}}

        for horizon in horizons:
            signals = extract_signals(series, price_map, horizon)
            vresult["horizons"][f"h{horizon}"] = {
                "horizon_days": horizon,
                "simple_avg": compute_accuracy(
                    signals["simple"]["signals"], signals["simple"]["changes"]
                ),
                "author_weighted": compute_accuracy(
                    signals["author"]["signals"], signals["author"]["changes"]
                ),
            }

        # 取 h1 的作者加权准确率作为排行依据
        h1 = vresult["horizons"].get("h1", {})
        vresult["score"] = h1.get("author_weighted", {}).get("direction_accuracy", 0)
        variety_results[variety] = vresult

    return variety_results


# ============================================================
# 网格搜索最优 dir_weight
# ============================================================


def grid_search_optimal_weight(
    varieties: list[str],
    horizons: list[int],
    min_points: int,
) -> dict:
    """搜索 dir_weight 从 0.0 到 1.0 的最优值。

    dir_weight = 1.0 → 纯方向准确率
    dir_weight = 0.0 → 纯 Pearson |r|
    """
    best_result = None
    best_weight = 0.5
    best_acc = 0.0
    all_results = []

    for dw in [i / (GRID_SEARCH_STEPS - 1) for i in range(GRID_SEARCH_STEPS)]:
        gb = compute_global_backtest(varieties, horizons, min_points, dir_weight=dw)
        h1 = gb["results_by_horizon"].get("h1", {})
        acc = (
            h1.get("signal_comparison", {}).get("author_weighted", {}).get("direction_accuracy", 0)
        )
        all_results.append({"dir_weight": round(dw, 2), "accuracy": acc})

        if acc > best_acc:
            best_acc = acc
            best_weight = dw
            best_result = gb

    return {
        "optimal_dir_weight": round(best_weight, 2),
        "optimal_accuracy": best_acc,
        "all_results": all_results,
        "best_backtest": best_result,
    }


# ============================================================
# 应用权重 + 保存
# ============================================================


def apply_and_save(
    varieties: list[str],
    global_backtest: dict,
    variety_backtests: dict,
) -> None:
    """将全局回测结果写入 _global_weights.json 和各品种 _weights.json"""
    TRENDS_DIR.mkdir(parents=True, exist_ok=True)

    # 取 h1 的权重作为默认
    h1 = global_backtest["results_by_horizon"].get("h1", {})
    weights = h1.get("platform_weights", {})
    metrics = h1.get("platform_metrics", {})

    # 保存全局权重
    global_out = {
        "weights": weights,
        "metrics": metrics,
        "weight_source": h1.get("weight_source", "global_backtest"),
        "signal_comparison": h1.get("signal_comparison", {}),
        "best_horizon": global_backtest.get("best_horizon"),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "all_horizons": {},
    }
    for hk, hd in global_backtest["results_by_horizon"].items():
        global_out["all_horizons"][hk] = {
            "horizon_days": hd["horizon_days"],
            "platform_weights": hd["platform_weights"],
            "signal_comparison": hd["signal_comparison"],
        }

    with open(TRENDS_DIR / "_global_weights.json", "w", encoding="utf-8") as f:
        json.dump(global_out, f, ensure_ascii=False, indent=2)

    # 保存品种级权重
    for variety in varieties:
        vb = variety_backtests.get(variety, {})
        sent_data = load_sentiment(variety)
        if not sent_data:
            continue
        series = sent_data.get("series", [])

        # 加权序列
        weighted_series = []
        for entry in series:
            ps = entry.get("platform_scores", {})
            w_sum, w_total = 0.0, 0.0
            for plat, pdata in ps.items():
                w = weights.get(plat, 0)
                w_sum += w * pdata.get("avg_score", 0)
                w_total += w
            score = round(w_sum / w_total, 3) if w_total > 0 else 0.0
            weighted_series.append({"date": entry["date"], "weighted_score": score})

        variety_out = {
            "variety": variety,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "weights": weights,
            "weight_source": h1.get("weight_source", "global_backtest"),
            "combined_metrics": {
                "direction_accuracy": vb.get("horizons", {})
                .get("h1", {})
                .get("author_weighted", {})
                .get("direction_accuracy", 0.5),
                "pearson_r": vb.get("horizons", {})
                .get("h1", {})
                .get("author_weighted", {})
                .get("pearson_r", 0.0),
                "data_points": vb.get("horizons", {})
                .get("h1", {})
                .get("author_weighted", {})
                .get("data_points", 0),
            },
            "variety_backtest": vb,
            "weighted_sentiment": weighted_series,
        }

        with open(TRENDS_DIR / f"{variety}_weights.json", "w", encoding="utf-8") as f:
            json.dump(variety_out, f, ensure_ascii=False, indent=2)

    print(f"Saved: _global_weights.json + {len(varieties)} variety weights")


# ============================================================
# 主流程
# ============================================================


def run_all(
    min_points: int = DEFAULT_MIN_POINTS,
    horizons: list[int] = None,
    do_grid_search: bool = False,
) -> dict:
    if horizons is None:
        horizons = DEFAULT_HORIZONS

    TRENDS_DIR.mkdir(parents=True, exist_ok=True)

    # 发现品种
    varieties = sorted(
        {
            f.replace("_sentiment.json", "")
            for f in os.listdir(TRENDS_DIR)
            if f.endswith("_sentiment.json")
        }
    )
    print(f"Varieties with sentiment data: {len(varieties)}")
    print(f"Horizons: {horizons}")
    print()

    # ---- 1. 全局回测 ----
    print("=" * 60)
    print("  1. Global Backtest (all varieties pooled)")
    print("=" * 60)
    global_backtest = compute_global_backtest(varieties, horizons, min_points)

    for _hk, hd in global_backtest["results_by_horizon"].items():
        print(f"\n  Horizon {hd['horizon_days']}d:")
        print(f"    Pairs: {hd['total_pairs']} ({hd['n_varieties']} varieties)")
        print(f"    Weights ({hd['weight_source']}):")
        for p, w in hd["platform_weights"].items():
            m = hd["platform_metrics"].get(p, {})
            print(
                f"      {p}: {w:.4f}  (acc={m.get('direction_accuracy', 0):.3f}, "
                f"r={m.get('pearson_r', 0):.3f}, n={m.get('data_points', 0)})"
            )
        sc = hd["signal_comparison"]
        print("    Signal comparison:")
        print(
            f"      simple_avg:       acc={sc['simple_avg'].get('direction_accuracy', 0):.3f}  "
            f"r={sc['simple_avg'].get('pearson_r', 0):.3f}  n={sc['simple_avg'].get('data_points', 0)}"
        )
        print(
            f"      author_weighted:  acc={sc['author_weighted'].get('direction_accuracy', 0):.3f}  "
            f"r={sc['author_weighted'].get('pearson_r', 0):.3f}  n={sc['author_weighted'].get('data_points', 0)}"
        )
        print(f"      Winner: {sc['winner']}")

    print(
        f"\n  Best horizon: {global_backtest['best_horizon']} "
        f"(acc={global_backtest['best_accuracy']:.3f})"
    )

    # ---- 2. 品种级回测 ----
    print(f"\n{'=' * 60}")
    print("  2. Per-Variety Backtest")
    print("=" * 60)
    variety_backtests = compute_variety_backtests(varieties, horizons, min_points)

    # 排行
    ranked = sorted(variety_backtests.items(), key=lambda x: -x[1].get("score", 0))
    print("\n  Top 10 by author_weighted direction accuracy (h1):")
    for i, (vname, vdata) in enumerate(ranked[:10]):
        h1 = vdata["horizons"].get("h1", {})
        aw = h1.get("author_weighted", {})
        sa = h1.get("simple_avg", {})
        print(
            f"  {i + 1:>2}. {vname:<12s}  author_acc={aw.get('direction_accuracy', 0):.3f}  "
            f"simple_acc={sa.get('direction_accuracy', 0):.3f}  n={aw.get('data_points', 0)}"
        )

    print("\n  Bottom 5:")
    for i, (vname, vdata) in enumerate(ranked[-5:]):
        h1 = vdata["horizons"].get("h1", {})
        aw = h1.get("author_weighted", {})
        print(
            f"  {len(ranked) - 4 + i:>2}. {vname:<12s}  author_acc={aw.get('direction_accuracy', 0):.3f}  "
            f"n={aw.get('data_points', 0)}"
        )

    # ---- 3. 网格搜索 (可选) ----
    if do_grid_search:
        print(f"\n{'=' * 60}")
        print("  3. Grid Search: optimal dir_weight")
        print("=" * 60)
        gs = grid_search_optimal_weight(varieties, horizons, min_points)
        print(f"  Optimal dir_weight: {gs['optimal_dir_weight']}")
        print(f"  Best accuracy: {gs['optimal_accuracy']:.3f}")
        print("  Top 5 weights:")
        for r in sorted(gs["all_results"], key=lambda x: -x["accuracy"])[:5]:
            print(f"    dir_weight={r['dir_weight']:.2f}  accuracy={r['accuracy']:.3f}")
    else:
        gs = None

    # ---- 4. 保存 ----
    print(f"\n{'=' * 60}")
    print("  4. Saving weights")
    print("=" * 60)
    apply_and_save(varieties, global_backtest, variety_backtests)

    return {
        "global_backtest": global_backtest,
        "variety_backtests": variety_backtests,
        "grid_search": gs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="多平台情绪权重回测 v2")
    parser.add_argument("--min-points", type=int, default=DEFAULT_MIN_POINTS)
    parser.add_argument("--horizons", type=int, nargs="+", default=DEFAULT_HORIZONS)
    parser.add_argument("--grid-search", action="store_true", help="网格搜索最优 dir_weight")
    args = parser.parse_args()
    run_all(args.min_points, args.horizons, args.grid_search)
