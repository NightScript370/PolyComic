/* /admin: refund review + API cost stats. Non-admins get 404 from the API. */
(function () {
  'use strict';
  const PC = window.PC, $ = PC.$, el = PC.el;
  let pendingCount = null, costs = null;

  PC.tabs([{ tab: $('tab-refunds'), panel: $('panel-refunds') }, { tab: $('tab-costs'), panel: $('panel-costs') }]);

  const td = (label, cls) => { const c = el('td', cls || null); c.setAttribute('data-label', label); return c; };
  const chipClass = (s) => (s === 'pending' ? 'pending' : s === 'approved' ? 'done' : s === 'rejected' ? 'muted' : 'failed');
  const msg = (t, bad) => { const m = $('admin-msg'); m.textContent = t || ''; m.classList.toggle('ok', !bad); };

  function denied() {
    $('admin-denied').hidden = false;
    document.querySelector('.head-pair').hidden = true;
    $('tab-refunds').closest('.tabbar').hidden = true;
    $('panel-refunds').hidden = true;
    $('panel-costs').hidden = true;
  }

  /* ---------- refunds ---------- */
  function userCell(r) {
    const c = td('User', 'what');
    c.appendChild(el('span', 'u-name', r.user_name || r.user_email));
    c.appendChild(el('small', 'u-mail', r.user_email));
    return c;
  }
  function renderPending(list) {
    pendingCount = list.length;
    $('head-pending').textContent = String(list.length);
    $('pend-empty').hidden = list.length > 0;
    const ul = $('pend-list');
    ul.innerHTML = '';
    list.forEach((r) => {
      const li = el('li', 'rf adm');
      const head = el('div', 'rf-head');
      head.appendChild(el('strong', null, PC.money(r.amount_cents)));
      head.appendChild(el('span', 'rf-meta', PC.fmtDateTime(r.created_at)));
      li.appendChild(head);
      li.appendChild(el('p', 'rf-user', (r.user_name ? r.user_name + ' · ' : '') + r.user_email));
      li.appendChild(el('p', 'rf-meta', 'Top-up ' + PC.money(r.topup_amount_cents) + ' · current balance ' + PC.money(r.balance_cents)));
      const act = el('div', 'rpg-actions');
      const ok = el('button', 'pill small', 'APPROVE');
      ok.type = 'button';
      ok.setAttribute('aria-label', 'Approve refund of ' + PC.money(r.amount_cents) + ' for ' + r.user_email);
      const no = el('button', 'pill small ghost', 'REJECT');
      no.type = 'button';
      no.setAttribute('aria-label', 'Reject refund of ' + PC.money(r.amount_cents) + ' for ' + r.user_email);
      ok.addEventListener('click', () => decide(r, 'approve', [ok, no]));
      no.addEventListener('click', () => decide(r, 'reject', [ok, no]));
      act.appendChild(ok);
      act.appendChild(no);
      li.appendChild(act);
      ul.appendChild(li);
    });
  }
  function renderHistory(list) {
    const rows = list.filter((r) => r.status !== 'pending');
    $('hist-empty').hidden = rows.length > 0;
    $('hist-table').hidden = rows.length === 0;
    const body = $('hist-body');
    body.innerHTML = '';
    rows.forEach((r) => {
      const tr = document.createElement('tr');
      tr.appendChild(td('Date')).textContent = PC.fmtDate(r.created_at);
      tr.appendChild(userCell(r));
      tr.appendChild(td('Amount', 'amt')).textContent = PC.money(r.amount_cents);
      const st = td('Status');
      st.appendChild(el('span', 'chip ' + chipClass(r.status), String(r.status).toUpperCase()));
      tr.appendChild(st);
      tr.appendChild(td('Note', 'note')).textContent = r.admin_note || '—';
      body.appendChild(tr);
    });
  }
  function loadRefunds() {
    return PC.api('GET', '/api/admin/refund-requests?status=all').then((r) => {
      if (r.status === 404 || r.status === 401 || r.status === 403) { denied(); return; }
      if (!r.ok || !Array.isArray(r.data)) { msg('Could not load refund requests.', true); return; }
      const all = r.data.slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
      renderPending(all.filter((x) => x.status === 'pending').reverse());
      renderHistory(all);
    }).catch(() => msg('Could not reach the server.', true));
  }
  function decide(r, action, btns) {
    const approve = action === 'approve';
    PC.dialog({
      title: approve ? 'APPROVE REFUND' : 'REJECT REFUND',
      text: (approve ? 'Refund ' : 'Reject the refund of ') + PC.money(r.amount_cents) + (approve ? ' to ' : ' for ') + r.user_email + '?',
      input: approve ? null : { label: 'Note to the user (optional)' },
      ok: approve ? 'APPROVE' : 'REJECT', cancel: 'CANCEL'
    }).then((res) => {
      if (!res) return;
      btns.forEach((b) => { b.disabled = true; });
      return PC.api('POST', '/api/admin/refund-requests/' + encodeURIComponent(r.id) + '/' + action, approve ? undefined : { note: res.value }).then((x) => {
        if (x.ok) msg((approve ? 'Approved ' : 'Rejected ') + PC.money(r.amount_cents) + '.');
        else msg((x.data && x.data.error) || 'That did not work (' + x.status + ').', true);
        return loadRefunds();
      });
    }).catch(() => { btns.forEach((b) => { b.disabled = false; }); msg('Could not reach the server.', true); });
  }

  /* ---------- costs ---------- */
  function tile(label, value, sub, cls) {
    const t = el('div', 'tile' + (cls ? ' ' + cls : ''));
    t.appendChild(el('p', 'tile-lab', label));
    t.appendChild(el('p', 'tile-num', value));
    if (sub) t.appendChild(el('p', 'tile-sub', sub));
    return t;
  }
  const signed = (n) => (n < 0 ? '-' : '+') + PC.usd(Math.abs(n));
  function renderCosts(c) {
    costs = c;
    const tiers = Array.isArray(c.tiers) ? c.tiers : [];
    const priceOf = {};
    tiers.forEach((t) => { priceOf[t.id] = t.price_usd; });
    const head = tiers.filter((t) => t.id === 'cost_efficient')[0];
    const headMargin = head ? head.margin_per_book_usd : null;
    $('head-margin').textContent = typeof headMargin === 'number' ? signed(headMargin) : '—';
    const tiles = $('tiles');
    tiles.innerHTML = '';
    /* one wide tile per quality tier: avg cost next to the tier's price, median, margin */
    tiers.forEach((t) => {
      const has = typeof t.margin_per_book_usd === 'number';
      const pct = has && t.price_usd > 0 ? Math.round(t.margin_per_book_usd / t.price_usd * 100) : null;
      const sub = 'Price ' + PC.usd(t.price_usd) + ' · median ' + PC.usd(t.median_cost_per_book_usd) +
        ' · margin ' + (has ? signed(t.margin_per_book_usd) + (pct === null ? '' : ' (' + pct + '%)') : '—') +
        ' · ' + t.books_done + (t.books_done === 1 ? ' book' : ' books');
      tiles.appendChild(tile(t.label + ': avg cost / book', PC.usd(t.avg_cost_per_book_usd), sub, has && t.margin_per_book_usd < 0 ? 'bad wide' : 'wide'));
    });
    if (!tiers.length) tiles.appendChild(tile('Avg cost / book', PC.usd(c.avg_cost_per_book_usd), 'Price ' + PC.usd(c.price_usd), 'wide'));
    tiles.appendChild(tile('Books done', String(c.books_done == null ? '—' : c.books_done)));
    tiles.appendChild(tile('Median cost (all)', PC.usd(c.median_cost_per_book_usd)));
    tiles.appendChild(tile('Total API spend', PC.usd(c.total_api_cost_usd)));
    tiles.appendChild(tile('Failed calls', String(c.failed_calls == null ? '—' : c.failed_calls), null, c.failed_calls > 0 ? 'warn' : ''));

    const kb = $('kind-body');
    kb.innerHTML = '';
    Object.keys(c.by_kind || {}).forEach((k) => {
      const v = c.by_kind[k];
      const tr = document.createElement('tr');
      tr.appendChild(td('Kind', 'what')).textContent = k;
      tr.appendChild(td('Calls', 'amt')).textContent = String(v.calls);
      tr.appendChild(td('Cost', 'amt')).textContent = PC.usd(v.cost_usd);
      tr.appendChild(td('Per call', 'amt')).textContent = v.calls ? '$' + (v.cost_usd / v.calls).toFixed(3) : '—';
      kb.appendChild(tr);
    });
    const rb = $('recent-body');
    rb.innerHTML = '';
    (c.recent_books || []).forEach((b) => {
      const tr = document.createElement('tr');
      const nm = td('Book', 'what');
      nm.textContent = (b.story === 'red' ? 'Red Riding Hood' : b.story === 'jack' ? 'Jack' : b.story || 'Book');
      nm.appendChild(el('small', 'u-mail', String(b.book_id).slice(0, 8)));
      tr.appendChild(nm);
      const tc = td('Tier');
      if (b.tier) tc.appendChild(el('span', 'chip tier ' + (b.tier === 'professional' ? 'professional' : 'cost-efficient'), b.tier === 'professional' ? 'PRO' : 'COST-EFF.'));
      else tc.textContent = '—';
      tr.appendChild(tc);
      tr.appendChild(td('Date')).textContent = PC.fmtDate(b.created_at);
      const price = priceOf[b.tier] != null ? priceOf[b.tier] : c.price_usd;
      const overPrice = typeof price === 'number' && b.cost_usd > price;
      tr.appendChild(td('Cost', 'amt' + (overPrice ? ' neg' : ''))).textContent = PC.usd(b.cost_usd);
      tr.appendChild(td('Calls', 'amt')).textContent = String(b.calls);
      tr.appendChild(td('Retries', 'amt')).textContent = String(b.retries);
      rb.appendChild(tr);
    });
  }
  function loadCosts() {
    return PC.api('GET', '/api/admin/costs').then((r) => {
      if (r.status === 404 || r.status === 401 || r.status === 403) { denied(); return; }
      if (r.ok && r.data) renderCosts(r.data);
    }).catch(() => { });
  }

  PC.onMe((me) => {
    if (!me || !me.is_admin) { denied(); if (!me) $('admin-denied').innerHTML = 'Please <a href="/auth/login">sign in</a> as an admin.'; return; }
    loadRefunds();
    loadCosts();
  });
})();
