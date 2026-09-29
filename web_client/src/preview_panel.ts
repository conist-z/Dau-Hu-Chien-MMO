// Preview panel (DEV/PREVIEW builds only): a floating remote control for the
// server's REAL mechanics. Talks to the local preview stack
// (scripts/_preview_stack.py) via preview_cmd frames; the stack answers with
// push toasts (the log feed) and preview_state dumps (the status line).
//
// MULTI-SESSION (user 28/09): every browser tab is its own server-side user
// (see scripts/_local_game_stack.py) — 2-4 tabs play in separate worlds at
// once. The panel therefore shows THIS tab's identity in the header and the
// status line so instances never get confused with each other.
//
// OVERHAUL (user 28/09): collapsed sections keep the panel compact —
// "Thế giới" (clock/meteors/weather), "Quái & Động vật", "Nhân vật"
// (heal/kill/respawn/status/teleport), "Map" — plus a boxed log. Sections
// are <details> so the reviewer opens only what they need.
//
// Gated by `?preview=1` (or #preview) so production clients never build it.

type Cmd =
  | "clock" | "meteor" | "weather" | "zombies" | "animals" | "map"
  | "state" | "bite" | "heal" | "kill" | "respawn" | "status" | "tp"
  | "hurt"; // 🩸 Nặng Má: HP% để thử bloody screen (main.ts lowHpFx)

interface PanelHooks {
  send: (frame: Record<string, unknown>) => void;
}

const MAPS: Array<[string, string]> = [
  ["ekonia/overworld", "Bigmap"],
  ["ekonia/forest", "Rừng"],
  ["ekonia/cave_area1", "Hang"],
];

const WEATHERS: Array<[string, string]> = [
  ["normal", "Tự động"],
  ["rain", "Mưa"],
  ["heavy_rain", "Mưa to"],
  ["storm", "Bão"],
  ["snow", "Tuyết"],
  ["cold", "Rét"],
  ["wind", "Gió"],
  ["sun_clouds", "Nắng"],
  ["cloud_shadow", "Mây đen"],
];

/** Real server-side debuffs (game.status_effects.EFFECTS). */
const STATUSES: Array<[string, string]> = [
  ["infection", "Thối Rửa"],
  ["poison", "Độc"],
];

const LS_POS_KEY = "preview-panel-pos";
const LS_OPEN_KEY = "preview-panel-open";

export class PreviewPanel {
  private root: HTMLDivElement | null = null;
  private log: HTMLDivElement | null = null;
  private status: HTMLDivElement | null = null;
  private hooks: PanelHooks | null = null;
  private demoTimer: number | null = null;

  attach(hooks: PanelHooks): void {
    if (this.root || !enabledByQuery()) return;
    this.hooks = hooks;
    this.build();
  }

  private send(cmd: Cmd, value?: string | number): void {
    this.hooks?.send({ type: "preview_cmd", cmd, value });
  }

