"""
Image analysis pipeline (dual mode)
fast: rule-based, ~1s/image, 291 imgs ~5min
deep: Granite OCR + qwen2.5vl visual, ~30s/image, ~90min
Usage:
  python image_pipeline_v2.py data.jsonl                  # default deep
  python image_pipeline_v2.py data.jsonl --mode fast      # quick & rough
  python image_pipeline_v2.py data.jsonl --max-images 50
"""

import json, os, time, hashlib, io, base64, argparse, sys, re
from pathlib import Path
from datetime import datetime
from typing import Optional
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

IMAGE_CACHE_DIR = Path(__file__).parent / "output" / "images"
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_URL = "http://localhost:11434/api/generate"
FAST_MODEL = "granite3.2-vision:2b"
DEEP_MODEL = "qwen2.5vl:3b"

CLASSIFY_PROMPT = "What type of image is this? Answer with ONE WORD: chart, screenshot, meme, or text."
OCR_PROMPT = "Extract all visible text from this image. Output only the text, nothing else."
DEEP_PROMPT = 'Analyze this futures image. JSON: {"sentiment":"bullish/bearish/neutral","sentiment_score":-1to1,"description":"brief"}. Only JSON.'


# ============================================================
# Image download & preparation
# ============================================================

def download_image(url: str, note_id: str, img_idx: int, timeout: int = 30) -> Optional[str]:
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    cache_path = IMAGE_CACHE_DIR / f"{note_id}_{img_idx}_{url_hash}.jpg"
    if cache_path.exists():
        return str(cache_path)
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Referer": "https://www.xiaohongshu.com/"}
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and len(resp.content) > 1000:
            cache_path.write_bytes(resp.content)
            return str(cache_path)
    except Exception:
        pass
    return None


def prepare_image(image_path: str, max_size: int = 640):
    img = Image.open(image_path)
    w, h = img.size
    fmt = img.format
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    if w < 50 or h < 50:
        return None, 0, 0, fmt
    png_buf = io.BytesIO()
    img.convert("RGB").save(png_buf, format="PNG")
    return base64.b64encode(png_buf.getvalue()).decode(), img.size[0], img.size[1], fmt


# ============================================================
# Ollama helpers
# ============================================================

def call_ollama(model: str, prompt: str, image_b64: str,
                num_predict: int = 300, timeout: int = 120) -> dict:
    resp = requests.post(OLLAMA_URL, json={
        "model": model, "prompt": prompt,
        "images": [image_b64], "stream": False,
        "options": {"temperature": 0.1, "num_predict": num_predict}
    }, timeout=timeout)
    return resp.json()


def parse_json_response(response_text: str) -> Optional[dict]:
    if not response_text:
        return None
    cleaned = response_text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        cleaned = parts[1] if len(parts) > 1 else cleaned
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    s, e = cleaned.find("{"), cleaned.rfind("}") + 1
    if s >= 0 and e > s:
        try:
            return json.loads(cleaned[s:e])
        except json.JSONDecodeError:
            pass
    return None


# ============================================================
# Stage 1: classify + OCR (Granite, merged call)
# ============================================================

def stage1_classify_and_ocr(image_path: str) -> dict:
    img_b64, w, h, fmt = prepare_image(image_path, max_size=640)

    # classify
    data1 = call_ollama(FAST_MODEL, CLASSIFY_PROMPT, img_b64, num_predict=20, timeout=60)
    raw_type = (data1.get("response", "") or "").strip().lower()

    # normalize
    image_type = "general"
    if "chart" in raw_type or "kline" in raw_type or "graph" in raw_type:
        image_type = "kline_chart"
    elif "pnl" in raw_type or "profit" in raw_type or "loss" in raw_type:
        image_type = "pnl_screenshot"
    elif "meme" in raw_type or "sticker" in raw_type:
        image_type = "meme_sticker"
    elif "screenshot" in raw_type or "text" in raw_type or "document" in raw_type:
        image_type = "text_screenshot"

    # OCR
    data2 = call_ollama(FAST_MODEL, OCR_PROMPT, img_b64, num_predict=400, timeout=90)
    ocr_text = (data2.get("response", "") or "").strip()

    return {
        "image_type": image_type,
        "ocr_text": ocr_text,
        "image_size": f"{w}x{h}",
        "raw_classification": raw_type,
    }


# ============================================================
# Stage 2: deep visual analysis (qwen2.5vl:3b, 480px)
# ============================================================

