/* 作品提取：同行列表（一行一家）→ 二级详情页（同步 / 下载视频 / 提炼文案） */
(function() {
  var STORE_KEY = 'lobster_wct_results_v1';
  var WASM_BASE = '/static/vendor/wechat-video-decode';
  var KEYSTREAM_BYTES = 131072;
  var PLATFORM_LABEL = { douyin: '抖音', wechat_channels: '视频号' };
  var state = { peers: [], candidates: [], peer: null, items: [], selected: new Set(), results: {}, tasks: {}, filter: '', wasmPromise: null, keystreamCache: {} };

  function $(id) { return document.getElementById(id); }
  function esc(text) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(text == null ? '' : text));
    return String(text == null ? '' : text).replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }
  function apiBase() { return (typeof API_BASE !== 'undefined' && API_BASE ? String(API_BASE) : '').replace(/\/$/, ''); }
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
  /**
   * 本机接口一律走 XMLHttpRequest：app.js 会全局改写 window.fetch（把网络类错误统一成
   * 「连接正在自动恢复，请稍后重试」并自带重试），本机长任务（下载/转写）和 4xx 会被它干扰、
   * 真实原因也看不出来。
   */
  function localJson(path, body, timeoutMs) {
    return new Promise(function(resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', localBase() + path, true);
      xhr.timeout = timeoutMs || 30 * 60 * 1000;
      var hdr = jsonHeaders();
      Object.keys(hdr).forEach(function(name) {
        if (name.toLowerCase() === 'content-type') return;
        try { xhr.setRequestHeader(name, hdr[name]); } catch (e) {}
      });
      try { xhr.setRequestHeader('Content-Type', 'application/json'); } catch (e) {}
      xhr.onload = function() {
        var data = {};
        try { data = JSON.parse(xhr.responseText || '{}'); } catch (e) {}
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(parseErr(data, '本机服务返回 HTTP ' + xhr.status)));
          return;
        }
        if (data && data.ok === false) { reject(new Error(parseErr(data, '本机处理失败'))); return; }
        resolve(data);
      };
      xhr.onerror = function() { reject(new Error('连不上本机服务（请确认客户端在运行）')); };
      xhr.ontimeout = function() { reject(new Error('本机处理超时，请稍后重试')); };
      xhr.send(JSON.stringify(body || {}));
    });
  }
  /** 头像：底下垫首字母，图片加载失败自动隐藏（不再图裂） */
  function avatarCell(avatarUrl, name, size) {
    var url = thumbUrl(avatarUrl);
    var letter = String(name || '?').trim().charAt(0) || '?';
    var img = url
      ? '<img src="' + esc(url) + '" alt="" loading="lazy" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;" onerror="this.style.display=\'none\'">'
      : '';
    return '<span style="position:relative;display:inline-block;width:' + size + 'px;height:' + size + 'px;border-radius:50%;'
      + 'background:#eef2f7;color:#64748b;font-weight:800;text-align:center;line-height:' + size + 'px;'
      + 'font-size:' + Math.round(size * 0.42) + 'px;overflow:hidden;vertical-align:-6px;margin-right:6px;">'
      + esc(letter) + img + '</span>';
  }
  function thumbUrl(raw) {
    var url = String(raw || '').trim();
    if (!url) return '';
    if (url.indexOf('/api/wechat-channels-transcript/local-thumb') >= 0) return url;
    return localBase() + '/api/wechat-channels-transcript/local-thumb?url=' + encodeURIComponent(url);
  }
  function setMsg(text, isError) {
    var box = $('wctMsg');
    if (!box) return;
    if (!text) { box.className = 'wct-msg'; box.textContent = ''; return; }
    box.className = 'wct-msg ' + (isError ? 'err' : 'ok');
    box.textContent = text;
  }
  function setTask(text, isError) {
    var bar = $('wctTaskBar');
    if (!bar) return;
    if (!text) { bar.className = 'wct-statusbar'; bar.textContent = ''; return; }
    bar.className = 'wct-statusbar on' + (isError ? ' err' : '');
    bar.textContent = text;
  }
  function platformLabel(value) { return PLATFORM_LABEL[value] || value || ''; }
  function fmtCount(value) {
    var n = Number(value || 0);
    if (!n) return '';
    if (n >= 10000) return (n / 10000).toFixed(1) + ' 万';
    return String(n);
  }
  function fmtTime(value) { var t = String(value || '').trim(); return t ? t.replace('T', ' ').slice(0, 16) : ''; }
  function getPath(obj, path) {
    var cur = obj, parts = String(path || '').split('.');
    for (var i = 0; i < parts.length; i += 1) {
      if (cur == null) return '';
      if (Array.isArray(cur)) cur = cur[Number(parts[i])];
      else if (typeof cur === 'object') cur = cur[parts[i]];
      else return '';
    }
    return cur == null ? '' : cur;
  }
  function decodeKey(video) {
    var raw = video && video.raw && typeof video.raw === 'object' ? video.raw : {};
    return String(
      (video && video.decode_key) || getPath(raw, 'decode_key') || getPath(raw, 'decodeKey') ||
      getPath(raw, 'object_desc.media.0.decode_key') || getPath(raw, 'objectDesc.media.0.decode_key') ||
      getPath(raw, 'objectDesc.mediaList.0.decode_key') || getPath(raw, 'media.0.decode_key') ||
      getPath(raw, 'mediaList.0.decode_key') || ''
    ).trim();
  }
  function itemVideoUrl(item) {
    if (item && item.video_url) return String(item.video_url);
    var raw = item && item.raw && typeof item.raw === 'object' ? item.raw : {};
    return String(getPath(raw, 'video.play_addr.url_list.0') || getPath(raw, 'play_url') || '');
  }
  function itemTitle(item) {
    var title = String((item && (item.title || item.description)) || '').trim();
    return title || String((item && item.item_key) || '未命名作品');
  }
  function bytesToBase64(bytes) {
    var chunk = 0x8000, parts = [];
    for (var i = 0; i < bytes.length; i += chunk) parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + chunk)));
    return btoa(parts.join(''));
  }

  /* ── 视频号解密参数（WASM，沿用原实现）── */
  function ensureWasm() {
    if (window.Module && window.Module.WxIsaac64) return Promise.resolve(window.Module);
    if (state.wasmPromise) return state.wasmPromise;
    state.wasmPromise = new Promise(function(resolve, reject) {
      var base = localBase();
      var wasmUrl = base + WASM_BASE + '/wasm_video_decode.wasm';
      var scriptUrl = base + WASM_BASE + '/wasm_video_decode.js';
      var done = false, pollTimer = null, timeoutTimer = null;
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
      module.locateFile = function(path) { return String(path || '').endsWith('.wasm') ? wasmUrl : path; };
      module.onRuntimeInitialized = function() {
        if (typeof previousInit === 'function') { try { previousInit(); } catch (e) {} }
        finish();
      };
      window.Module = module;
      pollTimer = setInterval(finish, 100);
      timeoutTimer = setTimeout(function() {
        clearInterval(pollTimer);
        if (!done) { done = true; reject(new Error('视频号解密模块加载超时')); }
      }, 60000);
      var script = document.createElement('script');
      script.src = scriptUrl;
      script.async = true;
      script.onload = function() { finish(); setTimeout(finish, 300); };
      script.onerror = function() {
        if (pollTimer) clearInterval(pollTimer);
        if (timeoutTimer) clearTimeout(timeoutTimer);
        if (!done) { done = true; reject(new Error('视频号解密模块加载失败')); }
      };
      document.head.appendChild(script);
    });
    state.wasmPromise = state.wasmPromise.catch(function(err) { state.wasmPromise = null; throw err; });
    return state.wasmPromise;
  }
  function keystreamFor(item) {
    var key = decodeKey(item);
    if (!key) return Promise.resolve('');
    if (state.keystreamCache[key]) return Promise.resolve(state.keystreamCache[key]);
    return ensureWasm().then(function(Module) {
      window.__wctIsaacBytes = null;
      var instance = new Module.WxIsaac64(key);
      instance.generate(KEYSTREAM_BYTES);
      if (instance.delete) instance.delete();
      var bytes = window.__wctIsaacBytes;
      if (!bytes || bytes.length < KEYSTREAM_BYTES) throw new Error('视频号解密参数生成失败');
      var b64 = bytesToBase64(bytes);
      state.keystreamCache[key] = b64;
      return b64;
    });
  }

  function loadStore() {
    try { state.results = JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {}; } catch (e) { state.results = {}; }
  }
  function saveStore() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(state.results)); } catch (e) {}
  }
  function resultKey(item) { return (state.peer ? state.peer.id : 0) + ':' + String((item && item.item_key) || ''); }
  function itemKey(item, index) { return String((item && item.item_key) || index); }
  function itemTask(index) { return state.tasks[String(index)] || null; }
  function setItemTask(index, text, kind) {
    state.tasks[String(index)] = text ? { text: text, kind: kind || 'run' } : null;
    var el = document.querySelector('[data-wct-task="' + index + '"]');
    if (!el) return;
    if (!text) { el.className = 'wct-task'; el.textContent = ''; }
    else { el.className = 'wct-task ' + (kind || 'run'); el.textContent = text; }
  }

  /* ── 一级：同行列表（一行一家 + 检索）── */
  function loadPeers() {
    return apiJson('/api/ip-content/competitors').then(function(data) {
      state.peers = Array.isArray(data.items) ? data.items : [];
      renderPeers();
      return state.peers;
    }).catch(function(err) {
      setMsg(err.message || '同行列表加载失败', true);
      state.peers = [];
      renderPeers();
      return [];
    });
  }
  function peersFiltered() {
    var keyword = String(state.filter || '').trim().toLowerCase();
    if (!keyword) return state.peers;
    return state.peers.filter(function(peer) {
      var meta = peer.meta || {};
      return [peer.display_name, peer.account_key, peer.industry_tags, meta.unique_id, meta.username, meta.nickname]
        .filter(Boolean).join(' ').toLowerCase().indexOf(keyword) >= 0;
    });
  }
  function peerAccount(peer) {
    var meta = peer.meta || {};
    return meta.unique_id || meta.username || peer.account_key || '';
  }
  function peerStat(peer) {
    var meta = peer.meta || {}, parts = [];
    if (meta.follower_count) parts.push('粉丝 ' + fmtCount(meta.follower_count));
    if (meta.like_count) parts.push('赞 ' + fmtCount(meta.like_count));
    if (meta.aweme_count) parts.push('作品 ' + meta.aweme_count);
    if (meta.verify_info) parts.push(meta.verify_info);
    return parts.join(' · ');
  }
  function renderPeers() {
    var host = $('wctPeerList');
    var count = $('wctPeerCount');
    if (!host) return;
    var rows = peersFiltered();
    if (count) count.textContent = state.peers.length ? (rows.length + ' / ' + state.peers.length + ' 家同行') : '';
    if (!state.peers.length) {
      host.className = 'wct-empty';
      host.innerHTML = '还没有同行，点右上角「添加同行」。';
      return;
    }
    host.className = 'wct-table';
    var head = '<div class="wct-thead"><span>同行</span><span>平台</span><span>账号</span><span>数据</span><span>上次同步</span><span style="text-align:right;">操作</span></div>';
    if (!rows.length) {
      host.innerHTML = head + '<div class="wct-trow"><span class="wct-cell" style="grid-column:1/-1;">没有匹配的同行</span></div>';
      return;
    }
    host.innerHTML = head + rows.map(function(peer) {
      var meta = peer.meta || {};
      var avatarHtml = avatarCell(meta.avatar_url, peer.display_name || peer.account_key, 22);
      return '<div class="wct-trow">'
        + '<span class="wct-cell name" title="' + esc(peer.display_name) + '">' + avatarHtml + esc(peer.display_name || peer.account_key) + '</span>'
        + '<span class="wct-cell"><span class="wct-badge ' + esc(peer.platform) + '">' + esc(platformLabel(peer.platform)) + '</span></span>'
        + '<span class="wct-cell mono" title="' + esc(peerAccount(peer)) + '">' + esc(peerAccount(peer)) + '</span>'
        + '<span class="wct-cell" title="' + esc(peerStat(peer)) + '">' + esc(peerStat(peer) || '—') + '</span>'
        + '<span class="wct-cell">' + esc(fmtTime(peer.last_fetch_at) || '还没同步') + '</span>'
        + '<span class="wct-trow-actions">'
        + '<button type="button" class="btn btn-ghost btn-sm" data-wct-peer-sync="' + esc(peer.id) + '">同步</button>'
        + '<button type="button" class="btn btn-ghost btn-sm" data-wct-peer-delete="' + esc(peer.id) + '">删除</button>'
        + '<button type="button" class="btn btn-primary btn-sm" data-wct-peer-detail="' + esc(peer.id) + '">详情</button>'
        + '</span></div>';
    }).join('');
  }
  function findPeer(id) {
    var target = String(id || '');
    for (var i = 0; i < state.peers.length; i += 1) if (String(state.peers[i].id) === target) return state.peers[i];
    return null;
  }
  function syncPeer(id, btn) {
    var peer = findPeer(id);
    if (btn) { btn.disabled = true; btn.textContent = '同步中…'; }
    setTask('正在同步「' + ((peer && peer.display_name) || id) + '」的最新作品…');
    return apiJson('/api/ip-content/competitors/' + encodeURIComponent(id) + '/sync', { method: 'POST', body: { count: 20 } })
      .then(function(data) {
        var n = (data.items && data.items.length) || 0;
        setTask('同步完成：本次入库 ' + n + ' 条作品。');
        setMsg('同步完成，本次入库 ' + n + ' 条。');
        return loadPeers().then(function() {
          if (state.peer && String(state.peer.id) === String(id)) return loadItems(state.peer);
          return null;
        });
      })
      .catch(function(err) { setTask('同步失败：' + (err.message || ''), true); setMsg(err.message || '同步失败', true); })
      .finally(function() { if (btn) { btn.disabled = false; btn.textContent = '同步'; } });
  }
  function deletePeer(id) {
    if (!window.confirm('删除这家同行？已同步的作品不会删除。')) return Promise.resolve();
    var peer = findPeer(id);
    return apiJson('/api/ip-content/competitors/' + encodeURIComponent(id), { method: 'DELETE' })
      .then(function() {
        setMsg('已删除同行 ' + ((peer && peer.display_name) || ''));
        if (state.peer && String(state.peer.id) === String(id)) closeDetail();
        return loadPeers();
      })
      .catch(function(err) { setMsg(err.message || '删除失败', true); });
  }

  /* ── 添加同行弹窗 ── */
  function openAddModal() {
    var modal = $('wctAddModal');
    if (modal) modal.hidden = false;
    state.candidates = [];
    renderCandidates();
    updatePlatformFields();
  }
  function closeAddModal() { var modal = $('wctAddModal'); if (modal) modal.hidden = true; }
  function currentPlatform() { return ($('wctPlatform') && $('wctPlatform').value) || 'douyin'; }
  function updatePlatformFields() {
    var platform = currentPlatform();
    var input = $('wctSearchInput');
    var byId = $('wctAddByIdBtn');
    if (input) input.placeholder = platform === 'douyin' ? '输入抖音昵称或抖音号' : '输入视频号昵称、sph 开头公开 ID 或 username';
    if (byId) byId.hidden = platform !== 'wechat_channels';
  }
  function searchCandidates() {
    var platform = currentPlatform();
    var keyword = (($('wctSearchInput') && $('wctSearchInput').value) || '').trim();
    if (!keyword) { setMsg('请先输入要搜索的昵称或账号', true); return; }
    var btn = $('wctSearchBtn');
    if (btn) { btn.disabled = true; btn.textContent = '搜索中…'; }
    var url = platform === 'wechat_channels'
      ? '/api/ip-content/wechat-channels/users/search?q=' + encodeURIComponent(keyword)
      : '/api/ip-content/douyin/users/search?q=' + encodeURIComponent(keyword);
    apiJson(url).then(function(data) {
      state.candidates = Array.isArray(data.items) ? data.items : [];
      renderCandidates();
      setMsg(state.candidates.length ? ('搜到 ' + state.candidates.length + ' 个账号，选一个添加。') : '没有搜到匹配账号，换个关键字再试。', !state.candidates.length);
    }).catch(function(err) {
      state.candidates = [];
      renderCandidates();
      setMsg(err.message || '搜索失败', true);
    }).finally(function() { if (btn) { btn.disabled = false; btn.textContent = '搜索'; } });
  }
  function renderCandidates() {
    var host = $('wctCandidates');
    if (!host) return;
    if (!state.candidates.length) { host.innerHTML = ''; return; }
    host.innerHTML = state.candidates.map(function(cand, index) {
      var avatarHtml = avatarCell(cand.avatar_url, cand.display_name || cand.nickname || cand.unique_id, 40);
      var name = cand.display_name || cand.nickname || cand.unique_id || '';
      var line = [cand.unique_id ? ('抖音号 ' + cand.unique_id) : '', cand.follower_count ? ('粉丝 ' + fmtCount(cand.follower_count)) : '', cand.verify_info || ''].filter(Boolean).join(' · ');
      return '<div class="wct-cand">' + avatarHtml
        + '<div style="min-width:0;flex:1;"><div style="font-weight:800;">' + esc(name) + '</div>'
        + '<div class="wct-meta">' + esc(line) + '</div>'
        + '<div class="wct-meta" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc(cand.signature || '') + '</div></div>'
        + '<button type="button" class="btn btn-primary btn-sm" data-wct-add-cand="' + index + '">添加</button></div>';
    }).join('');
  }
  function addCandidate(index) {
    var cand = state.candidates[Number(index)];
    if (!cand) return;
    var platform = currentPlatform();
    var accountKey = platform === 'wechat_channels'
      ? String(cand.username || cand.finder_username || cand.id || '').trim()
      : String(cand.sec_user_id || cand.sec_uid || cand.id || '').trim();
    if (!accountKey) { setMsg(platform === 'wechat_channels' ? '该候选缺少 username，不能添加' : '该候选缺少 sec_user_id，不能添加', true); return; }
    var payload = {
      platform: platform,
      account_key: accountKey,
      display_name: String(cand.display_name || cand.nickname || '').trim(),
      homepage_url: String(cand.homepage_url || '').trim(),
      industry_tags: (($('wctTagsInput') && $('wctTagsInput').value) || '').trim(),
      meta: {
        source: platform === 'wechat_channels' ? 'wechat_channels_user_search' : 'douyin_user_search',
        unique_id: cand.unique_id || '', username: cand.username || cand.finder_username || '',
        uid: cand.uid || '', nickname: cand.nickname || cand.display_name || '',
        follower_count: cand.follower_count || 0, aweme_count: cand.aweme_count || 0,
        like_count: cand.like_count || 0, signature: cand.signature || '',
        avatar_url: cand.avatar_url || '', verify_info: cand.verify_info || ''
      }
    };
    setTask('正在添加同行…');
    apiJson('/api/ip-content/competitors', { method: 'POST', body: payload }).then(function() {
      setTask('同行已添加，进详情点「同步作品」拉作品。');
      state.candidates = [];
      renderCandidates();
      if ($('wctSearchInput')) $('wctSearchInput').value = '';
      closeAddModal();
      return loadPeers();
    }).catch(function(err) { setTask('添加失败：' + (err.message || ''), true); setMsg(err.message || '添加同行失败', true); });
  }
  function addByPublicId() {
    var channelId = (($('wctSearchInput') && $('wctSearchInput').value) || '').trim();
    if (!channelId) { setMsg('请先输入视频号公开 ID（sph 开头）', true); return; }
    setTask('正在按公开 ID 添加…');
    apiJson('/api/ip-content/wechat-channels/competitors/by-channel-id', {
      method: 'POST',
      body: { channel_id: channelId, industry_tags: (($('wctTagsInput') && $('wctTagsInput').value) || '').trim() }
    }).then(function() {
      setTask('已按公开 ID 添加视频号同行。');
      closeAddModal();
      return loadPeers();
    }).catch(function(err) { setTask('添加失败：' + (err.message || ''), true); setMsg(err.message || '按公开 ID 添加失败', true); });
  }

  /* ── 二级：详情页（不是弹窗；进入不自动同步）── */
  function openDetail(id) {
    var peer = findPeer(id);
    if (!peer) return;
    state.peer = peer;
    state.selected = new Set();
    state.items = [];
    state.tasks = {};
    if ($('wctListView')) $('wctListView').hidden = true;
    if ($('wctDetailView')) $('wctDetailView').hidden = false;
    if ($('wctDetailTitle')) $('wctDetailTitle').textContent = peer.display_name || peer.account_key;
    var badge = $('wctDetailBadge');
    if (badge) { badge.className = 'wct-badge ' + peer.platform; badge.textContent = platformLabel(peer.platform); }
    var meta = $('wctDetailMeta');
    if (meta) meta.textContent = [peerAccount(peer), peerStat(peer), peer.last_fetch_at ? ('上次同步 ' + fmtTime(peer.last_fetch_at)) : '还没同步过'].filter(Boolean).join('　');
    setTask('');
    renderItems();
    renderResults();
    return loadItems(peer);
  }
  function closeDetail() {
    state.peer = null; state.items = []; state.selected = new Set(); state.tasks = {};
    if ($('wctListView')) $('wctListView').hidden = false;
    if ($('wctDetailView')) $('wctDetailView').hidden = true;
    setTask('');
  }
  /**
   * 作品来源按平台分开：
   * - 视频号：走原有的 /api/wechat-channels-transcript/videos（返回带 url_token / decode_key 的条目，
   *   这是原来就能下载+解密提取的那条链路；同步入库的 raw 里没有 token，直接下会报 token not exist）
   * - 抖音：用同步入库的条目（raw.video.play_addr 就是可下载直链）
   */
  function loadItems(peer) {
    var host = $('wctItemList');
    if (host) host.innerHTML = '<div class="wct-empty">正在拉取作品…</div>';
    if (peer.platform === 'wechat_channels') {
      var username = peerAccount(peer);
      return apiJson('/api/wechat-channels-transcript/videos', {
        method: 'POST',
        body: { username: username, max_pages: 8, page_size: 20 }
      }).then(function(data) {
        state.items = Array.isArray(data.items) ? data.items : [];
        renderItems();
        return state.items;
      }).catch(function(err) {
        state.items = [];
        renderItems();
        setTask('作品拉取失败：' + (err.message || ''), true);
        return [];
      });
    }
    var url = '/api/ip-content/source-items?source_type=user_post&limit=200&competitor_id=' + encodeURIComponent(peer.id)
      + '&platform=' + encodeURIComponent(peer.platform);
    return apiJson(url).then(function(data) {
      state.items = Array.isArray(data.items) ? data.items : [];
      renderItems();
      return state.items;
    }).catch(function(err) {
      state.items = [];
      renderItems();
      setTask('作品读取失败：' + (err.message || ''), true);
      return [];
    });
  }
  function renderItems() {
    var host = $('wctItemList');
    var hint = $('wctItemHint');
    if (hint) hint.textContent = state.items.length
      ? (state.peer && state.peer.platform === 'wechat_channels' ? ('已拉取 ' + state.items.length + ' 条作品。') : ('已入库 ' + state.items.length + ' 条，点右上角「同步作品」拉最新。'))
      : (state.peer && state.peer.platform === 'wechat_channels' ? '点右上角「同步作品」或直接拉取。' : '还没同步过，点右上角「同步作品」。');
    if (!host) return;
    if (!state.items.length) { host.innerHTML = '<div class="wct-empty">暂无作品（点右上角「同步作品」）。</div>'; return; }
    host.innerHTML = state.items.map(function(item, index) {
      var cover = thumbUrl(item.cover_url);
      var coverHtml = cover
        ? '<img class="wct-cover" src="' + esc(cover) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
        : '<div class="wct-cover"></div>';
      var meta = [];
      if (item.publish_time) meta.push(fmtTime(item.publish_time));
      var metrics = item.metrics || {};
      var likes = metrics.digg_count || metrics.like_count || metrics.likeCount;
      if (likes) meta.push('赞 ' + fmtCount(likes));
      var comments = metrics.comment_count || metrics.commentCount;
      if (comments) meta.push('评 ' + fmtCount(comments));
      var stored = state.results[resultKey(item)];
      var task = itemTask(index);
      var statusHtml = task
        ? ('<span class="wct-task ' + esc(task.kind) + '" data-wct-task="' + index + '">' + esc(task.text) + '</span>')
        : '<span class="wct-task ' + (stored && stored.transcript ? 'done' : '') + '" data-wct-task="' + index + '">' + (stored && stored.transcript ? '已提取' : '') + '</span>';
      return '<article class="wct-item">'
        + '<input type="checkbox" data-wct-pick="' + index + '"' + (state.selected.has(itemKey(item, index)) ? ' checked' : '') + '>'
        + coverHtml
        + '<div><div class="wct-item-title">' + esc(itemTitle(item)) + '</div>'
        + '<div class="wct-meta" style="margin-top:2px;">' + esc(meta.join(' · ')) + ' ' + statusHtml + '</div>'
        + '<div class="wct-item-actions">'
        + '<button type="button" class="btn btn-ghost btn-sm" data-wct-download="' + index + '">下载视频</button>'
        + '<button type="button" class="btn btn-ghost btn-sm" data-wct-extract="' + index + '">提炼文案</button>'
        + (item.public_url ? '<a class="btn btn-ghost btn-sm" href="' + esc(item.public_url) + '" target="_blank" rel="noopener">打开原页</a>' : '')
        + '</div></div></article>';
    }).join('');
  }
  function togglePick(index, checked) {
    var item = state.items[Number(index)];
    if (!item) return;
    var key = itemKey(item, index);
    if (checked) state.selected.add(key); else state.selected.delete(key);
  }
  function pickAll(checked) {
    state.selected = new Set();
    if (checked) state.items.forEach(function(item, index) { state.selected.add(itemKey(item, index)); });
    renderItems();
  }
  function urlExpired(err) {
    var msg = String((err && err.message) || '');
    return /403|forbidden|expired|过期|失效/i.test(msg);
  }
  function itemByKey(key) {
    for (var i = 0; i < state.items.length; i += 1) {
      if (String(state.items[i].item_key) === String(key)) return state.items[i];
    }
    return null;
  }
  /** 作品链接是带签名的，会过期（403）：自动同步一次刷新链接再重试。 */
  function runWithRefresh(item, index, label, action) {
    var key = item.item_key;
    return action(item).catch(function(err) {
      if (!urlExpired(err) || !state.peer) throw err;
      setItemTask(index, '链接过期，同步中…', 'run');
      setTask(label + '：作品链接已过期（403），正在同步最新作品后重试…');
      return syncPeer(state.peer.id).then(function() {
        var fresh = itemByKey(key);
        if (!fresh) throw err;
        setTask(label + '：链接已刷新，重试中…');
        return action(fresh);
      });
    });
  }
  function downloadItem(index) {
    var item = state.items[Number(index)];
    if (!item) return;
    var url = itemVideoUrl(item);
    if (!url) {
      setItemTask(index, '无视频地址', 'fail');
      setTask('这条作品没有可下载的视频地址（public_url 是分享页、raw 里也没有直链）。', true);
      return;
    }
    setItemTask(index, '下载中…', 'run');
    setTask('正在下载：' + itemTitle(item).slice(0, 30));
    runWithRefresh(item, index, '下载视频', function (target) {
      return keystreamFor(target).then(function(keystream) {
        return localJson('/api/wechat-channels-transcript/local-download', { video: target, keystream_b64: keystream });
      });
    }).then(function(data) {
      setItemTask(index, '已下载', 'done');
      setTask('已下载到：' + (data.path || '') + (data.opened ? '（已打开文件夹）' : ''));
    }).catch(function(err) {
      setItemTask(index, '下载失败', 'fail');
      setTask('下载失败：' + (err.message || ''), true);
    });
  }
  function extractItem(item, index) {
    var url = itemVideoUrl(item);
    if (!url) return Promise.reject(new Error('这条作品没有可提取的视频地址'));
    function once(target) {
      return keystreamFor(target).then(function(keystream) {
        return localJson('/api/wechat-channels-transcript/local-transcribe', { video: target, keystream_b64: keystream });
      }).then(function(data) {
        var transcript = String(data.transcript || '').trim();
        if (!transcript) throw new Error('转写完成但没有返回文案');
        state.results[resultKey(target)] = { title: itemTitle(target), transcript: transcript, at: Date.now() };
        saveStore();
        return transcript;
      });
    }
    if (typeof index === 'number') return runWithRefresh(item, index, '提炼文案', once);
    return once(item);
  }
  function extractOne(index) {
    var item = state.items[Number(index)];
    if (!item) return;
    setItemTask(index, '提炼中…', 'run');
    setTask('正在提炼：' + itemTitle(item).slice(0, 30));
    extractItem(item, index).then(function() {
      setItemTask(index, '已提取', 'done');
      renderItems();
      renderResults();
      setTask('文案已提炼完成。');
    }).catch(function(err) {
      setItemTask(index, '失败', 'fail');
      setTask('提炼失败：' + (err.message || ''), true);
    });
  }
  function extractSelected() {
    if (!state.peer) return;
    var picked = [];
    state.items.forEach(function(item, index) { if (state.selected.has(itemKey(item, index))) picked.push(index); });
    if (!picked.length) { setTask('请先勾选要提炼的作品。', true); return; }
    var btn = $('wctExtractBtn');
    if (btn) { btn.disabled = true; btn.textContent = '提炼中…'; }
    var failed = 0, chain = Promise.resolve();
    picked.forEach(function(index, order) {
      chain = chain.then(function() {
        var item = state.items[index];
        setItemTask(index, '提炼中…', 'run');
        setTask('正在提炼 ' + (order + 1) + '/' + picked.length + '：' + itemTitle(item).slice(0, 26));
        return extractItem(item, index).then(function() { setItemTask(index, '已提取', 'done'); })
          .catch(function() { failed += 1; setItemTask(index, '失败', 'fail'); });
      });
    });
    chain.then(function() {
      renderItems();
      renderResults();
      setTask(failed ? ('提炼完成，失败 ' + failed + ' 条。') : ('提炼完成，共 ' + picked.length + ' 条。'), !!failed);
    }).finally(function() { if (btn) { btn.disabled = false; btn.textContent = '批量提炼文案'; } });
  }

  function resultRows() {
    if (!state.peer) return [];
    return state.items.map(function(item) {
      var stored = state.results[resultKey(item)];
      return stored && stored.transcript ? stored : null;
    }).filter(Boolean);
  }
  function renderResults() {
    var host = $('wctResultList');
    if (!host) return;
    var rows = resultRows();
    if (!rows.length) { host.innerHTML = '<div class="wct-empty">暂无结果</div>'; return; }
    host.innerHTML = rows.map(function(row) {
      return '<div class="wct-result"><div class="wct-item-title">' + esc(row.title) + '</div>'
        + '<textarea readonly>' + esc(row.transcript) + '</textarea>'
        + '<div class="wct-item-actions"><button type="button" class="btn btn-ghost btn-sm" data-wct-copy="1">复制这条</button></div></div>';
    }).join('');
  }
  function copyText(text) {
    try { navigator.clipboard.writeText(text); setTask('已复制到剪贴板。'); }
    catch (e) { setTask('复制失败，请手动选择文本。', true); }
  }
  function downloadFile(filename, content, type) {
    var blob = new Blob([content], { type: type || 'text/plain;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function() { URL.revokeObjectURL(url); }, 3000);
  }
  function exportTxt() {
    var rows = resultRows();
    if (!rows.length) { setTask('还没有可导出的文案。', true); return; }
    downloadFile('作品提取_' + (state.peer ? state.peer.platform : '') + '.txt',
      rows.map(function(row, i) { return '【' + (i + 1) + '】' + row.title + '\n' + row.transcript; }).join('\n\n'));
  }
  function exportCsv() {
    var rows = resultRows();
    if (!rows.length) { setTask('还没有可导出的文案。', true); return; }
    function cell(value) { return '"' + String(value == null ? '' : value).replace(/"/g, '""') + '"'; }
    downloadFile('作品提取_' + (state.peer ? state.peer.platform : '') + '.csv',
      '\ufeff' + ['标题,文案'].concat(rows.map(function(row) { return cell(row.title) + ',' + cell(row.transcript); })).join('\r\n'),
      'text/csv;charset=utf-8');
  }

  function bind() {
    var on = function(id, event, handler) { var el = $(id); if (el) el.addEventListener(event, handler); };
    on('wctRefreshBtn', 'click', function() { setMsg(''); setTask(''); loadPeers(); });
    on('wctAddPeerBtn', 'click', openAddModal);
    on('wctAddClose', 'click', closeAddModal);
    on('wctPlatform', 'change', updatePlatformFields);
    on('wctSearchBtn', 'click', searchCandidates);
    on('wctAddByIdBtn', 'click', addByPublicId);
    on('wctSearchInput', 'keydown', function(ev) { if (ev.key === 'Enter') { ev.preventDefault(); searchCandidates(); } });
    on('wctPeerFilter', 'input', function(ev) { state.filter = (ev.target && ev.target.value) || ''; renderPeers(); });
    on('wctDetailBack', 'click', closeDetail);
    on('wctSyncBtn', 'click', function() { if (state.peer) syncPeer(state.peer.id, $('wctSyncBtn')); });
    on('wctSelectAllBtn', 'click', function() { pickAll(true); });
    on('wctClearSelectBtn', 'click', function() { pickAll(false); });
    on('wctExtractBtn', 'click', extractSelected);
    on('wctExportTxtBtn', 'click', exportTxt);
    on('wctExportCsvBtn', 'click', exportCsv);
    on('wctPeerList', 'click', function(ev) {
      var el = ev.target;
      if (!el || !el.getAttribute) return;
      if (el.getAttribute('data-wct-peer-sync')) syncPeer(el.getAttribute('data-wct-peer-sync'), el);
      else if (el.getAttribute('data-wct-peer-delete')) deletePeer(el.getAttribute('data-wct-peer-delete'));
      else if (el.getAttribute('data-wct-peer-detail')) openDetail(el.getAttribute('data-wct-peer-detail'));
    });
    on('wctCandidates', 'click', function(ev) {
      var el = ev.target;
      if (el && el.getAttribute && el.getAttribute('data-wct-add-cand') !== null) addCandidate(el.getAttribute('data-wct-add-cand'));
    });
    on('wctItemList', 'change', function(ev) {
      var el = ev.target;
      if (el && el.getAttribute && el.getAttribute('data-wct-pick') !== null) togglePick(el.getAttribute('data-wct-pick'), el.checked);
    });
    on('wctItemList', 'click', function(ev) {
      var el = ev.target;
      if (!el || !el.getAttribute) return;
      if (el.getAttribute('data-wct-download') !== null) downloadItem(el.getAttribute('data-wct-download'));
      else if (el.getAttribute('data-wct-extract') !== null) extractOne(el.getAttribute('data-wct-extract'));
    });
    on('wctResultList', 'click', function(ev) {
      var el = ev.target;
      if (!el || !el.getAttribute || el.getAttribute('data-wct-copy') === null) return;
      var box = el.closest ? el.closest('.wct-result') : null;
      var area = box ? box.querySelector('textarea') : null;
      if (area) copyText(area.value);
    });
    var mask = $('wctAddModal');
    if (mask) mask.addEventListener('click', function(ev) { if (ev.target === mask) closeAddModal(); });
  }

  function init() {
    loadStore();
    bind();
    loadPeers();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();

  window.wctReload = function() { loadStore(); return loadPeers(); };
})();
