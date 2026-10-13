/* 视频号 / 抖音 文案提取：同行 → 详情（同步 / 下载视频 / 提炼文案） */
(function() {
  var STORE_KEY = 'lobster_wct_results_v1';
  var WASM_BASE = '/static/vendor/wechat-video-decode';
  var KEYSTREAM_BYTES = 131072;
  var PLATFORM_LABEL = { douyin: '抖音', wechat_channels: '视频号' };

  var state = {
    peers: [],
    candidates: [],
    peer: null,
    items: [],
    selected: new Set(),
    results: {},
    detailOpen: false,
    wasmPromise: null,
    keystreamCache: {}
  };

  function $(id) { return document.getElementById(id); }
  function esc(text) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(text == null ? '' : text));
    return String(text == null ? '' : text).replace(/[&<>"']/g, function(ch) {
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
        if (!resp.ok) throw new Error(parseErr(data, '本机服务请求失败'));
        return data;
      });
    });
  }
  function setMsg(text, isError) {
    var box = $('wctMsg');
    if (!box) return;
    if (!text) { box.className = 'wct-msg'; box.textContent = ''; return; }
    box.className = 'wct-msg ' + (isError ? 'err' : 'ok');
    box.textContent = text;
  }
  function platformLabel(value) { return PLATFORM_LABEL[value] || value || ''; }
  function fmtCount(value) {
    var n = Number(value || 0);
    if (!n) return '';
    if (n >= 10000) return (n / 10000).toFixed(1) + ' 万';
    return String(n);
  }
  function getPath(obj, path) {
    var cur = obj;
    var parts = String(path || '').split('.');
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
  function itemVideoUrl(item) {
    if (item && item.video_url) return String(item.video_url);
    var raw = item && item.raw && typeof item.raw === 'object' ? item.raw : {};
    return String(
      getPath(raw, 'video.play_addr.url_list.0') ||
      getPath(raw, 'video.play_addr.url_list.1') ||
      getPath(raw, 'video.play_addr_h264.url_list.0') ||
      getPath(raw, 'aweme_detail.video.play_addr.url_list.0') ||
      getPath(raw, 'play_url') ||
      ''
    );
  }
  function itemTitle(item) {
    var title = String((item && (item.title || item.description)) || '').trim();
    return title || String((item && item.item_key) || '未命名作品');
  }
  function bytesToBase64(bytes) {
    var chunk = 0x8000;
    var parts = [];
    for (var i = 0; i < bytes.length; i += chunk) {
      parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + chunk)));
    }
    return btoa(parts.join(''));
  }

  /* ── 视频号解密参数（WASM，原样保留）── */
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
  function generateKeystreamBase64(key) {
    key = String(key || '').trim();
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
  function keystreamFor(item) {
    var key = decodeKey(item);
    if (!key) return Promise.resolve('');
    return generateKeystreamBase64(key);
  }

  /* ── 结果本地存档 ── */
  function loadStore() {
    try { state.results = JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {}; } catch (e) { state.results = {}; }
  }
  function saveStore() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(state.results)); } catch (e) {}
  }
  function resultKey(item) { return (state.peer ? state.peer.id : 0) + ':' + String((item && item.item_key) || ''); }

  /* ── 同行列表 ── */
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
  function peerFields(peer) {
    var meta = (peer && peer.meta) || {};
    if (peer.platform === 'douyin') {
      return [
        ['抖音号', meta.unique_id || meta.douyin_id || '—'],
        ['粉丝', fmtCount(meta.follower_count) || '—'],
        ['获赞', fmtCount(meta.like_count) || '—'],
        ['作品', meta.aweme_count ? String(meta.aweme_count) : '—'],
        ['简介', meta.signature || '—']
      ];
    }
    return [
      ['视频号 ID', meta.username || peer.account_key || '—'],
      ['粉丝', fmtCount(meta.follower_count) || '—'],
      ['认证', meta.verify_info || '—'],
      ['简介', meta.signature || '—']
    ];
  }
  function renderPeers() {
    var host = $('wctPeerList');
    var count = $('wctPeerCount');
    if (!host) return;
    if (count) count.textContent = state.peers.length ? ('共 ' + state.peers.length + ' 家同行') : '';
    if (!state.peers.length) {
      host.className = 'wct-empty';
      host.innerHTML = '还没有同行，点右上角「添加同行」。';
      return;
    }
    host.className = 'wct-peer-list';
    host.innerHTML = state.peers.map(function(peer) {
      var meta = peer.meta || {};
      var avatar = meta.avatar_url ? '<img class="wct-avatar" src="' + esc(meta.avatar_url) + '" alt="">' : '<div class="wct-avatar"></div>';
      var rows = peerFields(peer).map(function(pair) {
        return '<b>' + esc(pair[0]) + '</b><span>' + esc(pair[1]) + '</span>';
      }).join('');
      var synced = peer.last_fetch_at ? ('上次同步 ' + String(peer.last_fetch_at).replace('T', ' ').slice(0, 16)) : '还没同步过';
      return '<article class="wct-peer">'
        + '<div class="wct-peer-head">' + avatar
        + '<div style="min-width:0;"><div class="wct-peer-name">' + esc(peer.display_name || peer.account_key) + '</div>'
        + '<span class="wct-badge ' + esc(peer.platform) + '">' + esc(platformLabel(peer.platform)) + '</span></div></div>'
        + '<div class="wct-fields">' + rows + '</div>'
        + '<div class="wct-meta">' + esc(synced) + (peer.industry_tags ? ' · ' + esc(peer.industry_tags) : '') + '</div>'
        + '<div class="wct-peer-foot">'
        + '<button type="button" class="btn btn-ghost btn-sm" data-wct-peer-sync="' + esc(peer.id) + '">同步</button>'
        + '<button type="button" class="btn btn-ghost btn-sm" data-wct-peer-delete="' + esc(peer.id) + '">删除</button>'
        + '<button type="button" class="btn btn-primary btn-sm" data-wct-peer-detail="' + esc(peer.id) + '">详情</button>'
        + '</div></article>';
    }).join('');
  }
  function findPeer(id) {
    var target = String(id || '');
    for (var i = 0; i < state.peers.length; i += 1) {
      if (String(state.peers[i].id) === target) return state.peers[i];
    }
    return null;
  }
  function syncPeer(id, btn) {
    if (btn) { btn.disabled = true; btn.textContent = '同步中…'; }
    var peer = findPeer(id);
    setMsg('正在同步 ' + ((peer && peer.display_name) || '同行') + ' 的最新作品…');
    return apiJson('/api/ip-content/competitors/' + encodeURIComponent(id) + '/sync', { method: 'POST', body: { count: 20 } })
      .then(function(data) {
        var n = (data.items && data.items.length) || 0;
        setMsg('同步完成，本次入库 ' + n + ' 条作品。');
        return loadPeers().then(function() {
          if (state.detailOpen && state.peer && String(state.peer.id) === String(id)) return loadItems(state.peer);
          return null;
        });
      })
      .catch(function(err) { setMsg(err.message || '同步失败', true); })
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
  function closeAddModal() {
    var modal = $('wctAddModal');
    if (modal) modal.hidden = true;
  }
  function currentPlatform() {
    return ($('wctPlatform') && $('wctPlatform').value) || 'douyin';
  }
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
      setMsg(state.candidates.length ? ('搜到 ' + state.candidates.length + ' 个账号，选一个添加。') : '没有搜到匹配账号，换个昵称再试。', !state.candidates.length);
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
      var avatar = cand.avatar_url ? '<img class="wct-avatar" src="' + esc(cand.avatar_url) + '" alt="">' : '<div class="wct-avatar"></div>';
      var name = cand.display_name || cand.nickname || cand.unique_id || '';
      var line = [cand.unique_id ? ('抖音号 ' + cand.unique_id) : '', cand.follower_count ? ('粉丝 ' + fmtCount(cand.follower_count)) : '', cand.verify_info || '']
        .filter(Boolean).join(' · ');
      return '<div class="wct-cand">' + avatar
        + '<div style="min-width:0;flex:1;"><div class="wct-peer-name">' + esc(name) + '</div><div class="wct-meta">' + esc(line) + '</div>'
        + '<div class="wct-meta">' + esc(cand.signature || '') + '</div></div>'
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
        unique_id: cand.unique_id || '',
        username: cand.username || cand.finder_username || '',
        uid: cand.uid || '',
        nickname: cand.nickname || cand.display_name || '',
        follower_count: cand.follower_count || 0,
        aweme_count: cand.aweme_count || 0,
        like_count: cand.like_count || 0,
        signature: cand.signature || '',
        avatar_url: cand.avatar_url || '',
        verify_info: cand.verify_info || ''
      }
    };
    apiJson('/api/ip-content/competitors', { method: 'POST', body: payload }).then(function() {
      setMsg('同行已添加，进详情点「同步作品」即可拉作品。');
      state.candidates = [];
      renderCandidates();
      if ($('wctSearchInput')) $('wctSearchInput').value = '';
      closeAddModal();
      return loadPeers();
    }).catch(function(err) { setMsg(err.message || '添加同行失败', true); });
  }
  function addByPublicId() {
    var channelId = (($('wctSearchInput') && $('wctSearchInput').value) || '').trim();
    if (!channelId) { setMsg('请先输入视频号公开 ID（sph 开头）', true); return; }
    apiJson('/api/ip-content/wechat-channels/competitors/by-channel-id', {
      method: 'POST',
      body: { channel_id: channelId, industry_tags: (($('wctTagsInput') && $('wctTagsInput').value) || '').trim() }
    }).then(function() {
      setMsg('已按公开 ID 添加视频号同行。');
      closeAddModal();
      return loadPeers();
    }).catch(function(err) { setMsg(err.message || '按公开 ID 添加失败', true); });
  }

  /* ── 详情（二级界面，进入不自动同步）── */
  function openDetail(id) {
    var peer = findPeer(id);
    if (!peer) return;
    state.peer = peer;
    state.selected = new Set();
    state.items = [];
    state.detailOpen = true;
    var modal = $('wctDetailModal');
    if (modal) modal.hidden = false;
    var title = $('wctDetailTitle');
    if (title) title.textContent = peer.display_name || peer.account_key;
    var badge = $('wctDetailBadge');
    if (badge) { badge.className = 'wct-badge ' + peer.platform; badge.textContent = platformLabel(peer.platform); }
    var meta = $('wctDetailMeta');
    if (meta) meta.textContent = peerFields(peer).map(function(pair) { return pair[0] + '：' + pair[1]; }).join('　');
    renderItems();
    renderResults();
    return loadItems(peer);
  }
  function closeDetail() {
    state.detailOpen = false;
    state.peer = null;
    state.items = [];
    state.selected = new Set();
    var modal = $('wctDetailModal');
    if (modal) modal.hidden = true;
  }
  function loadItems(peer) {
    var host = $('wctItemList');
    if (host) { host.className = 'wct-empty'; host.innerHTML = '正在读取已入库作品…'; }
    var url = '/api/ip-content/source-items?source_type=user_post&limit=200&competitor_id=' + encodeURIComponent(peer.id)
      + '&platform=' + encodeURIComponent(peer.platform);
    return apiJson(url).then(function(data) {
      state.items = Array.isArray(data.items) ? data.items : [];
      renderItems();
      return state.items;
    }).catch(function(err) {
      state.items = [];
      renderItems();
      setMsg(err.message || '作品读取失败', true);
      return [];
    });
  }
  function renderItems() {
    var host = $('wctItemList');
    var hint = $('wctItemHint');
    if (hint) hint.textContent = state.items.length ? ('已入库 ' + state.items.length + ' 条；点右上角「同步作品」可拉最新。') : '还没同步过，点右上角「同步作品」。';
    if (!host) return;
    if (!state.items.length) {
      host.className = 'wct-empty';
      host.innerHTML = '暂无作品（点右上角「同步作品」）。';
      return;
    }
    host.className = '';
    host.innerHTML = state.items.map(function(item, index) {
      var key = String(item.item_key || index);
      var cover = item.cover_url ? '<img class="wct-cover" src="' + esc(item.cover_url) + '" alt="">' : '<div class="wct-cover"></div>';
      var meta = [];
      if (item.publish_time) meta.push(String(item.publish_time).slice(0, 16));
      var metrics = item.metrics || {};
      if (metrics.digg_count || metrics.like_count) meta.push('赞 ' + fmtCount(metrics.digg_count || metrics.like_count));
      if (metrics.comment_count) meta.push('评 ' + fmtCount(metrics.comment_count));
      var stored = state.results[resultKey(item)];
      var status = stored && stored.transcript ? '<span class="wct-status completed">已提取</span>' : '';
      return '<article class="wct-item">'
        + '<input type="checkbox" data-wct-pick="' + index + '"' + (state.selected.has(key) ? ' checked' : '') + '>'
        + cover
        + '<div><div class="wct-item-title">' + esc(itemTitle(item)) + ' ' + status + '</div>'
        + '<div class="wct-meta">' + esc(meta.join(' · ')) + '</div>'
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
    var key = String(item.item_key || index);
    if (checked) state.selected.add(key); else state.selected.delete(key);
  }
  function pickAll(checked) {
    state.selected = new Set();
    if (checked) state.items.forEach(function(item, index) { state.selected.add(String(item.item_key || index)); });
    renderItems();
  }
  function downloadItem(index) {
    var item = state.items[Number(index)];
    if (!item) return;
    var url = itemVideoUrl(item);
    if (!url) { setMsg('这条作品没有可下载的视频地址', true); return; }
    setMsg('正在下载视频到本机…');
    keystreamFor(item).then(function(keystream) {
      return localJson('/api/wechat-channels-transcript/local-download', { video: item, keystream_b64: keystream });
    }).then(function(data) {
      setMsg('已下载：' + (data.path || '') + (data.opened ? '（已打开所在文件夹）' : ''));
    }).catch(function(err) { setMsg(err.message || '下载失败', true); });
  }
  function extractItem(item) {
    var url = itemVideoUrl(item);
    if (!url) return Promise.reject(new Error('这条作品没有可提取的视频地址'));
    return keystreamFor(item).then(function(keystream) {
      return localJson('/api/wechat-channels-transcript/local-transcribe', { video: item, keystream_b64: keystream });
    }).then(function(data) {
      var transcript = String(data.transcript || '').trim();
      if (!transcript) throw new Error('转写完成但没有返回文案');
      state.results[resultKey(item)] = { title: itemTitle(item), transcript: transcript, at: Date.now() };
      saveStore();
      return transcript;
    });
  }
  function extractSelected() {
    if (!state.peer) return;
    var list = state.items.filter(function(item, index) { return state.selected.has(String(item.item_key || index)); });
    if (!list.length) { setMsg('请先勾选要提炼的作品', true); return; }
    var btn = $('wctExtractBtn');
    if (btn) { btn.disabled = true; btn.textContent = '提炼中…'; }
    var chain = Promise.resolve();
    var failed = 0;
    list.forEach(function(item, idx) {
      chain = chain.then(function() {
        setMsg('正在提炼 ' + (idx + 1) + '/' + list.length + '：' + itemTitle(item).slice(0, 30));
        return extractItem(item).catch(function(err) { failed += 1; setMsg('第 ' + (idx + 1) + ' 条失败：' + err.message, true); });
      });
    });
    chain.then(function() {
      renderItems();
      renderResults();
      setMsg(failed ? ('提炼完成，失败 ' + failed + ' 条。') : '提炼完成，共 ' + list.length + ' 条。', !!failed);
    }).finally(function() { if (btn) { btn.disabled = false; btn.textContent = '提炼文案'; } });
  }
  function renderResults() {
    var host = $('wctResultList');
    var hint = $('wctResultHint');
    if (!host) return;
    var rows = state.items
      .map(function(item) {
        var stored = state.results[resultKey(item)];
        return stored && stored.transcript ? { title: stored.title || itemTitle(item), transcript: stored.transcript } : null;
      })
      .filter(Boolean);
    if (hint) hint.textContent = rows.length ? ('已提取 ' + rows.length + ' 条') : '勾选作品后点「提炼文案」。';
    if (!rows.length) { host.className = 'wct-empty'; host.innerHTML = '暂无结果'; return; }
    host.className = '';
    host.innerHTML = rows.map(function(row) {
      return '<div class="wct-result"><div class="wct-item-title" style="margin-bottom:6px;">' + esc(row.title) + '</div>'
        + '<textarea readonly>' + esc(row.transcript) + '</textarea>'
        + '<div class="wct-item-actions"><button type="button" class="btn btn-ghost btn-sm" data-wct-copy="1">复制这条</button></div></div>';
    }).join('');
  }
  function resultRows() {
    if (!state.peer) return [];
    return state.items.map(function(item) {
      var stored = state.results[resultKey(item)];
      return stored && stored.transcript ? stored : null;
    }).filter(Boolean);
  }
  function copyText(text) {
    try { navigator.clipboard.writeText(text); setMsg('已复制到剪贴板。'); }
    catch (e) { setMsg('复制失败，请手动选择文本。', true); }
  }
  function downloadFile(filename, content, type) {
    var blob = new Blob([content], { type: type || 'text/plain;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function() { URL.revokeObjectURL(url); }, 3000);
  }
  function exportTxt() {
    var rows = resultRows();
    if (!rows.length) { setMsg('还没有可导出的文案', true); return; }
    var text = rows.map(function(row, index) { return '【' + (index + 1) + '】' + row.title + '\n' + row.transcript; }).join('\n\n');
    downloadFile('文案提取_' + (state.peer ? state.peer.platform : '') + '.txt', text);
  }
  function exportCsv() {
    var rows = resultRows();
    if (!rows.length) { setMsg('还没有可导出的文案', true); return; }
    function cell(value) { return '"' + String(value == null ? '' : value).replace(/"/g, '""') + '"'; }
    var csv = '\ufeff' + ['标题,文案'].concat(rows.map(function(row) { return cell(row.title) + ',' + cell(row.transcript); })).join('\r\n');
    downloadFile('文案提取_' + (state.peer ? state.peer.platform : '') + '.csv', csv, 'text/csv;charset=utf-8');
  }

  function bind() {
    var on = function(id, event, handler) { var el = $(id); if (el) el.addEventListener(event, handler); };
    on('wctRefreshBtn', 'click', function() { setMsg(''); loadPeers(); });
    on('wctAddPeerBtn', 'click', openAddModal);
    on('wctAddClose', 'click', closeAddModal);
    on('wctPlatform', 'change', updatePlatformFields);
    on('wctSearchBtn', 'click', searchCandidates);
    on('wctAddByIdBtn', 'click', addByPublicId);
    on('wctSearchInput', 'keydown', function(ev) { if (ev.key === 'Enter') { ev.preventDefault(); searchCandidates(); } });
    on('wctDetailClose', 'click', closeDetail);
    on('wctSyncBtn', 'click', function() { if (state.peer) syncPeer(state.peer.id, $('wctSyncBtn')); });
    on('wctSelectAllBtn', 'click', function() { pickAll(true); });
    on('wctClearSelectBtn', 'click', function() { pickAll(false); });
    on('wctExtractBtn', 'click', extractSelected);
    on('wctCopyAllBtn', 'click', function() {
      var rows = resultRows();
      if (!rows.length) { setMsg('还没有可复制的文案', true); return; }
      copyText(rows.map(function(row) { return row.title + '\n' + row.transcript; }).join('\n\n'));
    });
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
      else if (el.getAttribute('data-wct-extract') !== null) {
        var index = Number(el.getAttribute('data-wct-extract'));
        var item = state.items[index];
        if (!item) return;
        setMsg('正在提炼：' + itemTitle(item).slice(0, 30));
        extractItem(item).then(function() { renderItems(); renderResults(); setMsg('文案已提炼完成。'); })
          .catch(function(err) { setMsg(err.message || '提炼失败', true); });
      }
    });
    on('wctResultList', 'click', function(ev) {
      var el = ev.target;
      if (!el || !el.getAttribute || el.getAttribute('data-wct-copy') === null) return;
      var box = el.closest ? el.closest('.wct-result') : null;
      var area = box ? box.querySelector('textarea') : null;
      if (area) copyText(area.value);
    });
    ['wctAddModal', 'wctDetailModal'].forEach(function(id) {
      var mask = $(id);
      if (!mask) return;
      mask.addEventListener('click', function(ev) {
        if (ev.target !== mask) return;
        if (id === 'wctAddModal') closeAddModal(); else closeDetail();
      });
    });
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
