/** 在线版：独立认证时显示手机号验证码注册和登录；/api/edition 可覆盖。 */
var USE_INDEPENDENT_AUTH = true;

(function bindDesktopContextMenu() {
  if (window.__bihuoDesktopContextMenuInstalled) return;
  window.__bihuoDesktopContextMenuInstalled = true;

  function isDesktopWindow() {
    try {
      if (window.pywebview && window.pywebview.api) return true;
      if (new URLSearchParams(window.location.search || '').get('desktop') === '1') return true;
      return /^(127\.0\.0\.1|localhost|\[::1\])$/i.test(window.location.hostname || '');
    } catch (e) {
      return false;
    }
  }

  function isEditable(el) {
    if (!el) return false;
    var node = el.nodeType === 1 ? el : el.parentElement;
    if (!node) return false;
    return !!(node.closest && node.closest('textarea,input,[contenteditable="true"],[contenteditable="plaintext-only"]'));
  }

  function editableTarget(el) {
    var node = el && el.nodeType === 1 ? el : (el ? el.parentElement : null);
    return node && node.closest ? node.closest('textarea,input,[contenteditable="true"],[contenteditable="plaintext-only"]') : null;
  }

  function hasSelection() {
    try {
      var sel = window.getSelection && window.getSelection();
      return !!(sel && !sel.isCollapsed && String(sel.toString() || '').length);
    } catch (e) {
      return false;
    }
  }

  function hasEditableSelection(target) {
    if (!target) return false;
    if (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT') {
      return Number(target.selectionEnd || 0) > Number(target.selectionStart || 0);
    }
    return hasSelection();
  }

  function copyTextFallback(text) {
    var ta = document.createElement('textarea');
    ta.value = text || '';
    ta.setAttribute('readonly', 'readonly');
    ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) {}
    ta.remove();
    return ok;
  }

  function textFromContextElement(el) {
    var node = el && el.nodeType === 1 ? el : (el ? el.parentElement : null);
    if (!node || !node.closest) return '';
    var copyNode = node.closest('[data-copy-text],.chat-msg-body,.chat-msg-text,.openclaw-skill-chat-bubble,.ip-content-draft-body,pre,code');
    if (!copyNode) {
      var msg = node.closest('.chat-msg');
      if (msg) copyNode = msg.querySelector('.chat-msg-body') || msg;
    }
    if (!copyNode) return '';
    var dataText = copyNode.getAttribute && copyNode.getAttribute('data-copy-text');
    return String(dataText || copyNode.innerText || copyNode.textContent || '').trim();
  }

  function copySelection(target, fallbackText) {
    var text = '';
    if (target && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT')) {
      var start = Number(target.selectionStart || 0);
      var end = Number(target.selectionEnd || 0);
      text = String(target.value || '').slice(start, end);
    } else {
      try {
        var sel = window.getSelection && window.getSelection();
        text = sel ? String(sel.toString() || '') : '';
      } catch (e) {}
    }
    if (!text && fallbackText) text = String(fallbackText || '');
    if (!text) return Promise.resolve(false);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function() { return true; }).catch(function() {
        return copyTextFallback(text);
      });
    }
    return Promise.resolve(copyTextFallback(text));
  }

  function command(name) {
    try {
      return document.execCommand(name);
    } catch (e) {
      return false;
    }
  }

  function selectAll(target) {
    if (target && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT')) {
      target.focus();
      target.select();
      return true;
    }
    command('selectAll');
    return true;
  }

  function createMenu() {
    var menu = document.createElement('div');
    menu.id = 'desktopContextMenu';
    menu.className = 'desktop-context-menu';
    menu.hidden = true;
    menu.innerHTML = ''
      + '<button type="button" data-cmd="copy"><span>复制</span><kbd>Ctrl+C</kbd></button>'
      + '<button type="button" data-cmd="cut"><span>剪切</span><kbd>Ctrl+X</kbd></button>'
      + '<button type="button" data-cmd="paste"><span>粘贴</span><kbd>Ctrl+V</kbd></button>'
      + '<div class="desktop-context-menu-divider"></div>'
      + '<button type="button" data-cmd="selectAll"><span>全选</span><kbd>Ctrl+A</kbd></button>';
    document.body.appendChild(menu);
    return menu;
  }

  var menu = null;
  var activeTarget = null;
  var activeFallbackText = '';

  function hideMenu() {
    if (menu) menu.hidden = true;
  }

  function placeMenu(x, y) {
    if (!menu) return;
    menu.hidden = false;
    menu.style.left = '0px';
    menu.style.top = '0px';
    var rect = menu.getBoundingClientRect();
    var left = Math.min(Math.max(8, x), Math.max(8, window.innerWidth - rect.width - 8));
    var top = Math.min(Math.max(8, y), Math.max(8, window.innerHeight - rect.height - 8));
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
  }

  function updateMenuState(target, fallbackText) {
    if (!menu) return;
    var editable = !!target;
    var selected = editable ? hasEditableSelection(target) : hasSelection();
    var copyBtn = menu.querySelector('[data-cmd="copy"]');
    var cutBtn = menu.querySelector('[data-cmd="cut"]');
    var pasteBtn = menu.querySelector('[data-cmd="paste"]');
    var allBtn = menu.querySelector('[data-cmd="selectAll"]');
    if (copyBtn) copyBtn.disabled = !selected && !fallbackText;
    if (cutBtn) cutBtn.disabled = !editable || !selected;
    if (pasteBtn) pasteBtn.disabled = !editable;
    if (allBtn) allBtn.disabled = false;
  }

  function bind() {
    if (!isDesktopWindow()) return;
    menu = createMenu();
    document.addEventListener('contextmenu', function(e) {
      if (e.target && e.target.closest && e.target.closest('.kf-customer-item')) return;
      activeTarget = editableTarget(e.target);
      activeFallbackText = activeTarget ? '' : textFromContextElement(e.target);
      e.preventDefault();
      e.stopPropagation();
      updateMenuState(activeTarget, activeFallbackText);
      placeMenu(e.clientX || 8, e.clientY || 8);
    }, true);
    document.addEventListener('click', function(e) {
      if (menu && !menu.hidden && !(e.target && e.target.closest && e.target.closest('#desktopContextMenu'))) {
        hideMenu();
      }
    }, true);
    document.addEventListener('scroll', hideMenu, true);
    window.addEventListener('resize', hideMenu);
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') hideMenu();
    });
    menu.addEventListener('click', function(e) {
      var btn = e.target && e.target.closest ? e.target.closest('button[data-cmd]') : null;
      if (!btn || btn.disabled) return;
      var cmd = btn.getAttribute('data-cmd');
      var target = activeTarget;
      hideMenu();
      if (target && typeof target.focus === 'function') target.focus();
      if (cmd === 'copy') {
        copySelection(target, activeFallbackText);
      } else if (cmd === 'cut') {
        if (!command('cut')) {
          copySelection(target, activeFallbackText).then(function(ok) {
            if (!ok || !target || !(target.tagName === 'TEXTAREA' || target.tagName === 'INPUT')) return;
            var start = Number(target.selectionStart || 0);
            var end = Number(target.selectionEnd || 0);
            target.setRangeText('', start, end, 'start');
            target.dispatchEvent(new Event('input', { bubbles: true }));
          });
        }
      } else if (cmd === 'paste') {
        if (!command('paste') && navigator.clipboard && navigator.clipboard.readText) {
          navigator.clipboard.readText().then(function(text) {
            if (!target || !(target.tagName === 'TEXTAREA' || target.tagName === 'INPUT')) return;
            var start = Number(target.selectionStart || 0);
            var end = Number(target.selectionEnd || 0);
            target.setRangeText(text || '', start, end, 'end');
            target.dispatchEvent(new Event('input', { bubbles: true }));
          }).catch(function() {});
        }
      } else if (cmd === 'selectAll') {
        selectAll(target);
      }
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();

(function bindBihuoCloseGuard() {
  if (window.__bihuoCloseGuardInstalled) return;
  window.__bihuoCloseGuardInstalled = true;
  window.__bihuoAllowClose = false;
  function isDesktopWindow() {
    try {
      if (window.pywebview && window.pywebview.api) return true;
      return new URLSearchParams(window.location.search || '').get('desktop') === '1';
    } catch (e) {
      return false;
    }
  }
  window.__bihuoConfirmAppClose = function() {
    if (window.__bihuoAllowClose) return true;
    return window.confirm('\u786e\u5b9a\u8981\u5173\u95ed\u5fc5\u706b\u667a\u80fd\u5417\uff1f\n\n\u5982\u679c\u6b63\u5728\u5168\u5c4f\u9884\u89c8\u89c6\u9891\uff0c\u53ef\u4ee5\u5148\u70b9\u51fb\u300c\u9000\u51fa\u5168\u5c4f\u300d\u6216\u6309 Esc\u3002');
  };
})();

(function bindDesktopHardRefresh() {
  function isDesktopWindow() {
    try {
      return new URLSearchParams(window.location.search || '').get('desktop') === '1';
    } catch (e) {
      return false;
    }
  }
  function nextDesktopUrl() {
    try {
      var u = new URL(window.location.href);
      u.searchParams.set('desktop', '1');
      u.searchParams.set('v', Date.now() + '-' + Math.random().toString(16).slice(2, 8));
      return u.toString();
    } catch (e) {
      var sep = window.location.href.indexOf('?') >= 0 ? '&' : '?';
      return window.location.href + sep + 'desktop=1&v=' + Date.now();
    }
  }
  function bind() {
    var btn = document.getElementById('desktopHardRefreshBtn');
    if (!btn) return;
    if (!isDesktopWindow()) {
      btn.style.display = 'none';
      return;
    }
    btn.style.display = '';
    btn.onclick = function() {
      window.__bihuoAllowClose = true;
      btn.disabled = true;
      btn.textContent = '刷新中...';
      window.location.replace(nextDesktopUrl());
    };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();

/**
 * 认证中心注册接口在「在线版 + 安装槽位」下强制要求合法 X-Installation-Id。
 * 若 app.js 未加载、脚本顺序异常或 localStorage 曾异常，避免发空请求头导致「请使用最新客户端」。
 * 与 static/js/app.js 中 getOrCreateInstallationId 逻辑一致。
 */
(function ensureInstallationIdFn() {
  if (typeof window.getOrCreateInstallationId === 'function') return;
  window.getOrCreateInstallationId = function() {
    var k = 'lobster_installation_id';
    var v = '';
    try { v = localStorage.getItem(k) || ''; } catch (e) {}
    if (v && v.length >= 8) return v;
    var u = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID().replace(/-/g, '')
      : (Date.now().toString(36) + Math.random().toString(36).slice(2, 18));
    try { localStorage.setItem(k, u); } catch (e2) {}
    return u;
  };
})();

/** 本机后端 /api/branding 与 .env LOBSTER_BRAND_MARK、static/branding/brands.json 一致 */
function applyBrandingFromApi() {
  function setHeroSubtitle(el, value) {
    if (!el) return;
    var lines = String(value == null ? '' : value).split(/\r?\n/);
    el.textContent = '';
    lines.forEach(function(line) {
      var span = document.createElement('span');
      span.className = 'hero-subtitle-line';
      span.textContent = line;
      el.appendChild(span);
    });
  }
  var base = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  if (!base) return;
  fetch(base + '/api/branding', { credentials: 'same-origin' })
    .then(function(r) {
      if (!r.ok) return Promise.reject(new Error('branding ' + r.status));
      return r.json();
    })
    .then(function(b) {
      if (!b || typeof b !== 'object') return;
      if (b.mark) window.__LOBSTER_BRAND_MARK = b.mark;
      if (b.parent_account) window.__LOBSTER_PARENT_ACCOUNT = b.parent_account;
      window.__LOBSTER_IS_OVERSEAS_USER = !!b.is_overseas_user;
      if (b.document_title) document.title = b.document_title;
      var icons = b.icons || {};
      var fav = document.getElementById('brandFavicon');
      if (fav && icons.favicon_32) fav.setAttribute('href', icons.favicon_32);
      var apt = document.getElementById('brandAppleTouch');
      if (apt && icons.apple_touch) apt.setAttribute('href', icons.apple_touch);
      var markImg = document.getElementById('brandLogoMark');
      if (markImg && icons.logo_mark) {
        markImg.src = icons.logo_mark;
        if (icons.logo_mark_width) markImg.width = Number(icons.logo_mark_width);
        if (icons.logo_mark_height) markImg.height = Number(icons.logo_mark_height);
      }
      var primary = document.getElementById('brandLogoPrimary');
      var accent = document.getElementById('brandLogoAccent');
      if (primary && b.logo_primary != null) primary.textContent = b.logo_primary;
      if (accent && b.logo_accent != null) accent.textContent = b.logo_accent;
      var heroH = document.getElementById('brandHeroTitle');
      var heroP = document.getElementById('brandHeroSubtitle');
      if (heroH && b.hero_title != null) heroH.textContent = b.hero_title;
      if (heroP && b.hero_subtitle != null) setHeroSubtitle(heroP, b.hero_subtitle);
    })
    .catch(function(e) {
      if (typeof console !== 'undefined') console.warn('[branding]', e);
    });
}
var USE_FUIOU_PAY = false;
var _fuiouPollTimer = null;
function _startFuiouPoll(outTradeNo, orderDate) {
  if (_fuiouPollTimer) clearInterval(_fuiouPollTimer);
  var attempts = 0, maxAttempts = 90;
  _fuiouPollTimer = setInterval(function() {
    attempts++;
    if (attempts > maxAttempts) { clearInterval(_fuiouPollTimer); _fuiouPollTimer = null; var el = document.getElementById('fuiouPollStatus'); if (el) el.textContent = '查询超时，请刷新页面查看是否到账'; return; }
    var url = API_BASE + '/api/recharge/fuiou-query?out_trade_no=' + encodeURIComponent(outTradeNo);
    if (orderDate) url += '&order_date=' + encodeURIComponent(orderDate);
    fetch(url, { headers: authHeaders() })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d && d.status === 'paid') {
          clearInterval(_fuiouPollTimer); _fuiouPollTimer = null;
          var el = document.getElementById('fuiouPollStatus');
          if (el) { el.style.color = '#27ae60'; el.textContent = '支付成功！已到账 ' + (d.credits || '') + ' 算力'; }
          if (typeof loadSutuiBalance === 'function') loadSutuiBalance();
          var balanceEl = document.getElementById('billingBalance');
          if (balanceEl) fetch(API_BASE + '/auth/me', { headers: authHeaders() }).then(function(r) { return r.json(); }).then(function(me) { balanceEl.textContent = '我的算力：' + (me && me.credits != null ? me.credits : '--'); });
        }
      }).catch(function() {});
  }, 3000);
}

/** 充值/套餐：单价（元），支持 price_yuan 或 price_fen */
function loadLoginCaptcha() {
  var img = document.getElementById('loginCaptchaImg');
  var msgEl = document.getElementById('loginMsg');
  fetch(API_BASE + '/auth/captcha').then(function(r) {
    return r.json().then(function(d) { return { ok: r.ok, data: d }; });
  }).then(function(x) {
    var d = x.data || {};
    if (!x.ok || !d.captcha_id || !d.image) {
      if (img) { img.alt = '验证码加载失败'; img.style.background = 'rgba(244,67,54,0.2)'; img.removeAttribute('src'); }
      if (msgEl) showMsg(msgEl, '验证码加载失败：无法从认证服务获取（' + (API_BASE || '') + '）。请确认云端 API 已启动并检查网络后刷新。', true);
      return;
    }
    if (img) { img.style.background = '#f5f5f5'; img.alt = '验证码'; img.src = d.image; }
    var hid = document.getElementById('loginCaptchaId');
    var ans = document.getElementById('loginCaptchaAnswer');
    if (hid) hid.value = d.captcha_id;
    if (ans) ans.value = '';
  }).catch(function(e) {
    if (img) { img.alt = '验证码加载失败'; img.style.background = 'rgba(244,67,54,0.2)'; img.removeAttribute('src'); }
    if (msgEl) showMsg(msgEl, '验证码请求失败（网络或认证服务不可用）。认证服务：' + (API_BASE || '') + '。请打开开发者工具 Network 查看。', true);
    console.warn('[login captcha]', e);
  });
}
function loadRegisterCaptcha() {
  var img = document.getElementById('registerCaptchaImg');
  var msgEl = document.getElementById('registerMsg');
  fetch(API_BASE + '/auth/captcha').then(function(r) {
    return r.json().then(function(d) { return { ok: r.ok, data: d }; });
  }).then(function(x) {
    var d = x.data || {};
    if (!x.ok || !d.captcha_id || !d.image) {
      if (img) { img.alt = '验证码加载失败'; img.style.background = 'rgba(244,67,54,0.2)'; img.removeAttribute('src'); }
      if (msgEl) showMsg(msgEl, '验证码加载失败：无法从认证服务获取（' + (API_BASE || '') + '）。请检查网络或 API 地址。', true);
      return;
    }
    if (img) { img.style.background = '#f5f5f5'; img.alt = '验证码'; img.src = d.image; }
    var hid = document.getElementById('registerCaptchaId');
    var ans = document.getElementById('registerCaptchaAnswer');
    if (hid) hid.value = d.captcha_id;
    if (ans) ans.value = '';
  }).catch(function(e) {
    if (img) { img.alt = '验证码加载失败'; img.style.background = 'rgba(244,67,54,0.2)'; img.removeAttribute('src'); }
    if (msgEl) showMsg(msgEl, '验证码请求失败（网络或认证服务不可用）。认证服务：' + (API_BASE || '') + '。', true);
    console.warn('[register captcha]', e);
  });
}

function applyEditionLoginUI() {
  var registerView = document.getElementById('authRegisterView');
  var ownWechatBlock = document.getElementById('ownWechatLoginBlock');
  /** 仅 online 且关闭独立认证时走服务号扫码；其余走手机号验证码注册和登录。 */
  if (EDITION === 'online' && !USE_INDEPENDENT_AUTH) {
    if (registerView) registerView.style.display = 'none';
    if (ownWechatBlock) {
      ownWechatBlock.style.display = 'block';
      startOwnWechatLogin();
    }
  } else {
    if (ownWechatBlock) ownWechatBlock.style.display = 'none';
    if (registerView) registerView.style.display = '';
    var regImg = document.getElementById('registerCaptchaImg');
    var regRefresh = document.getElementById('registerCaptchaRefresh');
    if (regImg) regImg.onclick = function() { loadRegisterCaptcha(); };
    if (regRefresh) regRefresh.onclick = function(e) { e.preventDefault(); loadRegisterCaptcha(); };
    loadRegisterCaptcha();
  }
}
(function fetchEdition() {
  function setClientVersionLabel(semver, build, appliedAt) {
    var el = document.getElementById('clientVersionLabel');
    var cacheBust = [semver || '', build == null ? '' : build, appliedAt || ''].join('-').replace(/[^a-zA-Z0-9_.-]/g, '_');
    if (cacheBust.replace(/[_-]/g, '')) window.__LOBSTER_STATIC_CACHE_BUST = cacheBust;
    if (!el) return;
    var v = (semver && String(semver).trim()) ? String(semver).trim().replace(/^v/i, '') : '';
    var b = (build === null || build === undefined) ? NaN : Number(build);
    var parts = [];
    if (v) parts.push('v' + v);
    if (!isNaN(b)) parts.push('build ' + b);
    if (appliedAt) parts.push(String(appliedAt));
    el.textContent = parts.length ? parts.join(' · ') : '';
  }
  function tryStaticClientVersionIfEmpty() {
    var el = document.getElementById('clientVersionLabel');
    if (!el || (el.textContent && String(el.textContent).trim())) return Promise.resolve();
    return fetch('/static/client_version.json').then(function(r) {
      return r.ok ? r.json() : null;
    }).then(function(j) {
      if (!j) return;
      if (j.build == null && !j.version) return;
      setClientVersionLabel(j.version || '1.0.0', j.build, j.applied_at || null);
    }).catch(function() {});
  }
  function applyEditionPayload(d) {
    d = d || {};
    EDITION = (d.edition) || 'online';
    USE_INDEPENDENT_AUTH = !!d.use_independent_auth;
    USE_FUIOU_PAY = !!(d.use_fuiou_pay);
    ALLOW_SELF_CONFIG_MODEL = d.allow_self_config_model !== false;
    RECHARGE_URL = (d.recharge_url && d.recharge_url.trim()) ? d.recharge_url.trim() : null;
    if (typeof d.client_code_build === 'number' || (d.client_code_version && String(d.client_code_version).trim())) {
      setClientVersionLabel(
        d.client_code_version || '1.0.0',
        typeof d.client_code_build === 'number' ? d.client_code_build : null,
        d.client_code_applied_at || null
      );
    }
    applyEditionLoginUI();
    if (typeof updateSutuiSubSelectVisibility === 'function') updateSutuiSubSelectVisibility();
  }
  function fetchFrom(base) {
    var b = (base || '').replace(/\/$/, '');
    if (!b) return Promise.reject(new Error('no base'));
    return fetch(b + '/api/edition').then(function(r) {
      return r.ok ? r.json() : Promise.reject(new Error('bad status'));
    });
  }
  var localBase = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).trim() : '';
  function supplementFuiouPayFromApiBase() {
    if (typeof EDITION === 'undefined' || EDITION !== 'online' || !USE_INDEPENDENT_AUTH || USE_FUIOU_PAY) return Promise.resolve();
    var apiB = (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
    if (!apiB) return Promise.resolve();
    return fetchFrom(apiB).then(function(remote) {
      if (remote && remote.use_fuiou_pay) USE_FUIOU_PAY = true;
    }).catch(function() {});
  }
  function chainAfterEdition(p) {
    return p.then(supplementFuiouPayFromApiBase).then(tryStaticClientVersionIfEmpty);
  }
  if (localBase) {
    chainAfterEdition(fetchFrom(localBase).then(applyEditionPayload)).catch(function() {
      chainAfterEdition(fetchFrom(API_BASE).then(applyEditionPayload)).catch(function() {
        applyEditionLoginUI();
        return tryStaticClientVersionIfEmpty();
      });
    });
  } else {
    chainAfterEdition(fetchFrom(API_BASE).then(applyEditionPayload)).catch(function() {
      applyEditionLoginUI();
      return tryStaticClientVersionIfEmpty();
    });
  }
})();

function startOwnWechatLogin() {
  var img = document.getElementById('ownWechatQrImg');
  var status = document.getElementById('ownWechatQrStatus');
  var link = document.getElementById('ownWechatLink');
  var qrWrap = document.getElementById('ownWechatQrWrap');
  var mpWrap = document.getElementById('ownWechatMiniprogramWrap');
  if (!status) return;
  status.textContent = '正在获取…';
  if (img) img.style.display = 'none';
  if (link) link.style.display = 'none';
  if (mpWrap) mpWrap.style.display = 'none';
  if (qrWrap) qrWrap.style.display = 'block';
  fetch(API_BASE + '/auth/wechat-login-url').then(function(r) {
    return r.json().then(function(d) {
      if (!r.ok) {
        var msg = (d && (d.detail || d.msg)) || ('请求失败 ' + r.status);
        status.textContent = (typeof msg === 'string' ? msg : (Array.isArray(msg) ? msg[0] : '获取失败'));
        return;
      }
      var url = (d && (d.login_url || (d.data && d.data.login_url))) || '';
      if (!url) {
        console.warn('[wechat-login-url] 200 但无 login_url，响应:', d);
        status.textContent = '服务器未返回登录链接';
        return;
      }
      var titleEl = document.getElementById('ownWechatLoginTitle');
      if (titleEl) titleEl.textContent = '请使用微信扫描下方二维码登录（服务号）';
      status.textContent = '请使用微信扫码登录';
      if (img) {
        img.src = 'https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=' + encodeURIComponent(url);
        img.style.display = 'inline';
      }
      if (link) {
        link.href = url;
        link.style.display = 'inline-block';
        link.textContent = '打开微信扫码登录';
      }
    });
  }).catch(function(e) {
    status.textContent = '网络错误或接口异常，请检查 API 地址（' + (API_BASE || '') + '）或稍后重试';
    console.warn('[wechat-login-url]', e);
  });
}

/** OAuth / 登录后同步到本机后端：写入 openclaw/.channel_fallback.json，供本机 OpenClaw（127.0.0.1:8000）读。
 * 须打 LOCAL_API_BASE，不能打 API_BASE：本机页默认 API_BASE 为公网认证域名，写文件会落在远端，本机永远 401。 */
function persistOpenclawChannelFallback(tok) {
  var t = (tok != null && tok !== '') ? String(tok) : (typeof token !== 'undefined' ? token : '');
  var localBase = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  if (!t || !localBase) {
    if (typeof console !== 'undefined' && console.debug) {
      console.debug('[openclaw-channel-fallback] 跳过：无 token 或未配置 LOCAL_API_BASE（本机须运行 backend 且 localhost 打开页面或设 lobster_local_api_base）');
    }
    return;
  }
  fetch(localBase + '/auth/persist-openclaw-channel-fallback', {
    method: 'POST',
    headers: {
      'Authorization': 'Bearer ' + t,
      'X-Installation-Id': typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : ''
    }
  }).catch(function() {});
}

(function applyTokenFromUrl() {
  var m = /[?&]token=([^&]+)/.exec(window.location.search || '');
  if (!m || !m[1]) return;
  var t = decodeURIComponent(m[1]);
  token = t;
  localStorage.setItem('token', t);
  persistOpenclawChannelFallback(t);
  if (window.opener) {
    try { window.opener.postMessage({ type: 'auth_login_ok', token: t }, '*'); } catch (e) {}
    window.close();
  } else {
    setTimeout(function() { loadDashboard(); }, 0);
  }
})();

window.addEventListener('message', function(e) {
  if (e.data && e.data.type === 'auth_login_ok' && e.data.token) {
    token = e.data.token;
    localStorage.setItem('token', token);
    persistOpenclawChannelFallback(token);
    loadDashboard();
  }
});

function validateCnPhone(raw) {
  var d = String(raw || '').replace(/\D/g, '');
  if (!d) return null;
  return /^1[3-9]\d{9}$/.test(d) ? d : null;
}

function setAuthLoginTab(tabName) {
  var name = tabName === 'password' ? 'password' : 'sms';
  document.querySelectorAll('[data-auth-tab]').forEach(function(btn) {
    var active = btn.getAttribute('data-auth-tab') === name;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('[data-auth-panel]').forEach(function(panel) {
    panel.hidden = panel.getAttribute('data-auth-panel') !== name;
  });
}

(function bindAuthLoginTabs() {
  document.querySelectorAll('[data-auth-tab]').forEach(function(btn) {
    if (btn._authTabBound) return;
    btn._authTabBound = true;
    btn.addEventListener('click', function() {
      setAuthLoginTab(btn.getAttribute('data-auth-tab'));
    });
  });
  setAuthLoginTab('sms');
})();

var loginForm = document.getElementById('loginForm');
if (loginForm) {
  loginForm.addEventListener('submit', function(e) {
    e.preventDefault();
    var account = (((document.getElementById('loginAccount') || {}).value) || '').trim();
    var password = ((document.getElementById('loginPassword') || {}).value || '');
    var msgEl = document.getElementById('loginMsg');
    if (!account) { showMsg(msgEl, '请输入账号或手机号', true); return; }
    if (!password) { showMsg(msgEl, '请输入密码', true); return; }
    fetch(API_BASE + '/auth/login-phone-password', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Installation-Id': typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : ''
      },
      body: JSON.stringify({ account: account, password: password })
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (x.ok) {
          token = x.data.access_token;
          localStorage.setItem('token', token);
          if (typeof persistOpenclawChannelFallback === 'function') persistOpenclawChannelFallback(token);
          showMsg(msgEl, '登录成功', false);
          loadDashboard();
        } else {
          showMsg(msgEl, normalizeAuthErrorDetail(x.data.detail) || '登录失败', true);
        }
      })
      .catch(function() { showMsg(msgEl, '网络错误', true); });
  });
}

var _registerSmsCooldownTimer = null;
function setRegisterSmsButtonCooldown(sec) {
  var btn = document.getElementById('registerSendSmsBtn');
  if (!btn) return;
  if (_registerSmsCooldownTimer) {
    clearInterval(_registerSmsCooldownTimer);
    _registerSmsCooldownTimer = null;
  }
  if (sec <= 0) {
    btn.disabled = false;
    btn.textContent = '获取短信验证码';
    return;
  }
  var left = sec;
  btn.disabled = true;
  function tick() {
    btn.textContent = left > 0 ? ('已发送（' + left + 's）') : '获取短信验证码';
    if (left <= 0) {
      btn.disabled = false;
      if (_registerSmsCooldownTimer) clearInterval(_registerSmsCooldownTimer);
      _registerSmsCooldownTimer = null;
      return;
    }
    left -= 1;
  }
  tick();
  _registerSmsCooldownTimer = setInterval(tick, 1000);
}
function normalizeAuthErrorDetail(detail) {
  if (detail == null) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    var parts = detail.map(function(item) {
      if (item == null) return '';
      if (typeof item === 'string') return item;
      if (typeof item === 'object') return item.msg || item.message || item.detail || JSON.stringify(item);
      return String(item);
    }).filter(Boolean);
    return parts.join('；');
  }
  if (typeof detail === 'object') {
    return detail.msg || detail.message || detail.detail || JSON.stringify(detail);
  }
  return String(detail);
}
(function bindRegisterSmsButton() {
  var btn = document.getElementById('registerSendSmsBtn');
  if (!btn || btn._smsBound) return;
  btn._smsBound = true;
  btn.addEventListener('click', function() {
    var msgEl = document.getElementById('registerMsg');
    var phone = validateCnPhone((document.getElementById('registerPhone') || {}).value);
    var captchaId = (document.getElementById('registerCaptchaId') || {}).value || '';
    var captchaAnswer = (document.getElementById('registerCaptchaAnswer') || {}).value || '';
    if (!phone) { showMsg(msgEl, '请输入有效的 11 位手机号', true); return; }
    if (!captchaAnswer) { showMsg(msgEl, '请先填写图形验证码', true); return; }
    btn.disabled = true;
    fetch(API_BASE + '/auth/sms/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: phone, captcha_id: captchaId, captcha_answer: captchaAnswer })
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d, status: r.status }; }); })
      .then(function(x) {
        if (x.ok) {
          showMsg(msgEl, '短信已发送，请查收', false);
          setRegisterSmsButtonCooldown(60);
          if (typeof loadRegisterCaptcha === 'function') loadRegisterCaptcha();
        } else {
          var det = normalizeAuthErrorDetail(x.data.detail);
          showMsg(msgEl, det || ('发送失败 (' + x.status + ')'), true);
          btn.disabled = false;
          if (typeof loadRegisterCaptcha === 'function') loadRegisterCaptcha();
        }
      })
      .catch(function() {
        showMsg(msgEl, '网络错误', true);
        btn.disabled = false;
      });
  });
})();

