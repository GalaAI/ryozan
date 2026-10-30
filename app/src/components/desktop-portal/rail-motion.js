// Mechanically extracted approved motion. Only selectors and timer ownership differ.
export class RailMotion {
  constructor(root, update, reducedMotion) { this.root=root; this.update=update; this.props={reducedMotion}; this.state={}; this.timers=new Set(); this.frames=new Set(); this.disposed=false; }
  setState(patch, callback) { if(this.disposed)return; Object.assign(this.state,patch); this.update(patch); if(callback)this.frame(callback); }
  timeout(fn,ms) { const id=setTimeout(()=>{this.timers.delete(id);if(!this.disposed)fn();},ms);this.timers.add(id);return id; }
  frame(fn) {const id=requestAnimationFrame(()=>{this.frames.delete(id);if(!this.disposed)fn();});this.frames.add(id);return id;}
  dispose(){this.disposed=true;this._introKill?.();this.timers.forEach(clearTimeout);this.frames.forEach(cancelAnimationFrame);this.root.querySelectorAll('*').forEach(el=>el.getAnimations?.().forEach(a=>a.cancel()));}
  railToggle() {
    if (this._railBusy) return;
    const aside = this.root.querySelector('aside[aria-label="Portal navigation"]');
    const tiger = aside && aside.querySelector('img');
    const banner = aside && aside.querySelector(':scope > div');
    const hide = !this.state.railHidden;
    if (this.props.reducedMotion || window.matchMedia('(prefers-reduced-motion: reduce)').matches) { this.setState({railHidden:hide,bannerT:hide?'translate(0,-110vh)':'translate(0px,-15px)',railSway:false}); return; }
    if (!aside || !tiger || !banner || !banner.animate) { this.setState({ railHidden: hide }); return; }
    this._railBusy = true;
    if (this._introAnims) { this._introAnims.forEach(a => { try { a.cancel(); } catch (e) {} }); this._introAnims = null; }
    const done = () => { this._railBusy = false; this.setState({ railHidden: hide, tigerT: 'translateX(-50%)' }); };
    try {
      const tRect = tiger.getBoundingClientRect();
      const up = -(tRect.top + tRect.height + 60);
      const after = (a, f) => { try { a.finished.then(() => { if (!this.disposed) f(); }, () => { if (!this.disposed) f(); }); } catch (e) { this.timeout(f, (a.effect && a.effect.getTiming().duration) || 600); } };
      /* 1. the icon retreats upwards */
      const t1 = tiger.animate([
        { transform: 'translateX(-50%)' },
        { transform: 'translateX(-50%) translateY(9px) scale(1.05,.92)', offset: .3 },
        { transform: 'translateX(-50%) translateY(' + up + 'px) scale(.94,1.08)' },
      ], { duration: 440, easing: 'cubic-bezier(.5,0,.85,.4)', fill: 'forwards' });
      after(t1, () => {
        t1.cancel(); this.setState({ tigerT: 'translateX(-50%) translateY(' + up + 'px) scale(.94,1.08)' });
        /* 2. the banner reverses its entry (rises off the top) or replays it (falls back in) */
        const off = -(banner.getBoundingClientRect().bottom + 40);
        const frames = hide
          ? [{ transform: 'translate(0,-15px)' }, { transform: 'translate(0,' + off + 'px)' }]
          : [{ transform: 'translate(0,' + off + 'px)' }, { transform: 'translate(0,-6px)', offset: .82 }, { transform: 'translate(0,-15px)' }];
        const b = banner.animate(frames, { duration: hide ? 500 : 760, easing: hide ? 'cubic-bezier(.6,-.08,.8,.4)' : 'cubic-bezier(.2,.9,.25,1)', fill: 'forwards' });
        after(b, () => {
          b.cancel(); this.setState({ bannerT: hide ? 'translate(0,' + off + 'px)' : 'translate(0px,-15px)' });
          /* 3. only after the banner's time runs out does the icon return downwards */
          const t2 = tiger.animate([
            { transform: 'translateX(-50%) translateY(' + up + 'px) scale(.94,1.08)' },
            { transform: 'translateX(-50%) translateY(8px) scale(1.07,.9)', offset: .72 },
            { transform: 'translateX(-50%) translateY(-4px) scale(.98,1.03)', offset: .88 },
            { transform: 'translateX(-50%)' },
          ], { duration: 560, easing: 'cubic-bezier(.32,.8,.3,1)', fill: 'forwards' });
          after(t2, () => { t2.cancel(); done(); });
        });
      });
    } catch (e) { console.error('railToggle failed:', e); done(); }
  }
  /* the opening entrance, built the same way as the working furl/unfurl: animate the REAL tiger + banner
     directly with WAAPI, no clone, no canvas/three.js cloth. Every settled checkpoint commits through
     setState (not a raw element.style write) so the component's own 1s re-render tick can never
     re-assert a stale template literal and strand the sprite mid-sequence. */
  railIntro() {
    if (this._railPlayed) return; this._railPlayed = true;
    if (this.props.reducedMotion) return; /* the reduced-motion tweak skips the entrance entirely */
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const aside = this.root.querySelector('aside[aria-label="Portal navigation"]');
    if (!aside || aside.offsetParent === null || !aside.animate) return;
    const tiger = aside.querySelector('img');
    const banner = aside.querySelector(':scope > div');
    if (!tiger || !banner || !banner.animate) return;
    /* tweakable timings (Banner intro section) */
    const P = this.props || {};
    const PAUSE = Math.round((P.introPause != null ? P.introPause : .5) * 1000);
    const SLIDE = P.introSlideMs || 700, JUMP = P.introJumpMs || 560, DROPD = P.introDropMs || 760, LAND = P.introLandMs || 560;
    const OVER = P.introOvershoot != null ? P.introOvershoot : 34;
    const tRect = tiger.getBoundingClientRect();
    const after = (a, f) => { try { a.finished.then(() => { if (!this.disposed) f(); }, () => { if (!this.disposed) f(); }); } catch (e) { this.timeout(f, (a.effect && a.effect.getTiming().duration) || 600); } };
    const inX = -(tRect.left + tRect.width + 160);
    const up = -(tRect.top + tRect.height + 120);
    const off = -(banner.getBoundingClientRect().bottom + 40);
    const inTiger = 'translateX(-50%) translateX(' + inX + 'px) rotate(-6deg)';
    const upTiger = 'translateX(-50%) translateY(' + up + 'px) scale(.92,1.1)';
    const restTiger = 'translateX(-50%)';
    const offBanner = 'translate(0,' + off + 'px)';
    const restBanner = 'translate(0px,-15px)';
    this.setState({ tigerT: inTiger, bannerT: offBanner });
    this._introRunning = true; /* freeze the 1s clock re-render while the sequence plays — a full re-render of this component mid-tween is the jank source */
    const anims = []; this._introAnims = anims;
    /* hard safety: whatever happens above, the rail always ends up visible and in place.
       cancel() every tracked animation first — with fill:'forwards' a stalled/still-running
       Animation keeps visual priority over the base style no matter what setState commits. */
    let settled = false;
    const settle = () => { if (settled) return; settled = true; this._introRunning = false; this.setState({ tigerT: restTiger, bannerT: restBanner, railSway: true }, () => this.frame(() => anims.forEach(a => { try { a.cancel(); } catch (e) {} }))); };
    this._introKill = settle; /* replay + unmount poison this run's whole chain via settled */
    this.timeout(settle, PAUSE * 3 + SLIDE + JUMP + DROPD + LAND + 900);
    /* 1. tiger slides right in from the left margin, overshoots, snaps back */
    this.timeout(() => {
      if (settled) return;
      const t1 = tiger.animate([
        { transform: inTiger },
        { transform: 'translateX(-50%) translateX(' + OVER + 'px) rotate(3deg)', offset: .58 },
        { transform: 'translateX(-50%) translateX(-10px) rotate(-1deg)', offset: .78 },
        { transform: restTiger },
      ], { duration: SLIDE, easing: 'cubic-bezier(.2,.9,.25,1)', fill: 'forwards' });
      anims.push(t1);
      after(t1, () => {
        if (settled) return;
        /* t1 stays alive (fill:forwards) — no cancel/setState mid-chain: a full re-render here is what caused the hitches */
        /* pause half a second, then jump up */
        this.timeout(() => {
          if (settled) return;
          const t2 = tiger.animate([
            { transform: restTiger },
            { transform: 'translateX(-50%) translateY(12px) scale(1.04,.94)', offset: .3 },
            { transform: upTiger },
          ], { duration: JUMP, easing: 'cubic-bezier(.5,0,.85,.4)', fill: 'forwards' });
          anims.push(t2);
          /* the banner only drops once the tiger reaches the top edge to grab it */
          after(t2, () => {
            if (settled) return;
            const b = banner.animate([
              { transform: offBanner },
              { transform: 'translate(0,-6px)', offset: .82 },
              { transform: restBanner },
            ], { duration: DROPD, easing: 'cubic-bezier(.2,.9,.25,1)', fill: 'forwards', delay: 500 });
            anims.push(b);
            after(b, () => {
              if (settled) return;
              /* pause half a second after the banner settles, then the icon falls back in, overshoots, and snaps into position */
              this.timeout(() => {
                if (settled) return;
                const t3 = tiger.animate([
                  { transform: upTiger },
                  { transform: 'translateX(-50%) translateY(8px) scale(1.07,.9)', offset: .72 },
                  { transform: 'translateX(-50%) translateY(-4px) scale(.98,1.03)', offset: .88 },
                  { transform: restTiger },
                ], { duration: LAND, easing: 'cubic-bezier(.32,.8,.3,1)', fill: 'forwards' });
                anims.push(t3);
                after(t3, () => { if (settled) return; settle(); });
              }, PAUSE);
            });
          });
        }, PAUSE);
      });
    }, PAUSE);
  }

}
