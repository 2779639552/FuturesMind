"""
图片下载 + Qwen3-VL 批量多模态分析
====================================
流程:
  1. 从JSONL读取notes (含image_urls字段)
  2. 下载图片到本地缓存
  3. Qwen3-VL 2B 逐张分析 (GPU加速)
  4. 结果合并回note, 输出enriched JSONL

使用:
  python image_pipeline.py output/batch_xxx.jsonl
  python image_pipeline.py data.jsonl --max-images 50 --no-download
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

# Add self to path
sys.path.insert(0, str(Path(__file__).parent))

IMAGE_CACHE_DIR = Path(__file__).parent / "output" / "images"
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_URL = "http://localhost:11434/api/generate"
QWEN_MODEL = "granite3.2-vision:2b"  # Fast non-thinking VL model (8.5s vs 56s for qwen3-vl)


# ============================================================
# 图片下载
# ============================================================


def download_image(
    url: str, note_id: str, img_idx: int, cookies: dict = None, timeout: int = 30
) -> str | None:
    """下载单张图片，返回本地路径。失败返回None。"""
    # 用URL hash做缓存key
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    cache_name = f"{note_id}_{img_idx}_{url_hash}.jpg"
    cache_path = IMAGE_CACHE_DIR / cache_name

    if cache_path.exists():
        return str(cache_path)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.xiaohongshu.com/",
        }
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and len(resp.content) > 1000:
            cache_path.write_bytes(resp.content)
            return str(cache_path)
    except Exception:
        pass
    return None


# ============================================================
# Qwen3-VL 分析
# ============================================================

ANALYSIS_PROMPT = """Analyze this futures trading image from Xiaohongshu. Output ONLY a JSON object, no other text.

Choose ONE image_type from: kline_chart, pnl_screenshot, meme_sticker, text_screenshot, general
Choose ONE sentiment from: bullish, bearish, neutral
sentiment_score must match sentiment: bullish=0.3~1.0, bearish=-1.0~-0.3, neutral=-0.2~0.2

