// HookLine multiplayer: a tiny presence relay with the same shape as claude.ai's room capability.
// Each client sends {t:'presence', p:{...}} (≤4 KB, ≤20/s); the server keeps the latest presence per
// connection and broadcasts the room's peer list ~15 times a second. Nothing is persisted.
const http = require('http');
const fs = require('fs');
const path = require('path');
const { WebSocketServer } = require('ws');
const accounts = require('./accounts');

// the game itself is served from ../game so one service is the whole deployment
const GAME_DIR = path.resolve(process.env.GAME_DIR || path.join(__dirname, '..', 'game'));
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.json': 'application/json', '.webp': 'image/webp', '.png': 'image/png', '.css': 'text/css', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.wasm': 'application/wasm', '.gz': 'application/gzip', '.glb': 'model/gltf-binary', '.mp3': 'audio/mpeg', '.woff2': 'font/woff2' };
// text and models go out gzipped (compressed once, kept in memory until the file changes)
const zlib = require('zlib');
const GZ_TYPES = new Set(['.html', '.js', '.json', '.css', '.svg', '.glb', '.wasm']);
const gzCache = new Map();   // file -> { mtime, buf }
function serveStatic(req, res) {
  let p = decodeURIComponent((req.url || '/').split('?')[0]); if (p === '/' || p === '') p = '/index.html';
  const file = path.resolve(GAME_DIR, '.' + p);
  if (!file.startsWith(GAME_DIR + path.sep)) { res.writeHead(403); res.end(); return; }
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) { res.writeHead(404); res.end('not found'); return; }
    const ext = path.extname(file).toLowerCase();
    const head = { 'content-type': TYPES[ext] || 'application/octet-stream', 'cache-control': ext === '.html' ? 'no-cache' : 'public, max-age=86400', vary: 'accept-encoding' };
    if (GZ_TYPES.has(ext) && st.size > 1024 && /\bgzip\b/.test(req.headers['accept-encoding'] || '')) {
      let c = gzCache.get(file);
      if (!c || c.mtime !== st.mtimeMs) { try { c = { mtime: st.mtimeMs, buf: zlib.gzipSync(fs.readFileSync(file), { level: 9 }) }; gzCache.set(file, c); } catch (_) { c = null; } }
      if (c) { res.writeHead(200, { ...head, 'content-encoding': 'gzip', 'content-length': c.buf.length }); res.end(req.method === 'HEAD' ? undefined : c.buf); return; }
    }
    res.writeHead(200, { ...head, 'content-length': st.size });
    if (req.method === 'HEAD') return res.end();
    fs.createReadStream(file).pipe(res);
  });
}

const PORT = +process.env.PORT || 8787;
const MAX_ROOM = +process.env.MAX_ROOM || 40;          // boats per room before a new room opens
const ORIGINS = (process.env.ALLOWED_ORIGINS || '*').split(',').map(s => s.trim());

const rooms = new Map();   // name -> Set(client)
let seq = 1;

/* ---- play for real: tiny Lighter helpers (the trading itself happens in the player's browser) ----
   GET /api/faucet?addr=0x…  TESTNET ONLY. Asks Lighter's testnet faucet to create + fund the account for
   this EVM address. Done here (not in the browser) so it can be rate limited and switched off. */
