// Entry point: wires Net (transport) + WorldScene (Phaser) + Hud (DOM) +
// KeyboardInput. OAuth return handling + asset texture cache live here.

import Phaser from "phaser";
import { WorldScene } from "./game";
import { KeyboardInput } from "./input";
import { MobileControls } from "./mobile_controls";
import { Net } from "./net";
import type { InventoryPayload, WelcomePayload } from "./protocol";
import { Hud } from "./ui";
import { weatherFx } from "./weather";
import { meteorFx } from "./meteors";
import { previewPanel } from "./preview_panel";
// Day/night tint: kept as its own DOM canvas BUT throttled to 8 Hz + dpr 1 +
// duplicate-frame skip (daynight.ts) — the per-rAF full-window repaint was
// the PC-only lag. Same visual as before.
import { dayNightPhaser } from "./daynight_phaser";
import { perf } from "./perf";
import { TravelVeil } from "./transitions";

const assetTextures = new Map<string, string>(); // image file name -> texture key
let welcome: WelcomePayload | null = null; // kept for held-item lookups
/** Channel to auto-join once a fresh login_result lands after a stale-token
 * error (silent reconnect path; cleared on use or when joining manually). */
let pendingRejoinChannel: string | null = null;

// Quick-play guest login: derive a stable pseudo user_id from localStorage
// so the same browser keeps the same identity/bag across sessions.
const hud = new Hud();
// Expose for the 🧪 preview panel's local demos (status effects rail).
(window as unknown as { hud?: Hud }).hud = hud;
// TRAVEL VEIL: iris transition + video loading driven by REAL load progress.
// Topmost DOM layer (z-index 6000) — hides the slow server handoff.
const travelVeil = new TravelVeil();
const scene = new WorldScene();

// =====================================================================
// MOBILE-UI MODE FLAG (PC ↔ mobile parity rule, docs §1b)
// =====================================================================
// One body class drives EVERY mobile decision: CSS layouts (hub tray,
// chat chip, rotate veil…), the touch-controls wiring, fullscreen and the
// two-tap aim mode. Resolution order (first hit wins):
//   1. ?mobile=1  → force ON    (desktop preview of the phone build)
//   2. ?mobile=0  → force OFF   (real phone, prefer the PC layout)
//   3. Shift+F9   → LIVE TOGGLE while playing (also persists)
//   4. localStorage "mobile-ui" ("1"/"0" from a previous toggle)
//   5. Auto: real touch device (pointer: coarse) or a narrow viewport
// Shift+F9 reloads the page so the class-gated CSS + fullscreen + aim
// wiring all re-initise coherently — no half-toggled hybrid state.
const MOBILE_UI_KEY = "mobile-ui";
const urlMobile = new URLSearchParams(location.search).get("mobile");
const storedMobile = localStorage.getItem(MOBILE_UI_KEY);
const isTouchDevice =
  window.matchMedia("(pointer: coarse)").matches ||
  Math.min(window.innerWidth, window.innerHeight) <= 480;
const wantMobileUI =
  urlMobile === "1" ? true :
  urlMobile === "0" ? false :
  storedMobile === "1" ? true :
  storedMobile === "0" ? false :
  isTouchDevice;
if (wantMobileUI) {
  document.body.classList.add("mobile-ui");
  if (isTouchDevice) document.body.classList.add("is-touch-device");
}

/** Shift+F9: flip the mobile UI on desktop/phone and reload — the CSS is
 *  class-gated, so a reload is the only way to re-run the gate wiring
 *  (fullscreen, chat chip injection, touch layer) coherently. */
window.addEventListener("keydown", (e) => {
  if (e.key === "F9" && e.shiftKey) {
    e.preventDefault();
    const next = document.body.classList.contains("mobile-ui") ? "0" : "1";
    localStorage.setItem(MOBILE_UI_KEY, next);
    const url = new URL(location.href);
    url.searchParams.delete("mobile"); // the toggle wins over a stale param
    location.replace(url.toString());
  }
});

/** Single source of truth for "run the mobile gameplay" — CSS class AND
 *  the JS gates below all read this. */
const MOBILE_UI = wantMobileUI;

// ---- LANDSCAPE, final form (NO CSS rotation, NO coordinate remap) ----
// History: a CSS 90°-rotate + pointer-remap hack (“FORCE LANDSCAPE”) was
// the source of a whole bug family — knob misplacement, aim box offsets,
// dead native scroll inside the rotated frame, login freeze (innerWidth
// recursion). Removed entirely. The one true mechanism:
//   fullscreen + screen.orientation.lock("landscape")
// which OVERRIDES the phone's auto-rotate (auto on or off is irrelevant).
// Devices without the lock API (iOS Safari) get a 2.5s “rotate your
// phone” splash fallback, gated on body.orientation-lock-failed.
if (isTouchDevice) {
  const tryLockLandscape = async (): Promise<void> => {
    // Lock only sticks while fullscreen — request/refresh it first.
    if (!document.fullscreenElement) {
      const root = document.documentElement;
      const fs = root.requestFullscreen?.({ navigationUI: "hide" })
        ?? (root as HTMLElement & { webkitRequestFullscreen?: () => Promise<void> })
          .webkitRequestFullscreen?.();
      try { await fs; } catch { /* denied — lock will likely fail too */ }
    }
    const so = screen.orientation as (ScreenOrientation & {
      lock?: (o: string) => Promise<void>;
    }) | undefined;
    try {
      await so?.lock?.("landscape");
      document.body.classList.remove("orientation-lock-failed");
    } catch {
      // Real failure (no API / rejected): mark it so the splash fallback
      // can show if the device is physically portrait.
      document.body.classList.add("orientation-lock-failed");
    }
  };
  // At welcome (inside the play-click gesture chain) + on every visibility
  // change back into the game (lock can drop when the browser tab loses
  // focus / the user swipes the URL bar back).
  (window as unknown as { __lockLandscape?: () => Promise<void> }).__lockLandscape = tryLockLandscape;
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && MOBILE_UI) void tryLockLandscape();
  });
  // Splash re-trigger: only meaningful when the lock failed and the device
  // is physically portrait — flip the animation back on.
  const veil = document.getElementById("rotate-veil");
  if (veil) {
    const retriggerSplash = (): void => {
      if (!document.body.classList.contains("orientation-lock-failed")) return;
      if (!window.matchMedia("(orientation: portrait)").matches) return;
      veil.style.animation = "none";
      void veil.offsetWidth; // flush — forces the animation restart
      veil.style.animation = ""; // restore the stylesheet's rv-splash
    };
    window.addEventListener("orientationchange", () => setTimeout(retriggerSplash, 60));
  }
}

// Kaetram hub: wire the pages to the scene (minimap source, debug toggle,
// player row → profile card) BEFORE any snapshot can arrive.
hud.attachHubPages(scene, (p) => showProfilePopup(p));

// ----- player profile popup (click another player in the world) -----

const profilePopup = document.getElementById("profile-popup")!;

/** Open the profile card for a clicked player (ekonia palette + layout). */
function showProfilePopup(p: {
  id: number; name: string; color?: string; mode: "chat" | "web";
  hp?: number; max_hp?: number; level?: number;
}): void {
  const nameEl = document.getElementById("pp-name")!;
  nameEl.textContent = p.name;
  // Name colored with the player's permanent role color (falls back white).
  nameEl.style.color = p.color || "#ffffff";
  (document.getElementById("pp-level") as HTMLElement).textContent =
    String(p.level ?? 1);
  (document.getElementById("pp-hp") as HTMLElement).textContent =
    `${p.hp ?? "?"}/${p.max_hp ?? "?"}`;
  (document.getElementById("pp-mode") as HTMLElement).textContent =
    p.mode === "web" ? "Web" : "Discord";
  (document.getElementById("pp-sub") as HTMLElement).textContent =
    `Cấp ${p.level ?? 1} · ${p.mode === "web" ? "Web" : "Discord"}`;
  // Avatar: chat players get their color circle letter; web players reuse
  // the shared base paperdoll portrait when it is registered.
  const img = document.getElementById("pp-avatar") as HTMLImageElement;
  if (scene.hasPaperdollTexture()) {
    img.src = scene.paperdollPortraitSrc();
    img.style.display = "";
  } else {
    // Inline SVG fallback: colored circle + first letter (no network).
    const letter = encodeURIComponent(p.name.charAt(0).toUpperCase() || "?");
    img.src =
      "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='38' height='38'%3E" +
      `%3Ccircle cx='19' cy='19' r='18' fill='${p.color || "#2f9e63"}'/%3E` +
      `%3Ctext x='19' y='25' text-anchor='middle' font-size='18' fill='%23fff'%3E${letter}%3C/text%3E%3C/svg%3E`;
    img.style.display = "";
  }
  profilePopup.classList.remove("hidden");
}

// Close paths: ✕ button (the card is the only interactive element —
// the rest of the screen stays in-game, no browser UI leaks through).
document.getElementById("pp-close")!.addEventListener("click", () =>
  profilePopup.classList.add("hidden"),
);
// "Nhắn tin" → prefill the chat input with a mention-ish prefix and focus it.
document.getElementById("pp-msg")!.addEventListener("click", () => {
  const name = (document.getElementById("pp-name") as HTMLElement).textContent ?? "";
  profilePopup.classList.add("hidden");
  const input = document.getElementById("chat-input") as HTMLInputElement;
  input.value = `@${name} `;
  input.focus();
});

// Scene -> popup wiring: click a remote body opens their card.
scene.onPlayerClick = (p) => showProfilePopup(p);
// every snapshot (and the welcome default below).
// ?fx=0 / ?fx=weather,daynight,cave gates (perf.ts): the PC-vs-mobile lag
// bisector — mobile smooth + PC lag fingered per-frame client render cost,
// so every optional overlay can be toggled off from the URL.
if (perf.weather) {
  weatherFx.mount(document.getElementById("game-root")!);
}
// Weather camera hook: rain/snow/CLOUD SHADOWS anchor to the MAP (world
// space) — they slide across the viewport as the player walks instead of
// being glued to the screen. Without this hook camScroll stays {0,0} and
// every blob sticks to the player's screen ("mây bám màn hình") — the block
// was deleted by accident in 8add5c7's main.ts cleanup; restored verbatim.
weatherFx.setCameraHook(() => {
  const cam = scene.cameras.main;
  return { x: cam.scrollX, y: cam.scrollY, zoom: cam.zoom };
});
// Day/night tint now renders INSIDE the Phaser canvas as GPU rectangles
// (daynight_phaser.ts, driven from WorldScene.update) — the standalone DOM
// canvas was the PC movement stutter: blending a full-window 2D canvas over
// the WebGL canvas every frame (idle screens skip recomposite, movement
// does not). No DOM element is mounted anymore.

