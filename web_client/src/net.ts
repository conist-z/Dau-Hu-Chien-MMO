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
  onCraftResult: (ok: boolean, reason: string, itemId: string | null, qty: number) => void;
  onPush: (message: string) => void;
  onError: (code: string) => void;
  onAssetData: (name: string, b64: string | null) => void;
  onLoginOk: (token: string, displayName: string) => void;
  onLoginFail: (error: string) => void;
  onHeld: (slot: number, itemId: string | null) => void;
  onActionResult: (frame: { name: string; ok: boolean; reason: string; tx: number | null; ty: number | null; kind: string; target_id?: string | null; target_defeated?: boolean; needed: number | null; drops: [string, number][]; damage?: number; critical?: boolean; missed?: boolean }) => void;
  onConnectionChange: (connected: boolean) => void;
  /** Optional RTT report (EMA ms) after each pong — feeds reconciliation. */
  onRtt?: (rttMs: number) => void;
  /** Each seq'd input as it is flushed to the wire (replay buffer feed). */
  onSeqInput?: (seq: number, dx: number, dy: number, running: boolean) => void;
}

export class Net {
  private ws: WebSocket | null = null;
  private token = "";
  private joined = false;
  /** Monotonic input sequence (input-sequence reconciliation): every input
   * frame carries seq; the snapshot acks last_seq and the game scene replays
   * unacked inputs from its buffer. Reset per connection. */
  private inputSeq = 0;
  /** Delivery callback for each seq'd input, so the scene can buffer exact
   * inputs for replay (set by main.ts right after construction). */
  onSeqInput: ((seq: number, dx: number, dy: number, running: boolean) => void) | null = null;
  private handlers: NetHandlers;
  private pendingInput = { dx: 0, dy: 0, running: false, dirty: false };
  private inputTimer: number | null = null;
  private pingTimer: number | null = null;
  private lastRttMs = 0;
  onRtt: ((rttMs: number) => void) | null = null;
  // --- auto-reconnect (tab-return freshness) ---
  /** Last joined channel id — replayed verbatim on reconnect. */
  private lastChannel: string | null = null;
  private reconnectTimer: number | null = null;
  private reconnectAttempt = 0;
  private connecting = false;
  /** True once the session ever joined a scenario (gates auto-rejoin). */
  private everJoined = false;

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
    if (this.connecting) return Promise.resolve();
    this.connecting = true;
    return new Promise((resolve, reject) => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${proto}//${location.host}${WS_PATH}`;
      this.ws = new WebSocket(url);
      this.ws.onopen = () => {
        this.connecting = false;
        this.reconnectAttempt = 0; // healthy again
        this.handlers.onConnectionChange(true);
        this.startPing();
        resolve();
      };
      this.ws.onerror = () => {
        this.connecting = false;
        reject(new Error("ws_error"));
      };
      this.ws.onclose = () => {
        this.joined = false;
        this.handlers.onConnectionChange(false);
        // Auto-reconnect with capped backoff — the tab-return freshness
        // story: rAF pauses while hidden, the OS may drop the idle socket,
        // and on return the client silently reconnects + rejoins the map.
        this.scheduleReconnect();
      };
      this.ws.onmessage = (ev) => this.onMessage(ev.data as string);
    });
  }

  /** Exponential backoff reconnect: 1s, 2s, 4s… capped at 8s, forever.
   * Skipped when no gameplay session ever joined (boot flow owns that). */
  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;
    if (!this.everJoined) return;
    const delay = Math.min(8000, 1000 * 2 ** this.reconnectAttempt);
    this.reconnectAttempt = Math.min(4, this.reconnectAttempt + 1);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      void this.reconnect();
    }, delay);
  }

  /** Immediate reconnect (watchdog / tab-return path): drop the current
   * socket, clear any pending backoff timer, then reconnect + rejoin now. */
  forceReconnect(): void {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      // onclose fires synchronously-ish and calls scheduleReconnect —
      // suppress that by marking the socket as the one we just closed.
      this.ws.onclose = null;
      this.ws.close();
    }
    this.joined = false;
    void this.reconnect();
  }

  /** Fresh socket + rejoin the last channel. The server-side session lives
   * in an in-memory registry, so the rejoin re-authenticates with the SAME
   * token; a stale guest token falls back via main.ts's error handler. */
  private async reconnect(): Promise<void> {
    if (this.isConnected) return;
    try {
      await this.connect();
    } catch {
      this.scheduleReconnect();
      return;
    }
    if (this.lastChannel) {
      this.joinScenario(this.lastChannel, this.token || undefined);
    }
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
    this.lastChannel = String(channelId); // reconnect replays this join
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
    const seq = ++this.inputSeq;
    this.send({ type: MSG_INPUT, seq, dx: p.dx, dy: p.dy, running: p.running });
    // Hand the exact input to the scene's replay buffer (same seq the server
    // will ack). Buffered even for the idle zero vector — replay needs it to
    // stop moving at the right instant.
    if (this.onSeqInput) this.onSeqInput(seq, p.dx, p.dy, p.running);
    if (p.dx === 0 && p.dy === 0 && this.inputTimer !== null) {
      // Idle: one zero vector sent, stop the timer until the next keypress.
      window.clearInterval(this.inputTimer);
      this.inputTimer = null;
    }
  }

  /** Actions target an ABSOLUTE tile (tx,ty) — no client position math. */
  actionAt(name: string, tx: number, ty: number): void {
    this.send({ type: "action", name, tx, ty });
  }

  /** Place at an absolute tile, explicitly naming the block to place. */
  placeAt(tx: number, ty: number, blockId?: string): void {
    this.send({ type: "action", name: "place", tx, ty, block_id: blockId ?? "" });
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

  inventoryOp(op: "move_to" | "use" | "split" | "reorder",
              payload: { item_id?: string; slot?: number; order?: { id: string; qty: number }[] }): void {
    this.send({ type: MSG_INV_OP, op, ...payload });
  }

  craftOp(recipeId: string): void {
    this.send({ type: "craft_op", recipe_id: recipeId });
  }

  /** Grid craft: send exactly what sits in the material grid. */
  craftGrid(inputs: { id: string; qty: number }[]): void {
    this.send({
      type: "craft_op",
      inputs: inputs.map((s) => ({ id: s.id, qty: s.qty })),
    });
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
    // First ping IMMEDIATELY (reconciliation needs RTT during the join
    // warmup — the worst-lag window), then every 5s to track drift.
    this.send({ type: MSG_PING, t: Date.now() });
    this.pingTimer = window.setInterval(() => {
      this.send({ type: MSG_PING, t: Date.now() });
    }, 5000);
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
      case "welcome": {
        this.joined = true;
        this.everJoined = true;
        this.inputSeq = 0; // fresh connection: restart the input sequence
        this.handlers.onWelcome(frame);
        break;
      }
      case "snapshot":
        this.handlers.onSnapshot(frame);
        break;
      case "scenario_list":
        this.handlers.onScenarioList(frame.items);
        break;
      case "inventory_delta":
        this.handlers.onInventory(frame.inventory);
        break;
      case "craft_result":
        // Server verdict for craft_op (previously silently dropped): success
        // toast + the inventory_delta that follows refreshes the bag grid.
        this.handlers.onCraftResult(frame.ok, frame.reason, frame.item_id, frame.qty);
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
      case "pong": {
        // RTT measurement: the server echoes the ping's Date.now() back, so
        // pong.t - now = full round trip. EMA smooths jitter; feeds the
        // reconciliation echoSlack (input transit = half RTT).
        const sent = frame.t as number;
        if (typeof sent === "number" && sent > 0) {
          const rtt = Date.now() - sent;
          if (rtt > 0 && rtt < 10000) {
            this.lastRttMs = this.lastRttMs > 0 ? this.lastRttMs * 0.7 + rtt * 0.3 : rtt;
            this.handlers.onRtt?.(this.lastRttMs);
          }
        }
        break;
      }
      case "action_result":
        this.handlers.onActionResult(frame);
        break;
      case "held":
        this.handlers.onHeld(frame.slot as number, (frame.item_id as string | null) ?? null);
        break;
    }
  }
}
