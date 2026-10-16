# 端到端验证：修复 no_thinking 后，长文件全流程要多久 + 归纳/翻译质量是否保持
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')

import foreign_report as fr

PDF = Path(r'D:\AgentSense\AgentSense\data\foreign\uploads\169e427adab5475c.pdf')
jid = fr.start_job(PDF, 'OPR_fast_e2e.pdf', {"include_images": True, "include_insight": True})
print(f"jid={jid}", flush=True)

marks = {}
t0 = time.time()
last = None
while time.time() - t0 < 2400:
    j = fr.get_job(jid) or {}
    msg = f"[{j.get('stage_label')}] {j.get('progress')}% {j.get('message')}"
    if msg != last:
        print(f"  +{time.time()-t0:6.0f}s {msg}", flush=True)
        last = msg
    # 记录各阶段首次出现时刻 → 算出每阶段耗时
    st = j.get('stage')
    if st and st not in marks:
        marks[st] = time.time() - t0
    if j.get('status') in ('done', 'error'):
        break
    time.sleep(3)

j = fr.get_job(jid) or {}
total = time.time() - t0
print(f"\n=== 阶段耗时（对比修复前长文件 2620s）===")
order = ['extract', 'analyze', 'translate', 'compose', 'render']
for i, st in enumerate(order):
    if st not in marks:
        continue
    nxt = next((marks[o] for o in order[i + 1:] if o in marks), total)
    print(f"  {st:10} {marks[st]:6.0f}s → {nxt:6.0f}s   (耗时 {nxt-marks[st]:.0f}s)")
print(f"\n总耗时: {total:.0f}s   (修复前: 2620s → 提速 {2620/total:.1f}x)")

res = {
    "jid": jid, "status": j.get('status'), "analyze_mode": j.get('analyze_mode'),
    "chunks": j.get('chunks'), "elapsed_s": round(total),
    "warnings": j.get('warnings') or [], "error": j.get('error'),
    "out_files": sorted(p.name for p in (fr.OUT_DIR / jid).iterdir()),
}
print(json.dumps({k: v for k, v in res.items() if k != 'out_files'}, ensure_ascii=False, indent=1))

# 质量：尾部标记（原文档页 47-54，截尾看不到）
html = (fr.OUT_DIR / jid / '_compile.html').read_text(encoding='utf-8')
TM = ['-3.35', '27.04', '40.46', '59.59', '67.15', '890.000', '-7.35', 'ASCI', 'Agbami']
hit = [m for m in TM if m in html]
print(f"\n尾部标记命中 {len(hit)}/{len(TM)}: {hit}")
print("旧截断文案(应为 False):", '只覆盖前' in html)
