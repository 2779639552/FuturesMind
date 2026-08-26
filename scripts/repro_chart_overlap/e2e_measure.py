"""End-to-end: render the real comparison chart in the running app, measure overlap."""
import pathlib
from playwright.sync_api import sync_playwright

MEASURE_JS = r"""
() => {
  const inst = echarts.getInstanceByDom(document.getElementById('chart-compare-price'));
  if (!inst) return { err: 'chart instance not found' };
  const rect = (group) => {
    if (!group) return null;
    const r = group.getBoundingRect();
    const tl = group.transformCoordToGlobal(r.x, r.y);
    const br = group.transformCoordToGlobal(r.x + r.width, r.y + r.height);
    return { x: Math.round(tl[0]), y: Math.round(tl[1]), right: Math.round(br[0]), bottom: Math.round(br[1]) };
  };
  const model = inst.getModel();
  const tV = inst.getViewOfComponentModel(model.getComponent('title'));
  const lV = inst.getViewOfComponentModel(model.getComponent('legend'));
  const dom = inst.getDom();
  const W = dom.clientWidth, H = dom.clientHeight;
  const g = (inst.getOption().grid || [{}])[0];
  const grid = { x: Math.round(g.left||0), y: Math.round(g.top||0), right: Math.round(W-(g.right||0)), bottom: Math.round(H-(g.bottom||0)) };
  return {
    title: rect(tV && tV.group), legend: rect(lV && lV.group), grid,
    container: { w: W, h: H },
    titleText: (inst.getOption().title || [{}])[0].text,
  };
}
"""

def overlap(a, b):
    if not a or not b:
        return False
    return not (a["right"] <= b["x"] or b["right"] <= a["x"] or a["bottom"] <= b["y"] or b["bottom"] <= a["y"])

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 800})
    page.goto("http://localhost:5000/", wait_until="domcontentloaded")
    page.wait_for_function("typeof App !== 'undefined' && typeof echarts !== 'undefined'")
    # render the real comparison chart via the app's own method
    page.evaluate("App.loadComparisonChart('RB', '2026-07-20')")
    page.wait_for_timeout(1800)
    d = page.evaluate(MEASURE_JS)
    if "err" in d:
        print("ERR:", d["err"])
    else:
        print("container:", d["container"], "| title:", d["titleText"])
        print("  title :", d["title"])
        print("  legend:", d["legend"])
        print("  grid  :", d["grid"])
        t, lg, g = d["title"], d["legend"], d["grid"]
        for label, a, c in [("title∩legend", t, lg), ("title∩grid", t, g), ("legend∩grid", lg, g)]:
            print(f"  {label}: {'YES (overlap)' if overlap(a, c) else 'no'}")
    page.screenshot(path=str(pathlib.Path(__file__).parent / "e2e_chart.png"))
    b.close()
