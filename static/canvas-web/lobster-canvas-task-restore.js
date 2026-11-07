(function () {
  "use strict";
  var KEY = "lobster_canvas_pending_tasks";
  var POLL_MS = 5000;
  var MAX_AGE = 24 * 60 * 60 * 1000;
  var nativeFetch = window.fetch ? window.fetch.bind(window) : null;
  var badge = null;
  var timer = null;
  var polling = false;

  function currentUid() {
    try {
      var user = JSON.parse(localStorage.getItem("user_info") || "null");
      return user && user.id ? String(user.id) : "";
    } catch (e) { return ""; }
  }

  function loadTasks() {
    try {
      var list = JSON.parse(localStorage.getItem(KEY) || "[]");
      if (!Array.isArray(list)) return [];
      var now = Date.now();
      var uid = currentUid();
      return list.filter(function (item) {
        return item && item.id && now - Number(item.at || 0) < MAX_AGE
          && (!uid || !item.uid || String(item.uid) === uid);
      });
    } catch (e) { return []; }
  }

  function saveTasks(list) {
    try { localStorage.setItem(KEY, JSON.stringify((list || []).slice(-10))); } catch (e) {}
  }

  function findValue(obj, keys, depth) {
    if (!obj || typeof obj !== "object" || depth > 6) return "";
    for (var i = 0; i < keys.length; i += 1) {
      var value = obj[keys[i]];
      if (typeof value === "string" && value.trim()) return value.trim();
      if (typeof value === "number") return String(value);
    }
    for (var k in obj) {
      if (!Object.prototype.hasOwnProperty.call(obj, k)) continue;
      var found = findValue(obj[k], keys, depth + 1);
      if (found) return found;
    }
    return "";
  }

  function taskIdFrom(data) { return findValue(data, ["task_id", "taskId"], 0); }
  function statusFrom(data) { return findValue(data, ["status", "state", "task_status"], 0).toLowerCase(); }
  function isDone(status) { return ["success", "succeeded", "succeed", "completed", "complete", "done", "3"].indexOf(status) >= 0; }
  function isFailed(status) { return ["failed", "failure", "error", "cancelled", "canceled", "4"].indexOf(status) >= 0; }
  function kindFrom(url) { if (/\/v2\//.test(url)) return "v2"; if (/\/fal\//.test(url)) return "fal"; return "v3"; }
  function queryPath(kind) {
    if (kind === "v2") return "/canvas-api/api/v2/tasks/info";
    if (kind === "fal") return "/canvas-api/api/fal/tasks/info";
    return "/canvas-api/api/v3/tasks/query";
  }
  function authHeaders() {
    var headers = { "Content-Type": "application/json", accept: "application/json" };
    try {
      var user = JSON.parse(localStorage.getItem("user_info") || "null");
      if (user && user.token) headers.Authorization = "Bearer " + user.token;
    } catch (e) {}
    return headers;
  }
  function addTask(id, kind) {
    var list = loadTasks();
    if (!list.some(function (item) { return item.id === id; })) {
      list.push({ id: id, kind: kind, uid: currentUid(), at: Date.now(), status: "processing" });
      saveTasks(list);
    }
    render();
    if (!polling && !timer) timer = setTimeout(poll, 1000);
  }
  function updateTask(id, status) {
    var list = loadTasks();
    list.forEach(function (item) { if (item.id === id) item.status = status; });
    saveTasks(list);
    render();
  }
  function removeTask(id) {
    saveTasks(loadTasks().filter(function (item) { return item.id !== id; }));
    render();
  }

  function ensureBadge() {
    if (badge) return badge;
    if (!document.body) return null;
    badge = document.createElement("div");
    badge.id = "lobster-canvas-task-badge";
    badge.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483000;background:#1f2430;color:#fff;border:1px solid rgba(255,255,255,.18);border-radius:10px;padding:10px 14px;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.35);display:none;align-items:center;gap:10px;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;";
    document.body.appendChild(badge);
    return badge;
  }

  function render() {
    var node = ensureBadge();
    if (!node) return;
    var list = loadTasks();
    if (!list.length) { node.style.display = "none"; node.innerHTML = ""; return; }
    var pending = list.filter(function (item) { return !isDone(item.status) && !isFailed(item.status); });
    var done = list.filter(function (item) { return isDone(item.status); });
    node.style.display = "flex";
    var text = pending.length ? "画布任务生成中…（" + pending.length + "）" : (done.length ? "画布任务已完成" : "画布任务失败");
    node.innerHTML = '<span>' + text + "</span>";
    var action = document.createElement("button");
    action.type = "button";
    action.style.cssText = "border:0;border-radius:6px;padding:4px 10px;cursor:pointer;background:#4b8cff;color:#fff;font-size:12px;";
    if (pending.length) {
      action.textContent = "后台处理中";
      action.disabled = true;
      action.style.opacity = "0.65";
    } else if (done.length) {
      action.textContent = "刷新查看";
      action.onclick = function () { try { localStorage.removeItem(KEY); } catch (e) {} location.reload(); };
    } else {
      action.textContent = "关闭";
      action.onclick = function () { try { localStorage.removeItem(KEY); } catch (e) {} render(); };
    }
    node.appendChild(action);
  }

  async function poll() {
    if (polling || !nativeFetch) return;
    polling = true;
    try {
      var list = loadTasks().filter(function (item) { return !isDone(item.status) && !isFailed(item.status); });
      for (var i = 0; i < list.length; i += 1) {
        var item = list[i];
        try {
          var response = await nativeFetch(queryPath(item.kind), {
            method: "POST",
            headers: authHeaders(),
            body: JSON.stringify({ task_id: item.id, taskId: item.id })
          });
          var data = await response.json();
          var status = statusFrom(data);
          if (isDone(status) || isFailed(status)) updateTask(item.id, status);
        } catch (e) {}
      }
    } finally {
      polling = false;
    }
    render();
    var stillRunning = loadTasks().some(function (item) { return !isDone(item.status) && !isFailed(item.status); });
    if (stillRunning) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(poll, POLL_MS);
    }
  }

  function installFetchHook() {
    if (!nativeFetch || nativeFetch.__lobsterCanvasHook) return;
    var wrapped = function (input, init) {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
      var promise = nativeFetch.apply(window, arguments);
      if (method === "POST" && /\/tasks\/create/.test(url)) {
        promise.then(function (response) {
          try {
            response.clone().json().then(function (data) {
              var id = taskIdFrom(data);
              if (id) addTask(id, kindFrom(url));
            }).catch(function () {});
          } catch (e) {}
        }).catch(function () {});
      }
      return promise;
    };
    wrapped.__lobsterCanvasHook = true;
    window.fetch = wrapped;
  }

  function start() {
    ensureBadge();
    render();
    poll();
  }

  installFetchHook();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();