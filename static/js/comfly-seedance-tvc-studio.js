(function() {
  var state = {
    mode: 'image_auto',
    mainView: 'storyboard',
    duration: 10,
    activeBoardIndex: 0,
    images: [],
    examplesOpen: true,
    examplesLoading: false,
    exampleCatalog: [],
    exampleFeaturedCount: 0,
    exampleVisibleCount: 0,
    examplePageSize: 12,
    exampleCategory: 'all',
    exampleSearch: '',
    activeExampleId: '',
    currentJobId: '',
    currentJobStatus: '',
    currentResultVideoUrl: '',
    currentJobTitle: '',
    currentJobPrompt: '',
    currentJobError: '',
    currentJobProgress: null,
    currentJobProgressPercent: null,
    currentJobProgressLabel: '',
    currentJobProgressDetail: '',
    currentSegmentArtifacts: null,
    finalComposeRequested: false,
    submitBusy: false,
    submitLabel: '',
    pollTimer: null,
    recentJobs: []
  };
  var seedanceTaskToastTimer = null;
  var seedanceTaskNotifiedJobs = {};
  var lastSeedanceModel = '';
  var seedanceVideoFullscreenEventsBound = false;
  var customSelectEventsBound = false;
  var RECENT_JOB_LIMIT = 60;
  var REFERENCE_IMAGE_PROMPT_TEXT = '提示图片为上传图片';
  var assetPickerState = {
    loading: false,
    items: [],
    selected: {},
    query: ''
  };

  var defaults = {
    aspectRatio: '9:16',
    visualTone: 'clean_bright',
    rhythm: 'smooth',
    model: 'grok-imagine-video-1.5-preview',
    needAudio: true,
    needMerge: true,
    prompt: ''
  };

  function isYunwuVeoModel(model) {
    var value = String(model || '').toLowerCase().replace(/\s+/g, '');
    return value === 'yunwu-veo3.1-plus' || value === 'veo3.1-plus' || value === 'veo3.1' || value === 'yingmeng-plus' || value === '影梦plus';
  }

  function isOpenMindGrokModel(model) {
    var value = String(model || '').toLowerCase().replace(/\s+/g, '');
    return value === 'grok-imagine-video-1.5-preview' || value === 'yingmeng1.5plus' || value === '影梦1.5plus';
  }

  function videoRequestForModel(model) {
    if (isOpenMindGrokModel(model)) {
      return { model: 'grok-imagine-video-1.5-preview', channel: 'openmind' };
    }
    if (isYunwuVeoModel(model)) {
      return { model: 'veo3.1', channel: 'yunwu' };
    }
    return { model: model, channel: '' };
  }

  function getCurrentSegmentSeconds(model) {
    return isYunwuVeoModel(model || (($('seedanceModelSelect') || {}).value)) ? 8 : 10;
  }

  function getDurationSegmentCount(duration, model) {
    var segmentSeconds = getCurrentSegmentSeconds(model);
    return Math.max(1, Math.round((Number(duration) || segmentSeconds) / segmentSeconds));
  }

  function updateDurationChipsForModel(model) {
    var segmentSeconds = getCurrentSegmentSeconds(model);
    document.querySelectorAll('#seedanceDurationGrid .tvc-duration-chip').forEach(function(chip, index) {
      var duration = (index + 1) * segmentSeconds;
      chip.setAttribute('data-duration', String(duration));
      chip.textContent = duration + 's';
    });
  }

  var narrativeSeeds = [
    { title: '开场定调', copy: '先把主体和整体气质立住，让后面的镜头都围绕同一条内容展开。' },
    { title: '主体亮相', copy: '把产品或人物主体推到画面中心，强化识别度和连续性。' },
    { title: '细节推进', copy: '切到关键卖点、动作或质感特写，让用户继续往下看。' },
    { title: '场景展开', copy: '补足使用环境和关系，让画面从展示进入叙事。' },
    { title: '价值确认', copy: '用更明确的镜头语言收束卖点、氛围或转化理由。' },
    { title: '结尾收束', copy: '回到最能代表这条视频的主体镜头，形成完整记忆点。' }
  ];

  var modeMeta = {
    image_auto: {
      name: '参考图自动分析',
      hint: '当前模式会优先使用参考图统一主体和画面风格；未上传图片时，会按提示词直接规划分镜。',
      emphasis: '主体统一'
    },
    image_prompt: {
      name: '图片 + 提示词共创',
      hint: '当前模式会参考上传图片，并把你的提示词原样用于每个分段，不再自动扩写分镜话术。',
      emphasis: '图文共同控制'
    },
    prompt_only: {
      name: '纯提示词规划',
      hint: '当前模式只用提示词规划分镜并生成视频。',
      emphasis: '脚本主导'
    }
  };

  var PURPOSE_LABELS = {
    storyboard: '分镜参考',
    person: '指定人物',
    product: '指定产品',
    style: '参考风格',
    scene: '参考场景',
    auto: '普通参考'
  };

  var PURPOSE_HINTS = {
    storyboard: '参考图{n}是分镜/画面结构参考，请学习它的构图、镜头节奏、画面层次和创意短视频表达方式，不要把它误当成必须替换的人物或产品。',
    person: '参考图{n}是目标人物，请所有分镜和最终视频都保持参考图{n}的人物脸型、五官、发型、气质和服装核心特征；不要生成相似但不同的人。',
    product: '参考图{n}是目标产品，请所有产品展示镜头都保持参考图{n}的产品外观、包装、颜色、材质、标签布局和主要识别特征；不要沿用其他产品。',
    style: '参考图{n}只作为风格参考，请学习它的色彩、光线、质感、镜头氛围和视觉调性，不要复制其中无关人物或产品。',
    scene: '参考图{n}是场景参考，请使用类似空间、背景环境、光线和布景关系，并让人物或产品自然融入该场景。',
    auto: '参考图{n}是普通参考图，请结合它的主体、风格或构图进行视频分镜规划。'
  };

  var EXAMPLE_CATEGORIES = {
    all: [],
    product: ['产品', '带货', '商品', '广告', '口播', '美妆', '护肤', '包装', '品牌', '电商', '种草', 'TVC'],
    comedy: ['搞笑', '喜剧', '反转', '爆笑', '沙雕', '整顿', '幽默'],
    drama: ['剧情', '短剧', '故事', '反转剧', '职场', '情绪', '叙事'],
    fashion: ['变装', '换装', '走秀', '时尚', '穿搭', '铠甲', '妆容', '舞蹈'],
    guofeng: ['国风', '古风', '武侠', '汉服', '东方', '水墨', '仙侠', '中国'],
    sci_fi: ['科幻', '奇幻', '赛博朋克', '未来', '机甲', '魔法', '反重力', '特效'],
    cinematic: ['电影感', '电影质感', '影视', '高清', '写实', '镜头', '慢动作', '大片'],
    anime: ['动漫', '二次元', '动画', '漫画', 'OVA', '赛璐璐'],
    travel: ['风景', '旅行', '山脉', '城市', '街头', '自然', '航拍', '海边']
  };

  function $(id) {
    return document.getElementById(id);
  }

  function authHeadersSafe() {
    if (typeof authHeaders === 'function') {
      var headers = Object.assign({}, authHeaders() || {});
      delete headers['Content-Type'];
      delete headers['content-type'];
      return headers;
    }
    return {};
  }

  function escapeHtml(text) {
    return String(text || '').replace(/[&<>"]/g, function(ch) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;'
      }[ch];
    });
  }

  function normalizeApiErrorText(detail, fallback) {
    var defaultText = arguments.length >= 2 ? String(fallback || '') : '未知错误';
    if (detail === null || detail === undefined || detail === '') return defaultText;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      var rows = detail.map(function(item) {
        if (item === null || item === undefined) return '';
        if (typeof item === 'string') return item;
        if (typeof item === 'object') {
          var loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
          var msg = String(item.msg || item.message || item.detail || '').trim();
          if (loc && msg) return loc + ': ' + msg;
          if (msg) return msg;
          try { return JSON.stringify(item); } catch (err) { return String(item); }
        }
        return String(item);
      }).filter(Boolean);
      return rows.join('；') || defaultText;
    }
    if (typeof detail === 'object') {
      if (typeof detail.detail === 'string' && detail.detail) return detail.detail;
      if (typeof detail.message === 'string' && detail.message) return detail.message;
      if (typeof detail.msg === 'string' && detail.msg) return detail.msg;
      if (detail.error) return normalizeApiErrorText(detail.error, fallback);
      try { return JSON.stringify(detail); } catch (err) { return String(detail); }
    }
    return String(detail);
  }

  function responseErrorText(data, fallback) {
    return normalizeApiErrorText(data && (data.detail || data.message || data.error || data), fallback);
  }

  function progressStepStatusText(status) {
    var key = String(status || '').trim().toLowerCase();
    var labels = {
      success: '成功',
      ready: '已就绪',
      running: '进行中',
      pending: '等待中',
      queued: '排队中',
      failed: '失败',
      error: '失败',
      partial_failure: '部分完成',
      completed: '完成',
      complete: '完成',
      done: '完成',
    };
    return labels[key] || String(status || '').trim();
  }

  function progressStepNameText(name) {
    var raw = String(name || '').trim();
    if (!raw) return '处理中';
    var key = raw
      .toLowerCase()
      .replace(/^\d+_/, '')
      .replace(/\s+/g, '_')
      .replace(/_+/g, '_');
    var segmentMatch = key.match(/^segment_(\d+)_([a-z0-9_]+)$/);
    var segmentLabel = '';
    var stageKey = key;
    if (segmentMatch) {
      segmentLabel = '第 ' + parseInt(segmentMatch[1], 10) + ' 段';
      stageKey = segmentMatch[2];
    }
    var labels = {
      reference_upload_01: '参考图上传',
      reference_upload: '参考图上传',
      direct_video_plan: '直接视频任务准备',
      storyboard_plan: '分镜规划',
      plan: '分镜规划',
      board_image: '分镜图生成',
      segment_reference_image: '视频参考图生成',
      submit_primary: '视频提交',
      video_fallback: '切换备用视频通道',
      submit_fallback: '备用通道提交',
      poll: '视频生成查询',
      video_poll: '视频生成查询',
      download_video: '视频下载',
      merge_clips: '视频合并',
      merge_download: '视频下载',
      final: '成片整理',
      audio: '音频生成',
      narration: '旁白生成',
    };
    var label = labels[stageKey] || labels[key];
    if (label) return segmentLabel ? (segmentLabel + label) : label;
    return raw.replace(/^\d+_/, '').replace(/_/g, ' ');
  }

  function progressSummary(progress) {
    if (!progress || typeof progress !== 'object') return '';
    var manifestStatus = String(progress.manifest_status || '').trim().toLowerCase();
    var errors = Array.isArray(progress.errors) ? progress.errors : [];
    var steps = Array.isArray(progress.last_steps) ? progress.last_steps : [];
    if (manifestStatus === 'partial_failure' || manifestStatus === 'failed') {
      for (var i = errors.length - 1; i >= 0; i -= 1) {
        var err = errors[i];
        var text = '';
        if (typeof err === 'string') text = err;
        else if (err && typeof err === 'object') text = err.error || err.message || err.detail || '';
        text = normalizeApiErrorText(text, '').trim();
        if (text) return text;
      }
      for (var j = steps.length - 1; j >= 0; j -= 1) {
        var failedStep = steps[j] || {};
        if (failedStep.error) return normalizeApiErrorText(failedStep.error, '').trim();
      }
      return '视频生成失败，未产出可播放的视频，请查看任务日志后重试。';
    }
    if (steps.length) {
      var last = steps[steps.length - 1] || {};
      var name = progressStepNameText(last.name);
      var status = progressStepStatusText(last.status);
      if (last.error && String(last.status || '').trim().toLowerCase() === 'failed') {
        return normalizeApiErrorText(last.error, '').trim() || (name + '失败');
      }
      if (name || status) return (name || '处理中') + (status ? ' · ' + status : '');
    }
    if (progress.step_count) return '已执行 ' + progress.step_count + ' 个步骤';
    return '';
  }

  function normalizeProgressPercent(value) {
    var num = Number(value);
    if (!isFinite(num)) return null;
    if (num >= 0 && num <= 1) num = num * 100;
    if (num < 0 || num > 100) return null;
    return Math.max(0, Math.min(100, Math.round(num)));
  }

  function jobProgressPercent(job) {
    if (job && job.progressPercent != null) return normalizeProgressPercent(job.progressPercent);
    var progress = job && job.progress;
    if (progress && typeof progress === 'object') {
      return normalizeProgressPercent(progress.progress_percent || progress.percent || progress.progress);
    }
    return null;
  }

  function normalizeSegmentArtifacts(raw) {
    var artifacts = raw && typeof raw === 'object' ? raw : null;
    var source = artifacts && Array.isArray(artifacts.segments) ? artifacts.segments : [];
    if (!source.length) return null;
    var segments = source.map(function(item, idx) {
      item = item && typeof item === 'object' ? item : {};
      var index = Number(item.index || idx + 1) || idx + 1;
      var imagePrompt = String(
        item.image_prompt
        || item.imagePrompt
        || item.storyboard_board_image_prompt
        || item.storyboardBoardImagePrompt
        || item.storyboard_image_prompt
        || item.storyboard_image_prompt_en
        || item.first_frame_prompt
        || item.first_frame_prompt_en
        || item.segment_reference_prompt
        || item.segment_reference_prompt_en
        || item.prompt
        || ''
      ).trim();
      var videoPrompt = String(
        item.video_prompt
        || item.videoPrompt
        || item.submitted_video_prompt
        || item.submitted_video_prompt_en
        || item.seedance_prompt
        || item.seedance_prompt_en
        || item.prompt
        || ''
      ).trim();
      var imageUrl = String(item.image_url || item.imageUrl || '').trim();
      var videoUrl = String(item.video_url || item.videoUrl || '').trim();
      var workflowMode = String(item.workflow_mode || item.workflowMode || '').trim();
      var imageSource = String(item.image_source || item.imageSource || item.source || '').trim();
      var status = String(item.status || '').trim() || (videoUrl ? 'video_ready' : (imageUrl ? 'image_ready' : 'pending'));
      return {
        index: index,
        start: item.start != null ? Number(item.start) : null,
        end: item.end != null ? Number(item.end) : null,
        status: status,
        imageStatus: String(item.image_status || item.imageStatus || (imageUrl ? 'ready' : 'pending')),
        videoStatus: String(item.video_status || item.videoStatus || (videoUrl ? 'ready' : 'pending')),
        imagePrompt: imagePrompt,
        videoPrompt: videoPrompt,
        imageUrl: imageUrl,
        videoUrl: videoUrl,
        workflowMode: workflowMode,
        imageSource: imageSource,
        usesReferenceImage: !!(item.uses_reference_image || item.usesReferenceImage),
        provider: String(item.provider || '').trim(),
        model: String(item.model || '').trim(),
        taskId: String(item.task_id || item.taskId || '').trim(),
        error: String(item.error || '').trim(),
        progress: item.progress != null ? item.progress : null
      };
    }).sort(function(a, b) {
      return a.index - b.index;
    });
    var imageReady = segments.filter(function(item) { return !!item.imageUrl; }).length;
    var videoReady = segments.filter(function(item) { return !!item.videoUrl; }).length;
    var failed = segments.filter(function(item) { return item.status === 'failed' || !!item.error; }).length;
    return {
      segmentCount: Number(artifacts.segment_count || artifacts.segmentCount || segments.length) || segments.length,
      imageReadyCount: Number(artifacts.image_ready_count || artifacts.imageReadyCount || imageReady) || imageReady,
      videoReadyCount: Number(artifacts.video_ready_count || artifacts.videoReadyCount || artifacts.ready_count || videoReady) || videoReady,
      failedCount: Number(artifacts.failed_count || artifacts.failedCount || failed) || failed,
      segmentSeconds: Number(artifacts.segment_seconds || artifacts.segmentSeconds || 0) || 0,
      segments: segments
    };
  }

  function currentProgressPercent() {
    var direct = normalizeProgressPercent(state.currentJobProgressPercent);
    if (direct != null) return direct;
    return jobProgressPercent({ progress: state.currentJobProgress });
  }

  function currentProgressText() {
    return state.currentJobProgressDetail || state.currentJobProgressLabel || progressSummary(state.currentJobProgress) || '';
  }

  function progressBarHtml(percent, text) {
    var pct = normalizeProgressPercent(percent);
    if (pct == null) return '';
    return [
      '<div class="seedance-progress-wrap" aria-label="生成进度 ' + pct + '%">',
      '<div class="seedance-progress-meta">',
      '<span>生成进度</span>',
      '<strong>' + pct + '%</strong>',
      '</div>',
      '<div class="seedance-progress-bar"><span style="width:' + pct + '%"></span></div>',
      text ? '<div class="seedance-progress-detail">' + escapeHtml(text) + '</div>' : '',
      '</div>'
    ].join('');
  }

  function jobStageText(job) {
    if (!job) return '';
    if (job.status === 'completed') {
      return job.videoUrl ? '最终视频已生成，可以点击查看。' : '任务已完成，正在同步最终视频展示。';
    }
    if (job.status === 'failed') {
      return jobDetailText(job) || '任务失败，请查看错误后重新提交。';
    }
    var detail = jobDetailText(job);
    if (detail && detail !== '未知错误') return detail;
    return '正在合成视频，完成后会自动切换到成片结果。';
  }

  function jobPromptText(job) {
    if (!job) return '';
    var candidates = [
      job.prompt,
      job.taskText,
      job.task_text,
      job.description,
      job.detail,
      job.title
    ];
    for (var i = 0; i < candidates.length; i += 1) {
      var text = String(candidates[i] || '').trim();
      if (text && text !== '创意视频任务') return text;
    }
    return '';
  }

  function formatJobTime(job) {
    var ts = Number(job && (job.createdAt || job.updatedAt) || 0);
    if (!ts) return '';
    var d = new Date(ts);
    if (!isFinite(d.getTime())) return '';
    var now = new Date();
    var sameDay = d.getFullYear() === now.getFullYear()
      && d.getMonth() === now.getMonth()
      && d.getDate() === now.getDate();
    var hh = String(d.getHours()).padStart(2, '0');
    var mm = String(d.getMinutes()).padStart(2, '0');
    if (sameDay) return hh + ':' + mm;
    return String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0') + ' ' + hh + ':' + mm;
  }

  function currentJobPromptText() {
    return String(state.currentJobPrompt || '').trim();
  }

  function currentJobHeading() {
    var text = currentJobPromptText() || String(state.currentJobTitle || '').trim();
    if (!text) return '创意视频任务';
    return text.length > 28 ? (text.slice(0, 28) + '...') : text;
  }

  function currentJobSummary(values, boards) {
    if (state.currentJobStatus === 'completed') {
      return state.currentResultVideoUrl ? '最终视频已生成，可以直接播放、下载或打开。' : '任务已完成，最终视频正在同步展示与入库，请稍后刷新结果。';
    }
    if (state.currentJobStatus === 'failed') {
      return jobDetailText({ error: state.currentJobError, progress: state.currentJobProgress }) || '任务执行失败，请调整后重新提交。';
    }
    if (state.currentJobStatus === 'running') {
      return currentProgressText() || '高质量模型生成通常需要 5-10 分钟，任务完成后会提醒你。';
    }
    var summary = String(state.duration || 0) + ' 秒 / ' + boards.length + ' 个分镜 / ' + values.aspectRatio;
    return state.mode === 'prompt_only'
      ? summary + '。当前按纯提示词模式规划并生成视频。'
      : summary + '。提交后这里会展示最终成片和任务信息。';
  }

  function renderResultPills(values, boards) {
    var pills = [
      '<span class="seedance-result-pill" data-tone="' + escapeHtml(statusTone(state.currentJobStatus)) + '">' + escapeHtml(statusLabel(state.currentJobStatus)) + '</span>'
    ];
    if (state.currentJobId) pills.push('<span class="seedance-result-pill" data-tone="processing">任务 ' + escapeHtml(state.currentJobId.slice(0, 8)) + '</span>');
    if (values && values.model) pills.push('<span class="seedance-result-pill">模型 ' + escapeHtml(customSelectLabel($('seedanceModelSelect')) || values.model) + '</span>');
    if (values && values.aspectRatio) pills.push('<span class="seedance-result-pill">' + escapeHtml(values.aspectRatio) + '</span>');
    if (boards && boards.length) pills.push('<span class="seedance-result-pill">' + escapeHtml(String(boards.length)) + ' 个分镜</span>');
    if (state.duration) pills.push('<span class="seedance-result-pill">' + escapeHtml(String(state.duration)) + ' 秒</span>');
    return pills.join('');
  }

  function copyCurrentPromptToEditor() {
    var prompt = currentJobPromptText();
    if (!prompt) return showMessage('当前任务还没有可复制的提示词。');
    if ($('seedanceTaskPromptInput')) {
      $('seedanceTaskPromptInput').value = prompt;
      $('seedanceTaskPromptInput').focus();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(prompt).catch(function() {});
    }
    showMessage('已复制提示词并回填到左侧输入框。');
  }

  function jobMediaHtml(job) {
    var status = String((job && job.status) || 'running');
    var videoUrl = String((job && job.videoUrl) || '').trim();
    if (videoUrl && status === 'completed') {
      var thumb = jobThumbUrl(job);
      if (thumb) {
        return [
          '<span class="seedance-job-thumb-shell">',
          '<img src="' + escapeHtml(thumb) + '" alt="' + escapeHtml(jobPromptText(job) || '任务缩略图') + '">',
          '<span class="seedance-job-play-badge">点击查看</span>',
          '</span>'
        ].join('');
      }
      return [
        '<span class="seedance-job-thumb-shell is-video-only">',
        '<span class="seedance-job-play-mark">▶</span>',
        '<span class="seedance-job-play-badge">点击查看</span>',
        '</span>'
      ].join('');
    }
    if (status === 'completed') {
      return '<span class="seedance-business-job-placeholder">已完成，正在同步成片</span>';
    }
    if (status === 'failed') {
      return '<span class="seedance-business-job-placeholder is-failed">生成失败</span>';
    }
    return '<span class="seedance-business-job-placeholder is-running"><span class="tvc-status-spinner" aria-hidden="true"></span><span>生成中</span></span>';
  }

  function jobThumbUrl(job) {
    if (!job || typeof job !== 'object') return '';
    var artifacts = job.artifacts || {};
    var segments = Array.isArray(artifacts.segments) ? artifacts.segments : [];
    for (var i = 0; i < segments.length; i += 1) {
      var seg = segments[i] || {};
      var imageUrl = String(seg.imageUrl || seg.image_url || seg.first_frame_image_url || seg.segment_reference_image_url || '').trim();
      if (imageUrl) return imageUrl;
    }
    var cloudJob = job.cloudJob || {};
    var result = cloudJob.result || cloudJob.result_payload || {};
    var completed = result.completed_segments || result.completed_shots || [];
    if (Array.isArray(completed)) {
      for (var j = 0; j < completed.length; j += 1) {
        var item = completed[j] || {};
        var url = String(item.first_frame_image_url || item.segment_reference_image_url || item.storyboard_board_image_url || '').trim();
        if (url) return url;
      }
    }
    return '';
  }

  function jobDetailText(job) {
    if (!job) return '';
    var err = normalizeApiErrorText(job.error || '', '').trim();
    if (err) return err;
    return progressSummary(job.progress);
  }

  function formatFileSize(size) {
    if (!size) return '本地素材';
    if (size >= 1024 * 1024) return (size / (1024 * 1024)).toFixed(1) + ' MB';
    if (size >= 1024) return Math.round(size / 1024) + ' KB';
    return size + ' B';
  }

  function cleanRemoteUrl(url) {
    return String(url || '').trim().replace(/[\\\/]+$/, '');
  }

  function mediaItemUrl(item) {
    if (!item || typeof item !== 'object') return '';
    var fields = [
      item.url,
      item.objectUrl,
      item.source_url,
      item.sourceUrl,
      item.preview_url,
      item.previewUrl,
      item.open_url,
      item.openUrl,
      item.local_preview_url,
      item.localPreviewUrl,
      item.image_url,
      item.imageUrl
    ];
    for (var i = 0; i < fields.length; i += 1) {
      var url = String(fields[i] || '').trim();
      if (url) return url;
    }
    return '';
  }

  function normalizeExampleItem(item) {
    var tags = Array.isArray(item && item.tags) ? item.tags : [];
    var prompt = String((item && item.prompt) || '').trim();
    var promptZh = String((item && item.prompt_zh) || '').trim();
    var promptEn = String((item && item.prompt_en) || '').trim();
    return {
      id: String((item && item.id) || '').trim(),
      title: String((item && item.title) || 'Seedance 案例').trim(),
      slug: String((item && item.slug) || '').trim(),
      prompt: promptZh || prompt || promptEn,
      prompt_zh: promptZh,
      prompt_en: promptEn,
      cover_image: cleanRemoteUrl(item && item.cover_image),
      video_url: cleanRemoteUrl(item && item.video_url),
      model: String((item && item.model) || 'Seedance 2.0').trim() || 'Seedance 2.0',
      tags: tags.map(function(tag) { return String(tag || '').trim(); }).filter(Boolean).slice(0, 6),
      language: String((item && item.language) || '').trim(),
      is_featured: !!(item && item.is_featured),
      author: String((item && item.author) || '').trim()
    };
  }

  function updateExamplesBadge() {
    var badge = $('seedanceExamplesBadge');
    if (!badge) return;
    badge.textContent = state.exampleFeaturedCount || state.exampleCatalog.length || 0;
  }

  function updateExamplesToggle() {
    var btn = $('seedanceExamplesToggleBtn');
    if (!btn) return;
    btn.classList.toggle('is-active', !!state.examplesOpen);
  }

  function visibleExampleCount() {
    return state.exampleVisibleCount || state.examplePageSize || 12;
  }

  function exampleMatchesCategory(item, category) {
    if (!category || category === 'all') return true;
    var words = EXAMPLE_CATEGORIES[category] || [];
    if (!words.length) return true;
    var haystack = [
      item.title || '',
      item.prompt || '',
      item.prompt_zh || '',
      item.prompt_en || '',
      (item.tags || []).join(' ')
    ].join(' ');
    return words.some(function(word) {
      return haystack.indexOf(word) >= 0;
    });
  }

  function exampleMatchesSearch(item, query) {
    var q = String(query || '').trim().toLowerCase();
    if (!q) return true;
    var haystack = [
      item.title || '',
      item.prompt || '',
      item.prompt_zh || '',
      item.prompt_en || '',
      (item.tags || []).join(' ')
    ].join(' ').toLowerCase();
    return haystack.indexOf(q) >= 0;
  }

  function filteredExampleCatalog() {
    return (state.exampleCatalog || []).filter(function(item) {
      return exampleMatchesCategory(item, state.exampleCategory) && exampleMatchesSearch(item, state.exampleSearch);
    });
  }

  function loadMoreExamples() {
    var filtered = filteredExampleCatalog();
    if (!state.examplesOpen || state.examplesLoading || !filtered.length) return;
    var current = visibleExampleCount();
    if (current >= filtered.length) return;
    state.exampleVisibleCount = Math.min(current + state.examplePageSize, filtered.length);
    renderExamplesPanel();
  }

  function updateExamplesMoreButton() {
    var btn = $('seedanceExamplesMoreBtn');
    if (!btn) return;
    var total = filteredExampleCatalog().length;
    var visible = Math.min(visibleExampleCount(), total);
    btn.style.display = (state.examplesOpen && visible > 0 && visible < total) ? '' : 'none';
    btn.disabled = !!state.examplesLoading;
    btn.textContent = state.examplesLoading ? '加载中...' : '加载更多示例';
  }

  function openExampleVideo(example) {
    if (!example || !example.video_url) return;
    var modal = $('seedanceVideoModal');
    var player = $('seedanceVideoModalPlayer');
    var title = $('seedanceVideoModalTitle');
    if (!modal || !player) return;
    if (title) title.textContent = example.title || '案例视频';
    player.src = example.video_url;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    try { player.play(); } catch (err) {}
  }

  function closeExampleVideo() {
    var modal = $('seedanceVideoModal');
    var player = $('seedanceVideoModalPlayer');
    if (player) {
      try { player.pause(); } catch (err) {}
      player.removeAttribute('src');
      player.load();
    }
    if (modal) {
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
    }
  }

  function localBase() {
    return (typeof LOCAL_API_BASE !== 'undefined' ? (LOCAL_API_BASE || '') : '').replace(/\/$/, '');
  }

  function cloudBase() {
    return (typeof API_BASE !== 'undefined' ? (API_BASE || '') : '').replace(/\/$/, '');
  }

  function pipelineBase() {
    return localBase();
  }

  function jobsStorageKey() {
    var uid = (window.__currentUserId || window.currentUserId || 'anon');
    return 'lobster_seedance_tvc_jobs_' + String(uid || 'anon');
  }

  function loadRecentJobs() {
    try {
      var raw = window.localStorage ? window.localStorage.getItem(jobsStorageKey()) : '';
      var rows = JSON.parse(raw || '[]');
      state.recentJobs = Array.isArray(rows) ? rows.slice(0, RECENT_JOB_LIMIT) : [];
    } catch (e) {
      state.recentJobs = [];
    }
  }

  function saveRecentJobs() {
    try {
      if (window.localStorage) window.localStorage.setItem(jobsStorageKey(), JSON.stringify(state.recentJobs.slice(0, RECENT_JOB_LIMIT)));
    } catch (e) {}
  }

  function rememberJob(job) {
    if (!job || !job.jobId) return;
    var next = state.recentJobs.filter(function(item) { return item && item.jobId !== job.jobId; });
    next.unshift(Object.assign({}, job, { updatedAt: Date.now() }));
    state.recentJobs = next.slice(0, RECENT_JOB_LIMIT);
    saveRecentJobs();
  }

  function updateRememberedJob(jobId, patch) {
    if (!jobId) return;
    state.recentJobs = state.recentJobs.map(function(item) {
      if (!item || item.jobId !== jobId) return item;
      return Object.assign({}, item, patch || {}, { updatedAt: Date.now() });
    });
    saveRecentJobs();
  }

  function mergeRecentJobs(rows) {
    var byId = {};
    (state.recentJobs || []).concat(rows || []).forEach(function(item) {
      if (!item || !item.jobId) return;
      var old = byId[item.jobId] || {};
      byId[item.jobId] = Object.assign({}, old, item, {
        updatedAt: item.updatedAt || old.updatedAt || Date.now()
      });
    });
    state.recentJobs = Object.keys(byId).map(function(id) { return byId[id]; })
      .sort(function(a, b) { return Number(b.updatedAt || 0) - Number(a.updatedAt || 0); })
      .slice(0, RECENT_JOB_LIMIT);
    saveRecentJobs();
  }

  function currentJobAssetId() {
    var current = (state.recentJobs || []).find(function(item) { return item && item.jobId === state.currentJobId; }) || {};
    return String(current.assetId || '').trim();
  }

  function creativeVideoAssetMeta(item) {
    if (!item || typeof item !== 'object') return {};
    var row = item.cloud_asset || item.asset || item || {};
    var meta = row && typeof row.meta === 'object' ? row.meta : {};
    return {
      row: row,
      meta: meta,
      kind: String(item.kind || row.kind || meta.kind || '').trim().toLowerCase(),
      assetId: String(row.asset_id || item.asset_id || '').trim(),
      mediaType: String(row.media_type || item.media_type || row.type || '').trim().toLowerCase(),
      sourceUrl: String(row.source_url || row.preview_url || item.source_url || item.preview_url || '').trim(),
      tags: String(row.tags || item.tags || '').trim().toLowerCase()
    };
  }

  function looksLikeFinalCreativeVideo(item, finalVideo) {
    var meta = creativeVideoAssetMeta(item);
    if (!meta.assetId && !meta.sourceUrl && !meta.kind) return false;
    var finalAssetId = String((finalVideo && finalVideo.asset_id) || '').trim();
    if (finalAssetId && meta.assetId === finalAssetId) return true;
    if (meta.kind === 'merged_final' || meta.kind === 'local_bestseller_captioned') return true;
    if (meta.meta && (meta.meta.seedance_final_video || meta.meta.origin === 'daihuo_merged')) return true;
    if (meta.tags && (meta.tags.indexOf('merged') >= 0 || meta.tags.indexOf('captioned') >= 0)) return true;
    return false;
  }

  function findJobAssetId(job) {
    if (!job || typeof job !== 'object') return '';
    var saved = Array.isArray(job.saved_assets) ? job.saved_assets : [];
    var result = job.result || {};
    var finalVideo = result.final_video || {};
    var finalAssetId = String(finalVideo.asset_id || '').trim();
    if (finalAssetId) return finalAssetId;
    for (var p = 0; p < saved.length; p += 1) {
      var preferred = creativeVideoAssetMeta(saved[p]);
      if (looksLikeFinalCreativeVideo(saved[p], finalVideo) && preferred.assetId) return preferred.assetId;
    }
    for (var i = 0; i < saved.length; i += 1) {
      var item = creativeVideoAssetMeta(saved[i]);
      if (item.assetId && (!item.mediaType || item.mediaType === 'video')) return item.assetId;
    }
    return '';
  }

  function normalizeLocalJob(job) {
    if (!job || !job.job_id) return null;
    var result = job.result || {};
    return {
      jobId: String(job.job_id || ''),
      status: String(job.status || 'running'),
      title: String(job.title || '创意视频任务').trim() || '创意视频任务',
      prompt: String(job.prompt || '').trim(),
      videoUrl: extractResultVideoUrl({ result: result, saved_assets: job.saved_assets || [] }),
      assetId: findJobAssetId(job),
      error: String(job.error || '').trim(),
      progress: job.progress || null,
      progressPercent: job.progress_percent != null ? job.progress_percent : null,
      progressLabel: job.progress_label || '',
      progressDetail: job.progress_detail || '',
      artifacts: normalizeSegmentArtifacts(job.artifacts || null),
      createdAt: Number(job.created_at_ts || 0) * 1000 || 0,
      updatedAt: Number(job.updated_at_ts || 0) * 1000 || Date.now(),
      local: true,
      saved_assets: Array.isArray(job.saved_assets) ? job.saved_assets : [],
      result: result
    };
  }

  function saveVideoAssetToLibrary(assetId, filename) {
    var local = localBase();
    if (!local || !assetId) return Promise.reject(new Error('video asset missing'));
    return fetch(local + '/api/assets/' + encodeURIComponent(assetId) + '/save-to-downloads', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeadersSafe()),
      body: JSON.stringify({ filename: filename || 'creative-video.mp4', open_folder: true })
    })
      .then(function(response) {
        return response.json().catch(function() { return {}; }).then(function(data) {
          if (!response.ok) throw new Error(responseErrorText(data, 'download failed'));
          return data || {};
        });
      });
  }

  function saveRemoteAssetToLibraryAndDownloads(url, mediaType, filename, prompt) {
    var local = localBase();
    var rawUrl = String(url || '').trim();
    var mt = mediaType === 'video' ? 'video' : 'image';
    if (!local || !rawUrl) return Promise.reject(new Error('asset url missing'));
    var body = {
      url: rawUrl,
      media_type: mt,
      tags: 'seedance_tvc,segment',
      prompt: String(prompt || '').trim().slice(0, 500)
    };
    return fetch(local + '/api/assets/save-url', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeadersSafe()),
      body: JSON.stringify(body)
    })
      .then(function(response) {
        return response.json().catch(function() { return {}; }).then(function(data) {
          if (!response.ok || !data || !data.asset_id) {
            throw new Error(responseErrorText(data, '素材入库失败'));
          }
          return data;
        });
      })
      .then(function(data) {
        return fetch(local + '/api/assets/' + encodeURIComponent(data.asset_id) + '/save-to-downloads', {
          method: 'POST',
          headers: Object.assign({ 'Content-Type': 'application/json' }, authHeadersSafe()),
          body: JSON.stringify({ filename: filename || (mt === 'video' ? 'seedance-segment.mp4' : 'seedance-segment.png'), open_folder: true })
        })
          .then(function(response) {
            return response.json().catch(function() { return {}; }).then(function(saveData) {
              if (!response.ok) throw new Error(responseErrorText(saveData, '保存到本机失败'));
              saveData.asset_id = data.asset_id;
              return saveData;
            });
          });
      });
  }

  function downloadUrlForVideo(videoUrl, filename) {
    var local = localBase();
    var safeName = filename || 'seedance-tvc-result.mp4';
    var rawUrl = String(videoUrl || '').trim();
    var absoluteUrl = rawUrl;
    try {
      absoluteUrl = new URL(rawUrl, window.location.origin).href;
    } catch (e) {}
    if (!local) return absoluteUrl || rawUrl;
    return local + '/api/hifly/video/download?url=' + encodeURIComponent(absoluteUrl || rawUrl) + '&filename=' + encodeURIComponent(safeName);
  }

  function openExternalUrl(url) {
    if (!url) return;
    var target = String(url || '').trim();
    try {
      target = new URL(target, window.location.origin).href;
    } catch (e) {}
    try {
      var opened = window.open(target, '_blank', 'noopener');
      if (opened) return;
    } catch (e) {}
    window.location.href = target;
  }

  function seedanceFullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement || null;
  }

  function requestSeedanceFullscreen(el) {
    if (!el) return Promise.reject(new Error('no fullscreen target'));
    var fn = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
    if (!fn) return Promise.reject(new Error('fullscreen unsupported'));
    var result = fn.call(el);
    return result && typeof result.then === 'function' ? result : Promise.resolve();
  }

  function exitSeedanceFullscreen() {
    var fn = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
    if (!fn) return Promise.resolve();
    var result = fn.call(document);
    return result && typeof result.then === 'function' ? result : Promise.resolve();
  }

  function syncSeedanceVideoFullscreenState() {
    var active = seedanceFullscreenElement();
    document.querySelectorAll('[data-seedance-video-shell]').forEach(function(shell) {
      shell.classList.toggle('is-seedance-video-fullscreen', !!active && (active === shell || shell.contains(active)));
    });
  }

  function bindSeedanceVideoFullscreenEvents() {
    if (seedanceVideoFullscreenEventsBound) return;
    seedanceVideoFullscreenEventsBound = true;
    document.addEventListener('click', function(event) {
      var target = event.target;
      var fullBtn = target && target.closest ? target.closest('[data-seedance-video-fullscreen]') : null;
      if (fullBtn) {
        event.preventDefault();
        event.stopPropagation();
        var shell = fullBtn.closest('[data-seedance-video-shell]');
        var video = shell ? shell.querySelector('video') : null;
        if (video) video.play().catch(function() {});
        requestSeedanceFullscreen(shell).then(syncSeedanceVideoFullscreenState).catch(function() {
          showMessage('当前环境不支持全屏预览，可以点击“打开视频”查看。');
        });
        return;
      }
      var exitBtn = target && target.closest ? target.closest('[data-seedance-video-exit]') : null;
      if (exitBtn) {
        event.preventDefault();
        event.stopPropagation();
        exitSeedanceFullscreen().then(syncSeedanceVideoFullscreenState).catch(function() {});
      }
    }, true);
    document.addEventListener('fullscreenchange', syncSeedanceVideoFullscreenState);
    document.addEventListener('webkitfullscreenchange', syncSeedanceVideoFullscreenState);
    document.addEventListener('MSFullscreenChange', syncSeedanceVideoFullscreenState);
    document.addEventListener('keydown', function(event) {
      if (event.key !== 'Escape' || !seedanceFullscreenElement()) return;
      exitSeedanceFullscreen().then(syncSeedanceVideoFullscreenState).catch(function() {});
    }, true);
  }

  function bindResultVideoActions() {
    bindSeedanceVideoFullscreenEvents();
    document.querySelectorAll('[data-seedance-video-download]').forEach(function(btn) {
      btn.onclick = function() {
        var url = btn.getAttribute('data-seedance-video-download') || '';
        var assetId = btn.getAttribute('data-seedance-asset-id') || '';
        var filename = btn.getAttribute('data-download-filename') || 'seedance-tvc-result.mp4';
        if (assetId) {
          saveVideoAssetToLibrary(assetId, filename)
            .then(function(data) {
              var folderText = data && data.directory ? (' / ' + data.directory) : '';
              showMessage((data && data.opened_folder ? '视频已保存并打开文件夹' : '视频已保存') + folderText);
            })
            .catch(function(err) {
              if (url) {
                openExternalUrl(downloadUrlForVideo(url, filename));
                showMessage('素材库保存失败，已改为直接下载。');
                return;
              }
              showMessage('保存视频失败：' + normalizeApiErrorText(err && (err.message || err), '未知错误'));
            });
          return;
        }
        if (!url) return showMessage('视频地址为空，无法下载。');
        openExternalUrl(downloadUrlForVideo(url, filename));
        showMessage('已发起下载。');
      };
    });
    document.querySelectorAll('[data-seedance-video-open]').forEach(function(btn) {
      btn.onclick = function() {
        var url = btn.getAttribute('data-seedance-video-open') || '';
        if (!url) return showMessage('视频地址为空，无法打开。');
        openExternalUrl(url);
      };
    });
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function schedulePoll(delayMs) {
    stopPolling();
    state.pollTimer = setTimeout(function() {
      refreshJobStatus(false);
    }, delayMs || 4000);
  }

  function customSelectLabel(select) {
    if (!select) return '';
    var option = select.options && select.options[select.selectedIndex];
    return option ? option.textContent : '';
  }

  function closeSeedanceCustomSelects(exceptWrap) {
    document.querySelectorAll('#content-seedance-tvc-studio .seedance-custom-select.is-open').forEach(function(wrap) {
      if (exceptWrap && wrap === exceptWrap) return;
      wrap.classList.remove('is-open');
      var button = wrap.querySelector('.seedance-custom-select-button');
      if (button) button.setAttribute('aria-expanded', 'false');
    });
  }

  function syncSeedanceCustomSelect(select) {
    if (!select || !select.dataset.seedanceCustomSelect) return;
    var wrap = select.nextElementSibling;
    if (!wrap || !wrap.classList || !wrap.classList.contains('seedance-custom-select')) return;
    var value = select.value;
    var label = customSelectLabel(select);
    var labelEl = wrap.querySelector('.seedance-custom-select-label');
    if (labelEl) labelEl.textContent = label;
    wrap.querySelectorAll('[data-custom-select-value]').forEach(function(item) {
      var active = item.getAttribute('data-custom-select-value') === value;
      item.classList.toggle('is-selected', active);
      item.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  }

  function buildSeedanceCustomSelect(select) {
    if (!select || select.dataset.seedanceCustomSelect === '1') return;
    select.dataset.seedanceCustomSelect = '1';
    select.classList.add('seedance-native-select-hidden');

    var wrap = document.createElement('div');
    wrap.className = 'seedance-custom-select';
    wrap.setAttribute('data-custom-select-for', select.id || '');

    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'seedance-custom-select-button';
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    button.innerHTML = '<span class="seedance-custom-select-label"></span><span class="seedance-custom-select-arrow" aria-hidden="true"></span>';

    var menu = document.createElement('div');
    menu.className = 'seedance-custom-select-menu';
    menu.setAttribute('role', 'listbox');

    Array.prototype.forEach.call(select.options || [], function(option) {
      var item = document.createElement('button');
      item.type = 'button';
      item.className = 'seedance-custom-select-option';
      item.setAttribute('role', 'option');
      item.setAttribute('data-custom-select-value', option.value);
      item.textContent = option.textContent || option.value;
      item.addEventListener('click', function(event) {
        event.preventDefault();
        event.stopPropagation();
        select.value = option.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        syncSeedanceCustomSelect(select);
        closeSeedanceCustomSelects();
      });
      menu.appendChild(item);
    });

    button.addEventListener('click', function(event) {
      event.preventDefault();
      event.stopPropagation();
      var willOpen = !wrap.classList.contains('is-open');
      closeSeedanceCustomSelects(wrap);
      wrap.classList.toggle('is-open', willOpen);
      button.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });

    wrap.appendChild(button);
    wrap.appendChild(menu);
    select.parentNode.insertBefore(wrap, select.nextSibling);
    syncSeedanceCustomSelect(select);
  }

  function initSeedanceCustomSelects() {
    [
      'seedanceAspectRatioSelect',
      'seedanceVisualToneSelect',
      'seedanceRhythmSelect',
      'seedanceModelSelect'
    ].forEach(function(id) {
      buildSeedanceCustomSelect($(id));
    });
    if (!customSelectEventsBound) {
      customSelectEventsBound = true;
      document.addEventListener('click', function(event) {
        if (event.target && event.target.closest && event.target.closest('#content-seedance-tvc-studio .seedance-custom-select')) return;
        closeSeedanceCustomSelects();
      });
      document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') closeSeedanceCustomSelects();
      });
    }
  }

  function syncSeedanceCustomSelects() {
    [
      'seedanceAspectRatioSelect',
      'seedanceVisualToneSelect',
      'seedanceRhythmSelect',
      'seedanceModelSelect'
    ].forEach(function(id) {
      syncSeedanceCustomSelect($(id));
    });
  }

  function getFormValues() {
    return {
      aspectRatio: $('seedanceAspectRatioSelect').value,
      visualTone: $('seedanceVisualToneSelect').value,
      rhythm: $('seedanceRhythmSelect').value,
      model: $('seedanceModelSelect').value,
      needAudio: !!$('seedanceNeedAudioCheck').checked,
      needMerge: !!$('seedanceNeedMergeCheck').checked,
      prompt: $('seedanceTaskPromptInput').value.trim()
    };
  }

  function resetFormFields() {
    $('seedanceAspectRatioSelect').value = defaults.aspectRatio;
    $('seedanceVisualToneSelect').value = defaults.visualTone;
    $('seedanceRhythmSelect').value = defaults.rhythm;
    $('seedanceModelSelect').value = defaults.model;
    lastSeedanceModel = defaults.model;
    $('seedanceNeedAudioCheck').checked = defaults.needAudio;
    $('seedanceNeedMergeCheck').checked = defaults.needMerge;
    $('seedanceTaskPromptInput').value = defaults.prompt;
    $('seedanceImageFileInput').value = '';
    if ($('seedanceReferencePurposeSelect')) $('seedanceReferencePurposeSelect').value = 'storyboard';
    syncSeedanceCustomSelects();
  }

  function setMode(mode) {
    if (!modeMeta[mode]) mode = 'image_auto';
    state.mode = mode;
    var modeSelect = $('seedanceInputModeSelect');
    if (modeSelect) modeSelect.value = mode;
    document.querySelectorAll('[data-seedance-input-mode]').forEach(function(tab) {
      var active = tab.getAttribute('data-seedance-input-mode') === mode;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    if ($('seedanceImageField')) {
      $('seedanceImageField').style.display = mode === 'prompt_only' ? 'none' : '';
    }
    if ($('seedanceInputModeHint')) {
      $('seedanceInputModeHint').textContent = modeMeta[mode].hint;
    }
  }

  function setDuration(duration) {
    var segmentSeconds = getCurrentSegmentSeconds();
    var normalized = Math.max(1, Math.round((Number(duration) || segmentSeconds) / segmentSeconds)) * segmentSeconds;
    state.duration = normalized;
    document.querySelectorAll('#seedanceDurationGrid .tvc-duration-chip').forEach(function(chip) {
      chip.classList.toggle('is-active', Number(chip.getAttribute('data-duration')) === normalized);
    });
  }

  function releaseMediaItems(items) {
    (items || []).forEach(function(item) {
      if (item && item.url && String(item.url).indexOf('blob:') === 0) {
        try {
          URL.revokeObjectURL(item.url);
        } catch (err) {}
      }
    });
  }

  function readFiles(fileList) {
    var purpose = (($('seedanceReferencePurposeSelect') || {}).value || 'storyboard').trim() || 'storyboard';
    return Array.prototype.slice.call(fileList || []).map(function(file) {
      return {
        name: file.name,
        size: file.size,
        type: file.type,
        url: URL.createObjectURL(file),
        objectUrl: '',
        source_url: '',
        preview_url: '',
        open_url: '',
        file: file,
        purpose: purpose
      };
    });
  }

  function resolveAssetMediaUrl(asset) {
    if (!asset) return '';
    return cleanRemoteUrl(asset.preview_url || asset.open_url || asset.source_url || '');
  }

  function existingImageAssetIds() {
    var map = {};
    (state.images || []).forEach(function(item) {
      var aid = String((item && item.asset_id) || '').trim();
      if (aid) map[aid] = true;
    });
    return map;
  }

  function normalizeAssetPickerItem(asset) {
    var aid = String((asset && asset.asset_id) || '').trim();
    if (!aid) return null;
    var mediaType = String((asset && asset.media_type) || '').toLowerCase();
    if (mediaType && mediaType !== 'image') return null;
    return {
      asset_id: aid,
      name: String((asset && asset.filename) || aid).trim() || aid,
      size: Number((asset && asset.file_size) || 0),
      url: resolveAssetMediaUrl(asset),
      source_url: String((asset && asset.source_url) || '').trim(),
      preview_url: String((asset && asset.preview_url) || '').trim(),
      open_url: String((asset && asset.open_url) || '').trim(),
      prompt: String((asset && asset.prompt) || '').trim(),
      purpose: (($('seedanceReferencePurposeSelect') || {}).value || 'storyboard').trim() || 'storyboard'
    };
  }

  function selectedAssetPickerItems() {
    var selected = assetPickerState.selected || {};
    return (assetPickerState.items || []).filter(function(item) {
      return item && selected[item.asset_id];
    });
  }

  function appendMediaItems(existing, incoming, maxCount) {
    var next = (existing || []).slice();
    var existingAssets = {};
    next.forEach(function(item) {
      var aid = String((item && item.asset_id) || '').trim();
      if (aid) existingAssets[aid] = true;
    });
    (incoming || []).forEach(function(item) {
      if (typeof maxCount === 'number' && next.length >= maxCount) return;
      var aid = String((item && item.asset_id) || '').trim();
      if (aid && existingAssets[aid]) return;
      if (aid) existingAssets[aid] = true;
      next.push(item);
    });
    return next;
  }

  function renderUploadList(targetId, items) {
    var el = $(targetId);
    if (!el) return;
    if (!items.length) {
      el.innerHTML = '<div class="tvc-empty-slot" style="grid-column:1 / -1;">还没有参考图片</div>';
      return;
    }

    el.innerHTML = items.map(function(item, index) {
      return [
        '<div class="tvc-upload-card">',
        '<button type="button" class="tvc-upload-remove" data-index="' + index + '" aria-label="remove image" title="移除">X</button>',
        '<img src="' + escapeHtml(item.url) + '" alt="' + escapeHtml(item.name || ('参考图 ' + (index + 1))) + '">',
        '<div class="tvc-upload-card-body">',
        '<div class="tvc-upload-card-title">' + escapeHtml(item.name || ('参考图 ' + (index + 1))) + '</div>',
        '<div class="tvc-upload-card-meta">' + escapeHtml(formatFileSize(item.size)) + '</div>',
        '<select class="seedance-ref-purpose" data-ref-purpose-index="' + index + '">',
        Object.keys(PURPOSE_LABELS).map(function(key) {
          return '<option value="' + key + '"' + ((item.purpose || 'storyboard') === key ? ' selected' : '') + '>' + escapeHtml(PURPOSE_LABELS[key]) + '</option>';
        }).join(''),
        '</select>',
        '</div>',
        '</div>'
      ].join('');
    }).join('');
  }

  function ensureAssetPickerModal() {
    var modal = $('seedanceAssetPickerModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'seedanceAssetPickerModal';
    modal.className = 'seedance-asset-picker-modal';
    modal.setAttribute('aria-hidden', 'true');
    modal.innerHTML = [
      '<div class="seedance-asset-picker-card" role="dialog" aria-modal="true" aria-labelledby="seedanceAssetPickerTitle">',
      '<div class="seedance-asset-picker-head">',
      '<div>',
      '<h4 id="seedanceAssetPickerTitle">选择素材库图片</h4>',
      '<p>可以选择一张或多张图片，确认后直接作为参考图使用。</p>',
      '</div>',
      '<button type="button" class="seedance-asset-picker-close" aria-label="关闭">X</button>',
      '</div>',
      '<div class="seedance-asset-picker-tools">',
      '<input type="search" id="seedanceAssetPickerSearch" placeholder="搜索文件名、提示词或标签">',
      '<button type="button" class="btn btn-ghost btn-sm" id="seedanceAssetPickerReload">刷新</button>',
      '</div>',
      '<div id="seedanceAssetPickerStatus" class="seedance-asset-picker-status"></div>',
      '<div id="seedanceAssetPickerGrid" class="seedance-asset-picker-grid"></div>',
      '<div class="seedance-asset-picker-foot">',
      '<span id="seedanceAssetPickerCount">已选择 0 张</span>',
      '<div class="seedance-asset-picker-actions">',
      '<button type="button" class="btn btn-ghost" id="seedanceAssetPickerCancel">取消</button>',
      '<button type="button" class="btn btn-primary" id="seedanceAssetPickerConfirm">确认使用</button>',
      '</div>',
      '</div>',
      '</div>'
    ].join('');
    document.body.appendChild(modal);

    modal.addEventListener('click', function(event) {
      if (event.target === modal) closeAssetPicker();
      var closeBtn = event.target && event.target.closest ? event.target.closest('.seedance-asset-picker-close, #seedanceAssetPickerCancel') : null;
      if (closeBtn) {
        event.preventDefault();
        closeAssetPicker();
      }
      var card = event.target && event.target.closest ? event.target.closest('[data-seedance-asset-id]') : null;
      if (card) {
        event.preventDefault();
        toggleAssetPickerSelection(card.getAttribute('data-seedance-asset-id'));
      }
    });

    var search = modal.querySelector('#seedanceAssetPickerSearch');
    if (search) {
      search.addEventListener('input', function(event) {
        assetPickerState.query = event.target.value || '';
        renderAssetPicker();
      });
    }
    var reload = modal.querySelector('#seedanceAssetPickerReload');
    if (reload) {
      reload.addEventListener('click', function() {
        loadAssetPickerItems(true);
      });
    }
    var confirm = modal.querySelector('#seedanceAssetPickerConfirm');
    if (confirm) {
      confirm.addEventListener('click', confirmAssetPicker);
    }
    document.addEventListener('keydown', function(event) {
      if (event.key === 'Escape' && modal.classList.contains('is-visible')) closeAssetPicker();
    });
    return modal;
  }

  function filteredAssetPickerItems() {
    var query = String(assetPickerState.query || '').trim().toLowerCase();
    if (!query) return assetPickerState.items || [];
    return (assetPickerState.items || []).filter(function(item) {
      var haystack = [item.name || '', item.asset_id || '', item.prompt || ''].join(' ').toLowerCase();
      return haystack.indexOf(query) >= 0;
    });
  }

  function renderAssetPicker() {
    var modal = ensureAssetPickerModal();
    var grid = modal.querySelector('#seedanceAssetPickerGrid');
    var status = modal.querySelector('#seedanceAssetPickerStatus');
    var count = modal.querySelector('#seedanceAssetPickerCount');
    var confirm = modal.querySelector('#seedanceAssetPickerConfirm');
    if (!grid || !status) return;
    var selected = selectedAssetPickerItems();
    if (count) count.textContent = '已选择 ' + selected.length + ' 张';
    if (confirm) confirm.disabled = selected.length < 1;

    if (assetPickerState.loading) {
      status.textContent = '正在加载素材库图片...';
      grid.innerHTML = '<div class="seedance-asset-picker-empty">正在加载...</div>';
      return;
    }
    var items = filteredAssetPickerItems();
    if (!assetPickerState.items.length) {
      status.textContent = '素材库暂无图片';
      grid.innerHTML = '<div class="seedance-asset-picker-empty">暂无图片素材。你也可以继续使用“上传图片”。</div>';
      return;
    }
    if (!items.length) {
      status.textContent = '没有匹配的图片';
      grid.innerHTML = '<div class="seedance-asset-picker-empty">换个关键词试试。</div>';
      return;
    }
    status.textContent = '共 ' + assetPickerState.items.length + ' 张图片，当前显示 ' + items.length + ' 张';
    var existing = existingImageAssetIds();
    grid.innerHTML = items.map(function(item) {
      var selectedClass = assetPickerState.selected[item.asset_id] ? ' is-selected' : '';
      var already = existing[item.asset_id] ? ' is-existing' : '';
      var thumb = item.url
        ? '<img src="' + escapeHtml(item.url) + '" alt="' + escapeHtml(item.name) + '">'
        : '<div class="seedance-asset-picker-no-thumb">无预览</div>';
      return [
        '<button type="button" class="seedance-asset-picker-item' + selectedClass + already + '" data-seedance-asset-id="' + escapeHtml(item.asset_id) + '">',
        '<span class="seedance-asset-picker-thumb">' + thumb + '</span>',
        '<span class="seedance-asset-picker-name" title="' + escapeHtml(item.name) + '">' + escapeHtml(item.name) + '</span>',
        '<span class="seedance-asset-picker-meta">' + escapeHtml(formatFileSize(item.size)) + (already ? ' · 已添加' : '') + '</span>',
        '<span class="seedance-asset-picker-check">✓</span>',
        '</button>'
      ].join('');
    }).join('');
  }

  function toggleAssetPickerSelection(assetId) {
    assetId = String(assetId || '').trim();
    if (!assetId) return;
    if (assetPickerState.selected[assetId]) delete assetPickerState.selected[assetId];
    else assetPickerState.selected[assetId] = true;
    renderAssetPicker();
  }

  function loadAssetPickerItems(force) {
    var modal = ensureAssetPickerModal();
    if (!force && assetPickerState.items.length) {
      renderAssetPicker();
      return Promise.resolve(assetPickerState.items);
    }
    var base = pipelineBase();
    if (!base) {
      assetPickerState.items = [];
      renderAssetPicker();
      return Promise.reject(new Error('当前未检测到可用的后端地址，无法读取素材库'));
    }
    assetPickerState.loading = true;
    renderAssetPicker();
    return fetch(base + '/api/assets?media_type=image&limit=100', { headers: authHeadersSafe() })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) throw new Error(responseErrorText(result.data, '素材库图片加载失败'));
        var rows = Array.isArray(result.data.assets) ? result.data.assets : [];
        assetPickerState.items = rows.map(normalizeAssetPickerItem).filter(Boolean);
        assetPickerState.loading = false;
        renderAssetPicker();
        return assetPickerState.items;
      })
      .catch(function(err) {
        assetPickerState.loading = false;
        var status = modal.querySelector('#seedanceAssetPickerStatus');
        if (status) status.textContent = (err && err.message) || '素材库图片加载失败';
        renderAssetPicker();
        throw err;
      });
  }

  function openAssetPicker() {
    var modal = ensureAssetPickerModal();
    assetPickerState.selected = {};
    assetPickerState.query = '';
    var search = modal.querySelector('#seedanceAssetPickerSearch');
    if (search) search.value = '';
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    loadAssetPickerItems(false).catch(function(err) {
      showMessage((err && err.message) || '素材库图片加载失败');
    });
  }

  function closeAssetPicker() {
    var modal = $('seedanceAssetPickerModal');
    if (!modal) return;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
  }

  function confirmAssetPicker() {
    var items = selectedAssetPickerItems();
    if (!items.length) return;
    var incoming = items.map(function(item) {
      return {
        name: item.name,
        size: item.size,
        type: 'image',
        url: item.url || item.source_url,
        source_url: item.source_url || item.url,
        asset_id: item.asset_id,
        purpose: (($('seedanceReferencePurposeSelect') || {}).value || item.purpose || 'storyboard').trim() || 'storyboard'
      };
    }).filter(function(item) {
      return item.asset_id && item.url;
    });
    var before = state.images.length;
    state.images = appendMediaItems(state.images, incoming);
    state.activeBoardIndex = 0;
    closeAssetPicker();
    renderWorkspace();
    var added = state.images.length - before;
    showMessage(added > 0 ? ('已从素材库添加 ' + added + ' 张参考图。') : '选择的图片已在参考图列表中。');
  }

  function updateReferencePurpose(index, purpose) {
    if (!state.images[index]) return;
    state.images[index].purpose = purpose || 'storyboard';
    renderWorkspace();
  }

  function removeMediaItem(index) {
    var next = state.images.slice();
    var removed = next.splice(index, 1)[0];
    if (removed) releaseMediaItems([removed]);
    state.images = next;
    state.activeBoardIndex = 0;
    renderWorkspace();
    showMessage('');
  }

  function bindUploadListRemoval() {
    var el = $('seedanceImageList');
    if (!el) return;
    el.addEventListener('click', function(event) {
      var btn = event.target && event.target.closest ? event.target.closest('.tvc-upload-remove') : null;
      if (!btn) return;
      event.preventDefault();
      removeMediaItem(Number(btn.getAttribute('data-index')) || 0);
    });
    el.addEventListener('change', function(event) {
      var sel = event.target && event.target.closest ? event.target.closest('.seedance-ref-purpose') : null;
      if (!sel) return;
      updateReferencePurpose(Number(sel.getAttribute('data-ref-purpose-index')) || 0, sel.value);
    });
  }

  function shortenText(text, maxLength) {
    var clean = String(text || '').replace(/\s+/g, ' ').trim();
    if (!clean) return '';
    if (clean.length <= maxLength) return clean;
    return clean.slice(0, maxLength) + '...';
  }

  function renderExamplesPanel() {
    var panel = $('seedanceExamplesPanel');
    var grid = $('seedanceExamplesGrid');
    var status = $('seedanceExamplesStatus');
    if (!panel || !grid || !status) return;

    updateExamplesToggle();
    if (!state.examplesOpen) {
      panel.hidden = true;
      panel.classList.remove('is-visible');
      updateExamplesMoreButton();
      return;
    }

    panel.hidden = false;
    panel.classList.add('is-visible');
    updateExamplesBadge();

    if (state.examplesLoading && !state.exampleCatalog.length) {
      status.textContent = '正在加载案例库...';
      grid.innerHTML = '<div class="tvc-empty-slot" style="grid-column:1 / -1;">正在加载案例视频与提示词...</div>';
      updateExamplesMoreButton();
      return;
    }

    if (!state.exampleCatalog.length) {
      status.textContent = '暂时没有案例数据';
      grid.innerHTML = '<div class="tvc-empty-slot" style="grid-column:1 / -1;">案例库暂时为空，请稍后再试。</div>';
      updateExamplesMoreButton();
      return;
    }

    var filteredItems = filteredExampleCatalog();
    if (!filteredItems.length) {
      status.textContent = '没有匹配的案例';
      grid.innerHTML = '<div class="tvc-empty-slot" style="grid-column:1 / -1;">换个分类或关键词试试。</div>';
      updateExamplesMoreButton();
      return;
    }

    var total = filteredItems.length;
    var visibleItems = filteredItems.slice(0, Math.min(visibleExampleCount(), total));
    status.textContent = '已加载 ' + visibleItems.length + ' / ' + total + ' 条视频灵感案例';
    grid.innerHTML = visibleItems.map(function(item) {
      var tags = (item.tags || []).slice(0, 3);
      if (item.language) tags.push(item.language.toUpperCase());
      var media = item.video_url
        ? '<video src="' + escapeHtml(item.video_url) + '"' + (item.cover_image ? ' poster="' + escapeHtml(item.cover_image) + '"' : '') + ' muted loop playsinline preload="metadata"></video>'
        : (item.cover_image ? '<img src="' + escapeHtml(item.cover_image) + '" alt="' + escapeHtml(item.title) + '">' : '');
      return [
        '<div class="tvc-case-card' + (state.activeExampleId === item.id ? ' is-active' : '') + '" data-example-id="' + escapeHtml(item.id) + '" data-example-apply="' + escapeHtml(item.id) + '" role="button" tabindex="0">',
        '<div class="tvc-case-thumb"' + (item.video_url ? ' data-example-video="' + escapeHtml(item.id) + '"' : '') + '>',
        media,
        '<div class="tvc-case-badges">',
        '<span class="tvc-case-badge is-featured">灵感案例</span>',
        '<span class="tvc-case-badge">' + escapeHtml(item.model) + '</span>',
        '</div>',
        '<div class="tvc-case-overlay-title">' + escapeHtml(item.title) + '</div>',
        '</div>',
        '<div class="tvc-case-body">',
        '<p class="tvc-case-copy">' + escapeHtml(shortenText(item.prompt, 220)) + '</p>',
        '<div class="tvc-case-tags">' + tags.map(function(tag) {
          return '<span>' + escapeHtml(tag) + '</span>';
        }).join('') + '</div>',
        '<div class="tvc-case-actions">',
        '<button type="button" class="btn btn-primary btn-sm" data-example-apply="' + escapeHtml(item.id) + '">带入提示词</button>',
        (item.video_url ? '<button type="button" class="btn btn-ghost btn-sm" data-example-video="' + escapeHtml(item.id) + '">播放案例视频</button>' : '<span class="btn btn-ghost btn-sm" style="pointer-events:none;opacity:0.55;">暂无视频</span>'),
        '</div>',
        '</div>',
        '</div>'
      ].join('');
    }).join('');
    updateExamplesMoreButton();
  }

  function ensureExampleCatalog() {
    if (state.exampleCatalog.length) {
      updateExamplesBadge();
      return Promise.resolve(state.exampleCatalog);
    }

    state.examplesLoading = true;
    renderExamplesPanel();

    return fetch('/static/data/comfly-seedance-tvc-examples.json', { cache: 'no-store' })
      .then(function(response) {
        if (!response.ok) throw new Error('案例库加载失败');
        return response.json();
      })
      .then(function(payload) {
        var items = Array.isArray(payload) ? payload : (payload && Array.isArray(payload.prompts) ? payload.prompts : []);
        state.exampleCatalog = items.map(normalizeExampleItem).filter(function(item) {
          return item.id && item.title && item.prompt;
        }).sort(function(a, b) {
          if (!!a.is_featured === !!b.is_featured) return 0;
          return a.is_featured ? -1 : 1;
        });
        state.exampleFeaturedCount = state.exampleCatalog.filter(function(item) { return item.is_featured; }).length;
        state.exampleVisibleCount = Math.min(state.examplePageSize, state.exampleCatalog.length);
        updateExamplesBadge();
        return state.exampleCatalog;
      })
      .catch(function(err) {
        state.exampleCatalog = [];
      state.exampleFeaturedCount = 0;
      state.exampleVisibleCount = 0;
      updateExamplesBadge();
        showMessage('案例库加载失败：' + (err && err.message ? err.message : '未知错误'));
        return [];
      })
      .finally(function() {
        state.examplesLoading = false;
        renderExamplesPanel();
      });
  }

  function buildBoards() {
    var values = getFormValues();
    var segmentSeconds = getCurrentSegmentSeconds(values.model);
    var count = getDurationSegmentCount(state.duration, values.model);
    var promptSnippet = shortenText(values.prompt, 42);
    var userPrompt = String(values.prompt || '').trim();
    var useUserPromptForEverySegment = state.mode === 'image_prompt' && !!userPrompt;
    var boards = [];

    for (var i = 0; i < count; i += 1) {
      var seed = narrativeSeeds[i] || narrativeSeeds[narrativeSeeds.length - 1];
      var media = state.images.length ? state.images[i % state.images.length] : null;
      var copy = useUserPromptForEverySegment ? userPrompt : seed.copy;
      if (!useUserPromptForEverySegment && promptSnippet) {
        copy += ' 当前提示重点：' + promptSnippet;
      }
      boards.push({
        index: i,
        start: i * segmentSeconds,
        end: (i + 1) * segmentSeconds,
        title: useUserPromptForEverySegment ? ('第 ' + (i + 1) + ' 段') : seed.title,
        copy: copy,
        media: media
      });
    }

    if (state.activeBoardIndex >= boards.length) {
      state.activeBoardIndex = 0;
    }
    return boards;
  }

  function renderBoards(boards) {
    if ($('seedanceBoardsCounter')) {
      $('seedanceBoardsCounter').textContent = boards.length + ' 段';
    }
    if ($('seedanceBoardsHint')) {
      $('seedanceBoardsHint').textContent = '';
    }
    if (!$('seedanceStoryboardStrip')) return;
    $('seedanceStoryboardStrip').innerHTML = renderSegmentBoard(boards);
    bindSegmentBoardActions();
  }

  function resultVideoHtml(values, boards) {
    var detailText = jobDetailText({
      error: state.currentJobError,
      progress: state.currentJobProgress
    });

    if (state.currentResultVideoUrl) {
      var resultUrl = String(state.currentResultVideoUrl);
      var bare = resultUrl.split('?')[0].toLowerCase();
      var looksVideo = /\.(mp4|mov|m4v|webm|mkv)$/.test(bare)
        || bare.indexOf('/api/comfly-ecommerce-detail/local-file/') >= 0;
      if (looksVideo) {
        return [
          '<div class="seedance-result-video-wrap">',
          '<div class="seedance-result-video-frame" data-seedance-video-shell>',
          '<video src="' + escapeHtml(resultUrl) + '" controls controlsList="nodownload nofullscreen" playsinline preload="metadata"></video>',
          '<button type="button" class="seedance-video-fs-btn" data-seedance-video-fullscreen>放大查看</button>',
          '<div class="seedance-video-fullscreen-hint">按 Esc 退出全屏</div>',
          '<button type="button" class="seedance-video-exit-btn" data-seedance-video-exit>退出全屏 Esc</button>',
          '</div>',
          '<div class="seedance-result-video-actions">',
          '<button type="button" class="btn btn-primary btn-sm" data-seedance-video-download="' + escapeHtml(resultUrl) + '" data-seedance-asset-id="' + escapeHtml(currentJobAssetId()) + '" data-download-filename="seedance-tvc-result.mp4">下载视频</button>',
          '<button type="button" class="btn btn-ghost btn-sm" data-seedance-video-open="' + escapeHtml(resultUrl) + '">打开视频</button>',
          '</div>',
          '</div>'
        ].join('');
      }
      return [
        '<div class="tvc-video-placeholder">',
        '<strong>任务已完成</strong>',
        '<span>最终视频已经生成，但当前预览地址暂时不是可直接内嵌播放的视频链接（' + escapeHtml(resultUrl.slice(0, 200)) + '）。你可以先在下方任务卡片或素材库中查看成片。</span>',
        '</div>'
      ].join('');
    }

    if (state.currentJobStatus === 'running') {
      var runningHint = '高质量模型生成通常需要 5-10 分钟。你可以继续提交新任务，或先去使用其他功能；任务完成后会在右下角提醒你。';
      var progressText = currentProgressText() || detailText;
      var runningText = progressText ? (progressText + '。' + runningHint) : runningHint;
      var progressHtml = progressBarHtml(currentProgressPercent(), progressText);
      return [
        '<div class="tvc-video-placeholder is-busy">',
        '<div class="tvc-status-head">',
        '<span class="tvc-status-spinner" aria-hidden="true"></span>',
        '<div>',
        '<strong>视频正在生成中</strong>',
        '<span>' + escapeHtml(runningText) + '</span>',
        '</div>',
        '</div>',
        progressHtml,
        '</div>'
      ].join('');
    }

    if (state.currentJobStatus === 'completed') {
      return [
        '<div class="tvc-video-placeholder">',
        '<strong>任务已完成</strong>',
        '<span>' + escapeHtml(detailText || '最终视频已生成，正在同步预览与素材库地址，请稍后刷新或到素材库查看。') + '</span>',
        '</div>'
      ].join('');
    }

    if (state.currentJobStatus === 'failed') {
      return [
        '<div class="tvc-video-placeholder">',
        '<strong>视频生成失败</strong>',
        '<span>' + escapeHtml(detailText || '请调整素材或参数后重新提交任务。') + '</span>',
        '</div>'
      ].join('');
    }

    var summary = state.duration + ' 秒 / ' + boards.length + ' 张分镜 / ' + values.aspectRatio;
    var detail = state.mode === 'prompt_only'
      ? '当前是纯提示词规划模式，会直接按提示词规划分镜并生成视频。'
      : '点击“开始生成视频”后，这里会展示最后合成的视频结果。';

    return [
      '<div class="tvc-video-placeholder">',
      '<strong>最终结果视频展示区</strong>',
      '<span>' + escapeHtml(summary + '。' + detail) + '</span>',
      '</div>'
    ].join('');
  }

  function segmentStatusLabel(status) {
    if (status === 'video_ready') return '视频完成';
    if (status === 'image_ready') return '图片完成';
    if (status === 'failed') return '失败';
    if (status === 'running') return '生成中';
    return '等待中';
  }

  function segmentDisplayStatus(seg) {
    if (!seg || typeof seg !== 'object') return '等待中';
    if (seg.error || seg.status === 'failed') return '失败';
    if (seg.videoUrl) return '视频完成';
    if (seg.imageUrl) return '视频合成中';
    return '图片合成中';
  }

  function fallbackSegmentImageUrl(index) {
    var list = state.images || [];
    if (!list.length) return '';
    var pos = Math.max(0, (Number(index || 1) || 1) - 1) % list.length;
    var item = list[pos] || {};
    return mediaItemUrl(item);
  }

  function isReferenceImageSegment(seg) {
    if (!seg || typeof seg !== 'object') return false;
    var workflow = String(seg.workflowMode || seg.workflow_mode || '').toLowerCase().replace(/-/g, '_');
    var source = String(seg.imageSource || seg.image_source || seg.source || '').toLowerCase();
    return workflow === 'direct_video'
      || source === 'uploaded_reference_image'
      || source === 'reference_image'
      || !!seg.usesReferenceImage;
  }

  function displayImagePrompt(seg) {
    if (isReferenceImageSegment(seg)) return REFERENCE_IMAGE_PROMPT_TEXT;
    if (!seg) return '';
    return seg.imagePrompt || seg.prompt || seg.videoPrompt || '';
  }

  function segmentPromptButton(prompt, action, label, disabled, extraClass) {
    return '<button type="button" class="btn btn-sm seedance-segment-action' + (extraClass ? ' ' + escapeHtml(extraClass) : '') + '" data-seedance-segment-' + action + '="' + escapeHtml(prompt || '') + '"' + (disabled ? ' disabled' : '') + '>' + escapeHtml(label) + '</button>';
  }

  function segmentPromptPanel(kind, segIndex, prompt) {
    var label = kind === 'video' ? '视频提示词' : '图片提示词';
    return [
      '<label class="seedance-segment-prompt-panel">',
      '<span class="seedance-segment-prompt-head">',
      '<strong>' + label + '</strong>',
      segmentPromptButton(prompt, 'copy', '复制', !prompt),
      '</span>',
      '<textarea data-seedance-segment-' + kind + '-prompt="' + escapeHtml(String(segIndex || '')) + '">' + escapeHtml(prompt || '') + '</textarea>',
      '</label>'
    ].join('');
  }

  function segmentMediaPanel(kind, seg, prompt) {
    var isVideo = kind === 'video';
    var url = String(isVideo ? (seg.videoUrl || '') : (seg.imageUrl || '')).trim();
    var title = (isVideo ? '视频结果' : '图片结果') + ' · 片段 ' + (seg.index || '');
    var retryAction = isVideo ? 'retry-video' : 'retry-image';
    var retryText = isVideo ? '重新合成视频' : '重新合成图片';
    var filename = 'seedance-segment-' + (seg.index || '1') + (isVideo ? '.mp4' : '.png');
    var mediaBody = '';
    if (url) {
      mediaBody = isVideo
        ? '<video src="' + escapeHtml(url) + '" muted playsinline preload="metadata"></video><em>视频已出</em>'
        : '<img src="' + escapeHtml(url) + '" alt="' + escapeHtml(title) + '"><em>图片已出</em>';
      mediaBody = [
        '<button type="button" class="seedance-segment-media-card' + (isVideo ? ' is-video' : '') + '"',
        ' data-seedance-segment-preview="' + escapeHtml(url) + '"',
        ' data-seedance-media-type="' + (isVideo ? 'video' : 'image') + '"',
        ' data-seedance-media-title="' + escapeHtml(title) + '"',
        ' data-download-filename="' + escapeHtml(filename) + '"',
        ' data-seedance-media-prompt="' + escapeHtml(prompt || '') + '">',
        mediaBody,
        '<span class="seedance-segment-open-hint">点击预览</span>',
        '</button>'
      ].join('');
    } else {
      var waitingTitle = isVideo ? '视频正在合成中' : '图片正在合成中';
      var waitingCopy = isVideo ? '可能需要 5-10 分钟，完成后会自动展示结果。' : '可能需要 1-2 分钟，完成后会自动展示结果。';
      mediaBody = [
        '<div class="seedance-segment-media-card is-waiting' + (isVideo ? ' is-video' : ' is-image') + '">',
        '<strong>' + waitingTitle + '</strong>',
        '<small>' + waitingCopy + '</small>',
        '</div>'
      ].join('');
    }

    return [
      '<div class="seedance-segment-result-panel">',
      '<div class="seedance-segment-result-head">',
      '<strong>' + (isVideo ? '视频结果' : '图片结果') + '</strong>',
      '<div class="seedance-segment-inline-actions">',
      segmentPromptButton(prompt, retryAction, retryText, !prompt, 'is-retry'),
      url ? '<button type="button" class="btn btn-sm seedance-segment-action" data-seedance-segment-download="' + escapeHtml(url) + '" data-seedance-media-type="' + (isVideo ? 'video' : 'image') + '" data-download-filename="' + escapeHtml(filename) + '" data-seedance-media-prompt="' + escapeHtml(prompt || '') + '">下载</button>' : '',
      '</div>',
      '</div>',
      mediaBody,
      '</div>'
    ].join('');
  }

  function renderSegmentBoard(boards) {
    var artifacts = state.currentSegmentArtifacts;
    var segments = artifacts && Array.isArray(artifacts.segments) ? artifacts.segments : [];
    if (!segments.length && Array.isArray(boards)) {
      segments = boards.map(function(board) {
        var prompt = (board.copy || board.title || '').trim();
        var media = board.media || null;
        var usesReferenceImage = state.mode === 'image_prompt' && !!mediaItemUrl(media);
        return {
          index: Number(board.index || 0) + 1,
          start: board.start,
          end: board.end,
          status: 'pending',
          imagePrompt: usesReferenceImage ? REFERENCE_IMAGE_PROMPT_TEXT : prompt,
          videoPrompt: prompt,
          imageUrl: mediaItemUrl(media),
          videoUrl: '',
          workflowMode: usesReferenceImage ? 'direct_video' : '',
          imageSource: usesReferenceImage ? 'uploaded_reference_image' : '',
          usesReferenceImage: usesReferenceImage,
          provider: '',
          model: '',
          taskId: '',
          error: ''
        };
      });
    } else {
      segments = segments.map(function(seg) {
        seg = seg && typeof seg === 'object' ? Object.assign({}, seg) : {};
        if (!String(seg.imageUrl || '').trim()) {
          var fallbackImageUrl = fallbackSegmentImageUrl(seg.index);
          if (fallbackImageUrl) {
            seg.imageUrl = fallbackImageUrl;
            seg.usesReferenceImage = true;
          }
        }
        return seg;
      });
    }
    if (!segments.length) return '';
    var total = artifacts && artifacts.segmentCount ? artifacts.segmentCount : segments.length;
    var imageReady = segments.filter(function(item) { return !!item.imageUrl; }).length;
    var videoReady = artifacts && artifacts.videoReadyCount ? artifacts.videoReadyCount : segments.filter(function(item) { return !!item.videoUrl; }).length;
    var failed = artifacts && artifacts.failedCount ? artifacts.failedCount : segments.filter(function(item) { return item.status === 'failed' || !!item.error; }).length;
    var rows = segments.map(function(seg) {
      var imagePrompt = displayImagePrompt(seg);
      var videoPrompt = seg.videoPrompt || seg.prompt || '';
      var timeText = (seg.start != null && seg.end != null) ? (seg.start + 's-' + seg.end + 's') : ('片段 ' + seg.index);
      return [
        '<article class="seedance-segment-card" data-status="' + escapeHtml(seg.status || 'pending') + '">',
        '<div class="seedance-segment-index">',
        '<strong>' + escapeHtml(String(seg.index || '')) + '</strong>',
        '<span>' + escapeHtml(timeText) + '</span>',
        '<em>' + escapeHtml(segmentDisplayStatus(seg)) + '</em>',
        seg.error ? '<small class="is-error">' + escapeHtml(seg.error) + '</small>' : '',
        '</div>',
        '<div class="seedance-segment-lane">',
        segmentPromptPanel('image', seg.index, imagePrompt),
        segmentMediaPanel('image', seg, imagePrompt),
        '</div>',
        '<div class="seedance-segment-lane">',
        segmentPromptPanel('video', seg.index, videoPrompt),
        segmentMediaPanel('video', seg, videoPrompt),
        '</div>',
        '</article>'
      ].join('');
    }).join('');
    return [
      '<section class="seedance-segment-board">',
      '<div class="seedance-segment-board-head">',
      '<div><strong>分段生产表</strong></div>',
      '<div class="seedance-segment-stats">',
      '<span>图片 ' + imageReady + '/' + total + '</span>',
      '<span>视频 ' + videoReady + '/' + total + '</span>',
      failed ? '<span data-tone="danger">失败 ' + failed + '</span>' : '',
      '</div>',
      '</div>',
      '<div class="seedance-segment-toolbar">',
      '<button type="button" class="btn btn-primary btn-sm seedance-segment-compose-btn" data-seedance-segment-merge' + (videoReady ? '' : ' disabled') + '>合成已有视频</button>',
      '<button type="button" class="btn btn-ghost btn-sm" data-seedance-segment-refresh>刷新状态</button>',
      '</div>',
      '<div class="seedance-segment-card-list">',
      rows,
      '</div>',
      '</section>'
    ].join('');
  }

  function ensureSegmentMediaModal() {
    var modal = $('seedanceSegmentMediaModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'seedanceSegmentMediaModal';
    modal.className = 'seedance-segment-media-modal';
    modal.hidden = true;
    modal.innerHTML = [
      '<div class="seedance-segment-media-dialog" role="dialog" aria-modal="true" aria-label="分段结果预览">',
      '<div class="seedance-segment-media-modal-head">',
      '<strong class="seedance-segment-media-modal-title"></strong>',
      '<div class="seedance-segment-media-modal-actions">',
      '<button type="button" class="btn btn-primary btn-sm" data-seedance-modal-download>下载到素材文件夹</button>',
      '<button type="button" class="btn btn-ghost btn-sm" data-seedance-modal-close>关闭</button>',
      '</div>',
      '</div>',
      '<div class="seedance-segment-media-modal-body"></div>',
      '</div>'
    ].join('');
    document.body.appendChild(modal);
    modal.addEventListener('click', function(event) {
      if (event.target === modal || (event.target && event.target.closest && event.target.closest('[data-seedance-modal-close]'))) {
        closeSegmentMediaModal();
      }
    });
    document.addEventListener('keydown', function(event) {
      if (event.key === 'Escape' && !modal.hidden) closeSegmentMediaModal();
    });
    return modal;
  }

  function closeSegmentMediaModal() {
    var modal = $('seedanceSegmentMediaModal');
    if (!modal) return;
    var body = modal.querySelector('.seedance-segment-media-modal-body');
    if (body) body.innerHTML = '';
    modal.hidden = true;
  }

  function openSegmentMediaModal(url, mediaType, title, filename, prompt) {
    var modal = ensureSegmentMediaModal();
    var body = modal.querySelector('.seedance-segment-media-modal-body');
    var titleEl = modal.querySelector('.seedance-segment-media-modal-title');
    var download = modal.querySelector('[data-seedance-modal-download]');
    var mt = mediaType === 'video' ? 'video' : 'image';
    if (titleEl) titleEl.textContent = title || (mt === 'video' ? '视频预览' : '图片预览');
    if (body) {
      body.innerHTML = mt === 'video'
        ? '<video src="' + escapeHtml(url) + '" controls playsinline preload="metadata"></video>'
        : '<img src="' + escapeHtml(url) + '" alt="' + escapeHtml(title || '图片预览') + '">';
    }
    if (download) {
      download.onclick = function() {
        downloadSegmentMedia(url, mt, filename, prompt);
      };
    }
    modal.hidden = false;
  }

  function downloadSegmentMedia(url, mediaType, filename, prompt) {
    var mt = mediaType === 'video' ? 'video' : 'image';
    if (!url) return showMessage((mt === 'video' ? '视频' : '图片') + '地址为空，无法下载。');
    saveRemoteAssetToLibraryAndDownloads(url, mt, filename, prompt)
      .then(function(data) {
        var folderText = data && data.directory ? (' / ' + data.directory) : '';
        showMessage((mt === 'video' ? '视频' : '图片') + '已保存到素材库并下载到本机文件夹' + folderText);
      })
      .catch(function(err) {
        showMessage('保存失败：' + normalizeApiErrorText(err && (err.message || err), '未知错误'));
        if (mt === 'video') openExternalUrl(downloadUrlForVideo(url, filename || 'seedance-segment.mp4'));
        else openExternalUrl(url);
      });
  }

  function setMainPromptFromSegment(prompt) {
    var text = String(prompt || '').trim();
    if (!text) return;
    var input = $('seedanceTaskPromptInput');
    if (input) {
      input.value = text;
      input.focus();
    }
    state.currentJobPrompt = text;
    showMessage('已把该段提示词回填到左侧输入框。');
    renderWorkspace();
  }

  function bindSegmentBoardActions() {
    document.querySelectorAll('[data-seedance-segment-copy]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function() {
        var text = btn.getAttribute('data-seedance-segment-copy') || '';
        if (!text) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).catch(function() {});
        }
        setMainPromptFromSegment(text);
      });
    });
    document.querySelectorAll('[data-seedance-segment-retry-image],[data-seedance-segment-retry-video]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function() {
        var text = btn.getAttribute('data-seedance-segment-retry-image') || btn.getAttribute('data-seedance-segment-retry-video') || '';
        setMainPromptFromSegment(text);
      });
    });
    document.querySelectorAll('[data-seedance-segment-refresh]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function() {
        refreshJobStatus(true);
      });
    });
    document.querySelectorAll('[data-seedance-segment-merge]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function() {
        state.finalComposeRequested = true;
        showMessage('已开始查看总视频合成状态，完成后会自动展示。');
        renderWorkspace();
        refreshJobStatus(false);
      });
    });
    document.querySelectorAll('[data-seedance-segment-preview]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function() {
        openSegmentMediaModal(
          btn.getAttribute('data-seedance-segment-preview') || '',
          btn.getAttribute('data-seedance-media-type') || 'image',
          btn.getAttribute('data-seedance-media-title') || '',
          btn.getAttribute('data-download-filename') || '',
          btn.getAttribute('data-seedance-media-prompt') || ''
        );
      });
    });
    document.querySelectorAll('[data-seedance-segment-download]').forEach(function(btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function() {
        downloadSegmentMedia(
          btn.getAttribute('data-seedance-segment-download') || '',
          btn.getAttribute('data-seedance-media-type') || 'image',
          btn.getAttribute('data-download-filename') || '',
          btn.getAttribute('data-seedance-media-prompt') || ''
        );
      });
    });
  }

  function shouldShowFinalResultStage() {
    return !!String(state.currentResultVideoUrl || '').trim() || !!state.finalComposeRequested;
  }

  function renderVideoStage(values, boards) {
    var resultStage = $('seedanceFinalResultStage');
    var videoStage = $('seedanceVideoStage');
    var videoSurface = $('seedanceVideoSurface');
    var stateEl = $('seedanceFinalResultState');
    var hintEl = $('seedanceFinalResultHint');
    var showFinal = shouldShowFinalResultStage();
    if (resultStage) resultStage.hidden = !showFinal;
    if (videoStage) videoStage.hidden = !showFinal;
    if (stateEl) {
      stateEl.textContent = state.currentResultVideoUrl ? '已完成' : '合成中';
    }
    if (hintEl) {
      hintEl.textContent = state.currentResultVideoUrl
        ? '合成完成后，这里显示最终成片。'
        : '视频正在合成中，可能需要 1-2 分钟。';
    }
    if (!showFinal) {
      if (videoSurface) videoSurface.innerHTML = '';
      return;
    }
    if (!videoSurface) return;
    videoSurface.innerHTML = resultVideoHtml(values, boards);
    bindResultVideoActions();
  }

  function bindRecentJobButtons() {
    document.querySelectorAll('[data-seedance-job]').forEach(function(card) {
      if (card.dataset.bound) return;
      card.dataset.bound = '1';
      function openCard(event) {
        var interactive = event.target && event.target.closest
          ? event.target.closest('button,a,[data-seedance-video-download],[data-seedance-video-open]')
          : null;
        if (interactive) return;
        var jobId = card.getAttribute('data-seedance-job') || '';
        if (!jobId) return;
        state.currentJobId = jobId;
        var hit = state.recentJobs.find(function(item) { return item && item.jobId === jobId; });
        state.currentJobStatus = (hit && hit.status) || 'running';
        state.currentResultVideoUrl = (hit && hit.videoUrl) || '';
        state.currentJobTitle = (hit && hit.title) || '创意视频任务';
        state.currentJobPrompt = (hit && hit.prompt) || '';
        state.currentJobError = (hit && hit.error) || '';
        state.currentJobProgress = (hit && hit.progress) || null;
        state.currentJobProgressPercent = hit && hit.progressPercent != null ? hit.progressPercent : null;
        state.currentJobProgressLabel = (hit && hit.progressLabel) || '';
        state.currentJobProgressDetail = (hit && hit.progressDetail) || '';
        state.currentSegmentArtifacts = (hit && hit.artifacts) || null;
        state.examplesOpen = false;
        state.mainView = 'result';
        renderWorkspace();
        if (false && hit && hit.cloud && hit.status === 'completed' && hit.videoUrl) {
          showMessage('已加载服务器保存的历史视频结果。');
          return;
        }
        refreshJobStatus(true);
      }
      card.addEventListener('click', openCard);
      card.addEventListener('keydown', function(event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        openCard(event);
      });
    });
  }

  function statusLabel(status) {
    if (status === 'completed') return '已完成';
    if (status === 'failed') return '失败';
    if (status === 'running') return '生成中';
    return '等待提交';
  }

  function statusTone(status) {
    if (status === 'completed') return 'success';
    if (status === 'failed') return 'danger';
    if (status === 'running') return 'processing';
    return 'idle';
  }

  function renderBusinessResultView() {
    var view = $('seedanceBusinessResultView');
    var stage = $('seedanceBusinessResultSurface');
    var status = $('seedanceTaskStatusText');
    var kind = $('seedanceTaskKindText');
    if (!view || !stage) return;

    if (status) {
      status.textContent = statusLabel(state.currentJobStatus);
      status.setAttribute('data-tone', statusTone(state.currentJobStatus));
    }
    if (kind) {
      kind.textContent = state.currentJobId ? ('任务 ' + state.currentJobId.slice(0, 8)) : '未开始';
      kind.setAttribute('data-tone', state.currentJobId ? 'processing' : 'idle');
    }
    var values = getFormValues();
    var boards = buildBoards();
    var canOperate = !!String(state.currentResultVideoUrl || '').trim();
    stage.innerHTML = [
      '<div class="seedance-result-layout">',
      '<div class="seedance-result-preview-pane">',
      resultVideoHtml(values, boards),
      '</div>',
      '<div class="seedance-result-side">',
      '<div class="seedance-result-side-top">',
      '<span class="seedance-result-kicker">' + escapeHtml(state.currentJobId ? '当前结果' : '结果预览') + '</span>',
      '<h5 class="seedance-result-heading">' + escapeHtml(currentJobHeading()) + '</h5>',
      '</div>',
      '<div class="seedance-result-meta">' + renderResultPills(values, boards) + '</div>',
      '<div class="seedance-result-actions">',
      '<button type="button" class="btn btn-sm seedance-action-btn is-copy" data-seedance-copy-prompt' + (currentJobPromptText() ? '' : ' disabled') + '>复制提示词并回填</button>',
      '<button type="button" class="btn btn-sm seedance-action-btn is-download" data-seedance-video-download="' + escapeHtml(state.currentResultVideoUrl || '') + '" data-seedance-asset-id="' + escapeHtml(currentJobAssetId()) + '" data-download-filename="creative-video.mp4"' + (canOperate ? '' : ' disabled') + '>下载视频</button>',
      '<button type="button" class="btn btn-sm seedance-action-btn is-open" data-seedance-video-open="' + escapeHtml(state.currentResultVideoUrl || '') + '"' + (canOperate ? '' : ' disabled') + '>打开视频</button>',
      '</div>',
      '<div class="seedance-result-prompt-panel">',
      '<div class="seedance-result-prompt-head">提示词</div>',
      '<div class="seedance-result-prompt-body">' + escapeHtml(currentJobPromptText() || '当前任务还没有可展示的提示词。') + '</div>',
      '</div>',
      '<p class="seedance-result-task-note">' + escapeHtml(currentJobSummary(values, boards)) + '</p>',
      '</div>',
      '</div>'
    ].join('');
    bindResultVideoActions();
    document.querySelectorAll('[data-seedance-copy-prompt]').forEach(function(btn) {
      btn.onclick = function() {
        if (btn.disabled) return;
        copyCurrentPromptToEditor();
      };
    });

    var list = $('seedanceBusinessHistoryGrid');
    var empty = $('seedanceBusinessHistoryEmpty');
    var count = $('seedanceBusinessHistoryCount');
    if (count) count.textContent = String(state.recentJobs.length || 0);
    if (list) {
      list.innerHTML = state.recentJobs.map(function(job) {
        var selected = job.jobId === state.currentJobId ? ' is-active' : '';
        var stageText = jobStageText(job);
        var promptText = jobPromptText(job) || stageText;
        var timeText = formatJobTime(job);
        var videoUrl = String((job && job.videoUrl) || '').trim();
        var jobPct = jobProgressPercent(job);
        var jobProgressHtml = job.status === 'running' && jobPct != null
          ? '<span class="seedance-business-job-progress"><span style="width:' + jobPct + '%"></span><em>' + jobPct + '%</em></span>'
          : '';
        var actionHtml = videoUrl && job.status === 'completed'
          ? [
              '<span class="seedance-business-job-actions">',
              '<button type="button" class="btn btn-primary btn-sm" data-seedance-video-download="' + escapeHtml(videoUrl) + '" data-seedance-asset-id="' + escapeHtml(job.assetId || '') + '" data-download-filename="creative-video.mp4">下载</button>',
              '<button type="button" class="btn btn-ghost btn-sm" data-seedance-video-open="' + escapeHtml(videoUrl) + '">打开</button>',
              '</span>'
            ].join('')
          : '';
        return [
          '<article class="seedance-business-job' + selected + '" data-seedance-job="' + escapeHtml(job.jobId) + '" tabindex="0" role="button">',
          '<span class="seedance-business-job-head">',
          '<span class="seedance-business-job-prompt" title="' + escapeHtml(promptText || '创意视频任务') + '">' + escapeHtml(promptText || '创意视频任务') + '</span>',
          '<span class="seedance-business-job-time">' + escapeHtml(timeText || '') + '</span>',
          '</span>',
          '<span class="seedance-business-job-media" data-status="' + escapeHtml(job.status || 'running') + '">' + jobMediaHtml(job) + '</span>',
          jobProgressHtml,
          '<span class="seedance-business-job-foot">',
          '<span class="seedance-result-pill" data-tone="' + escapeHtml(statusTone(job.status)) + '">' + escapeHtml(statusLabel(job.status)) + '</span>',
          actionHtml,
          '</span>',
          '</article>'
        ].join('');
      }).join('');
      bindRecentJobButtons();
      bindResultVideoActions();
    }
    if (empty) empty.style.display = state.recentJobs.length ? 'none' : 'block';
  }

  function setMainView(view) {
    state.mainView = view === 'result' ? 'result' : 'storyboard';
    state.examplesOpen = false;
    renderWorkspace();
  }

  function renderWorkspace() {
    var values = getFormValues();
    var boards = buildBoards();
    var resultPanel = $('seedanceResultPanel');
    var businessPanel = $('seedanceBusinessResultView');
    if (resultPanel) resultPanel.hidden = !!state.examplesOpen || state.mainView === 'result';
    if (businessPanel) businessPanel.hidden = !!state.examplesOpen || state.mainView !== 'result';
    if ($('seedanceStoryboardTabBtn')) $('seedanceStoryboardTabBtn').classList.toggle('is-active', state.mainView !== 'result' && !state.examplesOpen);
    if ($('seedanceBusinessResultTabBtn')) $('seedanceBusinessResultTabBtn').classList.toggle('is-active', state.mainView === 'result' && !state.examplesOpen);
    renderUploadList('seedanceImageList', state.images);
    renderBoards(boards);
    renderVideoStage(values, boards);
    renderBusinessResultView();
    renderExamplesPanel();
    updateStartButtonState();
  }

  function showMessage(text) {
    var el = $('seedanceStudioMsg');
    if (!el) return;
    el.textContent = text;
    el.style.display = text ? 'block' : 'none';
  }

  function ensureTaskToast() {
    var toast = $('seedanceTaskToast');
    if (toast) return toast;
    toast = document.createElement('div');
    toast.id = 'seedanceTaskToast';
    toast.className = 'seedance-task-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.innerHTML = [
      '<div class="seedance-task-toast-main">',
      '<strong class="seedance-task-toast-title"></strong>',
      '<p class="seedance-task-toast-body"></p>',
      '</div>',
      '<button type="button" class="seedance-task-toast-action">查看</button>'
    ].join('');
    var action = toast.querySelector('.seedance-task-toast-action');
    if (action) {
      action.addEventListener('click', function() {
        state.mainView = 'result';
        state.examplesOpen = false;
        renderWorkspace();
        hideTaskToast();
      });
    }
    document.body.appendChild(toast);
    return toast;
  }

  function hideTaskToast() {
    var toast = $('seedanceTaskToast');
    if (!toast) return;
    toast.classList.remove('is-visible');
  }

  function showTaskToast(kind, title, body, actionText) {
    var toast = ensureTaskToast();
    toast.classList.toggle('is-error', kind === 'error');
    toast.classList.toggle('is-success', kind !== 'error');
    var titleEl = toast.querySelector('.seedance-task-toast-title');
    var bodyEl = toast.querySelector('.seedance-task-toast-body');
    var actionEl = toast.querySelector('.seedance-task-toast-action');
    if (titleEl) titleEl.textContent = title || '';
    if (bodyEl) bodyEl.textContent = body || '';
    if (actionEl) actionEl.textContent = actionText || '查看';
    window.clearTimeout(seedanceTaskToastTimer);
    window.requestAnimationFrame(function() {
      toast.classList.add('is-visible');
    });
    seedanceTaskToastTimer = window.setTimeout(hideTaskToast, 9000);
  }

  function showCreditWarning(requiredCredits, balanceCredits) {
    var required = Math.ceil(Number(requiredCredits || 0));
    var balance = Number(balanceCredits || 0);
    var balanceText = isFinite(balance) ? balance.toFixed(2).replace(/\.?0+$/, '') : '--';
    var body = '本次生成预计需要 ' + required + ' 算力，当前余额 ' + balanceText + ' 算力。请充值后再提交。';
    showMessage('提交失败：' + body);
    var modal = $('seedanceCreditModal');
    var bodyEl = $('seedanceCreditModalBody');
    if (bodyEl) bodyEl.textContent = body;
    if (modal) modal.classList.add('visible');
    else window.alert(body);
  }

  function closeCreditWarning() {
    var modal = $('seedanceCreditModal');
    if (modal) modal.classList.remove('visible');
  }

  function openRechargeFromCreditWarning() {
    closeCreditWarning();
    var rechargeUrl = (typeof RECHARGE_URL !== 'undefined' && RECHARGE_URL) ? String(RECHARGE_URL) : '';
    if (rechargeUrl && rechargeUrl !== '#') {
      try {
        window.open(rechargeUrl, '_blank', 'noopener');
        return;
      } catch (e) {}
    }
    if (typeof window.showAppView === 'function') {
      window.showAppView('billing');
      return;
    }
    var billingBtn = document.querySelector('.nav-left-item[data-view="billing"]');
    if (billingBtn) billingBtn.click();
  }

  function notifyTaskOnce(status) {
    if (!state.currentJobId || !status) return;
    var key = state.currentJobId + ':' + status;
    if (seedanceTaskNotifiedJobs[key]) return;
    seedanceTaskNotifiedJobs[key] = true;

    if (status === 'completed') {
      showTaskToast(
        'success',
        '创意视频已生成',
        state.currentResultVideoUrl ? '任务已完成，可以回来查看或下载最终视频。' : '任务已完成，最终视频正在同步展示与入库，可以到结果页查看。',
        '查看结果'
      );
    } else if (status === 'failed') {
      showTaskToast(
        'error',
        '创意视频生成失败',
        state.currentJobError || progressSummary(state.currentJobProgress) || '任务执行失败，请查看结果页原因后重新提交。',
        '查看原因'
      );
    }
  }

  function updateStartButtonState() {
    var btn = $('seedanceStartBtn');
    if (!btn) return;
    var isBusy = !!state.submitBusy;
    var label = state.submitLabel || '开始生成视频';
    btn.disabled = isBusy;
    btn.classList.toggle('is-loading', isBusy);
    btn.innerHTML = [
      '<span class="tvc-btn-spinner" aria-hidden="true"></span>',
      '<span class="tvc-btn-label">' + escapeHtml(label) + '</span>'
    ].join('');
  }

  function setSubmitBusy(isBusy, label) {
    state.submitBusy = !!isBusy;
    state.submitLabel = isBusy ? (label || '处理中...') : '';
    updateStartButtonState();
  }

  function uploadAssetItem(item) {
    if (!item || item.asset_id) return Promise.resolve(item);
    if (!item.file) return Promise.reject(new Error('缺少本地文件，无法上传'));
    var base = pipelineBase();
    if (!base) return Promise.reject(new Error('当前未检测到可用的后端地址'));

    var fd = new FormData();
    fd.append('file', item.file);

    return fetch(base + '/api/assets/upload', {
      method: 'POST',
      headers: authHeadersSafe(),
      body: fd
    })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
    })
      .then(function(result) {
        if (!result.ok || !result.data || !result.data.asset_id) {
          throw new Error(responseErrorText(result.data, '素材上传失败'));
        }
        item.asset_id = result.data.asset_id || '';
        item.source_url = result.data.source_url || item.source_url || '';
        item.preview_url = result.data.preview_url || result.data.local_preview_url || item.preview_url || '';
        item.open_url = result.data.open_url || item.open_url || '';
        item.url = mediaItemUrl(item) || item.url || '';
        return item;
      });
  }

  function ensureImageAssetsUploaded() {
    if (state.mode === 'prompt_only') {
      return Promise.resolve([]);
    }
    return state.images.reduce(function(chain, item) {
      return chain.then(function(list) {
        return uploadAssetItem(item).then(function(doneItem) {
          list.push(doneItem);
          return list;
        });
      });
    }, Promise.resolve([]));
  }

  function buildPromptWithReferenceHints(prompt, uploadedImages) {
    var hints = [];
    (uploadedImages || state.images || []).forEach(function(item, index) {
      var purpose = item.purpose || 'storyboard';
      var tpl = PURPOSE_HINTS[purpose] || PURPOSE_HINTS.storyboard;
      hints.push(tpl.replace(/\{n\}/g, String(index + 1)));
    });
    var userPrompt = String(prompt || '').trim();
    if (!hints.length) return userPrompt;
    if (!userPrompt) {
      userPrompt = '请基于以上参考图规划一条统一、连贯、适合短视频平台发布的创意短视频。除非用户明确要求电商、商品、品牌广告或带货，不要按电商广告方向分析。';
    }
    return hints.join('\n') + '\n\n用户提示词：' + userPrompt;
  }

  function buildRunPayload(uploadedImages) {
    var values = getFormValues();
    var uploaded = uploadedImages || [];
    var segmentSeconds = getCurrentSegmentSeconds(values.model);
    var segmentCount = getDurationSegmentCount(state.duration, values.model);
    var videoRequest = videoRequestForModel(values.model);
    var useDirectVideo = state.mode === 'image_prompt'
      && uploaded.length >= 1
      && !!(uploaded[0] && uploaded[0].asset_id)
      && !!values.prompt;
    var basePayload = {
      total_duration_seconds: segmentCount * segmentSeconds,
      segment_count: segmentCount,
      segment_duration_seconds: segmentSeconds,
      workflow_mode: useDirectVideo ? 'direct_video' : 'storyboard',
      merge_clips: !!values.needMerge,
      auto_save: true,
      analysis_model: typeof ANALYSIS_MODEL !== 'undefined' ? ANALYSIS_MODEL : '',
      image_model: typeof IMAGE_MODEL !== 'undefined' ? IMAGE_MODEL : '',
      image_model_fallback: 'gpt-image-2-yunwu',
      video_model: videoRequest.model,
      video_channel: videoRequest.channel,
      video_fallbacks: isYunwuVeoModel(values.model) ? [
        { channel: 'comfly', model: 'veo3.1-fast' }
      ] : [],
      aspect_ratio: values.aspectRatio,
      generate_audio: !!values.needAudio,
      watermark: false
    };

    if (state.mode === 'prompt_only') {
      if (!values.prompt) {
        return { error: '请先输入创意提示词后再开始生成。' };
      }
      basePayload.task_text = values.prompt;
      return { payload: basePayload };
    }

    if (!uploaded.length || !uploaded[0].asset_id) {
      if (values.prompt) {
        basePayload.task_text = values.prompt;
        return { payload: basePayload, effectiveMode: 'prompt_only' };
      }
      return { error: '请先上传至少 1 张参考图，或输入创意提示词后再开始生成。' };
    }

    return {
      payload: Object.assign(basePayload, {
        asset_id: uploaded[0].asset_id,
        reference_asset_ids: uploaded.slice(1).map(function(item) {
          return item.asset_id;
        }).filter(Boolean),
        task_text: useDirectVideo ? values.prompt : buildPromptWithReferenceHints(values.prompt || '', uploaded)
      })
    };
  }

  function extractResultVideoUrl(resp) {
    if (!resp || typeof resp !== 'object') return '';

    function _looksLikeVideoUrl(u) {
      if (!u) return false;
      var s = String(u).split('?')[0].toLowerCase();
      return /\.(mp4|mov|m4v|webm|mkv)$/.test(s);
    }

    var result = resp.result || {};
    var finalVideo = result.final_video || {};
    var finalUrl = String(finalVideo.url || finalVideo.preview_url || finalVideo.local_preview_url || '').trim();
    if (finalUrl) return finalUrl;

    var saved = Array.isArray(resp.saved_assets) ? resp.saved_assets : [];
    for (var p = 0; p < saved.length; p += 1) {
      var preferred = creativeVideoAssetMeta(saved[p]);
      if (!looksLikeFinalCreativeVideo(saved[p], finalVideo)) continue;
      if (preferred.sourceUrl) return preferred.sourceUrl;
    }
    for (var i = 0; i < saved.length; i += 1) {
      var item = creativeVideoAssetMeta(saved[i]);
      if (!item.sourceUrl) continue;
      if (item.mediaType === 'video' || _looksLikeVideoUrl(item.sourceUrl)) return item.sourceUrl;
    }

    function pickSegmentUrl(item) {
      if (!item || typeof item !== 'object') return '';
      var direct = String(item.mp4url || item.video_url || item.url || item.output || '').trim();
      if (direct) return direct;

      var raw = item.video_raw || item.raw || {};
      var content = raw && typeof raw.content === 'object' ? raw.content : {};
      var contentUrl = String(content.video_url || content.url || '').trim();
      if (contentUrl) return contentUrl;

      var data = raw && typeof raw.data === 'object' ? raw.data : {};
      var dataUrl = String(data.video_url || data.output || '').trim();
      if (dataUrl) return dataUrl;

      var resultObj = raw && typeof raw.result === 'object' ? raw.result : {};
      return String(resultObj.video_url || resultObj.output || '').trim();
    }

    var groups = [result.completed_segments, result.completed_shots, result.shots];
    for (var g = 0; g < groups.length; g += 1) {
      var list = Array.isArray(groups[g]) ? groups[g] : [];
      for (var j = 0; j < list.length; j += 1) {
        var url = pickSegmentUrl(list[j]);
        if (url) return url;
      }
    }

    return '';
  }

  function cloudVideoUrlFromJob(job) {
    var assets = (job && job.assets) || {};
    var ids = Array.isArray(job && job.asset_ids) ? job.asset_ids : [];
    var resultPayload = (job && job.result_payload) || {};
    var finalVideo = resultPayload.final_video || {};
    var finalAssetId = String(finalVideo.asset_id || '').trim();
    if (finalAssetId) {
      var matched = assets[finalAssetId] || {};
      var matchedUrl = String(matched.source_url || matched.preview_url || '').trim();
      if (matchedUrl) return matchedUrl;
    }
    for (var i = 0; i < ids.length; i += 1) {
      var asset = assets[ids[i]] || {};
      var assetTags = String(asset.tags || '').trim().toLowerCase();
      var src = String(asset.source_url || asset.preview_url || '').trim();
      if (src && (assetTags.indexOf('merged') >= 0 || assetTags.indexOf('captioned') >= 0)) return src;
    }
    var saved = Array.isArray(job && job.saved_assets) ? job.saved_assets : [];
    for (var p = 0; p < saved.length; p += 1) {
      var preferred = creativeVideoAssetMeta(saved[p]);
      if (looksLikeFinalCreativeVideo(saved[p], finalVideo) && preferred.sourceUrl) return preferred.sourceUrl;
    }
    for (var j = 0; j < ids.length; j += 1) {
      var row = assets[ids[j]] || {};
      var rowUrl = String(row.source_url || row.preview_url || '').trim();
      if (rowUrl) return rowUrl;
    }
    for (var j = 0; j < saved.length; j += 1) {
      var item = creativeVideoAssetMeta(saved[j]);
      if (item.sourceUrl) return item.sourceUrl;
    }
    return extractResultVideoUrl({
      result: resultPayload,
      saved_assets: saved
    });
  }

  function normalizeCloudJob(job) {
    if (!job || !job.job_id) return null;
    var req = job.request_payload || {};
    var reqPayload = (req && req.payload) || {};
    var reqInp = (req && req.inp) || {};
    var promptText = String(job.prompt || reqPayload.task_text || reqInp.task_text || '').trim();
    return {
      jobId: String(job.job_id || ''),
      status: String(job.status || 'running'),
      title: job.title || '创意视频任务',
      prompt: promptText,
      videoUrl: cloudVideoUrlFromJob(job),
      assetId: findJobAssetId(job),
      error: String(job.error || '').trim(),
      progress: null,
      artifacts: normalizeSegmentArtifacts(job.artifacts || null),
      createdAt: Date.parse(job.created_at || job.createdAt || '') || 0,
      updatedAt: Date.parse(job.updated_at || job.completed_at || job.created_at || '') || Date.now(),
      cloud: true,
      cloudJob: job
    };
  }

  function loadLocalJobHistory() {
    var base = pipelineBase();
    if (!base) return Promise.resolve([]);
    return fetch(base + '/api/comfly-seedance-tvc/pipeline/jobs?limit=' + encodeURIComponent(String(RECENT_JOB_LIMIT)), {
      headers: authHeadersSafe()
    })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) throw new Error(responseErrorText(result.data, 'local history failed'));
        var rows = (Array.isArray(result.data.items) ? result.data.items : [])
          .map(normalizeLocalJob)
          .filter(Boolean);
        mergeRecentJobs(rows);
        renderWorkspace();
        return rows;
      })
      .catch(function(err) {
        console.warn('Seedance local history load failed', err);
        return [];
      });
  }

  function loadCloudJobHistory() {
    var base = cloudBase();
    if (!base) return Promise.resolve([]);
    return fetch(base + '/api/creative-jobs?feature_type=seedance_tvc&limit=' + encodeURIComponent(String(RECENT_JOB_LIMIT)), {
      headers: authHeadersSafe()
    })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, status: response.status, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) throw new Error('cloud history failed');
        var rows = (Array.isArray(result.data.items) ? result.data.items : [])
          .map(normalizeCloudJob)
          .filter(Boolean);
        mergeRecentJobs(rows);
        renderWorkspace();
        return rows;
      })
      .catch(function(err) {
        console.warn('Seedance 云端历史加载失败', err);
        return [];
      });
  }

  function fetchCloudJob(jobId) {
    var base = cloudBase();
    if (!base || !jobId) return Promise.resolve(null);
    return fetch(base + '/api/creative-jobs/' + encodeURIComponent(jobId), {
      headers: authHeadersSafe()
    })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok || !result.data || !result.data.job) return null;
        return normalizeCloudJob(result.data.job);
      })
      .catch(function(err) {
        console.warn('Seedance 单任务云端状态加载失败', err);
        return null;
      });
  }

  function applyJobSnapshot(job, fallbackStatus) {
    if (!job || !job.jobId) return false;
    var status = job.status || fallbackStatus || 'running';
    state.currentJobStatus = status;
    state.currentResultVideoUrl = job.videoUrl || state.currentResultVideoUrl || '';
    state.currentJobTitle = job.title || state.currentJobTitle || '创意视频任务';
    state.currentJobPrompt = job.prompt || state.currentJobPrompt || '';
    state.currentJobError = job.error || '';
    state.currentJobProgress = job.progress || null;
    state.currentJobProgressPercent = job.progressPercent != null ? job.progressPercent : null;
    state.currentJobProgressLabel = job.progressLabel || '';
    state.currentJobProgressDetail = job.progressDetail || '';
    state.currentSegmentArtifacts = job.artifacts || state.currentSegmentArtifacts || null;
    updateRememberedJob(job.jobId, {
      status: status,
      title: state.currentJobTitle,
      prompt: state.currentJobPrompt || job.prompt || '',
      videoUrl: state.currentResultVideoUrl || '',
      assetId: job.assetId || currentJobAssetId() || '',
      error: state.currentJobError,
      progress: state.currentJobProgress,
      progressPercent: state.currentJobProgressPercent,
      progressLabel: state.currentJobProgressLabel,
      progressDetail: state.currentJobProgressDetail,
      artifacts: state.currentSegmentArtifacts,
      createdAt: job.createdAt || 0,
      cloud: !!job.cloud
    });
    return true;
  }

  function markCurrentJobInterrupted(message) {
    stopPolling();
    state.currentJobStatus = 'failed';
    state.currentJobError = message || '本地生成任务已中断，请重新提交任务。';
    state.currentJobProgress = {
      last_steps: [{ name: state.currentJobError, status: 'failed' }]
    };
    state.currentJobProgressPercent = 100;
    state.currentJobProgressLabel = '任务失败';
    state.currentJobProgressDetail = state.currentJobError;
    updateRememberedJob(state.currentJobId, {
      status: 'failed',
      error: state.currentJobError,
      progress: state.currentJobProgress,
      progressPercent: state.currentJobProgressPercent,
      progressLabel: state.currentJobProgressLabel,
      progressDetail: state.currentJobProgressDetail
    });
    renderWorkspace();
    showMessage('任务失败：' + state.currentJobError);
    notifyTaskOnce('failed');
  }

  function refreshJobStatus(showToast) {
    var base = pipelineBase();
    if (!base || !state.currentJobId) return;

    fetch(base + '/api/comfly-seedance-tvc/pipeline/jobs/' + encodeURIComponent(state.currentJobId), {
      headers: authHeadersSafe()
    })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, status: response.status, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok) {
          var statusCode = Number(result.status || 0);
          var message = responseErrorText(result.data, '状态查询失败');
          if (statusCode === 404 || /任务不存在|已过期|not found/i.test(message)) {
            return fetchCloudJob(state.currentJobId).then(function(cloudJob) {
              if (cloudJob && cloudJob.status === 'completed' && !cloudJob.videoUrl) {
                markCurrentJobInterrupted('本地生成任务已中断，服务器没有可播放的最终视频。');
                return null;
              }
              if (cloudJob && applyJobSnapshot(cloudJob, cloudJob.status)) {
                renderWorkspace();
                if (cloudJob.status === 'completed' && cloudJob.videoUrl) {
                  stopPolling();
                  showMessage('本地任务已恢复，已从服务器历史加载视频结果。');
                  notifyTaskOnce('completed');
                } else if (cloudJob.status === 'failed') {
                  stopPolling();
                  showMessage('任务失败：' + (cloudJob.error || '服务器历史记录显示失败。'));
                  notifyTaskOnce('failed');
                } else {
                  markCurrentJobInterrupted('本地生成任务已中断，服务器仍未返回最终视频结果。');
                }
                return null;
              }
              markCurrentJobInterrupted('本地生成任务已中断，服务器暂时没有可恢复的结果。');
              return null;
            });
          }
          throw new Error(message);
        }

        state.currentJobStatus = String(result.data.status || '').trim();
        state.currentResultVideoUrl = extractResultVideoUrl(result.data) || state.currentResultVideoUrl;
        state.currentJobError = normalizeApiErrorText(result.data.error || '', '').trim();
        state.currentJobProgress = result.data.progress || null;
        state.currentJobProgressPercent = result.data.progress_percent != null ? result.data.progress_percent : null;
        state.currentJobProgressLabel = result.data.progress_label || '';
        state.currentJobProgressDetail = result.data.progress_detail || '';
        state.currentSegmentArtifacts = normalizeSegmentArtifacts(result.data.artifacts || null) || state.currentSegmentArtifacts || null;
        updateRememberedJob(state.currentJobId, {
          status: state.currentJobStatus,
          prompt: state.currentJobPrompt || '',
          videoUrl: state.currentResultVideoUrl || '',
          assetId: findJobAssetId(result.data) || currentJobAssetId() || '',
          error: state.currentJobError,
          progress: state.currentJobProgress,
          progressPercent: state.currentJobProgressPercent,
          progressLabel: state.currentJobProgressLabel,
          progressDetail: state.currentJobProgressDetail,
          artifacts: state.currentSegmentArtifacts
        });
        renderWorkspace();

        if (state.currentJobStatus === 'running') {
          schedulePoll(4000);
          return;
        }

        stopPolling();
        if (state.currentJobStatus === 'completed') {
          if (state.currentResultVideoUrl) {
            showMessage('任务已完成，右侧已切换到最终结果视频。');
          } else {
            showMessage('任务已完成，最终视频正在同步展示与入库，请稍后刷新结果页或到素材库查看。');
          }
          notifyTaskOnce('completed');
        } else if (state.currentJobStatus === 'failed') {
          showMessage('任务失败：' + (state.currentJobError || progressSummary(state.currentJobProgress) || '未知错误'));
          notifyTaskOnce('failed');
        } else if (showToast) {
          showMessage('任务状态已刷新。');
        }
        return null;
      })
      .catch(function(err) {
        stopPolling();
        state.currentJobStatus = 'failed';
        state.currentJobError = '状态刷新失败：' + normalizeApiErrorText(err && (err.message || err), '未知错误');
        state.currentJobProgress = {
          last_steps: [{ name: state.currentJobError, status: 'failed' }]
        };
        state.currentJobProgressPercent = 100;
        state.currentJobProgressLabel = '状态刷新失败';
        state.currentJobProgressDetail = state.currentJobError;
        updateRememberedJob(state.currentJobId, {
          status: 'failed',
          error: state.currentJobError,
          progress: state.currentJobProgress,
          progressPercent: state.currentJobProgressPercent,
          progressLabel: state.currentJobProgressLabel,
          progressDetail: state.currentJobProgressDetail
        });
        renderWorkspace();
        showMessage('任务失败：' + state.currentJobError);
        notifyTaskOnce('failed');
      });
  }

  function startRun() {
    var base = pipelineBase();

    if (!base) {
      showMessage('当前未检测到可用的后端地址，无法提交 Seedance 视频任务。');
      return;
    }

    var values = getFormValues();
    if (isOpenMindGrokModel(values.model) && (state.mode === 'prompt_only' || !state.images.length)) {
      showMessage('影梦 1.5 Plus 需要先上传或选择一张参考图；纯提示词请切换影梦 2.0 Pro。');
      return;
    }
    var duration = state.duration || getCurrentSegmentSeconds(values.model);
    var segmentCount = getDurationSegmentCount(duration, values.model);
    var estimatedCreditsPerSegment = 40;
    var userCredits = estimatedCreditsPerSegment * segmentCount * 2;

    setSubmitBusy(true, '检查算力...');
    state.examplesOpen = false;
    renderWorkspace();

    fetch((typeof API_BASE !== 'undefined' ? API_BASE : '') + '/auth/me', {
      headers: (typeof authHeaders === 'function' ? authHeaders() : {})
    })
      .then(function(r) {
        if (!r.ok) return {};
        return r.json().catch(function() { return {}; });
      })
      .then(function(meData) {
        var balance = meData.credits != null ? meData.credits : null;
        if (balance !== null && balance < userCredits) {
          var creditError = new Error('算力不足');
          creditError.code = 'INSUFFICIENT_CREDITS';
          creditError.requiredCredits = userCredits;
          creditError.balanceCredits = balance;
          throw creditError;
        }

        setSubmitBusy(true, '提交中...');
        showMessage(
          (state.mode === 'prompt_only' || !state.images.length)
            ? '正在提交纯提示词视频任务，请稍候...'
            : '正在上传参考素材并提交视频任务，请稍候...'
        );

        return ensureImageAssetsUploaded();
      })
      .catch(function(err) {
        if (err && err.code === 'INSUFFICIENT_CREDITS') {
          showCreditWarning(err.requiredCredits, err.balanceCredits);
          setSubmitBusy(false);
          throw err;
        }
        showMessage('算力检查暂时不可用，继续提交任务...');
        setSubmitBusy(true, '提交中...');
        return ensureImageAssetsUploaded();
      })
      .then(function(uploadedImages) {
        var built = buildRunPayload(uploadedImages);
        if (built.error) throw new Error(built.error);

        function submitPayload(payload) {
          return fetch(base + '/api/comfly-seedance-tvc/pipeline/start', {
            method: 'POST',
            headers: Object.assign({ 'Content-Type': 'application/json' }, authHeadersSafe()),
            body: JSON.stringify({ payload: payload })
          });
        }

        return submitPayload(built.payload).then(function(response) {
          if (response.ok) return response;
          return response.json().catch(function() { return {}; }).then(function(data) {
            var message = responseErrorText(data, '');
            var looksLikeOldDurationApi = /segment_duration_seconds|total_duration_seconds|固定为 10|仅支持 10\/20\/30\/40\/50\/60/.test(message);
            if (!looksLikeOldDurationApi) {
              return new Response(JSON.stringify(data || {}), { status: response.status, statusText: response.statusText, headers: { 'Content-Type': 'application/json' } });
            }
            var fallbackPayload = Object.assign({}, built.payload, {
              segment_duration_seconds: 10,
              total_duration_seconds: Math.max(1, Number(built.payload.segment_count || 1)) * 10,
              workflow_mode: 'storyboard'
            });
            showMessage('本地服务仍是旧版参数，已自动按兼容模式重新提交...');
            return submitPayload(fallbackPayload);
          });
        });
      })
      .then(function(response) {
        return response.json().then(function(data) {
          return { ok: response.ok, data: data || {} };
        });
      })
      .then(function(result) {
        if (!result.ok || !result.data || !result.data.job_id) {
          throw new Error(responseErrorText(result.data, '任务提交失败'));
        }

      state.currentJobId = result.data.job_id;
      state.currentJobStatus = 'running';
      state.currentResultVideoUrl = '';
      state.currentJobTitle = ($('seedanceTaskPromptInput').value || '创意视频').trim().slice(0, 18) || '创意视频任务';
      state.currentJobPrompt = ($('seedanceTaskPromptInput').value || '').trim();
      state.currentJobError = '';
      state.currentJobProgress = null;
      state.currentJobProgressPercent = 1;
      state.currentJobProgressLabel = '任务已提交';
      state.currentJobProgressDetail = '任务已提交，正在准备生成';
      state.currentSegmentArtifacts = null;
      state.finalComposeRequested = false;
      state.examplesOpen = false;
      state.mainView = 'storyboard';
      rememberJob({
        jobId: state.currentJobId,
        status: 'running',
        title: state.currentJobTitle,
        prompt: state.currentJobPrompt,
        assetId: '',
        error: '',
        progress: null,
        progressPercent: state.currentJobProgressPercent,
        progressLabel: state.currentJobProgressLabel,
        progressDetail: state.currentJobProgressDetail,
        artifacts: null,
        createdAt: Date.now()
      });
      setSubmitBusy(false);
      renderWorkspace();
        showMessage('任务已提交，可以切换页面或继续提交新任务。');
        refreshJobStatus(false);
      })
      .catch(function(err) {
        setSubmitBusy(false);
        if (err && err.code === 'INSUFFICIENT_CREDITS') return;
        showMessage('提交失败：' + normalizeApiErrorText(err && (err.message || err), '未知错误'));
      })
      .finally(function() {
        updateStartButtonState();
      });
  }

  function bindEvents() {
    ['seedanceCreditModalClose', 'seedanceCreditModalCancel'].forEach(function(id) {
      var btn = $(id);
      if (btn) btn.addEventListener('click', closeCreditWarning);
    });
    var creditRechargeBtn = $('seedanceCreditModalRecharge');
    if (creditRechargeBtn) creditRechargeBtn.addEventListener('click', openRechargeFromCreditWarning);
    var creditModal = $('seedanceCreditModal');
    if (creditModal) {
      creditModal.addEventListener('click', function(event) {
        if (event.target === creditModal) closeCreditWarning();
      });
    }

    $('seedanceTvcStudioBackBtn').addEventListener('click', function() {
      if (typeof window._ensureSkillStoreVisible === 'function') window._ensureSkillStoreVisible();
      try {
        location.hash = 'skill-store';
      } catch (err) {}
    });

    var modeSelect = $('seedanceInputModeSelect');
    if (modeSelect) {
      modeSelect.addEventListener('change', function(event) {
        setMode(event.target.value || 'image_auto');
        state.activeBoardIndex = 0;
        renderWorkspace();
        showMessage('');
      });
    }

    document.querySelectorAll('[data-seedance-input-mode]').forEach(function(tab) {
      tab.addEventListener('click', function() {
        setMode(tab.getAttribute('data-seedance-input-mode') || 'image_auto');
        state.activeBoardIndex = 0;
        renderWorkspace();
        showMessage('');
      });
    });

    document.querySelectorAll('#seedanceDurationGrid .tvc-duration-chip').forEach(function(chip) {
      chip.addEventListener('click', function() {
        setDuration(Number(chip.getAttribute('data-duration')) || 20);
        state.activeBoardIndex = 0;
        renderWorkspace();
      });
    });

    $('seedanceImageUploadBtn').addEventListener('click', function() {
      $('seedanceImageFileInput').click();
    });

    if ($('seedanceAssetPickBtn')) {
      $('seedanceAssetPickBtn').addEventListener('click', function() {
        openAssetPicker();
      });
    }

    $('seedanceImageFileInput').addEventListener('change', function(event) {
      state.images = appendMediaItems(state.images, readFiles(event.target.files));
      state.activeBoardIndex = 0;
      event.target.value = '';
      renderWorkspace();
      showMessage(state.images.length ? '已载入 ' + state.images.length + ' 张参考图。' : '');
    });

    bindUploadListRemoval();

    if ($('seedanceExamplesToggleBtn')) {
      $('seedanceExamplesToggleBtn').addEventListener('click', function() {
        state.examplesOpen = !state.examplesOpen;
        if (state.examplesOpen && state.exampleCatalog.length && !state.exampleVisibleCount) {
          state.exampleVisibleCount = Math.min(state.examplePageSize, state.exampleCatalog.length);
        }
        renderWorkspace();
        if (state.examplesOpen) ensureExampleCatalog();
      });
    }

    if ($('seedanceExamplesCloseBtn')) {
      $('seedanceExamplesCloseBtn').addEventListener('click', function() {
        state.examplesOpen = false;
        renderWorkspace();
      });
    }

    if ($('seedanceExamplesMoreBtn')) {
      $('seedanceExamplesMoreBtn').addEventListener('click', function() {
        loadMoreExamples();
      });
    }

    document.querySelectorAll('[data-seedance-category]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        state.exampleCategory = btn.getAttribute('data-seedance-category') || 'all';
        state.exampleVisibleCount = Math.min(state.examplePageSize, filteredExampleCatalog().length || state.examplePageSize);
        document.querySelectorAll('[data-seedance-category]').forEach(function(item) {
          item.classList.toggle('active', item === btn);
        });
        renderExamplesPanel();
      });
    });

    if ($('seedanceExampleSearchInput')) {
      $('seedanceExampleSearchInput').addEventListener('input', function(event) {
        state.exampleSearch = event.target.value || '';
        state.exampleVisibleCount = Math.min(state.examplePageSize, filteredExampleCatalog().length || state.examplePageSize);
        renderExamplesPanel();
      });
    }

    if ($('seedanceVideoModalClose')) {
      $('seedanceVideoModalClose').addEventListener('click', closeExampleVideo);
    }
    if ($('seedanceVideoModal')) {
      $('seedanceVideoModal').addEventListener('click', function(event) {
        if (event.target === $('seedanceVideoModal')) closeExampleVideo();
      });
    }
    document.addEventListener('keydown', function(event) {
      if (event.key === 'Escape') closeExampleVideo();
    });

    if ($('seedanceExamplesGrid')) {
      $('seedanceExamplesGrid').addEventListener('click', function(event) {
        var videoBtn = event.target && event.target.closest ? event.target.closest('[data-example-video]') : null;
        if (videoBtn) {
          event.preventDefault();
          event.stopPropagation();
          var videoId = String(videoBtn.getAttribute('data-example-video') || '').trim();
          var videoExample = state.exampleCatalog.find(function(item) { return item.id === videoId; });
          openExampleVideo(videoExample);
          return;
        }
        var applyBtn = event.target && event.target.closest ? event.target.closest('[data-example-apply]') : null;
        if (!applyBtn) return;
        event.preventDefault();
        var targetId = String(applyBtn.getAttribute('data-example-apply') || '').trim();
        var example = state.exampleCatalog.find(function(item) { return item.id === targetId; });
        if (!example || !$('seedanceTaskPromptInput')) return;
        state.activeExampleId = example.id;
        $('seedanceTaskPromptInput').value = example.prompt || '';
        $('seedanceTaskPromptInput').focus();
        renderWorkspace();
        showMessage('已带入案例提示词：' + example.title);
      });

      $('seedanceExamplesGrid').addEventListener('keydown', function(event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        var card = event.target && event.target.closest ? event.target.closest('[data-example-apply]') : null;
        if (!card) return;
        event.preventDefault();
        card.click();
      });
    }

    if ($('seedanceStoryboardTabBtn')) {
      $('seedanceStoryboardTabBtn').addEventListener('click', function() {
        setMainView('storyboard');
      });
    }

    if ($('seedanceBusinessResultTabBtn')) {
      $('seedanceBusinessResultTabBtn').addEventListener('click', function() {
        setMainView('result');
      });
    }

    if ($('seedanceBusinessResultBackBtn')) {
      $('seedanceBusinessResultBackBtn').addEventListener('click', function() {
        setMainView('storyboard');
      });
    }

    [
      'seedanceAspectRatioSelect',
      'seedanceVisualToneSelect',
      'seedanceRhythmSelect',
      'seedanceModelSelect',
      'seedanceNeedAudioCheck',
      'seedanceNeedMergeCheck',
      'seedanceTaskPromptInput'
    ].forEach(function(id) {
      var el = $(id);
      if (!el) return;
      var eventName = (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && el.type === 'text')) ? 'input' : 'change';
      el.addEventListener(eventName, renderWorkspace);
    });

    if ($('seedanceModelSelect')) {
      $('seedanceModelSelect').addEventListener('change', function() {
        var nextModel = $('seedanceModelSelect').value;
        var oldSegments = getDurationSegmentCount(state.duration, lastSeedanceModel || nextModel);
        updateDurationChipsForModel(nextModel);
        setDuration(oldSegments * getCurrentSegmentSeconds(nextModel));
        lastSeedanceModel = nextModel;
        state.activeBoardIndex = 0;
        syncSeedanceCustomSelects();
        renderWorkspace();
      });
    }

    $('seedancePreviewRefreshBtn').addEventListener('click', function() {
      state.activeBoardIndex = 0;
      renderWorkspace();
      showMessage('已按 ' + state.duration + ' 秒生成 ' + getDurationSegmentCount(state.duration) + ' 张分镜预览。');
    });

    $('seedanceStartBtn').addEventListener('click', function() {
      startRun();
    });

    $('seedanceStudioResetBtn').addEventListener('click', function() {
      stopPolling();
      releaseMediaItems(state.images);
      state.images = [];
      state.activeBoardIndex = 0;
      state.examplesOpen = true;
      state.mainView = 'storyboard';
      state.activeExampleId = '';
      state.exampleCategory = 'all';
      state.exampleSearch = '';
      state.exampleVisibleCount = Math.min(state.examplePageSize, state.exampleCatalog.length || state.examplePageSize);
      state.currentJobId = '';
      state.currentJobStatus = '';
      state.currentResultVideoUrl = '';
      state.currentJobTitle = '';
      state.currentJobError = '';
      state.currentJobProgress = null;
      state.currentJobProgressPercent = null;
      state.currentJobProgressLabel = '';
      state.currentJobProgressDetail = '';
      setSubmitBusy(false);
      if ($('seedanceExampleSearchInput')) $('seedanceExampleSearchInput').value = '';
      document.querySelectorAll('[data-seedance-category]').forEach(function(item) {
        item.classList.toggle('active', item.getAttribute('data-seedance-category') === 'all');
      });
      setMode('image_auto');
      resetFormFields();
      syncSeedanceCustomSelects();
      updateDurationChipsForModel(defaults.model);
      setDuration(getCurrentSegmentSeconds(defaults.model));
      renderWorkspace();
      showMessage('界面已重置，回到默认分镜状态。');
    });
  }

  window.initSeedanceTvcStudioView = function() {
    var root = $('content-seedance-tvc-studio');
    if (!root) return;

    if (!root.getAttribute('data-seedance-init')) {
      root.setAttribute('data-seedance-init', '1');
      bindEvents();
      resetFormFields();
      setMode(state.mode);
      updateDurationChipsForModel(defaults.model);
      setDuration(getCurrentSegmentSeconds(defaults.model));
      initSeedanceCustomSelects();
      syncSeedanceCustomSelects();
      loadRecentJobs();
      Promise.allSettled([loadLocalJobHistory(), loadCloudJobHistory()]).then(function() {
        var active = state.recentJobs.find(function(item) { return item && item.status === 'running' && item.jobId; })
          || state.recentJobs.find(function(item) { return item && item.jobId; });
        if (!active) return;
        state.currentJobId = active.jobId;
        state.examplesOpen = false;
        state.mainView = 'result';
        applyJobSnapshot(active, active.status || 'running');
        renderWorkspace();
        if (String(active.status || '').toLowerCase() === 'running') {
          refreshJobStatus(false);
        }
      });
    }

    updateExamplesBadge();
    renderWorkspace();
    ensureExampleCatalog();
  };
})();
