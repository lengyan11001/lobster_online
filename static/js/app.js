/** 定死：公网 lobster_server（登录/验证码/auth/me；与 pack_bundle AUTH_SERVER_BASE 一致；走 HTTPS 与 Nginx 443） */
var LOBSTER_SERVER_PUBLIC = 'https://bhzn.top';

(function setApiBaseFromUrl() {
  // 定死：本机回环端口与当前页端口一致（默认 8000）
  var lp = (window.location && window.location.port) ? window.location.port : '8000';
  var LOBSTER_LOCAL_LOOPBACK = 'http://127.0.0.1:' + lp;

  // 正式环境：登录/验证码/auth/me 固定走公网认证服务。
  // 调试覆盖：?api=http://127.0.0.1:8002 或 localStorage.lobster_server_api_base
  var p = new URLSearchParams(window.location.search);
  var serverApiOverride = (p.get('api') || '').trim();
  if (serverApiOverride) {
    try { localStorage.setItem('lobster_server_api_base', serverApiOverride.replace(/\/$/, '')); } catch (eApi) {}
  } else {
    try { serverApiOverride = (localStorage.getItem('lobster_server_api_base') || '').trim(); } catch (eApi2) { serverApiOverride = ''; }
  }
  window.__API_BASE = serverApiOverride ? serverApiOverride.replace(/\/$/, '') : LOBSTER_SERVER_PUBLIC;

  window.__LOCAL_API_BASE = (typeof window.__LOCAL_API_BASE !== 'undefined' ? window.__LOCAL_API_BASE : '');
  var exLocal = String(window.__LOCAL_API_BASE || '').trim();
  if (!exLocal && window.location && /^https?:/i.test(window.location.protocol || '')) {
    var h = (window.location.hostname || '').toLowerCase();
    // 公网静态页也可显式指向本机 lobster_online（内网 IP / 穿透 URL）：?local_api= 或 localStorage.lobster_local_api_base
    var localApiOverride = (p.get('local_api') || '').trim() || (localStorage.getItem('lobster_local_api_base') || '').trim();
    if (p.get('local_api')) {
      try { localStorage.setItem('lobster_local_api_base', localApiOverride.replace(/\/$/, '')); } catch (eLoc) {}
    }
    if (h === 'localhost' || h === '127.0.0.1') {
      // 架构：LOCAL_API_BASE = 本机 lobster_online 后端（对话/素材/发布/OpenClaw 扫码）；推荐与页面同源（backend/run.py 与静态同端口）。
      window.__LOCAL_API_BASE = localApiOverride ? localApiOverride.replace(/\/$/, '') : LOBSTER_LOCAL_LOOPBACK;
    } else if (
      /^192\.168\.\d{1,3}\.\d{1,3}$/.test(h) ||
      /^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h) ||
      /^172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}$/.test(h)
    ) {
      window.__LOCAL_API_BASE = localApiOverride ? localApiOverride.replace(/\/$/, '') : window.location.origin;
    } else if (localApiOverride) {
      window.__LOCAL_API_BASE = localApiOverride.replace(/\/$/, '');
    }
  }
})();
// 本机打开静态页时 API_BASE 仍指向远程；LOCAL_API_BASE 默认同源（见 docs/架构说明_server与本地职责.md）
var API_BASE = window.__API_BASE || LOBSTER_SERVER_PUBLIC;
/** 发布与素材接口走本地（同源）；需本机运行 lobster_online 并配置 AUTH_SERVER_BASE */
var LOCAL_API_BASE = (typeof window.__LOCAL_API_BASE !== 'undefined' ? window.__LOCAL_API_BASE : '');
/** Messenger：默认海外 lobster_server（与 Meta Webhook 同机）；?messenger_api= / localStorage 可覆盖 */
(function setMessengerApiBase() {
  /** 使用 https:// 与 443，避免 https 前端页对 http:8000 的混合内容拦截；与 Nginx 反代一致 */
  var def = 'http://43.162.111.36';
  var p = new URLSearchParams(window.location.search);
  var m = (p.get('messenger_api') || '').trim() || (localStorage.getItem('lobster_messenger_api_base') || '').trim() || def;
  if (m === 'http://43.162.111.36:8000') {
    m = def;
  }
  if (m) localStorage.setItem('lobster_messenger_api_base', m);
  window.__MESSENGER_API_BASE = m;
})();
var MESSENGER_API_BASE = (typeof window.__MESSENGER_API_BASE !== 'undefined' ? window.__MESSENGER_API_BASE : '');
/** Twilio：与企微一致默认走本机同源 LOCAL_API_BASE；仅 ?twilio_api= / localStorage 显式指定时才打其它根地址（调试） */
(function setTwilioApiBase() {
  var p = new URLSearchParams(window.location.search);
  var q = (p.get('twilio_api') || '').trim();
  if (q) {
    if (q === 'http://43.162.111.36:8000') q = 'http://43.162.111.36';
    try { localStorage.setItem('lobster_twilio_api_base', q); } catch (e) {}
    window.__TWILIO_API_BASE = q;
  } else {
    var v = (localStorage.getItem('lobster_twilio_api_base') || '').trim();
    // 旧版默认写死海外根地址会导致浏览器直连跨域 Failed to fetch；与企微一致改走后，清除该默认值
    if (v === 'http://43.162.111.36' || v === 'http://43.162.111.36:8000' || v === 'https://lobster-server.icu' || v === 'http://lobster-server.icu:8000') {
      try { localStorage.removeItem('lobster_twilio_api_base'); } catch (e2) {}
      window.__TWILIO_API_BASE = '';
    } else {
      window.__TWILIO_API_BASE = v;
    }
  }
})();
var TWILIO_API_BASE = (typeof window.__TWILIO_API_BASE !== 'undefined' ? window.__TWILIO_API_BASE : '');

