var ocConfigLoaded = false;
var ocProviderData = [];
var _currentSysTab = 'model';
var _chatRouteModeSavedValue = null;
var _chatRouteModeMsgTimer = null;

function clearChatRouteModeMsg() {
  var msgEl = document.getElementById('chatRouteModeMsg');
  if (_chatRouteModeMsgTimer) {
    clearTimeout(_chatRouteModeMsgTimer);
    _chatRouteModeMsgTimer = null;
  }
  if (msgEl) {
    msgEl.textContent = '';
    msgEl.className = 'msg';
    msgEl.style.display = 'none';
  }
}

function showChatRouteModeMsg(text, isErr, autoHide) {
  var msgEl = document.getElementById('chatRouteModeMsg');
  if (_chatRouteModeMsgTimer) {
    clearTimeout(_chatRouteModeMsgTimer);
    _chatRouteModeMsgTimer = null;
  }
  if (typeof showMsg === 'function') {
    showMsg(msgEl, text, isErr);
  } else if (msgEl) {
    msgEl.textContent = text || '';
    msgEl.className = 'msg ' + (isErr ? 'err' : 'ok');
    msgEl.style.display = text ? 'inline-block' : 'none';
  }
  if (autoHide) {
    _chatRouteModeMsgTimer = setTimeout(function() {
      clearChatRouteModeMsg();
    }, 2500);
  }
}

document.querySelectorAll('.sys-tab').forEach(function(tab) {
  tab.addEventListener('click', function() {
    var target = tab.getAttribute('data-sys-tab');
    if (!target || target === _currentSysTab) return;
    _currentSysTab = target;
    document.querySelectorAll('.sys-tab').forEach(function(t) { t.classList.remove('active'); });
    tab.classList.add('active');
    document.getElementById('sysTabModel').style.display = (target === 'model') ? '' : 'none';
    document.getElementById('sysTabCustom').style.display = (target === 'custom') ? '' : 'none';
    if (target === 'custom') loadCustomConfigs();
  });
});

function loadLanInfo() {
  return;
}

function showAssetPathMsg(text, isErr) {
  var msgEl = document.getElementById('assetPathMsg');
  if (typeof showMsg === 'function') {
    showMsg(msgEl, text, !!isErr);
  } else if (msgEl) {
    msgEl.textContent = text || '';
    msgEl.className = 'msg ' + (isErr ? 'err' : 'ok');
    msgEl.style.display = text ? 'inline-block' : 'none';
  }
}

function showInstallationSlotMsg(text, isErr) {
  var msgEl = document.getElementById('installationSlotMsg');
  if (typeof showMsg === 'function') {
    showMsg(msgEl, text, !!isErr);
  } else if (msgEl) {
    msgEl.textContent = text || '';
    msgEl.className = 'msg ' + (isErr ? 'err' : 'ok');
    msgEl.style.display = text ? 'inline-block' : 'none';
  }
  if (msgEl && text) msgEl.style.display = 'inline-block';
}

function renderInstallationSlotId(status) {
  var idEl = document.getElementById('installationSlotIdText');
  var hintEl = document.getElementById('installationSlotHint');
  var iid = typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : '';
  var machineId = typeof getCachedLobsterMachineInstanceId === 'function' ? getCachedLobsterMachineInstanceId() : '';
  if (idEl) {
    idEl.textContent = iid || '-';
    idEl.title = machineId ? (iid + '\nMachine: ' + machineId) : (iid || '');
  }
  if (hintEl) {
    if (status && status.duplicate) {
      hintEl.textContent = '服务器检测到这个槽位还被其他账号/设备记录使用，建议点击“随机更换”。';
      hintEl.style.color = 'var(--danger, #dc2626)';
    } else if (status && status.ok) {
      hintEl.textContent = '服务器已确认当前槽位可用于当前账号。';
      hintEl.style.color = 'var(--success, #16a34a)';
    } else {
      hintEl.textContent = '首次生成和随机更换都会向服务器确认唯一性。';
      hintEl.style.color = '';
    }
  }
}

function installationSlotCloudBase() {
  return String((typeof API_BASE !== 'undefined' && API_BASE) || window.__API_BASE || '').replace(/\/$/, '');
}

