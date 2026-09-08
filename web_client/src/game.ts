// Phaser game scene: builds the world from the welcome payload (Tiled layers
// + tilesets fetched through the relay), interpolates 20 Hz snapshots to
// 60 fps rendering, follows the camera on the local player.

import Phaser from "phaser";
import type { PlayerPayload, SnapshotPayload, WelcomePayload } from "./protocol";

const PLAYER_SIZE = 22; // px in world space (tile = 32)
const INTERP_BUFFER_MS = 120; // render ~2 ticks behind for smoothness

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
  private players = new Map<number, RemotePlayer>();
  private selfMarker: Phaser.GameObjects.Rectangle | null = null;
  private blockLayer: Phaser.GameObjects.Layer | null = null;
  private selfId = 0;

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
    this.buildTileLayers();
    this.buildBlocks(welcome.blocks);

    // --- physics-less world: positions are authoritative from the server ---
    this.cameras.main.setBounds(0, 0, map.width * map.tile_width, map.height * map.tile_height);
    this.cameras.main.setBackgroundColor("#20303c");

    this.spawnSelf(welcome);
    for (const p of welcome.players) this.upsertPlayer(p);

    // Camera follows the self marker smoothly.
    if (this.selfMarker) {
      this.cameras.main.startFollow(this.selfMarker, true, 0.12, 0.12);
    }
  }

  private buildTileLayers(): void {
    const welcome = this.welcome;
    if (!welcome) return;
    const map = welcome.map;
    const tw = map.tile_width;
    const th = map.tile_height;
    for (const layer of map.layers) {
      for (let y = 0; y < map.height; y++) {
        const row = layer.data[y];
        if (!row) continue;
        for (let x = 0; x < map.width; x++) {
          const gid = row[x];
          if (!gid) continue;
          const ts = map.tilesets.find(
            (t) => gid >= t.firstgid && gid < t.firstgid + t.columns * 1000,
          );
          const texKey = ts?.image ? this.tileTextures.get(ts.image) : undefined;
          if (!texKey || !this.textures.exists(texKey)) continue;
          const tex = this.textures.get(texKey);
          const tileW = ts?.tilewidth ?? tw;
          const local = gid - (ts?.firstgid ?? 1);
          const col = local % (ts?.columns ?? 1);
          const rowIdx = Math.floor(local / (ts?.columns ?? 1));
          if (!tex || col * tileW >= tex.getSourceImage().width) continue;
          const img = this.add.image(x * tw + tw / 2, y * th + th / 2, texKey);
          img.setDisplaySize(tw, th);
          img.setCrop(
            (col * tex.getSourceImage().width) / (ts?.columns ?? 1),
            0, tw, th,
          );
          img.setData("gid", gid);
          void rowIdx;
        }
      }
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

  // ---- per-frame update (60fps): interpolate every player ----

  update(_time: number): void {
    const now = performance.now() - INTERP_BUFFER_MS;
    for (const rp of this.players.values()) {
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

  applySnapshot(snap: SnapshotPayload): void {
    for (const p of snap.players) this.upsertPlayer(p);
    // Despawn players no longer present.
    const seen = new Set(snap.players.map((p) => p.id));
    for (const [id, rp] of this.players) {
      if (!seen.has(id)) {
        rp.container.destroy();
        this.players.delete(id);
      }
    }
    this.updateBlocks(snap.blocks);
  }

  // Local self position in tile units (for the HUD + camera sanity).
  get selfPos(): { x: number; y: number } {
    if (this.selfMarker) {
      return { x: this.selfMarker.x / 32, y: this.selfMarker.y / 32 };
    }
    return { x: 0, y: 0 };
  }
}
