// ── Publish Management (发布管理) ─────────────────────────────────

var _currentPubTab = 'accounts';

function bindPublishTabs() {
  document.querySelectorAll('.pub-tab').forEach(function(tab) {
    if (tab._pubTabBound) return;
    tab._pubTabBound = true;
    tab.addEventListener('click', function() {
      if (typeof window.closeAllPublishModals === 'function') window.closeAllPublishModals();
      var target = tab.getAttribute('data-pub-tab');
      if (!target || target === _currentPubTab) return;
      _currentPubTab = target;
      document.querySelectorAll('.pub-tab').forEach(function(t) { t.classList.remove('active'); });
      tab.classList.add('active');
      var accounts = document.getElementById('pubTabAccounts');
      var tasks = document.getElementById('pubTabTasks');
      if (accounts) accounts.style.display = (target === 'accounts') ? '' : 'none';
      if (tasks) tasks.style.display = (target === 'tasks') ? '' : 'none';
      if (target === 'accounts') {
        hideAccountDetailPanel();
        loadAccounts();
      }
      if (target === 'tasks') loadTasks();
    });
  });
}

var PLATFORM_NAMES = { douyin: '抖音', bilibili: 'B站', xiaohongshu: '小红书', kuaishou: '快手', toutiao: '今日头条', wechat_channels: '视频号', douyin_shop: '抖店', xiaohongshu_shop: '小红书店铺', alibaba1688: '1688', taobao: '淘宝', pinduoduo: '拼多多' };
var PUBLISH_ACCOUNT_PLATFORMS = ['douyin', 'wechat_channels', 'bilibili', 'xiaohongshu', 'kuaishou', 'toutiao'];
var ECOMMERCE_ACCOUNT_PLATFORMS = ['douyin_shop', 'xiaohongshu_shop', 'alibaba1688', 'taobao', 'pinduoduo'];
var ECOMMERCE_PLATFORMS = { douyin_shop: true, xiaohongshu_shop: true, alibaba1688: true, taobao: true, pinduoduo: true };
var _currentAccountType = 'publish';
var STATUS_LABELS = { active: '已登录', online: '已登录', pending: '待登录', waiting: '待登录', offline: '未登录', error: '异常' };
var STATUS_COLORS = { active: '#34d399', online: '#34d399', pending: '#fb923c', waiting: '#fb923c', offline: '#94a3b8', error: '#f87171' };

/** 发布/素材/创作者同步须走本机 lobster_online（LOCAL_API_BASE），勿用公网 API_BASE */
function publishLocalBase() {
  var b = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? String(LOCAL_API_BASE).replace(/\/$/, '') : '';
  return b;
}

function _accountTypeForPlatform(platform) {
  return ECOMMERCE_PLATFORMS[platform] ? 'ecommerce' : 'publish';
}

function _platformsForAccountType(type) {
  return type === 'ecommerce' ? ECOMMERCE_ACCOUNT_PLATFORMS : PUBLISH_ACCOUNT_PLATFORMS;
}

function _accountTypeUiText(type) {
  if (type === 'ecommerce') {
    return {
      addButton: '添加电商平台账号',
      modalTitle: '添加电商平台账号',
      platformLabel: '电商平台',
      filterLabel: '选电商平台：',
      emptyAll: '暂无电商平台账号。请添加账号后登录。',
      emptyPlatform: '该电商平台暂无账号。'
    };
  }
  return {
    addButton: '添加发布账号',
    modalTitle: '添加发布账号',
    platformLabel: '账号平台',
    filterLabel: '选账号平台：',
    emptyAll: '暂无发布账号。请添加账号后登录。',
    emptyPlatform: '该账号平台暂无发布账号。'
  };
}

function _renderPlatformOptions(selectEl, type, includeAll, selectedValue) {
  if (!selectEl) return;
  var platforms = _platformsForAccountType(type);
  var html = includeAll ? '<option value="">全部</option>' : '';
  platforms.forEach(function(platform) {
    html += '<option value="' + escapeAttr(platform) + '">' + escapeHtml(PLATFORM_NAMES[platform] || platform) + '</option>';
  });
  selectEl.innerHTML = html;
  if (selectedValue && platforms.indexOf(selectedValue) !== -1) {
    selectEl.value = selectedValue;
  } else if (!includeAll && platforms.length) {
    selectEl.value = platforms[0];
  } else {
    selectEl.value = '';
  }
}

function _syncAccountTypeUi() {
  var text = _accountTypeUiText(_currentAccountType);
  document.querySelectorAll('.account-type-tab').forEach(function(tab) {
    tab.classList.toggle('active', tab.getAttribute('data-account-type') === _currentAccountType);
  });
  var addBtn = document.getElementById('openAddPublishAccountModalBtn');
  if (addBtn) addBtn.textContent = text.addButton;
  var filterLabel = document.getElementById('accountPlatformFilterLabel');
  if (filterLabel) filterLabel.textContent = text.filterLabel;
  var filter = document.getElementById('accountPlatformFilter');
  var current = filter ? filter.value : '';
  _renderPlatformOptions(filter, _currentAccountType, true, current);
}

function _syncAddAccountModalPlatformOptions() {
  var filter = document.getElementById('accountPlatformFilter');
  var selected = filter ? filter.value : '';
  var text = _accountTypeUiText(_currentAccountType);
  var title = document.getElementById('addPublishAccountModalTitleText');
  if (title) title.textContent = text.modalTitle;
  var label = document.getElementById('modalAddAcctPlatformLabel');
  if (label) label.textContent = text.platformLabel;
  _renderPlatformOptions(document.getElementById('modalAddAcctPlatform'), _currentAccountType, false, selected);
}

/** 解析 fetch 响应：静态服返回 HTML 时给出可操作的报错 */
function _publishParseResponse(r) {
  return r.text().then(function(text) {
    var d = {};
    try {
      d = text ? JSON.parse(text) : {};
    } catch (e1) {
      var hint = 'HTTP ' + r.status;
      if (text && (/<!DOCTYPE/i.test(text) || /<html/i.test(text))) {
        hint += '：未打到本机 lobster_online 后端（架构见 docs/架构说明_server与本地职责.md）。请在本机执行 LOBSTER_EDITION=online python3 backend/run.py；若后端与静态不同端口，用 ?local_api= 或 localStorage.lobster_local_api_base 指定后端根 URL';
      } else if (text) {
        hint += '（非 JSON）：' + text.slice(0, 200);
      } else {
        hint += '（空响应）';
      }
      return Promise.reject(new Error(hint));
    }
    return { ok: r.ok, status: r.status, d: d };
  });
}

// ── Accounts ─────────────────────────────────────────────────────

var _allAccounts = [];
var _detailAccountId = null;
var _schModalAccountId = null;
var _schTasksAccountId = null;
var _schTasksPollTimer = null;
var _creatorDefaultTtlSec = 3600;
var _detailScheduleCache = null;
/** 审核发布子 Tab：current | history */
var _detailReviewSubTab = 'current';

function _formatScheduleIntervalMinutes(m) {
  m = parseInt(m, 10);
  if (!m || m < 1) m = 60;
  if (m % 1440 === 0 && m >= 1440) return '每' + (m / 1440) + '天';
  if (m % 60 === 0 && m >= 60) return '每' + (m / 60) + '小时';
  return '每' + m + '分钟';
}

function _scheduleKindLabel(kind) {
  return kind === 'video' ? '视频' : '图文';
}

function _scheduleVideoBranchHint(sch) {
  if (!sch || sch.schedule_kind !== 'video') return '';
  var aid = (sch.video_source_asset_id || '').trim();
  return aid ? '图生视频' : '文生视频';
}

function _schUpdateScheduleKindUI() {
  var sel = document.getElementById('schScheduleKind');
  var wrap = document.getElementById('schVideoAssetWrap');
  var lbl = document.getElementById('schRequirementsLabel');
  var hint = document.getElementById('schRequirementsHint');
  if (!sel || !wrap || !lbl || !hint) return;
  var isVideo = sel.value === 'video';
  wrap.style.display = isVideo ? '' : 'none';
  if (isVideo) {
    lbl.textContent = '生产要求';
    hint.textContent = '参考图请填「素材 ID」；正文按提纲写：模型、画面方向、生成素材、发布文案、是否发布（见上方说明）。';
  } else {
    lbl.textContent = '描述需求';
    hint.textContent = '可用上方提纲：模型、画面方向、生成素材、发布文案、是否发布；不需要的栏写「无」。';
  }
}

function _schUpdatePublishModeUI() {
  var pm = document.getElementById('schPublishMode');
  var wrap = document.getElementById('schReviewVariantWrap');
  var act = document.getElementById('schModalReviewActions');
  if (!pm) return;
  var isReview = pm.value === 'review';
  if (wrap) wrap.style.display = isReview ? '' : 'none';
  if (act) act.style.display = isReview ? '' : 'none';
}

/** 与「保存」弹窗相同的校验，供生成审核稿前 PUT 使用 */
function _buildSchedulePutBodyFromModal(msgEl) {
  var enabled = document.getElementById('schEnabled').checked;
  var intervalMinutes = _intervalMinutesFromModal();
  var req = document.getElementById('schRequirements').value || '';
  var skEl = document.getElementById('schScheduleKind');
  var scheduleKind = skEl && skEl.value === 'video' ? 'video' : 'image';
  var videoAssetId = '';
  if (scheduleKind === 'video') {
    var aEl = document.getElementById('schVideoAssetId');
    videoAssetId = ((aEl && aEl.value) || '').trim();
    if (videoAssetId.length > 64) {
      if (msgEl) {
        msgEl.textContent = '素材 ID 最长 64 字符。';
        msgEl.style.display = 'block';
        msgEl.className = 'msg err';
      }
      return { ok: false };
    }
  }
  if (intervalMinutes == null) {
    if (msgEl) {
      msgEl.textContent = '请填写有效间隔：数字 ≥1，合计不超过 10080 分钟（7 天）。';
      msgEl.style.display = 'block';
      msgEl.className = 'msg err';
    }
    return { ok: false };
  }
  var putBody = {
    enabled: enabled,
    interval_minutes: intervalMinutes,
    schedule_kind: scheduleKind,
    requirements_text: req || null
  };
  if (scheduleKind === 'video') {
    putBody.video_source_asset_id = videoAssetId || null;
  }
  var pmEl = document.getElementById('schPublishMode');
  var rvcEl = document.getElementById('schReviewVariantCount');
  if (pmEl) {
    putBody.schedule_publish_mode = pmEl.value === 'review' ? 'review' : 'immediate';
  }
  if (rvcEl && pmEl && pmEl.value === 'review') {
    putBody.review_variant_count = Math.max(1, Math.min(10, parseInt(rvcEl.value, 10) || 3));
  }
  return { ok: true, body: putBody };
}

function _parsePublishJsonResponse(r) {
  return r.text().then(function(text) {
    var d = {};
    try {
      d = text ? JSON.parse(text) : {};
    } catch (e1) {
      d = { detail: text ? text.slice(0, 600) : ('HTTP ' + r.status) };
    }
    return { ok: r.ok, status: r.status, data: d };
  });
}

function _setReviewGenBusy(busy) {
  document.querySelectorAll('[data-action="review-generate"]').forEach(function(b) {
    b.disabled = !!busy;
  });
  document.querySelectorAll('[data-action="review-generate-assets"]').forEach(function(b) {
    b.disabled = !!busy;
  });
}

/** 智能生成提示词开始前：清空当前草稿列表并显示等待（仅当前详情账号与列表一致时更新 DOM） */
function _reviewGenerateClearDraftsUi(accountId) {
  if (_detailAccountId !== accountId) return;
  if (_detailScheduleCache) {
    _detailScheduleCache.review_drafts_json = [];
  }
  var ac = _allAccounts.filter(function(a) { return a.id === accountId; })[0];
  if (ac && ac.creator_schedule) {
    ac.creator_schedule = Object.assign({}, ac.creator_schedule, { review_drafts_json: [] });
  }
  var host = document.getElementById('accountDetailReviewDraftsList');
  if (host) {
    host.innerHTML = '<p class="meta" style="margin:0;font-size:0.82rem;color:var(--text-muted);">正在重新生成提示词，请稍候…</p>';
  }
}

function _postReviewGenerate(accountId, variantCount) {
  _reviewGenerateClearDraftsUi(accountId);
  var msgEl = document.getElementById('accountDetailReviewMsg');
  var modalMsg = document.getElementById('schModalMsg');
  if (msgEl) {
    msgEl.textContent = '正在智能生成提示词，请稍候（可能需数分钟）…';
    msgEl.style.display = 'block';
    msgEl.style.color = 'var(--text-muted)';
  }
  return fetch(publishLocalBase() + '/api/accounts/' + accountId + '/creator-schedule/review-generate', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
    body: JSON.stringify({ variant_count: variantCount })
  })
    .then(function(r) { return _parsePublishJsonResponse(r); })
    .then(function(x) {
      if (!x.ok) {
        var det = x.data && x.data.detail;
        var t = typeof det === 'string' ? det : JSON.stringify(det || x.data);
        if (msgEl) {
          msgEl.textContent = t;
          msgEl.style.display = 'block';
          msgEl.style.color = '#f87171';
        }
        if (modalMsg) {
          modalMsg.textContent = t;
          modalMsg.style.display = 'block';
          modalMsg.className = 'msg err';
        }
        return;
      }
      _detailScheduleCache = x.data;
      var ac = _allAccounts.filter(function(a) { return a.id === accountId; })[0];
      if (ac) {
        ac.creator_schedule = Object.assign({}, ac.creator_schedule || {}, x.data);
        if (_detailAccountId === accountId) _refreshDetailScheduleSummary(ac);
      }
      if (_detailAccountId === accountId) _detailApplyScheduleTabFields(x.data);
      var n = (x.data && x.data.review_drafts_json && x.data.review_drafts_json.length) ? x.data.review_drafts_json.length : 0;
      var okT = '已生成 ' + n + ' 条提示词草稿，可在下方编辑后再点「生成发布内容」。';
      if (msgEl) {
        msgEl.textContent = okT;
        msgEl.style.display = 'block';
        msgEl.style.color = '#86efac';
      }
      if (modalMsg) {
        modalMsg.textContent = okT;
        modalMsg.style.display = 'block';
        modalMsg.className = 'msg ok';
      }
      loadAccounts();
      _refreshReviewSnapshotsIfNeeded();
    })
    .catch(function() {
      if (msgEl) {
        msgEl.textContent = '请求失败（网络或服务异常）';
        msgEl.style.display = 'block';
        msgEl.style.color = '#f87171';
      }
      if (modalMsg) {
        modalMsg.textContent = '请求失败（网络或服务异常）';
        modalMsg.style.display = 'block';
        modalMsg.className = 'msg err';
      }
    });
}

function _handleReviewGenerateClick() {
  var base = publishLocalBase();
  var msgEl = document.getElementById('accountDetailReviewMsg');
  var modalMsg = document.getElementById('schModalMsg');
  if (!base) {
    var t0 = '未配置本机 API。请用本机运行 lobster_online 后端后从该地址打开页面（需 LOCAL_API_BASE / 同源）。';
    if (msgEl) {
      msgEl.textContent = t0;
      msgEl.style.display = 'block';
      msgEl.style.color = '#f87171';
    }
    if (modalMsg) {
      modalMsg.textContent = t0;
      modalMsg.style.display = 'block';
      modalMsg.className = 'msg err';
    }
    return;
  }
  var accountId = _detailAccountId || _schModalAccountId;
  if (!accountId) {
    var t1 = '请先进入账号详情或打开「完整配置」弹窗后再生成。';
    if (msgEl) {
      msgEl.textContent = t1;
      msgEl.style.display = 'block';
      msgEl.style.color = '#f87171';
    }
    alert(t1);
    return;
  }
  var modalMask = document.getElementById('creatorScheduleModal');
  var modalOpen = modalMask && modalMask.style.display === 'flex';
  var nEl = document.getElementById('accountDetailReviewVariantCount');
  if (modalOpen) {
    var rvcM = document.getElementById('schReviewVariantCount');
    if (rvcM) nEl = rvcM;
  }
  var n = Math.max(1, Math.min(10, parseInt(nEl && nEl.value, 10) || 3));
  _setReviewGenBusy(true);
  function afterPost() {
    _setReviewGenBusy(false);
  }
  if (modalOpen && _schModalAccountId === accountId) {
    var built = _buildSchedulePutBodyFromModal(modalMsg);
    if (!built.ok) {
      _setReviewGenBusy(false);
      return;
    }
    fetch(publishLocalBase() + '/api/accounts/' + accountId + '/creator-schedule', {
      method: 'PUT',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
      body: JSON.stringify(built.body)
    })
      .then(function(r) { return _parsePublishJsonResponse(r); })
      .then(function(x) {
        if (!x.ok) {
          var det = x.data && x.data.detail;
          var te = typeof det === 'string' ? det : JSON.stringify(det || x.data);
          if (modalMsg) {
            modalMsg.textContent = te;
            modalMsg.style.display = 'block';
            modalMsg.className = 'msg err';
          }
          return;
        }
        if (_detailAccountId === accountId && x.data) {
          _detailScheduleCache = Object.assign({}, x.data, { review_drafts_json: [] });
          var ac = _allAccounts.filter(function(a) { return a.id === accountId; })[0];
          if (ac) {
            ac.creator_schedule = Object.assign({}, ac.creator_schedule || {}, x.data, {
              review_drafts_json: []
            });
            _refreshDetailScheduleSummary(ac);
          }
          _detailApplyScheduleTabFields(_detailScheduleCache, { skipDrafts: true });
        }
        return _postReviewGenerate(accountId, n);
      })
      .catch(function() {
        if (modalMsg) {
          modalMsg.textContent = '保存失败，无法继续生成';
          modalMsg.style.display = 'block';
          modalMsg.className = 'msg err';
        }
      })
      .finally(afterPost);
    return;
  }
  _postReviewGenerate(accountId, n).finally(afterPost);
}

function _handleReviewConfirmClick() {
  var base = publishLocalBase();
  if (!base) {
    alert('未配置本机 API，无法提交。');
    return;
  }
  var accountId = _detailAccountId || _schModalAccountId;
  if (!accountId) {
    alert('请先进入账号详情。');
    return;
  }
  var acf = document.getElementById('accountDetailReviewConfirmBtn');
  if (acf) acf.disabled = true;
  fetch(publishLocalBase() + '/api/accounts/' + accountId + '/creator-schedule/review-confirm', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
    body: JSON.stringify({})
  })
    .then(function(r) { return _parsePublishJsonResponse(r); })
    .then(function(x) {
      if (!x.ok) {
        var det = x.data && x.data.detail;
        alert(typeof det === 'string' ? det : JSON.stringify(det || x.data));
        return;
      }
      _detailScheduleCache = x.data;
      var ac = _allAccounts.filter(function(a) { return a.id === accountId; })[0];
      if (ac) {
        ac.creator_schedule = Object.assign({}, ac.creator_schedule || {}, x.data);
        if (_detailAccountId === accountId) _refreshDetailScheduleSummary(ac);
      }
      if (_detailAccountId === accountId) _detailApplyScheduleTabFields(x.data);
      alert('已提交确认，后台将按所选草稿执行编排（可在「任务列表」查看进度）。');
      loadAccounts();
    })
    .catch(function() { alert('请求失败'); })
    .finally(function() { if (acf) acf.disabled = false; });
}

function _showReviewGenProgressHtml(html) {
  var box = document.getElementById('accountDetailReviewGenProgress');
  if (!box) return;
  box.innerHTML = html;
  box.style.display = 'block';
}

function _hideReviewGenProgress() {
  var box = document.getElementById('accountDetailReviewGenProgress');
  if (!box) return;
  box.style.display = 'none';
  box.innerHTML = '';
}

/** 生成发布内容：分步展示 saveDone / 当前第几条 / 共几条 */
function _reviewGenProgressMarkup(state) {
  var n = state.n;
  var saveLine = state.saveDone
    ? '<span style="color:#86efac;">✓ 已保存提示词到服务器</span>'
    : '<span style="color:var(--accent);">① 正在保存提示词…</span>';
  var genLine = '';
  if (!state.saveDone) {
    genLine = '<div style="margin-top:0.4rem;color:var(--text-muted);">② 生成发布内容：等待保存完成（共 ' + n + ' 条）</div>';
  } else if (state.phase === 'done') {
    genLine = '<div style="margin-top:0.4rem;color:#86efac;">② 生成发布内容：已全部完成（' + n + ' 条）</div>';
  } else if (state.currentIdx >= 1 && state.currentIdx <= n) {
    genLine = '<div style="margin-top:0.4rem;color:var(--accent);">② 正在生成第 <strong>' + state.currentIdx + '</strong>/' + n + ' 条</div>' +
      '<div class="meta" style="margin-top:0.25rem;font-size:0.74rem;">本步会调用本机 POST /chat 与能力（单条可能数分钟），请勿关闭页面。</div>';
  } else {
    genLine = '<div style="margin-top:0.4rem;color:var(--text-muted);">② 生成发布内容：准备中…（共 ' + n + ' 条）</div>';
  }
  return '<div style="font-weight:600;margin-bottom:0.35rem;">生成发布内容 · 进度</div><div>' + saveLine + '</div>' + genLine;
}

function _reviewGenProgressShortText(state) {
  if (!state.saveDone) return '① 保存提示词…';
  if (state.phase === 'done') return '② 已完成 ' + state.n + ' 条';
  return '② 第 ' + state.currentIdx + '/' + state.n + ' 条生成中…';
}

function _syncReviewGenModalProgress(state) {
  var modalMsg = document.getElementById('schModalMsg');
  var modalMask = document.getElementById('creatorScheduleModal');
  if (!modalMsg || !modalMask || modalMask.style.display !== 'flex') return;
  modalMsg.textContent = _reviewGenProgressShortText(state);
  modalMsg.style.display = 'block';
  modalMsg.className = 'msg';
}

function _postReviewGenerateAssetsOne(accountId, slotIndex) {
  return fetch(publishLocalBase() + '/api/accounts/' + accountId + '/creator-schedule/review-generate-assets', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
    body: JSON.stringify({ slot_indices: [slotIndex] })
  }).then(function(r) { return _parsePublishJsonResponse(r); });
}

function _handleReviewGenerateAssets() {
  var base = publishLocalBase();
  var msgEl = document.getElementById('accountDetailReviewMsg');
  var modalMsg = document.getElementById('schModalMsg');
  if (!base) {
    var t0 = '未配置本机 API。请用本机运行 lobster_online 后端后从该地址打开页面（需 LOCAL_API_BASE / 同源）。';
    if (msgEl) {
      msgEl.textContent = t0;
      msgEl.style.display = 'block';
      msgEl.style.color = '#f87171';
    }
    return;
  }
  var accountId = _detailAccountId || _schModalAccountId;
  if (!accountId) {
    alert('请先进入账号详情。');
    return;
  }
  var drafts = _collectReviewDraftsFromDom();
  if (!drafts || !drafts.length) {
    var tn = '没有可保存的提示词条目。请先「智能生成提示词」或保存定时任务后再试。';
    if (msgEl) {
      msgEl.textContent = tn;
      msgEl.style.display = 'block';
      msgEl.style.color = '#f87171';
    }
    alert(tn);
    return;
  }
  var nSlots = drafts.length;
  var modalMask = document.getElementById('creatorScheduleModal');
  var modalOpen = modalMask && modalMask.style.display === 'flex';
  _setReviewGenBusy(true);
  function afterPost() {
    _setReviewGenBusy(false);
  }
  if (msgEl) {
    msgEl.style.display = 'none';
  }
  var progState = { saveDone: false, currentIdx: 0, n: nSlots, phase: '' };
  _showReviewGenProgressHtml(_reviewGenProgressMarkup(progState));
  _syncReviewGenModalProgress(progState);

  var putPromise;
  if (modalOpen && _schModalAccountId === accountId) {
    var built = _buildSchedulePutBodyFromModal(modalMsg);
    if (!built.ok) {
      _hideReviewGenProgress();
      afterPost();
      return;
    }
    built.body.review_drafts_json = drafts;
    putPromise = fetch(publishLocalBase() + '/api/accounts/' + accountId + '/creator-schedule', {
      method: 'PUT',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
      body: JSON.stringify(built.body)
    })
      .then(function(r) { return _parsePublishJsonResponse(r); })
      .then(function(x) {
        if (!x.ok) {
          var det = x.data && x.data.detail;
          var te = typeof det === 'string' ? det : JSON.stringify(det || x.data);
          if (modalMsg) {
            modalMsg.textContent = te;
            modalMsg.style.display = 'block';
            modalMsg.className = 'msg err';
          }
          throw new Error(te);
        }
        if (_detailAccountId === accountId && x.data) {
          _detailScheduleCache = x.data;
          var ac = _allAccounts.filter(function(a) { return a.id === accountId; })[0];
          if (ac) {
            ac.creator_schedule = Object.assign({}, ac.creator_schedule || {}, x.data);
            _refreshDetailScheduleSummary(ac);
          }
          _detailApplyScheduleTabFields(x.data);
        }
        return x.data;
      });
  } else {
    if (!_detailAccountId || _detailAccountId !== accountId) {
      _hideReviewGenProgress();
      afterPost();
      alert('请在账号详情页的定时任务中操作。');
      return;
    }
    putPromise = _detailPutScheduleMerge({ review_drafts_json: drafts });
  }

  function runSlotsSequential(idx) {
    if (idx >= nSlots) {
      return Promise.resolve(null);
    }
    progState.saveDone = true;
    progState.currentIdx = idx + 1;
    progState.phase = 'slot';
    _showReviewGenProgressHtml(_reviewGenProgressMarkup(progState));
    _syncReviewGenModalProgress(progState);
    return _postReviewGenerateAssetsOne(accountId, idx)
      .then(function(x) {
        if (!x.ok) {
          var det = x.data && x.data.detail;
          var t = typeof det === 'string' ? det : JSON.stringify(det || x.data);
          throw new Error('第 ' + (idx + 1) + ' 条失败：' + t);
        }
        _detailScheduleCache = x.data;
        var ac = _allAccounts.filter(function(a) { return a.id === accountId; })[0];
        if (ac) {
          ac.creator_schedule = Object.assign({}, ac.creator_schedule || {}, x.data);
          if (_detailAccountId === accountId) _refreshDetailScheduleSummary(ac);
        }
        if (_detailAccountId === accountId) _detailApplyScheduleTabFields(x.data);
        return runSlotsSequential(idx + 1);
      });
  }

  putPromise
    .then(function() {
      progState.saveDone = true;
      return runSlotsSequential(0);
    })
    .then(function() {
      progState.phase = 'done';
      progState.currentIdx = nSlots;
      _showReviewGenProgressHtml(_reviewGenProgressMarkup(progState));
      _syncReviewGenModalProgress(progState);
      _hideReviewGenProgress();
      var last = _detailScheduleCache;
      var n = (last && last.review_drafts_json && last.review_drafts_json.length) ? last.review_drafts_json.length : nSlots;
      var okT = '已为 ' + n + ' 条生成发布内容（拟发布说明与素材预览），可在下方查看。';
      if (msgEl) {
        msgEl.textContent = okT;
        msgEl.style.display = 'block';
        msgEl.style.color = '#86efac';
      }
      if (modalMsg && modalOpen) {
        modalMsg.textContent = okT;
        modalMsg.style.display = 'block';
        modalMsg.className = 'msg ok';
      }
      loadAccounts();
      _refreshReviewSnapshotsIfNeeded();
    })
    .catch(function(err) {
      _hideReviewGenProgress();
      var em = (err && err.message) ? err.message : '请求失败（网络或服务异常）';
      if (msgEl) {
        msgEl.textContent = em;
        msgEl.style.display = 'block';
        msgEl.style.color = '#f87171';
      }
      if (modalMsg && modalOpen) {
        modalMsg.textContent = em;
        modalMsg.style.display = 'block';
        modalMsg.className = 'msg err';
      }
    })
    .finally(afterPost);
}

function _parseUtcMs(iso) {
  if (!iso) return NaN;
  try {
    var s = String(iso).trim();
    if (s.indexOf(' ') > 0 && s.indexOf('T') < 0) s = s.replace(' ', 'T');
    if (!/[zZ]$/.test(s) && !/[+-]\d{2}:?\d{2}$/.test(s)) s += 'Z';
    var d = new Date(s);
    return d.getTime();
  } catch (e) {
    return NaN;
  }
}

