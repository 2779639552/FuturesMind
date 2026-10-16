"""
抖音平台适配器 (持久 profile + 页面 API 拦截, 2026-09-15)
==========================================================
抖音反爬极强(a_bogus 签名 + TLS 指纹 + 验证码),纯 API 必败;
本适配器走 Playwright 浏览器模拟,页面自己签名发请求,直接拦截响应解析。

【风控实证(2026-09-15 探测)】
  - storage_state 回放(只带 cookie)→ 搜索接口 200 但结果为空(风控软拦)
  - 无登录态 → 验证码中间页
  - 可用路径:launch_persistent_context 固定用户目录(douyin_login_profile.py
    扫码登录) + 综合搜索通道 /aweme/v1/web/general/search/single/
    (type=video 竖切通道 search/item 即使登录态也被软拦,勿用)
  - 评论:视频页 /aweme/v1/web/comment/list/ 响应拦截,滚动触发翻页

【形态创新】首个"评论"形态适配器:每条评论 = 一条独立 UNIFIED item
  (platform="douyin", note_id="dy:c:{cid}", note_type="comment", desc=评论正文,
   title=所属视频标题前60字),视频本身也收一条("dy:v:{aweme_id}")。
  评论直接进 NER+情感管线,与帖子平台同构,聚合/分析侧零改动。
"""

import os
import re
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from urllib.parse import quote

from .base import PlatformAdapter

logger = logging.getLogger("platforms.douyin")

PROFILE_DIR = Path(__file__).parent.parent / "credentials" / "douyin_profile"


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    """读环境变量并钳制到 [lo, hi];缺失/非法/越界一律回落, 绝不抛错。

    【为什么】这三项是"成本旋钮": 现场按风控松紧临时调整, 但不该让一个手滑的
    环境变量把采集器变成"开 0 个视频"(采不到东西)或"滚 999 次"(跑一整晚)。
    """
    try:
        value = int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default
    if value < lo or value > hi:
        return default
    return value


# ── 单词语成本闸 (2026-09-17) ────────────────────────────────────────────
# 【为什么要降】实测单关键词固定耗时 ≈240s: 搜索(~30s) + 6 个视频 ×(开页 +
#   3.5s 启动 + 15 次滚动×2s ≈ 35s)。而 batch_collect 的全局预算只有 12min、
#   调度器子进程硬超时 15min ⇒ 一轮最多跑 3 个词。这正是"抖音长期只覆盖沥青/
#   苯乙烯两个品种"的直接原因: 词池小是一回事, **成本把窗口压得比词池还窄**。
# 【为什么砍开页数几乎不亏数据】评论按页加载(每页 ~15~20 条), 而 batch_collect
#   侧 `items[:count]` 有 50 条上限 —— 也就是说多开视频只是"更快撞上上限",
#   开 3~4 个视频足以填满配额。所以砍开页数基本不损失单词语产出, 却按比例省时间。
# 【代价(写清, 别自我感觉良好)】单个视频的评论深度变浅(8 页 ≈ 120 条, 旧 15 页),
#   且评论稀疏的视频会因为数量少而少采一些; 换来的是**关键词覆盖数翻倍**
#   (3 词 → 6 词/轮)与更小的风控暴露面。
MAX_COMMENT_VIDEOS = _env_int("AGENTSENSE_DY_MAX_VIDEOS", 4, 1, 8)
# 单视频评论滚动次数(每次滚动触发一页评论 XHR,约 2s)
COMMENT_SCROLLS = _env_int("AGENTSENSE_DY_COMMENT_SCROLLS", 8, 1, 30)
# 连续 N 次滚动**没有新评论**就提前收尾: 评论少的视频不必白等满 8 页(实测大量视频
# 滚 3 页后就再也加载不出新评论, 却仍要每页等 2s)。
EMPTY_SCROLL_LIMIT = _env_int("AGENTSENSE_DY_EMPTY_SCROLLS", 3, 1, 10)
# ── 搜索阶段: "滚动优先" (2026-09-18 实测修正, 原为"先硬等 20s") ──────────
# 【为什么把顺序反过来】原实现是"先干等 SEARCH_WAIT_SECONDS=20s 等响应, 等不到再滚 2 次"。
#   同会话对照实验(2026-09-18, 同 profile 只改一个变量)证明**顺序是错的**:
#     · 先等 20s 再滚: 20s 内零响应, 首个响应 +21.3s 才到(靠滚动才触发) → 阶段 24.3s
#     · goto 后立刻滚 3 次: 首个响应 +0.0s / +4.4s 到 → 阶段 5.3s / 5.9s
#   ⇒ 搜索响应由**滚动**触发, 干等不产生任何数据。每词白花约 20s(占单词语成本约 28%),
#     一轮 6 词 = 白等 2 分钟。生产侧交叉验证: 单词语耗时是整齐常数(71/71/71/72/71/72s)。
# 【兜底还在】滚动后仍可能真没响应(接口异常慢/被软拦), 保留一个**短**兜底等待;
#   下文"假成功空数据"的判定照旧触发 DouyinBlocked, 不会把限流误报成"平台没内容"。
SEARCH_SCROLLS = _env_int("AGENTSENSE_DY_SEARCH_SCROLLS", 3, 1, 8)
SEARCH_SCROLL_INTERVAL = 1.5  # 每次滚动后给 XHR 的落地时间(实测响应 0~4.4s 内到)
SEARCH_FOLLOWUP_WAIT = _env_int("AGENTSENSE_DY_SEARCH_WAIT", 8, 1, 30)  # 滚完仍无响应才兜底等(秒)
SEARCH_MAX_RESPONSES = 6  # 最多捕获响应数(综合通道首屏+滚动会发多次)


