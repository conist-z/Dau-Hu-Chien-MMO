// Mobile controls: HOME-ANCHORED FLOATING KNOB joystick (Kenney CC0 art)
// + action buttons + a full-screen LOOK surface for the camera:
//   • the knob rests at a FIXED home spot bottom-left from the start (always
//     visible, slightly translucent); touch it (or the zone around it) and
//     drag any direction — the knob follows the finger analog-style; release
//     → it springs BACK TO ITS HOME (not wherever the finger was) and idles
//     as a translucent ghost. Push past 2× range = run (nub lights purple).
//   • one finger drag (elsewhere) → pan the camera (re-centers while walking)
//   • two finger pinch → zoom (drives the Phaser camera zoom directly)
//   • quick tap        → the SAME "primary" action as a desktop left click
//                        (chop / break / attack / MINE at that tile)
//   • long press       → the SAME "secondary" action as a right click
//                        (PLACE the held block / eat / station interact)
//   • 🎒 button        → inventory panel (drag items there like on desktop)
// The knob feeds the exact same input-vector hook as the keyboard
// (onMove → setLocalInput + net.setInput), so prediction/seq'd inputs/server
// path are IDENTICAL to desktop. Everything lives in a DOM overlay
// (#mobile-controls), display:none on fine-pointer / wide screens — desktop
// is untouched.

export interface MobileHooks {
  /** Analog movement vector, magnitude 0..1, screen-space (x right, y down).
   *  Magnitude > ~0.95 (rim) maps to run — mirrors the Shift behaviour. */
  onMove: (dx: number, dy: number, running: boolean) => void;
  /** Attack (F equivalent). */
  onAttack: () => void;
  /** Toggle the inventory panel (E/B equivalent). */
  onToggleInventory: () => void;
  /** Tap on the world: primary action at that point (normalized 0..1). */
  onTapWorld: (sx: number, sy: number) => void;
  /** Long press on the world: secondary action at that point (0..1). */
  onLongPressWorld: (sx: number, sy: number) => void;
  /** Pinch zoom: absolute scale vs the gesture start (1.0 = no change). */
  onZoomPinch: (factor: number) => void;
  /** Single-finger drag on the look area: camera pan in screen px. */
  onDragLook?: (dx: number, dy: number) => void;
}

/** Floating knob sizing (CSS px; the knob follows the finger). */
const KNOB_SIZE = 76; // knob diameter on screen (128px art scaled down)
const WALK_RADIUS = 60; // drag distance that maxes the WALK vector
const RUN_RADIUS = WALK_RADIUS * 2; // beyond this = RUN (Shift equivalent)

/** ms a finger must stay (and stay still) to count as a long press. */
const LONG_PRESS_MS = 450;
/** px of movement that cancels a pending tap/long-press (it's a drag). */
const TAP_SLOP_PX = 12;

export class MobileControls {
  private hooks: MobileHooks;

  // ---- dynamic knob state (one active pointer at a time) ----
  private stickPointer: number | null = null;
  private stickOrigin = { x: 0, y: 0 };
  private stickEl: HTMLElement | null = null;
  private knobEl: HTMLImageElement | null = null;
  private stickRunning = false;
  /** Resting spot (viewport px, set on mount). Release → spring back here. */
  private homeX = 90;
  private homeY = 0; // set in mount() from window size

  // look-area state (multi-touch: tracked per pointer id)
  private lookTouches = new Map<number, { x: number; y: number; startX: number; startY: number }>();
  private pinchStartDist = 0;
  private pinchStartFactor = 1;
  private longPressTimer: number | null = null;
  private longPressFired = false;

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
      <div id="mc-look" aria-hidden="true"></div>
      <div id="mc-stick-zone" aria-hidden="true"></div>
      <img id="mc-knob" src="/ui/mobile/stick_nub.png" alt="" draggable="false" />
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
    this.stickEl = document.getElementById("mc-knob");
    this.knobEl = this.stickEl as HTMLImageElement | null;
    // Home spot: bottom-left, above the chat frame (mirrors the CSS zone).
    this.homeX = 90;
    this.homeY = Math.max(90, Math.round(window.innerHeight * 0.22));
    // Park the knob at its home spot immediately (visible + idle ghost) so
    // players SEE the joystick from the first frame in-game.
    this.parkKnob();

