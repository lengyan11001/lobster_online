(function () {
  'use strict';

  var VIEW_ID = 'digital-human-koubo';
  var API = (typeof LOCAL_API_BASE !== 'undefined' && LOCAL_API_BASE) ? LOCAL_API_BASE : '';
  var state = {
    options: null,
    form: { generator: 'hifly', host: 'photo', shot: 'full', ratio: '', resolution: '', language: 'Mandarin Chinese', model: '', scene: 'none', scene_prompt: '', voice: '', compose_scene: false, packaging: [] },
    upload: null,
    quote: null,
    project: null,
    projects: [],
    preview: null,
    components: [],
    job: null,
    dirty: false,
    render: { preset: '标准', components: ['caption', 'keyword', 'cta', 'lowerthird', 'punch', 'chapter', 'stack', 'counter'], job: null, packaged: false, previewReady: false, showPreview: false, runningMode: '', qa: null, templateId: 'premium_kinetic_v1', templates: [], title: '', templateCategory: 'all', templateQuery: '', showAllTemplates: false, featuredPage: 0, recentTemplateIds: [], favoriteTemplateIds: [], recommendations: [], recommendFor: '', broll: null, brollOpen: false, bgm: null, bgmOpen: false, shotsEnabled: false, shots: [] },
    qa: null,
    timer: null
  };

  function $(id) { return document.getElementById(id); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function authOnly() {
    var h = {};
    if (typeof authHeaders === 'function') { try { h = Object.assign({}, authHeaders() || {}); } catch (e) { h = {}; } }
    Object.keys(h).forEach(function (k) { if (k.toLowerCase() === 'content-type') delete h[k]; });
    return h;
  }
  function headers() {
    var h = { 'Content-Type': 'application/json' };
    Object.assign(h, authOnly());
    return h;
  }
  function req(path, opts) {
    return fetch(API + path, opts || { headers: headers() }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) {
          var msg = d && d.detail ? (typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail)) : ('HTTP ' + r.status);
          throw new Error(msg);
        }
        return d;
      });
    });
  }
  function log(msg) {
    var el = $('kbLog');
    if (!el) return;
    var t = new Date().toTimeString().slice(0, 8);
    el.textContent = '[' + t + '] ' + msg + '\n' + el.textContent;
  }

  function toast(msg) {
    var el = document.getElementById('kbToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'kbToast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(el._toastTimer);
    el._toastTimer = setTimeout(function () { el.classList.remove('show'); }, 2800);
  }

  function injectCss() {
    if ($('kbStyle')) return;
    var css = ''
      + '#content-digital-human-koubo{background:#f8fafc;color:#334155;min-height:100%;padding:16px 20px 28px;box-sizing:border-box;font-size:13px;}'
      + '#content-digital-human-koubo .kb-root{display:grid;grid-template-columns:340px minmax(0,1fr);gap:20px;align-items:start;max-width:1600px;margin:0 auto;}'
      + '@media(max-width:980px){#content-digital-human-koubo .kb-root{grid-template-columns:1fr;}}'
      + '#content-digital-human-koubo .kb-side{display:flex;flex-direction:column;gap:12px;position:sticky;top:12px;max-height:calc(100vh - 24px);overflow-y:auto;padding-right:2px;}'
      + '#content-digital-human-koubo .kb-side-head{display:flex;align-items:center;justify-content:space-between;padding:2px 4px 6px;}'
      + '#content-digital-human-koubo .kb-side-head h3{margin:0;font-size:20px;font-weight:500;letter-spacing:-.02em;color:#0f172a;}'
      + '#content-digital-human-koubo .kb-pill{font-size:11px;padding:3px 9px;border-radius:999px;background:#eef2ff;color:#4f46e5;border:1px solid #e0e7ff;}'
      + '#content-digital-human-koubo .kb-pill.ok{background:#ecfdf5;color:#059669;border-color:#a7f3d0;}'
      + '#content-digital-human-koubo .kb-pill.warn{background:#fffbeb;color:#b45309;border-color:#fde68a;}'
      + '#content-digital-human-koubo .kb-card{background:#fff;border:1px solid #eef2f7;border-radius:16px;padding:14px;box-shadow:0 4px 20px -4px rgba(15,23,42,.04);}'
      + '#content-digital-human-koubo .kb-sec-title{display:flex;align-items:center;justify-content:space-between;font-size:13px;font-weight:500;color:#0f172a;margin-bottom:10px;}'
      + '#content-digital-human-koubo .kb-sec-title small{font-weight:400;font-size:11px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-drop{border:1.5px dashed #dbe3ef;border-radius:14px;background:#fbfdff;padding:18px 12px;text-align:center;cursor:pointer;transition:.15s;}'
      + '#content-digital-human-koubo .kb-drop:hover{border-color:#93c5fd;background:#f5f9ff;}'
      + '#content-digital-human-koubo .kb-drop b{display:block;font-size:13px;color:#334155;font-weight:500;}'
      + '#content-digital-human-koubo .kb-drop small{display:block;margin-top:4px;font-size:11px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-photo{width:100%;max-height:230px;object-fit:contain;border-radius:12px;background:#0f172a;display:block;}'
      + '#content-digital-human-koubo .kb-checks{margin:8px 0 0;padding:0;list-style:none;font-size:11px;color:#64748b;line-height:1.8;}'
      + '#content-digital-human-koubo .kb-checks .ok{color:#059669;}'
      + '#content-digital-human-koubo .kb-checks .warn{color:#b45309;}'
      + '#content-digital-human-koubo textarea{width:100%;min-height:120px;box-sizing:border-box;padding:10px 12px;background:#fbfdff;border:1px solid #e2e8f0;border-radius:12px;color:#0f172a;font-size:13px;line-height:1.7;resize:vertical;outline:none;}'
      + '#content-digital-human-koubo textarea:focus{border-color:#93c5fd;background:#fff;}'
      + '#content-digital-human-koubo select,#content-digital-human-koubo input[type=text]{width:100%;box-sizing:border-box;padding:7px 9px;background:#fff;border:1px solid #e2e8f0;border-radius:9px;color:#334155;font-size:12px;outline:none;}'
      + '#content-digital-human-koubo select:focus,#content-digital-human-koubo input[type=text]:focus{border-color:#93c5fd;}'
      + '#content-digital-human-koubo .kb-field{display:flex;flex-direction:column;gap:5px;font-size:11px;color:#64748b;}'
      + '#content-digital-human-koubo .kb-grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px;}'
      + '#content-digital-human-koubo .kb-collapse{display:flex;align-items:center;justify-content:space-between;width:100%;background:none;border:0;padding:0;cursor:pointer;font-size:13px;font-weight:500;color:#0f172a;}'
      + '#content-digital-human-koubo .kb-collapse span{font-weight:400;font-size:11px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-params{display:none;margin-top:12px;padding-top:12px;border-top:1px solid #f1f5f9;flex-direction:column;gap:10px;}'
      + '#content-digital-human-koubo .kb-params.open{display:flex;}'
      + '#content-digital-human-koubo .kb-pack{display:flex;flex-wrap:wrap;gap:6px 12px;margin-top:6px;}'
      + '#content-digital-human-koubo .kb-pack label{display:inline-flex;align-items:center;gap:4px;font-size:11px;color:#475569;}'
      + '#content-digital-human-koubo .kb-btn{border:1px solid #e2e8f0;background:#fff;color:#475569;border-radius:9px;padding:7px 12px;font-size:12px;cursor:pointer;transition:.15s;}'
      + '#content-digital-human-koubo .kb-btn:hover{border-color:#93c5fd;color:#2563eb;}'
      + '#content-digital-human-koubo .kb-btn:disabled{opacity:.5;cursor:default;}'
      + '#content-digital-human-koubo .kb-btn-primary{width:100%;border:0;border-radius:12px;padding:11px 14px;font-size:13px;font-weight:500;color:#fff;background:linear-gradient(135deg,#3b82f6,#2563eb);cursor:pointer;box-shadow:0 6px 16px -6px rgba(37,99,235,.6);}'
      + '#content-digital-human-koubo .kb-btn-primary:disabled{background:#cbd5e1;box-shadow:none;cursor:default;}'
      + '#content-digital-human-koubo .kb-cta{display:flex;flex-direction:column;gap:8px;position:sticky;bottom:0;padding:10px 0 2px;background:linear-gradient(to top,#f8fafc 72%,rgba(248,250,252,0));}'
      + '#content-digital-human-koubo .kb-cta-row{display:flex;gap:8px;}'
      + '#content-digital-human-koubo .kb-cta-row .kb-btn{flex:1;}'
      + '#content-digital-human-koubo .kb-hint{font-size:11px;color:#94a3b8;text-align:center;}'
      + '#content-digital-human-koubo .kb-stage{display:flex;flex-direction:column;gap:14px;min-width:0;}'
      + '#content-digital-human-koubo .kb-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:2px 2px 0;}'
      + '#content-digital-human-koubo .kb-crumb{font-size:12px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-crumb b{color:#0f172a;font-weight:500;}'
      + '#content-digital-human-koubo .kb-tools{display:flex;gap:8px;flex-wrap:wrap;}'
      + '#content-digital-human-koubo .kb-preview{position:relative;background:#0f172a;border-radius:18px;min-height:380px;display:flex;align-items:center;justify-content:center;overflow:hidden;}'
      + '#content-digital-human-koubo .kb-preview video{width:100%;height:100%;max-height:520px;object-fit:contain;background:#000;}'
      + '#content-digital-human-koubo .kb-empty{color:#64748b;font-size:13px;text-align:center;line-height:1.9;padding:24px;}'
      + '#content-digital-human-koubo .kb-empty b{display:block;color:#e2e8f0;font-size:15px;font-weight:500;margin-bottom:4px;}'
      + '#content-digital-human-koubo .kb-progress{position:absolute;left:16px;right:16px;bottom:14px;background:rgba(15,23,42,.72);backdrop-filter:blur(6px);border:1px solid rgba(148,163,184,.25);border-radius:10px;padding:8px 12px;color:#e2e8f0;font-size:12px;}'
      + '#content-digital-human-koubo .kb-progress .bar{height:4px;background:rgba(148,163,184,.25);border-radius:999px;overflow:hidden;margin-top:6px;}'
      + '#content-digital-human-koubo .kb-progress .bar i{display:block;height:100%;background:#60a5fa;width:0;transition:width .3s;}'
      + '#content-digital-human-koubo .kb-meta{display:flex;gap:8px;flex-wrap:wrap;font-size:11px;color:#64748b;}'
      + '#content-digital-human-koubo .kb-chip{background:#fff;border:1px solid #eef2f7;border-radius:999px;padding:4px 10px;}'
      + '#content-digital-human-koubo table{width:100%;border-collapse:collapse;font-size:12px;}'
      + '#content-digital-human-koubo th,#content-digital-human-koubo td{text-align:left;padding:7px 8px;border-bottom:1px solid #f1f5f9;vertical-align:top;color:#475569;}'
      + '#content-digital-human-koubo th{color:#94a3b8;font-weight:400;font-size:11px;}'
      + '#content-digital-human-koubo .kb-total{display:flex;justify-content:space-between;align-items:center;margin-top:10px;font-size:12px;color:#64748b;}'
      + '#content-digital-human-koubo .kb-total b{color:#0f172a;font-size:14px;}'
      + '#content-digital-human-koubo .kb-packbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12px;color:#64748b;}'
      + '#content-digital-human-koubo .kb-packbar select{padding:5px 8px;border:1px solid #e2e8f0;border-radius:8px;font-size:12px;color:#334155;background:#fff;}'
      + '#content-digital-human-koubo .kb-packbar label{display:inline-flex;align-items:center;gap:4px;font-size:11px;}'
      + '#content-digital-human-koubo .kb-templates{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px;align-items:start;}'
      + '#content-digital-human-koubo .kb-tpl{text-align:left;background:#fff;border:1px solid #eef2f7;border-radius:14px;padding:9px;cursor:pointer;transition:border-color .15s,box-shadow .15s,transform .15s;}'
      + '#content-digital-human-koubo .kb-tpl:hover{transform:translateY(-1px);box-shadow:0 8px 20px -10px rgba(15,23,42,.25);}'
      + '#content-digital-human-koubo .kb-tpl.on{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.12);}'
      + '#content-digital-human-koubo .kb-tpl-preview{display:block;height:58px;border-radius:10px;margin-bottom:8px;position:relative;overflow:hidden;}'
      + '#content-digital-human-koubo .kb-tpl-cover{display:block;width:100%;height:74px;object-fit:cover;border-radius:10px;margin-bottom:8px;background:#eef2f7;}'
      + '#content-digital-human-koubo .kb-tpl-coverwrap{position:relative;margin-bottom:8px;}'
      + '#content-digital-human-koubo .kb-tpl-coverwrap .kb-tpl-cover{margin-bottom:0;}'
      + '#content-digital-human-koubo .kb-tpl-play{position:absolute;right:6px;bottom:6px;border:0;border-radius:999px;padding:3px 8px;background:rgba(5,9,18,.72);color:#fff;font-size:10px;cursor:pointer;backdrop-filter:blur(6px);}'
      + '#content-digital-human-koubo .kb-tpl-play:hover{background:rgba(37,99,235,.9);}'
      + '#content-digital-human-koubo .kb-tpl-fav{position:absolute;right:6px;top:6px;width:22px;height:22px;border:1px solid rgba(255,255,255,.2);border-radius:50%;background:rgba(5,9,18,.58);color:#fff;font-size:13px;line-height:1;cursor:pointer;backdrop-filter:blur(6px);padding:0;}'
      + '#content-digital-human-koubo .kb-tpl-fav.on{color:#FFD84D;background:rgba(5,9,18,.8);}'
      + '#content-digital-human-koubo .kb-broll{border-top:1px solid #f1f5f9;margin-top:10px;padding-top:10px;}'
      + '#content-digital-human-koubo .kb-broll-head{display:flex;align-items:center;gap:8px;font-size:12px;color:#334155;}'
      + '#content-digital-human-koubo .kb-broll-head small{font-size:10px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-broll-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:8px;font-size:11px;color:#475569;}'
      + '#content-digital-human-koubo .kb-broll-row label{display:inline-flex;align-items:center;gap:5px;}'
      + '#content-digital-human-koubo .kb-broll-row select,#content-digital-human-koubo .kb-broll-row input[type=number]{border:1px solid #e2e8f0;border-radius:7px;padding:5px 7px;font-size:11px;color:#334155;background:#fff;}'
      + '#content-digital-human-koubo .kb-broll-row input[type=number]{width:58px;}'
      + '#content-digital-human-koubo .kb-broll-name{font-size:11px;color:#64748b;max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-broll-note{font-size:10px;color:#94a3b8;margin-top:6px;}'
      + '#content-digital-human-koubo .kb-tpl-bottom-actions{display:flex;gap:8px;margin-top:10px;}'
      + '#content-digital-human-koubo .kb-tpl-bottom-actions .kb-tpl-more{flex:1;margin-top:0;}'
      + '#kbSampleModal{position:fixed;inset:0;z-index:10050;background:rgba(5,9,18,.76);display:flex;align-items:center;justify-content:center;padding:24px;}'
      + '#kbSampleModal .kb-sample-card{width:min(92vw,520px);background:#0b1220;border-radius:18px;padding:12px;box-shadow:0 30px 80px rgba(0,0,0,.45);}'
      + '#kbSampleModal .kb-sample-head{display:flex;align-items:center;justify-content:space-between;color:#e2e8f0;font-size:12px;margin-bottom:8px;}'
      + '#kbSampleModal video{width:100%;max-height:78vh;border-radius:12px;background:#000;}'
      + '#kbSampleModal .kb-sample-close{border:1px solid rgba(255,255,255,.2);background:transparent;color:#e2e8f0;border-radius:999px;padding:4px 9px;font-size:11px;cursor:pointer;}'
      + '#kbTplModal{position:fixed;inset:0;z-index:10060;background:rgba(5,9,18,.78);display:flex;align-items:center;justify-content:center;padding:20px;}'
      + '#kbTplModal .kb-detail{width:min(94vw,880px);max-height:92vh;overflow:auto;background:#fff;border-radius:18px;box-shadow:0 30px 80px rgba(0,0,0,.45);display:grid;grid-template-columns:minmax(240px,42%) 1fr;}'
      + '#kbTplModal .kb-detail-media{background:#0b1220;padding:14px;border-radius:18px 0 0 18px;display:flex;flex-direction:column;gap:10px;justify-content:center;}'
      + '#kbTplModal .kb-detail-media img{display:block;width:100%;border-radius:12px;object-fit:cover;background:#111827;}'
      + '#kbTplModal .kb-detail-media video{display:block;width:100%;max-height:52vh;object-fit:contain;border-radius:12px;background:#000;}'
      + '#kbTplModal .kb-detail-nosample{border:1px dashed rgba(255,255,255,.24);border-radius:12px;padding:26px 12px;text-align:center;color:#cbd5e1;font-size:12px;line-height:1.7;}'
      + '#kbTplModal .kb-detail-nosample small{color:#64748b;font-size:10.5px;}'
      + '#kbTplModal .kb-detail-body{padding:18px 20px 18px;min-width:0;}'
      + '#kbTplModal .kb-detail-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;}'
      + '#kbTplModal .kb-detail-head h3{margin:0;font-size:19px;color:#0f172a;letter-spacing:-.01em;}'
      + '#kbTplModal .kb-detail-sub{font-size:11px;color:#94a3b8;margin-top:4px;}'
      + '#kbTplModal .kb-detail-desc{font-size:12.5px;line-height:1.7;color:#475569;margin-top:10px;}'
      + '#kbTplModal .kb-detail-sec{margin-top:14px;}'
      + '#kbTplModal .kb-detail-sec>b{display:block;font-size:11.5px;color:#0f172a;margin-bottom:6px;}'
      + '#kbTplModal .kb-detail-chips{display:flex;flex-wrap:wrap;gap:6px;}'
      + '#kbTplModal .kb-detail-chip{border:1px solid #e2e8f0;background:#f8fafc;color:#475569;border-radius:999px;padding:4px 9px;font-size:10.5px;}'
      + '#kbTplModal .kb-detail-kv{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;}'
      + '#kbTplModal .kb-detail-kv span{background:#f8fafc;border-radius:8px;padding:6px 8px;font-size:11px;color:#475569;}'
      + '#kbTplModal .kb-detail-note{font-size:11px;line-height:1.7;color:#64748b;background:#f8fafc;border-radius:8px;padding:8px 10px;}'
      + '#kbTplModal .kb-detail-actions{display:flex;align-items:center;gap:8px;margin-top:18px;position:sticky;bottom:-18px;background:#fff;padding:10px 0 0;border-top:1px solid #f1f5f9;}'
      + '#kbTplModal .kb-detail-actions .kb-btn{width:auto;}'
      + '#kbTplModal .kb-detail-actions .kb-btn-primary{width:auto;min-width:150px;margin:0;}'
      + '#kbTplModal .kb-fit{display:flex;gap:6px;flex-wrap:wrap;}'
      + '#kbTplModal .kb-fit-pill{font-size:10.5px;padding:3px 8px;border-radius:999px;background:#f1f5f9;color:#475569;}'
      + '#kbTplModal .kb-fit-pill.ok{background:#ecfdf5;color:#047857;}'
      + '#kbTplModal .kb-fit-pill.warn{background:#fffbeb;color:#b45309;}'
      + '@media (max-width:760px){#kbTplModal .kb-detail{grid-template-columns:1fr;}#kbTplModal .kb-detail-media{border-radius:18px 18px 0 0;}}'
      + '#content-digital-human-koubo .kb-tpl-foot{display:flex;align-items:center;justify-content:space-between;gap:6px;}'
      + '#content-digital-human-koubo .kb-tpl-detail{border:0;background:transparent;color:#2563eb;font-size:10.5px;cursor:pointer;padding:2px 0;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-tpl-detail:hover{text-decoration:underline;}'
      + '#content-digital-human-koubo .kb-tpl-use{border:1px solid #bfdbfe;background:#eff6ff;color:#1d4ed8;border-radius:999px;padding:2px 7px;font-size:10.5px;cursor:pointer;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-tpl-use:hover{background:#dbeafe;}'
      + '#content-digital-human-koubo .kb-multi{border-top:1px solid #f1f5f9;margin-top:10px;padding-top:10px;}'
      + '#content-digital-human-koubo .kb-multi-head{display:flex;align-items:center;gap:8px;font-size:12px;color:#334155;}'
      + '#content-digital-human-koubo .kb-multi-head label{display:inline-flex;align-items:center;gap:4px;font-size:11px;color:#475569;}'
      + '#content-digital-human-koubo .kb-multi-row{display:grid;grid-template-columns:44px minmax(0,1fr) minmax(0,1fr);gap:6px;align-items:center;margin-top:6px;}'
      + '#content-digital-human-koubo .kb-multi-row span{font-size:10.5px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-multi-row select,#content-digital-human-koubo .kb-multi-row input{border:1px solid #e2e8f0;border-radius:7px;padding:5px 7px;font-size:11px;color:#334155;background:#fff;min-width:0;}'
      + '#content-digital-human-koubo .kb-multi-note{font-size:10px;color:#94a3b8;margin-top:6px;line-height:1.6;}'
      + '#kbToast{position:fixed;left:50%;top:22px;transform:translateX(-50%) translateY(-6px);z-index:10100;background:rgba(15,23,42,.94);color:#fff;padding:10px 16px;border-radius:12px;font-size:12px;line-height:1.5;box-shadow:0 16px 40px rgba(0,0,0,.28);opacity:0;transition:opacity .18s,transform .18s;pointer-events:none;max-width:80vw;}'
      + '#kbToast.show{opacity:1;transform:translateX(-50%) translateY(0);}'
      + '#kbCreateModal{position:fixed;inset:0;z-index:10090;background:rgba(5,9,18,.8);display:flex;align-items:center;justify-content:center;padding:20px;}'
      + '#kbCreateModal .kb-create{width:min(94vw,820px);max-height:92vh;overflow:auto;background:#fff;border-radius:18px;box-shadow:0 30px 80px rgba(0,0,0,.45);display:grid;grid-template-columns:minmax(220px,38%) 1fr;}'
      + '#kbCreateModal .kb-create-media{background:#0b1220;padding:14px;border-radius:18px 0 0 18px;display:flex;flex-direction:column;gap:8px;}'
      + '#kbCreateModal .kb-create-media video{width:100%;max-height:46vh;object-fit:contain;border-radius:12px;background:#000;}'
      + '#kbCreateModal .kb-create-tpl{color:#e2e8f0;font-size:12px;text-align:center;}'
      + '#kbCreateModal .kb-create-tpl b{color:#fff;}'
      + '#kbCreateModal .kb-create-body{padding:18px 20px;min-width:0;}'
      + '#kbCreateModal .kb-create-head{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;}'
      + '#kbCreateModal h3{margin:0;font-size:19px;color:#0f172a;}'
      + '#kbCreateModal .kb-create-sub{font-size:11.5px;color:#64748b;line-height:1.7;margin-top:8px;}'
      + '#kbCreateModal .kb-create-sec{margin-top:14px;}'
      + '#kbCreateModal .kb-create-sec>b{display:block;font-size:12px;color:#0f172a;margin-bottom:6px;}'
      + '#kbCreateModal .kb-create-photo{display:flex;align-items:center;gap:10px;border:1px dashed #cbd5e1;border-radius:12px;padding:10px;cursor:pointer;}'
      + '#kbCreateModal .kb-create-photo img{width:56px;height:56px;object-fit:cover;border-radius:10px;background:#eef2f7;}'
      + '#kbCreateModal .kb-create-photo span{font-size:11.5px;color:#64748b;line-height:1.6;}'
      + '#kbCreateModal textarea{width:100%;box-sizing:border-box;border:1px solid #e2e8f0;border-radius:10px;padding:9px 11px;font-size:12px;color:#0f172a;resize:vertical;min-height:110px;}'
      + '#kbCreateModal input[type=text]{width:100%;box-sizing:border-box;border:1px solid #e2e8f0;border-radius:10px;padding:8px 11px;font-size:12px;color:#0f172a;}'
      + '#kbCreateModal .kb-create-meta{font-size:11px;color:#64748b;margin-top:10px;}'
      + '#kbCreateModal .kb-create-actions{display:flex;align-items:center;gap:8px;margin-top:14px;}'
      + '#kbCreateModal .kb-create-actions .kb-btn-primary{width:auto;min-width:160px;margin:0;}'
      + '#kbCreateModal .kb-create-bar{height:6px;background:#eef2f7;border-radius:999px;overflow:hidden;margin-top:10px;}'
      + '#kbCreateModal .kb-create-bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,#3b82f6,#8b5cf6);transition:width .3s;}'
      + '#kbCreateModal .kb-create-step{display:flex;align-items:center;gap:8px;font-size:11.5px;color:#64748b;padding:5px 0;}'
      + '#kbCreateModal .kb-create-step .dot{width:18px;height:18px;border-radius:50%;background:#eef2f7;color:#64748b;font-size:10px;display:flex;align-items:center;justify-content:center;flex:none;}'
      + '#kbCreateModal .kb-create-step.done .dot{background:#dcfce7;color:#15803d;}'
      + '#kbCreateModal .kb-create-step.run .dot{background:#dbeafe;color:#1d4ed8;}'
      + '#kbCreateModal .kb-create-result video{width:100%;max-height:52vh;border-radius:12px;background:#000;}'
      + '@media(max-width:760px){#kbCreateModal .kb-create{grid-template-columns:1fr;}#kbCreateModal .kb-create-media{border-radius:18px 18px 0 0;}}'
      + '#kbMatrixModal{position:fixed;inset:0;z-index:10080;background:rgba(5,9,18,.78);display:flex;align-items:center;justify-content:center;padding:20px;}'
      + '#kbMatrixModal .kb-matrix{width:min(94vw,760px);max-height:92vh;overflow:auto;background:#fff;border-radius:18px;padding:16px 18px;box-shadow:0 30px 80px rgba(0,0,0,.45);}'
      + '#kbMatrixModal .kb-matrix-head{display:flex;align-items:center;justify-content:space-between;}'
      + '#kbMatrixModal h3{margin:0;font-size:17px;color:#0f172a;}'
      + '#kbMatrixModal .kb-matrix-sec{margin-top:12px;}'
      + '#kbMatrixModal .kb-matrix-sec>b{display:block;font-size:12px;color:#0f172a;margin-bottom:6px;}'
      + '#kbMatrixModal .kb-matrix-sec small{font-size:10.5px;color:#94a3b8;margin-left:6px;}'
      + '#kbMatrixModal textarea{width:100%;box-sizing:border-box;border:1px solid #e2e8f0;border-radius:10px;padding:9px 11px;font-size:12px;color:#0f172a;resize:vertical;min-height:96px;}'
      + '#kbMatrixModal .kb-matrix-photos{display:flex;gap:8px;flex-wrap:wrap;max-height:150px;overflow:auto;border:1px solid #eef2f7;border-radius:10px;padding:8px;}'
      + '#kbMatrixModal .kb-matrix-photo{display:flex;align-items:center;gap:6px;border:1px solid #e2e8f0;border-radius:9px;padding:4px 7px;font-size:10.5px;color:#475569;cursor:pointer;}'
      + '#kbMatrixModal .kb-matrix-photo img{width:28px;height:28px;object-fit:cover;border-radius:6px;}'
      + '#kbMatrixModal .kb-matrix-photo.on{border-color:#93c5fd;background:#eff6ff;color:#1d4ed8;}'
      + '#kbMatrixModal .kb-matrix-sum{font-size:11.5px;color:#64748b;margin-top:10px;}'
      + '#kbMatrixModal .kb-matrix-actions{display:flex;gap:8px;align-items:center;margin-top:12px;}'
      + '#kbMatrixModal .kb-matrix-list{max-height:200px;overflow:auto;border:1px solid #eef2f7;border-radius:10px;padding:8px;margin-top:8px;}'
      + '#kbMatrixModal .kb-matrix-item{display:flex;align-items:center;gap:8px;font-size:11px;color:#475569;padding:5px 2px;border-bottom:1px dashed #f1f5f9;}'
      + '#kbMatrixModal .kb-matrix-item b{flex:1;font-weight:500;color:#0f172a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}'
      + '#kbMatrixModal .kb-matrix-bar{height:5px;background:#eef2f7;border-radius:999px;overflow:hidden;margin-top:8px;}'
      + '#kbMatrixModal .kb-matrix-bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,#3b82f6,#8b5cf6);}'
      + '#kbBatchModal{position:fixed;inset:0;z-index:10070;background:rgba(5,9,18,.78);display:flex;align-items:center;justify-content:center;padding:20px;}'
      + '#kbBatchModal .kb-batch{width:min(94vw,720px);max-height:90vh;overflow:auto;background:#fff;border-radius:18px;padding:16px 18px;box-shadow:0 30px 80px rgba(0,0,0,.45);}'
      + '#kbBatchModal .kb-batch-head{display:flex;align-items:center;justify-content:space-between;gap:10px;}'
      + '#kbBatchModal .kb-batch-head h3{margin:0;font-size:17px;color:#0f172a;}'
      + '#kbBatchModal .kb-batch-sub{font-size:11.5px;color:#64748b;margin-top:6px;line-height:1.7;}'
      + '#kbBatchModal .kb-batch-sub b{color:#2563eb;}'
      + '#kbBatchModal .kb-batch-list{display:flex;flex-direction:column;gap:2px;max-height:280px;overflow:auto;margin:10px 0;border:1px solid #eef2f7;border-radius:10px;padding:8px;}'
      + '#kbBatchModal .kb-batch-row{display:flex;align-items:center;gap:8px;font-size:12px;color:#334155;padding:5px 2px;}'
      + '#kbBatchModal .kb-batch-row input{accent-color:#2563eb;}'
      + '#kbBatchModal .kb-batch-row small{color:#94a3b8;font-size:10.5px;margin-left:auto;}'
      + '#kbBatchModal .kb-batch-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:12px;}'
      + '#kbBatchModal .kb-batch-actions label{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:#475569;}'
      + '#kbBatchModal .kb-batch-actions .kb-btn-primary{width:auto;min-width:140px;margin:0;}'
      + '#kbBatchModal .kb-batch-bar{height:5px;background:#eef2f7;border-radius:999px;overflow:hidden;margin:10px 0;}'
      + '#kbBatchModal .kb-batch-bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,#3b82f6,#8b5cf6);transition:width .3s;}'
      + '#kbBatchModal .kb-batch-item{display:flex;align-items:center;gap:8px;font-size:11.5px;color:#475569;padding:7px 2px;border-bottom:1px dashed #f1f5f9;}'
      + '#kbBatchModal .kb-batch-item:last-child{border-bottom:0;}'
      + '#kbBatchModal .kb-batch-item b{flex:1;min-width:0;font-weight:500;color:#0f172a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}'
      + '#kbBatchModal .kb-batch-item a{color:#2563eb;text-decoration:none;font-size:11px;white-space:nowrap;}'
      + '#kbBatchModal .kb-batch-pill{font-size:10px;padding:2px 7px;border-radius:999px;background:#eef2f7;color:#475569;white-space:nowrap;}'
      + '#kbBatchModal .kb-batch-pill.run{background:#dbeafe;color:#1d4ed8;}'
      + '#kbBatchModal .kb-batch-pill.done{background:#dcfce7;color:#15803d;}'
      + '#kbBatchModal .kb-batch-pill.err{background:#fee2e2;color:#b91c1c;}'
      + '#kbBatchModal .kb-batch-close{border:1px solid #e2e8f0;background:#fff;color:#475569;border-radius:999px;padding:4px 10px;font-size:11px;cursor:pointer;}'
      + '#content-digital-human-koubo .kb-tpl-preview:after{content:"";position:absolute;left:10%;right:10%;top:20%;height:12px;border-radius:6px;background:rgba(255,255,255,.82);box-shadow:0 10px 0 -2px rgba(255,255,255,.32);}'
      + '#content-digital-human-koubo .kb-tpl b{display:block;font-size:12px;color:#0f172a;margin-bottom:3px;}'
      + '#content-digital-human-koubo .kb-tpl p{font-size:11px;line-height:1.45;color:#64748b;margin:0 0 5px;min-height:31px;}'
      + '#content-digital-human-koubo .kb-tpl small{font-size:10px;color:#94a3b8;}'
      + '#content-digital-human-koubo .kb-tpl-tools{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:0 0 10px;}'
      + '#content-digital-human-koubo .kb-tpl-search{flex:1;min-width:130px;border:1px solid #e2e8f0;border-radius:999px;padding:6px 11px;font-size:11px;color:#0f172a;background:#fff;}'
      + '#content-digital-human-koubo .kb-tpl-chip{border:1px solid #e2e8f0;background:#fff;border-radius:999px;padding:4px 9px;font-size:10.5px;color:#475569;cursor:pointer;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-tpl-chip.on{background:#eff6ff;border-color:#93c5fd;color:#1d4ed8;}'
      + '#content-digital-human-koubo .kb-tpl-more{width:100%;border:1px dashed #bfdbfe;background:#f8fbff;color:#2563eb;border-radius:10px;padding:8px;font-size:11px;cursor:pointer;margin-top:10px;}'
      + '#content-digital-human-koubo .kb-visual-qa{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-start;margin:8px 0 2px;padding:8px 10px;border-radius:10px;background:#f8fafc;border:1px solid #eef2f7;}'
      + '#content-digital-human-koubo .kb-visual-qa b{font-size:11px;color:#334155;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-visual-qa .kb-qa-pill{font-size:10.5px;padding:2px 7px;border-radius:999px;background:#e0f2fe;color:#0369a1;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-visual-qa .kb-qa-pill.warn{background:#fef3c7;color:#92400e;}'
      + '#content-digital-human-koubo .kb-visual-qa small{font-size:10.5px;color:#64748b;line-height:1.5;}'
      + '#content-digital-human-koubo .kb-title-input{display:flex;align-items:center;gap:8px;margin-bottom:10px;}'
      + '#content-digital-human-koubo .kb-title-input label{font-size:12px;color:#334155;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-title-input input{flex:1;min-width:0;border:1px solid #e2e8f0;border-radius:9px;padding:8px 10px;font-size:12px;color:#0f172a;background:#fff;}'
      + '#content-digital-human-koubo .kb-title-input small{font-size:10px;color:#94a3b8;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-recommend{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:0 0 10px;padding:8px 10px;background:#f8fbff;border:1px solid #e0f2fe;border-radius:10px;}'
      + '#content-digital-human-koubo .kb-recommend b{font-size:11px;color:#0369a1;white-space:nowrap;}'
      + '#content-digital-human-koubo .kb-rec-chip{border:1px solid #bae6fd;background:#fff;color:#0c4a6e;border-radius:999px;padding:4px 9px;font-size:10.5px;cursor:pointer;}'
      + '#content-digital-human-koubo .kb-rec-chip:hover{background:#e0f2fe;}'
      + '#content-digital-human-koubo #kbQuote,#content-digital-human-koubo #kbSave,#content-digital-human-koubo #kbExport{display:none;}'
      + '#content-digital-human-koubo .kb-log{max-height:52px;overflow:auto;}'
      + '#content-digital-human-koubo .kb-proj{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:9px 2px;border-bottom:1px solid #f1f5f9;font-size:12px;}'
      + '#content-digital-human-koubo .kb-proj:last-child{border-bottom:0;}'
      + '#content-digital-human-koubo .kb-proj b{color:#0f172a;font-weight:500;}'
      + '#content-digital-human-koubo .kb-proj small{color:#94a3b8;font-size:11px;}'
      + '#content-digital-human-koubo .kb-log{margin-top:2px;padding:10px 12px;background:#fff;border:1px solid #eef2f7;border-radius:12px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:#94a3b8;max-height:120px;overflow:auto;white-space:pre-wrap;}'
      + '#kbDepsModal{position:fixed;inset:0;z-index:3200;}'
      + '#kbDepsModal .kb-modal-backdrop{position:absolute;inset:0;background:rgba(15,23,42,.45);}'
      + '#kbDepsModal .kb-modal-card{position:relative;width:min(92vw,560px);margin:10vh auto 0;border-radius:20px;background:#fff;color:#0f172a;box-shadow:0 32px 80px rgba(15,23,42,.28);overflow:hidden;}'
      + '#kbDepsModal .kb-modal-head{display:flex;align-items:center;justify-content:space-between;padding:18px 22px 10px;}'
      + '#kbDepsModal .kb-modal-head h4{margin:0;font-size:17px;font-weight:500;}'
      + '#kbDepsModal .kb-modal-close{border:none;background:transparent;font-size:22px;line-height:1;cursor:pointer;color:#94a3b8;}'
      + '#kbDepsModal .kb-modal-body{padding:0 22px 12px;max-height:min(66vh,600px);overflow:auto;}'
      + '#kbDepsModal .kb-modal-sub{font-size:12px;color:#64748b;margin:0 0 10px;line-height:1.6;}'
      + '#kbDepsModal .kb-dep{display:grid;grid-template-columns:1fr auto;gap:3px 10px;padding:8px 0;border-bottom:1px dashed #eef2f7;}'
      + '#kbDepsModal .kb-dep:last-child{border-bottom:0;}'
      + '#kbDepsModal .kb-dep b{font-size:13px;font-weight:500;}'
      + '#kbDepsModal .kb-dep small{display:block;color:#94a3b8;font-size:11px;margin-top:2px;line-height:1.5;word-break:break-all;}'
      + '#kbDepsModal .kb-dep-right{display:flex;align-items:center;gap:6px;}'
      + '#kbDepsModal .kb-pill{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid transparent;}'
      + '#kbDepsModal .kb-pill.ok{background:#ecfdf5;color:#059669;border-color:#a7f3d0;}'
      + '#kbDepsModal .kb-pill.missing{background:#fef2f2;color:#dc2626;border-color:#fecaca;}'
      + '#kbDepsModal .kb-pill.installing{background:#eff6ff;color:#2563eb;border-color:#bfdbfe;}'
      + '#kbDepsModal .kb-bar{grid-column:1/-1;height:4px;background:#eef2f7;border-radius:999px;overflow:hidden;display:none;}'
      + '#kbDepsModal .kb-bar.on{display:block;}'
      + '#kbDepsModal .kb-bar i{display:block;height:100%;background:#3b82f6;width:0;transition:width .3s;}'
      + '#kbDepsModal .kb-modal-foot{display:flex;justify-content:space-between;gap:10px;padding:12px 22px 18px;border-top:1px solid #f1f5f9;}'
      + '#kbDepsModal .kb-btn{font-size:13px;padding:7px 14px;border-radius:9px;border:1px solid #e2e8f0;background:#fff;cursor:pointer;}'
      + '#kbDepsModal .kb-btn.primary{background:linear-gradient(135deg,#3b82f6,#2563eb);border-color:transparent;color:#fff;}'
      + '#kbDepsModal .kb-install{font-size:12px;padding:4px 10px;border-radius:7px;border:1px solid #e2e8f0;background:#fff;cursor:pointer;}';
    var style = document.createElement('style');
    style.id = 'kbStyle';
    css += '#content-digital-human-koubo .kb-gen-row{display:flex;gap:8px;margin:0 0 8px;}'
      + '#content-digital-human-koubo .kb-gen-row label{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border:1px solid #e2e8f0;border-radius:999px;font-size:12px;color:#475569;cursor:pointer;background:#fff;}'
      + '#content-digital-human-koubo .kb-gen-row input{accent-color:#2563eb;}'
      + '#content-digital-human-koubo .kb-hifly-avatar select{height:34px;border:1px solid #e2e8f0;border-radius:8px;padding:0 8px;font-size:12px;}';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function shell() {
    return '<div class="kb-root">'
      + '<aside class="kb-side">'
      + '  <div class="kb-side-head"><h3>数字人3.0</h3><span class="kb-pill" id="kbStatus">未保存</span></div>'
      + '  <div class="kb-card"><div class="kb-sec-title">形象<small id="kbGenLabel">海螺数字人</small></div>'
      + '    <div class="kb-gen-row" id="kbGenRow">'
      + '      <label><input type="radio" name="kbGen" value="hifly" checked><span>海螺数字人</span></label>'
      + '      <label><input type="radio" name="kbGen" value="grok"><span>Grok AI</span></label>'
      + '    </div>'
      + '    <div id="kbAvatar"></div></div>'
      + '  <div class="kb-card"><div class="kb-sec-title">口播稿<small id="kbCount">0 字</small></div><textarea id="kbText" placeholder="粘贴口播稿。按句切分，每段不超过约 15 秒。"></textarea></div>'
      + '  <div class="kb-card"><button type="button" class="kb-collapse" id="kbParamToggle">高级设置<span id="kbParamSummary">一般不用改</span></button><div class="kb-params" id="kbParams"></div></div>'
      + '  <div class="kb-cta"><button type="button" class="kb-btn-primary" id="kbGenerate">生成第 1 段</button>'
      + '    <div class="kb-cta-row"><button type="button" class="kb-btn" id="kbGenerateAll">生成全部</button></div>'
      + '    <div class="kb-hint">先出第 1 段确认效果，再生成全部</div></div>'
      + '</aside>'
      + '<section class="kb-stage">'
      + '  <div class="kb-toolbar"><div class="kb-crumb">工作台 / <b id="kbProjName">未命名项目</b></div>'
      + '    <div class="kb-tools"><button type="button" class="kb-btn" id="kbSave">保存项目</button><button type="button" class="kb-btn" id="kbExport">导出 tasks.json</button><button type="button" class="kb-btn" id="kbDeps">运行环境</button><button type="button" class="kb-btn" id="kbBack">返回商店</button></div></div>'
      + '  <div class="kb-preview" id="kbPreview"><div class="kb-empty"><b>还没有成片</b>左侧填好形象与口播稿，点击「生成第 1 段」</div></div>'
      + '  <div class="kb-meta" id="kbMeta"></div>'
      + '  <div id="kbPack"></div>'
      + '  <div class="kb-card"><div class="kb-sec-title">项目记录<span><small id="kbProjCount"></small> <button type="button" class="kb-btn" id="kbProjRefresh" style="margin-left:8px;">刷新</button></span></div><div id="kbProjects"></div></div>'
      + '  <div class="kb-log" id="kbLog">准备就绪</div>'
      + '<div id="kbHiflyTrainModal" style="display:none;position:fixed;inset:0;z-index:10100;background:rgba(15,23,42,.45);align-items:center;justify-content:center;">'
      + '  <div style="width:min(420px,92vw);background:#fff;border-radius:12px;padding:16px;box-shadow:0 20px 60px rgba(15,23,42,.25);">'
      + '    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;"><b>训练海螺形象</b><button type="button" id="kbHiflyTrainClose" style="border:0;background:transparent;font-size:18px;cursor:pointer;">×</button></div>'
      + '    <label class="kb-field">名称<input id="kbHiflyTrainName" maxlength="20" placeholder="例如：老板形象"></label>'
      + '    <label class="kb-field">类型<select id="kbHiflyTrainType"><option value="image">图片</option><option value="video">视频</option></select></label>'
      + '    <label class="kb-field">文件<input id="kbHiflyTrainFile" type="file" accept="image/*,video/*"></label>'
      + '    <label style="display:flex;gap:6px;font-size:11px;color:#64748b;align-items:center;margin:6px 0;"><input type="checkbox" id="kbHiflyTrainAgree"> 我确认拥有该肖像授权</label>'
      + '    <div style="display:flex;gap:8px;justify-content:flex-end;"><button type="button" class="kb-btn" id="kbHiflyTrainCancel">取消</button><button type="button" class="kb-btn-primary" id="kbHiflyTrainSubmit">提交训练</button></div>'
      + '    <div class="kb-hint" id="kbHiflyTrainMsg" style="margin-top:8px;"></div>'
      + '  </div>'
      + '</div>'
      + '</section></div>';
  }

  function optList(key, value) {
    var arr = (state.options && state.options[key]) || [];
    return arr.map(function (o) {
      var v = (o && o.id != null) ? o.id : o;
      var label = (o && o.name) ? o.name : v;
      return '<option value="' + esc(v) + '"' + (String(v) === String(value) ? ' selected' : '') + '>' + esc(label) + '</option>';
    }).join('');
  }

  function hiflyAvatarBlockHtml() {
    if (!state.form || state.form.generator !== 'hifly') return '';
    var rows = (state.hiflyAvatars && state.hiflyAvatars.items) || [];
    var selected = String(state.form.hifly_avatar_id || '');
    var opts = '<option value="">当前默认（个人IP里最新的形象）</option>' + rows.map(function(it) {
      var id = String(it.avatar || it.hifly_avatar_id || '').trim();
      var title = it.title || id || '海螺形象';
      return '<option value="' + esc(id) + '"' + (id === selected ? ' selected' : '') + '>' + esc(title) + '</option>';
    }).join('');
    return '<div class="kb-hifly-avatar">'
      + '<label style="display:block;font-size:11px;color:#64748b;margin-bottom:4px;">海螺形象</label>'
      + '<select id="kbHiflyAvatar" style="width:100%;">' + opts + '</select>'
      + '<div style="display:flex;gap:6px;margin-top:6px;">'
      + '<button type="button" class="kb-btn" id="kbHiflyRefresh">刷新形象</button>'
      + '<button type="button" class="kb-btn" id="kbHiflyCreate">训练新形象</button>'
      + '</div>'
      + '<div style="font-size:10.5px;color:#94a3b8;margin-top:4px;">海螺走已训练形象，口型由海螺保证</div>'
      + '</div>';
  }

  function loadHiflyAvatars() {
    var base = (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : API;
    if (!base) return Promise.resolve();
    return fetch(base + '/api/hifly/my/avatar/list?page=1&size=100', { headers: headers() })
      .then(function(r) { return r.json().catch(function() { return {}; }); })
      .then(function(d) {
        state.hiflyAvatars = { items: (d && d.items) || [] };
        renderAvatar();
      })
      .catch(function() {
        state.hiflyAvatars = { items: [] };
        renderAvatar();
      });
  }

  function openHiflyTrainModal() {
    var m = $('kbHiflyTrainModal');
    if (!m) return;
    m.style.display = 'flex';
    var msg = $('kbHiflyTrainMsg');
    if (msg) msg.textContent = '';
  }

  function closeHiflyTrainModal() {
    var m = $('kbHiflyTrainModal');
    if (m) m.style.display = 'none';
  }

  function hiflyCloudBase() {
    return (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : API;
  }

  function submitHiflyTrain() {
    var name = (($('kbHiflyTrainName') || {}).value || '').trim();
    var type = (($('kbHiflyTrainType') || {}).value || 'image');
    var fileInput = $('kbHiflyTrainFile');
    var file = fileInput && fileInput.files ? fileInput.files[0] : null;
    var agree = !!($('kbHiflyTrainAgree') || {}).checked;
    var msg = $('kbHiflyTrainMsg');
    function setMsg(t) { if (msg) msg.textContent = t; }
    if (!name) return setMsg('请填写名称');
    if (!file) return setMsg('请选择图片或视频');
    if (!agree) return setMsg('请先勾选肖像授权确认');
    var base = hiflyCloudBase();
    var fd = new FormData();
    fd.append('title', name);
    fd.append('aigc_flag', '0');
    if (typeof token !== 'undefined' && token) fd.append('token', token);
    fd.append('file', file, file.name || (type === 'video' ? 'avatar.mp4' : 'avatar.jpg'));
    setMsg('提交训练中…');
    var url = base + (type === 'video' ? '/api/hifly/my/avatar/create-by-video-upload' : '/api/hifly/my/avatar/create-by-image-upload');
    fetch(url, { method: 'POST', headers: authOnly(), body: fd })
      .then(function(r) {
        return r.json().catch(function() { return {}; }).then(function(d) {
          if (!r.ok || d.ok === false) throw new Error(d.detail || ('HTTP ' + r.status));
          return d;
        });
      })
      .then(function(d) {
        var taskId = (d && (d.task_id || (d.item && d.item.task_id))) || '';
        if (!taskId) { setMsg('已提交，但没拿到任务号，请稍后刷新形象列表'); loadHiflyAvatars(); return; }
        setMsg('训练中，完成后会自动刷新…');
        pollHiflyAvatarTask(taskId);
      })
      .catch(function(e) { setMsg('提交失败：' + (e && e.message ? e.message : e)); });
  }

  function pollHiflyAvatarTask(taskId) {
    var base = hiflyCloudBase();
    var tries = 0;
    var timer = setInterval(function() {
      tries += 1;
      fetch(base + '/api/hifly/my/avatar/task?task_id=' + encodeURIComponent(taskId), { headers: authOnly() })
        .then(function(r) { return r.json().catch(function() { return {}; }); })
        .then(function(d) {
          var st = String((d && (d.status || (d.item && d.item.status))) || '').toLowerCase();
          if (st === 'success' || st === 'succeed' || st === 'completed' || st === 'done') {
            clearInterval(timer);
            var msg = $('kbHiflyTrainMsg'); if (msg) msg.textContent = '训练完成';
            closeHiflyTrainModal();
            loadHiflyAvatars();
          } else if (st === 'failed' || st === 'error') {
            clearInterval(timer);
            var msg2 = $('kbHiflyTrainMsg'); if (msg2) msg2.textContent = '训练失败：' + String((d && (d.message || d.error)) || '');
          }
        })
        .catch(function() {});
      if (tries > 120) clearInterval(timer);
    }, 5000);
  }

  function renderAvatar() {
    var box = $('kbAvatar');
    if (!box) return;
    var gen = (state.form && state.form.generator) || 'hifly';
    if (gen === 'hifly') {
      box.innerHTML = hiflyAvatarBlockHtml();
      return;
    }
    if (!state.upload) {
      box.innerHTML = hiflyAvatarBlockHtml() + '<div class="kb-drop" id="kbDrop"><b>点击或拖入人物照片</b><small>jpg / png / webp，≤ 15MB，短边 ≥ 512</small><input type="file" id="kbFile" accept="image/jpeg,image/png,image/webp" style="display:none;"></div>';
      bindUpload();
      return;
    }
    var c = state.upload.checks || {};
    var hasChecks = !!(c && c.width);
    box.innerHTML = hiflyAvatarBlockHtml() + '<img class="kb-photo" src="' + esc(API + state.upload.url) + '" alt="形象照">'
      + (hasChecks
        ? '<ul class="kb-checks">'
          + '<li class="' + (Math.min(c.width || 0, c.height || 0) >= 512 ? 'ok' : 'warn') + '">分辨率 ' + (c.width || 0) + '×' + (c.height || 0) + '</li>'
          + '<li class="' + ((c.sharpness || 0) >= 25 ? 'ok' : 'warn') + '">清晰度 ' + (c.sharpness || 0) + '</li>'
          + '<li class="' + ((c.brightness || 0) >= 60 && (c.brightness || 0) <= 215 ? 'ok' : 'warn') + '">亮度 ' + (c.brightness || 0) + '</li>'
          + (c.warnings || []).map(function (w) { return '<li class="warn">⚠ ' + esc(w) + '</li>'; }).join('')
          + '</ul>'
        : '<ul class="kb-checks"><li>已随项目保存</li></ul>')
      + '<div style="margin-top:8px;"><button type="button" class="kb-btn" id="kbReplace">换一张</button></div>';
    var rep = $('kbReplace');
    if (rep) rep.onclick = function () { state.upload = null; markDirty(); renderAvatar(); };
  }

  function bindUpload() {
    var drop = $('kbDrop'), file = $('kbFile');
    if (!drop || !file) return;
    drop.addEventListener('click', function () { file.click(); });
    drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.style.borderColor = '#93c5fd'; });
    drop.addEventListener('dragleave', function () { drop.style.borderColor = ''; });
    drop.addEventListener('drop', function (e) {
      e.preventDefault(); drop.style.borderColor = '';
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
    });
    file.addEventListener('change', function () { if (file.files && file.files[0]) upload(file.files[0]); });
  }

  function renderParams() {
    var box = $('kbParams');
    if (!box) return;
    var f = state.form;
    var isCustom = f.scene === 'custom';
    var packs = (state.options && state.options.packaging) || [];
    var groups = {};
    packs.forEach(function (o) { (groups[o.group] = groups[o.group] || []).push(o); });
    box.innerHTML = ''
      + '<div class="kb-grid2">'
      + '  <label class="kb-field">主播<select id="kbHost">' + optList('hosts', f.host) + '</select></label>'
      + '  <label class="kb-field">景别<select id="kbShot">' + optList('shots', f.shot) + '</select></label>'
      + '  <label class="kb-field">比例<select id="kbRatio"><option value="">跟随主播</option>' + ((state.options && state.options.ratios) || ['9:16', '16:9']).map(function (r) { return '<option value="' + esc(r) + '"' + (f.ratio === r ? ' selected' : '') + '>' + esc(r) + '</option>'; }).join('') + '</select></label>'
      + '  <label class="kb-field">分辨率<select id="kbResolution"><option value="">跟随景别</option>' + ((state.options && state.options.resolutions) || ['720p', '480p']).map(function (r) { return '<option value="' + esc(r) + '"' + (f.resolution === r ? ' selected' : '') + '>' + esc(r) + '</option>'; }).join('') + '</select></label>'
      + '  <label class="kb-field">语言<select id="kbLanguage">' + optList('languages', f.language) + '</select></label>'
      + '  <label class="kb-field">模型<select id="kbModel">' + optList('models', f.model) + '</select></label>'
      + '  <label class="kb-field">场景<select id="kbScene">' + optList('scenes', f.scene) + '</select></label>'
      + '  <label class="kb-field">音色描述<input type="text" id="kbVoice" value="' + esc(f.voice || '') + '" placeholder="留空按照片自动匹配"></label>'
      + '</div>'
      + '<div style="font-size:10.5px;color:#94a3b8;margin-top:6px;line-height:1.5;">底片来源：海螺数字人走「个人IP设置」里已训练的形象+声音，口型由海螺保证；Grok 图生视频更快，但口型不保证。普通生成和套模板共用这里选的底片来源。</div>'
      + '<div style="font-size:10.5px;color:#94a3b8;line-height:1.5;">「白背心」是固定女性形象；上传自己的照片请选「照片本人（自动匹配）」，否则提示词会按女性生成。</div>'
      + '<label class="kb-field" id="kbSceneWrap" style="display:' + (isCustom ? 'flex' : 'none') + ';">场景描述<input type="text" id="kbScenePrompt" value="' + esc(f.scene_prompt || '') + '" placeholder="例如：夜晚霓虹街头，浅景深"></label>'
      + '<label style="display:flex;align-items:center;gap:6px;font-size:11px;color:#64748b;"><input type="checkbox" id="kbCompose"' + (f.compose_scene ? ' checked' : '') + '> 先合成场景图（只生成背景，人物用原图抠像；特写照片会像贴片，建议半身照）</label>'
      ;
    bindParams();
    updateParamSummary();
  }

  function readParams() {
    state.form = {
      generator: (document.querySelector('input[name="kbGen"]:checked') || {}).value || 'hifly',
      host: ($('kbHost') || {}).value || 'tank',
      shot: ($('kbShot') || {}).value || 'full',
      ratio: ($('kbRatio') || {}).value || '',
      resolution: ($('kbResolution') || {}).value || '',
      language: ($('kbLanguage') || {}).value || 'Mandarin Chinese',
      model: ($('kbModel') || {}).value || '',
      scene: ($('kbScene') || {}).value || 'none',
      scene_prompt: ($('kbScenePrompt') || {}).value || '',
      voice: ($('kbVoice') || {}).value || '',
      compose_scene: !!($('kbCompose') && $('kbCompose').checked),
      packaging: Array.prototype.slice.call(document.querySelectorAll('[data-pack]:checked')).map(function (el) { return el.getAttribute('data-pack'); })
    };
    updateParamSummary();
  }

  function bindParams() {
    var box = $('kbParams');
    if (!box) return;
    box.onchange = function (ev) {
      if (ev && ev.target && ev.target.id === 'kbHost') {
        var h = ((state.options && state.options.hosts) || []).filter(function (x) { return x.id === ($('kbHost') || {}).value; })[0];
        if (h && !(($('kbRatio') || {}).value)) { var sel = $('kbRatio'); if (sel) sel.value = h.ratio || ''; }
      }
      readParams();
      markDirty();
      var wrap = $('kbSceneWrap');
      if (wrap) wrap.style.display = state.form.scene === 'custom' ? 'flex' : 'none';
    };
    box.oninput = function () { readParams(); markDirty(); };
  }

  function updateParamSummary() {
    var el = $('kbParamSummary');
    if (!el) return;
    var f = state.form || {};
    var shot = ((state.options && state.options.shots) || []).filter(function (x) { return x.id === f.shot; })[0];
    el.textContent = (shot ? shot.name : '全屏') + (f.ratio ? ' · ' + f.ratio : '') + (f.packaging && f.packaging.length ? ' · 包装 ' + f.packaging.length : '');
  }

  function renderMeta() {
    var el = $('kbMeta');
    if (!el) return;
    var q = state.quote || (state.project && state.project.segments ? state.project : null);
    if (!q) { el.innerHTML = ''; return; }
    var f = state.form || {};
    var host = ((state.options && state.options.hosts) || []).filter(function (x) { return x.id === f.host; })[0];
    var scene = ((state.options && state.options.scenes) || []).filter(function (x) { return x.id === f.scene; })[0];
    var chips = [];
    chips.push('<span class="kb-chip">' + esc(host ? host.name : '白背心') + '</span>');
    chips.push('<span class="kb-chip">' + (q.total_seconds || 0) + ' 秒</span>');
    chips.push('<span class="kb-chip">' + (q.total_credits || 0) + ' credits</span>');
    if (f.language) chips.push('<span class="kb-chip">' + esc(f.language) + '</span>');
    if (scene && f.scene !== 'none') chips.push('<span class="kb-chip">场景 · ' + esc(scene.name) + (f.compose_scene ? '（合成）' : '') + '</span>');
    el.innerHTML = chips.join('');
  }

  function renderSegments() {
    var box = $('kbSegments');
    if (!box) return;
    var q = state.quote || (state.project && state.project.segments ? state.project : null);
    if (!q || !q.segments || !q.segments.length) {
      box.innerHTML = '<span style="font-size:12px;color:#94a3b8;">还没有分段，先点「只看分段报价」或直接生成。</span>';
      if ($('kbSegCount')) $('kbSegCount').textContent = '0 段';
      return;
    }
    var rows = q.segments.map(function (s) {
      return '<tr><td>' + esc(s.id) + '</td><td>' + esc(s.text) + '</td><td>' + s.seconds + 's</td><td>' + esc(s.resolution) + '</td><td>' + s.credits + '</td></tr>';
    }).join('');
    box.innerHTML = '<table><thead><tr><th>段</th><th>台词</th><th>时长</th><th>分辨率</th><th>credits</th></tr></thead><tbody>' + rows + '</tbody></table>'
      + '<div class="kb-total"><span>共 ' + q.segments.length + ' 段 · ' + (q.total_seconds || 0) + ' 秒</span><b>' + (q.total_credits || 0) + ' credits</b></div>';
    if ($('kbSegCount')) $('kbSegCount').textContent = q.segments.length + ' 段';
  }

  function renderVisualQa(q) {
    if (!q) return '';
    var warnings = q.warnings || [];
    var ok = !!q.ok;
    var score = q.score || 0;
    return '<div class="kb-visual-qa"><b>画面质检</b><span class="kb-qa-pill' + (ok ? '' : ' warn') + '">' + (ok ? '通过' : '需注意') + ' · ' + score + ' 分</span>'
      + (warnings.length ? '<small>' + warnings.slice(0, 3).map(function (w) { return esc(w); }).join('；') + (warnings.length > 3 ? '…' : '') + '</small>' : '<small>黑帧、音轨、帧率、安全区均正常</small>')
      + '</div>';
  }

  function renderQaResult(q) {
    if (!q.ok) return '<div style="margin-top:8px;font-size:11px;color:#dc2626;">' + esc(q.error || '质检失败') + '</div>';
    var rows = (q.segments || []).map(function (x) {
      return '<tr><td>' + esc(x.id) + '</td><td>' + esc(x.text) + '</td><td style="color:' + (x.warn ? '#dc2626' : '#059669') + ';">' + Math.round(x.acc * 100) + '%</td><td style="font-size:11px;color:#64748b;">' + esc(x.heard || '') + '</td></tr>';
    }).join('');
    return '<div style="margin-top:10px;"><div style="font-size:12px;color:#0f172a;margin-bottom:6px;">质检结果 · 平均 ' + Math.round((q.overall || 0) * 100) + '%</div>'
      + '<table><thead><tr><th>段</th><th>文稿</th><th>准确率</th><th>听到</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
  }

  function runQa() {
    if (!state.project) return;
    var btn = $('kbQa');
    if (btn) { btn.disabled = true; btn.textContent = '质检中…'; }
    log('开始质检（whisper 回听）…');
    req('/api/skills/digital-human-koubo/projects/' + state.project.id + '/qa', { method: 'POST', headers: headers() })
      .then(function (d) {
        state.qa = d;
        log(d.ok ? '质检完成，平均 ' + Math.round((d.overall || 0) * 100) + '%' : '质检失败：' + d.error);
        renderPack();
      })
      .catch(function (e) { log('质检失败：' + e.message); if (btn) { btn.disabled = false; btn.textContent = '开始质检'; } });
  }

  function loadTemplatePrefs() {
    try {
      var raw = localStorage.getItem('koubo_template_prefs_v1');
      var data = raw ? JSON.parse(raw) : {};
      state.render.recentTemplateIds = Array.isArray(data.recent) ? data.recent.slice(0, 12) : [];
      state.render.favoriteTemplateIds = Array.isArray(data.favorites) ? data.favorites.slice(0, 50) : [];
    } catch (e) {
      state.render.recentTemplateIds = [];
      state.render.favoriteTemplateIds = [];
    }
  }

  function saveTemplatePrefs() {
    try {
      localStorage.setItem('koubo_template_prefs_v1', JSON.stringify({
        recent: state.render.recentTemplateIds || [],
        favorites: state.render.favoriteTemplateIds || []
      }));
    } catch (e) {}
  }

  function touchTemplate(id) {
    if (!id) return;
    var list = (state.render.recentTemplateIds || []).filter(function (x) { return x !== id; });
    list.unshift(id);
    state.render.recentTemplateIds = list.slice(0, 12);
    saveTemplatePrefs();
  }

  function toggleFavoriteTemplate(id) {
    if (!id) return;
    var list = (state.render.favoriteTemplateIds || []).slice();
    var idx = list.indexOf(id);
    if (idx >= 0) list.splice(idx, 1);
    else list.unshift(id);
    state.render.favoriteTemplateIds = list.slice(0, 50);
    saveTemplatePrefs();
  }

  function renderBrollBlock() {
    var b = state.render.broll;
    var open = !!state.render.brollOpen;
    return '<div class="kb-broll"><div class="kb-broll-head"><b>插入素材</b><small>可选 · B-roll / 画中画</small><span style="flex:1"></span><button type="button" class="kb-btn" id="kbBrollToggle">' + (open ? '收起' : '展开') + '</button></div>'
      + '<div id="kbBrollBody" style="display:' + (open ? 'block' : 'none') + ';">'
      + '<div class="kb-broll-row"><button type="button" class="kb-btn" id="kbBrollPick">上传图片/视频</button><button type="button" class="kb-btn" id="kbBrollAuto">自动匹配</button><span class="kb-broll-name" id="kbBrollName">' + esc(b ? (b.name || b.asset_id || '已选素材') : '未选择素材') + '</span>' + (b ? '<button type="button" class="kb-btn" id="kbBrollRemove">移除</button>' : '') + '</div>'
      + '<div class="kb-broll-row"><label>方式<select id="kbBrollMode"><option value="pip"' + (b && b.mode === 'pip' ? ' selected' : '') + '>画中画</option><option value="full"' + (b && b.mode === 'full' ? ' selected' : '') + '>全屏插入</option></select></label><label>开始<input id="kbBrollStart" type="number" min="0" step="0.5" value="' + esc(b ? (b.start || 2) : 2) + '"> 秒</label><label>时长<input id="kbBrollDuration" type="number" min="0.8" step="0.5" value="' + esc(b ? (b.duration || 2.5) : 2.5) + '"> 秒</label></div>'
      + '<input type="file" id="kbBrollFile" accept="video/mp4,video/quicktime,video/webm,image/jpeg,image/png,image/webp" style="display:none;">'
      + '<div class="kb-broll-note">素材插在底片上，原口播声音不变；画中画放在右下角，全屏插入会暂时盖住人物。' + (b && b.reason ? ' 已自动匹配：' + esc(b.reason) : '') + '</div>'
      + '</div></div>';
  }

  function bindBrollHandlers() {
    var toggle = $('kbBrollToggle');
    if (toggle) toggle.onclick = function () { state.render.brollOpen = !state.render.brollOpen; renderPack(); };
    var auto = $('kbBrollAuto');
    if (auto) auto.onclick = autoBroll;
    var pick = $('kbBrollPick');
    var file = $('kbBrollFile');
    if (pick && file) pick.onclick = function () { file.click(); };
    if (file) file.onchange = function () { if (file.files && file.files[0]) uploadBroll(file.files[0]); };
    var mode = $('kbBrollMode');
    if (mode) mode.onchange = function () { if (state.render.broll) state.render.broll.mode = mode.value; };
    var start = $('kbBrollStart');
    if (start) start.oninput = function () { if (state.render.broll) state.render.broll.start = Number(start.value || 2); };
    var dur = $('kbBrollDuration');
    if (dur) dur.oninput = function () { if (state.render.broll) state.render.broll.duration = Number(dur.value || 2.5); };
    var remove = $('kbBrollRemove');
    if (remove) remove.onclick = function () { state.render.broll = null; state.render.brollOpen = true; renderPack(); };
  }

  function autoBroll() {
    if (!state.project) { log('先保存项目再自动匹配'); return; }
    log('按口播稿匹配素材…');
    req('/api/skills/digital-human-koubo/broll-suggest', {
      method: 'POST', headers: headers(),
      body: JSON.stringify({ project_id: state.project.id, limit: 1 })
    }).then(function (d) {
      var item = (d.items || [])[0];
      if (!item) { log('素材文件名里没有匹配到口播关键词；把素材命名成「产品名_特写.jpg」这类再试'); return; }
      state.render.broll = { asset_id: item.asset_id, url: item.url, name: item.name, kind: item.kind,
        mode: item.mode || 'pip', start: item.start || 2, duration: item.duration || 2.5, reason: item.reason || '' };
      state.render.brollOpen = true;
      renderPack();
      log('已匹配素材 ' + item.name + '（' + (item.reason || '') + '）');
    }).catch(function (e) { log('自动匹配失败：' + e.message); });
  }

  function uploadBroll(f) {
    var fd = new FormData();
    fd.append('file', f);
    log('上传插入素材 ' + f.name + '…');
    fetch(API + '/api/skills/digital-human-koubo/broll-uploads', { method: 'POST', headers: authOnly(), body: fd })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail)); return d; }); })
      .then(function (d) {
        state.render.broll = { asset_id: d.id, url: d.url, name: d.name || d.id, kind: d.kind || 'image', mode: 'pip', start: 2, duration: 2.5 };
        state.render.brollOpen = true;
        renderPack();
        log('插入素材已上传 ' + (d.name || d.id));
      })
      .catch(function (e) { log('上传插入素材失败：' + e.message); });
  }

  function renderBgmBlock() {
    var b = state.render.bgm;
    var open = !!state.render.bgmOpen;
    var vol = Math.round(Number(b && b.volume != null ? b.volume : 0.18) * 100);
    var fade = !b || b.fade !== 0;
    return '<div class="kb-broll"><div class="kb-broll-head"><b>背景音乐</b><small>可选 · 原声保留</small><span style="flex:1"></span><button type="button" class="kb-btn" id="kbBgmToggle">' + (open ? '收起' : '展开') + '</button></div>'
      + '<div id="kbBgmBody" style="display:' + (open ? 'block' : 'none') + ';">'
      + '<div class="kb-broll-row"><button type="button" class="kb-btn" id="kbBgmPick">上传音频</button><span class="kb-broll-name" id="kbBgmName">' + esc(b ? (b.name || b.asset_id || '已选音乐') : '未选择音乐') + '</span>' + (b ? '<button type="button" class="kb-btn" id="kbBgmRemove">移除</button>' : '') + '</div>'
      + '<div class="kb-broll-row"><label>音量<input id="kbBgmVolume" type="range" min="0" max="50" step="1" value="' + vol + '"> <span id="kbBgmVolText">' + vol + '%</span></label><label><input type="checkbox" id="kbBgmFade"' + (fade ? ' checked' : '') + '> 片尾淡出</label></div>'
      + '<input type="file" id="kbBgmFile" accept="audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/ogg,audio/flac" style="display:none;">'
      + '<div class="kb-broll-note">背景音乐只垫在人声下面，默认 18%，口播原声不变；片尾自动淡出。</div>'
      + '</div></div>';
  }

  function bindBgmHandlers() {
    var toggle = $('kbBgmToggle');
    if (toggle) toggle.onclick = function () { state.render.bgmOpen = !state.render.bgmOpen; renderPack(); };
    var pick = $('kbBgmPick');
    var file = $('kbBgmFile');
    if (pick && file) pick.onclick = function () { file.click(); };
    if (file) file.onchange = function () { if (file.files && file.files[0]) uploadBgm(file.files[0]); };
    var vol = $('kbBgmVolume');
    if (vol) vol.oninput = function () {
      var text = $('kbBgmVolText');
      if (text) text.textContent = vol.value + '%';
      if (state.render.bgm) state.render.bgm.volume = Number(vol.value || 0) / 100;
    };
    var fade = $('kbBgmFade');
    if (fade) fade.onchange = function () { if (state.render.bgm) state.render.bgm.fade = fade.checked ? 1 : 0; };
    var remove = $('kbBgmRemove');
    if (remove) remove.onclick = function () { state.render.bgm = null; state.render.bgmOpen = true; renderPack(); };
  }

  function uploadBgm(f) {
    var fd = new FormData();
    fd.append('file', f);
    log('上传背景音乐 ' + f.name + '…');
    fetch(API + '/api/skills/digital-human-koubo/bgm-uploads', { method: 'POST', headers: authOnly(), body: fd })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail)); return d; }); })
      .then(function (d) {
        state.render.bgm = { asset_id: d.id, url: d.url, name: d.name || d.id, volume: 0.18, fade: 1 };
        state.render.bgmOpen = true;
        renderPack();
        log('背景音乐已上传 ' + (d.name || d.id));
      })
      .catch(function (e) { log('上传背景音乐失败：' + e.message); });
  }

  function openTemplateSample(url, name) {
    if (!url) return;
    var old = document.getElementById('kbSampleModal');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var src = String(url).indexOf('http') === 0 ? url : API + url;
    var modal = document.createElement('div');
    modal.id = 'kbSampleModal';
    modal.innerHTML = '<div class="kb-sample-card"><div class="kb-sample-head"><b>' + esc(name || '模板样片') + '</b><button type="button" class="kb-sample-close">关闭</button></div><video controls autoplay playsinline src="' + esc(src) + '"></video></div>';
    document.body.appendChild(modal);
    function close() {
      var v = modal.querySelector('video'); if (v) { try { v.pause(); } catch (e) {} }
      if (modal.parentNode) modal.parentNode.removeChild(modal);
    }
    modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
    modal.querySelector('.kb-sample-close').onclick = close;
  }

  function renderMultiBlock() {
    var tpls = state.render.templates || [];
    var cur = state.render.templateId || (tpls[0] && tpls[0].id) || '';
    if (!(state.render.shots || []).length) {
      state.render.shots = [
        { template_id: cur, title: '' },
        { template_id: cur, title: '' },
        { template_id: cur, title: '' }
      ];
    }
    var labels = ['开场', '正文', '结尾'];
    var rows = state.render.shots.slice(0, 3).map(function (x, i) {
      var opts = tpls.map(function (t) {
        return '<option value="' + esc(t.id) + '"' + (x.template_id === t.id ? ' selected' : '') + '>' + esc(t.name || t.id) + '</option>';
      }).join('');
      return '<div class="kb-multi-row"><span>' + labels[i] + '</span><select data-shot-tpl="' + i + '">' + opts + '</select><input data-shot-title="' + i + '" maxlength="24" placeholder="标题（可留空）" value="' + esc(x.title || '') + '"></div>';
    }).join('');
    return '<div class="kb-multi"><div class="kb-multi-head"><b>三段式分镜</b><span style="font-size:10.5px;color:#94a3b8;">开场 / 正文 / 结尾分别套模板</span><span style="flex:1"></span><label><input type="checkbox" id="kbMultiOn"' + (state.render.shotsEnabled ? ' checked' : '') + '> 开启</label></div>'
      + '<div id="kbMultiBody" style="display:' + (state.render.shotsEnabled ? 'block' : 'none') + ';">' + rows
      + '<div class="kb-multi-note">按口播段落自动切底片，逐段套模板后拼接；分镜模式暂不叠加 B-roll，背景音乐仍可用。预览会慢一些（约 1–2 分钟）。</div>'
      + '</div></div>';
  }

  function bindMultiHandlers() {
    var on = $('kbMultiOn');
    if (on) on.onchange = function () {
      state.render.shotsEnabled = !!on.checked;
      if (state.render.shotsEnabled && !(state.render.shots || []).length) {
        var cur = state.render.templateId;
        state.render.shots = [{ template_id: cur, title: '' }, { template_id: cur, title: '' }, { template_id: cur, title: '' }];
      }
      renderPack();
    };
    $('kbPack').querySelectorAll('[data-shot-tpl]').forEach(function (sel) {
      sel.onchange = function () {
        var i = Number(sel.getAttribute('data-shot-tpl') || 0);
        if (state.render.shots[i]) state.render.shots[i].template_id = sel.value;
      };
    });
    $('kbPack').querySelectorAll('[data-shot-title]').forEach(function (inp) {
      inp.oninput = function () {
        var i = Number(inp.getAttribute('data-shot-title') || 0);
        if (state.render.shots[i]) state.render.shots[i].title = inp.value || '';
      };
    });
  }

  function openMatrixModal() {
    var old = document.getElementById('kbMatrixModal');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var modal = document.createElement('div');
    modal.id = 'kbMatrixModal';
    document.body.appendChild(modal);
    var photos = [];
    var created = [];
    var timer = null;
    var genJob = '';

    function close() {
      if (timer) { clearInterval(timer); timer = null; }
      if (modal.parentNode) modal.parentNode.removeChild(modal);
    }

    function scripts() {
      var ta = modal.querySelector('#kbMatrixScripts');
      return (ta ? ta.value : '').split(/\n+/).map(function (x) { return x.trim(); }).filter(Boolean);
    }

    function selectedPhotos() {
      var ids = [];
      modal.querySelectorAll('[data-photo]').forEach(function (box) { if (box.checked) ids.push(box.value); });
      return ids;
    }

    function updateSum() {
      var n = scripts().length;
      var p = Math.max(1, selectedPhotos().length);
      var total = n * p;
      var el = modal.querySelector('#kbMatrixSum');
      if (el) el.textContent = '将创建 ' + total + ' 个项目' + (total > 20 ? '（超过 20 个上限，请减少文案或形象）' : '');
    }

    function paintForm() {
      modal.innerHTML = '<div class="kb-matrix">'
        + '<div class="kb-matrix-head"><h3>矩阵批量</h3><button type="button" class="kb-batch-close" data-close>关闭</button></div>'
        + '<div class="kb-matrix-sec"><b>文案<small>每行一条，重复的会自动去重</small></b><textarea id="kbMatrixScripts" placeholder="第一条口播文案\n第二条口播文案\n第三条口播文案"></textarea></div>'
        + '<div class="kb-matrix-sec"><b>形象照<small>可多选；不选则用当前项目形象</small></b><div class="kb-matrix-photos" id="kbMatrixPhotos">加载中…</div></div>'
        + '<div class="kb-matrix-sum" id="kbMatrixSum">将创建 0 个项目</div>'
        + '<div class="kb-matrix-actions"><span style="flex:1"></span><button type="button" class="kb-btn-primary" id="kbMatrixCreate">创建项目</button></div>'
        + '</div>';
      modal.querySelector('[data-close]').onclick = close;
      modal.querySelector('#kbMatrixScripts').oninput = updateSum;
      modal.querySelector('#kbMatrixCreate').onclick = createProjects;
      updateSum();
      req('/api/skills/digital-human-koubo/uploads', { headers: headers() }).then(function (d) {
        photos = d.uploads || [];
        var box = modal.querySelector('#kbMatrixPhotos');
        if (!photos.length) { box.innerHTML = '<span style="font-size:11px;color:#94a3b8;">还没有上传过形象照</span>'; return; }
        box.innerHTML = photos.map(function (x) {
          return '<label class="kb-matrix-photo"><input type="checkbox" data-photo value="' + esc(x.id) + '"><img src="' + esc(API + x.url) + '" alt="">' + esc(x.name.slice(0, 14)) + '</label>';
        }).join('');
        box.querySelectorAll('[data-photo]').forEach(function (cb) {
          cb.onchange = function () { cb.parentNode.classList.toggle('on', cb.checked); updateSum(); };
        });
      }).catch(function () {
        var box = modal.querySelector('#kbMatrixPhotos');
        if (box) box.innerHTML = '<span style="font-size:11px;color:#94a3b8;">读取形象照失败，可稍后重试</span>';
      });
    }

    function createProjects() {
      var list = scripts();
      if (!list.length) { log('先写至少一条文案'); return; }
      var btn = modal.querySelector('#kbMatrixCreate');
      btn.disabled = true; btn.textContent = '创建中…';
      req('/api/skills/digital-human-koubo/projects/batch', {
        method: 'POST', headers: headers(),
        body: JSON.stringify({ scripts: list, photo_ids: selectedPhotos(), options: state.form || null })
      }).then(function (d) {
        created = d.projects || [];
        loadProjects();
        paintCreated(d);
        log('矩阵已创建 ' + created.length + ' 个项目');
      }).catch(function (e) {
        btn.disabled = false; btn.textContent = '创建项目';
        log('创建矩阵失败：' + e.message);
      });
    }

    function paintCreated(d) {
      var rows = (d.projects || []).map(function (x) {
        return '<div class="kb-matrix-item"><b>' + esc(x.name) + '</b><span style="color:#94a3b8;">' + (x.total_credits || 0) + ' credits</span></div>';
      }).join('');
      modal.innerHTML = '<div class="kb-matrix">'
        + '<div class="kb-matrix-head"><h3>矩阵已创建</h3><button type="button" class="kb-batch-close" data-close>关闭</button></div>'
        + '<div class="kb-matrix-sum">共 ' + created.length + ' 个项目，预计消耗 ' + (d.total_credits || 0) + ' credits（生成底片时扣除）</div>'
        + '<div class="kb-matrix-list">' + rows + '</div>'
        + '<div class="kb-matrix-actions"><span style="font-size:10.5px;color:#94a3b8;">生成后可用「批量出片」统一套模板</span><span style="flex:1"></span><button type="button" class="kb-btn-primary" id="kbMatrixGen">批量生成底片（' + (d.total_credits || 0) + ' credits）</button></div>'
        + '</div>';
      modal.querySelector('[data-close]').onclick = close;
      modal.querySelector('#kbMatrixGen').onclick = startGenerate;
    }

    function startGenerate() {
      if (!window.confirm('将按顺序生成 ' + created.length + ' 个项目，预计消耗 ' + (created.reduce(function (a, x) { return a + (x.total_credits || 0); }, 0)) + ' credits。继续？')) return;
      var ids = created.map(function (x) { return x.id; });
      req('/api/skills/digital-human-koubo/batch-generate', {
        method: 'POST', headers: headers(), body: JSON.stringify({ project_ids: ids, all: true })
      }).then(function (d) {
        genJob = d.job_id;
        paintGen(d);
        timer = setInterval(pollGen, 2000);
        pollGen();
      }).catch(function (e) { log('批量生成失败：' + e.message); });
    }

    function paintGen(d) {
      var items = d.items || [];
      var rows = items.map(function (x) {
        var pill = x.status === 'done' ? '完成' : (x.status === 'error' ? '失败' : (x.status === 'running' ? '生成中' : '等待'));
        return '<div class="kb-matrix-item"><b>' + esc(x.name) + '</b><span style="font-size:10.5px;color:' + (x.status === 'error' ? '#b91c1c' : '#64748b') + ';">' + pill + ' ' + esc(x.message || '') + '</span></div>';
      }).join('');
      modal.innerHTML = '<div class="kb-matrix">'
        + '<div class="kb-matrix-head"><h3>批量生成底片</h3><button type="button" class="kb-batch-close" data-close>关闭</button></div>'
        + '<div class="kb-matrix-sum">' + esc(d.message || '') + ' · ' + (d.done || 0) + '/' + (d.total || items.length) + '</div>'
        + '<div class="kb-matrix-bar"><i style="width:' + Math.max(2, d.progress || 0) + '%"></i></div>'
        + '<div class="kb-matrix-list">' + rows + '</div>'
        + '<div class="kb-matrix-actions"><span style="font-size:10.5px;color:#94a3b8;">' + (d.status === 'running' ? '后台生成中，可关闭窗口' : '完成后可用「批量出片」套模板') + '</span><span style="flex:1"></span>'
        + (d.status === 'running' ? '<button type="button" class="kb-btn" data-cancel>取消剩余</button>' : '')
        + '</div></div>';
      modal.querySelector('[data-close]').onclick = close;
      var c = modal.querySelector('[data-cancel]');
      if (c) c.onclick = function () {
        c.disabled = true; c.textContent = '正在取消…';
        req('/api/skills/digital-human-koubo/batch-generate/jobs/' + encodeURIComponent(genJob) + '/cancel', { method: 'POST', headers: headers() })
          .then(function () { pollGen(); }).catch(function () { c.disabled = false; c.textContent = '取消剩余'; });
      };
    }

    function pollGen() {
      if (!genJob) return;
      req('/api/skills/digital-human-koubo/batch-generate/jobs/' + encodeURIComponent(genJob), { headers: headers() })
        .then(function (d) {
          if (!document.getElementById('kbMatrixModal')) { if (timer) clearInterval(timer); return; }
          paintGen(d);
          if (d.status !== 'running') {
            if (timer) { clearInterval(timer); timer = null; }
            loadProjects(); renderPack();
            log('批量生成：' + (d.message || d.status));
          }
        }).catch(function () { });
    }

    modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
    paintForm();
  }

  function openCreateModal(templateId) {
    var local = (state.render.templates || []).filter(function (t) { return t.id === templateId; })[0] || { id: templateId, name: templateId };
    var old = document.getElementById('kbCreateModal');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var modal = document.createElement('div');
    modal.id = 'kbCreateModal';
    document.body.appendChild(modal);
    var tpl = local;
    loadHiflyAvatars().catch(function () {});
    var photoId = '';
    var photoUrl = '';
    var generator = 'hifly';
    var hiflyAvatarId = '';
    var project = null;
    var timer = null;
    var busy = false;
    var projectTitle = '';
    var lastStage = '';
    var quote = null;
    var quoteTimer = null;

    function close() { if (timer) clearInterval(timer); if (modal.parentNode) modal.parentNode.removeChild(modal); }
    function sampleSrc(t) {
      var u = t.sample_video_url || '';
      return u ? (String(u).indexOf('http') === 0 ? u : API + u) : '';
    }
    function estimate() {
      var text = (modal.querySelector('#kbCreateScript') || {}).value || '';
      var chars = text.replace(/\s/g, '').length;
      var el = modal.querySelector('#kbCreateMeta');
      if (!el) return;
      if (!chars) { el.textContent = '按字数自动分段，生成时扣算力'; quote = null; return; }
      var guessSecs = Math.max(6, Math.ceil(chars / 5.4));
      el.textContent = '预计 ' + guessSecs + ' 秒 · 计算真实分段中…';
      clearTimeout(quoteTimer);
      quoteTimer = setTimeout(function () {
        req('/api/skills/digital-human-koubo/quote', {
          method: 'POST', headers: headers(),
          body: JSON.stringify({ text: text, shot: 'full', resolution: '720p', generator: (state.form && state.form.generator) || 'hifly' })
        }).then(function (d) {
          quote = d;
          el.textContent = '预计 ' + d.total_seconds + ' 秒 · ' + d.segments.length + ' 段 · 约 ' + d.total_credits + ' credits';
        }).catch(function () {
          el.textContent = '预计 ' + guessSecs + ' 秒 · 以创建后实际分段为准';
        });
      }, 450);
    }

    function paintForm() {
      var media = sampleSrc(tpl)
        ? '<video autoplay muted loop playsinline src="' + esc(sampleSrc(tpl)) + '"></video>'
        : '<div style="height:220px;border-radius:12px;background:linear-gradient(135deg,#334155,#0f172a)"></div>';
      modal.innerHTML = '<div class="kb-create">'
        + '<div class="kb-create-media">' + media + '<div class="kb-create-tpl">将使用模板<br><b>' + esc(tpl.name || tpl.id) + '</b></div></div>'
        + '<div class="kb-create-body">'
        + '<div class="kb-create-head"><h3>用「' + esc(tpl.name || tpl.id) + '」创作</h3><button type="button" class="kb-batch-close" data-close>关闭</button></div>'
        + '<div class="kb-create-sub">模板决定标题、字幕、关键词、角标和 CTA 的样式；底片由你上传的真人照片生成，不需要再选别的模板。</div>'
        + '<div class="kb-create-sec"><b>1. 底片来源</b>'
        + '<div class="kb-gen-row" id="kbCreateGenRow">'
        + '  <label><input type="radio" name="kbCreateGen" value="hifly" checked><span>海螺数字人</span></label>'
        + '  <label><input type="radio" name="kbCreateGen" value="grok"><span>Grok AI</span></label>'
        + '</div>'
        + '<div id="kbCreateHifly" style="margin-top:8px;">'
        + '  <select id="kbCreateAvatar" style="width:100%;height:34px;border:1px solid #e2e8f0;border-radius:8px;padding:0 8px;font-size:12px;"></select>'
        + '  <div style="display:flex;gap:8px;margin-top:8px;"><button type="button" class="kb-btn" id="kbCreateAvatarRefresh">刷新形象</button><button type="button" class="kb-btn" id="kbCreateAvatarTrain">训练新形象</button></div>'
        + '  <div class="kb-create-sub" style="margin-top:6px;">海螺走已训练形象，口型由海螺保证；留空用当前默认形象。</div>'
        + '</div>'
        + '<div id="kbCreatePhotoWrap" style="display:none;margin-top:8px;"><div class="kb-create-photo" id="kbCreatePhoto"><img id="kbCreatePhotoImg" alt=""><span id="kbCreatePhotoText">点击上传正脸照片（jpg/png/webp，建议正脸、光线均匀）</span></div><input type="file" id="kbCreateFile" accept="image/jpeg,image/png,image/webp" style="display:none;"></div>'
        + '</div>'
        + '<div class="kb-create-sec"><b>2. 口播文案</b><textarea id="kbCreateScript" placeholder="粘贴你的口播稿，按字数自动分段"></textarea></div>'
        + '<div class="kb-create-sec"><b>3. 标题（可选）</b><input type="text" id="kbCreateTitle" maxlength="24" placeholder="留空自动从口播稿提取"></div>'
        + '<div class="kb-create-meta" id="kbCreateMeta">按字数自动分段，生成时扣算力</div>'
        + '<div class="kb-create-actions"><span style="font-size:10.5px;color:#94a3b8;">创建任务后会先生成底片，再自动套用「' + esc(tpl.name || tpl.id) + '」出 480p 预览</span><span style="flex:1"></span><button type="button" class="kb-btn-primary" id="kbCreateStart">创建任务</button></div>'
        + '<div id="kbCreateSteps"></div><div class="kb-create-bar" id="kbCreateBar" style="display:none;"><i></i></div>'
        + '</div></div>';
      modal.querySelector('[data-close]').onclick = close;
      modal.querySelector('#kbCreatePhoto').onclick = function () { modal.querySelector('#kbCreateFile').click(); };
      function paintAvatarOptions() {
        var sel = modal.querySelector('#kbCreateAvatar');
        if (!sel) return;
        var rows = (state.hiflyAvatars && state.hiflyAvatars.items) || [];
        sel.innerHTML = '<option value="">当前默认（个人IP里最新的形象）</option>' + rows.map(function (it) {
          var id = String(it.avatar || it.hifly_avatar_id || '').trim();
          var title = it.title || id || '海螺形象';
          return '<option value="' + esc(id) + '"' + (id === hiflyAvatarId ? ' selected' : '') + '>' + esc(title) + '</option>';
        }).join('');
      }
      paintAvatarOptions();
      Array.prototype.forEach.call(modal.querySelectorAll('input[name="kbCreateGen"]'), function (radio) {
        radio.onchange = function () {
          generator = radio.value || 'hifly';
          var hiflyBox = modal.querySelector('#kbCreateHifly');
          var photoBox = modal.querySelector('#kbCreatePhotoWrap');
          if (hiflyBox) hiflyBox.style.display = generator === 'hifly' ? '' : 'none';
          if (photoBox) photoBox.style.display = generator === 'hifly' ? 'none' : '';
        };
      });
      var avSel = modal.querySelector('#kbCreateAvatar');
      if (avSel) avSel.onchange = function () { hiflyAvatarId = avSel.value || ''; };
      var avRefresh = modal.querySelector('#kbCreateAvatarRefresh');
      if (avRefresh) avRefresh.onclick = function () { loadHiflyAvatars().then(function () { paintAvatarOptions(); }); };
      var avTrain = modal.querySelector('#kbCreateAvatarTrain');
      if (avTrain) avTrain.onclick = function () { openHiflyTrainModal(); };
      modal.querySelector('#kbCreateFile').onchange = function (e) { if (e.target.files && e.target.files[0]) uploadPhoto(e.target.files[0]); };
      modal.querySelector('#kbCreateScript').oninput = estimate;
      modal.querySelector('#kbCreateStart').onclick = startCreate;
      estimate();
    }

    function uploadPhoto(file) {
      var fd = new FormData(); fd.append('file', file);
      var txt = modal.querySelector('#kbCreatePhotoText');
      if (txt) txt.textContent = '上传中…';
      var xhr = new XMLHttpRequest();
      xhr.open('POST', API + '/api/skills/digital-human-koubo/uploads');
      var h = {};
      try { h = authOnly(); } catch (e) { h = {}; }
      Object.keys(h).forEach(function (k) { try { xhr.setRequestHeader(k, h[k]); } catch (e) {} });
      xhr.onload = function () {
        try {
          var d = JSON.parse(xhr.responseText || '{}');
          if (xhr.status >= 400) throw new Error(typeof d.detail === 'string' ? d.detail : ('HTTP ' + xhr.status));
          photoId = d.id; photoUrl = d.url;
          var img = modal.querySelector('#kbCreatePhotoImg');
          if (img) { img.src = API + d.url; img.style.display = 'block'; }
          if (txt) txt.textContent = '已上传 ' + (file.name || d.id) + '，点击可更换';
        } catch (e) { if (txt) txt.textContent = '上传失败：' + e.message; }
      };
      xhr.onerror = function () { if (txt) txt.textContent = '上传失败：网络错误'; };
      xhr.send(fd);
    }

    function paintSteps(active, pct, message) {
      var names = ['创建项目', '生成底片', '套用模板', '完成'];
      var html = names.map(function (n, i) {
        var cls = i < active ? 'done' : (i === active ? 'run' : '');
        return '<div class="kb-create-step ' + cls + '"><span class="dot">' + (i < active ? '✓' : (i + 1)) + '</span>' + n + (i === active && message ? ' · ' + esc(message) : '') + '</div>';
      }).join('');
      var box = modal.querySelector('#kbCreateSteps');
      if (box) box.innerHTML = html;
      var bar = modal.querySelector('#kbCreateBar');
      if (bar) { bar.style.display = 'block'; bar.querySelector('i').style.width = Math.max(2, pct || 0) + '%'; }
    }

    function startCreate() {
      if (busy) return;
      var script = (modal.querySelector('#kbCreateScript') || {}).value || '';
      if (generator !== 'hifly' && !photoId) { toast('先上传一张形象照'); return; }
      if (!script.trim()) { toast('先粘贴口播稿'); return; }
      busy = true;
      var btn = modal.querySelector('#kbCreateStart');
      if (btn) { btn.disabled = true; btn.textContent = '处理中…'; }
      projectTitle = (modal.querySelector('#kbCreateTitle') || {}).value || '';
      if (project && project.id) {
        if (lastStage === 'render') { paintSteps(2, 70, '重新套用模板'); startPreview(); return; }
        paintSteps(1, 8, '重新提交生成');
        lastStage = 'generate';
        req('/api/skills/digital-human-koubo/projects/' + project.id + '/generate', {
          method: 'POST', headers: headers(), body: JSON.stringify({ all: true })
        }).then(function (g) { pollGenerate(g.job_id); })
          .catch(function (e) { fail('生成提交失败：' + e.message); });
        return;
      }
      paintSteps(0, 3, '创建项目');
      req('/api/skills/digital-human-koubo/projects', {
        method: 'POST', headers: headers(),
        body: JSON.stringify({ name: '', script: script, photo_id: photoId, host: 'photo', shot: 'full',
          options: { host: 'photo', shot: 'full', language: 'Mandarin Chinese', resolution: '720p', generator: generator, hifly_avatar_id: hiflyAvatarId } })
      }).then(function (p) {
        project = p;
        state.project = p;
        state.render.templateId = tpl.id;
        state.render.packaged = false;
        state.render.previewReady = false;
        loadProjects();
        var meta = modal.querySelector('#kbCreateMeta');
        if (meta) meta.textContent = '实际 ' + p.total_seconds + ' 秒 · ' + (p.segments || []).length + ' 段 · ' + p.total_credits + ' credits';
        paintSteps(1, 8, '提交生成');
        lastStage = 'generate';
        req('/api/skills/digital-human-koubo/projects/' + p.id + '/generate', {
          method: 'POST', headers: headers(), body: JSON.stringify({ all: true })
        }).then(function (g) { pollGenerate(g.job_id); })
          .catch(function (e) { fail('生成提交失败：' + e.message); });
      }).catch(function (e) { fail('创建项目失败：' + e.message); });
    }

    function pollGenerate(jobId) {
      timer = setInterval(function () {
        req('/api/skills/digital-human-koubo/generate/jobs/' + encodeURIComponent(jobId)).then(function (job) {
          paintSteps(1, 10 + (job.progress || 0) * 0.55, job.message || '生成中');
          if (job.status === 'running') return;
          if (timer) { clearInterval(timer); timer = null; }
          if (job.status === 'done') startPreview();
          else fail('底片生成失败：' + (job.error || job.message || '未知错误'));
        }).catch(function () { });
      }, 2000);
    }

    function startPreview() {
      lastStage = 'render';
      paintSteps(2, 70, '套用 ' + (tpl.name || tpl.id));
      req('/api/skills/digital-human-koubo/projects/' + project.id + '/render', {
        method: 'POST', headers: headers(),
        body: JSON.stringify({ template_id: tpl.id, title: projectTitle || '', quality: 'preview' })
      }).then(function (r) { pollRender(r.job_id); })
        .catch(function (e) { fail('套用模板失败：' + e.message); });
    }

    function pollRender(jobId) {
      timer = setInterval(function () {
        req('/api/skills/digital-human-koubo/render/jobs/' + encodeURIComponent(jobId)).then(function (job) {
          paintSteps(2, 70 + (job.progress || 0) * 0.3, job.message || '套用模板');
          if (job.status === 'running') return;
          if (timer) { clearInterval(timer); timer = null; }
          if (job.status === 'done') finish();
          else fail('套用模板失败：' + (job.error || job.message || '未知错误'));
        }).catch(function () { });
      }, 1500);
    }

    function finish() {
      busy = false;
      paintSteps(4, 100, '完成');
      loadProjects();
      req('/api/skills/digital-human-koubo/projects/' + project.id).then(function (p) {
        state.project = p; state.render.templateId = tpl.id;
        renderPreview(); renderProjects();
        var warn = document.getElementById('kbCreateWarn');
        if (warn) {
          var bad = (p.generated_segments || []).filter(function (x) { return x.trimmed; });
          if (bad.length) warn.textContent = '检测到上游时长异常：' + bad.map(function (x) { return (x.requested_seconds || '?') + 's→' + (x.actual_seconds || '?') + 's 已裁剪'; }).join('；');
        }
      }).catch(function () { });
      var src = API + '/api/skills/digital-human-koubo/projects/' + project.id + '/preview?v=' + Date.now();
      var body = modal.querySelector('.kb-create-body');
      body.innerHTML = '<div class="kb-create-head"><h3>完成：已套用「' + esc(tpl.name || tpl.id) + '」</h3><button type="button" class="kb-batch-close" data-close>关闭</button></div>'
        + '<div class="kb-create-sub">底片已按你的照片和口播生成，并自动套用了这个模板。下面是 480p 预览；满意后去工作台生成高清。</div>'
        + '<div class="kb-create-sub" id="kbCreateWarn" style="color:#b45309;"></div>'
        + '<div class="kb-create-result"><video controls autoplay playsinline src="' + esc(src) + '"></video></div>'
        + '<div class="kb-create-actions"><a class="kb-btn" href="' + esc(API + '/api/skills/digital-human-koubo/projects/' + project.id + '/preview') + '" download>下载预览</a><span style="flex:1"></span><button type="button" class="kb-btn" data-switch>换个模板</button><button type="button" class="kb-btn-primary" data-go>去工作台</button></div>';
      body.querySelector('[data-close]').onclick = close;
      body.querySelector('[data-switch]').onclick = function () {
        close();
        renderPack();
        var grid = document.getElementById('kbTplGrid');
        if (grid) { try { grid.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {} }
        toast('选另一个模板后点「预览效果」，同一段底片不会重新扣算力');
      };
      body.querySelector('[data-go]').onclick = function () {
        close();
        openProject(project.id);
        toast('已进入工作台，当前模板：' + (tpl.name || tpl.id));
      };
    }

    function fail(msg) {
      busy = false;
      if (timer) { clearInterval(timer); timer = null; }
      paintSteps(1, 5, '失败');
      var body = modal.querySelector('.kb-create-body');
      if (body) {
        var note = document.createElement('div');
        note.className = 'kb-create-sub';
        note.style.color = '#b91c1c';
        note.textContent = msg;
        body.appendChild(note);
      }
      var btn = modal.querySelector('#kbCreateStart');
      if (btn) { btn.disabled = false; btn.textContent = lastStage === 'render' ? '重试套用模板' : '重试生成'; }
      toast(msg);
    }

    modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
    paintForm();
  }

  function fitBlock(t) {
    var script = String((state.project && state.project.script) || '').trim();
    var rules = t.rules || {};
    var maxChars = Number(rules.captionMaxChars || 22);
    var capFraction = Number(t.caption_size || 0.05);
    var capacity = Math.max(6, Math.min(maxChars, (720 * 0.94 - 36) / (1280 * capFraction)));
    var titleMax = Number(rules.titleChars || 14);
    var sample = String(t.sample_script || '');
    var sampleLine = '样片文案' + (sample ? '「' + sample + '」' : '') + ' · ' + (t.sample_seconds || 4.2) + ' 秒';
    if (!script) {
      return '<div class="kb-detail-sec"><b>适配你的口播稿</b><div class="kb-detail-note">' + esc(sampleLine) + '。生成底片后，这里会按当前模板的字幕上限给出适配结果。</div></div>';
    }
    var segs = (state.project && state.project.segments) || [];
    var total = script.replace(/\s/g, '').length;
    var over = 0, screens = 0, longest = 0;
    segs.forEach(function (s) {
      var n = String(s.text || '').replace(/\s/g, '').length;
      longest = Math.max(longest, n);
      if (n > capacity) over++;
      screens += Math.max(1, Math.ceil(n / capacity));
    });
    var title = String(state.render.title || '').trim() || script.replace(/\s/g, '').slice(0, titleMax);
    var titleOver = title.length > titleMax;
    var pills = [];
    pills.push('<span class="kb-fit-pill ' + (over ? 'warn' : 'ok') + '">' + (over ? ('有 ' + over + ' 段偏长，自动拆屏') : '字幕适配') + '</span>');
    pills.push('<span class="kb-fit-pill">共 ' + screens + ' 屏</span>');
    pills.push('<span class="kb-fit-pill ' + (titleOver ? 'warn' : 'ok') + '">标题 ' + title.length + '/' + titleMax + ' 字' + (titleOver ? '，会截断' : '') + '</span>');
    return '<div class="kb-detail-sec"><b>适配你的口播稿</b><div class="kb-fit">' + pills.join('') + '</div>'
      + '<div class="kb-detail-note" style="margin-top:6px;">' + esc(sampleLine) + '；你的稿 ' + total + ' 字 / ' + segs.length + ' 段，单段最长 ' + longest + ' 字，模板单屏约 ' + Math.floor(capacity) + ' 字（竖屏成片估算）。</div></div>';
  }

  function openTemplateDetail(id) {
    if (!id) return;
    var old = document.getElementById('kbTplModal');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var local = (state.render.templates || []).filter(function (t) { return t.id === id; })[0] || null;
    var modal = document.createElement('div');
    modal.id = 'kbTplModal';
    document.body.appendChild(modal);
    var current = local || { id: id, name: id };
    var alive = true;

    function videoHtml(t) {
      var sample = t.sample_video_url || '';
      if (!sample) return '<div class="kb-detail-nosample">样片制作中<br><small>可直接套用，用你的口播稿出片</small></div>';
      var src = String(sample).indexOf('http') === 0 ? sample : API + sample;
      var poster = t.cover ? (String(t.cover).indexOf('http') === 0 ? t.cover : API + t.cover) : '';
      return '<video controls playsinline preload="metadata"' + (poster ? ' poster="' + esc(poster) + '"' : '') + ' src="' + esc(src) + '"></video>';
    }

    function paint(t) {
      if (!alive) return;
      current = t || current;
      var colors = current.colors || ['#FFD84D', '#FF4D8D', '#4DE1FF'];
      var cover = current.cover ? (String(current.cover).indexOf('http') === 0 ? current.cover : API + current.cover) : '';
      var media = cover
        ? '<img src="' + esc(cover) + '" alt="">'
        : '<span class="kb-tpl-preview" style="height:180px;margin:0;background:linear-gradient(135deg,' + esc(colors[0]) + ',' + esc(colors[1]) + ',' + esc(colors[2]) + ')"></span>';
      var scenes = current.scenes || [];
      var tags = current.tags || [];
      var chips = scenes.concat(tags.filter(function (x) { return scenes.indexOf(x) < 0; })).map(function (x) {
        return '<span class="kb-detail-chip">' + esc(x) + '</span>';
      }).join('');
      var kv = [
        ['画幅', current.ratio_label || current.ratio || '跟随底片'],
        ['排版', current.layout_label || current.variant || '标准排版'],
        ['镜头', current.motion_label || '固定机位'],
        ['转场', current.transition_label || '硬切'],
        ['字幕', '安全区内自动断句'],
        ['节奏', '跟随口播停顿']
      ].map(function (row) { return '<span>' + esc(row[0]) + '：' + esc(row[1]) + '</span>'; }).join('');
      var copy = current.copy || {};
      var notes = [];
      if (copy.badge) notes.push('角标「' + copy.badge + '」');
      if (copy.lowerTitle) notes.push('落版「' + copy.lowerTitle + (copy.lowerSub ? ' / ' + copy.lowerSub : '') + '」');
      if (copy.cta) notes.push('结尾「' + copy.cta + '」');
      var fav = (state.render.favoriteTemplateIds || []).indexOf(current.id) >= 0;
      var hasBase = !!(state.project && state.project.status === 'generated');
      modal.innerHTML = '<div class="kb-detail">'
        + '<div class="kb-detail-media">' + media + videoHtml(current) + '</div>'
        + '<div class="kb-detail-body">'
        + '<div class="kb-detail-head"><div><h3>' + esc(current.name || id) + '</h3><div class="kb-detail-sub">' + esc(current.category || '') + (current.layout_label ? ' · ' + esc(current.layout_label) : '') + '</div></div><button type="button" class="kb-sample-close" data-detail-close>关闭</button></div>'
        + '<div class="kb-detail-desc">' + esc(current.description || '') + '</div>'
        + fitBlock(current)
        + (chips ? '<div class="kb-detail-sec"><b>适用场景</b><div class="kb-detail-chips">' + chips + '</div></div>' : '')
        + '<div class="kb-detail-sec"><b>画面设置</b><div class="kb-detail-kv">' + kv + '</div></div>'
        + (notes.length ? '<div class="kb-detail-sec"><b>这版会怎么排</b><div class="kb-detail-note">' + esc(notes.join(' · ')) + '</div></div>' : '')
        + '<div class="kb-detail-note" style="margin-top:10px;">点下面按钮，上传形象照和口播稿，创建任务后自动用这个模板出 480p 预览。</div>'
        + '<div class="kb-detail-actions"><button type="button" class="kb-btn" data-detail-fav>' + (fav ? '★ 已收藏' : '☆ 收藏') + '</button><span style="flex:1"></span><button type="button" class="kb-btn-primary" data-detail-apply>用这个模板创作</button></div>'
        + '</div></div>';
      modal.querySelector('[data-detail-close]').onclick = close;
      modal.querySelector('[data-detail-apply]').onclick = function () {
        close();
        openCreateModal(current.id);
      };
      modal.querySelector('[data-detail-fav]').onclick = function () {
        toggleFavoriteTemplate(current.id);
        renderPack();
        paint(current);
      };
    }

    function close() {
      alive = false;
      var v = modal.querySelector('video');
      if (v) { try { v.pause(); } catch (e) {} }
      if (modal.parentNode) modal.parentNode.removeChild(modal);
    }

    modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
    paint(current);
    req('/api/skills/digital-human-koubo/templates/' + encodeURIComponent(id), { headers: headers() })
      .then(function (d) { if (d && d.id) paint(d); })
      .catch(function () {});
  }

  function openBatchModal() {
    var ready = (state.projects || []).filter(function (p) {
      return p.has_video && p.status !== 'generating';
    });
    if (!ready.length) { log('还没有可批量出片的底片，先生成一个项目'); return; }
    var old = document.getElementById('kbBatchModal');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var tpl = (state.render.templates || []).filter(function (t) { return t.id === state.render.templateId; })[0] || {};
    var tplName = tpl.name || state.render.templateId || '默认模板';
    var modal = document.createElement('div');
    modal.id = 'kbBatchModal';
    document.body.appendChild(modal);
    var timer = null;
    var jobId = '';

    function close() {
      if (timer) { clearInterval(timer); timer = null; }
      if (modal.parentNode) modal.parentNode.removeChild(modal);
    }

    function pill(x) {
      if (x.status === 'done') return '<span class="kb-batch-pill done">完成</span>';
      if (x.status === 'error') return '<span class="kb-batch-pill err">失败</span>';
      if (x.status === 'skipped') return '<span class="kb-batch-pill">已跳过</span>';
      if (x.status === 'running') return '<span class="kb-batch-pill run">' + (x.progress || 0) + '%</span>';
      return '<span class="kb-batch-pill">等待</span>';
    }

    function paintForm() {
      var rows = ready.map(function (p, i) {
        return '<label class="kb-batch-row"><input type="checkbox" data-pid="' + esc(p.id) + '"' + (i < 20 ? ' checked' : '') + '>'
          + '<span>' + esc(p.name || p.id) + '</span><small>' + esc(p.created_at || '') + (p.rendered ? ' · 已出过高清' : '') + '</small></label>';
      }).join('');
      modal.innerHTML = '<div class="kb-batch">'
        + '<div class="kb-batch-head"><h3>批量出片</h3><button type="button" class="kb-batch-close" data-close>关闭</button></div>'
        + '<div class="kb-batch-sub">当前模板 <b>' + esc(tplName) + '</b> · 选多个已生成底片，后台按顺序套同一个模板；一次最多 20 个。</div>'
        + '<div class="kb-batch-list">' + rows + '</div>'
        + '<div class="kb-batch-actions"><button type="button" class="kb-btn" data-all>全选 / 全不选</button>'
        + '<label><input type="radio" name="kbBatchQ" value="preview" checked> 快速预览</label>'
        + '<label><input type="radio" name="kbBatchQ" value="final"> 高清成片</label>'
        + '<span style="flex:1"></span><button type="button" class="kb-btn-primary" data-start>开始批量出片</button></div>'
        + '</div>';
      modal.querySelector('[data-close]').onclick = close;
      modal.querySelector('[data-all]').onclick = function () {
        var boxes = modal.querySelectorAll('[data-pid]');
        var allOn = true;
        boxes.forEach(function (b) { if (!b.checked) allOn = false; });
        boxes.forEach(function (b) { b.checked = !allOn; });
      };
      modal.querySelector('[data-start]').onclick = function () {
        var ids = [];
        modal.querySelectorAll('[data-pid]').forEach(function (b) { if (b.checked && ids.length < 20) ids.push(b.getAttribute('data-pid')); });
        if (!ids.length) { log('先勾选要出片的项目'); return; }
        var q = modal.querySelector('input[name="kbBatchQ"]:checked');
        var quality = q ? q.value : 'preview';
        modal.querySelector('[data-start]').disabled = true;
        req('/api/skills/digital-human-koubo/batch-render', {
          method: 'POST', headers: headers(),
          body: JSON.stringify({ project_ids: ids, template_id: state.render.templateId, quality: quality })
        }).then(function (d) {
          jobId = d.job_id;
          paintJob(d);
          timer = setInterval(poll, 1500);
          poll();
        }).catch(function (e) {
          modal.querySelector('[data-start]').disabled = false;
          log('批量出片失败：' + e.message);
        });
      };
    }

    function paintJob(d) {
      var items = d.items || [];
      var failed = items.filter(function (x) { return x.status === 'error'; }).length;
      var retryable = items.filter(function (x) { return x.status === 'error' || x.status === 'skipped'; }).length;
      var rows = items.map(function (x) {
        var extra = x.status === 'done'
          ? (x.score != null ? '<span style="font-size:10.5px;color:#15803d;">质检 ' + x.score + ' 分</span>' : '')
            + (x.output ? '<a href="' + esc(API + x.output) + '" target="_blank" download>下载</a>' : '')
          : (x.status === 'error' ? '<span style="font-size:10.5px;color:#b91c1c;" title="' + esc(x.error || '') + '">' + esc((x.error || '失败').slice(0, 24)) + '</span>' : '<span style="font-size:10.5px;color:#94a3b8;">' + esc(x.message || '') + '</span>');
        return '<div class="kb-batch-item">' + pill(x) + '<b>' + esc(x.name || x.project_id) + '</b>' + extra + '</div>';
      }).join('');
      var skipped = (d.skipped || []).map(function (s) {
        return '<div class="kb-batch-item"><span class="kb-batch-pill">跳过</span><b>' + esc(s.name || s.project_id) + '</b><span style="font-size:10.5px;color:#94a3b8;">' + esc(s.reason || '') + '</span></div>';
      }).join('');
      modal.innerHTML = '<div class="kb-batch">'
        + '<div class="kb-batch-head"><h3>批量出片</h3><button type="button" class="kb-batch-close" data-close>关闭</button></div>'
        + '<div class="kb-batch-sub">' + esc(d.message || '') + ' · ' + (d.done || 0) + '/' + (d.total || items.length) + '</div>'
        + '<div class="kb-batch-bar"><i style="width:' + Math.max(2, d.progress || 0) + '%"></i></div>'
        + '<div class="kb-batch-list">' + (rows + skipped || '<span style="font-size:12px;color:#94a3b8;">没有任务</span>') + '</div>'
        + '<div class="kb-batch-actions"><span style="font-size:11px;color:#94a3b8;">' + (d.status === 'running' ? '后台渲染中，可以关闭窗口，任务会继续。' : (d.status === 'cancelled' ? '已取消，可重试未完成的项。' : '已完成，可逐条下载或到项目记录查看。')) + '</span><span style="flex:1"></span>'
        + (d.status === 'running' ? '<button type="button" class="kb-btn" data-cancel>取消剩余</button>' : '')
        + (retryable ? '<button type="button" class="kb-btn-primary" data-retry>重试失败/未完成（' + retryable + '）</button>' : '')
        + '</div></div>';
      modal.querySelector('[data-close]').onclick = close;
      var cancelBtn = modal.querySelector('[data-cancel]');
      if (cancelBtn) cancelBtn.onclick = function () {
        cancelBtn.disabled = true; cancelBtn.textContent = '正在取消…';
        req('/api/skills/digital-human-koubo/batch-render/jobs/' + encodeURIComponent(jobId) + '/cancel', { method: 'POST', headers: headers() })
          .then(function () { log('已请求取消，当前这条完成后停止'); poll(); })
          .catch(function (e) { cancelBtn.disabled = false; cancelBtn.textContent = '取消剩余'; log('取消失败：' + e.message); });
      };
      var retryBtn = modal.querySelector('[data-retry]');
      if (retryBtn) retryBtn.onclick = function () {
        retryBtn.disabled = true; retryBtn.textContent = '正在重试…';
        req('/api/skills/digital-human-koubo/batch-render/jobs/' + encodeURIComponent(jobId) + '/retry', { method: 'POST', headers: headers() })
          .then(function (r) { jobId = r.job_id; paintJob(r); if (timer) clearInterval(timer); timer = setInterval(poll, 1500); poll(); })
          .catch(function (e) { retryBtn.disabled = false; retryBtn.textContent = '重试失败项'; log('重试失败：' + e.message); });
      };
    }

    function poll() {
      if (!jobId) return;
      req('/api/skills/digital-human-koubo/batch-render/jobs/' + encodeURIComponent(jobId), { headers: headers() })
        .then(function (d) {
          if (!document.getElementById('kbBatchModal')) { if (timer) clearInterval(timer); return; }
          paintJob(d);
          if (d.status !== 'running') {
            if (timer) { clearInterval(timer); timer = null; }
            loadProjects(); renderPack();
            log('批量出片：' + (d.message || d.status));
          }
        })
        .catch(function () {});
    }

    modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
    paintForm();
  }

  function selectTemplate(id, silent) {
    if (!id) return;
    state.render.templateId = id;
    touchTemplate(id);
    state.render.packaged = false;
    state.render.previewReady = false;
    state.render.showPreview = false;
    state.render.qa = null;
    state.render.job = null;
    if (state.project) { state.project.rendered = false; state.project.preview_rendered = false; }
    renderPack(); renderPreview();
    var found = (state.render.templates || []).filter(function (t) { return t.id === id; })[0];
    var name = (found && found.name) || id;
    log('已选模板：' + name);
    if (!silent) toast('已套用「' + name + '」' + (state.project && state.project.status === 'generated' ? '，点「预览效果」看 480p 成片' : '，生成底片后自动使用'));
  }

  function loadRecommendations() {
    var text = (state.project && state.project.script) || '';
    text = String(text).trim();
    if (!text) { state.render.recommendations = []; state.render.recommendFor = ''; return; }
    var key = text.slice(0, 1200);
    if (state.render.recommendFor === key) return;
    state.render.recommendFor = key;
    req('/api/skills/digital-human-koubo/recommend-templates', {
      method: 'POST', headers: headers(), body: JSON.stringify({ text: text, limit: 3 })
    }).then(function (d) {
      state.render.recommendations = (d && d.items) || [];
      renderPack();
    }).catch(function () { state.render.recommendations = []; });
  }

  function templateCardHtml(t) {
    var r = state.render;
    var on = t.id === r.templateId;
    var colors = t.colors || ['#FFD84D', '#FF4D8D', '#4DE1FF'];
    var cover = t.cover ? (String(t.cover).indexOf('http') === 0 ? t.cover : API + t.cover) : '';
    var sample = t.sample_video_url || '';
    var visual = cover
      ? '<img class="kb-tpl-cover" src="' + esc(cover) + '" alt="">'
      : '<span class="kb-tpl-preview" style="background:linear-gradient(135deg,' + esc(colors[0] || '#FFD84D') + ',' + esc(colors[1] || '#FF4D8D') + ',' + esc(colors[2] || '#4DE1FF') + ')"></span>';
    var play = sample ? '<button type="button" class="kb-tpl-play" data-sample="' + esc(sample) + '" data-name="' + esc(t.name || '') + '">▶ 看样片</button>' : '';
    var fav = (state.render.favoriteTemplateIds || []).indexOf(t.id) >= 0;
    var favBtn = '<button type="button" class="kb-tpl-fav' + (fav ? ' on' : '') + '" data-fav="' + esc(t.id) + '" title="收藏">' + (fav ? '★' : '☆') + '</button>';
    return '<div class="kb-tpl' + (on ? ' on' : '') + '" data-tpl="' + esc(t.id) + '">'
      + '<div class="kb-tpl-coverwrap">' + visual + favBtn + play + '</div>'
      + '<b>' + esc(t.name || t.id) + '</b>'
      + '<p>' + esc(t.description || '') + '</p>'
      + '<div class="kb-tpl-foot"><small>' + esc(t.category || '') + (t.tags && t.tags.length ? ' · ' + esc((t.tags || []).slice(0, 2).join(' · ')) : '') + '</small><button type="button" class="kb-tpl-use" data-use="' + esc(t.id) + '">用这个模板</button><button type="button" class="kb-tpl-detail" data-detail="' + esc(t.id) + '">详情</button></div>'
      + '</div>';
  }

  function paintTemplateGrid() {
    var grid = $('kbTplGrid');
    if (!grid) return;
    var r = state.render;
    var all = r.templates || [];
    var byId = {};
    all.forEach(function (t) { byId[t.id] = t; });
    var cat = r.templateCategory || 'all';
    var q = (r.templateQuery || '').trim().toLowerCase();
    var list;
    if (cat === 'recent') {
      list = (r.recentTemplateIds || []).map(function (id) { return byId[id]; }).filter(Boolean);
    } else if (cat === 'favorites') {
      list = (r.favoriteTemplateIds || []).map(function (id) { return byId[id]; }).filter(Boolean);
    } else {
      list = all.slice();
      if (cat !== 'all') {
        list = list.filter(function (t) { return t.category === cat; });
      }
    }
    if (q) {
      list = list.filter(function (t) {
        return [t.name, t.description, t.category, (t.tags || []).join(' ')].join(' ').toLowerCase().indexOf(q) >= 0;
      });
    }
    var browsing = cat !== 'all' || q;
    if (!browsing && !r.showAllTemplates) {
      var pageSize = 6;
      var page = Math.max(0, r.featuredPage || 0);
      var start = (page * pageSize) % Math.max(1, list.length);
      var paged = list.slice(start, start + pageSize);
      if (paged.length < pageSize) paged = paged.concat(list.slice(0, pageSize - paged.length));
      list = paged;
    }
    var emptyText = cat === 'recent' ? '最近还没有用过模板' : (cat === 'favorites' ? '还没有收藏模板' : '没有匹配的模板');
    grid.innerHTML = list.map(templateCardHtml).join('') || '<span style="font-size:12px;color:#94a3b8;padding:10px 0;">' + emptyText + '</span>';
    grid.querySelectorAll('[data-tpl]').forEach(function (card) {
      card.onclick = function () { selectTemplate(card.getAttribute('data-tpl')); };
    });
    grid.querySelectorAll('[data-sample]').forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        openTemplateSample(btn.getAttribute('data-sample'), btn.getAttribute('data-name'));
      };
    });
    grid.querySelectorAll('[data-fav]').forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        toggleFavoriteTemplate(btn.getAttribute('data-fav'));
        renderPack();
      };
    });
    grid.querySelectorAll('[data-detail]').forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        openTemplateDetail(btn.getAttribute('data-detail'));
      };
    });
    grid.querySelectorAll('[data-use]').forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var tid = btn.getAttribute('data-use');
        // 已有底片时，点模板直接套用到当前项目，而不是再开一个创作弹窗
        if (state.project && state.project.status === 'generated') {
          if (typeof selectTemplate === 'function') selectTemplate(tid);
          else state.render.templateId = tid;
          state.render.previewReady = false;
          startRender('preview');
          log('已用当前底片套用模板：' + tid);
          return;
        }
        openCreateModal(tid);
      };
    });
  }

  function renderGallery() {
    var r = state.render;
    var tpls = r.templates || [];
    var cats = [];
    tpls.forEach(function (t) { if (t.category && cats.indexOf(t.category) < 0) cats.push(t.category); });
    var chips = '<button type="button" class="kb-tpl-chip' + ((r.templateCategory || 'all') === 'all' ? ' on' : '') + '" data-cat="all">全部</button>'
      + '<button type="button" class="kb-tpl-chip' + (r.templateCategory === 'recent' ? ' on' : '') + '" data-cat="recent">最近</button>'
      + '<button type="button" class="kb-tpl-chip' + (r.templateCategory === 'favorites' ? ' on' : '') + '" data-cat="favorites">收藏</button>'
      + cats.map(function (c) { return '<button type="button" class="kb-tpl-chip' + (r.templateCategory === c ? ' on' : '') + '" data-cat="' + esc(c) + '">' + esc(c) + '</button>'; }).join('');
    var curTpl = tpls.filter(function (x) { return x.id === r.templateId; })[0];
    return '<div class="kb-card"><div class="kb-sec-title">模板库<small>' + (curTpl ? '当前：' + esc(curTpl.name || curTpl.id) + ' · ' : '') + tpls.length + ' 个模板 · 先看风格，生成后一键套用</small></div>'
      + '<div class="kb-hint" style="margin:0 0 10px;">先在左侧上传形象照、写口播稿并生成底片。模板可以先看样片；底片出来后同一段素材可反复换模板，不重复扣算力。</div>'
      + '<div class="kb-tpl-tools"><input id="kbTplSearch" class="kb-tpl-search" placeholder="搜模板、场景、标签" value="' + esc(r.templateQuery || '') + '">' + chips + '</div>'
      + '<div class="kb-templates" id="kbTplGrid"></div>'
      + '<div class="kb-tpl-bottom-actions">'
      + ((r.templateCategory || 'all') === 'all' && !(r.templateQuery || '').trim() ? '<button type="button" class="kb-tpl-more" id="kbTplRefresh">换一批</button>' : '')
      + '<button type="button" class="kb-tpl-more" id="kbTplMore">' + (r.showAllTemplates ? '收起，只看推荐' : '全部模板（' + tpls.length + '）') + '</button>'
      + '</div></div>';
  }

  function bindTemplateTools() {
    var box = $('kbPack');
    if (!box) return;
    box.querySelectorAll('[data-cat]').forEach(function (chip) {
      chip.onclick = function () {
        state.render.templateCategory = chip.getAttribute('data-cat') || 'all';
        state.render.featuredPage = 0;
        renderPack();
      };
    });
    var search = $('kbTplSearch');
    if (search) search.oninput = function () { state.render.templateQuery = search.value || ''; state.render.featuredPage = 0; paintTemplateGrid(); };
    var more = $('kbTplMore');
    if (more) more.onclick = function () { state.render.showAllTemplates = !state.render.showAllTemplates; if (!state.render.showAllTemplates) { state.render.templateCategory = 'all'; state.render.templateQuery = ''; state.render.featuredPage = 0; } renderPack(); };
    var refresh = $('kbTplRefresh');
    if (refresh) refresh.onclick = function () { state.render.featuredPage = (state.render.featuredPage || 0) + 1; renderPack(); };
    var batchOpen = $('kbBatchOpen');
    if (batchOpen) batchOpen.onclick = openBatchModal;
    var matrixOpen = $('kbMatrixOpen');
    if (matrixOpen) matrixOpen.onclick = openMatrixModal;
  }

  function renderPack() {
    var box = $('kbPack');
    if (!box) return;
    if (!state.project || state.project.status !== 'generated') {
      box.innerHTML = (state.render.templates || []).length ? renderGallery() : '';
      if (!box.innerHTML) return;
      paintTemplateGrid();
      bindTemplateTools();
    bindMultiHandlers();
      return;
    }
    var r = state.render;
    var job = r.job && r.job.status === 'running' ? r.job : null;
    var tpls = r.templates || [];
    var hasPreview = !!r.previewReady || !!(state.project && state.project.preview_rendered);
    var hasFinal = !!r.packaged || !!(state.project && state.project.rendered);
    var primaryMode = hasPreview ? 'final' : 'preview';
    var primaryLabel = job ? ('生成中 ' + (job.progress || 0) + '%') : (!hasPreview ? '预览效果' : (hasFinal ? '重新生成高清' : '生成高清成片'));
    var cats = [];
    tpls.forEach(function (t) { if (t.category && cats.indexOf(t.category) < 0) cats.push(t.category); });
    var chips = '<button type="button" class="kb-tpl-chip' + ((r.templateCategory || 'all') === 'all' ? ' on' : '') + '" data-cat="all">全部</button>'
      + '<button type="button" class="kb-tpl-chip' + (r.templateCategory === 'recent' ? ' on' : '') + '" data-cat="recent">最近</button>'
      + '<button type="button" class="kb-tpl-chip' + (r.templateCategory === 'favorites' ? ' on' : '') + '" data-cat="favorites">收藏</button>'
      + cats.map(function (c) { return '<button type="button" class="kb-tpl-chip' + (r.templateCategory === c ? ' on' : '') + '" data-cat="' + esc(c) + '">' + esc(c) + '</button>'; }).join('');
    var curTpl = tpls.filter(function (x) { return x.id === r.templateId; })[0];
    box.innerHTML = '<div class="kb-card"><div class="kb-sec-title">选个模板，直接出片<small>' + (curTpl ? '当前：' + esc(curTpl.name || curTpl.id) + ' · ' : '') + (tpls.length || 0) + ' 个模板</small></div>'
      + '<div class="kb-title-input"><label>标题</label><input id="kbTemplateTitle" maxlength="24" placeholder="留空自动从口播稿提取" value="' + esc(r.title || '') + '"><small>可选</small></div>'
      + renderMultiBlock()
      + ((r.recommendations && r.recommendations.length) ? '<div class="kb-recommend"><b>推荐</b>' + r.recommendations.map(function (x) { return '<button type="button" class="kb-rec-chip" data-rec="' + esc(x.template_id) + '" title="' + esc(x.reason || '') + '">' + esc(x.name) + '</button>'; }).join('') + '</div>' : '')
      + '<div class="kb-packbar" style="margin-top:2px;">'
      + (hasFinal ? '<a class="kb-btn" href="' + esc(API + '/api/skills/digital-human-koubo/projects/' + state.project.id + '/rendered') + '" download>下载成片</a>' : '')
      + '<span style="flex:1"></span>'
      + (hasPreview && !job ? '<button type="button" class="kb-btn" id="kbPreviewAgain">重新预览</button>' : '')
      + '<button type="button" class="kb-btn-primary" id="kbRender" data-mode="' + primaryMode + '" style="width:auto;min-width:150px;"' + (job ? ' disabled' : '') + '>' + primaryLabel + '</button>'
      + '</div>'
      + '<div class="kb-hint" style="margin:6px 0 10px;">先看 480p 快速预览，满意再出高清；同一段底片换模板不会重新生成，也不会再扣底片算力。</div>'
      + (job ? '<div style="margin-bottom:10px;font-size:11px;color:#64748b;">' + esc(job.message || '') + '<div style="height:4px;background:#eef2f7;border-radius:999px;overflow:hidden;margin-top:4px;"><i style="display:block;height:100%;width:' + Math.max(3, job.progress || 0) + '%;background:linear-gradient(90deg,#3b82f6,#8b5cf6);"></i></div></div>' : '')
      + renderVisualQa(r.qa)
      + (r.showAllTemplates ? '<div class="kb-tpl-tools"><input id="kbTplSearch" class="kb-tpl-search" placeholder="搜模板、场景、标签" value="' + esc(r.templateQuery || '') + '">' + chips + '</div>' : '')
      + '<div class="kb-templates" id="kbTplGrid"></div>'
      + (tpls.length > 6 ? '<div class="kb-tpl-bottom-actions">'
          + (!r.showAllTemplates && (r.templateCategory || 'all') === 'all' && !(r.templateQuery || '').trim() ? '<button type="button" class="kb-tpl-more" id="kbTplRefresh">换一批</button>' : '')
          + '<button type="button" class="kb-tpl-more" id="kbTplMore">' + (r.showAllTemplates ? '收起，只看推荐' : '全部模板（' + tpls.length + '）') + '</button>'
          + ((state.projects || []).filter(function (x) { return x.has_video; }).length ? '<button type="button" class="kb-tpl-more" id="kbBatchOpen">批量出片（' + (state.projects || []).filter(function (x) { return x.has_video; }).length + ' 个底片）</button>' : '')
          + '<button type="button" class="kb-tpl-more" id="kbMatrixOpen">矩阵批量（多文案 × 多形象）</button>'
          + '</div>' : '')
      + renderBrollBlock()
      + renderBgmBlock()
      + '<div class="kb-packbar" style="margin-top:10px;border-top:1px solid #f1f5f9;padding-top:10px;"><button type="button" class="kb-btn" id="kbQa">口播质检</button><span style="font-size:11px;color:#94a3b8;">回听比对文稿，低于 85% 建议重拍底片</span></div>'
      + (state.qa ? renderQaResult(state.qa) : '')
      + '</div>';
    paintTemplateGrid();
    box.querySelectorAll('[data-cat]').forEach(function (chip) {
      chip.onclick = function () {
        state.render.templateCategory = chip.getAttribute('data-cat') || 'all';
        state.render.featuredPage = 0;
        renderPack();
      };
    });
    var search = $('kbTplSearch');
    if (search) search.oninput = function () { state.render.templateQuery = search.value || ''; state.render.featuredPage = 0; paintTemplateGrid(); };
    var more = $('kbTplMore');
    if (more) more.onclick = function () { state.render.showAllTemplates = !state.render.showAllTemplates; if (!state.render.showAllTemplates) { state.render.templateCategory = 'all'; state.render.templateQuery = ''; state.render.featuredPage = 0; } renderPack(); };
    var refresh = $('kbTplRefresh');
    if (refresh) refresh.onclick = function () { state.render.featuredPage = (state.render.featuredPage || 0) + 1; renderPack(); };
    var batchOpen = $('kbBatchOpen');
    if (batchOpen) batchOpen.onclick = openBatchModal;
    var matrixOpen = $('kbMatrixOpen');
    if (matrixOpen) matrixOpen.onclick = openMatrixModal;
    bindMultiHandlers();
    var titleInput = $('kbTemplateTitle');
    if (titleInput) titleInput.oninput = function () { state.render.title = titleInput.value || ''; };
    var btn = $('kbRender');
    if (btn) btn.onclick = function () { startRender(btn.getAttribute('data-mode') || 'preview'); };
    var again = $('kbPreviewAgain');
    if (again) again.onclick = function () { startRender('preview'); };
    box.querySelectorAll('[data-rec]').forEach(function (chip) {
      chip.onclick = function () { selectTemplate(chip.getAttribute('data-rec')); };
    });
    var qaBtn = $('kbQa');
    if (qaBtn) qaBtn.onclick = runQa;
    bindBrollHandlers();
    bindBgmHandlers();
    loadRecommendations();
  }

  function startRender(mode) {
    if (!state.project) return;
    state.render.runningMode = mode || 'preview';
    state.render.job = { status: 'running', progress: 0, message: state.render.runningMode === 'preview' ? '准备预览' : '准备生成高清' };
    renderPack();
    req('/api/skills/digital-human-koubo/projects/' + state.project.id + '/render', {
      method: 'POST', headers: headers(),
      body: JSON.stringify({ template_id: state.render.templateId, title: state.render.title || '', quality: state.render.runningMode, broll: state.render.broll ? { asset_id: state.render.broll.asset_id, mode: state.render.broll.mode || 'pip', start: Number(state.render.broll.start || 2), duration: Number(state.render.broll.duration || 2.5) } : null, bgm: state.render.bgm ? { asset_id: state.render.bgm.asset_id, volume: Number(state.render.bgm.volume != null ? state.render.bgm.volume : 0.18), fade: Number(state.render.bgm.fade != null ? state.render.bgm.fade : 1) } : null, shots: state.render.shotsEnabled ? state.render.shots.slice(0, 3).map(function (x) { return { template_id: x.template_id || '', title: x.title || '' }; }) : null })
    }).then(function (d) {
      log((state.render.runningMode === 'preview' ? '预览任务已提交 ' : '高清任务已提交 ') + d.job_id);
      pollRender(d.job_id);
    }).catch(function (e) {
      state.render.job = null;
      renderPack();
      log('包装失败：' + e.message);
    });
  }

  function pollRender(jobId) {
    var t = setInterval(function () {
      req('/api/skills/digital-human-koubo/render/jobs/' + encodeURIComponent(jobId)).then(function (job) {
        state.render.job = job;
        renderPack();
        if (job.status !== 'running') {
          clearInterval(t);
          if (job.status === 'done') {
            var mode = state.render.runningMode || 'final';
            if (mode === 'preview') { state.render.previewReady = true; state.render.showPreview = true; }
            else { state.render.packaged = true; state.render.showPreview = false; }
            state.render.job = null;
            state.render.qa = job.qa || null;
            log(mode === 'preview' ? '预览完成' : '高清成片完成');
            req('/api/skills/digital-human-koubo/projects/' + state.project.id).then(function (p) { state.project = p; renderAll(); });
          } else {
            log((state.render.runningMode === 'preview' ? '预览失败：' : '高清生成失败：') + (job.error || job.message || ''));
          }
        }
      }).catch(function () {});
    }, 1500);
  }

  function renderPreview() {
    var box = $('kbPreview');
    if (!box) return;
    var proj = state.preview || state.project;
    var isPreview = !!state.preview;
    if (state.job && !isPreview) {
      var pct = state.job.total ? Math.round((state.job.done || 0) * 100 / state.job.total) : (state.job.progress || 0);
      if (state.job.status === 'running') {
        box.innerHTML = '<div class="kb-empty"><b>' + esc(state.job.message || '生成中') + '</b>任务在服务器排队，完成后自动出现在这里</div>'
          + '<div class="kb-progress">' + esc(state.job.message || '') + ' · ' + pct + '%<div class="bar"><i style="width:' + Math.max(3, pct) + '%"></i></div></div>';
        return;
      }
      if (state.job.status === 'error') {
        box.innerHTML = '<div class="kb-empty"><b>生成失败</b>' + esc(state.job.error || state.job.message || '未知错误') + '</div>';
        return;
      }
    }
    if (proj && proj.status === 'generated' && proj.has_video !== false) {
      var usePreview = !isPreview && state.render.showPreview && !!proj.preview_rendered;
      var packaged = isPreview ? !!proj.rendered : (!usePreview && (state.render.packaged || proj.rendered));
      var ver = usePreview ? (proj.preview_v || 0) : (packaged ? (proj.rendered_v || 0) : (proj.video_v || 0));
      var kind = usePreview ? '/preview' : (packaged ? '/rendered' : '/video');
      var src = API + '/api/skills/digital-human-koubo/projects/' + proj.id + kind + '?v=' + (ver || Date.now());
      var badge = usePreview ? '预览版 · 480p' : (packaged ? '高清成片' : '底片');
      box.innerHTML = '<video controls playsinline preload="metadata" src="' + esc(src) + '"></video>'
        + '<div style="position:absolute;left:14px;top:12px;background:rgba(15,23,42,.72);color:#e2e8f0;font-size:11px;padding:4px 9px;border-radius:999px;">'
        + (isPreview ? '历史预览：' : '当前项目：') + esc(proj.name) + '</div>'
        + (isPreview ? '<button type="button" class="kb-btn" id="kbPreviewBack" style="position:absolute;right:14px;top:10px;">返回当前</button>' : '')
        + '<div style="position:absolute;left:14px;bottom:12px;background:rgba(15,23,42,.72);color:#e2e8f0;font-size:11px;padding:4px 9px;border-radius:999px;">' + esc(badge) + '</div>'
        + ((packaged || usePreview) ? '<a href="' + esc(API + '/api/skills/digital-human-koubo/projects/' + proj.id + (usePreview ? '/preview' : '/rendered')) + '" target="_blank" style="position:absolute;right:14px;bottom:12px;color:#cbd5e1;font-size:11px;text-decoration:underline;">下载' + (usePreview ? '预览' : '成片') + '</a>' : '')
        + (packaged ? '<a href="' + esc(API + '/api/skills/digital-human-koubo/projects/' + proj.id + '/video') + '" target="_blank" style="position:absolute;right:90px;bottom:12px;color:#cbd5e1;font-size:11px;text-decoration:underline;">看原片</a>' : '');
      var vid = box.querySelector('video');
      if (vid) { try { vid.load(); } catch (e) {} }
      var back = $('kbPreviewBack');
      if (back) back.onclick = function () { state.preview = null; renderPreview(); renderProjects(); };
      return;
    }
    if (isPreview) {
      box.innerHTML = '<div class="kb-empty"><b>' + esc(proj.name) + '</b>这个项目还没有成片</div>'
        + '<button type="button" class="kb-btn" id="kbPreviewBack" style="position:absolute;right:14px;top:10px;">返回当前</button>';
      var back2 = $('kbPreviewBack');
      if (back2) back2.onclick = function () { state.preview = null; renderPreview(); renderProjects(); };
      return;
    }
    box.innerHTML = '<div class="kb-empty"><b>还没有成片</b>左侧填好形象与口播稿，点击「生成第 1 段」</div>';
  }

  function renderStatus() {
    var el = $('kbStatus');
    if (!el) return;
    if (state.job && state.job.status === 'running') { el.textContent = '生成中'; el.className = 'kb-pill warn'; return; }
    if (state.dirty) { el.textContent = '有未保存修改'; el.className = 'kb-pill warn'; return; }
    if (state.project && state.project.status === 'generated') { el.textContent = '已出片'; el.className = 'kb-pill ok'; return; }
    if (state.project && state.project.id) { el.textContent = '已保存'; el.className = 'kb-pill'; return; }
    el.textContent = '未保存'; el.className = 'kb-pill';
  }

  function renderProjects() {
    var box = $('kbProjects');
    if (!box) return;
    if ($('kbProjCount')) $('kbProjCount').textContent = state.projects.length ? state.projects.length + ' 个' : '';
    if (!state.projects.length) {
      box.innerHTML = '<span style="font-size:12px;color:#94a3b8;">还没有项目。生成或保存后会出现在这里。</span>';
      return;
    }
    var activeId = (state.preview && state.preview.id) || (state.project && state.project.id);
    box.innerHTML = state.projects.map(function (p) {
      var st = p.status || 'ready';
      var badge = st === 'generated' ? (p.rendered ? '已包装' : '已出片') : (st === 'generating' ? '生成中' : '未生成');
      var cls = st === 'generated' ? 'ok' : (st === 'generating' ? 'warn' : '');
      var active = p.id === activeId;
      return '<div class="kb-proj" style="' + (active ? 'background:#f0f7ff;border-radius:8px;padding-left:8px;padding-right:8px;' : '') + '">'
        + '<div><b>' + esc(p.name) + '</b> <span class="kb-pill ' + cls + '" style="margin-left:6px;">' + badge + '</span><br>'
        + '<small>' + esc(String(p.created_at || '').replace('T', ' ')) + ' · ' + (p.segments_count || 0) + ' 段 · ' + (p.total_credits || 0) + ' credits' + (active ? ' · 当前' : '') + '</small></div>'
        + '<div style="display:flex;gap:6px;">'
        + (p.has_video ? '<button type="button" class="kb-btn" data-preview="' + esc(p.id) + '">预览</button>' : '')
        + '<button type="button" class="kb-btn" data-open="' + esc(p.id) + '">打开</button>'
        + '<button type="button" class="kb-btn" data-del="' + esc(p.id) + '">删除</button>'
        + '</div></div>';
    }).join('');
    box.querySelectorAll('[data-preview]').forEach(function (btn) {
      btn.onclick = function () { previewProject(btn.getAttribute('data-preview')); };
    });
    box.querySelectorAll('[data-open]').forEach(function (btn) {
      btn.onclick = function () { openProject(btn.getAttribute('data-open')); };
    });
    box.querySelectorAll('[data-del]').forEach(function (btn) {
      btn.onclick = function () {
        var pid = btn.getAttribute('data-del');
        if (!window.confirm('删除这个项目？')) return;
        fetch(API + '/api/skills/digital-human-koubo/projects/' + encodeURIComponent(pid), { method: 'DELETE', headers: authOnly() })
          .then(function () {
            log('已删除 ' + pid);
            if (state.preview && state.preview.id === pid) state.preview = null;
            if (state.project && state.project.id === pid) state.project = null;
            state.render.packaged = false;
            loadProjects(); renderPreview(); renderStatus();
          });
      };
    });
  }

  function previewProject(pid) {
    req('/api/skills/digital-human-koubo/projects/' + encodeURIComponent(pid)).then(function (p) {
      state.preview = p;
      renderPreview();
      renderProjects();
      log('预览历史项目 ' + p.name);
    }).catch(function (e) { log('预览失败：' + e.message); });
  }

  function renderAll() {
    renderAvatar(); renderParams(); renderSegments(); renderPreview(); renderStatus(); renderMeta(); renderPack(); renderProjects();
  }

  function updateCount() {
    var ta = $('kbText'), el = $('kbCount');
    if (!ta || !el) return;
    var n = (ta.value || '').replace(/\s/g, '').length;
    el.textContent = n + ' 字 · 约 ' + Math.max(1, Math.ceil(n / 5.4)) + ' 秒';
  }

  function markDirty() {
    state.dirty = true;
    renderStatus();
  }

  function upload(f) {
    var fd = new FormData();
    fd.append('file', f);
    log('上传形象照 ' + f.name);
    fetch(API + '/api/skills/digital-human-koubo/uploads', { method: 'POST', headers: authOnly(), body: fd })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail)); return d; }); })
      .then(function (d) { state.upload = d; markDirty(); log('形象照已上传 ' + d.checks.width + '×' + d.checks.height); renderAvatar(); })
      .catch(function (e) { log('上传失败：' + e.message); });
  }

  function quote(goNext) {
    var text = ($('kbText') || {}).value || '';
    if (!text.trim()) { log('请先粘贴口播稿'); return; }
    readParams();
    log('生成分段与报价…');
    return req('/api/skills/digital-human-koubo/quote', {
      method: 'POST', headers: headers(),
      body: JSON.stringify({ text: text, shot: state.form.shot, resolution: state.form.resolution || '', generator: state.form.generator || 'hifly' })
    }).then(function (d) {
      state.quote = d;
      state.script = text;
      renderSegments(); renderMeta();
      log('报价：' + d.segments.length + ' 段 / ' + d.total_credits + ' credits');
      return d;
    }).catch(function (e) { log('报价失败：' + e.message); throw e; });
  }

  function saveProject() {
    if (!state.quote) return Promise.reject(new Error('先报价'));
    var text = ($('kbText') || {}).value || '';
    return req('/api/skills/digital-human-koubo/projects', {
      method: 'POST', headers: headers(),
      body: JSON.stringify({
        name: '', script: text, shot: state.form.shot, host: state.form.host,
        photo_id: state.upload ? state.upload.id : '', options: state.form
      })
    }).then(function (p) {
      state.project = p;
      state.dirty = false;
      state.render.packaged = false;
      state.render.job = null;
      state.qa = null;
      if ($('kbProjName')) $('kbProjName').textContent = p.name;
      renderStatus(); renderPreview(); renderProjects(); loadProjects();
      log('项目已保存 ' + p.id);
      return p;
    });
  }

  function generate(all) {
    state.preview = null;
    var btn = $('kbGenerate');
    if (btn) { btn.disabled = true; }
    var needsNewProject = !state.project || state.dirty;
    if (state.project && state.dirty) log('检测到形象/文案/参数已修改，先创建新项目…');
    var prep = needsNewProject ? quote(true).then(saveProject) : Promise.resolve(state.project);
    prep.then(function (p) {
      log('提交生成（' + (all ? '全部' : '第 1 段') + '）…');
      return req('/api/skills/digital-human-koubo/projects/' + p.id + '/generate', {
        method: 'POST', headers: headers(), body: JSON.stringify({ all: !!all })
      });
    }).then(function (d) {
      state.job = { status: 'running', message: '准备提交', progress: 0, done: 0, total: all ? (state.quote ? state.quote.segments.length : 1) : 1 };
      renderAll();
      log('任务已提交 ' + d.job_id);
      pollJob(d.job_id);
    }).catch(function (e) {
      if (btn) btn.disabled = false;
      var msg = e && e.message ? e.message : '提交失败';
      if (/连接正在自动恢复/.test(msg)) msg = '本地服务刚重启，请 3 秒后重试';
      log('提交失败：' + msg);
      if (state.job) { state.job.status = 'error'; state.job.error = msg; }
      renderPreview();
    });
  }

  function pollJob(jobId) {
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(function () {
      req('/api/skills/digital-human-koubo/generate/jobs/' + encodeURIComponent(jobId)).then(function (job) {
        state.job = job;
        renderPreview(); renderStatus();
        if (job.status === 'running') loadProjects();
        if (job.status !== 'running') {
          clearInterval(state.timer); state.timer = null;
          var btn = $('kbGenerate'); if (btn) btn.disabled = false;
          if (job.status === 'done') {
            log('生成完成');
            loadProjects();
            req('/api/skills/digital-human-koubo/projects/' + state.project.id).then(function (p) {
              state.project = p; state.render.packaged = !!p.rendered; state.render.previewReady = !!p.preview_rendered; renderAll();
              // 底片完成后自动套用当前模板出预览，避免只拿到没有样式的底片
              if (!state.render.packaged && !state.render.previewReady && state.render.templateId) {
                log('底片完成，自动套用模板：' + state.render.templateId);
                startRender('preview');
              }
            });
          } else {
            log('生成失败：' + (job.error || job.message || ''));
          }
        }
      }).catch(function () {});
    }, 2500);
  }

  function openProject(pid) {
    req('/api/skills/digital-human-koubo/projects/' + encodeURIComponent(pid)).then(function (p) {
      state.preview = null;
      state.project = p;
      state.dirty = false;
      state.script = p.script || '';
      state.form = Object.assign({}, state.form, p.options || {});
      state.quote = { segments: p.segments || [], total_seconds: p.total_seconds || 0, total_credits: p.total_credits || 0 };
      state.job = null;
      if (p.template_id) state.render.templateId = p.template_id;
      else if (p.preview_template_id) state.render.templateId = p.preview_template_id;
      state.render.title = p.template_title || p.preview_template_title || '';
      state.render.packaged = !!p.rendered;
      state.render.previewReady = !!p.preview_rendered;
      state.render.showPreview = !!p.preview_rendered && !p.rendered;
      state.render.qa = p.rendered ? (p.qa_final || null) : (p.qa_preview || null);
      state.render.broll = (p.broll && p.broll.asset_id) ? p.broll : null;
      state.render.brollOpen = !!state.render.broll;
      state.render.bgm = (p.bgm && p.bgm.asset_id) ? p.bgm : null;
      state.render.bgmOpen = !!state.render.bgm;
      state.render.shots = Array.isArray(p.shots) ? p.shots : [];
      state.render.shotsEnabled = state.render.shots.length >= 2;
      state.render.recommendFor = '';
      state.render.recommendations = [];
      if (p.photo_id) {
        state.upload = { id: p.photo_id, url: '/api/skills/digital-human-koubo/uploads/' + p.photo_id, checks: {} };
      }
      if ($('kbText')) $('kbText').value = p.script || '';
      if ($('kbProjName')) $('kbProjName').textContent = p.name;
      renderAll(); updateCount();
      log('打开项目 ' + p.id);
    }).catch(function (e) { log('打开失败：' + e.message); });
  }

  function exportTasks() {
    if (!state.project) { log('先保存项目'); return; }
    req('/api/skills/digital-human-koubo/projects/' + encodeURIComponent(state.project.id) + '/tasks').then(function (d) {
      var blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'koubo-tasks-' + state.project.id + '.json';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
      log('已导出 tasks.json');
    }).catch(function (e) { log('导出失败：' + e.message); });
  }

  function loadTemplates() {
    return req('/api/skills/digital-human-koubo/templates').then(function (d) {
      state.render.templates = (d && d.templates) || [];
      var has = state.render.templates.some(function (t) { return t.id === state.render.templateId; });
      if (!has) {
        var def = state.render.templates.filter(function (t) { return t.default; })[0] || state.render.templates[0];
        if (def) state.render.templateId = def.id;
      }
      renderPack();
      return state.render.templates;
    }).catch(function (e) { log('模板读取失败：' + e.message); });
  }

  function loadOptions() {
    return req('/api/skills/digital-human-koubo/options').then(function (d) {
      state.options = d;
      if (!state.form.model && d.models && d.models.length) state.form.model = d.models[0].id;
      renderParams(); updateParamSummary(); renderSegments(); renderMeta();
      return d;
    }).catch(function (e) { log('参数读取失败：' + e.message); });
  }

  function loadProjects() {
    req('/api/skills/digital-human-koubo/projects/summary').then(function (d) {
      state.projects = d.projects || [];
      if (state.project && state.project.id) {
        var cur = state.projects.filter(function (x) { return x.id === state.project.id; })[0];
        if (cur) Object.assign(state.project, cur);
      }
      if (state.preview && state.preview.id) {
        var pv = state.projects.filter(function (x) { return x.id === state.preview.id; })[0];
        if (pv) Object.assign(state.preview, pv);
      }
      renderProjects();
    }).catch(function () {});
  }

  function loadStatus() {
    return req('/api/skills/digital-human-koubo/status').then(function (d) { state.components = d.components || []; return d; })
      .catch(function () {});
  }

  function pill(c) {
    if (c.status === 'ok') return '<span class="kb-pill ok">就绪' + (c.version ? ' ' + esc(c.version) : '') + '</span>';
    if (c.status === 'installing') return '<span class="kb-pill installing">安装中 ' + (c.progress || 0) + '%</span>';
    if (c.status === 'error') return '<span class="kb-pill missing">异常</span>';
    return '<span class="kb-pill missing">缺失</span>';
  }

  function renderModalRows() {
    var box = $('kbDepsBody');
    if (!box) return;
    box.innerHTML = state.components.map(function (c) {
      var btn = (c.installable && c.status !== 'ok' && c.status !== 'installing') ? '<button type="button" class="kb-install" data-dep="' + esc(c.id) + '">安装</button>' : '';
      var bar = c.status === 'installing' ? '<div class="kb-bar on"><i style="width:' + (c.progress || 0) + '%"></i></div>' : '';
      return '<div class="kb-dep"><div><b>' + esc(c.name) + '</b><small>' + esc(c.purpose) + ' · ' + esc(c.size) + '</small><small>' + esc(c.detail || '') + '</small></div><div class="kb-dep-right">' + pill(c) + btn + '</div>' + bar + '</div>';
    }).join('');
    box.querySelectorAll('[data-dep]').forEach(function (btn) {
      btn.onclick = function () { installDep(btn.getAttribute('data-dep')); };
    });
  }

  function openDeps() {
    if ($('kbDepsModal')) return;
    var m = document.createElement('div');
    m.id = 'kbDepsModal';
    m.innerHTML = '<div class="kb-modal-backdrop"></div><div class="kb-modal-card"><div class="kb-modal-head"><h4>运行环境与依赖</h4><button type="button" class="kb-modal-close">×</button></div>'
      + '<div class="kb-modal-body"><p class="kb-modal-sub">ffmpeg / Node / Python 已内置；whisper 只在回听与逐字对齐时使用，不装也能出片。下载走自有 TOS。</p><div id="kbDepsBody">加载中…</div></div>'
      + '<div class="kb-modal-foot"><button type="button" class="kb-btn" data-close>关闭</button></div></div>';
    document.body.appendChild(m);
    m.querySelector('.kb-modal-backdrop').onclick = closeDeps;
    m.querySelector('.kb-modal-close').onclick = closeDeps;
    m.querySelector('[data-close]').onclick = closeDeps;
    loadStatus().then(renderModalRows);
  }
  function closeDeps() { var m = $('kbDepsModal'); if (m && m.parentNode) m.parentNode.removeChild(m); }

  function installDep(id) {
    req('/api/skills/digital-human-koubo/deps/install', { method: 'POST', headers: headers(), body: JSON.stringify({ component: id }) })
      .then(function (d) { pollDep(d.job_id, id); })
      .catch(function (e) { log('安装失败：' + e.message); });
  }
  function pollDep(jobId, id) {
    var t = setInterval(function () {
      req('/api/skills/digital-human-koubo/deps/jobs/' + encodeURIComponent(jobId)).then(function (job) {
        var c = state.components.filter(function (x) { return x.id === id; })[0];
        if (c) { c.status = job.status === 'running' ? 'installing' : (job.status === 'done' ? 'ok' : 'error'); c.progress = job.progress; c.detail = job.message; renderModalRows(); }
        if (job.status !== 'running') { clearInterval(t); loadStatus().then(renderModalRows); }
      }).catch(function () {});
    }, 1200);
  }

  function bind() {
    document.addEventListener('click', function(evt) {
      var t = evt.target;
      if (!t || !t.id) return;
      if (t.id === 'kbHiflyRefresh') { evt.preventDefault(); loadHiflyAvatars(); }
      else if (t.id === 'kbHiflyCreate') { evt.preventDefault(); openHiflyTrainModal(); }
      else if (t.id === 'kbHiflyTrainClose' || t.id === 'kbHiflyTrainCancel') { evt.preventDefault(); closeHiflyTrainModal(); }
      else if (t.id === 'kbHiflyTrainSubmit') { evt.preventDefault(); submitHiflyTrain(); }
    });
    document.addEventListener('change', function(evt) {
      var t = evt.target;
      if (t && t.id === 'kbHiflyAvatar') { state.form.hifly_avatar_id = t.value || ''; markDirty(); return; }
      if (t && t.name === 'kbGen') {
        state.form.generator = t.value || 'hifly';
        if ($('kbGenLabel')) $('kbGenLabel').textContent = state.form.generator === 'hifly' ? '海螺数字人' : 'Grok AI';
        renderAvatar();
        markDirty();
      }
    });
    var ta = $('kbText');
    if (ta) ta.addEventListener('input', function () { updateCount(); markDirty(); });
    var tgl = $('kbParamToggle');
    if (tgl) tgl.onclick = function () { $('kbParams').classList.toggle('open'); };
    var q = $('kbQuote'); if (q) q.onclick = function () { quote(false); };
    var g = $('kbGenerate'); if (g) g.onclick = function () { generate(false); };
    var ga = $('kbGenerateAll'); if (ga) ga.onclick = function () { generate(true); };
    var sv = $('kbSave'); if (sv) sv.onclick = function () { quote(false).then(saveProject).catch(function () {}); };
    var ex = $('kbExport'); if (ex) ex.onclick = exportTasks;
    var dp = $('kbDeps'); if (dp) dp.onclick = openDeps;
    var pr = $('kbProjRefresh'); if (pr) pr.onclick = function () { loadProjects(); log('已刷新项目列表'); };
    var bk = $('kbBack'); if (bk) bk.onclick = function () {
      if (typeof window._switchToHiddenView === 'function') window._switchToHiddenView('skill-store');
      else if (typeof showLobsterView === 'function') showLobsterView('skill-store');
    };
  }

  function ensureShell() {
    var root = document.getElementById('content-' + VIEW_ID);
    if (!root) return null;
    if (!root.dataset.kouboBuilt) {
      root.innerHTML = shell();
      root.dataset.kouboBuilt = '1';
      injectCss();
      bind();
      if ((state.form && state.form.generator || 'hifly') === 'hifly') loadHiflyAvatars();
      renderAll();
      updateCount();
    }
    return root;
  }

  window._openDigitalHumanKouboView = function () {
    var switched = false;
    try {
      if (typeof window._switchToHiddenView === 'function') { window._switchToHiddenView(VIEW_ID); switched = true; }
      else if (typeof showLobsterView === 'function') { showLobsterView(VIEW_ID); switched = true; }
    } catch (e) {
      console.warn('[koubo] 切换视图失败，改为直接显示容器:', e);
      switched = false;
    }
    if (!switched) {
      try {
        var root = document.getElementById('content-' + VIEW_ID);
        if (root) {
          document.querySelectorAll('.content-block').forEach(function (el) { el.style.display = 'none'; });
          root.style.display = '';
          root.classList.add('active');
        }
      } catch (e2) {}
    }
    ensureShell();
    loadOptions();
    loadTemplatePrefs();
    loadTemplates();
    loadProjects();
    log('进入数字人3.0 工作台');
  };

  document.addEventListener('DOMContentLoaded', function () {
    var root = document.getElementById('content-' + VIEW_ID);
    if (root && !root.dataset.kouboBuilt) { ensureShell(); }
  });
})();
