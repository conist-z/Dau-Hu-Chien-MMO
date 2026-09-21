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
// Day/night tint: kept as its own DOM canvas BUT throttled to 8 Hz + dpr 1 +
// duplicate-frame skip (daynight.ts) — the per-rAF full-window repaint was
// the PC-only lag. Same visual as before.
import { dayNightFx } from "./daynight";
import { perf } from "./perf";

const assetTextures = new Map<string, string>(); // image file name -> texture key
let welcome: WelcomePayload | null = null; // kept for held-item lookups
/** Channel to auto-join once a fresh login_result lands after a stale-token
 * error (silent reconnect path; cleared on use or when joining manually). */
let pendingRejoinChannel: string | null = null;

// Quick-play guest login: derive a stable pseudo user_id from localStorage
// so the same browser keeps the same identity/bag across sessions.
const hud = new Hud();
const scene = new WorldScene();

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
// Day/night tint overlay: kept as its own DOM canvas BUT throttled to 8 Hz
// + dpr 1 + duplicate-frame skip (daynight.ts) — the per-rAF full-window
// repaint was the PC-only lag. Same visual as before.
if (perf.daynight) {
  dayNightFx.mount(document.getElementById("game-root")!);
}

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
  if (keys.length === 0) return;
  // Safety net: a lost asset frame must never trap the player behind the
  // overlay — force-hide after 12 s no matter what.
  if (loadSafetyTimer !== null) window.clearTimeout(loadSafetyTimer);
  loadSafetyTimer = window.setTimeout(() => hud.hideLoading(), 12000);
}

function onBlockingAssetDone(key: string): void {
  if (!assetTextures.has(key)) return; // not a blocking asset we track
  hud.tickLoading();
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
    welcome = frame;
    // In-game now: reveal the touch controls (hidden during gate/lobby).
    (window as unknown as { __setMobileControls?: (on: boolean) => void })
      .__setMobileControls?.(true);
    // Landscape lock: on mobile the game is designed for LANDSCAPE — request
    // the OS orientation lock (works in most in-app browsers / after a user
    // gesture; gracefully a no-op where unsupported — the CSS #rotate-veil
    // still tells the user to rotate when the device stays portrait).
    const so = screen.orientation as (ScreenOrientation & {
      lock?: (o: string) => Promise<void>;
    }) | undefined;
    so?.lock?.("landscape").catch(() => { /* unsupported — veil handles it */ });
    // FULLSCREEN (mobile only): entering the game hides the browser URL
    // bar/search chrome — the whole screen becomes the game. Must be called
    // inside the welcome handling of a user-gesture-initiated flow (the
    // login/play click chain) to satisfy the browser's gesture requirement;
    // wrapped so desktop browsers and denied requests are plain no-ops.
    const isTouchDevice = window.matchMedia("(pointer: coarse)").matches;
    if (isTouchDevice && !document.fullscreenElement) {
      const root = document.documentElement;
      const fs = root.requestFullscreen?.({ navigationUI: "hide" })
        ?? (root as HTMLElement & { webkitRequestFullscreen?: () => Promise<void> })
          .webkitRequestFullscreen?.();
      fs?.catch(() => { /* denied/unsupported — immersive stays a best-effort */ });
    }
    scene.buildWorld(frame, (name) => net.fetchAsset(name));
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
    hud.setClock(0);
    // Welcome carries no weather of its own — prime the overlay with the
    // map default; the first snapshot sets the real key ~50ms later.
    weatherFx.setWeather(null);
    if (perf.daynight) dayNightFx.setClock(12 * 3600); // prime: noon (no tint) until first snapshot
    hud.setWeather("sun_clouds");
    hud.chatLine(`Đã vào ${frame.map.name}. WASD để đi, E túi đồ, F tấn công.`);
  },
  onSnapshot: (frame) => {
    lastSnapshotAt = performance.now();
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
    hud.setClock(frame.clock);
    hud.setWeather(frame.weather);
    // Admin /clouds N: >0 = force the cloud-shadow overlay on top of any
    // weather (test hook); 0/undefined = weather-driven only.
    const cloudsOverride = (frame as { clouds_override?: number }).clouds_override ?? 0;
    const weatherKey = cloudsOverride > 0 ? "cloud_shadow" : frame.weather;
    weatherFx.setWeather(weatherKey, cloudsOverride);
    if (perf.daynight) dayNightFx.setClock(frame.clock);
    hud.setBars(frame.self.hp, frame.self.max_hp, frame.self.mana, frame.self.max_mana,
      (frame.self as { stamina?: number }).stamina ?? 1,
      (frame.self as { max_stamina?: number }).max_stamina ?? 0);
    hud.setPurse(frame.self.coins, frame.self.crystals ?? 0);
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
    // prediction and this overlay explains why (5s respawn).
    hud.setDead(!!frame.self.dead, frame.self.respawn_s ?? 0);
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
  onPush: (message) => hud.toast(message),
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
    }
  },
});

