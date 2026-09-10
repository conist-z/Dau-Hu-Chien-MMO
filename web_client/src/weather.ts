// Animated weather overlay for the web client — mirrors the Discord client's
// WeatherFx visual identity (rendering/weather_fx.py) but drawn on a plain
// canvas ABOVE the Phaser canvas: no state mutation, no game-logic deps
// (rules 2/3). Each animated key gets its own look:
//
// - rain / heavy_rain / storm: slanted falling streaks (fast, dense) plus a
//   mood tint over the whole screen.
// - snow / cold: chunky diamond flakes drifting slowly.
// - wind: long horizontal gust dashes streaking right with faint tails.
// - fog: soft drifting mist bands (web-only nicety; Discord has no GIF).
// - storm lightning is an EVENT: a random strike (bolt polyline + glow +
//   screen brighten) fires every 4-11 s while the key is storm.
//
// Two particle layers (near + far) scroll at different speeds for parallax.
// Particles wrap around the screen edges so the field never runs out.

const TAU = Math.PI * 2;

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  len: number;   // streak length / flake radius
  width: number;
  alpha: number;
  color: string;
  phase: number; // per-particle wobble seed
}

interface WeatherStyle {
  near: number;          // near-layer particle count (scaled by area/512k)
  far: number;           // far-layer particle count
  speed: number;         // px/s fall speed (near layer)
  drift: number;         // horizontal drift px/s
  colors: string[];      // candidate particle colours
  alpha: [number, number];
  len: [number, number]; // streak length / flake radius range
  width: [number, number];
  tint: string | null;   // full-screen mood tint (rgba)
  tintAlpha: number;
}

const STYLES: Record<string, WeatherStyle> = {
  rain: {
    near: 70, far: 30, speed: 520, drift: 90,
    colors: ["#c4dbff", "#aac8ff"], alpha: [0.30, 0.62],
    len: [12, 26], width: [1, 1.5], tint: "rgba(12,18,34,0.07)", tintAlpha: 1,
  },
  heavy_rain: {
    near: 130, far: 60, speed: 760, drift: 150,
    colors: ["#cee2ff", "#bad3ff"], alpha: [0.42, 0.78],
    len: [18, 34], width: [1.5, 2.5], tint: "rgba(8,12,22,0.12)", tintAlpha: 1,
  },
  storm: {
    near: 140, far: 65, speed: 820, drift: 200,
    colors: ["#cae0ff", "#b4cfff"], alpha: [0.48, 0.85],
    len: [20, 38], width: [2, 3], tint: "rgba(5,9,18,0.16)", tintAlpha: 1,
  },
  snow: {
    near: 55, far: 28, speed: 90, drift: 28,
    colors: ["#f8faff", "#e8f0ff"], alpha: [0.5, 0.95],
    len: [2, 4], width: [2, 4], tint: "rgba(250,252,255,0.04)", tintAlpha: 1,
  },
  cold: {
    near: 30, far: 14, speed: 80, drift: 22,
    colors: ["#e4f0ff", "#d2e4fc"], alpha: [0.4, 0.75],
    len: [1.5, 3], width: [1.5, 3], tint: "rgba(185,214,255,0.06)", tintAlpha: 1,
  },
  wind: {
    near: 34, far: 16, speed: 0, drift: 640,
    colors: ["#f8fcff", "#e8f0fc", "#d4e6fa"], alpha: [0.35, 0.75],
    len: [18, 52], width: [1, 2], tint: "rgba(238,245,255,0.04)", tintAlpha: 1,
  },
  fog: {
    near: 14, far: 8, speed: 14, drift: 40,
    colors: ["#cfd6e4", "#e2e8f4"], alpha: [0.05, 0.13],
    len: [90, 220], width: [16, 34], tint: "rgba(200,210,228,0.07)", tintAlpha: 1,
  },
};

// Keys that render an animated overlay (everything else = static/clear sky).
export const ANIMATED_WEATHER_KEYS = new Set(Object.keys(STYLES));

function rand(a: number, b: number): number {
  return a + Math.random() * (b - a);
}

