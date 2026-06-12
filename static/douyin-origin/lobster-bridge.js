(function installLobsterDouyinBridge() {
  function readStorageValue(key) {
    try {
      if (window.parent && window.parent.localStorage) {
        var parentValue = window.parent.localStorage.getItem(key) || '';
        if (parentValue) return parentValue;
      }
    } catch (err) {}
    try {
      return window.localStorage.getItem(key) || '';
    } catch (innerErr) {
      return '';
    }
  }

  function readLatestToken() {
    return String(readStorageValue('token') || '').trim();
  }

  function readInstallationId() {
    var id = String(readStorageValue('installation_id') || '').trim();
    if (!id && typeof window.getOrCreateInstallationId === 'function') {
      try {
        id = String(window.getOrCreateInstallationId() || '').trim();
      } catch (err) {}
    }
    return id;
  }

  if (!window.__lobsterDouyinFetchPatched) {
    window.__lobsterDouyinFetchPatched = true;
    var rawFetch = window.fetch ? window.fetch.bind(window) : null;
    if (rawFetch) {
      window.fetch = function(input, init) {
        init = init || {};
        var url = typeof input === 'string' ? input : (input && input.url) || '';
        if (/^\/api\//.test(url)) {
          var headers = new Headers(init.headers || (input && input.headers) || {});
          var token = readLatestToken();
          var installationId = readInstallationId();
          if (token && !headers.has('Authorization')) headers.set('Authorization', 'Bearer ' + token);
          if (installationId && !headers.has('X-Installation-Id')) headers.set('X-Installation-Id', installationId);
          if (!headers.has('Content-Type') && init.body) headers.set('Content-Type', 'application/json');
          init = Object.assign({}, init, { headers: headers });
        }
        return rawFetch(input, init);
      };
    }
  }

  document.addEventListener('click', function(event) {
    var link = event.target && event.target.closest ? event.target.closest('a[href^="/static/douyin-origin/"]') : null;
    if (!link) return;
    if (link.target === '_blank') return;
    event.preventDefault();
    window.location.href = link.getAttribute('href');
  });
})();
