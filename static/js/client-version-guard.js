/**
 * 前后端版本守卫：界面（static）与后端（backend）必须来自同一个包。
 *
 * 典型故障（2026-09-21）：某台机器界面已经升到 2.0.64/344，后端还是旧代码，
 * 前端在调 /api/twilio-whatsapp/config 却一路 404，看起来像"功能坏了"。
 * 这里把这种不一致直接摆在界面上，并自动尝试一次强刷。
 */
(function initLobsterVersionGuard() {
  if (window.__lobsterVersionGuardReady) return;
  window.__lobsterVersionGuardReady = true;

  var RELOAD_KEY = '__lobster_version_guard_reloaded';

  function normalize(value) {
    return String(value === undefined || value === null ? '' : value).trim();
  }

  function keyOf(version, build) {
    var v = normalize(version);
    var b = normalize(build);
    return (v ? v : '?') + (b ? '-' + b : '');
  }

  function banner(text, detail) {
    try {
      var el = document.getElementById('lobsterVersionGuardBanner');
      if (!el) {
        el = document.createElement('div');
        el.id = 'lobsterVersionGuardBanner';
        el.style.cssText = [
          'position:fixed', 'top:0', 'left:0', 'right:0', 'z-index:2147483000',
          'background:#b91c1c', 'color:#fff', 'font:13px/1.6 "Microsoft YaHei",sans-serif',
          'padding:8px 14px', 'box-shadow:0 6px 18px rgba(0,0,0,.25)', 'white-space:pre-wrap'
        ].join(';');
        document.body.appendChild(el);
      }
      el.textContent = text + (detail ? '　' + detail : '');
      document.title = '【版本不一致】' + document.title.replace(/^【版本不一致】/, '');
    } catch (err) {
      if (window.console) console.warn('[version-guard] banner failed', err);
    }
  }

  function check() {
    return Promise.all([
      fetch('/api/version', { cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; }),
      fetch('/static/client_version.json', { cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; })
    ]).then(function (all) {
      var backend = all[0];
      var staticSide = all[1];
      if (!backend) return;                     // 后端没起来：交给启动流程提示，不重复报警
      var state = {
        backend: backend,
        staticSide: staticSide,
        mismatch: false,
        missingRoutes: backend.expected_routes_missing || []
      };
      var backendKey = keyOf(backend.client_version, backend.client_build);
      var staticKey = keyOf(staticSide && staticSide.version, staticSide && staticSide.build);
      if (staticSide && staticKey !== backendKey && staticKey !== '?-') {
        state.mismatch = true;
        banner(
          '界面与后端版本不一致：界面 ' + staticKey + ' / 后端 ' + backendKey,
          '正在重新加载；若反复出现，请关闭客户端再打开（或重新更新）'
        );
      }
      if (state.missingRoutes.length) {
        banner(
          '后端缺少关键接口：' + state.missingRoutes.join('、'),
          '说明后端代码不是当前这个包（半包/旧包），请重新更新或重启客户端'
        );
      }
      // 版本不一致时自动强刷一次（后端刚被 launcher 重启的场景一次就好）
      if ((state.mismatch || state.missingRoutes.length) && !window[RELOAD_KEY]) {
        window[RELOAD_KEY] = true;
        setTimeout(function () {
          try { location.reload(); } catch (err) { /* ignore */ }
        }, 1200);
      }
      window.__lobsterVersionState = state;
      return state;
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { check(); });
  } else {
    check();
  }
})();
