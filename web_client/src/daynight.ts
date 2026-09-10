// Day/night lighting overlay for the web client — mirrors the Discord client's
// rendering/daynight.py visual identity: the whole viewport is multiplied by
// the in-game clock colour sampled from the SAME 24h gradient stops
// (night -> dawn -> day -> dusk -> night, ported from the Godot
// daynightcycle2d addon). Zero game-logic deps (rules 2/3): pure visual.
//
// The tint is two layers painted on a plain canvas ABOVE the Phaser canvas
// (below the HUD, same slot as the weather layer): a monochrome ambient
// multiply (brightness only — never desaturates) plus a saturated period hue
// overlay (violet dawn / gold noon / orange dusk / indigo night). The in-game
// clock second-of-day arrives in every 20 Hz snapshot (snap.clock) and is
// interpolated locally so the lighting drifts smoothly instead of stepping
// 20 times per second.

export const SECONDS_PER_DAY = 86400;

// Gradient stops (RGB 0-255), verbatim from DayNightCanvasModulate.gd via
// rendering/daynight.py (_C_NIGHT/_C_DAWN/_C_DAY/_C_DUSK + _GRADIENT points).
const C_NIGHT = [0x27, 0x26, 0x4c];
const C_DAWN = [0x49, 0x46, 0x88];
const C_DAY = [0xff, 0xf1, 0xd0];
const C_DUSK = [0x85, 0x46, 0x46];

const DAWN_S = 6 * 3600;
const DUSK_S = 21 * 3600;

// (fraction_of_day, [r, g, b]) — mirrors rendering.daynight._GRADIENT.
const GRADIENT: [number, number[]][] = [
  [0.0, C_NIGHT],
  [(DAWN_S - 3600) / SECONDS_PER_DAY, C_NIGHT],
  [(DAWN_S + 3600) / SECONDS_PER_DAY, C_DAWN],
  [0.5, C_DAY],
  [(DUSK_S - 3600) / SECONDS_PER_DAY, C_DAY],
  [(DUSK_S + 3600) / SECONDS_PER_DAY, C_DUSK],
  [0.99999, C_NIGHT],
];

/** Multiply factors (0..1) for the given second-of-day — same math as
 * rendering.daynight.tint_factor. */
export function tintFactor(sec: number): [number, number, number] {
  const frac = ((sec % SECONDS_PER_DAY) + SECONDS_PER_DAY) % SECONDS_PER_DAY / SECONDS_PER_DAY;
  for (let i = 0; i < GRADIENT.length - 1; i++) {
    const [f0, c0] = GRADIENT[i];
    const [f1, c1] = GRADIENT[i + 1];
    if (f0 <= frac && frac <= f1) {
      const t = f1 === f0 ? 0 : (frac - f0) / (f1 - f0);
      return [
        (c0[0] + (c1[0] - c0[0]) * t) / 255,
        (c0[1] + (c1[1] - c0[1]) * t) / 255,
        (c0[2] + (c1[2] - c0[2]) * t) / 255,
      ];
    }
  }
  return [C_NIGHT[0] / 255, C_NIGHT[1] / 255, C_NIGHT[2] / 255];
}

// Peak-brightness override: when nothing visible would be drawn (midday)
// the rAF loop is stopped entirely until the clock drifts toward dusk/night.
const SKIP_AMBIENT = 0.97;

// --- Two-layer lighting (why not a plain colored multiply?) -----------------
// A raw colored multiply was tried twice and failed both ways:
//   - full strength: dusk/night crushed the screen ("màn xanh lè"), and
//   - blended toward white: the hue died with it, so the long dawn->noon
//     gradient segment painted a desaturated lavender-gray over the morning
//     ("buổi sáng màu xám").
// Fix: SEPARATE brightness from mood.
//   1. AMBIENT multiply — a MONOCHROME gray derived from the gradient's
//      luminance. Scaling every channel equally never desaturates; the scene
//      just gets darker (floored so night stays readable).
//   2. CAST overlay — the period's HUE, pushed far from gray (saturate()),
//      painted source-over with an alpha that fades out as the day brightens.
// Violet dawn, warm gold noon, orange dusk, indigo night — no gray anywhere.
const DARK_STRENGTH = 0.55; // 0..1 — how much of the gradient darkness applies
const MIN_AMBIENT = 0.45;   // floor: night never darker than this
const CAST_ALPHA = 0.30;    // overlay alpha at full night (fades to ~0 at noon)

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/** Push a gradient stop away from gray around its luminance (k > 1 = more
 * saturated). Turns the murky dawn violet into real violet, dusk into real
 * orange — hues the raw Godot stops only imply weakly. */
function saturateStop(r: number, g: number, b: number, k = 2.2): [number, number, number] {
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return [
    Math.max(0, Math.min(255, lum + (r - lum) * k)),
    Math.max(0, Math.min(255, lum + (g - lum) * k)),
    Math.max(0, Math.min(255, lum + (b - lum) * k)),
  ];
}