function loadInstallationSlotAlias() {
  var input = document.getElementById('installationSlotAliasInput');
  if (!input) return Promise.resolve(null);
  var iid = typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : '';
  var base = installationSlotCloudBase();
  if (!iid || !base) {
    input.value = '';
    return Promise.resolve(null);
  }
  input.disabled = true;
  return fetch(base + '/api/h5-chat/devices/status', {
    method: 'GET',
    headers: authHeaders()
  }).then(function(response) {
    return response.json().catch(function() { return {}; }).then(function(data) {
      if (!response.ok) throw new Error(data.detail || data.message || '系统别名读取失败');
      var devices = Array.isArray(data.devices) ? data.devices : [];
      var current = devices.find(function(device) {
        return String(device && device.installation_id || '').trim() === iid;
      });
      input.value = String(current && current.display_name || '').trim();
      return current || null;
    });
  }).finally(function() {
    input.disabled = false;
  });
}

function saveInstallationSlotAlias() {
  var input = document.getElementById('installationSlotAliasInput');
  var btn = document.getElementById('saveInstallationSlotAliasBtn');
  var iid = typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : '';
  var base = installationSlotCloudBase();
  if (!input || !iid || !base) {
    showInstallationSlotMsg('当前槽位或服务器地址不可用', true);
    return;
  }
  var alias = String(input.value || '').trim().slice(0, 128);
  var oldText = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '保存中...';
  }
  input.disabled = true;
  fetch(base + '/api/h5-chat/devices/' + encodeURIComponent(iid) + '/display-name', {
    method: 'PATCH',
    headers: authHeaders(),
    body: JSON.stringify({ display_name: alias })
  }).then(function(response) {
    return response.json().catch(function() { return {}; }).then(function(data) {
      if (!response.ok) throw new Error(data.detail || data.message || '系统别名保存失败');
      var saved = String(data.device && data.device.display_name || '').trim();
      input.value = saved;
      showInstallationSlotMsg(saved ? '系统别名已保存，H5 设备列表将优先显示该名称' : '系统别名已清除', false);
    });
  }).catch(function(err) {
    showInstallationSlotMsg((err && err.message) || '系统别名保存失败', true);
  }).finally(function() {
    input.disabled = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = oldText || '保存系统别名';
    }
  });
}

function syncLocalInstallationSlotId(iid) {
  if (!iid || !LOCAL_API_BASE) return Promise.resolve({ ok: false, skipped: true });
  return fetch((LOCAL_API_BASE || '') + '/api/settings/installation-id/sync', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ installation_id: iid })
  }).then(function(r) {
    return r.json().catch(function() { return {}; }).then(function(d) {
      if (!r.ok) throw new Error((d && d.detail) || '本机槽位同步失败');
      return d;
    });
  });
}

function loadInstallationSlotStatus() {
  if (!document.getElementById('installationSlotBlock')) return;
  renderInstallationSlotId();
  var machinePromise = typeof loadLobsterMachineInstanceId === 'function'
    ? loadLobsterMachineInstanceId()
    : Promise.resolve('');
  machinePromise.then(function() {
    if (typeof loadLobsterInstallationIdStatus !== 'function') return null;
    return loadLobsterInstallationIdStatus();
  })
    .then(function(status) {
      renderInstallationSlotId(status || {});
      return loadInstallationSlotAlias().then(function() {
        showInstallationSlotMsg('', false);
        return status;
      });
    })
    .catch(function(err) {
      renderInstallationSlotId();
      showInstallationSlotMsg((err && err.message) || '槽位状态检查失败', true);
    });
}

