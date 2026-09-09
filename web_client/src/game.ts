// Phaser game scene: builds the world from the welcome payload (Tiled layers
// + tilesets fetched through the relay), interpolates 20 Hz snapshots to
// 60 fps rendering, follows the camera on the local player.

import Phaser from "phaser";
import type { PlayerPayload, SnapshotPayload, WelcomePayload } from "./protocol";

const PLAYER_SIZE = 22; // px in world space (tile = 32)
const INTERP_BUFFER_MS = 120; // render ~2 ticks behind for smoothness

// Direction name -> unit vector (mirrors game.state.Direction).
const DIR_VECTORS: Record<string, [number, number]> = {
  NORTH: [0, -1], SOUTH: [0, 1], EAST: [1, 0], WEST: [-1, 0],
  NORTH_EAST: [1, -1], NORTH_WEST: [-1, -1],
  SOUTH_EAST: [1, 1], SOUTH_WEST: [-1, 1],
};

interface RemotePlayer {
  container: Phaser.GameObjects.Container;
  label: Phaser.GameObjects.Text;
  webBadge: Phaser.GameObjects.Arc | null;
  // interpolation buffer: [t_recv, x, y]
  buf: [number, number, number][];
  dir: string;
}

export class WorldScene extends Phaser.Scene {
  private welcome: WelcomePayload | null = null;
  private tileTextures = new Map<string, string>(); // image name -> texture key
  private loadedTilesets = new Set<string>(); // tileset images that arrived
  private players = new Map<number, RemotePlayer>();
  private selfMarker: Phaser.GameObjects.Rectangle | null = null;
  private blockLayer: Phaser.GameObjects.Layer | null = null;
  private mapBake: Phaser.GameObjects.Image | null = null;
  private lastBlockSig = "";
  private selfId = 0;
  // --- client-side prediction (instant local movement) ---
  private inputVec = { dx: 0, dy: 0, running: false };
  private selfX = 0; // predicted float position, TILE units
  private selfY = 0;
  private collision: number[][] = []; // collision[y][x] = 1 blocks
  private selfServerPos = { x: 0, y: 0 }; // last authoritative position
  private lastServerRecv = 0;
  // --- facing arm + hover cursor (Kaetram-style) ---
  private selfDir = "SOUTH";
  private lastMoveX = 0; // last nonzero input (arm points here while idle)
  private lastMoveY = 1;
  private arm: Phaser.GameObjects.Triangle | null = null;
  private armVec: { x: number; y: number } | null = null; // smoothed facing
  private hoverSquare: Phaser.GameObjects.Rectangle | null = null;
  private aimCursor: { dx: number; dy: number } | null = null;
  private mouseTile: { x: number; y: number } | null = null;
  private lastHoverUpdate = 0; // throttle hover reposition (perf)
  // --- resource nodes layer (trees/bushes/ore from the server) ---
  private resourceLayer: Phaser.GameObjects.Layer | null = null;
  private resourceTiles = new Map<string, Phaser.GameObjects.Image>();
  private resourceSig = "";
  // Progress bar PER NODE: one bar centred over the node's whole bbox
  // (a 2x2 tree gets a 64px-wide bar, not a sliver on the anchor tile).
  private progressBars = new Map<string, Phaser.GameObjects.Container>();
  private progressFills = new Map<string, Phaser.GameObjects.Rectangle>();
  private lastResProgress = "";
  // Per-node hit counts from the latest action_result echo (tool-adjusted
  // needed total — more accurate than the snapshot's base value).
  private neededByAnchor = new Map<string, number>();
  // Raw latest snapshot progress: anchor -> [hits, needed, bbox] — kept for
  // node grouping + fall animation of nodes that vanish.
  private lastProgressRaw = new Map<string, [number, number, number[][]]>();
  private lastProgressBbox = new Map<string, number[][]>();
  // Falling-tree animation state is derived per sync (collectNodesFromTiles
  // + playFallAnimation) — no persistent bookkeeping needed.
  // Placed blocks (x,y -> id): solid for the local prediction too.
  private blockSet = new Set<string>();
  private lastBlocksSig = "";
  // --- build mode: selected block to place ---
  private selectedBlock = "stone";

