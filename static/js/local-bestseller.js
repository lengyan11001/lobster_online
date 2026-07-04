(function() {
  var state = {
    initialized: false,
    selectedPhoto: null,
    selectedVideo: null,
    bgmOptions: [],
    bgmMenuOpenDay: null,
    bgmPreviewKey: '',
    bgmPreviewUrl: '',
    bgmPlayer: null,
    assetPickerOpen: false,
    assetPickerMode: 'person',
    assetPickerDay: null,
    batchSceneSelectedIds: [],
    assets: [],
    plan: [],
    loadingAssets: false,
    submitting: false,
    sceneGenerating: {},
    videoGenerating: {},
    videoPolling: {}
  };

  function $(id) {
    return document.getElementById(id);
  }

  function localBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  }

  function headersJson() {
    var h = { 'Content-Type': 'application/json' };
    if (typeof authHeaders === 'function') h = Object.assign(h, authHeaders() || {});
    return h;
  }

  function headersUpload() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    delete h['Content-Type'];
    delete h['content-type'];
    return h;
  }

  function escapeHtml(text) {
    return String(text || '').replace(/[&<>"']/g, function(ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function normalizeBgmOption(option, fallbackKey) {
    option = option && typeof option === 'object' ? option : {};
    var key = String(option.key || fallbackKey || '').trim();
    var musicName = String(option.music_name || option.name || '').trim();
    if (!key) {
      key = musicName ? musicName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') : '';
    }
    if (!key) key = String(fallbackKey || 'default-bgm');
    var url = String(option.bgm_url || option.url || '').trim();
    var volume = Number(option.volume);
    if (!(volume >= 0)) volume = 1;
    if (volume > 1) volume = 1;
    var hasPreview = option.has_preview != null ? !!option.has_preview : !!url;
    return {
      key: key,
      music_name: musicName || '默认背景音乐',
      note: String(option.note || '').trim(),
      bgm_url: url,
      volume: volume,
      has_preview: hasPreview,
      using_default_url: !!option.using_default_url
    };
  }

  function bgmOptionsForItem(item) {
    var seen = {};
    var out = [];
    var sourceOptions = (state.bgmOptions && state.bgmOptions.length) ? state.bgmOptions : collectBgmOptionsFromPlan();
    sourceOptions.forEach(function(option, idx) {
      var normalized = normalizeBgmOption(option, 'bgm-' + idx);
      if (seen[normalized.key]) return;
      seen[normalized.key] = true;
      out.push(normalized);
    });
    var current = normalizeBgmOption(item && item.bgm, item && item.day ? ('day-' + item.day) : 'current-bgm');
    if (current.music_name && !seen[current.key]) out.unshift(current);
    return out;
  }

  function collectBgmOptionsFromPlan() {
    var out = [];
    var seen = {};
    (state.plan || []).forEach(function(item, idx) {
      if (!item || !item.bgm) return;
      var normalized = normalizeBgmOption(item.bgm, 'plan-bgm-' + idx);
      var signature = normalized.key + '|' + normalized.music_name + '|' + normalized.bgm_url;
      if (seen[signature]) return;
      seen[signature] = true;
      out.push(normalized);
    });
    return out;
  }

  function ensureBgmOptions() {
    if (state.bgmOptions && state.bgmOptions.length) return;
    state.bgmOptions = collectBgmOptionsFromPlan();
  }

  function applyCurrentBgmOptionsToPlan() {
    ensureBgmOptions();
    if (!state.plan.length || !state.bgmOptions.length) return;
    var byName = {};
    state.bgmOptions.forEach(function(option) {
      var name = String(option.music_name || '').trim();
      if (name && !byName[name]) byName[name] = option;
    });
    state.plan.forEach(function(item) {
      var current = item && item.bgm ? String(item.bgm.music_name || '').trim() : '';
      if (current && byName[current]) item.bgm = byName[current];
    });
  }

  function syncItemBgm(item) {
    if (!item) return null;
    var current = normalizeBgmOption(item.bgm, item.day ? ('day-' + item.day) : 'current-bgm');
    var options = bgmOptionsForItem(item);
    var matched = null;
    options.forEach(function(option) {
      if (!matched && option.key === current.key) matched = option;
    });
    item.bgm = matched || current;
    return item.bgm;
  }

  function previewButtonLabel(item) {
    var bgm = syncItemBgm(item);
    if (!bgm || !bgm.has_preview || !bgm.bgm_url) return '暂无独立试听';
    if (state.bgmPreviewKey && state.bgmPreviewKey === bgm.key && state.bgmPreviewUrl === bgm.bgm_url) return '暂停试听';
    return '试听';
  }

  function bgmMenuOpenForDay(day) {
    return String(state.bgmMenuOpenDay || '') === String(day || '');
  }

  function closeBgmMenu() {
    if (state.bgmMenuOpenDay == null) return;
    state.bgmMenuOpenDay = null;
    renderPlan();
  }

  function toggleBgmMenu(day) {
    var nextDay = String(day || '').trim();
    state.bgmMenuOpenDay = bgmMenuOpenForDay(nextDay) ? null : nextDay;
    renderPlan();
  }

  function applyBgmSelection(day, nextKey) {
    var item = findPlanItem(day);
    if (!item) return;
    var matched = null;
    bgmOptionsForItem(item).forEach(function(option) {
      if (!matched && option.key === String(nextKey || '').trim()) matched = option;
    });
    if (!matched) return;
    item.bgm = matched;
    state.bgmMenuOpenDay = null;
    renderPlan();
    updateButtons();
  }

  function showMessage(text, danger) {
    var el = $('localBestsellerMsg');
    if (!el) return;
    el.style.display = text ? 'block' : 'none';
    el.className = 'msg' + (danger ? ' err' : '');
    el.textContent = text || '';
  }

  function loadBgmOptions() {
    var base = localBase();
    if (!base) return Promise.resolve();
    return fetch(base + '/api/local-bestseller/bgm-options', { headers: headersUpload() })
      .then(function(resp) {
        return resp.json().then(function(data) {
          return { ok: resp.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) throw new Error((result.data && result.data.detail) || '背景音乐列表加载失败');
        state.bgmOptions = Array.isArray(result.data.items) ? result.data.items.map(function(option, idx) {
          return normalizeBgmOption(option, 'bgm-' + idx);
        }) : [];
        ensureBgmOptions();
        applyCurrentBgmOptionsToPlan();
        renderPlan();
      })
      .catch(function() {});
  }

  function val(id) {
    var el = $(id);
    return el ? String(el.value || '').trim() : '';
  }

  function planDays() {
    var n = Number(val('localBestsellerDays') || 30);
    if (n !== 10 && n !== 20 && n !== 30) n = 30;
    return n;
  }

  function planLabel() {
    return planDays() + '天';
  }

  function setVal(id, value) {
    var el = $(id);
    if (el) el.value = value || '';
  }

  function sanitizeVideoPromptNoSpeech(text) {
    var out = String(text || '').trim();
    if (!out) return out;
    var protected = {};
    var index = 0;
    ['说话', '口播', '对白', '台词', '唱歌', '配音', '开口', '做明显嘴型', '唇同步'].forEach(function(term) {
      ['人物不要', '不要', '人物禁止', '禁止'].forEach(function(prefix) {
        var phrase = prefix + term;
        while (out.indexOf(phrase) !== -1) {
          var token = '__LB_NO_SPEECH_' + (index++) + '__';
          protected[token] = (prefix.indexOf('人物') === 0 ? '人物不要' : '不要') + term;
          out = out.replace(phrase, token);
        }
      });
    });
    [
      ['动作自然：走路、停下、看镜头、轻微招手或口播。', '动作自然：走路、停下、转身、低头整理东西、侧身工作或与环境自然互动；视频中间约第4-6秒要自然抬头看向镜头。'],
      ['动作自然：走路、停下、看镜头、轻微招手或口播', '动作自然：走路、停下、转身、低头整理东西、侧身工作或与环境自然互动；视频中间约第4-6秒要自然抬头看向镜头'],
      ['轻微招手或口播', '自然走动、停下、转身、整理东西或侧身工作'],
      ['招手或口播', '自然走动、整理东西或侧身工作'],
      ['自然口播', '自然动作'],
      ['正面对镜口播', '自然走动、整理东西或被侧面随手拍到'],
      ['对镜口播', '自然走动、整理东西或被侧面随手拍到'],
      ['人物正面口播', '人物自然走动、整理东西或被侧面随手拍到'],
      ['看镜头说话', '自然看向周围环境'],
      ['对着镜头说话', '自然看向周围环境'],
      ['说台词', '做自然动作'],
      ['说话', '做自然动作'],
      ['讲述', '自然行动'],
      ['开口', '嘴巴自然放松'],
      ['嘴型', '嘴巴自然放松'],
      ['唇形', '嘴巴自然放松'],
      ['唇同步', '嘴巴自然放松'],
      ['看镜头', '中间三秒自然看向镜头，其他时间自然看向周围']
    ].forEach(function(pair) {
      out = out.split(pair[0]).join(pair[1]);
    });
    out = out.split('口播').join('无声自然动作');
    out = out.split('对白').join('无对白');
    out = out.split('台词').join('无台词');
    out = out.split('配音').join('无配音');
    Object.keys(protected).forEach(function(token) {
      out = out.split(token).join(protected[token]);
    });
    var guard = '全程静默自然动作视频：人物不要说话、不要口播、不要对白、不要台词、不要唱歌、不要配音、不要做明显嘴型或唇同步，嘴巴自然放松或闭合；不要让AI生成任何字幕、文字、水印，最终字幕只由后期叠加。';
    if (out.indexOf('全程静默自然动作视频') === -1) {
      out += /[。；;.]$/.test(out) ? guard : '。' + guard;
    }
    var motion = '人物正常走动，步伐和手臂摆动自然；视频中间约第4-6秒，人物要自然抬头看向镜头或自然扫视镜头，像刚好发现朋友在拍；镜头像朋友拿手机边走边拍，轻微跟拍、轻微晃动、轻微推近或侧向视角变化，视角不完美但真实，像真的现场拍摄。';
    if (out.indexOf('人物正常走动') === -1 && out.indexOf('像真的现场拍摄') === -1) {
      out += /[。；;.]$/.test(out) ? motion : '。' + motion;
    }
    var midLook = '节奏要求：10秒视频中间约第4-6秒，人物要自然抬头看向镜头或自然扫视镜头，保持真实随手拍感；其他时间可以继续走路、整理东西、侧身工作或看向周围。';
    if (out.indexOf('第4-6秒') === -1 && out.indexOf('中间约第4') === -1) {
      out += /[。；;.]$/.test(out) ? midLook : '。' + midLook;
    }
    var bgm = '视频必须伴随街头背景音和轻快节奏音乐，音量低，只做真实街头氛围和轻快节奏铺底；不要人声、不要旁白、不要歌词、不要任何人物发声。';
    var optionalBgm = '可加入轻微背景音乐或真实环境氛围感，音量低，不要人声、不要旁白、不要歌词、不要任何人物发声。';
    if (out.indexOf(optionalBgm) !== -1) {
      out = out.split(optionalBgm).join(bgm);
    } else if (out.indexOf('街头背景音') === -1 || out.indexOf('轻快节奏音乐') === -1) {
      out += /[。；;.]$/.test(out) ? bgm : '。' + bgm;
    }
    return out;
  }

  function getProfile() {
    return {
      name: val('localBestsellerName'),
      nickname: val('localBestsellerNickname') || val('localBestsellerName') || '我',
      gender: val('localBestsellerGender') || 'female',
      identity: val('localBestsellerIdentity') || '女老板',
      industry: val('localBestsellerIndustry') || '大健康',
      city: val('localBestsellerCity') || '深圳',
      province: val('localBestsellerProvince') || '广东',
      hometown: val('localBestsellerHometown') || '广东潮汕',
      age_label: val('localBestsellerAge') || '80后',
      target_age: val('localBestsellerTargetAge') || '607080后',
      style: val('localBestsellerStyle') || '真实同城生活感',
      source_mode: selectedSourceMode(),
      photo_asset_id: state.selectedPhoto && state.selectedPhoto.asset_id ? state.selectedPhoto.asset_id : '',
      photo_url: state.selectedPhoto && state.selectedPhoto.url ? state.selectedPhoto.url : '',
      uploaded_video_url: state.selectedVideo && state.selectedVideo.url ? state.selectedVideo.url : ''
    };
  }

  function selectedSourceMode() {
    var checked = document.querySelector('input[name="localBestsellerSourceMode"]:checked');
    return checked ? checked.value : 'upload';
  }

  function renderSelectedMedia() {
    var box = $('localBestsellerSelectedMedia');
    if (!box) return;
    var photo = state.selectedPhoto;
    var video = state.selectedVideo;
    if (!photo && !video) {
      box.innerHTML = '<div class="lb-empty-media">还没有选择人物照片或视频。</div>';
      return;
    }
    var parts = [];
    if (photo) {
      parts.push(
        '<div class="lb-media-chip">' +
          '<img src="' + escapeHtml(photo.preview_url || photo.url || '') + '" alt="">' +
          '<div><strong>' + escapeHtml(photo.name || '人物照片') + '</strong><span>' + escapeHtml(photo.asset_id || '本次上传') + '</span></div>' +
          '<button type="button" data-lb-remove-photo>×</button>' +
        '</div>'
      );
    }
    if (video) {
      parts.push(
        '<div class="lb-media-chip">' +
          '<div class="lb-video-thumb">MP4</div>' +
          '<div><strong>' + escapeHtml(video.name || '上传视频') + '</strong><span>' + escapeHtml(video.asset_id || '本次上传') + '</span></div>' +
          '<button type="button" data-lb-remove-video>×</button>' +
        '</div>'
      );
    }
    box.innerHTML = parts.join('');
  }

  function normalizeAsset(row) {
    if (!row || !row.asset_id) return null;
    var url = row.open_url || row.source_url || row.preview_url || row.url || '';
    return {
      asset_id: row.asset_id,
      name: row.filename || row.name || row.asset_id,
      url: url,
      preview_url: row.preview_url || url,
      media_type: row.media_type || 'image',
      file_size: row.file_size || 0
    };
  }

  function applySceneAssetToDay(day, asset) {
    var item = findPlanItem(day);
    if (!item || !asset) return;
    item.scene_asset_id = asset.asset_id || '';
    item.scene_url = asset.url || asset.preview_url || '';
    item.scene_preview_url = asset.preview_url || asset.url || '';
    item.scene_name = asset.name || asset.asset_id || '场景底图';
    item.prefer_scene_for_video = true;
  }

  function applySceneAssetsToPlan(assets) {
    var list = (assets || []).filter(Boolean);
    if (!state.plan.length || !list.length) return;
    state.plan.forEach(function(item, idx) {
      var asset = list[idx] || list[idx % list.length];
      item.scene_asset_id = asset.asset_id || '';
      item.scene_url = asset.url || asset.preview_url || '';
      item.scene_preview_url = asset.preview_url || asset.url || '';
      item.scene_name = asset.name || asset.asset_id || '场景底图';
      item.prefer_scene_for_video = true;
    });
  }

  function uploadAssetFile(file, kind) {
    var base = localBase();
    if (!base) return Promise.reject(new Error('当前未检测到本机后端地址'));
    var fd = new FormData();
    fd.append('file', file);
    showMessage('正在上传' + (kind === 'video' ? '视频' : '照片') + '...', false);
    return fetch(base + '/api/assets/upload', {
      method: 'POST',
      headers: headersUpload(),
      body: fd
    })
      .then(function(resp) {
        return resp.json().then(function(data) {
          return { ok: resp.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok || !result.data.asset_id) {
          throw new Error((result.data && result.data.detail) || '上传失败');
        }
        var item = normalizeAsset(result.data) || {
          asset_id: result.data.asset_id,
          name: file.name,
          url: result.data.source_url || result.data.preview_url || '',
          preview_url: result.data.preview_url || result.data.source_url || '',
          media_type: kind
        };
        return item;
      });
  }

  function uploadFile(file, kind) {
    return uploadAssetFile(file, kind).then(function(item) {
      if (kind === 'video') state.selectedVideo = item;
      else state.selectedPhoto = item;
      renderSelectedMedia();
      showMessage('素材已上传。', false);
      return item;
    });
  }

  function uploadSceneFileForDay(file, day) {
    showMessage('正在上传 Day ' + day + ' 场景底图...', false);
    return uploadAssetFile(file, 'image').then(function(item) {
      applySceneAssetToDay(day, item);
      renderPlan();
      updateButtons();
      showMessage('Day ' + day + ' 场景底图已上传。', false);
      return item;
    });
  }

  function uploadBatchSceneFiles(files) {
    var list = Array.prototype.slice.call(files || []).filter(Boolean);
    if (!state.plan.length) {
      showMessage('请先生成' + planLabel() + '方案。', true);
      return Promise.resolve([]);
    }
    if (!list.length) return Promise.resolve([]);
    state.submitting = true;
    updateButtons();
    showMessage('正在上传 ' + list.length + ' 张场景底图...', false);
    return Promise.all(list.map(function(file) { return uploadAssetFile(file, 'image'); }))
      .then(function(assets) {
        applySceneAssetsToPlan(assets);
        renderPlan();
        showMessage('已按顺序给' + state.plan.length + '天卡片分配场景底图。', false);
        return assets;
      })
      .finally(function() {
        state.submitting = false;
        updateButtons();
      });
  }

  function loadAssets() {
    var base = localBase();
    if (!base) return Promise.reject(new Error('当前未检测到本机后端地址'));
    state.loadingAssets = true;
    renderAssetPicker();
    return fetch(base + '/api/assets?media_type=image&limit=120', { headers: headersUpload() })
      .then(function(resp) {
        return resp.json().then(function(data) {
          return { ok: resp.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) throw new Error((result.data && result.data.detail) || '素材库加载失败');
        state.assets = (Array.isArray(result.data.assets) ? result.data.assets : []).map(normalizeAsset).filter(Boolean);
        state.loadingAssets = false;
        renderAssetPicker();
      })
      .catch(function(err) {
        state.loadingAssets = false;
        renderAssetPicker((err && err.message) || '素材库加载失败');
      });
  }

  function renderAssetPicker(error) {
    var modal = $('localBestsellerAssetPicker');
    var grid = $('localBestsellerAssetGrid');
    var status = $('localBestsellerAssetStatus');
    var title = $('localBestsellerAssetTitle');
    var foot = $('localBestsellerAssetFoot');
    if (!modal || !grid || !status) return;
    if (foot) foot.style.display = state.assetPickerMode === 'batch-scene' ? 'flex' : 'none';
    if (title) {
      if (state.assetPickerMode === 'batch-scene') title.textContent = '批量选择素材库场景底图';
      else if (state.assetPickerMode === 'card-scene') title.textContent = '选择 Day ' + state.assetPickerDay + ' 场景底图';
      else title.textContent = '选择素材库人物照片';
    }
    modal.classList.toggle('is-visible', !!state.assetPickerOpen);
    if (error) {
      status.textContent = error;
      grid.innerHTML = '<div class="lb-asset-empty">素材库加载失败。</div>';
      return;
    }
    if (state.loadingAssets) {
      status.textContent = '正在加载素材库图片...';
      grid.innerHTML = '<div class="lb-asset-empty">正在加载...</div>';
      return;
    }
    var batchCount = state.batchSceneSelectedIds.length;
    status.textContent = state.assets.length
      ? ((state.assetPickerMode === 'batch-scene' || state.assetPickerMode === 'card-scene') ? '选择场景底图 · 共 ' : '选择人物照片 · 共 ') + state.assets.length + ' 张图片' + (state.assetPickerMode === 'batch-scene' ? '，已选 ' + batchCount + ' 张' : '')
      : '素材库暂无图片';
    if (!state.assets.length) {
      grid.innerHTML = '<div class="lb-asset-empty">可以先上传人物照片。</div>';
      return;
    }
    grid.innerHTML = state.assets.map(function(item) {
      var selected = state.batchSceneSelectedIds.indexOf(item.asset_id) !== -1;
      return '<button type="button" class="lb-asset-item' + (selected ? ' is-selected' : '') + '" data-lb-asset-id="' + escapeHtml(item.asset_id) + '">' +
        '<img src="' + escapeHtml(item.preview_url || item.url) + '" alt="" loading="lazy" decoding="async">' +
        '<span>' + escapeHtml(item.name) + '</span>' +
      '</button>';
    }).join('');
    if (foot) {
      foot.style.display = state.assetPickerMode === 'batch-scene' ? 'flex' : 'none';
      var countEl = $('localBestsellerAssetPickedCount');
      if (countEl) countEl.textContent = batchCount ? '已选 ' + batchCount + ' 张，按 Day 顺序分配' : '可多选；选 1 张则全部 Day 使用同一张';
    }
  }

  function openAssetPicker() {
    state.assetPickerMode = 'person';
    state.assetPickerDay = null;
    state.assetPickerOpen = true;
    renderAssetPicker();
    loadAssets();
  }

  function openCardSceneAssetPicker(day) {
    state.assetPickerMode = 'card-scene';
    state.assetPickerDay = Number(day) || null;
    state.assetPickerOpen = true;
    renderAssetPicker();
    loadAssets();
  }

  function openBatchSceneAssetPicker() {
    if (!state.plan.length) {
      showMessage('请先生成' + planLabel() + '方案。', true);
      return;
    }
    state.assetPickerMode = 'batch-scene';
    state.assetPickerDay = null;
    state.batchSceneSelectedIds = [];
    state.assetPickerOpen = true;
    renderAssetPicker();
    loadAssets();
  }

  function closeAssetPicker() {
    state.assetPickerOpen = false;
    renderAssetPicker();
  }

  function applySelectedBatchSceneAssets() {
    var selected = state.batchSceneSelectedIds.map(function(id) {
      return state.assets.filter(function(row) { return row.asset_id === id; })[0];
    }).filter(Boolean);
    if (!selected.length) {
      showMessage('请先在素材库里选择场景底图。', true);
      return;
    }
    applySceneAssetsToPlan(selected);
    closeAssetPicker();
    renderPlan();
    updateButtons();
    showMessage('已按顺序给' + state.plan.length + '天卡片分配素材库底图。', false);
  }

  function pickAsset(assetId) {
    var item = state.assets.filter(function(row) { return row.asset_id === assetId; })[0];
    if (!item) return;
    var isScene = state.assetPickerMode === 'card-scene' || state.assetPickerMode === 'batch-scene';
    if (state.assetPickerMode === 'card-scene') {
      applySceneAssetToDay(state.assetPickerDay, item);
    } else if (state.assetPickerMode === 'batch-scene') {
      var idx = state.batchSceneSelectedIds.indexOf(item.asset_id);
      if (idx === -1) state.batchSceneSelectedIds.push(item.asset_id);
      else state.batchSceneSelectedIds.splice(idx, 1);
      renderAssetPicker();
      return;
    } else {
      state.selectedPhoto = item;
    }
    closeAssetPicker();
    renderSelectedMedia();
    renderPlan();
    updateButtons();
    showMessage(isScene ? '已选择素材库场景底图。' : '已选择素材库人物照片。', false);
  }

  function splitCaptionLines(text) {
    return String(text || '')
      .replace(/。/g, '\n')
      .replace(/？/g, '？\n')
      .replace(/！/g, '！\n')
      .split(/\r?\n/)
      .map(function(line) { return line.trim(); })
      .filter(Boolean);
  }

  function cleanSubtitleText(text) {
    return String(text || '').split(/\r?\n/).map(function(raw) {
      var line = raw.trim();
      line = line.replace(/^(标题文案|数字人口播内容|口播内容|文案内容)\s*[：:]\s*/, '').trim();
      line = line.replace(/^坐标\s*[：:]\s*/, '').trim();
      if (/^音乐\s*[：:]/.test(line)) return '';
      if (!line || line.charAt(0) === '#') return '';
      return line;
    }).filter(Boolean).join('\n');
  }

  function findPlanItem(dayOrId) {
    return state.plan.filter(function(row) {
      return String(row.id) === String(dayOrId) || Number(row.day) === Number(dayOrId);
    })[0] || null;
  }

  function getNestedValue(item, path) {
    var cur = item;
    String(path || '').split('.').forEach(function(part) {
      if (cur && Object.prototype.hasOwnProperty.call(cur, part)) cur = cur[part];
      else cur = '';
    });
    return cur == null ? '' : cur;
  }

  function setNestedValue(item, path, value) {
    var parts = String(path || '').split('.');
    var cur = item;
    parts.forEach(function(part, idx) {
      if (idx === parts.length - 1) {
        cur[part] = value;
      } else {
        if (!cur[part] || typeof cur[part] !== 'object') cur[part] = {};
        cur = cur[part];
      }
    });
    if (path === 'douyin.copy') {
      item.douyin = item.douyin || {};
      item.douyin.subtitle = item.douyin.subtitle || {};
      item.douyin.subtitle.lines = splitCaptionLines(value);
    }
    if (path === 'videohao.copy') {
      item.videohao = item.videohao || {};
      item.videohao.subtitle = item.videohao.subtitle || {};
      item.videohao.subtitle.lines = splitCaptionLines(value);
    }
    if (path === 'subtitle_text') item.subtitle_lines = splitCaptionLines(value);
    if (path === 'scene_prompt') item.image_prompt = value;
  }

  function textarea(day, path, label, rows) {
    var item = findPlanItem(day) || {};
    return '<label class="lb-edit-field">' + escapeHtml(label) +
      '<textarea rows="' + escapeHtml(rows || 3) + '" data-lb-edit-day="' + escapeHtml(day) + '" data-lb-edit-path="' + escapeHtml(path) + '">' +
      escapeHtml(getNestedValue(item, path)) +
      '</textarea></label>';
  }

  function renderSubtitlePreview(item) {
    var douyin = item.douyin || {};
    var subtitleLines = splitCaptionLines(item.subtitle_text || '');
    var dyLines = subtitleLines.length ? subtitleLines : (douyin.subtitle && Array.isArray(douyin.subtitle.lines) ? douyin.subtitle.lines : splitCaptionLines(douyin.copy || ''));
    var style = (douyin.subtitle && douyin.subtitle.style) || {};
    if (style.variant === 'rank_table' && Number(item.day) === 1) {
      var titleA = dyLines[0] || '我国南北城市分布';
      var titleB = dyLines[1] || '四川竟然是南方';
      var south = ['上海', '江苏', '浙江', '安徽', '江西', '湖北', '湖南', '四川', '重庆', '贵州', '云南', '福建', '广东', '广西', '海南'];
      var north = ['北京', '天津', '河北', '山西', '内蒙古', '辽宁', '吉林', '黑龙江', '山东', '河南', '陕西', '甘肃', '青海', '宁夏', '新疆'];
      return '<div class="lb-rank-caption">' +
        '<div class="lb-rank-title"><span>' + escapeHtml(titleA) + '</span><strong>' + escapeHtml(titleB) + '</strong></div>' +
        '<div class="lb-rank-cols">' +
          '<div><b>南方</b>' + south.map(function(x) { return '<span>' + escapeHtml(x) + '</span>'; }).join('') + '</div>' +
          '<div><b>北方</b>' + north.map(function(x) { return '<span>' + escapeHtml(x) + '</span>'; }).join('') + '</div>' +
        '</div>' +
      '</div>';
    }
    var previewLines = dyLines.slice(0, 8);
    var titleLine = previewLines[0] || '';
    var bodyLines = previewLines.slice(1);
    function captionClass(line, base) {
      var len = String(line || '').replace(/[^\x00-\xff]/g, 'aa').length;
      var cls = base || '';
      if (len >= 22) cls += ' is-long';
      if (len >= 30) cls += ' is-xlong';
      return cls;
    }
    return '<div class="lb-phone-caption lb-phone-caption-top">' +
      (titleLine ? '<span class="' + captionClass(titleLine, 'is-hot') + '">' + escapeHtml(titleLine) + '</span>' : '') +
      (bodyLines.length ? '<div class="lb-caption-body">' + bodyLines.map(function(line) {
        return '<span class="' + captionClass(line, '') + '">' + escapeHtml(line) + '</span>';
      }).join('') + '</div>' : '') +
    '</div>';
  }

  function renderEffectPreview(item, previewUrl) {
    var videoUrl = String((item && item.video_url) || '').trim();
    if (videoUrl) {
      return [
        '<span class="lb-video-shell" data-lb-video-shell>',
        '<video class="lb-video-preview" src="' + escapeHtml(videoUrl) + '" controls controlsList="nodownload nofullscreen" playsinline preload="metadata"></video>',
        '<button type="button" class="lb-video-fs-btn" data-lb-video-fullscreen>放大</button>',
        '<span class="lb-video-fullscreen-hint">按 Esc 退出全屏</span>',
        '<button type="button" class="lb-video-exit-btn" data-lb-video-exit>退出全屏 Esc</button>',
        '</span>'
      ].join('');
    }
    var preview = previewUrl
      ? '<img class="lb-scene-image" src="' + escapeHtml(previewUrl) + '" alt="">'
      : '<div class="lb-phone-person"></div>';
    return preview + renderSubtitlePreview(item);
  }

  function compactSubtitle(text) {
    return splitCaptionLines(cleanSubtitleText(text)).slice(0, 8).join('\n');
  }

  function normalizeItem(item) {
    item = item || {};
    item.subtitle_text = compactSubtitle(item.subtitle_text || item.ai_variant || (item.videohao && item.videohao.copy) || (item.douyin && item.douyin.copy) || '');
    item.video_prompt = sanitizeVideoPromptNoSpeech(item.video_prompt || '');
    item.bgm = syncItemBgm(item);
    return item;
  }

  function normalizePlan(items) {
    return (items || []).map(normalizeItem);
  }

  function cardPayload(item) {
    return {
      id: item.id || '',
      day: Number(item.day) || 0,
      title: item.title || '',
      stage: item.stage || '',
      douyin: item.douyin || {},
      videohao: item.videohao || {},
      bgm: item.bgm || {},
      subtitle_text: item.subtitle_text || '',
      ai_variant: item.ai_variant || '',
      scene_prompt: item.scene_prompt || item.image_prompt || '',
      image_prompt: item.image_prompt || item.scene_prompt || '',
      video_prompt: sanitizeVideoPromptNoSpeech(item.video_prompt || ''),
      scene_asset_id: item.scene_asset_id || '',
      scene_url: item.scene_url || '',
      scene_preview_url: item.scene_preview_url || '',
      scene_name: item.scene_name || '',
      prefer_scene_for_video: !!item.prefer_scene_for_video,
      image_url: item.image_url || '',
      image_asset_id: item.image_asset_id || '',
      video_url: item.video_url || '',
      video_task_id: item.video_task_id || '',
      video_job_id: item.video_job_id || '',
      video_poll_path: item.video_poll_path || '',
      scene_status: item.scene_status || '',
      video_status: item.video_status || ''
    };
  }

  function planPayload() {
    return state.plan.map(cardPayload);
  }

  function renderBgmControls(item) {
    var bgm = syncItemBgm(item);
    var options = bgmOptionsForItem(item);
    var optionHtml = options.map(function(option) {
      var selected = option.key === bgm.key;
      return '' +
        '<button type="button" class="lb-bgm-option' + (selected ? ' is-selected' : '') + '"' +
          ' data-lb-bgm-option="' + escapeHtml(item.day) + '"' +
          ' data-lb-bgm-key="' + escapeHtml(option.key) + '">' +
          '<span class="lb-bgm-option-name">' + escapeHtml(option.music_name) + '</span>' +
          (option.note ? '<small class="lb-bgm-option-note">' + escapeHtml(option.note) + '</small>' : '') +
        '</button>';
    }).join('');
    var note = bgm.note ? '<small class="lb-bgm-note">' + escapeHtml(bgm.note) + '</small>' : '';
    var source = bgm.using_default_url ? '<small class="lb-bgm-meta">当前还没有这首歌的独立音频，合成时先走默认背景音乐</small>' : '';
    var previewDisabled = bgm.has_preview ? '' : ' disabled';
    var isOpen = bgmMenuOpenForDay(item.day);
    return '' +
      '<div class="lb-bgm-row">' +
        '<label class="lb-bgm-field">' +
          '<span>背景音乐</span>' +
          '<div class="lb-bgm-select-wrap">' +
            '<div class="lb-bgm-picker' + (isOpen ? ' is-open' : '') + '" data-lb-bgm-picker="' + escapeHtml(item.day) + '">' +
              '<button type="button" class="lb-bgm-select" data-lb-bgm-toggle="' + escapeHtml(item.day) + '" aria-expanded="' + (isOpen ? 'true' : 'false') + '">' +
                '<span class="lb-bgm-select-label">' + escapeHtml(bgm.music_name) + '</span>' +
                '<span class="lb-bgm-select-arrow" aria-hidden="true"></span>' +
              '</button>' +
              '<div class="lb-bgm-menu">' + optionHtml + '</div>' +
            '</div>' +
          '</div>' +
        '</label>' +
        '<button type="button" class="btn btn-ghost btn-sm lb-bgm-preview-btn" data-lb-bgm-preview="' + escapeHtml(item.day) + '"' + previewDisabled + '>' + escapeHtml(previewButtonLabel(item)) + '</button>' +
      '</div>' +
      '<div class="lb-bgm-meta-row">' + note + source + '</div>';
  }

  function renderPlan() {
    var grid = $('localBestsellerResultGrid');
    var empty = $('localBestsellerEmptyResult');
    if (!grid || !empty) return;
    if (!state.plan.length) {
      empty.style.display = 'flex';
      grid.innerHTML = '';
      return;
    }
    empty.style.display = 'none';
    grid.innerHTML = state.plan.map(function(item) {
      var tags = (item.hashtags || []).map(function(tag) {
        return '<span>' + escapeHtml(tag) + '</span>';
      }).join('');
      var hasImage = !!item.image_url;
      var hasVideo = !!item.video_url;
      var sceneRefUrl = item.scene_preview_url || item.scene_url || '';
      var hasVideoSource = !!(item.image_url || item.image_asset_id || item.scene_url || item.scene_asset_id);
      var previewUrl = item.image_url || sceneRefUrl;
      var sceneBusy = !!state.sceneGenerating[item.id];
      var videoBusy = !!state.videoGenerating[item.id];
      var sceneBtnText = sceneBusy ? '合成中...' : (hasImage ? '重新合成场景' : '合成场景');
      var videoBtnText = videoBusy ? '提交中...' : (item.video_task_id ? '重新合成视频' : '合成视频');
      var videoTask = item.video_task_id ? '<small class="lb-task-id">视频任务：' + escapeHtml(item.video_task_id) + '</small>' : '';
      var videoProgress = item.video_progress_label ? '<small class="lb-task-id">' + escapeHtml(item.video_progress_label) + '</small>' : '';
      var clearSceneBtn = sceneRefUrl ? '<button type="button" class="btn btn-ghost btn-sm" data-lb-clear-scene-ref="' + escapeHtml(item.day) + '">清除底图</button>' : '';
      var sceneRefActions = '<div class="lb-card-actions lb-card-actions-ref"><button type="button" class="btn btn-ghost btn-sm" data-lb-upload-scene-ref="' + escapeHtml(item.day) + '">上传底图</button><button type="button" class="btn btn-ghost btn-sm" data-lb-pick-scene-ref="' + escapeHtml(item.day) + '">选择底图</button>' + clearSceneBtn + '</div>';
      var renderActions = '<div class="lb-card-actions lb-card-actions-render"><button type="button" class="btn btn-primary btn-sm lb-card-action-main" data-lb-scene="' + escapeHtml(item.day) + '" ' + (sceneBusy ? 'disabled' : '') + '>' + escapeHtml(sceneBtnText) + '</button><button type="button" class="btn btn-primary btn-sm lb-card-action-main lb-card-action-alt" data-lb-video="' + escapeHtml(item.day) + '" ' + (videoBusy || !hasVideoSource ? 'disabled' : '') + '>' + escapeHtml(videoBtnText) + '</button></div>';
      return '<article class="lb-result-card">' +
        '<div class="lb-result-head"><span>Day ' + escapeHtml(item.day) + '</span><strong>' + escapeHtml(item.title) + '</strong><em>' + escapeHtml(item.stage) + '</em></div>' +
        '<div class="lb-result-preview"><div class="lb-phone-frame' + ((previewUrl || hasVideo) ? ' has-image' : '') + (hasVideo ? ' has-video' : '') + '">' + renderEffectPreview(item, previewUrl) + '</div></div>' +
        '<div class="lb-result-body">' +
          '<details class="lb-copy-details"><summary>字幕与提示词可修改</summary>' +
            textarea(item.day, 'subtitle_text', '最终上屏字幕', 4) +
            textarea(item.day, 'scene_prompt', '场景图片合成提示词', 6) +
            textarea(item.day, 'video_prompt', 'Grok 10秒视频提示词', 5) +
          '</details>' +
          renderBgmControls(item) +
          '<div class="lb-tags">' + tags + '</div>' +
        '</div>' +
        '<div class="lb-result-foot"><div class="lb-result-actions">' + sceneRefActions + renderActions + '</div><div class="lb-status-stack"><span data-status="' + escapeHtml(item.scene_status || item.status || 'ready') + '">' + escapeHtml(statusLabel(item.scene_status || item.status)) + '</span><span data-status="' + escapeHtml(item.video_status || 'ready') + '">' + escapeHtml(videoStatusLabel(item.video_status)) + '</span>' + videoTask + videoProgress + '</div></div>' +
      '</article>';
    }).join('');
  }

  function statusLabel(status) {
    if (status === 'scene_completed') return '场景已合成';
    if (status === 'scene_failed') return '场景失败';
    if (status === 'ready') return '待合成场景';
    if (status === 'queued') return '已入队';
    if (status === 'completed') return '已完成';
    if (status === 'running') return '生成中';
    return '待合成场景';
  }

  function videoStatusLabel(status) {
    if (status === 'submitted') return '视频已提交';
    if (status === 'completed') return '视频已完成';
    if (status === 'failed') return '视频失败';
    if (status === 'running') return '视频生成中';
    return '待合成视频';
  }

  function ensureMediaSelected() {
    if (!state.selectedPhoto) {
      showMessage('请先上传人物照片，或从素材库选择一张照片。', true);
      return false;
    }
    return true;
  }

  function requestPlan() {
    if (!ensureMediaSelected()) return;
    var base = localBase();
    if (!base) {
      showMessage('当前未检测到本机后端地址。', true);
      return;
    }
    state.submitting = true;
    updateButtons();
    var days = planDays();
    showMessage('正在生成' + days + '天方案...', false);
    fetch(base + '/api/local-bestseller/plan', {
      method: 'POST',
      headers: headersJson(),
      body: JSON.stringify({ days: days, profile: getProfile() })
    })
      .then(function(resp) {
        return resp.json().then(function(data) {
          return { ok: resp.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) throw new Error((result.data && result.data.detail) || '生成失败');
        state.bgmOptions = Array.isArray(result.data.bgm_options) ? result.data.bgm_options.map(function(option, idx) {
          return normalizeBgmOption(option, 'bgm-' + idx);
        }) : [];
        state.plan = normalizePlan(Array.isArray(result.data.items) ? result.data.items : []);
        ensureBgmOptions();
        renderPlan();
        showMessage(state.plan.length + '天同城爆款方案已生成。下一步可以单张或批量合成场景图片。', false);
      })
      .catch(function(err) {
        showMessage((err && err.message) || '生成失败', true);
      })
      .finally(function() {
        state.submitting = false;
        updateButtons();
      });
  }

  function mergeReturnedItems(items) {
    var byDay = {};
    (items || []).forEach(function(item) { byDay[String(item.day)] = item; });
    state.plan = state.plan.map(function(item) {
      return byDay[String(item.day)] ? normalizeItem(Object.assign({}, item, byDay[String(item.day)])) : item;
    });
  }

  function finalVideoUrlFromJob(data) {
    var result = data && data.result && typeof data.result === 'object' ? data.result : {};
    var captioned = result.captioned_video && typeof result.captioned_video === 'object' ? result.captioned_video : {};
    var bgmVideo = result.bgm_video && typeof result.bgm_video === 'object' ? result.bgm_video : {};
    var finalVideo = result.final_video && typeof result.final_video === 'object' ? result.final_video : {};
    var saved = Array.isArray(data.saved_assets) ? data.saved_assets : [];
    var savedUrl = '';
    saved.forEach(function(row) {
      var asset = row && row.asset && typeof row.asset === 'object' ? row.asset : {};
      var kind = String((row && row.kind) || asset.kind || '').toLowerCase();
      var url = asset.source_url || asset.open_url || '';
      if (!savedUrl && kind === 'local_bestseller_bgm_final' && url) {
        savedUrl = url;
        return;
      }
      if (!savedUrl && url) savedUrl = url;
    });
    return (
      finalVideo.url ||
      finalVideo.preview_url ||
      finalVideo.local_preview_url ||
      bgmVideo.source_url ||
      bgmVideo.open_url ||
      captioned.source_url ||
      captioned.open_url ||
      savedUrl ||
      ''
    );
  }

  function updateVideoItemFromJob(item, data) {
    if (!item || !data) return;
    var status = String(data.status || '').toLowerCase();
    var postStatus = String(data.post_status || '').toLowerCase();
    var postStage = String(data.post_stage || '').toLowerCase();
    var requiresCaption = !!data.requires_caption;
    var captionReady = !!data.caption_ready;
    var label = data.progress_label || data.progress_detail || '';
    var captioning = requiresCaption && !captionReady && (status === 'completed' || postStatus === 'captioning' || postStage === 'burn_subtitle');
    if (captioning) label = '字幕合成中';
    if (captioning && status === 'completed') {
      status = 'running';
    }
    if (status === 'completed') {
      item.video_status = 'completed';
      item.status = 'video_completed';
      item.video_progress_label = captionReady ? '字幕成片已完成' : '视频已完成';
      var url = finalVideoUrlFromJob(data);
      if (url) item.video_url = url;
      return;
    }
    if (status === 'failed') {
      item.video_status = 'failed';
      item.status = 'video_failed';
      item.video_progress_label = data.error || data.post_error || '视频生成失败';
      return;
    }
    item.video_status = 'running';
    item.video_progress_label = label || '视频生成中';
  }

  function toggleBgmPreview(dayOrId) {
    var item = findPlanItem(dayOrId);
    if (!item) return;
    var bgm = syncItemBgm(item);
    if (!bgm || !bgm.has_preview || !bgm.bgm_url) {
      showMessage('当前这首歌还没有可试听的音频地址。', true);
      return;
    }
    if (!state.bgmPlayer) {
      state.bgmPlayer = new Audio();
      state.bgmPlayer.preload = 'none';
      state.bgmPlayer.addEventListener('ended', function() {
        state.bgmPreviewKey = '';
        state.bgmPreviewUrl = '';
        renderPlan();
      });
      state.bgmPlayer.addEventListener('pause', function() {
        if (state.bgmPlayer && !state.bgmPlayer.ended) {
          state.bgmPreviewKey = '';
          state.bgmPreviewUrl = '';
          renderPlan();
        }
      });
    }
    if (state.bgmPreviewKey === bgm.key && state.bgmPreviewUrl === bgm.bgm_url && !state.bgmPlayer.paused) {
      state.bgmPlayer.pause();
      return;
    }
    state.bgmPlayer.src = bgm.bgm_url;
    state.bgmPlayer.currentTime = 0;
    state.bgmPlayer.volume = Math.max(0, Math.min(1, Number(bgm.volume) || 1));
    state.bgmPreviewKey = bgm.key;
    state.bgmPreviewUrl = bgm.bgm_url;
    state.bgmPlayer.play()
      .then(function() {
        renderPlan();
      })
      .catch(function(err) {
        state.bgmPreviewKey = '';
        state.bgmPreviewUrl = '';
        renderPlan();
        showMessage((err && err.message) || '背景音乐试听失败', true);
      });
  }

  function pollVideoJob(dayOrId) {
    var item = findPlanItem(dayOrId);
    if (!item || !item.video_task_id) return;
    var pollPath = item.video_poll_path || ('/api/comfly-seedance-tvc/pipeline/jobs/' + encodeURIComponent(item.video_task_id));
    var key = item.id || String(item.day);
    if (state.videoPolling[key]) return;
    state.videoPolling[key] = true;

    function once() {
      var current = findPlanItem(dayOrId);
      if (!current) {
        delete state.videoPolling[key];
        return;
      }
      fetch(localBase() + pollPath + (pollPath.indexOf('?') >= 0 ? '&' : '?') + 'compact=false', { headers: headersUpload() })
        .then(function(resp) {
          return resp.json().then(function(data) { return { ok: resp.ok, data: data || {} }; });
        })
        .then(function(result) {
          if (!result.ok) throw new Error((result.data && result.data.detail) || '视频任务状态查询失败');
          updateVideoItemFromJob(current, result.data);
          renderPlan();
          updateButtons();
          if (current.video_status === 'completed') {
            delete state.videoPolling[key];
            showMessage('Day ' + current.day + ' 视频已完成。', false);
            return;
          }
          if (current.video_status === 'failed') {
            delete state.videoPolling[key];
            showMessage('Day ' + current.day + ' 视频失败：' + (current.video_progress_label || ''), true);
            return;
          }
          window.setTimeout(once, 5000);
        })
        .catch(function(err) {
          var current = findPlanItem(dayOrId);
          if (current) {
            current.video_progress_label = (err && err.message) || '视频任务状态查询失败';
            renderPlan();
          }
          window.setTimeout(once, 8000);
        });
    }

    once();
  }

  function pollSubmittedVideos(items) {
    (items || []).forEach(function(item) {
      if (item && (item.video_task_id || item.video_job_id)) {
        pollVideoJob(item.day || item.id);
      }
    });
  }

  function generateScene(day) {
    if (!ensureMediaSelected()) return;
    if (!state.plan.length) {
      showMessage('请先生成' + planLabel() + '方案。', true);
      return;
    }
    var base = localBase();
    if (!base) {
      showMessage('当前未检测到本机后端地址。', true);
      return;
    }
    var item = state.plan.filter(function(row) { return Number(row.day) === Number(day); })[0];
    if (!item) return;
    state.sceneGenerating[item.id] = true;
    renderPlan();
    showMessage('正在合成 Day ' + day + ' 场景图片...', false);
    fetch(base + '/api/local-bestseller/scene/generate', {
      method: 'POST',
      headers: headersJson(),
      body: JSON.stringify({ days: state.plan.length || planDays(), day: Number(day), profile: getProfile(), item: cardPayload(item) })
    })
      .then(function(resp) {
        return resp.json().then(function(data) {
          return { ok: resp.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) throw new Error((result.data && result.data.detail) || '场景图片合成失败');
        mergeReturnedItems([result.data.item]);
        showMessage('Day ' + day + ' 场景图片已合成。', false);
      })
      .catch(function(err) {
        showMessage((err && err.message) || '场景图片合成失败', true);
      })
      .finally(function() {
        delete state.sceneGenerating[item.id];
        renderPlan();
        updateButtons();
      });
  }

  function batchGenerateScenes() {
    if (!ensureMediaSelected()) return;
    if (!state.plan.length) {
      showMessage('请先生成' + planLabel() + '方案。', true);
      return;
    }
    var base = localBase();
    if (!base) {
      showMessage('当前未检测到本机后端地址。', true);
      return;
    }
    state.submitting = true;
    var pending = state.plan.slice();
    var doneCount = 0;
    var failCount = 0;
    pending.forEach(function(item) { state.sceneGenerating[item.id] = true; });
    renderPlan();
    updateButtons();
    showMessage('正在并发合成' + pending.length + '天场景图片，完成一张会先显示一张...', false);
    Promise.all(pending.map(function(item) {
      return fetch(base + '/api/local-bestseller/scene/generate', {
        method: 'POST',
        headers: headersJson(),
        body: JSON.stringify({ days: state.plan.length || planDays(), day: Number(item.day), profile: getProfile(), item: cardPayload(item) })
      })
        .then(function(resp) {
          return resp.json().then(function(data) {
            return { ok: resp.ok, data: data || {}, item: item };
          });
        })
        .then(function(result) {
          doneCount += 1;
          if (!result.ok) throw new Error((result.data && result.data.detail) || ('Day ' + item.day + ' 场景图片合成失败'));
          mergeReturnedItems([result.data.item]);
          delete state.sceneGenerating[item.id];
          renderPlan();
          updateButtons();
          showMessage('场景图片进度：' + doneCount + '/' + pending.length + '，Day ' + item.day + ' 已完成。', false);
        })
        .catch(function(err) {
          doneCount += 1;
          failCount += 1;
          delete state.sceneGenerating[item.id];
          item.status = 'scene_failed';
          item.scene_status = 'failed';
          item.error = (err && err.message) || '场景图片合成失败';
          renderPlan();
          updateButtons();
          showMessage('场景图片进度：' + doneCount + '/' + pending.length + '，有 ' + failCount + ' 张失败，可单独重试。', true);
        });
    }))
      .finally(function() {
        state.submitting = false;
        state.sceneGenerating = {};
        renderPlan();
        updateButtons();
        if (failCount) {
          showMessage('批量场景图片已结束：成功 ' + (pending.length - failCount) + ' 张，失败 ' + failCount + ' 张。失败卡片可单独重试。', true);
        } else {
          showMessage(pending.length + '天场景图片已全部合成完成。', false);
        }
      });
  }

  function generateVideo(day) {
    if (!state.plan.length) {
      showMessage('请先生成' + planLabel() + '方案。', true);
      return;
    }
    var base = localBase();
    if (!base) {
      showMessage('当前未检测到本机后端地址。', true);
      return;
    }
    var item = state.plan.filter(function(row) { return Number(row.day) === Number(day); })[0];
    if (!item) return;
    if (!item.image_url && !item.image_asset_id && !item.scene_url && !item.scene_asset_id) {
      showMessage('请先给 Day ' + day + ' 上传/选择底图，或先合成场景图片，再合成视频。', true);
      return;
    }
    state.videoGenerating[item.id] = true;
    renderPlan();
    showMessage('正在提交 Day ' + day + ' Grok 10秒视频合成...', false);
    fetch(base + '/api/local-bestseller/video/generate', {
      method: 'POST',
      headers: headersJson(),
      body: JSON.stringify({ days: state.plan.length || planDays(), day: Number(day), profile: getProfile(), item: cardPayload(item), video_model: 'grok-imagine-video-1.5-preview' })
    })
      .then(function(resp) {
        return resp.json().then(function(data) {
          return { ok: resp.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) throw new Error((result.data && result.data.detail) || '视频合成提交失败');
        mergeReturnedItems([result.data.item]);
        pollSubmittedVideos([result.data.item]);
        showMessage('Day ' + day + ' 视频已提交到创意分镜 Grok 队列，卡片会自动刷新进度。', false);
      })
      .catch(function(err) {
        showMessage((err && err.message) || '视频合成提交失败', true);
      })
      .finally(function() {
        delete state.videoGenerating[item.id];
        renderPlan();
        updateButtons();
      });
  }

  function batchGenerateVideos() {
    if (!state.plan.length) {
      showMessage('请先生成' + planLabel() + '方案。', true);
      return;
    }
    var ready = state.plan.filter(function(item) { return item.image_url || item.image_asset_id || item.scene_url || item.scene_asset_id; });
    if (!ready.length) {
      showMessage('请先上传/选择底图，或先批量合成场景图片，再批量合成视频。', true);
      return;
    }
    var base = localBase();
    if (!base) {
      showMessage('当前未检测到本机后端地址。', true);
      return;
    }
    state.submitting = true;
    ready.forEach(function(item) { state.videoGenerating[item.id] = true; });
    renderPlan();
    updateButtons();
    showMessage('正在批量提交 Grok 10秒视频合成，已有合成图或底图的卡片会提交...', false);
    fetch(base + '/api/local-bestseller/video/batch', {
      method: 'POST',
      headers: headersJson(),
      body: JSON.stringify({ days: state.plan.length || planDays(), profile: getProfile(), items: planPayload(), video_model: 'grok-imagine-video-1.5-preview' })
    })
      .then(function(resp) {
        return resp.json().then(function(data) {
          return { ok: resp.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) throw new Error((result.data && result.data.detail) || '批量视频合成提交失败');
        var returned = Array.isArray(result.data.items) ? result.data.items : [];
        mergeReturnedItems(returned);
        pollSubmittedVideos(returned);
        showMessage('批量视频已提交到创意分镜 Grok 队列，卡片会自动刷新进度。', false);
      })
      .catch(function(err) {
        showMessage((err && err.message) || '批量视频合成提交失败', true);
      })
      .finally(function() {
        state.submitting = false;
        state.videoGenerating = {};
        renderPlan();
        updateButtons();
      });
  }

  function updateButtons() {
    var planBtn = $('localBestsellerPlanBtn');
    var renderBtn = $('localBestsellerRenderBtn');
    var batchSceneTopBtn = $('localBestsellerBatchSceneTopBtn');
    var batchVideoTopBtn = $('localBestsellerBatchVideoTopBtn');
    var batchSceneUploadBtn = $('localBestsellerBatchSceneUploadBtn');
    var batchSceneAssetBtn = $('localBestsellerBatchSceneAssetBtn');
    var hasPlan = !!state.plan.length;
    var hasSceneImage = state.plan.some(function(item) { return !!(item.image_url || item.image_asset_id || item.scene_url || item.scene_asset_id); });
    if (planBtn) {
      planBtn.disabled = state.submitting;
      planBtn.textContent = state.submitting ? '处理中...' : '生成' + planLabel() + '方案';
    }
    if (renderBtn) {
      renderBtn.disabled = state.submitting || !hasPlan;
      renderBtn.textContent = state.submitting ? '合成中...' : '批量合成' + (state.plan.length || planDays()) + '天场景图';
    }
    var title = $('localBestsellerResultTitle');
    if (title) title.textContent = (state.plan.length || planDays()) + '天结果卡';
    var emptyHint = $('localBestsellerEmptyHint');
    if (emptyHint) emptyHint.textContent = '先选择照片并填写个人信息，再生成' + planLabel() + '同城爆款方案。';
    if (batchSceneUploadBtn) batchSceneUploadBtn.disabled = state.submitting || !hasPlan;
    if (batchSceneAssetBtn) batchSceneAssetBtn.disabled = state.submitting || !hasPlan;
    if (batchSceneTopBtn) batchSceneTopBtn.disabled = state.submitting || !hasPlan;
    if (batchVideoTopBtn) batchVideoTopBtn.disabled = state.submitting || !hasPlan || !hasSceneImage;
  }

  function resetAll() {
    if (state.bgmPlayer) {
      state.bgmPlayer.pause();
      state.bgmPlayer.src = '';
    }
    state.bgmPreviewKey = '';
    state.bgmPreviewUrl = '';
    state.bgmOptions = [];
    state.selectedPhoto = null;
    state.selectedVideo = null;
    state.plan = [];
    state.sceneGenerating = {};
    state.videoGenerating = {};
    showMessage('');
    renderSelectedMedia();
    renderPlan();
    updateButtons();
  }

  function localBestsellerFullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement || null;
  }

  function requestLocalBestsellerFullscreen(el) {
    if (!el) return Promise.reject(new Error('no fullscreen target'));
    var fn = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
    if (!fn) return Promise.reject(new Error('fullscreen unsupported'));
    var result = fn.call(el);
    return result && typeof result.then === 'function' ? result : Promise.resolve();
  }

  function exitLocalBestsellerFullscreen() {
    var fn = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
    if (!fn) return Promise.resolve();
    var result = fn.call(document);
    return result && typeof result.then === 'function' ? result : Promise.resolve();
  }

  function syncLocalBestsellerVideoFullscreenState() {
    var active = localBestsellerFullscreenElement();
    document.querySelectorAll('[data-lb-video-shell]').forEach(function(shell) {
      shell.classList.toggle('is-lb-video-fullscreen', !!active && (active === shell || shell.contains(active)));
    });
  }

  function bindEvents() {
    var back = $('localBestsellerBackBtn');
    if (back) back.addEventListener('click', function() {
      var nav = document.querySelector('.nav-left-item[data-view="chat"]');
      if (nav) nav.click();
    });
    var upload = $('localBestsellerPhotoInput');
    var uploadBtn = $('localBestsellerPhotoBtn');
    if (uploadBtn && upload) uploadBtn.addEventListener('click', function() { upload.click(); });
    if (upload) upload.addEventListener('change', function(event) {
      var file = event.target.files && event.target.files[0];
      if (file) uploadFile(file, 'image').catch(function(err) { showMessage(err.message || '上传失败', true); });
      upload.value = '';
    });
    var videoInput = $('localBestsellerVideoInput');
    var videoBtn = $('localBestsellerVideoBtn');
    if (videoBtn && videoInput) videoBtn.addEventListener('click', function() { videoInput.click(); });
    if (videoInput) videoInput.addEventListener('change', function(event) {
      var file = event.target.files && event.target.files[0];
      if (file) uploadFile(file, 'video').catch(function(err) { showMessage(err.message || '上传失败', true); });
      videoInput.value = '';
    });
    var assetBtn = $('localBestsellerAssetBtn');
    if (assetBtn) assetBtn.addEventListener('click', openAssetPicker);
    var closeBtn = $('localBestsellerAssetClose');
    if (closeBtn) closeBtn.addEventListener('click', closeAssetPicker);
    var applyBatchSceneAssetsBtn = $('localBestsellerApplyBatchSceneAssets');
    if (applyBatchSceneAssetsBtn) applyBatchSceneAssetsBtn.addEventListener('click', applySelectedBatchSceneAssets);
    var planBtn = $('localBestsellerPlanBtn');
    if (planBtn) planBtn.addEventListener('click', function() { requestPlan(); });
    var renderBtn = $('localBestsellerRenderBtn');
    if (renderBtn) renderBtn.addEventListener('click', batchGenerateScenes);
    var batchSceneTopBtn = $('localBestsellerBatchSceneTopBtn');
    if (batchSceneTopBtn) batchSceneTopBtn.addEventListener('click', batchGenerateScenes);
    var batchVideoTopBtn = $('localBestsellerBatchVideoTopBtn');
    if (batchVideoTopBtn) batchVideoTopBtn.addEventListener('click', batchGenerateVideos);
    var cardSceneInput = $('localBestsellerCardSceneInput');
    if (cardSceneInput) cardSceneInput.addEventListener('change', function(event) {
      var file = event.target.files && event.target.files[0];
      var day = Number(cardSceneInput.getAttribute('data-day') || 0);
      if (file && day) uploadSceneFileForDay(file, day).catch(function(err) { showMessage(err.message || '上传场景底图失败', true); });
      cardSceneInput.value = '';
      cardSceneInput.removeAttribute('data-day');
    });
    var batchSceneInput = $('localBestsellerBatchSceneInput');
    if (batchSceneInput) batchSceneInput.addEventListener('change', function(event) {
      uploadBatchSceneFiles(event.target.files).catch(function(err) { showMessage(err.message || '批量上传底图失败', true); });
      batchSceneInput.value = '';
    });
    var batchSceneUploadBtn = $('localBestsellerBatchSceneUploadBtn');
    if (batchSceneUploadBtn && batchSceneInput) batchSceneUploadBtn.addEventListener('click', function() { batchSceneInput.click(); });
    var batchSceneAssetBtn = $('localBestsellerBatchSceneAssetBtn');
    if (batchSceneAssetBtn) batchSceneAssetBtn.addEventListener('click', openBatchSceneAssetPicker);
    var resetBtn = $('localBestsellerResetBtn');
    if (resetBtn) resetBtn.addEventListener('click', resetAll);
    var gender = $('localBestsellerGender');
    if (gender) gender.addEventListener('change', function() {
      var identity = $('localBestsellerIdentity');
      if (!identity) return;
      var current = String(identity.value || '').trim();
      if (gender.value === 'male' && (!current || current === '女老板')) identity.value = '男老板';
      if (gender.value !== 'male' && (!current || current === '男老板')) identity.value = '女老板';
    });
    var daysSelect = $('localBestsellerDays');
    if (daysSelect) daysSelect.addEventListener('change', updateButtons);
    document.addEventListener('input', function(event) {
      var edit = event.target.closest('[data-lb-edit-day][data-lb-edit-path]');
      if (!edit) return;
      var item = findPlanItem(edit.getAttribute('data-lb-edit-day'));
      if (!item) return;
      setNestedValue(item, edit.getAttribute('data-lb-edit-path'), edit.value || '');
      updateButtons();
    });
    document.addEventListener('click', function(event) {
      var bgmOption = event.target.closest('[data-lb-bgm-option][data-lb-bgm-key]');
      if (bgmOption) {
        applyBgmSelection(bgmOption.getAttribute('data-lb-bgm-option'), bgmOption.getAttribute('data-lb-bgm-key'));
        return;
      }
      var bgmToggle = event.target.closest('[data-lb-bgm-toggle]');
      if (bgmToggle) {
        toggleBgmMenu(bgmToggle.getAttribute('data-lb-bgm-toggle'));
        return;
      }
      if (!event.target.closest('[data-lb-bgm-picker]')) closeBgmMenu();
      var asset = event.target.closest('[data-lb-asset-id]');
      if (asset) {
        pickAsset(asset.getAttribute('data-lb-asset-id'));
        return;
      }
      var scene = event.target.closest('[data-lb-scene]');
      if (scene) {
        generateScene(scene.getAttribute('data-lb-scene'));
        return;
      }
      var uploadSceneRef = event.target.closest('[data-lb-upload-scene-ref]');
      if (uploadSceneRef) {
        var input = $('localBestsellerCardSceneInput');
        if (input) {
          input.setAttribute('data-day', uploadSceneRef.getAttribute('data-lb-upload-scene-ref'));
          input.click();
        }
        return;
      }
      var pickSceneRef = event.target.closest('[data-lb-pick-scene-ref]');
      if (pickSceneRef) {
        openCardSceneAssetPicker(pickSceneRef.getAttribute('data-lb-pick-scene-ref'));
        return;
      }
      var clearSceneRef = event.target.closest('[data-lb-clear-scene-ref]');
      if (clearSceneRef) {
        var clearItem = findPlanItem(clearSceneRef.getAttribute('data-lb-clear-scene-ref'));
        if (clearItem) {
          clearItem.scene_asset_id = '';
          clearItem.scene_url = '';
          clearItem.scene_preview_url = '';
          clearItem.scene_name = '';
          clearItem.prefer_scene_for_video = false;
          renderPlan();
          updateButtons();
        }
        return;
      }
      var video = event.target.closest('[data-lb-video]');
      if (video) {
        generateVideo(video.getAttribute('data-lb-video'));
        return;
      }
      var bgmPreview = event.target.closest('[data-lb-bgm-preview]');
      if (bgmPreview) {
        toggleBgmPreview(bgmPreview.getAttribute('data-lb-bgm-preview'));
        return;
      }
      var videoFull = event.target.closest('[data-lb-video-fullscreen]');
      if (videoFull) {
        event.preventDefault();
        event.stopPropagation();
        var shell = videoFull.closest('[data-lb-video-shell]');
        var player = shell ? shell.querySelector('video') : null;
        if (player) player.play().catch(function() {});
        requestLocalBestsellerFullscreen(shell).then(syncLocalBestsellerVideoFullscreenState).catch(function() {
          showMessage('当前环境不支持全屏预览。', true);
        });
        return;
      }
      var videoExit = event.target.closest('[data-lb-video-exit]');
      if (videoExit) {
        event.preventDefault();
        event.stopPropagation();
        exitLocalBestsellerFullscreen().then(syncLocalBestsellerVideoFullscreenState).catch(function() {});
        return;
      }
      if (event.target.closest('[data-lb-remove-photo]')) {
        state.selectedPhoto = null;
        renderSelectedMedia();
      }
      if (event.target.closest('[data-lb-remove-video]')) {
        state.selectedVideo = null;
        renderSelectedMedia();
      }
    });
    document.addEventListener('fullscreenchange', syncLocalBestsellerVideoFullscreenState);
    document.addEventListener('webkitfullscreenchange', syncLocalBestsellerVideoFullscreenState);
    document.addEventListener('MSFullscreenChange', syncLocalBestsellerVideoFullscreenState);
    document.addEventListener('keydown', function(event) {
      if (event.key !== 'Escape' || !localBestsellerFullscreenElement()) return;
      exitLocalBestsellerFullscreen().then(syncLocalBestsellerVideoFullscreenState).catch(function() {});
    }, true);
  }

  window.initLocalBestsellerView = function() {
    if (state.initialized) return;
    state.initialized = true;
    bindEvents();
    renderSelectedMedia();
    renderPlan();
    updateButtons();
    loadBgmOptions();
  };

  window._openLocalBestsellerView = function() {
    var target = 'local-bestseller';
    try { location.hash = target; } catch (e) {}
    document.querySelectorAll('.nav-left-item').forEach(function(b) { b.classList.remove('active'); });
    document.querySelectorAll('.content-block').forEach(function(p) { p.classList.remove('visible'); });
    var contentEl = document.getElementById('content-' + target);
    if (contentEl) contentEl.classList.add('visible');
    window.initLocalBestsellerView();
    loadBgmOptions();
  };

  function openFromHashIfNeeded() {
    var hash = String(window.location.hash || '').replace(/^#/, '').split(':')[0];
    if (hash === 'local-bestseller') window._openLocalBestsellerView();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', openFromHashIfNeeded);
  else openFromHashIfNeeded();
})();
