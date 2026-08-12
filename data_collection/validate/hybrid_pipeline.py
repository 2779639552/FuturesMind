"""
期货社交媒体 — 混合采集管道 (Hybrid Pipeline)
================================================

两层架构:
  Layer 1 (Playwright发现层): 浏览器搜索 → 收集note_id + 基础元数据
  Layer 2 (API拦截深挖层): 拦截XHR响应 → 获取结构化JSON正文 + 评论

核心思路:
  打开小红书搜索结果 → 搜索关键词 → 获取笔记列表 (标题/作者/时间/点赞)
  → 点击笔记卡片打开浮层 → 拦截 /api/sns/web/v1/feed 的API响应
  → 直接从JSON中提取完整正文、标签、评论、互动数据

优势:
  - 不需要逆向X-s签名 (浏览器自动完成)
  - 数据结构完整 (API返回的JSON比DOM提取更可靠)
  - 同一个浏览器会话 (复用登录态)
  - 正文、评论、标签 一次性获取

使用方式:
    python hybrid_pipeline.py                          # 首次运行(需要扫码)
    python hybrid_pipeline.py --no-login               # 使用已保存登录态
    python hybrid_pipeline.py --keywords "螺纹钢" "铁矿"  # 自定义关键词
    python hybrid_pipeline.py --max-depth 10            # 每个关键词深挖10篇
    python hybrid_pipeline.py --filter-futures          # 期货相关性过滤


本文件在"情绪数据生产链"中的角色
--------------------------------
    负责链条最前端的【数据采集】环节 (小红书平台)。
    输出: 原始笔记数据 (标题/正文/作者/互动/标签等), 保存为 JSON 文件。

    说明: 本文件本身不包含 NER 品种识别、情感打分、聚合写盘等后续环节。
          后续环节由以下模块完成 (本文件被调用方引用, 只产出原始数据):
            - batch_collect.py  : 采集后的 NER + 情感 enrich (可选)
            - trend_aggregator.py : 作者影响力加权 + 按品种聚合 → output/trends/
            - generate_tradingagents_sentiment.py : 转成
              ~/.tradingagents/external_data/{品种}_sentiment.json
          【待确认】若您看到的旧版文档称"本文件聚合情感", 那属于历史版本描述;
          当前代码仅做采集与简单过滤。
"""

import argparse  # 【调用包】命令行参数解析 (--keywords/--max-depth等)
import json  # 【调用包】JSON读写 (登录态/API响应/结果落盘)
import logging  # 【调用包】日志记录 (采集过程状态)
import random  # 【调用包】随机延时/滚动步长, 模拟真人操作
import re  # 【调用包】正则从href/ID中提取note_id
import sys  # 【调用包】sys.path注入工具目录 / 进程退出码
import time  # 【调用包】延时/超时/等待API响应
from dataclasses import dataclass, field  # 【调用包】定义DeepNote数据容器
from datetime import datetime  # 【调用包】结果文件时间戳
from pathlib import Path  # 【调用包】路径操作 (输出目录)

from playwright.sync_api import TimeoutError as PwTimeout, sync_playwright  # 【调用包】Playwright浏览器自动化 (同步API + 页面超时异常)

# --- 复用已有的工具函数 ---
sys.path.insert(0, str(Path(__file__).parent))
from xhs_scraper import (  # 【调用包】复用小红书采集工具 (反检测脚本/输出目录/笔记数据类/时间戳解码/期货过滤)
    ANTI_DETECTION_SCRIPT,
    OUTPUT_DIR,
    STORAGE_STATE_FILE,
    XHSNote,
    decode_objectid_timestamp,
    filter_futures_notes,
    is_futures_related_xhs,
    parse_like_count,
)

logger = logging.getLogger("hybrid.pipeline")

# 小红书 API 端点 (用于拦截)
XHS_API_PATTERNS = {
    "note_detail": "**/api/sns/web/v1/feed**",  # 笔记详情
    "note_comments": "**/api/sns/web/v2/comment/**",  # 评论
    "search_notes": "**/api/sns/web/v1/search/notes",  # 搜索
    "sub_notes": "**/api/sns/web/v1/note/sub**",  # 子笔记
}


@dataclass
class DeepNote:
    """
    【功能】"深度采集"得到的笔记数据结构 (API 级别数据)。
            相比搜索阶段返回的 XHSNote, 字段更完整: 包含正文全文 desc、
            评论列表 comments、作者粉丝数 author_followers 等。
    【关键逻辑】本类只是一个"数据容器": 通过 Python dataclass 装饰器
            自动生成 __init__ / __repr__ 等方法; 每个字段带默认值,
            便于创建后逐字段赋值。解析 API 响应时会逐字段填充到这里。
    """

    note_id: str = ""
    title: str = ""
    desc: str = ""  # 正文全文
    author_name: str = ""
    author_id: str = ""
    author_followers: int = 0  # 作者粉丝数
    like_count: int = 0
    comment_count: int = 0
    collect_count: int = 0
    share_count: int = 0
    tags: list = field(default_factory=list)
    topics: list = field(default_factory=list)  # 话题
    images: list = field(default_factory=list)  # 图片URL列表
    note_type: str = ""  # normal / video
    publish_time: str = ""
    ip_location: str = ""  # IP属地
    keyword: str = ""  # 搜索关键词
    comments: list = field(default_factory=list)  # 评论列表 (前20条)
    raw_api_response: dict = field(default_factory=dict)


