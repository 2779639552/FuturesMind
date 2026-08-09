"""
多模态情感分析 — 文本 + 图片 + Emoji 三通道融合
==================================================
双模型加权架构:
  - Qwen3-VL 2B (本地Ollama, 免费/CPU)  → 金融图表/数据截图专长
  - dots.vlm1 (HF Gradio API, 免费)     → 表情包/社交媒体图文专长

权重策略:
  - K线图/盈亏截图 → Qwen权重 0.7, dots权重 0.3
  - 表情包/贴纸   → Qwen权重 0.2, dots权重 0.8
  - 通用截图/其他  → Qwen权重 0.5, dots权重 0.5

设计原则:
  - 无GPU也能跑 (Qwen3-VL 2B GGUF CPU via Ollama)
  - dots.vlm1 走免费HuggingFace API
  - 任一模型不可用时自动降级到单模型
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("multimodal")

# ============================================================
# Emoji 情感词典
# ============================================================

EMOJI_SENTIMENT = {
    # 强烈看多 🚀📈💰🔥💎
    "🚀": ("strong_bullish", 0.8),
    "📈": ("bullish", 0.6),
    "💰": ("bullish", 0.5),
    "🔥": ("bullish", 0.6),
    "💎": ("strong_bullish", 0.7),
    "💎🙌": ("strong_bullish", 0.9),
    "🤑": ("bullish", 0.5),
    "💪": ("bullish", 0.4),
    "✨": ("slightly_bullish", 0.2),
    "🎉": ("bullish", 0.5),
    "🟢": ("bullish", 0.4),
    "✅": ("slightly_bullish", 0.2),
    "⬆️": ("bullish", 0.5),
    "↗️": ("slightly_bullish", 0.3),
    # 强烈看空 📉🔴💀😭
    "📉": ("bearish", -0.6),
    "🔴": ("bearish", -0.5),
    "💀": ("strong_bearish", -0.8),
    "😭": ("bearish", -0.5),
    "😱": ("bearish", -0.4),
    "🤮": ("strong_bearish", -0.6),
    "💔": ("bearish", -0.5),
    "📛": ("bearish", -0.4),
    "⚠️": ("slightly_bearish", -0.2),
    "😰": ("slightly_bearish", -0.3),
    "🔻": ("bearish", -0.5),
    "⬇️": ("bearish", -0.5),
    "↘️": ("slightly_bearish", -0.3),
    "😤": ("bearish", -0.3),
    # 中性/观望 🤔👀
    "🤔": ("neutral", 0.0),
    "👀": ("neutral", 0.0),
    "🤷": ("neutral", 0.0),
    "😐": ("neutral", 0.0),
}

# Emoji文本描述映射
EMOJI_TEXT_MAP = {
    "🚀": "火箭/暴涨",
    "📈": "上涨趋势",
    "📉": "下跌趋势",
    "💰": "赚钱",
    "💎": "钻石手/坚定持有",
    "💀": "爆仓/死了",
    "😭": "痛哭/亏损",
    "🔥": "火/热门",
    "🟢": "绿色/上涨",
    "🔴": "红色/下跌",
    "🤔": "思考/观望",
    "👀": "关注/观察",
}


@dataclass
class ImageAnalysis:
    """单张图片分析结果"""

    image_url: str = ""
    image_type: str = (
        "unknown"  # kline_chart | pnl_screenshot | meme_sticker | text_screenshot | general
    )
    ocr_text: str = ""  # OCR提取的文字
    visual_sentiment: str = "neutral"
    visual_score: float = 0.0
    visual_confidence: float = 0.0
    visual_summary: str = ""  # 图片内容简述
    qwen_result: dict = field(default_factory=dict)
    dots_result: dict = field(default_factory=dict)


class ImageTypeClassifier:
    """图片类型分类器 — 基于规则+关键特征快速判定"""

    CHART_KEYWORDS = [
        "K线",
        "蜡烛图",
        "走势图",
        "分时图",
        "日线",
        "周线",
        "均线",
        "MACD",
        "KDJ",
        "RSI",
        "布林带",
        "成交量",
        "涨跌幅",
        "开盘",
        "收盘",
        "最高",
        "最低",
    ]

    PNL_KEYWORDS = [
        "盈亏",
        "持仓",
        "当日盈亏",
        "浮动盈亏",
        "平仓",
        "手续费",
        "保证金",
        "可用资金",
        "权益",
        "逐笔",
    ]

    MEME_KEYWORDS = [
        "表情包",
        "笑哭",
        "哈哈哈",
        "卧槽",
        "心态",
        "韭菜",
        "割肉",
        "抄底",
        "踏空",
        "满仓",
        "爆仓",
        "梭哈",
    ]

    @classmethod
    def classify(cls, ocr_text: str = "", visual_summary: str = "") -> str:
        """判定图片类型"""
        combined = (ocr_text + " " + visual_summary).lower()

        chart_score = sum(1 for kw in cls.CHART_KEYWORDS if kw in combined)
        pnl_score = sum(1 for kw in cls.PNL_KEYWORDS if kw in combined)
        meme_score = sum(1 for kw in cls.MEME_KEYWORDS if kw in combined)

        if chart_score >= 2:
            return "kline_chart"
        elif pnl_score >= 2:
            return "pnl_screenshot"
        elif meme_score >= 2:
            return "meme_sticker"
        elif chart_score >= 1 or pnl_score >= 1:
            return "kline_chart" if chart_score > pnl_score else "pnl_screenshot"
        elif ocr_text and len(ocr_text) > 20:
            return "text_screenshot"
        return "general"


class ImageAnalyzer:
    """多模态图片分析器 — 双模型加权"""

    def __init__(self, use_qwen_local: bool = True, use_dots_api: bool = True):
        self.use_qwen = use_qwen_local
        self.use_dots = use_dots_api
        self.qwen_available = False
        self.dots_available = False
        self.classifier = ImageTypeClassifier()

        # 尝试初始化Qwen本地
        if use_qwen_local:
            self._init_qwen()

        # 尝试初始化dots API
        if use_dots_api:
            self._init_dots()

    def _init_qwen(self):
        """初始化Qwen3-VL 2B Ollama"""
        try:
            import requests

            resp = requests.get("http://localhost:11434/api/tags", timeout=5)
            models = [m["name"] for m in resp.json().get("models", [])]
            # 查找qwen3-vl模型
            vl_models = [m for m in models if "qwen3-vl" in m.lower() or "qwen-vl" in m.lower()]
            if vl_models:
                self.qwen_model = vl_models[0]
                self.qwen_available = True
                logger.info(f"Qwen3-VL available: {self.qwen_model}")
            else:
                logger.warning("Qwen3-VL not found in Ollama. Run: ollama pull qwen3-vl:2b")
        except Exception as e:
            logger.warning(f"Ollama not available: {e}")

    def _init_dots(self):
        """初始化dots.vlm1 — 尝试多个可用端点"""
        try:
            from gradio_client import Client

            # 尝试多个可能的space路径
            for space in [
                "rednote-hilab/dots-vlm1-demo",
                "rednote-hilab/dots.vlm1.demo",
            ]:
                try:
                    self.dots_client = Client(space)
                    self.dots_available = True
                    logger.info(f"dots.vlm1 API available: {space}")
                    return
                except Exception:
                    continue

            # HuggingFace space 不可用时的降级: 尝试 Inference API
            # 需要 HF_TOKEN 环境变量
            import os

            if os.environ.get("HF_TOKEN"):
                logger.info("dots.vlm1: using HF Inference API (requires HF_TOKEN)")
                self.dots_use_inference_api = True
                self.dots_available = True
            else:
                logger.warning(
                    "dots.vlm1: HF Space unavailable (401). "
                    "Set HF_TOKEN for Inference API, or use Qwen-only mode."
                )
        except ImportError:
            logger.warning("gradio_client not installed. pip install gradio_client")
        except Exception as e:
            logger.warning(f"dots.vlm1 API not available: {e}")

    def analyze_image(self, image_path: str, note_text: str = "") -> ImageAnalysis:
        """分析单张图片，双模型加权"""
        result = ImageAnalysis(image_url=str(image_path))

        results = []

        # Qwen分析
        if self.qwen_available:
            qwen_r = self._call_qwen(image_path, note_text)
            result.qwen_result = qwen_r
            results.append(("qwen", qwen_r))

        # dots分析
        if self.dots_available:
            dots_r = self._call_dots(image_path, note_text)
            result.dots_result = dots_r
            results.append(("dots", dots_r))

        if not results:
            result.visual_summary = "no model available"
            return result

        # 汇总OCR和描述
        ocr_parts = []
        summary_parts = []
        for model_name, r in results:
            if r.get("ocr_text"):
                ocr_parts.append(r["ocr_text"])
            if r.get("summary"):
                summary_parts.append(f"[{model_name}]: {r['summary']}")

        result.ocr_text = "\n".join(ocr_parts)
        result.visual_summary = "\n".join(summary_parts)

        # 图片类型判定
        result.image_type = self.classifier.classify(result.ocr_text, result.visual_summary)

        # 加权投票
        weights = self._get_weights(result.image_type)
        total_score = 0.0
        total_confidence = 0.0
        weight_sum = 0.0

        for model_name, r in results:
            w = weights.get(model_name, 0.5)
            total_score += r.get("sentiment_score", 0) * w
            total_confidence += r.get("confidence", 0) * w
            weight_sum += w

        if weight_sum > 0:
            result.visual_score = round(total_score / weight_sum, 3)
            result.visual_confidence = round(total_confidence / weight_sum, 3)

        result.visual_sentiment = self._score_to_sentiment(result.visual_score)
        return result

    def _get_weights(self, image_type: str) -> dict:
        """根据图片类型分配模型权重"""
        weights = {
            "kline_chart": {"qwen": 0.75, "dots": 0.25},
            "pnl_screenshot": {"qwen": 0.7, "dots": 0.3},
            "meme_sticker": {"qwen": 0.2, "dots": 0.8},
            "text_screenshot": {"qwen": 0.5, "dots": 0.5},
            "general": {"qwen": 0.5, "dots": 0.5},
        }
        return weights.get(image_type, {"qwen": 0.5, "dots": 0.5})

    def _call_qwen(self, image_path: str, context: str = "") -> dict:
        """调用本地Qwen3-VL (Ollama, GPU加速)"""
        try:
            import base64

            import requests

            with open(image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()

            prompt = _build_qwen_prompt(context)

            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": self.qwen_model,
                    "prompt": prompt,
                    "images": [img_b64],
                    "stream": False,
                    # Thinking variant needs high num_predict for reasoning+response
                    "options": {"temperature": 0.1, "num_predict": 1200},
                },
                timeout=120,  # GPU inference takes a few seconds
            )
            data = resp.json()
            return _parse_qwen_response(data.get("response", ""))
        except Exception as e:
            logger.error(f"Qwen call failed: {e}")
            return {"sentiment_score": 0, "confidence": 0, "summary": f"error: {e}"}

    def _call_dots(self, image_path: str, context: str = "") -> dict:
        """调用dots.vlm1 HF API"""
        try:
            prompt = _build_dots_prompt(context)
            result = self.dots_client.predict(
                image=image_path,
                prompt=prompt,
                api_name="/predict",
            )
            return _parse_dots_response(str(result))
        except Exception as e:
            logger.error(f"dots call failed: {e}")
            return {"sentiment_score": 0, "confidence": 0, "summary": f"error: {e}"}

    def _score_to_sentiment(self, score: float) -> str:
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


# ============================================================
# Emoji 分析器
# ============================================================


class EmojiAnalyzer:
    """Emoji情感分析"""

    def analyze(self, text: str) -> dict:
        """提取并分析文本中的Emoji"""
        emojis_found = []
        total_score = 0.0
        total_conf = 0.0

        for emoji, (sentiment, score) in EMOJI_SENTIMENT.items():
            count = text.count(emoji)
            if count > 0:
                emojis_found.append(
                    {
                        "emoji": emoji,
                        "text": EMOJI_TEXT_MAP.get(emoji, ""),
                        "count": count,
                        "sentiment": sentiment,
                        "score": score,
                    }
                )
                total_score += score * count
                total_conf += 0.6 * count  # emoji置信度默认0.6

        if emojis_found:
            avg_score = total_score / sum(e["count"] for e in emojis_found)
            avg_conf = min(1.0, total_conf / sum(e["count"] for e in emojis_found))
        else:
            avg_score = 0.0
            avg_conf = 0.0

        return {
            "emojis": emojis_found,
            "emoji_count": sum(e["count"] for e in emojis_found),
            "emoji_score": round(avg_score, 3),
            "emoji_confidence": round(avg_conf, 2),
        }


# ============================================================
# 三通道融合引擎
# ============================================================


class MultimodalSentimentEngine:
    """
    三通道情感融合:
      Channel 1: 文本情感 (现有规则引擎/LLM)
      Channel 2: 图片情感 (Qwen3-VL + dots.vlm1 双模型加权)
      Channel 3: Emoji情感 (词典映射)
    """

    def __init__(self, text_engine=None, use_qwen: bool = True, use_dots: bool = True):
        self.text_engine = text_engine  # SentimentAnalyzer or LLMSentimentEngine
        self.image_analyzer = ImageAnalyzer(use_qwen, use_dots)
        self.emoji_analyzer = EmojiAnalyzer()

        # 通道权重 (可调)
        self.channel_weights = {
            "text": 0.45,  # 文字权重
            "image": 0.40,  # 图片权重
            "emoji": 0.15,  # Emoji权重
        }

    def analyze(self, text: str = "", image_paths: list[str] = None) -> dict:
        """三通道融合分析"""
        image_paths = image_paths or []
        channels = {}

        # Channel 1: 文本
        if self.text_engine and text.strip():
            channels["text"] = self.text_engine.analyze(text)
        else:
            channels["text"] = {"sentiment": "neutral", "score": 0, "confidence": 0}

        # Channel 2: 图片
        image_results = []
        image_scores = []
        image_confs = []
        for img_path in image_paths:
            if Path(img_path).exists():
                r = self.image_analyzer.analyze_image(img_path, text)
                image_results.append(r)
                image_scores.append(r.visual_score)
                image_confs.append(r.visual_confidence)

        if image_scores:
            channels["image"] = {
                "sentiment": self.image_analyzer._score_to_sentiment(
                    sum(image_scores) / len(image_scores)
                ),
                "score": round(sum(image_scores) / len(image_scores), 3),
                "confidence": round(sum(image_confs) / len(image_confs), 3),
                "details": [
                    {
                        "image_type": r.image_type,
                        "score": r.visual_score,
                        "ocr_text": r.ocr_text[:200],
                        "summary": r.visual_summary[:200],
                    }
                    for r in image_results
                ],
            }
        else:
            channels["image"] = {"sentiment": "neutral", "score": 0, "confidence": 0, "details": []}

        # Channel 3: Emoji
        channels["emoji"] = self.emoji_analyzer.analyze(text)

        # === 融合 ===
        w = self.channel_weights
        final_score = (
            w["text"] * channels["text"].get("score", 0)
            + w["image"] * channels["image"].get("score", 0)
            + w["emoji"] * channels["emoji"].get("emoji_score", 0)
        )

        # 如果无图片，重新分配权重
        if not image_paths:
            final_score = 0.75 * channels["text"].get("score", 0) + 0.25 * channels["emoji"].get(
                "emoji_score", 0
            )

        final_confidence = max(
            channels["text"].get("confidence", 0),
            channels["image"].get("confidence", 0),
            channels["emoji"].get("emoji_confidence", 0),
        )

        final_sentiment = self.image_analyzer._score_to_sentiment(final_score)

        return {
            "sentiment": final_sentiment,
            "score": round(final_score, 3),
            "confidence": round(final_confidence, 2),
            "channels": channels,
            "weights": self.channel_weights,
        }


# ============================================================
# Prompt模板
# ============================================================


def _build_qwen_prompt(context: str = "") -> str:
    return f"""分析这张期货/股票相关的图片。请严格按以下JSON格式返回:
{{
  "ocr_text": "图片中的文字内容(如有)",
  "summary": "图片内容的简短描述(30字内)",
  "sentiment": "strong_bullish|bullish|slightly_bullish|neutral|slightly_bearish|bearish|strong_bearish",
  "sentiment_score": -1.0到1.0的浮点数,
  "confidence": 0到1的浮点数,
  "reasoning": "分析理由(20字内)"
}}

