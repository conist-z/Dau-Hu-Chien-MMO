// Entry point: wires Net (transport) + WorldScene (Phaser) + Hud (DOM) +
// KeyboardInput. OAuth return handling + asset texture cache live here.

import Phaser from "phaser";
import { WorldScene } from "./game";
import { KeyboardInput } from "./input";
import { Net } from "./net";
import type { InventoryPayload, WelcomePayload } from "./protocol";
import { Hud } from "./ui";
import { weatherFx } from "./weather";
import { dayNightFx } from "./daynight";

const assetTextures = new Map<string, string>(); // image file name -> texture key
let welcome: WelcomePayload | null = null; // kept for held-item lookups
let quickPlayArmed = false;

// Quick-play guest login: derive a stable pseudo user_id from localStorage
// so the same browser keeps the same identity/bag across sessions.
function guestLogin(net: Net): void {
  let guestId = localStorage.getItem("guest_id");
  if (!guestId) {
    guestId = String(900000000000000000 + Math.floor(Math.random() * 99999999999999999));
    localStorage.setItem("guest_id", guestId);
  }
  localStorage.setItem("web_token", `guest:${guestId}`);
  localStorage.setItem("web_name", `Khach-${guestId.slice(-4)}`);
  // Server-side: the login frame accepts a guest token path (see core.py).
  net.requestGuestJoin(guestId);
}

const hud = new Hud();
const scene = new WorldScene();
// Animated weather overlay (rain/snow/storm...) — plain canvas above the
// Phaser canvas, below the HUD. Mounts once; the weather key arrives in
// every snapshot (and the welcome default below).
weatherFx.mount(document.getElementById("game-root")!);
// Day/night lighting overlay — full-screen multiply tint sampled from the
// same 24h gradient as the Discord client; the clock arrives per snapshot.
dayNightFx.mount(document.getElementById("game-root")!);

const game = new Phaser.Game({
  type: Phaser.AUTO,
  parent: "game-root",
  backgroundColor: "#20303c",
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
  // buildBlocks. Mob sheets register under "mob-<id>". Tilesets keep their
  // basename key.
  const key = name.startsWith("blocks/")
    ? `block-${name.slice("blocks/".length).replace(/\.png$/i, "")}`
    : name.startsWith("mobs/")
      ? `mob-${name.slice("mobs/".length).replace(/\.png$/i, "")}`
      : name.replace(/\.png$/i, "");
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
      // Mob sheets MUST register as a 32x32 spritesheet: addImage + setCrop
      // keeps the render quad at the FULL sheet size (UV-only crop), so each
      // animation cell draws offset from the object origin and every frame
      // change visibly shifts the sprite. Spritesheet frames get their own
      // cut + origin — setFrame centres each cell exactly.
      game.textures.addSpriteSheet(key, img, { frameWidth: 32, frameHeight: 32 });
    } else {
      game.textures.addImage(key, img);
    }
    URL.revokeObjectURL(url);
    if (name.startsWith("blocks/")) {
      // Block face arrived: redraw the block layer with the real sprite.
      scene.onBlockTexture(name.slice("blocks/".length).replace(/\.png$/i, ""));
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
    // Tileset arrived: re-bake ONLY the map canvas (no world rebuild —
    // rebuilding duplicated players and reset the camera).
    scene.onTilesetLoaded(name);
  };
  img.src = url;
  assetTextures.set(key, key);
}

function applyInventory(inv: InventoryPayload): void {
  hud.setInventory(inv);
  // Plan A instant hand: self tool icon follows the LOCAL hotbar at once
  // (no 20 Hz wait) — snapshot.held converges it with server truth after.
  scene.setSelfHeldFromHotbar(inv.hotbar ?? [], hud.currentSlot);
}

