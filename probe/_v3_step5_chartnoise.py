# -*- coding: utf-8 -*-
"""密度重构 v3 · 第五步：用 setOption 包裹统一收敛坐标轴网格线。

为什么需要包裹而不是设默认：
  各图表在**自己的** setOption 里显式写了 splitLine/axisLine，
  单纯给实例设初始化默认会被它们覆盖。
  包裹 setOption 可以在每次配置后统一"降噪"，且只碰网格线、不碰数据。
规则（苹果式）：
  · 分类轴的 splitLine **一律关闭** —— 这是"每个数据点一条竖线"的根源
  · 数值轴只保留极淡横线，且条数收敛
  · 轴线/刻度线默认关闭（容器边框已足够表达边界）
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# 找到上一步注入的块，替换其中的 echarts.init 补丁为 setOption 包裹
OLD_MARK = "  // 统一给所有 ECharts 实例设默认（后续 setOption 里的显式配置仍可覆盖）"
i = src.find(OLD_MARK)
if i < 0:
    raise SystemExit("FAIL: 未找到上一步注入的锚点")

# 定位该 IIFE 的结尾（下一个 })(); 之后）
j = src.find("})();", i)
if j < 0:
    raise SystemExit("FAIL: 未找到 IIFE 结尾")

NEW = r'''  // 统一给所有 ECharts 实例设默认，并**包裹 setOption** 做网格线降噪。
  // 为什么要包裹：各图表在自己的 setOption 里显式写了 splitLine/axisLine，
  // 单纯设初始化默认会被覆盖。包裹后可在每次配置落地前统一收敛，
  // 且只碰网格线/轴线，绝不触碰 data 与 series。
  const demoAxes = (opt) => {
    if (!opt) return opt;
    const clean = (ax) => {
      if (!ax) return;
      const arr = Array.isArray(ax) ? ax : [ax];
      arr.forEach(a => {
        if (!a || typeof a !== 'object') return;
        a.axisLine = Object.assign({ show: false }, a.axisLine || {});
        a.axisTick = Object.assign({ show: false }, a.axisTick || {});
        if (a.type === 'category') {
          // 分类轴绝不画分割线 —— "每个数据点一条竖线"的根源
          a.splitLine = { show: false };
        } else {
          const sl = Object.assign({}, a.splitLine || {});
          sl.show = true;
          sl.lineStyle = Object.assign({ color: 'rgba(128,128,128,0.13)', width: 1 },
                                       sl.lineStyle || {});
          a.splitLine = sl;
          if (a.splitNumber == null) a.splitNumber = 4;
        }
      });
    };
    clean(opt.xAxis);
    clean(opt.yAxis);
    // 极坐标/雷达不受影响
    return opt;
  };
  const origInit = echarts.init;
  echarts.init = function () {
    const inst = origInit.apply(this, arguments);
    const origSet = inst.setOption;
    inst.setOption = function (opt, notMerge, lazy) {
      // 数组形式的 option 逐个处理
      if (Array.isArray(opt)) opt = opt.map(demoAxes);
      else demoAxes(opt);
      return origSet.call(this, opt, notMerge, lazy);
    };
    origSet.call(inst, {
      grid: { left: 6, right: 14, top: 22, bottom: 4, containLabel: true },
      textStyle: { fontFamily: getComputedStyle(document.body).fontFamily },
    }, false, true);
    return inst;
  };
})();'''

src = src[:i] + NEW + src[j + len("})();"):]

F.write_text(src, encoding="utf-8")
print("OK 已改为 setOption 包裹式降噪")
print(f"  {n0} -> {len(src.encode('utf-8'))} 字节")
print("  script 配平:", src.count("<script") - src.count("</script>"))
print("  裸 {{ 块:", len(re.findall(r'(?m)^{', src)))
