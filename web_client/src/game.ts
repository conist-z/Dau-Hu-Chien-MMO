// Phaser game scene: builds the world from the welcome payload (Tiled layers
// + tilesets fetched through the relay), interpolates 20 Hz snapshots to
// 60 fps rendering, follows the camera on the local player.

import Phaser from "phaser";
import type { DropPayload, PlayerPayload, PlayersManifest, SnapshotPayload, WebZombiePayload, WelcomePayload } from "./protocol";
import { PaperdollBody, b64ToBytes, registerPaperdollTextures, registerWeaponSheet } from "./paperdoll";
import { WEAPON_SHEETS as WEAPON_SHEET_BY_ITEM, weapon_sheet_for } from "./appearance_client";
import { ICON_ITEM_IDS } from "./pixel_ui";
import { perf } from "./perf";
import { dayNightPhaser } from "./daynight_phaser";
import { meteorFxBusyNear } from "./meteors";

const PLAYER_SIZE = 22; // px in world space (tile = 32)

// World pixel size of ONE TILE. Most maps are 32px (bigmap/kaetram), but
// ekonia maps are 16px — every `* 32` against the map grid MUST go through
// this per-scene value (set from the welcome payload's tile_width) or the
// actors/collision overlay land at 2x the art coordinates ("box chặn lệch
// hẳn khỏi map").
const BASE_TILE = 32;
// Per-kind night-mob sheet geometry (Kaetram client sprites.json, mob rows:
// atk/walk/idle x right/up/down; LEFT mirrors the right row). Sizes in px
// (32 = cell size); scale keeps every mob about one tile tall on screen.
interface MobSheetInfo {
  texKey: string;
  size: number;             // display size (px) on screen
  cellW: number;            // sheet frame width (px, from Kaetram sprites.json)
  cellH: number;            // sheet frame height (px)
  rows: Record<"atk" | "walk" | "idle", Record<"right" | "up" | "down", [number, number]>>; // [row, frameCount]
  /** Minifolks wildlife: ONE facing per sheet — never pick up/down rows,
   * flipX only for leftward facing. */
  singleFacing?: boolean;
}
const MOB_SHEETS: Record<string, MobSheetInfo> = {
  zombie: {
    texKey: "mob-zombie", size: 48, cellW: 32, cellH: 32,
    rows: { atk: { right: [0, 4], up: [3, 5], down: [6, 4] }, walk: { right: [1, 4], up: [4, 4], down: [7, 4] }, idle: { right: [2, 2], up: [5, 2], down: [8, 2] } },
  },
  skeleton: {
    texKey: "mob-skeleton", size: 70, cellW: 48, cellH: 48,
    rows: { atk: { right: [0, 3], up: [3, 3], down: [6, 3] }, walk: { right: [1, 4], up: [4, 4], down: [7, 4] }, idle: { right: [2, 2], up: [5, 3], down: [8, 3] } },
  },
  spider: {
    texKey: "mob-spider", size: 44, cellW: 35, cellH: 35,
    rows: { atk: { right: [7, 2], up: [6, 2], down: [4, 2] }, walk: { right: [3, 5], up: [2, 5], down: [0, 5] }, idle: { right: [3, 1], up: [2, 1], down: [0, 1] } },
  },
  slime: {
    texKey: "mob-slime", size: 48, cellW: 32, cellH: 32,
    rows: { atk: { right: [0, 5], up: [3, 5], down: [6, 5] }, walk: { right: [1, 4], up: [4, 4], down: [7, 4] }, idle: { right: [2, 2], up: [5, 2], down: [8, 2] } },
  },
  bat: {
    texKey: "mob-bat", size: 40, cellW: 32, cellH: 48,
    rows: { atk: { right: [0, 5], up: [3, 5], down: [6, 5] }, walk: { right: [1, 5], up: [4, 5], down: [7, 5] }, idle: { right: [2, 5], up: [5, 5], down: [8, 5] } },
  },
  rat: {
    texKey: "mob-rat", size: 32, cellW: 32, cellH: 32,
    rows: { atk: { right: [1, 6], up: [4, 4], down: [7, 4] }, walk: { right: [2, 3], up: [5, 4], down: [8, 4] }, idle: { right: [3, 2], up: [6, 4], down: [9, 4] } },
  },
  // Cave/forest packs (Kaetram sprites.json geometry, same 9-row layout).
  skeleton2: {
    texKey: "mob-skeleton2", size: 70, cellW: 48, cellH: 48,
    rows: { atk: { right: [0, 3], up: [3, 3], down: [6, 3] }, walk: { right: [1, 4], up: [4, 4], down: [7, 4] }, idle: { right: [2, 2], up: [5, 2], down: [8, 3] } },
  },
  spectre: {
    texKey: "mob-spectre", size: 46, cellW: 34, cellH: 34,
    rows: { atk: { right: [0, 8], up: [3, 6], down: [6, 6] }, walk: { right: [1, 4], up: [4, 8], down: [7, 4] }, idle: { right: [2, 2], up: [5, 8], down: [8, 2] } },
  },
  goblin: {
    texKey: "mob-goblin", size: 36, cellW: 26, cellH: 26,
    rows: { atk: { right: [0, 3], up: [3, 3], down: [6, 3] }, walk: { right: [1, 3], up: [4, 4], down: [7, 4] }, idle: { right: [2, 2], up: [5, 2], down: [8, 2] } },
  },
  hobgoblin: {
    texKey: "mob-hobgoblin", size: 48, cellW: 32, cellH: 32,
    rows: { atk: { right: [0, 3], up: [3, 3], down: [6, 3] }, walk: { right: [1, 3], up: [4, 4], down: [7, 4] }, idle: { right: [2, 2], up: [5, 2], down: [8, 2] } },
  },
  // ---- daytime wildlife (Minifolks Forest Animals, scripts/pack_animal_sheets.py)
  // 4-row layout (atk/walk/idle/dead) with ONE facing: up/down reuse the
  // right row; LEFT mirrors right (zombieFlipX already handles W/NW/SW).
  // Frames are VERBATIM 32px pack cells — art fills only its natural share
  // of the cell (bunny 8px … deer2 17px of 32), feet on the tile bottom.
  // `size` = displayed CELL (multiple of 16 so size/32 × camera-zoom 2 stays
  // integer); `artH` = displayed ART height = native_art_px × size/32 —
  // used to hover the HP bar just above the animal's back instead of an
  // empty transparent cell top (bear's cell is 2 tiles tall).
  bunny: {
    texKey: "mob-bunny", size: 64, artH: 16, cellW: 32, cellH: 32, singleFacing: true,
    rows: { atk: { right: [0, 5], up: [0, 5], down: [0, 5] }, walk: { right: [1, 5], up: [1, 5], down: [1, 5] }, idle: { right: [2, 5], up: [2, 5], down: [2, 5] } },
  },
  deer: {
    texKey: "mob-deer", size: 80, artH: 35, cellW: 32, cellH: 32, singleFacing: true,
    rows: { atk: { right: [0, 5], up: [0, 5], down: [0, 5] }, walk: { right: [1, 5], up: [1, 5], down: [1, 5] }, idle: { right: [2, 5], up: [2, 5], down: [2, 5] } },
  },
  deer2: {
    texKey: "mob-deer2", size: 80, artH: 43, cellW: 32, cellH: 32, singleFacing: true,
    rows: { atk: { right: [0, 5], up: [0, 5], down: [0, 5] }, walk: { right: [1, 5], up: [1, 5], down: [1, 5] }, idle: { right: [2, 5], up: [2, 5], down: [2, 5] } },
  },
  bird: {
    texKey: "mob-bird", size: 48, artH: 21, cellW: 32, cellH: 32, singleFacing: true,
    rows: { atk: { right: [0, 5], up: [0, 5], down: [0, 5] }, walk: { right: [1, 5], up: [1, 5], down: [1, 5] }, idle: { right: [2, 5], up: [2, 5], down: [2, 5] } },
  },
  boar: {
    texKey: "mob-boar", size: 96, artH: 30, cellW: 32, cellH: 32, singleFacing: true,
    rows: { atk: { right: [0, 5], up: [0, 5], down: [0, 5] }, walk: { right: [1, 5], up: [1, 5], down: [1, 5] }, idle: { right: [2, 5], up: [2, 5], down: [2, 5] } },
  },
  bear: {
    texKey: "mob-bear", size: 128, artH: 44, cellW: 32, cellH: 32, singleFacing: true,
    rows: { atk: { right: [0, 5], up: [0, 5], down: [0, 5] }, walk: { right: [1, 5], up: [1, 5], down: [1, 5] }, idle: { right: [2, 5], up: [2, 5], down: [2, 5] } },
  },
  fox: {
    texKey: "mob-fox", size: 80, artH: 28, cellW: 32, cellH: 32, singleFacing: true,
    rows: { atk: { right: [0, 5], up: [0, 5], down: [0, 5] }, walk: { right: [1, 5], up: [1, 5], down: [1, 5] }, idle: { right: [2, 5], up: [2, 5], down: [2, 5] } },
  },
  wolf: {
    texKey: "mob-wolf", size: 96, artH: 33, cellW: 32, cellH: 32, singleFacing: true,
    rows: { atk: { right: [0, 5], up: [0, 5], down: [0, 5] }, walk: { right: [1, 5], up: [1, 5], down: [1, 5] }, idle: { right: [2, 5], up: [2, 5], down: [2, 5] } },
  },
};
const INTERP_BUFFER_MS = 120; // render ~2 ticks behind for smoothness
// How long a client-optimistic place/break tile stays applied while we wait
// for the action_result echo. Longer than one RTT (~300ms worst case) but
// short enough that a lost echo self-heals: the every-snapshot blockSet
// rebuild + this expiry guarantee no phantom solid/walkable tile outlives
// this window.
const OPTIMISTIC_TTL_MS = 800;

// Direction name -> unit vector (mirrors game.state.Direction).
const DIR_VECTORS: Record<string, [number, number]> = {
  NORTH: [0, -1], SOUTH: [0, 1], EAST: [1, 0], WEST: [-1, 0],
  NORTH_EAST: [1, -1], NORTH_WEST: [-1, -1],
  SOUTH_EAST: [1, 1], SOUTH_WEST: [-1, 1],
};

// Client-side projection of the server's block self-repair (user rule
// 13/09): damage drains at 1/s after the 3.5 s idle — the client mirrors
// that pace between 20 Hz samples so the crack rewinds smoothly.
const BLOCK_HEAL_RATE = 1.0;
// Field-forage resource gids (bigmap nấm/cỏ/hoa layers): these SHATTER into
// sand grains when felled instead of the tree tip-over fall (mirrors
// game/resources.py TILE_NODE_PARTS one-hit forage nodes).
const FORAGE_GIDS = new Set([49, 50, 52, 53, 54, 55, 56, 57, 58]);
// Station blocks the E-prompt/hover/click interact flow targets (mirrors
// game/crafting.py STATION_BLOCK_IDS).
const STATION_BLOCK_IDS = new Set(["crafting_table", "furnace"]);
// Interact range: Chebyshev tiles (matches server STATION_RANGE = 3).
const STATION_INTERACT_RANGE = 3;

interface RemotePlayer {
  container: Phaser.GameObjects.Container;
  // Square (web players → paperdoll) or circle ("chat" mode players —
  // the round avatar token). Both are Phaser Shapes.
  body: Phaser.GameObjects.Shape;
  // Controlling client mode of this body ("chat" | "web").
  mode: "chat" | "web";
  label: Phaser.GameObjects.Text;
  webBadge: Phaser.GameObjects.Arc | null;
  // Plan A hand: small circle same colour as the body, orbiting with facing.
  hand: Phaser.GameObjects.Arc;
  handColor: number;
  // Tool/weapon icon over the hand: Kaetram pixel-art PNG (Image) when the
  // icon texture is in, emoji Text only as fallback. Empty = bare hand.
  toolIcon: Phaser.GameObjects.Text | Phaser.GameObjects.Image;
  held: string | null;
  swingT0: number; // performance.now() of the last swing (0 = never)
  // interpolation buffer: [t_recv, x, y]
  buf: [number, number, number][];
  dir: string;
  // Kaetram paperdoll for remote web bodies (spawned when the manifest +
  // base sheet are ready; null while the square placeholder is showing).
  doll: PaperdollBody | null;
  // Latest profile payload for the click-popup (kept fresh each snapshot).
  profile: PlayerPayload;
}

// Hand orbit: distance from the body centre + dot radius. Exported so the
// self hand (not in a container) shares the exact same geometry.
export const HAND_ORBIT = 20;
export const HAND_RADIUS = 5;
// Drop-entity icon (Kaetram PNG) displayed size in px — half a tile reads
// as a ground pickup without covering the tile it sits on.
export const DROP_ICON_PX = 16;
// Hand icon (Kaetram PNG) displayed size in px — 1.5 tiles read as a held
// tool at this scale; emoji Text fallback keeps its own 13px font size.
export const HAND_ICON_PX = 24;
// Swing: on every harvest hit the hand thrusts out by SWING_EXTRA and back
// over SWING_MS (sin curve), for self AND remote hands alike.
const SWING_MS = 220;
const SWING_EXTRA = 12;

export class WorldScene extends Phaser.Scene {
  private welcome: WelcomePayload | null = null;
  private tileTextures = new Map<string, string>(); // image name -> texture key
  private loadedTilesets = new Set<string>(); // tileset images that arrived
  // DEDUPE GUARD: signature of the last bake (map id + tileset sheet set).
  // A repeated welcome (reconnect, alt-tab rejoin, portal bounce-back) used
  // to re-run the FULL bake every time — on the big classic maps that is a
  // 5760x3840 canvas repaint + an 88 MB getImageData readback per pass
  // (phone 1 load, PC 3 loads = the PC-only stutter). Same map + same sheets
  // = nothing to repaint, so skip everything.
  private bakeSig = "";
  private players = new Map<number, RemotePlayer>();
  private selfMarker: Phaser.GameObjects.Rectangle | null = null;
  private selfLabel: Phaser.GameObjects.Text | null = null;
  private blockLayer: Phaser.GameObjects.Layer | null = null;
  // Diff cache for the block overlay: tile key -> sprite. Rapid place/break
  // used to tear down and rebuild EVERY block rectangle on each snapshot sig
  // change (~20 Hz while building) — the create/destroy churn was a real
  // source of stutter. Now only added/removed tiles touch the scene.
  private blockSprites = new Map<string, Phaser.GameObjects.GameObject>();

  // ----- Station interact (E prompt + hover cursor + click-to-open) -----
  /** Tile keys of STATION blocks from the latest snapshot (crafting table,
   *  furnace...). Rebuilt with the block sig — same cost class. */
  private stationTiles = new Set<string>();
  // ----- NPC tokens (welcome.npcs): emoji sprite + name label per NPC -----
  private npcSprites = new Map<
    string, { container: Phaser.GameObjects.Container; x: number; y: number }
  >();
  /** Set by main.ts: opens the NPC dialogue toast (E / click near an NPC). */
  onNpcInteract: ((npc: { id: string; name: string }) => void) | null = null;
  /** Nearest NPC within interact range (E-key gate). */
  private nearestNpc: { id: string; name: string } | null = null;

  /** True when an NPC is within interact range of self (E-key gate). */
  nearNpc(): boolean {
    return this.nearestNpc !== null;
  }

  /** E-key path: fire the dialogue handler for the adjacent NPC. */
  requestNpcDialogue(): void {
    if (this.nearestNpc) this.onNpcInteract?.(this.nearestNpc);
  }
  /** The "E" prompt bubble above the nearest in-range station. */
  private stationPrompt: Phaser.GameObjects.Container | null = null;
  /** Nearest station tile within interact range (recomputed per frame). */
  private nearestStation: { x: number; y: number } | null = null;
  /** Set from main.ts: opens the craft panel (E key / station click). */
  onStationInteract: (() => void) | null = null;
  /** E-key press feedback: the bubble pops (scale punch). */
  private promptPunchAt = 0;
  private mapBake: Phaser.GameObjects.Image | null = null;
  /** Asset requester captured from buildWorld: the bake needs it to re-ask
   *  for tileset sheets it is missing (see bakeMapIfReady). */
  private assetFetch: ((name: string) => void) | null = null;
  /** Rate limit for the missing-tileset re-request (one try/second max). */
  private lastTilesetKick = 0;
  private lastBlockSig = "";
  // Real block faces (assets/blocks/<id>.png fetched via asset_request).
  // Pending ids get a plain rectangle until the texture arrives, then the
  // next updateBlocks redraws them with the sprite.
  private blockTextures = new Set<string>();
  private pendingFetch: ((id: string) => void) | null = null;
  // --- plan A "tay cầm tool": self hand = small circle same colour as the
  // body (0x5865f2) + tool emoji from the HELD hotbar slot. Mirrors the
  // remote hand geometry (HAND_ORBIT/HAND_RADIUS) but lives in world space
  // next to selfMarker (self is predicted, not in a container).
  private selfHand: Phaser.GameObjects.Arc | null = null;
  private selfToolIcon: Phaser.GameObjects.Text | Phaser.GameObjects.Image | null = null;
  private selfHeld: string | null = null;
  // Latest server stamina (snapshot self payload). 0 = tired: prediction
  // caps at walk speed, matching the server's run gate.
  private selfStamina = 1.0;
  // Server says self is eating: prediction walks at HALF speed to mirror
  // the tick (EAT_SPEED_MULT) — divergence here would glide-correct visibly.
  private selfEating = false;
  // Server-authoritative emoji map (welcome.item_emojis): item id -> emoji.
  // Set on buildWorld + kept fresh on every snapshot (welcome may re-fire).
  private itemEmojis: Record<string, string> = {};
  // --- paperdoll (Kaetram parity): manifest + per-player bodies ---
  private playersManifest: PlayersManifest | null = null;
  private paperdollAsked = false;
  private paperdollReady = false;
  private selfDoll: PaperdollBody | null = null;
  private remoteDolls = new Map<number, PaperdollBody>();
  // --- client-side prediction (instant local movement) ---
  private inputVec = { dx: 0, dy: 0, running: false };
  /** Previous prediction frame's input vector — feeds the direction-reversal
   *  guard below. */
  private prevInputVec = { dx: 0, dy: 0, running: false };
  private selfX = 0; // predicted float position, TILE units
  private selfY = 0;
  private collision: number[][] = []; // collision[y][x] = 1 blocks
  private collisionDebug: Phaser.GameObjects.Graphics | null = null;
  /** World px per tile for the CURRENT map (welcome.tile_width; default 32). */
  private tilePx = BASE_TILE;
  // Sub-tile alpha masks (server: rendering/tile_masks.py). Key "x,y" ->
  // bitfield (res=8): bit my*res+mx = opaque sub-cell. Only PARTIAL tiles
  // (server strips near-full/empty ones) appear here — everything else uses
  // the plain square grid, so the prediction mirrors game/collision.py.
  private tileMasks = new Map<string, number>();
  private static readonly MASK_RES = 8;
  private selfServerPos = { x: 0, y: 0 }; // last authoritative position
  private lastServerRecv = 0;
  /** Input-sequence reconciliation (Source-engine style): buffer of recent
   * seq'd inputs (newest last). On each snapshot the scene rewinds self to
   * the server position, replays every buffered input with seq > acked
   * last_seq through the SAME collision used by prediction, and drops the
   * replayed prefix. Makes prediction match authority to ~0 error: no
   * threshold glide, no visible correction, no direction mis-guessing. */
  private inputLog: { seq: number; dx: number; dy: number; running: boolean; dt: number }[] = [];
  /** Highest seq acknowledged by the server. */
  private lastAckedSeq = -1;
  // performance.now() of the last ack increase — the dead-report snap only
  // arms after 3 s WITHOUT progress (a live ack stream = never).
  private lastAckProgressAt = 0;
  // ---- desync debug (F3 toggle, see getDebugInfo) ----
  debugEnabled = false;
  private lastConvergeMoveAt = 0;
  private convergeEvents = 0;
  private snapCount = 0;
  private snapRateT0 = 0;
  private snapRateCount = 0;
  private snapRate = 0;
  /** dt accumulated since the last seq'd input was flushed (replay needs
   * per-input real dt; see setLocalInput / recordInputDt). */
  private pendingDt = 0;
  /** Drift-recovery tracking: how long (ms) predicted pos has differed from
   * the server pos by more than a tile with NO pending inputs. Persisting
   * divergence = prediction desync → glide home (see applySnapshot). */
  private driftIdleMs = 0;
  /** True once a snapshot with last_seq has arrived (server supports it). */
  private seqReplayActive = false;
  /** Wall-clock time of the last update() frame — feeds pendingDt. */
  private lastFrameT = 0;

  /** CLIENT-AUTHORITATIVE position reporting: the web client is the truth
   *  source for its own body position (the user asked for this model — no
   *  server time-integration desync is possible in it). The prediction
   *  position is handed to Net on every input flush; the server pulls the
   *  real body toward the reported position (speed-capped + collision-checked
   *  server-side, so it stays fair) instead of integrating time itself. */
  getSelfPos(): { x: number; y: number } {
    return { x: this.selfX, y: this.selfY };
  }

  /** Server-side truth (last snapshot self pos) — debug overlay reads this. */
  getServerPos(): { x: number; y: number } {
    return { x: this.selfServerPos.x, y: this.selfServerPos.y };
  }

  /** One-shot debug line: everything needed to diagnose "hitbox bên kia".
   *  Called by the F3 overlay in main.ts (10 Hz). */
  getDebugInfo(): string {
    const d = Math.hypot(this.selfX - this.selfServerPos.x, this.selfY - this.selfServerPos.y);
    const idle = this.inputLog.length === 0;
    // PERF telemetry: EMA frame time + fps (12s half-life) — distinguishes
    // "network desync" stutter (d jumps, fps steady) from "render stall"
    // stutter (fps craters while d stays tiny).
    const fps = this.avgFrameMs > 0 ? 1000 / this.avgFrameMs : 0;
    return [
      `pred (${this.selfX.toFixed(2)}, ${this.selfY.toFixed(2)})`,
      `srv (${this.selfServerPos.x.toFixed(2)}, ${this.selfServerPos.y.toFixed(2)})`,
      `d=${d.toFixed(2)}`, d > 1 ? "⚠DIFY>1" : "ok",
      `ack=${this.lastAckedSeq}`, `log=${this.inputLog.length}`,
      idle ? "idle" : "moving",
      `snap=${this.snapRate.toFixed(0)}/s`,
      `in=${this.sentRate.toFixed(0)}/s ack=${this.ackedRate.toFixed(0)}/s rtt=${this.netRttMs.toFixed(0)}ms`,
      `cvg=${this.convergeEvents}`,
      `fps=${fps.toFixed(0)} (${this.avgFrameMs.toFixed(1)}ms)`,
    ].join("  ");
  }

  /** Perf telemetry (update()): EMA of real frame ms, 12s half-life. */
  private avgFrameMs = 16.7;
  /** INPUT CHANNEL throughputs (F3): sent/s vs acked/s. On a healthy 20 Hz
   *  link both read ~20; ack far below sent = input frames lost between the
   *  browser and the bot (relay/bot lag) — the true "lag y cũ" source. */
  inputsSent = 0;
  inputsAcked = 0;
  private sentRateT0 = 0;
  private sentRate = 0;
  private ackedRateT0 = 0;
  private ackedRate = 0;
  /** Server-body liveness: last authority pos + when it last CHANGED. A
   *  frozen body (>1.5 s) with residual drift = broken report channel; a
   *  moving body = healthy idle converge (echo lag, never snap). */
  private lastSrvX = 0;
  private lastSrvY = 0;
  private lastSrvMoveAt = 0;
  /** Set by main.ts: opens the profile popup for the clicked player. */
  onPlayerClick: ((p: PlayerPayload) => void) | null = null;

  /** Show name labels above players (Settings page toggle). The self
   *  label + remote labels share one flag; toggling repaints instantly. */
  showNames = true;

  /** Canvas of the current map bake — the minimap draws straight from the
   *  SAME pixels the game renders (zero extra art, zero extra requests).
   *  Null before the first bake (map page shows a placeholder then). */
  minimapSourcePx(): HTMLCanvasElement | null {
    if (!this.textures.exists("map-bake")) return null;
    const src = this.textures.get("map-bake").getSourceImage();
    return src instanceof HTMLCanvasElement ? src : null;
  }

  /** Toggle all remote + self name labels (Settings). Labels live inside
   *  player containers; selfLabel is a scene-level text. */
  setNamesVisible(visible: boolean): void {
    this.showNames = visible;
    this.selfLabel?.setVisible(visible);
    for (const rp of this.players.values()) rp.label.setVisible(visible);
  }

  /** True when the given normalized screen pos sits on a remote body
   *  (profile-clickable). Called from the canvas hover hook to swap the
   *  cursor icon (like the craft-table hover). */
  pointerOverRemotePlayerAt(nx: number, ny: number): boolean {
    const p = this.screenToWorldPx(nx, ny);
    if (!p) return false;
    const R = 20; // px hit radius around each body center
    for (const rp of this.players.values()) {
      const c = rp.container;
      if (Math.abs(c.x - p.x) <= R && Math.abs(c.y - p.y) <= R) return true;
    }
    return false;
  }

  /** Normalized (0..1) screen pos -> world px, or null off-map. */
  private screenToWorldPx(nx: number, ny: number): { x: number; y: number } | null {
    const cam = this.cameras.main;
    const dw = this.scale.displaySize.width || 1;
    const dh = this.scale.displaySize.height || 1;
    return {
      x: cam.scrollX + (nx * dw) / cam.zoom,
      y: cam.scrollY + (ny * dh) / cam.zoom,
    };
  }

  /** True when the base paperdoll sheet is registered (portrait available). */
  hasPaperdollTexture(): boolean {
    return this.paperdollReady && this.textures.exists("pd-base");
  }

  // ---- mobile camera (mobile_controls.ts) -------------------------------
  // ZOOM REMOVED (user 23/09: "bỏ hẳn tính năng thay đổi camera zoom — để
  // cam mặc định luôn luôn"). The camera is follow-only at the authored
  // 2.0 zoom for EVERY mode; no pinch, no pan, no offsets — nothing that
  // can desync the view from the prediction.