  private build(): void {
    const root = document.createElement("div");
    root.id = "preview-panel";
    root.style.cssText = [
      "position:fixed", "top:52px", "right:8px", "z-index:3000",
      // Translucent so the map/status rail underneath stays readable.
      "background:rgba(10,12,20,0.72)", "backdrop-filter:blur(2px)",
      "color:#dfe6ff",
      "border:1px solid #3b4a7a", "border-radius:10px",
      "font:12px/1.5 monospace", "padding:8px 10px", "width:248px",
      "max-height:calc(100vh - 70px)", "overflow-y:auto",
      "box-shadow:0 4px 18px rgba(0,0,0,0.5)", "user-select:none",
    ].join(";");

    const head = document.createElement("div");
    head.style.cssText = "display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;cursor:move";
    head.innerHTML = `<b style="color:#ffd75e">🧪 PREVIEW <span id="preview-tab-id" style="color:#8fa3d9;font-weight:400"></span></b>`;
    const mini = document.createElement("button");
    mini.textContent = "–";
    mini.style.cssText = btnStyle("#2a3352", "22px");
    mini.onclick = () => this.setMinimized(!this.minimized);

    // DRAGGABLE + position memory: drag by the header; the position is
    // remembered in localStorage so EVERY future session starts where the
    // user last parked the panel (and it stops covering the status rail).
    this.restorePosition(root);
    this.makeDraggable(root, head);
    head.appendChild(mini);
    root.appendChild(head);

    const body = document.createElement("div");
    body.id = "preview-body";
    root.appendChild(body);
    this.root = root;

    // ---- helpers ----
    const details = (label: string, open = false): HTMLDivElement => {
      const det = document.createElement("details");
      det.style.cssText = "margin:2px 0;border-top:1px solid #3b4a7a;padding-top:3px";
      det.open = open;
      const summary = document.createElement("summary");
      summary.textContent = label;
      summary.style.cssText = "cursor:pointer;color:#8fa3d9;list-style:none;user-select:none";
      det.appendChild(summary);
      const inner = document.createElement("div");
      inner.style.cssText = "margin-top:4px";
      det.appendChild(inner);
      body.appendChild(det);
      // Remember open/closed across sessions.
      det.addEventListener("toggle", () => {
        try {
          const openKeys = JSON.parse(localStorage.getItem(LS_OPEN_KEY) || "{}") as Record<string, boolean>;
          openKeys[label] = det.open;
          localStorage.setItem(LS_OPEN_KEY, JSON.stringify(openKeys));
        } catch { /* storage unavailable — fine this session */ }
      });
      try {
        const openKeys = JSON.parse(localStorage.getItem(LS_OPEN_KEY) || "{}") as Record<string, boolean>;
        det.open = !!openKeys[label];
      } catch { /* keep default */ }
      return inner;
    };
    const row = (parent: HTMLElement): HTMLDivElement => {
      const d = document.createElement("div");
      d.style.cssText = "display:flex;flex-wrap:wrap;gap:4px;margin-bottom:5px";
      parent.appendChild(d);
      return d;
    };
    const btn = (parent: HTMLElement, label: string, fn: () => void, color = "#2a3352"): void => {
      const b = document.createElement("button");
      b.textContent = label;
      b.style.cssText = btnStyle(color);
      b.onclick = fn;
      parent.appendChild(b);
    };

    // ---- 🌍 Thế giới: giờ / thiên thạch / thời tiết ----
    const world = details("🌍 Thế giới", true);
    row(world);
    btn(world, "☀️ Ngày", () => this.send("clock", "day"), "#3a3312");
    btn(world, "🌆 Hoàng hôn", () => this.send("clock", "dusk"));
    btn(world, "🌙 Đêm", () => this.send("clock", "night"), "#1a2a52");
    btn(world, "Tự nhiên", () => this.send("clock", "normal"));
    const timeRow = row(world);
    const timeInput = document.createElement("input");
    timeInput.placeholder = "HH:MM (vd 02:30)";
    timeInput.style.cssText =
      "flex:1;min-width:90px;background:#0e1424;color:#dfe6ff;border:1px solid #4a5a8a;border-radius:6px;padding:3px 6px;font:11px monospace";
    timeRow.appendChild(timeInput);
    const timeBtn = document.createElement("button");
    timeBtn.textContent = "Đặt giờ";
    timeBtn.style.cssText = btnStyle("#3a3312");
    timeBtn.onclick = () => {
      const v = timeInput.value.trim();
      if (v) this.send("clock", v);
    };
    timeRow.appendChild(timeBtn);
    row(world);
    btn(world, "☄️ Tại chỗ", () => this.send("meteor", "here"), "#5a1a1a");
    btn(world, "☄️ Gần", () => this.send("meteor", "rand"), "#5a1a1a");
    btn(world, "☄️ Auto ON", () => this.send("meteor", "auto"), "#1a3a1a");
    btn(world, "☄️ Auto OFF", () => this.send("meteor", "off"));
    row(world);
    for (const [key, label] of WEATHERS) {
      btn(world, label, () => this.send("weather", key));
    }

    // ---- 👾 Quái & động vật ----
    const mobs = details("👾 Quái & động vật", true);
    row(mobs);
    btn(mobs, "Spawn 10 quái", () => this.send("zombies", "pack"), "#1a3a1a");
    btn(mobs, "Cắn -10", () => this.send("bite"), "#5a3a1a");
    btn(mobs, "Cắn -50", () => this.send("bite", 50), "#5a3a1a");
    btn(mobs, "Dọn quái", () => this.send("zombies", "none"), "#5a1a1a");
    row(mobs);
    btn(mobs, "+1 động vật", () => this.send("animals", "rand"), "#1a3a1a");
    btn(mobs, "Thỏ", () => this.send("animals", "bunny"), "#1a2a1a");
    btn(mobs, "Nai", () => this.send("animals", "deer"), "#1a2a1a");
    btn(mobs, "Heo", () => this.send("animals", "boar"), "#1a2a1a");
    btn(mobs, "Gấu", () => this.send("animals", "bear"), "#1a2a1a");
    btn(mobs, "Sói", () => this.send("animals", "wolf"), "#1a2a1a");
    btn(mobs, "Dọn vật", () => this.send("animals", "none"), "#5a1a1a");

    // ---- 🧍 Nhân vật: heal / kill / respawn / status / teleport ----
    const me = details("🧍 Nhân vật", true);
    row(me);
    btn(me, "Rail demo", () => this.startStatusDemo(), "#2a2a3a");
    btn(me, "❤️ Hồi full", () => this.send("heal"), "#1a3a1a");
    // 🩸 Nặng Má: HP = 15% — ngưỡng bật bloody screen (xem main.ts lowHpFx).
    btn(me, "🩸 Nặng Má", () => this.send("hurt", "0.15"), "#3a1418");
    btn(me, "☠️ Chết", () => this.send("kill"), "#5a1a1a");
    btn(me, "✨ Hồi sinh", () => this.send("respawn"), "#1a2a52");
    row(me);
    for (const [key, label] of STATUSES) {
      btn(me, `☣️ ${label}`, () => this.send("status", key), "#1a2a1a");
    }
    btn(me, "Xoá hiệu ứng", () => this.send("status", "off"), "#5a1a1a");
    const tpRow = row(me);
    const tpInput = document.createElement("input");
    tpInput.placeholder = "x,y (trống = ngẫu nhiên)";
    tpInput.style.cssText =
      "flex:1;min-width:90px;background:#0e1424;color:#dfe6ff;border:1px solid #4a5a8a;border-radius:6px;padding:3px 6px;font:11px monospace";
    tpRow.appendChild(tpInput);
    const tpBtn = document.createElement("button");
    tpBtn.textContent = "🌀 Tới";
    tpBtn.style.cssText = btnStyle("#33321a");
    tpBtn.onclick = () => {
      const v = tpInput.value.trim();
      this.send("tp", v || "rand");
    };
    tpRow.appendChild(tpBtn);
    row(me);
    btn(me, "Về spawn", () => this.send("tp", "spawn"), "#33321a");
    btn(me, "Đi chỗ khác", () => this.send("tp", "rand"), "#33321a");

    // ---- 🗺️ Map ----
    const mapSec = details("🗺️ Đổi map");
    row(mapSec);
    for (const [id, label] of MAPS) {
      btn(mapSec, label, () => this.send("map", id), "#33321a");
    }
    // 🟧 Box chặn: local collision overlay (game.ts setCollisionDebug) —
    // đỏ = ô chặn vuông, cam = phần mask thực sự chặn. Toggle qua window
    // event — cùng đường với phím F3, không cần frame server.
    btn(mapSec, "🟧 Box chặn", () => {
      const scene = (window as unknown as {
        gameScene?: { getCollisionDebug(): boolean };
      }).gameScene;
      const on = !(scene?.getCollisionDebug() ?? false);
      window.dispatchEvent(new CustomEvent("toggle-collision", { detail: on }));
    }, "#3a2a12");

    // ---- 📡 status + log ----
    const st = details("📡 Trạng thái & log", true);
    this.status = document.createElement("div");
    this.status.style.cssText = "color:#7fff9f;white-space:pre-wrap;margin-bottom:4px";
    this.status.textContent = "…";
    st.appendChild(this.status);
    const refresh = document.createElement("button");
    refresh.textContent = "↻ Refresh";
    refresh.style.cssText = btnStyle("#2a3352");
    refresh.onclick = () => this.send("state");
    st.appendChild(refresh);
    this.log = document.createElement("div");
    this.log.style.cssText =
      "margin-top:5px;max-height:120px;overflow-y:auto;color:#a9b7e8;white-space:pre-wrap;border-top:1px solid #3b4a7a;padding-top:4px";
    st.appendChild(this.log);

    document.body.appendChild(root);
    // Auto-refresh the status line every 5s so the reviewer never pokes
    // Refresh manually to see mob counts / HP after an action.
    window.setInterval(() => {
      if (!this.minimized) this.send("state");
    }, 5000);
    this.send("state");
  }

