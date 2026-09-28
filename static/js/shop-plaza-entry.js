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

  function openPlaza() {
    var url = buildUrl();
    var win = null;
    try { win = window.open(url, '_blank', 'noopener'); } catch (e) { win = null; }
    if (!win) { try { location.href = url; } catch (e) {} }
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