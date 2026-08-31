(function() {
  var PENDING_TEMPLATE_STORAGE_KEY = 'lobster_multi_clip_pending_cutcli_template_v1';
  var HISTORY_STORAGE_KEY = 'lobster_multi_clip_history_v1';
  var HISTORY_LIMIT = 50;
  var HISTORY_PAGE_SIZE = 6;

  var state = {
    clips: [],
    pendingFiles: [],
    segmentContext: null,
    templates: [],
    templateCatalog: [],
    templateCatalogProvider: '',
    selectedTemplate: null,
    shanjianTemplateDetail: null,
    shanjianTemplateDetailId: '',
    shanjianTemplateDetailPromise: null,
    shanjianTemplateDetailError: '',
    overlayTexts: {},
    positionOverrides: {},
    musicOptions: [],
    selectedMusic: null,
    templateProvider: 'local',
    busy: false,
    resultObjectUrl: '',
    previewAudio: null,
    lastBaseResult: null,
    templatePollTimer: null,
    pendingTemplateTask: null,
    batchResults: [],
    batchSelection: {},
    history: [],
    historySelection: {},
    historyPage: 0,
    historyVisible: false,
    activeHistoryBatch: null,
    importingFiles: false
  };

  function $(id) { return document.getElementById(id); }

  function apiBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  }

  function cloudBaseUrl() {
    var api = (typeof API_BASE !== 'undefined' ? (API_BASE || '') : '').replace(/\/$/, '');
    if (api && !/^https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?(?:\/|$)/i.test(api) && api !== apiBase()) return api;
    return (typeof LOBSTER_SERVER_PUBLIC !== 'undefined'
      ? String(LOBSTER_SERVER_PUBLIC || '').replace(/\/$/, '')
      : api);
  }

  function shanjianApiBase(path) {
    var cloud = cloudBaseUrl();
    if (String(path || '').indexOf('/api/shanjian-') === 0 && cloud) return cloud;
    return apiBase();
  }

  function requestBase(path) {
    if (String(path || '').indexOf('/api/shanjian-') === 0) return shanjianApiBase(path);
    return apiBase();
  }

  function headers(json) {
    var result = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (json) result['Content-Type'] = 'application/json';
    else { delete result['Content-Type']; delete result['content-type']; }
    return result;
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function(ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function requestTo(base, path, options, allowErrorPayload) {
    return fetch(base + path, options || {}).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok || (!allowErrorPayload && data.ok === false)) {
          var detail = data.detail || data.error || data.message || ('请求失败：HTTP ' + response.status);
          throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        return data;
      });
    });
  }

  function request(path, options, allowErrorPayload) {
    if (String(path || '').indexOf('/api/shanjian-') === 0) {
      return requestTo(shanjianApiBase(path), path, options, allowErrorPayload);
    }
    return requestTo(requestBase(path), path, options, allowErrorPayload);
  }

  function post(path, body, allowErrorPayload) {
    return request(path, {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify(body || {})
    }, allowErrorPayload);
  }

  function formatSeconds(value) {
    var seconds = Number(value || 0);
    if (!Number.isFinite(seconds)) seconds = 0;
    return (Math.round(seconds * 10) / 10).toFixed(seconds % 1 ? 1 : 0) + ' 秒';
  }

  function showMessage(text, isError) {
    var element = $('mcmMessage');
    if (!element) return;
    element.hidden = !text;
    element.textContent = text || '';
    element.classList.toggle('is-error', !!isError);
  }

  function setBusy(busy, label) {
    state.busy = !!busy;
    var generate = $('mcmGenerateBtn');
    if (generate) {
      generate.disabled = state.busy;
      generate.textContent = state.busy ? (label || '正在生成...') : '生成混剪视频';
    }
    var add = $('mcmAddVideoBtn');
    if (add) add.disabled = state.busy;
  }

  function readFixedSegment(duration) {
    var start = Number((($('mcmFixedStart') || {}).value || 0));
    var end = Number((($('mcmFixedEnd') || {}).value || 3));
    if (!Number.isFinite(start) || start < 0) start = 0;
    if (!Number.isFinite(end) || end <= start) end = start + 3;
    var span = Math.max(0.1, end - start);
    if (Number.isFinite(duration) && duration > 0) {
      if (start >= duration) start = Math.max(0, duration - Math.min(span, duration));
      end = Math.min(duration, start + span);
      if (end <= start) {
        start = 0;
        end = Math.min(duration, span);
      }
    }
    return { start: Math.round(start * 10) / 10, end: Math.round(end * 10) / 10 };
  }

  function defaultSegment(index, duration) {
    return readFixedSegment(duration);
  }

  function currentClipMode() {
    var checked = document.querySelector('input[name="mcmClipMode"]:checked');
    return checked && checked.value === 'random' ? 'random' : 'fixed';
  }

  function currentTemplateProvider() {
    var checked = document.querySelector('input[name="mcmTemplateProvider"]:checked');
    state.templateProvider = checked && checked.value === 'shanjian' ? 'shanjian' : 'local';
    return state.templateProvider;
  }

  function randomChoice(items) {
    var list = Array.isArray(items) ? items.filter(Boolean) : [];
    if (!list.length) return null;
    return list[Math.floor(Math.random() * list.length)];
  }

  function markedAudioClip() {
    return state.clips.find(function(clip) { return !!clip.audioMaster; }) || null;
  }

  function openVideoPicker() {
    var input = $('mcmVideoInput');
    if (!input || state.busy) return;
    input.click();
  }

  function clipPosterUrl(clip) {
    var assetId = String((clip || {}).assetId || '').trim();
    if (!assetId) return '';
    return assetPosterUrl(assetId);
  }

  function fetchObjectUrl(url) {
    var target = resolveUrl(url || '', apiBase());
    if (!target) return Promise.reject(new Error('预览地址为空'));
    return fetch(target, { headers: headers(false) }).then(function(response) {
      if (!response.ok) throw new Error('预览读取失败');
      return response.blob();
    }).then(function(blob) {
      return URL.createObjectURL(blob);
    });
  }

  function revokeClipObjectUrls(clip) {
    ['posterObjectUrl', 'playObjectUrl'].forEach(function(key) {
      var value = clip && clip[key];
      if (value && /^blob:/i.test(value)) {
        try { URL.revokeObjectURL(value); } catch (error) {}
      }
      if (clip) clip[key] = '';
    });
  }

  function hydrateClipPoster(clip) {
    if (!clip || clip.localPosterUrl || clip.posterObjectUrl || clip.posterLoading || clip.posterFailed) return;
    var posterUrl = clip.posterUrl || clipPosterUrl(clip);
    if (!posterUrl) return;
    clip.posterLoading = true;
    fetchObjectUrl(posterUrl).then(function(objectUrl) {
      clip.posterObjectUrl = objectUrl;
      renderClips();
    }).catch(function() {
      clip.posterFailed = true;
    }).finally(function() {
      clip.posterLoading = false;
    });
  }

  function hydrateClipPosters() {
    state.clips.forEach(hydrateClipPoster);
  }

  function playableClipUrl(clip) {
    if (!clip) return Promise.resolve('');
    if (clip.playObjectUrl) return Promise.resolve(clip.playObjectUrl);
    // Uploaded phone videos are commonly HEVC/H.265, which Chromium/WebView
    // cannot decode. The playback endpoint returns a cached H.264 proxy.
    var previewUrl = clip.assetId ? assetPlaybackUrl(clip.assetId) : (clip.previewUrl || '');
    if (clip.playPromise) return clip.playPromise;
    if (previewUrl) {
      // Always prefer the authenticated local asset endpoint. A stored source_url
      // may be a deferred/remote URL that the video element cannot read directly.
      clip.playPromise = fetchObjectUrl(previewUrl).then(function(objectUrl) {
        clip.playObjectUrl = objectUrl;
        return objectUrl;
      }).finally(function() {
        clip.playPromise = null;
      });
      return clip.playPromise;
    }
    return clip.sourceUrl ? Promise.resolve(resolveUrl(clip.sourceUrl, apiBase())) : Promise.resolve('');
  }

  function randomSegmentForClip(clip, seconds) {
    var duration = Math.max(0, Number((clip || {}).duration || 0));
    var span = Math.max(0.1, Math.min(Number(seconds || 3), duration || Number(seconds || 3)));
    var maxStart = Math.max(0, duration - span);
    var start = maxStart > 0 ? Math.random() * maxStart : 0;
    var end = Math.min(duration || (start + span), start + span);
    if (end <= start) end = start + span;
    return { start: Math.round(start * 10) / 10, end: Math.round(end * 10) / 10 };
  }

  function buildClipPlan(outputIndex) {
    var mode = currentClipMode();
    var randomSeconds = Number((($('mcmRandomSegmentSeconds') || {}).value || 3));
    return state.clips.map(function(clip) {
      var segment = mode === 'random'
        ? randomSegmentForClip(clip, randomSeconds)
        : { start: Number(clip.startSec || 0), end: Number(clip.endSec || 0) };
      return { asset_id: clip.assetId, start_sec: segment.start, end_sec: segment.end };
    });
  }

  function outputCount() {
    if (currentClipMode() !== 'random') return 1;
    var count = parseInt((($('mcmRandomOutputCount') || {}).value || 1), 10);
    return Math.max(1, Math.min(Number.isFinite(count) ? count : 1, 20));
  }

  function applyFixedRangeToAll() {
    if (!state.clips.length) return showMessage('请先添加视频，再应用统一片段。', true);
    state.clips.forEach(function(clip) {
      var segment = readFixedSegment(Number(clip.duration || 0));
      clip.startSec = segment.start;
      clip.endSec = segment.end;
    });
    renderClips();
    showMessage('已应用到所有视频：统一取 ' + Number((($('mcmFixedStart') || {}).value || 0)).toFixed(1)
      + ' - ' + Number((($('mcmFixedEnd') || {}).value || 3)).toFixed(1) + ' 秒。', false);
  }

  function syncModePanels() {
    var mode = currentClipMode();
    if ($('mcmFixedModePanel')) $('mcmFixedModePanel').hidden = mode !== 'fixed';
    if ($('mcmRandomModePanel')) $('mcmRandomModePanel').hidden = mode !== 'random';
    if ($('mcmTemplateRandomRow')) $('mcmTemplateRandomRow').hidden = mode !== 'random';
    if ($('mcmMusicRandomRow')) $('mcmMusicRandomRow').hidden = mode !== 'random';
    renderClips();
    renderTemplates();
    renderMusicOptions();
  }

  function totalSelectedDuration() {
    return state.clips.reduce(function(total, clip) {
      return total + Math.max(0, Number(clip.endSec || 0) - Number(clip.startSec || 0));
    }, 0);
  }

  function compactBaseResult(result) {
    result = result || {};
    return {
      asset_id: result.asset_id || '',
      source_url: result.source_url || '',
      preview_url: result.preview_url || '',
      duration: Number(result.duration || 0)
    };
  }

  function rawResultVideoUrl(result) {
    result = result || {};
    return String(result.source_url || result.video_url || result.preview_url || '').trim();
  }

  function resultVideoUrl(result) {
    return resolveUrl(rawResultVideoUrl(result), apiBase());
  }

  function makeHistoryId(prefix) {
    return String(prefix || 'mcm') + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  }

  function compactHistoryResult(result, index) {
    var sourceUrl = rawResultVideoUrl(result);
    if (!sourceUrl) return null;
    result = result || {};
    var historyId = String(result.history_result_id || result._historyId || '');
    if (!historyId) {
      historyId = makeHistoryId('result-' + (index || 0));
      result._historyId = historyId;
    }
    return {
      id: historyId,
      history_result_id: historyId,
      asset_id: String(result.asset_id || ''),
      source_url: String(result.source_url || sourceUrl),
      video_url: String(result.video_url || ''),
      preview_url: String(result.preview_url || ''),
      duration: Number(result.duration || 0),
      template_name: String(result.template_name || '')
    };
  }

  function normalizeHistoryBatch(raw) {
    if (!raw || typeof raw !== 'object') return null;
    var results = (Array.isArray(raw.results) ? raw.results : []).map(compactHistoryResult).filter(Boolean);
    if (!results.length) return null;
    return {
      id: String(raw.id || makeHistoryId('batch')),
      created_at: Number(raw.created_at || Date.now()),
      completed_at: Number(raw.completed_at || 0),
      title: String(raw.title || '多段视频混剪'),
      clip_mode: raw.clip_mode === 'random' ? 'random' : 'fixed',
      total_runs: Math.max(1, Number(raw.total_runs || results.length || 1)),
      clip_count: Math.max(0, Number(raw.clip_count || 0)),
      template_name: String(raw.template_name || ''),
      music_name: String(raw.music_name || ''),
      status: raw.status === 'completed' ? 'completed' : 'partial',
      results: results
    };
  }

  function loadHistory() {
    try {
      var parsed = JSON.parse(localStorage.getItem(historyStorageKey()) || '[]');
      state.history = (Array.isArray(parsed) ? parsed : [])
        .map(normalizeHistoryBatch)
        .filter(Boolean)
        .sort(function(left, right) { return Number(right.created_at || 0) - Number(left.created_at || 0); })
        .slice(0, HISTORY_LIMIT);
    } catch (error) {
      state.history = [];
    }
  }

  function saveHistory() {
    try {
      localStorage.setItem(historyStorageKey(), JSON.stringify(state.history.slice(0, HISTORY_LIMIT)));
    } catch (error) {}
  }

  function historyStorageKey() {
    var userId = '';
    try {
      userId = typeof getCurrentUserIdFromToken === 'function' ? String(getCurrentUserIdFromToken() || '').trim() : '';
    } catch (error) {}
    return userId ? (HISTORY_STORAGE_KEY + ':' + userId) : HISTORY_STORAGE_KEY;
  }

  function activeHistoryTemplateName(options) {
    if (!options || !options.useTemplate) return '';
    if (currentClipMode() === 'random' && (($('mcmRandomTemplateSwitch') || {}).checked)) return '随机模板';
    return String((state.selectedTemplate || {}).name || '定制模板');
  }

  function startHistoryBatch(options, totalRuns, title) {
    state.activeHistoryBatch = {
      id: makeHistoryId('batch'),
      created_at: Date.now(),
      completed_at: 0,
      title: String(title || templateTitleValue() || '多段视频混剪'),
      clip_mode: currentClipMode(),
      total_runs: Math.max(1, Number(totalRuns || 1)),
      clip_count: state.clips.length,
      template_name: activeHistoryTemplateName(options),
      music_name: options && options.useMusic ? String((state.selectedMusic || {}).music_name || '') : ''
    };
    state.batchSelection = {};
    return state.activeHistoryBatch;
  }

  function saveActiveHistoryBatch(completed) {
    var active = state.activeHistoryBatch;
    if (!active) return;
    var results = state.batchResults.map(compactHistoryResult).filter(Boolean);
    if (!results.length) return;
    if (completed) active.completed_at = Date.now();
    var record = Object.assign({}, active, { results: results, status: completed ? 'completed' : 'partial' });
    var index = state.history.findIndex(function(item) { return item.id === record.id; });
    if (index >= 0) state.history[index] = record;
    else state.history.unshift(record);
    state.history.sort(function(left, right) { return Number(right.created_at || 0) - Number(left.created_at || 0); });
    state.history = state.history.slice(0, HISTORY_LIMIT);
    var selection = state.historySelection[record.id] || {};
    results.forEach(function(result) {
      if (typeof selection[result.id] === 'undefined') selection[result.id] = true;
    });
    state.historySelection[record.id] = selection;
    saveHistory();
    renderHistory();
  }

  function appendBatchResult(result) {
    state.batchResults.push(result || {});
    state.batchSelection[state.batchResults.length - 1] = true;
    renderBatchResults();
    saveActiveHistoryBatch(false);
  }

  function historyResultId(result, index) {
    return String((result || {}).id || ('result-' + index));
  }

  function currentBatchSelectedUrls() {
    return state.batchResults.reduce(function(urls, result, index) {
      var url = resultVideoUrl(result);
      if (url && state.batchSelection[index] !== false) urls.push(url);
      return urls;
    }, []);
  }

  function historySelectedUrls(batch) {
    if (!batch) return [];
    var selected = state.historySelection[batch.id] || {};
    return (batch.results || []).reduce(function(urls, result, index) {
      var url = resultVideoUrl(result);
      if (url && selected[historyResultId(result, index)] !== false) urls.push(url);
      return urls;
    }, []);
  }

  function safeDownloadName(prefix, index) {
    var stem = String(prefix || 'multi-clip-mixer').replace(/[\\/:*?"<>|]+/g, '-').slice(0, 72) || 'multi-clip-mixer';
    return stem + '-' + String(index + 1).padStart(2, '0') + '.mp4';
  }

  function downloadVideoUrls(urls, prefix) {
    urls = (Array.isArray(urls) ? urls : []).filter(Boolean);
    if (!urls.length) return showMessage('请先选择至少一条可下载的视频。', true);
    urls.forEach(function(url, index) {
      window.setTimeout(function() {
        var link = document.createElement('a');
        link.href = url;
        link.download = safeDownloadName(prefix, index);
        link.rel = 'noopener';
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        link.remove();
      }, index * 180);
    });
    showMessage('已发起 ' + urls.length + ' 条视频下载。', false);
  }

  function copyVideoUrls(urls) {
    urls = (Array.isArray(urls) ? urls : []).filter(Boolean);
    if (!urls.length) return showMessage('请先选择至少一条有链接的视频。', true);
    var text = urls.join('\n');
    if (typeof copyToClipboard === 'function') {
      copyToClipboard(text, function() { showMessage('已复制 ' + urls.length + ' 条视频链接。', false); });
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function() {
        showMessage('已复制 ' + urls.length + ' 条视频链接。', false);
      }).catch(function() { showMessage('复制链接失败，请重试。', true); });
      return;
    }
    showMessage('当前环境不支持复制链接。', true);
  }

  function formatHistoryTime(value) {
    var date = new Date(Number(value || 0));
    if (Number.isNaN(date.getTime())) return '';
    try {
      return date.toLocaleString('zh-CN', { hour12: false });
    } catch (error) {
      return date.toLocaleString();
    }
  }

  function batchLabel(batch) {
    var parts = [String(batch.title || '多段视频混剪')];
    if (batch.clip_mode === 'random') parts.push('随机');
    if (batch.template_name) parts.push(batch.template_name);
    return parts.join(' · ');
  }

  function renderBatchResults() {
    var root = $('mcmBatchResults');
    if (!root) return;
    root.hidden = !state.batchResults.length;
    if (!state.batchResults.length) {
      root.innerHTML = '';
      return;
    }
    var selectedCount = state.batchResults.reduce(function(count, result, index) {
      return count + (resultVideoUrl(result) && state.batchSelection[index] !== false ? 1 : 0);
    }, 0);
    root.innerHTML = '<div class="mcm-batch-head">'
      + '<div><strong>本次结果</strong><span>' + selectedCount + ' / ' + state.batchResults.length + ' 条已选</span></div>'
      + '<div class="mcm-result-tools">'
      + '<label class="mcm-check-label"><input type="checkbox" data-batch-select-all ' + (selectedCount && selectedCount === state.batchResults.filter(resultVideoUrl).length ? 'checked' : '') + '>全选</label>'
      + '<button type="button" class="btn btn-ghost btn-sm" data-batch-action="download">批量下载</button>'
      + '<button type="button" class="btn btn-ghost btn-sm" data-batch-action="copy">复制链接</button>'
      + '</div></div>'
      + '<div class="mcm-batch-list">'
      + state.batchResults.map(function(result, index) {
        var url = resultVideoUrl(result);
        var checked = state.batchSelection[index] !== false ? ' checked' : '';
        var label = result.template_name ? (' · ' + result.template_name) : '';
        return '<article class="mcm-batch-item">'
          + '<label class="mcm-batch-check"><input type="checkbox" data-batch-index="' + index + '"' + (url ? checked : ' disabled') + '><span class="mcm-visually-hidden">选择第 ' + (index + 1) + ' 条</span></label>'
          + (url ? '<button type="button" class="mcm-batch-preview" data-batch-preview="' + index + '"><video src="' + escapeHtml(url) + '" muted playsinline preload="metadata"></video></button>' : '<span class="mcm-batch-preview is-empty">无预览</span>')
          + '<span class="mcm-batch-copy">' + (index + 1) + '. ' + escapeHtml(formatSeconds(result.duration || 0)) + escapeHtml(label) + '</span>'
          + (url ? '<a class="btn btn-ghost btn-sm" target="_blank" rel="noopener" href="' + escapeHtml(url) + '">打开</a>' : '')
          + '</article>';
      }).join('') + '</div>';
  }

  function renderHistory() {
    var panel = $('mcmHistoryPanel');
    var list = $('mcmHistoryList');
    var count = $('mcmHistoryCount');
    var pager = $('mcmHistoryPager');
    if (!panel || !list) return;
    panel.hidden = !state.history.length || !state.historyVisible;
    if (count) count.textContent = state.history.length + ' 批';
    if ($('mcmHistoryToggleBtn')) $('mcmHistoryToggleBtn').textContent = state.history.length ? ('历史记录 ' + state.history.length) : '历史记录';
    if (!state.history.length) {
      list.innerHTML = '';
      if (pager) pager.hidden = true;
      return;
    }
    var pageCount = Math.max(1, Math.ceil(state.history.length / HISTORY_PAGE_SIZE));
    state.historyPage = Math.max(0, Math.min(state.historyPage, pageCount - 1));
    var pageRows = state.history.slice(state.historyPage * HISTORY_PAGE_SIZE, (state.historyPage + 1) * HISTORY_PAGE_SIZE);
    list.innerHTML = pageRows.map(function(batch) {
      var selection = state.historySelection[batch.id] || {};
      var results = Array.isArray(batch.results) ? batch.results : [];
      var availableCount = results.filter(resultVideoUrl).length;
      var selectedCount = results.reduce(function(total, result, index) {
        return total + (resultVideoUrl(result) && selection[historyResultId(result, index)] !== false ? 1 : 0);
      }, 0);
      var allChecked = availableCount > 0 && selectedCount === availableCount;
      return '<article class="mcm-history-item" data-history-id="' + escapeHtml(batch.id) + '">'
        + '<div class="mcm-history-item-head"><div><strong>' + escapeHtml(batchLabel(batch)) + '</strong><span>'
        + escapeHtml(formatHistoryTime(batch.completed_at || batch.created_at)) + ' · ' + results.length + ' 条'
        + (batch.status === 'completed' ? '' : ' · 部分完成') + '</span></div>'
        + '<div class="mcm-result-tools"><label class="mcm-check-label"><input type="checkbox" data-history-select-all ' + (allChecked ? 'checked' : '') + '>全选</label>'
        + '<button type="button" class="btn btn-ghost btn-sm" data-history-action="download">批量下载</button>'
        + '<button type="button" class="btn btn-ghost btn-sm" data-history-action="copy">复制链接</button>'
        + '<button type="button" class="btn btn-ghost btn-sm" data-history-action="delete">删除</button></div></div>'
        + '<div class="mcm-history-results">' + results.map(function(result, index) {
          var url = resultVideoUrl(result);
          var resultId = historyResultId(result, index);
          return '<div class="mcm-history-result">'
            + '<label class="mcm-batch-check"><input type="checkbox" data-history-result="' + escapeHtml(resultId) + '"' + (url ? (selection[resultId] !== false ? ' checked' : '') : ' disabled') + '><span class="mcm-visually-hidden">选择历史结果</span></label>'
            + (url ? '<button type="button" class="mcm-batch-preview" data-history-preview="' + index + '"><video src="' + escapeHtml(url) + '" muted playsinline preload="metadata"></video></button>' : '<span class="mcm-batch-preview is-empty">无预览</span>')
            + '<span class="mcm-batch-copy">' + (index + 1) + '. ' + escapeHtml(formatSeconds(result.duration || 0)) + '</span>'
            + (url ? '<a class="btn btn-ghost btn-sm" target="_blank" rel="noopener" href="' + escapeHtml(url) + '">打开</a>' : '')
            + '</div>';
        }).join('') + '</div></article>';
    }).join('');
    if (pager) pager.hidden = pageCount <= 1;
    if ($('mcmHistoryPageLabel')) $('mcmHistoryPageLabel').textContent = (state.historyPage + 1) + ' / ' + pageCount;
    if ($('mcmHistoryPrevBtn')) $('mcmHistoryPrevBtn').disabled = state.historyPage <= 0;
    if ($('mcmHistoryNextBtn')) $('mcmHistoryNextBtn').disabled = state.historyPage >= pageCount - 1;
  }

  function rememberPendingTemplateTask(taskId, baseResult) {
    var pending = {
      taskId: String(taskId || ''),
      baseResult: compactBaseResult(baseResult),
      createdAt: Date.now()
    };
    state.pendingTemplateTask = pending;
    try { localStorage.setItem(PENDING_TEMPLATE_STORAGE_KEY, JSON.stringify(pending)); } catch (error) {}
    return pending;
  }

  function loadPendingTemplateTask() {
    if (state.pendingTemplateTask && state.pendingTemplateTask.taskId) return state.pendingTemplateTask;
    try {
      var pending = JSON.parse(localStorage.getItem(PENDING_TEMPLATE_STORAGE_KEY) || 'null');
      if (pending && pending.taskId && pending.baseResult) {
        state.pendingTemplateTask = pending;
        return pending;
      }
    } catch (error) {}
    return null;
  }

  function clearPendingTemplateTask() {
    state.pendingTemplateTask = null;
    try { localStorage.removeItem(PENDING_TEMPLATE_STORAGE_KEY); } catch (error) {}
  }

  function renderClips() {
    var list = $('mcmClipList');
    var empty = $('mcmClipEmpty');
    if (!list) return;
    list.innerHTML = state.clips.map(function(clip, index) {
      var source = clip.sourceUrl || clip.previewUrl || '';
      var clipDuration = Math.max(0, Number(clip.endSec || 0) - Number(clip.startSec || 0));
      var poster = clip.localPosterUrl || clip.posterObjectUrl || '';
      var modeLabel = currentClipMode() === 'random'
        ? '随机抽段时会重新计算'
        : ('统一取段 ' + Number(clip.startSec || 0).toFixed(1) + ' - ' + Number(clip.endSec || 0).toFixed(1) + ' 秒');
      return '<article class="mcm-clip-item' + (clip.audioMaster ? ' is-audio-master' : '') + '" data-clip-index="' + index + '">'
        + '<div class="mcm-clip-thumb">'
        + (poster ? '<img src="' + escapeHtml(poster) + '" alt="" loading="lazy">' : '<span class="mcm-clip-thumb-placeholder">VIDEO</span>')
        + '<span class="mcm-clip-number">' + (index + 1) + '</span></div>'
        + '<div class="mcm-clip-copy"><strong title="' + escapeHtml(clip.name) + '">' + escapeHtml(clip.name) + '</strong>'
        + '<span>' + escapeHtml(modeLabel) + '</span>'
        + '<small>本段时长 ' + escapeHtml(formatSeconds(clipDuration)) + ' · 原片 ' + escapeHtml(formatSeconds(clip.duration))
        + (clip.audioMaster ? ' · 已标记为整片原声音轨' : '') + '</small></div>'
        + '<div class="mcm-clip-actions">'
        + '<button type="button" data-action="up" title="上移" aria-label="上移"' + (index === 0 ? ' disabled' : '') + '>↑</button>'
        + '<button type="button" data-action="down" title="下移" aria-label="下移"' + (index === state.clips.length - 1 ? ' disabled' : '') + '>↓</button>'
        + '<button type="button" data-action="audio" class="mcm-audio-master-btn" title="标记为整条成片原声音轨">' + (clip.audioMaster ? '原声源' : '用原声') + '</button>'
        + '<button type="button" data-action="edit" title="修改片段">编辑</button>'
        + '<button type="button" data-action="remove" title="移除" aria-label="移除">×</button>'
        + '</div></article>';
    }).join('');
    if (empty) empty.hidden = state.clips.length > 0;
    if ($('mcmTotalDuration')) $('mcmTotalDuration').textContent = formatSeconds(totalSelectedDuration());
    renderTemplateLayout();
    hydrateClipPosters();
  }

  function uploadVideo(file) {
    var data = new FormData();
    data.append('file', file);
    return request('/api/assets/upload', { method: 'POST', headers: headers(false), body: data });
  }

  function assetContentUrl(assetId) {
    return apiBase() + '/api/assets/' + encodeURIComponent(assetId) + '/content';
  }

  function assetPosterUrl(assetId) {
    return apiBase() + '/api/multi-clip-mixer/assets/' + encodeURIComponent(assetId) + '/poster.jpg';
  }

  function assetPlaybackUrl(assetId) {
    return apiBase() + '/api/multi-clip-mixer/assets/' + encodeURIComponent(assetId) + '/playback.mp4';
  }

  function addVideoClip(clip) {
    clip = clip || {};
    var duration = Number(clip.duration || 0);
    var segment = defaultSegment(state.clips.length, duration);
    clip.startSec = Number(clip.startSec != null ? clip.startSec : segment.start);
    clip.endSec = Number(clip.endSec != null ? clip.endSec : segment.end);
    state.clips.push(clip);
    renderClips();
    return clip;
  }

  function addUploadedVideoClip(file, data, info) {
    data = data || {};
    info = info || {};
    var assetId = String(data.asset_id || '').trim();
    if (!assetId) throw new Error('视频上传后没有返回素材 ID');
    return addVideoClip({
      assetId: assetId,
      name: (file && file.name) || ('视频 ' + (state.clips.length + 1)),
      sourceUrl: data.source_url || '',
      streamUrl: data.open_url || '',
      previewUrl: assetContentUrl(assetId),
      posterUrl: assetPosterUrl(assetId),
      duration: Number(info.duration || data.duration || 0),
      width: Number(info.width || data.width || 0),
      height: Number(info.height || data.height || 0),
      localPosterUrl: info.localPosterUrl || ''
    });
  }

  function addAssetVideoClip(item) {
    item = item || {};
    var assetId = String(item.asset_id || item.assetId || item.id || '').trim();
    if (!assetId) throw new Error('素材缺少 asset_id，无法加入混剪');
    return addVideoClip({
      assetId: assetId,
      name: item.name || item.title || item.filename || ('素材视频 ' + (state.clips.length + 1)),
      sourceUrl: item.source_url || item.sourceUrl || item.open_url || item.openUrl || '',
      streamUrl: item.open_url || item.openUrl || '',
      previewUrl: item.preview_url || item.previewUrl || assetContentUrl(assetId),
      posterUrl: item.poster_url || item.posterUrl || item.cover_url || item.coverUrl || assetPosterUrl(assetId),
      duration: Number(item.duration || item.video_duration || item.duration_sec || (item.meta && (item.meta.duration || item.meta.duration_sec)) || 0),
      width: Number(item.width || (item.meta && item.meta.width) || 0),
      height: Number(item.height || (item.meta && item.meta.height) || 0),
      localPosterUrl: item.localPosterUrl || ''
    });
  }

  function closeSegmentModal(cancelled) {
    var context = state.segmentContext;
    var modal = $('mcmSegmentModal');
    if (modal) modal.hidden = true;
    var video = $('mcmSegmentVideo');
    if (video) {
      video.pause();
      video.removeAttribute('src');
      video.load();
    }
    if (context && context.objectUrl && context.mode === 'new') URL.revokeObjectURL(context.objectUrl);
    state.segmentContext = null;
  }

  function configureSegmentVideo(video, src, poster, handlers) {
    if (!video) return;
    video.pause();
    video.removeAttribute('src');
    video.removeAttribute('poster');
    handlers = handlers || {};
    video.onerror = handlers.error || null;
    video.onloadedmetadata = handlers.loadedmetadata || null;
    video.controls = true;
    video.preload = 'auto';
    video.playsInline = true;
    if (poster) video.poster = poster;
    if (src) video.src = src;
    video.load();
  }

  function captureLocalVideoPoster(src, done) {
    if (!src || typeof document === 'undefined') return;
    var probe = document.createElement('video');
    probe.muted = true;
    probe.playsInline = true;
    probe.preload = 'auto';
    probe.crossOrigin = 'anonymous';
    var settled = false;
    function cleanup() {
      probe.onloadedmetadata = null;
      probe.onseeked = null;
      probe.onerror = null;
      probe.removeAttribute('src');
      try { probe.load(); } catch (error) {}
    }
    function finish(value) {
      if (settled) return;
      settled = true;
      cleanup();
      if (typeof done === 'function') done(value || '');
    }
    probe.onerror = function() { finish(''); };
    probe.onloadedmetadata = function() {
      try {
        var duration = Number(probe.duration || 0);
        probe.currentTime = Math.min(Math.max(duration * 0.08, 0.2), Math.max(duration - 0.05, 0));
      } catch (error) {
        finish('');
      }
    };
    probe.onseeked = function() {
      try {
        var canvas = document.createElement('canvas');
        var width = Number(probe.videoWidth || 0);
        var height = Number(probe.videoHeight || 0);
        if (!width || !height) return finish('');
        var scale = Math.min(1, 640 / Math.max(width, height));
        canvas.width = Math.max(2, Math.round(width * scale));
        canvas.height = Math.max(2, Math.round(height * scale));
        canvas.getContext('2d').drawImage(probe, 0, 0, canvas.width, canvas.height);
        finish(canvas.toDataURL('image/jpeg', 0.82));
      } catch (error) {
        finish('');
      }
    };
    probe.src = src;
    try { probe.load(); } catch (error) { finish(''); }
  }

  function readLocalVideoInfo(file) {
    return new Promise(function(resolve) {
      var objectUrl = URL.createObjectURL(file);
      var info = { objectUrl: objectUrl, duration: 0, width: 0, height: 0, localPosterUrl: '' };
      var video = document.createElement('video');
      var settled = false;
      var timer = setTimeout(function() { finish(); }, 2500);

      function cleanup() {
        clearTimeout(timer);
        video.onloadedmetadata = null;
        video.onerror = null;
        video.removeAttribute('src');
        try { video.load(); } catch (error) {}
      }

      function finish() {
        if (settled) return;
        settled = true;
        cleanup();
        var posterDone = false;
        var posterTimer = setTimeout(function() {
          if (posterDone) return;
          posterDone = true;
          resolve(info);
        }, 1800);
        captureLocalVideoPoster(objectUrl, function(poster) {
          if (posterDone) return;
          posterDone = true;
          clearTimeout(posterTimer);
          info.localPosterUrl = poster || '';
          resolve(info);
        });
      }

      video.preload = 'metadata';
      video.muted = true;
      video.playsInline = true;
      video.onloadedmetadata = function() {
        info.duration = Number(video.duration || 0);
        info.width = Number(video.videoWidth || 0);
        info.height = Number(video.videoHeight || 0);
        finish();
      };
      video.onerror = function() { finish(); };
      video.src = objectUrl;
      try { video.load(); } catch (error) { finish(); }
    });
  }

  function syncSegmentSummary() {
    var context = state.segmentContext;
    if (!context) return false;
    var start = Number(($('mcmSegmentStart') || {}).value || 0);
    var end = Number(($('mcmSegmentEnd') || {}).value || 0);
    var duration = Number(context.duration || 0);
    var message = '';
    if (!Number.isFinite(start) || start < 0) message = '开始时间不能小于 0。';
    else if (!Number.isFinite(end) || end <= start) message = '结束时间必须大于开始时间。';
    else if (duration > 0 && end > duration + 0.01) message = '结束时间不能超过原视频时长。';
    else if (end - start < 0.1) message = '所选片段不能短于 0.1 秒。';
    if ($('mcmSelectedDuration')) $('mcmSelectedDuration').textContent = formatSeconds(Math.max(0, end - start));
    var error = $('mcmSegmentError');
    if (error) {
      error.hidden = !message;
      error.textContent = message;
    }
    var confirm = $('mcmConfirmSegmentBtn');
    if (confirm && !context.uploading) confirm.disabled = !!message;
    return !message;
  }

  function setSegmentValues(segment) {
    if ($('mcmSegmentStart')) $('mcmSegmentStart').value = Number(segment.start || 0).toFixed(1);
    if ($('mcmSegmentEnd')) $('mcmSegmentEnd').value = Number(segment.end || 0).toFixed(1);
    syncSegmentSummary();
  }

  function openSegmentForEdit(index) {
    var clip = state.clips[index];
    if (!clip || state.busy) return;
    state.segmentContext = { mode: 'edit', index: index, duration: clip.duration, uploading: false };
    if ($('mcmSegmentIndex')) $('mcmSegmentIndex').textContent = '视频 ' + (index + 1);
    if ($('mcmSourceDuration')) $('mcmSourceDuration').textContent = formatSeconds(clip.duration);
    if ($('mcmConfirmSegmentBtn')) $('mcmConfirmSegmentBtn').textContent = '保存片段';
    var video = $('mcmSegmentVideo');
    var poster = clip.localPosterUrl || clip.posterObjectUrl || '';
    configureSegmentVideo(video, '', poster);
    playableClipUrl(clip).then(function(url) {
      if (!state.segmentContext || state.segmentContext.mode !== 'edit' || state.segmentContext.index !== index) return;
      configureSegmentVideo(video, url, clip.localPosterUrl || clip.posterObjectUrl || '');
    }).catch(function(error) {
      var errorEl = $('mcmSegmentError');
      if (errorEl) {
        errorEl.hidden = false;
        errorEl.textContent = error.message || '视频预览加载失败';
      }
    });
    setSegmentValues({ start: clip.startSec, end: clip.endSec });
    if ($('mcmSegmentModal')) $('mcmSegmentModal').hidden = false;
  }

  function processNextPendingFile() {
    if (state.importingFiles || !state.pendingFiles.length || state.busy) return;
    state.importingFiles = true;
    var total = state.pendingFiles.length;
    var added = 0;
    var failed = 0;

    function finishBatch() {
      state.importingFiles = false;
      setBusy(false);
      if (failed) showMessage('已添加 ' + added + ' 个视频，' + failed + ' 个上传失败。失败的视频请重新选择。', true);
      else showMessage('已添加 ' + added + ' 个视频，可直接生成或点“编辑”微调片段。', false);
    }

    function next() {
      if (!state.pendingFiles.length) return finishBatch();
      var file = state.pendingFiles.shift();
      setBusy(true, '正在添加视频 ' + (added + failed + 1) + '/' + total + '...');
      var infoForCleanup = null;
      readLocalVideoInfo(file).then(function(info) {
        infoForCleanup = info;
        return uploadVideo(file).then(function(data) {
          addUploadedVideoClip(file, data, info);
          added += 1;
        });
      }).catch(function(error) {
        failed += 1;
        showMessage(((file && file.name) || '视频') + ' 添加失败：' + (error.message || error), true);
      }).finally(function() {
        if (infoForCleanup && infoForCleanup.objectUrl) {
          try { URL.revokeObjectURL(infoForCleanup.objectUrl); } catch (error) {}
        }
        next();
      });
    }

    next();
  }

  function confirmSegment() {
    var context = state.segmentContext;
    if (!context || !syncSegmentSummary()) return;
    var start = Number(($('mcmSegmentStart') || {}).value || 0);
    var end = Number(($('mcmSegmentEnd') || {}).value || 0);
    if (context.mode === 'edit') {
      state.clips[context.index].startSec = start;
      state.clips[context.index].endSec = end;
      closeSegmentModal(false);
      renderClips();
    } else {
      closeSegmentModal(false);
    }
  }

  function previewSelectedSegment() {
    if (!syncSegmentSummary()) return;
    var video = $('mcmSegmentVideo');
    if (!video) return;
    var start = Number(($('mcmSegmentStart') || {}).value || 0);
    var end = Number(($('mcmSegmentEnd') || {}).value || 0);
    video.currentTime = start;
    video.play().catch(function() {});
    var stopAtEnd = function() {
      if (video.currentTime >= end) {
        video.pause();
        video.removeEventListener('timeupdate', stopAtEnd);
      }
    };
    video.addEventListener('timeupdate', stopAtEnd);
  }

  function templateImage(item) {
    return resolveUrl(item.coverUrl || item.cover_url || '', cloudBaseUrl());
  }

  function templateDemo(item) {
    return resolveUrl(
      item.preview_url || item.sample_video_url || item.demoUrl || item.demo_url || item.videoUrl || item.video_url || '',
      cloudBaseUrl()
    );
  }

  function resolveUrl(value, base) {
    var url = String(value || '').trim();
    if (!url || /^(https?:|blob:|data:)/i.test(url)) return url;
    var root = String(base || '').replace(/\/$/, '');
    if (!root && window.location && window.location.origin) root = window.location.origin;
    if (!root) return url;
    return root + (url.charAt(0) === '/' ? url : '/' + url);
  }

  function renderTemplates() {
    var grid = $('mcmTemplateGrid');
    if (!grid) return;
    var randomTemplate = currentClipMode() === 'random' && (($('mcmRandomTemplateSwitch') || {}).checked);
    var choiceArea = $('mcmTemplateChoiceArea');
    if (choiceArea) choiceArea.hidden = randomTemplate;
    grid.innerHTML = randomTemplate ? '' : state.templates.map(function(item) {
      var selected = state.selectedTemplate && String(state.selectedTemplate.id) === String(item.id);
      var cover = templateImage(item);
      var demo = templateDemo(item);
      var media = cover
        ? '<img src="' + escapeHtml(cover) + '" alt="' + escapeHtml(item.name || '剪辑模板') + '" loading="lazy" referrerpolicy="no-referrer">'
        : (demo
          ? '<video src="' + escapeHtml(demo) + '" muted playsinline preload="metadata"></video>'
          : '<span class="mcm-template-placeholder">暂无样片</span>');
      return '<button type="button" class="mcm-template-card' + (selected ? ' is-selected' : '') + '" data-template-id="' + escapeHtml(item.id) + '" title="双击预览模板">'
        + media + '<span>' + escapeHtml(item.name || '未命名模板') + '</span></button>';
    }).join('');
    if ($('mcmTemplatePicked')) $('mcmTemplatePicked').textContent = randomTemplate ? '每条随机选择' : (state.selectedTemplate ? ('已选：' + (state.selectedTemplate.name || '剪辑模板')) : '还未选择');
    if ($('mcmTemplateMoreBtn')) $('mcmTemplateMoreBtn').hidden = true;
    renderTemplateOverlayFields();
    renderTemplateLayout();
  }

  function shanjianEditInfo(detail) {
    return detail && detail.videoStructInfo && detail.videoStructInfo.editInfo
      ? detail.videoStructInfo.editInfo
      : {};
  }

  function renderShanjianTemplateCapabilities() {
    var target = $('mcmShanjianTemplateCapabilities');
    if (!target) return;
    if (currentTemplateProvider() !== 'shanjian' || !state.selectedTemplate) {
      target.textContent = '';
      target.hidden = true;
      return;
    }
    target.hidden = false;
    if (state.shanjianTemplateDetailPromise) {
      target.textContent = '正在读取模板支持的输入...';
      return;
    }
    if (state.shanjianTemplateDetailError) {
      target.textContent = '模板输入信息读取失败，暂不能确认介绍文案是否支持';
      return;
    }
    if (!state.shanjianTemplateDetail || state.shanjianTemplateDetailId !== String(state.selectedTemplate.id)) {
      target.textContent = '正在读取模板支持的输入...';
      return;
    }
    var editInfo = shanjianEditInfo(state.shanjianTemplateDetail);
    var supported = [];
    if (editInfo.headerLayer) supported.push('标题');
    if (editInfo.ipLayer) supported.push('介绍文案');
    target.textContent = supported.length ? ('当前模板支持：' + supported.join('、')) : '当前模板没有可填写的标题或介绍文案图层';
  }

  function loadShanjianTemplateDetail(item) {
    var templateId = String(item && item.id || '').trim();
    if (!templateId || !item || item.provider !== 'shanjian') return Promise.resolve(null);
    if (state.shanjianTemplateDetailId === templateId && state.shanjianTemplateDetail) {
      renderShanjianTemplateCapabilities();
      return Promise.resolve(state.shanjianTemplateDetail);
    }
    if (state.shanjianTemplateDetailId === templateId && state.shanjianTemplateDetailPromise) {
      renderShanjianTemplateCapabilities();
      return state.shanjianTemplateDetailPromise;
    }
    state.shanjianTemplateDetail = null;
    state.shanjianTemplateDetailId = templateId;
    state.shanjianTemplateDetailError = '';
    state.shanjianTemplateDetailPromise = post('/api/shanjian-smart-clip/template-detail', {
      template_id: templateId
    }).then(function(response) {
      if (state.shanjianTemplateDetailId === templateId) {
        state.shanjianTemplateDetail = response && response.item ? response.item : {};
        state.shanjianTemplateDetailError = '';
      }
      return response && response.item ? response.item : {};
    }).catch(function(error) {
      if (state.shanjianTemplateDetailId === templateId) state.shanjianTemplateDetailError = error.message || '模板详情读取失败';
      throw error;
    }).then(function(detail) {
      if (state.shanjianTemplateDetailId === templateId) state.shanjianTemplateDetailPromise = null;
      renderShanjianTemplateCapabilities();
      return detail;
    }, function(error) {
      if (state.shanjianTemplateDetailId === templateId) state.shanjianTemplateDetailPromise = null;
      renderShanjianTemplateCapabilities();
      throw error;
    });
    renderShanjianTemplateCapabilities();
    return state.shanjianTemplateDetailPromise;
  }

  function templateOverlayFields(item) {
    var strategy = item && item.generation_strategy && typeof item.generation_strategy === 'object' ? item.generation_strategy : {};
    var fields = item && Array.isArray(item.overlay_fields) ? item.overlay_fields : strategy.overlay_fields;
    return Array.isArray(fields) ? fields.filter(function(field) { return !!String((field || {}).key || '').trim(); }) : [];
  }

  function templateTitleField(item) {
    var fields = templateOverlayFields(item);
    var priorities = ['title', 'headline', 'top_text'];
    for (var index = 0; index < priorities.length; index += 1) {
      var field = fields.find(function(candidate) { return String(candidate.key || '').trim() === priorities[index]; });
      if (field) return field;
    }
    return null;
  }

  function templateTitleValue() {
    var input = $('mcmTemplateTitle');
    return truncateChars(String(input && input.value || '').replace(/\r\n?/g, '\n').trim(), 80);
  }

  function overlayTextsWithTemplateTitle(item, values) {
    var result = Object.assign({}, values || {}), field = templateTitleField(item);
    if (!field) return result;
    var title = templateTitleValue();
    result[String(field.key || '').trim()] = truncateChars(title || String(field.default || ''), overlayFieldMaxLength(field));
    return result;
  }

  function truncateChars(value, limit) {
    var max = parseInt(limit || 0, 10);
    if (!max || max < 1) return String(value || '');
    return Array.from(String(value || '')).slice(0, max).join('');
  }

  function overlayFieldPlaceholder(field) {
    var key = String((field || {}).key || '').trim();
    var fallback = {
      top_text: '输入顶部文案',
      title: '输入主标题',
      subtitle: '输入副标题',
      headline: '输入主标题',
      subheadline: '输入副标题',
      badge: '输入角标文案'
    };
    return String((field || {}).placeholder || fallback[key] || ('输入' + String((field || {}).label || '模板文案')));
  }

  function overlayFieldMaxLength(field) {
    var maxLength = parseInt((field || {}).max_length || 80, 10);
    return Number.isFinite(maxLength) && maxLength > 0 ? maxLength : 80;
  }

  function clamp(value, min, max) {
    var number = Number(value);
    if (!Number.isFinite(number)) number = min;
    return Math.max(min, Math.min(max, number));
  }

  function outputOrientation() {
    var clip = state.clips[0] || {};
    return Number(clip.height || 0) > Number(clip.width || 0) ? 'portrait' : 'landscape';
  }

  function templateCaptionStyle(item) {
    if (window.CutcliTemplatePreview && typeof window.CutcliTemplatePreview.captionStyle === 'function') {
      return window.CutcliTemplatePreview.captionStyle(item || {}, outputOrientation()) || {};
    }
    var strategy = item && item.generation_strategy && typeof item.generation_strategy === 'object' ? item.generation_strategy : {};
    if (strategy.caption_style && typeof strategy.caption_style === 'object') return strategy.caption_style;
    return item && item.caption_style && typeof item.caption_style === 'object' ? item.caption_style : {};
  }

  function templateOverlayStyle(item) {
    var style = templateCaptionStyle(item);
    return style.overlay_style && typeof style.overlay_style === 'object' ? style.overlay_style : {};
  }

  function defaultOverlayPosition(key, overlay) {
    if (key === 'top_text' || key === 'headline') {
      return {
        x_ratio: clamp(overlay.top_screen_x_ratio != null ? overlay.top_screen_x_ratio : (overlay.top_x_ratio != null ? overlay.top_x_ratio : (overlay.headline_x_ratio != null ? overlay.headline_x_ratio : 0.5)), 0.05, 0.95),
        y_ratio: clamp(overlay.top_screen_y_ratio != null ? overlay.top_screen_y_ratio : (overlay.top_y_ratio != null ? overlay.top_y_ratio : (overlay.headline_y_ratio != null ? overlay.headline_y_ratio : 0.16)), 0.05, 0.95)
      };
    }
    if (key === 'title') {
      return {
        x_ratio: clamp(overlay.title_x_ratio != null ? overlay.title_x_ratio : (overlay.profile_x_ratio != null ? overlay.profile_x_ratio : (overlay.headline_x_ratio != null ? overlay.headline_x_ratio : 0.5)), 0.05, 0.95),
        y_ratio: clamp(overlay.title_y_ratio != null ? overlay.title_y_ratio : (overlay.profile_y_ratio != null ? overlay.profile_y_ratio : (overlay.headline_y_ratio != null ? overlay.headline_y_ratio : 0.42)), 0.05, 0.95)
      };
    }
    if (key === 'subtitle' || key === 'subheadline') {
      return {
        x_ratio: clamp(overlay.subheadline_x_ratio != null ? overlay.subheadline_x_ratio : (overlay.headline_x_ratio != null ? overlay.headline_x_ratio : 0.5), 0.05, 0.95),
        y_ratio: clamp(overlay.subheadline_y_ratio != null ? overlay.subheadline_y_ratio : 0.55, 0.05, 0.95)
      };
    }
    return {
      x_ratio: clamp(overlay.badge_x_ratio != null ? overlay.badge_x_ratio : 0.5, 0.05, 0.95),
      y_ratio: clamp(overlay.badge_y_ratio != null ? overlay.badge_y_ratio : 0.62, 0.05, 0.95)
    };
  }

  function defaultPositionOverrides(item) {
    if (window.CutcliTemplatePreview && typeof window.CutcliTemplatePreview.defaultPositions === 'function') {
      return window.CutcliTemplatePreview.defaultPositions(item || {}, outputOrientation()) || { caption: {}, overlay: {} };
    }
    var style = templateCaptionStyle(item);
    var overlay = templateOverlayStyle(item);
    var captionX = Number(style.transform_x != null ? style.transform_x : (style.cutcli_transform_x != null ? style.cutcli_transform_x : 0));
    var captionY = Number(style.transform_y != null ? style.transform_y : (style.cutcli_transform_y != null ? style.cutcli_transform_y : -0.66));
    var result = {
      caption: {
        x: Number.isFinite(captionX) ? captionX : 0,
        y: Number.isFinite(captionY) ? captionY : -0.66
      },
      overlay: {}
    };
    templateOverlayFields(item).forEach(function(field) {
      var key = String(field.key || '').trim();
      result.overlay[key] = defaultOverlayPosition(key, overlay);
    });
    return result;
  }

  function layoutTargets() {
    var targets = templateOverlayFields(state.selectedTemplate).map(function(field) {
      return { key: String(field.key || '').trim(), label: String(field.label || field.key || '模板文案') };
    });
    targets.push({ key: 'caption', label: '字幕预览' });
    return targets;
  }

  function layoutTargetKind(key) {
    if (key === 'top_text' || key === 'headline') return 'headline';
    if (key === 'subtitle' || key === 'subheadline') return 'subtitle';
    return key;
  }

  function targetPosition(key) {
    var positions = state.positionOverrides || {};
    var defaults = defaultPositionOverrides(state.selectedTemplate);
    if (key === 'caption') {
      var caption = positions.caption || defaults.caption || {};
      return {
        x_ratio: clamp(0.5 + Number(caption.x || 0) * 0.5, 0.05, 0.95),
        y_ratio: clamp(0.5 - Number(caption.y != null ? caption.y : -0.66) * 0.5, 0.05, 0.95)
      };
    }
    return (positions.overlay || {})[key] || (defaults.overlay || {})[key] || defaultOverlayPosition(key, templateOverlayStyle(state.selectedTemplate));
  }

  function setTargetPosition(key, xRatio, yRatio) {
    state.positionOverrides = state.positionOverrides || { caption: {}, overlay: {} };
    if (key === 'caption') {
      state.positionOverrides.caption = {
        x: clamp((xRatio - 0.5) / 0.5, -0.95, 0.95),
        y: clamp((0.5 - yRatio) / 0.5, -0.95, 0.95)
      };
      return;
    }
    state.positionOverrides.overlay = state.positionOverrides.overlay || {};
    state.positionOverrides.overlay[key] = {
      x_ratio: clamp(xRatio, 0.05, 0.95),
      y_ratio: clamp(yRatio, 0.05, 0.95)
    };
  }

  function mergePositionOverrides(defaults, overrides) {
    var result = {
      caption: Object.assign({}, (defaults || {}).caption || {}),
      overlay: Object.assign({}, (defaults || {}).overlay || {})
    };
    if (overrides && typeof overrides === 'object') {
      if (overrides.caption && typeof overrides.caption === 'object') {
        result.caption = Object.assign(result.caption, overrides.caption);
      }
      if (overrides.overlay && typeof overrides.overlay === 'object') {
        Object.keys(overrides.overlay).forEach(function(key) {
          result.overlay[key] = Object.assign({}, result.overlay[key] || {}, overrides.overlay[key] || {});
        });
      }
    }
    return result;
  }

  function templatePositionOverridesForSubmit() {
    if (!state.selectedTemplate) return {};
    return mergePositionOverrides(defaultPositionOverrides(state.selectedTemplate), state.positionOverrides || {});
  }

  function layoutPreviewSource() {
    var clip = state.clips[0];
    return clip ? (clip.sourceUrl || clip.previewUrl || '') : templateDemo(state.selectedTemplate || {});
  }

  function renderTemplateLayoutItems() {
    var layer = $('mcmTemplateLayoutLayer');
    if (!layer || !state.selectedTemplate) return;
    layer.innerHTML = layoutTargets().map(function(target) {
      var position = targetPosition(target.key);
      var kind = layoutTargetKind(target.key);
      var value = target.key === 'caption' ? target.label : (state.overlayTexts[target.key] || target.label);
      var exactClass = '';
      var exactStyle = '';
      if (window.CutcliTemplatePreview) {
        if (typeof window.CutcliTemplatePreview.textClass === 'function') {
          exactClass = ' ' + window.CutcliTemplatePreview.textClass(state.selectedTemplate, target.key, outputOrientation());
        }
        if (typeof window.CutcliTemplatePreview.inlineStyle === 'function') {
          exactStyle = window.CutcliTemplatePreview.inlineStyle(state.selectedTemplate, target.key, outputOrientation()) || '';
        }
      }
      var textHtml = '<span>' + escapeHtml(value).replace(/\r\n|\r|\n/g, '<br>') + '</span>';
      if (window.CutcliTemplatePreview && typeof window.CutcliTemplatePreview.textHtml === 'function') {
        var previewValues = Object.assign({}, state.overlayTexts || {}, { caption: target.label });
        textHtml = window.CutcliTemplatePreview.textHtml(state.selectedTemplate, target.key, previewValues, outputOrientation()) || textHtml;
      }
      return '<div class="mcm-layout-item is-' + escapeHtml(kind) + exactClass + '" data-layout-key="' + escapeHtml(target.key)
        + '" style="left:' + (position.x_ratio * 100).toFixed(2) + '%;top:' + (position.y_ratio * 100).toFixed(2) + '%;' + escapeHtml(exactStyle) + '">'
        + '<i></i>' + textHtml + '</div>';
    }).join('');
  }

  function renderTemplateLayout() {
    var panel = $('mcmTemplateLayoutPanel');
    var editor = $('mcmTemplateLayoutEditor');
    var media = $('mcmTemplateLayoutMedia');
    if (!panel || !editor || !media) return;
    if (currentTemplateProvider() === 'shanjian') {
      panel.hidden = true;
      return;
    }
    panel.hidden = !state.selectedTemplate;
    if (!state.selectedTemplate) return;
    var clip = state.clips[0] || {};
    var portrait = Number(clip.height || 0) > Number(clip.width || 0)
      || (!clip.width && String(state.selectedTemplate.aspect_ratio || '').indexOf('9:16') >= 0);
    editor.classList.toggle('is-portrait', portrait);
    var source = layoutPreviewSource();
    if (editor.dataset.previewSource !== source) {
      editor.dataset.previewSource = source;
      media.innerHTML = source
        ? '<video src="' + escapeHtml(source) + '" muted playsinline preload="metadata"></video>'
        : '<div class="mcm-layout-empty">版式预览</div>';
    }
    renderTemplateLayoutItems();
  }

  function selectTemplate(item) {
    var sameTemplate = !!(item && state.selectedTemplate && String(item.id) === String(state.selectedTemplate.id));
    var previous = state.overlayTexts || {};
    var next = {};
    templateOverlayFields(item).forEach(function(field) {
      var key = String(field.key || '').trim();
      var maxLength = overlayFieldMaxLength(field);
      var initial = Object.prototype.hasOwnProperty.call(previous, key) ? previous[key] : String(field.default || '');
      next[key] = truncateChars(initial, maxLength);
    });
    state.selectedTemplate = item || null;
    state.overlayTexts = next;
    if (!sameTemplate) state.positionOverrides = {};
    if (!item || item.provider !== 'shanjian') {
      state.shanjianTemplateDetail = null;
      state.shanjianTemplateDetailId = '';
      state.shanjianTemplateDetailPromise = null;
      state.shanjianTemplateDetailError = '';
    } else if (!sameTemplate || (!state.shanjianTemplateDetail && !state.shanjianTemplateDetailPromise)) {
      loadShanjianTemplateDetail(item).catch(function() {});
    }
    renderShanjianTemplateCapabilities();
  }

  function updateOverlayCounter(input) {
    if (!input) return;
    var maxLength = parseInt(input.getAttribute('maxlength') || input.dataset.overlayMax || 0, 10);
    var value = truncateChars(input.value || '', maxLength);
    if (input.value !== value) input.value = value;
    var counter = input.closest('.mcm-overlay-field');
    counter = counter ? counter.querySelector('[data-overlay-count]') : null;
    if (counter) counter.textContent = Array.from(value).length + '/' + maxLength;
  }

  function renderTemplateOverlayFields() {
    var panel = $('mcmTemplateOverlayPanel');
    var fieldsRoot = $('mcmTemplateOverlayFields');
    var shanjianPanel = $('mcmShanjianCopyPanel');
    if (!panel || !fieldsRoot) return;
    if (currentTemplateProvider() === 'shanjian') {
      panel.hidden = true;
      fieldsRoot.innerHTML = '';
      if (shanjianPanel) shanjianPanel.hidden = !state.selectedTemplate;
      renderShanjianTemplateCapabilities();
      return;
    }
    if (shanjianPanel) shanjianPanel.hidden = true;
    var titleField = templateTitleField(state.selectedTemplate);
    var titleKey = titleField ? String(titleField.key || '').trim() : '';
    var fields = templateOverlayFields(state.selectedTemplate).filter(function(field) {
      return String(field.key || '').trim() !== titleKey;
    });
    state.overlayTexts = overlayTextsWithTemplateTitle(state.selectedTemplate, state.overlayTexts);
    panel.hidden = !fields.length;
    fieldsRoot.innerHTML = fields.map(function(field) {
      var key = String(field.key || '').trim();
      var label = String(field.label || key);
      var maxLength = overlayFieldMaxLength(field);
      var value = truncateChars(Object.prototype.hasOwnProperty.call(state.overlayTexts, key) ? state.overlayTexts[key] : (field.default || ''), maxLength);
      state.overlayTexts[key] = value;
      var attrs = ' data-overlay-key="' + escapeHtml(key) + '" data-overlay-max="' + maxLength + '" maxlength="' + maxLength
        + '" placeholder="' + escapeHtml(overlayFieldPlaceholder(field)) + '"';
      var control = field.multiline
        ? '<textarea' + attrs + ' rows="2">' + escapeHtml(value) + '</textarea>'
        : '<input' + attrs + ' type="text" value="' + escapeHtml(value) + '">';
      return '<label class="mcm-overlay-field"><span><strong>' + escapeHtml(label) + '</strong><em data-overlay-count>'
        + Array.from(value).length + '/' + maxLength + '</em></span>' + control + '</label>';
    }).join('');
  }

  function readTemplateOverlayTexts() {
    var result = {};
    Array.prototype.forEach.call(document.querySelectorAll('#mcmTemplateOverlayFields [data-overlay-key]'), function(input) {
      updateOverlayCounter(input);
      var key = String(input.dataset.overlayKey || '').trim();
      if (key) result[key] = String(input.value || '').trim();
    });
    state.overlayTexts = overlayTextsWithTemplateTitle(state.selectedTemplate, result);
    return state.overlayTexts;
  }

  function filterTemplates() {
    var search = String((($('mcmTemplateSearch') || {}).value || '')).trim().toLowerCase();
    state.templates = state.templateCatalog.filter(function(item) {
      if (!search) return true;
      var tags = Array.isArray(item.tags) ? item.tags.join(' ') : '';
      return [item.name, item.description, item.id, tags].join(' ').toLowerCase().indexOf(search) >= 0;
    });
    renderTemplates();
  }

  function normalizeShanjianTemplate(item) {
    item = item || {};
    var id = item.id || item.styleId || item.style_id || item.templateId || item.template_id || '';
    return Object.assign({}, item, {
      id: String(id),
      name: item.name || item.title || item.styleName || item.style_name || '闪剪模板',
      coverUrl: item.coverUrl || item.cover_url || item.cover || '',
      preview_url: item.preview_url || item.previewUrl || item.demoUrl || item.demo_url || item.videoUrl || item.video_url || '',
      provider: 'shanjian'
    });
  }

  function loadTemplates(refresh) {
    var provider = currentTemplateProvider();
    if (!refresh && state.templateCatalog.length && state.templateCatalogProvider === provider) {
      filterTemplates();
      return Promise.resolve(state.templates);
    }
    if (provider === 'shanjian') {
      return post('/api/shanjian-smart-clip/templates', {
        scene: 'newsMixCutting',
        page_size: 36,
        sort_by: 'desc'
      }).then(function(data) {
        state.templateCatalog = (Array.isArray(data.results) ? data.results : []).map(normalizeShanjianTemplate).filter(function(item) {
          return !!item.id;
        });
        state.templateCatalogProvider = provider;
        if (state.selectedTemplate) {
          selectTemplate(state.templateCatalog.find(function(item) { return String(item.id) === String(state.selectedTemplate.id); }) || null);
        }
        filterTemplates();
        if (!state.templateCatalog.length) throw new Error('闪剪暂时没有返回可用模板。');
        return state.templates;
      });
    }
    return request('/api/cutcli/local/templates', { headers: headers(false) }).then(function(data) {
      state.templateCatalog = Array.isArray(data.templates) ? data.templates : [];
      state.templateCatalogProvider = provider;
      if (state.selectedTemplate) {
        selectTemplate(state.templateCatalog.find(function(item) { return String(item.id) === String(state.selectedTemplate.id); }) || null);
      }
      filterTemplates();
      if (!state.templateCatalog.length) throw new Error('模板定制中暂时没有可用模板。');
      return state.templates;
    });
  }

  function renderMusicOptions() {
    var grid = $('mcmMusicGrid');
    if (!grid) return;
    var randomMusic = currentClipMode() === 'random' && (($('mcmRandomMusicSwitch') || {}).checked);
    if ($('mcmMusicGrid')) $('mcmMusicGrid').hidden = randomMusic;
    grid.innerHTML = randomMusic ? '' : state.musicOptions.map(function(item, index) {
      var selected = state.selectedMusic && String(state.selectedMusic.key) === String(item.key);
      return '<button type="button" class="mcm-music-card' + (selected ? ' is-selected' : '') + '" data-music-index="' + index + '">'
        + '<span class="mcm-music-play" data-play-music="' + index + '" title="试听">▶</span>'
        + '<span><strong>' + escapeHtml(item.music_name || '背景音乐') + '</strong><small>' + escapeHtml(item.note || (item.has_preview ? '可试听' : '默认音乐')) + '</small></span></button>';
    }).join('');
    if ($('mcmMusicPicked')) $('mcmMusicPicked').textContent = randomMusic ? '每条随机选择' : (state.selectedMusic ? ('已选：' + (state.selectedMusic.music_name || '背景音乐')) : '还未选择');
  }

  function loadMusicOptions() {
    return request('/api/local-bestseller/bgm-options', { headers: headers(false) }).then(function(data) {
      state.musicOptions = (data.items || []).filter(function(item) { return !!item.bgm_url; });
      renderMusicOptions();
    });
  }

  function playMusic(index) {
    var item = state.musicOptions[index];
    if (!item || !item.bgm_url) return;
    if (state.previewAudio) {
      state.previewAudio.pause();
      state.previewAudio = null;
    }
    state.previewAudio = new Audio(item.bgm_url);
    state.previewAudio.volume = Math.min(1, Number((($('mcmMusicVolume') || {}).value || 0.24)));
    state.previewAudio.play().catch(function(error) { showMessage('音乐试听失败：' + (error.message || error), true); });
  }

  function progress(stage, status, text) {
    var item = document.querySelector('#mcmProgressList [data-stage="' + stage + '"]');
    if (!item) return;
    item.classList.remove('is-active', 'is-done', 'is-error');
    if (status) item.classList.add('is-' + status);
    var detail = item.querySelector('small');
    if (detail && text) detail.textContent = text;
  }

  function resetProgress() {
    ['merge', 'music', 'template', 'done'].forEach(function(stage) { progress(stage, '', '等待开始'); });
    progress('music', '', $('mcmMusicSwitch') && $('mcmMusicSwitch').checked ? '等待处理' : '本次跳过');
    progress('template', '', $('mcmTemplateSwitch') && $('mcmTemplateSwitch').checked ? '等待处理' : '本次跳过');
    progress('done', '', '完成后自动进入素材库');
  }

  function setResultVideo(result) {
    var surface = $('mcmResultSurface');
    if (!surface || !result) return Promise.resolve();
    if (state.resultObjectUrl) {
      URL.revokeObjectURL(state.resultObjectUrl);
      state.resultObjectUrl = '';
    }
    var source = resolveUrl(result.source_url || result.video_url || result.open_url || '', apiBase());
    var preview = resolveUrl(result.preview_url || '', apiBase());
    var resolveSource = source
      ? Promise.resolve(source)
      : fetch(preview, { headers: headers(false) }).then(function(response) {
          if (!response.ok) throw new Error('成片预览读取失败');
          return response.blob();
        }).then(function(blob) {
          state.resultObjectUrl = URL.createObjectURL(blob);
          return state.resultObjectUrl;
        });
    return resolveSource.then(function(videoUrl) {
      surface.innerHTML = '<video controls playsinline src="' + escapeHtml(videoUrl) + '"></video>';
      var actions = $('mcmResultActions');
      if (actions) actions.hidden = false;
      var open = $('mcmOpenResultBtn');
      if (open) open.href = videoUrl;
      if ($('mcmTotalDuration') && result.duration) $('mcmTotalDuration').textContent = formatSeconds(result.duration);
    });
  }

  function pollTemplateTask(taskId, createdAt) {
    return new Promise(function(resolve, reject) {
      var startedAt = Number(createdAt || Date.now());
      function timedOut() {
        return Date.now() - startedAt > 30 * 60 * 1000;
      }
      function schedulePoll(message) {
        progress('template', 'active', message || '模板处理中');
        state.templatePollTimer = setTimeout(poll, 5000);
      }
      function poll() {
        state.templatePollTimer = null;
        if (timedOut()) return reject(new Error('模板处理超过 30 分钟，可稍后继续查询原任务'));
        request('/api/cutcli/local/templates/jobs/' + encodeURIComponent(taskId), { headers: headers(false) }).then(function(data) {
          var status = String(data.status || '').toLowerCase();
          if ((status === 'completed' || status === 'success') && (data.open_url || data.preview_url || data.preview_asset_id || data.final_asset_id)) return resolve(data);
          if (status === 'failed' || status === 'error' || data.ok === false) {
            clearPendingTemplateTask();
            return reject(new Error(data.error || data.error_code || '模板定制处理失败'));
          }
          schedulePoll(data.stage ? ('正在处理：' + data.stage) : ('任务 ' + taskId + ' 处理中'));
        }).catch(function() {
          if (timedOut()) return reject(new Error('模板处理超过 30 分钟，可稍后继续查询原任务'));
          schedulePoll('网络暂时中断，5 秒后继续查询任务 ' + taskId);
        });
      }
      poll();
    });
  }

  function finishTemplateTask(task, taskId, baseResult) {
    var baseDuration = Number((baseResult || {}).duration || 0);
    var assetId = task.final_asset_id || task.preview_asset_id || '';
    var resultUrl = resolveUrl(task.open_url || task.preview_url || '', apiBase());
    var saved = {
      asset_id: assetId,
      source_url: resultUrl,
      preview_url: resultUrl || (assetId ? ('/api/assets/' + encodeURIComponent(assetId) + '/content') : ''),
      duration: baseDuration,
      completion_message: '模板成片已完成，基础成片和模板成片均已保存在素材库。'
    };
    progress('template', 'done', task.caption_count ? ('模板处理完成，生成 ' + task.caption_count + ' 条字幕') : '模板定制处理完成');
    clearPendingTemplateTask();
    return setResultVideo(saved).then(function() { return saved; });
  }

  function pollShanjianTask(taskId) {
    return new Promise(function(resolve, reject) {
      var startedAt = Date.now();
      function poll() {
        if (Date.now() - startedAt > 30 * 60 * 1000) return reject(new Error('闪剪模板处理超过 30 分钟，可稍后查看任务结果'));
        post('/api/shanjian-smart-clip/task', { task_id: taskId }, true).then(function(data) {
          var status = String(data.status || '').toLowerCase();
          if ((status === 'succeed' || status === 'completed' || status === 'success') && data.video_url) return resolve(data);
          if (status === 'failed' || data.ok === false) return reject(new Error(data.message || data.error_code || '闪剪模板处理失败'));
          progress('template', 'active', data.status_text || ('闪剪任务 ' + taskId + ' 处理中'));
          setTimeout(poll, 5000);
        }).catch(function(error) {
          if (Date.now() - startedAt > 30 * 60 * 1000) return reject(error);
          progress('template', 'active', '闪剪查询暂时中断，5 秒后继续');
          setTimeout(poll, 5000);
        });
      }
      poll();
    });
  }

  function normalizeShanjianTitle(value) {
    var title = String(value || '').replace(/\r\n?/g, '\n').trim();
    if (Array.from(title).length < 3) title = '智能剪辑';
    return Array.from(title).slice(0, 80).join('');
  }

  function applyShanjianTemplate(baseResult) {
    if (!state.selectedTemplate) return Promise.reject(new Error('请先选择闪剪模板'));
    var videoUrl = baseResult && (baseResult.source_url || baseResult.video_url || '');
    if (!videoUrl) return Promise.reject(new Error('基础成片没有公网链接，无法提交闪剪模板；请先保证 TOS/转存成功。'));
    var title = normalizeShanjianTitle(templateTitleValue());
    var description = String((($('mcmShanjianDescription') || {}).value || '').trim()).slice(0, 240);
    progress('template', 'active', '正在提交闪剪模板任务');
    return loadShanjianTemplateDetail(state.selectedTemplate).then(function(detailResponse) {
      var editInfo = shanjianEditInfo(detailResponse);
      var supportsIpLayer = !!editInfo.ipLayer;
      var structLayers = [];
      if (editInfo.headerLayer) structLayers.push({ markCode: 'headerLayer', show: true });
      if (supportsIpLayer && description) structLayers.push({ markCode: 'ipLayer', show: true });
      return post('/api/shanjian-smart-clip/submit', {
      title: title,
      scene: 'newsMixCutting',
      style_id: state.selectedTemplate.id,
      materials: [{ type: 'video', fileUrl: videoUrl, soundSwitch: true }],
      material_sound_switch: true,
      material_composition: 'order',
      video_duration: Math.max(5, Math.round(Number(baseResult.duration || 30))),
      // 闪剪 introduceCard.name 是必填元数据；页面没有单独的 name 输入，服务端会以标题兜底。
      introduce_name: supportsIpLayer && description ? title : '',
      introduce_description: supportsIpLayer ? description : '',
      struct_layers: structLayers,
      header_switch: true,
      material_switch: true,
      subtitle_switch: true,
      keyword_switch: true,
      watermark_show: true
      });
    }).then(function(submitted) {
      if (!submitted.task_id) throw new Error('闪剪没有返回任务编号');
      return pollShanjianTask(submitted.task_id);
    }).then(function(task) {
      var saved = {
        source_url: task.video_url || '',
        preview_url: task.video_url || '',
        duration: Number(task.duration || baseResult.duration || 0),
        introduce_name: title,
        introduce_description: description,
        completion_message: '闪剪模板成片已完成，可打开预览。'
      };
      progress('template', 'done', '闪剪模板处理完成');
      return setResultVideo(saved).then(function() { return saved; });
    });
  }

  function applyTemplate(baseResult) {
    if (!state.selectedTemplate) return Promise.reject(new Error('请先选择剪辑模板'));
    if (!baseResult || (!baseResult.asset_id && !baseResult.source_url)) return Promise.reject(new Error('基础成片没有可用的素材记录，无法提交模板定制任务'));
    if (currentTemplateProvider() === 'shanjian') return applyShanjianTemplate(baseResult);
    progress('template', 'active', '正在提交模板定制任务');
    if ($('mcmRetryTemplateBtn')) $('mcmRetryTemplateBtn').hidden = true;
    var modes = Array.isArray(state.selectedTemplate.render_modes) ? state.selectedTemplate.render_modes : ['ffmpeg'];
    var renderMode = modes.indexOf('ffmpeg') >= 0 ? 'ffmpeg' : (modes[0] || 'ffmpeg');
    return post('/api/cutcli/local/tasks/start', {
      template_id: state.selectedTemplate.id,
      render_mode: renderMode,
      asset_id: baseResult.asset_id || '',
      video_url: baseResult.asset_id ? '' : (baseResult.source_url || ''),
      overlay_texts: readTemplateOverlayTexts(),
      position_overrides: templatePositionOverridesForSubmit(),
      external_task_id: 'multi_clip_mixer_' + Date.now()
    }).then(function(submitted) {
      if (!submitted.job_id) throw new Error('模板定制没有返回任务编号');
      var pending = rememberPendingTemplateTask(submitted.job_id, baseResult);
      progress('template', 'active', '任务 ' + submitted.job_id + ' 处理中');
      return pollTemplateTask(submitted.job_id, pending.createdAt).then(function(task) {
        return finishTemplateTask(task, submitted.job_id, baseResult);
      });
    });
  }

  function selectedTemplateForRun(useTemplate) {
    if (!useTemplate) return null;
    if (currentClipMode() === 'random' && (($('mcmRandomTemplateSwitch') || {}).checked)) {
      return randomChoice(state.templates) || state.selectedTemplate;
    }
    return state.selectedTemplate;
  }

  function selectedMusicForRun(useMusic) {
    if (!useMusic) return null;
    if (currentClipMode() === 'random' && (($('mcmRandomMusicSwitch') || {}).checked)) {
      return randomChoice(state.musicOptions) || state.selectedMusic;
    }
    return state.selectedMusic;
  }

  function templateStateSnapshot() {
    return {
      selectedTemplate: state.selectedTemplate,
      overlayTexts: Object.assign({}, state.overlayTexts || {}),
      positionOverrides: mergePositionOverrides({}, state.positionOverrides || {})
    };
  }

  function restoreTemplateState(snapshot) {
    if (!snapshot) return;
    state.selectedTemplate = snapshot.selectedTemplate || null;
    state.overlayTexts = Object.assign({}, snapshot.overlayTexts || {});
    state.positionOverrides = mergePositionOverrides({}, snapshot.positionOverrides || {});
    renderTemplates();
  }

  function runOneRender(runIndex, totalRuns, options) {
    var music = selectedMusicForRun(options.useMusic);
    var audioClip = markedAudioClip();
    var templateBeforeRun = templateStateSnapshot();
    var template = selectedTemplateForRun(options.useTemplate);
    if (options.useTemplate && template) selectTemplate(template);
    progress('merge', 'active', '正在处理第 ' + runIndex + '/' + totalRuns + ' 条，' + state.clips.length + ' 个视频片段');
    return post('/api/multi-clip-mixer/render', {
      title: templateTitleValue() || (totalRuns > 1 ? ('多段视频混剪 ' + runIndex) : '多段视频混剪'),
      clips: buildClipPlan(runIndex),
      keep_original_audio: false,
      audio_asset_id: audioClip ? audioClip.assetId : '',
      target_duration: audioClip ? Number(audioClip.duration || 0) : null,
      audio_volume: 1,
      clip_mode: currentClipMode(),
      output_index: runIndex,
      bgm_url: music ? music.bgm_url : '',
      bgm_name: music ? music.music_name : '',
      bgm_volume: Number((($('mcmMusicVolume') || {}).value || 0.24))
    }).then(function(baseResult) {
      state.lastBaseResult = baseResult;
      progress('merge', 'done', '第 ' + runIndex + '/' + totalRuns + ' 条基础成片完成，共 ' + formatSeconds(baseResult.duration));
      progress('music', 'done', music ? ('已添加 ' + music.music_name) : (audioClip ? '已使用标记视频主音轨' : '本次未添加音乐'));
      return setResultVideo(baseResult).then(function() {
        if (!options.useTemplate) {
          progress('template', 'done', '本次未套用模板');
          return baseResult;
        }
        setBusy(true, '正在套用模板 ' + runIndex + '/' + totalRuns + '...');
        return applyTemplate(baseResult);
      });
    }).then(function(result) {
      result = result || {};
      if (template) result.template_name = template.name || '';
      appendBatchResult(result);
      if (options.useTemplate) restoreTemplateState(templateBeforeRun);
      return result;
    }).catch(function(error) {
      if (options.useTemplate) restoreTemplateState(templateBeforeRun);
      throw error;
    });
  }

  function generate() {
    if (state.busy) return;
    if (!state.clips.length) return showMessage('请先添加至少一个视频。', true);
    var useTemplate = !!(($('mcmTemplateSwitch') || {}).checked);
    var useMusic = !!(($('mcmMusicSwitch') || {}).checked);
    setBusy(true, '正在准备素材...');
    Promise.resolve().then(function() {
      if (useTemplate && !state.templateCatalog.length) return loadTemplates(true);
      return null;
    }).then(function() {
      if (useMusic && !state.musicOptions.length) return loadMusicOptions();
      return null;
    }).then(function() {
      var canRandomTemplate = currentClipMode() === 'random' && (($('mcmRandomTemplateSwitch') || {}).checked) && state.templates.length;
      if (useTemplate && !state.selectedTemplate && !canRandomTemplate) throw new Error('已开启定制模板，请先选择一个模板，或在随机模式中开启模板随机。');
      if (useMusic && !state.selectedMusic && !(currentClipMode() === 'random' && (($('mcmRandomMusicSwitch') || {}).checked) && state.musicOptions.length)) {
        throw new Error('已开启音乐模板，请先选择一首音乐，或在随机模式中开启音乐随机。');
      }
      showMessage('', false);
      var totalRuns = outputCount();
      state.batchResults = [];
      state.batchSelection = {};
      startHistoryBatch({ useTemplate: useTemplate, useMusic: useMusic }, totalRuns);
      renderBatchResults();
      resetProgress();
      setBusy(true, '正在裁剪并拼接...');
      var chain = Promise.resolve();
      for (var index = 1; index <= totalRuns; index += 1) {
        (function(runIndex) {
          chain = chain.then(function() {
            setBusy(true, totalRuns > 1 ? ('正在生成 ' + runIndex + '/' + totalRuns + '...') : '正在裁剪并拼接...');
            return runOneRender(runIndex, totalRuns, { useTemplate: useTemplate, useMusic: useMusic });
          });
        })(index);
      }
      return chain.then(function(result) {
        return { result: result, totalRuns: totalRuns };
      });
    }).then(function(payload) {
      var result = payload.result;
      var totalRuns = payload.totalRuns;
      progress('done', 'done', result && result.asset_id ? '成片已保存到素材库' : '成片已生成，可打开预览');
      showMessage((result && result.completion_message) || ('视频生成完成，共 ' + totalRuns + ' 条结果已保存。'), false);
      saveActiveHistoryBatch(true);
    }).catch(function(error) {
      var templateActive = document.querySelector('#mcmProgressList [data-stage="template"].is-active');
      if (templateActive && state.lastBaseResult) {
        progress('template', 'error', error.message || '模板处理失败');
        progress('done', 'done', '基础混剪成片已保存在素材库');
        if ($('mcmRetryTemplateBtn')) $('mcmRetryTemplateBtn').hidden = false;
        showMessage('基础混剪已完成，但模板处理失败：' + (error.message || error) + '。可以直接使用基础成片或单独重试模板。', true);
      } else {
        progress('merge', 'error', error.message || '生成失败');
        showMessage(error.message || '生成失败', true);
      }
    }).finally(function() {
      setBusy(false);
    });
  }

  function resumePendingTemplateTask() {
    if (state.busy) return Promise.resolve(false);
    var pending = loadPendingTemplateTask();
    if (!pending || !pending.taskId || !pending.baseResult) return Promise.resolve(false);
    pending.createdAt = Date.now();
    state.pendingTemplateTask = pending;
    try { localStorage.setItem(PENDING_TEMPLATE_STORAGE_KEY, JSON.stringify(pending)); } catch (error) {}
    state.lastBaseResult = pending.baseResult;
    state.batchResults = [];
    state.batchSelection = {};
    startHistoryBatch({ useTemplate: true, useMusic: false }, 1, '恢复的多段视频混剪');
    renderBatchResults();
    resetProgress();
    progress('merge', 'done', '基础混剪成片已完成，共 ' + formatSeconds(pending.baseResult.duration));
    progress('music', 'done', '基础成片已保留');
    progress('template', 'active', '正在继续查询任务 ' + pending.taskId);
    setBusy(true, '正在继续查询剪辑任务...');
    showMessage('正在恢复剪辑任务 ' + pending.taskId + '，无需重新提交。', false);
    return setResultVideo(pending.baseResult).then(function() {
      return pollTemplateTask(pending.taskId, pending.createdAt);
    }).then(function(task) {
      return finishTemplateTask(task, pending.taskId, pending.baseResult);
    }).then(function(result) {
      appendBatchResult(result);
      saveActiveHistoryBatch(true);
      progress('done', 'done', '模板成片已保存到素材库');
      if ($('mcmRetryTemplateBtn')) $('mcmRetryTemplateBtn').hidden = true;
      showMessage((result && result.completion_message)
        || ('剪辑任务 ' + pending.taskId + ' 已完成，成片已保存到素材库。'), false);
      return true;
    }).catch(function(error) {
      progress('template', 'error', error.message || '模板处理失败');
      progress('done', 'done', '基础混剪成片已保存在素材库');
      if ($('mcmRetryTemplateBtn')) $('mcmRetryTemplateBtn').hidden = false;
      showMessage(error.message || '模板处理失败', true);
      return false;
    }).finally(function() {
      setBusy(false);
    });
  }

  function retryTemplate() {
    if (state.busy) return;
    if (loadPendingTemplateTask()) {
      resumePendingTemplateTask();
      return;
    }
    if (!state.lastBaseResult) return;
    state.batchResults = [];
    state.batchSelection = {};
    startHistoryBatch({ useTemplate: true, useMusic: false }, 1, '多段视频混剪模板重试');
    renderBatchResults();
    setBusy(true, '正在重试模板定制...');
    showMessage('', false);
    applyTemplate(state.lastBaseResult).then(function(result) {
      appendBatchResult(result);
      saveActiveHistoryBatch(true);
      progress('done', 'done', '模板成片已保存到素材库');
      showMessage((result && result.completion_message) || '模板成片生成完成。', false);
    }).catch(function(error) {
      progress('template', 'error', error.message || '模板处理失败');
      showMessage('模板处理失败：' + (error.message || error), true);
      if ($('mcmRetryTemplateBtn')) $('mcmRetryTemplateBtn').hidden = false;
    }).finally(function() { setBusy(false); });
  }

  function bindEvents() {
    if ($('mcmBackBtn')) $('mcmBackBtn').addEventListener('click', function() {
      if (typeof window.showLobsterView === 'function') window.showLobsterView('skill-store');
    });
    if ($('mcmAddVideoBtn')) $('mcmAddVideoBtn').addEventListener('click', openVideoPicker);
    if ($('mcmClipEmpty')) $('mcmClipEmpty').addEventListener('click', openVideoPicker);
    if ($('mcmVideoInput')) $('mcmVideoInput').addEventListener('change', function(event) {
      var files = Array.prototype.slice.call(event.target.files || []).filter(function(file) {
        return /^video\//i.test(file.type || '') || /\.(mp4|mov|m4v|webm|avi|mkv)$/i.test(file.name || '');
      });
      state.pendingFiles = state.pendingFiles.concat(files);
      event.target.value = '';
      processNextPendingFile();
    });
    if ($('mcmClipList')) $('mcmClipList').addEventListener('click', function(event) {
      var button = event.target.closest('[data-action]');
      var item = event.target.closest('[data-clip-index]');
      if (!button || !item || state.busy) return;
      var index = Number(item.getAttribute('data-clip-index'));
      var action = button.getAttribute('data-action');
      if (action === 'edit') openSegmentForEdit(index);
      if (action === 'audio') {
        state.clips.forEach(function(clip, clipIndex) { clip.audioMaster = clipIndex === index ? !clip.audioMaster : false; });
        showMessage(state.clips[index] && state.clips[index].audioMaster
          ? '已把这个视频标记为整条成片的原声音轨。之后无论怎么切片，都使用它的整段音频。'
          : '已取消原声音轨标记。', false);
      }
      if (action === 'remove') state.clips.splice(index, 1);
      if (action === 'up' && index > 0) {
        var previous = state.clips[index - 1]; state.clips[index - 1] = state.clips[index]; state.clips[index] = previous;
      }
      if (action === 'down' && index < state.clips.length - 1) {
        var next = state.clips[index + 1]; state.clips[index + 1] = state.clips[index]; state.clips[index] = next;
      }
      if (action !== 'edit') renderClips();
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-mcm-close-segment]'), function(button) {
      button.addEventListener('click', function() {
        if (!state.segmentContext || !state.segmentContext.uploading) closeSegmentModal(true);
      });
    });
    if ($('mcmSegmentStart')) $('mcmSegmentStart').addEventListener('input', syncSegmentSummary);
    if ($('mcmSegmentEnd')) $('mcmSegmentEnd').addEventListener('input', syncSegmentSummary);
    if ($('mcmPreviewSegmentBtn')) $('mcmPreviewSegmentBtn').addEventListener('click', previewSelectedSegment);
    if ($('mcmConfirmSegmentBtn')) $('mcmConfirmSegmentBtn').addEventListener('click', confirmSegment);
    Array.prototype.forEach.call(document.querySelectorAll('input[name="mcmClipMode"]'), function(input) {
      input.addEventListener('change', syncModePanels);
    });
    if ($('mcmRandomTemplateSwitch')) $('mcmRandomTemplateSwitch').addEventListener('change', renderTemplates);
    if ($('mcmApplyFixedBtn')) $('mcmApplyFixedBtn').addEventListener('click', applyFixedRangeToAll);
    if ($('mcmTemplateSwitch')) $('mcmTemplateSwitch').addEventListener('change', function(event) {
      $('mcmTemplatePanel').hidden = !event.target.checked;
      if (event.target.checked && !state.templateCatalog.length) loadTemplates(true).catch(function(error) { showMessage(error.message, true); });
    });
    Array.prototype.forEach.call(document.querySelectorAll('input[name="mcmTemplateProvider"]'), function(input) {
      input.addEventListener('change', function() {
        state.templateCatalog = [];
        state.templateCatalogProvider = '';
        state.templates = [];
        state.selectedTemplate = null;
        state.overlayTexts = {};
        state.positionOverrides = {};
        renderTemplates();
        if (($('mcmTemplateSwitch') || {}).checked) loadTemplates(true).catch(function(error) { showMessage(error.message, true); });
      });
    });
    if ($('mcmMusicSwitch')) $('mcmMusicSwitch').addEventListener('change', function(event) {
      $('mcmMusicPanel').hidden = !event.target.checked;
      if (event.target.checked && !state.musicOptions.length) loadMusicOptions().catch(function(error) { showMessage(error.message, true); });
    });
    if ($('mcmRandomMusicSwitch')) $('mcmRandomMusicSwitch').addEventListener('change', renderMusicOptions);
    var searchTimer = null;
    if ($('mcmTemplateSearch')) $('mcmTemplateSearch').addEventListener('input', function() {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(filterTemplates, 200);
    });
    if ($('mcmTemplateMoreBtn')) $('mcmTemplateMoreBtn').addEventListener('click', function() {
      loadTemplates(false).catch(function(error) { showMessage(error.message, true); });
    });
    if ($('mcmTemplateGrid')) $('mcmTemplateGrid').addEventListener('click', function(event) {
      var card = event.target.closest('[data-template-id]');
      if (!card) return;
      selectTemplate(state.templates.find(function(item) { return String(item.id) === String(card.getAttribute('data-template-id')); }) || null);
      renderTemplates();
    });
    if ($('mcmTemplateOverlayFields')) $('mcmTemplateOverlayFields').addEventListener('input', function(event) {
      var input = event.target.closest('[data-overlay-key]');
      if (!input) return;
      updateOverlayCounter(input);
      var key = String(input.dataset.overlayKey || '').trim();
      if (key) state.overlayTexts[key] = input.value || '';
      renderTemplateLayoutItems();
    });
    if ($('mcmTemplateTitle')) $('mcmTemplateTitle').addEventListener('input', function(event) {
      var value = truncateChars(String(event.target.value || '').replace(/\r\n?/g, '\n').trim(), 80);
      if (event.target.value !== value) event.target.value = value;
      state.overlayTexts = overlayTextsWithTemplateTitle(state.selectedTemplate, state.overlayTexts);
      renderTemplateLayoutItems();
    });
    if ($('mcmTemplateLayoutReset')) $('mcmTemplateLayoutReset').addEventListener('click', function() {
      state.positionOverrides = {};
      renderTemplateLayoutItems();
    });
    if ($('mcmTemplateLayoutLayer')) $('mcmTemplateLayoutLayer').addEventListener('pointerdown', function(event) {
      var item = event.target.closest('[data-layout-key]');
      var layer = $('mcmTemplateLayoutLayer');
      if (!item || !layer || (event.button != null && event.button !== 0)) return;
      event.preventDefault();
      var key = String(item.dataset.layoutKey || '');
      var bounds = layer.getBoundingClientRect();
      item.classList.add('is-dragging');
      function move(pointerEvent) {
        pointerEvent.preventDefault();
        var x = clamp((pointerEvent.clientX - bounds.left) / Math.max(1, bounds.width), 0.05, 0.95);
        var y = clamp((pointerEvent.clientY - bounds.top) / Math.max(1, bounds.height), 0.05, 0.95);
        setTargetPosition(key, x, y);
        item.style.left = (x * 100).toFixed(2) + '%';
        item.style.top = (y * 100).toFixed(2) + '%';
      }
      function stop(pointerEvent) {
        move(pointerEvent);
        item.classList.remove('is-dragging');
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', stop);
        window.removeEventListener('pointercancel', stop);
      }
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', stop);
      window.addEventListener('pointercancel', stop);
    });
    if ($('mcmTemplateGrid')) $('mcmTemplateGrid').addEventListener('dblclick', function(event) {
      var card = event.target.closest('[data-template-id]');
      if (!card) return;
      var item = state.templates.find(function(row) { return String(row.id) === String(card.getAttribute('data-template-id')); });
      var url = item ? templateDemo(item) : '';
      if (!url) return showMessage('这个模板没有提供样片。', true);
      if ($('mcmPreviewTitle')) $('mcmPreviewTitle').textContent = item.name || '模板预览';
      if ($('mcmTemplatePreviewVideo')) $('mcmTemplatePreviewVideo').src = url;
      if ($('mcmTemplatePreviewModal')) $('mcmTemplatePreviewModal').hidden = false;
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-mcm-close-preview]'), function(button) {
      button.addEventListener('click', function() {
        if ($('mcmTemplatePreviewVideo')) { $('mcmTemplatePreviewVideo').pause(); $('mcmTemplatePreviewVideo').removeAttribute('src'); }
        if ($('mcmTemplatePreviewModal')) $('mcmTemplatePreviewModal').hidden = true;
      });
    });
    if ($('mcmMusicGrid')) $('mcmMusicGrid').addEventListener('click', function(event) {
      var card = event.target.closest('[data-music-index]');
      if (!card) return;
      var index = Number(card.getAttribute('data-music-index'));
      if (event.target.closest('[data-play-music]')) {
        event.preventDefault();
        event.stopPropagation();
        playMusic(index);
        return;
      }
      state.selectedMusic = state.musicOptions[index] || null;
      renderMusicOptions();
    });
    if ($('mcmMusicVolume')) $('mcmMusicVolume').addEventListener('input', function(event) {
      var value = Number(event.target.value || 0);
      if ($('mcmMusicVolumeValue')) $('mcmMusicVolumeValue').textContent = Math.round(value * 100) + '%';
      if (state.previewAudio) state.previewAudio.volume = value;
    });
    if ($('mcmGenerateBtn')) $('mcmGenerateBtn').addEventListener('click', generate);
    if ($('mcmRetryTemplateBtn')) $('mcmRetryTemplateBtn').addEventListener('click', retryTemplate);
    if ($('mcmHistoryToggleBtn')) $('mcmHistoryToggleBtn').addEventListener('click', function() {
      if (!state.history.length) return showMessage('还没有历史记录，完成一次生成后会自动保留在这里。', false);
      state.historyVisible = !state.historyVisible;
      renderHistory();
    });
    if ($('mcmHistoryPrevBtn')) $('mcmHistoryPrevBtn').addEventListener('click', function() {
      if (state.historyPage <= 0) return;
      state.historyPage -= 1;
      renderHistory();
    });
    if ($('mcmHistoryNextBtn')) $('mcmHistoryNextBtn').addEventListener('click', function() {
      var maxPage = Math.max(0, Math.ceil(state.history.length / HISTORY_PAGE_SIZE) - 1);
      if (state.historyPage >= maxPage) return;
      state.historyPage += 1;
      renderHistory();
    });
    if ($('mcmBatchResults')) {
      $('mcmBatchResults').addEventListener('change', function(event) {
        var checkbox = event.target.closest('input[type="checkbox"]');
        if (!checkbox) return;
        if (checkbox.hasAttribute('data-batch-select-all')) {
          state.batchResults.forEach(function(result, index) {
            if (resultVideoUrl(result)) state.batchSelection[index] = checkbox.checked;
          });
        } else if (checkbox.hasAttribute('data-batch-index')) {
          state.batchSelection[Number(checkbox.getAttribute('data-batch-index'))] = checkbox.checked;
        }
        renderBatchResults();
      });
      $('mcmBatchResults').addEventListener('click', function(event) {
        var action = event.target.closest('[data-batch-action]');
        if (action) {
          var urls = currentBatchSelectedUrls();
          if (action.getAttribute('data-batch-action') === 'download') downloadVideoUrls(urls, (state.activeHistoryBatch || {}).title || '多段视频混剪');
          if (action.getAttribute('data-batch-action') === 'copy') copyVideoUrls(urls);
          return;
        }
        var button = event.target.closest('[data-batch-preview]');
        if (!button) return;
        var result = state.batchResults[Number(button.getAttribute('data-batch-preview'))];
        if (result) setResultVideo(result).catch(function(error) { showMessage(error.message || '预览失败', true); });
      });
    }
    if ($('mcmHistoryList')) {
      $('mcmHistoryList').addEventListener('change', function(event) {
        var checkbox = event.target.closest('input[type="checkbox"]');
        var item = event.target.closest('[data-history-id]');
        if (!checkbox || !item) return;
        var batch = state.history.find(function(row) { return row.id === item.getAttribute('data-history-id'); });
        if (!batch) return;
        var selection = state.historySelection[batch.id] || {};
        if (checkbox.hasAttribute('data-history-select-all')) {
          (batch.results || []).forEach(function(result, index) {
            if (resultVideoUrl(result)) selection[historyResultId(result, index)] = checkbox.checked;
          });
        } else if (checkbox.hasAttribute('data-history-result')) {
          selection[checkbox.getAttribute('data-history-result')] = checkbox.checked;
        }
        state.historySelection[batch.id] = selection;
        renderHistory();
      });
      $('mcmHistoryList').addEventListener('click', function(event) {
        var item = event.target.closest('[data-history-id]');
        if (!item) return;
        var batch = state.history.find(function(row) { return row.id === item.getAttribute('data-history-id'); });
        if (!batch) return;
        var action = event.target.closest('[data-history-action]');
        if (action) {
          var actionName = action.getAttribute('data-history-action');
          if (actionName === 'download') downloadVideoUrls(historySelectedUrls(batch), batch.title || '多段视频混剪');
          if (actionName === 'copy') copyVideoUrls(historySelectedUrls(batch));
          if (actionName === 'delete') {
            if (!window.confirm('删除这批混剪历史记录？已生成的视频不会从素材库删除。')) return;
            state.history = state.history.filter(function(row) { return row.id !== batch.id; });
            delete state.historySelection[batch.id];
            saveHistory();
            renderHistory();
          }
          return;
        }
        var preview = event.target.closest('[data-history-preview]');
        if (!preview) return;
        var result = (batch.results || [])[Number(preview.getAttribute('data-history-preview'))];
        if (result) setResultVideo(result).catch(function(error) { showMessage(error.message || '预览失败', true); });
      });
    }
  }

  window.initMultiClipMixerView = function() {
    var root = $('content-multi-clip-mixer');
    if (!root || root.dataset.mcmInitialized === '1') return;
    root.dataset.mcmInitialized = '1';
    bindEvents();
    loadHistory();
    syncModePanels();
    renderClips();
    renderBatchResults();
    renderHistory();
    resetProgress();
    resumePendingTemplateTask();
  };

  window.addMultiClipMixerAsset = function(item) {
    if (state.busy) return false;
    addAssetVideoClip(item || {});
    showMessage('已加入视频列表，可直接生成或点“编辑”微调片段。', false);
    return true;
  };

  window.addMultiClipMixerAssets = function(items) {
    if (state.busy) return 0;
    var added = 0;
    (Array.isArray(items) ? items : [items]).forEach(function(item) {
      try {
        addAssetVideoClip(item || {});
        added += 1;
      } catch (error) {}
    });
    if (added) showMessage('已加入 ' + added + ' 个素材视频，可直接生成或点“编辑”微调片段。', false);
    return added;
  };
})();
