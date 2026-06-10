(function registerOnlineViews() {
  if (typeof window.registerLobsterView !== 'function') return;

  window.registerLobsterView('logs', {
    html: '/static/views/logs.html',
    scripts: '/static/js/views/logs.js?v=20260528-view-split'
  });

  window.registerLobsterView('skill-store', {
    html: '/static/views/skill-store.html'
  });

  window.registerLobsterView('douyin-leads', {
    html: '/static/views/douyin-leads.html?v=20260606-douyin-origin'
  });

  window.registerLobsterView('openclaw-memory', {
    html: '/static/views/openclaw-memory.html?v=20260601-memory-manager',
    cache: 'reload'
  });

  window.registerLobsterView('production', {
    html: '/static/views/production.html',
    scripts: '/static/js/views/production.js?v=20260528-view-split'
  });

  window.registerLobsterView('publish', {
    html: '/static/views/publish.html'
  });

  window.registerLobsterView('wechat-article', {
    html: '/static/views/wechat-article.html',
    scripts: '/static/js/wechat-article.js?v=20260605-wechat-save-local-doubleclick'
  });

  window.registerLobsterView('creative-film-studio', {
    html: '/static/views/creative-film-studio.html?v=20260606-creative-film-entry',
    scripts: '/static/js/creative-film-studio.js?v=20260606-creative-film-entry',
    cache: 'reload'
  });

  window.registerLobsterView('ip-content-studio', {
    html: '/static/views/ip-content-studio.html?v=20260608-ip-content-flow',
    scripts: '/static/js/ip-content-studio.js?v=20260608-ip-content-flow',
    cache: 'reload'
  });

  window.registerLobsterView('assets', {
    html: '/static/views/assets.html?v=20260610-asset-origin-filter-fix',
    scripts: '/static/js/publish.js?v=20260610-asset-origin-filter-fix',
    cache: 'reload'
  });

  window.registerLobsterView('scheduled-tasks', {
    html: '/static/views/scheduled-tasks.html?v=20260610-scheduled-form-only',
    cache: 'reload'
  });

  window.registerLobsterView('billing', {
    html: '/static/views/billing.html',
    scripts: '/static/js/views/billing.js?v=20260528-view-split'
  });

  window.registerLobsterView('sys-config', {
    html: '/static/views/sys-config.html',
    scripts: '/static/js/views/sysconfig.js?v=20260528-view-split'
  });

  window.registerLobsterView('agent', {
    html: '/static/views/agent.html',
    scripts: '/static/js/views/agent.js?v=20260528-view-split'
  });
})();
