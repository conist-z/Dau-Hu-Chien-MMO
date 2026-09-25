/**
 * TRAVEL TRANSITION VEIL — web client map-switch/loading screen.
 *
 * Three visual phases, all pure rAF time-based (deterministic: identical
 * every run, immune to CSS transition drift — user: "lúc được lúc không,
 * nhanh, giật"):
 *
 *  1. CLOSE  — a black iris SHRINKS onto the center (the hole closes),
 *              revealing nothing: the screen ends fully black.
 *  2. LOAD   — the bundled webm ("LOADING..." pixel bar on black) IS the
 *              loading screen; its clock is scrubbed by the REAL load
 *              progress (blocking assets + first snapshot). No extra HTML
 *              bar/label — the video already contains one (user: "mắc gì
 *              phải thêm 1 cái loading bên dưới nữa").
 *  3. OPEN   — the video reverses (bar un-fills), then the black iris
 *              OPENS from the center back out, exposing the new map.
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

  /** idle → closing (iris in) → loading (video scrub) → reversing (bar
   *  un-fills) → opening (iris out) → idle. */
  private state:
    | "idle"
    | "closing"
    | "loading"
    | "reversing"
    | "opening" = "idle";
  private deathMode = false;

  // REAL load truth (0..1): blocking assets + first snapshot = 1.
  private truth = 0;
  private shown = 0; // eased display value (drives the video clock)
  private total = 0;
  private done = 0;
  private startedAt = 0;
  private raf: number | null = null;
  private heartbeat: number | null = null;
  private forceTimer: number | null = null;

  private static readonly MIN_LOAD_MS = 1400; // veil must be SEEN
  private static readonly IRIS_CLOSE_MS = 450;
  private static readonly IRIS_OPEN_MS = 550;
  private static readonly REVERSE_MS = 500; // video scrub back to 0
  private static readonly FORCE_MS = 8000;
  /** Soft edge of the iris hole (px). */
  private static readonly IRIS_FEATHER = 36;

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

  /** PRE-TRAVEL SIGNAL (server travel_begin): iris closes NOW. */
  beginTravel(_mapName: string): void {
    this.startTravel();
  }

  /** welcome for a DIFFERENT map without a travel_begin (reconnect etc.):
   *  same instant iris-close — the bake is already imminent. */
  onMapSwitch(_mapName: string): void {
    if (this.state === "idle") this.startTravel();
  }

  /** Blocking-asset count for the new map (0 = cached → truth jumps to 0.9;
   *  the last 0.1 is the first snapshot of the new map). */
  noteLoadTotal(total: number): void {
    this.total = total;
    this.done = 0;
    this.truth = total > 0 ? 0.04 : 0.9;
  }

  noteAssetDone(): void {
    if (this.total <= 0) return;
    this.done = Math.min(this.done + 1, this.total);
    this.truth = 0.04 + 0.86 * (this.done / this.total);
  }

  /** First snapshot of the NEW map (caller guards map_id): truth = 100% →
   *  let the video reach its end → reverse → iris-open. LATCHED: the first
   *  snapshot often lands DURING the iris-close (450 ms) — dropping it here
   *  would leave truth at 0 forever and the bar would never run (bug:
   *  "loading không chạy từ 0 đến 100"). */
  noteReady(): void {
    if (this.deathMode) return;
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
      // onMapSwitch — the server schedules the hook fire-and-forget, so its
      // frame can land last). Touch NOTHING: the close animation and the
      // force timer must keep running, or the veil sticks half-closed
      // (user: "lúc được lúc không"). Just re-arm the safety net.
      this.armForceTimer();
      return;
    }
    this.clearForceTimer();
    this.cancelRaf();
    this.deathMode = false;
    // Fresh travel: drop the previous run's progress. noteLoadTotal /
    // noteReady fire AFTER this (welcome ordering varies), so the reset
    // must live HERE — enterLoading must never discard what already came
    // in during the close animation.
    this.truth = 0;
    this.shown = 0;
    this.total = 0;
    this.done = 0;
    this.root.classList.remove("hidden");
    this.showVideo(false);
    this.setLabel("");
    this.state = "closing";
    this.animateIris(this.maxR(), 0, TravelVeil.IRIS_CLOSE_MS, () => {
      if (this.state === "closing") this.enterLoading();
    });
    this.armForceTimer();
  }

  /** Fully black now: swap the iris for the loading video and start the
   *  real-progress scrub. Keeps whatever truth/total the welcome already
   *  fed during the close (ordering: travel_begin ↔ welcome varies). */
  private enterLoading(): void {
    this.state = "loading";
    this.startedAt = performance.now();
    this.showVideo(true);
    if (this.video) {
      this.video.pause();
      this.video.currentTime = 0;
    }
    this.applyProgress(0);
    this.drive(this.tick);
  }

  /** Driver helper: run `fn` on rAF PLUS a 50 ms setInterval heartbeat —
   *  hidden/unfocused tabs throttle rAF to 0 Hz and the whole transition
   *  would freeze (user: "lúc được lúc không, nhanh, giật"). Every step is
   *  time-based and idempotent, so the two drivers converge safely. */
  private drive(fn: (now: number) => void): void {
    this.cancelRaf();
    this.raf = requestAnimationFrame(fn);
    this.heartbeat = window.setInterval(() => fn(performance.now()), 50);
  }

  /** Progress driver while loading: `shown` eases toward the REAL truth and
   *  the video clock follows it (fast load = the bar speeds up, slow load =
   *  it crawls and holds near the end — never lies, never runs backwards). */
  private tick = (): void => {
    if (this.state !== "loading") return;
    this.shown += (this.truth - this.shown) * 0.075;
    if (Math.abs(this.truth - this.shown) < 0.004) this.shown = this.truth;
    this.applyProgress(this.shown);
    // Truth complete + minimum display time honored → exit sequence.
    const minElapsed =
      performance.now() - this.startedAt >= TravelVeil.MIN_LOAD_MS;
    if (this.shown >= 0.995 && this.truth >= 1 && minElapsed) {
      this.exitSequence();
      return;
    }
  };

  /** Deterministic exit: video reverses to frame 0 (bar un-fills), then the
   *  black iris opens from the center back out. Works from any state. */
  private exitSequence(): void {
    if (this.state === "idle" || this.state === "opening") return;
    this.cancelRaf();
    this.setLabel("");
    if (this.state === "closing") {
      // Never finished closing (fast death/respawn): just open back up.
      this.beginOpening();
      return;
    }
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
    this.animateIris(0, this.maxR(), TravelVeil.IRIS_OPEN_MS, () => {
      this.finish();
    });
  }

  private finish(): void {
    this.state = "idle";
    this.root.classList.add("hidden");
    this.shown = 0;
    this.truth = 0;
    this.total = 0;
    this.done = 0;
    this.setLabel("");
    this.applyIris(0);
    this.clearForceTimer();
  }

  // ------------------------------------------------------------- internals

  /** Time-based rAF tween of the iris hole radius: deterministic — lost
   *  frames (hidden tab) never skew the trajectory, only skip ahead. */
  private animateIris(
    fromR: number,
    toR: number,
    ms: number,
    onDone: () => void,
  ): void {
    this.cancelRaf();
    const t0 = performance.now();
    let done = false;
    const step = (now: number) => {
      if (done || this.state === "idle") return;
      const k = Math.min(1, (now - t0) / ms);
      const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; // easeInOutCubic
      this.applyIris(fromR + (toR - fromR) * e);
      if (k >= 1) {
        done = true;
        this.raf = null;
        if (this.heartbeat !== null) {
          window.clearInterval(this.heartbeat);
          this.heartbeat = null;
        }
        onDone();
      }
    };
    // HEARTBEAT (throttle-proof driver): a hidden/unfocused tab (or a main
    // thread busy baking the new map) throttles requestAnimationFrame to 0
    // Hz — the iris would freeze mid-animation (user: "lúc được lúc không,
    // giật"). drive() runs the step on rAF PLUS a 50 ms setInterval; both
    // are pure time-based, so whichever ticks advances the same trajectory
    // and the first one past k>=1 finishes it.
    this.drive(step);
  }

  private applyIris(r: number): void {
    // Radius feeds the CSS radial-gradient hole (--tv-r); the element itself
    // is full-screen — sizing it inline would collapse the gradient to 0×0.
    this.iris.style.setProperty("--tv-r", `${Math.max(0, r)}px`);
  }

  /** Radius that clears every corner of the viewport (+feather). */
  private maxR(): number {
    return Math.hypot(window.innerWidth, window.innerHeight) / 2 +
      TravelVeil.IRIS_FEATHER + 2;
  }

  private showVideo(on: boolean): void {
    if (this.video) {
      this.video.style.visibility = on ? "visible" : "hidden";
      this.video.style.opacity = "1";
    }
  }

  private applyProgress(p: number): void {
    if (this.video && this.videoDur > 0) {
      // Map 0..1 onto the video, stopping a hair before the end so the bar
      // never visually "completes" before the world is actually ready.
      this.video.currentTime = Math.min(
        this.videoDur - 0.05,
        Math.max(0, p) * (this.videoDur - 0.05),
      );
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

  private cancelRaf(): void {
    if (this.raf !== null) {
      cancelAnimationFrame(this.raf);
      this.raf = null;
    }
    if (this.heartbeat !== null) {
      window.clearInterval(this.heartbeat);
      this.heartbeat = null;
    }
  }
}
