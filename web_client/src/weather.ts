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
// WORLD-SPACE WEATHER (user rule 14/09 — "mưa phải đè lên map chứ không dán
// lên màn hình"): the overlay is CAMERA-AWARE. Each frame the tiled sheets
// are offset by the Phaser camera's world scroll × a per-layer parallax
// factor, on top of their own falling motion. Walking east makes the whole
// rain field slide WEST across the viewport (near layer at 0.72× camera
// speed, far layer at 0.5×) — the particles read as anchored to the map, not
// glued to the player's screen. The tint/veil/gust/bolt layers stay
// screen-space (sky + flashes are atmosphere, not particles).
//
// If the sheets fail to load (missing files) we fall back to the old
// procedural canvas particles so weather never silently disappears.
//
// WEATHER TRANSITIONS (user rule 14/09 — "đổi trời phải có hiệu ứng, không
// được đùng một phát đổi luôn"): every key change runs a ~3.2 s staged
// transition driven by TWO concurrent slots (outgoing + incoming):
//
//   entering (clear -> rain/storm/snow/...):
//     1. A CLOUD VEIL (dark blue-gray gradient, heavier at the top) ramps up
//        first — the sky visibly darkens while nothing falls yet.
//     2. A GUST BURST of fast wind dashes sweeps across (the wind picking up).
//     3. The incoming weather's particles + tint fade in UNDER the veil.
//     4. The veil dissolves into the weather's own mood tint.
//   leaving (rain/... -> clear): the particles fade out first, then the veil
//     lifts gradually — the sky brightens back instead of snapping.
//   switching (rain -> snow): both render simultaneously, crossfaded, with a
//     smaller veil pulse + gust bridging them.

interface WeatherStyle {
  sheet: string;        // folder under ui/fx/
  speedPx: number;      // scroll speed px/s (Discord parity, see below)
  tint: string;         // full-screen mood tint under the particles
  bolt?: boolean;       // storm: fire thunder/bolt_*.png lightning events
  fog?: boolean;        // procedural mist (web-only key, no sheet)
  horizontal?: boolean; // wind scrolls sideways instead of down
  alphaBoost?: number;  // visibility multiplier for the particle layers
  particles?: boolean;  // ALWAYS procedural (never tile a sheet) — used by
                        // snow so every flake is an individual with its own
                        // size/fall/sway instead of a repeating strip
  cloudShadow?: boolean; // drift the soft cloud.png blobs OVER the ground
                         // (ekonia parity: 3-4 blobs, 28 s life, alpha .16);
                         // stacks with rain/snow — never forced alone
}

// speedPx ≈ Discord speed-per-frame (rendering/weather_fx.py _STYLES.speed)
// at the 160ms map-GIF frame duration: rain 32/0.16=200, heavy_rain/storm
// 48/0.16=300, snow 16/0.2=80, wind ~600 (matches the old web gust speed).
const STYLES: Record<string, WeatherStyle> = {
  // Admin test key (/clouds N): ONLY the shadow blobs — no rain, no tint.
  cloud_shadow: {
    sheet: "", speedPx: 0, tint: "rgba(0,0,0,0)", cloudShadow: true,
  },
  rain: {
    // Visibility boost (user rule 14/09: "mưa yếu phải lòi mắt"): the pack
    // sheets are faint streaks — the boost brightens + thickens them without
    // touching the artwork. cloudShadow: bóng mây trôi trên đất STACK cùng
    // mưa (ekonia parity) — không bao giờ xuất hiện một mình.
    sheet: "rain", speedPx: 200, tint: "rgba(12,18,34,0.12)", alphaBoost: 2.1,
    cloudShadow: true,
  },
  heavy_rain: {
    sheet: "rain", speedPx: 300, tint: "rgba(8,12,22,0.18)", alphaBoost: 2.3,
    cloudShadow: true,
  },
  storm: {
    sheet: "rain", speedPx: 300, tint: "rgba(5,9,18,0.22)", bolt: true,
    alphaBoost: 2.4, cloudShadow: true,
  },
  snow: {
    // Snow is ALWAYS procedural: a tiled 32px strip can only ever repeat —
    // individual flakes each with own size/fall-speed/sway read alive.
    sheet: "", speedPx: 80, tint: "rgba(250,252,255,0.05)", particles: true,
  },
  cold: {
    sheet: "", speedPx: 80, tint: "rgba(185,214,255,0.08)", particles: true,
  },
  wind: {
    // Discord parity: the 1024px master wraps across 6 GIF frames of 160ms
    // (divmod in _scroll_overlays) → ~1067 px/s, not the old web guess of 600.
    sheet: "wind", speedPx: 1067, tint: "rgba(238,245,255,0.04)", horizontal: true,
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
// World-space parallax: how much of the CAMERA SCROLL the particle layers
// inherit per frame. 1.0 = perfectly anchored to the map (rain column stays
// over the same tree as you walk); 0.0 = the old screen-glued behaviour.
// Slightly below 1.0 keeps a touch of "distant weather" depth and avoids the
// far layer moving identically to the near one.
const NEAR_CAM_PARALLAX = 0.72;
const FAR_CAM_PARALLAX = 0.5;
// The game camera's zoom (game.ts setZoom(2.0)) — converts world-px camera
// scroll into the screen-px offset this overlay canvas draws with.
const CAM_ZOOM = 2.0;

// ---- transition tuning (user rule 14/09) -----------------------------------
const TRANSITION_MS = 3200;   // full staged transition duration
const VEIL_ENTER_PEAK = 0.30; // max cloud-darkening while weather rolls in
const VEIL_LEAVE_PEAK = 0.16; // max veil while the sky clears
const GUST_COUNT = 26;        // gust-burst dashes per transition

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}
function smoothstep(x: number): number {
  const t = clamp01(x);
  return t * t * (3 - 2 * t);
}
/** Parse "rgba(r,g,b,a)" into components (weather tints are all rgba()). */
function tintParts(tint: string): [number, number, number, number] {
  const m = /rgba?\(([^)]+)\)/.exec(tint);
  if (!m) return [0, 0, 0, 0];
  const parts = m[1].split(",").map((s) => parseFloat(s));
  return [parts[0] || 0, parts[1] || 0, parts[2] || 0, parts[3] ?? 0];
}

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
  img: HTMLImageElement | HTMLCanvasElement;
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