const LIGHTER_NET = process.env.LIGHTER_NET || 'testnet';
const LIGHTER_TESTNET_URL = process.env.LIGHTER_TESTNET_URL || 'https://testnet.zklighter.elliot.ai';
const FAUCET_PER_IP_HOUR = +process.env.FAUCET_PER_IP_HOUR || 6;
const faucetIp = new Map();     // ip -> [timestamps in the last hour]
const faucetAddr = new Map();   // address -> last request time
const sendJson = (res, code, obj) => { res.writeHead(code, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(obj)); };
const clientIp = req => String(req.headers['x-forwarded-for'] || '').split(',')[0].trim() || req.socket.remoteAddress || '?';
async function faucet(req, res, url) {
  if (LIGHTER_NET !== 'testnet') return sendJson(res, 404, { ok: false, error: 'faucet is testnet only' });
  const addr = String(url.searchParams.get('addr') || '');
  if (!/^0x[0-9a-fA-F]{40}$/.test(addr)) return sendJson(res, 400, { ok: false, error: 'bad address' });
  const now = Date.now(), ip = clientIp(req), key = addr.toLowerCase();
  const hits = (faucetIp.get(ip) || []).filter(t => now - t < 3600e3);
  if (hits.length >= FAUCET_PER_IP_HOUR) return sendJson(res, 429, { ok: false, error: 'faucet limit reached, try again later' });
  if (now - (faucetAddr.get(key) || 0) < 60e3) return sendJson(res, 429, { ok: false, error: 'faucet already asked for this wallet a moment ago' });
  hits.push(now); faucetIp.set(ip, hits); faucetAddr.set(key, now);
  if (faucetIp.size > 5000) for (const [k, v] of faucetIp) if (now - v[v.length - 1] > 3600e3) faucetIp.delete(k);
  if (faucetAddr.size > 5000) for (const [k, t] of faucetAddr) if (now - t > 60e3) faucetAddr.delete(k);
  let last = 'no answer';
  for (let i = 0; i < 3; i++) {      // Lighter's testnet faucet is undocumented and flaky: retry a little
    try {
      const r = await fetch(`${LIGHTER_TESTNET_URL}/api/v1/faucet?l1_address=${addr}`, { signal: AbortSignal.timeout(15000) });
      const j = await r.json().catch(() => ({}));
      if (r.ok && (j.code === 200 || j.code == null)) return sendJson(res, 200, { ok: true, message: j.message || 'ok' });
      last = j.message || `HTTP ${r.status}`;
      if (r.status < 500 && j.message) return sendJson(res, 200, { ok: false, error: 'testnet faucet: ' + j.message, code: j.code });   // a real answer: don't retry
    } catch (e) { last = e.message; }
    await new Promise(r => setTimeout(r, 1500 * (i + 1)));
  }
  sendJson(res, 502, { ok: false, error: 'testnet faucet: ' + last });
}
/* POST /api/deposit-address  MAINNET, NOT IMPLEMENTED YET.
   TODO(mainnet deposits): create a Lighter Universal Deposit Address for the player's EVM address with
   POST https://bridge.lighter.xyz/v1/uda (header x-api-key: LIGHTER_BUILDER_API_KEY, which must stay on this
   server), return the address(es) for the card/crypto on-ramp to pay into, and let the browser poll its
   Lighter balance as it does on testnet. Needs a Lighter builder key; until then this answers 501. */
function depositAddress(req, res) { sendJson(res, 501, { ok: false, error: 'mainnet deposits are not set up yet' }); }

const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/api/faucet' && req.method === 'GET') { faucet(req, res, url).catch(e => sendJson(res, 500, { ok: false, error: e.message })); return; }
    if (url.pathname === '/api/deposit-address' && req.method === 'POST') return depositAddress(req, res);
    accounts.handle(req, res, url).then(done => { if (!done) sendJson(res, 404, { ok: false, error: 'not found' }); });
    return;
  }
  if (req.url === '/health') { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ ok: true, rooms: rooms.size, players: [...rooms.values()].reduce((n, r) => n + r.size, 0), roomSizes: [...rooms.values()].map(r => r.size), netKBs: +(NET.rate || 0).toFixed(1), cpu: +(NET.cpu || 0).toFixed(1), memMB: Math.round(process.memoryUsage().rss / 1048576), ...accounts.stats() })); return; }
  // public runtime config for the page (the Privy app ID is public by design; secrets never go here)
  if (req.url === '/config.js') {
    const cfg = { HOOKLINE_PRIVY_APP_ID: process.env.PRIVY_APP_ID || '', HOOKLINE_PRIVY_CLIENT_ID: process.env.PRIVY_CLIENT_ID || '', HOOKLINE_LIGHTER_NET: process.env.LIGHTER_NET || 'testnet', HOOKLINE_ECONOMY_MODE: accounts.MODE };
    res.writeHead(200, { 'content-type': 'text/javascript', 'cache-control': 'no-cache' });
    res.end(Object.entries(cfg).map(([k, v]) => `window.${k} = window.${k} || ${JSON.stringify(v)};`).join('\n'));
    return;
  }
  if (req.method === 'GET' || req.method === 'HEAD') return serveStatic(req, res);
  res.writeHead(405); res.end();
});
const wss = new WebSocketServer({ server, maxPayload: 8192 });

function pickRoom(want) {
  const base = String(want || 'lobby').replace(/[^a-z0-9_-]/gi, '').slice(0, 32) || 'lobby';
  for (let i = 0; ; i++) { const name = i ? `${base}-${i}` : base; const r = rooms.get(name); if (!r || r.size < MAX_ROOM) return name; }
}