    // --- dynamic D-pad: pointerdown in the zone spawns the pad under the
    // finger; move = analog vector + arm lighting; up/cancel releases. ---
    const zone = document.getElementById("mc-stick-zone")!;
    zone.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      if (this.stickPointer !== null) return; // one stick at a time
      this.stickPointer = e.pointerId;
      // Capture keeps pointermove flowing when the finger slides off the
      // zone (onto the canvas). Synthetic events (tests/automation) have no
      // active pointer — capture failures must not break the stick.
      try {
        zone.setPointerCapture(e.pointerId);
      } catch {
        /* synthetic pointer — moves will still fire while over the zone */
      }
      this.stickOrigin = { x: e.clientX, y: e.clientY };
      if (this.stickEl) {
        this.stickEl.classList.remove("mc-idle");
        this.stickEl.style.left = `${e.clientX - KNOB_SIZE / 2}px`;
        this.stickEl.style.top = `${e.clientY - KNOB_SIZE / 2}px`;
        if (this.knobEl) this.knobEl.style.transform = "translate(0px, 0px)";
      }
      this.updateNub(e.clientX, e.clientY);
    });
    zone.addEventListener("pointermove", (e) => {
      if (e.pointerId !== this.stickPointer) return;
      this.updateNub(e.clientX, e.clientY);
    });
    const stickLift = (e: PointerEvent): void => {
      if (e.pointerId !== this.stickPointer) return;
      this.stickPointer = null;
      this.stickRunning = false;
      // Spring back HOME (not wherever the finger was) + idle ghost opacity.
      this.parkKnob();
      if (this.knobEl) this.knobEl.src = "/ui/mobile/stick_nub.png";
      this.hooks.onMove(0, 0, false);
    };
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

    // --- LOOK surface: covers the WHOLE screen so any free spot is usable;
    // stick zone + buttons sit above it and capture their own touches. ---
    const look = document.getElementById("mc-look")!;
    const dist = (): number => {
      const pts = [...this.lookTouches.values()];
      return Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
    };
    const clearLongPress = (): void => {
      if (this.longPressTimer !== null) {
        window.clearTimeout(this.longPressTimer);
        this.longPressTimer = null;
      }
    };
    look.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      this.longPressFired = false;
      this.lookTouches.set(e.pointerId, { x: e.clientX, y: e.clientY, startX: e.clientX, startY: e.clientY });
      look.setPointerCapture(e.pointerId);
      if (this.lookTouches.size === 1) {
        // Single finger: arm a long press (secondary action) unless it
        // becomes a drag (moved too far) or a second finger lands.
        clearLongPress();
        this.longPressTimer = window.setTimeout(() => {
          const t = this.lookTouches.get(e.pointerId);
          if (!t || this.lookTouches.size !== 1) return;
          const moved = Math.hypot(t.x - t.startX, t.y - t.startY);
          if (moved > TAP_SLOP_PX) return;
          this.longPressFired = true;
          this.hooks.onLongPressWorld(t.x / window.innerWidth, t.y / window.innerHeight);
        }, LONG_PRESS_MS);
      } else {
        clearLongPress(); // two fingers = pinch, never a long press
        if (this.lookTouches.size === 2) {
          this.pinchStartDist = dist();
          this.pinchStartFactor = 1;
        }
      }
    });
    look.addEventListener("pointermove", (e) => {
      const prev = this.lookTouches.get(e.pointerId);
      if (!prev) return;
      const dx = e.clientX - prev.x;
      const dy = e.clientY - prev.y;
      this.lookTouches.set(e.pointerId, { ...prev, x: e.clientX, y: e.clientY });
      if (this.lookTouches.size === 1) {
        // Slid past the tap slop: it's a drag — kill the pending long press.
        const moved = Math.hypot(
          e.clientX - prev.startX,
          e.clientY - prev.startY,
        );
        if (moved > TAP_SLOP_PX) clearLongPress();
        this.hooks.onDragLook?.(dx, dy);
      } else if (this.lookTouches.size === 2 && this.pinchStartDist > 0) {
        this.hooks.onZoomPinch(this.pinchStartFactor * (dist() / this.pinchStartDist));
      }
    });
    const lookLift = (e: PointerEvent): void => {
      const t = this.lookTouches.get(e.pointerId);
      clearLongPress();
      if (!t) return;
      this.lookTouches.delete(e.pointerId);
      if (this.lookTouches.size < 2) this.pinchStartDist = 0;
      // Quick tap (single finger, barely moved, no long press fired) = the
      // desktop LEFT CLICK at that point — chop/break/attack under the tile.
      if (
        !this.longPressFired &&
        this.lookTouches.size === 0 &&
        Math.hypot(t.x - t.startX, t.y - t.startY) <= TAP_SLOP_PX
      ) {
        this.hooks.onTapWorld(t.x / window.innerWidth, t.y / window.innerHeight);
      }
    };
    look.addEventListener("pointerup", lookLift);
    look.addEventListener("pointercancel", lookLift);
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
   *  0..WALK_RADIUS = walk speed ramp (0..1), WALK_RADIUS..RUN_RADIUS =
   *  RUN (Shift equivalent — full walk vector + running flag). The knob
   *  follows the finger up to RUN_RADIUS, then clamps at the rim. At run
   *  the knob lights purple. */
  private updateNub(x: number, y: number): void {
    let dx = x - this.stickOrigin.x;
    let dy = y - this.stickOrigin.y;
    const len = Math.hypot(dx, dy);
    const clamped = Math.min(len, RUN_RADIUS);
    if (len > 0) {
      dx = (dx / len) * clamped;
      dy = (dy / len) * clamped;
    }
    if (this.knobEl) {
      this.knobEl.style.transform = `translate(${dx}px, ${dy}px)`;
    }
    // Walk vector: 0..1 across WALK_RADIUS. Past WALK_RADIUS the player is
    // running: vector stays at 1 and the running flag turns on (exactly the
    // keyboard Shift behaviour — run never multiplies the vector, only the
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
    this.hooks.onMove(0, 0, false);
  }

  /** Drop the whole DOM (kept symmetrical with mount). */
  destroy(): void {
    document.getElementById("mobile-controls")?.remove();
    this.stickEl = null;
    this.knobEl = null;
  }
}
