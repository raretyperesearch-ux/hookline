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
const LV = require('./lighter-verify.js');

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
if (!db.market) db.market = { listings: [], sales: [] };
if (!db.analytics) db.analytics = { days: {} };
if (!db.wallets) db.wallets = {};
if (!db.refs) db.refs = {};   // referral code -> account id   // wallet address -> account id (one HookLine account per wallet)
if (!db.treasury) db.treasury = { ax: +process.env.TREASURY_AX || 50000, in: 0, out: 0 };
let saveT = null, writing = false, again = false;
// write the whole file at most every 1.5s, off the request path; a write in flight queues one more
function save() {
  if (saveT) return;
  saveT = setTimeout(() => {
    saveT = null; if (writing) { again = true; return; }
    writing = true; const tmp = DB_FILE + '.tmp';
    fs.writeFile(tmp, JSON.stringify(db), err => {
      if (err) { console.error('[accounts] save failed:', err.message); writing = false; return; }
      fs.rename(tmp, DB_FILE, err2 => { if (err2) console.error('[accounts] save failed:', err2.message); writing = false; if (again) { again = false; save(); } });
    });
  }, 1500);
}
function flush() { if (saveT) { clearTimeout(saveT); saveT = null; } try { fs.writeFileSync(DB_FILE + '.tmp', JSON.stringify(db)); fs.renameSync(DB_FILE + '.tmp', DB_FILE); } catch (_) { } }
process.on('SIGTERM', () => { flush(); process.exit(0); });
process.on('SIGINT', () => { flush(); process.exit(0); });
// daily compressed snapshots under DATA_DIR/backups, 14 kept; GET /api/admin/backup downloads one on demand
const zlib = require('zlib');
const BACKUP_DIR = path.join(DATA_DIR, 'backups'); fs.mkdirSync(BACKUP_DIR, { recursive: true });
function backup() {
  const f = path.join(BACKUP_DIR, `hookline-${new Date().toISOString().slice(0, 10)}.json.gz`);
  if (fs.existsSync(f)) return;
  zlib.gzip(JSON.stringify(db), (err, buf) => {
    if (err) return console.error('[accounts] backup failed:', err.message);
    fs.writeFile(f, buf, e => { if (e) return console.error('[accounts] backup failed:', e.message);
      const old = fs.readdirSync(BACKUP_DIR).filter(x => x.startsWith('hookline-')).sort().slice(0, -14); for (const o of old) fs.unlink(path.join(BACKUP_DIR, o), () => { }); });
  });
}
setTimeout(backup, 30e3); setInterval(backup, 3600e3);
// errors players hit, reported by the page (rate limited, small); GET /api/admin/errors lists the latest
const ERR_FILE = path.join(DATA_DIR, 'client-errors.jsonl'); const recentErrors = [];
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
const PROFILE_KEYS = ['nick', 'coat', 'lvl', 'xp', 'tut', 'dex', 'quests', 'qday', 'trophies', 'best', 'stop', 'trail', 'tp', 'bait', 'voiceOff', 'strikeTip', 'towTip', 'reelTip', 'tips', 'hintSeen', 'paper', 'usd', 'real', 'onboarded', 'seedPos', 'riskAck', 'gfx'];
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
const view = acc => ({ banned: !!acc.banned, referredBy: !!acc.referredBy, wallet: acc.wallet || null, lighter: acc.lighter || [], id: acc.id, kind: acc.kind, nick: acc.nick || acc.profile.nick || '', coat: acc.coat || acc.profile.coat || null, email: acc.email || null, econ: ECON.pick(ECON.normalize(acc.econ)), profile: acc.profile, mode: MODE });
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

/* ---------- player market: bait for AX, escrowed on the server ----------
   Listing takes the bait out of the seller's inventory; buying moves AX buyer -> seller minus the Harbor fee,
   which goes into the Treasury. Cancel or expiry gives the bait back. Sellers hear about sales in their inbox. */