function lobsterBrandingUnavailable() {
  return window.__LOBSTER_BRANDING_AVAILABLE__ === false;
}
function normalizeLobsterBrandMark(raw) {
  if (lobsterBrandingUnavailable()) return '';
  var mark = String(raw || 'bihuo').trim().toLowerCase();
  return /^[a-z][a-z0-9_-]{0,62}$/.test(mark) ? mark : 'bihuo';
}
function getLobsterBrandMark() {
  if (lobsterBrandingUnavailable()) return '';
  return normalizeLobsterBrandMark(
    window.__LOBSTER_BRAND_MARK || localStorage.getItem('lobster_active_brand_mark') || 'bihuo'
  );
}
function lobsterTokenStorageKey(mark) {
  return 'token:' + normalizeLobsterBrandMark(mark || getLobsterBrandMark());
}
function getStoredAuthToken() {
  var mark = getLobsterBrandMark();
  var key = lobsterTokenStorageKey(mark);
  var stored = localStorage.getItem(key) || '';
  if (!stored && mark === 'bihuo') {
    stored = localStorage.getItem('token') || '';
    if (stored) localStorage.setItem(key, stored);
  }
  return stored;
}
function setStoredAuthToken(value) {
  var next = String(value || '');
  localStorage.setItem(lobsterTokenStorageKey(), next);
  if (getLobsterBrandMark() === 'bihuo') localStorage.setItem('token', next);
}
function clearStoredAuthToken() {
  localStorage.removeItem(lobsterTokenStorageKey());
  if (getLobsterBrandMark() === 'bihuo') localStorage.removeItem('token');
}
function setLobsterBrandMark(mark) {
  if (lobsterBrandingUnavailable()) return '';
  var next = normalizeLobsterBrandMark(mark);
  window.__LOBSTER_BRAND_MARK = next;
  if (document && document.documentElement) {
    document.documentElement.setAttribute('data-brand', next);
  }
  localStorage.setItem('lobster_active_brand_mark', next);
  token = getStoredAuthToken();
  return next;
}
window.getLobsterBrandMark = getLobsterBrandMark;
window.getStoredAuthToken = getStoredAuthToken;
window.setStoredAuthToken = setStoredAuthToken;
window.clearStoredAuthToken = clearStoredAuthToken;
window.setLobsterBrandMark = setLobsterBrandMark;
document.documentElement.setAttribute('data-brand', getLobsterBrandMark());

