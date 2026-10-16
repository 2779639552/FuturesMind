# -*- coding: utf-8 -*-
"""
公众号文章 → AgentSense 研报库 入库 adapter

把 `wechat_album_collector.py` 采集到的公众号文章接进既有研报链路
(落 .md → research_reports 落库 → 后台 LLM 提取观点 → 聚合/RAG),
镜像 research_collector_htfc._ingest_one 的做法。

================================ 为什么读 inbox 而不是读 new ================================
采集器的产物有两个:
  · wechat_new.json    —— **每次 --daily 覆盖写**,只反映"最近一轮"的新文
  · wechat_inbox.jsonl —— **追加式**,记录历轮发现过的全部新文,跨轮不丢
若入库读 new,一旦入库环节失败(或两轮之间服务重启),那份 new 会被下一轮覆盖,
**这批文章就永远进不了库且没有任何痕迹**。所以默认读 inbox,
再用 DB 去重(已入库的直接跳过) —— 入库因此**幂等且可重跑**。

================================ 去重口径 ================================
以采集器的稳定键 `key`(= `biz_mid_idx`,也是落盘文件名的前缀)为准,
**不用 url**:公众号链接带 sn 签名,有时效,不适合当唯一键。
对应 `database.get_research_report_by_external_key(key, url)`。

用法:
  python wechat_ingest.py                                  # 默认:扫 inbox,入库未入库的
  python wechat_ingest.py --dry-run                        # 只看会做什么,不写库
  python wechat_ingest.py --source new                     # 只入 wechat_new.json
  python wechat_ingest.py --source collect --limit 10      # 回填:album_collect.json 最近 10 篇
  python wechat_ingest.py --source collect --limit 20 --workers 3   # 回填+并行(见下)
  python wechat_ingest.py --account "多空逻辑"              # 只入某个号

================================ 并发(2026-09-21 新增) ================================
单篇端到端实测 5~12 分钟(两次串行 LLM + chroma 索引),串行回填 10 篇要 1~2 小时。
`--workers N` 用有界线程池并行"处理"阶段:
  · 默认 1 = **完全串行**,与历史行为逐字节一致(调度器/网页链路不受影响);
  · 上限 3 = **留 2 路余量给同账户其他链路**(LLM 账户并发配额 5,研报采集器已占 2~3 路);
    再高只会把配额抢光,让定时任务 429。
  · 环境变量 WECHAT_INGEST_WORKERS 可覆盖(仍被 min(3) 钳制)。
`--dry-run` 不并行、不预热(pure 判定,保持原样)。
⚠️ 回填期间 **不要重启 web 服务**:它启动时会把所有 processing/pending 行当孤儿回收,
   与正在跑的本进程抢同一篇(= 双花 LLM)。
"""
import argparse
import base64
import contextlib
import io
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_REG_CACHE = None  # 【变量】注册表缓存(见 _registry)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SOURCE_LABEL = "公众号"  # 【常量】source 列前缀(最终写成 "公众号-<号名>")
MIN_BODY_CHARS = 200     # 【常量】正文最小字符数(与 HTFC/发现报告同阈值)
DEFAULT_MAX_PER_RUN = 20  # 【常量】单轮最多入库篇数(挡"积压上百篇一次性烧 LLM")
DEFAULT_WORKERS = 1       # 【常量】默认串行(与历史行为一致;调度器/网页链路走这条路)
MAX_WORKERS = 3           # 【常量】硬上限:留 2 路余量给同账户其他链路(LLM 账户配额 5)


# ══════════════════════════════════════════════════════════════════════════
# 【内容噪声过滤】2026-10-14 用户报障：恒力期货研究院 `【招聘】能化研究员` 被当研报入库
#   （正文 1162 字，能过 MIN_BODY_CHARS 长度关）。本段按**内容**筛掉三类"非投研"文章。
#   设计原则：**宁可留错不可删错** —— 全部是"高精度词 + 组合条件"，不用裸词。
#   反例约束：`铝合金企业调研：票荒倒逼减产…`(4782 字) 含"调研"但是**真研报**
#             （有核心观点+调研汇总），任何把"调研/走访"当噪声词的做法都会误伤它。
# ══════════════════════════════════════════════════════════════════════════
# 招聘：标题词 **且** 正文词同时命中才判（防"从招聘数据看就业"这类真研究被误伤）
RECRUIT_TITLE = ("招聘", "诚聘", "招募", "招人", "求贤", "人才招聘", "社会招聘", "校园招聘")
RECRUIT_BODY = ("岗位职责", "任职要求", "任职资格", "简历", "应聘", "薪酬", "薪资",
                "福利待遇", "五险一金", "学历要求", "本科及以上", "硕士及以上",
                "工作地点", "招聘人数", "投递", "面试", "岗位要求", "职位描述")

