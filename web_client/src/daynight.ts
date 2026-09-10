// Day/night lighting overlay for the web client — mirrors the Discord client's
// rendering/daynight.py visual identity: the whole viewport is multiplied by
// the in-game clock colour sampled from the SAME 24h gradient stops
// (night -> dawn -> day -> dusk -> night, ported from the Godot
// daynightcycle2d addon). Zero game-logic deps (rules 2/3): pure visual.
//
// The tint is a full-screen multiply painted on a plain canvas ABOVE the
// Phaser canvas (below the HUD, same slot as the weather layer). The in-game
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

// Peak-brightness override: when the factor is >= this threshold the layer
// is skipped entirely (midday stays pixel-perfect — a full-screen multiply
// with ~1.0 factors would still cost a fillRect per frame for nothing).
const SKIP_ABOVE = 0.995;

// Restraint: on web the multiply hits EVERYTHING on screen (sprites, hover
// box, progress bars) — unlike Discord where entities keep 65% brightness and
// torches restore light. Blend the raw gradient factor toward white so dusk
// and night stay moody but readable (a raw night factor ~0.15 turned the
// whole screen near-black/blue: "màn xanh lè").
const STRENGTH = 0.45;
function gentle(r: number, g: number, b: number): [number, number, number] {
  return [
    1 - STRENGTH * (1 - r),
    1 - STRENGTH * (1 - g),
    1 - STRENGTH * (1 - b),
  ];
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
    const [rf, gf, bf] = gentle(...tintFactor(sec));
    // Midday (or any near-full-brightness stop): nothing to draw, stop the
    // rAF loop until the next snapshot drags the clock toward dusk/night.
    if (rf >= SKIP_ABOVE && gf >= SKIP_ABOVE && bf >= SKIP_ABOVE) {
      this.stop();
      return;
    }
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);
    // globalCompositeOperation "multiply" = the CanvasModulate semantics,
    // softened by gentle() above (see STRENGTH note).
    ctx.globalCompositeOperation = "multiply";
    ctx.fillStyle = `rgb(${Math.round(rf * 255)},${Math.round(gf * 255)},${Math.round(bf * 255)})`;
    ctx.fillRect(0, 0, this.w, this.h);
    ctx.globalCompositeOperation = "source-over";
  }
}

// Singleton wired from main.ts (one overlay for the whole page).
export const dayNightFx = new DayNightFx();
