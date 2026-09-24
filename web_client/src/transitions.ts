/**
 * TRAVEL TRANSITION VEIL (iris) — web client map-switch/loading screen.
 *
 * Logic ported from opera-gaming/prefab-transition (kCircleCrop shader):
 * the whole effect is a PURE FUNCTION of one progress value. Internally we
 * track `shown` = CLOSURE (0 = iris fully open/world visible, 1 = fully
 * black) — the iris HOLE radius is 80vmax × (1 − closure):
 *   closing:  closure 0→1  → black ring sweeps IN toward the center
 *   opening:  closure 1→0  → world reveals outward from the center
 * Nobody animates the picture by itself: whoever HOLDS the value holds the
 * image. The loading % is the truth source (blocking assets fetched + first
 * snapshot of the new map), so the veil can never lie — fast load = fast
 * reveal, slow load = the veil holds until the world is genuinely ready.
 *
 * HARD RULES honored (AGENTS.md):
 *  - Topmost layer: z-index 6000 — above #mobile-controls (1), #overlay HUD
 *    (2), danger-alert (2500), preview panel (3000). While covered it takes
 *    pointer-events:auto so NO input (keyboard, D-pad, taps) reaches the
 *    game during the server handoff ("chống dịch chuyển chậm").
 *  - body-level DOM (not inside #game-root) so no Phaser stacking-context
 *    interference; mouse input stays bound to game.canvas.
 *  - Timeline: iris closes ~0.4s (video art fades in DURING the close) →
 *    world bakes behind BLACK (freeze is invisible) → real % drives the
 *    video rate + bar → first snapshot of the new map = 100% → brief hold
 *    (~0.45s so the 100% + art are actually seen) → iris opens.
 *  - Safety: never trap the player — force-open 8s after beginTravel.
 */

/** Block-local CSS variable name for the iris hole radius (vmax units). */
const R_VAR = "--tv-r";

export class TravelVeil {
  private root: HTMLDivElement;
  private video: HTMLVideoElement | null = null;
  private videoReady = false;
  private label: HTMLDivElement;
  private pct: HTMLDivElement;
  private barFill: HTMLDivElement;

  /** "idle" → "closing" → "covered" → "opening" → "idle". Death reuses
   *  closing/covered with its own label and opens again on respawn. */
  private state: "idle" | "closing" | "covered" | "opening" = "idle";
  /** Death mode: no video art, own text, opens when dead=false arrives. */
  private deathMode = false;

  // Iris closure (0 = open, 1 = black). The displayed value chases the
  // target via rAF; the HOLE radius is 80vmax × (1 − closure).
  private shown = 0;
  private closureTarget = 0;
  // REAL loading progress (0..1) — drives the % text, bar and video rate.
  private pctTruth = 0;
  private total = 0;
  private done = 0;
  private raf: number | null = null;
  private forceTimer: number | null = null;
  private openDelayTimer: number | null = null;
  /** Monotonic start of the current close — feeds MIN_COVERED_MS so the
   *  loading art is actually SEEN even when everything is cached (user:
   *  "chưa kịp thấy loading là nó mất tiêu"). */
  private startedAt = 0;
  /** Minimum time the screen stays black (loading art visible). */
  private static readonly MIN_COVERED_MS = 1200;
  /** Hold at 100% before the iris opens (lets the art breathe). */
  private static readonly READY_HOLD_MS = 450;