  constructor() {
    super("world");
  }

  // No preload(): every texture is generated (shapes) or arrives later via
  // asset_data frames (tilesets fetched through the relay, license-safe).

  buildWorld(welcome: WelcomePayload, fetchAsset: (name: string) => void): void {
    this.welcome = welcome;
    this.selfId = welcome.self.id;
    const map = welcome.map;

    // --- tilesets: request each PNG through the relay (license-safe) ---
    for (const ts of map.tilesets) {
      if (!ts.image) continue;
      if (!this.tileTextures.has(ts.image)) {
        this.tileTextures.set(ts.image, ts.image.replace(/\.png$/i, ""));
        fetchAsset(ts.image);
      }
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
    if (!this.armVec) {
      const v0 = DIR_VECTORS[this.selfDir] ?? DIR_VECTORS.SOUTH;
      this.armVec = { x: v0[0], y: v0[1] };
    }

    // Arm (facing indicator) + hover square. No yellow target frame.
    if (!this.arm) {
      this.arm = this.add.triangle(0, 0, 0, -6, 5, 4, -5, 4, 0xffffff);
      this.arm.setDepth(6);
    }
    if (!this.hoverSquare) {
      this.hoverSquare = this.add.rectangle(0, 0, 30, 30)
        .setStrokeStyle(2, 0x8fd4ff, 0.6)
        .setDepth(3);
      this.hoverSquare.setVisible(false);
    }
    // Resource tiles from the welcome payload (trees etc.).
    this.updateResourceLayer(welcome.resources);

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
    if (this.blockLayer) this.blockLayer.removeAll(true);
    const list: Phaser.GameObjects.Rectangle[] = [];
    for (const [x, y, _id] of blocks) {
      const r = this.add.rectangle(x * 32 + 16, y * 32 + 16, 30, 30, 0x6b5a3e);
      r.setStrokeStyle(2, 0x8a7550);
      list.push(r);
    }
    this.blockLayer = this.add.layer();
    this.blockLayer.add(list);
  }

  updateBlocks(blocks: [number, number, string][]): void {
    this.buildBlocks(blocks);
  }

  private spawnSelf(welcome: WelcomePayload): void {
    const s = welcome.self;
    if (this.selfMarker) {
      // Re-welcome (re-login / reconnect without a page reload): reuse the
      // existing marker — spawning a second one left a frozen "clone" at
      // the spawn point that looked exactly like the player.
      this.selfMarker.setPosition(s.x * 32, s.y * 32);
      return;
    }
    // NOTE: server positions are already TILE-CENTER based (x_f = x + 0.5),
    // so x*32 lands exactly in the middle of the tile. Never add +16 here —
    // that shifts the avatar half a tile off the collision grid.
    this.selfMarker = this.add.rectangle(s.x * 32, s.y * 32, PLAYER_SIZE, PLAYER_SIZE, 0x5865f2);
    this.selfMarker.setStrokeStyle(2, 0xffffff, 0.9);
    this.selfMarker.setName("self");
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
      container.add(body);
      container.add(label);
      rp = { container, label, webBadge: null, buf: [], dir: p.dir };
      this.players.set(p.id, rp);
    }
    rp.buf.push([now, p.x * 32, p.y * 32]);
    if (rp.buf.length > 12) rp.buf.shift();
    rp.dir = p.dir;
  }

  // ---- per-frame update (60fps) ----