/** 将 UTC ISO 转为 datetime-local 用的 YYYY-MM-DDTHH:mm（按 Asia/Shanghai 墙钟，与输入语义一致） */
function _utcIsoToDatetimeLocalValueShanghai(iso) {
  if (!iso) return '';
  try {
    var s = String(iso).trim();
    if (s.indexOf(' ') > 0 && s.indexOf('T') < 0) s = s.replace(' ', 'T');
    if (!/[zZ]$/.test(s) && !/[+-]\d{2}:?\d{2}$/.test(s)) s += 'Z';
    var d = new Date(s);
    if (isNaN(d.getTime())) return '';
    var f = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    });
    var parts = {};
    f.formatToParts(d).forEach(function(p) {
      if (p.type !== 'literal') parts[p.type] = p.value;
    });
    return parts.year + '-' + parts.month + '-' + parts.day + 'T' + parts.hour + ':' + parts.minute;
  } catch (e2) {
    return '';
  }
}

/** datetime-local 值视为北京时间，转为 UTC ISO（带 Z）供 PUT */
function _datetimeLocalValueToUtcIsoShanghai(val) {
  if (!val || !String(val).trim()) return null;
  var m = String(val).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!m) return null;
  var y = m[1];
  var mo = m[2];
  var da = m[3];
  var h = m[4];
  var mi = m[5];
  var d = new Date(y + '-' + mo + '-' + da + 'T' + h + ':' + mi + ':00+08:00');
  if (isNaN(d.getTime())) return null;
  return d.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** 从当前时刻起延后 minutes 分钟 → UTC ISO；0 表示马上发，返回 null 清空服务端首条时间 */
function _minutesFromNowToUtcIso(minutes) {
  var m = Math.max(0, Math.min(10080, parseInt(minutes, 10) || 0));
  if (m === 0) return null;
  var ms = Date.now() + m * 60 * 1000;
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** 已保存的首条 UTC ISO → 与当前时刻的分钟差（展示用，最小 0） */
function _delayMinutesFromReviewFirstEta(iso) {
  if (!iso) return 0;
  var ms = _parseUtcMs(iso);
  if (isNaN(ms)) return 0;
  return Math.max(0, Math.round((ms - Date.now()) / 60000));
}

/** 审核稿列表：按间隔推算预计发布时间（北京时间） */
function _draftPromptText(d) {
  if (!d || typeof d !== 'object') return '';
  var p = (d.prompt || '').trim();
  if (p) return p;
  var t = (d.title || '').trim();
  var desc = (d.description || '').trim();
  var parts = [];
  if (t) parts.push('【标题意图】' + t);
  if (desc) parts.push('【正文/描述】' + desc);
  return parts.join('\n');
}

function _collectReviewDraftsFromDom() {
  var host = document.getElementById('accountDetailReviewDraftsList');
  if (!host || !_detailScheduleCache) return null;
  var base = _detailScheduleCache.review_drafts_json;
  if (!Array.isArray(base)) return null;
  var out = [];
  for (var i = 0; i < base.length; i++) {
    var ta = host.querySelector('textarea[data-review-prompt-idx="' + i + '"]');
    var prompt = ta ? String(ta.value || '').trim() : _draftPromptText(base[i]);
    var prev = base[i] && typeof base[i] === 'object' ? base[i] : {};
    out.push({
      prompt: prompt,
      attachment_asset_ids: Array.isArray(prev.attachment_asset_ids) ? prev.attachment_asset_ids : [],
      params: prev.params && typeof prev.params === 'object' ? prev.params : {},
      generated: prev.generated && typeof prev.generated === 'object' ? prev.generated : {}
    });
  }
  return out;
}

function _detailReviewEtaList(sch) {
  if (!sch) return [];
  var drafts = sch.review_drafts_json;
  var n = Array.isArray(drafts) ? drafts.length : 0;
  if (n < 1) return [];
  var iv = Math.max(1, parseInt(sch.interval_minutes, 10) || 60);
  var baseMs;
  if (sch.review_first_eta_at) {
    var ms0 = _parseUtcMs(sch.review_first_eta_at);
    if (!isNaN(ms0)) baseMs = ms0;
  }
  if (baseMs == null && sch.enabled && sch.next_run_at) {
    var ms = _parseUtcMs(sch.next_run_at);
    if (!isNaN(ms)) baseMs = ms;
  }
  if (baseMs == null) baseMs = Date.now() + iv * 60 * 1000;
  var out = [];
  for (var i = 0; i < n; i++) {
    out.push(new Date(baseMs + i * iv * 60 * 1000));
  }
  return out;
}

function _detailRenderReviewDrafts(sch) {
  var host = document.getElementById('accountDetailReviewDraftsList');
  if (!host || !sch) return;
  var drafts = sch.review_drafts_json;
  var etas = _detailReviewEtaList(sch);
  if (!Array.isArray(drafts) || !drafts.length) {
    host.innerHTML = '<p class="meta" style="margin:0;font-size:0.8rem;">暂无条目。请在「完整配置」中写好说明后，设置「出几次」并点「智能生成提示词」，或手动保存后再生成。</p>';
    return;
  }
  host.innerHTML = drafts.map(function(d, idx) {
    var eta = etas[idx] ? _formatDateTimeBeijing(etas[idx].toISOString()) : '—';
    var promptVal = _draftPromptText(d);
    var gen = (d && d.generated) ? d.generated : {};
    var excerpt = (gen.reply_excerpt || '').trim();
    var exShort = excerpt.length > 1200 ? excerpt.slice(0, 1200) + '…' : excerpt;
    var urls = Array.isArray(gen.preview_urls) ? gen.preview_urls : [];
    var aids = Array.isArray(gen.asset_ids) ? gen.asset_ids : [];
    var urlBlocks = urls.slice(0, 6).map(function(u) {
      var isImg = /\.(png|jpg|jpeg|webp|gif)(\?|$)/i.test(u);
      if (isImg) {
        return '<div style="margin-top:0.35rem;"><a href="' + escapeAttr(u) + '" target="_blank" rel="noopener"><img src="' + escapeAttr(u) + '" alt="" style="max-width:100%;max-height:160px;border-radius:6px;" referrerpolicy="no-referrer"></a></div>';
      }
      return '<div class="sch-task-mono" style="margin-top:0.25rem;font-size:0.75rem;"><a href="' + escapeAttr(u) + '" target="_blank" rel="noopener">' + escapeHtml(u.length > 80 ? u.slice(0, 80) + '…' : u) + '</a></div>';
    }).join('');
    var aidLine = aids.length ? ('<div class="meta" style="margin-top:0.25rem;font-size:0.75rem;">asset_id：' + escapeHtml(aids.join('、')) + '</div>') : '';
    var genBlock = (excerpt || urlBlocks || aidLine)
      ? ('<div style="margin-top:0.45rem;padding:0.45rem;border-radius:6px;background:rgba(6,182,212,0.08);border:1px solid rgba(6,182,212,0.2);">' +
        '<div style="font-size:0.78rem;font-weight:600;margin-bottom:0.25rem;">生成结果（拟发布说明与素材线索）</div>' +
        (excerpt ? ('<div style="font-size:0.78rem;white-space:pre-wrap;word-break:break-word;">' + escapeHtml(exShort) + '</div>') : '') +
        aidLine + urlBlocks + '</div>')
      : '';
    return '<div class="card" style="margin-bottom:0.5rem;padding:0.65rem;font-size:0.82rem;">' +
      '<div style="font-weight:600;margin-bottom:0.35rem;">第 ' + (idx + 1) + ' 条 · 预计发布时间（北京）：' + escapeHtml(eta) + '</div>' +
      '<label style="font-size:0.76rem;color:var(--text-muted);">发给 AI 的提示词（可编辑；改后请先保存或点「生成发布内容」前会自动保存）</label>' +
      '<textarea data-review-prompt-idx="' + idx + '" rows="5" style="width:100%;box-sizing:border-box;margin-top:0.3rem;padding:0.45rem;font-size:0.82rem;border-radius:var(--radius-sm);background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);color:var(--text);resize:vertical;">' +
      escapeHtml(promptVal) + '</textarea>' +
      genBlock +
      '<div style="margin-top:0.4rem;"><button type="button" class="btn btn-ghost btn-sm" data-review-regen="' + idx + '">重新生成此条提示词</button></div>' +
      '</div>';
  }).join('');
  host.querySelectorAll('button[data-review-regen]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var si = parseInt(btn.getAttribute('data-review-regen'), 10);
      if (!_detailAccountId || isNaN(si)) return;
      btn.disabled = true;
      fetch(publishLocalBase() + '/api/accounts/' + _detailAccountId + '/creator-schedule/review-regenerate-slot', {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
        body: JSON.stringify({ slot_index: si })
      })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
        .then(function(x) {
          if (!x.ok) {
            alert((x.data && x.data.detail) ? (typeof x.data.detail === 'string' ? x.data.detail : JSON.stringify(x.data.detail)) : '重新生成失败');
            return;
          }
          _detailScheduleCache = x.data;
          var ac = _allAccounts.filter(function(a) { return a.id === _detailAccountId; })[0];
          if (ac) {
            ac.creator_schedule = Object.assign({}, ac.creator_schedule || {}, x.data);
            _refreshDetailScheduleSummary(ac);
          }
          _detailApplyScheduleTabFields(x.data);
          _refreshReviewSnapshotsIfNeeded();
        })
        .catch(function() { alert('请求失败'); })
        .finally(function() { btn.disabled = false; });
    });
  });
}

function _reviewSnapshotKindLabel(k) {
  if (k === 'prompts') return '智能生成提示词';
  if (k === 'assets') return '生成发布内容';
  if (k === 'slot_regen') return '单条重生成提示词';
  return k || '';
}

function _syncReviewSubtabButtons() {
  document.querySelectorAll('#accountDetailReviewBlock .review-subtab').forEach(function(b) {
    var on = b.getAttribute('data-review-subtab') === _detailReviewSubTab;
    b.className = 'btn btn-sm review-subtab ' + (on ? 'btn-primary' : 'btn-ghost');
  });
}

function _resetReviewSubtabDom() {
  _detailReviewSubTab = 'current';
  var pc = document.getElementById('accountDetailReviewPanelCurrent');
  var ph = document.getElementById('accountDetailReviewPanelHistory');
  if (pc) pc.style.display = '';
  if (ph) ph.style.display = 'none';
  var det = document.getElementById('accountDetailReviewSnapshotDetail');
  if (det) {
    det.style.display = 'none';
    det.innerHTML = '';
  }
  _syncReviewSubtabButtons();
}

function _switchReviewSubTab(which) {
  _detailReviewSubTab = (which === 'history') ? 'history' : 'current';
  var pc = document.getElementById('accountDetailReviewPanelCurrent');
  var ph = document.getElementById('accountDetailReviewPanelHistory');
  if (pc) pc.style.display = (_detailReviewSubTab === 'current') ? '' : 'none';
  if (ph) ph.style.display = (_detailReviewSubTab === 'history') ? '' : 'none';
  _syncReviewSubtabButtons();
  if (_detailReviewSubTab === 'history' && _detailAccountId) _loadReviewSnapshots();
}

function _refreshReviewSnapshotsIfNeeded() {
  if (_detailReviewSubTab !== 'history' || !_detailAccountId) return;
  _loadReviewSnapshots();
}

function _loadReviewSnapshots() {
  var host = document.getElementById('accountDetailReviewSnapshotList');
  if (!host || !_detailAccountId) return;
  host.innerHTML = '<p class="meta" style="margin:0;">加载中…</p>';
  fetch(publishLocalBase() + '/api/accounts/' + _detailAccountId + '/creator-schedule/review-snapshots?limit=50', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      _renderReviewSnapshots(d.snapshots || []);
    })
    .catch(function() {
      host.innerHTML = '<p class="msg err" style="margin:0;">加载失败</p>';
    });
}

function _renderReviewSnapshots(snapshots) {
  var host = document.getElementById('accountDetailReviewSnapshotList');
  if (!host) return;
  if (!snapshots.length) {
    host.innerHTML = '<p class="meta" style="margin:0;">暂无历史记录。完成一次生成后会出现在此。</p>';
    return;
  }
  host.innerHTML = snapshots.map(function(s) {
    var kind = _reviewSnapshotKindLabel(s.kind);
    var st = s.status === 'ok' ? '成功' : '失败';
    var stColor = s.status === 'ok' ? '#86efac' : '#f87171';
    var time = escapeHtml(_formatDateTimeBeijing(s.created_at));
    var sum = escapeHtml((s.summary || '').slice(0, 220));
    var err = s.error_detail ? ('<div class="meta" style="margin-top:0.25rem;color:#f87171;font-size:0.78rem;">' + escapeHtml(String(s.error_detail).slice(0, 400)) + '</div>') : '';
    return '<div class="card" style="margin-bottom:0.5rem;padding:0.55rem 0.65rem;font-size:0.82rem;">' +
      '<div style="display:flex;flex-wrap:wrap;gap:0.5rem;align-items:flex-start;justify-content:space-between;">' +
      '<div><span style="font-weight:600;">' + escapeHtml(kind) + '</span> ' +
      '<span style="color:' + stColor + ';">' + escapeHtml(st) + '</span></div>' +
      '<div class="meta" style="font-size:0.76rem;">' + time + '</div></div>' +
      '<div style="margin-top:0.25rem;">' + sum + '</div>' + err +
      '<div style="margin-top:0.4rem;display:flex;gap:0.4rem;flex-wrap:wrap;">' +
      '<button type="button" class="btn btn-primary btn-sm" data-review-restore-snapshot="' + s.id + '">恢复为当前草稿</button>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-review-detail-snapshot="' + s.id + '">查看详情</button>' +
      '</div></div>';
  }).join('');
}

function _restoreReviewSnapshot(sid) {
  if (!_detailAccountId || !sid) return;
  if (!confirm('确定用此快照覆盖当前草稿？当前编辑区内容将被替换。')) return;
  fetch(publishLocalBase() + '/api/accounts/' + _detailAccountId + '/creator-schedule/review-snapshots/' + sid + '/restore', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
    body: JSON.stringify({})
  })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(x) {
      if (!x.ok) {
        alert((x.data && x.data.detail) ? String(x.data.detail) : '恢复失败');
        return;
      }
      _detailScheduleCache = x.data;
      var ac = _allAccounts.filter(function(a) { return a.id === _detailAccountId; })[0];
      if (ac) {
        ac.creator_schedule = Object.assign({}, ac.creator_schedule || {}, x.data);
        _refreshDetailScheduleSummary(ac);
      }
      _detailApplyScheduleTabFields(x.data);
      _switchReviewSubTab('current');
      loadAccounts();
      _refreshReviewSnapshotsIfNeeded();
    })
    .catch(function() { alert('请求失败'); });
}

function _showReviewSnapshotDetail(sid) {
  if (!_detailAccountId || !sid) return;
  var box = document.getElementById('accountDetailReviewSnapshotDetail');
  if (!box) return;
  box.style.display = 'block';
  box.textContent = '加载中…';
  fetch(publishLocalBase() + '/api/accounts/' + _detailAccountId + '/creator-schedule/review-snapshots/' + sid, { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var s = d.snapshot || {};
      var j = s.drafts_json;
      var txt = (typeof j === 'undefined') ? '(无)' : JSON.stringify(j, null, 2);
      if (txt.length > 12000) txt = txt.slice(0, 12000) + '\n…（已截断）';
      box.innerHTML = '<div style="font-size:0.76rem;color:var(--text-muted);margin-bottom:0.35rem;">#' + escapeHtml(String(s.id)) + ' · ' + escapeHtml(_reviewSnapshotKindLabel(s.kind)) + ' · ' + escapeHtml(s.status === 'ok' ? '成功' : '失败') + '</div><pre style="margin:0;font-size:0.72rem;white-space:pre-wrap;word-break:break-all;">' + escapeHtml(txt) + '</pre>';
    })
    .catch(function() {
      box.textContent = '加载失败';
    });
}

function _detailApplyScheduleTabFields(d, opts) {
  opts = opts || {};
  if (!d) return;
  var modeEl = document.getElementById('accountDetailScheduleMode');
  if (modeEl) modeEl.value = d.schedule_publish_mode === 'review' ? 'review' : 'immediate';
  var rvc = document.getElementById('accountDetailReviewVariantCount');
  if (rvc) rvc.value = d.review_variant_count != null ? String(d.review_variant_count) : '3';
  var fd = document.getElementById('accountDetailReviewFirstDelayMinutes');
  if (fd) fd.value = String(_delayMinutesFromReviewFirstEta(d.review_first_eta_at));
  var blk = document.getElementById('accountDetailReviewBlock');
  if (blk) blk.style.display = (d.schedule_publish_mode === 'review') ? '' : 'none';
  if (opts.skipDrafts) {
    var host = document.getElementById('accountDetailReviewDraftsList');
    if (host) {
      host.innerHTML = '<p class="meta" style="margin:0;font-size:0.82rem;color:var(--text-muted);">正在重新生成提示词，请稍候…</p>';
    }
  } else {
    _detailRenderReviewDrafts(d);
  }
}

function _detailPutScheduleMerge(extra) {
  var c = _detailScheduleCache;
  if (!c || !_detailAccountId) return Promise.resolve();
  extra = extra || {};
  var body = {
    enabled: !!c.enabled,
    interval_minutes: parseInt(c.interval_minutes, 10) || 60,
    schedule_kind: c.schedule_kind === 'video' ? 'video' : 'image',
    video_source_asset_id: c.video_source_asset_id || null,
    requirements_text: c.requirements_text || null,
    schedule_publish_mode: extra.schedule_publish_mode != null ? extra.schedule_publish_mode : (c.schedule_publish_mode || 'immediate'),
    review_variant_count: extra.review_variant_count != null ? extra.review_variant_count : (c.review_variant_count != null ? c.review_variant_count : 3),
    review_drafts_json: extra.review_drafts_json !== undefined ? extra.review_drafts_json : c.review_drafts_json,
    review_confirmed: extra.review_confirmed !== undefined ? extra.review_confirmed : !!c.review_confirmed
  };
  if (extra.review_first_eta_at !== undefined) {
    body.review_first_eta_at = extra.review_first_eta_at;
  } else if (Object.prototype.hasOwnProperty.call(c, 'review_first_eta_at')) {
    body.review_first_eta_at = c.review_first_eta_at;
  }
  return fetch(publishLocalBase() + '/api/accounts/' + _detailAccountId + '/creator-schedule', {
    method: 'PUT',
    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
    body: JSON.stringify(body)
  })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(x) {
      if (!x.ok) throw new Error((x.data && x.data.detail) ? String(x.data.detail) : '保存失败');
      _detailScheduleCache = x.data;
      var ac = _allAccounts.filter(function(a) { return a.id === _detailAccountId; })[0];
      if (ac) {
        ac.creator_schedule = Object.assign({}, ac.creator_schedule || {}, x.data);
        _refreshDetailScheduleSummary(ac);
      }
      _detailApplyScheduleTabFields(x.data);
      return x.data;
    });
}

/** 服务端时间为 UTC（带 Z 或与旧数据无后缀均按 UTC 解析），展示为北京时间 */
function _formatDateTimeBeijing(iso) {
  if (!iso) return '';
  try {
    var s = String(iso).trim();
    if (s.indexOf(' ') > 0 && s.indexOf('T') < 0) s = s.replace(' ', 'T');
    if (!/[zZ]$/.test(s) && !/[+-]\d{2}:?\d{2}$/.test(s)) s += 'Z';
    var d = new Date(s);
    if (isNaN(d.getTime())) return String(iso).substring(0, 19).replace('T', ' ');
    return d.toLocaleString('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    });
  } catch (e) {
    return String(iso);
  }
}

function _applyIntervalMinutesToModal(m) {
  m = parseInt(m, 10) || 60;
  if (m > 10080) m = 10080;
  if (m < 1) m = 1;
  var valEl = document.getElementById('schIntervalValue');
  var unitEl = document.getElementById('schIntervalUnit');
  if (!valEl || !unitEl) return;
  if (m % 1440 === 0 && m >= 1440) {
    unitEl.value = 'day';
    valEl.value = String(m / 1440);
  } else if (m % 60 === 0 && m >= 60) {
    unitEl.value = 'hour';
    valEl.value = String(m / 60);
  } else {
    unitEl.value = 'min';
    valEl.value = String(m);
  }
}

function _intervalMinutesFromModal() {
  var valEl = document.getElementById('schIntervalValue');
  var unitEl = document.getElementById('schIntervalUnit');
  if (!valEl || !unitEl) return null;
  var v = parseInt(valEl.value, 10);
  if (!v || v < 1) return null;
  var u = unitEl.value;
  var m = u === 'hour' ? v * 60 : u === 'day' ? v * 1440 : v;
  if (m < 1 || m > 10080) return null;
  return m;
}

function _renderAccountList(accounts, emptyMessage) {
  var el = document.getElementById('accountList');
  if (!el) return;
  if (!accounts.length) {
    el.innerHTML = '<div class="page-empty-card">' + escapeHtml(emptyMessage || '暂无账号。') + '</div>';
    return;
  }
  el.innerHTML = accounts.map(function(a) {
    var statusColor = STATUS_COLORS[a.status] || '#888';
    var statusLabel = STATUS_LABELS[a.status] || a.status;
    var isEcom = !!ECOMMERCE_PLATFORMS[a.platform];
    var isOriginSlot = !!a.is_origin_slot || a.managed_by === 'douyin_origin';
    var detailBtn = isEcom ? '' : '<button type="button" class="btn btn-primary btn-sm" data-open-account-detail="' + a.id + '" title="进入账号详情（数据与定时任务）">进入详情</button>';
    var openBtn = '<button type="button" class="btn btn-primary btn-sm" data-open-browser="' + a.id + '">打开浏览器</button>';
    var runsBtn = isEcom ? '' : '<button type="button" class="btn btn-ghost btn-sm" data-schedule-runs-acct="' + a.id + '" title="间隔定时任务的执行记录">执行记录</button>';
    var publishBtn = '<button type="button" class="btn btn-primary btn-sm" data-publish-acct="' + a.id + '" data-publish-nick="' + escapeAttr(a.nickname) + '">发布素材</button>';
    var deleteBtn = isOriginSlot ? '' : '<button type="button" class="btn btn-ghost btn-sm" data-delete-id="' + a.id + '">删除</button>';
    var lastLogin = a.last_login ? '上次登录: ' + _formatDateTimeBeijing(a.last_login) : '';
    var lc = a.last_creator_sync;
    var syncLine = '';
    if (isOriginSlot) {
      syncLine = '获客中心固定槽位' + (a.origin_account_id ? ' · 账号 ' + a.origin_account_id : '') + (a.origin_port ? ' · 端口 ' + a.origin_port : '');
    }
    if (!isEcom && lc && lc.fetched_at) {
      syncLine = '作品数据: ' + _formatDateTimeBeijing(lc.fetched_at) +
        (lc.sync_error ? ' (上次同步失败)' : ' · ' + (lc.item_count != null ? lc.item_count : 0) + ' 条');
    }
    var sch = a.creator_schedule;
    var schHint = '';
    if (!isEcom && sch && sch.enabled) {
      var im = sch.interval_minutes != null ? sch.interval_minutes : 60;
      var nextL = sch.next_run_at ? (' · 下次≈' + escapeHtml(_formatDateTimeBeijing(sch.next_run_at))) : '';
      var kindL = _scheduleKindLabel(sch.schedule_kind);
      var vHint = sch.schedule_kind === 'video' ? (' · ' + escapeHtml(_scheduleVideoBranchHint(sch))) : '';
      var modeShort = sch.schedule_publish_mode === 'review' ? '审核' : '立即';
      schHint = '<div class="account-card-highlight">定时已开 · ' + escapeHtml(modeShort) +
        ' · ' + escapeHtml(kindL) + ' · ' + escapeHtml(_formatScheduleIntervalMinutes(im)) + vHint + nextL + '</div>';
    }
    return '<div class="skill-store-card account-card" data-account-card="' + a.id + '" data-platform="' + escapeAttr(a.platform) + '" data-origin-slot="' + (isOriginSlot ? '1' : '0') + '" style="cursor:pointer;" title="' + (isOriginSlot ? '抖音获客中心固定槽位' : '点击查看详情') + '">' +
      '<div class="account-card-top">' +
      '<div class="card-label">' + escapeHtml(PLATFORM_NAMES[a.platform] || a.platform) + '</div>' +
      '<span class="account-card-status" style="color:' + statusColor + ';">' + escapeHtml(statusLabel) + '</span></div>' +
      '<div class="card-value">' + escapeHtml(a.nickname) + '</div>' +
      '<div class="account-card-meta">' +
      '<div class="card-desc" style="font-size:0.78rem;color:var(--text-muted);">' + escapeHtml(lastLogin) + '</div>' +
      (syncLine ? '<div class="card-desc" style="font-size:0.72rem;color:var(--text-muted);">' + escapeHtml(syncLine) + '</div>' : '') +
      '</div>' +
      schHint +
      '<div class="card-actions" onclick="event.stopPropagation();">' + detailBtn + ' ' + openBtn + ' ' + runsBtn + ' ' + publishBtn + ' ' + deleteBtn + '</div></div>';
  }).join('');
  _bindAccountButtons(el);
  _bindAccountCardClicks(el);
}

function _applyAccountPlatformFilter() {
  var platform = (document.getElementById('accountPlatformFilter') || {}).value || '';
  var text = _accountTypeUiText(_currentAccountType);
  var list = _allAccounts.filter(function(a) {
    return _accountTypeForPlatform(a.platform) === _currentAccountType;
  });
  if (platform) {
    list = list.filter(function(a) { return a.platform === platform; });
  }
  _renderAccountList(list, platform ? text.emptyPlatform : text.emptyAll);
}

function loadAccounts() {
  var el = document.getElementById('accountList');
  if (!el) return;
  if (!publishLocalBase()) {
    el.innerHTML = '<div class="page-empty-card msg err">未配置本机 API（LOCAL_API_BASE）。请用本机运行 backend/run.py 后从 <code>http://127.0.0.1:端口</code> 打开页面。</div>';
    return;
  }
  el.innerHTML = '<div class="page-empty-card">加载中…</div>';
  fetch(publishLocalBase() + '/api/accounts', { headers: authHeaders() })
    .then(_publishParseResponse)
    .then(function(x) {
      if (!x.ok) {
        var msg = (x.d && (x.d.detail || x.d.message)) ? String(x.d.detail || x.d.message) : ('HTTP ' + x.status);
        el.innerHTML = '<div class="page-empty-card msg err">加载失败：' + escapeHtml(msg) + '</div>';
        return;
      }
      var d = x.d;
      var accounts = (d && Array.isArray(d.accounts)) ? d.accounts : [];
      _allAccounts = accounts;
      _applyAccountPlatformFilter();
    })
    .catch(function(err) {
      var m = (err && err.message) ? err.message : String(err);
      el.innerHTML = '<div class="page-empty-card msg err">加载失败：' + escapeHtml(m) + '</div>';
    });
}

function _bindAccountButtons(el) {
  el.querySelectorAll('button[data-open-account-detail]').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var id = parseInt(btn.getAttribute('data-open-account-detail'), 10);
      if (id) openAccountDetailPanel(id);
    });
  });
  el.querySelectorAll('button[data-open-browser]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var id = btn.getAttribute('data-open-browser');
      btn.disabled = true; btn.textContent = '启动中…';
      fetch(publishLocalBase() + '/api/accounts/' + id + '/open-browser', {
        method: 'POST', headers: authHeaders()
      })
        .then(function(r) { return r.json(); })
        .then(function(d) {
          var status = d.logged_in ? '已登录' : '未登录，请在浏览器中扫码';
          btn.textContent = status;
          setTimeout(function() { loadAccounts(); }, 2000);
        })
        .catch(function() { alert('请求失败'); })
        .finally(function() { btn.disabled = false; });
    });
  });
  el.querySelectorAll('button[data-publish-acct]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var card = btn.closest('.skill-store-card');
      var platform = (card && card.getAttribute('data-platform')) || '';
      var id = btn.getAttribute('data-publish-acct');
      var nick = btn.getAttribute('data-publish-nick') || '';
      var assetId = prompt('请输入要发布的素材 ID（可在「素材库」tab 查看）：');
      if (!assetId || !assetId.trim()) return;
      var title = prompt('发布标题（可留空）：', '') || '';
      var options = {};
      var coverAssetId = null;
      if (platform === 'xiaohongshu') {
        var typeChoice = prompt('发布类型（仅图片素材时有效）：1=图文 2=长文，直接回车=图文', '1') || '1';
        if ((typeChoice || '1').trim() === '2') options.xiaohongshu_publish_type = 'article';
      }
      if (platform === 'douyin') {
        var cm = prompt(
          '抖音视频封面策略（必填）：\n' +
          '1 = smart  智能识别后按需自动点横/竖封面（默认）\n' +
          '2 = upload 必须再指定一张「封面图」素材 ID\n' +
          '3 = manual 仅在浏览器里手动选封面，脚本不自动点',
          '1'
        ) || '1';
        var m = (cm || '1').trim();
        if (m === '2') options.douyin_cover_mode = 'upload';
        else if (m === '3') options.douyin_cover_mode = 'manual';
        else options.douyin_cover_mode = 'smart';
        if (options.douyin_cover_mode === 'upload') {
          coverAssetId = prompt('封面图素材 ID（必填）：');
          if (!coverAssetId || !coverAssetId.trim()) {
            alert('upload 模式必须填写封面图素材 ID');
            return;
          }
        }
      }
      if (platform === 'wechat_channels') {
        var wcLocation = prompt('视频号位置（可留空；例如：深圳市）：', '') || '';
        var wcCollection = prompt('视频号合集（可留空；填写已有合集名称）：', '') || '';
        var wcLink = prompt('视频号链接（可留空；填写已有链接名称）：', '') || '';
        var wcMusic = prompt('视频号音乐（可留空；填写页面可选音乐名称）：', '') || '';
        var wcActivity = prompt('视频号活动（可留空；例如：不参与活动 或活动名称）：', '') || '';
        if (wcLocation.trim()) options.wechat_channels_location = wcLocation.trim();
        if (wcCollection.trim()) options.wechat_channels_collection = wcCollection.trim();
        if (wcLink.trim()) options.wechat_channels_link = wcLink.trim();
        if (wcMusic.trim()) options.wechat_channels_music = wcMusic.trim();
        if (wcActivity.trim()) options.wechat_channels_activity = wcActivity.trim();
      }
      btn.disabled = true; btn.textContent = '发布中…';
      var payload = {
        asset_id: assetId.trim(),
        account_id: parseInt(id, 10) || undefined,
        account_nickname: nick,
        title: title,
        options: Object.keys(options).length ? options : undefined
      };
      if (coverAssetId && coverAssetId.trim()) payload.cover_asset_id = coverAssetId.trim();
      fetch(publishLocalBase() + '/api/publish', {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify(payload)
      })
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
        .then(function(x) {
          if (x.data && x.data.need_login) {
            alert('未登录，已打开浏览器，请扫码登录后重试');
          } else if (x.data && x.data.status === 'success') {
            alert('发布成功！' + (x.data.result_url ? '\n' + x.data.result_url : ''));
          } else {
            alert(x.data.error || x.data.detail || '发布失败');
          }
          loadAccounts();
        })
        .catch(function() { alert('请求失败'); })
        .finally(function() { btn.disabled = false; btn.textContent = '发布素材'; });
    });
  });
  el.querySelectorAll('button[data-delete-id]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var id = btn.getAttribute('data-delete-id');
      if (!confirm('确定删除此账号？')) return;
      fetch(publishLocalBase() + '/api/accounts/' + id, {
        method: 'DELETE', headers: authHeaders()
      })
        .then(function() { loadAccounts(); })
        .catch(function() { alert('删除失败'); });
    });
  });
  el.querySelectorAll('button[data-schedule-runs-acct]').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var id = parseInt(btn.getAttribute('data-schedule-runs-acct'), 10);
      if (id) openCreatorScheduleTasksModal(id);
    });
  });
}

