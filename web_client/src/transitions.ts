/**
 * TRAVEL TRANSITION VEIL — web client map-switch/loading screen.
 *
 * Three visual phases:
 *
 *  1. CLOSE  — a black iris (CSS clip-path circle) SHRINKS onto the center:
 *              the screen ends fully black.
 *  2. LOAD   — the bundled webm ("LOADING..." pixel bar on black) plays
 *              NATIVELY from frame 0. Playback is compositor-driven, so the
 *              bar keeps moving even while the main thread is busy baking
 *              the new map — a JS seek-scrub froze right there (user:
 *              "bar đứng im", iris "mất vài giây mới open/out"). Exit is
 *              still gated on REAL readiness (first snapshot + MIN_LOAD),
 *              so the veil never opens before the world exists.
 *  3. OPEN   — the video scrubs back to frame 0 (bar un-fills while
 *              fading — main thread is idle again by now), then the iris
 *              OPENS from the center back out: the exact inverse of close.
 *
 * HARD RULES honored (AGENTS.md):
 *  - Topmost layer: z-index 6000 — above #mobile-controls (1), #overlay HUD
 *    (2), danger-alert (2500), preview panel (3000). While covered it takes
 *    pointer-events:auto so NO input reaches the game during the handoff.
 *  - body-level DOM, independent of Phaser stacking contexts.
 *  - Safety: never trap the player — force-open 8s after start.
 */

export class TravelVeil {
  private root: HTMLDivElement;
  private iris: HTMLDivElement;
  private video: HTMLVideoElement | null = null;
  private videoDur = 0;
  private label: HTMLDivElement;
  // SKULL-CLOSE mode (user 29/09): the death veil closes with a SKULL-shaped
  // hole instead of the circle; OPENING always stays the circle. The hole
  // is DRAWN on a full-screen <canvas> (black fill + destination-out skull
  // punch) — CSS mask-image with a data-URI DID NOT RENDER in the live
  // preview (screen stayed fully black, 29/09), and a mask-size tween left
  // the un-masked box remainder transparent (world leak). A canvas has real
  // alpha: what we draw is exactly what shows. The hole is path-drawn
  // (bezier dome, round sockets, triangle nose, teeth) — the old blocky
  // PNG read ugly (user: "đầu lâu trông xấu vl") and starts at ~48% of the
  // viewport diagonal so the black border is THICK from frame one (user:
  // "viền đen chưa đủ to, tăng diện tích gấp vài lần").
  private skullCanvas: HTMLCanvasElement | null = null;
  private skullCtx: CanvasRenderingContext2D | null = null;
  private skull = false;
  /** True while the CURRENT travel is a skull death-close: enterLoading
   *  then holds the loading screen for DEATH_MIN_LOAD_MS. Reset per
   *  travel (see startTravel / onMapSwitch). */
  private deathLoad = false;

  /** idle → closing (iris in) → loading (native video playback) →
   *  reversing (bar un-fills) → opening (iris out) → idle. */
  private state:
    | "idle"
    | "closing"
    | "loading"
    | "reversing"
    | "opening" = "idle";
  private deathMode = false;

  /** REAL readiness (0..1): latched to 1 by the first snapshot of the NEW
   *  map (noteReady). Drives the EXIT decision only — the bar itself is
   *  native playback (see header). */
  private truth = 0;
  /** BAR-DONE gate (user 30/09: "chạy đến 100% mới là xong"): the exit
   *  waits for the loading bar to visually COMPLETE, not just for the
   *  world to be ready. Latched by the video hold-handler below. */
  private barDone = false;
  /** Video seconds where the bar is visually FULL (frame ~69/82 of the
   *  clean webm — probed pixel-wise). */
  private static readonly BAR_FULL_AT = 3.4;
  private startedAt = 0;
  /** Current iris hole radius in px (mirrors the CSS var). */
  private r = 0;
  private raf: number | null = null;
  private heartbeat: number | null = null;
  private irisTimer: number | null = null;
  private forceTimer: number | null = null;

