// ── Tab switching ───────────────────────────────────────────────────
/** 为 false 时隐藏「官方在线」Tab，不加载 MCP 官方注册表 */
var STORE_OFFICIAL_TAB_ENABLED = false;
var _currentStoreTab = 'popular';

function bindSkillStoreTabs() {
  document.querySelectorAll('.store-tab').forEach(function(tab) {
    if (tab._skillStoreTabBound) return;
    tab._skillStoreTabBound = true;
    tab.addEventListener('click', function() {
      var target = tab.getAttribute('data-store-tab');
      if (target === 'official' && !STORE_OFFICIAL_TAB_ENABLED) return;
      if (!target || target === _currentStoreTab) return;
      _currentStoreTab = target;
      document.querySelectorAll('.store-tab').forEach(function(t) { t.classList.remove('active'); });
      tab.classList.add('active');
      var popular = document.getElementById('storeTabPopular');
      var official = document.getElementById('storeTabOfficial');
      if (popular) popular.style.display = (target === 'popular') ? '' : 'none';
      if (official) official.style.display = (target === 'official') ? '' : 'none';
      if (target === 'official' && !_officialLoaded) {
        browseOfficialPage(1);
      }
    });
  });
}

// ── 热门 Tab: local skills ──────────────────────────────────────────

var _xskillStatus = { has_token: false, token: '', url: '' };
var _comflyStatus = {
  effective_ready: false,
  has_user_key: false,
  masked_user_key: '',
  user_api_base: '',
  default_api_base_hint: 'https://ai.comfly.org',
};
var _youtubePublishStatus = { has_ready: false, accounts_count: 0 };
var _douyinWorkbenchState = {
  accounts: [],
  selectedAccountId: null,
  searchResults: [],
  leads: [],
  selectedLeadIds: {},
  config: {
    ai_filter_enabled: true,
    comment_direction: '',
    comment_filter_strategy: 'prompt',
    comment_max_comments: 120
  },
  pendingVideoIndex: null
};
var _skillStoreFetchCache = {};
var _SKILL_STORE_FETCH_TTL_MS = 60000;
var _SKILL_STORE_FETCH_TIMEOUT_MS = 8000;
var _SKILL_STORE_STATUS_TIMEOUT_MS = 3500;

function _skillStoreBrandSafeText(value) {
  var text = String(value == null ? '' : value);
  if (!text) return '';
  return text
    .replace(/速推视频制作/g, '必火视频制作')
    .replace(/速推\s*MCP/gi, 'AI 模型能力')
    .replace(/Sutui/gi, 'AI')
    .replace(/xSkill/gi, 'AI')
    .replace(/51aigc\.cc/gi, '必火平台')
    .replace(/速推/g, 'AI')
    .replace(/Comfly/gi, '视频引擎')
    .replace(/Seedance\s*2\.0/gi, '智能视频模型')
    .replace(/Seedance/gi, '智能视频模型')
    .replace(/CapCut/gi, '视频模板')
    .replace(/剪映/g, '视频模板')
    .replace(/山涧/g, '智能剪辑');
}

function _skillStoreTagHtml(tags) {
  return (tags || []).map(function(t) {
    return '<span class="tag">' + escapeHtml(_skillStoreBrandSafeText(t)) + '</span>';
  }).join('');
}

/** OpenClaw 微信插件本机扫码授权（/api/openclaw/weixin-login/*） */
var _openclawWeixinLast = { last_ok: false, at: null, detail: '' };
var _openclawWeixinPollTimer = null;

