(function() {
  var state = {
    token: '',
    taskId: '',
    taskType: '',
    taskKindLabel: '未开始',
    pollTimer: null,
    submitting: false,
    activeView: 'avatar',
    avatarLibrary: { mine: [], public: [], mine_supported: true, mine_message: '', using_default_token: false, public_page: 1, public_page_size: 20, public_has_more: false, public_loading: false, public_visible: 20 },
    voiceLibrary: { mine: [], public: [], mine_supported: true, mine_message: '', using_default_token: false },
    videoHistory: [],
    videoCreateMode: 'tts',
    voicePreviewMap: {},
    uploadPreviews: {},
    selectedAvatar: null,
    selectedVoice: null,
    avatarSearch: '',
    voiceSearch: '',
    libraryExpanded: {
      avatarMine: false,
      avatarPublic: false,
      voiceMine: false,
      voicePublic: false
    }
  };

  var HIFLY_TEMPLATE_VERSION = '20260511-cloud-library-2';
  var HIFLY_STYLE_VERSION = '20260511-brand-voice-preview-3';



  function $(id) { return document.getElementById(id); }

  function baseUrl() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  }

  function cloudBaseUrl() {
    return (typeof API_BASE !== 'undefined' ? (API_BASE || '') : '').replace(/\/$/, '');
  }

  function authHeadersSafe() {
    if (typeof authHeaders === 'function') return Object.assign({}, authHeaders() || {});
    return {};
  }

  function escapeHtml(text) {
    return String(text || '').replace(/[&<>"]/g, function(ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch];
    });
  }

  var voicePreviewPlayer = null;
  var voicePreviewButton = null;

  function stopVoicePreview(exceptButton) {
    if (voicePreviewPlayer) {
      try {
        voicePreviewPlayer.pause();
        voicePreviewPlayer.currentTime = 0;
      } catch (e) {}
    }
    Array.prototype.forEach.call(document.querySelectorAll('.hifly-preview-play-btn.is-playing'), function(btn) {
      if (btn !== exceptButton) {
        btn.classList.remove('is-playing');
        btn.innerHTML = '<span class="hifly-preview-play-icon">▶</span><span>试听</span>';
      }
    });
    if (!exceptButton) voicePreviewButton = null;
  }

  function voicePreviewButtonHtml(url) {
    url = normalizeAssetUrl(url);
    if (!url) return '';
    return ''
      + '<button type="button" class="hifly-preview-play-btn" data-preview-url="' + escapeHtml(url) + '">'
      + '<span class="hifly-preview-play-icon">▶</span><span>试听</span>'
      + '</button>';
  }

  function normalizeAssetUrl(url) {
    var raw = String(url || '').trim();
    if (!raw) return '';
    if (/^(?:https?:)?\/\//i.test(raw) || /^data:/i.test(raw) || /^blob:/i.test(raw)) return raw;
    var base = baseUrl();
    if (!base) return raw;
    if (raw.charAt(0) === '/') return base + raw;
    return base + '/' + raw.replace(/^\.?\//, '');
  }

  function bindVoicePreviewButtons() {
    Array.prototype.forEach.call(document.querySelectorAll('.hifly-preview-play-btn'), function(btn) {
      btn.onclick = function(ev) {
        if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation();
        var url = btn.getAttribute('data-preview-url') || '';
        if (!url) return;
        if (voicePreviewButton === btn && voicePreviewPlayer && !voicePreviewPlayer.paused) {
          stopVoicePreview();
          return;
        }
        stopVoicePreview(btn);
        if (!voicePreviewPlayer) voicePreviewPlayer = new Audio();
        voicePreviewButton = btn;
        voicePreviewPlayer.src = url;
        btn.classList.add('is-playing');
        btn.innerHTML = '<span class="hifly-preview-play-icon">■</span><span>停止</span>';
        voicePreviewPlayer.onended = function() { stopVoicePreview(); };
        voicePreviewPlayer.onerror = function() {
          stopVoicePreview();
          showMessage('试听音频加载失败，请刷新后重试', true);
        };
        voicePreviewPlayer.play().catch(function() {
          stopVoicePreview();
          showMessage('试听音频播放失败，请刷新后重试', true);
        });
      };
    });
  }

  function tokenValue() {
    return '';
  }

  function refreshLobsterCredits() {
    if (typeof loadSutuiBalance === 'function') {
      try { loadSutuiBalance(); } catch (e) {}
    }
  }

  function billingSummaryText(billing, fallbackDuration) {
    billing = billing || {};
    var finalCredits = billing.credits_final;
    var preCredits = billing.credits_pre_deducted;
    var seconds = billing.actual_seconds || fallbackDuration || billing.estimated_seconds;
    if (finalCredits != null) return '实际时长 ' + (seconds || '--') + ' 秒，实际消耗 ' + finalCredits + ' 算力。';
    if (preCredits != null) return '已预扣 ' + preCredits + ' 算力，完成后按实际视频时长多退少补。';
    return '';
  }

  function requestTo(base, path, body) {
    var headers = Object.assign({ 'Content-Type': 'application/json' }, authHeadersSafe());
    return fetch(base + path, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(Object.assign({ token: tokenValue() }, body || {}))
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) {
          var msg = data.detail || data.error || data.message || '请求失败';
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        return data;
      });
    });
  }

  function request(path, body) {
    return requestTo(baseUrl(), path, body);
  }

  function requestCloud(path, body) {
    return requestTo(cloudBaseUrl(), path, body);
  }

  function requestFormTo(base, path, formData) {
    var headers = authHeadersSafe();
    delete headers['Content-Type'];
    return fetch(base + path, {
      method: 'POST',
      headers: headers,
      body: formData
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) {
          var msg = data.detail || data.error || data.message || '请求失败';
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        return data;
      });
    });
  }

  function requestForm(path, formData) {
    return requestFormTo(baseUrl(), path, formData);
  }

  function requestCloudForm(path, formData) {
    return requestFormTo(cloudBaseUrl(), path, formData);
  }

  function requestGetTo(base, path) {
    return fetch(base + path, {
      method: 'GET',
      headers: authHeadersSafe()
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) {
          var msg = data.detail || data.error || data.message || '获取失败';
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        return data;
      });
    });
  }

  function requestCloudGet(path) {
    return requestGetTo(cloudBaseUrl(), path);
  }

  function showMessage(text, isError) {
    var el = $('hiflyMsg');
    if (!el) return;
    el.textContent = text || '';
    el.style.display = text ? 'block' : 'none';
    el.classList.toggle('err', !!isError);
  }

  function getVideoCreateMode() {
    return state.videoCreateMode === 'audio' ? 'audio' : 'tts';
  }

  function setVideoCreateMode(mode) {
    state.videoCreateMode = mode === 'audio' ? 'audio' : 'tts';
    syncVideoCreateModeUI();
  }

  function getAudioDriveDurationSeconds() {
    var preview = $('hiflyAudioDrivePreview');
    if (!preview) return 0;
    var audio = preview.querySelector('audio');
    if (!audio) return 0;
    var duration = Number(audio.duration || 0);
    return isFinite(duration) && duration > 0 ? Math.ceil(duration) : 0;
  }

  function syncVideoCreateModeUI() {
    var mode = getVideoCreateMode();
    var ttsBtn = $('hiflyVideoModeTtsBtn');
    var audioBtn = $('hiflyVideoModeAudioBtn');
    var ttsFields = $('hiflyVideoTtsFields');
    var audioFields = $('hiflyVideoAudioFields');
    var subtitle = $('hiflySubtitleCheck');
    var subtitleHint = $('hiflySubtitleModeHint');
    var voicePanelHint = $('hiflySelectedVoiceModeHint');
    var voiceBtn = $('hiflyOpenVoiceLibraryBtn');

    if (ttsBtn) ttsBtn.classList.toggle('is-active', mode === 'tts');
    if (audioBtn) audioBtn.classList.toggle('is-active', mode === 'audio');
    if (ttsFields) ttsFields.style.display = mode === 'tts' ? 'block' : 'none';
    if (audioFields) audioFields.style.display = mode === 'audio' ? 'block' : 'none';

    if (subtitle) {
      subtitle.disabled = mode === 'audio';
      if (mode === 'audio') subtitle.checked = false;
    }
    if (subtitleHint) {
      subtitleHint.style.display = mode === 'audio' ? 'block' : 'none';
    }
    if (voicePanelHint) {
      voicePanelHint.style.display = mode === 'audio' ? 'block' : 'none';
    }
    if (voiceBtn) {
      voiceBtn.disabled = mode === 'audio';
      voiceBtn.classList.toggle('is-disabled', mode === 'audio');
    }
  }

  function slugColor(text) {
    var seed = String(text || 'avatar');
    var total = 0;
    for (var i = 0; i < seed.length; i += 1) total = (total + seed.charCodeAt(i) * (i + 11)) % 360;
    return total;
  }

  function placeholderSvg(title, tone) {
    var safeTitle = String(title || '数字人');
    var label = safeTitle.slice(0, 4);
    var hue = slugColor(safeTitle + ':' + (tone || 'public'));
    var hue2 = (hue + 34) % 360;
    var svg = ''
      + '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 480">'
      + '<defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="1">'
      + '<stop offset="0%" stop-color="hsl(' + hue + ', 84%, 62%)"/>'
      + '<stop offset="100%" stop-color="hsl(' + hue2 + ', 74%, 48%)"/>'
      + '</linearGradient></defs>'
      + '<rect width="480" height="480" rx="40" fill="url(#g)"/>'
      + '<circle cx="240" cy="182" r="76" fill="rgba(255,255,255,0.30)"/>'
      + '<path d="M118 394c22-70 78-108 122-108s100 38 122 108" fill="rgba(255,255,255,0.22)"/>'
      + '</svg>';
    return 'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(svg);
  }

  function coverSrc(item) {
    if (!item) return placeholderSvg('数字人', 'public');
    if (item.source_type === 'image' && item.detail_url) return item.detail_url;
    // 优先取明确的图片字段，再尝试常见的 cover/face/avatar/poster/thumbnail 字段名
    var candidates = [
      item.cover_url, item.image_url, item.face_url, item.avatar_url,
      item.poster_url, item.thumbnail_url, item.preview_url,
      item.pic, item.pic_url, item.head_url, item.head, item.thumb, item.thumbnail
    ];
    for (var i = 0; i < candidates.length; i++) {
      var v = candidates[i];
      if (v && typeof v === 'string' && v.trim()) return v.trim();
    }
    return placeholderSvg(item.title, item.section);
  }

  function avatarMedia(item, className) {
    var mediaClass = className ? (' class="' + escapeHtml(className) + '"') : '';
    if (item && item.source_type === 'video' && item.detail_url) {
      return '<video' + mediaClass + ' src="' + escapeHtml(item.detail_url) + '" muted playsinline preload="metadata"></video>';
    }
    // 支持云端返回的视频字段名（如 video_url / face_video_url）
    var videoUrl = item && (item.video_url || item.face_video_url || item.preview_video_url);
    if (videoUrl && /\.(mp4|webm|mov)(\?|$)/i.test(String(videoUrl))) {
      return '<video' + mediaClass + ' src="' + escapeHtml(videoUrl) + '" muted playsinline preload="metadata"></video>';
    }
    var src = coverSrc(item);
    var fallback = placeholderSvg(item ? item.title : '数字人', item ? item.section : 'public');
    return '<img'
      + mediaClass
      + ' src="' + escapeHtml(src) + '"'
      + ' data-fallback-src="' + escapeHtml(fallback) + '"'
      + ' alt="' + escapeHtml((item && item.title) || '数字人') + '"'
      + ' referrerpolicy="no-referrer"'
      + ' loading="lazy"'
      + ' decoding="async"'
      + ' onerror="if(this.dataset&&this.dataset.fallbackSrc&&this.src!==this.dataset.fallbackSrc){this.src=this.dataset.fallbackSrc;}"'
      + '>';
  }

  function coverImage(item) {
    return avatarMedia(item);
  }

  function avatarKey(item) {
    if (!item) return '';
    return item.avatar ? ('avatar:' + item.avatar) : ('task:' + (item.task_id || ''));
  }

  function findAvatarByKey(key) {
    if (!key) return null;
    var all = (state.avatarLibrary.mine || []).concat(state.avatarLibrary.public || []);
    for (var i = 0; i < all.length; i += 1) {
      if (avatarKey(all[i]) === key) return all[i];
    }
    return null;
  }

  function avatarDetailSrc(item) {
    if (item && item.detail_url) return item.detail_url;
    if (item && item.image_url) return item.image_url;
    return coverSrc(item);
  }

  /** 懒创建「数字人详情」弹窗（图片/视频预览）。
      注意：.hifly-modal 系列 CSS 都 scoped 在 #content-hifly-digital-human 下；
      所以必须 append 到该容器，否则样式不生效（弹窗看不到）。 */
  function ensureAvatarDetailModal() {
    var modal = document.getElementById('hiflyAvatarDetailModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'hiflyAvatarDetailModal';
    modal.className = 'hifly-modal';
    /** 内联兜底：万一 append 到 body 也能定位与置顶 */
    modal.style.cssText = 'display:none;position:fixed;inset:0;z-index:2000;';
    modal.innerHTML = ''
      + '<div class="hifly-modal-backdrop" data-modal-close="hiflyAvatarDetailModal" style="position:absolute;inset:0;background:rgba(15,23,42,0.58);"></div>'
      + '<div class="hifly-modal-card" role="dialog" aria-modal="true" style="position:relative;width:min(92vw,720px);max-height:92vh;overflow:auto;margin:4vh auto 0;border-radius:24px;background:#fff;box-shadow:0 32px 80px rgba(15,23,42,0.28);">'
      +   '<div class="hifly-modal-head" style="display:flex;align-items:center;justify-content:space-between;padding:1rem 1.2rem;border-bottom:1px solid rgba(15,23,42,0.08);">'
      +     '<h4 class="hifly-modal-title" id="hiflyAvatarDetailTitle" style="margin:0;font-size:1.1rem;color:#111827;">数字人详情</h4>'
      +     '<button type="button" class="hifly-modal-close" data-modal-close="hiflyAvatarDetailModal" aria-label="关闭" style="border:none;background:transparent;font-size:1.4rem;cursor:pointer;color:#6b7280;">×</button>'
      +   '</div>'
      +   '<div class="hifly-modal-body" id="hiflyAvatarDetailBody" style="padding:1rem;display:flex;justify-content:center;align-items:center;background:#000;min-height:320px;max-height:75vh;overflow:hidden;"></div>'
      +   '<div class="hifly-modal-foot" style="display:flex;justify-content:flex-end;padding:0.8rem 1.2rem;border-top:1px solid rgba(15,23,42,0.08);">'
      +     '<button type="button" class="btn btn-ghost" data-modal-close="hiflyAvatarDetailModal">关闭</button>'
      +   '</div>'
      + '</div>';
    /** .hifly-modal 样式作用域在 #content-hifly-digital-human 下，append 到该容器才能继承 */
    var host = document.getElementById('content-hifly-digital-human') || document.body;
    host.appendChild(modal);
    /** data-modal-close 点击均关闭 */
    Array.prototype.forEach.call(modal.querySelectorAll('[data-modal-close]'), function(btn) {
      btn.addEventListener('click', function() { closeAvatarDetailModal(); });
    });
    return modal;
  }

  function closeAvatarDetailModal() {
    var modal = document.getElementById('hiflyAvatarDetailModal');
    if (!modal) return;
    var body = document.getElementById('hiflyAvatarDetailBody');
    if (body) body.innerHTML = '';
    modal.style.display = 'none';
    document.body.classList.remove('hifly-modal-open');
  }

  function openAvatarDetail(item) {
    var detailUrl = avatarDetailSrc(item);
    if (!detailUrl) {
      showMessage('当前数字人暂无可预览的素材。', true);
      return;
    }
    ensureAvatarDetailModal();
    var titleEl = document.getElementById('hiflyAvatarDetailTitle');
    var body = document.getElementById('hiflyAvatarDetailBody');
    if (titleEl) titleEl.textContent = (item && item.title) ? String(item.title) : '数字人详情';
    if (body) {
      body.innerHTML = '';
      var srcAttr = escapeHtml(detailUrl);
      /** 视频：source_type=video 或 URL 后缀是视频；其它一律按图片渲染 */
      var isVideo = (item && item.source_type === 'video') || /\.(mp4|webm|mov)(\?|$)/i.test(detailUrl);
      if (isVideo) {
        body.innerHTML = '<video src="' + srcAttr + '" controls autoplay playsinline style="max-width:100%;max-height:70vh;background:#000;"></video>';
      } else {
        body.innerHTML = '<img src="' + srcAttr + '" alt="" style="max-width:100%;max-height:70vh;object-fit:contain;background:#000;">';
      }
    }
    var modal = document.getElementById('hiflyAvatarDetailModal');
    if (modal) {
      modal.style.display = 'block';
      document.body.classList.add('hifly-modal-open');
    }
  }

  function uniqueStrings(values) {
    var seen = {};
    return (values || []).filter(function(value) {
      var key = String(value || '').trim();
      if (!key || seen[key]) return false;
      seen[key] = true;
      return true;
    });
  }

  function voiceStyles(item) {
    if (item && Array.isArray(item.styles) && item.styles.length) return item.styles;
    if (item && item.voice) {
      return [{
        voice: item.voice,
        label: item.style_label || '默认风格',
        title: item.title || item.voice,
        demo_url: item.demo_url || ''
      }];
    }
    return [];
  }

  function previewUrlForVoiceId(voiceId, fallbackUrl) {
    var cached = voiceId && state.voicePreviewMap ? state.voicePreviewMap[voiceId] : '';
    return normalizeAssetUrl(cached || fallbackUrl || '');
  }

  function voiceStyleWithPreview(style) {
    if (!style) return null;
    var cloned = Object.assign({}, style);
    cloned.demo_url = previewUrlForVoiceId(cloned.voice, cloned.demo_url);
    return cloned;
  }

  function getSelectedStyleForVoiceItem(item) {
    var styles = voiceStyles(item);
    var selectedVoiceId = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : '';
    var matched = styles.find(function(style) { return style.voice === selectedVoiceId; });
    return voiceStyleWithPreview(matched || styles[0] || null);
  }

  function buildSelectedVoice(group, style, overrideDemoUrl) {
    var pickedStyle = voiceStyleWithPreview(style || getSelectedStyleForVoiceItem(group) || null);
    if (!group || !pickedStyle) return null;

    var tags = uniqueStrings([]
      .concat(group.tags || [])
      .concat(pickedStyle.language ? [pickedStyle.language] : [])
      .concat(pickedStyle.label && pickedStyle.label !== '默认风格' ? [pickedStyle.label] : [])
    ).slice(0, 4);

    return {
      voice: pickedStyle.voice,
      title: group.title || pickedStyle.title || pickedStyle.voice,
      style_label: pickedStyle.label || '默认风格',
      cover_url: group.cover_url || '',
      demo_url: overrideDemoUrl || pickedStyle.demo_url || group.demo_url || '',
      section: group.section || '',
      section_label: group.section_label || '声音',
      tags: tags,
      styles: voiceStyles(group).map(function(row) { return voiceStyleWithPreview(row); }),
      style_count: group.style_count || voiceStyles(group).length || 1
    };
  }

  function findVoiceGroupByVoiceId(voiceId) {
    if (!voiceId) return null;
    var all = (state.voiceLibrary.mine || []).concat(state.voiceLibrary.public || []);
    for (var i = 0; i < all.length; i += 1) {
      var group = all[i];
      var styles = voiceStyles(group);
      for (var j = 0; j < styles.length; j += 1) {
        if (styles[j].voice === voiceId) {
          return { group: group, style: styles[j] };
        }
      }
    }
    return null;
  }

  function hydrateVoiceLibraryPreview(library) {
    ['mine', 'public'].forEach(function(key) {
      library[key] = (library[key] || []).map(function(item) {
        var cloned = Object.assign({}, item);
        var styles = voiceStyles(cloned).map(function(style) { return voiceStyleWithPreview(style); });
        cloned.styles = styles;
        var previewStyle = styles.find(function(style) { return !!style.demo_url; }) || styles[0] || null;
        cloned.voice = previewStyle && previewStyle.voice ? previewStyle.voice : cloned.voice;
        cloned.demo_url = previewStyle && previewStyle.demo_url ? previewStyle.demo_url : previewUrlForVoiceId(cloned.voice, cloned.demo_url);
        return cloned;
      });
    });
    return library;
  }

  function setActiveView(viewName) {
    state.activeView = viewName === 'voice' ? 'voice' : (viewName === 'result' ? 'result' : 'avatar');
    if ($('hiflyAvatarLibraryView')) $('hiflyAvatarLibraryView').style.display = state.activeView === 'avatar' ? 'block' : 'none';
    if ($('hiflyVoiceLibraryView')) $('hiflyVoiceLibraryView').style.display = state.activeView === 'voice' ? 'block' : 'none';
    if ($('hiflyResultView')) $('hiflyResultView').style.display = state.activeView === 'result' ? 'block' : 'none';
    if ($('hiflyAvatarLibraryTabBtn')) $('hiflyAvatarLibraryTabBtn').classList.toggle('is-active', state.activeView === 'avatar');
    if ($('hiflyVoiceLibraryTabBtn')) $('hiflyVoiceLibraryTabBtn').classList.toggle('is-active', state.activeView === 'voice');
    if ($('hiflyResultTabBtn')) $('hiflyResultTabBtn').classList.toggle('is-active', state.activeView === 'result');
  }

  function updateTaskStatus(text, tone) {
    var el = $('hiflyTaskStatusText');
    if (!el) return;
    el.textContent = text || '--';
    el.setAttribute('data-tone', tone || 'idle');
  }

  function updateTaskKind(text) {
    state.taskKindLabel = text || '未开始';
    var el = $('hiflyTaskKindText');
    if (!el) return;
    el.textContent = state.taskKindLabel;
  }

  function renderResultPlaceholder(title, subtitle, isBusy) {
    var el = $('hiflyResultSurface');
    if (!el) return;
    el.innerHTML = ''
      + '<div class="viral-video-placeholder">'
      + '<div class="hifly-result-status-head">'
      + (isBusy ? '<span class="hifly-result-spinner" aria-hidden="true"></span>' : '')
      + '<div class="hifly-result-status-copy">'
      + '<strong>' + escapeHtml(title || '等待生成') + '</strong>'
      + '<span>' + escapeHtml(subtitle || '提交后这里会自动显示任务进度和最终结果。') + '</span>'
      + '</div>'
      + '</div>'
      + '</div>';
  }

  function renderResultVideo(videoUrl) {
    var el = $('hiflyResultSurface');
    if (!el) return;
    el.innerHTML = '<video src="' + escapeHtml(videoUrl) + '" controls style="width:100%;height:100%;object-fit:contain;background:#000;border-radius:20px;"></video>';
  }

  function renderResultSuccessCard(title, bodyHtml) {
    var el = $('hiflyResultSurface');
    if (!el) return;
    el.innerHTML = ''
      + '<div class="hifly-success-card">'
      + '<strong>' + escapeHtml(title) + '</strong>'
      + bodyHtml
      + '</div>';
  }

  function renderAvatarSuccessBody(item) {
    var preview = item ? avatarMedia(item, 'hifly-success-visual-media') : '';
    var visual = preview ? '<div class="hifly-success-visual">' + preview + '</div>' : '';
    return ''
      + visual
      + '<span>任务已完成，新的数字人已经刷新到“我的数字人”。</span>';
  }

  function setBusy(flag) {
    state.submitting = !!flag;
    [
      'hiflyGenerateBtn',
      'hiflyLoadVoicesBtn',
      'hiflyRefreshLibraryBtn',
      'hiflyOpenAvatarCreateBtn',
      'hiflyOpenVoiceCreateBtn',
      'hiflyCreateVoiceBtn'
    ].forEach(function(id) {
      var btn = $(id);
      if (btn) btn.disabled = !!flag;
    });
    syncCreateSubmitStates();
  }

  function syncCreateSubmitStates() {
    [
      ['hiflyAvatarImageAgree', 'hiflyAvatarImageSubmitBtn'],
      ['hiflyAvatarVideoAgree', 'hiflyAvatarVideoSubmitBtn'],
      ['hiflyVoiceCreateAgree', 'hiflyVoiceSubmitBtn']
    ].forEach(function(pair) {
      var checkbox = $(pair[0]);
      var button = $(pair[1]);
      if (!button) return;
      button.disabled = !!state.submitting || !(checkbox && checkbox.checked);
    });
  }

  function selectAvatar(item, shouldScroll) {
    state.selectedAvatar = item || null;
    if ($('hiflyAvatarInput')) $('hiflyAvatarInput').value = item && item.avatar ? item.avatar : '';
    renderSelectedAvatar();
    renderAvatarLibrary();
    if (shouldScroll && window.innerWidth < 1100) {
      var preview = $('hiflySelectedAvatarPreview');
      if (preview && typeof preview.scrollIntoView === 'function') {
        preview.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  }

  function selectVoice(item, shouldScroll) {
    state.selectedVoice = buildSelectedVoice(item, getSelectedStyleForVoiceItem(item)) || null;
    if ($('hiflyVoiceInput')) $('hiflyVoiceInput').value = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : '';
    renderSelectedVoice();
    renderVoiceLibrary();
    if (shouldScroll && window.innerWidth < 1100) {
      var preview = $('hiflySelectedVoicePreview');
      if (preview && typeof preview.scrollIntoView === 'function') {
        preview.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  }

  function selectVoiceVariant(group, style, shouldScroll, overrideDemoUrl) {
    state.selectedVoice = buildSelectedVoice(group, style, overrideDemoUrl);
    if ($('hiflyVoiceInput')) $('hiflyVoiceInput').value = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : '';
    renderSelectedVoice();
    renderVoiceLibrary();
    if (shouldScroll && window.innerWidth < 1100) {
      var preview = $('hiflySelectedVoicePreview');
      if (preview && typeof preview.scrollIntoView === 'function') {
        preview.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  }

  function renderSelectedAvatar() {
    var el = $('hiflySelectedAvatarPreview');
    if (!el) return;
    var item = state.selectedAvatar;
    if (!item) {
      el.className = 'hifly-selected-avatar is-empty';
      el.innerHTML = ''
        + '<div class="hifly-selected-empty">'
        + '<strong>还没有选数字人</strong>'
        + '<span>去右侧数字人库点“选择数字人”，这里就会固定显示当前形象。</span>'
        + '</div>';
      return;
    }

    var tags = (item.tags || []).slice(0, 3).map(function(tag) {
      return '<span class="hifly-mini-tag">' + escapeHtml(tag) + '</span>';
    }).join('');
    var countText = item.material_count ? (item.material_count + ' 个素材') : '已就绪';

    el.className = 'hifly-selected-avatar';
    el.innerHTML = ''
      + '<div class="hifly-selected-avatar-cover">'
      + coverImage(item)
      + '<span class="hifly-section-chip">' + escapeHtml(item.section_label || '数字人') + '</span>'
      + '</div>'
      + '<div class="hifly-selected-copy">'
      + '<div class="hifly-selected-title-row"><strong>' + escapeHtml(item.title) + '</strong><span>' + escapeHtml(countText) + '</span></div>'
      + '<div class="hifly-selected-tags">' + tags + '</div>'
      + '<button type="button" class="btn btn-ghost btn-sm hifly-avatar-detail-btn" data-avatar-key="' + escapeHtml(avatarKey(item)) + '">查看详情</button>'
      + '</div>';
  }

  function renderSelectedVoice() {
    var el = $('hiflySelectedVoicePreview');
    if (!el) return;
    var item = state.selectedVoice;
    if (!item) {
      el.className = 'hifly-selected-voice is-empty';
      el.innerHTML = ''
        + '<div class="hifly-selected-empty">'
        + '<strong>还没有选声音</strong>'
        + '<span>去右侧声音库里选择声音，或者直接创建你自己的声音。</span>'
        + '</div>';
      return;
    }

    var tags = (item.tags || []).slice(0, 4).map(function(tag) {
      return '<span class="hifly-mini-tag">' + escapeHtml(tag) + '</span>';
    }).join('');
    var audio = voicePreviewButtonHtml(item.demo_url);

    el.className = 'hifly-selected-voice';
    el.innerHTML = ''
      + '<div class="hifly-selected-voice-cover">'
      + coverImage(item)
      + '<span class="hifly-section-chip">' + escapeHtml(item.section_label || '声音') + '</span>'
      + '</div>'
      + '<div class="hifly-selected-copy">'
      + '<div class="hifly-selected-voice-head">'
      + '<strong>' + escapeHtml(item.title) + '</strong>'
      + '<span class="hifly-section-chip is-inline">' + escapeHtml((item.style_count || 1) + ' 个风格') + '</span>'
      + '</div>'
      + '<div class="hifly-selected-style-label">当前风格：' + escapeHtml(item.style_label || '默认风格') + '</div>'
      + '<div class="hifly-selected-tags">' + tags + '</div>'
      + '<div class="hifly-selected-audio">' + audio + '</div>'
      + '</div>';
    bindVoicePreviewButtons();
  }

  function renderAvatarCard(item) {
    var selected = state.selectedAvatar && state.selectedAvatar.avatar === item.avatar;
    var tags = (item.tags || []).slice(0, 2).map(function(tag) {
      return '<span class="hifly-card-tag">' + escapeHtml(tag) + '</span>';
    }).join('');
    var countText = item.material_count ? (item.material_count + ' 个素材') : '已就绪';
    var key = avatarKey(item);
    return ''
      + '<article class="hifly-avatar-card' + (selected ? ' is-selected' : '') + '" data-avatar-key="' + escapeHtml(key) + '" style="cursor:pointer;">'
      + '<div class="hifly-avatar-card-cover">'
      + coverImage(item)
      + '<span class="hifly-avatar-card-badge">' + escapeHtml(item.section_label || '数字人') + '</span>'
      + '<span class="hifly-avatar-card-count">' + escapeHtml(countText) + '</span>'
      + '</div>'
      + '<div class="hifly-avatar-card-body">'
      + '<div class="hifly-avatar-card-main">'
      + '<div class="hifly-avatar-card-title" title="' + escapeHtml(item.title) + '">' + escapeHtml(item.title) + '</div>'
      + '<div class="hifly-avatar-card-tags">' + tags + '</div>'
      + '</div>'
      + '<div class="hifly-avatar-card-meta"><span>' + escapeHtml(countText) + '</span></div>'
      + '<div class="hifly-avatar-card-actions">'
      + '<button type="button" class="btn btn-ghost btn-sm hifly-avatar-detail-btn hifly-avatar-pick-btn" data-avatar-key="' + escapeHtml(key) + '">查看详情</button>'
      + '<button type="button" class="btn ' + (selected ? 'btn-ghost' : 'btn-primary') + ' btn-sm hifly-avatar-pick-btn" data-avatar-id="' + escapeHtml(item.avatar) + '">' + (selected ? '已选择' : '选择数字人') + '</button>'
      + '</div>'
      + '</div>'
      + '</article>';
  }

  function renderVoiceCard(item) {
    var styles = voiceStyles(item);
    var activeStyle = getSelectedStyleForVoiceItem(item);
    var selected = !!(state.selectedVoice && styles.some(function(style) { return style.voice === state.selectedVoice.voice; }));
    var tags = (item.tags || []).slice(0, 2).map(function(tag) {
      return '<span class="hifly-card-tag">' + escapeHtml(tag) + '</span>';
    }).join('');
    var styleList = styles.map(function(style) {
      var preview = previewUrlForVoiceId(style.voice, style.demo_url);
      var isActive = !!(state.selectedVoice && state.selectedVoice.voice === style.voice);
      return ''
        + '<button type="button" class="hifly-voice-style-btn' + (isActive ? ' is-active' : '') + '" data-voice-style-id="' + escapeHtml(style.voice) + '">'
        + '<span class="hifly-voice-style-copy"><span class="hifly-voice-style-play">' + (preview ? '>' : 'o') + '</span><span class="hifly-voice-style-text">' + escapeHtml(style.label || style.title || '默认风格') + '</span></span>'
        + '<span class="hifly-voice-style-state">' + escapeHtml(preview ? '可试听' : '选择') + '</span>'
        + '</button>';
    }).join('');
    var audio = voicePreviewButtonHtml(activeStyle && activeStyle.demo_url ? activeStyle.demo_url : '');
    return ''
      + '<article class="hifly-voice-card' + (selected ? ' is-selected' : '') + '">'
      + '<div class="hifly-voice-card-top">'
      + '<div class="hifly-voice-card-thumb">' + coverImage(item) + '</div>'
      + '<div class="hifly-voice-card-main">'
      + '<div class="hifly-voice-card-head">'
      + '<div><div class="hifly-voice-card-title">' + escapeHtml(item.title) + '</div></div>'
      + '<span class="hifly-section-chip is-inline">' + escapeHtml(item.section_label || '声音') + '</span>'
      + '</div>'
      + '<div class="hifly-avatar-card-tags">' + tags + '</div>'
      + '<div class="hifly-voice-style-list">' + styleList + '</div>'
      + '</div>'
      + '</div>'
      + '<div class="hifly-voice-audio">' + audio + '</div>'
      + '<button type="button" class="btn ' + (selected ? 'btn-ghost' : 'btn-primary') + ' btn-sm hifly-voice-pick-btn" data-voice-id="' + escapeHtml(activeStyle && activeStyle.voice ? activeStyle.voice : item.voice) + '">' + (selected ? '已选择当前风格' : '选择当前风格') + '</button>'
      + '</article>';
  }

  function filterRows(rows, query) {
    var text = String(query || '').trim().toLowerCase();
    if (!text) return (rows || []).slice();
    return (rows || []).filter(function(item) {
      var haystack = String(item.search_text || item.title || '').toLowerCase();
      if (Array.isArray(item.styles) && item.styles.length) {
        haystack += ' ' + item.styles.map(function(style) {
          return [style.label || '', style.title || '', style.voice || ''].join(' ');
        }).join(' ');
      }
      return haystack.indexOf(text) >= 0;
    });
  }

  function renderLibrarySection(config) {
    var grid = $(config.gridId);
    var empty = $(config.emptyId);
    var count = $(config.countId);
    var moreBtn = $(config.moreBtnId);
    if (!grid || !empty || !count || !moreBtn) return;

    var filtered = filterRows(config.rows || [], config.query || '');
    var incremental = config.mode === 'incremental';
    var clientIncremental = config.mode === 'client-incremental';
    var hasQuery = !!String(config.query || '').trim();
    var expanded = !!state.libraryExpanded[config.expandedKey] || hasQuery;
    var visibleRows;
    if (incremental) {
      visibleRows = filtered;
    } else if (clientIncremental) {
      var visCount = hasQuery ? filtered.length : Math.max(0, Number(config.visibleCount || filtered.length));
      visibleRows = filtered.slice(0, visCount);
    } else {
      visibleRows = expanded ? filtered : filtered.slice(0, config.limit);
    }

    count.textContent = String(filtered.length || 0);
    if (!filtered.length) {
      grid.innerHTML = '';
      empty.style.display = 'block';
      empty.textContent = config.emptyText;
      moreBtn.style.display = 'none';
      return;
    }

    empty.style.display = 'none';
    grid.innerHTML = visibleRows.map(config.render).join('');
    if (incremental) {
      moreBtn.style.display = config.hasMore && !hasQuery ? 'inline-flex' : 'none';
      moreBtn.disabled = !!config.isLoading;
      moreBtn.textContent = config.isLoading ? (config.loadingText || '正在加载...') : (config.moreText || '加载更多');
      return;
    }
    if (clientIncremental) {
      var remaining = filtered.length - visibleRows.length;
      if (remaining > 0 && !hasQuery) {
        moreBtn.style.display = 'inline-flex';
        moreBtn.disabled = false;
        var step = Number(config.step || 20);
        moreBtn.textContent = '再加载 ' + Math.min(step, remaining) + ' 个（剩余 ' + remaining + '）';
      } else {
        moreBtn.style.display = 'none';
      }
      return;
    }

    if (filtered.length > config.limit && !hasQuery) {
      moreBtn.style.display = 'inline-flex';
      moreBtn.textContent = expanded ? '收起' : '显示更多';
    } else {
      moreBtn.style.display = 'none';
    }
  }

  function renderAvatarLibrary() {
    renderLibrarySection({
      gridId: 'hiflyMineAvatarGrid',
      emptyId: 'hiflyMineAvatarEmpty',
      countId: 'hiflyMineCount',
      moreBtnId: 'hiflyMineMoreBtn',
      expandedKey: 'avatarMine',
      limit: 8,
      rows: state.avatarLibrary.mine || [],
      query: state.avatarSearch,
      emptyText: state.avatarLibrary.mine_supported
        ? (state.avatarLibrary.using_default_token
          ? '当前还没有拿到“我的数字人”。你可以先创建一个自己的数字人。'
          : '当前账号下还没有“我的数字人”，你可以先创建一个自己的数字人。')
        : '当前平台服务暂时无法读取“我的数字人”分类，请稍后重试。',
      render: renderAvatarCard
    });
    renderLibrarySection({
      gridId: 'hiflyPublicAvatarGrid',
      emptyId: 'hiflyPublicAvatarEmpty',
      countId: 'hiflyPublicCount',
      moreBtnId: 'hiflyPublicMoreBtn',
      expandedKey: 'avatarPublic',
      mode: 'client-incremental',
      rows: state.avatarLibrary.public || [],
      query: state.avatarSearch,
      visibleCount: Number(state.avatarLibrary.public_visible || 20),
      step: 20,
      emptyText: '暂时没有拿到公共数字人列表，请稍后重试。',
      render: renderAvatarCard
    });
    bindAvatarCardEvents();
  }

  function renderVoiceLibrary() {
    renderLibrarySection({
      gridId: 'hiflyMineVoiceGrid',
      emptyId: 'hiflyMineVoiceEmpty',
      countId: 'hiflyMineVoiceCount',
      moreBtnId: 'hiflyMineVoiceMoreBtn',
      expandedKey: 'voiceMine',
      limit: 8,
      rows: state.voiceLibrary.mine || [],
      query: state.voiceSearch,
      emptyText: state.voiceLibrary.using_default_token
        ? '当前还没有拿到“我的声音”。你可以直接创建一条自己的声音。'
        : '当前账号下还没有“我的声音”，你可以直接创建一条自己的声音。',
      render: renderVoiceCard
    });
    renderLibrarySection({
      gridId: 'hiflyPublicVoiceGrid',
      emptyId: 'hiflyPublicVoiceEmpty',
      countId: 'hiflyPublicVoiceCount',
      moreBtnId: 'hiflyPublicVoiceMoreBtn',
      expandedKey: 'voicePublic',
      limit: 12,
      rows: state.voiceLibrary.public || [],
      query: state.voiceSearch,
      emptyText: '暂时没有拿到公共声音列表，请稍后重试。',
      render: renderVoiceCard
    });
    bindVoiceCardEvents();
    bindVoicePreviewButtons();
  }

  function bindAvatarCardEvents() {
    Array.prototype.forEach.call(document.querySelectorAll('.hifly-avatar-pick-btn'), function(btn) {
      btn.onclick = function(ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        var avatarId = btn.getAttribute('data-avatar-id') || '';
        var all = (state.avatarLibrary.mine || []).concat(state.avatarLibrary.public || []);
        var picked = all.find(function(item) { return item.avatar === avatarId; });
        selectAvatar(picked, true);
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.hifly-avatar-detail-btn'), function(btn) {
      btn.onclick = function(ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        var picked = findAvatarByKey(btn.getAttribute('data-avatar-key') || '');
        if (picked) openAvatarDetail(picked);
      };
    });
    /** 点卡片任意位置（除按钮）也打开详情弹窗 */
    Array.prototype.forEach.call(document.querySelectorAll('.hifly-avatar-card[data-avatar-key]'), function(card) {
      card.onclick = function(ev) {
        if (ev && ev.target && ev.target.closest && ev.target.closest('button')) return;
        var picked = findAvatarByKey(card.getAttribute('data-avatar-key') || '');
        if (picked) openAvatarDetail(picked);
      };
    });
  }

  function bindVoiceCardEvents() {
    Array.prototype.forEach.call(document.querySelectorAll('.hifly-voice-pick-btn'), function(btn) {
      btn.onclick = function() {
        var voiceId = btn.getAttribute('data-voice-id') || '';
        var found = findVoiceGroupByVoiceId(voiceId);
        if (found) selectVoiceVariant(found.group, found.style, true);
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.hifly-voice-style-btn'), function(btn) {
      btn.onclick = function() {
        var voiceId = btn.getAttribute('data-voice-style-id') || '';
        var found = findVoiceGroupByVoiceId(voiceId);
        if (found) selectVoiceVariant(found.group, found.style, true);
      };
    });
  }

  function loadAvatarLibrary(silent) {
    if (!silent) showMessage('正在加载数字人列表...', false);
    state.avatarLibrary.public_loading = false;
    return Promise.all([
      requestCloudGet('/api/hifly/my/avatar/list?page=1&size=100'),
      requestCloud('/api/hifly/avatar/library', { page: 1, size: 10 })
    ]).then(function(results) {
      var mineData = results[0] || {};
      var data = results[1] || {};
      state.avatarLibrary = {
        mine: mineData.items || [],
        public: data.public || [],
        mine_supported: true,
        mine_message: '',
        using_default_token: !!data.using_default_token,
        public_page: Number(data.public_page || 1),
        public_page_size: Number(data.public_size || 20),
        public_has_more: !!data.public_has_more,
        public_loading: false,
        public_visible: 20
      };
      state.libraryExpanded.avatarPublic = false;

      var all = (state.avatarLibrary.mine || []).concat(state.avatarLibrary.public || []);
      var matched = state.selectedAvatar && all.find(function(item) { return item.avatar === state.selectedAvatar.avatar; });
      if (matched) {
        state.selectedAvatar = matched;
      } else if (all.length) {
        state.selectedAvatar = all[0];
      } else {
        state.selectedAvatar = null;
      }

      renderSelectedAvatar();
      renderAvatarLibrary();

      if (!silent) {
        if (state.avatarLibrary.using_default_token) {
          showMessage('已使用平台托管服务自动刷新数字人列表。', false);
        } else {
          showMessage('数字人列表已更新。', false);
        }
      }
    });
  }

  function loadMorePublicAvatars() {
    /** 后端已经一次性返回全部过滤后的公共数字人，前端按 20 个一批渐进展示，避免首屏拥堵。 */
    var total = (state.avatarLibrary.public || []).length;
    var current = Number(state.avatarLibrary.public_visible || 20);
    if (current >= total) return Promise.resolve();
    state.avatarLibrary.public_visible = Math.min(total, current + 20);
    renderAvatarLibrary();
    return Promise.resolve();
  }

  function loadVoices(silent) {
    if (!silent) showMessage('正在加载声音列表...', false);
    return Promise.all([
      requestCloudGet('/api/hifly/my/voice/list?page=1&size=100'),
      requestCloud('/api/hifly/voice/library')
    ]).then(function(results) {
      var mineData = results[0] || {};
      var data = results[1] || {};
      var previousVoiceId = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : '';
      state.voiceLibrary = hydrateVoiceLibraryPreview({
        mine: mineData.items || [],
        public: data.public || [],
        mine_supported: true,
        mine_message: '',
        using_default_token: !!data.using_default_token
      });

      var found = previousVoiceId ? findVoiceGroupByVoiceId(previousVoiceId) : null;
      if (found) {
        state.selectedVoice = buildSelectedVoice(found.group, found.style);
      } else {
        var all = (state.voiceLibrary.mine || []).concat(state.voiceLibrary.public || []);
        if (all.length) {
          state.selectedVoice = buildSelectedVoice(all[0], getSelectedStyleForVoiceItem(all[0]));
        } else {
          state.selectedVoice = null;
        }
      }

      if (!state.selectedVoice) {
        var fallbackAll = (state.voiceLibrary.mine || []).concat(state.voiceLibrary.public || []);
        if (fallbackAll.length) {
          state.selectedVoice = buildSelectedVoice(fallbackAll[0], getSelectedStyleForVoiceItem(fallbackAll[0]));
        }
      }
      if ($('hiflyVoiceInput')) $('hiflyVoiceInput').value = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : '';

      renderSelectedVoice();
      renderVoiceLibrary();

      if (!silent) {
        if (state.voiceLibrary.using_default_token) {
          showMessage('已使用平台托管服务自动刷新声音列表。', false);
        } else {
          showMessage('声音列表已更新。', false);
        }
      }
    });
  }

  function renderVideoHistoryItem(item) {
    /** 每个历史口播任务：缩略条目 + 视频/状态 + 重播按钮。 */
    var statusTone = item.status === 'success'
      ? 'success'
      : (item.status === 'failed' ? 'danger' : 'processing');
    var videoUrl = item.video_url || '';
    var mediaHtml;
    if (videoUrl && item.status === 'success') {
      mediaHtml = '<video src="' + escapeHtml(videoUrl) + '" controls preload="metadata" '
        + 'style="width:100%;height:140px;object-fit:cover;background:#000;border-radius:12px;"></video>';
    } else {
      mediaHtml = '<div class="hifly-video-history-placeholder" '
        + 'style="width:100%;height:140px;border-radius:12px;display:flex;align-items:center;justify-content:center;'
        + 'background:linear-gradient(135deg,#e9efff,#dcdff7);color:#5b6475;font-size:0.8rem;">'
        + escapeHtml(item.status_text || '处理中') + '</div>';
    }
    var statusBadge = '<span class="hifly-result-pill" data-tone="' + statusTone + '">'
      + escapeHtml(item.status_text || '处理中') + '</span>';
    var openBtn = (videoUrl && item.status === 'success')
      ? '<button type="button" class="btn btn-ghost btn-sm hifly-video-history-play" data-task-id="'
        + escapeHtml(item.task_id || '') + '">预览</button>'
      : '';
    var refreshBtn = (item.status !== 'success' && item.status !== 'failed')
      ? '<button type="button" class="btn btn-ghost btn-sm hifly-video-history-refresh" data-task-id="'
        + escapeHtml(item.task_id || '') + '">刷新</button>'
      : '';
    var deleteBtn = '<button type="button" class="btn btn-ghost btn-sm hifly-video-history-delete" data-id="'
      + escapeHtml(String(item.id || '')) + '">删除</button>';
    var textSnippet = item.text ? String(item.text).slice(0, 48) + (String(item.text).length > 48 ? '…' : '') : '';
    return ''
      + '<div class="hifly-video-history-card" '
      + 'style="background:#fff;border-radius:16px;padding:12px;box-shadow:0 6px 18px rgba(36,54,88,0.06);display:flex;flex-direction:column;gap:8px;">'
      + mediaHtml
      + '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">'
      + '<strong style="font-size:0.9rem;color:#243957;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
      + escapeHtml(item.title || '数字人口播') + '</strong>'
      + statusBadge
      + '</div>'
      + (textSnippet ? '<div style="font-size:0.75rem;color:#75839a;line-height:1.45;">' + escapeHtml(textSnippet) + '</div>' : '')
      + '<div style="display:flex;gap:6px;flex-wrap:wrap;">' + openBtn + refreshBtn + deleteBtn + '</div>'
      + '</div>';
  }

  function renderVideoHistory() {
    var grid = $('hiflyVideoHistoryGrid');
    var empty = $('hiflyVideoHistoryEmpty');
    var count = $('hiflyVideoHistoryCount');
    if (!grid || !empty || !count) return;
    var items = state.videoHistory || [];
    count.textContent = String(items.length);
    if (!items.length) {
      grid.innerHTML = '';
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    grid.innerHTML = items.map(renderVideoHistoryItem).join('');
    // 布局：2~4 列自适应
    grid.style.display = 'grid';
    grid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(220px, 1fr))';
    grid.style.gap = '12px';
    bindVideoHistoryEvents();
  }

  function bindVideoHistoryEvents() {
    Array.prototype.forEach.call(document.querySelectorAll('.hifly-video-history-play'), function(btn) {
      btn.onclick = function() {
        var taskId = btn.getAttribute('data-task-id') || '';
        var item = (state.videoHistory || []).find(function(it) { return String(it.task_id) === taskId; });
        if (item && item.video_url) {
          updateTaskKind(item.title || '数字人口播');
          updateTaskStatus(item.status_text || '已完成', 'success');
          renderResultVideo(item.video_url);
        }
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.hifly-video-history-refresh'), function(btn) {
      btn.onclick = function() {
        var taskId = btn.getAttribute('data-task-id') || '';
        if (!taskId) return;
        btn.disabled = true;
        requestCloud('/api/hifly/my/video/task', { task_id: taskId, token: tokenValue() })
          .catch(function() {})
          .finally(function() { loadVideoHistory(true); });
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.hifly-video-history-delete'), function(btn) {
      btn.onclick = function() {
        var id = btn.getAttribute('data-id') || '';
        if (!id) return;
        if (!window.confirm('确认删除这条口播任务记录？转存的视频素材不会被删除。')) return;
        btn.disabled = true;
        fetch(cloudBaseUrl() + '/api/hifly/my/video/' + encodeURIComponent(id), {
          method: 'DELETE',
          headers: Object.assign({ 'Accept': 'application/json' }, authHeadersSafe())
        }).then(function() { loadVideoHistory(true); })
          .catch(function() { btn.disabled = false; });
      };
    });
  }

  function loadVideoHistory(silent) {
    return requestCloudGet('/api/hifly/my/video/list?page=1&size=50')
      .then(function(data) {
        state.videoHistory = (data && data.items) || [];
        renderVideoHistory();
      })
      .catch(function(err) {
        if (!silent) showMessage(err && err.message ? err.message : '历史任务加载失败', true);
      });
  }

  function openModal(id) {
    var el = $(id);
    if (!el) return;
    el.style.display = 'block';
    document.body.classList.add('hifly-modal-open');
  }

  function closeModal(id) {
    var el = $(id);
    if (!el) return;
    el.style.display = 'none';
    var stillOpen = Array.prototype.some.call(document.querySelectorAll('.hifly-modal'), function(modal) {
      return modal.style.display !== 'none';
    });
    if (!stillOpen) document.body.classList.remove('hifly-modal-open');
  }

  function resetCreateForms() {
    ['hiflyAvatarImageName', 'hiflyAvatarVideoName', 'hiflyVoiceCreateName'].forEach(function(id) {
      if ($(id)) $(id).value = '';
    });
    ['hiflyAvatarImageFile', 'hiflyAvatarVideoFile', 'hiflyVoiceCreateFile'].forEach(function(id) {
      if ($(id)) $(id).value = '';
      revokeUploadPreview(id);
    });
    ['hiflyAvatarImageFileMeta', 'hiflyAvatarVideoFileMeta', 'hiflyVoiceFileMeta'].forEach(function(id) {
      if ($(id)) {
        $(id).style.display = 'none';
        $(id).innerHTML = '';
      }
    });
    [
      ['hiflyAvatarImageUploadBox', 'hiflyAvatarImageFile', 'hiflyAvatarImagePreview', 'hiflyAvatarImageFileMeta', 'image'],
      ['hiflyAvatarVideoUploadBox', 'hiflyAvatarVideoFile', 'hiflyAvatarVideoPreview', 'hiflyAvatarVideoFileMeta', 'video'],
      ['hiflyVoiceUploadBox', 'hiflyVoiceCreateFile', 'hiflyVoiceFilePreview', 'hiflyVoiceFileMeta', 'audio']
    ].forEach(function(item) {
      syncUploadSelection(item[0], item[1], item[2], item[3], item[4], null);
    });
    ['hiflyAvatarImageAgree', 'hiflyAvatarVideoAgree', 'hiflyVoiceCreateAgree'].forEach(function(id) {
      if ($(id)) $(id).checked = true;
    });
    var radios = document.querySelectorAll('input[name="hiflyAvatarImageModel"]');
    Array.prototype.forEach.call(radios, function(radio) {
      radio.checked = radio.value === '2';
    });
    syncCreateSubmitStates();
  }

  function formatFileSize(size) {
    var num = Number(size || 0);
    if (!isFinite(num) || num <= 0) return '--';
    if (num >= 1024 * 1024) return (num / (1024 * 1024)).toFixed(2) + ' MB';
    return (num / 1024).toFixed(1) + ' KB';
  }

  function revokeUploadPreview(inputId) {
    var previewUrl = state.uploadPreviews[inputId];
    if (!previewUrl) return;
    try { URL.revokeObjectURL(previewUrl); } catch (e) {}
    delete state.uploadPreviews[inputId];
  }

  function createUploadPreviewUrl(inputId, file) {
    revokeUploadPreview(inputId);
    if (!file) return '';
    try {
      var previewUrl = URL.createObjectURL(file);
      state.uploadPreviews[inputId] = previewUrl;
      return previewUrl;
    } catch (e) {
      return '';
    }
  }

  function renderFileMeta(metaId, file) {
    var el = $(metaId);
    if (!el) return;
    if (!file) {
      el.style.display = 'none';
      el.innerHTML = '';
      return;
    }
    el.style.display = 'block';
    el.innerHTML = '<strong>' + escapeHtml(file.name || '未命名文件') + '</strong><span>' + escapeHtml(formatFileSize(file.size)) + '</span>';
  }

  function renderUploadPreview(triggerId, inputId, previewId, metaId, previewKind, file) {
    var trigger = $(triggerId);
    var preview = $(previewId);
    if (trigger) trigger.style.display = file ? 'none' : 'flex';
    if (!preview) {
      renderFileMeta(metaId, file);
      return;
    }
    if (!file) {
      preview.style.display = 'none';
      preview.innerHTML = '';
      renderFileMeta(metaId, null);
      return;
    }

    var previewUrl = createUploadPreviewUrl(inputId, file);
    var mediaHtml = '';
    if (previewKind === 'video') {
      mediaHtml = '<video controls preload="metadata" playsinline src="' + escapeHtml(previewUrl) + '"></video>';
    } else if (previewKind === 'audio') {
      mediaHtml = '<audio controls preload="metadata" src="' + escapeHtml(previewUrl) + '"></audio>';
    } else {
      mediaHtml = '<img src="' + escapeHtml(previewUrl) + '" alt="' + escapeHtml(file.name || 'preview') + '">';
    }

    preview.style.display = 'block';
    preview.innerHTML = ''
      + '<div class="hifly-upload-preview-inner hifly-upload-preview-inner-' + escapeHtml(previewKind || 'image') + '">'
      + mediaHtml
      + '<button type="button" class="hifly-upload-remove" aria-label="删除已上传文件">×</button>'
      + '</div>';

    var removeBtn = preview.querySelector('.hifly-upload-remove');
    if (removeBtn) {
      removeBtn.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        clearUploadSelection(triggerId, inputId, previewId, metaId, previewKind);
      });
    }

    renderFileMeta(metaId, file);
  }

  function syncUploadSelection(triggerId, inputId, previewId, metaId, previewKind, file) {
    renderUploadPreview(triggerId, inputId, previewId, metaId, previewKind, file);
  }

  function clearUploadSelection(triggerId, inputId, previewId, metaId, previewKind) {
    var input = $(inputId);
    if (input) input.value = '';
    revokeUploadPreview(inputId);
    syncUploadSelection(triggerId, inputId, previewId, metaId, previewKind, null);
  }

  function wireFileBox(triggerId, inputId, metaId, previewId, previewKind) {
    var trigger = $(triggerId);
    var input = $(inputId);
    if (!trigger || !input || trigger.getAttribute('data-bound') === '1') return;
    trigger.setAttribute('data-bound', '1');
    trigger.addEventListener('click', function() { input.click(); });
    input.addEventListener('change', function() {
      syncUploadSelection(triggerId, inputId, previewId, metaId, previewKind, input.files && input.files[0] ? input.files[0] : null);
    });
    trigger.addEventListener('dragover', function(ev) {
      ev.preventDefault();
      trigger.classList.add('is-dragover');
    });
    trigger.addEventListener('dragleave', function() {
      trigger.classList.remove('is-dragover');
    });
    trigger.addEventListener('drop', function(ev) {
      ev.preventDefault();
      trigger.classList.remove('is-dragover');
      if (!ev.dataTransfer || !ev.dataTransfer.files || !ev.dataTransfer.files.length) return;
      input.files = ev.dataTransfer.files;
      syncUploadSelection(triggerId, inputId, previewId, metaId, previewKind, ev.dataTransfer.files[0]);
    });
    syncUploadSelection(triggerId, inputId, previewId, metaId, previewKind, input.files && input.files[0] ? input.files[0] : null);
  }

  function startTask(taskType, taskKindLabel, taskId) {
    state.taskType = taskType || '';
    state.taskId = taskId || '';
    updateTaskKind(taskKindLabel || '任务处理中');
    updateTaskStatus('提交中', 'processing');
    setActiveView('result');
  }

  function clearPoll() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function handleAvatarTaskSuccess(data) {
    var item = data && data.item ? data.item : (data || {});
    if (item.avatar && data && !data.avatar) data.avatar = item.avatar;
    updateTaskStatus('已完成', 'success');
    Promise.allSettled([loadAvatarLibrary(true)]).then(function() {
      var all = (state.avatarLibrary.mine || []).concat(state.avatarLibrary.public || []);
      var created = all.find(function(entry) { return entry.avatar === item.avatar; });
      if (created) selectAvatar(created, false);
      renderResultSuccessCard('数字人创建成功', renderAvatarSuccessBody(created || item));
      showMessage('数字人创建成功，已自动刷新“我的数字人”。', false);
    });
  }

  function handleVoiceTaskSuccess(data) {
    var item = data && data.item ? data.item : (data || {});
    if (item.voice && data && !data.voice) data.voice = item.voice;
    if (item.demo_url && data && !data.demo_url) data.demo_url = normalizeAssetUrl(item.demo_url);
    updateTaskStatus('已完成', 'success');
    if (item.voice && item.demo_url) state.voicePreviewMap[item.voice] = normalizeAssetUrl(item.demo_url);
    Promise.allSettled([loadVoices(true)]).then(function() {
      var found = findVoiceGroupByVoiceId(item.voice || '');
      if (found) selectVoiceVariant(found.group, found.style, false, normalizeAssetUrl(item.demo_url || ''));
      renderResultSuccessCard('声音创建成功', ''
        + '<span>任务已完成，新的声音已经刷新到“我的声音”。</span>'
        + '<div class="hifly-success-meta">声音 ID：' + escapeHtml(data.voice || '--') + '</div>'
        + (item.demo_url ? '<div class="hifly-success-audio"><audio controls preload="none" src="' + escapeHtml(normalizeAssetUrl(item.demo_url)) + '"></audio></div>' : ''));
      showMessage('声音创建成功，已自动刷新“我的声音”。', false);
    });
  }

  function pollTask(immediate) {
    clearPoll();
    if (!state.taskId || !state.taskType) return;
    state.pollTimer = setTimeout(function() {
      var path = state.taskType === 'video'
        ? '/api/hifly/my/video/task'
        : (state.taskType.indexOf('avatar') === 0 ? '/api/hifly/my/avatar/task' : '/api/hifly/my/voice/task');
      var pollRequest = requestCloud;
      pollRequest(path, { task_id: state.taskId, token: tokenValue() })
        .then(function(data) {
          var status = Number(data.status || 0);
          var statusText = data.status_text || '处理中';
          if (status === 3) {
            if (state.taskType === 'video' && data.video_url) {
              updateTaskStatus('已完成', 'success');
              renderResultVideo(data.video_url);
              var doneBilling = billingSummaryText(data.billing, data.duration);
              showMessage('数字人视频已生成完成，已自动保存到历史任务。' + (doneBilling ? ' ' + doneBilling : ''), false);
              refreshLobsterCredits();
              loadVideoHistory(true);
              return;
            }
            if (state.taskType.indexOf('avatar') === 0) {
              handleAvatarTaskSuccess(data);
              return;
            }
            handleVoiceTaskSuccess(data);
            return;
          }
          if (status === 4) {
            updateTaskStatus('失败', 'danger');
            renderResultPlaceholder(statusText || '任务失败', data.message || '请检查上传文件与 HiFly Token 后重试。', false);
            showMessage(data.message || 'HiFly 任务失败', true);
            refreshLobsterCredits();
            return;
          }
          updateTaskStatus(statusText, 'processing');
          renderResultPlaceholder(statusText, '任务已提交，系统会自动轮询当前状态。', true);
          pollTask(false);
        })
        .catch(function(err) {
          updateTaskStatus('查询失败', 'danger');
          renderResultPlaceholder('查询失败', err && err.message ? err.message : '任务查询失败', false);
          showMessage(err && err.message ? err.message : '任务查询失败', true);
        });
    }, immediate ? 0 : 8000);
  }

  function generateVideo() {
    var mode = getVideoCreateMode();
    var avatar = state.selectedAvatar && state.selectedAvatar.avatar ? state.selectedAvatar.avatar : ((($('hiflyAvatarInput') || {}).value || '').trim());
    var title = (($('hiflyTitleInput') || {}).value || '数字人口播').trim();
    var voice = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : ((($('hiflyVoiceInput') || {}).value || '').trim());
    var text = (($('hiflyScriptInput') || {}).value || '').trim();
    var audioFile = $('hiflyAudioDriveFile') && $('hiflyAudioDriveFile').files ? $('hiflyAudioDriveFile').files[0] : null;

    if (!avatar) return showMessage('请先从右侧选择一个数字人。', true);
    if (mode === 'tts') {
      if (!voice) return showMessage('请先从右侧选择一个声音。', true);
      if (!text) return showMessage('请先填写口播文案。', true);
    } else if (!audioFile) {
      return showMessage('请先上传驱动音频。', true);
    }

    setBusy(true);
    startTask('video', mode === 'audio' ? '声音驱动视频' : '数字人口播');
    renderResultPlaceholder('任务已提交', mode === 'audio' ? '正在提交声音驱动视频任务...' : '正在提交必火智能数字人口播任务...', true);
    showMessage(mode === 'audio' ? '正在提交声音驱动视频任务...' : '正在提交必火智能数字人口播任务...', false);

    if (mode === 'audio') {
      var formData = new FormData();
      formData.append('token', tokenValue());
      formData.append('title', title);
      formData.append('avatar', avatar);
      formData.append('aigc_flag', String(Number((($('hiflyAigcFlagSelect') || {}).value || 0))));
      formData.append('audio_duration', String(getAudioDriveDurationSeconds()));
      formData.append('file', audioFile);

      requestCloudForm('/api/hifly/my/video/create-by-audio-upload', formData).then(function(data) {
        startTask('video', '声音驱动视频', data.task_id || '');
        updateTaskStatus('等待中', 'processing');
        var createBilling = billingSummaryText(data.billing);
        renderResultPlaceholder('任务已提交', '正在生成声音驱动视频...' + (createBilling ? ' ' + createBilling : ''), true);
        showMessage('任务已提交，正在生成中。' + (createBilling ? ' ' + createBilling : ''), false);
        clearUploadSelection('hiflyAudioDriveUploadBox', 'hiflyAudioDriveFile', 'hiflyAudioDrivePreview', 'hiflyAudioDriveFileMeta', 'audio');
        refreshLobsterCredits();
        loadVideoHistory(true);
        pollTask(true);
      }).catch(function(err) {
        updateTaskStatus('提交失败', 'danger');
        renderResultPlaceholder('提交失败', err && err.message ? err.message : '任务提交失败', false);
        showMessage(err && err.message ? err.message : '任务提交失败', true);
      }).finally(function() {
        setBusy(false);
      });
      return;
    }

    requestCloud('/api/hifly/my/video/create-by-tts', {
      title: title,
      avatar: avatar,
      voice: voice,
      text: text,
      st_show: (($('hiflySubtitleCheck') || {}).checked ? 1 : 0),
      aigc_flag: Number((($('hiflyAigcFlagSelect') || {}).value || 0)),
      token: tokenValue()
    }).then(function(data) {
      startTask('video', '数字人口播', data.task_id || '');
      updateTaskStatus('等待中', 'processing');
      var createBilling = billingSummaryText(data.billing);
      renderResultPlaceholder('任务已提交', '正在生成数字人口播视频...' + (createBilling ? ' ' + createBilling : ''), true);
      showMessage('任务已提交，正在生成中。' + (createBilling ? ' ' + createBilling : ''), false);
      refreshLobsterCredits();
      loadVideoHistory(true);
      pollTask(true);
    }).catch(function(err) {
      updateTaskStatus('提交失败', 'danger');
      renderResultPlaceholder('提交失败', err && err.message ? err.message : '任务提交失败', false);
      showMessage(err && err.message ? err.message : '任务提交失败', true);
    }).finally(function() {
      setBusy(false);
    });
  }

  function submitAvatarImageCreate() {
    var file = $('hiflyAvatarImageFile') && $('hiflyAvatarImageFile').files ? $('hiflyAvatarImageFile').files[0] : null;
    var title = (($('hiflyAvatarImageName') || {}).value || '').trim();
    var agree = !!(($('hiflyAvatarImageAgree') || {}).checked);
    var checked = document.querySelector('input[name="hiflyAvatarImageModel"]:checked');
    var model = checked ? Number(checked.value || 2) : 2;
    if (!title) return showMessage('请填写数字人名称。', true);
    if (!file) return showMessage('请先上传图片。', true);
    if (!agree) return showMessage('请先勾选同意承诺。', true);

    var formData = new FormData();
    formData.append('token', tokenValue());
    formData.append('title', title);
    formData.append('model', String(model));
    formData.append('aigc_flag', String(Number((($('hiflyAigcFlagSelect') || {}).value || 0))));
    formData.append('file', file);

    setBusy(true);
    closeModal('hiflyAvatarModeModal');
    closeModal('hiflyAvatarImageModal');
    startTask('avatar-image', '图片数字人创建');
    renderResultPlaceholder('任务已提交', '正在上传图片并创建数字人...', true);
    showMessage('正在提交图片数字人创建任务...', false);

    requestCloudForm('/api/hifly/my/avatar/create-by-image-upload', formData)
      .then(function(data) {
        data = data && data.item ? Object.assign({}, data, { task_id: data.item.task_id || data.task_id || '' }) : data;
        resetCreateForms();
        startTask('avatar-image', '图片数字人创建', data.task_id || '');
        updateTaskStatus('等待中', 'processing');
        pollTask(true);
      })
      .catch(function(err) {
        updateTaskStatus('提交失败', 'danger');
        renderResultPlaceholder('提交失败', err && err.message ? err.message : '创建数字人失败', false);
        showMessage(err && err.message ? err.message : '创建数字人失败', true);
      })
      .finally(function() {
        setBusy(false);
      });
  }

  function submitAvatarVideoCreate() {
    var file = $('hiflyAvatarVideoFile') && $('hiflyAvatarVideoFile').files ? $('hiflyAvatarVideoFile').files[0] : null;
    var title = (($('hiflyAvatarVideoName') || {}).value || '').trim();
    var agree = !!(($('hiflyAvatarVideoAgree') || {}).checked);
    if (!title) return showMessage('请填写数字人名称。', true);
    if (!file) return showMessage('请先上传视频。', true);
    if (!agree) return showMessage('请先勾选同意承诺。', true);

    var formData = new FormData();
    formData.append('token', tokenValue());
    formData.append('title', title);
    formData.append('aigc_flag', String(Number((($('hiflyAigcFlagSelect') || {}).value || 0))));
    formData.append('file', file);

    setBusy(true);
    closeModal('hiflyAvatarModeModal');
    closeModal('hiflyAvatarVideoModal');
    startTask('avatar-video', '视频数字人创建');
    renderResultPlaceholder('任务已提交', '正在上传视频并创建数字人...', true);
    showMessage('正在提交视频数字人创建任务...', false);

    requestCloudForm('/api/hifly/my/avatar/create-by-video-upload', formData)
      .then(function(data) {
        data = data && data.item ? Object.assign({}, data, { task_id: data.item.task_id || data.task_id || '' }) : data;
        resetCreateForms();
        startTask('avatar-video', '视频数字人创建', data.task_id || '');
        updateTaskStatus('等待中', 'processing');
        pollTask(true);
      })
      .catch(function(err) {
        updateTaskStatus('提交失败', 'danger');
        renderResultPlaceholder('提交失败', err && err.message ? err.message : '创建数字人失败', false);
        showMessage(err && err.message ? err.message : '创建数字人失败', true);
      })
      .finally(function() {
        setBusy(false);
      });
  }

  function submitVoiceCreate() {
    var file = $('hiflyVoiceCreateFile') && $('hiflyVoiceCreateFile').files ? $('hiflyVoiceCreateFile').files[0] : null;
    var title = (($('hiflyVoiceCreateName') || {}).value || '').trim();
    var language = (($('hiflyVoiceLanguageSelect') || {}).value || 'zh').trim();
    var agree = !!(($('hiflyVoiceCreateAgree') || {}).checked);
    if (!title) return showMessage('请填写声音名称。', true);
    if (!file) return showMessage('请先上传声音样本。', true);
    if (!agree) return showMessage('请先勾选同意承诺。', true);

    var formData = new FormData();
    formData.append('token', tokenValue());
    formData.append('title', title);
    formData.append('languages', language || 'zh');
    formData.append('voice_type', '8');
    formData.append('file', file);

    setBusy(true);
    closeModal('hiflyVoiceCreateModal');
    startTask('voice-create', '声音创建');
    renderResultPlaceholder('任务已提交', '正在上传声音并创建克隆音色...', true);
    showMessage('正在提交声音创建任务...', false);

    requestCloudForm('/api/hifly/my/voice/create-upload', formData)
      .then(function(data) {
        data = data && data.item ? Object.assign({}, data, { task_id: data.item.task_id || data.task_id || '' }) : data;
        resetCreateForms();
        startTask('voice-create', '声音创建', data.task_id || '');
        updateTaskStatus('等待中', 'processing');
        pollTask(true);
      })
      .catch(function(err) {
        updateTaskStatus('提交失败', 'danger');
        renderResultPlaceholder('提交失败', err && err.message ? err.message : '创建声音失败', false);
        showMessage(err && err.message ? err.message : '创建声音失败', true);
      })
      .finally(function() {
        setBusy(false);
      });
  }

  function bindEvents() {
    if ($('hiflyBackBtn')) $('hiflyBackBtn').addEventListener('click', function() {
      if (typeof window._ensureSkillStoreVisible === 'function') window._ensureSkillStoreVisible();
      try { location.hash = 'skill-store'; } catch (e) {}
    });
    if ($('hiflyRefreshLibraryBtn')) $('hiflyRefreshLibraryBtn').addEventListener('click', function() {
      loadAvatarLibrary().catch(function(err) { showMessage(err && err.message ? err.message : '数字人列表刷新失败', true); });
    });
    if ($('hiflyLoadVoicesBtn')) $('hiflyLoadVoicesBtn').addEventListener('click', function() {
      loadVoices().catch(function(err) { showMessage(err && err.message ? err.message : '声音列表刷新失败', true); });
    });
    if ($('hiflyGenerateBtn')) $('hiflyGenerateBtn').addEventListener('click', generateVideo);
    if ($('hiflyVideoModeTtsBtn')) $('hiflyVideoModeTtsBtn').addEventListener('click', function() { setVideoCreateMode('tts'); });
    if ($('hiflyVideoModeAudioBtn')) $('hiflyVideoModeAudioBtn').addEventListener('click', function() { setVideoCreateMode('audio'); });
    if ($('hiflyAvatarLibraryTabBtn')) $('hiflyAvatarLibraryTabBtn').addEventListener('click', function() { setActiveView('avatar'); });
    if ($('hiflyVoiceLibraryTabBtn')) $('hiflyVoiceLibraryTabBtn').addEventListener('click', function() { setActiveView('voice'); });
    if ($('hiflyResultTabBtn')) $('hiflyResultTabBtn').addEventListener('click', function() {
      setActiveView('result');
      loadVideoHistory(true);
    });
    if ($('hiflyVideoHistoryRefreshBtn')) $('hiflyVideoHistoryRefreshBtn').addEventListener('click', function() {
      loadVideoHistory();
    });
    if ($('hiflyResultBackBtn')) $('hiflyResultBackBtn').addEventListener('click', function() { setActiveView('avatar'); });
    if ($('hiflyOpenAvatarLibraryBtn')) $('hiflyOpenAvatarLibraryBtn').addEventListener('click', function() { setActiveView('avatar'); });
    if ($('hiflyOpenVoiceLibraryBtn')) $('hiflyOpenVoiceLibraryBtn').addEventListener('click', function() { setActiveView('voice'); });
    if ($('hiflyAvatarSearchInput')) $('hiflyAvatarSearchInput').addEventListener('input', function(ev) {
      state.avatarSearch = ev.target.value || '';
      renderAvatarLibrary();
    });
    if ($('hiflyVoiceSearchInput')) $('hiflyVoiceSearchInput').addEventListener('input', function(ev) {
      state.voiceSearch = ev.target.value || '';
      renderVoiceLibrary();
    });

    [
      ['hiflyMineMoreBtn', 'avatarMine'],
      ['hiflyMineVoiceMoreBtn', 'voiceMine'],
      ['hiflyPublicVoiceMoreBtn', 'voicePublic']
    ].forEach(function(pair) {
      var btn = $(pair[0]);
      if (!btn) return;
      btn.addEventListener('click', function() {
        state.libraryExpanded[pair[1]] = !state.libraryExpanded[pair[1]];
        if (pair[1].indexOf('voice') === 0) renderVoiceLibrary();
        else renderAvatarLibrary();
      });
    });
    if ($('hiflyPublicMoreBtn')) $('hiflyPublicMoreBtn').addEventListener('click', function() {
      loadMorePublicAvatars();
    });

    if ($('hiflyOpenAvatarCreateBtn')) $('hiflyOpenAvatarCreateBtn').addEventListener('click', function() {
      resetCreateForms();
      openModal('hiflyAvatarModeModal');
    });
    if ($('hiflyOpenVoiceCreateBtn')) $('hiflyOpenVoiceCreateBtn').addEventListener('click', function() {
      resetCreateForms();
      openModal('hiflyVoiceCreateModal');
    });
    if ($('hiflyCreateVoiceBtn')) $('hiflyCreateVoiceBtn').addEventListener('click', function() {
      resetCreateForms();
      openModal('hiflyVoiceCreateModal');
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-modal-close]'), function(btn) {
      btn.addEventListener('click', function() {
        closeModal(btn.getAttribute('data-modal-close'));
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-avatar-mode]'), function(btn) {
      btn.addEventListener('click', function() {
        var mode = btn.getAttribute('data-avatar-mode');
        closeModal('hiflyAvatarModeModal');
        if (mode === 'video') openModal('hiflyAvatarVideoModal');
        else openModal('hiflyAvatarImageModal');
      });
    });

    wireFileBox('hiflyAvatarImageUploadBox', 'hiflyAvatarImageFile', 'hiflyAvatarImageFileMeta', 'hiflyAvatarImagePreview', 'image');
    wireFileBox('hiflyAvatarVideoUploadBox', 'hiflyAvatarVideoFile', 'hiflyAvatarVideoFileMeta', 'hiflyAvatarVideoPreview', 'video');
    wireFileBox('hiflyVoiceUploadBox', 'hiflyVoiceCreateFile', 'hiflyVoiceFileMeta', 'hiflyVoiceFilePreview', 'audio');
    wireFileBox('hiflyAudioDriveUploadBox', 'hiflyAudioDriveFile', 'hiflyAudioDriveFileMeta', 'hiflyAudioDrivePreview', 'audio');

    if ($('hiflyAvatarImageSubmitBtn')) $('hiflyAvatarImageSubmitBtn').addEventListener('click', submitAvatarImageCreate);
    if ($('hiflyAvatarVideoSubmitBtn')) $('hiflyAvatarVideoSubmitBtn').addEventListener('click', submitAvatarVideoCreate);
    if ($('hiflyVoiceSubmitBtn')) $('hiflyVoiceSubmitBtn').addEventListener('click', submitVoiceCreate);
    ['hiflyAvatarImageAgree', 'hiflyAvatarVideoAgree', 'hiflyVoiceCreateAgree'].forEach(function(id) {
      if ($(id)) $(id).addEventListener('change', syncCreateSubmitStates);
    });
    syncCreateSubmitStates();
  }

  function bootstrapData() {
    renderSelectedAvatar();
    renderSelectedVoice();
    renderResultPlaceholder('等待生成', '提交后这里会自动显示任务进度和最终结果。', false);
    updateTaskStatus('等待提交', 'idle');
    updateTaskKind(state.taskKindLabel || '未开始');
    syncVideoCreateModeUI();

    return Promise.allSettled([
      loadAvatarLibrary(true),
      loadVoices(true)
    ]).then(function(results) {
      var rejected = results.filter(function(item) { return item.status === 'rejected'; });
      renderSelectedAvatar();
      renderSelectedVoice();
      renderAvatarLibrary();
      renderVoiceLibrary();
      if (rejected.length === results.length) {
        throw rejected[0].reason || new Error('初始化必火智能数字人页面失败');
      }
      if (state.avatarLibrary.using_default_token || state.voiceLibrary.using_default_token) {
        showMessage('当前已使用平台托管服务自动加载资源。', false);
      } else {
        showMessage('必火智能数字人资源已加载完成。', false);
      }
      return results;
    });
  }

  function ensureStyles() {
    var oldStyle = $('hiflyDynamicStyle');
    if (oldStyle && oldStyle.getAttribute('data-version') === HIFLY_STYLE_VERSION) return;
    if (oldStyle && oldStyle.parentNode) oldStyle.parentNode.removeChild(oldStyle);
    var style = document.createElement('style');
    style.id = 'hiflyDynamicStyle';
    style.setAttribute('data-version', HIFLY_STYLE_VERSION);
    style.textContent = ''
      + '#content-hifly-digital-human .hifly-shell{display:grid;grid-template-columns:minmax(320px,390px) minmax(0,1fr);gap:1rem;align-items:start;}'
      + '#content-hifly-digital-human .hifly-sidebar{display:flex;flex-direction:column;gap:1rem;position:sticky;top:0.8rem;}'
      + '#content-hifly-digital-human .hifly-main{display:flex;flex-direction:column;gap:1rem;min-width:0;}'
      + '#content-hifly-digital-human .hifly-panel-head{display:flex;justify-content:space-between;gap:0.75rem;align-items:flex-start;flex-wrap:wrap;margin-bottom:0.7rem;}'
      + '#content-hifly-digital-human .hifly-head-actions,#content-hifly-digital-human .hifly-toolbar-actions{display:flex;gap:0.5rem;flex-wrap:wrap;}'
      + '#content-hifly-digital-human .hifly-mode-switch{display:flex;gap:0.65rem;flex-wrap:wrap;}'
      + '#content-hifly-digital-human .hifly-mode-chip{appearance:none;border:1px solid rgba(99,102,241,0.18);background:#fff;border-radius:999px;padding:0.7rem 1rem;font-weight:700;color:#475569;cursor:pointer;transition:all .18s ease;box-shadow:0 8px 24px rgba(15,23,42,0.05);}'
      + '#content-hifly-digital-human .hifly-mode-chip.is-active{background:linear-gradient(135deg,#6366f1,#4f46e5);color:#fff;border-color:transparent;box-shadow:0 12px 28px rgba(79,70,229,0.24);}'
      + '#content-hifly-digital-human .btn.is-disabled{opacity:0.55;cursor:not-allowed;pointer-events:none;}'
      + '#content-hifly-digital-human .hifly-selected-avatar,#content-hifly-digital-human .hifly-selected-voice{border:1px solid rgba(26,39,68,0.08);border-radius:20px;background:#fff;box-shadow:0 18px 38px rgba(26,39,68,0.08);padding:0.9rem;}'
      + '#content-hifly-digital-human .hifly-selected-avatar{display:grid;grid-template-columns:124px minmax(0,1fr);gap:0.9rem;align-items:start;}'
      + '#content-hifly-digital-human .hifly-selected-voice{display:grid;grid-template-columns:112px minmax(0,1fr);gap:0.9rem;align-items:start;}'
      + '#content-hifly-digital-human .hifly-selected-avatar.is-empty,#content-hifly-digital-human .hifly-selected-voice.is-empty{display:block;}'
      + '#content-hifly-digital-human .hifly-selected-empty strong,#content-hifly-digital-human .hifly-selected-empty span,#content-hifly-digital-human .hifly-result-status-copy strong,#content-hifly-digital-human .hifly-result-status-copy span{display:block;}'
      + '#content-hifly-digital-human .hifly-selected-empty span{margin-top:0.36rem;color:#667189;font-size:0.84rem;line-height:1.65;}'
      + '#content-hifly-digital-human .hifly-selected-avatar-cover{position:relative;aspect-ratio:1/1;border-radius:18px;overflow:hidden;background:linear-gradient(135deg, rgba(124,94,255,0.22), rgba(14,165,233,0.22));}'
      + '#content-hifly-digital-human .hifly-selected-voice-cover{position:relative;aspect-ratio:1/1;border-radius:18px;overflow:hidden;background:linear-gradient(135deg, rgba(124,94,255,0.18), rgba(14,165,233,0.18));}'
      + '#content-hifly-digital-human .hifly-selected-avatar-cover img,#content-hifly-digital-human .hifly-avatar-card-cover img,#content-hifly-digital-human .hifly-success-visual-media{width:100%;height:100%;object-fit:cover;display:block;}'
      + '#content-hifly-digital-human .hifly-selected-avatar-cover video,#content-hifly-digital-human .hifly-avatar-card-cover video{width:100%;height:100%;object-fit:cover;display:block;background:#0f172a;}'
      + '#content-hifly-digital-human .hifly-selected-voice-cover img,#content-hifly-digital-human .hifly-voice-card-thumb img{width:100%;height:100%;object-fit:cover;display:block;}'
      + '#content-hifly-digital-human .hifly-selected-copy{min-width:0;}'
      + '#content-hifly-digital-human .hifly-selected-title-row{display:flex;gap:0.6rem;justify-content:space-between;align-items:flex-start;}'
      + '#content-hifly-digital-human .hifly-selected-title-row strong,#content-hifly-digital-human .hifly-selected-voice-head strong{font-size:1rem;color:#1f2b42;line-height:1.4;}'
      + '#content-hifly-digital-human .hifly-selected-title-row span{font-size:0.76rem;color:#7c5eff;padding:0.35rem 0.62rem;border-radius:999px;background:rgba(124,94,255,0.10);font-weight:700;white-space:nowrap;}'
      + '#content-hifly-digital-human .hifly-selected-style-label{margin-top:0.45rem;font-size:0.82rem;color:#4c5a74;font-weight:600;}'
      + '#content-hifly-digital-human .hifly-selected-tags{margin-top:0.7rem;display:flex;gap:0.42rem;flex-wrap:wrap;}'
      + '#content-hifly-digital-human .hifly-mini-tag{display:inline-flex;align-items:center;padding:0.28rem 0.56rem;border-radius:999px;background:rgba(19,191,159,0.10);color:#157a66;font-size:0.74rem;font-weight:600;}'
      + '#content-hifly-digital-human .hifly-selected-voice-head{display:flex;justify-content:space-between;gap:0.6rem;align-items:flex-start;}'
      + '#content-hifly-digital-human .hifly-selected-audio{margin-top:0.75rem;}'
      + '#content-hifly-digital-human .hifly-selected-audio audio,#content-hifly-digital-human .hifly-voice-audio audio,#content-hifly-digital-human .hifly-success-audio audio{width:100%;}'
      + '#content-hifly-digital-human .hifly-preview-play-btn{border:none;width:100%;min-height:42px;border-radius:999px;background:rgba(15,23,42,0.04);color:#25324a;display:inline-flex;align-items:center;justify-content:center;gap:0.5rem;font-size:0.86rem;font-weight:700;cursor:pointer;transition:all 0.18s ease;}'
      + '#content-hifly-digital-human .hifly-preview-play-btn:hover{background:rgba(124,94,255,0.10);color:#5d4cdc;}'
      + '#content-hifly-digital-human .hifly-preview-play-btn.is-playing{background:rgba(124,94,255,0.14);color:#5d4cdc;box-shadow:inset 0 0 0 1px rgba(124,94,255,0.18);}'
      + '#content-hifly-digital-human .hifly-preview-play-icon{width:22px;height:22px;border-radius:999px;background:rgba(124,94,255,0.14);display:inline-flex;align-items:center;justify-content:center;font-size:0.72rem;line-height:1;}'
      + '#content-hifly-digital-human .hifly-audio-empty{padding:0.55rem 0.65rem;border-radius:14px;background:rgba(15,23,42,0.05);color:#738199;font-size:0.8rem;}'
      + '#content-hifly-digital-human .hifly-main-toolbar{display:flex;align-items:center;justify-content:space-between;gap:0.8rem;margin-bottom:0.8rem;flex-wrap:wrap;}'
      + '#content-hifly-digital-human .hifly-tab-row{display:flex;gap:0.65rem;flex-wrap:wrap;}'
      + '#content-hifly-digital-human .hifly-tab-btn{border:none;padding:0.58rem 0.98rem;border-radius:999px;background:rgba(124,94,255,0.08);color:#665a92;font-weight:700;cursor:pointer;transition:all 0.18s ease;}'
      + '#content-hifly-digital-human .hifly-tab-btn.is-active{background:linear-gradient(135deg,#7c5eff,#6a8cff);color:#fff;box-shadow:0 14px 30px rgba(108,99,255,0.22);}'
      + '#content-hifly-digital-human .hifly-library-toolbar{display:flex;gap:0.8rem;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:1rem;}'
      + '#content-hifly-digital-human .hifly-search{flex:1 1 260px;min-width:220px;border:1px solid rgba(124,94,255,0.16);border-radius:14px;padding:0.82rem 0.9rem;background:#fff;}'
      + '#content-hifly-digital-human .hifly-library-tip{font-size:0.82rem;color:#68748f;line-height:1.65;padding:0.8rem 0.92rem;border-radius:16px;background:linear-gradient(180deg, rgba(255,255,255,0.94), rgba(246,248,255,0.92));border:1px solid rgba(124,94,255,0.10);margin-bottom:1rem;white-space:normal;}'
      + '#content-hifly-digital-human .hifly-library-section + .hifly-library-section{margin-top:1.2rem;}'
      + '#content-hifly-digital-human .hifly-section-head{display:flex;align-items:center;justify-content:space-between;gap:0.75rem;margin-bottom:0.82rem;flex-wrap:wrap;}'
      + '#content-hifly-digital-human .hifly-section-head h4{margin:0;font-size:1.08rem;color:#1f2b42;}'
      + '#content-hifly-digital-human .hifly-section-head p{margin:0.18rem 0 0;font-size:0.8rem;color:#6d7791;}'
      + '#content-hifly-digital-human .hifly-section-count{display:inline-flex;align-items:center;gap:0.32rem;padding:0.38rem 0.72rem;border-radius:999px;background:rgba(124,94,255,0.10);color:#6a54e0;font-size:0.78rem;font-weight:700;}'
      + '#content-hifly-digital-human .hifly-avatar-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:0.92rem;}'
      + '#content-hifly-digital-human .hifly-voice-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:0.92rem;}'
      + '#content-hifly-digital-human .hifly-avatar-card,#content-hifly-digital-human .hifly-voice-card{display:flex;flex-direction:column;border-radius:24px;background:#fff;overflow:hidden;border:1px solid rgba(26,39,68,0.06);box-shadow:0 18px 38px rgba(26,39,68,0.08);transition:transform 0.18s ease,box-shadow 0.18s ease,border-color 0.18s ease;}'
      + '#content-hifly-digital-human .hifly-avatar-card:hover,#content-hifly-digital-human .hifly-voice-card:hover{transform:translateY(-2px);box-shadow:0 20px 44px rgba(31,41,55,0.12);}'
      + '#content-hifly-digital-human .hifly-avatar-card.is-selected,#content-hifly-digital-human .hifly-voice-card.is-selected{border-color:rgba(124,94,255,0.38);box-shadow:0 24px 50px rgba(108,99,255,0.16);}'
      + '#content-hifly-digital-human .hifly-avatar-card-cover{position:relative;aspect-ratio:1/1;overflow:hidden;background:linear-gradient(135deg, rgba(124,94,255,0.20), rgba(14,165,233,0.20));}'
      + '#content-hifly-digital-human .hifly-avatar-card-badge,#content-hifly-digital-human .hifly-avatar-card-count,#content-hifly-digital-human .hifly-section-chip{position:absolute;display:inline-flex;align-items:center;padding:0.3rem 0.62rem;border-radius:999px;font-size:0.73rem;font-weight:700;backdrop-filter:blur(8px);}'
      + '#content-hifly-digital-human .hifly-avatar-card-badge{left:0.72rem;top:0.72rem;background:rgba(255,255,255,0.92);color:#6a54e0;}'
      + '#content-hifly-digital-human .hifly-avatar-card-count{right:0.72rem;bottom:0.72rem;background:rgba(255,255,255,0.94);color:#6a54e0;}'
      + '#content-hifly-digital-human .hifly-section-chip{right:0.65rem;top:0.65rem;color:#6b4de6;background:rgba(255,255,255,0.92);position:absolute;}'
      + '#content-hifly-digital-human .hifly-section-chip.is-inline{position:static;white-space:nowrap;padding:0.28rem 0.56rem;}'
      + '#content-hifly-digital-human .hifly-avatar-card-body,#content-hifly-digital-human .hifly-voice-card{padding:0.9rem;}'
      + '#content-hifly-digital-human .hifly-avatar-card-body{display:flex;flex-direction:column;gap:0.6rem;flex:1;}'
      + '#content-hifly-digital-human .hifly-avatar-card-main{display:flex;flex-direction:column;gap:0.5rem;min-height:0;}'
      + '#content-hifly-digital-human .hifly-avatar-card-title,#content-hifly-digital-human .hifly-voice-card-title{font-size:1rem;font-weight:700;color:#202a3f;line-height:1.4;}'
      + '#content-hifly-digital-human .hifly-avatar-card-title{min-height:2.8em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}'
      + '#content-hifly-digital-human .hifly-avatar-card-id,#content-hifly-digital-human .hifly-voice-card-id{margin-top:0.38rem;font-size:0.76rem;color:#7c879f;word-break:break-all;}'
      + '#content-hifly-digital-human .hifly-avatar-card-tags{display:flex;gap:0.38rem;flex-wrap:wrap;min-height:1.8rem;align-items:flex-start;}'
      + '#content-hifly-digital-human .hifly-avatar-card-meta{display:flex;align-items:center;justify-content:flex-start;font-size:0.8rem;color:#6b7280;}'
      + '#content-hifly-digital-human .hifly-avatar-card-actions{display:grid;grid-template-columns:1fr 1fr;gap:0.55rem;margin-top:auto;}'
      + '#content-hifly-digital-human .hifly-card-tag{display:inline-flex;align-items:center;padding:0.24rem 0.5rem;border-radius:999px;background:rgba(15,23,42,0.06);color:#61708a;font-size:0.72rem;}'
      + '#content-hifly-digital-human .hifly-avatar-pick-btn,#content-hifly-digital-human .hifly-voice-pick-btn{width:100%;margin-top:0.75rem;justify-content:center;}'
      + '#content-hifly-digital-human .hifly-avatar-card-actions .hifly-avatar-pick-btn{margin-top:0;}'
      + '#content-hifly-digital-human .hifly-voice-card-top{display:grid;grid-template-columns:74px minmax(0,1fr);gap:0.82rem;align-items:start;}'
      + '#content-hifly-digital-human .hifly-voice-card-thumb{width:74px;height:74px;border-radius:20px;overflow:hidden;background:linear-gradient(135deg, rgba(124,94,255,0.18), rgba(14,165,233,0.18));box-shadow:inset 0 0 0 1px rgba(255,255,255,0.32);}'
      + '#content-hifly-digital-human .hifly-voice-card-main{min-width:0;}'
      + '#content-hifly-digital-human .hifly-voice-card-head{display:flex;justify-content:space-between;gap:0.7rem;align-items:flex-start;}'
      + '#content-hifly-digital-human .hifly-voice-style-list{margin-top:0.75rem;display:flex;flex-direction:column;gap:0.48rem;}'
      + '#content-hifly-digital-human .hifly-voice-style-btn{display:flex;align-items:center;justify-content:space-between;gap:0.75rem;width:100%;padding:0.68rem 0.78rem;border:none;border-radius:16px;background:rgba(15,23,42,0.04);cursor:pointer;text-align:left;transition:all 0.18s ease;}'
      + '#content-hifly-digital-human .hifly-voice-style-btn:hover{background:rgba(124,94,255,0.08);}'
      + '#content-hifly-digital-human .hifly-voice-style-btn.is-active{background:rgba(124,94,255,0.12);box-shadow:inset 0 0 0 1px rgba(124,94,255,0.18);}'
      + '#content-hifly-digital-human .hifly-voice-style-copy{display:flex;align-items:center;gap:0.55rem;min-width:0;}'
      + '#content-hifly-digital-human .hifly-voice-style-play{width:18px;height:18px;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;background:rgba(124,94,255,0.14);color:#6a54e0;font-size:0.72rem;font-weight:700;flex:0 0 auto;}'
      + '#content-hifly-digital-human .hifly-voice-style-text{min-width:0;font-size:0.84rem;color:#25324a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
      + '#content-hifly-digital-human .hifly-voice-style-state{font-size:0.72rem;color:#6d7791;white-space:nowrap;}'
      + '#content-hifly-digital-human .hifly-voice-audio{margin-top:0.75rem;}'
      + '#content-hifly-digital-human .hifly-section-empty{display:none;padding:1rem 1.05rem;border-radius:18px;border:1px dashed rgba(124,94,255,0.20);background:rgba(249,247,255,0.82);color:#68748f;font-size:0.84rem;line-height:1.7;}'
      + '#content-hifly-digital-human .hifly-more-row{margin-top:0.92rem;display:flex;justify-content:center;}'
      + '#content-hifly-digital-human .hifly-result-panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap;margin-bottom:0.85rem;}'
      + '#content-hifly-digital-human .hifly-result-meta{display:flex;flex-wrap:wrap;gap:0.65rem;margin-top:0.58rem;}'
      + '#content-hifly-digital-human .hifly-result-pill{display:inline-flex;align-items:center;gap:0.38rem;padding:0.42rem 0.74rem;border-radius:999px;background:rgba(124,94,255,0.10);color:#6a54e0;font-size:0.78rem;font-weight:700;}'
      + '#content-hifly-digital-human #hiflyTaskStatusText[data-tone="success"]{color:#1f8f5f;background:rgba(31,143,95,0.12);}'
      + '#content-hifly-digital-human #hiflyTaskStatusText[data-tone="danger"]{color:#c23b3b;background:rgba(194,59,59,0.12);}'
      + '#content-hifly-digital-human #hiflyTaskStatusText[data-tone="processing"]{color:#5c57d8;background:rgba(92,87,216,0.12);}'
      + '#content-hifly-digital-human .hifly-result-status-head{display:flex;gap:0.74rem;align-items:flex-start;}'
      + '#content-hifly-digital-human .hifly-result-spinner{flex:0 0 auto;width:34px;height:34px;border-radius:999px;border:3px solid rgba(124,94,255,0.14);border-top-color:#7c5eff;animation:viral-spin 0.85s linear infinite;}'
      + '#content-hifly-digital-human .hifly-success-card{width:min(92%,640px);padding:1.2rem 1.25rem;border-radius:22px;background:#fff;border:1px solid rgba(124,94,255,0.14);box-shadow:0 18px 42px rgba(31,41,55,0.10);}'
      + '#content-hifly-digital-human .hifly-success-card strong{display:block;font-size:1.04rem;color:#20314d;}'
      + '#content-hifly-digital-human .hifly-success-card span{display:block;margin-top:0.5rem;color:#5f6f87;line-height:1.65;}'
      + '#content-hifly-digital-human .hifly-success-meta{margin-top:0.8rem;padding:0.72rem 0.84rem;border-radius:14px;background:rgba(124,94,255,0.06);color:#5d4cdc;font-size:0.83rem;word-break:break-all;}'
      + '#content-hifly-digital-human .hifly-success-visual{margin-top:0.9rem;border-radius:18px;overflow:hidden;aspect-ratio:1/1;background:linear-gradient(135deg, rgba(124,94,255,0.18), rgba(14,165,233,0.18));}'
      + '#content-hifly-digital-human .hifly-success-visual video{width:100%;height:100%;object-fit:cover;display:block;background:#0f172a;}'
      + '#content-hifly-digital-human .hifly-modal{position:fixed;inset:0;z-index:1200;}'
      + '#content-hifly-digital-human .hifly-modal-backdrop{position:absolute;inset:0;background:rgba(15,23,42,0.58);backdrop-filter:blur(3px);}'
      + '#content-hifly-digital-human .hifly-modal-card{position:relative;width:min(92vw,760px);max-height:92vh;overflow:auto;margin:4vh auto 0;border-radius:28px;background:#fff;box-shadow:0 32px 80px rgba(15,23,42,0.28);}'
      + '#content-hifly-digital-human .hifly-mode-card{width:min(92vw,720px);}'
      + '#content-hifly-digital-human .hifly-modal-head{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:1.15rem 1.25rem;border-bottom:1px solid rgba(15,23,42,0.08);}'
      + '#content-hifly-digital-human .hifly-modal-title{margin:0;font-size:1.55rem;color:#111827;}'
      + '#content-hifly-digital-human .hifly-modal-close{border:none;background:transparent;font-size:2rem;line-height:1;color:#64748b;cursor:pointer;}'
      + '#content-hifly-digital-human .hifly-modal-body{padding:1.25rem;display:flex;flex-direction:column;gap:1rem;}'
      + '#content-hifly-digital-human .hifly-modal-foot{display:flex;justify-content:flex-end;gap:0.75rem;padding:1rem 1.25rem 1.25rem;border-top:1px solid rgba(15,23,42,0.08);}'
      + '#content-hifly-digital-human .hifly-mode-list{display:flex;flex-direction:column;gap:1rem;padding:1.25rem;}'
      + '#content-hifly-digital-human .hifly-mode-option{display:flex;align-items:center;gap:1rem;padding:1.2rem 1.25rem;border-radius:24px;border:1px solid rgba(15,23,42,0.08);background:#fff;cursor:pointer;text-align:left;box-shadow:0 12px 30px rgba(15,23,42,0.06);}'
      + '#content-hifly-digital-human .hifly-mode-option:hover{transform:translateY(-1px);box-shadow:0 16px 34px rgba(15,23,42,0.10);}'
      + '#content-hifly-digital-human .hifly-mode-icon{width:72px;height:72px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#6327ff,#c30099);color:#fff;font-weight:800;font-size:1.2rem;flex:0 0 auto;}'
      + '#content-hifly-digital-human .hifly-mode-copy{display:flex;flex-direction:column;gap:0.28rem;min-width:0;}'
      + '#content-hifly-digital-human .hifly-mode-copy strong{font-size:1rem;color:#111827;}'
      + '#content-hifly-digital-human .hifly-mode-copy span{color:#4b5563;line-height:1.55;}'
      + '#content-hifly-digital-human .hifly-recommend-chip{margin-left:auto;display:inline-flex;align-items:center;padding:0.42rem 0.82rem;border-radius:999px;background:rgba(124,94,255,0.12);color:#7c3aed;font-weight:700;white-space:nowrap;}'
      + '#content-hifly-digital-human .hifly-requirement-box{padding:1rem;border-radius:18px;background:rgba(244,247,255,0.88);border:1px solid rgba(124,94,255,0.10);}'
      + '#content-hifly-digital-human .hifly-requirement-box strong{display:block;color:#1f2b42;}'
      + '#content-hifly-digital-human .hifly-requirement-grid{margin-top:0.7rem;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0.75rem;color:#4b5563;font-size:0.92rem;}'
      + '#content-hifly-digital-human .hifly-requirement-list{margin:0.7rem 0 0;padding-left:1.2rem;color:#4b5563;display:grid;gap:0.55rem;line-height:1.65;}'
      + '#content-hifly-digital-human .hifly-upload-box{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0.42rem;min-height:220px;padding:1rem;border-radius:22px;border:1px dashed rgba(124,94,255,0.25);background:linear-gradient(180deg, rgba(255,255,255,0.98), rgba(248,246,255,0.92));cursor:pointer;}'
      + '#content-hifly-digital-human .hifly-upload-box.is-dragover{border-color:#7c5eff;background:rgba(124,94,255,0.06);}'
      + '#content-hifly-digital-human .hifly-upload-box strong{font-size:1.02rem;color:#1f2b42;}'
      + '#content-hifly-digital-human .hifly-upload-box span{color:#667189;line-height:1.6;}'
      + '#content-hifly-digital-human .hifly-upload-icon{width:56px;height:56px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;background:rgba(124,94,255,0.12);color:#7c5eff;font-size:1.55rem;font-weight:800;}'
      + '#content-hifly-digital-human .hifly-upload-preview{display:none;border-radius:22px;background:#f5f7fb;border:1px solid rgba(124,94,255,0.10);overflow:hidden;}'
      + '#content-hifly-digital-human .hifly-upload-preview-inner{position:relative;min-height:240px;display:flex;align-items:center;justify-content:center;background:linear-gradient(180deg, rgba(248,250,255,0.96), rgba(239,243,255,0.92));padding:1rem;}'
      + '#content-hifly-digital-human .hifly-upload-preview-inner img{max-width:100%;max-height:480px;display:block;border-radius:18px;object-fit:contain;box-shadow:0 18px 42px rgba(15,23,42,0.10);}'
      + '#content-hifly-digital-human .hifly-upload-preview-inner video{width:100%;max-height:420px;border-radius:18px;background:#0f172a;box-shadow:0 18px 42px rgba(15,23,42,0.14);}'
      + '#content-hifly-digital-human .hifly-upload-preview-inner audio{width:min(520px,100%);}'
      + '#content-hifly-digital-human .hifly-upload-preview-inner-image{padding:1.2rem;background:#f3f6fb;}'
      + '#content-hifly-digital-human .hifly-upload-preview-inner-video{padding:1rem;background:#eef2ff;}'
      + '#content-hifly-digital-human .hifly-upload-preview-inner-audio{min-height:120px;padding:1.2rem 1rem;background:#f8fafc;}'
      + '#content-hifly-digital-human .hifly-upload-remove{position:absolute;top:0.9rem;right:0.9rem;width:42px;height:42px;border:none;border-radius:14px;background:rgba(255,255,255,0.96);color:#334155;font-size:1.4rem;line-height:1;cursor:pointer;box-shadow:0 12px 28px rgba(15,23,42,0.12);}'
      + '#content-hifly-digital-human .hifly-upload-remove:hover{background:#fff;color:#111827;}'
      + '#content-hifly-digital-human .hifly-file-meta{display:flex;justify-content:space-between;gap:0.75rem;padding:0.78rem 0.9rem;border-radius:16px;background:rgba(15,23,42,0.05);color:#334155;word-break:break-all;}'
      + '#content-hifly-digital-human .hifly-radio-row{display:flex;gap:0.7rem;flex-wrap:wrap;}'
      + '#content-hifly-digital-human .hifly-radio-card{display:inline-flex;align-items:center;gap:0.45rem;padding:0.65rem 0.82rem;border-radius:999px;border:1px solid rgba(124,94,255,0.16);cursor:pointer;}'
      + 'body.hifly-modal-open{overflow:hidden;}'
      + '@media (max-width:1120px){#content-hifly-digital-human .hifly-shell{grid-template-columns:minmax(0,1fr);}#content-hifly-digital-human .hifly-sidebar{position:static;}}'
      + '@media (max-width:720px){#content-hifly-digital-human .hifly-selected-avatar,#content-hifly-digital-human .hifly-selected-voice{grid-template-columns:minmax(0,1fr);}#content-hifly-digital-human .hifly-avatar-grid,#content-hifly-digital-human .hifly-voice-grid{grid-template-columns:minmax(0,1fr);}#content-hifly-digital-human .hifly-requirement-grid{grid-template-columns:minmax(0,1fr);}#content-hifly-digital-human .hifly-modal-card{width:min(96vw,760px);}}';
    document.head.appendChild(style);
  }

  function buildTemplate() {
    return `
      <div class="tvc-studio">
        <div class="tvc-studio-hero">
          <div>
            <h3>必火智能数字人</h3>
            <p>把数字人、声音和口播任务放到同一个工作台里，左侧管理当前选择，右侧查看资源库和生成结果。</p>
            <div class="tvc-hero-meta">创建数字人或声音成功后，会自动刷新到“我的数字人 / 我的声音”。</div>
          </div>
          <div style="display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center;">
            <button type="button" id="hiflyBackBtn" class="btn btn-ghost btn-sm">返回技能商店</button>
          </div>
        </div>
        <div class="hifly-shell">
          <aside class="hifly-sidebar">
            <div class="tvc-panel tvc-panel-compact">
              <h4 class="tvc-panel-title">1. 必火智能数字人服务</h4>
              <p class="tvc-panel-hint">创作服务由平台统一托管，用户无需填写 API Token。</p>
              <div class="viral-action-row">
                <span class="viral-inline-status">按作品实际时长消耗平台积分</span>
              </div>
            </div>

            <div class="tvc-panel">
              <div class="hifly-panel-head">
                <div>
                  <h4 class="tvc-panel-title">2. 当前数字人</h4>
                  <p class="tvc-panel-hint">从右侧数字人库里选择后，这里会固定显示当前数字人。</p>
                </div>
                <button type="button" id="hiflyOpenAvatarLibraryBtn" class="btn btn-ghost btn-sm">打开数字人库</button>
              </div>
              <div id="hiflySelectedAvatarPreview" class="hifly-selected-avatar is-empty"></div>
              <input id="hiflyAvatarInput" type="hidden">
            </div>

            <div class="tvc-panel">
              <div class="hifly-panel-head">
                <div>
                  <h4 class="tvc-panel-title">3. 当前声音</h4>
                  <p class="tvc-panel-hint">声音也会以卡片方式选择，避免只看到内部 ID。</p>
                  <p id="hiflySelectedVoiceModeHint" class="tvc-panel-hint" style="display:none;">当前处于声音驱动模式，提交任务时会忽略这里已选的声音。</p>
                </div>
                <div class="hifly-head-actions">
                  <button type="button" id="hiflyOpenVoiceLibraryBtn" class="btn btn-ghost btn-sm">打开声音库</button>
                  <button type="button" id="hiflyOpenVoiceCreateBtn" class="btn btn-primary btn-sm">创建声音</button>
                </div>
              </div>
              <div id="hiflySelectedVoicePreview" class="hifly-selected-voice is-empty"></div>
              <input id="hiflyVoiceInput" type="hidden">
            </div>

            <div class="tvc-panel">
              <h4 class="tvc-panel-title">4. 提交任务</h4>
              <div class="tvc-field">
                <label for="hiflyTitleInput">作品标题</label>
                <input id="hiflyTitleInput" type="text" maxlength="20" placeholder="数字人口播">
              </div>
              <div class="tvc-field">
                <label>提交模式</label>
                <div class="hifly-mode-switch">
                  <button type="button" id="hiflyVideoModeTtsBtn" class="hifly-mode-chip is-active">文案驱动</button>
                  <button type="button" id="hiflyVideoModeAudioBtn" class="hifly-mode-chip">声音驱动</button>
                </div>
                <p class="tvc-panel-hint" style="margin-top:0.55rem;">文案驱动会使用你当前选中的声音；声音驱动会直接使用你上传的音频来驱动数字人。</p>
              </div>
              <div id="hiflyVideoTtsFields">
                <div class="tvc-field">
                  <label for="hiflyScriptInput">口播文案</label>
                  <textarea id="hiflyScriptInput" style="min-height:160px;" placeholder="输入数字人要说的文案，不超过 10000 字。"></textarea>
                </div>
              </div>
              <div id="hiflyVideoAudioFields" style="display:none;">
                <div class="tvc-field">
                  <label>驱动音频</label>
                  <div id="hiflyAudioDriveUploadBox" class="hifly-upload-box" tabindex="0" role="button">
                    <div class="hifly-upload-icon">♪</div>
                    <div class="hifly-upload-copy">
                      <strong>点击或拖拽上传音频</strong>
                      <span>支持 mp3、m4a、wav，大小 100MB 以内，建议 5 秒到 30 分钟。</span>
                    </div>
                  </div>
                  <input id="hiflyAudioDriveFile" type="file" accept=".mp3,.m4a,.wav,audio/mpeg,audio/mp4,audio/wav" style="display:none;">
                  <div id="hiflyAudioDrivePreview" class="hifly-upload-preview"></div>
                  <div id="hiflyAudioDriveFileMeta" class="hifly-file-meta" style="display:none;"></div>
                </div>
              </div>
              <div class="tvc-inline-grid">
                <div class="tvc-field">
                  <label for="hiflySubtitleCheck">字幕</label>
                  <label class="hifly-inline-check" for="hiflySubtitleCheck">
                    <input type="checkbox" id="hiflySubtitleCheck">
                    <span>显示字幕</span>
                  </label>
                  <p id="hiflySubtitleModeHint" class="tvc-panel-hint" style="display:none;margin-top:0.4rem;">声音驱动接口当前不支持单独传字幕开关，这里会自动关闭。</p>
                </div>
                <div class="tvc-field">
                  <label for="hiflyAigcFlagSelect">AIGC 水印</label>
                  <select id="hiflyAigcFlagSelect">
                    <option value="0" selected>跟随个人中心</option>
                    <option value="1">开启</option>
                    <option value="2">关闭</option>
                  </select>
                </div>
              </div>
              <div id="hiflyMsg" class="msg" style="display:none;margin-top:0.8rem;"></div>
              <div class="tvc-quick-actions">
                <button type="button" id="hiflyGenerateBtn" class="btn btn-primary">提交生成任务</button>
              </div>
            </div>
          </aside>

          <section class="hifly-main">
            <div class="tvc-panel">
              <div class="hifly-main-toolbar">
                <div>
                  <h4 class="tvc-panel-title" style="margin-bottom:0.25rem;">资源工作台</h4>
                  <p class="tvc-panel-hint" style="margin:0;">数字人和声音都做成卡片库，创建后会回到对应资源列表。</p>
                </div>
                <div class="hifly-tab-row">
                  <button type="button" id="hiflyAvatarLibraryTabBtn" class="hifly-tab-btn is-active">数字人库</button>
                  <button type="button" id="hiflyVoiceLibraryTabBtn" class="hifly-tab-btn">声音库</button>
                  <button type="button" id="hiflyResultTabBtn" class="hifly-tab-btn">任务结果</button>
                </div>
              </div>

              <div id="hiflyAvatarLibraryView">
                <div class="hifly-library-toolbar">
                  <input id="hiflyAvatarSearchInput" class="hifly-search" type="text" placeholder="搜索数字人名称">
                  <div class="hifly-toolbar-actions">
                    <button type="button" id="hiflyOpenAvatarCreateBtn" class="btn btn-primary btn-sm">创建数字人</button>
                    <button type="button" id="hiflyRefreshLibraryBtn" class="btn btn-ghost btn-sm">刷新数字人</button>
                  </div>
                </div>
                <div class="hifly-library-tip">创建成功后会自动刷新到“我的数字人”。如果暂时没有看到，请稍后刷新列表。</div>
                <div class="hifly-library-section">
                  <div class="hifly-section-head">
                    <div>
                      <h4>我的数字人</h4>
                      <p>这里优先放你自己创建或克隆出来的数字人。</p>
                    </div>
                    <span class="hifly-section-count"><span id="hiflyMineCount">0</span> 个</span>
                  </div>
                  <div id="hiflyMineAvatarGrid" class="hifly-avatar-grid"></div>
                  <div id="hiflyMineAvatarEmpty" class="hifly-section-empty"></div>
                  <div class="hifly-more-row"><button type="button" id="hiflyMineMoreBtn" class="btn btn-ghost btn-sm" style="display:none;">显示更多</button></div>
                </div>
                <div class="hifly-library-section">
                  <div class="hifly-section-head">
                    <div>
                      <h4>公共数字人</h4>
                      <p>公共数字人和我的数字人分开展示，方便快速挑选。</p>
                    </div>
                    <span class="hifly-section-count"><span id="hiflyPublicCount">0</span> 个</span>
                  </div>
                  <div id="hiflyPublicAvatarGrid" class="hifly-avatar-grid"></div>
                  <div id="hiflyPublicAvatarEmpty" class="hifly-section-empty"></div>
                  <div class="hifly-more-row"><button type="button" id="hiflyPublicMoreBtn" class="btn btn-ghost btn-sm" style="display:none;">显示更多</button></div>
                </div>
              </div>

              <div id="hiflyVoiceLibraryView" style="display:none;">
                <div class="hifly-library-toolbar">
                  <input id="hiflyVoiceSearchInput" class="hifly-search" type="text" placeholder="搜索声音名称">
                  <div class="hifly-toolbar-actions">
                    <button type="button" id="hiflyCreateVoiceBtn" class="btn btn-primary btn-sm">创建声音</button>
                    <button type="button" id="hiflyLoadVoicesBtn" class="btn btn-ghost btn-sm">刷新声音</button>
                  </div>
                </div>
                <div class="hifly-library-section">
                  <div class="hifly-section-head">
                    <div>
                      <h4>我的声音</h4>
                      <p>优先展示你自己创建的声音，选择后会同步到左侧生成区。</p>
                    </div>
                    <span class="hifly-section-count"><span id="hiflyMineVoiceCount">0</span> 个</span>
                  </div>
                  <div id="hiflyMineVoiceGrid" class="hifly-voice-grid"></div>
                  <div id="hiflyMineVoiceEmpty" class="hifly-section-empty"></div>
                  <div class="hifly-more-row"><button type="button" id="hiflyMineVoiceMoreBtn" class="btn btn-ghost btn-sm" style="display:none;">显示更多</button></div>
                </div>
                <div class="hifly-library-section">
                  <div class="hifly-section-head">
                    <div>
                      <h4>公共声音</h4>
                      <p>公共声音作为预置音色，和我的声音分开管理。</p>
                    </div>
                    <span class="hifly-section-count"><span id="hiflyPublicVoiceCount">0</span> 个</span>
                  </div>
                  <div id="hiflyPublicVoiceGrid" class="hifly-voice-grid"></div>
                  <div id="hiflyPublicVoiceEmpty" class="hifly-section-empty"></div>
                  <div class="hifly-more-row"><button type="button" id="hiflyPublicVoiceMoreBtn" class="btn btn-ghost btn-sm" style="display:none;">显示更多</button></div>
                </div>
              </div>

              <div id="hiflyResultView" style="display:none;">
                <div class="hifly-result-panel-head">
                  <div>
                    <h4 class="tvc-panel-title" style="margin-bottom:0.25rem;">任务结果</h4>
                    <p class="tvc-panel-hint" style="margin:0;">视频生成、数字人创建、声音创建都会统一在这里显示进度与结果。</p>
                    <div class="hifly-result-meta">
                      <span class="hifly-result-pill" id="hiflyTaskStatusText" data-tone="idle">等待提交</span>
                      <span class="hifly-result-pill" id="hiflyTaskKindText" data-tone="idle">未开始</span>
                    </div>
                  </div>
                  <button type="button" id="hiflyResultBackBtn" class="btn btn-ghost btn-sm">返回资源库</button>
                </div>
                <div class="tvc-video-stage" style="height:clamp(360px, 58vh, 680px);">
                  <div id="hiflyResultSurface" class="tvc-video-surface" style="height:100%;display:flex;align-items:center;justify-content:center;"></div>
                </div>

                <div class="hifly-library-section" style="margin-top:1rem;">
                  <div class="hifly-section-head">
                    <div>
                      <h4>历史口播任务</h4>
                      <p>过往的数字人口播任务会自动转存到素材库，过期也能回看与下载。</p>
                    </div>
                    <div style="display:flex;gap:0.5rem;align-items:center;">
                      <span class="hifly-section-count"><span id="hiflyVideoHistoryCount">0</span> 个</span>
                      <button type="button" id="hiflyVideoHistoryRefreshBtn" class="btn btn-ghost btn-sm">刷新</button>
                    </div>
                  </div>
                  <div id="hiflyVideoHistoryGrid" class="hifly-video-history-grid"></div>
                  <div id="hiflyVideoHistoryEmpty" class="hifly-section-empty" style="display:none;">还没有历史口播任务，提交一次后这里会出现。</div>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>

      <div id="hiflyAvatarModeModal" class="hifly-modal" style="display:none;">
        <div class="hifly-modal-backdrop" data-modal-close="hiflyAvatarModeModal"></div>
        <div class="hifly-modal-card hifly-mode-card" role="dialog" aria-modal="true" aria-labelledby="hiflyAvatarModeTitle">
          <div class="hifly-modal-head">
            <h4 id="hiflyAvatarModeTitle" class="hifly-modal-title">模式选择</h4>
            <button type="button" class="hifly-modal-close" data-modal-close="hiflyAvatarModeModal" aria-label="关闭">×</button>
          </div>
          <div class="hifly-mode-list">
            <button type="button" class="hifly-mode-option" data-avatar-mode="image">
              <span class="hifly-mode-icon">图</span>
              <span class="hifly-mode-copy">
                <strong>图片生成数字人</strong>
                <span>上传一张图片，快速创建自己的数字人。</span>
              </span>
            </button>
            <button type="button" class="hifly-mode-option" data-avatar-mode="video">
              <span class="hifly-mode-icon">视</span>
              <span class="hifly-mode-copy">
                <strong>视频生成数字人</strong>
                <span>上传一段视频，作为驱动数字人的底版视频。</span>
              </span>
              <span class="hifly-recommend-chip">推荐</span>
            </button>
          </div>
        </div>
      </div>

      <div id="hiflyAvatarImageModal" class="hifly-modal" style="display:none;">
        <div class="hifly-modal-backdrop" data-modal-close="hiflyAvatarImageModal"></div>
        <div class="hifly-modal-card" role="dialog" aria-modal="true" aria-labelledby="hiflyAvatarImageTitle">
          <div class="hifly-modal-head">
            <h4 id="hiflyAvatarImageTitle" class="hifly-modal-title">图片生成数字人</h4>
            <button type="button" class="hifly-modal-close" data-modal-close="hiflyAvatarImageModal" aria-label="关闭">×</button>
          </div>
          <div class="hifly-modal-body">
            <div class="tvc-field">
              <label for="hiflyAvatarImageName">数字人名称</label>
              <input id="hiflyAvatarImageName" type="text" maxlength="20" placeholder="输入数字人名称">
            </div>
            <div class="hifly-requirement-box">
              <strong>图片要求</strong>
              <div class="hifly-requirement-grid">
                <span>人物：正面、半身</span>
                <span>格式：png / jpg / jpeg</span>
                <span>大小：不超过 10MB</span>
              </div>
            </div>
            <input id="hiflyAvatarImageFile" type="file" accept=".png,.jpg,.jpeg,image/png,image/jpeg" style="display:none;">
            <button type="button" id="hiflyAvatarImageUploadBox" class="hifly-upload-box">
              <span class="hifly-upload-icon">↑</span>
              <strong>请上传一张图片，用于生成图片数字人</strong>
              <span>将文件拖到此处，或点击此区域上传</span>
            </button>
            <div id="hiflyAvatarImagePreview" class="hifly-upload-preview" style="display:none;"></div>
            <div id="hiflyAvatarImageFileMeta" class="hifly-file-meta" style="display:none;"></div>
            <div class="tvc-field">
              <label>选择模型</label>
              <div class="hifly-radio-row">
                <label class="hifly-radio-card"><input type="radio" name="hiflyAvatarImageModel" value="1"><span>2.0</span></label>
                <label class="hifly-radio-card"><input type="radio" name="hiflyAvatarImageModel" value="2" checked><span>2.1 推荐</span></label>
              </div>
            </div>
            <label class="tvc-check"><input type="checkbox" id="hiflyAvatarImageAgree" checked>我已阅读并同意《使用者承诺须知》</label>
          </div>
          <div class="hifly-modal-foot">
            <button type="button" class="btn btn-ghost" data-modal-close="hiflyAvatarImageModal">取消</button>
            <button type="button" id="hiflyAvatarImageSubmitBtn" class="btn btn-primary">提交</button>
          </div>
        </div>
      </div>

      <div id="hiflyAvatarVideoModal" class="hifly-modal" style="display:none;">
        <div class="hifly-modal-backdrop" data-modal-close="hiflyAvatarVideoModal"></div>
        <div class="hifly-modal-card" role="dialog" aria-modal="true" aria-labelledby="hiflyAvatarVideoTitle">
          <div class="hifly-modal-head">
            <h4 id="hiflyAvatarVideoTitle" class="hifly-modal-title">视频生成数字人</h4>
            <button type="button" class="hifly-modal-close" data-modal-close="hiflyAvatarVideoModal" aria-label="关闭">×</button>
          </div>
          <div class="hifly-modal-body">
            <div class="tvc-field">
              <label for="hiflyAvatarVideoName">数字人名称</label>
              <input id="hiflyAvatarVideoName" type="text" maxlength="20" placeholder="输入数字人名称">
            </div>
            <div class="hifly-requirement-box">
              <strong>视频要求</strong>
              <ol class="hifly-requirement-list">
                <li>尽量保证人脸清晰完整，不要遮挡。</li>
                <li>建议分辨率 720p 或 1080p，时长 5 秒到 30 分钟。</li>
                <li>支持 mp4 / mov，文件大小不超过 500MB。</li>
              </ol>
            </div>
            <input id="hiflyAvatarVideoFile" type="file" accept=".mp4,.mov,video/mp4,video/quicktime" style="display:none;">
            <button type="button" id="hiflyAvatarVideoUploadBox" class="hifly-upload-box">
              <span class="hifly-upload-icon">↑</span>
              <strong>请上传一段视频，作为驱动数字人的底版视频</strong>
              <span>将文件拖到此处，或点击此区域上传</span>
            </button>
            <div id="hiflyAvatarVideoPreview" class="hifly-upload-preview" style="display:none;"></div>
            <div id="hiflyAvatarVideoFileMeta" class="hifly-file-meta" style="display:none;"></div>
            <label class="tvc-check"><input type="checkbox" id="hiflyAvatarVideoAgree" checked>我已阅读并同意《使用者承诺须知》</label>
          </div>
          <div class="hifly-modal-foot">
            <button type="button" class="btn btn-ghost" data-modal-close="hiflyAvatarVideoModal">取消</button>
            <button type="button" id="hiflyAvatarVideoSubmitBtn" class="btn btn-primary">提交</button>
          </div>
        </div>
      </div>

      <div id="hiflyVoiceCreateModal" class="hifly-modal" style="display:none;">
        <div class="hifly-modal-backdrop" data-modal-close="hiflyVoiceCreateModal"></div>
        <div class="hifly-modal-card" role="dialog" aria-modal="true" aria-labelledby="hiflyVoiceCreateTitle">
          <div class="hifly-modal-head">
            <h4 id="hiflyVoiceCreateTitle" class="hifly-modal-title">创建声音</h4>
            <button type="button" class="hifly-modal-close" data-modal-close="hiflyVoiceCreateModal" aria-label="关闭">×</button>
          </div>
          <div class="hifly-modal-body">
            <div class="tvc-field">
              <label for="hiflyVoiceCreateName">声音名称</label>
              <input id="hiflyVoiceCreateName" type="text" maxlength="20" placeholder="输入声音名称">
            </div>
            <div class="tvc-field">
              <label for="hiflyVoiceLanguageSelect">语言</label>
              <select id="hiflyVoiceLanguageSelect">
                <option value="zh" selected>中文</option>
                <option value="en">英文</option>
              </select>
            </div>
            <div class="hifly-requirement-box">
              <strong>声音要求</strong>
              <div class="hifly-requirement-grid">
                <span>格式：mp3 / m4a / wav</span>
                <span>大小：不超过 20MB</span>
                <span>建议：人声清晰、环境噪音少</span>
              </div>
            </div>
            <input id="hiflyVoiceCreateFile" type="file" accept=".mp3,.m4a,.wav,audio/mpeg,audio/mp4,audio/wav" style="display:none;">
            <button type="button" id="hiflyVoiceUploadBox" class="hifly-upload-box">
              <span class="hifly-upload-icon">↑</span>
              <strong>请上传一段声音样本，用于创建声音</strong>
              <span>将文件拖到此处，或点击此区域上传</span>
            </button>
            <div id="hiflyVoiceFilePreview" class="hifly-upload-preview" style="display:none;"></div>
            <div id="hiflyVoiceFileMeta" class="hifly-file-meta" style="display:none;"></div>
            <label class="tvc-check"><input type="checkbox" id="hiflyVoiceCreateAgree" checked>我已阅读并同意《使用者承诺须知》</label>
          </div>
          <div class="hifly-modal-foot">
            <button type="button" class="btn btn-ghost" data-modal-close="hiflyVoiceCreateModal">取消</button>
            <button type="button" id="hiflyVoiceSubmitBtn" class="btn btn-primary">提交</button>
          </div>
        </div>
      </div>
    `;
  }

  function ensureMarkup() {
    var root = $('content-hifly-digital-human');
    if (!root) return null;
    if (root.getAttribute('data-hifly-built-version') !== HIFLY_TEMPLATE_VERSION) {
      root.innerHTML = buildTemplate();
      root.setAttribute('data-hifly-built', '1');
      root.setAttribute('data-hifly-built-version', HIFLY_TEMPLATE_VERSION);
      root.removeAttribute('data-hifly-init');
    }
    return root;
  }

  window.initHiflyDigitalHumanView = function() {
    var root = ensureMarkup();
    if (!root) return;
    ensureStyles();

    if (root.getAttribute('data-hifly-init') !== '1') {
      root.setAttribute('data-hifly-init', '1');
      bindEvents();
    }

    setActiveView(state.taskId ? 'result' : 'avatar');
    bootstrapData().catch(function(err) {
      showMessage(err && err.message ? err.message : '初始化 HiFly 页面失败', true);
    });
  };

  if (location.hash === '#hifly-digital-human') {
    setTimeout(function() {
      if (typeof window.initHiflyDigitalHumanView === 'function') window.initHiflyDigitalHumanView();
    }, 0);
  }
})();