function _bindAccountCardClicks(listEl) {
  if (!listEl || listEl._accountCardBound) return;
  listEl._accountCardBound = true;
  listEl.addEventListener('click', function(e) {
    if (e.target.closest('button')) return;
    var card = e.target.closest('[data-account-card]');
    if (!card) return;
    var id = parseInt(card.getAttribute('data-account-card'), 10);
    if (id) openAccountDetailPanel(id);
  });
}

function hideAccountDetailPanel() {
  _detailAccountId = null;
  _detailScheduleCache = null;
  _resetReviewSubtabDom();
  var lp = document.getElementById('accountListPanel');
  var dp = document.getElementById('accountDetailPanel');
  if (lp) lp.style.display = '';
  if (dp) dp.style.display = 'none';
}

function openAccountDetailPanel(accountId) {
  var acct = _allAccounts.filter(function(a) { return a.id === accountId; })[0];
  if (!acct) return;
  _detailAccountId = accountId;
  var lp = document.getElementById('accountListPanel');
  var dp = document.getElementById('accountDetailPanel');
  if (lp) lp.style.display = 'none';
  if (dp) dp.style.display = '';
  var isEcom = !!ECOMMERCE_PLATFORMS[acct.platform];
  var tabData = document.getElementById('accountDetailTabData');
  var tabSch = document.getElementById('accountDetailTabSchedule');
  var schTabBtn = document.querySelector('#accountDetailTabs [data-ad-tab="schedule"]');
  if (schTabBtn) schTabBtn.style.display = isEcom ? 'none' : '';
  document.querySelectorAll('#accountDetailTabs .sys-tab').forEach(function(t) { t.classList.remove('active'); });
  var firstTab = document.querySelector('#accountDetailTabs [data-ad-tab="data"]');
  if (firstTab) firstTab.classList.add('active');
  if (tabData) tabData.style.display = '';
  if (tabSch) tabSch.style.display = 'none';
  var titleEl = document.getElementById('accountDetailTitle');
  if (titleEl) {
    titleEl.textContent = (PLATFORM_NAMES[acct.platform] || acct.platform) + ' · ' + acct.nickname + ' — 详情';
  }
  _detailScheduleCache = acct.creator_schedule ? Object.assign({}, acct.creator_schedule) : null;
  _resetReviewSubtabDom();
  if (_detailScheduleCache) {
    _detailApplyScheduleTabFields(_detailScheduleCache);
  }
  _refreshDetailScheduleSummary(acct);
  _detailLoadCreatorSettings();
  _detailCreatorSetStatus('', false);
  _renderToutiaoInsightsPanel(acct.platform, null);
  _creatorRenderItems([], 'detailCreatorItemGrid');
  _detailLoadCreatorCache();
  fetch(publishLocalBase() + '/api/accounts/' + accountId + '/creator-schedule', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      _detailScheduleCache = d;
      var ac = _allAccounts.filter(function(a) { return a.id === accountId; })[0];
      if (ac) {
        ac.creator_schedule = Object.assign({}, ac.creator_schedule || {}, d);
        _refreshDetailScheduleSummary(ac);
      }
      _detailApplyScheduleTabFields(d);
    })
    .catch(function() { _detailScheduleCache = null; });
}

function _refreshDetailScheduleSummary(acct) {
  var sum = document.getElementById('accountDetailScheduleSummary');
  if (!sum) return;
  var sch = acct.creator_schedule;
  if (!sch) {
    sum.innerHTML = '尚未配置定时任务。点击「配置定时任务」设置间隔（每隔多久一次），并填写目标与要求（可后续提供给 AI）。';
    return;
  }
  var on = sch.enabled ? '已启用' : '未启用';
  var im = sch.interval_minutes != null ? sch.interval_minutes : 60;
  var nextLine = sch.next_run_at
    ? (' · 下次执行（北京时间）：' + escapeHtml(_formatDateTimeBeijing(sch.next_run_at)))
    : '';
  var modeLabel = sch.schedule_publish_mode === 'review' ? '审核后发布' : '立即发布';
  var kindLine = '类型：<strong>' + escapeHtml(_scheduleKindLabel(sch.schedule_kind)) + '</strong>';
  if (sch.schedule_kind === 'video') {
    kindLine += '（' + escapeHtml(_scheduleVideoBranchHint(sch)) + '）';
    var aid = (sch.video_source_asset_id || '').trim();
    if (aid) kindLine += ' · 素材 ID：<code style="font-size:0.85em;">' + escapeHtml(aid) + '</code>';
  }
  sum.innerHTML = '状态：<strong>' + on + '</strong> · 模式：<strong>' + escapeHtml(modeLabel) + '</strong> · 间隔：<strong>' + escapeHtml(_formatScheduleIntervalMinutes(im)) + '</strong> · ' + kindLine + nextLine +
    ' <button type="button" class="help-q" data-help-key="account_schedule_summary" aria-label="说明">?</button>';
}

function _detailLoadCreatorSettings() {
  fetch(publishLocalBase() + '/api/creator-content/settings', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(s) {
      if (s && typeof s.creator_content_ttl_seconds === 'number') _creatorDefaultTtlSec = s.creator_content_ttl_seconds;
      if (s && typeof s.creator_sync_headless_default === 'boolean') {
        var chk = document.getElementById('detailCreatorHeadlessChk');
        if (chk && !chk.dataset.inited) {
          chk.checked = s.creator_sync_headless_default;
          chk.dataset.inited = '1';
        }
      }
    })
    .catch(function() {});
}

function _detailCreatorSetStatus(t, isErr) {
  var el = document.getElementById('detailCreatorStatusMsg');
  if (!el) return;
  el.textContent = t || '';
  el.style.color = isErr ? '#f87171' : 'var(--text-muted)';
}

function _detailLoadCreatorCache() {
  if (!_detailAccountId) return;
  var id = _detailAccountId;
  var ac0 = _allAccounts.filter(function(a) { return a.id === id; })[0];
  var q = _creatorDefaultTtlSec > 0 ? ('?ttl_seconds=' + encodeURIComponent(String(_creatorDefaultTtlSec))) : '';
  _detailCreatorSetStatus('正在加载缓存…', false);
  fetch(publishLocalBase() + '/api/accounts/' + id + '/creator-content' + q, { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var platform = d.platform || (ac0 && ac0.platform) || '';
      if ((ac0 && (ac0.is_origin_slot || ac0.managed_by === 'douyin_origin')) || id < 0) platform = 'douyin';
      if (platform !== 'douyin' && platform !== 'xiaohongshu' && platform !== 'toutiao') {
        _detailCreatorSetStatus('该账号不是抖音/小红书/今日头条，无此类作品列表同步。', false);
        _creatorRenderItems([], 'detailCreatorItemGrid');
        return;
      }
      var insCount = 0;
      if (d.meta && d.meta.toutiao_insights && typeof d.meta.toutiao_insights === 'object') {
        insCount = Object.keys(d.meta.toutiao_insights).length;
      }
      var toutiaoExtra = (platform === 'toutiao' && insCount) ? (' · 已汇总 ' + insCount + ' 项数据/收益字段') : '';
      if (d.sync_error) _detailCreatorSetStatus('上次同步错误: ' + d.sync_error, true);
      else if (!d.has_snapshot) _detailCreatorSetStatus('尚无快照，请点击「从平台同步」。', false);
      else _detailCreatorSetStatus('共 ' + ((d.items && d.items.length) || 0) + ' 条作品' + toutiaoExtra + ' · 更新于（北京时间）' + _formatDateTimeBeijing(d.fetched_at), false);
      _renderToutiaoInsightsPanel(platform, d.meta || null);
      _creatorRenderItems(d.items || [], 'detailCreatorItemGrid');
    })
    .catch(function() { _detailCreatorSetStatus('加载失败', true); });
}

function openCreatorScheduleModal(accountId) {
  _schModalAccountId = accountId;
  var mask = document.getElementById('creatorScheduleModal');
  var msg = document.getElementById('schModalMsg');
  if (msg) { msg.style.display = 'none'; msg.textContent = ''; }
  if (!mask) return;
  fetch(publishLocalBase() + '/api/accounts/' + accountId + '/creator-schedule', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      document.getElementById('schEnabled').checked = !!d.enabled;
      _applyIntervalMinutesToModal(d.interval_minutes != null ? d.interval_minutes : 60);
      var kindSel = document.getElementById('schScheduleKind');
      if (kindSel) kindSel.value = d.schedule_kind === 'video' ? 'video' : 'image';
      var assetInp = document.getElementById('schVideoAssetId');
      if (assetInp) assetInp.value = d.video_source_asset_id || '';
      document.getElementById('schRequirements').value = d.requirements_text || '';
      var pmEl = document.getElementById('schPublishMode');
      if (pmEl) pmEl.value = d.schedule_publish_mode === 'review' ? 'review' : 'immediate';
      var rvcEl = document.getElementById('schReviewVariantCount');
      if (rvcEl) rvcEl.value = d.review_variant_count != null ? String(d.review_variant_count) : '3';
      _schUpdateScheduleKindUI();
      _schUpdatePublishModeUI();
      mask.style.display = 'flex';
    })
    .catch(function() { alert('加载定时配置失败'); });
}

function closeCreatorScheduleModal() {
  var mask = document.getElementById('creatorScheduleModal');
  if (mask) mask.style.display = 'none';
  _schModalAccountId = null;
}

function _schTriggerLabel(t) {
  if (t === 'bootstrap') return '保存首轮';
  if (t === 'tick') return '定时到点';
  return t || '—';
}

function _schStatusLabel(s) {
  var m = { running: '进行中', success: '成功', failed: '失败', partial: '部分成功', cancelled: '已取消' };
  return m[s] || s || '—';
}

function _schStatusClass(s) {
  if (s === 'running') return 'sch-task-st-running';
  if (s === 'success') return 'sch-task-st-success';
  if (s === 'failed') return 'sch-task-st-failed';
  if (s === 'partial') return 'sch-task-st-partial';
  if (s === 'cancelled') return 'sch-task-st-failed';
  return '';
}

function _schTri(v) {
  if (v === true) return '<span style="color:#4ade80">是</span>';
  if (v === false) return '<span style="color:#f87171">否</span>';
  return '<span class="meta">—</span>';
}

/** 作品同步：接口报错但已拉到条数（如小红书 406 + 导航兜底）时标为「部分」避免误解为完全失败 */
function _schSyncCell(r) {
  var se = (r.sync_error || '').trim();
  var n = r.item_count;
  var hasItems = n != null && n !== '' && Number(n) > 0;
  if (r.sync_ok === true) {
    var okCell = _schTri(true);
    if (se) {
      okCell += '<div class="sch-task-mono meta" style="margin-top:0.12rem;">提示：' + escapeHtml(se.length > 56 ? se.slice(0, 56) + '…' : se) + '</div>';
    }
    return okCell;
  }
  if (r.sync_ok === false && hasItems) {
    var cell = '<span style="color:#fbbf24">部分</span>';
    cell += '<div class="meta" style="margin-top:0.12rem;">已拉取 ' + escapeHtml(String(n)) + ' 条（接口或分页未完全成功）</div>';
    if (se) {
      cell += '<div class="sch-task-mono" style="margin-top:0.15rem;">' + escapeHtml(se.length > 48 ? se.slice(0, 48) + '…' : se) + '</div>';
    }
    return cell;
  }
  var syncCell = _schTri(r.sync_ok);
  if (se) {
    var sshort = se.length > 48 ? se.slice(0, 48) + '…' : se;
    syncCell += '<div class="sch-task-mono" style="margin-top:0.15rem;">' + escapeHtml(sshort) + '</div>';
  }
  if (hasItems) {
    syncCell += '<div class="meta" style="margin-top:0.12rem;">' + escapeHtml(String(n)) + ' 条</div>';
  }
  return syncCell;
}

function _stopSchTasksPoll() {
  if (_schTasksPollTimer) {
    clearInterval(_schTasksPollTimer);
    _schTasksPollTimer = null;
  }
}