var registerForm = document.getElementById('registerForm');
if (registerForm) {
  registerForm.addEventListener('submit', function(e) {
    e.preventDefault();
    var phone = validateCnPhone((document.getElementById('registerPhone') || {}).value);
    var smsCode = ((document.getElementById('registerSmsCode') || {}).value || '').trim();
    var msgEl = document.getElementById('registerMsg');
    if (!phone) { showMsg(msgEl, '请输入有效的 11 位手机号', true); return; }
    if (!smsCode) { showMsg(msgEl, '请填写短信验证码', true); return; }

    function postRegisterPhone(brandMark, parentAccount) {
      var payload = { phone: phone, code: smsCode };
      var isOverseasUser = !!window.__LOBSTER_IS_OVERSEAS_USER;
      if (brandMark) payload.brand_mark = brandMark;
      if (parentAccount) payload.parent_account = parentAccount;
      if (isOverseasUser) payload.is_overseas_user = true;
      fetch(API_BASE + '/auth/register-phone', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Installation-Id': typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : ''
        },
        body: JSON.stringify(payload)
      })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
        .then(function(x) {
          if (x.ok) {
            token = x.data.access_token;
            localStorage.setItem('token', token);
            if (typeof persistOpenclawChannelFallback === 'function') persistOpenclawChannelFallback(token);
            showMsg(msgEl, '登录成功', false);
            setRegisterSmsButtonCooldown(0);
            loadDashboard();
          } else {
            var detail = normalizeAuthErrorDetail(x.data.detail);
            showMsg(msgEl, detail || '登录失败', true);
          }
        })
        .catch(function() { showMsg(msgEl, '网络错误', true); });
    }

    var bm = (typeof window.__LOBSTER_BRAND_MARK !== 'undefined' && window.__LOBSTER_BRAND_MARK) ? String(window.__LOBSTER_BRAND_MARK) : '';
    var pa = (typeof window.__LOBSTER_PARENT_ACCOUNT !== 'undefined' && window.__LOBSTER_PARENT_ACCOUNT) ? String(window.__LOBSTER_PARENT_ACCOUNT) : '';
    if (bm) {
      postRegisterPhone(bm, pa);
      return;
    }
    var localBase = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
    if (!localBase) {
      postRegisterPhone('', pa);
      return;
    }
    fetch(localBase + '/api/branding', { credentials: 'same-origin' })
      .then(function(r) { return r.ok ? r.json() : {}; })
      .then(function(b) {
        var mark = (b && b.mark) ? String(b.mark) : '';
        var parent = (b && b.parent_account) ? String(b.parent_account) : pa;
        if (parent) window.__LOBSTER_PARENT_ACCOUNT = parent;
        postRegisterPhone(mark, parent);
      })
      .catch(function() { postRegisterPhone('', pa); });
  });
}

