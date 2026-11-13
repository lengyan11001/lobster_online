/* 视频号获客工作台 · 只调用 /api/channels/* */
(() => {
  'use strict';
  const API = '/api/channels';
  const NAV = [
    { group: '概览', items: [
      { key: 'overview', label: '数据大屏' }, { key: 'scheduler', label: '排期中心' },
      { key: 'config', label: '配置中心' }, { key: 'nurture', label: '账号养号', unsupported: true },
    ]},
    { group: '获客', items: [
      { key: 'search', label: '搜索采集' }, { key: 'monitor', label: '监控同行' },
      { key: 'collect', label: '视频评论' }, { key: 'follow', label: '关注评论', unsupported: true },
      { key: 'mention', label: '评论@客户', unsupported: true }, { key: 'interaction', label: '私信互动' },
      { key: 'stranger-leads', label: '私信引流', unsupported: true }, { key: 'self-comments', label: '我的评论区' },
      { key: 'all-customers', label: '全部客户' }, { key: 'intent', label: '精准客户' },
      { key: 'groups', label: '群成员提取', unsupported: true },
    ]},
    { group: '系统', items: [{ key: 'logs', label: '执行日志' }, { key: 'guide', label: '使用说明' }] },
  ];
  const TITLES = {
    overview: ['数据大屏', '视频号获客总览'], scheduler: ['排期中心', '关键词搜索计划'], config: ['配置中心', 'TikHub 与视频号助手登录'],
    nurture: ['账号养号', '视频号暂不支持'], search: ['搜索采集', '关键词搜视频号，选中后采集评论'],
    monitor: ['监控同行', '按视频号账号监控作品和评论'], collect: ['视频评论', '评论任务与客户提取'],
    follow: ['关注评论', '视频号暂不支持'], mention: ['评论@客户', '视频号暂不支持'], interaction: ['私信互动', '视频号助手私信会话'],
    'stranger-leads': ['私信引流', '视频号暂不支持主动冷私信'], 'self-comments': ['我的评论区', '视频号助手评论区采集'],
    'all-customers': ['全部客户', '评论里出现过的所有客户'], intent: ['精准客户', '按意向词筛选并可发私信'],
    groups: ['群成员提取', '视频号暂不支持'], logs: ['执行日志', '后端执行记录'], guide: ['使用说明', '能力边界与实测结论'],
  };
  const state = {
    settings: {}, stats: {}, session: null, items: [], selectedVideos: new Set(), tasks: [], currentTask: null,
    customers: [], precise: [], selectedCustomers: new Set(), conversations: [], monitor: { targets: [], videos: [] },
    logs: [], plans: [], capabilities: [], selfComments: [], loading: {},
  };

  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtNum = (n) => Number(n || 0).toLocaleString('zh-CN');
  const fmtTime = (ts) => { const n = Number(ts || 0); if (!n) return '-'; return new Date(n * 1000).toLocaleString('zh-CN', { hour12: false }); };
  const chip = (text, cls = '') => `<span class="chip ${cls}">${esc(text)}</span>`;
  const tag = (text, cls = '') => `<span class="tag ${cls}">${esc(text)}</span>`;

  function toast(message, type = '') {
    const box = $('toast'); if (!box) return;
    const el = document.createElement('div');
    el.className = 'toast-item ' + type; el.textContent = message;
    box.appendChild(el);
    setTimeout(() => el.remove(), type === 'bad' ? 5200 : 3200);
  }

  async function api(path, options = {}) {
    const opts = { headers: { 'Content-Type': 'application/json' }, ...options };
    if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
    const resp = await fetch(API + path, opts);
    let data = null;
    try { data = await resp.json(); } catch (e) { throw new Error('接口返回非 JSON（HTTP ' + resp.status + '）'); }
    if (!data || data.code !== 200) throw new Error((data && (data.message || data.msg)) || ('请求失败 HTTP ' + resp.status));
    return data.data;
  }

  async function guarded(name, fn) {
    if (state.loading[name]) return;
    state.loading[name] = true;
    try { await fn(); } catch (e) { toast(e.message || String(e), 'bad'); }
    finally { state.loading[name] = false; }
  }

  function buildNav() {
    const nav = $('nav');
    nav.innerHTML = NAV.map((group) => `
      <div class="side-label" style="padding-left:10px;padding-right:10px">${esc(group.group)}</div>
      ${group.items.map((item) => `
        <button class="nav-item" data-key="${item.key}" onclick="showSection('${item.key}')">
          <span class="nav-dot"></span><span>${esc(item.label)}</span>
          ${item.unsupported ? '<span class="nav-unsupported">限制</span>' : ''}
        </button>`).join('')}
    `).join('');
  }

  function panel(title, hint, body, actions = '') {
    return `<div class="panel"><div class="panel-head"><h3>${esc(title)}</h3><span class="hint">${esc(hint)}</span><div class="actions">${actions}</div></div><div class="panel-body">${body}</div></div>`;
  }

  function sectionHTML(key) {
    const t = TITLES[key] || [key, ''];
    let body = '';
    if (key === 'overview') body = overviewHTML();
    else if (key === 'search') body = searchHTML();
    else if (key === 'collect') body = collectHTML();
    else if (key === 'monitor') body = monitorHTML();
    else if (key === 'all-customers') body = customersHTML(false);
    else if (key === 'intent') body = customersHTML(true);
    else if (key === 'interaction') body = interactionHTML();
    else if (key === 'self-comments') body = selfCommentsHTML();
    else if (key === 'scheduler') body = schedulerHTML();
    else if (key === 'config') body = configHTML();
    else if (key === 'logs') body = logsHTML();
    else if (key === 'guide') body = guideHTML();
    else body = unsupportedHTML(key);
    return `<section class="section" id="sec-${key}"><div class="page-head"><div><h2>${esc(t[0])}</h2><p>${esc(t[1])}</p></div></div>${body}</section>`;
  }

  function overviewHTML() {
    return `
      <div class="notice blue">视频号获客 = <b>TikHub 公开数据</b>（搜索 / 视频详情 / 评论 / 同行作品）+ <b>视频号助手</b>（我的评论区 / 私信回复）。主动冷私信、关注、点赞、群成员提取暂不支持，原因见「使用说明」。</div>
      <div class="grid grid-4">
        <div class="card stat"><div class="label">搜索会话</div><div class="value" id="statSessions">0</div><div class="hint">关键词搜索记录</div></div>
        <div class="card stat"><div class="label">评论任务</div><div class="value" id="statTasks">0</div><div class="hint">视频评论采集队列</div></div>
        <div class="card stat"><div class="label">全部客户</div><div class="value" id="statCustomers">0</div><div class="hint">评论去重后的客户</div></div>
        <div class="card stat"><div class="label">精准客户</div><div class="value" id="statPrecise">0</div><div class="hint">命中意向词/问句</div></div>
      </div>
      <div class="split" style="margin-top:14px">
        <div class="panel" style="margin-top:0"><div class="panel-head"><h3>能力探测结论</h3><span class="hint">2026-11 实测</span></div><div class="panel-body" id="overviewCaps"></div></div>
        <div class="panel" style="margin-top:0"><div class="panel-head"><h3>推荐流程</h3></div><div class="panel-body">
          <div class="kv">
            <div class="k">第 1 步</div><div class="v">配置中心确认 TikHub 已配置；扫码登录视频号助手。</div>
            <div class="k">第 2 步</div><div class="v">搜索采集输入关键词，选中视频 → 采集评论。</div>
            <div class="k">第 3 步</div><div class="v">全部客户 / 精准客户里筛选，加入私信互动。</div>
            <div class="k">第 4 步</div><div class="v">先采集私信会话；视频号只能回复已有会话，不能冷私信。</div>
          </div>
          <div class="flex" style="margin-top:14px"><button class="btn btn-primary" onclick="showSection('search')">开始搜索采集</button><button class="btn" onclick="showSection('guide')">查看能力边界</button></div>
        </div></div>
      </div>`;
  }

  function searchHTML() {
    return `
      <div class="panel" style="margin-top:0"><div class="panel-head"><h3>关键词搜索</h3><span class="hint">TikHub 视频号搜一搜</span></div><div class="panel-body">
        <div class="toolbar">
          <input class="input wide" id="kw" placeholder="输入关键词，例如：装修 / 招商加盟 / 本地服务">
          <select class="select" id="sort"><option value="hot">最热</option><option value="latest">最新</option><option value="default">相关</option></select>
          <select class="select" id="pt"><option value="week">近 7 天</option><option value="day">近 1 天</option><option value="half_year">近半年</option><option value="all">不限</option></select>
          <input class="input small" id="maxr" type="number" min="1" max="50" value="20" title="返回视频数">
          <button class="btn btn-primary" id="searchBtn" onclick="doSearch()">搜索采集</button>
          <button class="btn" onclick="expandKeywords()">AI 扩词</button>
          <button class="btn" onclick="exportSearch()">导出 CSV</button>
        </div>
        <div class="muted tiny" style="margin-top:8px" id="searchHint">每次搜索会调用 TikHub（约 0.01$/次）；TikHub 响应较慢，约 10-20 秒；详情在采集评论时补齐。</div>
      </div></div>
      <div class="panel"><div class="panel-head"><h3>搜索结果</h3><span class="hint" id="searchCount">0 条</span><div class="actions">
        <button class="btn btn-sm" onclick="toggleVideos(true)">全选</button><button class="btn btn-sm" onclick="toggleVideos(false)">取消</button>
        <button class="btn btn-sm btn-primary" id="collectBtn" onclick="collectSelected()">采集选中视频评论</button>
      </div></div><div class="panel-body tight" id="searchResults"></div></div>`;
  }

  function collectHTML() {
    return `
      <div class="split">
        <div class="panel" style="margin-top:0"><div class="panel-head"><h3>评论任务</h3><span class="hint">选中后执行采集</span><div class="actions"><button class="btn btn-sm" onclick="loadTasks()">刷新</button></div></div><div class="panel-body tight" id="tasksBody"></div></div>
        <div class="panel" style="margin-top:0"><div class="panel-head"><h3>任务详情</h3><span class="hint" id="taskDetailHint">点左侧任务查看</span></div><div class="panel-body" id="taskDetail"><div class="empty"><strong>未选择任务</strong>点击左侧「查看」加载评论明细。</div></div></div>
      </div>`;
  }

  function monitorHTML() {
    return `
      <div class="panel" style="margin-top:0"><div class="panel-head"><h3>添加同行</h3><span class="hint">username 或视频号作品链接</span></div><div class="panel-body">
        <div class="toolbar">
          <input class="input wide" id="monInput" placeholder="v2_...@finder 或 https://weixin.qq.com/sph/...">
          <input class="input mid" id="monNick" placeholder="备注昵称（可选）">
          <button class="btn btn-primary" onclick="addMonitor()">添加监控</button>
        </div>
      </div></div>
      <div class="split">
        <div class="panel"><div class="panel-head"><h3>监控目标</h3><div class="actions"><button class="btn btn-sm" onclick="loadMonitor()">刷新</button></div></div><div class="panel-body tight" id="monitorTargets"></div></div>
        <div class="panel"><div class="panel-head"><h3>作品</h3><span class="hint">刷新后选中作品采集评论</span><div class="actions"><button class="btn btn-sm btn-primary" onclick="runMonitorTarget()">采集选中作品评论</button></div></div><div class="panel-body tight" id="monitorVideos"></div></div>
      </div>`;
  }

  function customersHTML(precise) {
    const id = precise ? 'precise' : 'customers';
    return `
      <div class="panel" style="margin-top:0"><div class="panel-head"><h3>${precise ? '精准客户' : '全部客户'}</h3><span class="hint" id="${id}Count">0 人</span><div class="actions">
        <input class="input mid" id="${id}Filter" placeholder="按昵称/内容/关键词过滤" oninput="renderCustomers('${id}')">
        <button class="btn btn-sm" onclick="loadCustomers()">刷新</button>
        <button class="btn btn-sm" onclick="addPrecise()">加入精准</button>
        <button class="btn btn-sm" onclick="exportCustomers('${id}')">导出</button>
        <button class="btn btn-sm btn-danger" onclick="deleteCustomers()">删除</button>
      </div></div><div class="panel-body tight" id="${id}Body"></div></div>
      ${precise ? `<div class="panel"><div class="panel-head"><h3>私信话术</h3><span class="hint">只发给已有私信会话的客户</span></div><div class="panel-body">
        <textarea id="intentMessage" placeholder="例如：看到你在视频号评论区咨询，方便说下你的城市和需求吗？我这边发一份报价/案例给你。"></textarea>
        <div class="flex" style="margin-top:10px"><button class="btn btn-primary" onclick="startInteraction()">开始发送</button><span class="muted tiny" id="interactionState">未开始</span></div>
      </div></div>` : ''}`;
  }

  function interactionHTML() {
    return `
      <div class="notice">视频号没有“主动给陌生人发私信”的公开接口。本页通过视频号助手 <span class="mono">/platform/private_msg</span> 读取/回复已有会话；要触达评论客户，先让对方私信你或在视频号内互动。</div>
      <div class="split">
        <div class="panel" style="margin-top:0"><div class="panel-head"><h3>私信会话</h3><span class="hint" id="inboxCount">0 个</span><div class="actions"><button class="btn btn-sm" onclick="collectInbox()">采集私信会话</button></div></div><div class="panel-body tight" id="inboxList"></div></div>
        <div class="panel" style="margin-top:0"><div class="panel-head"><h3>发送私信</h3><span class="hint">选择会话后填写</span></div><div class="panel-body">
          <div class="toolbar" style="margin-bottom:10px"><input class="input mid" id="inboxNickname" placeholder="会话昵称"><button class="btn btn-sm" onclick="checkLogin()">检查登录</button></div>
          <textarea id="inboxMessage" placeholder="输入要发送的内容"></textarea>
          <div class="flex" style="margin-top:10px"><button class="btn btn-primary" onclick="sendInbox()">发送</button><span class="muted tiny">发送会打开视频号助手操作真实会话，请确认内容。</span></div>
        </div></div>
      </div>`;
  }

  function selfCommentsHTML() {
    return `
      <div class="notice blue">读取视频号助手「评论管理」里你自己作品的评论；用于二次跟进和高意向客户沉淀。</div>
      <div class="panel" style="margin-top:0"><div class="panel-head"><h3>我的评论区</h3><span class="hint" id="selfCount">0 条</span><div class="actions"><button class="btn btn-sm btn-primary" onclick="runSelfComments()">立即采集</button></div></div><div class="panel-body tight" id="selfCommentsBody"></div></div>`;
  }

  function schedulerHTML() {
    return `
      <div class="panel" style="margin-top:0"><div class="panel-head"><h3>新建搜索计划</h3><span class="hint">保存关键词，随时一键执行</span></div><div class="panel-body">
        <div class="toolbar">
          <input class="input wide" id="planKeyword" placeholder="关键词">
          <select class="select" id="planSort"><option value="hot">最热</option><option value="latest">最新</option><option value="default">相关</option></select>
          <select class="select" id="planPt"><option value="week">近 7 天</option><option value="day">近 1 天</option><option value="half_year">近半年</option><option value="all">不限</option></select>
          <input class="input small" id="planMax" type="number" min="1" max="50" value="20">
          <button class="btn btn-primary" onclick="createPlan()">保存计划</button>
        </div>
      </div></div>
      <div class="panel"><div class="panel-head"><h3>计划列表</h3><div class="actions"><button class="btn btn-sm" onclick="loadPlans()">刷新</button></div></div><div class="panel-body tight" id="plansBody"></div></div>`;
  }

  function configHTML() {
    return `
      <div class="split">
        <div class="panel" style="margin-top:0"><div class="panel-head"><h3>运行配置</h3><span class="hint">TikHub + 采集上限</span></div><div class="panel-body">
          <div class="kv" style="margin-bottom:14px"><div class="k">TikHub</div><div class="v" id="cfgKeyState">未检查</div><div class="k">登录态</div><div class="v" id="cfgLoginState">未检查</div></div>
          <div class="toolbar" style="margin-bottom:12px"><input class="input wide" id="cfgKey" placeholder="TikHub API Key（留空沿用服务端/环境变量）"><button class="btn" onclick="saveConfig()">保存 Key</button></div>
          <div class="grid grid-2">
            <label class="tiny muted">默认返回视频数<input class="input" id="cfgMaxResults" type="number" min="1" max="50" style="width:100%;margin-top:4px"></label>
            <label class="tiny muted">单视频评论上限<input class="input" id="cfgMaxComments" type="number" min="1" max="500" style="width:100%;margin-top:4px"></label>
            <label class="tiny muted">详情补齐条数<input class="input" id="cfgEnrich" type="number" min="0" max="10" style="width:100%;margin-top:4px"></label>
            <label class="tiny muted">私信间隔（秒）<input class="input" id="cfgInterval" type="number" min="1" max="120" style="width:100%;margin-top:4px"></label>
          </div>
          <label class="tiny muted" style="display:block;margin-top:12px">意向词（逗号分隔）</label>
          <textarea id="cfgIntent" style="min-height:70px"></textarea>
          <div class="flex" style="margin-top:12px"><button class="btn btn-primary" onclick="saveLimits()">保存配置</button><button class="btn" onclick="openLogin()">扫码登录视频号助手</button><button class="btn" onclick="checkLogin()">检查登录</button></div>
        </div></div>
        <div class="panel" style="margin-top:0"><div class="panel-head"><h3>能力矩阵</h3><span class="hint">实测</span></div><div class="panel-body" id="capMatrix"></div></div>
      </div>`;
  }

  function logsHTML() {
    return `<div class="panel" style="margin-top:0"><div class="panel-head"><h3>执行日志</h3><div class="actions"><button class="btn btn-sm" onclick="loadLogs()">刷新</button></div></div><div class="panel-body tight" id="logsBody"></div></div>`;
  }

  function guideHTML() {
    return `
      <div class="notice green"><b>结论：视频号可以获客，但不能 1:1 照搬抖音。</b>公开搜索/评论/TikHub 已实测可用；登录后视频号助手可管自己的评论区和私信会话。主动冷私信、关注/点赞/评论别人、群成员、养号没有公开接口。</div>
      <div class="split">
        <div class="panel" style="margin-top:0"><div class="panel-head"><h3>实测证据</h3><span class="hint">2026-11-13</span></div><div class="panel-body"><div class="kv">
          <div class="k">视频号助手</div><div class="v">https://channels.weixin.qq.com/platform 返回 200，标题「视频号助手」；存在 /platform/comment、/platform/private_msg。</div>
          <div class="k">关键词搜索</div><div class="v">TikHub <span class="mono">wechat_search/v2/fetch_search_videos</span> 实测「装修」返回 10 条视频。</div>
          <div class="k">视频详情</div><div class="v">exportId + feedNonceId 换到 objectId、username、点赞/评论数。</div>
          <div class="k">评论采集</div><div class="v">TikHub <span class="mono">wechat_channels/v2/fetch_video_comments</span> 实测单视频返回 26 条评论。</div>
          <div class="k">账号作品</div><div class="v">TikHub <span class="mono">fetch_user_videos</span> 按 username 拉主页作品。</div>
          <div class="k">私信</div><div class="v">视频号助手 /platform/private_msg 可读/回已有会话；不能主动冷私信。</div>
        </div></div></div>
        <div class="panel" style="margin-top:0"><div class="panel-head"><h3>与抖音获客的差异</h3></div><div class="panel-body"><div id="guideCaps"></div></div></div>
      </div>`;
  }

  function unsupportedHTML(key) {
    const map = {
      nurture: '视频号没有公开的养号接口。抖音的“模拟浏览/点赞/关注”依赖网页自动化；视频号是微信客户端内的原生页面，只能做客户端 UI 自动化，稳定性差、风控高，本版本不提供。',
      follow: '视频号助手只能管理自己作品的评论，不能关注别人并自动评论。建议用「搜索采集 → 视频评论」拿到公开评论客户。',
      mention: '视频号没有公开的评论区 @ 客户接口。可在私信会话里回复已有客户。',
      'stranger-leads': '视频号不能像抖音一样主动给陌生人发私信。只能先让对方私信你，或在视频号内互动后进入会话，再用「私信互动」回复。',
      groups: '视频号没有群成员列表接口。群成员提取属于微信客户端能力，不在本模块范围。',
    };
    return `<div class="notice">${esc(map[key] || '视频号暂不支持该能力。')}</div><div class="panel" style="margin-top:0"><div class="panel-head"><h3>替代做法</h3></div><div class="panel-body"><div class="kv">
      <div class="k">可做</div><div class="v">关键词搜视频、采集公开评论、提取客户、导出 CSV、回复已有私信、采集我的评论区。</div>
      <div class="k">入口</div><div class="v">搜索采集 / 视频评论 / 全部客户 / 精准客户 / 私信互动 / 我的评论区。</div>
      <div class="k">不建议</div><div class="v">用客户端 UI 自动化硬做关注、点赞、群成员；视频号风控和版本变化会直接打断。</div>
    </div><div class="flex" style="margin-top:14px"><button class="btn btn-primary" onclick="showSection('search')">去搜索采集</button><button class="btn" onclick="showSection('guide')">看能力边界</button></div></div></div>`;
  }

  function renderCaps(targetId) {
    const el = $(targetId); if (!el) return;
    const items = state.capabilities || [];
    el.innerHTML = items.length ? items.map((c) => `<div class="cap"><div class="ico ${c.supported ? 'y' : 'n'}">${c.supported ? '✓' : '×'}</div><div><div class="name">${esc(c.name)} ${tag(c.channel || '', c.supported ? 'green' : 'red')}</div><div class="note">${esc(c.note || '')}</div></div></div>`).join('') : '<div class="empty">加载中...</div>';
  }

  function renderStats() {
    const s = state.stats || {};
    ['statSessions:search_sessions', 'statTasks:tasks', 'statCustomers:customers', 'statPrecise:precise_customers'].forEach((pair) => {
      const [id, key] = pair.split(':'); if ($(id)) $(id).textContent = fmtNum(s[key] || 0);
    });
  }

  function renderSearchResults() {
    const box = $('searchResults'); if (!box) return;
    const items = state.items || [];
    if ($('searchCount')) $('searchCount').textContent = items.length + ' 条';
    if (!items.length) { box.innerHTML = '<div class="empty"><strong>还没有搜索结果</strong>输入关键词后点「搜索采集」。</div>'; return; }
    box.innerHTML = `<div class="table-wrap"><table><thead><tr><th style="width:34px"></th><th>视频</th><th>作者</th><th>数据</th><th>发布时间</th><th>状态</th></tr></thead><tbody>${items.map((v, idx) => {
      const key = v.video_id || v.export_id || v.doc_id || String(idx);
      return `<tr><td><input class="check" type="checkbox" data-vkey="${esc(key)}" ${state.selectedVideos.has(key) ? 'checked' : ''} onchange="toggleVideo(this)"></td>
        <td><div style="font-weight:600;max-width:520px">${esc(v.title || '(无标题)')}</div><div class="tiny muted">${esc(v.duration || '')} ${v.export_id ? 'exportId 已取' : ''} ${v.video_id ? 'objectId 已取' : ''}</div></td>
        <td>${esc(v.nickname || '-')}<div class="tiny muted mono">${esc((v.username || '').slice(0, 28))}</div></td>
        <td class="nowrap">${v.like_count ? '赞 ' + fmtNum(v.like_count) + '<br>' : ''}${v.comment_count ? '评 ' + fmtNum(v.comment_count) : (v.video_id ? '评论数未知' : '待补详情')}</td>
        <td class="nowrap">${esc(v.date_text || fmtTime(v.publish_time))}</td>
        <td>${v.video_id ? tag('可采评论', 'green') : tag('需补详情', 'amber')}</td></tr>`;
    }).join('')}</tbody></table></div>`;
  }

  function renderTasks() {
    const box = $('tasksBody'); if (!box) return;
    const rows = state.tasks || [];
    if (!rows.length) { box.innerHTML = '<div class="empty"><strong>还没有评论任务</strong>先在搜索采集里选中视频。</div>'; return; }
    box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>视频</th><th>关键词</th><th>状态</th><th>评论</th><th>操作</th></tr></thead><tbody>${rows.map((t) => `<tr>
      <td><div style="font-weight:600;max-width:360px">${esc((t.title || '').slice(0, 60))}</div><div class="tiny muted">${esc(t.nickname || '')}</div></td>
      <td>${esc(t.keyword || '-')}</td><td>${statusTag(t.status)}</td><td>${fmtNum(t.collected || 0)} / ${fmtNum(t.target_count || 0)}</td>
      <td class="nowrap"><button class="btn btn-sm" onclick="viewTask(${Number(t.id)})">查看</button> <button class="btn btn-sm btn-primary" onclick="runTask(${Number(t.id)})">${t.status === 'done' ? '重采' : '采集'}</button></td></tr>`).join('')}</tbody></table></div>`;
  }

  function statusTag(status) {
    const map = { pending: ['待采集', 'amber'], running: ['采集中', 'blue'], done: ['已完成', 'green'], failed: ['失败', 'red'] };
    const pair = map[status] || [status || '-', '']; return tag(pair[0], pair[1]);
  }

  function renderTaskDetail() {
    const box = $('taskDetail'); if (!box) return;
    const t = state.currentTask;
    if (!t) { box.innerHTML = '<div class="empty"><strong>未选择任务</strong>点击左侧「查看」。</div>'; return; }
    if ($('taskDetailHint')) $('taskDetailHint').textContent = t.title || '';
    const rows = t.comments || [];
    box.innerHTML = `<div class="kv" style="margin-bottom:12px"><div class="k">视频</div><div class="v">${esc(t.title || '')}</div><div class="k">作者</div><div class="v">${esc(t.nickname || '')}</div><div class="k">状态</div><div class="v">${statusTag(t.status)} ${esc(t.error || '')}</div></div>
      ${rows.length ? `<div class="table-wrap" style="max-height:42vh"><table><thead><tr><th>昵称</th><th>评论</th><th>赞</th></tr></thead><tbody>${rows.slice(0, 200).map((c) => `<tr><td>${esc(c.nickname || '')}<div class="tiny muted mono">${esc((c.username || '').slice(0, 22))}</div></td><td>${esc(c.content || '')}</td><td>${fmtNum(c.like_count)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">暂无评论，点任务「采集」。</div>'}`;
  }

  function renderCustomers(which) {
    const precise = which === 'precise';
    const rows = precise ? (state.precise || []) : (state.customers || []);
    const filterEl = $(which + 'Filter');
    const filter = String(filterEl ? filterEl.value : '').toLowerCase();
    const filtered = rows.filter((r) => !filter || [r.nickname, r.content, r.keyword, r.video_title].join(' ').toLowerCase().includes(filter));
    const box = $(which + 'Body'); if (!box) return;
    if ($(which + 'Count')) $(which + 'Count').textContent = filtered.length + ' 人';
    if (!filtered.length) { box.innerHTML = '<div class="empty"><strong>还没有客户</strong>先采集视频评论。</div>'; return; }
    box.innerHTML = `<div class="table-wrap"><table><thead><tr><th style="width:34px"></th><th>客户</th><th>评论</th><th>来源</th><th>意向</th></tr></thead><tbody>${filtered.slice(0, 500).map((r) => {
      const key = r.customer_id || r.username || r.nickname;
      return `<tr><td><input class="check" type="checkbox" data-ckey="${esc(key)}" ${state.selectedCustomers.has(key) ? 'checked' : ''} onchange="toggleCustomer(this)"></td>
        <td>${esc(r.nickname || '-')}<div class="tiny muted mono">${esc((r.username || '').slice(0, 26))}</div></td>
        <td style="max-width:460px">${esc(r.content || '')}</td>
        <td><div class="tiny">${esc(r.keyword || '')}</div><div class="tiny muted">${esc((r.video_title || '').slice(0, 30))}</div></td>
        <td>${tag('意向 ' + (r.intent_score || 0), (r.intent_score || 0) >= 36 ? 'green' : '')}</td></tr>`;
    }).join('')}</tbody></table></div>`;
  }

  function renderMonitor() {
    const targets = (state.monitor && state.monitor.targets) || [];
    const videos = (state.monitor && state.monitor.videos) || [];
    const tb = $('monitorTargets');
    if (tb) {
      tb.innerHTML = targets.length ? `<div class="table-wrap"><table><thead><tr><th>账号</th><th>作品</th><th>操作</th></tr></thead><tbody>${targets.map((t) => `<tr><td>${esc(t.nickname || '-')}<div class="tiny mono">${esc((t.username || '').slice(0, 24))}</div></td><td>${fmtNum(t.video_count)}</td><td class="nowrap"><button class="btn btn-sm" onclick="refreshMonitor(${Number(t.id)})">刷新</button> <button class="btn btn-sm btn-danger" onclick="deleteMonitor(${Number(t.id)})">删除</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty"><strong>还没有监控同行</strong>输入 username 或作品链接。</div>';
    }
    const vb = $('monitorVideos');
    if (vb) {
      vb.innerHTML = videos.length ? `<div class="table-wrap"><table><thead><tr><th style="width:30px"></th><th>作品</th><th>数据</th></tr></thead><tbody>${videos.map((v) => `<tr><td><input class="check" type="checkbox" data-mvid="${esc(v.video_id)}" ${v.selected !== false ? 'checked' : ''}></td><td>${esc((v.title || '').slice(0, 60))}</td><td class="nowrap">赞 ${fmtNum(v.like_count)}<br>评 ${fmtNum(v.comment_count)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">选择左侧目标点「刷新」。</div>';
    }
  }

  function renderInbox() {
    const rows = state.conversations || [];
    if ($('inboxCount')) $('inboxCount').textContent = rows.length + ' 个';
    const box = $('inboxList'); if (!box) return;
    if (!rows.length) { box.innerHTML = '<div class="empty"><strong>还没有会话</strong>先扫码登录视频号助手，再点「采集私信会话」。</div>'; return; }
    box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>会话</th><th>最后消息</th><th>操作</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${esc(r.nickname || '-')}<div class="tiny muted">${r.unread ? '未读 ' + r.unread : ''}</div></td><td style="max-width:260px">${esc(r.last_message || '')}</td><td><button class="btn btn-sm" onclick="pickConversation('${esc(r.nickname || '')}')">选择</button></td></tr>`).join('')}</tbody></table></div>`;
  }

  function renderSelfComments() {
    const rows = state.selfComments || [];
    if ($('selfCount')) $('selfCount').textContent = rows.length + ' 条';
    const box = $('selfCommentsBody'); if (!box) return;
    box.innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>昵称</th><th>评论</th><th>赞</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${esc(r.nickname || '')}</td><td>${esc(r.content || '')}</td><td>${fmtNum(r.like_count)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty"><strong>还没有采集</strong>登录视频号助手后点「立即采集」。</div>';
  }

  function renderPlans() {
    const rows = state.plans || [];
    const box = $('plansBody'); if (!box) return;
    box.innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>计划</th><th>关键词</th><th>状态</th><th>上次</th><th>操作</th></tr></thead><tbody>${rows.map((p) => `<tr><td>${esc(p.name || '')}</td><td>${esc(p.keyword || '')}</td><td>${p.enabled ? tag('启用', 'green') : tag('暂停')}</td><td class="tiny">${esc(p.last_run || '-')}<br>${esc(p.last_result || '')}</td><td class="nowrap"><button class="btn btn-sm btn-primary" onclick="runPlan(${Number(p.id)})">执行</button> <button class="btn btn-sm" onclick="togglePlan(${Number(p.id)})">${p.enabled ? '暂停' : '启用'}</button> <button class="btn btn-sm btn-danger" onclick="deletePlan(${Number(p.id)})">删除</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty"><strong>还没有计划</strong>上面填关键词保存。</div>';
  }

  function renderLogs() {
    const rows = state.logs || [];
    const box = $('logsBody'); if (!box) return;
    box.innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th style="width:170px">时间</th><th style="width:70px">级别</th><th>内容</th></tr></thead><tbody>${rows.slice().reverse().map((r) => `<tr><td class="tiny nowrap">${esc(r.time || '')}</td><td>${tag(r.level || 'info', r.level === 'error' ? 'red' : '')}</td><td>${esc(r.message || '')}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">暂无日志。</div>';
  }

  async function loadStats() {
    state.stats = await api('/stats');
    renderStats();
    const s = state.stats || {};
    ['statSessions:search_sessions', 'statTasks:tasks', 'statCustomers:customers', 'statPrecise:precise_customers'].forEach((pair) => {
      const [id, key] = pair.split(':'); if ($(id)) $(id).textContent = fmtNum(s[key] || 0);
    });
  }

  async function loadCapabilities() {
    const data = await api('/capabilities');
    state.capabilities = (data && data.items) || [];
    renderCaps('overviewCaps'); renderCaps('capMatrix'); renderCaps('guideCaps');
  }

  async function loadSettings() {
    state.settings = await api('/local-settings');
    const s = state.settings || {};
    if ($('cfgMaxResults')) $('cfgMaxResults').value = s.max_results || 20;
    if ($('cfgMaxComments')) $('cfgMaxComments').value = s.max_comments || 100;
    if ($('cfgEnrich')) $('cfgEnrich').value = s.enrich_limit == null ? 0 : s.enrich_limit;
    if ($('cfgInterval')) $('cfgInterval').value = s.send_min_interval || 6;
    if ($('cfgIntent')) $('cfgIntent').value = s.intent_terms || '';
    if ($('cfgKeyState')) $('cfgKeyState').innerHTML = s.tikhub_configured ? tag('已配置 ' + (s.tikhub_key_masked || ''), 'green') : tag('未配置', 'red');
    if ($('keyChip')) { $('keyChip').className = 'chip ' + (s.tikhub_configured ? 'ok' : 'bad'); $('keyChip').textContent = s.tikhub_configured ? 'TikHub 已配置' : 'TikHub 未配置'; }
  }

  async function loadTasks() {
    const rows = await api('/tasks');
    state.tasks = Array.isArray(rows) ? rows : [];
    renderTasks();
  }

  async function loadCustomers() {
    const data = await api('/customer-pools');
    state.customers = (data && data.all) || [];
    state.precise = (data && data.precise) || [];
    renderCustomers('customers'); renderCustomers('precise');
  }

  async function loadMonitor() {
    const data = await api('/monitor/targets');
    state.monitor = { targets: (data && data.targets) || [], videos: (data && data.videos) || [] };
    renderMonitor();
  }

  async function loadInbox() {
    const data = await api('/inbox/status');
    state.conversations = (data && data.conversations) || [];
    renderInbox();
  }

  async function loadPlans() {
    const rows = await api('/scheduler/plans');
    state.plans = Array.isArray(rows) ? rows : [];
    renderPlans();
  }

  async function loadLogs() {
    const rows = await api('/logs?limit=300');
    state.logs = Array.isArray(rows) ? rows : [];
    renderLogs();
  }

  async function loadSelf() {
    const data = await api('/self-comment-monitor/status');
    state.selfComments = (data && data.comments) || state.selfComments || [];
    renderSelfComments();
  }

  async function checkLogin() {
    await guarded('login', async () => {
      const data = await api('/account/status');
      const chipEl = $('loginChip');
      if (chipEl) { chipEl.className = 'chip ' + (data.logged_in ? 'ok' : 'bad'); chipEl.textContent = data.logged_in ? '视频号助手已登录' : '视频号助手未登录'; }
      if ($('cfgLoginState')) $('cfgLoginState').innerHTML = data.logged_in ? tag('已登录', 'green') : tag('未登录', 'red');
      toast(data.logged_in ? '视频号助手已登录' : '视频号助手未登录，请扫码', data.logged_in ? 'ok' : 'warn');
    });
  }

  async function openLogin() {
    await guarded('openLogin', async () => {
      const data = await api('/account/default/login', { method: 'POST', body: {} });
      toast((data && data.message) || '已打开登录页', 'ok');
    });
  }

  async function doSearch() {
    const keyword = String(($('kw') || {}).value || '').trim();
    if (!keyword) { toast('请输入关键词', 'warn'); return; }
    await guarded('search', async () => {
      const btn = $('searchBtn'); if (btn) { btn.disabled = true; btn.textContent = '搜索中...'; }
      try {
        const data = await api('/search/collect', { method: 'POST', body: {
          keyword, sort: ($('sort') || {}).value || 'hot', publish_time: ($('pt') || {}).value || 'week',
          max_results: Number(($('maxr') || {}).value || 20),
        }});
        state.session = (data && data.session) || null;
        state.items = (data && data.items) || [];
        state.selectedVideos.clear();
        renderSearchResults();
        await loadStats();
        toast('搜索完成，共 ' + state.items.length + ' 条视频', 'ok');
      } finally { if (btn) { btn.disabled = false; btn.textContent = '搜索采集'; } }
    });
  }

  async function expandKeywords() {
    const keyword = String(($('kw') || {}).value || '').trim();
    if (!keyword) { toast('先输入种子关键词', 'warn'); return; }
    await guarded('expand', async () => {
      const data = await api('/monitor/keyword-expand-search', { method: 'POST', body: { keyword } });
      const list = (data && data.keywords) || [];
      if ($('searchHint')) $('searchHint').innerHTML = '扩词：' + list.map((k) => `<a href="javascript:void(0)" onclick="document.getElementById('kw').value='${esc(k)}'">${esc(k)}</a>`).join(' · ');
      toast('已生成 ' + list.length + ' 个关键词', 'ok');
    });
  }

  function toggleVideo(el) {
    const key = el.getAttribute('data-vkey');
    if (el.checked) state.selectedVideos.add(key); else state.selectedVideos.delete(key);
  }

  function toggleVideos(checked) {
    state.selectedVideos.clear();
    (state.items || []).forEach((v, idx) => {
      if (checked) state.selectedVideos.add(v.video_id || v.export_id || v.doc_id || String(idx));
    });
    renderSearchResults();
  }

  async function collectSelected() {
    if (!state.session) { toast('先搜索', 'warn'); return; }
    if (!state.selectedVideos.size) { toast('先勾选视频', 'warn'); return; }
    await guarded('collect', async () => {
      const data = await api('/tasks/from-search', { method: 'POST', body: { session_id: state.session.id, video_ids: Array.from(state.selectedVideos) } });
      const created = (data && data.created) || [];
      if (!created.length) { toast('没有创建任务（可能已存在）', 'warn'); return; }
      toast('已创建 ' + created.length + ' 个任务，开始采集评论...', 'ok');
      for (const task of created) {
        try { await api('/tasks/' + task.id + '/run', { method: 'POST', body: {} }); } catch (e) { toast('任务 ' + task.id + ' 失败：' + e.message, 'bad'); }
      }
      await loadTasks(); await loadCustomers(); await loadStats();
      toast('评论采集完成', 'ok');
    });
  }

  async function exportSearch() {
    if (!state.session) { toast('先搜索', 'warn'); return; }
    await guarded('export', async () => {
      const data = await api('/search/export', { method: 'POST', body: { session_id: state.session.id } });
      if (data && data.download_url) window.open(data.download_url, '_blank');
      toast('已导出 CSV', 'ok');
    });
  }

  async function viewTask(id) {
    await guarded('viewTask', async () => { state.currentTask = await api('/tasks/' + id); renderTaskDetail(); });
  }

  async function runTask(id) {
    await guarded('runTask', async () => {
      await api('/tasks/' + id + '/run', { method: 'POST', body: {} });
      await loadTasks(); await loadCustomers(); await loadStats();
      if (state.currentTask && Number(state.currentTask.id) === Number(id)) state.currentTask = await api('/tasks/' + id);
      renderTaskDetail(); toast('任务已完成', 'ok');
    });
  }

  function toggleCustomer(el) {
    const key = el.getAttribute('data-ckey');
    if (el.checked) state.selectedCustomers.add(key); else state.selectedCustomers.delete(key);
  }

  async function addPrecise() {
    if (!state.selectedCustomers.size) { toast('先勾选客户', 'warn'); return; }
    await guarded('addPrecise', async () => {
      await api('/customers/add-precise', { method: 'POST', body: { ids: Array.from(state.selectedCustomers) } });
      await loadCustomers(); toast('已加入精准客户', 'ok');
    });
  }

  async function deleteCustomers() {
    if (!state.selectedCustomers.size) { toast('先勾选客户', 'warn'); return; }
    if (!confirm('删除选中客户？评论任务里的记录也会移除。')) return;
    await guarded('deleteCustomers', async () => {
      await api('/customers/delete', { method: 'POST', body: { ids: Array.from(state.selectedCustomers) } });
      state.selectedCustomers.clear(); await loadCustomers(); await loadStats(); toast('已删除', 'ok');
    });
  }

  function exportCustomers(which) {
    const rows = which === 'precise' ? (state.precise || []) : (state.customers || []);
    if (!rows.length) { toast('没有客户可导出', 'warn'); return; }
    const head = ['昵称', 'username', '评论', '关键词', '视频', '意向分'];
    const lines = [head.join(',')].concat(rows.map((r) => [r.nickname, r.username, r.content, r.keyword, r.video_title, r.intent_score].map((v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"').join(',')));
    const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'channels_' + which + '_' + Date.now() + '.csv'; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  async function addMonitor() {
    const input = String(($('monInput') || {}).value || '').trim();
    if (!input) { toast('输入 username 或作品链接', 'warn'); return; }
    await guarded('addMonitor', async () => {
      const body = input.startsWith('http') ? { share_url: input, nickname: ($('monNick') || {}).value || '' } : { username: input, nickname: ($('monNick') || {}).value || '' };
      await api('/monitor/targets', { method: 'POST', body });
      $('monInput').value = ''; if ($('monNick')) $('monNick').value = '';
      await loadMonitor(); toast('已添加监控', 'ok');
    });
  }

  async function refreshMonitor(id) {
    await guarded('refreshMonitor', async () => {
      const data = await api('/monitor/targets/' + id + '/refresh', { method: 'POST', body: {} });
      state.monitor.videos = (data && data.videos) || [];
      await loadMonitor(); toast('已刷新作品', 'ok');
    });
  }

  async function deleteMonitor(id) {
    if (!confirm('删除该监控目标？')) return;
    await guarded('deleteMonitor', async () => { await api('/monitor/targets/' + id + '/delete', { method: 'POST', body: {} }); await loadMonitor(); });
  }

  async function runMonitorTarget() {
    const checked = Array.from(document.querySelectorAll('[data-mvid]')).filter((el) => el.checked).map((el) => el.getAttribute('data-mvid'));
    const targets = (state.monitor && state.monitor.targets) || [];
    if (!targets.length) { toast('先添加监控目标', 'warn'); return; }
    const targetId = targets[0].id;
    await guarded('runMonitor', async () => {
      if (checked.length) await api('/monitor/targets/' + targetId + '/videos/select', { method: 'POST', body: { video_ids: checked } });
      const data = await api('/monitor/run', { method: 'POST', body: { target_id: targetId, video_ids: checked } });
      const created = (data && data.created) || [];
      for (const task of created) { try { await api('/tasks/' + task.id + '/run', { method: 'POST', body: {} }); } catch (e) {} }
      await loadTasks(); await loadCustomers(); await loadStats();
      toast('已创建并采集 ' + created.length + ' 个作品', 'ok');
    });
  }

  async function collectInbox() {
    await guarded('collectInbox', async () => {
      const data = await api('/inbox/collect', { method: 'POST', body: {} });
      if (data && data.ok === false) { toast(data.error || '采集失败', 'bad'); return; }
      await loadInbox(); toast('已采集 ' + ((data && data.rows && data.rows.length) || 0) + ' 个会话', 'ok');
    });
  }

  function pickConversation(nickname) { if ($('inboxNickname')) $('inboxNickname').value = nickname; }

  async function sendInbox() {
    const nickname = String(($('inboxNickname') || {}).value || '').trim();
    const text = String(($('inboxMessage') || {}).value || '').trim();
    if (!nickname || !text) { toast('填写会话昵称和内容', 'warn'); return; }
    if (!confirm('确认发送给「' + nickname + '」？')) return;
    await guarded('sendInbox', async () => { const data = await api('/inbox/send', { method: 'POST', body: { nickname, text } }); toast('已发送', 'ok'); });
  }

  async function startInteraction() {
    const text = String(($('intentMessage') || {}).value || '').trim();
    if (!text) { toast('先填写私信话术', 'warn'); return; }
    const ids = Array.from(state.selectedCustomers);
    await guarded('interaction', async () => {
      const data = await api('/interaction/start', { method: 'POST', body: { ids, message: text } });
      if ($('interactionState')) $('interactionState').textContent = `发送 ${data.sent || 0}，失败 ${data.failed || 0}`;
      toast('私信执行完成：成功 ' + (data.sent || 0) + '，失败 ' + (data.failed || 0), 'ok');
    });
  }

  async function runSelfComments() {
    await guarded('self', async () => {
      const data = await api('/self-comment-monitor/run-once', { method: 'POST', body: {} });
      if (data && data.ok === false) { toast(data.error || '采集失败', 'bad'); return; }
      state.selfComments = (data && data.rows) || [];
      renderSelfComments(); toast('已采集 ' + state.selfComments.length + ' 条评论', 'ok');
    });
  }

  async function createPlan() {
    const keyword = String(($('planKeyword') || {}).value || '').trim();
    if (!keyword) { toast('输入关键词', 'warn'); return; }
    await guarded('createPlan', async () => {
      await api('/scheduler/plans', { method: 'POST', body: { keyword, sort: ($('planSort') || {}).value || 'hot', publish_time: ($('planPt') || {}).value || 'week', max_results: Number(($('planMax') || {}).value || 20) } });
      $('planKeyword').value = ''; await loadPlans(); toast('计划已保存', 'ok');
    });
  }

  async function runPlan(id) {
    await guarded('runPlan', async () => {
      const data = await api('/scheduler/plans/' + id + '/run', { method: 'POST', body: {} });
      const session = (data && data.session) || {};
      state.session = session; state.items = session.items || []; state.selectedVideos.clear();
      renderSearchResults(); await loadPlans(); showSection('search'); toast('计划执行完成，已跳到搜索结果', 'ok');
    });
  }

  async function togglePlan(id) { await guarded('togglePlan', async () => { await api('/scheduler/plans/' + id + '/toggle', { method: 'POST', body: {} }); await loadPlans(); }); }
  async function deletePlan(id) { if (!confirm('删除计划？')) return; await guarded('deletePlan', async () => { await api('/scheduler/plans/' + id + '/delete', { method: 'POST', body: {} }); await loadPlans(); }); }

  async function saveConfig() {
    const key = String(($('cfgKey') || {}).value || '').trim();
    if (!key) { toast('请输入 TikHub API Key；留空不会覆盖现有配置', 'warn'); return; }
    await guarded('saveKey', async () => {
      await api('/config', { method: 'POST', body: { tikhub_api_key: key } });
      if ($('cfgKey')) $('cfgKey').value = '';
      await loadSettings(); toast('TikHub Key 已保存', 'ok');
    });
  }

  async function saveLimits() {
    await guarded('saveLimits', async () => {
      await api('/config', { method: 'POST', body: {
        max_results: Number(($('cfgMaxResults') || {}).value || 20),
        max_comments: Number(($('cfgMaxComments') || {}).value || 100),
        enrich_limit: Number(($('cfgEnrich') || {}).value || 0),
        send_min_interval: Number(($('cfgInterval') || {}).value || 6),
        intent_terms: String(($('cfgIntent') || {}).value || ''),
      }});
      await loadSettings(); toast('配置已保存', 'ok');
    });
  }

  function showSection(key) {
    if (!TITLES[key]) key = 'overview';
    document.querySelectorAll('.section').forEach((el) => el.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach((el) => el.classList.toggle('active', el.getAttribute('data-key') === key));
    const sec = $('sec-' + key); if (sec) sec.classList.add('active');
    const t = TITLES[key] || [key, ''];
    if ($('pageTitle')) $('pageTitle').textContent = t[0];
    if ($('pageSub')) $('pageSub').textContent = t[1];
    try { history.replaceState(null, '', '#/' + key); } catch (e) {}
    const side = $('sidebar'); if (side) side.classList.remove('open');
    if (key === 'overview') { guarded('overview', async () => { await loadStats(); await loadCapabilities(); }); }
    else if (key === 'collect') guarded('collectPage', loadTasks);
    else if (key === 'all-customers' || key === 'intent') guarded('customersPage', loadCustomers);
    else if (key === 'monitor') guarded('monitorPage', loadMonitor);
    else if (key === 'interaction') guarded('inboxPage', loadInbox);
    else if (key === 'scheduler') guarded('plansPage', loadPlans);
    else if (key === 'config') guarded('configPage', async () => { await loadSettings(); await loadCapabilities(); });
    else if (key === 'logs') guarded('logsPage', loadLogs);
    else if (key === 'guide') guarded('guidePage', loadCapabilities);
  }

  async function init() {
    buildNav();
    const order = ['overview', 'scheduler', 'config', 'nurture', 'search', 'monitor', 'collect', 'follow', 'mention', 'interaction', 'stranger-leads', 'self-comments', 'all-customers', 'intent', 'groups', 'logs', 'guide'];
    $('content').innerHTML = order.map(sectionHTML).join('');
    let key = 'overview';
    const hash = String(location.hash || '').replace(/^#\/?/, '');
    if (hash && TITLES[hash]) key = hash;
    showSection(key);
    await Promise.allSettled([loadStats(), loadCapabilities(), loadSettings(), loadTasks(), loadCustomers(), loadInbox(), loadMonitor(), loadPlans(), loadLogs()]);
    checkLogin();
  }

  Object.assign(window, {
    showSection, doSearch, expandKeywords, toggleVideo, toggleVideos, collectSelected, exportSearch,
    viewTask, runTask, loadTasks, loadCustomers, renderCustomers, addPrecise, deleteCustomers, exportCustomers,
    addMonitor, loadMonitor, refreshMonitor, deleteMonitor, runMonitorTarget,
    collectInbox, pickConversation, sendInbox, startInteraction, runSelfComments,
    createPlan, loadPlans, runPlan, togglePlan, deletePlan, saveConfig, saveLimits,
    checkLogin, openLogin, loadLogs,
  });

  init();
})();
