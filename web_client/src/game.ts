// Phaser game scene: builds the world from the welcome payload (Tiled layers
// + tilesets fetched through the relay), interpolates 20 Hz snapshots to
// 60 fps rendering, follows the camera on the local player.

import Phaser from "phaser";
import type { PlayerPayload, PlayersManifest, SnapshotPayload, WebZombiePayload, WelcomePayload } from "./protocol";
import { PaperdollBody, b64ToBytes, registerPaperdollTextures } from "./paperdoll";
import { WEAPON_SHEETS as WEAPON_SHEET_BY_ITEM, weapon_sheet_for } from "./appearance_client";

const PLAYER_SIZE = 22; // px in world space (tile = 32)
const ZOMBIE_SIZE = 48; // mobs tower ~1.5x over the 32px player doll
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

interface RemotePlayer {
  container: Phaser.GameObjects.Container;
  body: Phaser.GameObjects.Rectangle;
  label: Phaser.GameObjects.Text;
  webBadge: Phaser.GameObjects.Arc | null;
  // Plan A hand: small circle same colour as the body, orbiting with facing.
  hand: Phaser.GameObjects.Arc;
  handColor: number;
  // Tool/weapon icon over the hand (emoji Text). Empty text = bare hand.
  toolIcon: Phaser.GameObjects.Text;
  held: string | null;
  swingT0: number; // performance.now() of the last swing (0 = never)
  // interpolation buffer: [t_recv, x, y]
  buf: [number, number, number][];
  dir: string;
}

// Hand orbit: distance from the body centre + dot radius. Exported so the
// self hand (not in a container) shares the exact same geometry.
export const HAND_ORBIT = 20;
export const HAND_RADIUS = 5;
// Swing: on every harvest hit the hand thrusts out by SWING_EXTRA and back
// over SWING_MS (sin curve), for self AND remote hands alike.
const SWING_MS = 220;
const SWING_EXTRA = 12;