  update(_time: number): void {
    // --- client-side prediction: move SELF instantly every frame ---
    // Server speed: walk 4 tiles/s, run 6 tiles/s (config.WEB_*_SPEED).
    this.stepSelf();

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
      }
      const span = next[0] - prev[0];
      const t = span > 0 ? Math.min(1, (now - prev[0]) / span) : 1;
      const x = prev[1] + (next[1] - prev[1]) * t;
      const y = prev[2] + (next[2] - prev[2]) * t;
      rp.container.setPosition(x, y);
    }
  }

  /** One frame of predicted local movement with tile collision. */
  private stepSelf(): void {
    const marker = this.selfMarker;
    if (!marker || !this.welcome) return;
    const v = this.inputVec;
    if (v.dx !== 0 || v.dy !== 0) {
      // Arm points where we walk (Kaetram-style: facing follows movement).
      this.lastMoveX = v.dx;
      this.lastMoveY = v.dy;
      // Do NOT snap selfDir from the input here: applySnapshot owns the
      // 8-way facing so the arm never jerks between a diagonal and its
      // dominant axis (the "giật giật 1 phát" bug).
      // Normalize so diagonal is not faster.
      const len = Math.hypot(v.dx, v.dy) || 1;
      const speed = v.running ? 6.0 : 4.0; // tiles/s, mirrors the server
      const stepX = (v.dx / len) * speed * (1 / 60);
      const stepY = (v.dy / len) * speed * (1 / 60);
      const nx = this.tryMoveAxis(this.selfX, this.selfY, stepX, 0);
      this.selfX = nx.x;
      const ny = this.tryMoveAxis(this.selfX, this.selfY, 0, stepY);
      this.selfY = ny.y;
    }
    this.updateAimVisuals();
    // Gentle server reconciliation: pull toward the authoritative position
    // only when drifting far (teleport/collision mismatch), never fight
    // normal prediction — that would re-introduce input lag.
    const age = performance.now() - this.lastServerRecv;
    if (age > 500) {
      const drift = Math.hypot(this.selfX - this.selfServerPos.x, this.selfY - this.selfServerPos.y);
      if (drift > 2) {
        // Hard correction: prediction diverged (teleport/trap).
        this.selfX = this.selfServerPos.x;
        this.selfY = this.selfServerPos.y;
      } else if (drift > 0.1 && age > 1000) {
        this.selfX += (this.selfServerPos.x - this.selfX) * 0.1;
        this.selfY += (this.selfServerPos.y - this.selfY) * 0.1;
      }
    }
    marker.setPosition(this.selfX * 32, this.selfY * 32);
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
   * Arm points at the facing/aim tile; hover square tracks the mouse.
   * The arm rotates SMOOTHLY: while moving, the direction vector follows
   * the raw input through a short exponential blend (no discrete 8-way
   * snap — the "giật 1 phát rồi mới final" bug), and when the server
   * reports a different facing while idle we blend toward it too.
   */
  private updateAimVisuals(): void {
    if (!this.arm || !this.selfMarker) return;
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
    if (!this.armVec) this.armVec = { x: targetX, y: targetY };
    const k = 0.25;
    this.armVec.x += (targetX - this.armVec.x) * k;
    this.armVec.y += (targetY - this.armVec.y) * k;
    const vecLen = Math.hypot(this.armVec.x, this.armVec.y);
    const ux = vecLen > 0.001 ? this.armVec.x / vecLen : 0;
    const uy = vecLen > 0.001 ? this.armVec.y / vecLen : 1;
    // Anchor = the avatar center (server pos IS the tile center — see
    // spawnSelf; adding +16 here would offset the arm off the body).
    this.arm.setPosition(this.selfX * 32 + ux * 20, this.selfY * 32 + uy * 20);
    this.arm.setRotation(Math.atan2(uy, ux) + Math.PI / 2);

    // Hover square: only reposition on change (throttled) — chasing the
    // mouse every frame caused the drift/lag the old version had.
    const now = performance.now();
    if (this.mouseTile && now - this.lastHoverUpdate > 50) {
      this.lastHoverUpdate = now;
      this.hoverSquare?.setPosition(this.mouseTile.x * 32 + 16, this.mouseTile.y * 32 + 16);
      this.hoverSquare?.setVisible(true);
    } else if (!this.mouseTile) {
      this.hoverSquare?.setVisible(false);
    }
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
   * Sync the per-node progress bars.
   *
   * progress: "ax,ay" -> [hits, base_needed, bbox]. The bar spans the
   * node's WHOLE bbox (a 2x2 tree -> 64px wide bar centred on the tree,
   * not a 30px sliver on one tile), and the fill width animates to
   * hits/needed each update (needed prefers the tool-adjusted count from
   * the action_result echo).
   */
  syncProgressBars(
    progress: Record<string, [number, number, number[][]]>,
  ): void {
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
      if (!bar || !fill) {
        bar = this.add.container(cx, maxY - 6);
        fill = this.add.rectangle(-width / 2, 0, width - 4, 5, 0x6fe26f)
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
      // One-shot "thunk" bounce of the bar on each landed hit.
      this.tweens.add({
        targets: bar,
        scaleY: { from: 1.25, to: 1 },
        duration: 110,
        ease: "Quad.Out",
      });
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

  /** Track the mouse tile for the hover highlight (from main.ts). */
  setMouseTile(tile: { x: number; y: number } | null): void {
    this.mouseTile = tile;
  }

  getMouseTile(): { x: number; y: number } | null {
    return this.mouseTile;
  }

  /** True when a player-placed block stands on this tile. */
  isBlockAt(x: number, y: number): boolean {
    return this.blockSet.has(`${x},${y}`);
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

  /** Axis-separated movement against the tile collision grid. */
  private tryMoveAxis(
    x: number, y: number, dx: number, dy: number,
  ): { x: number; y: number } {
    let nx = x + dx;
    let ny = y + dy;
    const half = 0.35; // player half-width in tiles (a bit smaller than 0.5)
    if (dx !== 0) {
      const edge = nx + Math.sign(dx) * half;
      const tx = Math.floor(edge);
      const tyA = Math.floor(y - half + 0.02);
      const tyB = Math.floor(y + half - 0.02);
      if (this.solidAt(tx, tyA) || this.solidAt(tx, tyB)) nx = x;
    }
    if (dy !== 0) {
      const edge = ny + Math.sign(dy) * half;
      const ty = Math.floor(edge);
      const txA = Math.floor(x - half + 0.02);
      const txB = Math.floor(x + half - 0.02);
      if (this.solidAt(txA, ty) || this.solidAt(txB, ty)) ny = y;
    }
    return { x: nx, y: ny };
  }

  private solidAt(tx: number, ty: number): boolean {
    const row = this.collision[ty];
    if (!row || tx < 0 || tx >= row.length || row[tx] === 1) return true;
    // Placed blocks block movement (server mirrors this via BlockGrid).
    if (this.blockSet.has(`${tx},${ty}`)) return true;
    // Standing resource nodes (trees/bushes) block until felled.
    if (this.resourceTiles.has(`${tx},${ty}`)) return true;
    return false;
  }

  applySnapshot(snap: SnapshotPayload): void {
    // Authoritative self position for reconciliation. Self is NOT in the
    // players payload anymore (the clone fix), so take it from snap.self.
    this.selfServerPos = { x: snap.self.x, y: snap.self.y };
    this.lastServerRecv = performance.now();
    // Build-Mode cursor. Deliberately do NOT take snap.self.dir as the arm
    // target: the server reports the dominant-axis 8-way name (SE -> E),
    // and blending toward it on every key release re-introduced the arm
    // jerk. The last raw input vector IS the true facing — keep it.
    this.aimCursor = snap.self.aim ?? null;
    // Resource nodes: chopped trees vanish / regrow, progress bars sync.
    this.updateResourceLayer(snap.resources);
    this.syncProgressBars(snap.res_progress);
    for (const p of snap.players) this.upsertPlayer(p);
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
    // Keep the local solid set in sync with placed blocks.
    const bsig = sig;
    if (bsig !== this.lastBlocksSig) {
      this.lastBlocksSig = bsig;
      this.blockSet = new Set(snap.blocks.map((b) => `${b[0]},${b[1]}`));
    }
  }

  // Local self position in tile units (for the HUD + camera sanity).
  get selfPos(): { x: number; y: number } {
    if (this.selfMarker) {
      return { x: this.selfMarker.x / 32, y: this.selfMarker.y / 32 };
    }
    return { x: 0, y: 0 };
  }
}
