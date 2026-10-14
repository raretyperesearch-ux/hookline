// HookLine economy: the one rulebook for AX, gear, the cooler, bait and quests.
// Loaded by the game (window.HL_ECON) to predict results instantly, and by the server
// (require('../game/economy.js')) which is the authority: only the server's answer sticks.
// apply(state, action, args, ctx) mutates an economy state and returns { ok, err?, out? }.
(function (root) {
  const ROSTER = typeof module !== 'undefined' && module.exports ? require('./markets.js').ROSTER : root.HL_ROSTER;

  /* ---------- gear (bought with AX) ---------- */
    const RODS = [
      { id: 'rod1', name: 'Bamboo Rod', lev: 2, price: 0, lvl: 1, desc: 'Gentle. Hard to get liquidated.' },
      { id: 'rod2', name: 'Carbon Rod', lev: 5, price: 150, lvl: 1, desc: 'The everyday rod.' },
      { id: 'rod4', name: 'Steel Rod', lev: 10, price: 400, lvl: 2, desc: 'Big moves both ways.' },
      { id: 'rod3', name: 'Golden Rod', lev: 25, price: 1200, lvl: 5, desc: 'For people who know what they are doing.' },
      { id: 'rod5', name: 'Kraken Rod', lev: 50, price: 3000, lvl: 8, desc: 'Legendary. The Kraken is always close.' },
    ];
    const KNIVES = [
      { id: 'knife1', name: 'Rusty Knife', price: 0, lvl: 1, kind: 'fixed', fixed: -0.25, charges: 3, desc: 'Cuts at −25%. Dulls after 3 cuts.' },
      { id: 'knife2', name: 'Fillet Knife', price: 200, lvl: 1, kind: 'adjust', charges: 8, desc: 'You choose where it cuts.' },
      { id: 'knife3', name: 'Smart Knife', price: 500, lvl: 3, kind: 'trail', charges: 12, desc: 'Trailing stop: follows the fish up, cuts if it turns.' },
      { id: 'knife4', name: 'Golden Knife', price: 900, lvl: 4, kind: 'half', charges: 12, desc: 'Cuts half the line at your stop, keeps fighting with the rest.' },
      { id: 'knife5', name: 'Kraken Blade', price: 1500, lvl: 6, kind: 'adjust', charges: Infinity, desc: 'Never dulls. You choose where it cuts.' },
    ];
    const NETS = [
      { id: 'net0', art: null, name: 'No net', price: 0, tp: null, desc: 'Close winners by hand.' },
      { id: 'net1', art: 'net', name: 'Keeper Net', price: 250, lvl: 1, tp: 0.5, desc: 'Scoops winners at +50%.' },
      { id: 'net2', art: 'net', name: 'Big Net', price: 600, lvl: 3, tp: 'adjust', desc: 'You choose the target.' },
    ];
    const SONARS = [
      { lvl: 0, name: 'No sonar', price: 0, desc: 'Every fish is a mystery.' },
      { lvl: 1, name: 'Sonar I', price: 150, desc: 'Shows the market.' },
      { lvl: 2, name: 'Sonar II', price: 450, desc: 'Shows market and long/short.' },
      { lvl: 3, name: 'Sonar III', price: 1000, desc: 'Adds the market\'s momentum.' },
    ];
    const CHESTS = [
      { id: 'chest1', name: 'Tin Bucket', slots: 2, price: 0, lvl: 1 },
      { id: 'chest2', name: 'Cooler', slots: 4, price: 300, lvl: 1 },
      { id: 'chest3', name: 'Iron Chest', slots: 6, price: 800, lvl: 3 },
      { id: 'chest4', name: 'Golden Vault', slots: 10, price: 2000, lvl: 5 },
    ];
    const BOATS = [
      { id: 'boat', art: 'b2_row', name: 'Rowboat', price: 0, lvl: 1, speed: 10, turn: 2.8, grip: 1, slots: 0, desc: 'Steady and free. Every angler starts here.' },
      { id: 'boat_speed', art: 'b2_speed', name: 'Speedboat', price: 600, lvl: 2, speed: 15, turn: 3.5, grip: 1.3, slots: 0, desc: 'First to every hot zone. Red fish yank it around.' },
      { id: 'boat_trawler', art: 'b2_trawler', name: 'Trawler', price: 1500, lvl: 3, speed: 8, turn: 2.0, grip: 0.4, slots: 2, desc: 'Holds 2 extra fish. Barely moves when one turns red.' },
    ];

  /* ---------- the catch economy ----------
     Green fish you pull in go in your cooler; cut losers leave scraps. Sell fish to the Harbor Treasury for AX
     (topped up from HookLine's trading fees), or craft them into bait. Bait never touches the trade itself:
     it changes bites, shinies, XP and what the fish sells for. */
    const BAITS = [
      { id: 'worm', name: 'Lucky Worm', icon: '🪱', desc: 'The fish it catches sells for +50% AX.', recipe: { scraps: 3 }, price: 40 },
      { id: 'chum', name: 'Chum Bucket', icon: '🪣', desc: 'Quick bites, no fake-outs, a longer strike window.', recipe: { fish: 1, scraps: 1 }, price: 60 },
      { id: 'glitter', name: 'Glitter Bait', icon: '✨', desc: 'Much better odds of a shiny.', recipe: { fish: 2 }, price: 90 },
      { id: 'grub', name: 'Golden Grub', icon: '🐛', desc: 'Double XP, and the fish sells for double.', recipe: { fish: 3 }, price: 160 },
    ];
  const LURE_PRICE = 300, SHARPEN_PRICE = 25;
  const DAILY_BUYBACK = 600, COOLER_MAX = 40, LEVEL_AX = 50, TREASURE_AX = 25, TREASURE_PER_DAY = 5, CATCHES_PER_DAY = 400;
  const TIERS = [{ n: 'Common', m: 1, c: '#9ad8ee' }, { n: 'Uncommon', m: 1.5, c: '#3ddc97' }, { n: 'Rare', m: 2.2, c: '#5fb4ff' }, { n: 'Legendary', m: 3.5, c: '#ffd23f' }];
  // rarity from trading volume: the big tickers are common fish, thin ones are rare
  const WEIGHT = {}; for (const r of ROSTER) WEIGHT[r[0]] = Math.sqrt(r[9] + 2e4);
  const RARITY = {}; Object.keys(WEIGHT).sort((a, b) => WEIGHT[b] - WEIGHT[a]).forEach((m, i, a) => { const q = i / a.length; RARITY[m] = q < 0.35 ? 0 : q < 0.7 ? 1 : q < 0.92 ? 2 : 3; });
  const MAXLEV = {}; for (const r of ROSTER) MAXLEV[r[0]] = r[3];
  const fishValue = f => Math.round((10 + Math.max(0, f.usd || 0) * 4 + Math.max(0, f.pct) * 40) * TIERS[RARITY[f.mk] ?? 0].m * (f.shiny ? 3 : 1) * (f.bait === 'worm' ? 1.5 : f.bait === 'grub' ? 2 : 1));

  /* ---------- daily quests: the same four for everyone each UTC day ---------- */
    const QUEST_POOL = [
      { id: 'land', text: n => `Land ${n} fish`, n: [3, 5, 8], reward: 60, icon: 'f_tuna' },
      { id: 'win', text: n => `Close ${n} fish in profit`, n: [1, 2, 3], reward: 80, icon: 'net' },
      { id: 'cut', text: () => 'Cut a loser before −20%', n: [1], reward: 80, icon: 'knife2' },
      { id: 'newsp', text: n => `Discover ${n} new species`, n: [1, 2, 3], reward: 90, icon: 'book' },
      { id: 'frenzy', text: n => `Hook ${n} fish in a Frenzy zone`, n: [1, 2], reward: 100, icon: 'star' },
      { id: 'perfect', text: n => `Make ${n} PERFECT casts`, n: [2, 3], reward: 70, icon: 'rod2' },
      { id: 'podium', text: () => 'Finish a round in the top 3', n: [1], reward: 120, icon: 'trophy' },
      { id: 'shiny', text: () => 'Hook a shiny fish ✦', n: [1], reward: 150, icon: 'star' },
      { id: 'rings', text: () => 'Chain 3 speed rings', n: [1], reward: 70, icon: 'bolt' },
      { id: 'trick', text: n => `Land ${n} spin tricks`, n: [1, 2], reward: 70, icon: 'star' },
      { id: 'loot', text: n => `Find ${n} message bottles`, n: [2, 3], reward: 60, icon: 'bubble' },
      { id: 'hold', text: n => `Hold ${n} fish at once`, n: [2, 3], reward: 90, icon: 'chest2' },
      { id: 'zone_bayou', text: n => `Hook ${n} fish in Meme Bayou`, n: [2, 3], reward: 70, icon: 'f_puffer' },
      { id: 'zone_pier', text: n => `Hook ${n} stocks off Wall St. Pier`, n: [2, 3], reward: 70, icon: 'f_shark' },
      { id: 'zone_abyss', text: n => `Hook ${n} fish in the AI Abyss`, n: [1, 2], reward: 80, icon: 'f_octopus' },
      { id: 'zone_gold', text: n => `Hook ${n} fish on the Gold Coast`, n: [2], reward: 70, icon: 'f_sunfish' },
    ];
  const dayNum = (t = Date.now()) => Math.floor(t / 864e5);
  const dayStr = (t = Date.now()) => new Date(t).toISOString().slice(0, 10);
  function questsForDay(day) {
    let seed = day * 9301 + 49297; const rnd = () => (seed = (seed * 9301 + 49297) % 233280) / 233280;
    const pool = QUEST_POOL.slice(), out = [];
    while (out.length < 4) { const q = pool.splice(Math.floor(rnd() * pool.length), 1)[0]; const n = q.n[Math.floor(rnd() * q.n.length)]; out.push({ id: q.id, text: q.text(n), goal: n, prog: 0, reward: q.reward + (n - 1) * 20, icon: q.icon }); }
    return out;
  }

  /* ---------- state ---------- */
  const KEYS = ['ax', 'owned', 'eq', 'sonar', 'lures', 'charges', 'cooler', 'scraps', 'baits', 'baitEq', 'sold', 'claims', 'lvlPaid'];
  const fresh = () => ({ ax: 250, owned: { rod1: 1, knife1: 1, net0: 1, chest1: 1, boat: 1 }, eq: { rod: 'rod1', knife: 'knife1', net: 'net0', chest: 'chest1', boat: 'boat' }, sonar: 0, lures: false, charges: { knife1: 3 },
    cooler: [], scraps: 0, baits: {}, baitEq: null, sold: { day: '', ax: 0 }, claims: { day: '', q: [], treasure: 0, catches: 0 }, lvlPaid: 1 });
  function normalize(s) {
    const f = fresh(); for (const k of KEYS) if (s[k] === undefined || s[k] === null && k !== 'baitEq') s[k] = f[k];
    s.ax = Math.max(0, Math.floor(+s.ax || 0)); if (!Array.isArray(s.cooler)) s.cooler = [];
    return s;
  }
  const pick = (s, keys = KEYS) => { const o = {}; for (const k of keys) o[k] = s[k]; return JSON.parse(JSON.stringify(o)); };
  function today(s, now) { const d = dayStr(now); if (s.sold.day !== d) s.sold = { day: d, ax: 0 }; if (s.claims.day !== d) s.claims = { day: d, q: [], treasure: 0, catches: 0 }; }
  const POOLS = { rod: RODS, knife: KNIVES, net: NETS, chest: CHESTS, boat: BOATS };
  const buybackLeft = s => Math.max(0, DAILY_BUYBACK - s.sold.ax);
  const canCraft = (s, b) => s.scraps >= (b.recipe.scraps || 0) && s.cooler.length >= (b.recipe.fish || 0);
  const has = (s, n) => s.ax >= n;
  const fail = err => ({ ok: false, err });

  /* apply(state, action, args, ctx): ctx = { now, lvl, treasury: {ax} | null, live: bool, id: () => string }
     treasury null means "predicting on the client": treasury checks are left to the server. */
  function apply(s, action, a = {}, ctx = {}) {
    normalize(s); const now = ctx.now || Date.now(); today(s, now); const lvl = ctx.lvl || 1;
    switch (action) {
      case 'buy': {   // rods, knives, nets, chests, boats
        const pool = POOLS[a.slot]; const it = pool && pool.find(x => x.id === a.id); if (!it) return fail('unknown item');
        if (s.owned[it.id]) { s.eq[a.slot] = it.id; return { ok: true }; }
        if (it.lvl && lvl < it.lvl) return fail(`unlocks at level ${it.lvl}`); if (!has(s, it.price)) return fail('not enough AX');
        s.ax -= it.price; s.owned[it.id] = 1; s.eq[a.slot] = it.id; if (a.slot === 'knife') s.charges[it.id] = it.charges === Infinity ? -1 : it.charges;
        return { ok: true, out: { spent: it.price } };
      }
      case 'equip': { if (!POOLS[a.slot] || !s.owned[a.id]) return fail('not owned'); s.eq[a.slot] = a.id; return { ok: true }; }
      case 'sonar': { const so = SONARS.find(x => x.lvl === +a.lvl); if (!so) return fail('unknown sonar'); if (s.sonar >= so.lvl) { s.sonar = so.lvl; return { ok: true }; } if (!has(s, so.price)) return fail('not enough AX'); s.ax -= so.price; s.sonar = so.lvl; return { ok: true }; }
      case 'lures': { if (s.lures) return { ok: true }; if (!has(s, LURE_PRICE)) return fail('not enough AX'); s.ax -= LURE_PRICE; s.lures = true; return { ok: true }; }
      case 'sharpen': { const k = KNIVES.find(x => x.id === a.id); if (!k || !s.owned[k.id]) return fail('not owned'); if (!has(s, SHARPEN_PRICE)) return fail('not enough AX'); s.ax -= SHARPEN_PRICE; s.charges[k.id] = k.charges === Infinity ? -1 : k.charges; return { ok: true }; }
      case 'knifeUse': { const c = s.charges[a.id]; if (c > 0) s.charges[a.id] = c - 1; return { ok: true }; }
      case 'catch': {   // a closed fish: green ones go in the cooler, cut ones leave a scrap
        if (s.claims.catches >= CATCHES_PER_DAY) return fail('daily catch limit');
        const mk = String(a.mk || ''); if (!(mk in MAXLEV)) return fail('unknown market');
        if (ctx.live && !a.verified) return fail('only verified Lighter trades land sellable fish');
        s.claims.catches++;
        if (!(a.pct > 0)) { s.scraps = Math.min(999, s.scraps + 1); return { ok: true, out: { scrap: true } }; }
        const lev = Math.max(1, Math.min(MAXLEV[mk] || 50, +a.lev || 1)), margin = Math.max(0, Math.min(10000, +a.margin || 0));
        const pct = Math.max(0, Math.min(5, +a.pct || 0)), usd = Math.max(0, Math.min(margin * pct, +a.usd || 0));
        const item = { id: (ctx.id ? ctx.id() : 'f' + now.toString(36) + Math.random().toString(36).slice(2, 6)), mk, side: a.side > 0 ? 1 : -1, lev, pct: +pct.toFixed(4), usd: +usd.toFixed(2),
          kg: +(margin * lev / 10 * (1 + Math.min(pct, 3))).toFixed(1), shiny: !!a.shiny, bait: BAITS.some(b => b.id === a.bait) ? a.bait : null, real: !!a.real, verified: !!a.verified, t: now };
        s.cooler.unshift(item); if (s.cooler.length > COOLER_MAX) s.cooler.length = COOLER_MAX;
        return { ok: true, out: { fish: item, value: fishValue(item) } };
      }
      case 'sell': case 'sellAll': {
        const ids = action === 'sell' ? [a.id] : s.cooler.slice().sort((x, y) => fishValue(y) - fishValue(x)).map(f => f.id);
        let total = 0, n = 0;
        for (const id of ids) {
          const i = s.cooler.findIndex(f => f.id === id); if (i < 0) continue; const f = s.cooler[i], v = fishValue(f);
          if (ctx.live && !f.verified) continue;
          if (v > buybackLeft(s)) continue; if (ctx.treasury && ctx.treasury.ax < v) continue;
          s.cooler.splice(i, 1); s.ax += v; s.sold.ax += v; total += v; n++; if (ctx.treasury) ctx.treasury.ax -= v;
        }
        if (!n) return fail(ctx.treasury && ctx.treasury.ax <= 0 ? 'the Harbor Treasury is empty' : 'today\'s buyback is used up');
        return { ok: true, out: { ax: total, n } };
      }
      case 'craft': {
        const b = BAITS.find(x => x.id === a.id); if (!b) return fail('unknown bait'); if (!canCraft(s, b)) return fail('missing ingredients');
        s.scraps -= b.recipe.scraps || 0;
        if (b.recipe.fish) { s.cooler.sort((x, y) => fishValue(y) - fishValue(x) || y.t - x.t); s.cooler.splice(s.cooler.length - b.recipe.fish, b.recipe.fish); }   // the cheapest fish go in the pot
        s.baits[b.id] = (s.baits[b.id] || 0) + 1; if (!s.baitEq) s.baitEq = b.id; return { ok: true };
      }
      case 'buyBait': { const b = BAITS.find(x => x.id === a.id); if (!b) return fail('unknown bait'); if (!has(s, b.price)) return fail('not enough AX'); s.ax -= b.price; s.baits[b.id] = (s.baits[b.id] || 0) + 1; if (!s.baitEq) s.baitEq = b.id; return { ok: true }; }
      case 'setBait': { if (a.id && !BAITS.some(b => b.id === a.id)) return fail('unknown bait'); s.baitEq = a.id || null; return { ok: true }; }
      case 'useBait': { const id = s.baitEq; if (!id || !(s.baits[id] > 0)) return fail('no bait on the hook'); s.baits[id]--; if (!s.baits[id]) s.baitEq = null; return { ok: true, out: { id } }; }
      case 'claimQuest': {
        const q = questsForDay(dayNum(now)).find(x => x.id === a.id); if (!q) return fail('not today\'s quest'); if (s.claims.q.includes(q.id)) return fail('already claimed');
        s.claims.q.push(q.id); s.ax += q.reward; return { ok: true, out: { ax: q.reward } };
      }
      case 'levelReward': {
        const L = Math.min(+a.lvl || 0, lvl, 99); if (L <= s.lvlPaid) return fail('already paid');
        const unlock = [...RODS, ...KNIVES, ...NETS, ...CHESTS].some(g => g.lvl === L); s.lvlPaid = L; if (unlock) return { ok: true, out: { ax: 0 } };
        s.ax += LEVEL_AX; return { ok: true, out: { ax: LEVEL_AX } };
      }
      case 'treasure': { if (s.claims.treasure >= TREASURE_PER_DAY) return fail('no more treasure today'); s.claims.treasure++; s.ax += TREASURE_AX; return { ok: true, out: { ax: TREASURE_AX } }; }
      default: return fail('unknown action');
    }
  }

  const API = { RODS, KNIVES, NETS, SONARS, CHESTS, BOATS, BAITS, LURE_PRICE, SHARPEN_PRICE, DAILY_BUYBACK, COOLER_MAX, TIERS, RARITY, QUEST_POOL,
    fishValue, questsForDay, dayNum, dayStr, KEYS, fresh, normalize, pick, apply, buybackLeft, canCraft };
  if (typeof module !== 'undefined' && module.exports) module.exports = API; else root.HL_ECON = API;
})(typeof self !== 'undefined' ? self : this);