(function installBrandRequestContext() {
  if (window.__LOBSTER_BRAND_FETCH_INSTALLED || typeof window.fetch !== 'function') return;
  var nativeFetch = window.fetch.bind(window);
  var apiPathPattern = /^\/(?:api|auth|chat|skills|capabilities)(?:\/|$)/;

  function apiOrigins() {
    var values = [];
    [
      window.location && window.location.origin,
      typeof API_BASE !== 'undefined' ? API_BASE : '',
      typeof LOCAL_API_BASE !== 'undefined' ? LOCAL_API_BASE : ''
    ].forEach(function(value) {
      try {
        var origin = new URL(String(value || ''), window.location.href).origin;
        if (origin && values.indexOf(origin) < 0) values.push(origin);
      } catch (e) {}
    });
    return values;
  }

  window.fetch = function(input, init) {
    try {
      var rawUrl = typeof input === 'string' ? input : (input && input.url) || '';
      var url = new URL(rawUrl, window.location.href);
      if (apiPathPattern.test(url.pathname) && apiOrigins().indexOf(url.origin) >= 0) {
        var next = Object.assign({}, init || {});
        var headers = new Headers(input instanceof Request ? input.headers : undefined);
        new Headers(next.headers || {}).forEach(function(value, key) { headers.set(key, value); });
        headers.set('X-Lobster-Brand', getLobsterBrandMark());
        next.headers = headers;
        return nativeFetch(input, next).catch(function(err) {
          try {
            var msg = String(err && err.message ? err.message : err || '');
            var name = String(err && err.name ? err.name : '');
            if (name !== 'AbortError' && /Failed to fetch|NetworkError|Load failed/i.test(msg) && typeof window.requestLobsterNetworkRecovery === 'function') {
              window.requestLobsterNetworkRecovery({
                view: currentView,
                reason: 'fetch',
                context: {
                  url: String(url.pathname || ''),
                  origin: String(url.origin || ''),
                  message: msg
                }
              });
            }
          } catch (eRecover) {}
          throw err;
        });
      }
    } catch (e) {}
    return nativeFetch(input, init);
  };
  window.__LOBSTER_BRAND_FETCH_INSTALLED = true;
})();

(function installNetworkRecovery() {
  if (window.__LOBSTER_NETWORK_RECOVERY_INSTALLED) return;

  // Recovery guard only: debounce repeated retries, not a business availability flag.
  var state = window.__LOBSTER_NETWORK_RECOVERY_STATE = window.__LOBSTER_NETWORK_RECOVERY_STATE || {
    timer: null,
    lastAttemptAt: 0
  };

  function clearRecoveryTimer() {
    if (state.timer != null) {
      try { clearTimeout(state.timer); } catch (e) {}
      state.timer = null;
    }
  }

  function currentRecoveryView(metaView) {
    var view = String(metaView || '').trim();
    if (view) return view;
    try {
      if (typeof currentView !== 'undefined' && currentView) return String(currentView).trim();
    } catch (e) {}
    try {
      if (window.__LOBSTER_LAST_ACTIVE_VIEW) return String(window.__LOBSTER_LAST_ACTIVE_VIEW).trim();
    } catch (e2) {}
    return '';
  }

  function runRecovery(meta, force) {
    meta = meta || {};
    var view = currentRecoveryView(meta.view);
    if (!view) return Promise.resolve(false);
    if (!force && Date.now() - state.lastAttemptAt < 8000) return Promise.resolve(false);
    state.lastAttemptAt = Date.now();

    if (view === 'chat') {
      if (typeof window.refreshMastraOnlineChat === 'function') {
        return Promise.resolve(window.refreshMastraOnlineChat(meta)).then(function() { return true; }).catch(function() { return false; });
      }
    }

    if (typeof window.showAppView === 'function') {
      return Promise.resolve(window.showAppView(view)).then(function() { return true; }).catch(function() { return false; });
    }

    if (typeof window.location !== 'undefined' && window.location && typeof window.location.reload === 'function') {
      window.location.reload();
      return Promise.resolve(true);
    }

    return Promise.resolve(false);
  }

  function scheduleRecovery(meta) {
    meta = meta || {};
    var view = currentRecoveryView(meta.view);
    if (!view) return Promise.resolve(false);
    if (state.timer != null) return Promise.resolve(false);
    if (Date.now() - state.lastAttemptAt < 3000) return Promise.resolve(false);
    var delay = 900;
    state.timer = window.setTimeout(function() {
      clearRecoveryTimer();
      runRecovery(meta, false);
    }, delay);
    return Promise.resolve(true);
  }

  window.requestLobsterNetworkRecovery = scheduleRecovery;
  window.refreshCurrentLobsterView = function(meta) {
    clearRecoveryTimer();
    return runRecovery(meta || { view: currentRecoveryView(), reason: 'manual' }, true);
  };
  window.__LOBSTER_NETWORK_RECOVERY_INSTALLED = true;

  window.addEventListener('online', function() {
    runRecovery({ view: currentRecoveryView(), reason: 'online' }, false);
  });
})();

