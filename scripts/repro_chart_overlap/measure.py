"""Measure title/legend/grid bounding rects via ECharts API — numeric overlap detection."""
import json
import pathlib
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent

JS = r"""
() => {
  const ids = ['c1', 'c2', 'c3'];
  const out = [];
  const rect = (group) => {
    if (!group) return null;
    const r = group.getBoundingRect();
    // convert group-local corners to container coordinates
    const tl = group.transformCoordToGlobal(r.x, r.y);
    const br = group.transformCoordToGlobal(r.x + r.width, r.y + r.height);
    return { x: Math.round(tl[0]), y: Math.round(tl[1]),
             right: Math.round(br[0]), bottom: Math.round(br[1]),
             w: Math.round(br[0]-tl[0]), h: Math.round(br[1]-tl[1]) };
  };
  for (const id of ids) {
    const inst = echarts.getInstanceByDom(document.getElementById(id));
    const model = inst.getModel();
    const dom = inst.getDom();
    const W = dom.clientWidth, H = dom.clientHeight;
    const titleV = inst.getViewOfComponentModel(model.getComponent('title'));
    const legendV = inst.getViewOfComponentModel(model.getComponent('legend'));
    // grid rect derived from option grid + container size (view group is empty)
    const g = (inst.getOption().grid || [{}])[0];
    const grid = { x: Math.round(g.left || 0), y: Math.round(g.top || 0),
                   right: Math.round(W - (g.right || 0)), bottom: Math.round(H - (g.bottom || 0)) };
    out.push({ id,
      title: rect(titleV && titleV.group),
      legend: rect(legendV && legendV.group),
      grid });
  }
  return out;
}
"""

def overlap(a, b):
    if not a or not b:
        return False
    return not (a["right"] <= b["x"] or b["right"] <= a["x"] or a["bottom"] <= b["y"] or b["bottom"] <= a["y"])

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 900, "height": 620})
    page.goto((HERE / "repro.html").as_uri())
    page.wait_for_function("window.echarts !== undefined")
    page.wait_for_timeout(600)
    data = page.evaluate(JS)
    for d in data:
        print(f"\n[{d['id']}]")
        print("  title :", d["title"])
        print("  legend:", d["legend"])
        print("  grid  :", d["grid"])
        t, lg, g = d["title"], d["legend"], d["grid"]
        if t and lg:
            print("  title∩legend:", "YES (overlap)" if overlap(t, lg) else "no")
        if t and g:
            print("  title∩grid  :", "YES (overlap)" if overlap(t, g) else "no")
        if lg and g:
            print("  legend∩grid :", "YES (overlap)" if overlap(lg, g) else "no")
    b.close()
