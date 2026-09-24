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
  onInventory: (inv: InventoryPayload, version?: number) => void;
  /** Craft-panel state sync (server material grid + parked result). */
  onCraftState?: (matGrid: [string, number][], result: { id: string; qty: number } | null) => void;
  onCraftResult: (ok: boolean, reason: string, itemId: string | null, qty: number) => void;
  onPush: (message: string, kind?: string) => void;
  /** Cross-player chat line (server broadcast, colored name). */
  onChat?: (uid: number, name: string, color: string, text: string) => void;
  /** Another player swung (attack/chop/break) — play their arm arc. */
  onRemoteSwing?: (uid: number, tx: number | null, ty: number | null) => void;
  onError: (code: string, message?: string) => void;
  onAssetData: (name: string, b64: string | null) => void;
  onLoginOk: (token: string, displayName: string, avatarUrl: string) => void;
  onLoginFail: (error: string) => void;
  onHeld: (slot: number, itemId: string | null) => void;
  onActionResult: (frame: { name: string; ok: boolean; reason: string; tx: number | null; ty: number | null; kind: string; target_id?: string | null; target_defeated?: boolean; needed: number | null; drops: [string, number][]; damage?: number; critical?: boolean; missed?: boolean }) => void;
  /** PRE-TRAVEL SIGNAL (travel_begin): the iris veil must close NOW, before
   *  the map-switch welcome lands. Optional — old servers never send it. */
  onTravelBegin?: (mapName: string, kind: string) => void;
  onConnectionChange: (connected: boolean) => void;
  /** Optional RTT report (EMA ms) after each pong — feeds reconciliation. */
  onRtt?: (rttMs: number) => void;
  /** Each seq'd input as it is flushed to the wire (replay buffer feed). */
  onSeqInput?: (seq: number, dx: number, dy: number, running: boolean) => void;
}

