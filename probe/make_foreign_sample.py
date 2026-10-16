# -*- coding: utf-8 -*-
"""造一份结构完整的英文商品周报 PDF，作为「外文研报」功能的验收样本。

为什么要造：本机没有现成英文机构报告，而只有真实形态的输入（多页 + 小标题 +
内嵌图表 + 数字密集）才能暴露提取/翻译/排版各环节的问题。
输出：probe/_sample_foreign_report.pdf
"""
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "_sample_foreign_report.pdf"
CHART = Path(__file__).resolve().parent / "_sample_chart.png"

# ── 1. 用 PIL 画一张折线图（模拟机构报告里的价格走势图）──
W, H = 900, 420
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)
d.rectangle([0, 0, W - 1, H - 1], outline=(180, 180, 180), width=2)
pts = [(60, 340), (140, 300), (220, 320), (300, 240), (380, 260),
       (460, 180), (540, 210), (620, 150), (700, 120), (780, 165), (850, 130)]
d.line(pts, fill=(0, 47, 167), width=4)
for x, y in pts:
    d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(0, 47, 167))
d.text((64, 30), "Front-month HH natural gas (US$/MMBtu)", fill=(60, 60, 60))
d.text((64, 52), "Sep 2025 - Sep 2026", fill=(120, 120, 120))
d.line([(60, 370), (850, 370)], fill=(140, 140, 140), width=2)
d.line([(60, 60), (60, 370)], fill=(140, 140, 140), width=2)
img.save(CHART)

# ── 2. 组页 ──
PAGES = [
    [
        ("T1", "NGL & GAS LIQUIDS WEEKLY"),
        ("T2", "Issue 42 | 22 September 2026"),
        ("T2", "Export economics stay wide, but winter risk is now priced"),
        ("H", "Highlights"),
        ("P", "US NGL export economics remain historically wide: the Mont Belvieu propane "
              "(non-LST) discount to front-month Brent held at 42% on a $/MMBtu basis last week, "
              "versus a five-year September average of 36%."),
        ("P", "Our balance implies a 180 thousand b/d (kb/d) build in US propane inventories over "
              "the next four weeks, leaving stocks at 92 million bbl by mid-October, 11 mmbbl above "
              "the five-year average."),
        ("P", "However, we flag that the forward curve already discounts a cold-start premium of "
              "roughly $0.09/gal in the December contract, so the asymmetry has narrowed."),
        ("H", "Three things to watch"),
        ("P", "1) The 14 October EIA weekly report, which will be the first to capture the "
              "post-turnaround restart of the Enterprise Chambers County splitter."),
        ("P", "2) Panama Canal booking slots for LPG carriers; the current auction premium of "
              "$185k is 2.4x the 2025 seasonal norm."),
        ("P", "3) Chinese PDH run rates, which we estimate at 61% for the week ended 18 September, "
              "down 4 percentage points w/w."),
    ],
    [
        ("H", "Section 1: Export arbitrage and freight"),
        ("P", "The US Gulf Coast (USGC) to Chiba arbitrage closed at $71/t on 19 September, "
              "narrowing from $104/t a week earlier as VLGC rates softened. Ras Tanura-Chiba "
              "rose to $58/t, which means US barrels retain a $13/t advantage into Japan."),
        ("P", "Freight: the Baltic LPG index for 44,000 cbm vessels averaged $118/t over the week, "
              "down from $131/t. We assume $115/t for our 4Q26 forecasts, which is $6/t below the "
              "forward market."),
        ("H", "Section 2: Domestic supply"),
        ("P", "Total US NGL production was 6.62 mmb/d in the week to 12 September, up 42 kb/d w/w. "
              "Associated gas growth in the Permian continued to drive the increase, with the "
              "Waha basis averaging -$0.78/MMBtu."),
        ("P", "Fractionation capacity utilisation rose to 94%, the highest since February 2025. "
              "Two new splitters are scheduled to start by year-end, adding 210 kb/d of capacity "
              "at Mont Belvieu."),
        ("H", "Section 3: Demand"),
        ("P", "US propane demand from the petrochemical sector was 1.24 mmb/d, down 3% w/w, "
              "reflecting planned maintenance at two Gulf Coast PDH units. Residential-commercial "
              "demand remained minimal, as expected for the shoulder season."),
        ("P", "Export volumes averaged 1.31 mmb/d, the second-highest weekly level on record. "
              "Japan and South Korea took 46% of the total, with China at 21%."),
    ],
    [
        ("H", "Section 4: Pricing and forward curve"),
        ("P", "Mont Belvieu propane (non-LST) settled at 71.5 cents/gal, up 3.2 cents w/w. "
              "Conway propane was at 68.1 cents/gal. The Mont Belvieu/Conway spread widened to "
              "3.4 cents/gal, which is above the $0.03/gal cost of moving barrels by pipeline."),
        ("P", "Butane at Mont Belvieu was 84.3 cents/gal, leaving the butane-propane spread at "
              "12.8 cents/gal. Ethane at Mont Belvieu was 24.6 cents/gal, and the ethane cracking "
              "advantage over naphtha is estimated at $216/t in Northeast Asia."),
        ("H", "Section 5: Risks to our view"),
        ("P", "Upside risks: an earlier-than-normal heating-degree-day (HDD) accumulation in the "
              "US Northeast; a further widening of the Panama congestion premium; unplanned "
              "outages in the Permian midstream."),
        ("P", "Downside risks: PDH margins in Asia remain negative, which could trigger run cuts; "
              "warm winter forecasts from the ECMWF seasonal model; higher-than-expected Canadian "
              "imports."),
        ("H", "Forecast summary"),
        ("P", "We forecast Mont Belvieu propane (non-LST) at 69.0 cents/gal in 4Q26, which implies "
              "a Brent-linked discount of 40%. Our 2027 average forecast is 66.5 cents/gal."),
    ],
    [
        ("H", "Data appendix"),
        ("P", "US propane inventory, week ended 12 September: 89.6 mmbbl (EIA). "
              "Five-year average: 81.2 mmbbl. Surplus to average: 8.4 mmbbl."),
        ("P", "US NGL exports, week ended 12 September: 1.31 mmb/d, of which propane 1.04 mmb/d, "
              "butane 0.27 mmb/d."),
        ("P", "Brent front-month: $71.4/bbl, down $1.9 w/w. Henry Hub front-month: $3.12/MMBtu, "
              "up $0.14 w/w."),
        ("P", "Baltic LPG index (44,000 cbm), USGC-Chiba: $125/t, down $13/t w/w."),
        ("H", "Disclaimer"),
        ("P", "This document is prepared by a synthetic research desk for pipeline testing "
              "purposes only. Figures are illustrative and must not be relied upon for trading. "
              "No part of this document may be redistributed without written consent."),
    ],
]