const game = new Phaser.Game({
  type: Phaser.AUTO,
  // Keep the dGPU/iGPU choice at full-power: 3.90 auto-drops to the low
  // pipeline on weak adapters and Windows battery saver halves rAF on
  // integrated GPUs — the PC 30fps stutter (mobile was smooth).
  powerPreference: "high-performance",
  parent: "game-root",
  backgroundColor: "#20303c",
  // Pixel-art rendering: nearest-neighbour sampling + rounded pixels. The
  // camera zoom (2.0) times the doll's non-integer manifest scale (1.6043)
  // gave a 3.2x draw — WITH antialiasing that bilinear-blurs the player
  // while the zombie (1.5 * 2 = integer 3x) stayed crisp. pixelArt kills
  // the smoothing for every sprite and snaps texels to the pixel grid.
  pixelArt: true,
  scale: {
    mode: Phaser.Scale.RESIZE,
    width: window.innerWidth,
    height: window.innerHeight,
  },
  scene: [],
  audio: { noAudio: true },
});
game.scene.add("world", scene, true);

function applyTexture(name: string, b64: string): void {
  // Block faces register under "block-<id>" — the key game.ts looks up in
  // buildBlocks. Mob sheets register under "mob-<id>". Tilesets register
  // under the BARE filename — the scene's tileTextures map (and the map
  // bake) key by basename, so keeping the "tilesets/" lane prefix in the
  // texture key left the bake lookup always missing → black canvas.
  const bareTileset = name.startsWith("tilesets/")
    ? name.slice("tilesets/".length)
    : name;
  const key = name.startsWith("blocks/")
    ? `block-${name.slice("blocks/".length).replace(/\.png$/i, "")}`
    : name.startsWith("mobs/")
      ? `mob-${name.slice("mobs/".length).replace(/\.png$/i, "")}`
      : name.startsWith("node/")
        ? `node-${name.slice("node/".length).replace(/\.png$/i, "").replace(/\//g, "-")}`
        : bareTileset.replace(/\.png$/i, "");
  if (assetTextures.has(key) || !game.textures) return;
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: "image/png" });
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => {
    if (game.textures.exists(key)) game.textures.remove(key);
    if (name.startsWith("mobs/")) {
      // Mob sheets MUST register as a spritesheet: addImage + setCrop
      // keeps the render quad at the FULL sheet size (UV-only crop), so each
      // animation cell draws offset from the object origin and every frame
      // change visibly shifts the sprite. Spritesheet frames get their own
      // cut + origin — setFrame centres each cell exactly. Cell size is
      // PER KIND (Kaetram sprites.json): zombie/slime 32x32, skeleton 48x48,
      // spider 35x35, bat 32x48, rat 32x32 — a blanket 32x32 mis-cropped
      // every non-zombie mob.
      const mobId = name.slice("mobs/".length).replace(/\.png$/i, "");
      const MOB_CELLS: Record<string, [number, number]> = {
        zombie: [32, 32], slime: [32, 32], skeleton: [48, 48],
        spider: [35, 35], bat: [32, 48], rat: [32, 32],
        // Cave/forest packs — a missing entry silently fell back to 32x32
        // and mis-cropped goblin (26x26) / spectre (34x34) frames.
        skeleton2: [48, 48], spectre: [34, 34],
        goblin: [26, 26], hobgoblin: [32, 32],
        // Daytime wildlife (Minifolks pack, normalized to 32px cells by
        // scripts/pack_animal_sheets.py).
        bunny: [32, 32], deer: [32, 32], deer2: [32, 32], bird: [32, 32],
        boar: [32, 32], bear: [32, 32], fox: [32, 32], wolf: [32, 32],
      };
      const [fw, fh] = MOB_CELLS[mobId] ?? [32, 32];
      game.textures.addSpriteSheet(key, img, { frameWidth: fw, frameHeight: fh });
    } else {
      game.textures.addImage(key, img);
    }
    URL.revokeObjectURL(url);
    // Cache only on SUCCESS. Registering the key before the bytes decoded made
    // a single failed decode permanent: every later request short-circuited on
    // this set, no texture was ever created, and the map could not bake again
    // (the interior showed the previous map's ground).
    assetTextures.set(key, key);
    if (name.startsWith("blocks/")) {
      // Block face arrived: redraw the block layer with the real sprite.
      scene.onBlockTexture(name.slice("blocks/".length).replace(/\.png$/i, ""));
      onBlockingAssetDone(key);
      return;
    }
    if (name.startsWith("mobs/")) {
      // Mob sheet arrived: upgrade zombie placeholders to the sprite.
      scene.onMobTexture(name);
      return;
    }
    if (name.startsWith("players/")) {
      // Paperdoll sheet (base body / weapon) — byte-registered with the
      // frame grid in game.ts, not a plain image.
      scene.onPaperdollAsset(name, b64);
      return;
    }
    if (name.startsWith("icons/")) {
      // Kaetram hand-icon: register the plain image (icon-<id>) and flip
      // every emoji-glyph hand icon to the pixel art.
      const itemId = name.slice("icons/".length).replace(/\.png$/i, "");
      if (game.textures) {
        game.textures.addImage(`icon-${itemId}`, img);
        scene.onIconTexture(itemId);
      }
      return;
    }
    if (name.startsWith("node/")) {
      // Bundled node sprite (meteor-ore crater rock): register as      // node-meteor-ore and rebuild the resource layer so pending
      // pseudo-tile draws pick the texture up.
      scene.onNodeTexture(name.slice("node/".length).replace(/\.png$/i, ""));
      return;
    }
    // Tileset image arrived (blocking asset) — tick the loading overlay.
    // Strip the "tilesets/" lane prefix: the scene keys baked textures by
    // the bare filename (tileTextures map).
    const bareName = name.replace(/^tilesets\//, "");
    onBlockingAssetDone(bareName.replace(/\.png$/i, ""));
    // Tileset arrived: re-bake ONLY the map canvas (no world rebuild —
    // rebuilding duplicated players and reset the camera).
    scene.onTilesetLoaded(bareName);
  };
  img.onerror = () => {
    assetTextures.delete(key);
    try {
      URL.revokeObjectURL(url);
    } catch {
      /* already revoked */
    }
    console.warn("[asset] decode failed:", name);
  };
  img.src = url;
}

function applyInventory(inv: InventoryPayload, version?: number): void {
  hud.setInventory(inv, version);
  // Plan A instant hand: self tool icon follows the LOCAL hotbar at once
  // (no 20 Hz wait) — snapshot.held converges it with server truth after.
  scene.setSelfHeldFromHotbar(inv.hotbar ?? [], hud.currentSlot);
}

// Asset-load tracking for the loading overlay. Only BLOCKING assets count:
// the map tilesets + block faces (without them the world renders as grey
// rectangles / an empty canvas). Mobs + paperdoll sheets stream in later and
// never block the overlay. When every needed asset is ALREADY cached (map
// revisited in the same session) the count is 0 -> no overlay, no flash.
let loadSafetyTimer: number | null = null;
let mapLoadSafetyTimer: number | null = null;

function beginLoadTracking(frame: WelcomePayload): void {
  const mapKeys = frame.map.tilesets
    .filter((t) => t.image)
    .map((t) => t.image!.replace(/\.png$/i, ""));
  const blockKeys = [...new Set(frame.blocks.map(([, , bid]) => bid))]
    .map((bid) => `block-${bid}`);
  // Count only what is NOT already registered in the texture cache.
  const keys = [...mapKeys, ...blockKeys].filter(
    (k) => !assetTextures.has(k) && !(game.textures && game.textures.exists(k)),
  );
  hud.showLoading(keys.length);
  // TRAVEL VEIL: feed the REAL blocking-asset count — the iris/loading %
  // is a pure function of this (prefab-transition: progress = truth).
  travelVeil.noteLoadTotal(keys.length);
  if (keys.length === 0) return;
  // Safety net: a lost asset frame must never trap the player behind the
  // overlay — force-hide after 12 s no matter what.
  if (loadSafetyTimer !== null) window.clearTimeout(loadSafetyTimer);
  loadSafetyTimer = window.setTimeout(() => hud.hideLoading(), 12000);
}

function onBlockingAssetDone(key: string): void {
  if (!assetTextures.has(key)) return; // not a blocking asset we track
  hud.tickLoading();
  travelVeil.noteAssetDone(); // real % += 1/N (drives the iris + video)
}

// DEBUG HANDLE: expose the Phaser game for console probes (preview_evaluate).
(window as unknown as { game: Phaser.Game }).game = game;
// (window.net is attached right after `const net = new Net(...)` below.)