class HybridPipeline:
    """
    【功能】混合采集管道 (小红书)。两层架构:
      - Phase 1 发现层 (Playwright 浏览器模拟): 打开搜索页, 滚动加载,
        从搜索卡片 DOM 中解析 note_id + 基础元数据。
      - Phase 2 深挖层 (API 拦截): 打开笔记详情页, 拦截浏览器发出的
        XHR 请求 (笔记详情/评论 API), 直接从 JSON 响应中提取完整数据。
    【关键逻辑】
      - 用"浏览器帮我们完成签名/登录", 从而绕开 X-s 签名逆向。
      - API 响应缓存在 self.api_cache, 解析时再取用。
      - 若 API 拦截失败, 提供 _extract_from_dom_fallback 的 DOM 降级方案。
    """

    def __init__(self, headless: bool = False):
        # 是否无头模式 (True 则浏览器不显示窗口, 适合服务器/CI)
        self.headless = headless
        # 以下对象在 start() 中创建, 这里先占位:
        self.playwright = None  # Playwright 运行时句柄
        self.browser = None     # Chromium 浏览器实例
        self.page = None        # 当前活动标签页
        self.context = None     # 浏览器上下文 (承载登录态/Cookie)

        # API响应拦截缓存: 拦截到的接口 JSON 按名称暂存, 供后续解析
        self.api_cache = {}
        self.intercept_enabled = False  # 防止重复绑定拦截器

    # ================================================================
    # Phase 0: 启动和登录
    # ================================================================

    def start(self, need_login: bool = True):
        """
        【功能】启动 Playwright 浏览器、创建带登录态的上下文，并可选扫码登录。
        【参数】need_login: bool, 是否需要在启动后执行登录 (True 则调用 _login)。
        【返回】无。
        【关键逻辑】
          1. sync_playwright().start() 启动 Playwright 运行时 (同步模式)。
          2. chromium.launch 打开浏览器, 传入反自动化检测参数与慢动作。
          3. new_context 创建独立上下文: 设置视口/UA/locale/timezone 模拟真实用户。
          4. 若已存在登录态文件 STORAGE_STATE_FILE, 则加载 (免重复扫码)。
          5. add_init_script 在页面加载前注入反检测脚本。
          6. need_login 时调用 _login() 完成扫码登录。
        """
        logger.info("Starting browser...")
        self.playwright = sync_playwright().start()  # 【调用函数】启动Playwright运行时 (同步模式)

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
        if not self.headless:
            launch_args.append("--window-size=1280,900")

        self.browser = self.playwright.chromium.launch(  # 【调用函数】启动Chromium浏览器 (反自动化检测参数 + 慢动作)
            headless=self.headless,
            args=launch_args,
            slow_mo=50,
        )

        context_options = {
            "viewport": {"width": 1280, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }

        # 加载已保存的登录态
        if STORAGE_STATE_FILE.exists():
            try:
                with open(STORAGE_STATE_FILE) as f:
                    context_options["storage_state"] = json.load(f)  # 【调用函数】读取已保存的登录态 (免重复扫码)
                logger.info("Loaded saved login state")
            except Exception:
                pass

        self.context = self.browser.new_context(**context_options)  # 【调用函数】创建浏览器上下文 (视口/UA/时区模拟 + 登录态)
        self.context.add_init_script(ANTI_DETECTION_SCRIPT)  # 【调用函数】注入反自动化检测JS脚本 (页面加载前执行)
        self.page = self.context.new_page()  # 【调用函数】创建页面标签页 (当前活动页)

        # 登录
        if need_login:
            self._login()

    def _login(self, timeout: int = 180):
        """
        【功能】打开小红书首页, 等待用户扫码完成登录。
        【参数】timeout: int, 最多等待多少秒 (默认 180 秒)。
        【返回】无。登录成功后调用 _save_state() 保存登录态到本地文件。
        【关键逻辑】
          - 打开 explore 首页后, 轮询检查页面是否出现"搜索框"或"用户头像"。
          - 出现即视为已登录 (说明 Cookie/登录态有效)。
          - 若超时仍未检测到, 只告警不报错 (保留浏览器供人工处理)。
        """
        logger.info("Opening homepage...")
        self.page.goto(  # 【调用函数】跳转小红书首页 (等待DOM加载, 触发登录检测)
            "https://www.xiaohongshu.com/explore", timeout=30000, wait_until="domcontentloaded"
        )
        time.sleep(2)

        # 检查是否已登录
        search_input = self.page.query_selector(
            'input[placeholder*="search"], input[placeholder*="Search"], '
            '#search-input, [class*="search-input"]'
        )
        if search_input:
            logger.info("Already logged in")
            self._save_state()
            return

        print("\n" + "=" * 50)
        print("Please scan QR code in browser to login")
        print(f"   Timeout: {timeout}s")
        print("=" * 50 + "\n")

        wait_start = time.time()
        while time.time() - wait_start < timeout:
            time.sleep(2)
            search_input = self.page.query_selector(
                'input[placeholder*="search"], input[placeholder*="Search"], '
                '#search-input, [class*="search-input"]'
            )
            # 也检查用户头像
            avatar = self.page.query_selector(
                '.avatar, [class*="avatar"], img[class*="avatar"], '
                '.side-bar-user img, [class*="user-avatar"]'
            )
            if search_input or avatar:
                elapsed = time.time() - wait_start
                logger.info(f"Login success! ({elapsed:.0f}s)")
                self._save_state()
                return

        logger.warning(f"Login timeout ({timeout}s)")

    def _save_state(self):
        """
        【功能】把当前浏览器的登录态 (Cookie/LocalStorage 等) 序列化保存到本地文件。
        【参数】无。
        【返回】无。
        【关键逻辑】下次启动时 start() 会读取该文件自动恢复登录, 从而免扫码。
        """
        try:
            state = self.context.storage_state()  # 【调用函数】获取浏览器登录态 (Cookie/LocalStorage)
            STORAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STORAGE_STATE_FILE, "w") as f:
                json.dump(state, f)  # 【调用函数】登录态序列化落盘 (下次启动免扫码)
            logger.info(f"State saved to {STORAGE_STATE_FILE}")
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

    # ================================================================
    # Phase 1: 搜索发现层 (Playwright)
    # ================================================================

    def discover(self, keyword: str, max_scroll: int = 5) -> list[XHSNote]:
        """
        【功能】Phase 1 发现层: 按关键词打开小红书搜索结果页, 滚动加载,
                并解析搜索卡片, 收集笔记的基础数据。
        【参数】
          keyword: str, 搜索关键词 (如 "螺纹钢期货")。
          max_scroll: int, 最多向下滚动几次以触发更多内容加载 (默认 5)。
        【返回】list[XHSNote]: 一批笔记的基础信息 (note_id/title/author/like 等)。
        【关键逻辑】
          1. 构造搜索 URL (type=51 表示笔记类型, sort=time 按时间排序)。
          2. _human_scroll() 模拟真人分步滚动, 让页面懒加载更多卡片。
          3. 用 CSS 选择器批量取到笔记卡片元素, 逐张 _parse_search_card 解析。
          4. 用 seen_ids 集合按 note_id 去重, 最多处理前 40 张卡片。
        """
        notes = []
        logger.info(f"Discovering: '{keyword}'")

        search_url = (
            f"https://www.xiaohongshu.com/search_result?keyword={keyword}&type=51&sort=time"
        )
        self.page.goto(search_url, timeout=20000, wait_until="domcontentloaded")  # 【调用函数】跳转小红书搜索页 (type=51笔记, sort=time按时间排序)
        time.sleep(random.uniform(2, 4))

        # 滚动加载: 每滚一屏等待 1~2.5 秒, 模拟人类浏览速度
        for _ in range(max_scroll):
            self._human_scroll()
            time.sleep(random.uniform(1, 2.5))

        # 提取笔记元素
        note_elements = self.page.query_selector_all(  # 【调用函数】批量定位搜索结果卡片DOM元素 (多组选择器兜底)
            '.note-item, [class*="noteItem"], section.note-item, '
            'a[href*="/explore/"][class*="note"], '
            'div[class*="feeds-page"] section, '
            'div[class*="search"] section'
        )

        logger.debug(f"  Found {len(note_elements)} potential note elements")

        seen_ids = set()
        for elem in note_elements[:40]:  # 【变量】单关键词最多解析前40张卡片 (控制耗时与请求量)
            try:
                note = self._parse_search_card(elem, keyword)
                if note and note.note_id and note.note_id not in seen_ids:
                    seen_ids.add(note.note_id)
                    notes.append(note)
            except Exception:
                pass

        logger.info(f"  Discovered {len(notes)} notes for '{keyword}'")
        return notes

    def _parse_search_card(self, elem, keyword: str) -> XHSNote | None:
        """
        【功能】从单张搜索卡片 DOM 元素中提取笔记基础数据。
        【参数】
          elem: Playwright 的 ElementHandle, 代表搜索结果页里的一张卡片。
          keyword: str, 本次搜索用的关键词, 会记入笔记对象。
        【返回】XHSNote | None: 解析成功返回笔记对象, 无法取得 note_id 则返回 None。
        【关键逻辑】
          - 优先从 <a href="/explore/{24位hex}"> 提取 note_id; 否则尝试 data-id 属性。
          - 笔记发布时间无法从卡片直接看到, 用 note_id 自带的雪花时间戳解码。
          - 标题/作者/点赞数分别用多组 CSS 选择器兜底 (页面结构可能变化)。
        """
        note = XHSNote(keyword=keyword)

        # URL和note_id
        link = elem.query_selector('a[href*="/explore/"], a[href*="/discovery/"]')
        if not link:
            link = elem
        href = link.get_attribute("href") or ""
        note_id_match = re.search(r"/(?:explore|discovery/item)/([a-f0-9]{24})", href)  # 【调用函数】正则从href提取24位十六进制note_id
        if note_id_match:
            note.note_id = note_id_match.group(1)
            note.url = f"https://www.xiaohongshu.com/explore/{note.note_id}"
        else:
            # 尝试 data-id 属性
            data_id = elem.get_attribute("data-id") or ""
            if re.match(r"^[a-f0-9]{24}$", data_id):
                note.note_id = data_id
                note.url = f"https://www.xiaohongshu.com/explore/{note.note_id}"
            else:
                return None

        # 时间戳
        note.publish_time = decode_objectid_timestamp(note.note_id) or ""  # 【调用函数】解码note_id雪花时间戳 → 发布时间 (卡片DOM无直接时间字段)

        # 标题
        for sel in [".title", '[class*="title"]', 'span[class*="title"]', "h3"]:
            el = elem.query_selector(sel)
            if el:
                note.title = (el.inner_text() or "").strip()
                if len(note.title) > 3:
                    break

        # 作者
        for sel in [
            ".author .name",
            ".name",
            '[class*="author"] [class*="name"]',
            ".nickname",
            '[class*="nickname"]',
        ]:
            el = elem.query_selector(sel)
            if el:
                note.author_name = (el.inner_text() or "").strip()
                if note.author_name:
                    break

        # 互动数据
        count_els = elem.query_selector_all(
            '.count, [class*="count"], [class*="like"] [class*="count"], span[class*="stat"]'
        )
        if len(count_els) >= 1:
            note.like_count = (count_els[0].inner_text() or "").strip()
            note.like_count_int = parse_like_count(note.like_count)  # 【调用函数】"1.2万"等中文点赞数 → 整数, 供排序使用

        # 封面
        img = elem.query_selector("img")
        if img:
            note.cover_url = img.get_attribute("src") or ""

        # 笔记类型
        if elem.query_selector('[class*="video"], [class*="play"], .duration'):
            note.note_type = "video"
        else:
            note.note_type = "normal"

        # 如果没有标题，尝试获取全部可见文本
        if not note.title:
            text = (elem.inner_text() or "").strip()
            lines = [line for line in text.split("\n") if len(line) > 5]
            note.title = lines[0][:100] if lines else ""

        return note

    def _human_scroll(self):
        """
        【功能】用"小步多次 + 随机停顿"的方式模拟人类滚动页面, 降低被反爬识别概率。
        【参数】无。
        【返回】无。
        【关键逻辑】把一次大距离滚动拆成 3~6 小步, 每步之间随机 sleep 0.1~0.3 秒。
        """
        dist = random.randint(400, 900)
        steps = random.randint(3, 6)
        for _ in range(steps):
            self.page.evaluate(f"window.scrollBy(0, {dist // steps})")  # 【调用函数】在页面执行JS滚动指令 (小步滚动)
            time.sleep(random.uniform(0.1, 0.3))

    # ================================================================
    # Phase 2: API拦截深挖层
    # ================================================================

    def setup_api_interceptor(self):
        """
        【功能】给当前页面绑定"响应拦截器": 监听页面发出的每一个网络响应,
                命中小红书 API 的响应 JSON 会被暂存到 self.api_cache。
        【参数】无。
        【返回】无。
        【关键逻辑】
          - 用 self.intercept_enabled 做幂等保护, 避免重复绑定多个拦截器。
          - on_response 内按 URL 特征区分: 笔记详情/评论/搜索 三类 API。
          - 只对 edith.xiaohongshu.com 或 www.xiaohongshu.com/api 的请求感兴趣。
          - 非 JSON 或解析失败的响应会被 try/except 静默忽略。
        """
        if self.intercept_enabled:
            return

        def on_response(response):
            """拦截XHS API响应 (每次网络响应到达时被 Playwright 调用)"""
            url = response.url
            try:
                # 只拦截API调用
                if not any(
                    domain in url for domain in ["edith.xiaohongshu.com", "www.xiaohongshu.com/api"]
                ):
                    return

                # 匹配笔记详情API (打开详情页时浏览器会请求它)
                if "/api/sns/web/v1/feed" in url or "/api/sns/web/v1/note/" in url:
                    body = response.json()  # 【调用函数】解析拦截到的API响应为JSON
                    self.api_cache["note_detail"] = body
                    logger.debug("Intercepted note detail API")

                # 匹配评论API
                elif "/api/sns/web/v2/comment" in url:
                    body = response.json()  # 【调用函数】解析拦截到的API响应为JSON
                    cache_key = f"comment_{url}"
                    self.api_cache[cache_key] = body
                    logger.debug("Intercepted comment API")

                # 匹配搜索API (可以获取更丰富的搜索结果)
                elif "/api/sns/web/v1/search/notes" in url:
                    body = response.json()  # 【调用函数】解析拦截到的API响应为JSON
                    self.api_cache["search_results"] = body
                    logger.debug("Intercepted search API")

            except Exception:
                pass  # 非JSON响应忽略

        self.page.on("response", on_response)  # 【调用函数】注册Playwright响应拦截回调 (每次网络响应到达时触发)
        self.intercept_enabled = True
        logger.info("API interceptor enabled")

    def deep_extract_one(self, note_id: str) -> DeepNote | None:
        """
        【功能】Phase 2 深挖层: 打开单条笔记详情页, 拦截 API 响应, 提取完整数据。
        【参数】note_id: str, 24 位十六进制笔记 ID。
        【返回】DeepNote | None: 成功返回完整笔记数据, 失败/超时返回 None。
        【关键逻辑】
          1. 先清空缓存里旧的 note_detail, 避免读到上一条笔记的数据。
          2. goto 打开详情页, 等待浏览器自动请求详情 API (最多等 10 秒)。
          3. 若 API 未拦截到 (页面结构变化或未加载), 降级走 DOM 提取。
          4. 最终调用 _parse_api_response 把 JSON 解析为 DeepNote 对象。
        """
        # 清空之前的缓存
        self.api_cache.pop("note_detail", None)

        # 打开详情页
        detail_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        try:
            self.page.goto(detail_url, timeout=20000, wait_until="domcontentloaded")  # 【调用函数】打开笔记详情页 (触发浏览器自动请求详情API)
        except PwTimeout:
            logger.warning(f"Detail page timeout: {note_id[:8]}...")
            return None

        # 等待API响应 (笔记详情通过XHR加载)
        wait_start = time.time()
        timeout = 10  # 最多等10秒
        while "note_detail" not in self.api_cache:
            time.sleep(0.5)
            if time.time() - wait_start > timeout:
                break

        # 如果API没被拦截到，尝试从DOM提取 (降级)
        if "note_detail" not in self.api_cache:
            logger.debug(f"API not intercepted for {note_id[:8]}..., trying DOM")
            return self._extract_from_dom_fallback(note_id)

        # 从API响应中解析
        api_data = self.api_cache["note_detail"]

        try:
            deep = self._parse_api_response(api_data, note_id)
            return deep
        except Exception as e:
            logger.warning(f"Failed to parse API response: {e}")
            return None

    def _parse_api_response(self, data: dict, note_id: str) -> DeepNote:
        """
        【功能】把拦截到的 /api/sns/web/v1/feed 响应 JSON, 解析成 DeepNote 对象。
        【参数】
          data: dict, 笔记详情 API 返回的完整 JSON。
          note_id: str, 笔记 ID, 用于回填。
        【返回】DeepNote: 填充好各字段的笔记对象。
        【关键逻辑】
          - 熟悉小红书 API 结构: {"data": {"items": [{"note_card": {...}}]}}。
          - 逐段提取: 基础字段/作者/互动/标签/话题/图片/视频。
          - 所有字段都用 .get(默认值) 兜底, 字段缺失时不会崩溃。
          - 视频笔记把最高画质 URL 复用进 images 字段, 便于统一保存。
        """
        deep = DeepNote(note_id=note_id)
        deep.raw_api_response = data

        # 小红书API响应结构: {"data": {"items": [{"note_card": {...}}, ...]}}
        items = data.get("data", {}).get("items", [])
        if not items:
            logger.debug("No items in API response")
            return deep

        note_card = items[0].get("note_card", {})
        if not note_card:
            return deep

        # --- 基础字段 ---
        deep.title = note_card.get("title", "") or note_card.get("display_title", "")
        deep.desc = note_card.get("desc", "")
        deep.note_type = note_card.get("type", "normal")  # "normal" or "video"
        deep.publish_time = decode_objectid_timestamp(note_id) or ""

        # IP属地
        deep.ip_location = note_card.get("ip_location", "")

        # --- 作者信息 ---
        user = note_card.get("user", {})
        deep.author_name = user.get("nickname", user.get("nick_name", ""))
        deep.author_id = user.get("user_id", user.get("id", ""))
        deep.author_followers = int(user.get("follower_count", 0) or 0)

        # --- 互动数据 ---
        interact = note_card.get("interact_info", {})
        deep.like_count = int(interact.get("liked_count", 0) or 0)
        deep.collect_count = int(interact.get("collected_count", 0) or 0)
        deep.comment_count = int(interact.get("comment_count", 0) or 0)
        deep.share_count = int(interact.get("share_count", 0) or 0)

        # --- 标签 ---
        tag_list = note_card.get("tag_list", [])
        for tag in tag_list:
            tag_name = tag.get("name", "")
            if tag_name:
                deep.tags.append(tag_name)

        # --- 话题 ---
        topic_list = note_card.get("topic_list", []) or note_card.get("topics", [])
        for topic in topic_list:
            topic_name = topic.get("name", "") or topic.get("topic_name", "")
            if topic_name:
                deep.topics.append(topic_name)

        # --- 图片 ---
        image_list = note_card.get("image_list", [])
        for img in image_list:
            img_url = ""
            # 尝试多种URL字段
            for key in ["url_default", "url", "trace_id", "original"]:
                img_info = img.get(key, {}) if isinstance(img, dict) else {}
                if isinstance(img_info, dict):
                    img_url = img_info.get("url", "")
                elif isinstance(img_info, str) and img_info.startswith("http"):
                    img_url = img_info
                if img_url:
                    break
            if not img_url and isinstance(img, dict):
                # 直接尝试URL
                for key in img:
                    val = img[key]
                    if isinstance(val, str) and val.startswith("http"):
                        img_url = val
                        break
            if img_url:
                deep.images.append(img_url)

        # --- 视频 ---
        if deep.note_type == "video":
            video = note_card.get("video", {})
            video_info = video.get("media", {}).get("stream", {})
            # 取最高画质
            for quality in ["h264", "h265"]:
                for res in ["1080p", "720p", "480p"]:
                    stream = video_info.get(quality, {}).get(res, {})
                    master_url = stream.get("master_url", "")
                    if master_url:
                        deep.images.append(master_url)  # 复用images字段存视频URL
                        break
                if deep.images:
                    break

        logger.debug(
            f"Deep: {note_id[:8]}... "
            f"desc={len(deep.desc)}chars "
            f"tags={len(deep.tags)} "
            f"likes={deep.like_count} "
            f"author={deep.author_name}"
        )

        return deep

    def _extract_from_dom_fallback(self, note_id: str) -> DeepNote | None:
        """
        【功能】当 API 拦截不到时, 改用"直接读页面 DOM"的降级方案提取数据。
        【参数】note_id: str, 笔记 ID。
        【返回】DeepNote | None: 至少提取到正文或作者才返回对象, 否则 None。
        【关键逻辑】
          - 等待 2 秒让页面渲染完成, 再用多组 CSS 选择器去匹配正文/互动/作者。
          - 这是兜底方案, 数据往往没有 API 方式完整 (拿不到评论、粉丝数等)。
        """
        deep = DeepNote(note_id=note_id)
        deep.publish_time = decode_objectid_timestamp(note_id) or ""

        # 等待页面渲染
        time.sleep(2)

        # 正文
        for sel in ["#detail-desc", ".note-text", '[class*="noteText"]', ".desc", ".note-content"]:
            el = self.page.query_selector(sel)
            if el:
                text = (el.inner_text() or "").strip()
                if len(text) > 20:
                    deep.desc = text[:3000]
                    break

        # 互动数据
        for sel in [".interact-item", '[class*="interact"] [class*="item"]']:
            els = self.page.query_selector_all(sel)
            if len(els) >= 1:
                deep.like_count = parse_like_count(els[0].inner_text() or "")
            if len(els) >= 2:
                deep.collect_count = parse_like_count(els[1].inner_text() or "")
            if len(els) >= 3:
                deep.comment_count = parse_like_count(els[2].inner_text() or "")
            break

        # 作者
        link = self.page.query_selector('a[href*="/user/profile/"]')
        if link:
            href = link.get_attribute("href") or ""
            uid = re.search(r"/user/profile/([a-f0-9]{24})", href)
            if uid:
                deep.author_id = uid.group(1)
            author_el = link.query_selector('[class*="name"], [class*="nickname"]')
            if author_el:
                deep.author_name = (author_el.inner_text() or "").strip()

        if deep.desc or deep.author_name:
            logger.debug(f"DOM fallback: {note_id[:8]}... desc={len(deep.desc)}chars")
            return deep
        return None

    def deep_extract_batch(self, note_ids: list[str], max_depth: int = 10) -> list[DeepNote]:
        """
        【功能】批量深挖: 对一列 note_id 逐个调用 deep_extract_one 提取完整数据。
        【参数】
          note_ids: list[str], 待深挖的笔记 ID 列表。
          max_depth: int, 最多深挖前多少条 (默认 10)。
        【返回】list[DeepNote]: 成功提取到的笔记列表 (失败条目不包含)。
        【关键逻辑】
          - 先 setup_api_interceptor() 确保拦截器已就绪。
          - 每条之间随机 sleep 2~4 秒, 避免高频访问触发反爬。
          - 单条失败不影响整体, 只记 warning 继续下一条。
        """
        self.setup_api_interceptor()
        results = []

        ids_to_fetch = note_ids[:max_depth]  # 【变量】只深挖前max_depth条 (控制详情API请求量)
        logger.info(f"Deep extracting {len(ids_to_fetch)} notes...")

        for i, nid in enumerate(ids_to_fetch):
            logger.info(f"  [{i + 1}/{len(ids_to_fetch)}] {nid[:8]}...")
            try:
                deep = self.deep_extract_one(nid)
                if deep:
                    results.append(deep)
                    status = "OK" if deep.desc else "no_content"
                    print(
                        f"  [{i + 1}/{len(ids_to_fetch)}] {nid[:8]}... {status} "
                        f"(desc={len(deep.desc)}c, tags={len(deep.tags)}, likes={deep.like_count})"
                    )
                else:
                    print(f"  [{i + 1}/{len(ids_to_fetch)}] {nid[:8]}... FAILED")
            except Exception as e:
                logger.warning(f"Deep extract failed for {nid[:8]}...: {e}")
                print(f"  [{i + 1}/{len(ids_to_fetch)}] {nid[:8]}... ERROR: {e}")

            # 避免触发反爬
            time.sleep(random.uniform(2, 4))

        logger.info(f"Deep extraction done: {len(results)}/{len(ids_to_fetch)} successful")
        return results

    # ================================================================
    # Phase 3: 整合运行
    # ================================================================

    def run(
        self,
        keywords: list[str],
        max_depth: int = 10,
        filter_futures: bool = True,
        need_login: bool = True,
    ) -> dict:
        """
        【功能】完整运行混合采集管道, 串起"发现 → 深挖 → 过滤排序"三阶段。
        【参数】
          keywords: list[str], 要搜索的关键词列表 (可多个)。
          max_depth: int, 每个关键词对应的全部笔记中, 最多深挖多少条。
          filter_futures: bool, 是否做期货相关性过滤 (默认 True)。
          need_login: bool, 是否需要在启动时登录 (默认 True)。
        【返回】dict, 形如:
          {
            "discovered": list[XHSNote],   # Phase 1 发现的全部笔记(已去重+过滤)
            "deep_notes":  list[DeepNote],  # Phase 2 深挖成功且通过期货过滤的笔记
            "stats": { ...统计字段... },
          }
        【关键逻辑】
          Phase 1: discover() 搜索发现 → filter_futures_notes() 初筛 → 去重+按点赞降序。
          Phase 2: deep_extract_batch() 对高互动笔记做 API 拦截深挖。
          Phase 3: 对深挖结果用 is_futures_related_xhs() 做更精准的相关性判断。
          注意: 本方法只产出"原始笔记数据", 不含情感打分/聚合写盘。
        """
        # ===== 主流程开始 =====
        self.start(need_login=need_login)

        all_discovered = []  # Phase 1 结果 (基础笔记数据)
        all_deep = []  # Phase 2 结果 (API级完整笔记数据)

        try:
            # ---------------- Phase 1: 发现 ----------------
            print("\n" + "=" * 60)
            print("PHASE 1: Discovery (Playwright Search)")
            print("=" * 60)

            # 遍历每个关键词, 各搜索一次, 合并所有发现结果
            for kw in keywords:
                notes = self.discover(kw, max_scroll=5)
                all_discovered.extend(notes)
                print(f"  '{kw}': {len(notes)} notes")

            print(f"\nPhase 1 total: {len(all_discovered)} notes discovered")

            # 期货过滤（在发现阶段先做一次初筛，减少深挖量）
            # filter_futures_notes 来自 xhs_scraper, 按标题/标签等判断是否和期货相关
            if filter_futures:
                before = len(all_discovered)
                all_discovered = filter_futures_notes(all_discovered)  # 【调用函数】跨模块(xhs_scraper): 按标题/标签初筛期货相关笔记
                print(f"Futures filter (Phase 1): {before} -> {len(all_discovered)} notes")

            # 去重 + 按点赞数排序（优先深挖高互动的笔记）
            # 用 note_id 去重, 再按 like_count_int 从高到低排序,
            # 让高互动的笔记排在前面, 优先进入深挖(价值更高)。
            seen = set()
            unique_notes = []
            for n in all_discovered:
                if n.note_id not in seen:
                    seen.add(n.note_id)
                    unique_notes.append(n)
            unique_notes.sort(key=lambda n: n.like_count_int, reverse=True)  # 【变量】按点赞数降序, 让高互动笔记优先进入深挖

            # ---------------- Phase 2: 深挖 ----------------
            print("\n" + "=" * 60)
            print("PHASE 2: Deep Extraction (API Intercept)")
            print("=" * 60)

            note_ids = [n.note_id for n in unique_notes]
            all_deep = self.deep_extract_batch(note_ids, max_depth=max_depth)

            # ---------------- Phase 3: 最终过滤和排序 ----------------
            if filter_futures:
                # 在深度数据上做更精准的期货相关性判断
                # 此时能拿到完整正文 desc + 标签 + 话题, 判断比 Phase 1 更可靠
                deep_filtered = []
                for deep in all_deep:
                    is_rel, conf = is_futures_related_xhs(  # 【调用函数】跨模块(xhs_scraper): 基于完整正文/标签/话题做精准期货相关性判断
                        deep.title or "",
                        deep.desc or "",
                        deep.tags + deep.topics,
                    )
                    if is_rel:
                        deep_filtered.append(deep)
                all_deep = deep_filtered
                print(f"\nFutures filter (Phase 3): {len(all_deep)} deep notes retained")

            # 返回结构化结果, 供调用方保存或继续下游处理
            return {
                "discovered": all_discovered,
                "deep_notes": all_deep,
                "stats": {
                    "keywords_count": len(keywords),
                    "discovered_count": len(all_discovered),
                    "deep_extracted_count": len(all_deep),
                    "deep_success_rate": len(all_deep) / max(len(note_ids), 1),
                },
            }

        finally:
            pass  # 不关闭浏览器，保留给后续使用

    def stop(self):
        """
        【功能】关闭浏览器并停止 Playwright 运行时, 释放资源。
        【参数】无。
        【返回】无。
        【关键逻辑】浏览器/运行时可能尚未创建, 因此用 try/except 分别保护。
        """
        try:
            if self.browser:
                self.browser.close()  # 【调用函数】关闭Chromium浏览器
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()  # 【调用函数】停止Playwright运行时, 释放资源
        except Exception:
            pass

    def save_results(self, results: dict, filename: str = None):
        """
        【功能】把 run() 返回的结果保存为 JSON 文件 (含 metadata/发现列表/深挖列表)。
        【参数】
          results: dict, run() 的返回值。
          filename: str | None, 自定义输出文件名; 缺省自动生成带时间戳的名字。
        【返回】Path: 写入的文件路径。
        【关键逻辑】
          - 输出目录取自 xhs_scraper.OUTPUT_DIR (默认 validate/output/)。
          - desc 只截取前 1000 字符、图片只留前 5 张, 控制文件体积。
          - json.dump(ensure_ascii=False) 保证中文不被转义成 \\uXXXX 形式。
        """
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")  # 【变量】默认输出文件名时间戳
            filename = f"hybrid_results_{ts}.json"

        output = {
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "method": "hybrid_playwright_api_intercept",
                "stats": results.get("stats", {}),
            },
            "discovered": [
                {
                    "note_id": n.note_id,
                    "title": n.title,
                    "author_name": n.author_name,
                    "like_count_int": n.like_count_int,
                    "publish_time": n.publish_time,
                    "url": n.url,
                    "keyword": n.keyword,
                    "note_type": n.note_type,
                }
                for n in results.get("discovered", [])
            ],
            "deep_notes": [
                {
                    "note_id": d.note_id,
                    "title": d.title,
                    "desc": d.desc[:1000] if d.desc else "",  # 【变量】正文截断前1000字符控制文件体积
                    "author_name": d.author_name,
                    "author_id": d.author_id,
                    "author_followers": d.author_followers,
                    "like_count": d.like_count,
                    "comment_count": d.comment_count,
                    "collect_count": d.collect_count,
                    "share_count": d.share_count,
                    "tags": d.tags,
                    "topics": d.topics,
                    "images": d.images[:5],  # 【变量】图片只保留前5张控制文件体积
                    "note_type": d.note_type,
                    "publish_time": d.publish_time,
                    "ip_location": d.ip_location,
                    "keyword": d.keyword,
                }
                for d in results.get("deep_notes", [])
            ],
        }

        filepath = OUTPUT_DIR / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)  # 【调用函数】结果JSON落盘 (ensure_ascii=False保留中文)

        logger.info(f"Results saved to: {filepath}")
        return filepath