// Idle heartbeat: the idle input timer keeps reporting the CURRENT predicted
// position (see Net.flushInput) so the server body can never drift from what
// the player sees — the "đứng yên mà hitbox ở chỗ khác" fix.
net.idlePosHook = () => scene.getSelfPos();

// DEBUG HANDLE: window.net for console probes (preview_evaluate).
(window as unknown as { net: Net }).net = net;

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
// (place/eat/station), drag = camera pan, pinch = zoom (mobile_controls.ts).
const mobile = new MobileControls({
  onMove: (dx, dy, running) => {
    // Analog vector (magnitude 0..1, screen-space) — identical contract to
    // the keyboard's normalized onVector. Reuse the SAME body as the
    // KeyboardInput onVector hook by routing through setLocalInput + setInput.
    scene.setLocalInput(dx, dy, running);
    const sp = scene.getSelfPos();
    net.setInput(dx, dy, running, sp);
  },
  onAttack: () => {
    net.action("attack");
    scene.combatSwing();
  },
  onToggleInventory: () => hud.toggleInventory(),
  onTapWorld: (sx, sy) => {
    // Same pipeline as a desktop primary click (onCanvasAction "primary").
    // MOBILE AIM MODE: touch has no cursor — the finger is imprecise, and
    // the old tap-to-act fired on whatever tile happened to sit under it
    // ("đập phá cứ lệch lệch"). Two-tap flow instead: FIRST tap arms the
    // green target square on the tile; SECOND tap on the SAME tile
    // executes chop/break/attack. (onTapWorld only ever fires from
    // MobileControls, so every call here is a touch call.)
    if (!scene.mobileTapAim(sx, sy)) return; // first tap: aim armed, no action
    scene.setMouseTile({ x: sx, y: sy });
    const tile = scene.screenToTile(sx, sy);
    if (!tile) {
      net.action("chop");
      scene.swingSelfHand();
      return;
    }
    const target = scene.clampClickTile(tile);
    if (!target) {
      net.actionAt("chop", tile.x, tile.y);
      scene.swingSelfHand();
      return;
    }
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
    // any armed target so the stale green square never lingers.
    scene.consumeAimTarget();
    const tile = scene.screenToTile(sx, sy);
    if (tile && scene.hoveringStation()) {
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
  onZoomPinch: (factor) => scene.applyPinchZoom(factor),
  onDragLook: (dx, dy) => scene.applyLookPan(dx, dy),
});
mobile.mount();

// ---- MOBILE CHAT CHIP + TRAY OUTSIDE-TAP (touch devices only) ----
// Chat moves to the TOP-left on phones (CSS above) as a collapsed chip:
// tap = expand log + input; a tap OUTSIDE the chat frame collapses it.
// The unread badge counts lines that arrived while collapsed (cleared on
// open) — the player never silently misses chat on a phone.
if (window.matchMedia("(pointer: coarse)").matches) {
  const chat = document.getElementById("hud-chat")!;
  const log = document.getElementById("chat-log")!;
  const toggle = document.createElement("div");
  toggle.id = "chat-toggle";
  toggle.setAttribute("role", "button");
  toggle.innerHTML = `<span>💬 Chat</span><span class="ct-badge" id="chat-badge"></span>`;
  chat.prepend(toggle);
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
  toggle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    e.stopPropagation(); // not an outside tap
    setChatOpen(!chatOpen);
  });
  // Clicks inside the EXPANDED chat (log scroll, input focus) are not
  // outside taps either.
  chat.addEventListener("pointerdown", (e) => e.stopPropagation());
  // Outside tap = collapse. Bubble-phase on window: taps INSIDE the chat
  // bubble up from #hud-chat (whose pointerdown handlers don't stop
  // propagation for non-toggle children), so the contains() check is the
  // authoritative guard — one listener, one code path.
  window.addEventListener("pointerdown", (e) => {
    if (!chatOpen) return;
    if (chat.contains(e.target as Node)) return;
    setChatOpen(false);
  });
  // New chat line while collapsed → bump the badge (chatLine/chatPlayerLine
  // append to #chat-log; watch it with a MutationObserver — zero coupling
  // to the hud methods that render lines).
  new MutationObserver(() => {
    if (!chatOpen) unread += 1;
    renderBadge();
  }).observe(log, { childList: true });
  renderBadge();

  // HUB TRAY outside-tap: same rule as the chat chip — a tap anywhere
  // outside the tray and the gear rolls the tray back in.
  window.addEventListener("pointerdown", (e) => {
    const tray = document.getElementById("buttons-tray");
    if (!tray || !tray.classList.contains("open")) return;
    const t = e.target as Node;
    if (tray.contains(t)) return;
    if (document.getElementById("settings-anchor")?.contains(t)) return;
    hud.closeHubTray();
  });
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
