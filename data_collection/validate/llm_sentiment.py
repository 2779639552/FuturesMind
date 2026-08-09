"""
LLM情感引擎 — 使用大语言模型做期货文本情感分析
=================================================
原理: 将期货领域知识注入Prompt, 让LLM理解专业术语/反讽/因果链

对比规则引擎:
  规则引擎: 词典匹配 + 简单否定/转折 → 43% 一致率 (vs LLM)
  LLM:      语义理解 + 领域知识 → 能正确判"多头撤离=看空"

使用:
  from llm_sentiment import LLMSentimentEngine
  engine = LLMSentimentEngine(provider="claude")  # or "openai"
  result = engine.analyze("多头资金大幅撤离，持仓骤降")
  # → {sentiment: "bearish", score: -0.55, confidence: 0.9, reasoning: "..."}
"""

import json
import os

# ============================================================
# Prompt 设计 — 注入期货领域知识
# ============================================================

SYSTEM_PROMPT = """你是期货市场情绪分析专家。分析给定的中文社交媒体文本，判断其对期货品种的情感倾向。

## 输出格式
严格返回JSON:
{
  "sentiment": "strong_bullish|bullish|slightly_bullish|neutral|slightly_bearish|bearish|strong_bearish",
  "score": -1.0到1.0之间的浮点数,
  "confidence": 0到1之间的浮点数,
  "reasoning": "简短的分析理由(30字以内)",
  "key_phrases": ["触发判断的关键短语"]
}

## 期货领域知识

### 看多信号 (bullish)
- 价格上涨: 大涨/拉升/冲高/突破/创新高/涨停
- 资金流入: 增仓/加仓/多头入场/放量上攻
- 结构改善: 基差走强/contango收窄/back结构/月差扩大
- 基本面: 库存下降/需求回暖/供给收缩/检修/限产
- 技术面: 金叉/均线多头排列/量价齐升/底部放量

### 看空信号 (bearish)
- 价格下跌: 暴跌/跳水/跌破/创新低/跌停
- 资金流出: 减仓/清仓/多头撤离/持仓骤降/主力出货
- 结构恶化: 基差走弱/contango扩大/月差收窄
- 基本面: 库存累积/需求疲软/供过于求/复产/增产
- 技术面: 死叉/破位/顶背离/缩量下跌

### 重要规则
1. "多头被套/多头亏损/多头撤离" → 看空! (多头在赔钱/跑路)
2. "空头被套/空头撤离/逼空" → 看多! (空头在赔钱)
3. "X突破但量能不足/假突破" → 看空/中性 (突破不可信)
4. "底部放量+空头衰竭" → 看多/企稳 (底部信号)
5. "建议观望/等待方向/看不懂" → 中性
6. 反讽: "多头狂欢→多头坟场" → 看空

只返回JSON，不要其他文字。"""

USER_PROMPT_TEMPLATE = """分析以下期货相关文本的情感:

"{text}"

返回JSON:"""


# ============================================================
# 引擎实现
# ============================================================


