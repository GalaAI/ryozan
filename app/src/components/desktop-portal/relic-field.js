// Source-derived field with module imports and owned listener cleanup.
/* Relic field v3 (GSAP) — traditional sleeve filler.

   WHY GSAP: the field's motion is no longer a raw sine. Each light pass
   is a real timeline tween carrying the brand's own easing curve
   (--t-slow, cubic-bezier(.16,1,.3,1) via CustomEase), with a held
   interval between passes and a fresh random heading per repeat
   (repeatRefresh). One shared gsap.ticker drives the draw, so this field
   and any other GSAP work on the page run on a single loop with
   lag smoothing — not competing requestAnimationFrames.

   GEOMETRY: one hexagonal lattice carries the dots, each point the same
   distance (PITCH) from all six neighbours. Stars are a seeded sporadic
   scatter (~1 in 9 lattice points) with a per-star offset, so their
   placement reads random, never gridded. Seeds are stable across loads.

   MOTION RULES (locked, in priority order):
     1. OPACITY ONLY. Marks never move, never resize.
     2. NO PER-MARK LIFE. Stars take the same alpha as dots — no shimmer.
     3. BROAD AND SLOW. One wide band crosses the field per pass.
     4. HEADING IS RANDOM PER PASS, and passes are separated by a rest
        interval, so the field is never continuously agitated.
     5. THE FIELD IS NEVER OFF. It rests at REST × --field-a1; the pass
        only lifts from there.

   Color: --field-rgb (marks), optional --field-star-rgb (stars only),
   --field-a0 (trough) / --field-a1 (crest), read from the host element.
   Reduced motion: one static frame at rest, no ticker, no tweens.

   Usage: RelicField.start(canvasEl, hostEl) → returns a kill function.
   Requires gsap (CustomEase optional; falls back to power4.out). */