const net = new Net({
  onRtt: (rttMs) => {
    scene.setNetRtt(rttMs);
    hud.setPing(rttMs); // live ms readout (green/amber/red)
    hud.setHubSelf({ pingMs: rttMs }); // profile page mirror
  },
  // Feed the scene's replay buffer with every seq'd input the moment it is
  // sent — before any snapshot can ack it (ordering guarantee: flushInput
  // fires onSeqInput synchronously after the ws.send, and snapshots arrive
  // on this same thread, so the buffer can never miss an acked input).
  onSeqInput: (seq, dx, dy, running) => {
    scene.noteSeqInput(seq, dx, dy, running);
    scene.inputsSent++;
  },
  onWelcome: (frame) => {
    everWelcomed = true;
    // PORTAL SWITCH FEEDBACK: a welcome for a DIFFERENT map means the map
    // body is about to bake on the main thread (heavy even when all assets
    // are cached). Cover with the iris veil NOW so the frozen frames read
    // as a transition, not a hang; the first snapshot of the new map opens
    // the iris back up (travel_begin normally closed it earlier).
    const mapChanged = welcome !== null && frame.map.id !== welcome.map.id;
    if (mapChanged) {
      travelVeil.onMapSwitch(frame.map.name || frame.map.id);
      // Safety net: never trap the player behind the veil.
      if (mapLoadSafetyTimer !== null) window.clearTimeout(mapLoadSafetyTimer);
      mapLoadSafetyTimer = window.setTimeout(() => hud.hideMapLoading(), 10000);
    }
    welcome = frame;
    // In-game now: reveal the touch controls (hidden during gate/lobby).
    (window as unknown as { __setMobileControls?: (on: boolean) => void })
      .__setMobileControls?.(true);
    // Landscape: the one true mechanism (see LANDSCAPE final form above) —
    // fullscreen + OS orientation lock, retried here inside the user-
    // gesture chain where the browser allows it.
    void (window as unknown as { __lockLandscape?: () => Promise<void> })
      .__lockLandscape?.();
    scene.buildWorld(frame, (name) => net.fetchAsset(name));
    // Map-switch input hygiene (both halves): scene.buildWorld clears its
    // own prediction mirror on a different-map welcome; here we zero the
    // NET-layer pending vector + the mobile stick mirror, or the next 16 ms
    // commit timer re-sent the OLD walk direction to the NEW map (the
    // "vào hang còn lệch chút chút" residue).
    net.resetInput();
    mobileInputState.dx = 0;
    mobileInputState.dy = 0;
    mobileInputState.running = false;
    beginLoadTracking(frame);
    hud.hideGate();
    hud.hideLobby();
    hud.setInventory(frame.inventory);
    hud.setRecipes(frame.recipes);
    hud.setNearStation(!!frame.near_station);
    // Re-sync the server's held slot after (re)login — the session was
    // recreated server-side and defaults to slot 0. Self hand shows the
    // server echo at once (welcome.held), hotbar may still be resolving.
    scene.setSelfHeld(frame.held ?? null);
    net.selectSlot(hud.currentSlot);
    hud.setItemEmojis(frame.item_emojis ?? {});
    hud.setBars(frame.self.hp, frame.self.max_hp, frame.self.mana, frame.self.max_mana,
      (frame.self as { stamina?: number }).stamina ?? 1,
      (frame.self as { max_stamina?: number }).max_stamina ?? 0);
    hud.setPurse(frame.self.coins, frame.self.crystals ?? 0);
    // Status effects rail (server truth): [effect_id, secs_left, level].
    const se = (frame.self as { status_effects?: Array<[string, number, number?]> }).status_effects;
    hud.setStatusEffects(Array.isArray(se) ? se : []);
    hud.setClock(0);
    // Welcome carries no weather of its own — prime the overlay with the
    // map default; the first snapshot sets the real key ~50ms later.
    weatherFx.setWeather(null);
    if (perf.daynight) dayNightPhaser.setClock(12 * 3600); // prime: noon (no tint) until first snapshot
    hud.setWeather("sun_clouds");
    hud.chatLine(`Đã vào ${frame.map.name}. WASD để đi, E túi đồ, F tấn công.`);
  },
  onSnapshot: (frame) => {
    lastSnapshotAt = performance.now();
    // First snapshot of the NEW map after a portal switch: world rebuilt +
    // streaming — open the iris (real progress = 100%) + drop any sheet.
    if (mapLoadSafetyTimer !== null && welcome && frame.map_id === welcome.map.id) {
      travelVeil.noteReady();
      hud.hideMapLoading();
      window.clearTimeout(mapLoadSafetyTimer);
      mapLoadSafetyTimer = null;
    }
    // Incoming-damage hitsplats: float the number over the VICTIM (dedupe
    // by unix timestamp — the feed window overlaps across snapshots).
    for (const [ts, uid, dmg] of frame.damage_feed ?? []) {
      const key = `${ts}:${uid}:${dmg}`;
      if (!seenDamageKeys.has(key)) {
        seenDamageKeys.add(key);
        scene.spawnSplatOnPlayer(uid, dmg);
      }
    }
    if (seenDamageKeys.size > 200) {
      // Trim: keep only recent keys (feed is 2s; a fixed cap suffices).
      const keep = [...seenDamageKeys].slice(-100);
      seenDamageKeys.clear();
      keep.forEach((k) => seenDamageKeys.add(k));
    }
    scene.applySnapshot(frame);
    // Meteor shower events (night bigmap): the FX lane syncs its warning
    // rings / falls / impacts with the server timeline from these rows.
    meteorFx.attach(scene as unknown as Phaser.Scene);
    meteorFx.sync(frame.meteors);
    hud.setClock(frame.clock);
    hud.setWeather(frame.weather);
    // Admin /clouds N: >0 = force the cloud-shadow overlay on top of any
    // weather (test hook); 0/undefined = weather-driven only.
    const cloudsOverride = (frame as { clouds_override?: number }).clouds_override ?? 0;
    const weatherKey = cloudsOverride > 0 ? "cloud_shadow" : frame.weather;
    weatherFx.setWeather(weatherKey, cloudsOverride);
    if (perf.daynight) dayNightPhaser.setClock(frame.clock);
    hud.setBars(frame.self.hp, frame.self.max_hp, frame.self.mana, frame.self.max_mana,
      (frame.self as { stamina?: number }).stamina ?? 1,
      (frame.self as { max_stamina?: number }).max_stamina ?? 0);
    hud.setPurse(frame.self.coins, frame.self.crystals ?? 0);
    // Status effects rail (server truth): [effect_id, secs_left, level].
    const se = (frame.self as { status_effects?: Array<[string, number, number?]> }).status_effects;
    hud.setStatusEffects(Array.isArray(se) ? se : []);
    // Hub pages: live bars + player list (20 Hz mirror, re-render only the
    // currently-open page).
    hud.setHubSelf({
      hp: frame.self.hp, maxHp: frame.self.max_hp,
      mana: frame.self.mana, maxMana: frame.self.max_mana,
      coins: frame.self.coins, crystals: frame.self.crystals ?? 0,
    });
    hud.setHubPlayers(frame.players);
    // near_station lives INSIDE self on snapshots (top-level only on
    // welcome) — reading the wrong spot meant the 20 Hz station state was
    // permanently false: no grid upgrade, no auto-close on range exit.
    hud.setNearStation(
      !!(frame.self as { near_station?: boolean }).near_station,
    );
    // Death veil: server ignores our inputs while dead; the scene freezes
    // prediction and this overlay explains why (5s respawn). The iris veil
    // shares the moment (close on death, open on respawn).
    hud.setDead(!!frame.self.dead, frame.self.respawn_s ?? 0);
    travelVeil.setDead(!!frame.self.dead, frame.self.respawn_s ?? 0);
    if (frame.self.dead) input.clearKeys();
    // Bag rides along only when it changed (inv_version ack) — otherwise
    // this is a no-op and the grid never re-renders mid-drag.
    if (frame.inventory) applyInventory(frame.inventory, frame.inv_version);
    // Craft-panel parked RESULT rides along with the bag (server truth);
    // the material grid is a local buffer and is never echoed here.
    // Also handles CLEARED results (craft_result === null) — collecting
    // must empty the slot visually right away.
    if (frame.craft_result !== undefined) {
      hud.setCraftResult(frame.craft_result ?? null);
    }
  },
  onScenarioList: (items) => {
    hud.showScenarioList(items, (channelId) => {
      pendingRejoinChannel = null;
      const token = localStorage.getItem("web_token") ?? "";
      localStorage.setItem("last_channel", String(channelId));
      net.joinScenario(channelId, token);
    });
    // AUTO-JOIN the main server ("Demo by conist") for anyone not already
    // bound to a world: first visit, and any session whose last_channel no
    // longer exists in the list. Applies to BOTH pc and mobile — they share
    // this exact code path (the lobby is one component).
    const haveSaved = localStorage.getItem("last_channel");
    const savedExists = haveSaved && items.some(
      (i) => String(i.channel_id) === haveSaved,
    );
    // PREVIEW MODE never auto-joins the demo server — the preview stack
    // already put us in the solo bigmap runtime.
    if (previewMode) return;
    if (!savedExists) {
      const demo = items.find((i) => i.map_name === "Demo by conist")
        ?? (items.length === 1 ? items[0] : undefined);
      if (demo) {
        pendingRejoinChannel = null;
        const token = localStorage.getItem("web_token") ?? "";
        localStorage.setItem("last_channel", String(demo.channel_id));
        hud.setLobbyStatus(null);
        net.joinScenario(demo.channel_id, token);
        return;
      }
    }
  },
  onInventory: applyInventory,
  onCraftState: (_matGrid, result) => {
    // Only the parked RESULT is server truth now; the material grid is a
    // local buffer (ignore server echoes of it entirely).
    hud.setCraftResult(result);
  },
  onCraftResult: (ok, reason, itemId, qty) => {
    hud.craftResult(ok, reason, itemId, qty);
    // NOTE: the parked result is SERVER truth — it arrives via the
    // inventory_delta's craft_result fragment (setCraftResult). Never clear
    // it here: the output must STAY in the result slot until collected.
  },
  onPush: (message, _kind) => {
    // NOTE: the danger banner (kind === "danger") is DISABLED per user
    // request — pushes always show as the plain toast. Re-enable by
    // routing to hud.dangerAlert when the user picks a final style.
    hud.toast(message);
    previewPanel.feed(message);
  },
  onChat: (_uid, name, color, text) => {
    hud.chatPlayerLine(name, color, text);
  },
  onRemoteSwing: (uid, tx, ty) => {
    // Exact player id — play their arc wherever they stand.
    if (tx === null || ty === null) {
      scene.swingRemoteHand(uid);
    } else {
      scene.swingRemoteHandAt(uid, tx, ty);
    }
  },

  onError: (code, message) => {
    // SESSION DESYNC RECOVERY: the client thinks it is joined but the
    // server disagrees (bot restarted, registry dropped, relay re-hub).
    // Symptom was "everything says Quá xa until F5". Soft path first:
    // force-reconnect + re-join the last channel with the same token (the
    // server orphans tokens for a grace window). Only if the token is truly
    // gone (bad_token) do we fall back to the login panel.
    if (code === "not_joined" && net.isJoined) {
      const lastMap = localStorage.getItem("last_channel");
      if (lastMap) {
        hud.toast("⟳ Đồng bộ lại phiên với server…");
        net.forceReconnect(); // reconnect() replays lastChannel's join
        return;
      }
    }
    const stale =
      code === "bad_token" ||
      (code === "not_joined" && !net.isJoined);
    if (stale) {
      localStorage.removeItem("web_token");
      // Quick login is disabled: a dead session always lands back on the
      // login panel — no silent guest recovery anymore.
      hud.showGate("Phiên cũ đã hết — đăng nhập lại:");
      hud.setLoginButton(true);
      hud.setQuickButton(true, "⚡ Vào nhanh (tắt)", false);
      return;
    }
    if (code === "scenario_missing_or_full") {
      hud.showGate("Map đầy hoặc không tồn tại.");
    }
    // Inventory/craft housekeeping errors are NEVER user-facing: a stale
    // reorder/split frame just means the server state moved on — the next
    // inventory delta repaints the truth. Toasting them was the "bad other"
    // spam during fast drags.
    if (code === "bad_order" || code === "bad_split" || code === "bad_slot") {
      return;
    }
    // Purse withdraw refused (empty counter or full bag): silent — the
    // ghost already sprang back; a toast per drag-out would be noisy.
    if (code === "purse_empty" || code === "bad_op") {
      // A refused purse op must UNDO its local optimistic preview — a
      // failed withdraw that stays on screen is the ghost coin (a stack
      // the server never owned; throwing it loses the item forever).
      if (code === "purse_empty") hud.revertPurseRefusal("coin");
      if (code === "purse_empty") hud.revertPurseRefusal("crystal");return;
    }
    // EAT refusals: the server has a real reason — show it (the eat
    // right-click used to fail silently, looking like a dead button).
    if (code.startsWith("eat_") && message) {
      hud.toast(message);
      return;
    }
    hud.toast(`Lỗi: ${code}`);
  },
  onAssetData: (name, b64) => {
    if (b64) applyTexture(name, b64);
  },
  onLoginOk: (token, displayName, avatarUrl) => {
    hud.setLoginButton(true, `Tiếp: ${displayName}`);
    // Persist the profile for the lobby chip across reloads.
    localStorage.setItem("web_name", displayName);
    if (avatarUrl) localStorage.setItem("web_avatar", avatarUrl);
    else localStorage.removeItem("web_avatar");
    // Silent reconnect recovery: a stale-token error queued the last map —
    // join it immediately on this fresh session (guest OR Discord).
    if (pendingRejoinChannel) {
      const ch = pendingRejoinChannel;
      pendingRejoinChannel = null;
      hud.showGate("Đang vào lại map…");
      net.joinScenario(ch, token);
      return;
    }
    // Everyone lands on the LOBBY (main menu) — guests included. Quick-login
    // is quick because it skips the OAuth dance, not because it skips the menu.
    hud.setLobbyProfile(
      displayName,
      token.startsWith("guest:") ? "Khách (thử nghiệm)" : "Tài khoản Discord",
      avatarUrl,
    );
    hud.showLobby(true);
    void token;
  },
  onLoginFail: (error) => {
    // A failed RESUME just means the token died (revoked by a newer login,
    // server wipe): clean it and show the fresh login panel. A failed
    // explicit login also lands here — same panel.
    localStorage.removeItem("web_token");
    hud.setLoginButton(true, "🔑 Đăng nhập Discord");
    hud.setQuickButton(true, "⚡ Vào nhanh (tắt)", false);
    hud.showGate(`Đăng nhập thất bại: ${error}`);
  },
  onHeld: (_slot, itemId) => {
    // Hotbar switches are SILENT on purpose: changing hands must not spam
    // chat with "Cầm: … / Tay không …" (bug report 11/09). The hand itself
    // is the feedback: server echo converges the self tool icon here.
    scene.setSelfHeld(itemId ?? null);
  },
  onTravelBegin: (mapName, kind) => {
    // PRE-TRAVEL SIGNAL: close the iris BEFORE the server handoff so the
    // slow welcome/bake lands behind a black screen ("chống dịch chuyển
    // chậm"). kind "death" reserved for future server-driven cases.
    void kind;
    travelVeil.beginTravel(mapName || "khu vực mới");
  },
  onActionResult: (frame) => {
    if (frame.needed != null) scene.noteChopNeeded(frame.tx, frame.ty, frame.needed);
    // place/break verdict: confirm or revert the optimistic collision tile
    // EXACTLY (tx/ty = the tile the server acted on, possibly clamped). This
    // closes the "đặt rồi xóa rồi chạy xuyên" gap in one RTT — a rejected
    // break restores the block immediately instead of leaving a walkable
    // phantom until some unrelated snapshot change.
    if (frame.name === "place" || frame.name === "break") {
      scene.reconcileBlockAction(frame.name, frame.ok, frame.tx, frame.ty);
      // Progressive crack: the echo carries (damage, needed) = the SERVER's
      // authoritative hit progress. A break (block gone) clears the overlay.
      // NOTE: nothing client-optimistic here anymore — pre-painting a fake
      // crack before the echo raced the real server sample and made the
      // damage appear to jump backwards ("crack tua ngược" bug).
      if (frame.name === "break" && !frame.ok) {
        scene.setBlockCrack(frame.tx, frame.ty, frame.damage ?? 0, frame.needed ?? 0);
      }
    }
    // Zombie kill echo (shared pack with Discord): death anim + loot pop.
    if (frame.kind === "zombie") {
      scene.noteZombieKill(frame.target_id, frame.target_defeated);
      if (frame.ok) {
        // Kaetram hitsplat parity: the damage number floats over the target
        // (red / gold crit / MISS) — the server resolved the roll already.
        scene.spawnSplat(frame.tx, frame.ty, frame.damage ?? 0, !!frame.critical, !!frame.missed);
        if (frame.drops.length > 0) {
          const loot = frame.drops.map(([id, qty]) => `${id}×${qty}`).join(", ");
          hud.chatLine(`Hạ zombie! Nhặt: ${loot}`);
        }
      } else if (frame.missed) {
        // Whiff on a live target still shows MISS over the zombie tile.
        scene.spawnSplat(frame.tx, frame.ty, 0, false, true);
      }
      if (frame.ok) return;
    }
    if (frame.ok) {
      if (frame.name === "chop" && frame.drops.length > 0) {
        const loot = frame.drops.map(([id, qty]) => `${id}×${qty}`).join(", ");
        hud.chatLine(`Đã hạ! Nhặt: ${loot}`);
      }
      return;
    }
    const REASONS: Record<string, string> = {
      // no_node / no_block: intentionally SILENT — punching air / empty tile
      // must not show any message (bug report 11/09).
      // regrowing: intentionally silent — the player must NOT think of the
      // spot as a pot that regrows; the tree just quietly comes back later
      // (nothing visible, nothing blocking in the meantime).
      too_hard: "Quá cứng — cần cúp từ tầng dirt trở lên.",
      not_placeable: "Khối này không thể đặt.",
      blocked_tile: "Không thể đặt ở ô đó.",
      tile_occupied: "Có người đứng ở ô đó.",
      no_material: "Không có nguyên liệu trong túi.",
      own_tile: "Không thể đặt lên chỗ mình đứng.",
      out_of_range: "Quá xa.",
      already_block: "Ô đó đã có khối.",
    };
    const msg = REASONS[frame.reason];
    if (msg) hud.toast(msg);
  },
  onConnectionChange: (connected) => {
    if (!connected) {
      hud.showGate("Mất kết nối — thử lại…");
      // Back at the gate: the touch layer must yield to it again.
      (window as unknown as { __setMobileControls?: (on: boolean) => void })
        .__setMobileControls?.(false);
      // PREVIEW AUTO-RECOVERY: the reconnect backoff gives up at attempt 4
      // (~15s), which strands the preview gate forever when the browser
      // dropped the WS while the tab was hidden. A single clean reload
      // restarts the whole boot (guest join + previewMap) — safe because
      // the preview stack rebuilds the runtime from scratch anyway.
      if (previewMode && !sessionStorage.getItem("preview_recovered")) {
        sessionStorage.setItem("preview_recovered", "1");
        window.setTimeout(() => location.reload(), 1500);
      }
    } else {
      sessionStorage.removeItem("preview_recovered");
    }
  },
});

