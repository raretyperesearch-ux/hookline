/* HookLine ⇄ Lighter core: everything the game needs to trade on Lighter from the player's own device.
   Runs unchanged in the browser (main thread or worker) and in Node 18+ (tests), so the code path that
   is tested on testnet is the code path players use.

   - NETWORKS: the one place testnet / mainnet differ.
   - loadWasmSigner(): boots Lighter's official Go→WASM signer (lighter-go/web-wasm) and returns
     { call(name, ...args) } over its Promise-based exports (_createClient, _signCreateOrder, ...).
   - LighterClient: REST + signing. Ports relay/app.py (same math, limits and decimals handling), but the
     API key lives on the player's device and never leaves it.

   Nothing here holds the player's EVM wallet: registering an API key asks the caller for one EIP-191
   signature (signMessage), which the page gets from the Privy embedded wallet. */
(function (root, factory) {
  const mod = factory();
  if (typeof module === 'object' && module.exports) module.exports = mod;
  else root.LighterCore = mod;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // ---- config: flip `net` to 'mainnet' later; everything else follows ----
  const NETWORKS = {
    testnet: { name: 'testnet', url: 'https://testnet.zklighter.elliot.ai', chainId: 300, markets: ['BTC', 'ETH', 'SOL'], faucet: true },
    mainnet: { name: 'mainnet', url: 'https://mainnet.zklighter.elliot.ai', chainId: 304, markets: ['BTC', 'ETH', 'SOL'], faucet: false },
  };
  const DEFAULTS = {
    apiKeyIndex: 5,          // 0-3 are used by Lighter's own apps; HookLine registers its device key at index 5
    maxMarginUsd: 25,        // same hard limits as the relay (MAX_MARGIN_USD / MAX_LEVERAGE)
    maxLeverage: 10,
    maxSlippage: 0.01,
  };
  const TX = { CHANGE_PUB_KEY: 8, CREATE_ORDER: 14, CANCEL_ORDER: 15, UPDATE_LEVERAGE: 20 };
  const ORDER = { LIMIT: 0, MARKET: 1, STOP_LOSS: 2, TAKE_PROFIT: 4 };
  const TIF = { IOC: 0, GTT: 1 };
  const ISOLATED = 1;
  const EXPIRY_28D = -1;     // the signer turns -1 into now + 28 days
  const IOC_EXPIRY = 0;

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const hex = bytes => Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  const randomSeed = () => { const b = new Uint8Array(32); globalThis.crypto.getRandomValues(b); return hex(b); };
  const strip0x = s => String(s || '').replace(/^0x/i, '').toLowerCase();

  class LighterError extends Error { constructor(msg, code, extra) { super(msg); this.name = 'LighterError'; this.code = code; Object.assign(this, extra || {}); } }

  // ---------------------------------------------------------------- WASM signer
  async function gunzip(buf) {
    const ds = new DecompressionStream('gzip');
    return new Response(new Blob([buf]).stream().pipeThrough(ds)).arrayBuffer();
  }
  /* opts: { url } (fetched) or { bytes } (ArrayBuffer/Uint8Array). Gzipped input (.wasm.gz) is detected and
     inflated in-process, so the server needs no Content-Encoding support. Needs Go's wasm_exec.js loaded first. */
  async function loadWasmSigner(opts) {
    const g = globalThis;
    if (typeof g._createClient === 'function' && g.__lighterSigner) return g.__lighterSigner;
    if (typeof g.Go !== 'function') throw new LighterError('Lighter signer: wasm_exec.js is not loaded');
    let buf = opts.bytes || await (await fetch(opts.url)).arrayBuffer();
    let u8 = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    if (u8[0] === 0x1f && u8[1] === 0x8b) u8 = new Uint8Array(await gunzip(u8));
    const go = new g.Go();
    const { instance } = await WebAssembly.instantiate(u8, go.importObject);
    go.run(instance);                       // never resolves: the Go program parks and serves calls
    for (let i = 0; i < 200 && typeof g._createClient !== 'function'; i++) await sleep(10);
    if (typeof g._createClient !== 'function') throw new LighterError('Lighter signer did not start');
    const signer = {
      kind: 'wasm',
      // every export returns a function that returns a Promise resolving to {..} or {error}
      async call(name, ...args) {
        const f = g[name]; if (typeof f !== 'function') throw new LighterError('signer has no ' + name);
        const r = await f(...args)();
        if (r && r.error) throw new LighterError('signer: ' + r.error);
        return r;
      },
    };
    g.__lighterSigner = signer;
    return signer;
  }

  // ---------------------------------------------------------------- client
  class LighterClient {
    /* opts: { net: 'testnet'|'mainnet'|{...}, signer: {call}|async()=>signer, fetch, apiKeyIndex, limits:{maxMarginUsd,maxLeverage,maxSlippage}, log } */
    constructor(opts = {}) {
      this.net = typeof opts.net === 'object' ? opts.net : NETWORKS[opts.net || 'testnet'];
      if (!this.net) throw new LighterError('unknown Lighter network ' + opts.net);
      this.url = this.net.url.replace(/\/+$/, '');
      this.chainId = this.net.chainId;
      this.allowed = new Set(opts.markets || this.net.markets || []);
      this.apiKeyIndex = opts.apiKeyIndex ?? DEFAULTS.apiKeyIndex;
      this.lim = Object.assign({}, DEFAULTS, opts.limits || {});
      this._fetch = opts.fetch || ((...a) => globalThis.fetch(...a));
      this._signerSrc = opts.signer || null;
      this._signer = null;
      this.log = opts.log || (() => { });
      this.accountIndex = null;     // set by useKey()/registerKey()
      this.publicKey = null;
      this._markets = null; this._marketsAt = 0; this._prices = null; this._pricesAt = 0;
      this._q = Promise.resolve();   // one signed tx at a time keeps nonces simple
      this._nonce = null;
      this._auth = null;
      this.lev = {};                 // market -> leverage we opened it with (knife/net math)
      this.trig = {};                // 'market:type' -> {coi, at} of the last knife / net we placed
    }

    // -------- plumbing
    async signer() {
      if (this._signer) return this._signer;
      const s = typeof this._signerSrc === 'function' ? await this._signerSrc() : this._signerSrc;
      if (!s || typeof s.call !== 'function') throw new LighterError('Lighter signer is not available');
      this._signer = s; return s;
    }
    async get(path, params, headers) {
      const qs = params ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)])) : '';
      const r = await this._fetch(this.url + path + qs, { headers: headers || {} });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || (j.code != null && j.code !== 200)) throw new LighterError(j.message || `Lighter ${path} failed (${r.status})`, j.code ?? r.status);
      return j;
    }
    async sendTx(txType, txInfo) {
      const body = new URLSearchParams({ tx_type: String(txType), tx_info: txInfo, price_protection: 'false' });
      const r = await this._fetch(this.url + '/api/v1/sendTx', { method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: body.toString() });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || j.code !== 200) throw new LighterError('Lighter rejected the transaction: ' + (j.message || r.status), j.code ?? r.status);
      return j;   // { code, message, tx_hash, predicted_execution_time_ms }
    }
    _serial(fn) { const p = this._q.then(fn, fn); this._q = p.catch(() => { }); return p; }
    async _nextNonce(fresh) {
      if (fresh || this._nonce == null) {
        const j = await this.get('/api/v1/nextNonce', { account_index: this.accountIndex, api_key_index: this.apiKeyIndex });
        this._nonce = j.nonce;
      }
      return this._nonce;
    }
    /* sign with `signFn(nonce)` → {txHash, txInfo}, send, bump the local nonce; one retry on a stale nonce */
    async _signed(txType, signFn) {
      for (let attempt = 0; ; attempt++) {
        const nonce = await this._nextNonce(attempt > 0);
        const tx = await signFn(nonce);
        try {
          const r = await this.sendTx(txType, tx.txInfo);
          this._nonce = nonce + 1;
          return { tx_hash: r.tx_hash || tx.txHash };
        } catch (e) {
          this._nonce = null;
          if (attempt === 0 && (e.code === 21104 || /nonce/i.test(e.message))) continue;
          throw e;
        }
      }
    }
    _need() { if (this.accountIndex == null || !this.publicKey) throw new LighterError('Trading is not enabled on this device yet.'); }

    // -------- public data
    async markets(force) {
      if (!force && this._markets && Date.now() - this._marketsAt < 600e3) return this._markets;
      const j = await this.get('/api/v1/orderBookDetails');
      const out = {};
      for (const o of j.order_book_details || []) {
        if (o.market_type !== 'perp' || o.status !== 'active') continue;
        out[o.symbol] = {
          id: o.market_id, size_decimals: o.size_decimals, price_decimals: o.price_decimals,
          min_base: +o.min_base_amount, min_quote: +o.min_quote_amount,
          max_leverage: Math.max(1, Math.round(10000 / Math.max(1, o.min_initial_margin_fraction || 1000))),
          mark: +(o.mark_price || o.last_trade_price || 0),
        };
      }
      this._markets = out; this._marketsAt = Date.now();
      return out;
    }
    async allowedMarkets() { const ms = await this.markets(); const o = {}; for (const k of Object.keys(ms)) if (!this.allowed.size || this.allowed.has(k)) o[k] = ms[k]; return o; }
    async market(sym) {
      if (this.allowed.size && !this.allowed.has(sym)) throw new LighterError(`${sym} is not enabled for real fills`);
      const ms = await this.markets(); if (!ms[sym]) throw new LighterError('unknown market ' + sym);
      return ms[sym];
    }
    async prices() {
      if (this._prices && Date.now() - this._pricesAt < 1500) return this._prices;
      const j = await this.get('/api/v1/orderBookDetails');
      const out = {};
      for (const o of j.order_book_details || []) {
        if (o.market_type !== 'perp' || (this.allowed.size && !this.allowed.has(o.symbol))) continue;
        const px = +(o.mark_price || o.last_trade_price || 0); if (px > 0) out[o.symbol] = px;
      }
      this._prices = out; this._pricesAt = Date.now();
      return out;
    }
    async book(marketId, limit = 50) { return this.get('/api/v1/orderBookOrders', { market_id: marketId, limit }); }

    // -------- accounts
    /* the player's main Lighter account for an EVM address, or null if Lighter has never seen it */
    async accountByL1(addr) {
      try {
        const j = await this.get('/api/v1/accountsByL1Address', { l1_address: addr });
        const subs = (j.sub_accounts || []).slice().sort((a, b) => (a.account_type || 0) - (b.account_type || 0) || a.index - b.index);
        return subs[0] || null;
      } catch (e) { if (e.code === 21100 || /not found/i.test(e.message)) return null; throw e; }
    }
    async account(index = this.accountIndex) {
      const j = await this.get('/api/v1/account', { by: 'index', value: index });
      return (j.accounts || [])[0] || null;
    }
    async apiKey(accountIndex = this.accountIndex, apiKeyIndex = this.apiKeyIndex) {
      try { const j = await this.get('/api/v1/apikeys', { account_index: accountIndex, api_key_index: apiKeyIndex }); return (j.api_keys || [])[0] || null; }
      catch (e) { if (e.code === 21109 || /not found/i.test(e.message)) return null; throw e; }
    }
    async keyIsRegistered(publicKey, accountIndex = this.accountIndex) {
      const k = await this.apiKey(accountIndex); return !!k && strip0x(k.public_key) === strip0x(publicKey);
    }

    // -------- API key on this device
    /* load an existing key into the signer (after reload) */
    async useKey({ accountIndex, privateKey }) {
      const s = await this.signer();
      const r = await s.call('_createClientByPrv', privateKey, this.chainId, accountIndex, 0, this.apiKeyIndex);
      this.accountIndex = accountIndex; this.publicKey = r.pk; this._nonce = null; this._auth = null;
      return { publicKey: r.pk };
    }
    /* make a fresh key and register it with ChangePubKey: the wallet signs Lighter's message (EIP-191),
       the new key signs the L2 tx. Resolves once Lighter lists the key. Returns { privateKey, publicKey, tx_hash }. */
    async registerKey({ accountIndex, signMessage, onStep, timeoutMs = 60000 }) {
      const step = onStep || (() => { });
      const s = await this.signer();
      this.accountIndex = accountIndex;
      return this._serial(async () => {
        const nonce = (await this.get('/api/v1/nextNonce', { account_index: accountIndex, api_key_index: this.apiKeyIndex })).nonce;
        const c = await s.call('_createClient', randomSeed(), this.chainId, accountIndex, nonce, this.apiKeyIndex);
        if (!c.pubKeySuccess || !c.body) throw new LighterError('could not build the key registration message');
        step('sign', c.body);
        const l1Sig = await signMessage(c.body);
        const tx = await s.call('_signChangePubKey', accountIndex, l1Sig, nonce, this.apiKeyIndex);
        step('send');
        const r = await this.sendTx(TX.CHANGE_PUB_KEY, tx.txInfo);
        step('wait', r.tx_hash);
        const t0 = Date.now();
        while (!(await this.keyIsRegistered(c.pk, accountIndex).catch(() => false))) {
          if (Date.now() - t0 > timeoutMs) throw new LighterError('Lighter has not activated the trading key yet; try again in a minute.', 'timeout', { tx_hash: r.tx_hash });
          await sleep(1500);
        }
        this.publicKey = c.pk; this._nonce = null; this._auth = null;
        return { privateKey: c.prv, publicKey: c.pk, tx_hash: r.tx_hash || tx.txHash };
      });
    }
    async authToken() {
      this._need();
      if (this._auth && this._auth.deadline * 1000 - Date.now() > 5 * 60e3) return this._auth.token;
      const s = await this.signer();
      this._auth = await s.call('_createAuthToken', this.accountIndex, this.apiKeyIndex);
      return this._auth.token;
    }

    // -------- trading (the relay's /v1/* endpoints, executed locally)
    _pos(p) {
      return {
        market: p.symbol, market_id: p.market_id, side: +p.sign > 0 ? 1 : -1, size: +p.position,
        size_str: String(p.position), entry: +(p.avg_entry_price || 0), upnl: +(p.unrealized_pnl || 0),
        liq: +(p.liquidation_price || 0), margin: +(p.allocated_margin || 0),
      };
    }
    /* balances + open positions for any account index (public data: no key needed).
       Isolated margin leaves `collateral` while a position is open, so `collateral` here is equity, like the
       demo adapter: free USDC + Σ(position margin + uPnL). `available` is what a new cast can use. */
    async summary(index = this.accountIndex) {
      const a = await this.account(index);
      const out = [];
      for (const p of (a && a.positions) || []) { if (+p.position === 0) continue; out.push(this._pos(p)); }
      const free = +((a && a.collateral) || 0);
      const equity = free + out.reduce((t, p) => t + p.margin + p.upnl, 0);
      return { exists: !!a, collateral: +equity.toFixed(6), available: +((a && a.available_balance) || 0), free, positions: out };
    }
    async positions() {
      this._need();
      const { collateral, available, free, positions } = await this.summary();
      return { collateral, available, free, positions };
    }
    async position(sym) { return (await this.positions()).positions.find(p => p.market === sym) || null; }
    _px(m, price) { return Math.round(price * 10 ** m.price_decimals); }
    _base(m, size) { return Math.floor(size * 10 ** m.size_decimals + 1e-9); }
    _baseStr(m, sizeStr) {   // exact integer from Lighter's decimal string, no float rounding
      const [i, f = ''] = String(sizeStr).replace('-', '').split('.');
      return BigInt(i + (f + '0'.repeat(m.size_decimals)).slice(0, m.size_decimals)).toString();
    }
    _coi() { this._lastCoi = Math.max((this._lastCoi || 0) + 1, Date.now() % 2 ** 31); return this._lastCoi; }
    /* walk the opposite side of the book for `notional` USD: { best, vwap, base } (base in human units) */
    async _quote(m, isAsk, notional) {
      const b = await this.book(m.id, 50);
      const lvls = (isAsk ? b.bids : b.asks) || [];
      if (!lvls.length) throw new LighterError('no liquidity on Lighter for this market right now');
      let usd = 0, base = 0;
      for (const o of lvls) {
        const px = +o.price, sz = +o.remaining_base_amount;
        const take = Math.min(sz, (notional - usd) / px);
        usd += take * px; base += take;
        if (usd >= notional - 1e-9) break;
      }
      if (usd < notional * 0.999) throw new LighterError('not enough liquidity on Lighter for that size right now');
      return { best: +lvls[0].price, vwap: usd / base, base };
    }
    async _readPosition(sym, want, tries = 15) {
      for (let i = 0; i < tries; i++) {
        await sleep(i ? 500 : 300);
        let p; try { p = await this.position(sym); } catch (_) { continue; }   // a failed read is not "no position"
        if (want(p)) return p;
      }
      return undefined;
    }
    async _txStatus(hash) { try { return await this.get('/api/v1/tx', { by: 'hash', value: hash }); } catch (_) { return null; } }

    /* /v1/open {market, side, margin_usd, leverage} → {leverage, margin_usd, position:{entry,size,...}, tx_hash} */
    async open({ market, side, margin_usd, leverage }) {
      this._need();
      if (side !== 1 && side !== -1) throw new LighterError('side must be 1 or -1');
      const m = await this.market(market);
      const lev = Math.max(1, Math.min(Math.floor(leverage), this.lim.maxLeverage, m.max_leverage));
      const margin = Math.min(+margin_usd, this.lim.maxMarginUsd);
      if (!(margin > 0)) throw new LighterError('margin must be positive');
      const notional = margin * lev;
      if (notional < m.min_quote) throw new LighterError(`position too small: $${notional.toFixed(2)} < Lighter minimum $${m.min_quote.toFixed(2)}`);
      const pre = await this.positions();
      if (pre.positions.some(p => p.market === market)) throw new LighterError(`You already have a ${market} fish on Lighter`);
      if (pre.available < margin) throw new LighterError('Not enough USDC on Lighter. Add funds.');
      const isAsk = side < 0;
      const s = await this.signer();
      const res = await this._serial(async () => {
        const levTx = await this._signed(TX.UPDATE_LEVERAGE, n => s.call('_signUpdateLeverage', this.accountIndex, m.id, Math.floor(10000 / lev), ISOLATED, n));
        const q = await this._quote(m, isAsk, notional);
        const base = this._base(m, notional / q.vwap);
        if (base < Math.round(m.min_base * 10 ** m.size_decimals)) throw new LighterError(`position too small: Lighter's minimum ${market} size is ${m.min_base} (about $${(m.min_base * q.vwap).toFixed(2)})`);
        const worst = q.best * (1 + (isAsk ? -1 : 1) * this.lim.maxSlippage);
        const ord = await this._signed(TX.CREATE_ORDER, n => s.call('_signCreateOrder', this.accountIndex, m.id, this._coi(), String(base), String(this._px(m, worst)),
          isAsk ? 1 : 0, ORDER.MARKET, TIF.IOC, 0, '0', IOC_EXPIRY, n));
        return { levTx, ord };
      });
      const pos = await this._readPosition(market, p => !!p);
      if (!pos) {
        const st = await this._txStatus(res.ord.tx_hash);
        throw new LighterError('order sent but no position showed up' + (st && st.event_info ? ': ' + st.event_info : ''), 'nofill', { tx_hash: res.ord.tx_hash });
      }
      this.lev[market] = lev;
      return { position: pos, leverage: lev, margin_usd: margin, tx_hash: res.ord.tx_hash, leverage_tx_hash: res.levTx.tx_hash };
    }
    _levOf(pos) { return this.lev[pos.market] || Math.max(1, Math.round(Math.abs(pos.size * pos.entry) / Math.max(pos.margin, 1e-9))); }
    async activeOrders(m) {
      const token = await this.authToken();
      const j = await this.get('/api/v1/accountActiveOrders', { account_index: this.accountIndex, market_id: m.id }, { authorization: token });
      return j.orders || [];
    }
    /* cancel this account's resting orders of the given types on a market. `expect` is the client order index
       of a trigger we placed moments ago: Lighter lists new orders after a short delay, so wait for it to show. */
    async _cancelKind(m, kinds, expect) {
      let list = [];
      try {
        for (let i = 0; i < 8; i++) {
          const all = await this.activeOrders(m);
          list = all.filter(o => kinds.includes(o.type));
          if (!expect || all.some(o => +o.client_order_index === expect.coi) || Date.now() - expect.at > 15000) break;
          await sleep(500);
        }
      } catch (e) { this.log('active orders unavailable: ' + e.message); return 0; }
      const s = await this.signer();
      for (const o of list) {
        try { await this._signed(TX.CANCEL_ORDER, n => s.call('_signCancelOrder', this.accountIndex, m.id, String(o.order_index), n)); }
        catch (e) { this.log('cancel failed: ' + e.message); }
      }
      return list.length;
    }
    async _trigger(m, pos, frac, type) {
      const lev = this._levOf(pos);
      const trig = pos.entry * (1 + pos.side * frac / lev);
      if (!(trig > 0)) throw new LighterError('trigger price out of range');
      const worst = trig * (1 - pos.side * this.lim.maxSlippage * 3);
      const s = await this.signer();
      const key = market => `${market}:${type}`;
      return this._serial(async () => {
        const kinds = type === ORDER.STOP_LOSS ? ['stop-loss', 'stop-loss-limit'] : ['take-profit', 'take-profit-limit'];
        const cancelled = await this._cancelKind(m, kinds, this.trig[key(pos.market)]);
        const coi = this._coi();
        const r = await this._signed(TX.CREATE_ORDER, n => s.call('_signCreateOrder', this.accountIndex, m.id, coi, this._baseStr(m, pos.size_str), String(this._px(m, worst)),
          pos.side > 0 ? 1 : 0, type, TIF.IOC, 1, String(this._px(m, trig)), EXPIRY_28D, n));
        this.trig[key(pos.market)] = { coi, at: Date.now() };
        return { trigger: trig, replaced: cancelled, ...r };
      });
    }
    /* /v1/stop {market, stop_frac}: reduce-only stop-loss at entry*(1+side*frac/lev), replacing any earlier one */
    async stop({ market, stop_frac }) {
      this._need(); const m = await this.market(market);
      const pos = await this.position(market); if (!pos) throw new LighterError('no open position');
      return this._trigger(m, pos, +stop_frac, ORDER.STOP_LOSS);
    }
    /* /v1/tp {market, tp_frac}: reduce-only take-profit, replacing any earlier one */
    async tp({ market, tp_frac }) {
      this._need(); const m = await this.market(market);
      const pos = await this.position(market); if (!pos) throw new LighterError('no open position');
      return this._trigger(m, pos, +tp_frac, ORDER.TAKE_PROFIT);
    }
    /* /v1/close {market, fraction}: reduce-only market order against a fresh book */
    async close({ market, fraction = 1 }) {
      this._need(); const m = await this.market(market);
      const f = Math.min(1, Math.max(0, +fraction || 1));
      const pos = await this.position(market); if (!pos) throw new LighterError('no open position');
      const isAsk = pos.side > 0;
      const base = f >= 1 ? this._baseStr(m, pos.size_str) : String(Math.max(1, this._base(m, pos.size * f)));
      const b = await this.book(m.id, 5);
      const lv = (isAsk ? b.bids : b.asks) || [];
      if (!lv.length) throw new LighterError(`no liquidity on Lighter to close ${market} right now; your knife and net are still set`);
      const ref = +lv[0].price;
      const worst = ref * (1 - pos.side * this.lim.maxSlippage * 2);
      const s = await this.signer();
      const r = await this._serial(() => this._signed(TX.CREATE_ORDER, n => s.call('_signCreateOrder', this.accountIndex, m.id, this._coi(), base, String(this._px(m, worst)),
        isAsk ? 1 : 0, ORDER.MARKET, TIF.IOC, 1, '0', IOC_EXPIRY, n)));
      // IOC can expire unfilled on a thin book: only report success once the position actually shrank
      const after = await this._readPosition(market, p => !p || Math.abs(p.size) < Math.abs(pos.size) - 1e-12, 14);
      if (after === undefined) {
        const st = await this._txStatus(r.tx_hash);
        throw new LighterError('close order did not fill' + (st && st.event_info ? ': ' + st.event_info : ' (thin order book)'), 'nofill', { tx_hash: r.tx_hash });
      }
      if (f >= 1 && after) this.log(`close left ${after.size} ${market} open`);
      if (f >= 1) {
        delete this.lev[market]; delete this.trig[market + ':' + ORDER.STOP_LOSS]; delete this.trig[market + ':' + ORDER.TAKE_PROFIT];
        // tidy up: drop the knife and net once the fish is off the line
        if (!after) this._serial(() => this._cancelKind(m, ['stop-loss', 'stop-loss-limit', 'take-profit', 'take-profit-limit'])).catch(() => { });
      }
      return { closed: +(Math.abs(pos.size) - Math.abs(after ? after.size : 0)).toFixed(m.size_decimals), remaining: after ? after.size : 0, entry: pos.entry, upnl_before: pos.upnl, ...r };
    }

    /* the relay's REST surface, so the game's broker.call(path, body) works unchanged */
    async call(path, body) {
      switch (path) {
        case '/v1/markets': return this.allowedMarkets();
        case '/v1/prices': return this.prices();
        case '/v1/positions': return this.positions();
        case '/v1/open': return this.open(body || {});
        case '/v1/stop': return this.stop(body || {});
        case '/v1/tp': return this.tp(body || {});
        case '/v1/close': return this.close(body || {});
        case '/v1/health': return { ok: true, lighter: this.url, account: this.accountIndex, configured: this.accountIndex != null && !!this.publicKey, limits: { max_margin_usd: this.lim.maxMarginUsd, max_leverage: this.lim.maxLeverage, markets: [...this.allowed] } };
        default: throw new LighterError('unknown call ' + path);
      }
    }
  }

  return { NETWORKS, DEFAULTS, TX, ORDER, TIF, LighterClient, LighterError, loadWasmSigner, randomSeed, version: 1 };
});
