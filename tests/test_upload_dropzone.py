# -*- coding: utf-8 -*-
r"""上传区（拖拽 + 选文件夹）回归护栏（2026-09-30）。

背景：用户要求「把输入文件路径改为选择本电脑中的文件路径，并支持拖拽文件到框内上传」。
落地为三件事：
  A. 三处原本只能点击的原生 file input（外文研报 / 上传研报 / 自传数据）外面套
     .dz 拖拽容器：dragover 高亮、drop 取 dataTransfer 写回 input.files，
     故 App.uploadXxx() 读 files[0] 的既有逻辑零改动。
  B. 「本地导入」原手填服务端路径文本框 → 改为拖拽/选择文件夹（webkitdirectory），
     前端读文件 → multipart 上传 → 后端落唯一临时目录 → 复用 ingest_dir。
     保留「粘贴本机路径」作为兜底（服务端同机时免一次 HTTP 上传）。
  C. 后端新增 POST /api/research/upload-folder。

本文件只做**静态文本断言**；真实拖拽行为（DataTransfer / File 构造）由
probe/_e2e_upload_dropzone.mjs 在浏览器里实测。

四条写断言的经验（都踩过，勿删）：
  1) 别做「整份源码子串断言」—— 注释里会合法地提到旧写法，会误判为残留。
     统一走 _live_occurrences()（按注释判定过滤）。
  2) 别用全局正则剥注释 —— 代码里有 https:// 这类字符串，`//` 到行尾一剥
     就把真实代码吃掉，断言静默永真。改为逐行判定行首是否为注释。
  3) 变异锚点必须精确到唯一位置，否则 replace(...,1) 打到别处，
     「未击杀」是假象。test_mutation_anchors_are_safe 专治这个。
  4) ⚠️ 拖文件夹时 e.dataTransfer.files 是**空的**（浏览器不展开目录），
     必须走 webkitGetAsEntry 递归。护栏要能杀掉「删掉递归分支」的变异，
     否则退化成「拖目录静默 0 篇」而护栏还是绿的。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web_template.html"
WEB_APP = ROOT / "web_app.py"

# 三个「单文件」拖拽区：data-dz 名 => (input id, 必须出现的 accept 扩展名)
SINGLE_DZ = {
    "fg-file": ("fg-file", [".pdf"]),
    "res-upload-file": ("res-upload-file", [".pdf", ".png", ".md", ".txt"]),
    "ud-upload-file": ("ud-upload-file", [".xlsx", ".csv", ".md", ".txt"]),
}
# 文件夹拖拽区
FOLDER_DZ = "res-local-folder"


def _tpl() -> str:
    return TEMPLATE.read_text(encoding="utf-8", errors="replace")


def _py() -> str:
    return WEB_APP.read_text(encoding="utf-8", errors="replace")


def _line_of(src: str, pos: int) -> str:
    return src[src.rfind("\n", 0, pos) + 1 : src.find("\n", pos)]


def _is_comment_pos(src: str, pos: int) -> bool:
    """pos 处是否处于注释内。

    只用两种**无歧义**判据（与 test_page_tools_variety_state 保持一致）：
    行级 `//`/`*` 开头，以及 `<-- -->` 区间。故意不做「最近未闭合 /* 推断到哪」
    的区间运算 —— 模板里有长块注释与 JS 字符串混排，区间推断会把后面整段活代码
    误判成注释，断言于是静默永真。
    """
    line = _line_of(src, pos)
    stripped = line.strip()
    if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
        return True
    # <!-- ... --> HTML 注释：用区间判定（HTML 注释不会跨越含 // 的 JS 代码）
    last_open = src.rfind("<!--", 0, pos)
    if last_open != -1:
        last_close = src.rfind("-->", 0, pos)
        if last_open > last_close:
            return True
    return False


def _live_occurrences(src: str, needle: str):
    """返回 needle 在源码里的**非注释**出现位置列表。"""
    out = []
    start = 0
    while True:
        i = src.find(needle, start)
        if i == -1:
            return out
        if not _is_comment_pos(src, i):
            out.append(i)
        start = i + 1


def _count_live(src: str, needle: str) -> int:
    return len(_live_occurrences(src, needle))


def _py_func_seg(name: str) -> str:
    """精确切出 web_app.py 里一个顶层函数的源码段。

    ⚠️ 不要用固定字符窗口（曾用 [:4200]，函数 128 行约 5000 字符，尾部被截断，
    导致「清理临时目录」断言找不到目标而假红）。以**下一个顶格 def/@** 为界才可靠。
    """
    py = _py()
    m = re.search(r"^def " + re.escape(name) + r"\(", py, re.M)
    assert m, f"web_app.py 里找不到函数 {name}"
    start = m.start()
    nxt = re.search(r"^(?:def |@app\.route)", py[m.end() :], re.M)
    end = m.end() + nxt.start() if nxt else len(py)
    return py[start:end]


def _method_body(src: str, name: str) -> str:
    """取 `name(` 起、到其配对花括号结束的方法体（模板是缩进 2 空格的对象方法）。"""
    m = re.search(r"^  (?:async )?" + re.escape(name) + r"\s*\(", src, re.M)
    if not m:
        return ""
    i = src.find("{", m.end())
    if i == -1:
        return ""
    depth = 0
    start = i
    while i < len(src):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
        i += 1
    return ""


# ══════════════════════════════════════════════════════════════
# 一、结构：三处单文件拖拽区就位
# ══════════════════════════════════════════════════════════════

def test_dz_css_block_exists():
    """拖拽区样式存在（缺了则框体没有虚线/高亮反馈，功能在但用户看不出来）。"""
    src = _tpl()
    for rule in [".dz {", ".dz.is-drag", ".dz.is-bad", ".dz.has-file .dz-file",
                 ".dz-list", ".dz-err"]:
        assert rule in src, f"缺少样式规则 {rule}"
    # 关键：原生 input 不能用 display:none 藏（部分浏览器会拒绝 click() 触发）
    m = re.search(r"\.dz\s*>\s*input\[type=file\]\s*\{([^}]*)\}", src)
    assert m, "缺少 .dz > input[type=file] 隐藏规则"
    assert "display: none" not in m.group(1).replace(" ", " "), (
        "原生 file input 不能用 display:none 隐藏 —— 会导致 click() 无法打开选择框"
    )


@pytest.mark.parametrize("dz_name", sorted(SINGLE_DZ))
def test_single_dz_structure(dz_name):
    """每个单文件拖拽区：容器 + 隐藏 input + hint + 回显 + 错误位，缺一不可。"""
    src = _tpl()
    key = SINGLE_DZ[dz_name]
    inp_id, exts = key
    assert f'data-dz="{dz_name}"' in src, f"{dz_name}: 容器缺 data-dz"
    # 容器内必须含同名 input（_dzInitAll 靠 box.querySelector 找它）
    blk_start = src.find(f'data-dz="{dz_name}"')
    assert blk_start != -1
    seg = src[blk_start : blk_start + 1800]
    assert f'id="{inp_id}"' in seg, f"{dz_name}: 容器内找不到 input#{inp_id}"
    assert 'class="dz-hint"' in seg, f"{dz_name}: 缺拖拽提示"
    assert 'class="dz-file"' in seg, f"{dz_name}: 缺已选文件回显位"
    assert 'class="dz-err"' in seg, f"{dz_name}: 缺校验失败提示位"
    for e in exts:
        assert e in seg, f"{dz_name}: accept 里缺 {e}"
    assert 'data-dz-max=' in seg, f"{dz_name}: 缺体积上限声明（应前置拦截，别等后端 400）"


def test_single_dz_max_matches_backend():
    """前端声明的体积上限必须与后端常量一致，否则会出现「前端放行→后端拒绝」。"""
    tpl = _tpl()
    py = _py()
    # 20MB 两处
    assert tpl.count('data-dz-max="20971520"') == 2, "研报/自传数据应为 20MB(20971520)"
    assert "RESEARCH_MAX_SIZE = 20 * 1024 * 1024" in py
    assert "USER_DATA_MAX_SIZE = 20 * 1024 * 1024" in py
    # 60MB 一处（外文研报）
    assert 'data-dz-max="62914560"' in tpl, "外文研报应为 60MB(62914560)"
    assert "MAX_UPLOAD_BYTES" in Path(ROOT / "foreign_report.py").read_text(
        encoding="utf-8", errors="replace"
    ), "foreign_report 里找不到 MAX_UPLOAD_BYTES"


def test_original_inputs_still_present_with_ids():
    """改造只加包裹层，原有 input id 必须原样保留（否则 App.uploadXxx 全挂）。"""
    src = _tpl()
    for inp_id in ["fg-file", "res-upload-file", "ud-upload-file", "res-local-folder"]:
        hits = _live_occurrences(src, f'id="{inp_id}"')
        assert hits, f"input#{inp_id} 消失 —— 既有上传函数会读不到文件"


# ══════════════════════════════════════════════════════════════
# 二、行为：拖拽绑定层（dragover/drop/DataTransfer/递归）
# ══════════════════════════════════════════════════════════════

def test_dz_bind_prevents_default_on_drag_events():
    """dragover/dragenter 必须 preventDefault，否则浏览器会直接打开拖入的 PDF。"""
    body = _method_body(_tpl(), "_dzBind")
    assert body, "未找到 _dzBind"
    assert "e.preventDefault()" in body, "drag 事件未 preventDefault"
    # 必须显式设 dropEffect，否则光标显示"禁止"、用户以为放不进去
    assert "dropEffect" in body, "未设置 dropEffect，拖拽光标会显示不可放置"
    # ⚠️ 用带引号的完整监听名：裸 'drop' 会被 'dropish' 这类变异子串骗过
    for ev in ["dragenter", "dragover", "dragleave", "dragend", "drop"]:
        assert f"'{ev}'" in body, f"_dzBind 未监听 {ev}"


def test_dz_assign_uses_datatransfer():
    """写回原生 input.files 只能靠 DataTransfer（FileList 是只读的）。"""
    body = _method_body(_tpl(), "_dzAssign")
    assert body, "未找到 _dzAssign"
    assert "DataTransfer" in body, "未用 DataTransfer 回填 —— input.files 无法直接赋值"
    assert "inputEl.files" in body, "未把 files 写回 input"
    # 必须有 try 兜底：旧浏览器无 DataTransfer 构造器时不能抛到用户面前
    assert "catch" in body, "DataTransfer 构造失败没有兜底"


def test_dz_folder_recursion_uses_entries_api():
    """⚠️ 核心：拖文件夹必须走 webkitGetAsEntry 递归。

    这里要能杀掉「删掉递归分支」的变异 —— 一旦只依赖 dataTransfer.files，
    拖目录会静默得到 0 个文件（files 里没有目录内容），用户看到的是"没反应"。
    """
    collect = _method_body(_tpl(), "_dzCollectFiles")
    assert collect, "未找到 _dzCollectFiles"
    assert "webkitGetAsEntry" in collect, (
        "未使用 webkitGetAsEntry —— 拖文件夹时 dataTransfer.files 是空的, 会静默 0 篇"
    )
    assert "isDirectory" in collect, "未判定目录类型"
    assert "createReader" in collect, "未用 reader 读目录内容"
    assert "readEntries" in collect, "未调 readEntries 枚举目录项"
    # 必须**循环** readEntries：单次有返回上限，只读一次会漏文件
    assert collect.count("readBatch") >= 2, (
        "readEntries 只调了一次 —— 单次有数量上限, 大目录会漏文件"
    )


def test_webkit_get_as_entry_called_once():
    """⚠️⚠️ webkitGetAsEntry 是**消耗性** API —— 全模板只允许调用一次。

    实测踩到：曾把"是否含目录"拆成独立 _dzHasDir() 先探一次，收集时再遍历第二次。
    真实拖文件夹时第二次全部返回 null，退化成 dataTransfer.files（拖目录时为空）
    ⇒ **静默收集到 0 个文件**，而程序构造的 DragEvent 里该 API 本就返回 null，
    所以 E2E 也照不出问题。只能靠静态计数锁死：整个模板里这个调用有且仅有一处。
    """
    src = _tpl()
    # ⚠️ 必须走 _live_occurrences：本函数的文档注释里也写了 webkitGetAsEntry() 与
    #    _dzHasDir（解释这个坑本身），裸子串统计会把注释一起算进去而假红（实测踩到）。
    live = _live_occurrences(src, "webkitGetAsEntry()")
    assert len(live) == 1, (
        f"webkitGetAsEntry() 有 {len(live)} 处**活代码**调用 —— 它是消耗性的, "
        "多处调用会让后一次拿到 null（拖文件夹静默 0 篇）。必须只留一处。"
    )
    # 也不允许存在"先探一次再收集"的辅助方法（同样只看活代码）
    assert not _live_occurrences(src, "_dzHasDir"), (
        "_dzHasDir 会消耗 entries，导致真正的收集拿不到条目。判定必须与收集同一次遍历完成。"
    )
    # 条目变量必须来自同一次遍历出的 entry 对象
    collect = _method_body(_tpl(), "_dzCollectFiles")
    assert "entry.isDirectory" in collect or "entry.isFile" in collect, (
        "_dzCollectFiles 未对同一批 entry 做类型分支"
    )


def test_folder_mode_filters_instead_of_rejecting():
    """多文件(文件夹)模式：应过滤非目标类型，而不是整批拒绝。

    场景：拖整个目录进来必然混着 .txt/.docx。若整批拒绝，用户看到"拖不进去"，
    但他本意是"把这堆 PDF 导进去"。单文件模式则相反 —— 要保留原始文件以便
    给出精确的"不支持的文件类型 .exe"提示。
    """
    body = _method_body(_tpl(), "_dzBind")
    assert "multiple" in body, "_dzBind 未识别多文件模式"
    # 分叉写法：multiple 时传 filter(pass)，否则传 null 保留原始
    assert "_dzCollectFiles(dt, pass)" in body, (
        "多文件模式未按扩展名过滤 —— 拖含杂项的文件夹会被整批拒绝"
    )
    assert "_dzCollectFiles(dt, null)" in body, (
        "单文件模式不应过滤 —— 过滤成空后只能报『未识别到文件』, 丢失精确原因"
    )


def test_dz_drop_filters_by_extension():
    """drop 与 change 两条路径都要按扩展名过滤（accept 只是建议，不可依赖）。"""
    body = _method_body(_tpl(), "_dzBind")
    assert "validate" in body, "_dzBind 内无校验函数"
    assert "accept.includes" in body, "未按 accept 扩展名白名单校验"
    assert "maxSize" in body and "_fmtSize" in body, "未做体积校验/回显"


def test_dz_init_all_and_wired_into_init():
    """_dzInitAll 必须被 init() 调用，否则框体永远没绑定（看着有样式、拖不进去）。"""
    body = _method_body(_tpl(), "_dzInitAll")
    assert body, "未找到 _dzInitAll"
    assert "querySelectorAll('.dz')" in body or 'querySelectorAll(".dz")' in body, (
        "_dzInitAll 未扫描 .dz 容器"
    )
    assert "_dzBind" in body, "_dzInitAll 未调用 _dzBind"
    init_body = _method_body(_tpl(), "init")
    assert "_dzInitAll" in init_body, "init() 未调用 _dzInitAll —— 拖拽区不会生效"


def test_dz_render_escapes_filename():
    """回显文件名必须转义 —— 文件名是用户可控输入，直拼 innerHTML 就是 XSS 口。"""
    body = _method_body(_tpl(), "_dzRender")
    assert body, "未找到 _dzRender"
    assert "_escHtml" in body, "文件名未转义（XSS 风险）"
    assert "innerHTML" in body, "_dzRender 应负责渲染回显"


# ══════════════════════════════════════════════════════════════
# 三、本地导入：文件夹上传 + 路径兜底双路
# ══════════════════════════════════════════════════════════════

def test_folder_dz_uses_webkitdirectory():
    """文件夹框：input 带 webkitdirectory + multiple，容器标 dz-multi。"""
    src = _tpl()
    assert f'data-dz="{FOLDER_DZ}"' in src, "本地导入没有改成拖拽区"
    blk = src[src.find(f'data-dz="{FOLDER_DZ}"') :][:1400]
    assert "webkitdirectory" in blk, "缺 webkitdirectory，无法选择文件夹"
    assert "multiple" in blk, "缺 multiple，无法多选 PDF"
    assert 'data-dz-multi="1"' in blk, "未标记多文件模式（回显会错用单文件分支）"
    assert 'data-dz-accept=".pdf"' in blk, "文件夹区应只收 PDF"


def test_local_import_has_upload_branch():
    """localImport 必须能从「浏览器选中的文件」走上传分支（新主路径）。"""
    body = _method_body(_tpl(), "localImport")
    assert body, "未找到 localImport"
    assert "_localImportUpload" in body, "localImport 未接入上传分支"
    assert "_localImportByPath" in body, "localImport 未保留路径兜底分支"
    # 判别依据：有选中文件优先走上传
    assert "files" in body, "localImport 未读取选中的文件"


def test_local_import_upload_posts_multipart():
    """上传分支：POST /api/research/upload-folder，同名键 files 多条 + org/varieties。"""
    body = _method_body(_tpl(), "_localImportUpload")
    assert body, "未找到 _localImportUpload"
    assert "/api/research/upload-folder" in body, "上传端点写错"
    assert "FormData" in body, "未用 FormData 构造 multipart"
    assert "fd.append('files'" in body or 'fd.append("files"' in body, (
        "未按 files 键追加（后端用 getlist('files') 接收）"
    )
    assert "endsWith('.pdf')" in body or 'endsWith(".pdf")' in body, "未在前端筛 PDF"
    # 必须把"被筛掉的条数"告知用户，否则数量对不上会困惑
    assert "skipped" in body, "未提示被忽略的非 PDF 数量"


def test_path_fallback_kept_and_default_collapsed():
    """路径兜底必须保留但默认收起（否则又变回主入口）。"""
    src = _tpl()
    m = re.search(r'<div id="res-local-path-box"([^>]*)>', src)
    assert m, "未找到路径兜底容器 res-local-path-box"
    assert "display:none" in m.group(1).replace(" ", ""), "路径兜底框应默认收起"
    assert 'id="res-local-path"' in src, "缺兜底路径输入框"
    assert "toggleLocalPathInput" in src, "缺路径框展开开关"
    assert _method_body(_tpl(), "toggleLocalPathInput"), "toggleLocalPathInput 未实现"


def test_pick_local_folder_implemented():
    """「选择文件夹」按钮要有实现，并处理浏览器不支持 webkitdirectory 的情况。"""
    body = _method_body(_tpl(), "pickLocalFolder")
    assert body, "未找到 pickLocalFolder"
    assert "webkitdirectory" in body, "未检测 webkitdirectory 能力"
    assert ".click()" in body, "未触发 input 点击"


def test_no_stale_folder_text_read():
    """旧写法（把 #res-local-folder 当文本框读 .value）必须清干净。

    ⚠️ 改造后 #res-local-folder 是 file input，读 .value 只会拿到假路径
    （浏览器显示 C:\\fakepath\\...），用它当目录传给后端必定 400。这是最隐蔽的回归。
    """
    src = _tpl()
    # 允许出现在注释里，故只看活代码
    bad = _live_occurrences(src, "getElementById('res-local-folder') || {}).value")
    bad2 = _live_occurrences(src, 'getElementById("res-local-folder") || {}).value')
    assert not bad and not bad2, "仍有把 #res-local-folder 当文本框读 .value 的活代码"


# ══════════════════════════════════════════════════════════════
# 四、后端端点
# ══════════════════════════════════════════════════════════════

def test_backend_endpoint_exists():
    py = _py()
    assert '@app.route("/api/research/upload-folder", methods=["POST"])' in py, (
        "后端缺 /api/research/upload-folder 端点"
    )
    assert "def api_research_upload_folder()" in py, "端点函数未定义"


def test_backend_reuses_ingest_dir():
    """后端必须复用 ingest_local_pdfs.ingest_dir —— 不能自己重写一套处理。"""
    py = _py()
    assert "from ingest_local_pdfs import ingest_dir" in py, "未复用 ingest_dir"
    seg = _py_func_seg("api_research_upload_folder")
    assert "ingest_dir(tmp_dir" in seg, "未把临时目录交给 ingest_dir"


def test_backend_uses_unique_tmp_dir():
    """临时目录名必须唯一。固定名在并发/多进程下必撞 WinError 32。"""
    seg = _py_func_seg("api_research_upload_folder")
    assert "uuid" in seg, "临时目录名未加 uuid —— 并发时会撞车"
    assert "gettempdir" in seg, "未用系统临时目录"
    assert "shutil" in seg, "未清理临时目录"


def test_backend_cleans_tmp_after_ingest():
    """删除临时目录必须在 ingest_dir **之后**（登记阶段要读原件复制进库）。"""
    seg = _py_func_seg("api_research_upload_folder")
    i_ingest = seg.find("ingest_dir(tmp_dir")
    i_rm = seg.find("shutil.rmtree(tmp_dir")
    assert i_ingest != -1 and i_rm != -1, "缺少 ingest 或清理步骤"
    assert i_rm > i_ingest, (
        "清理早于 ingest_dir —— 登记阶段读不到原件, 整批会失败"
    )
    assert "finally" in seg, "清理未放在 finally（异常时临时目录会残留）"


def test_backend_validates_and_reports_errors():
    """三态契约：非法输入必须显式报错，不得静默 0 篇。"""
    seg = _py_func_seg("api_research_upload_folder")
    for must in ["未收到任何文件", "仅支持 PDF", "超过 20MB", "FOLDER_UPLOAD_MAX_FILES",
                 "FOLDER_UPLOAD_MAX_TOTAL"]:
        assert must in seg, f"后端缺少校验/报错：{must}"
    assert "_research_collecting" in seg, "未复用采集闸门（会与采集抢 LLM 并发）"
    assert 'jsonify({"error"' in seg, "错误分支未返回 error 字段"


def test_backend_dedups_same_filename():
    """同名 PDF 去重：拖多子目录常有同名文件，后写会覆盖前者（静默少篇）。"""
    seg = _py_func_seg("api_research_upload_folder")
    assert "seen" in seg, "未做同名文件去重"


# ══════════════════════════════════════════════════════════════
# 五、变异自检：确认这些断言真的会红
# ══════════════════════════════════════════════════════════════

MUTATIONS = [
    # (说明, 原文, 变异后)  —— 每条都必须让至少一个断言失败
    ("去掉 drop 监听", "boxEl.addEventListener('drop', async (e) => {",
     "boxEl.addEventListener('dropish', async (e) => {"),
    ("去掉 preventDefault", "e.preventDefault(); e.stopPropagation();\n      // 必须显式声明 copy 效果",
     "// removed\n      // 必须显式声明 copy 效果"),
    ("放弃 DataTransfer 回填", "inputEl.files = dt.files;", "void dt.files;"),
    ("删掉目录递归分支", "} else if (entry.isDirectory) {", "} else if (false) {"),
    ("不再读满目录", "readBatch();   // readEntries 单次有返回上限, 必须循环读到空为止",
     "/* no loop */"),
    # ⚠️ 复现真实踩过的坑：再插一处 webkitGetAsEntry() 调用（消耗性 API 被调两次）
    ("重复消耗 entries", "if (e) ents.push(e);",
     "const _probe = it.webkitGetAsEntry(); if (e) ents.push(e);"),
    ("文件夹模式不过滤", "_dzCollectFiles(dt, pass)",
     "_dzCollectFiles(dt, null)"),
    ("init 不绑拖拽", "try { this._dzInitAll(); }", "try { void 0; }"),
    ("文件名不过滤", "const pdfs = files.filter(f => (f.name || '').toLowerCase().endsWith('.pdf'));",
     "const pdfs = files.slice();"),
    ("上传端点写错", "fetch('/api/research/upload-folder', { method: 'POST', body: fd })",
     "fetch('/api/research/upload-wrong', { method: 'POST', body: fd })"),
    ("后端不再清理临时目录", "shutil.rmtree(tmp_dir, ignore_errors=True)",
     "void tmp_dir"),
]


