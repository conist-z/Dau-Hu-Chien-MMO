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
  // MATERIAL grid state (3×3): what the player placed for crafting. Purely
  // a UI buffer — the server validates the multiset at CREATE time.
  private matGrid: (Stack | null)[] = Array(9).fill(null);
  private resultStack: Stack | null = null;
  private drag: DragSrc | null = null;
  private dragGhost: HTMLDivElement | null = null;
  private activeSlot = 0;
  private onCommand: ((text: string) => void) | null = null;
  private onCraftGrid: ((inputs: Stack[]) => void) | null = null;
  private onSplit: ((itemId: string) => void) | null = null;
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
    window.addEventListener("mouseup", () => {
      // Dropping outside any slot returns the stack to its source grid.
      if (this.drag) this.cancelDrag();
    });
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
        no_matching_recipe: "Chưa đúng công thức — xem Description.",
        empty_grid: "Đặt nguyên liệu vào ô tối màu trước.",
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

  /** Extra craft hooks: grid craft + quick-fill + split (all optional). */
  setCraftHooks(
    onCraftGrid: (inputs: Stack[]) => void,
    _onQuickFill: (recipeId: string) => void,
    onSplit: (itemId: string) => void,
  ): void {
    this.onCraftGrid = onCraftGrid;
    this.onSplit = onSplit;
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
      this.renderBagGrid(this.invItemsWrap);
    } else {
      this.renderBagGrid(this.invItemsCraftWrap); // craft tab: drag partner
      this.renderCraftPanel();
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
    this.syncBagOrder();
    this.renderInventory();
  }

  /** Send the whole bag order to the server (validated multiset there). */
  private syncBagOrder(): void {
    const order = this.inventory.bag.map((s) =>
      s ? { id: s.id, qty: s.qty } : { id: "", qty: 0 });
    this.onReorder?.(order);
  }

  /** Material grid internal move/merge. */
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

  /** Bag → material grid: place 1 (left-click drag) — Minecraft feel. */
  private bagToMat(bagIndex: number, matIndex: number): void {
    const src = this.inventory.bag[bagIndex];
    if (!src) return;
    const dst = this.matGrid[matIndex];
    if (dst && dst.id !== src.id) {
      // Different item: swap the two stacks.
      this.matGrid[matIndex] = { ...src };
      this.inventory.bag[bagIndex] = { ...dst };
      this.syncBagOrder();
      this.renderCraftPanel();
      return;
    }
    if (dst) {
      dst.qty += 1;
    } else {
      this.matGrid[matIndex] = { id: src.id, qty: 1 };
    }
    src.qty -= 1;
    if (src.qty <= 0) this.inventory.bag[bagIndex] = null;
    // Server: consume exactly 1 via move_to-free path — we model placement
    // client-side and reconcile at CREATE; a mid-session rejoin rebuilds
    // the bag from the server truth. Repaint now.
    this.renderCraftPanel();
  }

  /** Material grid → bag: return the stack (or 1 unit) to the bag. */
  private matToBag(matIndex: number, bagIndex: number): void {
    const src = this.matGrid[matIndex];
    if (!src) return;
    const target = this.inventory.bag[bagIndex];
    if (target && target.id !== src.id) {
      // Swap bag stack with the placed stack.
      this.matGrid[matIndex] = { ...target };
      this.inventory.bag[bagIndex] = { ...src };
    } else if (target) {
      target.qty += src.qty;
      this.matGrid[matIndex] = null;
    } else {
      this.inventory.bag[bagIndex] = { ...src };
      this.matGrid[matIndex] = null;
    }
    this.renderCraftPanel();
  }

  // Wire with bindMoveTo() — kept optional so a half-wired build never
  // blocks compilation with TS6133 (session WIP guard).
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  private onReorder: ((order: { id: string; qty: number }[]) => void) | null = null;

  /** Right-click a BAG slot: split half into the next empty bag slot. */
  private splitBag(index: number): void {
    const st = this.inventory.bag[index];
    if (!st || st.qty < 2) return;
    // Client-side split (visual) + server op to persist the new order.
    const half = st.qty - Math.floor(st.qty / 2);
    st.qty -= half;
    // Find the first empty slot after index (wrap).
    const bag = this.inventory.bag;
    let at = -1;
    for (let i = 1; i <= bag.length; i++) {
      const j = (index + i) % bag.length;
      if (!bag[j]) { at = j; break; }
    }
    if (at >= 0) bag[at] = { id: st.id, qty: half };
    else st.qty += half; // no room: undo
    this.onSplit?.(st.id);
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
        // mousedown (not click): guaranteed to fire even if another layer
        // stops the click event; ALSO more responsive (fires on press).
        slot.addEventListener("mousedown", (e) => {
          if (e.button !== 0) return;
          e.stopPropagation();
          e.preventDefault();
          this.selectedQuick = i;
          // Quick-fill: pull this recipe's materials from the bag into the
          // material grid (server validates the multiset at CREATE).
          this.fillMatGridFromBag(rec);
        });
      }
      this.invCraftWrap.appendChild(slot);
    }

    // --- MATERIAL grid (dark 3×3, top right; draggable).
    for (let i = 0; i < CRAFT_MAT_GRID.cols * CRAFT_MAT_GRID.rows; i++) {
      const [x, y] = slotXY(CRAFT_MAT_GRID, i);
      const st = this.matGrid[i];
      const slot = makeSlot(CRAFT_MAT_GRID.slotW, x, y, CRAFT_MAT_CELL, {
        iconUrl: st ? itemIconUrl(st.id) : undefined,
        emoji: st ? iconFor(st.id, this.itemEmojis) : "",
        qty: st ? String(st.qty) : "",
        title: st ? st.id : undefined,
      });
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

    // --- RESULT slot [108,90,16,16]: last craft's output; click collects.
    const outSlot = makeSlot(CRAFT_RESULT.w, CRAFT_RESULT.x, CRAFT_RESULT.y, CRAFT_RESULT_ATOM, {
      // class hook for the larger result-icon CSS rule
      iconUrl: this.resultStack ? itemIconUrl(this.resultStack.id) : undefined,
      emoji: this.resultStack ? iconFor(this.resultStack.id, this.itemEmojis) : "",
      qty: this.resultStack ? String(this.resultStack.qty) : "",
      title: this.resultStack ? this.resultStack.id : undefined,
    });
    outSlot.classList.add("result");
    if (this.resultStack) {
      outSlot.classList.add("craftable");
      outSlot.addEventListener("click", () => this.collectResult());
    }
    this.invCraftWrap.appendChild(outSlot);

    // --- In-panel pixel CREATE button [76,69,43,13].
    const gridHasMaterials = this.matGrid.some((s) => s);
    this.invCraftWrap.appendChild(this.makeCraftButton(gridHasMaterials, sel));

    // --- Description region [133,21,54,87]: selected quick-craft info.
    this.invCraftWrap.appendChild(this.makeCraftDescription(sel));
  }

  /** Pure client preview of can_craft — the SERVER re-checks at craft time. */
  private canCraftNow(r: RecipePayload): boolean {
    if (r.needs_table && !this.nearTable) return false;
    return r.inputs.every(
      (inp) => (this.inventory.bag.find((b) => b?.id === inp.id)?.qty ?? 0) >= inp.qty,
    );
  }

  /** Pull a quick-craft recipe's materials from the bag into the grid. */
  private fillMatGridFromBag(rec: RecipePayload): void {
    // Clear any previous materials back to the bag first (they were never
    // server-consumed — this is a pure UI buffer).
    this.returnMatGridToBag();
    // Place each ingredient (1 stack per material, qty per recipe).
    let slot = 0;
    for (const inp of rec.inputs) {
      const have = this.inventory.bag.find((b) => b?.id === inp.id)?.qty ?? 0;
      if (have < inp.qty) {
        this.toast(`Thiếu ${inp.id} (${have}/${inp.qty}).`);
        this.renderCraftPanel();
        return;
      }
      for (const b of this.inventory.bag) {
        if (!b || b.id !== inp.id) continue;
        const take = Math.min(b.qty, inp.qty);
        b.qty -= take;
        if (b.qty <= 0) this.inventory.bag[this.inventory.bag.indexOf(b)] = null;
        this.matGrid[slot] = { id: inp.id, qty: take };
        inp.qty -= take; // remaining need (mutating the payload copy is fine)
        if (inp.qty <= 0) break;
      }
      slot++;
    }
    // Sync the bag with the server (materials were "taken" client-side).
    this.onBagChanged?.(this.inventory);
    this.renderCraftPanel();
  }

  /** Return everything in the material grid to the bag (no server craft). */
  private returnMatGridToBag(): void {
    for (let i = 0; i < this.matGrid.length; i++) {
      const st = this.matGrid[i];
      if (!st) continue;
      const existing = this.inventory.bag.find((b) => b?.id === st.id);
      if (existing) existing.qty += st.qty;
      else {
        const free = this.inventory.bag.findIndex((b) => !b);
        if (free >= 0) this.inventory.bag[free] = st;
        else this.inventory.bag.push(st); // grid is 20 — server trims
      }
      this.matGrid[i] = null;
    }
  }

  /** CREATE pressed: send the material grid's multiset to the server. */
  private pressCreate(): void {
    const inputs = this.matGrid.filter((s): s is Stack => !!s);
    if (inputs.length === 0) return;
    this.onCraftGrid?.(inputs);
  }

  /** Craft result arrived: park it in the result slot (click to collect). */
  showCraftOutput(itemId: string, qty: number): void {
    if (!itemId) return;
    if (this.resultStack && this.resultStack.id === itemId) {
      this.resultStack.qty += qty;
    } else {
      this.resultStack = { id: itemId, qty };
    }
    if (this.craftTab.classList.contains("active")) this.renderCraftPanel();
  }

  /** Collect the result slot into the bag. */
  private collectResult(): void {
    const st = this.resultStack;
    if (!st) return;
    const existing = this.inventory.bag.find((b) => b?.id === st.id);
    if (existing) existing.qty += st.qty;
    else {
      const free = this.inventory.bag.findIndex((b) => !b);
      if (free >= 0) this.inventory.bag[free] = st;
      else this.inventory.bag.push(st);
    }
    this.resultStack = null;
    this.onBagChanged?.(this.inventory);
    this.renderCraftPanel();
  }

  private onBagChanged: ((inv: InventoryPayload) => void) | null = null;

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
    onBagChanged: (inv: InventoryPayload) => void,
    onReorder?: (order: { id: string; qty: number }[]) => void,
  ): void {
    this.onBagChanged = onBagChanged;
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