  /** Data-URL portrait of the base paperdoll (idle SOUTH frame) for the
   *  profile popup avatar. Extracts frame 0 onto a tiny offscreen canvas. */
  paperdollPortraitSrc(): string {
    if (!this.hasPaperdollTexture()) return "";
    const mf = this.playersManifest;
    const fw = mf?.base?.frame_w || 32;
    const fh = mf?.base?.frame_h || 32;
    const src = this.textures.get("pd-base").getSourceImage() as HTMLImageElement | HTMLCanvasElement;
    const canvas = document.createElement("canvas");
    canvas.width = fw;
    canvas.height = fh;
    const ctx = canvas.getContext("2d");
    if (!ctx) return "";
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(src, 0, 0, fw, fh, 0, 0, fw, fh);
    return canvas.toDataURL();
  }

  private frameDtSec = 1 / 60; // real Phaser frame delta (set each update)
  // True while hp == 0 (server-authoritative): prediction frozen, overlay on.
  private selfDead = false;
  // --- facing + hover cursor: the HAND dot is the direction indicator ---
  private selfDir = "SOUTH";
  private lastMoveX = 0; // last nonzero input (hand points here while idle)
  private lastMoveY = 1;
  private faceVec: { x: number; y: number } | null = null; // smoothed facing
  private hoverSquare: Phaser.GameObjects.Rectangle | null = null;
  private phaserPointerBound = false;
  private aimCursor: { dx: number; dy: number } | null = null;
  // Mouse tile cache — written by refreshMouseTile() each frame from the
  // Phaser pointer (single source of truth). Clicks read this AFTER a
  // synchronous refresh, so they never see stale coordinates.
  private mouseTile: { x: number; y: number } | null = null;
  private mouseScreen: { x: number; y: number } | null = null; // normalized 0..1 cursor pos
  // --- resource nodes layer (trees/bushes/ore from the server) ---
  private resourceLayer: Phaser.GameObjects.Layer | null = null;
  /** Above-player bake (roofs/canopies) — drawn OVER actors. */
  private mapAbove: Phaser.GameObjects.Image | null = null;
  /** Per-cell y-sorted canopy membership (Ekonia parity): "x,y" -> tile
   *  bakes into the OVER-player canvas. Rebuilt on every buildWorld. */
  private ysortCells: Set<string> = new Set();
  // --- Ekonia FadeOccluderLayer parity (port of fade_occluder_layer.gd) ---
  // The OVER canvas is an opaque sheet; canopy pixels near the local player
  // fade toward FADE_MIN_ALPHA and restore to full opacity outside the
  // window — Ekonia's lerp(1, min_alpha, closeness) curve, evaluated per
  // PIXEL (not per tile) so the dim halo is a smooth radial gradient.
  private occluderCtx: CanvasRenderingContext2D | null = null;
  private occluderPix: Uint8ClampedArray | null = null; // opaque alpha snapshot
  private occluderTexKey = ""; // texture key of the OVER canvas (for GL re-upload)
  // Fade hot-path state: last composed position + reusable pixel buffer (no
  // per-frame ImageData allocation; stationary player = zero fade work).
  private lastFadeX = NaN;
  private lastFadeY = NaN;
  private fadeBuf: ImageData | null = null;
  private static readonly FADE_RADIUS_CELLS = 2; // Ekonia fade_radius_cells
  private static readonly FADE_MIN_ALPHA = 0.35; // Ekonia min_alpha
  /** Snapshot reconciliation only rewrites the prediction when the drift
   *  EXCEEDS this many tiles (echo lag converges to well under half of it). */
  private static readonly RECONCILE_DRIFT = 0.75;
  private resourceTiles = new Map<string, Phaser.GameObjects.Image>();
  // Meteor-ore sprites held invisible until the impact FX near them has
  // finished exploding (meteorFxBusyNear gate in updateResourceLayer).
  private pendingOreReveal = new Map<string, Phaser.GameObjects.Image>();
  private resourceSig = "";
  // Tile count at the last base-map bake: the rebake trigger (chop/regrow
  // changes the count; an unchanged carried payload must not re-bake).
  // --- night zombies (Kaetram-style mob, SEPARATE realtime web pack) ---
  // One entry per live zombie id: interpolated 20 Hz -> 60 fps like players.
  // The sprite is ONE frame cut from the local sheet copy (Kaetram zombie
  // 160x288 = 5 cols x 9 rows of 32px; mob rows: 0 = atk 5f, 1 = walk 4f,
  // 2 = idle 2f) — never the whole stretched sheet. The server sends the
  // authoritative anim ("walk"|"idle"|"atk") + facing per zombie at 20 Hz;
  // the client advances frames locally at 60 fps (walk 4f ~6fps shambling,
  // atk 5f = one 450ms lunge, idle 2f = slow breathe).
  private zombieLayer: Phaser.GameObjects.Layer | null = null;
  // Kaetram hitsplat state: floating damage numbers (spawnSplat/updateSplats).
  private splats = new Set<{ txt: Phaser.GameObjects.Text; t0: number; x: number; y: number }>();
  // Progressive block-break cracks: tileKey -> Minetest-style crack stage
  // sprite (10-stage sheet ui/fx/cracks.png, CC0) drawn OVER the block.
  // RENDER-ONLY: the SERVER owns the damage number (apply_break_block) and
  // its self-repair beat (blocks.decay_damage — 3.5 s idle then heal 1/s).
  // The client mirrors it through syncBlockDamage (snapshots, 20 Hz) and the
  // break echo, and reverse-heals SMOOTHLY between samples so nobody mining
  // a half-cracked block sees it pop back to pristine (user rule 13/09).
  private crackOverlays = new Map<string, Phaser.GameObjects.Image>();
  private crackTextureReady = false;
  // tileKey -> [displayed damage, needed] the overlay currently shows.
  private crackState = new Map<string, { dmg: number; needed: number; t0: number }>();
  // tileKey -> performance.now() of the last server sample for that tile
  // (fresh samples reset the local heal projection).
  // Drop entities ("linh khí"): id -> live sprite group. Server-authoritative
  // position/phase at 20 Hz; the client animates bob/glow/collect locally.
  // target: user_id the server's magnet is homing this drop toward (0 = none)
  // so OBSERVERS animate the flight toward the RIGHT player, not themselves.
  private dropLayer: Phaser.GameObjects.Layer | null = null;
  private drops = new Map<string, {
    container: Phaser.GameObjects.Container;
    glow: Phaser.GameObjects.Arc;
    itemId: string;
    icon: Phaser.GameObjects.Text | Phaser.GameObjects.Image;
    label: Phaser.GameObjects.Text | null;
    tx: number; ty: number; // latest server tile-pos (px)
    z: number;              // latest server height (px)
    phase: string;
    bornT: number;
    collectedT: number;
  target: number;
    bobSeed: number;
  }>();
  private zombies = new Map<string, {
    container: Phaser.GameObjects.Container;
    body: Phaser.GameObjects.Image | Phaser.GameObjects.Rectangle;
    label: Phaser.GameObjects.Text;
    hpBg: Phaser.GameObjects.Rectangle;
    hpFill: Phaser.GameObjects.Rectangle;
    buf: [number, number, number][];
    lastX: number; lastY: number;
    anim: string; // last server anim (walk|idle|atk)
    animT0: number; // performance.now() when the server anim last changed
    serverAnimT: number; // last server anim_t (seconds) — re-arm detector
    frame: number; // current local frame index inside the row
    frameT0: number; // performance.now() of the last local frame advance
    facing: string;
    dieT0: number; // performance.now() when the kill echo landed (0 = alive)
    hunter: boolean;
    kind: string; // mob kind: zombie|skeleton|spider|slime|bat|rat
  }>();
  // Per-kind sheet readiness: kind -> true once its asset arrived.
  private mobTextureReady = new Set<string>();
  private zombieFetchAsked = false;
  private lastZombieFrameT = 0;
  /** Latest measured websocket RTT (EMA, ms) from the net ping/pong loop.
   * Currently unused (reconciliation disabled) but kept for the future
   * anti-desync re-tightening; public so tsc does not flag it unread. */
  netRttMs = 0;

  private sessionStartT = 0;

  /** Wire the measured websocket RTT (from net ping/pong EMA) — telemetry
   * now (the threshold glide that consumed it was replaced by input-seq
   * replay); kept for diagnostics. Marks the session start. */
  setNetRtt(rttMs: number): void {
    if (this.sessionStartT === 0) this.sessionStartT = performance.now();
    this.netRttMs = rttMs;
  }
  /** Rolling stats of real snapshot arrival gaps (ms), updated in
   * applySnapshot. Telemetry only since the input-seq replay rewrite. */
  snapGapAvg = 50;
  snapGapMax = 150; // pessimistic init: assume a bursty first seconds
  // Progress bar PER NODE: one bar centred over the node's whole bbox
  // (a 2x2 tree gets a 64px-wide bar, not a sliver on the anchor tile).
  private progressBars = new Map<string, Phaser.GameObjects.Container>();
  private progressFills = new Map<string, Phaser.GameObjects.Rectangle>();
  private lastResProgress = "";
  // Tiles of FELLED nodes (server res_felled): walkable in the local
  // prediction — the tree/ore is gone until it regrows (mirrors the
  // server's Collision rule exactly).
  private felledTiles = new Set<string>();
  // Per-node hit counts from the latest action_result echo (tool-adjusted
  // needed total — more accurate than the snapshot's base value).
  private neededByAnchor = new Map<string, number>();
  // Raw latest snapshot progress: anchor -> [hits, needed, bbox] — kept for
  // node grouping + fall animation of nodes that vanish.
  private lastProgressRaw = new Map<string, [number, number, number[][]]>();
  private lastProgressBbox = new Map<string, number[][]>();
  // Falling-tree animation state is derived per sync (collectNodesFromTiles
  // + playFallAnimation) — no persistent bookkeeping needed.
  // Placed blocks (x,y -> id): solid for the local prediction too. Rebuilt
  // from EVERY snapshot (cheap Set) — a sig-guarded rebuild let a rejected
  // or clamped own-action leave a phantom solid/walkable tile until some
  // OTHER block happened to change (the "đặt rồi xóa rồi chạy xuyên" bug).
  private blockSet = new Set<string>();
  // Optimistic local collision for OUR OWN in-flight place/break actions:
  // tile key -> click timestamp. Solid (or walkable) immediately so a fast
  // run can't slip through; the action_result echo confirms/reverts exactly,
  // the every-snapshot blockSet rebuild re-applies them, and the TTL bounds
  // anything the echo missed (clamped target, lost frame).
  private optimisticBlocks = new Map<string, number>();
  private optimisticBreaks = new Map<string, number>();
  // --- build mode: selected block to place ---
  private selectedBlock = "stone";

  constructor() {
    super("world");
  }

  // No preload(): every texture is generated (shapes) or arrives later via
  // asset_data frames (tilesets fetched through the relay, license-safe).

  buildWorld(welcome: WelcomePayload, fetchAsset: (name: string) => void): void {
    // (Re)connection boundary: the server restarted its input sequence at 0,
    // so stale reconciliation state from the previous session must go — or
    // lastAckedSeq would shadow the new stream and the replay buffer would
    // never trim (inputs replayed forever = rubber-band freeze after
    // reconnecting without a page reload). Also reset the drift-recovery
    // timer AND snap the prediction to the authoritative spawn: a rejoin
    // after exit/re-enter previously carried the old predicted position in
    // selfX/selfY, and while waiting for the first snapshot the drift-glide
    // saw it as "desync" and dragged the avatar across the map — the
    // accumulated-latency bug that re-fired every ~10 min of relogging.
    this.inputLog.length = 0;
    // Server handoff: the session's own input_seq keeps counting across map
    // switches, so a mid-session welcome must NOT reset the ack base to -1
    // (snapshots would then echo a seq far above ours and every new input
    // looked pre-acked — reconcile off = permanent desync).
    this.lastAckedSeq = welcome.input_seq ?? -1;
    this.seqReplayActive = false;
    this.pendingDt = 0;
    this.driftIdleMs = 0;
    this.selfX = welcome.self.x;
    this.selfY = welcome.self.y;
    // MAP-SWITCH INPUT RESET: a welcome for a DIFFERENT map mid-walk (portal
    // fire / /cuahang) invalidates every held prediction — the old runtime's
    // inputLog and the stale held vector made the next snapshot reconcile
    // against a dead authority ("đến cổng xong game đơ"): reset the replay
    // buffer, clear the held vector, and drop any in-flight aim. The physical
    // key can stay held — the OS key repeat / input timer re-arms it against
    // the NEW map within one flush.
    if (this.welcome && this.welcome.map.id !== welcome.map.id) {
      this.inputLog.length = 0;
      this.inputVec.dx = 0;
      this.inputVec.dy = 0;
      this.inputVec.running = false;
      this.prevInputVec.dx = 0;
      this.prevInputVec.dy = 0;
      this.mobileAimTile = null;
    }
    this.pendingFetch = (id: string) => fetchAsset(`blocks/${id}.png`);
    // MAP SWITCH (walking through a portal / /khutraodoi): the previous map's
    // baked canvas must go BEFORE the new one is baked. bakeMapIfReady bails
    // while the new sheets are still in flight, and the old canvas used to
    // just stay — the trade lobby's grass/flowers covering the whole interior
    // while the REAL (interior) collision blocked the player ("thấy cỏ hoa
    // nhưng không đi xuyên được"). Hide + drop it now; it is rebuilt (and
    // re-shown) as soon as the destination sheets land.
    if (this.welcome && this.welcome.map.id !== welcome.map.id) {
      if (this.mapBake) {
        this.mapBake.destroy(); // drop the object too — no invisible leftovers
        this.mapBake = null;
      }
      // STALE MOB PURGE (user 25/09): the zombie layer survives the map
      // switch, so the previous map's mobs (in OLD-map tile coords) stayed
      // painted over the new map — and because mob ids are PER-RUNTIME
      // ("wzombie-1" exists in every world), an arriving snapshot with the
      // new map's own "wzombie-1" ADOPTED the stale container and teleported
      // it — reading as "cave mobs spawning outside the bigmap". Destroy
      // every mob sprite + clear the map so the new world starts clean.
      if (this.zombieLayer) {
        this.zombieLayer.removeAll(true);
      }
      this.zombies.clear();
      if (this.mapAbove) {
        this.mapAbove.destroy(); // old roof/canopy must not cover the new map
        this.mapAbove = null;
      }
      this.occluderCtx = null;
      this.occluderPix = null;
      if (this.textures.exists("map-bake")) this.textures.remove("map-bake");
      if (this.textures.exists("map-above")) this.textures.remove("map-above");
      // Drop the old map's resource sprites NOW (they render above the baked
      // ground). The sig above is keyed by map id as well, so the rebuild
      // cannot be skipped later either.
      this.resourceSig = "";
      if (this.resourceLayer) this.resourceLayer.removeAll(true);
      this.resourceTiles.clear();
    }
    this.assetFetch = fetchAsset;
    this.welcome = welcome;
    // GPU day/night tint (daynight_phaser.ts): attach once per scene, before
    // any snapshot can try to update it. Idempotent on re-welcome.
    if (perf.daynight) dayNightPhaser.attach(this);
    // Paperdoll: stash manifest, fetch base + every mapped weapon sheet
    // once through the same relay pipe as blocks/mobs (license-safe).
    if (welcome.players_manifest && !this.paperdollAsked) {
      this.playersManifest = welcome.players_manifest;
      this.paperdollAsked = true;
      fetchAsset("players/base.png");
      for (const stem of new Set(Object.values(WEAPON_SHEET_BY_ITEM))) {
        fetchAsset(`players/weapon/${stem}.png`);
      }
      // Hand icons: request the Kaetram icon PNG for every known item so
      // hands show pixel art instead of font-dependent emoji glyphs.
      for (const id of ICON_ITEM_IDS) {
        fetchAsset(`icons/${id}.png`);
      }
    }
    // Server item emojis FIRST: hand icons (self + remote) resolve through
    // this map, so it must be fresh before any setText call below.
    this.itemEmojis = welcome.item_emojis ?? {};
    // Rebuild existing hand icons with the fresh map (same ids, new glyphs).
    this.setSelfHeld(this.selfHeld);
    for (const rp of this.players.values()) {
      rp.toolIcon = this.applyHandIcon(rp.toolIcon, rp.held);
    }
    const map = welcome.map;

    // --- tilesets: request each PNG through the relay (license-safe) ---
    for (const ts of map.tilesets) {
      if (!ts.image) continue;
      const key = this.tileTextures.get(ts.image)
        ?? ts.image.replace(/\.png$/i, "");
      this.tileTextures.set(ts.image, key);
      // Re-request whenever the TEXTURE is missing — not merely when the
      // name is new. A sticky tileTextures entry (name seen earlier) used to
      // make this a no-op, so a map whose sheet had never actually decoded
      // baked nothing and the PREVIOUS map stayed on screen (the interior
      // showing the trade lobby's grass/flowers).
      // Server asset lane: "tilesets/<name>.png" resolves in assets/tilesets
      // (the server also falls back to basename matching).
      if (!(this.textures && this.textures.exists(key))) {
        fetchAsset(`tilesets/${ts.image}`);
      }
    }
    // Request every DISTINCT block face once (blocks payload may repeat ids).
    for (const id of new Set(welcome.blocks.map(([, , bid]) => bid))) {
      if (!this.blockTextures.has(id)) fetchAsset(`blocks/${id}.png`);
    }
    // Night zombie sprite sheet (Kaetram 160x288, 5 cols x 9 rows of 32px):
    // requested once per session through the same relay pipe (license-safe —
    // the PNG stays on the bot, only this client receives the bytes).
    if (!this.zombieFetchAsked) {
      // Request every night-mob sheet once per session through the same
      // relay pipe (license-safe: PNGs stay on the bot).
      this.zombieFetchAsked = true;
      for (const kind of Object.keys(MOB_SHEETS)) {
        fetchAsset(`mobs/${kind}.png`);
      }
    }
    // PER-MAP TILE SIZE **BEFORE ANY BUILD** (user 25/09 — "ra bigmap cây cối
    // lệch tùm lum"): bakeMapIfReady/updateResourceLayer position every
    // resource sprite at x*tilePx. tilePx was assigned BELOW (line ~786), so
    // the first build after a tile-size CHANGE (cave 16px -> bigmap 32px)
    // built the whole layer with the STALE tile — trees at half/big-double
    // positions — and the sig guard then saw an unchanged resource list and
    // never rebuilt. Assign tilePx up front; the later assignment is kept as
    // a harmless re-write of the same value.
    this.tilePx = welcome.map.tile_width || BASE_TILE;
    this.buildBlocks(welcome.blocks);
    this.bakeMapIfReady();
    // Cave ambience (darkness + glowing mushrooms) — independent canvases,
    // safe to (re)build right after the map bake.
    if (perf.cave) this.setupCaveAmbience(welcome);

    // --- physics-less world: positions are authoritative from the server ---
    this.cameras.main.setBounds(0, 0, map.width * map.tile_width, map.height * map.tile_height);
    this.cameras.main.setBackgroundColor("#20303c");
    // Zoom 2.0 for EVERY map and EVERY mode (mobile zoom removed per user:
    // the camera is always exactly this). Actor sprites (paperdoll 64px,
    // mobs, drops) are authored in 32px world space — at 2.0 a 16px tile
    // shows at 32 screen px and ALL proportions match the classic maps.
    this.cameras.main.setZoom(2.0);

    this.spawnSelf(welcome);
    for (const p of welcome.players) this.upsertPlayer(p);

    // Client-side prediction state: start from the authoritative spawn.
    this.selfX = welcome.self.x;
    this.selfY = welcome.self.y;
    this.selfServerPos = { x: welcome.self.x, y: welcome.self.y };
    this.lastServerRecv = performance.now();
    this.collision = welcome.map.collision ?? [];
    // Per-map tile size: ekonia maps ship 16px tiles while the classic maps
    // are 32px. Every grid->world conversion below must use THIS value.
    this.tilePx = welcome.map.tile_width || BASE_TILE;
    // Paperdoll bodies are authored against 32px tiles — rescale for 16px
    // Ekonia maps (cave/forest) so the body matches the hover-square size.
    this.selfDoll?.setTileScale(this.tilePx);
    for (const rp of this.players.values()) rp.doll?.setTileScale(this.tilePx);
    // Sub-tile masks: sparse {y:{x:mask}} -> flat "x,y" map (server parity).
    this.tileMasks.clear();
    const tm = welcome.map.tile_masks;
    if (tm && tm.tiles) {
      for (const yKey of Object.keys(tm.tiles)) {
        const y = parseInt(yKey, 10);
        const row = tm.tiles[yKey];
        for (const xKey of Object.keys(row)) {
          this.tileMasks.set(`${parseInt(xKey, 10)},${y}`, row[xKey]);
        }
      }
    }
    this.selfDir = welcome.self.dir || "SOUTH";
    if (!this.faceVec) {
      const v0 = DIR_VECTORS[this.selfDir] ?? DIR_VECTORS.SOUTH;
      this.faceVec = { x: v0[0], y: v0[1] };
    }

    // Hover square only — the hand dot is the facing indicator now.
    this.ensureHoverSquare();
    // Interactive NPCs of this map: emoji token + name label. Also served
    // as the E-interact targets on the web (mirrors the Discord "NPC ở
    // gần" dialogue flow).
    this.spawnNpcs(welcome.npcs ?? []);
    // Second, independent cursor source: Phaser's own canvas listeners.
    // If the DOM mousemove chain ever misses (listener on the wrong canvas,
    // event swallowed), Phaser still reports every move/click here — and
    // vice versa. Both write the same normalized cursor position.
    if (!this.phaserPointerBound) {
      this.phaserPointerBound = true;
      const grab = (p: Phaser.Input.Pointer): void => {
        const dw = this.scale.displaySize.width || 1;
        const dh = this.scale.displaySize.height || 1;
        this.mouseScreen = { x: p.x / dw, y: p.y / dh };
      };
      this.input.on("pointermove", grab);
      this.input.on("pointerdown", grab);
    }
    // Resource tiles from the welcome payload (trees etc.).
    this.updateResourceLayer(welcome.resources);
    this.felledTiles = new Set((welcome.res_felled ?? []).map(([x, y]) => `${x},${y}`));
    // Any drops already lying around (welcome carries the same payload).
    this.syncDrops(welcome.drops ?? []);

    // Camera follows the SELF MARKER every frame — the marker itself is
    // driven by prediction in update(), so camera lag = marker lag.
    if (this.selfMarker) {
      this.cameras.main.startFollow(this.selfMarker, true, 0.15, 0.15);
    }
  }

  /** Receive one seq'd input (mirrors what net just sent to the server).
   * Appended to the replay buffer with the wall-clock dt accumulated since
   * the previous flushed input — exactly the time slice the server will
   * integrate this vector for. */
  noteSeqInput(seq: number, dx: number, dy: number, running: boolean): void {
    const dt = Math.max(0.001, this.pendingDt);
    this.pendingDt = 0;
    this.inputLog.push({ seq, dx, dy, running, dt });
    // ~2s of buffered inputs at 20 Hz flushes is plenty: anything older is
    // guaranteed acked (and if not — a lost frame replays at most once).
    while (this.inputLog.length > 40) this.inputLog.shift();
  }

  /** Called 20 Hz from main.ts: store the current input vector. */
  setLocalInput(dx: number, dy: number, running: boolean): void {
    this.prevInputVec.dx = this.inputVec.dx;
    this.prevInputVec.dy = this.inputVec.dy;
    this.prevInputVec.running = this.inputVec.running;
    this.inputVec.dx = dx;
    this.inputVec.dy = dy;
    this.inputVec.running = running;
    // DIRECTION-REVERSAL GUARD (user 23/09: "chạy lên rồi chạy xuống liền
    // kề nhau — cảm giác bị dịch chuyển 1 khoảng thay vì quay mặt lại"):
    // a 180° flip with the previous axis still held arrives at the server
    // as ~1 tick (≤50 ms) of the OLD direction. That stale half-step
    // integrates into a 1-tile sliver ABOVE us; the snapshot replay then
    // re-integrates it and the avatar LEAPS that tile instantly. Mirror the
    // server's own leash: drop ≤1-tile slivers against the held direction.
    // The body may still be ≤1 tile above us (the stale half-step we just
    // dropped) — applying the NEW axis latches us onto the live server
    // command immediately (see applySnapshot), instead of teleporting back.
    if (
      this.prevInputVec.dx !== 0 && dx !== 0 && Math.sign(dx) !== Math.sign(this.prevInputVec.dx)
    ) {
      const d = Math.hypot(this.selfX - this.selfServerPos.x, 0);
      const behind = (this.selfServerPos.x - this.selfX) * dx > 0.02;
      if (d > 0.02 && d <= 1.2 && behind) {
        // FULL latch, same rationale as the vertical branch above.
        this.selfX = this.selfServerPos.x;
      }
    }
    if (
      this.prevInputVec.dy !== 0 && dy !== 0 && Math.sign(dy) !== Math.sign(this.prevInputVec.dy)
    ) {
      const d = Math.hypot(0, this.selfY - this.selfServerPos.y);
      const behind = (this.selfServerPos.y - this.selfY) * dy > 0.02;
      if (d > 0.02 && d <= 1.2 && behind) {
        // FULL latch (was 0.6 partial — the leftover residue kept replaying
        // and snapped later, the "spam lên xuống xong quay ra chỗ khác là
        // bị dịch chuyển" report): we KNOW the residual is the stale
        // half-step (behind + ≤1.2 tiles), so drop it completely and stand
        // exactly on the server's live command position.
        this.selfY = this.selfServerPos.y;
      }
    }
    // Keep the 8-way facing label in sync with the raw input (used by
    // getSelfDir for actions); rendering blends the vector separately.
    if (dx !== 0 || dy !== 0) {
      this.selfDir = this.dominantDir(dx, dy);
    }
  }