const net = new Net({
  onWelcome: (frame) => {
    welcome = frame;
    scene.buildWorld(frame, (name) => net.fetchAsset(name));
    hud.hideGate();
    hud.setInventory(frame.inventory);
    hud.setRecipes(frame.recipes);
    // Re-sync the server's held slot after (re)login — the session was
    // recreated server-side and defaults to slot 0. Self hand shows the
    // server echo at once (welcome.held), hotbar may still be resolving.
    scene.setSelfHeld(frame.held ?? null);
    net.selectSlot(hud.currentSlot);
    hud.setItemEmojis(frame.item_emojis ?? {});
    hud.setBars(frame.self.hp, frame.self.max_hp, frame.self.mana, frame.self.max_mana);
    hud.setClock(0);
    // Welcome carries no weather of its own — prime the overlay with the
    // map default; the first snapshot sets the real key ~50ms later.
    weatherFx.setWeather(null);
    dayNightFx.setClock(12 * 3600); // prime: noon (no tint) until first snapshot
    hud.setWeather("sun_clouds");
    hud.chatLine(`Đã vào ${frame.map.name}. WASD để đi, E túi đồ, F tấn công.`);
  },
  onSnapshot: (frame) => {
    scene.applySnapshot(frame);
    hud.setClock(frame.clock);
    hud.setWeather(frame.weather);
    weatherFx.setWeather(frame.weather);
    dayNightFx.setClock(frame.clock);
    hud.setBars(frame.self.hp, frame.self.max_hp, frame.self.mana, frame.self.max_mana);
    // Death veil: server ignores our inputs while dead; the scene freezes
    // prediction and this overlay explains why (5s respawn).
    hud.setDead(!!frame.self.dead, frame.self.respawn_s ?? 0);
    if (frame.self.dead) input.clearKeys();
    applyInventory(frame.inventory);
  },
  onScenarioList: (items) => {
    hud.showScenarioList(items, (channelId) => {
      const token = localStorage.getItem("web_token") ?? "";
      localStorage.setItem("last_channel", String(channelId));
      net.joinScenario(channelId, token);
    });
  },
  onInventory: applyInventory,
  onPush: (message) => hud.toast(message),
  onError: (code) => {
    // Any auth/session error before joining: wipe the stale token and fall
    // back to quick-play so the user is never stuck on a dead-end gate.
    const stale =
      code === "bad_token" ||
      (code === "not_joined" && !net.isJoined);
    if (stale) {
      localStorage.removeItem("web_token");
      quickPlayArmed = true;
      hud.setLoginButton(true, "Vào game nhanh (không cần đăng nhập)");
      hud.showGate("Phiên cũ đã hết — bấm vào game để chơi ngay.");
      return;
    }
    if (code === "scenario_missing_or_full") {
      hud.showGate("Map đầy hoặc không tồn tại.");
    }
    hud.toast(`Lỗi: ${code}`);
  },
  onAssetData: (name, b64) => {
    if (b64) applyTexture(name, b64);
  },
  onLoginOk: (token, displayName) => {
    hud.setLoginButton(true, `Tiếp: ${displayName}`);
    // Guest flow: auto-join the remembered map immediately (no pick step).
    const lastMap = localStorage.getItem("last_channel");
    if (token.startsWith("guest:") || localStorage.getItem("guest_id")) {
      if (lastMap) {
        hud.showGate("Đang vào map…");
        // Keep the channel id as a STRING: snowflakes exceed JS Number precision.
        net.joinScenario(lastMap, token);
      } else {
        hud.showGate("Chọn map…");
        net.requestScenarioList();
      }
      return;
    }
    hud.showGate("Đăng nhập xong — chọn map…");
    net.requestScenarioList();
    void token;
  },
  onLoginFail: (error) => {
    hud.setLoginButton(true);
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
    }
  },
});

