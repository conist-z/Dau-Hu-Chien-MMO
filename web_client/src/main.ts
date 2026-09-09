// Entry point: wires Net (transport) + WorldScene (Phaser) + Hud (DOM) +
// KeyboardInput. OAuth return handling + asset texture cache live here.

import Phaser from "phaser";
import { WorldScene } from "./game";
import { KeyboardInput } from "./input";
import { Net } from "./net";
import type { InventoryPayload, WelcomePayload } from "./protocol";
import { Hud } from "./ui";

const assetTextures = new Map<string, string>(); // image file name -> texture key
let welcome: WelcomePayload | null = null; // kept for held-item lookups
void welcome;
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
    hud.setBars(frame.self.hp, frame.self.max_hp, frame.self.mana, frame.self.max_mana);
    hud.setClock(0);
    hud.setWeather("sun_clouds");
    hud.chatLine(`Đã vào ${frame.map.name}. WASD để đi, E túi đồ, F tấn công.`);
  },
  onSnapshot: (frame) => {
    scene.applySnapshot(frame);
    hud.setClock(frame.clock);
    hud.setWeather(frame.weather);
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
  onHeld: (slot, itemId) => {
    hud.chatLine(itemId ? `Cầm: ${itemId} (ô ${slot + 1})` : `Tay không (ô ${slot + 1})`);
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
// tool only (the server echoes back a `held` frame; no auto-use).
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
    const tile = scene.screenToTile(sx, sy);
    if (kind === "primary") {
      // Contextual harvest on the clicked tile (server resolves chop vs
      // mine vs block by what stands there + the held/best tool).
      net.action("chop");
    } else {
      const off = scene.offsetFromSelf(tile);
      net.action("place", off.dx, off.dy);
    }
  },
});

// Bind canvas clicks once Phaser creates it.
game.events.once("ready", () => {
  const canvas = document.querySelector("#game-root canvas") as HTMLCanvasElement | null;
  if (canvas) input.bindCanvas(canvas);
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
