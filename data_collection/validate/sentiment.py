"""
期货社交媒体情感分析模块
============================

双引擎:
  A. 规则引擎 (RuleEngine)    — 秒级、零依赖、可解释、适合实时流
  B. FinBERT引擎 (FinBERTEngine) — 准确度高、需pip install transformers

7级情感分类:
  strong_bullish / bullish / slightly_bullish /
  neutral /
  slightly_bearish / bearish / strong_bearish

额外维度:
  - confidence: 情感判断置信度
  - certainty: 表达确定性 ("一定涨" vs "可能涨")
  - time_horizon: 时间维度 (short/mid/long_term)
  - key_phrases: 触发情感的原文片段

使用:
  from sentiment import SentimentAnalyzer
  sa = SentimentAnalyzer()
  result = sa.analyze("螺纹钢今天增仓大涨，多头力量很强")
  # → {sentiment: "bullish", score: 0.52, confidence: 0.8, ...}
"""

import re
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple

# ================================================================
# 情感词库 — 期货领域专用
# ================================================================

# 看多信号词 (正向权重)
BULLISH_LEXICON = {
    # 强看多信号 (权重 3.0)
    "暴涨": 3.0, "飙升": 3.0, "涨停": 3.0, "逼空": 3.0,
    "强势突破": 3.0, "放量上攻": 3.0, "大牛市": 3.0,
    "主升浪": 3.0, "井喷": 3.0, "起飞": 2.5,

    # 看多信号 (权重 2.0)
    "大涨": 2.0, "上涨": 1.5, "走高": 1.5, "走强": 1.5,
    "拉升": 1.8, "推高": 1.5, "反弹": 1.5, "上扬": 1.5,
    "突破": 1.8, "冲高": 1.5, "创新高": 2.5, "新高": 2.0,
    "多头": 1.5, "做多": 2.0, "买入": 2.0, "加仓": 1.8,
    "建仓": 1.5, "增仓": 1.5, "入场": 1.0, "抄底": 1.5,
    "牛市": 2.5, "看多": 2.0, "看涨": 2.0,
    "利好": 1.8, "利多": 1.8, "偏多": 1.2, "企稳": 1.0,
    "放量": 1.2, "走好": 1.0, "回暖": 1.2, "复苏": 1.5,
    "盈利": 1.0, "止盈": 0.8, "浮盈": 1.0,
    "机会": 0.5, "布局": 0.8, "值得关注": 0.5,
    "强势": 1.5, "坚挺": 1.5, "抗跌": 1.2,
    "站稳": 1.0, "支撑": 0.5, "底部": 0.8,
    "单边上行": 2.5, "趋势向上": 2.0, "多头排列": 2.5,
    "金叉": 2.0, "均线多头": 2.0, "量价齐升": 2.5,

    # 弱看多信号 (权重 0.8)
    "小幅上涨": 1.0, "微涨": 0.8, "略涨": 0.8,
    "窄幅震荡偏强": 0.8, "短期偏多": 1.0,
}

# 看空信号词 (负向权重)
BEARISH_LEXICON = {
    # 强看空信号
    "暴跌": -3.0, "崩盘": -3.0, "跌停": -3.0, "跳水": -2.5,
    "砸盘": -2.5, "逼多": -2.5, "踩踏": -3.0, "恐慌": -2.5,
    "主力出货": -3.0, "断崖": -3.0, "腰斩": -3.0,

    # 看空信号
    "大跌": -2.0, "下跌": -1.5, "走低": -1.5, "走弱": -1.5,
    "下行": -1.5, "回落": -1.2, "回调": -1.0, "下滑": -1.5,
    "跌破": -1.8, "创新低": -2.5, "新低": -2.0,
    "空头": -1.5, "做空": -2.0, "卖出": -2.0, "减仓": -1.5,
    "清仓": -2.5, "止损": -1.8, "割肉": -2.5,
    "熊市": -2.5, "看空": -2.0, "看跌": -2.0,
    "利空": -1.8, "偏空": -1.2, "承压": -1.2,
    "缩量": -1.0, "走坏": -1.0, "低迷": -1.5,
    "亏损": -1.5, "浮亏": -1.5, "套牢": -2.0,
    "风险": -1.0, "谨慎": -0.8, "观望": -0.5,
    "弱势": -1.5, "疲软": -1.5, "阻力": -0.8,
    "单边下行": -2.5, "趋势向下": -2.0, "空头排列": -2.5,
    "死叉": -2.0, "顶背离": -2.0, "破位": -2.0,
    "出货": -1.5, "诱多": -2.0, "洗盘": -1.0,

    # 弱看空信号
    "小幅下跌": -1.0, "微跌": -0.8, "略跌": -0.8,
    "窄幅震荡偏弱": -0.8, "短期偏空": -1.0,
}