/** 在线版：切换为服务器端 TOS 转存，并清理本机旧 TOS_CONFIG */
function syncTosFromServerIfOnline() {
  if (typeof EDITION === 'undefined' || EDITION !== 'online' || !token) return;
  var localBase = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  if (!localBase) return;
  fetch(localBase + '/api/settings/sync-tos-from-server', { method: 'POST', headers: typeof authHeaders === 'function' ? authHeaders() : { 'Authorization': 'Bearer ' + token } })
    .catch(function() {});
}

function syncOpenclawMemoryFromServerIfOnline() {
  if (typeof EDITION === 'undefined' || EDITION !== 'online' || !token) return;
  if (typeof _syncOpenclawMemoryFromCloud !== 'function') return;
  _syncOpenclawMemoryFromCloud({ silent: true, reload: false }).catch(function() {});
}

function setAuthenticatedChrome(isAuthenticated) {
  var header = document.getElementById('appHeader');
  var nav = document.getElementById('appTopNav');
  var actions = document.getElementById('headerActions');
  if (header) header.classList.toggle('auth-guest', !isAuthenticated);
  if (nav) nav.style.display = isAuthenticated ? 'flex' : 'none';
  if (actions) actions.style.display = isAuthenticated ? 'flex' : 'none';
}

var LOBSTER_LAST_VIEW_KEY = 'lobster_online_last_view';
var LOBSTER_MAIN_VIEWS = {
  chat: true,
  'skill-store': true,
  'douyin-leads': true,
  publish: true,
  assets: true,
  'scheduled-tasks': true,
  production: true,
  billing: true,
  'sys-config': true,
  logs: true,
  agent: true,
  'openclaw-memory': true,
  'personal-settings': true,
  'creative-film-studio': true,
  'ppt-studio': true,
  'viral-tvc-studio': true
};
var LOBSTER_HIDDEN_VIEWS = {
  'wecom-config': true,
  'wecom-detail': true,
  'messenger-config': true,
  'twilio-whatsapp-config': true,
  'twilio-whatsapp-detail': true,
  'youtube-accounts': true,
  'meta-social': true,
  'ecommerce-detail-studio': true,
  'image-composer-studio': true,
  'seedance-tvc-studio': true,
  'viral-video-remix': true,
  'cutcli-template-studio': true,
  'hifly-digital-human': true,
  'shanjian-digital-human': true,
  'douyin-workbench': true,
  'shanjian-smart-clip': true,
  'global-leads': true,
  'ai-3d-model': true,
  'openclaw-skill-chat': true
};
var _restoringDashboardView = false;
var _suppressNextHashApply = false;
var LOBSTER_FEATURE_FLAGS = {};
var LOBSTER_FEATURE_RAW = {};
var LOBSTER_FEATURE_STRICT = false;
var _lobsterFeatureRefreshInFlight = null;
var _lobsterFeatureLastRefreshAt = 0;
var LOBSTER_FEATURE_REFRESH_INTERVAL_MS = 15000;
var LOBSTER_FEATURE_STRICT_MARKER = '__homepage_feature_gates_v1';
var LOBSTER_LEGACY_DENY_MISSING_FEATURES = {
  douyin_leads_access: true,
  reddit_leads_access: true,
  x_leads_access: true,
  tiktok_leads_access: true,
  openai_official_image_channel_access: true
};
var LOBSTER_VIEW_FEATURE_GATES = {
  chat: 'home_ai_chat_entry',
  'skill-store': 'skill_store_entry',
  'douyin-leads': 'douyin_leads_access',
  publish: 'publish_center_entry',
  assets: 'asset_library_entry',
  'scheduled-tasks': 'scheduled_tasks_entry',
  production: 'production_records_entry',
  billing: 'billing_entry',
  'sys-config': 'sys_config_entry',
  logs: 'logs_entry',
  'personal-settings': 'personal_settings_entry',
  agent: 'agent_entry',
  'openclaw-memory': 'openclaw_memory_skill',
  'creative-film-studio': 'goal_video_pipeline_skill',
  'ip-content-studio': 'ip_content_daily_skill',
  'wechat-article': 'wewrite_official_account_skill',
  'image-composer-studio': 'goal_video_pipeline_skill',
  'ppt-studio': 'create_ppt_skill',
  'viral-tvc-studio': 'comfly_veo_skill',
  'seedance-tvc-studio': 'comfly_seedance_tvc_skill',
  'local-bestseller': 'local_bestseller_skill',
  'viral-video-remix': 'viral_video_remix_skill',
  'hifly-digital-human': 'hifly_digital_human_skill',
  'shanjian-digital-human': 'hifly_digital_human_skill',
  'ecommerce-detail-studio': 'comfly_ecommerce_detail_skill',
  'juhe-wechat': 'juhe_wechat_skill',
  'wechat-channels-transcript': 'wechat_channels_transcript_skill',
  'ai-3d-model': 'ai_3d_model_skill',
  'wecom-config': 'wecom_reply',
  'wecom-detail': 'wecom_reply',
  'messenger-config': 'messenger_reply',
  'twilio-whatsapp-config': 'twilio_whatsapp',
  'twilio-whatsapp-detail': 'twilio_whatsapp',
  'youtube-accounts': 'youtube_publish',
  'meta-social': 'meta_social',
  'cutcli-template-studio': 'cutcli_template_studio',
  'douyin-workbench': 'douyin_leads_access',
  'shanjian-smart-clip': 'shanjian_smart_clip',
  'global-leads': 'global_trade_leads_skill',
  'linkedin-mining': 'linkedin_mining_skill',
  'reddit-leads': 'reddit_leads_access',
  'x-leads': 'x_leads_access',
  'tiktok-leads': 'tiktok_leads_access',
  'social-leads': ['reddit_leads_access', 'x_leads_access', 'tiktok_leads_access', 'reddit_leads', 'x_leads', 'tiktok_leads'],
  'openclaw-skill-chat': ['browser_use_skill', 'computer_use_skill']
};