  private static readonly MIN_LOAD_MS = 2200; // veil must be SEEN
  /** DEATH loading lasts LONGER than a map-switch one (user 30/09: death
   *  always exits at the bare minimum because the same map is instantly
   *  ready — the whole sequence felt rushed next to a real travel). The
   *  skull close sets deathLoad and enterLoading then holds the video
   *  for this long. */
  private static readonly DEATH_MIN_LOAD_MS = 4200;
  /** Slower native playback on the death path so the pixel bar keeps
   *  crawling through the longer hold instead of parking at ~85% for
   *  seconds (bar completes ≈3.5s video ÷ rate; death rate 0.85 ≈ 4.1s). */
  private static readonly DEATH_VIDEO_RATE = 0.85;
  private static readonly TRAVEL_VIDEO_RATE = 1.25;
  private static readonly IRIS_CLOSE_MS = 450; // hole shrinks onto the center
  private static readonly IRIS_OPEN_MS = 550;
  private static readonly REVERSE_MS = 500; // video scrub back to 0
  private static readonly FORCE_MS = 8000;
  private static readonly POLL_MS = 100;
  /** Hole height at close-start as a fraction of the viewport diagonal:
   *  ~48% keeps a THICK black border on every edge from the very first
   *  frame (user: "viền đen tăng diện tích gấp vài lần"). */
  private static readonly SKULL_START_FRAC = 0.48;

  constructor() {
    this.root = document.createElement("div");
    this.root.id = "travel-root";
    this.root.classList.add("hidden");
    // Layer order (bottom→top): iris (black, circular hole) → video
    // (opaque loading screen; during the reverse it FADES out over the
    // solid-black iris so the baked-in bar never pops) → label (death
    // text only — travel uses no HTML label: the video already says
    // "LOADING...").
    this.root.innerHTML = `
      <div class="tv-iris"></div>
      <div class="tv-fill"></div>
      <video class="tv-video" src="ui/travel_wipe_clean.webm" muted playsinline
             preload="auto" disablepictureinpicture></video>
      <div class="tv-label"></div>
    `;
    document.body.appendChild(this.root);
    this.label = this.root.querySelector(".tv-label")!;
    this.iris = this.root.querySelector(".tv-iris")!;
    // Skull-close canvas: ABOVE the iris, BELOW the fill/video. Hidden
    // unless a skull close is running (skull-close only; opening is a
    // circle). Full-screen, real alpha — the skull hole is cut with
    // destination-out so what we draw is exactly what shows.
    const sc = document.createElement("canvas");
    sc.id = "tv-skull-canvas";
    sc.style.display = "none";
    this.root.insertBefore(sc, this.root.querySelector(".tv-fill"));
    this.skullCanvas = sc;
    this.skullCtx = sc.getContext("2d");
    const vid = this.root.querySelector("video");
    if (vid) {
      vid.addEventListener("loadedmetadata", () => {
        this.videoDur = vid.duration || 0;
      });
      vid.addEventListener("error", () => {
        // Missing/corrupt asset: the black veil + iris still work.
        vid.remove();
        this.video = null;
      });
      this.video = vid;
    }
    this.applyIris(0); // r=0: shadow alone = FULLY black, no clamp needed
  }

  /** True while the veil is on screen. */
  get busy(): boolean {
    return this.state !== "idle";
  }

