/**
 * YouTube 多账号：列表页 + 添加弹窗（Client ID/Secret、代理）。
 * 授权由后端直接拉起本机 Google Chrome；失败时提示复制授权链接。
 */
(function() {
  function localApiBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? LOCAL_API_BASE : '') || '';
  }

  function apiUrl(path) {
    var base = localApiBase().replace(/\/$/, '');
    return (base ? base : '') + path;
  }

  function oauthRedirectUri() {
    var base = localApiBase().replace(/\/$/, '');
    if (!base) {
      var loc = window.location || {};
      var protocol = String(loc.protocol || 'http:');
      var host = String(loc.hostname || '127.0.0.1');
      var port = String(loc.port || '8000');
      base = protocol + '//' + host + (port ? (':' + port) : '');
    }
    return base.replace(/\/$/, '') + '/api/youtube-publish/oauth/callback';
  }

  function localApiCandidates(path) {
    var out = [];
    var loc = window.location || {};
    var host = String(loc.hostname || '').toLowerCase();
    var isLocalish = /^(localhost|127\.0\.0\.1)$/i.test(host) ||
      /^192\.168\.\d{1,3}\.\d{1,3}$/.test(host) ||
      /^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(host) ||
      /^172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}$/.test(host);
    function add(base, allowEmpty) {
      base = String(base || '').replace(/\/$/, '');
      if (!base && !allowEmpty) return;
      var url = (base ? base : '') + path;
      if (out.indexOf(url) === -1) out.push(url);
    }
    var configured = localApiBase();
    if (configured) add(configured);
    if (loc && /^https?:$/i.test(loc.protocol || '')) {
      var port = loc.port || '8000';
      add('http://127.0.0.1:' + port);
      add('http://localhost:' + port);
      if (port !== '8000') {
        add('http://127.0.0.1:8000');
        add('http://localhost:8000');
      }
      if (isLocalish) add(loc.origin || '', true);
    }
    return out;
  }

  function fetchLocalJson(path, options) {
    var urls = localApiCandidates(path);
    var idx = 0;
    var lastErr = null;
    function next() {
      var url = urls[idx++];
      if (!url) return Promise.reject(lastErr || new Error('请求失败'));
      return fetch(url, options).then(function(resp) {
        if ((resp.status === 404 || resp.status === 405 || resp.status >= 500) && idx < urls.length) return next();
        return resp;
      }).catch(function(err) {
        lastErr = err;
        return next();
      });
    }
    return next();
  }

  function hdrs() {
    return Object.assign({ 'Content-Type': 'application/json' }, typeof authHeaders === 'function' ? authHeaders() : {});
  }

  function showMsg(el, text, isErr) {
    if (!el) return;
    el.textContent = text || '';
    el.className = 'msg' + (isErr ? ' err' : '');
    el.style.display = text ? 'block' : 'none';
  }

  var _lastAccounts = [];
  var _youtubeModalMode = 'add';
  var _youtubeEditAid = '';
  var _youtubeScheduleAid = '';
  var _oauthRefreshTimer = null;
  var _ytPendingOauthId = '';

  function stopOauthRefreshPolling() {
    if (_oauthRefreshTimer) {
      clearInterval(_oauthRefreshTimer);
      _oauthRefreshTimer = null;
    }
  }

  function startOauthRefreshPolling(accountId, onDone) {
    stopOauthRefreshPolling();
    var tries = 0;
    function tick() {
      tries += 1;
      fetch(apiUrl('/api/youtube-publish/accounts'), {
        headers: typeof authHeaders === 'function' ? authHeaders() : {}
      })
        .then(function(r) { return r.json(); })
        .then(function(rows) {
          rows = Array.isArray(rows) ? rows : [];
          var changed = JSON.stringify(rows) !== JSON.stringify(_lastAccounts || []);
          if (changed) {
            _lastAccounts = rows;
            renderList();
          }
          var target = rows.filter(function(a) { return String(a.account_id || '') === String(accountId || ''); })[0];
          var ready = !!target && String(target.status || '').toLowerCase() === 'ready';
          if (ready || tries >= 60) { stopOauthRefreshPolling(); if (onDone) onDone(ready); }
        })
        .catch(function() {
          if (tries >= 60) stopOauthRefreshPolling();
        });
    }
    _oauthRefreshTimer = setInterval(tick, 3000);
  }

  function _ytStatus(st) {
    if (st === 'ready') return { label: '在线', color: '#34d399', dot: '●' };
    if (st === 'error') return { label: '异常', color: '#f87171', dot: '●' };
    return { label: '待授权', color: '#fb923c', dot: '●' };
  }

  function _ytFmt(n) {
    n = Number(n || 0);
    if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, '') + '万';
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(n);
  }

  function _ytAvatarText(a) {
    return String((a && (a.label || a.account_id)) || 'Y').trim().slice(0, 1).toUpperCase();
  }

  function _ytUpdateSummary(rows) {
    var ready = 0, pending = 0, error = 0;
    rows.forEach(function(a) {
      var st = String(a.status || '');
      if (st === 'ready') ready += 1;
      else if (st === 'error') error += 1;
      else pending += 1;
    });
    var set = function(id, v) { var el = document.getElementById(id); if (el) el.textContent = String(v); };
    set('ytSumAccounts', rows.length);
    set('ytSumReady', ready);
    set('ytSumPending', pending);
    set('ytSumError', error);
  }

  function _ytLoadOverview(aid) {
    fetch(apiUrl('/api/youtube-publish/accounts/' + encodeURIComponent(aid) + '/overview'), {
      headers: typeof authHeaders === 'function' ? authHeaders() : {}
    }).then(function(r) { return r.json(); }).then(function(d) {
      var box = document.getElementById('yt-metrics-' + aid);
      if (!box || !d) return;
      if (d.reauth_required) {
        var card = box.closest('[data-yt-card]');
        var sp = card ? card.querySelector('.yt-status') : null;
        if (sp) { sp.textContent = '● 授权失效'; sp.style.color = '#fb923c'; }
        box.innerHTML = '<span style="color:#fb923c;">授权已失效，请点「重新授权」</span>';
        return;
      }
      if (!d.ok) return;
      var av = document.getElementById('yt-avatar-' + aid);
      if (av && d.thumbnail) {
        av.innerHTML = '<img src="' + escapeAttr(d.thumbnail) + '" alt="" style="width:100%;height:100%;border-radius:50%;object-fit:cover;">';
      }
      box.innerHTML = '<span>订阅 ' + _ytFmt(d.subscriber_count) + '</span>' +
        '<span>总播放 ' + _ytFmt(d.view_count) + '</span>' +
        '<span>视频 ' + _ytFmt(d.video_count) + '</span>';
    }).catch(function() {});
  }

  function _ytOpenTest(aid) {
    var panel = document.getElementById('youtubeTestPanel');
    if (panel) panel.open = true;
    var sel = document.getElementById('youtubeAccountsTestSelect');
    if (sel) sel.value = aid;
    var input = document.getElementById('youtubeAccountsTestAssetId');
    if (input) input.focus();
  }

  function _ytRenderAnalytics(box, d) {
    if (!box) return;
    var ch = (d && d.channel_analytics) || {};
    var videos = (d && d.videos) || [];
    var html = '';
    if (ch.error) html += '<p class="msg err" style="font-size:0.78rem;">' + escapeHtml(ch.error) + '</p>';
    html += '<div style="display:flex;flex-wrap:wrap;gap:0.9rem;margin-bottom:0.6rem;">';
    if (ch.views !== undefined) html += '<div><div class="meta" style="font-size:0.72rem;">28天播放</div><div style="font-size:1.05rem;font-weight:600;">' + _ytFmt(ch.views) + '</div></div>';
    if (ch.estimatedMinutesWatched !== undefined) html += '<div><div class="meta" style="font-size:0.72rem;">观看分钟</div><div style="font-size:1.05rem;font-weight:600;">' + _ytFmt(ch.estimatedMinutesWatched) + '</div></div>';
    if (ch.subscribersGained !== undefined) html += '<div><div class="meta" style="font-size:0.72rem;">新增订阅</div><div style="font-size:1.05rem;font-weight:600;">' + _ytFmt(ch.subscribersGained) + '</div></div>';
    html += '</div>';
    if (videos.length) {
      html += '<div style="max-height:18rem;overflow:auto;"><table style="width:100%;border-collapse:collapse;font-size:0.78rem;"><thead><tr style="text-align:left;border-bottom:1px solid var(--border);"><th style="padding:0.25rem;">标题</th><th style="padding:0.25rem;">播放</th><th style="padding:0.25rem;">赞</th></tr></thead><tbody>';
      videos.slice(0, 30).forEach(function(v) {
        html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.05);"><td style="padding:0.25rem;max-width:18rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(v.title || '') + '</td><td style="padding:0.25rem;">' + _ytFmt(v.views) + '</td><td style="padding:0.25rem;">' + _ytFmt(v.likes) + '</td></tr>';
      });
      html += '</tbody></table></div>';
    } else {
      html += '<p class="meta" style="font-size:0.78rem;">暂无视频数据。</p>';
    }
    box.innerHTML = html;
  }

  function openDrawer(aid) {
    var a = (_lastAccounts || []).filter(function(x) { return x.account_id === aid; })[0];
    if (!a) return;
    var mask = document.getElementById('youtubeAccountDrawer');
    var title = document.getElementById('youtubeDrawerTitle');
    var body = document.getElementById('youtubeDrawerBody');
    if (!mask || !body) return;
    var st = _ytStatus(a.status);
    if (title) title.textContent = a.label || a.account_id;
    body.innerHTML = '<div style="display:flex;gap:0.65rem;align-items:center;">' +
      '<div style="width:44px;height:44px;border-radius:50%;background:rgba(255,0,0,0.15);display:flex;align-items:center;justify-content:center;font-weight:700;">' + escapeHtml(_ytAvatarText(a)) + '</div>' +
      '<div style="flex:1;min-width:0;"><div style="font-weight:600;">' + escapeHtml(a.label || a.account_id) + '</div><div class="meta" style="font-size:0.75rem;">' + escapeHtml(a.account_id) + '</div></div>' +
      '<span style="color:' + st.color + ';font-size:0.8rem;">' + st.dot + ' ' + st.label + '</span></div>' +
      '<div id="yt-drawer-metrics" class="meta" style="font-size:0.78rem;margin-top:0.6rem;">加载频道概览…</div>' +
      '<div style="display:flex;gap:0.35rem;flex-wrap:wrap;margin-top:0.7rem;">' +
      '<button type="button" class="btn btn-primary btn-sm" data-drawer-act="publish">发布素材</button>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-drawer-act="reauth">重新授权</button>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-drawer-act="edit">编辑</button>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-drawer-act="del">删除</button></div>' +
      '<div id="yt-drawer-data" style="margin-top:0.8rem;"></div>';
    mask.style.display = 'flex';
    fetch(apiUrl('/api/youtube-publish/accounts/' + encodeURIComponent(aid) + '/overview'), {
      headers: typeof authHeaders === 'function' ? authHeaders() : {}
    }).then(function(r) { return r.json(); }).then(function(d) {
      var box = document.getElementById('yt-drawer-metrics');
      if (box && d && d.ok) box.innerHTML = '订阅 ' + _ytFmt(d.subscriber_count) + ' · 总播放 ' + _ytFmt(d.view_count) + ' · 视频 ' + _ytFmt(d.video_count);
    }).catch(function() {});
    var dataBox = document.getElementById('yt-drawer-data');
    if (dataBox) dataBox.innerHTML = '<p class="meta" style="font-size:0.78rem;">加载频道数据…</p>';
    fetchLocalJson('/api/youtube-publish/accounts/' + encodeURIComponent(aid) + '/analytics', { headers: hdrs() })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, d: d }; }); })
      .then(function(x) {
        if (!x.ok) { if (dataBox) dataBox.innerHTML = '<p class="msg err" style="font-size:0.78rem;">' + escapeHtml((x.d && x.d.detail) || '加载失败') + '</p>'; return; }
        _ytRenderAnalytics(dataBox, x.d);
      }).catch(function(e) { if (dataBox) dataBox.innerHTML = '<p class="msg err" style="font-size:0.78rem;">' + escapeHtml(e.message || '加载失败') + '</p>'; });
    body.querySelectorAll('[data-drawer-act]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var act = btn.getAttribute('data-drawer-act');
        if (act === 'publish') { mask.style.display = 'none'; _ytOpenTest(aid); }
        else if (act === 'reauth') startOauth(aid);
        else if (act === 'edit') { mask.style.display = 'none'; openEditModal(aid); }
        else if (act === 'del') delAccount(aid);
      });
    });
  }

  function renderList() {
    var listEl = document.getElementById('youtubeAccountsList');
    var testSel = document.getElementById('youtubeAccountsTestSelect');
    if (!listEl) return;
    listEl.innerHTML = '<p class="meta">加载中…</p>';
    if (testSel) testSel.innerHTML = '<option value="">—</option>';
    fetch(apiUrl('/api/youtube-publish/accounts'), { headers: typeof authHeaders === 'function' ? authHeaders() : {} })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, d: d, status: r.status }; }); })
      .then(function(x) {
        if (!listEl) return;
        if (!x.ok) {
          listEl.innerHTML = '<p class="msg err">加载失败 ' + (x.status || '') + '</p>';
          return;
        }
        var rows = Array.isArray(x.d) ? x.d : [];
        _lastAccounts = rows;
        _ytUpdateSummary(rows);
        if (testSel) {
          testSel.innerHTML = '<option value="">选择账号</option>' + rows.map(function(a) {
            return '<option value="' + escapeAttr(a.account_id || '') + '">' + escapeHtml(a.label || a.account_id || '') + '</option>';
          }).join('');
        }
        if (!rows.length) {
          listEl.innerHTML = '<div class="page-empty-card" style="grid-column:1/-1;text-align:center;padding:2rem;">' +
            '<div style="font-size:1rem;font-weight:600;">还没有 YouTube 账号</div>' +
            '<p class="meta" style="font-size:0.82rem;margin:0.35rem 0 0.9rem;">添加后可以上传视频、查看频道数据、配置定时发布</p>' +
            '<button type="button" class="btn btn-primary btn-sm" id="ytEmptyAddBtn">+ 添加第一个账号</button></div>';
          var eb = document.getElementById('ytEmptyAddBtn');
          if (eb) eb.addEventListener('click', openAddModal);
          return;
        }
        listEl.innerHTML = rows.map(function(a) {
          var aid = escapeAttr(a.account_id || '');
          var st = _ytStatus(a.status);
          var err = (a.last_error || '').trim() ? ('<div class="msg err" style="font-size:0.72rem;margin-top:0.35rem;">' + escapeHtml(a.last_error) + '</div>') : '';
          return '<div class="skill-store-card" data-yt-card="' + aid + '" style="padding:0.9rem;cursor:pointer;">' +
            '<div style="display:flex;gap:0.6rem;align-items:center;">' +
            '<div id="yt-avatar-' + aid + '" style="width:40px;height:40px;border-radius:50%;flex:0 0 40px;background:rgba(255,0,0,0.12);display:flex;align-items:center;justify-content:center;font-weight:700;">' + escapeHtml(_ytAvatarText(a)) + '</div>' +
            '<div style="flex:1;min-width:0;"><div style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + escapeHtml(a.label || a.account_id || '') + '</div>' +
            '<div class="meta" style="font-size:0.72rem;">' + escapeHtml(a.account_id || '') + '</div></div>' +
            '<span class="yt-status" style="color:' + st.color + ';font-size:0.78rem;white-space:nowrap;">' + st.dot + ' ' + st.label + '</span></div>' +
            '<div id="yt-metrics-' + aid + '" class="meta" style="display:flex;gap:0.9rem;flex-wrap:wrap;font-size:0.76rem;margin-top:0.65rem;"><span>订阅 —</span><span>总播放 —</span><span>视频 —</span></div>' +
            err +
            '<div style="display:flex;gap:0.35rem;flex-wrap:wrap;margin-top:0.7rem;">' +
            '<button type="button" class="btn btn-primary btn-sm" data-yt-act="publish">发布素材</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-yt-act="data">查看数据</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-yt-act="schedule">定时</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-yt-act="more">更多 ▾</button></div>' +
            '<div id="yt-more-' + aid + '" style="display:none;gap:0.35rem;flex-wrap:wrap;margin-top:0.4rem;">' +
            '<button type="button" class="btn btn-ghost btn-sm" data-yt-act="edit">编辑</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-yt-act="reauth">重新授权</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-yt-act="copy">复制 ID</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-yt-act="del">删除</button></div></div>';
        }).join('');
        rows.forEach(function(a) { _ytLoadOverview(a.account_id); });
        listEl.querySelectorAll('[data-yt-card]').forEach(function(card) {
          card.addEventListener('click', function(e) {
            if (e.target.closest('button')) return;
            openDrawer(card.getAttribute('data-yt-card'));
          });
        });
        listEl.querySelectorAll('[data-yt-act]').forEach(function(btn) {
          btn.addEventListener('click', function(e) {
            e.stopPropagation();
            var card = btn.closest('[data-yt-card]');
            var aid = card ? card.getAttribute('data-yt-card') : '';
            var act = btn.getAttribute('data-yt-act');
            if (act === 'publish') _ytOpenTest(aid);
            else if (act === 'data') openDrawer(aid);
            else if (act === 'schedule') openScheduleModal(aid);
            else if (act === 'edit') openEditModal(aid);
            else if (act === 'reauth') startOauth(aid);
            else if (act === 'del') delAccount(aid);
            else if (act === 'copy') { try { navigator.clipboard.writeText(aid); } catch (e2) {} }
            else if (act === 'more') { var m = document.getElementById('yt-more-' + aid); if (m) m.style.display = m.style.display === 'flex' ? 'none' : 'flex'; }
          });
        });
      })
      .catch(function(err) {
        if (listEl) listEl.innerHTML = '<p class="msg err">' + escapeHtml((err && err.message) ? err.message : '加载失败') + '</p>';
      });
  }

  function showAnalytics(accountId, triggerBtn) {
    var aid = (accountId || '').trim();
    if (!aid) return;
    var existing = document.getElementById('yt-analytics-' + aid);
    if (existing) { existing.remove(); return; }
    var card = triggerBtn ? triggerBtn.closest('.config-block-item') : null;
    if (!card) return;
    var panel = document.createElement('div');
    panel.id = 'yt-analytics-' + aid;
    panel.style.cssText = 'margin:0.5rem 0 0.75rem;padding:0.75rem;border:1px solid var(--border);border-radius:var(--radius-sm);background:rgba(255,255,255,0.03);font-size:0.82rem;';
    panel.innerHTML = '<p class="meta">加载频道数据…</p>';
    card.appendChild(panel);
    fetchLocalJson('/api/youtube-publish/accounts/' + encodeURIComponent(aid) + '/analytics', { headers: hdrs() })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, d: d, status: r.status }; }); })
      .then(function(x) {
        if (!x.ok) {
          panel.innerHTML = '<p class="msg err">加载失败: ' + escapeHtml((x.d && x.d.detail) || ('HTTP ' + x.status)) + '</p>';
          return;
        }
        var d = x.d;
        var ch = d.channel_analytics || {};
        var videos = d.videos || [];
        var html = '<div style="margin-bottom:0.5rem;font-weight:600;">频道分析（近 28 天）</div>';
        if (ch.error) {
          html += '<p class="msg err" style="font-size:0.78rem;">' + escapeHtml(ch.error) + '</p>';
        } else if (Object.keys(ch).length > 0) {
          html += '<div style="display:flex;flex-wrap:wrap;gap:0.75rem;margin-bottom:0.5rem;">';
          if (ch.views !== undefined) html += '<div><div style="font-size:0.75rem;color:var(--text-muted);">播放</div><div style="font-size:1.1rem;font-weight:600;">' + ch.views + '</div></div>';
          if (ch.likes !== undefined) html += '<div><div style="font-size:0.75rem;color:var(--text-muted);">赞</div><div style="font-size:1.1rem;font-weight:600;">' + ch.likes + '</div></div>';
          if (ch.subscribersGained !== undefined) html += '<div><div style="font-size:0.75rem;color:var(--text-muted);">新增订阅</div><div style="font-size:1.1rem;font-weight:600;">' + ch.subscribersGained + '</div></div>';
          if (ch.estimatedMinutesWatched !== undefined) html += '<div><div style="font-size:0.75rem;color:var(--text-muted);">观看时长(分)</div><div style="font-size:1.1rem;font-weight:600;">' + ch.estimatedMinutesWatched + '</div></div>';
          html += '</div>';
        }
        if (videos.length > 0) {
          html += '<div style="margin-top:0.5rem;font-weight:600;">近期视频（' + videos.length + '）</div>';
          html += '<div style="max-height:16rem;overflow-y:auto;margin-top:0.35rem;"><table style="width:100%;border-collapse:collapse;font-size:0.78rem;"><thead><tr style="border-bottom:1px solid var(--border);text-align:left;"><th style="padding:0.25rem 0.5rem;">标题</th><th style="padding:0.25rem 0.5rem;">发布时间</th><th style="padding:0.25rem 0.5rem;">播放</th><th style="padding:0.25rem 0.5rem;">赞</th><th style="padding:0.25rem 0.5rem;">评论</th></tr></thead><tbody>';
          videos.slice(0, 30).forEach(function(v) {
            html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.05);"><td style="padding:0.2rem 0.5rem;max-width:16rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(v.title || '') + '</td><td style="padding:0.2rem 0.5rem;">' + escapeHtml((v.published_at || '').slice(0, 10)) + '</td><td style="padding:0.2rem 0.5rem;">' + (v.views || 0) + '</td><td style="padding:0.2rem 0.5rem;">' + (v.likes || 0) + '</td><td style="padding:0.2rem 0.5rem;">' + (v.comments || 0) + '</td></tr>';
          });
          html += '</tbody></table></div>';
        }
        html += '<div style="margin-top:0.5rem;"><button type="button" class="btn btn-ghost btn-sm yt-analytics-close" style="font-size:0.75rem;">收起</button></div>';
        panel.innerHTML = html;
        panel.querySelector('.yt-analytics-close').addEventListener('click', function() { panel.remove(); });
      })
      .catch(function(e) {
        panel.innerHTML = '<p class="msg err">' + escapeHtml(e.message || '请求失败') + '</p>';
      });
  }

  function startOauth(accountId, onDone) {
    var aid = (accountId || '').trim();
    if (!aid) return;
    fetch(apiUrl('/api/youtube-publish/accounts/' + encodeURIComponent(aid) + '/oauth/start'), {
      method: 'POST',
      headers: hdrs(),
      body: JSON.stringify({ open_chromium: true })
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, d: d, status: r.status }; }); })
      .then(function(x) {
        if (!x.ok) {
          alert((x.d && x.d.detail) ? x.d.detail : ('请求失败 HTTP ' + (x.status || '')));
          return;
        }
        var u = (x.d && x.d.url) ? String(x.d.url) : '';
        var opened = !!(x.d && x.d.chromium_opened);
        var cmsg = (x.d && x.d.chromium_message) ? String(x.d.chromium_message) : '';
        if (opened) {
          startOauthRefreshPolling(aid, onDone);
          return;
        }
        if (u) {
          startOauthRefreshPolling();
          alert('未能自动打开 Google Chrome，请复制这个链接到 Chrome 打开：\n' + u + (cmsg ? ('\n\n' + cmsg) : ''));
        } else if (cmsg) {
          alert(cmsg);
        }
      })
      .catch(function(e) {
        alert(e && e.message ? e.message : '请求失败');
      });
  }

  function closeScheduleModal() {
    var m = document.getElementById('youtubeScheduleModal');
    if (m) m.classList.remove('visible');
    _youtubeScheduleAid = '';
  }

  function parseScheduleAssetIds(text) {
    var lines = String(text || '').split(/[\n\r,]+/);
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var s = (lines[i] || '').trim();
      if (s && out.indexOf(s) === -1) out.push(s);
    }
    return out;
  }

  function openScheduleModal(accountId) {
    var aid = (accountId || '').trim();
    if (!aid) return;
    _youtubeScheduleAid = aid;
    var m = document.getElementById('youtubeScheduleModal');
    var title = document.getElementById('youtubeScheduleModalTitle');
    var smsg = document.getElementById('youtubeSchedMsg');
    var meta = document.getElementById('youtubeSchedMeta');
    if (title) title.textContent = '定时发布 · ' + aid;
    if (smsg) { smsg.style.display = 'none'; smsg.textContent = ''; }
    if (meta) meta.textContent = '加载中…';
    if (m) m.classList.add('visible');
    fetch(apiUrl('/api/youtube-publish/accounts/' + encodeURIComponent(aid) + '/publish-schedule'), { headers: hdrs() })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, d: d, status: r.status }; }); })
      .then(function(x) {
        if (!x.ok) {
          if (meta) meta.textContent = '';
          showMsg(smsg, (x.d && x.d.detail) ? x.d.detail : '加载失败', true);
          return;
        }
        var d = x.d || {};
        var en = document.getElementById('youtubeSchedEnabled');
        var iv = document.getElementById('youtubeSchedInterval');
        var ta = document.getElementById('youtubeSchedAssetIds');
        var mo = document.getElementById('youtubeSchedMaterialOrigin');
        var pr = document.getElementById('youtubeSchedPrivacy');
        var cat = document.getElementById('youtubeSchedCategory');
        var ti = document.getElementById('youtubeSchedTitle');
        var ds = document.getElementById('youtubeSchedDesc');
        if (en) en.checked = !!d.enabled;
        if (iv) iv.value = d.interval_minutes != null ? d.interval_minutes : 60;
        if (ta) ta.value = Array.isArray(d.asset_ids) ? d.asset_ids.join('\n') : '';
        if (mo) mo.value = (d.material_origin === 'ai_generated') ? 'ai_generated' : 'script_batch';
        if (pr) pr.value = d.privacy_status || 'public';
        if (cat) cat.value = d.category_id || '22';
        if (ti) ti.value = d.title || '';
        if (ds) ds.value = d.description || '';
        var metaStr = '';
        if (d.next_run_at) metaStr += '下次（UTC）：' + d.next_run_at;
        if (d.last_run_at) metaStr += (metaStr ? ' · ' : '') + '上次：' + d.last_run_at;
        if (d.last_video_id) metaStr += ' · video_id：' + d.last_video_id;
        if (d.last_run_error) metaStr += ' · 错误：' + d.last_run_error;
        if (meta) meta.textContent = metaStr || '—';
      })
      .catch(function(e) {
        if (meta) meta.textContent = '';
        showMsg(smsg, e && e.message ? e.message : '加载失败', true);
      });
  }

  function saveYoutubeSchedule() {
    var aid = (_youtubeScheduleAid || '').trim();
    var smsg = document.getElementById('youtubeSchedMsg');
    var btn = document.getElementById('youtubeSchedSaveBtn');
    if (!aid) return;
    var en = document.getElementById('youtubeSchedEnabled');
    var iv = document.getElementById('youtubeSchedInterval');
    var ta = document.getElementById('youtubeSchedAssetIds');
    var mo = document.getElementById('youtubeSchedMaterialOrigin');
    var pr = document.getElementById('youtubeSchedPrivacy');
    var cat = document.getElementById('youtubeSchedCategory');
    var ti = document.getElementById('youtubeSchedTitle');
    var ds = document.getElementById('youtubeSchedDesc');
    var body = {
      enabled: en && en.checked,
      interval_minutes: iv ? parseInt(iv.value, 10) || 60 : 60,
      asset_ids: parseScheduleAssetIds(ta ? ta.value : ''),
      material_origin: (mo && mo.value === 'ai_generated') ? 'ai_generated' : 'script_batch',
      privacy_status: pr ? pr.value : 'public',
      category_id: cat ? (cat.value || '22').trim() : '22',
      title: ti ? ti.value.trim() : '',
      description: ds ? ds.value.trim() : ''
    };
    if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
    showMsg(smsg, '', false);
    fetch(apiUrl('/api/youtube-publish/accounts/' + encodeURIComponent(aid) + '/publish-schedule'), {
      method: 'PUT',
      headers: hdrs(),
      body: JSON.stringify(body)
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, d: d, status: r.status }; }); })
      .then(function(x) {
        if (!x.ok) {
          showMsg(smsg, (x.d && x.d.detail) ? x.d.detail : ('保存失败 HTTP ' + (x.status || '')), true);
          return;
        }
        var d = x.d || {};
        var meta = document.getElementById('youtubeSchedMeta');
        var metaStr = '';
        if (d.next_run_at) metaStr += '下次（UTC）：' + d.next_run_at;
        if (d.last_run_at) metaStr += (metaStr ? ' · ' : '') + '上次：' + d.last_run_at;
        if (d.last_video_id) metaStr += ' · video_id：' + d.last_video_id;
        if (d.last_run_error) metaStr += ' · 错误：' + d.last_run_error;
        if (meta) meta.textContent = metaStr || '—';
        showMsg(smsg, '已保存', false);
      })
      .catch(function(e) { showMsg(smsg, e && e.message ? e.message : '保存失败', true); })
      .finally(function() {
        if (btn) { btn.disabled = false; btn.textContent = '保存'; }
      });
  }

  function delAccount(accountId) {
    var aid = (accountId || '').trim();
    if (!aid) return;
    if (!confirm('确定删除账号 ' + aid + '？')) return;
    fetch(apiUrl('/api/youtube-publish/accounts/' + encodeURIComponent(aid)), { method: 'DELETE', headers: hdrs() })
      .then(function(r) {
        if (!r.ok) return r.json().then(function(d) { throw new Error((d && d.detail) || ('HTTP ' + r.status)); });
        renderList();
        if (typeof loadSkillStore === 'function') loadSkillStore();
      })
      .catch(function(e) { alert(e.message || '删除失败'); });
  }

  function closeAddModal() {
    var m = document.getElementById('youtubeAccountAddModal');
    if (m) m.classList.remove('visible');
  }

  function _setModalUi(mode) {
    var titleEl = document.getElementById('youtubeAccountModalTitle');
    var hintEl = document.getElementById('youtubeAccountModalHint');
    var saveBtn = document.getElementById('youtubeAccountAddSaveBtn');
    if (titleEl) titleEl.textContent = mode === 'edit' ? '编辑 YouTube 账号' : '添加 YouTube 账号';
    if (hintEl) {
      hintEl.textContent = mode === 'edit'
        ? '可补填代理、修改 Client ID/Secret（Secret 留空则不修改）。若更换 OAuth 客户端，请保存后再点「浏览器授权」。'
        : '保存后将生成账号 ID（yt_ 开头），并自动打开浏览器向 Google 授权。';
    }
    if (saveBtn) saveBtn.textContent = mode === 'edit' ? '保存' : '保存并授权';
  }

  function openAddModal() {
    _youtubeModalMode = 'add';
    _youtubeEditAid = '';
    _ytPendingOauthId = '';
    _ytSetStep(1);
    _setModalUi('add');
    var m = document.getElementById('youtubeAccountAddModal');
    var msg = document.getElementById('youtubeAccountAddMsg');
    if (msg) { msg.style.display = 'none'; msg.textContent = ''; }
    var ids = ['youtubeAddLabel', 'youtubeAddClientId', 'youtubeAddClientSecret', 'youtubeAddProxy', 'youtubeAddProxyUser', 'youtubeAddProxyPass'];
    ids.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.value = '';
    });
    if (m) m.classList.add('visible');
  }

  function openEditModal(accountId) {
    _ytSetStep(1);
    var aid = (accountId || '').trim();
    if (!aid) return;
    var row = _lastAccounts.filter(function(a) { return (a.account_id || '') === aid; })[0];
    if (!row) {
      alert('找不到该账号，请刷新列表后重试');
      return;
    }
    _youtubeModalMode = 'edit';
    _youtubeEditAid = aid;
    _setModalUi('edit');
    var msg = document.getElementById('youtubeAccountAddMsg');
    if (msg) { msg.style.display = 'none'; msg.textContent = ''; }
    var labelEl = document.getElementById('youtubeAddLabel');
    var cidEl = document.getElementById('youtubeAddClientId');
    var csecEl = document.getElementById('youtubeAddClientSecret');
    var psEl = document.getElementById('youtubeAddProxy');
    var puEl = document.getElementById('youtubeAddProxyUser');
    var ppEl = document.getElementById('youtubeAddProxyPass');
    if (labelEl) labelEl.value = (row.label || '').trim();
    if (cidEl) cidEl.value = (row.oauth_client_id || '').trim();
    if (csecEl) csecEl.value = '';
    if (psEl) psEl.value = (row.proxy_server || '').trim();
    if (puEl) puEl.value = (row.proxy_username || '').trim();
    if (ppEl) ppEl.value = '';
    var m = document.getElementById('youtubeAccountAddModal');
    if (m) m.classList.add('visible');
  }

  function saveNewAccount() {
    var msg = document.getElementById('youtubeAccountAddMsg');
    var label = (document.getElementById('youtubeAddLabel') || {}).value || '';
    var cid = (document.getElementById('youtubeAddClientId') || {}).value || '';
    var csec = (document.getElementById('youtubeAddClientSecret') || {}).value || '';
    var ps = (document.getElementById('youtubeAddProxy') || {}).value || '';
    var pu = (document.getElementById('youtubeAddProxyUser') || {}).value || '';
    var pp = (document.getElementById('youtubeAddProxyPass') || {}).value || '';
    if (!cid.trim()) {
      showMsg(msg, '请填写 OAuth Client ID', true);
      return;
    }
    if (_youtubeModalMode === 'edit') {
      if (!_youtubeEditAid) {
        showMsg(msg, '内部错误：缺少 account_id', true);
        return;
      }
      var body = {
        label: label.trim(),
        oauth_client_id: cid.trim(),
        proxy_server: ps.trim(),
        proxy_username: pu.trim()
      };
      if (csec.trim()) body.oauth_client_secret = csec.trim();
      if (pp.trim()) body.proxy_password = pp.trim();
      var btn = document.getElementById('youtubeAccountAddSaveBtn');
      if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
      fetch(apiUrl('/api/youtube-publish/accounts/' + encodeURIComponent(_youtubeEditAid)), {
        method: 'PATCH',
        headers: hdrs(),
        body: JSON.stringify(body)
      })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, d: d, status: r.status }; }); })
        .then(function(x) {
          if (!x.ok) {
            showMsg(msg, (x.d && x.d.detail) ? x.d.detail : ('保存失败 HTTP ' + (x.status || '')), true);
            return;
          }
          closeAddModal();
          renderList();
          if (typeof loadSkillStore === 'function') loadSkillStore();
        })
        .catch(function(e) { showMsg(msg, e && e.message ? e.message : '保存失败', true); })
        .finally(function() {
          if (btn) { btn.disabled = false; btn.textContent = '保存'; }
        });
      return;
    }
    if (!csec.trim()) {
      showMsg(msg, '请填写 OAuth Client ID 与 Client Secret', true);
      return;
    }
    var btn = document.getElementById('youtubeAccountAddSaveBtn');
    if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
    fetch(apiUrl('/api/youtube-publish/accounts'), {
      method: 'POST',
      headers: hdrs(),
      body: JSON.stringify({
        label: label.trim(),
        oauth_client_id: cid.trim(),
        oauth_client_secret: csec.trim(),
        proxy_server: ps.trim(),
        proxy_username: pu.trim(),
        proxy_password: pp.trim()
      })
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, d: d, status: r.status }; }); })
      .then(function(x) {
        if (!x.ok) {
          showMsg(msg, (x.d && x.d.detail) ? x.d.detail : ('保存失败 HTTP ' + (x.status || '')), true);
          return;
        }
        var newId = (x.d && x.d.account_id) ? String(x.d.account_id) : '';
        _ytPendingOauthId = newId;
        renderList();
        if (typeof loadSkillStore === 'function') loadSkillStore();
        _ytSetStep(2);
        if (newId) {
          startOauth(newId, function(ready) { _ytFinishOauth(newId, ready); });
        }
      })
      .catch(function(e) { showMsg(msg, e && e.message ? e.message : '保存失败', true); })
      .finally(function() {
        if (btn) { btn.disabled = false; btn.textContent = '保存并授权'; }
      });
  }

  function _ytSetStep(step) {
    var n = parseInt(step, 10) || 1;
    ['1', '2', '3'].forEach(function(k) {
      var box = document.getElementById('youtubeAddStep' + k);
      if (box) box.style.display = (String(n) === k) ? '' : 'none';
      var badge = document.querySelector('[data-yt-step="' + k + '"]');
      if (badge) badge.style.color = (String(n) === k) ? 'var(--text)' : 'var(--text-muted)';
    });
  }

  function _ytFinishOauth(aid, ready) {
    if (ready) {
      var a = (_lastAccounts || []).filter(function(x) { return x.account_id === aid; })[0] || {};
      var txt = document.getElementById('youtubeAddSuccessText');
      if (txt) txt.textContent = '账号：' + (a.label || aid) + ' · ' + aid + ' · 已授权，可开始上传';
      _ytSetStep(3);
      renderList();
    } else {
      var msg = document.getElementById('youtubeAddWaitMsg');
      if (msg) msg.textContent = '授权尚未完成，请完成后重试';
    }
  }

  window.loadYoutubeAccountsPage = function() {
    var pre = document.getElementById('youtubeAccountsRedirectPre');
    if (pre) pre.textContent = oauthRedirectUri();
    renderList();
  };

  window.addEventListener('focus', function() {
    var view = (location.hash || '').replace(/^#/, '');
    if (view === 'youtube-accounts') renderList();
  });

  var backBtn = document.getElementById('youtubeAccountsBackBtn');
  if (backBtn) {
    backBtn.addEventListener('click', function() {
      var nav = document.querySelector('.nav-left-item[data-view="skill-store"]');
      if (nav) nav.click();
      try { history.replaceState(null, '', location.pathname + location.search); } catch (e2) {}
    });
  }
  var addBtn = document.getElementById('youtubeAccountsAddBtn');
  if (addBtn) addBtn.addEventListener('click', openAddModal);
  var addClose = document.getElementById('youtubeAccountAddModalClose');
  if (addClose) addClose.addEventListener('click', closeAddModal);
  var addModal = document.getElementById('youtubeAccountAddModal');
  if (addModal) {
    addModal.addEventListener('click', function(e) { if (e.target === addModal) closeAddModal(); });
  }
  var addSave = document.getElementById('youtubeAccountAddSaveBtn');
  if (addSave) addSave.addEventListener('click', saveNewAccount);

  var schedClose = document.getElementById('youtubeScheduleModalClose');
  if (schedClose) schedClose.addEventListener('click', closeScheduleModal);
  var schedModal = document.getElementById('youtubeScheduleModal');
  if (schedModal) {
    schedModal.addEventListener('click', function(e) { if (e.target === schedModal) closeScheduleModal(); });
  }
  var schedSave = document.getElementById('youtubeSchedSaveBtn');
  if (schedSave) schedSave.addEventListener('click', saveYoutubeSchedule);

  var refreshBtn = document.getElementById('youtubeAccountsRefreshBtn');
  if (refreshBtn) refreshBtn.addEventListener('click', renderList);

  var copyRedirectBtn = document.getElementById('youtubeCopyRedirectBtn');
  if (copyRedirectBtn) copyRedirectBtn.addEventListener('click', function() {
    try { navigator.clipboard.writeText(oauthRedirectUri()); } catch (e3) {}
  });

  var reopenBtn = document.getElementById('youtubeAddReopenBtn');
  if (reopenBtn) reopenBtn.addEventListener('click', function() {
    if (_ytPendingOauthId) startOauth(_ytPendingOauthId, function(ready) { _ytFinishOauth(_ytPendingOauthId, ready); });
  });

  var addCancelBtn = document.getElementById('youtubeAddCancelBtn');
  if (addCancelBtn) addCancelBtn.addEventListener('click', closeAddModal);

  var addDoneBtn = document.getElementById('youtubeAddDoneBtn');
  if (addDoneBtn) addDoneBtn.addEventListener('click', function() { closeAddModal(); renderList(); });

  var drawerClose = document.getElementById('youtubeDrawerClose');
  if (drawerClose) drawerClose.addEventListener('click', function() {
    var m = document.getElementById('youtubeAccountDrawer'); if (m) m.style.display = 'none';
  });
  var drawerMask = document.getElementById('youtubeAccountDrawer');
  if (drawerMask) drawerMask.addEventListener('click', function(e) {
    if (e.target === drawerMask) drawerMask.style.display = 'none';
  });

  var testBtn = document.getElementById('youtubeAccountsTestBtn');
  if (testBtn) {
    testBtn.addEventListener('click', function() {
      var testMsg = document.getElementById('youtubeAccountsTestMsg');
      var aidIn = document.getElementById('youtubeAccountsTestAssetId');
      var sel = document.getElementById('youtubeAccountsTestSelect');
      var titleIn = document.getElementById('youtubeAccountsTestTitle');
      var moSel = document.getElementById('youtubeAccountsTestMaterialOrigin');
      var mo = (moSel && moSel.value) ? moSel.value : 'script_batch';
      var vid = aidIn ? aidIn.value.trim() : '';
      var acc = sel ? sel.value.trim() : '';
      if (!vid) { showMsg(testMsg, '请填写素材 asset_id', true); return; }
      if (!acc) { showMsg(testMsg, '请选择 YouTube 账号', true); return; }
      testBtn.disabled = true;
      testBtn.textContent = '上传中…';
      showMsg(testMsg, '', false);
      fetch(apiUrl('/api/youtube-publish/upload'), {
        method: 'POST',
        headers: hdrs(),
        body: JSON.stringify({
          account_id: acc,
          asset_id: vid,
          title: (titleIn && titleIn.value.trim()) ? titleIn.value.trim() : 'Test upload',
          description: 'Uploaded from Lobster',
          material_origin: mo
        })
      })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, d: d, status: r.status }; }); })
        .then(function(x) {
          if (!x.ok) {
            showMsg(testMsg, (x.d && x.d.detail) ? x.d.detail : ('失败 HTTP ' + (x.status || '')), true);
            return;
          }
          var d = x.d || {};
          var url = d.watch_url || '';
          showMsg(testMsg, '成功：video_id=' + (d.video_id || '') + (url ? ' ' + url : ''), false);
        })
        .catch(function(err) { showMsg(testMsg, (err && err.message) ? err.message : '请求失败', true); })
        .finally(function() {
          testBtn.disabled = false;
          testBtn.textContent = '测试上传';
        });
    });
  }
})();
