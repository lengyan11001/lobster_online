(function installLobsterDouyinBridge() {
  var token = '';
  try {
    token = (window.parent && window.parent.localStorage ? window.parent.localStorage.getItem('token') : '') ||
      window.localStorage.getItem('token') ||
      '';
  } catch (err) {
    try { token = window.localStorage.getItem('token') || ''; } catch (innerErr) {}
  }

  if (token && !window.__lobsterDouyinFetchPatched) {
    window.__lobsterDouyinFetchPatched = true;
    var rawFetch = window.fetch ? window.fetch.bind(window) : null;
    if (rawFetch) {
      window.fetch = function(input, init) {
        init = init || {};
        var url = typeof input === 'string' ? input : (input && input.url) || '';
        if (/^\/api\//.test(url)) {
          var headers = new Headers(init.headers || (input && input.headers) || {});
          if (!headers.has('Authorization')) headers.set('Authorization', 'Bearer ' + token);
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
