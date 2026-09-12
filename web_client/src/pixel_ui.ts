// Pixel panel geometry — Free Basic Pixel Art UI **V5 reconstruction kit**.
//
// V5 rules encoded here (docs/VIBE_CODE_HANDOFF_V5.md + panels/*.json):
// - one panel JSON per panel, one variant, local frame root at [0,0];
// - render child assets at their exact `local_bbox`, never infer from size;
// - slots are INDEPENDENT entities (surface atom + content overlays), not
//   one composite atlas;
// - Inventory: 98×101, 20 slots 5×4, origin [10,17], pitch [16,16], slot 14×14;
// - Craft: 198×117, inputs 3×5 origin [10,33] pitch 16, outputs 3×3 origin
//   [74,17] pitch 16, result slot [108,90,16,16] (V5 PATCH_LOG correction),
//   button [75,68,43,14], description region [133,21,54,87];
// - uniform integer scaling, image-rendering: pixelated;
// - content anchoring: item centered in the 14×14 slot core, qty at the
//   slot's bottom-right corner.
//
// Game mapping (how the two panels bind to the game's craft model):
// - Inventory tab: bag maps row-major into the 5×4 grid; coin + crystal
//   currency overlays at the V5 layer bboxes.
// - Craft tab: the 3×5 INPUT grid shows the SELECTED recipe's materials
//   (one material stack per slot, repeated per V5 independence rule);
//   the 3×3 OUTPUT grid is the recipe catalog page (9 recipes per page,
//   arrows page through); the result slot renders the selected recipe's
//   output and triggers craft on click; the in-panel button also crafts.

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
// Independent overlays (layers from the V5 kit, exact local bboxes).
export const INV_TITLE = { x: 22, y: 3, w: 55, h: 8, file: "ui/v5/layers/inv_title.png" };
export const INV_COIN = { x: 12, y: 83, w: 10, h: 10, file: "ui/v5/layers/inv_coin.png" };
export const INV_CRYSTAL = { x: 61, y: 83, w: 9, h: 11, file: "ui/v5/layers/inv_crystal.png" };
export const INV_ARROW_L = { x: 5, y: 44, w: 4, h: 7, file: "ui/v5/layers/inv_arrow1.png" };
export const INV_ARROW_R = { x: 89, y: 44, w: 4, h: 7, file: "ui/v5/layers/inv_arrow2.png" };
// Slot surface atom (14×14) — every one of the 20 slots is this same core
// asset rendered as an independent entity (V5: cells_004 is reference-only).
export const INV_SLOT = "ui/v5/atoms/inv_cell.png";

// ---- Craft panel — EXACT craft-demo.html composition (V5 Frame3, corrected):
// frame 198×117, inputs 3×5 origin [10,33] pitch 16 (14×14 input surface),
// outputs 3×3 origin [74,17] pitch 16 (14×14 output surface), result slot
// [108,90] 16×16, create button [76,69] 43×13, decor at demo bboxes.
export const CRAFT_PANEL = {
  w: 198, h: 117,
  frame: "ui/v5/layers/craft_frame.png",
};
// Decorative demo layers, exact local bboxes.
export const CRAFT_LAYERS = [
  { x: 11, y: 12, w: 12, h: 13, file: "ui/v5/layers/craft_top1.png" },
  { x: 28, y: 18, w: 10, h: 13, file: "ui/v5/layers/craft_top2.png" },
  { x: 43, y: 18, w: 11, h: 13, file: "ui/v5/layers/craft_top3.png" },
  { x: 67, y: 87, w: 37, h: 22, file: "ui/v5/layers/craft_anvil.png" },
  { x: 133, y: 21, w: 54, h: 87, file: "ui/v5/layers/craft_text.png" },
  { x: 142, y: 85, w: 6, h: 7, file: "ui/v5/layers/craft_round1.png" },
  { x: 142, y: 101, w: 6, h: 7, file: "ui/v5/layers/craft_round2.png" },
  { x: 154, y: 33, w: 13, h: 13, file: "ui/v5/layers/craft_resulticon.png" },
  { x: 193, y: 14, w: 3, h: 20, file: "ui/v5/layers/craft_scroll.png" },
];
export const CRAFT_TITLE = { x: 82, y: 3, w: 29, h: 8, file: "ui/v5/layers/craft_title.png" };

