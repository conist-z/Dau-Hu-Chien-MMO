// Paperdoll sprite system (Kaetram parity) — web client side.
//
// A PaperdollBody owns two Phaser sprites (base body + optional weapon
// overlay) and plays the SHARED Kaetram animation grid: idle/walk/atk x
// down/right/up rows, 4 frames per row, same frame index on every layer
// (weapon swings exactly with the body — mirrors Kaetram's renderer
// drawSprite which reads one `frame.index` for all equipment layers).
// Facing LEFT reuses the right row with flipX. All geometry comes from the
// server's players_manifest (data-driven — nothing hard-coded per sheet).

import Phaser from "phaser";
import type { PlayersManifest, SheetEntry } from "./protocol";

// Client dir names (DIR_VECTORS keys) -> manifest row suffix.
const DIR_SUFFIX: Record<string, string> = {
  SOUTH: "down", NORTH: "up", EAST: "right", WEST: "right",
  NORTH_EAST: "right", NORTH_WEST: "right",
  SOUTH_EAST: "down", SOUTH_WEST: "down",
};

export class PaperdollBody {
  private scene: Phaser.Scene;
  private manifest: PlayersManifest;
  private base: Phaser.GameObjects.Sprite | null = null;
  private weapon: Phaser.GameObjects.Sprite | null = null;
  // Animation clock state.
  private action: "idle" | "walk" | "atk" = "idle";
  private dir = "SOUTH";
  private frame = 0;
  private frameT0 = 0;
  private atkEndsAt = 0; // performance.now() timestamp; atk runs once
  private weaponStem: string | null = null;

  constructor(scene: Phaser.Scene, manifest: PlayersManifest | null | undefined) {
    this.scene = scene;
    // Manifest may be missing (old server) — callers gate on ready().
    this.manifest = manifest ?? { base: { file: "", frame_w: 32, frame_h: 32, cols: 4, rows: 12, offset_x: 0, offset_y: 0 }, weapons: {}, rows: {}, frames_per_row: 4, speeds: { idle: 250, walk: 120, atk: 50 } };
  }

  get ready(): boolean {
    return !!this.manifest.base.file && this.scene.textures.exists("pd-base");
  }

  /** Create the two sprites at (x, y) — world px, feet anchor. */
  spawn(x: number, y: number, depth: number): void {
    if (this.base) return;
    this.base = this.scene.add
      .sprite(x, y, "pd-base", 0)
      .setOrigin(0.5, 1) // feet anchor: sprite bottom sits at (x, y)
      .setDepth(depth);
    this.applyBaseFrame();
  }

  /** Swap the weapon overlay for a held item (null = bare hand). */
  setWeapon(stem: string | null): void {
    if (stem === this.weaponStem) return;
    this.weaponStem = stem;
    if (this.weapon) {
      this.weapon.destroy();
      this.weapon = null;
    }
    if (!stem) return;
    const entry: SheetEntry | undefined = this.manifest.weapons[stem];
    const key = `pd-weapon-${stem}`;
    if (!entry || !this.scene.textures.exists(key)) return;
    this.weapon = this.scene.add
      .sprite(this.base!.x, this.base!.y + this.weaponYOff(entry), key, 0)
      .setOrigin(0.5, 1)
      .setDepth(this.base!.depth + 0.1);
    this.syncWeaponFrame();
  }