const M = ECON.MARKET;
const baitOk = id => ECON.BAITS.some(b => b.id === id);
function inbox(acc, msg) { acc.inbox = acc.inbox || []; acc.inbox.push({ t: Date.now(), ...msg }); if (acc.inbox.length > 30) acc.inbox.splice(0, acc.inbox.length - 30); }
function expireListings() {
  const now = Date.now(); let changed = false;
  db.market.listings = db.market.listings.filter(l => {
    if (now - l.t < M.ttl) return true;
    const seller = db.accounts[l.seller]; if (seller) { ECON.normalize(seller.econ); seller.econ.baits[l.bait] = (seller.econ.baits[l.bait] || 0) + l.qty; inbox(seller, { k: 'expired', bait: l.bait, qty: l.qty }); }
    changed = true; return false;
  });
  if (changed) save();
}
function marketStats() {
  const out = {};
  for (const b of ECON.BAITS) {
    const sold = db.market.sales.filter(x => x.bait === b.id).slice(-20), ls = db.market.listings.filter(l => l.bait === b.id);
    out[b.id] = { avg: sold.length ? Math.round(sold.reduce((a, x) => a + x.price, 0) / sold.length) : null, last: sold.length ? sold[sold.length - 1].price : null, best: ls.length ? Math.min(...ls.map(l => l.price)) : null, qty: ls.reduce((a, l) => a + l.qty, 0) };
  }
  return out;
}
function marketView(acc) {
  expireListings();
  const all = db.market.listings.slice().sort((a, b) => a.price - b.price || a.t - b.t);
  const box = acc.inbox || []; acc.inbox = []; if (box.length) save();
  return { listings: all.filter(l => l.seller !== acc.id).slice(0, 60).map(l => ({ id: l.id, bait: l.bait, qty: l.qty, price: l.price, nick: l.nick, t: l.t })),
    mine: all.filter(l => l.seller === acc.id).map(l => ({ id: l.id, bait: l.bait, qty: l.qty, price: l.price, t: l.t, ends: l.t + M.ttl })), stats: marketStats(), inbox: box };
}
function marketList(acc, a) {
  const bait = String(a.bait || ''), qty = Math.floor(+a.qty || 0), price = Math.floor(+a.price || 0);
  if (!baitOk(bait)) return 'unknown bait';
  if (qty < 1 || qty > M.maxQty) return `list between 1 and ${M.maxQty}`;
  if (price < M.minPrice || price > M.maxPrice) return `price must be ${M.minPrice} to ${M.maxPrice} AX`;
  if (db.market.listings.filter(l => l.seller === acc.id).length >= M.maxListings) return `you can have ${M.maxListings} listings at once`;
  const e = ECON.normalize(acc.econ); if ((e.baits[bait] || 0) < qty) return 'you don\'t have that much bait';
  e.baits[bait] -= qty; if (!e.baits[bait] && e.baitEq === bait) e.baitEq = null;
  const l = { id: newId('l'), seller: acc.id, nick: acc.nick || acc.profile.nick || 'Angler', bait, qty, price, t: Date.now() };
  db.market.listings.push(l); save(); ledger({ acc: acc.id, a: 'list', bait, qty, price, l: l.id });
  return null;
}
function marketBuy(acc, a) {
  expireListings();
  const l = db.market.listings.find(x => x.id === a.id); if (!l) return 'that listing is gone';
  if (l.seller === acc.id) return 'that\'s your own listing';
  const qty = Math.max(1, Math.min(l.qty, Math.floor(+a.qty || 1))), cost = l.price * qty, e = ECON.normalize(acc.econ);
  if (e.ax < cost) return 'not enough AX';
  const seller = db.accounts[l.seller]; const gets = ECON.sellerGets(l.price, qty), fee = cost - gets;
  e.ax -= cost; e.baits[l.bait] = (e.baits[l.bait] || 0) + qty; if (!e.baitEq) e.baitEq = l.bait;
  if (seller) { ECON.normalize(seller.econ); seller.econ.ax += gets; inbox(seller, { k: 'sold', bait: l.bait, qty, price: l.price, ax: gets, buyer: acc.nick || 'an angler' }); }
  db.treasury.ax += fee; db.treasury.in += fee;
  l.qty -= qty; if (l.qty <= 0) db.market.listings = db.market.listings.filter(x => x !== l);
  db.market.sales.push({ bait: l.bait, price: l.price, qty, t: Date.now() }); if (db.market.sales.length > 500) db.market.sales.splice(0, db.market.sales.length - 500);
  save(); ledger({ acc: acc.id, a: 'mbuy', l: l.id, bait: l.bait, qty, d: -cost, ax: e.ax }); ledger({ acc: l.seller, a: 'msell', l: l.id, d: gets, fee });
  return null;
}
function marketCancel(acc, a) {
  const l = db.market.listings.find(x => x.id === a.id && x.seller === acc.id); if (!l) return 'that listing is gone';
  const e = ECON.normalize(acc.econ); e.baits[l.bait] = (e.baits[l.bait] || 0) + l.qty;
  db.market.listings = db.market.listings.filter(x => x !== l); save(); ledger({ acc: acc.id, a: 'unlist', l: l.id });
  return null;
}

