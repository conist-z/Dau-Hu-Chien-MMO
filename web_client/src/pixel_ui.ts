// Pixel panel geometry — Free Basic Pixel Art UI **V5 reconstruction kit**.
//
// V5 rules encoded here (docs/VIBE_CODE_HANDOFF_V5.md + panels/*.json):
// - one panel JSON per panel, one variant, local frame root at [0,0];
// - render child assets at their exact `local_bbox`, never infer from size;
// - slots are INDEPENDENT entities (surface atom + content overlays);
// - Inventory: 98×101, 20 slots 5×4, origin [10,17], pitch [16,16], slot 14×14;
// - Craft: 198×117, quick-craft grid 3×5 origin [10,33] pitch 16 (LIGHT
//   cells), material grid 3×3 origin [74,17] pitch 16 (DARK cells), result
//   slot [108,90,16,16], create button [76,69,43,13], description [133,21,54,87];
// - uniform integer scaling, image-rendering: pixelated.
//
// Craft model (user spec, Minecraft-style):
// - LIGHT grid (3×5, left) = QUICK CRAFT catalog. Click a slot (or press
//   CREATE) and, if the bag has the materials, they are "pulled" from the
//   bag into the DARK grid automatically. NOT draggable.
// - DARK grid (3×3, top right) = the MATERIAL grid — the recipe being
//   assembled. Draggable: move stacks between it and the bag, split
//   (right-click) and merge (drop same onto same).
// - CREATE consumes the material grid (server matches the exact multiset
//   to a recipe) and the output lands in the RESULT slot [108,90,16,16]
//   next to the anvil; click it to collect into the bag.
// - DESCRIPTION region [133,21,54,87]: info about the selected quick-craft
//   recipe, rendered in the panel's own pixel style (Tahoma small caps).

export const PIXEL_SCALE = 3; // one shared integer scale (V5 rule 9)

export interface GridLayout {
  firstX: number;
  firstY: number;
  stepX: number;
  stepY: number;
  cols: number;
  rows: number;
  slotW: number;
  slotH: number;
}

// ---- Inventory panel (panels/Inventory.json, frame_01, local 98×101) ----
export const INVENTORY_PANEL = {
  w: 98, h: 101,
  frame: "ui/v5/layers/inv_frame.png",
};
export const INVENTORY_GRID: GridLayout = {
  firstX: 10, firstY: 17, stepX: 16, stepY: 16,
  cols: 5, rows: 4, slotW: 14, slotH: 14,
};
export const INV_TITLE = { x: 22, y: 3, w: 55, h: 8, file: "ui/v5/layers/inv_title.png" };
export const INV_COIN = { x: 12, y: 83, w: 10, h: 10, file: "ui/v5/layers/inv_coin.png" };
export const INV_CRYSTAL = { x: 61, y: 83, w: 9, h: 11, file: "ui/v5/layers/inv_crystal.png" };
export const INV_SLOT = "ui/v5/atoms/inv_cell.png";

// ---- Craft panel — EXACT craft-demo.html composition (V5 Frame3) ----
export const CRAFT_PANEL = {
  w: 198, h: 117,
  frame: "ui/v5/layers/craft_frame.png",
};
export const CRAFT_LAYERS = [
  { x: 11, y: 12, w: 12, h: 13, file: "ui/v5/layers/craft_top1.png" },
  { x: 28, y: 18, w: 10, h: 13, file: "ui/v5/layers/craft_top2.png" },
  { x: 43, y: 18, w: 11, h: 13, file: "ui/v5/layers/craft_top3.png" },
  { x: 67, y: 87, w: 37, h: 22, file: "ui/v5/layers/craft_anvil.png" },
  { x: 142, y: 85, w: 6, h: 7, file: "ui/v5/layers/craft_round1.png" },
  { x: 142, y: 101, w: 6, h: 7, file: "ui/v5/layers/craft_round2.png" },
  { x: 193, y: 14, w: 3, h: 20, file: "ui/v5/layers/craft_scroll.png" },
];
export const CRAFT_TITLE = { x: 82, y: 3, w: 29, h: 8, file: "ui/v5/layers/craft_title.png" };

