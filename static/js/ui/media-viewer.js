/* 统一媒体预览/下载：所有记录里的图片、视频都走本机同源代理，
   避免跨域 CDN 直接 <img>/<video> 打不开、点下载变空白页。 */
(function () {
  if (window.LobsterMedia) return;

  var LOCAL_HOSTS = ['127.0.0.1', 'localhost'];
  function localBase() {
    return String((typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) || window.__LOCAL_API_BASE || '').replace(/\/$/, '');
  }
  function isLocalOrRelative(url) {
    var u = String(url || '').trim();
    if (!u) return true;
    if (u.indexOf('data:') === 0 || u.indexOf('blob:') === 0) return true;
    if (u.charAt(0) === '/') return true;
    try {
      var h = new URL(u, location.href).hostname;
      return LOCAL_HOSTS.indexOf(h) >= 0 || h === location.hostname;
    } catch (e) { return false; }
  }
  function proxyUrl(url, inline) {
    var u = String(url || '').trim();
    if (!u || isLocalOrRelative(u)) return u;
    return localBase() + '/api/canvas-local/proxy-download?inline=' + (inline ? 1 : 0) + '&url=' + encodeURIComponent(u);
  }
  function fileName(url) {
    try {
      var p = new URL(url, location.href).pathname.split('/').pop() || 'download';
      return decodeURIComponent(p.split('?')[0]) || 'download';
    } catch (e) { return 'download'; }
  }
  function isVideo(url) {
    return /\.(mp4|mov|webm|mkv)$/i.test(String(url || '').split('?')[0]);
  }

  function view(url) { return proxyUrl(url, true); }
  function download(url, name) {
    var a = document.createElement('a');
    a.href = proxyUrl(url, false);
    a.download = name || fileName(url);
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function closeLightbox() {
    var m = document.getElementById('lobsterMediaLightbox');
    if (m) m.remove();
    document.removeEventListener('keydown', onKey);
  }
  function onKey(e) { if (e.key === 'Escape') closeLightbox(); }

  function open(url, opts) {
    opts = opts || {};
    var src = view(url);
    var raw = String(url || '');
    closeLightbox();
    var mask = document.createElement('div');
    mask.id = 'lobsterMediaLightbox';
    mask.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.82);z-index:9999;display:flex;align-items:center;justify-content:center;padding:28px;';
    var media = isVideo(raw)
      ? '<video src="' + src + '" controls autoplay playsinline style="max-width:100%;max-height:82vh;border-radius:10px;background:#000;"></video>'
      : '<img src="' + src + '" alt="preview" style="max-width:100%;max-height:82vh;border-radius:10px;background:#fff;">';
    mask.innerHTML = '<div style="display:flex;flex-direction:column;gap:12px;align-items:center;">' +
      media +
      '<div style="display:flex;gap:10px;">' +
      '<button type="button" data-lm-download style="border:0;border-radius:8px;padding:8px 16px;background:#2563eb;color:#fff;cursor:pointer;">下载</button>' +
      '<button type="button" data-lm-close style="border:0;border-radius:8px;padding:8px 16px;background:#e2e8f0;color:#334155;cursor:pointer;">关闭</button>' +
      '</div></div>';
    document.body.appendChild(mask);
    mask.addEventListener('click', function (e) { if (e.target === mask) closeLightbox(); });
    mask.querySelector('[data-lm-close]').addEventListener('click', closeLightbox);
    mask.querySelector('[data-lm-download]').addEventListener('click', function () { download(raw, opts.name); });
    document.addEventListener('keydown', onKey);
  }

  window.LobsterMedia = { view: view, download: download, open: open, proxyUrl: proxyUrl, isVideo: isVideo };
})();