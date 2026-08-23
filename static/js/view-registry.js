(function registerOnlineViews() {
  if (typeof window.registerLobsterView !== 'function') return;

  window.registerLobsterView('audio-transcription', {
    html: '/static/views/audio-transcription.html?v=20260810-ai-secretary-v2',
    css: '/static/css/audio-transcription.css?v=20260810-ai-secretary-v2',
    scripts: '/static/js/audio-transcription.js?v=20260810-ai-secretary-v2',
    init: 'initAudioTranscriptionView'
  });

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
    html: '/static/views/personal-settings.html?v=20260814-agent-memory-v1',
    scripts: '/static/js/personal-settings.js?v=20260814-agent-memory-v1',
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
    scripts: '/static/js/wechat-article.js?v=20260814-wechat-image-style',
    init: 'loadWechatArticlePage',
    cache: 'reload'
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
    html: '/static/views/ip-content-studio.html?v=20260807-moment-failure-scope',
    scripts: '/static/js/ip-content-studio.js?v=20260807-moment-failure-scope-20260807-content-publish-v1',
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

  window.registerLobsterView('alibaba-inquiries', {
    html: '/static/views/alibaba-inquiries.html?v=20260721-alibaba-doc-collapse',
    scripts: '/static/js/alibaba-inquiries.js?v=20260722-alibaba-archive-jobs',
    init: 'initAlibabaInquiriesView',
    cache: 'reload'
  });

  window.registerLobsterView('juhe-wechat', {
    html: '/static/views/juhe-wechat.html?v=20260808-native-wechat-pagination-v1',
    scripts: '/static/js/juhe-wechat.js?v=20260808-native-wechat-pagination-v1',
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
    html: '/static/views/ai-3d-model.html?v=20260803-component-split-v3',
    scripts: '/static/js/ai-3d-model.js?v=20260803-component-split-v3',
    init: 'initAi3dModelView',
    cache: 'reload',
    reloadExisting: true
  });

  window.registerLobsterView('assets', {
    html: '/static/views/assets.html?v=20260806-content-record-categories',
    scripts: '/static/js/publish.js?v=20260807-digital-human-v2-20260807-content-publish-v1'
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
    html: '/static/views/sys-config.html?v=20260818-device-alias-v1',
    scripts: '/static/js/views/sysconfig.js?v=20260818-device-alias-v1'
  });

  window.registerLobsterView('agent', {
    html: '/static/views/agent.html',
    scripts: '/static/js/views/agent.js?v=20260622-agent-execution-screen'
  });

  window.registerLobsterView('tutorial', {
    html: '/static/views/tutorial.html?v=20260803-global-navigation'
  });

  window.registerLobsterView('h5-employees', {
    html: '/static/views/h5-employees.html?v=20260823-douyin-online-keywords-v1',
    scripts: '/static/js/views/h5-employees.js?v=20260823-douyin-online-keywords-v1',
    init: 'initOnlineH5EmployeesView',
    cache: 'reload'
  });

  window.registerLobsterView('multi-clip-mixer', {
    html: '/static/views/multi-clip-mixer.html?v=20260823-template-copy-v4',
    css: '/static/css/multi-clip-mixer.css?v=20260814-v2',
    scripts: '/static/js/multi-clip-mixer.js?v=20260823-template-copy-v4',
    init: 'initMultiClipMixerView',
    cache: 'reload'
  });
})();
