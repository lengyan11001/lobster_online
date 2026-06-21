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
    html: '/static/views/douyin-leads.html?v=20260611-search-scroll-final',
    cache: 'reload'
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
    html: '/static/views/ip-content-studio.html?v=20260615-ip-content-batch',
    scripts: '/static/js/ip-content-studio.js?v=20260615-ip-content-batch',
    init: 'initIpContentStudioView',
    cache: 'reload'
  });

  window.registerLobsterView('linkedin-mining', {
    html: '/static/views/linkedin-mining.html?v=20260616-linkedin-workbench',
    scripts: '/static/js/linkedin-mining.js?v=20260616-linkedin-workbench',
    init: 'initLinkedinMiningView',
    cache: 'reload'
  });

  window.registerLobsterView('juhe-wechat', {
    html: '/static/views/juhe-wechat.html?v=20260618-user-actions-only',
    scripts: '/static/js/juhe-wechat.js?v=20260618-user-actions-only',
    init: 'initJuheWechatView',
    cache: 'reload'
  });

  window.registerLobsterView('wechat-channels-transcript', {
    html: '/static/views/wechat-channels-transcript.html?v=20260621-wct',
    scripts: '/static/js/wechat-channels-transcript.js?v=20260621-wct',
    init: 'initWechatChannelsTranscriptView',
    cache: 'reload'
  });

  window.registerLobsterView('assets', {
    html: '/static/views/assets.html?v=20260611-asset-cache-incremental',
    scripts: '/static/js/publish.js?v=20260611-asset-preview-cache-incremental'
  });

  window.registerLobsterView('scheduled-tasks', {
    html: '/static/views/scheduled-tasks.html?v=20260615-ip-daily-task-options',
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
