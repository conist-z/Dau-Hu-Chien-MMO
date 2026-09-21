// Mobile controls: on-screen D-pad (8-direction) + action buttons + a
// full-screen LOOK surface for the camera:
//   • one finger drag  → pan the camera (re-centers while you walk)
//   • two finger pinch → zoom (drives the Phaser camera zoom directly)
//   • quick tap        → the SAME "primary" action as a desktop left click
//                        (chop / break / attack at that tile)
//   • long press       → the SAME "secondary" action as a right click
//                        (place block / eat / station interact)
// Everything lives in a DOM overlay (#mobile-controls); the D-pad drives the
// exact same direction keys as the keyboard, so desktop behaviour is
// untouched (the layer is display:none on fine-pointer / wide screens).

export interface MobileHooks {
  /** Direction key press/release — the SAME hook KeyboardInput uses. */
  setDir: (key: "up" | "down" | "left" | "right", on: boolean) => void;
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
  /** Run toggle (Shift equivalent) latched on/off. */
  onRunToggle?: (running: boolean) => void;
}

/** Which single-direction keys a D-pad button toggles. */
const PAD_KEYS: Record<string, Array<"up" | "down" | "left" | "right">> = {
  up: ["up"],
  down: ["down"],
  left: ["left"],
  right: ["right"],
  // Diagonals press TWO keys at once (8-way movement from 4 DOM buttons).
  "up-left": ["up", "left"],
  "up-right": ["up", "right"],
  "down-left": ["down", "left"],
  "down-right": ["down", "right"],
};

/** ms a finger must stay (and stay still) to count as a long press. */
const LONG_PRESS_MS = 450;
/** px of movement that cancels a pending tap/long-press (it's a drag). */
const TAP_SLOP_PX = 12;

export class MobileControls {
  private hooks: MobileHooks;
  /** Directions currently held (by any pad button). */
  private held = new Set<"up" | "down" | "left" | "right">();
  private running = false;

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
    // Built entirely from JS so index.html stays untouched (desktop builds
    // never need the markup). The hotbar on screen IS the real HUD hotbar —
    // the 1..8 mini row was dropped (duplicate targets, wasted thumb space).
    root.innerHTML = `
      <div id="mc-look" aria-hidden="true"></div>
      <div id="mc-pad">
        <button class="mc-btn mc-diag" data-dir="up-left" type="button" aria-label="Lên trái">◤</button>
        <button class="mc-btn mc-dir" data-dir="up" type="button" aria-label="Lên">▲</button>
        <button class="mc-btn mc-diag" data-dir="up-right" type="button" aria-label="Lên phải">◥</button>
        <button class="mc-btn mc-dir" data-dir="left" type="button" aria-label="Trái">◀</button>
        <button class="mc-btn mc-run" id="mc-run" type="button" aria-label="Chạy">⚡</button>
        <button class="mc-btn mc-dir" data-dir="right" type="button" aria-label="Phải">▶</button>
        <button class="mc-btn mc-diag" data-dir="down-left" type="button" aria-label="Xuống trái">◣</button>
        <button class="mc-btn mc-dir" data-dir="down" type="button" aria-label="Xuống">▼</button>
        <button class="mc-btn mc-diag" data-dir="down-right" type="button" aria-label="Xuống phải">◢</button>
      </div>
      <div id="mc-actions">
        <button class="mc-btn mc-act" id="mc-inv" type="button" aria-label="Túi đồ">🎒</button>
        <button class="mc-btn mc-act" id="mc-atk" type="button" aria-label="Tấn công">⚔️</button>
      </div>
    `;
    document.body.appendChild(root);

    // --- D-pad: pointer events so touch + mouse behave identically ---
    const press = (dir: string): void => {
      for (const k of PAD_KEYS[dir] ?? []) {
        if (!this.held.has(k)) {
          this.held.add(k);
          this.hooks.setDir(k, true);
        }
      }
    };
    const release = (dir: string): void => {
      for (const k of PAD_KEYS[dir] ?? []) {
        if (this.held.delete(k)) this.hooks.setDir(k, false);
      }
    };
    root.querySelectorAll<HTMLElement>("[data-dir]").forEach((btn) => {
      const dir = btn.dataset.dir!;
      // pointerdown/up + cancel + lostpointercapture cover every lift path
      // (gliding off the button edge must release, or the player walks forever).
      btn.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        btn.classList.add("active");
        btn.setPointerCapture(e.pointerId);
        press(dir);
      });
      const lift = (): void => {
        btn.classList.remove("active");
        release(dir);
      };
      btn.addEventListener("pointerup", lift);
      btn.addEventListener("pointercancel", lift);
      btn.addEventListener("lostpointercapture", lift);
    });
    // Run toggle (Shift equivalent) — latched; a tap is friendlier on touch
    // than holding a second button while steering with the same thumb.
    const runBtn = document.getElementById("mc-run")!;
    runBtn.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      this.running = !this.running;
      runBtn.classList.toggle("active", this.running);
      this.hooks.onRunToggle?.(this.running);
    });

    // --- Action buttons ---
    document.getElementById("mc-atk")!.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      this.hooks.onAttack();
    });
    document.getElementById("mc-inv")!.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      this.hooks.onToggleInventory();
    });

    // --- LOOK surface: covers the WHOLE screen so any free spot is usable;
    // pad + buttons sit above it and capture their own touches first. ---
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

  /** Clear all held directions (death / tab switch parity with clearKeys). */
  clear(): void {
    for (const k of this.held) this.hooks.setDir(k, false);
    this.held.clear();
    this.running = false;
    document.getElementById("mc-run")?.classList.remove("active");
    this.hooks.onRunToggle?.(false);
  }

  /** Drop the whole DOM (kept symmetrical with mount). */
  destroy(): void {
    document.getElementById("mobile-controls")?.remove();
  }
}