  constructor() {
    this.root = document.createElement("div");
    this.root.id = "travel-root";
    this.root.classList.add("hidden");
    this.root.innerHTML = `
      <div class="tv-iris"></div>
      <div class="tv-center">
        <video class="tv-video" src="ui/travel_wipe.webm" muted playsinline
               loop autoplay preload="auto" disablepictureinpicture></video>
        <div class="tv-pct">0%</div>
        <div class="tv-bar"><div class="tv-bar-fill"></div></div>
        <div class="tv-label">Đang di chuyển…</div>
      </div>
    `;
    document.body.appendChild(this.root);
    this.label = this.root.querySelector(".tv-label")!;
    this.pct = this.root.querySelector(".tv-pct")!;
    this.barFill = this.root.querySelector(".tv-bar-fill")!;
    const vid = this.root.querySelector("video");
    if (vid) {
      vid.addEventListener("canplay", () => {
        this.videoReady = true;
        vid.play().catch(() => {
          /* autoplay blocked — CSS art still fine */
        });
      });
      vid.addEventListener("error", () => {
        // Missing/corrupt asset: the veil still works without the art.
        vid.remove();
        this.video = null;
      });
      this.video = vid;
    }
  }

  /** True while the veil is on screen (closing/covered/opening). */
  get busy(): boolean {
    return this.state !== "idle";
  }

  /** PRE-TRAVEL SIGNAL (server travel_begin): start the iris CLOSE now so
   *  the server handoff happens behind a black screen. */
  beginTravel(mapName: string): void {
    if (this.deathMode) {
      this.deathMode = false;
      this.video?.classList.remove("tv-novideo");
    }
    this.closeIris(false);
    this.setLabel(`Đang vào ${mapName}…`);
    this.armForceTimer();
  }

  /** welcome for a DIFFERENT map: label + cover. If no travel_begin beat us
   *  here (older server / reconnect), snap closed instantly — the heavy
   *  bake is already imminent, an animation would leak a frozen frame. */
  onMapSwitch(mapName: string): void {
    if (this.state === "idle") {
      this.closeIris(true);
      this.armForceTimer();
    }
    this.setLabel(`Đang vào ${mapName}…`);
  }

  /** Blocking-asset count for the new map (0 = everything cached → jump to
   *  ~90% at once; the last 10% is the first snapshot). */
  noteLoadTotal(total: number): void {
    this.total = total;
    this.done = 0;
    this.pctTruth = total > 0 ? 0.05 : 0.9;
    this.syncPct();
  }

  noteAssetDone(): void {
    if (this.total <= 0) return;
    this.done = Math.min(this.done + 1, this.total);
    this.pctTruth = 0.05 + 0.85 * (this.done / this.total);
    this.syncPct();
  }

  /** First snapshot of the NEW map (caller guards map_id): world is real →
   *  100% → hold (min covered time + 100% breathing room) → iris opens. */
  noteReady(): void {
    if (this.state === "idle" || this.deathMode) return;
    this.pctTruth = 1;
    this.syncPct();
    if (this.openDelayTimer !== null) window.clearTimeout(this.openDelayTimer);
    const coveredMs = performance.now() - this.startedAt;
    const wait = Math.max(
      TravelVeil.READY_HOLD_MS,
      TravelVeil.MIN_COVERED_MS - coveredMs,
    );
    this.openDelayTimer = window.setTimeout(() => {
      this.openDelayTimer = null;
      this.openIris();
    }, wait);
  }

  /** Death (iris also for death/respawn): quick close, own text, no video
   *  art. Call on every snapshot; dead=false opens back up. */
  setDead(dead: boolean, respawnS: number): void {
    if (dead) {
      this.deathMode = true;
      this.video?.classList.add("tv-novideo");
      if (this.state === "idle") {
        this.closeIris(false);
        this.armForceTimer();
      }
      this.setLabel(
        respawnS > 0
          ? `Bạn đã gục ngã… Hồi sinh sau ${Math.ceil(respawnS)}s`
          : "Bạn đã gục ngã… Đang hồi sinh…",
      );
      this.pct.textContent = "";
      this.barFill.style.width = "0%";
    } else if (this.deathMode) {
      // Respawned: reveal the world again.
      this.deathMode = false;
      this.video?.classList.remove("tv-novideo");
      this.openIris();
    }
  }

  // ------------------------------------------------------------------ core

