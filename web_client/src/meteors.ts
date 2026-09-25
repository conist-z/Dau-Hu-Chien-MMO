// Meteor shower FX (game/meteors.py server events): warning ring -> diagonal
// fall (ui/fx/meteor/flyNN frames) -> impact explosion (boomN frames) ->
// directional camera shake. Client-only animation lane driven by the
// snapshot's `meteors` rows: [id, tx, ty, dir, impact_in_s].

import Phaser from "phaser";

const FLY_FRAMES = 15;
const BOOM_FRAMES = 7;
const FALL_MS = 900;          // flight duration once the warning expires
const BOOM_MS = 1500;         // impact explosion lingers (user: the old 600ms
                              // burst ended before the snapshot-delivered ore
                              // even appeared — felt like the rock popped in
                              // BEFORE the explosion)
const METEOR_SPRITE_SCALE = 2; // fall sprite x2 (user request)
const WARNING_MS = 8000;      // must match server WARNING_SECONDS
const SHAKE_RADIUS_PX = 14 * 32;  // mirrors server IMPACT_SHAKE_RADIUS (tiles)
const SHAKE_MAX_PX = 22.0;        // mirrors server IMPACT_MAX_SHAKE

interface ActiveMeteor {
  id: number;
  tx: number; ty: number;      // target tile
  dir: "left" | "right";
  impactAt: number;            // performance.now() when the meteor lands
  fallStart?: number;
  phase: "warning" | "falling" | "done";
  sprite: Phaser.GameObjects.Image | null;
  trail: Phaser.GameObjects.Arc[];
  boom: Phaser.GameObjects.Image | null;
  boomStart: number;
  ring: Phaser.GameObjects.Graphics;
}

export class MeteorFx {
  private scene: Phaser.Scene | null = null;
  private active = new Map<number, ActiveMeteor>();
  private texturesReady = false;
  private shakeDir = { x: 0, y: 0, mag: 0 };
  private shakeDirty = false;
  private updateBound = this.update.bind(this);

  attach(scene: Phaser.Scene): void {
    if (this.scene === scene) return;
    this.scene = scene;
    this.loadTextures();
    scene.events.on(Phaser.Scenes.Events.UPDATE, this.updateBound);
    scene.events.once(Phaser.Scenes.Events.SHUTDOWN, () => {
      this.scene = null;
      this.active.clear();
    });
  }

  private loadTextures(): void {
    const scene = this.scene!;
    if (this.texturesReady) return;
    for (let i = 0; i < FLY_FRAMES; i++) {
      const key = `met-fly${i}`;
      if (scene.textures.exists(key)) continue;
      scene.load.image(key, `ui/fx/meteor/fly${i * 2}.png`);
    }
    for (let i = 0; i < BOOM_FRAMES; i++) {
      const key = `met-boom${i}`;
      if (scene.textures.exists(key)) continue;
      scene.load.image(key, `ui/fx/meteor/boom${i}.png`);
    }
    scene.load.once(Phaser.Loader.Events.COMPLETE, () => { this.texturesReady = true; });
    scene.load.start();
  }

  /** Feed a snapshot's `meteors` rows: [id, tx, ty, dir, impact_in_s]. */
  sync(rows: [number, number, number, string, number][] | undefined): void {
    if (!this.scene || !rows) return;
    const now = performance.now();
    const seen = new Set<number>();
    for (const [id, tx, ty, dir, eta] of rows) {
      seen.add(id);
      if (!this.active.has(id)) {
        this.start(id, tx, ty, dir === "right" ? "right" : "left", now + Math.max(0, eta) * 1000);
      } else {
        // Re-sync the countdown (server clock is truth; client drift heals).
        const m = this.active.get(id)!;
        const serverEta = now + Math.max(0, eta) * 1000;
        if (Math.abs(serverEta - m.impactAt) > 400) m.impactAt = serverEta;
      }
    }
    // Rows vanished => server landed them; the local anim finishes on its own.
  }