# 公告与通稿：这些词**本身**就是公告/回顾的性质 ⇒ 标题命中即判（不必再看内容）
ANNOUNCE_TITLE = ("邀请函", "倒计时", "即将开幕", "隆重开幕", "即将举办", "即将召开",
                  "即将启幕", "启幕", "成功举办", "圆满举办", "圆满落幕", "盛大举行",
                  "盛大开幕", "直播预告", "路演预告", "会议预告", "活动预告")
# 活动词：可能出现在正经研报标题里（如"某论坛观点集锦"）⇒ 必须**正文通稿特征**确认
EVENT_TITLE = ("策略会", "培训会", "沙龙", "论坛", "峰会", "研讨会", "宣讲会", "发布会")
EVENT_BODY = ("与会嘉宾", "致辞", "会议现场", "圆桌", "闭门", "主办", "协办", "承办",
              "签到", "参会", "报名截止", "扫码报名", "直播链接", "敬请期待",
              "欢迎参加", "诚挚邀请", "座无虚席", "反响热烈", "出席并", "发表主旨演讲")

MIN_SUBSTANCE_CHARS = 150  # 【常量】"实质内容"最小字数(去套话+去重复段后)

# 免责声明/引导语 —— **只列固定短语**。⚠️ 严禁在此处使用 `.*?`：那会在单行长文本上
# 跨段吞掉正文（v1 原型据此把 4702 字真研报判成"实质 155 字"，属实测事故）。
_BOILERPLATE_PHRASES = (
    "点击上方关注", "点击蓝字", "点击上方", "点击", "关注我们", "欢迎订阅", "扫码关注",
    "重要提示", "免责声明", "风险提示", "符合证监会适当性管理规定", "证监会适当性管理",
    "证监许可", "期货交易咨询业务资格", "投资咨询业务资格", "版权所有", "违者必究",
    "— END —", "END", "来源：", "阅读全文", "了解更多资讯", "关注“", "”公众号",
    "本报告观点和信息仅供", "符合证监会适当性管理规定的期货交易者参考",
    "本平台暂时无法设置访问限制", "请勿点击查看或使用本报告任何信息",
    "对由此给您造成的不便表示诚挚歉意", "感谢您的理解与配合",
    "据此操作", "责任自负", "不构成投资建议", "仅供参考", "不因任何订阅",
    "准确性和完整性", "不作出任何保证", "不保证", "发生任何变更",
    "其它媒体平台接收", "翻版", "复制或刊发", "不得对本报告进行任何增删或修改",
    "不承担任何形式的责任", "报告完成日期", "作者：", "联系方式：",
    "从业资格证号", "交易咨询证号", "编辑：", "审核：", "风险因子：",
)
_BOILERPLATE_RE = re.compile(r"未经[^\s]{0,6}许可")


def strip_boilerplate(text: str) -> str:
    """剥离免责声明/引导语(固定短语),返回剩余正文。

    ⚠️ 只做**固定短语**删除,不得用 `.*?` 跨段替换 —— 见上方常量处的事故说明。
    """
    out = text or ""
    for p in _BOILERPLATE_PHRASES:
        out = out.replace(p, " ")
    out = _BOILERPLATE_RE.sub(" ", out)
    out = re.sub(r"\s+", " ", out)
    return out.strip()


def _seg_key(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s)