var token = getStoredAuthToken();
var currentView = 'chat';
/** 在线版前端，默认连 lobster_server（注册/登录在 server 上） */
var EDITION = 'online';
/** 在线版是否允许自配模型（由 /api/edition 返回） */
var ALLOW_SELF_CONFIG_MODEL = true;
/** 在线版充值页 URL（由 /api/edition 返回） */
var RECHARGE_URL = null;

(function applyTokenFromUrl() {
  var params = new URLSearchParams(window.location.search);
  var brandFromUrl = params.get('brand') || params.get('brand_mark');
  if (brandFromUrl) setLobsterBrandMark(brandFromUrl);
  var t = params.get('token');
  if (t && t.length > 10) {
    token = t;
    setStoredAuthToken(t);
    window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);
  }
})();

function showMsg(el, text, isErr) {
  if (!el) return;
  el.textContent = text;
  el.className = 'msg ' + (isErr ? 'err' : 'ok');
  el.style.display = 'block';
}

function copyToClipboard(text, doneCb) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function() { if (doneCb) doneCb(); }).catch(function() {
      fallbackCopy(text, doneCb);
    });
  } else {
    fallbackCopy(text, doneCb);
  }
}
function fallbackCopy(text, doneCb) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed'; ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); if (doneCb) doneCb(); } catch (e) {}
  document.body.removeChild(ta);
}

/** 在线版：与认证中心「每账号最多 3 安装槽」对应的设备身份（持久化于 localStorage） */
var DEPRECATED_INSTALLATION_IDS = {
  '2fc3f43f7a684411a442cb661898aa74': true,
  'fa2d09cfbd9c4b2380352906225f2817': true
};
var LOBSTER_INSTALLATION_STORAGE_KEY = 'lobster_installation_id';
var LOBSTER_DEVICE_SEED_STORAGE_KEY = 'lobster_device_seed';
var LOBSTER_MACHINE_INSTANCE_STORAGE_KEY = 'lobster_machine_instance_id';

function rawInstallationId(value) {
  var text = String(value || '').trim();
  return text.indexOf('--') >= 0 ? text.split('--').slice(1).join('--') : text;
}

function isDeprecatedInstallationId(value) {
  return !!DEPRECATED_INSTALLATION_IDS[rawInstallationId(value)];
}

function isValidLobsterInstallationId(value) {
  return /^[a-zA-Z0-9_-]{8,128}$/.test(String(value || '').trim());
}