def _run_assertions_on(src_tpl: str, src_py: str) -> str:
    """在给定源码上跑一遍关键断言，返回 '' 表示全过，否则返回首个失败信息。

    故意与上面的用例**共用同一批判据字符串**，而不是另写一套 —— 否则会出现
    「变异逃过检测但单测也测不出」的双重失效。
    """
    if "boxEl.addEventListener('drop', async (e) => {" not in src_tpl:
        return "drop 监听缺失"
    if "e.preventDefault(); e.stopPropagation();\n      // 必须显式声明 copy 效果" not in src_tpl:
        return "drag 未 preventDefault"
    if "inputEl.files = dt.files;" not in src_tpl:
        return "未回填 input.files"
    # 递归分支：必须同时有 isDirectory 分支与循环读
    if "} else if (entry.isDirectory) {" not in src_tpl:
        return "目录递归分支缺失"
    if "readBatch();   // readEntries 单次有返回上限, 必须循环读到空为止" not in src_tpl:
        return "readEntries 未循环读满"
    # ⚠️ webkitGetAsEntry 只能调一次（消耗性 API）。用 _live_occurrences 排除注释。
    n_live = len(_live_occurrences(src_tpl, "webkitGetAsEntry()"))
    if n_live != 1:
        return f"webkitGetAsEntry() 活代码调用了 {n_live} 次（应为 1）"
    if _live_occurrences(src_tpl, "_dzHasDir"):
        return "存在 _dzHasDir（会重复消耗 entries）"
    # 文件夹模式必须过滤而非整批拒绝
    if "_dzCollectFiles(dt, pass)" not in src_tpl:
        return "文件夹模式未过滤（拖含杂项目录会被整批拒）"
    if "try { this._dzInitAll(); }" not in src_tpl:
        return "init 未绑定拖拽区"
    if "endsWith('.pdf')" not in src_tpl:
        return "前端未筛 PDF"
    # ⚠️ 必须用**完整的活代码调用**，不能只查裸端点名 —— _localImportUpload 的
    #    文档注释里也写着 `/api/research/upload-folder`，只查裸名会被注释骗过，
    #    变异「端点写错」于是静默逃逸（实测踩到）。
    if "fetch('/api/research/upload-folder', { method: 'POST', body: fd })" not in src_tpl:
        return "上传端点错误"
    if "shutil.rmtree(tmp_dir, ignore_errors=True)" not in src_py:
        return "未清理临时目录"
    return ""


