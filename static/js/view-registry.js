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

  window.registerLobsterView('personal-settings', {
    html: '/static/views/personal-settings.html?v=20260720-ip-profile-unified',
    scripts: '/static/js/personal-settings.js?v=20260720-ip-profile-unified',
    init: 'initPersonalSettingsView',
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

  window.registerLobsterView('ppt-studio', {
    html: '/static/views/ppt-studio.html?v=20260629-ppt-entry-page',
    scripts: '/static/js/ppt-studio.js?v=20260629-ppt-entry-page',
    init: 'initPptStudioView',
    cache: 'reload'
  });

  window.registerLobsterView('viral-tvc-studio', {
    html: '/static/views/viral-tvc-studio.html?v=20260629-viral-tvc-entry-page',
    scripts: '/static/js/viral-tvc-studio.js?v=20260629-viral-tvc-entry-page',
    init: 'initViralTvcStudioView',
    cache: 'reload'
  });

  window.registerLobsterView('ip-content-studio', {
    html: '/static/views/ip-content-studio.html?v=20260720-current-profile-only',
    scripts: '/static/js/ip-content-studio.js?v=20260720-current-profile-only',
    init: 'initIpContentStudioView',
    cache: 'reload'
  });

  window.registerLobsterView('linkedin-mining', {
    html: '/static/views/linkedin-mining.html?v=20260616-linkedin-workbench',
    scripts: '/static/js/linkedin-mining.js?v=20260616-linkedin-workbench',
    init: 'initLinkedinMiningView',
    cache: 'reload'
  });

  window.registerLobsterView('social-leads', {
    html: '/static/views/social-leads.html?v=20260630-social-leads-platform-isolation',
    scripts: '/static/js/social-leads.js?v=20260630-social-leads-platform-isolation',
    init: 'initSocialLeadsView',
    cache: 'reload'
  });

  window.registerLobsterView('global-leads', {
    html: '/static/views/global-leads.html?v=20260714-global-leads-web-search',
    scripts: '/static/js/global-leads.js?v=20260714-global-leads-web-search',
    init: 'initGlobalLeadsView',
    cache: 'reload'
  });

  window.registerLobsterView('juhe-wechat', {
    html: '/static/views/juhe-wechat.html?v=20260715-native-wechat-moments-comment',
    scripts: '/static/js/juhe-wechat.js?v=20260718-native-wechat-moments-publish',
    init: 'initJuheWechatView',
    cache: 'reload'
  });

  window.registerLobsterView('wechat-channels-transcript', {
    html: '/static/views/wechat-channels-transcript.html?v=20260626-wct-entry-cache',
    scripts: '/static/js/wechat-channels-transcript.js?v=20260626-wct-entry-cache',
    init: 'initWechatChannelsTranscriptView',
    cache: 'reload'
  });

  window.registerLobsterView('ai-3d-model', {
    html: '/static/views/ai-3d-model.html?v=20260709-component-split-v1',
    scripts: '/static/js/ai-3d-model.js?v=20260709-component-split-v1',
    init: 'initAi3dModelView',
    cache: 'reload',
    reloadExisting: true
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
    scripts: '/static/js/views/agent.js?v=20260622-agent-execution-screen'
  });
})();