// Idle heartbeat: the idle input timer keeps reporting the CURRENT predicted
// position (see Net.flushInput) so the server body can never drift from what
// the player sees — the "đứng yên mà hitbox ở chỗ khác" fix.
net.idlePosHook = () => scene.getSelfPos();

// DEBUG HANDLE: window.net for console probes (preview_evaluate).
(window as unknown as { net: Net }).net = net;
// DEBUG HANDLE: window.gameScene — preview harness probes (meteor ore,
// resource layer) without a module export.
(window as unknown as { gameScene: WorldScene }).gameScene = scene;

// F3 collision debug (lobby checkbox or F3 key): paint collision tiles red.
window.addEventListener("toggle-collision", (e) => {
  scene.setCollisionDebug(Boolean((e as CustomEvent).detail));
});
window.addEventListener("keydown", (e) => {
  if (e.key === "F3") {
    e.preventDefault();
    const cb = document.getElementById("show-collision") as HTMLInputElement | null;
    if (cb) {
      cb.checked = !cb.checked;
      scene.setCollisionDebug(cb.checked);
    } else {
      scene.setCollisionDebug(!scene.getCollisionDebug());
    }
  }
});

// ---- DESYNC DEBUG (F3 toggle) -------------------------------------------
// On-screen panel (10 Hz): predicted vs server position, divergence, ack seq,
// pending inputs, snapshot rate. PLUS a console tracer (in game.ts) that fires
// only on divergence > 1.5 tiles. Purpose: catch "hitbox bên kia" red-handed —
// read pred vs srv the moment the user feels the mismatch.
{
  const dbg = document.createElement("div");
  dbg.id = "desync-debug";
  dbg.style.cssText = [
    "position:fixed", "top:8px", "left:8px", "z-index:50",
    "background:rgba(0,0,0,0.75)", "color:#7fff9f", "padding:6px 9px",
    "font:12px/1.5 monospace", "white-space:pre", "border-radius:6px",
    "pointer-events:none", "display:none",
  ].join(";");
  document.body.appendChild(dbg);
  window.addEventListener("keydown", (e) => {
    if (e.code === "F3") {
      e.preventDefault();
      scene.debugEnabled = !scene.debugEnabled;
      dbg.style.display = scene.debugEnabled ? "block" : "none";
    }
  });
  window.setInterval(() => {
    if (scene.debugEnabled) dbg.textContent = scene.getDebugInfo();
  }, 100);
}

