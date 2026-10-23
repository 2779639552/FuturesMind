# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
spec = importlib.util.spec_from_file_location("bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bt_tech_fund.py"))
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)

raw = bt.fetch_one("TA")
print("price", len(raw["price"]), raw["price"][:1], raw["price"][-1:])
print("inv", raw["inv_id"], len(raw["inv"]), raw["inv"][:1], raw["inv"][-1:])
print("ck", len(raw["ck"]), raw["ck"][-1:])
print("basis", len(raw["basis"]), raw["basis"][-1:], "spot", len(raw["spot"]))
print("err", raw["err"])
rows = bt.build_panel(raw, 7)
print("panel rows", len(rows))
if rows:
    print("sample", {k: (round(v,5) if isinstance(v,float) else v) for k,v in rows[-1].items() if k!="fwd"})
    print("fwd", {k: round(v,4) for k,v in rows[-1]["fwd"].items()})
    tbl = bt.cell_table(rows, "mom20", lambda r: None if r["inv4"] is None else -r["inv4"], 10)
    print("cells", {k: v for k, v in tbl.items() if k.startswith("_")})
