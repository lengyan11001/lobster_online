(function() {
  // 2026-09-27 需求：信息台只保留两个榜；服务端已收敛请求集合，这里再兜一层，
  // 即使读到历史快照（含热搜/星图/创作者中心等旧分类）也只显示这两个。
  var ALLOWED_CATEGORIES = ['内容榜', '热点榜'];   // 内容榜排前面

  var state = {
    data: null,
    category: '',
    loading: false,
    lastTask: null      // 本次会话最后发起的做同款任务（搜索/切 tab 也不会丢）
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function errorText(err) {
    if (!err) return '操作失败';
    var raw = err.message !== undefined && err.message !== null ? err.message : err;
    if (typeof raw === 'string') return raw;
    if (raw && typeof raw === 'object') {
      var inner = raw.message || raw.detail || raw.error;
      if (typeof inner === 'string' && inner) return inner;
      try {
        return JSON.stringify(raw).slice(0, 300);
      } catch (e) {
        return '操作失败';
      }
    }
    return String(raw);
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
    renderLastTask();
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

  function statusLabel(status) {
    if (status === 'SUCCESS') return '已完成';
    if (status === 'FAILED') return '失败';
    return '生成中';
  }

  function renderLastTask() {
    var box = document.getElementById('douyinDeskLast');
    if (!box) return;
    var task = state.lastTask;
    if (!task) {
      box.hidden = true;
      box.innerHTML = '';
      return;
    }
    box.hidden = false;
    box.innerHTML = '<div class="douyin-desk-last-title">最近一次生成·' + escapeHtml(statusLabel(task.status)) + '</div>'
      + '<div class="douyin-desk-last-body">' + escapeHtml(String(task.title || task.taskId || '')) + '</div>'
      + (task.videoUrl
          ? '<video controls playsinline preload="metadata" src="' + escapeHtml(task.videoUrl) + '"></video>'
            + '<div class="douyin-desk-item-actions"><button type="button" class="douyin-desk-imitation" data-douyin-dl="' + escapeHtml(String(task.taskId || '')) + '">下载成片</button>'
            + (task.assetId ? '<span class="douyin-desk-item-status">已入库素材库</span>' : '')
            + '</div>'
          : (task.failReason ? '<div class="douyin-desk-last-fail">' + escapeHtml(task.failReason) + '</div>'
                             : '<div class="douyin-desk-last-tip">任务已提交，可点「生成历史」随时回来看成片</div>'));
    bindDownloadButtons(box);
  }

  function loadHistory() {
    var content = document.getElementById('douyinDeskContent');
    if (content) content.innerHTML = '<div class="douyin-desk-empty">正在读取生成历史…</div>';
    fetch(baseUrl() + '/api/douyin/platform-information-desk/imitation/history?limit=20', {
      headers: typeof authHeaders === 'function' ? authHeaders() : {}
    }).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok) throw new Error(data.detail || data.message || ('HTTP ' + response.status));
        var items = Array.isArray(data && data.items) ? data.items : [];
        if (!content) return;
        var rows = items.map(function(item) {
          var status = statusLabel(item.status);
          return '<article class="douyin-desk-history-item" data-douyin-history="' + escapeHtml(String(item.id)) + '">'
            + '<div class="douyin-desk-history-head"><span class="douyin-desk-history-status is-' + escapeHtml(String(item.status || '').toLowerCase()) + '">' + escapeHtml(status) + '</span>'
            + '<span class="douyin-desk-history-title">' + escapeHtml(item.title || item.task_id || '') + '</span></div>'
            + '<div class="douyin-desk-history-meta">' + escapeHtml(formatTime(item.created_at)) + ' · ' + escapeHtml(item.model || '') + '</div>'
            + (item.video_url ? '<video controls playsinline preload="metadata" src="' + escapeHtml(item.video_url) + '"></video>'
                              : (item.fail_reason ? '<div class="douyin-desk-last-fail">' + escapeHtml(item.fail_reason) + '</div>' : ''))
            + (item.video_url
                ? '<div class="douyin-desk-item-actions"><button type="button" class="douyin-desk-imitation" data-douyin-dl="' + escapeHtml(item.task_id || '') + '">下载成片</button>'
                  + (item.asset_id ? '<span class="douyin-desk-item-status">已入库</span>' : '')
                  + '<a class="douyin-desk-item-link" href="' + escapeHtml(item.video_url) + '" target="_blank" rel="noopener noreferrer">在浏览器打开</a></div>'
                : '')
            + '</article>';
        }).join('');
        content.innerHTML = '<section class="douyin-desk-section"><div class="douyin-desk-section-head"><h3>生成历史</h3>'
          + '<span>' + items.length + ' 条</span></div>'
          + '<div class="douyin-desk-history-actions"><button type="button" class="btn btn-ghost" id="douyinDeskHistoryBack">返回榜单</button></div>'
          + (items.length ? rows : '<div class="douyin-desk-empty">还没有生成记录，去榜单点「做同款（换人）」试试</div>')
          + '</section>';
        var back = document.getElementById('douyinDeskHistoryBack');
        if (back) back.addEventListener('click', render);
      });
    }).catch(function(error) {
      if (content) content.innerHTML = '<div class="douyin-desk-empty">' + escapeHtml(errorText(error)) + '</div>';
    });
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

  function bindDownloadButtons(root) {
    if (!root) return;
    root.querySelectorAll('[data-douyin-dl]').forEach(function(button) {
      if (button.dataset.bound) return;
      button.dataset.bound = '1';
      button.addEventListener('click', function(event) {
        event.preventDefault();
        event.stopPropagation();
        downloadImitation(button.getAttribute('data-douyin-dl'),
                          button.getAttribute('data-douyin-name') || '');
      });
    });
  }

  function bindCards(root) {
    if (!root) return;
    root.querySelectorAll('.douyin-desk-cover').forEach(function(image) {
      image.addEventListener('error', function() { image.style.display = 'none'; });
    });
    bindDownloadButtons(root);
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
          var searchFee = data && data.billing ? Number(data.billing.credits_charged || 0) : 0;
          var days = Number(data && data.days || 7) || 7;
          var fallbackItems = Array.isArray(data && data.fallback_items) ? data.fallback_items : [];
          var suggestions = Array.isArray(data && data.suggestions) ? data.suggestions : [];
          var html = '<section class="douyin-desk-section"><div class="douyin-desk-section-head"><h3>搜索「'
            + escapeHtml(query) + '」</h3><span>' + items.length + ' 条 · 最近 ' + days + ' 天 · 消耗 ' + searchFee + ' 算力</span></div>';
          if (items.length) {
            html += '<div class="douyin-desk-grid">' + items.map(itemCard).join('') + '</div>';
          } else {
            html += '<div class="douyin-desk-empty">'
              + escapeHtml(String(data && data.fallback_reason || ('最近 ' + days + ' 天的榜单里没搜到「' + query + '」')))
              + '，换个词，或先看今天的热榜 ↓</div>';
            if (suggestions.length) {
              html += '<div class="douyin-desk-copy-actions">' + suggestions.map(function (word) {
                return '<button type="button" class="btn btn-ghost" data-douyin-suggest="' + escapeHtml(word) + '">'
                  + escapeHtml(word) + '</button>';
              }).join('') + '</div>';
            }
            if (fallbackItems.length) {
              html += '<div class="douyin-desk-grid">' + fallbackItems.map(itemCard).join('') + '</div>';
            }
          }
          html += '</section>';
          content.innerHTML = html;
          bindCards(content);
          content.querySelectorAll('[data-douyin-suggest]').forEach(function (button) {
            button.addEventListener('click', function () {
              var word = button.getAttribute('data-douyin-suggest') || '';
              if (!word) return;
              if (input) input.value = word;
              searchDesk();
            });
          });
        }
        if (clearBtn) clearBtn.classList.remove('hidden');
      });
    }).catch(function(error) {
      if (content) content.innerHTML = '<div class="douyin-desk-empty">' + escapeHtml(errorText(error)) + '</div>';
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

  // 客户端的 authHeaders() 带 Content-Type: application/json，
  // 直接拿来传 FormData 会丢掉 multipart boundary，服务端只能报 422。
  function uploadHeaders() {
    var headers = typeof authHeaders === 'function' ? Object.assign({}, authHeaders()) : {};
    Object.keys(headers).forEach(function(key) {
      if (String(key).toLowerCase() === 'content-type') delete headers[key];
    });
    return headers;
  }

  // 成片下载：走 /imitation/{task_id}/download（带 attachment 头），不是打开原视频链接
  function downloadImitation(taskId, name) {
    if (!taskId) return;
    var url = baseUrl() + '/api/douyin/platform-information-desk/imitation/' + encodeURIComponent(taskId) + '/download';
    fetch(url, { headers: uploadHeaders() })
      .then(function(response) {
        if (!response.ok) {
          return response.json().catch(function() { return {}; }).then(function(data) {
            throw new Error(errorText(data && data.detail) || ('下载失败 HTTP ' + response.status));
          });
        }
        return response.blob();
      })
      .then(function(blob) {
        var objectUrl = URL.createObjectURL(blob);
        var link = document.createElement('a');
        link.href = objectUrl;
        link.download = String(name || ('douyin-imitation-' + taskId)).replace(/[\\/:*?"<>|]/g, '_') + '.mp4';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        setTimeout(function() { try { URL.revokeObjectURL(objectUrl); } catch (e) {} }, 60000);
      })
      .catch(function(err) {
        console.warn('[douyin-desk] download failed', err);
        var box = document.getElementById('douyinDeskLast');
        if (box) {
          var tip = box.querySelector('.douyin-desk-last-tip');
          if (!tip) {
            tip = document.createElement('div');
            tip.className = 'douyin-desk-last-tip';
            box.appendChild(tip);
          }
          tip.textContent = '下载失败：' + errorText(err);
        }
      });
  }

  function uploadReference(file) {
    var form = new FormData();
    form.append('file', file, file.name || 'imitation-image');
    return fetch(baseUrl() + '/api/assets/upload-temp', {
      method: 'POST',
      headers: uploadHeaders(),
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
            var playable = String(data.stored_url || data.video_url || '');
            if (state.lastTask && state.lastTask.taskId === taskId) {
              state.lastTask = { taskId: taskId, title: state.lastTask.title, status: 'SUCCESS',
                                 videoUrl: playable, assetId: data.asset_id || '' };
              renderLastTask();
            }
            if (statusEl) statusEl.textContent = data.asset_id ? '已完成（已入库）' : '已完成';
            if (resultEl) {
              resultEl.innerHTML = '<video controls playsinline preload="metadata" src="' + escapeHtml(playable) + '"></video>'
                + '<div class="douyin-desk-item-actions"><button type="button" class="douyin-desk-imitation" data-douyin-dl="' + escapeHtml(taskId) + '">下载成片</button>'
                + '<a class="douyin-desk-item-link" href="' + escapeHtml(playable) + '" target="_blank" rel="noopener noreferrer">在浏览器打开</a></div>';
              bindDownloadButtons(resultEl);
            }
            return;
          }
          if (data.done) {
            if (state.lastTask && state.lastTask.taskId === taskId) {
              state.lastTask = { taskId: taskId, title: state.lastTask.title, status: 'FAILED',
                                 failReason: String(data.fail_reason || '生成失败') };
              renderLastTask();
            }
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
        if (statusEl) statusEl.textContent = '查询失败：' + errorText(err);
      });
    }, 6000);
  }


  // ---------------- 热门视频跟创：自定义视频来源 + 模式（2026-10-05） ----------------
  var copyState = { videoUploadUrl: '', imageUploadUrl: '', defaultImageUrl: '', mode: 'effect_copy', taskId: '' };

  function copyEl(id) { return document.getElementById(id); }

  function setCopyStatus(text) {
    // 弹窗里的状态条（douyinCopyModalStatus）和页面顶部的状态条都要写，弹窗自成一个闭环
    ['douyinCopyStatus', 'douyinCopyModalStatus'].forEach(function (id) {
      var el = copyEl(id);
      if (el) el.textContent = text || '';
    });
  }

  function uploadFileToTemp(file, fallbackName) {
    var form = new FormData();
    form.append('file', file, file.name || fallbackName || 'upload');
    return fetch(baseUrl() + '/api/assets/upload-temp', {
      method: 'POST',
      headers: uploadHeaders(),
      body: form
    }).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok) throw new Error(data.detail || data.message || ('上传失败 HTTP ' + response.status));
        var url = String(data.public_url || '').trim();
        if (!url) throw new Error('上传后没有拿到公网地址');
        return url;
      });
    });
  }

  function findHttpUrlDeep(node, depth) {
    if (!node || depth > 4) return '';
    if (typeof node === 'string') return /^https?:\/\//i.test(node) ? node : '';
    if (typeof node !== 'object') return '';
    if (Array.isArray(node)) {
      for (var i = 0; i < node.length; i += 1) {
        var found = findHttpUrlDeep(node[i], depth + 1);
        if (found) return found;
      }
      return '';
    }
    var preferred = ['profile_photo_url', 'profile_photo', 'image_url', 'url'];
    for (var k = 0; k < preferred.length; k += 1) {
      var hit = findHttpUrlDeep(node[preferred[k]], depth + 1);
      if (hit) return hit;
    }
    var keys = Object.keys(node);
    for (var j = 0; j < keys.length; j += 1) {
      var value = findHttpUrlDeep(node[keys[j]], depth + 1);
      if (value) return value;
    }
    return '';
  }

  function showCopyPreview(url, tip) {
    var image = copyEl('douyinCopyImagePreview');
    var name = copyEl('douyinCopyImageName');
    if (image) {
      if (url) { image.src = url; image.hidden = false; } else { image.hidden = true; image.removeAttribute('src'); }
    }
    if (name) name.textContent = tip || (url ? '已选择' : '未选择');
  }

  function loadCopyDefaultImage() {
    return fetch(baseUrl() + '/api/ip-content/personal-default', {
      headers: typeof authHeaders === 'function' ? authHeaders() : {}
    }).then(function(response) {
      return response.json().catch(function() { return {}; });
    }).then(function(data) {
      var url = findHttpUrlDeep(data, 0);
      if (!url) return '';
      copyState.defaultImageUrl = url;
      if (!copyState.imageUploadUrl) showCopyPreview(url, '默认：IP 人设里的形象照');
      return url;
    }).catch(function() { return ''; });
  }

  function createCopyTask() {
    var videoInput = copyEl('douyinCopyVideoUrl');
    var typedVideo = String(videoInput && videoInput.value || '').trim();
    var video = copyState.videoUploadUrl || typedVideo;
    var image = copyState.imageUploadUrl || copyState.defaultImageUrl;
    var promptEl = copyEl('douyinCopyPrompt');
    var resolutionEl = copyEl('douyinCopyResolution');
    var resolution = String(resolutionEl && resolutionEl.value || '720P');
    var durationEl = copyEl('douyinCopyDuration');
    var durationSeconds = parseInt(String(durationEl && durationEl.value || '0'), 10) || 0;
    var modeEl = copyEl('douyinCopyMode');
    var mode = String(modeEl && modeEl.value || copyState.mode || 'effect_copy');
    copyState.mode = mode;
    if (!video) { setCopyStatus('请先填视频链接，或上传一个本地视频'); return; }
    if (!image) { setCopyStatus('请上传一张参考图（不传的话先在「IP人设定位」里放一张形象照）'); return; }
    setCopyStatus('正在提交（' + resolution + ' / ' + (durationSeconds > 0 ? durationSeconds + ' 秒' : '跟原片一样长')
      + '，约 1-3 分钟）…');
    postJson('/api/douyin/platform-information-desk/imitation', {
      video_url: video,
      image_url: image,
      mode: mode,
      resolution: resolution,
      duration_seconds: durationSeconds,
      prompt: String(promptEl && promptEl.value || '').trim(),
      title: '热门视频跟创'
    }).then(function(data) {
      var taskId = String(data && data.task_id || '');
      if (!taskId) throw new Error('没有拿到任务号');
      copyState.taskId = taskId;
      var charged = data && data.billing ? Number(data.billing.credits_charged || 0) : 0;
      setCopyStatus('已提交（扣 ' + charged + ' 算力）·生成中…（弹窗里出片，也可以先关掉，之后在「历史记录」里找回）');
      pollImitation(taskId, 1, copyEl('douyinCopyModalStatus') || copyEl('douyinCopyStatus'),
                    copyEl('douyinCopyModalResult') || copyEl('douyinCopyResult'));
    }).catch(function(err) {
      setCopyStatus('失败：' + errorText(err));
    });
  }

  function openCopyModal(title, html) {
    var modal = copyEl('douyinCopyModal');
    var titleEl = copyEl('douyinCopyModalTitle');
    var body = copyEl('douyinCopyModalBody');
    if (!modal || !body) return;
    if (titleEl) titleEl.textContent = title || '';
    body.innerHTML = html || '';
    modal.hidden = false;
  }

  function closeCopyModal() {
    var modal = copyEl('douyinCopyModal');
    if (modal) modal.hidden = true;
    var body = copyEl('douyinCopyModalBody');
    if (body) body.innerHTML = '';
  }

  function renderCopyHistory(items) {
    if (!items.length) return '<div class="douyin-desk-empty">还没有生成记录，点「新建跟创」填视频 + 图片创建一条试试</div>';
    copyHistoryItems = {};
    items.forEach(function (item) {
      if (item && item.task_id) copyHistoryItems[String(item.task_id)] = item;
    });
    return items.map(function(item) {
      var status = statusLabel(item.status);
      var video = item.video_url ? '<video controls playsinline preload="metadata" src="' + escapeHtml(item.video_url) + '"></video>' : '';
      var actions = item.video_url
        ? '<div class="douyin-desk-item-actions"><button type="button" class="douyin-desk-imitation" data-douyin-dl="' + escapeHtml(String(item.task_id || '')) + '">下载成片</button>'
          + '<a class="douyin-desk-item-link" href="' + escapeHtml(item.video_url) + '" target="_blank" rel="noopener noreferrer">在浏览器打开</a></div>'
        : '';
      return '<article class="douyin-desk-history-item douyin-desk-history-clickable" data-douyin-history-task="'
        + escapeHtml(String(item.task_id || '')) + '">'
        + '<div class="douyin-desk-history-head"><span class="douyin-desk-history-status">' + escapeHtml(status) + '</span>'
        + '<span class="douyin-desk-history-title">' + escapeHtml(item.source_desc || item.title || item.task_id || '') + '</span></div>'
        + '<div class="douyin-desk-history-meta">' + escapeHtml(formatTime(item.created_at)) + ' · ' + escapeHtml(item.model || '') + '</div>'
        + video
        + (item.fail_reason ? '<div class="douyin-desk-last-fail">' + escapeHtml(item.fail_reason) + '</div>' : '')
        + actions
        + '</article>';
    }).join('');
  }

  var copyHistoryItems = {};

  function openCopyModalById(modalId) {
    var modal = copyEl(modalId);
    if (modal) modal.hidden = false;
  }

  function closeCopyModalById(modalId) {
    var modal = copyEl(modalId);
    if (modal) modal.hidden = true;
  }

  function openCopyRecordDetail(taskId) {
    var item = copyHistoryItems[String(taskId || "")];
    if (!item) return;
    var titleEl = copyEl('douyinCopyDetailTitle');
    if (titleEl) titleEl.textContent = '跟创详情 · ' + statusLabel(item.status);
    var body = copyEl('douyinCopyDetailBody');
    if (!body) return;
    var rows = [
      ['时间', formatTime(item.created_at)],
      ['模式 / 来源', String(item.source_desc || item.title || '-')],
      ['上游任务号', String(item.task_id || '')],
      ['状态', statusLabel(item.status) + (item.fail_reason ? ' · ' + item.fail_reason : '')],
      ['计费 / 扣费', String(item.billable_seconds || 0) + ' 秒 · ' +
        String(item.credits_charged || 0) + ' 算力' +
        (item.credits_refunded ? '（已退 ' + item.credits_refunded + '）' : '')]
    ].map(function (pair) {
      return '<div><dt>' + escapeHtml(pair[0]) + '</dt><dd>' + escapeHtml(pair[1]) + '</dd></div>';
    }).join('');
    body.innerHTML = '<dl class="douyin-desk-detail-facts">' + rows + '</dl>'
      + (item.video_url
          ? '<video controls playsinline preload="metadata" src="' + escapeHtml(item.video_url) + '"></video>'
            + '<div class="douyin-desk-item-actions"><button type="button" class="douyin-desk-imitation" data-douyin-dl="'
            + escapeHtml(String(item.task_id || '')) + '">下载成片</button>'
            + '<a class="douyin-desk-item-link" href="' + escapeHtml(item.video_url)
            + '" target="_blank" rel="noopener noreferrer">在浏览器打开</a></div>'
          : '<div class="douyin-desk-empty">还没有成片' + (item.fail_reason ? '：' + escapeHtml(item.fail_reason) : '（生成中）') + '</div>');
    bindDownloadButtons(body);
    openCopyModalById('douyinCopyDetailModal');
  }

  function openCopyHistory() {
    openCopyModal('生成历史', '<div class="douyin-desk-empty">正在读取生成历史…</div>');
    fetch(baseUrl() + '/api/douyin/platform-information-desk/imitation/history?limit=20', {
      headers: typeof authHeaders === 'function' ? authHeaders() : {}
    }).then(function(response) {
      return response.json().catch(function() { return {}; }).then(function(data) {
        if (!response.ok) throw new Error(data.detail || data.message || ('HTTP ' + response.status));
        var items = Array.isArray(data && data.items) ? data.items : [];
        var body = copyEl('douyinCopyModalBody');
        if (body) {
          body.innerHTML = renderCopyHistory(items);
          bindDownloadButtons(body);
          body.querySelectorAll('[data-douyin-history-task]').forEach(function (node) {
            node.addEventListener('click', function (event) {
              if (event.target && event.target.closest && event.target.closest('button,a,video')) return;
              openCopyRecordDetail(node.getAttribute('data-douyin-history-task'));
            });
          });
        }
      });
    }).catch(function(error) {
      var body = copyEl('douyinCopyModalBody');
      if (body) body.innerHTML = '<div class="douyin-desk-empty">' + escapeHtml(errorText(error)) + '</div>';
    });
  }

  // 榜单卡片上的「跟创」：先选模式，再挑图片（原来直接弹图片选择）
  function startCardCopy(button) {
    var card = button.closest ? button.closest('.douyin-desk-item') : null;
    var statusEl = card ? card.querySelector('[data-douyin-status]') : null;
    var resultEl = card ? card.querySelector('[data-douyin-result]') : null;
    var title = button.getAttribute('data-douyin-title') || '';
    var itemId = button.getAttribute('data-douyin-item-id') || '';
    if (!itemId) {
      if (statusEl) statusEl.textContent = '这条数据没有作品 id，换不了人';
      return;
    }
    openCopyModal('跟创：选模式并挑图片',
      '<div class="douyin-desk-field"><span>跟创模式</span>'
      + '<select id="douyinCardCopyMode">'
      + '<option value="effect_copy">复刻特效（参考视频的特效用到图片人物上，场景街边）</option>'
      + '<option value="action_copy">复刻单人动作（图片人物模仿视频里的动作）</option>'
      + '<option value="person_swap">复刻人物（换人，原模式）</option>'
      + '</select></div>'
      + '<div class="douyin-desk-copy-actions"><button type="button" class="btn btn-primary" id="douyinCardCopyPick">选择图片并创建</button>'
      + '<span class="douyin-desk-item-status" id="douyinCardCopyStatus"></span></div>');
    var pick = copyEl('douyinCardCopyPick');
    var status = copyEl('douyinCardCopyStatus');
    if (!pick) return;
    pick.addEventListener('click', function() {
      var modeEl = copyEl('douyinCardCopyMode');
      var mode = String(modeEl && modeEl.value || 'effect_copy');
      var input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.addEventListener('change', function() {
        var file = input.files && input.files[0];
        if (!file) return;
        if (status) status.textContent = '正在上传照片…';
        uploadReference(file).then(function(imageUrl) {
          if (status) status.textContent = '正在提交换人（约 1-3 分钟）…';
          return postJson('/api/douyin/platform-information-desk/imitation', {
            image_url: imageUrl, item_id: itemId, title: title, mode: mode
          });
        }).then(function(data) {
          closeCopyModal();
          var taskId = String(data && data.task_id || '');
          if (!taskId) throw new Error('没有拿到任务号');
          var charged = data && data.billing ? Number(data.billing.credits_charged || 0) : 0;
          if (statusEl) statusEl.textContent = '已提交（扣 ' + charged + ' 算力）·生成中…';
          state.lastTask = { taskId: taskId, title: title, status: 'RUNNING', credits: charged };
          renderLastTask();
          pollImitation(taskId, 1, statusEl, resultEl);
        }).catch(function(err) {
          if (status) status.textContent = '失败：' + errorText(err);
        });
      });
      input.click();
    });
  }

  function initCopyPanel() {
    var videoPick = copyEl('douyinCopyVideoPick');
    var videoFile = copyEl('douyinCopyVideoFile');
    if (videoPick && videoFile && !videoPick.dataset.bound) {
      videoPick.dataset.bound = '1';
      videoPick.addEventListener('click', function() { videoFile.click(); });
      videoFile.addEventListener('change', function() {
        var file = videoFile.files && videoFile.files[0];
        if (!file) return;
        var nameEl = copyEl('douyinCopyVideoName');
        if (nameEl) nameEl.textContent = '上传中…';
        setCopyStatus('正在上传本地视频…');
        uploadFileToTemp(file, 'copy-video.mp4').then(function(url) {
          copyState.videoUploadUrl = url;
          if (nameEl) nameEl.textContent = String(file.name || '已上传');
          setCopyStatus('本地视频已就绪，可以创建任务了');
        }).catch(function(err) {
          copyState.videoUploadUrl = '';
          if (nameEl) nameEl.textContent = '上传失败';
          setCopyStatus('上传视频失败：' + errorText(err));
        });
      });
    }
    var imagePick = copyEl('douyinCopyImagePick');
    var imageFile = copyEl('douyinCopyImageFile');
    if (imagePick && imageFile && !imagePick.dataset.bound) {
      imagePick.dataset.bound = '1';
      imagePick.addEventListener('click', function() { imageFile.click(); });
      imageFile.addEventListener('change', function() {
        var file = imageFile.files && imageFile.files[0];
        if (!file) return;
        setCopyStatus('正在上传参考图…');
        uploadReference(file).then(function(url) {
          copyState.imageUploadUrl = url;
          showCopyPreview(url, '已选择：' + String(file.name || ''));
          setCopyStatus('参考图已就绪');
        }).catch(function(err) {
          setCopyStatus('上传参考图失败：' + errorText(err));
        });
      });
    }
    var createBtn = copyEl('douyinCopyCreateBtn');
    if (createBtn && !createBtn.dataset.bound) {
      createBtn.dataset.bound = '1';
      createBtn.addEventListener('click', createCopyTask);
    }
    var historyBtn = copyEl('douyinCopyHistoryBtn');
    if (historyBtn && !historyBtn.dataset.bound) {
      historyBtn.dataset.bound = '1';
      historyBtn.addEventListener('click', openCopyHistory);
    }
    // 两个入口都开同一个弹窗：「新建跟创」和顶部「添加链接跟创」
    ['douyinCopyNewBtn', 'douyinCopyMaterialBtn'].forEach(function (openId) {
      var openBtn = copyEl(openId);
      if (openBtn && !openBtn.dataset.bound) {
        openBtn.dataset.bound = '1';
        openBtn.addEventListener('click', function () { openCopyModalById('douyinCopyCreateModal'); });
      }
    });
    var modalHistoryBtn = copyEl('douyinCopyModalHistoryBtn');
    if (modalHistoryBtn && !modalHistoryBtn.dataset.bound) {
      modalHistoryBtn.dataset.bound = '1';
      modalHistoryBtn.addEventListener('click', openCopyHistory);
    }
    ['douyinCopyCreateClose', 'douyinCopyDetailClose'].forEach(function (closeId) {
      var close = copyEl(closeId);
      if (close && !close.dataset.bound) {
        close.dataset.bound = '1';
        close.addEventListener('click', function () { closeCopyModalById(closeId === 'douyinCopyCreateClose' ? 'douyinCopyCreateModal' : 'douyinCopyDetailModal'); });
      }
    });
    ['douyinCopyCreateModal', 'douyinCopyDetailModal'].forEach(function (modalId) {
      var node = copyEl(modalId);
      if (node && !node.dataset.bound) {
        node.dataset.bound = '1';
        node.addEventListener('click', function (event) { if (event.target === node) node.hidden = true; });
      }
    });
    var closeBtn = copyEl('douyinCopyModalClose');
    if (closeBtn && !closeBtn.dataset.bound) {
      closeBtn.dataset.bound = '1';
      closeBtn.addEventListener('click', closeCopyModal);
    }
    var modal = copyEl('douyinCopyModal');
    if (modal && !modal.dataset.bound) {
      modal.dataset.bound = '1';
      modal.addEventListener('click', function(event) {
        if (event.target === modal) closeCopyModal();
      });
    }
    var modeEl = copyEl('douyinCopyMode');
    if (modeEl && !modeEl.dataset.bound) {
      modeEl.dataset.bound = '1';
      modeEl.value = copyState.mode;
      modeEl.addEventListener('change', function() { copyState.mode = String(modeEl.value || 'effect_copy'); });
    }
    return loadCopyDefaultImage();
  }

  // 卡片上的「跟创」走选模式流程（保留旧函数名，事件绑定不用改）
  function startImitation(button) {
    return startCardCopy(button);
  }

  function _legacyStartImitation(button) {
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
        var charged = data && data.billing ? Number(data.billing.credits_charged || 0) : 0;
        if (statusEl) statusEl.textContent = '已提交（扣 ' + charged + ' 算力）·生成中…';
        state.lastTask = { taskId: taskId, title: title, status: 'RUNNING', credits: charged };
        renderLastTask();
        pollImitation(taskId, 1, statusEl, resultEl);
      }).catch(function(err) {
        if (statusEl) statusEl.textContent = '失败：' + errorText(err);
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
    var historyBtn = document.getElementById('douyinDeskHistoryBtn');
    if (historyBtn && !historyBtn.dataset.bound) {
      historyBtn.dataset.bound = '1';
      historyBtn.addEventListener('click', loadHistory);
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
    try { initCopyPanel(); } catch (e) { console.warn('[douyin-desk] copy panel init failed', e); }
    return load();
  };
})();
