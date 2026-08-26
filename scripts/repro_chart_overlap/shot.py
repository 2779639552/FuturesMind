"""Render repro.html with Playwright and screenshot — to SEE the legend/title overlap."""
import pathlib
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
out = HERE / "shot.png"

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 900, "height": 620, "device_scale_factor": 2})
    page.goto((HERE / "repro.html").as_uri())
    page.wait_for_function("window.echarts !== undefined")
    page.wait_for_timeout(800)  # let charts paint
    page.screenshot(path=str(out), full_page=True)
    b.close()
print("saved:", out)
