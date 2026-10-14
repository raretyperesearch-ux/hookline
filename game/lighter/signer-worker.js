/* Runs Lighter's Go→WASM signer off the main thread so signing never stalls the game's frame loop.
   Protocol: postMessage({ id, name, args }) → { id, ok, r } | { id, ok:false, e }.
   The API private key reaches this worker only to sign; it is never sent anywhere else. */
/* global importScripts, LighterCore */
importScripts('wasm_exec.js', 'lighter-core.js');
const ready = LighterCore.loadWasmSigner({ url: 'lighter-signer.wasm.gz' });
ready.then(() => postMessage({ id: 0, ok: true, r: 'ready' }), e => postMessage({ id: 0, ok: false, e: String(e && e.message || e) }));
self.onmessage = async ev => {
  const { id, name, args } = ev.data || {};
  try { const s = await ready; const r = await s.call(name, ...(args || [])); postMessage({ id, ok: true, r }); }
  catch (e) { postMessage({ id, ok: false, e: String(e && e.message || e) }); }
};