# 中性/观望信号 — 抵消弱情感
NEUTRAL_SIGNALS = {
    "震荡", "盘整", "横盘", "窄幅震荡", "宽幅震荡",
    "不明", "说不清", "不好说", "再看看", "观望",
    "建议观望", "等待方向", "方向不明", "不确定",
    "看不清", "看不懂", "谨慎观望",
}

# 确定性修饰词 (影响 confidence)
CERTAINTY_MODIFIERS = {
    "一定": 1.5, "肯定": 1.5, "必然": 1.5, "绝对": 1.3,
    "毫无疑问": 1.5, "毋庸置疑": 1.5, "铁定": 1.5,
    "大概率": 1.2, "极有可能": 1.2, "强烈": 1.3,
    "可能": 0.7, "或许": 0.6, "也许": 0.6, "大概": 0.6,
    "估计": 0.5, "感觉": 0.4, "猜测": 0.3, "猜": 0.3,
    "不确定": 0.2, "难说": 0.2, "未必": 0.3,
}

# 时间维度信号
TIME_HORIZON_SIGNALS = {
    "短期": "short", "短线": "short", "日内": "short",
    "今天": "short", "今天下午": "short", "明日": "short",
    "本周": "short", "这周": "short", "下周": "short",
    "中期": "mid", "中线": "mid", "波段": "mid",
    "本月": "mid", "下月": "mid", "季度": "mid",
    "长期": "long", "长线": "long", "趋势": "long",
    "今年": "long", "明年": "long", "半年": "long",
    "年度": "long", "大周期": "long", "远期": "long",
}

# 否定词 (翻转情感) — 必须是独立否定，不含固定搭配
NEGATION_WORDS = {"没有", "并未", "并非", "不会", "不可能",
                   "难以", "很难", "不会再有"}

# 单字否定词 — 需额外验证不是固定搭配的一部分
SINGLE_CHAR_NEGATION = {"不", "未", "无", "非"}

# 含否定字的固定搭配 (不应触发情感翻转)
NEGATION_FALSE_POSITIVES = {
    "不明", "不错", "不少", "不久", "不断", "不然", "不如",
    "不足", "不已", "不止", "不菲", "不二", "不大",
    "未知", "未必", "未免", "未定", "未见",
    "无关", "无论", "无比", "无效", "无意",
    "非常", "非凡", "非但",
}

# 转折词 (情感可能转变)
TRANSITION_WORDS = {"但", "但是", "然而", "不过", "可是",
                     "却", "虽然", "尽管", "只是", "可惜"}


# ================================================================
# 数据类
# ================================================================

@dataclass
class SentimentResult:
    """情感分析结果"""
    sentiment: str = "neutral"            # strong_bullish|bullish|slightly_bullish|neutral|slightly_bearish|bearish|strong_bearish
    score: float = 0.0                     # -1.0(最强空) ~ +1.0(最强多)
    confidence: float = 0.0                # 0~1 判断置信度
    certainty: float = 0.0                 # 0~1 表达确定性
    time_horizon: str = ""                 # short|mid|long|""
    key_phrases: list = field(default_factory=list)  # 触发情感的关键原文片段
    bull_signals: int = 0                  # 看多信号计数
    bear_signals: int = 0                  # 看空信号计数
    engine: str = "rule"                   # rule|finbert


# ================================================================
# 规则引擎
# ================================================================