hud.setHooks(
  (itemId) => net.inventoryOp("use", { item_id: itemId }),
  (text) => {
    hud.chatLine(`> ${text}`);
    net.chatCommand(text);
  },
  (recipeId) => net.craftOp(recipeId),
);

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
    net.setInput(dx, dy, running);
  },
  onAttack: () => {
    // Cheap melee: attack IN PLACE + swing the hand at once (the swing is
    // client-optimistic; a landed server hit re-triggers it via the echo).
    // Kaetram parity: atk anim plays exactly ONCE per click, ~450ms.
    net.action("attack");
    scene.combatSwing();
  },
  onToggleInventory: () => hud.toggleInventory(),
  onSlot: (index) => hud.selectSlot(index),
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
      // Contextual: placed block under cursor -> break; else chop. The hand
      // swings AT ONCE (client-optimistic); the server echo (res_progress
      // grows) re-swings it on each LANDED hit for the multi-hit rhythm.
      const breaking = scene.isBlockAt(target.x, target.y);
      net.actionAt(breaking ? "break" : "chop", target.x, target.y);
      scene.swingSelfHand();
      if (breaking) {
        // Optimistic local collision: the block stops blocking movement NOW
        // (the echo confirms, or reconcileBlockAction restores it fast if
        // the server rejected the break).
        scene.optimisticBreak(target.x, target.y);
      }
      return;
    }
    // Secondary (right-click): scope follows the HELD hotbar slot ONLY —
    // place the block actually in hand. If the active slot holds a tool,
    // an unplaceable item, or nothing, nothing happens at all (no action,
    // no message). The client never sends place with a non-block held.
    const placeable = new Set((welcome?.blocks_catalog ?? []).map((b) => b.id));
    const held = hud.heldItem;
    if (!tile || !held || !placeable.has(held)) return;
    const target = scene.clampClickTile(tile);
    if (!target) {
      // Too far: still send so the server answers "Quá xa." honestly — but
      // NO optimistic state (the block will not appear).
      net.placeAt(tile.x, tile.y, held);
      return;
    }
    net.placeAt(target.x, target.y, held);
    // Optimistic local collision: the block is solid IMMEDIATELY so a fast
    // run cannot pass through a block we just placed before the snapshot
    // arrives (the echo/snapshot reconciles if the server rejected it).
    scene.optimisticPlace(target.x, target.y);
  },
  onCanvasHover: (sx, sy) => {
    // Store the raw cursor position; the scene re-derives the tile every
    // frame (camera moves under a still cursor — cached tiles go stale).
    scene.setMouseTile(sx < 0 ? null : { x: sx, y: sy });
  },
});

// Bind canvas clicks once Phaser creates it.
// NOTE: must be Phaser's OWN canvas (game.canvas). The weather-fx canvas is
// mounted into #game-root BEFORE Phaser boots, so "#game-root canvas"
// matched the weather canvas — which has pointer-events:none and never
// receives events (clicks dead, browser context menu leaked through).
game.events.once("ready", () => {
  input.bindCanvas(game.canvas);
});

// Tab-return hygiene: rAF paused while hidden — clear stuck movement keys
// so returning to the tab never leaves the player walking or targeting
// from stale input.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    input.clearKeys();
    scene.setLocalInput(0, 0, false);
    net.setInput(0, 0, false);
  }
});

// --- boot: OAuth return or direct connect ---

async function boot(): Promise<void> {
  hud.showGate("Đang kết nối…");
  hud.setLoginButton(false, "Đang kết nối…");
  const isOAuthReturn = new URLSearchParams(location.search).has("code");
  if (isOAuthReturn) {
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
  if (saved && saved.startsWith("guest:")) {
    // Guest tokens do not survive a bot restart (server-side registry is
    // in-memory): ALWAYS re-run guest login, never send frames with the
    // stale token — the server would reject them with not_joined.
    hud.showGate("Chế độ nhanh: tự động vào game…");
    guestLogin(net);
  } else if (saved) {
    hud.showGate("Đã có phiên — chọn map…");
    net.requestScenarioList();
  } else {
    // Quick-play: skip Discord login, join as a guest id.
    hud.showGate("Chế độ nhanh: vào game không cần đăng nhập Discord.");
    hud.setLoginButton(true, "Vào game nhanh (không cần đăng nhập)");
    quickPlayArmed = true;
  }
}

// (input declared below onSnapshot's usage — hoisted const reference is
// fine because the handler only RUNS after boot.)

hud.onLoginClick(() => {
  if (quickPlayArmed) {
    hud.setLoginButton(false, "Đang vào game…");
    guestLogin(net);
    // Guard: server không trả lời trong 10s -> báo lỗi thay vì treo.
    window.setTimeout(() => {
      if (!net.isJoined) {
        hud.setLoginButton(true);
        hud.showGate(
          "Server game chưa kết nối được relay (bot offline hoặc RELAY_URL sai). " +
          "Thử lại sau — hoặc báo admin xem log panel có dòng [WEB] relay connected.",
        );
      }
    }, 10000);
    return;
  }
  hud.setLoginButton(false, "Đang mở Discord…");
  void net.loginWithDiscord();
});

void boot();