// Consumable ids the right-click EAT applies to (server re-validates type
// + stock; this set only routes the click so tools still place nothing).
const EDIBLE_IDS = new Set([
  "apple", "cooked_meat", "raw_meat", "rotten_flesh", "potion_hp", "potion_mp",
  "banana", "orange", "watermelon", "blueberry", "bread", "cheese", "carrot",
]);

// Drag item outside panel + left click = toss it into the world.
// Purse drag-out: pull exactly ONE coin/crystal from the counter into the
// bag (server op purse_withdraw — one unit per drag). Dropping onto a bag
// slot deposits into that exact slot.
hud.onPurseWithdraw = (itemId, slot) => {
  net.inventoryOp("purse_withdraw",
    slot === undefined ? { item_id: itemId } : { item_id: itemId, slot });
};

// Purse deposit: dragging a currency stack into the bag grid banks it —
// sent as a reorder (the server's reorder path converts currency to the
// purse counters server-side).
hud.onPurseDeposit = (itemId, qty, fromIndex) => {
  net.inventoryOp("purse_deposit",
    { item_id: itemId, qty, slot: fromIndex });
};

hud.onThrow = (itemId, qty) => {
  // The stack visibly flies where the player faces (server spawns the drop
  // with a directional launch; the scene's 8-way selfDir is the label).
  net.inventoryOp("throw", { item_id: itemId, qty, direction: scene.getSelfDir() });
  hud.toast(`Đã vứt ${itemId}×${qty}`);
};

hud.setHooks(
  (itemId) => net.inventoryOp("use", { item_id: itemId }),
  (text) => {
    if (text.startsWith("/")) {
      net.chatCommand(text);
    } else {
      // Plain chat: the server broadcasts "chat" back to EVERYONE including
      // self — no local echo here (it used to double the line).
      net.chatCommand(text);
    }
  },
  (recipeId) => net.craftOp(recipeId),
);

// Craft hooks (LOCAL-grid model): the material grid is a pure client
// buffer; CREATE sends the exact multiset once (server validates + consumes
// from the real bag). Split = slot-based op. Bag reorder is debounced
// client-side (ui.ts) so fast drags never spam the network.
hud.setCraftHooks(
  (inputs, layout) => net.craftFromGrid(inputs, layout),
  (_grid) => { /* no-op: the grid is local now */ },
  (slot) => net.inventoryOp("split", { slot }),
);
hud.setBagSync(
  (itemId, slot) => net.inventoryOp("move_to", { item_id: itemId, slot }),
  (_inv) => { /* client buffer already updated; server delta repaints */ },
  (order) => net.inventoryOp("reorder", { order }),
);
// Result slot click: collect the crafted output into the bag (server op).
hud.onCollectResult((slot) => net.craftCollect(slot));

// Slot selection: numbers 1-8, mouse wheel, or click — changes the held
// tool only. Silent on purpose: no chat spam. The self hand updates
// INSTANTLY from the local hotbar; the server echo/snapshot converge it.
hud.onSlotSelect((slot) => {
  net.selectSlot(slot);
  scene.setSelfHeldFromHotbar(hud.inventoryHotbar, slot);
});

const input = new KeyboardInput({
  onVector: (dx, dy, running) => {
    // MULTI-TOUCH GUARD: while the mobile stick holds the pointer, the
    // keyboard vector is suppressed — otherwise a WASD key held before (or
    // during) a stick drag re-asserts its vector over the stick's through
    // the 20 Hz flush, and the player runs BOTH directions interleaved (a
    // classic "lia cam đi tùm lum / giật giật" source on phones with
    // phantom key states).
    if (mobileStickActive) return;
    // Zero-lag: prediction runs every frame locally; the network copy is
    // just the authoritative echo (20 Hz throttle in Net).
    scene.setLocalInput(dx, dy, running);
    // Client-authoritative movement: report the PREDICTED position with the
    // input — the server pulls its body to where the player actually is
    // (speed-capped, collision-checked) instead of integrating time itself.
    // This removes the whole class of server-side integration desync.
    const sp = scene.getSelfPos();
    net.setInput(dx, dy, running, sp);
  },
  onAttack: () => {
    // Cheap melee: attack IN PLACE + swing the hand at once (the swing is
    // client-optimistic; a landed server hit re-triggers it via the echo).
    // Kaetram parity: atk anim plays exactly ONCE per click, ~450ms.
    net.action("attack");
    scene.combatSwing();
  },
  onToggleInventory: () => hud.toggleInventory(),
  // E near a station: open the craft panel (bubble punch effect plays in
  // the scene). Returns true when handled; false falls back to inventory.
  onStationKey: () => {
    // NPC FIRST: standing next to an NPC (chợ đen, bảng thông báo, cửa…)
    // E chats with it instead of opening the inventory/craft panel.
    if (scene.nearNpc()) {
      scene.requestNpcDialogue();
      return true;
    }
    if (!scene.nearStation()) return false;
    scene.stationInteract();
    return true;
  },
  onSlot: (index) => hud.selectSlot(index),
  onThrowHeld: () => {
    // Q: toss the full active-slot stack (the server removes + spawns the
    // drop with NO_COLLECT window so it isn't instantly re-magnetized).
    hud.throwHeldStack();
  },
  onChatFocus: () => document.activeElement === document.getElementById("chat-input"),
  onCanvasAction: (kind, sx, sy) => {
    // Resolve the tile from the CLICK's own coordinates — always the cell
    // under the cursor at this exact instant, never a cached value.
    const tile = scene.screenToTile(sx, sy);
    if (kind === "primary") {
      if (!tile) {
        net.action("chop");
        return;
      }
      // LEFT click is ALWAYS a break/chop — even on a station. The left
      // click must never be stolen (breaking the table would be impossible).
      // Clamp to the tile the server will ACTUALLY act on (mirrors its own
      // clamp). The break-vs-chop decision AND the optimistic collision must
      // live on that tile — targeting from a stale snapshot used to break
      // the wrong tile (server clamped elsewhere) leaving a walkable phantom
      // where the real block still stood: "đặt rồi xóa rồi chạy xuyên".
      const target = scene.clampClickTile(tile);
      if (!target) {
        // Genuinely out of range: send the raw click — the server answers
        // out_of_range honestly ("Quá xa."), no optimistic state involved.
        net.actionAt("chop", tile.x, tile.y);
        scene.swingSelfHand();
        return;
      }
      // COMBAT FIRST: a zombie near the click is an ATTACK, never a chop —
      // left click on a mob must damage it (melee resolves in a radius
      // server-side; tile targeting only picks the swing direction).
      if (scene.zombieNear(target)) {
        net.action("attack");
        scene.swingSelfHand();
        return;
      }
      // Contextual: placed block under cursor -> break; else chop. The hand
      // swings AT ONCE (client-optimistic); the server echo (res_progress
      // grows) re-swings it on each LANDED hit for the multi-hit rhythm.
      const breaking = scene.isBlockAt(target.x, target.y);
      net.actionAt(breaking ? "break" : "chop", target.x, target.y);
      scene.swingSelfHand();
      // NO optimistic crack here: the block stays fully solid and pristine
      // until the server echo reports the real damage (the client used to
      // pre-paint a light crack that clashed with the echo and made the
      // damage look like it rewound on rapid clicks).
      return;
    }
    // Secondary (right-click): FIRST a station interact — right-clicking a
    // crafting table in range opens/toggles the craft panel (left-click
    // stays free for breaking the table). Then: place the block in hand.
    if (tile && scene.hoveringStation()) {
      scene.stationInteract();
      return;
    }
    const placeable = new Set((welcome?.blocks_catalog ?? []).map((b) => b.id));
    const held = hud.heldItem;
    if (!tile || !held) return;
    // CONSUMABLE in hand + right-click = EAT it (the chew + heal flow).
    if (EDIBLE_IDS.has(held)) {
      net.inventoryOp("use", { item_id: held });
      return;
    }
    if (!placeable.has(held)) return;
    const target = scene.clampClickTile(tile);
    if (!target) {
      // Too far: still send so the server answers "Quá xa." honestly — but
      // NO optimistic state (the block will not appear).
      net.placeAt(tile.x, tile.y, held);
      return;
    }
    net.placeAt(target.x, target.y, held);
    // Same hand-swing feedback as breaking a block — placing is an arm
    // motion too (the doll plays its one-shot atk rows).
    scene.combatSwing();
    // Optimistic local collision: the block is solid IMMEDIATELY so a fast
    // run cannot pass through a block we just placed before the snapshot
    // arrives (the echo/snapshot reconciles if the server rejected it).
    scene.optimisticPlace(target.x, target.y);
  },
  onCanvasHover: (sx, sy) => {
    // Store the raw cursor position; the scene re-derives the tile every
    // frame (camera moves under a still cursor — cached tiles go stale).
    scene.setMouseTile(sx < 0 ? null : { x: sx, y: sy });
    // Cursor swap: over a player -> pointer (profile-clickable), else default.
    const canvas = game.canvas;
    if (canvas) {
      canvas.style.cursor =
        sx >= 0 && scene.pointerOverRemotePlayerAt(sx, sy) ? "pointer" : "";
    }
  },
});

