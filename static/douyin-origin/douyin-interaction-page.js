let douyinTasks = [];
let douyinAccounts = [];
let activeDouyinAccountId = null;
let douyinInteractionUsers = [];
let douyinInteractionState = {};
let douyinInteractionRunning = false;
let interactionSelectionKeys = new Set();
let interactionSelectionTouched = false;
let interactionCurrentPage = 1;
let interactionRegionFilter = "all";
let interactionTimeFilterDays = "all";
let interactionFollowStatusFilter = "all";
let interactionMessageStatusFilter = "all";
let interactionPollTimer = 0;
let interactionPollInFlight = false;
let douyinInteractionPresetState = { activeIndex: 0, presets: [] };

const defaultDouyinInteractionMessage = "你好，我看到你对 OpenClaw 感兴趣，方便交流一下你现在最想解决的问题吗？";
const douyinInteractionPresetCount = 10;
const douyinInteractionPresetStorageKey = "douyin-interaction-message-presets-v1";

const esc = value => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
const cut = (value, max = 30) => {
    const text = String(value ?? "");
    return text.length > max ? `${text.slice(0, max)}...` : text;
};
const normalize = value => String(value ?? "").replace(/\u200b|\ufeff/g, "").replace(/\s+/g, " ").trim();

function toggleSidebarGroup(id) {
    const target = document.getElementById(id);
    if (!target) return;
    const group = target.closest(".sidebar-group");
    const shouldOpen = !group?.classList.contains("is-open");
    document.querySelectorAll(".sidebar-group[data-collapsible='true']").forEach(item => {
        const open = shouldOpen && item === group;
        item.classList.toggle("is-open", open);
        const header = item.querySelector(".sidebar-group-header");
        if (header) header.setAttribute("aria-expanded", open ? "true" : "false");
    });
}

function addLog(message, level = "info") {
    const box = document.getElementById("log-content");
    if (!box) return;
    const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    const entry = document.createElement("div");
    entry.className = "log-entry";
    entry.innerHTML = `<span class="log-time">${time}</span><span class="log-level-${esc(level)}">${esc(message)}</span>`;
    box.appendChild(entry);
    while (box.children.length > 120) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
}

function clearLogs() {
    const box = document.getElementById("log-content");
    if (box) box.innerHTML = "";
}

async function fetchJson(url, options = {}) {
    const res = await fetch(url, options);
    const data = await res.json();
    if (!res.ok) throw new Error(data?.msg || data?.detail || `HTTP ${res.status}`);
    return data;
}

function setBtn(button, loading, idleText, loadingText) {
    if (!button) return;
    button.disabled = !!loading;
    button.textContent = loading ? loadingText : idleText;
}

function userChoiceKey(user = {}) {
    const id = normalize(user.comment_id || user.commentId || "");
    if (id) return `id:${id}`;
    return [
        normalize(user.user_id || user.userId || ""),
        normalize(user.username || ""),
        normalize(user.comment || user.content || ""),
        normalize(user.comment_time || ""),
    ].join("|");
}

function parseCommentMeta(row = {}) {
    const explicitRegion = normalize(row.region || row.location || row.province || row.city || row.ip_location || "");
    const rawCommentTime = normalize(row.comment_time || "");
    const parts = rawCommentTime.split(/[·•・]/).map(normalize).filter(Boolean);
    const commentTime = parts.length ? parts[0] : rawCommentTime;
    const derivedRegion = parts.length > 1 ? parts.slice(1).join(" · ") : "";
    return { commentTime, region: explicitRegion || derivedRegion };
}

