/* Shared photo / style / detail / quality-tier controls used by the story book and /hw1 forms. */
(function () {
  'use strict';
  const PC = window.PC, $ = PC.$, el = PC.el;

  /* ---------- quality tiers (prices from GET /api/wallet, else the server-rendered page) ---------- */
  const TIER_FALLBACK = { professional: { label: 'Professional', price_cents: 399 }, cost_efficient: { label: 'Cost-Efficient', price_cents: 99 } };
  PC.DEFAULT_TIER = 'cost_efficient';
  PC.selectedTier = () => (document.querySelector('input[name=tier]:checked') || {}).value || PC.DEFAULT_TIER;
  PC.tierInfo = (id) => {
    id = id || PC.selectedTier();
    const w = PC.wallet;
    const t = w && Array.isArray(w.tiers) ? w.tiers.filter((x) => x.id === id)[0] : null;
    if (t && typeof t.price_cents === 'number') return t;
    const fb = TIER_FALLBACK[id] || TIER_FALLBACK[PC.DEFAULT_TIER];
    const span = document.querySelector('[data-tier-price="' + id + '"]');
    const c = span && span.dataset.cents !== '' ? Number(span.dataset.cents) : NaN;
    return { id, label: fb.label, price_cents: isFinite(c) ? c : fb.price_cents };
  };
  PC.tierPrice = (id) => PC.tierInfo(id).price_cents;
  PC.tierLabel = (id) => ((TIER_FALLBACK[id] || {}).label || String(id || '')).toUpperCase();
  /* Refresh the price shown on each tier cartridge (after the wallet loads). */
  PC.renderTierPrices = () => {
    document.querySelectorAll('[data-tier-price]').forEach((s) => {
      const c = PC.tierPrice(s.dataset.tierPrice);
      s.textContent = c === 0 ? 'FREE' : PC.money(c);
    });
  };
  /* fn(tierId) runs whenever the buyer picks another tier. */
  PC.onTierChange = (fn) => {
    document.querySelectorAll('input[name=tier]').forEach((i) => i.addEventListener('change', () => fn(PC.selectedTier())));
  };
  const MAX_BYTES = 10 * 1024 * 1024;
  const FALLBACK_STYLES = [
    { id: 'n64', label: 'Polygon 64', description: 'Chunky polygons and flat-shaded colour.' },
    { id: 'ps1', label: 'Low-Poly 32', description: 'Wobbly low-poly look with pixel textures.' },
    { id: 'sprite16', label: '16-Bit Sprite', description: 'Crisp side-on pixel art.' }
  ];
  const DETAIL_NAMES = ['Chunky', 'Classic', 'Refined'];

  PC.initForm = () => {
    const photo = $('photo'), thumb = $('thumb'), drop = $('dropzone');
    const stylesEl = $('styles'), detail = $('detail'), msg = $('form-msg');
    let selectedStyle = null;

    const setMsg = (t) => { msg.textContent = t || ''; };

    function renderStyles(list) {
      stylesEl.innerHTML = '';
      selectedStyle = null;
      list.forEach((s, i) => {
        const label = el('label', 'cart');
        const input = document.createElement('input');
        input.type = 'radio';
        input.name = 'style';
        input.value = s.id;
        if (i === 0) { input.checked = true; selectedStyle = s.id; }
        input.addEventListener('change', () => { selectedStyle = s.id; });
        const body = el('span', 'cart-body');
        body.appendChild(el('span', 'cart-name', s.label));
        body.appendChild(el('span', 'cart-desc', s.description || ''));
        label.appendChild(input);
        label.appendChild(body);
        stylesEl.appendChild(label);
      });
    }
    renderStyles(FALLBACK_STYLES);
    fetch('/api/styles', { headers: { Accept: 'application/json' } })
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((list) => { if (Array.isArray(list) && list.length) renderStyles(list); })
      .catch(() => { /* keep fallback list */ });

    function updateDetail() {
      const n = DETAIL_NAMES[detail.value - 1];
      $('detail-name').textContent = n;
      detail.setAttribute('aria-valuetext', n);
    }
    detail.addEventListener('input', updateDetail);
    updateDetail();

    function validate(file) {
      if (!file) return 'Please choose a photo first.';
      if (!/^image\/(png|jpeg|webp)$/.test(file.type)) return 'That file is not a PNG, JPEG or WebP image.';
      if (file.size > MAX_BYTES) return 'That photo is larger than 10 MB.';
      return '';
    }
    photo.addEventListener('change', () => {
      const f = photo.files && photo.files[0];
      setMsg(f ? validate(f) : '');
      if (f && f.type.indexOf('image/') === 0) {
        thumb.src = URL.createObjectURL(f);
        thumb.hidden = false;
        drop.classList.add('has-file');
        $('photo-help').textContent = f.name;
      }
    });
    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove('over')));
    drop.addEventListener('drop', (e) => {
      e.preventDefault();
      const f = e.dataTransfer && e.dataTransfer.files[0];
      if (!f) return;
      try {
        const dt = new DataTransfer();
        dt.items.add(f);
        photo.files = dt.files;
      } catch (x) { return; }
      photo.dispatchEvent(new Event('change'));
    });

    return {
      photo, detail, setMsg,
      style: () => selectedStyle,
      tier: () => PC.selectedTier(),
      file: () => photo.files && photo.files[0],
      validate,
      /* returns an error string or '' */
      check() {
        const err = validate(this.file());
        if (err) return err;
        if (!$('consent').checked) return 'Please confirm you have permission to use this photo.';
        return '';
      }
    };
  };
})();
