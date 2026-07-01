(function() {
  var state = { currentJobId: '', lastItems: [] };
  function isYunwuVeoModel(model) {
    var value = String(model || '').toLowerCase().replace(/\s+/g, '');
    return value === 'yunwu-veo3.1-plus' || value === 'veo3.1-plus' || value === 'veo3.1' || value === 'yingmeng-plus' || value === '褰辨ⅵplus';
  }
  function isOpenMindGrokModel(model) {
    var value = String(model || '').toLowerCase().replace(/\s+/g, '');
    return value === 'grok-imagine-video-1.5-preview' || value === 'yingmeng1.5plus' || value === '褰辨ⅵ1.5plus';
  }
  function videoRequestForModel(model) {
    if (isOpenMindGrokModel(model)) return { model: 'grok-imagine-video-1.5-preview', channel: 'openmind' };
    if (isYunwuVeoModel(model)) return { model: 'veo3.1', channel: 'yunwu' };
    return { model: model, channel: '' };
  }
  function modelDisplayName(model) {
    var value = String(model || '').trim();
    if (!value) return '--';
    if (isOpenMindGrokModel(value) || value === 'grok-imagine-video-1.5-preview' || value === 'yingmeng1.5plus' || value === '影梦1.5plus') return '影梦1.5pro';
    if (isYunwuVeoModel(value) || value === 'veo3.1') return '影梦plus';
    if (value === 'gpt-image-2' || value === 'gpt-image-2-yunwu') return 'GPT 图片';
    return value;
  }
  function base() { return (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE ? String(LOCAL_API_BASE) : '').replace(/\/$/, ''); }
  function headers() { return Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : {}); }
  function esc(text) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(text || ''));
    return String(text || '').replace(/[&<>"']/g, function(ch) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]; });
  }
  function el(id) { return document.getElementById(id); }
  function setMsg(text, isErr) {
    var box = el('viralTvcMsg');
    if (!box) return;
    box.textContent = text || '';
    box.className = 'viral-tvc-msg' + (isErr ? ' err' : '');
    box.style.display = text ? 'block' : 'none';
  }
  function setBusy(btn, busy, text) {
    if (!btn) return;
    if (busy) {
      btn.dataset.oldText = btn.textContent || '';
      btn.textContent = text || '处理中...';
      btn.disabled = true;
    } else {
      btn.textContent = btn.dataset.oldText || btn.textContent || '';
      btn.disabled = false;
    }
  }
  function responseErrorText(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.error || data.message;
    if (typeof detail === 'string') return detail;
    try { return JSON.stringify(detail || data); } catch (e) { return fallback || '请求失败'; }
  }
  function statusLabel(status) {
    var key = String(status || '').toLowerCase();
    if (key === 'completed' || key === 'succeeded' || key === 'success') return '已完成';
    if (key === 'failed' || key === 'error') return '失败';
    return '执行中';
  }
  function statusClass(status) {
    var key = String(status || '').toLowerCase();
    if (key === 'completed' || key === 'succeeded' || key === 'success') return ' is-success';
    if (key === 'failed' || key === 'error') return ' is-failed';
    return '';
  }
  function buildPayload() {
    var prompt = (el('viralTvcPrompt') ? el('viralTvcPrompt').value : '').trim();
    if (!prompt) throw new Error('请先填写视频需求。');
    var duration = Number((el('viralTvcDuration') || {}).value || 60);
    var ratio = String((el('viralTvcRatio') || {}).value || '9:16');
    var tone = String((el('viralTvcTone') || {}).value || 'premium');
    var model = String((el('viralTvcModel') || {}).value || 'grok-imagine-video-1.5-preview');
    var videoRequest = videoRequestForModel(model);
    var segmentSeconds = isYunwuVeoModel(model) ? 8 : 10;
    var segmentCount = Math.max(1, Math.round(duration / segmentSeconds));
    var needAudio = String((el('viralTvcAudio') || {}).value || 'true') !== 'false';
    var needMerge = String((el('viralTvcSaveMode') || {}).value || 'merge') === 'merge';
    return {
      payload: {
        aspect_ratio: ratio,
        visual_tone: tone,
        rhythm: tone,
        duration: duration,
        segment_count: segmentCount,
        segment_duration_seconds: segmentSeconds,
        total_duration_seconds: segmentCount * segmentSeconds,
        workflow_mode: 'storyboard',
        merge_clips: needMerge,
        auto_save: true,
        task_text: prompt,
        image_model: 'gpt-image-2',
        image_model_fallback: 'gpt-image-2-yunwu',
        video_model: videoRequest.model,
        video_channel: videoRequest.channel,
        video_fallbacks: isYunwuVeoModel(model) ? [{ channel: 'comfly', model: 'veo3.1-fast' }] : [],
        generate_audio: needAudio,
        watermark: false,
        input_mode: 'prompt_only'
      }
    };
  }
  function renderStatus(job) {
    var host = el('viralTvcStatusHost');
    if (!host) return;
    var listHtml = (state.lastItems || []).slice(0, 6).map(function(item) {
      var prompt = String(item.prompt || item.title || '视频任务').trim() || '视频任务';
      var stamp = String(item.updated_at || item.created_at || '').trim();
      return '<div class="viral-tvc-record"><strong>' + esc(prompt) + '</strong><small>' + esc(statusLabel(item.status)) + (stamp ? ' · ' + esc(stamp) : '') + '</small></div>';
    }).join('');
    if (!job && !listHtml) {
      host.className = 'viral-tvc-empty';
      host.innerHTML = '还没有提交任务。<br>左侧填好需求后，点一下就能直接发起爆款 TVC 任务。';
      return;
    }
    var prompt = job ? String(job.prompt || job.title || '爆款 TVC 任务').trim() : '最近任务';
    var jobId = job ? String(job.job_id || job.jobId || state.currentJobId || '').trim() : '';
    var status = job ? String(job.status || 'running').trim() : '';
    var summary = job && job.video_url ? '成片已经生成，可以继续打开完整工作台查看结果和历史记录。' : (job ? '任务已提交到视频工作台，后台会继续处理分镜、生成和成片合成。' : '这里会显示你刚刚提交的任务。');
    host.className = '';
    host.innerHTML = [
      '<div class="viral-tvc-status-hero"><div class="viral-tvc-status-kicker">当前任务</div><h4 class="viral-tvc-status-title">' + esc(prompt || '爆款 TVC 任务') + '</h4><div class="viral-tvc-status-copy">' + esc(summary) + '</div></div>',
      job ? '<div class="viral-tvc-job-box"><div class="viral-tvc-job-head"><div><strong>' + esc(prompt || '爆款 TVC 任务') + '</strong><div class="viral-tvc-job-id">' + esc(jobId || '--') + '</div></div><span class="viral-tvc-pill' + statusClass(status) + '">' + esc(statusLabel(status)) + '</span></div><div class="viral-tvc-meta"><div class="viral-tvc-meta-card"><strong>' + esc(String(job.aspect_ratio || (el('viralTvcRatio') || {}).value || '--')) + '</strong><span>画幅</span></div><div class="viral-tvc-meta-card"><strong>' + esc(String(job.duration || (el('viralTvcDuration') || {}).value || '--')) + ' 秒</strong><span>时长</span></div><div class="viral-tvc-meta-card"><strong>' + esc(modelDisplayName(job.model || (el('viralTvcModel') || {}).value || '--')) + '</strong><span>模型</span></div></div></div>' : '',
      listHtml ? '<div class="viral-tvc-record-list">' + listHtml + '</div>' : ''
    ].join('');
  }
  function normalizeHistoryItem(item) {
    if (!item || typeof item !== 'object') return null;
    return {
      jobId: String(item.job_id || item.id || '').trim(),
      status: String(item.status || '').trim(),
      prompt: String(item.prompt || item.title || item.meta_prompt || '').trim(),
      title: String(item.title || '').trim(),
      model: String(item.model || item.video_model || '').trim(),
      aspect_ratio: String(item.aspect_ratio || '').trim(),
      duration: Number(item.duration || item.total_duration_seconds || 0) || '',
      updated_at: String(item.updated_at || item.finished_at || item.created_at || '').trim(),
      video_url: String(item.video_url || item.result_video_url || '').trim()
    };
  }
  function normalizeLocalHistoryItem(item) {
    if (!item || typeof item !== 'object') return null;
    return {
      jobId: String(item.job_id || item.id || '').trim(),
      status: String(item.status || '').trim(),
      prompt: String(item.prompt || item.title || '').trim(),
      title: String(item.title || item.prompt || '').trim(),
      model: String(item.model || item.video_model || '').trim(),
      aspect_ratio: String(item.aspect_ratio || '').trim(),
      duration: Number(item.duration || item.total_duration_seconds || 0) || '',
      updated_at: String(item.updated_at || item.finished_at || item.created_at || '').trim(),
      video_url: String(item.video_url || item.result_video_url || '').trim()
    };
  }
  function applyHistoryRows(rows) {
    state.lastItems = rows || [];
    var current = state.lastItems.find(function(item) { return item.jobId && item.jobId === state.currentJobId; }) || state.lastItems[0] || null;
    renderStatus(current);
  }
  function loadLocalHistory() {
    return fetch(base() + '/api/comfly-seedance-tvc/pipeline/jobs?limit=8', {
      headers: typeof authHeaders === 'function' ? authHeaders() : {}
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(responseErrorText(data, '本地任务加载失败'));
        var rows = Array.isArray(data.items) ? data.items.map(normalizeLocalHistoryItem).filter(Boolean) : [];
        applyHistoryRows(rows);
        return rows;
      });
    });
  }
  function loadCloudHistory() {
    return fetch(base() + '/api/creative-jobs?feature_type=seedance_tvc&limit=8', {
      headers: typeof authHeaders === 'function' ? authHeaders() : {}
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(responseErrorText(data, '历史任务加载失败'));
        var rows = Array.isArray(data.items) ? data.items.map(normalizeHistoryItem).filter(Boolean) : [];
        applyHistoryRows(rows);
        return rows;
      });
    });
  }
  function loadHistory() {
    return loadLocalHistory().catch(function(localErr) {
      return loadCloudHistory().catch(function(cloudErr) {
        if (!state.currentJobId) renderStatus(null);
        setMsg((localErr && localErr.message) || (cloudErr && cloudErr.message) || '历史任务加载失败', true);
        return [];
      });
    });
  }
  function submitTask() {
    var btn = el('viralTvcGenerateBtn');
    var payload;
    try { payload = buildPayload(); } catch (err) { setMsg(err && err.message ? err.message : '参数不完整', true); return; }
    setBusy(btn, true, '提交中...');
    setMsg('正在提交爆款 TVC 任务...', false);
    fetch(base() + '/api/comfly-seedance-tvc/pipeline/start', {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload)
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || !data || !data.job_id) throw new Error(responseErrorText(data, '任务提交失败'));
        return data;
      });
    }).then(function(data) {
      state.currentJobId = String(data.job_id || '').trim();
      renderStatus({
        job_id: state.currentJobId,
        status: 'running',
        prompt: (el('viralTvcPrompt') ? el('viralTvcPrompt').value : '').trim(),
        aspect_ratio: (el('viralTvcRatio') || {}).value || '9:16',
        duration: Number((el('viralTvcDuration') || {}).value || 60),
        model: (function() {
          var raw = (el('viralTvcModel') || {}).value || '';
          return videoRequestForModel(raw).model || raw;
        })()
      });
      setMsg('任务已提交。右侧会展示当前状态，想继续细调可以进入完整分镜工作台。', false);
      return loadHistory();
    }).catch(function(err) {
      setMsg(err && err.message ? err.message : '任务提交失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }
  function bind() {
    var root = el('content-viral-tvc-studio');
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';
    var back = el('viralTvcBackBtn');
    if (back) back.addEventListener('click', function() { if (typeof window.showAppView === 'function') window.showAppView('chat'); });
    var refresh = el('viralTvcRefreshBtn');
    if (refresh) refresh.addEventListener('click', loadHistory);
    var full = el('viralTvcOpenFullBtn');
    if (full) full.addEventListener('click', function() {
      if (typeof window._openSeedanceTvcStudioView === 'function') window._openSeedanceTvcStudioView();
      else if (typeof window._openHiddenWorkspaceView === 'function') window._openHiddenWorkspaceView('seedance-tvc-studio');
    });
    var start = el('viralTvcGenerateBtn');
    if (start) start.addEventListener('click', submitTask);
  }
  window.initViralTvcStudioView = function() {
    bind();
    renderStatus(null);
    return loadHistory();
  };
})();
