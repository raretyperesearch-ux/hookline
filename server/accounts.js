// HookLine accounts + economy authority.
// Every player has an account: guests get one the moment they open the game; signing in with Privy
// verifies Privy's access token (ES256 JWT) and keeps the guest's progress on the Privy account.
// AX, gear, the cooler and bait only change here, through game/economy.js (the same rulebook the
// browser uses to predict): the browser's copy is a cache, the server's answer is the truth.
// Storage: one JSON file (atomic rewrite) plus an append-only ledger of every AX movement, under
// DATA_DIR (a Railway volume in production).
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const ECON = require('../game/economy.js');

const DATA_DIR = path.resolve(process.env.DATA_DIR || path.join(__dirname, '..', 'data'));
const DB_FILE = path.join(DATA_DIR, 'hookline.json');
const LEDGER_FILE = path.join(DATA_DIR, 'ledger.jsonl');
const MODE = process.env.ECONOMY_MODE === 'live' ? 'live' : 'demo';   // live: only verified Lighter trades land sellable fish
const PRIVY_APP_ID = process.env.PRIVY_APP_ID || '';
const ADMIN_TOKEN = process.env.ADMIN_TOKEN || '';

/* ---------- storage ---------- */
fs.mkdirSync(DATA_DIR, { recursive: true });
let db;
try { db = JSON.parse(fs.readFileSync(DB_FILE, 'utf8')); } catch (_) { db = null; }
if (!db || !db.accounts) db = { v: 1, accounts: {}, sessions: {}, privy: {}, treasury: null };
if (!db.treasury) db.treasury = { ax: +process.env.TREASURY_AX || 50000, in: 0, out: 0 };
let saveT = null;
function save() {
  if (saveT) return;
  saveT = setTimeout(() => {
    saveT = null; const tmp = DB_FILE + '.tmp';
    try { fs.writeFileSync(tmp, JSON.stringify(db)); fs.renameSync(tmp, DB_FILE); } catch (e) { console.error('[accounts] save failed:', e.message); }
  }, 400);
}
function flush() { if (saveT) { clearTimeout(saveT); saveT = null; } try { fs.writeFileSync(DB_FILE + '.tmp', JSON.stringify(db)); fs.renameSync(DB_FILE + '.tmp', DB_FILE); } catch (_) { } }
process.on('SIGTERM', () => { flush(); process.exit(0); });
process.on('SIGINT', () => { flush(); process.exit(0); });
const ledger = row => fs.appendFile(LEDGER_FILE, JSON.stringify({ t: Date.now(), ...row }) + '\n', () => { });

/* ---------- helpers ---------- */
const sha = s => crypto.createHash('sha256').update(s).digest('hex');
const newId = p => p + crypto.randomBytes(9).toString('base64url');
const sendJson = (res, code, obj) => { res.writeHead(code, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(obj)); };
const clientIp = req => String(req.headers['x-forwarded-for'] || '').split(',')[0].trim() || req.socket.remoteAddress || '?';
function readBody(req, limit = 64 * 1024) {
  return new Promise((resolve, reject) => {
    let n = 0; const chunks = [];
    req.on('data', c => { n += c.length; if (n > limit) { reject(Object.assign(new Error('body too large'), { code: 413 })); req.destroy(); } else chunks.push(c); });
    req.on('end', () => { if (!n) return resolve({}); try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); } catch (_) { reject(Object.assign(new Error('bad json'), { code: 400 })); } });
    req.on('error', reject);
  });
}
// token buckets: per account for economy calls, per IP for new guests
const buckets = new Map();
function allow(key, cap, perSec) {
  const now = Date.now(), b = buckets.get(key) || { t: now, n: cap };
  b.n = Math.min(cap, b.n + (now - b.t) / 1000 * perSec); b.t = now;
  if (b.n < 1) { buckets.set(key, b); return false; } b.n -= 1; buckets.set(key, b);
  if (buckets.size > 20000) for (const [k, v] of buckets) if (now - v.t > 600e3) buckets.delete(k);
  return true;
}

