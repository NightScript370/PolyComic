/* /hw1: unlisted Quick Scenes flow. */
(function () {
  'use strict';
  const PC = window.PC, $ = PC.$, el = PC.el, params = PC.params, DEMO = PC.DEMO;
  const form = $('hw1-form');
  if (!form) return;
  const F = PC.initForm();
  const btn = $('start-btn');
  let running = false;

  function placeholder(color, label) {
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="384"><rect width="512" height="384" fill="' + color +
      '"/><polygon points="256,60 400,150 400,260 256,330 112,260 112,150" fill="rgba(255,255,255,.35)"/>' +
      '<text x="256" y="200" font-family="monospace" font-size="28" text-anchor="middle" fill="#123">' + label + '</text></svg>';
    return 'data:image/svg+xml;base64,' + btoa(svg);
  }
  PC.demo((method, url, body) => {
    if (url !== '/api/hw1/generate') return undefined;
    const tier = (body && body.get && body.get('tier')) || 'cost_efficient';
    const bad = PC.demoCharge('Quick scenes run', null, tier);
    if (bad) return bad;
    return {
      ok: true, status: 200,
      data: {
        style: F.style(), detail: +F.detail.value, tier, est_cost_usd: tier === 'professional' ? 0.4 : 0.12,
        hero: { data_url: placeholder('#8cc8ff', 'HERO') },
        scenes: [
          { page: 1, title: 'The Quest Begins', caption: 'Wide shot: our hero stands at the edge of a sunlit meadow, holding a tiny wooden sword, a faint castle on the horizon.', data_url: placeholder('#b6e3a8', 'SCENE 1') },
          { page: 2, title: 'The Friendly Dragon', caption: 'Medium shot: the hero shares a picnic with a smiling green dragon beside a bubbling stream.', data_url: placeholder('#ffd9a0', 'SCENE 2') }
        ]
      }
    };
  });

  function setRunning(on) {
    running = on;
    btn.disabled = on;
    if (on) btn.setAttribute('aria-busy', 'true'); else btn.removeAttribute('aria-busy');
  }

  function renderPrice() {
    PC.renderTierPrices();
    const p = $('price-line');
    const w = PC.wallet;
    const price = PC.tierPrice();
    p.textContent = price === 0 ? 'Free right now' : PC.money(price) + ' per run';
    if (PC.me && w && typeof w.balance_cents === 'number') {
      p.appendChild(document.createTextNode(' · balance ' + PC.money(w.balance_cents) + ' '));
      if (price > 0 && w.balance_cents < price) {
        const a = el('a', 'price-add', 'ADD FUNDS');
        a.href = PC.href('/wallet');
        p.appendChild(a);
      }
    }
  }

  function renderResults(d) {
    $('hero-img').src = d.hero.data_url;
    $('result-meta').textContent = '(' + d.style + ', detail ' + d.detail + (d.tier ? ', ' + PC.tierLabel(d.tier).toLowerCase() : '') + ')';
    const list = $('scene-list');
    list.innerHTML = '';
    (d.scenes || []).forEach((s) => {
      const fig = el('figure', 'card');
      const img = document.createElement('img');
      img.src = s.data_url;
      img.alt = 'Page ' + s.page + ': ' + (s.title || 'Scene');
      const cap = el('figcaption');
      cap.appendChild(el('strong', null, 'Page ' + s.page + (s.title ? ' - ' + s.title : '')));
      cap.appendChild(el('p', null, s.caption || ''));
      fig.appendChild(img);
      fig.appendChild(cap);
      list.appendChild(fig);
    });
    $('cost-text').textContent = typeof d.est_cost_usd === 'number' ? 'Est. cost: $' + d.est_cost_usd.toFixed(2) : '';
    PC.show('results');
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (running) return;
    const err = F.check();
    if (err) { F.setMsg(err); return; }
    F.setMsg('');
    if (PC.me === null) { PC.showError(PC.SIGNIN_MSG, { signin: true }); return; }
    setRunning(true);
    PC.show('progress');
    const fd = new FormData();
    fd.append('photo', F.file());
    fd.append('style', F.style());
    fd.append('detail', F.detail.value);
    fd.append('tier', F.tier());
    PC.api('POST', '/api/hw1/generate', fd).then((r) => {
      setRunning(false);
      if (r.ok) { renderResults(r.data); PC.loadWallet(true).then(renderPrice); }
      else if (!PC.handleProblem(r)) PC.showError((r.data && r.data.error) || 'The server had a problem (' + r.status + ').');
    }).catch((x) => {
      setRunning(false);
      PC.showError(x && x.message ? x.message : 'Could not reach the server. Please try again.');
    });
  });

  renderPrice();
  PC.onMe(renderPrice);
  PC.onTierChange(renderPrice);
  const state = params.get('state');
  if (state === 'error') PC.showError('The hero machine hiccuped. Please try again!');
  if (state === 'signin') PC.showError(PC.SIGNIN_MSG, { signin: true });
  if (state === '402') PC.handleProblem({ status: 402, data: { balance_cents: 50, price_cents: 99 } });
  if (state === 'loading' || state === 'progress') PC.show('progress');
})();
