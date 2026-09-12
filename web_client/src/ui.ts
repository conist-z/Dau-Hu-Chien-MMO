// HUD overlay: binds server state to the DOM elements declared in index.html.
// Layout spec (plan): clock+weather TOP-LEFT flush, HP/mana TOP-RIGHT flush,
// hotbar BOTTOM-CENTER always visible, chat BOTTOM-RIGHT.

import type { InventoryPayload, RecipePayload } from "./protocol";
import {
  CRAFT_GRID, CRAFT_OUTPUT, CRAFT_PANEL, INVENTORY_GRID, INVENTORY_PANEL,
  makeSlot, sizePanel, slotXY,
} from "./pixel_ui";

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
  private invItemsWrap = document.getElementById("inv-items-wrap")!;
  private invCraftWrap = document.getElementById("inv-craft-wrap")!;
  private craftDetail = document.getElementById("craft-detail")!;
  private craftTab: HTMLElement;
  private itemsTab: HTMLElement;
  private selectedRecipeId: string | null = null;
  private nearTable = false; // updated from snapshots (server truth)

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
    this.itemsTab = document.querySelector<HTMLElement>(".inv-tab[data-tab=items]")!;
    this.craftTab = document.querySelector<HTMLElement>(".inv-tab[data-tab=craft]")!;
    [this.itemsTab, this.craftTab].forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll<HTMLElement>(".inv-tab").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        const isCraft = tab.dataset.tab === "craft";
        this.invItemsWrap.classList.toggle("hidden", isCraft);
        this.invCraftWrap.classList.toggle("hidden", !isCraft);
        this.craftDetail.classList.toggle("hidden", !isCraft);
        this.renderInventory();
      });
    });
    document.getElementById("inv-close")!.addEventListener("click", () => this.toggleInventory(false));
    // Panel geometry once at boot (handoff: integer scale, absolute slots).
    sizePanel(this.invItemsWrap, INVENTORY_PANEL);
    sizePanel(this.invCraftWrap, CRAFT_PANEL);
  }


  /** Frame from the server after craft_op: success toast / error message. */
  craftResult(ok: boolean, reason: string, itemId: string | null, qty: number): void {
    if (ok) {
      const r = this.recipes.find((x) => x.output.id === itemId);
      this.toast(`Đã chế tạo ${r?.name ?? itemId} ×${qty}`);
    } else {
      const WHY: Record<string, string> = {
        missing_materials: "Không đủ nguyên liệu.",
        no_station: "Cần đứng gần bàn chế tạo.",
        unknown_recipe: "Công thức không tồn tại.",
      };
      this.toast(WHY[reason] ?? `Chế tạo thất bại (${reason}).`);
    }
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
    if (this.itemsTab.classList.contains("active")) {
      this.renderBagGrid();
    } else {
      this.renderCraftPanel();
    }
  }

  /** Túi đồ: fixed 5×4 pixel grid; bag order maps row-major into slots. */
  private renderBagGrid(): void {
    this.invItemsWrap.querySelectorAll(".slot-pix").forEach((n) => n.remove());
    const g = INVENTORY_GRID;
    const total = g.cols * g.rows;
    for (let i = 0; i < total; i++) {
      const [x, y] = slotXY(g, i);
      const stack = this.inventory.bag[i];
      const [r, c] = [Math.floor(i / g.cols), i % g.cols];
      const slot = makeSlot("inventory", r, c, x, y, g, {
        icon: stack ? iconFor(stack.id, this.itemEmojis) : "",
        qty: stack ? String(stack.qty) : "",
        title: stack ? stack.id : undefined,
      });
      if (stack) {
        slot.addEventListener("click", () => this.onUse?.(stack.id));
        slot.classList.add("has-item");
      }
      this.invItemsWrap.appendChild(slot);
    }
  }

  /** Chế tạo: 4×4 recipe grid (pick one) + output slot + detail strip. */
  private renderCraftPanel(): void {
    this.invCraftWrap.querySelectorAll(".slot-pix").forEach((n) => n.remove());
    const g = CRAFT_GRID;
    const total = g.cols * g.rows;
    // Keep the selection valid across recipe list changes.
    if (this.selectedRecipeId && !this.recipes.some((r) => r.id === this.selectedRecipeId)) {
      this.selectedRecipeId = null;
    }
    if (!this.selectedRecipeId && this.recipes.length > 0) {
      this.selectedRecipeId = this.recipes[0].id;
    }
    for (let i = 0; i < total; i++) {
      const [x, y] = slotXY(g, i);
      const rec = this.recipes[i];
      const [r, c] = [Math.floor(i / g.cols), i % g.cols];
      const sel = !!rec && rec.id === this.selectedRecipeId;
      const slot = makeSlot("recipe", r, c, x, y, g, {
        icon: rec ? rec.emoji : "",
        selected: sel,
        title: rec ? `${rec.name}${rec.needs_table ? " — cần bàn chế tạo" : ""}` : undefined,
      });
      if (rec) {
        slot.addEventListener("click", () => {
          this.selectedRecipeId = rec.id;
          this.renderCraftPanel();
        });
        slot.classList.add("has-item");
      }
      this.invCraftWrap.appendChild(slot);
    }
    // Output slot: FIXED (107,91) — never flex/grid (handoff §3.3).
    const sel = this.recipes.find((x) => x.id === this.selectedRecipeId) ?? null;
    const outSlot = makeSlot("output", 0, 0, CRAFT_OUTPUT.x, CRAFT_OUTPUT.y, CRAFT_GRID, {
      icon: sel ? sel.emoji : "",
      qty: sel && sel.output.qty > 1 ? String(sel.output.qty) : "",
      title: sel ? sel.name : undefined,
    });
    if (sel && this.canCraftNow(sel)) {
      outSlot.classList.add("craftable");
      outSlot.addEventListener("click", () => this.onCraft?.(sel.id));
    }
    this.invCraftWrap.appendChild(outSlot);
    this.renderCraftDetail(sel);
  }

  /** Pure client preview of can_craft — the SERVER re-checks at craft time. */
  private canCraftNow(r: RecipePayload): boolean {
    if (r.needs_table && !this.nearTable) return false;
    return r.inputs.every(
      (inp) => (this.inventory.bag.find((b) => b.id === inp.id)?.qty ?? 0) >= inp.qty,
    );
  }

  /** Detail strip under the craft panel: name, inputs ×qty (green/red), button. */
  private renderCraftDetail(sel: RecipePayload | null): void {
    const d = this.craftDetail;
    d.innerHTML = "";
    if (!sel) {
      d.classList.add("hidden");
      return;
    }
    d.classList.remove("hidden");
    const title = document.createElement("div");
    title.className = "craft-title";
    title.textContent = `${sel.emoji} ${sel.name}` +
      (sel.needs_table ? " 🛠️" : "");
    const mats = document.createElement("div");
    mats.className = "craft-mats";
    for (const inp of sel.inputs) {
      const have = this.inventory.bag.find((b) => b.id === inp.id)?.qty ?? 0;
      const chip = document.createElement("span");
      chip.className = `mat-chip ${have >= inp.qty ? "ok" : "lack"}`;
      chip.textContent = `${iconFor(inp.id, this.itemEmojis)}×${inp.qty}`;
      chip.title = `${inp.id}: có ${have}/${inp.qty}`;
      mats.appendChild(chip);
    }
    if (sel.needs_table && !this.nearTable) {
      const warn = document.createElement("span");
      warn.className = "mat-chip lack";
      warn.textContent = "🛠️ cần bàn";
      mats.appendChild(warn);
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "Chế tạo";
    btn.disabled = !this.canCraftNow(sel);
    btn.addEventListener("click", () => this.onCraft?.(sel.id));
    d.append(title, mats, btn);
  }

  // ----- loading overlay -----
  // Shown ONLY when the world genuinely needs to fetch blocking assets
  // (tilesets + block faces on a FRESH session). When every asset is already
  // cached (re-join within the same session) showLoading is never called —
  // the player drops straight into the world.
  private loadingTotal = 0;
  private loadingDone = 0;

  /** Begin a load pass for ``total`` blocking assets. No-op when total <= 0
   * (everything cached — the overlay never flashes on screen). */
  showLoading(total: number): void {
    if (total <= 0) return;
    this.loadingTotal = total;
    this.loadingDone = 0;
    const el = document.getElementById("loading-overlay")!;
    el.classList.remove("hidden");
    this.updateLoadingUI("0 / " + total);
  }

  /** One blocking asset arrived. Auto-hides at 100%. */
  tickLoading(): void {
    if (this.loadingTotal <= 0) return;
    this.loadingDone = Math.min(this.loadingDone + 1, this.loadingTotal);
    if (this.loadingDone >= this.loadingTotal) {
      this.hideLoading();
      return;
    }
    this.updateLoadingUI(this.loadingDone + " / " + this.loadingTotal);
  }

  hideLoading(): void {
    this.loadingTotal = 0;
    this.loadingDone = 0;
    document.getElementById("loading-overlay")!.classList.add("hidden");
  }

  private updateLoadingUI(status: string): void {
    const pct = this.loadingTotal > 0 ? (this.loadingDone / this.loadingTotal) * 100 : 0;
    const fill = document.getElementById("loading-fill")!;
    fill.style.width = pct.toFixed(0) + "%";
    document.getElementById("loading-status")!.textContent = status;
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

  /** Death veil + respawn countdown (dead while hp == 0). */
  setDead(dead: boolean, respawnS: number, reason?: string): void {
    const overlay = document.getElementById("death-overlay")!;
    overlay.classList.toggle("hidden", !dead);
    if (!dead) return;
    const r = document.getElementById("death-reason");
    if (r && reason) r.textContent = reason;
    const t = document.getElementById("death-timer");
    if (t) t.textContent = respawnS > 0 ? `Hồi sinh sau ${Math.ceil(respawnS)}s…` : "Đang hồi sinh…";
  }

  setInventory(inv: InventoryPayload): void {
    this.inventory = inv;
    this.renderHotbar();
    if (this.inventoryOpen) this.renderInventory();
  }

  setRecipes(recipes: RecipePayload[]): void {
    this.recipes = recipes;
  }

  setNearStation(near: boolean): void {
    if (near === this.nearTable) return;
    this.nearTable = near;
    if (this.inventoryOpen) this.renderInventory();
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

  /** Raw hotbar item ids (for the instant self-hand preview on slot switch). */
  get inventoryHotbar(): (string | null)[] {
    return this.inventory.hotbar ?? [];
  }

  get currentSlot(): number {
    return this.activeSlot;
  }
}
