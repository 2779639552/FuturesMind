"""skills 规范体检器: 扫描 ~/.workbuddy/skills 下全部 SKILL.md, 输出违规清单。

【判据来源】WorkBuddy 技能规范(frontmatter 最低要求 name + description;
 可选 version/display_name/description_zh/agent_created 等)。
【输出】_skills_audit.json, 供生成报告用。
"""
import io
import json
import os
import re
from pathlib import Path

ROOT = Path(os.path.expanduser("~/.workbuddy/skills"))
OUT = Path("_skills_audit.json")

FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", re.S)
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_fm(text):
    """极简 YAML frontmatter 解析(只取顶层标量, 不引入 yaml 依赖)。"""
    m = FM_RE.match(text)
    if not m:
        return None, {}
    fm = {}
    for line in m.group(1).split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return m.group(1), fm


def main():
    rows = []
    for p in sorted(ROOT.rglob("SKILL.md")):
        rel = p.relative_to(ROOT)
        parts = rel.parts
        # 技能标识: 命名空间容器下取"容器/技能名", 否则取顶层目录名
        if parts[0].startswith("@") and len(parts) >= 3:
            skill_dir = parts[1]
            ns = parts[0]
        else:
            skill_dir = parts[0]
            ns = ""
        try:
            text = io.open(p, encoding="utf-8").read()
        except Exception as e:
            rows.append({"path": str(rel).replace("\\", "/"), "skill": skill_dir, "ns": ns,
                         "fatal": f"无法读取: {type(e).__name__}"})
            continue

        fm_raw, fm = parse_fm(text)
        body = text[len(fm_raw) + 8:] if fm_raw is not None else text
        issues = []

        # ① frontmatter
        if fm_raw is None:
            issues.append(("P0", "frontmatter", "缺少 YAML frontmatter(文件未以 --- 开头)"))
        else:
            # ② name
            name = fm.get("name", "")
            if not name:
                issues.append(("P0", "name", "缺必填字段 name"))
            elif name != skill_dir:
                issues.append(("P1", "name", f"name({name}) 与目录名({skill_dir}) 不一致"))
            elif not KEBAB_RE.match(name):
                issues.append(("P2", "name", f"name({name}) 非 kebab-case(小写+连字符)"))
            # ③ description
            desc = fm.get("description", "")
            if not desc:
                issues.append(("P0", "description", "缺必填字段 description"))
            elif len(desc) < 20:
                issues.append(("P1", "description", f"description 过短({len(desc)} 字符), 难以触发匹配"))
            elif len(desc) > 1024:
                issues.append(("P2", "description", f"description 过长({len(desc)} 字符), 建议 ≤1024"))

        # ④ 正文非空
        if len(body.strip()) < 50:
            issues.append(("P0", "body", f"正文过短({len(body.strip())} 字符), 无实质指令"))

        # ⑤ agent_created 标注
        if not fm.get("agent_created"):
            issues.append(("P3", "agent_created", "未标 agent_created: true(自研技能应标注, 否则无法被 SkillManage 修改)"))

        rows.append({
            "path": str(rel).replace("\\", "/"), "skill": skill_dir, "ns": ns,
            "name": fm.get("name", ""), "desc_len": len(fm.get("description", "")),
            "agent_created": fm.get("agent_created", "") or "",
            "lines": text.count("\n") + 1, "bytes": len(text.encode("utf-8")),
            "issues": [{"level": a, "field": b, "msg": c} for a, b, c in issues],
            "fm_keys": sorted(fm.keys()),
        })

    # 汇总
    sev = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for r in rows:
        for it in r.get("issues", []):
            sev[it["level"]] += 1
    clean = [r for r in rows if not r.get("issues")]
    summary = {
        "skills_root": str(ROOT),
        "total": len(rows),
        "clean": len(clean),
        "with_issues": len(rows) - len(clean),
        "severity": sev,
    }
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n=== 有问题的技能 ===")
    for r in rows:
        if r.get("issues"):
            print(f"[{r['skill']}] " + "; ".join(f"{i['level']}:{i['msg']}" for i in r["issues"]))


if __name__ == "__main__":
    main()