/* ---------- analytics: first-party, aggregated, nothing leaves this server ----------
   Events count per day (total + unique accounts), each account keeps the first time it hit a milestone
   (the funnel) and the days it was active (retention). Names are whitelisted. */
const EVENTS = new Set(['open', 'onboard_login', 'onboard_guest', 'onboard_angler', 'onboard_rod', 'sail', 'cast', 'hook', 'perfect_hook', 'catch', 'cut', 'kraken',
  'sell', 'craft', 'buy_bait', 'buy_gear', 'market_list', 'market_buy', 'quest_claim', 'tab', 'signin', 'real_open', 'risk_ack', 'fund_start', 'real_trade', 'verified_catch', 'tutorial_done', 'error']);
const MILESTONES = ['open', 'onboard_angler', 'onboard_rod', 'sail', 'cast', 'hook', 'catch', 'sell', 'craft', 'market_buy', 'market_list', 'tutorial_done', 'signin', 'real_open', 'risk_ack', 'fund_start', 'real_trade'];
const dayKey = (t = Date.now()) => new Date(t).toISOString().slice(0, 10);
function track(acc, name, now = Date.now()) {
  if (!EVENTS.has(name)) return;
  const d = dayKey(now), A = db.analytics.days[d] || (db.analytics.days[d] = { ev: {}, users: {} });
  A.ev[name] = (A.ev[name] || 0) + 1;
  if (!acc) return;
  if (!acc.evDay || acc.evDay.d !== d) { acc.evDay = { d, seen: {} }; A.users.active = (A.users.active || 0) + 1; acc.days = acc.days || []; if (acc.days[acc.days.length - 1] !== d) { acc.days.push(d); if (acc.days.length > 120) acc.days.shift(); } }
  if (!acc.evDay.seen[name]) { acc.evDay.seen[name] = 1; A.users[name] = (A.users[name] || 0) + 1; }
  acc.ms = acc.ms || {}; if (MILESTONES.includes(name) && !acc.ms[name]) { acc.ms[name] = now; if (name === 'tutorial_done') payReferral(acc); }
}
setInterval(() => { const keep = Object.keys(db.analytics.days).sort().slice(-180); for (const k of Object.keys(db.analytics.days)) if (!keep.includes(k)) delete db.analytics.days[k]; }, 6 * 3600e3);
function adminStats(q) {
  const now = Date.now(), accs = Object.values(db.accounts), days = Object.keys(db.analytics.days).sort();
  const since = Math.max(1, Math.min(90, +q.get('days') || 30)), cut = now - since * 864e5;
  const recent = accs.filter(a => a.created >= cut);
  const activeIn = n => accs.filter(a => (a.days || []).some(d => Date.parse(d) >= now - n * 864e5)).length;
  // retention: of accounts created on day D, how many came back on D+1 / D+7
  const cohorts = [];
  for (let i = 14; i >= 1; i--) {
    const d = dayKey(now - i * 864e5), born = accs.filter(a => dayKey(a.created) === d); if (!born.length) continue;
    const back = k => { const t = dayKey(Date.parse(d) + k * 864e5); return Date.parse(t) > now ? null : born.filter(a => (a.days || []).includes(t)).length; };
    cohorts.push({ day: d, n: born.length, d1: back(1), d7: back(7) });
  }
  const econ = accs.map(a => ECON.normalize(a.econ));
  return {
    totals: { accounts: accs.length, signedIn: accs.filter(a => a.kind === 'privy').length, newInRange: recent.length, active1: activeIn(1), active7: activeIn(7), active30: activeIn(30), online: null },
    dau: Array.from({ length: since }, (_, i) => dayKey(now - (since - 1 - i) * 864e5)).map(d => { const A = db.analytics.days[d]; return { day: d, users: A ? (A.users.active || 0) : 0, events: A ? Object.values(A.ev).reduce((a, b) => a + b, 0) : 0 }; }),
    funnel: MILESTONES.map(m => ({ step: m, n: recent.filter(a => a.ms && a.ms[m]).length })),
    cohorts,
    today: Object.entries((db.analytics.days[dayKey(now)] || { users: {} }).users).sort((a, b) => b[1] - a[1]),
    economy: { treasury: publicTreasury(), axInCirculation: econ.reduce((t, e) => t + e.ax, 0), fishInCoolers: econ.reduce((t, e) => t + e.cooler.length, 0), baitHeld: econ.reduce((t, e) => t + Object.values(e.baits).reduce((x, y) => x + y, 0), 0), listings: db.market.listings.length, marketSales7d: db.market.sales.filter(x => now - x.t < 7 * 864e5).length, wallets: accs.filter(a => a.wallet).length, verifiedFish: accs.reduce((t, a) => t + (a.verifiedCatches || 0), 0) },
    errors: { lastDay: recentErrors.filter(e => now - e.t < 864e5).length, latest: recentErrors.slice(-5).reverse().map(e => ({ t: e.t, msg: e.msg, where: e.where })) },
  };
}

