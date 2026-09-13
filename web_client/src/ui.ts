// HUD overlay: binds server state to the DOM elements declared in index.html.
// Layout spec (plan): clock+weather TOP-LEFT flush, HP/mana TOP-RIGHT flush,
// hotbar BOTTOM-CENTER always visible, chat BOTTOM-RIGHT.
//
// Craft model (user spec, Minecraft-style):
// - LIGHT 3×5 grid = QUICK-CRAFT catalog (recipes). Click a slot (or press
//   CREATE with one selected) and the server PULLS that recipe's materials
//   from the bag into the DARK grid. Not draggable.
// - DARK 3×3 grid = MATERIAL grid (what the player placed). Draggable:
//   move stacks between it and the inventory panel below, split (right-
//   click), merge (drop a stack onto the same item kind).
// - CREATE consumes the material grid (server matches the exact multiset);
//   the output lands in the RESULT slot next to the anvil; click to collect.
// - DESCRIPTION region shows the selected quick-craft recipe's info in the
//   panel's own pixel style.

import type { InventoryPayload, RecipePayload } from "./protocol";
import {
  CRAFT_BTN, CRAFT_BUTTON, CRAFT_DESC, CRAFT_LAYERS, CRAFT_MAT_CELL,
  CRAFT_MAT_GRID, CRAFT_PANEL, CRAFT_QUICK_CELL, CRAFT_QUICK_GRID,
  CRAFT_RESULT, CRAFT_RESULT_ATOM, CRAFT_TABS,
  CRAFT_TITLE, INV_COIN, INV_CRYSTAL,
  INV_SLOT, INV_TITLE, INVENTORY_GRID, INVENTORY_PANEL, PIXEL_SCALE,
  itemIconUrl, makeDigitRun, makeLayer, makeSlot, sizePanel, slotXY,
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
  iron_axe: "🪓", iron_pickaxe: "⛏️", iron_sword: "⚔️",
  gold_axe: "🪓", gold_pickaxe: "⛏️", gold_sword: "⚔️",
  steel_axe: "🪓", steel_pickaxe: "⛏️", steel_sword: "⚔️",
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

/** Hotbar HTML: Kaetram PNG icon when available (font-proof), emoji glyph
 * only as fallback. */
function iconHtml(id: string | null, serverMap: Record<string, string>): string {
  const url = itemIconUrl(id);
  if (url) return `<img class="icon-img" src="${url}" draggable="false">`;
  return escapeHtml(iconFor(id, serverMap));
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
}

function fmtClock(secondsOfDay: number): string {
  const h = Math.floor(secondsOfDay / 3600) % 24;
  const m = Math.floor((secondsOfDay % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** One stack living in a grid (bag slot or craft material slot). */
interface Stack { id: string; qty: number }

/** Drag payload: which grid a drag started from + the stack. */
interface DragSrc {
  from: "bag" | "mat" | "result";
  index: number;
  stack: Stack;
}

export class Hud {
  private clockEl = document.getElementById("hud-clock")!;
  private weatherEl = document.getElementById("hud-weather")!;
  private pingEl = document.getElementById("hud-ping")!;
  private daynightEl = document.getElementById("hud-daynight")!;
  private daynightImg: HTMLImageElement | null = null;
  private weatherImg: HTMLImageElement | null = null;
  private weatherKey: string | null = null;
  private weatherFrameIdx = 0;
  private weatherTimer: number | null = null;
  private hpFill = document.getElementById("bar-hp-fill")!;
  private hpLabel = document.getElementById("bar-hp-label")!;
  private manaFill = document.getElementById("bar-mana-fill")!;
  private manaLabel = document.getElementById("bar-mana-label")!;
  private staminaFill = document.getElementById("bar-stamina-fill")!;
  private staminaLabel = document.getElementById("bar-stamina-label")!;
  private hotbarEl = document.getElementById("hud-hotbar")!;
  private chatLog = document.getElementById("chat-log")!;
  private chatForm = document.getElementById("chat-form") as HTMLFormElement;
  private chatInput = document.getElementById("chat-input") as HTMLInputElement;
  private toastEl = document.getElementById("hud-toast")!;
  private gateEl = document.getElementById("login-gate")!;
  private statusEl = document.getElementById("login-status")!;
  private listEl = document.getElementById("scenario-list")!;
  private btnLogin = document.getElementById("btn-login") as HTMLButtonElement;
  private btnQuick = document.getElementById("btn-quick") as HTMLButtonElement;
  // Lobby (NEXT GAME 3-column layout, post-login pre-join).
  private lobbyEl = document.getElementById("lobby") as HTMLDivElement;
  private lobbyAvatar = document.getElementById("lobby-avatar") as HTMLSpanElement;
  private lobbyName = document.getElementById("lobby-name")!;
  private lobbySub = document.getElementById("lobby-sub")!;
  private nxBanner = document.getElementById("nx-banner") as HTMLDivElement;
  private lobbyStatus = document.getElementById("lobby-status")!;
  private gatePanel = document.querySelector(".gate-panel") as HTMLDivElement;
  private invPanel = document.getElementById("inv-panel")!;
  private invItemsWrap = document.getElementById("inv-items-wrap")!;
  private invCraftWrap = document.getElementById("inv-craft-wrap")!;
  private invItemsCraftWrap = document.getElementById("inv-items-craft")!;
  private craftDetail = document.getElementById("craft-detail")!;
  private craftTab: HTMLElement;
  private itemsTab: HTMLElement;
  private selectedQuick: number | null = null; // quick-craft catalog index
  private nearTable = false; // updated from snapshots (server truth)

  // ---- Quick-craft catalog SCROLL (mouse wheel over the LIGHT grid) ----
  // The 3×5 grid shows a WINDOW into the recipe list; `craftScroll` is the
  // index of the first visible recipe OF THE ACTIVE FILTER. Scroll bounds
  // are derived from the grid geometry itself (rows) — never hard-coded.
  private craftScroll = 0;

  // ---- Quick-craft category tabs (top-right icon trio) ----
  // "all" = every recipe (no icon lit — the kit has no lit-"all" art, this
  // IS the deselected mode). Clicking a tab filters; clicking the LIT tab
  // again returns to "all" (double-click any icon = same thing: first click
  // selects, second click on the same icon deselects).
  private craftCategory: "all" | "tool" | "decor" | "usable" = "all";
  private lastTabClick: { group: string; at: number } | null = null;

  private inventory: InventoryPayload = { bag: [], hotbar: [] };
  // Server-driven emoji map (welcome.item_emojis): every item the player has
  // EVER received gets its proper icon; the static fallback below only
  // covers the bootstrap moment before welcome arrives.
  private itemEmojis: Record<string, string> = {};
  private recipes: RecipePayload[] = [];
  // MATERIAL grid state: SERVER truth (synced via craft_op mat_sync). The
  // client keeps a local mirror for instant painting; the server owns the
  // bag<->grid delta.
  private matGrid: (Stack | null)[] = Array(9).fill(null);
  private parkedResult: { id: string; qty: number } | null = null;
  private drag: DragSrc | null = null;
  /** Set by main.ts: throws a stack into the world (drop entity). */
  onThrow: ((itemId: string, qty: number) => void) | null = null;
  /** Set by main.ts: purse drag-out — pull ONE coin/crystal into the bag. */
  onPurseWithdraw: ((itemId: string) => void) | null = null;
  /** Purse balances (server truth, 20 Hz): coins + crystals. */
  private purseCoins = 0;
  private purseCrystals = 0;
  private purseDigitEls: HTMLImageElement[] = [];
  private purseLastSig = "";
  /** Active purse drag: currency item id, or null. */
  private purseDrag: string | null = null;
  private purseGhost: HTMLElement | null = null;
  /** True when (x, y) is inside ANY open inventory/craft panel. */
  private pointInPanels(x: number, y: number): boolean {
    const inRect = (el: HTMLElement | null) => {
      if (!el || el.classList.contains("hidden")) return false;
      const r = el.getBoundingClientRect();
      return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
    };
    return (
      inRect(this.invItemsWrap) ||
      inRect(this.invCraftWrap) ||
      inRect(this.invItemsCraftWrap) ||
      inRect(this.invPanel)
    );
  }
  private dragGhost: HTMLDivElement | null = null;
  private activeSlot = 0;
  private onCommand: ((text: string) => void) | null = null;
  private onCraftGrid: ((inputs: { id: string; qty: number }[]) => void) | null = null;
  private onSplit: ((slot: number) => void) | null = null;
  private onCollect: ((slot: number | null) => void) | null = null;
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
        // Tab click while fully closed reopens BOTH panels (reset state).
        if (this.invPanel.classList.contains("hidden")) {
          this.invClosed = false;
          this.craftClosed = false;
          this.lastBagSig = "";
          this.toggleInventory(true);
        }
        document.querySelectorAll<HTMLElement>(".inv-tab").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        // Re-apply the per-panel layout; applyTabLayout also plays the
        // tab-swap entrance animation on whichever wrap just appeared.
        this.applyTabLayout();
        this.craftDetail.classList.toggle("hidden", true);
        this.renderInventory();
      });
    });
    // Legacy strip X (kept for DOM parity) closes the whole window.
    document.getElementById("inv-close")!.addEventListener("click", () => this.toggleInventory(false));
    // Per-panel pixel X buttons (cover the X baked into each frame art).
    document.querySelectorAll<HTMLButtonElement>(".panel-close").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        this.closePanel(btn.dataset.panel as "inv" | "craft");
      });
    });
    // Panel geometry once at boot (V5: integer scale, exact local bboxes).
    sizePanel(this.invItemsWrap, INVENTORY_PANEL);
    sizePanel(this.invCraftWrap, CRAFT_PANEL);
    sizePanel(this.invItemsCraftWrap, INVENTORY_PANEL);
    this.attachCraftScrollHandler();
    // Static kit layers — placed once, exact bboxes.
    this.invItemsWrap.append(makeLayer(INV_TITLE), makeLayer(INV_COIN), makeLayer(INV_CRYSTAL));
    this.invItemsCraftWrap.append(makeLayer(INV_TITLE), makeLayer(INV_COIN), makeLayer(INV_CRYSTAL));
    this.invCraftWrap.append(makeLayer(CRAFT_TITLE), ...CRAFT_LAYERS.map((l) => makeLayer(l)));
    // PURSE: the coin/crystal icons are drag sources — mousedown starts a
    // purse drag (ghost of the icon), release OUTSIDE the panel withdraws
    // exactly ONE unit into the bag (server op purse_withdraw).
    for (const [wrap, itemId] of [
      [this.invItemsWrap, "coin"], [this.invItemsWrap, "crystal"],
      [this.invItemsCraftWrap, "coin"], [this.invItemsCraftWrap, "crystal"],
    ] as [HTMLElement, string][]) {
      const iconSpec = itemId === "coin" ? INV_COIN : INV_CRYSTAL;
      const hit = document.createElement("div");
      hit.className = "purse-hit";
      hit.style.cssText =
        `left:${iconSpec.x * PIXEL_SCALE}px;top:${iconSpec.y * PIXEL_SCALE}px;` +
        `width:${iconSpec.w * PIXEL_SCALE}px;height:${iconSpec.h * PIXEL_SCALE}px;`;
      hit.dataset.itemId = itemId;
      hit.title = "Kéo ra ngoài panel để rút 1";
      hit.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        this.startPurseDrag((e.currentTarget as HTMLElement).dataset.itemId ?? "coin", e);
      });
      wrap.appendChild(hit);
    }
    // Global drag ghost tracking (mouse-move + drop outside any slot).
    window.addEventListener("mousemove", (e) => this.updateDragGhost(e.clientX, e.clientY));
    window.addEventListener("mouseup", (e) => {
      // MAGNETIC DROP: resolve to the NEAREST droppable slot within a
      // PURSE drag-out: release OUTSIDE every panel = withdraw exactly
      // ONE coin/crystal into the bag (server op purse_withdraw). Release
      // inside = cancel (nothing happens — the count stays put).
      if (this.purseDrag) {
        this.endPurseDrag(!this.pointInPanels(e.clientX, e.clientY));
        return;
      }
      // generous snap radius instead of only the exact hovered slot —
      // near-misses snap in instead of springing back.
      if (!this.drag) return;
      const t = this.nearestDropTarget(e.clientX, e.clientY);
      if (t) {
        if (t.from === "result") {
          // Dropping a bag/mat stack BACK onto the result slot = cancel:
          // the stack returns where it came from (endDrag repaints).
          this.cancelDrag();
          return;
        }
        this.dropOn(t.from as "bag" | "mat", t.index);
        return;
      }
      // DRAG OUT + RELEASE outside every panel = throw the stack into the
      // world (server spawns a drop entity). No extra click needed.
      if (
        e.button === 0 &&
        !this.pointInPanels(e.clientX, e.clientY) &&
        this.onThrow
      ) {
        const stack = this.drag.stack;
        this.onThrow(stack.id, stack.qty);
        this.endDrag();
        return;
      }
      this.cancelDrag();
    });
  }

  /** The nearest droppable slot (same-panel partner grids) within radius. */
  private nearestDropTarget(x: number, y: number): { from: "bag" | "mat" | "result"; index: number } | null {
    const RADIUS = 26; // px — generous snap (slot is 42px at scale 3)
    const hits: { from: "bag" | "mat" | "result"; index: number; d: number }[] = [];
    const scan = (wrap: HTMLElement, from: "bag" | "mat") => {
      wrap.querySelectorAll<HTMLElement>(".slot-pix").forEach((el) => {
        const r = el.getBoundingClientRect();
        const dx = x - (r.left + r.width / 2);
        const dy = y - (r.top + r.height / 2);
        const d = Math.hypot(dx, dy);
        const idx = el.dataset.slot;
        if (d <= RADIUS && idx !== undefined) {
          hits.push({ from, index: Number(idx), d });
        }
      });
    };
    // Result slot has no data-slot; find it by its .result class.
    const out = this.invCraftWrap.querySelector<HTMLElement>(".slot-pix.result");
    if (out) {
      const r = out.getBoundingClientRect();
      const d = Math.hypot(x - (r.left + r.width / 2), y - (r.top + r.height / 2));
      if (d <= RADIUS) hits.push({ from: "result", index: 0, d });
    }
    const craftActive = this.craftTab.classList.contains("active");
    if (craftActive) {
      scan(this.invCraftWrap, "mat");
      scan(this.invItemsCraftWrap, "bag");
    } else {
      scan(this.invItemsWrap, "bag");
    }
    if (hits.length === 0) return null;
    hits.sort((a, b) => a.d - b.d);
    return { from: hits[0]!.from, index: hits[0]!.index };
  }


  /** Frame from the server after craft_op: success toast / error message.
   *  A FAILED craft restores the material grid (optimistic clear reverted). */
  craftResult(ok: boolean, reason: string, itemId: string | null, qty: number): void {
    if (ok) {
      this.pendingCraftSnapshot = null;
      const r = this.recipes.find((x) => x.output.id === itemId);
      this.toast(`Đã chế tạo ${r?.name ?? itemId} ×${qty}`);
      // NO auto-collect: the output STAYS in the result slot — the player
      // sees it there and drags/clicks it back into the bag themselves.
    } else {
      if (this.pendingCraftSnapshot) {
        this.matGrid = this.pendingCraftSnapshot;
        this.pendingCraftSnapshot = null;
        this.renderInventory();
      }
      const WHY: Record<string, string> = {
        missing_materials: "Không đủ nguyên liệu.",
        no_station: "Cần đứng gần bàn chế tạo.",
        unknown_recipe: "Công thức không tồn tại.",
        no_matching_recipe: "Chưa đúng công thức — xem Description.",
        empty_grid: "Đặt nguyên liệu vào ô tối màu trước.",
        result_slot_occupied:
          "Hãy lấy vật phẩm ra khỏi ô nhận vật phẩm trước!",
      };
      this.toast(WHY[reason] ?? `Chế tạo thất bại (${reason}).`);
    }
  }

  setHooks(
    _onUse: (itemId: string) => void,
    onCommand: (text: string) => void,
    _onCraft: (recipeId: string) => void,
  ): void {
    this.onCommand = onCommand;
    // Mouse wheel over the game area cycles the hotbar slot (both dirs).
    window.addEventListener("wheel", (e) => {
      if (this.inventoryOpen || this.gateVisible) return;
      const dir = e.deltaY > 0 ? 1 : -1;
      this.selectSlot(this.activeSlot + dir);
    }, { passive: true });
  }

  /** Extra craft hooks: grid craft + split (all optional). The material
   *  grid is a LOCAL buffer — no per-move network op exists anymore. */
  setCraftHooks(
    onCraftGrid: (inputs: { id: string; qty: number }[]) => void,
    _onQuickFill: (grid: { id: string; qty: number }[]) => void,
    onSplit: (slot: number) => void,
  ): void {
    this.onCraftGrid = onCraftGrid;
    this.onSplit = onSplit;
  }

  /** Server-synced parked craft RESULT (the result slot is server truth).
   *  The material grid itself is a local buffer and is never overwritten
   *  by server echoes. */
  setCraftResult(result: { id: string; qty: number } | null): void {
    this.parkedResult = result;
    if (this.inventoryOpen && this.craftTab.classList.contains("active")) {
      this.renderCraftPanel();
    }
  }

  /** Register the result-slot collect callback (target slot optional:
   *  set when the output was DRAGGED onto a specific bag slot). */
  onCollectResult(cb: (slot: number | null) => void): void {
    this.onCollect = cb;
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

  // Per-panel visibility (independent of the wrapping #inv-panel): the X on
  // the inventory closes the bag while the craft tab stays; the X on the
  // craft panel closes craft only. Reopening (B key / tab click) resets both.
  private invClosed = false;
  private craftClosed = false;
  private hideTimer: number | null = null;

  /** Play the pop-out animation then flip .hidden (140 ms exit). */
  private animateHide(el: HTMLElement): void {
    el.classList.remove("anim-show", "slide-up");
    el.classList.add("anim-hide");
    if (this.hideTimer !== null) window.clearTimeout(this.hideTimer);
    this.hideTimer = window.setTimeout(() => {
      el.classList.add("hidden");
      el.classList.remove("anim-hide");
      this.hideTimer = null;
    }, 140);
  }

  /** Play the pop-in (or slide-up) animation on a freshly shown element. */
  private animateShow(el: HTMLElement, slide = false): void {
    if (this.hideTimer !== null) {
      // Cancel a pending hide of the same element and show it immediately.
      window.clearTimeout(this.hideTimer);
      this.hideTimer = null;
      el.classList.remove("anim-hide");
    }
    el.classList.remove("hidden", "anim-hide", "slide-up");
    if (slide) {
      // Force a reflow so the slide-up keyframes replay from the offset.
      void el.offsetWidth;
      el.classList.add("slide-up");
    } else {
      el.classList.add("anim-show");
    }
  }

  toggleInventory(force?: boolean): void {
    const show = force ?? this.invPanel.classList.contains("hidden");
    if (show) {
      // Opening resets the per-panel close state (fresh pair of panels).
      this.invClosed = false;
      this.craftClosed = false;
      this.lastBagSig = ""; // opening must ALWAYS repaint (stale-grid guard)
      this.applyTabLayout(false);
      this.animateShow(this.invPanel);
      this.renderInventory();
    } else {
      this.animateHide(this.invPanel);
    }
  }

  get inventoryOpen(): boolean {
    return !this.invPanel.classList.contains("hidden");
  }

  /** Re-apply the CURRENT tab layout to the three panel wraps, honoring
   *  the per-panel close flags, and sync the tab strip + drag-partner
   *  visibility. Called on every tab click / X press. */
  private applyTabLayout(slideInv = false): void {
    const craftActive = this.craftTab.classList.contains("active");
    // Tab strip: hide the tabs entirely only when BOTH panels are closed.
    const tabsEl = this.craftTab.parentElement;
    if (tabsEl) {
      tabsEl.classList.toggle("hidden", this.invClosed && this.craftClosed);
    }
    if (craftActive) {
      const showCraft = this.visiblyShow(this.invCraftWrap, this.craftClosed);
      const showBag = this.visiblyShow(this.invItemsCraftWrap, this.invClosed);
      this.invItemsWrap.classList.add("hidden");
      // Tab-swap entrance animation on whichever wrap(s) just appeared.
      if (showCraft) this.playTabIn(this.invCraftWrap);
      if (showBag) this.playTabIn(this.invItemsCraftWrap);
    } else {
      const showBag = this.visiblyShow(this.invItemsWrap, this.invClosed);
      this.invCraftWrap.classList.add("hidden");
      this.invItemsCraftWrap.classList.add("hidden");
      if (showBag) this.playTabIn(this.invItemsWrap);
    }
    if (slideInv && !this.invClosed) {
      // Craft closed while the inventory panel is visible: glide the bag
      // up into the freed space (the tab strip "chế đồ" -> stays).
      this.animateShow(this.invItemsCraftWrap, true);
    }
  }

  /** Show/hide a wrap; returns true when its visibility CHANGED to shown. */
  private visiblyShow(el: HTMLElement, closed: boolean): boolean {
    const wasHidden = el.classList.contains("hidden");
    el.classList.toggle("hidden", closed);
    return wasHidden && !closed;
  }

  /** Replay the tab-swap entrance animation (retrigger-safe). */
  private playTabIn(el: HTMLElement): void {
    el.classList.remove("tab-in");
    void el.offsetWidth; // restart the keyframes
    el.classList.add("tab-in");
  }

  /** A panel X was pressed: close THAT panel only (user rule), keep the
   *  other one, and sync the tab strip highlight. */
  private closePanel(which: "inv" | "craft"): void {
    if (which === "inv") {
      this.invClosed = true;
      // On the items tab the inv panel is the only one: whole window goes.
      if (!this.craftTab.classList.contains("active")) {
        this.animateHide(this.invPanel);
        return;
      }
      this.applyTabLayout();
      this.renderInventory();
    } else {
      this.craftClosed = true;
      // Craft X on the craft tab: craft hides, the bag slides up. The tab
      // strip stays (the bag is still open) — tabs remain clickable.
      this.applyTabLayout(true);
      // Tab strip: craft is closed; highlight nothing (or items if the bag
      // is what remains visible).
      if (this.invClosed) {
        // Both closed via craft X too: hide the whole window.
        this.animateHide(this.invPanel);
        return;
      }
    }
  }

  /** Reopen both panels from a fully-closed state (tab strip was hidden —
   *  B key path): reset the close flags and re-show everything. */
  reopenAfterFullClose(): void {
    this.invClosed = false;
    this.craftClosed = false;
  }

  private lastBagSig = "";

  private renderInventory(): void {
    // Repaint guard: identical bag + same tab + same craft context + same
    // purse = skip. (Purse in the sig: counters repaint on balance change.)
    const bagSig = JSON.stringify(this.inventory.bag);
    const craftActive = this.craftTab.classList.contains("active");
    const purseSig = `${this.purseCoins}:${this.purseCrystals}`;
    const sig = bagSig + "|" + (craftActive ? "craft" : "items") +
      "|" + (this.nearTable ? 1 : 0) +
      "|" + (this.parkedResult ? this.parkedResult.id + this.parkedResult.qty : "-") +
      "|" + purseSig;
    if (sig === this.lastBagSig && this.drag === null) return;
    this.lastBagSig = sig;
    this.renderPurse(craftActive);
    if (craftActive) {
      this.renderBagGrid(this.invItemsCraftWrap); // craft tab: drag partner
      this.renderCraftPanel();
    } else {
      this.renderBagGrid(this.invItemsWrap);
    }
  }

  // ===== DRAG & DROP core =====

  private startDrag(src: DragSrc, e: MouseEvent): void {
    if (this.drag) return;
    this.drag = src;
    const ghost = document.createElement("div");
    ghost.className = "drag-ghost";
    const url = itemIconUrl(src.stack.id);
    if (url) {
      const im = document.createElement("img");
      im.src = url;
      im.draggable = false;
      ghost.appendChild(im);
    } else {
      ghost.textContent = iconFor(src.stack.id, this.itemEmojis);
    }
    document.body.appendChild(ghost);
    this.dragGhost = ghost;
    this.updateDragGhost(e.clientX, e.clientY);
    // Cute wobble: gentle pendulum swing + bob (transform-only, GPU cheap,
    // pauses never — a 2.4s loop at ±6° reads as "the item is alive" without
    // stealing attention from the game).
    ghost.animate(
      [
        { transform: "rotate(-6deg) translateY(0px)" },
        { transform: "rotate(5deg) translateY(-2px)" },
        { transform: "rotate(-4deg) translateY(0px)" },
        { transform: "rotate(6deg) translateY(-1px)" },
        { transform: "rotate(-6deg) translateY(0px)" },
      ],
      { duration: 2400, iterations: Infinity, easing: "ease-in-out" },
    );
  }

  private updateDragGhost(x: number, y: number): void {
    if (this.purseGhost) {
      this.purseGhost.style.left = `${x - 16}px`;
      this.purseGhost.style.top = `${y - 16}px`;
      return;
    }
    if (!this.dragGhost) return;
    this.dragGhost.style.left = `${x - 16}px`;
    this.dragGhost.style.top = `${y - 16}px`;
  }

  private dropOn(target: "bag" | "mat", index: number): void {
    const d = this.drag;
    this.endDrag();
    if (!d || (d.from === target && d.index === index)) return;
    if (d.from === "bag" && target === "bag") {
      this.moveBag(d.index, index);
    } else if (d.from === "mat" && target === "mat") {
      this.moveMat(d.index, index);
    } else if (d.from === "bag" && target === "mat") {
      this.bagToMat(d.index, index);
    } else if (d.from === "mat" && target === "bag") {
      this.matToBag(d.index, index);
    } else if (d.from === "result" && target === "bag") {
      this.resultToBag(index);
    }
    // result → mat stays deliberately unsupported: the output is server
    // truth and belongs in the bag (or back on the result slot).
  }

  private endDrag(): void {
    this.drag = null;
    this.dragGhost?.remove();
    this.dragGhost = null;
  }

  private cancelDrag(): void {
    // Stack stays where it was — just repaint.
    this.endDrag();
    this.renderInventory();
  }

  /** Bag → bag drag: pure grid reorder (swap or merge). The whole bag
   *  order syncs to the server via ONE reorder op — any slot to any slot
   *  of the 5×4 grid, no hotbar clamp, no bad_slot possible. */
  private moveBag(from: number, to: number): void {
    const a = this.inventory.bag[from];
    const b = this.inventory.bag[to];
    if (!a) return;
    if (b && b.id === a.id) {
      // Same item: merge (drag one stack onto the same kind).
      this.inventory.bag[from] = null;
      b.qty += a.qty;
    } else {
      this.inventory.bag[from] = b ?? null;
      this.inventory.bag[to] = a;
    }
    this.renderHotbar(); // INSTANT local hotbar echo (no server wait)
    this.syncBagOrder();
    this.renderInventory();
  }

  /** Send the whole bag order to the server (validated multiset there).
   *  DEBOUNCED 400ms: fast consecutive drags coalesce into ONE reorder
   *  frame — the UI stays instant (local edit) and the network never sees
   *  the intermediate states (no jitter, no ping spikes, no bad_order). */
  private syncBagOrder(): void {
    if (this.reorderTimer !== null) {
      window.clearTimeout(this.reorderTimer);
    }
    this.reorderTimer = window.setTimeout(() => {
      this.reorderTimer = null;
      const order = this.inventory.bag.map((s) =>
        s ? { id: s.id, qty: s.qty } : { id: "", qty: 0 });
      this.onReorder?.(order);
    }, 400);
  }

  private reorderTimer: number | null = null;

  // ===== CRAFT: the 9-cell material grid is a PURE LOCAL BUFFER =====
  // The player "takes" stacks out of the bag view onto the grid; nothing
  // touches the network until CREATE (or a right-click put-back). The server
  // re-validates everything at craft time, so lag can never corrupt moves.

  /** Material grid internal move/merge — local only, zero network. */
  private moveMat(from: number, to: number): void {
    const a = this.matGrid[from];
    if (!a) return;
    const b = this.matGrid[to];
    if (b && b.id === a.id) {
      b.qty += a.qty;
      this.matGrid[from] = null;
    } else {
      this.matGrid[from] = b ?? null;
      this.matGrid[to] = a;
    }
    this.renderCraftPanel();
  }

  /** Bag → material grid: TAKE the whole stack off the bag view (local).
   *  The stack disappears from the bag panel and appears on the grid; the
   *  server never hears about it until CREATE (or put-back). */
  private bagToMat(bagIndex: number, matIndex: number): void {
    const src = this.inventory.bag[bagIndex];
    if (!src) return;
    const dst = this.matGrid[matIndex];
    if (dst && dst.id !== src.id) {
      // Different item: swap bag stack with the placed stack (local).
      this.matGrid[matIndex] = { ...src };
      this.inventory.bag[bagIndex] = { ...dst };
    } else if (dst) {
      dst.qty += src.qty;
      this.inventory.bag[bagIndex] = null;
    } else {
      this.matGrid[matIndex] = { ...src };
      this.inventory.bag[bagIndex] = null;
    }
    this.renderInventory();
    this.renderCraftPanel();
  }

  /** Material grid → bag: put the stack back into the bag view (local). */
  private matToBag(matIndex: number, bagIndex: number): void {
    const src = this.matGrid[matIndex];
    if (!src) return;
    const target = this.inventory.bag[bagIndex];
    if (target && target.id !== src.id) {
      this.matGrid[matIndex] = { ...target };
      this.inventory.bag[bagIndex] = { ...src };
    } else if (target) {
      target.qty += src.qty;
      this.matGrid[matIndex] = null;
    } else {
      this.inventory.bag[bagIndex] = { ...src };
      this.matGrid[matIndex] = null;
    }
    this.renderInventory();
    this.renderCraftPanel();
  }

  /** Compact the 9 material cells into the CREATE input list. */
  private compactMatGrid(): { id: string; qty: number }[] {
    return this.matGrid
      .filter((s): s is Stack => !!s && s.qty > 0)
      .map((s) => ({ id: s.id, qty: s.qty }));
  }

  /** Drop the parked result onto a bag slot: ONE collect frame tells the
   *  server WHERE to put it (merge onto the same kind, bad_slot otherwise).
   *  OPTIMISTIC: the result clears and lands in the bag AT ONCE — the
   *  server's inventory delta (same round-trip) reconciles via the
   *  delta-merge in setInventory, so no visible jank either way. */
  private resultToBag(slot: number | null): void {
    if (!this.parkedResult) return;
    const res = { ...this.parkedResult };
    this.parkedResult = null;
    if (this.craftTab.classList.contains("active")) this.renderCraftPanel();
    // Local landing: merge onto a same-kind stack, else the first free cell.
    const same = this.inventory.bag.find((b) => b && b.id === res.id);
    if (same) {
      same.qty += res.qty;
    } else {
      const free = this.inventory.bag.findIndex((b) => !b);
      if (free >= 0) this.inventory.bag[free] = res;
    }
    this.invVersion = -1; // force the next server delta to reconcile
    this.renderHotbar();
    if (this.inventoryOpen) this.renderInventory();
    this.onCollect?.(slot);
  }

  // Wire with bindMoveTo() — kept optional so a half-wired build never
  // blocks compilation with TS6133 (session WIP guard).
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  private onReorder: ((order: { id: string; qty: number }[]) => void) | null = null;

  /** Right-click a BAG slot: split half into the next empty bag slot.
   *  Client mirror first (instant), server op persists the new grid. */
  private splitBag(index: number): void {
    const st = this.inventory.bag[index];
    if (!st || st.qty < 2) return;
    const half = st.qty - Math.floor(st.qty / 2);
    st.qty -= half;
    const bag = this.inventory.bag;
    let at = -1;
    for (let i = 1; i <= bag.length; i++) {
      const j = (index + i) % bag.length;
      if (!bag[j]) { at = j; break; }
    }
    if (at >= 0) bag[at] = { id: st.id, qty: half };
    else { st.qty += half; return; } // no room: undo locally
    this.onSplit?.(index);
    this.syncBagOrder();
    this.renderInventory();
  }

  // ===== RENDER: bag tab =====

  /** Túi đồ: fixed 5×4 pixel grid (V5 origin [10,17] pitch 16, slot 14×14).
   *  IN-PLACE refresh: existing slot elements only get their CONTENT swapped
   *  (icon/qty class flips) — the DOM is never rebuilt, so hover brackets
   *  stay stable and drags never "blink" (the root cause of the jitter). */
  private renderBagGrid(wrap: HTMLElement): void {
    const g = INVENTORY_GRID;
    const total = g.cols * g.rows;
    const existing = new Map<string, HTMLElement>();
    wrap.querySelectorAll<HTMLElement>(".slot-pix").forEach((n) => {
      const k = n.dataset.slot ?? "";
      if (k !== "") existing.set(k, n);
    });
    for (let i = 0; i < total; i++) {
      const [x, y] = slotXY(g, i);
      const stack = this.inventory.bag[i];
      let slot = existing.get(String(i));
      if (!slot) {
        slot = makeSlot(g.slotW, x, y, INV_SLOT, {});
        slot.dataset.slot = String(i);
        slot.addEventListener("mousedown", (e) => {
          const idx = Number((e.currentTarget as HTMLElement).dataset.slot);
          const st = this.inventory.bag[idx];
          if (!st) return;
          if (e.button === 2) { this.splitBag(idx); return; }
          this.startDrag({ from: "bag", index: idx, stack: { ...st } }, e);
        });
        slot.addEventListener("mouseup", () =>
          this.dropOn("bag", Number(slot!.dataset.slot)));
        slot.addEventListener("contextmenu", (e) => e.preventDefault());
        wrap.appendChild(slot);
      }
      this.refreshSlotContent(slot, stack, g.slotW);
    }
  }

  /** Swap one slot element's content in place (icon, qty, cursor class). */
  private refreshSlotContent(slot: HTMLElement, stack: Stack | null, slotPx: number): void {
    const icon = slot.querySelector<HTMLElement>(".icon-img, .icon");
    const qtyEl = slot.querySelector<HTMLElement>(".qty");
    if (stack) {
      const url = itemIconUrl(stack.id);
      if (url) {
        if (icon && icon.classList.contains("icon-img")) {
          const im = icon as HTMLImageElement;
          if (im.dataset.item !== stack.id) {
            im.src = url;
            im.dataset.item = stack.id;
          }
          im.style.display = "";
        } else {
          if (icon) icon.remove();
          const im = document.createElement("img");
          im.className = "icon-img";
          im.src = url;
          im.draggable = false;
          im.dataset.item = stack.id;
          slot.appendChild(im);
        }
      } else {
        const glyph = iconFor(stack.id, this.itemEmojis);
        if (icon && icon.classList.contains("icon")) {
          icon.textContent = glyph;
          icon.style.display = "";
        } else {
          if (icon) icon.remove();
          const sp = document.createElement("span");
          sp.className = "icon";
          sp.textContent = glyph;
          slot.appendChild(sp);
        }
      }
      if (qtyEl) qtyEl.textContent = String(stack.qty);
      else {
        const q = document.createElement("span");
        q.className = "qty";
        q.textContent = String(stack.qty);
        slot.appendChild(q);
      }
      slot.title = stack.id;
      slot.classList.add("has-item");
    } else {
      if (icon) (icon as HTMLElement).style.display = "none";
      if (qtyEl) qtyEl.textContent = "";
      slot.title = "";
      slot.classList.remove("has-item");
    }
    slot.style.width = `${slotPx * PIXEL_SCALE}px`;
    slot.style.height = `${slotPx * PIXEL_SCALE}px`;
  }

  // ===== RENDER: craft tab =====

  /** Craft panel: QUICK-CRAFT 3×5 (light, left), MATERIAL 3×3 (dark, top
   *  right), RESULT slot by the anvil, CREATE button, description region. */
  private renderCraftPanel(): void {
    this.invCraftWrap.querySelectorAll(".slot-pix,.pix-btn,.pix-desc,.pix-pager").forEach((n) => n.remove());
    const sel = this.selectedQuick != null ? this.recipes[this.selectedQuick] ?? null : null;

    // --- Category tabs (top, right above the material grid): the trio of
    // pixel icons INSIDE their kit container boxes. Exactly ZERO or ONE
    // box is lifted/lit; zero = "all" mode (flat boxes, brown icons —
    // composed art, since the kit has no all-resting frame).
    this.invCraftWrap.querySelectorAll(".craft-tab").forEach((n) => n.remove());
    for (const tab of CRAFT_TABS) {
      const lit = this.craftCategory === tab.group;
      const el = document.createElement("div");
      el.className = "craft-tab" + (lit ? " lit" : "");
      // EXACT kit bboxes (Craft.json z20, local px × PIXEL_SCALE), nudged
      // +1px down (user calib): the container box is 18 kit px wide at the
      // column origin; the lit box sits at y=11 (15px tall), resting/flat
      // at y=12 (14px tall).
      const boxY = lit ? 11 : 12;
      const boxH = lit ? 15 : 14;
      const iconY = lit ? tab.yActive : tab.yRest;
      el.style.cssText =
        `left:${tab.boxX * PIXEL_SCALE}px;top:${boxY * PIXEL_SCALE}px;` +
        `width:${18 * PIXEL_SCALE}px;height:${boxH * PIXEL_SCALE}px;`;
      const box = document.createElement("img");
      box.className = "slot-bg";
      box.src = lit ? tab.boxLit : tab.boxFlat;
      box.draggable = false;
      el.appendChild(box);
      // Icon centered horizontally in the 18px box, on its kit y row.
      const icon = document.createElement("img");
      icon.className = "craft-tab-icon";
      icon.src = lit ? tab.lit : tab.rest;
      icon.draggable = false;
      icon.style.cssText =
        `left:${(tab.x - tab.boxX) * PIXEL_SCALE}px;` +
        `top:${(iconY - boxY) * PIXEL_SCALE}px;` +
        `width:${tab.w * PIXEL_SCALE}px;height:${tab.h * PIXEL_SCALE}px;`;
      el.appendChild(icon);
      el.title =
        tab.group === "tool" ? "Công cụ / Vũ khí" :
        tab.group === "decor" ? "Trang trí / Block" : "Đồ dùng được";
      el.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        e.stopPropagation();
        e.preventDefault();
        this.pressCraftTab(tab.group);
      });
      this.invCraftWrap.appendChild(el);
    }

    // --- QUICK-CRAFT catalog (light 3×5, left; NOT draggable).
    // Scrolling window over the FILTERED list: slot i shows
    // filtered[craftScroll + i]; bounds are clamped so the window never
    // shows past the end of the list.
    const list = this.filteredRecipes;
    this.clampCraftScroll();
    const scroll = this.craftScroll;
    const totalRecipes = list.length;
    const gridCells = CRAFT_QUICK_GRID.cols * CRAFT_QUICK_GRID.rows;
    // Map a filtered-list index back to the real recipes[] index so the
    // selection highlight and quick-fill keep working across filters.
    const indexOf = (r: RecipePayload): number => this.recipes.indexOf(r);
    for (let i = 0; i < gridCells; i++) {
      const [x, y] = slotXY(CRAFT_QUICK_GRID, i);
      const rec = scroll + i < totalRecipes ? list[scroll + i] : undefined;
      const rIdx = rec ? indexOf(rec) : -1;
      const haveAll = !!rec && this.canCraftNow(rec);
      const slot = makeSlot(CRAFT_QUICK_GRID.slotW, x, y, CRAFT_QUICK_CELL, {
        iconUrl: rec ? itemIconUrl(rec.output.id) : undefined,
        emoji: rec ? rec.emoji : "",
        qty: rec && rec.output.qty > 1 ? String(rec.output.qty) : "",
        selected: this.selectedQuick === rIdx,
        clickable: !!rec,
        title: rec ? rec.name : undefined,
      });
      if (rec) {
        slot.classList.add("quick");
        if (haveAll) slot.classList.add("ready");
        else if (this.isShortOnMaterials(rec)) slot.classList.add("short");
        else slot.classList.add("locked"); // red hatched overlay (no table)
        // mousedown (not click): guaranteed to fire even if another layer
        // stops the click event; ALSO more responsive (fires on press).
        slot.addEventListener("mousedown", (e) => {
          if (e.button !== 0) return;
          e.stopPropagation();
          e.preventDefault();
          this.selectedQuick = rIdx;
          // QUICK CRAFT — REAL takeover: take the recipe's materials out of
          // the BAG VIEW (local, instant) onto the grid. First return any
          // previously placed stacks, then pull from the bag stacks.
          this.fillMatGridFromBag(rec);
          this.renderInventory(); // bag cells that lost stacks repaint now
          this.renderCraftPanel();
        });
      }
      this.invCraftWrap.appendChild(slot);
    }
    // Pixel scrollbar along the right edge of the quick grid (the frame's
    // own decorative rail): draw a thumb ONLY when the list overflows, so
    // an all-fits catalog never shows a useless bar.
    this.invCraftWrap.appendChild(this.makeCraftScrollbar(scroll, totalRecipes, gridCells));

    // --- MATERIAL grid (dark 3×3, top right; draggable) — local buffer.
    for (let i = 0; i < CRAFT_MAT_GRID.cols * CRAFT_MAT_GRID.rows; i++) {
      const [x, y] = slotXY(CRAFT_MAT_GRID, i);
      const st = this.matGrid[i] ?? null;
      const slot = makeSlot(CRAFT_MAT_GRID.slotW, x, y, CRAFT_MAT_CELL, {
        iconUrl: st ? itemIconUrl(st.id) : undefined,
        emoji: st ? iconFor(st.id, this.itemEmojis) : "",
        qty: st ? String(st.qty) : "",
        title: st ? st.id : undefined,
      });
      slot.dataset.slot = String(i);
      if (st) {
        slot.addEventListener("mousedown", (e) => {
          if (e.button === 2) return;
          this.startDrag({ from: "mat", index: i, stack: { ...st } }, e);
        });
      }
      slot.addEventListener("mouseup", () => this.dropOn("mat", i));
      slot.addEventListener("contextmenu", (e) => e.preventDefault());
      this.invCraftWrap.appendChild(slot);
    }

    // --- RESULT slot [108,90,16,16]: parked craft output; click collects.
    const outSlot = makeSlot(CRAFT_RESULT.w, CRAFT_RESULT.x, CRAFT_RESULT.y, CRAFT_RESULT_ATOM, {
      iconUrl: this.parkedResult ? itemIconUrl(this.parkedResult.id) : undefined,
      emoji: this.parkedResult ? iconFor(this.parkedResult.id, this.itemEmojis) : "",
      qty: this.parkedResult ? String(this.parkedResult.qty) : "",
      title: this.parkedResult ? this.parkedResult.id : undefined,
    });
    outSlot.classList.add("result");
    if (this.parkedResult) {
      outSlot.classList.add("craftable");
      outSlot.addEventListener("click", () => this.onCollect?.(null));
      // DRAG the output straight into any bag slot (server places it there
      // — merge onto the same kind, bad_slot otherwise).
      outSlot.addEventListener("mousedown", (e) => {
        if (e.button !== 0 || !this.parkedResult) return;
        e.stopPropagation();
        e.preventDefault();
        this.startDrag(
          { from: "result", index: 0, stack: { ...this.parkedResult } }, e);
      });
    }
    this.invCraftWrap.appendChild(outSlot);

    // --- In-panel pixel CREATE button [76,69,43,13].
    const gridHasMaterials = this.compactMatGrid().length > 0;
    this.invCraftWrap.appendChild(this.makeCraftButton(gridHasMaterials, sel));

    // --- Description region [133,21,54,87]: selected quick-craft info.
    this.invCraftWrap.appendChild(this.makeCraftDescription(sel));
  }

  /** A category tab was pressed. Clicking a NEW tab selects it (filter);
   *  clicking the ALREADY-LIT tab again — or any tab within 400ms of the
   *  first click on it (the user's "double-click deselects") — returns to
   *  the ALL view (no tab lit). Selection is cleared when it falls outside
   *  the new filter so the CREATE button/description never go stale. */
  private pressCraftTab(group: "tool" | "decor" | "usable"): void {
    const now = performance.now();
    const dbl = this.lastTabClick &&
      this.lastTabClick.group === group && now - this.lastTabClick.at < 400;
    this.lastTabClick = { group, at: now };
    const next = (dbl || this.craftCategory === group) ? "all" : group;
    if (next === this.craftCategory && !dbl) return;
    this.craftCategory = next;
    this.craftScroll = 0; // new list: show it from the top
    // Deselect when the selected recipe no longer matches the filter.
    if (this.selectedQuick != null) {
      const sel = this.recipes[this.selectedQuick];
      if (!sel || (next !== "all" && (sel.group ?? "usable") !== next)) {
        this.selectedQuick = null;
      }
    }
    this.renderInventory();
  }

  /** Pure client preview of can_craft — the SERVER re-checks at craft time.
   *  Bag TOTALS are aggregated across all stacks (sparse grid = same item
   *  may sit in several slots). */
  private canCraftNow(r: RecipePayload): boolean {
    if (r.needs_table && !this.nearTable) return false;
    return r.inputs.every((inp) => this.bagCountOf(inp.id) >= inp.qty);
  }

  /** Craftable-in-principle (table OK) but short on materials. */
  private isShortOnMaterials(r: RecipePayload): boolean {
    if (r.needs_table && !this.nearTable) return false;
    return r.inputs.some((inp) => this.bagCountOf(inp.id) < inp.qty);
  }

  /** Total qty of ``id`` across ALL bag stacks (plus placed-on-grid stacks
   *  are NOT counted — the grid is what CREATE consumes). */
  private bagCountOf(id: string): number {
    return this.inventory.bag.reduce(
      (n, b) => n + (b && b.id === id ? b.qty : 0), 0);
  }

  /** QUICK CRAFT: return the current grid to the bag view, then pull each
   *  recipe input out of the bag stacks (LOCAL view edit only — the server
   *  validates + consumes the real bag at CREATE time). Missing inputs stay
   *  partial: the red-hatched quick slot + the desc rows say what's short. */
  private fillMatGridFromBag(rec: RecipePayload): void {
    // 1) Put back whatever sits on the grid (bag view first).
    for (let i = 0; i < this.matGrid.length; i++) {
      const st = this.matGrid[i];
      if (!st) continue;
      const slot = this.inventory.bag.findIndex((b) => b?.id === st.id);
      if (slot >= 0) this.inventory.bag[slot]!.qty += st.qty;
      else {
        const free = this.inventory.bag.findIndex((b) => !b);
        if (free >= 0) this.inventory.bag[free] = st;
      }
      this.matGrid[i] = null;
    }
    // 2) Pull each ingredient out of the bag view (multi-stack aware).
    let cell = 0;
    for (const inp of rec.inputs) {
      let need = inp.qty;
      while (need > 0 && cell < 9) {
        const src = this.inventory.bag.find((b) => b && b.id === inp.id && b.qty > 0);
        if (!src) break; // bag short — partial fill, desc shows the lack
        const take = Math.min(src.qty, need);
        this.matGrid[cell] = { id: inp.id, qty: take };
        cell++;
        src.qty -= take;
        need -= take;
        if (src.qty <= 0) {
          this.inventory.bag[this.inventory.bag.indexOf(src)] = null;
        }
      }
    }
  }

  /** CREATE pressed: send the material grid's multiset to the server.
   *  Optimistic: the grid clears AT ONCE (local); the server's craft_result
   *  verdict parks the output in the result slot. A FAILED craft restores
   *  the grid (nothing was consumed server-side). */
  private pressCreate(): void {
    const inputs = this.compactMatGrid();
    if (inputs.length === 0) return;
    const snapshot = this.matGrid.map((s) => (s ? { ...s } : null));
    this.matGrid = Array(9).fill(null);
    if (this.craftTab.classList.contains("active")) {
      this.renderInventory();
      this.renderCraftPanel();
    }
    this.pendingCraftSnapshot = snapshot;
    this.onCraftGrid?.(inputs);
  }

  private pendingCraftSnapshot: (Stack | null)[] | null = null;
  private lastCreateAt = 0; // CREATE debounce (see makeCraftButton)

  /** Pixel scrollbar for the quick-craft catalog. The rail's TOP and BOTTOM
   *  are EXACTLY the top of the first slot row and the bottom of the last
   *  slot row (user rule: "đỉnh và đáy của thanh cuộn = điểm cao nhất và
   *  thấp nhất của mấy slot công thức"). The thumb size/position is the
   *  visible-window fraction of the recipe list, snapped to whole pixels
   *  so it stays crisp at PIXEL_SCALE. Returns a 0-size element when the
   *  list fits the grid (nothing to scroll). */
  private makeCraftScrollbar(
    scroll: number, totalRecipes: number, gridCells: number,
  ): HTMLElement {
    const SC = PIXEL_SCALE;
    const g = CRAFT_QUICK_GRID;
    const rail = document.createElement("div");
    if (totalRecipes <= gridCells) return rail; // fits: no bar at all
    const railX = (g.firstX + (g.cols - 1) * g.stepX + g.slotW + 1) * SC;
    const railTop = g.firstY * SC;
    const railBottom = (g.firstY + (g.rows - 1) * g.stepY + g.slotH) * SC;
    const railH = railBottom - railTop;
    rail.className = "craft-scroll-rail";
    rail.style.cssText =
      `left:${railX}px;top:${railTop}px;width:${2 * SC}px;height:${railH}px;`;
    // Thumb fraction: visible cells over total recipes (min 1 cell tall).
    const frac = Math.max(gridCells / totalRecipes, gridCells / (gridCells * 4));
    let thumbH = Math.max(2 * SC, Math.round(railH * Math.min(1, frac)));
    if (thumbH > railH) thumbH = railH;
    const trackable = railH - thumbH;
    const max = this.craftScrollMax;
    const t = max > 0 ? scroll / max : 0;
    const thumbY = railTop + Math.round(trackable * t);
    const thumb = document.createElement("div");
    thumb.className = "craft-scroll-thumb";
    thumb.style.cssText =
      `left:0;top:${thumbY - railTop}px;width:100%;height:${thumbH}px;`;
    rail.appendChild(thumb);
    return rail;
  }

  /** Pixel CREATE button (demo sprite; states via filters). */
  private makeCraftButton(gridHasMaterials: boolean, sel: RecipePayload | null): HTMLElement {
    const btn = document.createElement("div");
    btn.className = "pix-btn" + (gridHasMaterials ? " ok" : " off");
    btn.style.cssText =
      `left:${CRAFT_BUTTON.x * PIXEL_SCALE}px;top:${CRAFT_BUTTON.y * PIXEL_SCALE}px;` +
      `width:${CRAFT_BUTTON.w * PIXEL_SCALE}px;height:${CRAFT_BUTTON.h * PIXEL_SCALE}px;`;
    const bg = document.createElement("img");
    bg.className = "slot-bg";
    bg.src = CRAFT_BTN.normal;
    bg.draggable = false;
    btn.appendChild(bg);
    btn.title = sel ? sel.name : "Chế tạo";
    if (gridHasMaterials) {
      btn.addEventListener("mousedown", () => btn.classList.add("pressed"));
      btn.addEventListener("mouseup", () => btn.classList.remove("pressed"));
      btn.addEventListener("mouseleave", () => btn.classList.remove("pressed"));
      // ONE definitive click: the pixel button sits under stacked layers,
      // so a click could previously land on an overlay and be swallowed
      // ("bấm cả chục lần mới ăn"). pointerdown fires through anything
      // without pointer-events that intercepts clicks; a 350 ms guard
      // debounces the mousedown+click double-fire.
      btn.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        e.stopPropagation();
        e.preventDefault();
        const now = performance.now();
        if (now - this.lastCreateAt < 350) return;
        this.lastCreateAt = now;
        this.pressCreate();
      });
    }
    return btn;
  }

  /** Description region: selected quick-craft recipe, panel-native style. */
  private makeCraftDescription(sel: RecipePayload | null): HTMLElement {
    const d = document.createElement("div");
    d.className = "pix-desc";
    d.style.cssText =
      `left:${CRAFT_DESC.x * PIXEL_SCALE}px;top:${CRAFT_DESC.y * PIXEL_SCALE}px;` +
      `width:${CRAFT_DESC.w * PIXEL_SCALE}px;height:${CRAFT_DESC.h * PIXEL_SCALE}px;`;
    if (!sel) return d;
    const out = document.createElement("div");
    out.className = "pix-desc-item";
    const url = itemIconUrl(sel.output.id);
    if (url) {
      const im = document.createElement("img");
      im.src = url;
      im.draggable = false;
      out.appendChild(im);
    } else {
      out.textContent = sel.emoji;
    }
    d.appendChild(out);
    const title = document.createElement("div");
    title.className = "pix-desc-title";
    title.textContent = sel.name;
    d.appendChild(title);
    for (const inp of sel.inputs) {
      const have = this.inventory.bag.find((b) => b?.id === inp.id)?.qty ?? 0;
      const row = document.createElement("div");
      row.className = `pix-desc-row ${have >= inp.qty ? "ok" : "lack"}`;
      const iurl = itemIconUrl(inp.id);
      if (iurl) {
        const im = document.createElement("img");
        im.src = iurl;
        im.draggable = false;
        row.appendChild(im);
      } else {
        row.textContent = iconFor(inp.id, this.itemEmojis);
      }
      const qty = document.createElement("span");
      qty.textContent = `×${inp.qty}`;
      row.appendChild(qty);
      d.appendChild(row);
    }
    if (sel.needs_table && !this.nearTable) {
      const warn = document.createElement("div");
      warn.className = "pix-desc-row lack";
      warn.textContent = "🛠️ cần bàn";
      d.appendChild(warn);
    }
    return d;
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
    this.gatePanel.classList.remove("hidden");
    this.statusEl.textContent = status;
  }

  /** Collapse just the login panel (both buttons clicked) — the gate keeps
   * showing whatever replaces it (lobby, error state) without the old panel
   * lingering beside it. */
  collapseGatePanel(): void {
    this.gatePanel.classList.add("hidden");
  }

  hideGate(): void {
    this.gateEl.classList.add("hidden");
  }

  setLoginButton(enabled: boolean, label = "🔑 Đăng nhập Discord"): void {
    this.btnLogin.disabled = !enabled;
    this.btnLogin.textContent = label;
  }

  /** The quick-login (test) button: label + enabled state + visibility. */
  setQuickButton(enabled: boolean, label = "⚡ Vào nhanh (thử nghiệm)",
                 visible = true): void {
    this.btnQuick.disabled = !enabled;
    this.btnQuick.textContent = label;
    this.btnQuick.classList.toggle("hidden", !visible);
  }

  onLoginClick(cb: () => void): void {
    this.btnLogin.addEventListener("click", cb);
  }

  onQuickClick(cb: () => void): void {
    this.btnQuick.addEventListener("click", cb);
  }

  // ----- lobby (main menu) -----

  /** Profile chip: avatar image (Discord CDN) or letter badge, name + sub. */
  setLobbyProfile(name: string, sub: string, avatarUrl: string): void {
    this.lobbyName.textContent = name || "—";
    this.lobbySub.textContent = sub;
    if (avatarUrl) {
      const img = document.createElement("img");
      img.src = avatarUrl;
      img.alt = "";
      img.draggable = false;
      this.lobbyAvatar.replaceChildren(img);
    } else {
      // Letter badge over the gradient circle (guests).
      this.lobbyAvatar.replaceChildren((name || "?").trim().charAt(0).toUpperCase());
    }
  }

  /** Show the lobby. `bannerOk` colours the server-selection banner. */
  showLobby(bannerOk = true): void {
    this.gatePanel.classList.add("hidden");
    this.lobbyEl.classList.remove("hidden");
    this.nxBanner.textContent = bannerOk ? "Đã đăng nhập" : "Chưa đăng nhập";
    this.nxBanner.classList.toggle("off", !bannerOk);
  }

  hideLobby(): void {
    this.lobbyEl.classList.add("hidden");
    this.gatePanel.classList.remove("hidden");
  }

  setLobbyStatus(text: string | null): void {
    this.lobbyStatus.hidden = text === null;
    this.lobbyStatus.textContent = text ?? "";
  }

  onLobbyPlay(cb: () => void): void {
    document.getElementById("lobby-play")!.addEventListener("click", cb);
  }

  onLobbyServers(cb: () => void): void {
    document.getElementById("lobby-servers-btn")!.addEventListener("click", cb);
  }

  onLobbyEvents(cb: () => void): void {
    document.getElementById("lobby-events")!.addEventListener("click", cb);
  }

  onLobbySettings(cb: () => void): void {
    document.getElementById("lobby-settings")!.addEventListener("click", cb);
  }

  onLobbyHelp(cb: () => void): void {
    document.getElementById("lobby-help")!.addEventListener("click", cb);
  }

  onLobbyPlayers(cb: () => void): void {
    document.getElementById("lobby-players")!.addEventListener("click", cb);
  }

  onLobbyLogout(cb: () => void): void {
    document.getElementById("lobby-logout")!.addEventListener("click", cb);
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
      const name = document.createElement("span");
      name.textContent = it.map_name;
      const pop = document.createElement("span");
      pop.className = "nx-pop";
      pop.textContent = `${it.players} ▸`;
      b.append(name, pop);
      b.addEventListener("click", () => onPick(it.channel_id));
      this.listEl.appendChild(b);
    }
  }

  // ----- HUD state -----

  /** Live connection ping (ms), colour-coded. Negative = stale/dead link. */
  setPing(rttMs: number): void {
    const el = this.pingEl;
    if (rttMs < 0) {
      el.textContent = "⚠ mất";
      el.className = "dead";
      return;
    }
    el.textContent = `${Math.round(rttMs)}ms`;
    el.className = rttMs < 120 ? "good" : rttMs < 300 ? "ok" : "bad";
  }

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

  /** Purse balances from a 20 Hz snapshot (welcome.self / snapshot.self). */
  setPurse(coins: number, crystals: number): void {
    this.purseCoins = coins;
    this.purseCrystals = crystals;
    if (this.inventoryOpen) this.renderInventory();
  }

  /**
   * Paint the two purse counters in the v5 pixel font, right-aligned
   * against each icon's right edge. Digits live OUTSIDE the repaint of
   * the bag grid — they are cleared and re-added only on balance change.
   */
  private renderPurse(_craftActive: boolean): void {
    const sig = `${this.purseCoins}:${this.purseCrystals}`;
    if (sig === this.purseLastSig) return;
    this.purseLastSig = sig;
    for (const el of this.purseDigitEls) el.remove();
    this.purseDigitEls = [
      ...makeDigitRun(this.purseCoins, INV_COIN.x + INV_COIN.w),
      ...makeDigitRun(this.purseCrystals, INV_CRYSTAL.x + INV_CRYSTAL.w + 2),
    ];
    this.invItemsWrap.append(...this.purseDigitEls);
    this.invItemsCraftWrap.append(...this.purseDigitEls.map((im) => {
      const c = im.cloneNode() as HTMLImageElement;
      return c;
    }));
    // clones need appending too (append of an already-parented node moves it)
    const clones = this.invItemsCraftWrap.querySelectorAll("img.purse-digit");
    this.purseDigitEls.push(...(clones as NodeListOf<HTMLImageElement>));
  }

  /** Purse drag: ghost icon follows the mouse; release outside = withdraw 1. */
  private startPurseDrag(itemId: string, e: MouseEvent): void {
    if (this.purseDrag) return;
    this.purseDrag = itemId;
    const ghost = document.createElement("div");
    ghost.className = "drag-ghost";
    const url = itemIconUrl(itemId);
    if (url) {
      const im = document.createElement("img");
      im.src = url;
      im.draggable = false;
      ghost.appendChild(im);
    }
    document.body.appendChild(ghost);
    this.purseGhost = ghost;
    this.updateDragGhost(e.clientX, e.clientY);
  }

  private endPurseDrag(commit: boolean): void {
    const itemId = this.purseDrag;
    this.purseDrag = null;
    this.purseGhost?.remove();
    this.purseGhost = null;
    if (commit && itemId && this.onPurseWithdraw) this.onPurseWithdraw(itemId);
  }

  setBars(hp: number, maxHp: number, mana: number, maxMana: number,
          stamina = 1, maxStamina = 0): void {
    this.hpFill.style.width = `${maxHp > 0 ? (hp / maxHp) * 100 : 0}%`;
    this.hpLabel.textContent = `${hp}/${maxHp}`;
    this.manaFill.style.width = `${maxMana > 0 ? (mana / maxMana) * 100 : 0}%`;
    this.manaLabel.textContent = `${mana}/${maxMana}`;
    if (maxStamina > 0) {
      this.staminaFill.style.width = `${(stamina / maxStamina) * 100}%`;
      this.staminaLabel.textContent = `${Math.round(stamina)}/${maxStamina}`;
    }
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

  private invVersion = -1;

  setInventory(inv: InventoryPayload, version?: number): void {
    // Same-version payloads (20 Hz snapshots that simply ack the bag) are
    // skipped: rebuilding the grid mid-drag is the inventory "jitter".
    if (version !== undefined && version === this.invVersion) return;
    this.invVersion = version ?? this.invVersion;
    // DELTA-MERGE (MMO standard): reconcile the SERVER's totals ONTO the
    // bag layout we are currently SHOWING instead of replacing it wholesale.
    // A wholesale replace is what reshuffled the grid after every
    // craft/pickup/collect (server slot order != the order the player had
    // arranged by dragging), and it fought the local drag preview.
    const placed = this.compactMatGrid();
    if (this.inventory.bag.length === 0) {
      // Bootstrap (welcome / fresh session): take the server layout as-is.
      this.inventory = inv;
      this.renderHotbar();
      if (this.inventoryOpen) this.renderInventory();
      return;
    }
    // 1) Totals currently on screen: visible bag + what sits on the local
    //    craft grid (the server doesn't know about the grid until CREATE).
    const shown: Record<string, number> = {};
    for (const s of this.inventory.bag) {
      if (s) shown[s.id] = (shown[s.id] ?? 0) + s.qty;
    }
    for (const p of placed) shown[p.id] = (shown[p.id] ?? 0) + p.qty;
    // 2) Server truth.
    const server: Record<string, number> = {};
    for (const s of inv.bag) {
      if (s) server[s.id] = (server[s.id] ?? 0) + s.qty;
    }
    // 3) Per-item delta. Everything unchanged keeps its slot untouched.
    const bag = this.inventory.bag.map((s) => (s ? { ...s } : null));
    const changed: string[] = [];
    const allIds = new Set([...Object.keys(shown), ...Object.keys(server)]);
    for (const id of allIds) {
      const d = (server[id] ?? 0) - (shown[id] ?? 0);
      if (d === 0) continue;
      changed.push(id);
      if (d > 0) {
        // Gained (pickup / collect / craft output): top up an existing stack
        // of the same kind, else fill the first empty slot, else append.
        let need = d;
        for (const b of bag) {
          if (need <= 0) break;
          if (b && b.id === id) {
            b.qty += need;
            need = 0;
          }
        }
        while (need > 0) {
          const free = bag.findIndex((b) => !b);
          if (free < 0) break; // bag full — overflow stays server-side
          const take = Math.min(need, 64);
          bag[free] = { id, qty: take };
          need -= take;
        }
      } else {
        // Lost (craft consumed / placed block / used): drain from the LAST
        // stacks of that kind (mirrors the server's later-slots-first),
        // clearing cells as they empty — no reshuffle of the rest.
        let need = -d;
        for (let i = bag.length - 1; i >= 0 && need > 0; i--) {
          const b = bag[i];
          if (!b || b.id !== id) continue;
          const take = Math.min(b.qty, need);
          b.qty -= take;
          need -= take;
          if (b.qty <= 0) bag[i] = null;
        }
      }
    }
    // If a stack grew past a display cap or drifted, the totals check below
    // falls back to a wholesale resync (rare; still never mid-drag).
    const gotTotals: Record<string, number> = {};
    for (const s of bag) if (s) gotTotals[s.id] = (gotTotals[s.id] ?? 0) + s.qty;
    for (const p of placed) gotTotals[p.id] = (gotTotals[p.id] ?? 0) + p.qty;
    const ids = new Set([...Object.keys(gotTotals), ...Object.keys(server)]);
    let totalsMatch = true;
    for (const id of ids) {
      if ((gotTotals[id] ?? 0) !== (server[id] ?? 0)) { totalsMatch = false; break; }
    }
    if (totalsMatch) {
      this.inventory = { bag, hotbar: inv.hotbar };
    } else {
      // Fallback: server layout, minus local craft-grid takeout (old path).
      const fb = inv.bag.map((s) => (s ? { ...s } : null));
      for (const p of placed) {
        let need = p.qty;
        for (const b of fb) {
          if (need <= 0) break;
          if (b && b.id === p.id) {
            const take = Math.min(b.qty, need);
            b.qty -= take;
            need -= take;
            if (b.qty <= 0) fb[fb.indexOf(b)] = null;
          }
        }
      }
      this.inventory = { bag: fb, hotbar: inv.hotbar };
    }
    this.renderHotbar();
    if (this.inventoryOpen) this.renderInventory();
  }

  setRecipes(recipes: RecipePayload[]): void {
    const changed = recipes.length !== this.recipes.length;
    this.recipes = recipes;
    // New recipe set: keep the scroll window valid (also handles shrinking).
    this.clampCraftScroll();
    if (changed && this.inventoryOpen && this.craftTab.classList.contains("active")) {
      this.renderInventory();
    }
  }

  // ----- Quick-craft catalog scrolling + category filter -----

  /** The recipe list AFTER the active category filter ("all" = everything). */
  private get filteredRecipes(): RecipePayload[] {
    if (this.craftCategory === "all") return this.recipes;
    return this.recipes.filter((r) => (r.group ?? "usable") === this.craftCategory);
  }

  /** Highest valid scroll offset = index of the LAST possible window start.
   *  With more recipes than grid cells the window slides 0..(N - cells);
   *  when everything fits, the only valid offset is 0. */
  private get craftScrollMax(): number {
    const cells = CRAFT_QUICK_GRID.cols * CRAFT_QUICK_GRID.rows;
    return Math.max(0, this.filteredRecipes.length - cells);
  }

  /** Keep the scroll window inside [0, max] (list size can change any time). */
  private clampCraftScroll(): void {
    this.craftScroll = Math.max(0, Math.min(this.craftScroll, this.craftScrollMax));
  }

  /** Mouse wheel over the quick-craft grid: scroll the catalog window.
   *  The wheel handler is attached to the CRAFT WRAP (not the window) and
   *  ignores everything outside the grid's pixel bbox — the panel, the bag
   *  and the material grid must NOT scroll or steal wheel input. */
  private attachCraftScrollHandler(): void {
    this.invCraftWrap.addEventListener("wheel", (e) => {
      const g = CRAFT_QUICK_GRID;
      const SC = PIXEL_SCALE;
      // Wheel must be INSIDE the quick-grid bbox (scroll region top/bottom
      // = top of the first slot row / bottom of the last slot row). Coords
      // are measured against the WRAP's rect — offsetX/Y would be relative
      // to whichever child element the cursor happens to be over.
      const r = this.invCraftWrap.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      const top = g.firstY * SC;
      const bottom = (g.firstY + (g.rows - 1) * g.stepY + g.slotH) * SC;
      const left = g.firstX * SC;
      const right = (g.firstX + (g.cols - 1) * g.stepX + g.slotW) * SC;
      if (
        my < top || my > bottom ||
        mx < left || mx > right
      ) return;
      const cells = g.cols * g.rows;
      if (this.filteredRecipes.length <= cells) return; // nothing to scroll
      e.preventDefault();
      e.stopPropagation();
      // 1 wheel notch = 1 row of recipes — a predictable, pixel-grid feel.
      const dir = e.deltaY > 0 ? g.cols : -g.cols;
      const max = this.craftScrollMax;
      const next = Math.max(0, Math.min(this.craftScroll + dir, max));
      if (next === this.craftScroll) return;
      this.craftScroll = next;
      this.renderCraftPanel();
    }, { passive: false });
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
    // LOCAL PROJECTION: hotbar slot N mirrors BAG SLOT N (positional, same
    // rule as the server) — dragging the stack OUT of the first N bag slots
    // really clears its hotbar cell, and an empty bag slot shows an empty
    // hotbar slot. Computed from the bag the client already holds, so every
    // local edit (drag/split/take-out) echoes INSTANTLY without waiting for
    // the server's inventory delta.
    const slotCount = Math.max(this.inventory.hotbar.length, 6);
    for (let idx = 0; idx < slotCount; idx++) {
      const stack = this.inventory.bag[idx] ?? null;
      const itemId = stack?.id ?? null;
      const qty = stack?.qty ?? 0;
      const div = document.createElement("div");
      div.className = "slot" + (idx === this.activeSlot ? " active" : "");
      div.innerHTML = `<span class="key">${idx + 1}</span><span>${iconHtml(itemId, this.itemEmojis)}</span>` +
        `<span class="qty">${qty > 0 ? qty : ""}</span>`;
      div.addEventListener("click", () => {
        this.selectSlot(idx);
      });
      this.hotbarEl.appendChild(div);
    }
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

  /** Register bag-sync + reorder callbacks (server round-trips). */
  setBagSync(
    _onMoveTo: (itemId: string, slot: number) => void,
    _onBagChanged: (inv: InventoryPayload) => void,
    onReorder?: (order: { id: string; qty: number }[]) => void,
  ): void {
    this.onReorder = onReorder ?? null;
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
