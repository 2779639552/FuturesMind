"""
小红书期货信息采集器 (Playwright浏览器方案)
=============================================

使用真实浏览器自动化，适合首次验证和小规模采集。
核心流程: 打开浏览器 → 扫码登录 → 保存登录态 → 搜索关键词 → 提取结果

使用方式:
    # 首次运行（需要扫码登录）
    python xhs_scraper.py

    # 使用已保存的登录态（无需扫码）
    python xhs_scraper.py --no-login

    # 搜索自定义关键词
    python xhs_scraper.py --keywords "螺纹钢期货" "铁矿石期货"

    # 获取笔记详情和评论
    python xhs_scraper.py --fetch-details

    # 无头模式（已有登录态时可用）
    python xhs_scraper.py --no-login --headless
"""

import json
import time
import random
import logging
import argparse
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout, Page, Browser

# --- 配置 ---
logger = logging.getLogger("xhs.scraper")

# 输出目录
OUTPUT_DIR = Path("./output")

# 登录状态文件
STORAGE_STATE_FILE = OUTPUT_DIR / "xhs_login_state.json"

# 期货搜索关键词
FUTURES_KEYWORDS = [
    "螺纹钢期货",
    "铁矿石期货",
    "原油期货分析",
    "黄金期货走势",
    "豆粕期货",
    "期货日内交易",
    "期货技术分析",
    "股指期货策略",
    "PTA期货",
    "棕榈油期货",
]

# 反检测脚本（注入浏览器）
ANTI_DETECTION_SCRIPT = """
// 覆盖 webdriver 属性
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
delete Object.defineProperty(navigator, 'webdriver');

// 伪造 plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
        {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''},
        {name: 'Native Client', filename: 'internal-nacl-plugin', description: ''},
    ]
});

// 伪造 languages
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});

// 伪装 chrome runtime
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {}
};

// 覆盖 permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
    Promise.resolve({state: Notification.permission}) :
    originalQuery(parameters)
);

// 覆盖 headless 检测
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
Object.defineProperty(screen, 'colorDepth', {get: () => 24});
"""

# 小红书搜索页URL模板
XHS_SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={keyword}&type=51&sort=time"
XHS_NOTE_URL = "https://www.xiaohongshu.com/explore/{note_id}"
XHS_HOME_URL = "https://www.xiaohongshu.com/explore"


# ============================================================
# 工具函数
# ============================================================

def decode_objectid_timestamp(note_id: str) -> Optional[str]:
    """
    从 MongoDB ObjectId (24位hex) 的前8位解码时间戳。
    小红书 note_id 使用 MongoDB ObjectId 格式，
    前4字节是 Unix timestamp。

    >>> decode_objectid_timestamp("6a55fac30000000006036102")
    "2026-07-14 18:20:19"
    """
    if not note_id or len(note_id) < 8:
        return None
    try:
        ts = int(note_id[:8], 16)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError):
        return None


def parse_like_count(raw: str) -> int:
    """
    清洗点赞数为整数。

    >>> parse_like_count("2562")   → 2562
    >>> parse_like_count("1.2万")  → 12000
    >>> parse_like_count("赞")     → 1
    >>> parse_like_count("")       → 0
    """
    if not raw:
        return 0
    raw = raw.strip()
    if raw == "赞":
        return 1
    if "万" in raw:
        try:
            num = float(raw.replace("万", ""))
            return int(num * 10000)
        except ValueError:
            return 0
    # 去掉非数字字符（除了负号）
    digits = re.sub(r'[^\d]', '', raw)
    try:
        return int(digits) if digits else 1
    except ValueError:
        return 0


def parse_interaction_count(raw: str) -> int:
    """同 parse_like_count，用于评论数、收藏数等"""
    return parse_like_count(raw)


# ============================================================
# 期货相关性过滤
# ============================================================

# 强信号词（命中1个即可判断为期货相关）
STRONG_FUTURES_SIGNALS = {
    "期货", "期市", "上期所", "大商所", "郑商所", "中金所", "上期能源",
    "主力合约", "交割", "开仓", "平仓", "持仓", "多头", "空头",
    "CME", "COMEX", "LME", "CBOT", "ICE",
}

# 品种词（需要命中2个或配合强信号）
VARIETY_NAMES = {
    "螺纹钢", "螺纹", "铁矿石", "铁矿", "热卷", "焦炭", "焦煤", "硅铁", "锰硅",
    "铜", "沪铜", "伦铜", "沪铝", "沪锌", "沪镍", "沪锡", "沪铅",
    "黄金", "沪金", "沪银", "白银", "金价",
    "原油", "油价", "PTA", "甲醇", "PVC", "聚丙烯", "塑料", "橡胶", "沥青",
    "尿素", "纯碱", "玻璃", "乙二醇", "苯乙烯",
    "豆粕", "豆油", "棕榈油", "棕榈", "菜粕", "菜油", "白糖", "棉花", "玉米",
    "生猪", "鸡蛋", "苹果", "红枣", "花生",
    "股指期货", "国债期货", "IF", "IC", "IH", "IM",
    "黑色系", "有色金属", "农产品", "能化", "能源化工",
}

