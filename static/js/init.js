/** 在线版：独立认证时显示手机号验证码注册和登录；/api/edition 可覆盖。 */
var USE_INDEPENDENT_AUTH = true;

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

var loginForm = document.getElementById('loginForm');
if (loginForm) {
  loginForm.addEventListener('submit', function(e) {
    e.preventDefault();
    var fd = new FormData(this);
    var body = new URLSearchParams({
      username: fd.get('username'),
      password: fd.get('password'),
      captcha_id: fd.get('captcha_id') || '',
      captcha_answer: fd.get('captcha_answer') || ''
    });
    var msgEl = document.getElementById('loginMsg');
    fetch(API_BASE + '/auth/login', {
      method: 'POST',
      body: body,
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Installation-Id': typeof getOrCreateInstallationId === 'function' ? getOrCreateInstallationId() : ''
      }
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
          if (typeof loadLoginCaptcha === 'function') loadLoginCaptcha();
        }
      })
      .catch(function() { showMsg(msgEl, '网络错误', true); if (typeof loadLoginCaptcha === 'function') loadLoginCaptcha(); });
  });
}
function validateCnPhone(raw) {
  var d = String(raw || '').replace(/\D/g, '');
  if (!d) return null;
  return /^1[3-9]\d{9}$/.test(d) ? d : null;
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
  return detail;
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
      if (brandMark) payload.brand_mark = brandMark;
      if (parentAccount) payload.parent_account = parentAccount;
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

/** 在线版：本机未配 TOS 时从认证中心拉取服务器 TOS_CONFIG 写入 custom_configs.json */
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

function loadDashboard() {
  if (!token) {
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
      if (navAgent) navAgent.style.display = 'none';
      window.__currentUserIsAgent = !!d.is_agent;
      if (typeof window._applyWecomConfigHash === 'function' && location.hash) {
        var hiddenHash = String(location.hash || '');
        if (
          hiddenHash.indexOf('wecom') !== -1 ||
          hiddenHash.indexOf('messenger') !== -1 ||
          hiddenHash.indexOf('twilio-whatsapp') !== -1 ||
          hiddenHash.indexOf('youtube-accounts') !== -1 ||
          hiddenHash.indexOf('meta-social') !== -1 ||
          hiddenHash.indexOf('ecommerce-detail-studio') !== -1 ||
          hiddenHash.indexOf('seedance-tvc-studio') !== -1
        ) {
          window._applyWecomConfigHash();
        }
      }
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
      if (navBtn) navBtn.click();
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
      kicker: '能力中心',
      title: '技能商店',
      desc: '安装可用技能、浏览常用能力，并把生成、运营、发布等工作流逐步接进您的 AI 员工工作台。',
      actions: [
        { label: '添加 MCP', clickId: 'openAddMcpModal', primary: true },
        { label: '刷新商店', clickId: 'refreshStoreBtn' }
      ]
    },
    {
      id: 'content-publish',
      kicker: '发布与运营',
      title: '发布中心',
      desc: '统一管理发布账号和发布记录，让生成结果能够顺手进入分发和运营阶段。',
      actions: [
        { label: '刷新内容', clickId: 'refreshPublishBtn', primary: true },
        { label: '账号列表', jumpView: 'publish' }
      ]
    },
    {
      id: 'content-assets',
      kicker: '素材管理',
      title: '素材库',
      desc: '管理图片、视频和创意成片备选素材组，让生成任务能直接复用本机素材。',
      actions: [
        { label: '刷新素材', clickId: 'assetTopRefreshBtn', primary: true }
      ]
    },
    {
      id: 'content-billing',
      kicker: '账户与消耗',
      title: '消费记录',
      desc: '查看算力余额、充值记录和消耗明细，清楚知道每一步生成任务都花在哪里。',
      actions: [
        { label: '立即刷新', clickId: 'billingRefreshBtn', primary: true }
      ]
    },
    {
      id: 'content-sys-config',
      kicker: '系统控制台',
      title: '系统配置',
      desc: '在这里集中管理模型、连接状态和自定义配置，让复杂设置藏在后面，但仍然清晰可达。',
      actions: [
        { label: '查看技能商店', jumpView: 'skill-store' },
        { label: '回到首页', jumpView: 'chat', primary: true }
      ]
    },
    {
      id: 'content-logs',
      kicker: '运行诊断',
      title: '日志与排查',
      desc: '查看运行日志、调试信息和任务线索，方便在出现问题时快速定位和恢复。',
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
    kicker: 'Workspace',
    title: readLabel('.store-tab[data-store-tab="popular"]', 'Popular'),
    desc: 'Keep your most-used skills close, and bring common generation and operations flows into one place.'
  });
  addSectionChrome(document.getElementById('storeTabOfficial'), {
    kicker: 'Registry',
    title: readLabel('.store-tab[data-store-tab="official"]', 'Registry'),
    desc: 'Search cached MCP capabilities, browse references, and add what you need without leaving the workspace.'
  });
  addSectionChrome(document.getElementById('pubTabAccounts'), {
    kicker: 'Operations',
    title: readLabel('.pub-tab[data-pub-tab="accounts"]', 'Accounts'),
    desc: 'Manage connected accounts, open their workspaces, and continue into review or publishing when you are ready.'
  });
  addSectionChrome(document.getElementById('pubTabAssets'), {
    kicker: 'Library',
    title: '素材列表',
    desc: 'Collect generated media in one place so it can flow naturally into chat, editing, and publishing.'
  });
  addSectionChrome(document.getElementById('pubTabTasks'), {
    kicker: 'Records',
    title: readLabel('.pub-tab[data-pub-tab="tasks"]', 'Tasks'),
    desc: 'Review recent publish activity, inspect results, and follow each run without digging through logs.'
  });

  markToolbars('pubTabAccounts', [
    { selector: '#accountAddToolbar', className: 'page-toolbar' },
    { selector: '#accountFilterToolbar', className: 'publish-filter-toolbar' }
  ]);
  markToolbars('pubTabAssets', [
    { selector: ':scope > div:nth-of-type(1)', className: 'publish-filter-toolbar' },
    { selector: ':scope > div:nth-of-type(2)', className: 'publish-assets-toolbar' }
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
    var view = el.dataset.view;
    if (!view) return;
    showAppView(view, el).catch(function() {});
  });
});

