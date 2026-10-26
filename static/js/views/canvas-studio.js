/* 灵感画布宿主：全屏工作台，隐藏系统顶部导航；左上角返回按钮回聊天工作台。 */
(function () {
  "use strict";

  var ENTRY = "/static/canvas-ui/projects.html?v=20261026-canvas-ui-v4";

  function initCanvasStudioView(mount) {
    var shell = (mount && mount.querySelector && mount.querySelector(".canvas-studio-shell"))
      || document.getElementById("lobsterCanvasStudioShell")
      || document.querySelector(".canvas-studio-shell");
    if (!shell) return;
    var frame = shell.querySelector("iframe");
    var boot = shell.querySelector(".canvas-studio-boot");
    var exit = document.getElementById("lobsterCanvasExit");
    if (!frame) return;
    if (frame.getAttribute("src") !== ENTRY) frame.setAttribute("src", ENTRY);
    frame.style.display = "block";
    if (boot) boot.style.display = "none";
    document.body.classList.add("canvas-studio-active");
    window.__lobsterCanvasStudioCleanup = function () {
      document.body.classList.remove("canvas-studio-active");
    };
    if (exit) {
      exit.onclick = function () {
        document.body.classList.remove("canvas-studio-active");
        if (typeof window.switchTab === "function") {
          try { window.switchTab("chat"); return; } catch (e) {}
        }
        location.hash = "#chat";
      };
    }
  }

  window.initCanvasStudioView = initCanvasStudioView;
})();