# 期货市场术语（配合品种词使用）
FUTURES_MARKET_TERMS = {
    "回调", "反弹", "突破", "震荡", "涨跌", "涨停", "跌停",
    "做多", "做空", "止损", "止盈", "套利", "对冲", "基差", "升水", "贴水",
    "K线", "均线", "MACD", "布林带", "成交量", "持仓量", "增仓", "减仓",
    "逼仓", "爆仓", "强平", "穿仓",
    "移仓", "换月", "近月", "远月",
    "实盘", "复盘", "盘面", "行情", "走势", "趋势",
    "夜盘", "日盘", "收盘", "开盘",
}

# 噪声检测（这些词出现通常意味着营销/喊单/非期货内容）
NOISE_PATTERNS = [
    r"配资", r"喊单", r"带单", r"暴富", r"稳赚", r"日赚",
    r"恋爱", r"相亲", r"脱单", r"交友",
    r"加微信", r"加V", r"私信", r"咨询\s*免费",
    r"薅羊毛", r"福利", r"抽奖",
]


def is_futures_related_xhs(title: str, desc: str = "", tags: list = None) -> tuple[bool, float]:
    """
    判断小红书笔记是否与期货相关。
    返回 (是否相关, 置信度 0.0-1.0)。
    采用多因素加权评分。
    """
    text = (title + " " + desc).strip()
    if not text:
        return False, 0.0

    # --- 噪声过滤 ---
    for pattern in NOISE_PATTERNS:
        if re.search(pattern, text):
            return False, 0.0

    # --- 多因素评分 ---
    score = 0.0
    max_score = 10.0

    # 1. 强信号词 (每个+3分)
    strong_hits = [s for s in STRONG_FUTURES_SIGNALS if s in text]
    score += len(strong_hits) * 3.0

    # 2. 品种词 (每个+2分)
    variety_hits = [v for v in VARIETY_NAMES if v in text]
    score += len(variety_hits) * 2.0

    # 3. 市场术语 (每个+1分)
    market_hits = [m for m in FUTURES_MARKET_TERMS if m in text]
    score += len(market_hits) * 1.0

    # 4. 标签加分
    if tags:
        tag_text = " ".join(tags)
        score += len([s for s in STRONG_FUTURES_SIGNALS if s in tag_text]) * 2.0

    # 5. 品种不在标题/描述中，但在搜索关键词中（略降）
    # （这个由调用者在构造 Note 时已经知道）

    # 规范化分数
    normalized = min(score / max_score, 1.0)

    # 判定：>0.3 即认为是期货相关
    is_related = normalized >= 0.3

    return is_related, normalized


def filter_futures_notes(notes: list) -> list:
    """从笔记列表中过滤出期货相关的"""
    relevant = []
    for note in notes:
        is_rel, confidence = is_futures_related_xhs(note.title, note.desc, note.tags)
        if is_rel:
            # 把置信度存到 desc 里用于调试（后续可改成独立字段）
            note._futures_confidence = confidence
            relevant.append(note)
    return relevant


@dataclass
class XHSNote:
    """小红书笔记"""
    note_id: str = ""
    title: str = ""
    desc: str = ""
    author_name: str = ""
    author_id: str = ""
    like_count: str = ""
    comment_count: str = ""
    collect_count: str = ""
    tags: list = field(default_factory=list)
    note_type: str = ""       # normal / video
    cover_url: str = ""
    publish_time: str = ""    # 格式化时间字符串
    url: str = ""
    keyword: str = ""         # 搜索用哪个关键词找到的
    # 以下为计算字段
    like_count_int: int = 0
    comment_count_int: int = 0
    collect_count_int: int = 0
    _futures_confidence: float = 0.0  # 期货相关置信度 (0.0~1.0)


