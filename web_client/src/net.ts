// WebSocket networking: connect, Discord OAuth login, join, throttled input,
// asset fetch, frame dispatch. One class, no framework.

import type { InventoryPayload, ScenarioItem, ServerFrame } from "./protocol";
import { MSG_CHAT_CMD, MSG_INPUT, MSG_INV_OP, MSG_JOIN, MSG_LIST, MSG_PING } from "./protocol";

const WS_PATH = "/ws";
const CONFIG_PATH = "/app-config.json"; // static file in dist/ (no env needed)
const INPUT_SEND_INTERVAL_MS = 50; // 20 Hz max — server ticks at 20 Hz

export interface NetHandlers {
  onWelcome: (frame: Extract<ServerFrame, { type: "welcome" }>) => void;
  onSnapshot: (frame: Extract<ServerFrame, { type: "snapshot" }>) => void;
  onScenarioList: (items: ScenarioItem[]) => void;
  onInventory: (inv: InventoryPayload) => void;
  onPush: (message: string) => void;
  onError: (code: string) => void;
  onAssetData: (name: string, b64: string | null) => void;
  onLoginOk: (token: string, displayName: string) => void;
  onLoginFail: (error: string) => void;
  onHeld: (slot: number, itemId: string | null) => void;
  onActionResult: (frame: { name: string; ok: boolean; reason: string; tx: number | null; ty: number | null; kind: string; drops: [string, number][] }) => void;
  onConnectionChange: (connected: boolean) => void;
}

export class Net {
  private ws: WebSocket | null = null;
  private token = "";
  private joined = false;
  private handlers: NetHandlers;
  private pendingInput = { dx: 0, dy: 0, running: false, dirty: false };
  private inputTimer: number | null = null;
  private pingTimer: number | null = null;

  constructor(handlers: NetHandlers) {
    this.handlers = handlers;
  }

