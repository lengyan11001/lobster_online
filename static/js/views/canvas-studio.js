/* 灵感画布宿主（客户端侧）。
 *
 * 2026-10-01：画布改挂「独立 origin」= http://127.0.0.1:8003/（画布在它自己的根下）。
 * 原因：画布是第三方打包的 hash 路由 SPA，内部会 pushState('/#/canvas-editor/<uuid>')。
 * 挂在客户端主站子路径 /static/canvas-web/ 时，'/#/...' 会被解析成主站根地址，
 * iframe 被顶成客户端整页（未登录态 = 登录页 + 套娃），复制/打开作品永远回不到画布。
 * 独立 origin 从根上消除这个 hash 路由 base 冲突。
 *
 * 注意：本视图的 HTML 是 view-loader 用 <template> 注入的，内联 <script> 不会执行，
 * 所以逻辑必须放在这个独立 js 文件里，由 view-registry 的 scripts + init 挂载。
 */
(function () {
  "use strict";

  var CANVAS_ORIGIN = "http://127.0.0.1:8003";
  var FRAME = CANVAS_ORIGIN + "/api/canvas-local/canvas-frame?v=20261001-canvas-origin";
  var FALLBACK = "/api/canvas-local/canvas-frame?v=20261001-canvas-origin-fallback";
  var MAX_PROBES = 8;
  var PROBE_GAP_MS = 700;
  var WATCH_MS = 800;

  function findShell(mount) {
    if (mount && mount.querySelector) {
      var local = mount.querySelector(".canvas-studio-shell");
      if (local) return local;
    }
    return document.querySelector(".canvas-studio-shell");
  }

  function cleanupPrevious() {
    if (typeof window.__lobsterCanvasStudioCleanup === "function") {
      try { window.__lobsterCanvasStudioCleanup(); } catch (e) { /* ignore */ }
      window.__lobsterCanvasStudioCleanup = null;
    }
  }

  function initCanvasStudioView(mount) {
    var shell = findShell(mount);
    if (!shell) return;
    var frame = shell.querySelector("iframe");
    var boot = shell.querySelector(".canvas-studio-boot");
    if (!frame) return;

    cleanupPrevious();

    var alive = true;
    var tries = 0;
    var enterTimer = null;

    function show(src) {
      if (frame.getAttribute("src") !== src) frame.setAttribute("src", src);
      if (boot) boot.style.display = "none";
      frame.style.display = "block";
    }

    function probe(cb) {
      var done = false;
      function finish(ok) { if (done) return; done = true; cb(ok); }
      try {
        var xhr = new XMLHttpRequest();
        xhr.open("GET", CANVAS_ORIGIN + "/healthz?ts=" + Date.now(), true);
        xhr.timeout = 1500;
        xhr.onreadystatechange = function () {
          if (xhr.readyState === 4) finish(xhr.status >= 200 && xhr.status < 300);
        };
        xhr.onerror = function () { finish(false); };
        xhr.ontimeout = function () { finish(false); };
        xhr.send();
      } catch (e) { finish(false); }
    }

    function enter() {
      if (!alive) return;
      probe(function (ok) {
        if (!alive) return;
        if (ok) { show(FRAME); return; }
        tries += 1;
        if (tries < MAX_PROBES) { enterTimer = window.setTimeout(enter, PROBE_GAP_MS); return; }
        if (boot) boot.textContent = "画布服务未就绪，已切到备用入口…";
        show(FALLBACK);
      });
    }

    enter();

    // 兜底：若 iframe 真被顶成主站整页（同源才读得到 pathname），立刻拉回画布。
    var watchdog = window.setInterval(function () {
      try {
        var w = frame.contentWindow;
        if (!w || !w.location) return;
        if (w.location.origin !== window.location.origin) return; // 画布自己的 origin，正常
        var p = String(w.location.pathname || "");
        if (p && p.indexOf("/static/canvas-web/") !== 0 && p.indexOf("/api/canvas-local/") !== 0) show(FRAME);
      } catch (e) { /* 跨域/未加载完，忽略 */ }
    }, WATCH_MS);

    window.__lobsterCanvasStudioCleanup = function () {
      alive = false;
      if (enterTimer) window.clearTimeout(enterTimer);
      window.clearInterval(watchdog);
    };
  }

  window.initCanvasStudioView = initCanvasStudioView;
})();
