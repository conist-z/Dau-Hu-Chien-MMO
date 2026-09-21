// Day/night tint INSIDE the Phaser canvas — kills the PC compositor blend
// (the "lag chỉ khi di chuyển" case: idle screen is static so the browser
// skips recompositing the 2D overlay canvases; movement repaints everything
// every frame, and blending the full-window 2D canvases over the WebGL
// canvas on a big window is the stutter).
//
// Same visual contract as daynight.ts (DayNightFx): one full-screen BLACK
// fill at (1 - ambient) + one saturated period-hue wash, driven by the same
// tintFactor()/castColor() math imported from daynight.ts. Rendered as a
// camera-SCROLL-IMMUNE Phaser Rectangle (scrollFactor 0) at depth 2000 —
// above every world sprite, below DOM HUD. GPU-composited by Phaser's
// WebGL pipeline, so the browser only blends ONE canvas (zero 2D overlay
// fill cost, zero extra compositor layer during movement).
//
// Kept behind perf.daynight so ?fx= bisecting still works.

import Phaser from "phaser";
import { tintFactor, castColor, SECONDS_PER_DAY } from "./daynight";

const DARK_STRENGTH = 0.55; // mirror daynight.ts
const MIN_AMBIENT = 0.45;
const CAST_ALPHA = 0.28;

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

export class DayNightPhaser {
  private dark: Phaser.GameObjects.Rectangle | null = null;
  private cast: Phaser.GameObjects.Rectangle | null = null;
  private scene: Phaser.Scene | null = null;
  private clockSec = -1;
  private clockRecvMs = 0;
  private lastKey = "";

  /** Attach to the world scene (called from WorldScene.buildWorld once).
   *  Idempotent: map switches re-run buildWorld — keep the existing rects. */
  attach(scene: Phaser.Scene): void {
    if (this.dark && this.cast && this.scene === scene) return;
    this.scene = scene;
    const W = scene.scale.width + 400; // generous overscan for zoom/rotate
    const H = scene.scale.height + 400;
    this.dark = scene.add.rectangle(0, 0, W, H, 0x000000, 1)
      .setOrigin(0.5)
      .setScrollFactor(0)
      .setDepth(2000);
    this.cast = scene.add.rectangle(0, 0, W, H, 0xffffff, 1)
      .setOrigin(0.5)
      .setScrollFactor(0)
      .setDepth(2001);
    // Centered on the camera every frame via update(); no per-frame alloc.
    scene.events.on("resize", this.handleResize, this);
  }

  private handleResize(): void {
    if (!this.scene || !this.dark || !this.cast) return;
    const W = this.scene.scale.width + 400;
    const H = this.scene.scale.height + 400;
    this.dark.setSize(W, H);
    this.cast.setSize(W, H);
  }

  /** Feed the authoritative in-game second-of-day (same hook as DayNightFx). */
  setClock(secondsOfDay: number): void {
    this.clockSec = secondsOfDay;
    this.clockRecvMs = performance.now();
  }

  /** Per-frame refresh (called from WorldScene.update — cheap: 2 GPU rects). */
  update(): void {
    if (!this.scene || !this.dark || !this.cast) return;
    const sec = this.currentSec();
    if (sec < 0) {
      this.dark.setVisible(false);
      this.cast.setVisible(false);
      return;
    }
    const cam = this.scene.cameras.main;
    this.dark.setPosition(cam.midPoint.x, cam.midPoint.y);
    this.cast.setPosition(cam.midPoint.x, cam.midPoint.y);
    const [r, g, b] = tintFactor(sec);
    const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    const ambient = Math.max(MIN_AMBIENT, 1 - DARK_STRENGTH * (1 - lum));
    const darkA = 1 - ambient;
    const cast = castColor(sec);
    const castA = clamp01((1 - lum) * CAST_ALPHA);
    const key = `${darkA.toFixed(3)}|${Math.round(cast[0])},${Math.round(cast[1])},${Math.round(cast[2])}|${castA.toFixed(3)}`;
    if (key === this.lastKey) return; // skip identical frames (GPU still draws, but no state churn)
    this.lastKey = key;
    if (darkA > 0.004) {
      this.dark.setVisible(true);
      this.dark.setFillStyle(0x000000, darkA);
    } else {
      this.dark.setVisible(false);
    }
    if (castA > 0.004) {
      this.cast.setVisible(true);
      this.cast.setFillStyle(
        (Math.round(cast[0]) << 16) | (Math.round(cast[1]) << 8) | Math.round(cast[2]),
        castA,
      );
    } else {
      this.cast.setVisible(false);
    }
  }

  /** Same interpolation contract as DayNightFx.currentSec(). */
  private currentSec(): number {
    if (this.clockSec < 0) return -1;
    const INGAME_MULT = SECONDS_PER_DAY / 1800; // mirror config default
    const elapsed = (performance.now() - this.clockRecvMs) / 1000;
    return (this.clockSec + elapsed * INGAME_MULT) % SECONDS_PER_DAY;
  }
}

/** Singleton wired from main.ts + game.ts (one overlay for the whole page). */
export const dayNightPhaser = new DayNightPhaser();
