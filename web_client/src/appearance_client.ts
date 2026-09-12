// Client-side mirror of game/appearance.py WEAPON_SHEETS — kept in sync by
// tests/test_appearance.py on the Python side. Pure mapping, no IO.
export function weapon_sheet_for(held_item: string | null | undefined): string | null {
  if (!held_item) return null;
  return WEAPON_SHEETS[held_item] ?? null;
}

export const WEAPON_SHEETS: Record<string, string> = {
  dirt_sword: "coppersword",
  wood_sword: "ironsword",
  stone_sword: "goldsword",
  dirt_axe: "bronzeaxe",
  wood_axe: "ironaxe",
  stone_axe: "cobaltaxe",
  dirt_pickaxe: "bronzepickaxe",
  wood_pickaxe: "ironpickaxe",
  stone_pickaxe: "cobaltpickaxe",
  dirt_shovel: "spoon",
  wood_shovel: "smithshammer",
  stone_shovel: "ancientshovel",
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
