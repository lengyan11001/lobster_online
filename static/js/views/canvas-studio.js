/* 灵感画布宿主：顶部三个入口（灵感 / 生成 / 画布），内容用同源 iframe 加载 designer 版界面。
 * 「进入画布」在 iframe 内直接切到 canvas-web 编辑器。 */
(function () {
  "use strict";

  var TABS = {
    inspiration: "/static/canvas-ui/inspiration.html?v=20261026-canvas-ui-v2",
    generate: "/static/canvas-ui/generate.html?v=20261026-canvas-ui-v2",
    projects: "/static/canvas-ui/projects.html?v=20261026-canvas-ui-v2"
  };
  var DEFAULT_TAB = "projects";

  function initCanvasStudioView(mount) {
    var shell = (mount && mount.querySelector && mount.querySelector(".canvas-studio-shell"))
      || document.querySelector(".canvas-studio-shell");
    if (!shell) return;
    var frame = shell.querySelector("iframe");
    var boot = shell.querySelector(".canvas-studio-boot");
    var buttons = Array.prototype.slice.call(shell.querySelectorAll("[data-canvas-tab]"));
    if (!frame) return;

    function activate(key) {
      var tab = TABS[key] ? key : DEFAULT_TAB;
      if (frame.getAttribute("src") !== TABS[tab]) frame.setAttribute("src", TABS[tab]);
      frame.style.display = "block";
      if (boot) boot.style.display = "none";
      buttons.forEach(function (btn) {
        var on = btn.getAttribute("data-canvas-tab") === tab;
        btn.style.background = on ? "#111827" : "#fff";
        btn.style.color = on ? "#fff" : "#4b5563";
        btn.style.borderColor = on ? "#111827" : "#e5e7eb";
      });
    }

    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () { activate(btn.getAttribute("data-canvas-tab")); });
    });
    window.__lobsterCanvasStudioActivate = activate;
    activate(DEFAULT_TAB);
  }

  window.initCanvasStudioView = initCanvasStudioView;
})();