  private start(id: number, tx: number, ty: number, dir: "left" | "right", impactAt: number): void {
    const scene = this.scene!;
    // Bounds/tile come from the scene (WorldScene keeps them in `welcome.map`
    // + `tilePx`, typed loosely here to keep this module decoupled).
    const s = scene as unknown as {
      welcome?: { map: { width: number; height: number } };
    };
    const w = s.welcome?.map.width ?? 0;
    const h = s.welcome?.map.height ?? 0;
    // Only reject out-of-bounds tiles when the bounds are actually known —
    // an unknown map size must never swallow the whole event.
    if (w > 0 && h > 0 && (tx < 0 || ty < 0 || tx >= w || ty >= h)) return;

    const ring = scene.add.graphics().setDepth(950);
    const m: ActiveMeteor = { id, tx, ty, dir, impactAt, phase: "warning", sprite: null, trail: [], boom: null, boomStart: 0, ring };
    this.active.set(id, m);
  }

  private update(): void {
    if (!this.scene) return;
    const scene = this.scene;
    const now = performance.now();
    const tile = (scene as any).tilePx || 32;

    for (const [id, m] of [...this.active.entries()]) {
      // ---- warning ring ---------------------------------------------
      if (m.phase === "warning") {
        const p = Math.min(1, 1 - (m.impactAt - now) / WARNING_MS);
        m.ring.clear();
        const blink = Math.sin(now / 90) > 0 ? 0.9 : 0.45;
        const cx = (m.tx + 0.5) * tile, cy = (m.ty + 0.5) * tile;
        const R = tile * (1.6 - 0.6 * p);
        m.ring.lineStyle(2, 0xff3c3c, blink);
        m.ring.strokeCircle(cx, cy, R);
        m.ring.lineStyle(2, 0xffc850, blink * 0.6);
        m.ring.strokeCircle(cx, cy, tile * 0.55);
        m.ring.lineStyle(1, 0xff3c3c, blink * 0.8);
        m.ring.lineBetween(cx - 4, cy, cx + 4, cy);
        m.ring.lineBetween(cx, cy - 4, cx, cy + 4);
        if (now >= m.impactAt) {
          m.phase = "falling";
          m.ring.clear();
          m.fallStart = now;
        }
      }
      // ---- falling ----------------------------------------------------
      if (m.phase === "falling") {
        (m as any).fallStart ??= now;
        const p = Math.min(1, (now - (m as any).fallStart) / FALL_MS);
        const ease = p * p;
        const tile2 = tile;
        const cx = (m.tx + 0.5) * tile2, cy = (m.ty + 0.5) * tile2;
        const dist = 700;
        const ang = 40 * Math.PI / 180;
        const sx = m.dir === "left" ? cx - dist : cx + dist;
        const sy = cy - dist * Math.tan(ang) * 0.9;
        const fx = sx + (cx - sx) * ease;
        const fy = sy + (cy - sy) * ease;

        if (!m.sprite) {
          m.sprite = scene.add.image(fx, fy, "met-fly0").setDepth(960).setScale(METEOR_SPRITE_SCALE);
        }
        m.sprite.setPosition(fx, fy);
        if (m.dir === "right") m.sprite.setFlipX(true);
        m.sprite.setRotation(40 * Math.PI / 180 * (m.dir === "right" ? -1 : 1));
        // frame cycling
        const fi = Math.floor(now / 60) % FLY_FRAMES;
        m.sprite.setTexture(`met-fly${fi}`);
        // trail (fading dots, cheap)
        if (this.scene && m.trail.length < 24) {
          const dot = scene.add.circle(fx, fy, 3 + (p * 8), 0xffa030, 0.45).setDepth(955);
          m.trail.push(dot);
        }
        for (const dot of m.trail) {
          dot.setAlpha(Math.max(0, dot.alpha - 0.02));
        }
        if (p >= 1) {
          m.phase = "done";
          m.boomStart = now;
          m.sprite?.destroy();
          for (const dot of m.trail) dot.destroy();
          m.trail = [];
          this.doImpact(m, cx, cy);
        }
      }
      if (m.phase === "done") {
        const p = (now - m.boomStart) / BOOM_MS;
        if (m.boom && p < 1) {
          const fi = Math.min(BOOM_FRAMES - 1, Math.floor(p * BOOM_FRAMES));
          m.boom.setTexture(`met-boom${fi}`);
        }
        if (p >= 1) {
          m.boom?.destroy();
          m.ring.destroy();
          this.active.delete(id);
        }
      }
    }

    // camera shake decay — MANUAL integer jitter (never Phaser cam.shake:
    // its subpixel offsets cracked the pixel art and exposed the raw map).
    // Max ±2 screen px, exponential decay, hard 0 cutoff.
    if (this.shakeDir.mag > 0.02) {
      this.shakeDir.mag *= Math.pow(0.82, 16.7 / 16);
      if (this.shakeDir.mag < 0.02) this.shakeDir.mag = 0;
      const cam = scene.cameras.main;
      const t = Math.min(2, Math.round(this.shakeDir.mag / 6));
      if (t > 0) {
        cam.setFollowOffset(
          -this.shakeDir.x * t + (Math.random() - 0.5),
          -this.shakeDir.y * t + (Math.random() - 0.5),
        );
      } else {
        cam.setFollowOffset(0, 0);
      }
    } else if (this.shakeDirty) {
      scene.cameras.main.setFollowOffset(0, 0);
      this.shakeDirty = false;
    }
  }

