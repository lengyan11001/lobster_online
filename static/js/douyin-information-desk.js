(function() {
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
    var summary = document.getElementById('douyinDeskSummary');
    var tabs = document.getElementById('douyinDeskTabs');
    var content = document.getElementById('douyinDeskContent');
    if (!content) return;
    var snapshot = state.data && state.data.snapshot;
    if (!snapshot) {
      if (fetched) fetched.textContent = '服务器尚未生成快照，将在每天 09:00（北京时间）采集';
      if (summary) summary.innerHTML = '';
      if (tabs) tabs.innerHTML = '';
      content.innerHTML = '<div class="douyin-desk-empty">暂无平台数据</div>';
      return;
    }
    var sections = Array.isArray(snapshot.sections) ? snapshot.sections : [];
    var categories = [];
    sections.forEach(function(section) {
      var category = String(section && section.category || '其他').trim() || '其他';
      if (categories.indexOf(category) < 0) categories.push(category);
    });
    if (categories.indexOf(state.category) < 0) state.category = categories[0] || '';
    var stat = snapshot.summary || {};
    if (fetched) fetched.textContent = '最近采集：' + formatTime(snapshot.fetched_at) + ' · 状态：' + (snapshot.status === 'success' ? '完整' : '部分可用');
    if (summary) {
      summary.innerHTML = [
        [stat.item_count || 0, '平台数据'],
        [(stat.success_count || 0) + '/' + (stat.endpoint_count || 0), '接口成功'],
        [stat.failed_count || 0, '接口异常'],
        [snapshot.snapshot_date || '-', '快照日期']
      ].map(function(entry) {
        return '<div class="douyin-desk-summary-item"><strong>' + escapeHtml(entry[0]) + '</strong><span>' + escapeHtml(entry[1]) + '</span></div>';
      }).join('');
    }
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
      var cards = items.length ? items.map(function(item) {
        var title = item.title || item.name || '热门内容';
        var metaParts = [];
        if (item.author && item.author !== title) metaParts.push('作者 ' + item.author);
        if (item.value) metaParts.push(item.value);
        if (item.detail) metaParts.push(item.detail);
        var metrics = metricText(item);
        if (metrics) metaParts.push(metrics);
        var meta = metaParts.join(' · ') || '暂无指标';
        var body = '<div class="douyin-desk-item-main"><span class="douyin-desk-rank">' + escapeHtml(item.rank || '-') + '</span><span class="douyin-desk-item-title">' + escapeHtml(title) + '</span></div><div class="douyin-desk-item-meta">' + escapeHtml(meta) + '</div>';
        if (item.url && /^https?:\/\//i.test(String(item.url))) {
          body = '<a href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener noreferrer">' + body + '<span class="douyin-desk-item-link">打开观看</span></a>';
        }
        return '<article class="douyin-desk-item">' + body + '</article>';
      }).join('') : '<div class="douyin-desk-empty">该接口暂无可展示条目' + (section.error ? '：' + escapeHtml(section.error) : '') + '</div>';
      return '<section class="douyin-desk-section"><div class="douyin-desk-section-head"><h3>' + escapeHtml(section.title || section.key || '数据') + '</h3><span>' + items.length + ' 条</span></div><div class="douyin-desk-grid">' + cards + '</div></section>';
    }).join('') : '<div class="douyin-desk-empty">该分类暂无数据</div>';
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