// ---------------------------------------------------------------- cloud shadows

// EKONIA PARITY (weather_layer.gd + cloud_shadows.tres): soft cloud blobs
// drift OVER the ground — area-scattered, constant slow velocity, 28 s life
// with a 0->1->0 fade, alpha ~0.16 dark tint, big scale. Renders in WORLD
// space via the same camera-parallax offset as the rain sheets.
const CLOUD_SHADOW = {
  // NOT readonly: the admin /clouds N test command writes the live count.
  count: 4,               // ekonia: amount 3 (we use 4 to cover wider screens)
  // Rain keys make the sky 5-6x CLOUDIER than the baseline 4 (user rule):
  // storm/heavy_rain blanket the ground, plain rain sits between.
  countRain: 20,
  countHeavyRain: 24,
  lifeMs: 28000,          // ekonia: lifetime 28.0
  windPx: 9,              // ekonia: wind 8 px/s (constant, never accelerates)
  fallPx: 2,              // ekonia: fall_speed 2 px/s
  alpha: 0.16,            // ekonia: Color(0.13, 0.14, 0.2, 0.16)
  color: "rgb(33,36,51)", // ekonia tint 0.13/0.14/0.2
  // Big clouds (user rule: "gấp 3-4 lần đám mây bạn làm"): 80px sheet ×
  // ~10-16 = 800-1280 px wide soft shadows drifting over the ground.
  scaleMin: 9.0,
  scaleMax: 16.0,
};

/** Blob count for a weather style: rain keys get the 5-6x dense field. */
function cloudCountFor(style: WeatherStyle | undefined): number {
  if (!style?.cloudShadow) return 0;
  if (style.bolt) return CLOUD_SHADOW.countHeavyRain;   // storm: 24
  if ((style.speedPx ?? 0) >= 300) return CLOUD_SHADOW.countHeavyRain; // heavy_rain
  if ((style.speedPx ?? 0) >= 200) return CLOUD_SHADOW.countRain;      // rain
  return CLOUD_SHADOW.count;                            // /clouds test key
}

interface CloudBlob {
  wx: number; wy: number;       // world-space anchor (px, incl. cam offset)
  vx: number; vy: number;       // drift velocity px/s
  scale: number;
  bornAt: number;               // ms
}

// ---------------------------------------------------------------- wind masters

// The web port originally tiled the raw CraftPix wind sheets (wind_00..03.png)
// across the screen — those are dense texture frames, so the effect read as an
// ugly, chaotic smear. The DISCORD client never does that: rendering/weather_fx.py
// _get_masters paints its own sparse seeded dash field onto a 1024x256 master
// (near 10 dashes, far 4, alpha 52-100/255, lengths 16-44, faint left tails)
// and scrolls THAT. Port the exact same generator here so wind looks identical.

/** Deterministic PRNG (same role as random.Random(seed) in the renderer). */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// _STYLES["wind"] in rendering/weather_fx.py, verbatim.
const WIND_MASTER = { w: 1024, h: 256, near: 10, far: 4, seed: 106 };
const WIND_COLORS = ["rgb(248,252,255)", "rgb(232,240,252)", "rgb(212,230,250)"];