/* ---------- referrals: invite a friend, both get rewarded once the friend finishes the tutorial ----------
   The friend gets a Glitter Bait on joining and +100 AX at the tutorial; the inviter gets +150 AX and a Golden Grub.
   Paid from the Harbor Treasury. No reward when both come from the same network; at most 20 rewards per inviter per day. */
const REF = { joinBait: 'glitter', friendAx: 100, inviterAx: 150, inviterBait: 'grub', perDay: 20 };
const REF_ABC = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
function refCode(acc) {
  if (acc.ref && db.refs[acc.ref] === acc.id) return acc.ref;
  let c; do { c = Array.from(crypto.randomBytes(6), b => REF_ABC[b % REF_ABC.length]).join(''); } while (db.refs[c]);
  acc.ref = c; db.refs[c] = acc.id; save(); return c;
}
function payReferral(acc) {
  if (!acc.referredBy || acc.refPaid) return;
  const inviter = db.accounts[acc.referredBy]; acc.refPaid = Date.now();
  if (!inviter || inviter.banned) return;
  if (inviter.ipHash && acc.ipHash && inviter.ipHash === acc.ipHash) { acc.refPaid = 'same-network'; ledger({ acc: inviter.id, a: 'ref-skip', friend: acc.id, why: 'same network' }); return; }
  const day = dayKey(); inviter.refDay = inviter.refDay && inviter.refDay.d === day ? inviter.refDay : { d: day, n: 0 };
  if (inviter.refDay.n >= REF.perDay) { acc.refPaid = 'cap'; return; }
  inviter.refDay.n++;
  const fe = ECON.normalize(acc.econ), ie = ECON.normalize(inviter.econ);
  fe.ax += REF.friendAx; ie.ax += REF.inviterAx; ie.baits[REF.inviterBait] = (ie.baits[REF.inviterBait] || 0) + 1;
  db.treasury.ax -= REF.friendAx + REF.inviterAx; db.treasury.out += REF.friendAx + REF.inviterAx;
  inviter.refCount = (inviter.refCount || 0) + 1;
  inbox(inviter, { k: 'ref', who: acc.nick || 'A friend', ax: REF.inviterAx, bait: REF.inviterBait });
  inbox(acc, { k: 'refjoin', who: inviter.nick || 'your friend', ax: REF.friendAx });
  ledger({ acc: inviter.id, a: 'ref', friend: acc.id, d: REF.inviterAx, ax: ie.ax }); ledger({ acc: acc.id, a: 'ref-friend', d: REF.friendAx, ax: fe.ax });
  save();
}