背景文字: {context[:200] if context else "无"}

只返回JSON。"""


def _build_dots_prompt(context: str = "") -> str:
    return f"""你是一个专业的金融社交媒体内容分析师。请分析这张来自小红书的图片。

图片可能包含: K线图、盈亏截图、表情包、聊天记录、分析图表。

背景文字: {context[:200] if context else "无"}

请用JSON格式回答:
{{
  "image_type": "kline_chart|pnl_screenshot|meme_sticker|text_screenshot|general",
  "ocr_text": "图片中的文字",
  "summary": "图片描述(30字)",
  "visual_sentiment": "看多/看空/中性",
  "sentiment_score": -1.0到1.0,
  "confidence": 0到1.0,
  "reasoning": "理由"
}}

只返回JSON。"""


def _parse_qwen_response(response: str) -> dict:
    """解析Qwen响应"""
    try:
        # 提取JSON
        if "```" in response:
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        data = json.loads(response.strip())
        return {
            "ocr_text": data.get("ocr_text", ""),
            "summary": data.get("summary", ""),
            "sentiment_score": float(data.get("sentiment_score", 0)),
            "confidence": float(data.get("confidence", 0.3)),
        }
    except (json.JSONDecodeError, KeyError, ValueError):
        return {"sentiment_score": 0, "confidence": 0, "summary": response[:100]}


def _parse_dots_response(response: str) -> dict:
    """解析dots响应"""
    try:
        if "```" in response:
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        data = json.loads(response.strip())
        return {
            "ocr_text": data.get("ocr_text", ""),
            "summary": data.get("summary", ""),
            "sentiment_score": float(data.get("sentiment_score", 0)),
            "confidence": float(data.get("confidence", 0.3)),
        }
    except (json.JSONDecodeError, KeyError, ValueError):
        return {"sentiment_score": 0, "confidence": 0, "summary": response[:100]}


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  多模态情感分析 — 可用性检查")
    print("=" * 60)

    # 检查Qwen本地
    print("\n[Qwen3-VL 本地]")
    try:
        import requests

        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        vl = [m for m in models if "qwen" in m.lower() or "vl" in m.lower()]
        if vl:
            print(f"  OK: {vl}")
            print("  安装命令: ollama pull qwen3-vl:2b")
        else:
            print("  Ollama可用，但未安装Qwen-VL模型")
            print("  安装: ollama pull qwen3-vl:2b")
    except Exception:
        print("  Ollama未运行。安装: https://ollama.com")
        print("  然后: ollama pull qwen3-vl:2b")

    # 检查dots API
    print("\n[dots.vlm1 HF API]")
    try:
        from gradio_client import Client

        c = Client("rednote-hilab/dots-vlm1-demo")
        print("  OK: HuggingFace Space可用")
        print("  URL: https://huggingface.co/spaces/rednote-hilab/dots-vlm1-demo")
    except ImportError:
        print("  需安装: pip install gradio_client")
    except Exception as e:
        print(f"  不可用: {e}")

    # 测试Emoji分析
    print("\n[Emoji分析测试]")
    ea = EmojiAnalyzer()
    test_text = "螺纹钢暴涨!!多头坚定持有!!铁矿暴跌!!空头哭了!!"
    # Note: actual emoji characters in data work fine, Windows console just can't print them
    r = ea.analyze(test_text)
    print(f"  输入: {test_text}")
    print(f"  Emoji数: {r['emoji_count']}, 分数: {r['emoji_score']}")

    # Test with actual emoji (from real data)
    r2 = ea.analyze("螺纹钢暴涨🚀🚀")
    print(f"  实际Emoji文本: Emoji数={r2['emoji_count']}, 分数={r2['emoji_score']}")

    # 权重示例
    print("\n[加权策略示例]")
    for img_type in ["kline_chart", "pnl_screenshot", "meme_sticker", "text_screenshot", "general"]:
        w = (
            ImageTypeClassifier.classify.__func__.__self__
            if hasattr(ImageTypeClassifier, "__func__")
            else None
        )
        weights = ImageAnalyzer()._get_weights(img_type)
        print(f"  {img_type}: Qwen={weights['qwen']:.0%}, dots={weights['dots']:.0%}")

    print("\n[三通道融合示例]")
    print("  text + image + emoji → 加权融合 (45%/40%/15%)")
    print("  无图片: text + emoji → 加权融合 (75%/25%)")