// QUICK-CRAFT grid (LIGHT cells): 3×5, origin [10,33] — recipe catalog.
export const CRAFT_QUICK_GRID: GridLayout = {
  firstX: 10, firstY: 33, stepX: 16, stepY: 16,
  cols: 3, rows: 5, slotW: 14, slotH: 14,
};
// MATERIAL grid (DARK cells): 3×3, origin [74,17] — placed recipe materials.
export const CRAFT_MAT_GRID: GridLayout = {
  firstX: 74, firstY: 17, stepX: 16, stepY: 16,
  cols: 3, rows: 3, slotW: 14, slotH: 14,
};
// Result (output) slot next to the anvil: [108,90] 16×16.
export const CRAFT_RESULT = { x: 108, y: 90, w: 16, h: 16 };
export const CRAFT_RESULT_ATOM = "ui/v5/atoms/craft_result.png";
// Create button: [76,69] 43×13 (single sprite; states via CSS filter).
export const CRAFT_BUTTON = { x: 76, y: 69, w: 43, h: 13 };
export const CRAFT_BTN = { normal: "ui/v5/atoms/create_btn.png" };
// Description region: [133,21,54,87] — selected quick-craft recipe info.
export const CRAFT_DESC = { x: 133, y: 21, w: 54, h: 87 };
// Slot surfaces: LIGHT cell for quick-craft, DARK cell for materials.
export const CRAFT_QUICK_CELL = "ui/v5/atoms/craft_input_cell.png";
export const CRAFT_MAT_CELL = "ui/v5/atoms/craft_output_cell.png";

// ---- Item icons: real bundled Twemoji PNGs (same pack the Discord hub
// uses, assets/gui/items/<codepoint>.png) — no emoji-font drift. ----
export const ITEM_ICONS: Record<string, string> = {
  potion_hp: "ui/icons/1f48a.png",
  potion_mp: "ui/icons/1f7e6.png",
  key_stone: "ui/icons/1f511.png",
  apple: "ui/icons/1f34e.png",
  plank: "ui/icons/1f7eb.png",
  stick: "ui/icons/1f962.png",
  coin: "ui/icons/1fa99.png",
  rotten_flesh: "ui/icons/1f969.png",
  iron_ore: "ui/icons/1f348.png",
  coal: "ui/icons/26ab.png",
  iron_ingot: "ui/icons/1f948.png",
  charcoal: "ui/icons/1f311.png",
  raw_meat: "ui/icons/1f356.png",
  cooked_meat: "ui/icons/1f357.png",
  stone: "ui/icons/1faa8.png",
  wood: "ui/icons/1fab5.png",
  leaves: "ui/icons/1f33f.png",
  torch: "ui/icons/1f56f.png",
  floor: "ui/icons/1f7e4.png",
  crafting_table: "ui/icons/1f6e0.png",
  furnace: "ui/icons/1f525.png",
  dirt_axe: "ui/icons/1fa93.png",
  dirt_pickaxe: "ui/icons/26cf.png",
  dirt_sword: "ui/icons/1f5e1.png",
  dirt_shovel: "ui/icons/1f944.png",
  wood_axe: "ui/icons/1fa93.png",
  wood_pickaxe: "ui/icons/26cf.png",
  wood_sword: "ui/icons/1f5e1.png",
  wood_shovel: "ui/icons/1f944.png",
  stone_axe: "ui/icons/1fa93.png",
  stone_pickaxe: "ui/icons/26cf.png",
  stone_sword: "ui/icons/1f5e1.png",
  stone_shovel: "ui/icons/1f944.png",
  iron_axe: "ui/icons/1fa93.png",
  iron_pickaxe: "ui/icons/26cf.png",
  iron_sword: "ui/icons/1f5e1.png",
  iron_shovel: "ui/icons/1f944.png",
  dirt: "ui/icons/1f7e4.png",
};

/** Icon URL for an item id, or null (caller falls back to an emoji glyph). */
export function itemIconUrl(id: string | null): string | null {
  if (!id) return null;
  return ITEM_ICONS[id] ?? null;
}