  /** Active events near (tx, ty) — public read-only view for the ore-reveal
   * gate (game.ts must not reach into the private map). */
  busyNear(tx: number, ty: number): ActiveMeteor[] {
    const out: ActiveMeteor[] = [];
    for (const m of this.active.values()) {
      if (Math.abs(m.tx - tx) <= 2 && Math.abs(m.ty - ty) <= 2) out.push(m);
    }
    return out;
  }

  private doImpact(m: ActiveMeteor, cx: number, cy: number): void {
    const scene = this.scene!;
    m.boom = scene.add.image(cx, cy, "met-boom0").setDepth(970);
    // Directional shake for SELF only: the vector from impact to the camera.
    // Phaser's shake `intensity` is a FRACTION of viewport size, not px —
    // the old px/400 number (mag 22 -> 0.055+) made the whole screen fly.
    // Cap at 0.004 (~5px on a 1280 viewport) and scale down with distance.
    const cam = scene.cameras.main;
    const selfX = cam.midPoint.x, selfY = cam.midPoint.y;
    const dx = selfX - cx, dy = selfY - cy;
    const dist = Math.hypot(dx, dy);
    if (dist < SHAKE_RADIUS_PX && dist > 0.001) {
      const mag = SHAKE_MAX_PX * (1 - dist / SHAKE_RADIUS_PX); // px, for our decay lane
      this.shakeDir = { x: dx / dist, y: dy / dist, mag };
      this.shakeDirty = true;
      cam.flash(120, 255, 240, 200, false);
    }
  }
}

export const meteorFx = new MeteorFx();

/** True while a meteor fall/impact FX is still animating near (tx, ty).
 *
 * The resource layer consults this before revealing a freshly spawned
 * meteor-ore sprite: the ore only fades in once the explosion has finished,
 * so the rock never pops into existence before/during the impact
 * ("xuất hiện trước cả khi vụ nổ xảy ra là sai"). Called from game.ts.
 */
export function meteorFxBusyNear(tx: number, ty: number): boolean {
  for (const m of meteorFx.busyNear(tx, ty)) {
    if (m.phase !== "warning") return true;
  }
  return false;
}