function parseCommentTimestamp(rawValue) {
    const text = normalize(rawValue);
    if (!text) return null;
    const now = new Date();
    const nowTs = now.getTime();
    const todayStartTs = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (/^(刚刚|刚才)$/.test(text)) return nowTs;
    let match = text.match(/^(\d+)\s*分钟[前内]?$/);
    if (match) return nowTs - Number(match[1]) * 60 * 1000;
    match = text.match(/^(\d+)\s*小时[前内]?$/);
    if (match) return nowTs - Number(match[1]) * 60 * 60 * 1000;
    match = text.match(/^(\d+)\s*天前$/);
    if (match) return nowTs - Number(match[1]) * 24 * 60 * 60 * 1000;
    match = text.match(/^(\d+)\s*周前$/);
    if (match) return nowTs - Number(match[1]) * 7 * 24 * 60 * 60 * 1000;
    match = text.match(/^(\d+)\s*个?月前$/);
    if (match) return nowTs - Number(match[1]) * 30 * 24 * 60 * 60 * 1000;
    match = text.match(/^(\d+)\s*年前$/);
    if (match) return nowTs - Number(match[1]) * 365 * 24 * 60 * 60 * 1000;
    if (text === "昨天") return todayStartTs - 24 * 60 * 60 * 1000;
    if (text === "前天") return todayStartTs - 2 * 24 * 60 * 60 * 1000;
    const normalizedText = text
        .replace(/[年./]/g, "-")
        .replace(/月/g, "-")
        .replace(/日/g, "")
        .replace(/\//g, "-")
        .replace(/\s+/g, " ")
        .trim();
    const parsed = Date.parse(normalizedText);
    return Number.isNaN(parsed) ? null : parsed;
}

function formatAbsoluteDate(rawValue) {
    const ts = parseCommentTimestamp(rawValue);
    if (!Number.isFinite(ts)) return normalize(rawValue) || "-";
    const date = new Date(ts);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function getCommentTimeFilterOptions() {
    return [
        { value: "all", label: "评论时间：无限制" },
        { value: "3", label: "评论时间：3天内" },
        { value: "7", label: "评论时间：7天内" },
        { value: "30", label: "评论时间：30天内" },
        { value: "90", label: "评论时间：90天内" },
        { value: "365", label: "评论时间：1年内" },
    ];
}

function renderCommentTimeFilter(selectId, currentValue = "all") {
    const select = document.getElementById(selectId);
    if (!select) return;
    const options = getCommentTimeFilterOptions();
    select.innerHTML = options.map(option => `<option value="${option.value}" ${String(currentValue) === String(option.value) ? "selected" : ""}>${option.label}</option>`).join("");
}

function matchesCommentTimeFilter(rawValue, daysValue = "all") {
    if (daysValue === "all") return true;
    const days = Number(daysValue || 0);
    if (!days) return true;
    const ts = parseCommentTimestamp(rawValue);
    if (!Number.isFinite(ts)) return false;
    return Date.now() - ts <= days * 24 * 60 * 60 * 1000;
}

function getCommentRegionLabel(row = {}) {
    return parseCommentMeta(row).region || normalize(row.region || row.location || row.ip_location || "");
}

function formatCommentDateLabel(row = {}) {
    const meta = parseCommentMeta(row);
    return formatAbsoluteDate(meta.commentTime || row.comment_time || "");
}

function getAvatarUrl(row = {}) {
    return normalize(row.avatar || row.avatar_url || row.avatarUrl || "");
}

function toCount(value) {
    const numeric = Number(value || 0);
    return Number.isFinite(numeric) ? numeric : 0;
}

function getTaskById(taskId) {
    return douyinTasks.find(item => Number(item.id || 0) === Number(taskId || 0)) || null;
}

function enrichInteractionUser(user = {}) {
    const task = getTaskById(user.task_id);
    return {
        ...user,
        task_title: user.task_title || task?.title || "",
        task_url: user.task_url || task?.url || "",
        task_author: user.task_author || task?.author || "",
        cover_image: user.cover_image || task?.cover_image || "",
        source_keyword: user.source_keyword || task?.source_keyword || "",
        avatar: getAvatarUrl(user),
        avatar_url: getAvatarUrl(user),
        comment_time_display: formatCommentDateLabel(user),
        region: getCommentRegionLabel(user),
    };
}

function getFlattenedInteractionUsers() {
    return (Array.isArray(douyinInteractionUsers) ? douyinInteractionUsers : []).map(enrichInteractionUser);
}

function getFilteredInteractionUsers(users = getFlattenedInteractionUsers()) {
    return users.filter(user => {
        const region = normalize(user.region || "");
        const commentTime = normalize(user.comment_time_display || user.comment_time || "");
        const followStatus = normalize(user.follow_comment_status || "pending").toLowerCase();
        const messageStatus = normalize(user.interaction_status || "pending").toLowerCase();
        if (interactionRegionFilter === "unknown" && region) return false;
        if (interactionRegionFilter !== "all" && interactionRegionFilter !== "unknown" && region !== interactionRegionFilter) return false;
        if (!matchesCommentTimeFilter(commentTime, interactionTimeFilterDays)) return false;
        if (interactionFollowStatusFilter !== "all" && followStatus !== interactionFollowStatusFilter) return false;
        if (interactionMessageStatusFilter !== "all" && messageStatus !== interactionMessageStatusFilter) return false;
        return true;
    });
}

function isInteractionUserSelectable(user = {}) {
    const status = normalize(user.interaction_status || "pending").toLowerCase();
    return status !== "sent" && status !== "processing";
}

function syncInteractionSelection(users = getFilteredInteractionUsers()) {
    const allKeys = new Set(users.filter(isInteractionUserSelectable).map(userChoiceKey).filter(Boolean));
    if (!interactionSelectionTouched && !interactionSelectionKeys.size) return;
    interactionSelectionKeys = new Set([...interactionSelectionKeys].filter(key => allKeys.has(key)));
}

function getSelectedInteractionUsers() {
    return getFlattenedInteractionUsers().filter(user => interactionSelectionKeys.has(userChoiceKey(user)));
}

function renderInteractionRegionFilter(users = getFlattenedInteractionUsers()) {
    const select = document.getElementById("interaction-region-filter");
    if (!select) return;
    const current = interactionRegionFilter || "all";
    const regions = [...new Set(users.map(user => normalize(user.region || "")).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b, "zh-CN"));
    select.innerHTML = [
        `<option value="all">全部地区</option>`,
        `<option value="unknown">未标注地区</option>`,
        ...regions.map(region => `<option value="${esc(region)}">${esc(region)}</option>`),
    ].join("");
    select.value = [...regions, "all", "unknown"].includes(current) ? current : "all";
}

function updateInteractionSelectionSummary(users = getFilteredInteractionUsers()) {
    const selected = users.filter(user => interactionSelectionKeys.has(userChoiceKey(user))).length;
    const accountIds = Array.isArray(douyinInteractionState.account_ids) ? douyinInteractionState.account_ids : [];
    const sourceCount = new Set(users.map(user => normalize(user.task_url || user.task_title || "")).filter(Boolean)).size;
    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };
    const summary = document.getElementById("douyin-interaction-selection-summary");
    if (summary) summary.innerHTML = `已勾选 <strong>${selected}</strong> / ${users.length} 人`;
    setText("interaction-total", users.length);
    setText("interaction-selected-total", selected);
    setText("interaction-completed-posts", sourceCount);
    setText("interaction-active-account", accountIds.length ? accountIds.join(" / ") : (activeDouyinAccountId || "-"));
}

function renderIntentAvatar(row = {}) {
    const avatarUrl = getAvatarUrl(row);
    const fallback = normalize(row.username || "").charAt(0) || "客";
    return avatarUrl
        ? `<span class="intent-avatar"><img src="${esc(avatarUrl)}" alt="${esc(row.username || "用户头像")}" loading="lazy" referrerpolicy="no-referrer"></span>`
        : `<span class="intent-avatar intent-avatar-fallback">${esc(fallback)}</span>`;
}

function renderIntentUserCell(user) {
    const author = normalize(user.task_author || "");
    const isMonitor = normalize(user.source || "") === "douyin_monitor" || Number(user.monitor_target_id || 0) > 0;
    const subtle = author ? `${isMonitor ? "来源同行" : "来源作者"}：${author}` : (normalize(user.reason || "") || "来自评论精准筛选");
    return `<div class="intent-user-cell">${renderIntentAvatar(user)}<div class="intent-user-meta"><div class="intent-user-name">${esc(user.username || "-")}</div><div class="intent-user-subtle">${esc(cut(subtle, 34))}</div></div></div>`;
}

function renderCustomerCommentCell(user = {}, limit = 96) {
    const text = cut(user.comment || user.content || "-", limit);
    const meta = [
        user.comment_time_display || user.comment_time || "",
        user.region || "未标注",
        toCount(user.like_count) > 0 ? `点赞 ${toCount(user.like_count)}` : "",
        toCount(user.reply_count) > 0 ? `回复 ${toCount(user.reply_count)}` : "",
    ].filter(Boolean).join(" · ");
    return `<div class="table-comment"><div class="table-comment-main" title="${esc(user.comment || user.content || "")}">${esc(text)}</div>${meta ? `<div class="table-comment-meta">${esc(meta)}</div>` : ""}</div>`;
}

function renderTaskPostHtml(row = {}) {
    const isMonitor = normalize(row.source || "") === "douyin_monitor" || Number(row.monitor_target_id || 0) > 0;
    const cover = normalize(row.cover_image || "");
    const url = esc(normalize(row.task_url || "#") || "#");
    const title = esc(normalize(row.task_title || "查看详情"));
    const author = normalize(row.task_author || "");
    const publishText = normalize(row.comment_time_display || row.comment_time || "");
    const coverHtml = cover
        ? `<a class="task-post-cover" href="${url}" target="_blank" rel="noopener noreferrer"><img src="${esc(cover)}" alt="${title}" loading="lazy" referrerpolicy="no-referrer"></a>`
        : `<a class="task-post-cover is-empty" href="${url}" target="_blank" rel="noopener noreferrer">无封面</a>`;
    return `<div class="task-post-cell">${coverHtml}<div class="task-post-meta"><a class="task-post-title" href="${url}" target="_blank" rel="noopener noreferrer" title="${title}">${title}</a>${author ? `<div class="task-post-subtle">${isMonitor ? "同行" : "作者"}：${esc(author)}</div>` : ""}${publishText ? `<div class="task-post-subtle">${esc(cut(publishText, 30))}</div>` : ""}</div></div>`;
}

function renderTableIndexCell(index, label = "") {
    return `<div class="table-index"><strong>${esc(index)}</strong>${label ? `<span>${esc(label)}</span>` : ""}</div>`;
}

function renderProfileAction(url, label = "前往主页") {
    return url ? `<a class="btn" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : `<span class="badge status-pending">无主页</span>`;
}

function followCommentStatusText(status) {
    return ({ pending: "待关注评论", queued: "排队中", processing: "执行中", completed: "已完成", failed: "失败", skipped: "已跳过" })[status] || "待关注评论";
}

function followCommentStatusClass(status) {
    return ({ pending: "pending", queued: "info", processing: "processing", completed: "completed", failed: "failed", skipped: "pending" })[status] || "pending";
}

function interactionStatusText(status) {
    return ({ pending: "待发送", queued: "排队中", processing: "发送中", sent: "已发送", failed: "失败", interrupted: "需确认" })[status] || "待发送";
}

function interactionStatusClass(status) {
    return ({ pending: "pending", queued: "info", processing: "processing", sent: "completed", failed: "failed", interrupted: "warning" })[status] || "pending";
}

function renderFollowCommentStatusCell(user) {
    const status = normalize(user.follow_comment_status || "pending").toLowerCase();
    const result = normalize(user.follow_comment_result || "");
    const error = normalize(user.follow_comment_error || "");
    const accountId = normalize(user.follow_comment_account_id || "");
    return `<div class="table-status-stack"><span class="badge status-${followCommentStatusClass(status)}">${esc(followCommentStatusText(status))}</span>${accountId ? `<div class="subtle">账号 ${esc(accountId)}</div>` : ""}${result ? `<div class="subtle">${esc(cut(result, 60))}</div>` : ""}${error ? `<div class="subtle" style="color:var(--danger)">${esc(cut(error, 60))}</div>` : ""}</div>`;
}

function renderInteractionStatusCell(user) {
    const status = normalize(user.interaction_status || "pending").toLowerCase();
    const error = normalize(user.interaction_error || "");
    const accountId = normalize(user.interaction_account_id || "");
    const key = userChoiceKey(user);
    const actions = status === "interrupted"
        ? `<div class="table-action-row" style="margin-top:4px;gap:4px"><button type="button" class="btn" onclick='resetInteractionUserStatusByKey(${JSON.stringify(key)},"pending")'>重置为待发送</button><button type="button" class="btn" onclick='resetInteractionUserStatusByKey(${JSON.stringify(key)},"sent")'>已确认发送</button></div>`
        : "";
    return `<div class="table-status-stack"><span class="badge status-${interactionStatusClass(status)}">${esc(interactionStatusText(status))}</span>${accountId ? `<div class="subtle">账号 ${esc(accountId)}</div>` : ""}${error ? `<div class="subtle" style="color:var(--danger)">${esc(cut(error, 60))}</div>` : ""}${actions}</div>`;
}

function renderInteractionPreview() {
    const tbody = document.getElementById("interaction-tbody");
    const info = document.getElementById("interaction-page-info");
    const pager = document.getElementById("interaction-pagination");
    if (!tbody || !pager) return;
    const allUsers = getFlattenedInteractionUsers();
    renderInteractionRegionFilter(allUsers);
    renderCommentTimeFilter("interaction-time-filter", interactionTimeFilterDays);
    const users = getFilteredInteractionUsers(allUsers);
    const hasFilter = interactionRegionFilter !== "all" || interactionTimeFilterDays !== "all" || interactionFollowStatusFilter !== "all" || interactionMessageStatusFilter !== "all";
    syncInteractionSelection(users);
    updateInteractionSelectionSummary(users);
    renderInteractionRuntime();
    if (!allUsers.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="empty">暂无精准客户，请先执行评论采集并筛选精准客户。</td></tr>`;
        if (info) info.textContent = "暂无互动数据";
        pager.innerHTML = "";
        refreshActionState();
        return;
    }
    if (!users.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="empty">${hasFilter ? "当前筛选条件下暂无精准客户。" : "当前地区筛选下暂无精准客户。"}</td></tr>`;
        if (info) info.textContent = hasFilter ? "当前筛选下暂无数据" : "当前地区筛选下暂无数据";
        pager.innerHTML = "";
        refreshActionState();
        return;
    }
    const size = parseInt(document.getElementById("interaction-page-size")?.value, 10) || 10;
    const total = Math.max(1, Math.ceil(users.length / size));
    interactionCurrentPage = Math.min(Math.max(interactionCurrentPage, 1), total);
    const start = (interactionCurrentPage - 1) * size;
    const page = users.slice(start, start + size);
    if (info) info.textContent = `第 ${interactionCurrentPage} / ${total} 页，共 ${users.length} 人`;
    tbody.innerHTML = page.map((user, index) => {
        const key = userChoiceKey(user);
        const selectable = isInteractionUserSelectable(user);
        const checked = selectable && interactionSelectionKeys.has(key);
        const actionLabel = !selectable ? "已发送" : checked ? "取消勾选" : "加入执行";
        return `<tr><td class="cell-tight"><input type="checkbox" ${checked ? "checked" : ""} ${!selectable ? "disabled" : ""} onchange='toggleInteractionUserSelectionByKey(${JSON.stringify(key)}, this.checked)'></td><td>${renderTableIndexCell(start + index + 1, "DM")}</td><td>${renderIntentUserCell(user)}</td><td>${renderCustomerCommentCell(user, 80)}</td><td>${renderTaskPostHtml(user)}</td><td class="nowrap">${renderFollowCommentStatusCell(user)}</td><td class="nowrap">${renderInteractionStatusCell(user)}</td><td><div class="table-action-row">${renderProfileAction(user.profile_url)}<button type="button" class="btn" ${!selectable ? "disabled" : ""} onclick='toggleInteractionUserSelectionByKey(${JSON.stringify(key)}, ${checked ? "false" : "true"})'>${actionLabel}</button></div></td></tr>`;
    }).join("");
    pager.innerHTML = total > 1
        ? `<div class="subtle">互动列表分页：每页 ${size} 条</div><div class="pager"><span>第 ${interactionCurrentPage} / ${total} 页</span><button type="button" class="pager-btn" onclick="goToInteractionPage(1)" ${interactionCurrentPage === 1 ? "disabled" : ""}>首页</button><button type="button" class="pager-btn" onclick="goToInteractionPage(${interactionCurrentPage - 1})" ${interactionCurrentPage === 1 ? "disabled" : ""}>上一页</button><button type="button" class="pager-btn" onclick="goToInteractionPage(${interactionCurrentPage + 1})" ${interactionCurrentPage === total ? "disabled" : ""}>下一页</button><button type="button" class="pager-btn" onclick="goToInteractionPage(${total})" ${interactionCurrentPage === total ? "disabled" : ""}>末页</button></div>`
        : `<div class="subtle">互动列表分页：每页 ${size} 条</div>`;
    refreshActionState();
}

function getCurrentPageInteractionUsers() {
    const users = getFilteredInteractionUsers();
    const size = parseInt(document.getElementById("interaction-page-size")?.value, 10) || 10;
    const total = Math.max(1, Math.ceil(users.length / size));
    interactionCurrentPage = Math.min(Math.max(interactionCurrentPage, 1), total);
    const start = (interactionCurrentPage - 1) * size;
    return users.slice(start, start + size).filter(isInteractionUserSelectable);
}

function goToInteractionPage(page) {
    interactionCurrentPage = page;
    renderInteractionPreview();
}

function changeInteractionPageSize() {
    interactionCurrentPage = 1;
    renderInteractionPreview();
}

function changeInteractionRegionFilter() {
    interactionRegionFilter = document.getElementById("interaction-region-filter")?.value || "all";
    interactionCurrentPage = 1;
    renderInteractionPreview();
}

function changeInteractionTimeFilter() {
    interactionTimeFilterDays = document.getElementById("interaction-time-filter")?.value || "all";
    interactionCurrentPage = 1;
    renderInteractionPreview();
}

function changeInteractionFollowStatusFilter() {
    interactionFollowStatusFilter = document.getElementById("interaction-follow-status-filter")?.value || "all";
    interactionCurrentPage = 1;
    renderInteractionPreview();
}

function changeInteractionMessageStatusFilter() {
    interactionMessageStatusFilter = document.getElementById("interaction-message-status-filter")?.value || "all";
    interactionCurrentPage = 1;
    renderInteractionPreview();
}

function toggleAllInteractionUsers(checked) {
    const users = getFilteredInteractionUsers().filter(isInteractionUserSelectable);
    interactionSelectionTouched = true;
    interactionSelectionKeys = checked ? new Set(users.map(userChoiceKey)) : new Set();
    renderInteractionPreview();
}

function toggleCurrentPageInteractionUsers(checked) {
    const nextSelection = new Set(interactionSelectionKeys);
    getCurrentPageInteractionUsers().forEach(user => {
        const key = userChoiceKey(user);
        if (!key) return;
        checked ? nextSelection.add(key) : nextSelection.delete(key);
    });
    interactionSelectionTouched = true;
    interactionSelectionKeys = nextSelection;
    renderInteractionPreview();
}

function toggleInteractionUserSelectionByKey(key, checked) {
    if (!key) return;
    interactionSelectionTouched = true;
    checked ? interactionSelectionKeys.add(key) : interactionSelectionKeys.delete(key);
    renderInteractionPreview();
}

function normalizeDouyinInteractionPresetState(state = {}) {
    const presets = Array.from({ length: douyinInteractionPresetCount }, (_, index) => String(Array.isArray(state.presets) ? state.presets[index] ?? "" : index === 0 ? defaultDouyinInteractionMessage : ""));
    let activeIndex = Number(state.activeIndex || 0);
    if (!Number.isInteger(activeIndex) || activeIndex < 0 || activeIndex >= douyinInteractionPresetCount) activeIndex = 0;
    return { activeIndex, presets };
}

function persistDouyinInteractionPresets() {
    try {
        localStorage.setItem(douyinInteractionPresetStorageKey, JSON.stringify(douyinInteractionPresetState));
    } catch (error) {}
}

function loadDouyinInteractionPresets() {
    let parsed = {};
    try {
        parsed = JSON.parse(localStorage.getItem(douyinInteractionPresetStorageKey) || "{}") || {};
    } catch (error) {
        parsed = {};
    }
    douyinInteractionPresetState = normalizeDouyinInteractionPresetState(parsed);
    applyDouyinInteractionPresetToForm();
}

function updateDouyinInteractionPresetDraftFromForm() {
    const messageEl = document.getElementById("douyin-interaction-message");
    if (!messageEl) return;
    douyinInteractionPresetState = normalizeDouyinInteractionPresetState(douyinInteractionPresetState);
    douyinInteractionPresetState.presets[douyinInteractionPresetState.activeIndex] = String(messageEl.value || "").trim();
}

function getDouyinInteractionFixedMessages() {
    updateDouyinInteractionPresetDraftFromForm();
    douyinInteractionPresetState = normalizeDouyinInteractionPresetState(douyinInteractionPresetState);
    return douyinInteractionPresetState.presets.map(item => String(item || "").trim()).filter(Boolean);
}

function renderDouyinInteractionPresetTabs() {
    const tabs = document.getElementById("douyin-interaction-preset-tabs");
    if (!tabs) return;
    tabs.innerHTML = Array.from({ length: douyinInteractionPresetCount }, (_, index) => `<button type="button" class="btn dm-preset-tab ${index === douyinInteractionPresetState.activeIndex ? "active" : ""}" data-preset-index="${index}" onclick="selectDouyinInteractionPreset(${index})">话术 ${index + 1}</button>`).join("");
}

function applyDouyinInteractionPresetToForm() {
    renderDouyinInteractionPresetTabs();
    const messageEl = document.getElementById("douyin-interaction-message");
    if (messageEl) messageEl.value = douyinInteractionPresetState.presets[douyinInteractionPresetState.activeIndex] || "";
    document.querySelectorAll("#douyin-interaction-preset-tabs .dm-preset-tab").forEach(button => {
        const index = Number(button.dataset.presetIndex || 0);
        button.classList.toggle("active", index === douyinInteractionPresetState.activeIndex);
    });
}

function saveDouyinInteractionPreset(showLog = false) {
    const messageEl = document.getElementById("douyin-interaction-message");
    if (!messageEl) return;
    douyinInteractionPresetState = normalizeDouyinInteractionPresetState(douyinInteractionPresetState);
    douyinInteractionPresetState.presets[douyinInteractionPresetState.activeIndex] = String(messageEl.value || "").trim();
    persistDouyinInteractionPresets();
    if (showLog) addLog(`已保存私信话术 ${douyinInteractionPresetState.activeIndex + 1}。`, "success");
}

function handleDouyinInteractionPresetInput() {
    updateDouyinInteractionPresetDraftFromForm();
}

function selectDouyinInteractionPreset(index) {
    updateDouyinInteractionPresetDraftFromForm();
    douyinInteractionPresetState = normalizeDouyinInteractionPresetState({ ...douyinInteractionPresetState, activeIndex: Number(index) || 0 });
    applyDouyinInteractionPresetToForm();
    refreshActionState();
}

function getDouyinInteractionMode() {
    const value = normalize(document.getElementById("douyin-interaction-mode")?.value || "fixed").toLowerCase();
    return ["fixed", "ai", "rewrite"].includes(value) ? value : "fixed";
}

function getDouyinInteractionModeLabel(mode) {
    return ({ fixed: "固定文案", ai: "AI 生成", rewrite: "AI 改写" })[mode] || "固定文案";
}

function toggleDouyinInteractionOptions() {
    const mode = getDouyinInteractionMode();
    const hint = document.getElementById("douyin-interaction-mode-hint");
    document.getElementById("douyin-interaction-fixed-wrap")?.classList.toggle("active", mode === "fixed");
    document.getElementById("douyin-interaction-ai-wrap")?.classList.toggle("active", mode === "ai");
    document.getElementById("douyin-interaction-rewrite-wrap")?.classList.toggle("active", mode === "rewrite");
    if (hint) {
        hint.textContent = mode === "ai"
            ? "系统会结合对方评论、来源视频和作者场景生成一条自然私信，不会按固定文案去改写。"
            : mode === "rewrite"
                ? "系统会围绕你给的基准私信做同方向改编，保持原有意图和语气，但每次表达会换一种说法。"
                : "直接发送你填写的统一私信。每换一行，系统就顺序发送一条单独消息。";
    }
    refreshActionState();
}

function getDouyinInteractionIntervalUnit() {
    const value = normalize(document.getElementById("douyin-interaction-interval-unit")?.value || "minutes").toLowerCase();
    return value === "seconds" ? "seconds" : "minutes";
}

function formatDouyinInteractionIntervalInputValue(value, unit) {
    const numeric = Number(value || 0);
    if (!Number.isFinite(numeric)) return "0";
    return unit === "minutes" ? String(Math.round(numeric * 10) / 10) : String(Math.round(numeric));
}

function changeDouyinInteractionIntervalUnit() {
    const unit = getDouyinInteractionIntervalUnit();
    const select = document.getElementById("douyin-interaction-interval-unit");
    const minInput = document.getElementById("douyin-interaction-interval-min");
    const maxInput = document.getElementById("douyin-interaction-interval-max");
    if (!select || !minInput || !maxInput) return;
    const previousUnit = select.dataset.lastUnit || unit;
    let minValue = parseFloat(minInput.value) || 0;
    let maxValue = parseFloat(maxInput.value) || 0;
    if (previousUnit !== unit) {
        if (unit === "minutes") {
            minValue /= 60;
            maxValue /= 60;
        } else {
            minValue *= 60;
            maxValue *= 60;
        }
    }
    if (unit === "minutes") {
        minInput.max = "1440";
        maxInput.max = "1440";
        minInput.step = "0.1";
        maxInput.step = "0.1";
    } else {
        minInput.max = "86400";
        maxInput.max = "86400";
        minInput.step = "1";
        maxInput.step = "1";
    }
    minInput.value = formatDouyinInteractionIntervalInputValue(minValue, unit);
    maxInput.value = formatDouyinInteractionIntervalInputValue(maxValue, unit);
    select.dataset.lastUnit = unit;
}

function formatDouyinInteractionIntervalFromSeconds(minSeconds, maxSeconds) {
    const min = Number(minSeconds || 0);
    const max = Number(maxSeconds || min || 0);
    if (!min && !max) return "";
    if (min >= 60 || max >= 60) {
        const minMinutes = Math.round((min / 60) * 10) / 10;
        const maxMinutes = Math.round((max / 60) * 10) / 10;
        return minMinutes === maxMinutes ? `${minMinutes} 分钟` : `${minMinutes}-${maxMinutes} 分钟随机`;
    }
    return min === max ? `${min} 秒` : `${min}-${max} 秒随机`;
}

function renderInteractionRuntime() {
    const box = document.getElementById("douyin-interaction-status");
    const detailBox = document.getElementById("douyin-interaction-runtime-detail");
    const metricsBox = document.getElementById("douyin-interaction-runtime-metrics");
    const badge = document.getElementById("douyin-interaction-runtime-badge");
    const card = document.getElementById("douyin-interaction-runtime-card");
    const spinner = document.getElementById("douyin-interaction-runtime-spinner");
    if (!box || !detailBox || !metricsBox || !badge || !card || !spinner) return;
    const state = douyinInteractionState || {};
    const total = Number(state.total || 0);
    const processed = Number(state.processed || 0);
    const success = Number(state.success || 0);
    const failed = Number(state.failed || 0);
    const intervalSeconds = Number(state.interval_seconds || 0);
    const intervalSecondsMin = Number(state.interval_seconds_min || 0);
    const intervalSecondsMax = Number(state.interval_seconds_max || 0);
    const currentUser = normalize(state.current_user || "");
    const statusMessage = normalize(state.message || "");
    const stopping = statusMessage.includes("停止中");
    const currentMessage = normalize(state.current_message_text || "");
    const lastMessage = normalize(state.last_message_text || "");
    const modeLabel = getDouyinInteractionModeLabel(normalize(state.message_mode || "fixed").toLowerCase());
    const summary = normalize(state.message_summary || "");
    const accountIds = Array.isArray(state.account_ids) ? state.account_ids : [];
    const workers = Array.isArray(state.workers) ? state.workers : [];
    const finishedAt = normalize(state.finished_at || "");
    const lastError = normalize(state.last_error || "");
    const intervalLabel = formatDouyinInteractionIntervalFromSeconds(intervalSecondsMin || intervalSeconds, intervalSecondsMax || intervalSeconds);
    const workerItems = workers.slice(0, 3).map(worker => {
        const accountId = worker.account_id || "-";
        const status = normalize(worker.status || "");
        const user = normalize(worker.current_user || "");
        if (status === "processing" && user) return `账号 ${accountId} 正在发送 ${user}`;
        if (status === "cooldown") return `账号 ${accountId} 冷却 ${worker.cooldown_remaining || 0} 秒`;
        if (status === "completed") return `账号 ${accountId} 已完成`;
        if (status === "failed") return `账号 ${accountId} 异常`;
        return `账号 ${accountId} 待命`;
    });
    const metricHtml = (label, value, extraClass = "") => `<div class="interaction-runtime-metric${extraClass ? ` ${extraClass}` : ""}"><span class="interaction-runtime-metric-label">${esc(label)}</span><strong>${esc(value)}</strong></div>`;
    const summaryParts = [summary, workerItems.length ? workerItems.join("；") : "", lastError ? `最近异常：${lastError}` : ""].filter(Boolean);
    const renderState = ({ tone, badgeText, message, detail, metrics, showSpinner }) => {
        card.classList.remove("is-running", "is-finished");
        badge.classList.remove("is-running", "is-finished");
        if (tone === "running") {
            card.classList.add("is-running");
            badge.classList.add("is-running");
        } else if (tone === "finished") {
            card.classList.add("is-finished");
            badge.classList.add("is-finished");
        }
        badge.textContent = badgeText;
        box.textContent = message;
        detailBox.innerHTML = `${esc(detail)}${summaryParts.length ? `<div class="interaction-runtime-workers">${esc(summaryParts.join("；"))}</div>` : ""}`;
        metricsBox.innerHTML = metrics.join("");
        spinner.classList.toggle("is-active", !!showSpinner);
    };
    if (douyinInteractionRunning) {
        renderState({
            tone: "running",
            badgeText: stopping ? "停止中" : "执行中",
            message: `私信任务正在${stopping ? "停止" : "执行"}，已完成 ${processed}/${total}。`,
            detail: [`成功 ${success}，失败 ${failed}。`, intervalLabel ? `每账号间隔 ${intervalLabel}。` : "", accountIds.length ? `当前并发账号：${accountIds.join("、")}。` : "", currentUser ? `当前目标：${currentUser}。` : "", currentMessage && !stopping ? `当前私信：「${cut(currentMessage, 44)}」。` : ""].filter(Boolean).join(" "),
            metrics: [metricHtml("处理进度", `${processed}/${total}`, "is-accent"), metricHtml("发送成功", String(success)), metricHtml("发送失败", String(failed), failed > 0 ? "is-warn" : ""), metricHtml("发送模式", modeLabel), metricHtml("执行账号", accountIds.length ? accountIds.join("、") : "-"), metricHtml("当前目标", currentUser || "排队中")],
            showSpinner: true,
        });
        return;
    }
    if (total > 0 || finishedAt) {
        renderState({
            tone: "finished",
            badgeText: "已完成",
            message: `最近一次私信任务已结束，完成 ${processed}/${total}。`,
            detail: [`成功 ${success}，失败 ${failed}。`, intervalLabel ? `执行间隔 ${intervalLabel}。` : "", accountIds.length ? `使用账号：${accountIds.join("、")}。` : "", lastMessage ? `最近发送：「${cut(lastMessage, 44)}」。` : "", finishedAt ? `结束于 ${finishedAt}。` : ""].filter(Boolean).join(" "),
            metrics: [metricHtml("处理进度", `${processed}/${total}`, "is-accent"), metricHtml("发送成功", String(success)), metricHtml("发送失败", String(failed), failed > 0 ? "is-warn" : ""), metricHtml("发送模式", modeLabel), metricHtml("使用账号", accountIds.length ? accountIds.join("、") : "-"), metricHtml("结束时间", finishedAt || "刚刚完成")],
            showSpinner: false,
        });
        return;
    }
    renderState({
        tone: "idle",
        badgeText: "待命",
        message: "尚未开始私信任务。",
        detail: "开始后这里会持续高亮显示执行进度、账号状态和最近一条私信内容。",
        metrics: [metricHtml("处理进度", "0/0"), metricHtml("发送成功", "0"), metricHtml("发送失败", "0"), metricHtml("发送模式", modeLabel), metricHtml("执行账号", "-"), metricHtml("当前目标", "-")],
        showSpinner: false,
    });
}

async function loadDouyinConfig(silent = true) {
    try {
        const data = await fetchJson("/api/douyin/config");
        douyinAccounts = Array.isArray(data.douyin_accounts) ? data.douyin_accounts : [];
        activeDouyinAccountId = Number(data.douyin_default_account_id || 0) || null;
        if (!silent) addLog("抖音配置已加载。");
    } catch (error) {
        if (!silent) addLog(`加载抖音配置失败：${error.message}`, "warning");
    }
}

async function updateDouyinTasks(silent = true) {
    try {
        const data = await fetchJson("/api/douyin/tasks-lite");
        douyinTasks = Array.isArray(data.tasks) ? data.tasks : [];
        if (!silent) addLog("抖音任务轻量信息已刷新。");
    } catch (error) {
        if (!silent) addLog(`刷新抖音任务失败：${error.message}`, "error");
    }
}

async function updateDouyinInteractionStatus(silent = true, options = {}) {
    const includeUsers = options.includeUsers !== false;
    const url = includeUsers ? "/api/douyin/interaction/status?lite=1" : "/api/douyin/interaction/status?lite=1&include_users=0";
    try {
        const data = await fetchJson(url);
        if (data.code === 200) {
            douyinInteractionState = data.state || {};
            douyinInteractionRunning = !!data.running;
            if (Array.isArray(data.users)) douyinInteractionUsers = data.users;
            renderInteractionPreview();
            if (!silent && normalize(douyinInteractionState.message || "")) addLog(`私信状态：${douyinInteractionState.message}`);
        } else if (!silent) {
            addLog(data.msg || data.detail || "刷新私信状态失败。", "error");
        }
    } catch (error) {
        if (!silent) addLog(`刷新私信状态失败：${error.message}`, "error");
    }
}

async function refreshInteractionPage(showLog = false) {
    await Promise.all([
        loadDouyinConfig(true),
        updateDouyinTasks(true),
        updateDouyinInteractionStatus(true, { includeUsers: true }),
    ]);
    if (showLog) addLog(`私信客户列表已刷新，共 ${getFlattenedInteractionUsers().length} 人。`, "success");
}

function refreshActionState() {
    const selectedInteraction = getSelectedInteractionUsers().length;
    const busy = !!douyinInteractionRunning;
    const startBtn = document.getElementById("douyin-interaction-start-btn");
    const stopBtn = document.getElementById("douyin-interaction-stop-btn");
    if (startBtn) startBtn.disabled = busy || selectedInteraction === 0;
    if (stopBtn) stopBtn.disabled = !busy;
    updateInteractionSelectionSummary(getFilteredInteractionUsers());
}

async function startDouyinInteraction() {
    const users = getSelectedInteractionUsers();
    const mode = getDouyinInteractionMode();
    const messages = getDouyinInteractionFixedMessages();
    const message = messages[0] || "";
    const messagePrompt = String(document.getElementById("douyin-interaction-prompt")?.value || "").trim();
    const messageSeedText = String(document.getElementById("douyin-interaction-seed-text")?.value || "").trim();
    const intervalUnit = getDouyinInteractionIntervalUnit();
    const intervalInputMin = Math.max(0, parseFloat(document.getElementById("douyin-interaction-interval-min")?.value) || 0);
    const intervalInputMax = Math.max(0, parseFloat(document.getElementById("douyin-interaction-interval-max")?.value) || 0);
    if (!users.length) return void addLog("请先勾选至少一个精准客户。", "warning");
    if (mode === "fixed" && !messages.length) return void addLog("请至少填写 1 条固定私信话术后再开始执行。", "warning");
    if (mode === "rewrite" && !messageSeedText) return void addLog("请输入私信基准文案后再开始执行 AI 改编。", "warning");
    const button = document.getElementById("douyin-interaction-start-btn");
    setBtn(button, true, "开始私信", "启动中...");
    try {
        const minValue = Math.min(intervalInputMin, intervalInputMax);
        const maxValue = Math.max(intervalInputMin, intervalInputMax);
        const payload = { message_mode: mode, message, messages, message_prompt: messagePrompt, message_seed_text: messageSeedText, users };
        let intervalLabel = "";
        if (intervalUnit === "minutes") {
            const minMinutes = Math.max(0, Math.min(minValue, 1440));
            const maxMinutes = Math.max(minMinutes, Math.min(maxValue, 1440));
            payload.interval_minutes_min = minMinutes;
            payload.interval_minutes_max = maxMinutes;
            intervalLabel = minMinutes === maxMinutes ? `${formatDouyinInteractionIntervalInputValue(minMinutes, "minutes")} 分钟` : `${formatDouyinInteractionIntervalInputValue(minMinutes, "minutes")}-${formatDouyinInteractionIntervalInputValue(maxMinutes, "minutes")} 分钟随机`;
        } else {
            const minSeconds = Math.max(0, Math.min(minValue, 86400));
            const maxSeconds = Math.max(minSeconds, Math.min(maxValue, 86400));
            payload.interval_seconds_min = minSeconds;
            payload.interval_seconds_max = maxSeconds;
            intervalLabel = minSeconds === maxSeconds ? `${formatDouyinInteractionIntervalInputValue(minSeconds, "seconds")} 秒` : `${formatDouyinInteractionIntervalInputValue(minSeconds, "seconds")}-${formatDouyinInteractionIntervalInputValue(maxSeconds, "seconds")} 秒随机`;
        }
        const data = await fetchJson("/api/douyin/interaction/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (data.code === 200) {
            douyinInteractionRunning = true;
            const messageTip = mode === "fixed" && messages.length > 1 ? `，${messages.length} 条话术轮换` : "";
            addLog(data.msg ? `${data.msg}，模式 ${getDouyinInteractionModeLabel(mode)}${messageTip}。` : `已启动私信任务，共 ${data.total || users.length} 人，每账号间隔 ${intervalLabel}，模式 ${getDouyinInteractionModeLabel(mode)}${messageTip}。`, "success");
            await updateDouyinInteractionStatus(true, { includeUsers: true });
            scheduleInteractionPolling(true);
        } else {
            addLog(data.msg || data.detail || "启动私信任务失败。", data.type === "no_online_account" ? "warning" : "error");
        }
    } catch (error) {
        addLog(`启动私信任务失败：${error.message}`, "error");
    } finally {
        setBtn(button, false, "开始私信", "启动中...");
        refreshActionState();
    }
}

async function stopDouyinInteraction() {
    try {
        const data = await fetchJson("/api/douyin/interaction/stop", { method: "POST" });
        if (data.code === 200) {
            douyinInteractionState = { ...douyinInteractionState, message: "私信任务停止中" };
            addLog(data.msg || "已请求停止私信任务。", "warning");
            await updateDouyinInteractionStatus(true, { includeUsers: false });
        } else {
            addLog(data.msg || data.detail || "停止私信任务失败。", "error");
        }
    } catch (error) {
        addLog(`停止私信任务失败：${error.message}`, "error");
    } finally {
        refreshActionState();
    }
}

async function resetInteractionUserStatusByKey(key, status) {
    const target = getFlattenedInteractionUsers().find(user => userChoiceKey(user) === key);
    if (!target) return void addLog("没找到对应客户，可能列表已刷新。", "warning");
    try {
        const data = await fetchJson("/api/douyin/interaction/reset", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status, users: [target] }),
        });
        if (data.code === 200) {
            addLog(data.msg || "私信状态已重置。", status === "sent" ? "success" : "warning");
            await updateDouyinInteractionStatus(true, { includeUsers: true });
        } else {
            addLog(data.msg || data.detail || "重置私信状态失败。", "error");
        }
    } catch (error) {
        addLog(`重置私信状态失败：${error.message}`, "error");
    } finally {
        refreshActionState();
    }
}

function getInteractionPollInterval() {
    if (document.hidden) return douyinInteractionRunning ? 30000 : 120000;
    return douyinInteractionRunning ? 5000 : 45000;
}

async function pollInteractionPageOnce() {
    if (interactionPollInFlight) return;
    interactionPollInFlight = true;
    try {
        await updateDouyinInteractionStatus(true, { includeUsers: false });
    } finally {
        interactionPollInFlight = false;
    }
}

function scheduleInteractionPolling(immediate = false) {
    if (interactionPollTimer) {
        window.clearTimeout(interactionPollTimer);
        interactionPollTimer = 0;
    }
    const delay = immediate ? 0 : getInteractionPollInterval();
    interactionPollTimer = window.setTimeout(async () => {
        await pollInteractionPageOnce();
        scheduleInteractionPolling(false);
    }, delay);
}

document.addEventListener("visibilitychange", () => {
    scheduleInteractionPolling(true);
});

document.addEventListener("DOMContentLoaded", async () => {
    loadDouyinInteractionPresets();
    renderCommentTimeFilter("interaction-time-filter", interactionTimeFilterDays);
    toggleDouyinInteractionOptions();
    changeDouyinInteractionIntervalUnit();
    renderInteractionRuntime();
    refreshActionState();
    await refreshInteractionPage(false);
    addLog(`私信互动独立页面已加载，共 ${getFlattenedInteractionUsers().length} 位客户。`, "success");
    scheduleInteractionPolling(false);
});
