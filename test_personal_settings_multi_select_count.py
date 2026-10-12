"""记忆文件「新增记忆」里三个多选（关键词 / 同行 / 资料）的选中计数。

2026-10-12 用户反馈：选第一个不计数、选第二个显示 1、选了 4 个取消一个又显示 4。
根因（已修）：多选组件里「写状态」的监听把属性名写死成 data-ps-option，而这些
checkbox 实际用的是 data-ps-memory-source（绑不上、状态压根没写）；而负责刷新计数的
监听又在状态写入之前读，所以面板永远比真实选中数慢一拍。

本测试锁定修复后的形状，防止回归。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_multi_select_writes_state_before_refreshing_summary():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    write_marker = "selected[input.value] = input.checked;   // 2026-10-12"
    refresh_call = "updatePsMultiSelectSummary(el, rows, idFn, titleFn, selected);"
    assert write_marker in script, "多选组件缺少「先写状态」"
    assert refresh_call in script
    assert script.index(write_marker) < script.index(refresh_call), "必须先在同一个监听里写状态，再刷新计数"
    # 不允许再出现「只刷新计数、不写状态」的监听
    assert "input.addEventListener('change', function() { updatePsMultiSelectSummary" not in script


def test_option_check_binding_uses_real_attribute_name():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "function bindOptionChecks(el, kind, selected, attributeName)" in script
    assert "var attr = attributeName || 'data-ps-option';" in script
    assert "bindOptionChecks(el, opts.kind, selected, attrName);" in script
    # 记忆来源选择器（关键词 / 同行 / 资料）用的是这个属性名，必须能被绑上
    assert "attributeName: 'data-ps-memory-source'" in script