function randomizeInstallationSlotId() {
  var btn = document.getElementById('randomizeInstallationSlotIdBtn');
  var copyBtn = document.getElementById('copyInstallationSlotIdBtn');
  var oldText = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '生成中...';
  }
  if (copyBtn) copyBtn.disabled = true;
  showInstallationSlotMsg('正在向服务器申请唯一槽位...', false);
  var previous = typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : '';
  var nextDeviceSeed = typeof randomizeLobsterDeviceSeed === 'function'
    ? randomizeLobsterDeviceSeed({ reason: 'manual_randomize' })
    : (typeof generateLobsterInstallationId === 'function' ? generateLobsterInstallationId() : String(Date.now()));
  if (typeof setLobsterDeviceSeed === 'function') {
    try { setLobsterDeviceSeed(nextDeviceSeed, { reason: 'manual_randomize' }); } catch (e) {}
  } else {
    try { localStorage.setItem('lobster_device_seed', nextDeviceSeed); } catch (e2) {}
  }
  var bindPromise;
  var machinePromise = typeof loadLobsterMachineInstanceId === 'function'
    ? loadLobsterMachineInstanceId()
    : Promise.resolve(typeof getCachedLobsterMachineInstanceId === 'function' ? getCachedLobsterMachineInstanceId() : '');
  if (typeof bindLobsterInstallationId === 'function') {
    bindPromise = machinePromise.then(function(machineInstanceId) {
      return bindLobsterInstallationId({
        installationId: previous,
        deviceId: nextDeviceSeed,
        machineInstanceId: machineInstanceId || '',
        forceNew: true
      });
    });
  } else if (typeof requestUniqueLobsterInstallationId === 'function') {
    bindPromise = requestUniqueLobsterInstallationId({ forceNew: true, candidate: previous });
  } else {
    bindPromise = Promise.resolve({ installation_id: (typeof generateLobsterInstallationId === 'function' ? generateLobsterInstallationId() : String(Date.now())) });
  }
  bindPromise
    .then(function(data) {
      var next = String(data && data.installation_id || '').trim();
      if (!next) throw new Error('服务器未返回新槽位');
      if (typeof setLobsterInstallationId === 'function') setLobsterInstallationId(next, { reason: 'manual_randomize' });
      else localStorage.setItem('lobster_installation_id', next);
      renderInstallationSlotId({ ok: true, duplicate: !!(data && data.duplicate) });
      return syncLocalInstallationSlotId(next).catch(function(err) {
        showInstallationSlotMsg('新槽位已保存，但本机缓存同步失败：' + ((err && err.message) || err), true);
        return null;
      }).then(function() {
        if (!(data && data.duplicate)) showInstallationSlotMsg('新槽位已生成并绑定当前账号，后续任务会使用这个 ID。', false);
      });
    })
    .catch(function(err) {
      showInstallationSlotMsg((err && err.message) || '随机更换失败', true);
    })
    .finally(function() {
      if (btn) {
        btn.disabled = false;
        btn.textContent = oldText || '随机更换';
      }
      if (copyBtn) copyBtn.disabled = false;
    });
}

function renderAssetPathSettings(data) {
  var internalEl = document.getElementById('assetInternalPathText');
  var input = document.getElementById('assetExportPathInput');
  var defaultEl = document.getElementById('assetDefaultPathText');
  if (internalEl) {
    internalEl.textContent = (data && data.internal_assets_dir) || '-';
    internalEl.title = internalEl.textContent;
  }
  if (input) {
    input.value = (data && data.export_dir) || '';
    input.title = input.value;
  }
  if (defaultEl) {
    defaultEl.textContent = '默认下载目录：' + ((data && data.default_export_dir) || '-');
  }
}

function loadAssetPathSettings() {
  if (!document.getElementById('assetPathSettingsBlock')) return;
  fetch((LOCAL_API_BASE || '') + '/api/settings/asset-paths', { headers: authHeaders() })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(x) {
      if (!x.ok) throw new Error((x.data && x.data.detail) || '加载失败');
      renderAssetPathSettings(x.data || {});
      showAssetPathMsg('', false);
    })
    .catch(function(err) {
      showAssetPathMsg((err && err.message) || '素材路径加载失败', true);
    });
}

function saveAssetPathSettings(useDefault) {
  var input = document.getElementById('assetExportPathInput');
  var btn = document.getElementById('saveAssetPathBtn');
  var resetBtn = document.getElementById('resetAssetPathBtn');
  var exportDir = useDefault ? '' : ((input && input.value) || '').trim();
  if (btn) btn.disabled = true;
  if (resetBtn) resetBtn.disabled = true;
  showAssetPathMsg('正在保存...', false);
  fetch((LOCAL_API_BASE || '') + '/api/settings/asset-paths', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ export_dir: exportDir })
  })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(x) {
      if (!x.ok) throw new Error((x.data && x.data.detail) || '保存失败');
      renderAssetPathSettings(x.data || {});
      showAssetPathMsg('素材下载路径已保存', false);
    })
    .catch(function(err) {
      showAssetPathMsg((err && err.message) || '保存失败', true);
    })
    .finally(function() {
      if (btn) btn.disabled = false;
      if (resetBtn) resetBtn.disabled = false;
    });
}

function setChatRouteModeValue(mode) {
  var normalized = (mode === 'openclaw') ? 'openclaw' : 'direct';
  document.querySelectorAll('input[name="chatRouteMode"]').forEach(function(radio) {
    radio.checked = (radio.value === normalized);
  });
}

function getChatRouteModeValue() {
  var checked = document.querySelector('input[name="chatRouteMode"]:checked');
  return checked && checked.value === 'openclaw' ? 'openclaw' : 'direct';
}

