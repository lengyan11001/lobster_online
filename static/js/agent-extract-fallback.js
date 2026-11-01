/* AI秘书「提取资料」兜底点击器。
   现有实现绑在 agent.js 的视图初始化里，若该视图没走那次初始化，按钮就完全没反应。
   这里在页面加载时绑定一次全局捕获监听，并用 elementFromPoint 兜住被遮挡的情况。 */
(function () {
  if (window.__lobsterExtractFallbackBound) return;
  window.__lobsterExtractFallbackBound = true;

  function pickButton(ev) {
    var t = ev.target;
    var btn = t && t.closest ? t.closest('[data-agent-extract], .agent-task-card-extract') : null;
    if (btn) return btn;
    try {
      var under = document.elementFromPoint(ev.clientX, ev.clientY);
      return under && under.closest ? under.closest('.agent-task-card-extract') : null;
    } catch (e) { return null; }
  }

  document.addEventListener('click', function (ev) {
    var btn = pickButton(ev);
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    var rid = btn.getAttribute('data-agent-extract') || '';
    console.info('[提取资料][fallback] recordId=%s', rid);
    if (window.LobsterProfileExtract && typeof window.LobsterProfileExtract.open === 'function') {
      try {
        window.LobsterProfileExtract.open({ recordId: rid });
      } catch (err) {
        console.error('[提取资料][fallback] 打开失败', err);
        alert('提取资料失败：' + (err && err.message ? err.message : err));
      }
      return;
    }
    alert('提取组件未加载（agent.js 未初始化），请刷新或重启客户端后重试');
  }, true);
})();