function _renderSchTasks(runs) {
  var el = document.getElementById('schTasksBody');
  if (!el) return;
  if (!runs || !runs.length) {
    el.innerHTML = '<p class="meta" style="margin:0;">暂无执行记录。保存定时配置触发首轮或等到点后会在此显示。</p>';
    return;
  }
  var html = '<table class="sch-tasks-table"><thead><tr>';
  html += '<th>开始时间（北京）</th><th>触发</th><th>状态</th><th>进度</th><th>作品同步</th><th>智能编排</th><th>结束时间</th>';
  html += '</tr></thead><tbody>';
  runs.forEach(function(r) {
    var phase = escapeHtml(r.phase || '');
    var det = (r.detail || '').trim();
    if (det) {
      var dshort = det.length > 140 ? det.slice(0, 140) + '…' : det;
      phase += '<div class="sch-task-mono" style="margin-top:0.2rem;">' + escapeHtml(dshort) + '</div>';
    }
    var syncCell = _schSyncCell(r);
    var oe = (r.orchestration_error || '').trim();
    var orchCell = _schTri(r.orchestration_ok);
    if (oe) {
      var oshort = oe.length > 48 ? oe.slice(0, 48) + '…' : oe;
      orchCell += '<div class="sch-task-mono" style="margin-top:0.15rem;">' + escapeHtml(oshort) + '</div>';
    }
    html += '<tr>';
    html += '<td class="sch-task-mono">' + escapeHtml(_formatDateTimeBeijing(r.started_at)) + '</td>';
    html += '<td>' + escapeHtml(_schTriggerLabel(r.trigger)) + '</td>';
    html += '<td class="' + _schStatusClass(r.status) + '">' + escapeHtml(_schStatusLabel(r.status)) + '</td>';
    html += '<td>' + phase + '</td>';
    html += '<td>' + syncCell + '</td>';
    html += '<td>' + orchCell + '</td>';
    html += '<td class="sch-task-mono">' + (r.finished_at ? escapeHtml(_formatDateTimeBeijing(r.finished_at)) : '—') + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

function loadCreatorScheduleTasks() {
  var id = _schTasksAccountId;
  var el = document.getElementById('schTasksBody');
  if (!id || !el) return;
  fetch(publishLocalBase() + '/api/accounts/' + id + '/creator-schedule/runs?limit=80', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var runs = (d && d.runs) ? d.runs : [];
      _renderSchTasks(runs);
      var anyRunning = runs.some(function(x) { return x.status === 'running'; });
      _stopSchTasksPoll();
      var mask = document.getElementById('creatorScheduleTasksModal');
      if (mask && mask.style.display === 'flex' && anyRunning) {
        _schTasksPollTimer = setInterval(loadCreatorScheduleTasks, 4000);
      }
    })
    .catch(function() {
      el.innerHTML = '<p class="msg err" style="margin:0;">加载失败</p>';
    });
}

function openCreatorScheduleTasksModal(accountId) {
  _schTasksAccountId = accountId;
  var mask = document.getElementById('creatorScheduleTasksModal');
  if (!mask) return;
  mask.style.display = 'flex';
  var el = document.getElementById('schTasksBody');
  if (el) el.innerHTML = '<p class="meta" style="margin:0;">加载中…</p>';
  loadCreatorScheduleTasks();
}

function closeCreatorScheduleTasksModal() {
  _stopSchTasksPoll();
  _schTasksAccountId = null;
  var mask = document.getElementById('creatorScheduleTasksModal');
  if (mask) mask.style.display = 'none';
}

function _setAccountType(type) {
  var next = type === 'ecommerce' ? 'ecommerce' : 'publish';
  if (next === _currentAccountType) return;
  _currentAccountType = next;
  hideAccountDetailPanel();
  _syncAccountTypeUi();
  if (_allAccounts.length === 0) {
    loadAccounts();
  } else {
    _applyAccountPlatformFilter();
  }
}

function bindPublishAccountUi() {
  document.querySelectorAll('.account-type-tab').forEach(function(tab) {
    if (tab._accountTypeTabBound) return;
    tab._accountTypeTabBound = true;
    tab.addEventListener('click', function() {
      _setAccountType(tab.getAttribute('data-account-type'));
    });
  });

  _syncAccountTypeUi();

  // 选平台筛选：切换时只显示当前类型下的平台账号，并确保下方列表立即刷新
  var accountPlatformFilter = document.getElementById('accountPlatformFilter');
  if (accountPlatformFilter && !accountPlatformFilter._accountPlatformFilterBound) {
    accountPlatformFilter._accountPlatformFilterBound = true;
    accountPlatformFilter.addEventListener('change', function() {
      if (_allAccounts.length === 0) {
        loadAccounts();
      } else {
        _applyAccountPlatformFilter();
      }
    });
  }
  bindPublishAccountModalUi();
}

// Add publish account（弹窗）
function openAddPublishAccountModal() {
  var mask = document.getElementById('addPublishAccountModal');
  if (!mask) return;
  var msg = document.getElementById('addPublishAccountModalMsg');
  if (msg) { msg.style.display = 'none'; msg.textContent = ''; }
  _syncAddAccountModalPlatformOptions();
  mask.style.display = 'flex';
}

function closeAddPublishAccountModal() {
  var mask = document.getElementById('addPublishAccountModal');
  if (mask) mask.style.display = 'none';
}

/** 关闭发布管理下所有全屏遮罩；未关时 fixed 层会盖住主区导致「整页点不动」 */
function closeAllPublishModals() {
  closeAddPublishAccountModal();
  _closeAssetPublishModal();
  closeCreatorScheduleModal();
  closeCreatorScheduleTasksModal();
}
window.closeAllPublishModals = closeAllPublishModals;

function bindPublishAccountModalUi() {
var openAddPubAcctBtn = document.getElementById('openAddPublishAccountModalBtn');
if (openAddPubAcctBtn && !openAddPubAcctBtn._publishAddAccountBound) {
  openAddPubAcctBtn._publishAddAccountBound = true;
  openAddPubAcctBtn.addEventListener('click', openAddPublishAccountModal);
}

var addPubAcctCancel = document.getElementById('addPublishAccountModalCancel');
if (addPubAcctCancel && !addPubAcctCancel._publishAddAccountBound) {
  addPubAcctCancel._publishAddAccountBound = true;
  addPubAcctCancel.addEventListener('click', closeAddPublishAccountModal);
}

var addPubAcctMask = document.getElementById('addPublishAccountModal');
if (addPubAcctMask && !addPubAcctMask._publishAddAccountBound) {
  addPubAcctMask._publishAddAccountBound = true;
  addPubAcctMask.addEventListener('click', function(e) {
    if (e.target === addPubAcctMask) closeAddPublishAccountModal();
  });
}

var addPubAcctSubmit = document.getElementById('addPublishAccountModalSubmit');
if (addPubAcctSubmit && !addPubAcctSubmit._publishAddAccountBound) {
  addPubAcctSubmit._publishAddAccountBound = true;
  addPubAcctSubmit.addEventListener('click', function() {
    var platform = document.getElementById('modalAddAcctPlatform').value;
    var nickname = (document.getElementById('modalAddAcctNickname').value || '').trim();
    var msgEl = document.getElementById('addPublishAccountModalMsg');
    if (_accountTypeForPlatform(platform) !== _currentAccountType) {
      _syncAddAccountModalPlatformOptions();
      platform = document.getElementById('modalAddAcctPlatform').value;
    }
    if (!nickname) {
      if (msgEl) {
        msgEl.textContent = '请输入账号昵称';
        msgEl.className = 'msg err';
        msgEl.style.display = 'block';
      }
      return;
    }
    var body = { platform: platform, nickname: nickname };
    var ps = (document.getElementById('modalAddAcctProxyServer').value || '').trim();
    var pu = (document.getElementById('modalAddAcctProxyUser').value || '').trim();
    var ppEl = document.getElementById('modalAddAcctProxyPass');
    var pp = ppEl ? (ppEl.value || '') : '';
    var ua = (document.getElementById('modalAddAcctUa').value || '').trim();
    if (ps) body.proxy_server = ps;
    if (pu) body.proxy_username = pu;
    if (pp) body.proxy_password = pp;
    if (ua) body.user_agent = ua;
    addPubAcctSubmit.disabled = true;
    fetch(publishLocalBase() + '/api/accounts', {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify(body)
    })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(x) {
        if (x.ok) {
          if (msgEl) {
            msgEl.textContent = (x.data && x.data.message) ? x.data.message : '添加成功';
            msgEl.className = 'msg ok';
            msgEl.style.display = 'block';
          }
          document.getElementById('modalAddAcctNickname').value = '';
          document.getElementById('modalAddAcctProxyServer').value = '';
          document.getElementById('modalAddAcctProxyUser').value = '';
          if (ppEl) ppEl.value = '';
          document.getElementById('modalAddAcctUa').value = '';
          loadAccounts();
          setTimeout(closeAddPublishAccountModal, 500);
        } else {
          var det = x.data && x.data.detail;
          var errText = typeof det === 'string' ? det : (det ? JSON.stringify(det) : '添加失败');
          if (msgEl) {
            msgEl.textContent = errText;
            msgEl.className = 'msg err';
            msgEl.style.display = 'block';
          }
        }
      })
      .catch(function() {
        if (msgEl) {
          msgEl.textContent = '网络错误';
          msgEl.className = 'msg err';
          msgEl.style.display = 'block';
        }
      })
      .finally(function() { addPubAcctSubmit.disabled = false; });
  });
}
}

// ── Assets ───────────────────────────────────────────────────────

var _MEDIA_TYPE_LABELS = { image: '图片', video: '视频', audio: '音频', document: '文档' };
var _assetCreativeGroupsCache = [];
var _assetCreativeGroupEditingAssetId = '';
var _currentAssetOrigin = 'generated';
var _assetPreviewState = null;
var _assetPublishModalState = { asset: null, accounts: [], busy: false };
var _assetListCache = {};
var _assetActiveCacheKey = '';
var _CONTENT_RECORD_TYPE_OPTIONS = [
  ['image', '图片'],
  ['video', '视频'],
  ['article', '文案'],
  ['wechat_article', '公众号文章'],
  ['ppt', 'PPT']
];
var _UPLOAD_ASSET_TYPE_OPTIONS = [
  ['', '全部类型'],
  ['image', '图片'],
  ['video', '视频'],
  ['audio', '音频'],
  ['document', '文档']
];

var _assetMsgHideTimer = null;

function _assetMsgShow(text, isErr) {
  var m = document.getElementById('assetUploadMsg');
  if (!m) return;
  if (_assetMsgHideTimer) clearTimeout(_assetMsgHideTimer);
  m.textContent = text;
  m.className = 'msg' + (isErr ? ' err' : ' ok');
  m.style.display = 'inline';
  _assetMsgHideTimer = setTimeout(function() {
    m.style.display = 'none';
    _assetMsgHideTimer = null;
  }, isErr ? 12000 : 4000);
}

function _assetPreviewMsgShow(text, isErr) {
  var el = document.getElementById('assetPreviewModalMsg');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'msg' + (isErr ? ' err' : ' ok');
  el.style.display = text ? 'block' : 'none';
}

function _authHeadersNoContentType() {
  var h = typeof authHeaders === 'function' ? Object.assign({}, authHeaders()) : {};
  delete h['Content-Type'];
  delete h['content-type'];
  return h;
}

function _currentAssetSearchQuery() {
  return ((document.getElementById('assetSearchInput') || {}).value || '').trim();
}

function _currentAssetCreativeGroupFilter() {
  return ((document.getElementById('assetCreativeGroupFilter') || {}).value || '').trim();
}

function _currentAssetOriginFilter() {
  return _currentAssetOrigin === 'user_upload' ? 'user_upload' : 'generated';
}

function _isSharedContentRecordType(value) {
  return ['article', 'wechat_article', 'ppt'].indexOf(String(value || '')) >= 0;
}

function _assetCloudBase() {
  return (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
}

function _configureAssetTypeFilter(origin) {
  var select = document.getElementById('assetTypeFilter');
  if (!select) return;
  var options = origin === 'user_upload' ? _UPLOAD_ASSET_TYPE_OPTIONS : _CONTENT_RECORD_TYPE_OPTIONS;
  var current = select.value;
  if (!options.some(function(item) { return item[0] === current; })) current = origin === 'user_upload' ? '' : 'image';
  select.innerHTML = options.map(function(item) {
    return '<option value="' + escapeAttr(item[0]) + '">' + escapeHtml(item[1]) + '</option>';
  }).join('');
  select.value = current;
}

function _contentRecordKindLabel(kind) {
  return ({ article: '文案', wechat_article: '公众号文章', ppt: 'PPT' })[String(kind || '')] || '文案';
}

function _contentRecordDisplayLabel(item) {
  item = item && typeof item === 'object' ? item : {};
  var meta = item.meta && typeof item.meta === 'object' ? item.meta : {};
  var task = String(item.task || meta.task || '').trim().toLowerCase();
  var taskLabels = {
    moments_candidate: '朋友圈文案',
    industry_hot_oral: '行业口播文案',
    professional_ip_oral: 'IP口播文案',
    article: '深度长文'
  };
  return taskLabels[task] || _contentRecordKindLabel(item.kind);
}

function _contentRecordImageUrls(item) {
  item = item && typeof item === 'object' ? item : {};
  var urls = [];
  function add(value) {
    var url = String(value || '').trim();
    if (_isAbsoluteHttpUrl(url) && urls.indexOf(url) < 0) urls.push(url);
  }
  function walk(value, imageContext) {
    if (typeof value === 'string') {
      if (imageContext) add(value);
      return;
    }
    if (Array.isArray(value)) {
      value.forEach(function(entry) { walk(entry, imageContext); });
      return;
    }
    if (!value || typeof value !== 'object') return;
    Object.keys(value).forEach(function(key) {
      var normalized = String(key || '').toLowerCase();
      var nestedImageContext = imageContext || /image|cover|thumbnail|poster|preview/.test(normalized);
      if (imageContext && ['url', 'src', 'source_url', 'public_url'].indexOf(normalized) >= 0) add(value[key]);
      else walk(value[key], nestedImageContext);
    });
  }
  add(item.cover_url);
  add(item.image_url);
  walk(item.images, true);
  walk(item.image_urls, true);
  walk(item.image_results, true);
  walk(item.image_update, true);
  walk(item.meta, false);
  var content = String(item.content || item.body || '');
  var markdownPattern = /!\[[^\]]*\]\(\s*(https?:\/\/[^\s)]+)/gi;
  var htmlPattern = /<img\b[^>]*\bsrc\s*=\s*(["'])(https?:\/\/.*?)\1/gi;
  var match;
  while ((match = markdownPattern.exec(content))) add(match[1]);
  while ((match = htmlPattern.exec(content))) add(match[2]);
  return urls.slice(0, 30);
}

function _contentRecordImageAssetIds(item) {
  item = item && typeof item === 'object' ? item : {};
  var ids = [];
  function add(value) {
    var id = String(value || '').trim();
    if (id && ids.indexOf(id) < 0) ids.push(id);
  }
  function walk(value, imageContext) {
    if (Array.isArray(value)) {
      value.forEach(function(entry) { walk(entry, imageContext); });
      return;
    }
    if (!value || typeof value !== 'object') return;
    Object.keys(value).forEach(function(key) {
      var normalized = String(key || '').toLowerCase();
      var nextImageContext = imageContext || /image|cover|thumbnail|poster|preview/.test(normalized);
      if (nextImageContext && ['asset_id', 'image_asset_id'].indexOf(normalized) >= 0) add(value[key]);
      else walk(value[key], nextImageContext);
    });
  }
  add(item.image_asset_id);
  walk(item.image_asset_ids, true);
  walk(item.images, true);
  walk(item.image_results, true);
  walk(item.image_update, true);
  walk(item.meta, false);
  return ids.slice(0, 30);
}

function _contentRecordImageRefs(item) {
  item = item && typeof item === 'object' ? item : {};
  var refs = [];
  function add(value) {
    if (typeof value === 'string') value = { image_url: value };
    if (!value || typeof value !== 'object') return;
    var url = String(value.image_url || value.url || value.source_url || value.public_url || value.preview_url || '').trim();
    var assetId = String(value.image_asset_id || value.asset_id || '').trim();
    if (url && !_isAbsoluteHttpUrl(url)) url = '';
    if (!url && !assetId) return;
    var existing = refs.find(function(ref) {
      return (url && ref.image_url === url) || (assetId && ref.image_asset_id === assetId);
    });
    if (existing) {
      if (url && !existing.image_url) existing.image_url = url;
      if (assetId && !existing.image_asset_id) existing.image_asset_id = assetId;
      return;
    }
    refs.push({ image_url: url, image_asset_id: assetId });
  }
  function walk(value, imageContext) {
    if (typeof value === 'string') {
      if (imageContext) add(value);
      return;
    }
    if (Array.isArray(value)) {
      value.forEach(function(entry) { walk(entry, imageContext); });
      return;
    }
    if (!value || typeof value !== 'object') return;
    if (imageContext) add(value);
    Object.keys(value).forEach(function(key) {
      var normalized = String(key || '').toLowerCase();
      var nextImageContext = imageContext || /image|cover|thumbnail|poster|preview/.test(normalized);
      if (nextImageContext && ['url', 'src', 'source_url', 'public_url', 'preview_url', 'asset_id', 'image_asset_id'].indexOf(normalized) < 0) {
        walk(value[key], nextImageContext);
      }
    });
  }
  var urls = Array.isArray(item.image_urls) ? item.image_urls : [];
  var ids = Array.isArray(item.image_asset_ids) ? item.image_asset_ids : [];
  walk(item.images, true);
  walk(item.image_results, true);
  walk(item.image_update, true);
  walk(item.meta, false);
  var hasStructuredRefs = refs.length > 0;
  if (hasStructuredRefs) {
    urls.forEach(function(url) { add({ image_url: url }); });
    ids.forEach(function(assetId) { add({ image_asset_id: assetId }); });
  } else {
    for (var index = 0; index < Math.max(urls.length, ids.length); index += 1) {
      add({ image_url: urls[index] || '', image_asset_id: ids[index] || '' });
    }
  }
  add({ image_url: item.image_url || item.cover_url || '', image_asset_id: item.image_asset_id || '' });
  _contentRecordImageUrls(item).forEach(function(url) { add({ image_url: url }); });
  _contentRecordImageAssetIds(item).forEach(function(assetId) { add({ image_asset_id: assetId }); });
  return refs.slice(0, 30);
}

function _isMomentsContentRecord(asset) {
  asset = asset && typeof asset === 'object' ? asset : {};
  var meta = asset.meta && typeof asset.meta === 'object' ? asset.meta : {};
  var source = String(asset.source || '').trim().toLowerCase();
  var task = String(asset.task || meta.task || '').trim().toLowerCase();
  return source === 'ip_daily' && task === 'moments_candidate';
}

function _normalizeSharedContentRecord(item) {
  item = item && typeof item === 'object' ? item : {};
  var imageRefs = _contentRecordImageRefs(item);
  var imageUrls = imageRefs.map(function(ref) { return ref.image_url; }).filter(Boolean);
  var imageAssetIds = imageRefs.map(function(ref) { return ref.image_asset_id; }).filter(Boolean);
  var coverUrl = String(imageUrls[0] || '').trim();
  var sourceUrl = String(item.file_url || item.source_url || coverUrl || '').trim();
  return Object.assign({}, item, {
    asset_id: String(item.asset_id || item.record_id || ('content:' + (item.source || '') + ':' + (item.source_id || ''))),
    asset_origin: 'generated',
    media_type: 'document',
    filename: String(item.filename || item.title || _contentRecordDisplayLabel(item)),
    source_url: sourceUrl,
    cover_url: coverUrl,
    image_urls: imageUrls,
    image_asset_ids: imageAssetIds,
    image_refs: imageRefs,
    preview_url: coverUrl,
    open_url: String(item.file_url || item.source_url || '').trim(),
    prompt: String(item.summary || item.prompt || '').trim(),
    _content_record: true,
    _compact: item._compact !== false
  });
}

function _assetContentText(asset) {
  asset = asset && typeof asset === 'object' ? asset : {};
  var meta = asset.meta && typeof asset.meta === 'object' ? asset.meta : {};
  var values = [
    asset.content, asset.body, asset.copy, asset.description, asset.caption, asset.publish_copy,
    asset.script, asset.voiceover_script, asset.oral_script, asset.summary,
    meta.content, meta.body, meta.copy, meta.description, meta.caption, meta.publish_copy,
    meta.script, meta.voiceover_script, meta.oral_script,
    asset.prompt
  ];
  for (var i = 0; i < values.length; i += 1) {
    var text = Array.isArray(values[i])
      ? values[i].map(function(value) { return String(value || '').trim(); }).filter(Boolean).join(' ')
      : String(values[i] || '').trim();
    if (text) return text;
  }
  return '';
}

function _assetCreativePromptValue(value) {
  var text = Array.isArray(value)
    ? value.map(function(item) { return String(item || '').trim(); }).filter(Boolean).join(' ')
    : String(value || '').trim();
  if (!text) return '';
  if (/^(?:https?:\/\/|data:|blob:|\/?(?:assets?|uploads?|files?|media)\/|[a-z]:[\\/]|\.{0,2}[\\/])\S+$/i.test(text)) return '';
  if (/^[^\s<>]+\.(?:png|jpe?g|webp|gif|bmp|svg|mp4|mov|webm|m4v)(?:[?#].*)?$/i.test(text)) return '';
  return text;
}

function _assetCreativePromptFromObject(value, depth, includeGenericPrompt) {
  if (!value || typeof value !== 'object' || depth > 4) return '';
  var keys = [
    'image_prompt', 'image_prompts', 'video_prompt', 'video_prompts',
    'visual_prompt', 'visual_prompts', 'original_prompt', 'creative_prompt',
    'generation_prompt'
  ];
  if (includeGenericPrompt) keys.push('prompt');
  for (var i = 0; i < keys.length; i += 1) {
    var prompt = _assetCreativePromptValue(value[keys[i]]);
    if (prompt) return prompt;
  }
  var nestedKeys = ['meta', 'params', 'payload', 'input', 'request', 'request_payload', 'generation'];
  for (var j = 0; j < nestedKeys.length; j += 1) {
    var nested = _assetCreativePromptFromObject(value[nestedKeys[j]], depth + 1, true);
    if (nested) return nested;
  }
  return '';
}

function _assetCreativePrompt(asset) {
  asset = asset && typeof asset === 'object' ? asset : {};
  var meta = asset.meta && typeof asset.meta === 'object' ? asset.meta : {};
  var prompt = _assetCreativePromptFromObject(asset, 0, false) || _assetCreativePromptFromObject(meta, 0, true);
  var kind = String(asset.kind || '').toLowerCase();
  var source = String(asset.source || '').toLowerCase();
  var storedPromptIsCreative = !asset._content_record || ['image', 'video'].indexOf(String(asset.media_type || '')) >= 0 || source === 'ip_daily' || ['article', 'wechat_article', 'ppt'].indexOf(kind) < 0;
  if (!prompt && storedPromptIsCreative) prompt = _assetCreativePromptValue(asset.prompt);
  if (prompt) return prompt;
  return _assetContentText(asset);
}

function _assetContentTags(asset) {
  asset = asset && typeof asset === 'object' ? asset : {};
  var meta = asset.meta && typeof asset.meta === 'object' ? asset.meta : {};
  var values = [asset.tags, asset.hashtags, meta.tags, meta.hashtags];
  for (var i = 0; i < values.length; i += 1) {
    var text = Array.isArray(values[i])
      ? values[i].map(function(value) { return String(value || '').trim(); }).filter(Boolean).join(' ')
      : String(values[i] || '').trim();
    if (text) return text;
  }
  return '';
}

function _assetContentActionDefinitions(asset) {
  asset = asset && typeof asset === 'object' ? asset : {};
  var mediaType = String(asset.media_type || '').toLowerCase();
  var kind = String(asset.kind || '').toLowerCase();
  var momentsRecord = _isMomentsContentRecord(asset);
  var momentsImageCount = _contentRecordImageUrls(asset).length + _contentRecordImageAssetIds(asset).length;
  var textBased = (!!_assetContentText(asset) || !!asset._compact) && ['article', 'wechat_article'].indexOf(kind) >= 0;
  var actions = [];
  function add(action, label) {
    if (!actions.some(function(row) { return row.action === action; })) actions.push({ action: action, label: label });
  }
  if (mediaType === 'image' || mediaType === 'video' || textBased || ['article', 'wechat_article', 'ppt'].indexOf(kind) >= 0) add('regenerate', '重新生成');
  if (textBased) {
    add('copy', '复制文案');
    add('generate_image', '生成图片');
    add('generate_video', '生成视频');
    add('generate_talking_video', '数字人口播');
  }
  if (mediaType === 'image') {
    add('generate_video', '生成视频');
    add('generate_avatar', '生成数字人');
  }
  if (mediaType === 'video') add('generate_avatar', '生成数字人');
  if (momentsRecord && momentsImageCount > 0) add('publish_moments', '发布到朋友圈');
  if (
    (mediaType === 'image' || mediaType === 'video' || kind === 'ppt') &&
    (_assetPublishExistingAssetId(asset) || _assetPublishUrlCandidate(asset).url)
  ) add('publish', '发布');
  return actions;
}

function _assetContentActionMenuHtml(asset) {
  var actions = _assetContentActionDefinitions(asset);
  if (!actions.length) return '';
  return '<details class="asset-content-action-menu"><summary>操作</summary><div class="asset-content-action-list">' + actions.map(function(row) {
    return '<button type="button" data-asset-content-action="' + escapeAttr(row.action) + '" data-asset-id="' + escapeAttr(asset.asset_id || '') + '">' + escapeHtml(row.label) + '</button>';
  }).join('') + '</div></details>';
}

function _resolveAssetContentActionDetail(asset) {
  if (!asset || !asset._content_record || asset._compact === false) return Promise.resolve(asset || {});
  var base = _assetCloudBase();
  if (!base || !asset.source || !asset.source_id) return Promise.resolve(asset);
  var url = base + '/api/content-records/detail?source=' + encodeURIComponent(asset.source) + '&source_id=' + encodeURIComponent(asset.source_id);
  return fetch(url, { headers: authHeaders() }).then(function(response) {
    return response.json().catch(function() { return {}; }).then(function(data) {
      if (!response.ok) throw new Error((data && data.detail) || ('HTTP ' + response.status));
      var item = data && data.item && typeof data.item === 'object' ? data.item : data;
      return _normalizeSharedContentRecord(Object.assign({}, asset, item, { _compact: false }));
    });
  });
}

function _assetWaitForElement(id, attempts) {
  attempts = attempts === undefined ? 40 : attempts;
  return new Promise(function(resolve, reject) {
    function check(left) {
      var node = document.getElementById(id);
      if (node) return resolve(node);
      if (left <= 0) return reject(new Error('目标功能加载失败，请返回后重试'));
      setTimeout(function() { check(left - 1); }, 50);
    }
    check(attempts);
  });
}

function _assetSetField(id, value) {
  var field = document.getElementById(id);
  if (!field) return false;
  field.value = String(value || '');
  field.dispatchEvent(new Event('input', { bubbles: true }));
  field.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

function _assetOpenWorkspace(view) {
  if (typeof window.isLobsterViewAllowed === 'function' && !window.isLobsterViewAllowed(view)) {
    return Promise.reject(new Error('当前账号没有该创作功能权限'));
  }
  var openers = {
    'image-composer-studio': '_openImageComposerStudioView',
    'seedance-tvc-studio': '_openSeedanceTvcStudioView',
    'shanjian-digital-human': '_openShanjianDigitalHumanView',
    'wechat-article': '_openWechatArticleView',
    'ip-content-studio': '_openIpContentStudioView'
  };
  var opener = openers[view] && window[openers[view]];
  if (typeof opener === 'function') return Promise.resolve(opener());
  if (typeof window.showAppView === 'function') return Promise.resolve(window.showAppView(view));
  if (typeof window._openHiddenWorkspaceView === 'function') {
    window._openHiddenWorkspaceView(view);
    return Promise.resolve();
  }
  return Promise.reject(new Error('目标功能暂时无法打开'));
}

function _assetPrimaryMediaUrl(asset) {
  var mediaType = String(asset && asset.media_type || '').toLowerCase();
  var images = _contentRecordImageUrls(asset);
  if (mediaType === 'image' && images.length) return images[0];
  var values = [asset && asset.open_url, asset && asset.source_url, asset && asset.file_url, asset && asset.preview_url, asset && asset.cover_url];
  for (var i = 0; i < values.length; i += 1) {
    var url = _resolvePossiblyRelativeMediaUrl(values[i]);
    if (url) return url;
  }
  if (asset && asset.asset_id && !asset._content_record && publishLocalBase()) {
    return publishLocalBase() + '/api/assets/' + encodeURIComponent(asset.asset_id) + '/content';
  }
  return '';
}

function _assetOpenImageWorkbench(asset, includeReference) {
  var prompt = _assetCreativePrompt(asset);
  var imageUrl = includeReference ? _assetPrimaryMediaUrl(asset) : '';
  return _assetOpenWorkspace('image-composer-studio').then(function() {
    return _assetWaitForElement('imglabPromptInput');
  }).then(function() {
    if (typeof window.prefillImageComposerContent === 'function') {
      window.prefillImageComposerContent({
        prompt: prompt,
        image_url: imageUrl,
        asset_id: asset.asset_id || '',
        filename: asset.filename || asset.title || '内容图片'
      });
    } else {
      _assetSetField('imglabPromptInput', prompt);
    }
  });
}

function _assetOpenVideoWorkbench(asset, includeReference) {
  var prompt = _assetCreativePrompt(asset);
  var imageUrl = includeReference ? _assetPrimaryMediaUrl(asset) : '';
  return _assetOpenWorkspace('seedance-tvc-studio').then(function() {
    return _assetWaitForElement('seedanceTaskPromptInput');
  }).then(function() {
    if (typeof window.prefillSeedanceTvcContent === 'function') {
      window.prefillSeedanceTvcContent({
        prompt: prompt,
        image_url: imageUrl,
        asset_id: asset.asset_id || '',
        filename: asset.filename || asset.title || '内容图片'
      });
    } else {
      _assetSetField('seedanceTaskPromptInput', prompt);
    }
  });
}

function _assetOpenTalkingVideo(asset) {
  var script = _assetContentText(asset);
  if (!script) return Promise.reject(new Error('当前内容没有可用于口播的正文'));
  return _assetOpenWorkspace('shanjian-digital-human').then(function() {
    return _assetWaitForElement('shanjianScriptInput');
  }).then(function() {
    _assetSetField('shanjianScriptInput', script);
    _assetSetField('shanjianTitleInput', String(asset.title || '数字人口播').slice(0, 20));
  });
}

function _assetOpenMomentsPublish(asset) {
  var imageRefs = Array.isArray(asset && asset.image_refs) && asset.image_refs.length
    ? asset.image_refs
    : _contentRecordImageRefs(asset);
  var imageUrls = imageRefs.map(function(ref) { return String(ref.image_url || '').trim(); });
  var imageAssetIds = imageRefs.map(function(ref) { return String(ref.image_asset_id || '').trim(); });
  if (!imageRefs.length) return Promise.reject(new Error('请先生成图片，再发布朋友圈'));
  if (typeof window._openJuheWechatView !== 'function') return Promise.reject(new Error('朋友圈发布功能暂时无法打开'));
  window._openJuheWechatView();
  return new Promise(function(resolve, reject) {
    var attempts = 80;
    function check() {
      if (document.getElementById('nativeWechatMomentsContent') && typeof window.prefillNativeWechatMoments === 'function') {
        window.prefillNativeWechatMoments({
          content: _assetContentText(asset),
          title: String(asset.title || '').trim(),
          image_urls: imageUrls,
          image_asset_ids: imageAssetIds,
          images: imageRefs,
          source: String(asset.source || '').trim(),
          source_id: String(asset.source_id || '').trim(),
          media_type: 'image_text'
        });
        resolve();
        return;
      }
      if (attempts <= 0) {
        reject(new Error('朋友圈发布页面尚未加载完成'));
        return;
      }
      attempts -= 1;
      setTimeout(check, 80);
    }
    check();
  });
}

function _assetOpenDocumentRegenerate(asset) {
  var kind = String(asset.kind || '').toLowerCase();
  var text = _assetContentText(asset);
  var title = String(asset.title || _contentRecordDisplayLabel(asset)).trim();
  if (kind === 'article' && String(asset.source || '').toLowerCase() === 'ip_daily') {
    return _assetOpenWorkspace('ip-content-studio').then(function() {
      var task = String((asset.meta || {}).task || '');
      var fieldId = task === 'moments_candidate' ? 'ipTask2Extra' : 'ipTask1Extra';
      return _assetWaitForElement(fieldId).then(function() { _assetSetField(fieldId, [title, text].filter(Boolean).join('\n\n')); });
    });
  }
  if (kind === 'ppt') {
    return _assetOpenWorkspace('ppt-studio').then(function() {
      return _assetWaitForElement('pptStudioTopic');
    }).then(function() {
      _assetSetField('pptStudioTopic', title);
      _assetSetField('pptStudioPrompt', text);
    });
  }
  return _assetOpenWorkspace('wechat-article').then(function() {
    return _assetWaitForElement('wechatArticleIdea');
  }).then(function() {
    _assetSetField('wechatArticleIdea', [title, text].filter(Boolean).join('\n\n'));
  });
}

function _assetFetchMediaFile(asset) {
  var url = _assetPrimaryMediaUrl(asset);
  if (!url) return Promise.reject(new Error('当前内容没有可读取的素材地址'));
  var localBase = publishLocalBase();
  var options = url.indexOf(localBase + '/') === 0 ? { headers: _authHeadersForMediaFetch() } : {};
  return fetch(url, options).then(function(response) {
    if (!response.ok) throw new Error('素材读取失败：HTTP ' + response.status);
    return response.blob();
  }).then(function(blob) {
    var mediaType = String(asset.media_type || '').toLowerCase();
    var fallback = mediaType === 'video' ? 'digital-human-video.mp4' : 'digital-human-image.jpg';
    var filename = String(asset.filename || fallback).trim() || fallback;
    return new File([blob], filename, { type: blob.type || (mediaType === 'video' ? 'video/mp4' : 'image/jpeg'), lastModified: Date.now() });
  });
}

function _assetOpenAvatarClone(asset) {
  var mediaType = String(asset.media_type || '').toLowerCase();
  if (['image', 'video'].indexOf(mediaType) < 0) return Promise.reject(new Error('只有图片或视频可以生成数字人'));
  return Promise.all([_assetOpenWorkspace('shanjian-digital-human'), _assetFetchMediaFile(asset)]).then(function(results) {
    var file = results[1];
    return _assetWaitForElement('shanjianOpenAvatarCreateBtn').then(function(openButton) {
      openButton.click();
      var modeButton = document.querySelector('#content-shanjian-digital-human [data-avatar-mode="' + mediaType + '"]');
      if (!modeButton) throw new Error('数字人分身入口加载失败');
      modeButton.click();
      var inputId = mediaType === 'video' ? 'shanjianAvatarVideoFile' : 'shanjianAvatarImageFile';
      var nameId = mediaType === 'video' ? 'shanjianAvatarVideoName' : 'shanjianAvatarImageName';
      return _assetWaitForElement(inputId).then(function(input) {
        var transfer = new DataTransfer();
        transfer.items.add(file);
        input.files = transfer.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
        _assetSetField(nameId, String(asset.title || '内容数字人').slice(0, 20));
      });
    });
  });
}

function _assetPublishMsgShow(text, isErr) {
  var msg = document.getElementById('assetPublishModalMsg');
  if (!msg) return;
  msg.textContent = text || '';
  msg.className = 'msg' + (isErr ? ' err' : ' ok');
  msg.style.display = text ? 'block' : 'none';
}

function _assetPublishIsOnlineAccount(account) {
  var status = String(account && account.status || '').trim().toLowerCase();
  return status === 'active' || status === 'online';
}

function _assetPublishAccountList(accounts) {
  return (accounts || []).filter(function(account) {
    var platform = String(account && account.platform || '').trim();
    return platform && !ECOMMERCE_PLATFORMS[platform] && PUBLISH_ACCOUNT_PLATFORMS.indexOf(platform) >= 0 && _assetPublishIsOnlineAccount(account);
  }).sort(function(a, b) {
    return String(a.platform || '').localeCompare(String(b.platform || '')) || String(a.nickname || '').localeCompare(String(b.nickname || ''));
  });
}

function _assetPublishAccountLabel(account) {
  var platform = PLATFORM_NAMES[account.platform] || account.platform || '平台';
  var name = account.nickname || ('账号 ' + account.id);
  var status = STATUS_LABELS[account.status] || account.status || '';
  return platform + ' · ' + name + (status ? ' · ' + status : '');
}

function _assetPublishSelectedAccount() {
  var select = document.getElementById('assetPublishAccountSelect');
  var id = select ? String(select.value || '') : '';
  return (_assetPublishModalState.accounts || []).filter(function(account) {
    return String(account.id) === id;
  })[0] || null;
}

function _assetPublishUpdateAccountMeta() {
  var meta = document.getElementById('assetPublishAccountMeta');
  var account = _assetPublishSelectedAccount();
  if (!meta) return;
  if (!account) {
    meta.textContent = '';
    return;
  }
  var status = STATUS_LABELS[account.status] || account.status || '';
  meta.textContent = (PLATFORM_NAMES[account.platform] || account.platform || '平台') +
    (status ? ' · ' + status : '') +
    (account.origin_account_id ? ' · 抖音账号' + account.origin_account_id : '');
}

function _assetPublishRenderAccounts(accounts, preferredAccountId) {
  var select = document.getElementById('assetPublishAccountSelect');
  if (!select) return;
  var list = _assetPublishAccountList(accounts);
  _assetPublishModalState.accounts = list;
  if (!list.length) {
    select.innerHTML = '<option value="">暂无可发布账号</option>';
    select.disabled = true;
    _assetPublishUpdateAccountMeta();
    return;
  }
  select.disabled = false;
  select.innerHTML = list.map(function(account) {
    return '<option value="' + escapeAttr(String(account.id)) + '">' + escapeHtml(_assetPublishAccountLabel(account)) + '</option>';
  }).join('');
  var selected = '';
  if (preferredAccountId && list.some(function(account) { return String(account.id) === String(preferredAccountId); })) {
    selected = String(preferredAccountId);
  } else {
    var online = list.filter(_assetPublishIsOnlineAccount)[0];
    selected = String((online || list[0]).id);
  }
  select.value = selected;
  _assetPublishUpdateAccountMeta();
}

function _assetPublishLoadAccounts(preferredAccountId) {
  var select = document.getElementById('assetPublishAccountSelect');
  if (select) {
    select.disabled = true;
    select.innerHTML = '<option value="">正在加载账号...</option>';
  }
  return fetch(publishLocalBase() + '/api/accounts', { headers: authHeaders() })
    .then(_publishParseResponse)
    .then(function(x) {
      if (!x.ok) throw new Error((x.d && (x.d.detail || x.d.message)) || ('HTTP ' + x.status));
      var accounts = (x.d && Array.isArray(x.d.accounts)) ? x.d.accounts : [];
      _allAccounts = accounts;
      _assetPublishRenderAccounts(accounts, preferredAccountId);
      return accounts;
    });
}

function _assetPublishSourcePrompt(asset) {
  asset = asset && typeof asset === 'object' ? asset : {};
  var meta = asset.meta && typeof asset.meta === 'object' ? asset.meta : {};
  var values = [
    _assetCreativePrompt(asset),
    _assetContentText(asset),
    asset.summary,
    asset.prompt,
    meta.prompt,
    meta.description,
    asset.title,
    asset.filename
  ];
  for (var i = 0; i < values.length; i += 1) {
    var text = String(values[i] || '').trim();
    if (text) return text;
  }
  return '';
}

function _assetPublishMediaTypeFromUrl(url, fallback) {
  var ext = String(url || '').split('?')[0].split('#')[0].split('.').pop().toLowerCase();
  if (['mp4', 'mov', 'webm', 'm4v', 'avi', 'mkv'].indexOf(ext) >= 0) return 'video';
  if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'].indexOf(ext) >= 0) return 'image';
  var fb = String(fallback || '').toLowerCase();
  return fb === 'video' ? 'video' : 'image';
}

function _assetPublishUrlCandidate(asset) {
  asset = asset && typeof asset === 'object' ? asset : {};
  if (asset._content_record) {
    var imageRefs = Array.isArray(asset.image_refs) && asset.image_refs.length ? asset.image_refs : _contentRecordImageRefs(asset);
    for (var i = 0; i < imageRefs.length; i += 1) {
      var imageUrl = String(imageRefs[i] && imageRefs[i].image_url || '').trim();
      if (_isAbsoluteHttpUrl(imageUrl)) return { url: imageUrl, media_type: 'image' };
    }
  }
  var mediaType = String(asset.media_type || '').toLowerCase();
  var values = [asset.source_url, asset.file_url, asset.open_url, asset.preview_url, asset.cover_url];
  for (var j = 0; j < values.length; j += 1) {
    var url = _resolvePossiblyRelativeMediaUrl(values[j]);
    if (_isAbsoluteHttpUrl(url)) {
      return { url: url, media_type: _assetPublishMediaTypeFromUrl(url, mediaType) };
    }
  }
  return { url: '', media_type: mediaType === 'video' ? 'video' : 'image' };
}

function _assetPublishExistingAssetId(asset) {
  asset = asset && typeof asset === 'object' ? asset : {};
  if (!asset._content_record) return String(asset.asset_id || '').trim();
  var imageIds = Array.isArray(asset.image_asset_ids) && asset.image_asset_ids.length
    ? asset.image_asset_ids
    : _contentRecordImageAssetIds(asset);
  if (imageIds.length) return String(imageIds[0] || '').trim();
  var kind = String(asset.kind || '').toLowerCase();
  var sourceId = String(asset.source_id || '').trim();
  if (kind === 'ppt' && sourceId) return sourceId;
  return '';
}

function _assetPublishResolveTarget(asset) {
  var existingId = _assetPublishExistingAssetId(asset);
  if (existingId) return Promise.resolve({ asset_id: existingId, saved_from_url: false });
  var candidate = _assetPublishUrlCandidate(asset);
  if (!candidate.url) return Promise.reject(new Error('当前记录没有可发布的素材 ID 或公网素材链接'));
  if (candidate.media_type !== 'image' && candidate.media_type !== 'video') {
    return Promise.reject(new Error('当前记录不是图片或视频素材，不能直接发布'));
  }
  var sourcePrompt = _assetPublishSourcePrompt(asset);
  return fetch(publishLocalBase() + '/api/assets/save-url', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
    body: JSON.stringify({
      url: candidate.url,
      media_type: candidate.media_type,
      name: String(asset.filename || asset.title || '').slice(0, 180),
      prompt: sourcePrompt.slice(0, 2000),
      tags: _assetContentTags(asset)
    })
  }).then(_publishParseResponse).then(function(x) {
    if (!x.ok) throw new Error((x.d && (x.d.detail || x.d.message)) || ('HTTP ' + x.status));
    if (!x.d || !x.d.asset_id) throw new Error('素材转存未返回 asset_id');
    return { asset_id: x.d.asset_id, saved_from_url: true, source_url: candidate.url };
  });
}

function _assetPublishSetBusy(busy) {
  _assetPublishModalState.busy = !!busy;
  var submit = document.getElementById('assetPublishModalSubmit');
  var cancel = document.getElementById('assetPublishModalCancel');
  var close = document.getElementById('assetPublishModalClose');
  var select = document.getElementById('assetPublishAccountSelect');
  if (submit) {
    submit.disabled = !!busy;
    submit.textContent = busy ? '发布中...' : '确认发布';
  }
  if (cancel) cancel.disabled = !!busy;
  if (close) close.disabled = !!busy;
  if (select) select.disabled = !!busy || !(_assetPublishModalState.accounts || []).length;
}

function _closeAssetPublishModal() {
  if (_assetPublishModalState.busy) return;
  var modal = document.getElementById('assetPublishModal');
  if (modal) modal.style.display = 'none';
  _assetPublishModalState.asset = null;
  _assetPublishMsgShow('', false);
}

function _assetShowPublishModal(asset) {
  var existingId = _assetPublishExistingAssetId(asset);
  var candidate = existingId ? { url: '', media_type: '' } : _assetPublishUrlCandidate(asset);
  if (!existingId && !candidate.url) return Promise.reject(new Error('当前记录没有可发布的素材'));
  _assetPublishModalState.asset = asset;
  _assetPublishModalState.accounts = [];
  _assetPublishModalState.busy = false;

  var modal = document.getElementById('assetPublishModal');
  var title = document.getElementById('assetPublishModalTitle');
  var meta = document.getElementById('assetPublishModalMeta');
  var info = document.getElementById('assetPublishAssetInfo');
  var titleInput = document.getElementById('assetPublishTitleInput');
  var descInput = document.getElementById('assetPublishDescriptionInput');
  var tagsInput = document.getElementById('assetPublishTagsInput');
  var mediaType = String(asset.media_type || '').toLowerCase();
  var label = asset._content_record ? _contentRecordDisplayLabel(asset) : (_MEDIA_TYPE_LABELS[mediaType] || mediaType || '素材');
  var sourcePrompt = _assetPublishSourcePrompt(asset);

  if (title) title.textContent = '发布' + label;
  if (meta) meta.textContent = String(asset.title || asset.filename || label || '素材').trim();
  if (titleInput) titleInput.value = '';
  if (descInput) descInput.value = '';
  if (tagsInput) tagsInput.value = '';
  if (info) {
    var targetId = existingId || '提交时自动转存';
    var promptLine = sourcePrompt ? sourcePrompt.slice(0, 180) + (sourcePrompt.length > 180 ? '...' : '') : '暂无提示词';
    info.innerHTML =
      '<div><span style="color:var(--text-muted);">素材：</span>' + escapeHtml(targetId) + '</div>' +
      '<div><span style="color:var(--text-muted);">类型：</span>' + escapeHtml(label) + '</div>' +
      '<div><span style="color:var(--text-muted);">提示词：</span>' + escapeHtml(promptLine) + '</div>';
  }
  _assetPublishMsgShow('', false);
  if (modal) modal.style.display = 'flex';
  return _assetPublishLoadAccounts().catch(function(error) {
    _assetPublishMsgShow('账号加载失败：' + ((error && error.message) || error), true);
  });
}

function _submitAssetPublishModal() {
  if (_assetPublishModalState.busy) return;
  var asset = _assetPublishModalState.asset;
  if (!asset) return _assetPublishMsgShow('当前发布素材已失效，请重新选择。', true);
  var account = _assetPublishSelectedAccount();
  if (!account) return _assetPublishMsgShow('请选择发布账号。', true);
  var titleInput = document.getElementById('assetPublishTitleInput');
  var descInput = document.getElementById('assetPublishDescriptionInput');
  var tagsInput = document.getElementById('assetPublishTagsInput');
  var userTitle = String(titleInput && titleInput.value || '').trim();
  var userDesc = String(descInput && descInput.value || '').trim();
  var userTags = String(tagsInput && tagsInput.value || '').trim();
  var sourcePrompt = _assetPublishSourcePrompt(asset);
  var autoCopy = !userTitle && !userDesc && !userTags;

  _assetPublishSetBusy(true);
  _assetPublishMsgShow('正在准备素材...', false);
  _assetPublishResolveTarget(asset).then(function(target) {
    var payload = {
      asset_id: target.asset_id,
      account_id: parseInt(account.id, 10) || undefined,
      account_nickname: account.nickname || '',
      title: userTitle,
      description: userDesc,
      tags: userTags,
      options: {
        _source_prompt: sourcePrompt.slice(0, 2000),
        _source_title: String(asset.title || asset.filename || '').slice(0, 240)
      }
    };
    if (autoCopy) {
      payload.ai_publish_copy = true;
      payload.description = sourcePrompt.slice(0, 2000) || String(asset.title || asset.filename || '作品发布').slice(0, 240);
      payload.tags = _assetContentTags(asset);
    }
    if (target.saved_from_url) payload.options._saved_from_url = String(target.source_url || '').slice(0, 300);
    _assetPublishMsgShow('正在发布到 ' + _assetPublishAccountLabel(account) + '...', false);
    return fetch(publishLocalBase() + '/api/publish', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
      body: JSON.stringify(payload)
    }).then(_publishParseResponse);
  }).then(function(x) {
    var data = x.d || {};
    if (!x.ok) throw new Error(data.detail || data.message || ('HTTP ' + x.status));
    if (data.need_login) {
      _assetPublishMsgShow('账号需要登录，浏览器已打开，请登录后重试。', true);
      return;
    }
    if (data.status === 'success') {
      _assetPublishMsgShow('发布成功。' + (data.result_url ? ' ' + data.result_url : ''), false);
    } else if (data.status === 'pending' || data.status === 'publishing') {
      _assetPublishMsgShow('发布任务已提交，任务 ID：' + (data.task_id || '-'), false);
    } else {
      _assetPublishMsgShow(data.error || data.detail || '发布失败', true);
    }
    if (_currentPubTab === 'tasks') loadTasks();
  }).catch(function(error) {
    _assetPublishMsgShow((error && error.message) || '发布失败', true);
  }).finally(function() {
    _assetPublishSetBusy(false);
  });
}

function _assetOpenPublish(asset) {
  return _assetShowPublishModal(asset);
}

function _performAssetContentAction(asset, action) {
  return _resolveAssetContentActionDetail(asset).then(function(detail) {
    var mediaType = String(detail.media_type || '').toLowerCase();
    if (action === 'copy') {
      var copyValue = _assetContentText(detail);
      if (!copyValue) return Promise.reject(new Error('当前文案为空'));
      if (typeof copyToClipboard === 'function') {
        return new Promise(function(resolve) {
          copyToClipboard(copyValue, function() {
            _assetMsgShow('文案已复制。', false);
            resolve();
          });
        });
      }
      return Promise.reject(new Error('当前浏览器不支持复制'));
    }
    if (action === 'regenerate') {
      if (mediaType === 'image') return _assetOpenImageWorkbench(detail, true);
      if (mediaType === 'video') return _assetOpenVideoWorkbench(detail, false);
      return _assetOpenDocumentRegenerate(detail);
    }
    if (action === 'generate_image') return _assetOpenImageWorkbench(detail, false);
    if (action === 'generate_video') return _assetOpenVideoWorkbench(detail, mediaType === 'image');
    if (action === 'generate_talking_video') return _assetOpenTalkingVideo(detail);
    if (action === 'generate_avatar') return _assetOpenAvatarClone(detail);
    if (action === 'publish_moments') return _assetOpenMomentsPublish(detail);
    if (action === 'publish') return _assetOpenPublish(detail);
    throw new Error('当前操作暂不支持');
  });
}

function _bindAssetContentActions(container, assetMap) {
  if (!container) return;
  container.querySelectorAll('button[data-asset-content-action]').forEach(function(button) {
    if (button._assetContentActionBound) return;
    button._assetContentActionBound = true;
    button.addEventListener('click', function(event) {
      event.stopPropagation();
      var asset = assetMap[button.getAttribute('data-asset-id') || ''];
      var action = button.getAttribute('data-asset-content-action') || '';
      var menu = button.closest('details');
      if (menu) menu.removeAttribute('open');
      if (!asset) return _assetMsgShow('当前记录已刷新，请重新操作。', true);
      button.disabled = true;
      _performAssetContentAction(asset, action).catch(function(error) {
        _assetMsgShow((error && error.message) || '操作失败', true);
      }).finally(function() {
        button.disabled = false;
      });
    });
  });
}

function _assetListCacheKey(query) {
  var mediaType = (document.getElementById('assetTypeFilter') || {}).value || '';
  var creativeGroup = _currentAssetCreativeGroupFilter();
  var origin = _currentAssetOriginFilter();
  return [
    publishLocalBase(),
    origin,
    mediaType,
    creativeGroup,
    (query || '').trim()
  ].join('\u0001');
}

function _assetListFingerprint(assets) {
  return JSON.stringify((assets || []).map(function(a) {
    return [
      a && a.asset_id,
      a && a.asset_origin,
      a && a.media_type,
      a && a.source_url,
      a && a.preview_url,
      a && a.open_url,
      a && a.filename,
      a && a.prompt,
      a && a.file_size,
      a && a.tags,
      a && a.creative_candidate_group,
      a && (Array.isArray(a.creative_candidate_groups) ? a.creative_candidate_groups.join('|') : ''),
      a && a.created_at,
      a && a.updated_at
    ];
  }));
}

function _assetListLoadingHtml() {
  return '<div class="page-empty-card">加载中...</div>';
}

function _assetListEmptyHtml() {
  if (_currentAssetOriginFilter() === 'user_upload') {
    return '<div class="page-empty-card">暂无用户上传素材。可上传本地文件或保存网络 URL。</div>';
  }
  return '<div class="page-empty-card">暂无内容记录。生成的图片、视频、音频和文档会显示在这里。</div>';
}

function _setAssetOriginTab(origin) {
  _currentAssetOrigin = origin === 'user_upload' ? 'user_upload' : 'generated';
  _configureAssetTypeFilter(_currentAssetOrigin);
  var root = document.getElementById('content-assets');
  if (root) root.setAttribute('data-asset-origin', _currentAssetOrigin);
  document.querySelectorAll('.asset-origin-tab').forEach(function(item) {
    item.classList.toggle('active', item.getAttribute('data-asset-origin') === _currentAssetOrigin);
  });
  var title = document.getElementById('assetViewTitle');
  var description = document.getElementById('assetViewDescription');
  if (title) title.textContent = _currentAssetOrigin === 'user_upload' ? '素材库' : '内容记录';
  if (description) description.textContent = _currentAssetOrigin === 'user_upload'
    ? '查看用户上传的图片、视频、音频和文档。'
    : '查看 AI 生成的图片、视频、文案、公众号文章和 PPT。';
}

function setAssetLibraryOrigin(origin, options) {
  options = options || {};
  _setAssetOriginTab(origin);
  if (options.load !== false) loadAssets(_currentAssetSearchQuery(), options);
}
window.setAssetLibraryOrigin = setAssetLibraryOrigin;

function _copyAssetPrompt(promptText) {
  var text = (promptText || '').trim();
  if (!text) {
    _assetPreviewMsgShow('当前素材没有可复制的提示词。', true);
    return;
  }
  if (typeof copyToClipboard === 'function') {
    copyToClipboard(text, function() {
      _assetPreviewMsgShow('提示词已复制。', false);
    });
    return;
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function() {
      _assetPreviewMsgShow('提示词已复制。', false);
    }).catch(function() {
      _assetPreviewMsgShow('复制失败，请稍后重试。', true);
    });
    return;
  }
  _assetPreviewMsgShow('当前环境不支持复制。', true);
}

function _closeAssetPreviewModal() {
  var mask = document.getElementById('assetPreviewModal');
  var stage = document.getElementById('assetPreviewStage');
  if (stage) stage.innerHTML = '';
  if (mask) mask.style.display = 'none';
  _assetPreviewState = null;
  _assetPreviewMsgShow('', false);
}

function _renderAssetPreviewStage(asset) {
  var stage = document.getElementById('assetPreviewStage');
  if (!stage) return;
  if (asset && asset._content_record) {
    var kind = String(asset.kind || 'article');
    var content = String(asset.content || '').trim();
    var summary = String(asset.summary || '').trim();
    var imageUrls = _contentRecordImageUrls(asset);
    var imagesHtml = imageUrls.length
      ? '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0.65rem;margin-bottom:0.9rem;">' + imageUrls.slice(0, 12).map(function(url) {
          return '<img data-content-record-image src="' + escapeAttr(url) + '" alt="" style="width:100%;max-height:280px;object-fit:contain;border-radius:8px;background:#f8fafc;">';
        }).join('') + '</div>'
      : '';
    var fileUrl = String(asset.file_url || '').trim();
    var body = content || summary || '当前记录暂无正文。';
    stage.innerHTML = '<article style="width:100%;max-width:780px;align-self:flex-start;padding:0.35rem;color:var(--text);">' +
      '<div style="display:flex;align-items:center;gap:0.45rem;margin-bottom:0.75rem;"><span class="asset-card-badge" style="background:#64748b;">' + escapeHtml(_contentRecordDisplayLabel(asset)) + '</span><strong style="font-size:1rem;">' + escapeHtml(asset.title || _contentRecordDisplayLabel(asset)) + '</strong></div>' +
      imagesHtml +
      '<div style="white-space:pre-wrap;word-break:break-word;line-height:1.75;font-size:0.9rem;">' + escapeHtml(body) + '</div>' +
      (fileUrl ? '<div style="margin-top:1rem;"><a class="btn btn-primary btn-sm" href="' + escapeAttr(fileUrl) + '" target="_blank" rel="noopener">打开文件</a></div>' : '') +
      '</article>';
    stage.querySelectorAll('img[data-content-record-image]').forEach(function(image) {
      image.addEventListener('error', function() { image.remove(); }, { once: true });
    });
    return;
  }
  var mediaType = (asset && asset.media_type) || '';
  var assetId = (asset && asset.asset_id) || '';
  var openUrl = _resolvePossiblyRelativeMediaUrl(asset && asset.open_url);
  var contentUrl = assetId ? (publishLocalBase() + '/api/assets/' + encodeURIComponent(assetId) + '/content') : '';
  if (mediaType === 'image') {
    var src = contentUrl || openUrl || '';
    stage.innerHTML = src
      ? '<img src="' + escapeAttr(src) + '" alt="" style="max-width:100%;max-height:68vh;border-radius:12px;object-fit:contain;box-shadow:0 20px 60px rgba(0,0,0,0.28);">'
      : '<div class="page-empty-card">当前图片暂无可预览地址。</div>';
    return;
  }
  if (mediaType === 'video') {
    var vsrc = contentUrl || openUrl || '';
    stage.innerHTML = vsrc
      ? '<video src="' + escapeAttr(vsrc) + '" controls playsinline preload="metadata" style="max-width:100%;max-height:68vh;border-radius:12px;background:#000;box-shadow:0 20px 60px rgba(0,0,0,0.28);"></video>'
      : '<div class="page-empty-card">当前视频暂无可预览地址。</div>';
    return;
  }
  var ext = String(asset && asset.filename || '').split('.').pop().toUpperCase();
  if (!ext || ext === String(asset && asset.filename || '').toUpperCase()) ext = 'FILE';
  stage.innerHTML =
    '<div style="width:min(420px,100%);padding:2rem 1.4rem;border-radius:18px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);text-align:center;">' +
    '<div style="width:84px;height:84px;border-radius:20px;background:rgba(255,255,255,0.08);display:flex;align-items:center;justify-content:center;margin:0 auto 1rem auto;font-size:1.1rem;font-weight:700;color:#e2e8f0;">' + escapeHtml(ext.slice(0, 4)) + '</div>' +
    '<div style="font-size:0.95rem;font-weight:600;margin-bottom:0.45rem;word-break:break-word;">' + escapeHtml(asset && asset.filename || '文档') + '</div>' +
    '<div style="font-size:0.82rem;color:var(--text-muted);line-height:1.7;">文档素材不直接内嵌预览。你可以在右侧点击下载，文件会保存到当前目录下的素材库文件夹。</div>' +
    '</div>';
}

function _openAssetPreviewModal(asset) {
  if (!asset) return;
  if (asset._content_record && asset._compact) {
    var source = String(asset.source || '').trim();
    var sourceId = String(asset.source_id || '').trim();
    var assetId = String(asset.asset_id || '');
    var base = _assetCloudBase();
    _openAssetPreviewModal(Object.assign({}, asset, {
      _compact: false,
      content: '正在加载完整内容...'
    }));
    if (!base || !source || !sourceId) return;
    var detailUrl = base + '/api/content-records/detail?source=' + encodeURIComponent(source) + '&source_id=' + encodeURIComponent(sourceId);
    fetch(detailUrl, { headers: authHeaders() })
      .then(function(r) {
        return r.json().catch(function() { return {}; }).then(function(d) {
          if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
          return d;
        });
      })
      .then(function(d) {
        if (!_assetPreviewState || String(_assetPreviewState.asset_id || '') !== assetId) return;
        var detail = _normalizeSharedContentRecord(Object.assign({}, asset, d.item || {}, { _compact: false }));
        _assetLibraryState.assetMap[assetId] = detail;
        _openAssetPreviewModal(detail);
      })
      .catch(function(err) {
        if (_assetPreviewState && String(_assetPreviewState.asset_id || '') === assetId) {
          _assetPreviewMsgShow('完整内容加载失败：' + ((err && err.message) || err), true);
        }
      });
    return;
  }
  _assetPreviewState = asset;
  var mask = document.getElementById('assetPreviewModal');
  var title = document.getElementById('assetPreviewModalTitle');
  var meta = document.getElementById('assetPreviewModalMeta');
  var prompt = document.getElementById('assetPreviewPrompt');
  var info = document.getElementById('assetPreviewInfo');
  var copyBtn = document.getElementById('assetPreviewCopyPromptBtn');
  if (title) title.textContent = (asset.title || asset.filename || _MEDIA_TYPE_LABELS[asset.media_type] || '素材预览');
  if (meta) meta.textContent = (asset._content_record ? _contentRecordDisplayLabel(asset) : (_MEDIA_TYPE_LABELS[asset.media_type] || '素材')) + ' · ' + (asset.created_at ? _formatDateTimeBeijing(asset.created_at) : '');
  if (prompt) prompt.textContent = (asset.prompt || asset.summary || '').trim() || '暂无提示词';
  if (copyBtn) copyBtn.disabled = !((asset.prompt || '').trim());
  var downloadBtn = document.getElementById('assetPreviewDownloadBtn');
  if (downloadBtn) {
    downloadBtn.style.display = asset._content_record && !String(asset.file_url || '').trim() ? 'none' : '';
    downloadBtn.textContent = asset._content_record ? '打开文件' : '下载到素材库文件夹';
  }
  if (info) {
    var rows = [
      ['素材 ID', asset.asset_id || '-'],
      ['素材类型', _MEDIA_TYPE_LABELS[asset.media_type] || (asset.media_type || '-')],
      ['文件名', asset.filename || '-'],
      ['创建时间', asset.created_at ? _formatDateTimeBeijing(asset.created_at) : '-']
    ];
    info.innerHTML = rows.map(function(row) {
      return '<div style="display:grid;grid-template-columns:86px minmax(0,1fr);gap:0.5rem;align-items:start;">' +
        '<div style="font-size:0.76rem;color:var(--text-muted);">' + escapeHtml(row[0]) + '</div>' +
        '<div style="font-size:0.84rem;line-height:1.55;word-break:break-word;">' + escapeHtml(row[1]) + '</div>' +
        '</div>';
    }).join('');
  }
  _assetPreviewMsgShow('', false);
  _renderAssetPreviewStage(asset);
  if (mask) mask.style.display = 'flex';
}

function _downloadAssetToLibrary(asset, options) {
  if (!asset || !asset.asset_id) return;
  options = options || {};
  if (asset._content_record) {
    var contentUrl = String(asset.file_url || '').trim();
    if (!contentUrl) {
      (options.usePreviewMsg ? _assetPreviewMsgShow : _assetMsgShow)('当前内容没有可下载文件。', true);
      return;
    }
    window.open(contentUrl, '_blank', 'noopener');
    return;
  }
  var previewBtn = document.getElementById('assetPreviewDownloadBtn');
  var btn = options.button || previewBtn;
  var showMsg = options.usePreviewMsg ? _assetPreviewMsgShow : _assetMsgShow;
  if (btn) {
    btn.disabled = true;
    btn.dataset.originalText = btn.dataset.originalText || btn.textContent || '下载';
    btn.textContent = '下载中...';
  }
  fetch(publishLocalBase() + '/api/assets/' + encodeURIComponent(asset.asset_id) + '/save-to-downloads', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
    body: JSON.stringify({ open_folder: true })
  })
    .then(function(r) {
      return r.json().catch(function() { return {}; }).then(function(d) {
        if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
        return d;
      });
    })
    .then(function(d) {
      var folderText = d && d.directory ? (' 位置：' + d.directory) : '';
      if (d && d.reused_existing) {
        showMsg((d.opened_folder ? '已打开本地素材位置。' : '本地素材文件已存在。') + folderText, false);
      } else {
        showMsg((d && d.opened_folder ? '已下载并打开文件夹。' : '已下载。') + folderText, false);
      }
    })
    .catch(function(err) {
      showMsg((err && err.message) || '下载失败，请稍后重试。', true);
    })
    .finally(function() {
      if (btn) {
        btn.disabled = false;
        btn.textContent = btn.dataset.originalText || '下载';
      }
    });
}

function _renderAssetCreativeGroupControls() {
  var filter = document.getElementById('assetCreativeGroupFilter');
  if (filter) {
    var current = filter.value;
    filter.innerHTML = '<option value="">全部备选组</option>' + _assetCreativeGroupsCache.map(function(row) {
      var label = row.name + (row.count ? ('（' + row.count + '张）') : '');
      return '<option value="' + escapeAttr(row.name) + '">' + escapeHtml(label) + '</option>';
    }).join('');
    if (current && _assetCreativeGroupsCache.some(function(row) { return row.name === current; })) {
      filter.value = current;
    }
  }
  var options = document.getElementById('assetCreativeGroupOptions');
  if (options) {
    options.innerHTML = _assetCreativeGroupsCache.map(function(row) {
      return '<option value="' + escapeAttr(row.name) + '"></option>';
    }).join('');
  }
}

function _showAssetCreativeGroupMsg(text, isErr) {
  var msg = document.getElementById('assetCreativeGroupMsg');
  if (!msg) return;
  msg.textContent = text || '';
  msg.className = 'msg' + (isErr ? ' err' : ' ok');
  msg.style.display = text ? 'block' : 'none';
}

function _openAssetCreativeGroupModal(assetId, currentGroup) {
  _assetCreativeGroupEditingAssetId = assetId || '';
  var modal = document.getElementById('assetCreativeGroupModal');
  var label = document.getElementById('assetCreativeGroupAssetId');
  var input = document.getElementById('assetCreativeGroupInput');
  if (label) label.textContent = assetId ? ('素材 ID：' + assetId) : '';
  if (input) {
    input.value = currentGroup || '';
    setTimeout(function() { input.focus(); input.select(); }, 30);
  }
  _showAssetCreativeGroupMsg('', false);
  _renderAssetCreativeGroupControls();
  if (modal) modal.style.display = 'flex';
}

function _closeAssetCreativeGroupModal() {
  var modal = document.getElementById('assetCreativeGroupModal');
  if (modal) modal.style.display = 'none';
  _assetCreativeGroupEditingAssetId = '';
  _showAssetCreativeGroupMsg('', false);
}

function _saveAssetCreativeGroup() {
  var aid = _assetCreativeGroupEditingAssetId;
  var input = document.getElementById('assetCreativeGroupInput');
  var save = document.getElementById('assetCreativeGroupSave');
  var groupName = (input && input.value ? input.value : '').trim();
  if (!aid) return;
  if (!groupName) {
    _showAssetCreativeGroupMsg('请填写备选组名字', true);
    return;
  }
  if (save) {
    save.disabled = true;
    save.textContent = '保存中...';
  }
  fetch(publishLocalBase() + '/api/assets/' + encodeURIComponent(aid) + '/creative-candidate-groups', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
    body: JSON.stringify({ group_name: groupName })
  })
    .then(function(r) { return r.json().catch(function() { return {}; }).then(function(d) { if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status)); return d; }); })
    .then(function() {
      _assetMsgShow('已设置创意备选组：' + groupName, false);
      _closeAssetCreativeGroupModal();
      return loadCreativeCandidateGroups();
    })
    .then(function() {
      loadAssets(_currentAssetSearchQuery(), { force: true });
    })
    .catch(function(e) {
      _showAssetCreativeGroupMsg('设置失败：' + e.message, true);
    })
    .finally(function() {
      if (save) {
        save.disabled = false;
        save.textContent = '保存';
      }
    });
}

