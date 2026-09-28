// Low-HP "bloody screen" controller (user 29/09). DOM overlay từ pack
// status_effect_assets_pixel_32x32_v3 — NHẸ: opacity thấp, fade in/out,
// tim đập qua CSS animation với chu kỳ set từ đây theo HP. Gate được gọi
// mỗi snapshot từ main.ts; phía engine/web_api KHÔNG đổi gì.
// Layer safety: cùng pattern weather-fx — pointer-events:none, z-index 1
// (trên canvas, dưới #overlay HUD); mount 1 lần vào #game-root.

export class LowHpFx {
  private root: HTMLElement | null = null;
  private on = false;
  // Debounce band quanh ngưỡng 15% (regen nhấp nhô quanh 0.15): bật <=15%,
  // tắt >=18% — không flicker khi HP dao động sát ngưỡng.
  private static readonly ENTER = 0.15;
  private static readonly EXIT = 0.18;

  /** Mount once into #game-root (after the canvas layers). Idempotent.
   * 1 lớp gân máu (user 29/09 rút gọn: bỏ drip/drops — blob máu trông bẩn
   * khi lên game thật; giữ veins + tim đập). */
  mount(parent: HTMLElement): void {
    if (this.root && this.root.parentElement === parent) return;
    const el = document.createElement("div");
    el.id = "lowhp-overlay";
    el.innerHTML = '<div class="lowhp-veins"></div>';
    el.style.pointerEvents = "none";
    parent.appendChild(el);
    this.root = el;
  }

  /**
   * Feed the latest self hp/max_hp (20 Hz snapshot). Handles band debounce,
   * .on toggle (CSS transition = fade in/out) and the heartbeat cadence
   * (--beat-s: 1.15s at 15% -> ~0.75s near 0 — tim đập nhanh dần).
   * dead=true always clears the effect (death veil takes over the screen).
   */
  setRatio(hp: number, maxHp: number, dead: boolean): void {
    if (!this.root) return;
    const ratio = maxHp > 0 ? hp / maxHp : 1;
    const shouldOn = this.on
      ? ratio < LowHpFx.EXIT && !dead // stay on until recovered past the band
      : ratio <= LowHpFx.ENTER && !dead && maxHp > 0;
    if (shouldOn !== this.on) {
      this.on = shouldOn;
      this.root.classList.toggle("on", this.on);
      if (this.on) {
        // Lazy warm: first activation triggers the 3 webp fetches (274KB
        // total) only for players who actually cross the threshold.
        void this.root.offsetWidth; // flush so the fade transition always runs
      }
    }
    if (this.on) {
      // Heartbeat cadence scales with severity (outside the band = default).
      const severity = Math.max(0, Math.min(1, (LowHpFx.ENTER - ratio) / LowHpFx.ENTER));
      this.root.style.setProperty("--beat-s", `${(1.15 - 0.4 * severity).toFixed(2)}s`);
    }
  }
}

export const lowHpFx = new LowHpFx();