// Same fraction-of-day knots as GRADIENT, but each stop hue-boosted for the
// cast overlay.
const CAST_GRADIENT: [number, [number, number, number]][] = GRADIENT.map(
  ([f, c]) => [f, saturateStop(c[0], c[1], c[2])],
);

/** Saturated cast colour for the given second-of-day (interpolated). */
function castColor(sec: number): [number, number, number] {
  const frac = (((sec % SECONDS_PER_DAY) + SECONDS_PER_DAY) % SECONDS_PER_DAY) / SECONDS_PER_DAY;
  for (let i = 0; i < CAST_GRADIENT.length - 1; i++) {
    const [f0, c0] = CAST_GRADIENT[i];
    const [f1, c1] = CAST_GRADIENT[i + 1];
    if (f0 <= frac && frac <= f1) {
      const t = f1 === f0 ? 0 : (frac - f0) / (f1 - f0);
      return [
        c0[0] + (c1[0] - c0[0]) * t,
        c0[1] + (c1[1] - c0[1]) * t,
        c0[2] + (c1[2] - c0[2]) * t,
      ];
    }
  }
  return CAST_GRADIENT[0][1];
}

export class DayNightFx {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  // Last server clock + local receive time -> smooth interpolation between
  // snapshots (20 Hz clock steps would make dusk visibly chop).
  private clockSec = -1;
  private clockRecvMs = 0;
  private raf = 0;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private running = false;

  constructor() {
    const canvas = document.createElement("canvas");
    canvas.id = "daynight-fx";
    this.canvas = canvas;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("daynight fx: no 2d context");
    this.ctx = ctx;
    canvas.style.pointerEvents = "none";
    window.addEventListener("resize", () => this.resize());
    this.resize();
  }

  /** Mount into a container (game-root) once, above the Phaser canvas. */
  mount(parent: HTMLElement): void {
    if (this.canvas.parentElement === parent) return;
    parent.appendChild(this.canvas);
  }

  /** Feed the authoritative in-game second-of-day from a snapshot frame. */
  setClock(secondsOfDay: number): void {
    this.clockSec = secondsOfDay;
    this.clockRecvMs = performance.now();
    this.start();
  }

  // ---------------------------------------------------------------- engine

  private resize(): void {
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = window.innerWidth;
    this.h = window.innerHeight;
    this.canvas.width = Math.max(1, Math.round(this.w * this.dpr));
    this.canvas.height = Math.max(1, Math.round(this.h * this.dpr));
    this.canvas.style.width = `${this.w}px`;
    this.canvas.style.height = `${this.h}px`;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  }

  /** Current interpolated in-game second-of-day. The in-game day runs at
   * DAY_LENGTH_SECONDS=1800 real seconds (server default) -> in-game seconds
   * advance 288x faster than wall time; extrapolating from the last snapshot
   * keeps the tint moving between 20 Hz ticks without new server frames. */
  private currentSec(): number {
    if (this.clockSec < 0) return -1;
    const INGAME_MULT = SECONDS_PER_DAY / 1800; // mirror config default
    const elapsed = (performance.now() - this.clockRecvMs) / 1000;
    return (this.clockSec + elapsed * INGAME_MULT) % SECONDS_PER_DAY;
  }

  private start(): void {
    if (this.running) return;
    this.running = true;
    const loop = () => {
      if (!this.running) return;
      this.draw();
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  stop(): void {
    this.running = false;
    cancelAnimationFrame(this.raf);
    this.ctx.clearRect(0, 0, this.w, this.h);
  }

  private draw(): void {
    const sec = this.currentSec();
    if (sec < 0) return;
    const [r, g, b] = tintFactor(sec);
    // 1. Ambient: monochrome brightness from the gradient's luminance.
    const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    const ambient = Math.max(MIN_AMBIENT, 1 - DARK_STRENGTH * (1 - lum));
    // 2. Cast: saturated period hue, fading out as the day brightens.
    const cast = castColor(sec);
    const castA = clamp01((1 - lum) * CAST_ALPHA);
    // Midday: nothing to draw — stop the loop until dusk approaches.
    if (ambient >= SKIP_AMBIENT && castA < 0.02) {
      this.stop();
      return;
    }
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);
    const v = Math.round(ambient * 255);
    ctx.globalCompositeOperation = "multiply";
    ctx.fillStyle = `rgb(${v},${v},${v})`;
    ctx.fillRect(0, 0, this.w, this.h);
    if (castA > 0.004) {
      ctx.globalCompositeOperation = "source-over";
      ctx.fillStyle = `rgba(${Math.round(cast[0])},${Math.round(cast[1])},${Math.round(cast[2])},${castA.toFixed(3)})`;
      ctx.fillRect(0, 0, this.w, this.h);
    }
    ctx.globalCompositeOperation = "source-over";
  }
}

// Singleton wired from main.ts (one overlay for the whole page).
export const dayNightFx = new DayNightFx();
