(function() {
  var JOB_STORE_KEY = 'lobster_wechat_channels_transcript_jobs_v2';
  var LAST_ACCOUNT_KEY = 'lobster_wechat_channels_transcript_last_account_v1';
  var WASM_BASE = '/static/vendor/wechat-video-decode';
  var KEYSTREAM_BYTES = 131072;

  var state = {
    accounts: [],
    selectedAccount: null,
    videos: [],
    selectedKeys: new Set(),
    jobs: [],
    activeJobId: '',
    processing: false,
    wasmPromise: null,
    keystreamCache: {}
  };

  function $(id) { return document.getElementById(id); }

  function esc(text) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(text || ''));
    return String(text || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function apiBase() {
    return (typeof API_BASE !== 'undefined' && API_BASE ? String(API_BASE) : '').replace(/\/$/, '');
  }

  function localBase() {
    var base = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE ? String(LOCAL_API_BASE) : '').replace(/\/$/, '');
    return base || window.location.origin;
  }

  function jsonHeaders() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    if (!h.Authorization && typeof token !== 'undefined' && token) h.Authorization = 'Bearer ' + token;
    h['Content-Type'] = 'application/json';
    return h;
  }

  function parseErr(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.error || data.message || data.msg;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail.message === 'string') return detail.message;
    try { return JSON.stringify(detail || data); } catch (e) { return fallback || '请求失败'; }
  }

  function apiJson(path, opts) {
    opts = opts || {};
    var base = apiBase();
    if (!base) return Promise.reject(new Error('未配置服务器 API_BASE'));
    return fetch(base + path, {
      method: opts.method || 'GET',
      headers: jsonHeaders(),
      body: opts.body !== undefined ? JSON.stringify(opts.body || {}) : undefined
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseErr(data, '请求失败'));
        return data;
      });
    });
  }

  function localJson(path, body) {
    return fetch(localBase() + path, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(body || {})
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseErr(data, '本机处理失败'));
        return data;
      });
    });
  }

  function localGet(path) {
    return fetch(localBase() + path, {
      method: 'GET',
      headers: jsonHeaders()
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseErr(data, '本机读取失败'));
        return data;
      });
    });
  }

  function setMsg(text, isErr) {
    var node = $('wctMsg');
    if (!node) return;
    node.textContent = text || '';
    node.className = text ? 'wct-msg ' + (isErr ? 'err' : 'ok') : 'wct-msg';
  }

  function accountKey(account) {
    return String(account && (account.username || account.finder_username || account.id) || '');
  }

  function videoKey(video) {
    return String(video && (video.item_key || video.public_url || video.video_url) || '');
  }

  function getPath(obj, path) {
    var cur = obj;
    var parts = String(path || '').split('.');
    for (var i = 0; i < parts.length; i += 1) {
      if (cur && Array.isArray(cur)) cur = cur[Number(parts[i])];
      else if (cur && typeof cur === 'object') cur = cur[parts[i]];
      else return '';
    }
    return cur == null ? '' : cur;
  }

  function decodeKey(video) {
    var raw = video && video.raw && typeof video.raw === 'object' ? video.raw : {};
    return String(
      (video && video.decode_key) ||
      getPath(raw, 'decode_key') ||
      getPath(raw, 'decodeKey') ||
      getPath(raw, 'object_desc.media.0.decode_key') ||
      getPath(raw, 'objectDesc.media.0.decode_key') ||
      getPath(raw, 'objectDesc.mediaList.0.decode_key') ||
      getPath(raw, 'media.0.decode_key') ||
      getPath(raw, 'mediaList.0.decode_key') ||
      ''
    ).trim();
  }

  function compactRaw(video) {
    var raw = video && video.raw && typeof video.raw === 'object' ? video.raw : {};
    var mediaUrl = getPath(raw, 'object_desc.media.0.url') ||
      getPath(raw, 'objectDesc.media.0.url') ||
      getPath(raw, 'objectDesc.mediaList.0.url') ||
      getPath(raw, 'media.0.url') ||
      getPath(raw, 'mediaList.0.url') ||
      '';
    var mediaToken = getPath(raw, 'object_desc.media.0.url_token') ||
      getPath(raw, 'objectDesc.media.0.url_token') ||
      getPath(raw, 'objectDesc.mediaList.0.url_token') ||
      getPath(raw, 'media.0.url_token') ||
      getPath(raw, 'mediaList.0.url_token') ||
      '';
    var key = decodeKey(video);
    if (!mediaUrl && !mediaToken && !key) return {};
    return { object_desc: { media: [{ url: mediaUrl, url_token: mediaToken, decode_key: key }] } };
  }

  function compactVideo(video) {
    return {
      item_key: videoKey(video),
      title: video.title || '',
      publish_time: video.publish_time || '',
      video_url: video.video_url || '',
      public_url: video.public_url || '',
      cover_url: video.cover_url || '',
      metrics: video.metrics && typeof video.metrics === 'object' ? video.metrics : {},
      decode_key: decodeKey(video),
      raw: compactRaw(video)
    };
  }

  function statusLabel(status) {
    var map = {
      queued: '排队中',
      running: '处理中',
      completed: '完成',
      failed: '失败',
      pending: '待处理'
    };
    return map[status] || status || '-';
  }

  function nowText() {
    var d = new Date();
    function pad(n) { return n < 10 ? '0' + n : '' + n; }
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function saveJobs() {
    try {
      localStorage.setItem(JOB_STORE_KEY, JSON.stringify(state.jobs.slice(0, 50)));
    } catch (e) {
      console.warn('[wct] save jobs failed', e);
    }
    localJson('/api/wechat-channels-transcript/local-jobs', {
      jobs: state.jobs.slice(0, 50)
    }).catch(function(err) {
      console.warn('[wct] save jobs db failed', err);
    });
  }

  function applyCachedWorkspace(data, showMessage, keepAccounts) {
    if (!data || !data.account || !Array.isArray(data.videos)) return false;
    state.selectedAccount = data.account;
    if (!keepAccounts) state.accounts = [data.account];
    state.videos = data.videos || [];
    state.selectedKeys = new Set(Array.isArray(data.selected_keys) ? data.selected_keys : state.videos.map(videoKey).filter(Boolean));
    try { localStorage.setItem(LAST_ACCOUNT_KEY, accountKey(data.account)); } catch (e) {}
    renderAccounts();
    renderSelectedAccount();
    renderVideos();
    if (showMessage) setMsg('已恢复上次查询的视频列表：' + state.videos.length + ' 条', false);
    return true;
  }

  function rememberCurrentVideos() {
    if (!state.selectedAccount) return;
    var key = accountKey(state.selectedAccount);
    if (!key) return;
    var videos = (state.videos || []).map(compactVideo);
    try { localStorage.setItem(LAST_ACCOUNT_KEY, key); } catch (e) {}
    return localJson('/api/wechat-channels-transcript/local-cache', {
      account_key: key,
      account: state.selectedAccount,
      videos: videos,
      selected_keys: Array.from(state.selectedKeys || [])
    }).catch(function(err) {
      console.warn('[wct] save video db cache failed', err);
    });
  }

  function restoreVideosForAccount(account) {
    var key = accountKey(account);
    if (!key) return Promise.resolve(false);
    return localGet('/api/wechat-channels-transcript/local-cache?account_key=' + encodeURIComponent(key))
      .then(function(data) { return applyCachedWorkspace(data, true, true); })
      .catch(function() { return false; });
  }

  function restoreLastWorkspace() {
    var lastKey = '';
    try { lastKey = localStorage.getItem(LAST_ACCOUNT_KEY) || ''; } catch (e) {}
    var req = lastKey
      ? localGet('/api/wechat-channels-transcript/local-cache?account_key=' + encodeURIComponent(lastKey)).catch(function() {
          return localGet('/api/wechat-channels-transcript/local-cache/latest');
        })
      : localGet('/api/wechat-channels-transcript/local-cache/latest');
    return req.then(function(data) {
      return applyCachedWorkspace(data, false);
    }).catch(function() {
      return false;
    });
  }

  function loadLocalJobs() {
    function applyJobs(rows) {
      state.jobs = Array.isArray(rows) ? rows : [];
      var changed = false;
      state.jobs.forEach(function(job) {
        (job.items || []).forEach(function(item) {
          if (item.status === 'running' || item.status === 'queued') {
            item.status = 'pending';
            item.stage = '上次中断，待重试';
            changed = true;
          }
        });
        if (job.status === 'running' || job.status === 'queued') {
          job.status = 'failed';
          changed = true;
        }
      });
      if (changed) saveJobs();
      if (!state.activeJobId && state.jobs.length) state.activeJobId = state.jobs[0].job_id;
      renderJobs();
    }

    return localGet('/api/wechat-channels-transcript/local-jobs').then(function(data) {
      var rows = Array.isArray(data && data.jobs) ? data.jobs : [];
      if (rows.length) {
        applyJobs(rows);
        return;
      }
      var legacy = [];
      try {
        legacy = JSON.parse(localStorage.getItem(JOB_STORE_KEY) || '[]');
      } catch (e) {
        legacy = [];
      }
      applyJobs(Array.isArray(legacy) ? legacy : []);
      if (state.jobs.length) saveJobs();
    }).catch(function() {
      var rows = [];
      try {
        rows = JSON.parse(localStorage.getItem(JOB_STORE_KEY) || '[]');
      } catch (e) {
        rows = [];
      }
      applyJobs(Array.isArray(rows) ? rows : []);
    });
  }

  function activeJob() {
    return state.jobs.filter(function(job) { return job.job_id === state.activeJobId; })[0] || null;
  }

  function updateJob(job) {
    var found = false;
    state.jobs = state.jobs.map(function(item) {
      if (item.job_id === job.job_id) {
        found = true;
        return job;
      }
      return item;
    });
    if (!found) state.jobs.unshift(job);
    state.activeJobId = job.job_id;
    saveJobs();
    renderJobs();
  }

  function updateProgress(job) {
    var total = (job.items || []).length || 1;
    var done = (job.items || []).filter(function(item) {
      return item.status === 'completed' || item.status === 'failed';
    }).length;
    job.progress = Math.round(done / total * 100);
    var completed = (job.items || []).filter(function(item) { return item.status === 'completed'; }).length;
    var failed = (job.items || []).filter(function(item) { return item.status === 'failed'; }).length;
    if (done >= total) {
      job.status = completed > 0 ? 'completed' : 'failed';
      job.completed_at = nowText();
    } else if (done > 0 || job.status === 'running') {
      job.status = 'running';
    }
    job.result_payload = { count: total, completed_count: completed, failed_count: failed };
    updateJob(job);
  }

  function renderAccounts() {
    var list = $('wctAccountList');
    if (!list) return;
    if (!state.accounts.length) {
      list.className = 'wct-empty';
      list.innerHTML = '暂无账号候选';
      return;
    }
    list.className = 'wct-list';
    list.innerHTML = state.accounts.map(function(account) {
      var key = accountKey(account);
      var active = state.selectedAccount && accountKey(state.selectedAccount) === key ? ' is-active' : '';
      return '<div class="wct-item' + active + '" data-wct-account="' + esc(key) + '" style="cursor:pointer;">' +
        (account.avatar_url ? '<img src="' + esc(account.avatar_url) + '" style="width:36px;height:36px;border-radius:50%;object-fit:cover;">' : '<span></span>') +
        '<div><div class="wct-item-title">' + esc(account.display_name || account.nickname || key) + '</div>' +
        '<div class="wct-meta">' + esc(key) + '</div>' +
        '<div class="wct-meta">' + esc(account.signature || account.verify_info || '') + '</div></div>' +
        '<button type="button" class="btn btn-primary btn-sm">选择</button>' +
      '</div>';
    }).join('');
  }

  function renderSelectedAccount() {
    var node = $('wctSelectedAccount');
    if (!node) return;
    if (!state.selectedAccount) {
      node.textContent = '请先选择账号';
      return;
    }
    node.textContent = (state.selectedAccount.display_name || state.selectedAccount.nickname || accountKey(state.selectedAccount)) + ' · ' + accountKey(state.selectedAccount);
  }

  function renderVideos() {
    var list = $('wctVideoList');
    if (!list) return;
    if (!state.videos.length) {
      list.className = 'wct-empty';
      list.innerHTML = state.selectedAccount ? '暂无作品，或作品里没有可下载视频链接。' : '选择账号后点击“拉取作品”。';
      return;
    }
    list.className = 'wct-list';
    list.innerHTML = state.videos.map(function(video, idx) {
      var key = videoKey(video);
      var checked = state.selectedKeys.has(key);
      var canDecode = !!decodeKey(video);
      return '<label class="wct-item wct-video-row" style="cursor:pointer;">' +
        '<input type="checkbox" data-wct-video="' + esc(key) + '"' + (checked ? ' checked' : '') + '>' +
        (video.cover_url ? '<img class="wct-cover" src="' + esc(video.cover_url) + '">' : '<div class="wct-cover"></div>') +
        '<div><div class="wct-item-title">' + esc(video.title || ('视频 ' + (idx + 1))) + '</div>' +
        '<div class="wct-meta">' + esc(video.publish_time || '') + (canDecode ? '' : ' · 缺少 decode_key') + '</div>' +
        '<div class="wct-meta">' + esc(video.video_url || video.public_url || '') + '</div></div>' +
      '</label>';
    }).join('');
  }

  function renderJobs() {
    var list = $('wctJobList');
    if (!list) return;
    if (!state.jobs.length) {
      list.className = 'wct-empty';
      list.innerHTML = '暂无记录';
      renderResults();
      return;
    }
    list.className = 'wct-list';
    list.innerHTML = state.jobs.map(function(job) {
      var active = job.job_id === state.activeJobId ? ' is-active' : '';
      var count = (job.items || []).length || 0;
      return '<div class="wct-item' + active + '" data-wct-job="' + esc(job.job_id) + '" style="grid-template-columns:minmax(0,1fr) auto;cursor:pointer;">' +
        '<div><div class="wct-item-title">' + esc(job.title || job.job_id) + '</div>' +
        '<div class="wct-meta">' + esc(job.created_at || '') + '</div>' +
        '<div class="wct-meta">' + esc(count) + ' 条 · ' + esc(job.progress || 0) + '%</div></div>' +
        '<span class="wct-status ' + esc(job.status || '') + '">' + esc(statusLabel(job.status)) + '</span>' +
      '</div>';
    }).join('');
    renderResults();
  }

  function renderResults() {
    var host = $('wctResultList');
    var summary = $('wctJobSummary');
    var job = activeJob();
    if (!host) return;
    if (!job) {
      host.className = 'wct-empty';
      host.innerHTML = '点击左侧记录查看结果。';
      if (summary) summary.textContent = '点击左侧记录查看结果。';
      return;
    }
    var items = job.items || [];
    if (summary) summary.textContent = statusLabel(job.status) + ' · ' + (job.progress || 0) + '% · ' + items.length + ' 条';
    if (!items.length) {
      host.className = 'wct-empty';
      host.innerHTML = '任务暂无明细';
      return;
    }
    host.className = '';
    host.innerHTML = items.map(function(item, idx) {
      return '<div class="wct-output">' +
        '<div class="wct-row" style="justify-content:space-between;margin-bottom:8px;">' +
        '<div><strong>' + esc(idx + 1) + '. ' + esc(item.title || item.item_key || '') + '</strong><div class="wct-meta">' + esc(item.publish_time || '') + (item.stage ? ' · ' + esc(item.stage) : '') + '</div></div>' +
        '<span class="wct-status ' + esc(item.status || '') + '">' + esc(statusLabel(item.status)) + '</span></div>' +
        (item.error ? '<div class="wct-msg err" style="display:block;margin-bottom:8px;">' + esc(item.error) + '</div>' : '') +
        '<textarea readonly>' + esc(item.transcript || '') + '</textarea>' +
        '<div class="wct-actions" style="margin-top:8px;"><button type="button" class="btn btn-ghost btn-sm" data-wct-copy-one="' + esc(idx) + '">复制</button></div>' +
      '</div>';
    }).join('');
  }

  function selectedVideos() {
    var keys = [];
    document.querySelectorAll('[data-wct-video]').forEach(function(input) {
      if (input.checked) keys.push(String(input.getAttribute('data-wct-video') || ''));
    });
    state.selectedKeys = new Set(keys);
    var wanted = new Set(keys);
    return state.videos.filter(function(video) { return wanted.has(videoKey(video)); });
  }

  function bytesToBase64(bytes) {
    var binary = '';
    var chunk = 0x8000;
    for (var i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  function ensureWasm() {
    if (window.Module && window.Module.WxIsaac64) return Promise.resolve(window.Module);
    if (state.wasmPromise) return state.wasmPromise;
    state.wasmPromise = new Promise(function(resolve, reject) {
      var base = localBase();
      var wasmUrl = base + WASM_BASE + '/wasm_video_decode.wasm';
      var scriptUrl = base + WASM_BASE + '/wasm_video_decode.js';
      var done = false;
      var pollTimer = null;
      var timeoutTimer = null;
      function finish() {
        if (done) return;
        if (window.Module && window.Module.WxIsaac64) {
          done = true;
          if (pollTimer) clearInterval(pollTimer);
          if (timeoutTimer) clearTimeout(timeoutTimer);
          resolve(window.Module);
        }
      }
      window.__wctIsaacBytes = null;
      window.VTS_WASM_URL = wasmUrl;
      window.wasm_isaac_generate = function(ptr, size) {
        var heap = window.Module && window.Module.HEAPU8;
        if (!heap) return;
        var bytes = new Uint8Array(size);
        bytes.set(heap.subarray(ptr, ptr + size));
        bytes.reverse();
        window.__wctIsaacBytes = bytes;
      };
      var module = window.Module && typeof window.Module === 'object' ? window.Module : {};
      var previousInit = module.onRuntimeInitialized;
      module.locateFile = function(path) {
        return String(path || '').endsWith('.wasm') ? wasmUrl : path;
      };
      module.onRuntimeInitialized = function() {
        if (typeof previousInit === 'function') {
          try { previousInit(); } catch (e) {}
        }
        finish();
      };
      window.Module = module;
      pollTimer = setInterval(finish, 100);
      timeoutTimer = setTimeout(function() {
        clearInterval(pollTimer);
        if (!done) {
          done = true;
          reject(new Error('微信视频解密模块加载超时'));
        }
      }, 60000);
      var script = document.createElement('script');
      script.src = scriptUrl;
      script.async = true;
      script.onload = function() {
        finish();
        setTimeout(finish, 300);
      };
      script.onerror = function() {
        if (pollTimer) clearInterval(pollTimer);
        if (timeoutTimer) clearTimeout(timeoutTimer);
        if (!done) {
          done = true;
          reject(new Error('微信视频解密模块加载失败'));
        }
      };
      document.head.appendChild(script);
    });
    state.wasmPromise = state.wasmPromise.catch(function(err) {
      state.wasmPromise = null;
      throw err;
    });
    return state.wasmPromise;
  }

  function generateKeystreamBase64(key) {
    key = String(key || '').trim();
    if (state.keystreamCache[key]) return Promise.resolve(state.keystreamCache[key]);
    return ensureWasm().then(function(Module) {
      window.__wctIsaacBytes = null;
      var instance = new Module.WxIsaac64(key);
      instance.generate(KEYSTREAM_BYTES);
      if (instance.delete) instance.delete();
      var bytes = window.__wctIsaacBytes;
      if (!bytes || bytes.length < KEYSTREAM_BYTES) throw new Error('微信视频解密参数生成失败');
      var b64 = bytesToBase64(bytes);
      state.keystreamCache[key] = b64;
      return b64;
    });
  }

  function processJob(jobId) {
    if (state.processing) return Promise.resolve();
    var job = state.jobs.filter(function(item) { return item.job_id === jobId; })[0];
    if (!job) return Promise.resolve();
    state.processing = true;
    job.status = 'running';
    updateJob(job);
    var chain = Promise.resolve();
    (job.items || []).forEach(function(item, idx) {
      chain = chain.then(function() {
        if (item.status === 'completed') return null;
        item.status = 'running';
        item.stage = '生成解密参数';
        item.error = '';
        updateJob(job);
        var key = decodeKey(item);
        if (!key) throw new Error('缺少 decode_key，无法解密视频号文件');
        return generateKeystreamBase64(key).then(function(keystream) {
          item.stage = '本机下载视频并分离音频';
          updateJob(job);
          return localJson('/api/wechat-channels-transcript/local-transcribe', {
            video: item,
            keystream_b64: keystream
          });
        }).then(function(data) {
          item.status = 'completed';
          item.stage = '转写完成';
          item.transcript = data.transcript || '';
          item.audio_url = data.audio_url || '';
          item.stt_task_id = data.task_id || '';
          item.error = '';
          updateProgress(job);
          setMsg('已完成 ' + (idx + 1) + '/' + (job.items || []).length, false);
        });
      }).catch(function(err) {
        item.status = 'failed';
        item.stage = '处理失败';
        item.error = err && err.message ? err.message : String(err || '处理失败');
        updateProgress(job);
        setMsg(item.error, true);
      });
    });
    return chain.finally(function() {
      state.processing = false;
      updateProgress(job);
    });
  }

  function searchAccounts() {
    var q = (($('wctKeywordInput') || {}).value || '').trim();
    if (!q) return setMsg('请输入视频号昵称或 username', true);
    setMsg('正在查询账号...', false);
    apiJson('/api/wechat-channels-transcript/users/search?q=' + encodeURIComponent(q)).then(function(data) {
      state.accounts = data.items || [];
      state.selectedAccount = null;
      state.videos = [];
      state.selectedKeys.clear();
      renderAccounts();
      renderSelectedAccount();
      renderVideos();
      setMsg(state.accounts.length ? '请选择要分析的视频号账号' : '没有搜到匹配账号', !state.accounts.length);
    }).catch(function(err) {
      setMsg(err.message || '账号查询失败', true);
    });
  }

  function fetchVideos() {
    if (!state.selectedAccount) return setMsg('请先选择账号', true);
    var username = accountKey(state.selectedAccount);
    setMsg('正在拉取作品...', false);
    apiJson('/api/wechat-channels-transcript/videos', {
      method: 'POST',
      body: { username: username, max_pages: 8, page_size: 20 }
    }).then(function(data) {
      state.videos = data.items || [];
      state.selectedKeys = new Set(state.videos.map(videoKey).filter(Boolean));
      rememberCurrentVideos();
      renderVideos();
      setMsg('作品拉取完成：' + state.videos.length + ' 条', false);
    }).catch(function(err) {
      setMsg(err.message || '作品拉取失败', true);
    });
  }

  function createJob() {
    if (!state.selectedAccount) return setMsg('请先选择账号', true);
    var videos = selectedVideos().map(compactVideo);
    if (!videos.length) return setMsg('请至少选择一个作品', true);
    var missing = videos.filter(function(video) { return !video.decode_key; }).length;
    if (missing) return setMsg('有 ' + missing + ' 个作品缺少 decode_key，无法解密提取', true);
    var job = {
      job_id: 'local_wct_' + Date.now(),
      status: 'queued',
      stage: 'queued',
      progress: 0,
      title: '视频号文案提取：' + (state.selectedAccount.display_name || state.selectedAccount.nickname || accountKey(state.selectedAccount)),
      created_at: nowText(),
      items: videos.map(function(video) {
        return Object.assign({}, video, { status: 'pending', stage: '待处理', transcript: '', error: '' });
      }),
      result_payload: { count: videos.length, completed_count: 0, failed_count: 0 }
    };
    updateJob(job);
    setMsg('已创建本机处理任务，开始下载视频并分离音频。', false);
    processJob(job.job_id);
  }

  function resumeJob() {
    var job = activeJob();
    if (!job) return setMsg('请先选择一个任务', true);
    if (state.processing) return setMsg('当前已有任务处理中', true);
    (job.items || []).forEach(function(item) {
      if (item.status !== 'completed') {
        item.status = 'pending';
        item.stage = '待重试';
        item.error = '';
      }
    });
    updateJob(job);
    processJob(job.job_id);
  }

  function allText(job) {
    job = job || activeJob();
    if (!job) return '';
    return (job.items || []).map(function(item, idx) {
      return '### ' + (idx + 1) + '. ' + (item.title || item.item_key || '') + '\n' + (item.transcript || '') + '\n';
    }).join('\n');
  }

  function downloadText(filename, text, type) {
    var blob = new Blob([text], { type: type || 'text/plain;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function() { URL.revokeObjectURL(url); a.remove(); }, 1000);
  }

  function exportCsv() {
    var job = activeJob();
    if (!job) return setMsg('请先选择一个任务', true);
    var rows = [['标题', '发布时间', '状态', '原链接', '视频链接', '转写文本']];
    (job.items || []).forEach(function(item) {
      rows.push([item.title || '', item.publish_time || '', statusLabel(item.status), item.public_url || '', item.video_url || '', item.transcript || item.error || '']);
    });
    var csv = rows.map(function(row) {
      return row.map(function(cell) { return '"' + String(cell || '').replace(/"/g, '""') + '"'; }).join(',');
    }).join('\r\n');
    downloadText('wechat-channels-transcripts.csv', '\ufeff' + csv, 'text/csv;charset=utf-8');
  }

  function bind(root) {
    var back = $('wctBackBtn');
    if (back) back.addEventListener('click', function() {
      var nav = document.querySelector('.nav-left-item[data-view="skill-store"]');
      if (nav) nav.click();
    });
    var search = $('wctSearchBtn');
    if (search) search.addEventListener('click', searchAccounts);
    var input = $('wctKeywordInput');
    if (input) input.addEventListener('keydown', function(evt) { if (evt.key === 'Enter') searchAccounts(); });
    var fetchBtn = $('wctFetchVideosBtn');
    if (fetchBtn) fetchBtn.addEventListener('click', fetchVideos);
    var allBtn = $('wctSelectAllBtn');
    if (allBtn) allBtn.addEventListener('click', function() {
      state.selectedKeys = new Set(state.videos.map(videoKey).filter(Boolean));
      renderVideos();
    });
    var clearBtn = $('wctClearSelectBtn');
    if (clearBtn) clearBtn.addEventListener('click', function() {
      state.selectedKeys.clear();
      renderVideos();
    });
    var createBtn = $('wctCreateJobBtn');
    if (createBtn) createBtn.addEventListener('click', createJob);
    var refreshBtn = $('wctRefreshJobsBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', loadLocalJobs);
    var resumeBtn = $('wctResumeJobBtn');
    if (resumeBtn) resumeBtn.addEventListener('click', resumeJob);
    var copyAll = $('wctCopyAllBtn');
    if (copyAll) copyAll.addEventListener('click', function() {
      var text = allText();
      if (!text) return setMsg('暂无可复制文本', true);
      navigator.clipboard.writeText(text).then(function() { setMsg('已复制全部文案', false); }).catch(function() { setMsg('复制失败', true); });
    });
    var exportTxt = $('wctExportTxtBtn');
    if (exportTxt) exportTxt.addEventListener('click', function() {
      var text = allText();
      if (!text) return setMsg('暂无可导出文本', true);
      downloadText('wechat-channels-transcripts.txt', text, 'text/plain;charset=utf-8');
    });
    var exportCsvBtn = $('wctExportCsvBtn');
    if (exportCsvBtn) exportCsvBtn.addEventListener('click', exportCsv);

    root.addEventListener('change', function(evt) {
      var video = evt.target.closest('[data-wct-video]');
      if (!video) return;
      var key = String(video.getAttribute('data-wct-video') || '');
      if (video.checked) state.selectedKeys.add(key);
      else state.selectedKeys.delete(key);
      if (state.selectedAccount) {
        localJson('/api/wechat-channels-transcript/local-cache/selection', {
          account_key: accountKey(state.selectedAccount),
          selected_keys: Array.from(state.selectedKeys || [])
        }).catch(function(err) {
          console.warn('[wct] save selection failed', err);
        });
      }
    });
    root.addEventListener('click', function(evt) {
      var account = evt.target.closest('[data-wct-account]');
      var job = evt.target.closest('[data-wct-job]');
      var copyOne = evt.target.closest('[data-wct-copy-one]');
      if (copyOne) {
        var active = activeJob();
        var idx = Number(copyOne.getAttribute('data-wct-copy-one'));
        var item = active && active.items && active.items[idx];
        var text = item && item.transcript || '';
        if (!text) return setMsg('这条暂无可复制文本', true);
        navigator.clipboard.writeText(text).then(function() { setMsg('已复制', false); }).catch(function() { setMsg('复制失败', true); });
        return;
      }
      if (job) {
        state.activeJobId = job.getAttribute('data-wct-job') || '';
        renderJobs();
        return;
      }
      if (account) {
        var key = account.getAttribute('data-wct-account') || '';
        state.selectedAccount = state.accounts.filter(function(item) { return accountKey(item) === key; })[0] || null;
        renderAccounts();
        renderSelectedAccount();
        restoreVideosForAccount(state.selectedAccount).then(function(restored) {
          if (!restored) {
            state.videos = [];
            state.selectedKeys.clear();
            renderVideos();
          }
        });
      }
    });
  }

  window.initWechatChannelsTranscriptView = function() {
    var root = $('content-wechat-channels-transcript');
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';
    bind(root);
    renderAccounts();
    renderSelectedAccount();
    renderVideos();
    restoreLastWorkspace().then(function(restored) {
      if (!restored) {
        renderAccounts();
        renderSelectedAccount();
        renderVideos();
      }
    });
    loadLocalJobs();
  };
})();