def stage2_deep_analysis(image_path: str) -> dict:
    img_b64, w, h, fmt = prepare_image(image_path, max_size=480)
    if img_b64 is None:
        return {"sentiment": "neutral", "sentiment_score": 0.0, "confidence": 0.0,
                "description": "image too small", "analyzed": False}

    t0 = time.time()
    data = call_ollama(DEEP_MODEL, DEEP_PROMPT, img_b64, num_predict=500, timeout=120)
    elapsed = time.time() - t0

    result = parse_json_response(data.get("response", ""))
    if result:
        result["analyzed"] = True
        result["model"] = DEEP_MODEL
    else:
        result = {"sentiment": "neutral", "sentiment_score": 0.0,
                  "confidence": 0.1, "description": "parse_failed"}
    result["image_size"] = f"{w}x{h}"
    result["analyzed_at"] = datetime.now().isoformat()
    result["deep_time"] = round(elapsed, 1)
    return result


# ============================================================
# Text sentiment (reuse existing engine)
# ============================================================

def text_sentiment_analysis(ocr_text: str, note_desc: str = "") -> dict:
    try:
        from sentiment import SentimentAnalyzer
        sa = SentimentAnalyzer()
        combined = f"{note_desc} {ocr_text}".strip()
        if combined:
            r = sa.analyze(combined)
            return {"sentiment": r["sentiment"], "sentiment_score": r["score"],
                    "confidence": r["confidence"], "analyzed": True, "model": "text_engine"}
    except ImportError:
        pass
    return {"sentiment": "neutral", "sentiment_score": 0.0, "confidence": 0.0}


# ============================================================
# FAST mode: rule-based, zero API calls
# ============================================================

def fast_analyze_image(image_path: str, note_desc: str = "") -> dict:
    try:
        img = Image.open(image_path)
        w, h = img.size
        aspect = w / h if h > 0 else 1

        # type from geometry
        if aspect > 2.5 or aspect < 0.4:
            image_type = "kline_chart"
        elif aspect > 1.8:
            image_type = "pnl_screenshot"
        elif w < 300 and h < 300:
            image_type = "meme_sticker"
        else:
            image_type = "text_screenshot"

        # sentiment from color
        img_small = img.resize((50, 50))
        pixels = list(img_small.getdata())
        red_score = sum(1 for p in pixels if p[0] > 180 and p[1] < 100 and p[2] < 100)
        green_score = sum(1 for p in pixels if p[0] < 100 and p[1] > 150 and p[2] < 100)

        if red_score > green_score * 1.5:
            color_sentiment, color_score = "bearish", -0.3
        elif green_score > red_score * 1.5:
            color_sentiment, color_score = "bullish", 0.3
        else:
            color_sentiment, color_score = "neutral", 0.0

        # text sentiment
        try:
            from sentiment import SentimentAnalyzer
            sa = SentimentAnalyzer()
            text_r = sa.analyze(note_desc) if note_desc else {"sentiment": "neutral", "score": 0}
        except ImportError:
            text_r = {"sentiment": "neutral", "score": 0}

        # fusion: text 70% + color 30%
        final_score = round(text_r["score"] * 0.7 + color_score * 0.3, 2)
        if final_score > 0.3: final_sent = "bullish"
        elif final_score > 0.1: final_sent = "slightly_bullish"
        elif final_score < -0.3: final_sent = "bearish"
        elif final_score < -0.1: final_sent = "slightly_bearish"
        else: final_sent = "neutral"

        return {
            "image_type": image_type, "sentiment": final_sent,
            "sentiment_score": final_score, "confidence": 0.3,
            "description": f"{image_type} | {w}x{h} | aspect={aspect:.1f}",
            "route": "fast", "model": "heuristics", "analyzed": True,
            "color_sentiment": color_sentiment, "color_score": color_score,
            "text_sentiment": text_r["sentiment"], "text_score": text_r["score"],
        }
    except Exception as e:
        return {"sentiment": "neutral", "sentiment_score": 0, "confidence": 0,
                "description": f"error: {e}", "route": "fast"}


# ============================================================
# Merge results
# ============================================================

def merge_results(note: dict, image_results: list) -> dict:
    scores = [r.get("sentiment_score", 0) for r in image_results if r.get("analyzed")]
    img_score = round(sum(scores) / len(scores), 3) if scores else 0.0
    note["image_analysis"] = {
        "total_images": len(image_results),
        "avg_score": img_score,
        "results": image_results,
    }
    text_score = note.get("sentiment_score", 0) or 0
    fused = text_score * 0.55 + img_score * 0.45 if scores else text_score
    note["image_analysis"]["fused_score"] = round(fused, 3)
    return note


# ============================================================
# Main pipeline
# ============================================================

