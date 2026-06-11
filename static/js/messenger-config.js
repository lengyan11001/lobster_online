/**
 * Messenger 多应用配置：CRUD 走 MESSENGER_API_BASE（海外 lobster_server）。
 */
(function() {
  var listEl = document.getElementById('messengerConfigList');
  var backBtn = document.getElementById('messengerConfigBackBtn');
  var addBtn = document.getElementById('messengerConfigAddBtn');
  var checklistBtn = document.getElementById('messengerCopyChecklistBtn');
  var modal = document.getElementById('messengerConfigModal');
  var modalTitle = document.getElementById('messengerConfigModalTitle');
  var editIdEl = document.getElementById('messengerConfigEditId');
  var cloudInput = document.getElementById('messengerCloudUrlInput');
  var saveCloudBtn = document.getElementById('saveMessengerCloudConfigBtn');
  var cloudMsg = document.getElementById('messengerCloudConfigMsg');
  var modalCancelBtn = document.getElementById('messengerConfigModalCancel');
  var modalSaveBtn = document.getElementById('messengerConfigModalSave');
  var messengerToastTimer = null;

  function resolveBase() {
    var stored = '';
    try { stored = localStorage.getItem('lobster_messenger_api_base') || ''; } catch (e1) {}
    var runtime = (typeof window.__MESSENGER_API_BASE !== 'undefined' && window.__MESSENGER_API_BASE) ? String(window.__MESSENGER_API_BASE) : '';
    var globalBase = (typeof MESSENGER_API_BASE !== 'undefined' && MESSENGER_API_BASE) ? String(MESSENGER_API_BASE) : '';
    return String(stored || runtime || globalBase || '').trim().replace(/\/$/, '');
  }

  function base() {
    return resolveBase();
  }

  function api(method, path, body) {
    var b = resolveBase();
    if (!b) {
      return Promise.reject(new Error('未配置 Messenger API 基址'));
    }
    var opts = { method: method, headers: typeof authHeaders === 'function' ? authHeaders() : {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(b.replace(/\/$/, '') + path, opts);
  }

  function showMsg(el, text, isErr) {
    if (!el) return;
    el.textContent = text || '';
    el.className = 'msg' + (isErr ? ' err' : '');
    el.style.display = text ? 'inline-block' : 'none';
  }

  function ensureMessengerToast() {
    var toast = document.getElementById('messengerConfigToast');
    if (toast) return toast;
    toast = document.createElement('div');
    toast.id = 'messengerConfigToast';
    toast.className = 'seedance-task-toast messenger-config-toast';
    toast.innerHTML = [
      '<div class="seedance-task-toast-main">',
      '<strong class="seedance-task-toast-title"></strong>',
      '<p class="seedance-task-toast-body"></p>',
      '</div>'
    ].join('');
    document.body.appendChild(toast);
    return toast;
  }

  function showToast(title, body, isErr) {
    var toast = ensureMessengerToast();
    var titleEl = toast.querySelector('.seedance-task-toast-title');
    var bodyEl = toast.querySelector('.seedance-task-toast-body');
    if (messengerToastTimer) {
      clearTimeout(messengerToastTimer);
      messengerToastTimer = null;
    }
    toast.classList.remove('is-visible', 'is-success', 'is-error');
    toast.classList.add(isErr ? 'is-error' : 'is-success');
    if (titleEl) titleEl.textContent = title || (isErr ? '保存失败' : '保存成功');
    if (bodyEl) {
      bodyEl.textContent = body || '';
      bodyEl.style.display = body ? 'block' : 'none';
    }
    window.requestAnimationFrame(function() {
      toast.classList.add('is-visible');
    });
    messengerToastTimer = window.setTimeout(function() {
      toast.classList.remove('is-visible');
      messengerToastTimer = null;
    }, 3000);
  }

  function loadMessengerCloudInput() {
    if (cloudInput) {
      cloudInput.value = (localStorage.getItem('lobster_messenger_api_base') || (typeof window.__MESSENGER_API_BASE !== 'undefined' ? window.__MESSENGER_API_BASE : '') || '').trim();
    }
  }

  function loadList() {
    if (!listEl) return;
    listEl.innerHTML = '<p class="meta">加载中…</p>';
    api('GET', '/api/messenger/configs')
      .then(function(r) {
        if (r.status === 401) {
          listEl.innerHTML = '<p class="meta">未登录或 JWT 无效。请确认 Messenger 海外服与登录服共用用户库与 SECRET_KEY，或先在海外站注册登录测试。</p>';
          return null;
        }
        return r.json();
      })
      .then(function(d) {
        if (!listEl || d === null) return;
        if (!d || !Array.isArray(d.configs)) {
          listEl.innerHTML = '<p class="meta">加载失败</p>';
          return;
        }
        var configs = d.configs;
        if (configs.length === 0) {
          listEl.innerHTML = '<p class="meta">暂无配置，点击「添加应用」。</p>';
          return;
        }
        listEl.innerHTML = configs.map(function(c) {
          var url = c.webhook_url || '';
          var name = (c.name || '未命名').trim() || '未命名';
          return '<div class="skill-store-card" data-mid="' + escapeAttr(String(c.id)) + '">' +
            '<div class="card-label">应用</div>' +
            '<div class="card-value">' + escapeHtml(name) + '</div>' +
            '<div class="card-desc">Page ID: ' + escapeHtml(c.page_id || '-') + '</div>' +
            '<pre class="config-block-item" style="font-size:0.72rem;margin:0.5rem 0;padding:0.4rem;background:rgba(0,0,0,0.2);border-radius:4px;overflow-x:auto;white-space:pre-wrap;word-break:break-all;">' + escapeHtml(url) + '</pre>' +
            '<div class="card-actions">' +
            '<button type="button" class="btn btn-ghost btn-sm messenger-copy-url" data-url="' + escapeAttr(url) + '">复制 Webhook URL</button>' +
            '<button type="button" class="btn btn-ghost btn-sm messenger-edit" data-id="' + escapeAttr(String(c.id)) + '">编辑</button>' +
            '<button type="button" class="btn btn-ghost btn-sm messenger-del" data-id="' + escapeAttr(String(c.id)) + '">删除</button>' +
            '</div></div>';
        }).join('');
        listEl.querySelectorAll('.messenger-copy-url').forEach(function(btn) {
          btn.addEventListener('click', function() {
            var u = btn.getAttribute('data-url') || '';
            copyToClipboard(u, function() { btn.textContent = '已复制'; setTimeout(function() { btn.textContent = '复制 Webhook URL'; }, 1500); });
          });
        });
        listEl.querySelectorAll('.messenger-edit').forEach(function(btn) {
          btn.addEventListener('click', function() { openEdit(parseInt(btn.getAttribute('data-id'), 10)); });
        });
        listEl.querySelectorAll('.messenger-del').forEach(function(btn) {
          btn.addEventListener('click', function() {
            if (!confirm('确定删除该 Messenger 配置？')) return;
            api('DELETE', '/api/messenger/configs/' + btn.getAttribute('data-id')).then(function(r) {
              if (r.ok) loadList();
            });
          });
        });
      })
      .catch(function() {
        if (listEl) listEl.innerHTML = '<p class="meta err">请求失败（检查 Messenger API 基址与网络）</p>';
      });
  }

  function openAdd() {
    editIdEl.value = '';
    modalTitle.textContent = '添加 Messenger 应用';
    document.getElementById('messengerConfigName').value = '';
    document.getElementById('messengerVerifyToken').value = '';
    document.getElementById('messengerAppSecret').value = '';
    document.getElementById('messengerPageId').value = '';
    document.getElementById('messengerPageToken').value = '';
    document.getElementById('messengerProductKnowledge').value = '';
    showMsg(document.getElementById('messengerConfigModalMsg'), '', false);
    modal.classList.add('visible');
  }

  function openEdit(id) {
    api('GET', '/api/messenger/configs/' + id)
      .then(function(r) { return r.json(); })
      .then(function(c) {
        editIdEl.value = String(c.id);
        modalTitle.textContent = '编辑 Messenger 应用';
        document.getElementById('messengerConfigName').value = c.name || '';
        document.getElementById('messengerVerifyToken').value = c.verify_token || '';
        document.getElementById('messengerAppSecret').value = c.app_secret || '';
        document.getElementById('messengerPageId').value = c.page_id || '';
        document.getElementById('messengerPageToken').value = c.page_access_token || '';
        document.getElementById('messengerProductKnowledge').value = c.product_knowledge || '';
        showMsg(document.getElementById('messengerConfigModalMsg'), '', false);
        modal.classList.add('visible');
      });
  }

  function saveModal() {
    var msgEl = document.getElementById('messengerConfigModalMsg');
    if (modalSaveBtn) {
      modalSaveBtn.disabled = true;
      modalSaveBtn.textContent = '保存中…';
    }
    showMsg(msgEl, '保存中…', false);
    var body = {
      name: document.getElementById('messengerConfigName').value.trim() || 'Messenger',
      verify_token: document.getElementById('messengerVerifyToken').value.trim(),
      app_secret: document.getElementById('messengerAppSecret').value.trim(),
      page_id: document.getElementById('messengerPageId').value.trim(),
      page_access_token: document.getElementById('messengerPageToken').value.trim(),
      product_knowledge: document.getElementById('messengerProductKnowledge').value.trim() || null
    };
    var eid = editIdEl.value.trim();
    var p = eid
      ? api('PUT', '/api/messenger/configs/' + eid, body)
      : api('POST', '/api/messenger/configs', body);
    p.then(function(r) {
      if (!r.ok) return r.json().then(function(j) { throw new Error(j.detail || r.status); });
      return r.json();
    })
      .then(function() {
        showMsg(msgEl, '已保存', false);
        modal.classList.remove('visible');
        showToast('保存成功', '配置已保存', false);
        loadList();
      })
      .catch(function(e) {
        var errText = (e && e.message) ? e.message : '保存失败';
        showMsg(msgEl, errText, true);
        showToast('保存失败', errText, true);
      })
      .finally(function() {
        if (modalSaveBtn) {
          modalSaveBtn.disabled = false;
          modalSaveBtn.textContent = '保存';
        }
      });
  }

  function copyChecklist() {
    var b = base();
    var t = [
      '【Meta 开发者后台 — Webhook】',
      '1. 回调 URL：保存下方列表中「复制 Webhook URL」的完整地址（每个应用不同 path）。',
      '2. Verify Token：与本页添加应用时填写的 Verify Token 完全一致。',
      '3. 订阅字段：messages、messaging_postbacks（按需）等。',
      '4. App Secret：用于签名校验，填在「App Secret」字段。',
      '5. Page Access Token：需含 messages 权限；Page ID 与主页一致。',
      '',
      '【API 基址】' + b
    ].join('\n');
    copyToClipboard(t, function() { if (checklistBtn) { checklistBtn.textContent = '已复制'; setTimeout(function() { checklistBtn.textContent = '复制 Meta 配置清单模板'; }, 2000); } });
  }

  if (saveCloudBtn && cloudInput) {
    saveCloudBtn.addEventListener('click', function() {
      saveCloudBtn.disabled = true;
      saveCloudBtn.textContent = '保存中…';
      var v = (cloudInput.value || '').trim().replace(/\/$/, '');
      if (!v) {
        showMsg(cloudMsg, '请填写 API Base', true);
        showToast('保存失败', '请填写 API Base', true);
        saveCloudBtn.disabled = false;
        saveCloudBtn.textContent = '保存';
        return;
      }
      localStorage.setItem('lobster_messenger_api_base', v);
      window.__MESSENGER_API_BASE = v;
      try { MESSENGER_API_BASE = v; } catch (e1) {}
      showMsg(cloudMsg, '已保存，刷新列表', false);
      showToast('保存成功', '配置已保存', false);
      Promise.resolve()
        .then(function() { return loadList(); })
        .catch(function(e) {
          var errText = (e && e.message) ? e.message : '保存失败';
          showMsg(cloudMsg, errText, true);
          showToast('保存失败', errText, true);
        })
        .finally(function() {
          saveCloudBtn.disabled = false;
          saveCloudBtn.textContent = '保存';
        });
    });
  }

  if (backBtn) backBtn.addEventListener('click', function() {
    var chatNav = document.querySelector('.nav-left-item[data-view="chat"]');
    if (chatNav) chatNav.click();
  });
  if (addBtn) addBtn.addEventListener('click', openAdd);
  if (checklistBtn) checklistBtn.addEventListener('click', copyChecklist);
  if (modalCancelBtn && modal) modalCancelBtn.addEventListener('click', function() { modal.classList.remove('visible'); });
  if (modalSaveBtn) modalSaveBtn.addEventListener('click', saveModal);

  window.loadMessengerConfigPage = function() {
    loadMessengerCloudInput();
    loadList();
  };

  loadMessengerCloudInput();
})();