class RuleEngine:
    """基于词典+规则的情感分析"""

    def analyze(self, text: str) -> SentimentResult:
        """对文本做情感分析"""
        if not text:
            return SentimentResult()

        result = SentimentResult()

        # 1. 信号扫描
        bull_score, bull_phrases = self._scan_lexicon(text, BULLISH_LEXICON)
        bear_score, bear_phrases = self._scan_lexicon(text, BEARISH_LEXICON)
        _, neutral_phrases = self._scan_lexicon(text, {n: 0 for n in NEUTRAL_SIGNALS})

        result.bull_signals = len(bull_phrases)
        result.bear_signals = len(bear_phrases)

        # 中性信号衰减: 如果有中性信号，整体偏向neutral
        neutral_damping = 0.3 * len(neutral_phrases)

        # 2. 否定词处理 (否定词后的看多/看空词应翻转)
        bull_score, bear_score = self._apply_negation(text, bull_score, bear_score)

        # 3. 转折词处理 (转折后的情感更重要)
        bull_score, bear_score = self._apply_transition(text, bull_score, bear_score)

        # 4. 综合得分 (-1.0 ~ +1.0)
        # 策略: 信号越多越可信; 孤立弱信号应衰减
        bull_count = len(bull_phrases)
        bear_count = len(bear_phrases)
        total_signals = bull_count + bear_count
        total_magnitude = abs(bull_score) + abs(bear_score)

        if total_magnitude > 0:
            # 基础得分
            raw_score = (bull_score + bear_score) / total_magnitude

            # 衰减因子: 信号太少时降权
            if total_signals == 1:
                decay = 0.5  # 孤立的单个信号不太可信
            elif total_signals == 2:
                decay = 0.75
            else:
                decay = 1.0

            # 双面信号(pure ambivalence): 多空都有→偏中性
            if bull_count > 0 and bear_count > 0:
                ambivalence_penalty = 0.6
            else:
                ambivalence_penalty = 1.0

            adjusted = raw_score * decay * ambivalence_penalty
            # 中性信号衰减
            if neutral_damping > 0:
                adjusted = adjusted * max(0.2, 1.0 - neutral_damping)
            result.score = round(max(-1.0, min(1.0, adjusted)), 3)
        else:
            result.score = 0.0

        # 5. 7级分类
        result.sentiment = self._score_to_sentiment(result.score)

        # 6. 置信度 (信号越多越确定)
        signal_count = result.bull_signals + result.bear_signals
        if signal_count >= 5:
            result.confidence = min(1.0, 0.6 + signal_count * 0.08)
        elif signal_count >= 2:
            result.confidence = 0.4 + signal_count * 0.1
        elif signal_count == 1:
            result.confidence = 0.3
        else:
            result.confidence = 0.1

        # 7. 确定性 (从修饰词判断)
        result.certainty = self._compute_certainty(text)

        # 8. 时间维度
        result.time_horizon = self._compute_time_horizon(text)

        # 9. 关键短语 (最多保留5个)
        result.key_phrases = (bull_phrases + bear_phrases)[:5]

        return result

    def _scan_lexicon(self, text: str, lexicon: dict) -> Tuple[float, list]:
        """扫描词库，返回(加权得分, 匹配短语列表)"""
        score = 0.0
        phrases = []
        for phrase, weight in lexicon.items():
            if phrase in text:
                score += weight
                phrases.append(phrase)
        return score, phrases

    def _apply_negation(self, text: str, bull: float, bear: float) -> Tuple[float, float]:
        """否定词翻转: "不会涨" → 看多变看空。排除固定搭配误判。"""
        all_negations = list(NEGATION_WORDS)

        # 单字否定词：需额外验证不是固定搭配
        for neg_char in SINGLE_CHAR_NEGATION:
            idx = 0
            while True:
                idx = text.find(neg_char, idx)
                if idx < 0:
                    break
                # 检查是否是固定搭配的一部分
                is_false_positive = False
                for fp in NEGATION_FALSE_POSITIVES:
                    if fp in text and text.find(fp) <= idx <= text.find(fp) + len(fp):
                        is_false_positive = True
                        break
                if not is_false_positive:
                    all_negations.append(neg_char)
                idx += 1

        for neg in all_negations:
            neg_pos = text.find(neg)
            if neg_pos < 0:
                continue
            after = text[neg_pos + len(neg):neg_pos + len(neg) + 12]
            for phrase, weight in BULLISH_LEXICON.items():
                if phrase in after:
                    bull -= weight * 1.5
                    bear += abs(weight) * 0.5
            for phrase, weight in BEARISH_LEXICON.items():
                if phrase in after:
                    bear -= weight * 1.5
                    bull += abs(weight) * 0.5
        return bull, bear

    def _apply_transition(self, text: str, bull: float, bear: float) -> Tuple[float, float]:
        """转折词处理: 转折后的情感权重加倍"""
        for trans in TRANSITION_WORDS:
            pos = text.find(trans)
            if pos < 0:
                continue
            # 转折词后的内容权重翻倍
            after = text[pos:]
            for phrase, weight in BULLISH_LEXICON.items():
                if phrase in after:
                    bull += weight * 0.5  # 额外的50%权重
            for phrase, weight in BEARISH_LEXICON.items():
                if phrase in after:
                    bear += weight * 0.5
        return bull, bear

    def _score_to_sentiment(self, score: float) -> str:
        """数值 → 7级情感标签"""
        if score >= 0.6:
            return "strong_bullish"
        elif score >= 0.3:
            return "bullish"
        elif score >= 0.1:
            return "slightly_bullish"
        elif score > -0.1:
            return "neutral"
        elif score > -0.3:
            return "slightly_bearish"
        elif score > -0.6:
            return "bearish"
        else:
            return "strong_bearish"

    def _compute_certainty(self, text: str) -> float:
        """从修饰词计算表达确定性"""
        total_mod = 0.0
        count = 0
        for mod, weight in CERTAINTY_MODIFIERS.items():
            if mod in text:
                total_mod += weight
                count += 1
        if count == 0:
            return 0.5  # 默认中等
        # 归一化到 [0, 1]
        return round(min(1.0, max(0.1, total_mod / count / 2.0)), 2)

    def _compute_time_horizon(self, text: str) -> str:
        """提取时间维度"""
        horizons = {"short": 0, "mid": 0, "long": 0}
        for phrase, horizon in TIME_HORIZON_SIGNALS.items():
            if phrase in text:
                horizons[horizon] += 1
        if sum(horizons.values()) == 0:
            return ""
        return max(horizons, key=horizons.get)