function buildWindMaster(layer: 0 | 1): Sheet {
  const { w: mw, h: mh, near, far, seed } = WIND_MASTER;
  const count = layer === 0 ? near : far;
  const rng = mulberry32(seed * 10 + layer);
  const cv = document.createElement("canvas");
  cv.width = mw;
  cv.height = mh;
  const ctx = cv.getContext("2d")!;
  for (let i = 0; i < count; i++) {
    const x = rng() * mw;
    const y = rng() * mh;
    const len = 16 + Math.floor(rng() * 29);   // style.length (16, 44)
    const wd = 1 + Math.floor(rng() * 2);      // style.width (1, 2)
    const alpha = Math.round(52 + rng() * 48); // style.alpha (52, 100)
    const color = WIND_COLORS[Math.floor(rng() * WIND_COLORS.length)];
    // Wrap-around: stamp 4 copies so the master tiles seamlessly (same trick
    // as _draw_particle).
    for (const ox of [0, -mw]) {
      for (const oy of [0, -mh]) {
        ctx.globalAlpha = alpha / 255;
        ctx.strokeStyle = color;
        ctx.lineWidth = wd;
        ctx.beginPath();
        ctx.moveTo(x + ox, y + oy);
        ctx.lineTo(x + ox + len, y + oy);
        ctx.stroke();
        // Fainter tail trailing LEFT (gusts drift right — motion cue).
        ctx.globalAlpha = Math.max(12, Math.round(alpha / 3)) / 255;
        ctx.beginPath();
        ctx.moveTo(x + ox - len / 2, y + oy);
        ctx.lineTo(x + ox, y + oy);
        ctx.stroke();
      }
    }
  }
  ctx.globalAlpha = 1;
  return { img: cv, w: mw, h: mh };
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
  rain: { n: 90, f: 46, spd: 620, dr: 130, len: [14, 30], wid: [1.4, 2.2],
    al: [0.38, 0.7], col: ["#cfE2ff", "#b6d0ff"].map((c) => c.toLowerCase()), kind: "streak" },
  heavy_rain: { n: 150, f: 76, spd: 840, dr: 190, len: [20, 38], wid: [1.8, 2.8],
    al: [0.5, 0.85], col: ["#d6e6ff", "#c0d8ff"], kind: "streak" },
  storm: { n: 165, f: 84, spd: 900, dr: 230, len: [22, 42], wid: [2.2, 3.2],
    al: [0.55, 0.9], col: ["#d2e4ff", "#bcd6ff"], kind: "streak" },
  // Snow: rich individual flakes — wide size band, per-flake drift + sway
  // (applied in step()), slow twinkle via alpha variance, several whites.
  snow: { n: 110, f: 60, spd: 70, dr: 26, len: [1.6, 5.2], wid: [1.6, 5.2],
    al: [0.45, 1.0], col: ["#ffffff", "#f4f8ff", "#e6eefc", "#dbe6f8"], kind: "flake" },
  cold: { n: 60, f: 34, spd: 62, dr: 20, len: [1.2, 4.2], wid: [1.2, 4.2],
    al: [0.4, 0.85], col: ["#eaf2ff", "#d6e4fa", "#c8daf6"], kind: "flake" },
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
  img: HTMLImageElement | HTMLCanvasElement;
  x: number;
  y: number;
  w: number;
  h: number;
  until: number;      // performance.now() when the bolt fades out
  flashUntil: number; // ambient flash window
  distant: boolean;   // ~35% distant flicker with no bolt (Discord parity)
}

// ---------------------------------------------------------------- slots

/** One concurrently-rendering weather layer. A transition keeps TWO alive
 * (outgoing fading out + incoming fading in); steady state keeps one. */
interface WeatherSlot {
  key: string;
  style: WeatherStyle;
  near: Sheet | null;
  far: Sheet | null;
  bolts: (HTMLImageElement | HTMLCanvasElement)[];
  proceduralKind: string | null;
  nearP: Particle[];
  farP: Particle[];
  clouds: CloudBlob[];
  nextBoltAt: number;
  bolt: Bolt | null;
}

function makeSlot(key: string): WeatherSlot {
  return {
    key,
    style: STYLES[key],
    near: null,
    far: null,
    bolts: [],
    proceduralKind: null,
    nearP: [],
    farP: [],
    clouds: [],
    nextBoltAt: 0,
    bolt: null,
  };
}

export class WeatherFx {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  // Transition slots: `outgoing` (fading out) + `current` (fading in / live).
  private outgoing: WeatherSlot | null = null;
  private current: WeatherSlot | null = null;
  private transitionT0 = 0;
  // Gust-burst dashes (transition-only; cleared when the transition ends).
  private gust: Particle[] = [];
  private raf = 0;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private running = false;
  private last = 0;
  private dt = 1 / 60;
  // Camera coupling (world-space weather): a hook installed by main.ts that
  // returns the Phaser camera's current world scroll in CSS pixels. null =
  // not yet in a world (pre-welcome) — the overlay then degrades gracefully
  // to the old screen-space behaviour.
  private cameraHook: (() => { x: number; y: number; zoom?: number } | null) | null = null;
  private camScroll = { x: 0, y: 0, zoom: CAM_ZOOM };
  // cloud.png sprite (lazy-loaded once; null until ready, retry each slot).
  private cloudImg: HTMLImageElement | null = null;
  // Pre-tinted DARK copy (built once from cloudImg — see buildCloudShadow).
  private cloudShadowImg: HTMLCanvasElement | null = null;

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