  /** Screen (0..1) -> world tile, through the camera (zoom-safe). */
  screenToTile(sx: number, sy: number): { x: number; y: number } {
    // getWorldPoint handles zoom + scroll + camera bounds correctly;
    // manual scroll math drifted under zoom != 1 (the reported offset).
    const p = this.cameras.main.getWorldPoint(
      sx * this.cameras.main.width,
      sy * this.cameras.main.height,
    );
    // Divide by THIS map's tile size: the old hard-coded /32 clicked one
    // tile off on 16px maps (every ekonia map).
    return { x: Math.floor(p.x / this.tilePx), y: Math.floor(p.y / this.tilePx) };
  }

  /** Offset of a clicked tile relative to the player (for place). */
  offsetFromSelf(tile: { x: number; y: number }): { dx: number; dy: number } {
    // IMPORTANT: offsets are relative to the SERVER's integer tile
    // (selfServerPos), not the predicted float position — the server
    // computes tx = player.x + dx with its own int tile, so using the
    // drifted prediction landed blocks on the wrong tile.
    return {
      dx: Math.round(tile.x - this.selfServerPos.x),
      dy: Math.round(tile.y - this.selfServerPos.y),
    };
  }

  // A tileset PNG arrived via the relay: bake the map (only redraws tiles,
  // never rebuilds players/physics — no duplication, no camera reset).
  onTilesetLoaded(image: string): void {
    if (!this.welcome) return;
    if (!this.welcome.map.tilesets.some((t) => t.image === image)) return;
    this.loadedTilesets.add(image);
    this.bakeMapIfReady();
    // Resource layer was skipped at build time (no textures yet) — rebuild
    // it now and clear the sig cache so snapshots can refresh it again.
    this.resourceSig = "";
    this.updateResourceLayer(this.welcome.resources);
  }

  /** GID -> tileset: the entry whose range [firstgid, firstgid + tilecount)
   *  contains the gid. Must NOT be "largest firstgid <= gid": lobbytrade
   *  registers [Base]BaseChip_pipo.png at TWO firstgids (577 + 5337), so the
   *  largest-firstgid rule resolved ground gid 577 against 5337 -> negative
   *  offset -> every ground tile skipped (invisible floor). The range rule
   *  is the correct Tiled semantics; fallback to largest-firstgid when no
   *  range covers the gid (malformed maps). */
  private tilesetForGid(
    map: WelcomePayload["map"], gid: number,
  ): WelcomePayload["map"]["tilesets"][number] | null {
    let best: WelcomePayload["map"]["tilesets"][number] | null = null;
    let rangeHit: WelcomePayload["map"]["tilesets"][number] | null = null;
    for (const t of map.tilesets) {
      if (!t.image || t.firstgid > gid) continue;
      // Prefer the server's exact tilecount; fall back to a generous guess
      // (512 rows) only when it's missing (stale cached welcome).
      const count = t.tilecount ?? t.columns * 512;
      if (gid < t.firstgid + count) {
        // Range hit: prefer the LAST such entry (later duplicates win —
        // Tiled re-exports append, and the later registration is authoritative).
        rangeHit = t;
      }
      if (best === null || t.firstgid > best.firstgid) best = t;
    }
    return rangeHit ?? best;
  }

  // ---- cave ambience (darkness + glowing mushrooms) ----
  /** Full-map black sheet at the cave's ambient darkness; punch soft light
   *  wells through destination-out (radial gradients). depth 40 = ABOVE the
   *  map bake (−10), actors (20) AND the OVER canvas (30): the cave's
   *  y-sorted props/pebbles/walls live in the OVER canvas — a darkness
   *  sheet below 30 left them at full brightness while the ground dimmed
   *  ("đá cuội lệch màu so với đất"). Everything darkens together; the
   *  light wells reveal the scene around the mushrooms. */
  private caveDark: Phaser.GameObjects.Image | null = null;
  /** Static warm glow halos behind the mushrooms (depth 18, additive). */
  private caveGlows: Phaser.GameObjects.Image[] = [];

  /** Build (or rebuild) the cave lighting layers from welcome.map.cave_ambience.
   *  No-op on non-cave maps (destroys any leftovers from a previous map). */
  private setupCaveAmbience(welcome: WelcomePayload): void {
    const amb = welcome.map.cave_ambience;
    if (!amb) {
      if (this.caveDark) {
        this.caveDark.destroy();
        this.caveDark = null;
      }
      for (const g of this.caveGlows) g.destroy();
      this.caveGlows = [];
      return;
    }
    const tw = welcome.map.tile_width || BASE_TILE;
    const W = welcome.map.width * tw;
    const H = welcome.map.height * tw;
    // --- darkness sheet: opaque black, light wells punched through ---
    const dark = document.createElement("canvas");
    dark.width = W;
    dark.height = H;
    const dctx = dark.getContext("2d");
    if (!dctx) return;
    dctx.fillStyle = `rgba(2,2,8,${Math.max(0, Math.min(0.97, amb.darkness))})`;
    dctx.fillRect(0, 0, W, H);
    dctx.globalCompositeOperation = "destination-out";
    for (const [lx, ly, lr] of amb.lights) {
      const r = lr * tw;
      const cx = lx * tw;
      const cy = ly * tw;
      const grad = dctx.createRadialGradient(cx, cy, 0, cx, cy, r);
      // Fully clear at the source, easing to zero clear at the rim.
      grad.addColorStop(0, "rgba(0,0,0,0.95)");
      grad.addColorStop(0.55, "rgba(0,0,0,0.45)");
      grad.addColorStop(1, "rgba(0,0,0,0)");
      dctx.fillStyle = grad;
      dctx.beginPath();
      dctx.arc(cx, cy, r, 0, Math.PI * 2);
      dctx.fill();
    }
    dctx.globalCompositeOperation = "source-over";
    const dkey = "cave-dark";
    if (this.textures.exists(dkey)) this.textures.remove(dkey);
    this.textures.addCanvas(dkey, dark);
    if (this.caveDark) {
      this.caveDark.setTexture(dkey);
      this.caveDark.setVisible(true);
    } else {
      this.caveDark = this.add.image(0, 0, dkey).setOrigin(0, 0).setDepth(40);
    }
    // --- warm glow halos (additive, behind actors for a soft bloom) ---
    for (const g of this.caveGlows) g.destroy();
    this.caveGlows = [];
    const glow = document.createElement("canvas");
    const GR = 64;
    glow.width = GR * 2;
    glow.height = GR * 2;
    const gctx = glow.getContext("2d");
    if (!gctx) return;
    const gg = gctx.createRadialGradient(GR, GR, 0, GR, GR, GR);
    gg.addColorStop(0, "rgba(255,190,120,0.34)");
    gg.addColorStop(0.5, "rgba(255,150,80,0.16)");
    gg.addColorStop(1, "rgba(255,120,60,0)");
    gctx.fillStyle = gg;
    gctx.fillRect(0, 0, GR * 2, GR * 2);
    const gkey = "cave-glow";
    if (this.textures.exists(gkey)) this.textures.remove(gkey);
    this.textures.addCanvas(gkey, glow);
    for (const [lx, ly, lr] of amb.lights) {
      const img = this.add.image(
        lx * tw, ly * tw, gkey,
      ).setOrigin(0.5, 0.5).setDepth(18).setBlendMode(Phaser.BlendModes.ADD);
      img.setScale((lr * tw * 2.1) / (GR * 2));
      this.caveGlows.push(img);
    }
    // Camera background must read as cave dark OUTSIDE the art, not sea-blue.
    this.cameras.main.setBackgroundColor("#07070c");
  }

  private bakeMapIfReady(): void {
    const welcome = this.welcome;
    if (!welcome) return;
    const map = welcome.map;
    const tw = map.tile_width;
    const th = map.tile_height;
    // DEDUPE GUARD (PC triple-load stutter): a repeated welcome for the SAME
    // map with the SAME usable sheet set is a no-op — skip the whole bake (a
    // full-map canvas repaint + the 88 MB occluder getImageData). Portal
    // switches / genuine sheet changes still bake because the sig changes.
    // The sig keys on the USABLE (decoded) sheets, not the requested ones:
    // an early partial bake (only some sheets decoded yet) must NOT pin the
    // sig, or the late sheet would arrive and be skipped forever (holes).
    const usableImgs = map.tilesets
      .filter((t) => t.image && this.textures.exists(this.tileTextures.get(t.image) ?? ""))
      .map((t) => t.image);
    const sig = `${map.id}|${usableImgs.length}|${usableImgs.join(",")}`;
    const alreadyUsable = this.mapBake !== null && this.bakeSig === sig;
    if (alreadyUsable) return;
    this.bakeSig = sig;
    // Require at least one tileset texture; bake with what we have.
    const usable = map.tilesets.filter(
      (t) => t.image && this.textures.exists(this.tileTextures.get(t.image) ?? ""),
    );
    if (usable.length === 0) {
      // Sheets not decoded yet. buildWorld already hid/removed the previous
      // map's canvas, so nothing wrong is on screen — but the bake MUST run
      // once they land. Re-ask for the missing sheets (rate-limited) instead
      // of giving up silently: the load callback (onTilesetLoaded) can be
      // missed when the same sheet was requested earlier and the client's
      // asset cache answered it, which left this map permanently un-baked.
      const nowKick = performance.now();
      if (nowKick - this.lastTilesetKick > 1000) {
        this.lastTilesetKick = nowKick;
        for (const t of map.tilesets) {
          if (t.image && !this.textures.exists(this.tileTextures.get(t.image) ?? "")) {
            this.assetFetch?.(`tilesets/${t.image}`);
          }
        }
      }
      return;
    }

    // Resource layers ("cây", "vật phẩm ko liên quan", ...) are drawn as a
    // separate dynamic layer (choppable), so the base bake must EXCLUDE the
    // whole LAYER (by folded name, mirroring the server's
    // RESOURCE_LAYER_NAMES) — NOT per-coordinate: excluding coordinates also
    // drops the ground/grass tiles UNDER a tree, leaving a hole when the
    // tree is felled.
    const RESOURCE_LAYERS = new Set([
      "cay", "tree", "trees", "resources",
      "vat pham ko lien quan", "ore", "ores", "mine",
      // mineable rocks (tảng đá nhỏ / tảng đá lớn) — choppable nodes, must
      // NOT bake into the base or they'd stay visible after being felled
      "tang da nho", "tang da lon",
      // field forage (nấm/cỏ/hoa) — same rule: baked copies would stay
      // visible forever after the node is felled (the "đập rồi vẫn còn"
      // bug)
      "nam nau", "nam tim", "co", "hoa trang", "hoa xanh", "hoa tim", "hoa vang",
    ]);
    const foldName = (s: string): string =>
      s.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
        .replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();

    const canvas = document.createElement("canvas");
    canvas.width = map.width * tw;
    canvas.height = map.height * th;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    // Resource-layer tiles must bake ONLY when the server lists them as a
    // visible resource tile (welcome.resources). That list ALREADY excludes
    // chopped nodes' tiles (visible_tiles drops chopped anchors), so a felled
    // tree's tiles never enter the base bake (the "đập rồi vẫn dính đất" bug:
    // the old isNodeTile check keyed off res_progress, which only covers
    // IN-PROGRESS nodes — after felling the bbox vanished and the baked copy
    // reappeared). Tiles on resource layers WITHOUT a server node (lobbytrade's
    // Pipoya trees — no registered node, not in the payload) still bake, so
    // they never become the "block vô hình" invisible wall again.
    // Tiles belonging to LIVE server nodes are drawn as sprites in
    // updateResourceLayer — they must NEVER enter the bake, or the baked
    // copy stays visible after felling ("đập rồi vẫn còn") and each felling
    // needs an expensive canvas-clear/rebake (the chop lag).
    const resourceTileSet = new Set(
      (welcome.resources ?? []).map(([x, y]) => `${x},${y}`),
    );
    // Above-player layers (roofs/canopies) bake to a SEPARATE canvas drawn
    // OVER the actors (depth 30) — Ekonia "Roof"/Kaetram "02_high" parity:
    // the player walks under the art and is visually covered. The server may
    // also send per-cell ysort membership (Godot y_sort_enabled parity):
    // those exact cells join the OVER canvas too — the canopy covers the
    // player walking BEHIND the tree while the trunk row still renders
    // underfoot ("layer lá cây đè lên player", y hệt game gốc).
    this.ysortCells = new Set(
      (map.ysort_cells ?? []).map((v, i, arr) =>
        i % 2 === 0 ? `${v},${arr[i + 1]}` : "",
      ).filter((s) => s !== ""),
    );
    const aboveSet = new Set((map.above_layers ?? []).map((n) => foldName(n)));
    const aboveCanvas = aboveSet.size || this.ysortCells.size
      ? document.createElement("canvas")
      : null;
    if (aboveCanvas) {
      aboveCanvas.width = map.width * tw;
      aboveCanvas.height = map.height * th;
    }
    const aboveCtx = aboveCanvas ? aboveCanvas.getContext("2d") : null;
    for (const layer of map.layers) {
      // Resource layers bake only tiles WITHOUT a live server node (those
      // render as choppable sprites instead). Node-less tiles (lobbytrade's
      // Pipoya decor trees) MUST bake, or they become invisible walls.
      const isResourceLayer = RESOURCE_LAYERS.has(foldName(layer.name || ""));
      // Above-player tiles go to the OVER canvas (never the base bake):
      // whole roof/canopy layers, or single y-sorted cells (Ekonia trees).
      const isAboveLayer = aboveSet.has(foldName(layer.name || ""));
      for (let y = 0; y < map.height; y++) {
        const row = layer.data[y];
        if (!row) continue;
        for (let x = 0; x < map.width; x++) {
          const gid = row[x];
          if (!gid) continue;
          if (isResourceLayer && resourceTileSet.has(`${x},${y}`)) continue;
          // GID -> tileset: the entry with the LARGEST firstgid <= gid.
          // The old range test (gid < firstgid + columns*1000) was a bogus
          // heuristic that broke twice on lobbytrade: the FIRST tileset
          // matched every gid (its 32000-tile synthetic range swallowed the
          // whole map -> wrong crops), and the BaseChip sheet registered at
          // TWO firstgids (577 + 5337) so a range test can never pick the
          // right one. Largest-firstgid-below is the correct Tiled rule.
          const ts = this.tilesetForGid(map, gid);
          if (!ts || ts.image === null || ts.image === undefined) continue;
          const texKey = this.tileTextures.get(ts.image);
          if (!texKey || !this.textures.exists(texKey)) continue;
          const src = this.textures.get(texKey).getSourceImage() as HTMLImageElement;
          if (!src || !src.width) continue;
          const local = gid - ts.firstgid;
          const col = local % ts.columns;
          const rowIdx = Math.floor(local / ts.columns);
          const tileW = ts.tilewidth ?? tw;
          const tileH = th;
          if (col * tileW >= src.width) continue;
          const dest =
            isAboveLayer || this.ysortCells.has(`${x},${y}`) ? aboveCtx : ctx;
          if (!dest) continue;
          dest.drawImage(
            src, col * tileW, rowIdx * tileH, tileW, tileH,
            x * tw, y * th, tw, th,
          );
        }
      }
    }
    // Node membership changed: force the sprite layer to rebuild on the
    // next snapshot (its sig-guard would otherwise skip the refresh).
    this.resourceSig = "";
    this.updateResourceLayer(welcome.resources ?? []);
    // REGISTER the canvas as a Phaser texture — without this the image
    // below references a nonexistent "map-bake" texture and the whole
    // ground stays invisible (trees still showed because they come from
    // the server resource list, a separate path — which masked this).
    const key = "map-bake";
    if (this.textures.exists(key)) this.textures.remove(key);
    this.textures.addCanvas(key, canvas);
    if (this.mapBake) {
      this.mapBake.setTexture(key);
      this.mapBake.setVisible(true); // re-show after a map switch hid it
    } else {
      this.mapBake = this.add.image(0, 0, key).setOrigin(0, 0).setDepth(-10);
    }
    // Above-player bake: register + draw OVER actors (depth 30 > player 20).
    const akey = "map-above";
    if (aboveCanvas && aboveCtx) {
      if (this.textures.exists(akey)) this.textures.remove(akey);
      this.textures.addCanvas(akey, aboveCanvas);
      // Ekonia FadeOccluderLayer: snapshot the opaque OVER sheet so cells
      // inside the player's fade window can dip alpha and RESTORE exactly
      // when the player walks away (no bake-redraw, no sticky dimming).
      this.occluderCtx = aboveCtx;
      this.occluderTexKey = akey;
      this.lastFadeX = NaN; // force a fresh fade composition on the new map
      this.lastFadeY = NaN;
      this.fadeBuf = null;
      try {
        this.occluderPix = aboveCtx.getImageData(0, 0, aboveCanvas.width, aboveCanvas.height).data;
      } catch {
        this.occluderPix = null; // tainted canvas — degrade to opaque sheet
      }
      if (this.mapAbove) {
        this.mapAbove.setTexture(akey);
        this.mapAbove.setVisible(true);
      } else {
        this.mapAbove = this.add.image(0, 0, akey).setOrigin(0, 0).setDepth(30);
      }
    } else if (this.mapAbove) {
      this.mapAbove.setVisible(false);
    }
  }
  private buildBlocks(blocks: [number, number, string][]): void {
    if (!this.blockLayer) this.blockLayer = this.add.layer();
    const seen = new Set<string>();
    for (const [x, y, id] of blocks) {
      const tileKey = `${x},${y}`;
      seen.add(tileKey);
      const texKey = `block-${id}`;
      const hasTex = this.blockTextures.has(id) && this.textures.exists(texKey);
      let go = this.blockSprites.get(tileKey);
      // Block FACE art is authored at 32px (assets/blocks/*.png). On a 16px
      // map (Ekonia cave) a full-size sprite covers a 2x2 tile area — the
      // "đặt block trong cave trông kì" bug. Display-scale it to ONE tile
      // cell of THIS map (bigmap 32px maps keep the authored size).
      const blockScale = this.tilePx / 32;
      if (!go) {
        // New block: sprite (or placeholder rectangle until the face
        // texture arrives — see onBlockTexture).
        if (hasTex) {
          go = this.add.image(x * this.tilePx + this.tilePx / 2, y * this.tilePx + this.tilePx / 2, texKey)
            .setScale(blockScale);
        } else {
          // Placeholder matches the map's tile: 30px on 32px maps, 30px
          // (2x2 cells) on 16px maps — same world size everywhere.
          const ph = this.tilePx >= 32 ? 30 : this.tilePx * 2 - 2;
          const r = this.add.rectangle(x * this.tilePx + this.tilePx / 2, y * this.tilePx + this.tilePx / 2, ph, ph, 0x6b5a3e);
          r.setStrokeStyle(2, 0x8a7550);
          go = r;
        }
        this.blockLayer.add(go);
        this.blockSprites.set(tileKey, go);
      } else if (hasTex && go instanceof Phaser.GameObjects.Rectangle) {
        // Texture arrived: upgrade the placeholder in place (no churn).
        const img = this.add.image(x * this.tilePx + this.tilePx / 2, y * this.tilePx + this.tilePx / 2, texKey)
          .setScale(blockScale);
        this.blockLayer.add(img);
        this.blockSprites.set(tileKey, img);
        go.destroy();
      }
    }
    for (const [tileKey, go] of this.blockSprites) {
      if (!seen.has(tileKey)) {
        go.destroy();
        this.blockSprites.delete(tileKey);
      }
    }
    // A removed block must not keep a ghost crack floating over the ground.
    for (const tileKey of [...this.crackOverlays.keys()]) {
      if (!seen.has(tileKey)) {
        this.crackOverlays.get(tileKey)?.destroy();
        this.crackOverlays.delete(tileKey);
        this.crackState.delete(tileKey);
      }
    }
  }

  /**
   * SERVER SAMPLE (20 Hz + break echo): authoritative damage for ONE block.
   *
   * The server drains damage after a 3.5 s idle (its self-repair beat); the
   * client projects that drain FORWARD between samples — dmg decreases at
   * BLOCK_HEAL_RATE/s until the next sample corrects it — so the crack
   * "rewinds" smoothly instead of jumping (user rule 13/09).
   */
  setBlockCrack(tx: number | null, ty: number | null, damage: number, needed: number): void {
    if (tx === null || ty === null) return;
    const key = `${tx},${ty}`;
    const dmg = Math.max(0, damage);
    const need = Math.max(1, needed || this.crackState.get(key)?.needed || 1);
    // GROWING damage (a new hit landed): show it immediately.
    const prev = this.crackState.get(key);
    if (prev && dmg > prev.dmg) {
      this.crackState.set(key, { dmg, needed: need, t0: performance.now() });
      this.renderCrack(key);
      return;
    }
    if (dmg <= 0 || !this.blockSet.has(key)) {
      this.clearBlockCrack(key);
      return;
    }
    // Same-or-lower damage: a routine server sample — keep the displayed
    // value monotonic (a late/reordered frame must not rewind the crack).
    const shown = prev ? Math.min(prev.dmg, dmg) : dmg;
    this.crackState.set(key, { dmg: shown, needed: need, t0: performance.now() });
    this.renderCrack(key);
  }

  /**
   * FULL SYNC from a snapshot: "x,y" -> [damage, needed]. Removes overlays
   * for tiles the server no longer reports (fully healed or block gone).
   * Lowered damage is applied by REWINDING the overlay from its last shown
   * value over the real elapsed time — the gradual self-repair animation.
   */
  syncBlockDamage(
    damage: Record<string, [number, number]> | undefined,
    blockTiles: Set<string>,
  ): void {
    const now = performance.now();
    const reported = new Set<string>();
    if (damage) {
      for (const [key, entry] of Object.entries(damage)) {
        if (!Array.isArray(entry) || entry.length < 2) continue;
        reported.add(key);
        const [dmgRaw, neededRaw] = entry;
        const dmg = Math.max(0, Number(dmgRaw) || 0);
        const need = Math.max(1, Number(neededRaw) || 1);
        const prev = this.crackState.get(key);
        if (prev && dmg < prev.dmg - 0.01) {
          // Server healed this block while we watched: keep showing the OLD
          // (deeper) crack and let the per-frame projection drain it at
          // BLOCK_HEAL_RATE/s — the smooth "tua ngược". The sample time is
          // NOT reset here, so the drain continues from the shown value.
          this.crackState.set(key, { dmg: prev.dmg, needed: need, t0: prev.t0 });
        } else {
          this.crackState.set(key, { dmg, needed: need, t0: now });
        }
      }
    }
    // Fully healed / broken tiles: drop the overlay (a kept block keeps
    // nothing; a broken block must not keep a ghost crack).
    for (const key of [...this.crackState.keys()]) {
      if (!reported.has(key) || !blockTiles.has(key)) {
        this.clearBlockCrack(key);
      }
    }
    // Paint all reported tiles (renderCrack skips unchanged ones).
    for (const key of reported) {
      this.renderCrack(key);
    }
  }

  /** Drop one crack overlay + its state. */
  private clearBlockCrack(key: string): void {
    this.crackState.delete(key);
    const img = this.crackOverlays.get(key);
    if (img) {
      img.destroy();
      this.crackOverlays.delete(key);
    }
  }

  /** Paint the crack overlay for one tile from its CURRENT projected state. */
  private renderCrack(key: string): void {
    const st = this.crackState.get(key);
    if (!st) return;
    // Server heals at 1 dmg/s after 3.5 s idle; project that drain forward
    // from the sample time so the 20 Hz updates never show a sawtooth.
    const healed = Math.max(0, (performance.now() - st.t0) / 1000) * BLOCK_HEAL_RATE;
    const ratio = st.needed > 0 ? Math.max(0, Math.min(1, (st.dmg - healed) / st.needed)) : 0;
    const [txStr, tyStr] = key.split(",");
    const tx = Number(txStr);
    const ty = Number(tyStr);
    if (ratio <= 0 || ratio >= 1 || !this.blockSet.has(key) || !Number.isFinite(tx) || !Number.isFinite(ty)) {
      this.clearBlockCrack(key);
      return;
    }
    if (!this.crackTextureReady) {
      // Load the 10-stage crack sheet once from the static bundle (no
      // server round-trip). Until it decodes, cracks simply don't show —
      // the next render pass will draw them.
      if (!this.textures.exists("fx-cracks")) {
        // SPRITESHEET (not image): the file is a 10-frame 32x32 strip —
        // loading it as a plain image crams the whole chain into one frame
        // and setFrame draws the entire strip squashed onto the block.
        this.load.spritesheet("fx-cracks", "ui/fx/cracks.png", {
          frameWidth: 32,
          frameHeight: 32,
        });
        this.load.once("complete", () => {
          this.crackTextureReady = true;
        });
        this.load.start();
      }
      return;
    }
    let img = this.crackOverlays.get(key);
    if (!img || !img.active) {
      this.crackOverlays.get(key)?.destroy();
      img = this.add.image(tx * this.tilePx + this.tilePx / 2, ty * this.tilePx + this.tilePx / 2, "fx-cracks", 0);
      if (this.blockLayer) this.blockLayer.add(img);
      img.setDepth(1); // above the block sprite, below actors
      this.crackOverlays.set(key, img);
    }
    // 10 stages: never show the last frame as "cracked" (that's the break
    // itself — the snapshot removes the block a beat later).
    const stage = Math.min(8, Math.floor(ratio * 10));
    img.setFrame(stage);
    // Slow flicker on the heaviest stages (7+): dust settling vibe without
    // strobing. Base alpha rises with damage so early cracks stay subtle.
    const wobble = stage >= 7
      ? 0.9 + 0.1 * Math.sin(this.time.now / 120)
      : 1.0;
    img.setAlpha((0.55 + ratio * 0.45) * wobble);
  }

