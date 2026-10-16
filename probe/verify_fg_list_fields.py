# -*- coding: utf-8 -*-
"""验证后端「列表接口字段与详情接口一致」的修复(2026-09-30)。

验收点:
  ① /api/foreign/jobs 的每条 must 含 has_pdf / out_name
  ② 已完成任务 has_pdf=True; 失败任务 has_pdf=False(不得把死链发给前端)
  ③ out_pdf(服务器绝对路径)**不得**出现在列表响应里(防路径外泄)
  ④ 与详情接口 /api/foreign/job/<id> 比对: 同一任务两边的 has_pdf / out_name 必须一致
     —— 这正是本次缺陷的判据(此前列表缺 has_pdf, 两条接口字段不一致)
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
out = []
checks = []


def chk(cond, label, detail=""):
    checks.append(bool(cond))
    out.append(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def get(path):
    r = opener.open(f"http://127.0.0.1:5000{path}", timeout=60)
    return json.loads(r.read().decode("utf-8", "replace"))


lst = get("/api/foreign/jobs?limit=50").get("jobs", [])
out.append(f"列表返回 {len(lst)} 条")
chk(len(lst) > 0, "列表非空")

# ① 必含字段
missing = [j["id"] for j in lst if "has_pdf" not in j or "out_name" not in j]
chk(not missing, "每条都含 has_pdf / out_name", f"缺失: {missing}")

# ③ 不得外泄服务器路径
leaked = [j["id"] for j in lst if "out_pdf" in j or "file_path" in j]
chk(not leaked, "不下发 out_pdf / file_path(服务器路径)", f"外泄: {leaked}")
raw = json.dumps(lst, ensure_ascii=False)
chk("AgentSense" not in raw and "D:\\" not in raw, "响应体内无服务器绝对路径痕迹")

# ② 终态任务的 has_pdf 必须与 status 自洽
done = [j for j in lst if j.get("status") == "done"]
bad_done = [j["id"] for j in done if not j.get("has_pdf")]
chk(not bad_done, "已完成任务 has_pdf=True", f"异常: {bad_done}")
err = [j for j in lst if j.get("status") == "error"]
bad_err = [j["id"] for j in err if j.get("has_pdf")]
chk(not bad_err, "失败任务 has_pdf=False(不会产生死链)", f"异常: {bad_err}")

# ④ 与详情接口逐条比对
out.append("")
out.append("  逐条比对列表 vs 详情:")
mismatch = []
for j in lst[:8]:
    try:
        det = get(f"/api/foreign/job/{j['id']}")
    except Exception as e:
        out.append(f"    {j['id']} 详情读取失败: {e}")
        continue
    same = (bool(det.get("has_pdf")) == bool(j.get("has_pdf"))) and \
           ((det.get("out_name") or "") == (j.get("out_name") or ""))
    if not same:
        mismatch.append(j["id"])
    out.append(f"    {j['id']}  status={j.get('status'):8s} "
               f"列表 has_pdf={j.get('has_pdf')!s:5s} 详情 has_pdf={det.get('has_pdf')!s:5s} "
               f"{'OK' if same else '<<< 不一致'}")
chk(not mismatch, "两接口 has_pdf/out_name 完全一致(本次缺陷判据)", f"不一致: {mismatch}")

out.append("")
out.append("=" * 70)
out.append(f"结果: {sum(checks)} 通过 / {len(checks) - sum(checks)} 失败")
out.append("=" * 70)

text = "\n".join(out)
(ROOT / "probe" / "_fgl.txt").write_text(text, encoding="utf-8")
print(text)
sys.exit(0 if all(checks) else 1)
