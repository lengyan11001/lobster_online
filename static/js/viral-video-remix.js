(function() {
  var state = {
    originalVideo: null,
    personImage: null,
    productImage: null,
    characterUrl: '',
    productUrl: '',
    cleanedProductUrl: '',
    originalVideoUrl: '',
    taskId: '',
    pollTimer: null,
    submitting: false
  };
  var characterRenderSeq = 0;

  function $(id) { return document.getElementById(id); }

  function baseUrl() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  }

  function authHeadersSafe() {
    if (typeof authHeaders === 'function') {
      var headers = Object.assign({}, authHeaders() || {});
      delete headers['Content-Type'];
      delete headers['content-type'];
      return headers;
    }
    return {};
  }

  function escapeHtml(text) {
    return String(text || '').replace(/[&<>"]/g, function(ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch];
    });
  }

  function showMessage(text, isError) {
    var el = $('viralRemixMsg');
    if (!el) return;
    el.textContent = text || '';
    el.style.display = text ? 'block' : 'none';
    el.classList.toggle('err', !!isError);
  }

  function showCharacterStatus(text, isError) {
    var el = $('viralCharacterStatus');
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('err', !!isError);
    el.classList.toggle('ok', !!text && !isError);
  }

  function normalizeApiError(detail, fallback) {
    if (!detail) return fallback || '请求失败';
    if (typeof detail === 'string') return detail;
    if (detail.detail) return normalizeApiError(detail.detail, fallback);
    if (detail.message) return detail.message;
    if (detail.error) return normalizeApiError(detail.error, fallback);
    try { return JSON.stringify(detail); } catch (e) { return String(detail); }
  }

  function videoStatusMeta(status) {
    var value = String(status || '').toLowerCase();
    var map = {
      queued: ['等待开始', '任务已提交，正在等待 Seedance 分配生成资源。'],
      pending: ['等待开始', '任务已提交，正在等待 Seedance 分配生成资源。'],
      submitted: ['等待开始', '任务已提交，正在等待 Seedance 分配生成资源。'],
      created: ['等待开始', '任务已提交，正在等待 Seedance 分配生成资源。'],
      running: ['正在生成视频', 'Seedance 正在按原视频的动作、运镜和节奏生成复刻结果。'],
      processing: ['正在生成视频', 'Seedance 正在按原视频的动作、运镜和节奏生成复刻结果。'],
      in_progress: ['正在生成视频', 'Seedance 正在按原视频的动作、运镜和节奏生成复刻结果。'],
      success: ['生成完成', '视频已经生成完成。'],
      succeeded: ['生成完成', '视频已经生成完成。'],
      done: ['生成完成', '视频已经生成完成。'],
      completed: ['生成完成', '视频已经生成完成。'],
      failed: ['生成失败', '任务没有完成，请调整素材或补充要求后重试。'],
      failure: ['生成失败', '任务没有完成，请调整素材或补充要求后重试。'],
      error: ['生成失败', '任务没有完成，请调整素材或补充要求后重试。'],
      cancelled: ['已取消', '任务已取消。'],
      canceled: ['已取消', '任务已取消。'],
      expired: ['任务过期', '任务已过期，请重新提交。']
    };
    return map[value] || ['正在生成视频', '正在查询生成进度，请稍等。'];
  }

  function setBusy(flag) {
    state.submitting = !!flag;
    ['viralCharacterGenerateBtn', 'viralRemixStartBtn', 'viralUploadVideoBtn', 'viralUploadProductBtn', 'viralCleanProductBtn'].forEach(function(id) {
      var btn = $(id);
      if (btn) btn.disabled = !!flag;
    });
  }

  function setCharacterLoading(flag) {
    var btn = $('viralCharacterGenerateBtn');
    if (!btn) return;
    btn.classList.toggle('is-loading', !!flag);
    btn.setAttribute('aria-busy', flag ? 'true' : 'false');
    var label = btn.querySelector('.viral-btn-label');
    if (label) label.textContent = flag ? '生成中...' : '生成人物四视图';
  }

  function setFilePreview(targetId, file, kind) {
    var el = $(targetId);
    if (!el) return;
    if (!file) {
      el.innerHTML = '<div class="tvc-upload-empty">未选择素材</div>';
      return;
    }
    var url = URL.createObjectURL(file);
    var media = kind === 'video'
      ? '<video src="' + escapeHtml(url) + '" muted playsinline preload="metadata"></video>'
      : '<img src="' + escapeHtml(url) + '" alt="">';
    el.innerHTML = '<div class="tvc-upload-card">' + media + '<span>' + escapeHtml(file.name) + '</span></div>';
  }

  function setUrlPreview(targetId, url, label) {
    var el = $(targetId);
    if (!el) return;
    if (!url) {
      el.innerHTML = '<div class="tvc-upload-empty">未选择素材</div>';
      return;
    }
    el.innerHTML = '<div class="tvc-upload-card"><img src="' + escapeHtml(url) + '" alt=""><span>' + escapeHtml(label || '产品参考图') + '</span></div>';
  }

  function uploadAsset(file) {
    var form = new FormData();
    form.append('file', file);
    return fetch(baseUrl() + '/api/viral-video-remix/assets/upload', {
      method: 'POST',
      headers: authHeadersSafe(),
      body: form
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(normalizeApiError(data, '素材上传失败'));
        var url = data.source_url || '';
        if (!url) throw new Error('素材上传成功，但没有得到公网 URL');
        return data;
      });
    });
  }

  function isRemoteUrl(url) {
    return /^https?:\/\//i.test(String(url || '').trim());
  }

  function ensureRemoteUrl(url, label, allowDataImage) {
    var value = String(url || '').trim();
    if (!value) return '';
    if (isRemoteUrl(value)) return value;
    if (/^data:image\//i.test(value) && allowDataImage) return value;
    if (/^data:/i.test(value)) {
      throw new Error(label + '当前只有本地预览数据，视频文件必须先上传到 Comfly /v1/files，图片也建议使用上传后的 URL。');
    }
    throw new Error(label + '必须是 http/https URL。');
  }

  function renderCharacterResult(url, done) {
    var surface = $('viralCharacterResult');
    if (!surface) return;
    if (!url) {
      surface.innerHTML = '<div class="tvc-video-placeholder"><strong>人物四视图</strong><span>上传人物图后生成彩铅/插画风格四视图。</span></div>';
      return;
    }
    var seq = ++characterRenderSeq;
    surface.innerHTML = '<div class="tvc-video-placeholder"><strong>图片加载中</strong><span>人物四视图已生成，正在加载高清结果。</span></div>';
    var img = new Image();
    img.decoding = 'async';
    img.onload = function() {
      if (seq !== characterRenderSeq) return;
      surface.innerHTML = '';
      img.alt = '人物四视图';
      img.style.width = '100%';
      img.style.height = '100%';
      img.style.objectFit = 'contain';
      img.style.background = '#f7f2eb';
      surface.appendChild(img);
      if (typeof done === 'function') done(true);
    };
    img.onerror = function() {
      if (seq !== characterRenderSeq) return;
      surface.innerHTML = '<div class="tvc-video-placeholder"><strong>图片加载失败</strong><span>结果 URL 已返回，但浏览器无法加载预览图，可检查人物图 URL 是否能直接访问。</span></div>';
      if (typeof done === 'function') done(false);
    };
    img.src = url;
  }

  function renderVideoResult(url, status, busy) {
    var surface = $('viralRemixVideoSurface');
    if (!surface) return;
    if (url) {
      surface.innerHTML = '<video src="' + escapeHtml(url) + '" controls playsinline preload="metadata"></video>';
      return;
    }
    if (busy) {
      var meta = videoStatusMeta(status);
      surface.innerHTML = [
        '<div class="viral-video-placeholder is-busy">',
          '<div class="viral-status-head">',
            '<span class="viral-status-spinner" aria-hidden="true"></span>',
            '<div class="viral-status-copy">',
              '<strong>' + escapeHtml(meta[0]) + '</strong>',
              '<span>' + escapeHtml(meta[1]) + '</span>',
            '</div>',
          '</div>',
          '<div class="viral-status-note">生成期间可以停留在当前页面，完成后视频会自动显示在这里。</div>',
        '</div>'
      ].join('');
      return;
    }
    var idle = status ? videoStatusMeta(status) : ['等待提交', '上传原视频、人物四视图和产品图后，点击开始复刻视频。'];
    surface.innerHTML = '<div class="viral-video-placeholder"><div class="viral-status-copy"><strong>' + escapeHtml(idle[0]) + '</strong><span>' + escapeHtml(idle[1]) + '</span></div></div>';
  }

  function generateCharacterReference() {
    if (state.submitting) return;
    var form = new FormData();
    form.append('prompt', (($('viralCharacterPrompt') || {}).value || '').trim());
    form.append('style', (($('viralCharacterStyle') || {}).value || 'colored_pencil'));
    if (state.personImage) form.append('image', state.personImage);
    setBusy(true);
    setCharacterLoading(true);
    showCharacterStatus('正在生成人物四视图...', false);
    showMessage('', false);
    fetch(baseUrl() + '/api/viral-video-remix/character-reference', {
      method: 'POST',
      headers: authHeadersSafe(),
      body: form
    })
      .then(function(resp) {
        return resp.json().catch(function() { return {}; }).then(function(data) {
          if (!resp.ok) throw new Error(normalizeApiError(data, '人物四视图生成失败'));
          var item = (data.images || [])[0] || {};
          var url = item.url || item.data_url || '';
          if (!url) throw new Error('人物四视图没有返回可用图片');
          state.characterUrl = url;
          if ($('viralCharacterUrlInput')) $('viralCharacterUrlInput').value = url;
          showCharacterStatus('已生成，正在加载预览图...', false);
          renderCharacterResult(url, function(ok) {
            showCharacterStatus(ok ? '已生成，可继续上传产品图和原视频。' : '已生成，但预览图加载失败。', !ok);
          });
        });
      })
      .catch(function(err) {
        showCharacterStatus(err && err.message ? err.message : '人物四视图生成失败', true);
      })
      .finally(function() {
        setCharacterLoading(false);
        setBusy(false);
      });
  }

  function uploadOriginalVideo() {
    if (!state.originalVideo) {
      showMessage('请先选择原爆款视频文件，或直接填写视频 URL。', true);
      return Promise.resolve('');
    }
    showMessage('正在上传原爆款视频...', false);
    return uploadAsset(state.originalVideo).then(function(data) {
      state.originalVideoUrl = data.source_url || '';
      if ($('viralOriginalVideoUrlInput')) $('viralOriginalVideoUrlInput').value = state.originalVideoUrl;
      showMessage('原视频已上传。', false);
      return state.originalVideoUrl;
    });
  }

  function uploadProductImage() {
    if (!state.productImage) {
      showMessage('请先选择产品图片，或直接填写产品图 URL。', true);
      return Promise.resolve('');
    }
    showMessage('正在上传产品图片...', false);
    return uploadAsset(state.productImage).then(function(data) {
      state.productUrl = data.source_url || '';
      state.cleanedProductUrl = '';
      if ($('viralProductUrlInput')) $('viralProductUrlInput').value = state.productUrl;
      showMessage('产品图片已上传。', false);
      return state.productUrl;
    });
  }

  function cleanProductReference(sourceUrl) {
    var productUrl = String(sourceUrl || (($('viralProductUrlInput') || {}).value || state.productUrl || '')).trim();
    if (!state.productImage && !productUrl) {
      showMessage('请先选择产品图，或填写产品图 URL。', true);
      return Promise.resolve('');
    }
    var form = new FormData();
    form.append('prompt', (($('viralProductCleanPrompt') || {}).value || '').trim());
    if (state.productImage) form.append('image', state.productImage);
    else form.append('image_url', productUrl);
    showMessage('正在生成白底产品参考图...', false);
    var status = $('viralProductCleanStatus');
    if (status) status.textContent = '正在生成白底图...';
    return fetch(baseUrl() + '/api/viral-video-remix/product-reference', {
      method: 'POST',
      headers: authHeadersSafe(),
      body: form
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(normalizeApiError(data, '白底产品图生成失败'));
        var item = (data.images || [])[0] || {};
        var url = item.url || item.data_url || '';
        if (!url) throw new Error('白底产品图没有返回可用图片');
        state.cleanedProductUrl = url;
        state.productUrl = url;
        if ($('viralProductUrlInput')) $('viralProductUrlInput').value = url;
        setUrlPreview('viralProductPreview', url, '白底产品参考图');
        if (status) status.textContent = '白底图已生成';
        showMessage('白底产品参考图已生成，将用它替换视频里的旧产品。', false);
        return url;
      });
    }).catch(function(err) {
      if (status) status.textContent = '白底图生成失败';
      throw err;
    });
  }

  function startRemix() {
    if (state.submitting) return;
    var originalVideoUrl = (($('viralOriginalVideoUrlInput') || {}).value || state.originalVideoUrl || '').trim();
    var characterUrl = (($('viralCharacterUrlInput') || {}).value || state.characterUrl || '').trim();
    var productUrl = (($('viralProductUrlInput') || {}).value || state.productUrl || '').trim();
    setBusy(true);
    Promise.resolve()
      .then(function() {
        if (!originalVideoUrl && state.originalVideo) return uploadOriginalVideo();
        return originalVideoUrl;
      })
      .then(function(url) {
        originalVideoUrl = url || originalVideoUrl;
        if (!productUrl && state.productImage) return uploadProductImage();
        return productUrl;
      })
      .then(function(url) {
        productUrl = url || productUrl;
        if (($('viralCleanProductCheck') || {}).checked) {
          if (state.cleanedProductUrl) return state.cleanedProductUrl;
          return cleanProductReference(productUrl);
        }
        return productUrl;
      })
      .then(function(url) {
        productUrl = url || productUrl;
        if (!originalVideoUrl || !productUrl) {
          throw new Error('缺少原视频或产品图 URL。请先上传/生成素材。');
        }
        var body = {
          original_video_url: ensureRemoteUrl(originalVideoUrl, '原视频', false),
          character_image_url: characterUrl ? ensureRemoteUrl(characterUrl, '人物四视图', true) : '',
          product_image_url: ensureRemoteUrl(productUrl, '产品图', true),
          prompt: (($('viralRemixPrompt') || {}).value || '').trim(),
          model: (($('viralRemixModelSelect') || {}).value || 'doubao-seedance-2-0-260128'),
          ratio: (($('viralRemixRatioSelect') || {}).value || '9:16'),
          resolution: (($('viralRemixResolutionSelect') || {}).value || '720p'),
          duration: Number((($('viralRemixDurationSelect') || {}).value || 5)),
          generate_audio: !!(($('viralRemixAudioCheck') || {}).checked),
          use_character_reference: !!(($('viralUseCharacterCheck') || {}).checked && characterUrl),
          watermark: false
        };
        showMessage('正在提交 Seedance 产品替换任务...', false);
        return fetch(baseUrl() + '/api/viral-video-remix/seedance/start', {
          method: 'POST',
          headers: Object.assign({ 'Content-Type': 'application/json' }, authHeadersSafe()),
          body: JSON.stringify(body)
        });
      })
      .then(function(resp) {
        return resp.json().catch(function() { return {}; }).then(function(data) {
          if (!resp.ok) throw new Error(normalizeApiError(data, 'Seedance 任务提交失败'));
          state.taskId = data.task_id || '';
          if (!state.taskId) throw new Error('Seedance 没有返回任务 ID');
          showMessage('任务已提交，正在生成视频。', false);
          renderVideoResult('', 'running', true);
          pollTask(true);
        });
      })
      .catch(function(err) {
        showMessage(err && err.message ? err.message : '提交失败', true);
      })
      .finally(function() { setBusy(false); });
  }

  function pollTask(immediate) {
    if (!state.taskId) return;
    if (state.pollTimer) clearTimeout(state.pollTimer);
    var wait = immediate ? 0 : 8000;
    state.pollTimer = setTimeout(function() {
      fetch(baseUrl() + '/api/viral-video-remix/seedance/tasks/' + encodeURIComponent(state.taskId), {
        headers: authHeadersSafe()
      })
        .then(function(resp) {
          return resp.json().catch(function() { return {}; }).then(function(data) {
            if (!resp.ok) throw new Error(normalizeApiError(data, '查询失败'));
            var status = String(data.status || '').toLowerCase();
            var videoUrl = data.video_url || '';
            if (['failed', 'failure', 'error', 'cancelled', 'canceled', 'expired'].indexOf(status) >= 0 || data.ok === false) {
              renderVideoResult('', status || 'failed', false);
              showMessage(data.error || 'Seedance 任务失败，请调整素材或补充要求后重试。', true);
              return;
            }
            if (videoUrl || ['success', 'succeeded', 'done', 'completed'].indexOf(status) >= 0) {
              renderVideoResult(videoUrl, status || 'completed', false);
              showMessage(videoUrl ? '复刻视频已生成。' : '任务已完成，但没有解析到视频 URL，请查看原始返回。', !videoUrl);
              return;
            }
            renderVideoResult('', data.status || 'running', true);
            showMessage(videoStatusMeta(data.status || 'running')[0] + '，请稍等。', false);
            pollTask(false);
          });
        })
        .catch(function(err) {
          showMessage(err && err.message ? err.message : '查询失败', true);
        });
    }, wait);
  }

  function bindEvents() {
    var back = $('viralRemixBackBtn');
    if (back) back.addEventListener('click', function() {
      if (typeof window._ensureSkillStoreVisible === 'function') window._ensureSkillStoreVisible();
      try { location.hash = 'skill-store'; } catch (e) {}
    });
    var personBtn = $('viralPersonUploadBtn');
    var personInput = $('viralPersonInput');
    if (personBtn && personInput) personBtn.addEventListener('click', function() { personInput.click(); });
    if (personInput) personInput.addEventListener('change', function(e) {
      state.personImage = (e.target.files || [])[0] || null;
      setFilePreview('viralPersonPreview', state.personImage, 'image');
    });
    var videoBtn = $('viralOriginalVideoPickBtn');
    var videoInput = $('viralOriginalVideoInput');
    if (videoBtn && videoInput) videoBtn.addEventListener('click', function() { videoInput.click(); });
    if (videoInput) videoInput.addEventListener('change', function(e) {
      state.originalVideo = (e.target.files || [])[0] || null;
      state.originalVideoUrl = '';
      if ($('viralOriginalVideoUrlInput')) $('viralOriginalVideoUrlInput').value = '';
      setFilePreview('viralOriginalVideoPreview', state.originalVideo, 'video');
      if (state.originalVideo) showMessage('已选择新原视频，开始复刻时会重新上传。', false);
    });
    var productBtn = $('viralProductPickBtn');
    var productInput = $('viralProductInput');
    if (productBtn && productInput) productBtn.addEventListener('click', function() { productInput.click(); });
    if (productInput) productInput.addEventListener('change', function(e) {
      state.productImage = (e.target.files || [])[0] || null;
      state.productUrl = '';
      state.cleanedProductUrl = '';
      if ($('viralProductUrlInput')) $('viralProductUrlInput').value = '';
      setFilePreview('viralProductPreview', state.productImage, 'image');
      if (state.productImage) showMessage('已选择新产品图，开始复刻时会重新上传并作为产品参考。', false);
    });
    if ($('viralCharacterGenerateBtn')) $('viralCharacterGenerateBtn').addEventListener('click', generateCharacterReference);
    if ($('viralUploadVideoBtn')) $('viralUploadVideoBtn').addEventListener('click', function() {
      setBusy(true);
      uploadOriginalVideo().catch(function(err) {
        showMessage(err && err.message ? err.message : '原视频上传失败', true);
      }).finally(function() { setBusy(false); });
    });
    if ($('viralUploadProductBtn')) $('viralUploadProductBtn').addEventListener('click', function() {
      setBusy(true);
      uploadProductImage().catch(function(err) {
        showMessage(err && err.message ? err.message : '产品图片上传失败', true);
      }).finally(function() { setBusy(false); });
    });
    if ($('viralCleanProductBtn')) $('viralCleanProductBtn').addEventListener('click', function() {
      setBusy(true);
      cleanProductReference().catch(function(err) {
        showMessage(err && err.message ? err.message : '白底产品图生成失败', true);
      }).finally(function() { setBusy(false); });
    });
    if ($('viralRemixStartBtn')) $('viralRemixStartBtn').addEventListener('click', startRemix);
  }

  window.initViralVideoRemixView = function() {
    var root = $('content-viral-video-remix');
    if (!root) return;
    if (!root.getAttribute('data-viral-remix-init')) {
      root.setAttribute('data-viral-remix-init', '1');
      bindEvents();
      renderCharacterResult('');
      renderVideoResult('', '');
    }
  };
})();