def print_summary(results: dict):
    """
    【功能】在终端打印本次采集的摘要信息 (关键词数/发现数/深挖数/成功率)。
    【参数】results: dict, run() 的返回值。
    【返回】无。
    【关键逻辑】只读 stats 和 deep_notes 字段做展示, 不修改任何数据。
    """
    stats = results.get("stats", {})
    results.get("discovered", [])
    deep = results.get("deep_notes", [])

    print("\n" + "=" * 60)
    print("HYBRID PIPELINE RESULTS")
    print("=" * 60)
    print(f"  Keywords:      {stats.get('keywords_count', 0)}")
    print(f"  Discovered:    {stats.get('discovered_count', 0)} notes")
    print(f"  Deep extracted:{stats.get('deep_extracted_count', 0)} notes")
    print(f"  Success rate:  {stats.get('deep_success_rate', 0) * 100:.0f}%")

    if deep:
        print("\n  Top deep notes:")
        for i, d in enumerate(deep[:5], 1):
            title = (d.title or d.desc or "")[:80].replace("\n", " ")
            desc_len = len(d.desc) if d.desc else 0
            print(f"  {i}. @{d.author_name} | {title}")
            print(
                f"     L{d.like_count} C{d.comment_count} | {desc_len} chars | {len(d.tags)} tags"
            )
            if d.tags:
                print(f"     Tags: {', '.join(d.tags[:8])}")