  /**
   * Switch the active weather — now with a STAGED TRANSITION (user rule
   * 14/09). The outgoing slot keeps rendering (fading out) while the
   * incoming one loads and fades in; a cloud veil + gust burst bridge them.
   * Repeated same-key calls (every snapshot) are no-ops.
   * cloudCount (admin /clouds N) overrides the blob count while forced.
   */
  setWeather(key: string | null | undefined, cloudCount = 0): void {
    const next = key && ANIMATED_WEATHER_KEYS.has(key) ? key : null;
    if (next === (this.current?.key ?? null)) return;
    // Collapse any in-flight transition: the previous incoming layer becomes
    // the new outgoing one (the oldest layer simply gives up its ghost —
    // acceptable for rapid admin weather flips).
    this.outgoing = this.current;
    this.transitionT0 = performance.now();
    if (next) {
      this.current = makeSlot(next);
      if (cloudCount > 0 && this.current.style?.cloudShadow) {
        // Admin override: seed with the requested blob count instead of the
        // style default (re-seeding on every snapshot keeps it authoritative).
        this.seedClouds(this.current, Math.min(8, cloudCount));
      }
      void this.loadFor(this.current);
    } else {
      this.current = null;
    }
    this.spawnGust();
    this.start();
  }

  /** True while any weather overlay renders (HUD could dim the icon). */
  get active(): boolean {
    return this.current !== null || this.outgoing !== null;
  }

  /** Install the camera-scroll hook (called once from main.ts after the
   *  Phaser game exists). The hook reads the LIVE camera each frame so the
   *  particle field slides with the map as the player walks. */
  setCameraHook(hook: () => { x: number; y: number } | null): void {
    this.cameraHook = hook;
  }