export class WorldScene extends Phaser.Scene {
  private welcome: WelcomePayload | null = null;
  private tileTextures = new Map<string, string>(); // image name -> texture key
  private loadedTilesets = new Set<string>(); // tileset images that arrived
  private players = new Map<number, RemotePlayer>();
  private selfMarker: Phaser.GameObjects.Rectangle | null = null;
  private blockLayer: Phaser.GameObjects.Layer | null = null;
  // Diff cache for the block overlay: tile key -> sprite. Rapid place/break
  // used to tear down and rebuild EVERY block rectangle on each snapshot sig
  // change (~20 Hz while building) — the create/destroy churn was a real
  // source of stutter. Now only added/removed tiles touch the scene.
  private blockSprites = new Map<string, Phaser.GameObjects.GameObject>();
  private mapBake: Phaser.GameObjects.Image | null = null;
  private lastBlockSig = "";
  // Real block faces (assets/blocks/<id>.png fetched via asset_request).
  // Pending ids get a plain rectangle until the texture arrives, then the
  // next updateBlocks redraws them with the sprite.
  private blockTextures = new Set<string>();
  private pendingFetch: ((id: string) => void) | null = null;
  private selfId = 0;
  // --- plan A "tay cầm tool": self hand = small circle same colour as the
  // body (0x5865f2) + tool emoji from the HELD hotbar slot. Mirrors the
  // remote hand geometry (HAND_ORBIT/HAND_RADIUS) but lives in world space
  // next to selfMarker (self is predicted, not in a container).
  private selfHand: Phaser.GameObjects.Arc | null = null;
  private selfToolIcon: Phaser.GameObjects.Text | null = null;
  private selfHeld: string | null = null;
  private selfSwingT0 = 0; // performance.now() of the last self swing
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
  private selfX = 0; // predicted float position, TILE units
  private selfY = 0;
  private collision: number[][] = []; // collision[y][x] = 1 blocks
  private selfServerPos = { x: 0, y: 0 }; // last authoritative position
  private lastServerRecv = 0;
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
  private resourceTiles = new Map<string, Phaser.GameObjects.Image>();
  private resourceSig = "";
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
    frame: number; // current local frame index inside the row
    frameT0: number; // performance.now() of the last local frame advance
    facing: string;
    dieT0: number; // performance.now() when the kill echo landed (0 = alive)
    hunter: boolean;
  }>();
  private zombieTextureKey = "mob-zombie";
  private zombieTextureReady = false;
  private zombieFetchAsked = false;
  private lastZombieFrameT = 0;
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
    this.pendingFetch = (id: string) => fetchAsset(`blocks/${id}.png`);
    this.welcome = welcome;
    this.selfId = welcome.self.id;
    // Paperdoll: stash manifest, fetch base + every mapped weapon sheet
    // once through the same relay pipe as blocks/mobs (license-safe).
    if (welcome.players_manifest && !this.paperdollAsked) {
      this.playersManifest = welcome.players_manifest;
      this.paperdollAsked = true;
      fetchAsset("players/base.png");
      for (const stem of new Set(Object.values(WEAPON_SHEET_BY_ITEM))) {
        fetchAsset(`players/weapon/${stem}.png`);
      }
    }
    // Server item emojis FIRST: hand icons (self + remote) resolve through
    // this map, so it must be fresh before any setText call below.
    this.itemEmojis = welcome.item_emojis ?? {};
    // Rebuild existing hand icons with the fresh map (same ids, new glyphs).
    this.setSelfHeld(this.selfHeld);
    for (const rp of this.players.values()) {
      rp.toolIcon.setText(this.emojiFor(rp.held));
    }
    const map = welcome.map;

    // --- tilesets: request each PNG through the relay (license-safe) ---
    for (const ts of map.tilesets) {
      if (!ts.image) continue;
      if (!this.tileTextures.has(ts.image)) {
        this.tileTextures.set(ts.image, ts.image.replace(/\.png$/i, ""));
        fetchAsset(ts.image);
      }
    }
    // Request every DISTINCT block face once (blocks payload may repeat ids).
    for (const id of new Set(welcome.blocks.map(([, , bid]) => bid))) {
      if (!this.blockTextures.has(id)) fetchAsset(`blocks/${id}.png`);
    }
    // Night zombie sprite sheet (Kaetram 160x288, 5 cols x 9 rows of 32px):
    // requested once per session through the same relay pipe (license-safe —
    // the PNG stays on the bot, only this client receives the bytes).
    if (!this.zombieTextureReady && !this.zombieFetchAsked) {
      this.zombieFetchAsked = true;
      fetchAsset("mobs/zombie.png");
    }
    this.buildBlocks(welcome.blocks);
    this.bakeMapIfReady();

    // --- physics-less world: positions are authoritative from the server ---
    this.cameras.main.setBounds(0, 0, map.width * map.tile_width, map.height * map.tile_height);
    this.cameras.main.setBackgroundColor("#20303c");
    this.cameras.main.setZoom(1.6); // zoom IN — close-up view

    this.spawnSelf(welcome);
    for (const p of welcome.players) this.upsertPlayer(p);

    // Client-side prediction state: start from the authoritative spawn.
    this.selfX = welcome.self.x;
    this.selfY = welcome.self.y;
    this.selfServerPos = { x: welcome.self.x, y: welcome.self.y };
    this.lastServerRecv = performance.now();
    this.collision = welcome.map.collision ?? [];
    this.selfDir = welcome.self.dir || "SOUTH";
    if (!this.faceVec) {
      const v0 = DIR_VECTORS[this.selfDir] ?? DIR_VECTORS.SOUTH;
      this.faceVec = { x: v0[0], y: v0[1] };
    }

    // Hover square only — the hand dot is the facing indicator now.
    this.ensureHoverSquare();
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

    // Camera follows the SELF MARKER every frame — the marker itself is
    // driven by prediction in update(), so camera lag = marker lag.
    if (this.selfMarker) {
      this.cameras.main.startFollow(this.selfMarker, true, 0.15, 0.15);
    }
  }

  /** Called 20 Hz from main.ts: store the current input vector. */
  setLocalInput(dx: number, dy: number, running: boolean): void {
    this.inputVec.dx = dx;
    this.inputVec.dy = dy;
    this.inputVec.running = running;
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
    return { x: Math.floor(p.x / 32), y: Math.floor(p.y / 32) };
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

  private bakeMapIfReady(): void {
    const welcome = this.welcome;
    if (!welcome) return;
    const map = welcome.map;
    const tw = map.tile_width;
    const th = map.tile_height;
    // Require at least one tileset texture; bake with what we have.
    const usable = map.tilesets.filter(
      (t) => t.image && this.textures.exists(this.tileTextures.get(t.image) ?? ""),
    );
    if (usable.length === 0) return;

    // Resource layers ("cây", "vật phẩm ko liên quan", ...) are drawn as a
    // separate dynamic layer (choppable), so the base bake must EXCLUDE the
    // whole LAYER (by folded name, mirroring the server's
    // RESOURCE_LAYER_NAMES) — NOT per-coordinate: excluding coordinates also
    // drops the ground/grass tiles UNDER a tree, leaving a hole when the
    // tree is felled.
    const RESOURCE_LAYERS = new Set([
      "cay", "tree", "trees", "resources",
      "vat pham ko lien quan", "ore", "ores", "mine",
    ]);
    const foldName = (s: string): string =>
      s.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
        .replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();

    const canvas = document.createElement("canvas");
    canvas.width = map.width * tw;
    canvas.height = map.height * th;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    for (const layer of map.layers) {
      if (RESOURCE_LAYERS.has(foldName(layer.name || ""))) continue;
      for (let y = 0; y < map.height; y++) {
        const row = layer.data[y];
        if (!row) continue;
        for (let x = 0; x < map.width; x++) {
          const gid = row[x];
          if (!gid) continue;
          const ts = map.tilesets.find(
            (t) => gid >= t.firstgid && gid < t.firstgid + t.columns * 1000,
          );
          if (!ts?.image) continue;
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
          ctx.drawImage(
            src, col * tileW, rowIdx * tileH, tileW, tileH,
            x * tw, y * th, tw, th,
          );
        }
      }
    }
    const key = "map-bake";
    if (this.textures.exists(key)) this.textures.remove(key);
    this.textures.addCanvas(key, canvas);
    if (this.mapBake) {
      this.mapBake.setTexture(key);
    } else {
      this.mapBake = this.add.image(0, 0, key).setOrigin(0, 0).setDepth(-10);
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
      if (!go) {
        // New block: sprite (or placeholder rectangle until the face
        // texture arrives — see onBlockTexture).
        if (hasTex) {
          go = this.add.image(x * 32 + 16, y * 32 + 16, texKey);
        } else {
          const r = this.add.rectangle(x * 32 + 16, y * 32 + 16, 30, 30, 0x6b5a3e);
          r.setStrokeStyle(2, 0x8a7550);
          go = r;
        }
        this.blockLayer.add(go);
        this.blockSprites.set(tileKey, go);
      } else if (hasTex && go instanceof Phaser.GameObjects.Rectangle) {
        // Texture arrived: upgrade the placeholder in place (no churn).
        const img = this.add.image(x * 32 + 16, y * 32 + 16, texKey);
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
    this.selfDoll.spawn(this.selfX * 32, this.selfY * 32 + 16, 7);
    if (this.selfHeld) this.selfDoll.setWeapon(weapon_sheet_for(this.selfHeld));
  }

  /** Spawn a remote paperdoll inside its interpolation container. */
  private spawnRemoteDoll(id: number, rp: RemotePlayer): void {
    if (!this.playersManifest || this.remoteDolls.has(id)) return;
    const doll = new PaperdollBody(this, this.playersManifest);
    doll.spawn(rp.container.x, rp.container.y + 16, 7);
    this.remoteDolls.set(id, doll);
    rp.body.setVisible(false); // hide the square; keep it for hit geometry
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
      this.selfMarker.setPosition(s.x * 32, s.y * 32);
      this.ensureSelfHand();
      this.setSelfHeld(welcome.held ?? null);
      return;
    }
    // NOTE: server positions are already TILE-CENTER based (x_f = x + 0.5),
    // so x*32 lands exactly in the middle of the tile. Never add +16 here —
    // that shifts the avatar half a tile off the collision grid.
    this.selfMarker = this.add.rectangle(s.x * 32, s.y * 32, PLAYER_SIZE, PLAYER_SIZE, 0x5865f2);
    this.selfMarker.setStrokeStyle(2, 0xffffff, 0.9);
    this.selfMarker.setName("self");
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

  /** Emoji for a held item id (server map first, "?" never — empty when unknown). */
  private emojiFor(itemId: string | null): string {
    if (!itemId) return "";
    return this.itemEmojis[itemId] ?? "";
  }

  /** Update the SELF hand icon (called on held echo + inventory + snapshot). */
  setSelfHeld(itemId: string | null): void {
    this.selfHeld = itemId;
    if (this.selfToolIcon) this.selfToolIcon.setText(this.emojiFor(itemId));
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
      const container = this.add.container(p.x * 32, p.y * 32);
      const color = p.id === this.selfId ? 0x5865f2 : p.web ? 0x2f9e63 : 0xd97706;
      const body = this.add.rectangle(0, 0, PLAYER_SIZE, PLAYER_SIZE, color);
      const label = this.add.text(0, 22, p.name, {
        fontSize: "10px", color: "#ffffff",
        stroke: "#000000", strokeThickness: 3,
      }).setOrigin(0.5);
      // Plan A hand: same-colour dot + tool icon, BOTH inside the container
      // so interpolation moves them for free (no per-frame sync needed).
      const hand = this.add.circle(HAND_ORBIT, 0, HAND_RADIUS, color);
      hand.setStrokeStyle(2, 0xffffff, 0.9);
      const toolIcon = this.add.text(HAND_ORBIT, 0, "", { fontSize: "13px" }).setOrigin(0.5);
      container.add(body);
      container.add(hand);
      container.add(toolIcon);
      container.add(label);
      rp = { container, body, label, webBadge: null, hand, handColor: color, toolIcon, held: null, swingT0: 0, buf: [], dir: p.dir };
      container.setData("pid", p.id);
      this.players.set(p.id, rp);
      // Paperdoll texture already live? Swap immediately (square stays as
      // invisible fallback geometry otherwise).
      if (this.paperdollReady) this.spawnRemoteDoll(p.id, rp);
    }
    rp.buf.push([now, p.x * 32, p.y * 32]);
    if (rp.buf.length > 12) rp.buf.shift();
    rp.dir = p.dir;
    // Held item changed -> refresh the tool icon (empty = bare hand dot).
    const held = p.held ?? null;
    if (held !== rp.held) {
      rp.held = held;
      rp.toolIcon.setText(this.emojiFor(held));
      const doll = this.remoteDolls.get(p.id);
      if (doll) doll.setWeapon(weapon_sheet_for(held));
    }
  }

  // ---- per-frame update (60fps) ----

  update(_time: number, delta?: number): void {
    // Real frame delta (ms). The server integrates movement from REAL wall
    // time at 20 Hz — the prediction must do the same or it silently runs
    // 2x fast on 120 Hz displays (the old hardcoded 1/60 per frame) and the
    // avatar outruns the server until every position/click drifts apart.
    // Clamp to 0.2s like the server's dt clamp so a stalled tab can never
    // teleport the player through walls on resume.
    this.frameDtSec = Math.min(0.2, Math.max(0.001, (delta ?? 16.7) / 1000));
    // Mouse tile + hover box derive FRESH each frame from the last cursor
    // position: the camera moves under a still cursor (follow lerp, tab
    // switch) and a tile cached at mousemove time would be stale.
    if (this.mouseScreen) {
      this.mouseTile = this.tileFromScreen(this.mouseScreen.x, this.mouseScreen.y);
    } else {
      this.mouseTile = null;
    }
    this.updateHoverSquare();
    // --- client-side prediction: move SELF instantly every frame ---
    // Server speed: walk 4 tiles/s, run 6 tiles/s (config.WEB_*_SPEED).
    this.stepSelf();
    // Hand orbit: self hand + icon follow the smoothed facing vector so the
    // dot + tool rotate WITH the avatar. updateFacing() must run first.
    this.updateFacing();
    this.updateSelfHand();

    // Paperdoll animation: drive self + remote dolls from the shared clock.
    const nowMs = performance.now();
    if (this.selfDoll?.ready && this.selfMarker) {
      const moving = this.inputVec.running;
      const action = this.selfDoll.attacking ? "atk" : moving ? "walk" : "idle";
      this.selfDoll.animate(this.selfX * 32, this.selfY * 32 + 16, action, this.selfDir, nowMs);
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
      // Paperdoll: hide the hand dot under the Kaetram body, animate the doll.
      const doll = this.remoteDolls.get(parseInt(String(rp.container.getData("pid") ?? ""), 10));
      if (doll?.ready) {
        rp.hand.setVisible(false);
        rp.toolIcon.setVisible(false);
        const moving = Math.hypot(next[1] - prev[1], next[2] - prev[2]) > 1;
        const action = doll.attacking ? "atk" : moving ? "walk" : "idle";
        doll.animate(x, y + 16, action, rp.dir, performance.now());
      }
    }
    this.updateZombieFrames();
    // Kaetram hitsplats: float + fade every frame (spawned from action_result).
    this.updateSplats(performance.now());
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
        if (dist > 96) {
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
        // Server-authoritative anim -> Kaetram row; local frame ticks at the
        // row's own pace (walk = shambling ~6fps, atk = one 450ms lunge,
        // idle = slow 2-frame breathe). setFrame picks ONE 32px cell — the
        // full 160x288 sheet is never drawn stretched.
        const row = z.anim === "atk" ? 0 : z.anim === "walk" ? 1 : 2;
        const len = z.anim === "atk" ? 5 : z.anim === "walk" ? 4 : 2;
        const pace = z.anim === "atk" ? 90 : z.anim === "walk" ? 160 : 500;
        if (now - z.frameT0 >= pace) {
          z.frameT0 = now;
          z.frame = z.anim === "atk"
            ? Math.min(len - 1, z.frame + 1) // lunge holds its last frame
            : (z.frame + 1) % len; // walk/idle loop
        }
        this.applyMobCell(z.body, z.frame, row, ZOMBIE_SIZE);
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
      marker.setPosition(this.selfServerPos.x * 32, this.selfServerPos.y * 32);
      this.updateSelfHand();
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
      const speed = v.running
        ? (s?.run_speed ?? 6.0)
        : (s?.walk_speed ?? 4.0);
      const stepX = v.dx * speed * dt;
      const stepY = v.dy * speed * dt;
      this.selfX += this.freeX(this.selfX, this.selfY, stepX);
      this.selfY += this.freeY(this.selfX, this.selfY, stepY);
    }
    // Reconciliation against the LATEST authority (never a stale echo) — a
    // collision-aware GLIDE that only ever fires on REAL divergence. The
    // prediction mirrors the server's integration exactly (same speed, same
    // real dt, same slide collision), so normal-play drift is just the
    // snapshot echo lag: speed x (tick interval + network one-way latency).
    // On a high-latency link (VN -> Railway can be 150-300ms RTT) that lag
    // reaches ~1.5-2 tiles at run speed — correcting it per-frame is what
    // made the avatar stutter (giật). The glide threshold (3.0) sits ABOVE
    // that worst case, so normal play is NEVER corrected. Real divergence
    // (server freeze, direction change mid-lag) is eased back THROUGH the
    // collision grid (freeX/freeY), so the marker can never visually pass
    // through a block (the old instant snap teleported across walls).
    // A > 20-tile gap is a portal/respawn — an instant jump is correct.
    const age = performance.now() - this.lastServerRecv;
    if (age < 600) {
      const drift = Math.hypot(
        this.selfX - this.selfServerPos.x,
        this.selfY - this.selfServerPos.y,
      );
      if (drift > 20) {
        this.selfX = this.selfServerPos.x;
        this.selfY = this.selfServerPos.y;
      } else if (drift > 3.0) {
        if (v.dx === 0 && v.dy === 0) {
          // Standing still: ease back to the authority (server-side force,
          // direction change mid-lag, etc.). Brisk glide, collision-aware.
          const pull = Math.min(
            0.3 * 60 * this.frameDtSec,
            (drift - 3.0) * 0.2 + 0.05,
          );
          const k = pull / Math.max(1e-6, drift);
          const dx = (this.selfServerPos.x - this.selfX) * k;
          const dy = (this.selfServerPos.y - this.selfY) * k;
          this.selfX += this.freeX(this.selfX, this.selfY, dx);
          this.selfY += this.freeY(this.selfX, this.selfY, dy);
        } else {
          // MOVING: never pull backwards against the held direction — that
          // counter-force (up to ~9 tiles/s at small drift) was the
          // "invisible block shoving me" feel at night. Instead brake the
          // prediction to half speed for this frame so the server can catch
          // up; drift decays without any visible shove. Server-side debt
          // repayment (WebSession.time_debt) keeps its lead small.
          const bs = v.running
            ? (this.welcome?.self?.run_speed ?? 6.0)
            : (this.welcome?.self?.walk_speed ?? 4.0);
          this.selfX += this.freeX(this.selfX, this.selfY, -v.dx * bs * this.frameDtSec * 0.5);
          this.selfY += this.freeY(this.selfX, this.selfY, -v.dy * bs * this.frameDtSec * 0.5);
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
    this.updateFacing();
    marker.setPosition(this.selfX * 32, this.selfY * 32);
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
    return { x: Math.floor(w.x / 32), y: Math.floor(w.y / 32) };
  }

  private ensureHoverSquare(): void {
    if (this.hoverSquare && this.hoverSquare.active) return;
    this.hoverSquare = this.add.rectangle(0, 0, 30, 30, 0x8fd4ff, 0.05)
      .setStrokeStyle(2, 0x8fd4ff, 0.9)
      .setDepth(100)
      .setVisible(false);
  }

  private updateHoverSquare(): void {
    this.ensureHoverSquare();
    if (!this.hoverSquare || !this.mouseTile) {
      this.hoverSquare?.setVisible(false);
      return;
    }
    this.hoverSquare
      .setPosition(this.mouseTile.x * 32 + 16, this.mouseTile.y * 32 + 16)
      .setVisible(true)
      .setDepth(100)
      .setActive(true)
      .setAlpha(1);
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

  /**
   * Self hand orbit (plan A): the dot + tool icon ride the smoothed facing
   * vector, so the hand rotates WITH the avatar. A swing thrusts the hand
   * out and back (dig/chop feedback). Runs right after updateFacing().
   */
  private updateSelfHand(): void {
    if (!this.selfHand || !this.selfToolIcon || !this.selfMarker) return;
    const v = this.faceVec ?? { x: this.lastMoveX, y: this.lastMoveY || 1 };
    const len = Math.hypot(v.x, v.y) || 1;
    const ux = v.x / len;
    const uy = v.y / len;
    const reach = HAND_ORBIT + this.swingExtra(this.selfSwingT0, performance.now());
    const hx = this.selfX * 32 + ux * reach;
    const hy = this.selfY * 32 + uy * reach;
    this.selfHand.setPosition(hx, hy);
    this.selfToolIcon.setPosition(hx, hy);
  }

  /** Swing the SELF hand at once (optimistic — no server wait). */
  swingSelfHand(): void {
    this.selfSwingT0 = performance.now();
    this.selfDoll?.swing(performance.now());
  }

  /** Swing one REMOTE hand when its harvest progress grows (20 Hz echo). */
  swingRemoteHand(id: number): void {
    const rp = this.players.get(id);
    if (rp) rp.swingT0 = performance.now();
    this.remoteDolls.get(id)?.swing(performance.now());
  }

  /**
   * Swing the remote hand standing closest to a tile (chop/break confirm).
   * The harvester is whoever works that node — no server id needed.
   */
  swingRemoteHandNear(tx: number, ty: number): void {
    const px = tx * 32 + 16;
    const py = ty * 32 + 16;
    let best: RemotePlayer | null = null;
    let bestD = 3.5 * 32; // ignore hands further than a harvest reach away
    for (const rp of this.players.values()) {
      const d = Math.hypot(rp.container.x - px, rp.container.y - py);
      if (d < bestD) {
        bestD = d;
        best = rp;
      }
    }
    if (best) best.swingT0 = performance.now();
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
  spawnSplat(tx: number | null, ty: number | null, damage: number, critical: boolean, missed: boolean): void {
    if (tx === null || ty === null) return;
    const text = missed || damage <= 0 ? "MISS" : String(damage);
    const fill = missed ? "#cfd6e4" : critical ? "#ffd75e" : "#ff3232";
    const stroke = missed ? "#2a2f3a" : critical ? "#7a5b00" : "#ffb4b4";
    const txt = this.add.text(tx * 32 + 16, ty * 32 - 4, text, {
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
    const sig = tiles.map((t) => t.join(",")).join(";");
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
    for (const [x, y, gid] of tiles) {
      const texKey = this.textureForGid(gid);
      if (!texKey) continue;
      const img = this.add.image(x * 32 + 16, y * 32 + 16, texKey);
      this.resourceLayer.add(img);
      this.resourceTiles.set(`${x},${y}`, img);
    }

    // Felled nodes: their tiles were present before, gone now -> animate.
    const nowKeys = new Set([...this.resourceTiles.keys()]);
    for (const [, imgs] of prevNodes) {
      const stillThere = imgs.every((img) => nowKeys.has(this.keyOf(img)));
      if (imgs.length > 0 && !stillThere) {
        this.playFallAnimation(imgs);
      }
    }
    // Progress bars of vanished nodes are stale.
    this.syncProgressBars({});
  }

  /** World key of a resource image ("x,y" from its centre position). */
  private keyOf(img: Phaser.GameObjects.Image): string {
    return `${Math.round((img.x - 16) / 32)},${Math.round((img.y - 16) / 32)}`;
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
            angle: img.x < this.selfX * 32 ? 12 : -12,
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
    const cacheKey = `res-${gid}`;
    if (this.textures.exists(cacheKey)) return cacheKey;
    const tw = map.tile_width;
    const th = map.tile_height;
    const ts = map.tilesets.find(
      (t) => gid >= t.firstgid && gid < t.firstgid + t.columns * 1000,
    );
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
    selfTile?: { x: number; y: number },
  ): void {
    // Any node whose hit count GREW since the last sync = a landed swing:
    // swing the nearest hand (self when selfTile is at/near the node, else
    // the closest remote). This IS the dig/chop animation, driven by the
    // 20 Hz server echo — no extra protocol needed.
    for (const [key, entry] of Object.entries(progress)) {
      const prev = this.lastProgressRaw.get(key)?.[0] ?? 0;
      const hits = entry[0] ?? 0;
      if (hits > prev && hits > 0) {
        const bbox = entry[2] ?? [];
        const nearSelf =
          !!selfTile &&
          bbox.some(
            ([bx, by]) =>
              Math.abs(bx - selfTile.x) <= 2 && Math.abs(by - selfTile.y) <= 2,
          );
        if (nearSelf) {
          this.swingSelfHand();
        } else if (bbox.length > 0) {
          const cx =
            bbox.reduce((a, [bx]) => a + bx, 0) / bbox.length;
          const cy =
            bbox.reduce((a, [, by]) => a + by, 0) / bbox.length;
          this.swingRemoteHandNear(Math.round(cx), Math.round(cy));
        }
      }
    }
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
      const minX = Math.min(...xs) * 32;
      const maxX = (Math.max(...xs) + 1) * 32;
      const maxY = (Math.max(...ys) + 1) * 32;
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

  /** Show ONE 32px cell of a mob spritesheet at the requested display size.
   * The texture registers as a spritesheet (frameWidth/Height = 32), so
   * setFrame gives every cell its own cut + origin — unlike setCrop on the
   * full-sheet Image, which kept the quad at sheet size and drew cells
   * offset sideways, shifting position on every frame change. */
  private applyMobCell(img: Phaser.GameObjects.Image, col: number, row: number, size: number = PLAYER_SIZE): void {
    const tex = this.textures.get(img.texture.key);
    const perRow = Math.max(1, Math.floor(tex.source[0].width / 32));
    img.setFrame(row * perRow + col);
    img.setScale(size / 32);
  }

  /** A mob sprite PNG arrived via the relay: mark ready for upgrade. */
  onMobTexture(name: string): void {
    if (name !== "mobs/zombie.png" || this.zombieTextureReady) return;
    if (!this.textures.exists(this.zombieTextureKey)) return;
    this.zombieTextureReady = true;
  }

  /** Sync the zombie layer from one snapshot payload (20 Hz). */
  private syncZombies(list: WebZombiePayload[]): void {
    const seen = new Set<string>();
    for (const [id, x, y, hp, maxHp, kind, facing, anim] of list) {
      seen.add(id);
      let z = this.zombies.get(id);
      if (!z) {
        if (!this.zombieLayer) this.zombieLayer = this.add.layer();
        const container = this.add.container(x * 32, y * 32);
        const body: Phaser.GameObjects.Image | Phaser.GameObjects.Rectangle =
          this.zombieTextureReady && this.textures.exists(this.zombieTextureKey)
            ? this.add.image(0, 0, this.zombieTextureKey)
            : this.add.rectangle(0, 0, 22, 26, 0x3a7d2c);
        // Cut the FIRST idle frame immediately so a fresh spawn never shows
        // the whole stretched sheet for even one frame.
        if (body instanceof Phaser.GameObjects.Image) {
          this.applyMobCell(body, 0, 2, ZOMBIE_SIZE);
        }
        const label = this.add.text(0, 24, kind === "hunter" ? "🧟‍♂️!" : "🧟", {
          fontSize: "10px", color: "#ffffff",
          stroke: "#000000", strokeThickness: 3,
        }).setOrigin(0.5);
        const hpBg = this.add.rectangle(0, -22, 28, 4, 0x000000, 0.6);
        const hpFill = this.add.rectangle(0, -22, 28, 4, 0x6fe26f).setOrigin(0.5);
        container.add([body as Phaser.GameObjects.GameObject, label, hpBg, hpFill]);
        container.setDepth(5);
        this.zombieLayer.add(container);
        z = {
          container, body, label, hpBg, hpFill,
          buf: [], lastX: x * 32, lastY: y * 32,
          anim: anim ?? "idle", animT0: performance.now(),
          frame: 0, frameT0: performance.now(),
          facing: facing ?? "S", dieT0: 0, hunter: kind === "hunter",
        };
        this.zombies.set(id, z);
      }
      const now = performance.now();
      z.buf.push([now, x * 32, y * 32]);
      if (z.buf.length > 12) z.buf.shift();
      z.hunter = kind === "hunter";
      z.facing = facing ?? z.facing;
      if ((anim ?? z.anim) !== z.anim) {
        z.anim = anim ?? z.anim;
        z.animT0 = now;
        z.frame = 0;
        z.frameT0 = now;
      }
      const ratio = Math.max(0, Math.min(1, maxHp > 0 ? hp / maxHp : 0));
      z.hpFill.setSize(28 * ratio, 4);
      z.hpFill.setFillStyle(ratio > 0.5 ? 0x6fe26f : ratio > 0.25 ? 0xf2c14e : 0xe5484d);
      if (
        this.zombieTextureReady &&
        z.body instanceof Phaser.GameObjects.Rectangle &&
        this.textures.exists(this.zombieTextureKey)
      ) {
        const img = this.add.image(0, 0, this.zombieTextureKey);
        this.applyMobCell(img, 0, 2, ZOMBIE_SIZE);
        z.container.add(img);
        z.container.sendToBack(img);
        z.body.destroy();
        z.body = img;
      }
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
        if (rows.some((t) => this.solidAt(c, t))) return Math.min(dx, c - r - x);
      }
      return dx;
    }
    const start = Math.floor(x - r);
    const end = Math.floor(x + dx - r);
    for (let c = start; c >= end; c--) {
      if (rows.some((t) => this.solidAt(c, t))) return Math.max(dx, c + 1 + r - x);
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
        if (cols.some((c) => this.solidAt(c, t))) return Math.min(dy, t - r - y);
      }
      return dy;
    }
    const start = Math.floor(y - r);
    const end = Math.floor(y + dy - r);
    for (let t = start; t >= end; t--) {
      if (cols.some((c) => this.solidAt(c, t))) return Math.max(dy, t + 1 + r - y);
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
    const row = this.collision[ty];
    if (!row || tx < 0 || tx >= row.length || row[tx] === 1) return true;
    // Placed blocks block movement (server mirrors this via BlockGrid).
    if (this.blockSet.has(`${tx},${ty}`)) return true;
    // Standing resource nodes (trees/bushes/ore) block until felled.
    if (this.resourceTiles.has(`${tx},${ty}`)) return true;
    return false;
  }

  applySnapshot(snap: SnapshotPayload): void {
    // Authoritative self position for reconciliation. Self is NOT in the
    // players payload anymore (the clone fix), so take it from snap.self.
    this.selfServerPos = { x: snap.self.x, y: snap.self.y };
    this.lastServerRecv = performance.now();
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
    this.updateResourceLayer(snap.resources);
    this.syncProgressBars(snap.res_progress, {
      x: Math.floor(snap.self.x),
      y: Math.floor(snap.self.y),
    });
    // Felled tiles: walkable in prediction until the node regrows.
    this.felledTiles = new Set((snap.res_felled ?? []).map(([x, y]) => `${x},${y}`));
    for (const p of snap.players) this.upsertPlayer(p);
    // Night zombies (web realtime pack): interpolate + animate.
    this.syncZombies(snap.zombies ?? []);
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
      this.updateBlocks(snap.blocks);
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
  }

  // Local self position in tile units (for the HUD + camera sanity).
  get selfPos(): { x: number; y: number } {
    if (this.selfMarker) {
      return { x: this.selfMarker.x / 32, y: this.selfMarker.y / 32 };
    }
    return { x: 0, y: 0 };
  }
}
