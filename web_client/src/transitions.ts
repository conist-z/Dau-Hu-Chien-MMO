/**
 * TRAVEL TRANSITION VEIL (iris) — web client map-switch/loading screen.
 *
 * Logic ported from opera-gaming/prefab-transition (kCircleCrop shader):
 * the whole effect is a PURE FUNCTION of one `progress` value 0→1 —
 *   dist = length(uv - center);  r = 1 - progress * 2.0 (+ smoothness)
 * Nobody animates the picture by itself: whoever HOLDS progress holds the
 * image. Here the holder is the REAL load state (blocking assets fetched +
 * first snapshot of the new map), so the veil can never lie — fast load =
 * fast reveal, slow load = the veil holds near the end until the world is
 * genuinely ready.
 *
 * HARD RULES honored (AGENTS.md):
 *  - Topmost layer: z-index 6000 — above #mobile-controls (1), #overlay HUD
 *    (2), danger-alert (2500), preview panel (3000). While covered it takes
 *    pointer-events:auto so NO input (keyboard, D-pad, taps) reaches the
 *    game during the server handoff ("chống dịch chuyển chậm").
 *  - body-level DOM (not inside #game-root) so no Phaser stacking-context
 *    interference; mouse input stays bound to game.canvas.
 *  - Timeline: close iris ~0.35s → world bakes behind BLACK (freeze is
 *    invisible) → video loading art fades in over ~0.2s ("đen rồi dần rõ
 *    lên") → real % drives playbackRate + text → first snapshot of the new
 *    map = 100% → iris opens ~0.45s.
 *  - Safety: never trap the player — force-open 8s after beginTravel.
 */

/** Block-local CSS variable name for the iris radius (vmax units). */
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

  // REAL progress bookkeeping (the % the iris + display follow):
  private total = 0;
  private done = 0;
  private target = 0; // 0..1 truth
  private shown = 0; // 0..1 displayed (rAF-eased chase)
  private raf: number | null = null;
  private forceTimer: number | null = null;
  private openDelayTimer: number | null = null;

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
    this.target = total > 0 ? 0 : 0.9;
    this.startRaf();
  }

  noteAssetDone(): void {
    if (this.total <= 0) return;
    this.done = Math.min(this.done + 1, this.total);
    this.target = 0.9 * (this.done / this.total);
    this.startRaf();
  }

  /** First snapshot of the NEW map (caller guards map_id): world is real →
   *  100% → iris opens. */
  noteReady(): void {
    if (this.state === "idle" || this.deathMode) return;
    this.target = 1;
    this.startRaf();
    if (this.openDelayTimer !== null) window.clearTimeout(this.openDelayTimer);
    this.openDelayTimer = window.setTimeout(() => {
      this.openDelayTimer = null;
      this.openIris();
    }, 260);
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

  private closeIris(instant: boolean): void {
    if (this.openDelayTimer !== null) {
      window.clearTimeout(this.openDelayTimer);
      this.openDelayTimer = null;
    }
    if (this.forceTimer !== null) {
      window.clearTimeout(this.forceTimer);
      this.forceTimer = null;
    }
    this.state = instant ? "covered" : "closing";
    // Shader parity: progress 0.5 = iris fully closed.
    this.shown = instant ? 0.5 : 0;
    this.target = 0.5;
    this.root.classList.remove("hidden", "tv-opening");
    if (instant) {
      this.applyShown();
      this.root.classList.add("tv-covered");
      this.video?.classList.remove("tv-novideo");
    } else {
      this.root.classList.remove("tv-covered");
      this.raf ??= requestAnimationFrame(this.tick);
    }
  }

  private openIris(): void {
    if (this.state === "idle" || this.state === "opening") return;
    if (this.forceTimer !== null) {
      window.clearTimeout(this.forceTimer);
      this.forceTimer = null;
    }
    this.state = "opening";
    this.root.classList.add("tv-opening");
    this.video?.classList.add("tv-novideo"); // art fades out, iris opens
    this.target = 1;
    this.startRaf();
  }

  private armForceTimer(): void {
    if (this.forceTimer !== null) window.clearTimeout(this.forceTimer);
    this.forceTimer = window.setTimeout(() => {
      this.forceTimer = null;
      // NEVER trap the player behind the veil (lost snapshot / dead conn).
      this.openIris();
    }, 8000);
  }

  /** rAF chase: displayed progress eases toward the REAL target (fast load
   *  = the video speeds up, slow load = it crawls — "% thật điều khiển"). */
  private tick = (): void => {
    this.raf = null;
    const k = this.state === "opening" ? 0.045 : 0.12;
    this.shown += (this.target - this.shown) * k;
    if (Math.abs(this.target - this.shown) < 0.002) this.shown = this.target;
    this.applyShown();
    if (this.state === "closing" && this.shown >= 0.5) {
      this.state = "covered";
      this.root.classList.add("tv-covered");
      // "đen rồi màn dần rõ dần lên" — loading art surfaces over ~0.2s.
      this.video?.classList.remove("tv-novideo");
      if (this.total <= 0) this.target = 0.9;
    }
    if (this.state === "opening" && this.shown >= 1) {
      this.finishOpen();
      return;
    }
    if (this.state !== "idle") this.raf = requestAnimationFrame(this.tick);
  };

  private applyShown(): void {
    // kCircleCrop: r = 1 - progress*2 → closed at shown=0.5 (r=0), open at
    // shown=0 or 1. Iris radius in vmax (80vmax covers any diagonal).
    const closedP =
      this.state === "opening" ? 1 - this.shown * 0.5 : Math.min(0.5, this.shown) * 2;
    const iris = this.root.querySelector<HTMLElement>(".tv-iris");
    if (iris) iris.style.setProperty(R_VAR, `${(80 * Math.max(0, Math.min(1, closedP))).toFixed(2)}vmax`);
    // Progress readout: 0..90% during covered, 100% while opening.
    if (!this.deathMode) {
      if (this.state === "opening") {
        this.pct.textContent = "100%";
        this.barFill.style.width = "100%";
      } else {
        const v = Math.round(Math.max(0, Math.min(1, this.shown / 0.9)) * 100);
        this.pct.textContent = `${v}%`;
        this.barFill.style.width = `${v}%`;
      }
    }
    // Real % drives the video clock (tua nhanh/chậm theo % thật): slow at
    // the start, fast near the end. The looping art is the dial face.
    if (this.video && this.videoReady) {
      const rate = 0.35 + 1.65 * Math.max(0, Math.min(1, this.shown / 0.9));
      this.video.playbackRate = Math.max(0.25, Math.min(2.5, rate));
    }
  }

  private finishOpen(): void {
    this.state = "idle";
    this.raf = null;
    this.root.classList.add("hidden");
    this.root.classList.remove("tv-covered", "tv-opening");
    this.shown = 0;
    this.target = 0;
    this.total = 0;
    this.done = 0;
    this.video?.classList.remove("tv-novideo");
  }

  private startRaf(): void {
    if (this.raf === null && this.state !== "idle") {
      this.raf = requestAnimationFrame(this.tick);
    }
  }

  private setLabel(text: string): void {
    this.label.textContent = text;
  }
}