class XHSScraper:
    """小红书采集器"""

    def __init__(self, headless: bool = False, debug: bool = False):
        self.headless = headless
        self.debug = debug
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.results: list[XHSNote] = []

    def start(self):
        """启动浏览器"""
        logger.info("启动浏览器...")
        self.playwright = sync_playwright().start()

        # 启动参数
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-features=IsolateOrigins,site-per-process",
        ]

        if not self.headless:
            launch_args.extend([
                "--window-size=1280,900",
                "--window-position=100,100",
            ])

        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=launch_args,
            slow_mo=50,  # 减慢操作速度，更像人类
        )

        # 创建上下文
        context_options = {
            "viewport": {"width": 1280, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "geolocation": {"longitude": 121.47, "latitude": 31.23},  # 上海
            "permissions": ["geolocation"],
        }

        # 加载已保存的登录状态
        if STORAGE_STATE_FILE.exists():
            try:
                with open(STORAGE_STATE_FILE, "r") as f:
                    storage_state = json.load(f)
                context_options["storage_state"] = storage_state
                logger.info("Loaded saved login state")
            except Exception as e:
                logger.warning(f"加载登录状态失败: {e}")

        context = self.browser.new_context(**context_options)

        # 注入反检测脚本
        context.add_init_script(ANTI_DETECTION_SCRIPT)

        self.page = context.new_page()
        logger.info("浏览器已启动")

    def login_if_needed(self, timeout: int = 120):
        """
        检查是否需要登录，如果需要则等待用户扫码。
        返回 True 表示登录成功。
        """
        logger.info("检查登录状态...")
        self.page.goto(XHS_HOME_URL, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

        # 小红书的登录检测：搜索框是否可见，或者是否有登录弹窗
        # 如果包含 "登录" 按钮且不在搜索页面，则未登录
        try:
            # 先检查是否已经有登录弹窗
            login_modal = self.page.query_selector(
                '.login-container, [class*="login"], .login-modal, '
                '.qrcode-img, [class*="qrcode"], img[src*="qrcode"]'
            )
            if login_modal:
                logger.info("检测到登录弹窗，请扫码登录...")
            else:
                # 检查是否已经登录 — 尝试找搜索框
                search_input = self.page.query_selector(
                    'input[placeholder*="搜索"], #search-input, '
                    '[class*="search-input"], [class*="searchInput"]'
                )
                if search_input:
                    logger.info("✅ 已登录（检测到搜索框）")
                    # 点击搜索框，展开搜索页面
                    try:
                        search_input.click()
                        time.sleep(1)
                    except Exception:
                        pass
                    return True

                logger.info("未检测到登录状态，等待登录...")

            # 等待用户扫码登录（检测搜索框出现或登录弹窗消失）
            print("\n" + "=" * 50)
            print("Please scan QR code in browser to login")
            print(f"   Timeout: {timeout}s")
            print("=" * 50 + "\n")

            wait_start = time.time()
            while time.time() - wait_start < timeout:
                time.sleep(2)

                # 检查登录弹窗是否消失
                login_modal = self.page.query_selector(
                    '.login-container, [class*="login-modal"], '
                    '[class*="qrcode"], img[src*="qrcode"]'
                )

                # 检查是否出现了已登录的特征
                search_input = self.page.query_selector(
                    'input[placeholder*="搜索"], #search-input, '
                    '[class*="search-input"], [class*="searchInput"], '
                    '.search-icon, [class*="searchIcon"]'
                )

                if not login_modal and search_input:
                    elapsed = time.time() - wait_start
                    logger.info(f"✅ 登录成功！(耗时 {elapsed:.0f}s)")

                    # 保存登录状态
                    self._save_login_state()
                    return True

                # 也可以检测用户头像/昵称
                user_avatar = self.page.query_selector(
                    '.avatar, [class*="avatar"], .user-avatar, '
                    'img[class*="avatar"], .side-bar-user img'
                )
                if user_avatar and not login_modal:
                    self._save_login_state()
                    return True

            logger.warning(f"⚠️ 登录超时 ({timeout}s)")
            return False

        except Exception as e:
            logger.error(f"登录检测异常: {e}")
            return False

    def _save_login_state(self):
        """保存登录状态到文件"""
        try:
            storage_state = self.page.context.storage_state()
            STORAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STORAGE_STATE_FILE, "w") as f:
                json.dump(storage_state, f)
            logger.info(f"💾 登录状态已保存到: {STORAGE_STATE_FILE}")
        except Exception as e:
            logger.warning(f"保存登录状态失败: {e}")

    def search(self, keyword: str, max_scroll: int = 5) -> list[XHSNote]:
        """
        搜索关键词并提取结果。
        max_scroll: 滚动加载更多内容的次数
        """
        notes = []
        logger.info(f"🔍 搜索: '{keyword}'")

        search_url = XHS_SEARCH_URL.format(keyword=quote(keyword))
        self.page.goto(search_url, timeout=20000, wait_until="domcontentloaded")

        # 等待搜索结果加载
        time.sleep(random.uniform(2, 4))

        # 检查是否有验证码/风控
        if self._check_captcha():
            logger.warning("⚠️ 触发验证码，等待手动处理...")
            time.sleep(10)
            if self._check_captcha():
                logger.error("❌ 验证码未解决，跳过此关键词")
                return notes

        # 滚动加载更多内容
        for i in range(max_scroll):
            self._human_scroll()
            time.sleep(random.uniform(1.5, 3))

            # 检查 "没有更多内容" 的提示
            no_more = self.page.query_selector(
                '.no-more, [class*="noMore"], [class*="no-more"], '
                '.empty-text, [class*="empty"]'
            )
            if no_more:
                logger.info("  已到页面底部")
                break

        # 提取笔记列表
        notes = self._extract_notes_from_page(keyword)

        # 去重
        seen_ids = set()
        unique_notes = []
        for note in notes:
            if note.note_id and note.note_id not in seen_ids:
                seen_ids.add(note.note_id)
                unique_notes.append(note)

        logger.info(f"  获取到 {len(unique_notes)} 条唯一笔记")
        return unique_notes

    def _check_captcha(self) -> bool:
        """检查是否出现验证码"""
        captcha_selectors = [
            '.captcha', '[class*="captcha"]',
            '.verify', '[class*="verify"]',
            '.slider', '[class*="slide"]',
            '#captcha', '#verify',
            'iframe[src*="captcha"]', 'iframe[src*="verify"]',
            '.geetest', '[class*="geetest"]',
            'text=请完成安全验证', 'text=验证',
        ]
        for selector in captcha_selectors:
            try:
                el = self.page.query_selector(selector)
                if el:
                    return True
            except Exception:
                pass
        return False

    def _human_scroll(self):
        """模拟人类滚动"""
        scroll_distance = random.randint(400, 900)
        # 分段滚动
        steps = random.randint(3, 6)
        for _ in range(steps):
            self.page.evaluate(f"window.scrollBy(0, {scroll_distance // steps})")
            time.sleep(random.uniform(0.1, 0.3))

        # 偶尔回滚一点
        if random.random() < 0.2:
            self.page.evaluate(f"window.scrollBy(0, {-random.randint(50, 150)})")
            time.sleep(random.uniform(0.3, 0.6))

    def _extract_notes_from_page(self, keyword: str) -> list[XHSNote]:
        """从当前页面提取笔记列表"""
        notes = []

        # 小红书的搜索结果通常包含在 .note-item 或 section 中
        # 2025年小红书PC端的DOM结构经常变化，使用多选择器策略

        # 方法1: 查找所有笔记卡片
        note_selectors = [
            '.note-item',
            '[class*="noteItem"]',
            'section.note-item',
            '.search-result-item',
            'a[href*="/explore/"]',
            '[class*="feeds-page"] a[href*="/explore/"]',
        ]

        note_elements = []
        for selector in note_selectors:
            elements = self.page.query_selector_all(selector)
            if elements:
                note_elements = elements
                break

        if not note_elements:
            # 尝试更宽泛的选择器
            note_elements = self.page.query_selector_all(
                'div[class*="note"], div[class*="card"], '
                'div[class*="item"], section'
            )

        logger.debug(f"  找到 {len(note_elements)} 个潜在笔记元素")

        for elem in note_elements[:30]:  # 最多处理30个
            try:
                note = self._parse_note_element(elem, keyword)
                if note and note.note_id:
                    notes.append(note)
            except Exception as e:
                logger.debug(f"  解析元素失败: {e}")

        return notes

    def _parse_note_element(self, elem, keyword: str) -> Optional[XHSNote]:
        """解析单个笔记元素"""
        note = XHSNote(keyword=keyword)

        # --- 笔记ID和URL ---
        link = elem.query_selector('a[href*="/explore/"], a[href*="/discovery/"]')
        if not link:
            link = elem if elem.evaluate("el => el.tagName") == "A" else None

        if link:
            href = link.get_attribute("href") or ""
            note.url = urljoin("https://www.xiaohongshu.com", href)
            note_id_match = re.search(r'/explore/([a-f0-9]{24})', href)
            if not note_id_match:
                note_id_match = re.search(r'/discovery/item/([a-f0-9]{24})', href)
            if note_id_match:
                note.note_id = note_id_match.group(1)

        if not note.note_id:
            note_id_attr = elem.get_attribute("data-id") or elem.get_attribute("id") or ""
            if re.match(r'^[a-f0-9]{24}$', note_id_attr):
                note.note_id = note_id_attr

        # --- 从 note_id 解码时间戳 ---
        if note.note_id:
            note.publish_time = decode_objectid_timestamp(note.note_id) or ""

        # --- 标题 ---
        for title_sel in [
            '.title', '[class*="title"]', '.note-title',
            'span[class*="title"]', 'a[class*="title"]',
            'h3', 'h4',
        ]:
            title_el = elem.query_selector(title_sel)
            if title_el:
                note.title = (title_el.inner_text() or "").strip()
                if len(note.title) > 3:
                    break

        # --- 描述文本 ---
        for desc_sel in [
            '.desc', '[class*="desc"]', '.note-desc',
            'p[class*="desc"]', 'span[class*="desc"]',
        ]:
            desc_el = elem.query_selector(desc_sel)
            if desc_el:
                note.desc = (desc_el.inner_text() or "").strip()
                if note.desc:
                    break

        # --- 作者 ---
        for author_sel in [
            '.author .name', '.name', '[class*="author"] [class*="name"]',
            '.nickname', '[class*="nickname"]',
            '.username', '[class*="username"]',
        ]:
            author_el = elem.query_selector(author_sel)
            if author_el:
                note.author_name = (author_el.inner_text() or "").strip()
                if note.author_name:
                    break

        # --- 互动数据 ---
        count_els = elem.query_selector_all(
            '.count, [class*="count"], .like-count, [class*="like-wrapper"] [class*="count"], '
            'span[class*="stat"], .interact-item [class*="count"]'
        )
        if count_els and len(count_els) >= 1:
            note.like_count = (count_els[0].inner_text() or "").strip()
            note.like_count_int = parse_like_count(note.like_count)
        if len(count_els) >= 2:
            note.comment_count = (count_els[1].inner_text() or "").strip()
            note.comment_count_int = parse_interaction_count(note.comment_count)
        if len(count_els) >= 3:
            note.collect_count = (count_els[2].inner_text() or "").strip()
            note.collect_count_int = parse_interaction_count(note.collect_count)

        # --- 如果互动数据未提取到，尝试遍历获取 ---
        if note.like_count_int == 0:
            for selector in [
                '[class*="like"] span', '[class*="like"] [class*="count"]',
                '.like-wrapper [class*="count"]', '.likes [class*="count"]',
            ]:
                el = elem.query_selector(selector)
                if el:
                    raw = (el.inner_text() or "").strip()
                    val = parse_like_count(raw)
                    if val > 0:
                        note.like_count = raw
                        note.like_count_int = val
                        break

        # --- 封面图 ---
        for img_sel in ['img[class*="cover"]', 'img[class*="note"]', 'img']:
            img_el = elem.query_selector(img_sel)
            if img_el:
                src = img_el.get_attribute("src") or ""
                if src and not src.endswith(".svg"):
                    note.cover_url = src
                    break

        # --- 笔记类型 ---
        video_icon = elem.query_selector(
            '[class*="video"], [class*="play"], '
            'svg[class*="play"], .duration'
        )
        note.note_type = "video" if video_icon else "normal"

        # 如果标题和描述都为空，尝试获取元素全部文本
        if not note.title and not note.desc:
            all_text = (elem.inner_text() or "").strip()
            lines = [l for l in all_text.split('\n') if len(l) > 3]
            for line in lines:
                if len(line) > 10:
                    note.title = line[:100]
                    break

        return note

    def get_note_detail(self, note_id: str) -> dict:
        """
        从搜索结果页点击笔记卡片，打开浮层弹窗，提取正文/时间/标签/互动数据。

        小红书搜索结果的详情是浮层弹窗（不是独立页面），
        所以实际上在 search() 中已经可以通过点击卡片打开浮层。
        这里采用两种策略：
        (1) 尝试在当前搜索页通过数据属性找到对应卡片并点击
        (2) 如果不在搜索结果页，则导航到详情页
        """
        detail = {
            "note_id": note_id,
            "desc": "",
            "tags": [],
            "publish_time": "",
            "comment_count": "",
            "comment_count_int": 0,
            "collect_count": "",
            "collect_count_int": 0,
            "like_count": "",
            "like_count_int": 0,
            "author_id": "",
        }

        # 策略(1): 尝试点击搜索结果中的卡片打开浮层
        card_clicked = False
        try:
            # 通过 data-id 或包含 note_id 的链接定位卡片
            card_selectors = [
                f'[data-id="{note_id}"]',
                f'a[href*="{note_id}"]',
                f'div[class*="note"] a[href*="{note_id}"]',
            ]
            for card_sel in card_selectors:
                card = self.page.query_selector(card_sel)
                if card:
                    card.click()
                    card_clicked = True
                    logger.debug(f"Clicked card for {note_id[:8]}...")
                    break

            if card_clicked:
                # 等待浮层弹窗出现
                time.sleep(2)

                # 检查浮层是否已经打开
                overlay_selectors = [
                    '.note-detail-mask', '[class*="noteDetail"]',
                    '.close-circle', '[class*="closeCircle"]',
                    '[class*="note-container"]', '.note-detail',
                ]
                overlay_opened = any(
                    self.page.query_selector(s) for s in overlay_selectors
                )

                if not overlay_opened:
                    logger.debug(f"Overlay did not open for {note_id[:8]}...")
                    card_clicked = False
        except Exception as e:
            logger.debug(f"Card click failed: {e}")
            card_clicked = False

        # 策略(2): 导航到详情页
        if not card_clicked:
            detail_url = XHS_NOTE_URL.format(note_id=note_id)
            try:
                self.page.goto(detail_url, timeout=15000, wait_until="domcontentloaded")
                time.sleep(random.uniform(2, 3))
            except PwTimeout:
                logger.warning(f"Detail page timeout: {note_id[:8]}...")
                return detail

        # --- 统一提取浮层/页面内容 ---
        time.sleep(1)  # 等JS渲染

        # 正文: 在浮层或详情页中查找长文本块
        content_selectors = [
            '#detail-desc', '.desc[class*="detail"]',
            '.note-scroller .note-text', '.note-text',
            '[class*="noteText"]', '.note-content',
            '[class*="note-scroller"] [class*="content"]',
            '[id*="detail"] [class*="desc"]',
        ]
        for sel in content_selectors:
            try:
                el = self.page.query_selector(sel)
                if el:
                    text = (el.inner_text() or "").strip()
                    if len(text) > 30:
                        detail["desc"] = text[:3000]
                        break
            except Exception:
                pass

        # 如果浮层/页面中没找到，尝试整个可见区域的文本
        if not detail["desc"]:
            try:
                # 只取浮层区域的文本（如果卡片点击成功）
                if card_clicked:
                    target = self.page.query_selector(
                        '.note-detail, [class*="noteDetail"], '
                        '[class*="note-container"], [class*="detail-wrapper"]'
                    )
                    body_text = target.inner_text() if target else ""
                else:
                    body_text = self.page.inner_text("body")

                paras = [p.strip() for p in (body_text or "").split("\n")
                         if len(p.strip()) > 30
                         and "你可能感兴趣" not in p
                         and "相关笔记" not in p
                         and "评论" not in p[:5]
                         and "举报" not in p]
                if paras:
                    detail["desc"] = paras[0][:2000]
            except Exception:
                pass

        # 标签: 在已打开的内容区域中搜索
        if card_clicked:
            tag_selectors = [
                '.note-scroller [class*="tag"], .note-scroller a[href*="tag"]',
                '[class*="note-scroller"] [class*="topic"]',
            ]
        else:
            tag_selectors = [
                '[class*="tag"], a[href*="/tag/"], [class*="topic"], [class*="hashtag"]',
            ]
        for sel in tag_selectors:
            try:
                els = self.page.query_selector_all(sel)
                tags = [t.inner_text().strip() for t in els
                        if t.inner_text().strip() and len(t.inner_text().strip()) < 30]
                if tags:
                    detail["tags"] = list(dict.fromkeys(tags))
                    break
            except Exception:
                pass

        # 互动数据: 在浮层底部或页面底部
        interact_containers = []
        if card_clicked:
            interact_containers = [
                '.note-detail .interact-item',
                '[class*="detail"] [class*="interact"] [class*="item"]',
                '[class*="note-container"] [class*="action"] span',
            ]
        if not interact_containers:
            interact_containers = [
                '.interact-item',
                '[class*="interact"] [class*="item"]',
                '[class*="engage"] [class*="item"]',
                '[class*="like-wrapper"]',
            ]
        for sel in interact_containers:
            try:
                els = self.page.query_selector_all(sel)
                if len(els) >= 1:
                    raw = (els[0].inner_text() or "").strip()
                    detail["like_count"] = raw
                    detail["like_count_int"] = parse_like_count(raw)
                if len(els) >= 2:
                    raw = (els[1].inner_text() or "").strip()
                    detail["collect_count"] = raw
                    detail["collect_count_int"] = parse_interaction_count(raw)
                if len(els) >= 3:
                    raw = (els[2].inner_text() or "").strip()
                    detail["comment_count"] = raw
                    detail["comment_count_int"] = parse_interaction_count(raw)
                break
            except Exception:
                pass

        # 作者
        try:
            a_sel = 'a[href*="/user/profile/"]'
            author_link = self.page.query_selector(a_sel)
            if author_link:
                href = author_link.get_attribute("href") or ""
                uid_match = re.search(r'/user/profile/([a-f0-9]{24})', href)
                if uid_match:
                    detail["author_id"] = uid_match.group(1)
        except Exception:
            pass

        # 关闭浮层（如果打开了）
        if card_clicked:
            try:
                close_sel = '.close-circle, [class*="closeCircle"], [class*="close-btn"]'
                close_btn = self.page.query_selector(close_sel)
                if close_btn:
                    close_btn.click()
                    time.sleep(0.5)
                else:
                    # 按 ESC 关闭
                    self.page.keyboard.press("Escape")
                    time.sleep(0.5)
            except Exception:
                pass

        logger.debug(f"Detail: {note_id[:8]}... body={len(detail['desc'])}chars tags={len(detail['tags'])}")
        return detail

    def run(
        self,
        keywords: list[str],
        fetch_details: bool = False,
        need_login: bool = True,
    ) -> list[XHSNote]:
        """运行采集流程"""
        try:
            self.start()

            # 登录
            if need_login:
                logged_in = self.login_if_needed(timeout=180)
                if not logged_in:
                    logger.error("登录失败，无法继续采集")
                    return []

            # 搜索
            all_notes = []
            for kw in keywords:
                notes = self.search(kw, max_scroll=5)
                all_notes.extend(notes)

                # 获取详情（可选）
                if fetch_details:
                    for note in notes[:5]:  # 每个关键词拿前5篇的详情(避免过慢)
                        try:
                            detail = self.get_note_detail(note.note_id)
                            if detail.get("desc"):
                                note.desc = detail["desc"]
                            if detail.get("tags"):
                                note.tags = detail["tags"]
                            if detail.get("publish_time"):
                                note.publish_time = detail["publish_time"]
                            if detail.get("comment_count_int"):
                                note.comment_count = detail["comment_count"]
                                note.comment_count_int = detail["comment_count_int"]
                            if detail.get("collect_count_int"):
                                note.collect_count = detail["collect_count"]
                                note.collect_count_int = detail["collect_count_int"]
                            if detail.get("like_count_int") and note.like_count_int == 0:
                                note.like_count_int = detail["like_count_int"]
                            if detail.get("author_id"):
                                note.author_id = detail["author_id"]
                        except Exception as e:
                            logger.warning(f"获取详情失败 {note.note_id[:8]}...: {e}")
                        time.sleep(random.uniform(1, 3))

                time.sleep(random.uniform(2, 5))

            self.results = all_notes
            return all_notes

        finally:
            self.stop()

    def stop(self):
        """关闭浏览器（幂等，可安全多次调用）"""
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        self.browser = None
        self.playwright = None

    def save_results(self, filename: str = None):
        """保存结果到JSON文件"""
        if not self.results:
            logger.warning("没有结果可保存")
            return

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"xhs_results_{timestamp}.json"

        filepath = OUTPUT_DIR / filename
        data = [{
            "note_id": n.note_id,
            "title": n.title,
            "desc": n.desc[:500] if n.desc else "",
            "author_name": n.author_name,
            "author_id": n.author_id,
            "like_count": n.like_count,
            "like_count_int": n.like_count_int,
            "comment_count": n.comment_count,
            "comment_count_int": n.comment_count_int,
            "collect_count": n.collect_count,
            "collect_count_int": n.collect_count_int,
            "tags": n.tags,
            "note_type": n.note_type,
            "cover_url": n.cover_url,
            "publish_time": n.publish_time,
            "url": n.url,
            "keyword": n.keyword,
            "futures_confidence": n._futures_confidence,
        } for n in self.results]

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"💾 结果已保存到: {filepath}")
        logger.info(f"   共 {len(self.results)} 条笔记")
        return filepath


