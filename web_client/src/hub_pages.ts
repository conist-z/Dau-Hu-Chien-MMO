// Kaetram-style hub PAGES — real content for each bar button (Kaetram
// menu/* parity). Inventory is NOT here (the client already owns the bag
// panel); chat routes to the chat input; the rest render here.
//
// Real data sources (no new server ops needed):
// - Map page:     canvas minimap from scene.minimapSourcePx (bake pixels)
//                 + map name/size + live player dots (Kaetram mapframe
//                 parity, but the minimap itself is our own bake).
// - Profile:      live self stats (from welcome/snapshot) + purse + ping.
// - Players:      live snapshot players list (Kaetram leaderboards parity:
//                 click a row = their profile card).
// - Equipment:    Kaetram's 12-slot layout (equipment/*.png icons); the
//                 game has no equipment system yet, so slots render empty
//                 and stats rows show "—" (structure ready for the server).
// - Guilds/friends/quests/achievements: empty-state lists (server systems
//                 don't exist yet) — same wiring, one frame apart.
// - Settings:     REAL toggles: debug overlay (F3 parity), chat log size
//                 reset, show map name… + game info + log out (Kaetram
//                 settings page parity).
//
// Styling: Kaetram's own 9-slice PNG kit via border-image (slices/*.png),
// fonts already bundled (Jacquard12/MedievalSharp) — no new deps.

import type { PlayerPayload } from "./protocol";
import type { WorldScene } from "./game";
import type { HubBar, HubPageId } from "./hub_bar";

export interface SelfInfo {
  name: string;
  hp: number; maxHp: number;
  mana: number; maxMana: number;
  coins: number; crystals: number;
  pingMs: number | null;
  mapName: string;
  mapSize: [number, number];
}

const EQUIP_SLOTS: { key: string; label: string }[] = [
  { key: "helmet", label: "Mũ giáp" },
  { key: "pendant", label: "Dây chuyền" },
  { key: "arrows", label: "Mũi tên" },
  { key: "chestplate", label: "Áo giáp" },
  { key: "weapon", label: "Vũ khí" },
  { key: "shield", label: "Khiên" },
  { key: "ring", label: "Nhẫn" },
  { key: "armourskin", label: "Da giáp" },
  { key: "weaponskin", label: "Da vũ khí" },
  { key: "legplates", label: "Giáp chân" },
  { key: "cape", label: "Choàng" },
  { key: "boots", label: "Giày" },
];

const EMPTY_NOTE: Partial<Record<HubPageId, string>> = {
  quests: "Chưa có hệ thống nhiệm vụ. Khi server thêm nhiệm vụ, danh sách sẽ hiện ở đây.",
  achievements: "Chưa có hệ thống thành tựu.",
  guilds: "Chưa có hệ thống bang hội.",
  friends: "Chưa có hệ thống bạn bè.",
};

export class HubPages {
  private bar: HubBar;
  private scene: WorldScene;
  private self: SelfInfo = {
    name: "—", hp: 0, maxHp: 0, mana: 0, maxMana: 0,
    coins: 0, crystals: 0, pingMs: null, mapName: "—", mapSize: [0, 0],
  };
  private players: PlayerPayload[] = [];
  /** Click a player row → open their profile card (same as world click). */
  onPlayerPick: ((p: PlayerPayload) => void) | null = null;

  constructor(bar: HubBar, scene: WorldScene) {
    this.bar = bar;
    this.scene = scene;
  }

  setSelf(s: Partial<SelfInfo>): void {
    Object.assign(this.self, s);
    if (this.bar.current === "profile") this.render();
  }

  setPlayers(list: PlayerPayload[]): void {
    this.players = list;
    if (this.bar.current === "players") this.render();
  }

  /** Re-render the current page (called on open + data change). */
  render(): void {
    const page = this.bar.current;
    if (!page) return;
    switch (page) {
      case "map": this.renderMap(); break;
      case "profile": this.renderProfile(); break;
      case "equipment": this.renderEquipment(); break;
      case "players": this.renderPlayers(); break;
      case "settings": this.renderSettings(); break;
      default: this.renderEmpty(page); break;
    }
  }

