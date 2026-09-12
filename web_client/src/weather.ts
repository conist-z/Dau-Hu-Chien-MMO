// Animated weather overlay for the web client — powered by the SAME textures
// + animation model as the Discord client (rendering/weather_fx.py):
//
// Textures (copied from assets/fx/, served statically from ui/fx/):
//   - rain/rain_00..09.png  (256x32 seamless particle sheets)
//   - snow/snow_tile.png    (256x32)
//   - wind/wind_00..03.png  (512x32)
//   - thunder/bolt_00..04.png (lightning bolt artwork)
//
// Each animated key keeps its Discord visual identity (same style table):
//
// - rain / heavy_rain / storm: slanted falling streaks (fast, dense) plus a
//   mood tint over the whole screen.
// - snow / cold: chunky diamond flakes drifting slowly.
// - wind: long horizontal gust dashes streaking right.
// - fog: soft drifting mist bands (web-only nicety; procedural fallback).
// - storm lightning is an EVENT: a random bolt sprite + glow + gentle ambient
//   brighten fires every 4-11 s while the key is storm (same cadence and
//   ~35% "distant flicker" rate as the Discord _lightning_loop).
//
// SCROLL MODEL: the Discord renderer tiles a tall seeded master sheet and
// advances it `speed` px per GIF frame (n*speed wraps the master height —
// seamless by construction). The pack sheets here are only 32px tall, so a
// 6-frame discrete loop cannot wrap them; instead the web port scrolls the
// sheet CONTINUOUSLY at the equivalent px/s, wrapped by the sheet dimension.
// Continuous wrapping is seamless for ANY speed and reads smoother than the
// GIF on a 60fps canvas. Near layer scrolls at full speed; the far layer is
// phase-shifted and fainter (parallax), exactly like the Discord renderer's
// two master fields (near alpha 0.72 / far alpha 0.34).
//
// If the sheets fail to load (missing files) we fall back to the old
// procedural canvas particles so weather never silently disappears.

interface WeatherStyle {
  sheet: string;        // folder under ui/fx/
  speedPx: number;      // scroll speed px/s (Discord parity, see below)
  tint: string;         // full-screen mood tint under the particles
  bolt?: boolean;       // storm: fire thunder/bolt_*.png lightning events
  fog?: boolean;        // procedural mist (web-only key, no sheet)
  horizontal?: boolean; // wind scrolls sideways instead of down
}

// speedPx ≈ Discord speed-per-frame (rendering/weather_fx.py _STYLES.speed)
// at the 160ms map-GIF frame duration: rain 32/0.16=200, heavy_rain/storm
// 48/0.16=300, snow 16/0.2=80, wind ~600 (matches the old web gust speed).
const STYLES: Record<string, WeatherStyle> = {
  rain: {
    sheet: "rain", speedPx: 200, tint: "rgba(12,18,34,0.07)",
  },
  heavy_rain: {
    sheet: "rain", speedPx: 300, tint: "rgba(8,12,22,0.12)",
  },
  storm: {
    sheet: "rain", speedPx: 300, tint: "rgba(5,9,18,0.16)", bolt: true,
  },
  snow: {
    sheet: "snow", speedPx: 80, tint: "rgba(250,252,255,0.04)",
  },
  cold: {
    sheet: "snow", speedPx: 80, tint: "rgba(185,214,255,0.06)",
  },
  wind: {
    sheet: "wind", speedPx: 600, tint: "rgba(238,245,255,0.04)", horizontal: true,
  },
  fog: {
    // Web-only nicety: soft procedural mist (no sheet in the Discord pack).
    sheet: "", speedPx: 0, tint: "rgba(200,210,228,0.07)", fog: true,
  },
};

// Keys that render an animated overlay (everything else = static/clear sky).
export const ANIMATED_WEATHER_KEYS = new Set(Object.keys(STYLES));

// Per-layer opacity — same near/far weighting as the Discord renderer
// (_get_masters scales near 0.72, far 0.34).
const NEAR_ALPHA = 0.72;
const FAR_ALPHA = 0.34;

const FX_ROOT = "ui/fx";

