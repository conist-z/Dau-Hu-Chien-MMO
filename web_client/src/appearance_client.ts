// Client-side mirror of game/appearance.py WEAPON_SHEETS — kept in sync by
// tests/test_appearance.py on the Python side. Pure mapping, no IO.
export function weapon_sheet_for(held_item: string | null | undefined): string | null {
  if (!held_item) return null;
  return WEAPON_SHEETS[held_item] ?? null;
}

export const WEAPON_SHEETS: Record<string, string> = {
  // 5 tool tiers: wood->bronze, stone->tin/battle/bone, iron->iron,
  // gold->gold, steel->cobalt (mirror of game/appearance.py)
  wood_sword: "bronzesword",
  stone_sword: "tinsword",
  iron_sword: "ironsword",
  gold_sword: "goldsword",
  steel_sword: "steelsword",
  wood_axe: "bronzeaxe",
  stone_axe: "bronzebattleaxe",
  iron_axe: "ironaxe",
  gold_axe: "goldaxe",
  steel_axe: "cobaltaxe",
  wood_pickaxe: "bronzepickaxe",
  stone_pickaxe: "bonepickaxe",
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
  gold_ore: "goldore",
  iron_ingot: "ironbar",
  gold_ingot: "goldbar",
  steel_ingot: "moonrockore",
  coin: "coin",
  cooked_meat: "cookedmeat",
  rotten_flesh: "rottenflesh",
  potion_hp: "potionhp",
  // ÉP BUỘC (hard rule): every item holds a sheet — keep in sync with
  // game/appearance.py (asserted by tests/test_appearance.py).
  charcoal: "charcoal",
  crafting_table: "crafting_table",
  dirt: "dirt",
  floor: "floor",
  furnace: "furnace",
  key_stone: "keystone",
  mushroom_brown: "mushroom_brown",
  mushroom_purple: "mushroom_purple",
  potion_mp: "potion_mp",
  raw_meat: "raw_meat",
  seed: "seed",
};
