(function() {
  var state = {
    currentJobId: '',
    lastItems: [],
    mainImage: null,
    referenceImages: []
  };

  function isYunwuVeoModel(model) {
    var value = String(model || '').toLowerCase().replace(/\s+/g, '');
    return value === 'yunwu-veo3.1-plus' || value === 'veo3.1-plus' || value === 'veo3.1' || value === 'yingmeng-plus' || value === '影梦plus';
  }

  function isOpenMindGrokModel(model) {
    var value = String(model || '').toLowerCase().replace(/\s+/g, '');
    return value === 'grok-imagine-video-1.5-preview' || value === 'yingmeng1.5plus' || value === '影梦1.5plus';
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

  function base() {
    return (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE ? String(LOCAL_API_BASE) : '').replace(/\/$/, '');
  }

  function headers() {
    return Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : {});
  }

  function uploadHeaders() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    delete h['Content-Type'];
    delete h['content-type'];
    return h;
  }

  function esc(text) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(text || ''));
    return String(text || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function el(id) {
    return document.getElementById(id);
  }

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
    try {
      return JSON.stringify(detail || data);
    } catch (e) {
      return fallback || '请求失败';
    }
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

  function looksLikeVideoUrl(value) {
    var url = String(value || '').trim();
    if (!url) return false;
    var clean = url.split('?')[0].toLowerCase();
    return /\.(mp4|mov|m4v|webm|mkv)$/.test(clean);
  }

  function compactText(text, maxLength) {
    var clean = String(text || '').trim();
    if (!clean) return '';
    if (clean.length <= maxLength) return clean;
    return clean.slice(0, maxLength) + '...';
  }

  function formatFileSize(size) {
    var value = Number(size || 0);
    if (!value) return '';
    if (value < 1024) return value + ' B';
    if (value < 1024 * 1024) return (value / 1024).toFixed(1).replace(/\.0$/, '') + ' KB';
    return (value / (1024 * 1024)).toFixed(1).replace(/\.0$/, '') + ' MB';
  }

  function isRemoteUrl(value) {
    return /^https?:\/\//i.test(String(value || '').trim());
  }

  function parseUrlList(text) {
    var seen = {};
    return String(text || '')
      .split(/[\n,，]+/)
      .map(function(item) { return String(item || '').trim(); })
      .filter(function(item) {
        if (!item || !isRemoteUrl(item) || seen[item]) return false;
        seen[item] = true;
        return true;
      });
  }

  function itemMediaUrl(item) {
    return String((item && (item.preview_url || item.source_url || item.url || item.objectUrl)) || '').trim();
  }

  function releaseItem(item) {
    var objectUrl = String((item && item.objectUrl) || '').trim();
    if (!objectUrl) return;
    try {
      URL.revokeObjectURL(objectUrl);
    } catch (e) {}
  }

  function createLocalImageItem(file) {
    var objectUrl = URL.createObjectURL(file);
    return {
      name: file.name || '图片',
      size: Number(file.size || 0),
      type: file.type || 'image/*',
      file: file,
      objectUrl: objectUrl,
      url: objectUrl,
      source_url: '',
      preview_url: '',
      asset_id: ''
    };
  }

  function renderImageList(targetId, items, emptyText, removeMode) {
    var host = el(targetId);
    if (!host) return;
    if (!items || !items.length) {
      host.innerHTML = '<div class="viral-tvc-upload-empty">' + esc(emptyText) + '</div>';
      return;
    }
    host.innerHTML = items.map(function(item, index) {
      var mediaUrl = itemMediaUrl(item);
      var title = compactText(item.name || ('图片 ' + (index + 1)), 18) || ('图片 ' + (index + 1));
      var meta = formatFileSize(item.size) || (item.asset_id ? ('素材ID ' + item.asset_id) : '');
      var removeAttr = removeMode === 'main'
        ? 'data-remove-main-image="1"'
        : 'data-remove-ref-image="' + index + '"';
      return ''
        + '<div class="viral-tvc-upload-card">'
        +   '<button type="button" class="viral-tvc-upload-remove" ' + removeAttr + ' title="移除">×</button>'
        +   '<img src="' + esc(mediaUrl) + '" alt="' + esc(title) + '">'
        +   '<div class="viral-tvc-upload-card-body">'
        +     '<div class="viral-tvc-upload-card-title">' + esc(title) + '</div>'
        +     '<div class="viral-tvc-upload-card-meta">' + esc(meta || '已选择') + '</div>'
        +   '</div>'
        + '</div>';
    }).join('');
  }

  function renderReferenceUrlSummary() {
    var host = el('viralTvcReferenceUrlSummary');
    if (!host) return;
    var items = parseUrlList((el('viralTvcReferenceUrls') || {}).value || '');
    if (!items.length) {
      host.textContent = '还没有填写公网参考图链接。';
      return;
    }
    host.textContent = '已填写 ' + items.length + ' 条公网参考图链接，提交时会一起带入参考图参数。';
  }

  function renderMediaState() {
    renderImageList('viralTvcMainImageList', state.mainImage ? [state.mainImage] : [], '未选择主产品图', 'main');
    renderImageList('viralTvcRefImageList', state.referenceImages || [], '未添加补充参考图', 'refs');
    renderReferenceUrlSummary();
  }

  function uploadAssetItem(item, label) {
    if (!item) return Promise.resolve(null);
    if (item.asset_id) return Promise.resolve(item);
    if (!item.file) return Promise.resolve(item);
    var apiBase = base();
    if (!apiBase) return Promise.reject(new Error('当前未检测到可用的本地后端地址'));
    var fd = new FormData();
    fd.append('file', item.file);
    setMsg('正在上传' + (label || '图片') + '...', false);
    return fetch(apiBase + '/api/assets/upload', {
      method: 'POST',
      headers: uploadHeaders(),
      body: fd
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || !data || !data.asset_id) {
          throw new Error(responseErrorText(data, (label || '图片') + '上传失败'));
        }
        item.asset_id = String(data.asset_id || '').trim();
        item.source_url = String(data.source_url || item.source_url || '').trim();
        item.preview_url = String(data.preview_url || data.local_preview_url || item.preview_url || '').trim();
        item.url = itemMediaUrl(item) || item.url || '';
        return item;
      });
    });
  }

  function uploadReferenceImages() {
    return (state.referenceImages || []).reduce(function(chain, item, index) {
      return chain.then(function(list) {
        return uploadAssetItem(item, '参考图 ' + (index + 1)).then(function(doneItem) {
          if (doneItem) list.push(doneItem);
          return list;
        });
      });
    }, Promise.resolve([]));
  }

  function buildPayloadWithAssets(mainImage, referenceImages) {
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
    var manualMainUrl = String((el('viralTvcMainImageUrl') || {}).value || '').trim();
    var manualRefUrls = parseUrlList((el('viralTvcReferenceUrls') || {}).value || '');

    var payload = {
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
        image_model_fallback: 'nano-banana-2',
        video_model: videoRequest.model,
        video_channel: videoRequest.channel,
        video_fallbacks: isYunwuVeoModel(model) ? [{ channel: 'comfly', model: 'veo3.1-fast' }] : [],
        generate_audio: needAudio,
        watermark: false,
        input_mode: 'prompt_only'
      }
    };

    var uploadedRefs = (referenceImages || []).filter(function(item) {
      return !!String((item && item.asset_id) || '').trim();
    });

    if (mainImage && String(mainImage.asset_id || '').trim()) {
      payload.payload.asset_id = String(mainImage.asset_id).trim();
    } else if (isRemoteUrl(manualMainUrl)) {
      payload.payload.image_url = manualMainUrl;
    }

    if (!payload.payload.asset_id && !payload.payload.image_url && uploadedRefs.length) {
      payload.payload.asset_id = String(uploadedRefs[0].asset_id || '').trim();
      uploadedRefs = uploadedRefs.slice(1);
    }

    if (!payload.payload.asset_id && !payload.payload.image_url && manualRefUrls.length) {
      payload.payload.image_url = manualRefUrls[0];
      manualRefUrls = manualRefUrls.slice(1);
    }

    var referenceAssetIds = uploadedRefs.map(function(item) {
      return String(item.asset_id || '').trim();
    }).filter(Boolean);

    if (referenceAssetIds.length) {
      payload.payload.reference_asset_ids = referenceAssetIds;
    }
    if (manualRefUrls.length) {
      payload.payload.reference_image_urls = manualRefUrls;
    }

    if (payload.payload.asset_id || payload.payload.image_url || referenceAssetIds.length || manualRefUrls.length) {
      payload.payload.input_mode = prompt ? 'image_prompt' : 'image_auto';
    }

    return payload;
  }

  function resolveSubmissionPayload() {
    return uploadAssetItem(state.mainImage, '主产品图').then(function(mainImage) {
      return uploadReferenceImages().then(function(referenceImages) {
        return buildPayloadWithAssets(mainImage, referenceImages);
      });
    });
  }

  function extractResultVideoUrl(payload) {
    if (!payload || typeof payload !== 'object') return '';

    var result = payload.result && typeof payload.result === 'object' ? payload.result : {};
    var finalVideo = result.final_video && typeof result.final_video === 'object' ? result.final_video : {};
    var direct = String(finalVideo.url || finalVideo.preview_url || finalVideo.local_preview_url || '').trim();
    if (direct) return direct;

    var saved = Array.isArray(payload.saved_assets) ? payload.saved_assets : [];

    function pickSavedUrl(item) {
      if (!item || typeof item !== 'object') return '';
      var asset = item.asset && typeof item.asset === 'object' ? item.asset : {};
      var meta = asset.meta && typeof asset.meta === 'object' ? asset.meta : {};
      var directUrl = String(
        item.video_url
        || item.url
        || item.output
        || asset.source_url
        || asset.preview_url
        || asset.open_url
        || ''
      ).trim();
      if (directUrl && (String(item.kind || '').toLowerCase().indexOf('final') >= 0
        || meta.seedance_final_video
        || String(meta.origin || '').toLowerCase().indexOf('merged') >= 0
        || looksLikeVideoUrl(directUrl))) {
        return directUrl;
      }
      return '';
    }

    var i;
    for (i = 0; i < saved.length; i += 1) {
      direct = pickSavedUrl(saved[i]);
      if (direct) return direct;
    }

    var groups = [result.completed_segments, result.completed_shots, result.shots];
    for (var g = 0; g < groups.length; g += 1) {
      var list = Array.isArray(groups[g]) ? groups[g] : [];
      for (i = 0; i < list.length; i += 1) {
        var row = list[i] || {};
        direct = String(row.mp4url || row.video_url || row.url || row.output || '').trim();
        if (direct) return direct;
        var raw = row.video_raw && typeof row.video_raw === 'object' ? row.video_raw : (row.raw && typeof row.raw === 'object' ? row.raw : {});
        var content = raw.content && typeof raw.content === 'object' ? raw.content : {};
        direct = String(content.video_url || content.url || '').trim();
        if (direct) return direct;
        var data = raw.data && typeof raw.data === 'object' ? raw.data : {};
        direct = String(data.video_url || data.output || '').trim();
        if (direct) return direct;
        var nested = raw.result && typeof raw.result === 'object' ? raw.result : {};
        direct = String(nested.video_url || nested.output || '').trim();
        if (direct) return direct;
      }
    }

    return '';
  }

  function buildVideoResultCard(job) {
    var videoUrl = String((job && job.video_url) || '').trim();
    if (!videoUrl) {
      return ''
        + '<div class="viral-tvc-result-card">'
        +   '<div class="viral-tvc-result-head"><div class="viral-tvc-result-title">任务视频结果</div></div>'
        +   '<div class="viral-tvc-video-stage">'
        +     '<div class="viral-tvc-video-placeholder">'
        +       '<strong>当前还没有可播放的成片</strong>'
        +       '<span>如果素材库里已经有成片，通常是这个快捷页还没从 final_video 或 saved_assets 取到结果；刷新后会重新读取。</span>'
        +     '</div>'
        +   '</div>'
        + '</div>';
    }
    return ''
      + '<div class="viral-tvc-result-card">'
      +   '<div class="viral-tvc-result-head">'
      +     '<div class="viral-tvc-result-title">任务视频结果</div>'
      +     '<div class="viral-tvc-result-actions">'
      +       '<button type="button" class="btn btn-ghost btn-sm" data-viral-tvc-open-video="' + esc(videoUrl) + '">新窗口打开</button>'
      +       '<button type="button" class="btn btn-primary btn-sm" data-viral-tvc-download-video="' + esc(videoUrl) + '">下载视频</button>'
      +     '</div>'
      +   '</div>'
      +   '<div class="viral-tvc-video-stage">'
      +     '<video controls playsinline preload="metadata" src="' + esc(videoUrl) + '"></video>'
      +   '</div>'
      + '</div>';
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
    var currentJob = job || null;
    var prompt = currentJob ? String(currentJob.prompt || currentJob.title || '爆款 TVC 任务').trim() : '最近任务';
    var jobId = currentJob ? String(currentJob.job_id || currentJob.jobId || currentJob.jobId || state.currentJobId || '').trim() : '';
    var status = currentJob ? String(currentJob.status || 'running').trim() : '';
    var summary = currentJob && currentJob.video_url
      ? '成片已经生成，可以继续打开完整工作台查看结果和历史记录。'
      : (currentJob ? '任务已提交到视频工作台，后台会继续处理分镜、生成和成片合成。' : '这里会显示你刚刚提交的任务。');
    host.className = '';
    host.innerHTML = [
      '<div class="viral-tvc-result-wrap">',
      '<div class="viral-tvc-status-hero"><div class="viral-tvc-status-kicker">当前任务</div><h4 class="viral-tvc-status-title">' + esc(prompt || '爆款 TVC 任务') + '</h4><div class="viral-tvc-status-copy">' + esc(summary) + '</div></div>',
      currentJob ? '<div class="viral-tvc-job-box"><div class="viral-tvc-job-head"><div><strong>' + esc(prompt || '爆款 TVC 任务') + '</strong><div class="viral-tvc-job-id">' + esc(jobId || '--') + '</div></div><span class="viral-tvc-pill' + statusClass(status) + '">' + esc(statusLabel(status)) + '</span></div><div class="viral-tvc-meta"><div class="viral-tvc-meta-card"><strong>' + esc(String(currentJob.aspect_ratio || (el('viralTvcRatio') || {}).value || '--')) + '</strong><span>画幅</span></div><div class="viral-tvc-meta-card"><strong>' + esc(String(currentJob.duration || (el('viralTvcDuration') || {}).value || '--')) + ' 秒</strong><span>时长</span></div><div class="viral-tvc-meta-card"><strong>' + esc(modelDisplayName(currentJob.model || (el('viralTvcModel') || {}).value || '--')) + '</strong><span>模型</span></div></div></div>' : '',
      buildVideoResultCard(currentJob),
      listHtml ? '<div class="viral-tvc-record-list">' + listHtml + '</div>' : '',
      '</div>'
    ].join('');

    Array.prototype.forEach.call(host.querySelectorAll('[data-viral-tvc-open-video]'), function(btn) {
      btn.onclick = function() {
        var url = btn.getAttribute('data-viral-tvc-open-video') || '';
        if (!url) return;
        try {
          window.open(url, '_blank', 'noopener');
        } catch (e) {}
      };
    });
    Array.prototype.forEach.call(host.querySelectorAll('[data-viral-tvc-download-video]'), function(btn) {
      btn.onclick = function() {
        var url = btn.getAttribute('data-viral-tvc-download-video') || '';
        if (!url) return;
        try {
          var link = document.createElement('a');
          link.href = url;
          link.target = '_blank';
          link.rel = 'noopener';
          link.download = '';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
        } catch (e) {
          try { window.open(url, '_blank', 'noopener'); } catch (_e) {}
        }
      };
    });
  }

  function normalizeHistoryItem(item) {
    if (!item || typeof item !== 'object') return null;
    var result = item.result && typeof item.result === 'object' ? item.result : (item.result_payload && typeof item.result_payload === 'object' ? item.result_payload : {});
    var savedAssets = Array.isArray(item.saved_assets) ? item.saved_assets : [];
    return {
      jobId: String(item.job_id || item.id || '').trim(),
      status: String(item.status || '').trim(),
      prompt: String(item.prompt || item.title || item.meta_prompt || '').trim(),
      title: String(item.title || '').trim(),
      model: String(item.model || item.video_model || '').trim(),
      aspect_ratio: String(item.aspect_ratio || '').trim(),
      duration: Number(item.duration || item.total_duration_seconds || 0) || '',
      updated_at: String(item.updated_at || item.finished_at || item.created_at || '').trim(),
      result: result,
      saved_assets: savedAssets,
      video_url: String(item.video_url || item.result_video_url || extractResultVideoUrl({ result: result, saved_assets: savedAssets })).trim()
    };
  }

  function normalizeLocalHistoryItem(item) {
    if (!item || typeof item !== 'object') return null;
    var result = item.result && typeof item.result === 'object' ? item.result : {};
    var savedAssets = Array.isArray(item.saved_assets) ? item.saved_assets : [];
    return {
      jobId: String(item.job_id || item.id || '').trim(),
      status: String(item.status || '').trim(),
      prompt: String(item.prompt || item.title || '').trim(),
      title: String(item.title || item.prompt || '').trim(),
      model: String(item.model || item.video_model || '').trim(),
      aspect_ratio: String(item.aspect_ratio || '').trim(),
      duration: Number(item.duration || item.total_duration_seconds || 0) || '',
      updated_at: String(item.updated_at || item.finished_at || item.created_at || '').trim(),
      result: result,
      saved_assets: savedAssets,
      video_url: String(item.video_url || item.result_video_url || extractResultVideoUrl({ result: result, saved_assets: savedAssets })).trim()
    };
  }

  function applyHistoryRows(rows) {
    state.lastItems = rows || [];
    var current = state.lastItems.find(function(item) {
      return item.jobId && item.jobId === state.currentJobId;
    }) || state.lastItems[0] || null;
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
    setBusy(btn, true, '提交中...');
    setMsg('正在整理素材并提交爆款 TVC 任务...', false);
    resolveSubmissionPayload().then(function(payload) {
      return fetch(base() + '/api/comfly-seedance-tvc/pipeline/start', {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(payload)
      }).then(function(resp) {
        return resp.json().catch(function() { return {}; }).then(function(data) {
          if (!resp.ok || !data || !data.job_id) throw new Error(responseErrorText(data, '任务提交失败'));
          return data;
        });
      });
    }).then(function(data) {
      state.currentJobId = String(data.job_id || '').trim();
      renderStatus({
        job_id: state.currentJobId,
        status: 'running',
        prompt: (el('viralTvcPrompt') ? el('viralTvcPrompt').value : '').trim(),
        aspect_ratio: (el('viralTvcRatio') || {}).value || '9:16',
        duration: Number((el('viralTvcDuration') || {}).value || 60),
        video_url: '',
        model: (function() {
          var raw = (el('viralTvcModel') || {}).value || '';
          return videoRequestForModel(raw).model || raw;
        })()
      });
      setMsg('任务已提交。右侧会显示当前状态；想继续细调可以进入完整分镜工作台。', false);
      return loadHistory();
    }).catch(function(err) {
      setMsg(err && err.message ? err.message : '任务提交失败', true);
    }).finally(function() {
      setBusy(btn, false);
      renderMediaState();
    });
  }

  function bindMainImageEvents() {
    var pickBtn = el('viralTvcMainImagePickBtn');
    var input = el('viralTvcMainImageInput');
    var list = el('viralTvcMainImageList');
    if (pickBtn && input) {
      pickBtn.addEventListener('click', function() {
        input.click();
      });
      input.addEventListener('change', function(event) {
        var file = event.target && event.target.files && event.target.files[0];
        if (!file) return;
        if (state.mainImage) releaseItem(state.mainImage);
        state.mainImage = createLocalImageItem(file);
        event.target.value = '';
        renderMediaState();
      });
    }
    if (list) {
      list.addEventListener('click', function(event) {
        var btn = event.target && event.target.closest ? event.target.closest('[data-remove-main-image]') : null;
        if (!btn || !state.mainImage) return;
        releaseItem(state.mainImage);
        state.mainImage = null;
        renderMediaState();
      });
    }
  }

  function bindReferenceImageEvents() {
    var pickBtn = el('viralTvcRefImagePickBtn');
    var input = el('viralTvcRefImageInput');
    var list = el('viralTvcRefImageList');
    if (pickBtn && input) {
      pickBtn.addEventListener('click', function() {
        input.click();
      });
      input.addEventListener('change', function(event) {
        var files = Array.prototype.slice.call((event.target && event.target.files) || []);
        files.forEach(function(file) {
          state.referenceImages.push(createLocalImageItem(file));
        });
        event.target.value = '';
        renderMediaState();
      });
    }
    if (list) {
      list.addEventListener('click', function(event) {
        var btn = event.target && event.target.closest ? event.target.closest('[data-remove-ref-image]') : null;
        if (!btn) return;
        var index = Number(btn.getAttribute('data-remove-ref-image') || -1);
        if (index < 0 || !state.referenceImages[index]) return;
        var removed = state.referenceImages.splice(index, 1)[0];
        releaseItem(removed);
        renderMediaState();
      });
    }
    var urlField = el('viralTvcReferenceUrls');
    if (urlField) {
      urlField.addEventListener('input', renderReferenceUrlSummary);
      urlField.addEventListener('change', renderReferenceUrlSummary);
    }
  }

  function bind() {
    var root = el('content-viral-tvc-studio');
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';
    var back = el('viralTvcBackBtn');
    if (back) {
      back.addEventListener('click', function() {
        if (typeof window.showAppView === 'function') window.showAppView('chat');
      });
    }
    var refresh = el('viralTvcRefreshBtn');
    if (refresh) refresh.addEventListener('click', loadHistory);
    var full = el('viralTvcOpenFullBtn');
    if (full) {
      full.addEventListener('click', function() {
        if (typeof window._openSeedanceTvcStudioView === 'function') window._openSeedanceTvcStudioView();
        else if (typeof window._openHiddenWorkspaceView === 'function') window._openHiddenWorkspaceView('seedance-tvc-studio');
      });
    }
    var start = el('viralTvcGenerateBtn');
    if (start) start.addEventListener('click', submitTask);
    bindMainImageEvents();
    bindReferenceImageEvents();
  }

  window.initViralTvcStudioView = function() {
    bind();
    renderMediaState();
    renderStatus(null);
    return loadHistory();
  };
})();