/* ---------- accounts ---------- */
const PROFILE_KEYS = ['nick', 'coat', 'lvl', 'xp', 'tut', 'dex', 'quests', 'qday', 'trophies', 'best', 'stop', 'trail', 'tp', 'bait', 'voiceOff', 'strikeTip', 'towTip', 'reelTip', 'tips', 'hintSeen', 'paper', 'usd', 'real', 'onboarded', 'seedPos'];
const cleanNick = n => String(n || '').replace(/[^\p{L}\p{N} '._-]/gu, '').trim().slice(0, 18);
const cleanCoat = c => /^#[0-9a-f]{6}$/i.test(String(c || '')) ? String(c) : null;
function cleanProfile(p) {
  const out = {}; if (!p || typeof p !== 'object') return out;
  for (const k of PROFILE_KEYS) if (p[k] !== undefined) out[k] = p[k];
  if (out.nick !== undefined) out.nick = cleanNick(out.nick);
  if (out.coat !== undefined) out.coat = cleanCoat(out.coat);
  if (out.lvl !== undefined) out.lvl = Math.max(1, Math.min(99, Math.floor(+out.lvl || 1)));
  if (JSON.stringify(out).length > 48 * 1024) return {};
  return out;
}
// a guest arriving with an older browser save keeps it, within reason (demo economy only)
function importEcon(e) {
  const s = ECON.fresh(); if (!e || typeof e !== 'object' || MODE === 'live') return s;
  const ids = new Set([...ECON.RODS, ...ECON.KNIVES, ...ECON.NETS, ...ECON.CHESTS, ...ECON.BOATS].map(x => x.id));
  s.ax = Math.max(0, Math.min(5000, Math.floor(+e.ax || 0)));
  for (const id of Object.keys(e.owned || {})) if (ids.has(id)) s.owned[id] = 1;
  for (const [slot, id] of Object.entries(e.eq || {})) if (s.owned[id]) s.eq[slot] = id;
  s.sonar = Math.max(0, Math.min(3, Math.floor(+e.sonar || 0))); s.lures = !!e.lures;
  for (const [k, v] of Object.entries(e.charges || {})) if (ids.has(k)) s.charges[k] = v === null || v === Infinity || v < 0 ? -1 : Math.min(50, Math.floor(+v || 0));
  s.scraps = Math.max(0, Math.min(50, Math.floor(+e.scraps || 0)));
  for (const b of ECON.BAITS) if (e.baits && e.baits[b.id] > 0) s.baits[b.id] = Math.min(20, Math.floor(e.baits[b.id]));
  if (e.baitEq && s.baits[e.baitEq]) s.baitEq = e.baitEq;
  if (Array.isArray(e.cooler)) for (const f of e.cooler.slice(0, ECON.COOLER_MAX)) {
    if (!f || !(f.mk in ECON.RARITY)) continue;
    s.cooler.push({ id: newId('f'), mk: f.mk, side: f.side > 0 ? 1 : -1, lev: Math.max(1, Math.min(50, +f.lev || 1)), pct: Math.max(0, Math.min(5, +f.pct || 0)), usd: Math.max(0, Math.min(500, +f.usd || 0)), kg: Math.max(0, Math.min(9999, +f.kg || 0)), shiny: !!f.shiny, bait: f.bait || null, real: false, verified: false, t: +f.t || Date.now() });
  }
  return s;
}
function createAccount(kind, extra = {}) {
  const id = newId('a'); const acc = { id, kind, created: Date.now(), seen: Date.now(), nick: '', coat: null, econ: ECON.fresh(), profile: {}, ...extra };
  db.accounts[id] = acc; save(); return acc;
}
function newSession(acc) { const token = crypto.randomBytes(32).toString('base64url'); db.sessions[sha(token)] = { acc: acc.id, created: Date.now(), seen: Date.now() }; save(); return token; }
function authed(req) {
  const h = String(req.headers.authorization || ''); const m = /^Bearer\s+(.+)$/.exec(h); if (!m) return null;
  const s = db.sessions[sha(m[1].trim())]; if (!s) return null; const acc = db.accounts[s.acc]; if (!acc) return null;
  const now = Date.now(); if (now - s.seen > 60e3) { s.seen = now; acc.seen = now; save(); }
  return acc;
}
// what the browser gets back: knife charges use -1 for "never dulls" (JSON has no Infinity)
const view = acc => ({ id: acc.id, kind: acc.kind, nick: acc.nick || acc.profile.nick || '', coat: acc.coat || acc.profile.coat || null, email: acc.email || null, econ: ECON.pick(ECON.normalize(acc.econ)), profile: acc.profile, mode: MODE });
const publicTreasury = () => ({ ax: Math.floor(db.treasury.ax), in: Math.floor(db.treasury.in), out: Math.floor(db.treasury.out), buyback: ECON.DAILY_BUYBACK });

/* ---------- Privy: verify the access token the browser got at sign-in ---------- */
let jwks = null, spki = null, jose = null;
async function verifyPrivy(token) {
  if (!PRIVY_APP_ID) throw Object.assign(new Error('Privy is not configured on the server (PRIVY_APP_ID).'), { code: 501 });
  jose = jose || require('jose');
  let key;
  if (process.env.PRIVY_VERIFICATION_KEY) key = spki || (spki = await jose.importSPKI(process.env.PRIVY_VERIFICATION_KEY.replace(/\\n/g, '\n'), 'ES256'));
  else key = jwks || (jwks = jose.createRemoteJWKSet(new URL(`https://auth.privy.io/api/v1/apps/${PRIVY_APP_ID}/jwks.json`)));
  const { payload } = await jose.jwtVerify(token, key, { issuer: 'privy.io', audience: PRIVY_APP_ID });
  if (!payload.sub) throw new Error('token has no subject');
  return payload;
}

/* ---------- routes ---------- */
async function handle(req, res, url) {
  const p = url.pathname, method = req.method;
  try {
    // public: how full the Harbor Treasury is
    if (p === '/api/economy' && method === 'GET') return sendJson(res, 200, { ok: true, mode: MODE, treasury: publicTreasury() }), true;

    // start or resume: a valid session token resumes; otherwise a new guest account (keeping an old browser save if sent)
    if (p === '/api/session' && method === 'POST') {
      const body = await readBody(req); const have = authed(req);
      if (have) return sendJson(res, 200, { ok: true, account: view(have), treasury: publicTreasury() }), true;
      if (!allow('guest:' + clientIp(req), 30, 30 / 3600)) return sendJson(res, 429, { ok: false, error: 'too many new accounts from this network, try again later' }), true;
      const acc = createAccount('guest', { econ: importEcon(body.import), profile: cleanProfile(body.profile) });
      acc.nick = cleanNick(acc.profile.nick); acc.coat = cleanCoat(acc.profile.coat);
      const token = newSession(acc); ledger({ acc: acc.id, a: 'create', ax: acc.econ.ax });
      return sendJson(res, 200, { ok: true, token, account: view(acc), treasury: publicTreasury() }), true;
    }

    // ops: top the treasury up from fee income (POST {add, note}, header x-admin-token)
    if (p === '/api/admin/treasury' && method === 'POST') {
      if (!ADMIN_TOKEN || req.headers['x-admin-token'] !== ADMIN_TOKEN) return sendJson(res, 403, { ok: false, error: 'forbidden' }), true;
      const body = await readBody(req); const add = Math.floor(+body.add || 0); if (!add) return sendJson(res, 400, { ok: false, error: 'add must be a number' }), true;
      db.treasury.ax += add; if (add > 0) db.treasury.in += add; save(); ledger({ a: 'treasury', d: add, note: String(body.note || '').slice(0, 200) });
      return sendJson(res, 200, { ok: true, treasury: publicTreasury() }), true;
    }
    const acc = authed(req);
    if (!acc) return sendJson(res, 401, { ok: false, error: 'no session' }), true;

    if (p === '/api/me' && method === 'GET') return sendJson(res, 200, { ok: true, account: view(acc), treasury: publicTreasury() }), true;

    // sign in with Privy: this guest becomes a Privy account, or (if that Privy user already has one) we switch to it
    if (p === '/api/auth/privy' && method === 'POST') {
      const body = await readBody(req);
      let claims; try { claims = await verifyPrivy(String(body.token || '')); } catch (e) { return sendJson(res, e.code || 401, { ok: false, error: e.code === 501 ? e.message : 'Privy sign-in could not be verified' }), true; }
      const did = claims.sub, email = typeof body.email === 'string' ? body.email.slice(0, 120) : null;
      const existing = db.privy[did] && db.accounts[db.privy[did]];
      if (existing && existing.id !== acc.id) {
        const token = newSession(existing); if (email) existing.email = email; save();
        return sendJson(res, 200, { ok: true, token, switched: true, account: view(existing), treasury: publicTreasury() }), true;
      }
      acc.kind = 'privy'; acc.privy = did; if (email) acc.email = email; db.privy[did] = acc.id; save(); ledger({ acc: acc.id, a: 'link', privy: did });
      return sendJson(res, 200, { ok: true, linked: true, account: view(acc), treasury: publicTreasury() }), true;
    }
    if (p === '/api/auth/logout' && method === 'POST') {
      const h = /^Bearer\s+(.+)$/.exec(String(req.headers.authorization || '')); if (h) delete db.sessions[sha(h[1].trim())]; save();
      return sendJson(res, 200, { ok: true }), true;
    }

    // profile: everything that is not money (name, coat, level, tutorial, Fishdex, settings)
    if (p === '/api/profile' && method === 'PUT') {
      const body = await readBody(req); const prof = cleanProfile(body.profile);
      Object.assign(acc.profile, prof); if (prof.nick) acc.nick = prof.nick; if (prof.coat) acc.coat = prof.coat; save();
      return sendJson(res, 200, { ok: true }), true;
    }

    // the economy: every AX movement goes through here
    if (p === '/api/econ' && method === 'POST') {
      if (!allow('econ:' + acc.id, 20, 8)) return sendJson(res, 429, { ok: false, error: 'slow down' }), true;
      const body = await readBody(req); const action = String(body.action || ''), args = body.args && typeof body.args === 'object' ? body.args : {};
      const before = acc.econ.ax, tBefore = db.treasury.ax;
      const r = ECON.apply(acc.econ, action, args, { now: Date.now(), lvl: acc.profile.lvl || 1, treasury: db.treasury, live: MODE === 'live', id: () => newId('f') });
      if (r.ok) {
        const paid = tBefore - db.treasury.ax; if (paid > 0) db.treasury.out += paid;
        if (acc.econ.ax !== before || paid) ledger({ acc: acc.id, a: action, d: acc.econ.ax - before, ax: acc.econ.ax, tr: paid ? -paid : undefined });
        save();
      }
      return sendJson(res, 200, { ok: r.ok, error: r.err, out: r.out, econ: ECON.pick(acc.econ), treasury: publicTreasury() }), true;
    }

    return false;
  } catch (e) {
    sendJson(res, e.code && e.code < 600 ? e.code : 500, { ok: false, error: e.code ? e.message : 'server error' }); if (!e.code) console.error('[accounts]', e);
    return true;
  }
}

module.exports = { handle, MODE, stats: () => ({ accounts: Object.keys(db.accounts).length, treasury: publicTreasury().ax }) };