// Explicit file lists per folder (no directory listing on static hosting;
// mirrors the files scripts/convert_weather_fx.py generated).
const SHEET_FILES: Record<string, string[]> = {
  rain: ["rain_00.png", "rain_01.png", "rain_02.png", "rain_03.png", "rain_04.png",
         "rain_05.png", "rain_06.png", "rain_07.png", "rain_08.png", "rain_09.png"],
  snow: ["snow_tile.png"],
  wind: ["wind_00.png", "wind_01.png", "wind_02.png", "wind_03.png"],
  thunder: ["bolt_00.png", "bolt_01.png", "bolt_02.png", "bolt_03.png", "bolt_04.png"],
};

interface Sheet {
  img: HTMLImageElement;
  w: number;
  h: number;
}

const sheetCache = new Map<string, Promise<Sheet | null>>();

function loadSheet(dir: string, file: string): Promise<Sheet | null> {
  const path = `${FX_ROOT}/${dir}/${file}`;
  let p = sheetCache.get(path);
  if (p) return p;
  p = new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve({ img, w: img.naturalWidth, h: img.naturalHeight });
    img.onerror = () => resolve(null);
    img.src = path;
  });
  sheetCache.set(path, p);
  return p;
}

function rand(a: number, b: number): number {
  return a + Math.random() * (b - a);
}

// ---------------------------------------------------------------- fallback particles

const TAU = Math.PI * 2;

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  len: number;
  width: number;
  alpha: number;
  color: string;
  phase: number;
}

// Procedural fallback density/look (previous web behaviour, kept as the
// texture-failure safety net + the web-only fog key).
const FALLBACK_CFG: Record<string, {
  n: number; f: number; spd: number; dr: number;
  len: [number, number]; wid: [number, number]; al: [number, number];
  col: string[]; kind: "streak" | "flake" | "dash" | "mist";
}> = {
  rain: { n: 70, f: 30, spd: 520, dr: 90, len: [12, 26], wid: [1, 1.5],
    al: [0.30, 0.62], col: ["#c4dbff", "#aac8ff"], kind: "streak" },
  heavy_rain: { n: 130, f: 60, spd: 760, dr: 150, len: [18, 34], wid: [1.5, 2.5],
    al: [0.42, 0.78], col: ["#cee2ff", "#bad3ff"], kind: "streak" },
  storm: { n: 140, f: 65, spd: 820, dr: 200, len: [20, 38], wid: [2, 3],
    al: [0.48, 0.85], col: ["#cae0ff", "#b4cfff"], kind: "streak" },
  snow: { n: 55, f: 28, spd: 90, dr: 28, len: [2, 4], wid: [2, 4],
    al: [0.5, 0.95], col: ["#f8faff", "#e8f0ff"], kind: "flake" },
  cold: { n: 30, f: 14, spd: 80, dr: 22, len: [1.5, 3], wid: [1.5, 3],
    al: [0.4, 0.75], col: ["#e4f0ff", "#d2e4fc"], kind: "flake" },
  wind: { n: 34, f: 16, spd: 0, dr: 640, len: [18, 52], wid: [1, 2],
    al: [0.35, 0.75], col: ["#f8fcff", "#e8f0fc", "#d4e6fa"], kind: "dash" },
  fog: { n: 14, f: 8, spd: 14, dr: 40, len: [90, 220], wid: [16, 34],
    al: [0.05, 0.13], col: ["#cfd6e4", "#e2e8f4"], kind: "mist" },
};

function makeParticle(
  c: { spd: number; dr: number; len: [number, number]; wid: [number, number]; al: [number, number]; col: string[] },
  w: number, h: number, far: boolean,
): Particle {
  const scale = far ? 0.65 : 1;
  return {
    x: Math.random() * w,
    y: Math.random() * h,
    vx: c.dr * scale * rand(0.8, 1.2),
    vy: c.spd * scale * rand(0.8, 1.2),
    len: rand(c.len[0], c.len[1]) * scale,
    width: rand(c.wid[0], c.wid[1]) * scale,
    alpha: rand(c.al[0], c.al[1]) * (far ? 0.45 : 1),
    color: c.col[Math.floor(Math.random() * c.col.length)],
    phase: Math.random() * TAU,
  };
}

