/* global API_BASE, LOCAL_API_BASE, authHeaders, escapeHtml */

(function () {
  'use strict';

  var POLL_MS = 5000;
  var REFRESH_DEBOUNCE_MS = 1200;

  var state = {
    loadedSubUsers: false,
    timer: null,
    inFlight: false,
    lastRefreshAt: 0,
    lastFingerprint: '',
    destroyed: false
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

  function latestRun(runs) {
    var rows = activeRuns(runs);
    if (!rows.length) rows = (runs || []).slice();
    rows.sort(function (a, b) { return newestTime(b) - newestTime(a); });
    return rows[0] || null;
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

  function collectResultRows(run, message) {
    var rows = [];
    if (run && run.resultPayload && typeof run.resultPayload === 'object') {
      var rp = run.resultPayload;
      if (rp.search_videos_total != null) rows.push({ label: '命中视频', value: String(rp.search_videos_total) });
      if (rp.selected_video && typeof rp.selected_video === 'object') {
        if (rp.selected_video.comments_collected != null) rows.push({ label: '采集客户', value: String(rp.selected_video.comments_collected) });
        if (rp.selected_video.high_intent_users != null) rows.push({ label: '精准客户', value: String(rp.selected_video.high_intent_users) });
      }
      if (Array.isArray(rp.media_urls) && rp.media_urls.length) {
        rows.push({ label: '素材结果', value: rp.media_urls.length + ' 个文件已生成' });
      }
      if (rp.publish_draft) {
        rows.push({ label: '发布草稿', value: '已生成草稿，可继续发布' });
      }
    }
    if (run && run.resultText) rows.push({ label: '结果摘要', value: compactText(run.resultText, '') });
    if (run && run.error) rows.push({ label: '错误信息', value: compactText(run.error, '') });
    if (!rows.length && message && message.replyText) rows.push({ label: '回复内容', value: compactText(message.replyText, '') });
    if (!rows.length && message && message.error) rows.push({ label: '错误信息', value: compactText(message.error, '') });
    return rows.filter(function (row) {
      return hasMeaningfulText(row && row.value);
    }).slice(0, 4);
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
      return ''
        + '<div class="agent-result-row">'
        + '  <span>' + h(row.label || '') + '</span>'
        + '  <strong>' + h(row.value || '') + '</strong>'
        + '</div>';
    }).join('');
  }

  function collectResultRows(run, message) {
    var rows = [];
    var primaryMedia = pickPrimaryMedia(run);
    if (run && run.resultPayload && typeof run.resultPayload === 'object') {
      var rp = run.resultPayload;
      if (rp.search_videos_total != null) rows.push({ label: '命中视频', value: String(rp.search_videos_total) });
      if (rp.selected_video && typeof rp.selected_video === 'object') {
        if (rp.selected_video.comments_collected != null) rows.push({ label: '采集客户', value: String(rp.selected_video.comments_collected) });
        if (rp.selected_video.high_intent_users != null) rows.push({ label: '精准客户', value: String(rp.selected_video.high_intent_users) });
      }
      if (Array.isArray(rp.media_urls) && rp.media_urls.length) {
        rows.push({ label: '素材结果', value: rp.media_urls.length + ' 个文件已生成' });
      }
      if (rp.publish_draft) {
        rows.push({ label: '发布草稿', value: '已生成草稿，可继续发布' });
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
    if (run && run.resultText && !primaryMedia) rows.push({ label: '结果摘要', value: compactText(run.resultText, '') });
    if (run && run.error) rows.push({ label: '错误信息', value: compactText(run.error, '') });
    if (!rows.length && message && message.replyText) rows.push({ label: '回复内容', value: compactText(message.replyText, '') });
    if (!rows.length && message && message.error) rows.push({ label: '错误信息', value: compactText(message.error, '') });
    return rows.filter(function (row) {
      if (row && row.kind === 'media') return !!(row.media && row.media.url);
      return hasMeaningfulText(row && row.value);
    }).slice(0, primaryMedia ? 3 : 4);
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
    var fp = fingerprint(snapshot);
    if (state.lastFingerprint === fp) return;
    state.lastFingerprint = fp;
    text('agentVoicePrompt', snapshot.voicePrompt);
    text('agentOnlineDeviceCount', snapshot.onlineDevices);
    text('agentTaskRunCount', snapshot.runningRuns);
    text('agentCurrentStage', snapshot.stageLabel);
    text('agentStageDetail', snapshot.stageDetail);
    text('agentExecutionHeadline', snapshot.headline);
    setProgress(snapshot.progressPercent);
    text('agentExecutionSubtitle', snapshot.subtitle);
    renderTimeline(snapshot.timeline);
    renderResult(snapshot.resultRows);
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
      refreshDashboard(true);
      loadAgentSubUsers();
    });
  }

  window.loadAgentSubUsers = function loadAgentView() {
    bindRefreshButton();
    bindMediaPreview();
    ensurePolling();
    loadAgentSubUsers();
    refreshDashboard(true);
  };
})();