  /** Per-frame crack maintenance (60 fps): advance the server's self-repair
   * projection for every damaged tile and repaint the changed stages. The
   * server keeps sending authoritative samples — this is render-only and
   * self-corrects on the next sample. */
  tickBlockCracks(): void {
    if (this.crackState.size === 0) return;
    for (const key of [...this.crackState.keys()]) {
      const st = this.crackState.get(key);
      if (!st) continue;
      const healed = Math.max(0, (performance.now() - st.t0) / 1000) * BLOCK_HEAL_RATE;
      // Fully healed locally: drop the overlay now (the server sample will
      // agree a moment later).
      if (st.dmg - healed <= 0) {
        this.clearBlockCrack(key);
        continue;
      }
      this.renderCrack(key);
    }
  }

  /** A block face PNG arrived: register + redraw with the real sprite. */
  onBlockTexture(id: string): void {
    this.blockTextures.add(id);
    if (this.welcome) this.updateBlocks(this.welcome.blocks);
  }

  /**
   * A paperdoll PNG arrived (players/base.png or players/weapon/<x>.png).
   * Register the sheet into the Phaser texture system with its frame grid;
   * once the BASE sheet is ready the paperdoll takes over rendering.
   */
  onPaperdollAsset(name: string, b64: string): void {
    if (!this.playersManifest) return;
    if (!this.pdBytes) this.pdBytes = {};
    if (name === "players/base.png") {
      this.pdBytes["__base"] = b64ToBytes(b64);
    } else if (name.startsWith("players/weapon/")) {
      const stem = name.slice("players/weapon/".length).replace(/\.png$/i, "");
      this.pdBytes[stem] = b64ToBytes(b64);
      // Weapon sheets arrive AFTER base (and the once-only registration fix
      // means a full re-run would skip them) — register each one directly so
      // setWeapon finds its texture and the item actually shows in hand.
      registerWeaponSheet(this, this.playersManifest, stem, this.pdBytes[stem]);
    } else {
      return;
    }
    if (this.pdBytes["__base"] && !this.paperdollReady) {
      registerPaperdollTextures(this, this.playersManifest, this.pdBytes["__base"], this.pdBytes);
      // addSpriteSheet decodes the image ASYNC — the texture does not exist
      // yet. Poll for it, and only then swap the square for the Kaetram body
      // (spawning earlier rendered the Phaser green "missing texture" box).
      const trySpawn = (): void => {
        if (this.paperdollReady || !this.textures.exists("pd-base")) return;
        // Only flip to the doll when the sheet is actually CUT (frame 0 AND 1
        // exist) — addSpriteSheet registers the key before/while cutting, and
        // spawning mid-cut crashed setFrame ("no frame 1") inside the update
        // loop and froze the whole scene (map dead, movement dead).
        const tex = this.textures.get("pd-base");
        if (!tex || !tex.has("0") || !tex.has("1")) return;
        this.paperdollReady = true;
        if (this.selfMarker && !this.selfDoll) {
          this.spawnSelfDoll();
          this.selfMarker.setVisible(false);
        }
        for (const [id, rp] of this.players) this.spawnRemoteDoll(id, rp);
      };
      const poll = this.time.addEvent({ delay: 50, loop: true, callback: () => {
        trySpawn();
        if (this.paperdollReady) poll.remove();
      } });
      trySpawn(); // in case the decode finished synchronously
    }
  }

  private pdBytes: Record<string, Uint8Array> | null = null;

  /** Spawn the SELF paperdoll at the predicted position. */
  private spawnSelfDoll(): void {
    if (!this.playersManifest || this.selfDoll) return;
    this.selfDoll = new PaperdollBody(this, this.playersManifest);
    this.selfDoll.setTileScale(this.tilePx);
    this.selfDoll.spawn(this.selfX * this.tilePx, this.selfY * this.tilePx + this.tilePx / 2, 7);
    this.hideSelfHand(); // Kaetram body carries its own weapon layer
    if (this.selfHeld) this.selfDoll.setWeapon(weapon_sheet_for(this.selfHeld));
  }

  /** Spawn a remote paperdoll inside its interpolation container. */
  private spawnRemoteDoll(id: number, rp: RemotePlayer): void {
    if (!this.playersManifest || this.remoteDolls.has(id)) return;
    const doll = new PaperdollBody(this, this.playersManifest);
    doll.setTileScale(this.tilePx);
    doll.spawn(rp.container.x, rp.container.y + 16, 7);
    this.remoteDolls.set(id, doll);
    rp.doll = doll;
    // Hide the square but KEEP IT INPUT-ENABLED: Phaser 3 disables the hit
    // test for invisible game objects, so setVisible(false) killed clicks.
    // Alpha 0.001 stays clickable while looking fully transparent — the
    // square remains the click target for the profile popup.
    rp.body.setVisible(true);
    rp.body.setAlpha(0.001);
    if (rp.held) doll.setWeapon(weapon_sheet_for(rp.held));
  }

  updateBlocks(blocks: [number, number, string][]): void {
    for (const id of new Set(blocks.map(([, , bid]) => bid))) {
      if (!this.blockTextures.has(id)) {
        this.blockTextures.add(id); // request once per session
        this.pendingFetch?.(id);
      }
    }
    this.buildBlocks(blocks);
  }

  private spawnSelf(welcome: WelcomePayload): void {
    const s = welcome.self;
    if (this.selfMarker) {
      // Re-welcome (re-login / reconnect without a page reload): reuse the
      // existing marker — spawning a second one left a frozen "clone" at
      // the spawn point that looked exactly like the player.
      this.selfMarker.setPosition(s.x * this.tilePx, s.y * this.tilePx);
      this.ensureSelfHand();
      this.setSelfHeld(welcome.held ?? null);
      return;
    }
    // NOTE: server positions are already TILE-CENTER based (x_f = x + 0.5),
    // so x*32 lands exactly in the middle of the tile. Never add +16 here —
    // that shifts the avatar half a tile off the collision grid.
    this.selfMarker = this.add.rectangle(s.x * this.tilePx, s.y * this.tilePx, PLAYER_SIZE, PLAYER_SIZE, 0x5865f2);
    this.selfMarker.setStrokeStyle(2, 0xffffff, 0.9);
    this.selfMarker.setName("self");
    // Permanent role color label above self (same rule as remote labels).
    if (s.name) {
      this.selfLabel = this.add.text(s.x * this.tilePx, s.y * this.tilePx + 22, s.name, {
        fontSize: "10px", color: s.color || "#ffffff",
        stroke: "#000000", strokeThickness: 3,
      }).setOrigin(0.5).setDepth(9);
    }
    // Paperdoll texture already live? Swap the square for the body at once.
    // (Keep the square — it becomes invisible only when the doll spawns.)
    if (this.paperdollReady) {
      this.spawnSelfDoll();
      this.selfMarker.setVisible(false);
    }
    this.ensureSelfHand();
    this.setSelfHeld(welcome.held ?? null);
  }

  /** Create the self hand dot + tool icon once (world-space siblings). */
  private ensureSelfHand(): void {
    if (!this.selfHand || !this.selfHand.active) {
      this.selfHand = this.add.circle(0, 0, HAND_RADIUS, 0x5865f2);
      this.selfHand.setStrokeStyle(2, 0xffffff, 0.9);
      this.selfHand.setDepth(7);
    }
    if (!this.selfToolIcon || !this.selfToolIcon.active) {
      this.selfToolIcon = this.add.text(0, 0, "", { fontSize: "13px" }).setOrigin(0.5);
      this.selfToolIcon.setDepth(8);
    }
  }

  /** Real icon texture key for a held item ("icon-<id>"), when registered. */
  private iconTexFor(itemId: string | null): string | null {
    if (!itemId) return null;
    const tex = `icon-${itemId}`;
    return this.textures.exists(tex) ? tex : null;
  }

  /** Point a hand icon at the best available art: Kaetram PNG (Image) when
   * the texture is registered, emoji Text otherwise (font-less machines
   * render tofu — PNG first is the whole point). May destroy + recreate the
   * object when switching kind; returns the object to keep. */
  private applyHandIcon(
    icon: Phaser.GameObjects.Text | Phaser.GameObjects.Image,
    itemId: string | null,
    sizePx: number = HAND_ICON_PX,
  ): Phaser.GameObjects.Text | Phaser.GameObjects.Image {
    const tex = this.iconTexFor(itemId);
    const isImage = icon.type === "Image";
    if (tex && isImage) {
      (icon as Phaser.GameObjects.Image).setTexture(tex);
      return icon;
    }
    if (!tex && !isImage) {
      (icon as Phaser.GameObjects.Text).setText(this.emojiFor(itemId));
      return icon;
    }
    const { x, y, depth, visible } = icon;
    const parent = icon.parentContainer;
    icon.destroy();
    const next: Phaser.GameObjects.Text | Phaser.GameObjects.Image = tex
      ? this.add.image(x, y, tex).setOrigin(0.5).setDisplaySize(sizePx, sizePx)
      : this.add.text(x, y, this.emojiFor(itemId), { fontSize: "13px" }).setOrigin(0.5);
    next.setDepth(depth).setVisible(visible);
    if (parent) parent.add(next);
    return next;
  }

  /** Emoji for a held item id (server map first, "?" never — empty when unknown). */
  private emojiFor(itemId: string | null): string {
    if (!itemId) return "";
    return this.itemEmojis[itemId] ?? "";
  }

  /** Update the SELF hand icon (called on held echo + inventory + snapshot). */
  setSelfHeld(itemId: string | null): void {
    this.selfHeld = itemId;
    if (this.selfToolIcon) this.selfToolIcon = this.applyHandIcon(this.selfToolIcon, itemId);
    if (this.selfDoll) this.selfDoll.setWeapon(weapon_sheet_for(itemId));
  }

  /** Update the SELF hand from the local hotbar (instant, no server wait). */
  setSelfHeldFromHotbar(hotbar: (string | null)[], slot: number): void {
    const held = hotbar[slot] ?? null;
    this.setSelfHeld(held);
  }
  private upsertPlayer(p: PlayerPayload): void {
    let rp = this.players.get(p.id);
    const now = performance.now();
    if (!rp) {
      const container = this.add.container(p.x * this.tilePx, p.y * this.tilePx);
      // Chat-client players render as ROUND avatar tokens (the Discord
      // avatar circle); web players keep the square + paperdoll body.
      const isChat = p.mode === "chat";
      const color = isChat ? 0xd97706 : 0x2f9e63;
      const body = isChat
        ? this.add.circle(0, 0, PLAYER_SIZE / 2, color)
        : this.add.rectangle(0, 0, PLAYER_SIZE, PLAYER_SIZE, color);
      body.setStrokeStyle(2, 0xffffff, 0.9);
      const label = this.add.text(0, 22, p.name, {
        fontSize: "10px", color: p.color || "#ffffff",
        stroke: "#000000", strokeThickness: 3,
      }).setOrigin(0.5).setVisible(this.showNames);
      // Plan A hand: same-colour dot + tool icon, BOTH inside the container
      // so interpolation moves them for free (no per-frame sync needed).
      const hand = this.add.circle(HAND_ORBIT, 0, HAND_RADIUS, color);
      hand.setStrokeStyle(2, 0xffffff, 0.9);
      const toolIcon = this.add.text(HAND_ORBIT, 0, "", { fontSize: "13px" }).setOrigin(0.5);
      container.add(body);
      container.add(hand);
      container.add(toolIcon);
      container.add(label);
      rp = { container, body, mode: p.mode, label, webBadge: null, hand, handColor: color, toolIcon, held: null, swingT0: 0, buf: [], dir: p.dir, doll: null, profile: p };
      container.setData("pid", p.id);
      // Click the player -> profile popup (ekonia parity). Only the body
      // shape is interactive so the label/hand don't steal map clicks.
      body.setInteractive({ useHandCursor: true });
      body.on("pointerdown", () => {
        const cur = this.players.get(p.id);
        if (cur) this.onPlayerClick?.(cur.profile);
      });
      this.players.set(p.id, rp);
      // Paperdoll texture already live? Swap immediately (square stays as
      // invisible fallback geometry otherwise). Chat players never get a
      // doll — their round token IS the agreed cross-client look.
      if (this.paperdollReady && !isChat) this.spawnRemoteDoll(p.id, rp);
    } else if (rp.mode !== p.mode) {
      // Mode switched while alive (web session attached/detached): the
      // cheapest correct redraw is destroy + respawn on the next snapshot.
      rp.container.destroy();
      this.remoteDolls.get(p.id)?.destroy();
      this.remoteDolls.delete(p.id);
      this.players.delete(p.id);
      this.upsertPlayer(p);
      return;
    }
    rp.buf.push([now, p.x * this.tilePx, p.y * this.tilePx]);
    if (rp.buf.length > 12) rp.buf.shift();
    rp.dir = p.dir;
    rp.profile = p; // fresh stats for the profile popup
    // Role color can arrive after the body is created (minted server-side on
    // first join) — live-update the label so every viewer sees the SAME color.
    if (p.color && rp.label.style.color !== p.color) {
      rp.label.setColor(p.color);
    }
    // Held item changed -> refresh the tool icon (empty = bare hand dot).
    const held = p.held ?? null;
    if (held !== rp.held) {
      rp.held = held;
      rp.toolIcon = this.applyHandIcon(rp.toolIcon, held);
      const doll = this.remoteDolls.get(p.id);
      if (doll) doll.setWeapon(weapon_sheet_for(held));
    }
  }

  // ---- per-frame update (60fps) ----

  /**
   * Ekonia FadeOccluderLayer — SMOOTH edition. Godot's fade_occluder_layer.gd
   * drops alpha per TILE (alpha = lerp(1, min_alpha, closeness), closeness =
   * 1 - ring/(radius+1)) which reads as hard square rings when tiles are
   * zoomed to 64 screen-px. We evaluate the SAME curve per PIXEL from the
   * player's continuous position, every frame: the dim halo follows the
   * avatar as a soft radial gradient (distance in cell units, opaque at the
   * window edge), recomputed from the opaque snapshot — so walking away
   * restores the canopy exactly, with zero bake redraws.
   */
  private updateOccluderFade(): void {
    // ?fx gate (perf.ts): the fade compositing uploads a sub-rect to the
    // canopy texture every step — skippable via ?fx=0 for perf isolation.
    if (!perf.cave) return;
    const ctx = this.occluderCtx;
    const pix = this.occluderPix;
    if (!ctx || !pix || !this.mapAbove || !this.mapAbove.visible) return;
    const R = WorldScene.FADE_RADIUS_CELLS;
    const MIN = WorldScene.FADE_MIN_ALPHA;
    const reach = R + 1; // curve hits exactly opaque at the window edge
    const tile = this.tilePx;
    // Player center in canvas pixels (bake canvas: 1px = 1 world px).
    const pcx = (this.selfX + 0.5) * tile;
    const pcy = (this.selfY + 0.5) * tile;
    const w = ctx.canvas.width;
    const h = ctx.canvas.height;
    const half = reach * tile;
    const x0 = Math.max(0, Math.floor(pcx - half));
    const y0 = Math.max(0, Math.floor(pcy - half));
    const x1 = Math.min(w, Math.ceil(pcx + half));
    const y1 = Math.min(h, Math.ceil(pcy + half));
    const bw = x1 - x0;
    const bh = y1 - y0;
    if (bw <= 0 || bh <= 0) return;
    // STATIONARY FAST PATH: when neither the player nor the fade window
    // changed since the last composition, the canvas AND its GL texture
    // already hold the exact right pixels — skip everything (this beat ran
    // per frame and its allocations/readbacks were the movement stutter on
    // big Ekonia maps). Reuse one preallocated buffer otherwise.
    // MOVEMENT THROTTLE (PC lag, 22/09): the float position changes EVERY
    // frame while walking, so the old equality check never skipped and the
    // full sqrt loop + texSubImage2D upload ran 60-144x/s — stuttering
    // exactly (and only) while moving. The fade is a smooth radial gradient:
    // recomputing it every 1/4 tile (~8-12 Hz at walk speed) is visually
    // identical and cuts the per-step cost ~6x.
    const MOVED = 0.25;
    if (this.fadeBuf && this.fadeBuf.width === bw && this.fadeBuf.height === bh
        && Math.abs(this.selfX - this.lastFadeX) < MOVED
        && Math.abs(this.selfY - this.lastFadeY) < MOVED) {
      return;
    }
    this.lastFadeX = this.selfX;
    this.lastFadeY = this.selfY;
    const prevBuf = this.fadeBuf;
    const reuse = !!prevBuf
      && prevBuf.width === bw && prevBuf.height === bh;
    const img = reuse ? prevBuf! : ctx.createImageData(bw, bh);
    this.fadeBuf = img;
    const dst = img.data;
    const falloff = 1 - MIN; // lerp(1, MIN, c) == 1 - falloff * c
    for (let py = 0; py < bh; py++) {
      const dy = (y0 + py + 0.5 - pcy) / tile; // player-relative, in cells
      let si = ((y0 + py) * w + x0) * 4;
      let di = py * bw * 4;
      for (let px = 0; px < bw; px++, si += 4, di += 4) {
        const a0 = pix[si + 3];
        // Transparent bake pixel: explicitly zero it (the buffer is reused
        // across frames — stale pixels would otherwise leak into
        // transparent areas when the window moves).
        if (a0 === 0) {
          dst[di] = 0; dst[di + 1] = 0; dst[di + 2] = 0; dst[di + 3] = 0;
          continue;
        }
        const dx = (x0 + px + 0.5 - pcx) / tile;
        const d = Math.sqrt(dx * dx + dy * dy);
        let c = 1 - d / reach;
        if (c <= 0) {
          dst[di] = pix[si];
          dst[di + 1] = pix[si + 1];
          dst[di + 2] = pix[si + 2];
          dst[di + 3] = a0;
          continue;
        }
        if (c > 1) c = 1;
        dst[di] = pix[si];
        dst[di + 1] = pix[si + 1];
        dst[di + 2] = pix[si + 2];
        dst[di + 3] = a0 * (1 - falloff * c);
      }
    }
    ctx.putImageData(img, x0, y0);
    // CRITICAL: putImageData only edits the 2D backing store — the WebGL
    // texture Phaser sampled at bake time never updates, so without this
    // the fade is computed but never rendered ("no visible change").
    // Upload the SAME RAM buffer straight to the GPU (texSubImage2D) — the
    // previous version round-tripped through ctx.getImageData (GPU->CPU
    // readback) TWICE per frame; each readback stalls the render pipeline
    // and on big Ekonia maps that stall was the "lúc nhanh lúc chậm" lag.
    const tex = this.textures.get(this.occluderTexKey);
    const src = tex ? tex.getSourceImage() : null;
    // glTexture lives on the TextureSource (tex.source[0]); it is a
    // WebGLTextureWrapper whose .webGLTexture is the raw WebGLTexture
    // (Phaser 3.60+: the wrapper exposes .webGLTexture, NOT .glTexture —
    // the old probe read .glTexture.glTexture which was always undefined,
    // silently disabling the fast sub-rect upload and falling back to
    // updateCanvasTexture = a full 3584x2896 re-upload on EVERY fade step
    // = the 59ms movement stutter measured on the local stack).
    const tsrc = (
      tex as unknown as { source?: { glTexture?: { webGLTexture?: WebGLTexture; glTexture?: WebGLTexture } }[] }
    )?.source?.[0];
    const wrapper = tsrc?.glTexture;
    const raw = wrapper?.webGLTexture ?? wrapper?.glTexture;
    const renderer = this.game.renderer as unknown as {
      gl?: WebGL2RenderingContext;
      updateCanvasTexture?: (
        c: HTMLCanvasElement,
        t: unknown,
        flipY?: boolean,
        noRepeat?: boolean
      ) => unknown;
    };
    const gl = renderer.gl;
    if (src === ctx.canvas && gl && raw) {
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.bindTexture(gl.TEXTURE_2D, raw);
      gl.texSubImage2D(
        gl.TEXTURE_2D, 0, x0, y0, bw, bh,
        gl.RGBA, gl.UNSIGNED_BYTE, dst as unknown as ArrayBufferView
      );
    } else if (src === ctx.canvas && renderer.updateCanvasTexture && wrapper) {
      // Fallback (heavier, whole-canvas): still guarantees the fade shows.
      renderer.updateCanvasTexture(ctx.canvas, wrapper, false, true);
    }
  }

  update(_time: number, delta?: number): void {
    // Real frame delta (ms). The server integrates movement from REAL wall
    // time at 20 Hz — the prediction must do the same or it silently runs
    // 2x fast on 120 Hz displays (the old hardcoded 1/60 per frame) and the
    // avatar outruns the server until every position/click drifts apart.
    // Clamp to 0.2s like the server's dt clamp so a stalled tab can never
    // teleport the player through walls on resume.
    this.frameDtSec = Math.min(0.2, Math.max(0.001, (delta ?? 16.7) / 1000));
    // Perf EMA (half-life ~12 s at 60fps: k = 1 - 2^(-dt/12)).
    const kFrame = 1 - Math.pow(2, -(this.frameDtSec / 12));
    this.avgFrameMs += ((delta ?? 16.7) - this.avgFrameMs) * kFrame;
    // Accumulate real frame time against the current input vector — when the
    // next seq'd input is flushed it carries this dt so the replay buffer can
    // re-integrate the EXACT same wall-clock slices the server will.
    const nowT = performance.now();
    if (this.lastFrameT > 0) {
      this.pendingDt = Math.min(0.25, this.pendingDt + (nowT - this.lastFrameT) / 1000);
    }
    this.lastFrameT = nowT;
    // Drift-recovery tracker: measure how long the prediction has sat far
    // from the server authority while idle. Movement (any pending input)
    // resets it — divergence while ACTIVELY moving is normal echo lag.
    if (this.seqReplayActive && this.inputLog.length === 0 && this.selfServerPos) {
      const d = Math.hypot(this.selfX - this.selfServerPos.x, this.selfY - this.selfServerPos.y);
      this.driftIdleMs = d > 0.75 ? this.driftIdleMs + this.frameDtSec * 1000 : 0;
    } else {
      this.driftIdleMs = 0;
    }
    // Mouse tile + hover box derive FRESH each frame from the last cursor
    // position: the camera moves under a still cursor (follow lerp, tab
    // switch) and a tile cached at mousemove time would be stale.
    if (this.mouseScreen) {
      this.mouseTile = this.tileFromScreen(this.mouseScreen.x, this.mouseScreen.y);
    } else {
      this.mouseTile = null;
    }
    this.updateHoverSquare();
    this.updateStationPrompt();
    // Meteor-ore reveal beat: fade in any ore sprite once the impact FX
    // near it has finished exploding (see updateResourceLayer gate).
    if (this.pendingOreReveal.size > 0) {
      for (const [key, img] of [...this.pendingOreReveal.entries()]) {
        const [tx, ty] = key.split(",").map(Number);
        if (!meteorFxBusyNear(tx, ty)) {
          this.pendingOreReveal.delete(key);
          this.tweens.add({ targets: img, alpha: 1, duration: 250, ease: "Quad.easeOut" });
        }
      }
    }
    // NPC proximity is recomputed per frame (cheap — a handful of tokens).
    this.nearestNpc = this.findNearestNpc();
    // --- client-side prediction: move SELF instantly every frame ---
    // Server speed: walk 4 tiles/s, run 6 tiles/s (config.WEB_*_SPEED).
    this.stepSelf();
    // Ekonia FadeOccluderLayer parity: smooth per-pixel canopy fade that
    // follows the player every frame (cheap — small window, typed loop).
    this.updateOccluderFade();
    // Day/night tint as GPU rects INSIDE this canvas (daynight_phaser.ts):
    // replaces the separate DOM 2D canvas whose full-window compositor blend
    // during movement was the PC-only stutter (idle = static screen = browser
    // skips recomposite; movement = every pixel changes = blend every frame).
    if (perf.daynight) dayNightPhaser.update();
    // Facing vector still feeds the hover square + swing geometry (the hand
    // dot itself is hidden once the paperdoll body renders).
    this.updateFacing();

    // Paperdoll animation: drive self + remote dolls from the shared clock.
    // The atk overlay is owned by PaperdollBody (swing() arms a one-shot
    // timer); we only feed the base idle/walk action here.
    const nowMs = performance.now();
    if (this.selfLabel) {
      this.selfLabel.setPosition(this.selfX * this.tilePx, this.selfY * this.tilePx + 22);
    }
    if (this.selfDoll?.ready && this.selfMarker) {
      // MOVING = any movement input, not Shift-running. The old check read
      // inputVec.running (Shift only), so plain walking never left the idle
      // row — no leg animation.
      const moving = this.inputVec.dx !== 0 || this.inputVec.dy !== 0;
      this.selfDoll.animate(this.selfX * this.tilePx, this.selfY * this.tilePx + this.tilePx / 2, moving ? "walk" : "idle", this.selfDir, nowMs);
    }

    const now = performance.now() - INTERP_BUFFER_MS;
    for (const rp of this.players.values()) {
      if (rp.container.getData("self")) continue; // self is predicted, not interpolated
      const buf = rp.buf;
      if (buf.length === 0) continue;
      // Find the pair bracketing `now`.
      let prev = buf[0];
      let next = buf[buf.length - 1];
      for (let i = 0; i < buf.length - 1; i++) {
        if (buf[i][0] <= now && now <= buf[i + 1][0]) {
          prev = buf[i];
          next = buf[i + 1];
          break;
        }
      }        const span = next[0] - prev[0];
        const t = span > 0 ? Math.max(0, Math.min(1, (now - prev[0]) / span)) : 1;
      const x = prev[1] + (next[1] - prev[1]) * t;
      const y = prev[2] + (next[2] - prev[2]) * t;
      rp.container.setPosition(x, y);
      // Remote hand orbits the facing dir (8-way, no smoothing needed — the
      // dir changes at most a few times per second) + swing thrust.
      const dv = DIR_VECTORS[rp.dir] ?? DIR_VECTORS.SOUTH;
      const len = Math.hypot(dv[0], dv[1]) || 1;
      const reach = HAND_ORBIT + this.swingExtra(rp.swingT0, performance.now());
      rp.hand.setPosition((dv[0] / len) * reach, (dv[1] / len) * reach);
      rp.toolIcon.setPosition(rp.hand.x, rp.hand.y);
      // Paperdoll: hide the hand dot + icon under the Kaetram body, animate
      // the doll. (The square stays hidden — it's hit geometry only.)
      const doll = rp.doll;
      if (doll?.ready) {
        rp.hand.setVisible(false);
        rp.toolIcon.setVisible(false);
        const moving = Math.hypot(next[1] - prev[1], next[2] - prev[2]) > 1;
        doll.animate(x, y + 16, moving ? "walk" : "idle", rp.dir, performance.now());
      }
    }
    this.updateZombieFrames();
    // Block crack self-repair projection (render-only; server owns truth).
    this.tickBlockCracks();
    // Drop entities: per-frame bob/glow/collect animation (server phase).
    this.updateDrops(performance.now());
    // Kaetram hitsplats: float + fade every frame (spawned from action_result).
    this.updateSplats(performance.now());
    this.updateChew();
  }