function _switchToHiddenView(view) {
  if (!view) return;
  if (typeof window.isLobsterViewAllowed === 'function' && !window.isLobsterViewAllowed(view)) {
    var fallback = (typeof window.getFirstAllowedLobsterView === 'function') ? window.getFirstAllowedLobsterView() : 'chat';
    if (fallback && fallback !== view && typeof window.showAppView === 'function') {
      window.showAppView(fallback).catch(function() {});
    }
    return;
  }
  location.hash = view;
  if (typeof window._rememberLobsterView === 'function') {
    window._rememberLobsterView(view, { skipHash: true });
  } else {
    try { localStorage.setItem('lobster_online_last_view', String(view || '').replace(/^#/, '').split(':')[0]); } catch (e0) {}
  }
  if (typeof currentView !== 'undefined' && currentView === 'chat' && typeof saveCurrentSessionToStore === 'function') {
    saveCurrentSessionToStore();
  }
  document.querySelectorAll('.nav-left-item').forEach(function(b) { b.classList.remove('active'); });
  document.querySelectorAll('.content-block').forEach(function(p) { p.classList.remove('visible'); });
  var contentEl = document.getElementById('content-' + view);
  if (contentEl) contentEl.classList.add('visible');
  if (typeof currentView !== 'undefined') currentView = view;
}

function _ensureDouyinWorkbenchHost() {
  var host = document.getElementById('content-douyin-workbench');
  if (host && host.dataset.douyinLayoutVersion === 'tabs-20260528-message-clean') return host;
  var main = document.querySelector('.dashboard-main');
  if (!main) return null;
  if (!host) {
    host = document.createElement('div');
    host.id = 'content-douyin-workbench';
    main.appendChild(host);
  }
  host.className = 'content-block';
  host.dataset.douyinLayoutVersion = 'tabs-20260528-message-clean';
  delete host.dataset.douyinTabsBound;
  host.innerHTML = `
    <div class="tvc-studio">
      <div class="tvc-studio-hero">
        <div>
          <h3>抖音获客工作台</h3>
          <p>左侧管理抖音账号，右侧在搜索采集和私信发送两个工位之间切换。</p>
          <div class="tvc-hero-meta">参考独立抖音工作台的结构：账号常驻左侧，采集和触达在右侧分屏处理。</div>
        </div>
        <div class="douyin-toolbar">
          <button type="button" id="douyinWorkbenchBackBtn" class="btn btn-ghost btn-sm">返回首页</button>
          <button type="button" id="douyinReloadAccountsBtn" class="btn btn-ghost btn-sm">刷新账号</button>
          <button type="button" id="douyinOpenLoginBtn" class="btn btn-primary btn-sm">打开登录</button>
        </div>
      </div>
      <div class="douyin-workflow-strip">
        <div class="douyin-step-pill"><span class="douyin-step-index">1</span><span class="douyin-step-copy"><strong>登录账号</strong><span>左侧固定账号和登录状态</span></span></div>
        <div class="douyin-step-pill"><span class="douyin-step-index">2</span><span class="douyin-step-copy"><strong>搜索采集</strong><span>右侧页签采集视频和客户</span></span></div>
        <div class="douyin-step-pill"><span class="douyin-step-index">3</span><span class="douyin-step-copy"><strong>私信发送</strong><span>上面配置消息，下面选客户</span></span></div>
      </div>
      <div class="douyin-shell">
        <aside class="douyin-sidebar">
          <div class="tvc-panel">
            <h4 class="tvc-panel-title">抖音账号</h4>
            <p class="tvc-panel-hint">这里直接添加和管理抖音获客账号，登录会打开抖音前台页面，不走创作者中心。</p>
            <div class="tvc-field">
              <label for="douyinNewAccountNicknameInput">添加抖音账号</label>
              <div class="douyin-inline-grid">
                <input id="douyinNewAccountNicknameInput" type="text" placeholder="账号备注，例如：主号、探店号">
                <button type="button" id="douyinCreateAccountBtn" class="btn btn-primary btn-sm">添加账号</button>
              </div>
            </div>
            <div class="tvc-field">
              <label for="douyinAccountSelect">选择账号</label>
              <select id="douyinAccountSelect"></select>
            </div>
            <div class="douyin-toolbar">
              <button type="button" id="douyinOpenLoginInlineBtn" class="btn btn-primary btn-sm">打开登录</button>
              <button type="button" id="douyinCheckStatusBtn" class="btn btn-ghost btn-sm">检查登录</button>
            </div>
            <p class="tvc-panel-hint" style="margin:0.45rem 0 0;">检查登录会打开抖音前台精选页，并基于页面登录状态判断。</p>
            <div id="douyinAccountList" class="douyin-account-list"></div>
          </div>
          <div class="tvc-panel tvc-panel-compact">
            <h4 class="tvc-panel-title">当前状态</h4>
            <div id="douyinStatusBox" class="douyin-status">等待加载抖音账号。</div>
          </div>
        </aside>
        <main class="douyin-main">
          <div class="douyin-tabbar" role="tablist" aria-label="抖音工作区">
            <button type="button" class="douyin-tab-btn is-active" data-douyin-tab="search">搜索采集</button>
            <button type="button" class="douyin-tab-btn" data-douyin-tab="message">私信发送</button>
          </div>
          <section class="douyin-tab-panel is-active" data-douyin-panel="search">
            <div class="tvc-panel douyin-search-card">
              <div class="douyin-panel-head">
                <div>
                  <h4 class="tvc-panel-title">搜索采集</h4>
                  <p class="tvc-panel-hint" style="margin:0;">先按关键词采集视频和作者线索，采集完成后沉淀到客户列表。</p>
                </div>
                <div class="douyin-count-badges">
                  <span class="douyin-badge" id="douyinLeadCountBadge">客户 0</span>
                  <span class="douyin-badge is-muted" id="douyinVideoCountBadge">视频 0</span>
                </div>
              </div>
              <div class="douyin-collect-form">
                <div class="tvc-field">
                  <label for="douyinKeywordInput">搜索关键词</label>
                  <input id="douyinKeywordInput" type="text" placeholder="例如：本地探店、母婴好物、装修案例">
                </div>
                <div class="tvc-field">
                  <label for="douyinMaxResultsInput">采集数量</label>
                  <input id="douyinMaxResultsInput" type="number" min="1" max="100" value="30">
                </div>
              </div>
              <div class="douyin-toolbar" style="margin-top:0.7rem;">
                <button type="button" id="douyinCollectBtn" class="btn btn-primary">开始采集</button>
                <button type="button" id="douyinClearResultsBtn" class="btn btn-ghost">清空结果</button>
              </div>
              <div id="douyinSearchResults" class="douyin-result-host" style="margin-top:0.85rem;">
                <div class="douyin-empty">抖音搜索结果会显示在这里，视频列表保留封面、标题、作者和采集进度。</div>
              </div>
            </div>
          </section>
          <section class="douyin-tab-panel" data-douyin-panel="message">
            <div class="tvc-panel">
              <div class="douyin-panel-head">
                <div>
                  <h4 class="tvc-panel-title">私信发送</h4>
                  <p class="tvc-panel-hint" style="margin:0;">配置私信内容后，从下方客户列表选择发送对象。</p>
                </div>
                <div class="douyin-count-badges">
                  <span class="douyin-badge" id="douyinMessageTargetBadge">已选 0</span>
                  <span class="douyin-badge is-muted" id="douyinCustomerCountBadge">客户 0</span>
                </div>
              </div>
              <div class="douyin-send-grid">
                <div>
                  <div class="douyin-message-grid">
                    <div class="tvc-field">
                      <label for="douyinMessageInput">私信内容</label>
                      <textarea id="douyinMessageInput" style="min-height:132px;" placeholder="支持多行内容，系统会逐行发送非空消息。"></textarea>
                    </div>
                    <div class="tvc-field">
                      <label for="douyinMessageTypeSelect">发送类型</label>
                      <select id="douyinMessageTypeSelect">
                        <option value="selected">发送给选中客户</option>
                        <option value="all">发送给全部客户</option>
                        <option value="intent">只发精准客户</option>
                      </select>
                    </div>
                  </div>
                  <div class="douyin-toolbar" style="margin-top:0.7rem;">
                    <button type="button" id="douyinSendMessageBtn" class="btn btn-primary">发送私信</button>
                    <button type="button" id="douyinSelectAllCustomersBtn" class="btn btn-ghost">全选客户</button>
                    <button type="button" id="douyinClearCustomerSelectionBtn" class="btn btn-ghost">清空选择</button>
                  </div>
                  <div class="douyin-interaction-note">私信对象来自下方客户列表，精准客户会自动排在前面。</div>
                </div>
                <div class="douyin-target-card">
                  <div id="douyinSelectedTargetCard">
                    <div class="douyin-target-title">尚未选择客户</div>
                    <div class="douyin-selected-target">在下面的客户列表里选择一个或多个对象开始私信。</div>
                  </div>
                </div>
              </div>
            </div>
            <div class="tvc-panel" style="margin-top:0.85rem;">
              <div class="douyin-panel-head">
                <div>
                  <h4 class="tvc-panel-title">客户列表</h4>
                  <p class="tvc-panel-hint" style="margin:0;">精准客户优先展示，勾选后即可发送私信。</p>
                </div>
              </div>
              <div id="douyinCustomerList" class="douyin-customer-list">
                <div class="douyin-empty">暂无客户。请先到“搜索采集”页按关键词采集视频和作者线索。</div>
              </div>
            </div>
          </section>
        </main>
      </div>
    </div>`;
  if (!document.getElementById('douyinCustomerCollectModal')) {
    var modal = document.createElement('div');
    modal.id = 'douyinCustomerCollectModal';
    modal.className = 'douyin-modal-backdrop';
    modal.style.display = 'none';
    modal.innerHTML = `
      <div class="douyin-modal-card" role="dialog" aria-modal="true" aria-labelledby="douyinCustomerCollectTitle">
        <div class="douyin-panel-head">
          <div>
            <h4 class="tvc-panel-title" id="douyinCustomerCollectTitle">采集视频客户</h4>
            <p class="tvc-panel-hint" id="douyinCustomerCollectSubtitle" style="margin:0;">采集评论客户后，可同步用 AI 筛出精准客户。</p>
          </div>
          <button type="button" class="btn btn-ghost btn-sm" id="douyinCollectModalCloseBtn">关闭</button>
        </div>
        <div class="douyin-modal-body">
          <div class="douyin-collect-form">
            <div class="tvc-field">
              <label for="douyinCollectMaxCommentsInput">采集评论数</label>
              <input id="douyinCollectMaxCommentsInput" type="number" min="1" max="500" value="120">
            </div>
            <label class="douyin-checkline" for="douyinAiFilterEnabledInput">
              <input id="douyinAiFilterEnabledInput" type="checkbox" checked>
              <span>启用 AI 筛选精准客户</span>
            </label>
          </div>
          <div class="tvc-field">
            <label for="douyinCommentFilterStrategySelect">筛选策略</label>
            <select id="douyinCommentFilterStrategySelect">
              <option value="prompt">正向筛选：按提示词筛精准客户</option>
              <option value="reverse">反向筛选：保留有效互动，排除灌水无关评论</option>
            </select>
          </div>
          <div class="tvc-field">
            <label for="douyinCommentDirectionTextarea">精准客户筛选提示词</label>
            <textarea id="douyinCommentDirectionTextarea" style="min-height:138px;" placeholder="描述什么样的评论用户算精准客户"></textarea>
          </div>
          <label class="douyin-checkline" for="douyinSaveFilterConfigInput">
            <input id="douyinSaveFilterConfigInput" type="checkbox" checked>
            <span>保存为下次默认规则</span>
          </label>
        </div>
        <div class="douyin-modal-actions">
          <button type="button" class="btn btn-ghost" id="douyinCollectModalCancelBtn">取消</button>
          <button type="button" class="btn btn-primary" id="douyinCollectModalConfirmBtn">开始采集客户</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
  }
  return host;
}

function _bindDouyinWorkbenchTabs(host) {
  if (!host || host.dataset.douyinTabsBound === '1') return;
  host.dataset.douyinTabsBound = '1';
  host.addEventListener('click', function(e) {
    var btn = e.target.closest('[data-douyin-tab]');
    if (!btn || !host.contains(btn)) return;
    var tab = btn.getAttribute('data-douyin-tab') || 'search';
    host.querySelectorAll('[data-douyin-tab]').forEach(function(item) {
      item.classList.toggle('is-active', item === btn);
    });
    host.querySelectorAll('[data-douyin-panel]').forEach(function(panel) {
      panel.classList.toggle('is-active', panel.getAttribute('data-douyin-panel') === tab);
    });
  });
}

function _douyinWorkbenchApiBase() {
  return (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE)
    ? String(LOCAL_API_BASE).replace(/\/$/, '')
    : '';
}

function _douyinWorkbenchHeaders() {
  var headers = (typeof authHeaders === 'function') ? Object.assign({}, authHeaders() || {}) : {};
  headers['Content-Type'] = headers['Content-Type'] || 'application/json';
  return headers;
}

function _douyinWorkbenchFetch(path, opts) {
  opts = opts || {};
  opts.headers = Object.assign(_douyinWorkbenchHeaders(), opts.headers || {});
  return fetch(_douyinWorkbenchApiBase() + path, opts).then(function(r) {
    return r.text().then(function(text) {
      var data = {};
      try { data = text ? JSON.parse(text) : {}; } catch (e1) {
        data = { detail: text || ('HTTP ' + r.status) };
      }
      if (!r.ok) {
        var msg = data.detail || data.message || ('HTTP ' + r.status);
        throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
      }
      return data;
    });
  });
}

function _douyinWorkbenchStatus(text, type) {
  var box = document.getElementById('douyinStatusBox');
  if (!box) return;
  var statusType = (typeof type === 'string') ? type : (type ? 'error' : 'info');
  box.classList.remove('is-error', 'is-loading', 'is-success', 'is-warning');
  if (statusType && statusType !== 'info') box.classList.add('is-' + statusType);
  box.innerHTML = (statusType === 'loading' ? '<span class="douyin-status-spinner" aria-hidden="true"></span>' : '') +
    '<span>' + escapeHtml(String(text || '')) + '</span>';
}

function _douyinWorkbenchDefaultDirection() {
  return '请筛选评论中是否属于精准客户。这里的精准客户指：有真实需求、了解意愿、咨询意愿、联系意愿的人。优先保留想了解、想咨询、感兴趣、想试试、想做、想进一步沟通，以及询问价格、费用、怎么买、怎么报名、怎么合作、怎么联系、适合我吗、新手能做吗、怎么开始这类评论。排除纯夸赞、纯围观、纯玩笑、无明确需求、重复内容。只判断是否精准，不做分层。';
}

function _normalizeDouyinFilterStrategy(value) {
  value = String(value || 'prompt').trim().toLowerCase();
  return value === 'reverse' ? 'reverse' : 'prompt';
}

function _douyinWorkbenchStorageKey() {
  return 'lobster_douyin_workbench_state_v1';
}

function _douyinWorkbenchPersistState() {
  try {
    localStorage.setItem(_douyinWorkbenchStorageKey(), JSON.stringify({
      searchResults: Array.isArray(_douyinWorkbenchState.searchResults) ? _douyinWorkbenchState.searchResults : [],
      leads: Array.isArray(_douyinWorkbenchState.leads) ? _douyinWorkbenchState.leads : [],
      selectedLeadIds: _douyinWorkbenchState.selectedLeadIds || {},
      saved_at: Date.now()
    }));
  } catch (e1) {}
}

function _douyinWorkbenchClearPersistedState() {
  try { localStorage.removeItem(_douyinWorkbenchStorageKey()); } catch (e1) {}
}

function _douyinWorkbenchRestoreState() {
  try {
    var raw = localStorage.getItem(_douyinWorkbenchStorageKey());
    if (!raw) return false;
    var data = JSON.parse(raw);
    _douyinWorkbenchState.searchResults = Array.isArray(data && data.searchResults) ? data.searchResults : [];
    _douyinWorkbenchState.leads = Array.isArray(data && data.leads) ? data.leads : [];
    _douyinWorkbenchState.selectedLeadIds = (data && typeof data.selectedLeadIds === 'object' && data.selectedLeadIds) ? data.selectedLeadIds : {};
    return !!(_douyinWorkbenchState.searchResults.length || _douyinWorkbenchState.leads.length);
  } catch (e1) {
    return false;
  }
}

function _douyinWorkbenchLeadBelongsToVideo(lead, video) {
  if (!lead || !video) return false;
  var leadAwemeId = String(lead.aweme_id || '').trim();
  var videoAwemeId = String(video.aweme_id || '').trim();
  if (leadAwemeId && videoAwemeId && leadAwemeId === videoAwemeId) return true;
  var leadUrl = String(lead.video_url || '').trim();
  var videoUrl = String(video.url || '').trim();
  if (leadUrl && videoUrl && leadUrl === videoUrl) return true;
  var leadTitle = String(lead.source_title || '').trim();
  var videoTitle = String(video.title || '').trim();
  var leadAuthor = String(lead.source_author || lead.video_author || '').trim();
  var videoAuthor = String(video.author || '').trim();
  if (leadTitle && videoTitle && leadTitle === videoTitle) {
    if (!leadAuthor || !videoAuthor || leadAuthor === videoAuthor) return true;
  }
  return false;
}

function _douyinWorkbenchVideoCustomerStats(video) {
  var stats = { all: 0, precise: 0 };
  (_douyinWorkbenchState.leads || []).forEach(function(lead) {
    if (!_douyinWorkbenchLeadBelongsToVideo(lead, video)) return;
    stats.all += 1;
    if (lead && lead.is_high_intent) stats.precise += 1;
  });
  return stats;
}

function _douyinWorkbenchRestoreAndRenderState() {
  if (!_douyinWorkbenchRestoreState()) return false;
  _douyinWorkbenchRenderSearchResults(_douyinWorkbenchState.searchResults || []);
  return true;
}

function loadDouyinWorkbenchConfig(silent) {
  return _douyinWorkbenchFetch('/api/douyin/workbench/config', { method: 'GET' })
    .then(function(data) {
      if (data && data.code === 200) {
        _douyinWorkbenchState.config = {
          ai_filter_enabled: data.ai_filter_enabled !== false,
          comment_direction: String(data.comment_direction || '').trim() || _douyinWorkbenchDefaultDirection(),
          comment_filter_strategy: _normalizeDouyinFilterStrategy(data.comment_filter_strategy),
          comment_max_comments: parseInt(data.comment_max_comments, 10) || 120
        };
      }
      return _douyinWorkbenchState.config;
    })
    .catch(function(err) {
      _douyinWorkbenchState.config = Object.assign({}, _douyinWorkbenchState.config || {}, {
        comment_direction: (_douyinWorkbenchState.config && _douyinWorkbenchState.config.comment_direction) || _douyinWorkbenchDefaultDirection(),
        comment_filter_strategy: _normalizeDouyinFilterStrategy(_douyinWorkbenchState.config && _douyinWorkbenchState.config.comment_filter_strategy),
        comment_max_comments: parseInt(_douyinWorkbenchState.config && _douyinWorkbenchState.config.comment_max_comments, 10) || 120
      });
      if (!silent) _douyinWorkbenchStatus('加载 AI 筛选默认配置失败，已使用内置默认规则：' + (err && err.message ? err.message : err), 'warning');
      return _douyinWorkbenchState.config;
    });
}

function saveDouyinWorkbenchConfigFromModal() {
  var enabledEl = document.getElementById('douyinAiFilterEnabledInput');
  var maxEl = document.getElementById('douyinCollectMaxCommentsInput');
  var strategyEl = document.getElementById('douyinCommentFilterStrategySelect');
  var directionEl = document.getElementById('douyinCommentDirectionTextarea');
  var payload = {
    ai_filter_enabled: enabledEl ? !!enabledEl.checked : true,
    comment_direction: directionEl ? String(directionEl.value || '').trim() : '',
    comment_filter_strategy: _normalizeDouyinFilterStrategy(strategyEl ? strategyEl.value : 'prompt'),
    comment_max_comments: maxEl ? (parseInt(maxEl.value, 10) || 120) : 120
  };
  _douyinWorkbenchState.config = Object.assign({}, _douyinWorkbenchState.config || {}, payload);
  return _douyinWorkbenchFetch('/api/douyin/workbench/config', {
    method: 'POST',
    body: JSON.stringify(payload)
  }).catch(function(err) {
    _douyinWorkbenchStatus('默认规则保存失败，本次仍会按当前弹窗设置采集：' + (err && err.message ? err.message : err), 'warning');
  });
}

function _douyinWorkbenchModalPayload() {
  var enabledEl = document.getElementById('douyinAiFilterEnabledInput');
  var maxEl = document.getElementById('douyinCollectMaxCommentsInput');
  var strategyEl = document.getElementById('douyinCommentFilterStrategySelect');
  var directionEl = document.getElementById('douyinCommentDirectionTextarea');
  return {
    ai_filter_enabled: enabledEl ? !!enabledEl.checked : true,
    comment_direction: directionEl ? String(directionEl.value || '').trim() : '',
    comment_filter_strategy: _normalizeDouyinFilterStrategy(strategyEl ? strategyEl.value : 'prompt'),
    max_comments: Math.max(1, Math.min(parseInt(maxEl ? maxEl.value : '120', 10) || 120, 500))
  };
}

function _douyinWorkbenchFillCollectModal(videoIndex) {
  var config = _douyinWorkbenchState.config || {};
  var item = (_douyinWorkbenchState.searchResults || [])[parseInt(videoIndex, 10)] || {};
  var subtitle = document.getElementById('douyinCustomerCollectSubtitle');
  var enabledEl = document.getElementById('douyinAiFilterEnabledInput');
  var maxEl = document.getElementById('douyinCollectMaxCommentsInput');
  var strategyEl = document.getElementById('douyinCommentFilterStrategySelect');
  var directionEl = document.getElementById('douyinCommentDirectionTextarea');
  if (subtitle) subtitle.textContent = '视频：' + (item.title || item.aweme_id || '未命名视频');
  if (enabledEl) enabledEl.checked = config.ai_filter_enabled !== false;
  if (maxEl) maxEl.value = parseInt(config.comment_max_comments, 10) || 120;
  if (strategyEl) strategyEl.value = _normalizeDouyinFilterStrategy(config.comment_filter_strategy);
  if (directionEl) directionEl.value = String(config.comment_direction || '').trim() || _douyinWorkbenchDefaultDirection();
}

function openDouyinWorkbenchCollectModal(videoIndex) {
  var modal = document.getElementById('douyinCustomerCollectModal');
  _douyinWorkbenchState.pendingVideoIndex = parseInt(videoIndex, 10);
  loadDouyinWorkbenchConfig(true).then(function() {
    _douyinWorkbenchFillCollectModal(videoIndex);
    if (modal) {
      modal.style.display = 'flex';
      modal.classList.add('is-open');
    }
  });
}

function closeDouyinWorkbenchCollectModal() {
  var modal = document.getElementById('douyinCustomerCollectModal');
  if (modal) {
    modal.classList.remove('is-open');
    modal.style.display = 'none';
  }
}

function _douyinWorkbenchSelectedAccount() {
  var id = parseInt(_douyinWorkbenchState.selectedAccountId, 10) || 0;
  return (_douyinWorkbenchState.accounts || []).find(function(acct) {
    return parseInt(acct.id, 10) === id;
  }) || null;
}

function _douyinWorkbenchSetBusy(ids, busy) {
  (ids || []).forEach(function(id) {
    var el = document.getElementById(id);
    if (el) {
      el.disabled = !!busy;
      el.classList.toggle('is-loading', !!busy);
    }
  });
}

function _douyinWorkbenchSetVideoBusy(index, busy) {
  document.querySelectorAll('[data-douyin-video-action="collect-customers"][data-douyin-video-index="' + String(index) + '"]').forEach(function(el) {
    el.disabled = !!busy;
    el.classList.toggle('is-loading', !!busy);
  });
}

function _douyinWorkbenchStatusLabel(status) {
  if (status === 'active' || status === 'online') return '已登录';
  if (status === 'offline') return '未登录';
  if (status === 'error') return '异常';
  return '待登录';
}

function _douyinWorkbenchRenderAccounts() {
  var accounts = _douyinWorkbenchState.accounts || [];
  var select = document.getElementById('douyinAccountSelect');
  var list = document.getElementById('douyinAccountList');
  if (!select || !list) return;

  if (!accounts.length) {
    _douyinWorkbenchState.selectedAccountId = null;
    select.innerHTML = '<option value="">暂无抖音账号</option>';
    list.innerHTML = '<div class="douyin-empty">还没有抖音账号。先在上方填写备注并点击“添加账号”。</div>';
    _douyinWorkbenchStatus('暂无抖音账号，请先添加账号。');
    return;
  }

  var selected = parseInt(_douyinWorkbenchState.selectedAccountId, 10) || 0;
  if (!accounts.some(function(acct) { return parseInt(acct.id, 10) === selected; })) {
    selected = parseInt(accounts[0].id, 10) || 0;
    _douyinWorkbenchState.selectedAccountId = selected;
  }

  select.innerHTML = accounts.map(function(acct) {
    return '<option value="' + escapeAttr(String(acct.id)) + '">' +
      escapeHtml(String(acct.nickname || '抖音账号')) + ' · ' +
      escapeHtml(_douyinWorkbenchStatusLabel(acct.status)) +
      '</option>';
  }).join('');
  select.value = String(selected);

  list.innerHTML = accounts.map(function(acct) {
    var id = parseInt(acct.id, 10) || 0;
    var active = id === selected;
    var status = String(acct.status || 'pending');
    var isOriginSlot = !!acct.is_origin_slot || acct.managed_by === 'douyin_origin';
    var lastLogin = acct.last_login ? String(acct.last_login).replace('T', ' ').replace('Z', '') : '未记录';
    return '<div class="douyin-account-card' + (active ? ' is-active' : '') + '" data-douyin-account-id="' + escapeAttr(String(id)) + '">' +
      '<div class="douyin-account-main">' +
        '<div class="douyin-account-name">' + escapeHtml(String(acct.nickname || '抖音账号')) + '</div>' +
        '<div class="douyin-account-meta">最近登录：' + escapeHtml(lastLogin) + '</div>' +
      '</div>' +
      '<span class="douyin-login-state is-' + escapeAttr(status) + '">' + escapeHtml(_douyinWorkbenchStatusLabel(status)) + '</span>' +
      '<div class="douyin-account-actions">' +
        '<button type="button" class="btn btn-ghost btn-sm" data-douyin-account-action="select">选择</button>' +
        '<button type="button" class="btn btn-primary btn-sm" data-douyin-account-action="open">登录</button>' +
        '<button type="button" class="btn btn-ghost btn-sm" data-douyin-account-action="check">检测</button>' +
        (isOriginSlot ? '' : '<button type="button" class="btn btn-ghost btn-sm" data-douyin-account-action="delete">删除</button>') +
      '</div>' +
    '</div>';
  }).join('');

  var current = _douyinWorkbenchSelectedAccount();
  if (current) {
    _douyinWorkbenchStatus(
      (current.status === 'active' || current.status === 'online')
        ? '当前账号“' + current.nickname + '”已登录，可以继续采集和私信。'
        : '当前账号“' + current.nickname + '”待登录，请点击“打开登录”。'
    );
  }
}

function loadDouyinWorkbenchAccounts() {
  _douyinWorkbenchStatus('正在加载抖音账号...', 'loading');
  return _douyinWorkbenchFetch('/api/accounts', { headers: _douyinWorkbenchHeaders() })
    .then(function(data) {
      var rows = Array.isArray(data.accounts) ? data.accounts : [];
      _douyinWorkbenchState.accounts = rows.filter(function(acct) {
        return acct && acct.platform === 'douyin';
      });
      _douyinWorkbenchRenderAccounts();
      _douyinWorkbenchRestoreAndRenderState();
    })
    .catch(function(err) {
      _douyinWorkbenchStatus('加载账号失败：' + (err && err.message ? err.message : err), true);
    });
}

function createDouyinWorkbenchAccount() {
  var input = document.getElementById('douyinNewAccountNicknameInput');
  var nick = input ? String(input.value || '').trim() : '';
  if (!nick) {
    _douyinWorkbenchStatus('请先填写账号备注，例如“主号”或“探店号”。', true);
    if (input) input.focus();
    return;
  }
  _douyinWorkbenchSetBusy(['douyinCreateAccountBtn'], true);
  _douyinWorkbenchStatus('正在添加抖音账号...', 'loading');
  _douyinWorkbenchFetch('/api/accounts', {
    method: 'POST',
    body: JSON.stringify({ platform: 'douyin', nickname: nick })
  }).then(function(data) {
    if (input) input.value = '';
    if (data && data.id) _douyinWorkbenchState.selectedAccountId = parseInt(data.id, 10);
    _douyinWorkbenchStatus('账号已添加，请点击“打开登录”完成抖音扫码。', 'success');
    return loadDouyinWorkbenchAccounts();
  }).catch(function(err) {
    _douyinWorkbenchStatus('添加账号失败：' + (err && err.message ? err.message : err), true);
  }).finally(function() {
    _douyinWorkbenchSetBusy(['douyinCreateAccountBtn'], false);
  });
}

function openDouyinWorkbenchLogin(accountId) {
  var id = parseInt(accountId || _douyinWorkbenchState.selectedAccountId, 10) || 0;
  if (!id) {
    _douyinWorkbenchStatus('请先添加或选择一个抖音账号。', true);
    return;
  }
  _douyinWorkbenchSetBusy(['douyinOpenLoginBtn', 'douyinOpenLoginInlineBtn'], true);
  _douyinWorkbenchStatus('正在打开抖音前台登录页...', 'loading');
  _douyinWorkbenchFetch('/api/accounts/' + encodeURIComponent(String(id)) + '/douyin-workbench/open', {
    method: 'POST'
  }).then(function(data) {
    _douyinWorkbenchStatus(data.message || '已打开抖音前台页面，请在浏览器窗口里完成登录。', data.logged_in ? 'success' : 'warning');
    return loadDouyinWorkbenchAccounts();
  }).catch(function(err) {
    _douyinWorkbenchStatus('打开登录失败：' + (err && err.message ? err.message : err), true);
  }).finally(function() {
    _douyinWorkbenchSetBusy(['douyinOpenLoginBtn', 'douyinOpenLoginInlineBtn'], false);
  });
}

function checkDouyinWorkbenchLogin(accountId) {
  var id = parseInt(accountId || _douyinWorkbenchState.selectedAccountId, 10) || 0;
  if (!id) {
    _douyinWorkbenchStatus('请先添加或选择一个抖音账号。', true);
    return;
  }
  _douyinWorkbenchSetBusy(['douyinCheckStatusBtn'], true);
  _douyinWorkbenchStatus('正在检测抖音前台登录状态...', 'loading');
  _douyinWorkbenchFetch('/api/accounts/' + encodeURIComponent(String(id)) + '/douyin-workbench/login-status', {
    method: 'GET'
  }).then(function(data) {
    _douyinWorkbenchStatus(data.message || (data.logged_in ? '抖音前台已登录。' : '抖音前台未登录。'), data.logged_in ? 'success' : 'warning');
    return loadDouyinWorkbenchAccounts();
  }).catch(function(err) {
    _douyinWorkbenchStatus('检测登录失败：' + (err && err.message ? err.message : err), true);
  }).finally(function() {
    _douyinWorkbenchSetBusy(['douyinCheckStatusBtn'], false);
  });
}

function deleteDouyinWorkbenchAccount(accountId) {
  var id = parseInt(accountId, 10) || 0;
  if (!id) return;
  var acct = (_douyinWorkbenchState.accounts || []).find(function(item) {
    return parseInt(item.id, 10) === id;
  });
  var name = acct ? acct.nickname : ('账号 ' + id);
  if (!window.confirm('确定删除抖音账号“' + name + '”？浏览器登录目录也会一起删除。')) return;
  _douyinWorkbenchStatus('正在删除账号...', 'loading');
  _douyinWorkbenchFetch('/api/accounts/' + encodeURIComponent(String(id)), { method: 'DELETE' })
    .then(function() {
      if (parseInt(_douyinWorkbenchState.selectedAccountId, 10) === id) {
        _douyinWorkbenchState.selectedAccountId = null;
      }
      return loadDouyinWorkbenchAccounts();
    })
    .catch(function(err) {
      _douyinWorkbenchStatus('删除账号失败：' + (err && err.message ? err.message : err), true);
    });
}

function _douyinLeadKey(item) {
  item = item || {};
  return String(item.sec_user_id || item.profile_url || item.author || item.aweme_id || item.url || '').trim();
}

function _douyinWorkbenchUpdateCounts() {
  var videos = _douyinWorkbenchState.searchResults || [];
  var leads = _douyinWorkbenchState.leads || [];
  var selectedCount = Object.keys(_douyinWorkbenchState.selectedLeadIds || {}).filter(function(key) {
    return _douyinWorkbenchState.selectedLeadIds[key];
  }).length;
  var videoBadge = document.getElementById('douyinVideoCountBadge');
  var leadBadge = document.getElementById('douyinLeadCountBadge');
  var customerBadge = document.getElementById('douyinCustomerCountBadge');
  var targetBadge = document.getElementById('douyinMessageTargetBadge');
  if (videoBadge) videoBadge.textContent = '视频 ' + videos.length;
  if (leadBadge) leadBadge.textContent = '客户 ' + leads.length;
  if (customerBadge) customerBadge.textContent = '客户 ' + leads.length;
  if (targetBadge) targetBadge.textContent = '已选 ' + selectedCount;
}

function _douyinWorkbenchBuildLeads(results) {
  var map = {};
  (results || []).forEach(function(item) {
    var key = _douyinLeadKey(item);
    if (!key) return;
    if (!map[key]) {
      map[key] = {
        id: key,
        author: item.author || '未知作者',
        profile_url: item.profile_url || '',
        sec_user_id: item.sec_user_id || '',
        avatar_url: item.avatar_url || '',
        source_title: item.title || '',
        source_cover: item.cover_image || '',
        aweme_id: item.aweme_id || ''
      };
    }
  });
  return Object.keys(map).map(function(key) { return map[key]; });
}

function _douyinWorkbenchMergeCustomers(customers, sourceVideo) {
  var map = {};
  (_douyinWorkbenchState.leads || []).forEach(function(lead) {
    var key = _douyinLeadKey(lead);
    if (key) map[key] = Object.assign({}, lead);
  });
  (customers || []).forEach(function(customer) {
    var key = _douyinLeadKey({
      sec_user_id: customer.sec_user_id || customer.sec_uid,
      profile_url: customer.profile_url,
      author: customer.author || customer.nickname,
      aweme_id: customer.aweme_id
    });
    if (!key) return;
    var existing = map[key] || {};
    map[key] = Object.assign({}, existing, {
      id: key,
      author: customer.author || customer.nickname || existing.author || '未知客户',
      nickname: customer.nickname || customer.author || existing.nickname || '',
      profile_url: customer.profile_url || existing.profile_url || '',
      sec_user_id: customer.sec_user_id || customer.sec_uid || existing.sec_user_id || '',
      avatar_url: customer.avatar_url || existing.avatar_url || '',
      latest_comment: customer.latest_comment || existing.latest_comment || '',
      comment_count: customer.comment_count || existing.comment_count || 0,
      digg_count: customer.digg_count || existing.digg_count || 0,
      is_high_intent: customer.is_high_intent || existing.is_high_intent || false,
      intent_level: customer.intent_level || existing.intent_level || '',
      intent_reason: customer.intent_reason || customer.reason || existing.intent_reason || '',
      intent_score: customer.intent_score || customer.score || existing.intent_score || '',
      source_title: (sourceVideo && sourceVideo.title) || existing.source_title || '',
      source_author: (sourceVideo && sourceVideo.author) || existing.source_author || '',
      source_cover: (sourceVideo && sourceVideo.cover_image) || existing.source_cover || '',
      aweme_id: customer.aweme_id || (sourceVideo && sourceVideo.aweme_id) || existing.aweme_id || '',
      video_url: customer.video_url || (sourceVideo && sourceVideo.url) || existing.video_url || ''
    });
  });
  _douyinWorkbenchState.leads = Object.keys(map).map(function(key) { return map[key]; });
  _douyinWorkbenchPersistState();
}

function _douyinWorkbenchRenderSearchResults(results) {
  var box = document.getElementById('douyinSearchResults');
  if (!box) return;
  results = Array.isArray(results) ? results : [];
  var preservedLeads = Array.isArray(_douyinWorkbenchState.leads) ? _douyinWorkbenchState.leads.slice() : [];
  _douyinWorkbenchState.searchResults = results;
  _douyinWorkbenchState.leads = _douyinWorkbenchBuildLeads(results);
  if (preservedLeads.length) _douyinWorkbenchMergeCustomers(preservedLeads, null);
  if (!results.length) {
    box.innerHTML = '<div class="douyin-empty">暂无搜索结果。已用可见 Chrome 打开抖音搜索页；如果页面有验证码、安全校验或内容未加载，请先在浏览器里处理后再点击“开始采集”。</div>';
    _douyinWorkbenchRenderCustomers();
    _douyinWorkbenchUpdateCounts();
    _douyinWorkbenchPersistState();
    return;
  }
  box.innerHTML = results.map(function(item, idx) {
    var title = String(item.title || '未命名视频').trim();
    var author = String(item.author || '未知作者').trim();
    var cover = String(item.cover_image || '').trim();
    var likes = String(item.likes_text || item.likes || '').trim();
    var comments = String(item.comments_text || item.comments || '').trim();
    var stats = _douyinWorkbenchVideoCustomerStats(item);
    return '<div class="douyin-video-card">' +
      '<div class="douyin-video-thumb">' +
        (cover ? '<img src="' + escapeAttr(cover) + '" alt="视频封面" loading="lazy">' : '<span>无封面</span>') +
      '</div>' +
      '<div class="douyin-video-body">' +
        '<div class="douyin-video-title">' + escapeHtml(title) + '</div>' +
        '<div class="douyin-video-meta">作者：' + escapeHtml(author) + '</div>' +
        '<div class="douyin-video-tags">' +
          '<span>序号 ' + (idx + 1) + '</span>' +
          (likes ? '<span>点赞 ' + escapeHtml(likes) + '</span>' : '') +
          (comments ? '<span>评论 ' + escapeHtml(comments) + '</span>' : '') +
        '</div>' +
        '<div class="douyin-video-meta">客户沉淀：全部 ' + stats.all + ' / 精准 ' + stats.precise + '</div>' +
        '<div class="douyin-video-actions">' +
          '<button type="button" class="btn btn-primary btn-sm" data-douyin-video-action="collect-customers" data-douyin-video-index="' + idx + '">采集客户</button>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-douyin-video-action="select-video" data-douyin-video-index="' + idx + '">选中视频</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  }).join('');
  _douyinWorkbenchRenderCustomers();
  _douyinWorkbenchUpdateCounts();
  _douyinWorkbenchPersistState();
}

function _douyinWorkbenchRenderCustomers() {
  var list = document.getElementById('douyinCustomerList');
  if (!list) return;
  var leads = (_douyinWorkbenchState.leads || []).slice().sort(function(a, b) {
    var ai = a && a.is_high_intent ? 1 : 0;
    var bi = b && b.is_high_intent ? 1 : 0;
    if (ai !== bi) return bi - ai;
    var as = !!(_douyinWorkbenchState.selectedLeadIds || {})[_douyinLeadKey(a)];
    var bs = !!(_douyinWorkbenchState.selectedLeadIds || {})[_douyinLeadKey(b)];
    if (as !== bs) return bs ? 1 : -1;
    var ac = parseInt(a && a.comment_count, 10) || 0;
    var bc = parseInt(b && b.comment_count, 10) || 0;
    if (ac !== bc) return bc - ac;
    return String(a && (a.author || a.nickname || '')).localeCompare(String(b && (b.author || b.nickname || '')), 'zh-Hans-CN');
  });
  if (!leads.length) {
    list.innerHTML = '<div class="douyin-empty">暂无客户。请先到“搜索采集”页按关键词采集视频和作者线索。</div>';
    _douyinWorkbenchUpdateSelectedTargetCard();
    return;
  }
  list.innerHTML = leads.map(function(lead) {
    var key = _douyinLeadKey(lead);
    var checked = !!(_douyinWorkbenchState.selectedLeadIds || {})[key];
    return '<label class="douyin-customer-card' + (checked ? ' is-selected' : '') + '">' +
      '<input type="checkbox" data-douyin-lead-id="' + escapeAttr(key) + '"' + (checked ? ' checked' : '') + '>' +
      '<div class="douyin-customer-main">' +
        '<div class="douyin-target-title">' + escapeHtml(String(lead.author || '未知客户')) + '</div>' +
        (lead.is_high_intent ? '<div class="douyin-intent-row"><span class="douyin-badge is-intent">精准客户</span>' + (lead.intent_reason ? '<span>' + escapeHtml(String(lead.intent_reason)) + '</span>' : '') + '</div>' : '') +
        '<div class="douyin-selected-target">' + escapeHtml(String(lead.latest_comment || lead.source_title || lead.profile_url || lead.sec_user_id || '来自搜索采集')) + '</div>' +
        (lead.comment_count ? '<div class="douyin-selected-target">评论 ' + escapeHtml(String(lead.comment_count)) + ' 条</div>' : '') +
        (lead.message_status ? '<div class="douyin-message-state is-' + escapeAttr(lead.message_status) + '">' + escapeHtml(lead.message_status === 'sent' ? ('私信已发送' + (lead.server_message_id ? '：' + lead.server_message_id : '')) : ('私信失败：' + (lead.message_error || '请重试'))) + '</div>' : '') +
      '</div>' +
    '</label>';
  }).join('');
  _douyinWorkbenchUpdateSelectedTargetCard();
  _douyinWorkbenchUpdateCounts();
  _douyinWorkbenchPersistState();
}

function _douyinWorkbenchUpdateSelectedTargetCard() {
  var card = document.getElementById('douyinSelectedTargetCard');
  if (!card) return;
  var leads = _douyinWorkbenchState.leads || [];
  var selected = leads.filter(function(lead) {
    return !!(_douyinWorkbenchState.selectedLeadIds || {})[_douyinLeadKey(lead)];
  });
  var preciseCount = leads.filter(function(lead) { return !!(lead && lead.is_high_intent); }).length;
  if (!selected.length) {
    card.innerHTML = '<div class="douyin-target-title">尚未选择客户</div>' +
      '<div class="douyin-selected-target">从下方客户列表勾选对象。当前共有 ' + leads.length + ' 位客户，其中精准客户 ' + preciseCount + ' 位。</div>';
    return;
  }
  var selectedPrecise = selected.filter(function(item) { return !!(item && item.is_high_intent); }).length;
  card.innerHTML = '<div class="douyin-target-title">已选择 ' + selected.length + ' 个客户</div>' +
    '<div class="douyin-selected-target">' + escapeHtml(selected.slice(0, 4).map(function(item) { return item.author || item.nickname || '客户'; }).join('、')) +
    (selected.length > 4 ? ' 等' : '') + '</div>' +
    '<div class="douyin-selected-target">其中精准客户 ' + selectedPrecise + ' 位。</div>';
}

function _douyinWorkbenchSelectedMessageTargets() {
  var leads = _douyinWorkbenchState.leads || [];
  var modeEl = document.getElementById('douyinMessageTypeSelect');
  var mode = modeEl ? String(modeEl.value || 'selected') : 'selected';
  var targets = [];
  leads.forEach(function(lead) {
    var key = _douyinLeadKey(lead);
    var selected = !!(_douyinWorkbenchState.selectedLeadIds || {})[key];
    if (mode === 'selected' && !selected) return;
    if (mode === 'intent' && !lead.is_high_intent && !selected) return;
    if (mode === 'all' || mode === 'selected' || mode === 'intent') {
      targets.push({
        nickname: lead.nickname || lead.author || '',
        author: lead.author || lead.nickname || '',
        profile_url: lead.profile_url || '',
        sec_user_id: lead.sec_user_id || lead.sec_uid || ''
      });
    }
  });
  var seen = {};
  return targets.filter(function(item) {
    var key = String(item.profile_url || item.sec_user_id || '').trim();
    if (!key || seen[key]) return false;
    seen[key] = true;
    return true;
  });
}

function sendDouyinWorkbenchMessages() {
  var account = _douyinWorkbenchSelectedAccount();
  var messageEl = document.getElementById('douyinMessageInput');
  var message = messageEl ? String(messageEl.value || '').trim() : '';
  var targets = _douyinWorkbenchSelectedMessageTargets();
  if (!account) {
    _douyinWorkbenchStatus('请先添加或选择一个抖音账号。', true);
    return;
  }
  if (!message) {
    _douyinWorkbenchStatus('请先填写私信内容。', true);
    if (messageEl) messageEl.focus();
    return;
  }
  if (!targets.length) {
    _douyinWorkbenchStatus('请先在客户列表选择客户。', true);
    return;
  }
  _douyinWorkbenchSetBusy(['douyinSendMessageBtn'], true);
  _douyinWorkbenchStatus('正在发送私信，共 ' + targets.length + ' 个客户...', 'loading');
  _douyinWorkbenchFetch('/api/douyin/message/send', {
    method: 'POST',
    body: JSON.stringify({
      account_id: account.id,
      message: message,
      targets: targets
    })
  }).then(function(data) {
    if (data.code !== 200) {
      throw new Error(data.msg || data.detail || '私信发送失败');
    }
    var rows = Array.isArray(data.results) ? data.results : [];
    rows.forEach(function(row) {
      var key = String(row.profile_url || row.sec_user_id || '').trim();
      (_douyinWorkbenchState.leads || []).forEach(function(lead) {
        var leadKey = String(lead.profile_url || lead.sec_user_id || '').trim();
        if (key && key === leadKey) {
          lead.message_status = row.ok ? 'sent' : 'failed';
          lead.message_error = row.ok ? '' : (row.message || '发送失败');
          lead.server_message_id = row.server_message_id || '';
        }
      });
    });
    _douyinWorkbenchRenderCustomers();
    var failed = parseInt(data.failed, 10) || 0;
    var firstOk = rows.find(function(row) { return row && row.ok; });
    var firstFail = rows.find(function(row) { return row && !row.ok; });
    var extra = firstOk && firstOk.server_message_id ? (' 消息ID：' + firstOk.server_message_id) : '';
    if (firstFail && firstFail.message) extra += ' 失败原因：' + firstFail.message;
    _douyinWorkbenchStatus((data.msg || ('私信发送完成，成功 ' + (data.success || 0) + ' 个，失败 ' + failed + ' 个。')) + extra, failed ? 'warning' : 'success');
    return loadDouyinWorkbenchAccounts();
  }).catch(function(err) {
    _douyinWorkbenchStatus('私信发送失败：' + (err && err.message ? err.message : err), true);
  }).finally(function() {
    _douyinWorkbenchSetBusy(['douyinSendMessageBtn'], false);
  });
}

function startDouyinWorkbenchCollect() {
  var account = _douyinWorkbenchSelectedAccount();
  var keywordEl = document.getElementById('douyinKeywordInput');
  var maxEl = document.getElementById('douyinMaxResultsInput');
  var keyword = keywordEl ? String(keywordEl.value || '').trim() : '';
  var maxResults = maxEl ? parseInt(maxEl.value, 10) || 30 : 30;
  if (!account) {
    _douyinWorkbenchStatus('请先添加或选择一个抖音账号。', true);
    return;
  }
  if (!keyword) {
    _douyinWorkbenchStatus('请输入抖音搜索关键词。', true);
    if (keywordEl) keywordEl.focus();
    return;
  }
  _douyinWorkbenchSetBusy(['douyinCollectBtn'], true);
  _douyinWorkbenchStatus('正在打开可见 Chrome 搜索抖音；如果页面出现验证码，请先在浏览器里处理后再重试采集。', 'loading');
  var box = document.getElementById('douyinSearchResults');
  if (box) box.innerHTML = '<div class="douyin-empty">正在用可见浏览器采集“' + escapeHtml(keyword) + '”的搜索结果；遇到验证码请先在打开的 Chrome 窗口处理。</div>';
  _douyinWorkbenchFetch('/api/douyin/search/collect', {
    method: 'POST',
    body: JSON.stringify({
      keyword: keyword,
      account_id: account.id,
      max_results: maxResults
    })
  }).then(function(data) {
    if (data.code !== 200) {
      throw new Error(data.msg || data.detail || '抖音搜索采集失败');
    }
    var rows = Array.isArray(data.data) ? data.data : [];
    _douyinWorkbenchState.selectedLeadIds = {};
    _douyinWorkbenchRenderSearchResults(rows);
    _douyinWorkbenchStatus(data.msg || ('采集完成，共 ' + rows.length + ' 条视频。'), rows.length === 0 ? 'warning' : 'success');
    return loadDouyinWorkbenchAccounts();
  }).catch(function(err) {
    _douyinWorkbenchRenderSearchResults([]);
    _douyinWorkbenchStatus('采集失败：' + (err && err.message ? err.message : err), true);
  }).finally(function() {
    _douyinWorkbenchSetBusy(['douyinCollectBtn'], false);
  });
}

function collectDouyinWorkbenchVideoCustomers(videoIndex, collectOptions) {
  var account = _douyinWorkbenchSelectedAccount();
  var results = _douyinWorkbenchState.searchResults || [];
  var item = results[parseInt(videoIndex, 10)];
  collectOptions = collectOptions || {};
  if (!account) {
    _douyinWorkbenchStatus('请先添加或选择一个抖音账号。', true);
    return;
  }
  if (!item || (!item.url && !item.aweme_id)) {
    _douyinWorkbenchStatus('请选择一个有效的视频后再采集客户。', true);
    return;
  }
  var saveEl = document.getElementById('douyinSaveFilterConfigInput');
  var savePromise = (saveEl && saveEl.checked) ? saveDouyinWorkbenchConfigFromModal() : Promise.resolve();
  closeDouyinWorkbenchCollectModal();
  _douyinWorkbenchSetVideoBusy(parseInt(videoIndex, 10), true);
  _douyinWorkbenchStatus('正在通过协议模式采集《' + (item.title || '当前视频') + '》下方客户，完成后会筛选精准客户...', 'loading');
  savePromise.then(function() {
    return _douyinWorkbenchFetch('/api/douyin/video/customers', {
      method: 'POST',
      body: JSON.stringify({
        account_id: account.id,
        video_url: item.url || '',
        aweme_id: item.aweme_id || '',
        max_comments: collectOptions.max_comments || 120,
        ai_filter_enabled: collectOptions.ai_filter_enabled !== false,
        comment_direction: collectOptions.comment_direction || '',
        comment_filter_strategy: collectOptions.comment_filter_strategy || 'prompt'
      })
    });
  }).then(function(data) {
    if (data.code !== 200) {
      throw new Error(data.msg || data.detail || '采集视频客户失败');
    }
    var customers = Array.isArray(data.customers) ? data.customers : [];
    _douyinWorkbenchMergeCustomers(customers, item);
    _douyinWorkbenchRenderCustomers();
    var messageTab = document.querySelector('[data-douyin-tab="message"]');
    if (messageTab) messageTab.click();
    var ai = data.ai_filter || {};
    var precise = parseInt(data.total_high_intent, 10) || (Array.isArray(data.high_intent_users) ? data.high_intent_users.length : 0);
    var suffix = ai.enabled ? ('，AI 筛出精准客户 ' + precise + ' 位' + (ai.fallback_used ? '（已用本地规则兜底）' : '')) : '';
    _douyinWorkbenchStatus((data.msg || ('客户采集完成，共沉淀 ' + customers.length + ' 位客户。')) + suffix, customers.length === 0 ? 'warning' : 'success');
  }).catch(function(err) {
    _douyinWorkbenchStatus('采集视频客户失败：' + (err && err.message ? err.message : err), true);
  }).finally(function() {
    _douyinWorkbenchSetVideoBusy(parseInt(videoIndex, 10), false);
  });
}

function _bindDouyinWorkbenchActions(host) {
  if (!host || host.dataset.douyinActionsBound === '1') return;
  host.dataset.douyinActionsBound = '1';
  host.addEventListener('click', function(e) {
    var target = e.target;
    if (!target) return;
    if (target.closest('#douyinWorkbenchBackBtn')) {
      var nav = document.querySelector('.nav-left-item[data-view="chat"]');
      if (nav) nav.click();
      return;
    }
    if (target.closest('#douyinReloadAccountsBtn')) {
      loadDouyinWorkbenchAccounts();
      return;
    }
    if (target.closest('#douyinCreateAccountBtn')) {
      createDouyinWorkbenchAccount();
      return;
    }
    if (target.closest('#douyinOpenLoginBtn') || target.closest('#douyinOpenLoginInlineBtn')) {
      openDouyinWorkbenchLogin();
      return;
    }
    if (target.closest('#douyinCheckStatusBtn')) {
      checkDouyinWorkbenchLogin();
      return;
    }
    if (target.closest('#douyinCollectBtn')) {
      startDouyinWorkbenchCollect();
      return;
    }
    if (target.closest('#douyinClearResultsBtn')) {
      _douyinWorkbenchState.searchResults = [];
      _douyinWorkbenchState.leads = [];
      _douyinWorkbenchState.selectedLeadIds = {};
      _douyinWorkbenchRenderSearchResults([]);
      _douyinWorkbenchStatus('已清空搜索结果和客户列表。');
      return;
    }
    if (target.closest('#douyinSelectAllCustomersBtn')) {
      (_douyinWorkbenchState.leads || []).forEach(function(lead) {
        var key = _douyinLeadKey(lead);
        if (key) _douyinWorkbenchState.selectedLeadIds[key] = true;
      });
      _douyinWorkbenchRenderCustomers();
      return;
    }
    if (target.closest('#douyinClearCustomerSelectionBtn')) {
      _douyinWorkbenchState.selectedLeadIds = {};
      _douyinWorkbenchRenderCustomers();
      return;
    }
    if (target.closest('#douyinSendMessageBtn')) {
      sendDouyinWorkbenchMessages();
      return;
    }
    var videoAction = target.closest('[data-douyin-video-action]');
    if (videoAction) {
      var idx = parseInt(videoAction.getAttribute('data-douyin-video-index'), 10);
      var actionName = videoAction.getAttribute('data-douyin-video-action');
      if (actionName === 'collect-customers') {
        openDouyinWorkbenchCollectModal(idx);
      } else if (actionName === 'select-video') {
        var item = (_douyinWorkbenchState.searchResults || [])[idx];
        if (item) _douyinWorkbenchStatus('已选中视频《' + (item.title || item.aweme_id || '未命名视频') + '》，点击“采集客户”会采集该视频下方评论客户。');
      }
      return;
    }
    var actionBtn = target.closest('[data-douyin-account-action]');
    if (actionBtn) {
      var card = actionBtn.closest('[data-douyin-account-id]');
      var id = card ? parseInt(card.getAttribute('data-douyin-account-id'), 10) : 0;
      var action = actionBtn.getAttribute('data-douyin-account-action');
      if (id) _douyinWorkbenchState.selectedAccountId = id;
      _douyinWorkbenchRenderAccounts();
      if (action === 'open') openDouyinWorkbenchLogin(id);
      if (action === 'check') checkDouyinWorkbenchLogin(id);
      if (action === 'delete') deleteDouyinWorkbenchAccount(id);
      return;
    }
  });
  host.addEventListener('change', function(e) {
    var select = e.target && e.target.closest('#douyinAccountSelect');
    if (!select) return;
    _douyinWorkbenchState.selectedAccountId = parseInt(select.value, 10) || null;
    _douyinWorkbenchRenderAccounts();
  });
  host.addEventListener('change', function(e) {
    var checkbox = e.target && e.target.closest('[data-douyin-lead-id]');
    if (!checkbox) return;
    var key = checkbox.getAttribute('data-douyin-lead-id') || '';
    if (!key) return;
    _douyinWorkbenchState.selectedLeadIds[key] = !!checkbox.checked;
    _douyinWorkbenchRenderCustomers();
  });
  var nickInput = host.querySelector('#douyinNewAccountNicknameInput');
  if (nickInput) {
    nickInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        createDouyinWorkbenchAccount();
      }
    });
  }
  var modal = document.getElementById('douyinCustomerCollectModal');
  if (modal && modal.dataset.douyinModalBound !== '1') {
    modal.dataset.douyinModalBound = '1';
    modal.addEventListener('click', function(e) {
      var target = e.target;
      if (target === modal || target.closest('#douyinCollectModalCloseBtn') || target.closest('#douyinCollectModalCancelBtn')) {
        closeDouyinWorkbenchCollectModal();
        return;
      }
      if (target.closest('#douyinCollectModalConfirmBtn')) {
        var idx = parseInt(_douyinWorkbenchState.pendingVideoIndex, 10);
        if (Number.isNaN(idx)) return;
        collectDouyinWorkbenchVideoCustomers(idx, _douyinWorkbenchModalPayload());
      }
    });
  }
}

window.initDouyinWorkbenchView = function() {
  var host = _ensureDouyinWorkbenchHost();
  _bindDouyinWorkbenchTabs(host);
  _bindDouyinWorkbenchActions(host);
  loadDouyinWorkbenchConfig(true);
  loadDouyinWorkbenchAccounts();
};

function _openMessengerConfigView() {
  _switchToHiddenView('messenger-config');
  if (typeof loadMessengerConfigPage === 'function') loadMessengerConfigPage();
}

function _ensureSkillStoreVisible() {
  var nav = document.querySelector('.nav-left-item[data-view="skill-store"]');
  if (nav) nav.click();
}
window._ensureSkillStoreVisible = _ensureSkillStoreVisible;

window._openYoutubeAccountsView = function() {
  _switchToHiddenView('youtube-accounts');
  if (typeof loadYoutubeAccountsPage === 'function') loadYoutubeAccountsPage();
  try { location.hash = 'youtube-accounts'; } catch (e1) {}
};

window._openEcommerceDetailStudioView = function() {
  _switchToHiddenView('ecommerce-detail-studio');
  if (typeof _bindComflyConfigBtn === 'function') _bindComflyConfigBtn();
  if (typeof window.initEcommerceDetailStudioView === 'function') window.initEcommerceDetailStudioView();
  try { location.hash = 'ecommerce-detail-studio'; } catch (e1) {}
};

window._openImageComposerStudioView = function() {
  _switchToHiddenView('image-composer-studio');
  if (typeof _bindComflyConfigBtn === 'function') _bindComflyConfigBtn();
  if (typeof window.initImageComposerStudioView === 'function') window.initImageComposerStudioView();
  try { location.hash = 'image-composer-studio'; } catch (e1) {}
};

window._openSeedanceTvcStudioView = function() {
  _switchToHiddenView('seedance-tvc-studio');
  if (typeof _bindComflyConfigBtn === 'function') _bindComflyConfigBtn();
  if (typeof window.initSeedanceTvcStudioView === 'function') window.initSeedanceTvcStudioView();
  try { location.hash = 'seedance-tvc-studio'; } catch (e1) {}
};

window._openViralVideoRemixView = function() {
  _switchToHiddenView('viral-video-remix');
  if (typeof _bindComflyConfigBtn === 'function') _bindComflyConfigBtn();
  if (typeof window.initViralVideoRemixView === 'function') window.initViralVideoRemixView();
  try { location.hash = 'viral-video-remix'; } catch (e1) {}
};

window._openHiflyDigitalHumanView = function() {
  _switchToHiddenView('hifly-digital-human');
  if (typeof window.initHiflyDigitalHumanView === 'function') window.initHiflyDigitalHumanView();
  try { location.hash = 'hifly-digital-human'; } catch (e1) {}
};

window._openShanjianDigitalHumanView = function() {
  _switchToHiddenView('shanjian-digital-human');
  if (typeof window.initShanjianDigitalHumanView === 'function') window.initShanjianDigitalHumanView();
  try { location.hash = 'shanjian-digital-human'; } catch (e1) {}
};

window._openDouyinWorkbenchView = function() {
  var host = _ensureDouyinWorkbenchHost();
  _bindDouyinWorkbenchTabs(host);
  _bindDouyinWorkbenchActions(host);
  _switchToHiddenView('douyin-workbench');
  if (typeof window.initDouyinWorkbenchView === 'function') window.initDouyinWorkbenchView();
  try { location.hash = 'douyin-workbench'; } catch (e1) {}
};

window._openShanjianSmartClipView = function() {
  _switchToHiddenView('shanjian-smart-clip');
  if (typeof window.initShanjianSmartClipView === 'function') window.initShanjianSmartClipView();
  try { location.hash = 'shanjian-smart-clip'; } catch (e1) {}
};

window._openGoalVideoChat = function() {
  var nav = document.querySelector('.nav-left-item[data-view="chat"]');
  if (nav) nav.click();
  var input = document.getElementById('chatInput');
  if (input) {
    var starter = '用创意成片，根据我的记忆，给某产品生成一个 6 秒抖音 9:16 宣传视频。';
    if (!String(input.value || '').trim()) input.value = starter;
    input.focus();
    try { input.setSelectionRange(input.value.length, input.value.length); } catch (e1) {}
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }
};

window._openCreativeFilmStudioView = function() {
  if (typeof window.registerLobsterView === 'function') {
    window.registerLobsterView('creative-film-studio', {
      html: '/static/views/creative-film-studio.html?v=20260606-creative-film-entry',
      scripts: '/static/js/creative-film-studio.js?v=20260606-creative-film-entry',
      cache: 'reload'
    });
  }
  if (typeof window.showLobsterView === 'function') {
    window.showLobsterView('creative-film-studio', document.querySelector('.nav-left-item[data-view="skill-store"]'))
      .then(function() {
        if (typeof window.initCreativeFilmStudioView === 'function') window.initCreativeFilmStudioView();
      })
      .catch(function(err) {
        console.error('Failed to open creative-film-studio', err);
        alert('创意成片页面加载失败，请刷新页面后重试。' + (err && err.message ? '\n' + err.message : ''));
      });
    return;
  }
  _switchToHiddenView('creative-film-studio');
  if (typeof window.initCreativeFilmStudioView === 'function') window.initCreativeFilmStudioView();
  try { location.hash = 'creative-film-studio'; } catch (e1) {}
};

window._openIpContentStudioView = function() {
  if (typeof window.registerLobsterView === 'function') {
    window.registerLobsterView('ip-content-studio', {
      html: '/static/views/ip-content-studio.html?v=20260615-ip-content-batch',
      scripts: '/static/js/ip-content-studio.js?v=20260615-ip-content-batch',
      cache: 'reload'
    });
  }
  if (typeof window.showLobsterView === 'function') {
    window.showLobsterView('ip-content-studio', document.querySelector('.nav-left-item[data-view="skill-store"]'))
      .then(function() {
        if (typeof window.initIpContentStudioView === 'function') window.initIpContentStudioView();
      })
      .catch(function(err) {
        console.error('Failed to open ip-content-studio', err);
        alert('IP日更文案页面加载失败，请刷新页面后重试。' + (err && err.message ? '\n' + err.message : ''));
      });
    return;
  }
  _switchToHiddenView('ip-content-studio');
  if (typeof window.initIpContentStudioView === 'function') window.initIpContentStudioView();
  try { location.hash = 'ip-content-studio'; } catch (e1) {}
};

window._openJuheWechatView = function() {
  if (typeof window.registerLobsterView === 'function') {
    window.registerLobsterView('juhe-wechat', {
      html: '/static/views/juhe-wechat.html?v=20260715-native-wechat-moments-comment',
      scripts: '/static/js/juhe-wechat.js?v=20260715-native-wechat-moments-comment',
      cache: 'reload'
    });
  }
  if (typeof window.showLobsterView === 'function') {
    window.showLobsterView('juhe-wechat', document.querySelector('.nav-left-item[data-view="skill-store"]'))
      .then(function() {
        if (typeof window.initJuheWechatView === 'function') window.initJuheWechatView();
      })
      .catch(function(err) {
        console.error('Failed to open juhe-wechat', err);
        alert('微信协议助手页面加载失败，请刷新页面后重试。' + (err && err.message ? '\n' + err.message : ''));
      });
    return;
  }
  _switchToHiddenView('juhe-wechat');
  if (typeof window.initJuheWechatView === 'function') window.initJuheWechatView();
  try { location.hash = 'juhe-wechat'; } catch (e1) {}
};

window._openLinkedinMiningView = function() {
  if (typeof window.registerLobsterView === 'function') {
    window.registerLobsterView('linkedin-mining', {
      html: '/static/views/linkedin-mining.html?v=20260616-linkedin-workbench',
      scripts: '/static/js/linkedin-mining.js?v=20260616-linkedin-workbench',
      cache: 'reload'
    });
  }
  if (typeof window.showLobsterView === 'function') {
    window.showLobsterView('linkedin-mining', document.querySelector('.nav-left-item[data-view="skill-store"]'))
      .then(function() {
        if (typeof window.initLinkedinMiningView === 'function') window.initLinkedinMiningView();
      })
      .catch(function(err) {
        console.error('Failed to open linkedin-mining', err);
        alert('LinkedIn线索挖掘页面加载失败，请刷新页面后重试。' + (err && err.message ? '\n' + err.message : ''));
      });
    return;
  }
  _switchToHiddenView('linkedin-mining');
  if (typeof window.initLinkedinMiningView === 'function') window.initLinkedinMiningView();
  try { location.hash = 'linkedin-mining'; } catch (e1) {}
};

window._openSocialLeadsView = function(platform) {
  var nextPlatform = String(platform || window.__socialLeadsPlatform || 'reddit').toLowerCase();
  if (nextPlatform === 'twitter' || nextPlatform === 'x_leads') nextPlatform = 'x';
  if (nextPlatform === 'tiktok_leads' || nextPlatform === 'tik_tok') nextPlatform = 'tiktok';
  if (['reddit', 'x', 'tiktok'].indexOf(nextPlatform) < 0) nextPlatform = 'reddit';
  window.__socialLeadsPlatform = nextPlatform;
  if (typeof window.registerLobsterView === 'function') {
    window.registerLobsterView('social-leads', {
      html: '/static/views/social-leads.html?v=20260630-social-leads-platform-isolation',
      scripts: '/static/js/social-leads.js?v=20260630-social-leads-platform-isolation',
      init: 'initSocialLeadsView',
      cache: 'reload'
    });
  }
  if (typeof window.showLobsterView === 'function') {
    window.showLobsterView('social-leads', document.querySelector('.nav-left-item[data-view="skill-store"]'))
      .then(function() {
        if (typeof window.initSocialLeadsView === 'function') window.initSocialLeadsView(nextPlatform);
      })
      .catch(function(err) {
        console.error('Failed to open social-leads', err);
        alert('线索采集页面加载失败，请刷新页面后重试。' + (err && err.message ? '\n' + err.message : ''));
      });
    return;
  }
  _switchToHiddenView('social-leads');
  if (typeof window.initSocialLeadsView === 'function') window.initSocialLeadsView(nextPlatform);
  try { location.hash = 'social-leads'; } catch (e1) {}
};

window._openGlobalLeadsView = function() {
  if (typeof window.registerLobsterView === 'function') {
    window.registerLobsterView('global-leads', {
      html: '/static/views/global-leads.html?v=20260714-global-leads-web-search',
      scripts: '/static/js/global-leads.js?v=20260714-global-leads-web-search',
      init: 'initGlobalLeadsView',
      cache: 'reload'
    });
  }
  if (typeof window.showLobsterView === 'function') {
    window.showLobsterView('global-leads', document.querySelector('.nav-left-item[data-view="skill-store"]'))
      .then(function() {
        if (typeof window.initGlobalLeadsView === 'function') window.initGlobalLeadsView();
      })
      .catch(function(err) {
        console.error('Failed to open global-leads', err);
        alert('全球客户开发工作台加载失败，请刷新页面后重试。' + (err && err.message ? '\n' + err.message : ''));
      });
    return;
  }
  _switchToHiddenView('global-leads');
  if (typeof window.initGlobalLeadsView === 'function') window.initGlobalLeadsView();
  try { location.hash = 'global-leads'; } catch (e1) {}
};

window._openAlibabaInquiriesView = function() {
  if (typeof window.registerLobsterView === 'function') {
    window.registerLobsterView('alibaba-inquiries', {
      html: '/static/views/alibaba-inquiries.html?v=20260721-alibaba-doc-collapse',
      scripts: '/static/js/alibaba-inquiries.js?v=20260722-alibaba-archive-jobs',
      init: 'initAlibabaInquiriesView',
      cache: 'reload'
    });
  }
  if (typeof window.showLobsterView === 'function') {
    window.showLobsterView('alibaba-inquiries', document.querySelector('.nav-left-item[data-view="skill-store"]'))
      .then(function() {
        if (typeof window.initAlibabaInquiriesView === 'function') window.initAlibabaInquiriesView();
      })
      .catch(function(err) {
        console.error('Failed to open alibaba-inquiries', err);
        alert('阿里询盘接管工作台加载失败，请刷新页面后重试。' + (err && err.message ? '\n' + err.message : ''));
      });
    return;
  }
  _switchToHiddenView('alibaba-inquiries');
  if (typeof window.initAlibabaInquiriesView === 'function') window.initAlibabaInquiriesView();
  try { location.hash = 'alibaba-inquiries'; } catch (e1) {}
};

window._openWechatChannelsTranscriptView = function() {
  if (typeof window.registerLobsterView === 'function') {
    window.registerLobsterView('wechat-channels-transcript', {
      html: '/static/views/wechat-channels-transcript.html?v=20260626-wct-entry-cache',
      scripts: '/static/js/wechat-channels-transcript.js?v=20260626-wct-entry-cache',
      init: 'initWechatChannelsTranscriptView',
      cache: 'reload'
    });
  }
  if (typeof window.showLobsterView === 'function') {
    window.showLobsterView('wechat-channels-transcript', document.querySelector('.nav-left-item[data-view="skill-store"]'))
      .then(function() {
        if (typeof window.initWechatChannelsTranscriptView === 'function') window.initWechatChannelsTranscriptView();
      })
      .catch(function(err) {
        console.error('Failed to open wechat-channels-transcript', err);
        alert('视频号文案提取页面加载失败，请刷新页面后重试。' + (err && err.message ? '\n' + err.message : ''));
      });
    return;
  }
  _switchToHiddenView('wechat-channels-transcript');
  if (typeof window.initWechatChannelsTranscriptView === 'function') window.initWechatChannelsTranscriptView();
  try { location.hash = 'wechat-channels-transcript'; } catch (e1) {}
};

window._openAi3dModelView = function() {
  if (typeof window.registerLobsterView === 'function') {
    window.registerLobsterView('ai-3d-model', {
      html: '/static/views/ai-3d-model.html?v=20260709-component-split-v1',
      scripts: '/static/js/ai-3d-model.js?v=20260709-component-split-v1',
      init: 'initAi3dModelView',
      cache: 'reload'
    });
  }
  if (typeof window.showLobsterView === 'function') {
    window.showLobsterView('ai-3d-model', document.querySelector('.nav-left-item[data-view="skill-store"]'))
      .then(function() {
        if (typeof window.initAi3dModelView === 'function') window.initAi3dModelView();
      })
      .catch(function(err) {
        console.error('Failed to open ai-3d-model', err);
        alert('\u9ad8\u8d28\u91cf 3D \u6a21\u578b\u9875\u9762\u52a0\u8f7d\u5931\u8d25\uff0c\u8bf7\u5237\u65b0\u9875\u9762\u540e\u91cd\u8bd5\u3002' + (err && err.message ? '\n' + err.message : ''));
      });
    return;
  }
  _switchToHiddenView('ai-3d-model');
  if (typeof window.initAi3dModelView === 'function') window.initAi3dModelView();
  try { location.hash = 'ai-3d-model'; } catch (e1) {}
};

window._openHiddenWorkspaceView = function(view) {
  var target = String(view || '').trim();
  if (!target) return;
  if (typeof window.lobsterFeatureGateForView === 'function') {
    var gate = window.lobsterFeatureGateForView(target);
    if (gate) {
      if (typeof window.isLobsterFeatureGateAllowed === 'function' && !window.isLobsterFeatureGateAllowed(gate)) return;
      if (typeof window.isLobsterFeatureGateAllowed !== 'function' && typeof window.isLobsterFeatureAllowed === 'function' && !window.isLobsterFeatureAllowed(gate)) return;
    }
  }
  if (target === 'hifly-digital-human' && typeof window._openHiflyDigitalHumanView === 'function') {
    window._openHiflyDigitalHumanView();
    return;
  }
  if (target === 'shanjian-digital-human' && typeof window._openShanjianDigitalHumanView === 'function') {
    window._openShanjianDigitalHumanView();
    return;
  }
  if (target === 'douyin-workbench' && typeof window._openDouyinWorkbenchView === 'function') {
    window._openDouyinWorkbenchView();
    return;
  }
  if (target === 'shanjian-smart-clip' && typeof window._openShanjianSmartClipView === 'function') {
    window._openShanjianSmartClipView();
    return;
  }
  if (target === 'viral-video-remix' && typeof window._openViralVideoRemixView === 'function') {
    window._openViralVideoRemixView();
    return;
  }
  if (target === 'seedance-tvc-studio' && typeof window._openSeedanceTvcStudioView === 'function') {
    window._openSeedanceTvcStudioView();
    return;
  }
  if (target === 'image-composer-studio' && typeof window._openImageComposerStudioView === 'function') {
    window._openImageComposerStudioView();
    return;
  }
  if (target === 'local-bestseller' && typeof window.initLocalBestsellerView === 'function') {
    _switchToHiddenView('local-bestseller');
    window.initLocalBestsellerView();
    try { location.hash = 'local-bestseller'; } catch (e1) {}
    return;
  }
  if (target === 'ecommerce-detail-studio' && typeof window._openEcommerceDetailStudioView === 'function') {
    window._openEcommerceDetailStudioView();
    return;
  }
  if (target === 'youtube-accounts' && typeof window._openYoutubeAccountsView === 'function') {
    window._openYoutubeAccountsView();
    return;
  }
  if (target === 'messenger-config' && typeof _openMessengerConfigView === 'function') {
    _openMessengerConfigView();
    return;
  }
  if (target === 'twilio-whatsapp-config' && typeof _openTwilioWhatsappConfigView === 'function') {
    _openTwilioWhatsappConfigView();
    return;
  }
  if (target === 'openclaw-skill-chat' && typeof window.openOpenclawSkillChat === 'function') {
    window.openOpenclawSkillChat();
    return;
  }
  if (target === 'creative-film-studio' && typeof window._openCreativeFilmStudioView === 'function') {
    window._openCreativeFilmStudioView();
    return;
  }
  if (target === 'ip-content-studio' && typeof window._openIpContentStudioView === 'function') {
    window._openIpContentStudioView();
    return;
  }
  if (target === 'juhe-wechat' && typeof window._openJuheWechatView === 'function') {
    window._openJuheWechatView();
    return;
  }
  if (target === 'wechat-channels-transcript' && typeof window._openWechatChannelsTranscriptView === 'function') {
    window._openWechatChannelsTranscriptView();
    return;
  }
  if (target === 'alibaba-inquiries' && typeof window._openAlibabaInquiriesView === 'function') {
    window._openAlibabaInquiriesView();
    return;
  }
  if (target === 'linkedin-mining' && typeof window._openLinkedinMiningView === 'function') {
    window._openLinkedinMiningView();
    return;
  }
  if ((target === 'social-leads' || target === 'reddit-leads' || target === 'x-leads' || target === 'tiktok-leads') && typeof window._openSocialLeadsView === 'function') {
    var socialPlatform = target === 'x-leads' ? 'x' : (target === 'tiktok-leads' ? 'tiktok' : (target === 'reddit-leads' ? 'reddit' : window.__socialLeadsPlatform));
    window._openSocialLeadsView(socialPlatform);
    return;
  }
  if (target === 'meta-social' && typeof window._openMetaSocialView === 'function') {
    window._openMetaSocialView();
  }
};

function _openTwilioWhatsappConfigView() {
  _ensureSkillStoreVisible();
  var modal = document.getElementById('twilioWhatsappConfigModal');
  if (modal) modal.classList.add('visible');
  if (typeof loadTwilioWhatsappConfigPage === 'function') loadTwilioWhatsappConfigPage();
  try { location.hash = 'twilio-whatsapp-config'; } catch (e1) {}
}

function _openTwilioWhatsappDetailView() {
  if (typeof showTwilioWhatsappDetailView === 'function') showTwilioWhatsappDetailView();
}

function _renderYoutubePublishCard(opts) {
  opts = opts || {};
  var pkg = opts.pkg || {};
  var showDebug = !!opts.showDebug;
  var debugBadge = showDebug
    ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">调试</span> '
    : '';
  var title = _skillStoreBrandSafeText((pkg.name && String(pkg.name).trim()) || 'YouTube 上传');
  var desc = _skillStoreBrandSafeText((pkg.description && String(pkg.description).trim()) ||
    '多账号管理：每个账号独立 OAuth 与代理；授权成功后即可在对话中指定素材与 YouTube 账号 ID。');
  var configured = _youtubePublishStatus.has_ready;
  var cnt = _youtubePublishStatus.accounts_count || 0;
  var statusBadge = configured
    ? '<span class="badge-installed">已有可用账号</span>'
    : (cnt > 0
      ? '<span class="badge-coming" style="background:rgba(251,146,60,0.15);color:#fb923c;border-color:rgba(251,146,60,0.3);">待完成授权</span>'
      : '<span class="badge-coming" style="background:rgba(251,146,60,0.15);color:#fb923c;border-color:rgba(251,146,60,0.3);">未添加</span>');
  var hint = configured ? '' :
    '<div style="margin-top:0.45rem;font-size:0.78rem;color:var(--text-muted);">点进列表添加账号；对话里用「账号 ID」（yt_ 开头）指定发到哪个 YouTube。</div>';
  return '<div class="skill-store-card youtube-publish-card" style="cursor:pointer;border-color:rgba(255,0,0,0.22);background:linear-gradient(135deg,rgba(255,0,0,0.06),transparent);">' +
    '<div class="card-label">' + debugBadge + '发布 <span class="badge-installed">可配置</span> ' + statusBadge + '</div>' +
    '<div class="card-value">' + escapeHtml(title) + '</div>' +
    '<div class="card-desc">' + escapeHtml(desc) + '</div>' +
    hint +
    '<div class="card-tags"><span class="tag">YouTube</span><span class="tag">OAuth</span></div>' +
    '<div class="card-actions" style="display:flex;flex-wrap:wrap;gap:0.35rem;">' +
    '<button type="button" class="btn btn-primary btn-sm youtube-publish-entry-btn">管理账号</button></div></div>';
}

function _renderMetaSocialCard(opts) {
  opts = opts || {};
  var cnt = (typeof _metaSocialStatus !== 'undefined') ? (_metaSocialStatus.accounts_count || 0) : 0;
  var statusBadge = cnt > 0
    ? '<span class="badge-installed">已连接 ' + cnt + ' 个</span>'
    : '<span class="badge-coming" style="background:rgba(251,146,60,0.15);color:#fb923c;border-color:rgba(251,146,60,0.3);">未连接</span>';
  return '<div class="skill-store-card meta-social-card" style="cursor:pointer;border-color:rgba(225,48,108,0.35);background:linear-gradient(135deg,rgba(24,119,242,0.06),rgba(225,48,108,0.06));">' +
    '<div class="card-label">发布 <span class="badge-installed">可配置</span> ' + statusBadge + '</div>' +
    '<div class="card-value">Instagram / Facebook</div>' +
    '<div class="card-desc">通过 Facebook OAuth 授权连接 IG Business 或 FB 主页；对话中可直接发布 photo / video / reel / story / carousel，也可拉取粉丝数据与互动指标。</div>' +
    '<div class="card-tags"><span class="tag">Instagram</span><span class="tag">Facebook</span><span class="tag">OAuth</span></div>' +
    '<div class="card-actions" style="display:flex;flex-wrap:wrap;gap:0.35rem;">' +
    '<button type="button" class="btn btn-primary btn-sm meta-social-entry-btn">管理账号</button></div></div>';
}

function _bindMetaSocialCardEntry() {
  document.querySelectorAll('.meta-social-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openMetaSocialView === 'function') window._openMetaSocialView();
    });
  });
  document.querySelectorAll('.meta-social-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openMetaSocialView === 'function') window._openMetaSocialView();
    });
  });
}

function _renderTwilioWhatsappCard(opts) {
  opts = opts || {};
  var pkg = opts.pkg || {};
  var showDebug = !!opts.showDebug;
  var debugBadge = showDebug
    ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">调试</span> '
    : '';
  var title = _skillStoreBrandSafeText((pkg.name && String(pkg.name).trim()) || 'Twilio WhatsApp');
  var desc = _skillStoreBrandSafeText((pkg.description && String(pkg.description).trim()) ||
    '云端入站 + 本机轮询 AI 回复；点卡片查看消息与公司列表，点「配置」填写 Twilio');
  return '<div class="skill-store-card twilio-whatsapp-card" style="cursor:pointer;border-color:rgba(37,211,102,0.45);background:linear-gradient(135deg,rgba(37,211,102,0.08),transparent);">' +
    '<div class="card-label">' + debugBadge + '通道 <span class="badge-installed">可配置</span></div>' +
    '<div class="card-value">' + escapeHtml(title) + '</div>' +
    '<div class="card-desc">' + escapeHtml(desc) + '</div>' +
    '<div class="card-tags"><span class="tag">WhatsApp</span><span class="tag">Twilio</span></div>' +
    '<div class="card-actions" style="display:flex;flex-wrap:wrap;gap:0.35rem;">' +
    '<button type="button" class="btn btn-primary btn-sm twilio-whatsapp-entry-btn">配置</button></div></div>';
}

function _renderXSkillCard() {
  var configured = _xskillStatus.has_token;
  var statusBadge = configured
    ? '<span class="badge-installed">已配置</span>'
    : '<span class="badge-coming" style="background:rgba(251,146,60,0.15);color:#fb923c;border-color:rgba(251,146,60,0.3);">未配置</span>';
  var guide = configured ? '' :
    '<div style="margin-top:0.6rem;padding:0.6rem 0.75rem;background:rgba(251,146,60,0.06);border:1px solid rgba(251,146,60,0.18);border-radius:8px;font-size:0.8rem;color:var(--text-muted);line-height:1.6;">' +
      '<div style="font-weight:600;color:#fb923c;margin-bottom:0.3rem;">获取 Token 步骤：</div>' +
      '<div>1. 打开平台入口，微信扫码 或 手机号登录</div>' +
      '<div>2. 登录后进入个人中心复制 API Token</div>' +
      '<div>3. 回到这里点击「配置 Token」粘贴即可</div>' +
    '</div>';
  var configBtn = (EDITION === 'online')
    ? '<span class="btn btn-ghost btn-sm" style="cursor:default;color:var(--text-muted);">已安装</span>'
    : '<button type="button" class="btn btn-primary btn-sm" id="xskillConfigBtn">' + (configured ? '修改 Token' : '配置 Token') + '</button>';
  if (EDITION === 'online') guide = '';
  return '<div class="skill-store-card" data-skill-package-id="sutui_mcp" style="border-color:rgba(6,182,212,0.25);background:linear-gradient(135deg,rgba(6,182,212,0.06),transparent);">' +
    '<div class="card-label">MCP · 内置 ' + statusBadge + '</div>' +
    '<div class="card-value">AI 模型能力</div>' +
    '<div class="card-desc">图片生成、视频生成、视频解析、语音合成、音色克隆等 50+ AI 模型能力</div>' +
    '<div class="card-tags"><span class="tag">图片</span><span class="tag">视频</span><span class="tag">音频</span><span class="tag">AI创作</span></div>' +
    guide +
    '<div class="card-actions">' +
      configBtn +
      '<button type="button" class="btn btn-ghost btn-sm" id="xskillModelsBtn">模型能力与定价</button>' +
    '</div></div>';
}

function _loadXSkillStatus(cb) {
  fetch((LOCAL_API_BASE || '') + '/api/sutui/config', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      _xskillStatus = { has_token: !!d.has_token, token: d.token || '', url: d.url || '' };
      if (cb) cb();
    })
    .catch(function() { if (cb) cb(); });
}

function _loadComflyStatus(cb) {
  fetch((LOCAL_API_BASE || '') + '/api/comfly/config', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      _comflyStatus = {
        effective_ready: !!d.effective_ready,
        has_user_key: !!d.has_user_key,
        masked_user_key: d.masked_user_key || '',
        user_api_base: d.user_api_base || '',
        default_api_base_hint: d.default_api_base_hint || 'https://ai.comfly.org',
      };
      if (cb) cb();
    })
    .catch(function() { if (cb) cb(); });
}

function _renderComflyCard() {
  var ok = _comflyStatus.effective_ready;
  var statusBadge = ok
    ? '<span class="badge-installed">已就绪</span>'
    : '<span class="badge-coming" style="background:rgba(251,146,60,0.15);color:#fb923c;border-color:rgba(251,146,60,0.3);">待配置</span>';
  var sub = ok
    ? ''
    : '<div style="margin-top:0.55rem;padding:0.55rem 0.7rem;background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.2);border-radius:8px;font-size:0.78rem;color:var(--text-muted);line-height:1.55;">'
      + '只需在视频引擎控制台复制 <strong>API Key</strong> 即可，接口地址走系统内置配置，无需用户单独填写。'
      + '用户对话可说「用<strong>爆款TVC</strong>和这个素材做视频」；技能会自动跑分镜、多段成片与入库，无需在卡片里配分镜参数。'
      + ' \u70b9\u51fb\u4e0b\u65b9\u300c\u914d\u7f6e\u300d\u4ec5\u4fdd\u5b58\u5230\u672c\u673a\uff0c\u4e0d\u4f1a\u5199\u5165\u804a\u5929\u8bb0\u5f55\uff0c\u4e5f\u4e0d\u4f1a\u5728\u4fdd\u5b58\u65f6\u8bf7\u6c42\u4e0a\u6e38\u6821\u9a8c\u51ed\u636e\u3002</div>';
  return '<div class="skill-store-card comfly-veo-card" data-skill-package-id="comfly_veo_skill" style="border-color:rgba(245,158,11,0.38);background:linear-gradient(135deg,rgba(245,158,11,0.07),transparent);">' +
    '<div class="card-label">生成 · 内置 ' + statusBadge + '</div>' +
    '<div class="card-value">爆款TVC</div>' +
    '<div class="card-desc">整包成片走 <code>comfly.daihuo.pipeline</code>（start_pipeline + 素材）；单段调试可走 <code>comfly.daihuo</code>。</div>' +
    sub +
    '<div class="card-tags"><span class="tag">爆款TVC</span><span class="tag">TVC</span><span class="tag">视频生成</span><span class="tag">AI成片</span></div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm" id="comflyConfigBtn">配置</button></div></div>';
}

function _renderEcommerceDetailCard(opts) {
  opts = opts || {};
  var pkg = opts.pkg || {};
  var ok = _comflyStatus.effective_ready;
  var rawTitle = (pkg.name && String(pkg.name).trim()) || '';
  var rawDesc = (pkg.description && String(pkg.description).trim()) || '';
  var title = _skillStoreBrandSafeText(rawTitle && !/^\?+$/.test(rawTitle) ? rawTitle : '电商上架套图');
  var desc = _skillStoreBrandSafeText(rawDesc && !/^\?+$/.test(rawDesc) ? rawDesc :
    '把商品图、卖点、风格和模板组织成一次完整的上架视觉资产生产流程，覆盖主图、SKU 图、透明/白底、详情图、素材图与橱窗图。');
  var statusBadge = ok
    ? '<span class="badge-installed">已就绪</span>'
    : '<span class="badge-coming" style="background:rgba(251,146,60,0.15);color:#fb923c;border-color:rgba(251,146,60,0.3);">待配置</span>';
  var sub = ok
    ? '<div style="margin-top:0.45rem;font-size:0.78rem;color:var(--text-muted);">直接进入工作台，按结构化参数控制本次套图生成内容。</div>'
    : '<div style="margin-top:0.55rem;padding:0.55rem 0.7rem;background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.2);border-radius:8px;font-size:0.78rem;color:var(--text-muted);line-height:1.55;">\u5148\u5728\u672c\u673a\u4fdd\u5b58\u89c6\u9891\u5f15\u64ce API Key\uff0c\u63a5\u53e3\u5730\u5740\u8d70\u5185\u7f6e\u914d\u7f6e\uff0c\u7136\u540e\u518d\u8fdb\u5165\u4ea7\u54c1\u5957\u56fe\u5de5\u4f5c\u53f0\u3002\u8fd9\u4e2a\u754c\u9762\u662f\u4e13\u95e8\u7528\u4e8e\u6279\u91cf\u751f\u6210\u7535\u5546\u4e0a\u67b6\u7d20\u6750\u7684\uff0c\u4e0d\u662f\u804a\u5929\u5165\u53e3\u3002</div>';
  return '<div class="skill-store-card ecommerce-detail-card" data-skill-package-id="comfly_ecommerce_detail_skill" style="cursor:pointer;border-color:rgba(236,72,153,0.34);background:linear-gradient(135deg,rgba(236,72,153,0.08),rgba(245,158,11,0.05));">' +
    '<div class="card-label">生成 · 内置 ' + statusBadge + '</div>' +
    '<div class="card-value">' + escapeHtml(title) + '</div>' +
    '<div class="card-desc">' + escapeHtml(desc) + '</div>' +
    sub +
    '<div class="card-tags"><span class="tag">上架套图</span><span class="tag">SKU</span><span class="tag">详情图</span><span class="tag">商品视觉</span></div>' +
    '<div class="card-actions" style="display:flex;flex-wrap:wrap;gap:0.35rem;">' +
      '<button type="button" class="btn btn-primary btn-sm ecommerce-detail-entry-btn">进入工作台</button>' +
      '<button type="button" class="btn btn-ghost btn-sm js-comfly-config-btn">配置引擎</button>' +
    '</div></div>';
}

function _renderSeedanceTvcStudioCard() {
  var ok = _comflyStatus.effective_ready;
  var statusBadge = ok
    ? '<span class="badge-installed">已就绪</span>'
    : '<span class="badge-coming" style="background:rgba(251,146,60,0.15);color:#fb923c;border-color:rgba(251,146,60,0.3);">待配置</span>';
  var sub = ok
    ? '<div style="margin-top:0.45rem;font-size:0.78rem;color:var(--text-muted);">进入工作台后可先切换输入方式，再组织参考图、参考视频、提示词、时长和分镜节奏。</div>'
    : '<div style="margin-top:0.55rem;padding:0.55rem 0.7rem;background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.2);border-radius:8px;font-size:0.78rem;color:var(--text-muted);line-height:1.55;">\u5148\u5728\u672c\u673a\u4fdd\u5b58\u89c6\u9891\u5f15\u64ce API Key\uff0c\u518d\u8fdb\u5165\u8fd9\u4e2a\u89c6\u9891\u5de5\u4f5c\u53f0\u3002\u5f53\u524d\u5148\u63d0\u4f9b\u7ed3\u6784\u5316 UI\uff0c\u65b9\u4fbf\u628a\u56fe\u7247\u53c2\u8003\u3001\u53c2\u8003\u89c6\u9891\u3001\u81ea\u52a8\u5206\u955c\u548c\u624b\u52a8\u63d0\u793a\u8bcd\u653e\u5728\u4e00\u4e2a\u754c\u9762\u91cc\u3002</div>';
  return '<div class="skill-store-card seedance-tvc-card" data-skill-package-id="seedance_tvc_studio" style="cursor:pointer;border-color:rgba(91,124,255,0.28);background:linear-gradient(135deg,rgba(91,124,255,0.09),rgba(14,165,233,0.05));">' +
    '<div class="card-label">生成 · 内置 ' + statusBadge + '</div>' +
    '<div class="card-value">创意分镜头视频</div>' +
    '<div class="card-desc">面向参考图、参考视频和纯提示词的统一视频创作界面。左侧管参数与输入方式，右侧同时看创意分镜预览和最终成片位。</div>' +
    sub +
    '<div class="card-tags"><span class="tag">TVC</span><span class="tag">智能视频模型</span><span class="tag">分镜</span><span class="tag">AI成片</span></div>' +
    '<div class="card-actions" style="display:flex;flex-wrap:wrap;gap:0.35rem;">' +
      '<button type="button" class="btn btn-primary btn-sm seedance-tvc-entry-btn">进入工作台</button>' +
      '<button type="button" class="btn btn-ghost btn-sm js-comfly-config-btn">配置引擎</button>' +
    '</div></div>';
}

function _renderViralVideoRemixCard() {
  var statusBadge = '<span class="badge-installed">平台计费</span>';
  var sub = '<div style="margin-top:0.45rem;font-size:0.78rem;color:var(--text-muted);">已接入平台统一算力计费，提交前会按原视频时长预估并弹窗确认，无需单独配置上游引擎。</div>';

  return '<div class="skill-store-card viral-video-remix-card" data-skill-package-id="viral_video_remix_skill" style="cursor:pointer;border-color:rgba(20,184,166,0.34);background:linear-gradient(135deg,rgba(20,184,166,0.08),rgba(245,158,11,0.05));">' +
    '<div class="card-label">生成 · 内测 ' + statusBadge + '</div>' +
    '<div class="card-value">爆款视频复刻</div>' +
    '<div class="card-desc">上传原爆款视频、人物四视图和产品图，用智能视频模型复刻动作、运镜和节奏。</div>' +
    sub +
    '<div class="card-tags"><span class="tag">复刻</span><span class="tag">智能视频模型</span><span class="tag">人物四视图</span><span class="tag">产品替换</span></div>' +
    '<div class="card-actions" style="display:flex;flex-wrap:wrap;gap:0.35rem;">' +
      '<button type="button" class="btn btn-primary btn-sm viral-video-remix-entry-btn">进入工作台</button>' +
    '</div></div>';
}

function _renderHiflyDigitalHumanCard(pkg) {
  pkg = pkg || {};
  var title = escapeHtml(_skillStoreBrandSafeText(pkg.name || '必火数字人'));
  var desc = escapeHtml(_skillStoreBrandSafeText(pkg.description || '选择数字人和声音，输入口播文案后生成必火数字人视频。'));
  var rawTags = Array.isArray(pkg.tags) && pkg.tags.length ? pkg.tags : ['数字人', '口播', 'TTS', '必火'];
  var tags = _skillStoreTagHtml(rawTags);
  return '<div class="skill-store-card hifly-digital-human-card" data-skill-package-id="hifly_digital_human_skill" style="cursor:pointer;border-color:rgba(14,165,233,0.35);background:linear-gradient(135deg,rgba(14,165,233,0.09),rgba(20,184,166,0.06));">' +
    '<div class="card-label">数字人 &middot; 必火 <span class="badge-installed">&#26032;&#25509;&#20837;</span></div>' +
    '<div class="card-value">' + title + '</div>' +
    '<div class="card-desc">' + desc + '</div>' +
    '<div style="margin-top:0.55rem;padding:0.55rem 0.7rem;background:rgba(14,165,233,0.06);border:1px solid rgba(14,165,233,0.18);border-radius:8px;font-size:0.78rem;color:var(--text-muted);line-height:1.55;">创作服务由平台统一托管，用户无需填写 API Token。</div>' +
    '<div class="card-tags">' + tags + '</div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm hifly-digital-human-entry-btn">&#36827;&#20837;&#24037;&#20316;&#21488;</button></div>' +
    '</div>';
}

function _renderShanjianSmartClipCard() {
  return '<div class="skill-store-card shanjian-smart-clip-card" data-skill-package-id="shanjian_smart_clip" style="cursor:pointer;border-color:rgba(37,99,235,0.35);background:linear-gradient(135deg,rgba(37,99,235,0.08),rgba(20,184,166,0.05));">' +
    '<div class="card-label">\u89c6\u9891\u5408\u6210 &middot; \u5fc5\u706b <span class="badge-installed">\u65b0\u9875\u9762</span></div>' +
    '<div class="card-value">智能剪辑</div>' +
    '<div class="card-desc">拉取内置模板、公共数字人和公共声音，选择模板后提交数字人口播混剪任务。</div>' +
    '<div style="margin-top:0.55rem;padding:0.55rem 0.7rem;background:rgba(37,99,235,0.06);border:1px solid rgba(37,99,235,0.18);border-radius:8px;font-size:0.78rem;color:var(--text-muted);line-height:1.55;">数字人口播混剪约 1 算力/秒，1 分钟约 60 算力。</div>' +
    '<div class="card-tags"><span class="tag">智能剪辑</span><span class="tag">模板</span><span class="tag">数字人</span><span class="tag">必火</span></div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm shanjian-smart-clip-entry-btn">进入智能剪辑</button></div>' +
  '</div>';
}

function _renderCutcliTemplateCard() {
  return '<div class="skill-store-card cutcli-template-card" style="cursor:pointer;border-color:rgba(15,23,42,0.32);background:linear-gradient(135deg,rgba(15,23,42,0.08),rgba(224,176,92,0.08));">' +
    '<div class="card-label">视频包装 &middot; 必火 <span class="badge-installed">模板</span></div>' +
    '<div class="card-value">模板定制</div>' +
    '<div class="card-desc">选择模板预览样片，上传视频或填写素材 ID，由服务端保留原片比例和时长生成同款。</div>' +
    '<div class="card-tags"><span class="tag">模板库</span><span class="tag">生成记录</span><span class="tag">视频入库</span></div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm cutcli-template-entry-btn">进入模板</button></div>' +
  '</div>';
}

function _renderIpContentStudioCard(pkg, showDebug) {
  pkg = pkg || {};
  var debugBadge = showDebug
    ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">调试</span> '
    : '';
  var tags = _skillStoreTagHtml(pkg.tags || ['TikHub', '抖音', '视频号']);
  var cap = pkg.capabilities_count ? ' · ' + pkg.capabilities_count + ' 个能力' : '';
  return '<div class="skill-store-card ip-content-studio-card" data-skill-package-id="' + escapeAttr(pkg.id || 'ip_content_daily_skill') + '" style="cursor:pointer;border-color:rgba(20,184,166,0.35);background:linear-gradient(135deg,rgba(20,184,166,0.08),rgba(59,130,246,0.05));">' +
    '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' <span class="badge-installed">可用</span></div>' +
    '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || 'IP日更文案')) + '</div>' +
    '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '榜单、同行和记忆资料生成可审核文案。')) + cap + '</div>' +
    '<div class="card-tags">' + tags + '</div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm ip-content-studio-entry-btn">进入工作台</button></div>' +
  '</div>';
}

function _renderLinkedinMiningCard(pkg, showDebug) {
  pkg = pkg || {};
  var debugBadge = showDebug
    ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">调试</span> '
    : '';
  var tags = _skillStoreTagHtml(pkg.tags || ['TikHub', 'LinkedIn', 'B2B']);
  var cap = pkg.capabilities_count ? ' · ' + pkg.capabilities_count + ' 个能力' : '';
  return '<div class="skill-store-card linkedin-mining-card" data-skill-package-id="' + escapeAttr(pkg.id || 'linkedin_mining_skill') + '" style="cursor:pointer;border-color:rgba(10,102,194,0.36);background:linear-gradient(135deg,rgba(10,102,194,0.09),rgba(15,118,110,0.05));">' +
    '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' <span class="badge-installed">可用</span></div>' +
    '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || 'LinkedIn线索挖掘')) + '</div>' +
    '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '输入LinkedIn主页、公司、关键词或话题，自动同步数据并生成线索分析报告。')) + cap + '</div>' +
    '<div class="card-tags">' + tags + '</div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm linkedin-mining-entry-btn">进入工作台</button></div>' +
  '</div>';
}

function _renderSocialLeadsCard(pkg, platform, showDebug) {
  pkg = pkg || {};
  platform = platform === 'x' ? 'x' : (platform === 'tiktok' ? 'tiktok' : 'reddit');
  var isX = platform === 'x';
  var isTikTok = platform === 'tiktok';
  var debugBadge = showDebug
    ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">调试</span> '
    : '';
  var fallbackName = isTikTok ? 'TikTok线索采集' : (isX ? 'X线索采集' : 'Reddit线索采集');
  var fallbackDesc = isTikTok ? '采集TikTok公开视频、账号和评论数据；按关键词方向筛选精准用户。' : (isX ? '采集X公开搜索、趋势、账号和评论数据；不执行评论、发布或私信。' : '采集Reddit公开帖子、社区、账号和评论数据；不执行评论、发布或私信。');
  var fallbackTags = isTikTok ? ['TikHub', 'TikTok', '采集'] : (isX ? ['TikHub', 'X', '采集'] : ['TikHub', 'Reddit', '采集']);
  var tags = _skillStoreTagHtml(pkg.tags || fallbackTags);
  var cap = pkg.capabilities_count ? ' · ' + pkg.capabilities_count + ' 个能力' : '';
  var border = isTikTok ? 'rgba(20,20,20,0.36)' : (isX ? 'rgba(15,23,42,0.36)' : 'rgba(234,88,12,0.36)');
  var bg = isTikTok ? 'linear-gradient(135deg,rgba(15,23,42,0.08),rgba(236,72,153,0.06))' : (isX ? 'linear-gradient(135deg,rgba(15,23,42,0.08),rgba(37,99,235,0.06))' : 'linear-gradient(135deg,rgba(234,88,12,0.08),rgba(20,184,166,0.05))');
  return '<div class="skill-store-card social-leads-card ' + (isTikTok ? 'tiktok-leads-card' : (isX ? 'x-leads-card' : 'reddit-leads-card')) + '" data-social-leads-platform="' + escapeAttr(platform) + '" data-skill-package-id="' + escapeAttr(pkg.id || (isTikTok ? 'tiktok_leads' : (isX ? 'x_leads' : 'reddit_leads'))) + '" style="cursor:pointer;border-color:' + border + ';background:' + bg + ';">' +
    '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' <span class="badge-installed">可用</span></div>' +
    '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || fallbackName)) + '</div>' +
    '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || fallbackDesc)) + cap + '</div>' +
    '<div class="card-tags">' + tags + '</div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm social-leads-entry-btn" data-social-leads-platform="' + escapeAttr(platform) + '">进入工作台</button></div>' +
  '</div>';
}

function _renderGlobalLeadsCard(pkg, showDebug) {
  pkg = pkg || {};
  var debugBadge = showDebug
    ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">调试</span> '
    : '';
  var tags = _skillStoreTagHtml(pkg.tags || ['外贸获客', 'LinkedIn', 'X', 'TikTok', 'Reddit']);
  var cap = pkg.capabilities_count ? ' · ' + pkg.capabilities_count + ' 个能力' : '';
  return '<div class="skill-store-card global-leads-card" data-skill-package-id="' + escapeAttr(pkg.id || 'global_trade_leads_skill') + '" style="cursor:pointer;border-color:rgba(20,99,255,0.34);background:linear-gradient(135deg,rgba(20,99,255,0.09),rgba(9,169,184,0.06),rgba(249,115,22,0.05));">' +
    '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' <span class="badge-installed">可用</span></div>' +
    '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || '全球客户开发')) + '</div>' +
    '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '搜客、模板、周期更新和线索明细。')) + cap + '</div>' +
    '<div class="card-tags">' + tags + '</div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm global-leads-entry-btn">进入工作台</button></div>' +
  '</div>';
}

function _renderAlibabaInquiriesCard(pkg, showDebug) {
  pkg = pkg || {};
  var debugBadge = showDebug
    ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">调试</span> '
    : '';
  var tags = _skillStoreTagHtml(pkg.tags || ['阿里国际站', '询盘', '客户档案', 'AI话术']);
  var cap = pkg.capabilities_count ? ' · ' + pkg.capabilities_count + ' 个能力' : '';
  return '<div class="skill-store-card alibaba-inquiries-card" data-skill-package-id="' + escapeAttr(pkg.id || 'alibaba_inquiry_takeover_skill') + '" style="cursor:pointer;border-color:rgba(249,115,22,0.36);background:linear-gradient(135deg,rgba(249,115,22,0.10),rgba(20,86,255,0.06),rgba(6,182,212,0.05));">' +
    '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' <span class="badge-installed">可用</span></div>' +
    '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || '阿里询盘接管')) + '</div>' +
    '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '接管阿里国际站询盘：账号登录、滚动同步历史询盘、客户档案、AI话术总结和回复辅助。')) + cap + '</div>' +
    '<div class="card-tags">' + tags + '</div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm alibaba-inquiries-entry-btn">进入工作台</button></div>' +
  '</div>';
}

function _renderJuheWechatCard(pkg, showDebug) {
  pkg = pkg || {};
  var debugBadge = showDebug
    ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">调试</span> '
    : '';
  var tags = _skillStoreTagHtml(pkg.tags || ['微信协议', '自研连接', '本机']);
  var cap = pkg.capabilities_count ? ' · ' + pkg.capabilities_count + ' 个能力' : '';
  return '<div class="skill-store-card juhe-wechat-card" data-skill-package-id="' + escapeAttr(pkg.id || 'juhe_wechat_skill') + '" style="cursor:pointer;border-color:rgba(34,197,94,0.34);background:linear-gradient(135deg,rgba(34,197,94,0.08),rgba(20,184,166,0.05));">' +
    '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' <span class="badge-installed">可用</span></div>' +
    '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || '微信协议助手')) + '</div>' +
    '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '使用后台已绑定的微信实例执行检测、消息发送和调用记录查看。')) + cap + '</div>' +
    '<div class="card-tags">' + tags + '</div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm juhe-wechat-entry-btn">进入工作台</button></div>' +
  '</div>';
}

function _renderWechatChannelsTranscriptCard(pkg, showDebug) {
  pkg = pkg || {};
  var debugBadge = showDebug
    ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">调试</span> '
    : '';
  var tags = _skillStoreTagHtml(pkg.tags || ['视频号', 'TikHub', '文案提取']);
  return '<div class="skill-store-card wechat-channels-transcript-card" data-skill-package-id="' + escapeAttr(pkg.id || 'wechat_channels_transcript_skill') + '" style="cursor:pointer;border-color:rgba(14,165,233,0.34);background:linear-gradient(135deg,rgba(14,165,233,0.08),rgba(20,184,166,0.05));">' +
    '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' <span class="badge-installed">可用</span></div>' +
    '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || '视频号文案提取')) + '</div>' +
    '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '查询视频号账号作品，批量下载视频并提取口播文案，可复制和导出。')) + '</div>' +
    '<div class="card-tags">' + tags + '</div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm wechat-channels-transcript-entry-btn">进入工作台</button></div>' +
  '</div>';
}

function _renderAi3dModelCard(pkg, showDebug) {
  pkg = pkg || {};
  var debugBadge = showDebug
    ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">\u8c03\u8bd5</span> '
    : '';
  var tags = _skillStoreTagHtml(pkg.tags || ['3D', 'Meshy', '\u591a\u89c6\u89d2', '\u62c6\u4ef6']);
  var title = escapeHtml(_skillStoreBrandSafeText(pkg.name || '\u9ad8\u8d28\u91cf 3D \u6a21\u578b'));
  var desc = escapeHtml(_skillStoreBrandSafeText(pkg.description || '\u6309\u4efb\u52a1\u7c7b\u578b\u4e0a\u4f20\u53c2\u8003\u56fe\u3001\u5b9e\u7269\u591a\u89d2\u5ea6\u56fe\u6216\u5df2\u6709\u591a\u89c6\u89d2\u56fe\uff0c\u81ea\u52a8\u8c03\u7528 Meshy \u751f\u6210 GLB/FBX/OBJ/USDZ\uff0c\u652f\u6301\u8d34\u56fe\u3001PBR \u548c\u91cd\u62d3\u6251\u3002'));
  return '<div class="skill-store-card ai3d-model-card" data-skill-package-id="' + escapeAttr(pkg.id || 'ai_3d_model_skill') + '" style="cursor:pointer;border-color:rgba(34,197,94,0.34);background:linear-gradient(135deg,rgba(34,197,94,0.08),rgba(14,165,233,0.06));">' +
    '<div class="card-label">' + debugBadge + 'AI 3D <span class="badge-installed">\u53ef\u7528</span></div>' +
    '<div class="card-value">' + title + '</div>' +
    '<div class="card-desc">' + desc + '</div>' +
    '<div style="margin-top:0.55rem;padding:0.55rem 0.7rem;background:rgba(14,165,233,0.06);border:1px solid rgba(14,165,233,0.18);border-radius:8px;font-size:0.78rem;color:var(--text-muted);line-height:1.55;">\u6e38\u620f\u9053\u5177\u53ef\u4e0a\u4f20\u591a\u5f20\u53c2\u8003\u56fe\u6216\u76f4\u63a5\u586b\u63d0\u793a\u8bcd\uff1b\u5b9e\u7269\u751f 3D \u5efa\u8bae\u4e0a\u4f20\u591a\u89d2\u5ea6\u7167\u7247\uff1b\u5df2\u6709\u591a\u89c6\u89d2\u56fe\u53ef\u76f4\u63a5\u9001\u5165 3D\u3002</div>' +
    '<div class="card-tags">' + tags + '</div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm ai3d-model-entry-btn">\u8fdb\u5165 3D \u5de5\u4f5c\u53f0</button></div>' +
  '</div>';
}

function _openclawWeixinResolveBase() {
  if (typeof LOCAL_API_BASE === 'undefined' || !LOCAL_API_BASE) return '';
  return String(LOCAL_API_BASE).replace(/\/$/, '');
}

function _fetchWithTimeout(url, opts, timeoutMs) {
  opts = opts || {};
  timeoutMs = Number(timeoutMs || 0) || 8000;
  if (typeof AbortController === 'undefined') {
    return Promise.race([
      fetch(url, opts),
      new Promise(function(_, reject) {
        setTimeout(function() { reject(new Error('timeout')); }, timeoutMs);
      })
    ]);
  }
  var controller = new AbortController();
  var timer = setTimeout(function() { controller.abort(); }, timeoutMs);
  var merged = Object.assign({}, opts, { signal: controller.signal });
  return fetch(url, merged).finally(function() {
    clearTimeout(timer);
  });
}

function _callbackJobWithTimeout(label, runner, timeoutMs) {
  return new Promise(function(resolve) {
    var done = false;
    function finish() {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve();
    }
    var timer = setTimeout(finish, timeoutMs || _SKILL_STORE_STATUS_TIMEOUT_MS);
    try {
      runner(finish);
    } catch (e) {
      finish();
    }
  });
}

function _fetchSkillStoreFrom(base) {
  var b = String(base || '').replace(/\/$/, '');
  if (!b) return Promise.resolve({ packages: [] });
  var auth = '';
  var overseas = 'domestic';
  try {
    var headers = (typeof authHeaders === 'function') ? authHeaders() : {};
    auth = String((headers && headers.Authorization) || (headers && headers.authorization) || '');
    overseas = (headers && (headers['X-Lobster-Client-Overseas'] || headers['x-lobster-client-overseas'])) ? 'overseas' : 'domestic';
  } catch (eAuth) {}
  var key = b + '|' + auth + '|' + overseas;
  var now = Date.now();
  var cached = _skillStoreFetchCache[key];
  if (cached && cached.data && (now - cached.at) < _SKILL_STORE_FETCH_TTL_MS) {
    return Promise.resolve(cached.data);
  }
  if (cached && cached.promise) {
    return cached.promise;
  }
  var req = _fetchWithTimeout(b + '/skills/store', { headers: authHeaders() }, _SKILL_STORE_FETCH_TIMEOUT_MS)
    .then(function(r) {
      return r.json().then(function(d) {
        return { ok: r.ok, data: d || {} };
      });
    })
    .then(function(res) {
      if (!res.ok) throw new Error((res.data && res.data.detail) || 'load_skill_store_failed');
      var data = res.data || {};
      _skillStoreFetchCache[key] = { data: data, at: Date.now(), promise: null };
      return data;
    })
    .catch(function(err) {
      if (_skillStoreFetchCache[key]) _skillStoreFetchCache[key].promise = null;
      if (cached && cached.data) return cached.data;
      throw err;
    });
  _skillStoreFetchCache[key] = {
    data: cached && cached.data,
    at: cached ? cached.at : 0,
    promise: req
  };
  return req;
}

function _openclawMemoryApiBase() {
  var base = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE)
    ? LOCAL_API_BASE
    : ((typeof API_BASE !== 'undefined' && API_BASE) ? API_BASE : '');
  return String(base || '').replace(/\/$/, '');
}

function _renderOpenclawSkillWorkspaceCard(pkg, opts) {
  pkg = pkg || {};
  opts = opts || {};
  var skillId = String(opts.skillId || pkg.id || '').trim();
  var title = _skillStoreBrandSafeText((pkg.name && String(pkg.name).trim()) || opts.title || skillId);
  var desc = _skillStoreBrandSafeText((opts.desc && String(opts.desc).trim()) || (pkg.description && String(pkg.description).trim()) || '');
  var tags = _skillStoreTagHtml(pkg.tags || opts.tags || ['OpenClaw']);
  var accent = opts.accent || '99,102,241';
  var badge = (opts.badge && String(opts.badge).trim()) || '已授权';
  return '<div class="skill-store-card openclaw-skill-workspace-card" data-openclaw-skill-id="' + escapeAttr(skillId) + '" data-openclaw-skill-title="' + escapeAttr(title) + '" style="cursor:pointer;border-color:rgba(' + accent + ',0.35);background:linear-gradient(135deg,rgba(' + accent + ',0.08),transparent);">' +
    '<div class="card-label">OpenClaw <span class="badge-installed">' + escapeHtml(badge) + '</span></div>' +
    '<div class="card-value">' + escapeHtml(title) + '</div>' +
    '<div class="card-desc">' + escapeHtml(desc) + '</div>' +
    '<div class="card-tags">' + tags + '</div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm openclaw-skill-workspace-entry-btn">进入工作台</button></div></div>';
}

function _loadOpenclawSkillChatScript(cb) {
  if (typeof window.openOpenclawSkillChat === 'function') {
    cb();
    return;
  }
  var existing = document.querySelector('script[data-openclaw-skill-chat-loader="1"]');
  if (existing) {
    existing.addEventListener('load', cb, { once: true });
    existing.addEventListener('error', function() {
      alert('OpenClaw 工作台脚本加载失败，请刷新后重试');
    }, { once: true });
    return;
  }
  var script = document.createElement('script');
  script.src = '/static/js/openclaw-skill-chat.js?v=20260429-return-fix';
  script.async = true;
  script.dataset.openclawSkillChatLoader = '1';
  script.onload = cb;
  script.onerror = function() {
    alert('OpenClaw 工作台脚本加载失败，请刷新后重试');
  };
  document.head.appendChild(script);
}

function _openOpenclawSkillWorkspace(skillId, title) {
  _loadOpenclawSkillChatScript(function() {
    window.openOpenclawSkillChat(skillId, title);
  });
}

function _bindOpenclawSkillWorkspaceCardEntry() {
  document.querySelectorAll('.openclaw-skill-workspace-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      _openOpenclawSkillWorkspace(card.getAttribute('data-openclaw-skill-id'), card.getAttribute('data-openclaw-skill-title'));
    });
  });
  document.querySelectorAll('.openclaw-skill-workspace-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var card = btn.closest('.openclaw-skill-workspace-card');
      if (!card) return;
      _openOpenclawSkillWorkspace(card.getAttribute('data-openclaw-skill-id'), card.getAttribute('data-openclaw-skill-title'));
    });
  });
}

function _renderOpenclawMemoryCard(pkg) {
  pkg = pkg || {};
  var tags = _skillStoreTagHtml(pkg.tags || ['个人记忆', '资料']);
  return '<div class="skill-store-card openclaw-memory-card" style="cursor:pointer;border-color:rgba(20,184,166,0.35);background:linear-gradient(135deg,rgba(20,184,166,0.09),transparent);">' +
    '<div class="card-label">OpenClaw <span class="badge-installed">个人记忆</span></div>' +
    '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || '个人记忆')) + '</div>' +
    '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '上传或同步到本设备的 Word/PDF/Excel/txt/md/csv/json 等资料，智能对话可按会话选择是否使用。')) + '</div>' +
    '<div class="card-tags">' + tags + '</div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm openclaw-memory-entry-btn">管理资料</button></div></div>';
}

function _openOpenclawMemoryPage() {
  if (typeof window.showLobsterView === 'function') {
    window.showLobsterView('openclaw-memory', document.querySelector('.nav-left-item[data-view="skill-store"]')).catch(function() {});
    return;
  }
  var nav = document.querySelector('.nav-left-item[data-view="skill-store"]');
  if (nav) nav.click();
}

function _bindOpenclawMemoryCardEntry() {
  document.querySelectorAll('.openclaw-memory-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      _openOpenclawMemoryPage();
    });
  });
  document.querySelectorAll('.openclaw-memory-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      _openOpenclawMemoryPage();
    });
  });
}

function _showOpenclawMemoryMsg(text, isErr) {
  var el = document.getElementById('openclawMemoryMsg');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'msg' + (isErr ? ' err' : '');
  el.style.display = text ? 'block' : 'none';
}

function _showOpenclawMemoryPageMsg(text, isErr) {
  var el = document.getElementById('openclawMemoryPageMsg');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'msg' + (isErr ? ' err' : '');
  el.style.display = text ? 'block' : 'none';
}

function _syncOpenclawMemoryFromCloud(opts) {
  opts = opts || {};
  var base = _openclawMemoryApiBase();
  if (!base) {
    if (!opts.silent) {
      _showOpenclawMemoryMsg('未找到本机后端地址，无法同步云端资料。', true);
      _showOpenclawMemoryPageMsg('未找到本机后端地址，无法同步云端资料。', true);
    }
    return Promise.resolve({ ok: false });
  }
    if (!opts.silent) {
      _showOpenclawMemoryMsg('正在同步云端个人记忆…', false);
      _showOpenclawMemoryPageMsg('正在同步云端个人记忆…', false);
    }
  return fetch(base + '/api/openclaw/memory/sync-cloud', {
    method: 'POST',
    headers: typeof authHeaders === 'function' ? authHeaders() : {}
  }).then(function(r) {
    return r.json().then(function(d) { return { ok: r.ok, data: d }; });
  }).then(function(x) {
    if (!x.ok) {
      if (!opts.silent) {
        var errMsg = (x.data && x.data.detail) || '同步失败';
        _showOpenclawMemoryMsg(errMsg, true);
        _showOpenclawMemoryPageMsg(errMsg, true);
      }
      return x;
    }
    var d = x.data || {};
    if (!opts.silent) {
      var msg = '同步完成：新增/更新 ' + (d.applied_count || 0) + ' 份，删除 ' + (d.deleted_count || 0) + ' 份。';
      _showOpenclawMemoryMsg(msg, false);
      _showOpenclawMemoryPageMsg(msg, false);
    }
    if (opts.reload !== false) _loadOpenclawMemoryList();
    return x;
  }).catch(function(err) {
    if (!opts.silent) {
      _showOpenclawMemoryMsg('无法连接本机 OpenClaw 资料同步接口', true);
      _showOpenclawMemoryPageMsg('无法连接本机 OpenClaw 资料同步接口', true);
    }
    return { ok: false, error: err };
  });
}

function _countEffectiveOpenclawMemoryDocs(docs) {
  if (!Array.isArray(docs)) return 0;
  return docs.filter(function(doc) {
    return doc && Array.isArray(doc.workspace_paths) && doc.workspace_paths.length > 0;
  }).length;
}

function _loadOpenclawMemoryEffectiveSummary() {
  var base = _openclawMemoryApiBase();
  if (!base) return Promise.reject(new Error('未找到本机后端地址'));
  return _fetchWithTimeout(base + '/api/openclaw/memory/list', {
    headers: typeof authHeaders === 'function' ? authHeaders() : {}
  }, 8000)
    .then(function(r) {
      return r.json().then(function(d) { return { ok: r.ok, data: d }; });
    })
    .then(function(x) {
      if (!x.ok) throw new Error((x.data && x.data.detail) || '读取资料列表失败');
      var docs = (x.data && Array.isArray(x.data.documents)) ? x.data.documents : [];
      return {
        total_count: docs.length,
        effective_count: _countEffectiveOpenclawMemoryDocs(docs)
      };
    });
}

function _setChatMemorySyncStatus(text, isErr) {
  var el = document.getElementById('chatMemorySyncStatus');
  if (!el) return;
  el.textContent = text || '';
  el.style.color = isErr ? '#b91c1c' : 'var(--text-muted)';
}

function _bindChatMemorySyncButton() {
  var btn = document.getElementById('chatMemorySyncBtn');
  if (!btn || btn.dataset.bound === '1') return;
  btn.dataset.bound = '1';
  btn.addEventListener('click', function() {
    btn.disabled = true;
    var oldText = btn.textContent;
    btn.textContent = '同步中...';
    _setChatMemorySyncStatus('正在同步个人记忆...', false);
    var syncResult = null;
    _syncOpenclawMemoryFromCloud({ silent: true, reload: false })
      .then(function(x) {
        syncResult = x || {};
        return _loadOpenclawMemoryEffectiveSummary();
      })
      .then(function(summary) {
        var prefix = '';
        var syncData = (syncResult && syncResult.data) || {};
        if (syncResult && syncResult.ok && syncData.ok !== false) {
          prefix = '同步完成，新增/更新 ' + (syncData.applied_count || 0) + ' 份，删除 ' + (syncData.deleted_count || 0) + ' 份；';
        } else if (syncData && syncData.skipped) {
          prefix = '云端同步跳过；';
        } else if (syncResult && syncResult.ok === false) {
          prefix = '云端同步未完成；';
        } else if (syncData && syncData.ok === false) {
          prefix = '云端同步未完成；';
        }
        _setChatMemorySyncStatus(prefix + '当前已生效 ' + (summary.effective_count || 0) + ' 份个人记忆。', false);
      })
      .catch(function(err) {
        _setChatMemorySyncStatus((err && err.message) ? err.message : '同步个人记忆失败', true);
      })
      .finally(function() {
        btn.disabled = false;
        btn.textContent = oldText || '同步记忆';
      });
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _bindChatMemorySyncButton);
} else {
  _bindChatMemorySyncButton();
}

var _openclawMemoryDocs = [];
var _openclawMemorySearch = '';

function _formatOpenclawMemorySize(size) {
  var n = Number(size || 0);
  if (!n || n < 0) return '';
  if (n < 1024) return n + 'B';
  if (n < 1024 * 1024) return Math.round(n / 1024) + 'KB';
  return (n / 1024 / 1024).toFixed(n >= 10 * 1024 * 1024 ? 0 : 1) + 'MB';
}

function _formatOpenclawMemoryDate(value) {
  if (!value) return '';
  var d = new Date(value);
  if (isNaN(d.getTime())) return String(value).slice(0, 19).replace('T', ' ');
  var pad = function(n) { return n < 10 ? '0' + n : String(n); };
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

function _isOpenclawMemoryEditable(doc) {
  if (!doc) return false;
  var src = String(doc.source || 'local_user');
  var layer = String(doc.memory_layer || '').trim();
  return !(layer === 'agent' || String(doc.origin || '') === 'agent_memory' || src.indexOf('cloud_') === 0);
}

function _renderOpenclawMemoryList(docs) {
  var list = document.getElementById('openclawMemoryList');
  if (!list) return;
  var count = document.getElementById('openclawMemoryCount');
  var query = (_openclawMemorySearch || '').trim().toLowerCase();
  var filtered = (Array.isArray(docs) ? docs : []).filter(function(doc) {
    if (!query) return true;
    var haystack = [
      doc.title || '',
      doc.filename || '',
      doc.notes || '',
      doc.id || ''
    ].join(' ').toLowerCase();
    return haystack.indexOf(query) >= 0;
  });
  if (count) {
    count.textContent = filtered.length + ' / ' + (Array.isArray(docs) ? docs.length : 0) + ' 份资料';
  }
  if (!filtered.length) {
    list.innerHTML = '<div class="openclaw-memory-empty">' + (query ? '没有匹配的资料。' : '还没有个人记忆。点击右上角“添加资料”上传，或同步云端个人记忆。') + '</div>';
    return;
  }
  list.innerHTML = filtered.map(function(doc) {
    var title = doc.title || doc.filename || doc.id;
    var filename = doc.filename || '';
    var notes = String(doc.notes || '').trim();
    var src = String(doc.source || 'local_user');
    var layer = String(doc.memory_layer || '').trim();
    var isAgentMemory = layer === 'agent' || String(doc.origin || '') === 'agent_memory';
    var sourceLabel = isAgentMemory ? '代理商记忆' : (src.indexOf('cloud_') === 0 ? '云端同步' : '个人记忆');
    var sourceClass = src.indexOf('cloud_') === 0 ? 'badge-coming' : 'badge-installed';
    var editable = _isOpenclawMemoryEditable(doc);
    var meta = [
      filename,
      _formatOpenclawMemorySize(doc.size),
      _formatOpenclawMemoryDate(doc.updated_at || doc.created_at)
    ].filter(Boolean).join(' · ');
    var actionHtml = editable
      ? '<button type="button" class="btn btn-ghost btn-sm openclaw-memory-edit" data-doc-id="' + escapeAttr(doc.id || '') + '">编辑</button>' +
        '<button type="button" class="btn btn-ghost btn-sm openclaw-memory-delete" data-doc-id="' + escapeAttr(doc.id || '') + '">删除</button>'
      : '<span class="meta">' + escapeHtml(isAgentMemory ? '由代理商配置' : '云端同步资料，需在下发端维护') + '</span>';
    return '<div class="openclaw-memory-item" data-doc-id="' + escapeAttr(doc.id || '') + '">' +
      '<div class="openclaw-memory-item-main">' +
        '<div class="openclaw-memory-item-label"><span class="' + sourceClass + '">' + escapeHtml(sourceLabel) + '</span></div>' +
        '<div class="openclaw-memory-item-title">' + escapeHtml(title) + '</div>' +
        '<div class="openclaw-memory-item-meta">' + escapeHtml(meta || doc.id || '') + '</div>' +
        (notes ? '<div class="openclaw-memory-item-notes">' + escapeHtml(notes) + '</div>' : '') +
      '</div>' +
      '<div class="openclaw-memory-item-actions">' + actionHtml + '</div>' +
    '</div>';
  }).join('');
  _bindOpenclawMemoryListActions();
}

function _findOpenclawMemoryDoc(docId) {
  docId = String(docId || '');
  for (var i = 0; i < _openclawMemoryDocs.length; i += 1) {
    if (String(_openclawMemoryDocs[i].id || '') === docId) return _openclawMemoryDocs[i];
  }
  return null;
}

function _resetOpenclawMemoryForm() {
  var editId = document.getElementById('openclawMemoryEditId');
  var title = document.getElementById('openclawMemoryTitle');
  var notes = document.getElementById('openclawMemoryNotes');
  var file = document.getElementById('openclawMemoryFile');
  if (editId) editId.value = '';
  if (title) title.value = '';
  if (notes) notes.value = '';
  if (file) file.value = '';
}

function _openOpenclawMemoryUploadModal(doc) {
  var modal = document.getElementById('openclawMemoryModal');
  if (!modal) return;
  var isEdit = !!(doc && doc.id);
  var titleEl = document.getElementById('openclawMemoryModalTitle');
  var editId = document.getElementById('openclawMemoryEditId');
  var fileWrap = document.getElementById('openclawMemoryFile');
  var title = document.getElementById('openclawMemoryTitle');
  var notes = document.getElementById('openclawMemoryNotes');
  var submit = document.getElementById('openclawMemoryUploadBtn');
  _showOpenclawMemoryMsg('', false);
  if (titleEl) titleEl.textContent = isEdit ? '编辑个人记忆' : '添加个人记忆';
  if (editId) editId.value = isEdit ? String(doc.id || '') : '';
  if (fileWrap) {
    fileWrap.value = '';
    fileWrap.disabled = isEdit;
    var field = fileWrap.closest('.modal-field');
    if (field) field.style.display = isEdit ? 'none' : '';
  }
  if (title) title.value = isEdit ? (doc.title || '') : '';
  if (notes) notes.value = isEdit ? (doc.notes || '') : '';
  if (submit) submit.textContent = isEdit ? '保存修改' : '上传并写入记忆';
  modal.classList.add('visible');
}

function _bindOpenclawMemoryListActions() {
  var list = document.getElementById('openclawMemoryList');
  if (!list) return;
  var base = _openclawMemoryApiBase();
  list.querySelectorAll('.openclaw-memory-edit').forEach(function(btn) {
    if (btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', function() {
      var doc = _findOpenclawMemoryDoc(btn.getAttribute('data-doc-id') || '');
      if (doc) _openOpenclawMemoryUploadModal(doc);
    });
  });
  list.querySelectorAll('.openclaw-memory-delete').forEach(function(btn) {
    if (btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', function() {
      var docId = btn.getAttribute('data-doc-id') || '';
      if (!base) {
        _showOpenclawMemoryPageMsg('未找到本机后端地址，请用本机页面打开或设置 local_api。', true);
        return;
      }
      if (!docId || !confirm('确定删除这份个人记忆资料？')) return;
      btn.disabled = true;
      fetch(base + '/api/openclaw/memory/' + encodeURIComponent(docId), {
        method: 'DELETE',
        headers: authHeaders()
      }).then(function(r) {
        return r.json().then(function(d) { return { ok: r.ok, data: d }; });
      }).then(function(y) {
        if (!y.ok) {
          _showOpenclawMemoryPageMsg((y.data && y.data.detail) || '删除失败', true);
          btn.disabled = false;
          return;
        }
        _showOpenclawMemoryPageMsg('已删除', false);
        _loadOpenclawMemoryList();
      }).catch(function() {
        _showOpenclawMemoryPageMsg('网络错误，删除失败', true);
        btn.disabled = false;
      });
    });
  });
}

function _loadOpenclawMemoryList() {
  var list = document.getElementById('openclawMemoryList');
  if (!list) return;
  var base = _openclawMemoryApiBase();
  if (!base) {
    list.innerHTML = '<p class="msg err">未找到本机后端地址，请用本机页面打开或设置 local_api。</p>';
    return;
  }
  list.innerHTML = '<p class="meta">加载资料列表中…</p>';
  fetch(base + '/api/openclaw/memory/list', { headers: authHeaders() })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(x) {
      if (!x.ok) {
        list.innerHTML = '<p class="msg err">' + escapeHtml((x.data && x.data.detail) || '加载失败') + '</p>';
        return;
      }
      var docs = (x.data && Array.isArray(x.data.documents)) ? x.data.documents : [];
      _openclawMemoryDocs = docs;
      _renderOpenclawMemoryList(docs);
    })
    .catch(function() {
      list.innerHTML = '<p class="msg err">无法连接本机 OpenClaw 记忆接口</p>';
    });
}

function initOpenclawMemoryManager() {
  var addBtn = document.getElementById('openclawMemoryAddBtn');
  var refreshBtn = document.getElementById('openclawMemoryRefreshBtn');
  var syncBtn = document.getElementById('openclawMemorySyncBtn');
  var backBtn = document.getElementById('openclawMemoryBackBtn');
  var search = document.getElementById('openclawMemorySearchInput');
  if (addBtn && addBtn.dataset.bound !== '1') {
    addBtn.dataset.bound = '1';
    addBtn.addEventListener('click', function() {
      _resetOpenclawMemoryForm();
      _openOpenclawMemoryUploadModal(null);
    });
  }
  if (refreshBtn && refreshBtn.dataset.bound !== '1') {
    refreshBtn.dataset.bound = '1';
    refreshBtn.addEventListener('click', _loadOpenclawMemoryList);
  }
  if (syncBtn && syncBtn.dataset.bound !== '1') {
    syncBtn.dataset.bound = '1';
    syncBtn.addEventListener('click', function() {
      syncBtn.disabled = true;
      _syncOpenclawMemoryFromCloud({ silent: false }).finally(function() {
        syncBtn.disabled = false;
      });
    });
  }
  if (backBtn && backBtn.dataset.bound !== '1') {
    backBtn.dataset.bound = '1';
    backBtn.addEventListener('click', function() {
      var nav = document.querySelector('.nav-left-item[data-view="skill-store"]');
      if (nav) nav.click();
      else if (typeof window.showLobsterView === 'function') window.showLobsterView('skill-store');
    });
  }
  if (search && search.dataset.bound !== '1') {
    search.dataset.bound = '1';
    search.addEventListener('input', function() {
      _openclawMemorySearch = search.value || '';
      _renderOpenclawMemoryList(_openclawMemoryDocs);
    });
  }
  _showOpenclawMemoryPageMsg('', false);
  _syncOpenclawMemoryFromCloud({ silent: true, reload: false }).then(function() {
    _loadOpenclawMemoryList();
  });
}
window.initOpenclawMemoryManager = initOpenclawMemoryManager;

(function _initOpenclawMemoryModal() {
  var modal = document.getElementById('openclawMemoryModal');
  if (!modal) return;
  var closeBtn = document.getElementById('openclawMemoryClose');
  var cancelBtn = document.getElementById('openclawMemoryCancel');
  var uploadBtn = document.getElementById('openclawMemoryUploadBtn');
  function closeModal() {
    modal.classList.remove('visible');
    _resetOpenclawMemoryForm();
  }
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (cancelBtn) cancelBtn.addEventListener('click', closeModal);
  if (uploadBtn) uploadBtn.addEventListener('click', function() {
    var base = _openclawMemoryApiBase();
    var editIdInput = document.getElementById('openclawMemoryEditId');
    var fileInput = document.getElementById('openclawMemoryFile');
    var titleInput = document.getElementById('openclawMemoryTitle');
    var notesInput = document.getElementById('openclawMemoryNotes');
    var editId = editIdInput ? String(editIdInput.value || '').trim() : '';
    if (!base) {
      _showOpenclawMemoryMsg('未找到本机后端地址，请用本机页面打开或设置 local_api。', true);
      return;
    }
    if (!editId && (!fileInput || !fileInput.files || !fileInput.files[0])) {
      _showOpenclawMemoryMsg('请选择要上传的资料文件', true);
      return;
    }
    var headers = typeof authHeaders === 'function' ? authHeaders() : {};
    uploadBtn.disabled = true;
    uploadBtn.textContent = editId ? '保存中…' : '上传中…';
    _showOpenclawMemoryMsg(editId ? '正在保存个人记忆…' : '正在写入个人记忆…', false);
    var reqUrl = base + '/api/openclaw/memory/upload';
    var reqOptions = { method: 'POST', headers: headers };
    if (editId) {
      headers['Content-Type'] = 'application/json';
      reqUrl = base + '/api/openclaw/memory/' + encodeURIComponent(editId);
      reqOptions.method = 'PATCH';
      reqOptions.body = JSON.stringify({
        title: titleInput ? titleInput.value : '',
        notes: notesInput ? notesInput.value : ''
      });
    } else {
      var fd = new FormData();
      fd.append('file', fileInput.files[0]);
      fd.append('title', titleInput ? titleInput.value : '');
      fd.append('notes', notesInput ? notesInput.value : '');
      delete headers['Content-Type'];
      reqOptions.body = fd;
    }
    fetch(reqUrl, reqOptions).then(function(r) {
      return r.json().then(function(d) { return { ok: r.ok, data: d }; });
    }).then(function(x) {
      if (!x.ok) {
        _showOpenclawMemoryMsg((x.data && x.data.detail) || (editId ? '保存失败' : '上传失败'), true);
        return;
      }
      _showOpenclawMemoryMsg(editId ? '已保存修改。' : '已写入个人记忆。之后智能对话可按会话设置参考这份资料。', false);
      _showOpenclawMemoryPageMsg(editId ? '已保存修改' : '已写入个人记忆', false);
      closeModal();
      _loadOpenclawMemoryList();
    }).catch(function() {
      _showOpenclawMemoryMsg(editId ? '网络错误，保存失败' : '网络错误，上传失败', true);
    }).finally(function() {
      uploadBtn.disabled = false;
      uploadBtn.textContent = editId ? '保存修改' : '上传并写入记忆';
    });
  });
})();

function _mergeSkillStorePackages(primary, secondary) {
  var out = [];
  var seen = {};
  var first = (primary && Array.isArray(primary.packages)) ? primary.packages : [];
  var second = (secondary && Array.isArray(secondary.packages)) ? secondary.packages : [];
  first.forEach(function(pkg) {
    if (!pkg || !pkg.id || seen[pkg.id]) return;
    seen[pkg.id] = true;
    out.push(pkg);
  });
  second.forEach(function(pkg) {
    if (!pkg || !pkg.id || seen[pkg.id]) return;
    seen[pkg.id] = true;
    out.push(pkg);
  });
  return {
    packages: out,
    is_skill_store_admin: !!(primary && primary.is_skill_store_admin)
  };
}

function _renderOpenclawWeixinCard(opts) {
  opts = opts || {};
  var showDebug = !!opts.showDebug;
  var noLocalBackend = !!opts.noLocalBackend;
  var debugBadge = showDebug
    ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">调试</span> '
    : '';
  var ok = _openclawWeixinLast.last_ok;
  var badge = ok
    ? '<span class="badge-installed">近期已登录</span>'
    : '<span class="badge-coming" style="background:rgba(7,193,96,0.12);color:#059669;border-color:rgba(7,193,96,0.25);">需授权</span>';
  var atHint = _openclawWeixinLast.at
    ? '<div style="font-size:0.78rem;color:var(--text-muted);margin-top:0.35rem;">上次记录：' + escapeHtml(String(_openclawWeixinLast.at)) + '</div>'
    : '';
  var localHint = noLocalBackend
    ? '<div style="font-size:0.78rem;color:var(--text-muted);margin-top:0.35rem;">商店入口由服务器控制；发起扫码须本机运行 lobster_online 并配置 LOCAL_API_BASE。</div>'
    : '';
  return '<div class="skill-store-card openclaw-weixin-card" style="border-color:rgba(7,193,96,0.35);background:linear-gradient(135deg,rgba(7,193,96,0.07),transparent);">' +
    '<div class="card-label">' + debugBadge + '通道 <span class="badge-installed">OpenClaw</span> ' + badge + '</div>' +
    '<div class="card-value">微信助手 (OpenClaw)</div>' +
    '<div class="card-desc">点击后将自动依次执行：① <code>plugins install</code> ② <code>config set … enabled true</code> ③ <code>channels login</code>（弹出层内展示二维码）④ 成功后 <code>gateway restart</code>。与腾讯官方微信插件文档步骤一致。</div>' +
    localHint +
    atHint +
    '<div class="card-tags"><span class="tag">微信</span><span class="tag">OpenClaw</span></div>' +
    '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm js-openclaw-weixin-auth">扫码授权</button></div></div>';
}

function _loadYoutubePublishStatus(cb) {
  fetch((LOCAL_API_BASE || '') + '/api/youtube-publish/summary', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      _youtubePublishStatus = {
        has_ready: !!d.has_ready,
        accounts_count: typeof d.accounts_count === 'number' ? d.accounts_count : 0
      };
      if (cb) cb();
    })
    .catch(function() {
      _youtubePublishStatus = { has_ready: false, accounts_count: 0 };
      if (cb) cb();
    });
}

var _SKILL_STORE_SIMPLE_COPY_BY_ID = {
  'sutui_mcp': { title: 'AI 模型能力', desc: '图片、视频、音频创作' },
  'comfly_veo_skill': { title: '爆款TVC', desc: '一键生成爆款视频' },
  'comfly_seedance_tvc_skill': { title: '创意分镜头视频', desc: '生成分镜视频' },
  'seedance_tvc_studio': { title: '创意分镜头视频', desc: '生成分镜视频' },
  'viral_video_remix_skill': { title: '爆款视频复刻', desc: '复刻视频风格' },
  'shanjian_smart_clip': { title: '智能剪辑', desc: '数字人口播混剪' },
  'comfly_ecommerce_detail_skill': { title: '电商详情页', desc: '生成商品套图' },
  'hifly_digital_human_skill': { title: '必火数字人', desc: '生成数字人口播' },
  'openclaw_weixin_channel': { title: '微信助手', desc: '微信通道授权' },
  'wewrite_official_account_skill': { title: '公众号文章', desc: '写文、配图、推草稿' },
  'openclaw_memory_skill': { title: '个人记忆', desc: '管理本机资料' },
  'browser_use_skill': { title: 'Browser Use', desc: '浏览器自动操作' },
  'computer_use_skill': { title: 'Computer Use', desc: '电脑自动操作' },
  'youtube_publish': { title: 'YouTube 上传', desc: '管理视频发布' },
  'meta_social': { title: 'Instagram / Facebook', desc: '管理社媒发布' },
  'twilio_whatsapp': { title: 'Twilio WhatsApp', desc: 'WhatsApp 客服' },
  'messenger_reply': { title: 'Facebook Messenger 客服', desc: 'Messenger 自动回复' },
  'wecom_reply': { title: '企业微信自动回复', desc: '企微客服回复' },
  'ecommerce_publish_skill': { title: '商品发布', desc: '管理店铺账号' },
  'ip_content_daily_skill': { title: 'IP日更文案', desc: '榜单、同行、记忆生成文案' },
  'linkedin_mining_skill': { title: 'LinkedIn线索挖掘', desc: '画像、候选人、图谱和报告' },
  'juhe_wechat_skill': { title: '微信协议助手', desc: '本机微信连接工作台' },
  'create_ppt_skill': { title: 'PPT 生成', desc: '主题生成演示文稿' },
  'mcp_agency_lona_trading': { title: 'Lona Trading', desc: '业务自动化工具' }
};
var _SKILL_STORE_SIMPLE_COPY_BY_CLASS = {
  'skill-card-ai-models': _SKILL_STORE_SIMPLE_COPY_BY_ID.sutui_mcp,
  'comfly-veo-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.comfly_veo_skill,
  'seedance-tvc-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.seedance_tvc_studio,
  'viral-video-remix-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.viral_video_remix_skill,
  'shanjian-smart-clip-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.shanjian_smart_clip,
  'ecommerce-detail-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.comfly_ecommerce_detail_skill,
  'hifly-digital-human-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.hifly_digital_human_skill,
  'openclaw-weixin-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.openclaw_weixin_channel,
  'wechat-article-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.wewrite_official_account_skill,
  'openclaw-memory-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.openclaw_memory_skill,
  'youtube-publish-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.youtube_publish,
  'meta-social-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.meta_social,
  'twilio-whatsapp-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.twilio_whatsapp,
  'messenger-reply-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.messenger_reply,
  'wecom-reply-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.wecom_reply,
  'ecommerce-publish-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.ecommerce_publish_skill,
  'ip-content-studio-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.ip_content_daily_skill,
  'linkedin-mining-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.linkedin_mining_skill,
  'juhe-wechat-card': _SKILL_STORE_SIMPLE_COPY_BY_ID.juhe_wechat_skill
};
var _SKILL_STORE_SIMPLE_COPY_PATTERNS = [
  [/AI\s*模型|模型能力|sutui|速推/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.sutui_mcp],
  [/爆款\s*TVC|comfly.*veo|带货/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.comfly_veo_skill],
  [/创意分镜|seedance|分镜头/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.seedance_tvc_studio],
  [/爆款视频复刻|复刻/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.viral_video_remix_skill],
  [/智能剪辑|山涧/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.shanjian_smart_clip],
  [/电商上架|电商详情|上架套图|详情图|SKU/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.comfly_ecommerce_detail_skill],
  [/必火数字人|数字人/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.hifly_digital_human_skill],
  [/微信助手|openclaw.*微信|weixin/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.openclaw_weixin_channel],
  [/公众号|微信文章|微信推文|wewrite/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.wewrite_official_account_skill],
  [/个人记忆|memory/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.openclaw_memory_skill],
  [/browser\s*use/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.browser_use_skill],
  [/computer\s*use/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.computer_use_skill],
  [/youtube/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.youtube_publish],
  [/instagram|facebook|meta/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.meta_social],
  [/twilio|whatsapp/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.twilio_whatsapp],
  [/messenger/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.messenger_reply],
  [/企业微信|企微|wecom/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.wecom_reply],
  [/商品发布|电商发布|店铺账号/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.ecommerce_publish_skill],
  [/IP日更|日更文案|TikHub|同行|榜单/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.ip_content_daily_skill],
  [/LinkedIn|linkedin|线索挖掘|B2B/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.linkedin_mining_skill],
  [/juhe|juhebot|wechat protocol|微信协议/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.juhe_wechat_skill],
  [/ppt|presentation|演示文稿/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.create_ppt_skill],
  [/agency_lona|lona/i, _SKILL_STORE_SIMPLE_COPY_BY_ID.mcp_agency_lona_trading]
];
var _SKILL_STORE_ICON_BY_ID = {
  'sutui_mcp': { icon: 'sparkles', tone: 'aurora' },
  'comfly_veo_skill': { icon: 'play', tone: 'sunset' },
  'comfly_seedance_tvc_skill': { icon: 'storyboard', tone: 'sky' },
  'seedance_tvc_studio': { icon: 'storyboard', tone: 'sky' },
  'viral_video_remix_skill': { icon: 'refresh', tone: 'mint' },
  'shanjian_smart_clip': { icon: 'scissors', tone: 'blue' },
  'comfly_ecommerce_detail_skill': { icon: 'bag', tone: 'rose' },
  'hifly_digital_human_skill': { icon: 'user', tone: 'violet' },
  'openclaw_weixin_channel': { icon: 'message', tone: 'green' },
  'wewrite_official_account_skill': { icon: 'file', tone: 'coral' },
  'openclaw_memory_skill': { icon: 'database', tone: 'teal' },
  'browser_use_skill': { icon: 'browser', tone: 'indigo' },
  'computer_use_skill': { icon: 'monitor', tone: 'cyan' },
  'youtube_publish': { icon: 'upload', tone: 'red' },
  'meta_social': { icon: 'share', tone: 'pink' },
  'twilio_whatsapp': { icon: 'message', tone: 'green' },
  'messenger_reply': { icon: 'chat', tone: 'indigo' },
  'wecom_reply': { icon: 'building', tone: 'cyan' },
  'ecommerce_publish_skill': { icon: 'store', tone: 'orange' },
  'ip_content_daily_skill': { icon: 'trend', tone: 'teal' },
  'linkedin_mining_skill': { icon: 'network', tone: 'blue' },
  'juhe_wechat_skill': { icon: 'message', tone: 'green' },
  'create_ppt_skill': { icon: 'presentation', tone: 'orange' },
  'mcp_agency_lona_trading': { icon: 'plug', tone: 'slate' }
};
var _SKILL_STORE_ICON_BY_CLASS = {
  'skill-card-ai-models': _SKILL_STORE_ICON_BY_ID.sutui_mcp,
  'comfly-veo-card': _SKILL_STORE_ICON_BY_ID.comfly_veo_skill,
  'seedance-tvc-card': _SKILL_STORE_ICON_BY_ID.seedance_tvc_studio,
  'viral-video-remix-card': _SKILL_STORE_ICON_BY_ID.viral_video_remix_skill,
  'shanjian-smart-clip-card': _SKILL_STORE_ICON_BY_ID.shanjian_smart_clip,
  'ecommerce-detail-card': _SKILL_STORE_ICON_BY_ID.comfly_ecommerce_detail_skill,
  'hifly-digital-human-card': _SKILL_STORE_ICON_BY_ID.hifly_digital_human_skill,
  'openclaw-weixin-card': _SKILL_STORE_ICON_BY_ID.openclaw_weixin_channel,
  'wechat-article-card': _SKILL_STORE_ICON_BY_ID.wewrite_official_account_skill,
  'openclaw-memory-card': _SKILL_STORE_ICON_BY_ID.openclaw_memory_skill,
  'youtube-publish-card': _SKILL_STORE_ICON_BY_ID.youtube_publish,
  'meta-social-card': _SKILL_STORE_ICON_BY_ID.meta_social,
  'twilio-whatsapp-card': _SKILL_STORE_ICON_BY_ID.twilio_whatsapp,
  'messenger-reply-card': _SKILL_STORE_ICON_BY_ID.messenger_reply,
  'wecom-reply-card': _SKILL_STORE_ICON_BY_ID.wecom_reply,
  'ecommerce-publish-card': _SKILL_STORE_ICON_BY_ID.ecommerce_publish_skill,
  'ip-content-studio-card': _SKILL_STORE_ICON_BY_ID.ip_content_daily_skill,
  'linkedin-mining-card': _SKILL_STORE_ICON_BY_ID.linkedin_mining_skill,
  'juhe-wechat-card': _SKILL_STORE_ICON_BY_ID.juhe_wechat_skill
};
var _SKILL_STORE_ICON_PATTERNS = [
  [/AI\s*模型|模型能力|sutui|速推/i, _SKILL_STORE_ICON_BY_ID.sutui_mcp],
  [/爆款\s*TVC|comfly.*veo|带货/i, _SKILL_STORE_ICON_BY_ID.comfly_veo_skill],
  [/创意分镜|seedance|分镜头/i, _SKILL_STORE_ICON_BY_ID.seedance_tvc_studio],
  [/爆款视频复刻|复刻/i, _SKILL_STORE_ICON_BY_ID.viral_video_remix_skill],
  [/智能剪辑|山涧/i, _SKILL_STORE_ICON_BY_ID.shanjian_smart_clip],
  [/电商上架|电商详情|上架套图|详情图|SKU/i, _SKILL_STORE_ICON_BY_ID.comfly_ecommerce_detail_skill],
  [/必火数字人|数字人/i, _SKILL_STORE_ICON_BY_ID.hifly_digital_human_skill],
  [/微信助手|openclaw.*微信|weixin/i, _SKILL_STORE_ICON_BY_ID.openclaw_weixin_channel],
  [/公众号|微信文章|微信推文|wewrite/i, _SKILL_STORE_ICON_BY_ID.wewrite_official_account_skill],
  [/个人记忆|memory/i, _SKILL_STORE_ICON_BY_ID.openclaw_memory_skill],
  [/browser\s*use/i, _SKILL_STORE_ICON_BY_ID.browser_use_skill],
  [/computer\s*use/i, _SKILL_STORE_ICON_BY_ID.computer_use_skill],
  [/youtube/i, _SKILL_STORE_ICON_BY_ID.youtube_publish],
  [/instagram|facebook|meta/i, _SKILL_STORE_ICON_BY_ID.meta_social],
  [/twilio|whatsapp/i, _SKILL_STORE_ICON_BY_ID.twilio_whatsapp],
  [/messenger/i, _SKILL_STORE_ICON_BY_ID.messenger_reply],
  [/企业微信|企微|wecom/i, _SKILL_STORE_ICON_BY_ID.wecom_reply],
  [/商品发布|电商发布|店铺账号/i, _SKILL_STORE_ICON_BY_ID.ecommerce_publish_skill],
  [/IP日更|日更文案|TikHub|同行|榜单/i, _SKILL_STORE_ICON_BY_ID.ip_content_daily_skill],
  [/LinkedIn|linkedin|线索挖掘|B2B/i, _SKILL_STORE_ICON_BY_ID.linkedin_mining_skill],
  [/juhe|juhebot|wechat protocol|微信协议/i, _SKILL_STORE_ICON_BY_ID.juhe_wechat_skill],
  [/ppt|presentation|演示文稿/i, _SKILL_STORE_ICON_BY_ID.create_ppt_skill],
  [/agency_lona|lona|mcp/i, _SKILL_STORE_ICON_BY_ID.mcp_agency_lona_trading]
];
function _skillStoreIconFor(card) {
  if (!card) return { icon: 'sparkles', tone: 'default' };
  var skillId = card.getAttribute('data-openclaw-skill-id') || '';
  if (skillId && _SKILL_STORE_ICON_BY_ID[skillId]) return _SKILL_STORE_ICON_BY_ID[skillId];
  var packageId = card.getAttribute('data-skill-package-id') || '';
  if (packageId && _SKILL_STORE_ICON_BY_ID[packageId]) return _SKILL_STORE_ICON_BY_ID[packageId];
  for (var cls in _SKILL_STORE_ICON_BY_CLASS) {
    if (Object.prototype.hasOwnProperty.call(_SKILL_STORE_ICON_BY_CLASS, cls) && card.classList.contains(cls)) {
      return _SKILL_STORE_ICON_BY_CLASS[cls];
    }
  }
  var titleEl = card.querySelector('.card-value');
  var title = titleEl ? (titleEl.textContent || '').trim() : '';
  var haystack = [packageId, skillId, title].join(' ');
  for (var i = 0; i < _SKILL_STORE_ICON_PATTERNS.length; i += 1) {
    if (_SKILL_STORE_ICON_PATTERNS[i][0].test(haystack)) return _SKILL_STORE_ICON_PATTERNS[i][1];
  }
  return { icon: 'sparkles', tone: 'default' };
}

function _skillStoreIconSvg(name) {
  var icons = {
    sparkles: '<path d="M12 3l1.3 4.1L17 8.5l-3.7 1.4L12 14l-1.3-4.1L7 8.5l3.7-1.4L12 3z"></path><path d="M5 14l.8 2.2L8 17l-2.2.8L5 20l-.8-2.2L2 17l2.2-.8L5 14z"></path><path d="M19 13l.7 1.8 1.8.7-1.8.7L19 18l-.7-1.8-1.8-.7 1.8-.7L19 13z"></path>',
    play: '<path d="M8 5.5v13l11-6.5-11-6.5z"></path>',
    storyboard: '<rect x="3" y="5" width="18" height="14" rx="3"></rect><path d="M9 5v14"></path><path d="M15 5v14"></path><path d="M3 10h18"></path><path d="M3 15h18"></path>',
    refresh: '<path d="M20 7v5h-5"></path><path d="M4 17v-5h5"></path><path d="M18 9a7 7 0 0 0-11.5-2.5L4 9"></path><path d="M6 15a7 7 0 0 0 11.5 2.5L20 15"></path>',
    scissors: '<circle cx="6" cy="7" r="3"></circle><circle cx="6" cy="17" r="3"></circle><path d="M8.6 8.5L20 4"></path><path d="M8.6 15.5L20 20"></path><path d="M9 12h3"></path>',
    bag: '<path d="M6 8h12l-1 12H7L6 8z"></path><path d="M9 8a3 3 0 0 1 6 0"></path>',
    user: '<circle cx="12" cy="8" r="4"></circle><path d="M4.5 20a7.5 7.5 0 0 1 15 0"></path>',
    message: '<path d="M5 6h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9l-5 3v-3H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z"></path><path d="M8 11h8"></path><path d="M8 15h5"></path>',
    file: '<path d="M7 3h7l5 5v13H7V3z"></path><path d="M14 3v6h5"></path><path d="M9.5 13h5"></path><path d="M9.5 17h5"></path>',
    database: '<ellipse cx="12" cy="6" rx="7" ry="3"></ellipse><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6"></path><path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"></path>',
    browser: '<rect x="4" y="5" width="16" height="14" rx="3"></rect><path d="M4 9h16"></path><path d="M8 7h.01"></path><path d="M11 7h.01"></path>',
    monitor: '<rect x="4" y="5" width="16" height="12" rx="2"></rect><path d="M8 21h8"></path><path d="M12 17v4"></path>',
    upload: '<path d="M12 19V6"></path><path d="M7 11l5-5 5 5"></path><path d="M5 19h14"></path>',
    share: '<circle cx="7" cy="12" r="3"></circle><circle cx="17" cy="7" r="3"></circle><circle cx="17" cy="17" r="3"></circle><path d="M9.6 10.5l4.8-2.8"></path><path d="M9.6 13.5l4.8 2.8"></path>',
    chat: '<path d="M4 6h16v10H9l-5 4V6z"></path><path d="M8 10h8"></path><path d="M8 14h5"></path>',
    building: '<path d="M5 21V5l7-3 7 3v16"></path><path d="M9 21v-6h6v6"></path><path d="M9 7h.01"></path><path d="M15 7h.01"></path><path d="M9 11h.01"></path><path d="M15 11h.01"></path>',
    store: '<path d="M4 10h16l-1.5-5h-13L4 10z"></path><path d="M6 10v10h12V10"></path><path d="M9 20v-5h6v5"></path><path d="M4 10c0 1.4 1.1 2.5 2.5 2.5S9 11.4 9 10"></path><path d="M9 10c0 1.4 1.1 2.5 2.5 2.5S14 11.4 14 10"></path><path d="M14 10c0 1.4 1.1 2.5 2.5 2.5S19 11.4 19 10"></path>',
    film: '<rect x="4" y="5" width="16" height="14" rx="3"></rect><path d="M8 5v14"></path><path d="M16 5v14"></path><path d="M4 10h4"></path><path d="M16 10h4"></path><path d="M4 15h4"></path><path d="M16 15h4"></path>',
    presentation: '<rect x="5" y="4" width="14" height="11" rx="2"></rect><path d="M12 15v5"></path><path d="M8 20h8"></path><path d="M9 9h6"></path><path d="M9 12h4"></path>',
    trend: '<path d="M4 17l6-6 4 4 6-8"></path><path d="M14 7h6v6"></path><path d="M4 21h16"></path>',
    network: '<circle cx="6" cy="7" r="3"></circle><circle cx="18" cy="7" r="3"></circle><circle cx="12" cy="18" r="3"></circle><path d="M8.6 8.6l6.8 6.8"></path><path d="M15.4 8.6l-6.8 6.8"></path><path d="M9 7h6"></path>',
    plug: '<path d="M9 7V3"></path><path d="M15 7V3"></path><path d="M7 7h10v5a5 5 0 0 1-10 0V7z"></path><path d="M12 17v4"></path>'
  };
  var paths = icons[name] || icons.sparkles;
  return '<svg class="skill-card-art-svg" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + paths + '</svg>';
}

function _skillStoreSimpleCopyFor(card) {
  if (!card) return null;
  var skillId = card.getAttribute('data-openclaw-skill-id') || '';
  if (skillId && _SKILL_STORE_SIMPLE_COPY_BY_ID[skillId]) return _SKILL_STORE_SIMPLE_COPY_BY_ID[skillId];
  var packageId = card.getAttribute('data-skill-package-id') || '';
  if (packageId && _SKILL_STORE_SIMPLE_COPY_BY_ID[packageId]) return _SKILL_STORE_SIMPLE_COPY_BY_ID[packageId];
  for (var cls in _SKILL_STORE_SIMPLE_COPY_BY_CLASS) {
    if (Object.prototype.hasOwnProperty.call(_SKILL_STORE_SIMPLE_COPY_BY_CLASS, cls) && card.classList.contains(cls)) {
      return _SKILL_STORE_SIMPLE_COPY_BY_CLASS[cls];
    }
  }
  var titleEl = card.querySelector('.card-value');
  var title = titleEl ? (titleEl.textContent || '').trim() : '';
  var haystack = [packageId, skillId, title].join(' ');
  for (var i = 0; i < _SKILL_STORE_SIMPLE_COPY_PATTERNS.length; i += 1) {
    if (_SKILL_STORE_SIMPLE_COPY_PATTERNS[i][0].test(haystack)) return _SKILL_STORE_SIMPLE_COPY_PATTERNS[i][1];
  }
  if (/^\?+$/.test(title) || /�/.test(title)) {
    return { title: '技能工具', desc: '智能辅助功能' };
  }
  return { title: title || '技能工具', desc: '智能辅助功能' };
}

function _isCreativeFilmSkillCard(card) {
  if (!card) return false;
  var packageId = card.getAttribute('data-skill-package-id') || '';
  return packageId === 'goal_video_pipeline_skill' ||
    card.classList.contains('goal-video-pipeline-card');
}

function _isIpContentSkillCard(card) {
  if (!card) return false;
  var packageId = card.getAttribute('data-skill-package-id') || '';
  return packageId === 'ip_content_daily_skill' ||
    card.classList.contains('ip-content-studio-card');
}

function _isLinkedinMiningSkillCard(card) {
  if (!card) return false;
  var packageId = card.getAttribute('data-skill-package-id') || '';
  return packageId === 'linkedin_mining_skill' ||
    card.classList.contains('linkedin-mining-card');
}

function _isSocialLeadsSkillCard(card) {
  if (!card) return false;
  var packageId = card.getAttribute('data-skill-package-id') || '';
  return packageId === 'reddit_leads' ||
    packageId === 'x_leads' ||
    packageId === 'tiktok_leads' ||
    card.classList.contains('social-leads-card') ||
    card.classList.contains('reddit-leads-card') ||
    card.classList.contains('x-leads-card') ||
    card.classList.contains('tiktok-leads-card');
}

function _isJuheWechatSkillCard(card) {
  if (!card) return false;
  var packageId = card.getAttribute('data-skill-package-id') || '';
  return packageId === 'juhe_wechat_skill' ||
    card.classList.contains('juhe-wechat-card');
}

function _isWechatChannelsTranscriptSkillCard(card) {
  if (!card) return false;
  var packageId = card.getAttribute('data-skill-package-id') || '';
  return packageId === 'wechat_channels_transcript_skill' ||
    card.classList.contains('wechat-channels-transcript-card');
}

function _isAlibabaInquiriesSkillCard(card) {
  if (!card) return false;
  var packageId = card.getAttribute('data-skill-package-id') || '';
  return packageId === 'alibaba_inquiry_takeover_skill' ||
    card.classList.contains('alibaba-inquiries-card');
}

function _simplifySkillStoreCards(el) {
  if (!el) return;
  el.querySelectorAll('.skill-store-card').forEach(function(card) {
    if (_isCreativeFilmSkillCard(card)) return;
    if (_isLinkedinMiningSkillCard(card)) return;
    if (_isSocialLeadsSkillCard(card)) return;
    if (_isJuheWechatSkillCard(card)) return;
    if (_isWechatChannelsTranscriptSkillCard(card)) return;
    if (_isAlibabaInquiriesSkillCard(card)) return;
    var copy = _skillStoreSimpleCopyFor(card) || {};
    card.classList.add('is-simple-skill-card');
    Array.prototype.slice.call(card.children || []).forEach(function(child) {
      if (child.classList && (
        child.classList.contains('skill-card-art-wrap') ||
        child.classList.contains('skill-card-art') ||
        child.classList.contains('card-label') ||
        child.classList.contains('card-value') ||
        child.classList.contains('card-desc') ||
        child.classList.contains('card-tags') ||
        child.classList.contains('card-actions')
      )) return;
      if (child.tagName === 'DIV') child.remove();
    });
    var labelEl = card.querySelector('.card-label');
    if (labelEl) labelEl.textContent = '';
    var titleEl = card.querySelector('.card-value');
    if (titleEl) titleEl.textContent = copy.title || '技能工具';
    var descEl = card.querySelector('.card-desc');
    if (!descEl) {
      descEl = document.createElement('div');
      descEl.className = 'card-desc';
      if (titleEl && titleEl.parentNode) titleEl.parentNode.insertBefore(descEl, titleEl.nextSibling);
      else card.appendChild(descEl);
    }
    descEl.textContent = copy.desc || '智能辅助功能';
    var tagsEl = card.querySelector('.card-tags');
    if (tagsEl) tagsEl.textContent = '';
    card.querySelectorAll('.card-actions .btn').forEach(function(btn, idx) {
      if (idx > 0) {
        btn.classList.add('skill-secondary-action');
        return;
      }
      var text = (btn.textContent || '').trim();
      if (/进入|打开|工作台|生成/.test(text)) btn.textContent = '打开';
      else if (/配置|授权|账号|Token|Key|保存/.test(text)) btn.textContent = '配置';
      else if (/安装|解锁/.test(text)) btn.textContent = text.replace(/（.*?）/g, '');
    });
  });
}

function _decorateSkillImageCards(el) {
  if (!el) return;
  var cardClickableClasses = [
    'youtube-publish-card',
    'meta-social-card',
    'twilio-whatsapp-card',
    'messenger-reply-card',
    'wecom-reply-card',
    'ecommerce-publish-card',
    'ecommerce-detail-card',
    'seedance-tvc-card',
    'viral-video-remix-card',
    'hifly-digital-human-card',
    'shanjian-smart-clip-card',
    'goal-video-pipeline-card',
    'ip-content-studio-card',
    'linkedin-mining-card',
    'juhe-wechat-card',
    'wechat-article-card',
    'openclaw-skill-workspace-card',
    'openclaw-memory-card'
  ];
  el.querySelectorAll('.skill-store-card').forEach(function(card) {
    if (!card.classList.contains('is-image-skill-card')) {
      var title = '';
      var titleEl = card.querySelector('.card-value');
      if (titleEl) title = (titleEl.textContent || '').trim();
      var iconMeta = _skillStoreIconFor(card);
      var artWrap = document.createElement('div');
      artWrap.className = 'skill-card-art-wrap';
      artWrap.setAttribute('data-skill-icon-tone', iconMeta.tone || 'default');
      artWrap.setAttribute('aria-hidden', 'true');
      artWrap.innerHTML = _skillStoreIconSvg(iconMeta.icon || 'sparkles');
      card.insertBefore(artWrap, card.firstChild);
      card.classList.add('is-image-skill-card');
    }

    var hasCardClick = cardClickableClasses.some(function(cls) { return card.classList.contains(cls); });
    if (_isCreativeFilmSkillCard(card)) return;
    if (_isIpContentSkillCard(card)) return;
    if (_isLinkedinMiningSkillCard(card)) return;
    if (_isSocialLeadsSkillCard(card)) return;
    if (_isJuheWechatSkillCard(card)) return;
    if (_isWechatChannelsTranscriptSkillCard(card)) return;
    if (hasCardClick || card.dataset.imageCardProxyBound === '1') return;
    var primaryAction = card.querySelector(
      '[data-unlock-credits], [data-install], ' +
      '.js-openclaw-weixin-auth, #xskillModelsBtn, #xskillConfigBtn, #comflyConfigBtn, .js-comfly-config-btn'
    );
    if (!primaryAction) return;
    card.dataset.imageCardProxyBound = '1';
    card.addEventListener('click', function(e) {
      if (e.defaultPrevented) return;
      if (e.target && e.target.closest && e.target.closest('.card-actions')) return;
      e.preventDefault();
      primaryAction.click();
    });
  });
}

function loadSkillStore() {
  var el = document.getElementById('skillStoreList');
  if (!el) return;
  el.innerHTML = '<p class="meta">加载中…</p>';

  var remoteBase = (typeof API_BASE !== 'undefined' ? API_BASE : '') || '';
  var remoteReq = _fetchSkillStoreFrom(remoteBase).catch(function() { return { packages: [] }; });

  Promise.all([remoteReq])
    .then(function(results) {
      var d = results[0] || { packages: [] };
      var isSkillAdmin = !!(d && d.is_skill_store_admin);
      var packages = (d && Array.isArray(d.packages)) ? d.packages : [];
      function pkgById(id) {
        return packages.filter(function(p) { return p && p.id === id; })[0] || null;
      }
      var needYoutube = !!pkgById('youtube_publish');
      var ecommercePkg = pkgById('comfly_ecommerce_detail_skill');
      var browserUsePkg = pkgById('browser_use_skill');
      var computerUsePkg = pkgById('computer_use_skill');
      var sutuiPkg = pkgById('sutui_mcp');
      var comflyPkg = pkgById('comfly_veo_skill');
      var seedancePkg = pkgById('comfly_seedance_tvc_skill') || pkgById('seedance_tvc_studio');
      var viralPkg = pkgById('viral_video_remix_skill');
      var shanjianPkg = pkgById('shanjian_smart_clip');
      var metaPkg = pkgById('meta_social');
      var cutcliPkg = pkgById('cutcli_template_skill') || pkgById('cutcli_templates_skill') || pkgById('cutcli_template_studio');
      var ipContentPkg = pkgById('ip_content_daily_skill');
      var linkedinMiningPkg = pkgById('linkedin_mining_skill');
      var redditLeadsPkg = pkgById('reddit_leads');
      var xLeadsPkg = pkgById('x_leads');
      var tiktokLeadsPkg = pkgById('tiktok_leads');
      var juheWechatPkg = pkgById('juhe_wechat_skill');
      var wechatTranscriptPkg = pkgById('wechat_channels_transcript_skill');
      var ai3dPkg = pkgById('ai_3d_model_skill');
      var globalLeadsPkg = pkgById('global_trade_leads_skill');
      var alibabaInquiriesPkg = pkgById('alibaba_inquiry_takeover_skill') || {
        id: 'alibaba_inquiry_takeover_skill',
        name: '阿里询盘接管',
        description: '接入阿里国际站询盘：账号登录、滚动同步历史询盘、客户档案、AI话术总结和回复辅助。',
        type: 'builtin',
        status: 'available',
        default_installed: true,
        tags: ['阿里国际站', '询盘', '客户档案', 'AI话术']
      };

      function paintSkillStoreList() {
        var html = '';
        if (sutuiPkg) html += _renderXSkillCard();
        if (comflyPkg) html += _renderComflyCard();
        if (seedancePkg) html += _renderSeedanceTvcStudioCard();
        if (viralPkg) html += _renderViralVideoRemixCard();
        if (cutcliPkg) html += _renderCutcliTemplateCard();
        if (shanjianPkg) html += _renderShanjianSmartClipCard();
        if (ecommercePkg) html += _renderEcommerceDetailCard({ pkg: ecommercePkg });
        if (metaPkg) html += _renderMetaSocialCard({ pkg: metaPkg });
        if (ipContentPkg) html += _renderIpContentStudioCard(ipContentPkg, !!(isSkillAdmin && ipContentPkg.store_visibility === 'debug'));
        if (linkedinMiningPkg) html += _renderLinkedinMiningCard(linkedinMiningPkg, !!(isSkillAdmin && linkedinMiningPkg.store_visibility === 'debug'));
        if (redditLeadsPkg) html += _renderSocialLeadsCard(redditLeadsPkg, 'reddit', !!(isSkillAdmin && redditLeadsPkg.store_visibility === 'debug'));
        if (xLeadsPkg) html += _renderSocialLeadsCard(xLeadsPkg, 'x', !!(isSkillAdmin && xLeadsPkg.store_visibility === 'debug'));
        if (tiktokLeadsPkg) html += _renderSocialLeadsCard(tiktokLeadsPkg, 'tiktok', !!(isSkillAdmin && tiktokLeadsPkg.store_visibility === 'debug'));
        if (globalLeadsPkg) html += _renderGlobalLeadsCard(globalLeadsPkg, !!(isSkillAdmin && globalLeadsPkg.store_visibility === 'debug'));
        if (alibabaInquiriesPkg) html += _renderAlibabaInquiriesCard(alibabaInquiriesPkg, !!(isSkillAdmin && alibabaInquiriesPkg.store_visibility === 'debug'));
        if (juheWechatPkg) html += _renderJuheWechatCard(juheWechatPkg, !!(isSkillAdmin && juheWechatPkg.store_visibility === 'debug'));
        if (wechatTranscriptPkg) html += _renderWechatChannelsTranscriptCard(wechatTranscriptPkg, !!(isSkillAdmin && wechatTranscriptPkg.store_visibility === 'debug'));
        if (ai3dPkg) html += _renderAi3dModelCard(ai3dPkg, !!(isSkillAdmin && ai3dPkg.store_visibility === 'debug'));
        if (browserUsePkg) {
          html += _renderOpenclawSkillWorkspaceCard(browserUsePkg, {
            skillId: 'browser_use_skill',
            title: 'Browser Use',
            accent: '99,102,241',
            tags: ['OpenClaw', 'Browser', 'automation'],
            desc: '独立 OpenClaw 浏览器工作台'
          });
        }
        if (computerUsePkg) {
          html += _renderOpenclawSkillWorkspaceCard(computerUsePkg, {
            skillId: 'computer_use_skill',
            title: 'Computer Use',
            accent: '14,165,233',
            tags: ['OpenClaw', 'Computer', 'automation'],
            desc: '独立 OpenClaw 电脑操作工作台'
          });
        }
        var wxPkg = pkgById('openclaw_weixin_channel');
        if (wxPkg) {
          var hasLocal = !!(typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE);
          html += _renderOpenclawWeixinCard({
            showDebug: !!(isSkillAdmin && wxPkg && wxPkg.store_visibility === 'debug'),
            noLocalBackend: !hasLocal,
          });
        }
        var memoryPkg = pkgById('openclaw_memory_skill');
        if (memoryPkg) html += _renderOpenclawMemoryCard(memoryPkg);
        html += packages.map(function(pkg) {
          var debugBadge = (isSkillAdmin && pkg.store_visibility === 'debug')
            ? '<span class="badge-coming" style="background:rgba(139,92,246,0.12);color:#a78bfa;border-color:rgba(139,92,246,0.25);margin-right:0.35rem;">调试</span> '
            : '';
          if (pkg.id === 'sutui_mcp') return '';
          /* 爆款TVC 仅由上方 _renderComflyCard() 展示，避免与 skill_registry 的 comfly_veo_skill 重复成两张卡 */
          if (pkg.id === 'comfly_veo_skill') return '';
          if (pkg.id === 'comfly_seedance_tvc_skill') return '';
          if (pkg.id === 'seedance_tvc_studio') return '';
          if (pkg.id === 'viral_video_remix_skill') return '';
          if (pkg.id === 'shanjian_smart_clip') return '';
          if (pkg.id === 'cutcli_template_skill') return '';
          if (pkg.id === 'cutcli_templates_skill') return '';
          if (pkg.id === 'cutcli_template_studio') return '';
          if (pkg.id === 'comfly_ecommerce_detail_skill') return '';
          if (pkg.id === 'ip_content_daily_skill') return '';
          if (pkg.id === 'linkedin_mining_skill') return '';
          if (pkg.id === 'reddit_leads') return '';
          if (pkg.id === 'x_leads') return '';
          if (pkg.id === 'tiktok_leads') return '';
          if (pkg.id === 'global_trade_leads_skill') return '';
          if (pkg.id === 'alibaba_inquiry_takeover_skill') return '';
          if (pkg.id === 'juhe_wechat_skill') return '';
          if (pkg.id === 'openclaw_weixin_channel') return '';
          if (pkg.id === 'openclaw_memory_skill') return '';
          if (pkg.id === 'ai_3d_model_skill') return '';
          if (pkg.id === 'browser_use_skill') return '';
          if (pkg.id === 'computer_use_skill') return '';
          if (pkg.id === 'meta_social') return '';
          if (pkg.id === 'hifly_digital_human_skill') return _renderHiflyDigitalHumanCard(pkg);
          if (pkg.id === 'youtube_publish') {
            return _renderYoutubePublishCard({
              pkg: pkg,
              showDebug: !!(isSkillAdmin && pkg.store_visibility === 'debug'),
            });
          }
          if (pkg.id === 'twilio_whatsapp') {
            return _renderTwilioWhatsappCard({
              pkg: pkg,
              showDebug: !!(isSkillAdmin && pkg.store_visibility === 'debug'),
            });
          }
        if (pkg.id === 'messenger_reply') {
          var tagsM = _skillStoreTagHtml(pkg.tags || []);
          var capM = pkg.capabilities_count ? ' · ' + pkg.capabilities_count + ' 个能力' : '';
          return '<div class="skill-store-card messenger-reply-card" style="cursor:pointer;border-color:rgba(99,102,241,0.35);background:linear-gradient(135deg,rgba(99,102,241,0.08),transparent);">' +
            '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' <span class="badge-installed">可配置</span></div>' +
            '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || pkg.id)) + '</div>' +
            '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '')) + capM + '</div>' +
            '<div class="card-tags">' + tagsM + '</div>' +
            '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm messenger-config-entry-btn">进入配置</button></div></div>';
        }
        if (pkg.id === 'ecommerce_publish_skill') {
          var tagsE = _skillStoreTagHtml(pkg.tags || []);
          var capE = pkg.capabilities_count ? ' · ' + pkg.capabilities_count + ' 个能力' : '';
          return '<div class="skill-store-card ecommerce-publish-card" style="cursor:pointer;border-color:rgba(251,146,60,0.35);background:linear-gradient(135deg,rgba(251,146,60,0.08),transparent);">' +
            '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' <span class="badge-installed">可配置</span></div>' +
            '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || pkg.id)) + '</div>' +
            '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '')) + capE + '</div>' +
            '<div class="card-tags">' + tagsE + '</div>' +
            '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm ecommerce-publish-entry-btn">管理店铺账号</button></div></div>';
        }
        if (pkg.id === 'wecom_reply') {
          var tags = _skillStoreTagHtml(pkg.tags || []);
          var capCount = pkg.capabilities_count ? ' · ' + pkg.capabilities_count + ' 个能力' : '';
          // 与 lobster 商店展示一致：仅「可配置」+ 配置按钮；算力解锁在点击时由服务器 wecom-config-eligible 判定
          return '<div class="skill-store-card wecom-reply-card" style="cursor:pointer;">' +
            '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' <span class="badge-installed">可配置</span></div>' +
            '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || pkg.id)) + '</div>' +
            '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '')) + capCount + '</div>' +
            '<div class="card-tags">' + tags + '</div>' +
            '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm wecom-config-entry-btn">配置</button></div></div>';
        }
        if (pkg.id === 'wewrite_official_account_skill') {
          var waTags = _skillStoreTagHtml(pkg.tags || []);
          var waCap = pkg.capabilities_count ? ' \u00b7 ' + pkg.capabilities_count + ' \u4e2a\u80fd\u529b' : '';
          return '<div class="skill-store-card wechat-article-card" data-skill-package-id="' + escapeAttr(pkg.id || '') + '" style="cursor:pointer;">' +
            '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' <span class="badge-installed">\u53ef\u914d\u7f6e</span></div>' +
            '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || '\u516c\u4f17\u53f7\u6587\u7ae0')) + '</div>' +
            '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '')) + waCap + '</div>' +
            '<div class="card-tags">' + waTags + '</div>' +
            '<div class="card-actions"><button type="button" class="btn btn-primary btn-sm wechat-article-entry-btn">\u53bb\u5bf9\u8bdd\u751f\u6210</button></div></div>';
        }
        var statusBadge = '';
        var actionBtn = '';
        if (pkg.status === 'installed') {
          statusBadge = '<span class="badge-installed">已安装</span>';
          actionBtn = pkg.default_installed ? '' : '<button type="button" class="btn btn-ghost btn-sm" data-uninstall="' + escapeAttr(pkg.id) + '">卸载</button>';
        } else if (pkg.status === 'coming_soon') {
          statusBadge = '<span class="badge-coming">即将推出</span>';
        } else {
          actionBtn = '<button type="button" class="btn btn-primary btn-sm" data-install="' + escapeAttr(pkg.id) + '">安装</button>';
          if (pkg.unlock_price_credits && !pkg.unlocked) {
            actionBtn = '<button type="button" class="btn btn-primary btn-sm" data-unlock-credits="' + escapeAttr(pkg.id) + '">算力解锁（' + (pkg.unlock_price_credits || 0) + '）</button> ' + actionBtn;
          }
        }
        var tags = _skillStoreTagHtml(pkg.tags || []);
          var capCount = pkg.capabilities_count ? ' · ' + pkg.capabilities_count + ' 个能力' : '';
        return '<div class="skill-store-card" data-skill-package-id="' + escapeAttr(pkg.id || '') + '">' +
          '<div class="card-label">' + debugBadge + escapeHtml(pkg.type || 'skill') + ' ' + statusBadge + '</div>' +
          '<div class="card-value">' + escapeHtml(_skillStoreBrandSafeText(pkg.name || pkg.id)) + '</div>' +
            '<div class="card-desc">' + escapeHtml(_skillStoreBrandSafeText(pkg.description || '')) + capCount + '</div>' +
          '<div class="card-tags">' + tags + '</div>' +
          '<div class="card-actions">' + actionBtn + '</div></div>';
      }).join('');
        el.innerHTML = html;
        _bindWecomConfigEntry();
        _bindMessengerCardEntry();
        _bindTwilioWhatsappCardEntry();
        _bindYoutubePublishCardEntry();
        _bindMetaSocialCardEntry();
        _bindSeedanceTvcCardEntry();
        _bindViralVideoRemixCardEntry();
        _bindCutcliTemplateCardEntry();
        _bindHiflyDigitalHumanCardEntry();
        _bindShanjianSmartClipCardEntry();
        _bindGoalVideoPipelineCardEntry();
        _bindIpContentStudioCardEntry();
        _bindLinkedinMiningCardEntry();
        _bindSocialLeadsCardEntry();
        _bindGlobalLeadsCardEntry();
        _bindAlibabaInquiriesCardEntry();
        _bindJuheWechatCardEntry();
        _bindWechatChannelsTranscriptCardEntry();
        _bindAi3dModelCardEntry();
        _bindWechatArticleCardEntry();
        _bindEcommerceDetailCardEntry();
        _bindEcommercePublishCardEntry();
        _bindOpenclawSkillWorkspaceCardEntry();
        _bindOpenclawMemoryCardEntry();
        _bindInstallUninstall(el);
        _bindXSkillConfigBtn();
        _bindComflyConfigBtn();
        _decorateSkillImageCards(el);
        _simplifySkillStoreCards(el);
      }

      function finishRender() {
        if (!pkgById('openclaw_weixin_channel')) {
          paintSkillStoreList();
          return;
        }
        var wxBase = _openclawWeixinResolveBase();
        if (!wxBase) {
          paintSkillStoreList();
          return;
        }
        _fetchWithTimeout(wxBase + '/api/openclaw/weixin-login/last', { headers: authHeaders() }, _SKILL_STORE_STATUS_TIMEOUT_MS)
          .then(function(r) { return r.json(); })
          .then(function(d) {
            _openclawWeixinLast = { last_ok: !!d.last_ok, at: d.at || null, detail: d.detail || '' };
            paintSkillStoreList();
          })
          .catch(function() { paintSkillStoreList(); });
      }

      var statusJobs = [
        _callbackJobWithTimeout('sutui', _loadXSkillStatus, _SKILL_STORE_STATUS_TIMEOUT_MS),
        _callbackJobWithTimeout('comfly', _loadComflyStatus, _SKILL_STORE_STATUS_TIMEOUT_MS)
      ];
      if (needYoutube) {
        statusJobs.push(_callbackJobWithTimeout('youtube', _loadYoutubePublishStatus, _SKILL_STORE_STATUS_TIMEOUT_MS));
      }
      if (typeof _loadMetaSocialStatus === 'function') {
        statusJobs.push(_callbackJobWithTimeout('meta-social', _loadMetaSocialStatus, _SKILL_STORE_STATUS_TIMEOUT_MS));
      }
      Promise.all(statusJobs).then(finishRender).catch(finishRender);
    })
    .catch(function() { el.innerHTML = '<p class="msg err">加载失败</p>'; });
}

// ── 企业微信：与 lobster 相同入口；online 仅在点击卡片/配置时多一步服务器 wecom-config-eligible ──

function _fetchWecomConfigEligible(serverBase) {
  return fetch(serverBase + '/skills/wecom-config-eligible', { headers: authHeaders() })
    .then(function(r) {
      if (r.status === 401) { throw new Error('401'); }
      if (!r.ok) throw new Error('eligible');
      return r.json();
    });
}

function _openWecomConfigIfUnlocked() {
  var base = (typeof API_BASE !== 'undefined' ? API_BASE : '');
  if (!base || typeof authHeaders !== 'function') {
    if (typeof showWecomConfigView === 'function') { location.hash = 'wecom-config'; showWecomConfigView(); }
    return;
  }
  function goConfig() {
    if (typeof showWecomConfigView === 'function') {
      location.hash = 'wecom-config';
      showWecomConfigView();
    }
  }
  _fetchWecomConfigEligible(base)
    .then(function(info) {
      if (info && info.allowed) {
        goConfig();
        return;
      }
      var amount = (info && info.amount_credits) || 1000;
      var pkgId = (info && info.package_id) || 'wecom_reply';
      var msg = (info && info.detail) || ('「企业微信自动回复」需 ' + amount + ' 算力解锁后才能管理本地配置。');
      if (!confirm(msg + '\n\n是否现在解锁？')) return;
      return fetch(base + '/skills/unlock-by-credits', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ package_id: pkgId })
      })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
        .then(function(x) {
          if (!x.ok) {
            alert((x.data && x.data.detail) || '解锁失败');
            return;
          }
          alert(x.data.message || '解锁成功');
          if (typeof loadSkillStore === 'function') loadSkillStore();
          return _fetchWecomConfigEligible(base);
        })
        .then(function(info2) {
          if (!info2) return;
          if (!info2.allowed) {
            alert('解锁后仍无权限，请刷新后重试');
            return;
          }
          goConfig();
        });
    })
    .catch(function(e) {
      if (e && e.message === '401') { alert('请先登录'); return; }
      alert('无法连接服务器校验企微权限，请稍后重试');
    });
}

function _bindYoutubePublishCardEntry() {
  document.querySelectorAll('.youtube-publish-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      window._openYoutubeAccountsView();
    });
  });
  document.querySelectorAll('.youtube-publish-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      window._openYoutubeAccountsView();
    });
  });
}

function _bindTwilioWhatsappCardEntry() {
  document.querySelectorAll('.twilio-whatsapp-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      _openTwilioWhatsappDetailView();
    });
  });
  document.querySelectorAll('.twilio-whatsapp-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      _openTwilioWhatsappConfigView();
    });
  });
}

function _bindMessengerCardEntry() {
  document.querySelectorAll('.messenger-reply-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      _openMessengerConfigView();
    });
  });
  document.querySelectorAll('.messenger-config-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      _openMessengerConfigView();
    });
  });
}

function _bindWecomConfigEntry() {
  document.querySelectorAll('.wecom-reply-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      _openWecomConfigIfUnlocked();
    });
  });
  document.querySelectorAll('.wecom-config-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      _openWecomConfigIfUnlocked();
    });
  });
}

function _bindEcommercePublishCardEntry() {
  document.querySelectorAll('.ecommerce-publish-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      _navigateToEcommerceAccounts();
    });
  });
  document.querySelectorAll('.ecommerce-publish-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      _navigateToEcommerceAccounts();
    });
  });
}

function _bindWechatArticleCardEntry() {
  document.querySelectorAll('.wechat-article-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      _openWechatArticleChatFlow();
    });
  });
  document.querySelectorAll('.wechat-article-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      _openWechatArticleChatFlow();
    });
  });
}

function _openWechatArticleChatFlow() {
  var prompt = window.WECHAT_ARTICLE_CHAT_PROMPT || '帮我写个公众号文章(已配置)，自动生成3张16:9横屏配图，完成公众号排版，并推送到草稿箱。主题是：';
  if (typeof window.showAppView === 'function') {
    window.showAppView('chat');
  } else {
    var chatNav = document.querySelector('.nav-left-item[data-view="chat"]');
    if (chatNav) chatNav.click();
  }
  setTimeout(function() {
    if (typeof window.fillChatPromptInput === 'function') {
      window.fillChatPromptInput(prompt);
      return;
    }
    var input = document.getElementById('chatInput');
    if (!input) return;
    input.value = prompt;
    input.focus();
    if (typeof input.setSelectionRange === 'function') input.setSelectionRange(prompt.length, prompt.length);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, 0);
}

function _navigateToEcommerceAccounts() {
  var publishTab = document.querySelector('[data-view="publish"]');
  if (publishTab) publishTab.click();
  setTimeout(function() {
    var filter = document.getElementById('accountPlatformFilter');
    if (filter) {
      filter.value = 'douyin_shop';
      filter.dispatchEvent(new Event('change'));
    }
  }, 300);
}

// ── Token 配置 Modal ──────────────────────────────────────────────

function _bindEcommerceDetailCardEntry() {
  document.querySelectorAll('.ecommerce-detail-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openEcommerceDetailStudioView === 'function') window._openEcommerceDetailStudioView();
    });
  });
  document.querySelectorAll('.ecommerce-detail-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openEcommerceDetailStudioView === 'function') window._openEcommerceDetailStudioView();
    });
  });
}

function _bindSeedanceTvcCardEntry() {
  document.querySelectorAll('.seedance-tvc-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openSeedanceTvcStudioView === 'function') window._openSeedanceTvcStudioView();
    });
  });
  document.querySelectorAll('.seedance-tvc-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openSeedanceTvcStudioView === 'function') window._openSeedanceTvcStudioView();
    });
  });
}

function _bindViralVideoRemixCardEntry() {
  document.querySelectorAll('.viral-video-remix-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openViralVideoRemixView === 'function') window._openViralVideoRemixView();
    });
  });
  document.querySelectorAll('.viral-video-remix-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openViralVideoRemixView === 'function') window._openViralVideoRemixView();
    });
  });
}

function _bindHiflyDigitalHumanCardEntry() {
  document.querySelectorAll('.hifly-digital-human-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openHiflyDigitalHumanView === 'function') window._openHiflyDigitalHumanView();
    });
  });
  document.querySelectorAll('.hifly-digital-human-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openHiflyDigitalHumanView === 'function') window._openHiflyDigitalHumanView();
    });
  });
}

function _bindShanjianSmartClipCardEntry() {
  document.querySelectorAll('.shanjian-smart-clip-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openShanjianSmartClipView === 'function') window._openShanjianSmartClipView();
    });
  });
  document.querySelectorAll('.shanjian-smart-clip-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openShanjianSmartClipView === 'function') window._openShanjianSmartClipView();
    });
  });
}

function _bindCutcliTemplateCardEntry() {
  document.querySelectorAll('.cutcli-template-card').forEach(function(card) {
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openCutcliTemplateStudioView === 'function') window._openCutcliTemplateStudioView();
    });
  });
  document.querySelectorAll('.cutcli-template-entry-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openCutcliTemplateStudioView === 'function') window._openCutcliTemplateStudioView();
    });
  });
}

function _bindGoalVideoPipelineCardEntry() {
  document.querySelectorAll('.goal-video-pipeline-card, [data-skill-package-id="goal_video_pipeline_skill"]').forEach(function(card) {
    if (card.dataset.creativeFilmEntryBound === '1') return;
    card.dataset.creativeFilmEntryBound = '1';
    card.style.cursor = 'pointer';
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openCreativeFilmStudioView === 'function') window._openCreativeFilmStudioView();
    });
  });
  document.querySelectorAll('.goal-video-chat-entry-btn').forEach(function(btn) {
    if (btn.dataset.creativeFilmEntryBound === '1') return;
    btn.dataset.creativeFilmEntryBound = '1';
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openCreativeFilmStudioView === 'function') window._openCreativeFilmStudioView();
    });
  });
}

function _bindIpContentStudioCardEntry() {
  document.querySelectorAll('.ip-content-studio-card, [data-skill-package-id="ip_content_daily_skill"]').forEach(function(card) {
    if (card.dataset.ipContentEntryBound === '1') return;
    card.dataset.ipContentEntryBound = '1';
    card.style.cursor = 'pointer';
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openIpContentStudioView === 'function') window._openIpContentStudioView();
    });
  });
  document.querySelectorAll('.ip-content-studio-entry-btn').forEach(function(btn) {
    if (btn.dataset.ipContentEntryBound === '1') return;
    btn.dataset.ipContentEntryBound = '1';
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openIpContentStudioView === 'function') window._openIpContentStudioView();
    });
  });
}

function _bindLinkedinMiningCardEntry() {
  document.querySelectorAll('.linkedin-mining-card, [data-skill-package-id="linkedin_mining_skill"]').forEach(function(card) {
    if (card.dataset.linkedinMiningEntryBound === '1') return;
    card.dataset.linkedinMiningEntryBound = '1';
    card.style.cursor = 'pointer';
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openLinkedinMiningView === 'function') window._openLinkedinMiningView();
    });
  });
  document.querySelectorAll('.linkedin-mining-entry-btn').forEach(function(btn) {
    if (btn.dataset.linkedinMiningEntryBound === '1') return;
    btn.dataset.linkedinMiningEntryBound = '1';
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openLinkedinMiningView === 'function') window._openLinkedinMiningView();
    });
  });
}

function _bindSocialLeadsCardEntry() {
  document.querySelectorAll('.social-leads-card, [data-skill-package-id="reddit_leads"], [data-skill-package-id="x_leads"], [data-skill-package-id="tiktok_leads"]').forEach(function(card) {
    if (card.dataset.socialLeadsEntryBound === '1') return;
    card.dataset.socialLeadsEntryBound = '1';
    card.style.cursor = 'pointer';
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      var packageId = card.getAttribute('data-skill-package-id') || '';
      var platform = card.getAttribute('data-social-leads-platform') || (packageId === 'x_leads' ? 'x' : (packageId === 'tiktok_leads' ? 'tiktok' : 'reddit'));
      if (typeof window._openSocialLeadsView === 'function') window._openSocialLeadsView(platform);
    });
  });
  document.querySelectorAll('.social-leads-entry-btn').forEach(function(btn) {
    if (btn.dataset.socialLeadsEntryBound === '1') return;
    btn.dataset.socialLeadsEntryBound = '1';
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var platform = btn.getAttribute('data-social-leads-platform') || 'reddit';
      if (typeof window._openSocialLeadsView === 'function') window._openSocialLeadsView(platform);
    });
  });
}

function _bindGlobalLeadsCardEntry() {
  document.querySelectorAll('.global-leads-card, [data-skill-package-id="global_trade_leads_skill"]').forEach(function(card) {
    if (card.dataset.globalLeadsEntryBound === '1') return;
    card.dataset.globalLeadsEntryBound = '1';
    card.style.cursor = 'pointer';
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openGlobalLeadsView === 'function') window._openGlobalLeadsView();
    });
  });
  document.querySelectorAll('.global-leads-entry-btn').forEach(function(btn) {
    if (btn.dataset.globalLeadsEntryBound === '1') return;
    btn.dataset.globalLeadsEntryBound = '1';
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openGlobalLeadsView === 'function') window._openGlobalLeadsView();
    });
  });
}

function _bindAlibabaInquiriesCardEntry() {
  document.querySelectorAll('.alibaba-inquiries-card, [data-skill-package-id="alibaba_inquiry_takeover_skill"]').forEach(function(card) {
    if (card.dataset.alibabaInquiriesEntryBound === '1') return;
    card.dataset.alibabaInquiriesEntryBound = '1';
    card.style.cursor = 'pointer';
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openAlibabaInquiriesView === 'function') window._openAlibabaInquiriesView();
    });
  });
  document.querySelectorAll('.alibaba-inquiries-entry-btn').forEach(function(btn) {
    if (btn.dataset.alibabaInquiriesEntryBound === '1') return;
    btn.dataset.alibabaInquiriesEntryBound = '1';
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openAlibabaInquiriesView === 'function') window._openAlibabaInquiriesView();
    });
  });
}

function _bindJuheWechatCardEntry() {
  document.querySelectorAll('.juhe-wechat-card, [data-skill-package-id="juhe_wechat_skill"]').forEach(function(card) {
    if (card.dataset.juheWechatEntryBound === '1') return;
    card.dataset.juheWechatEntryBound = '1';
    card.style.cursor = 'pointer';
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openJuheWechatView === 'function') window._openJuheWechatView();
    });
  });
  document.querySelectorAll('.juhe-wechat-entry-btn').forEach(function(btn) {
    if (btn.dataset.juheWechatEntryBound === '1') return;
    btn.dataset.juheWechatEntryBound = '1';
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openJuheWechatView === 'function') window._openJuheWechatView();
    });
  });
}

function _bindWechatChannelsTranscriptCardEntry() {
  document.querySelectorAll('.wechat-channels-transcript-card, [data-skill-package-id="wechat_channels_transcript_skill"]').forEach(function(card) {
    if (card.dataset.wechatChannelsTranscriptEntryBound === '1') return;
    card.dataset.wechatChannelsTranscriptEntryBound = '1';
    card.style.cursor = 'pointer';
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openWechatChannelsTranscriptView === 'function') window._openWechatChannelsTranscriptView();
    });
  });
  document.querySelectorAll('.wechat-channels-transcript-entry-btn').forEach(function(btn) {
    if (btn.dataset.wechatChannelsTranscriptEntryBound === '1') return;
    btn.dataset.wechatChannelsTranscriptEntryBound = '1';
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openWechatChannelsTranscriptView === 'function') window._openWechatChannelsTranscriptView();
    });
  });
}

function _bindAi3dModelCardEntry() {
  document.querySelectorAll('.ai3d-model-card, [data-skill-package-id="ai_3d_model_skill"]').forEach(function(card) {
    if (card.dataset.ai3dEntryBound === '1') return;
    card.dataset.ai3dEntryBound = '1';
    card.style.cursor = 'pointer';
    card.addEventListener('click', function(e) {
      if (e.target.closest('.card-actions')) return;
      if (typeof window._openAi3dModelView === 'function') window._openAi3dModelView();
    });
  });
  document.querySelectorAll('.ai3d-model-entry-btn').forEach(function(btn) {
    if (btn.dataset.ai3dEntryBound === '1') return;
    btn.dataset.ai3dEntryBound = '1';
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (typeof window._openAi3dModelView === 'function') window._openAi3dModelView();
    });
  });
}

function _bindComflyConfigBtn__legacy_unused() {
  document.querySelectorAll('#comflyConfigBtn, .js-comfly-config-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var modal = document.getElementById('comflyModal');
      var keyInput = document.getElementById('comflyApiKeyInput');
      var baseInput = document.getElementById('comflyApiBaseInput');
      if (!modal) return;
      if (keyInput) {
        keyInput.value = '';
        keyInput.placeholder = _comflyStatus.has_user_key
          ? '已保存(' + (_comflyStatus.masked_user_key || '……') + ')，输入新 Key 可覆盖'
          : '粘贴视频引擎 API Key';
      }
      if (baseInput) {
        baseInput.value = _comflyStatus.user_api_base || '';
        baseInput.placeholder = _comflyStatus.default_api_base_hint || 'https://ai.comfly.org';
      }
      var msgEl = document.getElementById('comflyModalMsg');
      if (msgEl) { msgEl.style.display = 'none'; msgEl.textContent = ''; }
      modal.classList.add('visible');
    });
  });
  return;
  document.querySelectorAll('#comflyConfigBtn, .js-comfly-config-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var modal = document.getElementById('comflyModal');
      var keyInput = document.getElementById('comflyApiKeyInput');
      var baseInput = document.getElementById('comflyApiBaseInput');
      if (!modal) return;
    if (keyInput) {
      keyInput.value = '';
      keyInput.placeholder = _comflyStatus.has_user_key
        ? '已保存 (' + (_comflyStatus.masked_user_key || '••••') + ')，输入新 Key 可覆盖'
        : '粘贴视频引擎 API Key';
    }
    if (baseInput) {
      baseInput.value = _comflyStatus.user_api_base || '';
      baseInput.placeholder = _comflyStatus.default_api_base_hint || 'https://ai.comfly.org';
    }
    var msgEl = document.getElementById('comflyModalMsg');
    if (msgEl) { msgEl.style.display = 'none'; msgEl.textContent = ''; }
      modal.classList.add('visible');
    });
  });
}

function _bindXSkillConfigBtn() {
  var modelsBtn = document.getElementById('xskillModelsBtn');
  if (modelsBtn) modelsBtn.addEventListener('click', function() {
    if (typeof window._openXSkillModelsModal === 'function') window._openXSkillModelsModal();
  });
  if (EDITION === 'online') return;
  var btn = document.getElementById('xskillConfigBtn');
  if (!btn) return;
  btn.addEventListener('click', function() {
    var modal = document.getElementById('xskillModal');
    var tokenInput = document.getElementById('xskillTokenInput');
    var urlInput = document.getElementById('xskillUrlInput');
    if (!modal) return;
    if (tokenInput) { tokenInput.value = ''; tokenInput.placeholder = _xskillStatus.has_token ? '已配置 (' + _xskillStatus.token + ')' : 'sk-...'; }
    if (urlInput) urlInput.value = _xskillStatus.url || '';
    modal.classList.add('visible');
  });
}

function _openComflyConfigModal() {
  var modal = document.getElementById('comflyModal');
  var keyInput = document.getElementById('comflyApiKeyInput');
  if (!modal) return;
  if (keyInput) {
    keyInput.value = '';
    keyInput.placeholder = _comflyStatus.has_user_key
      ? '已保存 Key（' + (_comflyStatus.masked_user_key || '已脱敏') + '），输入新 Key 可覆盖'
      : '粘贴视频引擎 API Key';
  }
  var msgEl = document.getElementById('comflyModalMsg');
  if (msgEl) {
    msgEl.style.display = 'none';
    msgEl.textContent = '';
  }
  modal.classList.add('visible');
}
window._openComflyConfigModal = _openComflyConfigModal;

function _bindComflyConfigBtn() {
  document.querySelectorAll('#comflyConfigBtn, .js-comfly-config-btn').forEach(function(btn) {
    if (btn.dataset.comflyBound === '1') return;
    btn.dataset.comflyBound = '1';
    btn.addEventListener('click', function() {
      _openComflyConfigModal();
    });
  });
}

(function _bindComflyConfigBtnDelegated() {
  if (window.__comflyConfigDelegatedBound) return;
  window.__comflyConfigDelegatedBound = true;
  document.addEventListener('click', function(e) {
    var btn = e.target && e.target.closest ? e.target.closest('#comflyConfigBtn, .js-comfly-config-btn') : null;
    if (!btn) return;
    e.preventDefault();
    _openComflyConfigModal();
  });
})();

(function _initXSkillModal() {
  var modal = document.getElementById('xskillModal');
  if (!modal) return;
  var cancelBtn = document.getElementById('xskillModalCancel');
  var saveBtn = document.getElementById('xskillModalSave');

  function closeModal() { modal.classList.remove('visible'); }

  if (cancelBtn) cancelBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', function(e) { if (e.target === modal) closeModal(); });

  if (saveBtn) saveBtn.addEventListener('click', function() {
    var tokenInput = document.getElementById('xskillTokenInput');
    var urlInput = document.getElementById('xskillUrlInput');
    var msgEl = document.getElementById('xskillModalMsg');
    var body = {};
    if (tokenInput && tokenInput.value.trim()) body.token = tokenInput.value.trim();
    if (urlInput && urlInput.value.trim()) body.url = urlInput.value.trim();
    if (!body.token && !_xskillStatus.has_token) {
      if (msgEl) { msgEl.textContent = '请输入 Token'; msgEl.className = 'msg err'; msgEl.style.display = ''; }
      return;
    }
    saveBtn.disabled = true; saveBtn.textContent = '保存中…';
    fetch((LOCAL_API_BASE || '') + '/api/sutui/config', {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify(body)
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (x.ok) {
          if (msgEl) { msgEl.textContent = '保存成功'; msgEl.className = 'msg'; msgEl.style.display = ''; }
          setTimeout(function() { closeModal(); loadSkillStore(); }, 600);
        } else {
          if (msgEl) { msgEl.textContent = x.data.detail || '保存失败'; msgEl.className = 'msg err'; msgEl.style.display = ''; }
        }
      })
      .catch(function() { if (msgEl) { msgEl.textContent = '网络错误'; msgEl.className = 'msg err'; msgEl.style.display = ''; } })
      .finally(function() { saveBtn.disabled = false; saveBtn.textContent = '保存'; });
  });
})();

// ── 模型与定价 Modal ──────────────────────────────────────
(function _initXSkillModelsModal() {
  var modal = document.getElementById('xskillModelsModal');
  if (!modal) return;
  var closeBtn = document.getElementById('xskillModelsClose');
  var body = document.getElementById('xskillModelsBody');
  var tabsWrap = document.getElementById('xskillModelsTabs');
  var searchInput = document.getElementById('xskillModelsSearch');
  var pagerWrap = document.getElementById('xskillModelsPager');
  var _data = null;
  var _activeCat = 'all';
  var _query = '';
  var _page = 1;
  var PAGE_SIZE = 15;

  function close() { modal.classList.remove('visible'); }
  if (closeBtn) closeBtn.addEventListener('click', close);
  modal.addEventListener('click', function(e) { if (e.target === modal) close(); });

  var _TASK_LABEL = { t2v:'文生视频', i2v:'图生视频', t2i:'文生图', i2i:'图生图/编辑',
    v2v:'视频转视频', t2a:'语音合成', stt:'语音转文字', a2a:'音频处理', chat:'对话' };

  function _priceShort(p) {
    if (!p) return '—';
    if (p.default_credits > 0) return p.default_credits + ' 算力';
    if (p.base_price_user > 0) return p.base_price_user + ' 算力/' + (p.price_type === 'duration_based' ? '秒' : '次');
    var exs = (p.examples || []).filter(function(e) { return e.price > 0; });
    if (exs.length > 0) return exs[0].price + ' 算力';
    return '—';
  }

  function _priceTooltip(p) {
    if (!p) return '';
    var lines = [];
    if (p.price_type) lines.push('计费方式: ' + p.price_type);
    if (p.base_price_user) lines.push('基础单价: ' + p.base_price_user + ' 算力');
    if (p.default_credits) lines.push('默认参数估价: ' + p.default_credits + ' 算力');
    var exs = (p.examples || []).filter(function(e) { return e.price > 0; });
    if (exs.length > 0) {
      lines.push('');
      exs.forEach(function(e) { lines.push((e.description || '默认') + ': ' + e.price + ' 算力'); });
    }
    return lines.join('\n');
  }

  function _filtered() {
    if (!_data) return [];
    var list = _activeCat === 'all' ? _data : _data.filter(function(m) { return m.category === _activeCat; });
    if (_query) {
      var q = _query.toLowerCase();
      list = list.filter(function(m) { return (m.name || '').toLowerCase().indexOf(q) >= 0 || (m.id || '').toLowerCase().indexOf(q) >= 0; });
    }
    return list;
  }

  function render() {
    if (!_data) { body.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text-muted);">无数据</div>'; if (pagerWrap) pagerWrap.innerHTML = ''; return; }
    var list = _filtered();
    var totalPages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    if (_page > totalPages) _page = totalPages;
    var start = (_page - 1) * PAGE_SIZE;
    var pageItems = list.slice(start, start + PAGE_SIZE);
    if (!list.length) { body.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text-muted);">无匹配模型</div>'; if (pagerWrap) pagerWrap.innerHTML = ''; return; }

    var html = '<table style="width:100%;border-collapse:collapse;font-size:0.82rem;"><thead><tr style="border-bottom:2px solid var(--border);text-align:left;">'
      + '<th style="padding:0.5rem 0.4rem;">模型名称</th>'
      + '<th style="padding:0.5rem 0.4rem;">会话中输入</th>'
      + '<th style="padding:0.5rem 0.4rem;">类型</th>'
      + '<th style="padding:0.5rem 0.4rem;min-width:80px;">定价</th>'
      + '</tr></thead><tbody>';
    pageItems.forEach(function(m) {
      var badges = '';
      if (m.isHot) badges += '<span style="background:rgba(239,68,68,0.15);color:#f87171;font-size:0.68rem;padding:0.1rem 0.35rem;border-radius:4px;margin-left:0.3rem;">热门</span>';
      if (m.isNew) badges += '<span style="background:rgba(34,197,94,0.15);color:#4ade80;font-size:0.68rem;padding:0.1rem 0.35rem;border-radius:4px;margin-left:0.3rem;">新</span>';
      var tip = _priceTooltip(m.pricing);
      var short = _priceShort(m.pricing);
      var priceTd = tip
        ? '<span class="xm-price-tip" title="' + tip.replace(/"/g, '&quot;') + '" style="cursor:help;border-bottom:1px dashed var(--text-muted);">' + short + '</span>'
        : '<span style="color:var(--text-muted)">' + short + '</span>';
      html += '<tr style="border-bottom:1px solid var(--border);">'
        + '<td style="padding:0.45rem 0.4rem;font-weight:500;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + m.name + badges + '</td>'
        + '<td style="padding:0.45rem 0.4rem;"><code style="background:rgba(6,182,212,0.1);padding:0.15rem 0.4rem;border-radius:4px;font-size:0.75rem;user-select:all;cursor:text;word-break:break-all;">' + m.id + '</code></td>'
        + '<td style="padding:0.45rem 0.4rem;color:var(--text-muted);white-space:nowrap;">' + (_TASK_LABEL[m.task_type] || m.task_type) + '</td>'
        + '<td style="padding:0.45rem 0.4rem;font-size:0.78rem;">' + priceTd + '</td>'
        + '</tr>';
    });
    html += '</tbody></table>';
    body.innerHTML = html;

    if (pagerWrap) {
      if (totalPages <= 1) { pagerWrap.innerHTML = '<span>' + list.length + ' 个模型</span>'; return; }
      var ph = '<span>' + list.length + ' 个模型</span><span style="display:flex;gap:0.3rem;align-items:center;">';
      ph += '<button type="button" class="btn btn-ghost btn-sm xm-pg" data-pg="' + Math.max(1, _page - 1) + '"' + (_page <= 1 ? ' disabled' : '') + '>&laquo;</button>';
      for (var i = 1; i <= totalPages; i++) {
        if (totalPages > 7 && i > 2 && i < totalPages - 1 && Math.abs(i - _page) > 1) {
          if (i === 3 || i === totalPages - 2) ph += '<span style="padding:0 0.2rem;">…</span>';
          continue;
        }
        ph += '<button type="button" class="btn btn-sm xm-pg' + (i === _page ? ' xm-tab active' : ' btn-ghost') + '" data-pg="' + i + '">' + i + '</button>';
      }
      ph += '<button type="button" class="btn btn-ghost btn-sm xm-pg" data-pg="' + Math.min(totalPages, _page + 1) + '"' + (_page >= totalPages ? ' disabled' : '') + '>&raquo;</button>';
      ph += '</span>';
      pagerWrap.innerHTML = ph;
    }
  }

  if (pagerWrap) pagerWrap.addEventListener('click', function(e) {
    var btn = e.target.closest('.xm-pg');
    if (!btn || btn.disabled) return;
    _page = parseInt(btn.dataset.pg) || 1;
    render();
    body.scrollTop = 0;
  });

  if (tabsWrap) tabsWrap.addEventListener('click', function(e) {
    var tab = e.target.closest('.xm-tab');
    if (!tab || tab.classList.contains('xm-pg')) return;
    _activeCat = tab.dataset.cat || 'all';
    _page = 1;
    tabsWrap.querySelectorAll('.xm-tab:not(.xm-pg)').forEach(function(t) { t.classList.toggle('active', t === tab); });
    render();
  });

  var _searchTimer = null;
  if (searchInput) searchInput.addEventListener('input', function() {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(function() {
      _query = (searchInput.value || '').trim();
      _page = 1;
      render();
    }, 200);
  });

  window._openXSkillModelsModal = function() {
    modal.classList.add('visible');
    if (_data) return;
    body.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text-muted);">加载中…</div>';
    fetch((LOCAL_API_BASE || '') + '/api/sutui/models', { headers: authHeaders() })
      .then(function(r) { return r.json(); })
      .then(function(d) { _data = d.models || d; render(); })
      .catch(function() { body.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text-muted);">加载失败</div>'; });
  };
})();

(function _initComflyModal() {
  var modal = document.getElementById('comflyModal');
  if (!modal) return;
  var cancelBtn = document.getElementById('comflyModalCancel');
  var saveBtn = document.getElementById('comflyModalSave');

  function closeModal() { modal.classList.remove('visible'); }

  if (cancelBtn) cancelBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', function(e) { if (e.target === modal) closeModal(); });

  if (saveBtn) saveBtn.addEventListener('click', function() {
    var keyInput = document.getElementById('comflyApiKeyInput');
    var msgEl = document.getElementById('comflyModalMsg');
    var k = keyInput ? keyInput.value.trim() : '';
    var body = {};
    if (k) body.api_key = k;
    else if (!_comflyStatus.has_user_key) {
      if (msgEl) { msgEl.textContent = '请填写视频引擎 API Key'; msgEl.className = 'msg err'; msgEl.style.display = ''; }
      return;
    }
    saveBtn.disabled = true; saveBtn.textContent = '保存中…';
    fetch((LOCAL_API_BASE || '') + '/api/comfly/config', {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify(body)
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, status: r.status, data: d }; }); })
      .then(function(x) {
        if (x.ok) {
          if (msgEl) { msgEl.textContent = '已保存。当前用户的电商套图、图片工作台和爆款TVC 会优先共用这份视频引擎配置；这里只做本机保存，不会在保存时校验上游凭据。'; msgEl.className = 'msg'; msgEl.style.display = ''; }
          setTimeout(function() { closeModal(); loadSkillStore(); }, 500);
        } else {
          var det = x.data && (x.data.detail || x.data.message);
          if (x.status === 401 && msgEl) {
            msgEl.textContent = '当前软件登录态已失效，请重新登录后再保存。这不是视频引擎 Key 校验失败。';
            msgEl.className = 'msg err';
            msgEl.style.display = '';
            return;
          }
          if (msgEl) { msgEl.textContent = det || '保存失败'; msgEl.className = 'msg err'; msgEl.style.display = ''; }
        }
      })
      .catch(function() { if (msgEl) { msgEl.textContent = '网络错误'; msgEl.className = 'msg err'; msgEl.style.display = ''; } })
      .finally(function() { saveBtn.disabled = false; saveBtn.textContent = '保存'; });
  });
})();

/** 在线版：认证在公网 API_BASE，MCP/剪辑走本机 LOCAL_API_BASE；安装技能须在两端写入（服务端记账 + 本机 CapabilityConfig/catalog）。 */
function _localSkillApiBase() {
  var l = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  var a = (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
  if (!l || l === a) return '';
  return l;
}

function _syncLocalSkillInstall(pkgId) {
  var base = _localSkillApiBase();
  if (!base) return Promise.resolve({ ok: true, skipped: true });
  return fetch(base + '/skills/install', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ package_id: pkgId })
  }).then(function(r) {
    return r.json().then(function(d) { return { ok: r.ok, data: d, status: r.status }; });
  });
}

function _syncLocalSkillUninstall(pkgId) {
  var base = _localSkillApiBase();
  if (!base) return Promise.resolve({ ok: true, skipped: true });
  return fetch(base + '/skills/uninstall', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ package_id: pkgId })
  }).then(function(r) {
    return r.json().then(function(d) { return { ok: r.ok, data: d, status: r.status }; });
  });
}

function _bindInstallUninstall(el) {
      el.querySelectorAll('button[data-unlock-credits]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var pkgId = btn.getAttribute('data-unlock-credits');
          btn.disabled = true; btn.textContent = '解锁中…';
          fetch(API_BASE + '/skills/unlock-by-credits', { method: 'POST', headers: authHeaders(), body: JSON.stringify({ package_id: pkgId }) })
            .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
            .then(function(x) {
              if (x.ok) {
                alert((x.data.message || '解锁成功') + '\n\n请再点击「安装」，以在本机注册能力（含素材剪辑 media.edit，供 MCP 使用）。');
                loadSkillStore();
              } else { alert(x.data.detail || '解锁失败'); btn.disabled = false; btn.textContent = '算力解锁'; }
            }).catch(function() { alert('网络错误'); btn.disabled = false; btn.textContent = '算力解锁'; });
        });
      });
      el.querySelectorAll('button[data-install]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var pkgId = btn.getAttribute('data-install');
          btn.disabled = true; btn.textContent = '安装中…';
          fetch(API_BASE + '/skills/install', { method: 'POST', headers: authHeaders(), body: JSON.stringify({ package_id: pkgId }) })
            .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
            .then(function(x) {
              if (!x.ok) {
                alert(x.data.detail || '安装失败');
                btn.disabled = false; btn.textContent = '安装';
                return;
              }
              return _syncLocalSkillInstall(pkgId).then(function(loc) {
                var msg = x.data.message || '安装成功';
                if (loc.skipped) {
                  alert(msg);
                } else if (!loc.ok) {
                  alert(msg + '\n\n【本机未同步】' + ((loc.data && loc.data.detail) || ('HTTP ' + loc.status)) + '\n请确认本机 lobster_online 后端已运行，再对同一技能点一次「安装」。');
                } else {
                  alert(msg + (loc.data && loc.data.already_installed ? '\n（本机能力已就绪）' : '\n（本机已注册能力）'));
                }
                loadSkillStore();
              }).catch(function() {
                alert((x.data.message || '服务端已安装') + '\n\n本机同步请求失败：请确认本机后端已启动，再点一次「安装」。');
                loadSkillStore();
              });
            }).catch(function() { alert('网络错误'); btn.disabled = false; btn.textContent = '安装'; });
        });
      });
      el.querySelectorAll('button[data-uninstall]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var pkgId = btn.getAttribute('data-uninstall');
          if (!confirm('确定卸载 ' + pkgId + '？')) return;
          btn.disabled = true; btn.textContent = '卸载中…';
          fetch(API_BASE + '/skills/uninstall', { method: 'POST', headers: authHeaders(), body: JSON.stringify({ package_id: pkgId }) })
            .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
            .then(function(x) {
              if (!x.ok) {
                alert(x.data.detail || '卸载失败');
                btn.disabled = false; btn.textContent = '卸载';
                return;
              }
              return _syncLocalSkillUninstall(pkgId).then(function(loc) {
                var baseMsg = x.data.message || '卸载成功';
                alert(baseMsg + ((loc.ok || loc.skipped) ? '' : '\n（本机卸载未完全同步，可再点卸载或重启本机后端）'));
                loadSkillStore();
              }).catch(function() {
                alert('服务端已卸载；本机同步失败，请稍后重试卸载或重启本机服务。');
                loadSkillStore();
              });
            }).catch(function() { alert('网络错误'); btn.disabled = false; btn.textContent = '卸载'; });
        });
      });
}

// Add MCP Modal
function bindAddMcpModal() {
  var modal = document.getElementById('addMcpModal');
  var openBtn = document.getElementById('openAddMcpModal');
  var cancelBtn = document.getElementById('addMcpModalCancel');
  var addBtn = document.getElementById('addMcpBtn');
  if (!modal) return;

  function closeModal() { modal.classList.remove('visible'); }

  if (openBtn && !openBtn._addMcpModalBound) {
    openBtn._addMcpModalBound = true;
    openBtn.addEventListener('click', function() {
      var nameInput = document.getElementById('addMcpName');
      var urlInput = document.getElementById('addMcpUrl');
      var msgEl = document.getElementById('addMcpMsg');
      if (nameInput) nameInput.value = '';
      if (urlInput) urlInput.value = '';
      if (msgEl) msgEl.style.display = 'none';
      modal.classList.add('visible');
    });
  }
  if (cancelBtn && !cancelBtn._addMcpModalBound) {
    cancelBtn._addMcpModalBound = true;
    cancelBtn.addEventListener('click', closeModal);
  }
  if (!modal._addMcpMaskBound) {
    modal._addMcpMaskBound = true;
    modal.addEventListener('click', function(e) { if (e.target === modal) closeModal(); });
  }

  if (addBtn && !addBtn._addMcpModalBound) {
    addBtn._addMcpModalBound = true;
    addBtn.addEventListener('click', function() {
    var nameInput = document.getElementById('addMcpName');
    var urlInput = document.getElementById('addMcpUrl');
    var msgEl = document.getElementById('addMcpMsg');
    var name = (nameInput.value || '').trim();
    var url = (urlInput.value || '').trim();
    if (!name || !url) { showMsg(msgEl, '请填写名称和 URL', true); return; }
    addBtn.disabled = true;
    fetch((LOCAL_API_BASE || '') + '/skills/add-mcp', {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ name: name, url: url })
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (x.ok) {
          showMsg(msgEl, x.data.message || 'MCP 已添加', false);
          setTimeout(function() { closeModal(); loadSkillStore(); }, 600);
        } else { showMsg(msgEl, x.data.detail || '添加失败', true); }
      })
      .catch(function() { showMsg(msgEl, '网络错误', true); })
      .finally(function() { addBtn.disabled = false; });
    });
  }
}

function bindSkillStoreRefreshButton() {
  var refreshStoreBtn = document.getElementById('refreshStoreBtn');
  if (refreshStoreBtn && !refreshStoreBtn._skillStoreRefreshBound) {
    refreshStoreBtn._skillStoreRefreshBound = true;
    refreshStoreBtn.addEventListener('click', function() {
      loadSkillStore();
      if (STORE_OFFICIAL_TAB_ENABLED && _currentStoreTab === 'official') browseOfficialPage(_officialPage);
    });
  }
}

// ── 官方在线 Tab: paginated browsing + cached search ───────────────

var _officialPage = 1;
var _officialHasNext = false;
var _officialLoaded = false;
var _activeCategory = null;
var _searchMode = false;

var CATEGORY_LABELS = {
  image: '图片', video: '视频', audio: '音频', database: '数据库',
  search: '搜索/爬虫', code: '代码/Git', file: '文件', ai: 'AI/LLM',
  communication: '通讯', devops: 'DevOps'
};

function renderCategoryBar(categories) {
  var bar = document.getElementById('mcpCategoryBar');
  if (!bar || !categories) return;
  var keys = Object.keys(categories);
  if (!keys.length) { bar.innerHTML = ''; return; }

  var html = '<span class="category-chip' + (!_activeCategory ? ' active' : '') + '" data-cat="">全部</span>';
  keys.forEach(function(cat) {
    var label = CATEGORY_LABELS[cat] || cat;
    var active = (_activeCategory === cat) ? ' active' : '';
    html += '<span class="category-chip' + active + '" data-cat="' + escapeAttr(cat) + '">' +
      escapeHtml(label) + '<span class="chip-count">(' + categories[cat] + ')</span></span>';
  });
  bar.innerHTML = html;
  bar.querySelectorAll('.category-chip').forEach(function(chip) {
    chip.addEventListener('click', function() {
      var cat = chip.getAttribute('data-cat') || '';
      _activeCategory = cat || null;
      searchCachedSkills(
        (document.getElementById('mcpRegistrySearch') || {}).value || '',
        cat || null, 1
      );
    });
  });
}

function browseOfficialPage(page) {
  var el = document.getElementById('mcpRegistryResults');
  var pagingEl = document.getElementById('mcpRegistryPaging');
  var totalEl = document.getElementById('mcpRegistryTotal');
  if (!el) return;
  _searchMode = false;
  _activeCategory = null;
  var searchInput = document.getElementById('mcpRegistrySearch');
  if (searchInput) searchInput.value = '';

  el.innerHTML = '<p class="meta">加载第 ' + page + ' 页…</p>';
  if (pagingEl) pagingEl.innerHTML = '';

  fetch((LOCAL_API_BASE || '') + '/api/mcp-registry/browse?page=' + page, { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      _officialLoaded = true;
      _officialPage = d.page || page;
      _officialHasNext = !!d.has_next;
      var servers = (d && Array.isArray(d.servers)) ? d.servers : [];
      if (d.categories) renderCategoryBar(d.categories);
      if (totalEl) totalEl.textContent = '本地已缓存 ' + (d.cached_total || 0) + ' 个技能';

      if (!servers.length) {
        el.innerHTML = '<p class="meta">该页没有更多技能了</p>';
      } else {
        _renderServerCards(el, servers);
      }
      _renderBrowsePaging(pagingEl);
    })
    .catch(function() { el.innerHTML = '<p class="msg err">网络错误，请确认可访问外网</p>'; });
}

function searchCachedSkills(query, category, page) {
  var el = document.getElementById('mcpRegistryResults');
  var pagingEl = document.getElementById('mcpRegistryPaging');
  var totalEl = document.getElementById('mcpRegistryTotal');
  if (!el) return;
  _searchMode = true;

  el.innerHTML = '<p class="meta">搜索中…</p>';
  if (pagingEl) pagingEl.innerHTML = '';

  var params = ['page=' + (page || 1), 'page_size=30'];
  if (query) params.push('q=' + encodeURIComponent(query));
  if (category) params.push('category=' + encodeURIComponent(category));
  var url = (LOCAL_API_BASE || '') + '/api/mcp-registry/search?' + params.join('&');

  fetch(url, { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var servers = (d && Array.isArray(d.servers)) ? d.servers : [];
      if (d.categories) renderCategoryBar(d.categories);
      var total = d.total || 0;
      var curPage = d.page || 1;
      var hasNext = !!d.has_next;
      if (totalEl) totalEl.textContent = '搜索到 ' + total + ' 个';

      if (!servers.length) {
        el.innerHTML = '<p class="meta">本地缓存中未找到匹配技能。请换个关键词，或稍后重试。</p>';
      } else {
        _renderServerCards(el, servers);
      }
      _renderSearchPaging(pagingEl, query, category, curPage, hasNext, total);
    })
    .catch(function() { el.innerHTML = '<p class="msg err">搜索失败</p>'; });
}

function _renderServerCards(el, servers) {
  el.innerHTML = servers.map(function(srv) {
    var hasRemote = srv.remote_url && srv.remote_url.indexOf('{') < 0;
    var addBtn = hasRemote
      ? '<button type="button" class="btn btn-primary btn-sm" data-add-registry-name="' + escapeAttr(srv.name) + '" data-add-registry-url="' + escapeAttr(srv.remote_url) + '">添加</button>'
      : '';
    var linkBtn = srv.website
      ? '<a href="' + escapeAttr(srv.website) + '" target="_blank" rel="noopener" class="btn btn-ghost btn-sm">官网</a>'
      : (srv.repo ? '<a href="' + escapeAttr(srv.repo) + '" target="_blank" rel="noopener" class="btn btn-ghost btn-sm">源码</a>' : '');
    var version = srv.version ? '<span class="tag">v' + escapeHtml(srv.version) + '</span>' : '';
    var tagHtml = (srv.tags || []).map(function(t) {
      var label = CATEGORY_LABELS[t] || t;
      return '<span class="tag">' + escapeHtml(label) + '</span>';
    }).join('');
    return '<div class="skill-store-card">' +
      '<div class="card-label">MCP ' + version + '</div>' +
      '<div class="card-value">' + escapeHtml(srv.title || srv.name) + '</div>' +
      '<div class="card-desc">' + escapeHtml(srv.description || '') + '</div>' +
      '<div class="card-tags">' + tagHtml + '</div>' +
      '<div style="font-size:0.75rem;color:var(--text-muted);margin-top:0.25rem;word-break:break-all;">' + escapeHtml(srv.name) + '</div>' +
      '<div class="card-actions">' + addBtn + linkBtn + '</div></div>';
  }).join('');
  _bindAddButtons(el);
}

function _renderBrowsePaging(pagingEl) {
  if (!pagingEl) return;
  var html = '';
  if (_officialPage > 1) {
    html += '<button type="button" class="btn btn-ghost btn-sm" id="pagePrev">上一页</button>';
  }
  html += '<span class="paging-info">第 ' + _officialPage + ' 页</span>';
  if (_officialHasNext) {
    html += '<button type="button" class="btn btn-primary btn-sm" id="pageNext">下一页</button>';
  }
  pagingEl.innerHTML = html;
  var prevBtn = document.getElementById('pagePrev');
  var nextBtn = document.getElementById('pageNext');
  if (prevBtn) prevBtn.addEventListener('click', function() { browseOfficialPage(_officialPage - 1); });
  if (nextBtn) nextBtn.addEventListener('click', function() { browseOfficialPage(_officialPage + 1); });
}

function _renderSearchPaging(pagingEl, query, category, curPage, hasNext, total) {
  if (!pagingEl) return;
  var html = '';
  if (curPage > 1) {
    html += '<button type="button" class="btn btn-ghost btn-sm" id="searchPrev">上一页</button>';
  }
  html += '<span class="paging-info">第 ' + curPage + ' 页 · 共 ' + total + ' 个</span>';
  if (hasNext) {
    html += '<button type="button" class="btn btn-primary btn-sm" id="searchNext">下一页</button>';
  }
  html += '<button type="button" class="btn btn-ghost btn-sm" id="backToBrowse" style="margin-left:0.5rem;">返回浏览</button>';
  pagingEl.innerHTML = html;
  var prev = document.getElementById('searchPrev');
  var next = document.getElementById('searchNext');
  var back = document.getElementById('backToBrowse');
  if (prev) prev.addEventListener('click', function() { searchCachedSkills(query, category, curPage - 1); });
  if (next) next.addEventListener('click', function() { searchCachedSkills(query, category, curPage + 1); });
  if (back) back.addEventListener('click', function() { browseOfficialPage(1); });
}

function _bindAddButtons(container) {
  container.querySelectorAll('button[data-add-registry-name]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var name = btn.getAttribute('data-add-registry-name') || '';
      var url = btn.getAttribute('data-add-registry-url') || '';
      var shortName = name.replace(/[^a-zA-Z0-9_-]/g, '_').replace(/_+/g, '_');
      btn.disabled = true; btn.textContent = '添加中…';
      fetch((LOCAL_API_BASE || '') + '/skills/add-mcp', {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ name: shortName, url: url })
      })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
        .then(function(x) {
          if (x.ok) {
            btn.textContent = '已添加'; btn.className = 'btn btn-ghost btn-sm';
            loadSkillStore();
          } else { alert(x.data.detail || '添加失败'); btn.disabled = false; btn.textContent = '添加'; }
        })
        .catch(function() { alert('网络错误'); btn.disabled = false; btn.textContent = '添加'; });
    });
  });
}

// search bar + enter key
function bindSkillStoreRegistrySearch() {
  var mcpSearchBtn = document.getElementById('mcpRegistrySearchBtn');
  var mcpSearchInput = document.getElementById('mcpRegistrySearch');
  if (mcpSearchBtn && !mcpSearchBtn._mcpRegistrySearchBound) {
    mcpSearchBtn._mcpRegistrySearchBound = true;
    mcpSearchBtn.addEventListener('click', function() {
      var q = mcpSearchInput ? mcpSearchInput.value.trim() : '';
      if (!q && !_activeCategory) { browseOfficialPage(1); return; }
      searchCachedSkills(q, _activeCategory, 1);
    });
  }
  if (mcpSearchInput && !mcpSearchInput._mcpRegistrySearchBound) {
    mcpSearchInput._mcpRegistrySearchBound = true;
    mcpSearchInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        var q = mcpSearchInput.value.trim();
        if (!q && !_activeCategory) { browseOfficialPage(1); return; }
        searchCachedSkills(q, _activeCategory, 1);
      }
    });
  }
}

function _clearOpenclawWeixinQrInModal() {
  var wrap = document.getElementById('openclawWeixinQrWrap');
  var img = document.getElementById('openclawWeixinQrImg');
  if (img) {
    img.removeAttribute('src');
    img.alt = '';
    img.onerror = null;
  }
  if (wrap) wrap.style.display = 'none';
}

function _showOpenclawWeixinQrInModal(url) {
  var wrap = document.getElementById('openclawWeixinQrWrap');
  var img = document.getElementById('openclawWeixinQrImg');
  if (!wrap || !img || !url) return;
  var s = String(url);
  if (s.length > 2000) {
    wrap.style.display = 'none';
    return;
  }
  img.alt = '微信扫码登录';
  img.onerror = function() { wrap.style.display = 'none'; };
  img.src = 'https://api.qrserver.com/v1/create-qr-code/?size=220x220&margin=2&data=' + encodeURIComponent(s);
  wrap.style.display = 'block';
}

function bindOpenclawWeixinSkillStore() {
  var list = document.getElementById('skillStoreList');
  if (list && !list._openclawWeixinAuthBound) {
    list._openclawWeixinAuthBound = true;
    list.addEventListener('click', function OpenclawWeixinAuthDelegate(ev) {
      var t = ev.target && ev.target.closest && ev.target.closest('.js-openclaw-weixin-auth');
      if (!t) return;
      ev.preventDefault();
      ev.stopPropagation();
      var modal = document.getElementById('openclawWeixinLoginModal');
      if (!modal) return;
      modal.classList.add('visible');
      _startOpenclawWeixinLoginFlow();
    });
  }
  var closeBtn = document.getElementById('openclawWeixinLoginModalClose');
  if (closeBtn && !closeBtn._openclawWeixinCloseBound) {
    closeBtn._openclawWeixinCloseBound = true;
    closeBtn.addEventListener('click', function() {
      var m = document.getElementById('openclawWeixinLoginModal');
      if (m) m.classList.remove('visible');
      _clearOpenclawWeixinQrInModal();
      if (_openclawWeixinPollTimer) {
        clearInterval(_openclawWeixinPollTimer);
        _openclawWeixinPollTimer = null;
      }
    });
  }
}

function _startOpenclawWeixinLoginFlow() {
  var lb = _openclawWeixinResolveBase();
  var statusEl = document.getElementById('openclawWeixinLoginStatus');
  var linkEl = document.getElementById('openclawWeixinLoginQrLink');
  var logEl = document.getElementById('openclawWeixinLoginLog');
  if (!lb) {
    if (statusEl) statusEl.textContent = 'OpenClaw 扫码仅走本机 lobster_online：请在设置中配置 LOCAL_API_BASE。';
    return;
  }
  _clearOpenclawWeixinQrInModal();
  if (linkEl) {
    linkEl.href = '#';
    linkEl.textContent = '';
    linkEl.style.display = 'none';
  }
  if (logEl) logEl.textContent = '';
  if (statusEl) statusEl.textContent = '正在启动扫码任务…';
  if (_openclawWeixinPollTimer) {
    clearInterval(_openclawWeixinPollTimer);
    _openclawWeixinPollTimer = null;
  }
  fetch(lb + '/api/openclaw/weixin-login/start', { method: 'POST', headers: authHeaders() })
    .then(function(r) {
      return r.json().then(function(d) { return { ok: r.ok, status: r.status, data: d }; });
    })
    .then(function(x) {
      if (!x.ok) {
        if (statusEl) statusEl.textContent = (x.data && x.data.detail) ? x.data.detail : ('请求失败 HTTP ' + x.status);
        return;
      }
      var jobId = x.data.job_id;
      if (!jobId) {
        if (statusEl) statusEl.textContent = '未返回 job_id';
        return;
      }
      function poll() {
        fetch(lb + '/api/openclaw/weixin-login/status?job_id=' + encodeURIComponent(jobId), { headers: authHeaders() })
          .then(function(r) { return r.json(); })
          .then(function(d) {
            if (statusEl) statusEl.textContent = d.message || d.status || '';
            if (d.qrcode_url) {
              _showOpenclawWeixinQrInModal(d.qrcode_url);
              if (linkEl) {
                linkEl.href = d.qrcode_url;
                linkEl.textContent = d.qrcode_url;
                linkEl.style.display = 'inline';
              }
            }
            if (logEl && d.log_tail) logEl.textContent = d.log_tail;
            var st = d.status || '';
            if (st === 'success' || st === 'failed' || st === 'timeout') {
              if (_openclawWeixinPollTimer) {
                clearInterval(_openclawWeixinPollTimer);
                _openclawWeixinPollTimer = null;
              }
              if (st === 'success' && typeof loadSkillStore === 'function') loadSkillStore();
            }
          })
          .catch(function() { /* 单次轮询失败可忽略 */ });
      }
      poll();
      _openclawWeixinPollTimer = setInterval(poll, 1500);
    })
    .catch(function() {
      if (statusEl) statusEl.textContent = '网络错误';
    });
}

function initOnlineSkillStore() {
  bindSkillStoreTabs();
  bindAddMcpModal();
  bindSkillStoreRefreshButton();
  bindSkillStoreRegistrySearch();
  bindOpenclawWeixinSkillStore();
  if (STORE_OFFICIAL_TAB_ENABLED && _currentStoreTab === 'official' && !_officialLoaded) {
    browseOfficialPage(1);
  }
}
