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
  // 每个标签页会话最多自动强刷一次：否则"后端比代码旧"这种持续状态会变成无限重载
  var SESSION_RELOAD_KEY = 'lobster_version_guard_reloaded_once';

  function alreadyReloadedThisSession() {
    try {
      return !!window.sessionStorage.getItem(SESSION_RELOAD_KEY);
    } catch (err) {
      return false;
    }
  }

  function markReloadedThisSession() {
    try {
      window.sessionStorage.setItem(SESSION_RELOAD_KEY, String(Date.now()));
    } catch (err) {
      /* ignore */
    }
  }

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
        // 小号浮条：不铺满顶栏，右侧给窗口控制按钮（最小化/最大化/关闭）留位置，绝不遮挡关闭按钮。
        el.style.cssText = [
          'position:fixed', 'top:6px', 'left:8px', 'right:auto', 'z-index:2147483000',
          'max-width:min(620px, calc(100vw - 190px))',
          'background:#b91c1c', 'color:#fff', 'font:12px/1.45 "Microsoft YaHei",sans-serif',
          'padding:4px 10px', 'border-radius:6px',
          'box-shadow:0 2px 8px rgba(0,0,0,.18)', 'white-space:normal'
        ].join(';');
        document.body.appendChild(el);
      }
      el.textContent = '';
      el.title = text + (detail ? '\n' + detail : '');
      var span = document.createElement('span');
      span.textContent = text;
      el.appendChild(span);
      // 不再自动刷新页面（会白屏闪一下）：给一个按钮，用户点了才刷
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = '立即刷新界面';
      btn.style.cssText = 'margin-left:8px;padding:1px 8px;border:0;border-radius:4px;background:#fff;color:#b91c1c;font:12px/1.4 "Microsoft YaHei",sans-serif;font-weight:700;cursor:pointer;vertical-align:middle';
      btn.onclick = function () { try { location.reload(); } catch (err) {} };
      el.appendChild(btn);
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
          '版本不一致：界面 ' + staticKey + ' / 后端 ' + backendKey,
          '点「立即刷新界面」；反复出现就重启客户端'
        );
      }
      if (state.missingRoutes.length) {
        banner(
          '后端缺少关键接口：' + state.missingRoutes.join('、'),
          '请重新更新或重启客户端'
        );
      }
      // 包已经更新、但后端进程还是更新前启动的：也会表现成"界面有、接口 404"
      var appliedAt = Date.parse(String(backend.applied_at || ''));
      var startedAt = Date.parse(String(backend.backend_started_at || ''));
      if (!isNaN(appliedAt) && !isNaN(startedAt) && startedAt + 5000 < appliedAt) {
        state.backend_stale = true;
        banner(
          '后端进程还是更新前的，请重启客户端',
          '后端启动 ' + backend.backend_started_at + ' / 代码包 ' + backend.applied_at
        );
      }
      // 一律不自动 reload（自动刷新=白屏闪一下，用户明确要求不允许跳闪）；
      // 只挂横幅 + 「立即刷新界面」按钮，由用户自己点。
      if (state.mismatch || state.missingRoutes.length || state.backend_stale) {
        window[RELOAD_KEY] = true;
        markReloadedThisSession();
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
