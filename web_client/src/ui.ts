// HUD overlay: binds server state to the DOM elements declared in index.html.
// Layout spec (plan): clock+weather TOP-LEFT flush, HP/mana TOP-RIGHT flush,
// hotbar BOTTOM-CENTER always visible, chat BOTTOM-RIGHT.

import type { InventoryPayload, RecipePayload } from "./protocol";
import {
  CRAFT_BTN, CRAFT_BUTTON, CRAFT_DESC, CRAFT_INPUT_CELL,
  CRAFT_INPUT_GRID, CRAFT_LAYERS, CRAFT_OUTPUT_CELL, CRAFT_OUTPUT_GRID,
  CRAFT_PANEL, CRAFT_RESULT, CRAFT_RESULT_ATOM,
  CRAFT_TITLE, INV_ARROW_L, INV_ARROW_R, INV_COIN, INV_CRYSTAL,
  INV_SLOT, INV_TITLE, INVENTORY_GRID, INVENTORY_PANEL, PIXEL_SCALE,
  itemIconUrl, makeLayer, makeSlot, sizePanel, slotXY,
} from "./pixel_ui";

// Animated pixel weather icons copied from the Discord hub renderer
// (assets/gui/weather/<key>/frame_*.png -> public/ui/hud/weather/). One frame
// set per key; the HUD cycles frames at the same 400ms beat as the hub GIF.
const WEATHER_FRAMES: Record<string, number[]> = {
  sun_clouds: [1, 2, 3, 4, 5, 6],
  sunny: [1, 2, 3, 4, 5, 6],
  cloudy: [1, 2, 3, 4, 5, 6, 7],
  heavy_clouds: [1, 2, 3, 4, 5, 6, 7, 8],
  rain: [1, 2, 3, 4, 5, 6],
  heavy_rain: [1, 2, 3, 4, 5, 6],
  storm: [1, 2, 3, 4, 5, 6],
  snow: [1, 2, 3, 4, 5, 6, 7],
  cold: [1, 2, 3, 4],
  wind: [1, 2, 3, 4, 5, 6, 7],
};
// Legacy aliases + web-only keys fall back to the closest pixel set / emoji.
const WEATHER_ICON_FALLBACK: Record<string, string> = {
  sun: "☀️", sunny: "☀️", clouds: "☁️", fog: "🌫️",
};
const WEATHER_NAMES: Record<string, string> = {
  sun_clouds: "Nắng Dịu", sun: "Nắng", sunny: "Nắng Vàng", clouds: "Mây",
  cloudy: "Nhiều Mây", heavy_clouds: "Trời Âm U", rain: "Mây Thưa",
  heavy_rain: "Mưa Tầm Tã", storm: "Giông Bão", snow: "Tuyết Rơi",
  cold: "Giá Lạnh", wind: "Gió Nhẹ", fog: "Sương mù",
};
// Day/night phase pixel icons copied from the Discord hub renderer
// (assets/gui/daynight/*.png -> public/ui/hud/daynight/).
const DAYNIGHT_ICONS: Record<string, string> = {
  morning: "ui/hud/daynight/sun_morning.png",
  day: "ui/hud/daynight/sun_noon.png",
  evening: "ui/hud/daynight/sun_dusk.png",
  night: "ui/hud/daynight/moon_night.png",
};
const DAYNIGHT_EMOJI: Record<string, string> = {
  morning: "🌅", day: "☀️", evening: "🌇", night: "🌙",
};
const DAYNIGHT_NAMES: Record<string, string> = {
  morning: "Buổi sáng", day: "Buổi trưa", evening: "Buổi chiều", night: "Ban đêm",
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
  private craftDetail = document.getElementById("craft-detail")!; // kept: tab-switch hidden toggle;
  private craftTab: HTMLElement;
  private itemsTab: HTMLElement;
  private selectedRecipeId: string | null = null;
  private recipePage = 0; // OUTPUT grid pages 9 recipes at a time (arrows)
  private nearTable = false; // updated from snapshots (server truth)
  // Animated pixel icon state (weather + day/night HUD icons).
  private weatherImg: HTMLImageElement | null = null;
  private weatherKey: string | null = null;
  private weatherFrameIdx = 0;
  private weatherTimer: number | null = null;
  private daynightImg: HTMLImageElement | null = null;

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
        this.craftDetail.classList.toggle("hidden", true); // detail moved in-panel (V5)
        this.renderInventory();
      });
    });
    document.getElementById("inv-close")!.addEventListener("click", () => this.toggleInventory(false));
    // Panel geometry once at boot (V5: integer scale, exact local bboxes).
    sizePanel(this.invItemsWrap, INVENTORY_PANEL);
    sizePanel(this.invCraftWrap, CRAFT_PANEL);
    // Static kit layers (title/currency/decor) — placed once, exact bboxes.
    this.invItemsWrap.append(
      makeLayer(INV_TITLE), makeLayer(INV_COIN), makeLayer(INV_CRYSTAL),
    );
    this.invCraftWrap.append(
      makeLayer(CRAFT_TITLE),
      ...CRAFT_LAYERS.map((l) => makeLayer(l)),
    );
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

  /** Túi đồ: fixed 5×4 pixel grid (V5 origin [10,17] pitch 16, slot 14×14);
   *  bag order maps row-major into slots; coin/crystal overlays stay put. */
  private renderBagGrid(): void {
    this.invItemsWrap.querySelectorAll(".slot-pix").forEach((n) => n.remove());
    const g = INVENTORY_GRID;
    const total = g.cols * g.rows;
    for (let i = 0; i < total; i++) {
      const [x, y] = slotXY(g, i);
      const stack = this.inventory.bag[i];
      const slot = makeSlot(g.slotW, x, y, INV_SLOT, {
        iconUrl: stack ? itemIconUrl(stack.id) : undefined,
        emoji: stack ? iconFor(stack.id, this.itemEmojis) : "",
        qty: stack ? String(stack.qty) : "",
        title: stack ? stack.id : undefined,
      });
      if (stack) {
        slot.addEventListener("click", () => this.onUse?.(stack.id));
      }
      this.invItemsWrap.appendChild(slot);
    }
  }

  /** Chế tạo (V5 frame3_03): INPUT grid 3×5 = selected recipe's materials,
   *  OUTPUT grid 3×3 = recipe catalog page (arrows page), result slot
   *  [108,90,16,16] = craft trigger, in-panel button + description region. */
  private renderCraftPanel(): void {
    this.invCraftWrap.querySelectorAll(".slot-pix,.pix-btn,.pix-desc,.pix-pager").forEach((n) => n.remove());
    // Keep the selection valid across recipe list changes.
    if (this.selectedRecipeId && !this.recipes.some((r) => r.id === this.selectedRecipeId)) {
      this.selectedRecipeId = null;
    }
    if (!this.selectedRecipeId && this.recipes.length > 0) {
      this.selectedRecipeId = this.recipes[0].id;
    }
    const sel = this.recipes.find((x) => x.id === this.selectedRecipeId) ?? null;
    const pageCount = Math.max(1, Math.ceil(this.recipes.length / 9));
    this.recipePage = Math.min(this.recipePage, pageCount - 1);

    // --- INPUT grid 3×5: the selected recipe's materials, one stack per slot.
    const inputs = sel?.inputs ?? [];
    for (let i = 0; i < CRAFT_INPUT_GRID.cols * CRAFT_INPUT_GRID.rows; i++) {
      const [x, y] = slotXY(CRAFT_INPUT_GRID, i);
      const inp = inputs[i];
      const have = inp ? (this.inventory.bag.find((b) => b.id === inp.id)?.qty ?? 0) : 0;
      const enough = inp ? have >= inp.qty : true;
      const slot = makeSlot(CRAFT_INPUT_GRID.slotW, x, y, CRAFT_INPUT_CELL, {
        iconUrl: inp ? itemIconUrl(inp.id) : undefined,
        emoji: inp ? iconFor(inp.id, this.itemEmojis) : "",
        qty: inp ? String(inp.qty) : "",
        title: inp ? `${inp.id}: có ${have}/${inp.qty}` : undefined,
      });
      if (inp && !enough) slot.classList.add("lacking");
      this.invCraftWrap.appendChild(slot);
    }

    // --- OUTPUT grid 3×3: recipe catalog, 9 per page (output cell surface).
    const pageRecipes = this.recipes.slice(this.recipePage * 9, this.recipePage * 9 + 9);
    for (let i = 0; i < CRAFT_OUTPUT_GRID.cols * CRAFT_OUTPUT_GRID.rows; i++) {
      const [x, y] = slotXY(CRAFT_OUTPUT_GRID, i);
      const rec = pageRecipes[i];
      const slot = makeSlot(CRAFT_OUTPUT_GRID.slotW, x, y, CRAFT_OUTPUT_CELL, {
        iconUrl: rec ? itemIconUrl(rec.output.id) : undefined,
        emoji: rec ? rec.emoji : "",
        qty: rec && rec.output.qty > 1 ? String(rec.output.qty) : "",
        selected: !!rec && rec.id === this.selectedRecipeId,
        title: rec ? `${rec.name}${rec.needs_table ? " — cần bàn chế tạo" : ""}` : undefined,
      });
      if (rec) {
        slot.addEventListener("click", () => {
          this.selectedRecipeId = rec.id;
          this.renderCraftPanel();
        });
      }
      this.invCraftWrap.appendChild(slot);
    }
    // Catalog paging arrows (V5 Inventory arrow atoms, mirrored to craft's
    // free corners beside the output grid).
    if (this.recipes.length > 9) {
      const left = makeLayer({ ...INV_ARROW_L, x: 74, y: 65 });
      left.classList.add("pix-pager");
      const right = makeLayer({ ...INV_ARROW_R, x: 111, y: 65 });
      right.classList.add("pix-pager");
      if (this.recipePage > 0) {
        left.classList.add("clickable");
        left.style.cursor = "pointer";
        left.addEventListener("click", () => {
          this.recipePage--;
          this.renderCraftPanel();
        });
      }
      if (this.recipePage < pageCount - 1) {
        right.classList.add("clickable");
        right.style.cursor = "pointer";
        right.addEventListener("click", () => {
          this.recipePage++;
          this.renderCraftPanel();
        });
      }
      this.invCraftWrap.append(left, right);
    }

    // --- Result slot [108,90,16,16] (V5 PATCH_LOG): shows the output;
    // clicking it crafts (when craftable). Pulse overlay when craftable.
    const outSlot = makeSlot(CRAFT_RESULT.w, CRAFT_RESULT.x, CRAFT_RESULT.y, CRAFT_RESULT_ATOM, {
      iconUrl: sel ? itemIconUrl(sel.output.id) : undefined,
      emoji: sel ? sel.emoji : "",
      qty: sel && sel.output.qty > 1 ? String(sel.output.qty) : "",
      title: sel ? sel.name : undefined,
    });
    const craftable = !!sel && this.canCraftNow(sel);
    if (craftable && sel) {
      outSlot.classList.add("craftable");
      outSlot.addEventListener("click", () => this.onCraft?.(sel.id));
    }
    this.invCraftWrap.appendChild(outSlot);

    // --- In-panel pixel button [75,68,43,14] with state atoms.
    this.invCraftWrap.appendChild(this.makeCraftButton(craftable, sel));

    // --- Description region [133,21,54,87]: name + ingredient checklist.
    this.invCraftWrap.appendChild(this.makeCraftDescription(sel));
  }

  /** Pixel create button (demo sprite [76,69] 43×13): states via filters. */
  private makeCraftButton(craftable: boolean, sel: RecipePayload | null): HTMLElement {
    const btn = document.createElement("div");
    btn.className = "pix-btn" + (craftable ? " ok" : " off");
    btn.style.cssText =
      `left:${CRAFT_BUTTON.x * PIXEL_SCALE}px;top:${CRAFT_BUTTON.y * PIXEL_SCALE}px;` +
      `width:${CRAFT_BUTTON.w * PIXEL_SCALE}px;height:${CRAFT_BUTTON.h * PIXEL_SCALE}px;`;
    const bg = document.createElement("img");
    bg.className = "slot-bg";
    bg.src = CRAFT_BTN.normal;
    bg.draggable = false;
    btn.appendChild(bg);
    if (craftable && sel) {
      btn.addEventListener("mousedown", () => btn.classList.add("pressed"));
      btn.addEventListener("mouseup", () => btn.classList.remove("pressed"));
      btn.addEventListener("mouseleave", () => btn.classList.remove("pressed"));
      btn.addEventListener("click", () => this.onCraft?.(sel.id));
    }
    return btn;
  }

  /** Description region [133,21,54,87]: recipe name + per-input have/need. */
  private makeCraftDescription(sel: RecipePayload | null): HTMLElement {
    const d = document.createElement("div");
    d.className = "pix-desc";
    d.style.cssText =
      `left:${CRAFT_DESC.x * PIXEL_SCALE}px;top:${CRAFT_DESC.y * PIXEL_SCALE}px;` +
      `width:${CRAFT_DESC.w * PIXEL_SCALE}px;height:${CRAFT_DESC.h * PIXEL_SCALE}px;`;
    if (!sel) return d;
    const title = document.createElement("div");
    title.className = "pix-desc-title";
    title.textContent = sel.name + (sel.needs_table ? " 🛠️" : "");
    d.appendChild(title);
    for (const inp of sel.inputs) {
      const have = this.inventory.bag.find((b) => b.id === inp.id)?.qty ?? 0;
      const row = document.createElement("div");
      row.className = `pix-desc-row ${have >= inp.qty ? "ok" : "lack"}`;
      row.textContent = `${iconFor(inp.id, this.itemEmojis)}×${inp.qty} (${have})`;
      d.appendChild(row);
    }
    if (sel.needs_table && !this.nearTable) {
      const warn = document.createElement("div");
      warn.className = "pix-desc-row lack";
      warn.textContent = "🛠️ cần bàn chế tạo";
      d.appendChild(warn);
    }
    return d;
  }

  /** Pure client preview of can_craft — the SERVER re-checks at craft time. */
  private canCraftNow(r: RecipePayload): boolean {
    if (r.needs_table && !this.nearTable) return false;
    return r.inputs.every(
      (inp) => (this.inventory.bag.find((b) => b.id === inp.id)?.qty ?? 0) >= inp.qty,
    );
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
    // Day/night phase PIXEL icon ("buổi") — mirrors the Discord hub renderer's
    // _daynight_phase periods (05-10 morning, 11-16 day, 17-18 evening,
    // otherwise night) driven by the SAME accelerated in-game clock, using
    // the same 17x17 pixel-art tiles (assets/gui/daynight -> ui/hud/daynight).
    const hour = Math.floor(secondsOfDay / 3600) % 24;
    const phase =
      hour >= 5 && hour <= 10 ? "morning" :
      hour >= 11 && hour <= 16 ? "day" :
      hour >= 17 && hour <= 18 ? "evening" : "night";
    this.setDaynightIcon(phase);
  }

  /** Swap the day/night pixel icon when the phase changes (emoji fallback). */
  private setDaynightIcon(phase: string): void {
    if (this.daynightImg && this.daynightImg.dataset.phase === phase) return;
    const file = DAYNIGHT_ICONS[phase];
    if (file) {
      let img = this.daynightImg;
      if (!img) {
        img = document.createElement("img");
        img.className = "hud-pix";
        img.draggable = false;
        img.alt = "";
        this.daynightEl.replaceChildren(img);
      }
      img.src = file;
      img.dataset.phase = phase;
      img.title = DAYNIGHT_NAMES[phase] ?? phase;
      this.daynightImg = img;
    } else {
      this.daynightEl.textContent = DAYNIGHT_EMOJI[phase] ?? "❓";
      this.daynightEl.title = DAYNIGHT_NAMES[phase] ?? phase;
      this.daynightImg = null;
    }
  }

  setWeather(key: string): void {
    this.weatherEl.title = WEATHER_NAMES[key] ?? key;
    const frames = WEATHER_FRAMES[key];
    if (!frames) {
      // No pixel set (legacy alias / fog): emoji fallback, stop the cycle.
      this.stopWeatherCycle();
      this.weatherEl.textContent = WEATHER_ICON_FALLBACK[key] ?? "❓";
      this.weatherKey = null;
      return;
    }
    if (this.weatherKey === key && this.weatherImg) return; // already cycling
    this.stopWeatherCycle();
    this.weatherKey = key;
    const img = document.createElement("img");
    img.className = "hud-pix";
    img.draggable = false;
    img.alt = "";
    img.title = WEATHER_NAMES[key] ?? key;
    this.weatherEl.replaceChildren(img);
    this.weatherImg = img;
    // Cycle frames at the same 400ms beat as the Discord hub GIF
    // (HubRenderResult.duration_ms=400).
    this.weatherFrameIdx = 0;
    img.src = this.weatherFrameSrc(key, 0);
    this.weatherTimer = window.setInterval(() => {
      this.weatherFrameIdx = (this.weatherFrameIdx + 1) % frames.length;
      if (this.weatherImg) {
        this.weatherImg.src = this.weatherFrameSrc(key, this.weatherFrameIdx);
      }
    }, 400);
  }

  private weatherFrameSrc(key: string, idx: number): string {
    const n = WEATHER_FRAMES[key][idx];
    return `ui/hud/weather/${key}/frame_${String(n).padStart(3, "0")}.png`;
  }

  private stopWeatherCycle(): void {
    if (this.weatherTimer !== null) {
      window.clearInterval(this.weatherTimer);
      this.weatherTimer = null;
    }
    this.weatherImg = null;
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
