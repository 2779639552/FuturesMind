"""Tests for the Weibo QR-login flow (2026-09-18).

背景：微博长期处于**游客态**，每个关键词只采到 8 条（第 2 页起 `ok=-100`），
表现为"微博数据莫名只有两三百条"。修这个问题的过程中，登录脚本本身暴露了
四个坑，本文件把每一个都锁成回归：

1. **goto 错了页面**：旧脚本 `goto("https://m.weibo.cn/")` —— 那是手机版
   **信息流首页**，实测 0 个二维码元素、无"扫码"字样。用户打开窗口后
   "没看到扫码"，82 秒后关窗走人。必须先到 passport 登录页。
2. **登录页默认不显示二维码**：默认只展示"账号密码"表单，二维码要再点一次
   底部「扫码登录」才出现。脚本必须替用户点掉这一步。
3. **二维码元素存在 ≠ 图片已渲染**：实测元素先出现、图还是白的
   （`naturalWidth=0`，截图是个空白框）。只判"元素存在"会把白框当就绪，
   用户又一次"没看到扫码"。必须等 `complete && naturalWidth > 0`。
4. **跨域同名 SUB**：扫码走 `passport.weibo.com`，会给 `.weibo.com` 下发一份 SUB；
   而采集端打 `m.weibo.cn`，用的是 `.weibo.cn` 域的 SUB（游客态也有一份）。
   两者一起拼进请求头会出现 `SUB=..; SUB=..`，服务端取哪份不确定 ——
   最坏表现是"登录明明成功、采回来还是 8 条"。

登录脚本用 importlib 直接加载，桩掉页面与网络，不启动浏览器、不发真实请求。
"""

import importlib.util
import sys
from pathlib import Path

_VALIDATE_DIR = Path(__file__).resolve().parents[1] / "data_collection" / "validate"
_LOGIN_PATH = _VALIDATE_DIR / "weibo_login.py"