  /** Sync the drop-entity layer with the server payload (20 Hz). */
  syncDrops(list: DropPayload[]): void {
    const now = performance.now();
    const seen = new Set<string>();
    for (const [id, itemId, qty, x, y, z, phase, target] of list) {
      seen.add(id);
      let d = this.drops.get(id);
      if (!d) {
        if (!this.dropLayer) this.dropLayer = this.add.layer();
        const container = this.add.container(x * this.tilePx, y * this.tilePx);
        const glow = this.add.circle(0, 0, 7, 0x8fd6ff, 0.35);
        // Icon: Kaetram pixel-art PNG (Image) when the texture is already
        // registered, emoji Text otherwise — flipped later by onIconTexture.
        const tex = this.iconTexFor(itemId);
        const icon = tex
          ? this.add.image(0, -4, tex).setOrigin(0.5).setDisplaySize(DROP_ICON_PX, DROP_ICON_PX)
          : this.add.text(0, -4, this.emojiFor(itemId) || "❖", {
              fontSize: "13px",
              stroke: "#0a0d12", strokeThickness: 3,
            }).setOrigin(0.5);
        // Qty badge as a SEPARATE object so the icon can swap kind freely.
        const label = qty > 1
          ? this.add.text(6, 4, `×${qty}`, {
              fontSize: "10px", color: "#ffe9a8",
              stroke: "#0a0d12", strokeThickness: 3,
            }).setOrigin(0.5)
          : null;
        const parts: Phaser.GameObjects.GameObject[] = label
          ? [glow, icon, label]
          : [glow, icon];
        container.add(parts);
        container.setDepth(6);
        this.dropLayer.add(container);
        d = {
          container, glow, itemId, icon, label,
          tx: x * this.tilePx, ty: y * this.tilePx, z: z * this.tilePx,
          phase, bornT: now, collectedT: 0, target,
          bobSeed: Math.random() * Math.PI * 2,
        };
        this.drops.set(id, d);
        // Spawn pop: scale from 0 with a small overshoot.
        container.setScale(0.1);
      } else {
        d.tx = x * this.tilePx;
        d.ty = y * this.tilePx;
        d.z = z * this.tilePx;
        d.target = target;
        if (d.phase !== phase) {
          d.phase = phase;
          if (phase === "collected") d.collectedT = now;
        }
      }
    }
    for (const [id, d] of this.drops) {
      if (!seen.has(id)) {
        d.container.destroy();
        this.drops.delete(id);
      }
    }
  }

  /** Per-frame drop animation: spawn pop, bob + glow pulse, collect burst. */
  private updateDrops(now: number): void {
    for (const d of this.drops.values()) {
      const age = now - d.bornT;
      // MAGNET phases home DIRECTLY to the live sprite of the player the
      // server is pulling the drop toward — for SELF that is the predicted
      // marker (zero sample lag), for OTHERS their remote body. Falling back
      // to self for someone else's drop made every observer see drops fly
      // into THEMSELVES first (bug 15/09).
      const self = this.selfMarker;
      const targetBody = d.target && d.target !== (this.welcome?.self.id ?? 0)
        ? this.players.get(d.target)?.container ?? null
        : (self !== null && !this.selfDead ? self : null);
      const magnet = d.phase === "magnet" && targetBody !== null;
      const gx = magnet && targetBody ? targetBody.x : d.tx;
      const gy = magnet && targetBody ? targetBody.y - 10 : d.ty; // torso, not feet
      // Snappier smoothing while magnet (the server sample moves in 0.55-tile
      // jumps at 11 tiles/s; a slow lerp made the flight feel mushy/laggy).
      const k = 1 - Math.exp((magnet ? -30 : -18) * this.frameDtSec);
      const cx = d.container.x + (gx - d.container.x) * k;
      const cy = d.container.y + (gy - d.container.y) * k;
      // Idle bob after landing (server z already carries the arc; add a
      // gentle client bob of ±2 px so the drop feels alive).
      const bob = Math.sin(now / 350 + d.bobSeed) * 2;
      d.container.setPosition(cx, cy - d.z - bob);
      // Glow pulse.
      const pulse = 0.28 + 0.14 * Math.sin(now / 260 + d.bobSeed);
      d.glow.setFillStyle(0x8fd6ff, pulse);
      // Spawn pop scale (0.1 -> 1 with overshoot at ~150 ms).
      if (age < 220) {
        const t = age / 220;
        d.container.setScale(t < 0.7 ? 0.1 + t * 1.4 : 1.08 - (t - 0.7) * 0.26);
      } else if (d.phase !== "collected") {
        d.container.setScale(1);
      }
      // Collect burst: quick scale-up + fade-out.
      if (d.phase === "collected" && d.collectedT > 0) {
        const t = Math.min(1, (now - d.collectedT) / 400);
        d.container.setScale(1 + t * 0.8);
        d.container.setAlpha(1 - t);
        d.glow.setFillStyle(0xfff3b0, 0.5 * (1 - t));
      }
    }
  }

  /** Per-frame zombie animation + position smoothing (60 fps).
   *
   * Position: exponential smoothing toward the LATEST server sample. The old
   * buffer-window interpolation (rendering 120ms behind, searching the sample
   * pair containing the cursor) flickered with bursty 20 Hz delivery: the
   * cursor kept falling outside the window, pinning the sprite to the oldest
   * sample and then gliding forward — "at this spot, back to the old spot",
   * over and over. Smoothing never moves backwards and ignores jitter. */
  private updateZombieFrames(): void {
    const now = performance.now();
    const dt = this.lastZombieFrameT > 0
      ? Math.min(0.1, (now - this.lastZombieFrameT) / 1000)
      : 0.016;
    this.lastZombieFrameT = now;
    for (const [id, z] of this.zombies) {
      const buf = z.buf;
      if (buf.length > 0) {
        const latest = buf[buf.length - 1];
        const lx = latest[1];
        const ly = latest[2];
        const dist = Math.hypot(lx - z.lastX, ly - z.lastY);
        if (dist > this.tilePx * 3) {
          // Teleport (>3 tiles): respawn/despawn lag — snap, don't glide.
          z.lastX = lx;
          z.lastY = ly;
        } else if (dist > 0.01) {
          // ~14/s rate: a 0.64-tile server tick is caught up in ~100ms.
          const a = 1 - Math.exp(-14 * dt);
          z.lastX += (lx - z.lastX) * a;
          z.lastY += (ly - z.lastY) * a;
        }
        z.container.setPosition(z.lastX, z.lastY);
      }
      if (z.dieT0 !== 0) {
        const age = now - z.dieT0;
        const k = Math.min(1, age / 500);
        z.container.setAlpha(1 - k);
        z.container.y += 10 * k * 0.016;
        if (z.body instanceof Phaser.GameObjects.Image) z.body.setTintFill(0xffffff);
        if (k >= 1) {
          z.container.destroy();
          this.zombies.delete(id);
        }
        continue;
      }
      if (z.body instanceof Phaser.GameObjects.Image) {
        // Server-authoritative anim + facing -> Kaetram mob sheet row.
        // Every mob uses the Kaetram atk/walk/idle x right/up/down layout —
        // LEFT mirrors the right row (flipX); frame counts come from the
        // per-kind MOB_SHEETS table (Kaetram sprites.json).
        const sheet = MOB_SHEETS[z.kind] ?? MOB_SHEETS.zombie;
        const fx = this.zombieFlipX(z.facing, z.kind);
        const facing = fx ? "right" : this.zombieRowFacing(z.facing, z.kind);
        const dirRow = (anim: string, face: string): [number, number] => {
          const group = (sheet.rows as any)[anim] ?? sheet.rows.idle;
          return (group as any)[face] ?? group.right;
        };
        const [row, len] = dirRow(z.anim === "atk" ? "atk" : z.anim === "walk" ? "walk" : "idle", facing);
        const pace = z.anim === "atk" ? 90 : z.anim === "walk" ? 160 : 500;
        if (now - z.frameT0 >= pace) {
          z.frameT0 = now;
          z.frame = z.anim === "atk"
            ? Math.min(len - 1, z.frame + 1) // lunge holds its last frame
            : (z.frame + 1) % Math.max(1, len); // walk/idle loop
        }
        this.applyMobCell(z.body, z.frame, row, sheet.size, sheet.cellW, sheet.cellH);
        z.body.setFlipX(fx);
        if (z.anim === "atk") {
          z.body.setTint(0xffb0a0);
          z.body.setAngle(z.frame % 2 === 0 ? -6 : 6);
        } else {
          z.body.clearTint();
          z.body.setAngle(0);
        }
      } else if (z.body instanceof Phaser.GameObjects.Rectangle) {
        if (z.anim === "atk") z.body.setScale(1.35, 0.85);
        else z.body.setScale(1, 1);
      }
    }
  }

  /** One frame of predicted local movement, mirroring the server exactly. */
  private stepSelf(): void {
    const marker = this.selfMarker;
    if (!marker || !this.welcome) return;
    const v = this.inputVec;
    if (this.selfDead) {
      // Dead: the server drops our movement inputs — integrating locally only
      // created the invisible-ring effect (server kept snapping us back).
      // Hold position on the authority, keep the aim visuals updating.
      marker.setPosition(this.selfServerPos.x * this.tilePx, this.selfServerPos.y * this.tilePx);
      return;
    }
    const dt = this.frameDtSec;
    if ((v.dx !== 0 || v.dy !== 0) && dt > 0) {
      // Hand points where we walk: facing follows movement.
      this.lastMoveX = v.dx;
      this.lastMoveY = v.dy;
      // Do NOT snap selfDir from the input here: applySnapshot owns the
      // 8-way facing so the arm never jerks between a diagonal and its
      // dominant axis (the "giật giật 1 phát" bug).
      // Mirror the server's continuous integration (game/manager.
      // _web_tick_runtime) EXACTLY: same speed constants, the same raw
      // (already-normalized) input vector, real wall-clock dt, and the same
      // swept per-axis SLIDE collision. Any divergence here accumulates
      // every frame and the avatar silently walks away from the server.
      // Speed from the SERVER's welcome payload (config.WEB_*_SPEED — the
      // panel can tune them per deployment). Hardcoding 4/6 here made the
      // prediction walk a different speed than the server, so drift grew
      // every second and the snap correction fired repeatedly (= giật).
      const s = this.welcome.self;
      // Stamina gate: once stamina hits 0 the server caps us at WALK speed —
      // mirror that locally so the prediction never diverges while tired.
      // SERVER PARITY (game/manager._web_tick_runtime): a sprint tick DRAINS
      // STAMINA_RUN_DRAIN per second — the prediction mirrors it with the
      // same frame dt, so the local bar empties at the same rate the server
      // authority does (the drain used to be server-only dead code for
      // modern clients AND absent here: the bar never moved).
      const tired = this.selfStamina <= 0;
      // Eating: half speed while chewing (server EAT_SPEED_MULT).
      const eatMul = this.selfEating ? 0.5 : 1.0;
      const speed = (v.running && !tired
        ? (s?.run_speed ?? 6.0)
        : (s?.walk_speed ?? 4.0)) * eatMul;
      if (v.running && !tired) {
        this.selfStamina = Math.max(
          0, this.selfStamina - (s?.stamina_run_drain ?? 4) * dt,
        );
      }
      const stepX = v.dx * speed * dt;
      const stepY = v.dy * speed * dt;
      this.selfX += this.freeX(this.selfX, this.selfY, stepX);
      this.selfY += this.freeY(this.selfX, this.selfY, stepY);
      // Pixel-accurate refinement (server parity: can_move_float's
      // correct()) — run per prediction step, not just per snapshot, so the
      // box visibly hugs the sprite's opaque shape instead of the tile edge.
      this.correctMaskOverlap();
    }
    // Reconciliation lives in applySnapshot now (input-sequence replay):
    // every snapshot rewinds self to the acked authority position and replays
    // unacked inputs — error converges to ~0 per snapshot with NO glide and
    // NO threshold, so there is never a visible correction yank. The only
    // hard corrections left are in applySnapshot: a >20-tile jump (portal/
    // respawn) snaps instantly, and a dead player freezes on the authority.
    // Safety net for pre-seq servers (no last_seq in snapshots): snap only on
    // an extreme divergence, never glide — a glide was the exact "bi kéo"
    // feel this rewrite removes.
    if (!this.seqReplayActive) {
      const age = performance.now() - this.lastServerRecv;
      if (age < 600) {
        const drift = Math.hypot(
          this.selfX - this.selfServerPos.x,
          this.selfY - this.selfServerPos.y,
        );
        if (drift > 20) {
          this.selfX = this.selfServerPos.x;
          this.selfY = this.selfServerPos.y;
        }
      }
    }
    // Invariant guard: the collision box must NEVER sit inside a solid tile.
    // Movement, glide and the snap are all collision-aware so this only fires
    // when a block APPEARS under the player between snapshots (another
    // player's place, a regrown tree) while the local grid was stale — push
    // the box out along the axis of least penetration instead of letting it
    // keep running through the block.
    this.resolveSolidOverlap();
    this.correctMaskOverlap();
    this.updateFacing();
    marker.setPosition(this.selfX * this.tilePx, this.selfY * this.tilePx);
  }

  /** Sub-tile mask refinement — client mirror of the server's
   * MapTileMasks.correct() (rendering/tile_masks.py), run AFTER the swept
   * prediction (which stays byte-identical to the server's tile sweep) and
   * only as a positional correction each snapshot, same as
   * resolveSolidOverlap. Never runs when the map has no masks. */
  private correctMaskOverlap(): void {
    if (this.tileMasks.size === 0) return;
    const r = 0.3;
    const E = 1e-6;
    const cell = 1 / WorldScene.MASK_RES;
    for (let pass = 0; pass < 4; pass++) {
      let bestPen = Infinity;
      let bestShiftX = 0;
      let bestShiftY = 0;
      for (let ty = Math.floor(this.selfY - r); ty <= Math.floor(this.selfY + r); ty++) {
        for (let tx = Math.floor(this.selfX - r); tx <= Math.floor(this.selfX + r); tx++) {
          const mask = this.tileMasks.get(`${tx},${ty}`);
          if (mask === undefined) continue;
          // Felled node tiles walk free even under a mask (server parity).
          if (this.felledTiles.has(`${tx},${ty}`)) continue;
          const left = this.selfX - r;
          const right = this.selfX + r;
          const top = this.selfY - r;
          const bottom = this.selfY + r;
          if (right <= tx + E || left >= tx + 1 - E ||
              bottom <= ty + E || top >= ty + 1 - E) continue;
          const mx0 = Math.max(0, Math.floor((left - tx) / cell));
          const mx1 = Math.min(WorldScene.MASK_RES - 1, Math.floor((right - tx) / cell));
          const my0 = Math.max(0, Math.floor((top - ty) / cell));
          const my1 = Math.min(WorldScene.MASK_RES - 1, Math.floor((bottom - ty) / cell));
          let sx0 = -1, sx1 = -1, sy0 = -1, sy1 = -1;
          for (let my = my0; my <= my1; my++) {
            const rowbits = mask >> (my * WorldScene.MASK_RES);
            for (let mx = mx0; mx <= mx1; mx++) {
              if ((rowbits & (1 << mx)) === 0) continue;
              // Real interior overlap (box shrunk a hair).
              const cx0 = tx + mx * cell;
              const cx1 = cx0 + cell;
              const cy0 = ty + my * cell;
              const cy1 = cy0 + cell;
              if (right - 1e-6 <= cx0 || left + 1e-6 >= cx1) continue;
              if (bottom - 1e-6 <= cy0 || top + 1e-6 >= cy1) continue;
              if (sx0 < 0 || mx < sx0) sx0 = mx;
              if (mx > sx1) sx1 = mx;
              if (sy0 < 0 || my < sy0) sy0 = my;
              if (my > sy1) sy1 = my;
            }
          }
          if (sx0 < 0) continue;
          const cX0 = tx + sx0 * cell;
          const cX1 = tx + (sx1 + 1) * cell;
          const cY0 = ty + sy0 * cell;
          const cY1 = ty + (sy1 + 1) * cell;
          const cand = [
            { pen: cX1 - left, dx: (cX1 + r) - this.selfX, dy: 0 },
            { pen: right - cX0, dx: (cX0 - r) - this.selfX, dy: 0 },
            { pen: cY1 - top, dx: 0, dy: (cY1 + r) - this.selfY },
            { pen: bottom - cY0, dx: 0, dy: (cY0 - r) - this.selfY },
          ];
          for (const c of cand) {
            if (c.pen >= -1e-6 && c.pen < bestPen) {
              bestPen = c.pen;
              bestShiftX = c.dx;
              bestShiftY = c.dy;
            }
          }
        }
      }
      // Max legitimate penetration: half box + one sub-cell (~0.43) —
      // beyond that the box tunneled; never yank (server parity).
      if (bestPen === Infinity || bestPen > r + 1 / WorldScene.MASK_RES + 1e-6) return;
      const nx = this.selfX + bestShiftX;
      const ny = this.selfY + bestShiftY;
      // The correction must never push the box INTO a square-solid tile
      // (server parity: the server bails out to the old position then).
      let legal = true;
      const cx = Math.floor(nx), cy = Math.floor(ny);
      if (this.solidAt(cx, cy)) legal = false;
      if (legal && this.solidAt(Math.floor(nx - r), cy)) legal = false;
      if (legal && this.solidAt(Math.floor(nx + r), cy)) legal = false;
      if (legal && this.solidAt(cx, Math.floor(ny - r))) legal = false;
      if (legal && this.solidAt(cx, Math.floor(ny + r))) legal = false;
      if (!legal) return;
      this.selfX = nx;
      this.selfY = ny;
    }
  }

  /** Push the box out of any solid tile it currently overlaps (least-
   * penetration axis, iterated). Guarantees the "never inside a block"
   * invariant regardless of how the position got there. */
  private resolveSolidOverlap(): void {
    const r = 0.3;
    const E = 1e-6;
    for (let pass = 0; pass < 4; pass++) {
      let bestShiftX = 0;
      let bestShiftY = 0;
      let bestDist = Infinity;
      for (let ty = Math.floor(this.selfY - r); ty <= Math.floor(this.selfY + r); ty++) {
        for (let tx = Math.floor(this.selfX - r); tx <= Math.floor(this.selfX + r); tx++) {
          if (!this.solidAt(tx, ty)) continue;
          // Mask-refined tiles are ENTERABLE (freeX/freeY let the box step
          // in so correctMaskOverlap can hug the opaque shape) — pushing the
          // box out of them here fought the mask correction EVERY frame
          // (enter -> yanked out -> re-enter): the bigmap movement stutter.
          // The server has no equivalent push-out either — parity = skip.
          if (this.maskPassable(tx, ty)) continue;
          const left = this.selfX - r;
          const right = this.selfX + r;
          const top = this.selfY - r;
          const bottom = this.selfY + r;
          // Skip tiles the box merely touches (edge on the boundary).
          if (right <= tx + E || left >= tx + 1 - E ||
              bottom <= ty + E || top >= ty + 1 - E) continue;
          const cand = [
            { x: tx + 1 + r - this.selfX, y: 0, d: Math.abs(tx + 1 + r - this.selfX) },  // left edge to tile right
            { x: tx - r - this.selfX, y: 0, d: Math.abs(tx - r - this.selfX) },            // right edge to tile left
            { x: 0, y: ty + 1 + r - this.selfY, d: Math.abs(ty + 1 + r - this.selfY) },  // top edge to tile bottom
            { x: 0, y: ty - r - this.selfY, d: Math.abs(ty - r - this.selfY) },            // bottom edge to tile top
          ];
          for (const c of cand) {
            if (c.d < bestDist) {
              bestDist = c.d;
              bestShiftX = c.x;
              bestShiftY = c.y;
            }
          }
        }
      }
      if (bestDist === Infinity) return; // legal: nothing overlapping
      this.selfX += bestShiftX;
      this.selfY += bestShiftY;
    }
  }

  /**
   * Screen (0..1, from DOM mouse events) -> world tile, through the camera
   * (zoom-safe). Called fresh EVERY FRAME for the hover box, and with the
   * click's own coordinates for actions — no cached tile can go stale.
   */
  private tileFromScreen(sx: number, sy: number): { x: number; y: number } | null {
    const cam = this.cameras.main;
    if (!cam.width || !cam.height) return null;
    const w = cam.getWorldPoint(sx * cam.width, sy * cam.height);
    if (w.x < 0 || w.y < 0) return null;
    const map = this.welcome?.map;
    if (map) {
      const mw = map.width * map.tile_width;
      const mh = map.height * map.tile_height;
      if (w.x >= mw || w.y >= mh) return null;
    }
    return { x: Math.floor(w.x / this.tilePx), y: Math.floor(w.y / this.tilePx) };
  }

  private ensureHoverSquare(): void {
    if (this.hoverSquare && this.hoverSquare.active) return;
    this.hoverSquare = this.add.rectangle(0, 0, 30, 30, 0x8fd4ff, 0.05)
      .setStrokeStyle(2, 0x8fd4ff, 0.9)
      .setDepth(100)
      .setVisible(false);
  }

  /** MOBILE AIM TARGET (touch): the tile the player confirmed with a first
   *  tap — a second tap on the SAME tile executes, a tap elsewhere re-aims.
   *  null = aim mode idle (the hover square follows taps only). The cursor
   *  (updateHoverSquare) drives it; the main.ts mobile hooks consume it via
   *  hasAimTarget/peekAimTarget/consumeAimTarget. Pure client state: the
   *  server still receives the tile the action lands on. */
  private mobileAimTile: { x: number; y: number } | null = null;

  /** Mobile aim helpers — see mobileAimTile above. */
  hasAimTarget(): boolean { return this.mobileAimTile !== null; }

  /** STICKY LOCK (flicker-free rapid tapping): set the aim tile without
   *  any two-tap gate — the box stays put across rapid taps on the same
   *  tile and only re-renders when the lock actually moves. */
  lockAimTarget(t: { x: number; y: number }): void {
    this.mobileAimTile = { ...t };
  }

  /** Drop the sticky lock (box hides). */
  clearAimTarget(): void { this.mobileAimTile = null; }

  peekAimTarget(): { x: number; y: number } | null {
    return this.mobileAimTile ? { ...this.mobileAimTile } : null;
  }

  /** Read-and-clear (executing an action dismisses the confirmation). */
  consumeAimTarget(): { x: number; y: number } | null {
    const t = this.mobileAimTile;
    this.mobileAimTile = null;
    return t ? { ...t } : null;
  }

  /** Two-tap aim state machine for touch: FIRST tap on a world point =
   *  select the tile (hover square locks onto it, turns green); SECOND tap
   *  on the SAME tile = confirmed -> returns true (caller executes);
   *  tap on a DIFFERENT tile = re-aim -> returns false. Desktop is never
   *  routed through this (main.ts branches on pointer type).
   *  OFFSET KILLER: the armed tile is the CLAMPED one — the exact tile the
   *  server will act on (clampClickTile mirrors the server's aim clamp).
   *  Showing the raw tapped tile while the action landed on the clamped
   *  neighbour was the "box đập vẫn lệch" report: the box and the effect
   *  MUST be the same tile, so they share one source of truth. */
  mobileTapAim(sx: number, sy: number): boolean {
    const tile = this.screenToTile(sx, sy);
    if (!tile) { this.mobileAimTile = null; return false; }
    const target = this.clampClickTile(tile) ?? tile;
    // OUT-OF-REACH GUARD: when the tap is genuinely beyond the server's
    // reach (clampClickTile null, > AIM_RANGE + tolerance), do NOT arm the
    // square at the raw tile — the server would refuse the action while
    // the box sat there inviting a second tap ("box lệch" #2: aim box at
    // tap tile, effect either clamped away or refused). Show NOTHING and
    // leave aim idle so the player walks closer and taps a REACHABLE tile.
    if (this.clampClickTile(tile) === null) return false;
    if (
      this.mobileAimTile &&
      this.mobileAimTile.x === target.x &&
      this.mobileAimTile.y === target.y
    ) {
      this.mobileAimTile = null;
      return true; // confirmed
    }
    this.mobileAimTile = target;
    return false;
  }

  private updateHoverSquare(): void {
    this.ensureHoverSquare();
    const sq = this.hoverSquare;
    if (!sq) return; // ensureHoverSquare guarantees construction; belt+braces
    // SIZE PARITY ACROSS MAPS: the box matches the tile cell (tilePx - 2).
    // On 32px maps that is ~30px; on Ekonia's 16px tiles it shrank to 14px
    // — half the on-screen size of the bigmap box at the same zoom, which
    // read as "ô đặt block siêu nhỏ". Match the BIGMAP look: the box spans
    // 2x2 tile cells (32px) on 16px maps, 1 cell on 32px maps — the same
    // world-space size everywhere. Placement still lands on the CENTER
    // cell; the oversized outline is the aiming aid, not the footprint.
    const boxPx = this.tilePx >= 32 ? this.tilePx - 2 : this.tilePx * 2 - 2;
    sq.setSize(boxPx, boxPx);
    // MOBILE AIM LOCK: while a touch aim target is armed it OWNS the cursor
    // square (locked green = "tap again to act"). Otherwise the cursor
    // follows the live mouse tile as before (desktop unchanged).
    if (this.mobileAimTile) {
      sq
        .setPosition(this.mobileAimTile.x * this.tilePx + this.tilePx / 2, this.mobileAimTile.y * this.tilePx + this.tilePx / 2)
        .setVisible(true)
        .setDepth(100)
        .setActive(true)
        .setAlpha(1)
        .setFillStyle(0x6fe08c, 0.14) // green tint: armed
        .setStrokeStyle(2, 0x6fe08c, 0.95);
      return;
    }
    if (!this.mouseTile) {
      sq.setVisible(false);
      return;
    }
    sq
      .setPosition(this.mouseTile.x * this.tilePx + this.tilePx / 2, this.mouseTile.y * this.tilePx + this.tilePx / 2)
      .setVisible(true)
      .setDepth(100)
      .setActive(true)
      .setAlpha(1)
      .setFillStyle(0x8fd4ff, 0.05) // default blue: hover
      .setStrokeStyle(2, 0x8fd4ff, 0.9);
  }