// ---- MOBILE TOUCH CONTROLS (phones/tablets; hidden on desktop via CSS) ----
// Roblox-style dynamic joystick: feeds the SAME pipeline as WASD (vector
// goes through the onVector hook, so prediction/seq'd inputs/server path are
// identical). Tap = left click (chop/break/attack), long-press = right click
// (place/eat/station), pinch = zoom-IN only (mobile_controls.ts).
// MULTI-TOUCH: the stick owns its pointer — tap/hold with a second finger
// acts on the world WHILE moving. Keyboard movement is frozen while the
// stick is held (no phantom vector mixing).
let mobileStickActive = false;
const mobileInputState = { dx: 0, dy: 0, running: false };
// The stick has no key auto-repeat: while the vector is non-zero this 60 Hz
// timer re-commits setInput (Net still throttles the wire to ~20 Hz) so the
// server's activity liveness never starves mid-drag — a single onMove call
// used to be the only commit, and a slow network tick could end the drag
// early ("chạy được một nhịp rồi đứng").
let mobileCommitTimer: number | null = null;
const stopMobileCommit = (): void => {
  if (mobileCommitTimer !== null) {
    window.clearInterval(mobileCommitTimer);
    mobileCommitTimer = null;
  }
};
const mobile = new MobileControls({
  onMove: (dx, dy, running) => {
    // Analog vector (magnitude 0..1, screen-space) — identical contract to
    // the keyboard's normalized onVector. Reuse the SAME body as the
    // KeyboardInput onVector hook by routing through setLocalInput + setInput.
    mobileStickActive = dx !== 0 || dy !== 0;
    mobileInputState.dx = dx;
    mobileInputState.dy = dy;
    mobileInputState.running = running;
    scene.setLocalInput(dx, dy, running);
    const sp = scene.getSelfPos();
    net.setInput(dx, dy, running, sp);
    if (mobileStickActive && mobileCommitTimer === null) {
      mobileCommitTimer = window.setInterval(() => {
        const s2 = scene.getSelfPos();
        net.setInput(mobileInputState.dx, mobileInputState.dy, mobileInputState.running, s2);
      }, 16);
    } else if (!mobileStickActive) {
      stopMobileCommit();
    }
  },
  onAttack: () => {
    net.action("attack");
    scene.combatSwing();
  },
  onToggleInventory: () => hud.toggleInventory(),
  onTapWorld: (sx, sy) => {
    // Same pipeline as a desktop primary click (onCanvasAction "primary").
    // MOBILE AIM — STICKY TARGET (no flicker): the old two-tap gate armed
    // a square then REQUIRED a second tap; rapid tap-to-chop sessions
    // re-armed/flashed the box every tap ("nháy nháy khựng khựng"). Now:
    // the FIRST tap locks a sticky target tile (green box, steady); every
    // later tap on a tile ALREADY locked (or clamped onto it) acts AT ONCE
    // — chop/break/attack repeat as fast as you can tap. Tap a different
    // tile = re-lock (one quiet transition, no blink). Tap empty ground
    // with nothing targetable = clears the lock. The box never toggles
    // per action.
    const tapped = scene.screenToTile(sx, sy);
    const target = tapped ? scene.clampClickTile(tapped) ?? tapped : null;
    const existing = scene.peekAimTarget();
    const sameLock = existing && target &&
      target.x === existing.x && target.y === existing.y;
    if (!target) { scene.clearAimTarget(); return; }
    if (!sameLock) scene.lockAimTarget(target);
    // Act immediately either way — rapid taps on a locked tile chop at
    // full speed; a fresh lock also acts at once (tap-to-hit = instant).
    if (scene.zombieNear(target)) {
      net.action("attack");
      scene.swingSelfHand();
      return;
    }
    const breaking = scene.isBlockAt(target.x, target.y);
    net.actionAt(breaking ? "break" : "chop", target.x, target.y);
    scene.swingSelfHand();
  },
  onLongPressWorld: (sx, sy) => {
    // Same pipeline as a desktop secondary click (place / eat / station).
    // MOBILE AIM: a long press ALWAYS acts at the held finger's tile (no
    // confirmation gate — placing is already a deliberate hold) and clears
    // any armed target so the stale green square never lingers. The tile is
    // passed INTO hoveringStation: touch never moves the desktop cursor, so
    // the parameterless read was always null/stale there.
    scene.consumeAimTarget();
    const tile = scene.screenToTile(sx, sy);
    if (tile && scene.hoveringStation(tile)) {
      scene.stationInteract();
      return;
    }
    const placeable = new Set((welcome?.blocks_catalog ?? []).map((b) => b.id));
    const held = hud.heldItem;
    if (!tile || !held) return;
    if (EDIBLE_IDS.has(held)) {
      net.inventoryOp("use", { item_id: held });
      return;
    }
    if (!placeable.has(held)) return;
    const target = scene.clampClickTile(tile);
    if (!target) {
      net.placeAt(tile.x, tile.y, held);
      return;
    }
    net.placeAt(target.x, target.y, held);
    scene.combatSwing();
    scene.optimisticPlace(target.x, target.y);
  },
  onZoomPinch: (_factor) => { /* zoom removed — camera stays at authored 2.0 */ },
});
mobile.mount();

// HIDE the atk/inv buttons while an inventory/craft window is open (user:
// "khi đang craft thì ẩn 2 cái button inv và atk đi để đỡ vướng") — the
// buttons overlap the right side of the side-by-side inv+craft row.
// ClassObserver on #inv-panel: .hidden off → buttons hide, on → back.
if (MOBILE_UI) {
  const invPanelEl = document.getElementById("inv-panel");
  const mcActions = document.getElementById("mc-actions");
  if (invPanelEl && mcActions) {
    const syncMcHidden = (): void => {
      mcActions.classList.toggle("mc-hidden", !invPanelEl.classList.contains("hidden"));
    };
    new MutationObserver(syncMcHidden).observe(invPanelEl, { attributeFilter: ["class"] });
    syncMcHidden();
  }
}

// ---- MOBILE CHAT CHIP + TRAY OUTSIDE-TAP (mobile-ui mode only) ----
// Chat moves to the TOP-left on phones (CSS above) as a collapsed chip:
// tap = expand log + input; a tap OUTSIDE the chat frame collapses it.
// The unread badge counts lines that arrived while collapsed (cleared on
// open) — the player never silently misses chat on a phone.
// MOBILE_UI (not matchMedia): the desktop ?mobile=1 / Shift+F9 preview
// gets the exact same chat chip as a real phone (parity rule).

/** The target of the most recent pointerdown (set by onRealOutsideTap;
 *  callbacks read it to exclude gear/strip hits). */
let lastOutsideTarget: Element | null = null;
/** REAL outside-tap detector: fires `cb` when a pointerdown OUTSIDE `el`
 *  completes as a TAP (finger moved ≤12px, released within 500ms). A drag
 *  that started elsewhere — swiping an item across the screen, a world
 *  drag — must NOT close panels ("đang làm cái này mà dính ra ngoài màn
 *  hình là nhảy tùm lum"): the old immediate-pointerdown close fired on
 *  every touch anywhere, so gestures slammed panels shut mid-use. */
function onRealOutsideTap(el: HTMLElement, cb: () => void): void {
  const start = new Map<number, { x: number; y: number; t: number }>();
  window.addEventListener("pointerdown", (e) => {
    lastOutsideTarget = e.target as Element | null;
    if (el.contains(e.target as Node)) return;
    start.set(e.pointerId, { x: e.clientX, y: e.clientY, t: performance.now() });
  });
  window.addEventListener("pointerup", (e) => {
    const s = start.get(e.pointerId);
    start.delete(e.pointerId);
    if (!s) return;
    if (performance.now() - s.t > 500) return;
    if (Math.hypot(e.clientX - s.x, e.clientY - s.y) > 12) return;
    cb();
  });
  window.addEventListener("pointercancel", (e) => start.delete(e.pointerId));
}