// ---------------------------------------------------------------- bolts

interface Bolt {
  img: HTMLImageElement;
  x: number;
  y: number;
  w: number;
  h: number;
  until: number;      // performance.now() when the bolt fades out
  flashUntil: number; // ambient flash window
  distant: boolean;   // ~35% distant flicker with no bolt (Discord parity)
}

export class WeatherFx {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private key: string | null = null;
  private raf = 0;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private running = false;
  private last = 0;
  private dt = 1 / 60;

  // Sheet state (loaded once per key switch).
  private near: Sheet | null = null;
  private far: Sheet | null = null;
  private bolts: HTMLImageElement[] = [];

  // Storm lightning events.
  private nextBoltAt = 0;
  private bolt: Bolt | null = null;

  // Procedural fallback particles (fog / missing textures).
  private nearP: Particle[] = [];
  private farP: Particle[] = [];
  private proceduralKind: string | null = null;

  constructor() {
    const canvas = document.createElement("canvas");
    canvas.id = "weather-fx";
    this.canvas = canvas;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("weather fx: no 2d context");
    this.ctx = ctx;
    canvas.style.pointerEvents = "none";
    window.addEventListener("resize", () => this.resize());
    this.resize();
  }

  /** Mount into a container (game-root) once, above the Phaser canvas. */
  mount(parent: HTMLElement): void {
    if (this.canvas.parentElement === parent) return;
    // LAYER SAFETY (bug cũ: weather canvas đè màn hình nuốt click — đã fix
    // bằng pointer-events:none + bind input vào game.canvas của Phaser):
    // canvas này PHẢI nằm TRÊN game canvas mới thấy được hạt mưa/tuyết
    // (game canvas render opaque, phủ kín mọi thứ bên dưới). Giữ appendChild
    // + tái khẳng định pointer-events:none ở đây và trong CSS (!important).
    this.canvas.style.pointerEvents = "none";
    parent.appendChild(this.canvas);
  }

  /** Switch the active weather; no-op when the key is unchanged. */
  setWeather(key: string | null | undefined): void {
    const next = key && ANIMATED_WEATHER_KEYS.has(key) ? key : null;
    if (next === this.key) return;
    this.key = next;
    this.bolt = null;
    this.nearP = [];
    this.farP = [];
    this.proceduralKind = null;
    if (next) {
      void this.loadFor(next);
    } else {
      this.stop();
    }
  }

  /** True when the current key draws an overlay (HUD could dim the icon). */
  get active(): boolean {
    return this.key !== null;
  }