def substance_len(text: str) -> int:
    """实质内容字数 = 去套话后「**文内只出现一次**」的句子总字数。

    【为什么要按唯一片段计量】免责声明在文内会**重复出现**（`会议预告` 那篇 1024 字
    其实是同一段免责重复 1.5 遍）。若只做固定短语删除，会度出 739 字假内容 ⇒ 漏判。
    实测分布：真噪声 71 字 / 下一条真研报 240 字，**3.4 倍间隔**，阈值可靠。
    """
    s = strip_boilerplate(text)
    segs = re.split(r"[。！？；!?;]+", s)
    keys = [_seg_key(x) for x in segs]
    cnt = {}
    for k in keys:
        if len(k) >= 6:
            cnt[k] = cnt.get(k, 0) + 1
    total = 0
    for seg, k in zip(segs, keys):
        if len(k) < 6:
            total += len(seg)          # 残句/小标题:一律计入
        elif cnt.get(k, 0) == 1:
            total += len(seg)          # 只出现一次的才算实质
    return total


def noise_reason(title: str, text: str) -> str:
    """判断是否"内容非投研"。返回 '' 表示保留;否则返回原因(用于日志/统计)。

    三类：recruit 招聘 / announce 公告通稿 / hollow 无实质内容。
    """
    t, x = (title or ""), (text or "")
    if any(w in t for w in RECRUIT_TITLE):
        rb = [w for w in RECRUIT_BODY if w in x]
        if rb:
            return "recruit(标题招聘词+正文招聘词 %d 个: %s)" % (len(rb), ",".join(rb[:4]))
    for w in ANNOUNCE_TITLE:
        if w in t:
            return "announce(标题公告词: %s)" % w
    if any(w in t for w in EVENT_TITLE):
        eb = [w for w in EVENT_BODY if w in x]
        if len(eb) >= 3:
            return "event(标题活动词+正文通稿特征词 %d 个: %s)" % (len(eb), ",".join(eb[:4]))
    sub = substance_len(x)
    if sub < MIN_SUBSTANCE_CHARS:
        return "hollow(实质仅 %d 字, 全文 %d 字)" % (sub, len(x))
    return ""


# 【状态目录】与采集器同一约定(见 wechat_album_collector.HERE)
STATE_DIR = Path(os.path.expanduser((os.environ.get("AGENTSENSE_WECHAT_DIR") or "").strip())
                 or (Path.home() / ".tradingagents" / "wechat"))
F_NEW = STATE_DIR / "wechat_new.json"
F_INBOX = STATE_DIR / "wechat_inbox.jsonl"
F_COLLECT = STATE_DIR / "album_collect.json"
F_RESULT = STATE_DIR / "wechat_ingest_result.json"   # 【结果文件】供调度器读计数(见 _write_result)


def _write_result(payload: dict) -> None:
    """把本轮统计写成**机器可读** JSON,供调度器取数(而不是解析 stdout)。

    【为什么必须 · 2026-09-21 实测】调度器原先用 `capture_output=True` 抓本脚本
    stdout 再正则抠数字。实测:从**服务进程**(web_app)启动本脚本时,子进程输出会
    **整段丢失**(落盘日志只有 `returncode=0 → ok` + "(无输出)",而同一父进程抓
    htfc/gtja/dz 的输出正常、从交互式 shell 用**同一条命令**跑又能拿到 511 字节)。
    根因未定位 ⇒ **计数不再依赖 stdout**,改为落一个结果文件当唯一事实来源;
    调度器在结果文件缺失时才退化回解析 stdout(两边都拿不到就报警 0,不猜)。

    【为什么 tmp + os.replace】读方(调度器)与写方(本脚本)是两个进程,
    直接重写会让读方可能读到半截 JSON ⇒ 先写临时文件再原子替换。
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = str(F_RESULT) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, F_RESULT)
    except Exception as e:  # noqa: BLE001 - 写结果文件失败绝不能影响退出码/主流程
        print("    ! 结果文件写入失败(不影响本轮): %s" % e)


def _now_str() -> str:
    """本地时间字符串(结果文件用;库里另有一套 UTC 口径,勿混)。"""
    import datetime as _dt
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sanitize_filename(name: str) -> str:
    """文件名安全化:非法字符替换为下划线,截断到 60 字符(与各采集器一致)。"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return safe[:60] or "report"