def print_results_summary(notes: list[XHSNote]):
    """打印结果摘要"""
    print("\n" + "=" * 60)
    print(f"XHS Results Summary: {len(notes)} notes")
    print("=" * 60)

    # 按关键词分组
    by_keyword = {}
    for note in notes:
        kw = note.keyword
        if kw not in by_keyword:
            by_keyword[kw] = []
        by_keyword[kw].append(note)

    for kw, kw_notes in by_keyword.items():
        print(f"\n[{kw}]: {len(kw_notes)} 条笔记")
        for i, note in enumerate(kw_notes[:3], 1):
            title = note.title or note.desc or "(无文本)"
            title = title[:80].replace('\n', ' ')
            author = note.author_name or "?"
            time_str = f" | {note.publish_time}" if note.publish_time else ""
            like_str = f"L{note.like_count_int}" if note.like_count_int > 0 else ""
            comment_str = f"C{note.comment_count_int}" if note.comment_count_int > 0 else ""
            stats = " ".join(filter(None, [like_str, comment_str]))
            vid = "V" if note.note_type == "video" else "T"
            print(f"  {i}. [{vid}] @{author}{time_str} | {title}")
            if stats:
                print(f"     {stats}")
            if note.tags:
                print(f"     标签: {', '.join(note.tags[:5])}")
            if note.desc:
                desc_preview = note.desc[:120].replace('\n', ' ')
                print(f"     正文: {desc_preview}...")


