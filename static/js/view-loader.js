(function initLobsterViewLoader() {
  if (window.__lobsterViewLoaderReady) return;
  window.__lobsterViewLoaderReady = true;

  var registry = window.__LOBSTER_VIEW_REGISTRY = window.__LOBSTER_VIEW_REGISTRY || {};
  var state = window.__LOBSTER_VIEW_STATE = window.__LOBSTER_VIEW_STATE || {};
  state.loaded = state.loaded || {};
  state.loading = state.loading || {};
  state.styles = state.styles || {};
  state.scripts = state.scripts || {};
  state.inited = state.inited || {};

  function normalizeViewName(view) {
    return String(view || '').trim();
  }

  function viewContentId(view) {
    return 'content-' + normalizeViewName(view);
  }

  function toAbsoluteUrl(url) {
    var a = document.createElement('a');
    a.href = url;
    return a.href;
  }

  function getViewMount(config) {
    if (config && config.mount) {
      var configured = document.querySelector(config.mount);
      if (configured) return configured;
    }
    return document.querySelector('.dashboard-main') || document.getElementById('dashboard') || document.body;
  }

  function findLoadedView(view) {
    return document.getElementById(viewContentId(view));
  }

  function asArray(value) {
    if (!value) return [];
    return Array.isArray(value) ? value : [value];
  }

  function cacheBustUrl(url) {
    if (!url || !/^\/static\//.test(String(url))) return url;
    var key = window.__LOBSTER_STATIC_CACHE_BUST;
    if (!key) {
      key = Date.now().toString(36);
      window.__LOBSTER_STATIC_CACHE_BUST = key;
    }
    var sep = String(url).indexOf('?') >= 0 ? '&' : '?';
    return String(url) + sep + '_lobster_v=' + encodeURIComponent(key);
  }

  function loadStyleOnce(href) {
    if (!href) return Promise.resolve();
    href = cacheBustUrl(href);
    if (state.styles[href]) return state.styles[href];
    var absoluteHref = toAbsoluteUrl(href);
    var links = document.getElementsByTagName('link');
    for (var i = 0; i < links.length; i += 1) {
      if (links[i].getAttribute('href') === href || links[i].href === absoluteHref) {
        state.styles[href] = Promise.resolve();
        return state.styles[href];
      }
    }
    state.styles[href] = new Promise(function(resolve, reject) {
      var link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = href;
      link.onload = resolve;
      link.onerror = function() { reject(new Error('Failed to load stylesheet: ' + href)); };
      document.head.appendChild(link);
    });
    return state.styles[href];
  }

  function loadScriptOnce(src) {
    if (!src) return Promise.resolve();
    src = cacheBustUrl(src);
    if (state.scripts[src]) return state.scripts[src];
    var absoluteSrc = toAbsoluteUrl(src);
    var scripts = document.getElementsByTagName('script');
    for (var i = 0; i < scripts.length; i += 1) {
      if (scripts[i].getAttribute('src') === src || scripts[i].src === absoluteSrc) {
        state.scripts[src] = Promise.resolve();
        return state.scripts[src];
      }
    }
    state.scripts[src] = new Promise(function(resolve, reject) {
      var script = document.createElement('script');
      script.src = src;
      script.async = false;
      script.onload = resolve;
      script.onerror = function() { reject(new Error('Failed to load script: ' + src)); };
      document.body.appendChild(script);
    });
    return state.scripts[src];
  }

  function appendViewHtml(view, html, config) {
    var existing = findLoadedView(view);
    if (existing) return existing;

    var id = viewContentId(view);
    var template = document.createElement('template');
    template.innerHTML = html || '';
    var embeddedHost = template.content.querySelector('#' + id);
    var mount = getViewMount(config);

    if (embeddedHost) {
      mount.appendChild(template.content);
      var appended = document.getElementById(id);
      if (appended && !appended.classList.contains('content-block')) appended.classList.add('content-block');
      return appended;
    }

    var host = document.createElement('div');
    host.id = id;
    host.className = 'content-block';
    host.appendChild(template.content);
    mount.appendChild(host);
    return host;
  }

  function createViewError(view, message) {
    var host = findLoadedView(view);
    if (!host) {
      host = document.createElement('div');
      host.id = viewContentId(view);
      host.className = 'content-block';
      getViewMount({}).appendChild(host);
    }
    host.innerHTML = '<div class="card"><p class="msg err" style="display:block;margin:0;">视图加载失败：' +
      String(message || 'unknown error').replace(/[&<>"']/g, function(ch) {
        return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
      }) + '</p></div>';
    return host;
  }

  function ensureLobsterViewLoaded(view) {
    view = normalizeViewName(view);
    if (!view) return Promise.resolve(null);

    var existing = findLoadedView(view);
    if (existing) {
      state.loaded[view] = true;
      return Promise.resolve(existing);
    }

    if (state.loading[view]) return state.loading[view];

    var config = registry[view] || {};
    var htmlUrl = cacheBustUrl(config.html || config.url);
    if (!htmlUrl) return Promise.resolve(null);

    state.loading[view] = Promise.all(asArray(config.css).map(loadStyleOnce))
      .then(function() {
        return fetch(htmlUrl, {
          credentials: 'same-origin',
          cache: config.cache || 'default'
        });
      })
      .then(function(resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status + ' loading view: ' + htmlUrl);
        return resp.text();
      })
      .then(function(html) {
        var el = appendViewHtml(view, html, config);
        return Promise.all(asArray(config.scripts).map(loadScriptOnce)).then(function() {
          state.loaded[view] = true;
          delete state.loading[view];
          return el;
        });
      })
      .catch(function(err) {
        delete state.loading[view];
        throw err;
      });

    return state.loading[view];
  }

  function runRegisteredLobsterViewInit(view) {
    view = normalizeViewName(view);
    var config = registry[view] || {};
    var init = config.init;
    if (!init) return Promise.resolve();
    if (config.initOnce && state.inited[view]) return Promise.resolve();

    var fn = null;
    if (typeof init === 'function') fn = init;
    if (!fn && typeof init === 'string' && typeof window[init] === 'function') fn = window[init];
    if (!fn) return Promise.resolve();

    return Promise.resolve(fn(findLoadedView(view), config)).then(function(result) {
      if (config.initOnce) state.inited[view] = true;
      return result;
    });
  }

  window.registerLobsterView = function(view, config) {
    view = normalizeViewName(view);
    if (!view) return null;
    if (typeof config === 'string') config = { html: config };
    registry[view] = Object.assign({}, registry[view] || {}, config || {});
    return registry[view];
  };

  window.ensureLobsterViewLoaded = ensureLobsterViewLoaded;
  window.runRegisteredLobsterViewInit = runRegisteredLobsterViewInit;
  window.createLobsterViewError = createViewError;
})();