function loadChatRouteMode() {
  if (!document.getElementById('chatRouteModeBlock')) return;
  fetch((LOCAL_API_BASE || '') + '/api/settings/chat-route', { headers: authHeaders() })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(x) {
      if (x.ok && x.data) {
        setChatRouteModeValue(x.data.mode);
        _chatRouteModeSavedValue = getChatRouteModeValue();
      } else {
        setChatRouteModeValue('direct');
        _chatRouteModeSavedValue = 'direct';
      }
      clearChatRouteModeMsg();
    })
    .catch(function() {
      setChatRouteModeValue('direct');
      _chatRouteModeSavedValue = 'direct';
      clearChatRouteModeMsg();
    });
}

function saveChatRouteMode() {
  var btn = document.getElementById('saveChatRouteModeBtn');
  var mode = getChatRouteModeValue();
  if (_chatRouteModeSavedValue !== null && _chatRouteModeSavedValue === mode) {
    showChatRouteModeMsg('当前已是这个路由', false, true);
    return;
  }
  if (btn) btn.disabled = true;
  showChatRouteModeMsg('正在保存…', false, false);
  fetch((LOCAL_API_BASE || '') + '/api/settings/chat-route', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ mode: mode })
  })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(x) {
      if (x.ok && x.data) {
        setChatRouteModeValue(x.data.mode);
        _chatRouteModeSavedValue = getChatRouteModeValue();
        showChatRouteModeMsg('已保存，新的智能对话立即生效', false, true);
      } else {
        showChatRouteModeMsg((x.data && x.data.detail) || '保存失败', true, false);
      }
    })
    .catch(function() { showChatRouteModeMsg('网络错误', true, false); })
    .finally(function() { if (btn) btn.disabled = false; });
}

function saveAccountPassword() {
  var pwdEl = document.getElementById('accountPasswordInput');
  var confirmEl = document.getElementById('accountPasswordConfirmInput');
  var btn = document.getElementById('saveAccountPasswordBtn');
  var msgEl = document.getElementById('accountPasswordMsg');
  if (!pwdEl || !confirmEl) return;
  var password = pwdEl.value || '';
  var confirm = confirmEl.value || '';
  if (password.length < 6) {
    showMsg(msgEl, '密码至少 6 位', true);
    return;
  }
  if (password.length > 128) {
    showMsg(msgEl, '密码不能超过 128 位', true);
    return;
  }
  if (password !== confirm) {
    showMsg(msgEl, '两次输入的密码不一致', true);
    return;
  }
  if (btn) btn.disabled = true;
  showMsg(msgEl, '正在保存...', false);
  fetch(API_BASE + '/auth/set-password', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ password: password })
  })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(x) {
      if (x.ok && x.data && x.data.ok) {
        pwdEl.value = '';
        confirmEl.value = '';
        showMsg(msgEl, '登录密码已保存', false);
      } else {
        var detail = x.data && (x.data.detail || x.data.message);
        showMsg(msgEl, typeof detail === 'string' ? detail : '保存失败', true);
      }
    })
    .catch(function() { showMsg(msgEl, '网络错误', true); })
    .finally(function() { if (btn) btn.disabled = false; });
}

function loadSutuiConfig() {
  var input = document.getElementById('sutuiTokenInput');
  if (!input) return;
  fetch((LOCAL_API_BASE || '') + '/api/sutui/config', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      input.value = '';
      input.placeholder = (d.has_token ? '已配置 (' + (d.token || 'sk-***') + ')，输入新值可覆盖' : '输入速推/xSkill Token (sk-...)');
    })
    .catch(function() {
      input.placeholder = '输入速推/xSkill Token (sk-...)';
    });
}

function saveSutuiToken() {
  var input = document.getElementById('sutuiTokenInput');
  var btn = document.getElementById('saveSutuiTokenBtn');
  var msgEl = document.getElementById('sutuiTokenMsg');
  if (!input) return;
  var token = (input.value || '').trim();
  if (!token) {
    showMsg(msgEl, '请输入 Token', true);
    return;
  }
  if (btn) btn.disabled = true;
  fetch((LOCAL_API_BASE || '') + '/api/sutui/config', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ token: token })
  })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(x) {
      if (x.ok) {
        showMsg(msgEl, 'Token 已保存', false);
        input.value = '';
        loadSutuiConfig();
      } else {
        showMsg(msgEl, (x.data && x.data.detail) || '保存失败', true);
      }
    })
    .catch(function() { showMsg(msgEl, '网络错误', true); })
    .finally(function() { if (btn) btn.disabled = false; });
}