// Debug mode (localStorage.pixel_ui_debug=1): red slot outlines + coords.
export const DEBUG_SLOTS =
  typeof localStorage !== "undefined" && localStorage.getItem("pixel_ui_debug") === "1";

/** Local px of slot N's top-left inside the panel (origin + pitch × col/row). */
export function slotXY(g: GridLayout, index: number): [number, number] {
  const r = Math.floor(index / g.cols);
  const c = index % g.cols;
  return [g.firstX + c * g.stepX, g.firstY + r * g.stepY];
}

export interface SlotContent {
  iconUrl?: string | null; // real PNG icon (ITEM_ICONS) — preferred
  emoji?: string;          // emoji glyph fallback — "" = empty
  qty?: string;            // quantity overlay — "" = hidden
  title?: string;          // tooltip
  selected?: boolean;
  clickable?: boolean;
}

/**
 * Build one independent pixel slot (V5 rule 6): surface atom + content
 * overlays, absolutely positioned by the CALLER at slotXY × PIXEL_SCALE.
 * `slotPx` is the slot's declared core size (14 for grids, 16 for result).
 */
export function makeSlot(
  slotPx: number,
  x: number,
  y: number,
  atomFile: string,
  content: SlotContent,
): HTMLDivElement {
  const s = document.createElement("div");
  s.className = "slot-pix" + (content.selected ? " selected" : "") +
    (content.clickable ? " clickable" : "");
  s.style.cssText =
    `left:${x * PIXEL_SCALE}px;top:${y * PIXEL_SCALE}px;` +
    `width:${slotPx * PIXEL_SCALE}px;height:${slotPx * PIXEL_SCALE}px;` +
    `--pix-scale:${PIXEL_SCALE};`;
  if (DEBUG_SLOTS) {
    s.classList.add("debug");
    const dbg = document.createElement("span");
    dbg.className = "dbg-label";
    dbg.textContent = `${x},${y}`;
    s.appendChild(dbg);
  }
  const bg = document.createElement("img");
  bg.className = "slot-bg";
  bg.src = atomFile;
  bg.draggable = false;
  let icon: HTMLElement;
  if (content.iconUrl) {
    const im = document.createElement("img");
    im.className = "icon-img";
    im.src = content.iconUrl;
    im.draggable = false;
    icon = im;
  } else {
    const sp = document.createElement("span");
    sp.className = "icon";
    sp.textContent = content.emoji ?? "";
    icon = sp;
  }
  s.append(bg, icon);
  if (content.qty) {
    const q = document.createElement("span");
    q.className = "qty";
    q.textContent = content.qty;
    s.appendChild(q);
  }
  if (content.title) s.title = content.title;
  return s;
}

/** One absolutely-positioned kit layer at its exact V5 local bbox. */
export function makeLayer(l: { x: number; y: number; w: number; h: number; file: string }): HTMLImageElement {
  const im = document.createElement("img");
  im.className = "pix-layer";
  im.src = l.file;
  im.draggable = false;
  im.style.cssText =
    `left:${l.x * PIXEL_SCALE}px;top:${l.y * PIXEL_SCALE}px;` +
    `width:${l.w * PIXEL_SCALE}px;height:${l.h * PIXEL_SCALE}px;`;
  return im;
}

/** Size a panel wrap and point its `<img.panel>` at the V5 frame layer. */
export function sizePanel(
  wrap: HTMLElement,
  panel: { w: number; h: number; frame: string },
): void {
  wrap.style.width = `${panel.w * PIXEL_SCALE}px`;
  wrap.style.height = `${panel.h * PIXEL_SCALE}px`;
  let img = wrap.querySelector<HTMLImageElement>("img.panel");
  if (!img) {
    img = document.createElement("img");
    img.className = "panel";
    wrap.appendChild(img);
  }
  img.src = panel.frame;
  img.style.width = `${panel.w * PIXEL_SCALE}px`;
  img.style.height = `${panel.h * PIXEL_SCALE}px`;
}