class DouyinBlocked(Exception):
    """抖音风控拦截（软限流 / 验证码中间页）——**不是凭证失效**。

    【为什么单独建异常类型】先前统一抛普通 Exception 且文案含"验证码"，
    而 `classify_error` 把含"验证码"的异常归成 `auth`，于是一路升级成
    「登录凭证失效，请重新登录」的告警 —— 而实测**重新扫码对软限流无效**，
    会把使用者引到错误方向。单独建模成 rate_limit 类，告警措辞才能与真实处置对齐。
    """

_STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    delete navigator.__proto__.webdriver;
    window.navigator.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
"""


def _ts_to_str(ts) -> str:
    """抖音 unix 秒 → 'YYYY-MM-DD HH:MM:SS'(本地时区);空/异常返回 ''。"""
    try:
        ts = int(ts)
        if ts <= 0:
            return ""
        return (datetime.fromtimestamp(ts, tz=timezone.utc)
                .astimezone().strftime("%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


class DouyinAdapter(PlatformAdapter):
    """抖音数据采集适配器 (持久 profile + 页面 API 拦截)"""

    name = "douyin"
    display_name = "抖音"
    id_prefix = "dy:"

    def __init__(self):
        self._playwright: Any = None
        self._context: Any = None  # launch_persistent_context 返回的即 context
        self._page: Any = None
        self._logged_in = False

    # ═══════════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════════

    def init(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise Exception(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        self._playwright = sync_playwright().start()
        if not PROFILE_DIR.exists():
            raise Exception(
                f"抖音持久 profile 不存在: {PROFILE_DIR}\n"
                "请先运行 python douyin_login_profile.py 扫码登录"
            )
        # 持久 profile:登录态+指纹态一体(douyin_login_profile.py 同目录),不换壳
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,  # headless 会触发验证码,必须有头
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-dev-shm-usage"],
        )
        self._context.add_init_script(_STEALTH_JS)
        names = {c["name"] for c in self._context.cookies("https://www.douyin.com")}
        self._logged_in = bool({"sessionid", "sessionid_ss"} & names)
        if not self._logged_in:
            logger.warning("抖音 profile 无登录态 — 请运行 python douyin_login_profile.py 扫码")
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

        # 开首页暖场(让 msToken 等动态 cookie 先生成)
        try:
            self._page.goto("https://www.douyin.com/", wait_until="domcontentloaded",
                            timeout=30000)
            time.sleep(4)
        except Exception as e:
            logger.warning("Douyin homepage warmup: %s", e)

        logger.info("Douyin browser session ready (logged_in=%s)", self._logged_in)

    def _check_captcha(self) -> bool:
        """当前页是否验证码中间页(标题最可靠,body 可能是 JS 空壳)。"""
        try:
            title = self._page.title() or ""
            return "验证码" in title
        except Exception:
            return False

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # 搜索 (综合通道 API 拦截;评论在同一趟完成)
    # ═══════════════════════════════════════════════════════════

    @property
    def needs_detail_fetch(self) -> bool:
        return False  # 搜索内部已含视频页评论采集,无需 batch_collect 再深挖

    def get_detail(self, raw_item: Any) -> Optional[dict]:
        return None

    def search(self, keyword: str, count: int) -> list[Any]:
        """综合搜索拿视频 → 前 N 个视频开页拦评论。返回 [_kind 标记的原始 dict] 列表。"""
        videos = self._search_videos(keyword)
        if not videos:
            if self._check_captcha():
                raise DouyinBlocked(self._diagnose_block(keyword, escalated=True))
            return []

        items: list[dict] = []
        for v in videos[:MAX_COMMENT_VIDEOS]:
            try:
                comments = self._scrape_comments(v, keyword)
                time.sleep(1.5)
            except Exception as e:
                logger.warning("Comments for %s failed: %s", v["aweme_id"], str(e)[:100])
                comments = []
            # 评论优先放前(评论是本适配器的核心价值),视频兜底
            items = comments + [v] + items

        return items[:count]

    def _diagnose_block(self, keyword: str, escalated: bool) -> str:
        """给抖音风控失败一个**准确**的说明（避免误导用户去白扫码）。

        【功能】区分"软限流"与"验证码拦截"两种风控，并给出正确处置建议。
        【参数】keyword: 当前关键词；escalated: 是否已升级为验证码中间页
        【返回】可直接透传给用户的诊断文案
        【关键逻辑】⚠️ 最要紧的是**别把人引到错的方向**：
          软限流的判据是搜索接口返回 `status_code:0 + data:[]`（假成功空数据），
          或干脆捕获不到响应；此时**账号登录态是好的**
          （同一 profile 内可正常拉取 user/profile/self、收藏、设置等）。
          因此"重新扫码"对软限流**无效**。真正有效的只有:
          (a) 长时间冷却（45 分钟不保证恢复，实测不足）、
          (b) 降载（减关键词 + 拉大间隔）、
          (c) 换设备/网络出口。
        """
        base = (
            f"抖音风控拦截（关键词 '{keyword}'）: "
            "搜索接口返回 status_code:0 但 data 为空数组（假成功空数据），"
            "账号登录态本身有效，属平台侧限流而非凭证失效。"
        )
        if escalated:
            base += " 且已升级为『验证码中间页』。"
        base += (
            " ⚠️ 重新扫码对软限流无效。有效手段只有: "
            "① 长时间冷却（≥数小时）；"
            "② 降载（减关键词、拉大关键词间隔）；"
            "③ 更换设备/网络出口。"
        )
        return base

    def _search_videos(self, keyword: str) -> list[dict]:
        """综合搜索(默认 tab)→ 拦截 general/search/single 响应 → 视频列表。

        注意:必须用默认综合通道;?type=video 的 search/item 通道登录态下
        也被风控软拦(200 + 空列表,2026-09-15 实证)。
        """
        payloads: list[dict] = []

        def handler(r):
            if ("/aweme/v1/web/general/search/single/" in r.url
                    and len(payloads) < SEARCH_MAX_RESPONSES):
                try:
                    payloads.append(r.json())
                except Exception:
                    pass

        self._page.on("response", handler)
        try:
            self._page.goto(f"https://www.douyin.com/search/{quote(keyword)}",
                            timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            logger.warning("Douyin search goto: %s", str(e)[:100])
        # 滚动优先(依据见文件顶部 SEARCH_SCROLLS 的实测): 响应由滚动触发,
        # 先滚既拿到数据又省掉干等; 捕获够 SEARCH_MAX_RESPONSES 就停手。
        for _ in range(SEARCH_SCROLLS):
            if len(payloads) >= SEARCH_MAX_RESPONSES:
                break
            self._page.evaluate("window.scrollBy(0, 1200)")
            time.sleep(SEARCH_SCROLL_INTERVAL)
        # 兜底: 只有"滚完仍无响应"才短等一次 —— 不再做"先等 20s 再看"。
        if not payloads:
            deadline = time.time() + SEARCH_FOLLOWUP_WAIT
            while time.time() < deadline and not payloads:
                time.sleep(0.5)
        self._page.remove_listener("response", handler)

        if self._check_captcha():
            raise DouyinBlocked(self._diagnose_block(keyword, escalated=True))

        seen: set[str] = set()
        out: list[dict] = []
        for d in payloads:
            for item in d.get("data") or []:
                aw = item.get("aweme_info") or (item if item.get("aweme_id") else None)
                if not aw or not aw.get("aweme_id") or aw["aweme_id"] in seen:
                    continue
                seen.add(aw["aweme_id"])
                st = aw.get("statistics") or {}
                out.append({
                    "_kind": "video",
                    "id": aw["aweme_id"],  # batch_collect 去重/主键读取此字段
                    "aweme_id": aw["aweme_id"],
                    "href": f"https://www.douyin.com/video/{aw['aweme_id']}",
                    "desc": (aw.get("desc") or "").strip(),
                    "author_name": ((aw.get("author") or {}).get("nickname") or "")[:50],
                    "like_count": int(st.get("digg_count") or 0),
                    "comment_count_hint": int(st.get("comment_count") or 0),
                    "create_time": _ts_to_str(aw.get("create_time")),
                })

        # ⚠️ 软限流必须**显式报错**，不能返回空列表。
        # 返回空会被上层 _decide_status 归成 `empty`（"搜索成功但平台确实没内容"），
        # 把"被限流"误报成"没语料"，正好掩盖真实故障 —— 本项目的静默失效就是这样发生的。
        # 判据: 一条响应都没捕获到，或所有响应都是 `status_code:0 + data:[]`（假成功空数据）。
        if not out:
            soft_blocked = (not payloads) or all(
                (d.get("status_code") == 0 and not (d.get("data") or []))
                for d in payloads
            )
            if soft_blocked:
                raise DouyinBlocked(self._diagnose_block(keyword, escalated=False))

        logger.info("  Douyin: %d videos for '%s'", len(out), keyword)
        return out

    def _scrape_comments(self, video: dict, keyword: str) -> list[dict]:
        """视频页 → 拦截 comment/list 响应(滚动触发翻页)→ 评论列表。"""
        comments: list[dict] = []
        seen: set[str] = set()

        def handler(r):
            if "/aweme/v1/web/comment/list/" not in r.url:
                return
            try:
                d = r.json()
                for c in d.get("comments") or []:
                    cid = c.get("cid")
                    text = (c.get("text") or "").strip()
                    if not cid or not text or cid in seen:
                        continue
                    seen.add(cid)
                    comments.append({
                        "_kind": "comment",
                        "id": f"{video['aweme_id']}:{cid}",  # batch_collect 主键
                        "_cid": str(cid),
                        "text": text[:800],
                        "author_name": ((c.get("user") or {}).get("nickname") or "")[:50],
                        "like_count": int(c.get("digg_count") or 0),
                        "publish_time": _ts_to_str(c.get("create_time")),
                        "ip_location": (c.get("ip_label") or "")[:30],
                        "video": video,
                    })
            except Exception:
                pass

        self._page.on("response", handler)
        try:
            self._page.goto(video["href"], timeout=30000, wait_until="domcontentloaded")
            time.sleep(3.5)
            if self._check_captcha():
                raise DouyinBlocked(self._diagnose_block(video.get("aweme_id", "?"), escalated=True))
            # 滚动评论区触发翻页加载; 连续 EMPTY_SCROLL_LIMIT 次滚不出新评论就提前收尾
            # (2026-09-17): 评论稀疏的视频原来要白等满 COMMENT_SCROLLS×2s, 是拉高
            # 单词语成本的主要尾巴之一。
            _empty_streak = 0
            for _ in range(COMMENT_SCROLLS):
                _before_n = len(comments)
                self._page.evaluate("""
                    const el = document.querySelector('[data-e2e="comment-list"]')
                        || document.scrollingElement;
                    if (el) el.scrollBy(0, 1500); else window.scrollBy(0, 1500);
                """)
                time.sleep(2)
                if len(comments) > _before_n:
                    _empty_streak = 0
                else:
                    _empty_streak += 1
                    if _empty_streak >= EMPTY_SCROLL_LIMIT:
                        break
        finally:
            self._page.remove_listener("response", handler)
        logger.info("  Douyin: %d comments on %s", len(comments), video["aweme_id"])
        return comments

    # ═══════════════════════════════════════════════════════════
    # 归一化 (视频/评论双形态分发)
    # ═══════════════════════════════════════════════════════════

    def normalize(self, raw_item: Any, detail: Optional[dict], keyword: str) -> Optional[dict]:
        if not isinstance(raw_item, dict):
            return None
        if raw_item.get("_kind") == "comment":
            return self._normalize_comment(raw_item, keyword)
        return self._normalize_video(raw_item, keyword)

    def _normalize_video(self, v: dict, keyword: str) -> Optional[dict]:
        desc = (v.get("desc") or "").strip()
        if not desc:
            return None
        return {
            "platform": "douyin",
            "note_id": f"{self.id_prefix}v:{v['aweme_id']}",
            "title": desc[:60],
            "desc": desc[:2000],
            "author_name": v.get("author_name", ""),
            "author_id": "",
            "author_fans": 0,
            "like_count": int(v.get("like_count") or 0),
            "comment_count": int(v.get("comment_count_hint") or 0),
            "collect_count": 0,
            "share_count": 0,
            "tags": [],
            "note_type": "video",
            "publish_time": v.get("create_time", ""),
            "ip_location": "",
            "keyword": keyword,
            "url": v.get("href", ""),
            "desc_length": len(desc),
            "image_count": 0,
            "is_video": True,
            "image_urls": [],
        }

    def _normalize_comment(self, c: dict, keyword: str) -> Optional[dict]:
        text = (c.get("text") or "").strip()
        if len(text) < 4:
            return None
        video = c.get("video", {}) or {}
        vid = video.get("aweme_id", "")
        cid = c.get("_cid") or str(abs(hash(text[:50])) % 10**12)  # 无 cid 时内容哈希兜底
        return {
            "platform": "douyin",
            "note_id": f"{self.id_prefix}c:{vid}:{cid}",
            "title": f"[抖音评论] {video.get('desc', '')[:50]}",
            "desc": text,
            "author_name": c.get("author_name", ""),
            "author_id": "",
            "author_fans": 0,
            "like_count": int(c.get("like_count") or 0),
            "comment_count": 0,
            "collect_count": 0,
            "share_count": 0,
            "tags": [],
            "note_type": "comment",
            "publish_time": c.get("publish_time", ""),
            "ip_location": c.get("ip_location", ""),
            "keyword": keyword,
            "url": video.get("href", ""),
            "desc_length": len(text),
            "image_count": 0,
            "is_video": False,
            "image_urls": [],
        }

    def classify_error(self, exc: Exception) -> str:
        """归一异常类型 → rate_limit / auth / other。

        【关键逻辑】⚠️ 顺序要紧: `DouyinBlocked` 的文案里含"验证码"三个字，
          必须**先**判类型，否则会掉进下面的 `"验证码" in str(exc)` 分支被误判成
          auth（凭证失效），导致告警让用户去重新扫码 —— 而软限流重扫无效。
        """
        if isinstance(exc, DouyinBlocked):
            return "rate_limit"
        msg = str(exc).lower()
        if "timeout" in msg:
            return "rate_limit"
        if "验证码" in str(exc):
            return "auth"
        return "other"

    @staticmethod
    def field_mapping() -> dict:
        from .base import FIELD_MAPPING_TABLE

        return FIELD_MAPPING_TABLE.get("douyin", {})