  get isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  get isJoined(): boolean {
    return this.joined;
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${proto}//${location.host}${WS_PATH}`;
      this.ws = new WebSocket(url);
      this.ws.onopen = () => {
        this.handlers.onConnectionChange(true);
        this.startPing();
        resolve();
      };
      this.ws.onerror = () => reject(new Error("ws_error"));
      this.ws.onclose = () => {
        this.joined = false;
        this.handlers.onConnectionChange(false);
      };
      this.ws.onmessage = (ev) => this.onMessage(ev.data as string);
    });
  }

  // ----- Discord OAuth (authorization-code + PKCE, redirect back here) -----

  async loginWithDiscord(): Promise<void> {
    // Static config file shipped in dist/ — client_id is public by design.
    // (Served from disk per request, so uploading a new file needs no restart.)
    const cfgResp = await fetch(CONFIG_PATH);
    const cfg = (await cfgResp.json()) as { client_id: string; redirect_uri?: string };
    const redirect = cfg.redirect_uri ?? location.origin + location.pathname;
    const { verifier, challenge } = await this.makePkce();
    sessionStorage.setItem("pkce_verifier", verifier);
    const params = new URLSearchParams({
      client_id: cfg.client_id,
      redirect_uri: redirect,
      response_type: "code",
      scope: "identify",
      state: challenge.slice(0, 8),
      code_challenge: challenge,
      code_challenge_method: "S256",
    });
    location.href = `https://discord.com/oauth2/authorize?${params}`;
  }

  async completeLoginFromUrl(): Promise<boolean> {
    const url = new URL(location.href);
    const code = url.searchParams.get("code");
    if (!code) return false;
    // Clean the address bar immediately (refresh-safe).
    history.replaceState(null, "", location.pathname);
    const verifier = sessionStorage.getItem("pkce_verifier") ?? "";
    // The exchange happens server-side via the login frame (code + verifier
    // round-trip through the relay; the secret never touches the browser).
    await this.connect();
    this.send({
      type: "login",
      code,
      redirect_uri: location.origin + location.pathname,
      code_verifier: verifier,
    });
    return true;
  }

  private async makePkce(): Promise<{ verifier: string; challenge: string }> {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    const verifier = btoa(String.fromCharCode(...bytes))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
    const challenge = btoa(String.fromCharCode(...new Uint8Array(digest)))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    return { verifier, challenge };
  }

  // ----- join / gameplay -----

  // channelId stays a string: Discord snowflakes exceed JS Number precision.
  joinScenario(channelId: string | number, token?: string): void {
    if (token) this.token = token;
    this.send({ type: MSG_JOIN, token: this.token, channel_id: String(channelId) });
  }

  requestScenarioList(): void {
    this.send({ type: MSG_LIST });
  }

  requestGuestJoin(guestId: string): void {
    this.send({ type: "guest_login", guest_id: guestId });
  }

  /** Tell the server which hotbar slot is held (tools resolve from it). */
  selectSlot(slot: number): void {
    this.send({ type: "select_slot", slot });
  }

  setInput(dx: number, dy: number, running: boolean): void {
    const p = this.pendingInput;
    p.dx = dx; p.dy = dy; p.running = running; p.dirty = true;
    if (this.inputTimer === null) {
      this.inputTimer = window.setInterval(() => this.flushInput(), INPUT_SEND_INTERVAL_MS) as unknown as number;
    }
  }

  private flushInput(): void {
    const p = this.pendingInput;
    if (!p.dirty || !this.joined) return;
    p.dirty = false;
    this.send({ type: MSG_INPUT, dx: p.dx, dy: p.dy, running: p.running });
    if (p.dx === 0 && p.dy === 0 && this.inputTimer !== null) {
      // Idle: one zero vector sent, stop the timer until the next keypress.
      window.clearInterval(this.inputTimer);
      this.inputTimer = null;
    }
  }

  action(name: string, dx?: number, dy?: number): void {
    const frame: Record<string, unknown> = { type: "action", name };
    if (dx !== undefined) frame.dx = dx;
    if (dy !== undefined) frame.dy = dy;
    this.send(frame);
  }

  /** Turn the player to an 8-way direction (server updates facing). */
  turn(dir: string): void {
    this.send({ type: "action", name: "turn", dir });
  }

  inventoryOp(op: "move_to" | "use", payload: { item_id?: string; slot?: number }): void {
    this.send({ type: MSG_INV_OP, op, ...payload });
  }

  craftOp(recipeId: string): void {
    this.send({ type: "craft_op", recipe_id: recipeId });
  }

  chatCommand(text: string): void {
    this.send({ type: MSG_CHAT_CMD, text });
  }

  fetchAsset(name: string): void {
    this.send({ type: "asset_request", name });
  }

  // ----- internals -----

  private send(obj: Record<string, unknown>): void {
    if (this.isConnected) {
      this.ws!.send(JSON.stringify(obj));
    }
  }

  private startPing(): void {
    if (this.pingTimer !== null) window.clearInterval(this.pingTimer);
    this.pingTimer = window.setInterval(() => {
      this.send({ type: MSG_PING, t: Date.now() });
    }, 15000);
  }

  private onMessage(raw: string): void {
    let frame: ServerFrame;
    try {
      frame = JSON.parse(raw) as ServerFrame;
    } catch {
      return;
    }
    switch (frame.type) {
      case "login_result":
        if (frame.ok && frame.token) {
          this.token = frame.token;
          localStorage.setItem("web_token", frame.token);
          localStorage.setItem("web_name", frame.display_name ?? "");
          this.handlers.onLoginOk(frame.token, frame.display_name ?? "");
        } else {
          this.handlers.onLoginFail(frame.error ?? "login_failed");
        }
        break;
      case "welcome":
        this.joined = true;
        this.handlers.onWelcome(frame);
        break;
      case "snapshot":
        this.handlers.onSnapshot(frame);
        break;
      case "scenario_list":
        this.handlers.onScenarioList(frame.items);
        break;
      case "inventory_delta":
        this.handlers.onInventory(frame.inventory);
        break;
      case "push":
        this.handlers.onPush(frame.message);
        break;
      case "error":
        this.handlers.onError(frame.code);
        break;
      case "asset_data":
        this.handlers.onAssetData(frame.name, frame.b64);
        break;
      case "pong":
        break;
      case "action_result":
        this.handlers.onActionResult(frame);
        break;
      case "held":
        this.handlers.onHeld(frame.slot as number, (frame.item_id as string | null) ?? null);
        break;
    }
  }
}
