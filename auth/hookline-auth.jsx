// HookLine sign-in island: Privy (Google / email) + an embedded EVM wallet, with no visible React UI of its own.
// Built by build.mjs into ../game/hookline-auth/ (ES module + lazy chunks; game/hookline-real.js loads it).
// The game never imports React: it only talks to
//   window.HookLineAuth = { ready(), isReady(), error(), login(), logout(), getWallet(), getUser(), signMessage(msg),
//                           sendTransaction(tx, opts), depositFunds(opts), fundWallet(opts) }
// The Privy app id comes from window.HOOKLINE_PRIVY_APP_ID (or <meta name="privy-app-id" content="...">).
import React, { useEffect, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import {
  PrivyProvider, usePrivy, useLogin, useLogout, useWallets, useSignMessage, useSendTransaction, useCreateWallet,
  useFundWallet, useDepositFunds,
} from '@privy-io/react-auth';

const appId = () => window.HOOKLINE_PRIVY_APP_ID || document.querySelector('meta[name="privy-app-id"]')?.content || '';
const clientId = () => window.HOOKLINE_PRIVY_CLIENT_ID || document.querySelector('meta[name="privy-client-id"]')?.content || undefined;
const wait = ms => new Promise(r => setTimeout(r, ms));

let readyResolve, readyReject;
const readyP = new Promise((res, rej) => { readyResolve = res; readyReject = rej; });
readyP.catch(() => { });
let initError = null;
const fail = err => { initError = err instanceof Error ? err : new Error(String(err)); readyReject(initError); };
const H = { privy: null, wallets: [], walletsReady: false, sign: null, send: null, create: null, fund: null, deposit: null, logout: null, login: null, pendingLogin: null };

// the embedded (Privy) EOA: never a smart wallet, never an injected wallet
const embedded = () => H.wallets.find(w => w.walletClientType === 'privy') || null;   // useWallets() lists EVM wallets only

async function waitForWallet(ms) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { const w = embedded(); if (w) return w; await wait(250); }
  return null;
}

function Bridge() {
  const privy = usePrivy();
  const { wallets, ready: walletsReady } = useWallets();
  const { signMessage } = useSignMessage();
  const { sendTransaction } = useSendTransaction();
  const { createWallet } = useCreateWallet();
  const { logout } = useLogout();
  const fund = useFundWallet();
  const dep = useDepositFunds();
  const { login } = useLogin({
    onComplete: async () => {
      const p = H.pendingLogin; if (!p) return;
      let w = await waitForWallet(12000);
      if (!w) { try { await H.create(); } catch (_) { } w = await waitForWallet(12000); }
      H.pendingLogin = null;
      if (w) p.resolve({ address: w.address }); else p.reject(new Error('Signed in, but the wallet was not created. Try again.'));
    },
    onError: err => { const p = H.pendingLogin; H.pendingLogin = null; if (p) p.reject(new Error(err === 'exited_auth_flow' ? 'Sign-in was cancelled.' : 'Sign-in failed: ' + err)); },
  });
  // keep the latest hook functions in refs so the plain-JS API always calls the current ones
  const ref = useRef(H);
  Object.assign(ref.current, {
    privy, wallets, walletsReady, sign: signMessage, send: sendTransaction, create: createWallet, logout, login,
    fund: fund && fund.fundWallet, deposit: dep && dep.depositFunds,
  });
  useEffect(() => { if (privy.ready && walletsReady) readyResolve(true); }, [privy.ready, walletsReady]);
  return null;
}

const api = {
  version: 1,
  appId: appId(),
  ready: () => readyP,
  isReady: () => !!(H.privy && H.privy.ready && H.walletsReady),
  error: () => initError && initError.message,
  async login() {
    await readyP;
    if (H.privy.authenticated) {
      let w = embedded();
      if (!w) { try { await H.create(); } catch (_) { } w = await waitForWallet(12000); }
      if (w) return { address: w.address };
    }
    if (H.pendingLogin) H.pendingLogin.reject(new Error('Sign-in restarted.'));
    return new Promise((resolve, reject) => { H.pendingLogin = { resolve, reject }; H.login(); });
  },
  async logout() { await readyP; try { await H.logout(); } catch (_) { } },
  getWallet() { const w = embedded(); return H.privy && H.privy.authenticated && w ? { address: w.address } : null; },
  getUser() {
    const u = H.privy && H.privy.user; if (!u || !H.privy.authenticated) return null;
    return { id: u.id, email: (u.email && u.email.address) || (u.google && u.google.email) || null, google: !!u.google, name: (u.google && u.google.name) || null };
  },
  // EIP-191 personal_sign with the embedded wallet, no Privy confirmation modal
  async signMessage(message) {
    await readyP; const w = embedded(); if (!w) throw new Error('No wallet: sign in first.');
    const { signature } = await H.sign({ message }, { address: w.address, uiOptions: { showWalletUIs: false } });
    return signature;
  },
  async sendTransaction(tx, opts = {}) {
    await readyP; const w = embedded(); if (!w) throw new Error('No wallet: sign in first.');
    return H.send(tx, { address: w.address, ...opts });
  },
  // Privy's deposit modal (card / crypto). Needs Account Funding enabled in the Privy dashboard.
  async depositFunds(opts) { await readyP; if (!H.deposit) throw new Error('Privy deposits are not available in this SDK build.'); return H.deposit(opts); },
  async fundWallet(opts = {}) { await readyP; const w = embedded(); if (!w) throw new Error('No wallet: sign in first.'); if (!H.fund) throw new Error('Privy funding is not available.'); return H.fund({ address: w.address, options: opts }); },
};

// Privy throws while rendering if the app id is wrong or the page is not https: report it instead of hanging
class Guard extends React.Component {
  constructor(p) { super(p); this.state = { dead: false }; }
  static getDerivedStateFromError() { return { dead: true }; }
  componentDidCatch(err) { console.error('[HookLineAuth] Privy failed to start:', err && err.message); fail(new Error('Sign-in is unavailable: ' + ((err && err.message) || err))); }
  render() { return this.state.dead ? null : this.props.children; }
}

function mount() {
  const id = appId();
  if (!id) { console.warn('[HookLineAuth] no Privy app id (window.HOOKLINE_PRIVY_APP_ID): not mounting'); fail(new Error('Sign-in is not configured (no Privy app id).')); return; }
  const el = document.createElement('div'); el.id = 'hookline-auth-root'; document.body.appendChild(el);
  createRoot(el).render(
    <Guard><PrivyProvider appId={id} clientId={clientId()} config={{
      loginMethods: ['google', 'email'],
      appearance: { theme: 'dark', accentColor: '#3ddc97', walletChainType: 'ethereum-only', showWalletLoginFirst: false },
      embeddedWallets: { ethereum: { createOnLogin: 'users-without-wallets' }, showWalletUIs: false },
    }}>
      <Bridge />
    </PrivyProvider></Guard>,
  );
}

window.HookLineAuth = api;
if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount, { once: true });