{
  "image_type": "your_choice",
  "description": "brief Chinese description",
  "ocr_text": "text in image",
  "sentiment": "your_choice",
  "sentiment_score": number,
  "confidence": number,
  "key_info": "key trading insight"
}"""


def analyze_image(image_path: str, timeout: int = 180) -> dict:
    """Qwen3-VL分析单张图片"""
    import base64

    if not os.path.exists(image_path):
        return {"error": "file not found", "image_path": image_path}

    try:
        # 检查图片有效性, 压缩大图, 转为PNG (Ollama不支持WEBP)
        img = Image.open(image_path)
        width, height = img.size
        img_format = img.format

        # 压缩大图: max 640px on longest side (平衡速度和质量)
        max_size = 640
        if max(width, height) > max_size:
            ratio = max_size / max(width, height)
            new_size = (int(width * ratio), int(height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        # 转为PNG bytes
        import io

        png_buf = io.BytesIO()
        img.convert("RGB").save(png_buf, format="PNG")
        png_bytes = png_buf.getvalue()
        img_b64 = base64.b64encode(png_bytes).decode()

        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": QWEN_MODEL,
                "prompt": ANALYSIS_PROMPT,
                "images": [img_b64],
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 800,  # Granite non-thinking: 500-800 tokens enough for JSON
                },
            },
            timeout=timeout,
        )

        data = resp.json()
        response_text = data.get("response", "")

        # 解析JSON — 增强容错
        try:
            # 清理markdown包裹
            cleaned = response_text.strip()
            if "```" in cleaned:
                parts = cleaned.split("```")
                cleaned = parts[1] if len(parts) > 1 else cleaned
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
                cleaned = cleaned.strip()

            # 提取JSON对象 (处理模型偶尔在JSON后加解释的情况)
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                cleaned = cleaned[start:end]

            result = json.loads(cleaned)
            result["image_path"] = image_path
            result["image_size"] = f"{width}x{height}"
            result["image_format"] = img_format
            result["analyzed_at"] = datetime.now().isoformat()
            return result

        except json.JSONDecodeError:
            return {
                "image_path": image_path,
                "raw_response": response_text[:500],
                "image_type": "general",
                "sentiment": "neutral",
                "sentiment_score": 0.0,
                "confidence": 0.1,
                "parse_error": True,
            }

    except requests.exceptions.Timeout:
        return {"error": "timeout", "image_path": image_path}
    except Exception as e:
        return {"error": str(e), "image_path": image_path}


# ============================================================
# 合并分析结果到note
# ============================================================


def merge_image_analysis(note: dict, image_results: list[dict]) -> dict:
    """将图片分析结果合并到note"""
    scores = []
    confidences = []
    types = []
    descriptions = []
    key_infos = []

    for r in image_results:
        if "error" in r or r.get("parse_error"):
            continue
        score = r.get("sentiment_score", 0)
        if isinstance(score, str):
            try:
                score = float(score)
            except ValueError:
                score = 0
        scores.append(score)
        confidences.append(r.get("confidence", 0))
        types.append(r.get("image_type", "unknown"))
        descriptions.append(r.get("description", ""))
        key_infos.append(r.get("key_info", ""))

    note["image_analysis"] = {
        "total_images": len(image_results),
        "analyzed_images": len(scores),
        "image_types": types,
        "image_sentiment_scores": scores,
        "image_avg_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "image_avg_confidence": round(sum(confidences) / len(confidences), 3)
        if confidences
        else 0.0,
        "image_descriptions": descriptions,
        "image_key_infos": key_infos,
        "results": image_results,
    }

    # 三通道融合: 文本 + 图片 + Emoji
    text_score = note.get("sentiment_score", 0) or 0
    img_score = note["image_analysis"]["image_avg_score"]
    img_count = note["image_analysis"]["analyzed_images"]

    if img_count > 0:
        # 图片越多, 权重越高 (最多40%)
        img_weight = min(0.4, 0.15 + img_count * 0.05)
        text_weight = 1.0 - img_weight
        fused_score = text_score * text_weight + img_score * img_weight
    else:
        fused_score = text_score

    note["image_analysis"]["fused_sentiment_score"] = round(fused_score, 3)
    note["image_analysis"]["fusion_weights"] = {
        "text": round(text_weight, 2) if img_count > 0 else 1.0,
        "image": round(img_weight, 2) if img_count > 0 else 0.0,
    }

    # Fused sentiment label
    fs = fused_score
    if fs >= 0.6:
        note["image_analysis"]["fused_sentiment"] = "strong_bullish"
    elif fs >= 0.3:
        note["image_analysis"]["fused_sentiment"] = "bullish"
    elif fs >= 0.1:
        note["image_analysis"]["fused_sentiment"] = "slightly_bullish"
    elif fs > -0.1:
        note["image_analysis"]["fused_sentiment"] = "neutral"
    elif fs > -0.3:
        note["image_analysis"]["fused_sentiment"] = "slightly_bearish"
    elif fs > -0.6:
        note["image_analysis"]["fused_sentiment"] = "bearish"
    else:
        note["image_analysis"]["fused_sentiment"] = "strong_bearish"

    return note


# ============================================================
# 主流程
# ============================================================


def run_pipeline(
    jsonl_path: str, max_images: int = None, no_download: bool = False, start_from: int = 0
):
    """主流程: 下载图片 → Qwen3-VL分析 → 合并结果"""

    # 读JSONL
    notes = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                notes.append(json.loads(line))

    if start_from > 0:
        notes = notes[start_from:]
        print(f"从第{start_from}条开始, 剩余{len(notes)}条")

    print(f"{'=' * 60}")
    print("  图片下载 + Qwen3-VL 多模态分析")
    print(f"{'=' * 60}")
    print(f"  输入: {jsonl_path}")
    print(f"  笔记数: {len(notes)}")
    print(f"  最大图片数: {max_images or '无限制'}")
    print(f"  下载模式: {'跳过' if no_download else '下载'}")
    print(f"  分析引擎: {QWEN_MODEL} (本地GPU)")
    print()

    # 统计
    total_images = 0
    total_downloaded = 0
    total_analyzed = 0
    start_time = time.time()

    # 输出文件
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(jsonl_path).parent / f"multimodal_{ts}.jsonl"

    with open(out_path, "w", encoding="utf-8") as f_out:
        for note_idx, note in enumerate(notes):
            image_urls = note.get("image_urls", []) or []
            note_id = note.get("note_id", f"unknown_{note_idx}")

            if not image_urls:
                note["image_analysis"] = {"total_images": 0, "results": []}
                f_out.write(json.dumps(note, ensure_ascii=False) + "\n")
                continue

            if max_images and total_images >= max_images:
                note["image_analysis"] = {"total_images": 0, "skipped": True}
                f_out.write(json.dumps(note, ensure_ascii=False) + "\n")
                continue

            print(
                f"\n[{note_idx + 1}/{len(notes)}] Note {note_id[:12]}... ({len(image_urls)} images)"
            )

            # Step 1: Download
            local_paths = []
            if not no_download:
                for i, url in enumerate(image_urls):
                    if not url:
                        continue
                    local = download_image(url, note_id, i)
                    if local:
                        local_paths.append(local)
                        total_downloaded += 1
                    else:
                        print(f"  Image {i + 1}: download FAILED")

                print(f"  Downloaded: {len(local_paths)}/{len(image_urls)}")
            else:
                # Use cached images
                for i, url in enumerate(image_urls):
                    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
                    cache_path = IMAGE_CACHE_DIR / f"{note_id}_{i}_{url_hash}.jpg"
                    if cache_path.exists():
                        local_paths.append(str(cache_path))

                print(f"  Cached: {len(local_paths)}/{len(image_urls)}")

            # Step 2: Analyze with Qwen3-VL
            image_results = []
            if local_paths:
                # 限制每篇笔记分析最多5张图（避免太长）
                for local_path in local_paths[:5]:
                    if max_images and total_images >= max_images:
                        break

                    print(f"  Analyzing: {Path(local_path).name[:40]}...", end=" ", flush=True)
                    t0 = time.time()
                    result = analyze_image(local_path)
                    elapsed = time.time() - t0
                    total_images += 1
                    total_analyzed += 1

                    if "error" in result:
                        print(f"ERROR: {result['error'][:40]} ({elapsed:.1f}s)")
                    elif result.get("parse_error"):
                        print(f"PARSE_ERR ({elapsed:.1f}s)")
                    else:
                        print(
                            f"{result.get('image_type', '?')} "
                            f"| {result.get('sentiment', '?')} "
                            f"| score={result.get('sentiment_score', 0)} "
                            f"| conf={result.get('confidence', 0):.2f} "
                            f"({elapsed:.1f}s)"
                        )

                    image_results.append(result)
                    time.sleep(0.5)  # 小间隔防止过热

            # Step 3: Merge
            note = merge_image_analysis(note, image_results)
            f_out.write(json.dumps(note, ensure_ascii=False) + "\n")
            f_out.flush()

            # Progress
            elapsed = time.time() - start_time
            if total_analyzed > 0:
                avg_time = elapsed / total_analyzed
                remaining = (max_images or 999) - total_images
                eta = max(0, remaining * avg_time)
                print(f"  Progress: {total_analyzed} imgs analyzed, ETA: {eta / 60:.0f}min")

    # Summary
    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print("  Pipeline Complete")
    print(f"{'=' * 60}")
    print(f"  Images downloaded: {total_downloaded}")
    print(f"  Images analyzed: {total_analyzed}")
    print(f"  Time: {elapsed / 60:.1f} min")
    print(f"  Speed: {total_analyzed / elapsed * 60:.0f} images/min")
    print(f"  Output: {out_path}")
    print(f"{'=' * 60}")

    return str(out_path)


def main():
    parser = argparse.ArgumentParser(description="图片下载 + Qwen3-VL多模态分析")
    parser.add_argument("input", help="JSONL数据文件 (含image_urls字段)")
    parser.add_argument("--max-images", type=int, default=None, help="最多分析图片数 (默认全部)")
    parser.add_argument("--no-download", action="store_true", help="跳过下载 (使用缓存)")
    parser.add_argument("--start-from", type=int, default=0, help="从第N条笔记开始")
    args = parser.parse_args()

    run_pipeline(
        args.input,
        max_images=args.max_images,
        no_download=args.no_download,
        start_from=args.start_from,
    )


if __name__ == "__main__":
    main()