def test_baseline_passes_assertions():
    """先证明未变异时全过 —— 否则后面「击杀」全是假象。"""
    assert _run_assertions_on(_tpl(), _py()) == ""


@pytest.mark.parametrize("desc,old,new", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_guard_kills_mutation(desc, old, new):
    tpl, py = _tpl(), _py()
    if old in tpl:
        tpl2 = tpl.replace(old, new, 1)
        py2 = py
    elif old in py:
        py2 = py.replace(old, new, 1)
        tpl2 = tpl
    else:
        pytest.fail(f"变异锚点在源码里找不到（锚点已失效，需更新）: {old!r}")
    assert tpl2 != tpl or py2 != py, f"变异未生效: {desc}"
    err = _run_assertions_on(tpl2, py2)
    assert err, f"变异「{desc}」未被击杀 —— 护栏太松, 必须收紧"


def test_mutation_anchors_are_safe():
    """变异锚点必须唯一，否则 replace(...,1) 会打到别处，「未击杀」是假象。"""
    tpl, py = _tpl(), _py()
    for desc, old, new in MUTATIONS:
        n = tpl.count(old) + py.count(old)
        assert n == 1, (
            f"变异锚点「{desc}」命中 {n} 次（应为 1）—— 锚点不唯一, 变异可能打偏"
        )


# ══════════════════════════════════════════════════════════════
# 六、回归：目录选择器「空文件夹」缺陷（2026-09-30 用户报障）
# ══════════════════════════════════════════════════════════════
# 【症状】能选中文件夹上传，但系统弹窗里"看不到研报"，显示为空文件夹。
# 【根因】accept 属性被挂在 webkitdirectory 目录选择器上 —— Chromium 会把它当成
#   "目录内文件过滤器"套到系统目录对话框，目录里真实存在的 PDF 于是不作为可选项
#   出现（呈"空文件夹"）。另有内联 display:none 盖掉了 .dz>input 的视觉隐藏保活。
# 【说明】Windows 的系统目录对话框内容区本就只列子目录、不列文件（Chromium 固有
#   行为），故同时补了「直接选文件」入口，让用户能在弹窗里真正看见并勾选 PDF。

def test_dir_picker_has_no_accept_attribute():
    """⚠️⚠️ 目录选择器(input[webkitdirectory])上绝不能有 accept —— 会让目录显示为空。"""
    src = _tpl()
    i = src.find('id="' + FOLDER_DZ + '"')
    assert i != -1, "找不到目录选择器 input"
    seg = src[i:i + 600]          # 只看该 input 自己的属性
    assert "webkitdirectory" in seg, "该 input 已不是目录选择器"
    tag = seg.split(">", 1)[0]    # 断言到第一个 '>' 为止的标签体内不含 accept
    assert "accept" not in tag, (
        "目录选择器上带了 accept —— Chromium 会当成目录内过滤器, "
        "使文件夹里真实存在的 PDF 显示为'空文件夹'（用户报障的根因）\n"
        "标签体: " + tag
    )


def test_dir_picker_is_not_display_none():
    """目录选择器不能用 display:none（会盖掉 CSS 保活, 部分浏览器拒绝 click()）。"""
    src = _tpl()
    i = src.find('id="' + FOLDER_DZ + '"')
    tag = src[i:i + 600].split(">", 1)[0]
    assert "display:none" not in tag.replace(" ", ""), (
        "目录选择器加了内联 display:none —— 优先级高于 .dz>input 的视觉隐藏, "
        "会盖掉保活规则, 部分浏览器点不开选择框"
    )


def test_dzinit_ignores_accept_for_dir_picker():
    """_dzInitAll 取 accept 时必须对 webkitdirectory 特判 —— 这是 bug 的第二道门。

    单删 HTML 上的 accept 只是治标：将来有人"顺手补全属性"就会复发。
    代码层面必须保证目录选择器**永不**从 input 回落读 accept。
    """
    body = _method_body(_tpl(), "_dzInitAll")
    assert body, "未找到 _dzInitAll"
    assert "webkitdirectory" in body, (
        "_dzInitAll 未对 webkitdirectory 特判 accept 来源 —— "
        "目录选择器会重新从 input 读到 accept, 缺陷复发"
    )
    assert "isDirPicker" in body, "缺少目录选择器的判别变量"
    assert "isDirPicker ? '' : input.getAttribute('accept')" in body, (
        "accept 回落未对目录选择器短路"
    )


def test_local_files_fallback_entry_exists():
    """必须有「直接选文件」兜底 —— 系统目录弹窗不列文件, 用户无从确认。"""
    src = _tpl()
    assert 'id="res-local-files"' in src, "缺「直接选文件」兜底 input"
    # 精确取该 input 的**标签体**: 从它前面的 '<input' 起到第一个 '>'
    i = src.find('id="res-local-files"')
    tag_start = src.rfind("<input", 0, i)
    assert tag_start != -1, "res-local-files 不在某个 <input> 标签内"
    tag = src[tag_start:src.find(">", i)]
    assert "multiple" in tag, "兜底 input 应支持多选(shift 连选一批 PDF)"
    assert "webkitdirectory" not in tag, "兜底 input 不该是目录选择器(否则等于没兜底)"
    assert 'App.pickLocalFiles()' in src, "缺「直接选文件」按钮"
    assert _method_body(_tpl(), "pickLocalFiles"), "pickLocalFiles 未实现"
    assert _method_body(_tpl(), "_bindLocalFilesFallback"), "_bindLocalFilesFallback 未实现"


def test_fallback_file_input_is_outside_dz_box():
    """⚠️ 兜底 input 必须在 .dz 容器**之外**。

    _dzInitAll 用 box.querySelector('input[type=file]') 取框内第一个 input；
    若把兜底 input 放进 .dz 里, 会把目录选择器顶掉, 拖拽/文件夹上传整体失效。
    """
    src = _tpl()
    i_box = src.find('data-dz="' + FOLDER_DZ + '"')
    assert i_box != -1
    seg = src[i_box:i_box + 1400]   # 覆盖该面板的 .dz 容器区间
    assert 'id="res-local-files"' not in seg, (
        "兜底 input 被放进了 .dz 容器内 —— _dzInitAll 会误取它, 破坏目录选择器绑定"
    )


def test_localimport_merges_both_inputs():
    """localImport 必须同时认两个 input（主入口优先），否则兜底选了也不生效。"""
    body = _method_body(_tpl(), "localImport")
    assert "res-local-folder" in body, "未读主入口"
    assert "res-local-files" in body, (
        "localImport 未读取「直接选文件」兜底 input —— 用兜底选的 PDF 提交不上去"
    )
    assert "pickFiles.length" in body, "未做'主入口优先'的合并判断"


def test_rereslecting_same_dir_clears_value():
    """重复选同一目录必须仍触发 change（先清 value）。"""
    body = _method_body(_tpl(), "pickLocalFolder")
    assert body, "未找到 pickLocalFolder"
    assert "inp.value = ''" in body, (
        "选中前未清空 value —— 再次选中同一目录时 change 不触发, 用户以为没反应"
    )


# ── 本缺陷的变异自检 ────────────────────────────────────────────

FOLDER_MUTATIONS = [
    ("目录选择器加回 accept",
     'webkitdirectory directory multiple>',
     'webkitdirectory directory multiple accept=".pdf">'),
    ("目录选择器加回 display:none",
     'webkitdirectory directory multiple>',
     'webkitdirectory directory multiple style="display:none">'),
    ("accept 回落不再特判目录",
     "isDirPicker ? '' : input.getAttribute('accept')",
     "input.getAttribute('accept')"),
    ("localImport 不读兜底 input",
     "if (!pickFiles.length && fbInp) pickFiles = Array.from(fbInp.files || []);",
     "void fbInp;"),
    ("pickLocalFolder 不清 value",
     "inp.value = '';\n    inp.click();",
     "inp.click();"),
    ("兜底 input 塞进 .dz 内",
     '<div class="dz-list"></div>',
     '<div class="dz-list"></div><input type="file" id="res-local-files">'),
]


def _run_folder_assertions_on(src: str) -> str:
    """与上面用例共用同一批判据。返回 '' 表示全过。"""
    i = src.find('id="' + FOLDER_DZ + '"')
    if i == -1:
        return "找不到目录选择器"
    tag = src[i:i + 600].split(">", 1)[0]
    if "webkitdirectory" not in tag:
        return "已不是目录选择器"
    if "accept" in tag:
        return "目录选择器带了 accept（空文件夹缺陷复发）"
    if "display:none" in tag.replace(" ", ""):
        return "目录选择器带了 display:none"
    j = src.find("_dzInitAll() {")
    if j == -1:
        return "缺 _dzInitAll"
    body = src[j:j + 1600]
    if "isDirPicker ? '' : input.getAttribute('accept')" not in body:
        return "accept 回落未对目录选择器短路"
    if 'id="res-local-files"' not in src:
        return "缺兜底 input"
    if 'App.pickLocalFiles()' not in src:
        return "缺兜底按钮"
    k = src.find('data-dz="' + FOLDER_DZ + '"')
    if 'id="res-local-files"' in src[k:k + 1400]:
        return "兜底 input 在 .dz 容器内"
    li = src.find("async localImport() {")
    if li == -1:
        return "缺 localImport"
    lib = src[li:li + 1200]
    # ⚠️ 必须断言**合并语句**本身, 不能只查 'res-local-files' 是否出现 ——
    #    该 id 在上一行 const 声明里也有, 只查名字会让"删掉合并逻辑"静默逃逸
    #    （实测踩到: 变异未被击杀）。
    if "if (!pickFiles.length && fbInp) pickFiles = Array.from(fbInp.files || [])" not in lib:
        return "localImport 未做兜底 input 的合并读取"
    pf = src.find("pickLocalFolder() {")
    if pf == -1:
        return "缺 pickLocalFolder"
    pfb = src[pf:pf + 600]
    if "inp.value = ''" not in pfb:
        return "pickLocalFolder 未清 value"
    return ""


def test_folder_baseline_passes_assertions():
    """先证明未变异时全过 —— 否则后面「击杀」全是假象。"""
    assert _run_folder_assertions_on(_tpl()) == ""


@pytest.mark.parametrize("desc,old,new", FOLDER_MUTATIONS,
                         ids=[m[0] for m in FOLDER_MUTATIONS])
def test_folder_guard_kills_mutation(desc, old, new):
    tpl = _tpl()
    assert tpl.count(old) == 1, (
        "变异锚点「%s」命中 %d 次（应为 1）—— 锚点不唯一, 变异会打偏"
        % (desc, tpl.count(old))
    )
    tpl2 = tpl.replace(old, new, 1)
    assert tpl2 != tpl, "变异未生效: " + desc
    err = _run_folder_assertions_on(tpl2)
    assert err, "变异「%s」未被击杀 —— 护栏太松, 必须收紧" % desc
