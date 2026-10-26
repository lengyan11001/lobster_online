/* 灵感画布：项目/灵感/生成 走设计页，编辑器走原 canvas-web SPA。 */
(function () {
  "use strict";
  var SCREENS = {
    inspiration: "/static/canvas-ui/inspiration.html?v=20261026-canvas-ui-v12",
    generate: "/static/canvas-ui/generate.html?v=20261026-canvas-ui-v12",
    projects: "/static/canvas-ui/projects.html?v=20261026-canvas-ui-v12"
  };
  function initCanvasStudioView(mount) {
    var shell = (mount && mount.querySelector && mount.querySelector(".canvas-studio-shell"))
      || document.getElementById("lobsterCanvasStudioShell") || document.querySelector(".canvas-studio-shell");
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
    show("projects");
  }
  window.initCanvasStudioView = initCanvasStudioView;
})();