/* ---------- admin: find players, look closer, fix things (every action goes in the ledger) ---------- */
const isAdmin = req => ADMIN_TOKEN && req.headers['x-admin-token'] === ADMIN_TOKEN;
const adminRow = a => ({ id: a.id, nick: a.nick || (a.profile && a.profile.nick) || '', kind: a.kind, email: a.email || null, wallet: a.wallet || null, ax: ECON.normalize(a.econ).ax, lvl: (a.profile && a.profile.lvl) || 1, created: a.created, seen: a.seen, banned: !!a.banned, refs: a.refCount || 0 });
function ledgerFor(id, max = 60) {
  try {
    const st = fs.statSync(LEDGER_FILE), len = Math.min(st.size, 4 * 1048576), fd = fs.openSync(LEDGER_FILE, 'r'), buf = Buffer.alloc(len);
    fs.readSync(fd, buf, 0, len, st.size - len); fs.closeSync(fd);
    return buf.toString('utf8').split('\n').filter(l => l.includes(id)).slice(-max).reverse().map(l => { try { return JSON.parse(l); } catch (_) { return null; } }).filter(Boolean);
  } catch (_) { return []; }
}
function adminAction(a, b) {
  const e = ECON.normalize(a.econ), note = String(b.note || '').slice(0, 200);
  switch (b.action) {
    case 'ax': { const d = Math.trunc(+b.amount || 0); if (!d) return 'amount must be a whole number'; if (e.ax + d < 0) return 'that would make their AX negative'; e.ax += d; ledger({ acc: a.id, a: 'admin-ax', d, ax: e.ax, note }); return null; }
    case 'bait': { const q = Math.trunc(+b.qty || 0); if (!ECON.BAITS.some(x => x.id === b.bait) || !q) return 'pick a bait and a quantity'; e.baits[b.bait] = Math.max(0, (e.baits[b.bait] || 0) + q); ledger({ acc: a.id, a: 'admin-bait', bait: b.bait, q, note }); return null; }
    case 'rename': { const n = cleanNick(b.nick); if (!n) return 'name is empty'; a.nick = n; a.profile.nick = n; ledger({ acc: a.id, a: 'admin-rename', nick: n, note }); return null; }
    case 'logout': { for (const [k, sv] of Object.entries(db.sessions)) if (sv.acc === a.id) delete db.sessions[k]; ledger({ acc: a.id, a: 'admin-logout', note }); return null; }
    case 'ban': case 'unban': {
      a.banned = b.action === 'ban' ? { t: Date.now(), note } : null;
      if (a.banned) for (const l of db.market.listings.filter(x => x.seller === a.id)) { e.baits[l.bait] = (e.baits[l.bait] || 0) + l.qty; db.market.listings = db.market.listings.filter(x => x !== l); }
      ledger({ acc: a.id, a: 'admin-' + b.action, note }); return null;
    }
    default: return 'unknown action';
  }
}

