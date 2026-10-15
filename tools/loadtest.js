// HookLine multiplayer load test: N fake anglers sail around and send presence like the game does.
// node tools/loadtest.js ws://localhost:8790 200 20     (url, players, seconds)
// Prints, per second: messages and bytes each player receives, and how late peer updates arrive.
const WebSocket = require('ws');
const [url = 'ws://localhost:8790', N = '100', SECS = '20', V = '2', MOVERS = '1'] = process.argv.slice(2);   // V=1: old full-list protocol; MOVERS: share of boats moving (fishing boats sit still)
const n = +N, secs = +SECS;
let c_moving = true, rxBytes = 0, rxMsgs = 0, open = 0, lagSum = 0, lagN = 0;
const clients = [];
for (let i = 0; i < n; i++) setTimeout(() => {
  const ws = new WebSocket(url + '/?room=lobby' + (V === '2' ? '&v=2' : '')); c_moving = Math.random() < +MOVERS; const c = { moving: c_moving, ws, x: Math.random() * 200 - 100, z: Math.random() * 200 - 100, h: Math.random() * 6.28 };
  ws.on('open', () => { open++; });
  ws.on('message', d => { rxBytes += d.length; rxMsgs++; try { const m = JSON.parse(d); const ps = m.peers || (m.d && m.d.map(x => ({ presence: x[1] }))) || (m.t === 'snap' ? m.peers.map(x => ({ presence: x[1] })) : []); for (const p of ps) if (p.presence && p.presence.ts) { lagSum += Date.now() - p.presence.ts; lagN++; } } catch (_) { } });
  ws.on('error', () => { });
  c.timer = setInterval(() => {
    if (ws.readyState !== 1) return;
    if (c.moving) { c.h += (Math.random() - 0.5) * 0.3; c.x += Math.cos(c.h) * 0.8; c.z += Math.sin(c.h) * 0.8; } else if (c.sentOnce && Math.random() > 0.04) return; c.sentOnce = true;
    ws.send(JSON.stringify({ t: 'presence', p: { rid: 1, rs: 0.0123, cr: 0, em: null, zn: 'open', b: 'boat', x: +c.x.toFixed(1), z: +c.z.toFixed(1), h: +c.h.toFixed(2), v: c.moving ? 6.2 : 0, c: '#2f6fc0', n: 'Angler ' + i, st: 'idle', mk: null, sd: 0, lv: 0, pnl: 0, fx: null, fz: null, ses: 0, vo: null, sk: 0, ev: null, ts: c.moving ? Date.now() : undefined } }));
  }, 120);
  clients.push(c);
}, i * 8);
let t = 0;
const iv = setInterval(() => {
  t++;
  console.log(`t=${t}s open=${open} per-player: ${(rxMsgs / Math.max(1, open)).toFixed(1)} msg/s, ${(rxBytes / Math.max(1, open) / 1024).toFixed(1)} KB/s · total egress ${(rxBytes / 1048576).toFixed(2)} MB/s · update lag ${lagN ? (lagSum / lagN).toFixed(0) : '-'} ms`);
  rxBytes = 0; rxMsgs = 0; lagSum = 0; lagN = 0;
  if (t >= secs) { clearInterval(iv); for (const c of clients) { clearInterval(c.timer); c.ws.close(); } setTimeout(() => process.exit(0), 300); }
}, 1000);
