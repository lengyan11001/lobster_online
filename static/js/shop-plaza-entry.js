/* 头像下拉「选品广场」入口：用客户端已有登录态打开 shop.bhzn.top/plaza.html
 * 说明：把当前 token 作为 ?token= 传给广场页（广场页会存到本地并在请求里带 Bearer），
 *      实现「用 online 已登录状态」，无需在网页里再登一次。
 */
(function () {
  'use strict';
  var SHOP_PLAZA_URL = 'https://shop.bhzn.top/plaza.html';

  function clientToken() {
    try {
      if (typeof getStoredAuthToken === 'function') {
        var t = getStoredAuthToken();
        if (t) return String(t);
      }
    } catch (e) { /* ignore */ }
    try { return String(localStorage.getItem('token') || ''); } catch (e) { return ''; }
  }

  function buildUrl() {
    var url = SHOP_PLAZA_URL;
    var token = clientToken();
    if (token) url += '?token=' + encodeURIComponent(token);
    return url;
  }

  function isDesktopShell() {
    try {
      if (window.pywebview && window.pywebview.api) return true;
      if (document.documentElement.dataset.desktopShell === '1') return true;
      return /pywebview/i.test(navigator.userAgent || '');
    } catch (e) {
      return false;
    }
  }

  function openExternally(url) {
    var api = window.pywebview && window.pywebview.api;
    if (api && typeof api.open_external_url === 'function') {
      try {
        var pending = api.open_external_url(url);
        if (pending && typeof pending.then === 'function') {
          return pending.then(function (res) { return !!(res && res.ok); }).catch(function () { return false; });
        }
        return Promise.resolve(true);
      } catch (e) { /* fall through */ }
    }
    /* 客户端外壳里绝不新开内部窗口：拿不到外部浏览器能力就提示复制链接 */
    if (isDesktopShell()) return Promise.resolve(false);
    /* 纯浏览器环境里 window.open 就是系统浏览器的新标签页 */
    try {
      var win = window.open(url, '_blank', 'noopener');
      return Promise.resolve(!!win);
    } catch (e) {
      return Promise.resolve(false);
    }
  }

  function openPlaza() {
    var url = buildUrl();
    try { window.ShopPlazaEntry.lastUrl = url; } catch (e) { /* ignore */ }
    openExternally(url).then(function (ok) {
      if (ok) return;
      /* 打不开就提示用户手动复制，绝不用 location.href 顶掉客户端当前页面 */
      var copied = false;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(url);
          copied = true;
        }
      } catch (e) { copied = false; }
      try {
        window.alert((copied ? '\u94fe\u63a5\u5df2\u590d\u5236\uff0c\u8bf7\u7c98\u8d34\u5230\u6d4f\u89c8\u5668\u6253\u5f00\uff1a\n' : '\u8bf7\u624b\u52a8\u590d\u5236\u4ee5\u4e0b\u94fe\u63a5\u5230\u6d4f\u89c8\u5668\u6253\u5f00\uff1a\n') + url);
      } catch (e) { /* ignore */ }
    });
  }

  function bind() {
    var btn = document.getElementById('shopPlazaEntry');
    if (btn && btn.dataset.shopPlazaBound !== '1') {
      btn.dataset.shopPlazaBound = '1';
      btn.addEventListener('click', openPlaza);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
  window.addEventListener('pageshow', bind);

  window.ShopPlazaEntry = { buildUrl: buildUrl, open: openPlaza, bind: bind };
})();