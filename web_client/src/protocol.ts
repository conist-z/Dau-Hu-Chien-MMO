// Protocol types mirroring web_api/protocol.py + payload shapes from
// web_api/snapshots.py. Keep in sync with the Python side.

export const MSG_JOIN = "join";
export const MSG_LIST = "list";
export const MSG_INPUT = "input";
export const MSG_ACTION = "action";
export const MSG_INV_OP = "inventory_op";
export const MSG_CRAFT_OP = "craft_op";
export const MSG_CHAT_CMD = "chat_cmd";
export const MSG_PING = "ping";

export interface WelcomePayload {
  type: "welcome";
  map: {
    id: string;
    name: string;
    width: number;
    height: number;
    tile_width: number;
    tile_height: number;
    collision: number[][];
    layers: { name: string; data: number[][] }[];
    tilesets: {
      index: number;
      firstgid: number;
      columns: number;
      tilewidth: number;
      image: string | null;
    }[];
    spawn: [number, number];
  };
  self: {
    id: number;
    name: string;
    x: number;
    y: number;
    hp: number;
    max_hp: number;
    mana: number;
    max_mana: number;
    coins: number;
    walk_speed: number;
    run_speed: number;
    dir: string;
  };
  inventory: InventoryPayload;
  recipes: RecipePayload[];
  // Item id -> emoji (server registries; authoritative for icons).
  item_emojis: Record<string, string>;
  blocks_catalog: { id: string; emoji: string; name: string }[];
  blocks: [number, number, string][];
  resources: [number, number, number][];
  // "ax,ay" -> [hits, base_needed, tiles] (node bbox for the bar + fall anim)
  res_progress: Record<string, [number, number, number[][]]>;
  // Tiles of felled nodes: [x, y, anchor_x, anchor_y] — walkable in prediction
  res_felled: [number, number, number, number][];
  players: PlayerPayload[];
  // Item id currently held by self (hotbar slot -> item). Null = empty hand
  // (the hand dot still renders — plan A — just without a tool icon).
  held: string | null;
  // Paperdoll manifest (frame grid + animation rows/speeds) for the player
  // sheets in assets/players/. Absent on older servers — client falls back
  // to the plan-A square body in that case.
  players_manifest?: PlayersManifest;
}

// Manifest shape (see game/appearance.py + assets/players/players_manifest.json).
export interface SheetEntry {
  file: string;
  frame_w: number;
  frame_h: number;
  cols: number;
  rows: number;
  offset_x: number;
  offset_y: number;
}

export interface PlayersManifest {
  base: SheetEntry;
  weapons: Record<string, SheetEntry>;
  rows: Record<string, number>;
  frames_per_row: number;
  speeds: Record<string, number>;
  // Optional visual-only upscale (2 = player spans 2 tiles tall). Pure
  // rendering — collision stays 1 tile, server unchanged.
  scale?: number;
}

// Web-pack zombie (realtime float mover, separate from the Discord turn
// pack): [id, x, y, hp, max_hp, kind, facing, anim]. Float x/y = tile units
// for 60 fps interpolation; facing = N/S/E/W/NE/NW/SE/SW; anim =
// "walk"|"idle"|"atk" cuts the matching row/frame from the local sheet copy
// (Kaetram 5 cols x 9 rows of 32px: row 0 atk 5f, row 1 walk 4f, row 2
// idle 2f) — never the whole stretched sheet.
export type WebZombiePayload = [string, number, number, number, number, string, string, string];

export interface SnapshotPayload {
  type: "snapshot";
  seq: number;
  map_id: string;
  clock: number;
  weather: string;
  players: PlayerPayload[];
  blocks: [number, number, string][];
  zombies: WebZombiePayload[];
  self: {
    hp: number;
    max_hp: number;
    mana: number;
    max_mana: number;
    coins: number;
    x: number;
    y: number;
    dir: string;
    aim: { dx: number; dy: number } | null;
    // Item id currently held by self (mirrors welcome.held, 20 Hz echo).
    held: string | null;
    // Death state (hp 0): the server ignores inputs while dead; the client
    // freezes prediction + shows a respawn overlay (Kaetram dead parity).
    dead?: boolean;
    respawn_s?: number;
  };
  inventory: InventoryPayload;
  resources: [number, number, number][];
  // "ax,ay" -> [hits, base_needed, tiles] (node bbox for the bar + fall anim)
  res_progress: Record<string, [number, number, number[][]]>;
  // Tiles of felled nodes: [x, y, anchor_x, anchor_y] — walkable in prediction
  res_felled: [number, number, number, number][];
}

export interface PlayerPayload {
  id: number;
  name: string;
  x: number; // float tile units (web truth)
  y: number;
  tile: [number, number]; // what Discord clients see (floor)
  dir: string;
  sprite: string;
  web: boolean;
  // Item id currently held (hotbar slot -> item). Null = empty hand.
  held: string | null;
}

// Night zombie (Kaetram-style mob, shared pack with Discord):
// [id, x, y, hp, max_hp, kind("walker"|"hunter")]. Float x/y = tile units.
export type ZombiePayload = [string, number, number, number, number, string];

export interface InventoryPayload {
  bag: { id: string; qty: number }[];
  hotbar: (string | null)[];
}

export interface RecipePayload {
  id: string;
  name: string;
  emoji: string;
  inputs: { id: string; qty: number }[];
  output: { id: string; qty: number };
  needs_table: boolean;
  description: string;
}

export interface ScenarioItem {
  channel_id: number;
  map_id: string;
  map_name: string;
  players: number;
}

export type ServerFrame =
  | WelcomePayload
  | SnapshotPayload
  | { type: "login_result"; ok: boolean; token?: string; user_id?: number; display_name?: string; error?: string }
  | { type: "scenario_list"; items: ScenarioItem[] }
  | { type: "inventory_delta"; inventory: InventoryPayload }
  | { type: "craft_result"; ok: boolean; reason: string; item_id: string | null; qty: number }
  | { type: "asset_data"; name: string; b64: string | null }
  | { type: "push"; message: string }
  | { type: "action_result"; name: string; ok: boolean; reason: string; tx: number | null; ty: number | null; kind: string; target_id?: string | null; target_defeated?: boolean; needed: number | null; drops: [string, number][]; damage?: number; critical?: boolean; missed?: boolean }
  | { type: "held"; slot: number; item_id: string | null }
  | { type: "error"; code: string }
  | { type: "pong"; t: unknown };