doc = pymupdf.open()
for idx, blocks in enumerate(PAGES):
    page = doc.new_page(width=595, height=842)  # A4 pt
    y = 62
    for kind, text in blocks:
        if kind == "T1":
            page.insert_textbox(pymupdf.Rect(56, y, 539, y + 34), text,
                                fontname="hebo", fontsize=21, color=(0, 0.12, 0.42))
            y += 40
        elif kind == "T2":
            page.insert_textbox(pymupdf.Rect(56, y, 539, y + 22), text,
                                fontname="heit", fontsize=11, color=(0.35, 0.35, 0.35))
            y += 26
        elif kind == "H":
            page.insert_textbox(pymupdf.Rect(56, y, 539, y + 20), text,
                                fontname="hebo", fontsize=12.5, color=(0, 0.12, 0.42))
            y += 26
        else:
            rc = page.insert_textbox(pymupdf.Rect(56, y, 539, y + 70), text,
                                     fontname="helv", fontsize=10.5, color=(0.14, 0.14, 0.19))
            y += (70 - rc) + 12
    if idx == 2:  # 第 3 页插一张图，验证内嵌图抽取与回插
        page.insert_image(pymupdf.Rect(70, y + 8, 525, y + 265), filename=str(CHART))
    page.insert_textbox(pymupdf.Rect(56, 800, 539, 820),
                        f"Synthetic NGL & Gas Liquids Weekly - Issue 42 - page {idx + 1}",
                        fontname="helv", fontsize=8, color=(0.45, 0.45, 0.45))

doc.save(str(OUT))
doc.close()
print("written:", OUT, OUT.stat().st_size, "bytes")
