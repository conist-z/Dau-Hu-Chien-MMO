// Mobile controls: ROBLOX-STYLE DYNAMIC D-PAD (Kenney CC0 art) + action
// buttons + a full-screen LOOK surface for the camera:
//   • touch the bottom-left zone → the pixel-art cross D-pad appears UNDER
//     the finger and follows it; drag in ANY direction for analog movement
//     (arm lights purple when its direction is active; push far = run)
//   • one finger drag (elsewhere) → pan the camera (re-centers while walking)
//   • two finger pinch → zoom (drives the Phaser camera zoom directly)
//   • quick tap        → the SAME "primary" action as a desktop left click
//                        (chop / break / attack at that tile)
//   • long press       → the SAME "secondary" action as a right click
//                        (place block / eat / station interact)
// The D-pad feeds the exact same input-vector hook as the keyboard
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

/** D-pad sizing (CSS px; the dynamic pad follows the finger). */
const PAD_SIZE = 150; // on-screen size of the 256px art square
const MOVE_RADIUS = PAD_SIZE * 0.55; // drag distance that maxes the vector
const RUN_THRESHOLD = 0.95; // |vector| treated as run

/** Which arm image(s) light up for the current drag direction. The drag
 *  angle (atan2, degrees, -180..180) is divided into 45° sectors indexed
 *  from EAST counter-clockwise-positive... in screen space (y down) positive
 *  angles sweep CLOCKWISE on screen (down first). sector index:
 *  0=E, 1=SE, 2=S, 3=SW, 4(or -4)=W, -3=NW, -2=N, -1=NE. */
const SECTOR_ARMS: string[][] = [
  ["e"],        //   0°  → E
  ["e", "s"],   //  45°  → SE (down-right on screen)
  ["s"],        //  90°  → S
  ["s", "w"],   // 135°  → SW
  ["w"],        // 180°  → W
  ["n", "w"],   // -135° → NW
  ["n"],        //  -90° → N (straight up)
  ["n", "e"],   //  -45° → NE
];

/** Map a drag angle (deg) to the arms to light. */
function armsForAngle(angleDeg: number): string[] {
  // Math.round(angle/45): 0°→E, 45°→SE, 90°→S, … −90°→N. JS rounds .5 ties
  // toward +∞, so a boundary drag at exactly 22.5° leans SE — harmless.
  const idx = Math.round(angleDeg / 45) % 8;
  const norm = (idx + 8) % 8; // wrap -4..-1 → 4..7
  return SECTOR_ARMS[norm];
}

/** ms a finger must stay (and stay still) to count as a long press. */
const LONG_PRESS_MS = 450;
/** px of movement that cancels a pending tap/long-press (it's a drag). */
const TAP_SLOP_PX = 12;

export class MobileControls {
  private hooks: MobileHooks;

  // ---- dynamic D-pad state (one active pointer at a time) ----
  private stickPointer: number | null = null;
  private stickOrigin = { x: 0, y: 0 };
  private stickEl: HTMLElement | null = null;
  private stickRunning = false;

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
    // Mobile Controls (CC0) — cross D-pad (body + 4 arm elements, base +
    // purple highlight each), circle/hexagon buttons with pressed states,
    // sword/menu icons. The pad is hidden until a touch lands in the
    // bottom-left zone (#mc-stick-zone), then follows the finger (Roblox).
    root.innerHTML = `
      <div id="mc-look" aria-hidden="true"></div>
      <div id="mc-stick-zone" aria-hidden="true"></div>
      <div id="mc-stick" class="hidden">
        <img class="mc-pad-base" src="/ui/mobile/dpad_body.png" alt="" draggable="false" />
        <img class="mc-arm" id="mc-el-n" src="/ui/mobile/dpad_n.png" alt="" draggable="false" />
        <img class="mc-arm" id="mc-el-s" src="/ui/mobile/dpad_s.png" alt="" draggable="false" />
        <img class="mc-arm" id="mc-el-w" src="/ui/mobile/dpad_w.png" alt="" draggable="false" />
        <img class="mc-arm" id="mc-el-e" src="/ui/mobile/dpad_e.png" alt="" draggable="false" />
      </div>
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
    this.stickEl = document.getElementById("mc-stick");

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
        this.stickEl.classList.remove("hidden");
        this.stickEl.style.left = `${e.clientX - PAD_SIZE / 2}px`;
        this.stickEl.style.top = `${e.clientY - PAD_SIZE / 2}px`;
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
      this.stickEl?.classList.add("hidden");
      this.setLitArms([]);
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

  /** Light/unlight the cross arms to match the drag direction. Stateless:
   *  every arm's src is set unconditionally (4 cheap writes) — no diff
   *  tracking to drift out of sync (the stuck-lit-arm bug). */
  private setLitArms(arms: string[]): void {
    const lit = new Set(arms);
    for (const arm of ["n", "s", "w", "e"]) {
      const img = document.getElementById(`mc-el-${arm}`) as HTMLImageElement | null;
      if (img) {
        const want = `/ui/mobile/dpad_${arm}${lit.has(arm) ? "_hl" : ""}.png`;
        if (!img.src.endsWith(want)) img.src = want;
      }
    }
  }

  /** Recompute the analog vector from the drag + light the cross arms. */
  private updateNub(x: number, y: number): void {
    let dx = x - this.stickOrigin.x;
    let dy = y - this.stickOrigin.y;
    const len = Math.hypot(dx, dy);
    // Magnitude 0..1 over MOVE_RADIUS — everything beyond is full speed.
    const mag = Math.min(1, len / MOVE_RADIUS);
    const running = mag >= RUN_THRESHOLD;
    if (len > 0) {
      const angle = (Math.atan2(dy, dx) * 180) / Math.PI; // -180..180
      this.setLitArms(mag > 0.18 ? armsForAngle(angle) : []);
    } else {
      this.setLitArms([]);
    }
    if (running !== this.stickRunning) this.stickRunning = running;
    // Normalize to the hook's contract: vector with magnitude 0..1.
    const nx = len > 0 ? (dx / len) * mag : 0;
    const ny = len > 0 ? (dy / len) * mag : 0;
    this.hooks.onMove(nx, ny, running);
  }

  /** Clear movement + hide the pad (death / tab switch parity). */
  clear(): void {
    this.stickPointer = null;
    this.stickRunning = false;
    this.stickEl?.classList.add("hidden");
    this.setLitArms([]);
    this.hooks.onMove(0, 0, false);
  }

  /** Drop the whole DOM (kept symmetrical with mount). */
  destroy(): void {
    document.getElementById("mobile-controls")?.remove();
    this.stickEl = null;
  }
}
