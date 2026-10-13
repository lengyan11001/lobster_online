/**
 * 界面「跳闪 / 跳动」取证（2026-10-13）
 *
 * 现象：某些页面上鼠标一移动，界面就闪 / 突然跳几下。服务端日志里却干干净净
 *      （没有 GET / 的 reload、没有重新拉视图 HTML），根本看不出是谁干的。
 * 做法：在客户端把「看不见的动作」全部记账 ——
 *      · 谁把内容重画了：innerHTML / insertAdjacentHTML / replaceChildren
 *      · 谁把页面滚了：window/元素 scrollTo / scrollBy / scrollIntoView，以及滚动突跳
 *      · 谁把页面换了 / 焦点丢了 / 谁报错了
 *      · 出事前 120ms 内鼠标停在哪、当前在哪个视图、调用栈是谁
 * 判定：2 秒内 >=3 次结构性事件 = 一次「跳闪」，立刻上报
 *      POST /api/client-ui-trace → logs/client_ui_trace.jsonl（随诊断包一起上传）
 * 手动看：控制台 window.lobsterUiTrace.events() / .flush()
 */
(function initLobsterUiJumpTrace() {
  if (window.__lobsterUiJumpTraceReady) return;
  window.__lobsterUiJumpTraceReady = true;

  var MAX_EVENTS = 220;
  var BURST_WINDOW_MS = 2000;
  var BURST_MIN_EVENTS = 3;
  var POST_URL = '/api/client-ui-trace';
  var STRUCTURAL = {
    innerHTML: 1,
    insertAdjacentHTML: 1,
    replaceChildren: 1,
    'scroll-call': 1,
    'scroll-jump': 1,
    error: 1,
    rejection: 1,
  };

  var events = [];
  var burstStamps = [];
  var lastMouse = null;
  var wheelUntil = 0;
  var dirty = false;
  var scrollState = new WeakMap();

  function nowMs() {
    return Date.now();
  }

  function clip(value, max) {
    var text = String(value === undefined || value === null ? '' : value);
    return text.length > max ? text.slice(0, max) : text;
  }

  function stackText() {
    try {
      var raw = String(new Error('trace').stack || '');
      return clip(
        raw.split('\n').slice(2, 7).map(function (line) { return line.trim(); }).join(' | '),
        700
      );
    } catch (err) {
      return '';
    }
  }

  function describe(node) {
    try {
      if (!node) return '';
      if (node.nodeType !== 1) return String(node.nodeName || '');
      var tag = String(node.tagName || '').toLowerCase();
      var id = node.id ? '#' + node.id : '';
      var cls = '';
      var raw = String(node.className || '');
      if (raw && raw.trim()) cls = '.' + raw.trim().split(/\s+/).slice(0, 3).join('.');
      return clip(tag + id + cls, 120);
    } catch (err) {
      return '';
    }
  }

  function currentView() {
    try {
      // 客户端视图容器是 .content-block，当前那个带 .visible（2026-10-13 实测）
      var shown = document.querySelector('.content-block.visible');
      if (shown && shown.id) return shown.id;
      var blocks = document.querySelectorAll('.content-block');
      for (var i = 0; i < blocks.length; i += 1) {
        if (window.getComputedStyle(blocks[i]).display !== 'none') return blocks[i].id || '(content-block)';
      }
    } catch (err) {}
    try {
      var active = document.querySelector('#appTopNav .nav-left-item.active');
      if (active) return 'nav:' + clip(String(active.textContent || '').trim(), 40);
    } catch (err) {}
    return '(unknown)';
  }

  function clientVersion() {
    try {
      var info = window.__lobsterClientVersion || {};
      return clip(String(info.version || '') + '#' + String(info.build || ''), 40);
    } catch (err) {
      return '';
    }
  }

  function scrollerOf(target) {
    if (!target || target === document || target === window) {
      return document.scrollingElement || document.documentElement;
    }
    return target;
  }

  function scrollerName(el) {
    if (!el) return '(none)';
    if (el === document.scrollingElement || el === document.documentElement || el === document.body) return 'window';
    return describe(el);
  }

  function push(kind, detail, extra) {
    var record = {
      t: nowMs(),
      at: new Date().toISOString(),
      kind: kind,
      detail: clip(detail, 320),
      view: currentView(),
      stack: stackText(),
    };
    if (extra) {
      for (var key in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, key)) record[key] = extra[key];
      }
    }
    if (lastMouse) record.mouse = lastMouse;
    events.push(record);
    if (events.length > MAX_EVENTS) events.shift();
    dirty = true;

    if (STRUCTURAL[kind]) {
      burstStamps.push(nowMs());
      while (burstStamps.length && burstStamps[0] < nowMs() - BURST_WINDOW_MS) burstStamps.shift();
      if (burstStamps.length >= BURST_MIN_EVENTS) {
        burstStamps = [];
        flush('burst');
      }
    }
    return record;
  }

  function snapshot(reason) {
    return {
      ts: new Date().toISOString(),
      reason: reason || 'manual',
      client_version: clientVersion(),
      url: location.href,
      view: currentView(),
      ua: navigator.userAgent,
      visibility: document.visibilityState || '',
      mouse: lastMouse,
      events: events.slice(-120),
    };
  }

  function flush(reason) {
    if (!dirty) return false;
    dirty = false;
    var payload = JSON.stringify(snapshot(reason));
    try {
      window.localStorage.setItem('lobster.ui_trace.tail', JSON.stringify(events.slice(-40)));
    } catch (err) {}
    try {
      if (navigator.sendBeacon) {
        var blob = new Blob([payload], { type: 'application/json' });
        if (navigator.sendBeacon(POST_URL, blob)) return true;
      }
    } catch (err) {}
    try {
      fetch(POST_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true,
      }).catch(function () {});
      return true;
    } catch (err) {
      return false;
    }
  }

  function hookScrollCalls() {
    try {
      ['scrollTo', 'scrollBy'].forEach(function (name) {
        var winFn = window[name];
        if (typeof winFn === 'function') {
          window[name] = function () {
            push('scroll-call', 'window.' + name + '(' + Array.prototype.join.call(arguments, ',') + ')');
            return winFn.apply(window, arguments);
          };
        }
        var protoFn = Element.prototype[name];
        if (typeof protoFn === 'function') {
          Element.prototype[name] = function () {
            push('scroll-call', describe(this) + '.' + name + '(' + Array.prototype.join.call(arguments, ',') + ')');
            return protoFn.apply(this, arguments);
          };
        }
      });
      var intoView = Element.prototype.scrollIntoView;
      if (typeof intoView === 'function') {
        Element.prototype.scrollIntoView = function () {
          push('scroll-call', describe(this) + '.scrollIntoView');
          return intoView.apply(this, arguments);
        };
      }
    } catch (err) {}
  }

  function hookContentWrites() {
    try {
      var desc = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
      if (desc && desc.set) {
        Object.defineProperty(Element.prototype, 'innerHTML', {
          configurable: true,
          enumerable: !!desc.enumerable,
          get: desc.get,
          set: function (value) {
            push('innerHTML', describe(this) + ' len=' + String(value === undefined || value === null ? 0 : value.length));
            return desc.set.call(this, value);
          },
        });
      }
    } catch (err) {}
    try {
      var insertHtml = Element.prototype.insertAdjacentHTML;
      if (typeof insertHtml === 'function') {
        Element.prototype.insertAdjacentHTML = function (position, markup) {
          push('insertAdjacentHTML', describe(this) + ' ' + String(position) + ' len=' + String(markup === undefined || markup === null ? 0 : markup.length));
          return insertHtml.apply(this, arguments);
        };
      }
    } catch (err) {}
    try {
      var replaceChildren = Element.prototype.replaceChildren;
      if (typeof replaceChildren === 'function') {
        Element.prototype.replaceChildren = function () {
          push('replaceChildren', describe(this) + ' args=' + arguments.length);
          return replaceChildren.apply(this, arguments);
        };
      }
    } catch (err) {}
  }

  function hookScrollWatch() {
    document.addEventListener('wheel', function () { wheelUntil = nowMs() + 700; }, { passive: true, capture: true });
    document.addEventListener('touchstart', function () { wheelUntil = nowMs() + 700; }, { passive: true, capture: true });
    document.addEventListener('keydown', function () { wheelUntil = nowMs() + 700; }, true);
    document.addEventListener('scroll', function (evt) {
      try {
        var el = scrollerOf(evt.target);
        var y = Number((el && el.scrollTop) || 0);
        var x = Number((el && el.scrollLeft) || 0);
        var prev = scrollState.get(el);
        scrollState.set(el, { y: y, x: x });
        if (!prev) return;
        var dy = y - prev.y;
        var dx = x - prev.x;
        if ((Math.abs(dy) >= 120 || Math.abs(dx) >= 400) && nowMs() > wheelUntil) {
          push('scroll-jump', scrollerName(el) + ' dy=' + Math.round(dy) + ' dx=' + Math.round(dx) + ' to=' + Math.round(y));
        }
      } catch (err) {}
    }, { passive: true, capture: true });
  }

  function hookMouseBreadcrumbs() {
    var lastAt = 0;
    document.addEventListener('mousemove', function (evt) {
      var stamp = nowMs();
      if (stamp - lastAt < 120) return;
      lastAt = stamp;
      var node = null;
      try { node = document.elementFromPoint(evt.clientX, evt.clientY); } catch (err) {}
      lastMouse = { x: Math.round(evt.clientX), y: Math.round(evt.clientY), over: describe(node), at: stamp };
    }, { passive: true, capture: true });
  }

  function hookGlobalSignals() {
    window.addEventListener('error', function (evt) {
      push('error', String((evt && (evt.message || evt.error)) || 'error'));
    });
    window.addEventListener('unhandledrejection', function (evt) {
      push('rejection', String((evt && evt.reason) || 'rejection'));
    });
    document.addEventListener('visibilitychange', function () {
      push('visibility', document.visibilityState || '');
    });
    window.addEventListener('blur', function () { push('window-blur', ''); });
    window.addEventListener('focus', function () { push('window-focus', ''); });
    window.addEventListener('pagehide', function () { flush('pagehide'); });
    window.addEventListener('beforeunload', function () { flush('beforeunload'); });
    window.setInterval(function () { if (dirty) flush('interval'); }, 30000);
  }

  try {
    var tail = window.localStorage.getItem('lobster.ui_trace.tail');
    if (tail) {
      var parsed = JSON.parse(tail);
      if (Object.prototype.toString.call(parsed) === '[object Array]') events = parsed;
    }
  } catch (err) {}

  hookScrollCalls();
  hookContentWrites();
  hookScrollWatch();
  hookMouseBreadcrumbs();
  hookGlobalSignals();

  window.lobsterUiTrace = {
    events: function () { return events.slice(); },
    snapshot: snapshot,
    flush: flush,
  };
})();