function loadOpenClawConfig() {
  var modelTab = document.querySelector('.sys-tab[data-sys-tab="model"]');
  var modelPanel = document.getElementById('sysTabModel');
  var allowModel = true; // 单机版始终允许配置模型与各 key；在线版由 ALLOW_SELF_CONFIG_MODEL 决定
  if (EDITION === 'online') {
    allowModel = typeof ALLOW_SELF_CONFIG_MODEL !== 'undefined' ? ALLOW_SELF_CONFIG_MODEL : true;
    if (modelTab) modelTab.style.display = allowModel ? '' : 'none';
    if (modelPanel) modelPanel.style.display = allowModel ? '' : 'none';
    if (!allowModel) {
      var customTab = document.querySelector('.sys-tab[data-sys-tab="custom"]');
      if (customTab) { customTab.click(); customTab.classList.add('active'); }
      if (document.getElementById('sysTabCustom')) document.getElementById('sysTabCustom').style.display = '';
    }
  } else {
    if (modelTab) modelTab.style.display = '';
    if (modelPanel) modelPanel.style.display = '';
  }
  var sutuiBlock = document.getElementById('sutuiTokenBlock');
  if (sutuiBlock) sutuiBlock.style.display = (EDITION !== 'online') ? '' : 'none';
  if (EDITION !== 'online') loadSutuiConfig();
  loadLanInfo();
  loadInstallationSlotStatus();
  loadAssetPathSettings();
  loadChatRouteMode();
  if (_currentSysTab === 'custom') loadCustomConfigs();
  ocConfigLoaded = true;
}

function checkOcStatus() {
  return;
  /*
  var dot = document.getElementById('ocStatusDot');
  var text = document.getElementById('ocStatusText');
  var msgEl = document.getElementById('ocSaveMsg');
  if (!dot || !text) return;
  dot.className = 'status-dot';
  text.textContent = '检查中...';
  fetch((LOCAL_API_BASE || '') + '/api/openclaw/status', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.online || d.listener_online) {
        dot.className = 'status-dot online';
        text.textContent = d.message || 'OpenClaw Gateway 运行中';
        if (msgEl && msgEl.textContent && msgEl.textContent.indexOf('上次启动') === 0) {
          msgEl.style.display = 'none';
        }
      } else {
        dot.className = 'status-dot offline';
        text.textContent = d.message || 'OpenClaw Gateway 未运行';
        if (d.last_startup && d.last_startup.status === 'failed') {
          var reason = d.last_startup.reason || '启动失败';
          var pids = d.last_startup.gateway_pids && d.last_startup.gateway_pids.length
            ? ('，进程 ' + d.last_startup.gateway_pids.join(','))
            : '';
          showMsg(msgEl, '上次启动：' + reason + pids, true);
        }
      }
    })
    .catch(function() {
      dot.className = 'status-dot offline';
      text.textContent = 'OpenClaw Gateway 无法连接';
    });
  */
}

function saveOcConfig() {
  var btn = document.getElementById('saveOcConfigBtn');
  var msgEl = document.getElementById('ocSaveMsg');
  if (btn) btn.disabled = true;
  var modelSel = document.getElementById('ocPrimaryModel');
  var body = {};
  if (modelSel) body.primary_model = modelSel.value;

  fetch((LOCAL_API_BASE || '') + '/api/openclaw/config', {
    method: 'POST', headers: authHeaders(),
    body: JSON.stringify(body)
  })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(x) {
      if (x.ok) {
        showMsg(msgEl, x.data.message || '保存成功', false);
        ocConfigLoaded = false;
        loadOpenClawConfig();
        if (typeof refreshModelSelector === 'function') refreshModelSelector();
        setTimeout(checkOcStatus, 3000);
      } else {
        showMsg(msgEl, x.data.detail || '保存失败', true);
      }
    })
    .catch(function() { showMsg(msgEl, '网络错误', true); })
    .finally(function() { if (btn) btn.disabled = false; });
}

// Custom JSON Config Import
function saveCustomConfig() {
  var nameEl = document.getElementById('customConfigName');
  var jsonEl = document.getElementById('customConfigJson');
  var msgEl = document.getElementById('customConfigMsg');
  var name = (nameEl.value || '').trim();
  var raw = (jsonEl.value || '').trim();
  if (!name) { showMsg(msgEl, '请填写配置名称', true); return; }
  if (!raw) { showMsg(msgEl, '请填写配置内容', true); return; }

  // Pre-process: strip Python variable assignment like "TOS_CONFIG = {"
  var cleaned = raw;
  var assignMatch = cleaned.match(/^\s*\w+\s*=\s*\{/);
  if (assignMatch) {
    cleaned = cleaned.replace(/^\s*\w+\s*=\s*/, '');
  }

  fetch((LOCAL_API_BASE || '') + '/api/custom-configs', {
    method: 'POST', headers: authHeaders(),
    body: JSON.stringify({ name: name, config_json: cleaned })
  })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(x) {
      if (x.ok) {
        showMsg(msgEl, x.data.message || '导入成功', false);
        nameEl.value = '';
        jsonEl.value = '';
        loadCustomConfigs();
        if (typeof refreshModelSelector === 'function') refreshModelSelector();
      } else {
        showMsg(msgEl, x.data.detail || '导入失败', true);
      }
    })
    .catch(function() { showMsg(msgEl, '网络错误', true); });
}

