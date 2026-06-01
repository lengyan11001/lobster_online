(function registerOnlineViews() {
  if (typeof window.registerLobsterView !== 'function') return;

  window.registerLobsterView('logs', {
    html: '/static/views/logs.html',
    scripts: '/static/js/views/logs.js?v=20260528-view-split'
  });

  window.registerLobsterView('skill-store', {
    html: '/static/views/skill-store.html'
  });

  window.registerLobsterView('production', {
    html: '/static/views/production.html',
    scripts: '/static/js/views/production.js?v=20260528-view-split'
  });

  window.registerLobsterView('publish', {
    html: '/static/views/publish.html'
  });

  window.registerLobsterView('assets', {
    html: '/static/views/assets.html?v=20260601-asset-doc-card',
    cache: 'reload'
  });

  window.registerLobsterView('scheduled-tasks', {
    html: '/static/views/scheduled-tasks.html'
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