  /** Load the sheets for a slot (async; draws the fallback until ready). */
  private async loadFor(slot: WeatherSlot): Promise<void> {
    const key = slot.key;
    const style = slot.style;
    if (!style) return;
    if (style.fog || style.particles) {
      slot.proceduralKind = FALLBACK_CFG[key]?.kind ?? "mist";
      this.seedProcedural(slot, key);
      return;
    }
    // WIND parity fix: the raw CraftPix wind sheets tile as a dense chaotic
    // smear. The Discord client scrolls its own sparse seeded dash field
    // instead (_get_masters), so build the same masters here — synchronously,
    // no network, and with no procedural-fallback warm-up phase needed.
    if (key === "wind") {
      slot.proceduralKind = null;
      slot.near = buildWindMaster(0);
      slot.far = buildWindMaster(1);
      return;
    }
    // Cloud shadows (ekonia parity): scatter blobs across the view + drift.
    if (style.cloudShadow) {
      // Rain keys blanket the ground (5-6x the baseline 4 blobs).
      this.seedClouds(slot, cloudCountFor(style));
      if (!this.cloudImg) {
        const img = new Image();
        img.onload = () => { this.cloudImg = img; };
        img.src = `${FX_ROOT}/cloud/cloud.png`;
      }
    }
    // Warm the fallback immediately so the first raindrops are visible while
    // the sheets stream in (procedural is swapped out once they arrive).
    slot.proceduralKind = FALLBACK_CFG[key]?.kind ?? null;
    this.seedProcedural(slot, key);

    const files = SHEET_FILES[style.sheet] ?? [];
    const loaded = (await Promise.all(files.map((n) => loadSheet(style.sheet, n))))
      .filter((s): s is Sheet => s !== null);
    if (this.current !== slot) return; // weather changed while loading
    if (loaded.length === 0) {
      return; // keep the procedural fallback running
    }
    slot.proceduralKind = null;
    slot.nearP = [];
    slot.farP = [];
    // Near layer = the first frame; far layer = the next frame at a shifted
    // phase (the Discord renderer builds an independent far field; with the
    // real pack art a phase-shifted frame reads as the same organic parallax
    // without double-drawing one particle field).
    slot.near = loaded[0];
    slot.far = loaded[1] ?? loaded[0];
    if (style.bolt) {
      const boltFiles = SHEET_FILES.thunder ?? [];
      const boltSheets = (await Promise.all(boltFiles.map((n) => loadSheet("thunder", n))))
        .filter((s): s is Sheet => s !== null);
      if (this.current !== slot) return;
      slot.bolts = boltSheets.map((s) => s.img);
    } else {
      slot.bolts = [];
    }
    slot.nextBoltAt = performance.now() + rand(1200, 4000);
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

  private seedProcedural(slot: WeatherSlot, key: string): void {
    const c = FALLBACK_CFG[key];
    if (!c) return;
    // Density scales with viewport area so ultrawide screens don't look
    // sparse and phones don't drown (reference area = 1280x800).
    const areaScale = Math.sqrt((this.w * this.h) / (1280 * 800));
    const clampScale = Math.max(0.55, Math.min(1.6, areaScale));
    slot.nearP = Array.from({ length: Math.round(c.n * clampScale) },
      () => makeParticle(c, this.w, this.h, false));
    slot.farP = Array.from({ length: Math.round(c.f * clampScale) },
      () => makeParticle(c, this.w, this.h, true));
  }

  /** Spawn the transition gust burst: fast wind dashes sweeping across the
   * whole viewport (the "wind picking up" cue of a changing sky). */
  private spawnGust(): void {
    this.gust = Array.from({ length: GUST_COUNT }, () => ({
      x: rand(-this.w * 0.4, this.w),
      y: rand(-20, this.h + 20),
      vx: rand(700, 1250),
      vy: rand(-50, 50),
      len: rand(26, 64),
      width: rand(1, 2.2),
      alpha: rand(0.22, 0.55),
      color: Math.random() < 0.5 ? "#f4f9ff" : "#dbe9fb",
      phase: Math.random() * TAU,
    }));
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
      // WORLD-SPACE: sample the RAW camera scroll (world px) every frame.
      // Each layer converts it to its own screen-space parallax offset at
      // draw time (zoom × factor) — see drawSheets / drawClouds.
      const cam = this.cameraHook?.() ?? null;
      if (cam) {
        this.camScroll.x = cam.x;
        this.camScroll.y = cam.y;
        this.camScroll.zoom = cam.zoom && cam.zoom > 0 ? cam.zoom : CAM_ZOOM;
      } else {
        this.camScroll.x = 0;
        this.camScroll.y = 0;
        this.camScroll.zoom = CAM_ZOOM;
      }
      this.step(t);
      this.draw(t);
      // Idle shutdown: transition finished and no live weather left.
      if (!this.current && (t - this.transitionT0) >= TRANSITION_MS) {
        this.running = false;
        this.ctx.clearRect(0, 0, this.w, this.h);
        cancelAnimationFrame(this.raf);
        return;
      }
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  private step(now: number): void {
    const p = clamp01((now - this.transitionT0) / TRANSITION_MS);
    // Storm lightning events (Discord parity: 4-11 s cadence, ~35% distant).
    // Only the INCOMING slot fires bolts, and only once it is mostly faded in.
    const slot = this.current;
    if (slot && slot.style.bolt && slot.bolts.length > 0 && p > 0.55) {
      if (slot.bolt && now > slot.bolt.flashUntil) slot.bolt = null;
      if (!slot.bolt && now >= slot.nextBoltAt) {
        slot.bolt = this.spawnBolt(slot, now);
        slot.nextBoltAt = now + rand(4000, 11000);
      }
    }
    // Procedural particle movement (fog + fallback while sheets load).
    for (const s of [this.outgoing, this.current]) {
      if (!s || !s.proceduralKind) continue;
      const snowLike = s.proceduralKind === "flake" || s.proceduralKind === "mist";
      for (const pt of [...s.nearP, ...s.farP]) {
        pt.x += pt.vx * this.dt;
        pt.y += pt.vy * this.dt;
        if (snowLike) {
          pt.phase += this.dt * rand(0.9, 2.2);
          // Bigger flakes sway wider + fall a touch faster — natural depth
          // (a single fixed sine made the old snow read as one rigid law).
          pt.x += Math.sin(pt.phase) * (6 + pt.len * 4) * this.dt;
          pt.x += Math.cos(pt.phase * 0.53) * 9 * this.dt;
        }
        const m = 60;
        if (pt.x > this.w + m) pt.x = -m;
        else if (pt.x < -m) pt.x = this.w + m;
        if (pt.y > this.h + m) pt.y = -m;
        else if (pt.y < -m) pt.y = this.h + m;
      }
    }
    // Gust burst: fast lateral sweep, culled off the right edge.
    for (const g of this.gust) {
      g.x += g.vx * this.dt;
      g.y += g.vy * this.dt;
    }
    // Cloud shadows drift at constant slow velocity in WORLD space; blobs
    // recycle on the ekonia 28 s life (fade handled at draw time).
    for (const s of [this.outgoing, this.current]) {
      if (!s || s.clouds.length === 0) continue;
      const nowMs = now;
      for (const c of s.clouds) {
        c.wx += c.vx * this.dt;
        c.wy += c.vy * this.dt;
        if (nowMs - c.bornAt >= CLOUD_SHADOW.lifeMs) {
          // Respawn INSIDE the current camera view (world px) + fresh size —
          // otherwise new blobs materialise off-screen and never appear.
          const a = this.randomCloudAnchor();
          c.wx = a.wx;
          c.wy = a.wy;
          c.scale = rand(CLOUD_SHADOW.scaleMin, CLOUD_SHADOW.scaleMax);
          c.bornAt = nowMs;
        }
      }
    }
    if (p >= 1) {
      // Transition done: drop the outgoing ghost + the burst.
      this.outgoing = null;
      this.gust = [];
    }
  }

  /** Seed the cloud-shadow blobs. Anchors are WORLD px inside the CURRENT
   *  camera view (with margin) — never screen px, or the scroll×zoom offset
   *  pushes every blob off-screen the moment the player moves (the "mây biến
   *  mất" bug). Births staggered so fade cycles never sync. */
  private seedClouds(slot: WeatherSlot, count = CLOUD_SHADOW.count): void {
    const now = performance.now();
    slot.clouds = Array.from({ length: count }, (_, i) => ({
      ...this.randomCloudAnchor(),
      vx: CLOUD_SHADOW.windPx * rand(0.8, 1.25),
      vy: CLOUD_SHADOW.fallPx * rand(0.6, 1.4),
      scale: rand(CLOUD_SHADOW.scaleMin, CLOUD_SHADOW.scaleMax),
      bornAt: now - (i / count) * CLOUD_SHADOW.lifeMs * rand(0.3, 0.95),
    }));
  }

  /** A random cloud anchor in WORLD px across the camera view + margin. */
  private randomCloudAnchor(): { wx: number; wy: number } {
    // Camera view in world px: scroll..scroll + innerWidth/zoom.
    const vw = this.w / this.camScroll.zoom;
    const vh = this.h / this.camScroll.zoom;
    const margin = vw * 0.5;
    return {
      wx: this.camScroll.x - margin + rand(0, vw + margin * 2),
      wy: this.camScroll.y - margin + rand(0, vh + margin * 2),
    };
  }

  private spawnBolt(slot: WeatherSlot, now: number): Bolt {
    const img = slot.bolts[Math.floor(Math.random() * slot.bolts.length)];
    // Same 0.55-1.2 scale band as the Discord _add_bolt, grown a little for
    // full-screen viewports.
    const scale = rand(0.55, 1.2) * Math.max(1, this.h / 480);
    const w = img.width * scale;
    const h = img.height * scale;
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
    ctx.clearRect(0, 0, this.w, this.h);
    const leaving = this.outgoing;
    const entering = this.current;
    const p = clamp01((now - this.transitionT0) / TRANSITION_MS);
    // Slot intensities (STAGED, user rule 14/09):
    //  - incoming starts at p≈0.18 — the veil darkens the sky FIRST, the
    //    rain/snow only becomes visible while the screen is already gloomy.
    //  - outgoing finishes fading at p≈0.72 — weather dies away before the
    //    sky finishes brightening.
    const inA = entering ? smoothstep((p - 0.18) / 0.82) : 0;
    const outA = leaving ? 1 - smoothstep(p / 0.72) : 0;

    // ---- 1. Blended mood tint (under everything) ----
    const lt = leaving ? tintParts(leaving.style.tint) : null;
    const it = entering ? tintParts(entering.style.tint) : null;
    if (lt || it) {
      const lr = lt ? lt[0] * outA : 0;
      const lg = lt ? lt[1] * outA : 0;
      const lb = lt ? lt[2] * outA : 0;
      const la = lt ? lt[3] * outA : 0;
      const ir = it ? it[0] * inA : 0;
      const ig = it ? it[1] * inA : 0;
      const ib = it ? it[2] * inA : 0;
      const ia = it ? it[3] * inA : 0;
      // Premultiplied blend of the two tints (clear sky contributes nothing).
      const a = Math.min(1, la + ia);
      if (a > 0.002) {
        const r = la + ia > 0 ? (lr + ir) / (la + ia) : 0;
        const g = la + ia > 0 ? (lg + ig) / (la + ia) : 0;
        const b = la + ia > 0 ? (lb + ib) / (la + ia) : 0;
        ctx.fillStyle = `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${a.toFixed(3)})`;
        ctx.fillRect(0, 0, this.w, this.h);
      }
    }

    // ---- 2. Cloud veil: the sky-darkening gradient ----
    let veilA = 0;
    if (entering && leaving) {
      // Weather -> weather: a smaller bridging pulse.
      veilA = VEIL_ENTER_PEAK * 0.6 * Math.sin(Math.PI * p);
    } else if (entering) {
      // Clear -> weather: triangle peaking mid-transition ("tối dần rồi mưa").
      veilA = VEIL_ENTER_PEAK * (p < 0.5 ? p / 0.5 : 1 - (p - 0.5) / 0.5);
    } else if (leaving) {
      // Weather -> clear: veil starts low and lifts ("trời sáng dần lên").
      veilA = VEIL_LEAVE_PEAK * Math.pow(1 - p, 1.4);
    }
    if (veilA > 0.004) {
      const grad = ctx.createLinearGradient(0, 0, 0, this.h);
      // Heavier at the top (clouds gather overhead first).
      grad.addColorStop(0, `rgba(8,11,20,${veilA.toFixed(3)})`);
      grad.addColorStop(0.55, `rgba(10,14,24,${(veilA * 0.72).toFixed(3)})`);
      grad.addColorStop(1, `rgba(13,17,28,${(veilA * 0.45).toFixed(3)})`);
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, this.w, this.h);
    }

    // ---- 3. Gust burst (bridges both directions) ----
    if (this.gust.length > 0 && p < 0.85) {
      const burstA = Math.sin((p / 0.85) * Math.PI);
      for (const g of this.gust) {
        ctx.globalAlpha = g.alpha * burstA;
        ctx.strokeStyle = g.color;
        ctx.lineWidth = g.width;
        ctx.beginPath();
        ctx.moveTo(g.x, g.y);
        ctx.lineTo(g.x + g.len, g.y);
        ctx.stroke();
        ctx.globalAlpha = g.alpha * burstA / 3;
        ctx.beginPath();
        ctx.moveTo(g.x - g.len / 2, g.y);
        ctx.lineTo(g.x, g.y);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // ---- 4. Weather layers (outgoing fades, incoming builds) ----
    if (leaving && outA > 0.01) {
      this.drawClouds(leaving, outA, now);
      this.drawSlot(leaving, outA, now);
    }
    if (entering && inA > 0.01) {
      this.drawClouds(entering, inA, now);
      this.drawSlot(entering, inA, now);
    }
  }

  /** Build the dark shadow copy of cloud.png ONCE (white sprite -> ekonia
   *  shadow color rgb(33,36,51)) via source-atop on an offscreen canvas. */
  private buildCloudShadow(src: HTMLImageElement): HTMLCanvasElement {
    const cv = document.createElement("canvas");
    cv.width = src.width;
    cv.height = src.height;
    const c2 = cv.getContext("2d")!;
    c2.drawImage(src, 0, 0);
    c2.globalCompositeOperation = "source-atop";
    c2.fillStyle = "rgb(33,36,51)"; // 0x21,0x24,0x33 = ekonia tint
    c2.fillRect(0, 0, cv.width, cv.height);
    this.cloudShadowImg = cv;
    return cv;
  }

  /** Draw the cloud-shadow blobs of ONE slot at the given intensity.
   *  WORLD-SPACE: the render position = world anchor − camera scroll
   *  (divided by zoom, same convention as the rain sheets), so the blob
   *  stays put over the same patch of ground while the player walks.
   *  Life fade: ease in over the first 12% and out over the last 12% of
   *  the 28 s life — never pops in/out (ekonia gradient ramp parity). */
  private drawClouds(slot: WeatherSlot, intensity: number, now: number): void {
    const img = this.cloudImg;
    if (!img || slot.clouds.length === 0) return;
    const ctx = this.ctx;
    // FULL world anchoring: screen pos = (world − camera scroll) × zoom —
    // the exact Phaser camera transform, so the blob moves 1:1 with the
    // ground while the player walks (only its own 9 px/s wind drifts it).
    for (const c of slot.clouds) {
      const age = (now - c.bornAt) / CLOUD_SHADOW.lifeMs;
      const fade = Math.min(1, Math.min(age, 1 - age) / 0.12);
      if (fade <= 0) continue;
      const x = (c.wx - this.camScroll.x) * this.camScroll.zoom;
      const y = (c.wy - this.camScroll.y) * this.camScroll.zoom;
      // Cull blobs far off-screen (cheap; seeds can land just outside).
      const w = img.width * c.scale;
      const h = img.height * c.scale;
      if (x < -w || x > this.w + w || y < -h || y > this.h + h) continue;
      // DARK SHADOW SPRITE: the source cloud.png is WHITE — painting it raw
      // reads as a white blob. A pre-tinted offscreen copy (recolors only the
      // sprite's own opaque pixels to the ekonia shadow color, alpha shape
      // untouched) is what gets drawn — a true dark translucent shadow.
      const shadow = this.cloudShadowImg ?? this.buildCloudShadow(img);
      ctx.globalAlpha = Math.min(1, CLOUD_SHADOW.alpha * intensity * fade);
      ctx.drawImage(shadow, x - w / 2, y - h / 2, w, h);
    }
    ctx.globalAlpha = 1;
  }

  /** Draw ONE weather slot at the given intensity multiplier (0..1). */
  private drawSlot(slot: WeatherSlot, alpha: number, now: number): void {
    const ctx = this.ctx;
    if (slot.proceduralKind) {
      this.drawProcedural(slot, alpha);
    } else if (slot.near && slot.far) {
      this.drawSheets(slot, alpha, now);
    }
    // Storm bolt: real pack artwork + glow + brief ambient flash.
    if (slot.bolt && now < slot.bolt.flashUntil) {
      const b = slot.bolt;
      const age = now - (b.until - 300);
      const fade = Math.max(0, Math.min(1, 1 - age / 300));
      if (!b.distant && now < b.until) {
        ctx.save();
        ctx.globalAlpha = 0.9 * fade * alpha;
        // Soft glow behind the bolt sprite (Discord composites a Gaussian
        // blur of the bolt at ~0.9 alpha).
        ctx.shadowColor = "rgba(210,225,255,0.9)";
        ctx.shadowBlur = 18;
        ctx.drawImage(b.img, b.x, b.y, b.w, b.h);
        ctx.restore();
      }
      // Ambient flash brightens the whole screen briefly (never a whiteout).
      ctx.fillStyle = `rgba(226,236,255,${(0.10 * fade * alpha).toFixed(3)})`;
      ctx.fillRect(0, 0, this.w, this.h);
    }
  }

  /** The Discord renderer's seamless scroll: tile the sheet across the
   * viewport wrapped by the sheet size, offset by a continuously-advancing
   * phase. Near layer scrolls at full speed; the far layer is phase-shifted
   * (+137px x like the Discord renderer) and fainter. Wind drifts RIGHT with
   * the far layer at 0.6x (Discord parity) at a different height. */
  private drawSheets(slot: WeatherSlot, intensity: number, now: number): void {
    const ctx = this.ctx;
    const style = slot.style;
    const dist = (now / 1000) * style.speedPx;
    // Visibility boost (rain pack art is faint — see STYLES.alphaBoost).
    const boost = style.alphaBoost ?? 1;

    const drawLayer = (sheet: Sheet, alpha: number, ox: number, oy: number): void => {
      ctx.globalAlpha = Math.min(1, alpha * intensity * boost);
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

    const z = this.camScroll.zoom;
    if (style.horizontal) {
      // Wind drifts RIGHT; the camera scroll slides BOTH layers sideways.
      const ox = dist - this.camScroll.x * z * NEAR_CAM_PARALLAX;
      // Far layer drifts slower (0.6x parallax) phase-shifted 512px — same
      // numbers as the Discord _scroll_overlays wind branch. Both layers tile
      // the full viewport; the 96px far offset just de-correlates the rows.
      drawLayer(slot.far!, FAR_ALPHA, ox * 0.6 + 512 - this.camScroll.x * z * FAR_CAM_PARALLAX, 96);
      drawLayer(slot.near!, NEAR_ALPHA, ox, 0);
    } else {
      // Falling particles scroll DOWN (the paste origin grows with the
      // offset, exactly like the Discord _tile_paste); the camera scroll is
      // subtracted so the field stays put in WORLD space while the viewport
      // moves across it. The far layer inherits LESS of the scroll (deeper
      // parallax) and keeps its 137px x phase shift.
      const oy = dist;
      drawLayer(slot.far!, FAR_ALPHA, 137 - this.camScroll.x * z * FAR_CAM_PARALLAX,
        oy + slot.far!.h / 3 - this.camScroll.y * z * FAR_CAM_PARALLAX);
      drawLayer(slot.near!, NEAR_ALPHA, -this.camScroll.x * z * NEAR_CAM_PARALLAX,
        oy - this.camScroll.y * z * NEAR_CAM_PARALLAX);
    }
  }

  /** Procedural layers (fog + texture-failure fallback) at a given intensity. */
  private drawProcedural(slot: WeatherSlot, intensity: number): void {
    const ctx = this.ctx;
    const kind = slot.proceduralKind!;
    // WORLD-SPACE for snow/rain flakes + streaks (fog stays screen-space —
    // it's atmosphere): each particle's screen position = world position −
    // camera scroll × zoom × layer parallax, so the field anchors to the map
    // exactly like the tiled sheets. Particles carry their own world offset
    // (p.wOffset) added to the shared parallax offset.
    const worldSpace = kind === "flake" || kind === "streak";
    const z = this.camScroll.zoom;
    const worldOff = worldSpace
      ? { x: this.camScroll.x * z * NEAR_CAM_PARALLAX, y: this.camScroll.y * z * NEAR_CAM_PARALLAX }
      : { x: 0, y: 0 };
    const farWorldOff = worldSpace
      ? { x: this.camScroll.x * z * FAR_CAM_PARALLAX, y: this.camScroll.y * z * FAR_CAM_PARALLAX }
      : { x: 0, y: 0 };
    const drawLayer = (layer: Particle[], off: { x: number; y: number }): void => {
      for (const p of layer) {
        const px = p.x - off.x;
        const py = p.y - off.y;
        ctx.globalAlpha = p.alpha * intensity;
        if (kind === "streak") {
          const vlen = Math.hypot(p.vx, p.vy) || 1;
          const ux = (p.vx / vlen) * p.len;
          const uy = (p.vy / vlen) * p.len;
          ctx.strokeStyle = p.color;
          ctx.lineWidth = p.width;
          ctx.beginPath();
          ctx.moveTo(px, py);
          ctx.lineTo(px - ux, py - uy);
          ctx.stroke();
        } else if (kind === "flake") {
          // Organic flakes: soft circle for small ones, faceted diamond for
          // big ones + a gentle twinkle (alpha breathes with phase) so the
          // field never reads as one repeated strip.
          const twinkle = 0.78 + 0.22 * Math.sin(p.phase * 1.7);
          ctx.globalAlpha = Math.min(1, p.alpha * intensity * twinkle);
          if (p.len < 2.6) {
            ctx.fillStyle = p.color;
            ctx.beginPath();
            ctx.arc(px, py, p.len * 0.9, 0, TAU);
            ctx.fill();
          } else {
            ctx.fillStyle = p.color;
            ctx.beginPath();
            ctx.moveTo(px, py - p.len);
            ctx.lineTo(px + p.len * 0.8, py);
            ctx.lineTo(px, py + p.len);
            ctx.lineTo(px - p.len * 0.8, py);
            ctx.closePath();
            ctx.fill();
          }
        } else if (kind === "mist") {
          const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.len);
          g.addColorStop(0, p.color);
          g.addColorStop(1, "rgba(0,0,0,0)");
          ctx.globalAlpha = p.alpha * 0.5 * intensity;
          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.ellipse(p.x, p.y, p.len, p.width, 0, 0, TAU);
          ctx.fill();
        } else {
          // Wind dash + faint tail trailing left (screen-space gusts).
          ctx.strokeStyle = p.color;
          ctx.lineWidth = p.width;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(p.x + p.len, p.y);
          ctx.stroke();
          ctx.globalAlpha = (p.alpha * intensity) / 3;
          ctx.beginPath();
          ctx.moveTo(p.x - p.len / 2, p.y);
          ctx.lineTo(p.x, p.y);
          ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
    };
    drawLayer(slot.farP, farWorldOff);
    drawLayer(slot.nearP, worldOff);
  }
}

// Singleton wired from main.ts (one overlay for the whole page).
export const weatherFx = new WeatherFx();