  // ===== NPC tokens: emoji sprite + label + E/click dialogue =====

  private spawnNpcs(
    npcs: { id: string; name: string; emoji: string; x: number; y: number }[],
  ): void {
    for (const s of this.npcSprites.values()) s.container.destroy();
    this.npcSprites.clear();
    for (const n of npcs) {
      const container = this.add.container(n.x * this.tilePx + this.tilePx / 2, n.y * this.tilePx + this.tilePx / 2);
      const label = this.add
        .text(0, -30, n.name, {
          fontFamily: "Verdana, sans-serif", fontSize: "10px",
          color: "#ffe9a8", stroke: "#1a1208", strokeThickness: 3,
        })
        .setOrigin(0.5);
      const emoji = this.add
        .text(0, 0, n.emoji, { fontSize: "26px" })
        .setOrigin(0.5);
      container.add([emoji, label]);
      container.setDepth(20);
      container.setInteractive(
        new Phaser.Geom.Rectangle(0, 0, 40, 48), Phaser.Geom.Rectangle.Contains,
      );
      container.on("pointerdown", () => this.onNpcInteract?.(n));
      this.npcSprites.set(n.id, { container, x: n.x, y: n.y });
    }
  }

  /** Nearest NPC within Chebyshev range 1 (adjacent, like the Discord
   *  npc_adjacent rule: |dx| + |dy| == 1). */
  private findNearestNpc(): { id: string; name: string; x: number; y: number } | null {
    let best: { id: string; name: string; x: number; y: number } | null = null;
    for (const [id, n] of this.npcSprites) {
      const d = Math.abs(n.x - this.selfX) + Math.abs(n.y - this.selfY);
      if (d === 1) {
        best = { id, name: id, x: n.x, y: n.y };
        break;
      }
    }
    return best;
  }

  // ===== Station interact: E prompt + hover cursor + click-to-open =====

  /** Nearest station tile within Chebyshev STATION_INTERACT_RANGE of the
   *  self position (server positions are tile-center based). */
  private findNearestStation(): { x: number; y: number } | null {
    let best: { x: number; y: number } | null = null;
    let bestD = Infinity;
    for (const key of this.stationTiles) {
      const [sx, sy] = key.split(",").map(Number);
      const d = Math.max(Math.abs(sx - this.selfX), Math.abs(sy - this.selfY));
      if (d <= STATION_INTERACT_RANGE && d < bestD) {
        bestD = d;
        best = { x: sx, y: sy };
      }
    }
    return best;
  }

  /** Per-frame: recompute the nearest station, show/hide + bob the "E"
   *  bubble, and swap the CSS cursor while hovering the station tile.
   *  The bubble FADES in/out (appear/disappear) instead of popping. */
  private updateStationPrompt(): void {
    this.nearestStation = this.findNearestStation();
    const st = this.nearestStation;
    if (st) this.lastPromptPos = st;
    // The bubble is suppressed while the station panel is open — it only
    // comes back after the player closes the panel (user rule).
    const target = st && !this.suppressPrompt ? 1 : 0;
    // Exponential fade toward the target (~0.25s in/out), plus a slight
    // upward drift while disappearing.
    this.promptAlpha += (target - this.promptAlpha) * Math.min(1, this.frameDtSec * 9);
    if (this.promptAlpha < 0.015) {
      this.promptAlpha = 0;
      this.stationPrompt?.setVisible(false);
    } else if (this.stationPrompt) {
      const p = this.lastPromptPos ?? { x: 0, y: 0 };
      const now = performance.now();
      const bob = Math.sin(now / 300) * 2; // gentle 2px float
      const rise = (1 - this.promptAlpha) * 8; // drifts up while fading out
      const sincePunch = now - this.promptPunchAt;
      const punch = sincePunch < 160 ? 1 + 0.35 * (1 - sincePunch / 160) : 1;
      this.stationPrompt.setPosition(p.x * this.tilePx + this.tilePx / 2, p.y * this.tilePx - 24 + bob + rise);
      this.stationPrompt.setScale(punch);
      this.stationPrompt.setAlpha(this.promptAlpha);
      this.stationPrompt.setVisible(true).setDepth(150);
    }
    if (!st || this.suppressPrompt) {
      if (this.hoverStationCursor) {
        this.hoverStationCursor = false;
        this.game.canvas.style.cursor = "";
      }
      if (st && this.suppressPrompt) return; // still in range, panel open
    }
    if (!st) return;
    // Bubble (lazy-built): pixel "E" in a dark rounded box + tail.
    if (!this.stationPrompt) this.stationPrompt = this.buildStationPrompt();
    // Hover cursor (Kaetram parity): crafting cursor over the station tile.
    const hovering = !!this.mouseTile &&
      this.mouseTile.x === st.x && this.mouseTile.y === st.y;
    if (hovering !== this.hoverStationCursor) {
      this.hoverStationCursor = hovering;
      this.game.canvas.style.cursor = hovering
        ? "url('ui/cursors/crafting.png') 8 8, pointer"
        : "";
    }
  }

  private hoverStationCursor = false;
  /** Bubble fade alpha (0..1) — eased per frame. */
  private promptAlpha = 0;
  /** Last tile that had a bubble (fades out in place when out of range). */
  private lastPromptPos: { x: number; y: number } | null = null;
  /** True while the station panel is open — the bubble hides until the
   *  player closes it (set via setPromptSuppressed). */
  private suppressPrompt = false;

  /** Show/hide the station bubble while the craft panel is open/closed. */
  setPromptSuppressed(on: boolean): void {
    this.suppressPrompt = on;
  }

  /** Build the pixel "E" prompt bubble (dark box + tail + letter). */
  private buildStationPrompt(): Phaser.GameObjects.Container {
    const W = 22, H = 22, R = 4;
    const g = this.add.graphics();
    g.fillStyle(0x1c1a17, 0.92);
    g.fillRoundedRect(-W / 2, -H / 2, W, H, R);
    g.lineStyle(2, 0xd8b46a, 1);
    g.strokeRoundedRect(-W / 2, -H / 2, W, H, R);
    // Tail pointing down at the station.
    g.fillStyle(0x1c1a17, 0.92);
    g.fillTriangle(-4, H / 2 - 1, 4, H / 2 - 1, 0, H / 2 + 5);
    const label = this.add.text(0, 0, "E", {
      fontFamily: "Verdana, sans-serif",
      fontSize: "14px",
      color: "#f0e6c8",
      fontStyle: "bold",
    }).setOrigin(0.5);
    return this.add.container(0, 0, [g, label]);
  }

  /** E pressed (or station clicked): on OPEN the bubble dissolves into an
   *  explosion burst, then the panel opens. A close-press (panel already
   *  open) is silent — no explosion, the bubble just fades back. */
  stationInteract(): void {
    if (!this.nearestStation) return;
    if (this.suppressPrompt) {
      // Panel already open: silent close (main.ts toggles it shut).
      this.onStationInteract?.();
      return;
    }
    this.promptPunchAt = performance.now();
    const p = this.nearestStation;
    // Kill the bubble instantly — the explosion takes its place. The
    // suppression flag keeps it hidden until the panel closes.
    this.promptAlpha = 0;
    this.stationPrompt?.setVisible(false);
    this.suppressPrompt = true;
    this.spawnPromptExplosion(p.x * this.tilePx + this.tilePx / 2, p.y * this.tilePx - 24);
    this.onStationInteract?.();
  }

  /** Golden spark EXPLOSION at the bubble position (E press): radial
   *  particles + an expanding ring flash. Pure cosmetic, self-cleaning. */
  private spawnPromptExplosion(x: number, y: number): void {
    const N = 12;
    for (let i = 0; i < N; i++) {
      const a = (i / N) * Math.PI * 2 + Math.random() * 0.6;
      const dist = 16 + Math.random() * 16;
      const size = 2 + Math.random() * 2;
      const spark = this.add.rectangle(x, y, size, size,
        i % 3 === 0 ? 0xfff2c4 : 0xd8b46a).setDepth(151);
      this.tweens.add({
        targets: spark,
        x: x + Math.cos(a) * dist,
        y: y + Math.sin(a) * dist - 8,
        alpha: 0,
        scale: { from: 1.6, to: 0.3 },
        duration: 380 + Math.random() * 160,
        ease: "Cubic.Out",
        onComplete: () => spark.destroy(),
      });
    }
    // Expanding ring flash.
    const ring = this.add.circle(x, y, 8)
      .setStrokeStyle(2, 0xd8b46a, 1)
      .setDepth(151);
    this.tweens.add({
      targets: ring,
      scale: 3.2,
      alpha: 0,
      duration: 360,
      ease: "Cubic.Out",
      onComplete: () => ring.destroy(),
    });
  }

  /** True when a station is within interact range (E-key gate). */
  nearStation(): boolean {
    return this.nearestStation !== null;
  }

  /** True when the CURRENT hover tile is a station in range (click router). */
  hoveringStation(tile?: { x: number; y: number } | null): boolean {
    // Optional tile param: the mobile long-press passes the ACTUAL pressed
    // tile — reading this.mouseTile there was always null/stale on touch
    // (touch never moves a mouse cursor), so station interact misfired.
    const t = tile ?? this.mouseTile;
    return !!this.nearestStation && !!t &&
      t.x === this.nearestStation.x &&
      t.y === this.nearestStation.y;
  }

  /**
   * Dominant 8-way direction name from a movement vector. Only used to
   * label the facing for actions (`getSelfDir`) — rendering blends the raw
   * vector, so pressing a diagonal no longer jerks the arm to a cardinal.
   */
  private dominantDir(dx: number, dy: number): string {
    if (Math.abs(dx) > Math.abs(dy)) return dx > 0 ? "EAST" : "WEST";
    if (Math.abs(dy) > Math.abs(dx)) return dy > 0 ? "SOUTH" : "NORTH";
    if (dx > 0) return dy > 0 ? "SOUTH_EAST" : "NORTH_EAST";
    return dy > 0 ? "SOUTH_WEST" : "NORTH_WEST";
  }

  /**
   * Facing smoothing: while moving, the direction vector follows the raw
   * input through a short exponential blend (no discrete 8-way snap — the
   * "giật 1 phát rồi mới final" bug), and when the server reports a
   * different facing while idle we blend toward it too. The HAND dot reads
   * this vector, so it rotates smoothly with the avatar (no arrow needed).
   */
  private updateFacing(): void {
    if (!this.selfMarker) return;
    let targetX = this.lastMoveX;
    let targetY = this.lastMoveY;
    if (this.aimCursor) {
      targetX = this.aimCursor.dx;
      targetY = this.aimCursor.dy;
    }
    const targetLen = Math.hypot(targetX, targetY) || 1;
    targetX /= targetLen;
    targetY /= targetLen;
    // Smoothed facing vector: exponential toward the target each frame
    // (k in 0..1 — 0.25 ≈ settles in ~5 frames, imperceptible but no jerk).
    if (!this.faceVec) this.faceVec = { x: targetX, y: targetY };
    const k = 0.25;
    this.faceVec.x += (targetX - this.faceVec.x) * k;
    this.faceVec.y += (targetY - this.faceVec.y) * k;
  }

  /** Extra hand reach (px) for a swing started at t0, sampled at now. */
  private swingExtra(t0: number, now: number): number {
    if (!t0) return 0;
    const t = (now - t0) / SWING_MS;
    if (t < 0 || t >= 1) return 0;
    return Math.sin(t * Math.PI) * SWING_EXTRA;
  }

  /** Hide the plan-A hand dot + tool icon once the paperdoll body is live
   * (the Kaetram sprite carries its own weapon layer — the orbiting dot
   * would poke out from under the taller 2-tile body). */
  private hideSelfHand(): void {
    this.selfHand?.setVisible(false);
    this.selfToolIcon?.setVisible(false);
  }

  /** Hide the plan-A hand dot + tool icon once the paperdoll body is live
   * (the Kaetram sprite carries its own weapon layer — the orbiting dot
   * would poke out from under the taller 2-tile body). */

  /** Swing the SELF hand at once (optimistic — no server wait). */
  swingSelfHand(): void {
    this.selfDoll?.swing(performance.now());
  }

  /** A Kaetram hand-icon texture just arrived: flip every Text-glyph hand
   * icon (self + remote) to the real pixel art, once per texture. */
  onIconTexture(itemId: string): void {
    if (!this.textures.exists(`icon-${itemId}`)) return;
    if (this.selfToolIcon) this.selfToolIcon = this.applyHandIcon(this.selfToolIcon, this.selfHeld);
    for (const rp of this.players.values()) {
      if (rp.held === itemId) rp.toolIcon = this.applyHandIcon(rp.toolIcon, rp.held);
    }
    // Drop entities of this item: emoji glyph -> real pixel art (smaller
    // than the hand icon — a ground pickup, not a held tool).
    for (const d of this.drops.values()) {
      if (d.itemId === itemId) d.icon = this.applyHandIcon(d.icon, itemId, DROP_ICON_PX);
    }
  }

  /** Swing one REMOTE hand when its harvest progress grows (20 Hz echo). */
  swingRemoteHand(id: number): void {
    const rp = this.players.get(id);
    if (rp) rp.swingT0 = performance.now();
    this.remoteDolls.get(id)?.swing(performance.now());
  }

  /** Remote swing keyed by the ACTOR's id (server "swing" echo): the arc
   *  plays on the attacker's own body — direction from the acted tile. */
  swingRemoteHandAt(id: number, tx: number, ty: number): void {
    const rp = this.players.get(id);
    if (rp) {
      rp.swingT0 = performance.now();
      // Face the acted tile so the arc points where the swing went.
      const dx = tx * this.tilePx + this.tilePx / 2 - rp.container.x;
      const dy = ty * this.tilePx + this.tilePx / 2 - rp.container.y;
      if (Math.abs(dx) > Math.abs(dy)) {
        rp.dir = dx > 0 ? "EAST" : "WEST";
      } else {
        rp.dir = dy > 0 ? "SOUTH" : "NORTH";
      }
    }
    this.remoteDolls.get(id)?.swing(performance.now());
  }

  // -------------------------------------------------- hostiles (zombies)

  /**
   * LEGACY stub (old object-payload zombie renderer, pre realtime-pack).
   * Kept as a no-op so older call sites never crash; the live path is
   * syncZombies() fed by applySnapshot() (WebZombiePayload tuples).
   */
  updateZombies(_zombies?: unknown): void {
    return;
  }

  // -------------------------------------------------- realtime-pack anim

  /**
   * Kaetram hitsplat (renderer/infos/splat.ts): damage number floats UP
   * from the target, fading over ~1s; red for normal, GOLD + bigger for a
   * critical, "MISS" when the hit whiffed. Pure client-side presentation —
   * the server already resolved the damage (rule: deterministic state).
   */
  /** Hitsplat over a PLAYER (victim of zombie bites etc.): resolves the
   * player's current tile from the authoritative state. */
  // ---- EATING FX (user feature) ----
  // Chew: colored crumbs (the item's icon color) burst around the player's
  // head while the server's eating flag is on; heal burst: the Kaetram
  // heal.png 8-frame sparkle plays over the player when the chew completes.
  private chewEmitters = new Map<number, { emitter: Phaser.GameObjects.Particles.ParticleEmitter; item: string }>();
  private healBurstT = 0;
  private healSprite: Phaser.GameObjects.Sprite | null = null;
  private eatColors: Record<string, number> = {
    apple: 0xd83a3a, cooked_meat: 0xb5651d, raw_meat: 0xd96a6a,
    rotten_flesh: 0x7a9b4e,
    potion_hp: 0xe04848, potion_mp: 0x4f6fe0, banana: 0xf0d060,
    watermelon: 0x3fae5a, orange: 0xf09030, blueberry: 0x5060c0,
    bread: 0xc89858, cheese: 0xf0c040, carrot: 0xe07020,
  };

  /** Server eating flag changed for self: start/stop the chew crumb burst. */
  setSelfEating(eating: boolean, itemId: string | null): void {
    const self = this.selfMarker;
    if (!self) return;
    const selfId = this.welcome?.self.id ?? 0;
    const existing = this.chewEmitters.get(selfId);
    if (eating && !existing && itemId) {
      const color = this.eatColors[itemId] ?? 0xc09050;
      // Texture-less emitters render NOTHING in Phaser 3.90 — paint a tiny
      // white square once and feed it to every crumb emitter.
      if (!this.textures.exists("fx-crum")) {
        const g = this.make.graphics({ x: 0, y: 0 }, false);
        g.fillStyle(0xffffff, 1);
        g.fillRect(0, 0, 4, 4);
        g.generateTexture("fx-crum", 4, 4);
        g.destroy();
      }
      const emitter = this.add.particles(0, 0, "fx-crum", {
        speed: { min: 30, max: 70 },
        angle: { min: 200, max: 340 }, // upward arc from the mouth
        gravityY: 140, // crumbs fall back down — the "chew" feel
        lifespan: 800,
        frequency: 40, // denser stream — crumbs must be unmissable
        scale: { start: 2.2, end: 0 },
        alpha: { start: 1, end: 0 },
        quantity: 2,
        tint: [color, color, 0xffffff, color],
        emitting: false,
      });
      // BELOW actors (doll container depth 6): the player body OVERLAPS the
      // crumbs — particles are a background layer behind the player.
      emitter.setDepth(5);
      this.chewEmitters.set(selfId, { emitter, item: itemId });
      emitter.start();
    } else if (!eating && existing) {
      existing.emitter.stop();
      this.time.delayedCall(900, () => existing.emitter.destroy());
      this.chewEmitters.clear();
    } else if (existing) {
      // Follow the player (mouth position, ~head height).
      existing.emitter.setPosition(self.x, self.y - 34);
    }
  }

  /** Per-frame: keep chew emitters glued to the player's mouth. */
  private updateChew(): void {
    const self = this.selfMarker;
    if (!self) return;
    // Mouth offset per facing (user-tuned): back views keep their tuned
    // spot; the other three axes sit 6px lower — front drops to +1 right,
    // left-facing shifts 2px left, right-facing keeps its x.
    const FRONT = { x: 1, y: 23 };
    const BACK = { x: -3, y: 10 };
    const dirOffsets: Record<string, { x: number; y: number }> = {
      SOUTH: FRONT,
      SE: FRONT,
      SW: FRONT,
      NORTH: BACK,
      NE: BACK,
      NW: BACK,
      EAST: { x: 0, y: 23 },
      WEST: { x: -2, y: 23 },
    };
    const off = dirOffsets[this.selfDir] ?? FRONT;
    // Layer per facing: BACK views put the emitter BEHIND the player
    // (depth 5, body overlaps crumbs); every other facing keeps the crumbs
    // in front (depth 400).
    const behind = this.selfDir === "NORTH" || this.selfDir === "NE" || this.selfDir === "NW";
    for (const e of this.chewEmitters.values()) {
      e.emitter.setPosition(self.x + off.x, self.y - 34 + off.y);
      e.emitter.setDepth(behind ? 5 : 400);
    }
  }

  /** The server says an eat just completed: play the Kaetram heal sparkles
   *  + a green "+heal" splat. Called from the snapshot loop. */
  playHealBurst(at: number): void {
    if (at <= this.healBurstT) return; // already seen this burst
    this.healBurstT = at;
    const self = this.selfMarker;
    if (!self) return;
    // Green heal splat (Kaetram: "++" prefix for points).
    this.spawnSplatAt(
      Math.floor(self.x / this.tilePx), Math.floor(self.y / this.tilePx), "+", "#6fe26f", "#1d5c22",
    );
    // Kaetram heal.png: 8 frames of 48x48 — register once, play once.
    if (!this.textures.exists("fx-heal")) {
      const img = new Image();
      img.onload = () => {
        if (!this.textures.exists("fx-heal") && this.textures) {
          this.textures.addSpriteSheet("fx-heal", img, { frameWidth: 48, frameHeight: 48 });
          this.playHealFrames(self.x, self.y);
        }
      };
      img.src = "ui/fx/heal.png";
    } else {
      this.playHealFrames(self.x, self.y);
    }
  }

  private playHealFrames(x: number, y: number): void {
    if (!this.textures.exists("fx-heal")) return;
    this.healSprite?.destroy();
    const s = this.add.sprite(x, y - 8, "fx-heal", 0).setDepth(160);
    s.setScale(1.2);
    this.healSprite = s;
    this.anims.create({
      key: "fx-heal-play",
      frames: this.anims.generateFrameNumbers("fx-heal", { start: 0, end: 7 }),
      frameRate: 14,
      hideOnComplete: true,
    });
    s.play("fx-heal-play");
    s.once("animationcomplete", () => {
      s.destroy();
      if (this.healSprite === s) this.healSprite = null;
    });
  }

  spawnSplatOnPlayer(userId: number, damage: number): void {
    let tx: number | null = null;
    let ty: number | null = null;
    if (userId === this.welcome?.self.id) {
      tx = Math.floor(this.selfServerPos.x);
      ty = Math.floor(this.selfServerPos.y);
    } else {
      const rp = this.players.get(userId);
      if (rp) {
        tx = Math.floor(rp.container.x / this.tilePx);
        ty = Math.floor(rp.container.y / this.tilePx);
      }
    }
    // Purple-red to distinguish INCOMING damage from player-dealt red.
    if (tx !== null && ty !== null) this.spawnSplatAt(tx, ty, String(damage), "#ff5bd6", "#5a1048");
  }

  /** Status-effect DOT splat: BLUE (the icon's colour, user 25/09) so
   *  poison/infection ticks read differently from bites (purple-red) and
   *  player-dealt damage (red). */
  spawnStatusSplat(userId: number, damage: number): void {
    let tx: number | null = null;
    let ty: number | null = null;
    if (userId === this.welcome?.self.id) {
      tx = Math.floor(this.selfServerPos.x);
      ty = Math.floor(this.selfServerPos.y);
    } else {
      const rp = this.players.get(userId);
      if (rp) {
        tx = Math.floor(rp.container.x / this.tilePx);
        ty = Math.floor(rp.container.y / this.tilePx);
      }
    }
    if (tx !== null && ty !== null) this.spawnSplatAt(tx, ty, String(damage), "#4db8ff", "#0f2a4a");
  }

  private spawnSplatAt(tx: number, ty: number, text: string, fill: string, stroke: string): void {
    const txt = this.add.text(tx * this.tilePx + this.tilePx / 2, ty * this.tilePx - 4, text, {
      fontSize: "12px",
      fontStyle: "bold",
      color: fill,
      stroke: stroke,
      strokeThickness: 3,
      fontFamily: "monospace",
    }).setOrigin(0.5).setDepth(900);
    this.tweens.add({
      targets: txt,
      y: txt.y - 22,
      alpha: { from: 1, to: 0 },
      duration: 900,
      ease: "Cubic.Out",
      onComplete: () => txt.destroy(),
    });
  }

  spawnSplat(tx: number | null, ty: number | null, damage: number, critical: boolean, missed: boolean): void {
    if (tx === null || ty === null) return;
    const text = missed || damage <= 0 ? "MISS" : String(damage);
    const fill = missed ? "#cfd6e4" : critical ? "#ffd75e" : "#ff3232";
    const stroke = missed ? "#2a2f3a" : critical ? "#7a5b00" : "#ffb4b4";
    const txt = this.add.text(tx * this.tilePx + this.tilePx / 2, ty * this.tilePx - 4, text, {
      fontSize: critical ? "15px" : "12px",
      fontStyle: critical ? "bold" : "bold",
      color: fill,
      stroke: stroke,
      strokeThickness: 3,
    }).setOrigin(0.5).setDepth(120);
    this.splats.add({ txt, t0: performance.now(), x: txt.x, y: txt.y });
  }

  /** Per-frame splat float + fade (Kaetram: 1px/100ms, 1s duration). */
  private updateSplats(now: number): void {
    for (const s of this.splats) {
      const age = now - s.t0;
      if (age > 1000) {
        s.txt.destroy();
        this.splats.delete(s);
        continue;
      }
      s.txt.setPosition(s.x, s.y - (age / 100) * 1);
      s.txt.setAlpha(Math.max(0, 1 - age / 1000));
    }
  }