function makeParticle(style: WeatherStyle, w: number, h: number, far: boolean): Particle {
  const scale = far ? 0.65 : 1; // far layer: smaller, fainter, slower
  return {
    x: Math.random() * w,
    y: Math.random() * h,
    vx: style.drift * scale * rand(0.8, 1.2),
    vy: style.speed * scale * rand(0.8, 1.2),
    len: rand(style.len[0], style.len[1]) * scale,
    width: rand(style.width[0], style.width[1]) * scale,
    alpha: rand(style.alpha[0], style.alpha[1]) * (far ? 0.45 : 1),
    color: style.colors[Math.floor(Math.random() * style.colors.length)],
    phase: Math.random() * TAU,
  };
}

interface Bolt {
  pts: [number, number][];
  glow: [number, number][];
  until: number;      // performance.now() when the bolt fades out
  flashUntil: number; // ambient flash window
}

export class WeatherFx {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private key: string | null = null;
  private near: Particle[] = [];
  private far: Particle[] = [];
  private raf = 0;
  private last = 0;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private nextBoltAt = 0;
  private bolt: Bolt | null = null;
  private running = false;

  constructor() {
    const canvas = document.createElement("canvas");
    canvas.id = "weather-fx";
    this.canvas = canvas;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("weather fx: no 2d context");
    this.ctx = ctx;
    // Inserted BEFORE the overlay so the HUD stays interactive/crisp on top;
    // pointer-events none so clicks reach the Phaser canvas below.
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
    this.nextBoltAt = performance.now() + rand(1200, 4000);
    this.seedParticles();
    if (next) this.start();
    else this.stop();
  }

  /** True when the current key draws an overlay (HUD could dim the icon). */
  get active(): boolean {
    return this.key !== null;
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
    if (this.key) this.seedParticles();
  }

  private seedParticles(): void {
    if (!this.key) {
      this.near = [];
      this.far = [];
      return;
    }
    const style = STYLES[this.key];
    // Density scales with viewport area so ultrawide screens don't look
    // sparse and phones don't drown (reference area = 1280x800 * 0.5).
    const areaScale = Math.sqrt((this.w * this.h) / (1280 * 800));
    const clampScale = Math.max(0.55, Math.min(1.6, areaScale));
    this.near = Array.from({ length: Math.round(style.near * clampScale) }, () =>
      makeParticle(style, this.w, this.h, false));
    this.far = Array.from({ length: Math.round(style.far * clampScale) }, () =>
      makeParticle(style, this.w, this.h, true));
  }

