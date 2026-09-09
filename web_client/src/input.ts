// Keyboard input: WASD/arrows -> movement vector, Shift -> run, E -> toggle
// the inventory panel, F / left-click -> attack, 1..8 -> hotbar select.
// Input is captured on the window; the chat box stops propagation so typing
// never moves the player.

export interface InputHooks {
  onVector: (dx: number, dy: number, running: boolean) => void;
  onAttack: () => void;
  onToggleInventory: () => void;
  onSlot: (index: number) => void;
  onChatFocus: () => boolean; // true while the chat input has focus
  /** Canvas clicks: "primary" = chop/break/mine, "secondary" = place block. */
  onCanvasAction?: (kind: "primary" | "secondary", sx: number, sy: number) => void;
  /** Mouse hover over the canvas (normalized; -1,-1 = left). */
  onCanvasHover?: (sx: number, sy: number) => void;
}

const MOVE_KEYS: Record<string, [number, number]> = {
  KeyW: [0, -1], ArrowUp: [0, -1],
  KeyS: [0, 1], ArrowDown: [0, 1],
  KeyA: [-1, 0], ArrowLeft: [-1, 0],
  KeyD: [1, 0], ArrowRight: [1, 0],
};

export class KeyboardInput {
  private keys = new Set<string>();
  private hooks: InputHooks;
  private running = false;
  private enabled = true;

  constructor(hooks: InputHooks) {
    this.hooks = hooks;
    window.addEventListener("keydown", (e) => this.onKeyDown(e));
    window.addEventListener("keyup", (e) => this.onKeyUp(e));
    window.addEventListener("blur", () => {
      this.keys.clear();
      this.emit();
    });
  }

  setEnabled(on: boolean): void {
    this.enabled = on;
    if (!on) {
      this.keys.clear();
      this.emit();
    }
  }

  /** Drop all held keys (tab-switch hygiene) without disabling input. */
  clearKeys(): void {
    this.keys.clear();
    this.running = false;
    this.emit();
  }

  private isChatFocused(): boolean {
    return this.hooks.onChatFocus();
  }

  private onKeyDown(e: KeyboardEvent): void {
    if (this.isChatFocused() || !this.enabled) return;
    if (e.repeat) {
      if (MOVE_KEYS[e.code]) e.preventDefault();
      return;
    }
    if (e.code === "KeyE") {
      e.preventDefault();
      this.hooks.onToggleInventory();
      return;
    }
    if (e.code === "KeyF") {
      e.preventDefault();
      this.hooks.onAttack();
      return;
    }
    if (/^Digit[1-8]$/.test(e.code)) {
      e.preventDefault();
      this.hooks.onSlot(Number(e.code.slice(5)) - 1);
      return;
    }
    if (MOVE_KEYS[e.code]) {
      e.preventDefault();
      this.keys.add(e.code);
      this.emit();
    }
    if (e.code === "ShiftLeft" || e.code === "ShiftRight") {
      this.running = true;
      this.emit();
    }
  }

  private onKeyUp(e: KeyboardEvent): void {
    if (e.code === "ShiftLeft" || e.code === "ShiftRight") {
      this.running = false;
      this.emit();
      return;
    }
    if (this.keys.delete(e.code)) {
      this.emit();
    }
  }

  private emit(): void {
    let dx = 0;
    let dy = 0;
    for (const code of this.keys) {
      const [kx, ky] = MOVE_KEYS[code] ?? [0, 0];
      dx += kx;
      dy += ky;
    }
    // Normalize diagonals so running diagonally isn't faster.
    const len = Math.hypot(dx, dy);
    if (len > 1) {
      dx /= len;
      dy /= len;
    }
    this.hooks.onVector(dx, dy, this.running);
  }

  /** Canvas mouse bindings: click actions + hover tracking. */
  bindCanvas(canvas: HTMLCanvasElement): void {
    const canvasPosition = (e: MouseEvent): [number, number] => {
      const rect = canvas.getBoundingClientRect();
      return [
        (e.clientX - rect.left) / rect.width,
        (e.clientY - rect.top) / rect.height,
      ];
    };

    canvas.addEventListener("mousedown", (e) => {
      if (this.isChatFocused() || !this.enabled) return;
      // A click can arrive before the first mousemove (especially after
      // opening the game or switching tabs). Keep the hover tile in sync so
      // actions never see a missing/stale mouse cell.
      const [sx, sy] = canvasPosition(e);
      this.hooks.onCanvasHover?.(sx, sy);
      if (e.button === 0) {
        this.hooks.onCanvasAction?.("primary", sx, sy);
      } else if (e.button === 2) {
        this.hooks.onCanvasAction?.("secondary", sx, sy);
      }
    });
    canvas.addEventListener("mousemove", (e) => {
      const [sx, sy] = canvasPosition(e);
      this.hooks.onCanvasHover?.(sx, sy);
    });
    canvas.addEventListener("mouseleave", () => this.hooks.onCanvasHover?.(-1, -1));
    canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  }
}
