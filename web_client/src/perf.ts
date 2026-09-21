// Perf kill-switches for FX overlays — the PC-vs-mobile lag bisector.
//
// Mobile smooth + PC lag pointed at per-frame client render cost, not the
// server or the netcode. On PC three full-window canvases stack up (Phaser
// WebGL + weather 2D + daynight 2D, each painted every frame at dpr up to
// 2x). `?fx=0` turns every optional overlay off so a quick reload can prove
// which layer eats the frame budget:
//   ?fx=0        all overlays off (weather, daynight, cave ambience, canopy fade)
//   ?fx=weather  keep ONLY weather
//   ?fx=daynight keep ONLY the day/night tint
//   ?fx=cave     keep ONLY cave ambience + canopy fade
// F3 prints `fx=...` so the tester can verify what is live.

const raw = typeof window !== "undefined"
  ? new URLSearchParams(window.location.search).get("fx")
  : null;

function flag(name: string): boolean {
  if (raw === null || raw === "") return true; // no param: everything on
  if (raw === "0" || raw === "off") return false;
  return raw.split(",").map((s) => s.trim()).includes(name);
}

export const perf = {
  weather: raw === null || raw === "0" ? false : flag("weather"),
  daynight: raw === null || raw === "0" ? false : flag("daynight"),
  cave: raw === null || raw === "0" ? false : flag("cave"),
  /** Human-readable tag for the F3 debug line. */
  get label(): string {
    if (raw === "0" || raw === "off") return "fx=OFF";
    if (raw === null || raw === "") return "fx=all";
    return `fx=${raw}`;
  },
};