window.addEventListener('beforeunload', function() {
  if (typeof saveCurrentSessionToStore === 'function') saveCurrentSessionToStore();
});

function loadSutuiBalance() {
  var wrap = document.getElementById('sutuiBalanceWrap');
  var textEl = document.getElementById('sutuiBalanceText');
  var rechargeBtn = document.getElementById('sutuiRechargeBtn');
  if (!wrap || !textEl) return;
  wrap.style.display = 'flex';
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

(function initWecomConfigHash() {
  function applyHash() {
    var hash = (location.hash || '').replace(/^#/, '');
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
  }
  window.addEventListener('hashchange', applyHash);
  window._applyWecomConfigHash = applyHash;
  if (location.hash && (
    location.hash.indexOf('wecom') !== -1 ||
    location.hash.indexOf('messenger') !== -1 ||
    location.hash.indexOf('twilio-whatsapp') !== -1 ||
    location.hash.indexOf('youtube-accounts') !== -1 ||
    location.hash.indexOf('meta-social') !== -1 ||
    location.hash.indexOf('ecommerce-detail-studio') !== -1 ||
    location.hash.indexOf('image-composer-studio') !== -1 ||
    location.hash.indexOf('seedance-tvc-studio') !== -1 ||
    location.hash.indexOf('viral-video-remix') !== -1
  )) applyHash();
})();

applyBrandingFromApi();
if (token) loadDashboard();
