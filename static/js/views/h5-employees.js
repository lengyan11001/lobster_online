(function() {
  'use strict';

  var state = {
    templates: [],
    devices: [],
    selectedId: '',
    selectedTemplate: null,
    editingId: '',
    editingMeta: {},
    nodes: [],
    active: null,
    loading: false,
    submitting: '',
    nodeEditIndex: -1,
    childParentIndex: -1,
    childEditId: '',
    momentContactPage: 1,
    momentContactSearch: '',
    momentContactSelected: {},
    childMomentContactSelected: {}
  };

  var SALES_ROWS = [
    {time:'06:00',end:'06:30',key:'local_bestseller',label:'创作同城爆款视频',note:'创作一条同城爆款视频（用于发公域平台）',publish:[['08:45','douyin','同城爆款视频发布抖音'],['09:00','wechat_channels','同城爆款视频发布视频号']]},
    {time:'06:30',end:'07:00',key:'hifly.video.create_by_tts',label:'创作数字人口播视频',note:'创作一条数字人口播视频（用于发朋友圈）',publish:[['09:30','wechat_moments','微信朋友圈发布']]},
    {time:'07:15',end:'07:30',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'07:45',end:'08:15',key:'douyin_leads',label:'抖音自动养号',note:'抖音自动养号'},
    {time:'08:15',end:'08:45',key:'wechat_channels_nurture',label:'视频号自动养号（敬请期待）',note:'视频号自动养号',soon:true},
    {time:'09:15',end:'09:30',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管'},
    {time:'09:45',end:'10:00',key:'native_wechat_moments_engage',label:'微信朋友圈点赞评论',note:'微信朋友圈点赞评论'},
    {time:'10:00',end:'10:15',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'10:30',end:'11:00',key:'douyin_leads',label:'抖音自动养号',note:'抖音自动养号'},
    {time:'11:00',end:'11:30',key:'wechat_channels_nurture',label:'视频号自动养号（敬请期待）',note:'视频号自动养号',soon:true},
    {time:'11:30',end:'12:45',key:'douyin_leads',label:'抖音获客·关键词抓取精准客户',note:'抖音获客·关键词抓取精准客户',params:{followup_actions:['reply_comments','mention_comment','follow_comment','direct_message'],customer_scope:'current_collection_batch'}},
    {time:'13:00',end:'13:15',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'13:30',end:'13:45',key:'native_wechat_moments_engage',label:'微信朋友圈自己评论区接管',note:'微信朋友圈自己评论区接管',params:{moment_action:'comment'}},
    {time:'13:45',end:'14:15',key:'hifly.video.create_by_tts',label:'创作数字人口播视频',note:'创作一条数字人口播视频（用于发朋友圈）',publish:[['14:15','wechat_moments','微信朋友圈发布']]},
    {time:'14:45',end:'15:00',key:'douyin_leads',label:'抖音私信接管',note:'抖音私信接管'},
    {time:'15:00',end:'15:15',key:'wechat_channels_comment',label:'视频号评论区接管（敬请期待）',note:'视频号评论区接管',soon:true},
    {time:'15:15',end:'15:30',key:'wechat_channels_message',label:'视频号私信接管（敬请期待）',note:'视频号私信接管',soon:true},
    {time:'15:30',end:'16:00',key:'douyin_leads',label:'抖音自动养号',note:'抖音自动养号'},
    {time:'16:00',end:'16:30',key:'wechat_channels_nurture',label:'视频号自动养号（敬请期待）',note:'视频号自动养号',soon:true},
    {time:'16:30',end:'16:45',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'17:00',end:'17:15',key:'native_wechat_moments_engage',label:'微信朋友圈点赞评论',note:'微信朋友圈点赞评论'},
    {time:'17:15',end:'18:15',key:'douyin_leads',label:'抖音获客·关键词抓取精准客户',note:'抖音获客·关键词抓取精准客户',params:{followup_actions:['reply_comments','mention_comment','follow_comment','direct_message'],customer_scope:'current_collection_batch'}},
    {time:'18:30',end:'18:45',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'19:15',end:'19:30',key:'douyin_leads',label:'抖音私信接管',note:'抖音私信接管'},
    {time:'19:30',end:'20:00',key:'hifly.video.create_by_tts',label:'创作数字人口播视频',note:'创作一条数字人口播视频（用于发朋友圈）',publish:[['20:00','wechat_moments','微信朋友圈发布']]},
    {time:'20:15',end:'20:30',key:'wechat_channels_comment',label:'视频号评论区接管（敬请期待）',note:'视频号评论区接管',soon:true},
    {time:'20:30',end:'20:45',key:'wechat_channels_message',label:'视频号私信接管（敬请期待）',note:'视频号私信接管',soon:true},
    {time:'21:00',end:'22:00',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'22:15',end:'22:30',key:'native_wechat_moments_engage',label:'朋友圈点赞评论（微信）',note:'朋友圈点赞评论（微信）'},
    {time:'22:30',end:'23:00',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管'},
    {time:'23:00',end:'23:30',key:'douyin_leads',label:'抖音自动养号',note:'抖音自动养号'},
    {time:'23:30',end:'24:00',key:'wechat_channels_nurture',label:'视频号自动养号（敬请期待）',note:'视频号自动养号',soon:true}
  ];

  // Keep the editor options aligned with H5: sales presets are distinct
  // actions even when they share the same ability key.
  var NODE_OPTIONS = [];
  var nodeOptionKeys = {};
  function addNodeOption(key, label, note, group) {
    var identity = String(key || '') + '@@' + String(label || '');
    if (!key || !label || nodeOptionKeys[identity]) return;
    nodeOptionKeys[identity] = true;
    NODE_OPTIONS.push([String(key), String(label), String(note || label), String(group || '销售员工'), identity]);
  }
  SALES_ROWS.forEach(function(row) {
    if (!row.soon) addNodeOption(row.key, row.label, row.note, '销售员工');
  });
  [
    ['hifly.video.create_by_tts','数字人口播视频','选择数字人和声音，生成口播视频。','AI营销创作'],
    ['comfly.seedance.tvc.pipeline','创意分镜头视频','按连续分镜规划视频，并生成完整成片。','AI营销创作'],
    ['local_bestseller','同城爆款视频','围绕同城热点和门店场景生成爆款视频方案。','AI营销创作'],
    ['comfly.daihuo.pipeline','爆款TVC','使用素材或产品图生成广告短片。','AI营销创作'],
    ['viral_video_remix','爆款复刻','基于爆款结构复刻视频脚本和执行方案。','AI营销创作'],
    ['image_composer_studio','AI设计图','根据文案或产品资料生成海报、详情页和朋友圈配图。','AI营销创作'],
    ['ip_content_daily','IP日更文案','生成短视频口播、朋友圈文案和配图提示词。','AI营销创作'],
    ['wewrite.article.pipeline','公众号文章','根据主题生成公众号文章、配图和发布草稿。','AI营销创作'],
    ['douyin_leads','抖音获客','采集客户线索、评论互动、私信触达和同行监控。','AI获客'],
    ['native_wechat_poll','个微私信接管','读取个人微信新消息，并按个人记忆自动生成回复。','私域销管'],
    ['native_wechat_add_friend','个微自动加好友','把目标手机号或微信号加入本机个人微信加好友队列。','私域销管'],
    ['native_wechat_moments_engage','朋友圈点赞评论','对指定联系人24小时内朋友圈进行点赞或评论。','私域销管'],
    ['linkedin_leads','LinkedIn线索挖掘','采集LinkedIn相关线索和账号资料。','AI海外平台'],
    ['reddit_leads','Reddit线索采集','采集社区帖子、评论并分析精准用户。','AI海外平台'],
    ['x_leads','X线索采集','采集账号内容、评论和潜在线索。','AI海外平台'],
    ['tiktok_leads','TikTok线索采集','采集账号作品、视频评论和潜在线索。','AI海外平台']
  ].forEach(function(item) { addNodeOption(item[0], item[1], item[2], item[3]); });

  var CHILD_ACTION_OPTIONS = [
    ['publish','发布内容'],
    ['native_wechat_moments_engage','朋友圈点赞评论']
  ];

  function el(id) { return document.getElementById(id); }
  function esc(value) { return String(value == null ? '' : value).replace(/[&<>"']/g, function(ch) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch]; }); }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function boolParam(value, fallback) {
    if (typeof value === 'boolean') return value;
    if (value == null || value === '') return !!fallback;
    if (typeof value === 'number') return value !== 0;
    var text=String(value).trim().toLowerCase();
    if (['1','true','yes','y','on','enabled'].indexOf(text) >= 0) return true;
    if (['0','false','no','n','off','disabled'].indexOf(text) >= 0) return false;
    return !!fallback;
  }
  function baseUrl() { return String((typeof API_BASE !== 'undefined' && API_BASE) || window.__API_BASE || '').replace(/\/$/, ''); }
  function localBaseUrl() { return String((typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) || window.__LOCAL_API_BASE || '').replace(/\/$/, ''); }
  function headers() { return Object.assign({}, typeof authHeaders === 'function' ? authHeaders() : {}, {'Content-Type':'application/json'}); }
  function api(path, options) {
    options = options || {};
    return fetch(baseUrl() + path, {method:options.method || 'GET', headers:Object.assign({}, headers(), options.headers || {}), body:options.json === undefined ? undefined : JSON.stringify(options.json)})
      .then(function(response) { return response.json().catch(function() { return {}; }).then(function(data) { if (!response.ok) throw new Error(data.detail || data.message || ('请求失败（' + response.status + '）')); return data; }); });
  }
  function showError(message) { var box = el('oeError'); if (!box) return; box.textContent = message || ''; box.hidden = !message; }
  function clearError() { showError(''); }
  function activeTemplateKey(template) { return String(template && template.meta && (template.meta.system_template_key || template.meta.systemTemplateKey) || '').trim(); }
  function isSalesTemplate(template) { return String(state.selectedId) === 'system_sales' || activeTemplateKey(template) === 'system_sales'; }
  function templateNeedsPlanDay(template) { return isSalesTemplate(template) || (state.nodes.length ? state.nodes : (template && template.nodes || [])).some(function(node) { var payload=node && node.plan && node.plan.payload || {}; return String(node && node.ability_key || '') === 'local_bestseller' || String(payload.action || '') === 'local_bestseller_daily_video'; }); }
  function selectedDeviceId() {
    return String(typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : '').trim();
  }
  function normalizedMomentContact(item) {
    if (!item || typeof item !== 'object') return null;
    var wxNo=String(item.wx_no || item.wxNo || item.wechat_id || item.wechatId || item.contact_key || '').trim();
    if (!wxNo) return null;
    var name=String(item.display_name || item.name || item.remark || item.nickname || wxNo).trim() || wxNo;
    var remark=String(item.remark || '').trim();
    return {wx_no:wxNo,name:name,remark:remark};
  }
  function momentContacts() {
    var device=state.devices.find(function(item){return String(item.installation_id || '') === selectedDeviceId();});
    var rows=device && Array.isArray(device.wechat_contacts) ? device.wechat_contacts : [];
    var seen={};
    return rows.map(normalizedMomentContact).filter(function(item){if(!item || seen[item.wx_no]) return false; seen[item.wx_no]=true; return true;});
  }
  function momentSelection(scope) { return scope === 'child' ? state.childMomentContactSelected : state.momentContactSelected; }
  function momentSelectionValues(scope) { return Object.keys(momentSelection(scope) || {}).filter(Boolean); }
  function setMomentSelection(scope, values) {
    var next={}; (Array.isArray(values) ? values : []).map(function(value){return String(value || '').trim();}).filter(Boolean).forEach(function(value){next[value]=true;});
    if (scope === 'child') state.childMomentContactSelected=next; else state.momentContactSelected=next;
  }
  function momentPickerIds(scope) {
    return scope === 'child'
      ? {field:'oeChildMomentField',trigger:'oeChildMomentTrigger',search:'oeChildMomentSearch',list:'oeChildMomentContacts',prev:'oeChildMomentPrev',next:'oeChildMomentNext',page:'oeChildMomentPage',summary:'oeChildMomentSummary',action:'oeChildMomentAction'}
      : {field:'oeNodeMomentField',trigger:'oeNodeMomentTrigger',search:'oeNodeMomentSearch',list:'oeNodeMomentContacts',prev:'oeNodeMomentPrev',next:'oeNodeMomentNext',page:'oeNodeMomentPage',summary:'oeNodeMomentSummary',action:'oeNodeMomentAction'};
  }
  function renderMomentPicker(scope) {
    var ids=momentPickerIds(scope), list=el(ids.list); if (!list) return;
    var query=String(state.momentContactSearch || '').trim().toLowerCase(), rows=momentContacts().filter(function(item){return !query || [item.name,item.remark,item.wx_no].join(' ').toLowerCase().indexOf(query) >= 0;});
    var pageSize=20, totalPages=Math.max(1,Math.ceil(rows.length / pageSize)); state.momentContactPage=Math.min(Math.max(1,state.momentContactPage || 1),totalPages);
    var start=(state.momentContactPage - 1) * pageSize, selected=momentSelection(scope), pageRows=rows.slice(start,start + pageSize);
    list.innerHTML=pageRows.length ? pageRows.map(function(item){return '<label class="oe-moment-contact"><input type="checkbox" data-oe-moment-contact="' + esc(item.wx_no) + '" data-oe-moment-scope="' + scope + '"' + (selected[item.wx_no] ? ' checked' : '') + '><span><strong>' + esc(item.name) + '</strong><small>' + esc(item.wx_no) + (item.remark && item.remark !== item.name ? ' · ' + esc(item.remark) : '') + '</small></span></label>';}).join('') : '<div class="oe-moment-empty">当前设备没有可用微信号通讯录，或没有匹配联系人</div>';
    if (el(ids.page)) el(ids.page).textContent=state.momentContactPage + ' / ' + totalPages;
    if (el(ids.prev)) el(ids.prev).disabled=state.momentContactPage <= 1;
    if (el(ids.next)) el(ids.next).disabled=state.momentContactPage >= totalPages;
    var selectedCount=momentSelectionValues(scope).length;
    if (el(ids.trigger)) el(ids.trigger).textContent=selectedCount ? '已选择 ' + selectedCount + ' 位联系人' : '选择通讯录联系人';
    if (el(ids.summary)) el(ids.summary).textContent='已选择 ' + selectedCount + ' 人，任务将按微信号定位';
  }
  function initMomentPicker(scope, values) { state.momentContactPage=1; state.momentContactSearch=''; setMomentSelection(scope, values); var ids=momentPickerIds(scope); if (el(ids.search)) el(ids.search).value=''; renderMomentPicker(scope); }
  function syncMomentPicker(scope, visible) { var ids=momentPickerIds(scope); if (el(ids.field)) el(ids.field).hidden=!visible; if (el(ids.action)) { var actionField=el(ids.action).closest('.oe-form-label'); if (actionField) actionField.hidden=!visible; } if (visible) renderMomentPicker(scope); }
  function scheduleDuration(start, end) {
    var parse = function(value) { var m = /^(\d{2}):(\d{2})$/.exec(String(value || '')); return m ? Number(m[1]) * 60 + Number(m[2]) : 0; };
    var a = parse(start), b = parse(end); if (end === '24:00') b = 1440; if (b && b < a) b += 1440; return Math.max(0, b - a);
  }
  function normalizeWorkflowTimeline(nodes) {
    var items = clone(Array.isArray(nodes) ? nodes : []);
    items.sort(function(a, b) { return String(a && a.time || '').localeCompare(String(b && b.time || '')); });
    items.forEach(function(node, index) {
      if (!node || typeof node !== 'object') return;
      var next = items[index + 1];
      var end = String(node.end_time || '').trim();
      if (!end && next && /^\d{2}:\d{2}$/.test(String(next.time || ''))) end = String(next.time || '').trim();
      node.end_time = end;
      node.time_range = end ? String(node.time || '') + '-' + end : String(node.time || '');
      var children = workflowChildren(node).slice().sort(function(a,b){return String(a && a.time || '').localeCompare(String(b && b.time || ''));});
      children.forEach(function(child, childIndex){
        var nextChild = children[childIndex + 1], childEnd = String(child && child.end_time || '').trim();
        if (!childEnd) childEnd = String(nextChild && nextChild.time || end || '').trim();
        child.end_time = childEnd;
        child.time_range = childEnd ? String(child.time || '') + '-' + childEnd : String(child.time || '');
      });
      if (children.length) node.children = children;
    });
    return items;
  }
  function salesAction(note) {
    var text = String(note || '');
    if (text.indexOf('养号') >= 0) return 'account_nurture';
    if (text.indexOf('关键词抓取') >= 0) return 'search_collect';
    if (text.indexOf('回复') >= 0 && text.indexOf('评论') >= 0) return 'reply_comments';
    if (text.indexOf('@精准') >= 0 || text.indexOf('评论并@') >= 0 || text.indexOf('自己评论区接管') >= 0) return 'mention_comment';
    if (text.indexOf('关注') >= 0 && text.indexOf('评论') >= 0) return 'follow_comment';
    if (text.indexOf('主动私信') >= 0 || text.indexOf('私信10') >= 0) return 'direct_message';
    if (text.indexOf('私信接管') >= 0 || text.indexOf('私信引流') >= 0) return 'stranger_message';
    return 'search_collect';
  }
  var DOUYIN_FOLLOWUP_ACTIONS=['reply_comments','mention_comment','follow_comment','direct_message'];
  function normalizeDouyinFollowupActions(value) {
    var selected={}; (Array.isArray(value) ? value : []).forEach(function(item){selected[String(item || '').trim().toLowerCase()]=true;});
    return DOUYIN_FOLLOWUP_ACTIONS.filter(function(action){return !!selected[action];});
  }
  function isDouyinCollection(node) {
    if (!node || String(node.ability_key || node.key || '') !== 'douyin_leads') return false;
    var payload=workflowPayload(node), params=payload.params && typeof payload.params === 'object' ? payload.params : {};
    return salesAction(node.note || node.ability_label || '') === 'search_collect' && String(params.sales_action || payload.action || 'search_collect') === 'search_collect';
  }
  function baseScheduleParams(row, params) {
    return Object.assign({}, params || {}, {sales_schedule_start:row.time, sales_schedule_end:row.end, sales_schedule_duration_minutes:scheduleDuration(row.time, row.end), sales_node_label:row.label || row.note || ''});
  }
  function nativePlan(key, row, extra) {
    var params = Object.assign({account_id:'pc-wechat-default', note:row.note || row.label || '', prompt:row.note || row.label || ''}, row.params || {}, extra || {});
    if (key === 'native_wechat_moments_engage') params.moment_action = params.moment_action || 'like_comment';
    if (key === 'native_wechat_add_friend') {
      params.targets = Array.isArray(params.targets) ? params.targets.filter(Boolean) : [];
      if (!String(params.source_mode || '').trim()) {
        delete params.source_mode;
        delete params.trigger;
        delete params.skip_without_clear_wechat_id;
      }
    }
    if (key === 'native_wechat_moments_engage') {
      var momentTargets=Array.isArray(params.contact_wx_nos) ? params.contact_wx_nos : params.targets;
      params.contact_wx_nos = Array.isArray(momentTargets) ? momentTargets.map(function(value){return String(value || '').trim();}).filter(Boolean) : [];
      params.targets = params.contact_wx_nos.slice();
      params.max_scrolls = Number(params.max_scrolls || 6);
      ['group_invite_enabled','group_invite_memory_doc_id','group_invite_keywords','group_invite_contacts','group_invite_primary_contact','group_invite_primary_contact_name','group_invite_welcome_message','group_invite_rule_status','group_invite_targets_source','group_invite_members','group_invite_manager_contacts','followup_action','group_invite_rules','trigger'].forEach(function(name){ delete params[name]; });
    }
    if (key === 'native_wechat_poll') {
      var duration=scheduleDuration(row.time, row.end);
      if (duration > 0) params.takeover_session_minutes = duration;
      else delete params.takeover_session_minutes;
      params.message_poll_interval_seconds = Number(params.message_poll_interval_seconds || 15);
      params.private_sessions_per_round = Math.max(
        1,
        Math.min(
          Number(params.private_sessions_per_round || params.max_private_sessions_per_round || 10),
          100
        )
      );
      params.accept_friend_requests_once = params.accept_friend_requests_once !== false;
    }
    if (params.followup_action === 'group_invite' || String(row.label || '').indexOf('拉群') >= 0) {
      delete params.followup_action;
      Object.assign(params, {group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'});
    }
    var title = key === 'native_wechat_add_friend' ? '个微自动加好友' : key === 'native_wechat_moments_engage' ? '朋友圈点赞评论' : '个微私信接管';
    return {title:title, task_kind:'client_workflow', content:'H5 工作流：' + title, payload:{action:key, params:baseScheduleParams(row, params)}};
  }
  function publishChild(parent, item, index) {
    var platform = item[1];
    var label = item[2];
    return {id:parent.id + '_action_' + (index + 1), time:item[0], parent_node_id:parent.id, action_type:'publish', type:'publish', platform:platform, ability_key:'publish_content', ability_label:label, department_id:'sales', department_name:'销售部', note:label + '，配文案、带标签发布', is_action_node:true, param_configured:true, plan:{title:label, task_kind:'client_workflow', content:'H5 工作流动作：' + label, payload:{action:'publish_content',params:{source_mode:'parent_latest_run',source_workflow_node_id:parent.id,source_workflow_node_label:parent.ability_label,platform:platform,media_type:platform === 'wechat_moments' ? 'image_text' : 'video',ai_publish_copy:true,note:label}}}};
  }
  function planForRow(row) {
    var prompt = row.note || row.label || '';
    if (row.key.indexOf('wechat_channels_') === 0 || row.soon) return {title:row.label,task_kind:'workflow_placeholder',content:'H5 工作流占位：' + row.label,payload:{action:'workflow_coming_soon',skip_execution:true,note:row.note || row.label,platform:'wechat_channels'}};
    if (row.key.indexOf('native_wechat_') === 0) return nativePlan(row.key, row);
    if (row.key === 'local_bestseller') return {title:'同城爆款视频',task_kind:'client_workflow',content:'H5 工作流：同城爆款视频',payload:{action:'local_bestseller_daily_video',params:baseScheduleParams(row,{note:prompt,prompt:prompt,days:30,day_mode:'workflow_elapsed'})}};
    if (row.key === 'hifly.video.create_by_tts') return {title:'数字人口播视频',task_kind:'capability',content:'H5 工作流：数字人口播视频',payload:{capability_id:'hifly.video.create_by_tts',payload:{script:prompt,prompt:prompt}}};
    if (row.key === 'comfly.seedance.tvc.pipeline') return {title:'创意分镜头视频',task_kind:'capability',content:'H5 工作流：创意分镜头视频',payload:{capability_id:'comfly.seedance.tvc.pipeline',payload:{action:'start_pipeline',task_text:prompt,prompt:prompt,auto_save:true}}};
    if (row.key === 'comfly.daihuo.pipeline') return {title:'爆款TVC',task_kind:'capability',content:'H5 工作流：爆款TVC',payload:{capability_id:'comfly.daihuo.pipeline',payload:{action:'start_pipeline',task_text:prompt,prompt:prompt,auto_save:true}}};
    if (row.key === 'viral_video_remix') return {title:'爆款复制',task_kind:'client_workflow',content:'H5 工作流：爆款复制',payload:{action:'viral_video_remix_start',params:baseScheduleParams(row,{prompt:prompt,billing_confirmed:true,ratio:'9:16'})}};
    if (row.key === 'image_composer_studio') return {title:'AI设计图',task_kind:'client_workflow',content:'H5 工作流：AI设计图',payload:{action:'image_studio_generate',params:baseScheduleParams(row,{prompt:prompt,note:prompt})}};
    if (row.key === 'ip_content_daily') return {title:'IP日更文案',task_kind:'ip_content_daily',content:'H5 工作流：IP日更文案',payload:{template_id:0,use_personal_default:true,tasks:['industry_hot_oral','professional_ip_oral','moments_candidate'],sync_before:true,industry_count:5,ip_count:5,moments_count:20,requirements:{}}};
    if (row.key === 'wewrite.article.pipeline') return {title:'公众号文章',task_kind:'capability',content:'H5 工作流：公众号文章',payload:{capability_id:'wewrite.article.pipeline',payload:{idea:prompt,style:'',include_images:true,image_count:3,image_aspect_ratio:'16:9'}}};
    if (row.key === 'linkedin_leads') return {title:'LinkedIn线索挖掘',task_kind:'linkedin_mining',content:'H5 工作流：LinkedIn线索挖掘',payload:{title:'LinkedIn线索挖掘',keywords:[prompt],max_people:30,auto_run:true}};
    if (row.key === 'reddit_leads' || row.key === 'x_leads' || row.key === 'tiktok_leads') {
      var platform = row.key === 'reddit_leads' ? 'reddit' : row.key === 'x_leads' ? 'x' : 'tiktok';
      var socialPayload = {platform:platform,title:platform.toUpperCase() + '线索采集',keywords:[prompt],max_items:100,include_comments:true,include_account_posts:true,auto_run:true};
      if (platform === 'reddit') socialPayload.communities = [prompt]; else socialPayload.source_keywords = [prompt];
      return {title:socialPayload.title,task_kind:'social_leads',content:'H5 工作流：' + socialPayload.title,payload:socialPayload};
    }
    if (row.key === 'douyin_leads') {
      var action = salesAction(prompt), max = action === 'search_collect' || action === 'account_nurture' ? 50 : 10;
      // Keep explicitly edited private-message options when rebuilding the
      // plan.  The editor stores the checkbox in row.params, but the old
      // branch rebuilt params from scratch and silently dropped it.
      var douyinParams = Object.assign({}, row.params || {});
      douyinParams.sales_action=action;
      if (douyinParams.max_results == null) douyinParams.max_results=max;
      if (douyinParams.max_users == null) douyinParams.max_users=max;
      if (!Array.isArray(douyinParams.regions) || !douyinParams.regions.length) douyinParams.regions=['全国'];
      if (!douyinParams.mode) douyinParams.mode='script';
      if (action === 'search_collect') {
        douyinParams.followup_actions=normalizeDouyinFollowupActions(douyinParams.followup_actions || []);
        douyinParams.customer_scope='current_collection_batch';
      }
      if (action !== 'stranger_message') {
        delete douyinParams.wechat_add_friend_enabled;
        delete douyinParams.wechat_add_friend_targets_source;
      }
      return {title:'抖音获客 - ' + prompt.slice(0,24),task_kind:'douyin_leads',content:'H5 工作流：抖音获客',payload:{action:action,params:baseScheduleParams(row,douyinParams)}};
    }
    throw new Error('该节点暂不支持加入工作流：' + row.key);
  }
  function makeNode(row, index) {
    var node = {id:'sales_' + row.time.replace(':','') + '_' + index,time:row.time,end_time:row.end,time_range:row.time + '-' + row.end,ability_key:row.key,ability_label:row.label,note:row.note,department_id:'sales',department_name:'销售部',sales_preset:true,comingSoon:!!row.soon,workflow_placeholder:!!row.soon,param_configured:false,plan:planForRow(row)};
    if (row.publish && !row.soon) node.children = row.publish.map(function(item, childIndex) { return publishChild(node, item, childIndex); });
    return node;
  }
  function workflowChildren(node) {
    if (!node || typeof node !== 'object') return [];
    if (Array.isArray(node.children)) return node.children;
    if (Array.isArray(node.actions)) return node.actions;
    return [];
  }
  function workflowPayload(node) {
    return node && node.plan && typeof node.plan === 'object' && node.plan.payload && typeof node.plan.payload === 'object' ? node.plan.payload : {};
  }
  function childActionType(child) {
    var explicit=String(child && (child.action_type || child.type) || '').trim().toLowerCase();
    if (explicit && explicit !== 'client_workflow') return explicit;
    var payload=workflowPayload(child), params=payload.params && typeof payload.params === 'object' ? payload.params : {};
    var action=String(payload.action || child && child.ability_key || '').trim().toLowerCase();
    if (action === 'native_wechat_poll' && params.followup_action === 'group_invite') return 'native_wechat_group_invite';
    if (action === 'native_wechat_add_friend' || action === 'native_wechat_moments_engage') return action;
    if (action === 'publish_content' || child && child.platform) return 'publish';
    return explicit || 'publish';
  }
  function isDouyinPrivate(node) {
    if (!node || String(node.ability_key || node.key || '') !== 'douyin_leads') return false;
    var payload=workflowPayload(node), params=payload.params && typeof payload.params === 'object' ? payload.params : {};
    var action=String(payload.action || params.sales_action || '').trim().toLowerCase();
    if (action === 'stranger_message') return true;
    var text=[node.ability_label,node.note,node.plan && node.plan.title].map(function(value){return String(value || '');}).join(' ');
    return text.indexOf('私信接管') >= 0 || text.indexOf('私信引流') >= 0 || text.toLowerCase().indexOf('private takeover') >= 0;
  }
  function isWechatPrivate(node) {
    if (!node) return false;
    var payload=workflowPayload(node), params=payload.params && typeof payload.params === 'object' ? payload.params : {};
    var action=String(payload.action || node.ability_key || '').trim().toLowerCase();
    var text=[node.ability_label,node.note,node.plan && node.plan.title].map(function(value){return String(value || '');}).join(' ');
    var takeover=action === 'native_wechat_poll' || text.indexOf('微信私信接管') >= 0 || text.indexOf('个微私信接管') >= 0 || text.indexOf('个人微信接管') >= 0;
    return takeover && params.followup_action !== 'group_invite' && text.indexOf('自动拉群') < 0;
  }
  function childOptions(parent, currentType) {
    var values=[];
    if (isWechatPrivate(parent)) values.push('native_wechat_moments_engage');
    values.push('publish');
    if (currentType && currentType !== 'native_wechat_group_invite' && values.indexOf(currentType) < 0) values.push(currentType);
    return values.map(function(value){return CHILD_ACTION_OPTIONS.find(function(item){return item[0] === value;});}).filter(Boolean);
  }
  function childPlatformLabel(platform) {
    return ({douyin:'抖音',toutiao:'头条',wechat_channels:'视频号',wechat_moments:'朋友圈图文'})[String(platform || '').trim()] || '平台';
  }
  function childActionLabel(child) {
    var type=childActionType(child);
    if (type === 'publish') return '发布' + childPlatformLabel(child && child.platform);
    if (type === 'native_wechat_add_friend') return '微信自动加好友';
    if (type === 'native_wechat_group_invite') return '微信自动拉群';
    if (type === 'native_wechat_moments_engage') return '朋友圈点赞评论';
    return child && (child.ability_label || child.note) || '下级动作';
  }
  function buildWorkflowChild(parent, form, existing) {
    var type=String(form.action_type || childActionType(existing) || 'publish').trim();
    var time=String(form.time || existing && existing.time || parent.time || '09:00').trim();
    var end=String(form.end_time || existing && existing.end_time || '').trim();
    var platform=String(form.platform || existing && existing.platform || 'douyin').trim();
    var id=String(existing && existing.id || ('wf_action_' + Date.now().toString(36) + '_' + Math.random().toString(16).slice(2,8)));
    if (type === 'publish') {
      var publish=publishChild(parent,[time,platform,'发布' + childPlatformLabel(platform)],0);
      publish.id=id;
      publish.time=time;
      publish.end_time=end;
      publish.time_range=end ? time + '-' + end : time;
      return publish;
    }
    var keys={native_wechat_add_friend:'native_wechat_add_friend',native_wechat_moments_engage:'native_wechat_moments_engage'};
    var key=keys[type];
    if (!key) throw new Error('不支持的下级动作');
    var label=childActionLabel({action_type:type});
    var extra={source_workflow_node_id:String(parent.id || ''),source_workflow_node_label:String(parent.ability_label || parent.note || '')};
    if (type === 'native_wechat_add_friend') Object.assign(extra,{source_mode:'douyin_private_message_phone',trigger:'clear_mobile',skip_without_clear_mobile:true,targets:[]});
    if (type === 'native_wechat_moments_engage') { var wxNos=Array.isArray(form.contact_wx_nos) ? form.contact_wx_nos.slice() : []; Object.assign(extra,{contact_wx_nos:wxNos,targets:wxNos.slice(),moment_action:String(form.moment_action || 'like_comment'),max_scrolls:6}); }
    var row={time:time,end:end,key:key,label:label,note:label,params:{}};
    return Object.assign({},existing || {},{id:id,time:time,end_time:end,time_range:end ? time + '-' + end : time,parent_node_id:String(parent.id || ''),action_type:type,type:type,platform:'',ability_key:key,ability_label:label,department_id:parent.department_id || '',department_name:parent.department_name || '',note:label,is_action_node:true,param_configured:true,plan:nativePlan(key,row,extra)});
  }
  function syncParentChildRules(parent) {
    var next=Object.assign({},parent || {}), children=workflowChildren(next).slice();
    var plan=next.plan && typeof next.plan === 'object' ? Object.assign({},next.plan) : {};
    var payload=plan.payload && typeof plan.payload === 'object' ? Object.assign({},plan.payload) : {};
    var params=payload.params && typeof payload.params === 'object' ? Object.assign({},payload.params) : {};
    var groups=children.filter(function(child){return childActionType(child) === 'native_wechat_group_invite';});
    children=children.filter(function(child){return childActionType(child) !== 'native_wechat_group_invite';});
    var friends=children.filter(function(child){return childActionType(child) === 'native_wechat_add_friend';});
    if (groups.length) {
      params.group_invite_enabled=true;
      params.group_invite_targets_source='qualified_intent';
      params.group_invite_rule_status='pending_rules';
    }
    delete params.followup_action;
    delete params.group_invite_rules;
    if (friends.length || Object.prototype.hasOwnProperty.call(params,'wechat_add_friend_rules')) {
      params.wechat_add_friend_enabled=friends.length > 0;
      params.wechat_add_friend_targets_source='douyin_private_message_phone';
      params.wechat_add_friend_rules=friends.map(function(child){return {child_node_id:child.id,time:child.time,trigger:'clear_mobile',skip_without_clear_mobile:true};});
    }
    payload.params=params;
    plan.payload=payload;
    next.plan=plan;
    next.children=children;
    return next;
  }
  function addChild(parent, row, index, type) {
    if (!parent) return false;
    if (type !== 'friend') {
      var inviteParams = parent.plan && parent.plan.payload && parent.plan.payload.params || {};
      parent.plan.payload.params = Object.assign({}, inviteParams, {group_invite_enabled:true,group_invite_rule_status:'pending_rules',group_invite_targets_source:'qualified_intent',group_invite_members:[],group_invite_manager_contacts:[]});
      delete parent.plan.payload.params.followup_action;
      delete parent.plan.payload.params.group_invite_rules;
      return true;
    }
    var childKey = 'native_wechat_add_friend';
    var child = {id:parent.id + '_native_' + row.time.replace(':','') + '_' + index,time:row.time,parent_node_id:parent.id,action_type:'native_wechat_add_friend',type:'native_wechat_add_friend',ability_key:childKey,ability_label:row.label,note:row.note,department_id:'sales',department_name:'销售部',sales_preset:true,is_action_node:true,param_configured:true,plan:nativePlan(childKey,row,{})};
    parent.children = (parent.children || []).concat(child).sort(function(a,b) { return String(a.time).localeCompare(String(b.time)); });
    var parentParams = parent.plan && parent.plan.payload && parent.plan.payload.params || {};
    parent.plan.payload.params = Object.assign({}, parentParams,{wechat_add_friend_enabled:true,wechat_add_friend_targets_source:'douyin_private_message_wechat_id',wechat_add_friend_rules:[{child_node_id:child.id,time:child.time,trigger:'clear_wechat_id',skip_without_clear_wechat_id:true}]});
    return true;
  }
  function salesNodes() {
    var nodes = [], pendingFriends = [];
    SALES_ROWS.forEach(function(row, index) {
      if (row.key === 'native_wechat_add_friend') { var friendParent = nodes.slice().reverse().find(isDouyinPrivate); if (!addChild(friendParent,row,index,'friend')) pendingFriends.push({row:row,index:index}); return; }
      if (row.key === 'native_wechat_poll' && row.label === '微信自动拉群') { addChild(nodes.slice().reverse().find(isWechatPrivate),row,index,'group'); return; }
      var node = makeNode(row,index); nodes.push(node);
      if (isDouyinPrivate(node) && pendingFriends.length) { var pending = pendingFriends.shift(); addChild(node,Object.assign({},pending.row,{time:node.end_time || node.time}),pending.index,'friend'); }
    });
    if (pendingFriends.length) {
      var fallbackParent = nodes.slice().reverse().find(isDouyinPrivate);
      pendingFriends.forEach(function(item) { addChild(fallbackParent,item.row,item.index,'friend'); });
    }
    return nodes;
  }
  function migrateGroupInviteNodes(nodes) {
    var result=[];
    (Array.isArray(nodes) ? nodes : []).forEach(function(raw){
      var node=clone(raw), children=workflowChildren(node).slice();
      var legacyGroup=children.find(function(child){return childActionType(child) === 'native_wechat_group_invite';});
      if (legacyGroup) {
        var parentParams=workflowPayload(node).params || {}, groupParams=workflowPayload(legacyGroup).params || {};
        node.plan=node.plan && typeof node.plan === 'object' ? node.plan : {};
        node.plan.payload=node.plan.payload && typeof node.plan.payload === 'object' ? node.plan.payload : {};
        node.plan.payload.params=Object.assign({},parentParams,groupParams,{group_invite_enabled:true});
        delete node.plan.payload.params.followup_action;
        delete node.plan.payload.params.group_invite_rules;
        node=syncParentChildRules(Object.assign({},node,{children:children}));
      }
      if (childActionType(node) === 'native_wechat_group_invite') {
        var source=workflowPayload(node).params || {}, parent=result.slice().reverse().find(isWechatPrivate);
        if (parent) {
          var params=workflowPayload(parent).params || {};
          parent.plan.payload.params=Object.assign({},params,source,{group_invite_enabled:true});
          delete parent.plan.payload.params.followup_action;
          delete parent.plan.payload.params.group_invite_rules;
          return;
        }
        var row={time:node.time || '09:00',end:node.end_time || '',label:'微信私信接管',note:'微信私信接管',params:Object.assign({},source,{group_invite_enabled:true})};
        delete row.params.followup_action;
        delete row.params.group_invite_rules;
        node.ability_key='native_wechat_poll';
        node.ability_label='微信私信接管';
        node.note='微信私信接管';
        delete node.action_type;
        delete node.type;
        node.plan=nativePlan('native_wechat_poll',row);
      }
      result.push(node);
    });
    return result;
  }
  function migrateDouyinAddFriendChildren(nodes) {
    var list = Array.isArray(nodes) ? listClone(nodes) : [];
    var parents = list.filter(isDouyinPrivate), legacyRows = list.filter(function(node) { return childActionType(node) === 'native_wechat_add_friend'; });
    if (!parents.length) return list;
    var prepared = list.filter(function(node) { return childActionType(node) !== 'native_wechat_add_friend'; });
    parents.forEach(function(parent) {
      var payload = workflowPayload(parent), params = payload.params && typeof payload.params === 'object' ? Object.assign({}, payload.params) : {}, children = workflowChildren(parent), hadChild = children.some(function(child) { return childActionType(child) === 'native_wechat_add_friend'; });
      params.wechat_add_friend_enabled = Object.prototype.hasOwnProperty.call(params, 'wechat_add_friend_enabled') ? !!params.wechat_add_friend_enabled : !!(hadChild || legacyRows.length);
      params.wechat_add_friend_targets_source = 'douyin_private_message_phone';
      delete params.wechat_add_friend_rules;
      parent.plan = parent.plan && typeof parent.plan === 'object' ? parent.plan : {};
      parent.plan.payload = Object.assign({}, payload, {params:params});
      var remaining = children.filter(function(child) { return childActionType(child) !== 'native_wechat_add_friend'; });
      if (remaining.length) parent.children = remaining; else delete parent.children;
    });
    return prepared;
  }
  function migrateDouyinFollowupNodes(nodes) {
    var prepared=[], current=null;
    (Array.isArray(nodes) ? nodes : []).forEach(function(node){
      var action=salesAction(node && (node.note || node.ability_label) || ''), payload=workflowPayload(node), params=payload.params && typeof payload.params === 'object' ? payload.params : {};
      if (action === 'search_collect' && String(node && node.ability_key || '') === 'douyin_leads') { current=node; prepared.push(node); return; }
      if (current && DOUYIN_FOLLOWUP_ACTIONS.indexOf(action) >= 0 && String(node && node.ability_key || '') === 'douyin_leads') {
        var currentPayload=workflowPayload(current), currentParams=Object.assign({},currentPayload.params || {});
        currentParams.followup_actions=normalizeDouyinFollowupActions((currentParams.followup_actions || []).concat(action));
        currentParams.customer_scope='current_collection_batch';
        current.plan.payload=Object.assign({},currentPayload,{params:currentParams});
        return;
      }
      prepared.push(node);
    });
    return prepared;
  }
  function listClone(value) { return clone(value); }
  function salesNodes() {
    var nodes = [];
    SALES_ROWS.forEach(function(row, index) {
      if (row.key === 'native_wechat_add_friend') return;
      if (row.key === 'native_wechat_poll' && row.label === '微信自动拉群') { addChild(nodes.slice().reverse().find(isWechatPrivate), row, index, 'group'); return; }
      var node = makeNode(row, index);
      if (isDouyinPrivate(node)) {
        node.plan.payload = node.plan.payload || {};
        node.plan.payload.params = Object.assign({}, node.plan.payload.params || {}, {wechat_add_friend_enabled:true, wechat_add_friend_targets_source:'douyin_private_message_phone'});
      }
      nodes.push(node);
    });
    return nodes;
  }
  function normalizeTemplate(template) { var out = Object.assign({}, template || {}); out.nodes = normalizeWorkflowTimeline(migrateDouyinAddFriendChildren(migrateDouyinFollowupNodes(migrateGroupInviteNodes(Array.isArray(out.nodes) ? clone(out.nodes) : [])))); out.meta = out.meta && typeof out.meta === 'object' ? Object.assign({},out.meta) : {}; return out; }
  function ownSalesMirror() { return state.templates.find(function(item) { return activeTemplateKey(item) === 'system_sales'; }); }
  function templateForSelected() { if (state.selectedId === 'system_sales') return ownSalesMirror() || {id:'system_sales',source:'system',name:'销售员工',meta:{system_template_key:'system_sales'},nodes:salesNodes()}; return state.templates.find(function(item) { return String(item.id) === String(state.selectedId); }) || null; }
  function templateIsEditable(template) { return !!(template && template.source === 'own'); }
  function currentTemplateIsEditable() { return state.selectedId === 'system_sales' || templateIsEditable(state.selectedTemplate); }
  function requireEditableTemplate() { if (!currentTemplateIsEditable()) throw new Error('授权模板为只读配置，不能修改'); }
  function activeForDevice() { var iid = selectedDeviceId(); return state.active && String(state.active.installation_id || '') === iid ? state.active : null; }
  function draftKeyFor(template, selectedId, editingId) {
    if (String(selectedId || '') === 'system_sales' || activeTemplateKey(template) === 'system_sales') return 'system_sales';
    var id=String(editingId || template && template.id || selectedId || '').trim();
    return id ? 'template:' + id : '';
  }
  function currentDraftKey() { return draftKeyFor(state.selectedTemplate,state.selectedId,state.editingId); }
  function editorNameValue() {
    var input=el('oeTemplateName');
    return String(input && input.value != null ? input.value : state.selectedTemplate && state.selectedTemplate.name || '').trim();
  }
  function rememberCurrentDraft() {
    if (!state.selectedTemplate || !currentTemplateIsEditable()) return;
    var name=editorNameValue() || state.selectedTemplate.name || '';
    state.selectedTemplate=Object.assign({},state.selectedTemplate,{name:name,nodes:clone(state.nodes)});
  }
  function deviceOptionLabel(device) {
    var id=String(device && device.installation_id || '').trim();
    var alias=String(device && (device.display_name || device.device_name) || '').trim();
    var shortId=id.slice(0,8);
    if (alias) return alias + (shortId ? ' · ' + shortId : '');
    return shortId ? '设备 ' + shortId : '未命名设备';
  }
  function renderDevices() {
    // The device is selected once in the global Online device context.
    // Employee editing must never switch to another installation slot.
  }
  function renderList() {
    var host = el('oeTemplateList'); if (!host) return;
    var rows = [{id:'system_sales',name:'销售员工',meta:{system_template_key:'system_sales'},source:'system',mark:'销'}].concat(state.templates.filter(function(item) { return activeTemplateKey(item) !== 'system_sales'; }).map(function(item) { return Object.assign({mark:String(item.name || '员').charAt(0)},item); }));
    if (!rows.length) { host.innerHTML = '<div class="oe-empty-list">当前账号没有可访问的员工模板。</div>'; return; }
    host.innerHTML = rows.map(function(item) { var selected=String(item.id)===String(state.selectedId); var meta=item.source === 'granted' ? '他人授权' : item.source === 'system' ? '系统员工' : '我的模板'; return '<button type="button" class="oe-employee-item' + (selected ? ' is-selected' : '') + '" data-oe-template="' + esc(item.id) + '"' + (state.submitting ? ' disabled aria-disabled="true"' : '') + '><span class="oe-employee-mark">' + esc(item.mark || String(item.name || '员').charAt(0)) + '</span><span class="oe-employee-copy"><span class="oe-employee-name">' + esc(item.name || '未命名员工') + '</span><span class="oe-employee-meta">' + meta + '</span></span></button>'; }).join('');
  }
  function renderStatus() {
    var status=el('oeActiveStatus'), active=activeForDevice(); if (!status) return;
    status.textContent = active ? '已启用 · ' + (active.template_name || '当前员工') : '未启用'; status.classList.toggle('is-active',!!active);
  }
  function childHtml(child,parentIndex,editable) { var soon=child.comingSoon || child.workflow_placeholder || (child.plan && child.plan.payload && child.plan.payload.skip_execution); return '<div class="oe-child"><span class="oe-child-time">' + esc(child.time || '--:--') + (child.end_time ? '<small>' + esc(child.end_time) + '</small>' : '') + '</span><span class="oe-child-copy">' + esc(child.ability_label || childActionLabel(child)) + (soon ? '<small>视频号功能敬请期待</small>' : '<small>' + esc(child.note || childActionLabel(child)) + '</small>') + '</span><span class="oe-tag child">下一级</span>' + (editable && !soon ? '<span class="oe-child-actions"><button type="button" class="oe-mini-btn" data-oe-child-edit="' + esc(child.id || '') + '" data-oe-child-parent="' + parentIndex + '">编辑</button><button type="button" class="oe-mini-btn" data-oe-child-delete="' + esc(child.id || '') + '" data-oe-child-parent="' + parentIndex + '">删除</button></span>' : '') + '</div>'; }
  function nodeActionsHtml(node,index,editable,soon) {
    if (soon) return '';
    var html='<button type="button" class="oe-mini-btn" data-oe-demo="' + index + '">演示</button>';
    if (editable) html+='<button type="button" class="oe-mini-btn" data-oe-child-add="' + index + '">添加下级</button><button type="button" class="oe-mini-btn" data-oe-edit="' + index + '">编辑</button><button type="button" class="oe-mini-btn" data-oe-delete="' + index + '">删除</button>';
    return html;
  }
  function nodeHtml(node,index,editable) { var soon=!!(node.comingSoon || node.workflow_placeholder || node.plan && node.plan.payload && node.plan.payload.skip_execution); var children=workflowChildren(node); return '<article class="oe-node' + (soon ? ' is-soon' : '') + '"><div class="oe-time">' + esc(node.time || '--:--') + (node.end_time ? '<br><span style="color:#a0aaba;font-size:.61rem;font-weight:400">' + esc(node.end_time) + '</span>' : '') + '</div><div class="oe-line"></div><div class="oe-node-main"><div class="oe-node-title"><span>' + esc(node.ability_label || node.note || '工作节点') + '</span>' + (soon ? '<span class="oe-tag soon">敬请期待</span>' : '') + (node.sales_preset ? '<span class="oe-tag">销售</span>' : '') + '</div><div class="oe-node-note">' + esc(node.note || '') + '</div><div class="oe-node-key">' + esc(node.ability_key || '') + '</div></div><div class="oe-node-actions">' + nodeActionsHtml(node,index,editable,soon) + '</div>' + (children.length ? '<div class="oe-children">' + children.map(function(child){return childHtml(child,index,editable);}).join('') + '</div>' : '') + '</article>'; }
  function renderTimeline() { var host=el('oeTimeline'); if (!host) return; var editable=currentTemplateIsEditable(), count=state.nodes.length, childCount=state.nodes.reduce(function(total,node){return total+workflowChildren(node).length;},0); el('oeTimelineMeta').textContent=count + ' 个节点' + (childCount ? ' · ' + childCount + ' 个下级动作' : ''); host.innerHTML=count ? state.nodes.map(function(node,index){return nodeHtml(node,index,editable);}).join('') : '<div class="oe-empty-list">还没有节点，点击“添加节点”开始配置。</div>'; }
  function renderEditor() {
    var body=el('oeEditorBody'), empty=el('oeEditorEmpty'), template=state.selectedTemplate; if (!template) { body.hidden=true; empty.hidden=false; return; }
    body.hidden=false; empty.hidden=true; el('oeEditorTitle').textContent=template.name || '未命名员工'; el('oeEditorSubtitle').textContent=isSalesTemplate(template) ? '销售 24 小时工作流 · 复用 H5 销售逻辑' : (template.source === 'granted' ? '授权模板 · 只读配置' : '自定义工作流'); if (el('oeTemplateName').dataset.oeTemplateDraftKey !== currentDraftKey()) { el('oeTemplateName').value=template.name || ''; el('oeTemplateName').dataset.oeTemplateDraftKey=currentDraftKey(); } renderStatus(); renderTimeline();
    var editable=currentTemplateIsEditable(); el('oeTemplateName').disabled=!editable; el('oeTemplateName').title=editable ? '' : '授权模板不能修改';
    document.querySelectorAll('#content-h5-employees [data-oe-action="save"],#content-h5-employees [data-oe-action="add"]').forEach(function(button){button.disabled=!editable || !!state.submitting;});
    document.querySelectorAll('#content-h5-employees [data-oe-action="activate"],#content-h5-employees [data-oe-action="stop"]').forEach(function(button){button.disabled=!!state.submitting;});
    document.querySelectorAll('#content-h5-employees [data-oe-action="delete-template"]').forEach(function(button){
      var id=String(state.editingId || template && template.id || '').trim();
      button.disabled=!!state.submitting || !id || !templateIsEditable(template);
    });
  }
  function render() { renderDevices(); renderList(); renderEditor(); }
  function loadTemplates() {
    var iid=selectedDeviceId(), query=iid ? '?installation_id=' + encodeURIComponent(iid) : '';
    return api('/api/h5-workflows/templates' + query).then(function(data){ state.templates=(Array.isArray(data.templates) ? data.templates : []).map(normalizeTemplate); return state.templates; });
  }
  function loadLocalWechatContacts() {
    var base=localBaseUrl();
    if (!base) return Promise.resolve([]);
    return fetch(base + '/api/native-wechat/contacts?account_id=pc-wechat-default&limit=500&offset=0', {headers:headers()})
      .then(function(response){return response.json().catch(function(){return {};}).then(function(data){if (!response.ok) return []; return Array.isArray(data.items) ? data.items : [];});})
      .catch(function(){return [];});
  }
  function loadDevices() {
    return Promise.all([api('/api/h5-chat/devices/status'), loadLocalWechatContacts()]).then(function(results){
      var data=results[0] || {}, localContacts=Array.isArray(results[1]) ? results[1] : [];
      state.devices=Array.isArray(data.devices) ? data.devices : [];
      if (localContacts.length) {
        var currentId=selectedDeviceId();
        var target=state.devices.find(function(item){return item && String(item.installation_id || '') === currentId;})
          || state.devices.find(function(item){return item && item.online;}) || state.devices[0];
        if (target) target.wechat_contacts=localContacts;
      }
      return state.devices;
    });
  }
  function loadActive() { var iid=selectedDeviceId(); if (!iid) { state.active=null; renderStatus(); return Promise.resolve(null); } return api('/api/h5-workflows/active?installation_id=' + encodeURIComponent(iid)).then(function(data){state.active=data.activation || null; renderStatus(); return state.active;}); }
  function applyServerTemplate(id) { state.selectedId=String(id || 'system_sales'); var base=normalizeTemplate(templateForSelected()); state.editingId=base.source === 'own' ? String(base.id || '') : ''; state.editingMeta=Object.assign({},base.meta || {}); state.nodes=normalizeWorkflowTimeline(base.nodes || []); if (state.selectedId === 'system_sales' && !ownSalesMirror()) { base=normalizeTemplate(Object.assign({},base,{nodes:salesNodes()})); state.nodes=normalizeWorkflowTimeline(base.nodes || []); } state.selectedTemplate=base; if (el('oeTemplateName')) delete el('oeTemplateName').dataset.oeTemplateDraftKey; render(); loadActive().catch(function(){}); return base; }
  function selectTemplate(id) { var selected=String(id || 'system_sales'); return loadTemplates().then(function(){return applyServerTemplate(selected);}); }
  function resetNew() { state.selectedId=''; state.selectedTemplate={id:'',source:'own',name:'新员工',nodes:[],meta:{}}; state.editingId=''; state.editingMeta={}; state.nodes=[]; if (el('oeTemplateName')) delete el('oeTemplateName').dataset.oeTemplateDraftKey; render(); }
  function findOption(key, label) { return NODE_OPTIONS.find(function(item){return item[0] === key && (!label || item[1] === label);}) || NODE_OPTIONS.find(function(item){return item[0] === key;}) || [String(key || ''), String(key || ''), String(key || ''), '销售员工', String(key || '')]; }
  function nodeOptionFromValue(value) { return NODE_OPTIONS.find(function(item){return item[4] === String(value || '');}) || findOption(value); }
  function fillNodeOptions(selected, selectedLabel) {
    var select=el('oeNodeKey'); if (!select) return;
    var groups={}, order=[];
    NODE_OPTIONS.forEach(function(item){var group=item[3] || '销售员工';if(!groups[group]){groups[group]=[];order.push(group);}groups[group].push(item);});
    select.innerHTML=order.map(function(group){return '<optgroup label="' + esc(group) + '">' + groups[group].map(function(item){return '<option value="' + esc(item[4]) + '">' + esc(item[1]) + '</option>';}).join('') + '</optgroup>';}).join('');
    var option=findOption(selected,selectedLabel);
    select.value=option[4] || NODE_OPTIONS[0][4];
  }
  function closeNodeModal() { el('oeNodeModal').hidden=true; state.nodeEditIndex=-1; }
  function fillChildOptions(parent,selected) { var select=el('oeChildType'), options=childOptions(parent,selected); if (!select) return; select.innerHTML=options.map(function(item){return '<option value="' + esc(item[0]) + '">' + esc(item[1]) + '</option>';}).join(''); select.value=options.some(function(item){return item[0] === selected;}) ? selected : (options[0] && options[0][0] || 'publish'); }
  function syncChildModalFields() { var type=String(el('oeChildType') && el('oeChildType').value || 'publish'), publish=type === 'publish', field=el('oeChildPlatformField'); if (field) field.hidden=!publish; if (el('oeChildPlatform')) el('oeChildPlatform').disabled=!publish; syncMomentPicker('child',type === 'native_wechat_moments_engage'); }
  function openChildModal(parentIndex,childId) { requireEditableTemplate(); var index=Number(parentIndex), parent=state.nodes[index]; if (!parent) throw new Error('未找到上级节点'); var existing=workflowChildren(parent).find(function(child){return String(child && child.id || '') === String(childId || '');}) || null; state.childParentIndex=index; state.childEditId=existing ? String(existing.id || '') : ''; el('oeChildModalTitle').textContent=existing ? '编辑下级动作' : '添加下级动作'; el('oeChildParent').textContent=parent.ability_label || parent.note || '上级节点'; el('oeChildTime').value=existing && existing.time || parent.end_time || parent.time || '09:00'; el('oeChildEndTime').value=existing && existing.end_time || ''; fillChildOptions(parent,existing ? childActionType(existing) : ''); el('oeChildPlatform').value=existing && existing.platform || 'douyin'; var childParams=workflowPayload(existing).params || {}; el('oeChildMomentAction').value=String(childParams.moment_action || 'like_comment'); initMomentPicker('child',Array.isArray(childParams.contact_wx_nos) ? childParams.contact_wx_nos : childParams.targets); syncChildModalFields(); el('oeChildModal').hidden=false; setTimeout(function(){el('oeChildTime').focus();},60); }
  function closeChildModal() { if (el('oeChildModal')) el('oeChildModal').hidden=true; state.childParentIndex=-1; state.childEditId=''; }
  function removeChild(parentIndex,childId) { requireEditableTemplate(); var index=Number(parentIndex), parent=state.nodes[index]; if (!parent) return Promise.resolve(); var children=workflowChildren(parent).filter(function(child){return String(child && child.id || '') !== String(childId || '');}); state.nodes[index]=syncParentChildRules(Object.assign({},parent,{children:children})); renderEditor(); return saveTemplate(); }
  function payloadToSave() { requireEditableTemplate(); state.nodes=normalizeWorkflowTimeline(migrateGroupInviteNodes(state.nodes)); var name=(el('oeTemplateName').value || '').trim(); if (!name) throw new Error('请填写员工名称'); if (!state.nodes.length) throw new Error('请至少添加一个节点'); var meta=Object.assign({},state.editingMeta || {}); if (isSalesTemplate(state.selectedTemplate)) {meta.system_template_key='system_sales';meta.source=meta.source || 'system_mirror';} return {name:name,nodes:clone(state.nodes),meta:meta}; }
  function workflowDemoPlan(node) {
    if (!node || node.comingSoon || node.workflow_placeholder || node.plan && node.plan.payload && node.plan.payload.skip_execution) throw new Error('敬请期待');
    var raw=node.plan && typeof node.plan === 'object' ? clone(node.plan) : planForRow({key:node.ability_key,label:node.ability_label,note:node.note,time:node.time,end:node.end_time});
    return {
      title:'演示-' + (raw.title || node.ability_label || '员工节点'),
      task_kind:raw.task_kind || raw.taskKind || 'client_workflow',
      content:raw.content || 'Online 员工节点演示：' + (node.ability_label || '任务节点'),
      payload:raw.payload || {}
    };
  }
  function demoNode(index) {
    var iid=selectedDeviceId(), device=state.devices.find(function(item){return String(item.installation_id || '') === iid;});
    if (!iid) throw new Error('请先选择 Online 设备');
    if (!device || !device.online) throw new Error('请选择在线的 Online 设备');
    var plan=workflowDemoPlan(state.nodes[Number(index)]);
    return runSubmission('demo',function(){
      return api('/api/scheduled-tasks/tasks',{method:'POST',headers:{'X-Installation-Id':iid},json:{
        title:plan.title,
        task_kind:plan.task_kind,
        content:plan.content,
        payload:plan.payload,
        schedule_type:'once',
        interval_seconds:60,
        start_at:'',
        daily_times:[],
        timezone_offset_minutes:-new Date().getTimezoneOffset(),
        installation_ids:[iid]
      }}).then(function(){if(typeof toast === 'function') toast('演示任务已下发，可在工作历史查看结果');});
    });
  }
  function runSubmission(kind, task) { if (state.submitting) return Promise.reject(new Error('操作正在处理中，请勿重复提交')); state.submitting=kind; clearError(); render(); return Promise.resolve().then(task).finally(function(){state.submitting='';render();}); }
  function saveTemplate() { var body=payloadToSave(), id=String(state.editingId || ''); body.installation_id=selectedDeviceId(); return runSubmission('save',function(){return api(id ? '/api/h5-workflows/templates/' + encodeURIComponent(id) : '/api/h5-workflows/templates',{method:id?'PATCH':'POST',json:body}).then(function(data){var saved=data.template || {}; state.editingId=String(saved.id || id); state.editingMeta=Object.assign({},saved.meta || body.meta); state.selectedId=String(saved.id || state.selectedId || 'system_sales'); return loadTemplates().then(function(){state.selectedTemplate=normalizeTemplate(state.templates.find(function(item){return String(item.id) === state.editingId;}) || Object.assign({},saved,{source:'own'})); state.nodes=clone(state.selectedTemplate.nodes || body.nodes); if (el('oeTemplateName')) delete el('oeTemplateName').dataset.oeTemplateDraftKey; render(); if (typeof loadOnlineH5Employees === 'function') loadOnlineH5Employees(); return saved;});});}); }
  function askPlanDay() { var answer=window.prompt('请输入本次销售工作流从第几天开始执行（1-30）','1'); if (answer === null) return null; var day=Number(answer); if (!Number.isInteger(day) || day < 1 || day > 30) throw new Error('执行天数请输入 1 到 30 的整数'); return day; }
  function activateTemplate() { var iid=selectedDeviceId(), template=state.selectedTemplate; if (!iid) throw new Error('请先选择 Online 设备'); var device=state.devices.find(function(item){return String(item.installation_id) === iid;}); if (!device || !device.online) throw new Error('请选择在线的 Online 设备'); var day=templateNeedsPlanDay(template) ? askPlanDay() : undefined; if (day === null) return Promise.resolve(); var requestFactory; if (state.selectedId === 'system_sales' && !state.editingId) requestFactory=function(){return api('/api/h5-workflows/activate-inline',{method:'POST',json:{template_key:'system_sales',name:'销售员工',nodes:clone(state.nodes),installation_id:iid,timezone_offset_minutes:-new Date().getTimezoneOffset(),plan_day:day}});}; else { var id=String(state.editingId || template && template.id || ''); if (!id) return saveTemplate().then(activateTemplate); requestFactory=function(){return api('/api/h5-workflows/activate',{method:'POST',json:{template_id:Number(id),installation_id:iid,timezone_offset_minutes:-new Date().getTimezoneOffset(),...(day ? {plan_day:day} : {})}});}; } return runSubmission('activate',requestFactory).then(function(data){state.active=data.activation || null; renderStatus(); if (typeof toast === 'function') toast('员工工作流已启用');}); }
  function stopTemplate() { var active=activeForDevice(); if (!active || !active.id) throw new Error('当前设备没有启用员工'); return runSubmission('stop',function(){return api('/api/h5-workflows/activations/' + encodeURIComponent(active.id) + '/stop',{method:'POST',json:{}}).then(function(){state.active=null;if(typeof toast==='function')toast('员工工作流已停用');});}); }
  function deleteTemplate() {
    var template=state.selectedTemplate, id=String(state.editingId || template && template.id || '').trim();
    if (!id || !templateIsEditable(template)) throw new Error('只能删除自己创建的员工');
    if (!window.confirm('删除员工「' + (template.name || '未命名员工') + '」？删除后服务器模板会移除，已启用的该员工也会停用。')) return Promise.resolve();
    return runSubmission('delete',function(){return api('/api/h5-workflows/templates/' + encodeURIComponent(id),{method:'DELETE'}).then(function(){
      if (state.active && String(state.active.template_id || '') === id) state.active=null;
      state.selectedTemplate=null;
      state.editingId='';
      state.editingMeta={};
      state.nodes=[];
      state.selectedId='system_sales';
      return loadTemplates().then(function(){applyServerTemplate('system_sales'); if (typeof loadOnlineH5Employees === 'function') loadOnlineH5Employees(); if(typeof toast==='function')toast('员工已删除');});
    });});
  }
  function bind(root) {
    if (root.dataset.oeBound) return;
    root.dataset.oeBound='1';
    root.addEventListener('click',function(event){
      var target=event.target.closest('[data-oe-action],[data-oe-template],[data-oe-demo],[data-oe-edit],[data-oe-delete],[data-oe-child-add],[data-oe-child-edit],[data-oe-child-delete],[data-oe-moment-contact]');
      if(!target || state.submitting) return;
      try {
        if (target.dataset.oeMomentContact != null) { var scope=String(target.dataset.oeMomentScope || 'node'), selected=momentSelection(scope), wxNo=String(target.dataset.oeMomentContact || ''); if (target.checked) selected[wxNo]=true; else delete selected[wxNo]; renderMomentPicker(scope); return; }
        if (target.dataset.oeTemplate != null) {selectTemplate(target.dataset.oeTemplate).catch(function(err){showError(err.message || '员工数据加载失败');});return;}
        if (target.dataset.oeDemo != null) {demoNode(Number(target.dataset.oeDemo)).catch(function(err){showError(err.message || '演示失败');});return;}
        if (target.dataset.oeChildAdd != null) {openChildModal(Number(target.dataset.oeChildAdd),'');return;}
        if (target.dataset.oeChildEdit != null) {openChildModal(Number(target.dataset.oeChildParent),target.dataset.oeChildEdit);return;}
        if (target.dataset.oeChildDelete != null) {if(window.confirm('删除这个下级动作？')) removeChild(Number(target.dataset.oeChildParent),target.dataset.oeChildDelete).catch(function(err){showError(err.message || '删除失败');});return;}
        if (target.dataset.oeEdit != null) {openNodeModal(Number(target.dataset.oeEdit));return;}
        if (target.dataset.oeDelete != null) {requireEditableTemplate();if(window.confirm('删除这个工作流节点？')) {state.nodes.splice(Number(target.dataset.oeDelete),1);state.nodes=normalizeWorkflowTimeline(state.nodes);renderEditor();saveTemplate().catch(function(err){showError(err.message || '删除失败');});}return;}
        var action=target.dataset.oeAction;
        if(action==='refresh') {initialize(true);return;}
        if(action==='new') {resetNew();return;}
        if(action==='add') {openNodeModal(-1);return;}
        if(action==='close-modal') {closeNodeModal();return;}
        if(action==='save-node') {saveNodeFromModal().catch(function(err){showError(err.message || '保存失败');});return;}
        if(action==='close-child-modal') {closeChildModal();return;}
        if(action==='save-child') {saveChildFromModal().catch(function(err){showError(err.message || '保存失败');});return;}
        if(action==='moment-node-prev' || action==='moment-child-prev') {state.momentContactPage=Math.max(1,state.momentContactPage-1);renderMomentPicker(action.indexOf('child')>=0?'child':'node');return;}
        if(action==='moment-node-next' || action==='moment-child-next') {state.momentContactPage+=1;renderMomentPicker(action.indexOf('child')>=0?'child':'node');return;}
        if(action==='save') {saveTemplate().catch(function(err){showError(err.message);});return;}
        if(action==='activate') {activateTemplate().catch(function(err){showError(err.message);});return;}
        if(action==='stop') {stopTemplate().catch(function(err){showError(err.message);});return;}
        if(action==='delete-template') {deleteTemplate().catch(function(err){showError(err.message);});return;}
      } catch(err) {showError(err.message || String(err));}
    });
    root.addEventListener('change',function(event){
      if(event.target.id==='oeNodeKey') { var option=nodeOptionFromValue(event.target.value); if (option) { el('oeNodeLabel').value=option[1]; el('oeNodeNote').value=option[2] || option[1]; } syncNodeModalFields(); }
      if(event.target.id==='oeChildType') syncChildModalFields();
    });
    root.addEventListener('input',function(event){
      if(event.target.id==='oeTemplateName') { if(state.selectedTemplate) state.selectedTemplate=Object.assign({},state.selectedTemplate,{name:String(event.target.value || '')}); rememberCurrentDraft(); return; }
      if(event.target.id==='oeNodeMomentSearch' || event.target.id==='oeChildMomentSearch') {state.momentContactSearch=String(event.target.value || '');state.momentContactPage=1;renderMomentPicker(event.target.id.indexOf('Child')>=0?'child':'node');}
    });
  }
  function initialize(force) { var root=el('content-h5-employees'); if(!root) return; bind(root); clearError(); var requested=String(window.__onlineEmployeeSelectedId || '').trim(); if(requested) {state.selectedId=requested; window.__onlineEmployeeSelectedId='';} state.loading=true; Promise.all([loadTemplates(),loadDevices()]).then(function(){ if(!state.selectedId) state.selectedId='system_sales'; applyServerTemplate(state.selectedId); }).catch(function(err){showError(err.message || '员工数据加载失败'); if(!state.selectedId) {state.selectedId='system_sales';state.selectedTemplate={id:'system_sales',source:'system',name:'销售员工',meta:{system_template_key:'system_sales'},nodes:salesNodes()};state.nodes=clone(state.selectedTemplate.nodes);render();} }).finally(function(){state.loading=false;}); }
  function syncNodeModalFields() {
    var option=nodeOptionFromValue((el('oeNodeKey') || {}).value || ''), key=String(option[0] || ''), label=String((el('oeNodeLabel') || {}).value || ''), note=String((el('oeNodeNote') || {}).value || ''), takeover=key === 'native_wechat_poll', douyinPrivate=isDouyinPrivate({ability_key:key,ability_label:label,note:note}), douyinCollection=key === 'douyin_leads' && salesAction(note || label) === 'search_collect';
    if (el('oeNodeGroupInviteField')) el('oeNodeGroupInviteField').hidden=!takeover;
    if (el('oeNodeWechatPrivateSessionLimitField')) el('oeNodeWechatPrivateSessionLimitField').hidden=!takeover;
    if (el('oeNodeWechatAddFriendField')) el('oeNodeWechatAddFriendField').hidden=!douyinPrivate;
    if (el('oeNodeDouyinReplyModeField')) el('oeNodeDouyinReplyModeField').hidden=!douyinPrivate;
    if (el('oeNodeDouyinCollectionField')) el('oeNodeDouyinCollectionField').hidden=!douyinCollection;
    if (el('oeNodeDouyinFollowupField')) el('oeNodeDouyinFollowupField').hidden=!douyinCollection;
    syncMomentPicker('node',key === 'native_wechat_moments_engage');
  }
  function openNodeModal(index) {
    requireEditableTemplate(); state.nodeEditIndex=typeof index === 'number' ? index : -1;
    var node=state.nodeEditIndex >= 0 ? state.nodes[state.nodeEditIndex] : null, params=workflowPayload(node).params || {};
    el('oeNodeModalTitle').textContent=node ? '编辑工作流节点' : '添加工作流节点';
    el('oeNodeTime').value=node && node.time || '09:00'; el('oeNodeEndTime').value=node && node.end_time || '';
    var option=findOption(node && node.ability_key,node && node.ability_label);
    fillNodeOptions(node && node.ability_key,node && node.ability_label); el('oeNodeLabel').value=node && node.ability_label || option[1]; el('oeNodeNote').value=node && node.note || option[2] || option[1];
    el('oeNodeGroupInviteEnabled').checked=!!params.group_invite_enabled; el('oeNodeWechatAddFriendEnabled').checked=boolParam(params.wechat_add_friend_enabled,false);
    if (el('oeNodeDouyinReplyMode')) el('oeNodeDouyinReplyMode').value=String(params.reply_mode || 'fixed').toLowerCase() === 'ai_lead' ? 'ai_lead' : 'fixed';
    if (el('oeNodeDouyinKeyword')) el('oeNodeDouyinKeyword').value=String(params.keyword || params.query || '');
    if (el('oeNodeDouyinRegions')) el('oeNodeDouyinRegions').value=(Array.isArray(params.regions) ? params.regions.join('，') : String(params.regions || '全国'));
    if (el('oeNodeDouyinMaxResults')) el('oeNodeDouyinMaxResults').value=Math.max(10,Math.min(100,Number(params.max_results || 50)));
    if (el('oeNodeDouyinMode')) el('oeNodeDouyinMode').value=['script','api'].indexOf(String(params.mode || '').toLowerCase()) >= 0 ? String(params.mode).toLowerCase() : 'script';
    var followups=Object.prototype.hasOwnProperty.call(params,'followup_actions') ? normalizeDouyinFollowupActions(params.followup_actions) : DOUYIN_FOLLOWUP_ACTIONS.slice();
    [['oeNodeDouyinFollowupReplyComments','reply_comments'],['oeNodeDouyinFollowupMentionComment','mention_comment'],['oeNodeDouyinFollowupFollowComment','follow_comment'],['oeNodeDouyinFollowupDirectMessage','direct_message']].forEach(function(item){if(el(item[0]))el(item[0]).checked=followups.indexOf(item[1])>=0;});
    el('oeNodeWechatPrivateSessionLimit').value=Math.max(1,Math.min(100,Number(params.private_sessions_per_round || 10)));
    el('oeNodeMomentAction').value=String(params.moment_action || 'like_comment'); initMomentPicker('node',Array.isArray(params.contact_wx_nos) ? params.contact_wx_nos : params.targets);
    syncNodeModalFields(); el('oeNodeModal').hidden=false; setTimeout(function(){el('oeNodeTime').focus();},60);
  }
  function saveNodeFromModal() {
    requireEditableTemplate(); var option=nodeOptionFromValue(el('oeNodeKey').value), key=String(option[0] || ''), time=el('oeNodeTime').value, end=el('oeNodeEndTime').value, label=el('oeNodeLabel').value.trim() || option[1], note=el('oeNodeNote').value.trim() || option[2] || label;
    if (!/^\d{2}:\d{2}$/.test(time)) throw new Error('请选择开始时间');
    var existing=state.nodeEditIndex >= 0 ? state.nodes[state.nodeEditIndex] : null, existingParams=workflowPayload(existing).params || {}, row={time:time,end:end,key:key,label:label,note:note,params:Object.assign({},existingParams,{group_invite_enabled:key === 'native_wechat_poll' && !!el('oeNodeGroupInviteEnabled').checked})};
    if (key === 'native_wechat_poll') row.params.private_sessions_per_round=Math.max(1,Math.min(100,Number(el('oeNodeWechatPrivateSessionLimit').value || 10)));
    else delete row.params.private_sessions_per_round;
    if (key === 'native_wechat_moments_engage') { row.params.contact_wx_nos=momentSelectionValues('node'); row.params.targets=row.params.contact_wx_nos.slice(); row.params.moment_action=String(el('oeNodeMomentAction').value || 'like_comment'); row.params.max_scrolls=Number(row.params.max_scrolls || 6); if (!row.params.contact_wx_nos.length) throw new Error('请选择至少一个朋友圈联系人'); }
    else { delete row.params.contact_wx_nos; delete row.params.targets; delete row.params.moment_action; }
    if (isDouyinPrivate({ability_key:key,ability_label:label,note:note})) { row.params.wechat_add_friend_enabled=!!el('oeNodeWechatAddFriendEnabled').checked; row.params.wechat_add_friend_targets_source='douyin_private_message_phone'; row.params.reply_mode=String((el('oeNodeDouyinReplyMode') || {}).value || 'fixed').toLowerCase() === 'ai_lead' ? 'ai_lead' : 'fixed'; }
    else { delete row.params.wechat_add_friend_enabled; delete row.params.wechat_add_friend_targets_source; delete row.params.wechat_add_friend_rules; delete row.params.reply_mode; }
    if (key === 'douyin_leads' && salesAction(note || label) === 'search_collect') {
      var keyword=String((el('oeNodeDouyinKeyword') || {}).value || '').trim();
      if (!keyword) throw new Error('请填写采集关键词');
      var regions=String((el('oeNodeDouyinRegions') || {}).value || '全国').split(/[，,\n]+/).map(function(value){return value.trim();}).filter(Boolean);
      row.params.keyword=keyword;
      row.params.regions=regions.length ? regions : ['全国'];
      row.params.max_results=Math.max(10,Math.min(100,Number((el('oeNodeDouyinMaxResults') || {}).value || 50)));
      row.params.mode=['script','api'].indexOf(String((el('oeNodeDouyinMode') || {}).value || '').toLowerCase()) >= 0 ? String(el('oeNodeDouyinMode').value).toLowerCase() : 'script';
      row.params.followup_actions=normalizeDouyinFollowupActions([
        el('oeNodeDouyinFollowupReplyComments') && el('oeNodeDouyinFollowupReplyComments').checked ? 'reply_comments' : '',
        el('oeNodeDouyinFollowupMentionComment') && el('oeNodeDouyinFollowupMentionComment').checked ? 'mention_comment' : '',
        el('oeNodeDouyinFollowupFollowComment') && el('oeNodeDouyinFollowupFollowComment').checked ? 'follow_comment' : '',
        el('oeNodeDouyinFollowupDirectMessage') && el('oeNodeDouyinFollowupDirectMessage').checked ? 'direct_message' : ''
      ]);
      row.params.customer_scope='current_collection_batch';
    } else { delete row.params.keyword; delete row.params.regions; delete row.params.max_results; delete row.params.mode; delete row.params.followup_actions; delete row.params.customer_scope; }
    delete row.params.followup_action; delete row.params.group_invite_rules;
    var next=existing ? Object.assign({},existing) : {id:'wf_' + Date.now().toString(36),department_id:'sales',department_name:'销售部',sales_preset:isSalesTemplate(state.selectedTemplate)};
    next.time=time; next.end_time=end; next.time_range=time + (end ? '-' + end : ''); next.ability_key=key; next.ability_label=label; next.note=note; next.plan=planForRow(row); if (existing) next.children=existing.children || existing.actions || [];
    state.nodes=state.nodeEditIndex >= 0 ? state.nodes.map(function(item,index){return index === state.nodeEditIndex ? next : item;}) : state.nodes.concat(next); state.nodes=normalizeWorkflowTimeline(state.nodes); closeNodeModal(); renderEditor();
    return saveTemplate().then(function(saved){if(typeof toast==='function')toast('节点参数已保存到服务器');return saved;});
  }
  function saveChildFromModal() {
    requireEditableTemplate();
    var parentIndex=state.childParentIndex, parent=state.nodes[parentIndex];
    if (!parent) throw new Error('未找到上级节点');
    var editId=String(state.childEditId || ''), time=String(el('oeChildTime').value || '').trim(), end=String(el('oeChildEndTime').value || '').trim();
    var type=String(el('oeChildType').value || 'publish').trim(), platform=String(el('oeChildPlatform').value || 'douyin').trim();
    if (!/^\d{2}:\d{2}$/.test(time)) throw new Error('请选择动作时间');
    if (end && !/^\d{2}:\d{2}$/.test(end)) throw new Error('结束时间格式不正确');
    var existing=editId ? workflowChildren(parent).find(function(child){return String(child.id || '') === editId;}) : null;
    if (childOptions(parent,existing ? childActionType(existing) : '').map(function(item){return item[0];}).indexOf(type) < 0) throw new Error('这个上级节点不支持所选动作');
    if (type === 'publish' && ['douyin','toutiao','wechat_channels','wechat_moments'].indexOf(platform) < 0) throw new Error('暂时只支持抖音、头条、视频号和朋友圈');
    if (type === 'native_wechat_moments_engage' && !momentSelectionValues('child').length) throw new Error('请选择至少一个朋友圈联系人');
    var children=workflowChildren(parent).slice(), duplicate=children.find(function(child){
      if (editId && String(child.id || '') === editId) return false;
      var childType=childActionType(child);
      return type === 'publish' ? childType === 'publish' && String(child.platform || '') === platform : childType === type;
    });
    if (duplicate) throw new Error(type === 'publish' ? '这个平台已经有发布动作了' : '这个下级动作已经添加过了');
    var next=buildWorkflowChild(parent,{time:time,end_time:end,action_type:type,platform:platform,contact_wx_nos:momentSelectionValues('child'),moment_action:String(el('oeChildMomentAction').value || 'like_comment')},existing);
    children=children.filter(function(child){return String(child.id || '') !== String(next.id || '');}).concat(next).sort(function(a,b){return String(a.time || '').localeCompare(String(b.time || ''));});
    state.nodes[parentIndex]=syncParentChildRules(Object.assign({},parent,{children:children}));
    closeChildModal(); renderEditor();
    return saveTemplate().then(function(saved){if(typeof toast==='function')toast('下级动作已保存到服务器');return saved;});
  }
  window.initOnlineH5EmployeesView = function() { initialize(false); };
})();