  /**
   * Combat swing: a bigger, faster arc than the harvest one — called on
   * every attack action (F key / left click) regardless of target.
   */
  combatSwing(): void {
    this.swingSelfHand();
  }
  /**
   * Sync the resource layer with the server's visible-tile list.
   *
   * Nodes that VANISHED since the previous sync (felled) play a falling
   * animation: shake -> tilt -> fade on the whole node's tile images.
   * The anchor of each node is derived from the known node map the server
   * also sends in res_progress's bbox — a vanished tile belongs to the node
   * whose bbox contained it in the previous snapshot.
   */
  updateResourceLayer(tiles: [number, number, number][]): void {
    // MAP ID IS PART OF THE SIGNATURE (user 16/09): the layer lives ABOVE the
    // baked ground (depth -5 vs -10), and buildWorld bumps resourceSig to ""
    // on a map switch. A destination map with ZERO resources (the trade
    // interior) therefore computed sig "" == resourceSig "" and the guard
    // skipped the rebuild — leaving the PREVIOUS map's grass/flowers sprites
    // painted over the interior while the real interior collision blocked the
    // player ("thấy cỏ hoa nhưng không đi xuyên được"). Keying by map forces
    // the clear on every switch, including into an empty map.
    const sig = `${this.welcome?.map.id ?? ""}|`
      + tiles.map((t) => t.join(",")).join(";");
    if (sig === this.resourceSig) return;
    this.resourceSig = sig;

    // Remember the previous tile->node grouping for the fall animation:
    // any node whose bbox tiles disappeared entirely just got felled.
    const prevNodes = this.collectNodesFromTiles(
      [...this.resourceTiles.entries()].map(([k]) => k),
    );

    if (this.resourceLayer) {
      this.resourceLayer.removeAll(true);
    } else {
      this.resourceLayer = this.add.layer().setDepth(-5);
    }
    this.resourceTiles.clear();
    // Meteor ore (gid -77): ONE big sprite covering the whole 2x2 node —
    // four identical per-tile sprites read as four separate rocks (user:
    // "spawn ra 1 cục thôi"). Tiles still map to the image in resourceTiles
    // so felled-node grouping stays uniform with other multi-tile nodes.
    const met = tiles.filter((t) => t[2] === -77);
    const rest = tiles.filter((t) => t[2] !== -77);
    if (met.length > 0 && this.textures.exists("node-meteor_ore")) {
      const pending = new Set(met.map(([x, y]) => `${x},${y}`));
      const anchors = met
        .map(([x, y]) => [x, y] as [number, number])
        .sort((a, b) => a[0] - b[0] || a[1] - b[1])
        .filter(([x, y]) => !pending.has(`${x - 1},${y}`) && !pending.has(`${x},${y - 1}`));
      for (const [ax, ay] of anchors) {
        const img = this.add.image(
          (ax + 1) * this.tilePx, (ay + 1) * this.tilePx, "node-meteor_ore",
        );
        // 2x2 node drawn at 80% size (user: "giảm kích thước quặng đi 20%")
        // — reads smaller than the full footprint without leaving gaps.
        img.setDisplaySize(this.tilePx * 2 * 0.8, this.tilePx * 2 * 0.8);
        // IMPACT GATE: the ore only becomes visible once the meteor FX has
        // finished exploding near this spot. Without it the snapshot delivers
        // the new node while the fall/boom is still playing and the rock
        // "popped in" before the explosion (user bug report).
        if (meteorFxBusyNear(ax, ay)) {
          img.setAlpha(0);
          this.pendingOreReveal.set(`${ax},${ay}`, img);
        }
        this.resourceLayer.add(img);
        for (const [tx, ty] of [[ax, ay], [ax + 1, ay], [ax, ay + 1], [ax + 1, ay + 1]] as Array<[number, number]>) {
          const k = `${tx},${ty}`;
          if (pending.delete(k)) this.resourceTiles.set(k, img);
        }
      }
      for (const k of pending) {
        const [tx, ty] = k.split(",").map(Number);
        const img = this.add.image(tx * this.tilePx + this.tilePx / 2, ty * this.tilePx + this.tilePx / 2, "node-meteor_ore");
        this.resourceLayer.add(img);
        this.resourceTiles.set(k, img);
      }
    } else {
      for (const t of met) rest.push(t as [number, number, number]);
    }
    for (const [x, y, gid] of rest) {
      const texKey = this.textureForGid(gid);
      if (!texKey) continue;
      const img = this.add.image(x * this.tilePx + this.tilePx / 2, y * this.tilePx + this.tilePx / 2, texKey);
      this.resourceLayer.add(img);
      this.resourceTiles.set(`${x},${y}`, img);
    }

    // Felled nodes: their tiles were present before, gone now -> animate.
    // Field forage (mushrooms/grass/flowers — small 1-tile gids) SHATTER
    // into sand grains; trees/bushes/rocks keep the tip-over fall.
    const nowKeys = new Set([...this.resourceTiles.keys()]);
    for (const [, imgs] of prevNodes) {
      const stillThere = imgs.every((img) => nowKeys.has(this.keyOf(img)));
      if (imgs.length > 0 && !stillThere) {
        if (imgs.every((i) => FORAGE_GIDS.has(this.gidOf(i)))) {
          this.playShatterAnimation(imgs);
        } else {
          this.playFallAnimation(imgs);
        }
      }
    }
    // Progress bars of vanished nodes are stale.
    this.syncProgressBars({});
  }

  /** World key of a resource image ("x,y" from its centre position). */
  private keyOf(img: Phaser.GameObjects.Image): string {
    const half = this.tilePx / 2;
    return `${Math.round((img.x - half) / this.tilePx)},${Math.round((img.y - half) / this.tilePx)}`;
  }

  /** Gid the resource image was cropped from ("res-<gid>" texture). */
  private gidOf(img: Phaser.GameObjects.Image): number {
    const m = /^res-(\d+)$/.exec(img.texture.key);
    return m ? Number(m[1]) : 0;
  }

  /**
   * Group tile keys into nodes using the res_progress bboxes the server
   * sends ("ax,ay" -> [hits, needed, [[x,y],...]]). Falls back to singletons
   * when no bbox is known yet (first snapshot before any swing).
   */
  private collectNodesFromTiles(
    keys: string[],
  ): Map<string, Phaser.GameObjects.Image[]> {
    const out = new Map<string, Phaser.GameObjects.Image[]>();
    const claimed = new Set<string>();
    for (const [anchor, _p] of this.lastProgressRaw) {
      const bbox = this.lastProgressBbox.get(anchor);
      if (!bbox) continue;
      const imgs: Phaser.GameObjects.Image[] = [];
      for (const [bx, by] of bbox) {
        const img = this.resourceTiles.get(`${bx},${by}`);
        if (img) {
          imgs.push(img);
          claimed.add(`${bx},${by}`);
        }
      }
      if (imgs.length > 0) out.set(anchor, imgs);
    }
    for (const k of keys) {
      if (!claimed.has(k)) {
        const img = this.resourceTiles.get(k);
        if (img) out.set(k, [img]);
      }
    }
    return out;
  }

  /**
   * Field-forage shatter: the sprite crumbles into a few sand grains that
   * puff outward and settle — deliberately SPARSE (5-7 grains) and short
   * (~0.5s) so rapid foraging never floods the screen (user rule: don't
   * repeat the eat-particle spam). Grains take the node's own color.
   */
  private playShatterAnimation(imgs: Phaser.GameObjects.Image[]): void {
    if (imgs.length === 0) return;
    for (const img of imgs) {
      const gx = img.x;
      const gy = img.y;
      const tint = this.sampleTextureTint(img.texture.key);
      // The sprite itself: quick shrink + slight downward puff (crumbles).
      this.tweens.add({
        targets: img,
        scaleY: 0.4,
        scaleX: 1.12,
        alpha: 0,
        duration: 200,
        ease: "Quad.In",
        onComplete: () => img.destroy(),
      });
      // Sparse grains (+40% over the first pass): 8 per tile, wider arcs,
      // slightly bigger grains — still far from eat-particle spam.
      const N = 8;
      for (let i = 0; i < N; i++) {
        const a = -Math.PI / 2 + (i - (N - 1) / 2) * 0.5; // fan upward
        const dist = 11 + Math.random() * 14;
        const size = 2 + Math.random() * 2;
        const grain = this.add.rectangle(gx + (Math.random() - 0.5) * 10,
          gy + (Math.random() - 0.5) * 8 - 4, size, size, tint ?? 0xb9a27a)
          .setDepth(9);
        this.tweens.add({
          targets: grain,
          x: grain.x + Math.cos(a) * dist,
          y: grain.y + Math.abs(Math.sin(a)) * dist + 10, // pop up, settle down
          alpha: { from: 1, to: 0 },
          duration: 380 + Math.random() * 140,
          ease: "Quad.Out",
          onComplete: () => grain.destroy(),
        });
      }
    }
  }

