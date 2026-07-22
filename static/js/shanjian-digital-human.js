(function() {
  var state = {
    token: '',
    taskId: '',
    taskType: '',
    taskApiScope: '',
    taskKindLabel: '未开始',
    pollTimer: null,
    submitting: false,
    activeView: 'avatar',
    avatarLibrary: { mine: [], public: [], mine_supported: true, mine_message: '', using_default_token: false, public_page: 1, public_page_size: 20, public_has_more: false, public_loading: false, public_visible: 20 },
    voiceLibrary: { mine: [], public: [], mine_supported: true, mine_message: '', using_default_token: false },
    templateLibrary: { rows: [], sid: '', loading: false },
    templatePreviewItem: null,
    selectedMaterials: [],
    materialPicker: { items: [], loading: false, query: '', selected: {} },
    materialFilter: 'all',
    videoHistory: [],
    videoCreateMode: 'tts',
    voicePreviewMap: {},
    uploadPreviews: {},
    voiceCreateRecordedFile: null,
    voiceRecordStream: null,
    voiceRecordContext: null,
    voiceRecordSource: null,
    voiceRecordProcessor: null,
    voiceRecordBuffers: [],
    voiceRecordStartedAt: 0,
    voiceRecordTimer: null,
    voiceRecording: false,
    voiceRecordingPending: false,
    voiceCreatePromptIndex: 0,
    selectedAvatar: null,
    selectedVoice: null,
    selectedTemplate: null,
    avatarSearch: '',
    voiceSearch: '',
    templateSearch: '',
    libraryExpanded: {
      avatarMine: false,
      avatarPublic: false,
      voiceMine: false,
      voicePublic: false
    }
  };

  var SHANJIAN_TEMPLATE_VERSION = '20260606-voice-provider-detect';
  var SHANJIAN_STYLE_VERSION = '20260606-voice-provider-detect';
  var SHANJIAN_AVATAR_COVER_MANIFEST = '/static/data/shanjian-public-avatar-covers.json?v=20260512';
  var SHANJIAN_AVATAR_VIDEO_MAX_BYTES = 200 * 1024 * 1024;
  var INLINE_MATERIAL_IMAGE_SECONDS = 2;
  var INLINE_MATERIAL_VIDEO_MAX_SECONDS = 60;
  var INLINE_MATERIAL_TOTAL_MAX_SECONDS = 300;
  var INLINE_MATERIAL_VIDEO_MAX_BYTES = 500 * 1024 * 1024;
  var INLINE_MATERIAL_MAX_EDGE_1080 = 2000;
  var SHANJIAN_VOICE_RECORD_PROMPTS = {
    zh: [
      '你好，欢迎使用声音创建功能。请保持自然语速和清晰发音，完整朗读这段文字。今天的风很轻，阳光也刚刚好，希望这段录音可以帮助系统更准确地识别你的声音特点。',
      '现在开始录制声音样本，请放松语气，像平时说话一样自然朗读。这个功能会根据你的发音、语速和停顿来创建声音，所以请尽量在安静环境中连续读完这一小段内容。',
      '接下来请对着麦克风清楚地读出这段话，速度不用太快，也不要刻意放慢。只要保持正常表达和稳定音量，大约十几秒的录音就可以帮助系统完成声音创建。'
    ],
    en: [
      'Hello and welcome. Please read this short passage in a clear and natural voice. Keep a steady pace and stay close to your microphone so the system can capture enough detail to build your custom voice.',
      'We are collecting a short voice sample. Please speak naturally, keep your volume stable, and read this text in one go. A calm environment and about ten to twenty seconds of speech will work best.',
      'Please read this paragraph out loud as if you were speaking normally. Do not rush, and try to keep your pronunciation clear and consistent. This short recording will be used to create your new voice.'
    ]
  };
  var MINIMAX_EMOTION_OPTIONS = [
    { value: 'happy', label: '生动' },
    { value: 'neutral', label: '自然' },
    { value: 'sad', label: '难过' },
    { value: 'angry', label: '愤怒' },
    { value: 'fearful', label: '害怕' },
    { value: 'disgusted', label: '厌恶' },
    { value: 'surprised', label: '惊讶' }
  ];
  var avatarCoverManifest = { by_basename: {} };
  var avatarCoverManifestPromise = null;



  function $(id) { return document.getElementById(id); }

  function baseUrl() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  }

  function cloudBaseUrl() {
    var api = (typeof API_BASE !== 'undefined' ? (API_BASE || '') : '').replace(/\/$/, '');
    if (api && !/^https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?(?:\/|$)/i.test(api) && api !== baseUrl()) return api;
    return (typeof LOBSTER_SERVER_PUBLIC !== 'undefined' ? String(LOBSTER_SERVER_PUBLIC || '').replace(/\/$/, '') : api);
  }

  function shanjianApiBase(path) {
    var cloud = cloudBaseUrl();
    if (String(path || '').indexOf('/api/shanjian-') === 0 && cloud) return cloud;
    return baseUrl();
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

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(String(value || ''));
    return String(value || '').replace(/["\\]/g, '\\$&');
  }

  var voicePreviewPlayer = null;
  var voicePreviewButton = null;
  var voicePreviewAudioContext = null;
  var voicePreviewSource = null;
  var voicePreviewGain = null;
  var voicePreviewPlaying = false;
  var voicePreviewRunId = 0;
  var voiceParamSaveTimers = {};

  function stopVoicePreview(exceptButton) {
    voicePreviewRunId += 1;
    voicePreviewPlaying = false;
    if (voicePreviewSource) {
      try { voicePreviewSource.onended = null; } catch (e) {}
      try { voicePreviewSource.stop(0); } catch (err) {}
      try { voicePreviewSource.disconnect(); } catch (disconnectErr) {}
      voicePreviewSource = null;
    }
    if (voicePreviewGain) {
      try { voicePreviewGain.disconnect(); } catch (gainErr) {}
      voicePreviewGain = null;
    }
    if (voicePreviewPlayer) {
      try {
        voicePreviewPlayer.pause();
        voicePreviewPlayer.currentTime = 0;
      } catch (e) {}
    }
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-preview-play-btn.is-playing'), function(btn) {
      if (btn !== exceptButton) {
        btn.classList.remove('is-playing');
        btn.innerHTML = '<span class="shanjian-preview-play-icon">▶</span><span>试听</span>';
      }
    });
    if (!exceptButton) voicePreviewButton = null;
  }

  function ensureVoicePreviewAudioContext() {
    var AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextCtor) return null;
    if (!voicePreviewAudioContext) voicePreviewAudioContext = new AudioContextCtor();
    if (voicePreviewAudioContext.state === 'suspended' && voicePreviewAudioContext.resume) {
      return voicePreviewAudioContext.resume().then(function() { return voicePreviewAudioContext; });
    }
    return Promise.resolve(voicePreviewAudioContext);
  }

  function playVoicePreviewFallback(url, params, btn, runId) {
    if (runId !== voicePreviewRunId || voicePreviewButton !== btn) return Promise.resolve();
    if (!voicePreviewPlayer) voicePreviewPlayer = new Audio();
    voicePreviewPlayer.src = url;
    voicePreviewPlayer.playbackRate = 1;
    voicePreviewPlayer.volume = 1;
    voicePreviewPlayer.onended = function() { stopVoicePreview(); };
    voicePreviewPlayer.onerror = function() {
      stopVoicePreview();
      showMessage('试听音频加载失败，请刷新后重试', true);
    };
    return voicePreviewPlayer.play().then(function() {
      voicePreviewPlaying = true;
    });
  }

  function playVoicePreviewWithParams(url, params, btn) {
    voicePreviewRunId += 1;
    var runId = voicePreviewRunId;
    return ensureVoicePreviewAudioContext()
      .then(function(audioContext) {
        if (!audioContext) return playVoicePreviewFallback(url, params, btn, runId);
        return fetch(url, { cache: 'force-cache' })
          .then(function(resp) {
            if (!resp.ok) throw new Error('preview fetch ' + resp.status);
            return resp.arrayBuffer();
          })
          .then(function(buffer) {
            return new Promise(function(resolve, reject) {
              audioContext.decodeAudioData(buffer.slice(0), resolve, reject);
            });
          })
          .then(function(audioBuffer) {
            if (runId !== voicePreviewRunId || voicePreviewButton !== btn) return;
            var source = audioContext.createBufferSource();
            var gain = audioContext.createGain();
            source.buffer = audioBuffer;
            source.playbackRate.value = 1;
            gain.gain.value = 1;
            source.connect(gain);
            gain.connect(audioContext.destination);
            voicePreviewSource = source;
            voicePreviewGain = gain;
            voicePreviewPlaying = true;
            source.onended = function() {
              if (runId === voicePreviewRunId) stopVoicePreview();
            };
            source.start(0);
          })
          .catch(function() {
            return playVoicePreviewFallback(url, params, btn, runId);
          });
      });
  }

  function voicePreviewButtonHtml(url, params, meta) {
    url = normalizeAssetUrl(url);
    meta = meta || {};
    if (!url && meta.provider !== 'minimax' && meta.provider !== 'qwen') return '';
    params = params || {};
    return ''
      + '<button type="button" class="shanjian-preview-play-btn" data-preview-url="' + escapeHtml(url) + '"'
      + ' data-preview-rate="' + escapeHtml(params.rate || '1') + '"'
      + ' data-preview-volume="' + escapeHtml(params.volume || '1') + '"'
      + ' data-preview-pitch="' + escapeHtml(params.pitch != null && params.pitch !== '' ? params.pitch : '1') + '"'
      + ' data-preview-emotion="' + escapeHtml(params.emotion || '') + '"'
      + ' data-preview-instructions="' + escapeHtml(params.instructions || '') + '"'
      + ' data-preview-provider="' + escapeHtml(meta.provider || '') + '"'
      + ' data-preview-voice="' + escapeHtml(meta.voice || '') + '">'
      + '<span class="shanjian-preview-play-icon">▶</span><span>试听</span>'
      + '</button>';
  }

  function numericParam(value, fallback, min, max) {
    var num = Number(value);
    if (!isFinite(num)) num = Number(fallback);
    num = Math.min(max, Math.max(min, num));
    return Number(num.toFixed(2));
  }

  function intParam(value, fallback, min, max) {
    var num = Number(value);
    if (!isFinite(num)) num = Number(fallback);
    num = Math.round(Math.min(max, Math.max(min, num)));
    return num;
  }

  function formatParamValue(value) {
    return String(Number(value).toFixed(2)).replace(/\.?0+$/, '');
  }

  function optionLabel(options, value, fallback) {
    var raw = String(value || '').trim();
    var item = (options || []).find(function(option) { return option.value === raw; });
    return item ? item.label : (fallback || raw || '');
  }

  function optionHtml(options, value) {
    var raw = String(value || '').trim();
    return (options || []).map(function(option) {
      return '<option value="' + escapeHtml(option.value) + '"' + (option.value === raw ? ' selected' : '') + '>' + escapeHtml(option.label) + '</option>';
    }).join('');
  }

  function voiceParamSummary(params, isMinimax) {
    params = params || {};
    return '语速 ' + (params.rate || '1.0')
      + ' / 音量 ' + (params.volume || '1.0')
      + ' / 语调 ' + (params.pitch || '0');
  }

  function readVoiceParamsFromScope(el) {
    var scope = el && el.closest ? el.closest('.shanjian-voice-card,.shanjian-selected-voice') : null;
    var provider = el && el.getAttribute ? (el.getAttribute('data-preview-provider') || '') : '';
    var isMinimax = provider === 'minimax';
    var rate = numericParam(el && el.getAttribute ? el.getAttribute('data-preview-rate') : 1, 1, 0.5, 2);
    var volume = numericParam(el && el.getAttribute ? el.getAttribute('data-preview-volume') : 1, 1, 0.1, 2);
    var pitch = numericParam(el && el.getAttribute ? el.getAttribute('data-preview-pitch') : (isMinimax ? 0 : 1), isMinimax ? 0 : 1, isMinimax ? -12 : 0.1, isMinimax ? 12 : 2);
    var emotion = el && el.getAttribute ? (el.getAttribute('data-preview-emotion') || (isMinimax ? 'happy' : '')) : (isMinimax ? 'happy' : '');
    var instructions = el && el.getAttribute ? (el.getAttribute('data-preview-instructions') || '') : '';
    if (scope) {
      var rateInput = scope.querySelector('.shanjian-voice-param-input[data-param="rate"]');
      var volumeInput = scope.querySelector('.shanjian-voice-param-input[data-param="volume"]');
      var pitchInput = scope.querySelector('.shanjian-voice-param-input[data-param="pitch"]');
      var emotionInput = scope.querySelector('[data-param="emotion"]');
      var instructionsInput = scope.querySelector('[data-param="instructions"]');
      if (rateInput) rate = numericParam(rateInput.value, rate, 0.5, 2);
      if (volumeInput) volume = numericParam(volumeInput.value, volume, 0.1, 2);
      if (pitchInput) pitch = numericParam(pitchInput.value, pitch, isMinimax ? -12 : 0.1, isMinimax ? 12 : 2);
      if (emotionInput) emotion = emotionInput.value || emotion;
      if (instructionsInput) instructions = instructionsInput.value || instructions;
    }
    return { rate: rate, volume: volume, pitch: pitch, emotion: emotion || 'happy', instructions: instructions };
  }

  function previewScriptText() {
    var text = (($('shanjianScriptInput') || {}).value || '').trim();
    if (text.length > 180) text = text.slice(0, 180);
    return text;
  }

  function previewTtsPayload(voiceId, text, params, provider) {
    params = params || {};
    var payload = {
      voice: voiceId,
      text: text,
      rate: String(params.rate != null ? params.rate : '1'),
      volume: String(params.volume != null ? params.volume : '1'),
      pitch: String(params.pitch != null ? params.pitch : '0'),
      emotion: params.emotion != null ? String(params.emotion) : 'happy'
    };
    if ((params.instructions || '').trim()) payload.instructions = String(params.instructions || '').trim();
    if (provider === 'minimax' || provider === 'qwen') payload.voice_provider = provider;
    return payload;
  }

  function findVoiceGroupByVoiceIdLoose(voiceId) {
    if (!voiceId) return null;
    var all = (state.voiceLibrary.mine || []).concat(state.voiceLibrary.public || []);
    for (var i = 0; i < all.length; i += 1) {
      var item = all[i];
      if (!item) continue;
      if (item.voice === voiceId || item.shanjian_voice_id === voiceId) {
        return { group: item, style: getSelectedStyleForVoiceItem(item) || { voice: voiceId } };
      }
      var styles = voiceStyles(item);
      for (var j = 0; j < styles.length; j += 1) {
        if (styles[j] && styles[j].voice === voiceId) return { group: item, style: styles[j] };
      }
    }
    return null;
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

  function isConsumerPreviewVoice(value) {
    return String(value || '').trim().indexOf('consumer_') === 0;
  }

  function assetBasename(url) {
    var raw = String(url || '').trim();
    if (!raw) return '';
    try {
      var parsed = new URL(raw, window.location.origin);
      var parts = parsed.pathname.split('/');
      return decodeURIComponent(parts[parts.length - 1] || '');
    } catch (e) {
      var clean = raw.split('#')[0].split('?')[0].replace(/\/+$/, '');
      var segments = clean.split('/');
      try {
        return decodeURIComponent(segments[segments.length - 1] || '');
      } catch (err) {
        return segments[segments.length - 1] || '';
      }
    }
  }

  function localAvatarCoverFor(item) {
    if (!item || !avatarCoverManifest || !avatarCoverManifest.by_basename) return '';
    var candidates = [
      item.cover_url, item.image_url, item.face_url, item.avatar_url,
      item.poster_url, item.thumbnail_url, item.preview_url, item.detail_url,
      item.pic, item.pic_url, item.head_url, item.head, item.thumb, item.thumbnail
    ];
    for (var i = 0; i < candidates.length; i += 1) {
      var basename = assetBasename(candidates[i]);
      if (basename && avatarCoverManifest.by_basename[basename]) {
        return avatarCoverManifest.by_basename[basename];
      }
    }
    return '';
  }

  function loadAvatarCoverManifest() {
    if (avatarCoverManifestPromise) return avatarCoverManifestPromise;
    avatarCoverManifestPromise = fetch(SHANJIAN_AVATAR_COVER_MANIFEST, { cache: 'no-store' })
      .then(function(resp) {
        if (!resp.ok) throw new Error('avatar cover manifest ' + resp.status);
        return resp.json();
      })
      .then(function(data) {
        avatarCoverManifest = data || { by_basename: {} };
        avatarCoverManifest.by_basename = avatarCoverManifest.by_basename || {};
        return avatarCoverManifest;
      })
      .catch(function(err) {
        avatarCoverManifest = { by_basename: {} };
        if (window.console && console.warn) console.warn('[shanjian] local avatar cover manifest unavailable', err);
        return avatarCoverManifest;
      });
    return avatarCoverManifestPromise;
  }

  function bindVoicePreviewButtons() {
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-preview-play-btn'), function(btn) {
      btn.onclick = function(ev) {
        if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation();
        var url = btn.getAttribute('data-preview-url') || '';
        if (voicePreviewButton === btn && voicePreviewPlaying) {
          stopVoicePreview();
          return;
        }
        stopVoicePreview(btn);
        var params = readVoiceParamsFromScope(btn);
        voicePreviewButton = btn;
        btn.classList.add('is-playing');
        btn.innerHTML = '<span class="shanjian-preview-play-icon">■</span><span>生成中</span>';
        var scope = btn.closest ? btn.closest('.shanjian-voice-card,.shanjian-selected-voice') : null;
        var voiceId = btn.getAttribute('data-preview-voice') || '';
        if (scope && scope.querySelector) {
          var paramPanel = scope.querySelector('.shanjian-voice-param-panel');
          if (!voiceId) voiceId = paramPanel && paramPanel.getAttribute ? (paramPanel.getAttribute('data-voice-id') || '') : '';
        }
        var foundVoice = findVoiceGroupByVoiceIdLoose(voiceId);
        var provider = btn.getAttribute('data-preview-provider')
          || (foundVoice && foundVoice.group ? foundVoice.group.provider : '')
          || (state.selectedVoice && state.selectedVoice.voice === voiceId ? state.selectedVoice.provider : '');
        if (provider) btn.setAttribute('data-preview-provider', provider);
        var sampleText = previewScriptText();
        var previewPromise;
        var canPreviewByTts = voiceId && !isConsumerPreviewVoice(voiceId);
        if (sampleText && canPreviewByTts) {
          previewPromise = requestCloud('/api/hifly/my/voice/preview-tts', previewTtsPayload(voiceId, sampleText, params, provider)).then(function(data) {
            return data.audio_url || '';
          });
        } else if (url) {
          previewPromise = Promise.resolve(url);
        } else if ((provider === 'minimax' || provider === 'qwen') && canPreviewByTts) {
          previewPromise = requestCloud('/api/hifly/my/voice/preview-tts', previewTtsPayload(
            voiceId,
            '你好，这是声音试听。当前声音参数会参与重新合成。',
            params,
            provider
          )).then(function(data) {
            return data.audio_url || '';
          });
        } else {
          previewPromise = Promise.resolve('');
        }
        previewPromise.then(function(playUrl) {
          if (!playUrl) throw new Error('当前声音没有试听音频。');
          if (voicePreviewButton === btn) btn.innerHTML = '<span class="shanjian-preview-play-icon">■</span><span>停止</span>';
          return playVoicePreviewWithParams(playUrl, params, btn);
        }).catch(function(err) {
          stopVoicePreview();
          showMessage(err && err.message ? err.message : '试听音频播放失败，请刷新后重试', true);
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
    var payload = Object.assign({ token: tokenValue() }, body || {});
    if (path === '/api/hifly/my/voice/preview-tts' || path === '/api/shanjian-digital-human/video/create') {
      try {
        console.log('[shanjian-debug] request', path, payload);
      } catch (logErr) {}
    }
    return fetch(base + path, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(payload)
    }).catch(function(err) {
      var raw = err && err.message ? err.message : '';
      if (raw === 'Failed to fetch' || /NetworkError|Load failed/i.test(raw)) {
        throw new Error('连接服务器失败，请检查网络后重试。接口：' + path);
      }
      throw err;
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (path === '/api/hifly/my/voice/preview-tts' || path === '/api/shanjian-digital-human/video/create') {
          try {
            console.log('[shanjian-debug] response', path, { status: resp.status, ok: resp.ok, data: data });
          } catch (respLogErr) {}
        }
        if (!resp.ok || data.ok === false) {
          var msg = data.detail || data.error || data.message || '请求失败';
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        return data;
      });
    });
  }

  function request(path, body) {
    return requestTo(shanjianApiBase(path), path, body);
  }

  function requestCloud(path, body) {
    return requestTo(cloudBaseUrl(), path, body);
  }

  function requestLibrary(path, body) {
    var local = baseUrl();
    var cloud = cloudBaseUrl();
    if (local) {
      return requestTo(local, path, body).catch(function(err) {
        if (cloud) return requestTo(cloud, path, body);
        throw err;
      });
    }
    if (cloud) return requestTo(cloud, path, body);
    return request(path, body);
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
    return requestFormTo(shanjianApiBase(path), path, formData);
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

  function requestGet(path) {
    return requestGetTo(shanjianApiBase(path), path);
  }

  function requestCloudDelete(path) {
    return fetch(cloudBaseUrl() + path, {
      method: 'DELETE',
      headers: Object.assign({ 'Accept': 'application/json' }, authHeadersSafe())
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) {
          var msg = data.detail || data.error || data.message || '删除失败';
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        return data;
      });
    });
  }

  function requestDelete(path) {
    return requestCloudDelete(path);
  }

  function assetUploadHeaders() {
    var headers = authHeadersSafe();
    delete headers['Content-Type'];
    delete headers['content-type'];
    return headers;
  }

  function uploadAssetFile(file) {
    var fd = new FormData();
    fd.append('file', file);
    return fetch((cloudBaseUrl() || baseUrl()) + '/api/assets/upload', {
      method: 'POST',
      headers: assetUploadHeaders(),
      body: fd
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) {
          var msg = data.detail || data.error || data.message || ('上传失败: HTTP ' + resp.status);
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        if (!data.asset_id) throw new Error('上传成功，但没有拿到素材 ID。');
        return data;
      });
    });
  }

  function taskStatusKind(status) {
    var raw = String(status || '').trim().toLowerCase();
    if (raw === '3' || raw === 'success' || raw === 'succeed' || raw === 'completed' || raw === 'done') return 'success';
    if (raw === '4' || raw === 'failed' || raw === 'error' || raw === 'failure') return 'failed';
    return 'processing';
  }

  function isTaskSuccessStatus(status) {
    return taskStatusKind(status) === 'success';
  }

  function isTaskFailedStatus(status) {
    return taskStatusKind(status) === 'failed';
  }

  function defaultShanjianAuthText() {
    return '案例：我是xxx(真实姓名),我授权【必火】使用视频中的肖像、声音,为我生成定制数字人及声音,并在本人【必火】账号中创作使用。';
  }

  function normalizeProfileItem(item) {
    item = item || {};
    return Object.assign({}, item, {
      avatar: item.virtualman_id || item.avatar || ('profile_' + String(item.id || '')),
      source_type: item.source_url && /\.(mp4|mov|webm|m4v)(\?|#|$)/i.test(String(item.source_url)) ? 'video' : 'image',
      image_url: item.cover_url || item.source_url || item.image_url || '',
      detail_url: item.source_url || item.cover_url || item.detail_url || '',
      section_label: item.section_label || '我的数字人',
      material_count: item.material_count || 1,
      is_mine: true
    });
  }

  function normalizeVideoHistoryItem(item) {
    item = item || {};
    var kind = taskStatusKind(item.status);
    return Object.assign({}, item, {
      status_kind: kind
    });
  }

  function showMessage(text, isError) {
    var el = $('shanjianMsg');
    if (!el) return;
    el.textContent = text || '';
    el.style.display = text ? 'block' : 'none';
    el.classList.toggle('err', !!isError);
  }

  function setFieldError(inputId, errorId, message) {
    var input = $(inputId);
    var error = $(errorId);
    if (input) input.classList.toggle('is-error', !!message);
    if (!error) return;
    error.textContent = message || '';
    error.style.display = message ? 'block' : 'none';
  }

  function showConfirmDialog(options) {
    options = options || {};
    return new Promise(function(resolve) {
      var host = $('content-shanjian-digital-human') || document.body;
      var modal = $('shanjianConfirmModal');
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'shanjianConfirmModal';
        modal.className = 'shanjian-modal shanjian-confirm-modal';
        modal.style.display = 'none';
        modal.innerHTML = ''
          + '<div class="shanjian-modal-backdrop" data-confirm-action="cancel"></div>'
          + '<div class="shanjian-modal-card shanjian-confirm-card" role="dialog" aria-modal="true" aria-labelledby="shanjianConfirmTitle">'
          + '<div class="shanjian-modal-head">'
          + '<h4 id="shanjianConfirmTitle" class="shanjian-modal-title"></h4>'
          + '<button type="button" class="shanjian-modal-close" data-confirm-action="cancel" aria-label="关闭">×</button>'
          + '</div>'
          + '<div class="shanjian-modal-body"><p id="shanjianConfirmMessage" class="shanjian-confirm-copy"></p></div>'
          + '<div class="shanjian-modal-foot">'
          + '<button type="button" class="btn btn-ghost" data-confirm-action="cancel">取消</button>'
          + '<button type="button" class="btn btn-primary shanjian-confirm-submit" data-confirm-action="confirm">确认</button>'
          + '</div>'
          + '</div>';
        host.appendChild(modal);
      }
      var titleEl = modal.querySelector('#shanjianConfirmTitle');
      var messageEl = modal.querySelector('#shanjianConfirmMessage');
      var submitBtn = modal.querySelector('.shanjian-confirm-submit');
      if (titleEl) titleEl.textContent = options.title || '确认操作';
      if (messageEl) messageEl.textContent = options.message || '';
      if (submitBtn) {
        submitBtn.textContent = options.confirmText || '确认';
        submitBtn.classList.toggle('shanjian-confirm-danger', options.tone === 'danger');
      }
      var done = false;
      function finish(value) {
        if (done) return;
        done = true;
        modal.style.display = 'none';
        Array.prototype.forEach.call(modal.querySelectorAll('[data-confirm-action]'), function(btn) {
          btn.onclick = null;
        });
        var stillOpen = Array.prototype.some.call(document.querySelectorAll('.shanjian-modal'), function(item) {
          return item.style.display !== 'none';
        });
        if (!stillOpen) document.body.classList.remove('shanjian-modal-open');
        resolve(!!value);
      }
      Array.prototype.forEach.call(modal.querySelectorAll('[data-confirm-action]'), function(btn) {
        btn.onclick = function(ev) {
          if (ev && ev.stopPropagation) ev.stopPropagation();
          finish(btn.getAttribute('data-confirm-action') === 'confirm');
        };
      });
      modal.style.display = 'block';
      document.body.classList.add('shanjian-modal-open');
      if (submitBtn && submitBtn.focus) submitBtn.focus();
    });
  }

  function getVideoCreateMode() {
    return state.videoCreateMode === 'audio' ? 'audio' : 'tts';
  }

  function setVideoCreateMode(mode) {
    state.videoCreateMode = mode === 'audio' ? 'audio' : 'tts';
    syncVideoCreateModeUI();
  }

  function getAudioDriveDurationSeconds() {
    var preview = $('shanjianAudioDrivePreview');
    if (!preview) return 0;
    var audio = preview.querySelector('audio');
    if (!audio) return 0;
    var duration = Number(audio.duration || 0);
    return isFinite(duration) && duration > 0 ? Math.ceil(duration) : 0;
  }

  function syncVideoCreateModeUI() {
    var mode = getVideoCreateMode();
    var ttsBtn = $('shanjianVideoModeTtsBtn');
    var audioBtn = $('shanjianVideoModeAudioBtn');
    var ttsFields = $('shanjianVideoTtsFields');
    var audioFields = $('shanjianVideoAudioFields');
    var voicePanelHint = $('shanjianSelectedVoiceModeHint');
    var voiceBtn = $('shanjianOpenVoiceLibraryBtn');

    if (ttsBtn) ttsBtn.classList.toggle('is-active', mode === 'tts');
    if (audioBtn) audioBtn.classList.toggle('is-active', mode === 'audio');
    if (ttsFields) ttsFields.style.display = mode === 'tts' ? 'block' : 'none';
    if (audioFields) audioFields.style.display = mode === 'audio' ? 'block' : 'none';
    if (voicePanelHint) {
      voicePanelHint.style.display = mode === 'audio' ? 'block' : 'none';
    }
    if (voiceBtn) {
      voiceBtn.disabled = mode === 'audio';
      voiceBtn.classList.toggle('is-disabled', mode === 'audio');
    }
    syncTemplateModeUI();
  }

  function syncTemplateModeUI() {
    var hasTemplate = !!(state.selectedTemplate && state.selectedTemplate.id);
    var panel = $('shanjianTemplateSmartParams');
    var hint = $('shanjianTemplateModeHint');
    var submitHint = $('shanjianGenerateModeHint');
    if (panel) panel.style.display = hasTemplate ? 'block' : 'none';
    if (hint) hint.style.display = hasTemplate ? 'block' : 'none';
    if (submitHint) {
      submitHint.textContent = hasTemplate
        ? '已选择真人视频模板，提交时会自动带上模板场景和剪辑参数。'
        : '不选模板时会走普通数字人口播；选了模板后才会切到真人视频模板剪辑。';
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
    var localCover = localAvatarCoverFor(item);
    if (localCover) return localCover;
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
    var localCover = localAvatarCoverFor(item);
    if (localCover) return localCover;
    if (item && item.detail_url) return item.detail_url;
    if (item && item.image_url) return item.image_url;
    return coverSrc(item);
  }

  /** 懒创建「数字人详情」弹窗（图片/视频预览）。
      注意：.shanjian-modal 系列 CSS 都 scoped 在 #content-shanjian-digital-human 下；
      所以必须 append 到该容器，否则样式不生效（弹窗看不到）。 */
  function ensureAvatarDetailModal() {
    var modal = document.getElementById('shanjianAvatarDetailModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'shanjianAvatarDetailModal';
    modal.className = 'shanjian-modal';
    /** 内联兜底：万一 append 到 body 也能定位与置顶 */
    modal.style.cssText = 'display:none;position:fixed;inset:0;z-index:6000;';
    modal.innerHTML = ''
      + '<div class="shanjian-modal-backdrop" data-modal-close="shanjianAvatarDetailModal" style="position:absolute;inset:0;background:rgba(15,23,42,0.58);"></div>'
      + '<div class="shanjian-modal-card" role="dialog" aria-modal="true" style="position:relative;width:min(92vw,720px);max-height:92vh;overflow:auto;margin:4vh auto 0;border-radius:24px;background:#fff;box-shadow:0 32px 80px rgba(15,23,42,0.28);">'
      +   '<div class="shanjian-modal-head" style="display:flex;align-items:center;justify-content:space-between;padding:1rem 1.2rem;border-bottom:1px solid rgba(15,23,42,0.08);">'
      +     '<h4 class="shanjian-modal-title" id="shanjianAvatarDetailTitle" style="margin:0;font-size:1.1rem;color:#111827;">数字人详情</h4>'
      +     '<button type="button" class="shanjian-modal-close" data-modal-close="shanjianAvatarDetailModal" aria-label="关闭" style="border:none;background:transparent;font-size:1.4rem;cursor:pointer;color:#6b7280;">×</button>'
      +   '</div>'
      +   '<div class="shanjian-modal-body" id="shanjianAvatarDetailBody" style="padding:1rem;display:flex;justify-content:center;align-items:center;background:#000;min-height:320px;max-height:75vh;overflow:hidden;"></div>'
      +   '<div class="shanjian-modal-foot" style="display:flex;justify-content:flex-end;padding:0.8rem 1.2rem;border-top:1px solid rgba(15,23,42,0.08);">'
      +     '<button type="button" class="btn btn-ghost" data-modal-close="shanjianAvatarDetailModal">关闭</button>'
      +   '</div>'
      + '</div>';
    document.body.appendChild(modal);
    /** data-modal-close 点击均关闭 */
    Array.prototype.forEach.call(modal.querySelectorAll('[data-modal-close]'), function(btn) {
      btn.addEventListener('click', function() { closeAvatarDetailModal(); });
    });
    return modal;
  }

  function closeAvatarDetailModal() {
    var modal = document.getElementById('shanjianAvatarDetailModal');
    if (!modal) return;
    var body = document.getElementById('shanjianAvatarDetailBody');
    if (body) body.innerHTML = '';
    modal.style.display = 'none';
    document.body.classList.remove('shanjian-modal-open');
  }

  function openAvatarDetail(item) {
    var detailUrl = avatarDetailSrc(item);
    if (!detailUrl) {
      showMessage('当前数字人暂无可预览的素材。', true);
      return;
    }
    ensureAvatarDetailModal();
    var titleEl = document.getElementById('shanjianAvatarDetailTitle');
    var body = document.getElementById('shanjianAvatarDetailBody');
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
    var modal = document.getElementById('shanjianAvatarDetailModal');
    if (modal) {
      modal.style.display = 'block';
      document.body.classList.add('shanjian-modal-open');
    }
  }

  function ensureTemplatePreviewModal() {
    var modal = document.getElementById('shanjianTemplatePreviewModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'shanjianTemplatePreviewModal';
    modal.className = 'shanjian-modal';
    modal.style.cssText = 'display:none;position:fixed;inset:0;z-index:7000;';
    modal.innerHTML = ''
      + '<div class="shanjian-modal-backdrop" data-modal-close="shanjianTemplatePreviewModal" style="position:absolute;inset:0;background:rgba(15,23,42,0.58);"></div>'
      + '<div class="shanjian-modal-card" role="dialog" aria-modal="true" aria-labelledby="shanjianTemplatePreviewTitle" style="position:relative;width:min(92vw,840px);max-height:92vh;overflow:auto;margin:4vh auto 0;border-radius:24px;background:#fff;box-shadow:0 32px 80px rgba(15,23,42,0.28);">'
      +   '<div class="shanjian-modal-head" style="display:flex;align-items:center;justify-content:space-between;padding:1rem 1.2rem;border-bottom:1px solid rgba(15,23,42,0.08);">'
      +     '<h4 class="shanjian-modal-title" id="shanjianTemplatePreviewTitle" style="margin:0;font-size:1.1rem;color:#111827;">模板样片</h4>'
      +     '<button type="button" class="shanjian-modal-close" data-modal-close="shanjianTemplatePreviewModal" aria-label="关闭" style="border:none;background:transparent;font-size:1.4rem;cursor:pointer;color:#6b7280;">×</button>'
      +   '</div>'
      +   '<div class="shanjian-modal-body" id="shanjianTemplatePreviewBody" style="padding:1rem;display:flex;justify-content:center;align-items:center;background:#000;min-height:360px;max-height:75vh;overflow:hidden;"></div>'
      +   '<div class="shanjian-modal-foot" style="display:flex;justify-content:flex-end;gap:0.6rem;padding:0.8rem 1.2rem;border-top:1px solid rgba(15,23,42,0.08);">'
      +     '<button type="button" class="btn btn-ghost" data-modal-close="shanjianTemplatePreviewModal">关闭</button>'
      +     '<button type="button" class="btn btn-primary" id="shanjianUseTemplateFromPreviewBtn">使用此模板</button>'
      +   '</div>'
      + '</div>';
    document.body.appendChild(modal);
    Array.prototype.forEach.call(modal.querySelectorAll('[data-modal-close]'), function(btn) {
      btn.addEventListener('click', function() { closeTemplatePreviewModal(); });
    });
    var useBtn = modal.querySelector('#shanjianUseTemplateFromPreviewBtn');
    if (useBtn) {
      useBtn.addEventListener('click', function() {
        if (state.templatePreviewItem) {
          selectTemplate(state.templatePreviewItem, true);
        }
        closeTemplatePreviewModal();
      });
    }
    return modal;
  }

  function closeTemplatePreviewModal() {
    var modal = document.getElementById('shanjianTemplatePreviewModal');
    if (!modal) return;
    var body = document.getElementById('shanjianTemplatePreviewBody');
    if (body) body.innerHTML = '';
    state.templatePreviewItem = null;
    modal.style.display = 'none';
    var stillOpen = Array.prototype.some.call(document.querySelectorAll('.shanjian-modal'), function(item) {
      return item.style.display !== 'none';
    });
    if (!stillOpen) document.body.classList.remove('shanjian-modal-open');
  }

  function openTemplatePreview(item) {
    item = item || null;
    if (!item) return;
    ensureTemplatePreviewModal();
    state.templatePreviewItem = item;
    var titleEl = document.getElementById('shanjianTemplatePreviewTitle');
    var body = document.getElementById('shanjianTemplatePreviewBody');
    if (titleEl) titleEl.textContent = item.name ? String(item.name) : '模板样片';
    if (body) {
      body.innerHTML = '';
      if (item.demo_url) {
        var src = escapeHtml(item.demo_url);
        var isVideo = /\.(mp4|webm|mov)(\?|$)/i.test(String(item.demo_url || ''));
        body.innerHTML = isVideo
          ? '<video src="' + src + '" controls autoplay playsinline style="max-width:100%;max-height:70vh;background:#000;"></video>'
          : '<img src="' + src + '" alt="" style="max-width:100%;max-height:70vh;object-fit:contain;background:#000;">';
      } else {
        body.innerHTML = '<div style="color:#fff;padding:1rem 1.2rem;">当前模板暂无样片。</div>';
      }
    }
    var modal = document.getElementById('shanjianTemplatePreviewModal');
    if (modal) {
      modal.style.display = 'block';
      document.body.classList.add('shanjian-modal-open');
    }
  }

  function saveVoiceParams(voiceId, params) {
    return requestCloud('/api/hifly/my/voice/edit', Object.assign({ voice: voiceId }, params));
  }

  function setVoiceParamPanelStatus(panel, text, tone) {
    var status = panel && panel.querySelector ? panel.querySelector('.shanjian-voice-param-status') : null;
    if (!status) return;
    status.textContent = text || '';
    status.setAttribute('data-tone', tone || 'idle');
  }

  function readVoiceParamPanelValues(panel) {
    var payload = {};
    Array.prototype.forEach.call((panel && panel.querySelectorAll) ? panel.querySelectorAll('.shanjian-voice-param-input') : [], function(input) {
      payload[input.getAttribute('data-param')] = input.value;
    });
    return {
      rate: String(numericParam(payload.rate, 1, 0.5, 2)),
      volume: String(numericParam(payload.volume, 1, 0.1, 2)),
      pitch: String(intParam(payload.pitch, 0, -12, 12)),
      emotion: 'happy'
    };
  }

  function currentSelectedVoiceParams() {
    var base = state.selectedVoice && state.selectedVoice.voice_params ? Object.assign({}, state.selectedVoice.voice_params) : {};
    var selectedVoiceId = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : '';
    if (selectedVoiceId) {
      var panel = document.querySelector('.shanjian-voice-param-panel[data-voice-id="' + cssEscape(selectedVoiceId) + '"]');
      if (panel) base = Object.assign(base, readVoiceParamPanelValues(panel));
    }
    return {
      rate: String(numericParam(base.rate, 1, 0.5, 2)),
      volume: String(numericParam(base.volume, 1, 0.1, 2)),
      pitch: String(intParam(base.pitch, 0, -12, 12)),
      instructions: String(base.instructions || '')
    };
  }

  function mergeVoiceParamsLocally(voiceId, params, updated) {
    params = params || {};
    state.voiceLibrary.mine = (state.voiceLibrary.mine || []).map(function(item) {
      var styles = voiceStyles(item);
      var matched = item.voice === voiceId || styles.some(function(style) { return style.voice === voiceId; });
      if (!matched) return item;
      var next = updated && (String(updated.id) === String(item.id) || updated.voice === item.voice || updated.voice === voiceId)
        ? Object.assign({}, item, updated, { is_mine: true })
        : Object.assign({}, item);
      next.voice_params = Object.assign({}, next.voice_params || {}, params);
      return next;
    });
    if (state.selectedVoice && state.selectedVoice.voice === voiceId) {
      state.selectedVoice.voice_params = Object.assign({}, state.selectedVoice.voice_params || {}, params);
    }
  }

  function syncVoicePreviewButtonParams(voiceId, params) {
    if (!voiceId || !params) return;
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-preview-play-btn'), function(btn) {
      if ((btn.getAttribute('data-preview-voice') || '') !== voiceId) return;
      btn.setAttribute('data-preview-provider', 'minimax');
      btn.setAttribute('data-preview-rate', String(params.rate || '1'));
      btn.setAttribute('data-preview-volume', String(params.volume || '1'));
      btn.setAttribute('data-preview-pitch', String(params.pitch || '0'));
      btn.setAttribute('data-preview-emotion', String(params.emotion || 'happy'));
    });
  }

  function saveVoiceParamPanel(panel, options) {
    options = options || {};
    if (!panel) return Promise.resolve();
    var voiceId = panel.getAttribute('data-voice-id') || '';
    if (!voiceId) return Promise.resolve();
    var params = readVoiceParamPanelValues(panel);
    panel.setAttribute('data-saving', '1');
    setVoiceParamPanelStatus(panel, '保存中...', 'busy');
    return saveVoiceParams(voiceId, params)
      .then(function(data) {
        var updated = data && data.item ? data.item : null;
        mergeVoiceParamsLocally(voiceId, params, updated);
        setVoiceParamPanelStatus(panel, '已自动保存', 'ok');
        var title = panel.querySelector('.shanjian-voice-param-title span');
        if (title) title.textContent = voiceParamSummary(params, true);
      })
      .catch(function(err) {
        setVoiceParamPanelStatus(panel, '保存失败', 'danger');
        if (!options.silent) showMessage(err && err.message ? err.message : '保存声音参数失败', true);
      })
      .finally(function() {
        panel.removeAttribute('data-saving');
      });
  }

  function scheduleVoiceParamPanelSave(input) {
    var panel = input && input.closest ? input.closest('.shanjian-voice-param-panel') : null;
    if (!panel) return;
    var voiceId = panel.getAttribute('data-voice-id') || '';
    if (!voiceId) return;
    var params = readVoiceParamPanelValues(panel);
    mergeVoiceParamsLocally(voiceId, params, null);
    syncVoicePreviewButtonParams(voiceId, params);
    setVoiceParamPanelStatus(panel, '待自动保存', 'idle');
    clearTimeout(voiceParamSaveTimers[voiceId]);
    voiceParamSaveTimers[voiceId] = setTimeout(function() {
      saveVoiceParamPanel(panel, { silent: true });
    }, 650);
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
        demo_url: item.demo_url || '',
        provider: item.provider || item.voice_provider || ''
      }];
    }
    return [];
  }

  function submittableVoiceStyles(item) {
    return voiceStyles(item).filter(function(style) {
      return style && style.voice && !isConsumerPreviewVoice(style.voice);
    });
  }

  function voiceProvider(item, style) {
    var raw = ''
      || (style && (style.provider || style.voice_provider))
      || (item && (item.provider || item.voice_provider))
      || '';
    raw = String(raw || '').trim().toLowerCase();
    if (raw === 'minimax' || raw === 'qwen') return raw;
    var voiceId = String(
      (style && style.voice)
      || (item && (item.voice || item.shanjian_voice_id))
      || ''
    ).trim().toLowerCase();
    if (voiceId.indexOf('qwen-') === 0 || voiceId.indexOf('qwen_') === 0) return 'qwen';
    if (voiceId.indexOf('lobster_u') === 0 || voiceId.indexOf('minimax') >= 0) return 'minimax';
    return '';
  }

  function isSubmittableVoiceGroup(item) {
    return submittableVoiceStyles(item).length > 0;
  }

  function filterSubmittableVoiceGroups(rows) {
    return (rows || []).filter(isSubmittableVoiceGroup).map(function(item) {
      var cloned = Object.assign({}, item);
      var styles = submittableVoiceStyles(cloned);
      cloned.styles = styles;
      cloned.style_count = styles.length;
      if (!cloned.voice || isConsumerPreviewVoice(cloned.voice)) {
        cloned.voice = styles[0] && styles[0].voice ? styles[0].voice : '';
      }
      cloned.tags = (cloned.tags || []).filter(function(tag) {
        var text = String(tag || '').trim();
        return text && text !== '可试听' && text !== '仅试听';
      });
      if (!cloned.tags.length) cloned.tags = ['公共声音'];
      return cloned;
    });
  }

  function voiceParams(item) {
    var params = item && item.voice_params && typeof item.voice_params === 'object' ? item.voice_params : {};
    var style = getSelectedStyleForVoiceItem(item) || {};
    return {
      rate: '1',
      volume: '1',
      pitch: '0',
      emotion: String(params.emotion || 'happy'),
      instructions: String(params.instructions || item && item.instructions || style.instructions || '')
    };
  }

  function previewUrlForVoiceId(voiceId, fallbackUrl) {
    var cached = voiceId && state.voicePreviewMap ? state.voicePreviewMap[voiceId] : '';
    return normalizeAssetUrl(cached || fallbackUrl || '');
  }

  function downloadUrlForVideo(videoUrl, filename) {
    var local = baseUrl();
    var safeName = filename || 'digital-human.mp4';
    if (!local) return videoUrl || '';
    return local + '/api/hifly/video/download?url=' + encodeURIComponent(videoUrl || '') + '&filename=' + encodeURIComponent(safeName);
  }

  function openExternalUrl(url) {
    if (!url) return;
    try {
      var opened = window.open(url, '_blank', 'noopener');
      if (opened) return;
    } catch (e) {}
    window.location.href = url;
  }

  function bindVideoResultActions() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-shanjian-video-download]'), function(btn) {
      btn.onclick = function() {
        var url = btn.getAttribute('data-shanjian-video-download') || '';
        if (!url) return showMessage('视频地址为空，无法下载。', true);
        openExternalUrl(downloadUrlForVideo(url, btn.getAttribute('data-download-filename') || 'digital-human.mp4'));
        showMessage('已发起下载。如果系统没有弹出下载，请点击“打开视频”后在浏览器中另存。', false);
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-shanjian-video-open]'), function(btn) {
      btn.onclick = function() {
        var url = btn.getAttribute('data-shanjian-video-open') || '';
        if (!url) return showMessage('视频地址为空，无法打开。', true);
        openExternalUrl(url);
      };
    });
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
      provider: voiceProvider(group, pickedStyle),
      is_mine: group.is_mine === true,
      voice_params: voiceParams(group),
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
    state.activeView = viewName === 'voice'
      ? 'voice'
      : (viewName === 'template'
        ? 'template'
        : (viewName === 'material'
          ? 'material'
          : (viewName === 'result' ? 'result' : 'avatar')));
    if ($('shanjianAvatarLibraryView')) $('shanjianAvatarLibraryView').style.display = state.activeView === 'avatar' ? 'block' : 'none';
    if ($('shanjianVoiceLibraryView')) $('shanjianVoiceLibraryView').style.display = state.activeView === 'voice' ? 'block' : 'none';
    if ($('shanjianTemplateLibraryView')) $('shanjianTemplateLibraryView').style.display = state.activeView === 'template' ? 'block' : 'none';
    if ($('shanjianMaterialLibraryView')) $('shanjianMaterialLibraryView').style.display = state.activeView === 'material' ? 'block' : 'none';
    if ($('shanjianResultView')) $('shanjianResultView').style.display = state.activeView === 'result' ? 'block' : 'none';
    if ($('shanjianAvatarLibraryTabBtn')) $('shanjianAvatarLibraryTabBtn').classList.toggle('is-active', state.activeView === 'avatar');
    if ($('shanjianVoiceLibraryTabBtn')) $('shanjianVoiceLibraryTabBtn').classList.toggle('is-active', state.activeView === 'voice');
    if ($('shanjianTemplateLibraryTabBtn')) $('shanjianTemplateLibraryTabBtn').classList.toggle('is-active', state.activeView === 'template');
    if ($('shanjianMaterialLibraryTabBtn')) $('shanjianMaterialLibraryTabBtn').classList.toggle('is-active', state.activeView === 'material');
    if ($('shanjianResultTabBtn')) $('shanjianResultTabBtn').classList.toggle('is-active', state.activeView === 'result');
    if (state.activeView === 'material') {
      loadMaterialPickerItems(false).catch(function() {});
      renderMaterialLibrary();
    }
  }

  function updateTaskStatus(text, tone) {
    var el = $('shanjianTaskStatusText');
    if (!el) return;
    el.textContent = text || '--';
    el.setAttribute('data-tone', tone || 'idle');
  }

  function updateTaskKind(text) {
    state.taskKindLabel = text || '未开始';
    var el = $('shanjianTaskKindText');
    if (!el) return;
    el.textContent = state.taskKindLabel;
  }

  function renderResultPlaceholder(title, subtitle, isBusy) {
    var el = $('shanjianResultSurface');
    if (!el) return;
    el.innerHTML = ''
      + '<div class="viral-video-placeholder">'
      + '<div class="shanjian-result-status-head">'
      + (isBusy ? '<span class="shanjian-result-spinner" aria-hidden="true"></span>' : '')
      + '<div class="shanjian-result-status-copy">'
      + '<strong>' + escapeHtml(title || '等待生成') + '</strong>'
      + '<span>' + escapeHtml(subtitle || '提交后这里会自动显示任务进度和最终结果。') + '</span>'
      + '</div>'
      + '</div>'
      + '</div>';
  }

  function renderResultVideo(videoUrl) {
    var el = $('shanjianResultSurface');
    if (!el) return;
    el.innerHTML = ''
      + '<div class="shanjian-video-result-wrap">'
      + '<video src="' + escapeHtml(videoUrl) + '" controls style="width:100%;height:100%;object-fit:contain;background:#000;border-radius:20px;"></video>'
      + '<div class="shanjian-video-result-actions">'
      + '<button type="button" class="btn btn-primary btn-sm" data-shanjian-video-download="' + escapeHtml(videoUrl) + '" data-download-filename="digital-human.mp4">下载视频</button>'
      + '<button type="button" class="btn btn-ghost btn-sm" data-shanjian-video-open="' + escapeHtml(videoUrl) + '">打开视频</button>'
      + '</div>'
      + '</div>';
    bindVideoResultActions();
  }

  function renderResultSuccessCard(title, bodyHtml) {
    var el = $('shanjianResultSurface');
    if (!el) return;
    el.innerHTML = ''
      + '<div class="shanjian-success-card">'
      + '<strong>' + escapeHtml(title) + '</strong>'
      + bodyHtml
      + '</div>';
  }

  function renderAvatarSuccessBody(item) {
    var preview = item ? avatarMedia(item, 'shanjian-success-visual-media') : '';
    var visual = preview ? '<div class="shanjian-success-visual">' + preview + '</div>' : '';
    return ''
      + visual
      + '<span>任务已完成，新的数字人已经刷新到“我的数字人”。</span>';
  }

  function setBusy(flag) {
    state.submitting = !!flag;
    [
      'shanjianGenerateBtn',
      'shanjianLoadVoicesBtn',
      'shanjianRefreshLibraryBtn',
      'shanjianRefreshTemplatesBtn',
      'shanjianTemplateMoreBtn',
      'shanjianOpenAvatarCreateBtn',
      'shanjianOpenVoiceCreateBtn',
      'shanjianCreateVoiceBtn',
      'shanjianOpenMaterialLibraryBtn',
      'shanjianOpenMaterialLibraryBtnInline',
      'shanjianUploadMaterialBtnInline',
      'shanjianClearMaterialsBtnInline',
      'shanjianMaterialReloadBtn'
    ].forEach(function(id) {
      var btn = $(id);
      if (btn) btn.disabled = !!flag;
    });
    syncCreateSubmitStates();
    syncVoiceRecordButtons();
  }

  function syncCreateSubmitStates() {
    [
      ['shanjianAvatarImageAgree', 'shanjianAvatarImageSubmitBtn'],
      ['shanjianAvatarVideoAgree', 'shanjianAvatarVideoSubmitBtn'],
      ['shanjianVoiceCreateAgree', 'shanjianVoiceSubmitBtn']
    ].forEach(function(pair) {
      var checkbox = $(pair[0]);
      var button = $(pair[1]);
      if (!button) return;
      button.disabled = !!state.submitting || !(checkbox && checkbox.checked);
    });
    syncVoiceRecordButtons();
  }

  function selectAvatar(item, shouldScroll) {
    state.selectedAvatar = item || null;
    if ($('shanjianAvatarInput')) $('shanjianAvatarInput').value = item && item.avatar ? item.avatar : '';
    renderSelectedAvatar();
    renderAvatarLibrary();
    if (shouldScroll && window.innerWidth < 1100) {
      var preview = $('shanjianSelectedAvatarPreview');
      if (preview && typeof preview.scrollIntoView === 'function') {
        preview.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  }

  function selectVoice(item, shouldScroll) {
    state.selectedVoice = buildSelectedVoice(item, getSelectedStyleForVoiceItem(item)) || null;
    if ($('shanjianVoiceInput')) $('shanjianVoiceInput').value = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : '';
    renderSelectedVoice();
    renderVoiceLibrary();
    if (shouldScroll && window.innerWidth < 1100) {
      var preview = $('shanjianSelectedVoicePreview');
      if (preview && typeof preview.scrollIntoView === 'function') {
        preview.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  }

  function selectVoiceVariant(group, style, shouldScroll, overrideDemoUrl) {
    state.selectedVoice = buildSelectedVoice(group, style, overrideDemoUrl);
    if ($('shanjianVoiceInput')) $('shanjianVoiceInput').value = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : '';
    renderSelectedVoice();
    renderVoiceLibrary();
    if (shouldScroll && window.innerWidth < 1100) {
      var preview = $('shanjianSelectedVoicePreview');
      if (preview && typeof preview.scrollIntoView === 'function') {
        preview.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  }

  function renderSelectedAvatar() {
    var el = $('shanjianSelectedAvatarPreview');
    if (!el) return;
    var item = state.selectedAvatar;
    if (!item) {
      el.className = 'shanjian-selected-avatar is-empty';
      el.innerHTML = ''
        + '<div class="shanjian-selected-empty">'
        + '<strong>还没有选数字人</strong>'
        + '<span>去右侧数字人库点“选择数字人”，这里就会固定显示当前形象。</span>'
        + '</div>';
      return;
    }

    var tags = (item.tags || []).slice(0, 3).map(function(tag) {
      return '<span class="shanjian-mini-tag">' + escapeHtml(tag) + '</span>';
    }).join('');
    var countText = item.material_count ? (item.material_count + ' 个素材') : '已就绪';

    el.className = 'shanjian-selected-avatar';
    el.innerHTML = ''
      + '<div class="shanjian-selected-avatar-cover">'
      + coverImage(item)
      + '<span class="shanjian-section-chip">' + escapeHtml(item.section_label || '数字人') + '</span>'
      + '</div>'
      + '<div class="shanjian-selected-copy">'
      + '<div class="shanjian-selected-title-row"><strong>' + escapeHtml(item.title) + '</strong><span>' + escapeHtml(countText) + '</span></div>'
      + '<div class="shanjian-selected-tags">' + tags + '</div>'
      + '<button type="button" class="btn btn-ghost btn-sm shanjian-avatar-detail-btn" data-avatar-key="' + escapeHtml(avatarKey(item)) + '">查看详情</button>'
      + '</div>';
  }

  function renderSelectedVoice() {
    var el = $('shanjianSelectedVoicePreview');
    if (!el) return;
    var item = state.selectedVoice;
    if (!item) {
      el.className = 'shanjian-selected-voice is-empty';
      el.innerHTML = ''
        + '<div class="shanjian-selected-empty">'
        + '<strong>还没有选声音</strong>'
        + '<span>去右侧声音库里选择声音，或者直接创建你自己的声音。</span>'
        + '</div>';
      return;
    }

    var tags = (item.tags || []).slice(0, 4).map(function(tag) {
      return '<span class="shanjian-mini-tag">' + escapeHtml(tag) + '</span>';
    }).join('');
    var audio = voicePreviewButtonHtml(item.demo_url, voiceParams(item), { provider: voiceProvider(item, null), voice: item.voice || '' });

    el.className = 'shanjian-selected-voice';
    el.innerHTML = ''
      + '<div class="shanjian-selected-voice-cover">'
      + coverImage(item)
      + '<span class="shanjian-section-chip">' + escapeHtml(item.section_label || '声音') + '</span>'
      + '</div>'
      + '<div class="shanjian-selected-copy">'
      + '<div class="shanjian-selected-voice-head">'
      + '<strong>' + escapeHtml(item.title) + '</strong>'
      + '<span class="shanjian-section-chip is-inline">' + escapeHtml((item.style_count || 1) + ' 个风格') + '</span>'
      + '</div>'
      + '<div class="shanjian-selected-style-label">当前风格：' + escapeHtml(item.style_label || '默认风格') + '</div>'
      + '<div class="shanjian-selected-tags">' + tags + '</div>'
      + '<div class="shanjian-selected-audio">' + audio + '</div>'
      + '</div>';
    bindVoicePreviewButtons();
  }

  function normalizeTemplateItem(item) {
    item = item || {};
    return {
      id: String(item.id || '').trim(),
      name: String(item.name || item.title || '未命名模板').trim(),
      scene: String(item.scene || 'realMan').trim() || 'realMan',
      cover_url: normalizeAssetUrl(item.coverUrl || item.cover_url || ''),
      demo_url: normalizeAssetUrl(item.demoUrl || item.demo_url || '')
    };
  }

  function normalizeMaterialAsset(item) {
    item = item || {};
    var mediaType = String(item.media_type || '').trim().toLowerCase();
    if (mediaType !== 'image' && mediaType !== 'video') return null;
    var assetId = String(item.asset_id || '').trim();
    if (!assetId) return null;
    var thumb = normalizeAssetUrl(item.preview_url || item.open_url || item.source_url || '');
    return {
      asset_id: assetId,
      name: String(item.filename || item.name || assetId).trim() || assetId,
      media_type: mediaType,
      file_size: Number(item.file_size || 0) || 0,
      duration_seconds: Number(item.duration_seconds || item.duration || 0) || 0,
      width: Number(item.width || 0) || 0,
      height: Number(item.height || 0) || 0,
      preview_url: thumb,
      open_url: normalizeAssetUrl(item.open_url || item.source_url || item.preview_url || ''),
      source_url: normalizeAssetUrl(item.source_url || ''),
      selected_key: assetId
    };
  }

  function getFileExtension(name) {
    var match = String(name || '').trim().toLowerCase().match(/\.([a-z0-9]+)$/i);
    return match ? match[1] : '';
  }

  function isInlineImageExtension(ext) {
    return ['jpg', 'jpeg', 'png', 'webp'].indexOf(String(ext || '').trim().toLowerCase()) >= 0;
  }

  function isInlineVideoExtension(ext) {
    return ['mp4', 'mov'].indexOf(String(ext || '').trim().toLowerCase()) >= 0;
  }

  function currentMaterialBudgetSeconds() {
    return (state.selectedMaterials || []).reduce(function(total, item) {
      var type = String(item && item.media_type || '').trim().toLowerCase();
      if (type === 'image') return total + INLINE_MATERIAL_IMAGE_SECONDS;
      if (type === 'video') {
        var seconds = Number(item && item.duration_seconds || 0);
        if (isFinite(seconds) && seconds > 0) return total + Math.min(INLINE_MATERIAL_VIDEO_MAX_SECONDS, seconds);
      }
      return total;
    }, 0);
  }

  function getInlineMaterialMaxEdge() {
    return INLINE_MATERIAL_MAX_EDGE_1080;
  }

  function readImageFileMeta(file) {
    return new Promise(function(resolve, reject) {
      var url = '';
      var img = new Image();
      function cleanup() {
        img.onload = null;
        img.onerror = null;
        if (url) {
          try { URL.revokeObjectURL(url); } catch (e) {}
        }
      }
      img.onload = function() {
        var meta = {
          media_type: 'image',
          width: Number(img.naturalWidth || img.width || 0) || 0,
          height: Number(img.naturalHeight || img.height || 0) || 0,
          duration_seconds: INLINE_MATERIAL_IMAGE_SECONDS,
          consumed_seconds: INLINE_MATERIAL_IMAGE_SECONDS
        };
        cleanup();
        resolve(meta);
      };
      img.onerror = function() {
        cleanup();
        reject(new Error('图片读取失败，请换一个文件重试'));
      };
      try {
        url = URL.createObjectURL(file);
        img.src = url;
      } catch (err) {
        cleanup();
        reject(err);
      }
    });
  }

  function readVideoFileMeta(file) {
    return new Promise(function(resolve, reject) {
      var url = '';
      var video = document.createElement('video');
      function cleanup() {
        video.onloadedmetadata = null;
        video.onerror = null;
        try { video.removeAttribute('src'); } catch (e) {}
        try { video.load(); } catch (e2) {}
        if (url) {
          try { URL.revokeObjectURL(url); } catch (e3) {}
        }
      }
      video.preload = 'metadata';
      video.muted = true;
      video.playsInline = true;
      video.onloadedmetadata = function() {
        var seconds = Number(video.duration || 0);
        var meta = {
          media_type: 'video',
          width: Number(video.videoWidth || 0) || 0,
          height: Number(video.videoHeight || 0) || 0,
          duration_seconds: seconds,
          consumed_seconds: Math.min(INLINE_MATERIAL_VIDEO_MAX_SECONDS, Math.max(0, seconds))
        };
        cleanup();
        resolve(meta);
      };
      video.onerror = function() {
        cleanup();
        reject(new Error('视频读取失败，请换一个文件重试'));
      };
      try {
        url = URL.createObjectURL(file);
        video.src = url;
      } catch (err) {
        cleanup();
        reject(err);
      }
    });
  }

  function validateInlineMaterialFile(file, usedSeconds) {
    if (!file) return Promise.reject(new Error('请选择要上传的素材文件'));
    var ext = getFileExtension(file.name);
    var fileType = String(file.type || '').trim().toLowerCase();
    var mediaType = '';
    if (isInlineImageExtension(ext) || /^image\//.test(fileType)) mediaType = 'image';
    else if (isInlineVideoExtension(ext) || /^video\//.test(fileType)) mediaType = 'video';
    if (!mediaType) {
      return Promise.reject(new Error('仅支持 jpg、jpeg、png、webp、mp4、mov 素材'));
    }
    if (mediaType === 'video' && Number(file.size || 0) > INLINE_MATERIAL_VIDEO_MAX_BYTES) {
      return Promise.reject(new Error('视频素材不能超过 500MB：' + (file.name || '未命名文件')));
    }
    var reader = mediaType === 'image' ? readImageFileMeta : readVideoFileMeta;
    return reader(file).then(function(meta) {
      var maxEdge = getInlineMaterialMaxEdge();
      var width = Number(meta.width || 0);
      var height = Number(meta.height || 0);
      var longest = Math.max(width, height);
      var duration = Number(meta.duration_seconds || 0);
      var consumed = Number(meta.consumed_seconds || 0);
      if (mediaType === 'video') {
        if (!(isFinite(duration) && duration > 0)) throw new Error('视频时长读取失败：' + (file.name || '未命名文件'));
        if (duration > INLINE_MATERIAL_VIDEO_MAX_SECONDS) {
          throw new Error('单个视频素材不能超过 60 秒：' + (file.name || '未命名文件'));
        }
      }
      if (longest > maxEdge) {
        throw new Error('素材分辨率过大，最长边不能超过 ' + maxEdge + '：' + (file.name || '未命名文件'));
      }
      if ((Number(usedSeconds || 0) + consumed) > INLINE_MATERIAL_TOTAL_MAX_SECONDS) {
        throw new Error('混剪素材总时长不能超过 5 分钟，请减少后再上传');
      }
      return Object.assign({ extension: ext, file_size: Number(file.size || 0) || 0 }, meta);
    });
  }

  function inferMaterialMediaType(item, file) {
    var mediaType = String(item && item.media_type || '').trim().toLowerCase();
    if (mediaType === 'image' || mediaType === 'video') return mediaType;
    var ext = getFileExtension((item && (item.filename || item.name)) || (file && file.name) || '');
    var fileType = String((item && (item.content_type || item.mime_type)) || (file && file.type) || '').trim().toLowerCase();
    if (isInlineImageExtension(ext) || /^image\//.test(fileType)) return 'image';
    if (isInlineVideoExtension(ext) || /^video\//.test(fileType)) return 'video';
    return '';
  }

  function materialAssetFromUpload(uploaded, file, meta) {
    uploaded = uploaded || {};
    meta = meta || {};
    var mediaType = inferMaterialMediaType(uploaded, file) || String(meta.media_type || '').trim().toLowerCase();
    var normalized = normalizeMaterialAsset({
      asset_id: uploaded.asset_id || uploaded.id || '',
      filename: uploaded.filename || uploaded.name || (file && file.name) || '',
      media_type: mediaType,
      file_size: uploaded.file_size || uploaded.size || (file && file.size) || meta.file_size || 0,
      duration_seconds: uploaded.duration_seconds || uploaded.duration || meta.duration_seconds || 0,
      width: uploaded.width || meta.width || 0,
      height: uploaded.height || meta.height || 0,
      preview_url: uploaded.preview_url || uploaded.thumbnail_url || uploaded.thumb_url || uploaded.open_url || uploaded.source_url || uploaded.url || uploaded.file_url || uploaded.asset_url || '',
      open_url: uploaded.open_url || uploaded.source_url || uploaded.url || uploaded.file_url || uploaded.asset_url || uploaded.preview_url || '',
      source_url: uploaded.source_url || uploaded.open_url || uploaded.url || uploaded.file_url || uploaded.asset_url || uploaded.preview_url || ''
    });
    if (normalized) return normalized;
    return {
      asset_id: String(uploaded.asset_id || '').trim(),
      name: String(uploaded.filename || uploaded.name || (file && file.name) || '').trim(),
      media_type: mediaType || 'image',
      file_size: Number((file && file.size) || meta.file_size || 0) || 0,
      duration_seconds: Number(meta.duration_seconds || 0) || 0,
      width: Number(meta.width || 0) || 0,
      height: Number(meta.height || 0) || 0,
      preview_url: normalizeAssetUrl(uploaded.preview_url || uploaded.open_url || uploaded.source_url || uploaded.url || ''),
      open_url: normalizeAssetUrl(uploaded.open_url || uploaded.source_url || uploaded.url || ''),
      source_url: normalizeAssetUrl(uploaded.source_url || uploaded.open_url || uploaded.url || ''),
      selected_key: String(uploaded.asset_id || '').trim()
    };
  }

  function mergeSelectedMaterials(existing, additions) {
    var map = {};
    var merged = [];
    (existing || []).concat(additions || []).forEach(function(item) {
      var normalized = normalizeMaterialAsset(item) || item;
      var assetId = String(normalized && normalized.asset_id || '').trim();
      if (!assetId) return;
      if (map[assetId]) {
        map[assetId] = Object.assign({}, map[assetId], normalized);
        for (var i = 0; i < merged.length; i += 1) {
          if (String(merged[i] && merged[i].asset_id || '').trim() === assetId) {
            merged[i] = map[assetId];
            break;
          }
        }
        return;
      }
      map[assetId] = normalized;
      merged.push(normalized);
    });
    return merged;
  }

  function syncMaterialPickerSelection() {
    state.materialPicker.selected = {};
    (state.selectedMaterials || []).forEach(function(item) {
      var assetId = String(item && item.asset_id || '').trim();
      if (assetId) state.materialPicker.selected[assetId] = true;
    });
  }

  function selectedMaterialIdsMap() {
    var map = {};
    (state.selectedMaterials || []).forEach(function(item) {
      var assetId = String(item && item.asset_id || '').trim();
      if (assetId) map[assetId] = true;
    });
    return map;
  }

  function materialRows() {
    return (state.selectedMaterials || []).map(function(item) {
      var type = String(item && item.media_type || '').trim().toLowerCase();
      if (type !== 'image' && type !== 'video') return null;
      var assetId = String(item && item.asset_id || '').trim();
      if (!assetId) return null;
      return {
        asset_id: assetId,
        type: type,
        fileUrl: String((item && (item.open_url || item.preview_url || item.source_url)) || '').trim(),
        name: String(item && item.name || assetId).trim()
      };
    }).filter(Boolean);
  }

  function renderSelectedMaterials() {
    var el = $('shanjianSelectedMaterialsPreview');
    var count = $('shanjianSelectedMaterialsCount');
    if (!el) return;
    var items = state.selectedMaterials || [];
    if (count) count.textContent = String(items.length);
    if (!items.length) {
      el.className = 'shanjian-material-list is-empty';
      el.innerHTML = '<div class="shanjian-material-empty">还没有添加混剪素材，点击“从素材库添加”就能把图片或视频加入模板混剪。</div>';
      return;
    }
    el.className = 'shanjian-material-list';
    el.innerHTML = items.map(function(item, index) {
      var thumb = item.preview_url
        ? ((item.media_type === 'video')
          ? '<video src="' + escapeHtml(item.preview_url) + '" muted playsinline preload="metadata"></video>'
          : '<img src="' + escapeHtml(item.preview_url) + '" alt="' + escapeHtml(item.name) + '" loading="lazy" referrerpolicy="no-referrer">')
        : '<div>素材</div>';
      return ''
        + '<div class="shanjian-material-card">'
        + '<div class="shanjian-material-card-thumb">' + thumb + '</div>'
        + '<div class="shanjian-material-card-copy">'
        + '<div class="shanjian-material-card-title" title="' + escapeHtml(item.name) + '">' + escapeHtml(item.name) + '</div>'
        + '<div class="shanjian-material-card-meta">' + escapeHtml(item.media_type === 'video' ? '视频素材' : '图片素材') + ' · ' + escapeHtml(item.asset_id) + '</div>'
        + '</div>'
        + '<button type="button" class="shanjian-material-card-remove" data-material-index="' + index + '" aria-label="移除素材">×</button>'
        + '</div>';
    }).join('');
    Array.prototype.forEach.call(el.querySelectorAll('[data-material-index]'), function(btn) {
      btn.addEventListener('click', function(ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        var idx = Number(btn.getAttribute('data-material-index'));
        if (!Number.isFinite(idx)) return;
        state.selectedMaterials.splice(idx, 1);
        syncMaterialPickerSelection();
        renderSelectedMaterials();
        renderMaterialLibrary();
        if ($('shanjianMaterialPickerModal')) renderMaterialPicker();
      });
    });
  }

  function uploadInlineMaterials(files, triggerBtn) {
    var input = $('shanjianInlineMaterialUploadInput');
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) {
      if (input) input.value = '';
      return Promise.resolve([]);
    }
    var button = triggerBtn || $('shanjianUploadMaterialBtnInline');
    var originalText = button ? button.textContent : '';
    if (button) {
      button.disabled = true;
      button.textContent = '上传中...';
    }
    var usedSeconds = currentMaterialBudgetSeconds();
    var validated = [];
    showMessage('正在上传混剪素材...', false);
    return list.reduce(function(chain, file) {
      return chain.then(function() {
        return validateInlineMaterialFile(file, usedSeconds).then(function(meta) {
          usedSeconds += Number(meta.consumed_seconds || 0);
          validated.push({ file: file, meta: meta });
        });
      });
    }, Promise.resolve()).then(function() {
      return Promise.all(validated.map(function(entry) {
        return uploadAssetFile(entry.file).then(function(data) {
          return materialAssetFromUpload(data, entry.file, entry.meta);
        });
      }));
    }).then(function(uploadedAssets) {
      return loadMaterialPickerItems(true).catch(function() {
        return state.materialPicker.items || [];
      }).then(function() {
        state.materialPicker.items = mergeSelectedMaterials(state.materialPicker.items || [], uploadedAssets);
        state.selectedMaterials = mergeSelectedMaterials(state.selectedMaterials || [], uploadedAssets);
        syncMaterialPickerSelection();
        renderSelectedMaterials();
        renderMaterialLibrary();
        if ($('shanjianMaterialPickerModal')) renderMaterialPicker();
        showMessage('已上传并加入 ' + uploadedAssets.length + ' 个混剪素材。', false);
        return uploadedAssets;
      });
    }).catch(function(err) {
      showMessage(err && err.message ? err.message : '素材上传失败', true);
      throw err;
    }).finally(function() {
      if (button) {
        button.disabled = false;
        button.textContent = originalText || '上传素材';
      }
      if (input) input.value = '';
    });
  }

  function ensureMaterialPickerModal() {
    var modal = document.getElementById('shanjianMaterialPickerModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'shanjianMaterialPickerModal';
    modal.className = 'shanjian-modal shanjian-material-picker-modal';
    modal.style.display = 'none';
    modal.innerHTML = ''
      + '<div class="shanjian-modal-backdrop" data-modal-close="shanjianMaterialPickerModal"></div>'
      + '<div class="shanjian-modal-card" role="dialog" aria-modal="true" aria-labelledby="shanjianMaterialPickerTitle" style="position:relative;width:min(94vw,980px);max-height:92vh;overflow:hidden;display:flex;flex-direction:column;">'
      +   '<div class="shanjian-modal-head">'
      +     '<h4 class="shanjian-modal-title" id="shanjianMaterialPickerTitle">选择混剪素材</h4>'
      +     '<button type="button" class="shanjian-modal-close" data-modal-close="shanjianMaterialPickerModal" aria-label="关闭">×</button>'
      +   '</div>'
      +   '<div style="padding:0.95rem 1rem 0 1rem;display:flex;gap:0.65rem;align-items:center;flex-wrap:wrap;">'
      +     '<input id="shanjianMaterialPickerSearch" class="shanjian-search" type="search" placeholder="搜索素材名称、ID 或类型" style="flex:1 1 280px;min-width:0;">'
      +     '<button type="button" id="shanjianMaterialPickerReload" class="btn btn-ghost btn-sm">刷新素材库</button>'
      +   '</div>'
      +   '<div id="shanjianMaterialPickerStatus" style="padding:0.6rem 1rem 0;color:#6d7791;font-size:0.8rem;">正在加载素材库...</div>'
      +   '<div id="shanjianMaterialPickerGrid" style="padding:0.75rem 1rem 1rem;overflow:auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:0.65rem;min-height:260px;max-height:58vh;"></div>'
      +   '<div class="shanjian-modal-foot">'
      +     '<span id="shanjianMaterialPickerCount" style="color:#6d7791;font-size:0.82rem;">已选择 0 个</span>'
      +     '<div class="shanjian-modal-actions">'
      +       '<button type="button" class="btn btn-ghost" data-modal-close="shanjianMaterialPickerModal">取消</button>'
      +       '<button type="button" id="shanjianMaterialPickerConfirm" class="btn btn-primary">确认加入混剪</button>'
      +     '</div>'
      +   '</div>'
      + '</div>';
    document.body.appendChild(modal);

    modal.addEventListener('click', function(event) {
      if (event.target === modal) {
        closeModal('shanjianMaterialPickerModal');
        return;
      }
      var closer = event.target && event.target.closest ? event.target.closest('[data-modal-close="shanjianMaterialPickerModal"]') : null;
      if (closer) {
        closeModal('shanjianMaterialPickerModal');
        return;
      }
      var card = event.target && event.target.closest ? event.target.closest('[data-material-asset-id]') : null;
      if (card) {
        var assetId = String(card.getAttribute('data-material-asset-id') || '').trim();
        if (assetId) toggleMaterialPickerSelection(assetId);
      }
    });
    var search = modal.querySelector('#shanjianMaterialPickerSearch');
    if (search) {
      search.addEventListener('input', function(ev) {
        state.materialPicker.query = String(ev.target.value || '');
        renderMaterialPicker();
      });
    }
    var reload = modal.querySelector('#shanjianMaterialPickerReload');
    if (reload) reload.addEventListener('click', function() { loadMaterialPickerItems(true); });
    var confirm = modal.querySelector('#shanjianMaterialPickerConfirm');
    if (confirm) confirm.addEventListener('click', confirmMaterialPicker);
    return modal;
  }

  function filteredMaterialPickerItems() {
    var query = String(state.materialPicker.query || '').trim().toLowerCase();
    var items = (state.materialPicker.items || []).filter(function(item) {
      if (state.materialFilter === 'image') return item && item.media_type === 'image';
      if (state.materialFilter === 'video') return item && item.media_type === 'video';
      return true;
    });
    if (!query) return items;
    return items.filter(function(item) {
      var haystack = [item.name || '', item.asset_id || '', item.media_type || ''].join(' ').toLowerCase();
      return haystack.indexOf(query) >= 0;
    });
  }

  function renderMaterialLibrary() {
    var grid = $('shanjianMaterialGrid');
    var empty = $('shanjianMaterialEmpty');
    var count = $('shanjianMaterialCount');
    if (!grid || !empty || !count) return;
    if ($('shanjianMaterialFilterAllBtn')) $('shanjianMaterialFilterAllBtn').classList.toggle('is-active', state.materialFilter === 'all');
    if ($('shanjianMaterialFilterImageBtn')) $('shanjianMaterialFilterImageBtn').classList.toggle('is-active', state.materialFilter === 'image');
    if ($('shanjianMaterialFilterVideoBtn')) $('shanjianMaterialFilterVideoBtn').classList.toggle('is-active', state.materialFilter === 'video');
    var items = filteredMaterialPickerItems();
    count.textContent = String(items.length);
    if (!items.length) {
      grid.innerHTML = '';
      empty.style.display = 'block';
      empty.textContent = state.materialPicker.loading
        ? '正在加载素材库...'
        : '当前分类下还没有可用素材。';
      return;
    }
    empty.style.display = 'none';
    var selectedMap = selectedMaterialIdsMap();
    grid.innerHTML = items.map(function(item) {
      var active = selectedMap[item.asset_id] ? ' is-selected' : '';
      var thumb = item.media_type === 'video'
        ? (item.preview_url
          ? '<video src="' + escapeHtml(item.preview_url) + '" muted playsinline preload="metadata"></video>'
          : '<div>视频</div>')
        : (item.preview_url
          ? '<img src="' + escapeHtml(item.preview_url) + '" alt="' + escapeHtml(item.name) + '" loading="lazy" referrerpolicy="no-referrer">'
          : '<div>图片</div>');
      return ''
        + '<button type="button" class="shanjian-material-picker-item' + active + '" data-material-library-id="' + escapeHtml(item.asset_id) + '">'
        + '<span class="shanjian-material-picker-thumb">' + thumb + '</span>'
        + '<span style="min-width:0;display:flex;flex-direction:column;gap:0.16rem;">'
        + '<span class="shanjian-material-picker-name" title="' + escapeHtml(item.name) + '">' + escapeHtml(item.name) + '</span>'
        + '<span class="shanjian-material-picker-meta">' + escapeHtml(item.media_type === 'video' ? '视频素材' : '图片素材') + '</span>'
        + '</span>'
        + '<span class="shanjian-material-picker-check">✓</span>'
        + '</button>';
    }).join('');
    Array.prototype.forEach.call(grid.querySelectorAll('[data-material-library-id]'), function(btn) {
      btn.addEventListener('click', function() {
        var assetId = String(btn.getAttribute('data-material-library-id') || '').trim();
        if (!assetId) return;
        if (selectedMap[assetId]) {
          state.selectedMaterials = (state.selectedMaterials || []).filter(function(item) {
            return String(item && item.asset_id || '').trim() !== assetId;
          });
        } else {
          var row = (state.materialPicker.items || []).find(function(item) { return item.asset_id === assetId; });
          if (row) {
            state.selectedMaterials = (state.selectedMaterials || []).concat([{
              asset_id: row.asset_id,
              name: row.name,
              media_type: row.media_type,
              file_size: row.file_size,
              duration_seconds: row.duration_seconds,
              width: row.width,
              height: row.height,
              preview_url: row.preview_url,
              open_url: row.open_url,
              source_url: row.source_url
            }]);
          }
        }
        syncMaterialPickerSelection();
        renderSelectedMaterials();
        renderMaterialLibrary();
        if ($('shanjianMaterialPickerModal')) renderMaterialPicker();
      });
    });
  }

  function renderMaterialPicker() {
    var modal = ensureMaterialPickerModal();
    var grid = modal.querySelector('#shanjianMaterialPickerGrid');
    var status = modal.querySelector('#shanjianMaterialPickerStatus');
    var count = modal.querySelector('#shanjianMaterialPickerCount');
    var confirm = modal.querySelector('#shanjianMaterialPickerConfirm');
    if (!grid || !status) return;
    var selectedMap = state.materialPicker.selected || {};
    var selectedCount = Object.keys(selectedMap).length;
    if (count) count.textContent = '已选择 ' + selectedCount + ' 个';
    if (confirm) confirm.disabled = selectedCount < 1;

    if (state.materialPicker.loading) {
      status.textContent = '正在加载素材库...';
      grid.innerHTML = '<div class="shanjian-material-empty" style="grid-column:1 / -1;">正在加载素材库...</div>';
      return;
    }
    var items = filteredMaterialPickerItems();
    if (!state.materialPicker.items.length) {
      status.textContent = '素材库暂无可选素材';
      grid.innerHTML = '<div class="shanjian-material-empty" style="grid-column:1 / -1;">素材库当前没有图片或视频素材，请先上传素材后再来选择。</div>';
      return;
    }
    if (!items.length) {
      status.textContent = '没有匹配的素材';
      grid.innerHTML = '<div class="shanjian-material-empty" style="grid-column:1 / -1;">没有搜到匹配的素材，换个关键词试试。</div>';
      return;
    }
    status.textContent = '共 ' + state.materialPicker.items.length + ' 个素材，当前显示 ' + items.length + ' 个';
    grid.innerHTML = items.map(function(item) {
      var active = selectedMap[item.asset_id] ? ' is-selected' : '';
      var thumb = item.media_type === 'video'
        ? (item.preview_url
          ? '<video src="' + escapeHtml(item.preview_url) + '" muted playsinline preload="metadata"></video>'
          : '<div>视频</div>')
        : (item.preview_url
          ? '<img src="' + escapeHtml(item.preview_url) + '" alt="' + escapeHtml(item.name) + '" loading="lazy" referrerpolicy="no-referrer">'
          : '<div>图片</div>');
      return ''
        + '<button type="button" class="shanjian-material-picker-item' + active + '" data-material-asset-id="' + escapeHtml(item.asset_id) + '">'
        +   '<span class="shanjian-material-picker-thumb">' + thumb + '</span>'
        +   '<span class="shanjian-material-picker-name" title="' + escapeHtml(item.name) + '">' + escapeHtml(item.name) + '</span>'
        +   '<span class="shanjian-material-picker-meta">' + escapeHtml(item.media_type === 'video' ? '视频素材' : '图片素材') + '</span>'
        +   '<span class="shanjian-material-picker-check">✓</span>'
        + '</button>';
    }).join('');
  }

  function toggleMaterialPickerSelection(assetId) {
    assetId = String(assetId || '').trim();
    if (!assetId) return;
    if (state.materialPicker.selected[assetId]) delete state.materialPicker.selected[assetId];
    else state.materialPicker.selected[assetId] = true;
    renderMaterialPicker();
  }

  function loadMaterialPickerItems(force) {
    var modal = ensureMaterialPickerModal();
    if (!force && state.materialPicker.items.length) {
      renderMaterialPicker();
      return Promise.resolve(state.materialPicker.items);
    }
    state.materialPicker.loading = true;
    renderMaterialPicker();
    return fetch((cloudBaseUrl() || baseUrl()) + '/api/assets?limit=120', {
      headers: authHeadersSafe()
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) {
          var msg = data.detail || data.error || data.message || '素材库加载失败';
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        var rows = Array.isArray(data.assets) ? data.assets : (Array.isArray(data.items) ? data.items : []);
        state.materialPicker.items = rows.map(normalizeMaterialAsset).filter(Boolean);
        state.materialPicker.loading = false;
        renderMaterialPicker();
        return state.materialPicker.items;
      });
    }).catch(function(err) {
      state.materialPicker.loading = false;
      var status = modal.querySelector('#shanjianMaterialPickerStatus');
      if (status) status.textContent = err && err.message ? err.message : '素材库加载失败';
      renderMaterialPicker();
      throw err;
    });
  }

  function openMaterialPicker() {
    syncMaterialPickerSelection();
    state.materialPicker.query = '';
    var modal = ensureMaterialPickerModal();
    var search = modal.querySelector('#shanjianMaterialPickerSearch');
    if (search) search.value = '';
    openModal('shanjianMaterialPickerModal');
    loadMaterialPickerItems(true);
  }

  function confirmMaterialPicker() {
    var picked = (state.materialPicker.items || []).filter(function(item) {
      return !!state.materialPicker.selected[item.asset_id];
    }).map(function(item) {
      return {
        asset_id: item.asset_id,
        name: item.name,
        media_type: item.media_type,
        file_size: item.file_size,
        duration_seconds: item.duration_seconds,
        width: item.width,
        height: item.height,
        preview_url: item.preview_url,
        open_url: item.open_url,
        source_url: item.source_url
      };
    });
    state.selectedMaterials = picked;
    syncMaterialPickerSelection();
    renderSelectedMaterials();
    renderMaterialLibrary();
    closeModal('shanjianMaterialPickerModal');
    showMessage(picked.length ? ('已选择 ' + picked.length + ' 个素材，提交模板混剪时会一起带上。') : '已清空素材选择。', false);
  }

  function selectTemplate(item, shouldScroll) {
    state.selectedTemplate = item ? normalizeTemplateItem(item) : null;
    renderSelectedTemplate();
    renderTemplateLibrary();
    syncTemplateModeUI();
    if (shouldScroll && window.innerWidth < 1100) {
      var preview = $('shanjianSelectedTemplatePreview');
      if (preview && typeof preview.scrollIntoView === 'function') {
        preview.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  }

  function renderSelectedTemplate() {
    var el = $('shanjianSelectedTemplatePreview');
    if (!el) return;
    var item = state.selectedTemplate;
    if (!item) {
      el.className = 'shanjian-selected-template is-empty';
      el.innerHTML = ''
        + '<div class="shanjian-selected-empty">'
        + '<strong>当前不使用模板</strong>'
        + '<span>不选模板时，提交任务会走普通数字人口播；选了模板后，才会切到真人视频模板剪辑。</span>'
        + '</div>';
      return;
    }
    el.className = 'shanjian-selected-template';
    el.innerHTML = ''
      + '<div class="shanjian-selected-template-cover">'
      + (item.cover_url
        ? '<img src="' + escapeHtml(item.cover_url) + '" alt="' + escapeHtml(item.name) + '" loading="lazy" referrerpolicy="no-referrer">'
        : '<div class="shanjian-template-cover-placeholder">' + escapeHtml(item.name.slice(0, 4) || '模板') + '</div>')
      + '<span class="shanjian-section-chip">真人视频模板</span>'
      + '</div>'
      + '<div class="shanjian-selected-copy">'
      + '<div class="shanjian-selected-title-row"><strong>' + escapeHtml(item.name) + '</strong></div>'
      + '<div class="shanjian-selected-tags"><span class="shanjian-mini-tag">已启用真人视频模板</span></div>'
      + '</div>';
  }

  function renderTemplateCard(item) {
    var selected = state.selectedTemplate && state.selectedTemplate.id === item.id;
    return ''
      + '<article class="shanjian-template-card' + (selected ? ' is-selected' : '') + '" data-template-id="' + escapeHtml(item.id) + '" data-template-demo-url="' + escapeHtml(item.demo_url || '') + '" style="cursor:pointer;">'
      + '<div class="shanjian-template-card-cover">'
      + (item.cover_url
        ? '<img src="' + escapeHtml(item.cover_url) + '" alt="' + escapeHtml(item.name) + '" loading="lazy" referrerpolicy="no-referrer">'
        : '<div class="shanjian-template-cover-placeholder">' + escapeHtml(item.name.slice(0, 4) || '模板') + '</div>')
      + '</div>'
      + '<div class="shanjian-template-card-body">'
      + '<div class="shanjian-template-card-title" title="' + escapeHtml(item.name) + '">' + escapeHtml(item.name) + '</div>'
      + '<div class="shanjian-template-card-actions">'
      + '<button type="button" class="btn ' + (selected ? 'btn-ghost' : 'btn-primary') + ' btn-sm shanjian-template-pick-btn" data-template-id="' + escapeHtml(item.id) + '">' + (selected ? '已启用' : '使用模板') + '</button>'
      + '</div>'
      + '</div>'
      + '</article>';
  }

  function renderTemplateLibrary() {
    var grid = $('shanjianTemplateGrid');
    var empty = $('shanjianTemplateEmpty');
    var count = $('shanjianTemplateCount');
    var moreBtn = $('shanjianTemplateMoreBtn');
    if (!grid || !empty || !count || !moreBtn) return;
    var query = String(state.templateSearch || '').trim().toLowerCase();
    var rows = (state.templateLibrary.rows || []).filter(function(item) {
      if (!query) return true;
      return String(item.name || '').toLowerCase().indexOf(query) >= 0
        || String(item.id || '').toLowerCase().indexOf(query) >= 0;
    });
    count.textContent = String(rows.length);
    if (!rows.length) {
      grid.innerHTML = '';
      empty.style.display = 'block';
      empty.textContent = query ? '没有匹配到模板。' : '暂时没有拿到真人视频模板。';
      moreBtn.style.display = 'none';
      return;
    }
    empty.style.display = 'none';
    grid.innerHTML = rows.map(renderTemplateCard).join('');
    moreBtn.style.display = state.templateLibrary.sid && !query ? 'inline-flex' : 'none';
    moreBtn.disabled = !!state.templateLibrary.loading;
    moreBtn.textContent = state.templateLibrary.loading ? '正在加载...' : '加载更多模板';
    bindTemplateCardEvents();
  }

  function bindTemplateCardEvents() {
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-template-pick-btn'), function(btn) {
      btn.onclick = function(ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        var id = btn.getAttribute('data-template-id') || '';
        if (!id) return;
        var item = (state.templateLibrary.rows || []).find(function(row) { return row.id === id; });
        if (item) selectTemplate(item, true);
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-template-card[data-template-id]'), function(card) {
      card.onclick = function(ev) {
        if (ev && ev.target && ev.target.closest && ev.target.closest('.shanjian-template-pick-btn')) return;
        var id = card.getAttribute('data-template-id') || '';
        if (!id) return;
        var item = (state.templateLibrary.rows || []).find(function(row) { return row.id === id; });
        if (!item) return;
        selectTemplate(item, false);
        if (item.demo_url) {
          openTemplatePreview(item);
          return;
        }
        selectTemplate(item, true);
      };
    });
  }

  function renderAvatarCard(item) {
    var selected = state.selectedAvatar && state.selectedAvatar.avatar === item.avatar;
    var canDelete = !!(item && item.is_mine === true && item.id != null);
    var tags = (item.tags || []).slice(0, 2).map(function(tag) {
      return '<span class="shanjian-card-tag">' + escapeHtml(tag) + '</span>';
    }).join('');
    var countText = item.material_count ? (item.material_count + ' 个素材') : '已就绪';
    var key = avatarKey(item);
    var deleteBtn = canDelete
      ? '<button type="button" class="btn btn-ghost btn-sm shanjian-avatar-delete-btn" data-avatar-asset-id="' + escapeHtml(item.id) + '">删除数字人</button>'
      : '';
    return ''
      + '<article class="shanjian-avatar-card' + (selected ? ' is-selected' : '') + '" data-avatar-key="' + escapeHtml(key) + '" style="cursor:pointer;">'
      + '<div class="shanjian-avatar-card-cover">'
      + coverImage(item)
      + '<span class="shanjian-avatar-card-badge">' + escapeHtml(item.section_label || '数字人') + '</span>'
      + '<span class="shanjian-avatar-card-count">' + escapeHtml(countText) + '</span>'
      + '</div>'
      + '<div class="shanjian-avatar-card-body">'
      + '<div class="shanjian-avatar-card-main">'
      + '<div class="shanjian-avatar-card-title" title="' + escapeHtml(item.title) + '">' + escapeHtml(item.title) + '</div>'
      + '<div class="shanjian-avatar-card-tags">' + tags + '</div>'
      + '</div>'
      + '<div class="shanjian-avatar-card-meta"><span>' + escapeHtml(countText) + '</span></div>'
      + '<div class="shanjian-avatar-card-actions">'
      + '<button type="button" class="btn btn-ghost btn-sm shanjian-avatar-detail-btn shanjian-avatar-pick-btn" data-avatar-key="' + escapeHtml(key) + '">查看详情</button>'
      + '<button type="button" class="btn ' + (selected ? 'btn-ghost' : 'btn-primary') + ' btn-sm shanjian-avatar-pick-btn" data-avatar-id="' + escapeHtml(item.avatar) + '">' + (selected ? '已选择' : '选择数字人') + '</button>'
      + deleteBtn
      + '</div>'
      + '</div>'
      + '</article>';
  }

  function renderVoiceCard(item) {
    var styles = voiceStyles(item);
    var activeStyle = getSelectedStyleForVoiceItem(item);
    var selected = !!(state.selectedVoice && styles.some(function(style) { return style.voice === state.selectedVoice.voice; }));
    var tags = (item.tags || []).slice(0, 2).map(function(tag) {
      return '<span class="shanjian-card-tag">' + escapeHtml(tag) + '</span>';
    }).join('');
    var styleList = styles.map(function(style) {
      var preview = previewUrlForVoiceId(style.voice, style.demo_url);
      var isActive = !!(state.selectedVoice && state.selectedVoice.voice === style.voice);
      return ''
        + '<button type="button" class="shanjian-voice-style-btn' + (isActive ? ' is-active' : '') + '" data-voice-style-id="' + escapeHtml(style.voice) + '">'
        + '<span class="shanjian-voice-style-copy"><span class="shanjian-voice-style-play">' + (preview ? '>' : 'o') + '</span><span class="shanjian-voice-style-text">' + escapeHtml(style.label || style.title || '默认风格') + '</span></span>'
        + '<span class="shanjian-voice-style-state">' + escapeHtml(preview ? '可试听' : '选择') + '</span>'
        + '</button>';
    }).join('');
    var audio = voicePreviewButtonHtml(activeStyle && activeStyle.demo_url ? activeStyle.demo_url : '', voiceParams(item), { provider: voiceProvider(item, activeStyle), voice: activeStyle && activeStyle.voice ? activeStyle.voice : item.voice });
    var canEdit = !!(item && item.is_mine === true && activeStyle && activeStyle.voice);
    var canDelete = !!(item && item.is_mine === true && item.id != null);
    var params = voiceParams(item);
    var editPanel = canEdit ? ''
      + '<div class="shanjian-voice-param-panel" data-voice-id="' + escapeHtml(activeStyle.voice) + '" data-voice-asset-id="' + escapeHtml(item.id || '') + '">'
      + '<div class="shanjian-voice-param-head">'
      + '<div class="shanjian-voice-param-title"><strong>声音参数</strong><span>' + escapeHtml(voiceParamSummary(params, true)) + '</span></div>'
      + '<span class="shanjian-voice-param-status" data-tone="idle"></span>'
      + '</div>'
      + '<div class="shanjian-voice-param-grid">'
      + '<label><span>语速</span><input class="shanjian-voice-param-input" data-param="rate" data-min="0.5" data-max="2" data-step="0.1" type="text" readonly value="' + escapeHtml(params.rate) + '"></label>'
      + '<label><span>音量</span><input class="shanjian-voice-param-input" data-param="volume" data-min="0.1" data-max="2" data-step="0.1" type="text" readonly value="' + escapeHtml(params.volume) + '"></label>'
      + '<label><span>语调</span><input class="shanjian-voice-param-input" data-param="pitch" data-min="-12" data-max="12" data-step="1" type="text" readonly value="' + escapeHtml(params.pitch) + '"></label>'
      + '</div>'
      + '</div>' : '';
    return ''
      + '<article class="shanjian-voice-card' + (selected ? ' is-selected' : '') + '">'
      + '<div class="shanjian-voice-card-top">'
      + '<div class="shanjian-voice-card-thumb">' + coverImage(item) + '</div>'
      + '<div class="shanjian-voice-card-main">'
      + '<div class="shanjian-voice-card-head">'
      + '<div><div class="shanjian-voice-card-title">' + escapeHtml(item.title) + '</div></div>'
      + '<span class="shanjian-section-chip is-inline">' + escapeHtml(item.section_label || '声音') + '</span>'
      + '</div>'
      + '<div class="shanjian-avatar-card-tags">' + tags + '</div>'
      + '<div class="shanjian-voice-style-list">' + styleList + '</div>'
      + '</div>'
      + '</div>'
      + '<div class="shanjian-voice-audio">' + audio + '</div>'
      + editPanel
      + '<div class="shanjian-voice-card-actions">'
      + '<button type="button" class="btn ' + (selected ? 'btn-ghost' : 'btn-primary') + ' btn-sm shanjian-voice-pick-btn" data-voice-id="' + escapeHtml(activeStyle && activeStyle.voice ? activeStyle.voice : item.voice) + '">' + (selected ? '已选择当前风格' : '选择当前风格') + '</button>'
      + (canDelete ? '<button type="button" class="btn btn-ghost btn-sm shanjian-voice-delete-btn" data-voice-asset-id="' + escapeHtml(item.id) + '">删除声音</button>' : '')
      + '</div>'
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
      gridId: 'shanjianMineAvatarGrid',
      emptyId: 'shanjianMineAvatarEmpty',
      countId: 'shanjianMineCount',
      moreBtnId: 'shanjianMineMoreBtn',
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
      gridId: 'shanjianPublicAvatarGrid',
      emptyId: 'shanjianPublicAvatarEmpty',
      countId: 'shanjianPublicCount',
      moreBtnId: 'shanjianPublicMoreBtn',
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
      gridId: 'shanjianMineVoiceGrid',
      emptyId: 'shanjianMineVoiceEmpty',
      countId: 'shanjianMineVoiceCount',
      moreBtnId: 'shanjianMineVoiceMoreBtn',
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
      gridId: 'shanjianPublicVoiceGrid',
      emptyId: 'shanjianPublicVoiceEmpty',
      countId: 'shanjianPublicVoiceCount',
      moreBtnId: 'shanjianPublicVoiceMoreBtn',
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
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-avatar-pick-btn'), function(btn) {
      btn.onclick = function(ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        var avatarId = btn.getAttribute('data-avatar-id') || '';
        var all = (state.avatarLibrary.mine || []).concat(state.avatarLibrary.public || []);
        var picked = all.find(function(item) { return item.avatar === avatarId; });
        selectAvatar(picked, true);
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-avatar-detail-btn'), function(btn) {
      btn.onclick = function(ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        var picked = findAvatarByKey(btn.getAttribute('data-avatar-key') || '');
        if (picked) openAvatarDetail(picked);
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-avatar-delete-btn'), function(btn) {
      btn.onclick = function(ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        var id = btn.getAttribute('data-avatar-asset-id') || '';
        if (!id) return showMessage('无法删除该数字人记录，请刷新后重试。', true);
        showConfirmDialog({
          title: '删除数字人',
          message: '确认删除这个数字人？删除后不会再显示在“我的数字人”列表。',
          confirmText: '删除',
          tone: 'danger'
        }).then(function(confirmed) {
          if (!confirmed) return;
          var oldText = btn.textContent;
          btn.disabled = true;
          btn.textContent = '删除中...';
          requestDelete('/api/shanjian-digital-human/profiles/' + encodeURIComponent(id))
            .then(function() {
              state.avatarLibrary.mine = (state.avatarLibrary.mine || []).filter(function(item) {
                return String(item && item.id) !== String(id);
              });
              if (state.selectedAvatar && String(state.selectedAvatar.id) === String(id)) {
                var all = (state.avatarLibrary.mine || []).concat(state.avatarLibrary.public || []);
                state.selectedAvatar = all.length ? all[0] : null;
                renderSelectedAvatar();
              }
              renderAvatarLibrary();
              btn.disabled = false;
              btn.textContent = oldText;
              showMessage('数字人已删除。', false);
            })
            .catch(function(err) {
              btn.disabled = false;
              btn.textContent = oldText;
              showMessage(err && err.message ? err.message : '删除数字人失败', true);
            });
        });
      };
    });
    /** 点卡片任意位置（除按钮）也打开详情弹窗 */
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-avatar-card[data-avatar-key]'), function(card) {
      card.onclick = function(ev) {
        if (ev && ev.target && ev.target.closest && ev.target.closest('button')) return;
        var picked = findAvatarByKey(card.getAttribute('data-avatar-key') || '');
        if (picked) openAvatarDetail(picked);
      };
    });
  }

  function ensureVoiceParamSlider() {
    var panel = $('shanjianVoiceParamSlider');
    if (panel) return panel;
    panel = document.createElement('div');
    panel.id = 'shanjianVoiceParamSlider';
    panel.className = 'shanjian-voice-param-popover';
    panel.style.display = 'none';
    panel.innerHTML = ''
      + '<div class="shanjian-param-popover-head"><strong id="shanjianParamSliderTitle">参数</strong><span id="shanjianParamSliderValue">1.0</span></div>'
      + '<input id="shanjianParamSliderRange" class="shanjian-param-slider" type="range">'
      + '<div class="shanjian-param-slider-scale"><span id="shanjianParamSliderMin">0.5</span><span id="shanjianParamSliderMax">2.0</span></div>';
    document.body.appendChild(panel);
    panel.onmousedown = function(ev) {
      if (ev && ev.stopPropagation) ev.stopPropagation();
    };
    return panel;
  }

  function closeVoiceParamSlider() {
    var panel = $('shanjianVoiceParamSlider');
    if (panel) {
      panel.style.display = 'none';
      panel._targetInput = null;
    }
  }

  function openVoiceParamSlider(input) {
    var panel = ensureVoiceParamSlider();
    var min = Number(input.getAttribute('data-min') || input.min || 0);
    var max = Number(input.getAttribute('data-max') || input.max || 2);
    var step = Number(input.getAttribute('data-step') || input.step || 0.1);
    var value = numericParam(input.value, 1, min, max);
    var range = $('shanjianParamSliderRange');
    var title = $('shanjianParamSliderTitle');
    var valueEl = $('shanjianParamSliderValue');
    var minEl = $('shanjianParamSliderMin');
    var maxEl = $('shanjianParamSliderMax');
    var label = input.parentElement ? (input.parentElement.querySelector('span') || {}).textContent || '参数' : '参数';
    panel._targetInput = input;
    if (title) title.textContent = label;
    if (valueEl) valueEl.textContent = formatParamValue(value);
    if (minEl) minEl.textContent = formatParamValue(min);
    if (maxEl) maxEl.textContent = formatParamValue(max);
    if (range) {
      range.min = String(min);
      range.max = String(max);
      range.step = String(step);
      range.value = String(value);
      range.oninput = function() {
        var next = numericParam(range.value, value, min, max);
        input.value = formatParamValue(next);
        if (valueEl) valueEl.textContent = input.value;
        scheduleVoiceParamPanelSave(input);
      };
    }
    var rect = input.getBoundingClientRect();
    panel.style.display = 'block';
    var panelWidth = 236;
    var left = Math.min(window.innerWidth - panelWidth - 12, Math.max(12, rect.left + rect.width / 2 - panelWidth / 2));
    var top = rect.bottom + 8;
    if (top + 126 > window.innerHeight) top = Math.max(12, rect.top - 132);
    panel.style.left = left + 'px';
    panel.style.top = top + 'px';
  }

  function bindVoiceCardEvents() {
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-voice-param-input'), function(input) {
      input.onclick = function(ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        openVoiceParamSlider(input);
      };
      input.onfocus = function() {
        openVoiceParamSlider(input);
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-voice-delete-btn'), function(btn) {
      btn.onclick = function(ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        var id = btn.getAttribute('data-voice-asset-id') || '';
        if (!id) return showMessage('无法删除该声音记录，请刷新后重试。', true);
        showConfirmDialog({
          title: '删除声音',
          message: '确认删除这个声音？删除后不会再显示在“我的声音”列表。',
          confirmText: '删除',
          tone: 'danger'
        }).then(function(confirmed) {
          if (!confirmed) return;
          var oldText = btn.textContent;
          btn.disabled = true;
          btn.textContent = '删除中...';
          requestCloudDelete('/api/hifly/my/voice/' + encodeURIComponent(id))
            .then(function() {
              var deleted = null;
              state.voiceLibrary.mine = (state.voiceLibrary.mine || []).filter(function(item) {
                var hit = String(item.id) === String(id);
                if (hit) deleted = item;
                return !hit;
              });
              if (deleted && state.selectedVoice) {
                var deletedStyles = voiceStyles(deleted);
                var selectedDeleted = deletedStyles.some(function(style) {
                  return style.voice === state.selectedVoice.voice;
                });
                if (selectedDeleted) {
                  var all = (state.voiceLibrary.mine || []).concat(state.voiceLibrary.public || []);
                  state.selectedVoice = all.length ? buildSelectedVoice(all[0], getSelectedStyleForVoiceItem(all[0])) : null;
                  if ($('shanjianVoiceInput')) $('shanjianVoiceInput').value = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : '';
                  renderSelectedVoice();
                }
              }
              renderVoiceLibrary();
              showMessage('声音已删除。', false);
              return loadVoices(true);
            })
            .catch(function(err) {
              btn.disabled = false;
              btn.textContent = oldText;
              showMessage(err && err.message ? err.message : '删除声音失败', true);
            });
        });
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-voice-pick-btn'), function(btn) {
      btn.onclick = function() {
        var voiceId = btn.getAttribute('data-voice-id') || '';
        var found = findVoiceGroupByVoiceId(voiceId);
        if (found) selectVoiceVariant(found.group, found.style, true);
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-voice-style-btn'), function(btn) {
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
    return requestGet('/api/shanjian-digital-human/profiles').then(function(data) {
      var rows = (data && data.items ? data.items : []).map(normalizeProfileItem);
      state.avatarLibrary = {
        mine: rows,
        public: [],
        mine_supported: true,
        mine_message: '',
        using_default_token: false,
        public_page: 1,
        public_page_size: 20,
        public_has_more: false,
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
        showMessage('数字人列表已更新。', false);
      }
    });
  }

  function dedupeById(rows) {
    var map = {};
    var list = [];
    (rows || []).forEach(function(item) {
      var id = String(item && item.id ? item.id : '').trim();
      if (!id || map[id]) return;
      map[id] = true;
      list.push(item);
    });
    return list;
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
    return requestCloudGet('/api/hifly/my/voice/list?page=1&size=100').then(function(mineData) {
      var previousVoiceId = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : '';
      state.voiceLibrary = hydrateVoiceLibraryPreview({
        mine: (mineData.items || []).map(function(item) { return Object.assign({}, item, { is_mine: true }); }),
        public: [],
        mine_supported: true,
        mine_message: '',
        using_default_token: false
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
      if ($('shanjianVoiceInput')) $('shanjianVoiceInput').value = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : '';

      renderSelectedVoice();
      renderVoiceLibrary();

      if (!silent) {
        showMessage('声音列表已更新。', false);
      }
    });
  }

  function loadTemplateLibrary(silent, reset) {
    if (!silent) showMessage('正在加载真人视频模板...', false);
    state.templateLibrary.loading = true;
    return request('/api/shanjian-smart-clip/templates', {
      page_size: 30,
      sid: reset ? '' : (state.templateLibrary.sid || ''),
      scene: 'realMan',
      search_key: state.templateSearch ? 'name' : '',
      search_value: state.templateSearch || '',
      sort_by: 'desc'
    }).then(function(data) {
      var incoming = ((data && data.results) || []).map(normalizeTemplateItem);
      state.templateLibrary.sid = (data && data.sid) || '';
      state.templateLibrary.rows = reset
        ? incoming
        : dedupeById((state.templateLibrary.rows || []).concat(incoming));
      if (state.selectedTemplate && state.selectedTemplate.id) {
        var matched = (state.templateLibrary.rows || []).find(function(item) { return item.id === state.selectedTemplate.id; });
        if (matched) state.selectedTemplate = matched;
      }
      renderSelectedTemplate();
      renderTemplateLibrary();
      syncTemplateModeUI();
      if (!silent) {
        showMessage('真人视频模板已更新。', false);
      }
    }).finally(function() {
      state.templateLibrary.loading = false;
      renderTemplateLibrary();
    });
  }

  function loadMoreTemplates() {
    if (!state.templateLibrary.sid || state.templateLibrary.loading) return Promise.resolve();
    return loadTemplateLibrary(true, false);
  }

  function currentTemplateClipOptions() {
    return {
      header_switch: !!(($('shanjianTemplateHeaderSwitch') || {}).checked),
      material_switch: !!(($('shanjianTemplateMaterialSwitch') || {}).checked),
      subtitle_switch: !!(($('shanjianTemplateSubtitleSwitch') || {}).checked),
      keyword_switch: !!(($('shanjianTemplateKeywordSwitch') || {}).checked),
      material_sound_switch: !!(($('shanjianTemplateMaterialSoundSwitch') || {}).checked),
      material_match_way: (($('shanjianTemplateMatchWaySelect') || {}).value || 'fuzzyMatch').trim() || 'fuzzyMatch'
    };
  }

  function selectedTemplateScene() {
    return 'realMan';
  }

  function renderVideoHistoryItem(item) {
    /** 每个历史口播任务：缩略条目 + 视频/状态 + 重播按钮。 */
    var statusKind = item.status_kind || taskStatusKind(item.status);
    var statusTone = statusKind === 'success'
      ? 'success'
      : (statusKind === 'failed' ? 'danger' : 'processing');
    var videoUrl = item.video_url || '';
    var mediaHtml;
    if (videoUrl && statusKind === 'success') {
      mediaHtml = '<video src="' + escapeHtml(videoUrl) + '" controls preload="metadata" '
        + 'style="width:100%;height:140px;object-fit:cover;background:#000;border-radius:12px;"></video>';
    } else {
      mediaHtml = '<div class="shanjian-video-history-placeholder" '
        + 'style="width:100%;height:140px;border-radius:12px;display:flex;align-items:center;justify-content:center;'
        + 'background:linear-gradient(135deg,#e9efff,#dcdff7);color:#5b6475;font-size:0.8rem;">'
        + escapeHtml(item.status_text || '处理中') + '</div>';
    }
    var statusBadge = '<span class="shanjian-result-pill" data-tone="' + statusTone + '">'
      + escapeHtml(item.status_text || '处理中') + '</span>';
    var openBtn = (videoUrl && statusKind === 'success')
      ? '<button type="button" class="btn btn-ghost btn-sm shanjian-video-history-play" data-task-id="'
        + escapeHtml(item.task_id || '') + '">预览</button>'
      : '';
    var downloadBtn = (videoUrl && statusKind === 'success')
      ? '<button type="button" class="btn btn-primary btn-sm" data-shanjian-video-download="'
        + escapeHtml(videoUrl) + '" data-download-filename="' + escapeHtml((item.title || 'digital-human') + '.mp4') + '">下载</button>'
      : '';
    var refreshBtn = (statusKind !== 'success' && statusKind !== 'failed')
      ? '<button type="button" class="btn btn-ghost btn-sm shanjian-video-history-refresh" data-task-id="'
        + escapeHtml(item.task_id || '') + '">刷新</button>'
      : '';
    var deleteBtn = '<button type="button" class="btn btn-ghost btn-sm shanjian-video-history-delete" data-id="'
      + escapeHtml(String(item.id || '')) + '">删除</button>';
    var textSnippet = item.text ? String(item.text).slice(0, 48) + (String(item.text).length > 48 ? '…' : '') : '';
    return ''
      + '<div class="shanjian-video-history-card" '
      + 'style="background:#fff;border-radius:16px;padding:12px;box-shadow:0 6px 18px rgba(36,54,88,0.06);display:flex;flex-direction:column;gap:8px;">'
      + mediaHtml
      + '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">'
      + '<strong style="font-size:0.9rem;color:#243957;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
      + escapeHtml(item.title || '数字人口播') + '</strong>'
      + statusBadge
      + '</div>'
      + (textSnippet ? '<div style="font-size:0.75rem;color:#75839a;line-height:1.45;">' + escapeHtml(textSnippet) + '</div>' : '')
      + '<div style="display:flex;gap:6px;flex-wrap:wrap;">' + openBtn + downloadBtn + refreshBtn + deleteBtn + '</div>'
      + '</div>';
  }

  function renderVideoHistory() {
    var grid = $('shanjianVideoHistoryGrid');
    var empty = $('shanjianVideoHistoryEmpty');
    var count = $('shanjianVideoHistoryCount');
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
    bindVideoResultActions();
  }

  function bindVideoHistoryEvents() {
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-video-history-play'), function(btn) {
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
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-video-history-refresh'), function(btn) {
      btn.onclick = function() {
        var taskId = btn.getAttribute('data-task-id') || '';
        if (!taskId) return;
        btn.disabled = true;
        request('/api/shanjian-digital-human/video/task', { task_id: taskId, token: tokenValue() })
          .catch(function() {})
          .finally(function() { loadVideoHistory(true); });
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.shanjian-video-history-delete'), function(btn) {
      btn.onclick = function() {
        var id = btn.getAttribute('data-id') || '';
        if (!id) return showMessage('无法删除该作品记录，请刷新后重试。', true);
        showConfirmDialog({
          title: '删除作品',
          message: '确认删除这个数字人作品记录？删除后不会再显示在历史作品中。',
          confirmText: '删除',
          tone: 'danger'
        }).then(function(confirmed) {
          if (!confirmed) return;
          var oldText = btn.textContent;
          btn.disabled = true;
          btn.textContent = '删除中...';
          requestDelete('/api/shanjian-digital-human/videos/' + encodeURIComponent(id))
            .then(function() {
              state.videoHistory = (state.videoHistory || []).filter(function(item) {
                return String(item && item.id) !== String(id);
              });
              renderVideoHistory();
              showMessage('作品记录已删除。', false);
            })
            .catch(function(err) {
              showMessage(err && err.message ? err.message : '删除作品记录失败', true);
            })
            .finally(function() {
              btn.disabled = false;
              btn.textContent = oldText;
            });
        });
      };
    });
  }

  function loadVideoHistory(silent) {
    return requestGet('/api/shanjian-digital-human/videos')
      .then(function(data) {
        state.videoHistory = ((data && data.items) || []).map(normalizeVideoHistoryItem);
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
    document.body.classList.add('shanjian-modal-open');
  }

  function closeModal(id) {
    var el = $(id);
    if (!el) return;
    if (id === 'shanjianVoiceCreateModal' && state.voiceRecording) {
      stopVoiceRecording(false);
    }
    el.style.display = 'none';
    var stillOpen = Array.prototype.some.call(document.querySelectorAll('.shanjian-modal'), function(modal) {
      return modal.style.display !== 'none';
    });
    if (!stillOpen) document.body.classList.remove('shanjian-modal-open');
  }

  function resetCreateForms() {
    ['shanjianAvatarImageName', 'shanjianAvatarVideoName', 'shanjianVoiceCreateName'].forEach(function(id) {
      if ($(id)) $(id).value = '';
    });
    ['shanjianAvatarImageAuthText', 'shanjianAvatarVideoAuthText'].forEach(function(id) {
      if ($(id)) $(id).value = defaultShanjianAuthText();
    });
    if ($('shanjianVoiceLanguageSelect')) $('shanjianVoiceLanguageSelect').value = 'zh';
    setFieldError('shanjianVoiceCreateName', 'shanjianVoiceCreateNameError', '');
    ['shanjianAvatarImageFile', 'shanjianAvatarVideoFile', 'shanjianVoiceCreateFile', 'shanjianAvatarImageAuthFile', 'shanjianAvatarVideoAuthFile'].forEach(function(id) {
      if ($(id)) $(id).value = '';
      revokeUploadPreview(id);
    });
    cancelVoiceRecording();
    state.voiceCreateRecordedFile = null;
    ['shanjianAvatarImageFileMeta', 'shanjianAvatarVideoFileMeta', 'shanjianVoiceFileMeta', 'shanjianAvatarImageAuthFileMeta', 'shanjianAvatarVideoAuthFileMeta'].forEach(function(id) {
      if ($(id)) {
        $(id).style.display = 'none';
        $(id).innerHTML = '';
      }
    });
    [
      ['shanjianAvatarImageUploadBox', 'shanjianAvatarImageFile', 'shanjianAvatarImagePreview', 'shanjianAvatarImageFileMeta', 'image'],
      ['shanjianAvatarImageAuthUploadBox', 'shanjianAvatarImageAuthFile', 'shanjianAvatarImageAuthPreview', 'shanjianAvatarImageAuthFileMeta', 'video'],
      ['shanjianAvatarVideoUploadBox', 'shanjianAvatarVideoFile', 'shanjianAvatarVideoPreview', 'shanjianAvatarVideoFileMeta', 'video'],
      ['shanjianAvatarVideoAuthUploadBox', 'shanjianAvatarVideoAuthFile', 'shanjianAvatarVideoAuthPreview', 'shanjianAvatarVideoAuthFileMeta', 'video'],
      ['shanjianVoiceUploadBox', 'shanjianVoiceCreateFile', 'shanjianVoiceFilePreview', 'shanjianVoiceFileMeta', 'audio']
    ].forEach(function(item) {
      syncUploadSelection(item[0], item[1], item[2], item[3], item[4], null);
    });
    ['shanjianAvatarImageAgree', 'shanjianAvatarVideoAgree', 'shanjianVoiceCreateAgree'].forEach(function(id) {
      if ($(id)) $(id).checked = true;
    });
    var radios = document.querySelectorAll('input[name="shanjianAvatarImageModel"]');
    Array.prototype.forEach.call(radios, function(radio) {
      radio.checked = radio.value === '2';
    });
    refreshVoiceRecordPrompt(true);
    setVoiceRecordStatus('可上传本地音频，或直接使用电脑麦克风录音。', 'muted');
    syncCreateSubmitStates();
  }

  function formatFileSize(size) {
    var num = Number(size || 0);
    if (!isFinite(num) || num <= 0) return '--';
    if (num >= 1024 * 1024) return (num / (1024 * 1024)).toFixed(2) + ' MB';
    return (num / 1024).toFixed(1) + ' KB';
  }

  function formatRecordDuration(ms) {
    var totalSeconds = Math.max(0, Math.floor(Number(ms || 0) / 1000));
    var minutes = Math.floor(totalSeconds / 60);
    var seconds = totalSeconds % 60;
    return String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
  }

  function currentVoicePromptList() {
    var language = (($('shanjianVoiceLanguageSelect') || {}).value || 'zh').trim().toLowerCase();
    return SHANJIAN_VOICE_RECORD_PROMPTS[language] || SHANJIAN_VOICE_RECORD_PROMPTS.zh;
  }

  function syncVoiceRecordButtons() {
    var supported = !!(navigator.mediaDevices
      && typeof navigator.mediaDevices.getUserMedia === 'function'
      && (window.AudioContext || window.webkitAudioContext));
    var startBtn = $('shanjianVoiceRecordStartBtn');
    var stopBtn = $('shanjianVoiceRecordStopBtn');
    var promptBtn = $('shanjianVoicePromptShuffleBtn');
    if (startBtn) {
      startBtn.disabled = !!state.submitting || state.voiceRecording || state.voiceRecordingPending || !supported;
      startBtn.classList.toggle('is-recording', !!state.voiceRecording);
      startBtn.classList.toggle('is-pending', !!state.voiceRecordingPending);
      if (state.voiceRecording) {
        startBtn.innerHTML = '<span class="shanjian-record-live-dot"></span><span>录音中…</span>';
      } else if (state.voiceRecordingPending) {
        startBtn.innerHTML = '<span class="shanjian-record-live-dot"></span><span>等待麦克风许可…</span>';
      } else {
        startBtn.innerHTML = '开始录音';
      }
    }
    if (stopBtn) {
      stopBtn.disabled = !!state.submitting || (!state.voiceRecording && !state.voiceRecordingPending);
      stopBtn.classList.add('shanjian-record-stop-btn');
    }
    if (promptBtn) promptBtn.disabled = !!state.submitting || state.voiceRecording;
  }

  function setVoiceRecordStatus(text, tone) {
    var el = $('shanjianVoiceRecordStatus');
    if (!el) return;
    el.textContent = text || '';
    el.setAttribute('data-tone', tone || 'muted');
  }

  function refreshVoiceRecordPrompt(pickRandom) {
    var prompts = currentVoicePromptList();
    if (!prompts.length) return;
    if (pickRandom) {
      state.voiceCreatePromptIndex = Math.floor(Math.random() * prompts.length);
    } else if (state.voiceCreatePromptIndex >= prompts.length) {
      state.voiceCreatePromptIndex = 0;
    }
    var el = $('shanjianVoiceRecordPrompt');
    if (el) el.textContent = prompts[state.voiceCreatePromptIndex];
  }

  function rotateVoiceRecordPrompt() {
    var prompts = currentVoicePromptList();
    if (!prompts.length) return;
    state.voiceCreatePromptIndex = (state.voiceCreatePromptIndex + 1) % prompts.length;
    refreshVoiceRecordPrompt(false);
  }

  function stopVoiceRecordTimer() {
    if (state.voiceRecordTimer) {
      clearInterval(state.voiceRecordTimer);
      state.voiceRecordTimer = null;
    }
  }

  function cleanupVoiceRecordRuntime() {
    stopVoiceRecordTimer();
    if (state.voiceRecordProcessor) {
      try {
        state.voiceRecordProcessor.disconnect();
      } catch (e) {}
      state.voiceRecordProcessor.onaudioprocess = null;
    }
    if (state.voiceRecordSource) {
      try {
        state.voiceRecordSource.disconnect();
      } catch (e) {}
    }
    if (state.voiceRecordStream) {
      try {
        state.voiceRecordStream.getTracks().forEach(function(track) { track.stop(); });
      } catch (e) {}
    }
    if (state.voiceRecordContext) {
      try {
        state.voiceRecordContext.close();
      } catch (e) {}
    }
    state.voiceRecordProcessor = null;
    state.voiceRecordSource = null;
    state.voiceRecordStream = null;
    state.voiceRecordContext = null;
  }

  function mergeRecordBuffers(buffers) {
    var totalLength = buffers.reduce(function(sum, chunk) { return sum + (chunk ? chunk.length : 0); }, 0);
    var merged = new Float32Array(totalLength);
    var offset = 0;
    buffers.forEach(function(chunk) {
      if (!chunk || !chunk.length) return;
      merged.set(chunk, offset);
      offset += chunk.length;
    });
    return merged;
  }

  function writeWaveString(view, offset, value) {
    for (var i = 0; i < value.length; i += 1) {
      view.setUint8(offset + i, value.charCodeAt(i));
    }
  }

  function encodeWaveFile(buffers, sampleRate) {
    var samples = mergeRecordBuffers(buffers);
    var dataLength = samples.length * 2;
    var wavBuffer = new ArrayBuffer(44 + dataLength);
    var view = new DataView(wavBuffer);
    var offset = 0;
    writeWaveString(view, offset, 'RIFF'); offset += 4;
    view.setUint32(offset, 36 + dataLength, true); offset += 4;
    writeWaveString(view, offset, 'WAVE'); offset += 4;
    writeWaveString(view, offset, 'fmt '); offset += 4;
    view.setUint32(offset, 16, true); offset += 4;
    view.setUint16(offset, 1, true); offset += 2;
    view.setUint16(offset, 1, true); offset += 2;
    view.setUint32(offset, sampleRate, true); offset += 4;
    view.setUint32(offset, sampleRate * 2, true); offset += 4;
    view.setUint16(offset, 2, true); offset += 2;
    view.setUint16(offset, 16, true); offset += 2;
    writeWaveString(view, offset, 'data'); offset += 4;
    view.setUint32(offset, dataLength, true); offset += 4;
    for (var i = 0; i < samples.length; i += 1) {
      var sample = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
    return new Blob([wavBuffer], { type: 'audio/wav' });
  }

  function setInputFileValue(inputId, file) {
    var input = $(inputId);
    if (!input) return;
    if (typeof DataTransfer !== 'undefined') {
      try {
        var transfer = new DataTransfer();
        transfer.items.add(file);
        input.files = transfer.files;
        return;
      } catch (e) {}
    }
    try {
      Object.defineProperty(input, 'files', {
        configurable: true,
        value: [file]
      });
    } catch (e) {}
  }

  function applyRecordedVoiceFile(file) {
    if (!file) return;
    state.voiceCreateRecordedFile = file;
    setInputFileValue('shanjianVoiceCreateFile', file);
    syncUploadSelection('shanjianVoiceUploadBox', 'shanjianVoiceCreateFile', 'shanjianVoiceFilePreview', 'shanjianVoiceFileMeta', 'audio', file);
    setVoiceRecordStatus('录音已生成，可先试听后直接提交。', 'success');
  }

  function cancelVoiceRecording(message) {
    cleanupVoiceRecordRuntime();
    state.voiceRecording = false;
    state.voiceRecordingPending = false;
    state.voiceRecordBuffers = [];
    state.voiceRecordStartedAt = 0;
    syncVoiceRecordButtons();
    if (message) setVoiceRecordStatus(message, 'muted');
  }

  function stopVoiceRecording(shouldSave) {
    if (!state.voiceRecordContext) {
      cancelVoiceRecording('可上传本地音频，或直接使用电脑麦克风录音。');
      return;
    }
    var sampleRate = state.voiceRecordContext.sampleRate || 44100;
    var buffers = state.voiceRecordBuffers.slice();
    var durationMs = Date.now() - (state.voiceRecordStartedAt || Date.now());
    cleanupVoiceRecordRuntime();
    state.voiceRecording = false;
    state.voiceRecordingPending = false;
    state.voiceRecordBuffers = [];
    state.voiceRecordStartedAt = 0;
    syncVoiceRecordButtons();
    if (!shouldSave) {
      setVoiceRecordStatus('录音已取消，可重新开始。', 'muted');
      return;
    }
    if (durationMs < 3000 || !buffers.length) {
      setVoiceRecordStatus('录音时间太短，请至少录制 3 秒。', 'danger');
      showMessage('录音时间太短，请至少录制 3 秒。', true);
      return;
    }
    var waveBlob = encodeWaveFile(buffers, sampleRate);
    var stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
    var recordedFile = new File([waveBlob], 'voice-record-' + stamp + '.wav', { type: 'audio/wav' });
    applyRecordedVoiceFile(recordedFile);
  }

  function startVoiceRecording() {
    if (state.voiceRecording || state.voiceRecordingPending) return;
    if (!(navigator.mediaDevices
      && typeof navigator.mediaDevices.getUserMedia === 'function'
      && (window.AudioContext || window.webkitAudioContext))) {
      setVoiceRecordStatus('当前浏览器不支持直接录音，请改用本地音频上传。', 'danger');
      return showMessage('当前浏览器不支持直接录音，请改用本地音频上传。', true);
    }
    state.voiceRecordingPending = true;
    syncVoiceRecordButtons();
    setVoiceRecordStatus('正在请求麦克风权限，请在浏览器弹窗中点击允许。', 'recording');
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function(stream) {
      var AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      var context = new AudioContextCtor();
      var source = context.createMediaStreamSource(stream);
      var processor = context.createScriptProcessor(4096, 1, 1);
      state.voiceRecordStream = stream;
      state.voiceRecordContext = context;
      state.voiceRecordSource = source;
      state.voiceRecordProcessor = processor;
      state.voiceRecordBuffers = [];
      state.voiceRecordStartedAt = Date.now();
      state.voiceRecordingPending = false;
      state.voiceRecording = true;
      processor.onaudioprocess = function(ev) {
        if (!state.voiceRecording) return;
        var channelData = ev.inputBuffer.getChannelData(0);
        state.voiceRecordBuffers.push(new Float32Array(channelData));
      };
      source.connect(processor);
      processor.connect(context.destination);
      stopVoiceRecordTimer();
      state.voiceRecordTimer = setInterval(function() {
        setVoiceRecordStatus('录音中 ' + formatRecordDuration(Date.now() - state.voiceRecordStartedAt) + '，请自然朗读上方文案。', 'recording');
      }, 300);
      setVoiceRecordStatus('录音中 00:00，请自然朗读上方文案。', 'recording');
      syncVoiceRecordButtons();
    }).catch(function(err) {
      state.voiceRecording = false;
      state.voiceRecordingPending = false;
      cleanupVoiceRecordRuntime();
      syncVoiceRecordButtons();
      setVoiceRecordStatus('麦克风无法使用，请检查浏览器权限或设备设置。', 'danger');
      showMessage(err && err.message ? ('麦克风打开失败：' + err.message) : '麦克风打开失败，请检查权限设置。', true);
    });
  }

  function avatarVideoSizeLimitMessage(file) {
    return '视频太大无法上传，当前文件 '
      + formatFileSize(file && file.size)
      + '，超过 200MB 限制。请压缩或裁剪后再上传。';
  }

  function renderUploadError(metaId, message) {
    var el = $(metaId);
    if (!el) return;
    el.style.display = 'block';
    el.classList.add('is-error');
    el.innerHTML = '<strong>' + escapeHtml(message || '文件无法上传') + '</strong>';
  }

  function validateAvatarVideoUploadFile(inputId, file, metaId) {
    if (inputId !== 'shanjianAvatarVideoFile' || !file) return true;
    if (Number(file.size || 0) <= SHANJIAN_AVATAR_VIDEO_MAX_BYTES) return true;
    var message = avatarVideoSizeLimitMessage(file);
    renderUploadError(metaId || 'shanjianAvatarVideoFileMeta', message);
    showMessage(message, true);
    return false;
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
      el.classList.remove('is-error');
      el.innerHTML = '';
      return;
    }
    el.style.display = 'block';
    el.classList.remove('is-error');
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
      + '<div class="shanjian-upload-preview-inner shanjian-upload-preview-inner-' + escapeHtml(previewKind || 'image') + '">'
      + mediaHtml
      + '<button type="button" class="shanjian-upload-remove" aria-label="删除已上传文件">×</button>'
      + '</div>';

    var removeBtn = preview.querySelector('.shanjian-upload-remove');
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
    if (inputId === 'shanjianVoiceCreateFile') {
      state.voiceCreateRecordedFile = null;
      setVoiceRecordStatus('已清空声音样本，可重新上传或重新录音。', 'muted');
    }
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
      var file = input.files && input.files[0] ? input.files[0] : null;
      if (inputId === 'shanjianVoiceCreateFile') {
        state.voiceCreateRecordedFile = file || null;
        if (file) setVoiceRecordStatus('已选择本地音频，可直接提交创建声音。', 'success');
      }
      if (!validateAvatarVideoUploadFile(inputId, file, metaId)) {
        if (input) input.value = '';
        revokeUploadPreview(inputId);
        syncUploadSelection(triggerId, inputId, previewId, metaId, previewKind, null);
        renderUploadError(metaId, avatarVideoSizeLimitMessage(file));
        return;
      }
      syncUploadSelection(triggerId, inputId, previewId, metaId, previewKind, file);
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
      var file = ev.dataTransfer.files[0];
      if (inputId === 'shanjianVoiceCreateFile') {
        state.voiceCreateRecordedFile = file || null;
        if (file) setVoiceRecordStatus('已选择本地音频，可直接提交创建声音。', 'success');
      }
      if (!validateAvatarVideoUploadFile(inputId, file, metaId)) {
        if (input) input.value = '';
        revokeUploadPreview(inputId);
        syncUploadSelection(triggerId, inputId, previewId, metaId, previewKind, null);
        renderUploadError(metaId, avatarVideoSizeLimitMessage(file));
        return;
      }
      input.files = ev.dataTransfer.files;
      syncUploadSelection(triggerId, inputId, previewId, metaId, previewKind, file);
    });
    syncUploadSelection(triggerId, inputId, previewId, metaId, previewKind, input.files && input.files[0] ? input.files[0] : null);
  }

  function startTask(taskType, taskKindLabel, taskId, taskApiScope) {
    state.taskType = taskType || '';
    state.taskId = taskId || '';
    state.taskApiScope = taskApiScope || '';
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
    var item = normalizeProfileItem((data && data.profile) || (data && data.item) || (data || {}));
    if (data && data.virtualman_id && !item.virtualman_id) item.virtualman_id = data.virtualman_id;
    updateTaskStatus('已完成', 'success');
    Promise.allSettled([loadAvatarLibrary(true)]).then(function() {
      var all = (state.avatarLibrary.mine || []).concat(state.avatarLibrary.public || []);
      var created = all.find(function(entry) {
        return (item.id && String(entry.id) === String(item.id))
          || (item.avatar && entry.avatar === item.avatar)
          || (item.virtualman_id && entry.virtualman_id === item.virtualman_id)
          || (item.task_id && String(entry.task_id || '') === String(item.task_id));
      });
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
        + '<div class="shanjian-success-meta">声音 ID：' + escapeHtml(data.voice || '--') + '</div>'
        + (item.demo_url ? '<div class="shanjian-success-audio"><audio controls preload="none" src="' + escapeHtml(normalizeAssetUrl(item.demo_url)) + '"></audio></div>' : ''));
      showMessage('声音创建成功，已自动刷新“我的声音”。', false);
    });
  }

  function pollTask(immediate) {
    clearPoll();
    if (!state.taskId || !state.taskType) return;
    state.pollTimer = setTimeout(function() {
      var path = state.taskType === 'video'
        ? '/api/shanjian-digital-human/video/task'
        : (state.taskType.indexOf('avatar') === 0 ? '/api/shanjian-digital-human/profile/task' : '/api/hifly/my/voice/task');
      var pollRequest = state.taskType.indexOf('voice') === 0 ? requestCloud : request;
      pollRequest(path, { task_id: state.taskId, token: tokenValue() })
        .then(function(data) {
          var status = data && data.status != null ? data.status : '';
          var statusText = data.status_text || '处理中';
          if (isTaskSuccessStatus(status)) {
            if (state.taskType === 'video') {
              var videoUrl = data.video_url || (data.record && data.record.video_url) || '';
              updateTaskStatus('已完成', 'success');
              if (videoUrl) renderResultVideo(videoUrl);
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
          if (isTaskFailedStatus(status)) {
            updateTaskStatus('失败', 'danger');
            renderResultPlaceholder(statusText || '任务失败', data.message || '请检查上传文件后重试。', false);
            showMessage(data.message || '任务失败', true);
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
    var selectedProfileId = state.selectedAvatar && state.selectedAvatar.id ? Number(state.selectedAvatar.id) : 0;
    var selectedTemplate = state.selectedTemplate && state.selectedTemplate.id ? state.selectedTemplate : null;
    var templateOptions = currentTemplateClipOptions();
    var title = (($('shanjianTitleInput') || {}).value || '数字人口播').trim();
    var voice = state.selectedVoice && state.selectedVoice.voice ? state.selectedVoice.voice : ((($('shanjianVoiceInput') || {}).value || '').trim());
    var text = (($('shanjianScriptInput') || {}).value || '').trim();
    var audioFile = $('shanjianAudioDriveFile') && $('shanjianAudioDriveFile').files ? $('shanjianAudioDriveFile').files[0] : null;
    var taskKindLabel = selectedTemplate ? '真人视频模板剪辑' : (mode === 'audio' ? '声音驱动视频' : '数字人口播');
    var submittingHint = selectedTemplate
      ? '正在提交真人视频模板剪辑任务...'
      : (mode === 'audio' ? '正在提交声音驱动视频任务...' : '正在提交必火智能数字人口播任务...');
    var processingHint = selectedTemplate
      ? '正在生成真人视频模板剪辑视频...'
      : (mode === 'audio' ? '正在生成声音驱动视频...' : '正在生成数字人口播视频...');

    if (!selectedProfileId) return showMessage('请先从右侧选择一个数字人。', true);
    if (mode === 'tts') {
      if (!voice) return showMessage('请先从右侧选择一个声音。', true);
      if (isConsumerPreviewVoice(voice)) return showMessage('该公共声音仅支持试听，请选择可生成公共声音或“我的声音”。', true);
      if (!text) return showMessage('请先填写口播文案。', true);
    } else if (!audioFile) {
      return showMessage('请先上传驱动音频。', true);
    }

    setBusy(true);
    startTask('video', taskKindLabel, '', 'local');
    renderResultPlaceholder('任务已提交', submittingHint, true);
    showMessage(submittingHint, false);

    if (mode === 'audio') {
      uploadAssetFile(audioFile).then(function(assetData) {
        var body = {
          profile_id: selectedProfileId,
          title: title,
          audio_asset_id: assetData.asset_id,
          template_scene: selectedTemplate ? selectedTemplateScene() : ''
        };
        if (selectedTemplate) {
          body.style_id = selectedTemplate.id;
          Object.assign(body, templateOptions);
          body.materials = materialRows();
        }
        return request('/api/shanjian-digital-human/video/create', body);
      }).then(function(data) {
        startTask('video', taskKindLabel, data.task_id || '', 'local');
        updateTaskStatus('等待中', 'processing');
        renderResultPlaceholder('任务已提交', processingHint, true);
        showMessage('任务已提交，正在生成中。', false);
        clearUploadSelection('shanjianAudioDriveUploadBox', 'shanjianAudioDriveFile', 'shanjianAudioDrivePreview', 'shanjianAudioDriveFileMeta', 'audio');
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

    var selectedVoiceParams = currentSelectedVoiceParams();
    var previewPayload = previewTtsPayload(
      voice,
      text,
      {
        rate: selectedVoiceParams.rate,
        volume: selectedVoiceParams.volume,
        pitch: selectedVoiceParams.pitch,
        emotion: 'happy',
        instructions: selectedVoiceParams.instructions
      },
      voiceProvider(state.selectedVoice, null)
    );
    requestCloud('/api/hifly/my/voice/preview-tts', previewPayload).then(function(ttsData) {
      try {
        console.log('[shanjian-debug] preview-tts payload', previewPayload);
        console.log('[shanjian-debug] preview-tts result', ttsData);
      } catch (previewLogErr) {}
      var audioUrl = String(ttsData.audio_url || '').trim();
      try {
        console.log('[shanjian-debug] preview-tts audio_url', audioUrl);
      } catch (audioLogErr) {}
      if (!audioUrl) throw new Error('声音合成成功，但没有拿到音频地址。');
      var body = {
        profile_id: selectedProfileId,
        title: title,
        text: text,
        audio_url: audioUrl,
        template_scene: selectedTemplate ? selectedTemplateScene() : ''
      };
      if (selectedTemplate) {
        body.style_id = selectedTemplate.id;
        Object.assign(body, templateOptions);
        body.materials = materialRows();
      }
      return request('/api/shanjian-digital-human/video/create', body);
    }).then(function(data) {
      startTask('video', taskKindLabel, data.task_id || '', 'local');
      updateTaskStatus('等待中', 'processing');
      renderResultPlaceholder('任务已提交', processingHint, true);
      showMessage('任务已提交，正在生成中。', false);
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
    var file = $('shanjianAvatarImageFile') && $('shanjianAvatarImageFile').files ? $('shanjianAvatarImageFile').files[0] : null;
    var authFile = $('shanjianAvatarImageAuthFile') && $('shanjianAvatarImageAuthFile').files ? $('shanjianAvatarImageAuthFile').files[0] : null;
    var title = (($('shanjianAvatarImageName') || {}).value || '').trim();
    var authText = (($('shanjianAvatarImageAuthText') || {}).value || '').trim();
    var agree = !!(($('shanjianAvatarImageAgree') || {}).checked);
    if (!title) return showMessage('请填写数字人名称。', true);
    if (!file) return showMessage('请先上传图片。', true);
    if (!authText) return showMessage('请填写授权说明。', true);
    if (authFile && !validateAvatarVideoUploadFile('shanjianAvatarImageAuthFile', authFile, 'shanjianAvatarImageAuthFileMeta')) return showMessage('授权视频不符合要求，请重新选择。', true);
    if (!agree) return showMessage('请先勾选同意承诺。', true);

    setBusy(true);
    closeModal('shanjianAvatarModeModal');
    closeModal('shanjianAvatarImageModal');
    startTask('avatar-image', '图片数字人创建');
    renderResultPlaceholder('任务已提交', '正在上传图片并创建数字人...', true);
    showMessage('正在提交图片数字人创建任务...', false);

    Promise.all([uploadAssetFile(file), authFile ? uploadAssetFile(authFile) : Promise.resolve(null)])
      .then(function(results) {
        var imageAsset = results[0];
        var authAsset = results[1];
        return request('/api/shanjian-digital-human/profile/train', {
          title: title,
          mode: 'image',
          image_asset_id: imageAsset.asset_id,
          auth_video_asset_id: authAsset && authAsset.asset_id ? authAsset.asset_id : '',
          auth_text: authText,
          make_default: true
        });
      })
      .then(function(data) {
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
    var file = $('shanjianAvatarVideoFile') && $('shanjianAvatarVideoFile').files ? $('shanjianAvatarVideoFile').files[0] : null;
    var authFile = $('shanjianAvatarVideoAuthFile') && $('shanjianAvatarVideoAuthFile').files ? $('shanjianAvatarVideoAuthFile').files[0] : null;
    var title = (($('shanjianAvatarVideoName') || {}).value || '').trim();
    var authText = (($('shanjianAvatarVideoAuthText') || {}).value || '').trim();
    var agree = !!(($('shanjianAvatarVideoAgree') || {}).checked);
    if (!title) return showMessage('请填写数字人名称。', true);
    if (!file) {
      renderUploadError('shanjianAvatarVideoFileMeta', '请先上传视频。');
      return showMessage('请先上传视频。', true);
    }
    if (!validateAvatarVideoUploadFile('shanjianAvatarVideoFile', file, 'shanjianAvatarVideoFileMeta')) {
      if ($('shanjianAvatarVideoFile')) $('shanjianAvatarVideoFile').value = '';
      revokeUploadPreview('shanjianAvatarVideoFile');
      syncUploadSelection('shanjianAvatarVideoUploadBox', 'shanjianAvatarVideoFile', 'shanjianAvatarVideoPreview', 'shanjianAvatarVideoFileMeta', 'video', null);
      renderUploadError('shanjianAvatarVideoFileMeta', avatarVideoSizeLimitMessage(file));
      return;
    }
    if (authFile && !validateAvatarVideoUploadFile('shanjianAvatarVideoAuthFile', authFile, 'shanjianAvatarVideoAuthFileMeta')) {
      return showMessage('授权视频不符合要求，请重新选择。', true);
    }
    if (!authText) return showMessage('请填写授权说明。', true);
    if (!agree) return showMessage('请先勾选同意承诺。', true);

    setBusy(true);
    closeModal('shanjianAvatarModeModal');
    closeModal('shanjianAvatarVideoModal');
    startTask('avatar-video', '视频数字人创建');
    renderResultPlaceholder('任务已提交', '正在上传视频并创建数字人...', true);
    showMessage('正在提交视频数字人创建任务...', false);

    uploadAssetFile(file)
      .then(function(videoAsset) {
        var authPromise = authFile ? uploadAssetFile(authFile) : Promise.resolve(videoAsset);
        return authPromise.then(function(authAsset) {
          return request('/api/shanjian-digital-human/profile/train', {
            title: title,
            mode: 'fast_video',
            video_asset_id: videoAsset.asset_id,
            auth_video_asset_id: authAsset.asset_id,
            auth_text: authText,
            make_default: true
          });
        });
      })
      .then(function(data) {
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
    var file = $('shanjianVoiceCreateFile') && $('shanjianVoiceCreateFile').files ? $('shanjianVoiceCreateFile').files[0] : null;
    if (!file && state.voiceCreateRecordedFile) file = state.voiceCreateRecordedFile;
    var title = (($('shanjianVoiceCreateName') || {}).value || '').trim();
    var language = (($('shanjianVoiceLanguageSelect') || {}).value || 'zh').trim();
    var agree = !!(($('shanjianVoiceCreateAgree') || {}).checked);
    if (!title) {
      setFieldError('shanjianVoiceCreateName', 'shanjianVoiceCreateNameError', '请输入声音名称后再提交。');
      showMessage('请填写声音名称。', true);
      if ($('shanjianVoiceCreateName') && $('shanjianVoiceCreateName').focus) $('shanjianVoiceCreateName').focus();
      return;
    }
    setFieldError('shanjianVoiceCreateName', 'shanjianVoiceCreateNameError', '');
    if (!file) return showMessage('请先上传声音样本，或使用电脑录音生成样本。', true);
    if (!agree) return showMessage('请先勾选同意承诺。', true);

    var formData = new FormData();
    formData.append('token', tokenValue());
    formData.append('title', title);
    formData.append('languages', language || 'zh');
    formData.append('voice_type', '8');
    formData.append('file', file);

    setBusy(true);
    closeModal('shanjianVoiceCreateModal');
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
    if ($('shanjianBackBtn')) $('shanjianBackBtn').addEventListener('click', function() {
      if (typeof window._ensureSkillStoreVisible === 'function') window._ensureSkillStoreVisible();
      try { location.hash = 'skill-store'; } catch (e) {}
    });
    if ($('shanjianRefreshLibraryBtn')) $('shanjianRefreshLibraryBtn').addEventListener('click', function() {
      loadAvatarLibrary().catch(function(err) { showMessage(err && err.message ? err.message : '数字人列表刷新失败', true); });
    });
    if ($('shanjianLoadVoicesBtn')) $('shanjianLoadVoicesBtn').addEventListener('click', function() {
      loadVoices().catch(function(err) { showMessage(err && err.message ? err.message : '声音列表刷新失败', true); });
    });
    if ($('shanjianGenerateBtn')) $('shanjianGenerateBtn').addEventListener('click', generateVideo);
    if ($('shanjianVideoModeTtsBtn')) $('shanjianVideoModeTtsBtn').addEventListener('click', function() { setVideoCreateMode('tts'); });
    if ($('shanjianVideoModeAudioBtn')) $('shanjianVideoModeAudioBtn').addEventListener('click', function() { setVideoCreateMode('audio'); });
    if ($('shanjianAvatarLibraryTabBtn')) $('shanjianAvatarLibraryTabBtn').addEventListener('click', function() { setActiveView('avatar'); });
    if ($('shanjianVoiceLibraryTabBtn')) $('shanjianVoiceLibraryTabBtn').addEventListener('click', function() { setActiveView('voice'); });
    if ($('shanjianTemplateLibraryTabBtn')) $('shanjianTemplateLibraryTabBtn').addEventListener('click', function() { setActiveView('template'); });
    if ($('shanjianMaterialLibraryTabBtn')) $('shanjianMaterialLibraryTabBtn').addEventListener('click', function() { setActiveView('material'); });
    if ($('shanjianResultTabBtn')) $('shanjianResultTabBtn').addEventListener('click', function() {
      setActiveView('result');
      loadVideoHistory(true);
    });
    if ($('shanjianVideoHistoryRefreshBtn')) $('shanjianVideoHistoryRefreshBtn').addEventListener('click', function() {
      loadVideoHistory();
    });
    if ($('shanjianResultBackBtn')) $('shanjianResultBackBtn').addEventListener('click', function() { setActiveView('avatar'); });
    if ($('shanjianOpenAvatarLibraryBtn')) $('shanjianOpenAvatarLibraryBtn').addEventListener('click', function() { setActiveView('avatar'); });
    if ($('shanjianOpenVoiceLibraryBtn')) $('shanjianOpenVoiceLibraryBtn').addEventListener('click', function() { setActiveView('voice'); });
    if ($('shanjianOpenTemplateLibraryBtn')) $('shanjianOpenTemplateLibraryBtn').addEventListener('click', function() { setActiveView('template'); });
    if ($('shanjianOpenMaterialLibraryBtn')) $('shanjianOpenMaterialLibraryBtn').addEventListener('click', function() { setActiveView('material'); });
    if ($('shanjianOpenMaterialLibraryBtnInline')) $('shanjianOpenMaterialLibraryBtnInline').addEventListener('click', function() { setActiveView('material'); });
    if ($('shanjianUploadMaterialBtnInline')) $('shanjianUploadMaterialBtnInline').addEventListener('click', function() {
      var input = $('shanjianInlineMaterialUploadInput');
      if (input) input.click();
    });
    if ($('shanjianInlineMaterialUploadInput')) $('shanjianInlineMaterialUploadInput').addEventListener('change', function(ev) {
      uploadInlineMaterials(ev && ev.target ? ev.target.files : null, $('shanjianUploadMaterialBtnInline')).catch(function() {});
    });
    if ($('shanjianClearTemplateBtn')) $('shanjianClearTemplateBtn').addEventListener('click', function() { selectTemplate(null, false); });
    if ($('shanjianClearMaterialsBtnInline')) $('shanjianClearMaterialsBtnInline').addEventListener('click', function() {
      state.selectedMaterials = [];
      syncMaterialPickerSelection();
      renderSelectedMaterials();
      renderMaterialLibrary();
      if ($('shanjianMaterialPickerModal')) renderMaterialPicker();
      showMessage('已清空混剪素材。', false);
    });
    if ($('shanjianAvatarSearchInput')) $('shanjianAvatarSearchInput').addEventListener('input', function(ev) {
      state.avatarSearch = ev.target.value || '';
      renderAvatarLibrary();
    });
    if ($('shanjianVoiceSearchInput')) $('shanjianVoiceSearchInput').addEventListener('input', function(ev) {
      state.voiceSearch = ev.target.value || '';
      renderVoiceLibrary();
    });
    if ($('shanjianTemplateSearchInput')) $('shanjianTemplateSearchInput').addEventListener('input', function(ev) {
      state.templateSearch = ev.target.value || '';
      renderTemplateLibrary();
    });
    if ($('shanjianMaterialSearchInput')) $('shanjianMaterialSearchInput').addEventListener('input', function(ev) {
      state.materialPicker.query = ev.target.value || '';
      renderMaterialLibrary();
    });
    if ($('shanjianRefreshTemplatesBtn')) $('shanjianRefreshTemplatesBtn').addEventListener('click', function() {
      loadTemplateLibrary(false, true).catch(function(err) { showMessage(err && err.message ? err.message : '模板列表刷新失败', true); });
    });
    if ($('shanjianMaterialReloadBtn')) $('shanjianMaterialReloadBtn').addEventListener('click', function() {
      loadMaterialPickerItems(true).then(function() {
        renderMaterialLibrary();
      }).catch(function(err) {
        showMessage(err && err.message ? err.message : '素材库刷新失败', true);
      });
    });
    if ($('shanjianTemplateMoreBtn')) $('shanjianTemplateMoreBtn').addEventListener('click', function() {
      loadMoreTemplates().catch(function(err) { showMessage(err && err.message ? err.message : '加载更多模板失败', true); });
    });
    if ($('shanjianMaterialFilterAllBtn')) $('shanjianMaterialFilterAllBtn').addEventListener('click', function() {
      state.materialFilter = 'all';
      renderMaterialLibrary();
    });
    if ($('shanjianMaterialFilterImageBtn')) $('shanjianMaterialFilterImageBtn').addEventListener('click', function() {
      state.materialFilter = 'image';
      renderMaterialLibrary();
    });
    if ($('shanjianMaterialFilterVideoBtn')) $('shanjianMaterialFilterVideoBtn').addEventListener('click', function() {
      state.materialFilter = 'video';
      renderMaterialLibrary();
    });
    document.addEventListener('click', function(ev) {
      var panel = $('shanjianVoiceParamSlider');
      if (!panel || panel.style.display === 'none') return;
      var target = ev.target;
      if (panel.contains(target)) return;
      if (target && target.classList && target.classList.contains('shanjian-voice-param-input')) return;
      closeVoiceParamSlider();
    });
    window.addEventListener('resize', closeVoiceParamSlider);

    [
      ['shanjianMineMoreBtn', 'avatarMine'],
      ['shanjianMineVoiceMoreBtn', 'voiceMine'],
      ['shanjianPublicVoiceMoreBtn', 'voicePublic']
    ].forEach(function(pair) {
      var btn = $(pair[0]);
      if (!btn) return;
      btn.addEventListener('click', function() {
        state.libraryExpanded[pair[1]] = !state.libraryExpanded[pair[1]];
        if (pair[1].indexOf('voice') === 0) renderVoiceLibrary();
        else renderAvatarLibrary();
      });
    });
    if ($('shanjianPublicMoreBtn')) $('shanjianPublicMoreBtn').addEventListener('click', function() {
      loadMorePublicAvatars();
    });

    if ($('shanjianOpenAvatarCreateBtn')) $('shanjianOpenAvatarCreateBtn').addEventListener('click', function() {
      resetCreateForms();
      openModal('shanjianAvatarModeModal');
    });
    if ($('shanjianOpenVoiceCreateBtn')) $('shanjianOpenVoiceCreateBtn').addEventListener('click', function() {
      resetCreateForms();
      openModal('shanjianVoiceCreateModal');
    });
    if ($('shanjianCreateVoiceBtn')) $('shanjianCreateVoiceBtn').addEventListener('click', function() {
      resetCreateForms();
      openModal('shanjianVoiceCreateModal');
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-modal-close]'), function(btn) {
      btn.addEventListener('click', function() {
        closeModal(btn.getAttribute('data-modal-close'));
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-avatar-mode]'), function(btn) {
      btn.addEventListener('click', function() {
        var mode = btn.getAttribute('data-avatar-mode');
        closeModal('shanjianAvatarModeModal');
        if (mode === 'video') openModal('shanjianAvatarVideoModal');
        else openModal('shanjianAvatarImageModal');
      });
    });

    wireFileBox('shanjianAvatarImageUploadBox', 'shanjianAvatarImageFile', 'shanjianAvatarImageFileMeta', 'shanjianAvatarImagePreview', 'image');
    wireFileBox('shanjianAvatarImageAuthUploadBox', 'shanjianAvatarImageAuthFile', 'shanjianAvatarImageAuthFileMeta', 'shanjianAvatarImageAuthPreview', 'video');
    wireFileBox('shanjianAvatarVideoUploadBox', 'shanjianAvatarVideoFile', 'shanjianAvatarVideoFileMeta', 'shanjianAvatarVideoPreview', 'video');
    wireFileBox('shanjianAvatarVideoAuthUploadBox', 'shanjianAvatarVideoAuthFile', 'shanjianAvatarVideoAuthFileMeta', 'shanjianAvatarVideoAuthPreview', 'video');
    wireFileBox('shanjianVoiceUploadBox', 'shanjianVoiceCreateFile', 'shanjianVoiceFileMeta', 'shanjianVoiceFilePreview', 'audio');
    wireFileBox('shanjianAudioDriveUploadBox', 'shanjianAudioDriveFile', 'shanjianAudioDriveFileMeta', 'shanjianAudioDrivePreview', 'audio');
    if ($('shanjianVoiceRecordStartBtn')) $('shanjianVoiceRecordStartBtn').addEventListener('click', startVoiceRecording);
    if ($('shanjianVoiceRecordStopBtn')) $('shanjianVoiceRecordStopBtn').addEventListener('click', function() { stopVoiceRecording(true); });
    if ($('shanjianVoicePromptShuffleBtn')) $('shanjianVoicePromptShuffleBtn').addEventListener('click', rotateVoiceRecordPrompt);
    if ($('shanjianVoiceLanguageSelect')) $('shanjianVoiceLanguageSelect').addEventListener('change', function() {
      refreshVoiceRecordPrompt(true);
    });
    if ($('shanjianVoiceCreateName')) $('shanjianVoiceCreateName').addEventListener('input', function() {
      if ((this.value || '').trim()) setFieldError('shanjianVoiceCreateName', 'shanjianVoiceCreateNameError', '');
    });

    if ($('shanjianAvatarImageSubmitBtn')) $('shanjianAvatarImageSubmitBtn').addEventListener('click', submitAvatarImageCreate);
    if ($('shanjianAvatarVideoSubmitBtn')) $('shanjianAvatarVideoSubmitBtn').addEventListener('click', submitAvatarVideoCreate);
    if ($('shanjianVoiceSubmitBtn')) $('shanjianVoiceSubmitBtn').addEventListener('click', submitVoiceCreate);
    ['shanjianAvatarImageAgree', 'shanjianAvatarVideoAgree', 'shanjianVoiceCreateAgree'].forEach(function(id) {
      if ($(id)) $(id).addEventListener('change', syncCreateSubmitStates);
    });
    refreshVoiceRecordPrompt(true);
    setVoiceRecordStatus('可上传本地音频，或直接使用电脑麦克风录音。', 'muted');
    syncCreateSubmitStates();
  }

  function bootstrapData() {
    renderSelectedAvatar();
    renderSelectedVoice();
    renderSelectedTemplate();
    renderSelectedMaterials();
    renderMaterialLibrary();
    renderResultPlaceholder('等待生成', '提交后这里会自动显示任务进度和最终结果。', false);
    updateTaskStatus('等待提交', 'idle');
    updateTaskKind(state.taskKindLabel || '未开始');
    syncVideoCreateModeUI();

    return Promise.allSettled([
      loadAvatarCoverManifest(),
      loadAvatarLibrary(true),
      loadVoices(true),
      loadTemplateLibrary(true, true)
    ]).then(function(results) {
      var rejected = results.filter(function(item) { return item.status === 'rejected'; });
      renderSelectedAvatar();
      renderSelectedVoice();
      renderSelectedTemplate();
      renderSelectedMaterials();
      renderAvatarLibrary();
      renderVoiceLibrary();
      renderTemplateLibrary();
      renderMaterialLibrary();
      if (rejected.length >= 3 && results[1].status === 'rejected' && results[2].status === 'rejected' && results[3].status === 'rejected') {
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
    var oldStyle = $('shanjianDynamicStyle');
    if (oldStyle && oldStyle.getAttribute('data-version') === SHANJIAN_STYLE_VERSION) return;
    if (oldStyle && oldStyle.parentNode) oldStyle.parentNode.removeChild(oldStyle);
    var style = document.createElement('style');
    style.id = 'shanjianDynamicStyle';
    style.setAttribute('data-version', SHANJIAN_STYLE_VERSION);
    style.textContent = ''
      + '#content-shanjian-digital-human .tvc-studio{min-height:0;}'
      + '#content-shanjian-digital-human .shanjian-shell{display:grid;grid-template-columns:minmax(330px,410px) minmax(0,1fr);gap:1rem;align-items:stretch;height:calc(100dvh - 158px);min-height:560px;max-height:calc(100dvh - 158px);overflow:hidden;}'
      + '#content-shanjian-digital-human .shanjian-sidebar{display:flex;flex-direction:column;gap:0.8rem;position:relative;top:auto;min-height:0;max-height:100%;overflow-y:auto;overscroll-behavior:contain;padding-right:0.22rem;}'
      + '#content-shanjian-digital-human .shanjian-main{display:flex;flex-direction:column;gap:1rem;min-width:0;min-height:0;max-height:100%;overflow-y:auto;overscroll-behavior:contain;padding-right:0.22rem;}'
      + '#content-shanjian-digital-human .shanjian-sidebar::-webkit-scrollbar,#content-shanjian-digital-human .shanjian-main::-webkit-scrollbar{width:8px;}'
      + '#content-shanjian-digital-human .shanjian-sidebar::-webkit-scrollbar-thumb,#content-shanjian-digital-human .shanjian-main::-webkit-scrollbar-thumb{background:rgba(117,139,179,0.24);border-radius:999px;}'
      + '#content-shanjian-digital-human .shanjian-submit-panel{order:0;}'
      + '#content-shanjian-digital-human .shanjian-selection-panel{order:1;}'
      + '#content-shanjian-digital-human .shanjian-service-panel{order:2;}'
      + '#content-shanjian-digital-human .shanjian-compact-selection-block{display:flex;flex-direction:column;gap:0.62rem;}'
      + '#content-shanjian-digital-human .shanjian-compact-selection-head{display:flex;align-items:flex-start;justify-content:space-between;gap:0.6rem;margin:0 0 0.48rem;}'
      + '#content-shanjian-digital-human .shanjian-compact-selection-head strong{display:block;color:#26334d;font-size:0.88rem;}'
      + '#content-shanjian-digital-human .shanjian-compact-selection-head span{display:block;margin-top:0.16rem;color:#7b8498;font-size:0.74rem;line-height:1.45;}'
      + '#content-shanjian-digital-human .shanjian-script-input{min-height:128px!important;max-height:240px;resize:vertical;}'
      + '#content-shanjian-digital-human .shanjian-panel-head{display:flex;justify-content:space-between;gap:0.75rem;align-items:flex-start;flex-wrap:wrap;margin-bottom:0.7rem;}'
      + '#content-shanjian-digital-human .shanjian-head-actions,#content-shanjian-digital-human .shanjian-toolbar-actions{display:flex;gap:0.5rem;flex-wrap:wrap;}'
      + '#content-shanjian-digital-human .shanjian-mode-switch{display:flex;gap:0.65rem;flex-wrap:wrap;}'
      + '#content-shanjian-digital-human .shanjian-mode-chip{appearance:none;border:1px solid rgba(99,102,241,0.18);background:#fff;border-radius:999px;padding:0.7rem 1rem;font-weight:700;color:#475569;cursor:pointer;transition:all .18s ease;box-shadow:0 8px 24px rgba(15,23,42,0.05);}'
      + '#content-shanjian-digital-human .shanjian-mode-chip.is-active{background:linear-gradient(135deg,#6366f1,#4f46e5);color:#fff;border-color:transparent;box-shadow:0 12px 28px rgba(79,70,229,0.24);}'
      + '#content-shanjian-digital-human .btn.is-disabled{opacity:0.55;cursor:not-allowed;pointer-events:none;}'
      + '#content-shanjian-digital-human .shanjian-record-divider{position:relative;margin:0.2rem 0 0.4rem;text-align:center;}'
      + '#content-shanjian-digital-human .shanjian-record-divider:before{content:"";position:absolute;left:0;right:0;top:50%;height:1px;background:rgba(148,163,184,0.25);}'
      + '#content-shanjian-digital-human .shanjian-record-divider span{position:relative;display:inline-block;padding:0 0.8rem;background:#fff;color:#64748b;font-size:0.78rem;font-weight:700;}'
      + '#content-shanjian-digital-human .shanjian-record-box{border:1px solid rgba(99,102,241,0.12);border-radius:18px;background:linear-gradient(180deg,rgba(248,250,255,0.98),rgba(255,255,255,0.98));padding:0.9rem 1rem;display:flex;flex-direction:column;gap:0.75rem;}'
      + '#content-shanjian-digital-human .shanjian-record-box-head{display:flex;align-items:center;justify-content:space-between;gap:0.75rem;flex-wrap:wrap;}'
      + '#content-shanjian-digital-human .shanjian-record-box-head strong{font-size:0.95rem;color:#1f2b42;}'
      + '#content-shanjian-digital-human .shanjian-record-box-hint{margin:0;color:#667189;font-size:0.82rem;line-height:1.7;}'
      + '#content-shanjian-digital-human .shanjian-record-prompt{padding:0.9rem 1rem;border-radius:16px;background:rgba(99,102,241,0.06);border:1px solid rgba(99,102,241,0.14);color:#2b3345;font-size:0.9rem;line-height:1.8;}'
      + '#content-shanjian-digital-human .shanjian-record-actions{display:flex;gap:0.6rem;flex-wrap:wrap;}'
      + '#content-shanjian-digital-human .shanjian-record-actions .btn{display:inline-flex;align-items:center;gap:0.4rem;}'
      + '#content-shanjian-digital-human .shanjian-record-live-dot{width:0.55rem;height:0.55rem;border-radius:999px;background:currentColor;display:inline-block;box-shadow:0 0 0 0 rgba(255,255,255,0.55);animation:shanjianRecordPulse 1.2s ease infinite;}'
      + '#content-shanjian-digital-human .shanjian-record-start-btn.is-recording{background:linear-gradient(135deg,#ef4444,#dc2626)!important;border-color:transparent!important;color:#fff!important;box-shadow:0 10px 22px rgba(220,38,38,0.26);}'
      + '#content-shanjian-digital-human .shanjian-record-start-btn.is-pending{background:linear-gradient(135deg,#f59e0b,#d97706)!important;border-color:transparent!important;color:#fff!important;box-shadow:0 10px 22px rgba(217,119,6,0.24);}'
      + '#content-shanjian-digital-human .shanjian-record-stop-btn{background:rgba(239,68,68,0.10)!important;border:1px solid rgba(239,68,68,0.28)!important;color:#b42318!important;}'
      + '#content-shanjian-digital-human .shanjian-record-stop-btn:not(:disabled){background:linear-gradient(135deg,#ef4444,#dc2626)!important;border-color:transparent!important;color:#fff!important;box-shadow:0 10px 22px rgba(220,38,38,0.22);}'
      + '#content-shanjian-digital-human .shanjian-record-status{border-radius:14px;padding:0.72rem 0.85rem;font-size:0.82rem;line-height:1.6;background:rgba(148,163,184,0.12);color:#5b677b;}'
      + '#content-shanjian-digital-human .shanjian-record-status[data-tone="recording"]{background:rgba(239,68,68,0.10);color:#b42318;font-weight:700;}'
      + '#content-shanjian-digital-human .shanjian-record-status[data-tone="success"]{background:rgba(16,185,129,0.12);color:#0f766e;font-weight:700;}'
      + '#content-shanjian-digital-human .shanjian-record-status[data-tone="danger"]{background:rgba(239,68,68,0.10);color:#b42318;font-weight:700;}'
      + '#content-shanjian-digital-human .shanjian-field-error{display:none;margin-top:0.45rem;color:#b42318;font-size:0.8rem;line-height:1.5;font-weight:600;}'
      + '#content-shanjian-digital-human input.is-error,#content-shanjian-digital-human select.is-error,#content-shanjian-digital-human textarea.is-error{border-color:rgba(220,38,38,0.48)!important;box-shadow:0 0 0 3px rgba(220,38,38,0.10)!important;background:#fff8f8;}'
      + '@keyframes shanjianRecordPulse{0%{transform:scale(0.96);box-shadow:0 0 0 0 rgba(255,255,255,0.45);}70%{transform:scale(1);box-shadow:0 0 0 8px rgba(255,255,255,0);}100%{transform:scale(0.96);box-shadow:0 0 0 0 rgba(255,255,255,0);}}'
      + '#content-shanjian-digital-human .shanjian-selected-avatar,#content-shanjian-digital-human .shanjian-selected-voice,#content-shanjian-digital-human .shanjian-selected-template{border:1px solid rgba(26,39,68,0.08);border-radius:16px;background:#fff;box-shadow:0 12px 26px rgba(26,39,68,0.06);padding:0.68rem;}'
      + '#content-shanjian-digital-human .shanjian-selected-avatar{display:grid;grid-template-columns:72px minmax(0,1fr);gap:0.62rem;align-items:start;}'
      + '#content-shanjian-digital-human .shanjian-selected-voice{display:grid;grid-template-columns:64px minmax(0,1fr);gap:0.62rem;align-items:start;}'
      + '#content-shanjian-digital-human .shanjian-selected-template{display:grid;grid-template-columns:76px minmax(0,1fr);gap:0.62rem;align-items:start;}'
      + '#content-shanjian-digital-human .shanjian-material-strip{margin-top:0.78rem;padding:0.82rem;border-radius:16px;border:1px solid rgba(14,165,233,0.12);background:linear-gradient(180deg, rgba(248,250,252,0.98), rgba(241,245,249,0.96));}'
      + '#content-shanjian-digital-human .shanjian-material-strip-head{display:flex;align-items:center;justify-content:space-between;gap:0.6rem;flex-wrap:wrap;margin-bottom:0.6rem;}'
      + '#content-shanjian-digital-human .shanjian-material-strip-head strong{display:block;font-size:0.96rem;color:#182033;}'
      + '#content-shanjian-digital-human .shanjian-material-strip-head span{display:block;font-size:0.78rem;color:#6d7791;margin-top:0.14rem;}'
      + '#content-shanjian-digital-human .shanjian-material-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0.55rem;}'
      + '#content-shanjian-digital-human .shanjian-material-list.is-empty{display:block;}'
      + '#content-shanjian-digital-human .shanjian-material-empty{padding:0.7rem 0.8rem;border-radius:12px;border:1px dashed rgba(124,94,255,0.18);background:#fff;color:#6d7791;font-size:0.8rem;line-height:1.45;}'
      + '#content-shanjian-digital-human .shanjian-material-card{display:grid;grid-template-columns:56px minmax(0,1fr) auto;gap:0.58rem;align-items:center;padding:0.54rem 0.58rem;border:1px solid rgba(26,39,68,0.08);border-radius:12px;background:#fff;box-shadow:0 8px 18px rgba(26,39,68,0.05);}'
      + '#content-shanjian-digital-human .shanjian-material-card-thumb{width:56px;height:56px;border-radius:10px;overflow:hidden;background:#eaf0ff;display:flex;align-items:center;justify-content:center;color:#5b6478;font-size:0.76rem;flex:0 0 auto;}'
      + '#content-shanjian-digital-human .shanjian-material-card-thumb img,#content-shanjian-digital-human .shanjian-material-card-thumb video{width:100%;height:100%;object-fit:cover;display:block;}'
      + '#content-shanjian-digital-human .shanjian-material-card-copy{min-width:0;display:flex;flex-direction:column;gap:0.16rem;}'
      + '#content-shanjian-digital-human .shanjian-material-card-title{font-size:0.85rem;font-weight:600;color:#182033;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
      + '#content-shanjian-digital-human .shanjian-material-card-meta{font-size:0.74rem;color:#6d7791;}'
      + '#content-shanjian-digital-human .shanjian-material-card-remove{border:none;background:rgba(239,68,68,0.08);color:#b91c1c;width:30px;height:30px;border-radius:999px;cursor:pointer;flex:0 0 auto;}'
      + '#content-shanjian-digital-human .shanjian-material-picker-item{position:relative;display:grid;grid-template-columns:66px minmax(0,1fr) auto;gap:0.58rem;align-items:center;padding:0.6rem 0.62rem;border:1px solid rgba(26,39,68,0.08);border-radius:14px;background:#fff;cursor:pointer;box-shadow:0 10px 22px rgba(26,39,68,0.05);text-align:left;}'
      + '#content-shanjian-digital-human .shanjian-material-picker-item.is-selected{border-color:rgba(124,94,255,0.34);box-shadow:0 12px 28px rgba(124,94,255,0.14);}'
      + '#content-shanjian-digital-human .shanjian-material-picker-thumb{width:66px;height:66px;border-radius:12px;overflow:hidden;background:#eef2ff;display:flex;align-items:center;justify-content:center;color:#60708c;font-size:0.76rem;}'
      + '#content-shanjian-digital-human .shanjian-material-picker-thumb img,#content-shanjian-digital-human .shanjian-material-picker-thumb video{width:100%;height:100%;object-fit:cover;display:block;}'
      + '#content-shanjian-digital-human .shanjian-material-picker-name{min-width:0;font-size:0.84rem;font-weight:700;color:#1f2b42;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
      + '#content-shanjian-digital-human .shanjian-material-picker-meta{font-size:0.72rem;color:#6d7791;white-space:nowrap;}'
      + '#content-shanjian-digital-human .shanjian-material-picker-check{width:26px;height:26px;border-radius:999px;background:rgba(124,94,255,0.12);color:#6a54e0;display:inline-flex;align-items:center;justify-content:center;font-size:0.76rem;font-weight:800;opacity:0;}'
      + '#content-shanjian-digital-human .shanjian-material-picker-item.is-selected .shanjian-material-picker-check{opacity:1;}'
      + '#content-shanjian-digital-human .shanjian-selected-avatar.is-empty,#content-shanjian-digital-human .shanjian-selected-voice.is-empty,#content-shanjian-digital-human .shanjian-selected-template.is-empty{display:block;}'
      + '#content-shanjian-digital-human .shanjian-selected-empty strong,#content-shanjian-digital-human .shanjian-selected-empty span,#content-shanjian-digital-human .shanjian-result-status-copy strong,#content-shanjian-digital-human .shanjian-result-status-copy span{display:block;}'
      + '#content-shanjian-digital-human .shanjian-selected-empty span{margin-top:0.36rem;color:#667189;font-size:0.84rem;line-height:1.65;}'
      + '#content-shanjian-digital-human .shanjian-selected-avatar-cover{position:relative;aspect-ratio:1/1;border-radius:14px;overflow:hidden;background:linear-gradient(135deg, rgba(124,94,255,0.22), rgba(14,165,233,0.22));}'
      + '#content-shanjian-digital-human .shanjian-selected-voice-cover{position:relative;aspect-ratio:1/1;border-radius:14px;overflow:hidden;background:linear-gradient(135deg, rgba(124,94,255,0.18), rgba(14,165,233,0.18));}'
      + '#content-shanjian-digital-human .shanjian-selected-template-cover{position:relative;aspect-ratio:3/4;border-radius:14px;overflow:hidden;background:linear-gradient(135deg, rgba(124,94,255,0.18), rgba(14,165,233,0.18));}'
      + '#content-shanjian-digital-human .shanjian-selected-avatar-cover img,#content-shanjian-digital-human .shanjian-avatar-card-cover img,#content-shanjian-digital-human .shanjian-success-visual-media{width:100%;height:100%;object-fit:cover;display:block;}'
      + '#content-shanjian-digital-human .shanjian-selected-avatar-cover video,#content-shanjian-digital-human .shanjian-avatar-card-cover video{width:100%;height:100%;object-fit:cover;display:block;background:#0f172a;}'
      + '#content-shanjian-digital-human .shanjian-selected-voice-cover img,#content-shanjian-digital-human .shanjian-voice-card-thumb img,#content-shanjian-digital-human .shanjian-selected-template-cover img,#content-shanjian-digital-human .shanjian-template-card-cover img{width:100%;height:100%;object-fit:cover;display:block;}'
      + '#content-shanjian-digital-human .shanjian-template-cover-placeholder{width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg, rgba(124,94,255,0.14), rgba(14,165,233,0.16));color:#5f55d8;font-size:1rem;font-weight:800;}'
      + '#content-shanjian-digital-human .shanjian-selected-copy{min-width:0;}'
      + '#content-shanjian-digital-human .shanjian-selected-title-row{display:flex;gap:0.6rem;justify-content:space-between;align-items:flex-start;}'
      + '#content-shanjian-digital-human .shanjian-selected-title-row strong,#content-shanjian-digital-human .shanjian-selected-voice-head strong{font-size:0.9rem;color:#1f2b42;line-height:1.35;}'
      + '#content-shanjian-digital-human .shanjian-selected-title-row span{font-size:0.76rem;color:#7c5eff;padding:0.35rem 0.62rem;border-radius:999px;background:rgba(124,94,255,0.10);font-weight:700;white-space:nowrap;}'
      + '#content-shanjian-digital-human .shanjian-selected-style-label{margin-top:0.45rem;font-size:0.82rem;color:#4c5a74;font-weight:600;}'
      + '#content-shanjian-digital-human .shanjian-selected-tags{margin-top:0.46rem;display:flex;gap:0.36rem;flex-wrap:wrap;}'
      + '#content-shanjian-digital-human .shanjian-mini-tag{display:inline-flex;align-items:center;padding:0.28rem 0.56rem;border-radius:999px;background:rgba(19,191,159,0.10);color:#157a66;font-size:0.74rem;font-weight:600;}'
      + '#content-shanjian-digital-human .shanjian-selected-voice-head{display:flex;justify-content:space-between;gap:0.6rem;align-items:flex-start;}'
      + '#content-shanjian-digital-human .shanjian-selected-audio{margin-top:0.5rem;}'
      + '#content-shanjian-digital-human .shanjian-selected-audio audio,#content-shanjian-digital-human .shanjian-voice-audio audio,#content-shanjian-digital-human .shanjian-success-audio audio{width:100%;}'
      + '#content-shanjian-digital-human .shanjian-preview-play-btn{border:none;width:100%;min-height:42px;border-radius:999px;background:rgba(15,23,42,0.04);color:#25324a;display:inline-flex;align-items:center;justify-content:center;gap:0.5rem;font-size:0.86rem;font-weight:700;cursor:pointer;transition:all 0.18s ease;}'
      + '#content-shanjian-digital-human .shanjian-preview-play-btn:hover{background:rgba(124,94,255,0.10);color:#5d4cdc;}'
      + '#content-shanjian-digital-human .shanjian-preview-play-btn.is-playing{background:rgba(124,94,255,0.14);color:#5d4cdc;box-shadow:inset 0 0 0 1px rgba(124,94,255,0.18);}'
      + '#content-shanjian-digital-human .shanjian-preview-play-icon{width:22px;height:22px;border-radius:999px;background:rgba(124,94,255,0.14);display:inline-flex;align-items:center;justify-content:center;font-size:0.72rem;line-height:1;}'
      + '#content-shanjian-digital-human .shanjian-audio-empty{padding:0.55rem 0.65rem;border-radius:14px;background:rgba(15,23,42,0.05);color:#738199;font-size:0.8rem;}'
      + '#content-shanjian-digital-human .shanjian-main-toolbar{display:flex;align-items:center;justify-content:space-between;gap:0.8rem;margin-bottom:0.8rem;flex-wrap:wrap;}'
      + '#content-shanjian-digital-human .shanjian-tab-row{display:flex;gap:0.65rem;flex-wrap:wrap;}'
      + '#content-shanjian-digital-human .shanjian-tab-btn{border:none;padding:0.58rem 0.98rem;border-radius:999px;background:rgba(124,94,255,0.08);color:#665a92;font-weight:700;cursor:pointer;transition:all 0.18s ease;}'
      + '#content-shanjian-digital-human .shanjian-tab-btn.is-active{background:linear-gradient(135deg,#7c5eff,#6a8cff);color:#fff;box-shadow:0 14px 30px rgba(108,99,255,0.22);}'
      + '#content-shanjian-digital-human .shanjian-library-toolbar{display:flex;gap:0.8rem;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:1rem;}'
      + '#content-shanjian-digital-human .shanjian-search{flex:1 1 260px;min-width:220px;border:1px solid rgba(124,94,255,0.16);border-radius:14px;padding:0.82rem 0.9rem;background:#fff;}'
      + '#content-shanjian-digital-human .shanjian-library-tip{font-size:0.82rem;color:#68748f;line-height:1.65;padding:0.8rem 0.92rem;border-radius:16px;background:linear-gradient(180deg, rgba(255,255,255,0.94), rgba(246,248,255,0.92));border:1px solid rgba(124,94,255,0.10);margin-bottom:1rem;white-space:normal;}'
      + '#content-shanjian-digital-human .shanjian-library-section + .shanjian-library-section{margin-top:1.2rem;}'
      + '#content-shanjian-digital-human .shanjian-section-head{display:flex;align-items:center;justify-content:space-between;gap:0.75rem;margin-bottom:0.82rem;flex-wrap:wrap;}'
      + '#content-shanjian-digital-human .shanjian-section-head h4{margin:0;font-size:1.08rem;color:#1f2b42;}'
      + '#content-shanjian-digital-human .shanjian-section-head p{margin:0.18rem 0 0;font-size:0.8rem;color:#6d7791;}'
      + '#content-shanjian-digital-human .shanjian-section-count{display:inline-flex;align-items:center;gap:0.32rem;padding:0.38rem 0.72rem;border-radius:999px;background:rgba(124,94,255,0.10);color:#6a54e0;font-size:0.78rem;font-weight:700;}'
      + '#content-shanjian-digital-human .shanjian-avatar-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:0.92rem;}'
      + '#content-shanjian-digital-human .shanjian-voice-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:0.92rem;}'
      + '#content-shanjian-digital-human .shanjian-template-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:0.92rem;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card,#content-shanjian-digital-human .shanjian-voice-card{display:flex;flex-direction:column;border-radius:24px;background:#fff;overflow:hidden;border:1px solid rgba(26,39,68,0.06);box-shadow:0 18px 38px rgba(26,39,68,0.08);transition:transform 0.18s ease,box-shadow 0.18s ease,border-color 0.18s ease;}'
      + '#content-shanjian-digital-human .shanjian-template-card{display:flex;flex-direction:column;border-radius:24px;background:#fff;overflow:hidden;border:1px solid rgba(26,39,68,0.06);box-shadow:0 18px 38px rgba(26,39,68,0.08);transition:transform 0.18s ease,box-shadow 0.18s ease,border-color 0.18s ease;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card:hover,#content-shanjian-digital-human .shanjian-voice-card:hover{transform:translateY(-2px);box-shadow:0 20px 44px rgba(31,41,55,0.12);}'
      + '#content-shanjian-digital-human .shanjian-template-card:hover{transform:translateY(-2px);box-shadow:0 20px 44px rgba(31,41,55,0.12);}'
      + '#content-shanjian-digital-human .shanjian-avatar-card.is-selected,#content-shanjian-digital-human .shanjian-voice-card.is-selected{border-color:rgba(124,94,255,0.38);box-shadow:0 24px 50px rgba(108,99,255,0.16);}'
      + '#content-shanjian-digital-human .shanjian-template-card.is-selected{border-color:rgba(124,94,255,0.38);box-shadow:0 24px 50px rgba(108,99,255,0.16);}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-cover{position:relative;aspect-ratio:1/1;overflow:hidden;background:linear-gradient(135deg, rgba(124,94,255,0.20), rgba(14,165,233,0.20));}'
      + '#content-shanjian-digital-human .shanjian-template-card-cover{position:relative;aspect-ratio:3/4;overflow:hidden;background:linear-gradient(135deg, rgba(124,94,255,0.14), rgba(14,165,233,0.14));}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-badge,#content-shanjian-digital-human .shanjian-avatar-card-count,#content-shanjian-digital-human .shanjian-section-chip{position:absolute;display:inline-flex;align-items:center;padding:0.3rem 0.62rem;border-radius:999px;font-size:0.73rem;font-weight:700;backdrop-filter:blur(8px);}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-badge{left:0.72rem;top:0.72rem;background:rgba(255,255,255,0.92);color:#6a54e0;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-count{right:0.72rem;bottom:0.72rem;background:rgba(255,255,255,0.94);color:#6a54e0;}'
      + '#content-shanjian-digital-human .shanjian-section-chip{right:0.65rem;top:0.65rem;color:#6b4de6;background:rgba(255,255,255,0.92);position:absolute;}'
      + '#content-shanjian-digital-human .shanjian-section-chip.is-inline{position:static;white-space:nowrap;padding:0.28rem 0.56rem;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-body,#content-shanjian-digital-human .shanjian-voice-card{padding:0.9rem;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-body{display:flex;flex-direction:column;gap:0.6rem;flex:1;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-main{display:flex;flex-direction:column;gap:0.5rem;min-height:0;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-title,#content-shanjian-digital-human .shanjian-voice-card-title{font-size:1rem;font-weight:700;color:#202a3f;line-height:1.4;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-title{min-height:2.8em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-id,#content-shanjian-digital-human .shanjian-voice-card-id{margin-top:0.38rem;font-size:0.76rem;color:#7c879f;word-break:break-all;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-tags{display:flex;gap:0.38rem;flex-wrap:wrap;min-height:1.8rem;align-items:flex-start;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-meta{display:flex;align-items:center;justify-content:flex-start;font-size:0.8rem;color:#6b7280;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-actions{display:grid;grid-template-columns:1fr 1fr;gap:0.55rem;margin-top:auto;}'
      + '#content-shanjian-digital-human .shanjian-avatar-delete-btn{grid-column:1/-1;justify-content:center;color:#b42318;border-color:rgba(180,35,24,0.22);}'
      + '#content-shanjian-digital-human .shanjian-avatar-delete-btn:hover{background:rgba(180,35,24,0.08);}'
      + '#content-shanjian-digital-human .shanjian-card-tag{display:inline-flex;align-items:center;padding:0.24rem 0.5rem;border-radius:999px;background:rgba(15,23,42,0.06);color:#61708a;font-size:0.72rem;}'
      + '#content-shanjian-digital-human .shanjian-avatar-pick-btn,#content-shanjian-digital-human .shanjian-voice-pick-btn{width:100%;margin-top:0.75rem;justify-content:center;}'
      + '#content-shanjian-digital-human .shanjian-avatar-card-actions .shanjian-avatar-pick-btn{margin-top:0;}'
      + '#content-shanjian-digital-human .shanjian-voice-card-actions{display:grid;grid-template-columns:1fr;gap:0.5rem;margin-top:0.72rem;}'
      + '#content-shanjian-digital-human .shanjian-voice-card-actions .shanjian-voice-pick-btn{margin-top:0;}'
      + '#content-shanjian-digital-human .shanjian-voice-delete-btn{justify-content:center;color:#b42318;border-color:rgba(180,35,24,0.22);}'
      + '#content-shanjian-digital-human .shanjian-voice-delete-btn:hover{background:rgba(180,35,24,0.08);}'
      + '#content-shanjian-digital-human .shanjian-voice-card-top{display:grid;grid-template-columns:74px minmax(0,1fr);gap:0.82rem;align-items:start;}'
      + '#content-shanjian-digital-human .shanjian-voice-card-thumb{width:74px;height:74px;border-radius:20px;overflow:hidden;background:linear-gradient(135deg, rgba(124,94,255,0.18), rgba(14,165,233,0.18));box-shadow:inset 0 0 0 1px rgba(255,255,255,0.32);}'
      + '#content-shanjian-digital-human .shanjian-template-card-body{display:flex;flex-direction:column;gap:0.45rem;padding:0.78rem 0.82rem 0.86rem;min-width:0;}'
      + '#content-shanjian-digital-human .shanjian-template-card-title{font-size:0.92rem;font-weight:700;color:#1f2b42;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
      + '#content-shanjian-digital-human .shanjian-template-card-meta{display:grid;gap:0.18rem;font-size:0.76rem;color:#667189;word-break:break-all;}'
      + '#content-shanjian-digital-human .shanjian-template-card-actions{display:grid;grid-template-columns:1fr auto;gap:0.5rem;align-items:center;margin-top:0.1rem;}'
      + '#content-shanjian-digital-human .shanjian-template-demo-empty{font-size:0.74rem;color:#98a1b2;}'
      + '#content-shanjian-digital-human .shanjian-selected-template-actions{margin-top:0.55rem;}'
      + '#content-shanjian-digital-human .shanjian-template-mode-hint{margin:0 0 0.7rem;padding:0.75rem 0.82rem;border-radius:14px;background:rgba(124,94,255,0.07);color:#5c6682;font-size:0.82rem;line-height:1.6;border:1px solid rgba(124,94,255,0.10);}'
      + '#content-shanjian-digital-human .shanjian-template-param-panel{margin-top:0.8rem;padding:0.82rem;border-radius:18px;background:linear-gradient(180deg, rgba(255,255,255,0.96), rgba(246,248,255,0.94));border:1px solid rgba(124,94,255,0.12);}'
      + '#content-shanjian-digital-human .shanjian-template-param-head{margin-bottom:0.72rem;}'
      + '#content-shanjian-digital-human .shanjian-template-param-head strong{display:block;font-size:0.9rem;color:#1f2b42;}'
      + '#content-shanjian-digital-human .shanjian-template-param-head span{display:block;margin-top:0.24rem;font-size:0.8rem;color:#69758f;line-height:1.55;}'
      + '#content-shanjian-digital-human .shanjian-template-switch-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0.55rem;margin-bottom:0.8rem;}'
      + '#content-shanjian-digital-human .shanjian-template-switch{display:flex;align-items:center;gap:0.48rem;padding:0.62rem 0.68rem;border-radius:14px;background:#fff;border:1px solid rgba(15,23,42,0.07);font-size:0.82rem;font-weight:600;color:#334155;}'
      + '#content-shanjian-digital-human .shanjian-template-switch input{width:auto;}'
      + '#content-shanjian-digital-human .shanjian-voice-card-main{min-width:0;}'
      + '#content-shanjian-digital-human .shanjian-voice-card-head{display:flex;justify-content:space-between;gap:0.7rem;align-items:flex-start;}'
      + '#content-shanjian-digital-human .shanjian-voice-style-list{margin-top:0.75rem;display:flex;flex-direction:column;gap:0.48rem;}'
      + '#content-shanjian-digital-human .shanjian-voice-style-btn{display:flex;align-items:center;justify-content:space-between;gap:0.75rem;width:100%;padding:0.68rem 0.78rem;border:none;border-radius:16px;background:rgba(15,23,42,0.04);cursor:pointer;text-align:left;transition:all 0.18s ease;}'
      + '#content-shanjian-digital-human .shanjian-voice-style-btn:hover{background:rgba(124,94,255,0.08);}'
      + '#content-shanjian-digital-human .shanjian-voice-style-btn.is-active{background:rgba(124,94,255,0.12);box-shadow:inset 0 0 0 1px rgba(124,94,255,0.18);}'
      + '#content-shanjian-digital-human .shanjian-voice-style-copy{display:flex;align-items:center;gap:0.55rem;min-width:0;}'
      + '#content-shanjian-digital-human .shanjian-voice-style-play{width:18px;height:18px;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;background:rgba(124,94,255,0.14);color:#6a54e0;font-size:0.72rem;font-weight:700;flex:0 0 auto;}'
      + '#content-shanjian-digital-human .shanjian-voice-style-text{min-width:0;font-size:0.84rem;color:#25324a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
      + '#content-shanjian-digital-human .shanjian-voice-style-state{font-size:0.72rem;color:#6d7791;white-space:nowrap;}'
      + '#content-shanjian-digital-human .shanjian-voice-audio{margin-top:0.75rem;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-panel{margin-top:0.72rem;padding:0.7rem;border-radius:14px;background:linear-gradient(180deg,rgba(248,250,255,0.92),rgba(255,255,255,0.98));border:1px solid rgba(124,94,255,0.10);}'
      + '#content-shanjian-digital-human .shanjian-voice-param-head{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:start;gap:0.58rem;margin-bottom:0.62rem;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-title{display:flex;flex-direction:column;gap:0.14rem;min-width:0;line-height:1.25;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-title strong{font-size:0.82rem;color:#27324a;white-space:nowrap;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-title span{font-size:0.68rem;color:#8a94a8;white-space:normal;overflow-wrap:anywhere;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-status{justify-self:end;align-self:start;min-height:1.1rem;font-size:0.68rem;color:#8a94a8;white-space:nowrap;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-status[data-tone="busy"]{color:#6a54e0;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-status[data-tone="ok"]{color:#158467;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-status[data-tone="danger"]{color:#b42318;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0.42rem;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-grid label{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:0.34rem;padding:0.32rem 0.42rem;border-radius:12px;background:#fff;border:1px solid rgba(15,23,42,0.07);}'
      + '#content-shanjian-digital-human .shanjian-voice-param-grid label span{font-size:0.7rem;font-weight:700;color:#748096;white-space:nowrap;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-grid input{width:100%;min-width:0;box-sizing:border-box;border:none;border-radius:8px;padding:0.22rem 0.1rem;font-size:0.86rem;font-weight:700;color:#27324a;background:transparent;text-align:right;outline:none;}'
      + '#content-shanjian-digital-human .shanjian-voice-param-grid input:focus{box-shadow:0 0 0 2px rgba(124,94,255,0.14);background:rgba(124,94,255,0.045);}'
      + '#content-shanjian-digital-human .shanjian-voice-param-grid input::-webkit-outer-spin-button,#content-shanjian-digital-human .shanjian-voice-param-grid input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0;}'
      + '@media (max-width:980px){#content-shanjian-digital-human .shanjian-voice-param-head{grid-template-columns:minmax(0,1fr);}}'
      + '.shanjian-voice-param-popover{position:fixed;z-index:2600;width:236px;padding:0.82rem;border-radius:16px;background:#fff;border:1px solid rgba(124,94,255,0.16);box-shadow:0 22px 52px rgba(31,41,55,0.18);}'
      + '.shanjian-param-popover-head{display:flex;align-items:center;justify-content:space-between;gap:0.75rem;margin-bottom:0.72rem;}'
      + '.shanjian-param-popover-head strong{font-size:0.86rem;color:#26334d;}'
      + '.shanjian-param-popover-head span{min-width:2.6rem;text-align:center;padding:0.22rem 0.42rem;border-radius:999px;background:rgba(124,94,255,0.10);color:#5d4cdc;font-size:0.82rem;font-weight:800;}'
      + '.shanjian-param-slider{width:100%;accent-color:#7c5eff;}'
      + '.shanjian-param-slider-scale{display:flex;justify-content:space-between;margin-top:0.32rem;color:#8a94a8;font-size:0.72rem;font-weight:700;}'
      + '#content-shanjian-digital-human .shanjian-modal-subtitle{margin:0.24rem 0 0;color:#7b8498;font-size:0.82rem;}'
      + '#content-shanjian-digital-human .shanjian-section-empty{display:none;padding:1rem 1.05rem;border-radius:18px;border:1px dashed rgba(124,94,255,0.20);background:rgba(249,247,255,0.82);color:#68748f;font-size:0.84rem;line-height:1.7;}'
      + '#content-shanjian-digital-human .shanjian-more-row{margin-top:0.92rem;display:flex;justify-content:center;}'
      + '#content-shanjian-digital-human .shanjian-result-panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap;margin-bottom:0.85rem;}'
      + '#content-shanjian-digital-human .shanjian-result-meta{display:flex;flex-wrap:wrap;gap:0.65rem;margin-top:0.58rem;}'
      + '#content-shanjian-digital-human .shanjian-result-pill{display:inline-flex;align-items:center;gap:0.38rem;padding:0.42rem 0.74rem;border-radius:999px;background:rgba(124,94,255,0.10);color:#6a54e0;font-size:0.78rem;font-weight:700;}'
      + '#content-shanjian-digital-human #shanjianTaskStatusText[data-tone="success"]{color:#1f8f5f;background:rgba(31,143,95,0.12);}'
      + '#content-shanjian-digital-human #shanjianTaskStatusText[data-tone="danger"]{color:#c23b3b;background:rgba(194,59,59,0.12);}'
      + '#content-shanjian-digital-human #shanjianTaskStatusText[data-tone="processing"]{color:#5c57d8;background:rgba(92,87,216,0.12);}'
      + '#content-shanjian-digital-human .shanjian-result-status-head{display:flex;gap:0.74rem;align-items:flex-start;}'
      + '#content-shanjian-digital-human .shanjian-result-spinner{flex:0 0 auto;width:34px;height:34px;border-radius:999px;border:3px solid rgba(124,94,255,0.14);border-top-color:#7c5eff;animation:viral-spin 0.85s linear infinite;}'
      + '#content-shanjian-digital-human .shanjian-video-result-wrap{width:100%;height:100%;display:flex;flex-direction:column;gap:0.75rem;}'
      + '#content-shanjian-digital-human .shanjian-video-result-wrap video{min-height:320px;}'
      + '#content-shanjian-digital-human .shanjian-video-result-actions{display:flex;gap:0.55rem;flex-wrap:wrap;justify-content:flex-end;}'
      + '#content-shanjian-digital-human .shanjian-success-card{width:min(92%,640px);padding:1.2rem 1.25rem;border-radius:22px;background:#fff;border:1px solid rgba(124,94,255,0.14);box-shadow:0 18px 42px rgba(31,41,55,0.10);}'
      + '#content-shanjian-digital-human .shanjian-success-card strong{display:block;font-size:1.04rem;color:#20314d;}'
      + '#content-shanjian-digital-human .shanjian-success-card span{display:block;margin-top:0.5rem;color:#5f6f87;line-height:1.65;}'
      + '#content-shanjian-digital-human .shanjian-success-meta{margin-top:0.8rem;padding:0.72rem 0.84rem;border-radius:14px;background:rgba(124,94,255,0.06);color:#5d4cdc;font-size:0.83rem;word-break:break-all;}'
      + '#content-shanjian-digital-human .shanjian-success-visual{margin-top:0.9rem;border-radius:18px;overflow:hidden;aspect-ratio:1/1;background:linear-gradient(135deg, rgba(124,94,255,0.18), rgba(14,165,233,0.18));}'
      + '#content-shanjian-digital-human .shanjian-success-visual video{width:100%;height:100%;object-fit:cover;display:block;background:#0f172a;}'
      + '#content-shanjian-digital-human .shanjian-modal{position:fixed;inset:0;z-index:1200;}'
      + '#content-shanjian-digital-human .shanjian-modal-backdrop{position:absolute;inset:0;background:rgba(15,23,42,0.58);backdrop-filter:blur(3px);}'
      + '#content-shanjian-digital-human .shanjian-modal-card{position:relative;width:min(92vw,760px);max-height:92vh;overflow:auto;margin:4vh auto 0;border-radius:28px;background:#fff;box-shadow:0 32px 80px rgba(15,23,42,0.28);}'
      + '#content-shanjian-digital-human .shanjian-mode-card{width:min(92vw,720px);}'
      + '#content-shanjian-digital-human .shanjian-modal-head{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:1.15rem 1.25rem;border-bottom:1px solid rgba(15,23,42,0.08);}'
      + '#content-shanjian-digital-human .shanjian-modal-title{margin:0;font-size:1.55rem;color:#111827;}'
      + '#content-shanjian-digital-human .shanjian-modal-close{border:none;background:transparent;font-size:2rem;line-height:1;color:#64748b;cursor:pointer;}'
      + '#content-shanjian-digital-human .shanjian-modal-body{padding:1.25rem;display:flex;flex-direction:column;gap:1rem;}'
      + '#content-shanjian-digital-human .shanjian-modal-foot{display:flex;justify-content:flex-end;gap:0.75rem;padding:1rem 1.25rem 1.25rem;border-top:1px solid rgba(15,23,42,0.08);}'
      + '#content-shanjian-digital-human .shanjian-confirm-modal{z-index:2200;}'
      + '#content-shanjian-digital-human .shanjian-confirm-card{width:min(92vw,440px);margin:18vh auto 0;border-radius:20px;}'
      + '#content-shanjian-digital-human .shanjian-confirm-card .shanjian-modal-title{font-size:1.18rem;}'
      + '#content-shanjian-digital-human .shanjian-confirm-copy{margin:0;color:#344054;line-height:1.7;font-size:0.95rem;}'
      + '#content-shanjian-digital-human .shanjian-confirm-danger{background:#d92d20;border-color:#d92d20;}'
      + '#content-shanjian-digital-human .shanjian-confirm-danger:hover{background:#b42318;border-color:#b42318;}'
      + '#content-shanjian-digital-human .shanjian-mode-list{display:flex;flex-direction:column;gap:1rem;padding:1.25rem;}'
      + '#content-shanjian-digital-human .shanjian-mode-option{display:flex;align-items:center;gap:1rem;padding:1.2rem 1.25rem;border-radius:24px;border:1px solid rgba(15,23,42,0.08);background:#fff;cursor:pointer;text-align:left;box-shadow:0 12px 30px rgba(15,23,42,0.06);}'
      + '#content-shanjian-digital-human .shanjian-mode-option:hover{transform:translateY(-1px);box-shadow:0 16px 34px rgba(15,23,42,0.10);}'
      + '#content-shanjian-digital-human .shanjian-mode-icon{width:72px;height:72px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#6327ff,#c30099);color:#fff;font-weight:800;font-size:1.2rem;flex:0 0 auto;}'
      + '#content-shanjian-digital-human .shanjian-mode-copy{display:flex;flex-direction:column;gap:0.28rem;min-width:0;}'
      + '#content-shanjian-digital-human .shanjian-mode-copy strong{font-size:1rem;color:#111827;}'
      + '#content-shanjian-digital-human .shanjian-mode-copy span{color:#4b5563;line-height:1.55;}'
      + '#content-shanjian-digital-human .shanjian-recommend-chip{margin-left:auto;display:inline-flex;align-items:center;padding:0.42rem 0.82rem;border-radius:999px;background:rgba(124,94,255,0.12);color:#7c3aed;font-weight:700;white-space:nowrap;}'
      + '#content-shanjian-digital-human .shanjian-requirement-box{padding:1rem;border-radius:18px;background:rgba(244,247,255,0.88);border:1px solid rgba(124,94,255,0.10);}'
      + '#content-shanjian-digital-human .shanjian-requirement-box strong{display:block;color:#1f2b42;}'
      + '#content-shanjian-digital-human .shanjian-requirement-grid{margin-top:0.7rem;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0.75rem;color:#4b5563;font-size:0.92rem;}'
      + '#content-shanjian-digital-human .shanjian-requirement-list{margin:0.7rem 0 0;padding-left:1.2rem;color:#4b5563;display:grid;gap:0.55rem;line-height:1.65;}'
      + '#content-shanjian-digital-human .shanjian-upload-box{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0.42rem;min-height:220px;padding:1rem;border-radius:22px;border:1px dashed rgba(124,94,255,0.25);background:linear-gradient(180deg, rgba(255,255,255,0.98), rgba(248,246,255,0.92));cursor:pointer;}'
      + '#content-shanjian-digital-human .shanjian-upload-box.is-dragover{border-color:#7c5eff;background:rgba(124,94,255,0.06);}'
      + '#content-shanjian-digital-human .shanjian-upload-box strong{font-size:1.02rem;color:#1f2b42;}'
      + '#content-shanjian-digital-human .shanjian-upload-box span{color:#667189;line-height:1.6;}'
      + '#content-shanjian-digital-human .shanjian-upload-icon{width:56px;height:56px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;background:rgba(124,94,255,0.12);color:#7c5eff;font-size:1.55rem;font-weight:800;}'
      + '#content-shanjian-digital-human .shanjian-upload-preview{display:none;border-radius:22px;background:#f5f7fb;border:1px solid rgba(124,94,255,0.10);overflow:hidden;}'
      + '#content-shanjian-digital-human .shanjian-upload-preview-inner{position:relative;min-height:240px;display:flex;align-items:center;justify-content:center;background:linear-gradient(180deg, rgba(248,250,255,0.96), rgba(239,243,255,0.92));padding:1rem;}'
      + '#content-shanjian-digital-human .shanjian-upload-preview-inner img{max-width:100%;max-height:480px;display:block;border-radius:18px;object-fit:contain;box-shadow:0 18px 42px rgba(15,23,42,0.10);}'
      + '#content-shanjian-digital-human .shanjian-upload-preview-inner video{width:100%;max-height:420px;border-radius:18px;background:#0f172a;box-shadow:0 18px 42px rgba(15,23,42,0.14);}'
      + '#content-shanjian-digital-human .shanjian-upload-preview-inner audio{width:min(520px,100%);}'
      + '#content-shanjian-digital-human .shanjian-upload-preview-inner-image{padding:1.2rem;background:#f3f6fb;}'
      + '#content-shanjian-digital-human .shanjian-upload-preview-inner-video{padding:1rem;background:#eef2ff;}'
      + '#content-shanjian-digital-human .shanjian-upload-preview-inner-audio{min-height:120px;padding:1.2rem 1rem;background:#f8fafc;}'
      + '#content-shanjian-digital-human .shanjian-upload-remove{position:absolute;top:0.9rem;right:0.9rem;width:42px;height:42px;border:none;border-radius:14px;background:rgba(255,255,255,0.96);color:#334155;font-size:1.4rem;line-height:1;cursor:pointer;box-shadow:0 12px 28px rgba(15,23,42,0.12);}'
      + '#content-shanjian-digital-human .shanjian-upload-remove:hover{background:#fff;color:#111827;}'
      + '#content-shanjian-digital-human .shanjian-file-meta{display:flex;justify-content:space-between;gap:0.75rem;padding:0.78rem 0.9rem;border-radius:16px;background:rgba(15,23,42,0.05);color:#334155;word-break:break-all;}'
      + '#content-shanjian-digital-human .shanjian-file-meta.is-error{display:block;background:rgba(220,38,38,0.08);border:1px solid rgba(220,38,38,0.24);color:#b42318;line-height:1.55;}'
      + '#content-shanjian-digital-human .shanjian-radio-row{display:flex;gap:0.7rem;flex-wrap:wrap;}'
      + '#content-shanjian-digital-human .shanjian-radio-card{display:inline-flex;align-items:center;gap:0.45rem;padding:0.65rem 0.82rem;border-radius:999px;border:1px solid rgba(124,94,255,0.16);cursor:pointer;}'
      + 'body.shanjian-modal-open{overflow:hidden;}'
      + '@media (max-width:1120px){#content-shanjian-digital-human .shanjian-shell{grid-template-columns:minmax(0,1fr);height:auto;max-height:none;overflow:visible;}#content-shanjian-digital-human .shanjian-sidebar,#content-shanjian-digital-human .shanjian-main{position:relative;max-height:none;overflow:visible;padding-right:0;}}'
      + '@media (max-width:720px){#content-shanjian-digital-human .shanjian-selected-avatar,#content-shanjian-digital-human .shanjian-selected-voice,#content-shanjian-digital-human .shanjian-selected-template{grid-template-columns:minmax(0,1fr);}#content-shanjian-digital-human .shanjian-avatar-grid,#content-shanjian-digital-human .shanjian-voice-grid,#content-shanjian-digital-human .shanjian-template-grid{grid-template-columns:minmax(0,1fr);}#content-shanjian-digital-human .shanjian-template-switch-grid,#content-shanjian-digital-human .shanjian-requirement-grid{grid-template-columns:minmax(0,1fr);}#content-shanjian-digital-human .shanjian-modal-card{width:min(96vw,760px);}}';
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
            <button type="button" id="shanjianBackBtn" class="btn btn-ghost btn-sm">返回技能商店</button>
          </div>
        </div>
        <div class="shanjian-shell">
          <aside class="shanjian-sidebar">
            <div class="tvc-panel shanjian-submit-panel">
              <h4 class="tvc-panel-title">1. 提交任务</h4>
              <div class="tvc-field">
                <label for="shanjianTitleInput">作品标题</label>
                <input id="shanjianTitleInput" type="text" maxlength="20" placeholder="数字人口播">
              </div>
              <div class="tvc-field">
                <label>提交模式</label>
                <div class="shanjian-mode-switch">
                  <button type="button" id="shanjianVideoModeTtsBtn" class="shanjian-mode-chip is-active">文案驱动</button>
                  <button type="button" id="shanjianVideoModeAudioBtn" class="shanjian-mode-chip">声音驱动</button>
                </div>
                <p class="tvc-panel-hint" style="margin-top:0.55rem;">文案驱动会使用你当前选中的声音；声音驱动会直接使用你上传的音频来驱动数字人。</p>
              </div>
              <div id="shanjianVideoTtsFields">
                <div class="tvc-field">
                  <label for="shanjianScriptInput">口播文案</label>
                  <textarea id="shanjianScriptInput" class="shanjian-script-input" placeholder="输入数字人要说的文案，不超过 10000 字。"></textarea>
                </div>
              </div>
              <div id="shanjianVideoAudioFields" style="display:none;">
                <div class="tvc-field">
                  <label>驱动音频</label>
                  <div id="shanjianAudioDriveUploadBox" class="shanjian-upload-box" tabindex="0" role="button">
                    <div class="shanjian-upload-icon">♪</div>
                    <div class="shanjian-upload-copy">
                      <strong>点击或拖拽上传音频</strong>
                      <span>支持 mp3、m4a、wav，大小 100MB 以内，建议 5 秒到 30 分钟。</span>
                    </div>
                  </div>
                  <input id="shanjianAudioDriveFile" type="file" accept=".mp3,.m4a,.wav,audio/mpeg,audio/mp4,audio/wav" style="display:none;">
                  <div id="shanjianAudioDrivePreview" class="shanjian-upload-preview"></div>
                  <div id="shanjianAudioDriveFileMeta" class="shanjian-file-meta" style="display:none;"></div>
                </div>
              </div>
              <p id="shanjianGenerateModeHint" class="tvc-panel-hint" style="margin:0.7rem 0 0;">不选模板时会走普通数字人口播；选了模板后才会切到真人视频模板剪辑。</p>
              <div id="shanjianTemplateSmartParams" class="shanjian-template-param-panel" style="display:none;">
                <div class="shanjian-template-param-head">
                  <strong>模板剪辑参数</strong>
                  <span>只有选中了右侧模板库里的真人视频模板，下面这些智能剪辑参数才会生效。</span>
                </div>
                <div class="shanjian-template-switch-grid">
                  <label class="shanjian-template-switch"><input type="checkbox" id="shanjianTemplateHeaderSwitch" checked><span>片头开场</span></label>
                  <label class="shanjian-template-switch"><input type="checkbox" id="shanjianTemplateMaterialSwitch" checked><span>智能配图</span></label>
                  <label class="shanjian-template-switch"><input type="checkbox" id="shanjianTemplateSubtitleSwitch" checked><span>自动字幕</span></label>
                  <label class="shanjian-template-switch"><input type="checkbox" id="shanjianTemplateKeywordSwitch" checked><span>关键词高亮</span></label>
                </div>
                <div class="tvc-inline-grid">
                  <div class="tvc-field">
                    <label for="shanjianTemplateMatchWaySelect">素材匹配方式</label>
                    <select id="shanjianTemplateMatchWaySelect">
                      <option value="fuzzyMatch" selected>模糊匹配</option>
                      <option value="preciseMatch">精准匹配</option>
                    </select>
                  </div>
                  <div class="tvc-field">
                    <label class="shanjian-inline-check" for="shanjianTemplateMaterialSoundSwitch" style="margin-top:1.6rem;">
                      <input type="checkbox" id="shanjianTemplateMaterialSoundSwitch">
                      <span>保留素材原声</span>
                    </label>
                  </div>
                </div>
              </div>
              <div id="shanjianMsg" class="msg" style="display:none;margin-top:0.8rem;"></div>
              <div class="tvc-quick-actions">
                <button type="button" id="shanjianGenerateBtn" class="btn btn-primary">提交生成任务</button>
              </div>
            </div>

            <div class="tvc-panel shanjian-selection-panel">
              <h4 class="tvc-panel-title">2. 已选数字人、声音与模板</h4>
              <p class="tvc-panel-hint">先在右侧资源库选择数字人、声音和模板，再回到这里提交口播任务。</p>
              <div class="shanjian-compact-selection-block">
                <div>
                  <div class="shanjian-compact-selection-head">
                    <div>
                      <strong>当前数字人</strong>
                      <span>用于生成画面的数字人形象</span>
                    </div>
                    <button type="button" id="shanjianOpenAvatarLibraryBtn" class="btn btn-ghost btn-sm">选择数字人</button>
                  </div>
                  <div id="shanjianSelectedAvatarPreview" class="shanjian-selected-avatar is-empty"></div>
                  <input id="shanjianAvatarInput" type="hidden">
                </div>
                <div>
                  <div class="shanjian-compact-selection-head">
                    <div>
                      <strong>当前声音</strong>
                      <span>文案驱动时用于合成口播音频</span>
                    </div>
                    <div class="shanjian-head-actions">
                      <button type="button" id="shanjianOpenVoiceLibraryBtn" class="btn btn-ghost btn-sm">选择声音</button>
                      <button type="button" id="shanjianOpenVoiceCreateBtn" class="btn btn-primary btn-sm">创建</button>
                    </div>
                  </div>
                  <p id="shanjianSelectedVoiceModeHint" class="tvc-panel-hint" style="display:none;margin:0 0 0.5rem;">当前处于声音驱动模式，提交任务时会忽略这里已选的声音。</p>
                  <div id="shanjianSelectedVoicePreview" class="shanjian-selected-voice is-empty"></div>
                  <input id="shanjianVoiceInput" type="hidden">
                </div>
                <div>
                  <div class="shanjian-compact-selection-head">
                    <div>
                      <strong>当前模板</strong>
                      <span>不选模板走普通口播，选了模板就走真人视频模板剪辑</span>
                    </div>
                    <div class="shanjian-head-actions">
                      <button type="button" id="shanjianOpenTemplateLibraryBtn" class="btn btn-ghost btn-sm">选择模板</button>
                      <button type="button" id="shanjianOpenMaterialLibraryBtn" class="btn btn-ghost btn-sm">选择素材</button>
                      <button type="button" id="shanjianClearTemplateBtn" class="btn btn-ghost btn-sm">清空</button>
                    </div>
                  </div>
                  <p id="shanjianTemplateModeHint" class="shanjian-template-mode-hint" style="display:none;">当前已经启用模板剪辑，提交后会按模板结构生成真人视频混剪结果。</p>
                  <div id="shanjianSelectedTemplatePreview" class="shanjian-selected-template is-empty"></div>
                  <div class="shanjian-material-strip">
                    <div class="shanjian-material-strip-head">
                      <div>
                        <strong>已选混剪素材</strong>
                        <span>模板混剪会优先使用这里选中的图片和视频素材。</span>
                      </div>
                      <div class="shanjian-head-actions">
                        <button type="button" id="shanjianUploadMaterialBtnInline" class="btn btn-ghost btn-sm">上传素材</button>
                        <button type="button" id="shanjianOpenMaterialLibraryBtnInline" class="btn btn-primary btn-sm">去素材库选择</button>
                        <button type="button" id="shanjianClearMaterialsBtnInline" class="btn btn-ghost btn-sm">清空</button>
                      </div>
                    </div>
                    <input id="shanjianInlineMaterialUploadInput" type="file" accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,image/jpeg,image/png,image/webp,video/mp4,video/quicktime" multiple style="display:none;">
                    <div id="shanjianSelectedMaterialsPreview" class="shanjian-material-list is-empty"></div>
                  </div>
                </div>
              </div>
            </div>

            <div class="tvc-panel tvc-panel-compact shanjian-service-panel">
              <h4 class="tvc-panel-title">3. 必火智能数字人服务</h4>
              <p class="tvc-panel-hint">创作服务由平台统一托管，用户无需填写 API Token。</p>
              <div class="viral-action-row">
                <span class="viral-inline-status">按作品实际时长消耗平台积分</span>
              </div>
            </div>
          </aside>

          <section class="shanjian-main">
            <div class="tvc-panel">
              <div class="shanjian-main-toolbar">
                <div>
                  <h4 class="tvc-panel-title" style="margin-bottom:0.25rem;">资源工作台</h4>
                  <p class="tvc-panel-hint" style="margin:0;">数字人、声音、模板都统一放进资源工作台里，选择后会立刻同步到左侧提交区。</p>
                </div>
                <div class="shanjian-tab-row">
                  <button type="button" id="shanjianAvatarLibraryTabBtn" class="shanjian-tab-btn is-active">数字人库</button>
                  <button type="button" id="shanjianVoiceLibraryTabBtn" class="shanjian-tab-btn">声音库</button>
                  <button type="button" id="shanjianTemplateLibraryTabBtn" class="shanjian-tab-btn">模板库</button>
                  <button type="button" id="shanjianMaterialLibraryTabBtn" class="shanjian-tab-btn">素材库</button>
                  <button type="button" id="shanjianResultTabBtn" class="shanjian-tab-btn">任务结果</button>
                </div>
              </div>

              <div id="shanjianAvatarLibraryView">
                <div class="shanjian-library-toolbar">
                  <input id="shanjianAvatarSearchInput" class="shanjian-search" type="text" placeholder="搜索数字人名称">
                  <div class="shanjian-toolbar-actions">
                    <button type="button" id="shanjianOpenAvatarCreateBtn" class="btn btn-primary btn-sm">创建数字人</button>
                    <button type="button" id="shanjianRefreshLibraryBtn" class="btn btn-ghost btn-sm">刷新数字人</button>
                  </div>
                </div>
                <div class="shanjian-library-tip">创建成功后会自动刷新到“我的数字人”。如果暂时没有看到，请稍后刷新列表。</div>
                <div class="shanjian-library-section">
                  <div class="shanjian-section-head">
                    <div>
                      <h4>我的数字人</h4>
                      <p>这里优先放你自己创建或克隆出来的数字人。</p>
                    </div>
                    <span class="shanjian-section-count"><span id="shanjianMineCount">0</span> 个</span>
                  </div>
                  <div id="shanjianMineAvatarGrid" class="shanjian-avatar-grid"></div>
                  <div id="shanjianMineAvatarEmpty" class="shanjian-section-empty"></div>
                  <div class="shanjian-more-row"><button type="button" id="shanjianMineMoreBtn" class="btn btn-ghost btn-sm" style="display:none;">显示更多</button></div>
                </div>
                <div class="shanjian-library-section" id="shanjianPublicAvatarSection" style="display:none;">
                  <div class="shanjian-section-head">
                    <div>
                      <h4>公共数字人</h4>
                      <p>公共数字人和我的数字人分开展示，方便快速挑选。</p>
                    </div>
                    <span class="shanjian-section-count"><span id="shanjianPublicCount">0</span> 个</span>
                  </div>
                  <div id="shanjianPublicAvatarGrid" class="shanjian-avatar-grid"></div>
                  <div id="shanjianPublicAvatarEmpty" class="shanjian-section-empty"></div>
                  <div class="shanjian-more-row"><button type="button" id="shanjianPublicMoreBtn" class="btn btn-ghost btn-sm" style="display:none;">显示更多</button></div>
                </div>
              </div>

              <div id="shanjianVoiceLibraryView" style="display:none;">
                <div class="shanjian-library-toolbar">
                  <input id="shanjianVoiceSearchInput" class="shanjian-search" type="text" placeholder="搜索声音名称">
                  <div class="shanjian-toolbar-actions">
                    <button type="button" id="shanjianCreateVoiceBtn" class="btn btn-primary btn-sm">创建声音</button>
                    <button type="button" id="shanjianLoadVoicesBtn" class="btn btn-ghost btn-sm">刷新声音</button>
                  </div>
                </div>
                <div class="shanjian-library-section">
                  <div class="shanjian-section-head">
                    <div>
                      <h4>我的声音</h4>
                      <p>优先展示你自己创建的声音，选择后会同步到左侧生成区。</p>
                    </div>
                    <span class="shanjian-section-count"><span id="shanjianMineVoiceCount">0</span> 个</span>
                  </div>
                  <div id="shanjianMineVoiceGrid" class="shanjian-voice-grid"></div>
                  <div id="shanjianMineVoiceEmpty" class="shanjian-section-empty"></div>
                  <div class="shanjian-more-row"><button type="button" id="shanjianMineVoiceMoreBtn" class="btn btn-ghost btn-sm" style="display:none;">显示更多</button></div>
                </div>
                <div class="shanjian-library-section">
                  <div class="shanjian-section-head">
                    <div>
                      <h4>公共声音</h4>
                      <p>公共声音作为预置音色，和我的声音分开管理。</p>
                    </div>
                    <span class="shanjian-section-count"><span id="shanjianPublicVoiceCount">0</span> 个</span>
                  </div>
                  <div id="shanjianPublicVoiceGrid" class="shanjian-voice-grid"></div>
                  <div id="shanjianPublicVoiceEmpty" class="shanjian-section-empty"></div>
                  <div class="shanjian-more-row"><button type="button" id="shanjianPublicVoiceMoreBtn" class="btn btn-ghost btn-sm" style="display:none;">显示更多</button></div>
                </div>
              </div>

              <div id="shanjianTemplateLibraryView" style="display:none;">
                <div class="shanjian-library-toolbar">
                  <input id="shanjianTemplateSearchInput" class="shanjian-search" type="text" placeholder="搜索模板名称">
                  <div class="shanjian-toolbar-actions">
                    <button type="button" id="shanjianRefreshTemplatesBtn" class="btn btn-ghost btn-sm">刷新模板</button>
                  </div>
                </div>
                <div class="shanjian-library-tip">这里展示的是真人视频模板。选中后，左侧提交任务会自动切到真人视频模板剪辑链路。</div>
                <div class="shanjian-library-section">
                  <div class="shanjian-section-head">
                    <div>
                      <h4>真人视频模板</h4>
                      <p>当前只加载支持真人口播剪辑的模板。</p>
                    </div>
                    <span class="shanjian-section-count"><span id="shanjianTemplateCount">0</span> 个</span>
                  </div>
                  <div id="shanjianTemplateGrid" class="shanjian-template-grid"></div>
                  <div id="shanjianTemplateEmpty" class="shanjian-section-empty"></div>
                  <div class="shanjian-more-row"><button type="button" id="shanjianTemplateMoreBtn" class="btn btn-ghost btn-sm" style="display:none;">加载更多模板</button></div>
                </div>
              </div>

              <div id="shanjianMaterialLibraryView" style="display:none;">
                <div class="shanjian-library-toolbar">
                  <input id="shanjianMaterialSearchInput" class="shanjian-search" type="text" placeholder="搜索素材名称、素材ID">
                  <div class="shanjian-toolbar-actions">
                    <button type="button" id="shanjianMaterialReloadBtn" class="btn btn-ghost btn-sm">刷新素材</button>
                  </div>
                </div>
                <div class="shanjian-library-tip">这里展示的是当前用户素材库中的图片和视频素材。可多选，选中后会直接加入当前模板混剪。</div>
                <div class="shanjian-section-head" style="margin-bottom:0.7rem;">
                  <div>
                    <h4>混剪素材库</h4>
                    <p>支持按全部、图片、视频分类筛选。</p>
                  </div>
                  <div class="shanjian-head-actions">
                    <button type="button" class="shanjian-tab-btn is-active" id="shanjianMaterialFilterAllBtn">全部</button>
                    <button type="button" class="shanjian-tab-btn" id="shanjianMaterialFilterImageBtn">图片</button>
                    <button type="button" class="shanjian-tab-btn" id="shanjianMaterialFilterVideoBtn">视频</button>
                    <span class="shanjian-section-count"><span id="shanjianMaterialCount">0</span> 个</span>
                  </div>
                </div>
                <div id="shanjianMaterialGrid" class="shanjian-template-grid"></div>
                <div id="shanjianMaterialEmpty" class="shanjian-section-empty"></div>
              </div>

              <div id="shanjianResultView" style="display:none;">
                <div class="shanjian-result-panel-head">
                  <div>
                    <h4 class="tvc-panel-title" style="margin-bottom:0.25rem;">任务结果</h4>
                    <p class="tvc-panel-hint" style="margin:0;">视频生成、数字人创建、声音创建都会统一在这里显示进度与结果。</p>
                    <div class="shanjian-result-meta">
                      <span class="shanjian-result-pill" id="shanjianTaskStatusText" data-tone="idle">等待提交</span>
                      <span class="shanjian-result-pill" id="shanjianTaskKindText" data-tone="idle">未开始</span>
                    </div>
                  </div>
                  <button type="button" id="shanjianResultBackBtn" class="btn btn-ghost btn-sm">返回资源库</button>
                </div>
                <div class="tvc-video-stage" style="height:clamp(360px, 58vh, 680px);">
                  <div id="shanjianResultSurface" class="tvc-video-surface" style="height:100%;display:flex;align-items:center;justify-content:center;"></div>
                </div>

                <div class="shanjian-library-section" style="margin-top:1rem;">
                  <div class="shanjian-section-head">
                    <div>
                      <h4>历史口播任务</h4>
                      <p>过往的数字人口播任务会自动转存到素材库，过期也能回看与下载。</p>
                    </div>
                    <div style="display:flex;gap:0.5rem;align-items:center;">
                      <span class="shanjian-section-count"><span id="shanjianVideoHistoryCount">0</span> 个</span>
                      <button type="button" id="shanjianVideoHistoryRefreshBtn" class="btn btn-ghost btn-sm">刷新</button>
                    </div>
                  </div>
                  <div id="shanjianVideoHistoryGrid" class="shanjian-video-history-grid"></div>
                  <div id="shanjianVideoHistoryEmpty" class="shanjian-section-empty" style="display:none;">还没有历史口播任务，提交一次后这里会出现。</div>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>

      <div id="shanjianAvatarModeModal" class="shanjian-modal" style="display:none;">
        <div class="shanjian-modal-backdrop" data-modal-close="shanjianAvatarModeModal"></div>
        <div class="shanjian-modal-card shanjian-mode-card" role="dialog" aria-modal="true" aria-labelledby="shanjianAvatarModeTitle">
          <div class="shanjian-modal-head">
            <h4 id="shanjianAvatarModeTitle" class="shanjian-modal-title">模式选择</h4>
            <button type="button" class="shanjian-modal-close" data-modal-close="shanjianAvatarModeModal" aria-label="关闭">×</button>
          </div>
          <div class="shanjian-mode-list">
            <button type="button" class="shanjian-mode-option" data-avatar-mode="image">
              <span class="shanjian-mode-icon">图</span>
              <span class="shanjian-mode-copy">
                <strong>图片生成数字人</strong>
                <span>上传一张图片，快速创建自己的数字人。</span>
              </span>
            </button>
            <button type="button" class="shanjian-mode-option" data-avatar-mode="video">
              <span class="shanjian-mode-icon">视</span>
              <span class="shanjian-mode-copy">
                <strong>视频生成数字人</strong>
                <span>上传一段视频，作为驱动数字人的底版视频。</span>
              </span>
              <span class="shanjian-recommend-chip">推荐</span>
            </button>
          </div>
        </div>
      </div>

      <div id="shanjianAvatarImageModal" class="shanjian-modal" style="display:none;">
        <div class="shanjian-modal-backdrop" data-modal-close="shanjianAvatarImageModal"></div>
        <div class="shanjian-modal-card" role="dialog" aria-modal="true" aria-labelledby="shanjianAvatarImageTitle">
          <div class="shanjian-modal-head">
            <h4 id="shanjianAvatarImageTitle" class="shanjian-modal-title">图片生成数字人</h4>
            <button type="button" class="shanjian-modal-close" data-modal-close="shanjianAvatarImageModal" aria-label="关闭">×</button>
          </div>
          <div class="shanjian-modal-body">
            <div class="tvc-field">
              <label for="shanjianAvatarImageName">数字人名称</label>
              <input id="shanjianAvatarImageName" type="text" maxlength="20" placeholder="输入数字人名称">
            </div>
            <div class="shanjian-requirement-box">
              <strong>图片要求</strong>
              <div class="shanjian-requirement-grid">
                <span>人物：正面、半身</span>
                <span>格式：png / jpg / jpeg</span>
                <span>大小：不超过 10MB</span>
              </div>
            </div>
            <input id="shanjianAvatarImageFile" type="file" accept=".png,.jpg,.jpeg,image/png,image/jpeg" style="display:none;">
            <button type="button" id="shanjianAvatarImageUploadBox" class="shanjian-upload-box">
              <span class="shanjian-upload-icon">↑</span>
              <strong>请上传一张图片，用于生成图片数字人</strong>
              <span>将文件拖到此处，或点击此区域上传</span>
            </button>
            <div id="shanjianAvatarImagePreview" class="shanjian-upload-preview" style="display:none;"></div>
            <div id="shanjianAvatarImageFileMeta" class="shanjian-file-meta" style="display:none;"></div>
            <div class="shanjian-requirement-box">
              <strong>授权视频</strong>
              <div class="shanjian-requirement-grid">
                <span>文件大小：小于 100MB</span>
                <span>时长：小于 2 分钟</span>
                <span>格式：mp4 / mov，编码 h264 / h265</span>
              </div>
            </div>
            <div class="tvc-field">
              <label for="shanjianAvatarImageAuthText">授权说明</label>
              <textarea id="shanjianAvatarImageAuthText" rows="3" placeholder="请输入授权说明">${defaultShanjianAuthText()}</textarea>
            </div>
            <input id="shanjianAvatarImageAuthFile" type="file" accept=".mp4,.mov,video/mp4,video/quicktime" style="display:none;">
            <button type="button" id="shanjianAvatarImageAuthUploadBox" class="shanjian-upload-box">
              <span class="shanjian-upload-icon">↑</span>
              <strong>请上传授权视频</strong>
              <span>将文件拖到此处，或点击此区域上传</span>
            </button>
            <div id="shanjianAvatarImageAuthPreview" class="shanjian-upload-preview" style="display:none;"></div>
            <div id="shanjianAvatarImageAuthFileMeta" class="shanjian-file-meta" style="display:none;"></div>
            <div class="tvc-field">
              <label>选择模型</label>
              <div class="shanjian-radio-row">
                <label class="shanjian-radio-card"><input type="radio" name="shanjianAvatarImageModel" value="1"><span>2.0</span></label>
                <label class="shanjian-radio-card"><input type="radio" name="shanjianAvatarImageModel" value="2" checked><span>2.1 推荐</span></label>
              </div>
            </div>
            <label class="tvc-check"><input type="checkbox" id="shanjianAvatarImageAgree" checked>我已阅读并同意《使用者承诺须知》</label>
          </div>
          <div class="shanjian-modal-foot">
            <button type="button" class="btn btn-ghost" data-modal-close="shanjianAvatarImageModal">取消</button>
            <button type="button" id="shanjianAvatarImageSubmitBtn" class="btn btn-primary">提交</button>
          </div>
        </div>
      </div>

      <div id="shanjianAvatarVideoModal" class="shanjian-modal" style="display:none;">
        <div class="shanjian-modal-backdrop" data-modal-close="shanjianAvatarVideoModal"></div>
        <div class="shanjian-modal-card" role="dialog" aria-modal="true" aria-labelledby="shanjianAvatarVideoTitle">
          <div class="shanjian-modal-head">
            <h4 id="shanjianAvatarVideoTitle" class="shanjian-modal-title">视频生成数字人</h4>
            <button type="button" class="shanjian-modal-close" data-modal-close="shanjianAvatarVideoModal" aria-label="关闭">×</button>
          </div>
          <div class="shanjian-modal-body">
            <div class="tvc-field">
              <label for="shanjianAvatarVideoName">数字人名称</label>
              <input id="shanjianAvatarVideoName" type="text" maxlength="20" placeholder="输入数字人名称">
            </div>
            <div class="shanjian-requirement-box">
              <strong>视频要求</strong>
              <ol class="shanjian-requirement-list">
                <li>尽量保证人脸清晰完整，不要遮挡。</li>
                <li>训练视频：分辨率单边不大于 2000，时长 5 到 60 秒，帧率 10 到 60fps。</li>
                <li>支持 mp4 / mov，编码 h264 / h265，文件大小不超过 500MB。</li>
              </ol>
            </div>
            <input id="shanjianAvatarVideoFile" type="file" accept=".mp4,.mov,video/mp4,video/quicktime" style="display:none;">
            <button type="button" id="shanjianAvatarVideoUploadBox" class="shanjian-upload-box">
              <span class="shanjian-upload-icon">↑</span>
              <strong>请上传一段视频，作为驱动数字人的底版视频</strong>
              <span>将文件拖到此处，或点击此区域上传</span>
            </button>
            <div id="shanjianAvatarVideoPreview" class="shanjian-upload-preview" style="display:none;"></div>
            <div id="shanjianAvatarVideoFileMeta" class="shanjian-file-meta" style="display:none;"></div>
            <div class="shanjian-requirement-box">
              <strong>授权视频</strong>
              <div class="shanjian-requirement-grid">
                <span>文件大小：小于 100MB</span>
                <span>时长：小于 2 分钟</span>
                <span>格式：mp4 / mov，编码 h264 / h265</span>
              </div>
            </div>
            <div class="tvc-field">
              <label for="shanjianAvatarVideoAuthText">授权说明</label>
              <textarea id="shanjianAvatarVideoAuthText" rows="3" placeholder="请输入授权说明">${defaultShanjianAuthText()}</textarea>
            </div>
            <input id="shanjianAvatarVideoAuthFile" type="file" accept=".mp4,.mov,video/mp4,video/quicktime" style="display:none;">
            <button type="button" id="shanjianAvatarVideoAuthUploadBox" class="shanjian-upload-box">
              <span class="shanjian-upload-icon">↑</span>
              <strong>如需单独授权视频，请在这里上传</strong>
              <span>不上传时默认复用训练视频作为授权视频</span>
            </button>
            <div id="shanjianAvatarVideoAuthPreview" class="shanjian-upload-preview" style="display:none;"></div>
            <div id="shanjianAvatarVideoAuthFileMeta" class="shanjian-file-meta" style="display:none;"></div>
            <label class="tvc-check"><input type="checkbox" id="shanjianAvatarVideoAgree" checked>我已阅读并同意《使用者承诺须知》</label>
          </div>
          <div class="shanjian-modal-foot">
            <button type="button" class="btn btn-ghost" data-modal-close="shanjianAvatarVideoModal">取消</button>
            <button type="button" id="shanjianAvatarVideoSubmitBtn" class="btn btn-primary">提交</button>
          </div>
        </div>
      </div>

      <div id="shanjianVoiceCreateModal" class="shanjian-modal" style="display:none;">
        <div class="shanjian-modal-backdrop" data-modal-close="shanjianVoiceCreateModal"></div>
        <div class="shanjian-modal-card" role="dialog" aria-modal="true" aria-labelledby="shanjianVoiceCreateTitle">
          <div class="shanjian-modal-head">
            <h4 id="shanjianVoiceCreateTitle" class="shanjian-modal-title">创建声音</h4>
            <button type="button" class="shanjian-modal-close" data-modal-close="shanjianVoiceCreateModal" aria-label="关闭">×</button>
          </div>
          <div class="shanjian-modal-body">
            <div class="tvc-field">
              <label for="shanjianVoiceCreateName">声音名称</label>
              <input id="shanjianVoiceCreateName" type="text" maxlength="20" placeholder="输入声音名称">
              <div id="shanjianVoiceCreateNameError" class="shanjian-field-error"></div>
            </div>
            <div class="tvc-field">
              <label for="shanjianVoiceLanguageSelect">语言</label>
              <select id="shanjianVoiceLanguageSelect">
                <option value="zh" selected>中文</option>
                <option value="en">英文</option>
              </select>
            </div>
            <div class="shanjian-requirement-box">
              <strong>声音要求</strong>
              <div class="shanjian-requirement-grid">
                <span>格式：mp3 / m4a / wav</span>
                <span>大小：不超过 20MB</span>
                <span>建议：人声清晰、环境噪音少</span>
              </div>
            </div>
            <input id="shanjianVoiceCreateFile" type="file" accept=".mp3,.m4a,.wav,audio/mpeg,audio/mp4,audio/wav" style="display:none;">
            <button type="button" id="shanjianVoiceUploadBox" class="shanjian-upload-box">
              <span class="shanjian-upload-icon">↑</span>
              <strong>请上传一段声音样本，用于创建声音</strong>
              <span>将文件拖到此处，或点击此区域上传</span>
            </button>
            <div id="shanjianVoiceFilePreview" class="shanjian-upload-preview" style="display:none;"></div>
            <div id="shanjianVoiceFileMeta" class="shanjian-file-meta" style="display:none;"></div>
            <div class="shanjian-record-divider"><span>或者直接电脑录音</span></div>
            <div class="shanjian-record-box">
              <div class="shanjian-record-box-head">
                <strong>电脑录音建声</strong>
                <button type="button" id="shanjianVoicePromptShuffleBtn" class="btn btn-ghost btn-sm">换一段文案</button>
              </div>
              <p class="shanjian-record-box-hint">点击开始录音后，对着麦克风自然朗读下方文案，建议录制 10 到 20 秒。</p>
              <div id="shanjianVoiceRecordPrompt" class="shanjian-record-prompt"></div>
              <div class="shanjian-record-actions">
                <button type="button" id="shanjianVoiceRecordStartBtn" class="btn btn-primary btn-sm shanjian-record-start-btn">开始录音</button>
                <button type="button" id="shanjianVoiceRecordStopBtn" class="btn btn-ghost btn-sm shanjian-record-stop-btn" disabled>停止并使用录音</button>
              </div>
              <div id="shanjianVoiceRecordStatus" class="shanjian-record-status" data-tone="muted">可上传本地音频，或直接使用电脑麦克风录音。</div>
            </div>
            <label class="tvc-check"><input type="checkbox" id="shanjianVoiceCreateAgree" checked>我已阅读并同意《使用者承诺须知》</label>
          </div>
          <div class="shanjian-modal-foot">
            <button type="button" class="btn btn-ghost" data-modal-close="shanjianVoiceCreateModal">取消</button>
            <button type="button" id="shanjianVoiceSubmitBtn" class="btn btn-primary">提交</button>
          </div>
        </div>
      </div>
    `;
  }

  function ensureMarkup() {
    var root = $('content-shanjian-digital-human');
    if (!root) return null;
    if (root.getAttribute('data-shanjian-built-version') !== SHANJIAN_TEMPLATE_VERSION) {
      root.innerHTML = buildTemplate();
      root.setAttribute('data-shanjian-built', '1');
      root.setAttribute('data-shanjian-built-version', SHANJIAN_TEMPLATE_VERSION);
      root.removeAttribute('data-shanjian-init');
    }
    return root;
  }

  window.initShanjianDigitalHumanView = function() {
    var root = ensureMarkup();
    if (!root) return;
    ensureStyles();

    if (root.getAttribute('data-shanjian-init') !== '1') {
      root.setAttribute('data-shanjian-init', '1');
      bindEvents();
    }

    setActiveView(state.taskId ? 'result' : 'avatar');
    bootstrapData().catch(function(err) {
      showMessage(err && err.message ? err.message : '初始化 Shanjian 页面失败', true);
    });
  };

  if (typeof window.initHiflyDigitalHumanView !== 'function') {
    window.initHiflyDigitalHumanView = window.initShanjianDigitalHumanView;
  }

  if (location.hash === '#shanjian-digital-human') {
    setTimeout(function() {
      if (typeof window.initShanjianDigitalHumanView === 'function') window.initShanjianDigitalHumanView();
    }, 0);
  }
})();