/** 素材列表缩略图：识别 http(s)（大小写不敏感）；支持后端返回的以 / 开头的相对路径 */
function _isAbsoluteHttpUrl(s) {
  return /^https?:\/\//i.test((s || '').trim());
}

function _resolvePossiblyRelativeMediaUrl(s) {
  var t = (s || '').trim();
  if (!t) return '';
  if (_isAbsoluteHttpUrl(t)) return t;
  if (t.length >= 2 && t.charAt(0) === '/' && t.charAt(1) === '/') {
    return (window.location && window.location.protocol ? window.location.protocol : 'https:') + t;
  }
  if (t.charAt(0) === '/' && publishLocalBase()) {
    return publishLocalBase().replace(/\/$/, '') + t;
  }
  return '';
}

/** 当前页是否在回环主机上打开（与签名 URL 里的 127.0.0.1 一致时才适合直链缩略图） */
function _pageHostIsLoopback() {
  var h = (window.location && window.location.hostname) ? String(window.location.hostname).toLowerCase() : '';
  return h === 'localhost' || h === '127.0.0.1';
}

/**
 * 直链放在 img/video 上很可能加载失败：局域网访问时签名根常为 127.0.0.1；HTTPS 页拉 HTTP 会被混合内容拦截。
 */
function _thumbDirectLoadLikelyBroken(url) {
  var u = (url || '').trim();
  if (!u) return true;
  var locProto = (window.location && window.location.protocol) ? String(window.location.protocol).toLowerCase() : '';
  if (locProto === 'https:' && /^http:\/\//i.test(u)) return true;
  if (!_pageHostIsLoopback()) {
    var low = u.toLowerCase();
    if (low.indexOf('127.0.0.1') >= 0 || low.indexOf('localhost') >= 0) return true;
  }
  return false;
}

/** GET 二进制不要用 Content-Type: application/json，减少网关/中间件异常 */
function _authHeadersForMediaFetch() {
  var h = {};
  if (typeof authHeaders === 'function') {
    var ah = authHeaders();
    if (ah && ah.Authorization) h.Authorization = ah.Authorization;
    if (ah && ah['X-Installation-Id']) h['X-Installation-Id'] = ah['X-Installation-Id'];
  }
  return h;
}

function _bindVideoListThumbSeek(vid) {
  if (!vid || !vid.addEventListener) return;
  function bump() {
    try {
      var d = vid.duration;
      if (d && !isNaN(d) && d > 0) vid.currentTime = Math.min(0.15, Math.max(0.05, d * 0.02));
      else vid.currentTime = 0.1;
    } catch (e) {}
  }
  vid.addEventListener(
    'loadeddata',
    function onData() {
      vid.removeEventListener('loadeddata', onData);
      bump();
    },
    { once: true }
  );
  vid.addEventListener(
    'loadedmetadata',
    function onMeta() {
      vid.removeEventListener('loadedmetadata', onMeta);
      bump();
    },
    { once: true }
  );
}

function _pickAssetListThumbUrl(a) {
  var parts = [
    _resolvePossiblyRelativeMediaUrl(a.local_preview_url),
    _resolvePossiblyRelativeMediaUrl(a.preview_url),
    _resolvePossiblyRelativeMediaUrl(a.open_url),
    _resolvePossiblyRelativeMediaUrl(a.source_url)
  ];
  for (var i = 0; i < parts.length; i++) {
    if (parts[i]) return parts[i];
  }
  return '';
}

/**
 * 缩略图：已配置本机 API 时优先走带登录头的 /content（与本机素材文件一致，避免签名 URL 误用 127.0.0.1 导致局域网打不开）；
 * 无本机 base 或 /content 失败时再尝试安全直链（公网 CDN 等）；视频 seek 一小段以显示首帧。
 */
function _wireAssetListThumbs(container) {
  var base = publishLocalBase();
  if (!base || typeof fetch !== 'function') return;

  function loadBlobIntoMedia(el, isVideo, directFallback) {
    var aid = el.getAttribute('data-asset-id');
    if (!aid) return;
    var fb = (directFallback || el.getAttribute('data-direct-fallback') || '').trim();
    fetch(base + '/api/assets/' + encodeURIComponent(aid) + '/content', {
      headers: _authHeadersForMediaFetch()
    })
      .then(function(r) {
        if (!r.ok) throw new Error('content ' + r.status);
        return r.blob();
      })
      .then(function(blob) {
        el.src = URL.createObjectURL(blob);
        if (isVideo) _bindVideoListThumbSeek(el);
      })
      .catch(function() {
        if (fb && !_thumbDirectLoadLikelyBroken(fb)) {
          el.src = fb;
          if (isVideo) _bindVideoListThumbSeek(el);
        }
      });
  }

  function wireImg(img) {
    var initial = (img.getAttribute('data-initial-src') || '').trim();
    var preferBlobFirst = img.getAttribute('data-prefer-content') === '1';
    var hideOnError = img.getAttribute('data-hide-on-error') === '1';
    if (initial && !_thumbDirectLoadLikelyBroken(initial)) preferBlobFirst = false;
    if (preferBlobFirst) {
      loadBlobIntoMedia(img, false, '');
      return;
    }
    if (initial) {
      img.src = initial;
      img.addEventListener(
        'error',
        function onErr() {
          img.removeEventListener('error', onErr);
          if (hideOnError) {
            var wrap = img.closest('.asset-preview-wrap');
            if (wrap) wrap.remove();
            return;
          }
          loadBlobIntoMedia(img, false, '');
        },
        { once: true }
      );
    } else {
      loadBlobIntoMedia(img, false, '');
    }
  }

  container.querySelectorAll('img.asset-list-thumb').forEach(function(img) {
    wireImg(img);
  });

  container.querySelectorAll('video.asset-list-thumb-video').forEach(function(vid) {
    var initial = (vid.getAttribute('data-initial-src') || '').trim();
    var preferBlobFirst = vid.getAttribute('data-prefer-content') === '1';
    if (initial && !_thumbDirectLoadLikelyBroken(initial)) preferBlobFirst = false;
    if (preferBlobFirst) {
      loadBlobIntoMedia(vid, true, '');
      return;
    }
    if (initial) {
      vid.src = initial;
      _bindVideoListThumbSeek(vid);
      vid.addEventListener(
        'error',
        function onErr() {
          vid.removeEventListener('error', onErr);
          loadBlobIntoMedia(vid, true, '');
        },
        { once: true }
      );
    } else {
      loadBlobIntoMedia(vid, true, '');
    }
  });
}

function _bindRenderedAssetListInteractions(el, assets) {
  if (!el) return;
  var assetMap = {};
  (assets || []).forEach(function(asset) {
    if (asset && asset.asset_id) assetMap[asset.asset_id] = asset;
  });
  _bindAssetContentActions(el, assetMap);
  el.querySelectorAll('button[data-preview-asset]').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var aid = btn.getAttribute('data-preview-asset');
      _openAssetPreviewModal(assetMap[aid]);
    });
  });
  el.querySelectorAll('button[data-copy-asset-prompt]').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var aid = btn.getAttribute('data-copy-asset-prompt');
      _copyAssetPrompt(assetMap[aid] && assetMap[aid].prompt);
      _assetMsgShow((assetMap[aid] && assetMap[aid].prompt) ? '提示词已复制。' : '当前素材没有提示词可复制。', !(assetMap[aid] && assetMap[aid].prompt));
    });
  });
  el.querySelectorAll('button[data-download-asset]').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var aid = btn.getAttribute('data-download-asset');
      _openAssetPreviewModal(assetMap[aid]);
      _downloadAssetToLibrary(assetMap[aid]);
    });
  });
  el.querySelectorAll('button[data-creative-candidate]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var aid = btn.getAttribute('data-creative-candidate');
      var currentGroup = btn.getAttribute('data-current-creative-group') || '';
      _openAssetCreativeGroupModal(aid, currentGroup);
    });
  });
  el.querySelectorAll('button[data-use-as-attach]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var aid = btn.getAttribute('data-use-as-attach');
      var mtype = btn.getAttribute('data-attach-media-type') || 'image';
      var hasUrl = btn.getAttribute('data-attach-has-url') === '1';
      if (typeof addChatAttachment === 'function') {
        addChatAttachment(aid, mtype, hasUrl);
        var chatNav = document.querySelector('.nav-left-item[data-view="chat"]');
        if (chatNav) chatNav.click();
        if (typeof _assetMsgShow === 'function') _assetMsgShow('已添加为附图，输入内容后发送即可带图生成', false);
        else alert('已添加为附图，请在输入框输入内容后发送');
      }
    });
  });
  el.querySelectorAll('button[data-delete-asset]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var aid = btn.getAttribute('data-delete-asset');
      if (!confirm('确定删除此素材？')) return;
      fetch(publishLocalBase() + '/api/assets/' + aid, { method: 'DELETE', headers: authHeaders() })
        .then(function() {
          _assetSetSelected(aid, false);
          _updateAssetBulkUi();
          return loadCreativeCandidateGroups();
        })
        .then(function() {
          loadAssets(_currentAssetSearchQuery(), { force: true });
        })
        .catch(function() { alert('删除失败'); });
    });
  });
  _wireAssetListThumbs(el);
  el.querySelectorAll('.asset-preview-wrap').forEach(function(wrap) {
    wrap.addEventListener('click', function() {
      var aid = wrap.getAttribute('data-asset-id');
      if (aid && assetMap[aid]) {
        _openAssetPreviewModal(assetMap[aid]);
        return;
      }
      var url = wrap.getAttribute('data-open-url');
      if (!url || !_isAbsoluteHttpUrl(url)) {
        alert('无法在新窗口打开：当前无公网 http(s) 链接。缩略图已从本机加载时，可在素材目录或对话附图中使用。');
        return;
      }
      window.open(url, '_blank');
    });
  });
}

