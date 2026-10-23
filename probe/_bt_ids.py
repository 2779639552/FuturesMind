# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
import gtja_module as g
for code in ["TA", "RB", "M"]:
    r = g.fetch_overview(code, range_days=60)
    print("=== %s ok=%s ===" % (code, r.get("ok")))
    for i in r.get("indicators", []):
        print("  %-26s %-8s n=%-4d %s" % (i.get("id"), i.get("group"), len(i.get("series") or []), i.get("label")))
