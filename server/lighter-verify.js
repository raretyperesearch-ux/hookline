// Real-trade verification against Lighter's public API.
//  - recoverAddress(message, signature): who signed an EIP-191 personal_sign message (proves wallet ownership)
//  - lighterAccounts(address): the Lighter account indexes owned by that wallet
//  - verifyCatch({ indexes, market, side, openAt, closeAt, used }): rebuilds a closed position from the account's
//    fills and returns Lighter's own realized PnL, so a fish's value never comes from the browser.
const { secp256k1 } = require('@noble/curves/secp256k1');
const { keccak_256 } = require('@noble/hashes/sha3');

const NET = process.env.LIGHTER_NET === 'mainnet' ? 'mainnet' : 'testnet';
const BASE = process.env.LIGHTER_API_URL || (NET === 'mainnet' ? 'https://mainnet.zklighter.elliot.ai' : 'https://testnet.zklighter.elliot.ai');
const hex = b => Buffer.from(b).toString('hex');

function recoverAddress(message, signature) {
  const sig = String(signature || '').replace(/^0x/, '');
  if (!/^[0-9a-fA-F]{130}$/.test(sig)) throw new Error('bad signature');
  const msg = Buffer.from(String(message), 'utf8');
  const digest = keccak_256(Buffer.concat([Buffer.from(`\x19Ethereum Signed Message:\n${msg.length}`, 'utf8'), msg]));
  let v = parseInt(sig.slice(128, 130), 16); if (v >= 27) v -= 27;
  const pub = secp256k1.Signature.fromCompact(sig.slice(0, 128)).addRecoveryBit(v).recoverPublicKey(digest).toRawBytes(false);
  return '0x' + hex(keccak_256(pub.slice(1)).slice(-20));
}

async function getJson(path) {
  const r = await fetch(BASE + path, { signal: AbortSignal.timeout(8000) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || (j.code && j.code !== 200)) throw new Error(j.message || `Lighter HTTP ${r.status}`);
  return j;
}

async function lighterAccounts(address) {
  try { const j = await getJson(`/api/v1/accountsByL1Address?l1_address=${address}`); return (j.sub_accounts || []).map(a => a.index).filter(i => Number.isInteger(i)); }
  catch (e) { if (/not found|does not exist/i.test(e.message)) return []; throw e; }
}

async function trades(index) {
  const j = await getJson(`/api/v1/trades?account_index=${index}&sort_by=timestamp&limit=100`);
  return j.trades || [];
}

// For a long, the opening fills are buys (we are the bid) and the closing fills are sells (we are the ask); a short is the mirror.
// Realized PnL comes from Lighter's per-side `*_account_pnl` on the closing fills; if missing we fall back to prices.
async function verifyCatch({ indexes, market, side, openAt, closeAt, used }) {
  const from = (+openAt || 0) - 120e3, to = (+closeAt || Date.now()) + 120e3;
  for (const index of indexes) {
    const mine = (await trades(index)).filter(t => t.market_symbol === market && t.timestamp >= from && t.timestamp <= to && !used.has(String(t.trade_id)) && (t.ask_account_id === index || t.bid_account_id === index));
    if (!mine.length) continue;
    const role = t => (t.ask_account_id === index ? 'ask' : 'bid');
    const openRole = side > 0 ? 'bid' : 'ask';
    const opens = mine.filter(t => role(t) === openRole), closes = mine.filter(t => role(t) !== openRole && t.timestamp >= (opens.length ? Math.min(...opens.map(o => o.timestamp)) : from));
    if (!opens.length || !closes.length) continue;
    const notional = opens.reduce((a, t) => a + +t.usd_amount, 0), openSize = opens.reduce((a, t) => a + +t.size, 0);
    const avgOpen = notional / Math.max(1e-12, openSize);
    let pnl = 0;
    for (const t of closes) {
      const f = t[`${role(t)}_account_pnl`];
      pnl += f != null && f !== '' ? +f : (+t.price - avgOpen) * +t.size * side;
    }
    return { ok: true, index, notional, pnl, closeSize: closes.reduce((a, t) => a + +t.size, 0), tradeIds: [...opens, ...closes].map(t => String(t.trade_id)) };
  }
  return { ok: false, reason: 'not found on Lighter yet' };
}

module.exports = { recoverAddress, lighterAccounts, verifyCatch, NET, BASE };
