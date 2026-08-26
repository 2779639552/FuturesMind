"""回归测试: generate_tradingagents_sentiment 读取平台权重的键 (2026-08-26)。

Bug 背景: `{品种}_weights.json` 的平台权重存在 **"weights"** 键下(apply_and_save 写入),
生成脚本 `generate_sentiment_json` 却读 `"platform_weights"` → 生成的
`*_sentiment.json` 里 `data.platform_weights.weights` 恒空 {} → get_futures_sentiment
「##4 平台权重」段对 LLM 缺失。

本测试锁定从 `"weights"` 键读取;weights 缺失/空时回退空字典不崩。
用 importlib 直接加载模块(仅标准库依赖), 不触真实文件与外部数据。
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_VALIDATE_DIR = Path(__file__).resolve().parents[1] / "data_collection" / "validate"


def _load_generator():
    """直接加载 generate_tradingagents_sentiment 模块(无副作用, 仅标准库)。"""
    mod_path = _VALIDATE_DIR / "generate_tradingagents_sentiment.py"
    spec = importlib.util.spec_from_file_location("_gts_gen", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_gts_gen"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
class TestGenerateSentimentPlatformWeights:
    @pytest.fixture(scope="class")
    def gen(self):
        return _load_generator()

    @staticmethod
    def _trends(weights: dict) -> dict:
        return {
            "sentiment": {
                "series": [
                    {
                        "date": "2026-08-01",
                        "avg_score": 0.2,
                        "simple_avg": 0.2,
                        "note_count": 3,
                        "bull_count": 2,
                        "bear_count": 1,
                        "weighted_score": 0.2,
                        "platform_counts": {"weibo": 3},
                        "top_authors": [],
                    }
                ]
            },
            "weights": weights,
        }

    def test_platform_weights_read_from_weights_key(self, gen):
        """真实 weights 文件结构: 平台权重在 'weights' 键下 → 输出非空。"""
        out = gen.generate_sentiment_json(
            "螺纹钢",
            self._trends(
                {"weights": {"weibo": 0.5, "xhs": 0.5}, "weight_source": "global_backtest"}
            ),
            {},
            {},
        )
        assert out is not None
        pw = out["data"]["platform_weights"]
        assert pw["weights"] == {"weibo": 0.5, "xhs": 0.5}  # 修复前恒空 {}
        assert pw["source"] == "global_backtest"

    def test_missing_weights_falls_back_empty(self, gen):
        """weights 文件无 'weights' 键(未校准) → 空字典, 不崩。"""
        out = gen.generate_sentiment_json("螺纹钢", self._trends({}), {}, {})
        assert out is not None
        assert out["data"]["platform_weights"]["weights"] == {}

    def test_weight_source_default(self, gen):
        """weight_source 缺失 → 默认 not_calibrated。"""
        out = gen.generate_sentiment_json(
            "螺纹钢", self._trends({"weights": {"weibo": 1.0}}), {}, {}
        )
        assert out is not None
        assert out["data"]["platform_weights"]["source"] == "not_calibrated"