function loadCustomConfigs() {
  var el = document.getElementById('customConfigList');
  if (!el) return;
  fetch((LOCAL_API_BASE || '') + '/api/custom-configs', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var configs = (d && Array.isArray(d.configs)) ? d.configs : [];
      if (!configs.length) {
        el.innerHTML = '<p class="meta">暂无自定义配置</p>';
        return;
      }
      el.innerHTML = configs.map(function(c) {
        var preview = JSON.stringify(c.config, null, 2);
        if (preview.length > 500) preview = preview.substring(0, 500) + '\n...';
        return '<div class="config-block-item">' +
          '<div class="block-header">' +
          '<span class="block-name">' + escapeHtml(c.name) + '</span>' +
          '<button type="button" class="btn btn-ghost btn-sm" data-delete-config="' + escapeAttr(c.name) + '">删除</button>' +
          '</div>' +
          '<pre>' + escapeHtml(preview) + '</pre>' +
          '</div>';
      }).join('');
      el.querySelectorAll('button[data-delete-config]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var cfgName = btn.getAttribute('data-delete-config');
          if (!confirm('确定删除配置 ' + cfgName + '？')) return;
          fetch((LOCAL_API_BASE || '') + '/api/custom-configs/' + encodeURIComponent(cfgName), {
            method: 'DELETE', headers: authHeaders()
          })
            .then(function(r) { return r.json(); })
            .then(function() { loadCustomConfigs(); })
            .catch(function() { alert('删除失败'); });
        });
      });
    })
    .catch(function() { el.innerHTML = '<p class="msg err">加载失败</p>'; });
}

var saveOcBtn = document.getElementById('saveOcConfigBtn');
if (saveOcBtn) saveOcBtn.addEventListener('click', saveOcConfig);
var saveSutuiTokenBtn = document.getElementById('saveSutuiTokenBtn');
if (saveSutuiTokenBtn) saveSutuiTokenBtn.addEventListener('click', saveSutuiToken);
var saveAccountPasswordBtn = document.getElementById('saveAccountPasswordBtn');
if (saveAccountPasswordBtn) saveAccountPasswordBtn.addEventListener('click', saveAccountPassword);
document.addEventListener('click', function(e) {
  var target = e.target && e.target.closest ? e.target.closest('#saveChatRouteModeBtn') : null;
  if (!target) return;
  e.preventDefault();
  saveChatRouteMode();
});
document.querySelectorAll('input[name="chatRouteMode"]').forEach(function(radio) {
  radio.addEventListener('change', function() {
    if (!radio.checked) return;
    clearChatRouteModeMsg();
    if (_chatRouteModeSavedValue !== null && getChatRouteModeValue() !== _chatRouteModeSavedValue) {
      showChatRouteModeMsg('已选择，点击保存后生效', false, false);
    }
  });
});
var refreshOcBtn = document.getElementById('refreshOcStatusBtn');
if (refreshOcBtn) refreshOcBtn.addEventListener('click', function() {
  checkOcStatus();
  ocConfigLoaded = false;
  loadOpenClawConfig();
});
var restartOcBtn = document.getElementById('restartOcBtn');
if (restartOcBtn) {
  restartOcBtn.addEventListener('click', function() {
    var msgEl = document.getElementById('ocSaveMsg');
    restartOcBtn.disabled = true;
    restartOcBtn.textContent = '重启中…';
    showMsg(msgEl, '正在重启 OpenClaw，请稍等...', false);
    fetch((LOCAL_API_BASE || '') + '/api/openclaw/restart', { method: 'POST', headers: authHeaders() })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        showMsg(msgEl, d.message || (d.ok ? '重启成功' : '重启失败'), !d.ok);
        checkOcStatus();
        setTimeout(checkOcStatus, 3000);
      })
      .catch(function() { showMsg(msgEl, '网络错误', true); })
      .finally(function() { restartOcBtn.disabled = false; restartOcBtn.textContent = '重启 OpenClaw'; });
  });
}
var saveCustomBtn = document.getElementById('saveCustomConfigBtn');
if (saveCustomBtn) saveCustomBtn.addEventListener('click', saveCustomConfig);
var saveAssetPathBtn = document.getElementById('saveAssetPathBtn');
if (saveAssetPathBtn) saveAssetPathBtn.addEventListener('click', function() {
  saveAssetPathSettings(false);
});
var copyInstallationSlotIdBtn = document.getElementById('copyInstallationSlotIdBtn');
if (copyInstallationSlotIdBtn) copyInstallationSlotIdBtn.addEventListener('click', function() {
  var id = typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : '';
  if (!id) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(id).then(function() {
      showInstallationSlotMsg('已复制当前槽位 ID', false);
    }).catch(function() {
      showInstallationSlotMsg('复制失败，请手动复制', true);
    });
  } else {
    showInstallationSlotMsg('当前浏览器不支持自动复制', true);
  }
});
var saveInstallationSlotAliasBtn = document.getElementById('saveInstallationSlotAliasBtn');
if (saveInstallationSlotAliasBtn) saveInstallationSlotAliasBtn.addEventListener('click', function() {
  saveInstallationSlotAlias();
});
var randomizeInstallationSlotIdBtn = document.getElementById('randomizeInstallationSlotIdBtn');
if (randomizeInstallationSlotIdBtn) randomizeInstallationSlotIdBtn.addEventListener('click', function() {
  randomizeInstallationSlotId();
});
var resetAssetPathBtn = document.getElementById('resetAssetPathBtn');
if (resetAssetPathBtn) resetAssetPathBtn.addEventListener('click', function() {
  saveAssetPathSettings(true);
});
window.addEventListener('lobster:installation-id-changed', function() {
  loadInstallationSlotStatus();
});

