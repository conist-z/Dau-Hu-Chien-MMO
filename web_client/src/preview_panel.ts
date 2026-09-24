// Preview panel (DEV/PREVIEW builds only): a floating remote control for the
// server's REAL mechanics — pin the in-game clock (night gate), summon
// meteors, force weather, spawn/clear the mob pack, switch preview maps, and
// read a live status line. Talks to the local preview stack
// (scripts/_preview_stack.py) via preview_cmd frames; the stack answers with
// push toasts (the log feed) and preview_state dumps (the status line).
//
// Gated by `?preview=1` (or #preview) so production clients never build it.

type Cmd = "clock" | "meteor" | "weather" | "zombies" | "animals" | "map" | "state" | "bite";

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

export class PreviewPanel {
  private root: HTMLDivElement | null = null;
  private log: HTMLDivElement | null = null;
  private status: HTMLDivElement | null = null;
  private hooks: PanelHooks | null = null;
  private minimized = false;
  private statusTimer: number | null = null;

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
      "background:rgba(10,12,20,0.55)", "backdrop-filter:blur(2px)",
      "color:#dfe6ff",
      "border:1px solid #3b4a7a", "border-radius:10px",
      "font:12px/1.5 monospace", "padding:10px", "width:244px",
      "box-shadow:0 4px 18px rgba(0,0,0,0.5)", "user-select:none",
    ].join(";");

    const head = document.createElement("div");
    head.style.cssText = "display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;cursor:move";
    head.innerHTML = `<b style="color:#ffd75e">🧪 PREVIEW</b>`;
    const mini = document.createElement("button");
    mini.textContent = "–";
    mini.style.cssText = btnStyle("#2a3352", "22px");
    mini.onclick = () => {
      this.minimized = !this.minimized;
      body.style.display = this.minimized ? "none" : "block";
      mini.textContent = this.minimized ? "+" : "–";
    };

    // DRAGGABLE + position memory: drag by the header; the position is
    // remembered in localStorage so EVERY future session starts where the
    // user last parked the panel (and it stops covering the status rail).
    this.restorePosition(root);
    this.makeDraggable(root, head);
    head.appendChild(mini);
    root.appendChild(head);

    const body = document.createElement("div");
    root.appendChild(body);

    const section = (label: string): HTMLDivElement => {
      const d = document.createElement("div");
      d.style.cssText = "margin:4px 0 2px;color:#8fa3d9";
      d.textContent = label;
      body.appendChild(d);
      return d;
    };
    const row = (): HTMLDivElement => {
      const d = document.createElement("div");
      d.style.cssText = "display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px";
      body.appendChild(d);
      return d;
    };
    const btn = (label: string, fn: () => void, color = "#2a3352"): void => {
      const b = document.createElement("button");
      b.textContent = label;
      b.style.cssText = btnStyle(color);
      b.onclick = fn;
      return void body.lastElementChild!.appendChild(b);
    };

    // ---- clock ----
    section("⏰ Giờ trong game (gate đêm)");
    row();
    btn("☀️ Day", () => this.send("clock", "day"), "#3a3312");
    btn("🌆 Dusk", () => this.send("clock", "dusk"));
    btn("🌙 Night", () => this.send("clock", "night"), "#1a2a52");
    btn("Tự nhiên", () => this.send("clock", "normal"));

    // ---- meteors ----
    section("☄️ Thiên thạch (bigmap)");
    row();
    btn("Tại chỗ", () => this.send("meteor", "here"), "#5a1a1a");
    btn("Random gần", () => this.send("meteor", "rand"), "#5a1a1a");
    btn("Auto ON", () => this.send("meteor", "auto"), "#1a3a1a");
    btn("Auto OFF", () => this.send("meteor", "off"));

    // ---- weather ----
    section("🌧️ Thời tiết");
    row();
    for (const [key, label] of WEATHERS) {
      btn(label, () => this.send("weather", key));
    }

    // ---- mobs ----
    section("👾 Quái (spawn theo map)");
    row();
    btn("Spawn 10", () => this.send("zombies", "pack"), "#1a3a1a");
    btn("Dịch sát mặt", () => this.send("bite"), "#5a3a1a");
    btn("Dọn sạch", () => this.send("zombies", "none"), "#5a1a1a");

    // ---- wildlife (daytime animals) ----
    section("🦌 Động vật ban ngày");
    row();
    btn("Spawn random", () => this.send("animals", "rand"), "#1a3a1a");
    btn("Thỏ", () => this.send("animals", "bunny"), "#1a2a1a");
    btn("Nai", () => this.send("animals", "deer"), "#1a2a1a");
    btn("Heo", () => this.send("animals", "boar"), "#1a2a1a");
    btn("Gấu", () => this.send("animals", "bear"), "#1a2a1a");
    btn("Sói", () => this.send("animals", "wolf"), "#1a2a1a");
    btn("Dọn", () => this.send("animals", "none"), "#5a1a1a");

    // ---- map switch ----
    section("🗺️ Đổi map preview");
    row();
    for (const [id, label] of MAPS) {
      btn(label, () => this.send("map", id), "#33321a");
    }

    // ---- status effects (DEMO — local only, no server frames) ----
    section("✨ Status effects (demo)");
    row();
    btn("ON", () => this.startStatusDemo(), "#1a3a1a");
    btn("OFF", () => this.stopStatusDemo(), "#5a1a1a");

    // ---- status + log ----
    section("📡 Trạng thái");
    this.status = document.createElement("div");
    this.status.style.cssText = "color:#7fff9f;white-space:pre-wrap;margin-bottom:4px";
    this.status.textContent = "…";
    body.appendChild(this.status);
    const refresh = document.createElement("button");
    refresh.textContent = "↻ Refresh";
    refresh.style.cssText = btnStyle("#2a3352");
    refresh.onclick = () => this.send("state");
    body.appendChild(refresh);

    this.log = document.createElement("div");
    this.log.style.cssText =
      "margin-top:6px;max-height:140px;overflow-y:auto;color:#a9b7e8;white-space:pre-wrap;border-top:1px solid #3b4a7a;padding-top:4px";
    body.appendChild(this.log);

    document.body.appendChild(root);
    this.root = root;
    this.send("state");
  }

  /** Restore the last-dragged position (every session, same browser). */
  private restorePosition(root: HTMLDivElement): void {
    try {
      const raw = localStorage.getItem("preview-panel-pos");
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
          "preview-panel-pos",
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

  /** DEMO: fake 3 effects with LIVE countdowns and the new LEVEL tier
   *  (infection L2 gold, poison L3 red, poison2 L4 purple — tier 3/4 pulse
   *  a faint aura). poison2 expires first and the rest slide RIGHT. */
  private startStatusDemo(): void {
    this.stopStatusDemo();
    const hud = (window as unknown as { hud?: { setStatusEffects(e: Array<[string, number, number?]>): void } }).hud;
    if (!hud) return;
    let effects: Array<[string, number, number?]> = [
      ["infection", 30, 2], ["poison", 20, 3], ["poison2", 10, 4],
    ];
    const tick = (): void => {
      effects = effects
        .map(([id, s, l]) => [id, s - 1, l] as [string, number, number?])
        .filter(([, s]) => s > 0);
      hud.setStatusEffects(effects);
      // All effects expired: stop the loop instead of spamming empty rails.
      this.statusTimer = effects.length ? window.setTimeout(tick, 1000) : null;
    };
    tick();
  }

  private stopStatusDemo(): void {
    if (this.statusTimer !== null) {
      clearTimeout(this.statusTimer);
      this.statusTimer = null;
    }
    (window as unknown as { hud?: { setStatusEffects(e: Array<[string, number, number?]>): void } })
      .hud?.setStatusEffects([]);
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
    const status = Array.isArray(s.status) ? (s.status as Array<[string, number, number]>) : [];
    const kinds = Array.isArray(s.mob_kinds) ? (s.mob_kinds as string[]).join(",") : "";
    this.status.textContent =
      `map: ${s.map}\n` +
      `giờ: ${s.clock} (${s.night ? "ĐÊM" : "day"})\n` +
      `thời tiết: ${s.weather}\n` +
      `quái: ${s.zombies} [${kinds}] | ☄️: ${s.meteors}\n` +
      `status: ${status.length ? JSON.stringify(status) : "KHÔNG"}\n` +
      `đã rơi đêm nay: ${s.felled_tonight} | auto: ${s.auto_meteor ? "ON" : "OFF"}`;
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
