// MCP sign-in, for both builds. The server build signs in on the server (noah/mcp_client.py: /api/mcp/<name>/signin, its
// callback route keeps the tokens); the browser build signs in in the page (lib/mcp.js, tokens in localStorage, here).
// Either way a popup shows the MCP server's sign-in page and reports back on the 'noah-mcp-oauth' channel.
import { send } from './api.js';
import { IS_WASM } from '../store.js';

const KEY = url => 'noah.mcp.oauth.' + url;
export const readAuth = url => { try { return JSON.parse(localStorage.getItem(KEY(url)) || '{}'); } catch { return {}; } };
export function writeAuth(url, part) { try { localStorage.setItem(KEY(url), JSON.stringify({ ...readAuth(url), ...part })); } catch { /* private window */ } }
export function forget(url, scope = 'all') {
  if (scope === 'all') { try { localStorage.removeItem(KEY(url)); } catch { /* private window */ } return; }
  const field = { tokens: 'tokens', client: 'client', verifier: 'verifier' }[scope];
  if (field) { const a = readAuth(url); delete a[field]; try { localStorage.setItem(KEY(url), JSON.stringify(a)); } catch { /* */ } }
}

/** Whether this MCP expert has tokens: from the server's config, or from this browser's storage. */
export const signedIn = e => IS_WASM ? !!readAuth(e.url).tokens : !!e.signed_in;

const openPopup = () => window.open('about:blank', 'noah-mcp-signin', 'width=520,height=700');

function waitForServer(popup) {   // the server build: Noah's callback route posts {noahMcpSignIn, ok, message}
  return new Promise((resolve, reject) => {
    const channel = new BroadcastChannel('noah-mcp-oauth');
    const done = (f, v) => { channel.close(); clearInterval(watch); window.removeEventListener('message', onMessage); f(v); };
    const handle = data => { if (data?.noahMcpSignIn && 'ok' in data) done(data.ok ? resolve : reject, data.ok ? undefined : new Error(data.message)); };
    const onMessage = ({ data, origin }) => { if (origin === location.origin) handle(data); };
    channel.onmessage = ({ data }) => handle(data);
    window.addEventListener('message', onMessage);
    const watch = setInterval(() => { if (popup.closed) done(reject, new Error('The sign-in window was closed before signing in finished.')); }, 500);
  });
}

/** Sign in to an MCP expert's server (call from a click), then list its tools. Resolves with the refresh result. */
export async function signIn(e) {
  const popup = openPopup();
  if (!popup) throw new Error('The browser blocked the sign-in window. Allow popups for this page and try again.');
  try {
    if (IS_WASM) await (await import('./mcp.js')).signIn({ url: e.url }, popup);
    else {
      const r = await send(`/api/mcp/${encodeURIComponent(e.name)}/signin`, 'POST', {});
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || 'could not start signing in');
      if (j.url) { popup.location.href = j.url; await waitForServer(popup); } else popup.close();
    }
  } catch (err) { if (!popup.closed) popup.close(); throw err; }
  const r = await send(`/api/mcp/${encodeURIComponent(e.name)}/refresh`, 'POST', {});
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail || 'signed in, but listing the tools failed');
  return j;
}

export async function signOut(e) {
  if (IS_WASM) await (await import('./mcp.js')).signOut({ url: e.url });
  else await fetch(`/api/mcp/${encodeURIComponent(e.name)}/signin`, { method: 'DELETE' });
}