function _normalizeLobsterFeatureFlags(features) {
  var out = {};
  if (!features) return out;
  if (Array.isArray(features)) {
    features.forEach(function(item) {
      var key = String(item || '').trim();
      if (key) out[key] = true;
    });
    return out;
  }
  if (typeof features === 'string') {
    features.split(/[,;\s]+/).forEach(function(item) {
      var key = String(item || '').trim();
      if (key) out[key] = true;
    });
    return out;
  }
  if (typeof features === 'object') {
    Object.keys(features).forEach(function(key) {
      var value = features[key];
      var enabled = value === true || value === 1;
      if (!enabled && typeof value === 'string') {
        enabled = /^(1|true|yes|on|enabled)$/i.test(value.trim());
      }
      if (enabled) out[key] = true;
    });
  }
  return out;
}

function _hasLobsterFeature(featureKey) {
  if (!featureKey) return false;
  if (LOBSTER_FEATURE_FLAGS && LOBSTER_FEATURE_FLAGS[featureKey]) return true;
  if (!LOBSTER_FEATURE_STRICT && !LOBSTER_LEGACY_DENY_MISSING_FEATURES[featureKey]) return true;
  return false;
}

function _isLobsterFeatureGateAllowed(gate) {
  if (!gate) return true;
  if (Array.isArray(gate)) {
    return gate.some(function(item) { return _hasLobsterFeature(item); });
  }
  return _hasLobsterFeature(gate);
}

function _lobsterFeatureGateForView(view) {
  var key = _baseHashView(view);
  return LOBSTER_VIEW_FEATURE_GATES[key] || '';
}

function _isFeatureGatedViewAllowed(view) {
  var gate = _lobsterFeatureGateForView(view);
  if (gate) return _isLobsterFeatureGateAllowed(gate);
  return true;
}

