/**
 * Twilio WhatsApp：配置弹窗走本机 lobster_online（LOCAL_API_BASE 同源）。
 * 调试可用 ?twilio_api= 或 localStorage.lobster_twilio_api_base。
 */
(function() {
  function localApiBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? LOCAL_API_BASE : '') || '';
  }

  /** 与企微 localApiBase() 一致：优先调试覆盖，否则本机根（可为空→相对 URL） */
  function twilioConfigApiBase() {
    var ov = (typeof window.__TWILIO_API_BASE !== 'undefined' && window.__TWILIO_API_BASE)
      ? String(window.__TWILIO_API_BASE).replace(/\/$/, '').trim()
      : '';
    if (!ov) ov = (localStorage.getItem('lobster_twilio_api_base') || '').trim().replace(/\/$/, '');
    if (ov) return ov;
    return localApiBase().replace(/\/$/, '');
  }

  function twilioApiUrl(path) {
    var base = twilioConfigApiBase();
    return (base ? base.replace(/\/$/, '') : '') + path;
  }

  function showMsg(el, text, isErr) {
    if (!el) return;
    el.textContent = text || '';
    el.className = 'msg' + (isErr ? ' err' : '');
    el.style.display = text ? 'block' : 'none';
  }

  function fillSuggested(d) {
    var pre = document.getElementById('twilioWebhookSuggestedPre');
    var pathLbl = document.getElementById('twilioInboundPathLabel');
    if (pathLbl && d && d.inbound_path) pathLbl.textContent = d.inbound_path;
    if (pre && d) {
      var s = (d.webhook_suggested || '').trim();
      pre.textContent = s || '（服务器未配置 PUBLIC_BASE_URL / TWILIO_WHATSAPP_WEBHOOK_FULL_URL，无法预览）';
    }
  }

  window.loadTwilioWhatsappConfigPage = function() {
    var sidIn = document.getElementById('twilioAccountSidInput');
    var tokIn = document.getElementById('twilioAuthTokenInput');
    var saveMsg = document.getElementById('twilioWhatsappSaveMsg');
    showMsg(saveMsg, '', false);
    fetch(twilioApiUrl('/api/twilio-whatsapp/config'), { headers: typeof authHeaders === 'function' ? authHeaders() : {} })
      .then(function(r) {
        return r.text().then(function(text) {
          var d = {};
          try { d = text ? JSON.parse(text) : {}; } catch (e1) { d = { detail: text ? text.slice(0, 200) : ('HTTP ' + r.status) }; }
          return { ok: r.ok, d: d, status: r.status };
        });
      })
      .then(function(x) {
        if (!x.ok) {
          showMsg(saveMsg, (x.d && x.d.detail) ? x.d.detail : ('加载失败 HTTP ' + (x.status || '')));
          return;
        }
        var d = x.d || {};
        fillSuggested(d);
        if (sidIn) {
          sidIn.value = '';
          sidIn.placeholder = d.has_account_sid ? '已保存 · 修改请填入完整 Account SID' : 'ACxxxxxxxx…';
        }
        if (tokIn) tokIn.value = '';
      })
      .catch(function(err) {
        fillSuggested({});
        showMsg(saveMsg, '无法连接本机 Twilio 接口：' + ((err && err.message) ? err.message : '请确认已用 ./start_online.sh 启动本机 backend（同源 /api）'), true);
      });
  };

  var cfgModal = document.getElementById('twilioWhatsappConfigModal');
  function closeTwilioConfigModal() {
    if (cfgModal) cfgModal.classList.remove('visible');
    if (location.hash === '#twilio-whatsapp-config') {
      try { history.replaceState(null, '', location.pathname + location.search); } catch (e2) {}
    }
  }
  var cfgCloseBtn = document.getElementById('twilioWhatsappConfigModalClose');
  if (cfgCloseBtn) cfgCloseBtn.addEventListener('click', closeTwilioConfigModal);
  if (cfgModal) {
    cfgModal.addEventListener('click', function(e) {
      if (e.target === cfgModal) closeTwilioConfigModal();
    });
  }

  var copyBtn = document.getElementById('twilioCopyWebhookBtn');
  if (copyBtn) {
    copyBtn.addEventListener('click', function() {
      var pre = document.getElementById('twilioWebhookSuggestedPre');
      var t = pre ? pre.textContent.trim() : '';
      if (!t || t.indexOf('http') !== 0) {
        alert('当前无可用 Webhook 预览：请在承接 Twilio 的那台服务器 .env 中配置 PUBLIC_BASE_URL 或 TWILIO_WHATSAPP_WEBHOOK_FULL_URL');
        return;
      }
      if (typeof copyToClipboard === 'function') {
        copyToClipboard(t, function() { copyBtn.textContent = '已复制'; setTimeout(function() { copyBtn.textContent = '复制'; }, 1500); });
      }
    });
  }

  var saveBtn = document.getElementById('twilioWhatsappSaveBtn');
  if (saveBtn) {
    saveBtn.addEventListener('click', function() {
      var saveMsg = document.getElementById('twilioWhatsappSaveMsg');
      var sidIn = document.getElementById('twilioAccountSidInput');
      var tokIn = document.getElementById('twilioAuthTokenInput');
      var body = {};
      if (sidIn && sidIn.value.trim()) body.account_sid = sidIn.value.trim();
      if (tokIn && tokIn.value.trim()) body.auth_token = tokIn.value.trim();
      saveBtn.disabled = true; saveBtn.textContent = '保存中…';
      fetch(twilioApiUrl('/api/twilio-whatsapp/config'), {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : {}),
        body: JSON.stringify(body)
      })
        .then(function(r) {
          return r.text().then(function(text) {
            var d = {};
            try { d = text ? JSON.parse(text) : {}; } catch (e1) { d = { detail: text ? text.slice(0, 200) : ('HTTP ' + r.status) }; }
            return { ok: r.ok, d: d, status: r.status };
          });
        })
        .then(function(x) {
          if (x.ok) {
            showMsg(saveMsg, (x.d && x.d.message) || '已保存', false);
            window.loadTwilioWhatsappConfigPage();
          } else {
            showMsg(saveMsg, (x.d && x.d.detail) || ('保存失败 HTTP ' + (x.status || '')), true);
          }
        })
        .catch(function(err) {
          showMsg(saveMsg, '无法保存：' + ((err && err.message) ? err.message : '请确认本机 backend 已启动'), true);
        })
        .finally(function() { saveBtn.disabled = false; saveBtn.textContent = '保存'; });
    });
  }
})();