  /** (Re)size the skull canvas to the viewport and DRAW the hole at
   *  progress `t` (0 = start, 1 = fully black). Redraw per step is cheap:
   *  one fillRect + one path fill on a viewport-sized canvas. */
  private drawSkull(t: number): void {
    const cv = this.skullCanvas;
    const ctx = this.skullCtx;
    if (!cv || !ctx) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const pw = Math.ceil(vw * dpr);
    const ph = Math.ceil(vh * dpr);
    if (cv.width !== pw || cv.height !== ph) {
      cv.width = pw;
      cv.height = ph;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, vw, vh);
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, vw, vh);
    const diag = Math.hypot(vw, vh);
    const h = diag * TravelVeil.SKULL_START_FRAC * (1 - t);
    if (h > 2) {
      ctx.globalCompositeOperation = "destination-out";
      ctx.fillStyle = "#fff";
      drawSkullHole(ctx, vw / 2, vh / 2, h);
    }
  }

  /** PRE-TRAVEL SIGNAL (server travel_begin): FULLY BLACK NOW.
   *  No close animation on purpose: the map bake freezes the main thread
   *  right after this frame, and a frozen mid-close iris = the game flashes
   *  on screen uncovered (user: "thấy preview trước rồi iris mới load").
   *  The circle experience lives on the OPEN phase, which runs after the
   *  bake when the main thread is free. */
  beginTravel(_mapName: string): void {
    this.startTravel();
  }

  /** welcome for a DIFFERENT map without a travel_begin (reconnect etc.):
   *  the bake is IMMINENT — there is no 450 ms to animate a close; go
   *  FULLY BLACK this very frame (user: "iris chưa kịp load nữa là đã
   *  thấy màn game 1 nháy"). The close-side animation only runs on the
   *  early travel_begin path where the world still exists for a moment. */
  onMapSwitch(_mapName: string): void {
    if (this.state !== "idle") return;
    this.clearForceTimer();
    this.cancelDrivers();
    this.deathMode = false;
    this.skull = false;
    this.deathLoad = false;
    this.iris.classList.remove("tv-skull");
    this.truth = 0;
    this.barDone = false;
    this.iris.style.transition = "none"; // no animation — snap
    this.syncShadowSpread();
    this.applyIris(0); // fully black THIS frame
    this.showVideo(true);
    this.setLabel("");
    this.root.classList.remove("hidden");
    this.state = "loading"; // skip "closing" — already black
    this.enterLoading();
    this.armForceTimer();
  }

  /** Blocking-asset counters are no longer rendered (the bar is native
   *  playback — see header); kept as no-ops for call-site stability. */
  noteLoadTotal(_total: number): void {}

  /** Run `fn` once the screen is SOLID BLACK (iris close finished / snap
   *  path) — the heavy map bake is then invisible to the player, and the
   *  compositor-driven loading video keeps animating through the freeze.
   *  If the veil is somehow idle the callback runs immediately (caller
   *  fallback already handles that case; this is belt-and-braces). */
  whenBlack(fn: () => void): void {
    if (this.state === "loading" || this.state === "reversing") {
      fn();
      return;
    }
    if (this.state === "idle") {
      fn();
      return;
    }
    // state === "closing": poll until enterLoading flips the state. 25ms
    // granularity is far below human perception on a black screen; the
    // 8s force timer bounds a pathological miss.
    const t0 = performance.now();
    const poll = () => {
      if (this.state !== "closing" || performance.now() - t0 > 8000) {
        fn();
        return;
      }
      window.setTimeout(poll, 25);
    };
    window.setTimeout(poll, 25);
  }

  noteAssetDone(): void {}

  /** First snapshot of the NEW map: truth = 100% (latched). May land during
   *  the iris-close — dropping it here would stall the exit until the 8 s
   *  force timer (user: "vài giây mới thật sự open"). */
  noteReady(): void {
    // Only meaningful while a travel is actually in progress — a stray
    // snapshot of the CURRENT map (arriving before travel_begin) must not
    // pre-latch truth=1 or the veil would exit after one frame.
    if (this.deathMode || this.state === "idle") return;
    this.truth = 1;
  }

  /** Death — 3-PHASE flow (user 29/09). Phase 1 (the world + dissolve +
   *  death text) runs WITHOUT the veil: the client calls armSkullDeath()
   *  when the death is fresh and this class does nothing until the
   *  countdown ends (deathVeilNow()). Phase 2 = closeSkull(): the skull
   *  iris swallows the screen, then the loading video holds. Phase 3 =
   *  dead=false → the loading screen exits and the iris OPENS AS A CIRCLE
   *  (no skull on respawn — user: "lúc mở ra thì vẫn là hình tròn"). */
  setDead(dead: boolean, _respawnS: number): void {
    if (dead) {
      this.deathMode = true; // countdown running client-side; veil not yet
      return;
    } else if (this.deathMode) {
      this.deathMode = false;
      // Respawn while the skull veil is up → normal circle-open exit.
      if (this.state !== "idle") this.exitSequence();
    }
  }

  /** PHASE 2 entry (called by the client when the respawn countdown hits
   *  0): skull-iris close onto the loading screen. Safe to call repeatedly
   *  (idempotent once the veil has started). */
  closeSkull(): void {
    this.deathMode = true;
    if (this.state === "idle") {
      this.skull = true;
      this.startTravel();
    }
  }

  /** Force-open safety for the death path (a lost snapshot mid-death must
   *  never trap the player behind the veil). */
  abortDeath(): void {
    if (this.state !== "idle") this.exitSequence();
    this.deathMode = false;
    this.skull = false;
    this.iris.classList.remove("tv-skull");
  }

  // ------------------------------------------------------------------ flow

  private startTravel(): void {
    if (this.state !== "idle") {
      // Already transitioning (travel_begin arriving AFTER the welcome's
      // onMapSwitch — either frame covers the screen solid black anyway).
      // Touch NOTHING except the safety net.
      this.armForceTimer();
      return;
    }
    this.clearForceTimer();
    this.cancelDrivers();
    this.deathMode = false;
    this.truth = 0;
    this.barDone = false;
    // IRIS CLOSE (the user's "hình tròn kéo về tâm siêu nhỏ"): the hole
    // starts at the INSCRIBED radius — the four corners are already black
    // on the first frame — and shrinks to r=0 (FULLY black: the shadow
    // covers every corner at every radius — no clamp, no rubber-band, no
    // hand-off flash). SKULL mode: the black lives on a full-screen CANVAS
    // whose skull hole shrinks per step (CSS mask proved unrenderable with
    // data-URIs in the live preview). The canvas covers the viewport the
    // whole time — the black never uncovers the world.
    this.syncShadowSpread();
    const useSkull = this.skull && !!this.skullCanvas;
    this.deathLoad = useSkull; // skull close ⇒ LONGER loading hold
    this.iris.classList.toggle("tv-skull", useSkull);
    this.iris.style.transition = "none";
    if (useSkull) {
      this.iris.style.boxShadow = "none";
      this.iris.style.background = "transparent"; // black lives on the canvas
      this.applyIris(0);
      this.skullCanvas!.style.display = "block";
      this.drawSkull(0);
    } else {
      this.applyIris(this.inscribedR());
    }
    this.root.classList.remove("hidden");
    this.showVideo(false);
    this.setLabel("");
    void this.iris.offsetWidth; // flush the start state before animating
    this.state = "closing";
    if (useSkull) {
      this.skull = false; // opening always re-opens as the CIRCLE
      this.stepSkullFrames(TravelVeil.IRIS_CLOSE_MS, () => {
        if (this.state === "closing") this.enterLoading();
      });
    } else {
      this.animateIris(0, TravelVeil.IRIS_CLOSE_MS, () => {
        this.skull = false; // opening always re-opens as the CIRCLE
        if (this.state === "closing") this.enterLoading();
      });
    }
    this.armForceTimer();
  }

  /** STEP the skull-hole shrink (setTimeout 25ms ticks — hidden/throttled
   *  tabs still finish): each tick redraws the canvas with a smaller hole;
   *  the black around it stays pinned over every pixel. */
  private stepSkullFrames(ms: number, onDone: () => void): void {
    const t0 = performance.now();
    const tick = () => {
      if (this.state !== "closing") return; // raced: abort/force
      const t = Math.min(1, (performance.now() - t0) / ms);
      this.drawSkull(t);
      if (t >= 1) {
        this.irisTimer = null;
        onDone();
        return;
      }
      this.irisTimer = window.setTimeout(tick, 25);
    };
    tick();
  }

  /** Fully black now: swap the iris for the loading video and let it play
   *  natively (compositor-driven — survives the main-thread bake stall). */
  private enterLoading(): void {
    this.state = "loading";
    this.startedAt = performance.now();
    // The skull canvas owns the CLOSE visuals — hand the iris back to
    // CIRCLE mode the moment the close finishes. The legacy .tv-skull CSS
    // mask clips the element's ENTIRE rendering INCLUDING its box-shadow;
    // leaving the class on through the open phase made the black shadow
    // transparent and the circle-open invisible (user: "loading xong là
    // hiện game luôn"). The canvas hides in exitSequence.
    this.iris.classList.remove("tv-skull");
    this.barDone = this.video === null; // no video → don't gate on the bar
    this.showVideo(true);
    const vid = this.video;
    if (vid) {
      vid.pause();
      vid.currentTime = 0;
      vid.style.opacity = "1";
      // CLEAN VIDEO (rebuilt 30/09 from the original's decodable span):
      // the webm now decodes ALL 82 frames (20fps, 4.1s) — the fill runs
      // frame 0→69 (bar VISUALLY FULL at t≈3.45s) then HOLDS the 100%
      // frame ~0.6s. No broken tail, no wrap frame → the bar runs to a
      // real 100% and the old "0% sau khi chạy 100%" leak is gone.
      // Playback: rate 1.25 spreads the fill (~2.8s to full); at FULL_AT
      // we latch barDone + pause (never seek — parked on the full frame,
      // which the clean tail holds anyway).
      const target = TravelVeil.BAR_FULL_AT; // bar visually 100%
      vid.playbackRate = this.deathLoad
        ? TravelVeil.DEATH_VIDEO_RATE
        : TravelVeil.TRAVEL_VIDEO_RATE;
      const hold = () => {
        if (this.state !== "loading") {
          vid.removeEventListener("timeupdate", hold);
          vid.removeEventListener("ended", hold);
          return;
        }
        if (vid.ended || vid.currentTime >= target) {
          // Bar at 100%: latch the exit gate + pause on the full frame.
          this.barDone = true;
          vid.pause();
        }
      };
      vid.addEventListener("timeupdate", hold);
      vid.addEventListener("ended", hold);
      // muted+playsinline ⇒ autoplay is always permitted.
      vid.play().catch(() => {
        this.barDone = true; // blocked playback: don't gate the exit
        /* the black screen + iris still work */
      });
    }
    // Exit poll: world ready + BAR AT 100% (user 30/09: "chạy đến 100%
    // mới là xong") + minimum display time → exit sequence. DEATH holds
    // the loading screen longer (DEATH_MIN_LOAD_MS — user 30/09).
    this.heartbeat = window.setInterval(() => {
      if (
        this.state === "loading" &&
        this.truth >= 1 &&
        this.barDone &&
        performance.now() - this.startedAt >=
          (this.deathLoad
            ? TravelVeil.DEATH_MIN_LOAD_MS
            : TravelVeil.MIN_LOAD_MS)
      ) {
        this.exitSequence();
      }
    }, TravelVeil.POLL_MS);
  }

  /** Exit: FADE the video into the black iris beneath (no reverse-scrub:
   *  seeking the paused video re-painted its current frame one extra time
   *  — the user's "nháy 1 frame loading sau khi load xong") then open the
   *  iris. A short crossfade reads the same and can never flash. */
  private exitSequence(): void {
    if (this.state === "idle" || this.state === "opening") return;
    this.cancelDrivers();
    this.setLabel("");
    // Hide the skull canvas BEFORE the open: it is fully black (t=1) and
    // sits ABOVE the iris — left visible it covers the circle-opening
    // entirely and the game just pops in when the video fades (user:
    // "loading xong là hiện game luôn"). The iris takes over the black.
    if (this.skullCanvas) this.skullCanvas.style.display = "none";
    // Belt-and-braces: drop the skull class here too (enterLoading already
    // does it — this covers a force-exit straight out of "closing"), then
    // restore the circle iris's black box-shadow (skull close set it to
    // "none" — without it the opening circle has no black to reveal
    // through, so the open is invisible).
    this.iris.classList.remove("tv-skull");
    this.syncShadowSpread();
    const vid = this.video;
    if (!vid || this.videoDur <= 0) {
      this.beginOpening();
      return;
    }
    this.state = "reversing";
    vid.pause();
    vid.style.transition = `opacity ${TravelVeil.REVERSE_MS}ms linear`;
    vid.style.opacity = "0";
    const t0 = performance.now();
    const step = (now: number) => {
      if (this.state !== "reversing") return;
      if (now - t0 >= TravelVeil.REVERSE_MS) {
        this.raf = null;
        if (this.heartbeat !== null) {
          window.clearInterval(this.heartbeat);
          this.heartbeat = null;
        }
        vid.style.transition = "none";
        this.showVideo(false);
        this.beginOpening();
      }
    };
    this.drive(step);
  }

  private beginOpening(): void {
    this.state = "opening";
    this.showVideo(false);
    // Grow the hole from a pinpoint (fully black) past the corners for a
    // full reveal — the shadow stays pinned to every corner throughout.
    this.applyIris(0);
    this.iris.style.transition = "none";
    void this.iris.offsetWidth;
    // The screen is SOLID BLACK here, so waiting is invisible. The new
    // map's first-render burst (bigmap tile streaming) hogs the main
    // thread for far longer than a fixed 2-frame wait — starting the tween
    // inside that burst froze it mid-way (user: "open từ khu vực khác ra
    // bigmap bị đơ"). Instead, START ONLY WHEN THE MAIN THREAD SETTLES:
    // the tween then runs end-to-end on a responsive thread.
    this.waitMainThreadIdle(() => {
      if (this.state !== "opening") return; // force-timer/skip raced us
      this.animateIris(this.maxR(), TravelVeil.IRIS_OPEN_MS, () => {
        if (this.state === "opening") this.finish();
      });
    });
  }

  private finish(): void {
    this.state = "idle";
    this.root.classList.add("hidden");
    this.truth = 0;
    this.barDone = false;
    this.setLabel("");
    this.iris.style.transition = "none";
    this.iris.classList.remove("tv-skull");
    this.applyIris(0); // reset: fully black, ready for the next close
    this.syncShadowSpread(); // undo a skull close's boxShadow:none
    this.iris.style.background = "transparent"; // undo a skull close's bg
    if (this.skullCanvas) this.skullCanvas.style.display = "none";
    this.clearForceTimer();
    this.cancelDrivers();
  }

  // ------------------------------------------------------------- internals

  /** Iris tween via CSS TRANSITION (not per-frame JS): the style
   *  recalc cost is paid by the compositor-driven transition timeline, not
   *  by 60 full-screen gradient repaints per second — the JS-driven version
   *  was visibly slow/janky (user: "RẤT CHẬM"). `transitionend` fires the
   *  callback; a timeout fallback covers missed events.
   *  SKULL mode tweens the element INSTEAD (fixed full-screen box, the mask
   *  hole scales from maskScale 1 → 0): the box never moves, so the black
   *  stays pinned over every pixel while the skull hole shrinks into the
   *  center — width/height/margin transitions would shrink the BOX and
   *  uncover the world. */
  private animateIris(toR: number, ms: number, onDone: () => void): void {
    if (this.irisTimer !== null) {
      window.clearTimeout(this.irisTimer);
      this.irisTimer = null;
    }
    const fromR = this.r;
    if (Math.abs(toR - fromR) < 0.5) {
      onDone();
      return;
    }
    let fired = false;
    const done = () => {
      if (fired) return;
      fired = true;
      this.iris.removeEventListener("transitionend", done);
      if (this.irisTimer !== null) {
        window.clearTimeout(this.irisTimer);
        this.irisTimer = null;
      }
      onDone();
    };
    this.iris.style.transition = "none";
    this.applyIris(fromR);
    void this.iris.offsetWidth; // flush the start state
    this.iris.style.transition =
      `width ${ms}ms cubic-bezier(0.65, 0, 0.35, 1), height ${ms}ms cubic-bezier(0.65, 0, 0.35, 1), margin ${ms}ms cubic-bezier(0.65, 0, 0.35, 1)`;
    this.applyIris(toR);
    this.iris.addEventListener("transitionend", done);
    this.irisTimer = window.setTimeout(done, ms + 150);
  }

  /** Throttle-proof driver for the video reverse: rAF PLUS a 50 ms
   *  setInterval heartbeat — hidden/unfocused tabs throttle rAF to 0 Hz
   *  and the scrub would freeze. Both drivers are pure time-based and
   *  idempotent, so whichever ticks advances the same trajectory. */
  private drive(fn: (now: number) => void): void {
    this.raf = requestAnimationFrame(fn);
    this.heartbeat = window.setInterval(() => fn(performance.now()), 50);
  }

  /** Hole radius in px → element size. The element IS the see-through
   *  hole (transparent circle); its box-shadow (fixed spread, sized by
   *  syncShadowSpread) is the black outside. Animating the REAL size (not
   *  transform:scale) keeps the shadow's coverage absolute at every
   *  radius — scale shrank the shadow with the hole and uncovered the
   *  corners for one frame (user: "không thật sự lấp đầy / cọng thun" +
   *  the 1-frame flash). */
  private applyIris(r: number): void {
    this.r = r;
    const d = Math.max(0, r * 2);
    this.iris.style.width = `${d}px`;
    this.iris.style.height = `${d}px`;
    this.iris.style.margin = `${-d / 2}px 0 0 ${-d / 2}px`;
  }

  /** Resolve when the main thread is IDLE ENOUGH to run a smooth tween:
   *  N consecutive rAF frames each serviced within FRAME_BUDGET_MS. A
   *  frame slower than the budget = still inside the map-bake burst →
   *  reset the counter and keep waiting. Hard cap keeps a pathological
   *  stall from trapping the player: after MAX_WAIT the tween starts
   *  regardless (a janky iris beats an eternal black screen). The screen
   *  is solid black the whole time, so a longer settle is invisible. */
  private static readonly IDLE_FRAMES_NEEDED = 3;
  private static readonly FRAME_BUDGET_MS = 28; // <2 missed frames @60Hz
  private static readonly IDLE_MAX_WAIT_MS = 2500;

  private waitMainThreadIdle(onIdle: () => void): void {
    const t0 = performance.now();
    let good = 0;
    let last = performance.now();
    const tick = (now: number) => {
      if (this.state !== "opening") return; // raced: force-timer/skip
      const dt = now - last;
      last = now;
      if (dt <= TravelVeil.FRAME_BUDGET_MS) {
        good++;
      } else {
        good = 0; // a slow frame = the burst is still running
      }
      if (
        good >= TravelVeil.IDLE_FRAMES_NEEDED ||
        now - t0 >= TravelVeil.IDLE_MAX_WAIT_MS
      ) {
        onIdle();
        return;
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  /** Size the black shadow ONCE per travel to just cover the viewport: the
   *  shadow starts at the hole's edge, so spread ≥ half-diagonal covers
   *  every corner even at r=0 (+margin for safety). The old fixed 12000px
   *  made the per-frame repaint of the moving shadow ~25× more expensive
   *  than needed — that repaint ran DURING the open tween and janked it
   *  whenever the bigmap streamed tiles (user: "open bị lag khi ra big
   *  map"). Same geometry, far cheaper paint. */
  private syncShadowSpread(): void {
    const spread = Math.ceil(Math.hypot(window.innerWidth, window.innerHeight)) + 200;
    this.iris.style.boxShadow = `0 0 0 ${spread}px #000`;
  }

  /** Radius that clears every corner of the viewport (+ a margin). */
  private maxR(): number {
    return Math.hypot(window.innerWidth, window.innerHeight) / 2 + 40;
  }

  /** Inscribed-circle radius: a hole this size touches the screen edges —
   *  everything OUTSIDE it (the four corners) is already black from frame
   *  one, and shrinking the hole pulls the old map into a center pinpoint. */
  private inscribedR(): number {
    return Math.min(window.innerWidth, window.innerHeight) / 2;
  }

  /** Video visible ⇔ the black filler covers the clamp hole. Kept in sync
   *  so the tiny center hole never shows the world. */
  private showVideo(on: boolean): void {
    const fill = this.root.querySelector(".tv-fill") as HTMLDivElement | null;
    if (fill) fill.style.visibility = on ? "visible" : "hidden";
    if (this.video) {
      this.video.style.visibility = on ? "visible" : "hidden";
      this.video.style.opacity = on ? "1" : "0";
    }
  }

  private setLabel(text: string): void {
    this.label.textContent = text;
  }

  private armForceTimer(): void {
    this.clearForceTimer();
    this.forceTimer = window.setTimeout(() => {
      this.forceTimer = null;
      // NEVER trap the player behind the veil (lost snapshot / dead conn):
      // run the graceful exit (reverse + iris-open) from any state.
      this.exitSequence();
    }, TravelVeil.FORCE_MS);
  }

  private clearForceTimer(): void {
    if (this.forceTimer !== null) {
      window.clearTimeout(this.forceTimer);
      this.forceTimer = null;
    }
  }

  private cancelDrivers(): void {
    if (this.raf !== null) {
      cancelAnimationFrame(this.raf);
      this.raf = null;
    }
    if (this.heartbeat !== null) {
      window.clearInterval(this.heartbeat);
      this.heartbeat = null;
    }
    if (this.irisTimer !== null) {
      window.clearTimeout(this.irisTimer);
      this.irisTimer = null;
    }
  }
}

/** Draw a SMOOTH skull silhouette (the mask HOLE) centered at (cx, cy)
 *  with total height `h`, using canvas paths: bezier dome, round cheeks,
 *  jaw, oval eye sockets, triangle nose, teeth slits — all one evenodd
 *  path so the sockets/nose/teeth punch back INSIDE the silhouette
 *  (they stay black while the world shows through the skull bone).
 *  Fractions tuned against the user's reference art. */
function drawSkullHole(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  h: number,
): void {
  const w = h * 0.84;
  const top = cy - h / 2;
  ctx.beginPath();
  // Dome (temple → top → temple):
  ctx.moveTo(cx - w * 0.5, top + h * 0.4);
  ctx.bezierCurveTo(cx - w * 0.5, top + h * 0.06, cx - w * 0.3, top, cx, top);
  ctx.bezierCurveTo(cx + w * 0.3, top, cx + w * 0.5, top + h * 0.06, cx + w * 0.5, top + h * 0.4);
  // Right cheek bulge → taper to the jaw:
  ctx.bezierCurveTo(cx + w * 0.5, top + h * 0.56, cx + w * 0.46, top + h * 0.6, cx + w * 0.3, top + h * 0.68);
  ctx.lineTo(cx + w * 0.26, top + h * 0.9);
  ctx.quadraticCurveTo(cx + w * 0.2, top + h, cx, top + h);
  ctx.quadraticCurveTo(cx - w * 0.2, top + h, cx - w * 0.26, top + h * 0.9);
  ctx.lineTo(cx - w * 0.3, top + h * 0.68);
  ctx.bezierCurveTo(cx - w * 0.46, top + h * 0.6, cx - w * 0.5, top + h * 0.56, cx - w * 0.5, top + h * 0.4);
  ctx.closePath();
  // Eye sockets (circles), nose (rounded triangle), teeth (4 slits) —
  // sub-paths reversed by the evenodd fill rule punch back INSIDE.
  const eyeY = top + h * 0.45;
  const eyeR = h * 0.105;
  const eyeDx = w * 0.22;
  ctx.moveTo(cx - eyeDx + eyeR, eyeY);
  ctx.arc(cx - eyeDx, eyeY, eyeR, 0, Math.PI * 2);
  ctx.moveTo(cx + eyeDx + eyeR, eyeY);
  ctx.arc(cx + eyeDx, eyeY, eyeR, 0, Math.PI * 2);
  // Nose: rounded triangle.
  const nY = top + h * 0.62;
  const nW = w * 0.09;
  const nH = h * 0.1;
  ctx.moveTo(cx, nY - nH / 2);
  ctx.quadraticCurveTo(cx + nW, nY, cx, nY + nH / 2);
  ctx.quadraticCurveTo(cx - nW, nY, cx, nY - nH / 2);
  ctx.closePath();
  // Teeth: 4 vertical slits between the cheeks.
  const tY0 = top + h * 0.78;
  const tY1 = top + h * 0.94;
  const tW = h * 0.024;
  const tGap = h * 0.075;
  for (let i = -1.5; i <= 1.5; i++) {
    const tx = cx + i * tGap;
    ctx.rect(tx - tW / 2, tY0, tW, tY1 - tY0);
  }
  ctx.fill("evenodd");
}