# ============================================================
# 期货相关性过滤器
# ============================================================

def filter_futures_notes(notes: list) -> list:
    """过滤出与期货相关的笔记"""
    FUTURES_TERMS = {
        "期货", "期市", "商品", "黑色系", "有色", "农产品", "能化",
        "螺纹", "螺纹钢", "铁矿石", "铁矿", "热卷", "焦炭", "焦煤",
        "铜", "沪铜", "铝", "沪铝", "锌", "镍", "沪镍",
        "黄金", "沪金", "白银", "沪银", "金价", "银价",
        "原油", "油价", "PTA", "甲醇", "PVC", "PP",
        "塑料", "橡胶", "沥青", "尿素", "纯碱", "玻璃",
        "豆粕", "豆油", "棕榈油", "菜粕", "白糖", "棉花", "玉米",
        "生猪", "鸡蛋", "苹果", "红枣",
        "股指", "国债", "IF", "IC", "IH",
        "多头", "空头", "做多", "做空", "开仓", "平仓", "多头",
        "止损", "止盈", "套利", "对冲", "基差", "升水", "贴水",
        "主力合约", "交割", "上期所", "大商所", "郑商所",
        "K线", "均线", "MACD", "成交量", "持仓量",
        "回调", "突破", "震荡", "涨跌", "涨停", "跌停",
    }

    # 噪声词
    NOISE = {"期货配资", "喊单", "带单", "暴富", "稳赚", "恋爱", "相亲"}

    relevant = []
    for note in notes:
        text = note.title + " " + note.desc
        if any(noise in text for noise in NOISE):
            continue
        match_count = sum(1 for term in FUTURES_TERMS if term in text)
        if match_count >= 2:
            relevant.append(note)

    return relevant


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="小红书期货信息采集器 (Playwright方案)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python xhs_scraper.py                                  # 基本搜索
  python xhs_scraper.py --no-login                       # 跳过登录(使用已保存状态)
  python xhs_scraper.py --keywords "豆粕期货" "PTA分析"   # 自定义关键词
  python xhs_scraper.py --fetch-details                  # 同时获取笔记详情
  python xhs_scraper.py --headless --no-login            # 无头模式(需要已有登录态)
  python xhs_scraper.py --filter-futures                 # 只保留期货相关笔记
        """,
    )
    parser.add_argument("--keywords", type=str, nargs="+",
                        help="搜索关键词 (默认使用期货关键词列表)")
    parser.add_argument("--no-login", action="store_true",
                        help="不需要登录（使用之前保存的登录状态）")
    parser.add_argument("--headless", action="store_true",
                        help="无头模式（隐藏浏览器窗口，需要已有登录状态）")
    parser.add_argument("--fetch-details", action="store_true",
                        help="获取笔记详情页内容")
    parser.add_argument("--output", type=str, default=None,
                        help="输出文件名")
    parser.add_argument("--filter-futures", action="store_true",
                        help="只保留期货相关的笔记（过滤无关内容）")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="详细日志")
    parser.add_argument("--debug", action="store_true",
                        help="调试模式（浏览器可视化每一步）")
    args = parser.parse_args()

    # 日志配置
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    keywords = args.keywords if args.keywords else FUTURES_KEYWORDS

    print("=" * 60)
    print("Xiaohongshu Futures Scraper")
    print("=" * 60)
    print(f"Keywords: {len(keywords)}")
    print(f"Fetch details: {'Y' if args.fetch_details else 'N'}")
    print(f"Need login:   {'Y' if not args.no_login else 'N (using saved state)'}")
    print(f"Headless:    {'Y' if args.headless else 'N'}")
    print()

    scraper = XHSScraper(headless=args.headless, debug=args.debug)

    try:
        notes = scraper.run(
            keywords=keywords,
            fetch_details=args.fetch_details,
            need_login=not args.no_login,
        )

        # 期货过滤
        if args.filter_futures:
            before = len(notes)
            notes = filter_futures_notes(notes)
            scraper.results = notes  # 同步过滤后的结果到保存
            print(f"\nFutures filter: {before} -> {len(notes)} notes")

        # 打印摘要
        print_results_summary(notes)

        # 保存结果
        if notes:
            output_path = scraper.save_results(args.output)
            print(f"\nDone! Results: {output_path}")
            print("\nNext steps:")
            print("  1. Check JSON for data structure")
            print("  2. Use --filter-futures for futures relevance")
            print("  3. Use --fetch-details for full text + comments")
            print("  4. Scale up: consider Spider_XHS project")
        else:
            print("\nNo notes collected. Possible reasons:")
            print("  1. Login expired (re-run without --no-login)")
            print("  2. Page structure changed (try --debug mode)")
            print("  3. Network issues")
            print("  4. Anti-bot detection (wait and retry)")

    except KeyboardInterrupt:
        print("\nInterrupted")
    except Exception as e:
        logger.error(f"采集异常: {e}", exc_info=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