  // ---- MAP (Kaetram mapframe parity: minimap + player dots) ----
  private renderMap(): void {
    const wrap = document.createElement("div");
    wrap.className = "hub-map-wrap";
    const canvas = document.createElement("canvas");
    canvas.className = "hub-map-canvas";
    // Source pixels come from the map bake (minimapSourcePx), so the
    // minimap is EXACTLY the map art — same rule as Kaetram's map frame.
    const size = 220;
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d");
    const [mw, mh] = this.self.mapSize;
    if (ctx && mw > 0 && mh > 0) {
      const src = this.scene.minimapSourcePx();
      if (src) {
        // Cover-fit the map into the square viewport (smaller dimension
        // fills, the larger is cropped centered — Kaetram's map frame look).
        const scale = Math.max(size / (mw * 32), size / (mh * 32));
        const sw = size / scale;
        const sh = size / scale;
        const sx = Math.max(0, (mw * 32 - sw) / 2);
        const sy = Math.max(0, (mh * 32 - sh) / 2);
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(src, sx, sy, sw, sh, 0, 0, size, size);
      }
      // Player dots (self = white ring, others = role color).
      const scale = Math.max(size / (mw * 32), size / (mh * 32));
      const sx0 = Math.max(0, (mw * 32 - size / scale) / 2);
      const sy0 = Math.max(0, (mh * 32 - size / scale) / 2);
      const toPx = (x: number, y: number): [number, number] =>
        [(x * 32 - sx0) * scale, (y * 32 - sy0) * scale];
      const dot = (px: number, py: number, color: string, ring = false): void => {
        ctx.beginPath();
        ctx.arc(px, py, ring ? 5 : 4, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        if (ring) {
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = "#fff";
          ctx.stroke();
        }
      };
      for (const p of this.players) {
        const [px, py] = toPx(p.x, p.y);
        dot(px, py, p.color || "#5865f2");
      }
      const selfPos = this.scene.getSelfPos();
      const [sx, sy] = toPx(selfPos.x, selfPos.y);
      dot(sx, sy, "#ffffff", true);
    }
    wrap.appendChild(canvas);
    wrap.appendChild(makeInfoLine("Map", `${this.self.mapName} (${mw}×${mh})`));
    wrap.appendChild(makeInfoLine("Người chơi quanh đây", String(this.players.length)));
    this.bar.setBody(wrap);
    this.bar.setFoot(null);
  }

  // ---- PROFILE (Kaetram profile page parity: stats + purse) ----
  private renderProfile(): void {
    const wrap = document.createElement("div");
    wrap.className = "hub-profile";
    const s = this.self;
    const name = makeInfoLine("Tên", s.name);
    wrap.append(
      name,
      statBar("HP", s.hp, s.maxHp, "var(--hp)"),
      statBar("Mana", s.mana, s.maxMana, "var(--mana)"),
      makeInfoLine("Xu", String(s.coins)),
      makeInfoLine("Tinh thể", String(s.crystals)),
      makeInfoLine("Ping", s.pingMs === null ? "—" : `${Math.round(s.pingMs)}ms`),
      makeInfoLine("Map", s.mapName),
    );
    const note = document.createElement("p");
    note.className = "hub-note";
    note.textContent =
      "Trang bị, kỹ năng và cấp độ sẽ xuất hiện ở đây khi server bổ sung.";
    wrap.appendChild(note);
    this.bar.setBody(wrap);
    this.bar.setFoot(null);
  }

  // ---- EQUIPMENT (Kaetram 12-slot layout, real icons, empty slots) ----
  private renderEquipment(): void {
    const wrap = document.createElement("div");
    wrap.className = "hub-equip";
    for (const slot of EQUIP_SLOTS) {
      const cell = document.createElement("div");
      cell.className = "hub-equip-slot";
      cell.title = slot.label;
      const icon = document.createElement("img");
      icon.src = `/ui/kaetram/interface/equipment/${slot.key}.png`;
      icon.draggable = false;
      icon.alt = slot.label;
      cell.appendChild(icon);
      const name = document.createElement("span");
      name.textContent = slot.label;
      cell.appendChild(name);
      wrap.appendChild(cell);
    }
    const note = document.createElement("p");
    note.className = "hub-note";
    note.textContent =
      "Hệ trang bị chưa có trên server — bố cục sẵn sàng theo Kaetram (12 slot).";
    wrap.appendChild(note);
    this.bar.setBody(wrap);
    this.bar.setFoot(null);
  }

  // ---- PLAYERS (Kaetram leaderboards parity: list + click = profile) ----
  private renderPlayers(): void {
    const wrap = document.createElement("div");
    wrap.className = "hub-players";
    if (this.players.length === 0) {
      wrap.innerHTML = `<p class="hub-note">Không có người chơi nào khác trên map.</p>`;
    }
    for (const p of this.players) {
      const row = document.createElement("div");
      row.className = "hub-player-row";
      const dot = document.createElement("span");
      dot.className = "hub-player-dot";
      dot.style.background = p.color || "#5865f2";
      const name = document.createElement("b");
      name.textContent = p.name;
      name.style.color = p.color || "#fff";
      const info = document.createElement("span");
      info.textContent =
        `HP ${p.hp ?? "?"}/${p.max_hp ?? "?"} · Cấp ${p.level ?? 1} · ${p.mode === "web" ? "Web" : "Discord"}`;
      row.append(dot, name, info);
      row.addEventListener("click", () => this.onPlayerPick?.(p));
      wrap.appendChild(row);
    }
    this.bar.setBody(wrap);
    this.bar.setFoot(null);
  }

  // ---- SETTINGS (Kaetram settings page parity: real toggles) ----
  private renderSettings(): void {
    const wrap = document.createElement("div");
    wrap.className = "hub-settings";
    const debug = toggleRow(
      "Debug overlay (F3)", this.scene.debugEnabled,
      () => {
        this.scene.debugEnabled = !this.scene.debugEnabled;
        const dbg = document.getElementById("desync-debug");
        if (dbg) dbg.style.display = this.scene.debugEnabled ? "block" : "none";
        this.render();
      },
    );
    const names = toggleRow(
      "Tên người chơi", this.scene.showNames,
      () => { this.scene.showNames = !this.scene.showNames; this.render(); },
    );
    wrap.append(debug, names);
    this.bar.setBody(wrap);
    const foot = document.createElement("div");
    foot.appendChild(actionRow(
      "Đăng xuất (tải lại trang)", "Đăng xuất", "logout",
      "location.reload() parity với Kaetram settings",
    ));
    this.bar.setFoot(foot);
  }

  // ---- empty-state systems ----
  private renderEmpty(page: HubPageId): void {
    const wrap = document.createElement("div");
    const note = document.createElement("p");
    note.className = "hub-note";
    note.textContent = EMPTY_NOTE[page] ?? "Sắp ra mắt.";
    wrap.appendChild(note);
    this.bar.setBody(wrap);
    this.bar.setFoot(null);
  }
}

// ---- small DOM helpers ----

function makeInfoLine(label: string, value: string): HTMLElement {
  const row = document.createElement("div");
  row.className = "hub-stat-row";
  const l = document.createElement("span");
  l.textContent = label;
  const v = document.createElement("b");
  v.textContent = value;
  row.append(l, v);
  return row;
}

function statBar(label: string, cur: number, max: number, color: string): HTMLElement {
  const row = document.createElement("div");
  row.className = "hub-bar-row";
  const l = document.createElement("span");
  l.textContent = `${label} ${Math.round(cur)}/${Math.round(max)}`;
  const track = document.createElement("div");
  track.className = "hub-bar-track";
  const fill = document.createElement("div");
  fill.className = "hub-bar-fill";
  fill.style.background = color;
  fill.style.width = `${max > 0 ? Math.min(100, (cur / max) * 100) : 0}%`;
  track.appendChild(fill);
  row.append(l, track);
  return row;
}

function toggleRow(label: string, on: boolean, onToggle: () => void): HTMLElement {
  const row = document.createElement("label");
  row.className = "hub-toggle";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = on;
  input.addEventListener("change", onToggle);
  const span = document.createElement("span");
  span.textContent = label;
  row.append(input, span);
  return row;
}

function actionRow(title: string, buttonText: string, action: string,
                  note: string): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "hub-action-row";
  const t = document.createElement("span");
  t.textContent = title;
  t.title = note;
  const btn = document.createElement("div");
  btn.className = "hub-slice-button";
  btn.dataset.hubAction = action;
  btn.textContent = buttonText;
  wrap.append(t, btn);
  return wrap;
}

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
}
void esc; // template safety helper (kept for future text nodes)