function applyLobsterFeatureGates(features) {
  if (arguments.length) {
    LOBSTER_FEATURE_RAW = features || {};
    LOBSTER_FEATURE_FLAGS = _normalizeLobsterFeatureFlags(features);
    LOBSTER_FEATURE_STRICT = !!(LOBSTER_FEATURE_FLAGS && LOBSTER_FEATURE_FLAGS[LOBSTER_FEATURE_STRICT_MARKER]);
  }
  document.querySelectorAll('[data-feature-gate]').forEach(function(el) {
    var key = el.getAttribute('data-feature-gate') || '';
    var allowed = _hasLobsterFeature(key);
    if (!el.dataset.featureGateDefaultDisplay) {
      var inlineDisplay = (el.style && el.style.display) ? String(el.style.display) : '';
      var preserveDisplay = el.getAttribute('data-feature-gate-preserve-display') === '1';
      el.dataset.featureGateDefaultDisplay = preserveDisplay && inlineDisplay
        ? inlineDisplay
        : (inlineDisplay && inlineDisplay !== 'none' ? inlineDisplay : '__empty__');
    }
    if (!el.dataset.featureGateDefaultHidden) {
      el.dataset.featureGateDefaultHidden = el.hidden ? '1' : '0';
    }
    if (allowed) {
      el.hidden = el.dataset.featureGateDefaultHidden === '1';
      el.style.display = el.dataset.featureGateDefaultDisplay === '__empty__'
        ? ''
        : el.dataset.featureGateDefaultDisplay;
    } else {
      el.hidden = true;
      el.style.display = 'none';
    }
    var actuallyHidden = el.hidden || el.style.display === 'none';
    el.setAttribute('aria-hidden', actuallyHidden ? 'true' : 'false');
  });
  document.querySelectorAll('.chat-sidebar-tree-group').forEach(function(group) {
    var entries = Array.prototype.slice.call(group.querySelectorAll('.chat-sidebar-entry'));
    if (!entries.length) return;
    var hasVisible = entries.some(function(entry) {
      return !entry.hidden && entry.style.display !== 'none';
    });
    group.hidden = !hasVisible;
  });
}
window.applyLobsterFeatureGates = applyLobsterFeatureGates;
window.isLobsterFeatureAllowed = _hasLobsterFeature;
window.isLobsterFeatureGateAllowed = _isLobsterFeatureGateAllowed;
window.isLobsterViewAllowed = _isFeatureGatedViewAllowed;
window.lobsterFeatureGateForView = _lobsterFeatureGateForView;
window.getLobsterFeatureFlags = function() { return LOBSTER_FEATURE_RAW || {}; };

function refreshLobsterFeatureGatesFromServer(force) {
  if (!token) return Promise.resolve(LOBSTER_FEATURE_FLAGS);
  if (_lobsterFeatureRefreshInFlight) return _lobsterFeatureRefreshInFlight;
  var now = Date.now();
  if (!force && now - _lobsterFeatureLastRefreshAt < LOBSTER_FEATURE_REFRESH_INTERVAL_MS) {
    return Promise.resolve(LOBSTER_FEATURE_FLAGS);
  }
  _lobsterFeatureLastRefreshAt = now;
  var headers = typeof authHeaders === 'function'
    ? authHeaders()
    : { 'Authorization': 'Bearer ' + token };
  _lobsterFeatureRefreshInFlight = fetch(API_BASE + '/auth/me', {
    headers: headers,
    cache: 'no-store'
  }).then(function(r) {
    if (!r.ok) throw new Error('auth/me ' + r.status);
    return r.json();
  }).then(function(d) {
    if (d && Object.prototype.hasOwnProperty.call(d, 'features')) {
      applyLobsterFeatureGates(d.features || {});
    }
    return LOBSTER_FEATURE_FLAGS;
  }).catch(function() {
    return LOBSTER_FEATURE_FLAGS;
  }).then(function(result) {
    _lobsterFeatureRefreshInFlight = null;
    return result;
  }, function(err) {
    _lobsterFeatureRefreshInFlight = null;
    throw err;
  });
  return _lobsterFeatureRefreshInFlight;
}
window.refreshLobsterFeatureGatesFromServer = refreshLobsterFeatureGatesFromServer;

(function bindLobsterFeatureGateRefresh() {
  window.addEventListener('focus', function() {
    refreshLobsterFeatureGatesFromServer(false).catch(function() {});
  });
  document.addEventListener('visibilitychange', function() {
    if (!document.hidden) refreshLobsterFeatureGatesFromServer(false).catch(function() {});
  });
})();