  /** Close the iris: closure 0→1. `instant` snaps straight to black (used
   *  when the bake is already imminent — no travel_begin warning came). */
  private closeIris(instant: boolean): void {
    if (this.openDelayTimer !== null) {
      window.clearTimeout(this.openDelayTimer);
      this.openDelayTimer = null;
    }
    if (this.forceTimer !== null) {
      window.clearTimeout(this.forceTimer);
      this.forceTimer = null;
    }
    this.root.classList.remove("hidden", "tv-opening");
    this.root.classList.add("tv-active");
    this.startedAt = performance.now();
    if (instant) {
      this.state = "covered";
      this.shown = 1;
      this.applyShown();
      this.root.classList.add("tv-covered");
    } else {
      this.state = "closing";
      this.closureTarget = 1;
      this.root.classList.remove("tv-covered");
      this.raf ??= requestAnimationFrame(this.tick);
    }
  }

  /** Open the iris: closure 1→0 (world reveals outward from the center). */
  private openIris(): void {
    if (this.state === "idle" || this.state === "opening") return;
    if (this.forceTimer !== null) {
      window.clearTimeout(this.forceTimer);
      this.forceTimer = null;
    }
    this.state = "opening";
    this.root.classList.add("tv-opening");
    this.closureTarget = 0;
    this.raf ??= requestAnimationFrame(this.tick);
  }

  private armForceTimer(): void {
    if (this.forceTimer !== null) window.clearTimeout(this.forceTimer);
    this.forceTimer = window.setTimeout(() => {
      this.forceTimer = null;
      // NEVER trap the player behind the veil (lost snapshot / dead conn).
      this.openIris();
    }, 8000);
  }

  /** rAF chase: the closure eases toward its target; the loading % chases
   *  the truth. Fast load = the video speeds up, slow load = it crawls. */
  private tick = (): void => {
    this.raf = null;
    this.shown += (this.closureTarget - this.shown) * 0.09;
    const eps = 0.008;
    if (Math.abs(this.closureTarget - this.shown) < eps) this.shown = this.closureTarget;
    this.applyShown();
    if (this.state === "closing" && this.shown >= 1) {
      // Fully black: drop the mask cost, keep the art + % on screen.
      this.state = "covered";
      this.root.classList.add("tv-covered");
    }
    if (this.state === "opening" && this.shown <= 0) {
      this.finishOpen();
      return;
    }
    if (this.state !== "idle") this.raf = requestAnimationFrame(this.tick);
  };

  private applyShown(): void {
    // kCircleCrop parity: hole radius shrinks to 0 as closure → 1 (black
    // ring sweeps IN toward the center), grows back on open.
    const iris = this.root.querySelector<HTMLElement>(".tv-iris");
    if (iris) {
      const r = (80 * (1 - this.shown)).toFixed(2);
      iris.style.setProperty(R_VAR, `${r}vmax`);
    }
    // Loading art: visible from the closing phase on (user: "đen rồi màn
    // dần rõ dần lên" — it must be SEEN, not flashed after the black).
    // tv-opening hides it again so the reveal is pure world.
  }

  /** Push the REAL % into the readout + drive the video clock by it. */
  private syncPct(): void {
    if (this.deathMode) return;
    const v = Math.round(Math.max(0, Math.min(1, this.pctTruth)) * 100);
    this.pct.textContent = `${v}%`;
    this.barFill.style.width = `${v}%`;
    if (this.video && this.videoReady) {
      // Tua nhanh/chậm theo % thật (user requirement): slow start, fast end.
      const rate = 0.35 + 1.65 * Math.max(0, Math.min(1, this.pctTruth));
      this.video.playbackRate = Math.max(0.25, Math.min(2.5, rate));
    }
  }

  private finishOpen(): void {
    this.state = "idle";
    this.raf = null;
    this.root.classList.add("hidden");
    this.root.classList.remove("tv-covered", "tv-opening", "tv-active");
    this.shown = 0;
    this.closureTarget = 0;
    this.total = 0;
    this.done = 0;
    this.pctTruth = 0;
    this.video?.classList.remove("tv-novideo");
  }

  private setLabel(text: string): void {
    this.label.textContent = text;
  }
}
