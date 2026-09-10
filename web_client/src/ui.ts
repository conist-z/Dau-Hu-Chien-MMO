// HUD overlay: binds server state to the DOM elements declared in index.html.
// Layout spec (plan): clock+weather TOP-LEFT flush, HP/mana TOP-RIGHT flush,
// hotbar BOTTOM-CENTER always visible, chat BOTTOM-RIGHT.

import type { InventoryPayload, RecipePayload } from "./protocol";

const WEATHER_ICONS: Record<string, string> = {
  sun_clouds: "⛅", sun: "☀️", sunny: "☀️", clouds: "☁️", cloudy: "☁️",
  heavy_clouds: "☁️", rain: "🌧️", heavy_rain: "⛈️", storm: "⛈️",
  snow: "🌨️", cold: "🥶", wind: "🌬️", fog: "🌫️",
};
const WEATHER_NAMES: Record<string, string> = {
  sun_clouds: "Nắng có mây", sun: "Nắng", sunny: "Nắng", clouds: "Mây",
  cloudy: "Nhiều mây", heavy_clouds: "U ám", rain: "Mưa",
  heavy_rain: "Mưa to", storm: "Bão", snow: "Tuyết", cold: "Lạnh",
  wind: "Gió", fog: "Sương mù",
};

// Hotbar slot icons: bundled Twemoji codepoints are a SERVER-side concern;
// on web we render emoji glyphs directly (zero asset dependency).
const ITEM_EMOJI: Record<string, string> = {
  wood: "🪵", plank: "🟫", stone: "🪨", dirt: "🟤", stick: "🥢",
  grass: "🌿", sand: "🏖️", coin: "🪙", rotten_flesh: "🍖",
  crafting_table: "🛠️", furnace: "🔥",
  wood_axe: "🪓", wood_pickaxe: "⛏️", wood_sword: "🗡️", wood_shovel: "🥄",
  stone_axe: "🪓", stone_pickaxe: "⛏️", stone_sword: "🗡️", stone_shovel: "🥄",
  dirt_axe: "🪓", dirt_pickaxe: "⛏️", dirt_sword: "🗡️", dirt_shovel: "🥄",
};

function itemEmoji(id: string | null): string {
  if (!id) return "";
  return ITEM_EMOJI[id] ?? id[0]?.toUpperCase() ?? "?";
}

/** Icon through the server's registry first, static map as fallback. */
function iconFor(id: string | null, serverMap: Record<string, string>): string {
  if (!id) return "";
  return serverMap[id] ?? itemEmoji(id);
}

