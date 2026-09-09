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
  private hoverSquare: Phaser.GameObjects.Rectangle | null = null;
  private aimCursor: { dx: number; dy: number } | null = null;
  private mouseTile: { x: number; y: number } | null = null;
  private lastHoverUpdate = 0; // throttle hover reposition (perf)
  // --- resource nodes layer (trees/bushes/ore from the server) ---
  private resourceLayer: Phaser.GameObjects.Layer | null = null;
  private resourceTiles = new Map<string, Phaser.GameObjects.Image>();
  private resourceSig = "";
  private progressBars = new Map<string, Phaser.GameObjects.Container>();
  private lastResProgress = "";
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
    return {
      dx: Math.round(tile.x - this.selfX),
      dy: Math.round(tile.y - this.selfY),
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

    // Resource tiles are drawn as a separate dynamic layer (choppable),
    // so the base bake must EXCLUDE them or chopped trees would leave
    // ghosts painted into the canvas.
    const resourceSet = new Set(
      welcome.resources.map(([x, y]) => `${x},${y}`),
    );

    const canvas = document.createElement("canvas");
    canvas.width = map.width * tw;
    canvas.height = map.height * th;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    for (const layer of map.layers) {
      for (let y = 0; y < map.height; y++) {
        const row = layer.data[y];
        if (!row) continue;
        for (let x = 0; x < map.width; x++) {
          const gid = row[x];
          if (!gid) continue;
          if (resourceSet.has(`${x},${y}`)) continue; // dynamic layer draws it
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
    this.selfMarker = this.add.rectangle(s.x * 32, s.y * 32, PLAYER_SIZE, PLAYER_SIZE, 0x5865f2);
    this.selfMarker.setStrokeStyle(2, 0xffffff, 0.9);
    this.selfMarker.setName("self");
  }
  private upsertPlayer(p: PlayerPayload): void {
    if (p.id === this.selfId) {
      // Authoritative self position from the server (reconciliation only —
      // rendering stays on the predicted position for zero perceived lag).
      this.selfServerPos = { x: p.x, y: p.y };
      this.lastServerRecv = performance.now();
      return;
    }
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
      this.selfDir = this.dominantDir(v.dx, v.dy);
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

  /** Dominant 8-way direction name from a movement vector. */
  private dominantDir(dx: number, dy: number): string {
    if (Math.abs(dx) > Math.abs(dy)) return dx > 0 ? "EAST" : "WEST";
    if (Math.abs(dy) > Math.abs(dx)) return dy > 0 ? "SOUTH" : "NORTH";
    if (dx > 0) return dy > 0 ? "SOUTH_EAST" : "NORTH_EAST";
    return dy > 0 ? "SOUTH_WEST" : "NORTH_WEST";
  }

  /** Arm points at the facing/aim tile; hover square tracks the mouse. */
  private updateAimVisuals(): void {
    if (!this.arm || !this.selfMarker) return;
    // Arm direction: Build-Mode cursor when active, else last movement.
    let ux: number;
    let uy: number;
    if (this.aimCursor) {
      const len = Math.hypot(this.aimCursor.dx, this.aimCursor.dy) || 1;
      ux = this.aimCursor.dx / len;
      uy = this.aimCursor.dy / len;
    } else {
      const len = Math.hypot(this.lastMoveX, this.lastMoveY) || 1;
      ux = this.lastMoveX / len;
      uy = this.lastMoveY / len;
    }
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

  /** Sync the resource layer with the server's visible-tile list. */
  updateResourceLayer(tiles: [number, number, number][]): void {
    const sig = tiles.map((t) => t.join(",")).join(";");
    if (sig === this.resourceSig) return;
    this.resourceSig = sig;
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
    // Progress bars of vanished nodes are stale.
    this.syncProgressBars({});
  }

  /** Find the tileset texture a gid belongs to (firstgid ranges). */
  private textureForGid(gid: number): string | null {
    const map = this.welcome?.map;
    if (!map) return null;
    const ts = map.tilesets.find((t) => gid >= t.firstgid);
    if (!ts?.image) return null;
    const key = this.tileTextures.get(ts.image);
    return key && this.textures.exists(key) ? key : null;
  }

  /** Draw/update the small progress bar above nodes being harvested. */
  syncProgressBars(progress: Record<string, number>): void {
    const sig = JSON.stringify(progress);
    if (sig === this.lastResProgress) return;
    this.lastResProgress = sig;
    const wanted = new Set(Object.keys(progress));
    for (const [key, bar] of this.progressBars) {
      if (!wanted.has(key)) {
        bar.destroy();
        this.progressBars.delete(key);
      }
    }
    for (const [key, hits] of Object.entries(progress)) {
      if (hits <= 0) continue;
      let bar = this.progressBars.get(key);
      if (!bar) {
        const [ax, ay] = key.split(",").map(Number);
        bar = this.add.container(ax * 32 + 16, ay * 32 - 8);
        const bg = this.add.rectangle(0, 0, 30, 6, 0x000000, 0.55);
        const fill = this.add.rectangle(-14, 0, Math.max(2, (hits / 4) * 28), 4, 0x6fe26f)
          .setOrigin(0, 0.5);
        bar.add([bg, fill]);
        bar.setDepth(20);
        this.progressBars.set(key, bar);
      }
    }
  }

  /** Track the mouse tile for the hover highlight (from main.ts). */
  setMouseTile(tile: { x: number; y: number } | null): void {
    this.mouseTile = tile;
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
    return !row || tx < 0 || tx >= row.length || row[tx] === 1;
  }

  applySnapshot(snap: SnapshotPayload): void {
    // Build-Mode cursor + server-side facing (authoritative).
    this.aimCursor = snap.self.aim ?? null;
    if (!this.inputVec.dx && !this.inputVec.dy) {
      this.selfDir = snap.self.dir || this.selfDir;
      const v = DIR_VECTORS[this.selfDir];
      if (v) {
        this.lastMoveX = v[0];
        this.lastMoveY = v[1];
      }
    }
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
  }

  // Local self position in tile units (for the HUD + camera sanity).
  get selfPos(): { x: number; y: number } {
    if (this.selfMarker) {
      return { x: this.selfMarker.x / 32, y: this.selfMarker.y / 32 };
    }
    return { x: 0, y: 0 };
  }
}
