(function() {
  var SKILLS = {
    browser_use_skill: {
      title: 'Browser Use',
      subtitle: 'OpenClaw 浏览器工作台'
    },
    computer_use_skill: {
      title: 'Computer Use',
      subtitle: 'OpenClaw 电脑操作工作台'
    }
  };

  var state = {
    skillId: 'browser_use_skill',
    title: 'Browser Use',
    history: [],
    busy: false,
    execMode: 'auto',
    configBusy: false,
    cleanupBusy: false,
    pendingText: '',
    pendingTimer: null,
    requestStartedAt: 0
  };
  var attachmentUrlCache = {};

  function safeText(text) {
    return String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function localBase() {
    return String((typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? LOCAL_API_BASE : '').replace(/\/$/, '');
  }

  function storageKey(skillId) {
    var uid = '';
    try {
      if (typeof getCurrentUserIdFromToken === 'function') uid = getCurrentUserIdFromToken() || '';
    } catch (e) {}
    return 'lobster_openclaw_skill_chat_' + (uid || 'anon') + '_' + String(skillId || 'browser_use_skill');
  }

  function looksLikeUnexecutedToolMarkup(text) {
    var value = String(text || '');
    if (!value) return false;
    return /DSML[\s\S]*tool_calls/i.test(value) ||
      /<\/?tool_calls/i.test(value) ||
      /<\/?invoke/i.test(value);
  }

  function normalizeAssistantReply(text) {
    var value = String(text || '').trim();
    if (!value) return 'OpenClaw 已完成，但没有返回文本内容。';
    if (looksLikeUnexecutedToolMarkup(value)) {
      return 'OpenClaw 返回了未执行的工具调用文本，浏览器步骤没有继续执行。请重新发送任务。';
    }
    return value.replace(/(?:⚠️\s*)?(?:✉️\s*)?Message:\s*`?[^`\r\n]+`?\s+failed/ig, '').trim() || 'OpenClaw 已完成。';
  }

  function isNoisyHistoryMessage(m) {
    var text = String((m && m.content) || '');
    if (!text) return true;
    if (looksLikeUnexecutedToolMarkup(text)) return true;
    return /(Approval required|Reply with:\s*\/approve|\/approve\s+|allow-once|allow-always|需要批准|请批准|批准执行|审批码|Message:\s*`?[^`\r\n]+`?\s+failed)/i.test(text);
  }

  function cleanAttachments(attachments) {
    if (!Array.isArray(attachments)) return [];
    return attachments.filter(function(item) {
      return item && item.kind === 'image' && item.path;
    }).slice(0, 4).map(function(item) {
      return {
        kind: 'image',
        path: String(item.path || ''),
        name: String(item.name || ''),
        size: Number(item.size || 0)
      };
    });
  }

  function cleanHistoryArray(arr) {
    if (!Array.isArray(arr)) return [];
    return arr.filter(function(m) {
      return m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && !isNoisyHistoryMessage(m);
    }).map(function(m) {
      return {
        role: m.role,
        content: String(m.content || ''),
        attachments: cleanAttachments(m.attachments)
      };
    }).slice(-40);
  }

  function ensurePanel() {
    if (document.getElementById('content-openclaw-skill-chat')) return;
    var host = document.querySelector('.dashboard-main') || document.body;
    var panel = document.createElement('div');
    panel.id = 'content-openclaw-skill-chat';
    panel.className = 'content-block';
    panel.innerHTML =
      '<div class="card">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:0.75rem;flex-wrap:wrap;">' +
          '<div>' +
            '<h3 id="openclawSkillChatTitle" style="margin:0;">OpenClaw 工作台</h3>' +
            '<p id="openclawSkillChatSubtitle" style="margin:0.3rem 0 0 0;font-size:0.84rem;color:var(--text-muted);">Browser Use</p>' +
          '</div>' +
          '<div style="display:flex;gap:0.5rem;flex-wrap:wrap;">' +
            '<label style="display:flex;align-items:center;gap:0.35rem;font-size:0.82rem;color:var(--text-muted);">' +
              '<span>执行权限</span>' +
              '<select id="openclawSkillExecMode" style="height:32px;border:1px solid var(--border);border-radius:6px;background:var(--panel);color:var(--text);padding:0 0.45rem;">' +
                '<option value="auto">自动执行</option>' +
                '<option value="confirm">每次确认</option>' +
              '</select>' +
            '</label>' +
            '<button type="button" id="openclawSkillChatClearBtn" class="btn btn-ghost btn-sm">清空</button>' +
            '<button type="button" id="openclawSkillBrowserCloseBtn" class="btn btn-ghost btn-sm">关闭浏览器</button>' +
            '<button type="button" id="openclawSkillChatBackBtn" class="btn btn-ghost btn-sm">返回技能商店</button>' +
          '</div>' +
        '</div>' +
        '<div id="openclawSkillConfigStatus" style="margin-top:0.5rem;font-size:0.8rem;color:var(--text-muted);"></div>' +
      '</div>' +
      '<div class="card openclaw-skill-chat-shell">' +
        '<div id="openclawSkillChatLog" class="openclaw-skill-chat-log"></div>' +
        '<form id="openclawSkillChatForm" class="openclaw-skill-chat-form">' +
          '<textarea id="openclawSkillChatInput" rows="2" placeholder="发消息给 OpenClaw"></textarea>' +
          '<button type="submit" id="openclawSkillChatSendBtn" class="btn btn-primary">发送</button>' +
        '</form>' +
        '<div id="openclawSkillChatMsg" class="msg" style="display:none;"></div>' +
      '</div>';
    host.appendChild(panel);
  }

  function loadHistory(skillId) {
    try {
      var raw = localStorage.getItem(storageKey(skillId));
      var arr = raw ? JSON.parse(raw) : [];
      var cleaned = cleanHistoryArray(arr);
      if (JSON.stringify(cleaned) !== JSON.stringify(arr)) {
        localStorage.setItem(storageKey(skillId), JSON.stringify(cleaned));
      }
      return cleaned;
    } catch (e) {
      return [];
    }
  }

  function saveHistory() {
    try {
      state.history = cleanHistoryArray(state.history);
      localStorage.setItem(storageKey(state.skillId), JSON.stringify(state.history.slice(-40)));
    } catch (e) {}
  }

  function switchView() {
    ensurePanel();
    try { location.hash = 'openclaw-skill-chat'; } catch (e0) {}
    try {
      if (typeof currentView !== 'undefined' && currentView === 'chat' && typeof saveCurrentSessionToStore === 'function') {
        saveCurrentSessionToStore();
      }
      document.querySelectorAll('.nav-left-item').forEach(function(b) { b.classList.remove('active'); });
      document.querySelectorAll('.content-block').forEach(function(p) { p.classList.remove('visible'); });
      var contentEl = document.getElementById('content-openclaw-skill-chat');
      if (contentEl) contentEl.classList.add('visible');
      if (typeof currentView !== 'undefined') currentView = 'openclaw-skill-chat';
    } catch (e) {}
  }

  function setMessage(text, isErr) {
    var el = document.getElementById('openclawSkillChatMsg');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'msg' + (isErr ? ' err' : '');
    el.style.display = text ? 'block' : 'none';
  }

  function pendingStatusText() {
    if (!state.requestStartedAt) return 'OpenClaw 已收到消息，正在执行...';
    var elapsed = Math.max(1, Math.floor((Date.now() - state.requestStartedAt) / 1000));
    if (elapsed < 8) return 'OpenClaw 已收到消息，正在执行...';
    if (elapsed < 45) return 'OpenClaw 正在执行，已等待 ' + elapsed + ' 秒...';
    if (elapsed < 120) return '浏览器/电脑操作可能需要更久，已等待 ' + elapsed + ' 秒...';
    return '任务仍在运行，已等待 ' + elapsed + ' 秒；如果目标网页很慢，可以继续等最终结果。';
  }

  function startPendingStatus() {
    stopPendingStatus();
    state.requestStartedAt = Date.now();
    state.pendingText = pendingStatusText();
    setMessage(state.pendingText, false);
    render();
    state.pendingTimer = setInterval(function() {
      if (!state.busy) return;
      state.pendingText = pendingStatusText();
      setMessage(state.pendingText, false);
      render();
    }, 5000);
  }

  function stopPendingStatus() {
    if (state.pendingTimer) {
      clearInterval(state.pendingTimer);
      state.pendingTimer = null;
    }
    state.pendingText = '';
    state.requestStartedAt = 0;
  }

  function setConfigStatus(text, isErr) {
    var el = document.getElementById('openclawSkillConfigStatus');
    if (!el) return;
    el.textContent = text || '';
    el.style.color = isErr ? '#b91c1c' : 'var(--text-muted)';
  }

  function renderExecConfig(data) {
    var mode = (data && data.mode === 'confirm') ? 'confirm' : 'auto';
    state.execMode = mode;
    var select = document.getElementById('openclawSkillExecMode');
    if (select) select.value = mode;
    var status = mode === 'auto' ? '执行权限：自动执行' : '执行权限：每次确认';
    if (data && data.changed) status += data.restarted ? '，已重启 OpenClaw' : '，已保存';
    if (data && data.gateway_online === false) status += '，OpenClaw 未监听';
    setConfigStatus(status, data && data.gateway_online === false);
  }

  function requestExecConfig(path, body) {
    var base = localBase();
    if (!base) return Promise.reject(new Error('未检测到本机后端'));
    var headers = (typeof authHeaders === 'function') ? authHeaders() : {};
    headers['Content-Type'] = 'application/json';
    return fetch(base + path, {
      method: body ? 'POST' : 'GET',
      headers: headers,
      body: body ? JSON.stringify(body) : undefined
    }).then(function(r) {
      return r.json().catch(function() { return {}; }).then(function(d) {
        if (!r.ok) throw new Error((d && d.detail) || '配置失败');
        return d;
      });
    });
  }

  function cleanupBrowserRuntime(opts) {
    opts = opts || {};
    if (state.skillId !== 'browser_use_skill') return Promise.resolve({ ok: true, skipped: true });
    if (state.cleanupBusy) return Promise.resolve({ ok: true, busy: true });
    var base = localBase();
    if (!base) return Promise.reject(new Error('未检测到本机后端'));
    state.cleanupBusy = true;
    var btn = document.getElementById('openclawSkillBrowserCloseBtn');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '关闭中...';
    }
    if (!opts.silent) setMessage('正在关闭 Browser Use 浏览器...', false);
    var headers = (typeof authHeaders === 'function') ? authHeaders() : {};
    headers['Content-Type'] = 'application/json';
    return fetch(base + '/api/openclaw/skill-chat/runtime/cleanup', {
      method: 'POST',
      headers: headers,
      keepalive: !!opts.keepalive,
      body: JSON.stringify({ skill_id: 'browser_use_skill' })
    }).then(function(r) {
      return r.json().catch(function() { return {}; }).then(function(d) {
        if (!r.ok) throw new Error((d && d.detail) || '关闭 Browser Use 浏览器失败');
        if (!opts.silent) {
          var leftovers = Number(d && d.leftovers_count || 0);
          setMessage(leftovers > 0 ? '已尝试关闭，仍有残留进程，请稍后重试。' : 'Browser Use 浏览器已关闭。', leftovers > 0);
        }
        return d;
      });
    }).catch(function(err) {
      if (!opts.silent) setMessage((err && err.message) ? err.message : '关闭 Browser Use 浏览器失败', true);
      throw err;
    }).finally(function() {
      state.cleanupBusy = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = '关闭浏览器';
      }
    });
  }

  function showSkillStore() {
    if (typeof _ensureSkillStoreVisible === 'function') {
      _ensureSkillStoreVisible();
      return;
    }
    var nav = document.querySelector('.nav-left-item[data-view="skill-store"]');
    if (nav) nav.click();
  }

  function ensureExecConfig() {
    if (state.configBusy) return;
    state.configBusy = true;
    setConfigStatus('执行权限检查中...', false);
    requestExecConfig('/api/openclaw/skill-chat/config/ensure', {})
      .then(renderExecConfig)
      .catch(function(err) {
        setConfigStatus((err && err.message) ? err.message : '配置检查失败', true);
      })
      .finally(function() {
        state.configBusy = false;
      });
  }

  function updateExecConfig(mode) {
    state.configBusy = true;
    setConfigStatus('执行权限保存中...', false);
    requestExecConfig('/api/openclaw/skill-chat/config', { mode: mode })
      .then(renderExecConfig)
      .catch(function(err) {
        setConfigStatus((err && err.message) ? err.message : '配置保存失败', true);
        var select = document.getElementById('openclawSkillExecMode');
        if (select) select.value = state.execMode || 'auto';
      })
      .finally(function() {
        state.configBusy = false;
      });
  }

  function setBusy(flag) {
    state.busy = !!flag;
    var btn = document.getElementById('openclawSkillChatSendBtn');
    var input = document.getElementById('openclawSkillChatInput');
    if (btn) {
      btn.disabled = state.busy;
      btn.textContent = state.busy ? '处理中' : '发送';
    }
    if (input) input.disabled = state.busy;
  }

  function renderAttachmentHtml(m) {
    var attachments = cleanAttachments(m && m.attachments);
    if (!attachments.length) return '';
    return '<div class="openclaw-skill-chat-attachments">' + attachments.map(function(item) {
      var title = item.name || item.path;
      return '<div class="openclaw-skill-chat-attachment">' +
        '<img alt="' + safeText(title) + '" data-openclaw-img-path="' + safeText(item.path) + '" />' +
        '<div class="openclaw-skill-chat-attachment-name">' + safeText(title) + '</div>' +
      '</div>';
    }).join('') + '</div>';
  }

  function hydrateAttachmentImages(root) {
    var base = localBase();
    if (!base || !root) return;
    root.querySelectorAll('img[data-openclaw-img-path]').forEach(function(img) {
      if (img.dataset.loaded === '1') return;
      var path = img.getAttribute('data-openclaw-img-path') || '';
      if (!path) return;
      var cacheKey = state.skillId + '|' + path;
      if (attachmentUrlCache[cacheKey]) {
        img.src = attachmentUrlCache[cacheKey];
        img.dataset.loaded = '1';
        return;
      }
      img.dataset.loaded = '1';
      img.style.opacity = '0.45';
      var headers = (typeof authHeaders === 'function') ? authHeaders() : {};
      fetch(base + '/api/openclaw/skill-chat/workspace-file?skill_id=' + encodeURIComponent(state.skillId) + '&path=' + encodeURIComponent(path), {
        method: 'GET',
        headers: headers
      }).then(function(r) {
        if (!r.ok) throw new Error('image load failed');
        return r.blob();
      }).then(function(blob) {
        var url = URL.createObjectURL(blob);
        attachmentUrlCache[cacheKey] = url;
        img.src = url;
        img.style.opacity = '1';
      }).catch(function() {
        img.alt = '图片加载失败：' + path;
        img.style.display = 'none';
      });
    });
  }

  function render() {
    var meta = SKILLS[state.skillId] || SKILLS.browser_use_skill;
    var titleEl = document.getElementById('openclawSkillChatTitle');
    var subEl = document.getElementById('openclawSkillChatSubtitle');
    var log = document.getElementById('openclawSkillChatLog');
    if (titleEl) titleEl.textContent = state.title || meta.title;
    if (subEl) subEl.textContent = meta.subtitle || '';
    var closeBtn = document.getElementById('openclawSkillBrowserCloseBtn');
    if (closeBtn) closeBtn.style.display = state.skillId === 'browser_use_skill' ? '' : 'none';
    if (!log) return;
    var html = '';
    if (!state.history.length) {
      html = '<div class="openclaw-skill-chat-bubble assistant">已连接 ' + safeText(state.title || meta.title) + '。</div>';
    } else {
      html = state.history.map(function(m) {
        var role = m.role === 'user' ? 'user' : 'assistant';
        return '<div class="openclaw-skill-chat-bubble ' + role + '">' + safeText(m.content || '') + renderAttachmentHtml(m) + '</div>';
      }).join('');
    }
    if (state.pendingText) {
      html += '<div class="openclaw-skill-chat-bubble assistant">' + safeText(state.pendingText) + '</div>';
    }
    log.innerHTML = html;
    hydrateAttachmentImages(log);
    log.scrollTop = log.scrollHeight;
  }

  function openSkill(skillId, title) {
    ensurePanel();
    var id = String(skillId || state.skillId || 'browser_use_skill').trim();
    if (!SKILLS[id]) id = 'browser_use_skill';
    state.skillId = id;
    state.title = title || SKILLS[id].title;
    state.history = loadHistory(id);
    if (state.busy) {
      if (!state.pendingText) state.pendingText = pendingStatusText();
      setMessage(state.pendingText, false);
    } else {
      setMessage('', false);
    }
    switchView();
    render();
    ensureExecConfig();
    setTimeout(function() {
      var input = document.getElementById('openclawSkillChatInput');
      if (input) input.focus();
    }, 60);
  }

  function append(role, content, attachments) {
    state.history.push({ role: role, content: String(content || ''), attachments: cleanAttachments(attachments) });
    state.history = state.history.slice(-40);
    saveHistory();
    render();
  }

  function sendMessage() {
    if (state.busy) return;
    var input = document.getElementById('openclawSkillChatInput');
    if (!input) return;
    var text = String(input.value || '').trim();
    if (!text) return;
    var base = localBase();
    if (!base) {
      setMessage('未检测到本机后端，无法连接 OpenClaw。', true);
      return;
    }
    var previous = cleanHistoryArray(state.history).slice(-20).map(function(m) {
      return { role: m.role, content: m.content };
    });
    input.value = '';
    setMessage('', false);
    append('user', text);
    setBusy(true);
    startPendingStatus();
    var headers = (typeof authHeaders === 'function') ? authHeaders() : {};
    headers['Content-Type'] = 'application/json';
    fetch(base + '/api/openclaw/skill-chat', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({
        skill_id: state.skillId,
        message: text,
        history: previous
      })
    })
      .then(function(r) {
        return r.json().catch(function() { return {}; }).then(function(d) {
          return { ok: r.ok, data: d };
        });
      })
      .then(function(res) {
        if (!res.ok) {
          throw new Error((res.data && res.data.detail) || 'OpenClaw 请求失败');
        }
        stopPendingStatus();
        setMessage('', false);
        append('assistant', normalizeAssistantReply(res.data && res.data.reply), (res.data && res.data.attachments) || []);
      })
      .catch(function(err) {
        stopPendingStatus();
        var msg = (err && err.message) ? err.message : String(err || 'OpenClaw 请求失败');
        append('assistant', msg);
        setMessage(msg, true);
      })
      .finally(function() {
        setBusy(false);
        if (state.skillId === 'browser_use_skill') {
          cleanupBrowserRuntime({ silent: true }).catch(function() {});
        }
      });
  }

  function bind() {
    ensurePanel();
    var form = document.getElementById('openclawSkillChatForm');
    if (form) {
      form.addEventListener('submit', function(e) {
        e.preventDefault();
        sendMessage();
      });
    }
    var input = document.getElementById('openclawSkillChatInput');
    if (input) {
      input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendMessage();
        }
      });
    }
    var clearBtn = document.getElementById('openclawSkillChatClearBtn');
    if (clearBtn) {
      clearBtn.addEventListener('click', function() {
        state.history = [];
        saveHistory();
        setMessage('', false);
        render();
      });
    }
    var browserCloseBtn = document.getElementById('openclawSkillBrowserCloseBtn');
    if (browserCloseBtn) {
      browserCloseBtn.addEventListener('click', function() {
        cleanupBrowserRuntime({ silent: false }).catch(function() {});
      });
    }
    var execMode = document.getElementById('openclawSkillExecMode');
    if (execMode) {
      execMode.addEventListener('change', function() {
        updateExecConfig(execMode.value === 'confirm' ? 'confirm' : 'auto');
      });
    }
    var backBtn = document.getElementById('openclawSkillChatBackBtn');
    if (backBtn) {
      backBtn.addEventListener('click', function() {
        if (state.skillId === 'browser_use_skill') {
          showSkillStore();
          cleanupBrowserRuntime({ silent: true }).catch(function() {});
          return;
        }
        showSkillStore();
      });
    }
    window.addEventListener('beforeunload', function() {
      if (state.skillId === 'browser_use_skill' && !state.busy) {
        cleanupBrowserRuntime({ silent: true, keepalive: true }).catch(function() {});
      }
    });
    document.addEventListener('click', function(e) {
      var nav = e.target && e.target.closest ? e.target.closest('.nav-left-item[data-view]') : null;
      if (!nav) return;
      if (state.skillId === 'browser_use_skill' && typeof currentView !== 'undefined' && currentView === 'openclaw-skill-chat') {
        cleanupBrowserRuntime({ silent: true, keepalive: true }).catch(function() {});
      }
    }, true);
  }

  window.openOpenclawSkillChat = openSkill;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
