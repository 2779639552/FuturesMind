import json, os, sys, urllib.request

BASE = "http://127.0.0.1:5000"

def post(path, body, timeout=600):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

# 1) RAG 索引兜底补齐
r = post("/api/research/rag/index", {"since_days": 3})
print("索引补齐:", r)