# ================================================================
# FinBERT 模型引擎（需 transformers）
# ================================================================

class FinBERTEngine:
    """
    使用中文金融BERT做情感分析。
    需要: pip install transformers torch
    首次运行自动下载模型 (~400MB)
    """

    MODEL_NAME = "ProsusAI/finBERT"  # 英文FinBERT
    # 中文金融情感备选: "bigeasy/FinBERT-Chinese" (需要验证)

    def __init__(self):
        self.model = None
        self.tokenizer = None
        self._loaded = False

    def _lazy_load(self):
        if self._loaded:
            return
        try:
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            import torch
            self.tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME)
            self.model = AutoModelForSequenceClassification.from_pretrained(self.MODEL_NAME)
            self.model.eval()
            self._loaded = True
        except ImportError:
            raise ImportError(
                "FinBERT requires: pip install transformers torch\n"
                "Or use RuleEngine for instant results."
            )

    def analyze(self, text: str) -> SentimentResult:
        """用FinBERT分析情感"""
        self._lazy_load()
        import torch

        inputs = self.tokenizer(text, return_tensors="pt",
                                truncation=True, max_length=512)
        with torch.no_grad():
            outputs = self.model(**inputs)
            probs = torch.softmax(outputs.logits, dim=-1)[0]

        # FinBERT: [positive, negative, neutral]
        pos, neg, neu = probs[0].item(), probs[1].item(), probs[2].item()
        score = round(pos - neg, 3)

        result = SentimentResult(
            sentiment=self._score_to_sentiment(score),
            score=score,
            confidence=max(pos, neg, neu),
            engine="finbert",
        )
        return result

    def _score_to_sentiment(self, score: float) -> str:
        return RuleEngine()._score_to_sentiment(score)


# ================================================================
# 统一分析器
# ================================================================

