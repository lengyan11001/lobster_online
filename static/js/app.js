/** 定死：公网 lobster_server（登录/验证码/auth/me；与 pack_bundle AUTH_SERVER_BASE 一致；走 HTTPS 与 Nginx 443） */
var LOBSTER_SERVER_PUBLIC = 'https://bhzn.top';

(function setApiBaseFromUrl() {
  // 定死：本机回环端口与当前页端口一致（默认 8000）
  var lp = (window.location && window.location.port) ? window.location.port : '8000';
  var startupParams = new URLSearchParams(window.location.search || '');
  // 8765 is only a static preview port. It can serve HTML, but it has no
  // FastAPI endpoints; keep API requests on the real local backend instead.
  var staticPreviewPort = lp === '8765' && startupParams.get('desktop') !== '1';
  var LOBSTER_LOCAL_LOOPBACK = 'http://127.0.0.1:' + (staticPreviewPort ? '8000' : lp);

  // 正式环境：登录/验证码/auth/me 固定走公网认证服务。
  // 调试覆盖：?api=http://127.0.0.1:8002 或 localStorage.lobster_server_api_base
  var p = startupParams;
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
// Some OEMs use an operator-provided balance and must not expose self-service recharge.
function isLobsterRechargeHiddenForBrand() {
  var mark = getLobsterBrandMark();
  return mark === 'daka';
}
window.isLobsterRechargeHiddenForBrand = isLobsterRechargeHiddenForBrand;
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
  var publicAuthPathPattern = /^\/auth\/(?:captcha|login-phone-password|sms\/send|register-phone)(?:\/|$)/;
  var NETWORK_RETRY_DELAYS = [0, 350, 1000, 2500];
  var nativeTaskPathPattern = /^\/api\/native-wechat\/(?:messages\/send|friends\/add|groups\/create|moments\/(?:like|comment|publish))(?:\/|$)/;
  var backendRecoveryPromises = {};
  var backendRecoveryLastSuccessAt = {};
  var authRecoveryPromise = null;
  var RECOVERY_MESSAGE = '连接正在自动恢复，请稍后重试';
  var LOGIN_EXPIRED_MESSAGE = '登录状态已失效，请重新登录';

  function isTransientNetworkError(error) {
    var name = String(error && error.name || '');
    var message = String(error && error.message || error || '');
    return !!(error && error.lobsterNetworkError) || (
      name !== 'AbortError' && /Failed to fetch|fetch(?:ing)?(?:\s+to\b.{0,160}?)?\s+failed|NetworkError|Load failed|network request failed|timed out|timeout/i.test(message)
    );
  }

  function normalizeNetworkError(error) {
    if (!isTransientNetworkError(error)) return error;
    var normalized = new Error(RECOVERY_MESSAGE);
    normalized.name = 'LobsterNetworkError';
    normalized.code = 'NETWORK_UNAVAILABLE';
    normalized.lobsterNetworkError = true;
    normalized.cause = error;
    return normalized;
  }

  function normalizeRecoverableMessage(value) {
    var message = String(value && value.message || value || '');
    if (/无法验证凭证|Could not validate credentials|invalid credentials/i.test(message)) return LOGIN_EXPIRED_MESSAGE;
    if (/Failed to fetch|fetch(?:ing)?(?:\s+to\b.{0,160}?)?\s+failed|NetworkError|Load failed|network request failed/i.test(message)) return RECOVERY_MESSAGE;
    return message;
  }

  window.normalizeLobsterRecoverableMessage = normalizeRecoverableMessage;

  function recoverableTextKind(value) {
    var text = String(value == null ? '' : value);
    if (/无法验证凭证|Could not validate credentials|invalid credentials|not authenticated|invalid token|token expired/i.test(text)) return 'auth';
    if (/Failed to fetch|fetch(?:ing)?(?:\s+to\b.{0,160}?)?\s+failed|NetworkError|Load failed|network request failed/i.test(text)) return 'network';
    return '';
  }

  function payloadErrorText(value) {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.map(payloadErrorText).filter(Boolean).join(' ');
    if (!value || typeof value !== 'object') return '';
    return ['detail', 'error', 'message', 'msg'].map(function(key) {
      return Object.prototype.hasOwnProperty.call(value, key) ? payloadErrorText(value[key]) : '';
    }).filter(Boolean).join(' ');
  }

  function recoverablePayloadKind(value) {
    if (typeof value === 'string') {
      if (/^\s*[\[{]/.test(value)) {
        try { return recoverablePayloadKind(JSON.parse(value)); } catch (e) {}
      }
      return value.length <= 1000 ? recoverableTextKind(value) : '';
    }
    if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
    var numericCode = Number(value.code || value.status_code || (typeof value.status === 'number' ? value.status : 0));
    var state = String(typeof value.status === 'string' ? value.status : value.state || '').toLowerCase();
    var hasErrorField = Object.prototype.hasOwnProperty.call(value, 'detail') || Object.prototype.hasOwnProperty.call(value, 'error');
    var failed = value.ok === false || value.success === false || numericCode >= 400 ||
      /^(?:error|failed|failure|unauthorized|unavailable)$/.test(state) || hasErrorField;
    return failed ? recoverableTextKind(payloadErrorText(value)) : '';
  }

  function sanitizeRecoverablePayload(value, force) {
    if (typeof value === 'string') {
      if (/^\s*[\[{]/.test(value)) {
        try { return JSON.stringify(sanitizeRecoverablePayload(JSON.parse(value), false)); } catch (e) {}
      }
      return force ? normalizeRecoverableMessage(value) : value;
    }
    if (Array.isArray(value)) return force ? value.map(function(item) { return sanitizeRecoverablePayload(item, true); }) : value;
    if (!value || typeof value !== 'object') return value;
    Object.keys(value).forEach(function(key) {
      var errorField = /^(?:detail|error|message|msg)$/i.test(key);
      value[key] = sanitizeRecoverablePayload(value[key], !!force || errorField);
    });
    return value;
  }

  function requestMethod(input, init) {
    var method = (init && init.method) || (input && input.method) || 'GET';
    return String(method || 'GET').toUpperCase();
  }

  function retryableRequest(input, init) {
    return requestMethod(input, init) === 'GET' || requestMethod(input, init) === 'HEAD';
  }

  function requestId() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    } catch (e) {}
    return 'lobster-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
  }

  function storedToken() {
    try {
      if (typeof getStoredAuthToken === 'function') return String(getStoredAuthToken() || '').trim();
    } catch (e) {}
    try { return String(localStorage.getItem('token') || '').trim(); } catch (e2) { return ''; }
  }

  function syncLatestToken(headers, url) {
    var latest = storedToken();
    try { token = latest; } catch (e) {}
    var hadAuthorization = headers.has('Authorization');
    if (latest && (hadAuthorization || !publicAuthPathPattern.test(url.pathname))) {
      headers.set('Authorization', 'Bearer ' + latest);
    } else if (!latest && hadAuthorization) {
      headers.delete('Authorization');
    }
    return latest;
  }

  function backendRecoveryOrigins(preferredOrigin) {
    var values = [];
    // A failed request must recover the same backend that owns that request.
    // A healthy cloud API cannot make a dead local WeChat backend reachable.
    var candidates = String(preferredOrigin || '').trim() ? [preferredOrigin] : apiOrigins();
    candidates.forEach(function(value) {
      try {
        var origin = new URL(String(value || ''), window.location.href).origin;
        if (origin && values.indexOf(origin) < 0) values.push(origin);
      } catch (e) {}
    });
    return values;
  }

  function probeBackend(origin) {
    var url = String(origin || '').replace(/\/$/, '') + '/api/health?fast=1&recovery_probe=1';
    return nativeFetch(url, { method: 'GET', cache: 'no-store' }).then(function(resp) {
      if (!resp.ok) throw new Error('backend health ' + resp.status);
      return true;
    });
  }

  function isLocalRecoveryOrigin(origin) {
    var target = String(origin || '').replace(/\/$/, '');
    var candidates = [];
    try {
      if (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) {
        candidates.push(new URL(String(LOCAL_API_BASE), window.location.href).origin);
      }
    } catch (e) {}
    try {
      var current = window.location && window.location.origin;
      var host = String(window.location && window.location.hostname || '').toLowerCase();
      if (current && (host === '127.0.0.1' || host === 'localhost')) candidates.push(current);
    } catch (e2) {}
    return candidates.indexOf(target) >= 0;
  }

  function invokeDesktopRecovery(origin, reason) {
    if (!isLocalRecoveryOrigin(origin)) return Promise.resolve(false);
    try {
      var api = window.pywebview && window.pywebview.api;
      if (!api || typeof api.recover_local_services !== 'function') return Promise.resolve(false);
      return Promise.resolve(api.recover_local_services(String(reason || 'network')))
        .then(function(result) { return !!(result && result.ok); })
        .catch(function() { return false; });
    } catch (e) {
      return Promise.resolve(false);
    }
  }

  function recoverBackend(preferredOrigin, reason) {
    var origins = backendRecoveryOrigins(preferredOrigin);
    var recoveryKey = origins.join('|') || 'default';
    if (backendRecoveryPromises[recoveryKey]) return backendRecoveryPromises[recoveryKey];
    if (Date.now() - Number(backendRecoveryLastSuccessAt[recoveryKey] || 0) < 2000) {
      return Promise.resolve(true);
    }
    var delays = [0, 500, 1200, 2500, 5000, 8000];
    backendRecoveryPromises[recoveryKey] = new Promise(function(resolve) {
      var index = 0;
      function attempt() {
        var originIndex = 0;
        function nextOrigin() {
          if (originIndex >= origins.length) {
            if (index >= delays.length - 1) return resolve(false);
            var localOrigin = origins.filter(isLocalRecoveryOrigin)[0] || '';
            index += 1;
            var desktopRecovery = index === 1
              ? invokeDesktopRecovery(localOrigin, reason || 'network')
              : Promise.resolve(false);
            return Promise.resolve(desktopRecovery).finally(function() {
              window.setTimeout(attempt, delays[index]);
            });
          }
          var origin = origins[originIndex++];
          probeBackend(origin).then(function() {
            window.__LOBSTER_BACKEND_LAST_RECOVERY_AT = Date.now();
            backendRecoveryLastSuccessAt[recoveryKey] = Date.now();
            resolve(true);
          }).catch(nextOrigin);
        }
        nextOrigin();
      }
      attempt();
    }).finally(function() {
      delete backendRecoveryPromises[recoveryKey];
    });
    return backendRecoveryPromises[recoveryKey];
  }

  window.__lobsterProbeBackend = recoverBackend;
  window.__lobsterRecoverRequest = function(meta) {
    meta = meta || {};
    return recoverBackend(meta.origin || '', meta.reason || 'network');
  };

  function waitForNetworkRetry(ms) {
    return new Promise(function(resolve) { window.setTimeout(resolve, ms); });
  }

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

  function credentialFailureResponse(response, headers) {
    if (!response || !headers.get('Authorization')) return Promise.resolve(false);
    if (response.status !== 401 && response.status !== 403) return Promise.resolve(false);
    if (/bearer/i.test(String(response.headers && response.headers.get('WWW-Authenticate') || ''))) {
      return Promise.resolve(true);
    }
    try {
      return response.clone().text().then(function(body) {
        return /无法验证凭证|Could not validate credentials|invalid credentials|not authenticated|invalid token|token expired/i.test(String(body || ''));
      }).catch(function() { return false; });
    } catch (e) {
      return Promise.resolve(false);
    }
  }

  function connectivityFailureResponse(response) {
    if (!response || response.status < 400) return Promise.resolve(false);
    try {
      return response.clone().text().then(function(body) {
        return /Failed to fetch|fetch(?:ing)?(?:\s+to\b.{0,160}?)?\s+failed|NetworkError|network request failed|认证中心不可达|认证中心暂时不可用/i.test(String(body || ''));
      }).catch(function() { return false; });
    } catch (e) {
      return Promise.resolve(false);
    }
  }

  function jsonResponse(message, status, code) {
    var body = JSON.stringify({
      ok: false,
      code: Number(code || status || 503),
      detail: String(message || RECOVERY_MESSAGE),
      msg: String(message || RECOVERY_MESSAGE),
      recoverable: status !== 401
    });
    return new Response(body, {
      status: status || 503,
      statusText: status === 401 ? 'Unauthorized' : 'Service Unavailable',
      headers: { 'Content-Type': 'application/json; charset=utf-8', 'X-Lobster-Recovered-Error': '1' }
    });
  }

  function validateStoredToken(latest) {
    if (!latest) return Promise.resolve('invalid');
    var base = '';
    try { base = String(typeof API_BASE !== 'undefined' ? API_BASE : '').replace(/\/$/, ''); } catch (e) {}
    if (!base) return Promise.resolve('unavailable');
    return nativeFetch(base + '/auth/me', {
      method: 'GET',
      cache: 'no-store',
      headers: {
        'Authorization': 'Bearer ' + latest,
        'X-Lobster-Brand': getLobsterBrandMark()
      }
    }).then(function(response) {
      if (response.status === 200) return 'valid';
      if (response.status === 401 || response.status === 403) return 'invalid';
      return 'unavailable';
    }).catch(function() { return 'unavailable'; });
  }

  function recoverAuthentication(url, previousToken) {
    if (authRecoveryPromise) return authRecoveryPromise;
    authRecoveryPromise = Promise.resolve().then(function() {
      var latest = storedToken();
      try { token = latest; } catch (e) {}
      if (!latest) return { valid: false, changed: latest !== previousToken, token: latest };
      if (latest !== previousToken) return { valid: true, changed: true, token: latest };
      return validateStoredToken(latest).then(function(state) {
        if (state === 'invalid') return { valid: false, unavailable: false, changed: false, token: latest };
        return recoverBackend(url.origin, state === 'valid' ? 'auth' : 'auth_unavailable').then(function() {
          return { valid: true, unavailable: state === 'unavailable', changed: false, token: latest };
        });
      });
    }).finally(function() {
      authRecoveryPromise = null;
    });
    return authRecoveryPromise;
  }

  function requestViewRecovery(url, reason) {
    if (typeof window.requestLobsterNetworkRecovery !== 'function') return;
    Promise.resolve(window.requestLobsterNetworkRecovery({
      origin: url.origin,
      reason: reason || 'network',
      skipProbe: true
    })).catch(function() {});
  }

  window.fetch = function(input, init) {
    try {
      var rawUrl = typeof input === 'string' ? input : (input && input.url) || '';
      var url = new URL(rawUrl, window.location.href);
      var canRecoverBackend = apiOrigins().indexOf(url.origin) >= 0;
      var shouldAddBrand = apiPathPattern.test(url.pathname) && canRecoverBackend;
      var method = requestMethod(input, init);
      var next = Object.assign({}, init || {});
      var headers = new Headers(input instanceof Request ? input.headers : undefined);
      new Headers(next.headers || {}).forEach(function(value, key) { headers.set(key, value); });
      var isNativeTask = method === 'POST' && nativeTaskPathPattern.test(url.pathname);
      if (isNativeTask && !headers.get('X-Lobster-Request-Id')) headers.set('X-Lobster-Request-Id', requestId());
      var hasIdempotencyKey = !!headers.get('X-Lobster-Request-Id');
      var canRetryAfterRecovery = retryableRequest(input, init) || (isNativeTask && hasIdempotencyKey);
      {
        if (shouldAddBrand) headers.set('X-Lobster-Brand', getLobsterBrandMark());
        if (shouldAddBrand) syncLatestToken(headers, url);
        next.headers = headers;
        var attempt = 0;
        var authAttempt = 0;
        var authValidationUnavailable = false;
        var authTokenConfirmedValid = false;
        var requestTemplate = null;
        if (input instanceof Request) {
          try { requestTemplate = input.clone(); } catch (eTemplate) {}
        }
        function recoverPayload(kind, value, consumer) {
          var sanitized = sanitizeRecoverablePayload(value, typeof value === 'string');
          if (kind === 'auth' && authAttempt < 1) {
            authAttempt += 1;
            return recoverAuthentication(url, storedToken()).then(function(result) {
              if (result && result.valid) {
                authValidationUnavailable = !!result.unavailable;
                authTokenConfirmedValid = !result.unavailable;
                return run().then(function(retried) { return retried[consumer](); });
              }
              return sanitized;
            });
          }
          if (kind === 'auth' && (authValidationUnavailable || authTokenConfirmedValid)) {
            if (canRecoverBackend) requestViewRecovery(url, 'auth_payload_unavailable');
            return Promise.resolve(sanitizeRecoverablePayload({
              ok: false,
              code: 503,
              detail: RECOVERY_MESSAGE,
              msg: RECOVERY_MESSAGE,
              recoverable: true
            }, false)).then(function(payload) {
              return consumer === 'text' ? JSON.stringify(payload) : payload;
            });
          }
          if (kind === 'network' && canRetryAfterRecovery && attempt < NETWORK_RETRY_DELAYS.length - 1) {
            attempt += 1;
            var recovery = canRecoverBackend ? recoverBackend(url.origin, 'payload') : Promise.resolve(false);
            return waitForNetworkRetry(NETWORK_RETRY_DELAYS[attempt]).then(function() {
              return recovery.then(function() {
                return run().then(function(retried) { return retried[consumer](); });
              });
            });
          }
          var finalRecovery = canRecoverBackend ? recoverBackend(url.origin, 'payload') : Promise.resolve(false);
          return finalRecovery.then(function() {
            if (canRecoverBackend) requestViewRecovery(url, 'payload');
            return sanitized;
          });
        }
        function protectApplicationResponse(response) {
          if (!response) return response;
          try {
            var originalJson = response.json.bind(response);
            Object.defineProperty(response, 'json', {
              configurable: true,
              value: function() {
                return originalJson().then(function(value) {
                  var kind = recoverablePayloadKind(value);
                  return kind ? recoverPayload(kind, value, 'json') : value;
                });
              }
            });
            var originalText = response.text.bind(response);
            Object.defineProperty(response, 'text', {
              configurable: true,
              value: function() {
                return originalText().then(function(value) {
                  var kind = recoverablePayloadKind(value);
                  return kind ? recoverPayload(kind, value, 'text') : value;
                });
              }
            });
          } catch (eProtect) {}
          return response;
        }
        function run() {
          var usedToken = shouldAddBrand ? syncLatestToken(headers, url) : '';
          next.headers = headers;
          var requestInput = requestTemplate ? requestTemplate.clone() : input;
          return nativeFetch(requestInput, next).then(function(response) {
            var credentialCheck = shouldAddBrand
              ? credentialFailureResponse(response, headers)
              : Promise.resolve(false);
            return credentialCheck.then(function(isCredentialFailure) {
              if (isCredentialFailure) {
                if (authAttempt < 1) {
                  authAttempt += 1;
                  return recoverAuthentication(url, usedToken).then(function(result) {
                    if (result && result.valid) {
                      authValidationUnavailable = !!result.unavailable;
                      authTokenConfirmedValid = !result.unavailable;
                      return run();
                    }
                    return jsonResponse(LOGIN_EXPIRED_MESSAGE, 401, 401);
                  });
                }
                if (authValidationUnavailable || authTokenConfirmedValid) {
                  requestViewRecovery(url, 'auth_unavailable');
                  return jsonResponse(RECOVERY_MESSAGE, 503, 503);
                }
                return jsonResponse(LOGIN_EXPIRED_MESSAGE, 401, 401);
              }
              return connectivityFailureResponse(response).then(function(isConnectivityFailure) {
                if (!isConnectivityFailure) return protectApplicationResponse(response);
                if (canRetryAfterRecovery && attempt < NETWORK_RETRY_DELAYS.length - 1) {
                  attempt += 1;
                  return waitForNetworkRetry(NETWORK_RETRY_DELAYS[attempt]).then(function() {
                    var recovery = canRecoverBackend
                      ? recoverBackend(url.origin, 'http_' + response.status)
                      : Promise.resolve(false);
                    return recovery.then(run);
                  });
                }
                if (canRecoverBackend) requestViewRecovery(url, 'http_' + response.status);
                if (shouldAddBrand) return jsonResponse(RECOVERY_MESSAGE, 503, 503);
                throw normalizeNetworkError(new Error(RECOVERY_MESSAGE));
              });
            });
          }).catch(function(err) {
            if (!isTransientNetworkError(err)) throw err;
            if (canRetryAfterRecovery && attempt < NETWORK_RETRY_DELAYS.length - 1) {
              attempt += 1;
              return waitForNetworkRetry(NETWORK_RETRY_DELAYS[attempt]).then(function() {
                var recovery = canRecoverBackend
                  ? recoverBackend(url.origin, 'fetch')
                  : Promise.resolve(false);
                return recovery.then(run);
              });
            }
            var finalRecovery = canRecoverBackend
              ? recoverBackend(url.origin, 'fetch')
              : Promise.resolve(false);
            return finalRecovery.then(function() {
              if (canRecoverBackend) requestViewRecovery(url, 'fetch');
              if (shouldAddBrand) return jsonResponse(RECOVERY_MESSAGE, 503, 503);
              throw normalizeNetworkError(err);
            });
          });
        }
        return run();
      }
    } catch (e) {}
    return nativeFetch(input, init).catch(function(error) {
      throw normalizeNetworkError(error);
    });
  };
  window.__LOBSTER_BRAND_FETCH_INSTALLED = true;
})();

(function installNetworkRecovery() {
  if (window.__LOBSTER_NETWORK_RECOVERY_INSTALLED) return;
  var recoveryState = window.__LOBSTER_NETWORK_RECOVERY_STATE = window.__LOBSTER_NETWORK_RECOVERY_STATE || {
    promise: null,
    lastReason: '',
    lastRecoveredAt: 0
  };

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

  function runRecovery(meta) {
    meta = meta || {};
    var view = currentRecoveryView(meta.view);
    if (!view) return Promise.resolve(false);

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

  // Network recovery is request-scoped. Do not use a shared availability state
  // to block or skip requests for the current view.
  window.requestLobsterNetworkRecovery = function(meta) {
    meta = meta || { view: currentRecoveryView(), reason: 'network' };
    if (recoveryState.promise) return recoveryState.promise;
    recoveryState.lastReason = String(meta.reason || 'network');
    var probe = !meta.skipProbe && typeof window.__lobsterProbeBackend === 'function'
      ? window.__lobsterProbeBackend(meta.origin || '')
      : Promise.resolve(true);
    recoveryState.promise = Promise.resolve(probe).then(function() {
      return runRecovery(meta);
    }).then(function(result) {
      recoveryState.lastRecoveredAt = Date.now();
      return result;
    }).finally(function() {
      recoveryState.promise = null;
    });
    return recoveryState.promise;
  };
  window.refreshCurrentLobsterView = function(meta) {
    meta = meta || { view: currentRecoveryView(), reason: 'manual' };
    return runRecovery(meta);
  };
  window.__LOBSTER_NETWORK_RECOVERY_INSTALLED = true;
})();

var token = getStoredAuthToken();
/*
 * Video compatibility stays on the Online machine.  The local endpoint
 * probes the source and only invokes the bundled ffmpeg for HEVC/WebM or
 * other formats the embedded WebView cannot decode.  All callers can keep
 * passing their original URL; this hook covers dynamically-rendered videos
 * across every view as well.
 */
window.lobsterVideoCompatUrl = function(rawUrl, filename, variant) {
  var raw = String(rawUrl || '').trim();
  if (!raw || /^(?:blob:|data:|file:)/i.test(raw) || /\/api\/media\/compat(?:[/?]|$)/i.test(raw)) return raw;
  var local = String(typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  if (!local) return raw;
  var absolute = raw;
  try { absolute = new URL(raw, raw.charAt(0) === '/' && local ? local + '/' : window.location.href).href; } catch (e) {}
  var params = new URLSearchParams();
  params.set('url', absolute);
  params.set('filename', String(filename || 'digital-human.mp4'));
  var tokenValue = String(typeof token !== 'undefined' ? (token || '') : '');
  if (tokenValue) params.set('token', tokenValue);
  if (variant) params.set('variant', String(variant));
  return local + '/api/media/compat?' + params.toString();
};
window.lobsterVideoPosterUrl = function(rawUrl, filename) {
  return window.lobsterVideoCompatUrl(rawUrl, filename, 'poster');
};

/* Convert video sources at the DOM boundary so legacy and newly-added views
 * receive the same behavior without duplicating media logic in each module. */
(function installVideoCompatibilityObserver() {
  function upgrade(video) {
    if (!video || !video.getAttribute || video.dataset.lobsterVideoCompat === '1') return;
    var source = String(video.getAttribute('src') || '').trim();
    if (!source && video.querySelector) {
      var childSource = video.querySelector('source[src]');
      source = String(childSource && childSource.getAttribute('src') || '').trim();
    }
    if (!source || /^(?:blob:|data:|file:)/i.test(source)) return;
    if (/\/api\/media\/compat(?:[/?]|$)|\/api\/multi-clip-mixer\/assets\/[^/]+\/playback\.mp4/i.test(source)) return;
    var name = String(source.split(/[?#]/)[0].split('/').pop() || 'digital-human.mp4');
    if (!/\.[a-z0-9]{2,8}$/i.test(name)) name = 'digital-human.mp4';
    var converted = window.lobsterVideoCompatUrl(source, name, '');
    if (!converted || converted === source) return;
    video.dataset.lobsterVideoCompat = '1';
    video.dataset.lobsterOriginalSrc = source;
    video.addEventListener('error', function() {
      if (video.dataset.lobsterVideoCompatFallback === '1') return;
      video.dataset.lobsterVideoCompatFallback = '1';
      video.setAttribute('src', source);
      try { video.load(); } catch (e) {}
    }, { once: true });
    if (!video.getAttribute('poster') && typeof window.lobsterVideoPosterUrl === 'function') {
      video.setAttribute('poster', window.lobsterVideoPosterUrl(source, 'video-cover.jpg'));
    }
    video.setAttribute('src', converted);
    if (video.querySelectorAll) {
      Array.prototype.forEach.call(video.querySelectorAll('source[src]'), function(childSource) {
        childSource.setAttribute('src', converted);
      });
    }
  }
  function scan(root) {
    if (!root) return;
    if (root.tagName === 'SOURCE' && root.parentElement) {
      upgrade(root.parentElement);
      return;
    }
    if (root.tagName === 'VIDEO') upgrade(root);
    if (root.querySelectorAll) Array.prototype.forEach.call(root.querySelectorAll('video'), upgrade);
  }
  function start() {
    scan(document);
    if (!window.MutationObserver || !document.body) return;
    new MutationObserver(function(records) {
      records.forEach(function(record) {
        if (record.type === 'attributes') scan(record.target);
        Array.prototype.forEach.call(record.addedNodes || [], scan);
      });
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
// The task center must wait for /auth/me to validate this token.
window.__lobsterAuthReady = false;
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
  el.textContent = typeof window.normalizeLobsterRecoverableMessage === 'function'
    ? window.normalizeLobsterRecoverableMessage(text)
    : text;
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
  try { token = getStoredAuthToken(); } catch (e) {}
  var h = { 'Content-Type': 'application/json' };
  if (token) h.Authorization = 'Bearer ' + token;
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

function _stringifyUiValue(s) {
  if (s == null) return '';
  if (typeof s === 'string') return s;
  if (typeof s === 'object') {
    try { return JSON.stringify(s); } catch (e) { return String(s); }
  }
  return String(s);
}
function escapeHtml(s) { return _stringifyUiValue(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function escapeAttr(s) { return _stringifyUiValue(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function truncate(s, len) { s = _stringifyUiValue(s).trim(); return s.length <= len ? s : s.slice(0, len) + '…'; }
