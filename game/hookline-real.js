/* HookLine "play for real" adapter: window.HookLineReal for the game (same interface as its built-in demo).
   Sign in with Privy (Google / email → embedded EVM wallet), fund the Lighter account, register a trading
   key for this device, then trade on Lighter straight from the browser:
     - hookline-auth/hookline-auth.js  Privy island (window.HookLineAuth), loaded here as an ES module
     - lighter/lighter-core.js         Lighter REST + signing (shared with the Node testnet test)
     - lighter/signer-worker.js        Lighter's official WASM signer, in a Web Worker
   The Lighter API private key is generated on this device, stored only in IndexedDB encrypted with a
   non-extractable WebCrypto AES key, and never sent to any server (HookLine's included).
   Only defines window.HookLineReal when a Privy app id is configured; otherwise the game keeps its demo.

   Page config (set before this script):
     window.HOOKLINE_PRIVY_APP_ID = '...'      (or <meta name="privy-app-id" content="...">)   required
     window.HOOKLINE_LIGHTER_NET  = 'testnet'  ('mainnet' later: deposits there are still a TODO)
     window.HOOKLINE_API          = ''         base URL of the HookLine server for /api/faucet (default: this site) */
(function () {
  'use strict';
  const meta = n => { const m = document.querySelector(`meta[name="${n}"]`); return m ? m.content : ''; };
  const APP_ID = window.HOOKLINE_PRIVY_APP_ID || meta('privy-app-id');
  if (!APP_ID) { console.info('[HookLine] play for real: no Privy app id configured, the game keeps its demo adapter'); return; }
  window.HOOKLINE_PRIVY_APP_ID = APP_ID;

  const HERE = (document.currentScript && document.currentScript.src) || location.href;
  const asset = p => new URL(p, HERE).href;
  const CFG = {
    net: window.HOOKLINE_LIGHTER_NET || 'testnet',
    apiKeyIndex: 5,
    limits: { maxMarginUsd: 25, maxLeverage: 10 },
    api: String(window.HOOKLINE_API || new URL('/', HERE).href).replace(/\/+$/, ''),
  };
  const LIMITS = { maxMargin: CFG.limits.maxMarginUsd, maxLev: CFG.limits.maxLeverage };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const withTimeout = (p, ms, msg) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(msg || 'timed out')), ms))]);
  const log = (...a) => { if (window.HOOKLINE_DEBUG) console.log('[HookLineReal]', ...a); };

  // ---------------------------------------------------------------- loading
  const loadScript = (src, module) => new Promise((res, rej) => {
    const s = document.createElement('script'); s.src = src; if (module) s.type = 'module'; else s.async = true;
    s.onload = () => res(); s.onerror = () => rej(new Error('could not load ' + src)); document.head.appendChild(s);
  });
  const coreP = (window.LighterCore ? Promise.resolve() : loadScript(asset('lighter/lighter-core.js'))).then(() => window.LighterCore);
  const authLoadP = window.HookLineAuth ? Promise.resolve() : loadScript(asset('hookline-auth/hookline-auth.js'), true);
  const authP = authLoadP.then(() => { if (!window.HookLineAuth) throw new Error('sign-in did not load'); return window.HookLineAuth; });
  const authReadyP = authP.then(a => a.ready().then(() => a));
  coreP.catch(e => console.warn('[HookLineReal]', e.message)); authReadyP.catch(e => console.warn('[HookLineReal]', e.message));

  // Lighter's signer: a Web Worker when possible (signing never blocks a frame), else the main thread
  let signerP = null;
  function signer() {
    if (signerP) return signerP;
    signerP = (async () => {
      if (typeof Worker === 'function') {
        try {
          const w = new Worker(asset('lighter/signer-worker.js'));
          const calls = new Map(); let seq = 1, ready;
          const readyP = new Promise((res, rej) => { ready = { res, rej }; });
          w.onmessage = ev => {
            const { id, ok, r, e } = ev.data || {};
            if (id === 0) { ok ? ready.res() : ready.rej(new Error(e)); return; }
            const c = calls.get(id); if (!c) return; calls.delete(id); ok ? c.res(r) : c.rej(new Error(e));
          };
          w.onerror = ev => { ready.rej(new Error(ev.message || 'signer worker failed')); };
          await withTimeout(readyP, 30000, 'Lighter signer did not start');
          return { kind: 'worker', call: (name, ...args) => new Promise((res, rej) => { const id = seq++; calls.set(id, { res, rej }); w.postMessage({ id, name, args }); }) };
        } catch (e) { console.warn('[HookLineReal] signer worker unavailable, signing on the main thread:', e.message); }
      }
      if (typeof window.Go !== 'function') await loadScript(asset('lighter/wasm_exec.js'));
      const LC = await coreP;
      return LC.loadWasmSigner({ url: asset('lighter/lighter-signer.wasm.gz') });
    })();
    signerP.catch(() => { signerP = null; });
    return signerP;
  }

  let client = null;
  async function lc() {
    if (client) return client;
    const LC = await coreP;
    client = new LC.LighterClient({ net: CFG.net, signer, apiKeyIndex: CFG.apiKeyIndex, limits: CFG.limits, log });
    return client;
  }

  // ---------------------------------------------------------------- key storage (IndexedDB, AES-GCM, non-extractable)
  const KS = {
    _db: null,
    db() {
      if (this._db) return this._db;
      this._db = new Promise((res, rej) => {
        const r = indexedDB.open('hookline-real', 1);
        r.onupgradeneeded = () => r.result.createObjectStore('kv');
        r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error || new Error('IndexedDB unavailable'));
      });
      this._db.catch(() => { this._db = null; });
      return this._db;
    },
    async op(mode, fn) { const db = await this.db(); return new Promise((res, rej) => { const tx = db.transaction('kv', mode); const r = fn(tx.objectStore('kv')); tx.oncomplete = () => res(r && r.result); tx.onerror = () => rej(tx.error); tx.onabort = () => rej(tx.error); }); },
    get(k) { return this.op('readonly', s => s.get(k)); },
    put(k, v) { return this.op('readwrite', s => s.put(v, k)); },
    del(k) { return this.op('readwrite', s => s.delete(k)); },
    async aes() {
      let key = await this.get('aes');
      if (!key) { key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']); await this.put('aes', key); }
      return key;
    },
    async save(id, privateKey, publicKey) {
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, await this.aes(), new TextEncoder().encode(privateKey));
      await this.put('key:' + id, { iv, ct, pub: publicKey, at: Date.now() });
    },
    async load(id) {
      const rec = await this.get('key:' + id); if (!rec) return null;
      try { const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: rec.iv }, await this.aes(), rec.ct); return { prv: new TextDecoder().decode(pt), pub: rec.pub }; }
      catch (_) { await this.del('key:' + id).catch(() => { }); return null; }
    },
    async pub(id) { const rec = await this.get('key:' + id); return rec ? rec.pub : null; },
  };
  const keyId = (addr, index) => `${CFG.net}:${String(addr).toLowerCase()}:${index}:${CFG.apiKeyIndex}`;

  // ---------------------------------------------------------------- state
  const st = { addr: null, acct: null, acctAt: 0, keyOk: {}, want: false, poll: null, keyP: null };
  const pendKey = addr => `hookline-real-pending:${CFG.net}:${String(addr).toLowerCase()}`;
  const getPending = addr => { try { const p = JSON.parse(localStorage.getItem(pendKey(addr)) || 'null'); return p && Date.now() - p.at < 45 * 60e3 ? p : null; } catch (_) { return null; } };
  const setPending = (addr, p) => { try { p ? localStorage.setItem(pendKey(addr), JSON.stringify(p)) : localStorage.removeItem(pendKey(addr)); } catch (_) { } };

  const wallet = () => (window.HookLineAuth && window.HookLineAuth.getWallet()) || null;
  async function account(addr, fresh) {
    if (!fresh && st.addr === addr && Date.now() - st.acctAt < 4000) return st.acct;
    const c = await lc(); const a = await c.accountByL1(addr);
    st.addr = addr; st.acct = a; st.acctAt = Date.now(); return a;
  }
  async function balanceOf(index) { const c = await lc(); return c.summary(index); }

  // is there a working key for this wallet's account on this device? (no WASM needed to answer)
  async function hasKey(addr, index) {
    const id = keyId(addr, index);
    if (st.keyOk[id] != null && Date.now() - st.keyOk[id].at < 60e3) return st.keyOk[id].ok;
    let ok = false;
    try { const pub = await KS.pub(id); ok = !!pub && await (await lc()).keyIsRegistered(pub, index); } catch (_) { ok = false; }
    st.keyOk[id] = { ok, at: Date.now() }; return ok;
  }
  // load this device's key into the signer, registering a fresh one (one silent wallet signature) if needed
  function ensureKey(addr, index) {
    const c = client, id = keyId(addr, index);
    if (c && c.accountIndex === index && c.publicKey && st.keyId === id) return Promise.resolve();
    if (st.keyP && st.keyP.id === id) return st.keyP.p;
    const p = (async () => {
      const c = await lc(), auth = await authReadyP;
      const rec = await KS.load(id).catch(() => null);
      if (rec && await c.keyIsRegistered(rec.pub, index).catch(() => false)) { await c.useKey({ accountIndex: index, privateKey: rec.prv }); }
      else {
        const reg = await c.registerKey({ accountIndex: index, signMessage: msg => auth.signMessage(msg), onStep: s => log('register key:', s) });
        await KS.save(id, reg.privateKey, reg.publicKey);
        log('trading key registered', reg.tx_hash);
      }
      st.keyId = id; st.keyOk[id] = { ok: true, at: Date.now() };
    })();
    st.keyP = { id, p }; p.finally(() => { if (st.keyP && st.keyP.p === p) st.keyP = null; }).catch(() => { });
    return p;
  }

  // background: watch for a deposit to land, then (if asked) turn trading on and tell the game
  function watchDeposit(addr) {
    if (st.poll) return; const t0 = Date.now();
    st.poll = setInterval(async () => {
      const p = getPending(addr);
      if (!p || Date.now() - t0 > 45 * 60e3 || !wallet() || wallet().address !== addr) { clearInterval(st.poll); st.poll = null; return; }
      try {
        const a = await account(addr, true); if (!a) return;
        const b = await balanceOf(a.index);
        if (b.collateral > p.base + 0.01) {
          clearInterval(st.poll); st.poll = null; setPending(addr, null);
          if (st.want) { st.want = false; try { await ensureKey(addr, a.index); } catch (e) { console.warn('[HookLineReal] could not turn on trading:', e.message); } }
          if (window.onHookLineDeposit) window.onHookLineDeposit(+(b.collateral - p.base).toFixed(2));
        }
      } catch (_) { }
    }, 4000);
  }

  // ---------------------------------------------------------------- broker (the relay's /v1/* surface, run locally)
  const broker = {
    ok: true, demo: false, net: CFG.net, markets: null,
    async call(path, body) {
      const c = await lc();
      if (path === '/v1/markets' || path === '/v1/prices') return c.call(path, body);
      const w = wallet(); if (!w) throw new Error('Sign in to play for real.');
      const a = await account(w.address); if (!a) throw new Error('No Lighter account yet: add funds first.');
      await ensureKey(w.address, a.index);
      return c.call(path, body);
    },
  };
  coreP.then(LC => { const n = LC.NETWORKS[CFG.net]; broker.markets = n && n.markets ? new Set(n.markets) : null; }).catch(() => { });

  // ---------------------------------------------------------------- the adapter
  window.HookLineReal = {
    demo: false,
    net: CFG.net,
    broker: () => broker,

    async status() {
      const out = { ready: false, signedIn: false, address: null, who: null, account: null, balance: 0, pending: 0, trading: false, limits: LIMITS, net: CFG.net };
      try { await withTimeout(Promise.all([coreP, authReadyP]), 15000, 'sign-in is still loading'); } catch (e) { out.error = e.message; return out; }
      out.ready = true;
      const w = wallet(); if (!w) return out;
      const u = window.HookLineAuth.getUser();
      Object.assign(out, { signedIn: true, address: w.address, who: (u && (u.email || u.name)) || 'Signed in' });
      try {
        const a = await account(w.address);
        const p = getPending(w.address);
        if (a) {
          out.account = a.index;
          const b = await balanceOf(a.index);
          out.balance = b.collateral; out.available = b.available;
          out.trading = await hasKey(w.address, a.index);
          if (p && b.collateral > p.base + 0.01) setPending(w.address, null);
          else if (p) { out.pending = p.amount; watchDeposit(w.address); }
        } else if (p) { out.pending = p.amount; watchDeposit(w.address); }
      } catch (e) { out.error = 'Lighter is not answering: ' + e.message; }
      return out;
    },

    async login() {
      const auth = await withTimeout(authReadyP, 20000, 'Sign-in is still loading. Try again in a moment.');
      const r = await auth.login();
      st.acctAt = 0;
      return r;
    },

    /* testnet: free test USDC from Lighter's faucet (via the HookLine server). mainnet: TODO (see server.js). */
    async fund(amountUsd, method, prog) {
      prog = prog || (() => { });
      const w = wallet(); if (!w) throw new Error('Sign in first.');
      const LC = await coreP; const net = LC.NETWORKS[CFG.net];
      if (!net.faucet) {
        // TODO(mainnet): card → Privy on-ramp / crypto → Lighter Universal Deposit Address from POST /api/deposit-address,
        // then setPending() + watchDeposit() exactly like the faucet path below.
        throw new Error('Real-money deposits are not switched on yet.');
      }
      const before = await account(w.address, true);
      const base = before ? (await balanceOf(before.index)).collateral : 0;
      prog(0.15, 'Testnet: asking Lighter\'s faucet for free test USDC…');
      const r = await fetch(`${CFG.api}/api/faucet?addr=${w.address}`).then(r => r.json().catch(() => ({ ok: false, error: 'HTTP ' + r.status }))).catch(e => ({ ok: false, error: e.message }));
      if (!r.ok) throw new Error(r.error ? `Faucet: ${r.error}` : 'The testnet faucet did not answer. Try again in a minute.');
      setPending(w.address, { amount: amountUsd, base, at: Date.now() });
      prog(0.4, 'Test USDC is on its way to Lighter…');
      const t0 = Date.now();
      while (Date.now() - t0 < 45000) {
        await sleep(3000);
        prog(Math.min(0.95, 0.4 + (Date.now() - t0) / 45000 * 0.55), 'Waiting for Lighter to credit your account…');
        try {
          const a = await account(w.address, true);
          if (a) { const b = await balanceOf(a.index); if (b.collateral > base + 0.01) { setPending(w.address, null); prog(1, 'On Lighter ✓'); return { pending: false, amount: +(b.collateral - base).toFixed(2) }; } }
        } catch (_) { }
      }
      watchDeposit(w.address);
      return { pending: true };
    },

    async enableTrading() {
      const w = wallet(); if (!w) throw new Error('Sign in first.');
      const a = await account(w.address, true);
      if (!a) { st.want = true; return { pending: true }; }   // turned on as soon as the account exists (see watchDeposit)
      await ensureKey(w.address, a.index);
      return { ok: true, account: a.index };
    },

    signOut() {
      const w = wallet(), a = st.acct;
      if (w && a) KS.del('key:' + keyId(w.address, a.index)).catch(() => { });   // the key is useless without the wallet; don't leave it behind
      if (client) { client.accountIndex = null; client.publicKey = null; }
      if (st.poll) { clearInterval(st.poll); st.poll = null; }
      Object.assign(st, { addr: null, acct: null, acctAt: 0, keyOk: {}, keyId: null, want: false });
      if (window.HookLineAuth) window.HookLineAuth.logout();
    },

    // for debugging / tests
    _debug: { CFG, KS, lc, signer, account, ensureKey },
  };
})();
