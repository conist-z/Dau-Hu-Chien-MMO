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
  };
  inventory: InventoryPayload;
  recipes: RecipePayload[];
  blocks: [number, number, string][];
  players: PlayerPayload[];
}

export interface SnapshotPayload {
  type: "snapshot";
  seq: number;
  map_id: string;
  clock: number;
  weather: string;
  players: PlayerPayload[];
  blocks: [number, number, string][];
  self: {
    hp: number;
    max_hp: number;
    mana: number;
    max_mana: number;
    coins: number;
    x: number;
    y: number;
  };
  inventory: InventoryPayload;
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
}

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
  | { type: "held"; slot: number; item_id: string | null }
  | { type: "error"; code: string }
  | { type: "pong"; t: unknown };
