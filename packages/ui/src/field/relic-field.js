/* Relic field — traditional sleeve filler (dots + stars).

   GEOMETRY (locked, unchanged): one hexagonal lattice carries every
   mark, so each point sits the same distance (PITCH) from all six
   neighbours. Stars occupy the SAME lattice scaled ×STAR_STEP — star
   spacing is uniform and a star's point is never also a dot's point, so
   overlap is geometrically impossible. No positional jitter; rotation is
   the only per-star variance (seeded, stable across loads).

   MOTION — the original: two crossing sines move OPACITY ONLY. Marks
   never move or resize, and stars take exactly the same alpha as dots
   (no shimmer, no twinkle, no per-mark life). Wide wavelengths, one
   continuous drift, no passes or intervals.

   Color: --field-rgb (marks), optional --field-star-rgb (stars only),
   --field-a0 (trough) / --field-a1 (crest), read from the host element.
   Reduced motion renders one static frame.

   Usage: RelicField.start(canvasEl, hostEl) → returns a kill function. */
(function () {
  const PITCH = 30;
  const STAR_STEP = 3;
  const DOT_R = 1.75;
  const ARMS = 6;
  const STAR_R = 11;
  const ROW = Math.sqrt(3) / 2;

  const rnd = (i) => {
    let h = (i * 374761393 + 668265263) | 0;
    h = (h ^ (h >>> 13)) * 1274126177 | 0;
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  };

  function star(ctx, x, y, r, rot) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(rot);
    ctx.lineWidth = Math.max(1, r * 0.2);
    ctx.lineCap = 'round';
    ctx.beginPath();
    for (let i = 0; i < ARMS; i++) {
      const a = i * (Math.PI * 2 / ARMS);
      ctx.moveTo(0, 0);
      ctx.lineTo(Math.cos(a) * r, Math.sin(a) * r);
    }
    ctx.stroke();
    ctx.restore();
  }

  function start(canvas, host) {
    if (!canvas || canvas.__relicField) return;
    canvas.__relicField = true;
    const ctx = canvas.getContext('2d');
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const DPR = Math.min(devicePixelRatio || 1, 2);
    let W = 0, H = 0, t = 0, raf = 0, dead = false;

    const scoped = host && host.nodeType === 1 && host !== document.documentElement && host !== document.body;
    const size = () => {
      const w = scoped && host.clientWidth ? host.clientWidth : innerWidth;
      const h = scoped && host.clientHeight ? host.clientHeight : innerHeight;
      W = canvas.width = w * DPR;
      H = canvas.height = h * DPR;
      canvas.style.width = w + 'px';
      canvas.style.height = h + 'px';
    };
    addEventListener('resize', size);
    if (scoped && window.ResizeObserver) new ResizeObserver(size).observe(host);
    size();

    function draw() {
      if (dead) return;
      const cs = getComputedStyle(host || canvas.parentElement || document.documentElement);
      const rgb = (cs.getPropertyValue('--field-rgb') || '226,218,194').trim();
      const srgb = (cs.getPropertyValue('--field-star-rgb') || '').trim() || rgb;
      const a0 = parseFloat(cs.getPropertyValue('--field-a0')) || 0.018;
      const a1 = parseFloat(cs.getPropertyValue('--field-a1')) || 0.115;

      // the original two crossing sines
      const alphaAt = (x, y) => {
        const w =
          Math.sin(x * 0.0014 / DPR + t * 0.3) +
          Math.sin((x * 0.0006 + y * 0.0012) / DPR - t * 0.19);
        return a0 + Math.max(0, (w + 2) / 4) * a1;
      };

      ctx.clearRect(0, 0, W, H);
      const p = PITCH * DPR, rowH = p * ROW;
      const cols = Math.ceil(W / p) + 2, rows = Math.ceil(H / rowH) + 2;

      for (let row = -1; row < rows; row++) {
        const y = row * rowH;
        const shift = (row & 1) ? p / 2 : 0;
        for (let col = -1; col < cols; col++) {
          const x = col * p + shift;
          const a = alphaAt(x, y).toFixed(3);
          const isStar = row % STAR_STEP === 0 &&
            ((col - (row / STAR_STEP) * (STAR_STEP % 2 ? 1 : 0)) % STAR_STEP + STAR_STEP) % STAR_STEP === 0;
          if (isStar) {
            ctx.strokeStyle = 'rgba(' + srgb + ',' + a + ')';
            star(ctx, x, y, STAR_R * DPR, rnd(row * 733 + col * 37) * Math.PI * 2);
          } else {
            ctx.fillStyle = 'rgba(' + rgb + ',' + a + ')';
            ctx.beginPath();
            ctx.arc(x, y, DOT_R * DPR, 0, 6.2832);
            ctx.fill();
          }
        }
      }

      if (!reduced) {
        t += 0.016;
        raf = requestAnimationFrame(draw);
      }
    }

    draw();
    return () => {
      dead = true;
      cancelAnimationFrame(raf);
      removeEventListener('resize', size);
      canvas.__relicField = false;
    };
  }

  window.RelicField = { start };
})();
