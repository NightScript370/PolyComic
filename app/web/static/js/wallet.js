/* /wallet: balance, top-up checkout, history, refund requests. */
(function () {
  'use strict';
  const PC = window.PC, $ = PC.$, el = PC.el, params = PC.params;
  let wallet = null, txs = [], refunds = [];
  let busy = false;

  PC.tabs(
    [{ tab: $('tab-add'), panel: $('panel-add') }, { tab: $('tab-history'), panel: $('panel-history') }, { tab: $('tab-refunds'), panel: $('panel-refunds') }]
  );

  function note(text, kind) {
    const n = $('wallet-note');
    n.textContent = text || '';
    n.hidden = !text;
    n.className = 'wallet-note' + (kind ? ' ' + kind : '');
  }
  function stripQuery() {
    const q = new URLSearchParams(location.search);
    ['checkout', 'session_id'].forEach((k) => q.delete(k));
    const s = q.toString();
    try { history.replaceState(null, '', location.pathname + (s ? '?' + s : '') + location.hash); } catch (x) { }
  }

  /* ---------- balance / price ---------- */
  function renderTop() {
    if (!wallet) return;
    $('balance').textContent = PC.money(wallet.balance_cents);
    const tiers = Array.isArray(wallet.tiers) && wallet.tiers.length ? wallet.tiers
      : [{ label: 'Book', price_cents: typeof wallet.book_price_cents === 'number' ? wallet.book_price_cents : 99, avg_cost_per_book_usd: wallet.avg_cost_per_book_usd }];
    const free = tiers.every((t) => t.price_cents === 0);
    $('price-line').firstChild.nodeValue = (free ? 'Books are free right now' : 'Books: ' + tiers.map((t) => t.label + ' ' + PC.money(t.price_cents)).join(' · ')) + ' ';
    $('avg-line').textContent = '· Avg. cost to make one: ' + tiers.map((t) => (tiers.length > 1 ? t.label + ' ' : '') + PC.usd(t.avg_cost_per_book_usd)).join(', ');
  }

  /* ---------- top-up ---------- */
  function renderPresets() {
    const box = $('presets');
    box.innerHTML = '';
    (wallet.presets_cents || [500, 1000, 2000]).forEach((c) => {
      const b = el('button', 'pill', PC.money(c).replace('.00', ''));
      b.type = 'button';
      b.setAttribute('aria-label', 'Add ' + PC.money(c));
      b.addEventListener('click', () => checkout(c));
      box.appendChild(b);
    });
    $('custom-amount').placeholder = Math.round(wallet.custom_min_cents / 100) + ' - ' + Math.round(wallet.custom_max_cents / 100);
  }
  function setTopupBusy(on) {
    busy = on;
    document.querySelectorAll('#presets .pill, #custom-go').forEach((b) => { b.disabled = on; });
  }
  function topupMsg(t) { $('topup-msg').textContent = t || ''; }
  function checkout(cents) {
    if (busy) return;
    topupMsg('');
    setTopupBusy(true);
    PC.api('POST', '/api/wallet/checkout', { amount_cents: cents }).then((r) => {
      if (r.ok && r.data && r.data.checkout_url) { location.href = r.data.checkout_url; return; }
      setTopupBusy(false);
      topupMsg((r.data && r.data.error) || 'Could not start checkout (' + r.status + ').');
    }).catch(() => { setTopupBusy(false); topupMsg('Could not reach the server. Please try again.'); });
  }
  $('custom-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const min = (wallet && wallet.custom_min_cents) || 100, max = (wallet && wallet.custom_max_cents) || 10000;
    const raw = $('custom-amount').value.trim().replace(/^\$/, '');
    if (!/^\d{1,3}(\.\d{1,2})?$/.test(raw)) { topupMsg('Enter an amount like 15 or 7.50.'); $('custom-amount').focus(); return; }
    const cents = Math.round(parseFloat(raw) * 100);
    if (cents < min || cents > max) { topupMsg('Amount must be between ' + PC.money(min) + ' and ' + PC.money(max) + '.'); $('custom-amount').focus(); return; }
    checkout(cents);
  });

  /* ---------- history ---------- */
  function chipClass(status) {
    const s = String(status || '').toLowerCase();
    if (/pending/.test(s)) return 'pending';
    if (/refund|reject|cancel/.test(s)) return 'muted';
    if (/fail|error/.test(s)) return 'failed';
    return 'done';
  }
  const chipText = (status) => String(status || 'ok').replace(/_/g, ' ').toUpperCase();
  function td(label, cls) {
    const c = el('td', cls || null);
    c.setAttribute('data-label', label);
    return c;
  }
  function pendingFor(topupId) {
    return refunds.some((r) => r.topup_id === topupId && /pending|approved/i.test(r.status || ''));
  }
  function renderTx() {
    const body = $('tx-body');
    body.innerHTML = '';
    $('tx-empty').hidden = txs.length > 0;
    $('tx-table').hidden = txs.length === 0;
    txs.forEach((t) => {
      const tr = document.createElement('tr');
      tr.appendChild(td('Date')).textContent = PC.fmtDate(t.created_at);
      const what = td('What', 'what');
      if (t.book_id) {
        const a = el('a', null, t.description || 'Book');
        a.href = PC.href('/', { mode: 'books', open: t.book_id });
        what.appendChild(a);
      } else what.textContent = t.description || t.kind;
      tr.appendChild(what);
      const amt = td('Amount', 'amt ' + (t.amount_cents < 0 ? 'neg' : 'pos'));
      amt.textContent = (t.amount_cents > 0 ? '+' : '') + PC.money(t.amount_cents);
      tr.appendChild(amt);
      tr.appendChild(td('Balance')).textContent = PC.money(t.balance_after_cents);
      const st = td('Status');
      st.appendChild(el('span', 'chip ' + chipClass(t.status), chipText(t.status)));
      tr.appendChild(st);
      const act = td('', 'act');
      if (t.kind === 'topup') {
        const b = el('button', 'pill small ghost', 'REQUEST REFUND');
        b.type = 'button';
        b.setAttribute('aria-label', 'Request refund for ' + PC.money(t.amount_cents) + ' top-up on ' + PC.fmtDate(t.created_at));
        const blocked = /pending|refund/i.test(t.status || '') || pendingFor(t.id);
        b.disabled = blocked;
        if (blocked) b.title = 'A refund was already requested for this top-up.';
        b.addEventListener('click', () => requestRefund(t));
        act.appendChild(b);
      }
      tr.appendChild(act);
      body.appendChild(tr);
    });
  }
  function requestRefund(t) {
    PC.dialog({
      title: 'REQUEST REFUND',
      text: 'Ask for a refund of the unspent part of your ' + PC.money(t.amount_cents) + ' top-up from ' + PC.fmtDate(t.created_at) + ' (up to about ' + PC.money(Math.min(t.amount_cents, wallet ? wallet.balance_cents : t.amount_cents)) + ', an estimate; the server works out the exact amount)? An admin will review it.',
      ok: 'REQUEST', cancel: 'CANCEL'
    }).then((res) => {
      if (!res) return;
      return PC.api('POST', '/api/wallet/refund-requests', { topup_id: t.id }).then((r) => {
        if (r.ok) note('Refund requested (up to ' + PC.money(r.data.amount_cents) + '). We will let you know here.', 'ok');
        else note((r.data && r.data.error) || 'Could not request that refund.', 'bad');
        return loadAll();
      });
    }).catch(() => note('Could not reach the server. Please try again.', 'bad'));
  }

  /* ---------- refund requests ---------- */
  function renderRefunds() {
    const ul = $('rf-list');
    ul.innerHTML = '';
    $('rf-empty').hidden = refunds.length > 0;
    refunds.forEach((r) => {
      const li = el('li', 'rf');
      const head = el('div', 'rf-head');
      head.appendChild(el('strong', null, PC.money(r.amount_cents)));
      head.appendChild(el('span', 'chip ' + chipClass(r.status === 'approved' ? 'done' : r.status), chipText(r.status)));
      li.appendChild(head);
      li.appendChild(el('p', 'rf-meta', 'Requested ' + PC.fmtDate(r.created_at)));
      if (r.admin_note) li.appendChild(el('p', 'rf-note', r.admin_note));
      ul.appendChild(li);
    });
  }

  function loadAll() {
    return Promise.all([
      PC.loadWallet(true),
      PC.api('GET', '/api/wallet/transactions'),
      PC.api('GET', '/api/wallet/refund-requests')
    ]).then((res) => {
      if (res[0]) { wallet = res[0]; renderTop(); }
      txs = res[1].ok && Array.isArray(res[1].data) ? res[1].data : [];
      refunds = res[2].ok && Array.isArray(res[2].data) ? res[2].data : [];
      renderTx();
      renderRefunds();
    });
  }

  /* ---------- checkout return ---------- */
  function handleReturn() {
    const c = params.get('checkout');
    if (c === 'cancel') {
      note('Checkout cancelled. No money was taken.', 'info');
      stripQuery();
      return Promise.resolve();
    }
    if (c === 'success' && params.get('session_id')) {
      note('Confirming your payment…', 'info');
      return PC.api('POST', '/api/wallet/checkout/confirm', { session_id: params.get('session_id') }).then((r) => {
        stripQuery();
        if (r.ok && r.data) {
          if (typeof r.data.balance_cents === 'number') PC.setBalance(r.data.balance_cents);
          note(r.data.credited ? 'Payment received. Your balance is now ' + PC.money(r.data.balance_cents) + '.' : 'Your balance is ' + PC.money(r.data.balance_cents) + '. If you were charged and it is not here yet, it will appear shortly.', r.data.credited ? 'ok' : 'info');
        } else note((r.data && r.data.error) || 'We could not confirm that payment yet. If you were charged, it will appear here shortly.', 'bad');
      }).catch(() => { stripQuery(); note('Could not confirm the payment right now. Please refresh in a moment.', 'bad'); });
    }
    return Promise.resolve();
  }

  PC.onMe((me) => {
    if (!me) {
      $('wallet-signin').hidden = false;
      $('presets').innerHTML = '';
      document.querySelectorAll('#custom-go, #custom-amount').forEach((b) => { b.disabled = true; });
      return;
    }
    handleReturn().then(loadAll).then(() => { if (wallet) renderPresets(); });
  });
})();