def _resolve_workers(workers=None) -> int:
    """解析并发度:显式参数 > 环境变量 WECHAT_INGEST_WORKERS > 默认,再钳制到 [1, MAX_WORKERS]。

    【为什么钳制而不是信任调用方】LLM 账户是**共享硬约束**(研报三源 + 网页上传 + 本链路
    共用一个 key,并发上限 5)。worker 数是"每一次外部调用 = 一路并发"的直接换算,
    写死或放大到 5 会把这批回填变成"配额独占",定时任务只能 429 等重试。
    """
    if workers is None:
        raw = (os.environ.get("WECHAT_INGEST_WORKERS") or "").strip()
        try:
            workers = int(raw) if raw else None
        except ValueError:
            workers = None
    try:
        workers = int(workers) if workers else DEFAULT_WORKERS
    except (TypeError, ValueError):
        workers = DEFAULT_WORKERS
    if workers < 1:
        workers = DEFAULT_WORKERS
    return min(workers, MAX_WORKERS)


def _warm_up() -> str | None:
    """在**调用线程**里先完成重量级导入;返回 None 表示成功,否则返回错误文本。

    【为什么必须】`ingest_one` 把 `database` / `web_app` 的导入写在函数体内(懒导入)。
    串行时这次首导入发生在主线程;一旦开线程池,若让首个 worker 线程承担它,就变成
    "非主线程初始化"——`web_app` 会建 app/日志/线程池,部分库依赖主线程环境,有隐性故障。
    """
    try:
        from database import get_db, guess_report_type  # noqa: F401
        from web_app import RESEARCH_UPLOAD_DIR, _process_research_report  # noqa: F401
        get_db().list_research_reports(None, limit=1)  # 【连接】把懒连接也在主线程建一次
        return None
    except Exception as e:  # noqa: BLE001 - 预热失败就不开池,不冒险并行
        return "预热导入失败: %s" % e


class _ThreadRoutedStdout(io.TextIOBase):
    """线程感知的 stdout 路由器:挂了缓冲区的线程收进自己的缓冲,其余线程照常直出。

    【为什么不直接用 contextlib.redirect_stdout】它改的是**进程级** `sys.stdout`:
    两个 worker 同时进 `redirect_stdout(buf)` 会互相覆盖,退出时还会把 stdout
    "还原成另一个线程的缓冲区" ⇒ 日志串台、进度丢失(实测:T1 的汇总整段消失,
    且墙钟反而比串行更慢)。这里改成"整体装一次、按线程分流",天然无竞态。
    """

    def __init__(self, real):
        self._real = real          # 【变量】被包住的真实 stdout(通常是重定向的日志文件)
        self._local = threading.local()

    def buffer(self, buf):
        """在当前线程挂/撤缓冲区(传 None 撤销)。"""
        self._local.buf = buf

    def write(self, s):
        buf = getattr(self._local, "buf", None)
        if buf is not None:
            return buf.write(s)
        return self._real.write(s)

    def flush(self):
        if getattr(self._local, "buf", None) is None:
            with contextlib.suppress(Exception):
                self._real.flush()

    def reconfigure(self, *args, **kwargs):
        """透传给真实 stdout —— 有些被懒导入的模块会 reconfigure,不能让它 AttributeError。"""
        fn = getattr(self._real, "reconfigure", None)
        if callable(fn):
            with contextlib.suppress(Exception):
                fn(*args, **kwargs)

    @property
    def encoding(self):
        return getattr(self._real, "encoding", None) or "utf-8"


