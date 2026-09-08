/**
 * NexNode relay: fronts browser WebSockets for the web client and tunnels
 * every frame to the bot over ONE bot-dialed outbound socket.
 *
 * Why: the bot's hosting panel has no openable public inbound port, so the
 * bot dials OUT to this relay (ws /bot, header X-Relay-Token). Browsers
 * connect here (wss via the platform's TLS) and speak the exact frames the
 * bot's WebHub understands — the relay is a pure pipe, zero game logic.
 *
 * Env: RELAY_TOKEN (shared secret, optional), PORT (default 8787).
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer, WebSocket } from "ws";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Config: env vars first; falls back to relay-config.json next to relay.js
// (panels without an env UI — the file is editable via SFTP/file manager).
// client_id is PUBLIC by design; never put a bot token in the config file.
function loadFileConfig() {
  try {
    return JSON.parse(fs.readFileSync(path.join(__dirname, "relay-config.json"), "utf8"));
  } catch {
    return {};
  }
}
const fileCfg = loadFileConfig();
const cfg = (key) => process.env[key] ?? fileCfg[key] ?? "";

// Hardcoded fallback for panels with no env UI at all (values are NOT
// secrets: client_id is public by design; RELAY_TOKEN here matches the
// bot's .env — rotate both together if it ever leaks).
const HARDCODED = {
  RELAY_TOKEN: "bd3b9c716b74a4abc782a0166de9f9959af83170e00f0493",
  DISCORD_OAUTH_CLIENT_ID: "965153822861307914",
  WEB_REDIRECT_URI: "https://rcatmmo.nexnodesite.xyz/",
};

const PORT = process.env.PORT || cfg("PORT") || 8787;
const RELAY_TOKEN = cfg("RELAY_TOKEN") || HARDCODED.RELAY_TOKEN;
const DISCORD_OAUTH_CLIENT_ID =
  cfg("DISCORD_OAUTH_CLIENT_ID") || HARDCODED.DISCORD_OAUTH_CLIENT_ID;
const WEB_REDIRECT_URI = cfg("WEB_REDIRECT_URI") || HARDCODED.WEB_REDIRECT_URI;
const DIST_DIR = path.join(__dirname, "dist");

// ---- state ----
let botSocket = null;          // the bot's outbound connection
let nextCid = 1;               // per-browser connection id
const browsers = new Map();    // cid -> WebSocket
// Frames buffered while the bot reconnects (bounded, newest dropped first).
let pendingToBot = [];
const PENDING_MAX = 500;

// ---- static file serving (the built web client) ----
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".css": "text/css",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".ico": "image/x-icon",
};

function serveStatic(req, res) {
  // /config.json is generated (client_id is public by design).
  if (req.url === "/config.json") {
    res.writeHead(200, {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    });
    res.end(JSON.stringify({
      client_id: DISCORD_OAUTH_CLIENT_ID,
      redirect_uri: WEB_REDIRECT_URI || null,
    }));
    return;
  }
  let file = req.url === "/" ? "/index.html" : req.url.split("?")[0];
  // Hashed asset filenames are safe; still, basename-confine everything.
  const resolved = path.join(DIST_DIR, path.normalize(file).replace(/^(\.\.[/\\])+/, ""));
  if (!resolved.startsWith(DIST_DIR) || !fs.existsSync(resolved) || fs.statSync(resolved).isDirectory()) {
    // SPA fallback: unknown paths get the shell (client routes by hash/URL).
    const shell = path.join(DIST_DIR, "index.html");
    if (fs.existsSync(shell)) {
      res.writeHead(200, { "Content-Type": MIME[".html"] });
      res.end(fs.readFileSync(shell));
    } else {
      res.writeHead(404).end();
    }
    return;
  }
  res.writeHead(200, { "Content-Type": MIME[path.extname(resolved)] || "application/octet-stream" });
  res.end(fs.readFileSync(resolved));
}

function toBot(envelope) {
  if (botSocket && botSocket.readyState === WebSocket.OPEN) {
    botSocket.send(JSON.stringify(envelope));
    return true;
  }
  if (pendingToBot.length >= PENDING_MAX) pendingToBot.shift();
  pendingToBot.push(envelope);
  return false;
}

function toBrowser(cid, envelope) {
  const ws = browsers.get(cid);
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(envelope.frame ?? envelope));
  }
}

// ---- HTTP + browser WS server ----
const server = http.createServer(serveStatic);
// Both endpoints use noServer: the shared upgrade handler routes by path
// (ws's own server-attached listener would destroy /bot upgrades first).
const wss = new WebSocketServer({ noServer: true });
const botWss = new WebSocketServer({ noServer: true });

wss.on("connection", (ws) => {
  const cid = nextCid++;
  browsers.set(cid, ws);
  toBot({ type: "client_connected", cid });
  ws.on("message", (data) => {
    // Browser frames arrive raw; wrap with the cid for the bot.
    try {
      const frame = JSON.parse(data.toString());
      toBot({ cid, frame });
    } catch {
      /* malformed frame: drop */
    }
  });
  ws.on("close", () => {
    browsers.delete(cid);
    toBot({ type: "client_gone", cid });
  });
});

server.on("upgrade", (req, socket, head) => {
  const { pathname } = new URL(req.url, "http://localhost");
  if (pathname === "/bot") {
    if (RELAY_TOKEN && req.headers["x-relay-token"] !== RELAY_TOKEN) {
      socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
      socket.destroy();
      return;
    }
    botWss.handleUpgrade(req, socket, head, (ws) => {
      // One bot socket at a time: the newest wins, the old one dies.
      if (botSocket && botSocket.readyState === WebSocket.OPEN) botSocket.close();
      botSocket = ws;
      console.log("[relay] bot connected");
      // Flush anything buffered while the bot was away.
      const queued = pendingToBot.splice(0);
      for (const envelope of queued) {
        try { ws.send(JSON.stringify(envelope)); } catch { /* socket died */ }
      }
      ws.on("message", (data) => {
        // Bot envelopes: {cid, frame} to a browser, or {cid, type:"asset_data"...}.
        try {
          const envelope = JSON.parse(data.toString());
          if (typeof envelope.cid === "number") toBrowser(envelope.cid, envelope);
        } catch { /* malformed: drop */ }
      });
      ws.on("close", () => {
        if (botSocket === ws) {
          botSocket = null;
          console.log("[relay] bot disconnected");
        }
      });
    });
    return;
  }
  if (pathname === "/ws") {
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req));
    return;
  }
  socket.destroy();
});

server.listen(PORT, () => {
  console.log(`[relay] listening on :${PORT} (bot: /bot, web: /ws)`);
});
