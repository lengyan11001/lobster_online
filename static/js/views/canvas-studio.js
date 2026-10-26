/* 灵感画布宿主：直接加载设计师版界面（static/canvas-ui），
 * 内部「进入画布」再切到 canvas-web 的编辑器。 */
(function () {
  "use strict";

  var ENTRY = "/static/canvas-ui/projects.html?v=20261026-canvas-ui-v1";

  function initCanvasStudioView(mount) {
    var shell = (mount && mount.querySelector && mount.querySelector(".canvas-studio-shell"))
      || document.querySelector(".canvas-studio-shell");
    if (!shell) return;
    var frame = shell.querySelector("iframe");
    var boot = shell.querySelector(".canvas-studio-boot");
    if (!frame) return;
    cleanup();
    if (frame.getAttribute("src") !== ENTRY) frame.setAttribute("src", ENTRY);
    frame.style.display = "block";
    if (boot) boot.style.display = "none";
    window.__lobsterCanvasStudioCleanup = cleanup;
    function cleanup() {
      if (boot) boot.style.display = "none";
    }
  }

  function cleanup() {
    if (typeof window.__lobsterCanvasStudioCleanup === "function") {
      try { window.__lobsterCanvasStudioCleanup(); } catch (e) {}
      window.__lobsterCanvasStudioCleanup = null;
    }
  }

  window.initCanvasStudioView = initCanvasStudioView;
})();