function fmtClock(secondsOfDay: number): string {
  const h = Math.floor(secondsOfDay / 3600) % 24;
  const m = Math.floor((secondsOfDay % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export class Hud {
  private clockEl = document.getElementById("hud-clock")!;
  private weatherEl = document.getElementById("hud-weather")!;
  private daynightEl = document.getElementById("hud-daynight")!;
  private hpFill = document.getElementById("bar-hp-fill")!;
  private hpLabel = document.getElementById("bar-hp-label")!;
  private manaFill = document.getElementById("bar-mana-fill")!;
  private manaLabel = document.getElementById("bar-mana-label")!;
  private hotbarEl = document.getElementById("hud-hotbar")!;
  private chatLog = document.getElementById("chat-log")!;
  private chatForm = document.getElementById("chat-form") as HTMLFormElement;
  private chatInput = document.getElementById("chat-input") as HTMLInputElement;
  private toastEl = document.getElementById("hud-toast")!;
  private gateEl = document.getElementById("login-gate")!;
  private statusEl = document.getElementById("login-status")!;
  private listEl = document.getElementById("scenario-list")!;
  private btnLogin = document.getElementById("btn-login") as HTMLButtonElement;
  private invPanel = document.getElementById("inv-panel")!;
  private invItems = document.getElementById("inv-items")!;
  private invCraft = document.getElementById("inv-craft")!;

  private inventory: InventoryPayload = { bag: [], hotbar: [] };
  // Server-driven emoji map (welcome.item_emojis): every item the player has
  // EVER received gets its proper icon; the static fallback below only
  // covers the bootstrap moment before welcome arrives.
  private itemEmojis: Record<string, string> = {};
  private recipes: RecipePayload[] = [];
  private activeSlot = 0;
  private onUse: ((itemId: string) => void) | null = null;
  private onCommand: ((text: string) => void) | null = null;
  private onCraft: ((recipeId: string) => void) | null = null;
  private onSelectSlot: ((slot: number) => void) | null = null;

  constructor() {
    this.chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const text = this.chatInput.value.trim();
      if (text && this.onCommand) this.onCommand(text);
      this.chatInput.value = "";
      this.chatInput.blur();
    });
    // Tab switching
    document.querySelectorAll<HTMLElement>(".inv-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll<HTMLElement>(".inv-tab").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        const isCraft = tab.dataset.tab === "craft";
        this.invItems.classList.toggle("hidden", isCraft);
        this.invCraft.classList.toggle("hidden", !isCraft);
      });
    });
    document.getElementById("inv-close")!.addEventListener("click", () => this.toggleInventory(false));
  }

  setHooks(
    onUse: (itemId: string) => void,
    onCommand: (text: string) => void,
    onCraft: (recipeId: string) => void,
  ): void {
    this.onUse = onUse;
    this.onCommand = onCommand;
    this.onCraft = onCraft;
    // Mouse wheel over the game area cycles the hotbar slot (both dirs).
    window.addEventListener("wheel", (e) => {
      if (this.inventoryOpen || this.gateVisible) return;
      const dir = e.deltaY > 0 ? 1 : -1;
      this.selectSlot(this.activeSlot + dir);
    }, { passive: true });
  }

  get gateVisible(): boolean {
    return !this.gateEl.classList.contains("hidden");
  }

  /** Select a hotbar slot (clamped, wraps); selection only — no auto-use. */
  selectSlot(index: number): void {
    const n = this.inventory.hotbar.length;
    if (n === 0) return;
    const next = ((index % n) + n) % n;
    if (next === this.activeSlot) return;
    this.activeSlot = next;
    this.renderHotbar();
    this.onSelectSlot?.(next);
  }

  // ----- inventory + craft panel -----

  toggleInventory(force?: boolean): void {
    const show = force ?? this.invPanel.classList.contains("hidden");
    this.invPanel.classList.toggle("hidden", !show);
    if (show) this.renderInventory();
  }

  get inventoryOpen(): boolean {
    return !this.invPanel.classList.contains("hidden");
  }

  private renderInventory(): void {
    // Items tab: one cell per bag stack.
    this.invItems.innerHTML = "";
    for (const stack of this.inventory.bag) {
      const cell = document.createElement("div");
      cell.className = "inv-item";
      cell.title = stack.id;
      cell.innerHTML = `<span>${iconFor(stack.id, this.itemEmojis)}</span><span class="qty">${stack.qty}</span>`;
      cell.addEventListener("click", () => this.onUse?.(stack.id));
      this.invItems.appendChild(cell);
    }
    if (this.inventory.bag.length === 0) {
      this.invItems.innerHTML = `<p style="grid-column:1/-1;font-size:12px;opacity:0.6">Túi trống.</p>`;
    }
    // Craft tab: recipes with material check.
    this.invCraft.innerHTML = "";
    for (const r of this.recipes) {
      const row = document.createElement("div");
      row.className = "craft-row";
      const canMake = r.inputs.every(
        (inp) => (this.inventory.bag.find((b) => b.id === inp.id)?.qty ?? 0) >= inp.qty,
      );
      const mats = r.inputs
        .map((i) => `${itemEmoji(i.id)}×${i.qty}`)
        .join(" + ");
      row.innerHTML =
        `<span style="font-size:18px">${r.emoji}</span>` +
        `<span class="info"><b>${r.name}</b><br>${mats}</span>`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Chế tạo";
      btn.disabled = !canMake;
      btn.addEventListener("click", () => this.onCraft?.(r.id));
      row.appendChild(btn);
      this.invCraft.appendChild(row);
    }
  }

  // ----- gate -----

  showGate(status: string): void {
    this.gateEl.classList.remove("hidden");
    this.statusEl.textContent = status;
  }

  hideGate(): void {
    this.gateEl.classList.add("hidden");
  }

  setLoginButton(enabled: boolean, label = "Đăng nhập bằng Discord"): void {
    this.btnLogin.disabled = !enabled;
    this.btnLogin.textContent = label;
  }

  onLoginClick(cb: () => void): void {
    this.btnLogin.addEventListener("click", cb);
  }

  showScenarioList(items: { channel_id: number; map_name: string; players: number }[],
                    onPick: (channelId: number) => void): void {
    this.listEl.innerHTML = "";
    if (items.length === 0) {
      const p = document.createElement("p");
      p.textContent = "Chưa có map nào. Dùng /startmap trên Discord trước.";
      this.listEl.appendChild(p);
      return;
    }
    for (const it of items) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "scenario-btn";
      b.textContent = `${it.map_name} — ${it.players} người chơi`;
      b.addEventListener("click", () => onPick(it.channel_id));
      this.listEl.appendChild(b);
    }
  }

  // ----- HUD state -----

  setClock(secondsOfDay: number): void {
    this.clockEl.textContent = fmtClock(secondsOfDay);
    // Day/night phase icon ("buổi") — mirrors the Discord hub renderer's
    // _daynight_phase periods (05-10 morning, 11-16 day, 17-18 evening,
    // otherwise night) driven by the SAME accelerated in-game clock.
    const hour = Math.floor(secondsOfDay / 3600) % 24;
    const phase =
      hour >= 5 && hour <= 10 ? "morning" :
      hour >= 11 && hour <= 16 ? "day" :
      hour >= 17 && hour <= 18 ? "evening" : "night";
    const icons: Record<string, [string, string]> = {
      morning: ["🌅", "Buổi sáng"],
      day: ["☀️", "Buổi trưa"],
      evening: ["🌇", "Buổi chiều"],
      night: ["🌙", "Ban đêm"],
    };
    const [icon, name] = icons[phase];
    this.daynightEl.textContent = icon;
    this.daynightEl.title = name;
  }

  setWeather(key: string): void {
    this.weatherEl.textContent = WEATHER_ICONS[key] ?? "❓";
    this.weatherEl.title = WEATHER_NAMES[key] ?? key;
  }

  setBars(hp: number, maxHp: number, mana: number, maxMana: number): void {
    this.hpFill.style.width = `${maxHp > 0 ? (hp / maxHp) * 100 : 0}%`;
    this.hpLabel.textContent = `${hp}/${maxHp}`;
    this.manaFill.style.width = `${maxMana > 0 ? (mana / maxMana) * 100 : 0}%`;
    this.manaLabel.textContent = `${mana}/${maxMana}`;
  }

  setInventory(inv: InventoryPayload): void {
    this.inventory = inv;
    this.renderHotbar();
    if (this.inventoryOpen) this.renderInventory();
  }

  setRecipes(recipes: RecipePayload[]): void {
    this.recipes = recipes;
  }

  /** Authoritative id->emoji map from the server's item registries. */
  setItemEmojis(map: Record<string, string>): void {
    this.itemEmojis = map ?? {};
    this.renderHotbar();
    if (this.inventoryOpen) this.renderInventory();
  }

  get recipeList(): RecipePayload[] {
    return this.recipes;
  }

  private renderHotbar(): void {
    this.hotbarEl.innerHTML = "";
    const slots = this.inventory.hotbar;
    slots.forEach((itemId, idx) => {
      const div = document.createElement("div");
      div.className = "slot" + (idx === this.activeSlot ? " active" : "");
      const qty = itemId
        ? (this.inventory.bag.find((b) => b.id === itemId)?.qty ?? 0)
        : 0;
      div.innerHTML = `<span class="key">${idx + 1}</span><span>${iconFor(itemId, this.itemEmojis)}</span>` +
        `<span class="qty">${qty > 0 ? qty : ""}</span>`;
      div.addEventListener("click", () => {
        this.selectSlot(idx);
      });
      this.hotbarEl.appendChild(div);
    });
  }

  // ----- chat + toasts -----

  chatLine(text: string): void {
    const div = document.createElement("div");
    div.className = "line";
    div.textContent = text;
    this.chatLog.appendChild(div);
    while (this.chatLog.children.length > 50) {
      this.chatLog.removeChild(this.chatLog.firstChild!);
    }
    this.chatLog.scrollTop = this.chatLog.scrollHeight;
  }

  toast(message: string): void {
    const div = document.createElement("div");
    div.className = "toast";
    div.textContent = message;
    this.toastEl.appendChild(div);
    window.setTimeout(() => div.remove(), 3500);
  }

  onSlotSelect(cb: (slot: number) => void): void {
    this.onSelectSlot = cb;
  }

  /** The item in the currently selected hotbar slot (for explicit place). */
  get heldItem(): string | null {
    return this.inventory.hotbar[this.activeSlot] ?? null;
  }

  get currentSlot(): number {
    return this.activeSlot;
  }
}