class SentimentAnalyzer:
    """
    统一情感分析入口。
    默认使用规则引擎。如果装了FinBERT自动使用双引擎。
    """

    def __init__(self, use_finbert: bool = False):
        self.rule_engine = RuleEngine()
        self.finbert_engine = None
        if use_finbert:
            try:
                self.finbert_engine = FinBERTEngine()
            except ImportError:
                print("Warning: FinBERT not available, using rule engine only.")

    def analyze(self, text: str) -> dict:
        """分析单条文本，返回完整结果dict"""
        rule_result = self.rule_engine.analyze(text)

        output = asdict(rule_result)

        # 双引擎时，加入FinBERT结果做对比
        if self.finbert_engine:
            try:
                fb_result = self.finbert_engine.analyze(text)
                output["finbert_sentiment"] = fb_result.sentiment
                output["finbert_score"] = fb_result.score
                output["finbert_confidence"] = fb_result.confidence
                # 双引擎共识
                if rule_result.sentiment == fb_result.sentiment:
                    output["consensus"] = True
                    output["confidence"] = max(rule_result.confidence,
                                               fb_result.confidence)
                else:
                    output["consensus"] = False
            except Exception:
                pass

        return output

    def analyze_batch(self, texts: list[str]) -> list[dict]:
        """批量分析"""
        return [self.analyze(t) for t in texts]

    def analyze_aspects(self, text: str, varieties: list[dict],
                         window: int = 80) -> list[dict]:
        """
        品种级情感分析: 对每个品种，提取其上下文并单独做情感判断。

        Args:
          text: 完整原文
          varieties: NER提取的品种列表 [{"name":"螺纹钢","matched":"螺纹",...}]
          window: 品种名前后各取多少字符做上下文

        Returns:
          [{"variety":"螺纹钢","context":"...","sentiment":"bullish","score":+0.5,...}, ...]
        """
        results = []

        for v in varieties:
            name = v.get("name", "")
            matched = v.get("matched", "")
            if not name or not matched:
                continue

            # 找品种在原文中的位置
            pos = text.find(matched)
            if pos < 0:
                # 部分匹配（如别名和原文不完全一致）
                results.append({
                    "variety": name,
                    "context": "",
                    "sentiment": "neutral",
                    "score": 0.0,
                    "confidence": 0.1,
                    "key_phrases": [],
                })
                continue

            # 提取上下文窗口
            start = max(0, pos - window)
            end = min(len(text), pos + len(matched) + window)
            context = text[start:end].strip()

            # 在句号处截断
            if start > 0:
                first_period = context.find("。")
                if 0 < first_period < window:
                    context = context[first_period + 1:]
            if end < len(text):
                last_period = context.rfind("。")
                if last_period > window:
                    context = context[:last_period + 1]

            # 对上下文做情感分析
            r = self.analyze(context.strip())

            results.append({
                "variety": name,
                "matched_alias": matched,
                "context": context.strip(),
                "sentiment": r["sentiment"],
                "score": r["score"],
                "confidence": r["confidence"],
                "certainty": r["certainty"],
                "time_horizon": r["time_horizon"],
                "key_phrases": r["key_phrases"],
            })

        return results

    def enrich_notes(self, notes: list[dict],
                     text_field: str = "desc",
                     title_field: str = "title") -> list[dict]:
        """
        批量丰富笔记数据：对每条笔记的 title+desc 做情感分析，
        将结果直接合并到原 dict 中。

        Usage:
          sa = SentimentAnalyzer()
          enriched = sa.enrich_notes(deep_notes)
        """
        for note in notes:
            text = (note.get(title_field, "") or "") + " " + (note.get(text_field, "") or "")
            if text.strip():
                sentiment = self.analyze(text.strip())
                note["sentiment"] = sentiment["sentiment"]
                note["sentiment_score"] = sentiment["score"]
                note["sentiment_confidence"] = sentiment["confidence"]
                note["certainty"] = sentiment["certainty"]
                note["time_horizon"] = sentiment["time_horizon"]
                note["sentiment_signals"] = {
                    "bull_count": sentiment["bull_signals"],
                    "bear_count": sentiment["bear_signals"],
                    "key_phrases": sentiment["key_phrases"],
                }
            else:
                note["sentiment"] = "neutral"
                note["sentiment_score"] = 0.0
                note["sentiment_confidence"] = 0.0
        return notes


# ================================================================
# CLI 测试
# ================================================================

if __name__ == "__main__":
    sa = SentimentAnalyzer()

    test_texts = [
        "螺纹钢今天增仓大涨，多头力量很强，短期看涨",
        "铁矿石暴跌3%，空头砸盘，赶紧止损",
        "PTA窄幅震荡，方向不明，建议观望",
        "黄金可能还会涨，但是短期有回调风险",
        "不会大涨的，感觉还要跌",
        "焦煤09进入交割预演阶段，基差走强",
    ]

    print("=" * 70)
    print("期货情感分析 — 规则引擎测试")
    print("=" * 70)

    for text in test_texts:
        r = sa.analyze(text)
        emoji = {"strong_bullish": "🟢🟢", "bullish": "🟢", "slightly_bullish": "🟢▫",
                 "neutral": "⚪",
                 "strong_bearish": "🔴🔴", "bearish": "🔴", "slightly_bearish": "🔴▫"}
        print(f"\n  Text: {text}")
        print(f"  → {emoji.get(r['sentiment'], '?')} {r['sentiment']} "
              f"(score={r['score']}, conf={r['confidence']}, "
              f"certainty={r['certainty']}, horizon={r['time_horizon']})")
        print(f"     signals: +{r['bull_signals']}/-{r['bear_signals']} "
              f"→ {r['key_phrases'][:5]}")

    print(f"\n{'='*70}")
    print("测试完成。运行方式:")
    print("  python sentiment.py                         # 测试")
    print("  from sentiment import SentimentAnalyzer     # 导入使用")
    print("  sa = SentimentAnalyzer()")
    print("  result = sa.analyze('螺纹钢大涨')")
