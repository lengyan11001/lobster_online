(function() {
  // 2026-09-27 需求：信息台只保留两个榜；服务端已收敛请求集合，这里再兜一层，
  // 即使读到历史快照（含热搜/星图/创作者中心等旧分类）也只显示这两个。
  var ALLOWED_CATEGORIES = ['内容榜', '热点榜'];   // 内容榜排前面

  var state = {
    data: null,
    category: '',
    loading: false
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function baseUrl() {
    return String(typeof API_BASE !== 'undefined' ? API_BASE : '').replace(/\/$/, '');
  }

  function formatTime(value) {
    if (!value) return '-';
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('zh-CN', { hour12: false });
  }

  function metricText(item) {
    var metrics = item && item.metrics && typeof item.metrics === 'object' ? item.metrics : {};
    var labels = { score: '热度分', hot_score: '热度', hot_value: '热度', heat: '热度', play_cnt: '播放', like_cnt: '点赞', follow_cnt: '涨粉', fans_cnt: '粉丝', new_like_cnt: '新增点赞', new_fans_cnt: '新增粉丝', publish_cnt: '发布', avg_play_cnt: '平均播放', video_count: '视频数', rank_diff: '上升', duration: '时长', like_rate: '点赞率', follow_rate: '涨粉率' };
    return Object.keys(metrics).slice(0, 6).map(function(key) {
      return (labels[key] || key.replace(/_/g, ' ')) + ' ' + metrics[key];
    }).join(' · ');
  }

  function render() {
    var fetched = document.getElementById('douyinDeskFetchedAt');
    var tabs = document.getElementById('douyinDeskTabs');
    var content = document.getElementById('douyinDeskContent');
    if (!content) return;
    var snapshot = state.data && state.data.snapshot;
    if (!snapshot) {
      if (fetched) fetched.textContent = '服务器尚未生成快照，将在每天 09:00（北京时间）采集';
      if (tabs) tabs.innerHTML = '';
      content.innerHTML = '<div class="douyin-desk-empty">暂无平台数据</div>';
      return;
    }
    var sections = (Array.isArray(snapshot.sections) ? snapshot.sections : []).filter(function(section) {
      return ALLOWED_CATEGORIES.indexOf(String(section && section.category || '').trim()) >= 0;
    });
    var categories = [];
    sections.forEach(function(section) {
      var category = String(section && section.category || '其他').trim() || '其他';
      if (categories.indexOf(category) < 0) categories.push(category);
    });
    if (categories.indexOf(state.category) < 0) state.category = categories[0] || '';
    if (fetched) fetched.textContent = '最近采集：' + formatTime(snapshot.fetched_at);
    if (tabs) {
      tabs.innerHTML = categories.map(function(category) {
        return '<button type="button" class="douyin-desk-tab' + (category === state.category ? ' active' : '') + '" data-douyin-desk-category="' + escapeHtml(category) + '">' + escapeHtml(category) + '</button>';
      }).join('');
    }
    var visible = sections.filter(function(section) {
      return String(section && section.category || '其他') === state.category;
    });
    content.innerHTML = visible.length ? visible.map(function(section) {
      var items = Array.isArray(section.items) ? section.items : [];
      var cards = items.length ? items.map(itemCard).join('') : '<div class="douyin-desk-empty">该接口暂无可展示条目' + (section.error ? '：' + escapeHtml(section.error) : '') + '</div>';
      return '<section class="douyin-desk-section"><div class="douyin-desk-section-head"><h3>' + escapeHtml(section.title || section.key || '数据') + '</h3><span>' + items.length + ' 条</span></div><div class="douyin-desk-grid">' + cards + '</div></section>';
    }).join('') : '<div class="douyin-desk-empty">该分类暂无数据</div>';
    bindCards(content);
  }

  function itemCard(item) {
    var title = item.title || item.name || '热门内容';
    var metaParts = [];
    if (item.category) metaParts.push(String(item.category));
    if (item.section_title) metaParts.push(String(item.section_title));
    if (item.author && item.author !== title) metaParts.push('作者 ' + item.author);
    if (item.value) metaParts.push(item.value);
    if (item.detail) metaParts.push(item.detail);
    var metrics = metricText(item);
    if (metrics) metaParts.push(metrics);
    var meta = metaParts.join(' · ') || '暂无指标';
    var cover = item.cover_url && /^https?:\/\//i.test(String(item.cover_url))
      ? '<img class="douyin-desk-cover" src="' + escapeHtml(item.cover_url) + '" alt="" loading="lazy" referrerpolicy="no-referrer" />'
      : '';
    var body = '<div class="douyin-desk-item-main"><span class="douyin-desk-rank">' + escapeHtml(item.rank || '-') + '</span><span class="douyin-desk-item-title">' + escapeHtml(title) + '</span></div><div class="douyin-desk-item-meta">' + escapeHtml(meta) + '</div>';
    if (item.url && /^https?:\/\//i.test(String(item.url))) {
      body = '<a href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener noreferrer">' + body + '<span class="douyin-desk-item-link">打开观看</span></a>';
    }
    var actions = '<div class="douyin-desk-item-actions">'
      + (item.id ? '<button type="button" class="douyin-desk-imitation" data-douyin-imitation="1" data-douyin-item-id="' + escapeHtml(item.id || '') + '" data-douyin-title="' + escapeHtml(title) + '">做同款（换人）</button>' : '')
      + '<span class="douyin-desk-item-status" data-douyin-status="1"></span>'
      + '</div><div class="douyin-desk-item-result" data-douyin-result="1"></div>';
    return '<article class="douyin-desk-item">' + cover + body + actions + '</article>';
  }

  function bindCards(root) {
    if (!root) return;
    root.querySelectorAll('.douyin-desk-cover').forEach(function(image) {
      image.addEventListener('error', function() { image.style.display = 'none'; });
    });
    root.querySelectorAll('[data-douyin-imitation]').forEach(function(button) {
      button.addEventListener('click', function(event) {
        event.preventDefault();
        event.stopPropagation();
        startImitation(button);
      });
    });
  }

  function searchDesk() {
    var input = document.getElementById('douyinDeskSearchInput');
    var content = document.getElementById('douyinDeskContent');
    var clearBtn = document.getElementById('douyinDeskSearchClear');
    var query = String(input && input.value || '').trim();
    if (!query) return;
    state.category = '';
    if (content) content.innerHTML = '<div class="douyin-desk-empty">正在搜索「' + escapeHtml(query) + '」…</div>';
    fetch(baseUrl() + '/api/douyin/platform-information-desk/search?q=' + encodeURIComponent(query), {
      headers: typeof authHeaders === 'function' ? authHeaders() : {}
    }).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok) throw new Error(data.detail || data.message || ('HTTP ' + response.status));
        var items = Array.isArray(data && data.items) ? data.items : [];
        if (content) {
          content.innerHTML = '<section class="douyin-desk-section"><div class="douyin-desk-section-head"><h3>搜索「' + escapeHtml(query) + '」</h3><span>' + items.length + ' 条</span></div>'
            + (items.length ? '<div class="douyin-desk-grid">' + items.map(itemCard).join('') + '</div>'
                            : '<div class="douyin-desk-empty">没搜到，换个词试试</div>') + '</section>';
          bindCards(content);
        }
        if (clearBtn) clearBtn.classList.remove('hidden');
      });
    }).catch(function(error) {
      if (content) content.innerHTML = '<div class="douyin-desk-empty">' + escapeHtml(error && error.message || '搜索失败') + '</div>';
    });
  }

  function clearDeskSearch() {
    var input = document.getElementById('douyinDeskSearchInput');
    var clearBtn = document.getElementById('douyinDeskSearchClear');
    if (input) input.value = '';
    if (clearBtn) clearBtn.classList.add('hidden');
    render();
  }

  function postJson(path, payload) {
    return fetch(baseUrl() + path, {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : {}),
      body: JSON.stringify(payload || {})
    }).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok) throw new Error(data.detail || data.message || ('HTTP ' + response.status));
        return data;
      });
    });
  }

  function uploadReference(file) {
    var form = new FormData();
    form.append('file', file, file.name || 'imitation-image');
    return fetch(baseUrl() + '/api/assets/upload-temp', {
      method: 'POST',
      headers: typeof authHeaders === 'function' ? authHeaders() : {},
      body: form
    }).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok) throw new Error(data.detail || data.message || ('上传失败 HTTP ' + response.status));
        var url = String(data.public_url || '').trim();
        if (!url) throw new Error('参考图上传后没有拿到公网地址');
        return url;
      });
    });
  }

  function pollImitation(taskId, tries, statusEl, resultEl) {
    setTimeout(function() {
      fetch(baseUrl() + '/api/douyin/platform-information-desk/imitation/' + encodeURIComponent(taskId), {
        headers: typeof authHeaders === 'function' ? authHeaders() : {}
      }).then(function(response) {
        return response.json().catch(function() { return {}; }).then(function(data) {
          if (!response.ok) throw new Error(data.detail || data.message || ('HTTP ' + response.status));
          if (data.status === 'SUCCESS' && data.video_url) {
            if (statusEl) statusEl.textContent = '已完成';
            if (resultEl) {
              resultEl.innerHTML = '<video controls playsinline preload="metadata" src="' + escapeHtml(data.video_url) + '"></video>'
                + '<a class="douyin-desk-item-link" href="' + escapeHtml(data.video_url) + '" target="_blank" rel="noopener noreferrer">打开 / 下载</a>';
            }
            return;
          }
          if (data.done) {
            if (statusEl) statusEl.textContent = '失败：' + String(data.fail_reason || '生成失败');
            return;
          }
          if (statusEl) statusEl.textContent = '生成中 ' + String(data.progress || '') + '（第 ' + tries + ' 次查询）';
          if (tries >= 40) {
            if (statusEl) statusEl.textContent = '还在生成中，任务已提交';
            return;
          }
          pollImitation(taskId, tries + 1, statusEl, resultEl);
        });
      }).catch(function(err) {
        if (statusEl) statusEl.textContent = '查询失败：' + (err && err.message || err);
      });
    }, 6000);
  }

  function startImitation(button) {
    var card = button.closest ? button.closest('.douyin-desk-item') : null;
    var statusEl = card ? card.querySelector('[data-douyin-status]') : null;
    var resultEl = card ? card.querySelector('[data-douyin-result]') : null;
    var title = button.getAttribute('data-douyin-title') || '';
    var itemId = button.getAttribute('data-douyin-item-id') || '';
    if (!itemId) {
      if (statusEl) statusEl.textContent = '这条数据没有作品 id，换不了人';
      return;
    }
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.addEventListener('change', function() {
      var file = input.files && input.files[0];
      if (!file) return;
      if (statusEl) statusEl.textContent = '正在上传照片…';
      uploadReference(file).then(function(imageUrl) {
        if (statusEl) statusEl.textContent = '正在准备素材并提交换人（约 1-3 分钟）…';
        return postJson('/api/douyin/platform-information-desk/imitation', {
          image_url: imageUrl,
          item_id: itemId,
          title: title
        });
      }).then(function(data) {
        var taskId = String(data && data.task_id || '');
        if (!taskId) throw new Error('没有拿到任务号');
        pollImitation(taskId, 1, statusEl, resultEl);
      }).catch(function(err) {
        if (statusEl) statusEl.textContent = '失败：' + (err && err.message || err);
      });
    });
    input.click();
  }

  function load() {
    if (state.loading) return Promise.resolve();
    state.loading = true;
    var content = document.getElementById('douyinDeskContent');
    if (content) content.innerHTML = '<div class="douyin-desk-empty">正在读取服务器快照...</div>';
    return fetch(baseUrl() + '/api/douyin/platform-information-desk', {
      headers: typeof authHeaders === 'function' ? authHeaders() : {}
    }).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok) throw new Error(data.detail || data.message || ('HTTP ' + response.status));
        state.data = data;
        render();
      });
    }).catch(function(error) {
      if (content) content.innerHTML = '<div class="douyin-desk-empty">' + escapeHtml(error && error.message || '读取平台数据失败') + '</div>';
    }).then(function() {
      state.loading = false;
    });
  }

  window.initDouyinInformationDeskView = function() {
    var refresh = document.getElementById('douyinDeskRefreshBtn');
    var tabs = document.getElementById('douyinDeskTabs');
    if (refresh && !refresh.dataset.bound) {
      refresh.dataset.bound = '1';
      refresh.addEventListener('click', load);
    }
    var searchBtn = document.getElementById('douyinDeskSearchBtn');
    if (searchBtn && !searchBtn.dataset.bound) {
      searchBtn.dataset.bound = '1';
      searchBtn.addEventListener('click', searchDesk);
      var clearBtn = document.getElementById('douyinDeskSearchClear');
      if (clearBtn) clearBtn.addEventListener('click', clearDeskSearch);
      var searchInput = document.getElementById('douyinDeskSearchInput');
      if (searchInput) {
        searchInput.addEventListener('keydown', function(event) {
          if (event.key === 'Enter') {
            event.preventDefault();
            searchDesk();
          }
        });
      }
    }
    if (tabs && !tabs.dataset.bound) {
      tabs.dataset.bound = '1';
      tabs.addEventListener('click', function(event) {
        var button = event.target.closest('[data-douyin-desk-category]');
        if (!button) return;
        state.category = String(button.dataset.douyinDeskCategory || '');
        render();
      });
    }
    return load();
  };
})();
