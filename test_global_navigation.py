from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_global_navigation_matches_workbench_layout():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    nav = html[html.index('<nav class="app-top-nav"'): html.index("</nav>", html.index('<nav class="app-top-nav"')) + 6]
    labels = [
        "首页",
        "技能商店",
        "发布中心",
        "内容记录",
        "素材库",
        "客资线索",
        "定时任务",
        "AI执行台",
        "教程",
    ]
    positions = [nav.index(label) for label in labels]
    assert positions == sorted(positions)
    assert 'data-view="assets" data-asset-origin-target="generated"' in nav
    assert 'data-view="assets" data-asset-origin-target="user_upload"' in nav
    assert 'data-view="global-leads" data-feature-gate="global_trade_leads_skill"' in nav

    side = html[html.index('<aside id="appSideNav"'): html.index("<!-- Chat -->")]
    assert 'id="appSideNavToggle"' in side
    assert 'id="appSideNavMount"' in side
    assert "AI调度助手" in html
    assert "AI营销创作" in html
    assert "AI海外平台" in html
    assert 'data-view="agent" data-feature-gate="agent_entry"><span class="chat-sidebar-entry-icon">销</span>' not in side
    assert 'chat-sidebar-tree-group" open' not in html


def test_navigation_runtime_mounts_and_preserves_permission_gates():
    init = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")
    assert "mountAppSideNav();" in init
    assert "openAppSideNavPlaceholder" in init
    assert "alibaba_inquiry_takeover_skill" in init
    assert "_syncAppSideNavActive(view, sourceEl);" in init
    assert "assets:" in init
    assert "data-asset-origin-target" in init


def test_my_ai_employees_use_h5_workflow_data_and_keep_sales():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    init = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")
    employee_nav = html[html.index('id="onlineEmployeeNavGroup"'): html.index("</details>", html.index('id="onlineEmployeeNavGroup"'))]

    assert 'id="onlineEmployeeNavItems"' in employee_nav
    assert 'data-online-employee="system_sales"' in employee_nav
    assert 'data-feature-gate="local_bestseller_skill"' in employee_nav
    assert 'data-nav-placeholder="HR"' not in employee_nav
    assert 'data-nav-placeholder="海外员工"' not in employee_nav
    assert 'data-jump-view="wecom-config"' not in employee_nav
    assert "/api/h5-workflows/templates" in init
    assert "_onlineEmployeeSystemKey(template)" in init
    assert "loadOnlineH5Employees();" in init
    assert "data-online-employee" in init
    assert "showAppView('h5-employees'" in init
    assert "window.open" not in init[init.index("function _onlineEmployeeEscape"):init.index("function openAppSideNavPlaceholder")]


def test_online_employee_view_mirrors_h5_workflow_contract():
    registry = (ROOT / "static" / "js" / "view-registry.js").read_text(encoding="utf-8")
    html = (ROOT / "static" / "views" / "h5-employees.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "views" / "h5-employees.js").read_text(encoding="utf-8")

    assert "registerLobsterView('h5-employees'" in registry
    assert 'id="content-h5-employees"' in html
    assert "SALES_ROWS" in script
    assert "system_sales" in script
    assert "/api/h5-workflows/templates" in script
    assert "/api/h5-workflows/activate-inline" in script
    assert "/api/h5-workflows/activate" in script
    assert "/api/h5-workflows/activations/" in script
    assert "native_wechat_add_friend" in script
    assert "native_wechat_group_invite" in script
    assert "wechat_channels_nurture" in script
    assert "window.open" not in script


def test_version_and_refresh_live_in_account_menu():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    init = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")
    header_left = html[html.index('<div class="header-left">'): html.index('<nav class="app-top-nav"')]
    dropdown = html[html.index('<div class="header-dropdown-menu">'): html.index("</header>")]

    assert 'id="clientVersionLabel"' not in header_left
    assert 'id="desktopHardRefreshBtn"' not in header_left
    assert 'id="clientVersionLabel"' in dropdown
    assert 'id="desktopHardRefreshBtn"' in dropdown
    version_formatter = init[init.index("function setClientVersionLabel"): init.index("function tryStaticClientVersionIfEmpty")]
    assert "parts.push(String(appliedAt))" not in version_formatter


def test_online_chat_uses_shared_h5_mastra_surface():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    chat = (ROOT / "static" / "js" / "mastra-chat.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "index.css").read_text(encoding="utf-8")
    init = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")

    assert 'id="onlineMastraChat"' in html
    assert 'id="mastraSessionList"' in html
    assert 'id="mastraNewSessionBtn"' in html
    assert 'id="chatNewSessionBtn"' not in html
    assert 'id="chatModeSwitch" aria-label="对话模式" hidden' in html
    assert 'class="online-mastra-chat" id="onlineMastraChat" aria-label="AI调度助手" hidden' in html
    assert 'class="chat-panel card chat-workspace is-empty" id="chatWorkspace"' in html
    assert 'class="chat-home-badge-row" hidden' in html
    assert 'id="onlineMastraPermissionCurrent"' in html
    assert 'id="onlineMastraPermissionLabel"' in html
    assert 'id="onlineMastraModelLabel">5.6 Sol 极速' in html
    assert 'id="chatHomeTools"' not in html
    assert 'mastra-chat.js' in html
    assert "/api/mastra-chat/sessions" in chat
    assert "/api/mastra-chat/messages" in chat
    assert "/api/h5-chat/messages" in chat
    assert "new MutationObserver(syncActiveViewClass)" in chat
    assert "document.body.classList.toggle('online-mastra-chat-compose'" in chat
    assert "function bindHomeEntry()" in chat
    assert "function resetToHome()" in chat
    assert "state.sending = false;\n      setComposerEnabled(true);" in chat
    assert "if (!document.getElementById('onlineMastraChat')) initChatSessions();" in init
    assert "new EventSource" in chat
    assert "online-mastra-chat-compose" in css
