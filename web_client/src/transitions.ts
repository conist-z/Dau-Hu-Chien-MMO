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
  private startedAt = 0;
  /** Current iris hole radius in px (mirrors the CSS var). */
  private r = 0;
  private raf: number | null = null;
  private heartbeat: number | null = null;
  private irisTimer: number | null = null;
  private forceTimer: number | null = null;

  private static readonly MIN_LOAD_MS = 2200; // veil must be SEEN
  private static readonly IRIS_CLOSE_MS = 450;
  private static readonly IRIS_OPEN_MS = 550;
  private static readonly REVERSE_MS = 500; // video scrub back to 0
  private static readonly FORCE_MS = 8000;
  private static readonly POLL_MS = 100;

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
      <video class="tv-video" src="ui/travel_wipe.webm" muted playsinline
             preload="auto" disablepictureinpicture></video>
      <div class="tv-label"></div>
    `;
    document.body.appendChild(this.root);
    this.label = this.root.querySelector(".tv-label")!;
    this.iris = this.root.querySelector(".tv-iris")!;
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
    this.applyIris(0);
  }

  /** True while the veil is on screen. */
  get busy(): boolean {
    return this.state !== "idle";
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
    this.truth = 0;
    this.iris.style.transition = "none"; // no animation — snap
    this.applyIris(0); // 0 = fully black hole = fully covered
    this.showVideo(false);
    this.setLabel("");
    this.root.classList.remove("hidden");
    this.state = "loading"; // skip "closing" — already black
    this.enterLoading();
    this.armForceTimer();
  }

  /** Blocking-asset counters are no longer rendered (the bar is native
   *  playback — see header); kept as no-ops for call-site stability. */
  noteLoadTotal(_total: number): void {}

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

  /** Death: same veil flow (iris closes, video holds). dead=false →
   *  reverse out + iris-open. */
  setDead(dead: boolean, respawnS: number): void {
    if (dead) {
      this.deathMode = true;
      if (this.state === "idle") this.startTravel();
      this.setLabel(
        respawnS > 0
          ? `Bạn đã gục ngã… Hồi sinh sau ${Math.ceil(respawnS)}s`
          : "Bạn đã gục ngã… Đang hồi sinh…",
      );
    } else if (this.deathMode) {
      this.deathMode = false;
      this.exitSequence();
    }
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
    // IRIS CLOSE FIRST, video only after: the server AWAITS travel_begin
    // before teleporting, so this frame arrives while the main thread is
    // still free — the ~450 ms close animation fits inside that window and
    // the bake only starts when the welcome lands (veil already black).
    // Prep the iris fully OPEN while hidden, then reveal + close.
    this.iris.style.transition = "none";
    this.applyIris(this.maxR());
    this.root.classList.remove("hidden");
    this.showVideo(false);
    this.setLabel("");
    void this.iris.offsetWidth; // flush the open state before animating
    this.state = "closing";
    this.animateIris(0, TravelVeil.IRIS_CLOSE_MS, () => {
      if (this.state === "closing") this.enterLoading();
    });
    this.armForceTimer();
  }

  /** Fully black now: swap the iris for the loading video and let it play
   *  natively (compositor-driven — survives the main-thread bake stall). */
  private enterLoading(): void {
    this.state = "loading";
    this.startedAt = performance.now();
    this.showVideo(true);
    const vid = this.video;
    if (vid) {
      vid.pause();
      vid.currentTime = 0;
      vid.style.opacity = "1";
      // BAR COMPLETES AT EXIT: run the video at a rate that lands ~96%
      // right when MIN_LOAD elapses (the usual exit moment) — otherwise the
      // bar died at ~25% while the world was already ready (user: "load mới
      // 20% là mất tiêu"). A slower-than-expected load just holds the bar
      // at its end; a faster one cuts it slightly short — both fine.
      if (this.videoDur > 0) {
        const rate = (this.videoDur * 0.96) / (TravelVeil.MIN_LOAD_MS / 1000);
        vid.playbackRate = Math.min(3, Math.max(1, rate));
      }
      // muted+playsinline ⇒ autoplay is always permitted.
      vid.play().catch(() => {
        /* blocked playback: the black screen + iris still work */
      });
    }
    // Exit poll: truth complete + minimum display time → exit sequence.
    this.heartbeat = window.setInterval(() => {
      if (
        this.state === "loading" &&
        this.truth >= 1 &&
        performance.now() - this.startedAt >= TravelVeil.MIN_LOAD_MS
      ) {
        this.exitSequence();
      }
    }, TravelVeil.POLL_MS);
  }

  /** Deterministic exit: video reverses to frame 0 (bar un-fills, fading
   *  into the black iris beneath), then the iris opens from the center back
   *  out. Works from any state. */
  private exitSequence(): void {
    if (this.state === "idle" || this.state === "opening") return;
    this.cancelDrivers();
    this.setLabel("");
    const vid = this.video;
    if (!vid || this.videoDur <= 0) {
      this.beginOpening();
      return;
    }
    this.state = "reversing";
    vid.pause();
    const from = vid.currentTime;
    const t0 = performance.now();
    const step = (now: number) => {
      if (this.state !== "reversing") return;
      const k = Math.min(1, (now - t0) / TravelVeil.REVERSE_MS);
      vid.currentTime = Math.max(0, from * (1 - k));
      // Fade the whole loading screen into the black iris beneath it while
      // the bar un-fills — no abrupt bar disappearance at the swap.
      vid.style.opacity = String(1 - k);
      if (k >= 1) {
        this.raf = null;
        if (this.heartbeat !== null) {
          window.clearInterval(this.heartbeat);
          this.heartbeat = null;
        }
        this.showVideo(false);
        this.beginOpening();
      }
    };
    this.drive(step);
  }

  private beginOpening(): void {
    this.state = "opening";
    this.showVideo(false);
    this.animateIris(this.maxR(), TravelVeil.IRIS_OPEN_MS, () => {
      if (this.state === "opening") this.finish();
    });
  }

  private finish(): void {
    this.state = "idle";
    this.root.classList.add("hidden");
    this.truth = 0;
    this.setLabel("");
    this.iris.style.transition = "none";
    this.applyIris(0);
    this.clearForceTimer();
    this.cancelDrivers();
  }

  // ------------------------------------------------------------- internals

  /** Iris tween via CSS clip-path TRANSITION (not per-frame JS): the style
   *  recalc cost is paid by the compositor-driven transition timeline, not
   *  by 60 full-screen gradient repaints per second — the JS-driven version
   *  was visibly slow/janky (user: "RẤT CHẬM"). `transitionend` fires the
   *  callback; a timeout fallback covers missed events. */
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
      `clip-path ${ms}ms cubic-bezier(0.65, 0, 0.35, 1)`;
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

  private applyIris(r: number): void {
    this.r = r;
    this.iris.style.setProperty("--tv-r", `${Math.max(0, r)}px`);
  }

  /** Radius that clears every corner of the viewport (+ a margin). */
  private maxR(): number {
    return Math.hypot(window.innerWidth, window.innerHeight) / 2 + 40;
  }

  private showVideo(on: boolean): void {
    if (this.video) {
      this.video.style.visibility = on ? "visible" : "hidden";
      this.video.style.opacity = "1";
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
