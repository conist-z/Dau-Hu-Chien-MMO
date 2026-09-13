// Client-side mirror of game/appearance.py WEAPON_SHEETS — kept in sync by
// tests/test_appearance.py on the Python side. Pure mapping, no IO.
export function weapon_sheet_for(held_item: string | null | undefined): string | null {
  if (!held_item) return null;
  return WEAPON_SHEETS[held_item] ?? null;
}

export const WEAPON_SHEETS: Record<string, string> = {
  // 4 tool tiers: wood->bronze sheets, iron->iron, gold->gold, steel->cobalt
  wood_sword: "bronzesword",
  iron_sword: "ironsword",
  gold_sword: "goldsword",
  steel_sword: "steelsword",
  wood_axe: "bronzeaxe",
  iron_axe: "ironaxe",
  gold_axe: "goldaxe",
  steel_axe: "cobaltaxe",
  wood_pickaxe: "bronzepickaxe",
  iron_pickaxe: "ironpickaxe",
  gold_pickaxe: "goldpickaxe",
  steel_pickaxe: "cobaltpickaxe",
  wood_shovel: "spoon",
  // generated held-item sheets (blocks + common items) — keep in sync with
  // game/appearance.py WEAPON_SHEETS (asserted by tests/test_appearance.py)
  apple: "apple",
  stone: "stone",
  wood: "wood",
  leaves: "leaves",
  torch: "torch",
  stick: "stick",
  plank: "plank",
  coal: "coal",
  iron_ore: "ironore",
  iron_ingot: "ironbar",
  coin: "coin",
  cooked_meat: "cookedmeat",
  rotten_flesh: "rottenflesh",
  potion_hp: "potionhp",
};
