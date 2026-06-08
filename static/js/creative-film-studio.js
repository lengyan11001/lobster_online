(function() {
  var state = {
    docs: [],
    selected: {},
    imageResult: null,
    copies: {}
  };

  function base() {
    return (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE ? String(LOCAL_API_BASE) : '').replace(/\/$/, '');
  }

  function apiUrl(path) {
    return base() + path;
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
    var node = el('creativeFilmMsg');
    if (!node) return;
    node.textContent = text || '';
    node.className = 'msg' + (isErr ? ' err' : '');
    node.style.whiteSpace = 'pre-line';
    node.style.display = text ? 'block' : 'none';
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

  function selectedIds() {
    return Object.keys(state.selected).filter(function(id) { return !!state.selected[id]; });
  }

  function formatSize(size) {
    var n = Number(size || 0);
    if (!n) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }

  function renderMemoryTrigger() {
    var ids = selectedIds();
    var text = el('creativeFilmMemoryTriggerText');
    if (!text) return;
    if (!ids.length) {
      text.textContent = '选择记忆文件';
      return;
    }
    var names = state.docs
      .filter(function(doc) { return ids.indexOf(String(doc.id || '')) >= 0; })
      .map(function(doc) { return doc.title || doc.filename || doc.id; });
    text.textContent = names.length <= 2 ? names.join('、') : ('已选择 ' + names.length + ' 份记忆');
  }

  function renderMemoryList() {
    var list = el('creativeFilmMemoryList');
    if (!list) return;
    if (!state.docs.length) {
      list.innerHTML = '<div class="creative-film-memory-empty">暂无个人记忆。请先到技能商店里的个人记忆上传资料。</div>';
      renderMemoryTrigger();
      return;
    }
    list.innerHTML = state.docs.map(function(doc) {
      var id = String(doc.id || '');
      var title = doc.title || doc.filename || id;
      var meta = [doc.filename || '', formatSize(doc.size), doc.created_at || ''].filter(Boolean).join(' · ');
      var notes = String(doc.notes || '').trim();
      var checked = state.selected[id] ? ' checked' : '';
      return '<label class="creative-film-memory-option">' +
        '<input type="checkbox" data-creative-memory-id="' + escAttr(id) + '"' + checked + '>' +
        '<span><strong>' + esc(title) + '</strong>' +
        (meta ? '<small>' + esc(meta) + '</small>' : '') +
        (notes ? '<em>' + esc(notes) + '</em>' : '') +
        '</span></label>';
    }).join('');
    list.querySelectorAll('[data-creative-memory-id]').forEach(function(input) {
      input.addEventListener('change', function() {
        var id = input.getAttribute('data-creative-memory-id') || '';
        state.selected[id] = !!input.checked;
        renderMemoryTrigger();
      });
    });
    renderMemoryTrigger();
  }

  function loadMemoryDocs() {
    var list = el('creativeFilmMemoryList');
    if (list) list.innerHTML = '<div class="creative-film-memory-empty">正在加载...</div>';
    return fetch(apiUrl('/api/openclaw/memory/list'), { headers: typeof authHeaders === 'function' ? authHeaders() : {} })
      .then(function(resp) { return resp.json().then(function(data) { return { ok: resp.ok, data: data }; }); })
      .then(function(x) {
        if (!x.ok || !x.data || x.data.ok === false) throw new Error((x.data && x.data.detail) || '记忆列表加载失败');
        state.docs = Array.isArray(x.data.documents) ? x.data.documents : [];
        renderMemoryList();
      })
      .catch(function(err) {
        if (list) list.innerHTML = '<div class="creative-film-memory-empty">' + esc(err && err.message ? err.message : '记忆列表加载失败') + '</div>';
      });
  }

  function parseApiError(data, fallback) {
    if (!data) return fallback || '操作失败';
    if (typeof data === 'string') return data;
    var detail = data.detail || data.error || data.message;
    if (typeof detail === 'string') return detail;
    try { return JSON.stringify(detail || data); } catch (e) { return fallback || '操作失败'; }
  }

  function requestJson(path, payload) {
    return fetch(apiUrl(path), {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload || {})
    }).then(function(resp) {
      return resp.json().catch(function() { return {}; }).then(function(data) {
        if (!resp.ok || data.ok === false) throw new Error(parseApiError(data, '请求失败'));
        return data;
      });
    });
  }

  function setImagePreview(data) {
    state.imageResult = data || null;
    var prompt = el('creativeFilmImagePrompt');
    var preview = el('creativeFilmPreview');
    var meta = el('creativeFilmPreviewMeta');
    var open = el('creativeFilmOpenImage');
    var actions = el('creativeFilmPlatformActions');
    var imageUrl = data && ((data.asset && data.asset.source_url) || data.source_url || data.image_url);
    var assetId = data && data.asset && data.asset.asset_id;
    if (prompt) prompt.textContent = (data && data.image_prompt) || '生成后显示。';
    if (meta) {
      meta.textContent = data
        ? ((data.title || '已生成图片') + (assetId ? ' · 已入库 ' + assetId : '') + (data.summary ? ' · ' + data.summary : ''))
        : '等待生成。';
    }
    if (preview) {
      if (imageUrl) {
        preview.innerHTML = '<img src="' + escAttr(imageUrl) + '" alt="创意图片预览">';
      } else {
        preview.innerHTML = '<div class="creative-film-empty">暂无图片</div>';
      }
    }
    if (open) {
      if (imageUrl) {
        open.href = imageUrl;
        open.hidden = false;
      } else {
        open.hidden = true;
      }
    }
    if (actions) actions.hidden = !imageUrl;
  }

  function generateImage() {
    var ids = selectedIds();
    if (!ids.length) {
      setMsg('请选择至少一份记忆资料。', true);
      return;
    }
    var btn = el('creativeFilmGenerateImageBtn');
    setMsg('正在根据记忆生成提示词和图片...', false);
    setBusy(btn, true, '生成中...');
    setImagePreview(null);
    requestJson('/api/creative-film-studio/generate-image', {
      memory_doc_ids: ids,
      goal: el('creativeFilmGoal') ? el('creativeFilmGoal').value.trim() : '',
      image_model: el('creativeFilmImageModel') ? el('creativeFilmImageModel').value : 'gpt-image-2',
      aspect_ratio: el('creativeFilmRatio') ? el('creativeFilmRatio').value : '9:16'
    }).then(function(data) {
      setImagePreview(data);
      setMsg('图片已生成。可以继续生成各平台文案。', false);
    }).catch(function(err) {
      setMsg(err && err.message ? err.message : '图片生成失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function platformTextarea(platform) {
    if (platform === 'douyin') return el('creativeFilmCopyDouyin');
    if (platform === 'wechat') return el('creativeFilmCopyWechat');
    if (platform === 'shipinhao') return el('creativeFilmCopyShipinhao');
    return null;
  }

  function setCopy(platform, text) {
    state.copies[platform] = text || '';
    var ta = platformTextarea(platform);
    if (ta) ta.value = text || '';
    var btn = document.querySelector('[data-copy-platform-btn="' + platform + '"]');
    if (btn) btn.disabled = !text;
  }

  function generateCopy(platform, btn) {
    var ids = selectedIds();
    if (!ids.length) {
      setMsg('请选择至少一份记忆资料。', true);
      return;
    }
    if (!state.imageResult || !state.imageResult.image_prompt) {
      setMsg('请先生成图片，再生成平台文案。', true);
      return;
    }
    setBusy(btn, true, '生成中...');
    setMsg('正在生成平台文案...', false);
    requestJson('/api/creative-film-studio/generate-copy', {
      memory_doc_ids: ids,
      goal: el('creativeFilmGoal') ? el('creativeFilmGoal').value.trim() : '',
      image_prompt: state.imageResult.image_prompt || '',
      image_url: (state.imageResult.asset && state.imageResult.asset.source_url) || state.imageResult.image_url || '',
      platform: platform
    }).then(function(data) {
      var copy = data.copy || {};
      setCopy(platform, copy.full_text || '');
      setMsg((data.platform_label || '平台') + '文案已生成，可直接复制。', false);
    }).catch(function(err) {
      setMsg(err && err.message ? err.message : '文案生成失败', true);
    }).finally(function() {
      setBusy(btn, false);
    });
  }

  function bind() {
    var root = el('content-creative-film-studio');
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';

    var trigger = el('creativeFilmMemoryTrigger');
    var dropdown = el('creativeFilmMemoryDropdown');
    if (trigger && dropdown) {
      trigger.addEventListener('click', function(e) {
        e.stopPropagation();
        dropdown.hidden = !dropdown.hidden;
      });
      document.addEventListener('click', function(e) {
        if (!dropdown.hidden && !e.target.closest('.creative-film-memory-picker')) dropdown.hidden = true;
      });
    }

    var back = el('creativeFilmBackBtn');
    if (back) back.addEventListener('click', function() {
      if (typeof window.showAppView === 'function') window.showAppView('skill-store');
      else location.hash = 'skill-store';
    });
    var refresh = el('creativeFilmRefreshMemoryBtn');
    if (refresh) refresh.addEventListener('click', loadMemoryDocs);
    var generate = el('creativeFilmGenerateImageBtn');
    if (generate) generate.addEventListener('click', generateImage);

    root.querySelectorAll('[data-creative-platform]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        generateCopy(btn.getAttribute('data-creative-platform') || '', btn);
      });
    });
    root.querySelectorAll('[data-copy-platform-btn]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var platform = btn.getAttribute('data-copy-platform-btn') || '';
        var text = state.copies[platform] || (platformTextarea(platform) ? platformTextarea(platform).value : '');
        if (!text) return;
        if (typeof copyToClipboard === 'function') {
          copyToClipboard(text, function() {
            btn.textContent = '已复制';
            setTimeout(function() { btn.textContent = '复制'; }, 1200);
          });
        }
      });
    });
  }

  window.initCreativeFilmStudioView = function() {
    bind();
    return loadMemoryDocs();
  };
})();