def run_pipeline_v2(jsonl_path: str, max_images: int = None,
                    start_from: int = 0, mode: str = "deep"):
    notes = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                notes.append(json.loads(line))
    if start_from > 0:
        notes = notes[start_from:]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(jsonl_path).parent / f"multimodal_v2_{ts}.jsonl"

    print(f"{'='*60}")
    print(f"  Image Analysis Pipeline ({mode.upper()} mode)")
    print(f"{'='*60}")
    if mode == "fast":
        print(f"  Method: rule-based (geometry + color + text)")
    else:
        print(f"  Stage1: {FAST_MODEL} (classify+OCR)")
        print(f"  Stage2: {DEEP_MODEL} (visual sentiment)")
    print(f"  Notes: {len(notes)}, Max images: {max_images or 'all'}")
    print()

    stats = {"stage1": 0, "stage2": 0, "text_route": 0,
             "deep_route": 0, "downloaded": 0, "total": 0}
    start_time = time.time()

    with open(out_path, "w", encoding="utf-8") as f_out:
        for note_idx, note in enumerate(notes):
            image_urls = note.get("image_urls", []) or []
            note_id = note.get("note_id", f"n{note_idx}")
            note_desc = note.get("desc", "") or ""

            if not image_urls:
                note["image_analysis"] = {"total_images": 0}
                f_out.write(json.dumps(note, ensure_ascii=False) + "\n")
                continue
            if max_images and stats["total"] >= max_images:
                note["image_analysis"] = {"skipped": True}
                f_out.write(json.dumps(note, ensure_ascii=False) + "\n")
                continue

            print(f"\n[{note_idx+1}/{len(notes)}] {note_id[:12]}... ({len(image_urls)} imgs)")

            image_results = []
            for i, url in enumerate(image_urls[:5]):
                if not url or (max_images and stats["total"] >= max_images):
                    continue

                local_path = download_image(url, note_id, i)
                if not local_path:
                    continue
                stats["downloaded"] += 1
                stats["total"] += 1

                # === FAST mode ===
                if mode == "fast":
                    t0 = time.time()
                    result = fast_analyze_image(local_path, note_desc)
                    t = time.time() - t0
                    print(f"  Img{i+1}: {result['image_type']} {t:.1f}s | "
                          f"{result['sentiment']} {result['sentiment_score']:+.1f} (conf={result['confidence']:.1f})")
                    image_results.append(result)
                    continue

                # === DEEP mode ===
                print(f"  Img{i+1}: S1...", end=" ", flush=True)
                t0 = time.time()
                s1 = stage1_classify_and_ocr(local_path)
                s1_time = time.time() - t0
                stats["stage1"] += 1

                img_type = s1["image_type"]
                ocr_text = s1["ocr_text"]
                result = {"image_type": img_type, "ocr_text": ocr_text}

                if img_type in ("text_screenshot", "general"):
                    print(f"text({len(ocr_text)}c) {s1_time:.0f}s", end=" ", flush=True)
                    ts = text_sentiment_analysis(ocr_text, note_desc)
                    result.update(ts)
                    result["route"] = "text"
                    stats["text_route"] += 1
                else:
                    print(f"{img_type} {s1_time:.0f}s -> S2...", end=" ", flush=True)
                    t1 = time.time()
                    s2 = stage2_deep_analysis(local_path)
                    s2_time = time.time() - t1
                    result.update(s2)
                    result["route"] = "deep"
                    result["deep_time"] = round(s2_time, 1)
                    stats["deep_route"] += 1
                    stats["stage2"] += 1
                    print(f"{s2_time:.0f}s", end=" ", flush=True)

                sent = result.get("sentiment", "?")
                score = result.get("sentiment_score", 0)
                conf = result.get("confidence", 0)
                print(f"| {sent} {score:+.1f} (conf={conf:.1f})")

                image_results.append(result)

            note = merge_results(note, image_results)
            f_out.write(json.dumps(note, ensure_ascii=False) + "\n")
            f_out.flush()

            elapsed = time.time() - start_time
            if stats["total"] > 0:
                avg = elapsed / stats["total"]
                remaining = (max_images or 999) - stats["total"]
                print(f"  [{stats['text_route']} text | {stats['deep_route']} deep] "
                      f"ETA: {max(0, remaining * avg / 60):.0f}min")

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"  Complete: {stats['total']} imgs in {elapsed/60:.0f}min")
    print(f"  Text: {stats['text_route']}, Deep: {stats['deep_route']}")
    print(f"  Output: {out_path}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Image analysis pipeline (dual mode)")
    parser.add_argument("input", help="JSONL with image_urls")
    parser.add_argument("--mode", choices=["fast", "deep"], default="deep")
    parser.add_argument("--max-images", type=int, default=None)
    parser.add_argument("--start-from", type=int, default=0)
    args = parser.parse_args()
    run_pipeline_v2(args.input, args.max_images, args.start_from, args.mode)


if __name__ == "__main__":
    main()