/* ---------- routes ---------- */
async function handle(req, res, url) {
  const p = url.pathname, method = req.method;
  try {
    if (p === '/api/client-error' && method === 'POST') {
      if (!allow('err:' + clientIp(req), 10, 10 / 60)) return sendJson(res, 429, { ok: false }), true;
      const b = await readBody(req, 8 * 1024); const row = { t: Date.now(), msg: String(b.msg || '').slice(0, 300), where: String(b.where || '').slice(0, 300), stack: String(b.stack || '').slice(0, 1500), ua: String(req.headers['user-agent'] || '').slice(0, 160), v: String(b.v || '').slice(0, 20) };
      recentErrors.push(row); if (recentErrors.length > 200) recentErrors.shift(); fs.appendFile(ERR_FILE, JSON.stringify(row) + '\n', () => { });
      return sendJson(res, 200, { ok: true }), true;
    }
    if (p === '/api/events' && method === 'POST') {
      if (!allow('ev:' + clientIp(req), 120, 2)) return sendJson(res, 429, { ok: false }), true;
      const b = await readBody(req, 16 * 1024), who = authed(req), list = Array.isArray(b.events) ? b.events.slice(0, 50) : [];
      for (const ev of list) if (ev && typeof ev.e === 'string') track(who, ev.e, Math.min(Date.now(), +ev.t || Date.now()));
      if (who) save();
      return sendJson(res, 200, { ok: true }), true;
    }
    if (p === '/api/admin/stats' && method === 'GET') {
      if (!ADMIN_TOKEN || req.headers['x-admin-token'] !== ADMIN_TOKEN) return sendJson(res, 403, { ok: false, error: 'forbidden' }), true;
      return sendJson(res, 200, { ok: true, ...adminStats(url.searchParams) }), true;
    }
    if ((p === '/api/admin/errors' || p === '/api/admin/backup') && method === 'GET') {
      if (!ADMIN_TOKEN || req.headers['x-admin-token'] !== ADMIN_TOKEN) return sendJson(res, 403, { ok: false, error: 'forbidden' }), true;
      if (p === '/api/admin/errors') return sendJson(res, 200, { ok: true, errors: recentErrors.slice(-100).reverse() }), true;
      const buf = zlib.gzipSync(JSON.stringify(db)); res.writeHead(200, { 'content-type': 'application/gzip', 'content-disposition': `attachment; filename="hookline-${new Date().toISOString().slice(0, 10)}.json.gz"`, 'content-length': buf.length }); res.end(buf); return true;
    }
    // public: how full the Harbor Treasury is
    if (p === '/api/economy' && method === 'GET') return sendJson(res, 200, { ok: true, mode: MODE, treasury: publicTreasury() }), true;

    // start or resume: a valid session token resumes; otherwise a new guest account (keeping an old browser save if sent)
    if (p === '/api/session' && method === 'POST') {
      const body = await readBody(req); const have = authed(req);
      if (have) return sendJson(res, 200, { ok: true, account: view(have), treasury: publicTreasury() }), true;
      if (!allow('guest:' + clientIp(req), 60, 60 / 3600)) return sendJson(res, 429, { ok: false, error: 'too many new accounts from this network, try again later' }), true;
      const acc = createAccount('guest', { econ: importEcon(body.import), profile: cleanProfile(body.profile), ipHash: sha('ip:' + clientIp(req)).slice(0, 16) });
      acc.nick = cleanNick(acc.profile.nick); acc.coat = cleanCoat(acc.profile.coat);
      const inviter = body.ref && db.refs[String(body.ref).toUpperCase()]; if (inviter && db.accounts[inviter]) { acc.referredBy = inviter; ECON.normalize(acc.econ).baits[REF.joinBait] = (acc.econ.baits[REF.joinBait] || 0) + 1; acc.econ.baitEq = acc.econ.baitEq || REF.joinBait; ledger({ acc: acc.id, a: 'ref-join', by: inviter }); }
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
    if (p.startsWith('/api/admin/')) {
      if (!isAdmin(req)) return sendJson(res, 403, { ok: false, error: 'forbidden' }), true;
      if (p === '/api/admin/players' && method === 'GET') {
        const q = String(url.searchParams.get('q') || '').trim().toLowerCase();
        let list = Object.values(db.accounts);
        if (q) list = list.filter(a => [a.id, a.nick, a.email, a.wallet, a.privy, a.ref].some(v => v && String(v).toLowerCase().includes(q)));
        list.sort((x, y) => (y.seen || 0) - (x.seen || 0));
        return sendJson(res, 200, { ok: true, total: list.length, players: list.slice(0, 50).map(adminRow) }), true;
      }
      if (p === '/api/admin/player' && method === 'GET') {
        const a = db.accounts[url.searchParams.get('id')]; if (!a) return sendJson(res, 404, { ok: false, error: 'no such player' }), true;
        const invited = Object.values(db.accounts).filter(x => x.referredBy === a.id).map(x => ({ id: x.id, nick: x.nick, created: x.created, paid: x.refPaid || null }));
        return sendJson(res, 200, { ok: true, player: { ...adminRow(a), econ: ECON.pick(ECON.normalize(a.econ)), profile: a.profile, milestones: a.ms || {}, days: (a.days || []).slice(-30), referredBy: a.referredBy || null, refCode: a.ref || null, invited, listings: db.market.listings.filter(l => l.seller === a.id), lighter: a.lighter || [], verifiedCatches: a.verifiedCatches || 0 }, ledger: ledgerFor(a.id) }), true;
      }
      if (p === '/api/admin/player' && method === 'POST') {
        const b = await readBody(req), a = db.accounts[b.id]; if (!a) return sendJson(res, 404, { ok: false, error: 'no such player' }), true;
        const err = adminAction(a, b); save();
        return sendJson(res, 200, { ok: !err, error: err || undefined, player: adminRow(a) }), true;
      }
      if (p === '/api/admin/listings' && method === 'GET') return sendJson(res, 200, { ok: true, listings: db.market.listings.slice().sort((x, y) => y.t - x.t).slice(0, 100) }), true;
      if (p === '/api/admin/listing/remove' && method === 'POST') {
        const b = await readBody(req), l = db.market.listings.find(x => x.id === b.id); if (!l) return sendJson(res, 404, { ok: false, error: 'listing is gone' }), true;
        const seller = db.accounts[l.seller]; if (seller) { const e = ECON.normalize(seller.econ); e.baits[l.bait] = (e.baits[l.bait] || 0) + l.qty; inbox(seller, { k: 'removed', bait: l.bait, qty: l.qty }); }
        db.market.listings = db.market.listings.filter(x => x !== l); save(); ledger({ acc: l.seller, a: 'admin-unlist', l: l.id, note: String(b.note || '').slice(0, 200) });
        return sendJson(res, 200, { ok: true }), true;
      }
    }
    const acc = authed(req);
    if (!acc) return sendJson(res, 401, { ok: false, error: 'no session' }), true;
    if (acc.banned && p !== '/api/me') return sendJson(res, 403, { ok: false, error: 'this account is suspended. Contact support if you think this is a mistake.' }), true;
    if (p === '/api/referral' && method === 'GET') {
      const code = refCode(acc), invited = Object.values(db.accounts).filter(x => x.referredBy === acc.id);
      return sendJson(res, 200, { ok: true, code, invited: invited.length, rewarded: invited.filter(x => typeof x.refPaid === 'number').length, reward: { you: REF.inviterAx, bait: REF.inviterBait, friend: REF.friendAx, friendBait: REF.joinBait } }), true;
    }

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

    // link the player's wallet: sign a one-time message, we recover the address and find their Lighter accounts
    if (p === '/api/wallet/nonce' && method === 'POST') {
      const nonce = crypto.randomBytes(12).toString('hex'); acc.walletNonce = { n: nonce, t: Date.now() }; save();
      return sendJson(res, 200, { ok: true, message: `HookLine: link this wallet to my game account ${acc.id}.\nNonce: ${nonce}` }), true;
    }
    if (p === '/api/wallet/link' && method === 'POST') {
      const b = await readBody(req), wn = acc.walletNonce;
      if (!wn || Date.now() - wn.t > 10 * 60e3) return sendJson(res, 400, { ok: false, error: 'link request expired, try again' }), true;
      let addr; try { addr = LV.recoverAddress(`HookLine: link this wallet to my game account ${acc.id}.\nNonce: ${wn.n}`, b.signature).toLowerCase(); } catch (_) { return sendJson(res, 400, { ok: false, error: 'signature could not be checked' }), true; }
      if (!/^0x[0-9a-fA-F]{40}$/.test(String(b.address || '')) || String(b.address).toLowerCase() !== addr) return sendJson(res, 400, { ok: false, error: 'signature does not match this wallet' }), true;
      if (db.wallets[addr] && db.wallets[addr] !== acc.id) return sendJson(res, 409, { ok: false, error: 'this wallet is already linked to another HookLine account' }), true;
      let idx = []; try { idx = await LV.lighterAccounts(addr); } catch (e) { return sendJson(res, 502, { ok: false, error: 'Lighter did not answer, try again' }), true; }
      if (acc.wallet && acc.wallet !== addr) delete db.wallets[acc.wallet];
      acc.wallet = addr; acc.lighter = idx; acc.walletNonce = null; db.wallets[addr] = acc.id; save(); ledger({ acc: acc.id, a: 'wallet', addr, lighter: idx });
      return sendJson(res, 200, { ok: true, wallet: addr, lighter: idx }), true;
    }
    // a real fish closed: rebuild it from the player's Lighter fills and pay out on Lighter's numbers
    if (p === '/api/catch/real' && method === 'POST') {
      if (!allow('real:' + acc.id, 10, 0.5)) return sendJson(res, 429, { ok: false, error: 'slow down' }), true;
      const b = await readBody(req), mk = String(b.mk || ''), side = b.side > 0 ? 1 : -1;
      if (!(mk in ECON.RARITY)) return sendJson(res, 400, { ok: false, error: 'unknown market' }), true;
      if (!acc.wallet) return sendJson(res, 409, { ok: false, error: 'link your wallet first' }), true;
      if (!acc.lighter || !acc.lighter.length) { try { acc.lighter = await LV.lighterAccounts(acc.wallet); } catch (_) { } }
      if (!acc.lighter || !acc.lighter.length) return sendJson(res, 409, { ok: false, error: 'no Lighter account for your wallet yet' }), true;
      const used = new Set(acc.usedTrades || []);
      let v = null;
      for (let i = 0; i < 4; i++) {   // Lighter's trade history can lag a few seconds behind the fill
        try { v = await LV.verifyCatch({ indexes: acc.lighter, market: mk, side, openAt: +b.openAt, closeAt: +b.closeAt, used }); } catch (e) { v = { ok: false, reason: e.message }; }
        if (v.ok) break; await new Promise(r => setTimeout(r, 2500));
      }
      if (!v.ok) return sendJson(res, 202, { ok: false, pending: true, error: v.reason || 'not on Lighter yet' }), true;
      acc.usedTrades = [...(acc.usedTrades || []), ...v.tradeIds].slice(-3000);
      const lev = Math.max(1, Math.min(+b.lev || 1, 50)), margin = v.notional / lev, pct = margin > 0 ? v.pnl / margin : 0;
      const r = ECON.apply(acc.econ, 'catch', { mk, side, lev, margin, pct, usd: v.pnl, shiny: !!b.shiny, bait: b.bait || null, real: true, verified: true }, { now: Date.now(), lvl: acc.profile.lvl || 1, live: MODE === 'live', id: () => newId('f') });
      if (r.ok) { acc.verifiedCatches = (acc.verifiedCatches || 0) + 1; track(acc, 'verified_catch'); }
      save(); ledger({ acc: acc.id, a: 'realcatch', mk, side, pnl: +v.pnl.toFixed(4), notional: +v.notional.toFixed(2), trades: v.tradeIds, ok: r.ok });
      return sendJson(res, 200, { ok: r.ok, error: r.err, out: r.out, verified: { pnl: v.pnl, notional: v.notional, pct }, econ: ECON.pick(acc.econ), treasury: publicTreasury() }), true;
    }

    // player market
    if (p === '/api/market' && method === 'GET') return sendJson(res, 200, { ok: true, ...marketView(acc), econ: ECON.pick(acc.econ), treasury: publicTreasury() }), true;
    if (p.startsWith('/api/market/') && method === 'POST') {
      if (!allow('econ:' + acc.id, 20, 8)) return sendJson(res, 429, { ok: false, error: 'slow down' }), true;
      const body = await readBody(req), op = p.slice('/api/market/'.length);
      const fn = { list: marketList, buy: marketBuy, cancel: marketCancel }[op]; if (!fn) return false;
      const err = fn(acc, body);
      return sendJson(res, 200, { ok: !err, error: err || undefined, ...marketView(acc), econ: ECON.pick(acc.econ), treasury: publicTreasury() }), true;
    }

    return false;
  } catch (e) {
    sendJson(res, e.code && e.code < 600 ? e.code : 500, { ok: false, error: e.code ? e.message : 'server error' }); if (!e.code) console.error('[accounts]', e);
    return true;
  }
}

module.exports = { handle, MODE, stats: () => ({ accounts: Object.keys(db.accounts).length, treasury: publicTreasury().ax, errorsLastHour: recentErrors.filter(e => Date.now() - e.t < 3600e3).length }) };
