(function() {
  var state = {
    originalVideo: null,
    personImage: null,
    productImage: null,
    productImages: [],
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

  function compactText(text, maxLen) {
    var value = String(text || '').replace(/\s+/g, ' ').trim();
    var limit = Number(maxLen || 16);
    if (!value || value.length <= limit) return value;
    return value.slice(0, Math.max(1, limit - 1)) + '\u2026';
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

  function showShareVideoStatus(text, isError) {
    var el = $('viralShareVideoStatus');
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('err', !!isError);
    el.classList.toggle('ok', !!text && !isError);
  }

  function normalizeApiError(detail, fallback) {
    if (!detail) return fallback || '\u8bf7\u6c42\u5931\u8d25';
    if (typeof detail === 'string') return detail;
    if (detail.detail) return normalizeApiError(detail.detail, fallback);
    if (detail.message) return detail.message;
    if (detail.error) return normalizeApiError(detail.error, fallback);
    try { return JSON.stringify(detail); } catch (e) { return String(detail); }
  }

  function videoStatusMeta(status) {
    var value = String(status || '').toLowerCase();
    var map = {
      queued: ['\u7b49\u5f85\u5f00\u59cb', '\u4efb\u52a1\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u7b49\u5f85 Seedance \u5206\u914d\u751f\u6210\u8d44\u6e90\u3002'],
      pending: ['\u7b49\u5f85\u5f00\u59cb', '\u4efb\u52a1\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u7b49\u5f85 Seedance \u5206\u914d\u751f\u6210\u8d44\u6e90\u3002'],
      submitted: ['\u7b49\u5f85\u5f00\u59cb', '\u4efb\u52a1\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u7b49\u5f85 Seedance \u5206\u914d\u751f\u6210\u8d44\u6e90\u3002'],
      created: ['\u7b49\u5f85\u5f00\u59cb', '\u4efb\u52a1\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u7b49\u5f85 Seedance \u5206\u914d\u751f\u6210\u8d44\u6e90\u3002'],
      running: ['\u6b63\u5728\u751f\u6210\u89c6\u9891', 'Seedance \u6b63\u5728\u6309\u539f\u89c6\u9891\u7684\u52a8\u4f5c\u3001\u8fd0\u955c\u548c\u8282\u594f\u751f\u6210\u590d\u523b\u7ed3\u679c\u3002'],
      processing: ['\u6b63\u5728\u751f\u6210\u89c6\u9891', 'Seedance \u6b63\u5728\u6309\u539f\u89c6\u9891\u7684\u52a8\u4f5c\u3001\u8fd0\u955c\u548c\u8282\u594f\u751f\u6210\u590d\u523b\u7ed3\u679c\u3002'],
      in_progress: ['\u6b63\u5728\u751f\u6210\u89c6\u9891', 'Seedance \u6b63\u5728\u6309\u539f\u89c6\u9891\u7684\u52a8\u4f5c\u3001\u8fd0\u955c\u548c\u8282\u594f\u751f\u6210\u590d\u523b\u7ed3\u679c\u3002'],
      success: ['\u751f\u6210\u5b8c\u6210', '\u89c6\u9891\u5df2\u7ecf\u751f\u6210\u5b8c\u6210\u3002'],
      succeeded: ['\u751f\u6210\u5b8c\u6210', '\u89c6\u9891\u5df2\u7ecf\u751f\u6210\u5b8c\u6210\u3002'],
      done: ['\u751f\u6210\u5b8c\u6210', '\u89c6\u9891\u5df2\u7ecf\u751f\u6210\u5b8c\u6210\u3002'],
      completed: ['\u751f\u6210\u5b8c\u6210', '\u89c6\u9891\u5df2\u7ecf\u751f\u6210\u5b8c\u6210\u3002'],
      failed: ['\u751f\u6210\u5931\u8d25', '\u4efb\u52a1\u6ca1\u6709\u5b8c\u6210\uff0c\u8bf7\u8c03\u6574\u7d20\u6750\u6216\u8865\u5145\u8981\u6c42\u540e\u91cd\u8bd5\u3002'],
      failure: ['\u751f\u6210\u5931\u8d25', '\u4efb\u52a1\u6ca1\u6709\u5b8c\u6210\uff0c\u8bf7\u8c03\u6574\u7d20\u6750\u6216\u8865\u5145\u8981\u6c42\u540e\u91cd\u8bd5\u3002'],
      error: ['\u751f\u6210\u5931\u8d25', '\u4efb\u52a1\u6ca1\u6709\u5b8c\u6210\uff0c\u8bf7\u8c03\u6574\u7d20\u6750\u6216\u8865\u5145\u8981\u6c42\u540e\u91cd\u8bd5\u3002'],
      cancelled: ['\u5df2\u53d6\u6d88', '\u4efb\u52a1\u5df2\u53d6\u6d88\u3002'],
      canceled: ['\u5df2\u53d6\u6d88', '\u4efb\u52a1\u5df2\u53d6\u6d88\u3002'],
      expired: ['\u4efb\u52a1\u8fc7\u671f', '\u4efb\u52a1\u5df2\u8fc7\u671f\uff0c\u8bf7\u91cd\u65b0\u63d0\u4ea4\u3002']
    };
    return map[value] || ['\u6b63\u5728\u751f\u6210\u89c6\u9891', '\u6b63\u5728\u67e5\u8be2\u751f\u6210\u8fdb\u5ea6\uff0c\u8bf7\u7a0d\u7b49\u3002'];
  }

  function setBusy(flag) {
    state.submitting = !!flag;
    ['viralCharacterGenerateBtn', 'viralRemixStartBtn', 'viralUploadVideoBtn', 'viralUploadProductBtn', 'viralCleanProductBtn', 'viralShareVideoResolveBtn'].forEach(function(id) {
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
    if (label) label.textContent = flag ? '\u751f\u6210\u4e2d...' : '\u751f\u6210\u4eba\u7269\u56db\u89c6\u56fe';
  }

  function setFilePreview(targetId, file, kind) {
    var el = $(targetId);
    if (!el) return;
    if (!file) {
      el.innerHTML = '<div class="tvc-upload-empty">\u672a\u9009\u62e9\u7d20\u6750</div>';
      return;
    }
    var url = URL.createObjectURL(file);
    var media = kind === 'video'
      ? '<video src="' + escapeHtml(url) + '" muted playsinline preload="metadata"></video>'
      : '<img src="' + escapeHtml(url) + '" alt="">';
    el.innerHTML = '<div class="tvc-upload-card">' + media + '<div class="tvc-upload-card-body"><div class="tvc-upload-card-title">' + escapeHtml(compactText(file.name, 18)) + '</div></div></div>';
  }

  function setFilesPreview(targetId, files) {
    var el = $(targetId);
    if (!el) return;
    var rows = Array.isArray(files) ? files.filter(Boolean) : [];
    if (!rows.length) {
      el.innerHTML = '<div class="tvc-upload-empty">\u672a\u9009\u62e9\u7d20\u6750</div>';
      return;
    }
    el.innerHTML = rows.map(function(file, index) {
      var url = URL.createObjectURL(file);
      return '<div class="viral-product-thumb-card" title="' + escapeHtml(file.name) + '"><img src="' + escapeHtml(url) + '" alt=""><span class="viral-product-thumb-index">' + (index + 1) + '</span><span class="viral-product-thumb-tag">\u89c6\u89d2</span><span class="viral-product-thumb-title">' + escapeHtml(compactText(file.name, 10)) + '</span></div>';
    }).join('');
  }

  function setUrlPreview(targetId, url, label) {
    var el = $(targetId);
    if (!el) return;
    if (!url) {
      el.innerHTML = '<div class="tvc-upload-empty">\u672a\u9009\u62e9\u7d20\u6750</div>';
      return;
    }
    var title = label || '\u4ea7\u54c1\u53c2\u8003\u56fe';
    el.innerHTML = '<div class="viral-product-thumb-card" title="' + escapeHtml(title) + '"><img src="' + escapeHtml(url) + '" alt=""><span class="viral-product-thumb-tag">\u5df2\u751f\u6210</span><span class="viral-product-thumb-title">' + escapeHtml(compactText(title, 10)) + '</span></div>';
  }

  function setVideoUrlPreview(targetId, url, label) {
    var el = $(targetId);
    if (!el) return;
    if (!url) {
      el.innerHTML = '<div class="tvc-upload-empty">\u672a\u9009\u62e9\u7d20\u6750</div>';
      return;
    }
    var title = label || '\u89c6\u9891\u5df2\u5c31\u7eea';
    el.innerHTML = '<div class="viral-source-video-card" role="button" tabindex="0" title="' + escapeHtml(title) + '"><video src="' + escapeHtml(url) + '" muted playsinline preload="metadata"></video><span class="viral-source-video-badge">\u70b9\u51fb\u9884\u89c8</span><span class="viral-source-video-title">' + escapeHtml(compactText(title, 14)) + '</span></div>';
    var card = el.querySelector('.viral-source-video-card');
    if (card) {
      card.addEventListener('click', function() { openVideoModal(url, title); });
      card.addEventListener('keydown', function(evt) {
        if (evt.key === 'Enter' || evt.key === ' ') {
          evt.preventDefault();
          openVideoModal(url, title);
        }
      });
    }
  }

  function ensureVideoModal() {
    var root = document.getElementById('content-viral-video-remix') || document.body;
    var modal = $('viralVideoPreviewModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'viralVideoPreviewModal';
    modal.className = 'viral-video-modal';
    modal.innerHTML = '<div class="viral-video-modal-card" role="dialog" aria-modal="true" aria-labelledby="viralVideoPreviewTitle"><div class="viral-video-modal-head"><h4 id="viralVideoPreviewTitle" class="viral-video-modal-title"></h4><button type="button" class="viral-video-modal-close" aria-label="\u5173\u95ed\u9884\u89c8">\u00d7</button></div><video controls playsinline></video></div>';
    root.appendChild(modal);
    modal.addEventListener('click', function(evt) {
      if (evt.target === modal) closeVideoModal();
    });
    var closeBtn = modal.querySelector('.viral-video-modal-close');
    if (closeBtn) closeBtn.addEventListener('click', closeVideoModal);
    document.addEventListener('keydown', function(evt) {
      if (evt.key === 'Escape' && modal.classList.contains('is-open')) closeVideoModal();
    });
    return modal;
  }

  function openVideoModal(url, title) {
    var modal = ensureVideoModal();
    var video = modal.querySelector('video');
    var heading = modal.querySelector('.viral-video-modal-title');
    if (heading) heading.textContent = title || '\u89c6\u9891\u9884\u89c8';
    if (video) {
      if (video.getAttribute('src') !== url) video.src = url;
      video.currentTime = 0;
    }
    modal.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    var closeBtn = modal.querySelector('.viral-video-modal-close');
    if (closeBtn) closeBtn.focus();
    if (video) video.play().catch(function() {});
  }

  function closeVideoModal() {
    var modal = $('viralVideoPreviewModal');
    if (!modal) return;
    var video = modal.querySelector('video');
    if (video) video.pause();
    modal.classList.remove('is-open');
    document.body.style.overflow = '';
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
        if (!resp.ok) throw new Error(normalizeApiError(data, '\u7d20\u6750\u4e0a\u4f20\u5931\u8d25'));
        var url = data.source_url || '';
        if (!url) throw new Error('\u7d20\u6750\u4e0a\u4f20\u6210\u529f\uff0c\u4f46\u6ca1\u6709\u5f97\u5230\u516c\u7f51 URL');
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
      throw new Error(label + '\u5f53\u524d\u53ea\u6709\u672c\u5730\u9884\u89c8\u6570\u636e\uff0c\u8bf7\u5148\u4e0a\u4f20\u6216\u751f\u6210\u516c\u7f51 URL\u3002');
    }
    throw new Error(label + '\u5fc5\u987b\u662f http/https URL\u3002');
  }

  function parseProductUrlList(raw) {
    return String(raw || '')
      .split(/[\n\r,;]+/)
      .map(function(item) { return String(item || '').trim(); })
      .filter(function(item, index, arr) { return item && arr.indexOf(item) === index; });
  }

  function renderCharacterResult(url, done) {
    var surface = $('viralCharacterResult');
    if (!surface) return;
    if (!url) {
      surface.innerHTML = '<div class="tvc-video-placeholder"><strong>\u4eba\u7269\u56db\u89c6\u56fe</strong><span>\u751f\u6210\u4eba\u7269\u53c2\u8003\u56fe\u540e\uff0c\u4f1a\u5728\u8fd9\u91cc\u9884\u89c8\u3002</span></div>';
      return;
    }
    var seq = ++characterRenderSeq;
    surface.innerHTML = '<div class="tvc-video-placeholder"><strong>\u56fe\u7247\u52a0\u8f7d\u4e2d</strong><span>\u4eba\u7269\u56db\u89c6\u56fe\u5df2\u751f\u6210\uff0c\u6b63\u5728\u52a0\u8f7d\u9884\u89c8\u3002</span></div>';
    var img = new Image();
    img.decoding = 'async';
    img.onload = function() {
      if (seq !== characterRenderSeq) return;
      surface.innerHTML = '';
      img.alt = '\u4eba\u7269\u56db\u89c6\u56fe';
      img.style.width = '100%';
      img.style.height = '100%';
      img.style.objectFit = 'contain';
      img.style.background = '#f7f2eb';
      surface.appendChild(img);
      if (typeof done === 'function') done(true);
    };
    img.onerror = function() {
      if (seq !== characterRenderSeq) return;
      surface.innerHTML = '<div class="tvc-video-placeholder"><strong>\u56fe\u7247\u52a0\u8f7d\u5931\u8d25</strong><span>\u7ed3\u679c URL \u5df2\u8fd4\u56de\uff0c\u4f46\u6d4f\u89c8\u5668\u65e0\u6cd5\u52a0\u8f7d\u4eba\u7269\u9884\u89c8\u56fe\uff0c\u8bf7\u68c0\u67e5 URL \u662f\u5426\u53ef\u8bbf\u95ee\u3002</span></div>';
      if (typeof done === 'function') done(false);
    };
    img.src = url;
  }

  function renderProductReferenceResult(url, done) {
    var surface = $('viralProductReferenceResult');
    if (!surface) return;
    if (!url) {
      surface.innerHTML = '<div class="tvc-video-placeholder"><strong>\u4ea7\u54c1\u5168\u89c6\u56fe</strong><span>\u5148\u751f\u6210\u4ea7\u54c1\u53c2\u8003\u56fe\uff0c\u786e\u8ba4\u540e\u518d\u5f00\u59cb\u590d\u523b\u89c6\u9891\u3002</span></div>';
      return;
    }
    surface.innerHTML = '<div class="tvc-video-placeholder"><strong>\u52a0\u8f7d\u4e2d</strong><span>\u6b63\u5728\u52a0\u8f7d\u4ea7\u54c1\u53c2\u8003\u56fe\u3002</span></div>';
    var img = new Image();
    img.decoding = 'async';
    img.onload = function() {
      surface.innerHTML = '';
      img.alt = '\u4ea7\u54c1\u5168\u89c6\u56fe';
      img.style.width = '100%';
      img.style.height = '100%';
      img.style.objectFit = 'contain';
      img.style.background = '#f7f2eb';
      surface.appendChild(img);
      if (typeof done === 'function') done(true);
    };
    img.onerror = function() {
      surface.innerHTML = '<div class="tvc-video-placeholder"><strong>\u52a0\u8f7d\u5931\u8d25</strong><span>\u4ea7\u54c1\u53c2\u8003\u56fe URL \u5df2\u8fd4\u56de\uff0c\u4f46\u9884\u89c8\u52a0\u8f7d\u5931\u8d25\u3002</span></div>';
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
          '<div class="viral-status-note">\u751f\u6210\u671f\u95f4\u53ef\u4ee5\u505c\u7559\u5728\u5f53\u524d\u9875\u9762\uff0c\u5b8c\u6210\u540e\u89c6\u9891\u4f1a\u81ea\u52a8\u663e\u793a\u5728\u8fd9\u91cc\u3002</div>',
        '</div>'
      ].join('');
      return;
    }
    var idle = status ? videoStatusMeta(status) : ['\u7b49\u5f85\u63d0\u4ea4', '\u4e0a\u4f20\u539f\u89c6\u9891\u3001\u4eba\u7269\u56db\u89c6\u56fe\u548c\u4ea7\u54c1\u56fe\u540e\uff0c\u70b9\u51fb\u5f00\u59cb\u590d\u523b\u89c6\u9891\u3002'];
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
    showCharacterStatus('\u6b63\u5728\u751f\u6210\u4eba\u7269\u56db\u89c6\u56fe...', false);
    showMessage('', false);
    fetch(baseUrl() + '/api/viral-video-remix/character-reference', {
      method: 'POST',
      headers: authHeadersSafe(),
      body: form
    })
      .then(function(resp) {
        return resp.json().catch(function() { return {}; }).then(function(data) {
          if (!resp.ok) throw new Error(normalizeApiError(data, '\u4eba\u7269\u56db\u89c6\u56fe\u751f\u6210\u5931\u8d25'));
          var item = (data.images || [])[0] || {};
          var url = item.url || item.data_url || '';
          if (!url) throw new Error('\u4eba\u7269\u56db\u89c6\u56fe\u6ca1\u6709\u8fd4\u56de\u53ef\u7528\u56fe\u7247');
          state.characterUrl = url;
          if ($('viralCharacterUrlInput')) $('viralCharacterUrlInput').value = url;
          showCharacterStatus('\u5df2\u751f\u6210\uff0c\u6b63\u5728\u52a0\u8f7d\u9884\u89c8\u56fe...', false);
          renderCharacterResult(url, function(ok) {
            showCharacterStatus(ok ? '\u5df2\u751f\u6210\uff0c\u53ef\u4ee5\u7ee7\u7eed\u4e0a\u4f20\u4ea7\u54c1\u56fe\u548c\u539f\u89c6\u9891\u3002' : '\u5df2\u751f\u6210\uff0c\u4f46\u9884\u89c8\u56fe\u52a0\u8f7d\u5931\u8d25\u3002', !ok);
          });
        });
      })
      .catch(function(err) {
        showCharacterStatus(err && err.message ? err.message : '\u4eba\u7269\u56db\u89c6\u56fe\u751f\u6210\u5931\u8d25', true);
      })
      .finally(function() {
        setCharacterLoading(false);
        setBusy(false);
      });
  }

  function uploadOriginalVideo() {
    if (!state.originalVideo) {
      showMessage('\u8bf7\u5148\u9009\u62e9\u539f\u7206\u6b3e\u89c6\u9891\u6587\u4ef6\uff0c\u6216\u7c98\u8d34\u5206\u4eab\u6587\u6848\u5e76\u70b9\u51fb\u89e3\u6790\u3002', true);
      return Promise.resolve('');
    }
    showMessage('\u6b63\u5728\u4e0a\u4f20\u539f\u7206\u6b3e\u89c6\u9891...', false);
    return uploadAsset(state.originalVideo).then(function(data) {
      state.originalVideoUrl = data.source_url || '';
      if ($('viralOriginalVideoUrlInput')) $('viralOriginalVideoUrlInput').value = state.originalVideoUrl;
      showMessage('\u539f\u89c6\u9891\u5df2\u4e0a\u4f20\u3002', false);
      return state.originalVideoUrl;
    });
  }

  function resolveShareVideo() {
    var input = $('viralShareVideoTextInput');
    var text = (input && input.value ? input.value : '').trim();
    if (!text) {
      showShareVideoStatus('\u8bf7\u5148\u7c98\u8d34\u5305\u542b\u89c6\u9891\u94fe\u63a5\u7684\u5206\u4eab\u6587\u6848\u6216\u89c6\u9891\u94fe\u63a5\u3002', true);
      return Promise.resolve('');
    }
    showShareVideoStatus('\u6b63\u5728\u89e3\u6790\u5206\u4eab\u94fe\u63a5\u5e76\u4e0b\u8f7d\u89c6\u9891...', false);
    showMessage('\u6b63\u5728\u89e3\u6790\u5206\u4eab\u89c6\u9891...', false);
    return fetch(baseUrl() + '/api/viral-video-remix/share-video/resolve', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeadersSafe()),
      body: JSON.stringify({ share_text: text })
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(normalizeApiError(data, '\u89c6\u9891\u89e3\u6790\u5931\u8d25'));
        var url = data.source_url || data.url || '';
        if (!url) throw new Error('\u89c6\u9891\u89e3\u6790\u6210\u529f\uff0c\u4f46\u6ca1\u6709\u5f97\u5230\u53ef\u7528\u89c6\u9891 URL');
        state.originalVideo = null;
        state.originalVideoUrl = url;
        if ($('viralOriginalVideoUrlInput')) $('viralOriginalVideoUrlInput').value = url;
        setVideoUrlPreview('viralOriginalVideoPreview', url, data.title || '\u5206\u4eab\u89c6\u9891\u5df2\u5c31\u7eea');
        showShareVideoStatus('\u8bc6\u522b\u5b8c\u6210\uff0c\u89c6\u9891\u5df2\u4e0b\u8f7d\u5e76\u4e0a\u4f20\u6210\u529f\u3002', false);
        showMessage('\u539f\u89c6\u9891\u5df2\u5c31\u7eea\uff0c\u53ef\u4ee5\u7ee7\u7eed\u751f\u6210\u4ea7\u54c1\u5168\u89c6\u56fe\u6216\u5f00\u59cb\u590d\u523b\u3002', false);
        return url;
      });
    });
  }

  function uploadProductImage() {
    if ((state.productImages || []).length > 1) {
      showMessage('\u5df2\u9009\u62e9\u591a\u5f20\u4ea7\u54c1\u56fe\uff0c\u8bf7\u76f4\u63a5\u751f\u6210\u4ea7\u54c1\u53c2\u8003\u56fe\u3002', false);
      return Promise.resolve('');
    }
    if (!state.productImage) {
      showMessage('\u8bf7\u5148\u9009\u62e9\u4ea7\u54c1\u56fe\uff0c\u6216\u7c98\u8d34\u4ea7\u54c1\u56fe URL\u3002', true);
      return Promise.resolve('');
    }
    showMessage('\u6b63\u5728\u4e0a\u4f20\u4ea7\u54c1\u56fe...', false);
    return uploadAsset(state.productImage).then(function(data) {
      state.productUrl = data.source_url || '';
      state.cleanedProductUrl = '';
      if ($('viralProductUrlInput')) $('viralProductUrlInput').value = state.productUrl;
      showMessage('\u4ea7\u54c1\u56fe\u5df2\u4e0a\u4f20\u3002', false);
      return state.productUrl;
    });
  }

  function cleanProductReference(sourceUrl) {
    var urlList = Array.isArray(sourceUrl) ? sourceUrl : parseProductUrlList(sourceUrl || (($('viralProductUrlInput') || {}).value || state.productUrl || ''));
    var localImages = (state.productImages || []).filter(Boolean);
    if (!localImages.length && !urlList.length) {
      showMessage('\u8bf7\u5148\u9009\u62e9\u4e00\u5f20\u6216\u591a\u5f20\u4ea7\u54c1\u56fe\uff0c\u6216\u7c98\u8d34\u4ea7\u54c1\u56fe URL\u3002', true);
      return Promise.resolve('');
    }
    var form = new FormData();
    form.append('prompt', (($('viralProductCleanPrompt') || {}).value || '').trim());
    if (localImages.length) {
      localImages.forEach(function(file) { form.append('images', file); });
    } else {
      urlList.forEach(function(url) { form.append('image_urls', url); });
      if (urlList[0]) form.append('image_url', urlList[0]);
    }
    showMessage(localImages.length + urlList.length > 1 ? '\u6b63\u5728\u751f\u6210\u4ea7\u54c1\u5168\u89c6\u56fe...' : '\u6b63\u5728\u751f\u6210\u4ea7\u54c1\u53c2\u8003\u56fe...', false);
    var status = $('viralProductCleanStatus');
    if (status) status.textContent = '\u6b63\u5728\u751f\u6210\u53c2\u8003\u56fe...';
    return fetch(baseUrl() + '/api/viral-video-remix/product-reference', {
      method: 'POST',
      headers: authHeadersSafe(),
      body: form
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(normalizeApiError(data, '\u4ea7\u54c1\u53c2\u8003\u56fe\u751f\u6210\u5931\u8d25'));
        var item = (data.images || [])[0] || {};
        var url = item.url || item.data_url || '';
        if (!url) throw new Error('\u4ea7\u54c1\u53c2\u8003\u56fe\u6ca1\u6709\u8fd4\u56de\u53ef\u7528\u56fe\u7247');
        state.cleanedProductUrl = url;
        state.productUrl = url;
        if ($('viralProductUrlInput')) $('viralProductUrlInput').value = url;
        setUrlPreview('viralProductPreview', url, Number(data.source_count || 0) > 1 ? '\u4ea7\u54c1\u5168\u89c6\u56fe' : '\u4ea7\u54c1\u53c2\u8003\u56fe');
        renderProductReferenceResult(url);
        if (status) status.textContent = data.fallback_used ? '\u5df2\u7528\u62fc\u56fe\u515c\u5e95\u751f\u6210' : '\u53c2\u8003\u56fe\u5df2\u751f\u6210';
        var productAssetId = data.asset_id || ((data.asset || {}).asset_id || '') || item.asset_id || '';
        showMessage((data.fallback_used ? 'GPT-Image-2 \u751f\u6210\u5931\u8d25\uff0c\u5df2\u81ea\u52a8\u6539\u7528\u591a\u56fe\u62fc\u56fe\u515c\u5e95\u3002' : '\u4ea7\u54c1\u53c2\u8003\u56fe\u5df2\u751f\u6210\uff0c\u53ef\u4ee5\u5148\u786e\u8ba4\u518d\u590d\u523b\u3002') + (productAssetId ? ' \u5df2\u5165\u7d20\u6750\u5e93\uff1a' + productAssetId : ''), false);
        return url;
      });
    }).catch(function(err) {
      if (status) status.textContent = '\u53c2\u8003\u56fe\u751f\u6210\u5931\u8d25';
      throw err;
    });
  }

  function startRemix() {
    if (state.submitting) return;
    var originalVideoUrl = (($('viralOriginalVideoUrlInput') || {}).value || state.originalVideoUrl || '').trim();
    var characterUrl = (($('viralCharacterUrlInput') || {}).value || state.characterUrl || '').trim();
    var productUrl = (($('viralProductUrlInput') || {}).value || state.productUrl || '').trim();
    var productUrlList = parseProductUrlList(productUrl);
    var useGeneratedReference = !!(($('viralCleanProductCheck') || {}).checked);
    setBusy(true);
    Promise.resolve()
      .then(function() {
        if (state.originalVideo) return uploadOriginalVideo();
        return originalVideoUrl;
      })
      .then(function(url) {
        originalVideoUrl = url || originalVideoUrl;
        if (useGeneratedReference) {
          if (state.cleanedProductUrl) return state.cleanedProductUrl;
          return cleanProductReference(productUrlList);
        }
        if (!productUrl && state.productImage) return uploadProductImage();
        if (!productUrl && (state.productImages || []).length > 1) {
          throw new Error('\u5df2\u9009\u62e9\u591a\u5f20\u4ea7\u54c1\u56fe\uff0c\u8bf7\u4fdd\u6301\u52fe\u9009\u201c\u751f\u6210\u4ea7\u54c1\u53c2\u8003\u56fe\u201d\u540e\u518d\u5f00\u59cb\u590d\u523b\u3002');
        }
        return productUrl;
      })
      .then(function(url) {
        productUrl = url || productUrl;
        if (!originalVideoUrl || !productUrl) {
          throw new Error('\u7f3a\u5c11\u539f\u89c6\u9891\u6216\u4ea7\u54c1\u56fe URL\uff0c\u8bf7\u5148\u4e0a\u4f20\u6216\u751f\u6210\u7d20\u6750\u3002');
        }
        var audioPrompt = (($('viralRemixAudioPrompt') || {}).value || '').trim();
        var narrationScript = (($('viralRemixNarrationScript') || {}).value || '').trim();
        var body = {
          original_video_url: ensureRemoteUrl(originalVideoUrl, '\u539f\u89c6\u9891', false),
          character_image_url: characterUrl ? ensureRemoteUrl(characterUrl, '\u4eba\u7269\u56db\u89c6\u56fe', true) : '',
          product_image_url: ensureRemoteUrl(productUrl, '\u4ea7\u54c1\u56fe', true),
          prompt: (($('viralRemixPrompt') || {}).value || '').trim(),
          audio_prompt: audioPrompt,
          narration_script: narrationScript,
          model: (($('viralRemixModelSelect') || {}).value || 'doubao-seedance-2-0-260128'),
          ratio: (($('viralRemixRatioSelect') || {}).value || '9:16'),
          resolution: (($('viralRemixResolutionSelect') || {}).value || '720p'),
          duration: Number((($('viralRemixDurationSelect') || {}).value || 10)),
          generate_audio: !!(($('viralRemixAudioCheck') || {}).checked || audioPrompt || narrationScript),
          use_character_reference: !!(($('viralUseCharacterCheck') || {}).checked && characterUrl),
          watermark: false
        };
        showMessage('\u6b63\u5728\u63d0\u4ea4 Seedance \u4ea7\u54c1\u66ff\u6362\u4efb\u52a1...', false);
        return fetch(baseUrl() + '/api/viral-video-remix/seedance/start', {
          method: 'POST',
          headers: Object.assign({ 'Content-Type': 'application/json' }, authHeadersSafe()),
          body: JSON.stringify(body)
        });
      })
      .then(function(resp) {
        return resp.json().catch(function() { return {}; }).then(function(data) {
          if (!resp.ok) throw new Error(normalizeApiError(data, 'Seedance \u4efb\u52a1\u63d0\u4ea4\u5931\u8d25'));
          state.taskId = data.task_id || '';
          if (!state.taskId) throw new Error('Seedance \u6ca1\u6709\u8fd4\u56de\u4efb\u52a1 ID');
          showMessage('\u4efb\u52a1\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u751f\u6210\u89c6\u9891\u3002', false);
          renderVideoResult('', 'running', true);
          pollTask(true);
        });
      })
      .catch(function(err) {
        showMessage(err && err.message ? err.message : '\u63d0\u4ea4\u5931\u8d25', true);
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
            if (!resp.ok) throw new Error(normalizeApiError(data, '\u67e5\u8be2\u5931\u8d25'));
            var status = String(data.status || '').toLowerCase();
            var videoUrl = data.video_url || '';
            if (['failed', 'failure', 'error', 'cancelled', 'canceled', 'expired'].indexOf(status) >= 0 || data.ok === false) {
              renderVideoResult('', status || 'failed', false);
              showMessage(data.error || 'Seedance \u4efb\u52a1\u5931\u8d25\uff0c\u8bf7\u8c03\u6574\u7d20\u6750\u6216\u8865\u5145\u8981\u6c42\u540e\u91cd\u8bd5\u3002', true);
              return;
            }
            if (videoUrl || ['success', 'succeeded', 'done', 'completed'].indexOf(status) >= 0) {
              renderVideoResult(videoUrl, status || 'completed', false);
              var finalAssetId = data.asset_id || ((data.asset || {}).asset_id || '') || (((data.result || {}).asset || {}).asset_id || '') || ((data.result || {}).asset_id || '');
              showMessage(videoUrl ? ('\u590d\u523b\u89c6\u9891\u5df2\u751f\u6210\u3002' + (finalAssetId ? ' \u5df2\u5165\u7d20\u6750\u5e93\uff1a' + finalAssetId : '')) : '\u4efb\u52a1\u5df2\u5b8c\u6210\uff0c\u4f46\u6ca1\u6709\u89e3\u6790\u5230\u89c6\u9891 URL\uff0c\u8bf7\u67e5\u770b\u539f\u59cb\u8fd4\u56de\u3002', !videoUrl);
              return;
            }
            renderVideoResult('', data.status || 'running', true);
            showMessage(videoStatusMeta(data.status || 'running')[0] + '\uff0c\u8bf7\u7a0d\u7b49\u3002', false);
            pollTask(false);
          });
        })
        .catch(function(err) {
          showMessage(err && err.message ? err.message : '\u67e5\u8be2\u5931\u8d25', true);
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
      if (state.originalVideo) showMessage('\u5df2\u9009\u62e9\u65b0\u539f\u89c6\u9891\uff0c\u5f00\u59cb\u590d\u523b\u65f6\u4f1a\u91cd\u65b0\u4e0a\u4f20\u3002', false);
    });
    var productBtn = $('viralProductPickBtn');
    var productInput = $('viralProductInput');
    if (productBtn && productInput) productBtn.addEventListener('click', function() { productInput.click(); });
    if (productInput) productInput.addEventListener('change', function(e) {
      state.productImages = Array.prototype.slice.call(e.target.files || []);
      state.productImage = state.productImages[0] || null;
      state.productUrl = '';
      state.cleanedProductUrl = '';
      if ($('viralProductUrlInput')) $('viralProductUrlInput').value = '';
      setFilesPreview('viralProductPreview', state.productImages);
      renderProductReferenceResult('');
      if (state.productImages.length > 1) showMessage('\u5df2\u9009\u62e9\u591a\u5f20\u4ea7\u54c1\u56fe\uff0c\u5f00\u59cb\u590d\u523b\u524d\u4f1a\u5148\u751f\u6210\u4e00\u5f20\u4ea7\u54c1\u5168\u89c6\u56fe\u3002', false);
      else if (state.productImage) showMessage('\u5df2\u9009\u62e9\u65b0\u4ea7\u54c1\u56fe\uff0c\u5f00\u59cb\u590d\u523b\u65f6\u4f1a\u91cd\u65b0\u4e0a\u4f20\u3002', false);
    });
    if ($('viralCharacterGenerateBtn')) $('viralCharacterGenerateBtn').addEventListener('click', generateCharacterReference);
    if ($('viralUploadVideoBtn')) $('viralUploadVideoBtn').addEventListener('click', function() {
      setBusy(true);
      uploadOriginalVideo().catch(function(err) {
        showMessage(err && err.message ? err.message : '\u539f\u89c6\u9891\u4e0a\u4f20\u5931\u8d25', true);
      }).finally(function() { setBusy(false); });
    });
    if ($('viralShareVideoResolveBtn')) $('viralShareVideoResolveBtn').addEventListener('click', function() {
      setBusy(true);
      resolveShareVideo().catch(function(err) {
        showShareVideoStatus(err && err.message ? err.message : '\u89c6\u9891\u89e3\u6790\u5931\u8d25', true);
        showMessage(err && err.message ? err.message : '\u89c6\u9891\u89e3\u6790\u5931\u8d25', true);
      }).finally(function() { setBusy(false); });
    });
    if ($('viralUploadProductBtn')) $('viralUploadProductBtn').addEventListener('click', function() {
      setBusy(true);
      uploadProductImage().catch(function(err) {
        showMessage(err && err.message ? err.message : '\u4ea7\u54c1\u56fe\u7247\u4e0a\u4f20\u5931\u8d25', true);
      }).finally(function() { setBusy(false); });
    });
    if ($('viralCleanProductBtn')) $('viralCleanProductBtn').addEventListener('click', function() {
      setBusy(true);
      cleanProductReference().catch(function(err) {
        showMessage(err && err.message ? err.message : '\u4ea7\u54c1\u5168\u89c6\u56fe\u751f\u6210\u5931\u8d25', true);
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
      if ($('viralProductInput')) $('viralProductInput').setAttribute('multiple', 'multiple');
      if ($('viralProductPickBtn')) $('viralProductPickBtn').textContent = '\u9009\u62e9\u4ea7\u54c1\u56fe';
      if ($('viralCleanProductBtn')) $('viralCleanProductBtn').textContent = '\u751f\u6210\u4ea7\u54c1\u5168\u89c6\u56fe';
      showProductStatus('\u4e0a\u4f20\u4ea7\u54c1\u56fe\u540e\uff0c\u5efa\u8bae\u5148\u70b9\u4e0a\u65b9\u6309\u94ae\u751f\u6210\u5168\u89c6\u56fe\u3002', false);
      if ($('viralRemixDurationSelect')) $('viralRemixDurationSelect').value = '10';
      renderCharacterResult('');
      renderProductReferenceResult('');
      renderVideoResult('', '');
    }
  };
})();
