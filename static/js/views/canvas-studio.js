/* 灵感画布宿主：全屏工作台。顶部悬浮「灵感/生成/画布」+ 窗口三键，左下角返回工作台。 */
(function () {
  "use strict";

  var SCREENS = {
    inspiration: "/static/canvas-ui/inspiration.html?v=20261026-canvas-ui-v6",
    generate: "/static/canvas-ui/generate.html?v=20261026-canvas-ui-v6",
    projects: "/static/canvas-ui/projects.html?v=20261026-canvas-ui-v6"
  };

  function pywebviewApi() {
    try { return (window.pywebview && window.pywebview.api) || null; } catch (e) { return null; }
  }

  function initCanvasStudioView(mount) {
    var shell = (mount && mount.querySelector && mount.querySelector(".canvas-studio-shell"))
      || document.getElementById("lobsterCanvasStudioShell")
      || document.querySelector(".canvas-studio-shell");
    if (!shell) return;
    var frame = shell.querySelector("iframe");
    var boot = shell.querySelector(".canvas-studio-boot");
    if (!frame) return;

    function show(key) {
      var src = SCREENS[key] || SCREENS.projects;
      if (frame.getAttribute("src") !== src) frame.setAttribute("src", src);
      frame.style.display = "block";
      if (boot) boot.style.display = "none";
      Array.prototype.forEach.call(shell.querySelectorAll("[data-canvas-screen]"), function (btn) {
        btn.classList.toggle("active", btn.getAttribute("data-canvas-screen") === key);
      });
    }

    Array.prototype.forEach.call(shell.querySelectorAll("[data-canvas-screen]"), function (btn) {
      btn.onclick = function () { show(btn.getAttribute("data-canvas-screen")); };
    });

    function wireWindowButtons() {
      var api = pywebviewApi();
      var win = document.getElementById("lobsterCanvasWin");
      if (win) win.style.display = api ? "flex" : "none";
      var min = document.getElementById("canvasWinMin");
      var max = document.getElementById("canvasWinMax");
      var close = document.getElementById("canvasWinClose");
      if (min) min.onclick = function () { if (api && api.window_minimize) api.window_minimize(); };
      if (max) max.onclick = function () { if (api && api.window_toggle_maximize) api.window_toggle_maximize(); };
      if (close) close.onclick = function () { if (api && api.window_close) api.window_close(); };
    }
    wireWindowButtons();
    try {
      window.addEventListener("pywebviewready", wireWindowButtons);
      var tries = 0;
      var timer = setInterval(function () {
        tries += 1;
        if (pywebviewApi() || tries > 20) clearInterval(timer);
        wireWindowButtons();
      }, 500);
    } catch (e) {}

    var exit = document.getElementById("lobsterCanvasExit");
    if (exit) {
      exit.onclick = function () {
        document.body.classList.remove("canvas-studio-active");
        if (typeof window.showAppView === "function") {
          try { window.showAppView("chat"); return; } catch (e) {}
        }
        if (typeof window.showLobsterView === "function") {
          try { window.showLobsterView("chat"); return; } catch (e2) {}
        }
        location.hash = "#chat";
      };
    }

    document.body.classList.add("canvas-studio-active");
    window.__lobsterCanvasStudioCleanup = function () {
      document.body.classList.remove("canvas-studio-active");
    };
    show("projects");
  }

  window.initCanvasStudioView = initCanvasStudioView;
})();
