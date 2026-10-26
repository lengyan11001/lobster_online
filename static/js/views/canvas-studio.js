/* 灵感画布：项目/灵感/生成 走设计页，编辑器走原 canvas-web SPA。 */
(function () {
  "use strict";
  var SCREENS = {
    inspiration: "/static/canvas-ui/inspiration.html?v=20261026-canvas-ui-v18",
    // 生成页沿用 canvas-web 原生的 GeneratePage（模型/参数/上传/提交/历史全是原逻辑），
    // 只由 canvas-theme.css 覆盖样式；不再用自绘的 generate.html。
    generate: "/api/canvas-local/canvas-frame#/generate",
    projects: "/static/canvas-ui/projects.html?v=20261026-canvas-ui-v18"
  };
  function initCanvasStudioView(mount) {
    var shell = (mount && mount.querySelector && mount.querySelector(".canvas-studio-shell"))
      || document.getElementById("lobsterCanvasStudioShell") || document.querySelector(".canvas-studio-shell");
    if (!shell) return;
    var frame = shell.querySelector("iframe");
    var boot = shell.querySelector(".canvas-studio-boot");
    if (!frame) return;
    var currentKey = "projects";
    var editorBackKey = "";
    var editorActive = false;
    function show(key) {
      var src = SCREENS[key] || SCREENS.projects;
      currentKey = key;
      editorActive = false;
      if (frame.getAttribute("src") !== src) frame.setAttribute("src", src);
      frame.style.display = "block";
      if (boot) boot.style.display = "none";
      Array.prototype.forEach.call(shell.querySelectorAll("[data-canvas-screen]"), function (btn) {
        btn.classList.toggle("active", btn.getAttribute("data-canvas-screen") === key);
      });
    }
    // 设计页里点「做同款 / 打开画布」不自己跳转，交给宿主切 iframe；
    // 编辑器里点返回（SPA 会回到 #/ 的套娃灵感页）时，切回原来的设计页。
    window.addEventListener("message", function (e) {
      if (frame.contentWindow && e.source && e.source !== frame.contentWindow) return;
      var data = e.data || {};
      if (data.type === "canvas:open-editor" && data.uuid) {
        editorBackKey = currentKey;
        editorActive = true;
        frame.setAttribute("src", "/api/canvas-local/canvas-frame#/canvas-editor/" + encodeURIComponent(data.uuid));
        frame.style.display = "block";
      } else if (data.type === "canvas:route") {
        if (!editorActive) return;
        var hash = String(data.hash || "");
        if (hash.indexOf("#/canvas-editor/") !== 0) {
          editorActive = false;
          show(editorBackKey || "projects");
        }
      }
    });
    Array.prototype.forEach.call(shell.querySelectorAll("[data-canvas-screen]"), function (btn) {
      btn.onclick = function () { show(btn.getAttribute("data-canvas-screen")); };
    });
    show("projects");
  }
  window.initCanvasStudioView = initCanvasStudioView;
})();
