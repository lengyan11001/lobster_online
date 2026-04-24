(function() {
  var state = {
    mode: 'image_auto',
    duration: 20,
    activeBoardIndex: 0,
    images: [],
    currentJobId: '',
    currentJobStatus: '',
    currentResultVideoUrl: '',
    pollTimer: null
  };

  var defaults = {
    aspectRatio: '9:16',
    visualTone: 'clean_bright',
    rhythm: 'smooth',
    model: 'seedance-2-0-pro-250528',
    needAudio: true,
    needMerge: true,
    prompt: ''
  };

  var narrativeSeeds = [
    { title: '开场定调', copy: '先把主体和整体气质立住，让后面的镜头都围绕同一条内容展开。' },
    { title: '主体亮相', copy: '把产品或人物主体推到画面中心，强化识别度和连续性。' },
    { title: '细节推进', copy: '切到关键卖点、动作或质感特写，让用户继续往下看。' },
    { title: '场景展开', copy: '补足使用环境和关系，让画面从展示进入叙事。' },
    { title: '价值确认', copy: '用更明确的镜头语言收束卖点、氛围或转化理由。' },
    { title: '结尾收束', copy: '回到最能代表这条视频的主体镜头，形成完整记忆点。' }
  ];

  var modeMeta = {
    image_auto: {
      name: '参考图自动分析',
      hint: '当前模式会优先使用参考图统一主体和画面风格，再按每 10 秒生成一张分镜图。',
      emphasis: '主体统一'
    },
    image_prompt: {
      name: '图片 + 提示词共创',
      hint: '当前模式会同时参考图片主体和提示词描述，适合想自己控制视频方向时使用。',
      emphasis: '图文共同控制'
    },
    prompt_only: {
      name: '纯提示词规划',
      hint: '当前模式只用提示词规划分镜，右侧先展示草案；真正提交还需要后端支持纯文生视频。',
      emphasis: '脚本主导'
    }
  };

  function $(id) {
    return document.getElementById(id);
  }

  function authHeadersSafe() {
    if (typeof authHeaders === 'function') {
      return authHeaders() || {};
    }
    return {};
  }

  function escapeHtml(text) {
    return String(text || '').replace(/[&<>"]/g, function(ch) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;'
      }[ch];
    });
  }

  function formatFileSize(size) {
    if (!size) return '本地素材';
    if (size >= 1024 * 1024) return (size / (1024 * 1024)).toFixed(1) + ' MB';
    if (size >= 1024) return Math.round(size / 1024) + ' KB';
    return size + ' B';
  }

  function localBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function schedulePoll(delayMs) {
    stopPolling();
    state.pollTimer = setTimeout(function() {
      refreshJobStatus(false);
    }, delayMs || 4000);
  }

  function getFormValues() {
    return {
      aspectRatio: $('seedanceAspectRatioSelect').value,
      visualTone: $('seedanceVisualToneSelect').value,
      rhythm: $('seedanceRhythmSelect').value,
      model: $('seedanceModelSelect').value,
      needAudio: !!$('seedanceNeedAudioCheck').checked,
      needMerge: !!$('seedanceNeedMergeCheck').checked,
      prompt: $('seedanceTaskPromptInput').value.trim()
    };
  }

  function resetFormFields() {
    $('seedanceAspectRatioSelect').value = defaults.aspectRatio;
    $('seedanceVisualToneSelect').value = defaults.visualTone;
    $('seedanceRhythmSelect').value = defaults.rhythm;
    $('seedanceModelSelect').value = defaults.model;
    $('seedanceNeedAudioCheck').checked = defaults.needAudio;
    $('seedanceNeedMergeCheck').checked = defaults.needMerge;
    $('seedanceTaskPromptInput').value = defaults.prompt;
    $('seedanceImageFileInput').value = '';
  }

  function setMode(mode) {
    if (!modeMeta[mode]) mode = 'image_auto';
    state.mode = mode;
    var modeSelect = $('seedanceInputModeSelect');
    if (modeSelect) modeSelect.value = mode;
    if ($('seedanceImageField')) {
      $('seedanceImageField').style.display = mode === 'prompt_only' ? 'none' : '';
    }
    if ($('seedanceInputModeHint')) {
      $('seedanceInputModeHint').textContent = modeMeta[mode].hint;
    }
  }

  function setDuration(duration) {
    state.duration = duration;
    document.querySelectorAll('#seedanceDurationGrid .tvc-duration-chip').forEach(function(chip) {
      chip.classList.toggle('is-active', Number(chip.getAttribute('data-duration')) === duration);
    });
  }

  function releaseMediaItems(items) {
    (items || []).forEach(function(item) {
      if (item && item.url) {
        try {
          URL.revokeObjectURL(item.url);
        } catch (err) {}
      }
    });
  }

  function readFiles(fileList) {
    return Array.prototype.slice.call(fileList || []).map(function(file) {
      return {
        name: file.name,
        size: file.size,
        type: file.type,
        url: URL.createObjectURL(file),
        file: file
      };
    });
  }

  function appendMediaItems(existing, incoming, maxCount) {
    var next = (existing || []).slice();
    (incoming || []).forEach(function(item) {
      if (typeof maxCount === 'number' && next.length >= maxCount) return;
      next.push(item);
    });
    return next;
  }

  function renderUploadList(targetId, items) {
    var el = $(targetId);
    if (!el) return;
    if (!items.length) {
      el.innerHTML = '<div class="tvc-empty-slot" style="grid-column:1 / -1;">还没有参考图片</div>';
      return;
    }

    el.innerHTML = items.map(function(item, index) {
      return [
        '<div class="tvc-upload-card">',
        '<button type="button" class="tvc-upload-remove" data-index="' + index + '" aria-label="remove image" title="移除">X</button>',
        '<img src="' + escapeHtml(item.url) + '" alt="' + escapeHtml(item.name || ('参考图 ' + (index + 1))) + '">',
        '<div class="tvc-upload-card-body">',
        '<div class="tvc-upload-card-title">' + escapeHtml(item.name || ('参考图 ' + (index + 1))) + '</div>',
        '<div class="tvc-upload-card-meta">' + escapeHtml(formatFileSize(item.size)) + '</div>',
        '</div>',
        '</div>'
      ].join('');
    }).join('');
  }

  function removeMediaItem(index) {
    var next = state.images.slice();
    var removed = next.splice(index, 1)[0];
    if (removed) releaseMediaItems([removed]);
    state.images = next;
    state.activeBoardIndex = 0;
    renderWorkspace();
    showMessage('');
  }

  function bindUploadListRemoval() {
    var el = $('seedanceImageList');
    if (!el) return;
    el.addEventListener('click', function(event) {
      var btn = event.target && event.target.closest ? event.target.closest('.tvc-upload-remove') : null;
      if (!btn) return;
      event.preventDefault();
      removeMediaItem(Number(btn.getAttribute('data-index')) || 0);
    });
  }

  function shortenText(text, maxLength) {
    var clean = String(text || '').replace(/\s+/g, ' ').trim();
    if (!clean) return '';
    if (clean.length <= maxLength) return clean;
    return clean.slice(0, maxLength) + '...';
  }

  function buildBoards() {
    var values = getFormValues();
    var count = Math.max(1, Math.floor(state.duration / 10));
    var promptSnippet = shortenText(values.prompt, 42);
    var boards = [];

    for (var i = 0; i < count; i += 1) {
      var seed = narrativeSeeds[i] || narrativeSeeds[narrativeSeeds.length - 1];
      var media = state.images.length ? state.images[i % state.images.length] : null;
      var copy = seed.copy;
      if (promptSnippet) {
        copy += ' 当前提示重点：' + promptSnippet;
      }
      boards.push({
        index: i,
        start: i * 10,
        end: (i + 1) * 10,
        title: seed.title,
        copy: copy,
        media: media
      });
    }

    if (state.activeBoardIndex >= boards.length) {
      state.activeBoardIndex = 0;
    }
    return boards;
  }

  function renderBoards(boards) {
    if ($('seedanceBoardsCounter')) {
      $('seedanceBoardsCounter').textContent = boards.length + ' 张分镜';
    }
    if ($('seedanceBoardsHint')) {
      $('seedanceBoardsHint').textContent = '下面按每 10 秒展示一张分镜图。';
    }
    if (!$('seedanceStoryboardStrip')) return;

    $('seedanceStoryboardStrip').innerHTML = boards.map(function(board) {
      var media = board.media
        ? '<img src="' + escapeHtml(board.media.url) + '" alt="' + escapeHtml(board.title) + '">'
        : '<div style="position:absolute;left:0;right:0;bottom:0;padding:0.85rem;color:#31445f;font-size:0.82rem;font-weight:600;">' + escapeHtml(modeMeta[state.mode].emphasis) + '</div>';

      return [
        '<button type="button" class="tvc-board-card' + (board.index === state.activeBoardIndex ? ' is-active' : '') + '" data-board-index="' + board.index + '">',
        '<div class="tvc-board-media">' + media + '</div>',
        '<div class="tvc-board-body">',
        '<div class="tvc-board-time">' + board.start + 's - ' + board.end + 's</div>',
        '<div class="tvc-board-title">' + escapeHtml(board.title) + '</div>',
        '<div class="tvc-board-copy">' + escapeHtml(board.copy) + '</div>',
        '</div>',
        '</button>'
      ].join('');
    }).join('');

    document.querySelectorAll('#seedanceStoryboardStrip .tvc-board-card').forEach(function(card) {
      card.addEventListener('click', function() {
        state.activeBoardIndex = Number(card.getAttribute('data-board-index')) || 0;
        renderWorkspace();
      });
    });
  }

  function renderVideoStage(values, boards) {
    var videoSurface = $('seedanceVideoSurface');
    if (!videoSurface) return;

    if (state.currentResultVideoUrl) {
      videoSurface.innerHTML = '<video src="' + escapeHtml(state.currentResultVideoUrl) + '" controls playsinline preload="metadata"></video>';
      return;
    }

    if (state.currentJobStatus === 'running') {
      videoSurface.innerHTML = [
        '<div class="tvc-video-placeholder">',
        '<strong>视频任务生成中</strong>',
        '<span>任务已提交，正在生成最终视频，完成后这里会自动切换到成片结果。</span>',
        '</div>'
      ].join('');
      return;
    }

    if (state.currentJobStatus === 'failed') {
      videoSurface.innerHTML = [
        '<div class="tvc-video-placeholder">',
        '<strong>视频生成失败</strong>',
        '<span>请调整素材或参数后重新提交任务。</span>',
        '</div>'
      ].join('');
      return;
    }

    var summary = state.duration + ' 秒 / ' + boards.length + ' 张分镜 / ' + values.aspectRatio;
    var detail = state.mode === 'prompt_only'
      ? '当前是纯提示词规划模式，右侧先展示分镜草案。真正提交成片时，后端还需要补上纯文生视频支持。'
      : '点击“开始生成视频”后，这里会展示最后合成的视频结果。';

    videoSurface.innerHTML = [
      '<div class="tvc-video-placeholder">',
      '<strong>最终结果视频展示区</strong>',
      '<span>' + escapeHtml(summary + '。' + detail) + '</span>',
      '</div>'
    ].join('');
  }

  function renderWorkspace() {
    var values = getFormValues();
    var boards = buildBoards();
    renderUploadList('seedanceImageList', state.images);
    renderBoards(boards);
    renderVideoStage(values, boards);
  }

  function showMessage(text) {
    var el = $('seedanceStudioMsg');
    if (!el) return;
    el.textContent = text;
    el.style.display = text ? 'block' : 'none';
  }

  function uploadAssetItem(item) {
    if (!item || item.asset_id) return Promise.resolve(item);
    if (!item.file) return Promise.reject(new Error('缺少本地文件，无法上传'));
    var base = localBase();
    if (!base) return Promise.reject(new Error('当前未检测到本机 LOCAL_API_BASE'));

    var fd = new FormData();
    fd.append('file', item.file);

    return fetch(base + '/api/assets/upload', {
      method: 'POST',
      headers: authHeadersSafe(),
      body: fd
    })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok || !result.data || !result.data.asset_id) {
          throw new Error((result.data && (result.data.detail || result.data.message)) || '素材上传失败');
        }
        item.asset_id = result.data.asset_id || '';
        item.source_url = result.data.source_url || '';
        return item;
      });
  }

  function ensureImageAssetsUploaded() {
    return state.images.reduce(function(chain, item) {
      return chain.then(function(list) {
        return uploadAssetItem(item).then(function(doneItem) {
          list.push(doneItem);
          return list;
        });
      });
    }, Promise.resolve([]));
  }

  function buildRunPayload(uploadedImages) {
    var values = getFormValues();
    var uploaded = uploadedImages || [];

    if (state.mode === 'prompt_only') {
      return { error: '当前后端还不支持纯提示词直接提交，请先上传参考图后再生成。' };
    }

    if (!uploaded.length || !uploaded[0].asset_id) {
      return { error: '请先上传至少 1 张参考图后再开始生成。' };
    }

    return {
      payload: {
        asset_id: uploaded[0].asset_id,
        reference_asset_ids: uploaded.slice(1).map(function(item) {
          return item.asset_id;
        }).filter(Boolean),
        total_duration_seconds: state.duration,
        segment_count: Math.max(1, Math.floor(state.duration / 10)),
        segment_duration_seconds: 10,
        merge_clips: !!values.needMerge,
        auto_save: true,
        task_text: values.prompt || '',
        analysis_model: typeof ANALYSIS_MODEL !== 'undefined' ? ANALYSIS_MODEL : '',
        image_model: typeof IMAGE_MODEL !== 'undefined' ? IMAGE_MODEL : '',
        video_model: values.model
      }
    };
  }

  function extractResultVideoUrl(resp) {
    if (!resp || typeof resp !== 'object') return '';

    var saved = Array.isArray(resp.saved_assets) ? resp.saved_assets : [];
    for (var i = 0; i < saved.length; i += 1) {
      var asset = saved[i] && saved[i].asset;
      var src = asset && (asset.source_url || asset.preview_url || '');
      if (src) return src;
    }

    var result = resp.result || {};
    var finalVideo = result.final_video || {};
    return String(finalVideo.url || '').trim();
  }

  function refreshJobStatus(showToast) {
    var base = localBase();
    if (!base || !state.currentJobId) return;

    fetch(base + '/api/comfly-seedance-tvc/pipeline/jobs/' + encodeURIComponent(state.currentJobId), {
      headers: authHeadersSafe()
    })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) {
          throw new Error((result.data && (result.data.detail || result.data.message)) || '状态查询失败');
        }

        state.currentJobStatus = String(result.data.status || '').trim();
        state.currentResultVideoUrl = extractResultVideoUrl(result.data) || state.currentResultVideoUrl;
        renderWorkspace();

        if (state.currentJobStatus === 'running') {
          schedulePoll(4000);
          return;
        }

        stopPolling();
        if (state.currentJobStatus === 'completed') {
          showMessage('任务已完成，右侧已切换到最终结果视频。');
        } else if (state.currentJobStatus === 'failed') {
          showMessage('任务失败：' + String(result.data.error || '未知错误'));
        } else if (showToast) {
          showMessage('任务状态已刷新。');
        }
      })
      .catch(function(err) {
        stopPolling();
        showMessage('刷新任务状态失败：' + (err && err.message ? err.message : '未知错误'));
      });
  }

  function startRun() {
    var base = localBase();
    var btn = $('seedanceStartBtn');

    if (!base) {
      showMessage('当前未检测到本机 LOCAL_API_BASE，无法提交 Seedance 视频任务。');
      return;
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = '提交中...';
    }

    showMessage('正在上传参考素材并提交视频任务，请稍候...');

    ensureImageAssetsUploaded()
      .then(function(uploadedImages) {
        var built = buildRunPayload(uploadedImages);
        if (built.error) throw new Error(built.error);

        return fetch(base + '/api/comfly-seedance-tvc/pipeline/start', {
          method: 'POST',
          headers: Object.assign({ 'Content-Type': 'application/json' }, authHeadersSafe()),
          body: JSON.stringify({ payload: built.payload })
        });
      })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok || !result.data || !result.data.job_id) {
          throw new Error((result.data && (result.data.detail || result.data.message)) || '任务提交失败');
        }

        state.currentJobId = result.data.job_id;
        state.currentJobStatus = 'running';
        state.currentResultVideoUrl = '';
        renderWorkspace();
        showMessage('任务已提交，开始自动查询生成结果。');
        refreshJobStatus(false);
      })
      .catch(function(err) {
        showMessage('提交失败：' + (err && err.message ? err.message : '未知错误'));
      })
      .finally(function() {
        if (btn) {
          btn.disabled = false;
          btn.textContent = '开始生成视频';
        }
      });
  }

  function bindEvents() {
    $('seedanceTvcStudioBackBtn').addEventListener('click', function() {
      if (typeof window._ensureSkillStoreVisible === 'function') window._ensureSkillStoreVisible();
      try {
        location.hash = 'skill-store';
      } catch (err) {}
    });

    $('seedanceInputModeSelect').addEventListener('change', function(event) {
      setMode(event.target.value || 'image_auto');
      state.activeBoardIndex = 0;
      renderWorkspace();
      showMessage('');
    });

    document.querySelectorAll('#seedanceDurationGrid .tvc-duration-chip').forEach(function(chip) {
      chip.addEventListener('click', function() {
        setDuration(Number(chip.getAttribute('data-duration')) || 20);
        state.activeBoardIndex = 0;
        renderWorkspace();
      });
    });

    $('seedanceImageUploadBtn').addEventListener('click', function() {
      $('seedanceImageFileInput').click();
    });

    $('seedanceImageFileInput').addEventListener('change', function(event) {
      state.images = appendMediaItems(state.images, readFiles(event.target.files));
      state.activeBoardIndex = 0;
      event.target.value = '';
      renderWorkspace();
      showMessage(state.images.length ? '已载入 ' + state.images.length + ' 张参考图。' : '');
    });

    bindUploadListRemoval();

    [
      'seedanceAspectRatioSelect',
      'seedanceVisualToneSelect',
      'seedanceRhythmSelect',
      'seedanceModelSelect',
      'seedanceNeedAudioCheck',
      'seedanceNeedMergeCheck',
      'seedanceTaskPromptInput'
    ].forEach(function(id) {
      var el = $(id);
      if (!el) return;
      var eventName = (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && el.type === 'text')) ? 'input' : 'change';
      el.addEventListener(eventName, renderWorkspace);
    });

    $('seedancePreviewRefreshBtn').addEventListener('click', function() {
      state.activeBoardIndex = 0;
      renderWorkspace();
      showMessage('已按 ' + state.duration + ' 秒生成 ' + Math.max(1, state.duration / 10) + ' 张分镜预览。');
    });

    $('seedanceStartBtn').addEventListener('click', function() {
      startRun();
    });

    $('seedanceStudioResetBtn').addEventListener('click', function() {
      stopPolling();
      releaseMediaItems(state.images);
      state.images = [];
      state.activeBoardIndex = 0;
      state.currentJobId = '';
      state.currentJobStatus = '';
      state.currentResultVideoUrl = '';
      setMode('image_auto');
      setDuration(20);
      resetFormFields();
      renderWorkspace();
      showMessage('界面已重置，回到默认 20 秒分镜状态。');
    });
  }

  window.initSeedanceTvcStudioView = function() {
    var root = $('content-seedance-tvc-studio');
    if (!root) return;

    if (!root.getAttribute('data-seedance-init')) {
      root.setAttribute('data-seedance-init', '1');
      bindEvents();
      resetFormFields();
      setMode(state.mode);
      setDuration(state.duration);
    }

    renderWorkspace();
  };
})();