class LLMSentimentEngine:
    """LLM情感分析引擎"""

    def __init__(
        self, provider: str = "claude", model: str = None, api_key: str = None, base_url: str = None
    ):
        """
        provider: "claude" | "openai" | "local"
        model: 模型名 (claude默认 claude-sonnet-5, openai默认 gpt-4o)
        """
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

        if provider == "claude":
            self.model = model or "claude-sonnet-5"
        elif provider == "openai":
            self.model = model or "gpt-4o"
        elif provider == "local":
            self.model = model or "localhost"

    def analyze(self, text: str) -> dict:
        """分析单条文本，返回完整结果dict"""
        if not text or not text.strip():
            return {
                "sentiment": "neutral",
                "score": 0.0,
                "confidence": 0.1,
                "reasoning": "empty text",
                "key_phrases": [],
                "engine": "llm",
            }

        if self.provider == "claude":
            return self._call_claude(text)
        elif self.provider == "openai":
            return self._call_openai(text)
        elif self.provider == "local":
            return self._call_local(text)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    def _call_claude(self, text: str) -> dict:
        """调用Claude API"""
        try:
            import anthropic
        except ImportError:
            return self._fallback_error("pip install anthropic")

        api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return self._fallback_error("No ANTHROPIC_API_KEY set")

        client = anthropic.Anthropic(api_key=api_key)
        if self.base_url:
            client = anthropic.Anthropic(api_key=api_key, base_url=self.base_url)

        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": USER_PROMPT_TEMPLATE.format(text=text)}],
            )

            # 解析JSON响应
            response_text = message.content[0].text.strip()
            # 提取JSON (Claude可能包裹在```json```中)
            if "```" in response_text:
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            result = json.loads(response_text)
            result["engine"] = "llm"
            return result

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            return self._fallback_error(f"Parse error: {e}")
        except Exception as e:
            return self._fallback_error(str(e))

    def _call_openai(self, text: str) -> dict:
        """调用OpenAI API"""
        try:
            from openai import OpenAI
        except ImportError:
            return self._fallback_error("pip install openai")

        api_key = self.api_key or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return self._fallback_error("No OPENAI_API_KEY set")

        client = OpenAI(api_key=api_key)
        if self.base_url:
            client = OpenAI(api_key=api_key, base_url=self.base_url)

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": USER_PROMPT_TEMPLATE.format(text=text)},
                ],
                max_tokens=256,
                temperature=0.1,  # 低温度保证一致性
            )
            response_text = response.choices[0].message.content.strip()
            if "```" in response_text:
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            result = json.loads(response_text)
            result["engine"] = "llm"
            return result

        except (json.JSONDecodeError, KeyError) as e:
            return self._fallback_error(f"Parse error: {e}")
        except Exception as e:
            return self._fallback_error(str(e))

    def _call_local(self, text: str) -> dict:
        """调用本地模型 (Ollama兼容)"""
        import requests

        try:
            resp = requests.post(
                f"{self.base_url or 'http://localhost:11434'}/api/generate",
                json={
                    "model": self.model or "qwen2.5:7b",
                    "prompt": SYSTEM_PROMPT + "\n\n" + USER_PROMPT_TEMPLATE.format(text=text),
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
                timeout=30,
            )
            data = resp.json()
            response_text = data.get("response", "").strip()
            if "```" in response_text:
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            result = json.loads(response_text)
            result["engine"] = "llm"
            return result
        except Exception as e:
            return self._fallback_error(str(e))

    def _fallback_error(self, msg: str) -> dict:
        return {
            "sentiment": "neutral",
            "score": 0.0,
            "confidence": 0.0,
            "reasoning": f"LLM error: {msg}",
            "key_phrases": [],
            "engine": "llm_fallback",
        }

    def analyze_batch(self, texts: list[str]) -> list[dict]:
        """批量分析"""
        return [self.analyze(t) for t in texts]


# ============================================================
# 混合分析器 — 规则引擎 + LLM
# ============================================================


class HybridSentimentAnalyzer:
    """
    混合情感分析器:
    - 默认使用规则引擎 (快速/免费)
    - 对高不确定性样本自动调用LLM验证
    - 或全部使用LLM (需API key)
    """

    def __init__(self, llm_engine: LLMSentimentEngine = None, fallback_threshold: float = 0.4):
        from sentiment import SentimentAnalyzer

        self.rule = SentimentAnalyzer()
        self.llm = llm_engine
        self.threshold = fallback_threshold

    def analyze(self, text: str) -> dict:
        """规则引擎先行, 低置信度时LLM验证"""
        rule_result = self.rule.analyze(text)

        # 如果规则引擎置信度高, 直接返回
        if rule_result["confidence"] >= self.threshold:
            rule_result["engine"] = "rule"
            return rule_result

        # 低置信度 → LLM验证
        if self.llm:
            llm_result = self.llm.analyze(text)
            llm_result["rule_score"] = rule_result["score"]
            llm_result["rule_sentiment"] = rule_result["sentiment"]
            return llm_result

        rule_result["engine"] = "rule_lowconf"
        return rule_result


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    test_texts = [
        "多头资金大幅撤离，持仓量骤降，这波行情要小心了",
        "螺纹钢今天增仓大涨，突破3800，多头力量很强",
        "焦煤09进入交割预演阶段，基差走强",
        "纯碱来回割韭菜，建议观望等方向",
        "连续下跌后底部放量，空头力量衰竭，可能企稳",
    ]

    # 测试规则引擎
    from sentiment import SentimentAnalyzer

    sa = SentimentAnalyzer()
    print("=" * 60)
    print("  规则引擎 (当前)")
    print("=" * 60)
    for text in test_texts:
        r = sa.analyze(text)
        print(f"  [{r['sentiment']:>16s}] score={r['score']:+.2f} conf={r['confidence']}")
        print(f"    {text[:55]}")
        print()

    # LLM引擎需要API key
    print("=" * 60)
    print("  LLM引擎使用方式:")
    print("=" * 60)
    print("""
  # Claude API
  export ANTHROPIC_API_KEY="sk-ant-..."
  python -c "
  from llm_sentiment import LLMSentimentEngine
  engine = LLMSentimentEngine(provider='claude')
  result = engine.analyze('多头资金大幅撤离，持仓骤降')
  print(result)
  "

  # OpenAI API
  export OPENAI_API_KEY="sk-..."
  engine = LLMSentimentEngine(provider='openai')

  # 本地模型 (Ollama)
  engine = LLMSentimentEngine(provider='local', model='qwen2.5:7b')

  # 混合模式 (规则先行, 不确定时LLM验证)
  from llm_sentiment import HybridSentimentAnalyzer
  hybrid = HybridSentimentAnalyzer(llm_engine=engine, fallback_threshold=0.4)
  result = hybrid.analyze(text)
    """)
