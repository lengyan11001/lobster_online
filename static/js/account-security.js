(function () {
  function $(id) { return document.getElementById(id); }

  function cloudBase() {
    return String((typeof API_BASE !== 'undefined' && API_BASE) ? API_BASE : '').replace(/\/$/, '');
  }

  function headers() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders() || {}) : {};
    h['Content-Type'] = 'application/json';
    return h;
  }

  function cloudJson(path, opts) {
    opts = opts || {};
    var base = cloudBase();
    if (!base) return Promise.reject(new Error('未配置云端 API_BASE'));
    var req = { method: opts.method || 'GET', headers: headers() };
    if (opts.body !== undefined) req.body = JSON.stringify(opts.body || {});
    return fetch(base + path, req).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok) {
          var detail = data && (data.detail || data.error || data.message);
          throw new Error(typeof detail === 'string' && detail ? detail : '请求失败');
        }
        return data;
      });
    });
  }

  function phoneFromEmail(email) {
    var value = String(email || '').trim().toLowerCase();
    var at = value.indexOf('@');
    if (at < 0) return '';
    var local = value.slice(0, at);
    var domain = value.slice(at + 1);
    if (domain !== 'sms.lobster.local') return '';
    var tag = local.indexOf('+brand-');
    if (tag >= 0) local = local.slice(0, tag);
    return /^1[3-9]\d{9}$/.test(local) ? local : '';
  }

  function maskPhone(phone) {
    var value = String(phone || '');
    return value.length === 11 ? value.slice(0, 3) + '****' + value.slice(-4) : value;
  }

  function setMsg(text, isErr) {
    var node = $('asMsg');
    if (!node) return;
    node.textContent = text || '';
    node.className = 'as-msg' + (text ? ' show ' + (isErr ? 'err' : 'ok') : '');
  }

  function setChip(email) {
    var chip = $('asAccountChip');
    if (!chip) return;
    var phone = phoneFromEmail(email);
    chip.textContent = phone ? ('当前账号：' + maskPhone(phone)) : ('当前账号：' + String(email || '--'));
  }

  function refreshAccount() {
    return cloudJson('/auth/me').then(function (data) {
      setChip((data && data.email) || '');
      return data;
    }).catch(function (err) {
      setMsg('读取账号信息失败：' + (err && err.message ? err.message : '未知错误'), true);
    });
  }

  function changePassword() {
    var oldPassword = (($('asOldPassword') || {}).value || '');
    var newPassword = (($('asNewPassword') || {}).value || '');
    var confirmPassword = (($('asConfirmPassword') || {}).value || '');
    if (!oldPassword) return setMsg('请输入当前密码', true);
    if (!newPassword || newPassword.length < 6) return setMsg('新密码至少 6 位', true);
    if (newPassword.length > 128) return setMsg('新密码不能超过 128 位', true);
    if (newPassword !== confirmPassword) return setMsg('两次输入的新密码不一致', true);
    var btn = $('asChangePasswordBtn');
    if (btn) btn.disabled = true;
    setMsg('正在保存新密码…', false);
    return cloudJson('/auth/password/change', {
      method: 'POST',
      body: { old_password: oldPassword, new_password: newPassword }
    }).then(function () {
      [ 'asOldPassword', 'asNewPassword', 'asConfirmPassword' ].forEach(function (id) {
        var el = $(id); if (el) el.value = '';
      });
      setMsg('密码已更新，下次登录请使用新密码', false);
    }).catch(function (err) {
      setMsg('修改密码失败：' + (err && err.message ? err.message : '未知错误'), true);
    }).finally(function () { if (btn) btn.disabled = false; });
  }

  function sendPhoneCode() {
    var password = (($('asPhonePassword') || {}).value || '');
    var newPhone = (($('asNewPhone') || {}).value || '').trim();
    if (!password) return setMsg('请输入当前密码', true);
    if (!/^1[3-9]\d{9}$/.test(newPhone)) return setMsg('请输入正确的 11 位手机号', true);
    var btn = $('asSendPhoneCodeBtn');
    if (btn) btn.disabled = true;
    setMsg('正在发送验证码…', false);
    return cloudJson('/auth/phone/change/send-code', {
      method: 'POST',
      body: { password: password, new_phone: newPhone }
    }).then(function () {
      setMsg('验证码已发送到 ' + maskPhone(newPhone) + '，请查收', false);
    }).catch(function (err) {
      setMsg('发送验证码失败：' + (err && err.message ? err.message : '未知错误'), true);
    }).finally(function () { if (btn) btn.disabled = false; });
  }

  function changePhone() {
    var password = (($('asPhonePassword') || {}).value || '');
    var newPhone = (($('asNewPhone') || {}).value || '').trim();
    var code = (($('asPhoneCode') || {}).value || '').trim();
    if (!password) return setMsg('请输入当前密码', true);
    if (!/^1[3-9]\d{9}$/.test(newPhone)) return setMsg('请输入正确的 11 位手机号', true);
    if (!code) return setMsg('请输入短信验证码', true);
    var btn = $('asChangePhoneBtn');
    if (btn) btn.disabled = true;
    setMsg('正在换绑手机号…', false);
    return cloudJson('/auth/phone/change', {
      method: 'POST',
      body: { password: password, new_phone: newPhone, code: code }
    }).then(function (data) {
      setChip((data && data.email) || '');
      [ 'asPhonePassword', 'asNewPhone', 'asPhoneCode' ].forEach(function (id) {
        var el = $(id); if (el) el.value = '';
      });
      setMsg('换绑成功，新手机号 ' + maskPhone((data && data.phone) || newPhone) + ' 已生效', false);
      return refreshAccount();
    }).catch(function (err) {
      setMsg('换绑失败：' + (err && err.message ? err.message : '未知错误'), true);
    }).finally(function () { if (btn) btn.disabled = false; });
  }

  var bound = false;
  window.initAccountSecurityView = function () {
    if (!bound) {
      bound = true;
      var pwdBtn = $('asChangePasswordBtn');
      if (pwdBtn) pwdBtn.addEventListener('click', changePassword);
      var sendBtn = $('asSendPhoneCodeBtn');
      if (sendBtn) sendBtn.addEventListener('click', sendPhoneCode);
      var phoneBtn = $('asChangePhoneBtn');
      if (phoneBtn) phoneBtn.addEventListener('click', changePhone);
    }
    setMsg('', false);
    return refreshAccount();
  };
})();