def main():
    """
    【功能】命令行入口: 解析参数 → 创建管道 → 运行 → 打印摘要 → 保存结果。
    【关键逻辑】
      - 支持 --keywords / --no-login / --headless / --max-depth /
        --filter-futures / --no-filter / --output / --verbose 等参数。
      - --no-filter 会覆盖 --filter-futures (filter_futures = not args.no_filter)。
      - 捕获 KeyboardInterrupt 保证 Ctrl+C 能干净退出。
    """
    parser = argparse.ArgumentParser(
        description="Hybrid Pipeline: Playwright discovery + API intercept deep extraction"
    )
    parser.add_argument(
        "--keywords",
        type=str,
        nargs="+",
        default=["螺纹钢期货", "铁矿石期货", "原油期货", "黄金期货"],
        help="Search keywords",
    )
    parser.add_argument("--no-login", action="store_true", help="Skip login (use saved state)")
    parser.add_argument("--headless", action="store_true", help="Headless mode")
    parser.add_argument(
        "--max-depth", type=int, default=10, help="Max notes to deep-extract per keyword"
    )
    parser.add_argument(
        "--filter-futures", action="store_true", default=True, help="Apply futures relevance filter"
    )
    parser.add_argument("--no-filter", action="store_true", help="Disable futures filter")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()  # 【调用函数】解析命令行参数

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    filter_futures = not args.no_filter

    pipeline = HybridPipeline(headless=args.headless)

    try:
        results = pipeline.run(
            keywords=args.keywords,
            max_depth=args.max_depth,
            filter_futures=filter_futures,
            need_login=not args.no_login,
        )

        print_summary(results)
        output_path = pipeline.save_results(results, args.output)
        print(f"\nDone! Results: {output_path}")

    except KeyboardInterrupt:
        print("\nInterrupted")
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=args.verbose)
    finally:
        pipeline.stop()


if __name__ == "__main__":
    sys.exit(main())
