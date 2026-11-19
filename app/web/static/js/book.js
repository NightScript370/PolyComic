/* Story book flow: form, live progress (SSE), viewer, MY BOOKS. */
(function () {
  'use strict';
  const PC = window.PC, $ = PC.$, el = PC.el, params = PC.params, DEMO = PC.DEMO;
  const form = $('hw1-form');
  if (!form) return;
  const F = PC.initForm();
  const btn = $('start-btn');
  const MAX_TRIES = 3;
  let running = false;

  /* ================= demo backend ================= */
  const TITLES = {
    jack: ['The Poor Farm', 'Selling the Cow', 'Magic Beans', 'Mum Is Cross', 'Overnight Sprout', 'The Giant Stalk', 'First Climb', 'Above the Clouds', "The Giant's Castle", 'A Tiny Hideaway', 'Fee Fi Fo Fum', 'The Golden Coins', 'The Singing Harp', 'The Golden Goose', 'A Sneaky Escape', 'Racing Down', 'The Axe', 'Timber!', 'Safe at Home', 'Happily Ever After'],
    red: ["Mum's Basket", "Red's Hood", 'Into the Woods', "A Bird's Warning", 'Meeting the Wolf', "Wolf's Question", 'The Short Path', 'Picking Flowers', 'Wolf at the Door', "Grandma's Bed", 'Big Eyes', 'Big Ears', 'Big Teeth', 'The Gulp', 'A Woodcutter', 'The Rescue', 'Safe at Last', 'Lesson Learned', 'Tea Together', 'The End']
  };
  const PDF_NAMES = { jack: 'JackAndTheBeanstalk.pdf', red: 'LittleRedRidingHood.pdf' };
  const COLORS = ['#8fd0ff', '#b9a6ff', '#ffd08a', '#ff9fb0', '#a8e6a1'];
  const ZONES = ['top', 'middle', 'bottom', 'bottom'];
  let demoBooks = [];
  let demoSeq = 0;
  let demoCtx = { name: 'Sam', story: 'jack' };
  const TRIES = { 3: 2, 7: 2, 16: 2, 12: 3 }; // demo: pages that needed retries

  function demoPage(i, name, story) { // i is the page number 1..20
    const t = TITLES[story || 'jack'][i - 1];
    return {
      page: i, title: t,
      text: t + ': ' + name + ' takes the next step of the adventure.',
      caption: 'Wide shot: scene ' + i + ' of the demo adventure.',
      zone: { zone: ZONES[i % 4], x: 0.05, y: 0.05, w: 0.9, h: 0.25 },
      thumb_url: PC.canvasImg(COLORS[i % 5], 'P' + i, 120, 120),
      image_url: PC.canvasImg(COLORS[i % 5], 'PAGE ' + i, 480, 480),
      attempts: TRIES[i] || 1,
      scores: { likeness: 0.9, fidelity: 0.94 }
    };
  }
  function demoDetail(b) {
    const d = Object.assign({}, b);
    d.hero_thumb_url = PC.canvasImg('#8cc8ff', 'HERO', 256, 256);
    d.hero_attempts = 1;
    d.hero_scores = { likeness: 0.88 };
    d.pages = [];
    for (let i = 1; i <= 20; i++) d.pages.push(demoPage(i, b.hero_name, b.story));
    return d;
  }
  function seedDemo() {
    const now = Date.now();
    demoBooks = [
      { book_id: 'demo-done', story: 'jack', tier: 'professional', title: 'Sam and the Beanstalk', hero_name: 'Sam', status: 'done', created_at: new Date(now - 86400000).toISOString(), cover_thumb_url: PC.canvasImg('#8fd0ff', 'SAM', 160, 160), pdf_url: '/files/JackAndTheBeanstalk.pdf', est_cost_usd: 0.42 },
      { book_id: 'demo-fail', story: 'red', tier: 'cost_efficient', title: 'Mia and the Wolf', hero_name: 'Mia', status: 'failed', created_at: new Date(now - 3 * 86400000).toISOString(), cover_thumb_url: null, pdf_url: null, est_cost_usd: 0.1 }
    ];
    if (params.get('state') === 'resume') {
      demoBooks.unshift({ book_id: 'demo-run', story: 'jack', tier: 'cost_efficient', title: 'Zoe and the Beanstalk', hero_name: 'Zoe', status: 'running', created_at: new Date(now - 60000).toISOString(), cover_thumb_url: null, pdf_url: null, est_cost_usd: 0 });
    }
    if (params.get('empty') === '1') demoBooks = [];
  }
  if (DEMO) seedDemo();

  PC.demoStream = function () {
    const listeners = {}, timers = [];
    let closed = false;
    const f = PC.SLOW ? 20 : 1;
    const name = demoCtx.name, story = demoCtx.story, total = 21;
    const emit = (type, data) => {
      if (closed) return;
      (listeners[type] || []).forEach((fn) => fn({ data: JSON.stringify(data) }));
    };
    const at = (ms, type, data) => { timers.push(setTimeout(() => emit(type, data), ms * f)); };
    at(100, 'stage', { stage: 'hero', label: 'Building hero', done: 0, total });
    at(150, 'attempt', { page: 0, attempt: 1, phase: 'generating' });
    at(600, 'attempt', { page: 0, attempt: 1, phase: 'checking' });
    at(1100, 'hero', { thumb_url: PC.canvasImg('#8cc8ff', 'HERO', 120, 120), attempts: 1, scores: { likeness: 0.88 } });
    at(1200, 'stage', { stage: 'pages', label: 'Painting pages', done: 1, total });
    let end = 0;
    for (let i = 1; i <= 20; i++) {
      const s = 1300 + (i - 1) * 180, t = TRIES[i] || 1;
      for (let a = 1; a <= t; a++) {
        const ta = s + (a - 1) * 300;
        at(ta, 'attempt', { page: i, attempt: a, phase: 'generating' });
        at(ta + 120, 'attempt', { page: i, attempt: a, phase: 'checking' });
        if (a < t) at(ta + 220, 'attempt', { page: i, attempt: a + 1, phase: 'retrying', likeness: 0.61, fidelity: 0.7 });
      }
      const pe = s + (t - 1) * 300 + 260;
      at(pe, 'page', demoPage(i, name, story));
      if (i === 1) at(pe + 40, 'page', demoPage(i, name, story)); // replayed duplicate
      end = Math.max(end, pe);
    }
    at(2400, 'stage', { stage: 'checking', label: 'Checking quality', done: 8, total });
    at(3600, 'stage', { stage: 'pages', label: 'Painting pages', done: 14, total });
    at(end + 100, 'stage', { stage: 'binding', label: 'Binding the book', done: 21, total });
    at(end + 900, 'stage', { stage: 'done', label: 'Done', done: 21, total });
    at(end + 1000, 'done', { pdf_url: '/files/' + PDF_NAMES[story] });
    return {
      addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
      close: () => { closed = true; timers.forEach(clearTimeout); },
      readyState: 1
    };
  };
  PC.demo((method, url, body) => {
    const R = (data, status) => ({ ok: !status || status < 400, status: status || 200, data });
    let m;
    if (url === '/api/books' && method === 'GET') return R(demoBooks.slice());
    if (url === '/api/books' && method === 'POST') {
      const nm = (body && body.get && body.get('name')) || 'Sam';
      const story = (body && body.get && body.get('story')) || 'jack';
      const tier = (body && body.get && body.get('tier')) || 'cost_efficient';
      const id = 'demo-new' + (++demoSeq);
      const title = nm + (story === 'red' ? ' and the Wolf' : ' and the Beanstalk');
      const bad = PC.demoCharge('Book: ' + title, id, tier);
      if (bad) return bad;
      demoCtx = { name: nm, story };
      demoBooks.unshift({ book_id: id, story, tier, title, hero_name: nm, status: 'running', created_at: new Date().toISOString(), cover_thumb_url: null, pdf_url: null, est_cost_usd: 0 });
      return R({ book_id: id, events_url: 'demo://' + id }, 202);
    }
    if ((m = /^\/api\/books\/([\w-]+)$/.exec(url))) {
      const found = demoBooks.filter((x) => x.book_id === m[1])[0];
      if (method === 'DELETE') { demoBooks = demoBooks.filter((x) => x !== found); return R({ ok: true }); }
      if (found) return R(demoDetail(found));
      return R({ error: 'Not found.' }, 404);
    }
    return undefined;
  });

  /* ================= price line (selected quality tier) ================= */
  function renderPrice() {
    PC.renderTierPrices();
    const p = $('price-line');
    p.textContent = '';
    const w = PC.wallet;
    const price = PC.tierPrice();
    p.appendChild(document.createTextNode(price === 0 ? 'This book is free right now' : 'This book costs ' + PC.money(price)));
    if (PC.me && w && typeof w.balance_cents === 'number') {
      p.appendChild(document.createTextNode(' · balance ' + PC.money(w.balance_cents)));
      if (price > 0 && w.balance_cents < price) {
        const a = el('a', 'price-add', 'ADD FUNDS');
        a.href = PC.href('/wallet');
        p.appendChild(document.createTextNode(' '));
        p.appendChild(a);
      }
    }
  }
  const refreshWallet = () => PC.loadWallet(true).then(renderPrice);
  PC.onTierChange(renderPrice);

  /* ================= misc state ================= */
  const BUSY_MSG = 'You already have a book being made. Watch it in MY BOOKS, or wait for it to finish.';
  function handleProblem(r) {
    if (PC.handleProblem(r)) return true;
    if (r.status === 409) { PC.showError(BUSY_MSG); loadBooks(); return true; }
    return false;
  }
  const setMsg = F.setMsg;

  /* ================= tabs ================= */
  let mode = 'book';
  const tabsUi = PC.tabs(
    [{ tab: $('tab-book'), panel: null }, { tab: $('tab-books'), panel: null }],
    (i) => {
      mode = i === 0 ? 'book' : 'books';
      form.hidden = mode === 'books';
      $('books-panel').hidden = mode !== 'books';
      setMsg('');
      if (mode === 'books') loadBooks();
    }
  );

  const about = $('about');
  about.addEventListener('input', () => { $('about-count').textContent = about.value.length + ' / 500'; });

  /* ================= viewer ================= */
  let pages = [], cur = 0;
  const scrub = $('scrub');
  function showPage(i, announce) {
    if (!pages.length) return;
    cur = Math.max(0, Math.min(pages.length - 1, i));
    const p = pages[cur];
    const img = $('page-img');
    img.src = p.image_url || p.thumb_url || '';
    img.alt = 'Page ' + p.page + ' art' + (p.title ? ': ' + p.title : '');
    const z = p.zone && typeof p.zone === 'object' ? p.zone.zone : p.zone;
    const zone = ['top', 'middle', 'bottom'].indexOf(z) >= 0 ? z : 'bottom';
    const portrait = $('page-portrait');
    portrait.hidden = !p.portrait_url;
    if (p.portrait_url) {
      portrait.src = p.portrait_url;
      portrait.alt = p.speaker ? 'Portrait of ' + p.speaker : 'Speaker portrait';
    } else {
      portrait.removeAttribute('src');
      portrait.alt = '';
    }
    $('page-text').className = 'page-text zone-' + zone + (p.portrait_url ? ' has-portrait' : '');
    // Mixed narration + speech: spoken runs arrive already wrapped in quotation marks and
    // get the highlight colour (quotes + colour, never colour alone). textContent only.
    const para = $('page-text-p');
    para.textContent = '';
    if (Array.isArray(p.speech) && p.speech.length) {
      p.speech.forEach((run) => {
        const t = String((run && run[0]) || '');
        if (run && run[1]) {
          const s = document.createElement('span');
          s.className = 'spoken';
          s.textContent = t;
          para.appendChild(s);
        } else {
          para.appendChild(document.createTextNode(t));
        }
      });
    } else {
      para.textContent = p.text || '';
    }
    const tab = $('page-tab');
    tab.textContent = p.speaker || '';
    tab.hidden = !p.speaker;
    $('page-title').textContent = 'Page ' + p.page + (p.title ? ' - ' + p.title : '');
    setTries($('page-tries'), p.attempts, p.scores);
    $('page-caption').textContent = p.caption || '';
    $('prev-btn').disabled = cur === 0;
    $('next-btn').disabled = cur === pages.length - 1;
    $('pager-count').textContent = (cur + 1) + ' / ' + pages.length;
    scrub.value = String(cur + 1);
    scrub.setAttribute('aria-valuetext', 'Page ' + (cur + 1) + ' of ' + pages.length);
    if (announce) $('page-status').textContent = 'Page ' + p.page + ' of ' + pages.length + (p.title ? ', ' + p.title : '');
  }
  $('prev-btn').addEventListener('click', () => showPage(cur - 1, true));
  $('next-btn').addEventListener('click', () => showPage(cur + 1, true));
  scrub.addEventListener('input', () => showPage(+scrub.value - 1, false));
  scrub.addEventListener('change', () => showPage(+scrub.value - 1, true));
  document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || $('state-book').hidden) return;
    const t = e.target;
    if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || (t.closest && t.closest('.tabbar')))) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); showPage(cur - 1, true); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); showPage(cur + 1, true); }
  });

  function setTries(node, n, scores) {
    if (!node) return;
    node.hidden = !n;
    if (!n) return;
    const word = n + (n === 1 ? ' attempt' : ' attempts');
    node.textContent = '\u00d7' + n + (n === 1 ? ' try' : ' tries');
    node.className = 'tries' + (n > 1 ? ' multi' : '');
    node.title = word + (scores ? '\n' + scoreText(scores) : '');
    node.setAttribute('aria-label', word + (scores ? ', ' + scoreText(scores).replace(/\n/g, '; ') : ''));
  }
  // The page text box wears the book's art-style look (see .page-text.look-* in app.css).
  function setBoxStyle(preset) {
    $('page-frame').className = 'page-frame' + (/^(n64|ps1|sprite16)$/.test(preset) ? ' look-' + preset : '');
  }
  function renderBook(d) {
    pages = (d.pages || []).slice().sort((a, b) => a.page - b.page);
    setBoxStyle(d.preset || '');
    const hi = $('book-hero-img');
    hi.src = d.hero_thumb_url || '';
    hi.parentNode.hidden = !d.hero_thumb_url;
    setTries($('hero-tries'), d.hero_attempts, d.hero_scores);
    scrub.max = String(Math.max(1, pages.length));
    scrub.hidden = pages.length < 2;
    const pdf = $('pdf-btn');
    if (d.pdf_url) {
      pdf.href = d.pdf_url;
      const name = PC.pdfName(d.pdf_url);
      if (/\.pdf$/i.test(name)) pdf.setAttribute('download', name); else pdf.removeAttribute('download');
      pdf.title = name || '';
      pdf.hidden = false;
    } else pdf.hidden = true;
    $('book-cost').textContent = typeof d.est_cost_usd === 'number' && d.est_cost_usd > 0 ? 'Est. cost: $' + d.est_cost_usd.toFixed(2) : '';
    showPage(0, false);
    PC.show('book');
    $('viewer').focus();
  }
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href^="/files/"]');
    if (a && DEMO) { e.preventDefault(); PC.dialog({ title: 'DEMO', text: 'In the real app this downloads ' + PC.pdfName(a.getAttribute('href')) + '.', ok: 'OK', cancel: 'CLOSE' }); }
  });

  /* ================= live progress (SSE) ================= */
  let stream = null;
  let prog = null; // {id, total, stageDone, pages, hero}
  const slotsEl = $('slots');
  const slotInfo = []; // per slot: {scores}

  function closeStream() { if (stream) { try { stream.close(); } catch (x) { } stream = null; } }
  function setRunning(on) {
    running = on;
    btn.disabled = on;
    if (on) btn.setAttribute('aria-busy', 'true'); else btn.removeAttribute('aria-busy');
  }
  const slotName = (i) => (i === 0 ? 'Hero' : 'Page ' + i);
  function ensureSlots(n) {
    while (slotsEl.children.length < n) {
      const i = slotsEl.children.length;
      const li = el('li', 'slot');
      li.setAttribute('aria-label', slotName(i) + ': waiting');
      const ld = el('span', 'slot-load');
      ld.setAttribute('aria-hidden', 'true');
      for (let k = 0; k < 3; k++) ld.appendChild(document.createElement('i'));
      li.appendChild(ld);
      li.appendChild(el('span', 'tag', i === 0 ? 'H' : String(i)));
      slotsEl.appendChild(li);
      slotInfo[i] = {};
    }
  }
  const fmtScore = (v) => (typeof v === 'number' ? (Math.round(v * 100) / 100).toString() : String(v));
  function scoreText(sc) {
    if (!sc) return '';
    if (Array.isArray(sc)) return sc.map((s, i) => 'Try ' + (i + 1) + ': ' + scoreText(s)).join('\n');
    if (typeof sc === 'object') return Object.keys(sc).map((k) => k.charAt(0).toUpperCase() + k.slice(1) + ' ' + fmtScore(sc[k])).join(', ');
    return fmtScore(sc);
  }
  function setAttempt(d) {
    if (typeof d.page !== 'number') return;
    ensureSlots(d.page + 1);
    const li = slotsEl.children[d.page];
    if (li.classList.contains('filled')) return;
    const info = slotInfo[d.page] || (slotInfo[d.page] = {});
    if (d.likeness != null || d.fidelity != null) {
      info.scores = Object.assign({}, info.scores, d.likeness != null ? { likeness: d.likeness } : {}, d.fidelity != null ? { fidelity: d.fidelity } : {});
    }
    const a = d.attempt || 1;
    const long = d.phase === 'checking' ? 'checking' : d.phase === 'retrying' ? 'retry ' + a + '/' + MAX_TRIES : a + '/' + MAX_TRIES + ' paint';
    const short = d.phase === 'checking' ? 'chk' : d.phase === 'retrying' ? 'r' + a : a + '/' + MAX_TRIES;
    let st = li.querySelector('.st');
    if (!st) { st = el('span', 'st'); st.appendChild(el('span', 's')); st.appendChild(el('span', 'w')); li.appendChild(st); }
    st.className = 'st ' + (d.phase || 'generating');
    st.firstChild.textContent = short;
    st.lastChild.textContent = long;
    li.dataset.phase = d.phase || 'generating';
    li.setAttribute('aria-label', slotName(d.page) + ': ' + long);
    li.title = slotName(d.page) + ': ' + long + (info.scores ? '\n' + scoreText(info.scores) : '');
    const now = $('prog-now');
    now.removeAttribute('aria-hidden');
    now.textContent = slotName(d.page) + ' · ' + (d.phase === 'checking' ? 'checking quality' : d.phase === 'retrying' ? 'retry ' + a + ' of ' + MAX_TRIES : 'painting, try ' + a + ' of ' + MAX_TRIES);
  }
  function fillSlot(idx, url, label, attempts, scores) {
    ensureSlots(idx + 1);
    const li = slotsEl.children[idx];
    const already = li.classList.contains('filled');
    const info = slotInfo[idx] || (slotInfo[idx] = {});
    const sc = scores || info.scores;
    li.classList.add('filled');
    li.classList.remove('busy');
    delete li.dataset.phase;
    if (!already) li.classList.add('pop');
    li.innerHTML = '';
    const im = document.createElement('img');
    im.alt = label;
    im.src = url || '';
    li.appendChild(im);
    li.appendChild(el('span', 'tag', idx === 0 ? 'H' : String(idx)));
    const tip = attempts ? attempts + (attempts === 1 ? ' attempt' : ' attempts') + (sc ? '\n' + scoreText(sc) : '') : '';
    if (attempts) {
      const bd = el('span', 'badge' + (attempts > 1 ? ' multi' : ''), '×' + attempts);
      bd.setAttribute('aria-hidden', 'true');
      li.appendChild(bd);
    }
    li.title = label + (tip ? '\n' + tip : '');
    li.setAttribute('aria-label', label + (attempts ? ', ' + attempts + (attempts === 1 ? ' attempt' : ' attempts') : ''));
    updateBar();
  }
  const filledCount = () => slotsEl.querySelectorAll('.slot.filled').length;
  function updateBar(stageDone) {
    if (!prog) return;
    if (typeof stageDone === 'number') prog.stageDone = stageDone;
    const total = Math.max(prog.total || 0, slotsEl.children.length, 1);
    const done = Math.min(total, Math.max(prog.stageDone || 0, filledCount()));
    $('prog-fill').style.width = Math.round(done / total * 100) + '%';
    const bar = $('prog-bar');
    bar.setAttribute('aria-valuemax', String(total));
    bar.setAttribute('aria-valuenow', String(done));
    $('prog-count').textContent = done + ' / ' + (prog.total ? total : '?') + ' pictures';
  }
  const STAGE_LABELS = { hero: 'Building hero', pages: 'Painting pages', checking: 'Checking quality', binding: 'Binding the book', done: 'Done' };
  function setStage(d) {
    const label = d.label || STAGE_LABELS[d.stage] || '';
    if (label) $('stage-text').textContent = label + (d.stage === 'done' ? '' : '…');
    if (typeof d.total === 'number' && d.total > 0) { prog.total = d.total; ensureSlots(d.total); }
    updateBar(typeof d.done === 'number' ? d.done : undefined);
  }
  const parse = (e) => { try { return JSON.parse(e.data); } catch (x) { return null; } };

  function resetProgress() {
    slotsEl.innerHTML = '';
    slotInfo.length = 0;
    $('prog-now').textContent = ' ';
    $('prog-now').setAttribute('aria-hidden', 'true');
  }
  function failBook(message) {
    closeStream();
    setRunning(false);
    prog = null;
    PC.showError(message || 'The book machine hiccuped. Please try again!');
    loadBooks();
    refreshWallet();
  }
  function watch(bookId, eventsUrl) {
    closeStream();
    prog = { id: bookId, total: 0, stageDone: 0, pages: {}, hero: null };
    resetProgress();
    ensureSlots(1);
    $('stage-text').textContent = 'Building hero…';
    updateBar(0);
    PC.show('progress');
    setRunning(true);
    const es = stream = PC.openEvents(eventsUrl || '/api/books/' + encodeURIComponent(bookId) + '/events');
    es.addEventListener('stage', (e) => { const d = parse(e); if (d && prog) setStage(d); });
    es.addEventListener('attempt', (e) => { const d = parse(e); if (d && prog) setAttempt(d); });
    es.addEventListener('hero', (e) => {
      const d = parse(e); if (!d || !prog) return;
      prog.hero = d.thumb_url; prog.heroAttempts = d.attempts; prog.heroScores = d.scores;
      fillSlot(0, d.thumb_url, 'Your hero', d.attempts, d.scores);
    });
    es.addEventListener('page', (e) => {
      const d = parse(e); if (!d || !prog || !d.page) return;
      prog.pages[d.page] = d; // keyed by page number: replays are idempotent
      fillSlot(d.page, d.thumb_url, 'Page ' + d.page + (d.title ? ': ' + d.title : ''), d.attempts, d.scores);
    });
    es.addEventListener('done', (e) => {
      const d = parse(e) || {};
      if (!prog) return;
      const finished = prog;
      closeStream();
      setRunning(false);
      prog = null;
      $('stage-text').textContent = 'Done';
      const list = Object.keys(finished.pages).map((k) => finished.pages[k]);
      renderBook({ preset: d.preset, pages: list, hero_thumb_url: finished.hero, hero_attempts: finished.heroAttempts, hero_scores: finished.heroScores, pdf_url: d.pdf_url });
      $('page-status').textContent = 'Your book is ready.';
      loadBooks();
      refreshWallet();
    });
    es.addEventListener('error', (e) => {
      if (e && typeof e.data === 'string' && e.data) {
        const d = parse(e);
        failBook(d && d.message);
      } else if (es.readyState === 2) {
        failBook('Lost the connection to the book machine. Check MY BOOKS in a moment.');
      } else {
        $('stage-text').textContent = 'Reconnecting…';
      }
    });
  }

  /* ================= MY BOOKS ================= */
  const listEl = $('book-list'), emptyEl = $('books-empty');
  function renderBooks(list) {
    listEl.innerHTML = '';
    emptyEl.hidden = true;
    if (!list.length) {
      emptyEl.textContent = 'No books yet. Switch to STORY BOOK, add a photo and press start to make your first one!';
      emptyEl.hidden = false;
      return;
    }
    list.forEach((b) => {
      const li = el('li', 'bk');
      if (b.cover_thumb_url) {
        const im = document.createElement('img');
        im.className = 'bk-cover'; im.alt = 'Cover of ' + (b.title || 'book'); im.src = b.cover_thumb_url;
        li.appendChild(im);
      } else {
        const ph = el('div', 'bk-cover', b.status === 'failed' ? 'X' : '...');
        ph.setAttribute('aria-hidden', 'true');
        li.appendChild(ph);
      }
      const info = el('div', 'bk-info');
      info.appendChild(el('p', 'bk-title', b.title || 'Untitled book'));
      info.appendChild(el('p', 'bk-meta', [b.hero_name ? 'Starring ' + b.hero_name : '', PC.fmtDate(b.created_at)].filter(Boolean).join(' · ')));
      const st = b.status;
      info.appendChild(el('span', 'chip ' + st, { done: 'DONE', running: 'MAKING', queued: 'QUEUED', failed: 'FAILED' }[st] || String(st).toUpperCase()));
      if (b.tier) {
        const tc = el('span', 'chip tier ' + (b.tier === 'professional' ? 'professional' : 'cost-efficient'), PC.tierLabel(b.tier));
        tc.setAttribute('aria-label', 'Quality: ' + PC.tierLabel(b.tier).toLowerCase());
        info.appendChild(tc);
      }
      li.appendChild(info);
      if (b.status === 'failed') li.appendChild(el('p', 'bk-note', 'This book did not finish. What you paid for it is put back in your wallet. You can delete it and try again.'));
      const act = el('div', 'bk-actions');
      if (b.status === 'done') {
        const open = el('button', 'pill small', 'OPEN');
        open.type = 'button';
        open.setAttribute('aria-label', 'Open ' + (b.title || 'book'));
        open.addEventListener('click', () => openBook(b.book_id));
        act.appendChild(open);
        if (b.pdf_url) {
          const a = el('a', 'pill small ghost', 'PDF');
          a.href = b.pdf_url;
          const name = PC.pdfName(b.pdf_url);
          if (/\.pdf$/i.test(name)) a.setAttribute('download', name);
          a.setAttribute('aria-label', 'Download PDF of ' + (b.title || 'book'));
          act.appendChild(a);
        }
      } else if (b.status === 'running' || b.status === 'queued') {
        const w = el('button', 'pill small', 'WATCH');
        w.type = 'button';
        w.setAttribute('aria-label', 'Watch ' + (b.title || 'book') + ' being made');
        w.addEventListener('click', () => watch(b.book_id, DEMO ? 'demo://' + b.book_id : null));
        act.appendChild(w);
      }
      if (b.status === 'done' || b.status === 'failed') {
        const del = el('button', 'pill small ghost', 'DELETE');
        del.type = 'button';
        del.setAttribute('aria-label', 'Delete ' + (b.title || 'book'));
        del.addEventListener('click', () => deleteBook(b));
        act.appendChild(del);
      }
      li.appendChild(act);
      listEl.appendChild(li);
    });
  }
  function loadBooks() {
    if (PC.me === null) {
      listEl.innerHTML = '';
      emptyEl.innerHTML = '';
      emptyEl.appendChild(document.createTextNode('Sign in to see your books. '));
      const a = el('a', null, 'Sign in with Google');
      a.href = '/auth/login';
      emptyEl.appendChild(a);
      emptyEl.hidden = false;
      return Promise.resolve([]);
    }
    return PC.api('GET', '/api/books').then((r) => {
      if (r.status === 401) { PC.me = null; return loadBooks(); }
      if (!r.ok || !Array.isArray(r.data)) throw new Error('bad');
      renderBooks(r.data);
      return r.data;
    }).catch(() => {
      listEl.innerHTML = '';
      emptyEl.textContent = 'Could not load your books right now. Please try again in a moment.';
      emptyEl.hidden = false;
      return [];
    });
  }
  function openBook(id) {
    return PC.api('GET', '/api/books/' + encodeURIComponent(id)).then((r) => {
      if (!r.ok) { if (!handleProblem(r)) PC.showError((r.data && r.data.error) || 'Could not open that book.'); return; }
      renderBook(r.data);
    }).catch(() => PC.showError('Could not reach the server. Please try again.'));
  }
  function deleteBook(b) {
    PC.dialog({ title: 'DELETE BOOK', text: 'Delete "' + (b.title || 'this book') + '"? This cannot be undone.', ok: 'DELETE', cancel: 'KEEP' }).then((res) => {
      if (!res) return;
      return PC.api('DELETE', '/api/books/' + encodeURIComponent(b.book_id)).then((r) => {
        if (!r.ok && !handleProblem(r)) PC.showError((r.data && r.data.error) || 'Could not delete that book.');
        return loadBooks();
      }).catch(() => PC.showError('Could not reach the server. Please try again.'));
    });
  }

  /* ================= resume offer ================= */
  let resumeBook = null, resumeDismissed = false;
  function checkResume() {
    if (resumeDismissed || running) return;
    PC.api('GET', '/api/books').then((r) => {
      if (!r.ok || !Array.isArray(r.data)) return;
      const b = r.data.filter((x) => x.status === 'running' || x.status === 'queued')[0];
      if (!b) return;
      resumeBook = b;
      $('resume-text').textContent = '"' + (b.title || 'Your book') + '" is still being made. Watch it?';
      $('resume-offer').hidden = false;
    }).catch(() => { });
  }
  $('resume-yes').addEventListener('click', () => {
    $('resume-offer').hidden = true;
    resumeDismissed = true;
    if (resumeBook) watch(resumeBook.book_id, DEMO ? 'demo://' + resumeBook.book_id : null);
  });
  $('resume-no').addEventListener('click', () => {
    $('resume-offer').hidden = true;
    resumeDismissed = true;
    btn.focus();
  });

  /* ================= submit ================= */
  function startBook(f, nm) {
    const bf = new FormData();
    bf.append('photo', f);
    bf.append('style', F.style());
    bf.append('detail', F.detail.value);
    bf.append('tier', F.tier());
    bf.append('story', (form.querySelector('input[name=story]:checked') || {}).value || 'jack');
    bf.append('name', nm);
    bf.append('pronouns', $('pronouns').value);
    bf.append('age', $('age').value);
    bf.append('fav_color', $('fav-color').value.trim());
    bf.append('fav_food', $('fav-food').value.trim());
    bf.append('fav_game', $('fav-game').value.trim());
    bf.append('fav_animal', $('fav-animal').value.trim());
    bf.append('about', about.value.trim());
    setRunning(true);
    return PC.api('POST', '/api/books', bf).then((r) => {
      if (r.status === 202 || (r.ok && r.data && r.data.book_id)) {
        $('resume-offer').hidden = true;
        resumeDismissed = true;
        watch(r.data.book_id, r.data.events_url);
        loadBooks();
        refreshWallet();
      } else {
        setRunning(false);
        if (!handleProblem(r)) PC.showError((r.data && r.data.error) || 'The server had a problem (' + r.status + ').');
      }
    });
  }
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (running) return;
    const nm = $('hero-name').value.trim();
    let err = F.check();
    if (!err && (!nm || nm.length > 40)) { err = 'Please enter a hero name (1-40 characters).'; $('hero-name').focus(); }
    if (!err && $('age').value !== '') {
      const ag = +$('age').value;
      if (!(ag >= 1 && ag <= 17 && Math.floor(ag) === ag)) { err = 'Age must be a whole number from 1 to 17.'; $('age').focus(); }
    }
    if (err) { setMsg(err); return; }
    setMsg('');
    if (PC.me === null) { PC.showError(PC.SIGNIN_MSG, { signin: true }); return; }
    startBook(F.file(), nm).catch((x) => {
      prog = null;
      setRunning(false);
      PC.showError(x && x.message ? x.message : 'Could not reach the server. Please try again.');
    });
  });

  /* ================= boot ================= */
  tabsUi.select(params.get('mode') === 'books' ? 1 : 0);
  renderPrice();
  PC.onMe((me) => {
    renderPrice();
    if (mode === 'books') loadBooks();
    if (me) {
      checkResume();
      const open = params.get('open');
      if (open) openBook(open);
    }
  });

  const state = params.get('state');
  if (state === 'error') PC.showError('The hero machine hiccuped. Please try again!');
  if (state === 'signin') PC.showError(PC.SIGNIN_MSG, { signin: true });
  if (state === '402') PC.handleProblem({ status: 402, data: { balance_cents: 50, price_cents: 99 } });
  if (state === 'progress' || state === 'loading') {
    prog = { total: 21, stageDone: 0 };
    resetProgress();
    ensureSlots(21);
    const c = (col, l) => PC.canvasImg(col, l, 120, 120);
    fillSlot(0, c('#8cc8ff', 'HERO'), 'Your hero', 1, { likeness: 0.88 });
    for (let i = 1; i <= 8; i++) fillSlot(i, c(COLORS[i % 5], 'P' + i), 'Page ' + i, i === 3 ? 2 : i === 7 ? 3 : 1, { likeness: 0.9, fidelity: 0.93 });
    setAttempt({ page: 9, attempt: 1, phase: 'checking' });
    setAttempt({ page: 10, attempt: 1, phase: 'generating' });
    setAttempt({ page: 11, attempt: 2, phase: 'retrying', likeness: 0.6, fidelity: 0.7 });
    setAttempt({ page: 12, attempt: 3, phase: 'generating' });
    $('stage-text').textContent = 'Checking quality…';
    updateBar(9);
    PC.show('progress');
  }
})();