function renderRuntimeRepairResult(data) {
  var root = document.getElementById('repairRuntimeDependenciesResult');
  if (!root) return;
  root.replaceChildren();
  root.style.display = '';

  var checks = data && Array.isArray(data.checks) ? data.checks : [];
  checks.forEach(function(item) {
    var row = document.createElement('div');
    row.style.cssText = 'display:flex;justify-content:space-between;align-items:flex-start;gap:0.75rem;padding:0.55rem 0;border-top:1px solid var(--border);font-size:0.82rem;';
    var label = document.createElement('span');
    label.textContent = item.label || item.key || '运行组件';
    var state = document.createElement('span');
    var failedModules = Array.isArray(item.failures) ? item.failures.map(function(failure) {
      return failure && failure.module ? failure.module : '';
    }).filter(Boolean) : [];
    state.textContent = item.ok ? '正常' : ((item.message || '修复失败') + (failedModules.length ? '：' + failedModules.join('、') : ''));
    if (!item.ok && Array.isArray(item.failures)) {
      state.title = item.failures.map(function(failure) {
        return ((failure && failure.module) || 'module') + ': ' + ((failure && failure.error) || '导入失败');
      }).join('\n');
    }
    state.style.color = item.ok ? 'var(--success, #16a34a)' : 'var(--danger, #dc2626)';
    row.appendChild(label);
    row.appendChild(state);
    root.appendChild(row);
  });

  var install = data && data.install;
  var logText = install && install.log ? String(install.log) : (data && data.pip_log ? String(data.pip_log) : '');
  if (logText) {
    var details = document.createElement('details');
    details.style.marginTop = '0.55rem';
    var summary = document.createElement('summary');
    summary.textContent = '查看修复日志';
    summary.style.cssText = 'cursor:pointer;font-size:0.8rem;color:var(--text-muted);';
    var pre = document.createElement('pre');
    pre.textContent = logText;
    pre.style.cssText = 'margin:0.55rem 0 0;max-height:15rem;overflow:auto;white-space:pre-wrap;word-break:break-word;padding:0.65rem;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:0.72rem;line-height:1.5;';
    details.appendChild(summary);
    details.appendChild(pre);
    root.appendChild(details);
  }
}

var repairRuntimeDependenciesBtn = document.getElementById('repairRuntimeDependenciesBtn');
if (repairRuntimeDependenciesBtn) {
  repairRuntimeDependenciesBtn.addEventListener('click', function() {
    var msgEl = document.getElementById('repairRuntimeDependenciesMsg');
    var resultEl = document.getElementById('repairRuntimeDependenciesResult');
    var originalText = repairRuntimeDependenciesBtn.textContent;
    repairRuntimeDependenciesBtn.disabled = true;
    repairRuntimeDependenciesBtn.textContent = '修复中...';
    if (resultEl) {
      resultEl.replaceChildren();
      resultEl.style.display = 'none';
    }
    showMsg(msgEl, '正在检查并修复运行依赖，请不要关闭客户端...', false);
    if (msgEl) msgEl.style.display = '';

    fetch((LOCAL_API_BASE || '') + '/api/settings/repair-runtime-dependencies', {
      method: 'POST',
      headers: authHeaders()
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (!x.ok) throw new Error((x.data && x.data.detail) || '依赖修复失败');
        renderRuntimeRepairResult(x.data || {});
        var text = (x.data && x.data.message) || '依赖修复完成';
        if (x.data && x.data.restart_recommended) text += '，重启客户端后全部生效';
        showMsg(msgEl, text, !(x.data && x.data.ok));
        if (msgEl) msgEl.style.display = '';
      })
      .catch(function(err) {
        showMsg(msgEl, (err && err.message) || '依赖修复失败', true);
        if (msgEl) msgEl.style.display = '';
      })
      .finally(function() {
        repairRuntimeDependenciesBtn.disabled = false;
        repairRuntimeDependenciesBtn.textContent = originalText;
      });
  });
}

