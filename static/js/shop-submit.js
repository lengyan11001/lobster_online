/* 投稿（Online → 商家商品）：商品列表 / 复制邀请链接 / 从素材库或内容记录投稿 / 我的投稿与详情 */
(function () {
  'use strict';

  var state = {
    tab: 'products',
    products: [], total: 0, page: 1, size: 12, keyword: '', sort: 'heat',
    mine: [], mineTotal: 0, minePage: 1,
    detail: null,
    submit: null,
    picker: { origin: 'user_upload', keyword: '', page: 1, size: 24, items: [], total: 0, picked: {}, loading: false }
  };

  function base() {
    try {
      if (typeof API_BASE !== 'undefined' && API_BASE) return String(API_BASE).replace(/\/$/, '');
      if (window.__API_BASE) return String(window.__API_BASE).replace(/\/$/, '');
    } catch (e) { /* ignore */ }
    return '';
  }

  function localBase() {
    try {
      if (typeof publishLocalBase === 'function') return String(publishLocalBase() || '').replace(/\/$/, '');
    } catch (e) { /* ignore */ }
    return '';
  }

  function authOnlyHeaders() {
    var h = {};
    try {
      if (typeof authHeaders === 'function') {
        var ah = authHeaders() || {};
        if (ah.Authorization) h.Authorization = ah.Authorization;
        if (ah['X-Installation-Id']) h['X-Installation-Id'] = ah['X-Installation-Id'];
        if (ah['X-Lobster-Brand']) h['X-Lobster-Brand'] = ah['X-Lobster-Brand'];
      }
    } catch (e) { /* ignore */ }
    return h;
  }

  function headers(extra) {
    return Object.assign({ Accept: 'application/json' }, authOnlyHeaders(), extra || {});
  }

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function yuan(cents) {
    var v = Number(cents || 0) / 100;
    return '\uffe5' + (v % 1 === 0 ? v : v.toFixed(2));
  }

  function fmtTime(v) {
    if (!v) return '';
    try { return new Date(v).toLocaleString('zh-CN'); } catch (e) { return String(v); }
  }

  function setMsg(id, text, isError) {
    var node = document.getElementById(id);
    if (!node) return;
    node.textContent = text || '';
    node.className = 'ss-msg' + (isError ? ' is-error' : '');
  }

  function toast(text, isError) {
    setMsg(state.tab === 'mine' ? 'ssMineMsg' : 'ssMsg', text, isError);
    if (!isError) setTimeout(function () { setMsg('ssMsg', ''); setMsg('ssMineMsg', ''); }, 4000);
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      try {
        var ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
        resolve();
      } catch (e) { reject(e); }
    });
  }

  async function request(path, opts) {
    opts = opts || {};
    var url = (opts.absolute ? '' : base()) + path;
    var init = { method: opts.method || 'GET', headers: headers(opts.headers) };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    if (opts.form) init.body = opts.form;
    var res = await fetch(url, init);
    var data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok) throw new Error((data && data.detail) || ('HTTP ' + res.status));
    return data;
  }

  function isPublicUrl(url) {
    var text = String(url || '').trim();
    if (!/^https?:\/\//i.test(text)) return false;
    if (/localhost|127\.0\.0\.1|0\.0\.0\.0/i.test(text)) return false;
    return true;
  }

  function productMediaUrl(item) {
    var candidates = [item && item.source_url, item && item.open_url, item && item.file_url, item && item.preview_url, item && item.cover_url];
    for (var i = 0; i < candidates.length; i += 1) {
      if (isPublicUrl(candidates[i])) return String(candidates[i]).trim();
    }
    return '';
  }

  // ───────────────────────── 商品列表 ─────────────────────────

  window.initShopSubmitView = function () {
    var host = document.getElementById('ssProducts');
    if (!host) return;
    bindOnce();
    if (!state.products.length) loadProducts(true);
    else renderProducts();
    if (state.tab === 'mine' && !state.mine.length) loadMine(true);
  };

  function bindOnce() {
    if (state.bound) return;
    state.bound = true;
    var search = document.getElementById('ssSearch');
    if (search) search.addEventListener('click', function () { loadProducts(true); });
    var kw = document.getElementById('ssKeyword');
    if (kw) kw.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') loadProducts(true); });
    var sort = document.getElementById('ssSort');
    if (sort) sort.addEventListener('change', function () { state.sort = sort.value; loadProducts(true); });
    var refresh = document.getElementById('ssRefresh');
    if (refresh) refresh.addEventListener('click', function () {
      if (state.tab === 'mine') loadMine(true); else loadProducts(true);
      toast('\u5df2\u5237\u65b0');
    });
    var plaza = document.getElementById('ssOpenPlaza');
    if (plaza) plaza.addEventListener('click', function () {
      var url = 'https://shop.bhzn.top/plaza.html';
      try {
        var token = typeof getStoredAuthToken === 'function' ? getStoredAuthToken() : localStorage.getItem('token');
        if (token) url += '?token=' + encodeURIComponent(token);
      } catch (e) { /* ignore */ }
      try { window.open(url, '_blank', 'noopener'); } catch (e) { copyText(url); }
    });
    document.querySelectorAll('#content-shop-submit [data-ss-tab]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var tab = btn.getAttribute('data-ss-tab');
        state.tab = tab;
        document.querySelectorAll('#content-shop-submit [data-ss-tab]').forEach(function (b) {
          b.classList.toggle('active', b === btn);
        });
        var productsBox = document.getElementById('ssProducts');
        var mineBox = document.getElementById('ssMine');
        var pager = document.getElementById('ssPager');
        var productsBar = document.getElementById('ssProductsBar');
        var mineBar = document.getElementById('ssMineBar');
        if (tab === 'mine') {
          if (productsBox) productsBox.hidden = true;
          if (productsBar) productsBar.hidden = true;
          if (mineBox) mineBox.hidden = false;
          if (mineBar) mineBar.hidden = false;
          if (pager) pager.innerHTML = '';
          loadMine(true);
        } else {
          if (productsBox) productsBox.hidden = false;
          if (productsBar) productsBar.hidden = false;
          if (mineBox) mineBox.hidden = true;
          if (mineBar) mineBar.hidden = true;
          loadProducts(true);
        }
      });
    });
    var submitGo = document.getElementById('ssSubmitGo');
    if (submitGo) submitGo.addEventListener('click', submitPicked);
    var assetSearch = document.getElementById('ssAssetSearch');
    if (assetSearch) assetSearch.addEventListener('click', function () { loadPicker(true); });
    var assetKw = document.getElementById('ssAssetKeyword');
    if (assetKw) assetKw.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') loadPicker(true); });
    document.querySelectorAll('#content-shop-submit [data-ss-origin]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        state.picker.origin = chip.getAttribute('data-ss-origin') || 'user_upload';
        document.querySelectorAll('#content-shop-submit [data-ss-origin]').forEach(function (c) {
          c.classList.toggle('active', c === chip);
        });
        loadPicker(true);
      });
    });
  }

  async function loadProducts(reset) {
    var host = document.getElementById('ssProducts');
    if (!host) return;
    if (reset) state.page = 1;
    var kw = document.getElementById('ssKeyword');
    if (kw) state.keyword = kw.value.trim();
    host.innerHTML = '<div class="ss-empty">\u52a0\u8f7d\u4e2d\u2026</div>';
    try {
      var data = await request('/api/shop/plaza?keyword=' + encodeURIComponent(state.keyword) +
        '&sort=' + encodeURIComponent(state.sort) + '&page=' + state.page + '&size=' + state.size);
      state.products = data.items || [];
      state.total = Number(data.total || state.products.length);
      renderProducts();
    } catch (e) {
      host.innerHTML = '<div class="ss-empty is-error">\u5546\u54c1\u52a0\u8f7d\u5931\u8d25\uff1a' + esc(e.message) + '</div>';
    }
  }

  function renderProducts() {
    var host = document.getElementById('ssProducts');
    var pager = document.getElementById('ssPager');
    if (!host) return;
    if (!state.products.length) {
      host.innerHTML = '<div class="ss-empty">\u6682\u65e0\u5728\u552e\u5546\u54c1\uff08\u5546\u5bb6\u4e0a\u67b6\u540e\u4f1a\u51fa\u73b0\u5728\u8fd9\u91cc\uff09</div>';
      if (pager) pager.innerHTML = '';
      return;
    }
    host.innerHTML = state.products.map(function (p) {
      var merchant = p.merchant || {};
      var commission = (Number(p.commission_bp || 0) / 100).toFixed(1) + '%';
      return '<article class="ss-card" data-ss-product="' + p.id + '">'
        + '<div class="ss-card-cover">' + (p.cover_url ? '<img src="' + esc(p.cover_url) + '" alt="" loading="lazy">' : '<span>\u65e0\u4e3b\u56fe</span>') + '</div>'
        + '<div class="ss-card-body">'
        + '<strong class="ss-card-title">' + esc(p.title || ('\u5546\u54c1 #' + p.id)) + '</strong>'
        + '<div class="ss-card-sub">' + esc(merchant.company_name || '') + '</div>'
        + '<div class="ss-card-meta"><span>' + yuan(p.price_cents) + '</span><span class="ss-badge">\u4f63\u91d1 ' + commission + '</span></div>'
        + '<div class="ss-card-actions">'
        + '<button type="button" class="btn btn-outline btn-sm" data-ss-invite="' + p.id + '">\u590d\u5236\u9080\u8bf7\u94fe\u63a5</button>'
        + '<button type="button" class="btn btn-primary btn-sm" data-ss-submit="' + p.id + '">\u6295\u7a3f</button>'
        + '<button type="button" class="btn btn-ghost btn-sm" data-ss-detail="' + p.id + '">\u8be6\u60c5</button>'
        + '</div></div></article>';
    }).join('');
    host.querySelectorAll('[data-ss-invite]').forEach(function (btn) {
      btn.addEventListener('click', function () { copyInvite(Number(btn.getAttribute('data-ss-invite')), btn); });
    });
    host.querySelectorAll('[data-ss-submit]').forEach(function (btn) {
      btn.addEventListener('click', function () { openSubmit(productById(Number(btn.getAttribute('data-ss-submit')))); });
    });
    host.querySelectorAll('[data-ss-detail]').forEach(function (btn) {
      btn.addEventListener('click', function () { openDetail(Number(btn.getAttribute('data-ss-detail'))); });
    });
    if (pager) {
      var pages = Math.max(1, Math.ceil(state.total / state.size));
      pager.innerHTML = state.total > state.size
        ? '<span class="ss-hint">\u5171 ' + state.total + ' \u4e2a\u5546\u54c1 \u00b7 \u7b2c ' + state.page + '/' + pages + ' \u9875</span>'
          + '<button type="button" class="btn btn-outline btn-sm" data-ss-page="prev"' + (state.page <= 1 ? ' disabled' : '') + '>\u4e0a\u4e00\u9875</button>'
          + '<button type="button" class="btn btn-outline btn-sm" data-ss-page="next"' + (state.page >= pages ? ' disabled' : '') + '>\u4e0b\u4e00\u9875</button>'
        : '';
      pager.querySelectorAll('[data-ss-page]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          state.page += btn.getAttribute('data-ss-page') === 'next' ? 1 : -1;
          loadProducts(false);
        });
      });
    }
  }

  function productById(id) {
    return state.products.filter(function (p) { return Number(p.id) === Number(id); })[0] || { id: id };
  }

  async function copyInvite(productId, btn) {
    try {
      var data = await request('/api/shop/plaza/link', { method: 'POST', body: { product_id: Number(productId), source: 'online_submit' } });
      await copyText(data.link || '');
      if (btn) {
        var old = btn.textContent;
        btn.textContent = '\u5df2\u590d\u5236\u94fe\u63a5';
        setTimeout(function () { btn.textContent = old; }, 2000);
      }
      toast('\u9080\u8bf7\u94fe\u63a5\u5df2\u590d\u5236');
    } catch (e) {
      toast('\u590d\u5236\u9080\u8bf7\u94fe\u63a5\u5931\u8d25\uff1a' + e.message, true);
    }
  }

  // ───────────────────────── 我的投稿 ─────────────────────────

  async function loadMine(reset) {
    var host = document.getElementById('ssMine');
    if (!host) return;
    if (reset) state.minePage = 1;
    host.innerHTML = '<div class="ss-empty">\u52a0\u8f7d\u4e2d\u2026</div>';
    try {
      var data = await request('/api/shop/submissions/mine?page=' + state.minePage + '&size=20');
      state.mine = data.items || [];
      state.mineTotal = Number(data.total || state.mine.length);
      renderMine();
    } catch (e) {
      host.innerHTML = '<div class="ss-empty is-error">\u6295\u7a3f\u8bb0\u5f55\u52a0\u8f7d\u5931\u8d25\uff1a' + esc(e.message) + '</div>';
    }
  }

  function renderMine() {
    var host = document.getElementById('ssMine');
    var hint = document.getElementById('ssMineHint');
    if (!host) return;
    if (hint) hint.textContent = '\u5171 ' + state.mineTotal + ' \u6761\u6295\u7a3f';
    if (!state.mine.length) {
      host.innerHTML = '<div class="ss-empty">\u8fd8\u6ca1\u6709\u6295\u7a3f\uff0c\u53bb\u300c\u5546\u54c1\u300d\u91cc\u6311\u4e00\u4e2a\u5546\u54c1\u6295\u7d20\u6750</div>';
      return;
    }
    host.innerHTML = state.mine.map(function (it) {
      var product = it.product || {};
      var statusMap = { new: '\u5df2\u6295\u9012', used: '\u5df2\u91c7\u7528', rejected: '\u5df2\u5ffd\u7565' };
      var thumb = it.thumb_url || it.url || '';
      return '<article class="ss-row" data-ss-submission="' + it.id + '">'
        + '<div class="ss-row-thumb">' + (thumb ? '<img src="' + esc(thumb) + '" alt="" loading="lazy">' : '<span>\u7d20\u6750</span>') + '</div>'
        + '<div class="ss-row-main">'
        + '<strong>' + esc(it.title || product.title || ('\u6295\u7a3f #' + it.id)) + '</strong>'
        + '<div class="ss-row-sub">\u5546\u54c1\uff1a' + esc(product.title || ('#' + it.product_id)) + (it.merchant && it.merchant.company_name ? ' \u00b7 ' + esc(it.merchant.company_name) : '') + '</div>'
        + '<div class="ss-row-sub">' + esc(fmtTime(it.created_at)) + (it.note ? ' \u00b7 ' + esc(it.note) : '') + '</div>'
        + '</div>'
        + '<span class="ss-status ss-status-' + esc(it.status) + '">' + (statusMap[it.status] || esc(it.status)) + '</span>'
        + '<div class="ss-row-actions">'
        + '<button type="button" class="btn btn-ghost btn-sm" data-ss-detail="' + it.product_id + '">\u5546\u54c1\u8be6\u60c5</button>'
        + '<a class="btn btn-outline btn-sm" href="' + esc(it.url) + '" target="_blank" rel="noopener">\u67e5\u770b\u7d20\u6750</a>'
        + '</div></article>';
    }).join('');
    host.querySelectorAll('[data-ss-detail]').forEach(function (btn) {
      btn.addEventListener('click', function () { openDetail(Number(btn.getAttribute('data-ss-detail'))); });
    });
  }

  // ───────────────────────── 详情 ─────────────────────────

  async function openDetail(productId) {
    var modal = document.getElementById('ssDetailModal');
    if (!modal) return;
    document.getElementById('ssDetailTitle').textContent = '\u6295\u7a3f\u8be6\u60c5';
    document.getElementById('ssDetailMeta').textContent = '\u52a0\u8f7d\u4e2d\u2026';
    document.getElementById('ssDetailBody').innerHTML = '';
    modal.classList.add('visible');
    modal.style.display = 'flex';
    try {
      var data = await request('/api/shop/submissions/product/' + Number(productId));
      state.detail = data;
      renderDetail(data);
    } catch (e) {
      document.getElementById('ssDetailMeta').textContent = '';
      document.getElementById('ssDetailBody').innerHTML = '<div class="ss-empty is-error">\u52a0\u8f7d\u5931\u8d25\uff1a' + esc(e.message) + '</div>';
    }
  }

  function renderDetail(data) {
    var product = data.product || {};
    var merchant = product.merchant || {};
    document.getElementById('ssDetailTitle').textContent = product.title || ('\u5546\u54c1 #' + product.id);
    document.getElementById('ssDetailMeta').textContent = (merchant.company_name ? merchant.company_name + ' \u00b7 ' : '') +
      yuan(product.price_cents) + ((product.commission_bp ? ' \u00b7 \u4f63\u91d1 ' + (Number(product.commission_bp) / 100).toFixed(1) + '%' : ''));
    var materials = (data.materials || []).map(function (m) {
      return '<a class="ss-material" href="' + esc(m.url) + '" target="_blank" rel="noopener">'
        + (/\.(png|jpe?g|webp|gif)$/i.test(m.url) ? '<img src="' + esc(m.url) + '" alt="" loading="lazy">' : '<span>\u7d20\u6750</span>')
        + '<small>' + esc(m.title) + '</small></a>';
    }).join('');
    var mine = (data.my_submissions || []).map(function (it) {
      return '<div class="ss-detail-mine"><a href="' + esc(it.url) + '" target="_blank" rel="noopener">' + esc(it.title || it.url) + '</a>'
        + '<span class="ss-hint">' + esc(fmtTime(it.created_at)) + '</span></div>';
    }).join('');
    var gallery = (product.gallery || []).slice(0, 8).map(function (url) {
      return '<a class="ss-material" href="' + esc(url) + '" target="_blank" rel="noopener"><img src="' + esc(url) + '" alt="" loading="lazy"></a>';
    }).join('');
    document.getElementById('ssDetailBody').innerHTML =
      (product.subtitle ? '<div class="ss-detail-sub">' + esc(product.subtitle) + '</div>' : '')
      + '<div class="ss-detail-section"><b>\u5546\u54c1\u56fe</b><div class="ss-materials">' + (gallery || '<span class="ss-hint">\u6682\u65e0</span>') + '</div></div>'
      + '<div class="ss-detail-section"><b>\u5546\u5bb6\u7d20\u6750\uff08' + (data.materials || []).length + '\uff09</b><div class="ss-materials">' + (materials || '<span class="ss-hint">\u6682\u65e0</span>') + '</div></div>'
      + '<div class="ss-detail-section"><b>\u6211\u6295\u7684\u7d20\u6750\uff08' + (data.my_submissions || []).length + '\uff09</b>' + (mine || '<span class="ss-hint">\u8fd8\u6ca1\u6295\u8fc7</span>') + '</div>';
    document.getElementById('ssDetailInvite').onclick = function () { copyInvite(product.id); };
    document.getElementById('ssDetailSubmit').onclick = function () { openSubmit(product); };
  }

  window.ShopSubmit = window.ShopSubmit || {};
  window.ShopSubmit.closeDetail = function () {
    var modal = document.getElementById('ssDetailModal');
    if (!modal) return;
    modal.classList.remove('visible');
    modal.style.display = '';
  };

  // ───────────────────────── 投稿弹窗 ─────────────────────────

  function openSubmit(product, presetItems) {
    var modal = document.getElementById('ssSubmitModal');
    if (!modal || !product) return;
    state.submit = { product: product };
    state.picker = { origin: 'user_upload', keyword: '', page: 1, size: 24, items: [], total: 0, picked: {}, loading: false };
    document.getElementById('ssSubmitTitle').textContent = '\u6295\u7a3f\u7d20\u6750';
    document.getElementById('ssSubmitMeta').textContent = (product.title || ('\u5546\u54c1 #' + product.id)) +
      ((product.merchant && product.merchant.company_name) ? ' \u00b7 ' + product.merchant.company_name : '');
    document.getElementById('ssSubmitNote').value = '';
    setMsg('ssSubmitMsg', '');
    document.querySelectorAll('#content-shop-submit [data-ss-origin]').forEach(function (c) {
      c.classList.toggle('active', c.getAttribute('data-ss-origin') === 'user_upload');
    });
    modal.classList.add('visible');
    modal.style.display = 'flex';
    (presetItems && presetItems.length ? Promise.resolve() : loadPicker(true)).then(function () {
      if (presetItems && presetItems.length) presetItems.forEach(function (item) { pickItem(item); });
    });
  }

  window.ShopSubmit.closeSubmit = function () {
    var modal = document.getElementById('ssSubmitModal');
    if (!modal) return;
    modal.classList.remove('visible');
    modal.style.display = '';
    state.submit = null;
  };

  async function loadPicker(reset) {
    if (reset) state.picker.page = 1;
    var host = document.getElementById('ssPicker');
    if (!host) return;
    var kw = document.getElementById('ssAssetKeyword');
    if (kw) state.picker.keyword = kw.value.trim();
    state.picker.loading = true;
    host.innerHTML = '<div class="ss-empty">\u52a0\u8f7d\u4e2d\u2026</div>';
    var offset = (state.picker.page - 1) * state.picker.size;
    var query = '?limit=' + state.picker.size + '&offset=' + offset + '&origin=' + encodeURIComponent(state.picker.origin) +
      (state.picker.keyword ? '&q=' + encodeURIComponent(state.picker.keyword) : '');
    var sources = [];
    var lb = localBase();
    if (lb) sources.push(lb);
    if (base()) sources.push(base());
    var merged = [];
    var seen = {};
    var lastError = null;
    for (var i = 0; i < sources.length; i += 1) {
      try {
        var res = await fetch(sources[i] + '/api/assets' + query, { headers: headers() });
        if (!res.ok) continue;
        var data = await res.json();
        (data.assets || data.items || []).forEach(function (item) {
          var key = String(item.asset_id || item.url || item.filename || '');
          if (!key || seen[key]) return;
          seen[key] = true;
          merged.push(item);
        });
      } catch (e) { lastError = e; }
    }
    state.picker.items = merged;
    state.picker.loading = false;
    if (!merged.length && lastError) {
      host.innerHTML = '<div class="ss-empty is-error">\u7d20\u6750\u52a0\u8f7d\u5931\u8d25\uff1a' + esc(lastError.message) + '</div>';
      return;
    }
    renderPicker();
  }

  function renderPicker() {
    var host = document.getElementById('ssPicker');
    var pager = document.getElementById('ssPickerPager');
    if (!host) return;
    if (!state.picker.items.length) {
      host.innerHTML = '<div class="ss-empty">\u6ca1\u6709\u627e\u5230\u7d20\u6750</div>';
      if (pager) pager.innerHTML = '';
      updatePickedCount();
      return;
    }
    host.innerHTML = state.picker.items.map(function (item) {
      var id = String(item.asset_id || item.url || '');
      var picked = !!state.picker.picked[id];
      var thumb = item.cover_url || item.preview_url || item.open_url || item.source_url || '';
      var isVideo = String(item.media_type || '') === 'video';
      return '<button type="button" class="ss-asset' + (picked ? ' is-picked' : '') + '" data-ss-asset="' + esc(id) + '">'
        + '<span class="ss-asset-thumb">' + (thumb && !isVideo ? '<img src="' + esc(thumb) + '" alt="" loading="lazy">' : '<em>' + (isVideo ? '\u89c6\u9891' : '\u7d20\u6750') + '</em>') + '</span>'
        + '<span class="ss-asset-title">' + esc(item.title || item.filename || item.asset_id) + '</span>'
        + '<span class="ss-asset-tick">' + (picked ? '\u2713' : '') + '</span></button>';
    }).join('');
    host.querySelectorAll('[data-ss-asset]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-ss-asset');
        var item = state.picker.items.filter(function (x) { return String(x.asset_id || x.url || '') === key; })[0];
        if (!item) return;
        if (state.picker.picked[key]) delete state.picker.picked[key];
        else state.picker.picked[key] = item;
        btn.classList.toggle('is-picked', !!state.picker.picked[key]);
        var tick = btn.querySelector('.ss-asset-tick');
        if (tick) tick.textContent = state.picker.picked[key] ? '\u2713' : '';
        updatePickedCount();
      });
    });
    if (pager) {
      var pages = Math.max(1, Math.ceil((state.picker.total || state.picker.items.length) / state.picker.size));
      pager.innerHTML = '<button type="button" class="btn btn-outline btn-sm" data-ss-picker-page="prev"' + (state.picker.page <= 1 ? ' disabled' : '') + '>\u4e0a\u4e00\u9875</button>'
        + '<span class="ss-hint">\u7b2c ' + state.picker.page + ' \u9875</span>'
        + '<button type="button" class="btn btn-outline btn-sm" data-ss-picker-page="next"' + (state.picker.items.length < state.picker.size ? ' disabled' : '') + '>\u4e0b\u4e00\u9875</button>';
      pager.querySelectorAll('[data-ss-picker-page]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          state.picker.page += btn.getAttribute('data-ss-picker-page') === 'next' ? 1 : -1;
          if (state.picker.page < 1) state.picker.page = 1;
          loadPicker(false);
        });
      });
    }
    updatePickedCount();
  }

  function pickItem(item) {
    var key = String(item.asset_id || item.url || '');
    if (!key) return;
    state.picker.picked[key] = Object.assign({}, item, { asset_id: String(item.asset_id || '') });
    var modal = document.getElementById('ssSubmitModal');
    if (modal) {
      modal.classList.add('visible');
      modal.style.display = 'flex';
    }
    var host = document.getElementById('ssPicker');
    if (host && state.picker.items.length) renderPicker();
    updatePickedCount();
  }

  function updatePickedCount() {
    var node = document.getElementById('ssPickedCount');
    if (node) node.textContent = '\u5df2\u9009 ' + Object.keys(state.picker.picked).length;
  }

  async function ensureCloudItem(item) {
    var direct = productMediaUrl(item);
    if (direct) return { url: direct, asset_id: String(item.asset_id || ''), media_type: item.media_type || '', title: item.title || item.filename || '' };
    var lb = localBase();
    var assetId = String(item.asset_id || '');
    if (!lb || !assetId) {
      throw new Error('\u7d20\u6750\u300c' + (item.title || item.filename || assetId) + '\u300d\u6ca1\u6709\u53ef\u516c\u5f00\u8bbf\u95ee\u7684\u5730\u5740\uff0c\u65e0\u6cd5\u6295\u9012');
    }
    var res = await fetch(lb + '/api/assets/' + encodeURIComponent(assetId) + '/content', { headers: headers() });
    if (!res.ok) throw new Error('\u8bfb\u53d6\u672c\u5730\u7d20\u6750\u5931\u8d25');
    var blob = await res.blob();
    var form = new FormData();
    form.append('file', blob, item.filename || (assetId + '.bin'));
    var up = await request('/api/assets/upload', { method: 'POST', form: form, headers: { Accept: 'application/json' } });
    var url = up.source_url || up.url || up.preview_url || '';
    if (!url) throw new Error('\u7d20\u6750\u4e0a\u4f20\u540e\u6ca1\u6709\u62ff\u5230\u516c\u7f51\u5730\u5740');
    return { url: url, asset_id: String(up.asset_id || assetId), media_type: up.media_type || item.media_type || '', title: item.title || item.filename || '' };
  }

  async function submitPicked() {
    if (!state.submit || !state.submit.product) return;
    var picked = Object.keys(state.picker.picked).map(function (k) { return state.picker.picked[k]; });
    if (!picked.length) {
      setMsg('ssSubmitMsg', '\u8bf7\u5148\u9009\u4e00\u4e2a\u6216\u591a\u4e2a\u7d20\u6750', true);
      return;
    }
    var btn = document.getElementById('ssSubmitGo');
    if (btn) { btn.disabled = true; btn.textContent = '\u6295\u9012\u4e2d\u2026'; }
    setMsg('ssSubmitMsg', '\u6b63\u5728\u63d0\u4ea4\u2026');
    try {
      var items = [];
      for (var i = 0; i < picked.length; i += 1) {
        var row = await ensureCloudItem(picked[i]);
        items.push({
          asset_id: row.asset_id,
          url: row.url,
          thumb_url: picked[i].cover_url || picked[i].preview_url || '',
          media_type: row.media_type,
          title: row.title
        });
      }
      var note = (document.getElementById('ssSubmitNote') || {}).value || '';
      var data = await request('/api/shop/submissions', {
        method: 'POST',
        body: { product_id: Number(state.submit.product.id), note: note, source: 'online_submit', items: items }
      });
      setMsg('ssSubmitMsg', '\u5df2\u6295\u9012 ' + (data.total || items.length) + ' \u4e2a\u7d20\u6750\u7ed9\u300c' + (state.submit.product.title || '') + '\u300d\uff0c\u5546\u5bb6\u5728\u540e\u53f0\u80fd\u770b\u5230\u3002');
      state.picker.picked = {};
      updatePickedCount();
      if (state.tab === 'mine') loadMine(true);
    } catch (e) {
      setMsg('ssSubmitMsg', '\u6295\u9012\u5931\u8d25\uff1a' + e.message, true);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '\u6295\u9012'; }
    }
  }

  // 供内容记录「投稿」按钮调用：带着素材直接打开商品列表并进入投稿弹窗
  window.ShopSubmit.open = function (opts) {
    opts = opts || {};
    var items = opts.items || (opts.item ? [opts.item] : []);
    return loadProducts(true).then(function () {
      if (opts.productId) {
        var product = productById(opts.productId);
        openSubmit(product, items);
        return;
      }
      if (items.length) {
        setMsg('ssMsg', '\u9009\u4e00\u4e2a\u5546\u54c1\u6295\u7d20\u6750\uff08\u5df2\u4e3a\u4f60\u9009\u597d ' + items.length + ' \u4e2a\u7d20\u6750\uff09');
        state.pendingItems = items;
        return;
      }
      setMsg('ssMsg', '\u9009\u4e00\u4e2a\u5546\u54c1\u6295\u7d20\u6750');
    });
  };

  window.ShopSubmit.openForAssets = function (items, opts) {
    return window.ShopSubmit.open(Object.assign({}, opts || {}, { items: items || [] }));
  };

  window.ShopSubmit.refresh = function () {
    if (state.tab === 'mine') return loadMine(true);
    return loadProducts(true);
  };

  // 待投递素材：打开投稿弹窗时自动带上
  var originalOpenSubmit = openSubmit;
  openSubmit = function (product, presetItems) {
    var items = presetItems && presetItems.length ? presetItems : (state.pendingItems || []);
    state.pendingItems = null;
    return originalOpenSubmit(product, items);
  };

  if (document.readyState !== 'loading') setTimeout(function () { window.initShopSubmitView(); }, 0);
  else document.addEventListener('DOMContentLoaded', function () { window.initShopSubmitView(); });
})();