function _hashRoute(value) {
  return String(value || '').replace(/^#/, '').trim();
}

function _baseHashView(value) {
  return _hashRoute(value).split(':')[0].trim();
}

function _isRestorableView(view) {
  view = _baseHashView(view);
  if (!_isFeatureGatedViewAllowed(view)) return false;
  return !!(view && (LOBSTER_MAIN_VIEWS[view] || LOBSTER_HIDDEN_VIEWS[view]));
}

function _firstAllowedDashboardView() {
  var navItems = Array.prototype.slice.call(document.querySelectorAll('.nav-left-item[data-view]'));
  for (var i = 0; i < navItems.length; i++) {
    if (navItems[i].hidden || navItems[i].style.display === 'none') continue;
    var view = navItems[i].getAttribute('data-view') || '';
    if (view && _isRestorableView(view)) return view;
  }
  return _isFeatureGatedViewAllowed('chat') ? 'chat' : '';
}
window.getFirstAllowedLobsterView = _firstAllowedDashboardView;

function _saveLastView(view) {
  view = _baseHashView(view);
  if (!view || !_isRestorableView(view)) return;
  try { localStorage.setItem(LOBSTER_LAST_VIEW_KEY, view); } catch (e) {}
}

function _replaceHashView(view) {
  var route = _hashRoute(view);
  view = _baseHashView(route);
  if (!route || !view || !_isRestorableView(view)) return;
  var current = _baseHashView(location.hash || '');
  if (_hashRoute(location.hash || '') === route) return;
  _suppressNextHashApply = true;
  try {
    history.replaceState(null, document.title, window.location.pathname + window.location.search + '#' + route);
  } catch (e) {
    try { location.hash = route; } catch (e2) {}
  }
  setTimeout(function() { _suppressNextHashApply = false; }, 0);
}

function _rememberView(view, opts) {
  opts = opts || {};
  var route = _hashRoute(view);
  var baseView = _baseHashView(route);
  if (!route || !baseView || !_isRestorableView(baseView)) return;
  _saveLastView(baseView);
  if (!opts.skipHash) _replaceHashView(route);
}
window._rememberLobsterView = _rememberView;

function _preferredInitialView() {
  var hashRoute = _hashRoute(location.hash || '');
  if (_isRestorableView(hashRoute)) return hashRoute;
  try {
    var stored = _baseHashView(localStorage.getItem(LOBSTER_LAST_VIEW_KEY) || '');
    if (_isRestorableView(stored)) return stored;
  } catch (e) {}
  return _firstAllowedDashboardView() || 'chat';
}

function restoreDashboardViewAfterLogin() {
  if (window.__lobsterDashboardViewRestored) return;
  window.__lobsterDashboardViewRestored = true;
  var route = _preferredInitialView();
  var view = _baseHashView(route);
  _rememberView(route);
  if (view === 'chat') {
    if (typeof showAppView === 'function') showAppView('chat').catch(function() {});
    return;
  }
  _restoringDashboardView = true;
  try {
    if (LOBSTER_MAIN_VIEWS[view] && typeof showAppView === 'function') {
      showAppView(view, document.querySelector('.nav-left-item[data-view="' + view + '"]')).catch(function() {});
    } else if (typeof window._applyWecomConfigHash === 'function') {
      window._applyWecomConfigHash(true);
    } else if (typeof window._openHiddenWorkspaceView === 'function') {
      window._openHiddenWorkspaceView(view);
    }
  } finally {
    setTimeout(function() { _restoringDashboardView = false; }, 0);
  }
}

function loadDashboard() {
  if (!token) {
    applyLobsterFeatureGates({});
    if (typeof window.resetChatSessionsForLogout === 'function') window.resetChatSessionsForLogout();
    document.getElementById('authPanel').style.display = 'block';
    document.getElementById('dashboard').classList.remove('visible');
    setAuthenticatedChrome(false);
    var heroEl = document.getElementById('pageHero');
    if (heroEl) heroEl.style.display = '';
    return;
  }
  fetch(API_BASE + '/auth/me', { headers: typeof authHeaders === 'function' ? authHeaders() : { 'Authorization': 'Bearer ' + token } })
    .then(function(r) {
      if (r.status === 401) { token = null; localStorage.removeItem('token'); loadDashboard(); return null; }
      return r.json();
    })
    .then(function(d) {
      if (!d) return;
      if (d.id == null) {
        token = null;
        localStorage.removeItem('token');
        loadDashboard();
        return;
      }
      if (typeof persistOpenclawChannelFallback === 'function') persistOpenclawChannelFallback(token);
      window.__currentUserId = d.id;
      applyLobsterFeatureGates(d.features || {});
      if (typeof window.resetChatSessionsMemory === 'function') window.resetChatSessionsMemory();
      document.getElementById('userEmail').textContent = d.email;
      document.getElementById('headerUserEmail').textContent = (d.email || '').split('@')[0];
      var avatarEl = document.getElementById('headerUserAvatar');
      if (avatarEl) avatarEl.textContent = ((d.email || 'U').trim().charAt(0) || 'U').toUpperCase();
      setAuthenticatedChrome(true);
      document.getElementById('authPanel').style.display = 'none';
      document.getElementById('dashboard').classList.add('visible');
      var heroEl = document.getElementById('pageHero');
      if (heroEl) heroEl.style.display = 'none';
      loadModelSelector(d.preferred_model);
      initChatSessions();
      setTimeout(restoreDashboardViewAfterLogin, 0);
      syncTosFromServerIfOnline();
      syncOpenclawMemoryFromServerIfOnline();
      if (EDITION === 'online') {
        loadSutuiBalance();
        var rBtn = document.getElementById('sutuiRechargeBtn');
        if (USE_INDEPENDENT_AUTH && rBtn) {
          rBtn.onclick = function(e) { e.preventDefault(); document.querySelector('.nav-left-item[data-view="billing"]') && document.querySelector('.nav-left-item[data-view="billing"]').click(); };
        }
      } else {
        var w = document.getElementById('sutuiBalanceWrap');
        if (w) w.style.display = 'none';
      }
      var navAgent = document.getElementById('navAgent');
      if (navAgent) navAgent.style.display = '';
      window.__currentUserIsAgent = !!d.is_agent;
    });
}

var _modelSelectorBound = false;

function updateSutuiSubSelectVisibility() {
  var sub = document.getElementById('sutuiModelSelect');
  var lab = document.getElementById('sutuiModelLabel');
  if (typeof EDITION !== 'undefined' && EDITION === 'online') {
    if (sub) sub.style.display = 'none';
    if (lab) lab.style.display = 'none';
    return;
  }
  var main = document.getElementById('modelSelect');
  if (!main || !sub) return;
  var on = (main.value === 'sutui_aggregate' && typeof EDITION !== 'undefined' && EDITION === 'online');
  sub.style.display = on ? '' : 'none';
  if (lab) lab.style.display = on ? '' : 'none';
  if (on) loadSutuiSubModels();
}

function loadSutuiSubModels() {
  var sub = document.getElementById('sutuiModelSelect');
  if (!sub) return;
  var b = (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
  if (!b || !token) return;
  fetch(b + '/api/sutui-llm/models', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var list = (d && Array.isArray(d.models)) ? d.models : [];
      var cur = sub.value;
      sub.innerHTML = '';
      function appendOptions(showUnavailable) {
        list.forEach(function(m) {
          if (!m || !m.id) return;
          if (!showUnavailable && m.available === false) return;
          var opt = document.createElement('option');
          opt.value = m.id;
          opt.textContent = m.name || m.id;
          sub.appendChild(opt);
        });
      }
      appendOptions(false);
      if (!sub.options.length && list.length) appendOptions(true);
      var rec = d && d.recommended;
      if (rec && sub.options.length && Array.prototype.some.call(sub.options, function(o) { return o.value === rec; })) {
        sub.value = rec;
      } else if (cur && Array.prototype.some.call(sub.options, function(o) { return o.value === cur; })) {
        sub.value = cur;
      } else {
        var last = '';
        try {
          last = (localStorage.getItem('lobster_last_sutui_submodel') || '').trim();
        } catch (e0) {}
        if (last && Array.prototype.some.call(sub.options, function(o) { return o.value === last; })) {
          sub.value = last;
        }
      }
      if (sub.value) {
        try {
          localStorage.setItem('lobster_last_sutui_submodel', sub.value);
        } catch (e1) {}
      }
      sub.onchange = function() {
        try {
          if (sub.value) localStorage.setItem('lobster_last_sutui_submodel', sub.value);
        } catch (e2) {}
      };
      // 勿在此调用 maybeAutoResume：每次切回「对话」会 refreshModelSelector→loadSutuiSubModels，
      // 会误把会话拉回「恢复进度」或擅自切换到带 poll_resume 的其它会话；续查仅在 initChatSessions 触发。
    })
    .catch(function() {});
}

function loadModelSelector(preferredModel) {
  var sel = document.getElementById('modelSelect');
  if (!sel) return;
  var row = sel.closest('.model-selector');
  if (typeof EDITION !== 'undefined' && EDITION === 'online') {
    if (row) row.style.display = 'none';
    sel.innerHTML = '<option value="sutui_aggregate">速推聚合</option>';
    sel.value = 'sutui_aggregate';
    try {
      localStorage.setItem('lobster_last_sutui_submodel', 'deepseek-chat');
    } catch (eOnlineModel) {}
    return;
  }
  if (row) row.style.display = '';
  if (preferredModel) sel.setAttribute('data-preferred', preferredModel);
  var pref = preferredModel || sel.getAttribute('data-preferred') || '';
  if (pref === 'sutui') pref = 'sutui_aggregate';
  if (typeof EDITION !== 'undefined' && EDITION === 'online' && !pref) {
    pref = 'sutui_aggregate';
  }
  var cloudBase = (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
  var localBase = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  var cloudP = (cloudBase && token)
    ? fetch(cloudBase + '/api/settings/models', { headers: authHeaders() }).then(function(r) { return r.json(); }).catch(function() { return { models: [] }; })
    : Promise.resolve({ models: [] });
  var localP = localBase
    ? fetch(localBase + '/api/settings/models', { headers: token ? authHeaders() : {} }).then(function(r) { return r.json(); }).catch(function() { return { models: [] }; })
    : Promise.resolve({ models: [] });
  Promise.all([cloudP, localP]).then(function(arr) {
    var cloud = arr[0] || {};
    var local = arr[1] || {};
    var merged = [];
    var seen = {};
    (cloud.models || []).forEach(function(m) {
      if (!m || !m.id || seen[m.id]) return;
      seen[m.id] = true;
      merged.push(m);
    });
    (local.models || []).forEach(function(m) {
      if (!m || !m.id || seen[m.id]) return;
      if (m.id === 'sutui' || m.id === 'sutui_aggregate') return;
      seen[m.id] = true;
      merged.push(m);
    });
    merged.sort(function(a, b) {
      if (a.id === 'sutui_aggregate') return -1;
      if (b.id === 'sutui_aggregate') return 1;
      return 0;
    });
    if (!merged.length) {
      updateSutuiSubSelectVisibility();
      return;
    }
    var curVal = sel.value;
    sel.innerHTML = merged.map(function(m) {
      var selected = (m.id === pref || m.id === curVal) ? ' selected' : '';
      var label = m.custom ? m.name + ' (自定义)' : m.name;
      return '<option value="' + escapeAttr(m.id) + '"' + selected + '>' + escapeHtml(label) + '</option>';
    }).join('');
    updateSutuiSubSelectVisibility();
  });
  if (!_modelSelectorBound) {
    _modelSelectorBound = true;
    sel.addEventListener('change', function() {
      if (typeof API_BASE !== 'undefined' && API_BASE) {
        fetch(API_BASE + '/api/settings', {
          method: 'POST', headers: authHeaders(),
          body: JSON.stringify({ preferred_model: sel.value })
        }).catch(function() {});
      }
      updateSutuiSubSelectVisibility();
    });
  }
}

function refreshModelSelector() {
  loadModelSelector();
}

(function initChatTipsModal() {
  var modal = document.getElementById('chatTipsModal');
  var openBtn = document.getElementById('chatTipsBtn');
  var closeBtn = document.getElementById('chatTipsModalClose');
  function closeModal() {
    if (modal) modal.classList.remove('visible');
  }
  function openModal() {
    if (modal) modal.classList.add('visible');
  }
  if (openBtn) openBtn.addEventListener('click', openModal);
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (modal) {
    modal.addEventListener('click', function(e) {
      if (e.target === modal) closeModal();
    });
  }
})();

document.getElementById('logout').addEventListener('click', function() {
  token = null;
  localStorage.removeItem('token');
  if (typeof window.resetChatSessionsForLogout === 'function') window.resetChatSessionsForLogout();
  var avatarEl = document.getElementById('headerUserAvatar');
  if (avatarEl) avatarEl.textContent = 'U';
  document.getElementById('dashboard').classList.remove('visible');
  document.getElementById('authPanel').style.display = 'block';
  setAuthenticatedChrome(false);
  var heroEl = document.getElementById('pageHero');
  if (heroEl) heroEl.style.display = '';
});

(function initDropdown() {
  var dropdown = document.getElementById('headerUserDropdown');
  var btn = document.getElementById('headerDropdownBtn');
  if (dropdown && btn) {
    btn.addEventListener('click', function(e) { e.stopPropagation(); dropdown.classList.toggle('open'); });
    document.addEventListener('click', function() { dropdown.classList.remove('open'); });
  }
  document.querySelectorAll('.header-menu-view[data-view]').forEach(function(item) {
    item.addEventListener('click', function(e) {
      e.stopPropagation();
      var view = item.getAttribute('data-view');
      var navBtn = view ? document.querySelector('.nav-left-item[data-view="' + view + '"]') : null;
      if (navBtn) {
        navBtn.click();
      } else if (view && typeof showAppView === 'function') {
        showAppView(view, item).catch(function() {});
      }
      if (dropdown) dropdown.classList.remove('open');
    });
  });
})();

function decorateWorkspacePages() {
  if (window.__workspacePagesDecorated) return;
  window.__workspacePagesDecorated = true;
  var configs = [
    {
      id: 'content-skill-store',
      title: '技能商店',
      actions: [
        { label: '添加 MCP', clickId: 'openAddMcpModal', primary: true },
        { label: '刷新商店', clickId: 'refreshStoreBtn' }
      ]
    },
    {
      id: 'content-publish',
      title: '发布中心',
      actions: [
        { label: '刷新内容', clickId: 'refreshPublishBtn', primary: true },
        { label: '账号列表', jumpView: 'publish' }
      ]
    },
    {
      id: 'content-assets',
      title: '素材库',
      actions: [
        { label: '刷新素材', clickId: 'assetTopRefreshBtn', primary: true }
      ]
    },
    {
      id: 'content-billing',
      title: '消费记录',
      actions: [
        { label: '立即刷新', clickId: 'billingRefreshBtn', primary: true }
      ]
    },
    {
      id: 'content-sys-config',
      title: '系统配置',
      actions: [
        { label: '查看技能商店', jumpView: 'skill-store' },
        { label: '回到首页', jumpView: 'chat', primary: true }
      ]
    },
    {
      id: 'content-logs',
      title: '日志与排查',
      actions: [
        { label: '刷新日志', clickId: 'logsRefreshBtn', primary: true },
        { label: '上传诊断', clickId: 'logsUploadDiagnosticBtn' }
      ]
    }
  ];
  configs.forEach(function(cfg) {
    var block = document.getElementById(cfg.id);
    if (!block || block.querySelector('.page-hero-card')) return;
    block.classList.add('has-page-shell');
    var hero = document.createElement('div');
    hero.className = 'page-hero-card';
    var copy = document.createElement('div');
    copy.className = 'page-hero-copy';
    var kicker = document.createElement('div');
    kicker.className = 'page-hero-kicker';
    kicker.textContent = cfg.kicker;
    var title = document.createElement('h2');
    title.className = 'page-hero-title';
    title.textContent = cfg.title;
    var desc = document.createElement('p');
    desc.className = 'page-hero-desc';
    desc.textContent = cfg.desc;
    copy.appendChild(kicker);
    copy.appendChild(title);
    copy.appendChild(desc);
    hero.appendChild(copy);
    if (cfg.actions && cfg.actions.length) {
      var actions = document.createElement('div');
      actions.className = 'page-hero-actions';
      cfg.actions.forEach(function(action) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'page-hero-action' + (action.primary ? ' is-primary' : '');
        btn.textContent = action.label;
        if (action.clickId) btn.setAttribute('data-click-id', action.clickId);
        if (action.jumpView) btn.setAttribute('data-jump-view', action.jumpView);
        actions.appendChild(btn);
      });
      hero.appendChild(actions);
    }
    block.insertBefore(hero, block.firstChild);
  });
  document.addEventListener('click', function(e) {
    var clickBtn = e.target.closest('[data-click-id]');
    if (clickBtn) {
      var targetId = clickBtn.getAttribute('data-click-id');
      var target = targetId ? document.getElementById(targetId) : null;
      if (target) target.click();
    }
  });
}
// Each tab already has its own card header/actions. Avoid injecting a second
// page-level hero that repeats the same title and controls.
// decorateWorkspacePages();

function decorateWorkspaceSubsections() {
  function addClass(id, className) {
    var el = document.getElementById(id);
    if (el) el.classList.add(className);
    return el;
  }

  function addSectionChrome(block, options) {
    if (!block) return;
    block.classList.add('page-section-shell');
    if (block.querySelector('.page-section-head') || !options || !options.title) return;

    var head = document.createElement('div');
    head.className = 'page-section-head';

    var copy = document.createElement('div');
    copy.className = 'page-section-copy';

    if (options.kicker) {
      var kicker = document.createElement('div');
      kicker.className = 'page-section-kicker';
      kicker.textContent = options.kicker;
      copy.appendChild(kicker);
    }

    var title = document.createElement('h3');
    title.className = 'page-section-title';
    title.textContent = options.title;
    copy.appendChild(title);

    if (options.desc) {
      var desc = document.createElement('p');
      desc.className = 'page-section-desc';
      desc.textContent = options.desc;
      copy.appendChild(desc);
    }

    head.appendChild(copy);

    if (Array.isArray(options.actions) && options.actions.length) {
      var actions = document.createElement('div');
      actions.className = 'page-section-actions';
      options.actions.forEach(function(action) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'page-section-action' + (action.primary ? ' is-primary' : '');
        btn.textContent = action.label;
        if (action.clickId) btn.setAttribute('data-click-id', action.clickId);
        if (action.jumpView) btn.setAttribute('data-jump-view', action.jumpView);
        actions.appendChild(btn);
      });
      head.appendChild(actions);
    }

    block.insertBefore(head, block.firstChild);
  }

  function readLabel(selector, fallback) {
    var el = document.querySelector(selector);
    var text = el ? (el.textContent || '').trim() : '';
    return text || fallback;
  }

  function markToolbars(parentId, selectors) {
    var parent = document.getElementById(parentId);
    if (!parent || !Array.isArray(selectors)) return;
    selectors.forEach(function(config) {
      var node = parent.querySelector(config.selector);
      if (!node) return;
      node.classList.add(config.className);
    });
  }

  addSectionChrome(document.getElementById('storeTabPopular'), {
    title: readLabel('.store-tab[data-store-tab="popular"]', '热门')
  });
  addSectionChrome(document.getElementById('storeTabOfficial'), {
    title: readLabel('.store-tab[data-store-tab="official"]', '能力列表')
  });
  addSectionChrome(document.getElementById('pubTabAccounts'), {
    title: readLabel('.pub-tab[data-pub-tab="accounts"]', '发布账号')
  });
  addSectionChrome(document.getElementById('pubTabTasks'), {
    title: readLabel('.pub-tab[data-pub-tab="tasks"]', '发布记录')
  });

  markToolbars('pubTabAccounts', [
    { selector: '#accountAddToolbar', className: 'page-toolbar' },
    { selector: '#accountFilterToolbar', className: 'publish-filter-toolbar' }
  ]);
  markToolbars('pubTabAssets', [
    { selector: ':scope > div:nth-of-type(2)', className: 'publish-filter-toolbar' },
    { selector: ':scope > div:nth-of-type(3)', className: 'publish-assets-toolbar' }
  ]);

  addClass('accountPublishShell', 'publish-shell-grid');
  addClass('accountListPanel', 'publish-list-panel');
  addClass('accountDetailPanel', 'account-detail-shell');
  addClass('taskList', 'publish-task-list');
  addClass('skillStoreList', 'skill-store-grid');
  addClass('mcpRegistryResults', 'skill-store-grid');
  addClass('accountList', 'skill-store-grid');
  addClass('assetList', 'skill-store-grid');

  var detailBackRow = document.querySelector('#accountDetailPanel > div:first-child');
  if (detailBackRow) {
    detailBackRow.classList.add('account-detail-summary');
    var title = document.getElementById('accountDetailTitle');
    if (title) title.classList.add('account-detail-title');
  }
}
decorateWorkspaceSubsections();