function clearLocalUserConfigClientStorage() {
  try {
    var i;
    var k;
    for (i = localStorage.length - 1; i >= 0; i--) {
      k = localStorage.key(i);
      if (k && (k === 'lobster_chat_sessions' || k.indexOf('lobster_chat_sessions_u') === 0)) {
        try { localStorage.removeItem(k); } catch (e) {}
      }
    }
    ['lobster_api_base', 'lobster_local_api_base', 'lobster_messenger_api_base', 'lobster_twilio_api_base'].forEach(function(key) {
      try { localStorage.removeItem(key); } catch (e) {}
    });
  } catch (e) {}
}

var clearLocalUserConfigBtn = document.getElementById('clearLocalUserConfigBtn');
if (clearLocalUserConfigBtn) {
  clearLocalUserConfigBtn.addEventListener('click', function() {
    var msgEl = document.getElementById('clearLocalUserConfigMsg');
    if (!confirm('确定清除本机当前账号的个人配置？\n（数据库 Token/偏好/算力账号 + 浏览器对话与 API 调试项；不退出登录）')) return;
    clearLocalUserConfigBtn.disabled = true;
    if (msgEl) { msgEl.style.display = 'none'; }
    fetch((LOCAL_API_BASE || '') + '/api/settings/clear-local-user-config', {
      method: 'POST',
      headers: authHeaders()
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (x.ok && x.data && x.data.ok) {
          clearLocalUserConfigClientStorage();
          if (msgEl) {
            showMsg(msgEl, '已清除，页面将刷新…', false);
            msgEl.style.display = '';
          }
          setTimeout(function() { window.location.reload(); }, 600);
        } else {
          var detail = (x.data && (x.data.detail || x.data.message)) || '清除失败';
          if (msgEl) {
            showMsg(msgEl, typeof detail === 'string' ? detail : '清除失败', true);
            msgEl.style.display = '';
          }
        }
      })
      .catch(function() {
        if (msgEl) {
          showMsg(msgEl, '网络错误或本机后端未启动', true);
          msgEl.style.display = '';
        }
      })
      .finally(function() { clearLocalUserConfigBtn.disabled = false; });
  });
}

var clearOpenclawMemoryBtn = document.getElementById('clearOpenclawMemoryBtn');
if (clearOpenclawMemoryBtn) {
  clearOpenclawMemoryBtn.addEventListener('click', function() {
    var msgEl = document.getElementById('clearOpenclawMemoryMsg');
    if (!confirm('确定清除当前账号上传给 OpenClaw 的个人记忆资料？\n（只清除本机 OpenClaw 个人记忆，不删除登录、算力、素材和系统配置）')) return;
    clearOpenclawMemoryBtn.disabled = true;
    if (msgEl) { msgEl.style.display = 'none'; }
    fetch((LOCAL_API_BASE || '') + '/api/openclaw/memory/clear', {
      method: 'DELETE',
      headers: authHeaders()
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (x.ok && x.data && x.data.ok) {
          var deletedCount = Number(x.data.deleted_count || 0);
          if (msgEl) {
            showMsg(msgEl, '已清除 ' + deletedCount + ' 份个人记忆资料', false);
            msgEl.style.display = '';
          }
        } else {
          var detail = (x.data && (x.data.detail || x.data.message)) || '清除失败';
          if (msgEl) {
            showMsg(msgEl, typeof detail === 'string' ? detail : '清除失败', true);
            msgEl.style.display = '';
          }
        }
      })
      .catch(function() {
        if (msgEl) {
          showMsg(msgEl, '网络错误或本机后端未启动', true);
          msgEl.style.display = '';
        }
      })
      .finally(function() { clearOpenclawMemoryBtn.disabled = false; });
  });
}

// xSkill/SuTui config moved to skill store (skill.js)
