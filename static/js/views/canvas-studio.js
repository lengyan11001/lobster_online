/* 灵感画布宿主：不带任何内嵌导航，直接整块加载画布界面。
 * 「进入画布」在 iframe 内切到 canvas-web 编辑器。 */
(function () {
  "use strict";

  var ENTRY = "/static/canvas-ui/projects.html?v=20261026-canvas-ui-v3";

  function initCanvasStudioView(mount) {
    var shell = (mount && mount.querySelector && mount.querySelector(".canvas-studio-shell"))
      || document.querySelector(".canvas-studio-shell");
    if (!shell) return;
    var frame = shell.querySelector("iframe");
    var boot = shell.querySelector(".canvas-studio-boot");
    if (!frame) return;
    if (frame.getAttribute("src") !== ENTRY) frame.setAttribute("src", ENTRY);
    frame.style.display = "block";
    if (boot) boot.style.display = "none";
  }

  window.initCanvasStudioView = initCanvasStudioView;
})();