  /** Load the sheets for a key (async; draws the fallback until ready). */
  private async loadFor(key: string): Promise<void> {
    const style = STYLES[key];
    if (!style) return;
    if (style.fog) {
      this.proceduralKind = FALLBACK_CFG.fog.kind;
      this.seedProcedural(key);
      this.start();
      return;
    }
    // Warm the fallback immediately so the first raindrops are visible while
    // the sheets stream in (procedural is swapped out once they arrive).
    this.proceduralKind = FALLBACK_CFG[key]?.kind ?? null;
    this.seedProcedural(key);
    this.start();

    const files = SHEET_FILES[style.sheet] ?? [];
    const loaded = (await Promise.all(files.map((n) => loadSheet(style.sheet, n))))
      .filter((s): s is Sheet => s !== null);
    if (this.key !== key) return; // weather changed while loading
    if (loaded.length === 0) {
      return; // keep the procedural fallback running
    }
    this.proceduralKind = null;
    this.nearP = [];
    this.farP = [];
    // Near layer = the first frame; far layer = the next frame at a shifted
    // phase (the Discord renderer builds an independent far field; with the
    // real pack art a phase-shifted frame reads as the same organic parallax
    // without double-drawing one particle field).
    this.near = loaded[0];
    this.far = loaded[1] ?? loaded[0];
    if (style.bolt) {
      const boltFiles = SHEET_FILES.thunder ?? [];
      const boltSheets = (await Promise.all(boltFiles.map((n) => loadSheet("thunder", n))))
        .filter((s): s is Sheet => s !== null);
      if (this.key !== key) return;
      this.bolts = boltSheets.map((s) => s.img);
    } else {
      this.bolts = [];
    }
    this.nextBoltAt = performance.now() + rand(1200, 4000);
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

  private seedProcedural(key: string): void {
    const c = FALLBACK_CFG[key];
    if (!c) return;
    // Density scales with viewport area so ultrawide screens don't look
    // sparse and phones don't drown (reference area = 1280x800).
    const areaScale = Math.sqrt((this.w * this.h) / (1280 * 800));
    const clampScale = Math.max(0.55, Math.min(1.6, areaScale));
    this.nearP = Array.from({ length: Math.round(c.n * clampScale) },
      () => makeParticle(c, this.w, this.h, false));
    this.farP = Array.from({ length: Math.round(c.f * clampScale) },
      () => makeParticle(c, this.w, this.h, true));
  }

  private start(): void {
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    const loop = (t: number) => {
      if (!this.running) return;
      // Clamp dt: a background tab that froze the rAF must not teleport
      // every particle across the screen when the tab returns.
      this.dt = Math.min(0.05, (t - this.last) / 1000) || 1 / 60;
      this.last = t;
      this.step(t);
      this.draw(t);
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  private stop(): void {
    this.running = false;
    cancelAnimationFrame(this.raf);
    this.ctx.clearRect(0, 0, this.w, this.h);
  }

  private step(now: number): void {
    if (!this.key) return;
    // Storm lightning events (Discord parity: 4-11 s cadence, ~35% distant).
    const style = STYLES[this.key];
    if (style?.bolt && this.bolts.length > 0) {
      if (this.bolt && now > this.bolt.flashUntil) this.bolt = null;
      if (!this.bolt && now >= this.nextBoltAt) {
        this.bolt = this.spawnBolt(now);
        this.nextBoltAt = now + rand(4000, 11000);
      }
    }
    // Procedural particle movement (fog + fallback while sheets load).
    if (this.proceduralKind) {
      const snowLike = this.proceduralKind === "flake" || this.proceduralKind === "mist";
      for (const p of [...this.nearP, ...this.farP]) {
        p.x += p.vx * this.dt;
        p.y += p.vy * this.dt;
        if (snowLike) {
          p.phase += this.dt * 1.6;
          p.x += Math.sin(p.phase) * 12 * this.dt;
        }
        const m = 60;
        if (p.x > this.w + m) p.x = -m;
        else if (p.x < -m) p.x = this.w + m;
        if (p.y > this.h + m) p.y = -m;
        else if (p.y < -m) p.y = this.h + m;
      }
    }
  }

  private spawnBolt(now: number): Bolt {
    const img = this.bolts[Math.floor(Math.random() * this.bolts.length)];
    // Same 0.55-1.2 scale band as the Discord _add_bolt, grown a little for
    // full-screen viewports.
    const scale = rand(0.55, 1.2) * Math.max(1, this.h / 480);
    const w = img.naturalWidth * scale;
    const h = img.naturalHeight * scale;
    const x = rand(0, Math.max(1, this.w - w));
    const y = rand(0, Math.max(1, this.h * 0.22));
    const duration = rand(260, 420);
    return {
      img,
      x, y, w, h,
      until: now + duration,
      flashUntil: now + duration + 160,
      distant: Math.random() < 0.35,
    };
  }

  private draw(now: number): void {
    const ctx = this.ctx;
    if (!this.key) return;
    const style = STYLES[this.key];
    if (!style) return;
    ctx.clearRect(0, 0, this.w, this.h);

    // Mood tint first (under particles, above the game) — same tints as the
    // Discord renderer's per-key _Style.tint.
    if (style.tint) {
      ctx.fillStyle = style.tint;
      ctx.fillRect(0, 0, this.w, this.h);
    }

    if (this.proceduralKind) {
      this.drawProcedural();
    } else if (this.near && this.far) {
      this.drawSheets(style, now);
    }

    // Storm bolt: real pack artwork + glow + brief ambient flash.
    if (this.bolt && now < this.bolt.flashUntil) {
      const b = this.bolt;
      const age = now - (b.until - 300);
      const fade = Math.max(0, Math.min(1, 1 - age / 300));
      if (!b.distant && now < b.until) {
        ctx.save();
        ctx.globalAlpha = 0.9 * fade;
        // Soft glow behind the bolt sprite (Discord composites a Gaussian
        // blur of the bolt at ~0.9 alpha).
        ctx.shadowColor = "rgba(210,225,255,0.9)";
        ctx.shadowBlur = 18;
        ctx.drawImage(b.img, b.x, b.y, b.w, b.h);
        ctx.restore();
      }
      // Ambient flash brightens the whole screen briefly (never a whiteout).
      ctx.fillStyle = `rgba(226,236,255,${(0.10 * fade).toFixed(3)})`;
      ctx.fillRect(0, 0, this.w, this.h);
    }
  }

  /** The Discord renderer's seamless scroll: tile the sheet across the
   * viewport wrapped by the sheet size, offset by a continuously-advancing
   * phase. Near layer scrolls at full speed; the far layer is phase-shifted
   * (+137px x like the Discord renderer) and fainter. Wind drifts RIGHT with
   * the far layer at 0.6x (Discord parity) at a different height. */
  private drawSheets(style: WeatherStyle, now: number): void {
    const ctx = this.ctx;
    const dist = (now / 1000) * style.speedPx;

    const drawLayer = (sheet: Sheet, alpha: number, ox: number, oy: number): void => {
      ctx.globalAlpha = alpha;
      const w = sheet.w;
      const h = sheet.h;
      let y = (oy % h) - h;
      while (y < this.h) {
        let x = (ox % w) - w;
        while (x < this.w) {
          ctx.drawImage(sheet.img, x, y);
          x += w;
        }
        y += h;
      }
      ctx.globalAlpha = 1;
    };

    if (style.horizontal) {
      const ox = dist;
      drawLayer(this.far!, FAR_ALPHA, ox * 0.6 + 512, 96 % Math.max(1, this.h));
      drawLayer(this.near!, NEAR_ALPHA, ox, 0);
    } else {
      // Falling particles scroll DOWN (the paste origin grows with the
      // offset, exactly like the Discord _tile_paste).
      const oy = dist;
      drawLayer(this.far!, FAR_ALPHA, 137, oy + this.far!.h / 3);
      drawLayer(this.near!, NEAR_ALPHA, 0, oy);
    }
  }

  /** Procedural layers (fog + texture-failure fallback). */
  private drawProcedural(): void {
    const ctx = this.ctx;
    const kind = this.proceduralKind!;
    const drawLayer = (layer: Particle[]): void => {
      for (const p of layer) {
        ctx.globalAlpha = p.alpha;
        if (kind === "streak") {
          const vlen = Math.hypot(p.vx, p.vy) || 1;
          const ux = (p.vx / vlen) * p.len;
          const uy = (p.vy / vlen) * p.len;
          ctx.strokeStyle = p.color;
          ctx.lineWidth = p.width;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(p.x - ux, p.y - uy);
          ctx.stroke();
        } else if (kind === "flake") {
          ctx.fillStyle = p.color;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y - p.len);
          ctx.lineTo(p.x + p.len, p.y);
          ctx.lineTo(p.x, p.y + p.len);
          ctx.lineTo(p.x - p.len, p.y);
          ctx.closePath();
          ctx.fill();
        } else if (kind === "mist") {
          const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.len);
          g.addColorStop(0, p.color);
          g.addColorStop(1, "rgba(0,0,0,0)");
          ctx.globalAlpha = p.alpha * 0.5;
          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.ellipse(p.x, p.y, p.len, p.width, 0, 0, TAU);
          ctx.fill();
        } else {
          // Wind dash + faint tail trailing left.
          ctx.strokeStyle = p.color;
          ctx.lineWidth = p.width;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(p.x + p.len, p.y);
          ctx.stroke();
          ctx.globalAlpha = p.alpha / 3;
          ctx.beginPath();
          ctx.moveTo(p.x - p.len / 2, p.y);
          ctx.lineTo(p.x, p.y);
          ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
    };
    drawLayer(this.farP);
    drawLayer(this.nearP);
  }
}

// Singleton wired from main.ts (one overlay for the whole page).
export const weatherFx = new WeatherFx();
