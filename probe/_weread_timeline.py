# -*- coding: utf-8 -*-
"""钉细「微信读书这两天做了什么改动」的时间线（GitHub 公开 API，只读）。"""
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
H = {"User-Agent": UA, "Accept": "application/vnd.github+json"}


def commits(repo, n=40, path=None, since=None):
    url = "https://api.github.com/repos/%s/commits?per_page=%d" % (repo, n)
    if path:
        url += "&path=" + path
    if since:
        url += "&since=" + since
    try:
        r = requests.get(url, headers=H, timeout=45)
        if r.status_code != 200:
            return [("HTTP %s" % r.status_code, "")]
        return [(c["commit"]["committer"]["date"], c["commit"]["message"].split("\n")[0][:88])
                for c in (r.json() or [])]
    except Exception as e:
        return [("err", str(e)[:60])]


print("=" * 84)
print("① steptian/weread-mp  —— 2026-10-10 之后的提交（找平台变更的起点）")
print("=" * 84)
for d, m in commits("steptian/weread-mp", 40, since="2026-10-10T00:00:00Z"):
    print("   %s  %s" % (d[:19], m))

print()
print("=" * 84)
print("② rachelos/we-mp-rss  —— 近期整体提交（看有没有围绕这两天的适配）")
print("=" * 84)
for d, m in commits("rachelos/we-mp-rss", 12):
    print("   %s  %s" % (d[:19], m))
