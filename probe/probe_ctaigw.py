# -*- coding: utf-8 -*-
"""探针: 验证天翼云中转端点可用性 + 确认 base_url 正确形态 + 探测可用模型

只读取证, 不修改任何生产配置。
"""
import json
import urllib.error
import urllib.request

KEY = "e90015ceb7044b15989170290925c5e7"
FULL = "https://ai.ctaigw.cn/v1/chat/completions"
BASE = "https://ai.ctaigw.cn/v1"


def post(url, payload, timeout=30):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {KEY}",
        },
        method="POST",
    )
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {KEY}"})
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


print("=" * 74)
print("STEP 1  探测模型列表: GET /v1/models")
print("=" * 74)
st, body = get(f"{BASE}/models")
print(f"  status = {st}")
if st == 200:
    try:
        j = json.loads(body)
        ids = [m.get("id") for m in (j.get("data") or [])]
        print(f"  可用模型 {len(ids)} 个:")
        for i in range(0, len(ids), 4):
            print("    " + "  ".join(f"{x:<34}" for x in ids[i:i + 4]))
    except Exception as e:
        print("  解析失败:", e)
        print("  body[:400]:", body[:400])
else:
    print("  body[:400]:", body[:400])

print()
print("=" * 74)
print("STEP 2  chat/completions 实测(逐个试待选模型)")
print("=" * 74)
CANDIDATES = [
    "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v3", "deepseek-chat",
    "gpt-4o-mini", "glm-4-flash", "qwen-plus", "kimi-k2.5",
]
for model in CANDIDATES:
    st, body = post(FULL, {
        "model": model,
        "messages": [{"role": "user", "content": "回复两个字: 收到"}],
        "max_tokens": 20,
        "temperature": 0,
    })
    short = body[:160].replace("\n", " ")
    if st == 200:
        try:
            j = json.loads(body)
            txt = j["choices"][0]["message"]["content"]
            print(f"  [{st}] {model:<20} -> OK  回复={txt!r}")
        except Exception:
            print(f"  [{st}] {model:<20} -> 200 但解析异常: {short}")
    else:
        print(f"  [{st}] {model:<20} -> {short}")

print()
print("=" * 74)
print("STEP 3  base_url 形态确认: 带 /chat/completions 是否重复追加")
print("=" * 74)
# SDK 会在 base_url 后追加 /chat/completions; 若 base_url 已含该段则会变成双段 -> 404
st, body = post(FULL, {"model": CANDIDATES[0], "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5})
print(f"  直接 POST 完整端点 {FULL}")
print(f"    status={st}  {'可用' if st == 200 else body[:200]}")
print()
print(f"  ⇒ 结论: .env 里 TRADINGAGENTS_LLM_BACKEND_URL 应填 '{BASE}'")
print("     (SDK 自行追加 /chat/completions; 填完整端点会得到 .../v1/chat/completions/chat/completions)")
print()
print("完成")
