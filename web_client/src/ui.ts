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
  CRAFT_RESULT, CRAFT_RESULT_ATOM,
  CRAFT_TITLE, INV_COIN, INV_CRYSTAL,
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

function fmtClock(secondsOfDay: number): string {
  const h = Math.floor(secondsOfDay / 3600) % 24;
  const m = Math.floor((secondsOfDay % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** One stack living in a grid (bag slot or craft material slot). */
interface Stack { id: string; qty: number }

/** Drag payload: which grid a drag started from + the stack. */
interface DragSrc {
  from: "bag" | "mat";
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
  private invPanel = document.getElementById("inv-panel")!;
  private invItemsWrap = document.getElementById("inv-items-wrap")!;
  private invCraftWrap = document.getElementById("inv-craft-wrap")!;
  private invItemsCraftWrap = document.getElementById("inv-items-craft")!;
  private craftDetail = document.getElementById("craft-detail")!;
  private craftTab: HTMLElement;
  private itemsTab: HTMLElement;
  private selectedQuick: number | null = null; // quick-craft catalog index
  private nearTable = false; // updated from snapshots (server truth)

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
  private dragGhost: HTMLDivElement | null = null;
  private activeSlot = 0;
  private onCommand: ((text: string) => void) | null = null;
  private onCraftGrid: ((inputs: { id: string; qty: number }[]) => void) | null = null;
  private onSplit: ((slot: number) => void) | null = null;
  private onCollect: (() => void) | null = null;
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
        // Craft tab ALSO shows the inventory panel below it (drag partner).
        this.invItemsCraftWrap.classList.toggle("hidden", !isCraft);
        this.craftDetail.classList.toggle("hidden", true);
        this.renderInventory();
      });
    });
    document.getElementById("inv-close")!.addEventListener("click", () => this.toggleInventory(false));
    // Panel geometry once at boot (V5: integer scale, exact local bboxes).
    sizePanel(this.invItemsWrap, INVENTORY_PANEL);
    sizePanel(this.invCraftWrap, CRAFT_PANEL);
    sizePanel(this.invItemsCraftWrap, INVENTORY_PANEL);
    // Static kit layers — placed once, exact bboxes.
    this.invItemsWrap.append(makeLayer(INV_TITLE), makeLayer(INV_COIN), makeLayer(INV_CRYSTAL));
    this.invItemsCraftWrap.append(makeLayer(INV_TITLE), makeLayer(INV_COIN), makeLayer(INV_CRYSTAL));
    this.invCraftWrap.append(makeLayer(CRAFT_TITLE), ...CRAFT_LAYERS.map((l) => makeLayer(l)));
    // Global drag ghost tracking (mouse-move + drop outside any slot).
    window.addEventListener("mousemove", (e) => this.updateDragGhost(e.clientX, e.clientY));
    window.addEventListener("mouseup", (e) => {
      // MAGNETIC DROP: resolve to the NEAREST droppable slot within a
      // generous snap radius instead of only the exact hovered slot —
      // near-misses snap in instead of springing back.
      if (!this.drag) return;
      const t = this.nearestDropTarget(e.clientX, e.clientY);
      if (t) this.dropOn(t.from, t.index);
      else this.cancelDrag();
    });
  }

  /** The nearest droppable slot (same-panel partner grids) within radius. */
  private nearestDropTarget(x: number, y: number): { from: "bag" | "mat"; index: number } | null {
    const RADIUS = 26; // px — generous snap (slot is 42px at scale 3)
    const hits: { from: "bag" | "mat"; index: number; d: number }[] = [];
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

  /** Register the result-slot collect callback. */
  onCollectResult(cb: () => void): void {
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

  toggleInventory(force?: boolean): void {
    const show = force ?? this.invPanel.classList.contains("hidden");
    this.invPanel.classList.toggle("hidden", !show);
    if (show) {
      // Opening must ALWAYS repaint (the guard would skip a same-sig open
      // and show a stale grid after server-side changes while hidden).
      this.lastBagSig = "";
      this.renderInventory();
    }
  }

  get inventoryOpen(): boolean {
    return !this.invPanel.classList.contains("hidden");
  }

  private lastBagSig = "";

  private renderInventory(): void {
    // Repaint guard: identical bag + same tab + same craft context = skip.
    // nearTable + parkedResult are part of the sig: a stale quick-craft
    // overlay (stuck red hatch while near the table) meant those state
    // flips didn't repaint the slots.
    const bagSig = JSON.stringify(this.inventory.bag);
    const craftActive = this.craftTab.classList.contains("active");
    const sig = bagSig + "|" + (craftActive ? "craft" : "items") +
      "|" + (this.nearTable ? 1 : 0) +
      "|" + (this.parkedResult ? this.parkedResult.id + this.parkedResult.qty : "-");
    if (sig === this.lastBagSig && this.drag === null) return;
    this.lastBagSig = sig;
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
  }

  private updateDragGhost(x: number, y: number): void {
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
    } else {
      this.matToBag(d.index, index);
    }
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

  /** Túi đồ: fixed 5×4 pixel grid (V5 origin [10,17] pitch 16, slot 14×14). */
  private renderBagGrid(wrap: HTMLElement): void {
    wrap.querySelectorAll(".slot-pix").forEach((n) => n.remove());
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
      slot.dataset.slot = String(i);
      if (stack) {
        slot.addEventListener("mousedown", (e) => {
          if (e.button === 2) { this.splitBag(i); return; }
          this.startDrag({ from: "bag", index: i, stack: { ...stack } }, e);
        });
      }
      slot.addEventListener("mouseup", () => this.dropOn("bag", i));
      slot.addEventListener("contextmenu", (e) => e.preventDefault());
      wrap.appendChild(slot);
    }
  }

  // ===== RENDER: craft tab =====

  /** Craft panel: QUICK-CRAFT 3×5 (light, left), MATERIAL 3×3 (dark, top
   *  right), RESULT slot by the anvil, CREATE button, description region. */
  private renderCraftPanel(): void {
    this.invCraftWrap.querySelectorAll(".slot-pix,.pix-btn,.pix-desc,.pix-pager").forEach((n) => n.remove());
    const sel = this.selectedQuick != null ? this.recipes[this.selectedQuick] ?? null : null;

    // --- QUICK-CRAFT catalog (light 3×5, left; NOT draggable).
    for (let i = 0; i < CRAFT_QUICK_GRID.cols * CRAFT_QUICK_GRID.rows; i++) {
      const [x, y] = slotXY(CRAFT_QUICK_GRID, i);
      const rec = this.recipes[i];
      const haveAll = !!rec && this.canCraftNow(rec);
      const slot = makeSlot(CRAFT_QUICK_GRID.slotW, x, y, CRAFT_QUICK_CELL, {
        iconUrl: rec ? itemIconUrl(rec.output.id) : undefined,
        emoji: rec ? rec.emoji : "",
        qty: rec && rec.output.qty > 1 ? String(rec.output.qty) : "",
        selected: this.selectedQuick === i,
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
          this.selectedQuick = i;
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
      outSlot.addEventListener("click", () => this.onCollect?.());
    }
    this.invCraftWrap.appendChild(outSlot);

    // --- In-panel pixel CREATE button [76,69,43,13].
    const gridHasMaterials = this.compactMatGrid().length > 0;
    this.invCraftWrap.appendChild(this.makeCraftButton(gridHasMaterials, sel));

    // --- Description region [133,21,54,87]: selected quick-craft info.
    this.invCraftWrap.appendChild(this.makeCraftDescription(sel));
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
      btn.addEventListener("click", () => this.pressCreate());
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
    this.statusEl.textContent = status;
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

  private invVersion = -1;

  setInventory(inv: InventoryPayload, version?: number): void {
    // Same-version payloads (20 Hz snapshots that simply ack the bag) are
    // skipped: rebuilding the grid mid-drag is the inventory "jitter".
    if (version !== undefined && version === this.invVersion) return;
    this.invVersion = version ?? this.invVersion;
    // MERGE with local craft takeout: the server's bag doesn't know about
    // stacks sitting on the material grid (local buffer until CREATE).
    // If the server bag has the taken quantities, subtract them so the bag
    // view stays consistent with what the player sees on the grid; when a
    // craft consumes them the server bag simply no longer has them.
    const placed = this.compactMatGrid();
    if (placed.length > 0) {
      const bag = inv.bag.map((s) => (s ? { ...s } : null));
      for (const p of placed) {
        let need = p.qty;
        for (const b of bag) {
          if (need <= 0) break;
          if (b && b.id === p.id) {
            const take = Math.min(b.qty, need);
            b.qty -= take;
            need -= take;
            if (b.qty <= 0) bag[bag.indexOf(b)] = null;
          }
        }
      }
      this.inventory = { bag, hotbar: inv.hotbar };
    } else {
      this.inventory = inv;
    }
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
        ? (this.inventory.bag.find((b) => b?.id === itemId)?.qty ?? 0)
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
