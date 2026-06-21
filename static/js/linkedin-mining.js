(function() {
  var state = {
    tab: 'new',
    jobs: [],
    activeJobId: '',
    activeOutputId: '',
    activeLeadKey: '',
    activeLeadSegment: 'all',
    docs: [],
    pollTimer: null
  };
  var DRAFT_KEY = 'linkedinMining.inputDraft.v1';
  var LEAD_STATE_PREFIX = 'linkedinMining.leadState.';

  function $(id) { return document.getElementById(id); }

  function esc(text) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(text || ''));
    return String(text || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function authHeaderJson() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (!h.Authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    if (typeof getOrCreateInstallationId === 'function') h['X-Installation-Id'] = getOrCreateInstallationId();
    h['Content-Type'] = 'application/json';
    return h;
  }

  function apiBase() {
    return (typeof API_BASE !== 'undefined' && API_BASE ? String(API_BASE) : '').replace(/\/$/, '');
  }

  function parseErr(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.error || data.message;
    if (typeof detail === 'string') return detail;
    try { return JSON.stringify(detail || data); } catch (e) { return fallback || '请求失败'; }
  }

  function apiJson(path, opts) {
    opts = opts || {};
    var base = apiBase();
    if (!base) return Promise.reject(new Error('未配置服务器 API_BASE'));
    var req = { method: opts.method || 'GET', headers: authHeaderJson() };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body || {});
    return fetch(base + path, req).catch(function(err) {
      if (/Failed to fetch|NetworkError|Load failed/i.test(String(err && err.message || ''))) {
        throw new Error('网络请求中断：服务器处理较久或连接被断开，可在任务记录里继续执行。');
      }
      throw err;
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseErr(data, '请求失败'));
        return data;
      });
    });
  }

  function setMsg(text, isErr) {
    var node = $('linkedinMiningMsg');
    if (!node) return;
    node.textContent = text || '';
    node.className = 'li-msg' + (isErr ? ' err' : '');
    node.style.display = text ? 'block' : 'none';
  }

  function switchTab(tab) {
    state.tab = tab || 'new';
    document.querySelectorAll('#content-linkedin-mining [data-li-tab]').forEach(function(btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-li-tab') === state.tab);
    });
    document.querySelectorAll('#content-linkedin-mining [data-li-panel]').forEach(function(panel) {
      panel.classList.toggle('is-active', panel.getAttribute('data-li-panel') === state.tab);
    });
  }

  function lines(value) {
    return String(value || '').split(/\r?\n|[,，]/).map(function(x) { return x.trim(); }).filter(Boolean);
  }

  function readInputs() {
    return {
      title: ($('liJobTitleInput') || {}).value || '',
      seed_profile_urls: lines(($('liSeedProfilesInput') || {}).value),
      seed_company_urls: lines(($('liSeedCompaniesInput') || {}).value),
      keywords: lines(($('liKeywordsInput') || {}).value),
      hashtags: lines(($('liHashtagsInput') || {}).value),
      target_profile: ($('liTargetProfileInput') || {}).value || '',
      max_people: Number(($('liMaxPeopleInput') || {}).value || 30),
      max_company_employees: Number(($('liMaxEmployeesInput') || {}).value || 20),
      max_interactions_per_post: Number(($('liMaxInteractionsInput') || {}).value || 20),
      memory_docs: selectedDocs(),
      auto_run: true
    };
  }

  function selectedDocs() {
    var sel = $('liMemorySelect');
    if (!sel) return [];
    var ids = Array.prototype.slice.call(sel.selectedOptions || []).map(function(opt) { return opt.value; });
    return state.docs.filter(function(doc) {
      var id = String(doc.id || doc.doc_id || doc.filename || doc.name || '');
      return ids.indexOf(id) >= 0;
    });
  }

  function saveDraft() {
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify(readInputs())); } catch (e) {}
    setMsg('已保存输入。', false);
  }

  function loadDraft() {
    var data = {};
    try { data = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}') || {}; } catch (e) { data = {}; }
    if ($('liJobTitleInput')) $('liJobTitleInput').value = data.title || '';
    if ($('liSeedProfilesInput')) $('liSeedProfilesInput').value = (data.seed_profile_urls || []).join('\n');
    if ($('liSeedCompaniesInput')) $('liSeedCompaniesInput').value = (data.seed_company_urls || []).join('\n');
    if ($('liKeywordsInput')) $('liKeywordsInput').value = (data.keywords || []).join('\n');
    if ($('liHashtagsInput')) $('liHashtagsInput').value = (data.hashtags || []).join('\n');
    if ($('liTargetProfileInput')) $('liTargetProfileInput').value = data.target_profile || '';
    if ($('liMaxPeopleInput')) $('liMaxPeopleInput').value = data.max_people || 30;
    if ($('liMaxEmployeesInput')) $('liMaxEmployeesInput').value = data.max_company_employees || 20;
    if ($('liMaxInteractionsInput')) $('liMaxInteractionsInput').value = data.max_interactions_per_post || 20;
    var selected = (data.memory_docs || []).map(function(doc) { return String(doc.id || doc.doc_id || doc.filename || doc.name || ''); });
    var sel = $('liMemorySelect');
    if (sel) Array.prototype.slice.call(sel.options || []).forEach(function(opt) { opt.selected = selected.indexOf(opt.value) >= 0; });
  }

  function statusBadge(status) {
    var cls = status === 'completed' ? 'done' : (status === 'failed' ? 'fail' : (status === 'running' || status === 'queued' ? 'run' : ''));
    var label = { queued: '排队', running: '执行中', completed: '完成', failed: '失败', skipped: '跳过' }[status] || status || '未知';
    return '<span class="li-badge ' + cls + '">' + esc(label) + '</span>';
  }

  function fmtTime(value) {
    if (!value) return '';
    try {
      var d = new Date(value);
      if (!isNaN(d.getTime())) return d.toLocaleString();
    } catch (e) {}
    return String(value);
  }

  function activeJob() {
    return state.jobs.filter(function(j) { return j.job_id === state.activeJobId; })[0] || null;
  }

  function leadStateKey(jobId) {
    return LEAD_STATE_PREFIX + String(jobId || '');
  }

  function loadLeadState(jobId) {
    if (!jobId) return {};
    try { return JSON.parse(localStorage.getItem(leadStateKey(jobId)) || '{}') || {}; } catch (e) { return {}; }
  }

  function saveLeadState(jobId, map) {
    if (!jobId) return;
    try { localStorage.setItem(leadStateKey(jobId), JSON.stringify(map || {})); } catch (e) {}
  }

  function leadKey(item) {
    item = item || {};
    return String(item.candidate_key || item.url || item.name || item.profile_url || item.company || JSON.stringify({
      name: item.name || '',
      company: item.company || '',
      role: item.role || item.headline || ''
    })).slice(0, 240);
  }

  function normalizeContactText(contact) {
    if (!contact) return '';
    if (typeof contact === 'string') return contact;
    var rows = [];
    if (contact.email) rows.push(contact.email);
    if (Array.isArray(contact.phone_numbers)) rows = rows.concat(contact.phone_numbers);
    if (contact.wechat) rows.push(contact.wechat);
    if (Array.isArray(contact.websites)) rows = rows.concat(contact.websites);
    if (Array.isArray(contact.twitter)) rows = rows.concat(contact.twitter);
    if (contact.address) rows.push(contact.address);
    return rows.filter(Boolean).join('，');
  }

  function contactFromText(text) {
    text = String(text || '').trim();
    if (!text || text === '-') return {};
    var contact = {};
    var email = text.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
    if (email) contact.email = email[0];
    var urls = text.match(/https?:\/\/[^\s，,；;]+|www\.[^\s，,；;]+/ig);
    if (urls && urls.length) contact.websites = urls;
    var phones = text.match(/(?:\+?\d[\d\s().-]{6,}\d)/g);
    if (phones && phones.length) contact.phone_numbers = phones.map(function(x) { return x.trim(); });
    if (!Object.keys(contact).length) contact.note = text;
    return contact;
  }

  function normalizeLead(raw, source) {
    raw = raw || {};
    var contact = raw.contact && typeof raw.contact === 'object' ? raw.contact : contactFromText(raw.contact || raw.contact_status || '');
    var name = raw.name || raw.candidate_key || raw.author || raw.username || '-';
    var role = raw.role || raw.headline || raw.title || '';
    var company = raw.company || raw.company_name || '';
    var score = Number(raw.score || raw.rank_score || 0);
    var next = raw.next_action || raw.next_step || raw.next || nextActionFromContact(contact, raw);
    var why = raw.why || raw.reason || raw.source_reason || raw.source_type || '';
    var url = raw.url || raw.profile_url || raw.source || '';
    return {
      key: leadKey(raw),
      name: String(name || '-'),
      role: String(role || ''),
      company: String(company || ''),
      score: score || '',
      contact: contact,
      contact_text: normalizeContactText(contact) || raw.contact_status || raw.contact || '',
      why: String(why || ''),
      opening_line: String(raw.opening_line || raw.opening || ''),
      next_action: String(next || ''),
      next_step: String(raw.next_step || next || ''),
      source: String(source || raw.source || raw.source_type || ''),
      url: String(url || ''),
      evidence_count: raw.evidence_count || (Array.isArray(raw.evidence) ? raw.evidence.length : 0),
      raw: raw
    };
  }

  function classifyLead(lead) {
    var score = Number(lead.score || 0);
    if (lead.contact_text && (score >= 70 || /founder|ceo|owner|director|vp|创始|老板|负责人/i.test(lead.role))) return 'a';
    if (score >= 70 || /founder|ceo|owner|director|vp|创始|老板|负责人/i.test(lead.role)) return 'b';
    if (!lead.contact_text) return 'supplement';
    return 'watch';
  }

  function buildLeads(job) {
    if (!job) return [];
    var report = job.result_payload && job.result_payload.report || {};
    var payload = job.result_payload || {};
    var out = [];
    var seen = {};
    function add(raw, source, forcedSegment) {
      var lead = normalizeLead(raw, source);
      if (!lead.name || lead.name === '-') return;
      if (seen[lead.key]) {
        var old = seen[lead.key];
        if (forcedSegment) old.segment = forcedSegment;
        if (!old.contact_text && lead.contact_text) {
          old.contact = lead.contact;
          old.contact_text = lead.contact_text;
        }
        if (!old.opening_line && lead.opening_line) old.opening_line = lead.opening_line;
        if (!old.why && lead.why) old.why = lead.why;
        if (!old.next_action && lead.next_action) old.next_action = lead.next_action;
        if (!old.url && lead.url) old.url = lead.url;
        old.sources = old.sources || [];
        if (source && old.sources.indexOf(source) < 0) old.sources.push(source);
        return;
      }
      lead.sources = source ? [source] : [];
      lead.segment = forcedSegment || classifyLead(lead);
      seen[lead.key] = lead;
      out.push(lead);
    }
    var wb = report.action_workbench && typeof report.action_workbench === 'object' ? report.action_workbench : {};
    (Array.isArray(wb.list_a) ? wb.list_a : []).forEach(function(x) { add(x, '行动工作台A类', 'a'); });
    (Array.isArray(wb.list_b) ? wb.list_b : []).forEach(function(x) { add(x, '行动工作台B类', 'b'); });
    (Array.isArray(wb.watch_list) ? wb.watch_list : []).forEach(function(x) { add(x, '行动工作台观察', 'watch'); });
    (Array.isArray(report.priority_leads) ? report.priority_leads : []).forEach(function(x) { add(x, '优先线索'); });
    (Array.isArray(report.contact_list) ? report.contact_list : []).forEach(function(x) { add(x, '联系方式列表'); });
    var leadSummary = payload.lead_summary || {};
    (Array.isArray(leadSummary.top_leads) ? leadSummary.top_leads : []).forEach(function(x) { add(x, '候选人评分'); });
    (Array.isArray(payload.candidates) ? payload.candidates : []).forEach(function(x) { add(x, '候选人池'); });
    out.sort(function(a, b) {
      var order = { a: 0, b: 1, supplement: 2, watch: 3 };
      return (order[a.segment] || 9) - (order[b.segment] || 9) || Number(b.score || 0) - Number(a.score || 0);
    });
    var local = loadLeadState(job.job_id);
    out.forEach(function(lead) {
      var s = local[lead.key] || {};
      if (s.segment) lead.segment = s.segment;
      lead.status = s.status || '待处理';
      lead.note = s.note || '';
      lead.local_actions = s.actions || {};
    });
    return out;
  }

  function renderJobs() {
    var host = $('liJobList');
    if (!host) return;
    if (!state.jobs.length) {
      host.innerHTML = '<div class="li-meta">暂无任务。</div>';
      renderActiveJob();
      return;
    }
    host.innerHTML = state.jobs.map(function(job) {
      var active = job.job_id === state.activeJobId ? ' is-active' : '';
      return '<div class="li-item' + active + '" data-li-job-id="' + esc(job.job_id) + '">'
        + '<strong>' + esc(job.title || 'LinkedIn线索挖掘') + '</strong>'
        + '<span class="li-meta">' + esc(fmtTime(job.created_at)) + ' · ' + esc(job.progress || 0) + '%</span>'
        + '<div class="li-badges">' + statusBadge(job.status) + '<span class="li-badge">' + esc((job.steps || []).length) + ' 步</span></div>'
        + (job.error ? '<span class="li-meta" style="color:#be123c;">' + esc(job.error) + '</span>' : '')
        + '</div>';
    }).join('');
    host.querySelectorAll('[data-li-job-id]').forEach(function(node) {
      node.addEventListener('click', function() {
        state.activeJobId = node.getAttribute('data-li-job-id') || '';
        renderJobs();
        renderActiveJob();
        switchTab('runs');
      });
    });
    renderActiveJob();
  }

  function renderActiveJob() {
    var job = activeJob();
    var bar = $('liProgressBar');
    if (bar) bar.style.width = Math.max(0, Math.min(100, Number(job && job.progress || 0))) + '%';
    renderSteps(job);
    renderOutputs(job);
    renderReport(job);
    renderWorkbench(job);
  }

  function renderSteps(job) {
    var host = $('liStepList');
    if (!host) return;
    if (!job) {
      host.innerHTML = '<div class="li-meta">请选择一个任务。</div>';
      return;
    }
    var steps = job.steps || [];
    host.innerHTML = steps.map(function(step, idx) {
      var st = step.status || 'pending';
      return '<div class="li-step is-' + esc(st) + '">'
        + '<span class="li-step-dot">' + esc(idx + 1) + '</span>'
        + '<div><strong>' + esc(step.label || step.key) + '</strong>'
        + '<span class="li-meta">' + esc(step.detail || '') + '</span>'
        + (step.error ? '<span class="li-meta" style="color:#be123c;">' + esc(step.error) + '</span>' : '')
        + '</div>'
        + '<div class="li-copy-row">' + statusBadge(st)
        + (st === 'failed' ? '<button type="button" class="btn btn-ghost btn-sm" data-retry-step="' + esc(step.key) + '">重试</button>' : '')
        + '</div></div>';
    }).join('');
    host.querySelectorAll('[data-retry-step]').forEach(function(btn) {
      btn.addEventListener('click', function() { runNext(btn.getAttribute('data-retry-step') || ''); });
    });
  }

  function renderOutputs(job) {
    var host = $('liOutputList');
    if (!host) return;
    if (!job || !(job.outputs || []).length) {
      host.innerHTML = '<div class="li-meta">暂无输出。任务执行后，每一步结果会出现在这里。</div>';
      if ($('liOutputDetail')) $('liOutputDetail').textContent = '请选择左侧输出。';
      return;
    }
    host.innerHTML = (job.outputs || []).slice().reverse().map(function(out) {
      var active = out.id === state.activeOutputId ? ' is-active' : '';
      return '<div class="li-item' + active + '" data-output-id="' + esc(out.id) + '">'
        + '<strong>' + esc(out.title || out.kind || '输出') + '</strong>'
        + '<span class="li-meta">' + esc(out.kind || '') + ' · ' + esc(fmtTime(out.created_at)) + '</span>'
        + '</div>';
    }).join('');
    host.querySelectorAll('[data-output-id]').forEach(function(node) {
      node.addEventListener('click', function() {
        state.activeOutputId = node.getAttribute('data-output-id') || '';
        renderOutputDetail();
        loadOutputDetail(state.activeOutputId);
        renderOutputs(activeJob());
      });
    });
    renderOutputDetail();
  }

  function renderOutputDetail() {
    var job = activeJob();
    var detail = $('liOutputDetail');
    if (!detail) return;
    var out = null;
    if (job) out = (job.outputs || []).filter(function(x) { return x.id === state.activeOutputId; })[0] || null;
    if (!out && job && (job.outputs || []).length) out = job.outputs[job.outputs.length - 1];
    if (!out) {
      detail.textContent = '请选择左侧输出。';
      return;
    }
    state.activeOutputId = out.id;
    detail.innerHTML = renderPrettyOutput(out);
  }

  function renderPrettyOutput(out) {
    var data = out && (out.data || out) || {};
    if (data.lead_summary || (data.summary && Array.isArray(data.top_leads))) {
      return renderLeadSummary(data.lead_summary || data);
    }
    if (data && Array.isArray(data.queries)) {
      return renderQueryOutput(data);
    }
    if (data && Array.isArray(data.candidates)) {
      return renderLeadSummary(data);
    }
    if (data && Array.isArray(data.hashtag_queries)) {
      return renderInteractionOutput(data);
    }
    if (out && out.kind === 'report') {
      return renderReportBody(data);
    }
    if (data && (data.executive_summary || data.priority_leads || data.contact_list)) {
      return renderReportBody(data);
    }
    return '<pre style="white-space:pre-wrap;margin:0;">' + esc(JSON.stringify(data, null, 2)) + '</pre>';
  }

  function renderQueryOutput(data) {
    var queries = data.queries || [];
    var cards = queries.map(renderQueryCard).join('');
    return '<div class="li-output-section">'
      + '<h3>执行结果</h3>'
      + '<p>这一轮主要用于同步数据。可直接查看哪些资料已返回联系方式、哪些查询为空。</p>'
      + '<div class="li-output-cards">' + cards + '</div>'
      + renderRawDetails(data)
      + '</div>';
  }

  function renderInteractionOutput(data) {
    return '<div class="li-output-section">'
      + '<h3>话题内容与互动</h3>'
      + '<div class="li-stats">'
      + statCard('话题查询', (data.hashtag_queries || []).length)
      + statCard('可追踪帖子', (data.post_ids || []).length)
      + statCard('互动查询', (data.interaction_queries || []).length)
      + '</div>'
      + '<h4>话题同步</h4><div class="li-output-cards">' + (data.hashtag_queries || []).map(renderQueryCard).join('') + '</div>'
      + '<h4>互动抓取</h4><div class="li-output-cards">' + (data.interaction_queries || []).map(renderQueryCard).join('') + '</div>'
      + renderRawDetails(data)
      + '</div>';
  }

  function renderLeadSummary(data) {
    data = data || {};
    var summary = data.summary || {};
    var leads = data.top_leads || data.candidates || [];
    if (data.lead_summary) {
      summary = data.lead_summary.summary || summary;
      leads = data.lead_summary.top_leads || leads;
    }
    var contactLeads = leads.filter(function(item) { return hasContact(item.contact || {}); });
    return '<div class="li-output-section">'
      + '<h3>可跟进线索</h3>'
      + '<div class="li-stats">'
      + statCard('候选人', summary.candidate_count || leads.length || 0)
      + statCard('有公开联系方式', summary.with_public_contact || contactLeads.length || 0)
      + statCard('数据来源', summary.source_rows || 0)
      + '</div>'
      + '<div class="li-toolbar-note">优先看有公开邮箱、网站、电话或微信的线索；没有联系方式的，先从 LinkedIn 主页和互动证据继续补充。</div>'
      + '<div class="li-output-cards">' + leads.map(renderLeadCard).join('') + '</div>'
      + renderRawDetails(data)
      + '</div>';
  }

  function statCard(label, value) {
    return '<div class="li-stat"><strong>' + esc(value) + '</strong><span>' + esc(label) + '</span></div>';
  }

  function hasContact(contact) {
    contact = contact || {};
    return !!(contact.email || contact.wechat || contact.address
      || (Array.isArray(contact.phone_numbers) && contact.phone_numbers.length)
      || (Array.isArray(contact.websites) && contact.websites.length)
      || (Array.isArray(contact.twitter) && contact.twitter.length));
  }

  function renderQueryCard(item) {
    item = item || {};
    var title = item.username || item.company || item.keyword || item.hashtag || item.query_type || '输出';
    var contact = item.contact || {};
    var contactHtml = renderContact(contact);
    return '<div class="li-output-card">'
      + '<div class="li-output-head"><strong>' + esc(title) + '</strong>' + statusBadge(item.ok ? 'completed' : (item.skipped ? 'skipped' : 'failed')) + '</div>'
      + '<div class="li-meta">' + esc(item.query_type || '') + (item.query_id ? ' · ' + esc(item.query_id) : '') + '</div>'
      + (item.reason ? '<div class="li-meta">' + esc(item.reason) + '</div>' : '')
      + (contactHtml || '<div class="li-meta">未返回公开邮箱/电话/微信等联系方式。</div>')
      + '</div>';
  }

  function renderLeadCard(item) {
    item = item || {};
    var contact = item.contact || {};
    var evidenceCount = item.evidence_count || (Array.isArray(item.evidence) ? item.evidence.length : 0);
    return '<div class="li-output-card">'
      + '<div class="li-output-head"><strong>' + esc((item.rank ? '#' + item.rank + ' ' : '') + (item.name || item.candidate_key || '候选人')) + '</strong>'
      + '<span class="li-score">' + esc(item.score || '-') + '</span></div>'
      + (item.headline ? '<div class="li-main-text">' + esc(item.headline) + '</div>' : '')
      + (item.company ? '<div class="li-meta">公司：' + esc(item.company) + '</div>' : '')
      + renderContact(contact)
      + '<div class="li-next-action"><span>建议动作</span><strong>' + esc(item.next_action || nextActionFromContact(contact, item)) + '</strong></div>'
      + '<div class="li-meta">证据来源：' + esc(item.source_reason || item.source_type || '-') + (evidenceCount ? ' · 证据 ' + esc(evidenceCount) + ' 条' : '') + '</div>'
      + (item.url ? '<div><a href="' + esc(item.url) + '" target="_blank" rel="noopener">' + esc(item.url) + '</a></div>' : '')
      + '</div>';
  }

  function renderCandidateCard(item) {
    item = item || {};
    return renderLeadCard(item);
  }

  function nextActionFromContact(contact, item) {
    contact = contact || {};
    if (contact.email) return '优先邮件触达，开场围绕他的职位、公司或近期内容。';
    if (Array.isArray(contact.websites) && contact.websites.length) return '先打开公开网站补背景，再决定邮件或表单触达。';
    if (Array.isArray(contact.phone_numbers) && contact.phone_numbers.length) return '可人工电话触达，先确认身份和业务相关性。';
    if (contact.wechat) return '可人工微信触达，先用一句话说明来源和价值。';
    if (item && item.url) return '先打开 LinkedIn 主页核对背景，继续补联系方式。';
    return '先保留为待补充线索。';
  }

  function renderContact(contact) {
    contact = contact || {};
    var rows = [];
    if (contact.email) rows.push(['邮箱', contact.email]);
    if (Array.isArray(contact.phone_numbers) && contact.phone_numbers.length) rows.push(['电话', contact.phone_numbers.join('，')]);
    if (contact.wechat) rows.push(['微信', contact.wechat]);
    if (Array.isArray(contact.websites) && contact.websites.length) rows.push(['网站', contact.websites.join('，')]);
    if (Array.isArray(contact.twitter) && contact.twitter.length) rows.push(['Twitter', contact.twitter.join('，')]);
    if (contact.address) rows.push(['地址', contact.address]);
    if (!rows.length) return '';
    return '<div class="li-contact-box">'
      + rows.map(function(row) { return '<div><span>' + esc(row[0]) + '</span><strong>' + esc(row[1]) + '</strong></div>'; }).join('')
      + '</div>';
  }

  function renderReportBody(report) {
    report = report || {};
    function block(title, html) {
      if (!html) return '';
      return '<div class="li-report-section"><h4>' + esc(title) + '</h4>' + html + '</div>';
    }
    function paragraph(value) {
      if (!value) return '';
      return '<div class="li-main-text">' + esc(typeof value === 'string' ? value : JSON.stringify(value, null, 2)) + '</div>';
    }
    var contactList = Array.isArray(report.contact_list) ? report.contact_list : [];
    var priority = Array.isArray(report.priority_leads) ? report.priority_leads : [];
    var actions = Array.isArray(report.next_actions) ? report.next_actions : [];
    var limitations = Array.isArray(report.limitations) ? report.limitations : [];
    var overview = report.lead_overview || {};
    var actionWorkbench = report.action_workbench && typeof report.action_workbench === 'object' ? report.action_workbench : {};
    return '<div class="li-report">'
      + block('执行摘要', paragraph(report.executive_summary))
      + block('线索概览', '<div class="li-stats">' + statCard('候选人', overview.candidate_count || '-') + statCard('有公开联系方式', overview.with_public_contact || '-') + statCard('建议', overview.recommendation || '-') + '</div>')
      + block('联系方式列表', contactList.length ? '<div class="li-table-wrap"><table class="li-table"><thead><tr><th>姓名</th><th>角色/公司</th><th>联系方式</th><th>下一步</th></tr></thead><tbody>' + contactList.map(function(item) {
          return '<tr><td>' + esc(item.name || '-') + '</td><td>' + esc([item.role, item.company].filter(Boolean).join(' / ') || '-') + '</td><td>' + esc(item.contact || '-') + '</td><td>' + esc(item.next_action || '-') + '</td></tr>';
        }).join('') + '</tbody></table></div>' : '<div class="li-meta">本轮没有可直接展示的公开联系方式。</div>')
      + block('优先跟进线索', priority.length ? '<div class="li-output-cards">' + priority.map(function(item) {
          return '<div class="li-output-card"><div class="li-output-head"><strong>' + esc(item.name || '-') + '</strong><span class="li-score">' + esc(item.score || '-') + '</span></div>'
            + '<div class="li-main-text">' + esc(item.why || '') + '</div>'
            + '<div class="li-meta">联系方式：' + esc(item.contact_status || '-') + '</div>'
            + '<div class="li-next-action"><span>开场白</span><strong>' + esc(item.opening_line || '-') + '</strong></div>'
            + '<div class="li-next-action"><span>下一步</span><strong>' + esc(item.next_step || '-') + '</strong></div></div>';
        }).join('') + '</div>' : '')
      + block('行动工作台建议', renderActionWorkbenchReport(actionWorkbench))
      + block('下一步动作', actions.length ? '<ul class="li-bullet-list">' + actions.map(function(x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '')
      + block('数据限制', limitations.length ? '<ul class="li-bullet-list">' + limitations.map(function(x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '')
      + renderRawDetails(report)
      + '</div>';
  }

  function renderActionWorkbenchReport(workbench) {
    workbench = workbench || {};
    var parts = [];
    function list(title, rows) {
      rows = Array.isArray(rows) ? rows : [];
      if (!rows.length) return;
      parts.push('<h4>' + esc(title) + '</h4><div class="li-output-cards">' + rows.map(function(item) {
        return '<div class="li-output-card"><strong>' + esc(item.name || item.target || '-') + '</strong>'
          + '<div class="li-main-text">' + esc(item.reason || item.missing || item.copy || '') + '</div>'
          + '<div class="li-next-action"><span>下一步</span><strong>' + esc(item.next_action || item.how_to_fill || item.channel || '-') + '</strong></div></div>';
      }).join('') + '</div>');
    }
    list('A类核心名单', workbench.list_a);
    list('B类扩展名单', workbench.list_b);
    list('观察名单', workbench.watch_list);
    list('补资料任务', workbench.supplement_tasks);
    list('触达资产', workbench.outreach_assets);
    return parts.join('') || '';
  }

  function renderRawDetails(data) {
    return '<details class="li-raw-details"><summary>查看原始数据</summary><pre>' + esc(JSON.stringify(data || {}, null, 2)) + '</pre></details>';
  }

  function loadOutputDetail(outputId) {
    var job = activeJob();
    if (!job || !outputId) return Promise.resolve();
    var detail = $('liOutputDetail');
    if (detail) detail.textContent = '加载中...';
    return apiJson('/api/linkedin-mining/jobs/' + encodeURIComponent(job.job_id) + '/outputs/' + encodeURIComponent(outputId))
      .then(function(data) {
        var out = data.output || data;
        var replaced = false;
        job.outputs = (job.outputs || []).map(function(item) {
          if (item.id === outputId) {
            replaced = true;
            return Object.assign({}, item, out);
          }
          return item;
        });
        if (!replaced) job.outputs = (job.outputs || []).concat([out]);
        renderOutputDetail();
      })
      .catch(function(err) {
        if (detail) detail.textContent = (err && err.message) ? err.message : '输出详情加载失败';
      });
  }

  function renderReport(job) {
    var host = $('liReportHost');
    if (!host) return;
    var report = job && job.result_payload && job.result_payload.report;
    if (!report) {
      host.innerHTML = '<div class="li-meta">任务完成后会在这里展示最终报告。</div>';
      return;
    }
    host.innerHTML = renderReportBody(report);
  }

  function segmentLabel(seg) {
    return {
      all: '全部',
      a: 'A类核心',
      b: 'B类扩展',
      supplement: '待补资料',
      watch: '观察名单',
      done: '已处理'
    }[seg] || seg || '全部';
  }

  function renderWorkbench(job) {
    var segHost = $('liLeadSegments');
    var listHost = $('liLeadList');
    var detailHost = $('liLeadDetail');
    if (!segHost || !listHost || !detailHost) return;
    if (!job) {
      segHost.innerHTML = '';
      listHost.innerHTML = '<div class="li-meta">请选择一个任务。</div>';
      detailHost.innerHTML = '<div class="li-meta">请选择左侧线索。</div>';
      return;
    }
    var leads = buildLeads(job);
    if (!leads.length) {
      segHost.innerHTML = '';
      listHost.innerHTML = '<div class="li-meta">暂无可操作线索。任务完成候选人评分或最终报告后会自动生成名单。</div>';
      detailHost.innerHTML = '<div class="li-meta">暂无线索详情。</div>';
      return;
    }
    var counts = { all: leads.length, a: 0, b: 0, supplement: 0, watch: 0, done: 0 };
    leads.forEach(function(lead) {
      counts[lead.segment] = (counts[lead.segment] || 0) + 1;
      if (lead.status === '已处理') counts.done += 1;
    });
    var segs = ['all', 'a', 'b', 'supplement', 'watch', 'done'];
    if (segs.indexOf(state.activeLeadSegment) < 0) state.activeLeadSegment = 'all';
    segHost.innerHTML = segs.map(function(seg) {
      var active = state.activeLeadSegment === seg ? ' is-active' : '';
      return '<button type="button" class="li-segment-tab' + active + '" data-lead-seg="' + esc(seg) + '">'
        + esc(segmentLabel(seg)) + ' ' + esc(counts[seg] || 0) + '</button>';
    }).join('');
    segHost.querySelectorAll('[data-lead-seg]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        state.activeLeadSegment = btn.getAttribute('data-lead-seg') || 'all';
        renderWorkbench(activeJob());
      });
    });
    var filtered = leads.filter(function(lead) {
      if (state.activeLeadSegment === 'all') return true;
      if (state.activeLeadSegment === 'done') return lead.status === '已处理';
      return lead.segment === state.activeLeadSegment;
    });
    if (!filtered.length) {
      listHost.innerHTML = '<div class="li-meta">当前分组没有线索。</div>';
      detailHost.innerHTML = '<div class="li-meta">请选择其他分组。</div>';
      return;
    }
    if (!state.activeLeadKey || !leads.some(function(x) { return x.key === state.activeLeadKey; })) {
      state.activeLeadKey = filtered[0].key;
    }
    if (!filtered.some(function(x) { return x.key === state.activeLeadKey; })) {
      state.activeLeadKey = filtered[0].key;
    }
    listHost.innerHTML = filtered.map(function(lead) {
      var active = lead.key === state.activeLeadKey ? ' is-active' : '';
      return '<div class="li-lead-row' + active + '" data-lead-key="' + esc(lead.key) + '">'
        + '<div class="li-lead-row-top"><div><div class="li-lead-name">' + esc(lead.name) + '</div>'
        + '<div class="li-meta">' + esc([lead.role, lead.company].filter(Boolean).join(' / ') || '未标注角色公司') + '</div></div>'
        + '<span class="li-priority-chip ' + esc(lead.segment) + '">' + esc(segmentLabel(lead.segment)) + '</span></div>'
        + '<div class="li-badges"><span class="li-badge">' + esc(lead.status || '待处理') + '</span>'
        + (lead.contact_text ? '<span class="li-badge done">有联系方式</span>' : '<span class="li-badge run">待补资料</span>')
        + (lead.score ? '<span class="li-badge">评分 ' + esc(lead.score) + '</span>' : '') + '</div>'
        + '</div>';
    }).join('');
    listHost.querySelectorAll('[data-lead-key]').forEach(function(node) {
      node.addEventListener('click', function() {
        state.activeLeadKey = node.getAttribute('data-lead-key') || '';
        renderWorkbench(activeJob());
      });
    });
    var selected = leads.filter(function(x) { return x.key === state.activeLeadKey; })[0] || filtered[0];
    detailHost.innerHTML = renderLeadDetail(selected, job);
    bindLeadDetailActions(selected, job);
  }

  function renderLeadDetail(lead, job) {
    if (!lead) return '<div class="li-meta">请选择左侧线索。</div>';
    var contactHtml = renderContact(lead.contact) || (lead.contact_text ? '<div class="li-contact-box"><div><span>联系</span><strong>' + esc(lead.contact_text) + '</strong></div></div>' : '<div class="li-meta">暂无公开联系方式。</div>');
    var followCard = leadFollowCardText(lead, job);
    return '<div class="li-output-section">'
      + '<div class="li-output-card">'
      + '<div class="li-output-head"><div><h3>' + esc(lead.name) + '</h3><p class="li-main-text">' + esc([lead.role, lead.company].filter(Boolean).join(' / ') || '未标注角色公司') + '</p></div>'
      + '<span class="li-score">' + esc(lead.score || '-') + '</span></div>'
      + '<div class="li-badges"><span class="li-priority-chip ' + esc(lead.segment) + '">' + esc(segmentLabel(lead.segment)) + '</span><span class="li-badge">' + esc(lead.status || '待处理') + '</span></div>'
      + '</div>'
      + '<div class="li-detail-grid">'
      + '<div class="li-detail-box"><h4>判断依据</h4><div class="li-main-text">' + esc(lead.why || '暂无明确依据，建议先补资料。') + '</div><div class="li-meta">证据 ' + esc(lead.evidence_count || 0) + ' 条 · 来源 ' + esc((lead.sources || []).join('，') || lead.source || '-') + '</div></div>'
      + '<div class="li-detail-box"><h4>公开联系方式</h4>' + contactHtml + '</div>'
      + '</div>'
      + '<div class="li-detail-box"><h4>建议跟进方式</h4><div class="li-main-text">' + esc(lead.next_step || lead.next_action || nextActionFromContact(lead.contact, lead)) + '</div>'
      + (lead.opening_line ? '<div class="li-next-action"><span>开场白</span><strong>' + esc(lead.opening_line) + '</strong></div>' : '') + '</div>'
      + '<div class="li-action-board">'
      + renderActionItem('supplement', '继续补资料', '跳到执行过程，继续跑失败或未完成步骤。')
      + renderActionItem('script', '生成/复制触达话术', '根据当前报告里的开场白和判断依据生成可复制卡片。')
      + renderActionItem('list_a', '加入A类名单', '标记为马上跟进的核心名单。')
      + renderActionItem('watch', '加入观察名单', '暂不触达，后续继续观察内容互动。')
      + renderActionItem('done', '标记已处理', '本地记录为已处理，方便下一轮筛选。')
      + '</div>'
      + '<div class="li-detail-box"><h4>跟进卡片</h4><textarea id="liLeadFollowCard" class="li-note-editor">' + esc(followCard) + '</textarea></div>'
      + '<div class="li-detail-box"><h4>备注</h4><textarea id="liLeadNoteInput" class="li-note-editor" placeholder="记录人工判断、已联系渠道、下一次跟进时间">' + esc(lead.note || '') + '</textarea></div>'
      + (lead.url ? '<div><a href="' + esc(lead.url) + '" target="_blank" rel="noopener">' + esc(lead.url) + '</a></div>' : '')
      + '</div>';
  }

  function renderActionItem(action, title, desc) {
    return '<div class="li-action-item"><div><strong>' + esc(title) + '</strong><span>' + esc(desc) + '</span></div>'
      + '<button type="button" class="btn btn-ghost btn-sm" data-lead-action="' + esc(action) + '">执行</button></div>';
  }

  function updateLeadLocalState(job, lead, patch) {
    if (!job || !lead) return;
    var map = loadLeadState(job.job_id);
    var cur = map[lead.key] || {};
    map[lead.key] = Object.assign({}, cur, patch || {});
    saveLeadState(job.job_id, map);
    renderWorkbench(job);
  }

  function bindLeadDetailActions(lead, job) {
    var note = $('liLeadNoteInput');
    if (note) note.addEventListener('change', function() { updateLeadLocalState(job, lead, { note: note.value || '' }); });
    document.querySelectorAll('#content-linkedin-mining [data-lead-action]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var action = btn.getAttribute('data-lead-action') || '';
        if (action === 'supplement') {
          switchTab('runs');
          if (job && job.status !== 'completed') resumeJob();
          else setMsg('当前任务已完成；如需更多资料，可在新建任务里增加公司、关键词或话题后重新挖掘。', false);
          return;
        }
        if (action === 'script') {
          copyText(leadFollowCardText(lead, job), '跟进卡片已复制。');
          return;
        }
        if (action === 'list_a') updateLeadLocalState(job, lead, { segment: 'a', status: '待跟进' });
        if (action === 'watch') updateLeadLocalState(job, lead, { segment: 'watch', status: '观察中' });
        if (action === 'done') updateLeadLocalState(job, lead, { status: '已处理' });
      });
    });
  }

  function leadFollowCardText(lead, job) {
    lead = lead || {};
    var lines = [];
    lines.push('【线索】' + (lead.name || '-'));
    if (lead.role || lead.company) lines.push('【角色/公司】' + [lead.role, lead.company].filter(Boolean).join(' / '));
    lines.push('【优先级】' + segmentLabel(lead.segment) + (lead.score ? '，评分 ' + lead.score : ''));
    lines.push('【推荐原因】' + (lead.why || '待补充'));
    lines.push('【联系方式】' + (lead.contact_text || '暂无公开联系方式'));
    lines.push('【下一步】' + (lead.next_step || lead.next_action || nextActionFromContact(lead.contact, lead)));
    if (lead.opening_line) lines.push('【开场白】' + lead.opening_line);
    if (lead.url) lines.push('【主页】' + lead.url);
    if (job && job.title) lines.push('【来源任务】' + job.title);
    return lines.join('\n');
  }

  function copyText(text, okMsg) {
    text = String(text || '');
    if (!text) { setMsg('没有可复制内容。', true); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function() { setMsg(okMsg || '已复制。', false); }).catch(function() { fallbackCopy(text, okMsg); });
    } else {
      fallbackCopy(text, okMsg);
    }
  }

  function fallbackCopy(text, okMsg) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); setMsg(okMsg || '已复制。', false); } catch (e) { setMsg('复制失败。', true); }
    ta.remove();
  }

  function leadsMarkdown() {
    var job = activeJob();
    var leads = buildLeads(job);
    if (!job || !leads.length) return '';
    var lines = ['# ' + (job.title || 'LinkedIn线索名单'), ''];
    ['a', 'b', 'supplement', 'watch'].forEach(function(seg) {
      var rows = leads.filter(function(x) { return x.segment === seg; });
      if (!rows.length) return;
      lines.push('## ' + segmentLabel(seg), '');
      rows.forEach(function(lead, idx) {
        lines.push((idx + 1) + '. ' + lead.name + (lead.company ? ' / ' + lead.company : ''));
        lines.push('   - 角色：' + (lead.role || '-'));
        lines.push('   - 联系方式：' + (lead.contact_text || '待补充'));
        lines.push('   - 推荐原因：' + (lead.why || '-'));
        lines.push('   - 下一步：' + (lead.next_step || lead.next_action || '-'));
      });
      lines.push('');
    });
    return lines.join('\n');
  }

  function reportMarkdown() {
    var job = activeJob();
    var report = job && job.result_payload && job.result_payload.report;
    if (!report) return '';
    var lines = ['# ' + (job.title || 'LinkedIn线索挖掘报告'), ''];
    function add(title, body) {
      if (body === undefined || body === null || body === '') return;
      lines.push('## ' + title, '');
      if (Array.isArray(body)) {
        body.forEach(function(item) {
          lines.push('- ' + (typeof item === 'string' ? item : JSON.stringify(item, null, 2)));
        });
      } else {
        lines.push(typeof body === 'string' ? body : JSON.stringify(body, null, 2));
      }
      lines.push('');
    }
    add('执行摘要', report.executive_summary);
    add('目标客户画像', report.target_profile);
    add('候选人分层', report.candidate_segments);
    add('联系方式列表', report.contact_list);
    add('优先线索', report.priority_leads);
    add('关系路径', report.relationship_map);
    add('行动工作台', report.action_workbench);
    add('下一步动作', report.next_actions);
    add('限制说明', report.limitations);
    return lines.join('\n');
  }

  function loadJobs(silent) {
    return apiJson('/api/linkedin-mining/jobs?limit=50').then(function(data) {
      state.jobs = data.items || [];
      if (!state.activeJobId && state.jobs.length) state.activeJobId = state.jobs[0].job_id;
      renderJobs();
      if (!silent) setMsg('', false);
    }).catch(function(err) {
      setMsg(err.message || '任务加载失败', true);
    });
  }

  function loadDocs() {
    return apiJson('/api/openclaw-memory/docs').then(function(data) {
      state.docs = data.items || data.docs || [];
      var sel = $('liMemorySelect');
      if (!sel) return;
      sel.innerHTML = state.docs.map(function(doc) {
        var id = String(doc.id || doc.doc_id || doc.filename || doc.name || '');
        var title = doc.title || doc.name || doc.filename || id || '未命名记忆';
        return '<option value="' + esc(id) + '">' + esc(title) + '</option>';
      }).join('');
      loadDraft();
    }).catch(function() {
      var sel = $('liMemorySelect');
      if (sel) sel.innerHTML = '';
    });
  }

  function startJob() {
    var body = readInputs();
    if (!body.seed_profile_urls.length && !body.seed_company_urls.length && !body.keywords.length && !body.hashtags.length) {
      setMsg('请至少输入个人主页、公司主页、关键词或话题。', true);
      return;
    }
    saveDraft();
    var btn = $('liStartJobBtn');
    if (btn) { btn.disabled = true; btn.textContent = '启动中...'; }
    apiJson('/api/linkedin-mining/jobs', { method: 'POST', body: body }).then(function(data) {
      state.activeJobId = data.job && data.job.job_id || '';
      setMsg('任务已启动，正在后台执行。', false);
      switchTab('runs');
      return loadJobs(true);
    }).finally(function() {
      if (btn) { btn.disabled = false; btn.textContent = '开始一键挖掘'; }
    }).catch(function(err) {
      setMsg(err.message || '任务启动失败', true);
    });
  }

  function runNext(stepKey) {
    var job = activeJob();
    if (!job) { setMsg('请先选择任务。', true); return; }
    setMsg('正在执行...', false);
    apiJson('/api/linkedin-mining/jobs/' + encodeURIComponent(job.job_id) + '/run-next', {
      method: 'POST',
      body: { step_key: stepKey || '' }
    }).then(function(data) {
      replaceJob(data.job);
      renderJobs();
      setMsg('', false);
    }).catch(function(err) {
      setMsg(err.message || '执行失败，可稍后继续执行。', true);
      loadJobs(true);
    });
  }

  function resumeJob() {
    var job = activeJob();
    if (!job) { setMsg('请先选择任务。', true); return; }
    setMsg('已发起续跑，页面会自动刷新进度。', false);
    apiJson('/api/linkedin-mining/jobs/' + encodeURIComponent(job.job_id) + '/resume', { method: 'POST', body: {} })
      .then(function(data) {
        replaceJob(data.job);
        renderJobs();
      })
      .catch(function(err) { setMsg(err.message || '续跑失败', true); });
  }

  function replaceJob(job) {
    if (!job) return;
    var found = false;
    state.jobs = state.jobs.map(function(item) {
      if (item.job_id === job.job_id) { found = true; return job; }
      return item;
    });
    if (!found) state.jobs.unshift(job);
    state.activeJobId = job.job_id;
  }

  function bind() {
    document.querySelectorAll('#content-linkedin-mining [data-li-tab]').forEach(function(btn) {
      btn.addEventListener('click', function() { switchTab(btn.getAttribute('data-li-tab') || 'new'); });
    });
    var back = $('linkedinMiningBackBtn');
    if (back) back.addEventListener('click', function() {
      if (typeof window.showAppView === 'function') window.showAppView('skill-store');
      else if (typeof window.showLobsterView === 'function') window.showLobsterView('skill-store');
    });
    var refresh = $('linkedinMiningRefreshBtn');
    if (refresh) refresh.addEventListener('click', function() { loadJobs(false); });
    var start = $('liStartJobBtn');
    if (start) start.addEventListener('click', startJob);
    var save = $('liSaveDraftBtn');
    if (save) save.addEventListener('click', saveDraft);
    var load = $('liLoadDraftBtn');
    if (load) load.addEventListener('click', function() { loadDraft(); setMsg('已读取上次输入。', false); });
    var resume = $('liResumeJobBtn');
    if (resume) resume.addEventListener('click', resumeJob);
    var next = $('liRunNextBtn');
    if (next) next.addEventListener('click', function() { runNext(''); });
    var exportLeads = $('liExportLeadsBtn');
    if (exportLeads) exportLeads.addEventListener('click', function() {
      var md = leadsMarkdown();
      if (!md) { setMsg('暂无名单可复制。', true); return; }
      copyText(md, '线索名单已复制。');
    });
    var copyLead = $('liCopyLeadBtn');
    if (copyLead) copyLead.addEventListener('click', function() {
      var job = activeJob();
      var lead = buildLeads(job).filter(function(x) { return x.key === state.activeLeadKey; })[0];
      if (!lead) { setMsg('请先选择线索。', true); return; }
      copyText(leadFollowCardText(lead, job), '跟进卡片已复制。');
    });
    var copy = $('liCopyReportBtn');
    if (copy) copy.addEventListener('click', function() {
      var md = reportMarkdown();
      if (!md) { setMsg('暂无报告可复制。', true); return; }
      copyText(md, '报告已复制。');
    });
    var dl = $('liDownloadReportBtn');
    if (dl) dl.addEventListener('click', function() {
      var md = reportMarkdown();
      if (!md) { setMsg('暂无报告可下载。', true); return; }
      var blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'linkedin-mining-report.md';
      document.body.appendChild(a);
      a.click();
      setTimeout(function() { URL.revokeObjectURL(url); a.remove(); }, 0);
    });
  }

  function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(function() {
      var job = activeJob();
      if (!job || ['completed', 'failed', 'canceled', 'stale'].indexOf(job.status) >= 0) return;
      loadJobs(true);
    }, 3500);
  }

  window.initLinkedinMiningView = function() {
    var root = document.getElementById('content-linkedin-mining');
    if (!root || root.dataset.bound === '1') {
      loadJobs(true);
      return;
    }
    root.dataset.bound = '1';
    bind();
    loadDocs();
    loadJobs(true);
    startPolling();
  };
})();
