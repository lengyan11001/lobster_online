(function() {
  var state = {
    generated: null,
    selectedImages: [],
    assetPickerItems: [],
    assetPickerSelected: {}
  };

  function apiBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? LOCAL_API_BASE : '') || '';
  }

  function apiUrl(path) {
    var base = apiBase().replace(/\/$/, '');
    return (base ? base : '') + path;
  }

  function hdrs() {
    return Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : {});
  }

  function authOnlyHeaders() {
    var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders()) : {};
    delete h['Content-Type'];
    delete h['content-type'];
    return h;
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, function(ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function showMsg(text, isErr) {
    var el = document.getElementById('wechatArticleMsg');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'msg' + (isErr ? ' err' : '');
    el.style.whiteSpace = 'pre-line';
    el.style.display = text ? 'block' : 'none';
  }

  function formatApiError(data, fallback) {
    if (!data) return fallback || '操作失败';
    if (typeof data === 'string') return data || fallback || '操作失败';
    var detail = data.detail || data.error || data.message || data.errmsg || data;
    if (typeof detail === 'string') return detail || fallback || '操作失败';
    if (!detail || typeof detail !== 'object') return fallback || '操作失败';

    var parts = [];
    if (detail.message) parts.push(String(detail.message));
    if (detail.errcode !== undefined && detail.errcode !== null && detail.errcode !== '') {
      parts.push('错误码：' + detail.errcode);
    }
    if (detail.errmsg && parts.indexOf(String(detail.errmsg)) === -1) {
      parts.push('微信返回：' + detail.errmsg);
    }
    if (detail.hint) parts.push(String(detail.hint));
    if (!parts.length && detail.raw && typeof detail.raw === 'object') {
      if (detail.raw.errcode !== undefined && detail.raw.errcode !== null) parts.push('错误码：' + detail.raw.errcode);
      if (detail.raw.errmsg) parts.push('微信返回：' + detail.raw.errmsg);
    }
    if (parts.length) return parts.join('\n');
    try {
      return JSON.stringify(detail);
    } catch (err) {
      return fallback || '操作失败';
    }
  }

  function throwApiError(data, fallback) {
    throw new Error(formatApiError(data, fallback));
  }

  function setBusy(btn, busy, text) {
    if (!btn) return;
    if (busy) {
      btn.dataset.oldText = btn.textContent || '';
      btn.textContent = text || '处理中…';
      btn.disabled = true;
    } else {
      btn.textContent = btn.dataset.oldText || btn.textContent || '';
      btn.disabled = false;
    }
  }

  function field(id) {
    return document.getElementById(id);
  }

  function currentTheme() {
    return field('wechatArticleTheme') ? field('wechatArticleTheme').value : 'professional-clean';
  }

  function currentMarkdown() {
    var edit = field('wechatArticleMarkdownEdit');
    if (edit && edit.value.trim()) return edit.value;
    return (state.generated && state.generated.markdown) || '';
  }

  function imageSrc(item) {
    return (item && (item.url || item.preview_url || item.open_url || item.source_url)) || '';
  }

  function addSelectedImages(items) {
    var existing = {};
    state.selectedImages.forEach(function(item) {
      var key = item.asset_id || imageSrc(item);
      if (key) existing[key] = true;
    });
    (items || []).forEach(function(item) {
      var src = imageSrc(item);
      var key = item.asset_id || src;
      if (!src || !key || existing[key]) return;
      existing[key] = true;
      state.selectedImages.push({
        asset_id: item.asset_id || '',
        url: src,
        name: item.name || item.filename || item.asset_id || '图片'
      });
    });
    renderSelectedImages();
  }

  function renderSelectedImages() {
    var el = field('wechatArticleSelectedImages');
    if (!el) return;
    if (!state.selectedImages.length) {
      el.innerHTML = '<p class="meta">可上传图片或选择素材库图片，生成文章时会插入正文。</p>';
      return;
    }
    el.innerHTML = state.selectedImages.map(function(item, idx) {
      return '<div class="wechat-article-selected-image">' +
        '<button type="button" data-wechat-remove-image="' + idx + '" aria-label="移除">×</button>' +
        '<img src="' + escapeHtml(imageSrc(item)) + '" alt="">' +
        '<span title="' + escapeHtml(item.name || '') + '">' + escapeHtml(item.name || '图片') + '</span>' +
      '</div>';
    }).join('');
    el.querySelectorAll('[data-wechat-remove-image]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var idx = Number(btn.getAttribute('data-wechat-remove-image'));
        if (!Number.isNaN(idx)) {
          state.selectedImages.splice(idx, 1);
          renderSelectedImages();
        }
      });
    });
  }

  function selectedImagePayload() {
    return {
      selected_image_urls: state.selectedImages.map(function(item) { return item.url || ''; }).filter(Boolean),
      selected_asset_ids: state.selectedImages.map(function(item) { return item.asset_id || ''; }).filter(Boolean)
    };
  }

  function switchTab(tab) {
    tab = tab || 'compose';
    document.querySelectorAll('.wechat-article-tab').forEach(function(btn) {
      btn.classList.toggle('active', btn.getAttribute('data-wechat-article-tab') === tab);
    });
    ['compose', 'drafts', 'settings'].forEach(function(k) {
      var panel = field('wechatArticlePanel' + k.charAt(0).toUpperCase() + k.slice(1));
      if (panel) panel.style.display = k === tab ? '' : 'none';
    });
    if (tab === 'drafts') loadDrafts();
    if (tab === 'settings') loadConfig();
  }

  function loadConfig() {
    return fetch(apiUrl('/api/wechat-article/config'), { headers: typeof authHeaders === 'function' ? authHeaders() : {} })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (!x.ok) throw new Error((x.data && x.data.detail) || '配置加载失败');
        var d = x.data || {};
        if (field('wechatArticleAppid')) field('wechatArticleAppid').value = d.appid || '';
        if (field('wechatArticleAuthor')) field('wechatArticleAuthor').value = d.author || '';
        if (field('wechatArticleDefaultTheme')) field('wechatArticleDefaultTheme').value = d.theme || 'professional-clean';
        if (field('wechatArticleTheme')) field('wechatArticleTheme').value = d.theme || 'professional-clean';
        if (field('wechatArticleSecret')) field('wechatArticleSecret').value = '';
        if (field('wechatArticleSecretHint')) {
          field('wechatArticleSecretHint').textContent = d.has_secret ? ('已保存密钥：' + (d.secret_masked || '已设置')) : '尚未保存 AppSecret。';
        }
        return d;
      })
      .catch(function(err) {
        showMsg(err && err.message ? err.message : '配置加载失败', true);
      });
  }

  function saveConfig() {
    var btn = field('wechatArticleSaveConfigBtn');
    setBusy(btn, true, '保存中…');
    var body = {
      appid: field('wechatArticleAppid') ? field('wechatArticleAppid').value.trim() : '',
      secret: field('wechatArticleSecret') ? field('wechatArticleSecret').value.trim() : '',
      author: field('wechatArticleAuthor') ? field('wechatArticleAuthor').value.trim() : '',
      theme: field('wechatArticleDefaultTheme') ? field('wechatArticleDefaultTheme').value : 'professional-clean'
    };
    fetch(apiUrl('/api/wechat-article/config'), {
      method: 'PUT',
      headers: hdrs(),
      body: JSON.stringify(body)
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (!x.ok) throw new Error((x.data && x.data.detail) || '保存失败');
        showMsg('公众号配置已保存。', false);
        loadConfig();
      })
      .catch(function(err) {
        showMsg(err && err.message ? err.message : '保存失败', true);
      })
      .finally(function() {
        setBusy(btn, false);
      });
  }

  function generatedPayload() {
    var generated = state.generated || {};
    var markdown = currentMarkdown();
    return {
      title: generated.title || '',
      digest: generated.digest || '',
      theme: generated.theme || currentTheme(),
      cover_asset_id: '',
      cover_image_url: (generated.image && generated.image.url) || '',
      markdown: markdown,
      upload_article_images: true
    };
  }

  var previewTimer = null;

  function setEditVisible(visible) {
    var box = field('wechatArticleEditBox');
    if (box) box.style.display = visible ? '' : 'none';
  }

  function schedulePreviewRefresh() {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(refreshPreviewFromEditor, 260);
  }

  function refreshPreviewFromEditor() {
    var generated = state.generated || {};
    var markdown = currentMarkdown();
    if (!markdown.trim()) return Promise.resolve();
    var btn = field('wechatArticleRefreshPreviewBtn');
    setBusy(btn, true, '刷新中…');
    return fetch(apiUrl('/api/wechat-article/preview'), {
      method: 'POST',
      headers: hdrs(),
      body: JSON.stringify({
        title: generated.title || '',
        markdown: markdown,
        theme: currentTheme()
      })
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (!x.ok) throw new Error((x.data && x.data.detail) || '预览刷新失败');
        var d = x.data || {};
        state.generated = Object.assign({}, state.generated || {}, {
          title: d.title || generated.title || '',
          digest: d.digest || generated.digest || '',
          markdown: markdown,
          html: d.html || '',
          theme: d.theme || currentTheme()
        });
        if (field('wechatArticlePreviewTitle')) field('wechatArticlePreviewTitle').textContent = state.generated.title || '公众号预览';
        if (field('wechatArticlePreviewMeta')) field('wechatArticlePreviewMeta').textContent = state.generated.digest || '';
        if (field('wechatArticlePreview')) field('wechatArticlePreview').innerHTML = state.generated.html || '<p class="meta">暂无预览</p>';
      })
      .catch(function(err) {
        showMsg(err && err.message ? err.message : '预览刷新失败', true);
      })
      .finally(function() {
        setBusy(btn, false);
      });
  }

  function uploadSelectedImageFiles(files) {
    var list = Array.prototype.slice.call(files || []).filter(function(file) {
      return file && /^image\//i.test(file.type || '');
    });
    if (!list.length) return;
    showMsg('正在上传图片...', false);
    Promise.all(list.map(function(file) {
      var fd = new FormData();
      fd.append('file', file);
      return fetch(apiUrl('/api/assets/upload'), {
        method: 'POST',
        headers: authOnlyHeaders(),
        body: fd
      })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
        .then(function(x) {
          if (!x.ok) throw new Error((x.data && x.data.detail) || '图片上传失败');
          var d = x.data || {};
          return {
            asset_id: d.asset_id || '',
            url: d.source_url || '',
            name: d.filename || file.name || '上传图片'
          };
        });
    }))
      .then(function(items) {
        addSelectedImages(items);
        showMsg('已添加 ' + items.length + ' 张图片。', false);
      })
      .catch(function(err) {
        showMsg(err && err.message ? err.message : '图片上传失败', true);
      });
  }

  function normalizeAssetItem(row) {
    if (!row || row.media_type !== 'image') return null;
    var src = row.preview_url || row.open_url || row.source_url || '';
    if (!src) return null;
    return {
      asset_id: row.asset_id || '',
      filename: row.filename || row.asset_id || '图片',
      url: src,
      preview_url: row.preview_url || src,
      open_url: row.open_url || row.source_url || src,
      file_size: row.file_size || 0
    };
  }

  function formatSize(bytes) {
    var n = Number(bytes || 0);
    if (!n) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return Math.round(n / 1024) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }

  function openAssetPicker() {
    var modal = document.getElementById('wechatAssetPickerModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'wechatAssetPickerModal';
      modal.className = 'wechat-asset-picker-modal';
      modal.innerHTML = '<div class="wechat-asset-picker-card" role="dialog" aria-modal="true">' +
        '<div class="wechat-asset-picker-head"><h4>选择素材库图片</h4><button type="button" class="wechat-asset-picker-close" aria-label="关闭">×</button></div>' +
        '<div class="wechat-asset-picker-tools"><input type="search" id="wechatAssetPickerSearch" placeholder="搜索素材库图片"></div>' +
        '<div id="wechatAssetPickerStatus" class="wechat-asset-picker-status">正在加载...</div>' +
        '<div id="wechatAssetPickerGrid" class="wechat-asset-picker-grid"></div>' +
        '<div class="wechat-asset-picker-foot"><span class="meta">选择后会插入本次公众号文章。</span><div class="btns"><button type="button" class="btn btn-ghost btn-sm" id="wechatAssetPickerCancel">取消</button><button type="button" class="btn btn-primary btn-sm" id="wechatAssetPickerConfirm">加入文章</button></div></div>' +
      '</div>';
      document.body.appendChild(modal);
      modal.addEventListener('click', function(event) {
        if (event.target === modal || (event.target.closest && event.target.closest('.wechat-asset-picker-close, #wechatAssetPickerCancel'))) {
          closeAssetPicker();
          return;
        }
        var card = event.target.closest && event.target.closest('[data-wechat-asset-id]');
        if (card) {
          var aid = card.getAttribute('data-wechat-asset-id') || '';
          if (state.assetPickerSelected[aid]) delete state.assetPickerSelected[aid];
          else state.assetPickerSelected[aid] = true;
          renderAssetPicker();
        }
      });
      modal.querySelector('#wechatAssetPickerConfirm').addEventListener('click', confirmAssetPicker);
      modal.querySelector('#wechatAssetPickerSearch').addEventListener('input', renderAssetPicker);
    }
    state.assetPickerSelected = {};
    modal.style.display = 'flex';
    loadAssetPickerItems(true);
  }

  function closeAssetPicker() {
    var modal = document.getElementById('wechatAssetPickerModal');
    if (modal) modal.style.display = 'none';
  }

  function loadAssetPickerItems(force) {
    if (!force && state.assetPickerItems.length) {
      renderAssetPicker();
      return Promise.resolve(state.assetPickerItems);
    }
    var status = document.getElementById('wechatAssetPickerStatus');
    if (status) status.textContent = '正在加载素材库图片...';
    return fetch(apiUrl('/api/assets?media_type=image&limit=80'), { headers: authOnlyHeaders() })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (!x.ok) throw new Error((x.data && x.data.detail) || '素材库图片加载失败');
        var rows = Array.isArray(x.data && x.data.items) ? x.data.items : [];
        state.assetPickerItems = rows.map(normalizeAssetItem).filter(Boolean);
        renderAssetPicker();
      })
      .catch(function(err) {
        if (status) status.textContent = err && err.message ? err.message : '素材库图片加载失败';
      });
  }

  function renderAssetPicker() {
    var grid = document.getElementById('wechatAssetPickerGrid');
    var status = document.getElementById('wechatAssetPickerStatus');
    var search = document.getElementById('wechatAssetPickerSearch');
    if (!grid || !status) return;
    var q = String(search && search.value || '').trim().toLowerCase();
    var items = state.assetPickerItems.filter(function(item) {
      if (!q) return true;
      return String(item.filename || '').toLowerCase().indexOf(q) >= 0 || String(item.asset_id || '').toLowerCase().indexOf(q) >= 0;
    });
    status.textContent = state.assetPickerItems.length ? ('共 ' + state.assetPickerItems.length + ' 张图片，当前显示 ' + items.length + ' 张') : '素材库暂无图片';
    if (!items.length) {
      grid.innerHTML = '<div class="wechat-asset-picker-empty">暂无可选图片。</div>';
      return;
    }
    grid.innerHTML = items.map(function(item) {
      var selected = state.assetPickerSelected[item.asset_id] ? ' is-selected' : '';
      return '<button type="button" class="wechat-asset-picker-item' + selected + '" data-wechat-asset-id="' + escapeHtml(item.asset_id) + '">' +
        '<img src="' + escapeHtml(item.preview_url || item.url) + '" alt="">' +
        '<span class="wechat-asset-picker-name" title="' + escapeHtml(item.filename) + '">' + escapeHtml(item.filename) + '</span>' +
        '<span class="wechat-asset-picker-meta">' + escapeHtml(formatSize(item.file_size) || item.asset_id) + '</span>' +
        '<span class="wechat-asset-picker-check">✓</span>' +
      '</button>';
    }).join('');
  }

  function confirmAssetPicker() {
    var picked = state.assetPickerItems.filter(function(item) {
      return !!state.assetPickerSelected[item.asset_id];
    }).map(function(item) {
      return {
        asset_id: item.asset_id,
        url: item.open_url || item.url || item.preview_url,
        name: item.filename
      };
    });
    addSelectedImages(picked);
    closeAssetPicker();
    if (picked.length) showMsg('已从素材库添加 ' + picked.length + ' 张图片。', false);
  }

  function renderGenerated(data) {
    state.generated = data || null;
    var title = (data && data.title) || '公众号预览';
    var digest = (data && data.digest) || '';
    if (field('wechatArticleMarkdownEdit')) field('wechatArticleMarkdownEdit').value = (data && data.markdown) || '';
    if (field('wechatArticleEditToggle')) field('wechatArticleEditToggle').disabled = !(data && data.markdown);
    if (field('wechatArticleRefreshPreviewBtn')) field('wechatArticleRefreshPreviewBtn').disabled = !(data && data.markdown);
    if (field('wechatArticlePreviewTitle')) field('wechatArticlePreviewTitle').textContent = title;
    if (field('wechatArticlePreviewMeta')) field('wechatArticlePreviewMeta').textContent = digest;
    if (field('wechatArticlePreview')) {
      field('wechatArticlePreview').innerHTML = (data && data.html) || '<p class="meta">暂无预览</p>';
    }
    if (field('wechatArticleDraftBtn')) field('wechatArticleDraftBtn').disabled = !(data && data.markdown);
    var meta = field('wechatArticleGeneratedMeta');
    if (meta) {
      var image = data && data.image;
      var warnings = Array.isArray(data && data.warnings) ? data.warnings : [];
      var generatedCount = image && typeof image.generated_count === 'number' ? image.generated_count : (image && Array.isArray(image.urls) ? image.urls.length : 0);
      var requestedCount = image && image.count ? image.count : generatedCount;
      var imageError = image && (image.error || (Array.isArray(image.errors) && image.errors.length ? image.errors.join(' ') : ''));
      var imageText = image && image.enabled
        ? (generatedCount ? ('已插入 ' + generatedCount + '/' + requestedCount + ' 张自动配图。') : ('自动配图未生成。' + (imageError ? ' ' + imageError : '')))
        : '未开启自动配图。';
      meta.innerHTML = '<p class="meta">已生成：' + escapeHtml(title) + ' · ' + escapeHtml(imageText) + '</p>' +
        (warnings.length ? '<p class="meta">' + escapeHtml(warnings.join(' ')) + '</p>' : '');
    }
  }

  function generateArticle() {
    var idea = field('wechatArticleIdea') ? field('wechatArticleIdea').value.trim() : '';
    if (!idea) {
      showMsg('先输入一个主题或想法。', true);
      return;
    }
    var btn = field('wechatArticleGenerateBtn');
    var wantsImages = !!(field('wechatArticleIncludeImages') && field('wechatArticleIncludeImages').checked);
    var imageCount = field('wechatArticleImageCount') ? Number(field('wechatArticleImageCount').value || 3) : 3;
    setBusy(btn, true, wantsImages ? ('写作并生成 ' + imageCount + ' 张配图…') : '写作排版中…');
    showMsg('', false);
    if (field('wechatArticlePreview')) {
      field('wechatArticlePreview').innerHTML = '<div class="wechat-article-empty-preview"><strong>正在生成</strong><p>AI 正在写作并生成公众号排版。</p></div>';
    }
    fetch(apiUrl('/api/wechat-article/generate'), {
      method: 'POST',
      headers: hdrs(),
      body: JSON.stringify(Object.assign({
        idea: idea,
        audience: field('wechatArticleAudience') ? field('wechatArticleAudience').value.trim() : '',
        style: field('wechatArticleStyle') ? field('wechatArticleStyle').value : '专业、有观点、适合公众号阅读',
        theme: currentTheme(),
        include_images: wantsImages,
        image_model: 'gpt-image-2',
        image_aspect_ratio: field('wechatArticleImageRatio') ? field('wechatArticleImageRatio').value : '3:2',
        image_count: imageCount
      }, selectedImagePayload()))
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (!x.ok) throw new Error((x.data && x.data.detail) || '生成失败');
        renderGenerated(x.data || {});
        showMsg('文章已生成，可以在右侧确认效果。', false);
        if (field('wechatArticleEditToggle') && field('wechatArticleEditToggle').checked) setEditVisible(true);
      })
      .catch(function(err) {
        showMsg(err && err.message ? err.message : '生成失败', true);
        if (field('wechatArticlePreview')) {
          field('wechatArticlePreview').innerHTML = '<div class="wechat-article-empty-preview"><strong>生成失败</strong><p>' + escapeHtml(err && err.message ? err.message : '请稍后重试') + '</p></div>';
        }
      })
      .finally(function() {
        setBusy(btn, false);
      });
  }

  function createDraft() {
    var payload = generatedPayload();
    if (!payload.markdown.trim()) {
      showMsg('请先 AI 生成文章，再推送草稿箱。', true);
      return;
    }
    var btn = field('wechatArticleDraftBtn');
    setBusy(btn, true, '推送中…');
    fetch(apiUrl('/api/wechat-article/drafts'), {
      method: 'POST',
      headers: hdrs(),
      body: JSON.stringify(payload)
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (!x.ok) throwApiError(x.data, '推送失败');
        var draft = (x.data && x.data.draft) || {};
        if (x.data && x.data.pushed) {
          showMsg('已推送到公众号草稿箱：' + (draft.media_id || ''), false);
        } else {
          var pushError = (draft && draft.push_error) || (x.data && x.data.push_error) || '';
          var msg = pushError
            ? (((x.data && x.data.message) || '文章已保存到本地，但推送到公众号草稿箱失败。') + '\n失败原因：' + pushError)
            : ((x.data && x.data.message) || '文章已保存到公众号文章页面，可稍后再次推送。');
          showMsg(msg, !!pushError);
        }
        loadDrafts();
      })
      .catch(function(err) {
        showMsg(err && err.message ? err.message : '推送失败', true);
      })
      .finally(function() {
        setBusy(btn, false);
      });
  }

  function loadDrafts() {
    var el = field('wechatArticleDraftList');
    if (!el) return;
    el.innerHTML = '<p class="meta">加载中…</p>';
    fetch(apiUrl('/api/wechat-article/drafts'), { headers: typeof authHeaders === 'function' ? authHeaders() : {} })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (!x.ok) throw new Error((x.data && x.data.detail) || '草稿加载失败');
        var rows = (x.data && Array.isArray(x.data.drafts)) ? x.data.drafts : [];
        if (!rows.length) {
          el.innerHTML = '<p class="meta">暂无草稿记录。推送成功后会显示在这里。</p>';
          return;
        }
        el.innerHTML = rows.map(function(d) {
          var localSaved = (d.push_status === 'local_saved' || d.status === 'local_saved' || !d.media_id);
          var statusText = localSaved ? '本地保存' : '已推送';
          return '<div class="wechat-article-draft-item">' +
            '<div class="wechat-article-draft-main">' +
            '<strong>' + escapeHtml(d.title || '未命名文章') + '</strong>' +
            '<p>' + escapeHtml(d.digest || '') + '</p>' +
            '<span class="meta">' + escapeHtml(statusText) +
            (d.media_id ? (' · media_id: ' + escapeHtml(d.media_id || '-')) : '') +
            ' · ' + escapeHtml(d.theme || '-') +
            ' · ' + escapeHtml(d.created_at || '') + '</span>' +
            '</div>' +
            '<div class="btns">' +
            '<button type="button" class="btn btn-ghost btn-sm wechat-article-load-draft" data-draft-id="' + escapeHtml(d.id || '') + '">载入</button>' +
            '<button type="button" class="btn btn-ghost btn-sm wechat-article-delete-draft" data-draft-id="' + escapeHtml(d.id || '') + '">删除记录</button>' +
            '</div></div>';
        }).join('');
        el.querySelectorAll('.wechat-article-load-draft').forEach(function(btn) {
          btn.addEventListener('click', function() { loadDraftDetail(btn.getAttribute('data-draft-id')); });
        });
        el.querySelectorAll('.wechat-article-delete-draft').forEach(function(btn) {
          btn.addEventListener('click', function() { deleteDraft(btn.getAttribute('data-draft-id')); });
        });
      })
      .catch(function(err) {
        el.innerHTML = '<p class="msg err" style="display:block;">' + escapeHtml(err && err.message ? err.message : '草稿加载失败') + '</p>';
      });
  }

  function loadDraftDetail(id) {
    if (!id) return;
    fetch(apiUrl('/api/wechat-article/drafts/' + encodeURIComponent(id)), { headers: typeof authHeaders === 'function' ? authHeaders() : {} })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (!x.ok) throw new Error((x.data && x.data.detail) || '草稿读取失败');
        var d = x.data || {};
        renderGenerated({
          title: d.title || '',
          digest: d.digest || '',
          theme: d.theme || 'professional-clean',
          markdown: d.markdown || '',
          html: d.html || '',
          image: { enabled: !!d.has_cover, url: '', prompt: '', error: '' },
          warnings: []
        });
        if (field('wechatArticleEditToggle')) field('wechatArticleEditToggle').checked = true;
        setEditVisible(true);
        switchTab('compose');
        showMsg('已载入草稿记录，可确认后再次推送。', false);
      })
      .catch(function(err) {
        showMsg(err && err.message ? err.message : '草稿读取失败', true);
      });
  }

  function deleteDraft(id) {
    if (!id || !window.confirm('只删除本地记录，不会删除微信公众号后台草稿。确定删除？')) return;
    fetch(apiUrl('/api/wechat-article/drafts/' + encodeURIComponent(id)), {
      method: 'DELETE',
      headers: typeof authHeaders === 'function' ? authHeaders() : {}
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (!x.ok) throw new Error((x.data && x.data.detail) || '删除失败');
        loadDrafts();
      })
      .catch(function(err) {
        showMsg(err && err.message ? err.message : '删除失败', true);
      });
  }

  function bindOnce() {
    var root = document.getElementById('content-wechat-article');
    if (!root || root.dataset.bound === '1') return;
    root.dataset.bound = '1';
    document.querySelectorAll('.wechat-article-tab').forEach(function(btn) {
      btn.addEventListener('click', function() {
        switchTab(btn.getAttribute('data-wechat-article-tab') || 'compose');
      });
    });
    field('wechatArticleBackBtn') && field('wechatArticleBackBtn').addEventListener('click', function() {
      if (window.showAppView) window.showAppView('skill-store');
      else location.hash = 'skill-store';
    });
    field('wechatArticleReloadBtn') && field('wechatArticleReloadBtn').addEventListener('click', loadDrafts);
    field('wechatArticleGenerateBtn') && field('wechatArticleGenerateBtn').addEventListener('click', generateArticle);
    field('wechatArticleRefreshPreviewBtn') && field('wechatArticleRefreshPreviewBtn').addEventListener('click', refreshPreviewFromEditor);
    field('wechatArticleDraftBtn') && field('wechatArticleDraftBtn').addEventListener('click', createDraft);
    field('wechatArticleSaveConfigBtn') && field('wechatArticleSaveConfigBtn').addEventListener('click', saveConfig);
    field('wechatArticleImageUpload') && field('wechatArticleImageUpload').addEventListener('change', function(event) {
      uploadSelectedImageFiles(event.target.files);
      event.target.value = '';
    });
    field('wechatArticleAssetPickerBtn') && field('wechatArticleAssetPickerBtn').addEventListener('click', openAssetPicker);
    field('wechatArticleEditToggle') && field('wechatArticleEditToggle').addEventListener('change', function() {
      setEditVisible(!!field('wechatArticleEditToggle').checked);
    });
    field('wechatArticleMarkdownEdit') && field('wechatArticleMarkdownEdit').addEventListener('input', schedulePreviewRefresh);
    field('wechatArticleTheme') && field('wechatArticleTheme').addEventListener('change', function() {
      if (state.generated && currentMarkdown().trim()) refreshPreviewFromEditor();
    });
  }

  window.loadWechatArticlePage = function() {
    bindOnce();
    switchTab('compose');
  };

  window._openWechatArticleView = function() {
    location.hash = 'wechat-article';
    if (typeof window.showAppView === 'function') {
      return window.showAppView('wechat-article').then(function() {
        window.loadWechatArticlePage();
      });
    }
    window.loadWechatArticlePage();
  };
})();
