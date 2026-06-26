/* global API_BASE, LOCAL_API_BASE, authHeaders, escapeHtml */

(function () {
  'use strict';

  var POLL_MS = 5000;
  var REFRESH_DEBOUNCE_MS = 1200;
  var SWARM_PAGE_SIZE = 10;

  var state = {
    loadedSubUsers: false,
    timer: null,
    inFlight: false,
    lastRefreshAt: 0,
    lastFingerprints: {
      summary: '',
      swarm: '',
      timeline: '',
      result: ''
    },
    destroyed: false,
    selectedSwarmId: '',
    lastSnapshot: null,
    swarmVisibleCount: SWARM_PAGE_SIZE
  };

  function localBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE)
      ? String(LOCAL_API_BASE).replace(/\/$/, '')
      : ((typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '');
  }

  function h(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(value == null ? '' : String(value));
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function headers() {
    return Object.assign({}, typeof authHeaders === 'function' ? authHeaders() : {});
  }

  function el(id) {
    return document.getElementById(id);
  }

  function text(id, value) {
    var node = el(id);
    if (node) node.textContent = value == null ? '' : String(value);
  }

  function html(id, value, preserveScroll) {
    var node = el(id);
    if (!node) return;
    var next = value == null ? '' : String(value);
    if (node.innerHTML === next) return;
    var scrollTop = preserveScroll ? node.scrollTop : 0;
    var scrollLeft = preserveScroll ? node.scrollLeft : 0;
    node.innerHTML = next;
    if (preserveScroll) {
      node.scrollTop = scrollTop;
      node.scrollLeft = scrollLeft;
    }
  }

  function hasMeaningfulText(value) {
    var out = compactText(value, '');
    if (!out) return false;
    var lower = out.toLowerCase();
    return lower !== 'null' && lower !== 'undefined' && lower !== '[object object]';
  }

  function clampPercent(value) {
    var num = Number(value);
    if (!isFinite(num)) return 0;
    if (num < 0) return 0;
    if (num > 100) return 100;
    return Math.round(num);
  }

  function setProgress(percent) {
    var pct = clampPercent(percent);
    var bar = el('agentCurrentProgressBar');
    if (bar) bar.style.width = pct + '%';
    text('agentCurrentProgressText', pct + '%');
  }

  function parseJsonResponse(resp) {
    return resp.text().then(function (raw) {
      var data = {};
      try { data = raw ? JSON.parse(raw) : {}; } catch (e) {}
      if (!resp.ok) {
        throw new Error((data && (data.detail || data.message)) || raw || ('HTTP ' + resp.status));
      }
      return data;
    });
  }

  function fetchJson(path) {
    var base = localBase();
    if (!base) return Promise.reject(new Error('本地服务地址未配置'));
    return fetch(base + path, { headers: headers() }).then(parseJsonResponse);
  }

  function fmtTime(value) {
    if (!value) return '';
    var d = new Date(value);
    if (isNaN(d.getTime())) return String(value).replace('T', ' ').slice(0, 19);
    var mm = String(d.getMonth() + 1).padStart(2, '0');
    var dd = String(d.getDate()).padStart(2, '0');
    var hh = String(d.getHours()).padStart(2, '0');
    var mi = String(d.getMinutes()).padStart(2, '0');
    var ss = String(d.getSeconds()).padStart(2, '0');
    return mm + '/' + dd + ' ' + hh + ':' + mi + ':' + ss;
  }

  function relativeTime(value) {
    if (!value) return '';
    var ts = Date.parse(value);
    if (!ts) return '';
    var diff = Date.now() - ts;
    if (diff < 0) diff = 0;
    var sec = Math.floor(diff / 1000);
    if (sec < 60) return sec + ' 秒前';
    var min = Math.floor(sec / 60);
    if (min < 60) return min + ' 分钟前';
    var hour = Math.floor(min / 60);
    if (hour < 24) return hour + ' 小时前';
    return Math.floor(hour / 24) + ' 天前';
  }

  function isRunningStatus(status) {
    var s = String(status || '').trim().toLowerCase();
    return ['pending', 'processing', 'running', 'queued', 'waiting', 'claimed', 'in_progress'].indexOf(s) >= 0;
  }

  function statusLabel(status) {
    var s = String(status || '').trim().toLowerCase();
    return {
      pending: '等待中',
      processing: '执行中',
      running: '执行中',
      queued: '排队中',
      waiting: '等待中',
      completed: '已完成',
      failed: '失败',
      cancelled: '已取消',
      claimed: '已接单',
      in_progress: '执行中'
    }[s] || (status || '处理中');
  }

  function messageStatusLabel(status) {
    var s = String(status || '').trim().toLowerCase();
    return {
      pending: '等待接单',
      processing: '语音处理中',
      completed: '语音处理完成',
      failed: '语音处理失败',
      cancelled: '已取消'
    }[s] || (status || '处理中');
  }

  function isSkippedRun(run) {
    var rp = run && run.resultPayload && typeof run.resultPayload === 'object' ? run.resultPayload : {};
    return !!(rp && rp.skipped);
  }

  function capabilityLabel(run) {
    var payload = run && run.payload && typeof run.payload === 'object' ? run.payload : {};
    var capabilityId = String(payload.capability_id || '').trim();
    var action = String((((run || {}).result_payload || {}).action) || (payload.action || '')).trim();
    if (action) {
      return {
        search_collect: '采集客户',
        tasks_from_search: '同步任务池',
        comment_collect: '评论采集',
        interaction: '精准私信',
        stranger_collect: '陌生人私信采集',
        stranger_send: '陌生人私信发送'
      }[action] || action;
    }
    return {
      'goal.video.pipeline': '创意视频',
      'goal.image.pipeline': '图片创作',
      'create.video.pipeline': '视频生成',
      'create.ppt.pipeline': 'PPT 生成',
      'hifly.video.create_by_tts': '数字人口播'
    }[capabilityId] || capabilityId || String(run && run.task_kind || '任务');
  }

  function isInstructionalResultText(value) {
    var text = compactText(value, '');
    if (!text) return false;
    return text.indexOf('统计说明') >= 0
      || text.indexOf('上报口径') >= 0
      || text.indexOf('精准客户\t') >= 0
      || text.indexOf('关注/评论客户') >= 0
      || text.indexOf('私信客户') >= 0
      || text.indexOf('视频评论数') >= 0;
  }

  function compactText(value, fallback) {
    var out = String(value || '').replace(/\s+/g, ' ').trim();
    return out || (fallback || '');
  }

  function isHttpUrl(value) {
    return /^https?:\/\//i.test(String(value || '').trim());
  }

  function mediaTypeFromUrl(url) {
    var clean = String(url || '').split('?')[0].toLowerCase();
    if (/\.(png|jpe?g|webp|gif|bmp)$/i.test(clean)) return 'image';
    if (/\.(mp4|webm|mov|m4v|avi)$/i.test(clean)) return 'video';
    return '';
  }

  function pushUniqueUrl(list, seen, url, type) {
    var clean = String(url || '').trim();
    if (!clean || !isHttpUrl(clean) || seen[clean]) return;
    seen[clean] = true;
    list.push({ url: clean, type: type || mediaTypeFromUrl(clean) || '' });
  }

  function extractRunMedia(run) {
    var payload = run && run.resultPayload && typeof run.resultPayload === 'object' ? run.resultPayload : {};
    var list = [];
    var seen = {};
    var media = payload.media_urls;
    if (Array.isArray(media)) {
      media.forEach(function (url) { pushUniqueUrl(list, seen, url, ''); });
    } else if (media && typeof media === 'object') {
      (Array.isArray(media.image) ? media.image : []).forEach(function (url) { pushUniqueUrl(list, seen, url, 'image'); });
      (Array.isArray(media.video) ? media.video : []).forEach(function (url) { pushUniqueUrl(list, seen, url, 'video'); });
    }
    var refs = payload.result_refs;
    if (refs && typeof refs === 'object') {
      (Array.isArray(refs.urls) ? refs.urls : []).forEach(function (url) { pushUniqueUrl(list, seen, url, ''); });
    }
    var generated = payload.generated;
    if (generated && typeof generated === 'object') {
      var gmedia = generated.media_urls;
      if (Array.isArray(gmedia)) {
        gmedia.forEach(function (url) { pushUniqueUrl(list, seen, url, ''); });
      } else if (gmedia && typeof gmedia === 'object') {
        (Array.isArray(gmedia.image) ? gmedia.image : []).forEach(function (url) { pushUniqueUrl(list, seen, url, 'image'); });
        (Array.isArray(gmedia.video) ? gmedia.video : []).forEach(function (url) { pushUniqueUrl(list, seen, url, 'video'); });
      }
    }
    return list;
  }

  function pickPrimaryMedia(run) {
    var capabilityId = String((((run || {}).payload || {}).capability_id) || '').trim();
    var media = extractRunMedia(run);
    var want = '';
    var i;
    if (!media.length) return null;
    if (capabilityId === 'goal.image.pipeline') want = 'image';
    else if (capabilityId === 'goal.video.pipeline' || capabilityId === 'create.video.pipeline') want = 'video';
    if (want) {
      for (i = media.length - 1; i >= 0; i -= 1) {
        if (media[i].type === want) return media[i];
      }
    }
    return media[media.length - 1] || null;
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function firstMeaningful() {
    var i;
    for (i = 0; i < arguments.length; i += 1) {
      if (hasMeaningfulText(arguments[i])) return String(arguments[i]).trim();
    }
    return '';
  }

  function resultPayloadRoot(run) {
    return run && run.resultPayload && typeof run.resultPayload === 'object' ? run.resultPayload : {};
  }

  function resultPayloadMcp(run) {
    var root = resultPayloadRoot(run);
    return root.mcp_result && typeof root.mcp_result === 'object' ? root.mcp_result : {};
  }

  function resultPayloadSelectedVideo(run) {
    var root = resultPayloadRoot(run);
    var mcp = resultPayloadMcp(run);
    if (root.selected_video && typeof root.selected_video === 'object') return root.selected_video;
    if (mcp.selected_video && typeof mcp.selected_video === 'object') return mcp.selected_video;
    return {};
  }

  function resultPayloadCustomers(run) {
    var root = resultPayloadRoot(run);
    var mcp = resultPayloadMcp(run);
    var selectedVideo = resultPayloadSelectedVideo(run);
    var lists = [
      root.precise_customers,
      root.high_intent_users,
      selectedVideo.precise_customers,
      selectedVideo.high_intent_users,
      mcp.precise_customers,
      mcp.high_intent_users
    ];
    var seen = {};
    var rows = [];
    lists.forEach(function (list) {
      asArray(list).forEach(function (item) {
        if (!item || typeof item !== 'object') return;
        var key = [
          firstMeaningful(item.comment_id, item.cid, item.id),
          firstMeaningful(item.sec_user_id, item.sec_uid, item.user_id),
          firstMeaningful(item.nickname, item.user_name, item.display_name),
          firstMeaningful(item.comment_text, item.comment, item.text, item.content)
        ].join('||');
        if (seen[key]) return;
        seen[key] = true;
        rows.push({
          nickname: firstMeaningful(item.nickname, item.username, item.user_name, item.display_name, item.name, '未命名客户'),
          commentText: firstMeaningful(item.comment_text, item.comment, item.text, item.content),
          reason: firstMeaningful(item.intent_reason, item.reason, item.ai_reason, item.summary),
          score: firstMeaningful(item.score, item.intent_score, item.confidence),
          profileUrl: firstMeaningful(item.profile_url, item.homepage, item.user_profile_url),
          sourceVideoTitle: firstMeaningful(item.video_title, item.source_video_title, item.aweme_title),
          sourceVideoUrl: firstMeaningful(item.video_url, item.source_video_url, item.aweme_url)
        });
      });
    });
    return rows;
  }

  function resultPayloadSummaryStats(run) {
    var root = resultPayloadRoot(run);
    var mcp = resultPayloadMcp(run);
    var selectedVideo = resultPayloadSelectedVideo(run);
    var customers = resultPayloadCustomers(run);
    var selectedVideoHighIntentCount = asArray(selectedVideo.high_intent_users).length || asArray(selectedVideo.precise_customers).length || 0;
    var rootHighIntentCount = asArray(root.precise_customers).length || asArray(root.high_intent_users).length || 0;
    var mcpHighIntentCount = asArray(mcp.precise_customers).length || asArray(mcp.high_intent_users).length || 0;
    return {
      searchVideosTotal: firstMeaningful(root.search_videos_total, mcp.search_videos_total, root.total_videos, mcp.total_videos),
      commentsCollected: firstMeaningful(
        selectedVideo.comments_collected,
        root.total_customers,
        mcp.total_customers,
        root.all_customers,
        mcp.all_customers
      ),
      highIntentUsers: firstMeaningful(
        selectedVideo.high_intent_count,
        root.total_high_intent,
        mcp.total_high_intent,
        selectedVideoHighIntentCount ? selectedVideoHighIntentCount : '',
        rootHighIntentCount ? rootHighIntentCount : '',
        mcpHighIntentCount ? mcpHighIntentCount : '',
        customers.length ? customers.length : ''
      )
    };
  }

  function openMediaLightbox(item, title) {
    var modal = el('agentMediaLightbox');
    var stage = el('agentMediaLightboxStage');
    var heading = el('agentMediaLightboxTitle');
    if (!modal || !stage || !item || !item.url) return;
    if (heading) heading.textContent = title || '媒体预览';
    stage.innerHTML = '';
    if (item.type === 'video') {
      var video = document.createElement('video');
      video.src = item.url;
      video.controls = true;
      video.autoplay = true;
      video.playsInline = true;
      stage.appendChild(video);
    } else {
      var img = document.createElement('img');
      img.src = item.url;
      img.alt = title || '图片预览';
      stage.appendChild(img);
    }
    modal.hidden = false;
  }

  function closeMediaLightbox() {
    var modal = el('agentMediaLightbox');
    var stage = el('agentMediaLightboxStage');
    if (!modal || !stage) return;
    stage.innerHTML = '';
    modal.hidden = true;
  }

  function summarizeVoicePrompt(content) {
    var out = compactText(content, '');
    if (!out) return '等待新的手机语音任务...';
    out = out
      .replace(/^\[定时任务\]\s*/i, '')
      .replace(/^执行抖音获客任务[:：]?\s*/i, '')
      .replace(/^执行任务[:：]?\s*/i, '')
      .trim();
    if (/^(search_collect|tasks_from_search|comment_collect|interaction|stranger_collect|stranger_send)$/i.test(out)) {
      return ({
        search_collect: '采集客户',
        tasks_from_search: '同步任务池',
        comment_collect: '评论采集',
        interaction: '精准私信',
        stranger_collect: '陌生人私信采集',
        stranger_send: '陌生人私信发送'
      })[out.toLowerCase()] || out;
    }
    return out || '已收到手机任务，正在处理中';
  }

  function normalizeMessageItem(item) {
    var msg = item && item.message && typeof item.message === 'object' ? item.message : {};
    var events = Array.isArray(item && item.events) ? item.events : [];
    return {
      id: String(msg.id || ''),
      status: String(msg.status || ''),
      content: String(msg.content || ''),
      replyText: String(msg.reply_text || ''),
      error: String(msg.error || ''),
      createdAt: msg.created_at || '',
      updatedAt: msg.updated_at || '',
      events: events
    };
  }

  function normalizeRun(run) {
    run = run && typeof run === 'object' ? run : {};
    var payload = run.payload && typeof run.payload === 'object' ? run.payload : {};
    var resultPayload = run.result_payload && typeof run.result_payload === 'object' ? run.result_payload : {};
    var progress = run.progress && typeof run.progress === 'object' ? run.progress : {};
    return {
      id: String(run.id || ''),
      title: String(run.title || run.content || ''),
      content: String(run.content || ''),
      taskKind: String(run.task_kind || ''),
      status: String(run.status || ''),
      createdAt: run.created_at || '',
      updatedAt: run.updated_at || '',
      startedAt: run.started_at || '',
      finishedAt: run.finished_at || '',
      progress: progress,
      progressPercent: run.progress_percent != null ? run.progress_percent : (progress.progress != null ? progress.progress : progress.percent),
      progressLabel: String(run.progress_label || progress.label || progress.stage || ''),
      progressDetail: String(run.progress_detail || progress.text || progress.message || ''),
      resultText: String(run.result_text || ''),
      error: String(run.error || ''),
      payload: payload,
      resultPayload: resultPayload,
      installationId: String(run.installation_id || '')
    };
  }

  function newestTime(item) {
    return Date.parse(item.updatedAt || item.finishedAt || item.createdAt || item.startedAt || 0) || 0;
  }

  function latestMessage(messages) {
    return (messages || []).slice().sort(function (a, b) {
      return newestTime(b) - newestTime(a);
    })[0] || null;
  }

  function activeRuns(runs) {
    return (runs || []).filter(function (run) { return isRunningStatus(run.status); });
  }

  function recentRuns(runs, limit) {
    return (runs || []).slice().sort(function (a, b) {
      return newestTime(b) - newestTime(a);
    }).slice(0, limit || 4);
  }

  function latestRun(runs) {
    var rows = activeRuns(runs);
    if (!rows.length) rows = (runs || []).slice();
    rows.sort(function (a, b) { return newestTime(b) - newestTime(a); });
    return rows[0] || null;
  }

  function resetSwarmVisibleCount() {
    state.swarmVisibleCount = SWARM_PAGE_SIZE;
  }

  function growSwarmVisibleCount() {
    state.swarmVisibleCount += SWARM_PAGE_SIZE;
  }

  function inferProgress(run, message) {
    if (run) {
      if (run.progressPercent != null && run.progressPercent !== '') return clampPercent(run.progressPercent);
      var detail = String(run.progressDetail || '').match(/(\d{1,3})\s*%/);
      if (detail) return clampPercent(detail[1]);
      if (String(run.status).toLowerCase() === 'completed') return 100;
      if (String(run.status).toLowerCase() === 'failed') return 100;
      if (isRunningStatus(run.status)) return 18;
    }
    if (message) {
      if (String(message.status).toLowerCase() === 'completed') return 100;
      if (String(message.status).toLowerCase() === 'failed') return 100;
      if (String(message.status).toLowerCase() === 'processing') return 12;
    }
    return 0;
  }

  function buildVoicePrompt(message) {
    if (!message) return '等待新的手机语音任务...';
    var content = summarizeVoicePrompt(message.content);
    if (!content) return '已收到手机任务，正在等待解析内容...';
    return content;
  }

  function buildStageLabel(run, message) {
    if (run) {
      return compactText(run.progressLabel, capabilityLabel(run) + ' · ' + statusLabel(run.status));
    }
    if (message) {
      return messageStatusLabel(message.status);
    }
    return '待命中';
  }

  function buildSubtitle(run, message) {
    if (run) {
      var parts = [];
      parts.push(capabilityLabel(run));
      parts.push(statusLabel(run.status));
      return parts.join(' · ');
    }
    if (message) {
      return '手机任务 · ' + messageStatusLabel(message.status);
    }
    return '当前没有活跃任务';
  }

  function buildHeadline(run, message) {
    if (run) {
      return capabilityLabel(run) + '正在自动处理';
    }
    if (message) {
      return '手机任务已进入执行流程';
    }
    return '任务正在自动处理';
  }

  function buildStageDetail(run, message) {
    if (run) {
      if (hasMeaningfulText(run.progressDetail)) return compactText(run.progressDetail, '');
      if (hasMeaningfulText(run.title)) return compactText(run.title, '');
      if (hasMeaningfulText(run.content)) return compactText(run.content, '');
      return capabilityLabel(run) + '正在执行';
    }
    if (message) {
      return messageStatusLabel(message.status);
    }
    return '当前没有活跃任务';
  }

  function workerTone(run) {
    var status = String((run && run.status) || '').toLowerCase();
    if (status === 'completed') return 'done';
    if (status === 'failed' || status === 'cancelled') return 'failed';
    return 'running';
  }

  function workerStateLabel(run) {
    if (!run) return '等待中';
    if (isSkippedRun(run)) return '已完成';
    return statusLabel(run.status);
  }

  function workerDetailText(run) {
    if (!run) return '等待新的任务接入';
    if (isSkippedRun(run) && hasMeaningfulText(run.resultText)) return compactText(run.resultText, '');
    if (hasMeaningfulText(run.progressDetail)) return compactText(run.progressDetail, '');
    if (hasMeaningfulText(run.title)) return compactText(run.title, '');
    if (hasMeaningfulText(run.content)) return compactText(run.content, '');
    if (hasMeaningfulText(run.resultText) && String(run.status).toLowerCase() === 'completed') return compactText(run.resultText, '');
    if (!isSkippedRun(run) && hasMeaningfulText(run.error)) return compactText(run.error, '');
    return capabilityLabel(run) + '正在处理';
  }

  function workerProgress(run) {
    if (!run) return 0;
    if (run.progressPercent != null && run.progressPercent !== '') return clampPercent(run.progressPercent);
    if (isSkippedRun(run)) return 100;
    if (String(run.status).toLowerCase() === 'completed') return 100;
    if (String(run.status).toLowerCase() === 'failed' || String(run.status).toLowerCase() === 'cancelled') return 100;
    return inferProgress(run, null) || 16;
  }

  function workerAvatar(run, index) {
    var status = String((run && run.status) || '').toLowerCase();
    var capability = String((((run || {}).payload || {}).capability_id) || '').toLowerCase();
    var action = String((((run || {}).payload || {}).action) || '').toLowerCase();
    if (capability.indexOf('image') >= 0) {
      return status === 'failed'
        ? '/static/generated/agent/avatars/h5-employee-female-offline.png'
        : '/static/generated/agent/avatars/h5-employee-female-working.png';
    }
    if (capability.indexOf('video') >= 0) {
      return status === 'completed'
        ? '/static/generated/agent/avatars/h5-employee-male-idle.png'
        : '/static/generated/agent/avatars/h5-employee-male-working.png';
    }
    if (action === 'search_collect' || action === 'tasks_from_search') {
      return status === 'completed'
        ? '/static/generated/agent/avatars/h5-employee-male-idle.png'
        : '/static/generated/agent/avatars/h5-employee-male-working.png';
    }
    if (action === 'comment_collect' || action === 'interaction') {
      return status === 'failed'
        ? '/static/generated/agent/avatars/h5-employee-male-offline.png'
        : '/static/generated/agent/avatars/h5-employee-female-idle.png';
    }
    return [
      '/static/generated/agent/avatars/h5-employee-female-working.png',
      '/static/generated/agent/avatars/h5-employee-male-working.png',
      '/static/generated/agent/avatars/h5-employee-female-idle.png',
      '/static/generated/agent/avatars/h5-boss-avatar.png'
    ][index % 4];
  }

  function buildFocusTimeline(run, message) {
    var timeline = buildTimeline(message, run) || [];
    return timeline.slice(0, 6);
  }

  function buildFocusRows(run, message) {
    var rows = collectResultRows(run, message) || [];
    return rows.slice(0, 4);
  }

  function normalizeFocusItem(item) {
    if (!item) return null;
    return {
      id: item.id,
      title: item.title,
      subtitle: item.subtitle,
      detail: item.detail,
      tone: item.tone,
      progress: item.progress,
      state: item.state,
      age: item.age,
      timeline: item.timeline || [],
      resultRows: item.resultRows || []
    };
  }

  function buildSwarmItems(runs, message) {
    var items = recentRuns(runs, state.swarmVisibleCount).map(function (run, index) {
      return {
        id: run.id || ('run-' + index),
        title: capabilityLabel(run),
        subtitle: compactText(run.progressLabel, '执行单元 ' + String(index + 1).padStart(2, '0')),
        detail: workerDetailText(run),
        tone: workerTone(run),
        progress: workerProgress(run),
        avatar: workerAvatar(run, index),
        state: workerStateLabel(run),
        statusText: (function () {
          if (isSkippedRun(run)) return compactText(run.resultText || '已跳过重复任务，已展示历史结果', workerStateLabel(run));
          var lowered = String(run.status || '').toLowerCase();
          if (lowered === 'completed' || lowered === 'failed' || lowered === 'cancelled') return workerStateLabel(run);
          return compactText(run.progressDetail || run.progressLabel || run.title || '', workerStateLabel(run));
        })(),
        age: relativeTime(run.updatedAt || run.finishedAt || run.createdAt || '') || fmtTime(run.updatedAt || run.createdAt || ''),
        timeline: buildFocusTimeline(run, message),
        resultRows: buildFocusRows(run, message)
      };
    });
    if (!items.length && message) {
      items.push({
        id: message.id || 'message-primary',
        title: '语音任务',
        subtitle: '等待转入执行单元',
        detail: summarizeVoicePrompt(message.content),
        tone: String(message.status).toLowerCase() === 'failed' ? 'failed' : 'running',
        progress: inferProgress(null, message),
        avatar: '/static/generated/agent/avatars/h5-boss-avatar.png',
        state: messageStatusLabel(message.status),
        statusText: messageStatusLabel(message.status),
        age: relativeTime(message.updatedAt || message.createdAt || '') || fmtTime(message.updatedAt || message.createdAt || ''),
        timeline: buildFocusTimeline(null, message),
        resultRows: buildFocusRows(null, message)
      });
    }
    return items.slice(0, state.swarmVisibleCount);
  }

  function renderSwarm(items, stageLabel, stageText) {
    text('agentVisualStageLabel', stageLabel || '等待任务接入');
    text('agentVisualStageText', stageText || '手机端下发语音任务后，左侧会展示多个执行任务的实时状态。');
    if (!items || !items.length) {
      html('agentVisualSwarm', '<div class="agent-visual-empty">等待任务接入后，这里会出现多个执行单元的协同状态。</div>');
      return;
    }
    html('agentVisualSwarm', items.map(function (item) {
      var progress = clampPercent(item.progress);
      return ''
        + '<article class="agent-visual-worker" data-tone="' + h(item.tone || 'running') + '" style="--agent-x:' + h(item.x) + ';--agent-y:' + h(item.y) + ';--agent-progress:' + h(progress) + ';">'
        + '  <div class="agent-visual-worker-head">'
        + '    <div class="agent-visual-worker-avatar"></div>'
        + '    <div class="agent-visual-worker-title">'
        + '      <strong>' + h(item.title || '执行单元') + '</strong>'
        + '      <span>' + h(item.subtitle || '') + '</span>'
        + '    </div>'
        + '    <div class="agent-visual-worker-state">' + h(item.state || '') + '</div>'
        + '  </div>'
        + '  <div class="agent-visual-worker-desc">' + h(item.detail || '') + '</div>'
        + '  <div class="agent-visual-worker-progress">'
        + '    <div class="agent-visual-worker-progress-top">'
        + '      <span>' + h(item.age || '') + '</span>'
        + '      <span>' + h(progress + '%') + '</span>'
        + '    </div>'
        + '    <div class="agent-visual-worker-progress-bar"><div class="agent-visual-worker-progress-fill"></div></div>'
        + '  </div>'
        + '</article>';
    }).join(''));
  }

  function renderTaskGrid(items) {
    if (!items || !items.length) {
      html('agentVisualSwarm', '<div class="agent-task-grid-empty">等待任务接入后，这里会出现规则排列的任务卡片。</div>', true);
      return;
    }
    html('agentVisualSwarm', items.map(function (item) {
      var progress = clampPercent(item.progress);
      return ''
        + '<button type="button" class="agent-task-card' + (item.id === state.selectedSwarmId ? ' active' : '') + '" data-agent-id="' + h(item.id || '') + '" data-tone="' + h(item.tone || 'running') + '" style="--agent-progress:' + h(progress) + ';">'
        + '  <div class="agent-task-card-head">'
        + '    <div class="agent-task-card-avatar"><img src="' + h(item.avatar || '/static/generated/agent/avatars/h5-boss-avatar.png') + '" alt="' + h(item.title || '任务头像') + '"></div>'
        + '    <div class="agent-task-card-title">'
        + '      <strong>' + h(item.title || '执行单元') + '</strong>'
        + '      <span>' + h(item.subtitle || '') + '</span>'
        + '    </div>'
        + '  </div>'
        + '  <div class="agent-task-card-desc">' + h(item.detail || '') + '</div>'
        + '  <div class="agent-task-card-meta">'
        + '    <span>当前状态</span>'
        + '    <span>' + h(item.statusText || item.state || '') + '</span>'
        + '  </div>'
        + '  <div class="agent-task-card-meta">'
        + '    <span>' + h(item.age || '') + '</span>'
        + '    <span class="agent-task-card-state">' + h(item.state || '') + '</span>'
        + '  </div>'
        + '  <div class="agent-task-card-meta">'
        + '    <span>进度</span>'
        + '    <span>' + h(progress + '%') + '</span>'
        + '  </div>'
        + '  <div class="agent-task-card-progress"><div class="agent-task-card-progress-fill"></div></div>'
        + '</button>';
    }).join(''), true);
  }

  function renderFocusTimeline(items) {
    var host = el('agentFocusTimeline');
    if (!host) return;
    if (!items || !items.length) {
      host.innerHTML = '<div class="agent-focus-empty">当前没有可展示的执行过程。</div>';
      return;
    }
    host.innerHTML = items.map(function (item) {
      return ''
        + '<article class="agent-focus-event">'
        + '  <div class="agent-focus-event-head">'
        + '    <strong>' + h(item.title || '任务事件') + '</strong>'
        + '    <span>' + h(relativeTime(item.time) || fmtTime(item.time) || '') + '</span>'
        + '  </div>'
        + '  <p>' + h(item.text || '') + '</p>'
        + '</article>';
    }).join('');
  }

  function renderFocusResult(rows) {
    var host = el('agentFocusResult');
    if (!host) return;
    if (!rows || !rows.length) {
      host.innerHTML = '<div class="agent-focus-empty">当前没有可展示的结果。</div>';
      return;
    }
    host.innerHTML = rows.map(function (row) {
      if (row && row.kind === 'media' && row.media && row.media.url) {
        return ''
          + '<div class="agent-result-media-card">'
          + '  <div class="agent-result-media-head">'
          + '    <span>' + h(row.label || '素材结果') + '</span>'
          + '    <div class="agent-result-media-meta">' + h(row.meta || '') + '</div>'
          + '  </div>'
          + '  <button type="button" class="agent-result-media-button" data-media-url="' + h(row.media.url) + '" data-media-type="' + h(row.media.type || '') + '" data-media-title="' + h(row.label || '媒体预览') + '">'
          + (row.media.type === 'video'
            ? '    <video src="' + h(row.media.url) + '" muted playsinline preload="metadata"></video>'
            : '    <img src="' + h(row.media.url) + '" alt="' + h(row.label || '图片结果') + '">')
          + '    <span class="agent-result-media-action">' + h(row.media.type === 'video' ? '查看视频' : '放大查看') + '</span>'
          + '  </button>'
          + (hasMeaningfulText(row.note) ? '  <div class="agent-result-media-note">' + h(row.note) + '</div>' : '')
          + '</div>';
      }
      return ''
        + '<div class="agent-result-row">'
        + '  <span>' + h(row.label || '') + '</span>'
        + '  <strong>' + h(row.value || '') + '</strong>'
        + '</div>';
    }).join('');
  }

  function renderFocusCard(item) {
    var focus = normalizeFocusItem(item);
    text('agentFocusTitle', focus ? focus.title : '等待任务接入');
    text('agentFocusSummary', focus ? focus.detail : '点击右侧任务卡后，这里会显示对应任务的执行过程和结果。');
    var statusNode = el('agentFocusStatus');
    if (statusNode) {
      statusNode.textContent = focus ? (focus.state || '等待中') : '等待中';
      statusNode.setAttribute('data-tone', focus ? (focus.tone || 'running') : 'running');
    }
    renderFocusTimeline(focus ? focus.timeline : []);
    renderFocusResult(focus ? focus.resultRows : []);
  }

  function syncSelectedSwarm(items) {
    var list = Array.isArray(items) ? items : [];
    if (!list.length) {
      state.selectedSwarmId = '';
      return null;
    }
    var found = list.some(function (item) { return item.id === state.selectedSwarmId; });
    if (!found) state.selectedSwarmId = list[0].id;
    return list.find(function (item) { return item.id === state.selectedSwarmId; }) || list[0];
  }

  function bindSwarmSelection() {
    var host = el('agentVisualSwarm');
    if (!host || host.__agentSwarmBound) return;
    host.__agentSwarmBound = true;
    host.addEventListener('click', function (event) {
      var card = event.target && event.target.closest ? event.target.closest('.agent-task-card') : null;
      if (!card) return;
      var nextId = card.getAttribute('data-agent-id') || '';
      if (!nextId) return;
      state.selectedSwarmId = nextId;
      rerenderFromCache();
      refreshDashboard(false);
    });
  }

  function bindSwarmScrollPagination() {
    var host = el('agentVisualSwarm');
    if (!host || host.__agentSwarmScrollBound) return;
    host.__agentSwarmScrollBound = true;
    host.addEventListener('scroll', function () {
      if (host.scrollHeight <= host.clientHeight + 8) return;
      if (host.scrollTop + host.clientHeight < host.scrollHeight - 24) return;
      growSwarmVisibleCount();
      rerenderFromCache();
    });
  }

  function appendEvent(events, item) {
    if (!item || !item.text) return;
    if (!hasMeaningfulText(item.text)) return;
    events.push(item);
  }

  function stageTextFromPayload(payload) {
    if (!payload || typeof payload !== 'object') return '';
    return compactText(payload.text || payload.message || payload.status_text || payload.stage || '', '');
  }

  function buildTimeline(message, run) {
    var items = [];
    if (message) {
      appendEvent(items, {
        type: 'phone',
        title: '收到任务',
        text: summarizeVoicePrompt(message.content),
        time: message.createdAt || message.updatedAt || '',
        status: messageStatusLabel(message.status)
      });
      (message.events || []).slice(-8).forEach(function (ev) {
        var payload = ev && ev.payload && typeof ev.payload === 'object' ? ev.payload : {};
        var type = String(ev && ev.type || 'progress');
        var labelMap = {
          claimed: '客户端已接单',
          thinking: '正在理解任务',
          progress: '执行进度',
          tool_start: '开始处理',
          tool_end: '处理完成',
          final: '语音任务完成',
          error: '语音任务失败'
        };
        var eventText = stageTextFromPayload(payload);
        if (!hasMeaningfulText(eventText)) return;
        appendEvent(items, {
          type: type,
          title: labelMap[type] || '任务事件',
          text: eventText,
          time: ev.created_at || ev.updated_at || message.updatedAt || '',
          status: ''
        });
      });
      if (message.replyText) {
        appendEvent(items, {
          type: 'reply',
          title: '语音结果',
          text: (run && isMediaRun(run) && pickPrimaryMedia(run))
            ? '宸插姞鍏ョ礌鏉愬簱锛屽彲鐩存帴鍦ㄧ礌鏉愬簱涓户缁煡鐪嬪拰浣跨敤銆?'
            : compactText(message.replyText, ''),
          time: message.updatedAt || '',
          status: ''
        });
      }
      if (message.error) {
        appendEvent(items, {
          type: 'error',
          title: '语音失败',
          text: compactText(message.error, ''),
          time: message.updatedAt || '',
          status: ''
        });
      }
    }
    if (run) {
      appendEvent(items, {
        type: 'run',
        title: capabilityLabel(run),
        text: compactText(run.progressDetail || run.title || run.content || '任务已进入执行队列', ''),
        time: run.createdAt || run.updatedAt || '',
        status: statusLabel(run.status)
      });
      if (run.progressDetail) {
        appendEvent(items, {
          type: 'progress',
          title: '当前阶段',
          text: compactText(run.progressDetail, ''),
          time: run.updatedAt || '',
          status: ''
        });
      }
      if (run.resultText && String(run.status).toLowerCase() !== 'failed') {
        appendEvent(items, {
          type: 'result',
          title: '任务结果',
          text: compactText(run.resultText, ''),
          time: run.finishedAt || run.updatedAt || '',
          status: ''
        });
      }
      if (run.error && !isSkippedRun(run)) {
        appendEvent(items, {
          type: 'error',
          title: '任务失败',
          text: compactText(run.error, ''),
          time: run.finishedAt || run.updatedAt || '',
          status: ''
        });
      }
    }
    items.sort(function (a, b) {
      return (Date.parse(b.time || 0) || 0) - (Date.parse(a.time || 0) || 0);
    });
    var deduped = [];
    var seen = {};
    items.forEach(function (item) {
      var key = [item.title, item.text, item.status].join('||');
      if (seen[key]) return;
      seen[key] = true;
      deduped.push(item);
    });
    return deduped.slice(0, 4);
  }

  function renderTimeline(items) {
    var host = el('agentEventList');
    if (!host) return;
    if (!items || !items.length) {
      host.innerHTML = '<div class="agent-empty-state-small">正在等待执行事件...</div>';
      return;
    }
    host.innerHTML = items.map(function (item) {
      return ''
        + '<article class="agent-event-item" data-type="' + h(item.type || 'progress') + '">'
        + '  <div class="agent-event-dot"></div>'
        + '  <div class="agent-event-body">'
        + '    <div class="agent-event-topline">'
        + '      <strong>' + h(item.title || '任务事件') + '</strong>'
        + '      <span>' + h(relativeTime(item.time) || fmtTime(item.time) || '') + '</span>'
        + '    </div>'
        + (item.status ? '<div class="agent-event-status">' + h(item.status) + '</div>' : '')
        + '    <p>' + h(item.text || '') + '</p>'
        + '  </div>'
        + '</article>';
    }).join('');
  }

  function collectResultRows(run, message) {
    var rows = [];
    var primaryMedia = pickPrimaryMedia(run);
    if (run && run.resultPayload && typeof run.resultPayload === 'object') {
      var rp = run.resultPayload;
      var stats = resultPayloadSummaryStats(run);
      var preciseCustomers = resultPayloadCustomers(run);
      if (hasMeaningfulText(stats.searchVideosTotal)) rows.push({ label: '命中视频', value: String(stats.searchVideosTotal) });
      if (hasMeaningfulText(stats.commentsCollected)) rows.push({ label: '采集客户', value: String(stats.commentsCollected) });
      if (hasMeaningfulText(stats.highIntentUsers)) rows.push({ label: '精准客户', value: String(stats.highIntentUsers) });
      if (Array.isArray(rp.media_urls) && rp.media_urls.length) {
        rows.push({ label: '素材结果', value: rp.media_urls.length + ' 个文件已生成' });
      }
      if (rp.publish_draft) {
        rows.push({ label: '发布草稿', value: '已生成草稿，可继续发布' });
      }
      if (preciseCustomers.length) {
        rows.push({
          label: '精准客户列表',
          kind: 'customer-list',
          customers: preciseCustomers.slice(0, 8)
        });
      }
    }
    if (primaryMedia) {
      rows.unshift({
        label: primaryMedia.type === 'video' ? '最终视频' : '最终图片',
        kind: 'media',
        media: primaryMedia,
        meta: capabilityLabel(run),
        note: '已加入素材库，可直接在素材库中继续查看和使用。'
      });
    }
    if (run && run.resultText && !primaryMedia && !isInstructionalResultText(run.resultText)) {
      rows.push({ label: isSkippedRun(run) ? '处理结果' : '结果摘要', value: compactText(run.resultText, '') });
    }
    if (run && run.error && !isSkippedRun(run)) rows.push({ label: '错误信息', value: compactText(run.error, '') });
    if (!rows.length && message && message.replyText) rows.push({ label: '回复内容', value: compactText(message.replyText, '') });
    if (!rows.length && message && message.error) rows.push({ label: '错误信息', value: compactText(message.error, '') });
    return rows.filter(function (row) {
      if (row && row.kind === 'media') return !!(row.media && row.media.url);
      if (row && row.kind === 'customer-list') return Array.isArray(row.customers) && row.customers.length > 0;
      return hasMeaningfulText(row && row.value);
    }).slice(0, primaryMedia ? 4 : 5);
  }

  function renderResult(rows) {
    var card = el('agentResultCard');
    var body = el('agentResultBody');
    if (!card || !body) return;
    if (!rows || !rows.length) {
      card.hidden = true;
      body.innerHTML = '';
      return;
    }
    card.hidden = false;
    body.innerHTML = rows.map(function (row) {
      if (row && row.kind === 'media' && row.media && row.media.url) {
        return ''
          + '<div class="agent-result-media-card">'
          + '  <div class="agent-result-media-head">'
          + '    <span>' + h(row.label || '素材结果') + '</span>'
          + '    <div class="agent-result-media-meta">' + h(row.meta || '') + '</div>'
          + '  </div>'
          + '  <button type="button" class="agent-result-media-button" data-media-url="' + h(row.media.url) + '" data-media-type="' + h(row.media.type || '') + '" data-media-title="' + h(row.label || '媒体预览') + '">'
          + (row.media.type === 'video'
            ? '    <video src="' + h(row.media.url) + '" muted playsinline preload="metadata"></video>'
            : '    <img src="' + h(row.media.url) + '" alt="' + h(row.label || '图片结果') + '">')
          + '    <span class="agent-result-media-action">' + h(row.media.type === 'video' ? '查看视频' : '放大查看') + '</span>'
          + '  </button>'
          + (hasMeaningfulText(row.note) ? '  <div class="agent-result-media-note">' + h(row.note) + '</div>' : '')
          + '</div>';
      }
      if (row && row.kind === 'customer-list' && Array.isArray(row.customers) && row.customers.length) {
        return ''
          + '<div class="agent-customer-list-card">'
          + '  <div class="agent-customer-list-head">'
          + '    <span>' + h(row.label || '精准客户列表') + '</span>'
          + '    <strong>' + h(String(row.customers.length)) + ' 人</strong>'
          + '  </div>'
          + '  <div class="agent-customer-list-body">'
          + row.customers.map(function (customer) {
            var meta = [];
            if (hasMeaningfulText(customer.reason)) meta.push(customer.reason);
            if (hasMeaningfulText(customer.score)) meta.push('评分 ' + customer.score);
            if (hasMeaningfulText(customer.sourceVideoTitle)) meta.push('来源：' + customer.sourceVideoTitle);
            return ''
              + '<article class="agent-customer-item">'
              + '  <div class="agent-customer-item-top">'
              + '    <strong>' + h(customer.nickname || '未命名客户') + '</strong>'
              + (hasMeaningfulText(customer.profileUrl)
                ? '    <a href="' + h(customer.profileUrl) + '" target="_blank" rel="noopener noreferrer">查看主页</a>'
                : '')
              + '  </div>'
              + (hasMeaningfulText(customer.commentText)
                ? '  <p class="agent-customer-comment">' + h(customer.commentText) + '</p>'
                : '  <p class="agent-customer-comment is-empty">暂无客户评论内容</p>')
              + (meta.length
                ? '  <div class="agent-customer-meta">' + h(meta.join(' · ')) + '</div>'
                : '')
              + '</article>';
          }).join('')
          + '  </div>'
          + '</div>';
      }
      return ''
        + '<div class="agent-result-row">'
        + '  <span>' + h(row.label || '') + '</span>'
        + '  <strong>' + h(row.value || '') + '</strong>'
        + '</div>';
    }).join('');
  }

  function bindMediaPreview() {
    var root = el('agentResultBody');
    var closeBtn = el('agentMediaLightboxClose');
    var modal = el('agentMediaLightbox');
    if (root && !root.__agentMediaBound) {
      root.__agentMediaBound = true;
      root.addEventListener('click', function (event) {
        var btn = event.target && event.target.closest ? event.target.closest('.agent-result-media-button') : null;
        if (!btn) return;
        openMediaLightbox({
          url: btn.getAttribute('data-media-url') || '',
          type: btn.getAttribute('data-media-type') || ''
        }, btn.getAttribute('data-media-title') || '媒体预览');
      });
    }
    if (closeBtn && !closeBtn.__agentMediaBound) {
      closeBtn.__agentMediaBound = true;
      closeBtn.addEventListener('click', closeMediaLightbox);
    }
    if (modal && !modal.__agentMediaBound) {
      modal.__agentMediaBound = true;
      modal.addEventListener('click', function (event) {
        if (event.target === modal) closeMediaLightbox();
      });
    }
    if (!document.__agentMediaEscBound) {
      document.__agentMediaEscBound = true;
      document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') closeMediaLightbox();
      });
    }
  }

  function isMediaRun(run) {
    var capabilityId = String((((run || {}).payload || {}).capability_id) || '').trim();
    return capabilityId === 'goal.image.pipeline'
      || capabilityId === 'goal.video.pipeline'
      || capabilityId === 'create.video.pipeline';
  }

  function shouldHideResultRowForMediaRun(run, row) {
    if (!isMediaRun(run) || !row) return false;
    if (row.kind === 'media') return false;
    if (compactText(row.label, '').toLowerCase().indexOf('错误') >= 0) return false;
    return true;
  }

  function timelineResultText(run) {
    if (!run) return '';
    if (isMediaRun(run) && pickPrimaryMedia(run)) {
      return '已加入素材库，可直接在素材库中继续查看和使用。';
    }
    return compactText(run.resultText, '');
  }

  function buildTimeline(message, run) {
    var items = [];
    if (message) {
      appendEvent(items, {
        type: 'phone',
        title: '收到任务',
        text: summarizeVoicePrompt(message.content),
        time: message.createdAt || message.updatedAt || '',
        status: messageStatusLabel(message.status)
      });
      (message.events || []).slice(-8).forEach(function (ev) {
        var payload = ev && ev.payload && typeof ev.payload === 'object' ? ev.payload : {};
        var type = String(ev && ev.type || 'progress');
        var labelMap = {
          claimed: '客户端已接单',
          thinking: '正在理解任务',
          progress: '执行进度',
          tool_start: '开始处理',
          tool_end: '处理完成',
          final: '语音任务完成',
          error: '语音任务失败'
        };
        var eventText = stageTextFromPayload(payload);
        if (!hasMeaningfulText(eventText)) return;
        appendEvent(items, {
          type: type,
          title: labelMap[type] || '任务事件',
          text: eventText,
          time: ev.created_at || ev.updated_at || message.updatedAt || '',
          status: ''
        });
      });
      if (message.replyText) {
        appendEvent(items, {
          type: 'reply',
          title: '语音结果',
          text: compactText(message.replyText, ''),
          time: message.updatedAt || '',
          status: ''
        });
      }
      if (message.error) {
        appendEvent(items, {
          type: 'error',
          title: '语音失败',
          text: compactText(message.error, ''),
          time: message.updatedAt || '',
          status: ''
        });
      }
    }
    if (run) {
      appendEvent(items, {
        type: 'run',
        title: capabilityLabel(run),
        text: compactText(run.progressDetail || run.title || run.content || '任务已进入执行队列', ''),
        time: run.createdAt || run.updatedAt || '',
        status: statusLabel(run.status)
      });
      if (run.progressDetail) {
        appendEvent(items, {
          type: 'progress',
          title: '当前阶段',
          text: compactText(run.progressDetail, ''),
          time: run.updatedAt || '',
          status: ''
        });
      }
      if (run.resultText && String(run.status).toLowerCase() !== 'failed') {
        appendEvent(items, {
          type: 'result',
          title: '任务结果',
          text: timelineResultText(run),
          time: run.finishedAt || run.updatedAt || '',
          status: ''
        });
      }
      if (run.error) {
        appendEvent(items, {
          type: 'error',
          title: '任务失败',
          text: compactText(run.error, ''),
          time: run.finishedAt || run.updatedAt || '',
          status: ''
        });
      }
    }
    items.sort(function (a, b) {
      return (Date.parse(b.time || 0) || 0) - (Date.parse(a.time || 0) || 0);
    });
    var deduped = [];
    var seen = {};
    items.forEach(function (item) {
      var key = [item.title, item.text, item.status].join('||');
      if (seen[key]) return;
      seen[key] = true;
      deduped.push(item);
    });
    return deduped.slice(0, 4);
  }

  function fingerprint(snapshot) {
    try {
      return JSON.stringify(snapshot);
    } catch (e) {
      return String(Date.now());
    }
  }

  function renderSnapshot(snapshot) {
    state.lastSnapshot = snapshot;
    var activeItem = syncSelectedSwarm(snapshot.swarmItems || []);
    var activeTimeline = activeItem && activeItem.timeline ? activeItem.timeline : snapshot.timeline;
    var activeResultRows = activeItem && activeItem.resultRows ? activeItem.resultRows : snapshot.resultRows;
    var activeHeadline = activeItem && activeItem.title ? activeItem.title + '姝ｅ湪鑷姩澶勭悊' : snapshot.headline;
    var activeStageLabel = activeItem && activeItem.subtitle ? activeItem.subtitle : snapshot.stageLabel;
    var activeStageDetail = activeItem && activeItem.detail ? activeItem.detail : snapshot.stageDetail;
    var summaryFp = fingerprint({
      voicePrompt: snapshot.voicePrompt,
      onlineDevices: snapshot.onlineDevices,
      runningRuns: snapshot.runningRuns,
      stageLabel: activeStageLabel,
      stageDetail: activeStageDetail,
      headline: activeHeadline,
      progressPercent: snapshot.progressPercent,
      subtitle: snapshot.subtitle
    });
    var swarmFp = fingerprint({
      selectedSwarmId: state.selectedSwarmId || '',
      swarmItems: snapshot.swarmItems || []
    });
    var timelineFp = fingerprint(activeTimeline || []);
    var resultFp = fingerprint(activeResultRows || []);

    if (state.lastFingerprints.summary !== summaryFp) {
      state.lastFingerprints.summary = summaryFp;
      text('agentVoicePrompt', snapshot.voicePrompt);
      text('agentVisualOnlineDevices', snapshot.onlineDevices);
      text('agentVisualRunningRuns', snapshot.runningRuns);
      text('agentOnlineDeviceCount', snapshot.onlineDevices);
      text('agentTaskRunCount', snapshot.runningRuns);
      text('agentCurrentStage', activeStageLabel);
      text('agentStageDetail', activeStageDetail);
      text('agentExecutionHeadline', activeHeadline);
      setProgress(snapshot.progressPercent);
      text('agentExecutionSubtitle', snapshot.subtitle);
    }
    if (state.lastFingerprints.swarm !== swarmFp) {
      state.lastFingerprints.swarm = swarmFp;
      renderTaskGrid(snapshot.swarmItems || []);
    }
    if (state.lastFingerprints.timeline !== timelineFp) {
      state.lastFingerprints.timeline = timelineFp;
      renderTimeline(activeTimeline);
    }
    if (state.lastFingerprints.result !== resultFp) {
      state.lastFingerprints.result = resultFp;
      renderResult(activeResultRows);
    }
  }

  function rerenderFromCache() {
    if (!state.lastSnapshot) return;
    state.lastFingerprints.summary = '';
    state.lastFingerprints.swarm = '';
    state.lastFingerprints.timeline = '';
    state.lastFingerprints.result = '';
    renderSnapshot(state.lastSnapshot);
  }
  function loadAgentSubUsers() {
    var listEl = el('agentSubUserList');
    var countEl = el('agentSubUserCount');
    var base = localBase();
    if (!listEl || !countEl || !base) return Promise.resolve();
    if (!state.loadedSubUsers) countEl.textContent = '下级用户：加载中...';

    return fetch(base + '/auth/agent/sub-users', { headers: headers() })
      .then(function (resp) {
        if (resp.status === 403) return { sub_users: [], count: 0, _forbidden: true };
        return parseJsonResponse(resp);
      })
      .then(function (data) {
        state.loadedSubUsers = true;
        if (data._forbidden) {
          countEl.textContent = '无权限访问（非代理商账号）';
          listEl.innerHTML = '';
          return;
        }
        var list = Array.isArray(data.sub_users) ? data.sub_users : [];
        countEl.textContent = '下级用户：' + (data.count || list.length || 0) + ' 人';
        if (!list.length) {
          listEl.innerHTML = '<p class="agent-debug-empty">暂无下级用户</p>';
          return;
        }
        var html = '<table><thead><tr><th>ID</th><th>账号</th><th>当前算力</th><th>累计充值</th><th>注册时间</th></tr></thead><tbody>';
        list.forEach(function (user) {
          var email = String(user.email || '-').replace(/@sms\.lobster\.local$/, '');
          var created = String(user.created_at || '').replace('T', ' ').slice(0, 19);
          html += ''
            + '<tr>'
            + '<td>' + h(user.id) + '</td>'
            + '<td>' + h(email) + '</td>'
            + '<td>' + h(user.credits != null ? user.credits : '-') + '</td>'
            + '<td>' + h(user.total_recharged || 0) + '</td>'
            + '<td>' + h(created || '-') + '</td>'
            + '</tr>';
        });
        html += '</tbody></table>';
        listEl.innerHTML = html;
      })
      .catch(function (err) {
        countEl.textContent = '加载失败';
        listEl.innerHTML = '<p class="agent-debug-error">' + h(err && err.message ? err.message : String(err)) + '</p>';
      });
  }

  function refreshDashboard(force) {
    if (state.destroyed) return Promise.resolve(false);
    var now = Date.now();
    if (state.inFlight) return Promise.resolve(false);
    if (!force && now - state.lastRefreshAt < REFRESH_DEBOUNCE_MS) return Promise.resolve(false);
    state.inFlight = true;
    state.lastRefreshAt = now;
    return Promise.all([
      fetchJson('/api/h5-chat/messages?limit=40'),
      fetchJson('/api/scheduled-tasks/runs?limit=80')
    ]).then(function (results) {
      var messageItems = Array.isArray(results[0] && results[0].messages) ? results[0].messages.map(normalizeMessageItem) : [];
      var runItems = Array.isArray(results[1] && results[1].runs) ? results[1].runs.map(normalizeRun) : [];
      var msg = latestMessage(messageItems);
      var run = latestRun(runItems);
      var snapshot = {
        headline: buildHeadline(run, msg),
        voicePrompt: buildVoicePrompt(msg),
        onlineDevices: String(msg ? 1 : 0),
        runningRuns: String(activeRuns(runItems).length),
        stageLabel: buildStageLabel(run, msg),
        stageDetail: buildStageDetail(run, msg),
        progressPercent: inferProgress(run, msg),
        subtitle: buildSubtitle(run, msg),
        swarmItems: buildSwarmItems(runItems, msg),
        timeline: buildTimeline(msg, run),
        resultRows: collectResultRows(run, msg)
      };
      renderSnapshot(snapshot);
      return true;
    }).catch(function (err) {
      renderSnapshot({
        headline: '执行状态暂时不可用',
        voicePrompt: '状态拉取失败，请稍后重试',
        onlineDevices: '0',
        runningRuns: '0',
        stageLabel: '状态同步失败',
        stageDetail: '当前无法获取执行状态，请稍后重试',
        progressPercent: 100,
        subtitle: '本地客户端暂时无法同步执行态数据',
        timeline: [{
          type: 'error',
          title: '同步失败',
          text: err && err.message ? err.message : String(err),
          time: new Date().toISOString(),
          status: '异常'
        }],
        resultRows: []
      });
      return false;
    }).finally(function () {
      state.inFlight = false;
    });
  }

  function clearTimer() {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  function ensurePolling() {
    clearTimer();
    state.destroyed = false;
    state.timer = setInterval(function () {
      if (!document.getElementById('content-agent') || !document.getElementById('content-agent').classList.contains('visible')) return;
      refreshDashboard(false);
    }, POLL_MS);
  }

  function bindRefreshButton() {
    var btn = el('agentRefreshBtn');
    if (!btn || btn.__agentBound) return;
    btn.__agentBound = true;
    btn.addEventListener('click', function () {
      resetSwarmVisibleCount();
      refreshDashboard(true);
      loadAgentSubUsers();
    });
  }

  window.loadAgentSubUsers = function loadAgentView() {
    bindRefreshButton();
    bindMediaPreview();
    bindSwarmSelection();
    bindSwarmScrollPagination();
    resetSwarmVisibleCount();
    ensurePolling();
    loadAgentSubUsers();
    refreshDashboard(true);
  };
})();