function loadAssets(query, options) {
  options = options || {};
  var el = document.getElementById('assetList');
  if (!el) return;
  var mediaType = (document.getElementById('assetTypeFilter') || {}).value || '';
  var creativeGroup = _currentAssetCreativeGroupFilter();
  var origin = _currentAssetOriginFilter();
  var cacheKey = _assetListCacheKey(query);
  var hasRenderedCurrentKey = el.getAttribute('data-asset-cache-key') === cacheKey && !!el.innerHTML.trim();
  var cached = _assetListCache[cacheKey];
  _assetActiveCacheKey = cacheKey;
  if (!hasRenderedCurrentKey && cached && cached.html) {
    el.innerHTML = cached.html;
    el.setAttribute('data-asset-cache-key', cacheKey);
    _bindRenderedAssetListInteractions(el, cached.assets || []);
    hasRenderedCurrentKey = true;
  }
  if (!hasRenderedCurrentKey) {
    el.innerHTML = _assetListLoadingHtml();
  }
  var url = publishLocalBase() + '/api/assets?limit=50';
  if (origin) url += '&origin=' + encodeURIComponent(origin);
  if (mediaType) url += '&media_type=' + encodeURIComponent(mediaType);
  if (creativeGroup) url += '&creative_group=' + encodeURIComponent(creativeGroup);
  if (query) url += '&q=' + encodeURIComponent(query);
  fetch(url, { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var assets = (d && Array.isArray(d.assets)) ? d.assets : [];
      assets = assets.filter(function(a) {
        var itemOrigin = a && a.asset_origin === 'user_upload' ? 'user_upload' : 'generated';
        return itemOrigin === origin;
      });
      var nextFingerprint = _assetListFingerprint(assets);
      var prev = _assetListCache[cacheKey];
      if (!options.force && hasRenderedCurrentKey && prev && prev.fingerprint === nextFingerprint) {
        prev.fetchedAt = Date.now();
        return;
      }
      _assetListCache[cacheKey] = {
        assets: assets,
        fingerprint: nextFingerprint,
        html: '',
        fetchedAt: Date.now()
      };
      if (_assetActiveCacheKey !== cacheKey) return;
      el.setAttribute('data-asset-cache-key', cacheKey);
      if (!assets.length) {
        el.innerHTML = _assetListEmptyHtml();
        _assetListCache[cacheKey].html = el.innerHTML;
        return;
      }
      var assetMap = {};
      el.innerHTML = assets.map(function(a) {
        assetMap[a.asset_id] = a;
        var isImage = a.media_type === 'image';
        var isVideo = a.media_type === 'video';
        var isDocument = a.media_type === 'document';
        var hasUrl = _isAbsoluteHttpUrl(a.source_url);
        var thumbUrl = _pickAssetListThumbUrl(a);
        var openUrl = _resolvePossiblyRelativeMediaUrl(a.open_url);
        if (!openUrl && hasUrl) openUrl = _resolvePossiblyRelativeMediaUrl(a.source_url);
        if (!openUrl) openUrl = thumbUrl || '';
        var localBase = publishLocalBase();
        var blobOk = !!(localBase && a.asset_id);
        var canDirectThumb = _isAbsoluteHttpUrl(thumbUrl) || (!!thumbUrl && thumbUrl.charAt(0) === '/');
        var showThumb = (isImage || isVideo) && (canDirectThumb || blobOk);
        var safeDirectFallback = (canDirectThumb && !_thumbDirectLoadLikelyBroken(thumbUrl)) ? thumbUrl : '';
        var preview = '';
        var titleHint = openUrl && _isAbsoluteHttpUrl(openUrl)
          ? '点击在新窗口打开（优先公网可分享链接）'
          : blobOk
            ? '预览由本机素材接口加载；点击尝试打开公网链'
            : '无可用缩略图';
        var wrapAttrs = 'data-asset-id="' + escapeAttr(a.asset_id) + '" data-media-type="' + escapeAttr(a.media_type || '') + '" data-open-url="' + escapeAttr(openUrl || '') + '" style="margin:0.5rem 0;cursor:pointer;" title="' + titleHint + '"';
        if (isImage) {
          if (showThumb) {
            var imgPreferContent = blobOk ? '1' : '0';
            var imgInitialSrc = blobOk ? '' : safeDirectFallback;
            preview =
              '<div class="asset-preview-wrap" ' +
              wrapAttrs +
              '><img class="asset-list-thumb" data-asset-id="' +
              escapeAttr(a.asset_id) +
              '" data-prefer-content="' +
              imgPreferContent +
              '" data-direct-fallback="' +
              escapeAttr(safeDirectFallback) +
              '" data-initial-src="' +
              escapeAttr(imgInitialSrc) +
              '" alt="" style="max-width:160px;max-height:120px;border-radius:6px;object-fit:cover;pointer-events:none;"></div>';
          } else {
            preview = '<div class="asset-preview-wrap" ' + wrapAttrs + '><div style="max-width:160px;max-height:120px;border-radius:6px;background:rgba(255,255,255,0.08);display:flex;align-items:center;justify-content:center;font-size:0.72rem;color:var(--text-muted);padding:0.5rem;">无缩略图<br>（未配置本机 API 或素材无文件）</div></div>';
          }
        } else if (isVideo) {
          if (showThumb) {
            var vidPreferContent = blobOk ? '1' : '0';
            var vidInitialSrc = blobOk ? '' : safeDirectFallback;
            preview =
              '<div class="asset-preview-wrap" ' +
              wrapAttrs +
              '><video class="asset-list-thumb-video" data-asset-id="' +
              escapeAttr(a.asset_id) +
              '" data-prefer-content="' +
              vidPreferContent +
              '" data-direct-fallback="' +
              escapeAttr(safeDirectFallback) +
              '" data-initial-src="' +
              escapeAttr(vidInitialSrc) +
              '" style="max-width:160px;max-height:120px;border-radius:6px;pointer-events:none;" muted preload="metadata" playsinline></video></div>';
          } else {
            preview = '<div class="asset-preview-wrap" ' + wrapAttrs + '><div style="max-width:160px;max-height:120px;border-radius:6px;background:rgba(255,255,255,0.08);display:flex;align-items:center;justify-content:center;font-size:0.72rem;color:var(--text-muted);padding:0.5rem;">无缩略图<br>（未配置本机 API 或素材无文件）</div></div>';
          }
        } else if (isDocument) {
          var docExt = String(a.filename || '').split('.').pop().toUpperCase();
          if (!docExt || docExt === String(a.filename || '').toUpperCase()) docExt = 'DOC';
          if (docExt === 'PPTX') docExt = 'PPT';
          preview =
            '<div class="asset-preview-wrap asset-document-preview" ' +
            wrapAttrs +
            '><div class="asset-document-preview-inner">' +
            '<div class="asset-document-icon">' + escapeHtml(docExt.slice(0, 4)) + '</div>' +
            '<div class="asset-document-name">' + escapeHtml(a.filename || 'document') + '</div>' +
            '<div class="asset-document-meta">文档文件</div>' +
            '</div></div>';
        } else {
          preview = '<div class="asset-preview-wrap asset-document-preview" ' + wrapAttrs + '><div class="asset-document-preview-inner"><div class="asset-document-icon">FILE</div><div class="asset-document-name">' + escapeHtml(a.filename || a.media_type || 'file') + '</div><div class="asset-document-meta">' + escapeHtml(a.media_type || '文件') + '</div></div></div>';
        }
        var typeLabel = _MEDIA_TYPE_LABELS[a.media_type] || a.media_type;
        var originLabel = a.asset_origin === 'user_upload' ? '用户上传' : '生成素材';
        var originClass = a.asset_origin === 'user_upload' ? ' is-upload' : ' is-generated';
        var currentGroup = (a.creative_candidate_group || (Array.isArray(a.creative_candidate_groups) && a.creative_candidate_groups[0]) || '').trim();
        var groupHtml = currentGroup ? '<div class="card-tags"><span class="tag">备选：' + escapeHtml(currentGroup) + '</span></div>' : '';
        var size = a.file_size ? (a.file_size > 1048576 ? (a.file_size / 1048576).toFixed(1) + ' MB' : (a.file_size / 1024).toFixed(1) + ' KB') : '';
        var useAsAttachBtn = (isImage || isVideo) ? '<button type="button" class="btn btn-primary btn-sm" data-use-as-attach="' + escapeAttr(a.asset_id) + '" data-attach-media-type="' + escapeAttr(a.media_type || '') + '" data-attach-has-url="' + (hasUrl ? '1' : '0') + '">用作附图</button>' : '';
        var previewBtn = '<button type="button" class="btn btn-ghost btn-sm" data-preview-asset="' + escapeAttr(a.asset_id) + '">查看结果</button>';
        var copyPromptBtn = '<button type="button" class="btn btn-ghost btn-sm" data-copy-asset-prompt="' + escapeAttr(a.asset_id) + '"' + (((a.prompt || '').trim()) ? '' : ' disabled') + '>复制提示词</button>';
        var downloadBtn = '<button type="button" class="btn btn-ghost btn-sm" data-download-asset="' + escapeAttr(a.asset_id) + '">下载</button>';
        var candidateBtn = isImage ? '<button type="button" class="btn btn-ghost btn-sm" data-creative-candidate="' + escapeAttr(a.asset_id) + '" data-current-creative-group="' + escapeAttr(currentGroup) + '">设为创意备选</button>' : '';
        var actionMenu = _assetContentActionMenuHtml(a);
        var deleteBtn = '<button type="button" class="btn btn-ghost btn-sm" data-delete-asset="' + escapeAttr(a.asset_id) + '">删除</button>';
        var badgeColor = isImage ? '#6366f1' : isVideo ? '#f59e0b' : isDocument ? '#64748b' : '#888';
        return '<div class="skill-store-card asset-card">' +
          '<div class="card-label"><span style="display:inline-flex;align-items:center;gap:0.35rem;flex-wrap:wrap;"><span class="asset-card-badge" style="background:' + badgeColor + ';">' + escapeHtml(typeLabel) + '</span><span class="asset-origin-badge' + originClass + '">' + escapeHtml(originLabel) + '</span></span><span class="asset-card-size">' + escapeHtml(size) + '</span></div>' +
          preview +
          '<div class="card-desc asset-card-desc-clamp" style="font-size:0.78rem;">' + escapeHtml(a.prompt || a.filename) + '</div>' +
          groupHtml +
          '<div class="card-desc" style="font-size:0.72rem;color:var(--text-muted);">ID: ' + escapeHtml(a.asset_id) + ' · ' + escapeHtml(_formatDateTimeBeijing(a.created_at)) + '</div>' +
          '<div class="card-actions">' + previewBtn + ' ' + copyPromptBtn + ' ' + downloadBtn + ' ' + useAsAttachBtn + ' ' + candidateBtn + ' ' + actionMenu + ' ' + deleteBtn + '</div></div>';
      }).join('');
      _assetListCache[cacheKey].html = el.innerHTML;
      _bindRenderedAssetListInteractions(el, assets);
    })
    .catch(function() {
      if (_assetActiveCacheKey !== cacheKey) return;
      if (hasRenderedCurrentKey) {
        _assetMsgShow('素材列表刷新失败，已显示当前列表', true);
        return;
      }
      el.innerHTML = '<div class="page-empty-card msg err">加载失败</div>';
    });
}

var _ASSET_PAGE_SIZE = 20;
var _ASSET_PROGRESSIVE_FIRST_BATCH = 4;
var _ASSET_PROGRESSIVE_BATCH = 4;
var _assetLibraryState = {
  offset: 0,
  total: 0,
  query: '',
  mediaType: '',
  creativeGroup: '',
  origin: '',
  loading: false,
  assetMap: {}
};
var _assetLibraryLoadSeq = 0;
var _assetLibraryRenderTimer = 0;
var _assetSelectedAssets = {};
var _assetBulkMode = false;
var _assetUserUploadSyncPromise = null;
var _assetUserUploadSyncAt = 0;
var _ASSET_USER_UPLOAD_SYNC_TTL_MS = 60000;

function _syncUserUploadAssetsAfterRender(snapshot, options) {
  options = options || {};
  if (!snapshot || snapshot.origin !== 'user_upload') return Promise.resolve(null);
  var now = Date.now();
  if (_assetUserUploadSyncPromise) return _assetUserUploadSyncPromise;
  if (!options.force && now - _assetUserUploadSyncAt < _ASSET_USER_UPLOAD_SYNC_TTL_MS) {
    return Promise.resolve(null);
  }
  _assetUserUploadSyncAt = now;
  var url = publishLocalBase() + '/api/assets/sync-user-uploads';
  if (snapshot.mediaType) url += '?media_type=' + encodeURIComponent(snapshot.mediaType);
  _assetUserUploadSyncPromise = fetch(url, {
    method: 'POST',
    headers: authHeaders()
  })
    .then(function(r) {
      return r.json().catch(function() { return {}; }).then(function(d) {
        if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
        return d;
      });
    })
    .then(function(d) {
      if (Number(d && d.synced || 0) > 0
          && _currentAssetOriginFilter() === 'user_upload'
          && _assetLibraryState.query === snapshot.query
          && _assetLibraryState.mediaType === snapshot.mediaType) {
        loadAssets(snapshot.query, { skipCloudSync: true });
      }
      return d;
    })
    .catch(function(err) {
      if (options.showError) {
        _assetMsgShow('云端素材同步失败：' + ((err && err.message) || err), true);
      }
      return null;
    })
    .finally(function() {
      _assetUserUploadSyncPromise = null;
    });
  return _assetUserUploadSyncPromise;
}

function _assetSelectedIds() {
  return Object.keys(_assetSelectedAssets).filter(function(aid) { return !!_assetSelectedAssets[aid]; });
}

function _assetIsSelected(assetId) {
  return !!_assetSelectedAssets[String(assetId || '')];
}

function _assetSetSelected(assetId, selected) {
  var aid = String(assetId || '').trim();
  if (!aid) return;
  if (selected) _assetSelectedAssets[aid] = true;
  else delete _assetSelectedAssets[aid];
}

function _assetClearSelection() {
  _assetSelectedAssets = {};
  _updateAssetBulkUi();
}

function _setAssetBulkMode(enabled) {
  _assetBulkMode = !!enabled;
  var root = document.getElementById('content-assets');
  if (root) root.classList.toggle('asset-bulk-mode', _assetBulkMode);
  if (!_assetBulkMode) _assetSelectedAssets = {};
  _updateAssetBulkUi();
}

function _loadedAssetIds() {
  var map = (_assetLibraryState && _assetLibraryState.assetMap) || {};
  return Object.keys(map).filter(function(aid) { return !!map[aid]; });
}

function _updateAssetBulkUi() {
  var loadedIds = _loadedAssetIds();
  var selectedIds = _assetSelectedIds().filter(function(aid) {
    return loadedIds.indexOf(aid) >= 0;
  });
  var countEl = document.getElementById('assetSelectedCount');
  var selectLoaded = document.getElementById('assetSelectLoaded');
  var bulkConfirmBtn = document.getElementById('assetBulkConfirmBtn');
  if (countEl) countEl.textContent = '已选 ' + selectedIds.length + ' 个';
  if (bulkConfirmBtn) bulkConfirmBtn.disabled = selectedIds.length < 1;
  if (selectLoaded) {
    selectLoaded.checked = loadedIds.length > 0 && selectedIds.length === loadedIds.length;
    selectLoaded.indeterminate = selectedIds.length > 0 && selectedIds.length < loadedIds.length;
    selectLoaded.disabled = loadedIds.length < 1;
  }
  document.querySelectorAll('input[data-select-asset]').forEach(function(input) {
    var aid = input.getAttribute('data-select-asset') || '';
    input.checked = _assetIsSelected(aid);
  });
}

function _selectLoadedAssets(checked) {
  _loadedAssetIds().forEach(function(aid) {
    _assetSetSelected(aid, checked);
  });
  _updateAssetBulkUi();
}

function _bulkDeleteSelectedAssets() {
  if (!_assetBulkMode) {
    _setAssetBulkMode(true);
    return;
  }
  var selectedIds = _assetSelectedIds().filter(function(aid) {
    return _assetLibraryState.assetMap && _assetLibraryState.assetMap[aid];
  });
  if (!selectedIds.length) {
    _assetMsgShow('请先选择要删除的素材。', true);
    return;
  }
  if (!confirm('确定删除选中的 ' + selectedIds.length + ' 个素材？本地文件也会一起删除。')) return;
  var btn = document.getElementById('assetBulkConfirmBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '删除中...';
  }
  fetch(publishLocalBase() + '/api/assets/bulk-delete', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
    body: JSON.stringify({ asset_ids: selectedIds })
  })
    .then(function(r) {
      return r.json().catch(function() { return {}; }).then(function(d) {
        if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
        return d;
      });
    })
    .then(function(d) {
      _setAssetBulkMode(false);
      _assetMsgShow('已删除 ' + ((d && d.deleted) || selectedIds.length) + ' 个素材。', false);
      return loadCreativeCandidateGroups();
    })
    .then(function() {
      loadAssets(_currentAssetSearchQuery(), { force: true, skipCloudSync: true });
    })
    .catch(function(e) {
      _assetMsgShow('批量删除失败：' + ((e && e.message) || e), true);
    })
    .finally(function() {
      if (btn) {
        btn.textContent = '确定删除';
        _updateAssetBulkUi();
      }
    });
}

function _snapshotAssetQuery(query) {
  return {
    query: query || '',
    mediaType: (document.getElementById('assetTypeFilter') || {}).value || '',
    creativeGroup: _currentAssetCreativeGroupFilter(),
    origin: _currentAssetOriginFilter()
  };
}

function _ensureAssetLoadMoreWrap() {
  var el = document.getElementById('assetList');
  if (!el) return null;
  var wrap = document.getElementById('assetLoadMoreWrap');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.id = 'assetLoadMoreWrap';
    wrap.style.display = 'none';
    wrap.style.marginTop = '0.9rem';
    el.insertAdjacentElement('afterend', wrap);
  }
  return wrap;
}

function _setAssetLoadMoreState(visible, loading) {
  var wrap = _ensureAssetLoadMoreWrap();
  if (!wrap) return;
  if (!visible) {
    wrap.style.display = 'none';
    wrap.innerHTML = '';
    return;
  }
  var shown = _assetLibraryState.offset || 0;
  var total = _assetLibraryState.total || 0;
  wrap.style.display = 'flex';
  wrap.style.justifyContent = 'center';
  wrap.innerHTML =
    '<div style="display:flex;flex-direction:column;align-items:center;gap:0.45rem;">' +
      '<div class="meta" style="font-size:0.78rem;color:var(--text-muted);">已加载 ' + escapeHtml(String(shown)) + ' / ' + escapeHtml(String(total)) + '</div>' +
      '<button type="button" id="assetLoadMoreBtn" class="btn btn-ghost">' + (loading ? '加载中...' : '查看更多') + '</button>' +
    '</div>';
  var btn = document.getElementById('assetLoadMoreBtn');
  if (btn) {
    btn.disabled = !!loading;
    if (!btn._assetLibraryBound) {
      btn._assetLibraryBound = true;
      btn.addEventListener('click', function() {
        if (_assetLibraryState.loading) return;
        loadAssets(_assetLibraryState.query, { append: true });
      });
    }
  }
}