  /** Dominant opaque color of a cropped "res-<gid>" canvas texture (the
   *  grain tint), or null when unreadable. */
  private sampleTextureTint(key: string): number | null {
    if (!this.textures.exists(key)) return null;
    const src = this.textures.get(key).getSourceImage() as HTMLCanvasElement;
    if (!src || !src.width) return null;
    const c = document.createElement("canvas");
    c.width = src.width; c.height = src.height;
    const ctx = c.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(src, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let r = 0, g = 0, b = 0, n = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] < 128) continue;
      r += d[i]; g += d[i + 1]; b += d[i + 2]; n++;
    }
    if (n === 0) return null;
    return ((r / n) << 16) | ((g / n) << 8) | (b / n);
  }

  /**
   * Kaetram-style fell: the whole tree shakes, tilts ~14deg around its
   * base, and fades — 450ms, then the images are destroyed.
   */
  private playFallAnimation(imgs: Phaser.GameObjects.Image[]): void {
    if (imgs.length === 0) return;
    // Base line = lowest row of the node (trees tip over from the stump).
    const baseY = Math.max(...imgs.map((i) => i.y));
    for (const img of imgs) {
      // Bring above everything so the fall reads clearly.
      this.resourceLayer?.add(img);
      img.setDepth(8);
      this.tweens.add({
        targets: img,
        x: img.x + (Math.random() < 0.5 ? -2 : 2),
        duration: 60,
        yoyo: true,
        repeat: 2,
        ease: "Sine.InOut",
        onComplete: () => {
          this.tweens.add({
            targets: img,
            angle: img.x < this.selfX * this.tilePx ? 12 : -12,
            y: baseY + 4,
            alpha: 0,
            duration: 280,
            ease: "Quad.In",
            onComplete: () => img.destroy(),
          });
        },
      });
    }
  }

  /**
   * Find (or lazily crop) the per-tile texture for a gid. The tileset texture
   * is the WHOLE sheet — using it directly would draw the entire raw tileset
   * on every resource tile. Instead crop the tile into its own canvas texture
   * once ("res-<gid>") and reuse it.
   */
  private textureForGid(gid: number): string | null {
    const map = this.welcome?.map;
    if (!map) return null;
    // CACHE KEY INCLUDES THE MAP ID (user 25/09): the same gid means
    // DIFFERENT art on different maps (bigmap gid 41 = cây on the [Base]
    // sheet; cave gid 41 = nấm on ekonia_baked). A bare "res-<gid>" key
    // kept the cave's crop alive after walking back to bigmap — resource
    // tiles rendered the previous map's art ("cái title của bigmap bị
    // hiển thị lỗi"). Keying by map.id makes each world's crops distinct.
    const cacheKey = `res-${map.id}-${gid}`;
    if (this.textures.exists(cacheKey)) return cacheKey;
    // Meteor-ore pseudo-tiles (NEGATIVE gids, spawned at meteor craters):
    // no Tiled tileset crop exists for them — they draw the bundled
    // ui/node/meteor_ore.png sprite fetched through the asset lane.
    if (gid < 0) {
      if (gid === -77) {
        // main.ts registers node assets as "node-<basename-without-.png>"
        // (underscores preserved): meteor_ore.png -> "node-meteor_ore".
        if (!this.textures.exists("node-meteor_ore")) {
          this.assetFetch?.("node/meteor_ore.png");
          return null; // retry on the next layer refresh once bytes arrive
        }
        return "node-meteor_ore";
      }
      return null;
    }
    const tw = map.tile_width;
    const th = map.tile_height;
    const ts = this.tilesetForGid(map, gid);
    if (!ts?.image) return null;
    const sheetKey = this.tileTextures.get(ts.image);
    if (!sheetKey || !this.textures.exists(sheetKey)) return null;
    const src = this.textures.get(sheetKey).getSourceImage() as HTMLImageElement;
    if (!src || !src.width) return null;
    const local = gid - ts.firstgid;
    const col = local % ts.columns;
    const rowIdx = Math.floor(local / ts.columns);
    const tileW = ts.tilewidth ?? tw;
    const tileH = th;
    if ((col + 1) * tileW > src.width || (rowIdx + 1) * tileH > src.height) return null;
    const canvas = document.createElement("canvas");
    canvas.width = tileW;
    canvas.height = tileH;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(src, col * tileW, rowIdx * tileH, tileW, tileH, 0, 0, tileW, tileH);
    this.textures.addCanvas(cacheKey, canvas);
    return cacheKey;
  }

  /**
   * Sync the per-node progress bars (+ hand swings on new hits).
   *
   * progress: "ax,ay" -> [hits, base_needed, bbox]. The bar spans the
   * node's WHOLE bbox (a 2x2 tree -> 64px wide bar centred on the tree,
   * not a 30px sliver on one tile), and the fill width animates to
   * hits/needed each update (needed prefers the tool-adjusted count from
   * the action_result echo).
   */
  syncProgressBars(
    progress: Record<string, [number, number, number[][]]>,
    _selfTile?: { x: number; y: number }, // kept for call-site compat (unused)
  ): void {
    // Progress-bar sync ONLY. It must NOT trigger swing animations —
    // "who is hitting this node" is unknowable from a hit counter (a
    // neighbor standing near MY tree got animated as the harvester).
    // Actor swings arrive as server "swing" echoes keyed by uid
    // (swingRemoteHandAt) + the client-optimistic self swing.
    this.lastProgressRaw = new Map(Object.entries(progress));
    this.lastProgressBbox.clear();
    for (const [anchor, entry] of this.lastProgressRaw) {
      this.lastProgressBbox.set(anchor, entry[2]);
    }
    const sig = JSON.stringify(progress);
    if (sig === this.lastResProgress) return;
    this.lastResProgress = sig;

    const wanted = new Set(Object.keys(progress));
    for (const [key, bar] of this.progressBars) {
      if (!wanted.has(key)) {
        bar.destroy();
        this.progressBars.delete(key);
        this.progressFills.delete(key);
        // Stale tool-adjusted count must not poison the node's NEXT life.
        this.neededByAnchor.delete(key);
      }
    }
    for (const [key, entry] of Object.entries(progress)) {
      const hits = entry[0];
      if (hits <= 0) continue;
      const bbox = entry[2];
      // Node bbox in pixels -> bar spans the whole sprite, centred.
      const xs = bbox.map(([bx]) => bx);
      const ys = bbox.map(([, by]) => by);
      const minX = Math.min(...xs) * this.tilePx;
      const maxX = (Math.max(...xs) + 1) * this.tilePx;
      const maxY = (Math.max(...ys) + 1) * this.tilePx;
      const cx = (minX + maxX) / 2;
      const width = Math.min(72, Math.max(30, maxX - minX - 8));
      const needed = this.neededByAnchor.get(key) ?? entry[1] ?? 4;
      const ratio = Math.max(0, Math.min(1, hits / Math.max(1, needed)));

      let bar = this.progressBars.get(key);
      let fill = this.progressFills.get(key);
      const isNew = !bar || !fill;
      if (isNew) {
        bar = this.add.container(cx, maxY - 6);
        // Fresh bar starts at ZERO width — never lerps from a previous
        // node's leftover value (the old green-flash bug).
        fill = this.add.rectangle(-width / 2, 0, 2, 5, 0x6fe26f)
          .setOrigin(0, 0.5);
        const bg = this.add.rectangle(0, 0, width, 7, 0x000000, 0.6);
        const border = this.add.rectangle(0, 0, width, 7)
          .setStrokeStyle(1, 0xffffff, 0.35);
        bar.add([bg, fill, border]);
        bar.setDepth(20);
        this.progressBars.set(key, bar);
        this.progressFills.set(key, fill);
      }
      // Smooth fill toward the new ratio (no snap on each swing).
      this.tweens.add({
        targets: fill,
        width: Math.max(2, (width - 4) * ratio),
        duration: 120,
        ease: "Sine.Out",
      });
      // One-shot "thunk" bounce on each LANDED hit (not on creation — the
      // creation bounce was the blink the user saw).
      if (!isNew) {
        this.tweens.add({
          targets: bar,
          scaleY: { from: 1.25, to: 1 },
          duration: 110,
          ease: "Quad.Out",
        });
      }
    }
  }

  /** Remember the tool-adjusted needed count from an action_result echo. */
  noteChopNeeded(tx: number | null, ty: number | null, needed: number | null): void {
    if (tx === null || ty === null || !needed) return;
    // The echo lands on the clicked tile; resolve it to the node anchor via
    // the known bboxes (any bbox containing the tile claims it).
    for (const [anchor, bbox] of this.lastProgressBbox) {
      if (bbox.some(([bx, by]) => bx === tx && by === ty)) {
        this.neededByAnchor.set(anchor, needed);
        return;
      }
    }
    this.neededByAnchor.set(`${tx},${ty}`, needed);
  }

  /** True when the zombie facing needs the RIGHT row mirrored (Kaetram
   * ships no left rows — left = flipX of right). Minifolks wildlife ships
   * ONE facing per sheet (rows up/down duplicate right), so up/down NEVER
   * flips — only W/NW/SW mirrors. */
  private zombieFlipX(facing: string, kind?: string): boolean {
    const f = (facing || "S").toUpperCase();
    const left = f === "W" || f === "NW" || f === "SW";
    if (kind && MOB_SHEETS[kind]?.singleFacing) return left;
    return left;
  }

  /** Sheet-facing suffix (right/up/down) for a non-mirrored zombie facing. */
  private zombieRowFacing(facing: string, kind?: string): "right" | "up" | "down" {
    const f = (facing || "S").toUpperCase();
    if (kind && MOB_SHEETS[kind]?.singleFacing) return "right";
    if (f === "N" || f === "NE" || f === "NW") return "up";
    if (f === "E" || f === "NE" || f === "SE") return "right";
    return "down"; // S, SE, SW
  }

  /** Show ONE 32px cell of a mob spritesheet at the requested display size.
   * The texture registers as a spritesheet (frameWidth/Height = 32), so
   * setFrame gives every cell its own cut + origin — unlike setCrop on the
   * full-sheet Image, which kept the quad at sheet size and drew cells
   * offset sideways, shifting position on every frame change. */
  /** Cut one animation cell from a mob sheet. Cell size comes from the
   * MOB_SHEETS registry (Kaetram sprites.json) — sheets are NOT all 32px
   * (skeleton 48, spider 35, bat 32x48), so a hard-coded 32 mis-cropped
   * every non-zombie mob. */
  private applyMobCell(img: Phaser.GameObjects.Image, col: number, row: number, size: number, cellW = 32, cellH = 32): void {
    const tex = this.textures.get(img.texture.key);
    const perRow = Math.max(1, Math.floor(tex.source[0].width / cellW));
    img.setFrame(row * perRow + col);
    img.setScale(size / cellH);
  }

  /** A mob sprite PNG arrived via the relay: mark ready for upgrade. */
  onMobTexture(name: string): void {
    if (!name.startsWith("mobs/")) return;
    const kind = name.slice("mobs/".length).replace(/\.png$/i, "");
    if (!MOB_SHEETS[kind] || this.mobTextureReady.has(kind)) return;
    if (!this.textures.exists(MOB_SHEETS[kind].texKey)) return;
    this.mobTextureReady.add(kind);
  }

  /** Bundled node sprite arrived (meteor-ore crater rock): register the
   *  texture and rebuild the resource layer so pseudo-tile draws (negative
   *  gids) pick it up. Mirrors onMobTexture but for plain images. */
  onNodeTexture(_nodeId: string): void {
    if (!this.textures) return;
    // The texture was already registered by applyTexture under
    // "node-<id>"; textureForGid maps gid -77 -> "node-meteor-ore".
    this.resourceSig = "";
    if (this.welcome) this.updateResourceLayer(this.welcome.resources);
  }

  /** Sync the zombie layer from one snapshot payload (20 Hz). */
  private syncZombies(list: WebZombiePayload[]): void {
    // Snapshot can arrive before the scene is fully booted; bail out instead
    // of crashing on this.add (it is undefined pre-boot).
    if (!this.add || !this.scene) return;
    const seen = new Set<string>();
    for (const [id, x, y, hp, maxHp, kind, hunter, facing, anim, animT] of list) {
      seen.add(id);
      const kindKey = MOB_SHEETS[kind] ? kind : "zombie";
      const sheet = MOB_SHEETS[kindKey];
      const ready = this.mobTextureReady.has(kindKey) &&
        this.textures.exists(sheet.texKey);
      let z = this.zombies.get(id);
      if (!z) {
        if (!this.zombieLayer) this.zombieLayer = this.add.layer();
        const container = this.add.container(x * this.tilePx, y * this.tilePx);
        const body: Phaser.GameObjects.Image | Phaser.GameObjects.Rectangle =
          ready
            ? this.add.image(0, 0, sheet.texKey)
            : this.add.rectangle(0, 0, 22, 26, 0x3a7d2c);
        // FEET ANCHOR (player parity): the player doll's FEET sit at the
        // container's tile-bottom; the old mob body (origin 0.5 centred on
        // the tile centre) spilled ~half a tile SOUTH into the next row —
        // a mob just north of a tree visually stomped OVER the trunk.
        // dy shifts the body so its bottom edge lands on the tile bottom.
        const bodyY = ready ? this.tilePx / 2 - sheet.size / 2 : 4;
        // Cut the FIRST idle frame immediately so a fresh spawn never shows
        // the whole stretched sheet for even one frame.
        if (body instanceof Phaser.GameObjects.Image) {
          body.setPosition(0, bodyY);
          this.applyMobCell(body, 0, sheet.rows.idle.down[0], sheet.size, sheet.cellW, sheet.cellH);
        }
        // No emoji label under mobs (user request): the sprite + hp bar are
        // enough; the container still needs a placeholder for typing.
        const label = this.add.text(0, 24, "", {});
        // HP bar hovers just above the VISIBLE art (artH), not the raw cell
        // top — the bear's verbatim cell is 2 tiles tall, mostly transparent;
        // a cell-anchored bar floated a tile above its back.
        const barH = (sheet as { artH?: number }).artH ?? sheet.size;
        const barY = bodyY + sheet.size / 2 - barH - 5;
        const hpBg = this.add.rectangle(0, barY, 28, 4, 0x000000, 0.6);
        const hpFill = this.add.rectangle(0, barY, 28, 4, 0x6fe26f).setOrigin(0.5);
        container.add([body as Phaser.GameObjects.GameObject, label, hpBg, hpFill]);
        container.setDepth(5);
        this.zombieLayer.add(container);
        z = {
          container, body, label, hpBg, hpFill,
          buf: [], lastX: x * this.tilePx, lastY: y * this.tilePx,
          anim: anim ?? "idle", animT0: performance.now(),
          serverAnimT: animT ?? 0,
          frame: 0, frameT0: performance.now(),
          facing: facing ?? "S", dieT0: 0, hunter: hunter === "hunter",
          kind: kindKey,
        };
        this.zombies.set(id, z);
      }
      const now = performance.now();
      z.buf.push([now, x * this.tilePx, y * this.tilePx]);
      if (z.buf.length > 12) z.buf.shift();
      z.hunter = hunter === "hunter";
      z.facing = facing ?? z.facing;
      // Late-arriving sheet: upgrade the placeholder rect to the sprite.
      if (
        ready &&
        z.body instanceof Phaser.GameObjects.Rectangle
      ) {
        const img = this.add.image(0, 0, sheet.texKey);
        const bodyY = this.tilePx / 2 - sheet.size / 2;
        img.setPosition(0, bodyY);
        this.applyMobCell(img, 0, sheet.rows.idle.down[0], sheet.size, sheet.cellW, sheet.cellH);
        z.container.add(img);
        z.container.sendToBack(img);
        z.body.destroy();
        z.body = img;
      }
      // Restart the anim when (a) the anim STRING changes, or (b) the server
      // re-armed the SAME anim (repeat bites keep anim="atk"; only anim_t
      // advances). Without (b) every bite after the first froze on the lunge
      // frame until the zombie moved again.
      const reArmed = animT != null && z.anim === "atk" && anim === "atk" &&
        Math.abs(animT - z.serverAnimT) > 0.001;
      if (reArmed || (anim ?? z.anim) !== z.anim) {
        z.anim = anim ?? z.anim;
        z.animT0 = now;
        z.frame = 0;
        z.frameT0 = now;
      }
      if (animT != null) z.serverAnimT = animT;
      const atkRows = sheet.rows.atk;
      const atkReplayMs = atkRows.right[1] * 90; // one full atk swing on the client
      // Auto-unfreeze: the server holds anim="atk" between bites; drop back
      // to idle locally once one swing has played so the pose never sticks.
      if (z.anim === "atk" && now - z.animT0 >= atkReplayMs) {
        z.anim = "idle";
        z.frame = 0;
        z.frameT0 = now;
      }
      const ratio = Math.max(0, Math.min(1, maxHp > 0 ? hp / maxHp : 0));
      z.hpFill.setSize(28 * ratio, 4);
      z.hpFill.setFillStyle(ratio > 0.5 ? 0x6fe26f : ratio > 0.25 ? 0xf2c14e : 0xe5484d);
    }
    for (const [id, z] of this.zombies) {
      if (!seen.has(id) && z.dieT0 === 0) {
        z.dieT0 = performance.now() - 400;
      }
    }
  }

  /** Kill echo from action_result: play the death animation for one zombie. */
  noteZombieKill(targetId: string | null | undefined, defeated: boolean | undefined): void {
    if (!targetId || !defeated) return;
    const z = this.zombies.get(targetId);
    if (z && z.dieT0 === 0) z.dieT0 = performance.now();
  }

  /** Track the raw cursor position (normalized 0..1 from DOM events);
   * the TILE is derived fresh every frame from it — the camera moves under
   * a still cursor, so caching the tile itself goes stale. */
  setMouseTile(tile: { x: number; y: number } | null): void {
    this.mouseScreen = tile;
  }

  /** Live mouse tile from the last known cursor position. */
  getMouseTile(): { x: number; y: number } | null {
    if (!this.mouseScreen) return null;
    const t = this.tileFromScreen(this.mouseScreen.x, this.mouseScreen.y);
    this.mouseTile = t;
    return t;
  }

  /** True when a player-placed block stands on this tile. */
  isBlockAt(x: number, y: number): boolean {
    return this.blockSet.has(`${x},${y}`);
  }

  /** Optimistic local collision: a block WE just placed is solid IMMEDIATELY
   * — no waiting for the next snapshot. On a high-RTT link the snapshot
   * staleness window (~0.3-2 tiles of travel at run speed) used to let the
   * player run through their own fresh block. The optimistic entry is ALSO
   * tracked (with a timestamp) so the every-snapshot blockSet rebuild can
   * re-apply it until the action_result echo lands — the rebuild alone
   * would clear our own in-flight block before the server even saw it.
   * Bounded by optimisticTtlMs so a lost echo (clamped target, dropped
   * frame) can never pin a phantom solid tile forever. */
  optimisticPlace(x: number, y: number): void {
    const key = `${x},${y}`;
    this.blockSet.add(key);
    this.optimisticBlocks.set(key, performance.now());
  }

  /** Optimistic local collision for a break we just sent: the block stops
   * blocking movement right away; tracked like optimisticPlace so the
   * snapshot rebuild re-applies the walkable state until the echo confirms
   * or reverts (a rejected break — out of range, already gone — restores
   * the block via reconcileBlockAction, no phantom walkable tile). */
  optimisticBreak(x: number, y: number): void {
    const key = `${x},${y}`;
    this.blockSet.delete(key);
    this.optimisticBreaks.set(key, performance.now());
  }

  /** action_result echo for OUR OWN place/break: confirm/revert the
   * optimistic tile EXACTLY. tx/ty is the tile the server actually acted on
   * (it may have clamped the target or rejected it) — clear the pending
   * entry and restore collision truth the moment the verdict arrives, no
   * waiting for the next snapshot. This is what kills the "đặt rồi xóa rồi
   * chạy xuyên" family: a rejected break used to leave a phantom walkable
   * tile (the block was really still there) until some OTHER block changed. */
  reconcileBlockAction(name: string, ok: boolean, tx: number | null, ty: number | null): void {
    if (tx === null || ty === null) return;
    const key = `${tx},${ty}`;
    if (name === "place") {
      this.optimisticBlocks.delete(key);
      if (!ok) this.blockSet.delete(key); // server said no -> drop the solid phantom NOW
    } else if (name === "break") {
      this.optimisticBreaks.delete(key);
      if (!ok) this.blockSet.add(key); // server kept the block -> solid again NOW
    }
  }

  /** Mirror the server's click-target clamp (web_api/core.py): truncate each
   * axis into AIM_RANGE (3), reject only when the click is genuinely beyond
   * AIM_RANGE + WEB_AIM_RANGE_TOLERANCE (default 1). Keeps the optimistic
   * place/break state on the SAME tile the server will act on — targeting
   * from a stale snapshot used to leave optimistic phantoms on the clicked
   * tile while the server clamped to a different one. Returns null when the
   * server would reject with out_of_range (caller still sends the raw click
   * so the "Quá xa." toast appears honestly). */
  clampClickTile(tile: { x: number; y: number }): { x: number; y: number } | null {
    const sx = Math.floor(this.selfServerPos.x);
    const sy = Math.floor(this.selfServerPos.y);
    let dx = Math.round(tile.x - sx);
    let dy = Math.round(tile.y - sy);
    const LIM = 4; // 3 + WEB_AIM_RANGE_TOLERANCE (default 1)
    if (Math.max(Math.abs(dx), Math.abs(dy)) > LIM) return null;
    if (Math.max(Math.abs(dx), Math.abs(dy)) > 3) {
      dx = Math.max(-3, Math.min(3, dx));
      dy = Math.max(-3, Math.min(3, dy));
    }
    return { x: sx + dx, y: sy + dy };
  }

  /** True when a LIVE zombie sits within attack reach of the clicked tile
   * (zombie tile hit, or a half-tile slack so near-misses still connect).
   * The primary click routes to `attack` first — combat beats chopping. */
  zombieNear(tile: { x: number; y: number }): boolean {
    for (const z of this.zombies.values()) {
      if (z.dieT0 !== 0) continue;
      const zx = z.lastX / this.tilePx;
      const zy = z.lastY / this.tilePx;
      if (Math.hypot(zx - (tile.x + 0.5), zy - (tile.y + 0.5)) <= 0.75) return true;
    }
    return false;
  }

  /** Set the active Build-Mode cursor offset (from the server snapshot). */
  setAimCursor(aim: { dx: number; dy: number } | null): void {
    this.aimCursor = aim;
  }

  getSelfDir(): string {
    return this.selfDir;
  }

  getSelectedBlock(): string {
    return this.selectedBlock;
  }

  setSelectedBlock(id: string): void {
    this.selectedBlock = id;
  }

  /** F3 debug: paint every collision tile RED over the map (viewport-wide
   *  grid, follows the map origin exactly) so solids-vs-art mismatches are
   *  visible in-game without any external tooling. */
  getCollisionDebug(): boolean {
    return this.collisionDebug !== null;
  }

  setCollisionDebug(on: boolean): void {
    if (this.collisionDebug) {
      this.collisionDebug.destroy();
      this.collisionDebug = null;
    }
    if (!on) return;
    // Tile size MUST come from the map payload: ekonia maps are 16px tiles
    // (hard-coding 32 painted each red rect over 2x2 tiles — the overlay
    // looked shifted half a map off the real blocking).
    const tw = this.welcome?.map?.tile_width ?? 32;
    const th = this.welcome?.map?.tile_height ?? tw;
    const g = this.add.graphics().setDepth(900);
    g.fillStyle(0xff2020, 0.45);
    for (let y = 0; y < this.collision.length; y++) {
      const row = this.collision[y];
      for (let x = 0; x < row.length; x++) {
        if (row[x]) g.fillRect(x * tw, y * th, tw, th);
      }
    }
    this.collisionDebug = g;
  }

  /** Tile rows/columns the player box overlaps on one axis — a direct port
   * of game/collision._overlapped_rows (FLOAT_BOX_HALF = 0.3). The prediction
   * must agree with the server's swept collision or drift accumulates. */
  private overlappedRows(v: number): number[] {
    const r = 0.3;
    const out: number[] = [];
    for (const t of [Math.floor(v - r), Math.floor(v + r)]) {
      if (!out.includes(t) && v - r < t + 1 && v + r > t) out.push(t);
    }
    return out;
  }

  /** Mask-aware tile passability (server parity: Collision._mask_passable).
   *  A statically-blocked tile refined by an alpha mask lets the swept tile
   *  step ENTER — correctMaskOverlap() then pushes the box out of the real
   *  opaque shape. Without this the sweep stopped the box at the tile edge
   *  and the mask never applied ("box chặn tường hang dày quá mức" — the
   *  server walked closer to the rock than the client could). */
  private maskPassable(tx: number, ty: number): boolean {
    return this.tileMasks.has(`${tx},${ty}`);
  }

  /** Movement allowed along x, clamped EXACTLY to the blocking wall so the
   * player SLIDES along it — a direct port of game/collision._free_x (the
   * old client collision STOPPED at the wall while the server slid, so the
   * prediction fell behind on every wall-hug). MUST stay byte-identical to
   * the server or the prediction drifts.
   *
   * Boundary rule (the "đi xuyên khối" fix): the sweep starts at
   * floor(edge0), NOT floor(edge0)+1. A wall slide stops the box edge
   * EXACTLY on the tile boundary; the old +1 skipped that boundary column
   * on the next inward step and the box crept into the wall — reproduced
   * by brute force on the server (30k+ tunnels out of 155k cases).
   */
  private freeX(x: number, y: number, dx: number): number {
    if (dx === 0) return 0;
    const r = 0.3;
    const rows = this.overlappedRows(y);
    if (dx > 0) {
      const start = Math.floor(x + r);
      const end = Math.floor(x + dx + r);
      for (let c = start; c <= end; c++) {
        if (rows.some((t) => this.solidAt(c, t) && !this.maskPassable(c, t))) {
          return Math.min(dx, c - r - x);
        }
      }
      return dx;
    }
    const start = Math.floor(x - r);
    const end = Math.floor(x + dx - r);
    for (let c = start; c >= end; c--) {
      if (rows.some((t) => this.solidAt(c, t) && !this.maskPassable(c, t))) {
        return Math.max(dx, c + 1 + r - x);
      }
    }
    return dx;
  }

  /** Movement allowed along y (mirror of game/collision._free_y). */
  private freeY(x: number, y: number, dy: number): number {
    if (dy === 0) return 0;
    const r = 0.3;
    const cols = this.overlappedRows(x);
    if (dy > 0) {
      const start = Math.floor(y + r);
      const end = Math.floor(y + dy + r);
      for (let t = start; t <= end; t++) {
        if (cols.some((c) => this.solidAt(c, t) && !this.maskPassable(c, t))) {
          return Math.min(dy, t - r - y);
        }
      }
      return dy;
    }
    const start = Math.floor(y - r);
    const end = Math.floor(y + dy - r);
    for (let t = start; t >= end; t--) {
      if (cols.some((c) => this.solidAt(c, t) && !this.maskPassable(c, t))) {
        return Math.max(dy, t + 1 + r - y);
      }
    }
    return dy;
  }

  /** True when the tile blocks movement for the local prediction. Mirrors
   * the server's ``is_walkable`` (inverted): out-of-bounds, static collision,
   * placed blocks and standing resource nodes block; felled nodes don't. */
  private solidAt(tx: number, ty: number): boolean {
    // A FELLED node's tile walks free even though the static grid still
    // lists it as blocked (the standing-tree blocker): server parity.
    if (this.felledTiles.has(`${tx},${ty}`)) return false;
    // Mask-refined tiles are ENTERABLE for the sweep (server parity:
    // Collision._mask_passable) — the opaque-pixel correction
    // (correctMaskOverlap, mirrored server-side in can_move_float) does the
    // actual pixel-accurate blocking.
    if (this.tileMasks.has(`${tx},${ty}`)) return false;
    const row = this.collision[ty];
    if (!row || tx < 0 || tx >= row.length || row[tx] === 1) return true;
    // Placed blocks block movement (server mirrors this via BlockGrid).
    if (this.blockSet.has(`${tx},${ty}`)) return true;
    // Standing resource nodes (trees/bushes/ore) block until felled — EXCEPT
    // field forage (mushrooms/grass/flowers): waist-high decor, the player
    // walks straight through (server parity via game/collision.py).
    if (this.resourceTiles.has(`${tx},${ty}`)) {
      return !FORAGE_GIDS.has(this.gidOf(this.resourceTiles.get(`${tx},${ty}`)!));
    }
    return false;
  }

  applySnapshot(snap: SnapshotPayload): void {
    // MAP-SWITCH GUARD (user 16/09): the server moves the body to the
    // destination runtime on the portal tick, but the matching welcome (the
    // payload that actually swaps map/collision) arrives a frame later.
    // Applying that in-between snapshot painted the avatar onto the OLD map
    // at NEW-map coordinates — the "nháy qua cửa nhà gỗ rồi nháy về" flash.
    // Ignore snapshots from another map; the welcome rebuilds the world.
    if (this.welcome && snap.map_id && snap.map_id !== this.welcome.map.id) {
      return;
    }
    // Authoritative self position for reconciliation. Self is NOT in the
    // players payload anymore (the clone fix), so take it from snap.self.
    this.selfServerPos = { x: snap.self.x, y: snap.self.y };
    // SERVER BODY LIVENESS: the idle converge moves the body toward our
    // report every tick — a MOVING body proves the report channel works and
    // any residual divergence is transient echo that the server is actively
    // erasing (never snap the prediction back for it).
    if (this.selfServerPos.x !== this.lastSrvX || this.selfServerPos.y !== this.lastSrvY) {
      this.lastSrvX = this.selfServerPos.x;
      this.lastSrvY = this.selfServerPos.y;
      this.lastSrvMoveAt = performance.now();
    }
    // Debug telemetry: snapshot cadence + a console tracer that fires ONLY on
    // divergence > 1.5 tiles (no spam — the event itself is the signal).
    this.snapCount++;
    this.snapRateCount++;
    const nowT0 = performance.now();
    if (this.snapRateT0 === 0) this.snapRateT0 = nowT0;
    if (nowT0 - this.snapRateT0 >= 1000) {
      this.snapRate = this.snapRateCount * 1000 / (nowT0 - this.snapRateT0);
      this.snapRateT0 = nowT0;
      this.snapRateCount = 0;
    }
    // Rate trackers for the F3 throughput line (1s windows).
    const nowIn = performance.now();
    if (this.sentRateT0 === 0) this.sentRateT0 = nowIn;
    if (nowIn - this.sentRateT0 >= 1000) {
      this.sentRate = this.inputsSent * 1000 / (nowIn - this.sentRateT0);
      this.inputsSent = 0;
      this.sentRateT0 = nowIn;
    }
    if (this.ackedRateT0 === 0) this.ackedRateT0 = nowIn;
    if (nowIn - this.ackedRateT0 >= 1000) {
      this.ackedRate = this.inputsAcked * 1000 / (nowIn - this.ackedRateT0);
      this.inputsAcked = 0;
      this.ackedRateT0 = nowIn;
    }
    const dbgD = Math.hypot(this.selfX - this.selfServerPos.x, this.selfY - this.selfServerPos.y);
    if (dbgD > 1.5 && nowT0 - this.lastConvergeMoveAt > 2000) {
      this.lastConvergeMoveAt = nowT0;
      this.convergeEvents++;
      console.warn(
        "[DESYNC]", `d=${dbgD.toFixed(2)}`, // tiles
        `pred=(${this.selfX.toFixed(2)},${this.selfY.toFixed(2)})`,
        `srv=(${this.selfServerPos.x.toFixed(2)},${this.selfServerPos.y.toFixed(2)})`,
        `ack=${this.lastAckedSeq}`, `pendingInputs=${this.inputLog.length}`,
        this.inputLog.length === 0 ? "IDLE" : "MOVING",
      );
    }
    // Stamina: the tired gate reads this in stepSelf (prediction mirrors
    // the server's run-at-walk-speed cap once it empties).
    const st = (snap.self as { stamina?: number }).stamina;
    if (typeof st === "number") this.selfStamina = st;
    // Role color live-update (minted server-side; welcome may have arrived
    // before it existed).
    const sc = (snap.self as { color?: string }).color;
    if (sc && this.selfLabel && this.selfLabel.style.color !== sc) {
      this.selfLabel.setColor(sc);
    }
    // Eating state: chew particles while the flag is on; heal burst when a
    // completed eat is announced (server monotonic timestamp gates replays).
    const eat = snap.self as { eating?: boolean; eating_item?: string | null; heal_eat?: { item: string; at: number } | null };
    this.setSelfEating(!!eat.eating, eat.eating_item ?? null);
    this.selfEating = !!eat.eating;
    if (eat.heal_eat) this.playHealBurst(eat.heal_eat.at);
    const nowMs = performance.now();
    if (this.lastServerRecv > 0) {
      const gap = Math.min(2000, nowMs - this.lastServerRecv);
      this.snapGapAvg += (gap - this.snapGapAvg) * 0.1; // EMA of cadence
      this.snapGapMax = Math.max(50, this.snapGapMax * 0.98, gap); // decays over ~2s
    }
    this.lastServerRecv = nowMs;
    // --- input-sequence reconciliation (Source-engine style) ---
    // Rewind to the server's authoritative position at last_seq, then replay
    // every buffered input newer than that ack through the same collision
    // the prediction uses. Error converges to ~0 every snapshot: no glide,
    // no threshold, no visible correction, no direction mis-guessing.
    const acked = snap.self.last_seq;
    if (typeof acked === "number" && acked >= 0) {
      this.seqReplayActive = true;
      if (acked > this.lastAckedSeq) {
        this.lastAckedSeq = acked;
        this.lastAckProgressAt = performance.now();
        this.inputsAcked++;
        // Drop everything the server already integrated.
        while (this.inputLog.length > 0 && this.inputLog[0].seq <= acked) {
          this.inputLog.shift();
        }
        // Big divergence (portal/respawn/death): snap HARD, skip replay —
        // the inputs that led here are invalid on the new authority state.
        const jump = Math.hypot(
          this.selfX - this.selfServerPos.x,
          this.selfY - this.selfServerPos.y,
        );
        if (jump > 20 || this.selfDead) {
          this.selfX = this.selfServerPos.x;
          this.selfY = this.selfServerPos.y;
          this.inputLog = [];
        } else if (jump > WorldScene.RECONCILE_DRIFT) {
          // ASYMMETRIC RECONCILE (client-authoritative latency parity):
          // the server body structurally TRAILS the prediction by
          // latency × speed (0.6-1.5 tiles at run speed on a 100-250 ms
          // relay link) — srv BEHIND pred along the held input axis is the
          // EXPECTED converge echo, not an error. Rewinding for it (the old
          // unconditional threshold) re-created the stutter several times a
          // second while running: cross the threshold, hard rewind to the
          // lagging body, replay, pop, repeat. Only correct when the server
          // is materially AHEAD of the prediction along the movement axis
          // (a block/teleport/converge overshoot reached us that the
          // prediction doesn't know) or the residual is absurd (>4 tiles =
          // the report channel genuinely broke).
          const v = this.inputVec;
          const moving = v.dx !== 0 || v.dy !== 0;
          const srvAhead = moving
            ? ((this.selfServerPos.x - this.selfX) * v.dx +
               (this.selfServerPos.y - this.selfY) * v.dy) /
              (Math.hypot(v.dx, v.dy) || 1)
            : 0; // idle: axis test meaningless — liveness branch below decides
          // IDLE: the axis test can't distinguish echo from error, so use
          // body liveness — a CONVERGING body (server pulling toward our
          // report) means the residual is pure echo: leave pred alone. The
          // old unconditional idle snap rubber-banded every key release
          // (d~1.6 tiles at run speed: snap-back on release, drift-forward
          // as the server caught up = the "lag y cũ" double jerk).
          const idleRescue = !moving
            && performance.now() - this.lastSrvMoveAt > 1500;
          if ((moving && (srvAhead >= 0.5 || jump > 4)) || jump > 4 || idleRescue) {
          if (this.inputLog.length > 0) {
            this.selfX = this.selfServerPos.x;
            this.selfY = this.selfServerPos.y;
            const s = this.welcome?.self;
            // Mirror the server's gates INSIDE the replay too: running is
            // capped at walk when tired, and eating halves ALL speed. The
            // replay previously ran at full speed, so reconciliation kept
            // erasing the chew slow-mo every snapshot (20x/s) — the slowdown
            // existed on the server but was invisible on the client.
            const tiredReplay = this.selfStamina <= 0;
            const eatMulReplay = this.selfEating ? 0.5 : 1.0;
            for (const inp of this.inputLog) {
              if (inp.dx === 0 && inp.dy === 0) continue;
              const sp = (inp.running && !tiredReplay
                ? (s?.run_speed ?? 6.0)
                : (s?.walk_speed ?? 4.0)) * eatMulReplay;
              const sx = inp.dx * sp * inp.dt;
              const sy = inp.dy * sp * inp.dt;
              this.selfX += this.freeX(this.selfX, this.selfY, sx);
              this.selfY += this.freeY(this.selfX, this.selfY, sy);
              // Server parity: can_move_float runs the mask correction per
              // sub-step; the replay must too, or reconciliation lands the
              // box inside opaque pixels and the next frames "đè lên" the
              // sprite.
              this.correctMaskOverlap();
            }
          } else {
            // No pending inputs, idle: snap ONLY when the server body is
            // frozen (report channel dead — liveness gate above) or the
            // residual is absurd (jump > 4). Otherwise pred stays put and
            // the server's idle converge closes the gap in ~2 ticks.
            this.selfX = this.selfServerPos.x;
            this.selfY = this.selfServerPos.y;
          }
          }
          // else: expected echo lag while moving — pred stays put; the
          // server converges forward on its own (see _converge_to_report).
        }
      }
    }
    // DRIFT RECOVERY — client-authoritative edition. The old 30%/snapshot
    // glide here WAS the visible rubber band: it dragged the player's avatar
    // backwards to srv while the server was merely catching up to pred (the
    // heartbeat makes srv converge server-side within ~0.5 s). Never fight
    // the model: pred stays put. The ONLY justified correction is a truly
    // dead report channel — no ack progress for 3 s AND still far — then a
    // single snap (rare orphan case), never a slide.
    if (
      this.seqReplayActive &&
      !this.selfDead &&
      this.inputLog.length === 0 &&
      this.driftIdleMs > 3000 &&
      performance.now() - this.lastAckProgressAt > 3000
    ) {
      const dead = Math.hypot(this.selfX - this.selfServerPos.x, this.selfY - this.selfServerPos.y);
      if (dead > 2) {
        this.selfX = this.selfServerPos.x;
        this.selfY = this.selfServerPos.y;
        this.inputLog = [];
        console.warn("[DESYNC] dead-report snap", dead.toFixed(2));
      } else {
        this.driftIdleMs = 0; // close enough; don't re-arm every frame
      }
    }
    // Death state: on dead, HARD-snap the prediction to the authority (the
    // server teleported/hid us — any predicted position is fiction). stepSelf
    // reads selfDead and stops integrating input while dead (no more
    // "walk inside an invisible circle": movement was server-rejected and
    // every reconcile pulled the ghost back).
    this.selfDead = snap.self.dead ?? false;
    if (this.selfDead) {
      this.selfX = snap.self.x;
      this.selfY = snap.self.y;
    }
    // Self held echo (20 Hz): converges the local instant hand with the
    // server truth (reconnect / bag change from another client / use).
    if (snap.self.held !== undefined) this.setSelfHeld(snap.self.held ?? null);
    // Build-Mode cursor. Deliberately do NOT take snap.self.dir as the hand
    // target: the server reports the dominant-axis 8-way name (SE -> E),
    // and blending toward it on every key release re-introduced the facing
    // jerk. The last raw input vector IS the true facing — keep it.
    this.aimCursor = snap.self.aim ?? null;
    // Resource nodes: chopped trees vanish / regrow, progress bars sync.
    // Pass the authoritative self tile so grown hit counts swing OUR hand
    // (not some remote's) when we are the harvester.
    // WORLD-DELTA protocol: the server omits `resources`/`res_felled` when
    // unchanged since the last one it sent us (they only change on chop/regrow
    // actions) — so only refresh the layer when the arrays are PRESENT. The
    // signature guard inside updateResourceLayer also keeps this idempotent.
    if (snap.resources !== undefined) {
      // Keep the welcome payload's resource list fresh too: bakeMapIfReady
      // reads it to decide which resource-layer tiles belong to LIVE nodes
      // (felled nodes' tiles must never re-enter the base bake).
      if (this.welcome) this.welcome.resources = snap.resources;
      this.updateResourceLayer(snap.resources);
      this.felledTiles = new Set((snap.res_felled ?? []).map(([x, y]) => `${x},${y}`));
      // Node tiles are NEVER baked into "map-bake" (only sprites), so a
      // felled node needs no canvas surgery — the sprite layer above handles
      // vanish/regrow. No rebake, no stall on felling.
    }
    this.syncProgressBars(snap.res_progress, {
      x: Math.floor(snap.self.x),
      y: Math.floor(snap.self.y),
    });
    for (const p of snap.players) this.upsertPlayer(p);
    // Night zombies (web realtime pack): interpolate + animate.
    this.syncZombies(snap.zombies ?? []);
    // Drop entities ("linh khí"): spawn/magnet/collect animation.
    this.syncDrops(snap.drops ?? []);
    // Despawn players no longer present.
    const seen = new Set(snap.players.map((p) => p.id));
    for (const [id, rp] of this.players) {
      if (!seen.has(id)) {
        rp.container.destroy();
        this.players.delete(id);
      }
    }
    // Skip block rebuild when nothing changed (snapshots arrive at 20 Hz;
    // rebuilding thousands of rectangles per tick is pure waste).
    const sig = snap.blocks.length + ":" + snap.blocks.map((b) => b[0] + "," + b[1]).join(";");
    if (sig !== this.lastBlockSig) {
      this.lastBlockSig = sig;
      // Keep welcome.blocks fresh: onBlockTexture re-runs updateBlocks from
      // this.welcome when a face PNG arrives — with the stale join-time list
      // it destroyed the placeholder for any block placed AFTER join (the
      // "đặt block tàn hình" bug).
      if (this.welcome) this.welcome.blocks = snap.blocks;
      this.updateBlocks(snap.blocks);
      // Station tiles (E-prompt targets) follow the same sig-guarded pass.
      this.stationTiles.clear();
      for (const [x, y, bid] of snap.blocks) {
        if (STATION_BLOCK_IDS.has(bid)) this.stationTiles.add(`${x},${y}`);
      }
    }
    // Collision truth rebuilt from EVERY snapshot (a cheap Set — the old
    // sig-guarded rebuild let a rejected/clamped own-action leave a phantom
    // solid/walkable tile until some OTHER block happened to change: the
    // "đặt rồi xóa rồi chạy xuyên" bug). Pending optimistic place/break
    // from OUR OWN in-flight actions are re-applied on top, bounded by a
    // TTL so a lost echo (clamped target, dropped frame) can never pin a
    // phantom forever.
    const now = performance.now();
    const pendingSolid = new Set<string>();
    for (const [k, t] of this.optimisticBlocks) {
      if (now - t > OPTIMISTIC_TTL_MS) this.optimisticBlocks.delete(k);
      else pendingSolid.add(k);
    }
    for (const [k, t] of this.optimisticBreaks) {
      if (now - t > OPTIMISTIC_TTL_MS) this.optimisticBreaks.delete(k);
    }
    this.blockSet = new Set(snap.blocks.map((b) => `${b[0]},${b[1]}`));
    for (const k of pendingSolid) this.blockSet.add(k);
    for (const k of this.optimisticBreaks.keys()) this.blockSet.delete(k);
    // Block crack damage (server self-repair truth, rides every snapshot —
    // damage drains even when nobody mines). MUST run AFTER blockSet is
    // rebuilt: the sync clears overlays for tiles that are no longer blocks.
    this.syncBlockDamage(snap.block_damage, this.blockSet);
  }

  // Local self position in tile units (for the HUD + camera sanity).
  get selfPos(): { x: number; y: number } {
    if (this.selfMarker) {
      return { x: this.selfMarker.x / this.tilePx, y: this.selfMarker.y / this.tilePx };
    }
    return { x: 0, y: 0 };
  }
}
