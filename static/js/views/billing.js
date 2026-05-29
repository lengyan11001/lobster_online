/* billing view logic: loaded on demand by view-registry.js */
function billingPackageYuan(p) {
  if (!p) return 0;
  if (p.price_yuan != null && p.price_yuan !== '') return Number(p.price_yuan);
  if (p.price_fen != null && p.price_fen !== '') return Number(p.price_fen) / 100;
  return 0;
}
/** 每元人民币可得算力（展示） */
function billingCreditsPerYuan(p) {
  var yuan = billingPackageYuan(p);
  var c = Number(p.credits || 0);
  if (!yuan || !c) return null;
  return Math.round((c / yuan) * 100) / 100;
}
function billingRatioHintLinesHtml(packages) {
  return '';
}
function billingRatioHintPlainText(packages) {
  return '';
}


function loadBillingView() {
  var balanceEl = document.getElementById('billingBalance');
  var rechargeListEl = document.getElementById('billingRechargeList');
  var creditHistoryEl = document.getElementById('billingCreditHistory');
  var rechargePagerEl = document.getElementById('billingRechargePager');
  var creditPagerEl = document.getElementById('billingCreditPager');
  var refreshBtn = document.getElementById('billingRefreshBtn');
  var pricingBlock = document.getElementById('billingPricingBlock');
  var pricingContent = document.getElementById('billingPricingContent');
  if (!rechargeListEl || !creditHistoryEl) return;
  var billingRechargePage = 1;
  var billingConsumptionPage = 1;
  /** 将服务端 UTC/无时区 ISO 格式化为北京时间展示（与 API 返回的 *_beijing 一致） */
  function formatIsoToBeijingDisplay(isoStr) {
    if (!isoStr) return '';
    try {
      var s = String(isoStr).trim();
      if (s.indexOf(' ') > 0 && s.indexOf('T') < 0) s = s.replace(' ', 'T');
      if (!/[zZ]$/.test(s) && !/[+-]\d{2}:?\d{2}$/.test(s)) s += 'Z';
      var d = new Date(s);
      if (isNaN(d.getTime())) return s.slice(0, 19).replace('T', ' ');
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
      return String(isoStr).slice(0, 19).replace('T', ' ');
    }
  }
  var PAGE_SIZE = 10;
  var base = (typeof API_BASE !== 'undefined' ? API_BASE : '').replace(/\/$/, '');
  if (!base) base = (typeof LOCAL_API_BASE !== 'undefined' ? LOCAL_API_BASE : '') || '';
  function parseListResp(d) {
    if (Array.isArray(d)) return { items: d, total: d.length };
    return { items: (d && d.items) ? d.items : [], total: (d && d.total != null) ? d.total : 0 };
  }
  function loadRechargePage(page) {
    billingRechargePage = Math.max(1, page);
    rechargeListEl.innerHTML = '<p class="meta" style="padding:1rem;">加载中…</p>';
    rechargePagerEl.innerHTML = '';
    var offset = (billingRechargePage - 1) * PAGE_SIZE;
    fetch(base + '/api/recharge/my-orders?limit=' + PAGE_SIZE + '&offset=' + offset, { headers: authHeaders() })
      .then(function(r) { return r.ok ? r.json() : { items: [], total: 0 }; })
      .then(function(d) {
        var data = parseListResp(d);
        var orders = data.items || [];
        var total = data.total || 0;
        if (orders.length === 0) {
          rechargeListEl.innerHTML = '<p class="meta" style="padding:1rem;">暂无充值记录。</p>';
        } else {
          var rh = '<table style="width:100%;border-collapse:collapse;font-size:0.82rem;"><thead><tr style="border-bottom:1px solid var(--border);"><th style="text-align:left;padding:0.5rem;">时间</th><th style="text-align:left;padding:0.5rem;">订单号</th><th style="text-align:right;padding:0.5rem;">金额</th><th style="text-align:right;padding:0.5rem;">算力</th><th style="text-align:left;padding:0.5rem;">状态</th></tr></thead><tbody>';
          orders.forEach(function(o) {
            var amt = (o.amount_fen && o.amount_fen > 0) ? (o.amount_fen / 100).toFixed(2) + ' 元' : (o.amount_yuan != null ? o.amount_yuan + ' 元' : '-');
            var time = (o.paid_at_beijing || o.created_at_beijing || '').trim() ||
              formatIsoToBeijingDisplay(o.paid_at || o.created_at || '');
            var st = o.status === 'paid' ? '已支付' : (o.status === 'cancelled' ? '已取消' : '待支付');
            rh += '<tr style="border-bottom:1px solid rgba(255,255,255,0.06);"><td style="padding:0.5rem;">' + escapeHtml(time) + '</td><td style="padding:0.5rem;">' + escapeHtml(o.out_trade_no || '-') + '</td><td style="padding:0.5rem;text-align:right;">' + amt + '</td><td style="padding:0.5rem;text-align:right;">' + (o.credits != null ? o.credits : '-') + '</td><td style="padding:0.5rem;">' + escapeHtml(st) + '</td></tr>';
          });
          rh += '</tbody></table>';
          rechargeListEl.innerHTML = rh;
        }
        var totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
        rechargePagerEl.innerHTML = '<span class="meta">第 ' + billingRechargePage + ' / ' + totalPages + ' 页</span>' +
          '<button type="button" class="btn btn-ghost btn-sm" id="billingRechargePrev"' + (billingRechargePage <= 1 ? ' disabled' : '') + '>上一页</button>' +
          '<button type="button" class="btn btn-ghost btn-sm" id="billingRechargeNext"' + (billingRechargePage >= totalPages ? ' disabled' : '') + '>下一页</button>';
        var prevBtn = document.getElementById('billingRechargePrev');
        var nextBtn = document.getElementById('billingRechargeNext');
        if (prevBtn && billingRechargePage > 1) prevBtn.onclick = function() { loadRechargePage(billingRechargePage - 1); };
        if (nextBtn && billingRechargePage < totalPages) nextBtn.onclick = function() { loadRechargePage(billingRechargePage + 1); };
      })
      .catch(function() { rechargeListEl.innerHTML = '<p class="meta" style="padding:1rem;">加载失败。</p>'; });
  }
  function loadConsumptionPage(page) {
    billingConsumptionPage = Math.max(1, page);
    creditHistoryEl.innerHTML = '<p class="meta" style="padding:1rem;">加载中…</p>';
    creditPagerEl.innerHTML = '';
    var offset = (billingConsumptionPage - 1) * PAGE_SIZE;
    fetch(base + '/api/billing/credit-history?limit=' + PAGE_SIZE + '&offset=' + offset, { headers: authHeaders() })
      .then(function(r) {
        return r.json().then(function(d) { return { ok: r.ok, status: r.status, d: d }; }).catch(function() {
          return { ok: r.ok, status: r.status, d: {} };
        });
      })
      .then(function(pack) {
        if (!pack.ok) {
          var msg = '加载算力流水失败（HTTP ' + pack.status + '）';
          var det = (pack.d && (pack.d.detail || pack.d.message)) ? String(pack.d.detail || pack.d.message) : '';
          if (det) msg += '：' + det.slice(0, 400);
          else if (pack.status === 404) {
            msg += '：本地址无算力流水接口。若用本机 IP 打开页面，请升级 lobster_online 后端（含 credit-history 转发），或用 ?api= 指向认证中心域名后重新登录。';
          }
          creditHistoryEl.innerHTML = '<p class="meta err" style="padding:1rem;">' + escapeHtml(msg) + '</p>';
          creditPagerEl.innerHTML = '';
          return;
        }
        var d = pack.d;
        var data = parseListResp(d);
        var history = data.items || [];
        var total = data.total || 0;
        if (history.length === 0) {
          creditHistoryEl.innerHTML = '<p class="meta" style="padding:1rem;">暂无算力变动。</p>';
        } else {
          var html = '<table style="width:100%;border-collapse:collapse;font-size:0.82rem;"><thead><tr style="border-bottom:1px solid var(--border);"><th style="text-align:left;padding:0.5rem;">时间</th><th style="text-align:left;padding:0.5rem;">类型</th><th style="text-align:right;padding:0.5rem;">变动</th><th style="text-align:left;padding:0.5rem;">说明</th></tr></thead><tbody>';
          function billingConsumptionTypeLabel(et, hType) {
            if (hType === 'recharge') return '充值增加';
            var e = (et || '').trim().toLowerCase();
            if (e === 'sutui_chat') return 'LLM对话扣费';
            if (e === 'pre_deduct') return '能力预扣';
            if (e === 'settle') return '能力结算';
            if (e === 'refund') return '退款';
            if (e === 'unit_charge' || e === 'direct_charge') return '能力扣费';
            if (e === 'skill_unlock') return '技能解锁';
            return et || '扣减';
          }
          function billingConsumptionDescription(h, typeText) {
            var hType = (h && h.type ? String(h.type) : '').trim().toLowerCase();
            var e = (h && h.entry_type ? String(h.entry_type) : '').trim().toLowerCase();
            if (hType === 'recharge') return '充值到账';
            if (e === 'pre_deduct') return '预扣';
            if (e === 'settle') return '结算';
            if (e === 'refund') return '退款';
            if (e === 'unit_charge' || e === 'direct_charge') return '扣费';
            if (e === 'sutui_chat') return '对话扣费';
            if (e === 'skill_unlock') return '技能解锁';
            return typeText || '算力变动';
          }
          history.forEach(function(h) {
            var time = (h.time_beijing || '').trim() || formatIsoToBeijingDisplay(h.time || '');
            var et = (h.entry_type || '').trim();
            var typeText = billingConsumptionTypeLabel(et, h.type);
            var amount = h.amount != null ? Number(h.amount) : 0;
            var amountStr;
            if (amount >= 0) {
              amountStr = '+' + (Math.abs(amount) > 0 && Math.abs(amount) < 1 ? amount.toFixed(4) : String(amount));
            } else {
              amountStr = (Math.abs(amount) > 0 && Math.abs(amount) < 1 ? amount.toFixed(4) : String(amount));
            }
            var desc = billingConsumptionDescription(h, typeText);
            if (h.balance_after != null && h.balance_after !== undefined) {
              desc = desc + '（余额 ' + h.balance_after + '）';
            }
            html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.06);"><td style="padding:0.5rem;">' + escapeHtml(time) + '</td><td style="padding:0.5rem;">' + escapeHtml(typeText) + '</td><td style="padding:0.5rem;text-align:right;">' + amountStr + '</td><td style="padding:0.5rem;">' + escapeHtml(desc) + '</td></tr>';
          });
          html += '</tbody></table>';
          creditHistoryEl.innerHTML = html;
        }
        var totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
        creditPagerEl.innerHTML = '<span class="meta">第 ' + billingConsumptionPage + ' / ' + totalPages + ' 页</span>' +
          '<button type="button" class="btn btn-ghost btn-sm" id="billingCreditPrev"' + (billingConsumptionPage <= 1 ? ' disabled' : '') + '>上一页</button>' +
          '<button type="button" class="btn btn-ghost btn-sm" id="billingCreditNext"' + (billingConsumptionPage >= totalPages ? ' disabled' : '') + '>下一页</button>';
        var prevBtn = document.getElementById('billingCreditPrev');
        var nextBtn = document.getElementById('billingCreditNext');
        if (prevBtn && billingConsumptionPage > 1) prevBtn.onclick = function() { loadConsumptionPage(billingConsumptionPage - 1); };
        if (nextBtn && billingConsumptionPage < totalPages) nextBtn.onclick = function() { loadConsumptionPage(billingConsumptionPage + 1); };
      })
      .catch(function() { creditHistoryEl.innerHTML = '<p class="meta err" style="padding:1rem;">网络错误，无法加载算力流水。</p>'; creditPagerEl.innerHTML = ''; });
  }
  loadRechargePage(1);
  loadConsumptionPage(1);
  var tabRecharge = document.querySelector('.store-tab[data-billing-tab="recharge"]');
  var tabConsumption = document.querySelector('.store-tab[data-billing-tab="consumption"]');
  var panelRecharge = document.getElementById('billingTabRecharge');
  var panelConsumption = document.getElementById('billingTabConsumption');
  function showBillingTab(tab) {
    if (tab === 'recharge') {
      if (panelRecharge) panelRecharge.style.display = '';
      if (panelConsumption) panelConsumption.style.display = 'none';
      if (tabRecharge) { tabRecharge.classList.add('active'); }
      if (tabConsumption) tabConsumption.classList.remove('active');
    } else {
      if (panelRecharge) panelRecharge.style.display = 'none';
      if (panelConsumption) panelConsumption.style.display = '';
      if (tabRecharge) tabRecharge.classList.remove('active');
      if (tabConsumption) tabConsumption.classList.add('active');
    }
  }
  if (tabRecharge) tabRecharge.onclick = function() { showBillingTab('recharge'); };
  if (tabConsumption) tabConsumption.onclick = function() { showBillingTab('consumption'); };
  if (pricingContent) {
    fetch(API_BASE + '/api/billing/pricing', { headers: authHeaders() })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) {
        if (!pricingContent) return;
        if (!d) { pricingContent.innerHTML = '<span class="meta">收费说明加载失败</span>'; return; }
        var packages = d.credit_packages || [];
        var html = '';
        if (packages.length) {
          html += '<p style="margin:0 0 0.35rem 0;"><strong>算力套餐</strong>：</p><ul style="margin:0;padding-left:1.25rem;">';
          packages.forEach(function(p) {
            html += '<li>' + escapeHtml(p.label || (p.price_yuan + '元 - ' + p.credits + '算力')) + '</li>';
          });
          html += '</ul>';
        } else {
          html = '<p style="margin:0;"><strong>算力套餐</strong>：100元/10000算力、300元/30000算力、500元/50000算力、1000元/100000算力。</p>';
        }
        pricingContent.innerHTML = html;
      })
      .catch(function() { if (pricingContent) pricingContent.innerHTML = '<span class="meta">收费说明加载失败</span>'; });
  }
  if (balanceEl) {
    if (typeof EDITION !== 'undefined' && EDITION !== 'online') {
      balanceEl.textContent = '单机版无速推余额，仅显示本机能力调用记录。';
    } else if (USE_INDEPENDENT_AUTH) {
      balanceEl.textContent = '我的算力：加载中…';
    } else {
      balanceEl.textContent = '速推余额：加载中…';
    }
  }
  function renderBalance(d) {
    if (!balanceEl || (typeof EDITION !== 'undefined' && EDITION !== 'online')) return;
    if (d && d.error) {
      balanceEl.textContent = '速推余额：' + (d.error || '--');
      return;
    }
    var yuan = (d && d.balance_yuan != null) ? String(d.balance_yuan) : (d && d.balance != null ? (d.balance / 1000).toFixed(2) : '--');
    balanceEl.textContent = '速推余额：' + yuan + ' 元' + (d && d.vip_level ? '（VIP' + d.vip_level + '）' : '');
  }
  if (USE_INDEPENDENT_AUTH && EDITION === 'online') {
    fetch(API_BASE + '/auth/me', { headers: authHeaders() })
      .then(function(r) { return r.json(); })
      .then(function(d) { if (balanceEl) balanceEl.textContent = '我的算力：' + (d && d.credits != null ? d.credits : '--'); })
      .catch(function() { if (balanceEl) balanceEl.textContent = '我的算力：--'; });
    var rechargeBlock = document.getElementById('rechargeBlock');
    if (rechargeBlock) {
      rechargeBlock.style.display = '';
      var rechargeTitle = rechargeBlock.querySelector('h4');
      if (rechargeTitle) rechargeTitle.textContent = '算力充值';
      var typeWrap = document.getElementById('rechargePaymentTypeWrap');
      var typeSel = document.getElementById('rechargePaymentType');
      if (typeWrap && typeSel) {
        if (USE_FUIOU_PAY) {
          typeWrap.style.display = 'block';
          typeSel.innerHTML = '<option value="WECHAT">微信支付</option><option value="ALIPAY">支付宝</option>';
          typeSel.value = 'WECHAT';
        } else {
          typeWrap.style.display = 'none';
        }
      }
      fetch(API_BASE + '/api/recharge/packages', { headers: authHeaders() })
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(opts) {
          var amountSel = document.getElementById('rechargeAmount');
          var hintEl = document.getElementById('rechargeRatioHint');
          if (amountSel && opts && Array.isArray(opts.packages) && opts.packages.length) {
            amountSel.innerHTML = opts.packages.map(function(p, i) {
              var py = billingPackageYuan(p);
              var lab = p.label || (py + '元 - ' + p.credits + '算力');
              return '<option value="' + i + '" data-credits="' + (p.credits || 0) + '">' + escapeHtml(lab) + '</option>';
            }).join('');
          }
          if (hintEl) {
            if (opts && Array.isArray(opts.packages) && opts.packages.length) {
              var ratioHint = billingRatioHintPlainText(opts.packages);
              hintEl.textContent = ratioHint;
              hintEl.style.display = ratioHint ? '' : 'none';
            } else {
              hintEl.textContent = '';
              hintEl.style.display = 'none';
            }
          }
        })
        .catch(function() {
          var hintEl = document.getElementById('rechargeRatioHint');
          if (hintEl) {
            hintEl.textContent = '';
            hintEl.style.display = 'none';
          }
        });
    }
    var rechargeSubmitBtn = document.getElementById('rechargeSubmitBtn');
    var rechargeMsg = document.getElementById('rechargeMsg');
    var rechargeResult = document.getElementById('rechargeResult');
    if (rechargeSubmitBtn && !rechargeSubmitBtn._ownRechargeBound) {
      rechargeSubmitBtn._ownRechargeBound = true;
      rechargeSubmitBtn.addEventListener('click', function() {
        var amountEl = document.getElementById('rechargeAmount');
        var idx = amountEl ? parseInt(amountEl.value, 10) : -1;
        var paymentType = 'WECHAT';
        if (USE_FUIOU_PAY) {
          var typeSel = document.getElementById('rechargePaymentType');
          paymentType = (typeSel && typeSel.value) ? String(typeSel.value).toUpperCase() : 'WECHAT';
        }
        var createBody = { package_index: idx };
        if (USE_FUIOU_PAY) createBody.payment_type = paymentType;
        if (!amountEl || idx < 0) { showMsg(rechargeMsg, '请选择套餐', true); return; }
        if (rechargeResult) { rechargeResult.style.display = 'none'; rechargeResult.innerHTML = ''; }
        rechargeSubmitBtn.disabled = true;
        showMsg(rechargeMsg, '正在创建订单…', false);
        var apiUrl = USE_FUIOU_PAY ? (API_BASE + '/api/recharge/fuiou-create') : (API_BASE + '/api/recharge/create');
        fetch(apiUrl, { method: 'POST', headers: authHeaders(), body: JSON.stringify(createBody) })
          .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
          .then(function(x) {
            if (!x.ok && x.data && x.data.detail) { showMsg(rechargeMsg, x.data.detail, true); return; }
            var d = x.data || {};
            showMsg(rechargeMsg, '', false);
            if (rechargeResult) {
              if (USE_FUIOU_PAY && d.qr_code) {
                var apiRoot = (typeof API_BASE !== 'undefined' && API_BASE) ? String(API_BASE).replace(/\/$/, '') : '';
                var qrSrc = apiRoot + '/api/recharge/qr-png?data=' + encodeURIComponent(d.qr_code);
                var responsePaymentType = String(d.payment_type || paymentType || 'WECHAT').toUpperCase();
                var paymentName = responsePaymentType === 'ALIPAY' ? '支付宝' : (responsePaymentType === 'UNIONPAY' ? '银联' : '微信');
                rechargeResult.innerHTML = '<p><strong>订单号：' + escapeHtml(d.out_trade_no || '') + '</strong></p>'
                  + '<p>请使用' + escapeHtml(paymentName) + '扫描下方二维码完成支付（富友扫码支付）。</p>'
                  + '<img src="' + escapeAttr(qrSrc) + '" alt="富友扫码支付二维码" style="max-width:220px;height:auto;margin-top:0.5rem;">'
                  + '<p id="fuiouPollStatus" style="margin-top:0.5rem;color:#888;">等待支付…</p>';
                rechargeResult.style.display = 'block';
                _startFuiouPoll(d.out_trade_no, d.order_date);
              } else {
                rechargeResult.innerHTML = '<p><strong>订单号：' + escapeHtml(d.out_trade_no || '') + '</strong></p><p>' + escapeHtml(d.payment_info || '') + '</p>';
                rechargeResult.style.display = 'block';
              }
            }
          })
          .catch(function() { showMsg(rechargeMsg, '网络错误', true); })
          .finally(function() { rechargeSubmitBtn.disabled = false; });
      });
    }
  } else if (typeof EDITION !== 'undefined' && EDITION === 'online') {
    fetch(API_BASE + '/api/sutui/balance', { headers: authHeaders() })
      .then(function(r) { return r.json(); })
      .then(renderBalance)
      .catch(function() { if (balanceEl) balanceEl.textContent = '速推余额：--'; });
    var rechargeBlock = document.getElementById('rechargeBlock');
    if (rechargeBlock) {
      rechargeBlock.style.display = '';
      fetch(API_BASE + '/api/sutui/recharge-options', { headers: authHeaders() })
        .then(function(r) { return r.json(); })
        .then(function(opts) {
          var amountSel = document.getElementById('rechargeAmount');
          var typeSel = document.getElementById('rechargePaymentType');
          if (amountSel && Array.isArray(opts.shops) && opts.shops.length) {
            amountSel.innerHTML = opts.shops.map(function(s) {
              return '<option value="' + Number(s.shop_id) + '" data-yuan="' + Number(s.money_yuan) + '">' + escapeHtml(s.title) + (s.tag ? ' ' + escapeHtml(s.tag) : '') + '</option>';
            }).join('');
          } else if (amountSel && Array.isArray(opts.amounts)) {
            amountSel.innerHTML = opts.amounts.map(function(a) { return '<option value="0" data-yuan="' + Number(a) + '">' + Number(a) + ' 元</option>'; }).join('');
          }
          if (typeSel) typeSel.style.display = 'none';
        })
        .catch(function() {});
    }
    var rechargeSubmitBtn = document.getElementById('rechargeSubmitBtn');
    var rechargeMsg = document.getElementById('rechargeMsg');
    var rechargeResult = document.getElementById('rechargeResult');
    if (rechargeSubmitBtn && !rechargeSubmitBtn._rechargeBound) {
      rechargeSubmitBtn._rechargeBound = true;
      rechargeSubmitBtn.addEventListener('click', function() {
        var amountEl = document.getElementById('rechargeAmount');
        var shopId = amountEl ? parseInt(amountEl.value, 10) : 0;
        if (!amountEl || (shopId === 0 && !amountEl.options[amountEl.selectedIndex].getAttribute('data-yuan'))) {
          showMsg(rechargeMsg, '请选择充值档位', true); return;
        }
        if (rechargeResult) { rechargeResult.style.display = 'none'; rechargeResult.innerHTML = ''; }
        rechargeSubmitBtn.disabled = true;
        showMsg(rechargeMsg, '正在创建订单…', false);
        fetch(API_BASE + '/api/sutui/recharge-create', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ shop_id: shopId })
        })
          .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, status: r.status, data: d }; }); })
          .then(function(x) {
            if (!x.ok && x.data && x.data.detail) {
              showMsg(rechargeMsg, x.data.detail, true);
              return;
            }
            var d = x.data || {};
            showMsg(rechargeMsg, '', false);
            if (d.need_oauth && d.recharge_url) {
              window.open(d.recharge_url, '_blank', 'noopener');
              if (rechargeResult) {
                rechargeResult.innerHTML = '<p>' + (d.message || '请前往速推官网完成登录后充值') + '。已为您打开充值页，若未打开<a href="' + escapeAttr(d.recharge_url) + '" target="_blank" rel="noopener" style="color:var(--primary);">点击此处</a>。</p>';
                rechargeResult.style.display = 'block';
              }
            } else if (d.pay_url) {
              window.open(d.pay_url, '_blank', 'noopener');
              if (rechargeResult) {
                rechargeResult.innerHTML = '<p>已打开支付页面，完成支付后余额将自动到账。若未打开，<a href="' + escapeAttr(d.pay_url) + '" target="_blank" rel="noopener" style="color:var(--primary);">点击此处</a>。</p>';
                rechargeResult.style.display = 'block';
              }
            } else if (d.qr_code) {
              if (rechargeResult) {
                var qr = d.qr_code;
                if (qr.indexOf('http') === 0 || qr.indexOf('data:') === 0) {
                  rechargeResult.innerHTML = '<p>请使用支付 App 扫描下方二维码：</p><img src="' + escapeAttr(qr) + '" alt="支付二维码" style="max-width:220px;height:auto;margin-top:0.5rem;">';
                } else {
                  rechargeResult.innerHTML = '<p>支付链接：<a href="' + escapeAttr(qr) + '" target="_blank" rel="noopener" style="color:var(--primary);">' + escapeHtml(qr.slice(0, 60)) + '…</a></p>';
                }
                rechargeResult.style.display = 'block';
              }
            }
            if (typeof loadSutuiBalance === 'function') loadSutuiBalance();
          })
          .catch(function() { showMsg(rechargeMsg, '网络错误', true); })
          .finally(function() { rechargeSubmitBtn.disabled = false; });
      });
    }
  } else {
    var rechargeBlock = document.getElementById('rechargeBlock');
    if (rechargeBlock) rechargeBlock.style.display = 'none';
  }
  if (refreshBtn) refreshBtn.onclick = loadBillingView;
}

