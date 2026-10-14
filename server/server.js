// HookLine multiplayer: a tiny presence relay with the same shape as claude.ai's room capability.
// Each client sends {t:'presence', p:{...}} (≤4 KB, ≤20/s); the server keeps the latest presence per
// connection and broadcasts the room's peer list ~15 times a second. Nothing is persisted.
const http = require('http');
const fs = require('fs');
const path = require('path');
const { WebSocketServer } = require('ws');

// the game itself is served from ../game so one service is the whole deployment
const GAME_DIR = path.resolve(process.env.GAME_DIR || path.join(__dirname, '..', 'game'));
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.json': 'application/json', '.webp': 'image/webp', '.png': 'image/png', '.css': 'text/css', '.svg': 'image/svg+xml', '.ico': 'image/x-icon' };
function serveStatic(req, res) {
  let p = decodeURIComponent((req.url || '/').split('?')[0]); if (p === '/' || p === '') p = '/index.html';
  const file = path.resolve(GAME_DIR, '.' + p);
  if (!file.startsWith(GAME_DIR + path.sep)) { res.writeHead(403); res.end(); return; }
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) { res.writeHead(404); res.end('not found'); return; }
    const ext = path.extname(file).toLowerCase();
    res.writeHead(200, { 'content-type': TYPES[ext] || 'application/octet-stream', 'cache-control': ext === '.html' ? 'no-cache' : 'public, max-age=86400', 'content-length': st.size });
    fs.createReadStream(file).pipe(res);
  });
}

const PORT = +process.env.PORT || 8787;
const MAX_ROOM = +process.env.MAX_ROOM || 40;          // boats per room before a new room opens
const ORIGINS = (process.env.ALLOWED_ORIGINS || '*').split(',').map(s => s.trim());

const rooms = new Map();   // name -> Set(client)
let seq = 1;

const server = http.createServer((req, res) => {
  if (req.url === '/health') { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ ok: true, rooms: rooms.size, players: [...rooms.values()].reduce((n, r) => n + r.size, 0) })); return; }
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
  const c = { ws, id: 'p' + (seq++), room, presence: null, last: 0, tokens: 20 };
  rooms.get(room).add(c);
  ws.send(JSON.stringify({ t: 'hello', id: c.id, room }));
  ws.on('message', buf => {
    const now = Date.now(); c.tokens = Math.min(20, c.tokens + (now - c.last) / 50); c.last = now;
    if (c.tokens < 1) return; c.tokens -= 1;               // ~20 msgs/s per player
    if (buf.length > 4096) return;
    let m; try { m = JSON.parse(buf); } catch { return; }
    if (m && m.t === 'presence' && m.p && typeof m.p === 'object') c.presence = m.p;
  });
  ws.isAlive = true; ws.on('pong', () => { ws.isAlive = true; });
  ws.on('close', () => { const r = rooms.get(c.room); if (r) { r.delete(c); if (!r.size) rooms.delete(c.room); } });
});

// broadcast each room's peers (everyone's latest presence) to everyone in it
setInterval(() => {
  for (const r of rooms.values()) {
    const peers = [...r].filter(c => c.presence).map(c => ({ peer: c.id, presence: c.presence }));
    for (const c of r) if (c.ws.readyState === 1) c.ws.send(JSON.stringify({ t: 'peers', you: c.id, peers }));
  }
}, 66);

// keep-alive: ping every 20s, drop sockets that stopped answering
setInterval(() => { for (const r of rooms.values()) for (const c of r) { if (!c.ws.isAlive) { c.ws.terminate(); continue; } c.ws.isAlive = false; try { c.ws.ping(); } catch (_) { } } }, 20000);

server.listen(PORT, () => console.log(`HookLine multiplayer on :${PORT}`));
