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
  checkOcStatus();
  loadLanInfo();
  loadChatRouteMode();
  if (_currentSysTab === 'custom') loadCustomConfigs();
  if (ocConfigLoaded && EDITION !== 'online') return;
  if (EDITION === 'online' && !allowModel) { return; }
  fetch((LOCAL_API_BASE || '') + '/api/openclaw/config', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      ocConfigLoaded = true;
      var modelSel = document.getElementById('ocPrimaryModel');
      if (modelSel && d.primary_model) {
        for (var i = 0; i < modelSel.options.length; i++) {
          if (modelSel.options[i].value === d.primary_model) {
            modelSel.selectedIndex = i;
            break;
          }
        }
      }
      ocProviderData = [];
    })
    .catch(function() {});
}

function checkOcStatus() {
  var dot = document.getElementById('ocStatusDot');
  var text = document.getElementById('ocStatusText');
  if (!dot || !text) return;
  dot.className = 'status-dot';
  text.textContent = '检查中...';
  fetch((LOCAL_API_BASE || '') + '/api/openclaw/status', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.online) {
        dot.className = 'status-dot online';
        text.textContent = 'OpenClaw Gateway 运行中';
      } else {
        dot.className = 'status-dot offline';
        text.textContent = 'OpenClaw Gateway 未运行';
      }
    })
    .catch(function() {
      dot.className = 'status-dot offline';
      text.textContent = 'OpenClaw Gateway 无法连接';
    });
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
    fetch((LOCAL_API_BASE || '') + '/api/openclaw/restart', { method: 'POST', headers: authHeaders() })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        showMsg(msgEl, d.message || (d.ok ? '重启成功' : '重启失败'), !d.ok);
        setTimeout(checkOcStatus, 3000);
      })
      .catch(function() { showMsg(msgEl, '网络错误', true); })
      .finally(function() { restartOcBtn.disabled = false; restartOcBtn.textContent = '重启 Gateway'; });
  });
}
var saveCustomBtn = document.getElementById('saveCustomConfigBtn');
if (saveCustomBtn) saveCustomBtn.addEventListener('click', saveCustomConfig);

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
