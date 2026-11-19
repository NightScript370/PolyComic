/* PolyComic shared helpers: API wrapper, header/auth/balance, dialogs, tabs, demo backend (?demo=1). */
(function () {
  'use strict';
  const params = new URLSearchParams(location.search);
  const DEMO = params.get('demo') === '1';
  const PC = (window.PC = { params, DEMO, SLOW: params.get('slow') === '1', me: undefined, wallet: null });

  const $ = (id) => document.getElementById(id);
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  PC.$ = $;
  PC.el = el;

  /* ---------- formatting ---------- */
  PC.money = (c) => (c < 0 ? '-' : '') + '$' + (Math.abs(Number(c) || 0) / 100).toFixed(2);
  PC.usd = (n) => (typeof n === 'number' && isFinite(n) ? '$' + n.toFixed(2) : '—');
  PC.fmtDate = (s) => {
    const d = new Date(s);
    return isNaN(d) ? '' : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  };
  PC.fmtDateTime = (s) => {
    const d = new Date(s);
    return isNaN(d) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' +
      d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  };
  /* Keep demo flags when moving between pages. */
  PC.href = (path, extra) => {
    const q = new URLSearchParams();
    if (DEMO) {
      q.set('demo', '1');
      ['admin', 'slow', 'signedout'].forEach((k) => { if (params.get(k) === '1') q.set(k, '1'); });
      if (params.get('state') === 'nofunds') q.set('state', 'nofunds');
    }
    Object.keys(extra || {}).forEach((k) => q.set(k, extra[k]));
    const s = q.toString();
    return path + (s ? '?' + s : '');
  };
  PC.pdfName = (url) => {
    if (!url) return '';
    const clean = String(url).split(/[?#]/)[0] || '';
    const last = clean.split('/').pop() || '';
    try { return decodeURIComponent(last); } catch (x) { return last; }
  };

  /* ---------- clock ---------- */
  function tick() {
    const d = new Date();
    const t = ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
    document.querySelectorAll('[data-clock]').forEach((e) => { e.textContent = t; });
  }
  tick();
  setInterval(tick, 30000);

  /* ---------- placeholder art (demo mode) ---------- */
  const imgCache = {};
  PC.canvasImg = (color, label, w, h) => {
    const key = [color, label, w, h].join('|');
    if (imgCache[key]) return imgCache[key];
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const g = c.getContext('2d');
    const grad = g.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, color); grad.addColorStop(1, '#2f7a3a');
    g.fillStyle = grad; g.fillRect(0, 0, w, h);
    g.fillStyle = 'rgba(255,255,255,.3)';
    g.beginPath(); g.moveTo(w / 2, h * .2); g.lineTo(w * .8, h * .38); g.lineTo(w * .8, h * .66); g.lineTo(w / 2, h * .82); g.lineTo(w * .2, h * .66); g.lineTo(w * .2, h * .38); g.closePath(); g.fill();
    g.fillStyle = '#123'; g.font = 'bold ' + Math.round(h / 9) + 'px monospace'; g.textAlign = 'center';
    g.fillText(label, w / 2, h / 2 + h / 18);
    imgCache[key] = c.toDataURL('image/png');
    return imgCache[key];
  };

  /* ---------- demo backend ---------- */
  const demoHandlers = [];
  PC.demo = (fn) => { demoHandlers.push(fn); };
  const DAY = 864e5;
  const dw = { balance: 0, tx: [], refunds: [], admin: [], sessions: {}, seq: 100 };

  function seedWallet() {
    const now = Date.now();
    const low = params.get('state') === 'nofunds';
    const rows = low ? [
      [8, 'topup', 500, 'Top-up $5.00', null, 'completed'],
      [7, 'charge', -99, 'Book: Sam and the Beanstalk', 'demo-done', 'completed'],
      [6, 'charge', -99, 'Book: Mia and the Wolf', 'demo-fail', 'completed'],
      [4, 'charge', -99, 'Book: Zoe and the Beanstalk', 'demo-x', 'completed'],
      [3, 'charge', -99, 'Book: Kai and the Beanstalk', 'demo-y', 'completed'],
      [1, 'charge', -99, 'Book: Ada and the Wolf', 'demo-z', 'completed']
    ] : [
      [12, 'topup', 1000, 'Top-up $10.00', null, 'completed'],
      [11, 'charge', -99, 'Book: Sam and the Beanstalk', 'demo-done', 'completed'],
      [9, 'charge', -99, 'Book: Mia and the Wolf', 'demo-fail', 'completed'],
      [9, 'recredit', 99, 'Re-credit: book failed', 'demo-fail', 'completed'],
      [6, 'topup', 500, 'Top-up $5.00', null, 'refunded'],
      [5, 'refund', -500, 'Refund of $5.00 top-up', null, 'completed'],
      [2, 'charge', -99, 'Book: Zoe and the Beanstalk', 'demo-x', 'completed'],
      [1, 'charge', -99, 'Book: Kai and the Beanstalk', 'demo-y', 'completed']
    ];
    let bal = 0;
    dw.tx = rows.map((r, i) => {
      bal += r[2];
      return { id: 't' + (i + 1), created_at: new Date(now - r[0] * DAY).toISOString(), kind: r[1], amount_cents: r[2], balance_after_cents: bal, description: r[3], book_id: r[4], status: r[5] };
    }).reverse();
    dw.balance = bal;
    if (!low) {
      dw.refunds = [{ id: 'r1', topup_id: 't5', status: 'approved', amount_cents: 500, created_at: new Date(now - 6 * DAY).toISOString(), admin_note: '' }];
      dw.admin = [
        { id: 'r2', user_email: 'sam.parent@example.com', user_name: 'Sam Parent', topup_id: 't90', topup_amount_cents: 2000, amount_cents: 2000, balance_cents: 1703, status: 'pending', created_at: new Date(now - 3600e3).toISOString(), admin_note: '' },
        { id: 'r3', user_email: 'mia@example.com', user_name: 'Mia R.', topup_id: 't91', topup_amount_cents: 500, amount_cents: 500, balance_cents: 500, status: 'pending', created_at: new Date(now - 5 * 3600e3).toISOString(), admin_note: '' },
        { id: 'r1', user_email: 'alex@example.com', user_name: 'Alex Demo', topup_id: 't5', topup_amount_cents: 500, amount_cents: 500, balance_cents: 703, status: 'approved', created_at: new Date(now - 6 * DAY).toISOString(), admin_note: '' },
        { id: 'r0', user_email: 'jo@example.com', user_name: 'Jo K.', topup_id: 't70', topup_amount_cents: 1000, amount_cents: 1000, balance_cents: 1000, status: 'rejected', created_at: new Date(now - 9 * DAY).toISOString(), admin_note: 'Books were already made.' }
      ];
    }
  }
  if (DEMO) seedWallet();

  PC.demoBalance = () => dw.balance;
  /* Demo tiers: same shape as GET /api/wallet "tiers". */
  const DEMO_TIERS = [
    { id: 'professional', label: 'Professional', price_cents: 399, avg_cost_per_book_usd: 2.84, books_costed: 5 },
    { id: 'cost_efficient', label: 'Cost-Efficient', price_cents: 99, avg_cost_per_book_usd: 0.31, books_costed: 12 }
  ];
  PC.demoTier = (id) => DEMO_TIERS.filter((t) => t.id === (id || 'cost_efficient'))[0] || null;
  /* Demo helper for the book / hw1 modules: charge the tier's price, or report a 400 / 402. */
  PC.demoCharge = (description, bookId, tierId) => {
    const tier = PC.demoTier(tierId);
    if (!tier) return { status: 400, ok: false, data: { error: 'tier must be one of: professional, cost_efficient' } };
    const price = tier.price_cents;
    if (dw.balance < price) return { status: 402, ok: false, data: { error: 'Not enough balance. Add funds to make a book.', balance_cents: dw.balance, price_cents: price } };
    dw.balance -= price;
    dw.tx.unshift({ id: 't' + (++dw.seq), created_at: new Date().toISOString(), kind: 'charge', amount_cents: -price, balance_after_cents: dw.balance, description: description + ' (' + tier.label + ')', book_id: bookId || null, status: 'completed' });
    return null;
  };

  function walletDemo(method, url, body) {
    const R = (data, status) => ({ ok: !status || status < 400, status: status || 200, data });
    let m;
    if (url === '/api/me') {
      return R({ user: params.get('signedout') === '1' ? null : { id: 'u1', name: 'Alex Demo', email: 'alex@example.com', picture: null, is_admin: params.get('admin') === '1' } });
    }
    if (url === '/api/wallet') {
      return R({ balance_cents: dw.balance, currency: 'usd', book_price_cents: 99, default_tier: 'cost_efficient', tiers: DEMO_TIERS.map((t) => Object.assign({}, t)), presets_cents: [500, 1000, 2000], custom_min_cents: 100, custom_max_cents: 10000, avg_cost_per_book_usd: 1.09, books_costed: 17 });
    }
    if (url === '/api/wallet/checkout' && method === 'POST') {
      const c = body && body.amount_cents;
      if (!(c >= 100 && c <= 10000)) return R({ error: 'Amount must be between $1.00 and $100.00.' }, 400);
      return R({ checkout_url: PC.href('/wallet', { checkout: 'success', session_id: 'demo_' + c + '_' + Date.now() }) });
    }
    if (url === '/api/wallet/checkout/confirm' && method === 'POST') {
      const mm = /^demo_(\d+)_/.exec((body && body.session_id) || '');
      if (!mm) return R({ error: 'Unknown checkout session.' }, 400);
      if (dw.sessions[body.session_id]) return R({ credited: false, balance_cents: dw.balance });
      dw.sessions[body.session_id] = 1;
      const c = +mm[1];
      dw.balance += c;
      dw.tx.unshift({ id: 't' + (++dw.seq), created_at: new Date().toISOString(), kind: 'topup', amount_cents: c, balance_after_cents: dw.balance, description: 'Top-up ' + PC.money(c), book_id: null, status: 'completed' });
      return R({ credited: true, balance_cents: dw.balance });
    }
    if (url === '/api/wallet/transactions') return R(dw.tx.slice());
    if (url === '/api/wallet/refund-requests' && method === 'GET') return R(dw.refunds.slice().reverse());
    if (url === '/api/wallet/refund-requests' && method === 'POST') {
      const t = dw.tx.filter((x) => x.id === (body && body.topup_id))[0];
      if (!t || t.kind !== 'topup') return R({ error: 'That top-up cannot be refunded.' }, 400);
      if (t.status !== 'completed' || dw.refunds.some((r) => r.topup_id === t.id)) return R({ error: 'A refund was already requested for this top-up.' }, 409);
      const r = { id: 'r' + (++dw.seq), topup_id: t.id, status: 'pending', amount_cents: t.amount_cents, created_at: new Date().toISOString(), admin_note: '' };
      dw.refunds.push(r);
      t.status = 'refund_pending';
      dw.admin.unshift({ id: r.id, user_email: 'alex@example.com', user_name: 'Alex Demo', topup_id: t.id, topup_amount_cents: t.amount_cents, amount_cents: t.amount_cents, balance_cents: dw.balance, status: 'pending', created_at: r.created_at, admin_note: '' });
      return R({ id: r.id, status: 'pending', amount_cents: r.amount_cents }, 201);
    }
    if (/^\/api\/admin\//.test(url)) {
      if (params.get('admin') !== '1') return R({ error: 'Not found.' }, 404);
      if (url.indexOf('/api/admin/refund-requests') === 0 && method === 'GET') {
        const all = /status=all/.test(url);
        return R(dw.admin.filter((x) => all || x.status === 'pending'));
      }
      if ((m = /^\/api\/admin\/refund-requests\/([\w-]+)\/(approve|reject)$/.exec(url))) {
        const r = dw.admin.filter((x) => x.id === m[1])[0];
        if (!r || r.status !== 'pending') return R({ error: 'Request is not pending.' }, 409);
        r.status = m[2] === 'approve' ? 'approved' : 'rejected';
        r.admin_note = (body && body.note) || '';
        return R({ id: r.id, status: r.status });
      }
      if (url === '/api/admin/costs') {
        return R({
          price_usd: 0.99, books_done: 17, avg_cost_per_book_usd: 1.09, median_cost_per_book_usd: 0.34, total_api_cost_usd: 18.92, failed_calls: 3,
          tiers: [
            { id: 'professional', label: 'Professional', price_cents: 399, price_usd: 3.99, books_done: 5, avg_cost_per_book_usd: 2.84, median_cost_per_book_usd: 2.71, margin_per_book_usd: 1.15 },
            { id: 'cost_efficient', label: 'Cost-Efficient', price_cents: 99, price_usd: 0.99, books_done: 12, avg_cost_per_book_usd: 0.31, median_cost_per_book_usd: 0.28, margin_per_book_usd: 0.68 }
          ],
          by_kind: { hero: { calls: 14, cost_usd: 0.55 }, page: { calls: 262, cost_usd: 2.9 }, check: { calls: 281, cost_usd: 0.67 } },
          recent_books: [
            { book_id: 'b7', story: 'jack', tier: 'professional', created_at: new Date(Date.now() - 1800e3).toISOString(), cost_usd: 2.71, calls: 46, retries: 2 },
            { book_id: 'b6', story: 'jack', tier: 'cost_efficient', created_at: new Date(Date.now() - 3600e3).toISOString(), cost_usd: 0.34, calls: 52, retries: 3 },
            { book_id: 'b5', story: 'red', tier: 'cost_efficient', created_at: new Date(Date.now() - 20 * 3600e3).toISOString(), cost_usd: 0.29, calls: 47, retries: 1 },
            { book_id: 'b4', story: 'jack', tier: 'cost_efficient', created_at: new Date(Date.now() - 2 * DAY).toISOString(), cost_usd: 0.41, calls: 58, retries: 6 },
            { book_id: 'b3', story: 'red', tier: 'professional', created_at: new Date(Date.now() - 3 * DAY).toISOString(), cost_usd: 0.27, calls: 44, retries: 0 },
            { book_id: 'b2', story: 'jack', tier: 'cost_efficient', created_at: new Date(Date.now() - 5 * DAY).toISOString(), cost_usd: 1.12, calls: 71, retries: 14 }
          ]
        });
      }
    }
    return undefined;
  }

  function demoApi(method, url, body) {
    return new Promise((resolve) => {
      let r = walletDemo(method, url, body);
      for (let i = 0; r === undefined && i < demoHandlers.length; i++) r = demoHandlers[i](method, url, body);
      if (r === undefined) r = { ok: false, status: 404, data: { error: 'Not found.' } };
      setTimeout(() => resolve(r), 120);
    });
  }

  /* ---------- API ---------- */
  PC.api = (method, url, body) => {
    if (DEMO) return demoApi(method, url, body);
    const opts = { method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (body instanceof FormData) opts.body = body;
    else if (body != null) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
    return fetch(url, opts).then((r) =>
      r.json().catch(() => ({})).then((j) => ({ ok: r.ok, status: r.status, data: j })));
  };
  PC.openEvents = (url) => {
    if (DEMO || /^demo:/.test(url)) return PC.demoStream(url);
    return new EventSource(url);
  };

  /* ---------- header: auth, balance chip, admin link ---------- */
  const initials = (name) => {
    const p = String(name || '?').trim().split(/\s+/);
    return ((p[0] || '?')[0] + (p.length > 1 ? p[p.length - 1][0] : '')).toUpperCase();
  };
  const listeners = [];
  PC.onMe = (fn) => { listeners.push(fn); if (PC.me !== undefined) fn(PC.me); };

  function renderHeader() {
    const me = PC.me;
    const signedIn = !!me;
    $('signin-link').hidden = signedIn || me === undefined;
    $('auth-user').hidden = !signedIn;
    $('admin-link').hidden = !(me && me.is_admin);
    $('admin-link').href = PC.href('/admin');
    $('balance-chip').href = PC.href('/wallet');
    $('brand-link').href = PC.href('/');
    if (!signedIn) { $('balance-chip').hidden = true; return; }
    $('auth-name').textContent = me.name || me.email || 'You';
    $('avatar').title = me.name || me.email || '';
    const av = $('avatar');
    av.innerHTML = '';
    const ini = initials(me.name || me.email);
    if (me.picture) {
      const im = document.createElement('img');
      im.alt = '';
      im.referrerPolicy = 'no-referrer';
      im.onerror = () => { av.textContent = ini; };
      im.src = me.picture;
      av.appendChild(im);
    } else av.textContent = ini;
  }
  PC.setBalance = (cents) => {
    if (PC.wallet) PC.wallet.balance_cents = cents;
    const chip = $('balance-chip');
    if (typeof cents !== 'number') { chip.hidden = true; return; }
    chip.textContent = PC.money(cents);
    chip.setAttribute('aria-label', 'Wallet balance ' + PC.money(cents) + '. Open wallet.');
    chip.hidden = !PC.me;
    /* "low" = not enough for even the cheapest tier */
    const w = PC.wallet;
    let bookPrice = (w && typeof w.book_price_cents === 'number') ? w.book_price_cents : 99;
    if (w && Array.isArray(w.tiers)) w.tiers.forEach((t) => { if (typeof t.price_cents === 'number') bookPrice = Math.min(bookPrice, t.price_cents); });
    chip.classList.toggle('low', cents < bookPrice);
  };
  let walletP = null;
  PC.loadWallet = (force) => {
    if (!PC.me) return Promise.resolve(null);
    if (!walletP || force) {
      walletP = PC.api('GET', '/api/wallet').then((r) => {
        if (!r.ok || !r.data || typeof r.data.balance_cents !== 'number') return null;
        PC.wallet = r.data;
        PC.setBalance(r.data.balance_cents);
        return r.data;
      }).catch(() => null);
    }
    return walletP;
  };
  PC.loadMe = () => PC.api('GET', '/api/me').then((r) => {
    PC.me = r.ok && r.data && r.data.user ? r.data.user : null;
  }).catch(() => { PC.me = undefined; }).then(() => {
    renderHeader();
    const w = PC.me ? PC.loadWallet(true) : Promise.resolve(null);
    return w.then(() => { listeners.forEach((fn) => fn(PC.me)); return PC.me; });
  });
  $('signout-btn').addEventListener('click', () => {
    if (DEMO) {
      const q = new URLSearchParams(location.search);
      q.set('signedout', '1');
      location.href = location.pathname + '?' + q.toString();
      return;
    }
    fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => { }).then(() => { location.reload(); });
  });
  PC.meReady = PC.loadMe();

  /* ---------- modal dialog (confirm / note prompt) ---------- */
  PC.dialog = (o) => new Promise((resolve) => {
    const d = document.createElement('dialog');
    d.className = 'rpg-dialog';
    d.setAttribute('aria-labelledby', 'dlg-title');
    const box = el('div', 'rpg-box');
    const t = el('p', 'rpg-name', o.title || 'SYSTEM');
    t.id = 'dlg-title';
    box.appendChild(t);
    box.appendChild(el('p', 'rpg-text', o.text || ''));
    let input = null;
    if (o.input) {
      const lab = el('label', 'dlg-label', o.input.label || 'Note');
      input = document.createElement('textarea');
      input.className = 'txt';
      input.rows = 3;
      input.maxLength = 300;
      lab.appendChild(input);
      box.appendChild(lab);
    }
    const act = el('div', 'rpg-actions');
    const cancel = el('button', 'pill small ghost', o.cancel || 'CANCEL');
    cancel.type = 'button';
    const ok = el('button', 'pill small', o.ok || 'OK');
    ok.type = 'button';
    act.appendChild(ok);
    act.appendChild(cancel);
    box.appendChild(act);
    d.appendChild(box);
    let result = null;
    ok.addEventListener('click', () => { result = { value: input ? input.value.trim() : '' }; d.close(); });
    cancel.addEventListener('click', () => d.close());
    d.addEventListener('close', () => { d.remove(); resolve(result); });
    document.body.appendChild(d);
    if (d.showModal) d.showModal(); else d.setAttribute('open', '');
    (input || ok).focus();
  });

  /* ---------- tabs (roving tabindex, arrow keys) ---------- */
  PC.tabs = (items, onChange) => {
    // items: [{tab, panel}]
    function select(i, focus) {
      items.forEach((it, k) => {
        it.tab.setAttribute('aria-selected', String(k === i));
        it.tab.tabIndex = k === i ? 0 : -1;
        if (it.panel) it.panel.hidden = k !== i;
      });
      if (focus) items[i].tab.focus();
      if (onChange) onChange(i);
    }
    items.forEach((it, i) => {
      it.tab.addEventListener('click', () => select(i));
      it.tab.addEventListener('keydown', (e) => {
        let n = null;
        if (e.key === 'ArrowRight') n = (i + 1) % items.length;
        else if (e.key === 'ArrowLeft') n = (i + items.length - 1) % items.length;
        else if (e.key === 'Home') n = 0;
        else if (e.key === 'End') n = items.length - 1;
        if (n !== null) { e.preventDefault(); select(n, true); }
      });
    });
    return { select };
  };

  /* ---------- top-screen states + RPG error box ---------- */
  PC.show = (state) => {
    document.querySelectorAll('.state').forEach((s) => { s.hidden = s.dataset.state !== state; });
  };
  PC.showError = (text, opts) => {
    opts = opts || {};
    $('error-text').textContent = text;
    $('error-signin').hidden = !opts.signin;
    const funds = $('error-funds');
    if (funds) { funds.hidden = !opts.funds; funds.href = PC.href('/wallet'); }
    PC.show('error');
    $('error-dismiss').focus();
  };
  const dismiss = $('error-dismiss');
  if (dismiss) {
    dismiss.addEventListener('click', () => {
      PC.show('idle');
      const b = $('start-btn');
      if (b) b.focus();
    });
  }
  PC.SIGNIN_MSG = 'Please sign in to make pictures. It only takes a moment!';
  /* Shows 401 / 402 problems; returns true when handled. */
  PC.handleProblem = (r) => {
    if (r.status === 401) { PC.me = null; renderHeader(); PC.showError(PC.SIGNIN_MSG, { signin: true }); return true; }
    if (r.status === 402) {
      const d = r.data || {};
      const price = typeof d.price_cents === 'number' ? d.price_cents : 99;
      if (typeof d.balance_cents === 'number') PC.setBalance(d.balance_cents);
      PC.showError('Not enough funds. This costs ' + PC.money(price) + (typeof d.balance_cents === 'number' ? ' and your balance is ' + PC.money(d.balance_cents) : '') + '.', { funds: true });
      return true;
    }
    return false;
  };
})();