  private start(): void {
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    const loop = (t: number) => {
      if (!this.running) return;
      // Clamp dt: a background tab that froze the rAF must not teleport
      // every particle across the screen when the tab returns.
      const dt = Math.min(0.05, (t - this.last) / 1000);
      this.last = t;
      this.step(dt, t);
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

  private step(dt: number, now: number): void {
    if (!this.key) return;
    const snowLike = this.key === "snow" || this.key === "cold" || this.key === "fog";
    for (const p of [...this.near, ...this.far]) {
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (snowLike) {
        // Chunky flakes wobble sideways as they fall (mist bands swell too).
        p.phase += dt * 1.6;
        p.x += Math.sin(p.phase) * 12 * dt;
      }
      // Wrap around all edges (with margin so streaks don't pop mid-screen).
      const m = 60;
      if (p.x > this.w + m) p.x = -m;
      else if (p.x < -m) p.x = this.w + m;
      if (p.y > this.h + m) p.y = -m;
      else if (p.y < -m) p.y = this.h + m;
    }
    // Storm lightning events.
    if (this.key === "storm") {
      if (this.bolt && now > this.bolt.until) this.bolt = null;
      if (!this.bolt && now >= this.nextBoltAt) {
        this.bolt = this.spawnBolt(now);
        this.nextBoltAt = now + rand(4000, 11000);
      }
    } else if (this.bolt) {
      this.bolt = null;
    }
  }

  private spawnBolt(now: number): Bolt {
    const mainX = rand(this.w * 0.1, this.w * 0.9);
    const pts: [number, number][] = [];
    const glow: [number, number][] = [];
    // Jagged main channel top->~65% height, branching into forks.
    const build = (x: number, y: number, endY: number, depth: number): void => {
      pts.push([x, y]);
      glow.push([x, y]);
      while (y < endY) {
        y += rand(14, 34);
        x += rand(-22, 22);
        pts.push([x, y]);
        glow.push([x, y]);
        // Fork chance: spawn a shorter, thinner branch.
        if (depth < 2 && Math.random() < 0.18) {
          build(x, y, Math.min(endY, y + rand(50, 130)), depth + 1);
        }
      }
    };
    build(mainX, -10, this.h * rand(0.45, 0.7), 0);
    const duration = rand(260, 420);
    return {
      pts,
      glow,
      until: now + duration,
      flashUntil: now + duration + 160,
    };
  }

  private draw(now: number): void {
    const ctx = this.ctx;
    if (!this.key) return;
    const style = STYLES[this.key];
    ctx.clearRect(0, 0, this.w, this.h);

    // Mood tint first (under particles, above the game).
    if (style.tint) {
      ctx.fillStyle = style.tint;
      ctx.fillRect(0, 0, this.w, this.h);
    }

    // Far layer fainter (parallax depth).
    this.drawLayer(this.far, 1);
    this.drawLayer(this.near, 1);

    // Storm bolt: jagged white channel + soft glow + brief ambient flash.
    if (this.bolt && now < this.bolt.flashUntil) {
      const b = this.bolt;
      const age = now - (b.until - 300);
      const fade = Math.max(0, Math.min(1, 1 - age / 300));
      if (now < b.until) {
        ctx.save();
        ctx.globalAlpha = 0.9 * fade;
        ctx.strokeStyle = "#eef4ff";
        ctx.lineWidth = 2.5;
        ctx.lineCap = "round";
        ctx.shadowColor = "rgba(210,225,255,0.9)";
        ctx.shadowBlur = 14;
        ctx.beginPath();
        for (let i = 0; i < b.pts.length; i++) {
          const [x, y] = b.pts[i];
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.restore();
      }
      // Ambient flash brightens the whole screen briefly (never a whiteout).
      ctx.fillStyle = `rgba(226,236,255,${(0.10 * fade).toFixed(3)})`;
      ctx.fillRect(0, 0, this.w, this.h);
    }
  }

  private drawLayer(layer: Particle[], _alpha: number): void {
    const ctx = this.ctx;
    const streak = this.key === "rain" || this.key === "heavy_rain" || this.key === "storm";
    const flake = this.key === "snow" || this.key === "cold";
    const mist = this.key === "fog";
    for (const p of layer) {
      ctx.globalAlpha = p.alpha;
      if (streak) {
        // Slanted streak along the velocity vector.
        const vlen = Math.hypot(p.vx, p.vy) || 1;
        const ux = (p.vx / vlen) * p.len;
        const uy = (p.vy / vlen) * p.len;
        ctx.strokeStyle = p.color;
        ctx.lineWidth = p.width;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.x - ux, p.y - uy);
        ctx.stroke();
      } else if (flake) {
        // Chunky diamond flake.
        ctx.fillStyle = p.color;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y - p.len);
        ctx.lineTo(p.x + p.len, p.y);
        ctx.lineTo(p.x, p.y + p.len);
        ctx.lineTo(p.x - p.len, p.y);
        ctx.closePath();
        ctx.fill();
      } else if (mist) {
        // Soft fog band: wide, very faint ellipse.
        const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.len);
        g.addColorStop(0, p.color);
        g.addColorStop(1, "rgba(0,0,0,0)");
        ctx.globalAlpha = p.alpha * 0.5;
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.ellipse(p.x, p.y, p.len, p.width, 0, 0, TAU);
        ctx.fill();
      } else {
        // Wind gust: horizontal dash + faint tail trailing left.
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
  }
}

// Singleton wired from main.ts (one overlay for the whole page).
export const weatherFx = new WeatherFx();
