// Mobile controls: HOME-ANCHORED FLOATING KNOB joystick (Kenney CC0 art)
// + action buttons. MULTI-TOUCH FIRST-CLASS:
//   • the knob rests at a FIXED home spot bottom-left from the start (always
//     visible, slightly translucent); touch it (or the zone around it) and
//     drag any direction — the knob follows the finger analog-style; release
//     → it springs BACK TO ITS HOME and idles as a translucent ghost. Push
//     past the walk radius = run (nub lights purple).
//   • THE KNOB OWNS ITS POINTER (setPointerCapture): while the move finger is
//     down, EVERY other touch is a world action (tap = chop/attack, hold =
//     place) — moving and acting AT THE SAME TIME works.
//   • pointercancel is handled like pointerup: Android fires it (no matching
//     pointerup) on edge-swipes / palm / gesture arbitration — missing it
//     stranded the stick ON with a dead pointer id (the "joystick drifts by
//     itself / ẻo ẻo" bug).
//   • two-finger pinch → zoom (zoom-IN only: the default is already the
//     widest the camera ever gets).
//   • CAMERA PAN: GONE. Every touch outside the stick is a world action; the
//     camera is follow-only (the pan layer was the desync/jump bug factory).
// The knob feeds the same input-vector hook as the keyboard
// (onMove → setLocalInput + net.setInput), so prediction/seq'd inputs/server
// path are IDENTICAL to desktop. Everything lives in a DOM overlay
// (#mobile-controls), display:none on fine-pointer / wide screens — desktop
// is untouched.

export interface MobileHooks {
  /** Analog movement vector, magnitude 0..1, screen-space (x right, y down).
   *  Magnitude ≥ 1 (rim) maps to run — mirrors the Shift behaviour. */
  onMove: (dx: number, dy: number, running: boolean) => void;
  /** Attack (F equivalent). */
  onAttack: () => void;
  /** Toggle the inventory panel (E/B equivalent). */
  onToggleInventory: () => void;
  /** Tap on the world: primary action at that point (normalized 0..1). */
  onTapWorld: (sx: number, sy: number) => void;
  /** Long press on the world: secondary action at that point (0..1). */
  onLongPressWorld: (sx: number, sy: number) => void;
  /** Pinch zoom: incremental scale vs the gesture start (1.0 = no change). */
  onZoomPinch: (factor: number) => void;
  /** The pinch gesture ended (fingers lifted) — commit the zoom value. */
  onZoomEnd?: () => void;
}

/** Floating knob sizing (CSS px; the knob follows the finger). */
const KNOB_SIZE = 76; // knob diameter on screen (128px art scaled down)
const WALK_RADIUS = 60; // drag distance that maxes the WALK vector
/** px of touch wobble still counted as a stationary long press. */
const LONG_PRESS_SLOP_PX = 22;

export class MobileControls {
  private hooks: MobileHooks;

  // ---- dynamic knob state (the move pointer, owned via capture) ----
  private stickPointer: number | null = null;
  private stickOrigin = { x: 0, y: 0 };
  private stickEl: HTMLElement | null = null;
  private knobEl: HTMLImageElement | null = null;
  private stickRunning = false;
  /** Resting spot (viewport px, set on mount). Release → spring back here. */
  private homeX = 90;
  private homeY = 0; // set in mount() from window size
  /** Re-anchor the home spot on rotate/resize (fullscreen + orientation
   *  lock change the viewport mid-session); re-park the knob when idle. */
  private onResize = (): void => {
    this.homeX = 90;
    this.homeY = this.computeHomeY();
    if (this.stickPointer === null) this.parkKnob();
  };

  /** Home Y measures UP FROM THE BOTTOM edge (~96px above it). The first
   *  version measured from the TOP (innerHeight * 0.22) — on a landscape
   *  phone that planted the knob near the TOP-left while the stick zone
   *  stayed bottom-left: the knob sat in the wrong corner and every grab
   *  missed (the reported "joystick lệch tùm lum"). */
  private computeHomeY(): number {
    return window.innerHeight - Math.max(96, Math.round(window.innerHeight * 0.24));
  }

  // ---- world-action pointer state (taps / long press / pinch) ----
  // Per-pointer tracking so the move finger NEVER interferes: while the
  // stick is captured, world touches arrive here as separate pointer ids.
  private worldTouches = new Map<number, { x: number; y: number; startX: number; startY: number }>();
  private pinchStartDist = 0;
  private pinchStartFactor = 1;
  private longPressTimer: number | null = null;
  private longPressFired = false;
  private worldPointerListener: ((e: PointerEvent) => void) | null = null;

