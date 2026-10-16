# -*- coding: utf-8 -*-
"""线上端到端: 通过 HTTP 让【服务进程自己】走一次真实 LLM 调用。

这是换源验证的最后一道闸 —— 前两步(离线工厂验证)证明了代码逻辑,
本步证明"重启后的线上进程 + .env + 修复后的工厂"整条链路真的通。
"""
import json
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
out = []


def call(path, payload=None, timeout=300):
    url = f"http://127.0.0.1:5000{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    try:
        r = opener.open(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        body = ""
        if hasattr(e, "read"):
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
        return f"ERR:{type(e).__name__}", f"{e}\n{body[:600]}"


st, body = call("/api/config")
out.append(f"[1] GET /api/config -> {st}")
out.append(f"    {body[:300]}")

# 真实触发 LLM 的问答端点(会走 create_llm_client)
st, body = call("/api/research/ask", {"question": "螺纹钢最近有什么主要矛盾？", "top_k": 3})
out.append("")
out.append(f"[2] POST /api/research/ask -> {st}")
try:
    d = json.loads(body)
    ans = d.get("answer") or d.get("error") or ""
    out.append(f"    ok={d.get('ok')} rag_enabled={d.get('rag_enabled')}")
    out.append(f"    answer(前400字): {str(ans)[:400]}")
except Exception:
    out.append(f"    raw: {body[:600]}")

text = "\n".join(out)
from pathlib import Path

Path(r"D:\AgentSense\AgentSense\probe\_e2e_result.txt").write_text(text, encoding="utf-8")
print(text)