function _bindAssetCardActions(container) {
  if (!container) return;
  _bindAssetContentActions(container, _assetLibraryState.assetMap);
  container.querySelectorAll('input[data-select-asset]').forEach(function(input) {
    if (input._assetLibraryBound) return;
    input._assetLibraryBound = true;
    input.addEventListener('click', function(e) {
      e.stopPropagation();
    });
    input.addEventListener('change', function(e) {
      e.stopPropagation();
      _assetSetSelected(input.getAttribute('data-select-asset'), input.checked);
      _updateAssetBulkUi();
    });
  });
  container.querySelectorAll('button[data-preview-asset]').forEach(function(btn) {
    if (btn._assetLibraryBound) return;
    btn._assetLibraryBound = true;
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var aid = btn.getAttribute('data-preview-asset');
      _openAssetPreviewModal(_assetLibraryState.assetMap[aid]);
    });
  });
  container.querySelectorAll('button[data-copy-asset-prompt]').forEach(function(btn) {
    if (btn._assetLibraryBound) return;
    btn._assetLibraryBound = true;
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var aid = btn.getAttribute('data-copy-asset-prompt');
      var asset = _assetLibraryState.assetMap[aid];
      var value = asset && asset._content_record ? _assetContentText(asset) : (asset && asset.prompt);
      _copyAssetPrompt(value);
      _assetMsgShow(value ? (asset && asset._content_record ? '文案已复制。' : '提示词已复制。') : (asset && asset._content_record ? '当前内容没有文案可复制。' : '当前素材没有提示词可复制。'), !value);
    });
  });
  container.querySelectorAll('button[data-download-asset]').forEach(function(btn) {
    if (btn._assetLibraryBound) return;
    btn._assetLibraryBound = true;
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var aid = btn.getAttribute('data-download-asset');
      var asset = _assetLibraryState.assetMap[aid];
      _downloadAssetToLibrary(asset, { button: btn, usePreviewMsg: false });
    });
  });
  container.querySelectorAll('button[data-creative-candidate]').forEach(function(btn) {
    if (btn._assetLibraryBound) return;
    btn._assetLibraryBound = true;
    btn.addEventListener('click', function() {
      var aid = btn.getAttribute('data-creative-candidate');
      var currentGroup = btn.getAttribute('data-current-creative-group') || '';
      _openAssetCreativeGroupModal(aid, currentGroup);
    });
  });
  container.querySelectorAll('button[data-use-as-attach]').forEach(function(btn) {
    if (btn._assetLibraryBound) return;
    btn._assetLibraryBound = true;
    btn.addEventListener('click', function() {
      var aid = btn.getAttribute('data-use-as-attach');
      var mtype = btn.getAttribute('data-attach-media-type') || 'image';
      var hasUrl = btn.getAttribute('data-attach-has-url') === '1';
      if (typeof addChatAttachment === 'function') {
        addChatAttachment(aid, mtype, hasUrl);
        var chatNav = document.querySelector('.nav-left-item[data-view="chat"]');
        if (chatNav) chatNav.click();
        if (typeof _assetMsgShow === 'function') _assetMsgShow('已添加为附图，输入内容后发送即可带图生成。', false);
        else alert('已添加为附图，请在输入框输入内容后发送。');
      }
    });
  });
  container.querySelectorAll('button[data-delete-asset]').forEach(function(btn) {
    if (btn._assetLibraryBound) return;
    btn._assetLibraryBound = true;
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var aid = btn.getAttribute('data-delete-asset');
      if (!confirm('确定删除此素材？')) return;
      btn.disabled = true;
      var originalText = btn.textContent;
      btn.textContent = '删除中...';
      fetch(publishLocalBase() + '/api/assets/' + encodeURIComponent(aid), { method: 'DELETE', headers: authHeaders() })
        .then(function(r) {
          return r.json().catch(function() { return {}; }).then(function(d) {
            if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
            return d;
          });
        })
        .then(function() {
          delete _assetLibraryState.assetMap[aid];
          _assetSetSelected(aid, false);
          return loadCreativeCandidateGroups();
        })
        .then(function() {
          _assetMsgShow('素材已删除。', false);
          loadAssets(_currentAssetSearchQuery(), { force: true, skipCloudSync: true });
        })
        .catch(function(err) {
          _assetMsgShow('删除失败：' + ((err && err.message) || err), true);
        })
        .finally(function() {
          btn.disabled = false;
          btn.textContent = originalText || '删除';
        });
    });
  });
  container.querySelectorAll('button[data-delete-content-record]').forEach(function(btn) {
    if (btn._assetLibraryBound) return;
    btn._assetLibraryBound = true;
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var aid = btn.getAttribute('data-delete-content-record');
      var asset = _assetLibraryState.assetMap[aid];
      if (!asset || !confirm('确定删除这条内容记录？')) return;
      var base = _assetCloudBase();
      if (!base) return _assetMsgShow('未配置云端 API_BASE。', true);
      btn.disabled = true;
      var originalText = btn.textContent;
      btn.textContent = '删除中...';
      var url = base + '/api/content-records?source=' + encodeURIComponent(asset.source || '') + '&source_id=' + encodeURIComponent(asset.source_id || '');
      fetch(url, { method: 'DELETE', headers: authHeaders() })
        .then(function(r) {
          return r.json().catch(function() { return {}; }).then(function(d) {
            if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
            return d;
          });
        })
        .then(function() {
          _assetMsgShow('内容记录已删除。', false);
          loadAssets(_currentAssetSearchQuery(), { force: true, skipCloudSync: true });
        })
        .catch(function(err) {
          _assetMsgShow('删除失败：' + ((err && err.message) || err), true);
        })
        .finally(function() {
          btn.disabled = false;
          btn.textContent = originalText || '删除';
        });
    });
  });
  container.querySelectorAll('.asset-preview-wrap').forEach(function(wrap) {
    if (wrap._assetLibraryBound) return;
    wrap._assetLibraryBound = true;
    wrap.addEventListener('click', function() {
      var aid = wrap.getAttribute('data-asset-id');
      _openAssetPreviewModal(_assetLibraryState.assetMap[aid]);
    });
  });
}

function _renderAssetCards(container, assets, append) {
  if (!container) return;
  var html = assets.map(function(a) {
    _assetLibraryState.assetMap[a.asset_id] = a;
    var isContentRecord = !!a._content_record;
    var isImage = a.media_type === 'image';
    var isVideo = a.media_type === 'video';
    var isDocument = a.media_type === 'document';
    var contentImages = isContentRecord ? _contentRecordImageUrls(a) : [];
    var hasUrl = _isAbsoluteHttpUrl(a.source_url);
    var thumbUrl = _pickAssetListThumbUrl(a);
    var openUrl = _resolvePossiblyRelativeMediaUrl(a.open_url);
    if (!openUrl && hasUrl) openUrl = _resolvePossiblyRelativeMediaUrl(a.source_url);
    if (!openUrl) openUrl = thumbUrl || '';
    var localBase = publishLocalBase();
    var blobOk = !!(localBase && a.asset_id && !isContentRecord);
    var canDirectThumb = _isAbsoluteHttpUrl(thumbUrl) || (!!thumbUrl && thumbUrl.charAt(0) === '/');
    var showThumb = (isImage || isVideo) && (canDirectThumb || blobOk);
    var safeDirectFallback = (canDirectThumb && !_thumbDirectLoadLikelyBroken(thumbUrl)) ? thumbUrl : '';
    var preview = '';
    var titleHint = openUrl && _isAbsoluteHttpUrl(openUrl)
      ? '点击在新窗口打开（优先公网可分享链接）'
      : blobOk
        ? '预览由本机素材接口加载；点击后会优先尝试打开可访问链接'
        : '无可用缩略图';
    var wrapAttrs = 'data-asset-id="' + escapeAttr(a.asset_id) + '" data-media-type="' + escapeAttr(a.media_type || '') + '" data-open-url="' + escapeAttr(openUrl || '') + '" style="margin:0.5rem 0;cursor:pointer;" title="' + titleHint + '"';
    if (isImage) {
      if (showThumb) {
        var imgPreferContent = blobOk ? '1' : '0';
        var imgInitialSrc = blobOk ? '' : safeDirectFallback;
        preview =
          '<div class="asset-preview-wrap" ' + wrapAttrs +
          '><img class="asset-list-thumb" data-asset-id="' + escapeAttr(a.asset_id) +
          '" data-prefer-content="' + imgPreferContent +
          '" data-direct-fallback="' + escapeAttr(safeDirectFallback) +
          '" data-initial-src="' + escapeAttr(imgInitialSrc) +
          '" alt="" style="max-width:160px;max-height:120px;border-radius:6px;object-fit:cover;pointer-events:none;"></div>';
      } else {
        preview = '<div class="asset-preview-wrap" ' + wrapAttrs + '><div style="max-width:160px;max-height:120px;border-radius:6px;background:rgba(255,255,255,0.08);display:flex;align-items:center;justify-content:center;font-size:0.72rem;color:var(--text-muted);padding:0.5rem;">无缩略图<br>（未配置本机 API 或素材无文件）</div></div>';
      }
    } else if (isVideo) {
      if (showThumb) {
        var vidPreferContent = blobOk ? '1' : '0';
        var vidInitialSrc = blobOk ? '' : safeDirectFallback;
        preview =
          '<div class="asset-preview-wrap" ' + wrapAttrs +
          '><video class="asset-list-thumb-video" data-asset-id="' + escapeAttr(a.asset_id) +
          '" data-prefer-content="' + vidPreferContent +
          '" data-direct-fallback="' + escapeAttr(safeDirectFallback) +
          '" data-initial-src="' + escapeAttr(vidInitialSrc) +
          '" style="max-width:160px;max-height:120px;border-radius:6px;pointer-events:none;" muted preload="metadata" playsinline></video></div>';
      } else {
        preview = '<div class="asset-preview-wrap" ' + wrapAttrs + '><div style="max-width:160px;max-height:120px;border-radius:6px;background:rgba(255,255,255,0.08);display:flex;align-items:center;justify-content:center;font-size:0.72rem;color:var(--text-muted);padding:0.5rem;">无缩略图<br>（未配置本机 API 或素材无文件）</div></div>';
      }
    } else if (isDocument && isContentRecord && contentImages.length) {
      var coverUrl = contentImages[0];
      var countBadge = contentImages.length > 1 ? '<span class="asset-content-image-count">' + escapeHtml(String(contentImages.length)) + ' 张</span>' : '';
      preview = '<div class="asset-preview-wrap" ' + wrapAttrs + '><img class="asset-list-thumb" data-hide-on-error="1" data-initial-src="' + escapeAttr(coverUrl) + '" alt="" style="max-width:160px;max-height:120px;border-radius:6px;object-fit:contain;pointer-events:none;">' + countBadge + '</div>';
    } else if (isDocument) {
      var docExt = String(a.filename || '').split('.').pop().toUpperCase();
      if (isContentRecord) docExt = _contentRecordDisplayLabel(a);
      else if (!docExt || docExt === String(a.filename || '').toUpperCase()) docExt = 'DOC';
      if (docExt === 'PPTX') docExt = 'PPT';
      preview =
        '<div class="asset-preview-wrap asset-document-preview" ' + wrapAttrs + '><div class="asset-document-preview-inner">' +
        '<div class="asset-document-icon">' + escapeHtml(docExt.slice(0, 4)) + '</div>' +
        '<div class="asset-document-name">' + escapeHtml(a.title || a.filename || 'document') + '</div>' +
        '<div class="asset-document-meta">' + escapeHtml(isContentRecord ? _contentRecordDisplayLabel(a) : '文档文件') + '</div>' +
        '</div></div>';
    } else {
      preview = '<div class="asset-preview-wrap asset-document-preview" ' + wrapAttrs + '><div class="asset-document-preview-inner"><div class="asset-document-icon">FILE</div><div class="asset-document-name">' + escapeHtml(a.filename || a.media_type || 'file') + '</div><div class="asset-document-meta">' + escapeHtml(a.media_type || '文件') + '</div></div></div>';
    }
    var typeLabel = isContentRecord ? _contentRecordDisplayLabel(a) : (_MEDIA_TYPE_LABELS[a.media_type] || a.media_type);
    var originLabel = a.asset_origin === 'user_upload' ? '用户上传' : '内容记录';
    var originClass = a.asset_origin === 'user_upload' ? ' is-upload' : ' is-generated';
    var currentGroup = (a.creative_candidate_group || (Array.isArray(a.creative_candidate_groups) && a.creative_candidate_groups[0]) || '').trim();
    var groupHtml = currentGroup ? '<div class="card-tags"><span class="tag">备选：' + escapeHtml(currentGroup) + '</span></div>' : '';
    var size = a.file_size ? (a.file_size > 1048576 ? (a.file_size / 1048576).toFixed(1) + ' MB' : (a.file_size / 1024).toFixed(1) + ' KB') : '';
    var selectHtml = isContentRecord ? '' : '<label class="asset-card-select" onclick="event.stopPropagation();"><input type="checkbox" data-select-asset="' + escapeAttr(a.asset_id) + '"' + (_assetIsSelected(a.asset_id) ? ' checked' : '') + '><span>选择</span></label>';
    var useAsAttachBtn = !isContentRecord && (isImage || isVideo) ? '<button type="button" class="btn btn-primary btn-sm" data-use-as-attach="' + escapeAttr(a.asset_id) + '" data-attach-media-type="' + escapeAttr(a.media_type || '') + '" data-attach-has-url="' + (hasUrl ? '1' : '0') + '">用作附图</button>' : '';
    var previewBtn = '<button type="button" class="btn btn-ghost btn-sm" data-preview-asset="' + escapeAttr(a.asset_id) + '">查看结果</button>';
    var copyValue = isContentRecord ? _assetContentText(a) : String(a.prompt || '').trim();
    var copyLabel = isContentRecord && ['article', 'wechat_article'].indexOf(String(a.kind || '').toLowerCase()) >= 0 ? '复制文案' : (isContentRecord ? '复制摘要' : '复制提示词');
    var copyPromptBtn = '<button type="button" class="btn btn-ghost btn-sm" data-copy-asset-prompt="' + escapeAttr(a.asset_id) + '"' + (copyValue ? '' : ' disabled') + '>' + copyLabel + '</button>';
    var downloadBtn = (!isContentRecord || String(a.file_url || '').trim()) ? '<button type="button" class="btn btn-ghost btn-sm" data-download-asset="' + escapeAttr(a.asset_id) + '">' + (isContentRecord ? '打开文件' : '下载') + '</button>' : '';
    var candidateBtn = !isContentRecord && isImage ? '<button type="button" class="btn btn-ghost btn-sm" data-creative-candidate="' + escapeAttr(a.asset_id) + '" data-current-creative-group="' + escapeAttr(currentGroup) + '">设为创意备选</button>' : '';
    var actionMenu = _assetContentActionMenuHtml(a);
    var contentPreviewText = isContentRecord ? _assetContentText(a) : '';
    var imageStrip = isContentRecord && contentImages.length
      ? '<div class="asset-content-image-strip">' + contentImages.slice(0, 3).map(function(url, imageIndex) {
          return '<img src="' + escapeAttr(url) + '" alt="朋友圈图片 ' + escapeAttr(imageIndex + 1) + '" loading="lazy">';
        }).join('') + '</div>'
      : '';
    var deleteBtn = isContentRecord
      ? '<button type="button" class="btn btn-ghost btn-sm" data-delete-content-record="' + escapeAttr(a.asset_id) + '">删除</button>'
      : '<button type="button" class="btn btn-ghost btn-sm" data-delete-asset="' + escapeAttr(a.asset_id) + '">删除</button>';
    var badgeColor = isImage ? '#6366f1' : isVideo ? '#f59e0b' : isDocument ? '#64748b' : '#888';
    return '<div class="skill-store-card asset-card">' +
      selectHtml +
      '<div class="card-label"><span style="display:inline-flex;align-items:center;gap:0.35rem;flex-wrap:wrap;"><span class="asset-card-badge" style="background:' + badgeColor + ';">' + escapeHtml(typeLabel) + '</span><span class="asset-origin-badge' + originClass + '">' + escapeHtml(originLabel) + '</span></span><span class="asset-card-size">' + escapeHtml(size) + '</span></div>' +
      preview +
      '<div class="card-desc asset-card-desc-clamp" style="font-size:0.78rem;">' + escapeHtml(contentPreviewText || a.summary || a.prompt || a.title || a.filename) + '</div>' +
      imageStrip +
      groupHtml +
      '<div class="card-desc" style="font-size:0.72rem;color:var(--text-muted);">ID: ' + escapeHtml(a.asset_id) + ' · ' + escapeHtml(_formatDateTimeBeijing(a.created_at)) + '</div>' +
      '<div class="card-actions">' + previewBtn + ' ' + copyPromptBtn + ' ' + downloadBtn + ' ' + useAsAttachBtn + ' ' + candidateBtn + ' ' + actionMenu + ' ' + deleteBtn + '</div></div>';
  }).join('');

  if (append) container.insertAdjacentHTML('beforeend', html);
  else container.innerHTML = html;
  _bindAssetCardActions(container);
  _wireAssetListThumbs(container);
  _updateAssetBulkUi();
}

function _cancelAssetProgressiveRender() {
  if (_assetLibraryRenderTimer) {
    var caf = window.cancelAnimationFrame || window.clearTimeout;
    caf(_assetLibraryRenderTimer);
    _assetLibraryRenderTimer = 0;
  }
}

function _renderAssetCardsProgressively(container, assets, append, seq, onDone) {
  if (!container) return;
  _cancelAssetProgressiveRender();
  var list = Array.isArray(assets) ? assets : [];
  if (!append) container.innerHTML = '';
  var index = 0;
  function renderChunk(count) {
    if (seq !== _assetLibraryLoadSeq) return;
    var end = Math.min(list.length, index + count);
    if (end > index) {
      _renderAssetCards(container, list.slice(index, end), true);
      index = end;
    }
    if (index >= list.length) {
      _assetLibraryRenderTimer = 0;
      if (typeof onDone === 'function') onDone();
      return;
    }
    var raf = window.requestAnimationFrame || function(fn) { return window.setTimeout(fn, 16); };
    _assetLibraryRenderTimer = raf(function() {
      renderChunk(_ASSET_PROGRESSIVE_BATCH);
    });
  }
  renderChunk(Math.max(1, _ASSET_PROGRESSIVE_FIRST_BATCH));
}

function loadAssets(query, options) {
  options = options || {};
  var el = document.getElementById('assetList');
  if (!el) return;
  var append = !!options.append;
  var snap = _snapshotAssetQuery(query);
  var offset = append ? (_assetLibraryState.offset || 0) : 0;
  var seq = ++_assetLibraryLoadSeq;
  _cancelAssetProgressiveRender();

  if (!append) {
    _assetLibraryState.assetMap = {};
    _setAssetBulkMode(false);
    el.innerHTML = '<div class="page-empty-card">加载中...</div>';
    _setAssetLoadMoreState(false, false);
  } else {
    _setAssetLoadMoreState(true, true);
  }

  _assetLibraryState.loading = true;
  var sharedContentMode = snap.origin === 'generated' && _isSharedContentRecordType(snap.mediaType);
  var url = '';
  if (sharedContentMode) {
    var cloud = _assetCloudBase();
    if (!cloud) {
      _assetLibraryState.loading = false;
      el.innerHTML = '<div class="page-empty-card msg err">未配置云端 API_BASE，无法读取内容记录。</div>';
      return;
    }
    url = cloud + '/api/content-records?kind=' + encodeURIComponent(snap.mediaType) + '&limit=' + _ASSET_PAGE_SIZE + '&offset=' + offset + '&compact=true';
  } else {
    url = publishLocalBase() + '/api/assets?limit=' + _ASSET_PAGE_SIZE + '&offset=' + offset;
    if (snap.origin) url += '&origin=' + encodeURIComponent(snap.origin);
    if (snap.mediaType) url += '&media_type=' + encodeURIComponent(snap.mediaType);
    if (snap.creativeGroup) url += '&creative_group=' + encodeURIComponent(snap.creativeGroup);
    if (snap.query) url += '&q=' + encodeURIComponent(snap.query);
  }

  fetch(url, { headers: authHeaders() })
    .then(function(r) {
      return r.json().catch(function() { return {}; }).then(function(d) {
        if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
        return d;
      });
    })
    .then(function(d) {
      if (seq !== _assetLibraryLoadSeq) return;
      var assets = sharedContentMode
        ? ((d && Array.isArray(d.items)) ? d.items.map(_normalizeSharedContentRecord) : [])
        : ((d && Array.isArray(d.assets)) ? d.assets : []);
      if (sharedContentMode && snap.query) {
        var needle = String(snap.query).toLowerCase();
        assets = assets.filter(function(item) {
          return [item.title, item.summary, item.prompt, item.filename].some(function(value) {
            return String(value || '').toLowerCase().indexOf(needle) >= 0;
          });
        });
      }
      if (snap.origin) {
        assets = assets.filter(function(a) {
          var itemOrigin = a && a.asset_origin === 'user_upload' ? 'user_upload' : 'generated';
          return itemOrigin === snap.origin;
        });
      }
      var total = sharedContentMode
        ? Number(d && d.pagination && d.pagination.total || assets.length)
        : ((d && typeof d.total === 'number') ? d.total : assets.length);
      if (!assets.length && !append) {
        el.innerHTML = '<div class="page-empty-card">' + (snap.origin === 'generated' ? '当前分类暂无内容记录。' : '暂无素材。可上传本地文件或保存网络 URL。') + '</div>';
        _assetLibraryState = {
          offset: 0,
          total: total,
          query: snap.query,
          mediaType: snap.mediaType,
          creativeGroup: snap.creativeGroup,
          origin: snap.origin,
          loading: false,
          assetMap: {}
        };
        _setAssetLoadMoreState(false, false);
        return;
      }
      _assetLibraryState.offset = offset;
      _assetLibraryState.total = total;
      _assetLibraryState.query = snap.query;
      _assetLibraryState.mediaType = snap.mediaType;
      _assetLibraryState.creativeGroup = snap.creativeGroup;
      _assetLibraryState.origin = snap.origin;
      _assetLibraryState.loading = true;
      _setAssetLoadMoreState(false, false);
      _renderAssetCardsProgressively(el, assets, append, seq, function() {
        if (seq !== _assetLibraryLoadSeq) return;
        _assetLibraryState.offset = offset + assets.length;
        _assetLibraryState.loading = false;
        _setAssetLoadMoreState(_assetLibraryState.offset < total, false);
        if (!append && !options.skipCloudSync) {
          _syncUserUploadAssetsAfterRender(snap, {
            force: !!options.syncUploads,
            showError: !!options.syncUploads
          });
        }
      });
    })
    .catch(function() {
      if (seq !== _assetLibraryLoadSeq) return;
      _assetLibraryState.loading = false;
      if (append) {
        _setAssetLoadMoreState(_assetLibraryState.offset < _assetLibraryState.total, false);
        _assetMsgShow('加载更多失败，请稍后重试。', true);
      } else {
        _setAssetLoadMoreState(false, false);
        el.innerHTML = '<div class="page-empty-card msg err">加载失败</div>';
      }
    });
}

var assetUploadFile = null;
var assetUploadLabel = null;
function setAssetUploadState(loading, text) {
  if (assetUploadLabel) {
    assetUploadLabel.style.opacity = loading ? '0.5' : '1';
    assetUploadLabel.style.pointerEvents = loading ? 'none' : '';
  }
  if (text) _assetMsgShow(text, false);
}

