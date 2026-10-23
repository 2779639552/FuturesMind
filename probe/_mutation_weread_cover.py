# -*- coding: utf-8 -*-
"""变异自检：验证「cover 换链路」那批护栏真的能拦住回归。

做法：逐条把生产代码改成**错误写法**，跑护栏 —— 期望变红（= 被击杀）；
跑完立即恢复，并逐字节校验 md5（防"变异污染源文件却自查已恢复"）。
"""
import ast
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COL = ROOT / "wechat_weread_collector.py"
TESTFILE = "tests/test_wechat_weread_collector_cover_dummy.py"  # 占位，下面改
PY = str(ROOT / "venv/Scripts/python.exe")

MUT = [
    ("M1 取清单改回已废弃的 fetch_articles",
     'arts = fetch_latest(bid)',
     'arts = fetch_articles(bid, 0)'),

    ("M2 去重键用 split('_')[-1]（bookId 含下划线 ⇒ 截错 token）",
     'oid = rid.split(bid + "_", 1)[1] if (bid + "_") in rid else rid',
     'oid = rid.split("_")[-1]'),

    ("M3 去掉跨格式标题兜底（过渡期会重复入库）",
     'if (name, _norm_title(a["title"])) in known_t:',
     'if False:'),

    ("M4 把兜底日期写进 create_time（污染水位线 ⇒ 之后静默不采）",
     '"create_time": ts,',
     '"create_time": ts or int(_dt.datetime.strptime(date, "%Y-%m-%d").timestamp()),'),

    ("M5 猜不到日期时瞎猜（返回采集日而不是空串）",
     '''        except Exception:  # noqa: BLE001
            pass
    return ""''',
     '''        except Exception:  # noqa: BLE001
            pass
    return _dt.datetime.now().strftime("%Y-%m-%d")'''),

    ("M6 cover 端点改回 /web/mp/articles",
     'r = _get("/web/mp/cover", {"bookId": bid}, timeout=40)',
     'r = _get("/web/mp/articles", {"bookId": bid, "offset": 0}, timeout=40)'),

    ("M7 去掉 --backfill 的能力受限提示（静默只采 1 篇）",
     '    if args.backfill and args.backfill > 1:',
     '    if False:'),

    ("M8 报错不带可执行指引（用户不知道怎么办）",
     '"   ⇒ cookie 的登录态已失效（跑 桌面「重登公众号读书通道.bat」）"',
     '"   ⇒ cookie 的登录态已失效"'),

    ("M9 去掉旧接口的废弃标注（后人会改回去）",
     '    """【已废弃 · 2026-10-16】取一页「群发」—— 平台已停用该接口。',
     '    """取一页「群发」。'),

    ("M10 去掉 collect_account 里的覆盖上限说明",
     '    #   ★ cover **一次只给最新一篇**（平台返回体里没有往期列表）⇒ 不再有"翻页"概念。',
     '    #'),
]

TESTS = "tests/test_wechat_weread_cover.py"


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def run_tests() -> tuple[int, str]:
    r = subprocess.run([PY, "-m", "pytest", TESTS, "-q", "--no-header", "-p", "no:warnings"],
                       cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out


def main() -> int:
    ast.parse(Path(__file__).read_text(encoding="utf-8"))
    base = COL.read_bytes()
    base_md5 = hashlib.md5(base).hexdigest()
    print("=" * 78)
    print("  变异自检 · 读书通道 cover 换链路")
    print("=" * 78)

    src = base.decode("utf-8")
    print("  基线护栏:")
    rc, out = run_tests()
    print("    %s" % (out.strip().splitlines()[-1] if out.strip() else "?"))
    if rc != 0:
        print("  ★ 基线就是红的 ⇒ 先修护栏，停止变异")
        return 1

    killed = survived = skipped = 0
    for name, old, new in MUT:
        if src.count(old) != 1:
            print("  ⚠ 跳过（锚点命中 %d）: %s" % (src.count(old), name))
            skipped += 1
            continue
        COL.write_bytes(src.replace(old, new, 1).encode("utf-8"))
        try:
            rc2, out2 = run_tests()
            line = out2.strip().splitlines()[-1] if out2.strip() else "?"
            if rc2 != 0:
                killed += 1
                print("  ✔ 击杀  %s\n           %s" % (name, line))
            else:
                survived += 1
                print("  ★ 存活  %s   ← 护栏没拦住！" % name)
        finally:
            COL.write_bytes(base)
            assert md5(COL) == base_md5, "★ 恢复失败！文件已被污染: " + name

    print()
    print("=" * 78)
    print("  结果: 击杀 %d / 存活 %d / 跳过 %d  （共 %d 项）" % (killed, survived, skipped, len(MUT)))
    print("=" * 78)
    assert md5(COL) == base_md5, "源文件最终 md5 与基线不一致"
    print("  ✔ 源文件 md5 已复原: %s" % base_md5[:12])
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