if (MOBILE_UI) {
  const chat = document.getElementById("hud-chat")!;
  const log = document.getElementById("chat-log")!;
  const toggle = document.createElement("div");
  toggle.id = "chat-toggle";
  toggle.setAttribute("role", "button");
  toggle.innerHTML = `<span>💬 Chat</span><span class="ct-badge" id="chat-badge"></span>`;
  chat.prepend(toggle);
  toggle.style.touchAction = "none"; // chip is a drag handle + tap toggle
  const badge = toggle.querySelector(".ct-badge")!;
  let unread = 0;
  let chatOpen = false;
  const renderBadge = (): void => {
    badge.textContent = String(Math.min(unread, 99));
    badge.classList.toggle("unseen", unread > 0 && !chatOpen);
  };
  const setChatOpen = (open: boolean): void => {
    chatOpen = open;
    chat.classList.toggle("chat-open", open);
    if (open) {
      unread = 0;
      log.scrollTop = log.scrollHeight;
    }
    renderBadge();
  };
  // ---- POPUP DRAG + PER-USER POSITION MEMORY (user spec: the chat box is
  // a popup — drag it ANYWHERE on screen and each user's placement is
  // remembered across sessions: "nhớ player để đâu thì mấy phiên sau sẽ y
  // vậy"). The key is the logged-in identity: a hash of web_token when
  // signed in, else a stable random uid (guests keep their identity in
  // localStorage). ----
  const chatUid = (() => {
    const tok = localStorage.getItem("web_token") ?? "";
    if (tok) {
      let h = 0;
      for (let i = 0; i < tok.length; i++) h = (h * 31 + tok.charCodeAt(i)) | 0;
      return `tok${(h >>> 0).toString(36)}`;
    }
    let uid = localStorage.getItem("chat_ui_uid");
    if (!uid) {
      uid = `u${Math.random().toString(36).slice(2, 10)}`;
      localStorage.setItem("chat_ui_uid", uid);
    }
    return uid;
  })();
  const CHAT_POS_KEY = `chat_pos_${chatUid}`;
  const clampChatPos = (p: { left: number; top: number }): { left: number; top: number } => {
    const r = chat.getBoundingClientRect();
    const w = r.width || 300;
    const h = Math.min(r.height || 160, window.innerHeight - 16);
    return {
      left: Math.min(Math.max(8, p.left), Math.max(8, window.innerWidth - w - 8)),
      top: Math.min(Math.max(8, p.top), Math.max(8, window.innerHeight - h - 8)),
    };
  };
  const applyChatPos = (p: { left: number; top: number }): void => {
    const c = clampChatPos(p);
    chat.style.left = `${c.left}px`;
    chat.style.top = `${c.top}px`;
    chat.style.right = "auto";
    chat.style.bottom = "auto";
  };
  try {
    const savedPos = JSON.parse(localStorage.getItem(CHAT_POS_KEY) ?? "null") as
      { left: number; top: number } | null;
    if (savedPos && Number.isFinite(savedPos.left) && Number.isFinite(savedPos.top)) {
      applyChatPos(savedPos);
    }
  } catch { /* corrupted saved pos — keep the CSS default spot */ }
  const saveChatPos = (): void => {
    const r = chat.getBoundingClientRect();
    localStorage.setItem(
      CHAT_POS_KEY,
      JSON.stringify({ left: Math.round(r.left), top: Math.round(r.top) }),
    );
  };
  // DRAG: pointerdown anywhere on the frame EXCEPT the log/input (they keep
  // scroll + focus) starts a drag; the frame follows the finger 1:1; release
  // persists. Drags never collapse the chat (movement guard below).
  let chatDragId: number | null = null;
  let chatDragOff = { x: 0, y: 0 };
  let chatDragStart = { x: 0, y: 0 };
  let chatDragMoved = false;
  chat.addEventListener("pointerdown", (e) => {
    if (chatDragId !== null) return;
    const t = e.target as HTMLElement;
    if (t.closest("#chat-log, input, button, textarea")) return;
    chatDragId = e.pointerId;
    const r = chat.getBoundingClientRect();
    chatDragOff = { x: e.clientX - r.left, y: e.clientY - r.top };
    chatDragStart = { x: e.clientX, y: e.clientY };
    chatDragMoved = false;
    try { chat.setPointerCapture(e.pointerId); } catch { /* synthetic */ }
  });
  chat.addEventListener("pointermove", (e) => {
    if (e.pointerId !== chatDragId) return;
    if (!chatDragMoved) {
      if (Math.hypot(e.clientX - chatDragStart.x, e.clientY - chatDragStart.y) <= 8) return;
      chatDragMoved = true;
      chat.classList.add("chat-dragging"); // kill text selection mid-drag
    }
    e.preventDefault();
    applyChatPos({ left: e.clientX - chatDragOff.x, top: e.clientY - chatDragOff.y });
  });
  const chatDragEnd = (e: PointerEvent): void => {
    if (e.pointerId !== chatDragId) return;
    chatDragId = null;
    chat.classList.remove("chat-dragging");
    if (chatDragMoved) saveChatPos();
  };
  chat.addEventListener("pointerup", chatDragEnd);
  chat.addEventListener("pointercancel", chatDragEnd);
  window.addEventListener("resize", () => {
    // Keep the saved spot on-screen when the viewport changes (rotation,
    // fullscreen) — re-clamp against the current rect.
    const r = chat.getBoundingClientRect();
    if (chat.style.left || chat.style.top) applyChatPos({ left: r.left, top: r.top });
  });
  // TAP vs DRAG on the toggle chip — the chip OWNS its pointer (capture):
  // a clean tap toggles the chat, a drag from the chip MOVES the whole
  // popup (the chip is the handle). Without the capture, the frame's
  // setPointerCapture retargeted pointerup to the frame and the toggle
  // NEVER fired (the "box chat sau khi kéo ko dùng đc nữa" bug).
  let tglId: number | null = null;
  let tglStart = { x: 0, y: 0 };
  let tglDragging = false;
  const toggleTapTimes: number[] = []; // triple-tap = reset position
  toggle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    e.stopPropagation(); // not an outside tap; the frame drag must not start
    if (tglId !== null) return;
    tglId = e.pointerId;
    tglStart = { x: e.clientX, y: e.clientY };
    tglDragging = false;
    try { toggle.setPointerCapture(e.pointerId); } catch { /* synthetic */ }
  });
  toggle.addEventListener("pointermove", (e) => {
    if (e.pointerId !== tglId) return;
    if (!tglDragging) {
      if (Math.hypot(e.clientX - tglStart.x, e.clientY - tglStart.y) <= 8) return;
      tglDragging = true;
      chat.classList.add("chat-dragging");
      const r = chat.getBoundingClientRect();
      chatDragOff = { x: e.clientX - r.left, y: e.clientY - r.top };
    }
    e.preventDefault();
    applyChatPos({ left: e.clientX - chatDragOff.x, top: e.clientY - chatDragOff.y });
  });
  const toggleEnd = (e: PointerEvent): void => {
    if (e.pointerId !== tglId) return;
    tglId = null;
    chat.classList.remove("chat-dragging");
    if (tglDragging) {
      saveChatPos();
      return;
    }
    // TRIPLE-TAP KILL SWITCH: three clean taps within 700ms drop the saved
    // position and snap the chat back to its CSS default spot — the user
    // is never trapped by a stale/broken saved position.
    const now = performance.now();
    while (toggleTapTimes.length && now - toggleTapTimes[0] > 700) toggleTapTimes.shift();
    toggleTapTimes.push(now);
    if (toggleTapTimes.length >= 3) {
      toggleTapTimes.length = 0;
      chat.style.left = "";
      chat.style.top = "";
      chat.style.right = "";
      chat.style.bottom = "";
      localStorage.removeItem(CHAT_POS_KEY);
      chat.classList.add("chat-reset-flash");
      window.setTimeout(() => chat.classList.remove("chat-reset-flash"), 600);
      return;
    }
    e.preventDefault();
    setChatOpen(!chatOpen);
  };
  toggle.addEventListener("pointerup", toggleEnd);
  toggle.addEventListener("pointercancel", (e) => {
    if (e.pointerId !== tglId) return;
    tglId = null;
    chat.classList.remove("chat-dragging");
  });
  // Outside tap = collapse (REAL taps only: a drag that started outside —
  // e.g. swiping an item across the screen — must not collapse the chat
  // when the finger happens to lift outside the frame).
  onRealOutsideTap(chat, () => {
    if (chatOpen) setChatOpen(false);
  });
  // New chat line while collapsed → bump the badge (chatLine/chatPlayerLine
  // append to #chat-log; watch it with a MutationObserver — zero coupling
  // to the hud methods that render lines).
  new MutationObserver(() => {
    if (!chatOpen) unread += 1;
    renderBadge();
  }).observe(log, { childList: true });
  renderBadge();
}

// MOBILE ROLL-UP HUB: the gear (#hub-roll-btn) toggles body.hub-open,
// which rolls the real #buttons strip up out of the screen edge (CSS).
// A tap anywhere outside the strip/gear rolls it back in; picking a
// button closes it too (one interaction, one hub, no dead duplicates).
if (MOBILE_UI) {
  const gear = document.getElementById("hub-roll-btn");
  const bar = document.getElementById("buttons");
  if (gear && bar) {
    const setHubOpen = (open: boolean): void => {
      document.body.classList.toggle("hub-open", open);
      gear.classList.toggle("open", open);
      // ui.ts listens: 300ms click grace after opening so the opening
      // finger can't accidentally press the first reel button.
      if (open) window.dispatchEvent(new Event("hub-reel-opened"));
    };
    gear.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation(); // not an outside tap
      // The gear ONLY rolls the reel (user correction: opening settings
      // here made the reel pointless — every other button still needs a
      // first tap to reveal, then its own tap to use).
      setHubOpen(!document.body.classList.contains("hub-open"));
    });
    // Taps inside the open strip are hub business, not outside taps.
    bar.addEventListener("pointerdown", (e) => e.stopPropagation());
    // Outside tap rolls the reel back in — REAL taps only (movement guard
    // inside the helper): drags ending outside never close it.
    onRealOutsideTap(bar, () => {
      if (!document.body.classList.contains("hub-open")) return;
      const t = lastOutsideTarget as Node | null;
      if (!t) return;
      if (gear.contains(t) || bar.contains(t)) return;
      setHubOpen(false);
    });
    // Picking any hub button rolls the strip back in (after the click —
    // pointerdown stopPropagation above means the click still fires here;
    // ui.ts's wobble/scroll guards run first and stop guarded clicks).
    bar.addEventListener("click", (e) => {
      if (!(e.target as HTMLElement).closest("div[id$=-button]")) return;
      setHubOpen(false);
    });
    // Picking any hub button rolls the strip back in (after the click —
    // pointerdown stopPropagation above means the click still fires here).
    bar.addEventListener("click", (e) => {
      if (!(e.target as HTMLElement).closest("div[id$=-button]")) return;
      setHubOpen(false);
    });
  }
}
// Reveal the touch layer ONLY once a real game session starts (welcome):
// the login gate + lobby sit in #overlay BELOW this layer, and an always-on
// look surface swallowed every tap on the login button (mobile login bug).
// onWelcome/onConnectionChange below toggle the .mc-on class via this fn.
function setMobileControls(on: boolean): void {
  document.getElementById("mobile-controls")?.classList.toggle("mc-on", on);
}
(window as unknown as { __setMobileControls?: (on: boolean) => void }).__setMobileControls =
  setMobileControls;
// Death / tab-return parity: the pad releases its held directions exactly
// like the keyboard's clearKeys (onSnapshot already calls input.clearKeys();
// hook the same conditions here).
const _mobileClearKeys = input.clearKeys.bind(input);
input.clearKeys = () => {
  _mobileClearKeys();
  mobile.clear();
};

// Bind canvas clicks once Phaser creates it.
// NOTE: must be Phaser's OWN canvas (game.canvas). The weather-fx canvas is
// mounted into #game-root BEFORE Phaser boots, so "#game-root canvas"
// matched the weather canvas — which has pointer-events:none and never
// receives events (clicks dead, browser context menu leaked through).
game.events.once("ready", () => {
  input.bindCanvas(game.canvas);
  // NPC dialogue: ask the SERVER for the NPC's dialogue text (it owns the
  // npcs.json data) — the reply rides the "push" toast lane.
  scene.onNpcInteract = (npc) => {
    net.chatCommand(`npc ${npc.id}`);
  };
  // Station interact pipeline. TOGGLE: if the station window is already
  // open, E closes it (bubble fades back); otherwise open + suppress the
  // bubble. The explosion only plays on the OPEN press.
  scene.onStationInteract = () => {
    if (hud.stationWindowOpen) {
      hud.toggleInventory(false);
      return;
    }
    hud.openCraftPanel();
    hud.onPanelWindowClosed = () => scene.setPromptSuppressed(false);
    scene.setPromptSuppressed(true);
  };
});

