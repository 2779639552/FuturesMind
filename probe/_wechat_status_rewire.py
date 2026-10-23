# -*- coding: utf-8 -*-
"""把 `_loadIngestStatus` 的接线从 `loadResearch` 移到 `loadWarehouse`。

【为什么】`#res-ingest-status` 容器在 **tab-warehouse**（研报库卡片内），
  而 `loadResearch()` 挂在 **tab-research**（研报问答页）——
  挂在后者等于**永不执行**（容器在那个页面根本不存在，`getElementById` 返回 null 直接 return）。
  这是浏览器 E2E 抓到的（静态断言当时是绿的：方法定义了、`this._loadIngestStatus()` 也在，
  只是挂在了错的 tab 上）⇒ 判据必须钉"**挂在拥有该容器的那个 tab 的加载入口**"。

⚠ 一律用 chr(13)+chr(10) 构造 CRLF。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRITE = "--write" in sys.argv
CR, LF = chr(13), chr(10)
EOL = CR + LF


def to_crlf(s: str) -> str:
    return s.replace(EOL, LF).replace(LF, EOL)


# ① 从 loadResearch 里撤掉（那是错的 tab）
OLD_A_LF = ''' this._loadIngestStatus(); // 【2026-10-16】采集通道巡检:通道断了要让用户**在页面上**看见
 // 2026-09-24 历史会话下拉已隐藏, 不再预拉会话列表(_loadQaSessions 函数保留待恢复)
 },''' + '\n'
NEW_A_LF = ''' // 2026-09-24 历史会话下拉已隐藏, 不再预拉会话列表(_loadQaSessions 函数保留待恢复)
 },''' + '\n'

# ② 挂到 loadWarehouse（#res-list 与 #res-ingest-status 都在这个 tab）
OLD_B_LF = ''' await this.refreshResearchList();
 await this.loadUserDatasets(); // 【自传数据】进入数据仓库页时刷新列表''' + '\n'
NEW_B_LF = ''' await this.refreshResearchList();
 // 【2026-10-16】采集通道巡检:通道断了要让用户**在页面上**看见(此前只写 alerts 表)。
 // ⚠ 必须挂**本页**(#res-ingest-status 在研报库卡内); 挂到研报问答页等于永不执行 ——
 //   容器那边不存在, getElementById 返回 null 直接 return, 静态断言还照样绿(实测踩到)。
 this._loadIngestStatus();
 await this.loadUserDatasets(); // 【自传数据】进入数据仓库页时刷新列表''' + '\n'


def main() -> int:
    p = ROOT / "web_template.html"
    raw = p.read_bytes()
    assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "EOL 不是纯 CRLF"
    t = raw.decode("utf-8")
    for tag, old_lf, new_lf in (
        ("① 从 loadResearch 撤掉（错误的 tab）", OLD_A_LF, NEW_A_LF),
        ("② 挂到 loadWarehouse（拥有该容器的 tab）", OLD_B_LF, NEW_B_LF),
    ):
        o, n = to_crlf(old_lf), to_crlf(new_lf)
        c = t.count(o)
        if c != 1:
            print("  ★ [%s] 锚点命中 %d 次（应为 1）" % (tag, c))
            return 2
        t = t.replace(o, n, 1)
        print("  ✓ %s" % tag)

    # 自检：调用必须在 loadWarehouse 体内、loadResearch 体内不得再有
    i = t.index("async loadWarehouse() {")
    body = t[i:i + 900]
    assert "this._loadIngestStatus();" in body, "loadWarehouse 里没挂上"
    j = t.index("async loadResearch() {")
    lb = t[j:t.index("\n },\n", j)] if "\n },\n" in t[j:j + 2000] else t[j:j + 900]
    assert "this._loadIngestStatus();" not in lb, "loadResearch 里还留着（会误导后人）"

    out = t.encode("utf-8")
    assert out.count(b"\n") == out.count(b"\r\n"), "写回破坏 EOL"
    if WRITE:
        p.write_bytes(out)
    print("=== %s ===  %d → %d 字节" % ("落盘" if WRITE else "空跑", len(raw), len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
