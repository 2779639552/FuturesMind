"""抖音平台接入测试(2026-09-07):web 层平台代码识别 + 前端平台勾选框。
适配器本体在思路2项目(tests 不跨项目依赖,适配器逻辑由手动 E2E 验证)。
"""

import web_app


def test_infer_platform_douyin():
    assert web_app._infer_platform("https://www.douyin.com/video/7412345678") == "douyin"
    assert web_app._infer_platform("https://www.douyin.com/note/7412345678") == "douyin"


def _template():
    from pathlib import Path

    tpl = Path(web_app.__file__).parent / "web_template.html"
    return tpl.read_text(encoding="utf-8")


def test_template_has_douyin_checkbox_and_hint():
    tpl = _template()
    assert 'id="plat-douyin"' in tpl  # 一键更新平台勾选框
    assert "platforms.push('douyin')" in tpl  # SSE 请求 platforms 数组
    assert "plat-douyin').checked" in tpl  # 平台估算 hint
    assert "douyin: '抖音'" in tpl  # 平台统计卡显示名
    assert ".platform-tag.douyin" in tpl  # 平台标签 CSS


def test_template_platform_boxes_all_present():
    """防回归:6 平台勾选框齐全(顺序即 UI 顺序)。"""
    tpl = _template()
    for pid in ("plat-weibo", "plat-zhihu", "plat-xhs", "plat-xueqiu", "plat-emg", "plat-douyin"):
        assert f'id="{pid}"' in tpl
