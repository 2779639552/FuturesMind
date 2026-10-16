import sys, pathlib
from playwright.sync_api import sync_playwright

SRC = pathlib.Path(r"D:\AgentSense\AgentSense\probe\redesign_preview.html")
OUT = SRC.parent / "_preview"
OUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
        pg.goto(SRC.as_uri())
        if theme == "dark":
            pg.evaluate("document.documentElement.dataset.theme='dark'")
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(OUT / f"preview_{theme}_full.png"), full_page=True)
        pg.screenshot(path=str(OUT / f"preview_{theme}_fold.png"))
        # 度量
        m = pg.evaluate("""() => {
          const sizes = {};
          document.querySelectorAll('*').forEach(el => {
            if (!el.offsetParent && el.tagName !== 'BODY') return;
            const cs = getComputedStyle(el);
            const t = (el.textContent || '').trim();
            if (!t || el.children.length) return;
            const k = cs.fontSize;
            sizes[k] = (sizes[k] || 0) + 1;
          });
          const h1 = document.querySelector('h1');
          return {
            fontSizes: sizes,
            h1: h1 ? {size: getComputedStyle(h1).fontSize, ls: getComputedStyle(h1).letterSpacing} : null,
            body: getComputedStyle(document.body).fontSize,
            overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
            emoji: (document.body.innerText.match(/[\\u{1F300}-\\u{1FAFF}\\u{2600}-\\u{27BF}]/gu) || []).join(''),
          };
        }""")
        lines = [f"=== {theme} ===", f"h1={m['h1']}", f"body={m['body']}",
                 f"横向溢出={m['overflowX']}", f"emoji={m['emoji'] or '无'}",
                 "字号分布: " + ", ".join(f"{k}x{v}" for k, v in sorted(m['fontSizes'].items(), key=lambda x: -x[1]))]
        (OUT / f"metrics_{theme}.txt").write_text("\n".join(lines), encoding="utf-8")
        pg.close()
    b.close()
print("done")
