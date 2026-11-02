(function () {
  if (window.LobsterHistoryDrawer) return;
  function ensureStyle() {
    if (document.getElementById('lobsterHistoryDrawerStyle')) return;
    var st = document.createElement('style');
    st.id = 'lobsterHistoryDrawerStyle';
    st.textContent = [
      '.lhd-mask{position:fixed;inset:0;background:rgba(15,23,42,.35);z-index:1300;display:none;}',
      '.lhd-mask.is-open{display:block;}',
      '.lhd-drawer{position:fixed;top:0;right:0;bottom:0;width:min(720px,92vw);background:#fff;z-index:1301;box-shadow:-16px 0 48px rgba(15,23,42,.18);display:flex;flex-direction:column;transform:translateX(100%);transition:transform .18s ease;}',
      '.lhd-drawer.is-open{transform:translateX(0);}',
      '.lhd-head{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid #eef2f7;}',
      '.lhd-head strong{font-size:15px;color:#0f172a;}',
      '.lhd-close{margin-left:auto;border:0;background:none;font-size:22px;line-height:1;color:#94a3b8;cursor:pointer;}',
      '.lhd-body{flex:1 1 auto;overflow:auto;padding:14px 18px;}'
    ].join('');
    document.head.appendChild(st);
  }
  var opened = null;
  function close() {
    if (!opened) return;
    opened.drawer.classList.remove('is-open');
    opened.mask.classList.remove('is-open');
    document.removeEventListener('keydown', opened.onKey);
    opened = null;
  }
  function attach(opts) {
    opts = opts || {};
    var trigger = document.getElementById(opts.triggerId || '');
    var source = document.querySelector(opts.sourceSelector || '');
    if (!trigger || !source || trigger.dataset.lhdBound === '1') return false;
    ensureStyle();
    var mask = document.createElement('div');
    mask.className = 'lhd-mask';
    var drawer = document.createElement('div');
    drawer.className = 'lhd-drawer';
    drawer.innerHTML = '<div class="lhd-head"><strong>' + String(opts.title || '历史记录') + '</strong><button type="button" class="lhd-close" aria-label="close">×</button></div><div class="lhd-body"></div>';
    document.body.appendChild(mask);
    document.body.appendChild(drawer);
    drawer.querySelector('.lhd-body').appendChild(source);
    trigger.dataset.lhdBound = '1';
    trigger.addEventListener('click', function (ev) {
      ev.preventDefault();
      if (opened) { close(); return; }
      mask.classList.add('is-open');
      drawer.classList.add('is-open');
      var onKey = function (e) { if (e.key === 'Escape') close(); };
      opened = { mask: mask, drawer: drawer, onKey: onKey };
      document.addEventListener('keydown', onKey);
      if (typeof opts.onOpen === 'function') { try { opts.onOpen(); } catch (e) {} }
    });
    mask.addEventListener('click', close);
    drawer.querySelector('.lhd-close').addEventListener('click', close);
    return true;
  }
  window.LobsterHistoryDrawer = { attach: attach, close: close };
})();