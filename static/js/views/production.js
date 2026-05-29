/* production.js — 生成历史：按 task_id 聚合，并支持手动查询进度 */
(function () {
  const PAGE_SIZE = 20;
  let _offset = 0;
  let _total = 0;
  let _initialized = false;
  let _lastRefreshTaskId = "";

  function _localBase() {
    return ((typeof LOCAL_API_BASE !== "undefined" && LOCAL_API_BASE) ? LOCAL_API_BASE : "").replace(/\/$/, "");
  }

  function _headers() {
    const h = typeof authHeaders === "function" ? Object.assign({}, authHeaders()) : { Authorization: "Bearer " + (localStorage.getItem("token") || "") };
    h["Content-Type"] = "application/json";
    return h;
  }

  function _esc(s) {
    if (s == null) return "";
    const d = document.createElement("div");
    d.textContent = String(s);
    return d.innerHTML;
  }

  function _formatTime(iso) {
    if (!iso) return "-";
    const s = String(iso).trim();
    const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + "Z");
    if (isNaN(d.getTime())) return s.slice(0, 19).replace("T", " ");
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function _statusMeta(status, success) {
    const s = String(status || "").toLowerCase();
    if (s.includes("fail") || s.includes("error") || s.includes("失败") || success === false) {
      return { text: status || "失败", cls: "failed", bg: "rgba(239,68,68,0.15)", fg: "var(--error)" };
    }
    if (s.includes("complete") || s.includes("success") || s.includes("done") || s.includes("完成")) {
      return { text: status || "完成", cls: "done", bg: "rgba(16,185,129,0.14)", fg: "var(--success)" };
    }
    if (s.includes("process") || s.includes("running") || s.includes("pending") || s.includes("queue") || s.includes("生成")) {
      return { text: status || "生成中", cls: "running", bg: "rgba(59,130,246,0.14)", fg: "var(--primary)" };
    }
    return { text: status || "已提交", cls: "submitted", bg: "rgba(6,182,212,0.14)", fg: "var(--accent)" };
  }

  function _shortTaskId(tid) {
    const s = String(tid || "");
    if (s.length <= 18) return s;
    return s.slice(0, 8) + "..." + s.slice(-6);
  }

  function _selectorEscape(s) {
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(s);
    return String(s || "").replace(/["\\]/g, "\\$&");
  }

  function _renderLinks(item) {
    const pieces = [];
    const seen = {};
    (item.saved_assets || []).forEach(function (a) {
      const aid = a && a.asset_id ? String(a.asset_id) : "";
      const url = a && (a.url || a.source_url) ? String(a.url || a.source_url) : "";
      const key = aid || url;
      if (!key || seen[key]) return;
      seen[key] = true;
      pieces.push(
        '<span class="gen-history-chip">' +
        (aid ? '素材 ' + _esc(aid) : '素材') +
        (url ? ' · <a href="' + _esc(url) + '" target="_blank" rel="noopener">打开</a>' : '') +
        '</span>'
      );
    });
    (item.media_urls || []).forEach(function (url) {
      const u = String(url || "");
      if (!u || seen[u]) return;
      seen[u] = true;
      pieces.push('<a class="gen-history-chip" href="' + _esc(u) + '" target="_blank" rel="noopener">预览链接</a>');
    });
    return pieces.length ? '<div class="gen-history-links">' + pieces.join("") + '</div>' : "";
  }

  function _renderLogs(item) {
    const logs = (item.logs || []).slice(-4).reverse();
    if (!logs.length) return "";
    const rows = logs.map(function (l) {
      const st = l.status || (l.success === false ? "失败" : "");
      return '<div class="gen-history-log-row"><span>' + _formatTime(l.created_at) + '</span><span>' +
        _esc(l.capability_id || "") + '</span><span>' + _esc(st) + '</span></div>';
    }).join("");
    return '<details class="gen-history-logs"><summary>调用记录</summary>' + rows + '</details>';
  }

  function _renderCard(item) {
    const status = _statusMeta(item.status, item.success);
    const taskId = item.task_id || "";
    const prompt = (item.prompt || "").trim();
    const model = (item.model || "").trim();
    const err = item.error_message ? '<div class="gen-history-error">' + _esc(item.error_message) + '</div>' : "";
    return `
      <div class="card gen-history-card" data-task-id="${_esc(taskId)}">
        <div class="gen-history-head">
          <div class="gen-history-title-wrap">
            <span class="gen-history-badge" style="background:${status.bg};color:${status.fg};">${_esc(status.text)}</span>
            <strong>${_esc(item.capability_id || "生成任务")}</strong>
          </div>
          <div class="gen-history-time">${_formatTime(item.updated_at || item.created_at)}</div>
        </div>
        <div class="gen-history-meta">
          ${taskId ? `<span title="${_esc(taskId)}">task_id：${_esc(_shortTaskId(taskId))}</span>` : '<span>task_id：-</span>'}
          ${model ? `<span>模型：${_esc(model)}</span>` : ""}
          ${item.credits_charged != null ? `<span>算力：${_esc(item.credits_charged)}</span>` : ""}
        </div>
        ${prompt ? `<div class="gen-history-prompt">${_esc(prompt)}</div>` : ""}
        ${err}
        ${_renderLinks(item)}
        <div class="gen-history-actions">
          ${taskId ? `<button type="button" class="btn btn-primary btn-sm" data-gen-history-refresh="${_esc(taskId)}" data-cap="${_esc(item.capability_id || "")}">查询进度</button>` : ""}
          ${taskId ? `<button type="button" class="btn btn-ghost btn-sm" data-gen-history-copy="${_esc(taskId)}">复制 task_id</button>` : ""}
        </div>
        <div class="gen-history-refresh-result" data-gen-history-result="${_esc(taskId)}" style="display:none;"></div>
        ${_renderLogs(item)}
      </div>`;
  }

  function _renderPagination() {
    const el = document.getElementById("prodPagination");
    if (!el) return;
    if (_total <= PAGE_SIZE) {
      el.innerHTML = _total ? '<span class="meta">共 ' + _total + ' 个生成任务</span>' : "";
      return;
    }
    const totalPages = Math.max(1, Math.ceil(_total / PAGE_SIZE));
    const curPage = Math.floor(_offset / PAGE_SIZE) + 1;
    el.innerHTML =
      '<button class="btn btn-ghost btn-sm" ' + (curPage <= 1 ? "disabled" : "") + ' id="prodPrev">上一页</button>' +
      '<span class="meta">' + curPage + ' / ' + totalPages + '（共 ' + _total + ' 个任务）</span>' +
      '<button class="btn btn-ghost btn-sm" ' + (curPage >= totalPages ? "disabled" : "") + ' id="prodNext">下一页</button>';
    const prev = document.getElementById("prodPrev");
    const next = document.getElementById("prodNext");
    if (prev) prev.onclick = function () { _offset = Math.max(0, _offset - PAGE_SIZE); _loadLogs(); };
    if (next) next.onclick = function () { _offset += PAGE_SIZE; _loadLogs(); };
  }

  async function _loadLogs() {
    const list = document.getElementById("prodList");
    if (!list) return;
    const base = _localBase();
    const qs = new URLSearchParams({ limit: PAGE_SIZE, offset: _offset });
    list.innerHTML = '<p class="meta" style="text-align:center;padding:2rem;">加载中...</p>';
    try {
      const res = await fetch(base + "/api/generation-history?" + qs.toString(), { headers: _headers() });
      const data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || data.message || "加载失败");
      const items = data.items || [];
      _total = data.total || items.length || 0;
      if (!items.length) {
        const extra = data.remote_error ? '<br><span class="err">' + _esc(data.remote_error) + '</span>' : "";
        list.innerHTML = '<p class="meta" style="text-align:center;padding:2rem;">暂无生成历史。对话里提交 image.generate / video.generate 后会显示在这里。' + extra + '</p>';
      } else {
        const warn = data.remote_error ? '<p class="msg err" style="display:block;margin:0 0 0.6rem 0;">' + _esc(data.remote_error) + '</p>' : "";
        list.innerHTML = warn + items.map(_renderCard).join("");
      }
      _renderPagination();
    } catch (e) {
      list.innerHTML = '<p class="msg err" style="display:block;text-align:center;padding:2rem;">' + _esc(e.message || "加载失败") + '</p>';
    }
  }

  function _renderRefreshResult(taskId, data) {
    const box = document.querySelector('[data-gen-history-result="' + _selectorEscape(taskId) + '"]');
    if (!box) return;
    const status = _statusMeta(data.status, data.ok);
    const links = _renderLinks({ media_urls: data.media_urls || [], saved_assets: data.saved_assets || [] });
    const text = data.result_text ? '<pre class="gen-history-result-text">' + _esc(data.result_text).slice(0, 1600) + '</pre>' : "";
    box.style.display = "block";
    box.innerHTML = '<div class="gen-history-refresh-head"><span class="gen-history-badge" style="background:' + status.bg + ';color:' + status.fg + ';">' +
      _esc(status.text) + '</span><span>刚刚查询</span></div>' + links + text;
  }

  async function _refreshTask(btn) {
    const taskId = btn.getAttribute("data-gen-history-refresh") || "";
    if (!taskId) return;
    _lastRefreshTaskId = taskId;
    btn.disabled = true;
    const oldText = btn.textContent;
    btn.textContent = "查询中...";
    try {
      const res = await fetch(_localBase() + "/api/generation-history/refresh", {
        method: "POST",
        headers: _headers(),
        body: JSON.stringify({ task_id: taskId, origin_capability_id: btn.getAttribute("data-cap") || "" }),
      });
      const data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || data.message || "查询失败");
      _renderRefreshResult(taskId, data);
      setTimeout(function () { if (_lastRefreshTaskId === taskId) _loadLogs(); }, 800);
    } catch (e) {
      const box = document.querySelector('[data-gen-history-result="' + _selectorEscape(taskId) + '"]');
      if (box) {
        box.style.display = "block";
        box.innerHTML = '<p class="msg err" style="display:block;margin:0;">' + _esc(e.message || "查询失败") + '</p>';
      }
    } finally {
      btn.disabled = false;
      btn.textContent = oldText;
    }
  }

  function _bind() {
    const btn = document.getElementById("prodRefreshBtn");
    if (btn) btn.onclick = function () { _offset = 0; _loadLogs(); };
    const list = document.getElementById("prodList");
    if (list) {
      list.addEventListener("click", function (e) {
        const refreshBtn = e.target && e.target.closest ? e.target.closest("[data-gen-history-refresh]") : null;
        if (refreshBtn) {
          _refreshTask(refreshBtn);
          return;
        }
        const copyBtn = e.target && e.target.closest ? e.target.closest("[data-gen-history-copy]") : null;
        if (copyBtn) {
          const tid = copyBtn.getAttribute("data-gen-history-copy") || "";
          if (typeof copyToClipboard === "function") copyToClipboard(tid, function () { copyBtn.textContent = "已复制"; setTimeout(function () { copyBtn.textContent = "复制 task_id"; }, 1200); });
        }
      });
    }
  }

  window.initProductionView = function () {
    if (!_initialized) {
      _bind();
      _initialized = true;
    }
    _offset = 0;
    _loadLogs();
  };
})();
