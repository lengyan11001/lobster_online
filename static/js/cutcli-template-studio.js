(function() {
  var DEFAULT_TEMPLATE_ID = 'auto_caption_pop_huazi_v1';
  var state = {
    templates: [],
    activeTab: 'templates',
    modalTemplateId: '',
    uploadedFile: null,
    uploadedPreviewUrl: '',
    uploadedPosterUrl: '',
    sourceVideoFallback: false,
    sourceVideoOrientation: '',
    renderMode: 'ffmpeg',
    overlayTexts: {},
    positionOverrides: {},
    jobs: [],
    pollTimer: null,
    videoEventsBound: false,
    busy: false
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function apiBase() {
    return (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
  }

  function localApiBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  }

  function resolveUrl(value, base) {
    var url = String(value || '').trim();
    if (!url || /^(https?:|blob:|data:)/i.test(url)) return url;
    if (url.charAt(0) !== '/') return url;
    var root = String(base || '').replace(/\/$/, '');
    if (!root && window.location && window.location.origin) root = window.location.origin;
    return root ? (root + url) : url;
  }

  function videoShellHtml(src, options) {
    options = options || {};
    var classes = ['cutcli-template-video-shell'];
    if (options.card) classes.push('is-card-preview');
    if (options.modal) classes.push('is-modal-preview');
    if (options.job) classes.push('is-job-preview');
    var attrs = [
      'src="' + esc(src) + '"',
      'playsinline',
      'preload="metadata"'
    ];
    if (options.controls) {
      attrs.push('controls');
      attrs.push('controlsList="nofullscreen"');
    }
    if (options.poster) attrs.push('poster="' + esc(options.poster) + '"');
    if (options.muted) attrs.push('muted');
    if (options.loop) attrs.push('loop');
    return [
      '<div class="' + classes.join(' ') + '" data-cutcli-video-shell>',
        '<video ' + attrs.join(' ') + '></video>',
        '<button type="button" class="cutcli-template-video-fs" data-cutcli-video-fullscreen aria-label="\u5168\u5c4f\u9884\u89c8">\u5168\u5c4f\u9884\u89c8</button>',
        '<button type="button" class="cutcli-template-video-exit" data-cutcli-video-exit aria-label="\u9000\u51fa\u5168\u5c4f">\u9000\u51fa\u5168\u5c4f&nbsp;Esc</button>',
      '</div>'
    ].join('');
  }

  function fullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement || null;
  }

  function requestFullscreenElement(el) {
    if (!el) return Promise.reject(new Error('no element'));
    var fn = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
    if (!fn) return Promise.reject(new Error('fullscreen unsupported'));
    var result = fn.call(el);
    return result && typeof result.then === 'function' ? result : Promise.resolve();
  }

  function exitFullscreenElement() {
    var fn = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
    if (!fn) return Promise.resolve();
    var result = fn.call(document);
    return result && typeof result.then === 'function' ? result : Promise.resolve();
  }

  function syncVideoFullscreenState() {
    var active = fullscreenElement();
    document.querySelectorAll('#content-cutcli-template-studio [data-cutcli-video-shell]').forEach(function(shell) {
      shell.classList.toggle('is-cutcli-video-fullscreen', !!active && (active === shell || shell.contains(active)));
    });
  }

  function revokeUploadedPreview() {
    if (state.uploadedPreviewUrl) {
      try { URL.revokeObjectURL(state.uploadedPreviewUrl); } catch (err) {}
    }
    if (state.uploadedPosterUrl) {
      try { URL.revokeObjectURL(state.uploadedPosterUrl); } catch (err) {}
    }
    state.uploadedPreviewUrl = '';
    state.uploadedPosterUrl = '';
    state.sourceVideoFallback = false;
    state.sourceVideoOrientation = '';
  }

  function updateSourceVideoOrientation(width, height) {
    var w = Number(width);
    var h = Number(height);
    if (!isFinite(w) || !isFinite(h) || w <= 0 || h <= 0) return false;
    var next = h > w ? 'portrait' : 'landscape';
    if (state.sourceVideoOrientation === next) return false;
    state.sourceVideoOrientation = next;
    return true;
  }

  function detectPosterOrientation(src) {
    if (!src) return;
    var img = new Image();
    img.onload = function() {
      if (state.uploadedPosterUrl !== src) return;
      if (updateSourceVideoOrientation(img.naturalWidth, img.naturalHeight)) {
        renderTemplateModal();
      }
    };
    img.src = src;
  }

  function captureVideoPoster(src) {
    if (!src) return;
    var video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.preload = 'metadata';
    video.src = src;
    function cleanup() {
      video.removeAttribute('src');
      try { video.load(); } catch (err) {}
    }
    video.addEventListener('loadeddata', function() {
      try {
        updateSourceVideoOrientation(video.videoWidth, video.videoHeight);
        var canvas = document.createElement('canvas');
        canvas.width = video.videoWidth || 720;
        canvas.height = video.videoHeight || 1280;
        var ctx = canvas.getContext('2d');
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function(blob) {
          if (!blob || !state.uploadedPreviewUrl || state.uploadedPreviewUrl !== src) return;
          if (state.uploadedPosterUrl) {
            try { URL.revokeObjectURL(state.uploadedPosterUrl); } catch (err) {}
          }
          state.uploadedPosterUrl = URL.createObjectURL(blob);
          detectPosterOrientation(state.uploadedPosterUrl);
          renderTemplateModal();
        }, 'image/jpeg', 0.82);
      } catch (err) {}
      cleanup();
    }, { once: true });
    video.addEventListener('error', cleanup, { once: true });
    try { video.currentTime = 0.05; } catch (err) {}
  }

  function setUploadedFile(file) {
    revokeUploadedPreview();
    state.uploadedFile = file || null;
    if (state.uploadedFile && window.URL && URL.createObjectURL) {
      state.uploadedPreviewUrl = URL.createObjectURL(state.uploadedFile);
      captureVideoPoster(state.uploadedPreviewUrl);
      fetchLocalPreviewFrame(state.uploadedFile).then(function(url) {
        if (!url || !state.uploadedFile || state.uploadedFile !== file) {
          if (url) {
            try { URL.revokeObjectURL(url); } catch (err) {}
          }
          return;
        }
        if (state.uploadedPosterUrl) {
          try { URL.revokeObjectURL(state.uploadedPosterUrl); } catch (err) {}
        }
        state.uploadedPosterUrl = url;
        detectPosterOrientation(state.uploadedPosterUrl);
        renderTemplateModal();
      }).catch(function() {});
    }
  }

  function bindSourceVideoFallback(video) {
    if (!video || !state.uploadedPreviewUrl) return;
    var settled = false;
    var currentSrc = state.uploadedPreviewUrl;
    function syncOrientation() {
      if (state.uploadedPreviewUrl !== currentSrc) return false;
      return updateSourceVideoOrientation(video.videoWidth, video.videoHeight);
    }
    function showVideo() {
      if (state.uploadedPreviewUrl !== currentSrc) return;
      settled = true;
      var orientationChanged = syncOrientation();
      if (state.sourceVideoFallback) {
        state.sourceVideoFallback = false;
        orientationChanged = true;
      }
      if (orientationChanged) {
        renderTemplateModal();
      }
    }
    function showPoster() {
      if (settled || state.uploadedPreviewUrl !== currentSrc || !state.uploadedPosterUrl) return;
      settled = true;
      state.sourceVideoFallback = true;
      renderTemplateModal();
    }
    video.addEventListener('loadedmetadata', function() {
      if (syncOrientation()) {
        settled = true;
        renderTemplateModal();
      }
    }, { once: true });
    video.addEventListener('loadeddata', showVideo, { once: true });
    video.addEventListener('canplay', showVideo, { once: true });
    video.addEventListener('playing', showVideo, { once: true });
    video.addEventListener('error', showPoster, { once: true });
    window.setTimeout(function() {
      if (settled) return;
      if (video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) showVideo();
      else showPoster();
    }, 1200);
  }

  function bindVideoPreviewEvents() {
    if (state.videoEventsBound) return;
    state.videoEventsBound = true;
    document.addEventListener('click', function(evt) {
      var target = evt.target;
      var fullBtn = target && target.closest ? target.closest('[data-cutcli-video-fullscreen]') : null;
      if (fullBtn) {
        evt.preventDefault();
        evt.stopPropagation();
        var shell = fullBtn.closest('[data-cutcli-video-shell]');
        var video = shell ? shell.querySelector('video') : null;
        if (video) video.play().catch(function() {});
        requestFullscreenElement(shell).then(syncVideoFullscreenState).catch(function() {});
        return;
      }
      var exitBtn = target && target.closest ? target.closest('[data-cutcli-video-exit]') : null;
      if (exitBtn) {
        evt.preventDefault();
        evt.stopPropagation();
        exitFullscreenElement().then(syncVideoFullscreenState).catch(function() {});
      }
    }, true);
    document.addEventListener('fullscreenchange', syncVideoFullscreenState);
    document.addEventListener('webkitfullscreenchange', syncVideoFullscreenState);
    document.addEventListener('MSFullscreenChange', syncVideoFullscreenState);
    document.addEventListener('keydown', function(evt) {
      if (evt.key !== 'Escape' || !fullscreenElement()) return;
      evt.preventDefault();
      evt.stopPropagation();
      exitFullscreenElement().then(syncVideoFullscreenState).catch(function() {});
    }, true);
  }

  function headers() {
    return (typeof authHeaders === 'function') ? authHeaders() : {};
  }

  function formHeaders() {
    var h = headers() || {};
    var out = {};
    Object.keys(h).forEach(function(key) {
      if (String(key).toLowerCase() !== 'content-type') out[key] = h[key];
    });
    return out;
  }

  function parseJsonResponse(resp) {
    return resp.text().then(function(text) {
      var data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch (err) {
        data = { ok: false, detail: text || resp.statusText };
      }
      if (!resp.ok || data.ok === false) {
        var msg = data.detail || data.error || data.message || ('HTTP ' + resp.status);
        throw new Error(msg);
      }
      return data;
    });
  }

  function assetPublicUrl(data) {
    data = data || {};
    return data.source_url || data.public_url || data.url || data.open_url || data.preview_url || '';
  }

  function postLocalFile(path, file) {
    var fd = new FormData();
    fd.append('file', file, file.name || 'source.mp4');
    return fetch(localApiBase() + path, {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    }).then(parseJsonResponse);
  }

  function fetchLocalPreviewFrame(file) {
    if (!file) return Promise.resolve('');
    var fd = new FormData();
    fd.append('file', file, file.name || 'source.mp4');
    return fetch(localApiBase() + '/api/cutcli/local/templates/preview-frame', {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    }).then(function(resp) {
      if (!resp.ok) throw new Error('preview frame failed');
      return resp.blob();
    }).then(function(blob) {
      if (!blob || !blob.size) return '';
      return URL.createObjectURL(blob);
    });
  }

  function uploadVideoLocally(file) {
    return postLocalFile('/api/assets/upload', file).then(function(data) {
      if (!data.asset_id) throw new Error('\u672c\u673a\u7d20\u6750\u4e0a\u4f20\u6ca1\u6709\u8fd4\u56de asset_id');
      return {
        asset_id: data.asset_id,
        video_url: assetPublicUrl(data)
      };
    });
  }

  function prepareLocalVideoForServer(file) {
    return postLocalFile('/api/assets/upload', file).then(function(videoResult) {
      var fd = new FormData();
      if (videoResult.asset_id) fd.append('asset_id', videoResult.asset_id);
      else fd.append('file', file, file.name || 'source.mp4');
      return fetch(localApiBase() + '/api/assets/extract-audio', {
        method: 'POST',
        headers: formHeaders(),
        body: fd
      }).then(parseJsonResponse).then(function(audioResult) {
        return [videoResult, audioResult];
      });
    }).then(function(results) {
      var videoUrl = assetPublicUrl(results[0]);
      var audioUrl = results[1].audio_url || assetPublicUrl(results[1]);
      if (!videoUrl) throw new Error('\u672c\u673a\u89c6\u9891\u4e0a\u4f20\u6ca1\u6709\u8fd4\u56de\u516c\u7f51 URL');
      if (!audioUrl) throw new Error('\u672c\u673a\u97f3\u9891\u4e0a\u4f20\u6ca1\u6709\u8fd4\u56de\u516c\u7f51 URL');
      return {
        video_url: videoUrl,
        audio_url: audioUrl,
        video_asset_id: results[0].asset_id || '',
        audio_asset_id: results[1].asset_id || ''
      };
    });
  }

  function submitRenderForm(tpl, fd) {
    var renderPath = tpl.render_path || ('/api/cutcli/templates/' + encodeURIComponent(tpl.id || DEFAULT_TEMPLATE_ID) + '/render');
    return fetch(apiBase() + renderPath, {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    }).then(parseJsonResponse);
  }

  function submitLocalRenderForm(tpl, fd) {
    var id = encodeURIComponent((tpl && tpl.id) || DEFAULT_TEMPLATE_ID);
    return fetch(localApiBase() + '/api/cutcli/local/templates/' + id + '/render', {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    }).then(parseJsonResponse);
  }

  function templateMedia(tpl) {
    return resolveUrl((tpl && (tpl.preview_url || tpl.sample_video_url)) || '', apiBase());
  }

  function templateById(templateId) {
    return state.templates.filter(function(tpl) { return tpl.id === templateId; })[0] || null;
  }

  function modalTemplate() {
    return templateById(state.modalTemplateId) || state.templates[0] || null;
  }

  function overlayFields(tpl) {
    var fields = (tpl && tpl.overlay_fields) || (tpl && tpl.generation_strategy && tpl.generation_strategy.overlay_fields) || [];
    return Array.isArray(fields) ? fields : [];
  }

  function overlayDefaults(tpl) {
    var out = {};
    overlayFields(tpl).forEach(function(field) {
      var key = String((field && field.key) || '').trim();
      if (key) out[key] = String((field && field.default) || '');
    });
    return out;
  }

  function templateCaptionStyle(tpl) {
    var strategy = tpl && tpl.generation_strategy && typeof tpl.generation_strategy === 'object' ? tpl.generation_strategy : {};
    return (strategy.caption_style && typeof strategy.caption_style === 'object')
      ? strategy.caption_style
      : ((tpl && tpl.caption_style && typeof tpl.caption_style === 'object') ? tpl.caption_style : {});
  }

  function clamp(value, min, max) {
    var n = Number(value);
    if (!Number.isFinite(n)) n = min;
    return Math.max(min, Math.min(max, n));
  }

  function normToRatioX(value) {
    return clamp(0.5 + Number(value || 0) * 0.5, 0.05, 0.95);
  }

  function normToRatioY(value) {
    return clamp(0.5 - Number(value || 0) * 0.5, 0.05, 0.95);
  }

  function ratioToNormX(value) {
    return clamp((Number(value || 0.5) - 0.5) / 0.5, -0.95, 0.95);
  }

  function ratioToNormY(value) {
    return clamp((0.5 - Number(value || 0.5)) / 0.5, -0.95, 0.95);
  }

  function positionTargets(tpl) {
    var fields = overlayFields(tpl);
    var keys = fields.map(function(field) { return String((field && field.key) || '').trim(); });
    var out = [];
    function add(key, label) {
      if (out.some(function(item) { return item.key === key; })) return;
      out.push({ key: key, label: label });
    }
    if (keys.indexOf('top_text') >= 0 || keys.indexOf('headline') >= 0) add('top_text', '\u9876\u90e8\u6587\u6848');
    if (keys.indexOf('title') >= 0) add('title', '\u4e3b\u6807\u9898');
    if (keys.indexOf('subtitle') >= 0 || keys.indexOf('subheadline') >= 0) add('subtitle', '\u526f\u6807\u9898');
    if (keys.indexOf('badge') >= 0) add('badge', '\u6807\u7b7e');
    add('caption', '\u5b57\u5e55');
    return out;
  }

  function defaultPositionOverrides(tpl) {
    var style = templateCaptionStyle(tpl);
    var overlay = style.overlay_style && typeof style.overlay_style === 'object' ? style.overlay_style : {};
    var captionX = Number(style.transform_x != null ? style.transform_x : (style.cutcli_transform_x != null ? style.cutcli_transform_x : 0));
    var captionY = Number(style.transform_y != null ? style.transform_y : (style.cutcli_transform_y != null ? style.cutcli_transform_y : -0.66));
    var out = {
      caption: { x: Number.isFinite(captionX) ? captionX : 0, y: Number.isFinite(captionY) ? captionY : -0.66 },
      overlay: {}
    };
    positionTargets(tpl).forEach(function(target) {
      if (target.key === 'caption') return;
      if (target.key === 'top_text') {
        var topY = overlay.top_screen_y_ratio != null ? overlay.top_screen_y_ratio : overlay.top_y_ratio;
        if (topY == null && overlay.layout === 'top_banner') {
          var bannerHeight = Number(overlay.banner_height_ratio != null ? overlay.banner_height_ratio : 0.32);
          var bannerTextY = Number(overlay.headline_y_ratio != null ? overlay.headline_y_ratio : 0.5);
          topY = clamp(bannerHeight * bannerTextY, 0.07, 0.26);
        }
        out.overlay.top_text = {
          x_ratio: clamp(Number(overlay.top_screen_x_ratio != null ? overlay.top_screen_x_ratio : (overlay.top_x_ratio != null ? overlay.top_x_ratio : (overlay.headline_x_ratio != null ? overlay.headline_x_ratio : 0.5))), 0.05, 0.95),
          y_ratio: clamp(Number(topY != null ? topY : (overlay.headline_y_ratio != null ? overlay.headline_y_ratio : 0.12)), 0.05, 0.95)
        };
      } else if (target.key === 'title') {
        out.overlay.title = {
          x_ratio: clamp(Number(overlay.title_x_ratio != null ? overlay.title_x_ratio : (overlay.profile_x_ratio != null ? overlay.profile_x_ratio : (overlay.headline_x_ratio != null ? overlay.headline_x_ratio : 0.5))), 0.05, 0.95),
          y_ratio: clamp(Number(overlay.title_y_ratio != null ? overlay.title_y_ratio : (overlay.profile_y_ratio != null ? overlay.profile_y_ratio : (overlay.headline_y_ratio != null ? overlay.headline_y_ratio : 0.45))), 0.05, 0.95)
        };
      } else if (target.key === 'subtitle') {
        out.overlay.subtitle = {
          x_ratio: clamp(Number(overlay.subheadline_x_ratio != null ? overlay.subheadline_x_ratio : (overlay.headline_x_ratio != null ? overlay.headline_x_ratio : 0.5)), 0.05, 0.95),
          y_ratio: clamp(Number(overlay.subheadline_y_ratio != null ? overlay.subheadline_y_ratio : ((overlay.headline_y_ratio != null ? Number(overlay.headline_y_ratio) : 0.45) + 0.08)), 0.05, 0.95)
        };
      } else if (target.key === 'badge') {
        out.overlay.badge = {
          x_ratio: clamp(Number(overlay.badge_x_ratio != null ? overlay.badge_x_ratio : 0.5), 0.05, 0.95),
          y_ratio: clamp(Number(overlay.badge_y_ratio != null ? overlay.badge_y_ratio : 0.60), 0.05, 0.95)
        };
      }
    });
    return out;
  }

  function targetPosition(targetKey) {
    var pos = state.positionOverrides || {};
    if (targetKey === 'caption') {
      var cap = pos.caption || {};
      return { x_ratio: normToRatioX(cap.x || 0), y_ratio: normToRatioY(cap.y != null ? cap.y : -0.66) };
    }
    var overlay = pos.overlay || {};
    return overlay[targetKey] || { x_ratio: 0.5, y_ratio: 0.5 };
  }

  function setTargetPosition(targetKey, xRatio, yRatio) {
    state.positionOverrides = state.positionOverrides || { caption: {}, overlay: {} };
    if (targetKey === 'caption') {
      state.positionOverrides.caption = { x: ratioToNormX(xRatio), y: ratioToNormY(yRatio) };
      return;
    }
    state.positionOverrides.overlay = state.positionOverrides.overlay || {};
    state.positionOverrides.overlay[targetKey] = {
      x_ratio: clamp(xRatio, 0.05, 0.95),
      y_ratio: clamp(yRatio, 0.05, 0.95)
    };
  }

  function overlayKeyForTarget(tpl, targetKey) {
    var keys = overlayFields(tpl).map(function(field) {
      return String((field && field.key) || '').trim();
    });
    if (targetKey === 'top_text') return keys.indexOf('top_text') >= 0 ? 'top_text' : 'headline';
    if (targetKey === 'subtitle') return keys.indexOf('subtitle') >= 0 ? 'subtitle' : 'subheadline';
    return targetKey;
  }

  function overlayTextForTarget(tpl, targetKey) {
    if (targetKey === 'caption') return '\u5b57\u5e55\u9884\u89c8';
    var key = overlayKeyForTarget(tpl, targetKey);
    if (state.overlayTexts && state.overlayTexts[key] != null) return String(state.overlayTexts[key] || '');
    var defaults = overlayDefaults(tpl);
    return String(defaults[key] || '');
  }

  function multilineHtml(value) {
    return esc(value || '').replace(/\r\n|\r|\n/g, '<br>');
  }

  function editablePlainText(el) {
    return String((el && (el.innerText || el.textContent)) || '').replace(/\u00a0/g, ' ').trim();
  }

  function previewTextClass(tpl, targetKey) {
    var style = templateCaptionStyle(tpl);
    var overlay = style.overlay_style && typeof style.overlay_style === 'object' ? style.overlay_style : {};
    var layout = String(overlay.layout || 'default').replace(/[^a-z0-9_-]/gi, '-').toLowerCase();
    return 'cutcli-template-preview-text is-' + esc(targetKey.replace(/_/g, '-')) + ' layout-' + esc(layout || 'default');
  }

  function updateOverlayInputFromPreview(tpl, targetKey, value) {
    if (targetKey === 'caption') return;
    var key = overlayKeyForTarget(tpl, targetKey);
    var input = document.getElementById('cutcliTplOverlay_' + key);
    if (input && input.value !== value) {
      input.value = value;
      updateOverlayCounter(input);
      value = input.value || '';
    }
    state.overlayTexts = state.overlayTexts || {};
    state.overlayTexts[key] = value;
  }

  function bindPositionReset(tpl) {
    var reset = $('cutcliTplResetPositionsBtn');
    if (reset) {
      reset.addEventListener('click', function() {
        state.positionOverrides = defaultPositionOverrides(tpl);
        renderPositionLayer(tpl);
      });
    }
  }

  function renderPositionLayer(tpl) {
    var layer = $('cutcliTplPositionLayer');
    if (!layer) return;
    layer.innerHTML = positionTargets(tpl).map(function(target) {
      var pos = targetPosition(target.key);
      var editable = target.key === 'caption' ? 'false' : 'plaintext-only';
      return '<div class="' + previewTextClass(tpl, target.key) + '" contenteditable="' + editable + '" spellcheck="false" data-cutcli-preview-text-target="' + esc(target.key) + '" style="left:' + (pos.x_ratio * 100).toFixed(2) + '%;top:' + (pos.y_ratio * 100).toFixed(2) + '%;">' +
        '<span class="cutcli-template-preview-drag-dot" contenteditable="false" aria-hidden="true"></span>' +
        multilineHtml(overlayTextForTarget(tpl, target.key)) +
      '</div>';
    }).join('');
    bindPreviewTextEditing(tpl, layer);
    bindPreviewTextDrag(tpl, layer);
  }

  function bindPreviewTextEditing(tpl, layer) {
    layer.querySelectorAll('[data-cutcli-preview-text-target]').forEach(function(node) {
      var targetKey = node.dataset.cutcliPreviewTextTarget || '';
      if (targetKey === 'caption') return;
      node.addEventListener('input', function() {
        updateOverlayInputFromPreview(tpl, targetKey, editablePlainText(node));
      });
      node.addEventListener('paste', function(evt) {
        evt.preventDefault();
        var text = (evt.clipboardData || window.clipboardData).getData('text/plain');
        document.execCommand && document.execCommand('insertText', false, text);
      });
    });
  }

  function bindPreviewTextDrag(tpl, layer) {
    layer.querySelectorAll('[data-cutcli-preview-text-target]').forEach(function(node) {
      node.addEventListener('pointerdown', function(evt) {
        if (evt.button != null && evt.button !== 0) return;
        var targetKey = node.dataset.cutcliPreviewTextTarget || '';
        var fromGrip = evt.target && evt.target.closest && evt.target.closest('.cutcli-template-preview-drag-dot');
        var parent = layer.getBoundingClientRect();
        var startX = evt.clientX;
        var startY = evt.clientY;
        var dragging = targetKey === 'caption' || !!fromGrip;
        var oldEditable = node.getAttribute('contenteditable');
        var captured = false;
        if (dragging) {
          evt.preventDefault();
          node.classList.add('is-dragging');
          if (node.setPointerCapture) {
            node.setPointerCapture(evt.pointerId);
            captured = true;
          }
        }
        function move(e) {
          var dx = e.clientX - startX;
          var dy = e.clientY - startY;
          if (!dragging && Math.sqrt(dx * dx + dy * dy) < 3) return;
          if (!dragging) {
            dragging = true;
            node.classList.add('is-dragging');
            node.setAttribute('contenteditable', 'false');
            if (node.setPointerCapture) {
              node.setPointerCapture(evt.pointerId);
              captured = true;
            }
          }
          e.preventDefault();
          var x = clamp((e.clientX - parent.left) / Math.max(1, parent.width), 0.05, 0.95);
          var y = clamp((e.clientY - parent.top) / Math.max(1, parent.height), 0.05, 0.95);
          setTargetPosition(targetKey, x, y);
          node.style.left = (x * 100).toFixed(2) + '%';
          node.style.top = (y * 100).toFixed(2) + '%';
        }
        function up(e) {
          if (dragging) move(e);
          node.classList.remove('is-dragging');
          node.setAttribute('contenteditable', oldEditable || (targetKey === 'caption' ? 'false' : 'plaintext-only'));
          if (captured && node.releasePointerCapture) {
            try { node.releasePointerCapture(evt.pointerId); } catch (err) {}
          }
          window.removeEventListener('pointermove', move);
          window.removeEventListener('pointerup', up);
        }
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up, { once: true });
      });
    });
  }

  function overlaySectionTitle(fields) {
    var keys = (fields || []).map(function(field) {
      return String((field && field.key) || '').trim();
    });
    var hasTop = keys.indexOf('top_text') >= 0 || keys.indexOf('headline') >= 0;
    var hasTitle = keys.indexOf('title') >= 0;
    var hasSubtitle = keys.indexOf('subtitle') >= 0 || keys.indexOf('subheadline') >= 0;
    var hasBadge = keys.indexOf('badge') >= 0;
    if (hasTop && (hasTitle || hasSubtitle)) return '顶部文案 / 主副标题';
    if (hasTitle && hasSubtitle) return '主副标题';
    if (hasBadge && hasTitle) return '标签 / 主标题';
    if (hasTop) return '顶部文案';
    if (hasTitle) return '主标题';
    if (hasBadge) return '标签';
    return '模板文案';
  }

  function readOverlayTexts() {
    var out = {};
    document.querySelectorAll('#cutcliTplModalOverlayFields [data-cutcli-overlay-key]').forEach(function(input) {
      var key = String(input.dataset.cutcliOverlayKey || '').trim();
      if (!key) return;
      var limit = overlayInputLimit(input);
      var value = limit ? truncateChars(input.value || '', limit) : String(input.value || '');
      if (input.value !== value) input.value = value;
      updateOverlayCounter(input);
      out[key] = String(value || '').trim();
    });
    state.overlayTexts = out;
    return out;
  }

  function charCount(value) {
    return Array.from(String(value || '')).length;
  }

  function truncateChars(value, limit) {
    var n = parseInt(limit || 0, 10);
    if (!n || n < 1) return String(value || '');
    return Array.from(String(value || '')).slice(0, n).join('');
  }

  function overlayInputLimit(input) {
    var raw = input ? (input.dataset.cutcliOverlayMax || input.getAttribute('maxlength') || '') : '';
    var n = parseInt(raw || '0', 10);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  function updateOverlayCounter(input) {
    if (!input) return;
    var limit = overlayInputLimit(input);
    if (limit) {
      var next = truncateChars(input.value || '', limit);
      if (input.value !== next) input.value = next;
    }
    var count = charCount(input.value || '');
    var counter = input.closest ? input.closest('.cutcli-template-overlay-field') : null;
    counter = counter ? counter.querySelector('[data-cutcli-overlay-count]') : null;
    if (counter) {
      counter.textContent = limit ? (count + '/' + limit) : String(count);
      counter.classList.toggle('is-full', !!limit && count >= limit);
    }
  }

  function bindOverlayFieldCounters(root) {
    if (!root) return;
    root.querySelectorAll('[data-cutcli-overlay-key]').forEach(function(input) {
      updateOverlayCounter(input);
      input.addEventListener('input', function() {
        updateOverlayCounter(input);
        var key = String(input.dataset.cutcliOverlayKey || '').trim();
        if (key) {
          state.overlayTexts = state.overlayTexts || {};
          state.overlayTexts[key] = input.value || '';
          renderPositionLayer(modalTemplate());
        }
      });
    });
  }

  function statusLabel(status) {
    var value = String(status || '').toLowerCase();
    if (value === 'completed' || value === 'success') return '完成';
    if (value === 'failed' || value === 'error') return '失败';
    if (value === 'running' || value === 'queued' || value === 'pending') return '处理中';
    return status || '未知';
  }

  function formatTime(ts) {
    var n = Number(ts || 0);
    if (!n) return '-';
    var d = new Date(n * 1000);
    if (Number.isNaN(d.getTime())) return '-';
    return d.toLocaleString('zh-CN', { hour12: false });
  }

  function setModalMsg(text, isErr) {
    var el = $('cutcliTplModalMsg');
    if (!el) return;
    el.style.display = text ? '' : 'none';
    el.className = 'msg' + (isErr ? ' err' : '');
    el.textContent = text || '';
  }

  function setHistoryMsg(text, isErr) {
    var el = $('cutcliTplHistoryMsg');
    if (!el) return;
    el.style.display = text ? '' : 'none';
    el.className = 'msg' + (isErr ? ' err' : '');
    el.textContent = text || '';
  }

  function stopPolling() {
    if (!state.pollTimer) return;
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  function isStudioVisible() {
    var host = $('content-cutcli-template-studio');
    if (!host || host.hidden) return false;
    var style = window.getComputedStyle ? window.getComputedStyle(host) : null;
    if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
    return true;
  }

  function shouldPollJobs() {
    return state.activeTab === 'jobs' && isStudioVisible();
  }

  function normalizeJob(data) {
    data = data || {};
    var tpl = data.template && typeof data.template === 'object' ? data.template : {};
    var quality = data.quality && typeof data.quality === 'object' ? data.quality : {};
    var jobId = data.job_id || '';
    return {
      job_id: jobId,
      status: data.status || (data.preview_url || data.open_url ? 'completed' : ''),
      stage: data.stage || '',
      template_id: data.template_id || tpl.id || '',
      template_name: data.template_name || tpl.name || '',
      source_asset_id: data.source_asset_id || '',
      source_name: data.source_name || '',
      preview_asset_id: data.preview_asset_id || data.final_asset_id || '',
      preview_url: data.preview_url || data.open_url || '',
      open_url: data.open_url || data.preview_url || '',
      caption_count: data.caption_count || quality.caption_count || 0,
      render_strategy: data.render_strategy || '',
      error: data.error || '',
      error_code: data.error_code || '',
      created_at: data.created_at || 0,
      updated_at: data.updated_at || Math.floor(Date.now() / 1000),
      poll_path: data.poll_path || (jobId ? '/api/cutcli/local/templates/jobs/' + encodeURIComponent(jobId) : '')
    };
  }

  function upsertJob(data) {
    var record = normalizeJob(data);
    if (!record.job_id) return;
    var found = false;
    state.jobs = state.jobs.map(function(item) {
      if (item.job_id !== record.job_id) return item;
      found = true;
      return Object.assign({}, item, record);
    });
    if (!found) state.jobs.unshift(record);
    state.jobs.sort(function(a, b) {
      return Number(b.updated_at || b.created_at || 0) - Number(a.updated_at || a.created_at || 0);
    });
  }

  function ensureHost() {
    var host = $('content-cutcli-template-studio');
    if (!host) return null;
    if (host.dataset.ready === '1') return host;
    host.dataset.ready = '1';
    host.innerHTML = [
      '<div class="tvc-studio cutcli-template-studio">',
        '<div class="tvc-studio-hero cutcli-template-hero">',
          '<div>',
            '<h3>模板定制</h3>',
            '<p>选择模板，预览样片，生成同款视频。</p>',
            '<div class="tvc-hero-meta">服务端生成 · 原片比例 · 自动入库</div>',
          '</div>',
          '<div class="tvc-hero-actions">',
            '<button type="button" id="cutcliTplBackBtn" class="btn btn-ghost btn-sm">返回</button>',
            '<button type="button" id="cutcliTplRefreshBtn" class="btn btn-ghost btn-sm">刷新</button>',
          '</div>',
        '</div>',
        '<div class="cutcli-template-tabs seedance-view-tabs">',
          '<button type="button" class="cutcli-template-tab seedance-view-tab is-active" data-cutcli-tab="templates">模板</button>',
          '<button type="button" class="cutcli-template-tab seedance-view-tab" data-cutcli-tab="jobs">生成记录</button>',
        '</div>',
        '<section id="cutcliTplTemplatesTab" class="cutcli-template-tab-panel">',
          '<div class="cutcli-template-toolbar">',
            '<div>',
              '<h4>模板</h4>',
              '<p id="cutcliTplCount">加载中</p>',
            '</div>',
          '</div>',
          '<div id="cutcliTplGrid" class="cutcli-template-grid"></div>',
        '</section>',
        '<section id="cutcliTplJobsTab" class="cutcli-template-tab-panel" hidden>',
          '<div class="cutcli-template-toolbar">',
            '<div>',
              '<h4>生成记录</h4>',
              '<p id="cutcliTplJobsCount">最近任务</p>',
            '</div>',
            '<button type="button" id="cutcliTplJobsRefreshBtn" class="btn btn-ghost btn-sm">刷新记录</button>',
          '</div>',
          '<div id="cutcliTplHistoryMsg" class="msg" style="display:none;margin-bottom:0.75rem;"></div>',
          '<div id="cutcliTplJobsGrid" class="cutcli-template-jobs-grid"></div>',
        '</section>',
        '<div id="cutcliTplModal" class="cutcli-template-modal-mask" aria-hidden="true">',
          '<div class="cutcli-template-modal" role="dialog" aria-modal="true" aria-labelledby="cutcliTplModalTitle">',
            '<div class="cutcli-template-modal-head">',
              '<div>',
                '<h4 id="cutcliTplModalTitle">模板</h4>',
                '<p id="cutcliTplModalSub">生成同款</p>',
              '</div>',
              '<button type="button" id="cutcliTplModalCloseBtn" class="cutcli-template-modal-close" aria-label="关闭">×</button>',
            '</div>',
            '<div class="cutcli-template-modal-grid">',
              '<div id="cutcliTplModalPreview" class="cutcli-template-modal-preview"></div>',
              '<div class="cutcli-template-modal-form">',
                '<div id="cutcliTplModalMeta" class="cutcli-template-modal-meta"></div>',
                '<div id="cutcliTplModalOverlayTitle" class="cutcli-template-source-title">模板文案</div>',
                '<div id="cutcliTplModalOverlayFields" class="cutcli-template-overlay-fields"></div>',
                '<div class="cutcli-template-source-title">视频来源</div>',
                '<div class="tvc-upload-box cutcli-template-upload">',
                  '<div class="tvc-upload-actions">',
                    '<button type="button" id="cutcliTplModalPickBtn" class="btn btn-primary btn-sm">上传视频</button>',
                    '<button type="button" id="cutcliTplModalClearFileBtn" class="btn btn-ghost btn-sm">清空</button>',
                    '<input type="file" id="cutcliTplModalFileInput" accept="video/*" style="display:none;">',
                  '</div>',
                  '<div id="cutcliTplModalFileName" class="tvc-panel-hint">未选择本地视频。</div>',
                '</div>',
                '<label class="cutcli-template-field" for="cutcliTplModalAssetIdInput">',
                  '<span>素材 ID</span>',
                  '<input id="cutcliTplModalAssetIdInput" type="text" placeholder="粘贴视频素材 ID">',
                '</label>',
                '<div class="cutcli-template-source-title">渲染方式</div>',
                '<div class="cutcli-template-render-mode" role="group" aria-label="渲染方式">',
                  '<button type="button" class="cutcli-template-render-option is-active" data-cutcli-render-mode="ffmpeg">本机 FFmpeg</button>',
                  '<button type="button" class="cutcli-template-render-option" data-cutcli-render-mode="cutcli_cloud">cutcli 云渲染</button>',
                '</div>',
                '<div id="cutcliTplModalMsg" class="msg" style="display:none;margin-top:0.75rem;"></div>',
                '<div class="cutcli-template-modal-actions">',
                  '<button type="button" id="cutcliTplModalStartBtn" class="btn btn-primary">生成同款</button>',
                  '<button type="button" id="cutcliTplModalCancelBtn" class="btn btn-ghost">取消</button>',
                '</div>',
              '</div>',
            '</div>',
          '</div>',
        '</div>',
      '</div>'
    ].join('');
    bind();
    return host;
  }

  function openView() {
    ensureHost();
    bindVideoPreviewEvents();
    if (typeof _switchToHiddenView === 'function') _switchToHiddenView('cutcli-template-studio');
    else if (typeof showContent === 'function') showContent('cutcli-template-studio');
    var store = document.getElementById('skillStoreSection');
    if (store) store.style.display = 'none';
    try { location.hash = 'cutcli-template-studio'; } catch (err) {}
    activateTab(state.activeTab || 'templates');
    loadTemplates();
  }

  function activateTab(tab) {
    state.activeTab = tab === 'jobs' ? 'jobs' : 'templates';
    var templatesPanel = $('cutcliTplTemplatesTab');
    var jobsPanel = $('cutcliTplJobsTab');
    if (templatesPanel) templatesPanel.hidden = state.activeTab !== 'templates';
    if (jobsPanel) jobsPanel.hidden = state.activeTab !== 'jobs';
    document.querySelectorAll('#content-cutcli-template-studio .cutcli-template-tab').forEach(function(btn) {
      btn.classList.toggle('is-active', btn.dataset.cutcliTab === state.activeTab);
    });
    if (state.activeTab === 'jobs') loadJobs(false);
    else stopPolling();
  }

  function loadTemplates() {
    var grid = $('cutcliTplGrid');
    if (grid) grid.innerHTML = '<div class="cutcli-template-empty">模板加载中...</div>';
    fetch(apiBase() + '/api/cutcli/templates', { headers: headers() })
      .then(parseJsonResponse)
      .then(function(data) {
        state.templates = Array.isArray(data.templates) ? data.templates : [];
        renderTemplateGrid();
      })
      .catch(function(err) {
        if (grid) grid.innerHTML = '<div class="cutcli-template-empty">模板加载失败：' + esc(err.message || err) + '</div>';
      });
  }

  function renderTemplateGrid() {
    var grid = $('cutcliTplGrid');
    var count = $('cutcliTplCount');
    if (count) count.textContent = state.templates.length ? (state.templates.length + ' 个模板') : '暂无模板';
    if (!grid) return;
    if (!state.templates.length) {
      grid.innerHTML = '<div class="cutcli-template-empty">暂无模板。</div>';
      return;
    }
    grid.innerHTML = state.templates.map(function(tpl) {
      var media = templateMedia(tpl);
      var tags = (tpl.tags || []).slice(0, 3).map(function(tag) {
        return '<span class="tag">' + esc(tag) + '</span>';
      }).join('');
      return [
        '<article class="cutcli-template-card" data-cutcli-template-id="' + esc(tpl.id) + '">',
          '<div class="cutcli-template-card-media">',
            media
              ? videoShellHtml(media, { card: true, muted: true, loop: true })
              : '<div class="cutcli-template-media-empty">暂无样片</div>',
            '<div class="cutcli-template-card-top">',
              '<span>模板</span>',
              '<span>' + esc(tpl.aspect_ratio === 'source' ? '原片比例' : (tpl.aspect_ratio || '模板比例')) + '</span>',
            '</div>',
            media ? '<button type="button" class="cutcli-template-play-mark" data-cutcli-template-preview aria-label="预览模板">▶</button>' : '',
          '</div>',
          '<div class="cutcli-template-card-body">',
            '<div class="cutcli-template-card-row">',
              '<strong>' + esc(tpl.name || tpl.id) + '</strong>',
              '<span>#' + esc(tpl.id || DEFAULT_TEMPLATE_ID) + '</span>',
            '</div>',
            '<p>' + esc(tpl.description || '') + '</p>',
            '<div class="card-tags">' + tags + '</div>',
            '<button type="button" class="btn btn-primary btn-sm cutcli-template-card-use" data-cutcli-template-use>做同款</button>',
          '</div>',
        '</article>'
      ].join('');
    }).join('');

    grid.querySelectorAll('[data-cutcli-template-id]').forEach(function(card) {
      var video = card.querySelector('video');
      if (video) {
        card.addEventListener('mouseenter', function() { video.play().catch(function() {}); });
        card.addEventListener('mouseleave', function() {
          video.pause();
          try { video.currentTime = 0; } catch (err) {}
        });
      }
      var previewBtn = card.querySelector('[data-cutcli-template-preview]');
      if (previewBtn) {
        previewBtn.addEventListener('click', function(evt) {
          evt.preventDefault();
          evt.stopPropagation();
          var shell = card.querySelector('[data-cutcli-video-shell]');
          var video = shell ? shell.querySelector('video') : null;
          if (video) {
            video.controls = true;
            video.muted = false;
            video.play().catch(function() {});
          }
          function restoreCardPreviewControls() {
            if (fullscreenElement()) return;
            if (video) {
              video.controls = false;
              video.muted = true;
              video.pause();
            }
            document.removeEventListener('fullscreenchange', restoreCardPreviewControls);
            document.removeEventListener('webkitfullscreenchange', restoreCardPreviewControls);
            document.removeEventListener('MSFullscreenChange', restoreCardPreviewControls);
          }
          document.addEventListener('fullscreenchange', restoreCardPreviewControls);
          document.addEventListener('webkitfullscreenchange', restoreCardPreviewControls);
          document.addEventListener('MSFullscreenChange', restoreCardPreviewControls);
          requestFullscreenElement(shell).then(syncVideoFullscreenState).catch(restoreCardPreviewControls);
        });
      }
      var useBtn = card.querySelector('[data-cutcli-template-use]');
      if (useBtn) {
        useBtn.addEventListener('click', function(evt) {
          evt.preventDefault();
          evt.stopPropagation();
          openTemplateModal(card.dataset.cutcliTemplateId || '');
        });
      }
    });
  }

  function openTemplateModal(templateId) {
    var tpl = templateById(templateId) || state.templates[0];
    if (!tpl) return;
    state.modalTemplateId = tpl.id;
    setUploadedFile(null);
    state.overlayTexts = overlayDefaults(tpl);
    state.positionOverrides = defaultPositionOverrides(tpl);
    var fileInput = $('cutcliTplModalFileInput');
    var fileName = $('cutcliTplModalFileName');
    var assetInput = $('cutcliTplModalAssetIdInput');
    if (fileInput) fileInput.value = '';
    if (fileName) fileName.textContent = '未选择本地视频。';
    if (assetInput) assetInput.value = '';
    var overlayBox = $('cutcliTplModalOverlayFields');
    if (overlayBox) overlayBox.innerHTML = '';
    state.renderMode = 'ffmpeg';
    updateRenderModeButtons();
    setModalMsg('', false);
    renderTemplateModal();
    var modal = $('cutcliTplModal');
    if (modal) {
      modal.classList.add('is-open');
      modal.setAttribute('aria-hidden', 'false');
    }
    document.body.classList.add('cutcli-template-modal-open');
  }

  function closeTemplateModal() {
    var modal = $('cutcliTplModal');
    if (modal) {
      modal.classList.remove('is-open');
      modal.setAttribute('aria-hidden', 'true');
    }
    var video = modal ? modal.querySelector('video') : null;
    if (video) video.pause();
    revokeUploadedPreview();
    document.body.classList.remove('cutcli-template-modal-open');
  }

  function renderTemplateModal() {
    var tpl = modalTemplate();
    if (!tpl) return;
    var media = state.uploadedPreviewUrl || '';
    var title = $('cutcliTplModalTitle');
    var sub = $('cutcliTplModalSub');
    var preview = $('cutcliTplModalPreview');
    var meta = $('cutcliTplModalMeta');
    var overlayBox = $('cutcliTplModalOverlayFields');
    var overlayTitle = $('cutcliTplModalOverlayTitle');
    if (title) title.textContent = tpl.name || tpl.id || '模板';
    if (sub) sub.textContent = tpl.quality_label || '生成同款';
    if (preview) {
      var hasSourcePreview = !!media;
      var orientationClass = state.sourceVideoOrientation === 'portrait' ? ' is-portrait' : ' is-landscape';
      var posterStyle = state.uploadedPosterUrl
        ? ' style="background-image:url(' + esc(state.uploadedPosterUrl) + ');"'
        : '';
      preview.innerHTML = [
        '<div class="cutcli-template-position-editor' + orientationClass + (hasSourcePreview ? '' : ' is-empty') + (state.sourceVideoFallback ? ' use-poster-fallback' : '') + '"' + posterStyle + '>',
          media
            ? videoShellHtml(media, { modal: true, controls: true, muted: true, loop: true, poster: state.uploadedPosterUrl || '' })
            : '<div class="cutcli-template-media-empty cutcli-template-source-empty">请先上传视频</div>',
          hasSourcePreview ? '<button type="button" class="cutcli-template-position-reset" id="cutcliTplResetPositionsBtn">\u91cd\u7f6e\u4f4d\u7f6e</button>' : '',
          hasSourcePreview ? '<div id="cutcliTplPositionLayer" class="cutcli-template-position-layer" aria-label="\u62d6\u52a8\u4fee\u6539\u6587\u6848\u4f4d\u7f6e"></div>' : '',
        '</div>',
      ].join('');
      var video = preview.querySelector('video');
      if (video) {
        video.currentTime = 0;
        video.pause();
        bindSourceVideoFallback(video);
      }
      if (hasSourcePreview) {
        renderPositionLayer(tpl);
        bindPositionReset(tpl);
      }
    }
    if (meta) {
      var tags = (tpl.tags || []).map(function(tag) {
        return '<span class="tag">' + esc(tag) + '</span>';
      }).join('');
      meta.innerHTML = [
        '<div class="cutcli-template-selected-meta">',
          '<span>' + esc(tpl.aspect_ratio === 'source' ? '原片比例' : (tpl.aspect_ratio || '模板比例')) + '</span>',
          '<span>' + esc(tpl.quality_label || '生成同款') + '</span>',
        '</div>',
        tags ? '<div class="card-tags">' + tags + '</div>' : ''
      ].join('');
    }
    var fields = overlayFields(tpl);
    if (overlayTitle) {
      overlayTitle.textContent = overlaySectionTitle(fields);
      overlayTitle.hidden = !fields.length;
    }
    if (overlayBox) {
      overlayBox.hidden = !fields.length;
      overlayBox.innerHTML = fields.length ? fields.map(function(field) {
        var key = String((field && field.key) || '').trim();
        if (!key) return '';
        var value = state.overlayTexts[key] != null ? state.overlayTexts[key] : String((field && field.default) || '');
        var label = String((field && field.label) || key);
        var placeholder = String((field && field.placeholder) || '');
        var maxLength = parseInt((field && field.max_length) || 80, 10);
        var multiline = !!(field && field.multiline);
        value = truncateChars(value, maxLength);
        var common = ' id="cutcliTplOverlay_' + esc(key) + '" data-cutcli-overlay-key="' + esc(key) + '" data-cutcli-overlay-max="' + esc(maxLength) + '" maxlength="' + esc(maxLength) + '" placeholder="' + esc(placeholder) + '"';
        var input = multiline
          ? '<textarea' + common + ' rows="2">' + esc(value) + '</textarea>'
          : '<input' + common + ' type="text" value="' + esc(value) + '">';
        var head = '<span class="cutcli-template-field-head"><span>' + esc(label) + '</span><em data-cutcli-overlay-count="' + esc(key) + '">' + esc(charCount(value)) + '/' + esc(maxLength) + '</em></span>';
        return '<label class="cutcli-template-field cutcli-template-overlay-field">' + head + input + '</label>';
      }).join('') : '';
      bindOverlayFieldCounters(overlayBox);
    }
  }

  function updateRenderModeButtons() {
    document.querySelectorAll('#content-cutcli-template-studio [data-cutcli-render-mode]').forEach(function(btn) {
      var active = (btn.dataset.cutcliRenderMode || 'ffmpeg') === (state.renderMode || 'ffmpeg');
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  function loadJobs(showLoading) {
    var grid = $('cutcliTplJobsGrid');
    if (state.activeTab !== 'jobs') {
      stopPolling();
      return;
    }
    stopPolling();
    if (showLoading && grid && grid.querySelector('[data-cutcli-job-id]')) {
      showLoading = false;
      setHistoryMsg('\u6b63\u5728\u540e\u53f0\u5237\u65b0\u8bb0\u5f55...', false);
    }
    if (showLoading && grid) grid.innerHTML = '<div class="cutcli-template-empty">生成记录加载中...</div>';
    fetch(localApiBase() + '/api/cutcli/local/templates/jobs?limit=50', { headers: headers() })
      .then(parseJsonResponse)
      .then(function(data) {
        state.jobs = Array.isArray(data.jobs) ? data.jobs.map(normalizeJob) : [];
        renderJobs();
        setHistoryMsg('', false);
        var running = state.jobs.filter(function(job) {
          return ['running', 'queued', 'pending'].indexOf(String(job.status || '').toLowerCase()) >= 0;
        })[0];
        if (running && shouldPollJobs()) pollJob(running.job_id, true);
        else stopPolling();
      })
      .catch(function(err) {
        if (grid && showLoading) grid.innerHTML = '<div class="cutcli-template-empty">生成记录加载失败：' + esc(err.message || err) + '</div>';
      });
  }

  function jobMedia(job) {
    return resolveUrl((job && (job.preview_url || job.open_url)) || '', localApiBase());
  }

  function jobStatusInfo(job) {
    var status = String((job && job.status) || '').toLowerCase();
    if (status === 'completed' || status === 'success') {
      return { tone: 'success', text: '\u5b8c\u6210', done: true, failed: false };
    }
    if (status === 'failed' || status === 'error') {
      return { tone: 'danger', text: '\u5931\u8d25', done: false, failed: true };
    }
    if (status === 'running' || status === 'queued' || status === 'pending') {
      return { tone: 'processing', text: '\u5904\u7406\u4e2d', done: false, failed: false };
    }
    return { tone: 'processing', text: status || '\u672a\u77e5', done: false, failed: false };
  }

  function jobPlaceholderHtml(job) {
    var info = jobStatusInfo(job);
    return '<div class="cutcli-template-job-placeholder">' + esc(info.failed ? '\u4efb\u52a1\u5931\u8d25' : '\u5904\u7406\u4e2d') + '</div>';
  }

  function jobActionHtml(job) {
    var media = jobMedia(job);
    if (media) {
      return '<a class="btn btn-primary btn-sm" href="' + esc(media) + '" target="_blank" rel="noopener">\u6253\u5f00\u7ed3\u679c</a>';
    }
    return '<button type="button" class="btn btn-ghost btn-sm" data-cutcli-poll-job="' + esc(job.job_id || '') + '">\u5237\u65b0</button>';
  }

  function jobCardHtml(job) {
    var info = jobStatusInfo(job);
    var media = jobMedia(job);
    var captionText = job.caption_count ? (job.caption_count + ' \u6761\u5b57\u5e55') : (job.render_strategy || '');
    var errorText = job.error_code ? (job.error_code + ': ' + job.error) : (job.error || '');
    return [
      '<article class="cutcli-template-job-card" data-cutcli-job-id="' + esc(job.job_id || '') + '" data-status="' + esc(info.tone) + '" data-preview-url="' + esc(media) + '">',
        '<div class="cutcli-template-job-media">',
          '<div class="cutcli-template-job-visual">',
            media
              ? videoShellHtml(media, { job: true, controls: true })
              : jobPlaceholderHtml(job),
          '</div>',
          '<div class="cutcli-template-card-top">',
            '<span class="cutcli-template-status" data-tone="' + esc(info.tone) + '">' + esc(info.text) + '</span>',
            '<span class="cutcli-template-job-time">' + esc(formatTime(job.updated_at || job.created_at)) + '</span>',
          '</div>',
        '</div>',
        '<div class="cutcli-template-card-body">',
          '<div class="cutcli-template-card-row">',
            '<strong class="cutcli-template-job-title">' + esc(job.template_name || job.template_id || '\u6a21\u677f\u4efb\u52a1') + '</strong>',
            '<span class="cutcli-template-job-caption-count">' + esc(captionText) + '</span>',
          '</div>',
          '<p class="cutcli-template-job-id">#' + esc(job.job_id || '-') + '</p>',
          '<div class="cutcli-template-job-error"' + (errorText ? '' : ' hidden') + '>' + esc(errorText) + '</div>',
          '<div class="cutcli-template-job-actions">' + jobActionHtml(job) + '</div>',
        '</div>',
      '</article>'
    ].join('');
  }

  function findJobCard(grid, jobId) {
    if (!grid || !jobId) return null;
    var cards = grid.querySelectorAll('[data-cutcli-job-id]');
    for (var i = 0; i < cards.length; i += 1) {
      if (cards[i].getAttribute('data-cutcli-job-id') === jobId) return cards[i];
    }
    return null;
  }

  function bindJobCard(card) {
    if (!card) return;
    card.querySelectorAll('[data-cutcli-poll-job]').forEach(function(btn) {
      if (btn.dataset.cutcliBound === '1') return;
      btn.dataset.cutcliBound = '1';
      btn.addEventListener('click', function() {
        activateTab('jobs');
        pollJob(btn.dataset.cutcliPollJob || '', false);
      });
    });
  }

  function setText(selector, root, text) {
    var el = root ? root.querySelector(selector) : null;
    if (el) el.textContent = text || '';
  }

  function patchJobMedia(card, job) {
    var visual = card.querySelector('.cutcli-template-job-visual');
    if (!visual) return;
    var media = jobMedia(job);
    var current = card.getAttribute('data-preview-url') || '';
    if (media === current) {
      if (!media) visual.innerHTML = jobPlaceholderHtml(job);
      return;
    }
    if (!media && current) return;
    visual.innerHTML = media
      ? videoShellHtml(media, { job: true, controls: true })
      : jobPlaceholderHtml(job);
    card.setAttribute('data-preview-url', media);
  }

  function patchJobCard(card, job) {
    var info = jobStatusInfo(job);
    var statusEl = card.querySelector('.cutcli-template-status');
    var captionText = job.caption_count ? (job.caption_count + ' \u6761\u5b57\u5e55') : (job.render_strategy || '');
    var errorText = job.error_code ? (job.error_code + ': ' + job.error) : (job.error || '');
    card.setAttribute('data-status', info.tone);
    if (statusEl) {
      statusEl.setAttribute('data-tone', info.tone);
      statusEl.textContent = info.text;
    }
    setText('.cutcli-template-job-time', card, formatTime(job.updated_at || job.created_at));
    setText('.cutcli-template-job-title', card, job.template_name || job.template_id || '\u6a21\u677f\u4efb\u52a1');
    setText('.cutcli-template-job-caption-count', card, captionText);
    setText('.cutcli-template-job-id', card, '#' + (job.job_id || '-'));
    var errorEl = card.querySelector('.cutcli-template-job-error');
    if (errorEl) {
      errorEl.hidden = !errorText;
      errorEl.textContent = errorText;
    }
    var actions = card.querySelector('.cutcli-template-job-actions');
    if (actions) actions.innerHTML = jobActionHtml(job);
    patchJobMedia(card, job);
    bindJobCard(card);
  }

  function renderJobsInPlace() {
    var grid = $('cutcliTplJobsGrid');
    var count = $('cutcliTplJobsCount');
    if (count) count.textContent = state.jobs.length ? (state.jobs.length + ' \u6761\u8bb0\u5f55') : '\u6682\u65e0\u8bb0\u5f55';
    if (!grid) return;
    if (!state.jobs.length) {
      if (!grid.querySelector('[data-cutcli-job-id]')) {
        grid.innerHTML = '<div class="cutcli-template-empty">\u6682\u65e0\u751f\u6210\u8bb0\u5f55\u3002</div>';
      }
      return;
    }
    if (!grid.querySelector('[data-cutcli-job-id]')) {
      grid.innerHTML = state.jobs.map(jobCardHtml).join('');
      grid.querySelectorAll('[data-cutcli-job-id]').forEach(bindJobCard);
      return;
    }
    state.jobs.forEach(function(job) {
      var card = findJobCard(grid, job.job_id);
      if (!card) {
        var holder = document.createElement('div');
        holder.innerHTML = jobCardHtml(job);
        card = holder.firstElementChild;
        if (card) {
          grid.appendChild(card);
          bindJobCard(card);
        }
        return;
      }
      patchJobCard(card, job);
    });
  }

  function renderJobs() {
    renderJobsInPlace();
  }

  function renderJobsLegacyDisabled() {
    return;
    var grid = $('cutcliTplJobsGrid');
    var count = $('cutcliTplJobsCount');
    if (count) count.textContent = state.jobs.length ? (state.jobs.length + ' 条记录') : '暂无记录';
    if (!grid) return;
    if (!state.jobs.length) {
      grid.innerHTML = '<div class="cutcli-template-empty">暂无生成记录。</div>';
      return;
    }
    grid.innerHTML = state.jobs.map(function(job) {
      var status = String(job.status || '').toLowerCase();
      var isDone = status === 'completed' || status === 'success';
      var isFailed = status === 'failed' || status === 'error';
      var media = job.preview_url || job.open_url || '';
      var tone = isDone ? 'success' : (isFailed ? 'danger' : 'processing');
      var action = media
        ? '<a class="btn btn-primary btn-sm" href="' + esc(media) + '" target="_blank" rel="noopener">打开结果</a>'
        : '<button type="button" class="btn btn-ghost btn-sm" data-cutcli-poll-job="' + esc(job.job_id) + '">刷新</button>';
      return [
        '<article class="cutcli-template-job-card" data-status="' + esc(tone) + '">',
          '<div class="cutcli-template-job-media">',
            media
              ? '<video src="' + esc(media) + '" controls playsinline preload="metadata"></video>'
              : '<div class="cutcli-template-job-placeholder">' + esc(isFailed ? '任务失败' : '处理中') + '</div>',
            '<div class="cutcli-template-card-top">',
              '<span class="cutcli-template-status" data-tone="' + esc(tone) + '">' + esc(statusLabel(job.status)) + '</span>',
              '<span>' + esc(formatTime(job.updated_at || job.created_at)) + '</span>',
            '</div>',
          '</div>',
          '<div class="cutcli-template-card-body">',
            '<div class="cutcli-template-card-row">',
              '<strong>' + esc(job.template_name || job.template_id || '模板任务') + '</strong>',
              '<span>' + esc(job.caption_count ? (job.caption_count + ' 条字幕') : (job.render_strategy || '')) + '</span>',
            '</div>',
            '<p>#' + esc(job.job_id || '-') + '</p>',
            job.error ? '<div class="cutcli-template-job-error">' + esc(job.error_code ? (job.error_code + '：' + job.error) : job.error) + '</div>' : '',
            '<div class="cutcli-template-job-actions">' + action + '</div>',
          '</div>',
        '</article>'
      ].join('');
    }).join('');
    grid.querySelectorAll('[data-cutcli-poll-job]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        pollJob(btn.dataset.cutcliPollJob || '', false);
      });
    });
  }

  function setBusy(busy) {
    state.busy = !!busy;
    var btn = $('cutcliTplModalStartBtn');
    if (!btn) return;
    btn.disabled = state.busy;
    btn.textContent = state.busy ? '提交中...' : '生成同款';
  }

  function startRender() {
    if (state.busy) return;
    var tpl = modalTemplate();
    if (!tpl) {
      setModalMsg('请选择模板。', true);
      return;
    }
    var assetInput = ($('cutcliTplModalAssetIdInput') && $('cutcliTplModalAssetIdInput').value || '').trim();
    if (!state.uploadedFile && !assetInput) {
      setModalMsg('请上传一个视频，或填写视频素材 ID。', true);
      return;
    }
    var fd = new FormData();
    if (assetInput) fd.append('asset_id', assetInput);
    if (state.uploadedFile) fd.append('file', state.uploadedFile, state.uploadedFile.name || 'source.mp4');

    setBusy(true);
    setModalMsg('正在提交服务端...', false);
    var renderPath = tpl.render_path || ('/api/cutcli/templates/' + encodeURIComponent(tpl.id || DEFAULT_TEMPLATE_ID) + '/render');
    fetch(apiBase() + renderPath, {
      method: 'POST',
      headers: formHeaders(),
      body: fd
    })
      .then(parseJsonResponse)
      .then(function(data) {
        upsertJob(data);
        renderJobs();
        closeTemplateModal();
        activateTab('jobs');
        setHistoryMsg('任务已提交。', false);
        if (data.job_id) pollJob(data.job_id, false);
      })
      .catch(function(err) {
        setModalMsg('生成失败：' + (err.message || err), true);
      })
      .finally(function() {
        setBusy(false);
      });
  }

  startRender = function() {
    if (state.busy) return;
    var tpl = modalTemplate();
    if (!tpl) {
      setModalMsg('\u8bf7\u9009\u62e9\u6a21\u677f\u3002', true);
      return;
    }
    var assetInput = ($('cutcliTplModalAssetIdInput') && $('cutcliTplModalAssetIdInput').value || '').trim();
    if (!state.uploadedFile && !assetInput) {
      setModalMsg('\u8bf7\u4e0a\u4f20\u4e00\u4e2a\u89c6\u9891\uff0c\u6216\u586b\u5199\u89c6\u9891\u7d20\u6750 ID\u3002', true);
      return;
    }

    setBusy(true);
    setModalMsg(state.uploadedFile ? '\u6b63\u5728\u4fdd\u5b58\u5230\u672c\u673a\u7d20\u6750\u5e93...' : '\u6b63\u5728\u63d0\u4ea4\u672c\u673a\u4efb\u52a1...', false);
    var prepare = state.uploadedFile
      ? uploadVideoLocally(state.uploadedFile)
      : Promise.resolve({ asset_id: assetInput, video_url: '' });

    prepare
      .then(function(source) {
        var fd = new FormData();
        if (source.asset_id) fd.append('asset_id', source.asset_id);
        if (source.video_url && !source.asset_id) fd.append('video_url', source.video_url);
        fd.append('render_mode', state.renderMode || 'ffmpeg');
        var overlays = readOverlayTexts();
        overlayFields(tpl).forEach(function(field) {
          var key = String((field && field.key) || '').trim();
          if (key) fd.append(key, overlays[key] || '');
        });
        fd.append('position_overrides', JSON.stringify(state.positionOverrides || {}));
        setModalMsg('\u4efb\u52a1\u5df2\u8fdb\u5165\u672c\u673a\u6d41\u7a0b\uff1a\u63d0\u53d6\u97f3\u9891\u2192STT\u2192\u6e32\u67d3\u3002', false);
        return submitLocalRenderForm(tpl, fd);
      })
      .then(function(data) {
        upsertJob(data);
        renderJobs();
        closeTemplateModal();
        activateTab('jobs');
        setHistoryMsg('\u4efb\u52a1\u5df2\u63d0\u4ea4\u3002', false);
        if (data.job_id) pollJob(data.job_id, false);
      })
      .catch(function(err) {
        setModalMsg('\u751f\u6210\u5931\u8d25\uff1a' + (err.message || err), true);
      })
      .finally(function() {
        setBusy(false);
      });
  };

  function pollJob(jobId, silent) {
    if (!jobId) return;
    if (!shouldPollJobs()) {
      stopPolling();
      return;
    }
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    if (!silent) setHistoryMsg('正在刷新任务状态...', false);
    fetch(localApiBase() + '/api/cutcli/local/templates/jobs/' + encodeURIComponent(jobId), { headers: headers() })
      .then(parseJsonResponse)
      .then(function(data) {
        upsertJob(data);
        renderJobs();
        var status = String(data.status || '').toLowerCase();
        if (['completed', 'success'].indexOf(status) >= 0) {
          setHistoryMsg('生成完成，结果已入库。', false);
          return;
        }
        if (['failed', 'error'].indexOf(status) >= 0) {
          setHistoryMsg('任务失败：' + (data.error || data.error_code || '未知错误'), true);
          return;
        }
        setHistoryMsg('任务处理中：' + (data.stage || status || 'running'), false);
        if (shouldPollJobs()) {
          state.pollTimer = setTimeout(function() { pollJob(jobId, true); }, 3500);
        }
      })
      .catch(function(err) {
        setHistoryMsg('刷新失败：' + (err.message || err), true);
      });
  }

  function bind() {
    var back = $('cutcliTplBackBtn');
    if (back) back.addEventListener('click', function() {
      if (typeof showContent === 'function') showContent('skill');
    });
    var refresh = $('cutcliTplRefreshBtn');
    if (refresh) refresh.addEventListener('click', function() {
      loadTemplates();
      loadJobs(false);
    });
    var jobsRefresh = $('cutcliTplJobsRefreshBtn');
    if (jobsRefresh) jobsRefresh.addEventListener('click', function() { loadJobs(true); });
    document.querySelectorAll('#content-cutcli-template-studio [data-cutcli-tab]').forEach(function(btn) {
      btn.addEventListener('click', function() { activateTab(btn.dataset.cutcliTab || 'templates'); });
    });
    document.querySelectorAll('#content-cutcli-template-studio [data-cutcli-render-mode]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        state.renderMode = btn.dataset.cutcliRenderMode || 'ffmpeg';
        updateRenderModeButtons();
      });
    });

    var modal = $('cutcliTplModal');
    var closeBtn = $('cutcliTplModalCloseBtn');
    var cancelBtn = $('cutcliTplModalCancelBtn');
    if (closeBtn) closeBtn.addEventListener('click', closeTemplateModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeTemplateModal);
    if (modal) {
      modal.addEventListener('click', function(evt) {
        if (evt.target === modal) closeTemplateModal();
      });
    }
    document.addEventListener('keydown', function(evt) {
      if (evt.key !== 'Escape') return;
      if (fullscreenElement()) {
        evt.preventDefault();
        exitFullscreenElement().then(syncVideoFullscreenState).catch(function() {});
        return;
      }
      if (modal && modal.classList.contains('is-open')) closeTemplateModal();
    });

    var pick = $('cutcliTplModalPickBtn');
    var input = $('cutcliTplModalFileInput');
    if (pick && input) pick.addEventListener('click', function() { input.click(); });
    if (input) input.addEventListener('change', function() {
      setUploadedFile(input.files && input.files[0] ? input.files[0] : null);
      var name = $('cutcliTplModalFileName');
      if (name) name.textContent = state.uploadedFile ? ('已选择：' + state.uploadedFile.name) : '未选择本地视频。';
      if (state.uploadedFile) setModalMsg('', false);
      renderTemplateModal();
    });
    var clear = $('cutcliTplModalClearFileBtn');
    if (clear) clear.addEventListener('click', function() {
      setUploadedFile(null);
      if (input) input.value = '';
      var name = $('cutcliTplModalFileName');
      if (name) name.textContent = '未选择本地视频。';
      renderTemplateModal();
    });
    var start = $('cutcliTplModalStartBtn');
    if (start) start.addEventListener('click', startRender);

    document.addEventListener('visibilitychange', function() {
      if (document.hidden) stopPolling();
      else if (shouldPollJobs()) loadJobs(false);
    });
    window.addEventListener('hashchange', function() {
      if (!shouldPollJobs()) stopPolling();
    });
  }

  window.openCutcliTemplateStudio = openView;
  window._openCutcliTemplateStudioView = openView;
})();