function generateLobsterInstallationId() {
  return (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID().replace(/-/g, '')
    : (Date.now().toString(36) + Math.random().toString(36).slice(2, 18));
}

function currentStoredInstallationId() {
  try { return localStorage.getItem(LOBSTER_INSTALLATION_STORAGE_KEY) || ''; } catch (e) { return ''; }
}

function currentStoredLobsterDeviceSeed() {
  try { return localStorage.getItem(LOBSTER_DEVICE_SEED_STORAGE_KEY) || ''; } catch (e) { return ''; }
}

function currentStoredLobsterMachineInstanceId() {
  try { return localStorage.getItem(LOBSTER_MACHINE_INSTANCE_STORAGE_KEY) || ''; } catch (e) { return ''; }
}

function setLobsterMachineInstanceId(value, meta) {
  var next = String(value || '').trim();
  if (!isValidLobsterInstallationId(next)) throw new Error('invalid machine instance id');
  var prev = currentStoredLobsterMachineInstanceId();
  try { localStorage.setItem(LOBSTER_MACHINE_INSTANCE_STORAGE_KEY, next); } catch (e) {}
  if (prev !== next && typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    try {
      window.dispatchEvent(new CustomEvent('lobster:machine-instance-id-changed', {
        detail: Object.assign({ previous: prev, current: next }, meta || {})
      }));
    } catch (e2) {}
  }
  return next;
}

function getCachedLobsterMachineInstanceId() {
  var value = currentStoredLobsterMachineInstanceId();
  if (isValidLobsterInstallationId(value)) return value;
  return '';
}

function loadLobsterMachineInstanceId() {
  var cached = getCachedLobsterMachineInstanceId();
  var base = String((typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? LOCAL_API_BASE : '').replace(/\/$/, '');
  if (!base || typeof fetch !== 'function') return Promise.resolve(cached || '');
  return fetch(base + '/api/settings/machine-identity', {
    method: 'GET',
    headers: uniqueInstallationRequestHeaders(false)
  }).then(function(resp) {
    return resp.json().catch(function() { return {}; }).then(function(data) {
      var next = String(data && data.machine_instance_id || '').trim();
      if (resp.ok && isValidLobsterInstallationId(next)) {
        return setLobsterMachineInstanceId(next, { reason: 'local_machine_identity' });
      }
      return cached || '';
    });
  }).catch(function() {
    return cached || '';
  });
}

function setLobsterDeviceSeed(value, meta) {
  var next = String(value || '').trim();
  if (!isValidLobsterInstallationId(next)) throw new Error('invalid device id');
  var prev = currentStoredLobsterDeviceSeed();
  try { localStorage.setItem(LOBSTER_DEVICE_SEED_STORAGE_KEY, next); } catch (e) {}
  if (prev !== next && typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    try {
      window.dispatchEvent(new CustomEvent('lobster:device-seed-changed', {
        detail: Object.assign({ previous: prev, current: next }, meta || {})
      }));
    } catch (e2) {}
  }
  return next;
}

function getOrCreateLobsterDeviceSeed() {
  var seed = currentStoredLobsterDeviceSeed();
  if (isDeprecatedInstallationId(seed)) {
    try { localStorage.removeItem(LOBSTER_DEVICE_SEED_STORAGE_KEY); } catch (e0) {}
    seed = '';
  }
  if (isValidLobsterInstallationId(seed)) return seed;
  var existing = currentStoredInstallationId();
  if (isValidLobsterInstallationId(existing) && !/^u\d+-[a-f0-9]{32}$/i.test(existing)) {
    return setLobsterDeviceSeed(rawInstallationId(existing), { reason: 'migrate_from_installation_id' });
  }
  return setLobsterDeviceSeed(generateLobsterInstallationId(), { reason: 'local_seed_initial' });
}

function randomizeLobsterDeviceSeed(meta) {
  return setLobsterDeviceSeed(generateLobsterInstallationId(), meta || { reason: 'manual_randomize' });
}

function setLobsterInstallationId(value, meta) {
  var next = String(value || '').trim();
  if (!isValidLobsterInstallationId(next)) throw new Error('invalid installation id');
  var prev = currentStoredInstallationId();
  try { localStorage.setItem(LOBSTER_INSTALLATION_STORAGE_KEY, next); } catch (e) {}
  if (prev !== next && typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    try {
      window.dispatchEvent(new CustomEvent('lobster:installation-id-changed', {
        detail: Object.assign({ previous: prev, current: next }, meta || {})
      }));
    } catch (e2) {}
  }
  return next;
}

function uniqueInstallationRequestHeaders(json) {
  var headers = {};
  if (json !== false) headers['Content-Type'] = 'application/json';
  try { headers['X-Lobster-Brand'] = getLobsterBrandMark(); } catch (e) {}
  return headers;
}

function requestUniqueLobsterInstallationId(options) {
  options = options || {};
  var base = String((typeof API_BASE !== 'undefined' && API_BASE) ? API_BASE : '').replace(/\/$/, '');
  var candidate = String(options.candidate || '').trim();
  if (!base || typeof fetch !== 'function') {
    return Promise.resolve({
      ok: false,
      offline: true,
      installation_id: candidate && isValidLobsterInstallationId(candidate) ? candidate : generateLobsterInstallationId()
    });
  }
  return fetch(base + '/api/installation-id/ensure', {
    method: 'POST',
    headers: uniqueInstallationRequestHeaders(true),
    body: JSON.stringify({
      candidate: candidate || null,
      force_new: !!options.forceNew,
      brand_mark: typeof getLobsterBrandMark === 'function' ? getLobsterBrandMark() : undefined
    })
  }).then(function(resp) {
    return resp.json().catch(function() { return {}; }).then(function(data) {
      if (!resp.ok || !data.installation_id) throw new Error(data.detail || data.message || 'installation id check failed');
      return data;
    });
  });
}

function bindLobsterInstallationId(options) {
  options = options || {};
  var base = String((typeof API_BASE !== 'undefined' && API_BASE) ? API_BASE : '').replace(/\/$/, '');
  if (!base || typeof fetch !== 'function') return Promise.reject(new Error('API_BASE unavailable'));
  var deviceId = String(options.deviceId || '').trim();
  if (!deviceId && options.forceNew) deviceId = randomizeLobsterDeviceSeed({ reason: 'bind_force_new' });
  if (!deviceId) deviceId = getOrCreateLobsterDeviceSeed();
  var machineInstanceId = String(options.machineInstanceId || options.machine_instance_id || getCachedLobsterMachineInstanceId() || '').trim();
  return fetch(base + '/api/installation-id/bind', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({
      installation_id: options.installationId || getOrCreateInstallationId(),
      device_id: deviceId,
      machine_instance_id: machineInstanceId || null,
      force_new: !!options.forceNew
    })
  }).then(function(resp) {
    return resp.json().catch(function() { return {}; }).then(function(data) {
      if (!resp.ok || !data.installation_id) throw new Error(data.detail || data.message || 'installation id bind failed');
      return data;
    });
  });
}

function loadLobsterInstallationIdStatus() {
  var base = String((typeof API_BASE !== 'undefined' && API_BASE) ? API_BASE : '').replace(/\/$/, '');
  if (!base || typeof fetch !== 'function') return Promise.reject(new Error('API_BASE unavailable'));
  return fetch(base + '/api/installation-id/status', {
    method: 'GET',
    headers: authHeaders()
  }).then(function(resp) {
    return resp.json().catch(function() { return {}; }).then(function(data) {
      if (!resp.ok) throw new Error(data.detail || data.message || 'installation id status failed');
      return data;
    });
  });
}

function scheduleInitialInstallationIdCheck(candidate) {
  // Kept only for old pages that may still call it. New Online clients bind
  // the effective slot after auth; unauthenticated startup must not rewrite the
  // formal installation id asynchronously.
  if (!candidate || window.__LOBSTER_INSTALLATION_INIT_CHECK__) return;
  window.__LOBSTER_INSTALLATION_INIT_CHECK__ = true;
  requestUniqueLobsterInstallationId({ candidate: candidate }).then(function(data) {
    var next = String(data && data.installation_id || '').trim();
    if (next && next !== candidate && typeof console !== 'undefined' && console.info) {
      console.info('[installation-id] initial uniqueness result ignored until auth bind', { candidate: candidate, suggested: next });
    }
  }).catch(function(err) {
    console.warn('[installation-id] initial uniqueness check skipped', err);
  });
}

function getOrCreateInstallationId() {
  var v = currentStoredInstallationId();
  if (isDeprecatedInstallationId(v)) {
    try { localStorage.removeItem(LOBSTER_INSTALLATION_STORAGE_KEY); } catch (e0) {}
    v = '';
  }
  if (isValidLobsterInstallationId(v)) return v;
  var u = setLobsterInstallationId(getOrCreateLobsterDeviceSeed(), { reason: 'local_initial' });
  scheduleInitialInstallationIdCheck(u);
  return u;
}

window.generateLobsterInstallationId = generateLobsterInstallationId;
window.getOrCreateInstallationId = getOrCreateInstallationId;
window.setLobsterInstallationId = setLobsterInstallationId;
window.getOrCreateLobsterDeviceSeed = getOrCreateLobsterDeviceSeed;
window.setLobsterDeviceSeed = setLobsterDeviceSeed;
window.randomizeLobsterDeviceSeed = randomizeLobsterDeviceSeed;
window.getCachedLobsterMachineInstanceId = getCachedLobsterMachineInstanceId;
window.setLobsterMachineInstanceId = setLobsterMachineInstanceId;
window.loadLobsterMachineInstanceId = loadLobsterMachineInstanceId;
window.requestUniqueLobsterInstallationId = requestUniqueLobsterInstallationId;
window.bindLobsterInstallationId = bindLobsterInstallationId;
window.loadLobsterInstallationIdStatus = loadLobsterInstallationIdStatus;

function authHeaders() {
  var h = { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (token || '') };
  h['X-Installation-Id'] = getOrCreateInstallationId();
  h['X-Lobster-Brand'] = getLobsterBrandMark();
  if (window.__LOBSTER_IS_OVERSEAS_USER) h['X-Lobster-Client-Overseas'] = 'true';
  return h;
}

/** 从 JWT payload 解析 sub（与认证中心签发一致），供本地会话等按用户隔离；无 token 或解析失败返回空串 */
function getCurrentUserIdFromToken() {
  try {
    var t = (typeof token !== 'undefined' && token) ? token : getStoredAuthToken();
    if (!t || t.indexOf('.') < 0) return '';
    var parts = t.split('.');
    if (parts.length < 2) return '';
    var b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    while (b64.length % 4) b64 += '=';
    var payload = JSON.parse(atob(b64));
    var sub = payload.sub;
    if (sub == null || sub === '') return '';
    return String(sub);
  } catch (e) {
    return '';
  }
}
window.getCurrentUserIdFromToken = getCurrentUserIdFromToken;

function escapeHtml(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function escapeAttr(s) { return (s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function truncate(s, len) { s = (s || '').trim(); return s.length <= len ? s : s.slice(0, len) + '…'; }
