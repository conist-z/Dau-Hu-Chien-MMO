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
  const key = name.replace(/\.png$/i, "");
  if (assetTextures.has(key) || !game.textures) return;
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: "image/png" });
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => {
    if (game.textures.exists(key)) game.textures.remove(key);
    game.textures.addImage(key, img);
    URL.revokeObjectURL(url);
    // Tileset arrived: re-bake ONLY the map canvas (no world rebuild —
    // rebuilding duplicated players and reset the camera).
    scene.onTilesetLoaded(name);
  };
  img.src = url;
  assetTextures.set(key, key);
}

function applyInventory(inv: InventoryPayload): void {
  hud.setInventory(inv);
}

const net = new Net({
  onWelcome: (frame) => {
    welcome = frame;
    scene.buildWorld(frame, (name) => net.fetchAsset(name));
    hud.hideGate();
    hud.setInventory(frame.inventory);
    hud.setRecipes(frame.recipes);
    // Re-sync the server's held slot after (re)login — the session was
    // recreated server-side and defaults to slot 0.
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
  onHeld: (_slot, _itemId) => {
    // Hotbar switches are SILENT on purpose: changing hands must not spam
    // chat with "Cầm: … / Tay không …" (bug report 11/09).
  },
  onActionResult: (frame) => {
    if (frame.needed != null) scene.noteChopNeeded(frame.tx, frame.ty, frame.needed);
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
// tool only. Silent on purpose: no chat spam.
hud.onSlotSelect((slot) => net.selectSlot(slot));

const input = new KeyboardInput({
  onVector: (dx, dy, running) => {
    // Zero-lag: prediction runs every frame locally; the network copy is
    // just the authoritative echo (20 Hz throttle in Net).
    scene.setLocalInput(dx, dy, running);
    net.setInput(dx, dy, running);
  },
  onAttack: () => net.action("attack"),
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
      // Contextual: placed block under cursor -> break; else chop.
      net.actionAt(scene.isBlockAt(tile.x, tile.y) ? "break" : "chop", tile.x, tile.y);
      return;
    }
    // Secondary (right-click): scope follows the HELD hotbar slot ONLY —
    // place the block actually in hand. If the active slot holds a tool,
    // an unplaceable item, or nothing, nothing happens at all (no action,
    // no message). The client never sends place with a non-block held.
    const placeable = new Set((welcome?.blocks_catalog ?? []).map((b) => b.id));
    const held = hud.heldItem;
    if (!tile || !held || !placeable.has(held)) return;
    net.placeAt(tile.x, tile.y, held);
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
