(function() {
  var state = { result: null };

  function base() {
    return (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE ? String(LOCAL_API_BASE) : '').replace(/\/$/, '');
  }

  function headers() {
    return Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : {});
  }

  function esc(text) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(text || ''));
    return String(text || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function escAttr(text) {
    if (typeof escapeAttr === 'function') return escapeAttr(String(text || ''));
    return esc(text);
  }

  function el(id) {
    return document.getElementById(id);
  }

  function setMsg(text, isErr) {
    var box = el('pptStudioMsg');
    if (!box) return;
    box.textContent = text || '';
    box.className = 'ppt-studio-msg' + (isErr ? ' err' : '');
    box.style.display = text ? 'block' : 'none';
  }

  function setBusy(btn, busy, text) {
    if (!btn) return;
    if (busy) {
      btn.dataset.oldText = btn.textContent || '';
      btn.textContent = text || '处理中...';
      btn.disabled = true;
    } else {
      btn.textContent = btn.dataset.oldText || btn.textContent || '';
      btn.disabled = false;
    }
  }

  function parseError(data, fallback) {
    if (!data) return fallback || '请求失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.error || data.message;
    if (typeof detail === 'string') return detail;
    try { return JSON.stringify(detail || data); } catch (e) { return fallback || '请求失败'; }
  }

  function buildPayload() {
    var topic = (el('pptStudioTopic') ? el('pptStudioTopic').value : '').trim();
    var prompt = (el('pptStudioPrompt') ? el('pptStudioPrompt').value : '').trim();
    if (!topic) throw new Error('请先填写 PPT 主题。');
    return {
      payload: {
        action: 'run_pipeline',
        topic: topic,
        prompt: prompt || topic,
        slide_count: Number((el('pptStudioSlideCount') || {}).value || 10),
        theme: String((el('pptStudioTheme') || {}).value || 'business'),
        audience: String((el('pptStudioAudience') || {}).value || 'business'),
        language: 'zh-CN',
        style: prompt || 'professional, clear, modern business presentation',
        planning_model: String((el('pptStudioPlanningModel') || {}).value || 'gpt-5.4'),
        image_model: String((el('pptStudioImageModel') || {}).value || 'gpt-image-2'),
        image_quality: String((el('pptStudioImageQuality') || {}).value || 'high'),
        image_background: String((el('pptStudioImageBackground') || {}).value || 'opaque'),
        aspect_ratio: String((el('pptStudioAspectRatio') || {}).value || '16:9'),
        generate_images: String((el('pptStudioImageMode') || {}).value || 'true') !== 'false'
      }
    };
  }

  function outlineItems(result) {
    var outline = result && result.outline;
    var slides = outline && Array.isArray(outline.slides) ? outline.slides : [];
    return slides.slice(0, 8).map(function(item, index) {
      var title = item && (item.title || item.claim) ? String(item.title || item.claim) : ('第 ' + (index + 1) + ' 页');
      return '<li>' + esc(title) + '</li>';
    }).join('');
  }

  function collectSlideBullets(item) {
    var out = [];
    var elements = item && Array.isArray(item.elements) ? item.elements : [];
    elements.forEach(function(entry) {
      if (!entry) return;
      var text = '';
      if (typeof entry === 'string') text = entry;
      else if (typeof entry === 'object') text = entry.text || entry.title || entry.content || '';
      text = String(text || '').trim();
      if (text) out.push(text);
    });
    if (!out.length && Array.isArray(item && item.bullets)) {
      item.bullets.forEach(function(entry) {
        var text = String(entry || '').trim();
        if (text) out.push(text);
      });
    }
    return out.slice(0, 5);
  }

  function slideTypeLabel(type) {
    var key = String(type || '').toLowerCase();
    var labels = {
      title: '封面页',
      section: '章节页',
      content: '内容页',
      two_column: '双栏页',
      chart: '图表页',
      table: '表格页',
      quote: '金句页',
      ending: '结束页',
      data: '数据页',
      comparison: '对比页',
      process: '流程页'
    };
    return labels[key] || '内容页';
  }

  function slideKind(type) {
    var key = String(type || '').toLowerCase();
    return (key === 'title' || key === 'section') ? key : 'content';
  }

  function renderSlideCards(result) {
    var renderPlan = result && result.render_meta && result.render_meta.plan;
    var slides = renderPlan && Array.isArray(renderPlan.slides) && renderPlan.slides.length
      ? renderPlan.slides
      : ((result && result.outline && Array.isArray(result.outline.slides)) ? result.outline.slides : []);
    if (!slides.length) return '';

    var cards = slides.map(function(item, index) {
      item = item || {};
      var typeValue = item.slide_type || item.layout;
      var kind = slideKind(typeValue);
      var title = String(item.title || item.claim || ('第 ' + (index + 1) + ' 页')).trim();
      var subtitle = String(item.subtitle || '').trim();
      var claim = String(item.claim || item.takeaway || '').trim();
      var bullets = collectSlideBullets(item);
      var metrics = Array.isArray(item.metrics) ? item.metrics.slice(0, 3) : [];
      var bulletHtml = bullets.length
        ? '<ol class="ppt-studio-slide-bullets">' + bullets.map(function(text) { return '<li>' + esc(text) + '</li>'; }).join('') + '</ol>'
        : '';
      var metricsHtml = metrics.length
        ? '<div class="ppt-studio-slide-metrics">' + metrics.map(function(text) { return '<span class="ppt-studio-slide-metric-pill">' + esc(String(text || '').trim()) + '</span>'; }).join('') + '</div>'
        : '';

      return [
        '<article class="ppt-studio-slide-card">',
        '<div class="ppt-studio-slide-frame" data-slide-kind="' + escAttr(kind) + '">',
        '<div class="ppt-studio-slide-chip">' + esc(slideTypeLabel(typeValue)) + '</div>',
        '<div class="ppt-studio-slide-title">' + esc(title) + '</div>',
        subtitle ? '<div class="ppt-studio-slide-subtitle">' + esc(subtitle) + '</div>' : '',
        claim ? '<div class="ppt-studio-slide-claim">' + esc(claim) + '</div>' : '',
        bulletHtml,
        metricsHtml,
        '</div>',
        '<div class="ppt-studio-slide-foot"><span class="ppt-studio-slide-index">第 ' + esc(String(index + 1)) + ' 页</span><span class="ppt-studio-slide-type">' + esc(String(item.visual_style || item.layout || typeValue || '').trim() || 'standard') + '</span></div>',
        '</article>'
      ].join('');
    }).join('');

    return [
      '<section class="ppt-studio-deck">',
      '<div class="ppt-studio-deck-head">',
      '<div class="ppt-studio-deck-title">逐页预览</div>',
      '<div class="ppt-studio-deck-subtitle">这里展示本次生成的每一页结构与核心内容，方便你直接判断整套 PPT 是否可用。</div>',
      '</div>',
      '<div class="ppt-studio-slide-grid">' + cards + '</div>',
      '</section>'
    ].join('');
  }

  function renderResult(result) {
    state.result = result || null;
    var host = el('pptStudioResult');
    if (!host) return;

    if (!result) {
      host.className = 'ppt-studio-result-empty';
      host.innerHTML = '还没有生成结果。<br>左侧填好主题后，直接点“生成 PPT”就可以了。';
      return;
    }

    var topic = String((result.outline && result.outline.title) || result.title || result.topic || 'PPT 已生成').trim();
    var subtitle = String((result.prompt || result.message || '')).trim();
    var slides = result.outline && Array.isArray(result.outline.slides) ? result.outline.slides.length : Number(result.slide_count || 0);
    var engine = String((result.render_meta && result.render_meta.engine) || result.engine || 'ppt_master').trim() || 'ppt_master';
    var assetId = String(result.ppt_asset_id || result.asset_id || (result.asset && result.asset.asset_id) || '').trim();
    var downloadUrl = String(result.download_url || (result.asset && result.asset.source_url) || '').trim();
    var localPath = String(result.pptx_path || '').trim();
    var outlineHtml = outlineItems(result);
    var slidePreviewHtml = renderSlideCards(result);

    host.className = 'ppt-studio-result-card';
    host.innerHTML = [
      '<div class="ppt-studio-result-cover">',
      '<div class="ppt-studio-result-cover-kicker">生成完成</div>',
      '<h4 class="ppt-studio-result-cover-title">' + esc(topic) + '</h4>',
      subtitle ? '<div class="ppt-studio-result-cover-subtitle">' + esc(subtitle) + '</div>' : '',
      '</div>',
      '<div class="ppt-studio-metrics">',
      '<div class="ppt-studio-metric"><strong>' + esc(String(slides || '--')) + '</strong><span>生成页数</span></div>',
      '<div class="ppt-studio-metric"><strong>' + esc(engine) + '</strong><span>生成引擎</span></div>',
      '<div class="ppt-studio-metric"><strong>' + esc(assetId || '--') + '</strong><span>素材编号</span></div>',
      '</div>',
      '<div class="ppt-studio-actions">',
      downloadUrl ? '<a class="btn btn-primary btn-sm" href="' + escAttr(downloadUrl) + '" target="_blank" rel="noopener">下载 PPT</a>' : '',
      downloadUrl ? '<a class="btn btn-ghost btn-sm" href="' + escAttr(downloadUrl) + '" target="_blank" rel="noopener">打开结果</a>' : '',
      '<button type="button" class="btn btn-ghost btn-sm" id="pptStudioRegenerateBtn">再生成一版</button>',
      '</div>',
      slidePreviewHtml,
      outlineHtml ? '<div class="ppt-studio-outline"><div class="ppt-studio-outline-title">页面结构</div><ol class="ppt-studio-outline-list">' + outlineHtml + '</ol></div>' : '',
      '<dl class="ppt-studio-meta-list">',
      assetId ? '<div class="ppt-studio-meta-row"><dt>素材库</dt><dd>已同步到素材库，可直接下载使用</dd></div>' : '',
      localPath ? '<div class="ppt-studio-meta-row"><dt>本地路径</dt><dd>' + esc(localPath) + '</dd></div>' : '',
      '<div class="ppt-studio-meta-row"><dt>生成说明</dt><dd>右侧已按页展示本次 PPT 的结构与内容，先确认思路，再决定是否下载或重新生成。</dd></div>',
      '</dl>'
    ].join('');

    var regen = el('pptStudioRegenerateBtn');
    if (regen) {
      regen.addEventListener('click', function() {
        var btn = el('pptStudioGenerateBtn');
        if (btn) btn.click();
      });
    }
  }

  function generate() {
    var btn = el('pptStudioGenerateBtn');
    var payload;
    try {
      payload = buildPayload();
    } catch (err) {
      setMsg(err && err.message ? err.message : '参数不完整', true);
      return;
    }

    setBusy(btn, true, '生成中...');
    setMsg('正在生成 PPT，请稍等...', false);

    fetch(base() + '/api/create-ppt/pipeline/run', {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload)
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok) throw new Error(parseError(data, 'PPT 生成失败'));
        return data || {};
      });
    }).then(function(data) {
      renderResult(data);
      setMsg('PPT 已生成完成。右侧现在会直接展示逐页结果。', false);
    }).catch(function(err) {
      renderResult(null);
      setMsg(err && err.message ? err.message : 'PPT 生成失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function resetForm() {
    if (el('pptStudioTopic')) el('pptStudioTopic').value = '';
    if (el('pptStudioPrompt')) el('pptStudioPrompt').value = '';
    if (el('pptStudioSlideCount')) el('pptStudioSlideCount').value = '10';
    if (el('pptStudioTheme')) el('pptStudioTheme').value = 'business';
    if (el('pptStudioAudience')) el('pptStudioAudience').value = 'business';
    if (el('pptStudioImageMode')) el('pptStudioImageMode').value = 'true';
    if (el('pptStudioPlanningModel')) el('pptStudioPlanningModel').value = 'gpt-5.4';
    if (el('pptStudioImageModel')) el('pptStudioImageModel').value = 'gpt-image-2';
    if (el('pptStudioImageQuality')) el('pptStudioImageQuality').value = 'high';
    if (el('pptStudioImageBackground')) el('pptStudioImageBackground').value = 'opaque';
    if (el('pptStudioAspectRatio')) el('pptStudioAspectRatio').value = '16:9';
    renderResult(null);
    setMsg('', false);
  }

  function bind() {
    var root = el('content-ppt-studio');
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';

    var back = el('pptStudioBackBtn');
    if (back) {
      back.addEventListener('click', function() {
        if (typeof window.showAppView === 'function') window.showAppView('chat');
      });
    }

    var reset = el('pptStudioResetBtn');
    if (reset) reset.addEventListener('click', resetForm);

    var generateBtn = el('pptStudioGenerateBtn');
    if (generateBtn) generateBtn.addEventListener('click', generate);
  }

  window.initPptStudioView = function() {
    bind();
    renderResult(state.result);
    return Promise.resolve();
  };
})();
