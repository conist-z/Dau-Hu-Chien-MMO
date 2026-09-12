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
  // Visual-only upscale from the manifest (2 = body spans 2 tiles tall).
  // Pure rendering: collision/positions stay in 1-tile server space.
  private readonly scale: number;

  constructor(scene: Phaser.Scene, manifest: PlayersManifest | null | undefined) {
    this.scene = scene;
    // Manifest may be missing (old server) — callers gate on ready().
    this.manifest = manifest ?? { base: { file: "", frame_w: 32, frame_h: 32, cols: 4, rows: 12, offset_x: 0, offset_y: 0 }, weapons: {}, rows: {}, frames_per_row: 4, speeds: { idle: 250, walk: 120, atk: 50 } };
    this.scale = Math.max(1, Math.min(4, this.manifest.scale ?? 1));
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
      .setDepth(depth)
      .setScale(this.scale);
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
    this.ensureWeapon();
  }

  /** Drive the animation: action + facing + frame clock. Call every tick.
   * `action` is the BASE action (idle/walk) derived from movement — the
   * atk overlay is OWNED by this class: swing() arms a one-shot timer and
   * the atk rows play exactly once over it. Callers must NOT pass "atk"
   * here (they can't know when the swing expires without re-arming it —
   * the stale-param comparison restarted the swing forever: one click,
   * infinite loop). */
  animate(
    x: number,
    y: number,
    action: "idle" | "walk",
    dir: string,
    now: number,
  ): void {
    if (!this.base) return;
    // Position (feet anchor).
    this.base.setPosition(x, y);
    // Atk plays ONCE (count=1 in Kaetram) then falls back to the base action.
    if (this.action === "atk" && now >= this.atkEndsAt) {
      this.action = action;
      this.frameT0 = now;
    }
    const eff = this.action === "atk" ? "atk" : action;
    if (eff !== this.action || dir !== this.dir) {
      // Restart the clock when the effective action or facing changes.
      this.action = eff;
      this.dir = dir;
      this.frame = 0;
      this.frameT0 = now;
    }
    // Advance frames on the manifest speed.
    const speed = this.manifest.speeds[this.action] ?? 200;
    this.frame = Math.floor((now - this.frameT0) / speed) % this.manifest.frames_per_row;
    // Walk animates only while the avatar actually moves (caller passes
    // idle when velocity is zero), idle loops, atk runs once via atkEndsAt.
    this.ensureWeapon(); // weapon sheet may have just registered
    this.applyBaseFrame();
    this.syncWeaponFrame();
  }

  /** True while the one-shot attack animation is playing. */
  get attacking(): boolean {
    return this.action === "atk";
  }

  /** Trigger a one-shot swing (attack/chop/mine feedback). A swing that is
   * still playing is NOT restarted (Kaetram plays the atk rows once per
   * attack; echo-driven re-triggers must never extend it). */
  swing(now: number): void {
    if (this.action === "atk" && now < this.atkEndsAt) return;
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

  /** Create the weapon sprite once its sheet texture has registered. */
  private ensureWeapon(): void {
    if (this.weapon || !this.base || !this.weaponStem) return;
    const entry: SheetEntry | undefined = this.manifest.weapons[this.weaponStem];
    const key = `pd-weapon-${this.weaponStem}`;
    if (!entry || !this.scene.textures.exists(key)) return; // retry next tick
    this.weapon = this.scene.add
      .sprite(this.weaponX(), this.weaponY(), key, 0)
      .setOrigin(0.5, 1) // feet anchor: sprite bottom sits at (x, y)
      .setDepth(this.base.depth - 0.1) // UNDER the body: held items read as
      // being gripped (the hand overlaps them) instead of pasted on top
      .setScale(this.scale);
    this.syncWeaponFrame();
  }

  /** Kaetram alignment, verified from its renderer + sprites.json: body
   * 32x32 draws at offset (-8,-16), weapon 48x48 at (-16,-24) relative to
   * the SAME entity origin => BOTH LAYERS SHARE ONE CENTRE. The weapon
   * frame is just a bigger canvas with the sword art pre-offset toward the
   * hand inside it — no lateral nudge needed here (flipX mirrors around
   * the shared centre, which is what Kaetram does via context.scale(-1,1)).
   * The earlier ±16px/+8px nudge sent the sword flying above the head. */
  private weaponX(): number {
    return this.base!.x;
  }

  /** Shared-centre Y, feet-anchored: body centre = feet − 16·scale,
   * weapon half-height = 24·scale => weapon origin (0.5,1) sits at
   * feet − 16·scale + 24·scale = feet + 8·scale. */
  private weaponY(): number {
    return this.base!.y + 8 * this.scale;
  }

  private rowFor(): number {
    const suffix = DIR_SUFFIX[this.dir] ?? "down";
    const rowName = `${this.action}_${suffix}`;
    const row = this.manifest.rows[rowName];
    return row ?? this.manifest.rows[`idle_${suffix}`] ?? 0;
  }

  private applyBaseFrame(): void {
    if (!this.base) return;
    const tex = this.base.texture;
    if (!tex || !tex.key || tex.key === "__MISSING") return;
    // The sheet registers ASYNC (blob -> Image -> addSpriteSheet). A frame
    // that does not exist yet throws inside Phaser's setFrame and KILLS the
    // whole scene update loop (frozen map: chat/tui DOM kept working, canvas
    // dead). Guard every setFrame with a frame-existence check.
    const idx = this.rowFor() * this.manifest.frames_per_row + this.frame;
    if (!tex.has(String(idx))) return; // sheet not cut yet — skip this tick
    const flip = this.dir === "WEST" || this.dir === "NORTH_WEST";
    this.base.setFrame(idx);
    this.base.setFlipX(flip);
  }

  private syncWeaponFrame(): void {
    if (!this.weapon || !this.base) return;
    const tex = this.weapon.texture;
    if (!tex || !tex.key || tex.key === "__MISSING") return;
    const idx = this.rowFor() * this.manifest.frames_per_row + this.frame;
    if (!tex.has(String(idx))) return; // sheet not cut yet — skip this tick
    const flip = this.dir === "WEST" || this.dir === "NORTH_WEST";
    this.weapon.setFrame(idx);
    this.weapon.setFlipX(flip);
    this.weapon.setPosition(this.weaponX(), this.weaponY());
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
    // Paperdoll sheets are STATIC assets: register each key EXACTLY ONCE.
    // onPaperdollAsset re-invokes this whole function on every asset arrival
    // (base + ~26 sheets => dozens of re-registers): re-adding an existing
    // key warns "already in use" and, worse, removing a texture that a live
    // doll sprite is rendering nulled its glTexture and killed the render
    // loop. Already present => skip (the bytes are identical anyway).
    if (scene.textures.exists(key)) return;
    // Phaser addSpriteSheet needs a Blob URL -> Image; build via DOM decode.
    const blob = new Blob([bytes.slice().buffer], { type: "image/png" });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      // Key cannot exist here (the exists-check above is the only gate and
      // this key is registered exactly once), so addSpriteSheet always wins.
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

/** Register ONE late-arriving weapon sheet (asset pipeline).
 *
 * The full registerPaperdollTextures runs once when base.png arrives, but
 * weapon sheets stream in AFTER it — and since the once-only guard
 * ("Texture key already in use" crash fix), a later re-run would skip them
 * forever, so setWeapon waited on a texture that never registered and
 * nothing appeared in the hand. Each arrival registers itself directly.
 */
export function registerWeaponSheet(
  scene: Phaser.Scene,
  manifest: PlayersManifest,
  stem: string,
  bytes: Uint8Array,
): void {
  const entry = manifest.weapons[stem];
  if (!entry) return;
  const key = `pd-weapon-${stem}`;
  if (scene.textures.exists(key)) return; // static asset: register once
  const blob = new Blob([bytes.slice().buffer], { type: "image/png" });
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => {
    scene.textures.addSpriteSheet(key, img, {
      frameWidth: entry.frame_w,
      frameHeight: entry.frame_h,
    });
    URL.revokeObjectURL(url);
  };
  img.src = url;
}

/** Decode a base64 payload to bytes (shared with the main asset path). */
export function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}
