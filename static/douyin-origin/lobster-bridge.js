(function installLobsterDouyinBridge() {
  var RECOVERY_MESSAGE = '连接正在自动恢复，请稍后重试';
  var LOGIN_EXPIRED_MESSAGE = '登录状态已失效，请重新登录';
  var RETRY_DELAYS = [0, 350, 1000, 2500];
  var recoveryPromise = null;

  function parentWindow() {
    try { return window.parent && window.parent !== window ? window.parent : null; } catch (err) { return null; }
  }

  function readStorageValue(key) {
    var parent = parentWindow();
    try {
      if (parent && parent.localStorage) {
        var parentValue = parent.localStorage.getItem(key) || '';
        if (parentValue) return parentValue;
      }
    } catch (err) {}
    try { return window.localStorage.getItem(key) || ''; } catch (innerErr) { return ''; }
  }

  function readBrandMark() {
    return String(readStorageValue('lobster_active_brand_mark') || 'bihuo').trim().toLowerCase() || 'bihuo';
  }

  function readLatestToken() {
    var parent = parentWindow();
    try {
      if (parent && typeof parent.getStoredAuthToken === 'function') {
        return String(parent.getStoredAuthToken() || '').trim();
      }
    } catch (err) {}
    var mark = readBrandMark();
    return String(readStorageValue('token:' + mark) || (mark === 'bihuo' ? readStorageValue('token') : '') || '').trim();
  }

  function readInstallationId() {
    var id = String(readStorageValue('lobster_installation_id') || readStorageValue('installation_id') || '').trim();
    if (!id && typeof window.getOrCreateInstallationId === 'function') {
      try { id = String(window.getOrCreateInstallationId() || '').trim(); } catch (err) {}
    }
    return id;
  }

  function methodOf(input, init) {
    return String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
  }

  function wait(ms) {
    return new Promise(function(resolve) { window.setTimeout(resolve, ms); });
  }

  function isNetworkError(error) {
    var name = String(error && error.name || '');
    var message = String(error && error.message || error || '');
    return name !== 'AbortError' && /Failed to fetch|fetch(?:ing)?(?:\s+to\b.{0,160}?)?\s+failed|NetworkError|Load failed|network request failed/i.test(message);
  }

  function jsonResponse(message, status) {
    var body = JSON.stringify({
      ok: false,
      code: status || 503,
      detail: message,
      msg: message,
      recoverable: status !== 401
    });
    return new Response(body, {
      status: status || 503,
      statusText: status === 401 ? 'Unauthorized' : 'Service Unavailable',
      headers: { 'Content-Type': 'application/json; charset=utf-8', 'X-Lobster-Recovered-Error': '1' }
    });
  }

  function payloadTextKind(value) {
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

  function payloadFailureKind(value) {
    if (typeof value === 'string') {
      if (/^\s*[\[{]/.test(value)) {
        try { return payloadFailureKind(JSON.parse(value)); } catch (err) {}
      }
      return value.length <= 1000 ? payloadTextKind(value) : '';
    }
    if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
    var numericCode = Number(value.code || value.status_code || (typeof value.status === 'number' ? value.status : 0));
    var state = String(typeof value.status === 'string' ? value.status : value.state || '').toLowerCase();
    var hasErrorField = Object.prototype.hasOwnProperty.call(value, 'detail') || Object.prototype.hasOwnProperty.call(value, 'error');
    var failed = value.ok === false || value.success === false || numericCode >= 400 ||
      /^(?:error|failed|failure|unauthorized|unavailable)$/.test(state) || hasErrorField;
    return failed ? payloadTextKind(payloadErrorText(value)) : '';
  }

  function sanitizePayload(value, force) {
    if (typeof value === 'string') {
      if (/^\s*[\[{]/.test(value)) {
        try { return JSON.stringify(sanitizePayload(JSON.parse(value), false)); } catch (err) {}
      }
      if (!force) return value;
      if (/无法验证凭证|Could not validate credentials|invalid credentials|not authenticated|invalid token|token expired/i.test(value)) return LOGIN_EXPIRED_MESSAGE;
      if (/Failed to fetch|fetch(?:ing)?(?:\s+to\b.{0,160}?)?\s+failed|NetworkError|Load failed|network request failed/i.test(value)) return RECOVERY_MESSAGE;
      return value;
    }
    if (Array.isArray(value)) return force ? value.map(function(item) { return sanitizePayload(item, true); }) : value;
    if (!value || typeof value !== 'object') return value;
    Object.keys(value).forEach(function(key) {
      value[key] = sanitizePayload(value[key], !!force || /^(?:detail|error|message|msg)$/i.test(key));
    });
    return value;
  }

  function recover(origin, reason, rawFetch) {
    if (recoveryPromise) return recoveryPromise;
    var probeUrl = String(origin || window.location.origin).replace(/\/$/, '') + '/api/health?fast=1&recovery_probe=1';
    function probe() {
      return rawFetch(probeUrl, { method: 'GET', cache: 'no-store' })
        .then(function(response) { return response.ok; })
        .catch(function() { return false; });
    }
    recoveryPromise = probe().then(function(healthy) {
      if (healthy) return true;
      var parent = parentWindow();
      try {
        if (parent && typeof parent.__lobsterRecoverRequest === 'function') {
          return parent.__lobsterRecoverRequest({ origin: origin, reason: reason || 'douyin_fetch' });
        }
      } catch (err) {}
      try {
        var api = window.pywebview && window.pywebview.api;
        if (api && typeof api.recover_local_services === 'function') {
          return Promise.resolve(api.recover_local_services(String(reason || 'douyin_fetch'))).then(function() { return true; });
        }
      } catch (innerErr) {}
      return false;
    }).then(function(healthy) {
      return healthy ? true : probe();
    }).finally(function() {
      recoveryPromise = null;
    });
    return recoveryPromise;
  }

  if (!window.__lobsterDouyinFetchPatched) {
    window.__lobsterDouyinFetchPatched = true;
    var ownFetch = window.fetch ? window.fetch.bind(window) : null;
    if (ownFetch) {
      window.fetch = function(input, init) {
        init = init || {};
        var rawUrl = typeof input === 'string' ? input : (input && input.url) || '';
        var url;
        try { url = new URL(rawUrl, window.location.href); } catch (err) { return ownFetch(input, init); }
        if (!/^\/api\//.test(url.pathname)) return ownFetch(input, init);

        var headers = new Headers((input instanceof Request ? input.headers : null) || init.headers || {});
        new Headers(init.headers || {}).forEach(function(value, key) { headers.set(key, value); });
        var latestToken = readLatestToken();
        var installationId = readInstallationId();
        if (latestToken) headers.set('Authorization', 'Bearer ' + latestToken);
        else headers.delete('Authorization');
        headers.set('X-Lobster-Brand', readBrandMark());
        if (installationId) headers.set('X-Installation-Id', installationId);
        if (!headers.has('Content-Type') && init.body) headers.set('Content-Type', 'application/json');
        var next = Object.assign({}, init, { headers: headers });

        var parent = parentWindow();
        try {
          var canDelegateRequest = typeof input === 'string' || methodOf(input, next) === 'GET' ||
            methodOf(input, next) === 'HEAD' || Object.prototype.hasOwnProperty.call(next, 'body');
          if (parent && canDelegateRequest && parent.__LOBSTER_BRAND_FETCH_INSTALLED && typeof parent.fetch === 'function') {
            return parent.fetch(typeof input === 'string' ? rawUrl : input, next);
          }
        } catch (parentErr) {}

        var method = methodOf(input, next);
        var canRetry = method === 'GET' || method === 'HEAD' || !!headers.get('X-Lobster-Request-Id');
        var attempt = 0;
        var authAttempt = 0;
        var requestTemplate = null;
        if (input instanceof Request) {
          try { requestTemplate = input.clone(); } catch (cloneError) {}
        }
        function recoverPayload(kind, value, consumer) {
          var sanitized = sanitizePayload(value, typeof value === 'string');
          if (kind === 'auth' && authAttempt < 1) {
            authAttempt += 1;
            return recover(url.origin, 'douyin_payload_auth', ownFetch).then(function() {
              return run().then(function(retried) { return retried[consumer](); });
            });
          }
          if (kind === 'network' && canRetry && attempt < RETRY_DELAYS.length - 1) {
            attempt += 1;
            return wait(RETRY_DELAYS[attempt]).then(function() {
              return recover(url.origin, 'douyin_payload', ownFetch).then(function() {
                return run().then(function(retried) { return retried[consumer](); });
              });
            });
          }
          return recover(url.origin, 'douyin_payload', ownFetch).then(function() { return sanitized; });
        }
        function protectResponse(response) {
          try {
            var originalJson = response.json.bind(response);
            Object.defineProperty(response, 'json', {
              configurable: true,
              value: function() {
                return originalJson().then(function(value) {
                  var kind = payloadFailureKind(value);
                  return kind ? recoverPayload(kind, value, 'json') : value;
                });
              }
            });
            var originalText = response.text.bind(response);
            Object.defineProperty(response, 'text', {
              configurable: true,
              value: function() {
                return originalText().then(function(value) {
                  var kind = payloadFailureKind(value);
                  return kind ? recoverPayload(kind, value, 'text') : value;
                });
              }
            });
          } catch (err) {}
          return response;
        }
        function inspectResponse(response) {
          if (!response) return Promise.resolve({ auth: false, connection: false });
          if (
            (response.status === 401 || response.status === 403) &&
            headers.get('Authorization') &&
            /bearer/i.test(String(response.headers && response.headers.get('WWW-Authenticate') || ''))
          ) {
            return Promise.resolve({ auth: true, connection: false });
          }
          if (response.status < 400) {
            return Promise.resolve({ auth: false, connection: false });
          }
          try {
            return response.clone().text().then(function(body) {
              var text = String(body || '');
              return {
                auth: (response.status === 401 || response.status === 403) && /无法验证凭证|Could not validate credentials|invalid credentials|not authenticated|invalid token|token expired/i.test(text),
                connection: /Failed to fetch|fetch(?:ing)?(?:\s+to\b.{0,160}?)?\s+failed|NetworkError|network request failed|认证中心不可达|认证中心暂时不可用/i.test(text)
              };
            }).catch(function() { return { auth: false, connection: false }; });
          } catch (err) {
            return Promise.resolve({ auth: false, connection: false });
          }
        }
        function run() {
          var usedToken = readLatestToken();
          if (usedToken) headers.set('Authorization', 'Bearer ' + usedToken);
          next.headers = headers;
          var requestInput = requestTemplate ? requestTemplate.clone() : input;
          return ownFetch(requestInput, next).then(function(response) {
            return inspectResponse(response).then(function(failure) {
              if (failure.auth) {
                if (authAttempt < 1) {
                  authAttempt += 1;
                  return recover(url.origin, 'douyin_auth', ownFetch).then(run);
                }
                return jsonResponse(LOGIN_EXPIRED_MESSAGE, 401);
              }
              if (failure.connection) {
                if (canRetry && attempt < RETRY_DELAYS.length - 1) {
                  attempt += 1;
                  return wait(RETRY_DELAYS[attempt]).then(function() {
                    return recover(url.origin, 'douyin_http_' + response.status, ownFetch).then(run);
                  });
                }
                return jsonResponse(RECOVERY_MESSAGE, 503);
              }
              return protectResponse(response);
            });
          }).catch(function(error) {
            if (!isNetworkError(error)) throw error;
            if (canRetry && attempt < RETRY_DELAYS.length - 1) {
              attempt += 1;
              return wait(RETRY_DELAYS[attempt]).then(function() {
                return recover(url.origin, 'douyin_fetch', ownFetch).then(run);
              });
            }
            return recover(url.origin, 'douyin_fetch', ownFetch).then(function() {
              return jsonResponse(RECOVERY_MESSAGE, 503);
            });
          });
        }
        return run();
      };
    }
  }

  var DOUYIN_SIDEBAR_SCROLL_KEY = 'douyin-sidebar-scroll-left-v1';
  var DOUYIN_SIDEBAR_TARGETS = {
    '/douyin': 'overview',
    '/douyin/search': 'search',
    '/douyin/monitor': 'monitor',
    '/douyin/self-comments': 'self-comments',
    '/douyin/collect': 'collect',
    '/douyin/mention': 'mention',
    '/douyin/follow': 'follow',
    '/douyin/customers': 'all-customers',
    '/douyin/intent': 'intent',
    '/douyin/interaction': 'interaction',
    '/douyin/stranger-leads': 'stranger-leads',
    '/douyin/inbox': 'inbox',
    '/douyin/scheduler': 'scheduler',
    '/douyin/nurture': 'nurture',
    '/douyin/groups': 'groups',
    '/douyin/logs': 'logs',
    '/douyin/guide': 'guide',
    '/douyin/config': 'config'
  };
  var DOUYIN_SIDEBAR_FILE_TARGETS = {
    'douyin.html': 'overview',
    'douyin-search.html': 'search',
    'douyin-monitor.html': 'monitor',
    'douyin-self-comments.html': 'self-comments',
    'douyin-collect.html': 'collect',
    'douyin-mention.html': 'mention',
    'douyin-follow.html': 'follow',
    'douyin-customer-pools.html': 'all-customers',
    'douyin-interaction.html': 'interaction',
    'douyin-stranger-leads.html': 'stranger-leads',
    'douyin-inbox.html': 'inbox',
    'douyin-scheduler.html': 'scheduler',
    'douyin-nurture.html': 'nurture',
    'douyin-groups.html': 'groups',
    'douyin-logs.html': 'logs',
    'douyin-guide.html': 'guide',
    'douyin-config.html': 'config'
  };

  function getDouyinSidebarTargetFromLocation() {
    var pathname = String(window.location.pathname || '').replace(/\/+$/, '') || '/douyin';
    var hash = String(window.location.hash || '').replace(/^#/, '').toLowerCase();
    var target = DOUYIN_SIDEBAR_TARGETS[pathname] || '';
    if (!target) {
      var filename = pathname.split('/').pop() || '';
      target = DOUYIN_SIDEBAR_FILE_TARGETS[filename] || '';
      if (filename === 'douyin.html') {
        try {
          var requested = new URLSearchParams(window.location.search || '').get('target') || '';
          if (requested) target = requested;
        } catch (err) {}
      }
    }
    if (hash === 'precise' || hash === 'intent') target = 'intent';
    if (hash === 'all' || hash === 'customers' || hash === 'all-customers') target = 'all-customers';
    return target;
  }

  function readDouyinSidebarScrollLeft() {
    try { return Math.max(0, Number(window.sessionStorage.getItem(DOUYIN_SIDEBAR_SCROLL_KEY) || 0) || 0); } catch (err) { return 0; }
  }

  function saveDouyinSidebarScrollLeft(nav) {
    if (!nav) return;
    try { window.sessionStorage.setItem(DOUYIN_SIDEBAR_SCROLL_KEY, String(Math.max(0, nav.scrollLeft || 0))); } catch (err) {}
  }

  function syncDouyinSidebarState() {
    var nav = document.querySelector('.workspace-sidebar .sidebar-accordion');
    if (!nav) return;
    var target = getDouyinSidebarTargetFromLocation();
    if (target) {
      var controls = nav.querySelectorAll('.sidebar-group-header,.sidebar-subbtn,.sidebar-utility-btn');
      for (var i = 0; i < controls.length; i += 1) controls[i].classList.remove('active');
      var groups = nav.querySelectorAll('.sidebar-group');
      for (var j = 0; j < groups.length; j += 1) groups[j].classList.remove('is-active');
      var active = nav.querySelector('[data-sidebar="' + target + '"]');
      if (active) {
        active.classList.add('active');
        var group = active.closest('.sidebar-group');
        if (group) group.classList.add('is-active');
      }
    }
    var savedScrollLeft = readDouyinSidebarScrollLeft();
    nav.scrollLeft = savedScrollLeft;
    window.requestAnimationFrame(function() {
      nav.scrollLeft = savedScrollLeft;
      var active = nav.querySelector('.sidebar-group-header.active,.sidebar-subbtn.active,.sidebar-utility-btn.active');
      if (!active) return;
      var navRect = nav.getBoundingClientRect();
      var activeRect = active.getBoundingClientRect();
      if (activeRect.left < navRect.left || activeRect.right > navRect.right) {
        active.scrollIntoView({ behavior: 'auto', block: 'nearest', inline: 'nearest' });
      }
    });
  }

  document.addEventListener('scroll', function(event) {
    var nav = event.target && event.target.closest ? event.target.closest('.workspace-sidebar .sidebar-accordion') : null;
    if (nav) saveDouyinSidebarScrollLeft(nav);
  }, true);

  document.addEventListener('click', function(event) {
    var link = event.target && event.target.closest ? event.target.closest('a[href^="/static/douyin-origin/"]') : null;
    if (link) saveDouyinSidebarScrollLeft(link.closest('.sidebar-accordion'));
  }, true);

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', syncDouyinSidebarState);
  else syncDouyinSidebarState();

  document.addEventListener('click', function(event) {
    var link = event.target && event.target.closest ? event.target.closest('a[href^="/static/douyin-origin/"]') : null;
    if (!link || link.target === '_blank') return;
    event.preventDefault();
    window.location.href = link.getAttribute('href');
  });
})();
