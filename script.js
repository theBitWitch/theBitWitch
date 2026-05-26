/* ─── Dark Mode Toggle ─────────────────────────────────────────── */
(function setupTheme() {
  const html   = document.documentElement;
  const btn    = document.getElementById('theme-btn');
  const icon   = document.getElementById('theme-icon');
  const DARK   = 'dark';
  const STORE  = 'theme';

  // Restore saved preference (or default to light)
  const saved = localStorage.getItem(STORE);
  if (saved === DARK) html.classList.add(DARK);
  icon.textContent = html.classList.contains(DARK) ? '☀️' : '🌙';

  btn.addEventListener('click', function () {
    const isDark = html.classList.toggle(DARK);
    localStorage.setItem(STORE, isDark ? DARK : 'light');

    // Animate icon flip
    btn.classList.add('switching');
    setTimeout(function () {
      icon.textContent = isDark ? '☀️' : '🌙';
      btn.setAttribute('aria-label', isDark ? 'Lightmode umschalten' : 'Darkmode umschalten');
      btn.classList.remove('switching');
    }, 200);
  });
})();

/* ─── Floating sparkles ───────────────────────────────────────── */
(function () {
  const chars  = ['★', '✦', '♥', '✿', '·', '◆', '✧'];
  const colors = ['#FFB3C6', '#E8648A', '#8BAD79', '#C9DDB5', '#5D7A50'];
  const wrap   = document.getElementById('sparkles');
  const COUNT  = 22;

  for (let i = 0; i < COUNT; i++) {
    const el = document.createElement('span');
    el.className   = 'sparkle';
    el.textContent = chars[Math.floor(Math.random() * chars.length)];
    el.style.left             = `${Math.random() * 100}vw`;
    el.style.top              = `${80 + Math.random() * 40}vh`;
    el.style.color            = colors[Math.floor(Math.random() * colors.length)];
    el.style.fontSize         = `${6 + Math.random() * 10}px`;
    el.style.animationDuration = `${8 + Math.random() * 18}s`;
    el.style.animationDelay   = `-${Math.random() * 20}s`;
    wrap.appendChild(el);
  }
})();

/* ─── Lost Counter ─────────────────────────────────────────────── */
(function fetchLostCount() {
  const el = document.getElementById('lost-count');

  fetch('./lost.JSON', { cache: 'no-store' })
    .then(function (res) {
      if (!res.ok) throw new Error('Fetch failed');
      return res.json();
    })
    .then(function (data) {
      // Supports plain number (61) and object shapes ({ count: N } / { lost: N })
      const num = (typeof data === 'number') ? data : (data.count ?? data.lost ?? '?');
      el.textContent = num;
    })
    .catch(function () {
      el.textContent = '?';
    });
})();

/* ─── QR Code Modal ────────────────────────────────────────────── */
(function setupQR() {
  const modal     = document.getElementById('qr-modal');
  const closeBtn  = document.getElementById('modal-close-btn');
  const avatarBtn = document.getElementById('avatar-btn');

  function openModal() {
    modal.classList.add('open');
    closeBtn.focus();
    document.body.style.overflow = 'hidden';
  }

  function closeModal() {
    modal.classList.remove('open');
    document.body.style.overflow = '';
    avatarBtn.focus();
  }

  avatarBtn.addEventListener('click', openModal);
  closeBtn.addEventListener('click', closeModal);

  modal.addEventListener('click', function (e) {
    if (e.target === modal) closeModal();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && modal.classList.contains('open')) closeModal();
  });
})();
