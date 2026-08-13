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
    childEditId: ''
  };

  var SALES_ROWS = [
    {time:'06:00',end:'06:30',key:'local_bestseller',label:'创作同城爆款视频',note:'创作一条同城爆款视频（用于发公域平台）',publish:[['08:45','douyin','同城爆款视频发布抖音'],['09:00','wechat_channels','同城爆款视频发布视频号']]},
    {time:'06:30',end:'07:00',key:'hifly.video.create_by_tts',label:'创作数字人口播视频',note:'创作一条数字人口播视频（用于发朋友圈）',publish:[['09:30','wechat_moments','微信朋友圈发布']]},
    {time:'07:00',end:'07:15',key:'native_wechat_add_friend',label:'微信自动加好友',note:'从抖音私信接管结果中提取明确微信号后加好友，没有明确微信号则跳过',params:{source_mode:'douyin_private_message_wechat_id',trigger:'clear_wechat_id',skip_without_clear_wechat_id:true}},
    {time:'07:15',end:'07:30',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'07:45',end:'08:15',key:'douyin_leads',label:'抖音自动养号',note:'抖音自动养号'},
    {time:'08:15',end:'08:45',key:'wechat_channels_nurture',label:'视频号自动养号（敬请期待）',note:'视频号自动养号',soon:true},
    {time:'09:15',end:'09:30',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管'},
    {time:'09:45',end:'10:00',key:'native_wechat_moments_engage',label:'微信朋友圈点赞评论',note:'微信朋友圈点赞评论'},
    {time:'10:00',end:'10:15',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'10:30',end:'11:00',key:'douyin_leads',label:'抖音自动养号',note:'抖音自动养号'},
    {time:'11:00',end:'11:30',key:'wechat_channels_nurture',label:'视频号自动养号（敬请期待）',note:'视频号自动养号',soon:true},
    {time:'11:30',end:'12:00',key:'douyin_leads',label:'抖音获客·关键词抓取精准客户',note:'抖音获客·关键词抓取精准客户'},
    {time:'12:00',end:'12:15',key:'douyin_leads',label:'抖音回复精准客户评论10个',note:'抖音回复精准客户评论10个'},
    {time:'12:15',end:'12:30',key:'douyin_leads',label:'抖音自己评论区接管',note:'抖音自己评论区接管，评论并@10个精准客户'},
    {time:'12:30',end:'12:45',key:'douyin_leads',label:'抖音关注精准客户并评论首条作品',note:'抖音关注10个精准客户，并找到他的首条作品去评论'},
    {time:'12:45',end:'13:00',key:'native_wechat_add_friend',label:'微信自动加好友',note:'从抖音私信接管结果中提取明确微信号后加好友，没有明确微信号则跳过',params:{source_mode:'douyin_private_message_wechat_id',trigger:'clear_wechat_id',skip_without_clear_wechat_id:true}},
    {time:'13:00',end:'13:15',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'13:30',end:'13:45',key:'native_wechat_moments_engage',label:'微信朋友圈自己评论区接管',note:'微信朋友圈自己评论区接管',params:{moment_action:'comment'}},
    {time:'13:45',end:'14:15',key:'hifly.video.create_by_tts',label:'创作数字人口播视频',note:'创作一条数字人口播视频（用于发朋友圈）',publish:[['14:15','wechat_moments','微信朋友圈发布']]},
    {time:'14:30',end:'14:45',key:'douyin_leads',label:'抖音主动私信精准客户',note:'抖音主动私信10个精准客户'},
    {time:'14:45',end:'15:00',key:'douyin_leads',label:'抖音私信接管',note:'抖音私信接管'},
    {time:'15:00',end:'15:15',key:'wechat_channels_comment',label:'视频号评论区接管（敬请期待）',note:'视频号评论区接管',soon:true},
    {time:'15:15',end:'15:30',key:'wechat_channels_message',label:'视频号私信接管（敬请期待）',note:'视频号私信接管',soon:true},
    {time:'15:30',end:'16:00',key:'douyin_leads',label:'抖音自动养号',note:'抖音自动养号'},
    {time:'16:00',end:'16:30',key:'wechat_channels_nurture',label:'视频号自动养号（敬请期待）',note:'视频号自动养号',soon:true},
    {time:'16:30',end:'16:45',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'17:00',end:'17:15',key:'native_wechat_moments_engage',label:'微信朋友圈点赞评论',note:'微信朋友圈点赞评论'},
    {time:'17:15',end:'17:30',key:'douyin_leads',label:'抖音获客·关键词抓取精准客户',note:'抖音获客·关键词抓取精准客户'},
    {time:'17:30',end:'17:45',key:'douyin_leads',label:'抖音回复精准客户评论10个',note:'抖音回复精准客户评论10个'},
    {time:'17:45',end:'18:00',key:'douyin_leads',label:'抖音自己评论区接管',note:'抖音自己评论区接管，评论并@10个精准客户'},
    {time:'18:00',end:'18:15',key:'douyin_leads',label:'抖音关注精准客户并评论首条作品',note:'抖音关注10个精准客户，并找到他的首条作品去评论'},
    {time:'18:15',end:'18:30',key:'native_wechat_add_friend',label:'微信自动加好友',note:'从抖音私信接管结果中提取明确微信号后加好友，没有明确微信号则跳过',params:{source_mode:'douyin_private_message_wechat_id',trigger:'clear_wechat_id',skip_without_clear_wechat_id:true}},
    {time:'18:30',end:'18:45',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
    {time:'19:00',end:'19:15',key:'douyin_leads',label:'抖音主动私信精准客户',note:'抖音主动私信10个精准客户'},
    {time:'19:15',end:'19:30',key:'douyin_leads',label:'抖音私信接管',note:'抖音私信接管'},
    {time:'19:30',end:'20:00',key:'hifly.video.create_by_tts',label:'创作数字人口播视频',note:'创作一条数字人口播视频（用于发朋友圈）',publish:[['20:00','wechat_moments','微信朋友圈发布']]},
    {time:'20:15',end:'20:30',key:'wechat_channels_comment',label:'视频号评论区接管（敬请期待）',note:'视频号评论区接管',soon:true},
    {time:'20:30',end:'20:45',key:'wechat_channels_message',label:'视频号私信接管（敬请期待）',note:'视频号私信接管',soon:true},
    {time:'20:45',end:'21:00',key:'native_wechat_add_friend',label:'微信自动加好友',note:'从抖音私信接管结果中提取明确微信号后加好友，没有明确微信号则跳过',params:{source_mode:'douyin_private_message_wechat_id',trigger:'clear_wechat_id',skip_without_clear_wechat_id:true}},
    {time:'21:00',end:'22:00',key:'native_wechat_poll',label:'微信私信接管',note:'微信私信接管',params:{group_invite_enabled:true,group_invite_rule_status:'pending_rules',trigger:'qualified_intent'}},
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

  var CHILD_ACTION_OPTIONS = [
    ['publish','发布内容'],
    ['native_wechat_add_friend','自动加好友'],
    ['native_wechat_moments_engage','朋友圈点赞评论']
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
  function isSalesTemplate(template) { return String(state.selectedId) === 'system_sales' || activeTemplateKey(template) === 'system_sales'; }
  function templateNeedsPlanDay(template) { return isSalesTemplate(template) || (state.nodes.length ? state.nodes : (template && template.nodes || [])).some(function(node) { var payload=node && node.plan && node.plan.payload || {}; return String(node && node.ability_key || '') === 'local_bestseller' || String(payload.action || '') === 'local_bestseller_daily_video'; }); }
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
    if (row.key === 'ip_content_daily') return {title:'IP日更文案',task_kind:'ip_content_daily',content:'H5 工作流：IP日更文案',payload:{template_id:0,use_personal_default:true,tasks:['industry_hot_oral','professional_ip_oral','moments_candidate'],sync_before:true,industry_count:5,ip_count:5,moments_count:20,requirements:{}}};
    var action = salesAction(prompt), max = action === 'search_collect' || action === 'account_nurture' ? 50 : 10;
    return {title:'抖音获客 - ' + prompt.slice(0,24),task_kind:'douyin_leads',content:'H5 工作流：抖音获客',payload:{action:'search_collect',params:baseScheduleParams(row,{keyword:prompt,query:prompt,search_keyword:prompt,sales_action:action,max_results:max,max_users:max,regions:['全国'],mode:'script'})}};
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
    var text=[node && node.ability_label,node && node.note].map(function(value){return String(value || '');}).join(' ');
    return !!node && String(node.ability_key || '') === 'douyin_leads' && text.indexOf('私信接管') >= 0;
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
    if (isDouyinPrivate(parent)) values.push('native_wechat_add_friend');
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
    var platform=String(form.platform || existing && existing.platform || 'douyin').trim();
    var id=String(existing && existing.id || ('wf_action_' + Date.now().toString(36) + '_' + Math.random().toString(16).slice(2,8)));
    if (type === 'publish') {
      var publish=publishChild(parent,[time,platform,'发布' + childPlatformLabel(platform)],0);
      publish.id=id;
      publish.time=time;
      return publish;
    }
    var keys={native_wechat_add_friend:'native_wechat_add_friend',native_wechat_moments_engage:'native_wechat_moments_engage'};
    var key=keys[type];
    if (!key) throw new Error('不支持的下级动作');
    var label=childActionLabel({action_type:type});
    var extra={source_workflow_node_id:String(parent.id || ''),source_workflow_node_label:String(parent.ability_label || parent.note || '')};
    if (type === 'native_wechat_add_friend') Object.assign(extra,{source_mode:'douyin_private_message_phone',trigger:'clear_mobile',skip_without_clear_mobile:true,targets:[]});
    if (type === 'native_wechat_moments_engage') Object.assign(extra,{targets:[],moment_action:'like_comment',max_scrolls:6});
    var row={time:time,end:'',key:key,label:label,note:label,params:{}};
    return Object.assign({},existing || {},{id:id,time:time,parent_node_id:String(parent.id || ''),action_type:type,type:type,platform:'',ability_key:key,ability_label:label,department_id:parent.department_id || '',department_name:parent.department_name || '',note:label,is_action_node:true,param_configured:true,plan:nativePlan(key,row,extra)});
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
  function normalizeTemplate(template) { var out = Object.assign({}, template || {}); out.nodes = migrateGroupInviteNodes(Array.isArray(out.nodes) ? clone(out.nodes) : []); out.meta = out.meta && typeof out.meta === 'object' ? Object.assign({},out.meta) : {}; return out; }
  function ownSalesMirror() { return state.templates.find(function(item) { return activeTemplateKey(item) === 'system_sales'; }); }
  function templateForSelected() { if (state.selectedId === 'system_sales') return ownSalesMirror() || {id:'system_sales',source:'system',name:'销售员工',meta:{system_template_key:'system_sales'},nodes:salesNodes()}; return state.templates.find(function(item) { return String(item.id) === String(state.selectedId); }) || null; }
  function templateIsEditable(template) { return !!(template && template.source === 'own'); }
  function currentTemplateIsEditable() { return state.selectedId === 'system_sales' || templateIsEditable(state.selectedTemplate); }
  function requireEditableTemplate() { if (!currentTemplateIsEditable()) throw new Error('授权模板为只读配置，不能修改'); }
  function activeForDevice() { var iid = selectedDeviceId(); return state.active && String(state.active.installation_id || '') === iid ? state.active : null; }
  function renderDevices() {
    var select = el('oeDeviceSelect'); if (!select) return;
    var current = select.value;
    select.innerHTML = '<option value="">请选择 Online 设备</option>' + state.devices.map(function(device) { var id=String(device.installation_id || ''); var name=String(device.display_name || id.slice(0,12) || '未命名设备'); return '<option value="' + esc(id) + '">' + esc(name) + (device.online ? ' · 在线' : ' · 离线') + '</option>'; }).join('');
    if (current && state.devices.some(function(device) { return String(device.installation_id) === current; })) select.value = current;
    else { var online = state.devices.find(function(device) { return device.online; }); if (online) select.value = online.installation_id; }
    select.disabled = !!state.submitting;
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
  function childHtml(child,parentIndex,editable) { var soon=child.comingSoon || child.workflow_placeholder || (child.plan && child.plan.payload && child.plan.payload.skip_execution); return '<div class="oe-child"><span class="oe-child-time">' + esc(child.time || '--:--') + '</span><span class="oe-child-copy">' + esc(child.ability_label || childActionLabel(child)) + (soon ? '<small>视频号功能敬请期待</small>' : '<small>' + esc(child.note || childActionLabel(child)) + '</small>') + '</span><span class="oe-tag child">下一级</span>' + (editable && !soon ? '<span class="oe-child-actions"><button type="button" class="oe-mini-btn" data-oe-child-edit="' + esc(child.id || '') + '" data-oe-child-parent="' + parentIndex + '">编辑</button><button type="button" class="oe-mini-btn" data-oe-child-delete="' + esc(child.id || '') + '" data-oe-child-parent="' + parentIndex + '">删除</button></span>' : '') + '</div>'; }
  function nodeHtml(node,index,editable) { var soon=!!(node.comingSoon || node.workflow_placeholder || node.plan && node.plan.payload && node.plan.payload.skip_execution); var children=workflowChildren(node); return '<article class="oe-node' + (soon ? ' is-soon' : '') + '"><div class="oe-time">' + esc(node.time || '--:--') + (node.end_time ? '<br><span style="color:#a0aaba;font-size:.61rem;font-weight:400">' + esc(node.end_time) + '</span>' : '') + '</div><div class="oe-line"></div><div class="oe-node-main"><div class="oe-node-title"><span>' + esc(node.ability_label || node.note || '工作节点') + '</span>' + (soon ? '<span class="oe-tag soon">敬请期待</span>' : '') + (node.sales_preset ? '<span class="oe-tag">销售</span>' : '') + '</div><div class="oe-node-note">' + esc(node.note || '') + '</div><div class="oe-node-key">' + esc(node.ability_key || '') + '</div></div><div class="oe-node-actions">' + (editable && !soon ? '<button type="button" class="oe-mini-btn" data-oe-child-add="' + index + '">添加下级</button><button type="button" class="oe-mini-btn" data-oe-edit="' + index + '">编辑</button><button type="button" class="oe-mini-btn" data-oe-delete="' + index + '">删除</button>' : '') + '</div>' + (children.length ? '<div class="oe-children">' + children.map(function(child){return childHtml(child,index,editable);}).join('') + '</div>' : '') + '</article>'; }
  function renderTimeline() { var host=el('oeTimeline'); if (!host) return; var editable=currentTemplateIsEditable(), count=state.nodes.length, childCount=state.nodes.reduce(function(total,node){return total+workflowChildren(node).length;},0); el('oeTimelineMeta').textContent=count + ' 个节点' + (childCount ? ' · ' + childCount + ' 个下级动作' : ''); host.innerHTML=count ? state.nodes.map(function(node,index){return nodeHtml(node,index,editable);}).join('') : '<div class="oe-empty-list">还没有节点，点击“添加节点”开始配置。</div>'; }
  function renderEditor() {
    var body=el('oeEditorBody'), empty=el('oeEditorEmpty'), template=state.selectedTemplate; if (!template) { body.hidden=true; empty.hidden=false; return; }
    body.hidden=false; empty.hidden=true; el('oeEditorTitle').textContent=template.name || '未命名员工'; el('oeEditorSubtitle').textContent=isSalesTemplate(template) ? '销售 24 小时工作流 · 复用 H5 销售逻辑' : (template.source === 'granted' ? '授权模板 · 只读配置' : '自定义工作流'); el('oeTemplateName').value=template.name || ''; renderStatus(); renderTimeline();
    var editable=currentTemplateIsEditable(); el('oeTemplateName').disabled=!editable; el('oeTemplateName').title=editable ? '' : '授权模板不能修改';
    document.querySelectorAll('#content-h5-employees [data-oe-action="save"],#content-h5-employees [data-oe-action="add"]').forEach(function(button){button.disabled=!editable || !!state.submitting;});
    document.querySelectorAll('#content-h5-employees [data-oe-action="activate"],#content-h5-employees [data-oe-action="stop"]').forEach(function(button){button.disabled=!!state.submitting;});
  }
  function render() { renderDevices(); renderList(); renderEditor(); }
  function loadTemplates() { return api('/api/h5-workflows/templates').then(function(data){ state.templates=(Array.isArray(data.templates) ? data.templates : []).map(normalizeTemplate); return state.templates; }); }
  function loadDevices() { return api('/api/h5-chat/devices/status').then(function(data){state.devices=Array.isArray(data.devices) ? data.devices : []; return state.devices;}); }
  function loadActive() { var iid=selectedDeviceId(); if (!iid) { state.active=null; renderStatus(); return Promise.resolve(null); } return api('/api/h5-workflows/active?installation_id=' + encodeURIComponent(iid)).then(function(data){state.active=data.activation || null; renderStatus(); return state.active;}); }
  function selectTemplate(id) { state.selectedId=String(id || 'system_sales'); state.selectedTemplate=normalizeTemplate(templateForSelected()); state.editingId=state.selectedTemplate.source === 'own' ? String(state.selectedTemplate.id || '') : ''; state.editingMeta=Object.assign({},state.selectedTemplate.meta || {}); state.nodes=clone(state.selectedTemplate.nodes || []); if (state.selectedId === 'system_sales' && !ownSalesMirror()) state.nodes=salesNodes(); render(); loadActive().catch(function(){}); }
  function resetNew() { state.selectedId=''; state.selectedTemplate={id:'',source:'own',name:'新员工',nodes:[],meta:{}}; state.editingId=''; state.editingMeta={}; state.nodes=[]; render(); }
  function findOption(key) { return NODE_OPTIONS.find(function(item){return item[0] === key;}) || NODE_OPTIONS[0]; }
  function fillNodeOptions(selected) { var select=el('oeNodeKey'); if (!select) return; select.innerHTML=NODE_OPTIONS.map(function(item){return '<option value="' + esc(item[0]) + '">' + esc(item[1]) + '</option>';}).join(''); select.value=selected || NODE_OPTIONS[0][0]; }
  function syncNodeModalFields() { var takeover=String((el('oeNodeKey') || {}).value || '') === 'native_wechat_poll', field=el('oeNodeGroupInviteField'); if (field) field.hidden=!takeover; }
  function openNodeModal(index) { requireEditableTemplate(); state.nodeEditIndex=typeof index === 'number' ? index : -1; var node=state.nodeEditIndex >= 0 ? state.nodes[state.nodeEditIndex] : null, params=workflowPayload(node).params || {}; el('oeNodeModalTitle').textContent=node ? '编辑工作流节点' : '添加工作流节点'; el('oeNodeTime').value=node && node.time || '09:00'; el('oeNodeEndTime').value=node && node.end_time || ''; fillNodeOptions(node && node.ability_key); el('oeNodeGroupInviteEnabled').checked=!!params.group_invite_enabled; syncNodeModalFields(); el('oeNodeLabel').value=node && node.ability_label || findOption(node && node.ability_key)[1]; el('oeNodeNote').value=node && node.note || ''; el('oeNodeModal').hidden=false; setTimeout(function(){el('oeNodeTime').focus();},60); }
  function closeNodeModal() { el('oeNodeModal').hidden=true; state.nodeEditIndex=-1; }
  function saveNodeFromModal() { requireEditableTemplate(); var key=el('oeNodeKey').value, time=el('oeNodeTime').value, end=el('oeNodeEndTime').value, label=el('oeNodeLabel').value.trim() || findOption(key)[1], note=el('oeNodeNote').value.trim() || label; if (!/^\d{2}:\d{2}$/.test(time)) throw new Error('请选择开始时间'); var existing=state.nodeEditIndex >= 0 ? state.nodes[state.nodeEditIndex] : null, existingParams=workflowPayload(existing).params || {}, row={time:time,end:end,key:key,label:label,note:note,params:Object.assign({},existingParams,{group_invite_enabled:key === 'native_wechat_poll' && !!el('oeNodeGroupInviteEnabled').checked})}; delete row.params.followup_action; delete row.params.group_invite_rules; var next=existing ? Object.assign({},existing) : {id:'wf_' + Date.now().toString(36),department_id:'sales',department_name:'销售部',sales_preset:isSalesTemplate(state.selectedTemplate)}; next.time=time; next.end_time=end; next.time_range=time + (end ? '-' + end : ''); next.ability_key=key; next.ability_label=label; next.note=note; next.plan=planForRow(row); if (existing) next.children=existing.children || existing.actions || []; state.nodes=state.nodeEditIndex >= 0 ? state.nodes.map(function(item,index){return index === state.nodeEditIndex ? next : item;}) : state.nodes.concat(next); state.nodes.sort(function(a,b){return String(a.time).localeCompare(String(b.time));}); closeNodeModal(); renderEditor(); }
  function fillChildOptions(parent,selected) { var select=el('oeChildType'), options=childOptions(parent,selected); if (!select) return; select.innerHTML=options.map(function(item){return '<option value="' + esc(item[0]) + '">' + esc(item[1]) + '</option>';}).join(''); select.value=options.some(function(item){return item[0] === selected;}) ? selected : (options[0] && options[0][0] || 'publish'); }
  function syncChildModalFields() { var publish=String(el('oeChildType') && el('oeChildType').value || 'publish') === 'publish', field=el('oeChildPlatformField'); if (field) field.hidden=!publish; if (el('oeChildPlatform')) el('oeChildPlatform').disabled=!publish; }
  function openChildModal(parentIndex,childId) { requireEditableTemplate(); var index=Number(parentIndex), parent=state.nodes[index]; if (!parent) throw new Error('未找到上级节点'); var existing=workflowChildren(parent).find(function(child){return String(child && child.id || '') === String(childId || '');}) || null; state.childParentIndex=index; state.childEditId=existing ? String(existing.id || '') : ''; el('oeChildModalTitle').textContent=existing ? '编辑下级动作' : '添加下级动作'; el('oeChildParent').textContent=parent.ability_label || parent.note || '上级节点'; el('oeChildTime').value=existing && existing.time || parent.end_time || parent.time || '09:00'; fillChildOptions(parent,existing ? childActionType(existing) : ''); el('oeChildPlatform').value=existing && existing.platform || 'douyin'; syncChildModalFields(); el('oeChildModal').hidden=false; setTimeout(function(){el('oeChildTime').focus();},60); }
  function closeChildModal() { if (el('oeChildModal')) el('oeChildModal').hidden=true; state.childParentIndex=-1; state.childEditId=''; }
  function saveChildFromModal() { requireEditableTemplate(); var parentIndex=state.childParentIndex, parent=state.nodes[parentIndex]; if (!parent) throw new Error('未找到上级节点'); var editId=String(state.childEditId || ''), time=String(el('oeChildTime').value || '').trim(), type=String(el('oeChildType').value || 'publish').trim(), platform=String(el('oeChildPlatform').value || 'douyin').trim(); if (!/^\d{2}:\d{2}$/.test(time)) throw new Error('请选择动作时间'); if (childOptions(parent,editId ? childActionType(workflowChildren(parent).find(function(child){return String(child.id || '') === editId;})) : '').map(function(item){return item[0];}).indexOf(type) < 0) throw new Error('这个上级节点不支持所选动作'); if (type === 'publish' && ['douyin','toutiao','wechat_channels','wechat_moments'].indexOf(platform) < 0) throw new Error('暂时只支持抖音、头条、视频号和朋友圈'); var children=workflowChildren(parent).slice(), duplicate=children.find(function(child){if (editId && String(child.id || '') === editId) return false; var childType=childActionType(child); return type === 'publish' ? childType === 'publish' && String(child.platform || '') === platform : childType === type;}); if (duplicate) throw new Error(type === 'publish' ? '这个平台已经有发布动作了' : '这个下级动作已经添加过了'); var existing=editId ? children.find(function(child){return String(child.id || '') === editId;}) : null, next=buildWorkflowChild(parent,{time:time,action_type:type,platform:platform},existing); children=children.filter(function(child){return String(child.id || '') !== String(next.id || '');}).concat(next).sort(function(a,b){return String(a.time || '').localeCompare(String(b.time || ''));}); state.nodes[parentIndex]=syncParentChildRules(Object.assign({},parent,{children:children})); closeChildModal(); renderEditor(); }
  function removeChild(parentIndex,childId) { requireEditableTemplate(); var index=Number(parentIndex), parent=state.nodes[index]; if (!parent) return; var children=workflowChildren(parent).filter(function(child){return String(child && child.id || '') !== String(childId || '');}); state.nodes[index]=syncParentChildRules(Object.assign({},parent,{children:children})); renderEditor(); }
  function payloadToSave() { requireEditableTemplate(); state.nodes=migrateGroupInviteNodes(state.nodes); var name=(el('oeTemplateName').value || '').trim(); if (!name) throw new Error('请填写员工名称'); if (!state.nodes.length) throw new Error('请至少添加一个节点'); var meta=Object.assign({},state.editingMeta || {}); if (isSalesTemplate(state.selectedTemplate)) {meta.system_template_key='system_sales';meta.source=meta.source || 'system_mirror';} return {name:name,nodes:clone(state.nodes),meta:meta}; }
  function runSubmission(kind, task) { if (state.submitting) return Promise.reject(new Error('操作正在处理中，请勿重复提交')); state.submitting=kind; clearError(); render(); return Promise.resolve().then(task).finally(function(){state.submitting='';render();}); }
  function saveTemplate() { var body=payloadToSave(), id=String(state.editingId || ''); return runSubmission('save',function(){return api(id ? '/api/h5-workflows/templates/' + encodeURIComponent(id) : '/api/h5-workflows/templates',{method:id?'PATCH':'POST',json:body}).then(function(data){var saved=data.template || {}; state.editingId=String(saved.id || id); state.editingMeta=Object.assign({},saved.meta || body.meta); state.selectedId=String(saved.id || state.selectedId || 'system_sales'); return loadTemplates().then(function(){state.selectedTemplate=normalizeTemplate(state.templates.find(function(item){return String(item.id) === state.editingId;}) || Object.assign({},saved,{source:'own'})); state.nodes=clone(state.selectedTemplate.nodes || body.nodes); render(); if (typeof loadOnlineH5Employees === 'function') loadOnlineH5Employees(); return saved;});});}); }
  function askPlanDay() { var answer=window.prompt('请输入本次销售工作流从第几天开始执行（1-30）','1'); if (answer === null) return null; var day=Number(answer); if (!Number.isInteger(day) || day < 1 || day > 30) throw new Error('执行天数请输入 1 到 30 的整数'); return day; }
  function activateTemplate() { var iid=selectedDeviceId(), template=state.selectedTemplate; if (!iid) throw new Error('请先选择 Online 设备'); var device=state.devices.find(function(item){return String(item.installation_id) === iid;}); if (!device || !device.online) throw new Error('请选择在线的 Online 设备'); var day=templateNeedsPlanDay(template) ? askPlanDay() : undefined; if (day === null) return Promise.resolve(); var requestFactory; if (state.selectedId === 'system_sales' && !state.editingId) requestFactory=function(){return api('/api/h5-workflows/activate-inline',{method:'POST',json:{template_key:'system_sales',name:'销售员工',nodes:clone(state.nodes),installation_id:iid,timezone_offset_minutes:-new Date().getTimezoneOffset(),plan_day:day}});}; else { var id=String(state.editingId || template && template.id || ''); if (!id) return saveTemplate().then(activateTemplate); requestFactory=function(){return api('/api/h5-workflows/activate',{method:'POST',json:{template_id:Number(id),installation_id:iid,timezone_offset_minutes:-new Date().getTimezoneOffset(),...(day ? {plan_day:day} : {})}});}; } return runSubmission('activate',requestFactory).then(function(data){state.active=data.activation || null; renderStatus(); if (typeof toast === 'function') toast('员工工作流已启用');}); }
  function stopTemplate() { var active=activeForDevice(); if (!active || !active.id) throw new Error('当前设备没有启用员工'); return runSubmission('stop',function(){return api('/api/h5-workflows/activations/' + encodeURIComponent(active.id) + '/stop',{method:'POST',json:{}}).then(function(){state.active=null;if(typeof toast==='function')toast('员工工作流已停用');});}); }
  function bind(root) {
    if (root.dataset.oeBound) return;
    root.dataset.oeBound='1';
    root.addEventListener('click',function(event){
      var target=event.target.closest('[data-oe-action],[data-oe-template],[data-oe-edit],[data-oe-delete],[data-oe-child-add],[data-oe-child-edit],[data-oe-child-delete]');
      if(!target || state.submitting) return;
      try {
        if (target.dataset.oeTemplate != null) {selectTemplate(target.dataset.oeTemplate);return;}
        if (target.dataset.oeChildAdd != null) {openChildModal(Number(target.dataset.oeChildAdd),'');return;}
        if (target.dataset.oeChildEdit != null) {openChildModal(Number(target.dataset.oeChildParent),target.dataset.oeChildEdit);return;}
        if (target.dataset.oeChildDelete != null) {if(window.confirm('删除这个下级动作？')) removeChild(Number(target.dataset.oeChildParent),target.dataset.oeChildDelete);return;}
        if (target.dataset.oeEdit != null) {openNodeModal(Number(target.dataset.oeEdit));return;}
        if (target.dataset.oeDelete != null) {requireEditableTemplate();if(window.confirm('删除这个工作流节点？')) {state.nodes.splice(Number(target.dataset.oeDelete),1);renderEditor();}return;}
        var action=target.dataset.oeAction;
        if(action==='refresh') {initialize(true);return;}
        if(action==='new') {resetNew();return;}
        if(action==='add') {openNodeModal(-1);return;}
        if(action==='close-modal') {closeNodeModal();return;}
        if(action==='save-node') {saveNodeFromModal();return;}
        if(action==='close-child-modal') {closeChildModal();return;}
        if(action==='save-child') {saveChildFromModal();return;}
        if(action==='save') {saveTemplate().catch(function(err){showError(err.message);});return;}
        if(action==='activate') {activateTemplate().catch(function(err){showError(err.message);});return;}
        if(action==='stop') {stopTemplate().catch(function(err){showError(err.message);});return;}
      } catch(err) {showError(err.message || String(err));}
    });
    root.addEventListener('change',function(event){
      if(event.target.id==='oeDeviceSelect') loadActive().catch(function(err){showError(err.message);});
      if(event.target.id==='oeNodeKey') syncNodeModalFields();
      if(event.target.id==='oeChildType') syncChildModalFields();
    });
  }
  function initialize(force) { var root=el('content-h5-employees'); if(!root) return; bind(root); clearError(); var requested=String(window.__onlineEmployeeSelectedId || '').trim(); if(requested) {state.selectedId=requested; window.__onlineEmployeeSelectedId='';} state.loading=true; Promise.all([loadTemplates(),loadDevices()]).then(function(){ if(!state.selectedId) state.selectedId='system_sales'; selectTemplate(state.selectedId); }).catch(function(err){showError(err.message || '员工数据加载失败'); if(!state.selectedId) {state.selectedId='system_sales';state.selectedTemplate={id:'system_sales',source:'system',name:'销售员工',meta:{system_template_key:'system_sales'},nodes:salesNodes()};state.nodes=clone(state.selectedTemplate.nodes);render();} }).finally(function(){state.loading=false;}); }
  window.initOnlineH5EmployeesView = function() { initialize(false); };
})();