export class Net {
  private ws: WebSocket | null = null;
  /** Extra dev hook (preview panel status) — assigned from main.ts when
   *  ?preview=1; optional so normal clients never touch it. */
  onPreviewState?: (state: Record<string, unknown>) => void;
  private token = "";
  private joined = false;
  /** Monotonic input sequence (input-sequence reconciliation): every input
   * frame carries seq; the snapshot acks last_seq and the game scene replays
   * unacked inputs from its buffer. Reset per connection. */
  private inputSeq = 0;
  /** Current server map epoch (welcome/snapshot). Echoed on every input
   *  frame — see the map_epoch note in onMessage. */
  private mapEpoch = 0;
  /** Delivery callback for each seq'd input, so the scene can buffer exact
   * inputs for replay (set by main.ts right after construction). */
  onSeqInput: ((seq: number, dx: number, dy: number, running: boolean) => void) | null = null;
  /** Idle heartbeat: returns the CURRENT predicted position so the idle
   *  timer keeps reporting it (set from main.ts → scene.getSelfPos). */
  idlePosHook: (() => { x: number; y: number } | null) | null = null;
  private handlers: NetHandlers;
  private pendingInput = { dx: 0, dy: 0, running: false, dirty: false, px: 0, py: 0, hasPos: false };
  private inputTimer: number | null = null;
  private pingTimer: number | null = null;
  private lastRttMs = 0;
  onRtt: ((rttMs: number) => void) | null = null;
  /** Extra connection listener (lobby auto-refresh etc.); called after the
   * primary handler on every connect/disconnect. */
  onConnectionChangeExtra: ((connected: boolean) => void) | null = null;
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
        this.onConnectionChangeExtra?.(true);
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
        this.onConnectionChangeExtra?.(false);
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
    console.warn("[NET] scheduleReconnect in", delay, "ms (attempt", this.reconnectAttempt + ")");
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
    if (this.ws && this.ws.readyState === WebSocket.CONNECTING) {
      // A connect is already in flight (double watchdog tick, alt-tab +
      // interval racing). Aborting the socket here left joined=false with a
      // half-open socket and no join replay — the stuck-session bug.
      return;
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
    // Remember the exact redirect used for authorize — the token exchange
    // MUST echo it byte-for-byte or Discord returns 400 invalid_grant.
    sessionStorage.setItem("oauth_redirect", redirect);
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
      redirect_uri: sessionStorage.getItem("oauth_redirect") ?? location.origin + location.pathname,
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

  /** Web-only preview: join a solo runtime for any map in the catalog. */
  previewMap(mapId: string): void {
    this.lastChannel = ""; // not a Discord channel — skip auto-rejoin
    this.send({ type: "map_preview", map_id: mapId });
  }

  /** MAP-SWITCH INPUT RESET: a portal welcome arriving mid-walk must not
   *  let the stale held vector keep flowing to the NEW map's server body
   *  (the residual "vào hang còn lệch chút chút": scene cleared its own
   *  mirror but pendingInput kept re-sending the old direction until the
   *  next key event). Zero it + the next flush is an idle heartbeat. */
  resetInput(): void {
    const p = this.pendingInput;
    p.dx = 0; p.dy = 0; p.running = false; p.dirty = false;
    p.px = 0; p.py = 0; p.hasPos = false;
  }

  requestGuestJoin(guestId: string): void {
    this.send({ type: "guest_login", guest_id: guestId });
  }

  /** Silent session resume: replay a persisted token — the server answers
   * login_result (no OAuth) if the token is still live anywhere (memory
   * or the persistent SQLite registry). */
  resumeLogin(token: string): void {
    this.send({ type: "resume_login", token });
  }

  /** Tell the server which hotbar slot is held (tools resolve from it). */
  selectSlot(slot: number): void {
    this.send({ type: "select_slot", slot });
  }

  setInput(dx: number, dy: number, running: boolean, pos?: { x: number; y: number }): void {
    const p = this.pendingInput;
    p.dx = dx; p.dy = dy; p.running = running; p.dirty = true;
    if (pos) {
      p.px = pos.x; p.py = pos.y; p.hasPos = true;
    }
    if (this.inputTimer === null) {
      this.inputTimer = window.setInterval(() => this.flushInput(), INPUT_SEND_INTERVAL_MS) as unknown as number;
    }
  }

  private flushInput(): void {
    const p = this.pendingInput;
    if (!this.joined) return;
    // HELD-VECTOR RESEND: a key held down fires onVector exactly ONCE
    // (keydown; OS auto-repeat is filtered), so p.dirty was true for only
    // ONE flush per keypress and every following flush degraded to the
    // zero-vector heartbeat below. The server then saw dx=0 for 19 of 20
    // ticks (no integration, no sprint-stamina drain) AND the replay log
    // flooded with zero entries — each snapshot rewound the avatar to the
    // lagging server position and replayed NOTHING (the replay skips zero
    // vectors), losing the frames since the last flush: the bigmap
    // "lag lag, không mượt" stutter at up to 20 Hz.
    const heldMoving = p.dx !== 0 || p.dy !== 0;
    if (!p.dirty && !heldMoving) {
      // IDLE HEARTBEAT (the "hitbox bên kia" bug): only when the held
      // vector is genuinely ZERO (player stopped), keep reporting the
      // CURRENT predicted position on the same 20 Hz cadence so the server
      // body never drifts from the screen. Still fed to the scene's seq
      // mirror so its dt accounting stays exact (the scene chooses what to
      // log — zero entries carry no movement).
      const pos = this.idlePosHook?.();
      if (pos) {
        const seq = ++this.inputSeq;
        this.send({
          type: MSG_INPUT, seq, dx: 0, dy: 0, running: false,
          x: Math.round(pos.x * 1000) / 1000, y: Math.round(pos.y * 1000) / 1000,
          map_epoch: this.mapEpoch,
        });
        if (this.onSeqInput) this.onSeqInput(seq, 0, 0, false);
      }
      return;
    }
    p.dirty = false;
    const seq = ++this.inputSeq;
    // Fresh position EVERY flush (not the one sampled at keydown): the
    // server converges to this report, and a stale one would drag the body
    // backward between onVector events.
    const pos = this.idlePosHook?.();
    const px = pos ? pos.x : p.px;
    const py = pos ? pos.y : p.py;
    this.send({
      type: MSG_INPUT, seq, dx: p.dx, dy: p.dy, running: p.running,
      // Client-authoritative position piggybacks on every flushed input:
      // the server converges its body to this (speed-capped + collision-
      // checked), so a server time-integration desync can never diverge
      // from what the player sees on screen.
      ...(p.hasPos || pos
        ? { x: Math.round(px * 1000) / 1000, y: Math.round(py * 1000) / 1000 }
        : {}),
      map_epoch: this.mapEpoch,
    });
    // Hand the exact input to the scene's replay buffer (same seq the
    // server will ack) — every moving slice is now logged, so the
    // snapshot rewind+replay reconstructs the prediction exactly.
    if (this.onSeqInput) this.onSeqInput(seq, p.dx, p.dy, p.running);
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

  inventoryOp(op: "move_to" | "use" | "split" | "reorder" | "throw" | "purse_withdraw" | "purse_deposit",
              payload: { item_id?: string; slot?: number; qty?: number; order?: { id: string; qty: number }[]; direction?: string }): void {
    this.send({ type: MSG_INV_OP, op, ...payload });
  }

  /** Sync the craft material grid (server-authoritative bag<->grid delta). */
  craftMatSync(grid: { id: string; qty: number }[]): void {
    this.send({
      type: "craft_op",
      op: "mat_sync",
      grid: grid.filter((g) => g.qty > 0),
    });
  }

  /** Craft: send the LOCAL material grid's exact multiset once — the server
   *  validates against the real bag and consumes there. ``layout`` is the
   *  optional exact 3x3 placement [(id, col, row)] — when present the
   *  server matches the ARRANGEMENT (mirror allowed), Minecraft parity. */
  craftFromGrid(inputs?: { id: string; qty: number }[],
                layout?: { id: string; col: number; row: number }[]): void {
    this.send({
      type: "craft_op",
      op: "craft",
      inputs: inputs ?? [],
      ...(layout && layout.length > 0 ? { layout } : {}),
    });
  }

  /** Collect the parked craft result into the bag. ``slot`` targets a
   *  specific bag cell (drag-to-slot); null = first free slot (click). */
  craftCollect(slot: number | null = null): void {
    this.send({ type: "craft_op", op: "collect",
      ...(slot !== null ? { slot } : {}) });
  }

  /** Legacy recipe-id craft (Discord parity). */
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

  /** Escape hatch for dev tooling (preview panel): send a raw frame the
   *  production server ignores (preview_cmd) — no typed handler needed. */
  sendRaw(obj: Record<string, unknown>): void {
    this.send(obj);
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
          this.handlers.onLoginOk(frame.token, frame.display_name ?? "", (frame as { avatar_url?: string }).avatar_url ?? "");
        } else {
          this.handlers.onLoginFail(frame.error ?? "login_failed");
        }
        break;
      case "welcome": {
        this.joined = true;
        this.everJoined = true;
        // Welcome is not always a fresh connection: a portal step or
        // /khutraodoi re-sends it while the server session (and its input
        // seq counter) keeps running. Resuming from the server's handoff
        // avoids re-sending seqs the server already acked — restarting at 0
        // left the ack pinned high, disabled the replay reconcile and
        // desynced the client permanently after every map switch.
        this.inputSeq = (frame as { input_seq?: number }).input_seq ?? 0;
        // MAP EPOCH: remember the current epoch from welcome + snapshots;
        // every input frame echoes it. After a map switch the server drops
        // position reports until a frame with the NEW epoch arrives — so a
        // stale bigmap report can never drag the fresh cave arrival body
        // across the map (the "vào hang lại đáp lên đỉnh" bug).
        const wep = (frame as { map_epoch?: number }).map_epoch;
        if (typeof wep === "number") this.mapEpoch = wep;
        this.handlers.onWelcome(frame);
        break;
      }
      case "snapshot": {
        // Keep the epoch fresh mid-session (a portal switch bumps it; the
        // next snapshot carries the new value even before a welcome lands).
        const sep = (frame as { self?: { map_epoch?: number } }).self?.map_epoch;
        if (typeof sep === "number") this.mapEpoch = sep;
        this.handlers.onSnapshot(frame);
        break;
      }
      case "scenario_list":
        this.handlers.onScenarioList(frame.items);
        break;
      case "inventory_delta":
        this.handlers.onInventory(frame.inventory, frame.inv_version);
        if (frame.mat_grid || frame.craft_result !== undefined) {
          this.handlers.onCraftState?.(frame.mat_grid ?? [], frame.craft_result ?? null);
        }
        break;
      case "craft_result":
        // Server verdict for craft_op (previously silently dropped): success
        // toast + the inventory_delta that follows refreshes the bag grid.
        // DIAG (temporary): the verdict as the browser sees it.
        console.log("[CRAFT] verdict", frame.ok, frame.reason, frame.item_id, frame.qty);
        this.handlers.onCraftResult(frame.ok, frame.reason, frame.item_id, frame.qty);
        break;
      case "push":
        this.handlers.onPush(frame.message, frame.kind);
        break;
      case "preview_state":
        this.onPreviewState?.(frame as unknown as Record<string, unknown>);
        break;
      case "chat":
        this.handlers.onChat?.(frame.uid, frame.name, frame.color, frame.text);
        break;
      case "swing":
        this.handlers.onRemoteSwing?.(frame.uid, frame.tx, frame.ty);
        break;
      case "error":
        this.handlers.onError(frame.code, frame.message);
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
      case "travel_begin":
        // Optional hook — veil only reacts when the client wired it.
        this.handlers.onTravelBegin?.(
          (frame.map_name as string | undefined) ?? "",
          (frame.kind as string | undefined) ?? "travel",
        );
        break;
    }
  }
}
