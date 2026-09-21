"""阿里接管工作台：多级界面 / 弹窗 / 抽屉 的结构回归（读静态文件断言，防止又堆回一屏）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "static" / "views" / "alibaba-inquiries.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "js" / "alibaba-inquiries.js").read_text(encoding="utf-8")


def test_shell_has_nav_main_drawer_modal_toast_containers():
    for anchor in ('id="aliNav"', 'id="aliMain"', 'id="aliDrawer"', 'id="aliModal"', 'id="aliToasts"',
                   'id="aliDrawerMask"', 'id="aliModalMask"', 'id="aliAccountSelect"'):
        assert anchor in HTML, anchor


def test_hidden_overlays_are_explicitly_hidden_by_css():
    """hidden 属性必须压过 display 规则，否则弹窗一进页面就盖住整屏（曾踩过）。"""
    assert '.ali-modal-mask[hidden]' in HTML
    assert 'display: none !important' in HTML


def test_nav_defines_expected_sections():
    for key in ("desk", "inquiries", "pool", "customers", "kb", "persona", "rules", "accounts"):
        assert "key: '" + key + "'" in JS, key


def test_view_renderers_and_components_exist():
    for fn in ("renderDesk", "renderInquiries", "renderPool", "renderCustomers", "renderKb",
               "renderPersona", "renderRules", "renderAccounts", "openModal", "closeModal",
               "openDrawer", "closeDrawer", "showInquiryDrawer", "showArchiveDrawer",
               "showPoolDrawer", "showPoolImportModal", "showRunModal", "showDocModal"):
        assert "function " + fn in JS, fn


def test_new_reception_endpoints_are_wired():
    for path in ("/reception-config", "/dashboard", "/reception/run", "/takeover", "/public-pool/targets"):
        assert path in JS, path


def test_dry_run_is_default_path_for_run_modal():
    assert "showRunModal(!!(S.config && S.config.dry_run))" in JS


def test_list_badges_use_the_same_source_as_their_lists():
    """角标不能拿"询盘总数"当客户档案数；知识库列表要能吃下接口的 items 字段。"""
    assert "stats.customer_archives" in JS
    assert "stats.inquiries : null" not in JS
    assert "(data.items || data.docs || data.documents)" in JS
