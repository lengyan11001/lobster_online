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
    nodeEditIndex: -1
  };

  var SALES_ROWS = [
    {time:'06:00',end:'06:30',key:'local_bestseller',label:'创作同城爆款视频',note:'创作一条同城爆款视频（用于发公域平台）',publish:[['08:45','douyin','同城爆款视频发布抖音'],['09:00','wechat_channels','同城爆款视频发布视频号']]},
    {time:'06:30',end:'07:00',key:'hifly.video.create_by_tts',label:'创作数字人口播视频',note:'创作一条数字人口播视频（用于发朋友圈）',publish:[['09:30','wechat_moments','微信朋友圈发布']]},
    {time:'07:00',end:'07:15',key:'native_wechat_add_friend',label:'微信自动加好友',note:'从抖音私信接管结果中提取明确微信号后加好友，没有明确微信号则跳过',params:{source_mode:'douyin_private_message_wechat_id',trigger:'clear_wechat_id',skip_without_clear_wechat_id:true}},
    {time:'07:15',end:'07:30',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管'},
    {time:'07:30',end:'07:45',key:'native_wechat_poll',label:'微信自动拉群',note:'微信私信接管后判断特殊意向，命中后拉群；拉群成员规则待配置',params:{followup_action:'group_invite',group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'07:45',end:'08:15',key:'douyin_leads',label:'抖音自动养号',note:'抖音自动养号'},
    {time:'08:15',end:'08:45',key:'wechat_channels_nurture',label:'视频号自动养号（敬请期待）',note:'视频号自动养号',soon:true},
    {time:'09:15',end:'09:30',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管'},
    {time:'09:45',end:'10:00',key:'native_wechat_moments_engage',label:'微信朋友圈点赞评论',note:'微信朋友圈点赞评论'},
    {time:'10:00',end:'10:15',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管'},
    {time:'10:15',end:'10:30',key:'native_wechat_poll',label:'微信自动拉群',note:'微信私信接管后判断特殊意向，命中后拉群；拉群成员规则待配置',params:{followup_action:'group_invite',group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'10:30',end:'11:00',key:'douyin_leads',label:'抖音自动养号',note:'抖音自动养号'},
    {time:'11:00',end:'11:30',key:'wechat_channels_nurture',label:'视频号自动养号（敬请期待）',note:'视频号自动养号',soon:true},
    {time:'11:30',end:'12:00',key:'douyin_leads',label:'抖音获客·关键词抓取精准客户',note:'抖音获客·关键词抓取精准客户'},
    {time:'12:00',end:'12:15',key:'douyin_leads',label:'抖音回复精准客户评论10个',note:'抖音回复精准客户评论10个'},
    {time:'12:15',end:'12:30',key:'douyin_leads',label:'抖音自己评论区接管',note:'抖音自己评论区接管，评论并@10个精准客户'},
    {time:'12:30',end:'12:45',key:'douyin_leads',label:'抖音关注精准客户并评论首条作品',note:'抖音关注10个精准客户，并找到他的首条作品去评论'},
    {time:'12:45',end:'13:00',key:'native_wechat_add_friend',label:'微信自动加好友',note:'从抖音私信接管结果中提取明确微信号后加好友，没有明确微信号则跳过',params:{source_mode:'douyin_private_message_wechat_id',trigger:'clear_wechat_id',skip_without_clear_wechat_id:true}},
    {time:'13:00',end:'13:15',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管'},
    {time:'13:15',end:'13:30',key:'native_wechat_poll',label:'微信自动拉群',note:'微信私信接管后判断特殊意向，命中后拉群；拉群成员规则待配置',params:{followup_action:'group_invite',group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'13:30',end:'13:45',key:'native_wechat_moments_engage',label:'微信朋友圈自己评论区接管',note:'微信朋友圈自己评论区接管',params:{moment_action:'comment'}},
    {time:'13:45',end:'14:15',key:'hifly.video.create_by_tts',label:'创作数字人口播视频',note:'创作一条数字人口播视频（用于发朋友圈）',publish:[['14:15','wechat_moments','微信朋友圈发布']]},
    {time:'14:30',end:'14:45',key:'douyin_leads',label:'抖音主动私信精准客户',note:'抖音主动私信10个精准客户'},
    {time:'14:45',end:'15:00',key:'douyin_leads',label:'抖音私信接管',note:'抖音私信接管'},
    {time:'15:00',end:'15:15',key:'wechat_channels_comment',label:'视频号评论区接管（敬请期待）',note:'视频号评论区接管',soon:true},
    {time:'15:15',end:'15:30',key:'wechat_channels_message',label:'视频号私信接管（敬请期待）',note:'视频号私信接管',soon:true},
    {time:'15:30',end:'16:00',key:'douyin_leads',label:'抖音自动养号',note:'抖音自动养号'},
    {time:'16:00',end:'16:30',key:'wechat_channels_nurture',label:'视频号自动养号（敬请期待）',note:'视频号自动养号',soon:true},
    {time:'16:30',end:'16:45',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管'},
    {time:'16:45',end:'17:00',key:'native_wechat_poll',label:'微信自动拉群',note:'微信私信接管后判断特殊意向，命中后拉群；拉群成员规则待配置',params:{followup_action:'group_invite',group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'17:00',end:'17:15',key:'native_wechat_moments_engage',label:'微信朋友圈点赞评论',note:'微信朋友圈点赞评论'},
    {time:'17:15',end:'17:30',key:'douyin_leads',label:'抖音获客·关键词抓取精准客户',note:'抖音获客·关键词抓取精准客户'},
    {time:'17:30',end:'17:45',key:'douyin_leads',label:'抖音回复精准客户评论10个',note:'抖音回复精准客户评论10个'},
    {time:'17:45',end:'18:00',key:'douyin_leads',label:'抖音自己评论区接管',note:'抖音自己评论区接管，评论并@10个精准客户'},
    {time:'18:00',end:'18:15',key:'douyin_leads',label:'抖音关注精准客户并评论首条作品',note:'抖音关注10个精准客户，并找到他的首条作品去评论'},
    {time:'18:15',end:'18:30',key:'native_wechat_add_friend',label:'微信自动加好友',note:'从抖音私信接管结果中提取明确微信号后加好友，没有明确微信号则跳过',params:{source_mode:'douyin_private_message_wechat_id',trigger:'clear_wechat_id',skip_without_clear_wechat_id:true}},
    {time:'18:30',end:'18:45',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管'},
    {time:'18:45',end:'19:00',key:'native_wechat_poll',label:'微信自动拉群',note:'微信私信接管后判断特殊意向，命中后拉群；拉群成员规则待配置',params:{followup_action:'group_invite',group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'19:00',end:'19:15',key:'douyin_leads',label:'抖音主动私信精准客户',note:'抖音主动私信10个精准客户'},
    {time:'19:15',end:'19:30',key:'douyin_leads',label:'抖音私信接管',note:'抖音私信接管'},
    {time:'19:30',end:'20:00',key:'hifly.video.create_by_tts',label:'创作数字人口播视频',note:'创作一条数字人口播视频（用于发朋友圈）',publish:[['20:00','wechat_moments','微信朋友圈发布']]},
    {time:'20:15',end:'20:30',key:'wechat_channels_comment',label:'视频号评论区接管（敬请期待）',note:'视频号评论区接管',soon:true},
    {time:'20:30',end:'20:45',key:'wechat_channels_message',label:'视频号私信接管（敬请期待）',note:'视频号私信接管',soon:true},
    {time:'20:45',end:'21:00',key:'native_wechat_add_friend',label:'微信自动加好友',note:'从抖音私信接管结果中提取明确微信号后加好友，没有明确微信号则跳过',params:{source_mode:'douyin_private_message_wechat_id',trigger:'clear_wechat_id',skip_without_clear_wechat_id:true}},
    {time:'21:00',end:'22:00',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管'},
    {time:'22:00',end:'22:15',key:'native_wechat_poll',label:'微信自动拉群',note:'微信私信接管后判断特殊意向，命中后拉群；拉群成员规则待配置',params:{followup_action:'group_invite',group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'22:15',end:'22:30',key:'native_wechat_moments_engage',label:'朋友圈点赞评论（微信）',note:'朋友圈点赞评论（微信）'},
    {time:'22:30',end:'23:00',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管'},
    {time:'23:00',end:'23:30',key:'douyin_leads',label:'抖音自动养号',note:'抖音自动养号'},
    {time:'23:30',end:'24:00',key:'wechat_channels_nurture',label:'视频号自动养号（敬请期待）',note:'视频号自动养号',soon:true}
  ];

  var NODE_OPTIONS = [
    ['local_bestseller','同城爆款视频'], ['hifly.video.create_by_tts','数字人口播视频'],
    ['douyin_leads','抖音获客'], ['native_wechat_poll','微信私信接管'],
    ['native_wechat_add_friend','微信自动加好友'], ['native_wechat_moments_engage','朋友圈点赞评论'],
    ['ip_content_daily','IP日更文案']
  ];

  function el(id) { return document.getElementById(id); }
  function esc(value) { return String(value == null ? '' : value).replace(/[&<>"']/g, function(ch) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch]; }); }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function baseUrl() { return String((typeof API_BASE !== 'undefined' && API_BASE) || window.__API_BASE || '').replace(/\/$/, ''); }
  function headers() { return Object.assign({}, typeof authHeaders === 'function' ? authHeaders() : {}, {'Content-Type':'application/json'}); }
  function api(path, options) {
    options = options || {};
    return fetch(baseUrl() + path, {method:options.method || 'GET', headers:headers(), body:options.json === undefined ? undefined : JSON.stringify(options.json)})
      .then(function(response) { return response.json().catch(function() { return {}; }).then(function(data) { if (!response.ok) throw new Error(data.detail || data.message || ('请求失败（' + response.status + '）')); return data; }); });
  }
  function showError(message) { var box = el('oeError'); if (!box) return; box.textContent = message || ''; box.hidden = !message; }
  function clearError() { showError(''); }
  function activeTemplateKey(template) { return String(template && template.meta && (template.meta.system_template_key || template.meta.systemTemplateKey) || '').trim(); }
  function isSalesTemplate(template) { return String(state.selectedId) === 'system_sales' || activeTemplateKey(template) === 'system_sales' || (template && template.nodes || []).some(function(node) { return String(node && node.ability_key || '') === 'local_bestseller'; }); }
  function selectedDeviceId() { return String((el('oeDeviceSelect') && el('oeDeviceSelect').value) || '').trim(); }
  function scheduleDuration(start, end) {
    var parse = function(value) { var m = /^(\d{2}):(\d{2})$/.exec(String(value || '')); return m ? Number(m[1]) * 60 + Number(m[2]) : 0; };
    var a = parse(start), b = parse(end); if (end === '24:00') b = 1440; if (b && b < a) b += 1440; return Math.max(0, b - a);
  }
  function salesAction(note) {
    var text = String(note || '');
    if (text.indexOf('养号') >= 0) return 'account_nurture';
    if (text.indexOf('关键词抓取') >= 0) return 'search_collect';
    if (text.indexOf('回复') >= 0 && text.indexOf('评论') >= 0) return 'reply_comments';
    if (text.indexOf('@精准') >= 0) return 'mention_comment';
    if (text.indexOf('关注') >= 0 && text.indexOf('评论') >= 0) return 'follow_comment';
    if (text.indexOf('主动私信') >= 0) return 'direct_message';
    if (text.indexOf('私信接管') >= 0) return 'stranger_message';
    return 'search_collect';
  }
  function baseScheduleParams(row, params) {
    return Object.assign({}, params || {}, {sales_schedule_start:row.time, sales_schedule_end:row.end, sales_schedule_duration_minutes:scheduleDuration(row.time, row.end), sales_node_label:row.label || row.note || ''});
  }
  function nativePlan(key, row, extra) {
    var params = Object.assign({account_id:'pc-wechat-default', note:row.note || row.label || '', prompt:row.note || row.label || ''}, row.params || {}, extra || {});
    if (key === 'native_wechat_add_friend') Object.assign(params, {source_mode:'douyin_private_message_wechat_id',trigger:'clear_wechat_id',skip_without_clear_wechat_id:true,targets:[]});
    if (key === 'native_wechat_moments_engage') params.moment_action = params.moment_action || 'like_comment';
    if (params.followup_action === 'group_invite' || String(row.label || '').indexOf('拉群') >= 0) Object.assign(params, {followup_action:'group_invite',group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'});
    var title = key === 'native_wechat_add_friend' ? '个微自动加好友' : key === 'native_wechat_moments_engage' ? '朋友圈点赞评论' : (params.followup_action === 'group_invite' ? '个微自动拉群' : '个微私信接管');
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
    if (row.key === 'ip_content_daily') return {title:'IP日更文案',task_kind:'ip_content_daily',content:'H5 工作流：IP日更文案',payload:{template_id:0,use_personal_default:true,tasks:['industry_hot_oral','professional_ip_oral','moments_candidate'],sync_before:true,industry_count:5,ip_count:5,moments_count:20,requirements:{}}};
    var action = salesAction(prompt), max = action === 'search_collect' || action === 'account_nurture' ? 50 : 10;
    return {title:'抖音获客 - ' + prompt.slice(0,24),task_kind:'douyin_leads',content:'H5 工作流：抖音获客',payload:{action:'search_collect',params:baseScheduleParams(row,{keyword:prompt,query:prompt,search_keyword:prompt,sales_action:action,max_results:max,max_users:max,regions:['全国'],mode:'script'})}};
  }
  function makeNode(row, index) {
    var node = {id:'sales_' + row.time.replace(':','') + '_' + index,time:row.time,end_time:row.end,time_range:row.time + '-' + row.end,ability_key:row.key,ability_label:row.label,note:row.note,department_id:'sales',department_name:'销售部',sales_preset:true,comingSoon:!!row.soon,workflow_placeholder:!!row.soon,param_configured:false,plan:planForRow(row)};
    if (row.publish && !row.soon) node.children = row.publish.map(function(item, childIndex) { return publishChild(node, item, childIndex); });
    return node;
  }
  function isDouyinPrivate(node) { return node && node.ability_key === 'douyin_leads' && String(node.note || '').indexOf('私信接管') >= 0; }
  function isWechatPrivate(node) { return node && node.ability_key === 'native_wechat_poll' && String(node.note || '').indexOf('微信私信接管') >= 0 && String(node.note || '').indexOf('拉群') < 0; }
  function addChild(parent, row, index, type) {
    if (!parent) return false;
    var childKey = type === 'friend' ? 'native_wechat_add_friend' : 'native_wechat_poll';
    var child = {id:parent.id + '_native_' + row.time.replace(':','') + '_' + index,time:row.time,parent_node_id:parent.id,action_type:type === 'friend' ? 'native_wechat_add_friend' : 'native_wechat_group_invite',type:type === 'friend' ? 'native_wechat_add_friend' : 'native_wechat_group_invite',ability_key:childKey,ability_label:row.label,note:row.note,department_id:'sales',department_name:'销售部',sales_preset:true,is_action_node:true,param_configured:true,plan:nativePlan(childKey,row,type === 'friend' ? {} : {followup_action:'group_invite',group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'})};
    parent.children = (parent.children || []).concat(child).sort(function(a,b) { return String(a.time).localeCompare(String(b.time)); });
    var parentParams = parent.plan && parent.plan.payload && parent.plan.payload.params || {};
    if (type === 'friend') parent.plan.payload.params = Object.assign({}, parentParams,{wechat_add_friend_enabled:true,wechat_add_friend_targets_source:'douyin_private_message_wechat_id',wechat_add_friend_rules:[{child_node_id:child.id,time:child.time,trigger:'clear_wechat_id',skip_without_clear_wechat_id:true}]});
    else parent.plan.payload.params = Object.assign({}, parentParams,{group_invite_enabled:true,group_invite_rule_status:'pending_rules',group_invite_targets_source:'qualified_intent',group_invite_members:[],group_invite_manager_contacts:[],group_invite_rules:[{child_node_id:child.id,time:child.time,trigger:'qualified_intent',members:[],manager_contacts:[]}]});
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
  function normalizeTemplate(template) { var out = Object.assign({}, template || {}); out.nodes = Array.isArray(out.nodes) ? clone(out.nodes) : []; out.meta = out.meta && typeof out.meta === 'object' ? Object.assign({},out.meta) : {}; return out; }
  function ownSalesMirror() { return state.templates.find(function(item) { return activeTemplateKey(item) === 'system_sales'; }); }
  function templateForSelected() { if (state.selectedId === 'system_sales') return ownSalesMirror() || {id:'system_sales',source:'system',name:'销售员工',meta:{system_template_key:'system_sales'},nodes:salesNodes()}; return state.templates.find(function(item) { return String(item.id) === String(state.selectedId); }) || null; }
  function templateIsEditable(template) { return !!(template && template.source === 'own'); }
  function activeForDevice() { var iid = selectedDeviceId(); return state.active && String(state.active.installation_id || '') === iid ? state.active : null; }
  function renderDevices() {
    var select = el('oeDeviceSelect'); if (!select) return;
    var current = select.value;
    select.innerHTML = '<option value="">请选择 Online 设备</option>' + state.devices.map(function(device) { var id=String(device.installation_id || ''); var name=String(device.display_name || id.slice(0,12) || '未命名设备'); return '<option value="' + esc(id) + '">' + esc(name) + (device.online ? ' · 在线' : ' · 离线') + '</option>'; }).join('');
    if (current && state.devices.some(function(device) { return String(device.installation_id) === current; })) select.value = current;
    else { var online = state.devices.find(function(device) { return device.online; }); if (online) select.value = online.installation_id; }
  }
  function renderList() {
    var host = el('oeTemplateList'); if (!host) return;
    var rows = [{id:'system_sales',name:'销售员工',meta:{system_template_key:'system_sales'},source:'system',mark:'销'}].concat(state.templates.filter(function(item) { return activeTemplateKey(item) !== 'system_sales'; }).map(function(item) { return Object.assign({mark:String(item.name || '员').charAt(0)},item); }));
    if (!rows.length) { host.innerHTML = '<div class="oe-empty-list">当前账号没有可访问的员工模板。</div>'; return; }
    host.innerHTML = rows.map(function(item) { var selected=String(item.id)===String(state.selectedId); var meta=item.source === 'granted' ? '他人授权' : item.source === 'system' ? '系统员工' : '我的模板'; return '<button type="button" class="oe-employee-item' + (selected ? ' is-selected' : '') + '" data-oe-template="' + esc(item.id) + '"><span class="oe-employee-mark">' + esc(item.mark || String(item.name || '员').charAt(0)) + '</span><span class="oe-employee-copy"><span class="oe-employee-name">' + esc(item.name || '未命名员工') + '</span><span class="oe-employee-meta">' + meta + '</span></span></button>'; }).join('');
  }
  function renderStatus() {
    var status=el('oeActiveStatus'), active=activeForDevice(); if (!status) return;
    status.textContent = active ? '已启用 · ' + (active.template_name || '当前员工') : '未启用'; status.classList.toggle('is-active',!!active);
  }
  function childHtml(child) { var soon=child.comingSoon || child.workflow_placeholder || (child.plan && child.plan.payload && child.plan.payload.skip_execution); return '<div class="oe-child"><span class="oe-child-time">' + esc(child.time || '--:--') + '</span><span class="oe-child-copy">' + esc(child.ability_label || child.note || '子动作') + (soon ? '<small>视频号功能敬请期待</small>' : '<small>' + esc(child.note || '') + '</small>') + '</span><span class="oe-tag child">下一级</span></div>'; }
  function nodeHtml(node,index) { var soon=!!(node.comingSoon || node.workflow_placeholder || node.plan && node.plan.payload && node.plan.payload.skip_execution); var children=Array.isArray(node.children) ? node.children : (Array.isArray(node.actions) ? node.actions : []); return '<article class="oe-node' + (soon ? ' is-soon' : '') + '"><div class="oe-time">' + esc(node.time || '--:--') + (node.end_time ? '<br><span style="color:#a0aaba;font-size:.61rem;font-weight:400">' + esc(node.end_time) + '</span>' : '') + '</div><div class="oe-line"></div><div class="oe-node-main"><div class="oe-node-title"><span>' + esc(node.ability_label || node.note || '工作节点') + '</span>' + (soon ? '<span class="oe-tag soon">敬请期待</span>' : '') + (node.sales_preset ? '<span class="oe-tag">销售</span>' : '') + '</div><div class="oe-node-note">' + esc(node.note || '') + '</div><div class="oe-node-key">' + esc(node.ability_key || '') + '</div></div><div class="oe-node-actions">' + (!soon ? '<button type="button" class="oe-mini-btn" data-oe-edit="' + index + '">编辑</button><button type="button" class="oe-mini-btn" data-oe-delete="' + index + '">删除</button>' : '') + '</div>' + (children.length ? '<div class="oe-children">' + children.map(childHtml).join('') + '</div>' : '') + '</article>'; }
  function renderTimeline() { var host=el('oeTimeline'); if (!host) return; var count=state.nodes.length; el('oeTimelineMeta').textContent=count + ' 个节点' + (state.nodes.reduce(function(total,node){return total+(Array.isArray(node.children)?node.children.length:0);},0) ? ' · 含下一级动作' : ''); host.innerHTML=count ? state.nodes.map(nodeHtml).join('') : '<div class="oe-empty-list">还没有节点，点击“添加节点”开始配置。</div>'; }
  function renderEditor() {
    var body=el('oeEditorBody'), empty=el('oeEditorEmpty'), template=state.selectedTemplate; if (!template) { body.hidden=true; empty.hidden=false; return; }
    body.hidden=false; empty.hidden=true; el('oeEditorTitle').textContent=template.name || '未命名员工'; el('oeEditorSubtitle').textContent=isSalesTemplate(template) ? '销售 24 小时工作流 · 复用 H5 销售逻辑' : (template.source === 'granted' ? '授权模板 · 只读配置' : '自定义工作流'); el('oeTemplateName').value=template.name || ''; renderStatus(); renderTimeline();
    var editable=state.selectedId === 'system_sales' || templateIsEditable(template); el('oeTemplateName').disabled=!editable; el('oeTemplateName').title=editable ? '' : '授权模板不能修改';
    document.querySelectorAll('#content-h5-employees [data-oe-action="save"],#content-h5-employees [data-oe-action="add"]').forEach(function(button){button.disabled=!editable;});
  }
  function render() { renderDevices(); renderList(); renderEditor(); }
  function loadTemplates() { return api('/api/h5-workflows/templates').then(function(data){ state.templates=(Array.isArray(data.templates) ? data.templates : []).map(normalizeTemplate); return state.templates; }); }
  function loadDevices() { return api('/api/h5-chat/devices/status').then(function(data){state.devices=Array.isArray(data.devices) ? data.devices : []; return state.devices;}); }
  function loadActive() { var iid=selectedDeviceId(); if (!iid) { state.active=null; renderStatus(); return Promise.resolve(null); } return api('/api/h5-workflows/active?installation_id=' + encodeURIComponent(iid)).then(function(data){state.active=data.activation || null; renderStatus(); return state.active;}); }
  function selectTemplate(id) { state.selectedId=String(id || 'system_sales'); state.selectedTemplate=normalizeTemplate(templateForSelected()); state.editingId=state.selectedTemplate.source === 'own' ? String(state.selectedTemplate.id || '') : ''; state.editingMeta=Object.assign({},state.selectedTemplate.meta || {}); state.nodes=clone(state.selectedTemplate.nodes || []); if (state.selectedId === 'system_sales' && !ownSalesMirror()) state.nodes=salesNodes(); render(); loadActive().catch(function(){}); }
  function resetNew() { state.selectedId=''; state.selectedTemplate={id:'',source:'own',name:'新员工',nodes:[],meta:{}}; state.editingId=''; state.editingMeta={}; state.nodes=[]; render(); }
  function findOption(key) { return NODE_OPTIONS.find(function(item){return item[0] === key;}) || NODE_OPTIONS[0]; }
  function fillNodeOptions(selected) { var select=el('oeNodeKey'); if (!select) return; select.innerHTML=NODE_OPTIONS.map(function(item){return '<option value="' + esc(item[0]) + '">' + esc(item[1]) + '</option>';}).join(''); select.value=selected || NODE_OPTIONS[0][0]; }
  function openNodeModal(index) { state.nodeEditIndex=typeof index === 'number' ? index : -1; var node=state.nodeEditIndex >= 0 ? state.nodes[state.nodeEditIndex] : null; el('oeNodeModalTitle').textContent=node ? '编辑工作流节点' : '添加工作流节点'; el('oeNodeTime').value=node && node.time || '09:00'; el('oeNodeEndTime').value=node && node.end_time || ''; fillNodeOptions(node && node.ability_key); el('oeNodeLabel').value=node && node.ability_label || findOption(node && node.ability_key)[1]; el('oeNodeNote').value=node && node.note || ''; el('oeNodeModal').hidden=false; setTimeout(function(){el('oeNodeTime').focus();},60); }
  function closeNodeModal() { el('oeNodeModal').hidden=true; state.nodeEditIndex=-1; }
  function saveNodeFromModal() { var key=el('oeNodeKey').value, time=el('oeNodeTime').value, end=el('oeNodeEndTime').value, label=el('oeNodeLabel').value.trim() || findOption(key)[1], note=el('oeNodeNote').value.trim() || label; if (!/^\d{2}:\d{2}$/.test(time)) throw new Error('请选择开始时间'); var row={time:time,end:end,key:key,label:label,note:note}; var next=state.nodeEditIndex >= 0 ? Object.assign({},state.nodes[state.nodeEditIndex]) : {id:'wf_' + Date.now().toString(36),department_id:'sales',department_name:'销售部',sales_preset:isSalesTemplate(state.selectedTemplate)}; next.time=time; next.end_time=end; next.time_range=time + (end ? '-' + end : ''); next.ability_key=key; next.ability_label=label; next.note=note; next.plan=planForRow(row); if (state.nodeEditIndex >= 0) { var old=state.nodes[state.nodeEditIndex]; next.children=old.children || old.actions || []; } state.nodes=state.nodeEditIndex >= 0 ? state.nodes.map(function(item,index){return index === state.nodeEditIndex ? next : item;}) : state.nodes.concat(next); state.nodes.sort(function(a,b){return String(a.time).localeCompare(String(b.time));}); closeNodeModal(); renderEditor(); }
  function payloadToSave() { var name=(el('oeTemplateName').value || '').trim(); if (!name) throw new Error('请填写员工名称'); if (!state.nodes.length) throw new Error('请至少添加一个节点'); var meta=Object.assign({},state.editingMeta || {}); if (isSalesTemplate(state.selectedTemplate) || state.selectedId === 'system_sales') {meta.system_template_key='system_sales';meta.source=meta.source || 'system_mirror';} return {name:name,nodes:clone(state.nodes),meta:meta}; }
  function saveTemplate() { var body=payloadToSave(), id=String(state.editingId || ''); return api(id ? '/api/h5-workflows/templates/' + encodeURIComponent(id) : '/api/h5-workflows/templates',{method:id?'PATCH':'POST',json:body}).then(function(data){var saved=data.template || {}; state.editingId=String(saved.id || id); state.editingMeta=Object.assign({},saved.meta || body.meta); state.selectedId=String(saved.id || state.selectedId || 'system_sales'); return loadTemplates().then(function(){state.selectedTemplate=normalizeTemplate(state.templates.find(function(item){return String(item.id) === state.editingId;}) || Object.assign({},saved,{source:'own'})); state.nodes=clone(state.selectedTemplate.nodes || body.nodes); render(); if (typeof loadOnlineH5Employees === 'function') loadOnlineH5Employees(); return saved;}); }); }
  function askPlanDay() { var answer=window.prompt('请输入本次销售工作流从第几天开始执行（1-30）','1'); if (answer === null) return null; var day=Number(answer); if (!Number.isInteger(day) || day < 1 || day > 30) throw new Error('执行天数请输入 1 到 30 的整数'); return day; }
  function activateTemplate() { var iid=selectedDeviceId(), template=state.selectedTemplate; if (!iid) throw new Error('请先选择 Online 设备'); var device=state.devices.find(function(item){return String(item.installation_id) === iid;}); if (!device || !device.online) throw new Error('请选择在线的 Online 设备'); var day=isSalesTemplate(template) ? askPlanDay() : undefined; if (day === null) return Promise.resolve(); var request; if (state.selectedId === 'system_sales' && !state.editingId) request=api('/api/h5-workflows/activate-inline',{method:'POST',json:{template_key:'system_sales',name:'销售员工',nodes:clone(state.nodes),installation_id:iid,timezone_offset_minutes:-new Date().getTimezoneOffset(),plan_day:day}}); else { var id=String(state.editingId || ''); if (!id) return saveTemplate().then(activateTemplate); request=api('/api/h5-workflows/activate',{method:'POST',json:{template_id:Number(id),installation_id:iid,timezone_offset_minutes:-new Date().getTimezoneOffset(),...(day ? {plan_day:day} : {})}}); } return request.then(function(data){state.active=data.activation || null; renderStatus(); if (typeof toast === 'function') toast('员工工作流已启用');}); }
  function stopTemplate() { var active=activeForDevice(); if (!active || !active.id) throw new Error('当前设备没有启用员工'); return api('/api/h5-workflows/activations/' + encodeURIComponent(active.id) + '/stop',{method:'POST',json:{}}).then(function(){state.active=null;renderStatus();if(typeof toast==='function')toast('员工工作流已停用');}); }
  function bind(root) { if (root.dataset.oeBound) return; root.dataset.oeBound='1'; root.addEventListener('click',function(event){var target=event.target.closest('[data-oe-action],[data-oe-template],[data-oe-edit],[data-oe-delete]'); if(!target) return; try { if (target.dataset.oeTemplate != null) {selectTemplate(target.dataset.oeTemplate);return;} if (target.dataset.oeEdit != null) {openNodeModal(Number(target.dataset.oeEdit));return;} if (target.dataset.oeDelete != null) {if(window.confirm('删除这个工作流节点？')) {state.nodes.splice(Number(target.dataset.oeDelete),1);renderEditor();}return;} var action=target.dataset.oeAction; if(action==='refresh') {initialize(true);return;} if(action==='new') {resetNew();return;} if(action==='add') {openNodeModal(-1);return;} if(action==='close-modal') {closeNodeModal();return;} if(action==='save-node') {saveNodeFromModal();return;} if(action==='save') {saveTemplate().catch(function(err){showError(err.message);});return;} if(action==='activate') {activateTemplate().catch(function(err){showError(err.message);});return;} if(action==='stop') {stopTemplate().catch(function(err){showError(err.message);});return;} } catch(err) {showError(err.message || String(err));} }); root.addEventListener('change',function(event){if(event.target.id==='oeDeviceSelect') loadActive().catch(function(err){showError(err.message);});}); }
  function initialize(force) { var root=el('content-h5-employees'); if(!root) return; bind(root); clearError(); var requested=String(window.__onlineEmployeeSelectedId || '').trim(); if(requested) {state.selectedId=requested; window.__onlineEmployeeSelectedId='';} state.loading=true; Promise.all([loadTemplates(),loadDevices()]).then(function(){ if(!state.selectedId) state.selectedId='system_sales'; selectTemplate(state.selectedId); }).catch(function(err){showError(err.message || '员工数据加载失败'); if(!state.selectedId) {state.selectedId='system_sales';state.selectedTemplate={id:'system_sales',source:'system',name:'销售员工',meta:{system_template_key:'system_sales'},nodes:salesNodes()};state.nodes=clone(state.selectedTemplate.nodes);render();} }).finally(function(){state.loading=false;}); }
  window.initOnlineH5EmployeesView = function() { initialize(false); };
})();
