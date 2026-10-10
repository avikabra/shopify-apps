/* Simple Returns — customer return portal (vanilla JS, no dependencies).
 *
 * Flow:
 *   1. Customer enters order number + email  -> POST /portal/lookup
 *   2. Picks items, quantities, reasons, resolution -> (exchange: variant picker via /portal/variants)
 *   3. Submits -> POST /portal/submit -> confirmation screen
 */
(function () {
  'use strict';

  var root = document.getElementById('simple-returns-portal');
  if (!root) return;
  var SHOP = root.dataset.shop;
  var API = (root.dataset.api || '').replace(/\/$/, '');

  var state = {
    step: 1,
    order: null,
    reasons: [],
    selected: {},   // line_item_id -> {quantity, reason, note, exchangeVariantId}
    resolution: 'refund',
    variantCache: {}, // product_id -> variants
    submitting: false,
  };

  function el(html) {
    var t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstChild;
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function showError(msg) {
    var old = root.querySelector('.sr-error');
    if (old) old.remove();
    root.prepend(el('<div class="sr-error" role="alert">' + esc(msg) + '</div>'));
  }

  async function api(path, body) {
    var res = await fetch(API + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ shop: SHOP }, body || {})),
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.detail || 'Something went wrong. Please try again.');
    return data;
  }

  function stepsBar() {
    return '<div class="sr-steps" aria-hidden="true">' +
      [1, 2, 3].map(function (n) {
        return '<div class="sr-step' + (state.step >= n ? ' done' : '') + '"></div>';
      }).join('') + '</div>';
  }

  /* -- step 1: find order ------------------------------------------------ */
  function renderLookup() {
    state.step = 1;
    root.innerHTML = stepsBar() +
      '<div class="sr-card"><h2>Find your order</h2>' +
      '<form id="sr-lookup">' +
      '<label>Order number<br><input type="text" name="order_name" required placeholder="#1001" autocomplete="off"></label>' +
      '<label>Email used for the order<br><input type="email" name="email" required autocomplete="email"></label>' +
      '<p><button class="sr-btn" type="submit">Find order</button></p>' +
      '<p class="sr-muted">Usually in your confirmation email, e.g. #1001.</p>' +
      '</form></div>';
    if (!API) showError('This store has not finished setting up returns yet (missing API URL).');

    root.querySelector('#sr-lookup').addEventListener('submit', async function (e) {
      e.preventDefault();
      var f = new FormData(e.target);
      var btn = e.target.querySelector('button');
      btn.disabled = true;
      try {
        state.reasons = (await api('/portal/reasons')).reasons;
        state.order = await api('/portal/lookup', {
          order_name: f.get('order_name'), email: f.get('email'),
        });
        state.order._email = f.get('email');
        state.order._orderName = f.get('order_name');
        state.selected = {};
        renderItems();
      } catch (err) { showError(err.message); btn.disabled = false; }
    });
  }

  /* -- step 2: pick items ------------------------------------------------- */
  function reasonOptions(selected) {
    return state.reasons.map(function (r) {
      return '<option value="' + esc(r.value) + '"' +
        (r.value === selected ? ' selected' : '') + '>' + esc(r.label) + '</option>';
    }).join('');
  }

  function renderItems() {
    state.step = 2;
    var items = state.order.items.map(function (it) {
      var sel = state.selected[it.line_item_id] || { quantity: 1, reason: 'OTHER', note: '', exchangeVariantId: '' };
      var dis = it.eligible ? '' : ' disabled';
      var qtyOpts = '';
      for (var q = 1; q <= it.quantity; q++) {
        qtyOpts += '<option value="' + q + '"' + (q === sel.quantity ? ' selected' : '') + '>' + q + '</option>';
      }
      return '<div class="sr-item" data-li="' + esc(it.line_item_id) + '">' +
        '<label><input type="checkbox" class="sr-pick"' + dis + '> <strong>' + esc(it.title) + '</strong>' +
        (it.variant_title ? ' <span class="sr-muted">(' + esc(it.variant_title) + ')</span>' : '') + '</label>' +
        (it.eligible ? '' : '<p class="sr-muted">Not eligible yet (hasn\u2019t shipped).</p>') +
        '<div class="sr-detail" hidden>' +
        '<label>Quantity <select class="sr-qty">' + qtyOpts + '</select></label>' +
        '<label>Reason <select class="sr-reason">' + reasonOptions(sel.reason) + '</select></label>' +
        '<label>Note (optional)<br><input type="text" class="sr-note" maxlength="500" value="' + esc(sel.note) + '"></label>' +
        '<div class="sr-exchange" hidden><label>Exchange for <select class="sr-variant"><option value="">Loading\u2026</option></select></label></div>' +
        '</div></div>';
    }).join('');

    root.innerHTML = stepsBar() +
      '<div class="sr-card"><h2>What would you like to return?</h2>' +
      '<p class="sr-muted">Order ' + esc(state.order.order_name) + '</p>' + items +
      '<h3>How should we make it right?</h3>' +
      '<label><input type="radio" name="sr-res" value="refund" checked> Refund to original payment method</label>' +
      '<label><input type="radio" name="sr-res" value="store_credit"> Store credit</label>' +
      '<label><input type="radio" name="sr-res" value="exchange"> Exchange for a different variant</label>' +
      '<p><button class="sr-btn sr-back sr-btn-secondary" type="button">Back</button> ' +
      '<button class="sr-btn sr-submit" type="button">Submit return request</button></p></div>';

    root.querySelector('.sr-back').addEventListener('click', renderLookup);

    root.querySelectorAll('.sr-pick').forEach(function (cb) {
      cb.addEventListener('change', function () {
        var item = cb.closest('.sr-item');
        item.querySelector('.sr-detail').hidden = !cb.checked;
      });
    });

    root.querySelectorAll('input[name="sr-res"]').forEach(function (r) {
      r.addEventListener('change', function () {
        state.resolution = root.querySelector('input[name="sr-res"]:checked').value;
        var show = state.resolution === 'exchange';
        root.querySelectorAll('.sr-item').forEach(function (item) {
          var ex = item.querySelector('.sr-exchange');
          ex.hidden = !(show && item.querySelector('.sr-pick').checked);
          if (show && item.querySelector('.sr-pick').checked) loadVariants(item);
        });
      });
    });

    root.querySelectorAll('.sr-item').forEach(function (item) {
      item.querySelector('.sr-pick').addEventListener('change', function () {
        if (state.resolution === 'exchange' && this.checked) loadVariants(item);
      });
    });

    root.querySelector('.sr-submit').addEventListener('click', submit);
  }

  async function loadVariants(itemEl) {
    var liId = itemEl.dataset.li;
    var item = state.order.items.find(function (i) { return i.line_item_id === liId; });
    var sel = itemEl.querySelector('.sr-variant');
    try {
      if (!state.variantCache[item.product_id]) {
        var data = await api('/portal/variants', { product_id: item.product_id });
        state.variantCache[item.product_id] = data.variants;
      }
      var variants = state.variantCache[item.product_id].filter(function (v) {
        return v.title !== item.variant_title;
      });
      sel.innerHTML = '<option value="">Choose a variant\u2026</option>' + variants.map(function (v) {
        return '<option value="' + esc(v.variant_id) + '">' + esc(v.title) + ' (' + esc(v.price) + ')</option>';
      }).join('');
    } catch (err) {
      sel.innerHTML = '<option value="">Could not load variants</option>';
    }
  }

  /* -- step 3: submit ------------------------------------------------------ */
  async function submit(e) {
    var btn = e.target;
    var items = [];
    var valid = true;
    root.querySelectorAll('.sr-item').forEach(function (itemEl) {
      if (!itemEl.querySelector('.sr-pick').checked) return;
      var liId = itemEl.dataset.li;
      var qty = parseInt(itemEl.querySelector('.sr-qty').value, 10);
      var reason = itemEl.querySelector('.sr-reason').value;
      var note = itemEl.querySelector('.sr-note').value;
      var payload = { line_item_id: liId, quantity: qty, reason: reason, note: note };
      if (state.resolution === 'exchange') {
        payload.exchange_variant_id = itemEl.querySelector('.sr-variant').value;
        if (!payload.exchange_variant_id) {
          showError('Please choose a replacement variant for each exchange item.');
          valid = false;
        }
      }
      items.push(payload);
    });
    if (!valid) return;
    if (!items.length) { showError('Please select at least one item.'); return; }

    btn.disabled = true;
    try {
      var res = await api('/portal/submit', {
        order_name: state.order._orderName,
        email: state.order._email,
        resolution: state.resolution,
        items: items,
      });
      renderDone(res);
    } catch (err) { showError(err.message); btn.disabled = false; }
  }

  function renderDone(res) {
    state.step = 3;
    var auto = res.status === 'approved' || res.status === 'label_created';
    root.innerHTML = stepsBar() +
      '<div class="sr-card"><h2>Request received</h2>' +
      '<p>Reference <strong>#' + esc(res.request_id) + '</strong>.</p>' +
      (auto && res.label_url
        ? '<p>Your return was auto-approved. Check your email for the shipping label.</p>'
        : auto
        ? '<p>Your return was auto-approved. We\u2019ll email your shipping label shortly.</p>'
        : '<p>We\u2019ll review your request and email you shortly.</p>') +
      '</div>';
  }

  renderLookup();
})();