def _load_login():
    """加载登录脚本模块（模块级只依赖 json/os/sys/time/pathlib，不含 Playwright）。"""
    spec = importlib.util.spec_from_file_location("weibo_login_under_test", _LOGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["weibo_login_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 桩

class FakeCtx:
    """假浏览器上下文：只需要回答 cookies()。"""

    def __init__(self, cookies=None, on_cookies=None):
        self._cookies = cookies or []
        self._on_cookies = on_cookies
        self.closed = False

    def cookies(self):
        if self._on_cookies is not None:
            return self._on_cookies()
        return self._cookies

    def close(self):
        self.closed = True


class FakePage:
    """假页面：记录动作序列，可控制"二维码是否真的加载出来"。"""

    def __init__(self, qr_loads=True, click_works=True, qr_element_count=1):
        self.actions = []
        self.qr_loads = qr_loads
        self.click_works = click_works
        self.qr_element_count = qr_element_count

    # --- 被 _prepare_login_page / _qr_box_note 调用到的接口 ---
    def goto(self, url, **kw):
        self.actions.append(("goto", url))

    def wait_for_timeout(self, ms):
        self.actions.append(("wait", ms))

    def wait_for_function(self, js, timeout=None):
        self.actions.append(("wait_fn",))
        if self.qr_loads:
            return True
        raise TimeoutError("二维码图片始终没有渲染")

    def click(self, sel, timeout=None):
        self.actions.append(("click", sel))
        if not self.click_works:
            raise RuntimeError("selector not found")

    def evaluate(self, js):
        self.actions.append(("evaluate",))
        return {"natural": 180, "box": 180}

    def locator(self, sel):
        """旧实现靠"元素存在"判就绪；这里照样能报"元素在"，用来证明新实现不靠它。"""
        self.actions.append(("locator", sel))
        page = self

        class _L:
            def count(self_inner):
                return page.qr_element_count

        return _L()


# ---------------------------------------------------------------- 1. 必须去登录页

def test_login_url_is_passport_not_the_feed_home():
    """核心回归：goto 的目标必须是 passport 登录页，不能是 m.weibo.cn 信息流首页。"""
    mod = _load_login()
    url = mod.LOGIN_URL
    assert "passport" in url, f"LOGIN_URL 必须指向 passport 登录页, 实际: {url}"
    assert "//m.weibo.cn" not in url, (
        "LOGIN_URL 不能以 m.weibo.cn 为主站 —— 那是信息流首页, "
        "既无二维码也无登录框, 用户会'没看到扫码'。实际: %s" % url
    )


def test_login_url_returns_to_m_weibo_cn_after_scan():
    """登录成功后必须回跳 m.weibo.cn：只有它被访问过才会下发 .weibo.cn 域会话 Cookie。"""
    mod = _load_login()
    assert "m.weibo.cn" in mod.LOGIN_URL, "必须带 r= 回跳参数指向采集端真正使用的 m.weibo.cn"
    assert "r=" in mod.LOGIN_URL, "缺少 r= 回跳参数"


# ---------------------------------------------------------------- 2/3. 二维码必须"真的显示"

def test_prepare_login_page_goes_to_login_url_then_clicks_scan_tab():
    """"账号密码"表单页上二维码要点一次「扫码登录」才出来，脚本必须自动点。"""
    mod = _load_login()
    page = FakePage(qr_loads=False, click_works=True)
    note = mod._prepare_login_page(page)

    assert page.actions[0] == ("goto", mod.LOGIN_URL), "第一步必须是打开登录页"
    clicked = [a[1] for a in page.actions if a[0] == "click"]
    assert any("扫码登录" in c for c in clicked), (
        "必须自动点击底部「扫码登录」, 实际点击: %s" % clicked
    )
    assert not note.startswith("QR_READY"), "图没出来就不能报就绪"


def test_prepare_login_page_requires_loaded_qr_image_not_just_dom_node():
    """坑 3 回归：元素存在但图片未渲染(naturalWidth=0)时，绝不能判"二维码已就绪"。

    旧实现判 `locator(QR_IMG_SELECTOR).count()`，在白框状态下会误报就绪 ——
    用户看到的仍是一个没有码的空白框。
    """
    mod = _load_login()
    page = FakePage(qr_loads=False, click_works=True, qr_element_count=1)
    note = mod._prepare_login_page(page)

    assert page.qr_element_count == 1, "本用例前提: DOM 里元素确实存在"
    assert not note.startswith("QR_READY"), (
        "元素存在但图片未加载时必须判未就绪, 实际: %s" % note
    )
    assert "未能自动点出二维码" in note


def test_prepare_login_page_reports_ready_only_after_image_renders():
    """图片真的渲染出来后才报就绪，并带上尺寸诊断。"""
    mod = _load_login()
    page = FakePage(qr_loads=True)
    note = mod._prepare_login_page(page)
    assert note.startswith("QR_READY"), note
    assert "180" in note, "应带上二维码尺寸说明, 便于排查'码太小扫不动'"


def test_qr_loaded_probe_checks_natural_width():
    """防退回：就绪判据必须查 naturalWidth(渲染完成)，而不是只看元素/选择器。"""
    mod = _load_login()
    js = mod.QR_LOADED_JS
    assert "naturalWidth" in js, "必须检查 naturalWidth, 否则白框会被当成就绪"
    assert "complete" in js, "必须检查 complete"


# ---------------------------------------------------------------- 4. 跨域同名 SUB

def test_cookie_variants_collapse_cross_domain_duplicate_sub():
    """坑 4 回归：去重后的候选里 SUB 只能出现一次(否则服务端取到哪份不确定)。"""
    mod = _load_login()
    ctx = FakeCtx([
        {"name": "SUB", "value": "GUEST_CN", "domain": ".weibo.cn"},
        {"name": "SUB", "value": "LOGGED_COM", "domain": ".weibo.com"},
        {"name": "SUBP", "value": "p1", "domain": ".weibo.cn"},
    ])
    variants = mod._cookie_variants(ctx)
    labels = [label for label, _ in variants]
    assert "weibo.cn优先" in labels and "weibo.com优先" in labels, (
        "两个方向都要试: 登录态落在哪个域由真实请求判定, 不能靠猜。实际: %s" % labels
    )

    def sub_count(ck):
        return sum(1 for part in ck.split("; ") if part.split("=")[0] == "SUB")

    for label, ck in variants:
        if label == "原样全量":
            assert sub_count(ck) == 2, "原样全量本就该暴露重复, 用来佐证去重的必要性"
        else:
            assert sub_count(ck) == 1, f"[{label}] 里 SUB 必须唯一, 实际: {ck}"
    # 两个方向的去重结果必须不同(分别保留各自域的值)
    values = {label: ck for label, ck in variants}
    assert values["weibo.cn优先"] != values["weibo.com优先"]


# ---------------------------------------------------------------- 5. 不能假成功

def test_validate_context_rejects_guest_cookie(monkeypatch):
    """游客态(有 SUB 但 login=False)必须判失败，且不返回可落盘的 Cookie。"""
    mod = _load_login()
    monkeypatch.setattr(mod, "_is_logged_in", lambda ck: False)
    ok, note, ck = mod._validate_context(FakeCtx([{"name": "SUB", "value": "g", "domain": ".weibo.cn"}]))
    assert ok is False
    assert ck == "", "失败时不能返回 Cookie, 否则会覆盖掉旧凭证"
    assert "游客态" in note


def test_validate_context_rejects_logged_in_but_page2_blocked(monkeypatch):
    """已登录但第 2 页被拒(风控)也必须判失败 —— 这种 Cookie 落盘也只采 8 条。"""
    mod = _load_login()
    monkeypatch.setattr(mod, "_is_logged_in", lambda ck: True)
    monkeypatch.setattr(mod, "_probe", lambda ck, page: {"ok": -100})
    ok, note, ck = mod._validate_context(FakeCtx([{"name": "SUB", "value": "g", "domain": ".weibo.cn"}]))
    assert ok is False, "第 2 页翻不动就不算可用登录态"
    assert ck == ""
    assert "第 2 页" in note


def test_validate_context_returns_winning_cookie_on_success(monkeypatch):
    """两条判据都过时，返回的必须是**被验证通过的那一份** Cookie 串。"""
    mod = _load_login()
    monkeypatch.setattr(mod, "_is_logged_in", lambda ck: True)
    monkeypatch.setattr(mod, "_probe", lambda ck, page: {"ok": 1})
    ctx = FakeCtx([
        {"name": "SUB", "value": "GUEST_CN", "domain": ".weibo.cn"},
        {"name": "SUB", "value": "LOGGED_COM", "domain": ".weibo.com"},
    ])
    ok, note, ck = mod._validate_context(ctx)
    assert ok is True
    subs = [p for p in ck.split("; ") if p.split("=")[0] == "SUB"]
    assert len(subs) == 1, "落盘的 Cookie 里 SUB 必须唯一, 实际: %s" % subs
    assert "第 2 页可翻" in note


# ---------------------------------------------------------------- 6. 窗口被关掉要能补救

def test_harvest_once_is_headless_and_always_closes_context(monkeypatch):
    """窗口被关掉后靠 _harvest_once 补救：必须以无头方式开新上下文，且无论成败都关掉。"""
    mod = _load_login()
    seen = {}

    def fake_open(p, headless):
        seen["headless"] = headless
        ctx = FakeCtx([])
        seen["ctx"] = ctx
        return ctx

    monkeypatch.setattr(mod, "_open_context", fake_open)
    ok, note, ck = mod._harvest_once(object())
    assert seen["headless"] is True, "补救收割必须无头(不打扰用户)"
    assert seen["ctx"].closed is True, "上下文必须被关闭, 否则下次启动会撞 profile 锁"
    assert ok is False and ck == ""