def load_entries(source: str) -> list[dict]:
    """读入待入库条目。

    【参数】source: 'inbox' | 'new' | 'collect'。
    【返回】list[dict]: 条目列表(含 key/title/url/date/text 等);文件缺失返回 []。
    【关键逻辑】collect 源只保留**带正文**的条目(该文件默认只有元信息)。
    """
    if source == "inbox":
        if not F_INBOX.exists():
            return []
        out = []
        for line in io.open(F_INBOX, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    path = F_NEW if source == "new" else F_COLLECT
    if not path.exists():
        return []
    try:
        data = json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    if source == "collect":
        data = [x for x in data if (x.get("text") or "").strip()]
    return data


def _registry() -> dict:
    """读账号注册表(带缓存,供"按合集名/按 biz 反查号名"用)。"""
    global _REG_CACHE
    if _REG_CACHE is None:
        try:
            _REG_CACHE = json.load(io.open(STATE_DIR / "wechat_accounts.json", encoding="utf-8"))
        except Exception:
            _REG_CACHE = {"accounts": {}}
    return _REG_CACHE


def entry_account(e: dict) -> str:
    """取条目所属**号名**(不是合集名)。

    【为什么要有这个函数】采集器有两条产出路径,字段不一致:
        · --daily 的条目带 `account`(= 号名) —— 直接用
        · --full 的 album_collect.json **只带 `albums`(合集名),没有号名** ——
          早期版本直接拿 `albums[0]` 当号名,于是 source 被写成 "公众号-交易思考"
          (交易思考是**合集名**,不是号名),同一号的多个合集会分裂成多个 source。
    【关键逻辑】无 account 时按三级回退:① 合集名 → 注册表反查号名;
              ② 去重键首段(十进制 biz) → base64 解注册表 biz 比对;
              ③ 都没有才用 "未知号"。
    """
    a = e.get("account") or e.get("nickname")
    if a:
        return str(a)

    reg = (_registry().get("accounts") or {})

    # ① 合集名 → 号名
    title2nick = {}
    for biz, acct in reg.items():
        nick = acct.get("nickname") or biz
        for _aid, m in (acct.get("albums") or {}).items():
            if m.get("title"):
                title2nick[m["title"]] = nick
    for t in (e.get("albums") or e.get("_albums") or []):
        if t in title2nick:
            return title2nick[t]

    # ② 去重键首段(十进制 biz) → 号名(base64 解注册表 biz 后比对)
    k = entry_key(e)
    if k and "_" in k:
        head = k.split("_")[0]
        for biz, acct in reg.items():
            try:
                if str(int(base64.b64decode(biz))) == head:
                    return str(acct.get("nickname") or biz)
            except Exception:
                continue
    return "未知号"


def entry_key(e: dict) -> str:
    """取稳定去重键;缺 key 时按 biz/mid/idx 或 msgid 兜底。"""
    k = e.get("key") or e.get("perm_key")
    if k:
        return str(k)
    if e.get("msgid"):
        return str(e["msgid"])
    return ""


def entry_date(e: dict) -> str:
    """取发布日期 YYYY-MM-DD(优先 date 字段,退化用 create_time 时间戳)。"""
    d = (e.get("date") or "").strip()
    if d:
        return d[:10]
    ct = e.get("create_time")
    try:
        import datetime
        return datetime.datetime.fromtimestamp(int(ct)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def entry_url(e: dict) -> str:
    return (e.get("url") or e.get("source_url") or "").strip()


def ingest_one(e: dict, dry_run: bool = False) -> str:
    """把一篇文章写进研报库并触发 LLM 处理。

    【参数】e: 条目(含 key/title/url/date/text);dry_run: 只判定不写。
    【返回】str 处置结果之一:
            'ok'      新插入,LLM 处理成功(拿到观点)
            'okerr'   新插入,但 LLM 判不可用(池外品种/心法类无品种)→ 行留痕,status=error
            'retry'   上次崩在 processing,复用该行重跑且处理成功
            'retryerr' 复用该行重跑,但仍判不可用
            'dup'     已入库且已处理完,跳过(幂等)
            'short'   正文过短,跳过
            'empty'   没正文(通常是采集时带了 --no-fetch),跳过
            'noise'   内容非投研(招聘/活动公告通稿/整篇免责声明),跳过
            'nokey'   无去重键,跳过(无法保证幂等,宁可不动)
    【关键逻辑】以 DB 行为准判重(不是看文件是否存在),与 HTFC 同一套幂等口径;
              命中 processing 残留时复用原行重跑,避免观点永久缺失;
              处理完**必须读回终态**再定口径(见下方终态读回)。
    """
    key = entry_key(e)
    title = (e.get("title") or "").strip() or "(无标题)"
    text = (e.get("text") or "").strip()
    url = entry_url(e)
    date = entry_date(e)
    acct = entry_account(e)

    if not key:
        print("    ! 无去重键(key/msgid 皆空),跳过: %s" % title[:46])
        return "nokey"
    if not text:
        print("    ! 无正文(采集时可能带了 --no-fetch),跳过: %s" % title[:46])
        return "empty"
    if len(text) < MIN_BODY_CHARS:
        print("    ! 正文过短(%d 字符 < %d),跳过: %s" % (len(text), MIN_BODY_CHARS, title[:46]))
        return "short"

    # 【内容噪声关】2026-10-14 用户报障:招聘/活动通稿被当研报入库。
    #   放在"长度关"之后:长度关挡的是空壳(0~40 字的预告),本关挡的是
    #   **正文够长但内容不是投研**的(如 1162 字的【招聘】)。
    _noise = noise_reason(title, text)
    if _noise:
        print("    ! 内容非投研(%s),跳过: %s" % (_noise, title[:46]))
        return "noise"

    org = "%s-%s" % (SOURCE_LABEL, acct)  # 【变量】org: 落盘子目录名 + source 列取值

    if dry_run:
        from database import get_db
        ex = get_db().get_research_report_by_external_key(key, url)
        if ex and ex.get("status") != "processing":
            print("    = [DRY] 已入库(id=%s, status=%s),会跳过: %s"
                  % (ex["id"], ex["status"], title[:42]))
            return "dup"
        print("    + [DRY] 会入库: %s | %s | %s | %d 字"
              % (date, acct, title[:42], len(text)))
        return "ok"

    from database import get_db, guess_report_type  # 【调用包】落库 + 标题启发式类型
    from web_app import RESEARCH_UPLOAD_DIR, _process_research_report  # 【调用包】存储目录 + 后台处理

    upload_dir = RESEARCH_UPLOAD_DIR / org
    upload_dir.mkdir(parents=True, exist_ok=True)
    fname = "%s_%s.md" % (key, _sanitize_filename(title))  # 【变量】文件名前缀即去重键
    file_path = upload_dir / fname

    db = get_db()
    existing = db.get_research_report_by_external_key(key, url)

    md = (
        "# %s\n"
        "- 来源: %s\n"
        "- 链接: %s\n"
        "- 日期: %s\n\n"
        "%s\n" % (title, org, url, date, text)
    )
    # 覆盖写:孤儿文件 / 上次半成品一律补齐正文(与 HTFC 口径一致)
    file_path.write_text(md, encoding="utf-8")

    if existing and existing.get("status") != "processing":
        print("    = 已在研报库(id=%s, status=%s),跳过(幂等): %s"
              % (existing["id"], existing["status"], title[:42]))
        return "dup"

    if existing:
        # 【自愈】上次进程在 LLM 处理中途崩溃残留:复用该行重跑,不重复 insert
        print("    ~ 上次卡在 processing,复用 id=%s 重跑: %s" % (existing["id"], title[:42]))
        report_id = existing["id"]
        verdict = "retry"
    else:
        report_id = db.insert_research_report(
            variety="",              # 【品种】留空由 LLM 识别
            title=title,
            source=org,
            source_url=url,          # 【出处】原始文章链接(2026-09-21 新增列)
            filename=fname,
            file_path=str(file_path),
            ingest_source="auto",    # 【来源】数据仓库"研报库"打"自动"徽标
            publish_date=date,       # 【发布日期】真实发布日,不是入库日
            report_type=guess_report_type(title),
        )
        verdict = "ok"

    _process_research_report(report_id)  # 【调用函数】复用 web_app 后台处理(LLM 提取 → 落库 → 聚合)
    # 【终态读回】`_process_research_report` 把**业务失败**吞成 status=error 后**正常返回**
    # (池外品种 / 心法类无品种文),所以"插入成功"≠"拿到观点"。读回一行才能给出真实口径:
    # 公众号这类号普遍有一半是心法/池外品种文,不读回就会把"白烧一次 LLM"算成"入库成功"。
    st, why = "", ""
    try:
        row = db.get_research_report(report_id) or {}
        st = (row.get("status") or "").strip()
        why = (row.get("error") or "").replace("\n", " ")[:90]
    except Exception:  # noqa: BLE001 - 读回失败不影响入库事实,降级按成功计
        st = ""
    if st == "error":
        print("    - %s %s -> report_id=%s（已留痕但 LLM 判不可用：%s）"
              % (date, title[:46], report_id, why or "见 error 列"))
        return verdict + "err"
    print("    + %s %s -> report_id=%s" % (date, title[:46], report_id))
    return verdict


def main():
    ap = argparse.ArgumentParser(description="公众号文章入库 AgentSense 研报库")
    ap.add_argument("--source", choices=["inbox", "new", "collect"], default="inbox",
                    help="inbox=追加归档(默认,崩溃安全) / new=最近一轮 / collect=全量清单(回填用)")
    ap.add_argument("--account", help="只入库某个号(按号名匹配)")
    ap.add_argument("--since",
                    help="只入库发布日期 >= 该日期(YYYY-MM-DD)的文章。用于「每日只处理新文」:"
                         "本脚本按 create_time **最旧优先** 且 --limit 限流,若 inbox 积压了"
                         "历史基线(实测微信读书初始化灌入 118 篇),当天新文会被排到数轮之后。\n"
                         "过滤发生在去重/排序/限流**之前**,故 --limit 的名额只给窗口内的文章。")
    ap.add_argument("--limit", type=int, default=DEFAULT_MAX_PER_RUN,
                    help="单轮最多入库 N 篇按时间**最旧优先**(0=不限;默认 %d)" % DEFAULT_MAX_PER_RUN)
    ap.add_argument("--dry-run", action="store_true", help="只判定不写库")
    ap.add_argument("--workers", type=int, default=None,
                    help="并行处理路数(默认 1=串行;上限 %d;env WECHAT_INGEST_WORKERS 可覆盖)"
                         % MAX_WORKERS)
    ap.add_argument("--state-dir", help="覆盖状态目录(默认 $AGENTSENSE_WECHAT_DIR 或 ~/.tradingagents/wechat)")
    args = ap.parse_args()

    if args.state_dir:
        # ⚠️ 【2026-10-14 修】原来漏了 F_RESULT —— `--state-dir` 只重定向了可读文件，
        #   结果文件仍写到**真实**状态目录 ⇒ 任何带 --state-dir 的运行(测试/回填)
        #   都会**污染调度器读的计数**(实测:测试跑一次就把"新入库 9 篇"改成"2 篇")。
        global STATE_DIR, F_NEW, F_INBOX, F_COLLECT, F_RESULT
        STATE_DIR = Path(os.path.expanduser(args.state_dir))
        F_NEW = STATE_DIR / "wechat_new.json"
        F_INBOX = STATE_DIR / "wechat_inbox.jsonl"
        F_COLLECT = STATE_DIR / "album_collect.json"
        F_RESULT = STATE_DIR / "wechat_ingest_result.json"

    print("=" * 78)
    print("【公众号入库】源=%s   目录=%s   时间=%s" % (args.source, STATE_DIR, __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    entries = load_entries(args.source)
    if args.account:
        entries = [e for e in entries if entry_account(e) == args.account]
    # 【2026-10-14】发布日过滤:**必须在去重/排序/限流之前** —— 否则 --limit 的 20 个
    #   名额会被历史积压吃掉(实测当天 13 篇要等 6 轮才轮到)。
    if args.since:
        _since = str(args.since).strip()[:10]
        _before = len(entries)
        entries = [e for e in entries if (entry_date(e) or "") >= _since]
        print("  --since %s 过滤: %d -> %d 条" % (_since, _before, len(entries)))
    # 去重(同一 key 在 inbox 里可能出现多次) + 按时间升序(最旧优先)
    uniq = {}
    for e in entries:
        k = entry_key(e)
        if k:
            uniq[k] = e
    items = sorted(uniq.values(), key=lambda x: int(x.get("create_time") or 0))
    print("  读入 %d 条，去重后 %d 条" % (len(entries), len(items)))
    if not items:
        print("\nCollected: 0\nProcessed: 0")
        _write_result({
            "ts": _now_str(), "source": args.source, "dry_run": bool(args.dry_run),
            "collected": 0, "processed": 0, "inserted": 0, "usable": 0, "retried": 0,
            "skipped": 0, "noise": 0, "failed": 0,
        })
        return 0

    if args.limit and len(items) > args.limit:
        print("  超出单轮上限 %d，本次只处理最旧的 %d 篇，其余留待下轮" % (args.limit, args.limit))
        items = items[:args.limit]

    stats = {"ok": 0, "okerr": 0, "retry": 0, "retryerr": 0,
             "dup": 0, "short": 0, "empty": 0, "nokey": 0, "noise": 0, "err": 0}
    _KNOWN = set(stats)

    def _bump(v):
        stats[v if v in _KNOWN else "err"] += 1

    def _run_serial():
        """串行路径:默认行为,与历史逐字节一致。"""
        for e in items:
            try:
                _bump(ingest_one(e, dry_run=args.dry_run))
            except Exception as ex:  # noqa: BLE001 - 单篇失败不阻断整批
                stats["err"] += 1
                print("    ✗ 入库异常: %s | %s" % ((e.get("title") or "")[:40], str(ex)[:160]))

    workers = _resolve_workers(args.workers)
    warm = None
    if not args.dry_run and workers > 1:
        warm = _warm_up()

    if args.dry_run or workers <= 1 or warm:
        if warm:
            print("  ! %s —— 本次回退为串行" % warm)
        _run_serial()
    else:
        print("  [并发] %d 篇 / %d 路并行(LLM 账户配额 5,本链路占 %d 路)"
              % (len(items), workers, workers))
        lock = threading.Lock()
        done = 0
        real_out = sys.stdout
        router = _ThreadRoutedStdout(real_out)
        sys.stdout = router  # 【整体装一次】worker 只管往自己的线程缓冲区写

        def _work(e):
            """单篇任务体:输出收进本线程缓冲区,由主线程按完成顺序整段回放。"""
            t0 = time.time()
            buf = io.StringIO()
            router.buffer(buf)
            try:
                verdict = ingest_one(e)
                return e, verdict, None, time.time() - t0, buf.getvalue()
            except Exception as ex:  # noqa: BLE001 - 单篇异常在 worker 内收敛,绝不冒泡
                return e, None, str(ex)[:160], time.time() - t0, buf.getvalue()
            finally:
                router.buffer(None)

        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wx-ingest") as pool:
                futs = [pool.submit(_work, e) for e in items]
                for fut in as_completed(futs):  # 按完成顺序回报(不代表请求顺序)
                    e, verdict, err, took, out = fut.result()
                    with lock:
                        done += 1
                        for line in out.splitlines():
                            print("  [%d/%d]%s" % (done, len(items), line))
                        if err:
                            stats["err"] += 1
                            print("  [%d/%d] ✗ %.0fs %s | %s"
                                  % (done, len(items), took, (e.get("title") or "")[:36], err))
                        else:
                            _bump(verdict)
                            print("  [%d/%d] %s %.0fs %s"
                                  % (done, len(items), verdict, took, (e.get("title") or "")[:36]))
        finally:
            sys.stdout = real_out

    print("")
    print("=" * 78)
    # 【口径】新入库 = 插入成功的行数(含被判不可用的);有效观点 = 真正拿到结论的。
    # ⚠️ 调度器 `_run_wechat_ingest` 按 "新入库"/"重跑 processing 残留" 两个片段解析数字,
    #    改文案时这两个片段必须原地保留,否则服务端告警会静默变成 0。
    inserted = stats["ok"] + stats["okerr"]
    retried = stats["retry"] + stats["retryerr"]
    usable = stats["ok"] + stats["retry"]
    print("【汇总】新入库 %d 篇（有效观点 %d 篇 / 池外或非投研 %d 篇，均已在库留痕）| "
          "重跑 processing 残留 %d 篇（有效 %d 篇）| "
          "跳过(已入库 %d / 无正文 %d / 正文过短 %d / 内容噪声 %d / 无键 %d) | 失败 %d"
          % (inserted, usable, stats["okerr"] + stats["retryerr"],
             retried, stats["retry"],
             stats["dup"], stats["empty"], stats["short"], stats["noise"],
             stats["nokey"], stats["err"]))
    # 【机器可读】供调度器解析(与 HTFC/国君采集器同一格式)
    print("Collected: %d" % len(items))
    print("Processed: %d" % (inserted + retried))
    # 【结果文件】调度器优先读它 —— stdout 在服务进程里可能整段丢失(见 _write_result)
    _write_result({
        "ts": _now_str(), "source": args.source, "dry_run": bool(args.dry_run),
        "collected": len(items), "processed": inserted + retried,
        "inserted": inserted, "usable": usable, "retried": retried,
        "skipped": (stats["dup"] + stats["empty"] + stats["short"]
                    + stats["noise"] + stats["nokey"]),
        "noise": stats["noise"],
        "failed": stats["err"],
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
