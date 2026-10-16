"""重建技能覆盖率审计(分层口径)。

【为什么重建】旧 _skill_coverage_audit.json 算法错误: 对 65 张卡全部标记"缺口",
连 cap-value-drive / cap-stop-loss 这类显然已接入的也标缺口 —— 判据写错, 结论不可用。

【为什么分层 —— 单一数字会骗人】同一张卡"被提及"与"真产出读数"是两回事:
  A 层(结构化输出契约): `'card': 'x.cap-y'` 或 `cards.append("x.cap-y")`
        → 机器可读, 前端能直接列进"本次用到的方法论卡"。
  B 层(注入块显式署名): 注入给分析师的提示块里以 "◆ ... cap-y" 署名
        → 分析师能看见并据此引用, 但不进前端卡片列表(性质是"提示词在场")。
  C 层(仅注释/docstring): 提了但不产生任何读数 → **不算接入**(旧审计误把 C 当 A)。

【用法】python _rebuild_coverage_audit.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILLS_DIR = Path.home() / ".workbuddy" / "skills"
CODE_FILES = ["quant_methodology.py", "quant_verdict.py",
              "tradingagents/dataflows/methodology_context.py"]
SKILL_DIRS = {
    "Carver": "carver-systematic-futures",
    "多空逻辑": "qihuo-duokong-logic",
    "墨菲": "murphy-technical-analysis",
    "Harris": "harris-execution",
    "鹿希武": "luxiwu-trend-trading",
}
# 【为什么必须带前缀判定】`money-management` 在墨菲与鹿希武里**同名不同卡**:
#   墨菲版=组合层限额, 鹿希武版=单笔层 30%/10次。若只按 slug 名判定, 代码只接了
#   鹿希武版时会把墨菲版误报为"已接入" —— 这正是项目里"跨书 slug 必须带书前缀"
#   那条教训的同款陷阱。故判定键为 (书前缀, slug) 二元组。
PREFIX_BY_SKILL = {
    "Carver": "carver", "多空逻辑": "duokong", "墨菲": "murphy",
    "Harris": "harris", "鹿希武": "luxiwu",
}

# A 层: card 字段 / cards.append 的字符串实参。值可能是合并署名(如
# "'duokong.cap-term-structure + cap-term-structure-warrant'"), 故先取整串再抽 slug。
A_STR_PAT = re.compile(r"""(?:['"]card['"]\s*:\s*|cards\.append\(\s*)['"]([^'"]+)['"]""")
SLUG_PAT = re.compile(r"cap-([a-z][a-z-]*[a-z])")


_CN_PREFIX = {"鹿希武": "luxiwu", "墨菲": "murphy", "多空逻辑": "duokong",
              "Carver": "carver", "Harris": "harris"}
# 代码里的长前缀归一到短前缀(同一本书两种写法都出现过)
_PREFIX_ALIAS = {"qihuo-duokong-logic": "duokong", "duokong": "duokong",
                 "luxiwu": "luxiwu", "murphy": "murphy", "carver": "carver",
                 "harris": "harris", "m urphy": "murphy"}
_CAP_ONLY = re.compile(r"cap-[a-z][a-z-]*[a-z]")


def _pairs_in(text: str, default_prefix: str | None = None) -> set[tuple[str, str]]:
    """从一段文本里抽 (书前缀, slug)。

    【三态前缀】`duokong.cap-x`(点) / `鹿希武 cap-x`(空格) / 裸 `cap-x`。
    裸 slug 的归属: ① 若同串里出现了**唯一**显式前缀, 继承之(合并署名场景, 如
      `'duokong.cap-term-structure + cap-term-structure-warrant'`);
      ② 否则用 default_prefix; ③ 都无 → `''`(归属未知, 交给 _resolve 按唯一拥有者判定)。
    """
    explicit: set[str] = set()
    bare: set[str] = set()
    # 点前缀: 如 duokong.cap-value-drive / qihuo-duokong-logic.cap-...
    for m in re.finditer(r"(?:([a-z][a-z-]*)\.)?(cap-[a-z][a-z-]*[a-z])", text):
        pre = m.group(1)
        if pre:
            explicit.add(_PREFIX_ALIAS.get(pre, pre))
        else:
            bare.add(m.group(2))
    # 空格前缀: 如 "鹿希武 cap-pivot-line" / "Carver cap-cost-speed"
    for cn, pre in _CN_PREFIX.items():
        for m in re.finditer(re.escape(cn) + r"\s+(cap-[a-z][a-z-]*[a-z])", text):
            explicit.add(pre)
            bare.discard(m.group(1))
    out: set[tuple[str, str]] = set()
    for m in re.finditer(r"([a-z][a-z-]*)\.(cap-[a-z][a-z-]*[a-z])", text):
        out.add((_PREFIX_ALIAS.get(m.group(1), m.group(1)), m.group(2)))
    for cn, pre in _CN_PREFIX.items():
        for m in re.finditer(re.escape(cn) + r"\s+(cap-[a-z][a-z-]*[a-z])", text):
            out.add((pre, m.group(1)))
    # 裸 slug 的归属
    if bare:
        if len(explicit) == 1:
            only = next(iter(explicit))
            out |= {(only, s) for s in bare}
        else:
            out |= {(default_prefix or "", s) for s in bare}
    return out


def _scan() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """返回 (A 层 (书前缀,slug) 集, B 层 (书前缀,slug) 集)。"""
    tier_a: set[tuple[str, str]] = set()
    tier_b: set[tuple[str, str]] = set()
    for rel in CODE_FILES:
        p = ROOT / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in A_STR_PAT.finditer(text):
            for pre, slug in _pairs_in(m.group(1)):
                tier_a.add((pre, slug))
        if rel.endswith("methodology_context.py"):
            # B 层: 注入块的显示行(以 ◆ 开头的那几行)里显式署名的卡
            for line in text.splitlines():
                if "◆" in line:
                    for pre, slug in _pairs_in(line, default_prefix=None):
                        # 注入块行里前缀三态: "鹿希武 cap-x"(空格) / "carver.cap-x"(点) / 裸 "cap-x"
                        tier_b.add((pre, slug))
    return tier_a, tier_b - tier_a   # B 层只留"未进 A 层"的, 避免重复计数


def _all_slugs() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for cn, dirname in SKILL_DIRS.items():
        d = SKILLS_DIR / dirname / "references" / "capabilities"
        out[cn] = sorted(f.stem.replace("cap-", "") for f in d.glob("cap-*.md")) if d.is_dir() else []
    return out


def _runtime_union() -> tuple[set[str], dict]:
    """运行时实测的卡并集。

    【优先 HTTP 口径】_http_cards.json 来自对**运行中的服务**打接口, 是最强证据:
    它包含路由层约束(品种白名单、参数处理、降级分支), 进程内直调看不到这些
    (实测: 进程内能跑 RB/CU, 线上它们不在白名单里; 路由曾把 term 硬编码为 None,
    导致期限结构票线上永不触发而进程内探针完全看不出来)。
    没有 HTTP 产物时回退到 _runtime_cards.json(进程内探针)。
    """
    hp = ROOT / "_http_cards.json"
    if hp.exists():
        d = json.loads(hp.read_text(encoding="utf-8"))
        d["_source"] = "http"
        return set(d.get("union") or []), d
    p = ROOT / "_runtime_cards.json"
    if not p.exists():
        return set(), {}
    d = json.loads(p.read_text(encoding="utf-8"))
    d["_source"] = "in_process"
    return set(d.get("union") or []), d


def _strip(slug: str) -> str:
    """去掉 'cap-' 前缀, 让两侧口径一致(_all_slugs 给的是无前缀形式)。"""
    return slug[4:] if slug.startswith("cap-") else slug


def _resolve(pairs: set[tuple[str, str]], slug: str, prefix: str,
             owners: dict[str, set[str]]) -> bool:
    """判定 (prefix, slug) 是否被这些 pair 覆盖(slug 两侧均按无前缀比对)。

    带前缀的 pair 精确匹配; 裸 slug(--> prefix='') 只在"该书是唯一拥有者"时算命中,
    同名跨书(money-management)时必须前缀明确, 不靠猜。
    """
    p = {(_strip(s)) for pre, s in pairs if pre == prefix}
    if slug in p:
        return True
    if any(pre == "" and _strip(s) == slug for pre, s in pairs):
        return owners.get(slug) == {prefix}
    return False


def main() -> None:
    a, b = _scan()
    rt, rt_detail = _runtime_union()
    per_skill = _all_slugs()

    # slug -> 拥有它的书前缀集(用于消歧裸 slug)
    owners: dict[str, set[str]] = {}
    for cn, slugs in per_skill.items():
        pre = PREFIX_BY_SKILL[cn]
        for s in slugs:
            owners.setdefault(s, set()).add(pre)

    rows, total, na, nb, nr = [], 0, 0, 0, 0
    for cn, slugs in per_skill.items():
        pre = PREFIX_BY_SKILL[cn]
        for s in slugs:
            total += 1
            triggered = s in rt
            if _resolve(a, s, pre, owners):
                status, na = "A-输出契约", na + 1
            elif _resolve(b, s, pre, owners):
                status, nb = "B-注入署名", nb + 1
            else:
                status = "C-缺口"
            if triggered:
                nr += 1
            rows.append({"skill": cn, "cap": s, "prefix": pre, "status": status,
                         "runtime_triggered": triggered})
    overall = na + nb
    # 运行时覆盖: 按**去重后的 slug** 计数。HTTP 产物只带裸 slug, 同名跨书卡
    # (money-management) 无法区分是真触发还是"另一本同名卡触发" → 宁可少报不可虚报。
    all_known = set().union(*per_skill.values()) if per_skill else set()
    rt_distinct = rt & all_known
    rt_cov = len(rt_distinct)
    same_name = [s for s in rt_distinct if len(owners.get(s, set())) > 1]
    report = {
        "generated": "2026-09-29",
        "method": "分层: A=card字段/cards.append; B=注入块◆行署名; C=仅注释(不计); "
                  "runtime=五品种实测真跑出的并集",
        "code_files": CODE_FILES,
        "coverage_A": f"{na}/{total}={na / total * 100:.1f}%",
        "coverage_A_plus_B": f"{overall}/{total}={overall / total * 100:.1f}%",
        "coverage_runtime": f"{rt_cov}/{total}={rt_cov / total * 100:.1f}%",
        "coverage_runtime_note": "去重 slug 口径; HTTPS 产物无书前缀, 同名跨书卡按 1 张计 "
                                 f"(同名项: {same_name or '无'})",
        "runtime_source": rt_detail.get("_source", "none"),
        "tier_A": sorted(a),
        "tier_B": sorted(b),
        "runtime_symbols": rt_detail.get("varieties") or rt_detail.get("symbols") or [],
        "runtime_union": sorted(rt),
        "rows": rows,
    }
    out = ROOT / "_skill_coverage_audit.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"A 层(输出契约, 静态): {report['coverage_A']}")
    print(f"A+B(含提示词在场): {report['coverage_A_plus_B']}")
    print(f"运行时实测(并集, 口径={report['runtime_source']}, "
          f"样本 {len(report['runtime_symbols'])} 个品种): {report['coverage_runtime']}")
    print(f"  A({len(a)}): {' '.join(sorted(f'{p}.{s}' for p, s in a))}")
    print(f"  B({len(b)}): {' '.join(sorted(f'{p}.{s}' for p, s in b))}")
    print(f"  运行时({len(rt_distinct)}): {' '.join(sorted(rt_distinct))}")
    if same_name:
        print(f"  ⚠️ 同名跨书卡(运行时按 1 张计): {same_name}")
    gaps = [f"{r['skill']}.cap-{r['cap']}" for r in rows if r["status"] == "C-缺口"]
    print(f"  C-缺口({len(gaps)}): {' '.join(gaps)}")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