function runAppViewInit(view) {
  applyLobsterFeatureGates(LOBSTER_FEATURE_FLAGS);
  decorateWorkspaceSubsections();
  if (view === 'chat') {
    if (typeof setChatMode === 'function') setChatMode('default');
    refreshModelSelector();
  }
  if (view === 'skill-store') { loadSkillStore(); if (typeof initOnlineSkillStore === 'function') initOnlineSkillStore(); }
  if (view === 'publish') { if (typeof initPublishView === 'function') initPublishView(); }
  if (view === 'assets') { if (typeof initAssetLibraryView === 'function') initAssetLibraryView(); }
  if (view === 'scheduled-tasks') { if (typeof initScheduledTasksView === 'function') initScheduledTasksView(); }
  if (view === 'production') { if (typeof initProductionView === 'function') initProductionView(); }
  if (view === 'billing') { if (typeof loadBillingView === 'function') loadBillingView(); }
  if (view === 'sys-config') { loadOpenClawConfig(); }
  if (view === 'logs') { if (typeof ensureLogsBindings === 'function') ensureLogsBindings(); }
  if (view === 'openclaw-memory' && typeof window.initOpenclawMemoryManager === 'function') window.initOpenclawMemoryManager();
  if (view === 'personal-settings' && typeof window.initPersonalSettingsView === 'function') window.initPersonalSettingsView();
  if (view === 'creative-film-studio' && typeof window.initCreativeFilmStudioView === 'function') window.initCreativeFilmStudioView();
  if (view === 'messenger-config' && typeof loadMessengerConfigPage === 'function') loadMessengerConfigPage();
  if (view === 'youtube-accounts' && typeof loadYoutubeAccountsPage === 'function') loadYoutubeAccountsPage();
  if (view === 'meta-social' && typeof loadMetaSocialPage === 'function') loadMetaSocialPage();
  if (view === 'agent' && typeof loadAgentSubUsers === 'function') loadAgentSubUsers();
  if (typeof window.runRegisteredLobsterViewInit === 'function') return window.runRegisteredLobsterViewInit(view);
  return Promise.resolve();
}

function showAppView(view, sourceEl) {
  if (typeof window.closeAllPublishModals === 'function') window.closeAllPublishModals();
  var chatTips = document.getElementById('chatTipsModal');
  if (chatTips) chatTips.classList.remove('visible');
  if (!view) return Promise.resolve(null);
  if (!_isFeatureGatedViewAllowed(view)) {
    var fallbackView = _firstAllowedDashboardView();
    try { if (fallbackView) localStorage.setItem(LOBSTER_LAST_VIEW_KEY, fallbackView); } catch (e0) {}
    if (fallbackView && fallbackView !== view) return showAppView(fallbackView);
    return Promise.resolve(null);
  }
  _rememberView(view);
  if (currentView === 'chat' && view !== 'chat' && typeof saveCurrentSessionToStore === 'function') saveCurrentSessionToStore();
  document.querySelectorAll('.nav-left-item').forEach(function(b) { b.classList.remove('active'); });
  var navEl = document.querySelector('.nav-left-item[data-view="' + view + '"]');
  if (navEl) navEl.classList.add('active');
  else if (sourceEl) sourceEl.classList.add('active');

  var ensure = (typeof window.ensureLobsterViewLoaded === 'function')
    ? window.ensureLobsterViewLoaded(view)
    : Promise.resolve(document.getElementById('content-' + view));

  return ensure.then(function(contentEl) {
    document.querySelectorAll('.content-block').forEach(function(p) { p.classList.remove('visible'); });
    if (!contentEl) contentEl = document.getElementById('content-' + view);
    if (contentEl) contentEl.classList.add('visible');
    currentView = view;
    return runAppViewInit(view).then(function() { return contentEl; });
  }).catch(function(err) {
    console.error('Failed to show view:', view, err);
    var fallback = (typeof window.createLobsterViewError === 'function')
      ? window.createLobsterViewError(view, err && err.message ? err.message : String(err))
      : null;
    document.querySelectorAll('.content-block').forEach(function(p) { p.classList.remove('visible'); });
    if (fallback) fallback.classList.add('visible');
    return fallback;
  });
}
window.showAppView = showAppView;
window.showLobsterView = showAppView;

document.querySelectorAll('.nav-left-item').forEach(function(el) {
  el.addEventListener('click', function() {
    var hiddenView = el.dataset.openHiddenView;
    if (hiddenView && typeof window._openHiddenWorkspaceView === 'function') {
      document.querySelectorAll('.nav-left-item').forEach(function(b) { b.classList.remove('active'); });
      el.classList.add('active');
      window._openHiddenWorkspaceView(hiddenView);
      return;
    }
    var view = el.dataset.view;
    if (!view) return;
    showAppView(view, el).catch(function() {});
  });
});

document.addEventListener('click', function(event) {
  var trigger = event.target.closest('[data-view]');
  if (!trigger || trigger.classList.contains('nav-left-item') || trigger.classList.contains('header-menu-view')) return;
  var view = trigger.getAttribute('data-view');
  if (!view) return;
  event.preventDefault();
  showAppView(view, trigger).catch(function() {});
});

window.addEventListener('beforeunload', function() {
  if (typeof saveCurrentSessionToStore === 'function') saveCurrentSessionToStore();
});

function loadSutuiBalance() {
  var wrap = document.getElementById('sutuiBalanceWrap');
  var textEl = document.getElementById('sutuiBalanceText');
  var rechargeBtn = document.getElementById('sutuiRechargeBtn');
  if (!wrap || !textEl) return;
  if (typeof window.isLobsterFeatureAllowed === 'function' && !window.isLobsterFeatureAllowed('billing_entry')) {
    wrap.hidden = true;
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = 'flex';
  wrap.dataset.featureGateDefaultDisplay = 'flex';
  wrap.dataset.featureGateDefaultHidden = '0';
  wrap.classList.add('credit-balance-action');
  wrap.setAttribute('title', '查看算力消耗与每日上限');
  if (USE_INDEPENDENT_AUTH) {
    textEl.textContent = '算力：加载中…';
    if (rechargeBtn) { rechargeBtn.style.display = ''; rechargeBtn.href = '#'; rechargeBtn.textContent = '充值'; }
    fetch(API_BASE + '/auth/me', { headers: authHeaders() })
      .then(function(r) { return r.json(); })
      .then(function(d) { textEl.textContent = '算力：' + (d && d.credits != null ? d.credits : '--'); })
      .catch(function() { textEl.textContent = '算力：--'; });
    return;
  }
  textEl.textContent = '余额：加载中…';
  if (rechargeBtn) {
    rechargeBtn.style.display = RECHARGE_URL ? '' : 'none';
    rechargeBtn.href = RECHARGE_URL || '#';
    rechargeBtn.textContent = '充值';
  }
  fetch(API_BASE + '/api/sutui/balance', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.error) {
        textEl.textContent = '余额：--';
        return;
      }
      var yuan = (d.balance_yuan != null) ? String(d.balance_yuan) : (d.balance != null ? (d.balance / 1000).toFixed(2) : '--');
      textEl.textContent = '余额：' + yuan + ' 元';
    })
    .catch(function() { textEl.textContent = '余额：--'; });
}