function bindAssetLibraryUi() {
  var assetSelectLoaded = document.getElementById('assetSelectLoaded');
  if (assetSelectLoaded && !assetSelectLoaded._assetLibraryBound) {
    assetSelectLoaded._assetLibraryBound = true;
    assetSelectLoaded.addEventListener('change', function() {
      _selectLoadedAssets(assetSelectLoaded.checked);
    });
  }

  var assetBulkStartBtn = document.getElementById('assetBulkStartBtn');
  if (assetBulkStartBtn && !assetBulkStartBtn._assetLibraryBound) {
    assetBulkStartBtn._assetLibraryBound = true;
    assetBulkStartBtn.addEventListener('click', function() {
      _setAssetBulkMode(true);
    });
  }

  var assetBulkCancelBtn = document.getElementById('assetBulkCancelBtn');
  if (assetBulkCancelBtn && !assetBulkCancelBtn._assetLibraryBound) {
    assetBulkCancelBtn._assetLibraryBound = true;
    assetBulkCancelBtn.addEventListener('click', function() {
      _setAssetBulkMode(false);
    });
  }

  var assetBulkConfirmBtn = document.getElementById('assetBulkConfirmBtn');
  if (assetBulkConfirmBtn && !assetBulkConfirmBtn._assetLibraryBound) {
    assetBulkConfirmBtn._assetLibraryBound = true;
    assetBulkConfirmBtn.addEventListener('click', _bulkDeleteSelectedAssets);
  }

  document.querySelectorAll('.asset-origin-tab').forEach(function(tab) {
    if (tab._assetOriginBound) return;
    tab._assetOriginBound = true;
    tab.addEventListener('click', function() {
      _setAssetOriginTab(tab.getAttribute('data-asset-origin'));
      loadAssets(_currentAssetSearchQuery());
    });
  });

  var assetSearchBtn = document.getElementById('assetSearchBtn');
  if (assetSearchBtn && !assetSearchBtn._assetLibraryBound) {
    assetSearchBtn._assetLibraryBound = true;
    assetSearchBtn.addEventListener('click', function() {
      loadAssets(_currentAssetSearchQuery());
    });
  }

  var assetTypeFilter = document.getElementById('assetTypeFilter');
  if (assetTypeFilter && !assetTypeFilter._assetLibraryBound) {
    assetTypeFilter._assetLibraryBound = true;
    assetTypeFilter.addEventListener('change', function() {
      loadAssets(_currentAssetSearchQuery());
    });
  }

  var assetCreativeGroupFilter = document.getElementById('assetCreativeGroupFilter');
  if (assetCreativeGroupFilter && !assetCreativeGroupFilter._assetLibraryBound) {
    assetCreativeGroupFilter._assetLibraryBound = true;
    assetCreativeGroupFilter.addEventListener('change', function() {
      loadAssets(_currentAssetSearchQuery());
    });
  }

  var assetRefreshBtn = document.getElementById('assetRefreshBtn');
  if (assetRefreshBtn && !assetRefreshBtn._assetLibraryBound) {
    assetRefreshBtn._assetLibraryBound = true;
    assetRefreshBtn.addEventListener('click', function() {
      loadCreativeCandidateGroups();
      loadAssets(_currentAssetSearchQuery(), { force: true, syncUploads: true });
    });
  }

  var assetCreativeGroupClose = document.getElementById('assetCreativeGroupClose');
  if (assetCreativeGroupClose && !assetCreativeGroupClose._assetLibraryBound) {
    assetCreativeGroupClose._assetLibraryBound = true;
    assetCreativeGroupClose.addEventListener('click', _closeAssetCreativeGroupModal);
  }
  var assetCreativeGroupCancel = document.getElementById('assetCreativeGroupCancel');
  if (assetCreativeGroupCancel && !assetCreativeGroupCancel._assetLibraryBound) {
    assetCreativeGroupCancel._assetLibraryBound = true;
    assetCreativeGroupCancel.addEventListener('click', _closeAssetCreativeGroupModal);
  }
  var assetCreativeGroupSave = document.getElementById('assetCreativeGroupSave');
  if (assetCreativeGroupSave && !assetCreativeGroupSave._assetLibraryBound) {
    assetCreativeGroupSave._assetLibraryBound = true;
    assetCreativeGroupSave.addEventListener('click', _saveAssetCreativeGroup);
  }
  var assetCreativeGroupInput = document.getElementById('assetCreativeGroupInput');
  if (assetCreativeGroupInput && !assetCreativeGroupInput._assetLibraryBound) {
    assetCreativeGroupInput._assetLibraryBound = true;
    assetCreativeGroupInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        _saveAssetCreativeGroup();
      }
    });
  }

  var assetPreviewClose = document.getElementById('assetPreviewModalClose');
  if (assetPreviewClose && !assetPreviewClose._assetLibraryBound) {
    assetPreviewClose._assetLibraryBound = true;
    assetPreviewClose.addEventListener('click', _closeAssetPreviewModal);
  }
  var assetPreviewMask = document.getElementById('assetPreviewModal');
  if (assetPreviewMask && !assetPreviewMask._assetLibraryBound) {
    assetPreviewMask._assetLibraryBound = true;
    assetPreviewMask.addEventListener('click', function(e) {
      if (e.target === assetPreviewMask) _closeAssetPreviewModal();
    });
  }
  var assetPreviewCopy = document.getElementById('assetPreviewCopyPromptBtn');
  if (assetPreviewCopy && !assetPreviewCopy._assetLibraryBound) {
    assetPreviewCopy._assetLibraryBound = true;
    assetPreviewCopy.addEventListener('click', function() {
      _copyAssetPrompt(_assetPreviewState && _assetPreviewState.prompt);
    });
  }
  var assetPreviewDownload = document.getElementById('assetPreviewDownloadBtn');
  if (assetPreviewDownload && !assetPreviewDownload._assetLibraryBound) {
    assetPreviewDownload._assetLibraryBound = true;
    assetPreviewDownload.addEventListener('click', function() {
      _downloadAssetToLibrary(_assetPreviewState, { button: assetPreviewDownload, usePreviewMsg: true });
    });
  }

  var assetPublishClose = document.getElementById('assetPublishModalClose');
  if (assetPublishClose && !assetPublishClose._assetLibraryBound) {
    assetPublishClose._assetLibraryBound = true;
    assetPublishClose.addEventListener('click', _closeAssetPublishModal);
  }
  var assetPublishCancel = document.getElementById('assetPublishModalCancel');
  if (assetPublishCancel && !assetPublishCancel._assetLibraryBound) {
    assetPublishCancel._assetLibraryBound = true;
    assetPublishCancel.addEventListener('click', _closeAssetPublishModal);
  }
  var assetPublishMask = document.getElementById('assetPublishModal');
  if (assetPublishMask && !assetPublishMask._assetLibraryBound) {
    assetPublishMask._assetLibraryBound = true;
    assetPublishMask.addEventListener('click', function(e) {
      if (e.target === assetPublishMask) _closeAssetPublishModal();
    });
  }
  var assetPublishSubmit = document.getElementById('assetPublishModalSubmit');
  if (assetPublishSubmit && !assetPublishSubmit._assetLibraryBound) {
    assetPublishSubmit._assetLibraryBound = true;
    assetPublishSubmit.addEventListener('click', _submitAssetPublishModal);
  }
  var assetPublishAccountSelect = document.getElementById('assetPublishAccountSelect');
  if (assetPublishAccountSelect && !assetPublishAccountSelect._assetLibraryBound) {
    assetPublishAccountSelect._assetLibraryBound = true;
    assetPublishAccountSelect.addEventListener('change', _assetPublishUpdateAccountMeta);
  }

  // Upload local files; prepare a public URL only when a later action needs one.
  assetUploadFile = document.getElementById('assetUploadFile');
  assetUploadLabel = assetUploadFile ? assetUploadFile.closest('label') : null;
  if (assetUploadFile && !assetUploadFile._assetLibraryBound) {
    assetUploadFile._assetLibraryBound = true;
    assetUploadFile.addEventListener('change', function() {
    var files = assetUploadFile.files;
    if (!files || !files.length) return;
    var total = files.length;
    var done = 0, failed = 0;
    var uploadErrors = [];
    setAssetUploadState(true, '正在保存到本地素材库 ' + total + ' 个文件…');
    Array.from(files).forEach(function(f, idx) {
      var fd = new FormData();
      fd.append('file', f);
      fetch(publishLocalBase() + '/api/assets/upload', { method: 'POST', headers: _authHeadersNoContentType(), body: fd })
        .then(function(r) {
          return r.text().then(function(raw) {
            var d = {};
            if (raw) {
              try { d = JSON.parse(raw); } catch (e) { d = {}; }
            }
            if (!r.ok) {
              var msg = 'HTTP ' + r.status;
              if (d && d.detail) {
                msg = typeof d.detail === 'string' ? d.detail : (Array.isArray(d.detail) ? d.detail.map(function(x) { return x.msg || JSON.stringify(x); }).join('; ') : JSON.stringify(d.detail));
              } else if (raw) {
                msg += ': ' + raw.slice(0, 300);
              }
              throw new Error(msg);
            }
            return d;
          });
        })
        .then(function(d) {
          if (d && d.asset_id) {
            done++;
          } else {
            throw new Error('本地保存未返回素材编号');
          }
        })
        .catch(function(err) {
          failed++;
          var reason = (err && err.message) ? String(err.message) : '未知错误';
          if (/failed to fetch|networkerror|load failed/i.test(reason)) {
            reason = '网络请求失败，无法连接本机服务或云端上传接口';
          }
          uploadErrors.push((f.name || ('文件' + (idx + 1))) + '：' + reason.slice(0, 300));
        })
        .finally(function() {
          var finished = done + failed;
          if (finished === total) {
            assetUploadFile.value = '';
            setAssetUploadState(false, '');
            var msg = '本地保存完成: ' + done + ' 个文件';
            if (failed) msg += ', ' + failed + ' 个失败';
            if (uploadErrors.length) {
              msg += '；失败原因：' + uploadErrors.slice(0, 2).join('；');
              if (uploadErrors.length > 2) msg += '；另有 ' + (uploadErrors.length - 2) + ' 个文件失败';
            }
            _assetMsgShow(msg, failed > 0);
            if (done) _setAssetOriginTab('user_upload');
            loadCreativeCandidateGroups();
            loadAssets(_currentAssetSearchQuery(), { force: true, skipCloudSync: true });
          } else {
            setAssetUploadState(true, '正在保存到本地素材库 ' + finished + '/' + total + '…');
          }
        });
    });
  });
  }
}

function loadCreativeCandidateGroups() {
  var base = publishLocalBase();
  if (!base) return Promise.resolve([]);
  return fetch(base + '/api/assets/creative-candidate-groups', { headers: authHeaders() })
    .then(function(r) { return r.json().catch(function() { return {}; }).then(function(d) { if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status)); return d; }); })
    .then(function(d) {
      _assetCreativeGroupsCache = Array.isArray(d.groups) ? d.groups : [];
      _renderAssetCreativeGroupControls();
      if (typeof window.refreshScheduledCreativeGroups === 'function') {
        window.refreshScheduledCreativeGroups(_assetCreativeGroupsCache);
      }
      return _assetCreativeGroupsCache;
    })
    .catch(function() {
      _renderAssetCreativeGroupControls();
      return _assetCreativeGroupsCache;
    });
}
window.loadCreativeCandidateGroups = loadCreativeCandidateGroups;

function bindAssetSaveUrlUi() {
  var assetSaveUrlBtn = document.getElementById('assetSaveUrlBtn');
  if (assetSaveUrlBtn && !assetSaveUrlBtn._assetSaveUrlBound) {
    assetSaveUrlBtn._assetSaveUrlBound = true;
    assetSaveUrlBtn.addEventListener('click', function() {
      var urlInput = document.getElementById('assetUrlInput');
      var rawUrl = (urlInput ? urlInput.value : '').trim();
      if (!rawUrl) { _assetMsgShow('请输入素材URL', true); return; }
      assetSaveUrlBtn.disabled = true;
      _assetMsgShow('正在保存…', false);
      var ext = rawUrl.split('?')[0].split('#')[0].split('.').pop().toLowerCase();
      var mtype = 'image';
      if (['mp4', 'mov', 'avi', 'mkv', 'webm', 'flv'].indexOf(ext) >= 0) mtype = 'video';
      fetch(publishLocalBase() + '/api/assets/save-url', {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
        body: JSON.stringify({ url: rawUrl, media_type: mtype })
      })
        .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(function(d) {
          if (urlInput) urlInput.value = '';
          _assetMsgShow('保存成功 (ID: ' + (d.asset_id || '') + ')', false);
          loadCreativeCandidateGroups().then(function() {
            loadAssets(_currentAssetSearchQuery(), { force: true });
          });
        })
        .catch(function(e) { _assetMsgShow('保存失败: ' + e.message, true); })
        .finally(function() { assetSaveUrlBtn.disabled = false; });
    });
  }
}

// ── Tasks ────────────────────────────────────────────────────────

var TASK_STATUS = { pending: '排队中', publishing: '发布中', success: '成功', failed: '失败', need_login: '需登录' };
var TASK_COLORS = { pending: '#fbbf24', publishing: '#60a5fa', success: '#34d399', failed: '#f87171', need_login: '#fb923c' };

function _renderSteps(steps) {
  if (!steps || !steps.length) return '';
  var html = '<div style="margin-top:0.5rem;padding:0.5rem;background:rgba(255,255,255,0.03);border-radius:6px;font-size:0.75rem;">';
  html += '<div style="color:var(--text-muted);margin-bottom:0.25rem;font-weight:600;">执行步骤：</div>';
  for (var i = 0; i < steps.length; i++) {
    var s = steps[i];
    var icon = s.ok ? '✓' : '✗';
    var color = s.ok ? '#34d399' : '#f87171';
    var action = s.action || s.note || '';
    var detail = '';
    if (s.error) detail = ' — ' + s.error;
    else if (s.selector) detail = '';
    else if (s.url) detail = '';
    else if (s.tried && !s.ok) detail = ' (未匹配)';
    html += '<div style="color:' + color + ';padding:1px 0;">' +
      '<span style="display:inline-block;width:1.2em;text-align:center;">' + icon + '</span> ' +
      escapeHtml(action) + escapeHtml(detail) + '</div>';
  }
  html += '</div>';
  return html;
}

function loadTasks() {
  var el = document.getElementById('taskList');
  if (!el) return;
  el.innerHTML = '<div class="page-empty-card">加载中…</div>';
  fetch(publishLocalBase() + '/api/publish/tasks?limit=50', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var tasks = (d && Array.isArray(d.tasks)) ? d.tasks : [];
      if (!tasks.length) {
        el.innerHTML = '<div class="page-empty-card">暂无<strong>单次发布</strong>记录（对话触发的 publish 任务）。<br>' +
          '若您配置的是账号上的<strong>间隔定时任务</strong>：请到<strong>发布账号</strong> → 点击该账号 → <strong>执行记录</strong> 或进入详情后点 <strong>任务列表</strong>（今日头条与抖音、小红书相同）。</div>';
        return;
      }
      el.innerHTML = '<div class="publish-task-list">' + tasks.map(function(t) {
        var statusColor = TASK_COLORS[t.status] || '#888';
        var statusLabel = TASK_STATUS[t.status] || t.status;
        var resultLink = t.result_url ? ' <a href="' + escapeAttr(t.result_url) + '" target="_blank" style="color:var(--primary);">查看</a>' : '';
        var errorText = t.error ? '<div class="card-desc" style="color:#f87171;font-size:0.78rem;margin-top:0.35rem;">' + escapeHtml(t.error) + '</div>' : '';
        var acctInfo = (t.platform ? (PLATFORM_NAMES[t.platform] || t.platform) : '') +
          (t.account_nickname ? ' · ' + t.account_nickname : '');
        var stepsHtml = _renderSteps(t.steps || []);
        return '<div class="publish-task-item">' +
          '<div class="publish-task-top">' +
            '<div><div class="publish-task-title">' + escapeHtml(t.title || '无标题') + '</div>' +
            '<div class="publish-task-meta">素材:' + escapeHtml(t.asset_id) +
            (acctInfo ? ' · ' + escapeHtml(acctInfo) : '') + '</div></div>' +
            '<span class="publish-task-status" style="color:' + statusColor + ';">' + statusLabel + resultLink + '</span>' +
          '</div>' +
          errorText +
          stepsHtml +
          '<div class="publish-task-timeline">' +
            escapeHtml(_formatDateTimeBeijing(t.created_at)) +
            (t.finished_at ? ' → ' + escapeHtml(_formatDateTimeBeijing(t.finished_at)) : '') +
          '</div>' +
        '</div>';
      }).join('') + '</div>';
    })
    .catch(function() { el.innerHTML = '<div class="page-empty-card msg err">加载失败</div>'; });
}

// ── Refresh button ───────────────────────────────────────────────

function bindPublishRefreshButtons() {
  var refreshPubBtn = document.getElementById('refreshPublishBtn');
  if (refreshPubBtn && !refreshPubBtn._publishRefreshBound) {
    refreshPubBtn._publishRefreshBound = true;
    refreshPubBtn.addEventListener('click', function() {
      if (_currentPubTab === 'accounts') {
        if (_detailAccountId) {
          var ac = _allAccounts.filter(function(a) { return a.id === _detailAccountId; })[0];
          if (ac) openAccountDetailPanel(_detailAccountId);
        }
        loadAccounts();
      }
      if (_currentPubTab === 'tasks') loadTasks();
    });
  }

}

function initPublishView() {
  bindPublishTabs();
  bindPublishAccountUi();
  bindPublishRefreshButtons();
  bindPublishAccountDetailAndSchedule();
  hideAccountDetailPanel();
  loadAccounts();
  loadCreativeCandidateGroups();
}

function initAssetLibraryView() {
  _setAssetOriginTab(window.__assetLibraryRequestedOrigin || _currentAssetOrigin);
  bindAssetLibraryUi();
  bindAssetSaveUrlUi();
  bindPublishRefreshButtons();
  loadAssets(_currentAssetSearchQuery());
  loadCreativeCandidateGroups();
}
window.initAssetLibraryView = initAssetLibraryView;

// ── 详情页作品网格 + 定时弹窗 ─────────────────────────────────────

function _renderToutiaoInsightsPanel(platform, meta) {
  var el = document.getElementById('detailCreatorToutiaoInsights');
  if (!el) return;
  if (platform !== 'toutiao') {
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  var ins = meta && meta.toutiao_insights;
  if (!ins || typeof ins !== 'object') {
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  var keys = Object.keys(ins);
  if (!keys.length) {
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  keys.sort(function(a, b) { return a.toLowerCase().localeCompare(b.toLowerCase()); });
  var rows = keys.map(function(k) {
    var v = ins[k];
    if (v === null || v === undefined) v = '';
    if (typeof v === 'object') v = JSON.stringify(v);
    return '<tr><td class="sch-task-mono" style="padding:0.25rem 0.5rem 0.25rem 0;vertical-align:top;color:var(--text-muted);max-width:42%;word-break:break-all;">' + escapeHtml(k) + '</td><td style="padding:0.25rem 0;word-break:break-word;">' + escapeHtml(String(v)) + '</td></tr>';
  }).join('');
  el.style.display = 'block';
  el.innerHTML = '<div style="font-weight:600;margin-bottom:0.35rem;font-size:0.9rem;">账号 / 收益 / 数据（XHR 摘要）</div>' +
    '<p class="meta" style="font-size:0.75rem;margin-bottom:0.5rem;line-height:1.45;">同步时依次打开首页、内容管理、收益与数据等页并抓取接口中的标量字段；字段名随头条后台可能变化，仅供参考。</p>' +
    '<div style="overflow-x:auto;max-height:240px;overflow-y:auto;border:1px solid rgba(255,255,255,0.1);border-radius:8px;"><table style="width:100%;font-size:0.78rem;border-collapse:collapse;">' + rows + '</table></div>';
}

function _creatorFormatMetrics(m) {
  if (!m || typeof m !== 'object') return '';
  var parts = [];
  if (m.view_count != null) parts.push('播/阅 ' + m.view_count);
  if (m.play_count != null && m.play_count > 0) parts.push('播放 ' + m.play_count);
  if (m.like_count != null) parts.push('赞 ' + m.like_count);
  if (m.comment_count != null) parts.push('评 ' + m.comment_count);
  if (m.collect_count != null) parts.push('藏 ' + m.collect_count);
  if (m.share_count != null) parts.push('享 ' + m.share_count);
  return parts.join(' · ');
}

function _creatorRenderItems(items, gridId) {
  var grid = document.getElementById(gridId || 'detailCreatorItemGrid');
  if (!grid) return;
  if (!items || !items.length) {
    grid.innerHTML = '<p class="meta" style="padding:1rem;">暂无作品数据。抖音/小红书请先「从平台同步」并确保已登录。</p>';
    return;
  }
  grid.innerHTML = items.map(function(it) {
    var title = it.title || '(无标题)';
    var cover = it.cover_url && it.cover_url.indexOf('http') === 0
      ? '<img src="' + escapeAttr(it.cover_url) + '" alt="" referrerpolicy="no-referrer" style="width:100%;max-height:140px;object-fit:cover;border-radius:6px;">'
      : '<div style="height:100px;border-radius:6px;background:rgba(255,255,255,0.06);display:flex;align-items:center;justify-content:center;font-size:0.75rem;color:var(--text-muted);">无封面</div>';
    var metrics = _creatorFormatMetrics(it.metrics);
    return '<div class="skill-store-card">' +
      '<div class="card-label" style="font-size:0.72rem;color:var(--text-muted);">' + escapeHtml(it.content_type || '') + '</div>' +
      cover +
      '<div class="card-value" style="font-size:0.85rem;margin-top:0.35rem;max-height:3.2em;overflow:hidden;">' + escapeHtml(title) + '</div>' +
      '<div class="card-desc" style="font-size:0.75rem;color:var(--text-muted);">' + escapeHtml(metrics) + '</div>' +
      '<div class="card-desc" style="font-size:0.7rem;color:var(--text-muted);">ID: ' + escapeHtml(String(it.id || '')) + '</div></div>';
  }).join('');
}

function bindPublishAccountDetailAndSchedule() {
  var back = document.getElementById('accountDetailBack');
  if (back && !back._bound) {
    back._bound = true;
    back.addEventListener('click', function() {
      hideAccountDetailPanel();
      loadAccounts();
    });
  }
  var schBtn = document.getElementById('accountDetailScheduleBtn');
  if (schBtn && !schBtn._bound) {
    schBtn._bound = true;
    schBtn.addEventListener('click', function() {
      if (_detailAccountId) openCreatorScheduleModal(_detailAccountId);
    });
  }
  var schTasksBtn = document.getElementById('accountDetailScheduleTasksBtn');
  if (schTasksBtn && !schTasksBtn._bound) {
    schTasksBtn._bound = true;
    schTasksBtn.addEventListener('click', function() {
      if (!_detailAccountId) return;
      openCreatorScheduleTasksModal(_detailAccountId);
    });
  }
  var loadB = document.getElementById('detailCreatorLoadBtn');
  if (loadB && !loadB._bound) {
    loadB._bound = true;
    loadB.addEventListener('click', function() { _detailLoadCreatorCache(); });
  }
  var syncB = document.getElementById('detailCreatorSyncBtn');
  if (syncB && !syncB._bound) {
    syncB._bound = true;
    syncB.addEventListener('click', function() {
      if (!_detailAccountId) return;
      var chk = document.getElementById('detailCreatorHeadlessChk');
      _detailCreatorSetStatus('正在从平台同步…', false);
      syncB.disabled = true;
      fetch(publishLocalBase() + '/api/accounts/' + _detailAccountId + '/sync-creator-content', {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
        body: JSON.stringify({ headless: !!(chk && chk.checked) })
      })
        .then(function(r) { return r.json(); })
        .then(function(d) {
          var ac = _allAccounts.filter(function(a) { return a.id === _detailAccountId; })[0];
          var plat = ac ? ac.platform : '';
          if (!d.ok) _detailCreatorSetStatus('同步失败: ' + (d.error || d.detail || JSON.stringify(d)), true);
          else if (plat === 'toutiao') {
            var ic = (d.meta && d.meta.toutiao_insights) ? Object.keys(d.meta.toutiao_insights).length : 0;
            var tx = (d.item_count || 0) + ' 条作品' + (ic ? ' · ' + ic + ' 项数据/收益字段' : '');
            _detailCreatorSetStatus('同步成功，共 ' + tx, false);
          } else {
            _detailCreatorSetStatus('同步成功，共 ' + (d.item_count || 0) + ' 条', false);
          }
          _renderToutiaoInsightsPanel(plat, d.meta || null);
          _creatorRenderItems(d.items || [], 'detailCreatorItemGrid');
          loadAccounts();
        })
        .catch(function() { _detailCreatorSetStatus('请求失败', true); })
        .finally(function() { syncB.disabled = false; });
    });
  }
  var kindSel = document.getElementById('schScheduleKind');
  if (kindSel && !kindSel._schBound) {
    kindSel._schBound = true;
    kindSel.addEventListener('change', _schUpdateScheduleKindUI);
  }
  var schPm = document.getElementById('schPublishMode');
  if (schPm && !schPm._schBound) {
    schPm._schBound = true;
    schPm.addEventListener('change', _schUpdatePublishModeUI);
  }
  document.querySelectorAll('#accountDetailTabs [data-ad-tab]').forEach(function(btn) {
    if (btn._adTabBound) return;
    btn._adTabBound = true;
    btn.addEventListener('click', function() {
      var tab = btn.getAttribute('data-ad-tab');
      document.querySelectorAll('#accountDetailTabs .sys-tab').forEach(function(t) { t.classList.remove('active'); });
      btn.classList.add('active');
      var d = document.getElementById('accountDetailTabData');
      var s = document.getElementById('accountDetailTabSchedule');
      if (tab === 'schedule') {
        if (d) d.style.display = 'none';
        if (s) s.style.display = '';
      } else {
        if (d) d.style.display = '';
        if (s) s.style.display = 'none';
      }
    });
  });
  var adm = document.getElementById('accountDetailScheduleMode');
  if (adm && !adm._bound) {
    adm._bound = true;
    adm.addEventListener('change', function() {
      var v = adm.value === 'review' ? 'review' : 'immediate';
      _detailPutScheduleMerge({ schedule_publish_mode: v }).catch(function(e) {
        alert(e && e.message ? e.message : String(e));
      });
    });
  }
  var adv = document.getElementById('accountDetailReviewVariantCount');
  if (adv && !adv._bound) {
    adv._bound = true;
    adv.addEventListener('change', function() {
      var n = Math.max(1, Math.min(10, parseInt(adv.value, 10) || 3));
      adv.value = String(n);
      _detailPutScheduleMerge({ review_variant_count: n }).catch(function(e) {
        alert(e && e.message ? e.message : String(e));
      });
    });
  }
  var firstDelay = document.getElementById('accountDetailReviewFirstDelayMinutes');
  if (firstDelay && !firstDelay._bound) {
    firstDelay._bound = true;
    firstDelay.addEventListener('change', function() {
      var m = Math.max(0, Math.min(10080, parseInt(firstDelay.value, 10) || 0));
      firstDelay.value = String(m);
      var iso = _minutesFromNowToUtcIso(m);
      _detailPutScheduleMerge({ review_first_eta_at: iso }).catch(function(e) {
        alert(e && e.message ? e.message : String(e));
      });
    });
  }
  var cbtn = document.getElementById('schCancelBtn');
  if (cbtn && !cbtn._bound) {
    cbtn._bound = true;
    cbtn.addEventListener('click', closeCreatorScheduleModal);
  }
  var sbtn = document.getElementById('schSaveBtn');
  if (sbtn && !sbtn._bound) {
    sbtn._bound = true;
    sbtn.addEventListener('click', function() {
      if (!_schModalAccountId) return;
      var msg = document.getElementById('schModalMsg');
      var built = _buildSchedulePutBodyFromModal(msg);
      if (!built.ok) return;
      sbtn.disabled = true;
      var putBody = built.body;
      fetch(publishLocalBase() + '/api/accounts/' + _schModalAccountId + '/creator-schedule', {
        method: 'PUT',
        headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
        body: JSON.stringify(putBody)
      })
        .then(function(r) { return _parsePublishJsonResponse(r); })
        .then(function(x) {
          if (!x.ok) {
            var det = x.data && x.data.detail;
            msg.textContent = typeof det === 'string' ? det : JSON.stringify(det || x.data);
            msg.style.display = 'block';
            msg.className = 'msg err';
            return;
          }
          var savedAcct = _schModalAccountId;
          closeCreatorScheduleModal();
          loadAccounts();
          if (savedAcct && _detailAccountId === savedAcct) {
            fetch(publishLocalBase() + '/api/accounts', { headers: authHeaders() })
              .then(function(r) { return r.json(); })
              .then(function(d) {
                _allAccounts = (d && d.accounts) || [];
                var ac = _allAccounts.filter(function(a) { return a.id === _detailAccountId; })[0];
                if (ac) _refreshDetailScheduleSummary(ac);
              });
            fetch(publishLocalBase() + '/api/accounts/' + savedAcct + '/creator-schedule', { headers: authHeaders() })
              .then(function(r) { return r.json(); })
              .then(function(d) {
                _detailScheduleCache = d;
                var ac = _allAccounts.filter(function(a) { return a.id === _detailAccountId; })[0];
                if (ac) {
                  ac.creator_schedule = Object.assign({}, ac.creator_schedule || {}, d);
                  _refreshDetailScheduleSummary(ac);
                }
                _detailApplyScheduleTabFields(d);
              })
              .catch(function() {});
          }
        })
        .catch(function() {
          msg.textContent = '保存失败';
          msg.style.display = 'block';
          msg.className = 'msg err';
        })
        .finally(function() { sbtn.disabled = false; });
    });
  }
  var mask = document.getElementById('creatorScheduleModal');
  if (mask && !mask._bound) {
    mask._bound = true;
    mask.addEventListener('click', function(e) {
      if (e.target === mask) closeCreatorScheduleModal();
    });
  }

  var schTasksMask = document.getElementById('creatorScheduleTasksModal');
  if (schTasksMask && !schTasksMask._bound) {
    schTasksMask._bound = true;
    schTasksMask.addEventListener('click', function(e) {
      if (e.target === schTasksMask) closeCreatorScheduleTasksModal();
    });
  }
  var schTasksCloseBtn = document.getElementById('schTasksCloseBtn');
  if (schTasksCloseBtn && !schTasksCloseBtn._bound) {
    schTasksCloseBtn._bound = true;
    schTasksCloseBtn.addEventListener('click', closeCreatorScheduleTasksModal);
  }
  var schTasksRefreshBtn = document.getElementById('schTasksRefreshBtn');
  if (schTasksRefreshBtn && !schTasksRefreshBtn._bound) {
    schTasksRefreshBtn._bound = true;
    schTasksRefreshBtn.addEventListener('click', loadCreatorScheduleTasks);
  }
}

(function bindReviewSnapshotUi() {
  if (document.body._reviewSnapshotUi) return;
  document.body._reviewSnapshotUi = true;
  document.body.addEventListener('click', function(e) {
    var sub = e.target.closest('[data-review-subtab]');
    if (sub && sub.closest('#accountDetailReviewBlock')) {
      e.preventDefault();
      e.stopPropagation();
      _switchReviewSubTab(sub.getAttribute('data-review-subtab'));
      return;
    }
    if (e.target.closest('#accountDetailReviewSnapshotRefreshBtn')) {
      e.preventDefault();
      _loadReviewSnapshots();
      return;
    }
    var rst = e.target.closest('[data-review-restore-snapshot]');
    if (rst) {
      e.preventDefault();
      e.stopPropagation();
      _restoreReviewSnapshot(parseInt(rst.getAttribute('data-review-restore-snapshot'), 10));
      return;
    }
    var dtl = e.target.closest('[data-review-detail-snapshot]');
    if (dtl) {
      e.preventDefault();
      e.stopPropagation();
      _showReviewSnapshotDetail(parseInt(dtl.getAttribute('data-review-detail-snapshot'), 10));
    }
  });
})();

(function bindReviewDraftDelegation() {
  if (document.body._reviewDraftDeleg) return;
  document.body._reviewDraftDeleg = true;
  document.body.addEventListener('click', function(e) {
    var g = e.target.closest('[data-action="review-generate"]');
    if (g) {
      e.preventDefault();
      e.stopPropagation();
      _handleReviewGenerateClick();
      return;
    }
    var a = e.target.closest('[data-action="review-generate-assets"]');
    if (a) {
      e.preventDefault();
      e.stopPropagation();
      _handleReviewGenerateAssets();
      return;
    }
    var c = e.target.closest('[data-action="review-confirm"]');
    if (c) {
      e.preventDefault();
      e.stopPropagation();
      _handleReviewConfirmClick();
    }
  });
})();