  private minimized = false;

  private setMinimized(min: boolean): void {
    this.minimized = min;
    const body = document.getElementById("preview-body");
    const mini = this.root?.querySelector("button");
    if (body) body.style.display = min ? "none" : "block";
    if (mini) mini.textContent = min ? "+" : "–";
  }

  /** Restore the last-dragged position (every session, same browser). */
  private restorePosition(root: HTMLDivElement): void {
    try {
      const raw = localStorage.getItem(LS_POS_KEY);
      if (!raw) return;
      const { x, y } = JSON.parse(raw) as { x: number; y: number };
      if (typeof x !== "number" || typeof y !== "number") return;
      root.style.right = "auto";
      root.style.left = `${x}px`;
      root.style.top = `${y}px`;
    } catch { /* corrupted storage — keep the default corner */ }
  }

  /** Header-drag (mouse + touch), clamped to the viewport, saved on release. */
  private makeDraggable(root: HTMLDivElement, handle: HTMLElement): void {
    let sx = 0, sy = 0, ox = 0, oy = 0, dragging = false;
    const save = (): void => {
      const r = root.getBoundingClientRect();
      try {
        localStorage.setItem(
          LS_POS_KEY,
          JSON.stringify({ x: Math.round(r.left), y: Math.round(r.top) }),
        );
      } catch { /* storage unavailable — drag still works this session */ }
    };
    const clamp = (): void => {
      const r = root.getBoundingClientRect();
      const maxX = window.innerWidth - r.width;
      const maxY = window.innerHeight - r.height;
      const x = Math.min(Math.max(0, r.left), Math.max(0, maxX));
      const y = Math.min(Math.max(0, r.top), Math.max(0, maxY));
      root.style.right = "auto";
      root.style.left = `${x}px`;
      root.style.top = `${y}px`;
    };
    const down = (cx: number, cy: number): void => {
      const r = root.getBoundingClientRect();
      // Anchor on the panel's current top-left so right-positioned panels
      // don't jump when the drag starts.
      root.style.right = "auto";
      root.style.left = `${r.left}px`;
      root.style.top = `${r.top}px`;
      sx = cx; sy = cy; ox = r.left; oy = r.top; dragging = true;
    };
    const move = (cx: number, cy: number): void => {
      if (!dragging) return;
      root.style.left = `${ox + cx - sx}px`;
      root.style.top = `${oy + cy - sy}px`;
      clamp();
    };
    const up = (): void => {
      if (!dragging) return;
      dragging = false;
      save();
    };
    handle.addEventListener("mousedown", (e) => {
      if ((e.target as HTMLElement).tagName === "BUTTON") return;
      down(e.clientX, e.clientY);
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => move(e.clientX, e.clientY));
    window.addEventListener("mouseup", up);
    handle.addEventListener("touchstart", (e) => {
      const t = e.touches[0];
      down(t.clientX, t.clientY);
    }, { passive: true });
    window.addEventListener("touchmove", (e) => {
      if (!dragging) return;
      const t = e.touches[0];
      move(t.clientX, t.clientY);
      e.preventDefault();
    }, { passive: false });
    window.addEventListener("touchend", up);
    window.addEventListener("resize", clamp);
  }

  /** push toasts land here too (the preview stack tags them "[preview]"). */
  feed(message: string): void {
    if (!this.log) return;
    const line = document.createElement("div");
    line.textContent = message;
    this.log.prepend(line);
    while (this.log.childElementCount > 40) this.log.lastElementChild!.remove();
  }

  onState(s: Record<string, unknown>): void {
    if (!this.status) return;
    const self = (s.self ?? {}) as {
      name?: string; hp?: number; max_hp?: number;
      x?: number; y?: number; dead?: boolean;
      effects?: Array<[string, number, number]>;
    };
    const kinds = Array.isArray(s.mob_kinds) ? (s.mob_kinds as string[]).join(",") : "";
    const effTxt = (self.effects ?? []).length
      ? JSON.stringify(self.effects)
      : "KHÔNG";
    // Tab identity: the server mints a unique name per connection — two side-
    // by-side preview tabs can never be confused.
    const tabId = document.getElementById("preview-tab-id");
    if (tabId && self.name) tabId.textContent = `· ${self.name}`;
    this.status.textContent =
      `map: ${s.map}\n` +
      `giờ: ${s.clock} (${s.night ? "ĐÊM" : "day"})\n` +
      `thời tiết: ${s.weather}\n` +
      `quái: ${s.zombies} [${kinds}] | ☄️: ${s.meteors}\n` +
      `tôi: ${self.name ?? "?"} ${self.hp ?? "?"}/${self.max_hp ?? "?"} HP` +
      `${self.dead ? " (CHẾT)" : ""} @ (${self.x ?? "?"},${self.y ?? "?"})\n` +
      `hiệu ứng: ${effTxt}\n` +
      `đã rơi đêm nay: ${s.felled_tonight} | auto: ${s.auto_meteor ? "ON" : "OFF"}`;
  }

  /** DEMO rail (kept for UI tests): fake 3 effects with LIVE countdowns.
   *  Prefer the REAL server-side status via the "Nhân vật" section. */
  private startStatusDemo(): void {
    this.stopStatusDemo();
    const hud = (window as unknown as {
      hud?: {
        setDemoStatusEffects(e: Array<[string, number, number?]> | null): void;
      };
    }).hud;
    if (!hud) return;
    let effects: Array<[string, number, number?]> = [
      ["infection", 30, 2], ["poison", 20, 3], ["poison2", 10, 4],
    ];
    const tick = (): void => {
      hud.setDemoStatusEffects(effects);
      effects = effects
        .map(([id, s, l]) => [id, s - 1, l] as [string, number, number?])
        .filter(([, s]) => s > 0);
      if (!effects.length) {
        hud.setDemoStatusEffects(null);
        this.demoTimer = null;
        return;
      }
      this.demoTimer = window.setTimeout(tick, 1000);
    };
    tick();
  }

  private stopStatusDemo(): void {
    if (this.demoTimer !== null) {
      clearTimeout(this.demoTimer);
      this.demoTimer = null;
    }
    (window as unknown as {
      hud?: {
        setDemoStatusEffects(e: Array<[string, number, number?]> | null): void;
      };
    }).hud?.setDemoStatusEffects(null);
  }
}

function btnStyle(bg: string, w = "auto"): string {
  return [
    `background:${bg}`, "color:#dfe6ff", "border:1px solid #4a5a8a",
    "border-radius:6px", "padding:3px 7px", "cursor:pointer",
    "font:11px monospace", w === "auto" ? "" : `width:${w}`,
  ].filter(Boolean).join(";");
}

function enabledByQuery(): boolean {
  return (
    new URLSearchParams(location.search).has("preview") ||
    location.hash.includes("preview")
  );
}

export const previewPanel = new PreviewPanel();