// Tab-return hygiene: rAF paused while hidden — clear stuck movement keys
// so returning to the tab never leaves the player walking or targeting
// from stale input.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    input.clearKeys();
    scene.setLocalInput(0, 0, false);
    net.setInput(0, 0, false);
    // Returning to a long-hidden tab: the OS may have silently dropped the
    // idle socket (no onclose fired). If the last snapshot is stale (>4s —
    // normal cadence is 50ms), force a reconnect immediately instead of
    // waiting for the backoff to discover it.
    if (net.isJoined && everWelcomed && lastSnapshotAgeMs() > 4000) {
      if (hud.isLoading) {
        // PC regression guard (3/0): the first big-map bake blocks the main
        // thread for seconds AFTER welcome, so the age can legitimately
        // exceed 4s while the loading overlay is still up. Reconnecting
        // there re-fires welcome (and ANOTHER full bake). Defer instead.
        return;
      }
      console.warn("[WATCHDOG] tab-return forceReconnect: age=", lastSnapshotAgeMs().toFixed(0));
      net.forceReconnect();
    }
  }
});

/** ms since the last snapshot arrived (Infinity before the first one). */
let lastSnapshotAt = 0;
/** Dedupe keys for the incoming-damage hitsplat feed. */
const seenDamageKeys = new Set<string>();
function lastSnapshotAgeMs(): number {
  return lastSnapshotAt === 0 ? Infinity : performance.now() - lastSnapshotAt;
}
/** True once a welcome landed this page-load (i.e. the client IS loading or
 *  already in a world). Guards the alt-tab reconnect: lastSnapshotAgeMs()
 *  reads Infinity before the FIRST snapshot, so alt-tabbing during a long
 *  load (big maps on PC take several seconds) fired forceReconnect -> a
 *  second join -> a second full welcome -> the triple map-load the PC saw
 *  (phones load fast and never hit the window). */
let everWelcomed = false;

// Snapshot freshness watchdog (2s cadence): if we are joined but snapshots
// stopped (hidden-tab socket drop, relay hiccup), show a stale ping and
// start reconnecting. setInterval is throttled to 1 Hz in hidden tabs but
// still fires — exactly what the watchdog needs.
window.setInterval(() => {
  if (!net.isJoined) return;
  const age = lastSnapshotAgeMs();
  if (age > 4000) {
    // Before the first EVER snapshot this page-load the "age" is Infinity
    // but the join may still be mid-handshake (loading overlay counting
    // up) — force-reconnecting there killed the in-flight join and
    // re-sent welcome (the PC triple-load). Only arm once a world has
    // actually streamed snapshots.
    if (!everWelcomed) return;
    if (hud.isLoading) {
      // PC regression guard (3/0): a big-map bake can block the main thread
      // for seconds right after welcome, so no snapshot lands while the
      // overlay is still up. That is NOT a dead socket — never reconnect
      // while the loading overlay counts its blocking assets.
      return;
    }
    console.warn("[WATCHDOG] forceReconnect: age=", age.toFixed(0), "joined=", net.isJoined);
    hud.setPing(-1); // "⚠ mất" in the HUD
    net.forceReconnect();
  }
}, 2000);

// --- boot: OAuth return or direct connect ---

async function boot(): Promise<void> {
  hud.showGate("Đang kết nối…");
  hud.setLoginButton(false, "Đang kết nối…");
  const isOAuthReturn = new URLSearchParams(location.search).has("code");
  if (isOAuthReturn) {
    // Discord bounced back with the code: the server now exchanges it with
    // Discord's API (2 HTTPS roundtrips) — tell the user instead of sitting
    // on a stale "Đang kết nối…" for 2-3s.
    hud.showGate("Đang xác thực Discord…");
    const handled = await net.completeLoginFromUrl();
    if (handled) return; // login_result frame takes it from here
  }
  try {
    await net.connect();
  } catch {
    hud.showGate("Không kết nối được server. Thử tải lại trang.");
    hud.setLoginButton(false);
    return;
  }
  const saved = localStorage.getItem("web_token");
  // TOKEN RESUME: replay the persisted token against the fresh socket. A
  // live token answers login_result silently — NO Discord OAuth, no
  // buttons, straight to the lobby. Only an unknown token (never logged
  // in / logged out / token revoked by a newer login) shows the panel.
  if (saved) {
    hud.showGate("Đang khôi phục phiên…");
    net.resumeLogin(saved);
    return;
  }
  hud.setQuickButton(true, "⚡ Vào nhanh (tắt)", false);
  hud.setLoginButton(true, "🔑 Đăng nhập Discord");
  hud.showGate("Chọn cách vào game:");
}

// (input declared below onSnapshot's usage — hoisted const reference is
// fine because the handler only RUNS after boot.)

// Two independent entry paths — the panel shows BOTH at once, so neither
// "arms" anything: clicking a button IS the choice (no mode flag races).

hud.onLoginClick(() => {
  hud.setLoginButton(false, "Đang mở Discord…");
  hud.setQuickButton(false);
  hud.collapseGatePanel(); // login panel must yield — lobby replaces it
  void net.loginWithDiscord();
});

hud.onQuickClick(() => {
  // Disabled — kept as a no-op guard (button is hidden).
});

// ----- lobby (NEXT GAME layout) wiring -----

// PLAY GAME: join the remembered server instantly, or fetch the list.
hud.onLobbyPlay(() => {
  const token = localStorage.getItem("web_token") ?? "";
  const lastMap = localStorage.getItem("last_channel");
  if (lastMap) {
    hud.setLobbyStatus("Đang vào server…");
    // Keep the channel id as a STRING: snowflakes exceed JS Number precision.
    net.joinScenario(lastMap, token);
  } else {
    hud.setLobbyStatus("Đang lấy danh sách server…");
    net.requestScenarioList();
  }
});

hud.onLobbyServers(() => net.requestScenarioList()); // manual refresh (⟳)
hud.onLobbyEvents(() => hud.toast("Chưa có sự kiện nào đang diễn ra."));
hud.onLobbySettings(() => hud.toast("Cài đặt sẽ ra mắt sau — hiện chỉnh trong game (Esc → Setting)."));
hud.onLobbyHelp(() => hud.toast("Di chuyển bằng WASD/mũi tên, đập block bằng chuột. /help trong game để xem lệnh."));
hud.onLobbyPlayers(() => hud.toast("Số người chơi hiển thị trên từng server ở cột phải."));

hud.onLobbyLogout(() => {
  localStorage.removeItem("web_token");
  localStorage.removeItem("web_avatar");
  location.reload();
});

// scenario_list arrived: paint + clear the loading status.
const _paintScenarioList = hud.showScenarioList.bind(hud);
hud.showScenarioList = (items, onPick) => {
  hud.setLobbyStatus(null);
  _paintScenarioList(items, onPick);
};

// Web map preview: type a map id in the lobby -> solo preview runtime.
hud.onPreviewMap = (mapId) => {
  pendingRejoinChannel = null;
  net.previewMap(mapId);
  hud.setLobbyStatus("Đang mở preview: " + mapId + "…");
};

// The server list is ALWAYS live while the lobby is open: refresh every 5s
// and immediately when the socket (re)connects — no click needed.
let lobbyListTimer: number | null = null;
net.onConnectionChangeExtra = (connected: boolean) => {
  if (connected && !net.isJoined) net.requestScenarioList();
  if (lobbyListTimer !== null) {
    window.clearInterval(lobbyListTimer);
    lobbyListTimer = null;
  }
  if (connected) {
    lobbyListTimer = window.setInterval(() => {
      if (!net.isJoined) net.requestScenarioList();
    }, 5000);
  }
};

// Lobby chat box: local system feed while in the menu (join/leave hints).
// Real cross-player chat needs a session — pre-join we keep it informative.
const nxChatLog = document.getElementById("nx-chat-log")!;
const nxChatForm = document.getElementById("nx-chat-form") as HTMLFormElement;
const nxChatInput = document.getElementById("nx-chat-input") as HTMLInputElement;
function nxChatLine(html: string, cls = ""): void {
  const div = document.createElement("div");
  div.className = `nx-chat-line ${cls}`;
  div.innerHTML = html;
  nxChatLog.appendChild(div);
  while (nxChatLog.childElementCount > 30) nxChatLog.firstElementChild!.remove();
  nxChatLog.scrollTop = nxChatLog.scrollHeight;
}
nxChatLine("Chào mừng đến Đậu Hũ Chiến MMO!", "nx-chat-sys");
nxChatLine("Chọn server ở cột phải rồi bấm CHƠI NGAY.");
nxChatForm.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const text = nxChatInput.value.trim();
  if (!text) return;
  nxChatInput.value = "";
  if (!net.isJoined) {
    nxChatLine(`<b>Bạn:</b> ${text.replace(/[<&>]/g, "")} <span style="opacity:.5">(vào game để chat công khai)</span>`);
  }
});

void boot();

// ----- PREVIEW HARNESS (?preview=1) -----
// Local preview stack (scripts/_preview_stack.py): auto-guest + auto-join the
// bigmap, then hand the remote-control panel the socket. Production servers
// ignore preview_cmd frames; the panel itself only builds with the flag.
const previewMode = new URLSearchParams(location.search).has("preview");

if (previewMode) {
  previewPanel.attach({ send: (f) => net.sendRaw(f) });
  net.onPreviewState = (s) => previewPanel.onState(s);
  // The socket is still dialing at module scope — retry the guest join until
  // the wire is live (200ms beats; harmless if the gate's own flow races us,
  // the stack dedupes by re-creating the preview runtime).
  const startPreview = window.setInterval(() => {
    if (!net.isConnected) return;
    window.clearInterval(startPreview);
    net.requestGuestJoin("9000000000000000042");
    net.previewMap("ekonia/overworld");
  }, 200);
}