// INPUT grid: 3×5, origin [10,33], pitch [16,16] — selected recipe's materials.
export const CRAFT_INPUT_GRID: GridLayout = {
  firstX: 10, firstY: 33, stepX: 16, stepY: 16,
  cols: 3, rows: 5, slotW: 14, slotH: 14,
};
// OUTPUT grid: 3×3, origin [74,17], pitch [16,16] — recipe catalog page.
export const CRAFT_OUTPUT_GRID: GridLayout = {
  firstX: 74, firstY: 17, stepX: 16, stepY: 16,
  cols: 3, rows: 3, slotW: 14, slotH: 14,
};
// Result slot — demo composition: [108,90] 16×16.
export const CRAFT_RESULT = { x: 108, y: 90, w: 16, h: 16 };
export const CRAFT_RESULT_ATOM = "ui/v5/atoms/craft_result.png";
// Create button — demo: [76,69] 43×13 (single sprite; states via CSS filter).
export const CRAFT_BUTTON = { x: 76, y: 69, w: 43, h: 13 };
export const CRAFT_BTN = { normal: "ui/v5/atoms/create_btn.png" };
// Description region — demo text layer [133,21,54,87].
export const CRAFT_DESC = { x: 133, y: 21, w: 54, h: 87 };
// Craft slot surfaces — demo uses SEPARATE input/output cell art (different
// border shade); use each grid's own surface, never one shared cell.
export const CRAFT_INPUT_CELL = "ui/v5/atoms/craft_input_cell.png";
export const CRAFT_OUTPUT_CELL = "ui/v5/atoms/craft_output_cell.png";

// ---- Item icons: real bundled Twemoji PNGs (same pack the Discord hub
// uses, assets/gui/items/<codepoint>.png) — no emoji-font drift. ----
export const ITEM_ICONS: Record<string, string> = {
  // items (game/items.py)
  potion_hp: "ui/icons/1f48a.png",
  potion_mp: "ui/icons/1f7e6.png",
  key_stone: "ui/icons/1f511.png",
  apple: "ui/icons/1f34e.png",
  plank: "ui/icons/1f7eb.png",
  stick: "ui/icons/1f962.png",
  coin: "ui/icons/1fa99.png",
  rotten_flesh: "ui/icons/1f969.png",
  // smelting chain (game/smelting.py)
  iron_ore: "ui/icons/1f348.png",
  coal: "ui/icons/26ab.png",
  iron_ingot: "ui/icons/1f948.png",
  charcoal: "ui/icons/1f311.png",
  raw_meat: "ui/icons/1f356.png",
  cooked_meat: "ui/icons/1f357.png",
  // blocks (game/blocks.py)
  stone: "ui/icons/1faa8.png",
  wood: "ui/icons/1fab5.png",
  leaves: "ui/icons/1f33f.png",
  torch: "ui/icons/1f56f.png",
  floor: "ui/icons/1f7e4.png",
  crafting_table: "ui/icons/1f6e0.png",
  furnace: "ui/icons/1f525.png",
  // tools — every tier (dirt/wood/stone/iron) shares its family emoji
  // (axe/pickaxe/sword/shovel); mirrors ITEM_ICON_CODEPOINTS in the hub.
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
  // raw materials
  dirt: "ui/icons/1f7e4.png",
};

/** Icon URL for an item id, or null (caller falls back to an emoji glyph). */
export function itemIconUrl(id: string | null): string | null {
  if (!id) return null;
  return ITEM_ICONS[id] ?? null;
}

// Debug mode (localStorage.pixel_ui_debug=1): red slot outlines + local
// coords, per V5 validation workflow.
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
  emoji?: string;        // emoji glyph fallback — "" = empty
  qty?: string;          // quantity overlay — "" = hidden
  title?: string;        // tooltip
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
  // Icon: real PNG when available, else the emoji glyph.
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

/**
 * Size a panel wrap and swap in the layered frame. The wrap's `<img.panel>`
 * (declared in index.html) is replaced by the V5 frame layer so both tabs
 * share one composition path; decor layers are appended by the caller.
 */
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
