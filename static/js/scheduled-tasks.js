/* global API_BASE, LOCAL_API_BASE, authHeaders, escapeHtml, getOrCreateInstallationId */

(function () {
  'use strict';

  var CAPABILITIES = {
    'goal.video.pipeline': {
      label: '创意成片',
      description: '生成文案、首帧和视频，支持备选素材组。'
    },
    'goal.image.pipeline': {
      label: '文案 + 创意图片',
      description: '生成文案和创意图片，生成图片后结束。'
    },
    'hifly.video.create_by_tts': {
      label: '必火数字人',
      description: '选择本机已有数字人/模板数字人和声音，生成数字人口播视频。'
    },
    'ip_content_daily': {
      label: 'IP日更文案',
      description: '服务器定时同步关键词和同行数据，生成行业口播、专业IP口播、朋友圈文案。'
    },
    'lead_collection_templates': {
      label: '线索采集模板',
      description: '按选中的 Reddit、X、TikTok、LinkedIn 模板定时采集公开信息并汇总结果。'
    }
  };

  var state = {
    bound: false,
    avatarRows: [],
    voiceRows: [],
    candidateGroups: [],
    publishAccounts: [],
    publishAccountsLoaded: false,
    publishAccountsLoading: false,
    wechatMomentsAccounts: [],
    wechatMomentsAccountsLoaded: false,
    wechatMomentsAccountsLoading: false,
    ipTemplates: [],
    ipTemplatesLoaded: false,
    ipTemplatesLoading: false,
    leadTemplates: [],
    leadTemplatesLoaded: false,
    leadTemplatesLoading: false,
    hiflyLoaded: false,
    hiflyLoading: false,
    runsById: {}
  };

  var IP_DAILY_TASK_OPTIONS = [
    { value: 'industry_hot_oral', label: '行业热门口播' },
    { value: 'professional_ip_oral', label: '专业 IP 口播' },
    { value: 'moments_candidate', label: '朋友圈文案' }
  ];

  function base() {
    var local = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
    var remote = (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
    return local || remote;
  }

  function headers(withBody) {
    var h = Object.assign({}, typeof authHeaders === 'function' ? authHeaders() : {});
    if (withBody) h['Content-Type'] = 'application/json';
    else {
      delete h['Content-Type'];
      delete h['content-type'];
    }
    return h;
  }

  function authHeadersNoContentType() {
    var h = Object.assign({}, typeof authHeaders === 'function' ? authHeaders() : {});
    delete h['Content-Type'];
    delete h['content-type'];
    return h;
  }

  function currentInstallationId() {
    return typeof getOrCreateInstallationId === 'function' ? String(getOrCreateInstallationId() || '').trim() : '';
  }

  function friendlyError(err) {
    var msg = err && err.message ? String(err.message) : String(err || '');
    var name = err && err.name ? String(err.name) : '';
    if (name === 'AbortError' || /aborted|abort/i.test(msg)) {
      return '请求超时，请确认本机盒子和云端服务可访问。';
    }
    if (msg === 'Failed to fetch' || /NetworkError/i.test(msg)) {
      return '无法连接定时任务服务，请确认本机盒子已启动并能访问云端。';
    }
    return msg || '请求失败';
  }

  function fetchWithTimeout(url, options, timeoutMs) {
    options = options || {};
    if (typeof AbortController === 'undefined') return fetch(url, options);
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, timeoutMs || 12000);
    var opts = Object.assign({}, options, { signal: controller.signal });
    return fetch(url, opts).then(function (r) {
      clearTimeout(timer);
      return r;
    }, function (e) {
      clearTimeout(timer);
      throw e;
    });
  }

  function html(s) {
    if (typeof escapeHtml === 'function') return escapeHtml(s == null ? '' : String(s));
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function optionHtml(value, label) {
    return '<option value="' + html(value || '') + '">' + html(label || value || '-') + '</option>';
  }

  function api(path, options) {
    var b = base();
    if (!b) return Promise.reject(new Error('任务服务地址未配置'));
    options = options || {};
    var hasBody = options.body != null;
    options.headers = Object.assign(headers(hasBody), options.headers || {});
    return fetchWithTimeout(b + path, options).catch(function (e) {
      throw new Error(friendlyError(e));
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
        return d;
      });
    });
  }

  function postLocal(path, body) {
    return api(path, { method: 'POST', body: JSON.stringify(body || {}) });
  }

  function cloudBase() {
    return (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
  }

  function getCloud(path) {
    var b = cloudBase();
    if (!b) return Promise.reject(new Error('云端服务地址未配置'));
    return fetchWithTimeout(b + path, { method: 'GET', headers: headers(false) }).catch(function (e) {
      throw new Error(friendlyError(e));
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
        return d;
      });
    });
  }

  function fmtTime(v) {
    if (!v) return '-';
    var d = new Date(v);
    if (isNaN(d.getTime())) return String(v).replace('T', ' ').slice(0, 19);
    return d.toLocaleString('zh-CN', { hour12: false });
  }

  function statusText(s) {
    return {
      pending: '等待中',
      processing: '执行中',
      completed: '完成',
      failed: '失败',
      cancelled: '已取消',
      active: '启用',
      paused: '暂停'
    }[s] || s || '-';
  }

  function kindText(k) {
    return {
      openclaw_message: 'OpenClaw 消息',
      chat_message: '本地对话',
      capability: '能力调用',
      ip_content_daily: 'IP日更文案'
    }[k] || k || '-';
  }

  function capabilityText(id) {
    return (CAPABILITIES[id] && CAPABILITIES[id].label) || id || '';
  }

  function resultPayload(row) {
    return row && row.result_payload && typeof row.result_payload === 'object' ? row.result_payload : {};
  }

  function publishDraft(row) {
    var payload = resultPayload(row);
    var draft = payload.publish_draft;
    return draft && typeof draft === 'object' ? draft : null;
  }

  function publishStatusText(status) {
    return {
      ready: '待发布',
      draft: '待发布',
      pending: '等待发布',
      processing: '发布中',
      published: '已发布',
      failed: '发布失败'
    }[String(status || '').toLowerCase()] || '发布';
  }

  function collectMediaUrls(row) {
    var payload = resultPayload(row);
    var refs = payload.result_refs && typeof payload.result_refs === 'object' ? payload.result_refs : {};
    var raw = [];
    if (Array.isArray(payload.media_urls)) raw = raw.concat(payload.media_urls);
    if (Array.isArray(refs.urls)) raw = raw.concat(refs.urls);
    if (rowCapabilityId(row) === 'goal.video.pipeline') {
      var videos = raw.filter(function (u) {
        return /\.(mp4|webm|mov|m4v|avi)(\?|#|$)/i.test(String(u || ''));
      }).slice(0, 1);
      raw = videos.length ? videos : raw.filter(function (u) {
        return /\.(png|jpe?g|webp|gif)(\?|#|$)/i.test(String(u || ''));
      }).slice(0, 4);
    }
    var seen = {};
    return raw.map(function (u) { return String(u || '').trim(); }).filter(function (u) {
      if (!/^https?:\/\//i.test(u) || seen[u]) return false;
      seen[u] = true;
      return true;
    }).slice(0, 4);
  }

  function fileNameFromUrl(url, fallback) {
    try {
      var name = decodeURIComponent(new URL(url).pathname.split('/').pop() || '');
      return name || fallback;
    } catch (e) {
      var clean = String(url || '').split(/[?#]/)[0].split('/').pop() || '';
      return clean || fallback;
    }
  }

  function downloadAnchor(url, label, fallbackName) {
    return '<a href="' + html(url) + '" download="' + html(fileNameFromUrl(url, fallbackName)) + '" target="_blank" rel="noopener noreferrer" style="font-size:0.78rem;">'
      + html(label)
      + '</a>';
  }

  function mediaPreviewHtml(urls) {
    if (!urls || !urls.length) return '';
    return '<div style="display:flex;gap:0.45rem;flex-wrap:wrap;margin-top:0.45rem;">' + urls.map(function (u) {
      var low = u.toLowerCase();
      if (/\.(mp4|webm|mov)(\?|#|$)/.test(low)) {
        return '<div style="display:grid;gap:0.25rem;width:140px;max-width:100%;">'
          + '<video controls src="' + html(u) + '" style="width:140px;max-width:100%;height:86px;object-fit:cover;border-radius:8px;background:#111;"></video>'
          + '<div style="display:flex;gap:0.5rem;flex-wrap:wrap;">'
          + '<a href="' + html(u) + '" target="_blank" rel="noopener noreferrer" style="font-size:0.78rem;">打开</a>'
          + downloadAnchor(u, '下载视频', 'lobster-video.mp4')
          + '</div></div>';
      }
      if (/\.(png|jpe?g|webp|gif)(\?|#|$)/.test(low)) {
        return '<div style="display:grid;gap:0.25rem;width:86px;">'
          + '<a href="' + html(u) + '" target="_blank" rel="noopener noreferrer"><img src="' + html(u) + '" style="width:86px;height:86px;object-fit:cover;border-radius:8px;border:1px solid var(--border);"></a>'
          + downloadAnchor(u, '下载图片', 'lobster-image.png')
          + '</div>';
      }
      if (/\.(pptx?|pdf|docx?|xlsx?)(\?|#|$)/.test(low)) {
        var fallback = /\.(pptx?)(\?|#|$)/.test(low) ? 'lobster-presentation.pptx' : 'lobster-document';
        var label = /\.(pptx?)(\?|#|$)/.test(low) ? '下载PPT' : '下载文件';
        return '<div style="display:flex;align-items:center;gap:0.5rem;min-height:2rem;">'
          + '<span class="meta">文件</span>'
          + downloadAnchor(u, label, fallback)
          + '</div>';
      }
      return '<a href="' + html(u) + '" target="_blank" rel="noopener noreferrer" style="display:inline-flex;align-items:center;min-height:2rem;">打开预览</a>';
    }).join('') + '</div>';
  }

  function formatJson(value) {
    if (value == null || value === '') return '-';
    try {
      return JSON.stringify(value, null, 2);
    } catch (e) {
      return String(value);
    }
  }

  function collectPromptFields(obj) {
    var out = [];
    var seen = [];
    var promptKeys = {
      prompt: true,
      image_prompt: true,
      video_prompt: true,
      visual_prompt: true,
      motion_prompt: true,
      final_prompt: true,
      generated_prompt: true,
      creative_prompt: true,
      goal: true,
      script: true,
      requirements_text: true,
      custom_prompt: true
    };
    function walk(value, path, depth) {
      if (value == null || depth > 6) return;
      if (typeof value === 'object') {
        if (seen.indexOf(value) >= 0) return;
        seen.push(value);
      }
      if (Array.isArray(value)) {
        value.slice(0, 40).forEach(function (item, idx) {
          walk(item, path + '[' + idx + ']', depth + 1);
        });
        return;
      }
      if (typeof value !== 'object') return;
      Object.keys(value).forEach(function (key) {
        var child = value[key];
        var childPath = path ? path + '.' + key : key;
        var low = String(key || '').toLowerCase();
        if (promptKeys[low] && typeof child !== 'object' && String(child || '').trim()) {
          out.push({ path: childPath, text: String(child).trim() });
        }
        walk(child, childPath, depth + 1);
      });
    }
    walk(obj, '', 0);
    return out.slice(0, 24);
  }

  function runResultText(run) {
    return (run && (run.error || run.result_text || (run.progress && (run.progress.text || run.progress.message)))) || '';
  }

  function detailSection(title, bodyHtml) {
    return '<section class="scheduled-run-detail-section">'
      + '<h5>' + html(title) + '</h5>'
      + bodyHtml
      + '</section>';
  }

  function ipTaskLabel(task) {
    return {
      industry_hot_oral: '行业热门口播',
      professional_ip_oral: '专业 IP 口播',
      moments_candidate: '朋友圈文案'
    }[String(task || '')] || task || '文案';
  }

  function ipContentGroupHtml(payload) {
    if (!payload || !payload.ip_content_daily) return '';
    var groups = Array.isArray(payload.groups) ? payload.groups : [];
    if (!groups.length && payload.records_by_task && typeof payload.records_by_task === 'object') {
      groups = Object.keys(payload.records_by_task).map(function (key) {
        return { task: key, records: payload.records_by_task[key] || [] };
      });
    }
    if (!groups.length) return '<p class="meta">暂无 IP 日更文案结果。</p>';
    var openStudioBtn = '<button type="button" class="btn btn-primary btn-sm scheduled-open-ip-studio-btn">打开 IP日更工作台出图</button>';
    return '<div class="scheduled-ip-result">'
      + '<div style="display:flex;justify-content:flex-end;margin-bottom:0.5rem;">' + openStudioBtn + '</div>'
      + groups.map(function (group) {
        var records = Array.isArray(group.records) ? group.records : [];
        return '<div class="scheduled-ip-group">'
          + '<h6>' + html(ipTaskLabel(group.task)) + ' · ' + html(records.length) + '条</h6>'
          + (records.length ? records.map(function (rec, idx) {
            var prompts = Array.isArray(rec.image_prompts) ? rec.image_prompts : [];
            return '<article class="scheduled-ip-record">'
              + '<div class="scheduled-ip-record-title">' + html((idx + 1) + '. ' + (rec.title || '未命名文案')) + '</div>'
              + '<pre>' + html(rec.body || rec.content || '') + '</pre>'
              + (prompts.length ? '<div class="scheduled-ip-prompts">'
                + prompts.map(function (p, pIdx) { return '<div><strong>配图 ' + (pIdx + 1) + '</strong><span>' + html(p) + '</span></div>'; }).join('')
                + '</div>' : (rec.image_prompt ? '<div class="scheduled-ip-prompts"><div><strong>配图</strong><span>' + html(rec.image_prompt) + '</span></div></div>' : ''))
              + '</article>';
          }).join('') : '<p class="meta">本组暂无记录。</p>')
          + '</div>';
      }).join('')
      + '</div>';
  }

  function ensureRunDetailModal() {
    var modal = document.getElementById('scheduledRunDetailModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'scheduledRunDetailModal';
    modal.className = 'modal-mask scheduled-run-detail-mask';
    modal.innerHTML = ''
      + '<div class="modal scheduled-run-detail-modal" role="dialog" aria-modal="true" aria-labelledby="scheduledRunDetailTitle">'
      + '<div class="scheduled-run-detail-head">'
      + '<h4 id="scheduledRunDetailTitle">执行详情</h4>'
      + '<button type="button" class="btn btn-ghost btn-sm" data-scheduled-detail-close="1">关闭</button>'
      + '</div>'
      + '<div id="scheduledRunDetailBody" class="scheduled-run-detail-body"></div>'
      + '</div>';
    document.body.appendChild(modal);
    modal.addEventListener('click', function (evt) {
      if (evt.target === modal || (evt.target && evt.target.closest && evt.target.closest('[data-scheduled-detail-close]'))) {
        closeRunDetailModal();
      }
    });
    return modal;
  }

  function closeRunDetailModal() {
    var modal = document.getElementById('scheduledRunDetailModal');
    if (modal) modal.classList.remove('visible');
  }

  function openRunDetail(runId) {
    var run = state.runsById[String(runId || '')];
    if (!run) return;
    var modal = ensureRunDetailModal();
    var body = document.getElementById('scheduledRunDetailBody');
    if (!body) return;
    var payload = resultPayload(run);
    var urls = collectMediaUrls(run);
    var prompts = collectPromptFields({ request: run.payload || {}, result: payload });
    var metaRows = [
      ['执行时间', fmtTime(run.created_at)],
      ['任务名称', run.title || run.content || run.id],
      ['能力', capabilityText(rowCapabilityId(run)) || kindText(run.task_kind)],
      ['设备', run.installation_id || '任意设备'],
      ['状态', statusText(run.status)],
      ['记录 ID', run.id || '-']
    ];
    var metaHtml = '<div class="scheduled-run-detail-meta">'
      + metaRows.map(function (row) {
        return '<div><span>' + html(row[0]) + '</span><strong>' + html(row[1]) + '</strong></div>';
      }).join('')
      + '</div>';
    var materialHtml = urls.length
      ? '<div class="scheduled-run-detail-material">' + mediaPreviewHtml(urls) + '</div>'
      : '<p class="meta">暂无生成素材。</p>';
    var promptHtml = prompts.length
      ? '<div class="scheduled-run-detail-prompts">' + prompts.map(function (item) {
        return '<div class="scheduled-run-detail-prompt">'
          + '<div>' + html(item.path) + '</div>'
          + '<pre>' + html(item.text) + '</pre>'
          + '</div>';
      }).join('') + '</div>'
      : '<p class="meta">未找到提示词字段。</p>';
    var resultText = runResultText(run);
    var resultHtml = resultText
      ? '<pre class="scheduled-run-detail-pre">' + html(resultText) + '</pre>'
      : '<p class="meta">无错误或文本结果。</p>';
    var ipHtml = ipContentGroupHtml(payload);
    body.innerHTML = metaHtml
      + (ipHtml ? detailSection('IP日更文案', ipHtml) : '')
      + detailSection('生成素材', materialHtml)
      + detailSection('提示词', promptHtml)
      + detailSection('结果 / 错误', resultHtml)
      + detailSection('任务参数', '<pre class="scheduled-run-detail-pre">' + html(formatJson(run.payload || {})) + '</pre>')
      + detailSection('结果数据', '<pre class="scheduled-run-detail-pre">' + html(formatJson(payload || {})) + '</pre>');
    modal.classList.add('visible');
  }

  function openIpContentStudio() {
    closeRunDetailModal();
    if (typeof window.openIpContentStudio === 'function') {
      window.openIpContentStudio();
      return;
    }
    var target = document.querySelector('[data-skill-id="ip-content-studio"], [data-skill="ip-content-studio"], [data-view="ip-content-studio"]');
    if (target && typeof target.click === 'function') {
      target.click();
      return;
    }
    loadIpTemplates(true);
    showMsg('scheduledTaskMsg', '请在技能商店打开 IP日更文案工作台查看和出图。', false);
  }

  function taskActionHtml(task) {
    var status = String((task && task.status) || '').toLowerCase();
    var parts = [];
    if (status !== 'cancelled' && status !== 'completed') {
      var next = status === 'paused' ? 'active' : 'paused';
      var label = status === 'paused' ? '恢复' : '暂停';
      parts.push('<button type="button" class="btn btn-ghost btn-sm scheduled-task-status-btn" data-task-id="'
        + html(task.id)
        + '" data-next-status="' + html(next) + '">'
        + html(label)
        + '</button>');
    }
    parts.push('<button type="button" class="btn btn-ghost btn-sm scheduled-task-delete-btn" data-task-id="'
      + html(task.id)
      + '">删除</button>');
    return parts.join(' ');
  }

  function runActionHtml(run, options) {
    options = options || {};
    var parts = [];
    var draft = publishDraft(run);
    if (options.detail) {
      parts.push('<button type="button" class="btn btn-ghost btn-sm scheduled-run-detail-btn" data-run-id="'
        + html(run && run.id)
        + '">查看详情</button>');
    }
    if (canResumeVideoRun(run)) {
      parts.push('<button type="button" class="btn btn-primary btn-sm scheduled-run-resume-video-btn" data-run-id="'
        + html(run && run.id)
        + '">补发视频</button>');
    }
    if (draft) {
      var status = String(draft.status || 'ready').toLowerCase();
      if (status !== 'published') {
        var disabled = status === 'pending' || status === 'processing';
        parts.push('<button type="button" class="btn btn-ghost btn-sm scheduled-run-publish-btn" data-run-id="'
          + html(run && run.id)
          + '"' + (disabled ? ' disabled' : '') + '>'
          + html(status === 'failed' ? '重新发布' : publishStatusText(status))
          + '</button>');
      } else {
        parts.push('<span class="meta">' + html(publishStatusText(status)) + '</span>');
      }
    }
    parts.push('<button type="button" class="btn btn-ghost btn-sm scheduled-run-delete-btn" data-run-id="'
      + html(run && run.id)
      + '">删除</button>');
    return parts.join(' ');
  }

  function canResumeVideoRun(run) {
    var cid = rowCapabilityId(run);
    if (cid !== 'goal.video.pipeline') return false;
    if (runIsRunning(run)) return false;
    var payload = resultPayload(run);
    if (payload.resume_available) return true;
    var result = payload.mcp_result && typeof payload.mcp_result === 'object' ? payload.mcp_result : {};
    if (result.resume_available || String(result.status || '').toLowerCase() === 'partial_image') return true;
    var urls = collectMediaUrls(run);
    var hasImage = urls.some(function (u) { return /\.(png|jpe?g|webp|gif)(\?|#|$)/i.test(String(u || '')); });
    var hasVideo = urls.some(function (u) { return /\.(mp4|webm|mov|m4v|avi)(\?|#|$)/i.test(String(u || '')); });
    return hasImage && !hasVideo;
  }

  function rowCapabilityId(row) {
    var payload = row && row.payload && typeof row.payload === 'object' ? row.payload : {};
    if (row && row.task_kind === 'ip_content_daily') return 'ip_content_daily';
    return String(payload.capability_id || '');
  }

  function runIsRunning(row) {
    var s = String((row && row.status) || '').toLowerCase();
    return ['pending', 'processing', 'running', 'queued', 'waiting'].indexOf(s) >= 0;
  }

  function getSelected(selectId) {
    var el = document.getElementById(selectId);
    if (!el) return [];
    return Array.prototype.slice.call(el.selectedOptions || []).map(function (o) { return o.value; }).filter(Boolean);
  }

  function showMsg(id, text, bad) {
    var el = document.getElementById(id);
    if (!el) return;
    el.style.display = text ? '' : 'none';
    el.style.color = bad ? '#e74c3c' : '#2f9e66';
    el.textContent = text || '';
  }

  function val(id) {
    var el = document.getElementById(id);
    return el ? String(el.value || '').trim() : '';
  }

  function setVal(id, value) {
    var el = document.getElementById(id);
    if (el) el.value = value == null ? '' : String(value);
  }

  function timezoneOffsetMinutes() {
    return -new Date().getTimezoneOffset();
  }

  function collectDailyTimes(prefix) {
    return Array.prototype.slice.call(document.querySelectorAll('[data-daily-time-prefix="' + prefix + '"]'))
      .map(function (el) { return String(el.value || '').trim(); })
      .filter(Boolean);
  }

  function addDailyTime(prefix, value) {
    var list = document.getElementById(prefix + 'DailyTimesList');
    if (!list) return;
    var row = document.createElement('div');
    row.style.cssText = 'display:grid;grid-template-columns:minmax(0,1fr) auto;gap:0.4rem;align-items:center;';
    var input = document.createElement('input');
    input.type = 'time';
    input.step = '60';
    input.value = value || '';
    input.setAttribute('data-daily-time-prefix', prefix);
    var remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn btn-ghost btn-sm';
    remove.textContent = '-';
    remove.title = '删除时间点';
    remove.addEventListener('click', function () { row.remove(); });
    row.appendChild(input);
    row.appendChild(remove);
    list.appendChild(row);
  }

  function scheduleText(task) {
    if (task && task.schedule_label) return task.schedule_label;
    if (task && task.schedule_type === 'daily_times') {
      var cfg = task.schedule_config || {};
      return '每天 ' + (Array.isArray(cfg.daily_times) ? cfg.daily_times.join('、') : '');
    }
    if (task && task.schedule_type === 'interval') return '每 ' + Math.round((task.interval_seconds || 0) / 60) + ' 分钟';
    return '一次性';
  }

  function updateScheduleFields(prefix) {
    var scheduleType = (document.getElementById(prefix + 'ScheduleType') || {}).value || 'once';
    var intervalBlock = document.getElementById(prefix + 'IntervalBlock');
    var dailyBlock = document.getElementById(prefix + 'DailyTimesBlock');
    var startBlock = document.getElementById(prefix + 'StartAtBlock');
    if (intervalBlock) intervalBlock.style.display = scheduleType === 'interval' ? '' : 'none';
    if (dailyBlock) dailyBlock.style.display = scheduleType === 'daily_times' ? '' : 'none';
    if (startBlock) startBlock.style.display = scheduleType === 'daily_times' ? 'none' : '';
  }

  function parseAssetIds(text) {
    var seen = {};
    var out = [];
    String(text || '').split(/[\s,，;；]+/).forEach(function (raw) {
      var v = String(raw || '').trim();
      if (!v || seen[v]) return;
      seen[v] = true;
      out.push(v);
    });
    return out.slice(0, 20);
  }

  function mergeAssetIds(baseIds, extraIds) {
    var seen = {};
    var out = [];
    (baseIds || []).concat(extraIds || []).forEach(function (raw) {
      var v = String(raw || '').trim();
      if (!v || seen[v]) return;
      seen[v] = true;
      out.push(v);
    });
    return out.slice(0, 20);
  }

  function applyAttachmentAssetIds(body, assetIds) {
    var ids = mergeAssetIds([], assetIds || []);
    if (!ids.length) return body;
    body.payload = body.payload && typeof body.payload === 'object' && !Array.isArray(body.payload) ? body.payload : {};
    body.payload.attachment_asset_ids = mergeAssetIds(body.payload.attachment_asset_ids || [], ids);
    var capPayload = body.payload.payload;
    if (!capPayload || typeof capPayload !== 'object' || Array.isArray(capPayload)) {
      capPayload = {};
      body.payload.payload = capPayload;
    }
    capPayload.attachment_asset_ids = mergeAssetIds(capPayload.attachment_asset_ids || [], ids);
    capPayload.asset_ids = mergeAssetIds(capPayload.asset_ids || [], ids);
    return body;
  }

  function uploadOneAsset(file) {
    var fd = new FormData();
    fd.append('file', file);
    return fetch(base() + '/api/assets/upload', {
      method: 'POST',
      headers: authHeadersNoContentType(),
      body: fd
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error((d && d.detail) || ('上传素材失败: HTTP ' + r.status));
        if (!d || !d.asset_id) throw new Error('上传素材失败：未返回 asset_id');
        return String(d.asset_id);
      });
    });
  }

  var localPreviewUrls = typeof WeakMap !== 'undefined' ? new WeakMap() : null;

  function localFilePreviewUrl(file) {
    if (!file || !file.type || !/^(image|video)\//i.test(file.type) || !window.URL || !URL.createObjectURL) return '';
    if (localPreviewUrls && localPreviewUrls.has(file)) return localPreviewUrls.get(file);
    var url = URL.createObjectURL(file);
    if (localPreviewUrls) localPreviewUrls.set(file, url);
    return url;
  }

  function uploadPreviewMediaHtml(file) {
    var type = String(file && file.type || '');
    var url = localFilePreviewUrl(file);
    if (url && /^image\//i.test(type)) return '<img src="' + html(url) + '" alt="">';
    if (url && /^video\//i.test(type)) return '<video src="' + html(url) + '" muted playsinline preload="metadata"></video>';
    var suffix = String((file && file.name || 'FILE').split('.').pop() || 'FILE').slice(0, 5).toUpperCase();
    return '<span>' + html(suffix) + '</span>';
  }

  function hasVisualUploadPreview(file) {
    return !!(file && file.type && /^(image|video)\//i.test(file.type));
  }

  function renderAttachmentPreview(prefix) {
    var input = document.getElementById(prefix + 'AssetUpload');
    var host = document.getElementById(prefix + 'AssetPreview');
    if (!host) return;
    var files = input && input.files ? Array.prototype.slice.call(input.files) : [];
    if (!files.length) {
      host.innerHTML = '';
      return;
    }
    host.innerHTML = files.map(function (file) {
      var size = file.size ? Math.ceil(file.size / 1024) + 'KB' : '';
      var visual = hasVisualUploadPreview(file);
      return '<div class="scheduled-upload-thumb">' +
        '<div class="scheduled-upload-media">' + uploadPreviewMediaHtml(file) + '</div>' +
        (visual ? '' : '<div class="scheduled-upload-name" title="' + html(file.name || '') + '">' + html(file.name || '未命名文件') + '</div>') +
        '<div class="scheduled-upload-size">' + html(size) + '</div>' +
      '</div>';
    }).join('');
  }

  function collectAttachmentAssetIds(prefix) {
    var typed = parseAssetIds(((document.getElementById(prefix + 'AssetIds') || {}).value || ''));
    var input = document.getElementById(prefix + 'AssetUpload');
    var files = input && input.files ? Array.prototype.slice.call(input.files) : [];
    if (!files.length) return Promise.resolve(typed);
    showMsg(prefix + 'Msg', '正在上传素材…', false);
    return files.reduce(function (p, file) {
      return p.then(function (ids) {
        return uploadOneAsset(file).then(function (assetId) {
          ids.push(assetId);
          showMsg(prefix + 'Msg', '已上传素材 ' + ids.length + '/' + files.length, false);
          return ids;
        });
      });
    }, Promise.resolve([])).then(function (uploaded) {
      return mergeAssetIds(typed, uploaded);
    });
  }

  function compactGrid(inner) {
    return '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:0.5rem 0.65rem;align-items:start;">' + inner + '</div>';
  }

  function fieldHtml(label, control, full) {
    return '<div class="modal-field" style="margin:0;' + (full ? 'grid-column:1/-1;' : '') + '">'
      + '<label>' + html(label) + '</label>' + control + '</div>';
  }

  function inputHtml(id, type, attrs) {
    return '<input id="' + html(id) + '" type="' + html(type || 'text') + '" ' + (attrs || '') + ' style="width:100%;box-sizing:border-box;">';
  }

  function selectHtml(id, options) {
    return '<select id="' + html(id) + '" style="width:100%;padding:0.45rem;">' + options + '</select>';
  }

  function checkboxHtml(id, label, checked) {
    return '<label style="display:flex;align-items:center;gap:0.45rem;min-height:2.35rem;">'
      + '<input id="' + html(id) + '" type="checkbox" ' + (checked ? 'checked' : '') + ' style="width:auto;min-height:auto;">'
      + '<span>' + html(label) + '</span>'
      + '</label>';
  }

  function ipDailyTaskOptionsHtml() {
    return '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0.45rem;">'
      + IP_DAILY_TASK_OPTIONS.map(function (item) {
        return '<label style="display:flex;align-items:center;gap:0.45rem;min-height:2.35rem;padding:0 0.55rem;border:1px solid var(--border);border-radius:8px;background:#fff;">'
          + '<input type="checkbox" data-ip-daily-task="' + html(item.value) + '" checked style="width:auto;min-height:auto;">'
          + '<span>' + html(item.label) + '</span>'
          + '</label>';
      }).join('')
      + '</div>';
  }

  function selectedIpDailyTasks() {
    return Array.prototype.slice.call(document.querySelectorAll('[data-ip-daily-task]'))
      .filter(function (el) { return !!el.checked; })
      .map(function (el) { return String(el.getAttribute('data-ip-daily-task') || '').trim(); })
      .filter(Boolean);
  }

  function textareaHtml(id, rows, placeholder) {
    return '<textarea id="' + html(id) + '" rows="' + html(rows || 3) + '" placeholder="' + html(placeholder || '') + '" style="width:100%;box-sizing:border-box;"></textarea>';
  }

  function platformDisplayName(platform) {
    var p = String(platform || '').trim();
    var names = {
      douyin: '抖音',
      xiaohongshu: '小红书',
      toutiao: '今日头条',
      kuaishou: '快手',
      bilibili: 'B站',
      wechat_moments: '朋友圈图文'
    };
    return names[p] || p || '-';
  }

  function publishAccountLabel(row) {
    if (!row) return '-';
    return (row.platform_name || platformDisplayName(row.platform)) + ' · ' + (row.nickname || row.name || ('账号 #' + row.id));
  }

  function currentPublishAccounts(platform) {
    var p = String(platform || '').trim();
    if (p === 'wechat_moments') return state.wechatMomentsAccounts || [];
    return (state.publishAccounts || []).filter(function (row) {
      return row && (!p || String(row.platform || '') === p);
    });
  }

  function fillPublishPlatformSelect() {
    var sel = document.getElementById('scheduledTaskPublishPlatform');
    if (!sel) return;
    var current = sel.value;
    var seen = {};
    var rows = [];
    (state.publishAccounts || []).forEach(function (row) {
      var p = String(row && row.platform || '').trim();
      if (!p || seen[p]) return;
      seen[p] = true;
      rows.push({ platform: p, name: row.platform_name || platformDisplayName(p) });
    });
    if ((state.wechatMomentsAccounts || []).length && !seen.wechat_moments) {
      seen.wechat_moments = true;
      rows.push({ platform: 'wechat_moments', name: platformDisplayName('wechat_moments') });
    }
    sel.innerHTML = optionHtml('', '不发布，仅生成记录') + rows.map(function (row) {
      return optionHtml(row.platform, row.name);
    }).join('');
    if (current && seen[current]) sel.value = current;
    fillPublishAccountSelect();
  }

  function fillPublishAccountSelect() {
    var sel = document.getElementById('scheduledTaskPublishAccount');
    if (!sel) return;
    var platform = val('scheduledTaskPublishPlatform');
    var current = sel.value;
    var rows = currentPublishAccounts(platform);
    if (!platform) {
      sel.innerHTML = optionHtml('', '先选择发布平台');
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    if (!rows.length) {
      sel.innerHTML = optionHtml('', platform === 'wechat_moments'
        ? (state.wechatMomentsAccountsLoading ? '加载朋友圈账号中...' : '该平台暂无账号')
        : (state.publishAccountsLoading ? '加载账号中...' : '该平台暂无账号'));
      return;
    }
    sel.innerHTML = rows.map(function (row) {
      return optionHtml(row.id, publishAccountLabel(row));
    }).join('');
    if (current && rows.some(function (row) { return String(row.id) === String(current); })) {
      sel.value = current;
    }
  }

  function loadPublishAccounts() {
    if (state.publishAccountsLoaded && state.wechatMomentsAccountsLoaded) return Promise.resolve(state.publishAccounts);
    if (state.publishAccountsLoading || state.wechatMomentsAccountsLoading) return Promise.resolve(state.publishAccounts);
    state.publishAccountsLoading = true;
    state.wechatMomentsAccountsLoading = true;
    fillPublishPlatformSelect();
    return Promise.all([
      api('/api/accounts').catch(function (e) {
        showMsg('scheduledTaskMsg', e.message || '发布账号加载失败', true);
        return { accounts: [] };
      }),
      api('/api/native-wechat/accounts').catch(function () {
        return { items: [] };
      })
    ]).then(function (results) {
      var publishData = results[0] || {};
      var momentsData = results[1] || {};
      state.publishAccounts = Array.isArray(publishData.accounts) ? publishData.accounts : [];
      state.publishAccountsLoaded = true;
      state.wechatMomentsAccounts = Array.isArray(momentsData.items) ? momentsData.items.map(function (row) {
        var accountId = String(row && (row.account_id || row.id) || '').trim();
        if (!accountId) return null;
        return {
          id: accountId,
          account_id: accountId,
          platform: 'wechat_moments',
          platform_name: '朋友圈图文',
          nickname: String(row.name || row.nickname || row.user_id || row.account_id || accountId).trim(),
          name: String(row.name || row.nickname || '').trim(),
          source: String(row.source || '').trim(),
          version: String(row.version || '').trim()
        };
      }).filter(function (row) { return !!row; }) : [];
      state.wechatMomentsAccountsLoaded = true;
      fillPublishPlatformSelect();
      return state.publishAccounts;
    }).catch(function (e) {
      state.publishAccounts = [];
      state.wechatMomentsAccounts = [];
      fillPublishPlatformSelect();
      showMsg('scheduledTaskMsg', e.message || '发布账号加载失败', true);
      return [];
    }).then(function (rows) {
      state.publishAccountsLoading = false;
      state.wechatMomentsAccountsLoading = false;
      fillPublishPlatformSelect();
      return rows;
    }, function (e) {
      state.publishAccountsLoading = false;
      state.wechatMomentsAccountsLoading = false;
      fillPublishPlatformSelect();
      throw e;
    });
  }

  function fillIpTemplateSelect() {
    var sel = document.getElementById('scheduledTaskIpTemplate');
    if (!sel) return;
    var current = sel.value;
    if (state.ipTemplatesLoading) {
      sel.innerHTML = optionHtml('', '模板加载中...');
      return;
    }
    if (!state.ipTemplates.length) {
      sel.innerHTML = optionHtml('', '暂无服务器模板');
      return;
    }
    sel.innerHTML = optionHtml('', '请选择模板') + state.ipTemplates.map(function (row) {
      var k = Array.isArray(row.keyword_ids) ? row.keyword_ids.length : 0;
      var c = Array.isArray(row.competitor_ids) ? row.competitor_ids.length : 0;
      return optionHtml(row.id, (row.name || ('模板 #' + row.id)) + ' · 关键词' + k + ' · 同行' + c);
    }).join('');
    if (current && state.ipTemplates.some(function (row) { return String(row.id) === String(current); })) sel.value = current;
  }

  function loadIpTemplates(force) {
    if (!force && (state.ipTemplatesLoaded || state.ipTemplatesLoading)) {
      fillIpTemplateSelect();
      return Promise.resolve(state.ipTemplates);
    }
    state.ipTemplatesLoading = true;
    fillIpTemplateSelect();
    return getCloud('/api/ip-content/schedule-templates')
      .then(function (d) {
        state.ipTemplates = Array.isArray(d.items) ? d.items : [];
        state.ipTemplatesLoaded = true;
        return state.ipTemplates;
      })
      .catch(function (e) {
        state.ipTemplates = [];
        showMsg('scheduledTaskMsg', e.message || 'IP日更模板加载失败', true);
        return [];
      })
      .then(function (rows) {
        state.ipTemplatesLoading = false;
        fillIpTemplateSelect();
        return rows;
      }, function (e) {
        state.ipTemplatesLoading = false;
        fillIpTemplateSelect();
        throw e;
      });
  }

  function renderIpContentDailyFields(host) {
    host.innerHTML = compactGrid(
      fieldHtml('关键词和同行模板', selectHtml('scheduledTaskIpTemplate', optionHtml('', '模板加载中...')))
      + fieldHtml('生成内容', ipDailyTaskOptionsHtml(), true)
      + fieldHtml('执行前同步', checkboxHtml('scheduledTaskIpSyncBefore', '每次执行前同步关键词和同行新数据', true))
      + fieldHtml(
        '补充要求（可选）',
        textareaHtml('scheduledTaskIpRequirement', 3, '例如：口播更有案例感；朋友圈短句分行、多段落留白、适当 Emoji、强痛点和结果导向；图片干净真实')
      )
      + fieldHtml(
        '模板维护',
        '<button type="button" class="btn btn-ghost btn-sm scheduled-open-ip-studio-btn">打开 IP日更配置</button>',
        true
      )
    );
    loadIpTemplates(true);
  }

  function leadPlatformLabel(platform) {
    var p = String(platform || '').toLowerCase();
    if (p === 'reddit') return 'Reddit';
    if (p === 'x') return 'X';
    if (p === 'tiktok') return 'TikTok';
    if (p === 'linkedin') return 'LinkedIn';
    return p || '-';
  }

  function leadTemplateSummary(row) {
    var payload = (row && row.request_payload) || {};
    var parts = [];
    if ((payload.keywords || []).length) parts.push('关键词 ' + payload.keywords.length);
    if ((payload.accounts || []).length) parts.push('账号 ' + payload.accounts.length);
    if ((payload.communities || []).length) parts.push('社区 ' + payload.communities.length);
    if ((payload.source_keywords || []).length) parts.push('来源 ' + payload.source_keywords.length);
    if ((payload.seed_profile_urls || []).length) parts.push('个人 ' + payload.seed_profile_urls.length);
    if ((payload.seed_company_urls || []).length) parts.push('公司 ' + payload.seed_company_urls.length);
    if ((payload.hashtags || []).length) parts.push('话题 ' + payload.hashtags.length);
    return parts.join(' · ') || '未配置条件';
  }

  function selectedLeadTemplateIds() {
    return Array.prototype.slice.call(document.querySelectorAll('[data-lead-template-id]'))
      .filter(function (el) { return !!el.checked; })
      .map(function (el) { return parseInt(el.getAttribute('data-lead-template-id') || '0', 10); })
      .filter(function (id) { return id > 0; });
  }

  function renderLeadTemplatePicker() {
    var host = document.getElementById('scheduledTaskLeadTemplates');
    if (!host) return;
    if (state.leadTemplatesLoading) {
      host.innerHTML = '<p class="scheduled-inline-hint">模板加载中...</p>';
      return;
    }
    var rows = state.leadTemplates || [];
    if (!rows.length) {
      host.innerHTML = '<p class="scheduled-inline-hint">暂无采集模板，请先到对应采集技能里创建模板。</p>';
      return;
    }
    var grouped = {};
    rows.forEach(function (row) {
      var p = String(row.platform || '').toLowerCase();
      (grouped[p] = grouped[p] || []).push(row);
    });
    var order = ['reddit', 'x', 'tiktok', 'linkedin'];
    host.innerHTML = order.map(function (platform) {
      var items = grouped[platform] || [];
      if (!items.length) return '';
      return '<div class="scheduled-lead-template-group">'
        + '<strong>' + html(leadPlatformLabel(platform)) + '</strong>'
        + items.map(function (row) {
          return '<label class="scheduled-lead-template-row">'
            + '<input type="checkbox" data-lead-template-id="' + html(row.id) + '">'
            + '<span><b>' + html(row.name || ('模板 #' + row.id)) + '</b><small>' + html(leadTemplateSummary(row)) + '</small></span>'
            + '</label>';
        }).join('')
        + '</div>';
    }).join('');
  }

  function loadLeadTemplates(force) {
    if (!force && (state.leadTemplatesLoaded || state.leadTemplatesLoading)) {
      renderLeadTemplatePicker();
      return Promise.resolve(state.leadTemplates);
    }
    state.leadTemplatesLoading = true;
    renderLeadTemplatePicker();
    return getCloud('/api/lead-collection/templates')
      .then(function (d) {
        state.leadTemplates = Array.isArray(d.items) ? d.items : [];
        state.leadTemplatesLoaded = true;
        return state.leadTemplates;
      })
      .catch(function (e) {
        state.leadTemplates = [];
        showMsg('scheduledTaskMsg', e.message || '线索采集模板加载失败', true);
        return [];
      })
      .then(function (rows) {
        state.leadTemplatesLoading = false;
        renderLeadTemplatePicker();
        return rows;
      }, function (e) {
        state.leadTemplatesLoading = false;
        renderLeadTemplatePicker();
        throw e;
      });
  }

  function renderLeadCollectionTemplateFields(host) {
    host.innerHTML = compactGrid(
      fieldHtml('采集模板', '<div id="scheduledTaskLeadTemplates"></div>', true)
      + fieldHtml('模板维护', '<button type="button" class="btn btn-ghost btn-sm scheduled-refresh-lead-templates-btn">刷新模板</button>', true)
    );
    loadLeadTemplates(true);
  }

  function updateGoalVideoSourceMode() {
    var mode = val('scheduledTaskVideoSourceMode') || 'asset_random';
    var groupField = document.getElementById('scheduledTaskCandidateGroupField');
    if (groupField) groupField.style.display = mode === 'ai_image' ? 'none' : '';
  }

  function renderGoalFields(host) {
    host.innerHTML = compactGrid(
      fieldHtml('首帧图片来源', selectHtml('scheduledTaskVideoSourceMode',
        optionHtml('asset_random', '从素材库备选组轮换图片')
        + optionHtml('ai_image', 'AI 生成图片')
      ))
      + '<div id="scheduledTaskCandidateGroupField" style="margin:0;">'
      + fieldHtml('备选素材组', selectHtml('scheduledTaskCandidateGroup', optionHtml('', '加载中...')))
      + '</div>'
      + fieldHtml(
        '提示词（可选）',
        textareaHtml('scheduledTaskCreativePrompt', 3, '填写后直接按这段提示词生成；留空则根据记忆资料自动生成文案和画面方向')
        + '<p class="meta" style="margin:0.35rem 0 0;">留空时沿用记忆资料自动生成；填写后不再先生成本次文案。</p>',
        true
      )
    );
    var modeSel = document.getElementById('scheduledTaskVideoSourceMode');
    if (modeSel) modeSel.addEventListener('change', updateGoalVideoSourceMode);
    updateGoalVideoSourceMode();
    fillCandidateGroupSelect();
    loadCandidateGroups();
  }

  function fillCandidateGroupSelect() {
    var sel = document.getElementById('scheduledTaskCandidateGroup');
    if (!sel) return;
    var current = sel.value;
    if (!state.candidateGroups.length) {
      sel.innerHTML = optionHtml('', '暂无备选组，请先到素材库设置');
      return;
    }
    sel.innerHTML = state.candidateGroups.map(function (row) {
      return optionHtml(row.name, row.name + (row.count ? ('（' + row.count + '张）') : ''));
    }).join('');
    if (current && state.candidateGroups.some(function (row) { return row.name === current; })) sel.value = current;
  }

  function loadCandidateGroups() {
    if (typeof window.loadCreativeCandidateGroups === 'function') {
      return window.loadCreativeCandidateGroups().then(function (groups) {
        state.candidateGroups = Array.isArray(groups) ? groups : [];
        fillCandidateGroupSelect();
      });
    }
    var b = base();
    if (!b) return Promise.resolve();
    return fetchWithTimeout(b + '/api/assets/creative-candidate-groups', { headers: headers(false) }, 12000)
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status)); return d; }); })
      .then(function (d) {
        state.candidateGroups = Array.isArray(d.groups) ? d.groups : [];
        fillCandidateGroupSelect();
      })
      .catch(function () {
        state.candidateGroups = [];
        fillCandidateGroupSelect();
      });
  }

  window.refreshScheduledCreativeGroups = function (groups) {
    state.candidateGroups = Array.isArray(groups) ? groups : [];
    fillCandidateGroupSelect();
  };

  function renderImageFields(host) {
    host.innerHTML = compactGrid(
      fieldHtml('发布平台', selectHtml('scheduledTaskPublishPlatform', optionHtml('', '不发布，仅生成记录')))
      + fieldHtml('发布账号', selectHtml('scheduledTaskPublishAccount', optionHtml('', '先选择发布平台')))
      + fieldHtml(
        '发布方式',
        checkboxHtml('scheduledTaskPublishAuto', '生成后自动发布', false)
        + '<p class="meta" style="margin:0.35rem 0 0;">不勾选时只推送到 H5/小程序/online 记录，之后可手动点击发布。</p>',
        true
      )
      +
      fieldHtml(
        '提示词（可选）',
        textareaHtml('scheduledTaskCreativePrompt', 3, '填写后直接按这段提示词生成图片；留空则根据记忆资料自动生成文案和画面方向')
        + '<p class="meta" style="margin:0.35rem 0 0;">留空时沿用记忆资料自动生成；填写后不再先生成本次文案。</p>',
        true
      )
    );
    var platformSel = document.getElementById('scheduledTaskPublishPlatform');
    if (platformSel) platformSel.addEventListener('change', fillPublishAccountSelect);
    fillPublishPlatformSelect();
    loadPublishAccounts();
  }

  function avatarLabel(row) {
    var parts = [];
    if (row.section_label) parts.push(row.section_label);
    parts.push(row.title || row.avatar || '-');
    return parts.join(' · ');
  }

  function normalizeAvatarRows(rows) {
    var seen = {};
    var out = [];
    (rows || []).forEach(function (row) {
      var id = String(row && row.avatar || '').trim();
      if (!id || seen[id]) return;
      seen[id] = true;
      out.push(row);
    });
    return out;
  }

  function normalizeVoiceRows(rows) {
    var out = [];
    var seen = {};
    (rows || []).forEach(function (row) {
      if (!row) return;
      var styles = Array.isArray(row.styles) && row.styles.length ? row.styles : [row];
      styles.forEach(function (style) {
        var id = String((style && style.voice) || row.voice || '').trim();
        if (!id || /^consumer_/i.test(id) || seen[id]) return;
        seen[id] = true;
        var groupTitle = String(row.title || '').trim();
        var styleLabel = String((style && (style.label || style.title)) || '').trim();
        var label = groupTitle || styleLabel || id;
        if (styleLabel && styleLabel !== '默认风格' && styleLabel !== groupTitle) label += ' · ' + styleLabel;
        if (row.section_label) label = row.section_label + ' · ' + label;
        out.push({ voice: id, label: label });
      });
    });
    return out;
  }

  function fillHiflySelects() {
    var avatarSel = document.getElementById('scheduledTaskHiflyAvatar');
    var voiceSel = document.getElementById('scheduledTaskHiflyVoice');
    if (avatarSel) {
      avatarSel.innerHTML = state.avatarRows.length
        ? state.avatarRows.map(function (row) { return optionHtml(row.avatar, avatarLabel(row)); }).join('')
        : optionHtml('', '暂无可用数字人');
    }
    if (voiceSel) {
      voiceSel.innerHTML = state.voiceRows.length
        ? state.voiceRows.map(function (row) { return optionHtml(row.voice, row.label); }).join('')
        : optionHtml('', '暂无可用声音');
    }
  }

  function loadHiflyLibraries() {
    if (state.hiflyLoaded || state.hiflyLoading) return Promise.resolve();
    state.hiflyLoading = true;
    var avatarSel = document.getElementById('scheduledTaskHiflyAvatar');
    var voiceSel = document.getElementById('scheduledTaskHiflyVoice');
    if (avatarSel) avatarSel.innerHTML = optionHtml('', '加载中...');
    if (voiceSel) voiceSel.innerHTML = optionHtml('', '加载中...');
    return Promise.all([
      getCloud('/api/hifly/my/avatar/list?page=1&size=100').catch(function () { return { items: [] }; }),
      postLocal('/api/hifly/avatar/library', { page: 1, size: 100, include_mine: true }).catch(function () { return { public: [] }; }),
      getCloud('/api/hifly/my/voice/list?page=1&size=100').catch(function () { return { items: [] }; }),
      postLocal('/api/hifly/voice/library', {}).catch(function () { return { public: [] }; })
    ]).then(function (results) {
      var myAvatarData = results[0] || {};
      var avatarData = results[1] || {};
      var myVoiceData = results[2] || {};
      var voiceData = results[3] || {};
      state.avatarRows = normalizeAvatarRows([].concat(myAvatarData.items || [], avatarData.mine || [], avatarData.public || []));
      state.voiceRows = normalizeVoiceRows([].concat(myVoiceData.items || [], voiceData.mine || [], voiceData.public || []));
      state.hiflyLoaded = true;
      fillHiflySelects();
    }).catch(function (e) {
      if (avatarSel) avatarSel.innerHTML = optionHtml('', '数字人加载失败');
      if (voiceSel) voiceSel.innerHTML = optionHtml('', '声音加载失败');
      showMsg('scheduledTaskMsg', e.message || '必火数字人资源加载失败', true);
    }).then(function () {
      state.hiflyLoading = false;
    }, function (e) {
      state.hiflyLoading = false;
      throw e;
    });
  }

  function renderHiflyFields(host) {
    host.innerHTML = compactGrid(
      fieldHtml('数字人', selectHtml('scheduledTaskHiflyAvatar', optionHtml('', '加载中...')))
      + fieldHtml('声音', selectHtml('scheduledTaskHiflyVoice', optionHtml('', '加载中...')))
    );
    fillHiflySelects();
    loadHiflyLibraries();
  }

  function renderParamFields() {
    var host = document.getElementById('scheduledTaskParamFields');
    if (!host) return;
    var capabilityId = val('scheduledTaskCapability') || 'goal.video.pipeline';
    if (capabilityId === 'ip_content_daily') renderIpContentDailyFields(host);
    else if (capabilityId === 'lead_collection_templates') renderLeadCollectionTemplateFields(host);
    else if (capabilityId === 'hifly.video.create_by_tts') renderHiflyFields(host);
    else if (capabilityId === 'goal.image.pipeline') renderImageFields(host);
    else renderGoalFields(host);
  }

  function loadCapabilities() {
    var sel = document.getElementById('scheduledTaskCapability');
    if (!sel) return Promise.resolve();
    var current = sel.value;
    sel.innerHTML = [
      'goal.image.pipeline',
      'lead_collection_templates',
      'ip_content_daily',
      'goal.video.pipeline',
      'hifly.video.create_by_tts'
    ].map(function (id) {
      return optionHtml(id, (CAPABILITIES[id] || {}).label || id);
    }).join('');
    sel.value = CAPABILITIES[current] ? current : 'goal.image.pipeline';
    toggleCapability();
    return Promise.resolve();
  }

  function toggleCapability() {
    var capabilityId = val('scheduledTaskCapability') || 'goal.video.pipeline';
    var hint = document.getElementById('scheduledTaskCapabilityHint');
    if (hint) hint.textContent = (CAPABILITIES[capabilityId] || {}).description || '';
    var paramBlock = document.getElementById('scheduledTaskParamBlock');
    if (paramBlock) paramBlock.style.display = '';
    var autoFill = document.getElementById('scheduledTaskAutoFillBtn');
    if (autoFill) autoFill.style.display = capabilityId === 'hifly.video.create_by_tts' ? '' : 'none';
    renderParamFields();
  }

  function autoFillParams() {
    var capabilityId = val('scheduledTaskCapability') || 'goal.video.pipeline';
    if (capabilityId === 'hifly.video.create_by_tts') {
      if (!val('scheduledTaskHiflyAvatar') && state.avatarRows[0]) setVal('scheduledTaskHiflyAvatar', state.avatarRows[0].avatar);
      if (!val('scheduledTaskHiflyVoice') && state.voiceRows[0]) setVal('scheduledTaskHiflyVoice', state.voiceRows[0].voice);
      loadHiflyLibraries();
    }
  }

  function selectedIpTemplate() {
    var id = val('scheduledTaskIpTemplate');
    if (!id) return null;
    return (state.ipTemplates || []).find(function (row) { return String(row.id) === String(id); }) || null;
  }

  function collectCapabilityPayload() {
    var capabilityId = val('scheduledTaskCapability') || 'goal.video.pipeline';
    if (capabilityId === 'ip_content_daily') {
      var template = selectedIpTemplate();
      var templateId = parseInt(val('scheduledTaskIpTemplate') || '0', 10);
      if (!templateId || isNaN(templateId)) throw new Error('请选择 IP日更服务器模板');
      var tasks = selectedIpDailyTasks();
      if (!tasks.length) throw new Error('请选择至少一种生成内容');
      var extra = val('scheduledTaskIpRequirement');
      var requirements = {};
      if (extra) {
        requirements.common = extra;
        requirements.oral = extra;
        requirements.moments = extra;
        requirements.image = extra;
      }
      return {
        template_id: templateId,
        template_name: template ? (template.name || '') : '',
        tasks: tasks,
        sync_before: !!(document.getElementById('scheduledTaskIpSyncBefore') || {}).checked,
        requirements: requirements,
        industry_count: 5,
        ip_count: 5,
        moments_count: 20
      };
    }
    if (capabilityId === 'lead_collection_templates') {
      var templateIds = selectedLeadTemplateIds();
      if (!templateIds.length) throw new Error('请选择至少一个线索采集模板');
      return {
        template_ids: templateIds,
        title: val('scheduledTaskTitle') || '线索采集模板定时任务'
      };
    }
    if (capabilityId === 'hifly.video.create_by_tts') {
      var avatar = val('scheduledTaskHiflyAvatar');
      var voice = val('scheduledTaskHiflyVoice');
      if (!avatar) throw new Error('请选择数字人');
      if (!voice) throw new Error('请选择声音');
      return {
        avatar: avatar,
        voice: voice
      };
    }
    if (capabilityId === 'goal.video.pipeline') {
      var sourceMode = val('scheduledTaskVideoSourceMode') || 'asset_random';
      var group = val('scheduledTaskCandidateGroup');
      var prompt = val('scheduledTaskCreativePrompt');
      if (sourceMode !== 'ai_image' && !group) throw new Error('请选择创意成片备选素材组');
      return {
        source_mode: sourceMode,
        candidate_group: sourceMode === 'ai_image' ? '' : group,
        prompt: prompt
      };
    }
    if (capabilityId === 'goal.image.pipeline') {
      var publishPlatform = val('scheduledTaskPublishPlatform');
      var publishAccountId = val('scheduledTaskPublishAccount');
      var autoPublish = !!(document.getElementById('scheduledTaskPublishAuto') || {}).checked;
      var publishAccount = null;
      if (publishAccountId) {
        var publishSourceRows = publishPlatform === 'wechat_moments' ? (state.wechatMomentsAccounts || []) : (state.publishAccounts || []);
        publishAccount = publishSourceRows.find(function (row) {
          return row && String(row.id) === String(publishAccountId);
        }) || null;
      }
      var payload = {
        prompt: val('scheduledTaskCreativePrompt')
      };
      if (publishPlatform || publishAccountId || autoPublish) {
        if (!publishPlatform) throw new Error('请选择发布平台');
        if (!publishAccountId) throw new Error('请选择发布账号');
        payload.publish_platform = publishPlatform;
        payload.publish_platform_name = publishAccount ? (publishAccount.platform_name || platformDisplayName(publishPlatform)) : platformDisplayName(publishPlatform);
        payload.publish_account_id = publishPlatform === 'wechat_moments' ? publishAccountId : (function() {
          var parsedAccountId = parseInt(publishAccountId, 10);
          if (isNaN(parsedAccountId)) throw new Error('发布账号无效');
          return parsedAccountId;
        })();
        payload.publish_account_nickname = publishAccount ? (publishAccount.nickname || publishAccount.name || '') : '';
        payload.publish_auto = autoPublish;
      }
      return {
        prompt: payload.prompt,
        publish_platform: payload.publish_platform,
        publish_platform_name: payload.publish_platform_name,
        publish_account_id: payload.publish_account_id,
        publish_account_nickname: payload.publish_account_nickname,
        publish_auto: payload.publish_auto
      };
    }
    return {};
  }

  function buildPayload(prefix) {
    var kind = 'capability';
    var capabilityId = val(prefix + 'Capability') || 'goal.video.pipeline';
    var scheduleType = (document.getElementById(prefix + 'ScheduleType') || {}).value || 'once';
    var intervalMin = parseInt((document.getElementById(prefix + 'IntervalMinutes') || {}).value || '60', 10);
    var startAt = val(prefix + 'StartAt');
    var dailyTimes = collectDailyTimes(prefix);
    var installationIds = prefix === 'scheduledTask' ? [currentInstallationId()].filter(Boolean) : getSelected(prefix + 'Devices');
    var title = val(prefix + 'Title') || (capabilityText(capabilityId) || '能力定时任务');
    var capPayload = collectCapabilityPayload();
    if (scheduleType === 'daily_times' && !dailyTimes.length) throw new Error('请填写每天执行时间，例如 9,12,18 或 09:00,12:00,18:00');
    if (capabilityId === 'ip_content_daily') {
      kind = 'ip_content_daily';
      installationIds = [];
    }
    if (capabilityId === 'lead_collection_templates') {
      kind = 'lead_collection_templates';
      installationIds = [];
    }
    var publishPlatform = val('scheduledTaskPublishPlatform');
    var publishAccountId = val('scheduledTaskPublishAccount');
    var publishAccount = null;
    if (publishAccountId) {
      var publishSourceRows = publishPlatform === 'wechat_moments' ? (state.wechatMomentsAccounts || []) : (state.publishAccounts || []);
      publishAccount = publishSourceRows.find(function (row) {
        return row && String(row.id) === String(publishAccountId);
      }) || null;
    }
    var body = {
      title: title,
      task_kind: kind,
      content: capabilityId === 'ip_content_daily'
        ? '定时生成 IP日更文案'
        : (capabilityId === 'lead_collection_templates' ? '定时执行线索采集模板' : '定时调用能力 ' + capabilityId),
      payload: capabilityId === 'ip_content_daily' || capabilityId === 'lead_collection_templates'
        ? capPayload
        : { capability_id: capabilityId, payload: capPayload },
      schedule_type: scheduleType,
      timezone_offset_minutes: timezoneOffsetMinutes(),
      installation_ids: installationIds
    };
    if (startAt && scheduleType !== 'daily_times') body.start_at = startAt;
    if (scheduleType === 'interval') body.interval_seconds = Math.max(60, (isNaN(intervalMin) ? 60 : intervalMin) * 60);
    if (scheduleType === 'daily_times') body.daily_times = dailyTimes;
    if (publishPlatform || publishAccountId || autoPublish) {
      if (!publishPlatform) throw new Error('请选择发布平台');
      if (!publishAccountId) throw new Error('请选择发布账号');
      body.publish_platform = publishPlatform;
      body.publish_platform_name = publishAccount ? (publishAccount.platform_name || platformDisplayName(publishPlatform)) : platformDisplayName(publishPlatform);
      body.publish_account_nickname = publishAccount ? (publishAccount.nickname || publishAccount.name || '') : '';
      body.publish_auto = autoPublish;
      if (publishPlatform === 'wechat_moments') {
        body.publish_account_id = publishAccountId;
      } else {
        var parsedAccountId = parseInt(publishAccountId, 10);
        if (isNaN(parsedAccountId)) throw new Error('发布账号无效');
        body.publish_account_id = parsedAccountId;
      }
    }
    return body;
  }

  function renderRuns(rows, targetId, emptyText) {
    var el = document.getElementById(targetId || 'scheduledTaskRunningRunsList');
    if (!el) return;
    if (!rows || !rows.length) {
      el.innerHTML = '<p class="meta">' + html(emptyText || '暂无执行记录。') + '</p>';
      return;
    }
    var h = '<div style="overflow:auto;"><table style="width:100%;border-collapse:collapse;font-size:0.82rem;">'
      + '<thead><tr style="text-align:left;border-bottom:1px solid var(--border);">'
      + '<th style="padding:0.5rem;">时间</th><th style="padding:0.5rem;">任务</th><th style="padding:0.5rem;">类型</th>'
      + '<th style="padding:0.5rem;">设备</th><th style="padding:0.5rem;">状态</th><th style="padding:0.5rem;">结果/错误</th><th style="padding:0.5rem;">操作</th>'
      + '</tr></thead><tbody>';
    rows.forEach(function (r) {
      var result = r.error || r.result_text || (r.progress && (r.progress.text || r.progress.message)) || '';
      var previews = mediaPreviewHtml(collectMediaUrls(r));
      h += '<tr style="border-bottom:1px solid rgba(255,255,255,0.08);">'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(fmtTime(r.created_at)) + '</td>'
        + '<td style="padding:0.5rem;">' + html(r.title || r.content || r.id) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(capabilityText(rowCapabilityId(r)) || kindText(r.task_kind)) + '</td>'
        + '<td style="padding:0.5rem;font-size:0.75rem;color:var(--text-muted);">' + html(r.installation_id || '任意设备') + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(statusText(r.status)) + '</td>'
        + '<td style="padding:0.5rem;max-width:26rem;white-space:pre-wrap;word-break:break-word;">' + html(result || '-') + previews + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + runActionHtml(r) + '</td>'
        + '</tr>';
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
  }

  function renderFinishedRuns(rows) {
    var el = document.getElementById('scheduledTaskFinishedRunsList');
    if (!el) return;
    if (!rows || !rows.length) {
      el.innerHTML = '<p class="meta">暂无执行结束任务。</p>';
      return;
    }
    var h = '<div class="scheduled-finished-run-list">';
    rows.forEach(function (r) {
      var urls = collectMediaUrls(r);
      var previews = mediaPreviewHtml(urls);
      h += '<article class="scheduled-finished-run-card">'
        + '<div class="scheduled-finished-run-main">'
        + '<div class="scheduled-finished-run-topline">'
        + '<span>' + html(fmtTime(r.created_at)) + '</span>'
        + '<span>' + html(statusText(r.status)) + '</span>'
        + '</div>'
        + '<div class="scheduled-finished-run-title">' + html(r.title || r.content || r.id) + '</div>'
        + '<div class="scheduled-finished-run-meta">'
        + '<span>' + html(capabilityText(rowCapabilityId(r)) || kindText(r.task_kind)) + '</span>'
        + '<span>' + html(r.installation_id || '任意设备') + '</span>'
        + '</div>'
        + '<div class="scheduled-finished-run-assets">'
        + (previews || '<span class="meta">暂无生成素材</span>')
        + '</div>'
        + '</div>'
        + '<div class="scheduled-finished-run-actions">' + runActionHtml(r, { detail: true }) + '</div>'
        + '</article>';
    });
    h += '</div>';
    el.innerHTML = h;
  }

  function renderTasks(rows) {
    var el = document.getElementById('scheduledTaskList');
    if (!el) return;
    if (!rows || !rows.length) {
      el.innerHTML = '<p class="meta">暂无任务。</p>';
      return;
    }
    var h = '<div style="overflow:auto;"><table style="width:100%;border-collapse:collapse;font-size:0.82rem;">'
      + '<thead><tr style="text-align:left;border-bottom:1px solid var(--border);">'
      + '<th style="padding:0.5rem;">任务</th><th style="padding:0.5rem;">类型</th><th style="padding:0.5rem;">调度</th>'
      + '<th style="padding:0.5rem;">状态</th><th style="padding:0.5rem;">下次执行</th><th style="padding:0.5rem;">次数</th><th style="padding:0.5rem;">操作</th>'
      + '</tr></thead><tbody>';
    rows.forEach(function (t) {
      h += '<tr style="border-bottom:1px solid rgba(255,255,255,0.08);">'
        + '<td style="padding:0.5rem;">' + html(t.title || t.content || t.id) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(capabilityText(rowCapabilityId(t)) || kindText(t.task_kind)) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(scheduleText(t)) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(statusText(t.status)) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + html(fmtTime(t.next_run_at)) + '</td>'
        + '<td style="padding:0.5rem;">' + (t.run_count || 0) + '</td>'
        + '<td style="padding:0.5rem;white-space:nowrap;">' + taskActionHtml(t) + '</td>'
        + '</tr>';
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
  }

  function updateTaskStatus(taskId, status, btn) {
    if (!taskId || !status) return;
    if (btn) {
      btn.disabled = true;
      btn.textContent = status === 'paused' ? '暂停中...' : '恢复中...';
    }
    api('/api/scheduled-tasks/tasks/' + encodeURIComponent(taskId), {
      method: 'PATCH',
      body: JSON.stringify({ status: status })
    }).then(function () {
      loadTasks();
      loadRuns();
    }).catch(function (e) {
      showMsg('scheduledTaskMsg', e.message, true);
      if (btn) {
        btn.disabled = false;
        btn.textContent = status === 'paused' ? '暂停' : '恢复';
      }
    });
  }

  function deleteTask(taskId, btn) {
    if (!taskId) return;
    if (!window.confirm('删除前会先停止任务，并取消未完成的执行记录。确认删除？')) return;
    if (btn) {
      btn.disabled = true;
      btn.textContent = '删除中...';
    }
    api('/api/scheduled-tasks/tasks/' + encodeURIComponent(taskId), {
      method: 'DELETE'
    }).then(function () {
      showMsg('scheduledTaskMsg', '已停止并删除任务', false);
      loadTasks();
      loadRuns();
    }).catch(function (e) {
      showMsg('scheduledTaskMsg', e.message, true);
      if (btn) {
        btn.disabled = false;
        btn.textContent = '删除';
      }
    });
  }

  function deleteRun(runId, btn) {
    if (!runId) return;
    if (!window.confirm('确认删除这条执行记录？执行中的记录请等待完成或先删除任务。')) return;
    if (btn) {
      btn.disabled = true;
      btn.textContent = '删除中...';
    }
    api('/api/scheduled-tasks/runs/' + encodeURIComponent(runId), {
      method: 'DELETE'
    }).then(function () {
      showMsg('scheduledTaskMsg', '已删除执行记录', false);
      loadRuns();
    }).catch(function (e) {
      showMsg('scheduledTaskMsg', e.message, true);
      if (btn) {
        btn.disabled = false;
        btn.textContent = '删除';
      }
    });
  }

  function requestPublishRun(runId, btn) {
    if (!runId) return;
    if (btn) {
      btn.disabled = true;
      btn.textContent = '提交发布中...';
    }
    api('/api/scheduled-tasks/runs/' + encodeURIComponent(runId) + '/publish-request', {
      method: 'POST',
      body: JSON.stringify({})
    }).then(function () {
      showMsg('scheduledTaskMsg', '已提交发布，本机 online 将使用已绑定账号执行发布', false);
      loadRuns();
    }).catch(function (e) {
      showMsg('scheduledTaskMsg', e.message || '提交发布失败', true);
      if (btn) {
        btn.disabled = false;
        btn.textContent = '发布';
      }
    });
  }

  function resumeVideoRun(runId, btn) {
    if (!runId) return;
    if (btn) {
      btn.disabled = true;
      btn.textContent = '补发中...';
    }
    api('/api/scheduled-tasks/runs/' + encodeURIComponent(runId) + '/resume-video', {
      method: 'POST',
      body: JSON.stringify({})
    }).then(function () {
      showMsg('scheduledTaskMsg', '已重新排队，将跳过生图步骤补发视频', false);
      loadRuns();
    }).catch(function (e) {
      showMsg('scheduledTaskMsg', e.message || '补发视频失败', true);
      if (btn) {
        btn.disabled = false;
        btn.textContent = '补发视频';
      }
    });
  }

  function renderRunsByStatus(rows) {
    rows = Array.isArray(rows) ? rows : [];
    state.runsById = {};
    rows.forEach(function (row) {
      if (row && row.id != null) state.runsById[String(row.id)] = row;
    });
    var running = rows.filter(runIsRunning);
    var finished = rows.filter(function (row) { return !runIsRunning(row); });
    renderRuns(running, 'scheduledTaskRunningRunsList', '暂无执行中的任务。');
    renderFinishedRuns(finished);
  }

  function loadRuns() {
    var runningEl = document.getElementById('scheduledTaskRunningRunsList');
    var finishedEl = document.getElementById('scheduledTaskFinishedRunsList');
    if (runningEl) runningEl.innerHTML = '<p class="meta">加载中...</p>';
    if (finishedEl) finishedEl.innerHTML = '<p class="meta">加载中...</p>';
    return api('/api/scheduled-tasks/runs?limit=80').then(function (d) {
      renderRunsByStatus(d.runs || []);
    }).catch(function (e) {
      var errHtml = '<p class="meta" style="color:#e74c3c;">' + html(e.message) + '</p>';
      if (runningEl) runningEl.innerHTML = errHtml;
      if (finishedEl) finishedEl.innerHTML = errHtml;
    });
  }

  function loadTasks() {
    var el = document.getElementById('scheduledTaskList');
    if (el) el.innerHTML = '<p class="meta">加载中...</p>';
    return api('/api/scheduled-tasks/tasks?limit=80').then(function (d) {
      renderTasks(d.tasks || []);
    }).catch(function (e) {
      if (el) el.innerHTML = '<p class="meta" style="color:#e74c3c;">' + html(e.message) + '</p>';
    });
  }

  function createTask() {
    var btn = document.getElementById('scheduledTaskCreateBtn');
    if (btn) { btn.disabled = true; btn.textContent = '提交中...'; }
    showMsg('scheduledTaskMsg', '', false);
    var body;
    try {
      body = buildPayload('scheduledTask');
    } catch (e) {
      showMsg('scheduledTaskMsg', e.message, true);
      if (btn) { btn.disabled = false; btn.textContent = '添加并下发'; }
      return;
    }
    if (body.task_kind !== 'ip_content_daily' && body.task_kind !== 'lead_collection_templates' && (!body.installation_ids || !body.installation_ids.length)) {
      showMsg('scheduledTaskMsg', '未获取到本机设备标识，请刷新页面或重启本机盒子后再试。', true);
      if (btn) { btn.disabled = false; btn.textContent = '添加并下发'; }
      return;
    }
    collectAttachmentAssetIds('scheduledTask').then(function (assetIds) {
      applyAttachmentAssetIds(body, assetIds);
      return api('/api/scheduled-tasks/tasks', { method: 'POST', body: JSON.stringify(body) });
    }).then(function () {
      showMsg('scheduledTaskMsg', '已创建并下发', false);
      var upload = document.getElementById('scheduledTaskAssetUpload');
      if (upload) upload.value = '';
      renderAttachmentPreview('scheduledTask');
      loadRuns();
      loadTasks();
    }).catch(function (e) {
      showMsg('scheduledTaskMsg', e.message, true);
    }).then(function () {
      if (btn) { btn.disabled = false; btn.textContent = '添加并下发'; }
    }, function () {
      if (btn) { btn.disabled = false; btn.textContent = '添加并下发'; }
    });
  }

  function bind() {
    if (state.bound) return;
    state.bound = true;
    var deviceField = document.getElementById('scheduledTaskDevices');
    if (deviceField && deviceField.closest) {
      deviceField.closest('.modal-field').style.display = 'none';
    }
    document.querySelectorAll('.sched-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        var name = tab.getAttribute('data-sched-tab');
        document.querySelectorAll('.sched-tab').forEach(function (x) { x.classList.toggle('active', x === tab); });
        ['create', 'running', 'finished'].forEach(function (key) {
          var panel = document.getElementById('schedTab' + key.charAt(0).toUpperCase() + key.slice(1));
          if (panel) panel.style.display = name === key ? '' : 'none';
        });
        if (name === 'create') loadTasks(); else loadRuns();
      });
    });
    var refresh = document.getElementById('scheduledTaskRefreshBtn');
    if (refresh) refresh.addEventListener('click', function () { loadRuns(); loadTasks(); });
    var create = document.getElementById('scheduledTaskCreateBtn');
    if (create) create.addEventListener('click', createTask);
    document.addEventListener('click', function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest('.scheduled-task-status-btn') : null;
      if (!btn) return;
      updateTaskStatus(btn.getAttribute('data-task-id'), btn.getAttribute('data-next-status'), btn);
    });
    document.addEventListener('click', function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest('.scheduled-task-delete-btn') : null;
      if (!btn) return;
      deleteTask(btn.getAttribute('data-task-id'), btn);
    });
    document.addEventListener('click', function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest('.scheduled-run-delete-btn') : null;
      if (!btn) return;
      deleteRun(btn.getAttribute('data-run-id'), btn);
    });
    document.addEventListener('click', function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest('.scheduled-run-detail-btn') : null;
      if (!btn) return;
      openRunDetail(btn.getAttribute('data-run-id'));
    });
    document.addEventListener('click', function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest('.scheduled-run-publish-btn') : null;
      if (!btn) return;
      requestPublishRun(btn.getAttribute('data-run-id'), btn);
    });
    document.addEventListener('click', function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest('.scheduled-run-resume-video-btn') : null;
      if (!btn) return;
      resumeVideoRun(btn.getAttribute('data-run-id'), btn);
    });
    document.addEventListener('click', function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest('.scheduled-open-ip-studio-btn') : null;
      if (!btn) return;
      openIpContentStudio();
    });
    document.addEventListener('click', function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest('.scheduled-refresh-lead-templates-btn') : null;
      if (!btn) return;
      loadLeadTemplates(true);
    });
    var kind = document.getElementById('scheduledTaskKind');
    if (kind) kind.value = 'capability';
    var capability = document.getElementById('scheduledTaskCapability');
    if (capability) capability.addEventListener('change', toggleCapability);
    var scheduleType = document.getElementById('scheduledTaskScheduleType');
    if (scheduleType) scheduleType.addEventListener('change', function () { updateScheduleFields('scheduledTask'); });
    document.addEventListener('click', function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest('.daily-time-add-btn') : null;
      if (!btn) return;
      var prefix = btn.getAttribute('data-prefix') || 'scheduledTask';
      if (prefix !== 'scheduledTask') return;
      addDailyTime(prefix);
    });
    var autoFill = document.getElementById('scheduledTaskAutoFillBtn');
    if (autoFill) autoFill.addEventListener('click', autoFillParams);
    var assetUpload = document.getElementById('scheduledTaskAssetUpload');
    if (assetUpload) assetUpload.addEventListener('change', function () { renderAttachmentPreview('scheduledTask'); });
    updateScheduleFields('scheduledTask');
    if (!collectDailyTimes('scheduledTask').length) addDailyTime('scheduledTask');
  }

  window.initScheduledTasksView = function initScheduledTasksView() {
    bind();
    loadCapabilities();
    loadRuns();
    loadTasks();
  };

  window.__scheduledTasksApi = {
    api: api,
    buildPayload: buildPayload,
    loadCapabilities: loadCapabilities,
    loadHiflyLibraries: loadHiflyLibraries,
    autoFillParams: autoFillParams,
    parseAssetIds: parseAssetIds,
    applyAttachmentAssetIds: applyAttachmentAssetIds
  };
})();