  constructor(hooks: MobileHooks) {
    this.hooks = hooks;
  }

  mount(): void {
    if (document.getElementById("mobile-controls")) return;
    const root = document.createElement("div");
    root.id = "mobile-controls";
    // Built entirely from JS so index.html stays untouched. Art: Kenney
    // Mobile Controls (CC0) — ONE floating round knob (base + purple
    // highlight), circle/hexagon buttons with pressed states, sword/menu
    // icons. NO ring/base around the knob (user spec). The knob RESTS at a
    // fixed home spot from mount (never display:none — it idles translucent).
    root.innerHTML = `
      <img id="mc-knob" src="/ui/mobile/stick_nub.png" alt="" draggable="false" />
      <div id="mc-stick-zone" aria-hidden="true"></div>
      <div id="mc-actions">
        <button class="mc-btn mc-act" id="mc-inv" type="button" aria-label="Túi đồ">
          <img class="mc-act-bg" src="/ui/mobile/btn_hexagon.png" alt="" draggable="false" />
          <img class="mc-act-icon" src="/ui/mobile/icon_menu.png" alt="" draggable="false" />
        </button>
        <button class="mc-btn mc-act" id="mc-atk" type="button" aria-label="Tấn công">
          <img class="mc-act-bg" src="/ui/mobile/btn_circle.png" alt="" draggable="false" />
          <img class="mc-act-icon" src="/ui/mobile/icon_sword.png" alt="" draggable="false" />
        </button>
      </div>
    `;
    document.body.appendChild(root);
    window.addEventListener("resize", this.onResize);
    this.stickEl = document.getElementById("mc-knob");
    this.knobEl = this.stickEl as HTMLImageElement | null;
    // Home spot: bottom-left, above the chat frame (mirrors the CSS zone).
    this.homeX = 90;
    this.homeY = this.computeHomeY();
    // Park the knob at its home spot immediately (visible + idle ghost) so
    // players SEE the joystick from the first frame in-game.
    this.parkKnob();

    // --- dynamic knob: pointerdown in the zone grabs the stick and CAPTURES
    // the pointer; move = analog vector; up/CANCEL releases. With capture,
    // every move event of this pointer flows to the zone regardless of where
    // the finger travels, and all OTHER pointers remain free for taps. ---
    const zone = document.getElementById("mc-stick-zone")!;
    const stickDown = (e: PointerEvent): void => {
      e.preventDefault();
      e.stopPropagation();
      if (this.stickPointer !== null) return; // one stick at a time
      this.stickPointer = e.pointerId;
      // Capture is the whole multi-touch story: (a) the move finger keeps
      // reporting even when it slides over the canvas / out of the zone;
      // (b) other pointers stay untouched → tap-to-attack works WHILE moving.
      try {
        zone.setPointerCapture(e.pointerId);
      } catch {
        /* synthetic pointer (tests/automation) — best effort */
      }
      this.stickOrigin = { x: e.clientX, y: e.clientY };
      if (this.stickEl) {
        this.stickEl.classList.remove("mc-idle");
        this.stickEl.style.left = `${e.clientX - KNOB_SIZE / 2}px`;
        this.stickEl.style.top = `${e.clientY - KNOB_SIZE / 2}px`;
        if (this.knobEl) this.knobEl.style.transform = "translate(0px, 0px)";
      }
      this.updateNub(e.clientX, e.clientY);
    };
    const stickLift = (e: PointerEvent): void => {
      if (e.pointerId !== this.stickPointer) return;
      this.stickPointer = null;
      this.stickRunning = false;
      // Spring back HOME (not wherever the finger was) + idle ghost opacity.
      this.parkKnob();
      if (this.knobEl) this.knobEl.src = "/ui/mobile/stick_nub.png";
      this.hooks.onMove(0, 0, false);
    };
    zone.addEventListener("pointerdown", stickDown);
    zone.addEventListener("pointermove", (e) => {
      if (e.pointerId !== this.stickPointer) return;
      e.preventDefault();
      this.updateNub(e.clientX, e.clientY);
    });
    // pointercancel is NOT optional: Android delivers it without a matching
    // pointerup (edge gesture, palm, incoming call, browser gesture
    // arbitration). Treating it as a lift prevents the stick from being
    // stranded ON with a dead pointer id (infinite walking / drift).
    zone.addEventListener("pointerup", stickLift);
    zone.addEventListener("pointercancel", stickLift);
    zone.addEventListener("lostpointercapture", stickLift);

    // --- Action buttons: swap bg to the pressed sprite while held ---
    const bindAction = (id: string, cb: () => void): void => {
      const btn = document.getElementById(id)!;
      const bg = btn.querySelector(".mc-act-bg") as HTMLImageElement;
      const base = bg.src;
      const pressed = base.replace(/\.png$/, "_pressed.png");
      btn.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        bg.src = pressed;
        cb();
      });
      const lift = (): void => {
        bg.src = base;
      };
      btn.addEventListener("pointerup", lift);
      btn.addEventListener("pointercancel", lift);
      btn.addEventListener("pointerleave", lift);
    };
    bindAction("mc-atk", () => this.hooks.onAttack());
    bindAction("mc-inv", () => this.hooks.onToggleInventory());

    // --- WORLD ACTIONS: taps / long-press / pinch anywhere on the game
    // surface. One document-level capture listener (NOT a full-screen DOM
    // sheet — that layer was what blocked taps at the screen edges and
    // fought the hub/inventory). The stick zone and buttons sit above the
    // canvas and stopPropagation, so they never double-fire. ---
    const dist = (): number => {
      const pts = [...this.worldTouches.values()];
      return Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
    };
    const clearLongPress = (): void => {
      if (this.longPressTimer !== null) {
        window.clearTimeout(this.longPressTimer);
        this.longPressTimer = null;
      }
    };
    const onWorldPointer = (e: PointerEvent): void => {
      const t = e.target as Element | null;
      // ONLY the game canvas counts as "the world" (allowlist, not a
      // blacklist of UI ids): any DOM UI (hub reel, inventory, chat, gate)
      // handles itself and never spawns a world action. The stick zone and
      // the knob live OUTSIDE #game-root, so they are excluded here too;
      // the knob itself is pointer-events:none (target passes through to
      // the canvas — fine, the zone owns that pointer anyway).
      if (!(t && t.closest("#game-root"))) return;
      if (e.type === "pointerdown") {
        // The move finger NEVER seeds a world action (it is captured by the
        // zone anyway, but guard the synthetic-capture fallback too).
        if (e.pointerId === this.stickPointer) return;
        this.longPressFired = false;
        this.worldTouches.set(e.pointerId, {
          x: e.clientX, y: e.clientY, startX: e.clientX, startY: e.clientY,
        });
        if (this.worldTouches.size === 1) {
          // Single finger: arm a long press (secondary action) unless it
          // becomes a drag or a second finger lands.
          clearLongPress();
          const pid = e.pointerId;
          this.longPressTimer = window.setTimeout(() => {
            const tt = this.worldTouches.get(pid);
            if (!tt || this.worldTouches.size !== 1) return;
            const moved = Math.hypot(tt.x - tt.startX, tt.y - tt.startY);
            // Separate (wider) slop for the long press: a wobbling finger
            // must still count as "holding still"; only a real drag cancels.
            if (moved > LONG_PRESS_SLOP_PX) return;
            this.longPressFired = true;
            this.hooks.onLongPressWorld(tt.x / window.innerWidth, tt.y / window.innerHeight);
          }, 450);
        } else {
          clearLongPress(); // two fingers = pinch, never a long press
          if (this.worldTouches.size === 2) {
            this.pinchStartDist = dist();
            this.pinchStartFactor = 1;
          }
        }
        return;
      }
      if (e.type === "pointermove") {
        const prev = this.worldTouches.get(e.pointerId);
        if (!prev) return;
        this.worldTouches.set(e.pointerId, { ...prev, x: e.clientX, y: e.clientY });
        if (this.worldTouches.size === 1) {
          // Slid past the tap slop: it's a drag — kill the pending long
          // press. (Drags no longer pan anything: the camera follows.)
          const moved = Math.hypot(e.clientX - prev.startX, e.clientY - prev.startY);
          if (moved > 14) clearLongPress();
        } else if (this.worldTouches.size === 2 && this.pinchStartDist > 0) {
          // Feed incremental pinch scale: each move reports the cumulative
          // ratio vs gesture start; main.ts multiplies into absolute zoom.
          this.hooks.onZoomPinch((dist() / this.pinchStartDist) * this.pinchStartFactor);
        }
        return;
      }
      // pointerup / pointercancel
      const tt = this.worldTouches.get(e.pointerId);
      clearLongPress();
      if (!tt) return;
      this.worldTouches.delete(e.pointerId);
      if (this.worldTouches.size < 2) {
        if (this.pinchStartDist > 0) this.hooks.onZoomEnd?.();
        this.pinchStartDist = 0;
      }
      // Quick tap (single finger, barely moved, no long press fired) = the
      // desktop LEFT CLICK at that point — chop/break/attack under the tile.
      if (
        !this.longPressFired &&
        e.type === "pointerup" &&
        this.worldTouches.size === 0 &&
        Math.hypot(tt.x - tt.startX, tt.y - tt.startY) <= 14
      ) {
        this.hooks.onTapWorld(tt.x / window.innerWidth, tt.y / window.innerHeight);
      }
    };
    this.worldPointerListener = onWorldPointer;
    // capture=true: see the events BEFORE Phaser's input manager can act on
    // them; Phaser still receives them (we never stopPropagation world taps).
    document.addEventListener("pointerdown", onWorldPointer, true);
    document.addEventListener("pointermove", onWorldPointer, true);
    document.addEventListener("pointerup", onWorldPointer, true);
    document.addEventListener("pointercancel", onWorldPointer, true);
  }

  /** Return the knob to its home spot (bottom-left) as an idle ghost. */
  private parkKnob(): void {
    if (!this.stickEl) return;
    this.stickEl.classList.add("mc-idle");
    this.stickEl.style.left = `${this.homeX - KNOB_SIZE / 2}px`;
    this.stickEl.style.top = `${this.homeY - KNOB_SIZE / 2}px`;
    if (this.knobEl) this.knobEl.style.transform = "translate(0px, 0px)";
  }

  /** Recompute the knob offset + emit the analog move vector. Drag model:
   *  0..WALK_RADIUS = walk speed ramp (0..1), beyond = RUN (Shift
   *  equivalent — full walk vector + running flag). The knob follows the
   *  finger up to the run radius, then clamps at the rim; at run the knob
   *  lights purple. */
  private updateNub(x: number, y: number): void {
    let dx = x - this.stickOrigin.x;
    let dy = y - this.stickOrigin.y;
    const len = Math.hypot(dx, dy);
    const clamped = Math.min(len, WALK_RADIUS * 2);
    if (len > 0) {
      dx = (dx / len) * clamped;
      dy = (dy / len) * clamped;
    }
    if (this.knobEl) {
      this.knobEl.style.transform = `translate(${dx}px, ${dy}px)`;
    }
    // Walk vector: 0..1 across WALK_RADIUS. Past it the player is running:
    // vector stays at 1 and the running flag turns on (exactly the keyboard
    // Shift behaviour — run never multiplies the vector, only the
    // server-side speed).
    const running = len >= WALK_RADIUS;
    const mag = Math.min(1, len / WALK_RADIUS);
    if (running !== this.stickRunning) {
      this.stickRunning = running;
      if (this.knobEl) {
        this.knobEl.src = running ? "/ui/mobile/stick_nub_hl.png" : "/ui/mobile/stick_nub.png";
      }
    }
    // Normalize to the hook's contract: vector with magnitude 0..1.
    const nx = len > 0 ? (dx / len) * mag : 0;
    const ny = len > 0 ? (dy / len) * mag : 0;
    this.hooks.onMove(nx, ny, running);
  }

  /** Clear movement + ghost the knob (death / tab switch parity). */
  clear(): void {
    this.stickPointer = null;
    this.stickRunning = false;
    this.stickEl?.classList.add("mc-idle");
    if (this.knobEl) {
      this.knobEl.style.transform = "translate(0px, 0px)";
      this.knobEl.src = "/ui/mobile/stick_nub.png";
    }
    this.worldTouches.clear();
    this.hooks.onMove(0, 0, false);
  }

  /** Drop the whole DOM (kept symmetrical with mount). */
  destroy(): void {
    window.removeEventListener("resize", this.onResize);
    if (this.worldPointerListener) {
      document.removeEventListener("pointerdown", this.worldPointerListener, true);
      document.removeEventListener("pointermove", this.worldPointerListener, true);
      document.removeEventListener("pointerup", this.worldPointerListener, true);
      document.removeEventListener("pointercancel", this.worldPointerListener, true);
      this.worldPointerListener = null;
    }
    document.getElementById("mobile-controls")?.remove();
    this.stickEl = null;
    this.knobEl = null;
  }
}