  /** Drive the animation: action + facing + frame clock. Call every tick. */
  animate(
    x: number,
    y: number,
    action: "idle" | "walk" | "atk",
    dir: string,
    now: number,
  ): void {
    if (!this.base) return;
    // Position (feet anchor).
    this.base.setPosition(x, y);
    // Atk plays ONCE (count=1 in Kaetram) then falls back to idle.
    if (this.action === "atk" && now >= this.atkEndsAt) {
      this.action = "idle";
    }
    if (action !== this.action || dir !== this.dir) {
      // Restart the clock when the action or facing changes (walk loops
      // restart cleanly; atk restart = re-swing, matching Kaetram behaviour).
      if (action === "atk" && this.action !== "atk") {
        this.atkEndsAt = now + this.manifest.speeds.atk * this.manifest.frames_per_row;
      }
      this.action = action;
      this.dir = dir;
      this.frame = 0;
      this.frameT0 = now;
    }
    // Advance frames on the manifest speed.
    const speed = this.manifest.speeds[this.action] ?? 200;
    this.frame = Math.floor((now - this.frameT0) / speed) % this.manifest.frames_per_row;
    // Walk animates only while the avatar actually moves (caller passes
    // idle when velocity is zero), idle loops, atk runs once via atkEndsAt.
    this.applyBaseFrame();
    this.syncWeaponFrame();
  }

  /** True while the one-shot attack animation is playing. */
  get attacking(): boolean {
    return this.action === "atk";
  }

  /** Trigger a one-shot swing (attack/chop/mine feedback). */
  swing(now: number): void {
    this.action = "atk";
    this.atkEndsAt = now + this.manifest.speeds.atk * this.manifest.frames_per_row;
    this.frame = 0;
    this.frameT0 = now;
  }

  destroy(): void {
    this.base?.destroy();
    this.weapon?.destroy();
    this.base = null;
    this.weapon = null;
  }

  // ---- internals ----

  /** Weapon bottom offset: centre the taller weapon frame on the body. */
  private weaponYOff(_entry: SheetEntry): number {
    // Kaetram: weapon offsetY=-24 vs body offsetY=-32 => weapon bottom sits
    // 8px BELOW the body bottom (the 48px frame centres on the 32px body).
    // With origin (0.5, 1) anchored at feet, weapon origin sits at feet+8.
    return 8;
  }

  private rowFor(): number {
    const suffix = DIR_SUFFIX[this.dir] ?? "down";
    const rowName = `${this.action}_${suffix}`;
    const row = this.manifest.rows[rowName];
    return row ?? this.manifest.rows[`idle_${suffix}`] ?? 0;
  }

  private applyBaseFrame(): void {
    if (!this.base) return;
    const row = this.rowFor();
    // LEFT mirrors the right-facing row.
    const flip = this.dir === "WEST" || this.dir === "NORTH_WEST";
    this.base.setFrame(row * this.manifest.frames_per_row + this.frame);
    this.base.setFlipX(flip);
  }

  private syncWeaponFrame(): void {
    if (!this.weapon || !this.base) return;
    const row = this.rowFor();
    const flip = this.dir === "WEST" || this.dir === "NORTH_WEST";
    this.weapon.setFrame(row * this.manifest.frames_per_row + this.frame);
    this.weapon.setFlipX(flip);
    this.weapon.setPosition(this.base.x, this.base.y + 8);
  }
}

/** Register the paperdoll textures from raw PNG bytes (asset pipeline). */
export function registerPaperdollTextures(
  scene: Phaser.Scene,
  manifest: PlayersManifest,
  baseBytes: Uint8Array,
  weaponBytes: Record<string, Uint8Array>,
): void {
  const addSheet = (key: string, bytes: Uint8Array, entry: SheetEntry): void => {
    if (scene.textures.exists(key)) scene.textures.remove(key);
    // Phaser addSpriteSheet needs a Blob URL -> Image; build via DOM decode.
    const blob = new Blob([bytes.slice().buffer], { type: "image/png" });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      if (scene.textures.exists(key)) scene.textures.remove(key);
      scene.textures.addSpriteSheet(key, img, {
        frameWidth: entry.frame_w,
        frameHeight: entry.frame_h,
      });
      URL.revokeObjectURL(url);
    };
    img.src = url;
  };
  addSheet("pd-base", baseBytes, manifest.base);
  for (const [stem, bytes] of Object.entries(weaponBytes)) {
    const entry = manifest.weapons[stem];
    if (entry) addSheet(`pd-weapon-${stem}`, bytes, entry);
  }
}

/** Decode a base64 payload to bytes (shared with the main asset path). */
export function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}