function _creditLocalApiBase() {
  var b = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  return b || ((typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '');
}

function _creditFmt(v) {
  var n = Number(v);
  if (!isFinite(n)) return '--';
  if (Math.abs(n - Math.round(n)) < 0.0001) return String(Math.round(n));
  return String(Math.round(n * 10000) / 10000);
}

function _setCreditLimitMsg(text, isErr) {
  var el = document.getElementById('creditLimitMsg');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'credit-limit-msg' + (text ? (isErr ? ' err' : ' ok') : '');
}

function _renderCreditLimitStatus(status) {
  status = status || {};
  var balEl = document.getElementById('creditLimitBalance');
  var usedEl = document.getElementById('creditLimitUsed');
  var remEl = document.getElementById('creditLimitRemaining');
  var input = document.getElementById('creditLimitInput');
  if (balEl) {
    if (window._lobsterMe && window._lobsterMe.credits != null) balEl.textContent = _creditFmt(window._lobsterMe.credits);
    else balEl.textContent = '--';
  }
  if (usedEl) usedEl.textContent = _creditFmt(status.used_today);
  if (remEl) remEl.textContent = status.enabled ? _creditFmt(status.remaining_today) : '不限制';
  if (input) input.value = status.enabled ? _creditFmt(status.daily_limit) : '';
}

function loadCreditLimitStatus() {
  var base = _creditLocalApiBase();
  if (!base || !token) return Promise.reject(new Error('未登录'));
  return fetch(base + '/api/billing/daily-limit', { headers: authHeaders() })
    .then(function(r) {
      return r.json().catch(function() { return {}; }).then(function(d) {
        if (!r.ok) throw new Error((d && (d.detail || d.message)) || ('HTTP ' + r.status));
        return d;
      });
    })
    .then(function(d) {
      window._lobsterCreditLimitStatus = d;
      _renderCreditLimitStatus(d);
      return d;
    });
}

function saveCreditLimitValue(value) {
  var base = _creditLocalApiBase();
  if (!base || !token) return Promise.reject(new Error('未登录'));
  return fetch(base + '/api/billing/daily-limit', {
    method: 'PUT',
    headers: authHeaders(),
    body: JSON.stringify({ daily_limit: value })
  }).then(function(r) {
    return r.json().catch(function() { return {}; }).then(function(d) {
      if (!r.ok) throw new Error((d && (d.detail || d.message)) || ('HTTP ' + r.status));
      return d;
    });
  }).then(function(d) {
    window._lobsterCreditLimitStatus = d;
    _renderCreditLimitStatus(d);
    return d;
  });
}

function openCreditLimitModal() {
  var modal = document.getElementById('creditLimitModal');
  if (!modal) return;
  _setCreditLimitMsg('加载中...', false);
  modal.classList.add('visible');
  loadCreditLimitStatus()
    .then(function() { _setCreditLimitMsg('', false); })
    .catch(function(e) { _setCreditLimitMsg('加载失败：' + (e && e.message ? e.message : e), true); });
}

(function bindCreditLimitModal() {
  function bind() {
    var wrap = document.getElementById('sutuiBalanceWrap');
    var modal = document.getElementById('creditLimitModal');
    var closeBtn = document.getElementById('creditLimitClose');
    var saveBtn = document.getElementById('creditLimitSave');
    var disableBtn = document.getElementById('creditLimitDisable');
    var input = document.getElementById('creditLimitInput');
    if (wrap && !wrap.dataset.creditLimitBound) {
      wrap.dataset.creditLimitBound = '1';
      wrap.addEventListener('click', function(e) {
        if (e.target && e.target.id === 'sutuiRechargeBtn') return;
        e.preventDefault();
        openCreditLimitModal();
      });
    }
    function close() {
      if (modal) modal.classList.remove('visible');
    }
    if (closeBtn && !closeBtn.dataset.bound) {
      closeBtn.dataset.bound = '1';
      closeBtn.addEventListener('click', close);
    }
    if (modal && !modal.dataset.bound) {
      modal.dataset.bound = '1';
      modal.addEventListener('click', function(e) {
        if (e.target === modal) close();
      });
    }
    if (saveBtn && !saveBtn.dataset.bound) {
      saveBtn.dataset.bound = '1';
      saveBtn.addEventListener('click', function() {
        var raw = input ? String(input.value || '').trim() : '';
        var value = raw === '' ? 0 : Number(raw);
        if (!isFinite(value) || value < 0) {
          _setCreditLimitMsg('每日上限必须是大于等于 0 的数字。', true);
          return;
        }
        saveBtn.disabled = true;
        _setCreditLimitMsg('保存中...', false);
        saveCreditLimitValue(value)
          .then(function() { _setCreditLimitMsg('已保存。', false); })
          .catch(function(e) { _setCreditLimitMsg('保存失败：' + (e && e.message ? e.message : e), true); })
          .finally(function() { saveBtn.disabled = false; });
      });
    }
    if (disableBtn && !disableBtn.dataset.bound) {
      disableBtn.dataset.bound = '1';
      disableBtn.addEventListener('click', function() {
        if (input) input.value = '';
        disableBtn.disabled = true;
        _setCreditLimitMsg('保存中...', false);
        saveCreditLimitValue(0)
          .then(function() { _setCreditLimitMsg('已取消每日上限。', false); })
          .catch(function(e) { _setCreditLimitMsg('保存失败：' + (e && e.message ? e.message : e), true); })
          .finally(function() { disableBtn.disabled = false; });
      });
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();

(function initWecomConfigHash() {
  function applyHash(force) {
    if (!force && (_suppressNextHashApply || _restoringDashboardView)) return;
    var hash = (location.hash || '').replace(/^#/, '');
    var hashView = _baseHashView(hash);
    if (hashView && !_isFeatureGatedViewAllowed(hashView)) {
      var fallbackView = _firstAllowedDashboardView();
      if (fallbackView && typeof showAppView === 'function') {
        showAppView(fallbackView).catch(function() {});
      }
      return;
    }
    if (hashView && LOBSTER_MAIN_VIEWS[hashView] && typeof showAppView === 'function') {
      showAppView(hashView, document.querySelector('.nav-left-item[data-view="' + hashView + '"]')).catch(function() {});
      return;
    }
    if (hash === 'wecom-config' && typeof showWecomConfigView === 'function') showWecomConfigView();
    if (hash.indexOf('wecom-detail') === 0 && typeof showWecomDetailView === 'function') {
      var parts = hash.split(':');
      showWecomDetailView(parts[1] ? parseInt(parts[1], 10) : undefined);
    }
    if (hash === 'messenger-config' && typeof _openMessengerConfigView === 'function') {
      _openMessengerConfigView();
    }
    if (hash === 'twilio-whatsapp-config' && typeof _openTwilioWhatsappConfigView === 'function') {
      _openTwilioWhatsappConfigView();
    }
    if (hash === 'twilio-whatsapp-detail' && typeof showTwilioWhatsappDetailView === 'function') {
      showTwilioWhatsappDetailView();
    }
    if (hash === 'youtube-accounts' && typeof window._openYoutubeAccountsView === 'function') {
      window._openYoutubeAccountsView();
    }
    if (hash === 'meta-social' && typeof window._openMetaSocialView === 'function') {
      window._openMetaSocialView();
    }
    if (hash === 'ecommerce-detail-studio' && typeof window._openEcommerceDetailStudioView === 'function') {
      window._openEcommerceDetailStudioView();
    }
    if (hash === 'image-composer-studio' && typeof window._openImageComposerStudioView === 'function') {
      window._openImageComposerStudioView();
    }
    if (hash === 'seedance-tvc-studio' && typeof window._openSeedanceTvcStudioView === 'function') {
      window._openSeedanceTvcStudioView();
    }
    if (hash === 'viral-video-remix' && typeof window._openViralVideoRemixView === 'function') {
      window._openViralVideoRemixView();
    }
    if (hash === 'cutcli-template-studio' && typeof window._openCutcliTemplateStudioView === 'function') {
      window._openCutcliTemplateStudioView();
    }
    if (hash === 'hifly-digital-human' && typeof window._openHiflyDigitalHumanView === 'function') {
      window._openHiflyDigitalHumanView();
    }
    if (hash === 'shanjian-digital-human' && typeof window._openShanjianDigitalHumanView === 'function') {
      window._openShanjianDigitalHumanView();
    }
    if (hash === 'douyin-workbench' && typeof window._openDouyinWorkbenchView === 'function') {
      window._openDouyinWorkbenchView();
    }
    if (hash === 'shanjian-smart-clip' && typeof window._openShanjianSmartClipView === 'function') {
      window._openShanjianSmartClipView();
    }
    if (hash === 'openclaw-skill-chat' && typeof window.openOpenclawSkillChat === 'function') {
      window.openOpenclawSkillChat();
    }
  }
  window.addEventListener('hashchange', function() { applyHash(false); });
  window._applyWecomConfigHash = applyHash;
  if (location.hash && (
    location.hash.indexOf('wecom') !== -1 ||
    location.hash.indexOf('messenger') !== -1 ||
    location.hash.indexOf('twilio-whatsapp') !== -1 ||
    location.hash.indexOf('youtube-accounts') !== -1 ||
    location.hash.indexOf('juhe-wechat') !== -1 ||
    location.hash.indexOf('meta-social') !== -1 ||
    location.hash.indexOf('ecommerce-detail-studio') !== -1 ||
    location.hash.indexOf('image-composer-studio') !== -1 ||
    location.hash.indexOf('seedance-tvc-studio') !== -1 ||
    location.hash.indexOf('viral-video-remix') !== -1 ||
    location.hash.indexOf('cutcli-template-studio') !== -1 ||
    location.hash.indexOf('hifly-digital-human') !== -1 ||
    location.hash.indexOf('shanjian-digital-human') !== -1 ||
    location.hash.indexOf('douyin-workbench') !== -1 ||
    location.hash.indexOf('shanjian-smart-clip') !== -1 ||
    location.hash.indexOf('openclaw-skill-chat') !== -1
  )) applyHash();
})();

applyBrandingFromApi();
if (token) loadDashboard();