wss.on('connection', (ws, req) => {
  const origin = req.headers.origin || '';
  if (!ORIGINS.includes('*') && !ORIGINS.includes(origin)) { ws.close(1008, 'origin'); return; }
  const url = new URL(req.url, 'http://x');
  const room = pickRoom(url.searchParams.get('room'));
  if (!rooms.has(room)) rooms.set(room, new Set());
  const v2 = url.searchParams.get('v') === '2';
  const c = { ws, id: 'p' + (seq++), room, presence: null, sent: {}, dirty: false, v2, last: 0, tokens: 20 };
  const r0 = rooms.get(room); r0.add(c);
  ws.send(JSON.stringify({ t: 'hello', id: c.id, room }));
  // v2 clients get one full snapshot now, then only what changes
  if (v2) ws.send(JSON.stringify({ t: 'snap', you: c.id, peers: [...r0].filter(o => o.presence && o !== c).map(o => [o.id, o.presence]) }));
  ws.on('message', buf => {
    const now = Date.now(); c.tokens = Math.min(20, c.tokens + (now - c.last) / 50); c.last = now;
    if (c.tokens < 1) return; c.tokens -= 1;               // ~20 msgs/s per player
    if (buf.length > 4096) return;
    let m; try { m = JSON.parse(buf); } catch { return; }
    if (m && m.t === 'presence' && m.p && typeof m.p === 'object') { c.presence = m.p; c.dirty = true; }
  });
  ws.isAlive = true; ws.on('pong', () => { ws.isAlive = true; });
  ws.on('close', () => { const r = rooms.get(c.room); if (r) { r.delete(c); if (c.presence) (gone.get(c.room) || gone.set(c.room, []).get(c.room)).push(c.id); if (!r.size) { rooms.delete(c.room); gone.delete(c.room); } } });
});

// broadcast, 10 times a second. v2 clients: one shared message per room carrying only the fields that changed
// since the last tick ({t:'d', d:[[id, {changed keys}]], g:[ids that left]}); a key set to null was removed.
// Older pages (no ?v=2) still get the full list each tick.
const gone = new Map();   // room -> ids that left since the last tick
const same = (a, b) => a === b || (a && b && typeof a === 'object' && typeof b === 'object' && JSON.stringify(a) === JSON.stringify(b));
const NET = { ticks: 0, bytes: 0, msgs: 0 };
setInterval(() => {
  for (const [name, r] of rooms) {
    const d = [], left = gone.get(name) || [];
    for (const c of r) {
      if (!c.dirty || !c.presence) continue; c.dirty = false;
      const diff = {}; let any = false;
      for (const k in c.presence) if (!same(c.presence[k], c.sent[k])) { diff[k] = c.presence[k]; any = true; }
      for (const k in c.sent) if (!(k in c.presence)) { diff[k] = null; any = true; }
      if (any) { d.push([c.id, diff]); c.sent = { ...c.presence }; }
    }
    gone.delete(name);
    let msg = null, legacy = null;
    for (const c of r) {
      if (c.ws.readyState !== 1) continue;
      if (c.v2) { if (!d.length && !left.length) continue; msg = msg || JSON.stringify({ t: 'd', d, g: left.length ? left : undefined }); c.ws.send(msg); NET.bytes += msg.length; NET.msgs++; }
      else { legacy = legacy || [...r].filter(o => o.presence).map(o => ({ peer: o.id, presence: o.presence })); const s = JSON.stringify({ t: 'peers', you: c.id, peers: legacy }); c.ws.send(s); NET.bytes += s.length; NET.msgs++; }
    }
  }
  if (++NET.ticks % 50 === 0) { NET.rate = NET.bytes / 5 / 1024; NET.bytes = 0; const u = process.cpuUsage(), t = Date.now(); if (NET.cu) NET.cpu = ((u.user + u.system - NET.cu) / 1000) / (t - NET.ct) * 100; NET.cu = u.user + u.system; NET.ct = t; }
}, 100);

// keep-alive: ping every 20s, drop sockets that stopped answering
setInterval(() => { for (const r of rooms.values()) for (const c of r) { if (!c.ws.isAlive) { c.ws.terminate(); continue; } c.ws.isAlive = false; try { c.ws.ping(); } catch (_) { } } }, 20000);

server.listen(PORT, () => console.log(`HookLine multiplayer on :${PORT}`));
