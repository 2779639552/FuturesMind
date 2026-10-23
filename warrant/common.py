# -*- coding: utf-8 -*-
"""仓单数据采集 - 公共工具"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import random
import time
from typing import Iterable, Optional

import requests

log = logging.getLogger("warrant")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 仓单数据统一落在 <项目根>/data/warrant 下，与其它模块的 data/ 并列，便于打包备份
DATA_DIR = os.path.join(ROOT, "data", "warrant")
RAW_DIR = os.path.join(DATA_DIR, "raw")
OUT_DIR = os.path.join(DATA_DIR, "out")
WEB_DIR = os.path.join(DATA_DIR, "web")          # 供前端直接读取的 JSON
# 【2026-10-21】每所「上一次成功采集」的快照。单所失败时用它兜底，
#   避免一个瞬时网络故障就把该所从页面上整块抹掉（当时四所变两所、无提示）。
LASTGOOD_DIR = os.path.join(DATA_DIR, "lastgood")


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def new_session(extra_headers: Optional[dict] = None) -> requests.Session:
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    if extra_headers:
        s.headers.update(extra_headers)
    return s


def get(session: requests.Session, url: str, **kw):
    """带重试的 GET；verify=False 以兼容部分交易所证书链问题"""
    last = None
    for i in range(3):
        try:
            r = session.get(url, timeout=kw.pop("timeout", 30), verify=False, **kw)
            return r
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(1.0 + i + random.random())
    raise last


def post(session: requests.Session, url: str, **kw):
    last = None
    for i in range(3):
        try:
            r = session.post(url, timeout=kw.pop("timeout", 30), verify=False, **kw)
            return r
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(1.0 + i + random.random())
    raise last


def today() -> dt.date:
    return dt.date.today()


def date_range_back(end: dt.date, days: int) -> Iterable[dt.date]:
    """从 end 往前生成 days 个自然日"""
    for i in range(days):
        yield end - dt.timedelta(days=i)


def save_raw(subdir: str, filename: str, content) -> str:
    d = ensure_dir(os.path.join(RAW_DIR, subdir))
    p = os.path.join(d, filename)
    mode = "wb" if isinstance(content, (bytes, bytearray)) else "w"
    kw = {} if mode == "wb" else {"encoding": "utf-8"}
    with open(p, mode, **kw) as f:
        f.write(content)
    return p


def save_json(path: str, obj) -> str:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    return path


def to_float(v, default=None):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("　", "").strip()
    if s in ("", "-", "--", "nan", "None"):
        return default
    try:
        return float(s)
    except ValueError:
        return default
