"""user_data.py — 用户自传数据(Excel/CSV/MD/TXT)解析与分析师注入

【模块角色】
  个人数据接口:用户在数据仓库页上传 Excel/CSV/Markdown/TXT 表格数据文件 →
  LLM 分析表格结构与列含义,产出"解析规格"(JSON:日期列/数值列含义/单位/频率/
  品种)→ 按规格把文件归一化成数据行入库(user_datasets 表)→ 运行分析时
  宏观/情绪分析师节点把该品种的自传数据**确定性前置注入**系统提示。

  权重口径(用户明确要求,2026-09-07):自传数据是用户私有的一手数据,
  在其覆盖范围内(同指标同时间段)优先级高于 akshare 实时数据与研报数据,
  与国君/华泰等研报数据相仿或略高 —— 注入块内写明该规则,冲突时以用户数据为准。

【格式识别策略】"LLM 看样张 → 产规格 → 确定性解析":
  - LLM 只负责"看"(读前几行样张 + 文件名,判断品种/数据类型/哪列是日期/
    各数值列含义与单位/频率),输出严格 JSON 规格;
  - 解析本身是确定性的:Excel/CSV 走 pandas,MD/TXT 走内置管道表格解析器,
    不让 LLM 逐行改写数据(严禁 LLM 转录数值,防编造)。

【注入方式】与研报宏观事件(research_macro_context)同模式:不做 @tool
  (工具调用不保证发生),节点构造提示时确定性前置;空数据返回 "" 不注入。
  注入块放在研报事件块**之上**(最高优先级置顶)。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

USER_DATA_DIR = Path.home() / ".tradingagents" / "user_datasets"  # 【变量】自传数据文件落盘目录
MAX_USER_DATASET_ROWS = 2000  # 【变量】单文件归一化行数上限(超出截断并在 notes 标注)
SAMPLE_ROWS_FOR_LLM = 8  # 【变量】给 LLM 看的样张行数(判断格式足够,省 token)
MAX_RENDER_ROWS = 12  # 【变量】注入文本里每数据集最多展示的行数(取最近 N 行)
MAX_ACTIVE_DATASETS = 3  # 【变量】注入时每品种最多带的数据集个数(最新优先)

# 【变量】LLM 输出规格的 JSON 结构说明(放提示词里;字段缺失时解析端有兜底)
_SPEC_EXAMPLE = {
    "variety": "MA(品种代码,如 MA/TA/SC;无法判断填空串)",
    "data_type": "数据内容一句话(如 甲醇供需基本面数据)",
    "date_column": "日期列的列名(无日期列填空串)",
    "columns": [
        {"name": "列名(须与表头完全一致)", "meaning": "该列含义", "unit": "单位,无则空串"}
    ],
    "frequency": "日度/周度/月度/不定期",
    "notes": "口径说明/注意事项(没有填空串)",
}


# ── 文件 → 原始行(确定性,零 LLM) ───────────────────────────────────────

def _parse_markdown_tables(text: str) -> list[dict]:
    """从 Markdown/TXT 文本解析管道表格(| a | b |),返回行字典列表。

    【关键逻辑】连续的 | 开头行组成一张表:首行为表头,次行(含 ---)为分隔行,
    其余为数据行;多张表全部收进来(行内附 _table 序号区分)。单元格只做
    strip,**绝不改写数值**。非表格文本行忽略(标题/说明不影响解析)。
    """
    rows: list[dict] = []
    header: list[str] | None = None
    table_idx = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            header = None  # 表格中断(空行/正文行)
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = cells
            table_idx += 1
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c != ""):  # 分隔行
            continue
        row = dict(zip(header, cells, strict=False))
        row["_table"] = table_idx
        rows.append(row)
    return rows


def read_file_rows(file_path: str) -> list[dict]:
    """把上传文件读成原始行字典列表(确定性解析,零 LLM)。

    【参数】file_path: 落盘后的文件路径。
    【返回】list[dict]: 行字典(列名→值;MD 行附 _table 序号)。
    【异常】ValueError: 扩展名不支持/Excel 无数据/文件读不出任何表格行。
    【关键逻辑】xlsx/xls/csv 走 pandas(NaN→None,值保留原生类型);md/txt 走
              _parse_markdown_tables。xls 老格式需要 xlrd,未安装时报错提示转存。
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    rows: list[dict] = []
    if ext in (".xlsx", ".xls", ".csv"):
        import pandas as pd  # 【调用包】表格读取(xlsx 需 openpyxl,xls 需 xlrd)

        try:
            sheets = (
                pd.read_excel(path, sheet_name=None) if ext != ".csv"
                else {"csv": pd.read_csv(path)}
            )
        except ImportError as e:
            raise ValueError(f"读取 {ext} 文件缺依赖({e});请转存为 .xlsx 或 .csv 再上传") from e
        for sheet_name, df in sheets.items():
            df = df.dropna(how="all").dropna(axis=1, how="all")  # 全空行/列剔除
            for _, rec in df.iterrows():
                row = {}
                for col, val in rec.items():
                    key = str(col).strip()
                    if pd.isna(val):
                        row[key] = None
                    elif hasattr(val, "isoformat"):  # datetime → ISO 日期字符串
                        row[key] = str(val)[:19]
                    else:
                        row[key] = val
                row["_sheet"] = str(sheet_name)
                rows.append(row)
    elif ext in (".md", ".txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        rows = _parse_markdown_tables(text)
    else:
        raise ValueError(f"不支持的文件类型 {ext}")
    if not rows:
        raise ValueError("未能从文件解析出表格数据(Excel 需有内容;MD/TXT 需含管道表格)")
    return rows[:MAX_USER_DATASET_ROWS]


# ── LLM 看样张产规格 ─────────────────────────────────────────────────────

def _strip_json_fence(text: str) -> str:
    """剥掉 LLM 输出常见的 ```json 围栏,取最外层大括号内容。"""
    text = re.sub(r"```[a-zA-Z]*", "", text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def detect_spec(filename: str, sample_rows: list[dict], hint_variety: str,
                llm_client) -> dict:
    """让 LLM 看样张产出解析规格(品种/数据类型/日期列/列含义/频率)。

    【参数】filename: 原始文件名(含日期等线索);sample_rows: 前 N 行样张;
            hint_variety: 用户上传时手选的品种(非空时 LLM 结果以此为準);
            llm_client: web_app 侧 create_llm_client(...) 产物(用 quick 档即可)。
    【返回】spec dict(见 _SPEC_EXAMPLE 结构;保证 variety/columns 等键存在)。
    【异常】ValueError: LLM 返回无法解析为 JSON(状态置 error 展示给用户)。
    """
    cols = sorted({k for row in sample_rows[:SAMPLE_ROWS_FOR_LLM] for k in row
                   if not str(k).startswith("_")})
    sample = [{k: row.get(k) for k in cols} for row in sample_rows[:SAMPLE_ROWS_FOR_LLM]]
    prompt = (
        "你是数据工程师。用户上传了一个表格数据文件(用于接入期货分析系统),"
        "下面是文件名与前几行样张。请分析它的格式并输出**严格的 JSON**(不要任何解释文字),"
        "结构如下:\n"
        f"{json.dumps(_SPEC_EXAMPLE, ensure_ascii=False, indent=1)}\n\n"
        "要求:\n"
        "- columns 覆盖除日期列外的全部数据列,列名 name 必须与表头逐字一致;\n"
        "- 品种 variety 用两位字母代码(如 MA/TA/SC/LC);样张或文件名判断不出时填空串;\n"
        "- date_column 是日期/时间列的列名;确无日期列填空串;\n"
        "- 不要编造样张里没有的列。\n\n"
        f"文件名: {filename}\n"
        f"列: {cols}\n"
        f"样张: {json.dumps(sample, ensure_ascii=False, default=str)}"
    )
    result = llm_client.get_llm().invoke(prompt)
    body = str(result.content if hasattr(result, "content") else result)
    try:
        spec = json.loads(_strip_json_fence(body))
    except json.JSONDecodeError as e:
        raise ValueError("LLM 无法识别表格格式(输出不是合法 JSON),请检查文件内容") from e
    if not isinstance(spec, dict):
        raise ValueError("LLM 规格输出格式异常,请重试")
    spec.setdefault("variety", "")
    spec.setdefault("data_type", "")
    spec.setdefault("date_column", "")
    spec.setdefault("columns", [])
    spec.setdefault("frequency", "")
    spec.setdefault("notes", "")
    if hint_variety:  # 用户手选品种优先(LLM 只兜底自动识别)
        spec["variety"] = hint_variety
    return spec


def _norm_date(val) -> str:
    """单值 → 'YYYY-MM-DD...' 字符串;解析失败原样返回。

    【关键逻辑】依次尝试 fromisoformat(处理标准 ISO)与常见分隔格式
              (含非补零的 2026/9/1);都失败原样保留('第1周'等),不阻塞入库。
    """
    s = str(val).strip() if val is not None else ""
    if not s:
        return s
    try:
        dt = datetime.fromisoformat(s.replace("/", "-").replace(".", "-"))
        return dt.isoformat(sep=" ")[:19]
    except ValueError:
        pass
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).isoformat(sep=" ")[:19]
        except ValueError:
            continue
    return s


def normalize_rows(rows: list[dict], spec: dict) -> list[dict]:
    """按规格归一化数据行:日期列转 ISO,其余列原样保留(严禁数值改写)。

    【关键逻辑】只动日期列(_norm_date)与剔除内部标记列(_sheet/_table);
              数据列的值不做任何转换 —— LLM 规格只解释含义,不碰数值。
    """
    date_col = (spec.get("date_column") or "").strip()
    out = []
    for row in rows:
        clean = {k: v for k, v in row.items() if not str(k).startswith("_")}
        if date_col and date_col in clean:
            clean[date_col] = _norm_date(clean[date_col])
        out.append(clean)
    return out


# ── 数据库读写 + 分析师注入文本 ─────────────────────────────────────────

def ingest_file(dataset_id: int, file_path: str, filename: str, hint_variety: str,
                llm_client) -> None:
    """后台处理管线:读行 → LLM 产规格 → 归一化 → 回写 DB(done/error)。

    【调用方】web_app 上传路由的后台线程;异常不外抛,失败原因写进 error 字段。
    """
    from database import get_db  # 【调用包】数据库实例(懒导入避免循环)

    db = get_db()
    try:
        rows = read_file_rows(file_path)
        spec = detect_spec(filename, rows, hint_variety, llm_client)
        norm = normalize_rows(rows, spec)
        db.update_user_dataset(
            dataset_id,
            variety=(spec.get("variety") or hint_variety or "").upper(),
            data_type=spec.get("data_type") or "未分类表格数据",
            spec=json.dumps(spec, ensure_ascii=False),
            data=json.dumps(norm, ensure_ascii=False, default=str),
            row_count=len(norm),
            status="done",
            error="",
        )
    except Exception as e:  # 任何失败都落 status=error,前端可见原因
        db.update_user_dataset(dataset_id, status="error", error=str(e)[:300])
        return
    # 【上传后自检】2026-09-29:入库成功 ≠ 注入有效。渲染一次注入文本核出静默缺陷
    # (尾部取样踩脏区导致注入表格大片空白,而 status 一直 done、无任何报错)。
    # 单独兜异常:体检失败绝不能把已成功的解析结果带崩成 error。
    try:
        warning = _self_check_dataset(db.get_user_dataset(dataset_id) or {})
        if warning:
            db.update_user_dataset(dataset_id, ingest_warning=warning)
            logger.warning("自传数据 %s 入库自检告警: %s", dataset_id, warning)
    except Exception:
        logger.warning("自传数据 %s 入库自检异常(不影响解析结果)", dataset_id, exc_info=True)


# 【变量】判定日期列的候选列名(规格没给对时的兜底;吸收多块表格的表头漂移)
_DATE_COL_CANDIDATES = ("Code", "Unnamed: 0", "AssessDate", "Date", "date",
                        "日期", "时间", "交易日", "DateTime", "Datetime")


def _parseable_date(val) -> bool:
    """该值能否解析成日期(与 _norm_date 同套格式,只判真假不改写值)。"""
    s = str(val).strip() if val is not None else ""
    if not s:
        return False
    try:
        datetime.fromisoformat(s.replace("/", "-").replace(".", "-"))
        return True
    except ValueError:
        pass
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            datetime.strptime(s, fmt)
            return True
        except ValueError:
            pass
    return False


def _is_data_row(row: dict, date_col: str, cols=None) -> bool:
    """该行属于本数据集吗(真实数据行 vs 表头/单位/别的数据块的行)?

    【为什么需要】read_file_rows 只按首个 sheet 的首行定表头:当同一 sheet 里
    横排了第二块**列结构不同**的数据时,入库行里会混进「表头文本行 / 单位行 /
    另一块数据的行」—— 这些行在本数据集的列名(cols)下取值全为 None。
    若按位置取尾 N 行,恰好踩在这片脏区,注入给分析师的表格会大片空白。
    【判据】(1) 在 cols 上至少有一个非空值(列结构一致);且
            (2) 规格给的 date_col 值可解析为日期 —— 若不能,再试候选日期列
                (表头漂移时真正的日期列可能叫 Code/Unnamed: 0/日期 等)。
    【兜底】cols 未传 或 数据集确无日期列时,退化为「是否有任一非空值」。
    【注意】两个条件都必须满足:'AssessDate' 这种表头文本行在 cols 上有值
            (非空)但日期不可解析;而别的数据块的行日期可解析却在 cols 上全空。
    """
    if cols:
        if not any(row.get(c) is not None for c in cols):
            return False  # 列结构对不上 ⇒ 不是本数据集的行
    if not date_col:
        if cols:
            return True  # 无日期列:能在本列结构上取到值即视为数据行
        return any(v is not None for v in row.values())
    if _parseable_date(row.get(date_col)):
        return True
    for cand in _DATE_COL_CANDIDATES:
        if cand != date_col and cand in row and _parseable_date(row.get(cand)):
            return True
    return False


def _tail_data_rows(rows: list[dict], date_col: str, cols, n: int) -> list[dict]:
    """从尾部倒扫取最近 n 个**真实数据行**(保持原顺序);全是脏行时退回最后 n 行。"""
    real = [r for r in rows if _is_data_row(r, date_col, cols)]
    if not real:
        return rows[-n:]
    return real[-n:]


def _render_cols(rows: list[dict], spec: dict) -> list | None:
    """注入表格用哪些列(与 _render_dataset_block 同口径,自检须共用防两套口径漂移)。

    【关键逻辑】列名取材于首行的**键**(表头行的值不可用,但键与表头一致);
                只保留规格里声明过的列或日期列;对不上时兜底首行前 8 个键。
    """
    if not rows:
        return None
    date_col = (spec.get("date_column") or "").strip()
    col_specs = {c.get("name") for c in (spec.get("columns") or []) if isinstance(c, dict)}
    cols = [c for c in rows[0] if c in col_specs or c == date_col]
    if not cols:  # 规格列对不上(表头漂移)→ 兜底用首行全部键
        cols = list(rows[0].keys())[:8]
    return cols


def _render_dataset_block(ds: dict) -> str:
    """单个数据集 → 注入文本段(列说明 + 最近 N 行表格)。

    【关键逻辑】列名与取样都以**真实数据行**为准:倒扫尾部取最近 N 个真实行,
    跳过表头/单位/别块的行(否则注入表格大片空白,数据等于没注入)。
    """
    try:
        spec = json.loads(ds.get("spec") or "{}")
        rows = json.loads(ds.get("data") or "[]")
    except (json.JSONDecodeError, TypeError):
        return ""
    if not rows:
        return ""
    date_col = (spec.get("date_column") or "").strip()
    col_specs = {c.get("name"): c for c in (spec.get("columns") or []) if isinstance(c, dict)}
    cols = _render_cols(rows, spec) or []
    real = [r for r in rows if _is_data_row(r, date_col, cols)]
    recent = _tail_data_rows(rows, date_col, cols, MAX_RENDER_ROWS)
    lines = [
        f"### 数据集:《{ds.get('filename')}》({ds.get('data_type') or '未分类'};"
        f"{spec.get('frequency') or '频率未知'};共 {len(rows)} 行)"
    ]
    meanings = [
        f"{c}({col_specs[c].get('meaning', '')}{('，单位 ' + col_specs[c]['unit']) if col_specs[c].get('unit') else ''})"
        for c in cols if c in col_specs
    ]
    if meanings:
        lines.append("列含义: " + ";".join(meanings))
    if spec.get("notes"):
        lines.append(f"口径说明: {spec['notes']}")
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "---|" * len(cols))
    for r in recent:
        lines.append("| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + " |")
    n_data = len(real) or len(rows)
    if n_data > MAX_RENDER_ROWS:
        first_d = (real[0] if real else rows[0]).get(date_col, "") if date_col else ""
        last_d = recent[-1].get(date_col, "") if date_col else ""
        lines.append(
            f"(仅展示最近 {len(recent)} 行;有效数据 {n_data} 行自 {first_d} 至 {last_d},"
            "分析请以整体趋势与最新值为准)"
        )
    return "\n".join(lines)


def _self_check_dataset(ds: dict) -> str:
    """上传后自检:真的渲染一次注入文本,核出可疑就返回告警文案(无问题返回 "")。

    【为什么需要】入库成功(status=done)不等于**注入有效**。真实案例
    (Data_Platts.xlsx,2026-09-29):文件里横排了列结构不同的第二块数据,尾部
    12 行取样踩进脏区 → 注入给分析师的表格 8/12 行全空、页脚只剩 "AssessDate 至"
    —— 状态一直是 done、没有任何报错,系统"看起来在工作"却等于没注入。
    【检查项】
      ① 文件里混入的非本数据集行(表头/单位/其他数据块的横排行)有多少 ——
         这些行会被取样器跳过,**注入不受影响**,但用户该知道自己的文件里
         揉进了别的内容(常见于一个 sheet 横排两块数据的导出);
      ② 注入取样里有没有**整行全空**的行 —— 取样器一旦退化(改回按位置取尾 N 行)
         就会重现真实缺陷,这一项是那道回归的探测器;
      ③ 日期列没取到有效值(页脚只剩 "至")或全库无真实数据行 —— 注入了等于没注入。
    【纪律】只读不改 —— 不改 ds 的 data/spec,不参与归一化,纯粹体检。
    【失败模式】任何异常都返回 "",绝不因体检把解析结果带崩(与 render_user_data_context
                同纪律:自检是附加能力,不是链路的一环)。
    """
    try:
        spec = json.loads(ds.get("spec") or "{}")
        rows = json.loads(ds.get("data") or "[]")
    except (json.JSONDecodeError, TypeError):
        return ""
    if not rows:
        return "解析结果为空:没有读到任何数据行。"
    date_col = (spec.get("date_column") or "").strip()
    cols = _render_cols(rows, spec) or list(rows[0].keys())[:8]
    real = [r for r in rows if _is_data_row(r, date_col, cols)]
    recent = _tail_data_rows(rows, date_col, cols, MAX_RENDER_ROWS)
    warns: list[str] = []
    if not real:
        # 一行都认不出来 ⇒ 注入等于没注入,这一条覆盖"日期列全不可解析"与"列名对不上"
        warns.append(
            f"没有识别出可注入的真实数据行(共 {len(rows)} 行):日期列 "
            f"'{date_col or '未指定'}' 未取到可解析的日期,列名可能与实际表头不符"
        )
    else:
        skipped = len(rows) - len(real)
        if skipped:
            warns.append(
                f"文件里有 {skipped} 行不属于本数据集的内容(表头/单位/其他数据块),"
                f"已自动跳过;注入使用 {len(real)} 行真实数据"
            )
        blanks = sum(1 for r in recent if all(r.get(c) is None for c in cols))
        if blanks:
            warns.append(
                f"注入取样有 {blanks}/{len(recent)} 行整行空白(通常是文件里混了列结构不同的数据块)"
            )
        if date_col and recent:
            first_d = real[0].get(date_col)
            last_d = recent[-1].get(date_col)
            if not _parseable_date(first_d) or not _parseable_date(last_d):
                # 独立于上一条:行是靠**候选列**的日期被认出来的,而规格给的
                # date_col 本身取不到值(日期列名认错)⇒ 页脚会渲染成无意义文本。
                warns.append(
                    f"页脚日期无法解析(自 {first_d} 至 {last_d}):日期列 '{date_col}' "
                    "未取到有效值,实际日期可能藏在别的列里"
                )
    return ";".join(warns)


def render_user_data_context(variety: str, client_tag: str = "") -> str:
    """某品种的自传数据 → 分析师注入块;无数据/无客户端标识返回 ""(跳过注入)。

    【参数】client_tag: 发起分析的客户端标识(IP)。**必须非空才注入** —— 自传
            数据仅在上传电脑上使用(用户要求 2026-09-07):交互式分析带请求者
            IP;批量回测/校验等后台跑批不传 tag → 不注入(防历史行情失真)。
    【返回】形如 "# 用户自传数据(最高优先级)" 的 markdown 块,含权重规则
            (自传数据 > akshare/研报数据,冲突以用户数据为准)与数据表。
    """
    if not (variety or "").strip() or not (client_tag or "").strip():
        return ""
    try:
        from database import get_db  # 【调用包】懒导入(与 research_data 同模式)

        datasets = get_db().list_active_user_datasets(
            variety, client_tag=client_tag.strip()
        )[:MAX_ACTIVE_DATASETS]
    except Exception:  # DB 不可用时静默降级为无注入,绝不拖垮分析
        return ""
    blocks = [b for b in (_render_dataset_block(ds) for ds in datasets) if b]
    if not blocks:
        return ""
    head = (
        "# 用户自传数据(最高优先级)\n"
        f"以下是用户为 {variety} 人工上传的私有数据(一手材料,非公开源)。权重规则:\n"
        "- 在本数据覆盖的指标与时间段内,**以本数据为准** —— 优先级高于 akshare 等工具"
        "实时数据,也高于研报观点数据(用户明确要求,权重不低于研报渠道);\n"
        "- 若工具/研报数值与本数据冲突,采用本数据并明确指出差异与可能原因;\n"
        "- 本数据未覆盖的指标仍按常规流程取数;数值必须原样引用,严禁改写或臆造缺失行。\n"
    )
    return head + "\n".join(blocks)