import { gsap } from 'gsap';
import { CustomEase } from 'gsap/CustomEase';
  const PITCH = 30;
  const STAR_STEP = 3;
  const DOT_R = 1.75;
  const ARMS = 6;
  const STAR_R = 11;
  const ROW = Math.sqrt(3) / 2;

  const PASS_SECS = 2.6;   // duration of one light pass
  const REST_SECS = 1.4;   // held interval between passes
  const BAND = 520;        // px — how broad the pass is
  const REST = 0.45;       // share of --field-a1 held at rest

  const rnd = (i) => {
    let h = (i * 374761393 + 668265263) | 0;
    h = (h ^ (h >>> 13)) * 1274126177 | 0;
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  };

  /* seeded star picker with a spacing rule: greedy scan, a candidate only
     lands if no earlier star sits within MIN_GAP lattice cells (Chebyshev),
     so there are always >= 3 dots between any two stars. Cached per grid size. */
  const MIN_GAP = 2;
  let starMap = null, starDims = '';
  function buildStars(rows, cols) {
    const key = rows + 'x' + cols;
    if (starDims === key && starMap) return starMap;
    starDims = key; starMap = new Map();
    const chosen = [];
    for (let row = -1; row < rows; row++) {
      for (let col = -1; col < cols; col++) {
        if (rnd(row * 68917 + col * 41911) >= 0.52) continue;
        let ok = true;
        for (let k = chosen.length - 1; k >= 0; k--) {
          const c = chosen[k];
          if (row - c.row > MIN_GAP) break;
          if (Math.abs(row - c.row) <= MIN_GAP && Math.abs(col - c.col) <= MIN_GAP) { ok = false; break; }
        }
        if (!ok) continue;
        chosen.push({ row, col });
        starMap.set(row + ':' + col, 1);
      }
    }
    return starMap;
  }

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

  export function start(canvas, host, reducedMotion = false) {
    if (!canvas || canvas.__relicField) return;
    const lifetime = new AbortController(); let observer;
    if (!gsap) { console.warn('RelicField: gsap not loaded'); return; }
    canvas.__relicField = true;

    const ctx = canvas.getContext('2d');
    const reduced = reducedMotion || matchMedia('(prefers-reduced-motion: reduce)').matches;
    const DPR = Math.min(devicePixelRatio || 1, 2);
    let W = 0, H = 0;

    // the brand's signature out-quint, as a named ease
    let EASE = 'power4.out';
    if (CustomEase) {
      gsap.registerPlugin(CustomEase);
      if (!CustomEase.get('relicSlow')) {
        CustomEase.create('relicSlow', 'M0,0 C0.16,1 0.3,1 1,1');
      }
      EASE = 'relicSlow';
    }

    // the single animated state object — GSAP tweens this, we read it
    const sweep = { pos: -1, angle: 0, amp: 1 };

    // per-host overrides (optional CSS vars) — lets a page slow/broaden the pass locally
    const cs0 = getComputedStyle(host && host.nodeType === 1 ? host : document.documentElement);
    const passSecs = parseFloat(cs0.getPropertyValue('--field-pass-secs')) || PASS_SECS;
    const restSecs = parseFloat(cs0.getPropertyValue('--field-rest-secs')) || REST_SECS;
    const bandPx = parseFloat(cs0.getPropertyValue('--field-band')) || BAND;
    // optional pointer trail (--field-hover:1 on host): marks light up under the
    // cursor and the glow decays behind it
    const hoverOn = parseFloat(cs0.getPropertyValue('--field-hover')) || 0;
    const trail = [];
    if (hoverOn && !reduced && host && host.nodeType === 1) {
      host.addEventListener('pointermove', (e) => {
        const hb = host.getBoundingClientRect();
        trail.push({ x: (e.clientX - hb.left) * DPR, y: (e.clientY - hb.top) * DPR, t: performance.now() });
        if (trail.length > 28) trail.shift();
      }, { signal: lifetime.signal });
    }

    const box = () => {
      const h = host && host.nodeType === 1 ? host : null;
      if (h && h !== document.documentElement && h.clientWidth && h.clientHeight) {
        return { w: h.clientWidth, h: h.clientHeight };
      }
      return { w: innerWidth, h: innerHeight };
    };
    const size = () => {
      const b = box();
      W = canvas.width = b.w * DPR;
      H = canvas.height = b.h * DPR;
      canvas.style.width = b.w + 'px';
      canvas.style.height = b.h + 'px';
      if (reduced) render();
    };
    addEventListener('resize', size);
    if (window.ResizeObserver && host && host.nodeType === 1 && host !== document.documentElement) {
      observer = new ResizeObserver(size); observer.observe(host);
    }

    function render() {
      const cs = getComputedStyle(host || canvas.parentElement || document.documentElement);
      const rgb = (cs.getPropertyValue('--field-rgb') || '232,223,207').trim();
      const srgb = (cs.getPropertyValue('--field-star-rgb') || '').trim() || rgb;
      const a0 = parseFloat(cs.getPropertyValue('--field-a0')) || 0.018;
      const a1 = parseFloat(cs.getPropertyValue('--field-a1')) || 0.115;
      // stars only: resting appearance is dimmed, hover output is untouched.
      // both multipliers apply at boost 0 and blend back to 1 at boost 1, so the
      // peak under the pointer is pixel-identical to the undimmed field.
      const sRestL = parseFloat(cs.getPropertyValue('--field-star-rest-l'));
      const sRestA = parseFloat(cs.getPropertyValue('--field-star-rest-a'));
      const restL = isNaN(sRestL) ? 1 : sRestL;
      const restA = isNaN(sRestA) ? 1 : sRestA;
      const sBase = srgb.split(',').map(Number);

      ctx.clearRect(0, 0, W, H);
      const p = PITCH * DPR, rowH = p * ROW;
      const cols = Math.ceil(W / p) + 2, rows = Math.ceil(H / rowH) + 2;
      const stars = buildStars(rows, cols);

      const ux = Math.cos(sweep.angle), uy = Math.sin(sweep.angle);
      const band = bandPx * DPR;
      const reach = (Math.abs(W * ux) + Math.abs(H * uy)) / 2 + band;
      const front = sweep.pos * reach;
      const lift = (1 - REST) * sweep.amp;

      const now = performance.now();
      while (trail.length && now - trail[0].t > 900) trail.shift();
      const hr2 = Math.pow(90 * DPR, 2);

      for (let row = -1; row < rows; row++) {
        const y = row * rowH;
        const shift = (row & 1) ? p / 2 : 0;
        for (let col = -1; col < cols; col++) {
          const x = col * p + shift;
          const d = ((x - W / 2) * ux + (y - H / 2) * uy - front) / band;
          let boost = 0;
          for (let k = trail.length - 1; k >= 0; k--) {
            const tp = trail[k];
            const dx = x - tp.x, dy = y - tp.y, d2 = dx * dx + dy * dy;
            if (d2 < hr2 * 5) {
              const b = Math.exp(-d2 / hr2) * Math.exp(-(now - tp.t) / 380);
              if (b > boost) boost = b;
            }
          }
          let av = a0 + a1 * (REST + lift * Math.exp(-d * d));
          const hot = boost ? Math.min(1, av + a1 * boost) : av;
          if (boost) av = hot;
          const a = av.toFixed(3);
          const isStar = stars.has(row + ':' + col);
          if (isStar) {
            const sx = x, sy = y;
            let sa = a, scol = srgb;
            if (restL !== 1 || restA !== 1) {
              const t = boost;
              const rest = (a0 + a1 * (REST + lift * Math.exp(-d * d))) * restA;
              sa = (rest + (hot - rest) * t).toFixed(3);
              const lm = restL + (1 - restL) * t;
              scol = sBase.map(c => Math.round(c * lm)).join(',');
            }
            ctx.strokeStyle = 'rgba(' + scol + ',' + sa + ')';
            star(ctx, sx, sy, STAR_R * DPR, rnd(row * 733 + col * 37) * Math.PI * 2);
          }
          if (!isStar) {
            ctx.fillStyle = 'rgba(' + rgb + ',' + a + ')';
            ctx.beginPath();
            ctx.arc(x, y, DOT_R * DPR, 0, 6.2832);
            ctx.fill();
          }
        }
      }
    }

    size();

    if (reduced) {
      sweep.amp = 0;          // static frame, field at rest
      render();
      return () => { removeEventListener('resize', size); lifetime.abort(); observer?.disconnect(); canvas.__relicField=false; };
    }

    // one pass, then a held rest — new heading and reach each repeat
    const tl = gsap.timeline({ repeat: -1, repeatRefresh: true, repeatDelay: restSecs })
      .set(sweep, { pos: -1, angle: 'random(0, 6.283)', amp: 'random(0.85, 1)' })
      .to(sweep, { pos: 1, duration: passSecs, ease: EASE });

    gsap.ticker.add(render);

    return () => {
      tl.kill(); lifetime.abort(); observer?.disconnect();
      gsap.ticker.remove(render);
      removeEventListener('resize', size);
      canvas.__relicField = false;
    };
  }