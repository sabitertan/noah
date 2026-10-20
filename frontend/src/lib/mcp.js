// MCP servers for the browser build: the page is the MCP client (the official TypeScript SDK), because Noah's Python runs in
// a worker with no sockets or threads. Python's requests (noah/mcp_client.py: list_tools, call_tool, disconnect) arrive
// through wasm/bridge.js at `serve()`. Only remote servers work, and only ones that allow this page's origin (CORS).
//
// Sign-in (OAuth) is the SDK's: discovery, client registration, PKCE, refresh. `signIn()` opens the server's sign-in page in
// a popup; it comes back to oauth-callback.html (public/), which hands the code over here. Tokens stay in this browser
// (localStorage), per server. A server that needs signing in while Noah answers a message says so instead of opening a popup.
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { UnauthorizedError } from '@modelcontextprotocol/sdk/client/auth.js';
import { forget, readAuth, writeAuth } from './mcpAuth.js';

const clients = new Map();   // server key -> Promise<Client>
const keyOf = server => JSON.stringify(server);
const hasAuthHeader = server => Object.keys(server.headers || {}).some(k => k.toLowerCase() === 'authorization');   // a token given by hand wins

class SignInNeeded extends Error {}

/** The SDK's OAuthClientProvider, kept in localStorage. Only `interactive` (the Sign in button) may send the user anywhere. */
class Auth {
  constructor(url, interactive) { Object.assign(this, { url, interactive, pending: null }); }
  get redirectUrl() { return new URL('oauth-callback.html', document.baseURI).href; }
  get clientMetadata() {
    return { client_name: 'Noah', redirect_uris: [this.redirectUrl], grant_types: ['authorization_code', 'refresh_token'],
      response_types: ['code'], token_endpoint_auth_method: 'none' };
  }
  state() { return (this.stateValue ||= crypto.randomUUID()); }
  clientInformation() {
    const c = readAuth(this.url).client;
    return c && (!c.redirect_uris || c.redirect_uris.includes(this.redirectUrl)) ? c : undefined;   // registered for another address: again
  }
  saveClientInformation(client) { writeAuth(this.url, { client }); }
  tokens() { return readAuth(this.url).tokens; }
  saveTokens(tokens) { writeAuth(this.url, { tokens }); }
  saveCodeVerifier(verifier) { writeAuth(this.url, { verifier }); }
  codeVerifier() { return readAuth(this.url).verifier; }
  invalidateCredentials(scope) { forget(this.url, scope); }
  redirectToAuthorization(url) {
    if (!this.interactive) throw new SignInNeeded(`${this.url} needs you to sign in. Open this expert on the Experts page and press Sign in.`);
    this.pending = url;
  }
}

function transport(server, auth) {
  return new StreamableHTTPClientTransport(new URL(server.url), {
    requestInit: { headers: server.headers || {} }, authProvider: hasAuthHeader(server) ? undefined : auth });
}

async function open(server) {
  const client = new Client({ name: 'noah', version: '1.0.0' });
  await client.connect(transport(server, new Auth(server.url, false)));
  return client;
}

function connection(server) {
  const k = keyOf(server);
  if (!clients.has(k)) clients.set(k, open(server).catch(e => { clients.delete(k); throw e; }));
  return clients.get(k);
}

async function drop(server) {
  const c = clients.get(keyOf(server));
  clients.delete(keyOf(server));
  try { await (await c)?.close(); } catch { /* already gone */ }
}

function explain(e, server) {
  let cause = e;
  while (cause?.cause && !(cause instanceof SignInNeeded)) cause = cause.cause;
  if (cause instanceof SignInNeeded || e instanceof UnauthorizedError) {
    return { error: `${server.url} needs you to sign in. Open this expert on the Experts page and press Sign in.`, sign_in: true };
  }
  if (e instanceof TypeError && /fetch/i.test(e.message)) {
    return { error: `Could not reach ${server.url} from the browser. Is it up, and does it allow this page (CORS)?` };
  }
  return { error: `The MCP server (${server.url}) failed: ${e.message || e}` };
}

const within = (promise, ms, what) => Promise.race([promise, new Promise((_, no) => setTimeout(() => no(new Error(`${what} took longer than ${Math.round(ms / 1000)} s`)), ms))]);

/** One request from Noah's Python: {op, server, tool?, arguments?} -> JSON {result} or {error, sign_in?}. */
export async function serve(json, ms) {
  const { op, server, tool, arguments: args } = JSON.parse(json);
  if (!server.url) return JSON.stringify({ error: 'Local MCP servers (command) need the server or desktop version of Noah. In the browser, only remote servers (url) work.' });
  if (op === 'disconnect') { await drop(server); return JSON.stringify({ result: null }); }
  for (const attempt of [1, 2]) {   // a kept session may have expired on the server: reconnect once
    try {
      const client = await within(connection(server), ms, 'Connecting');
      if (op === 'list_tools') {
        const tools = [];
        let cursor;
        do { const page = await within(client.listTools(cursor ? { cursor } : undefined), ms, 'Listing tools'); tools.push(...page.tools); cursor = page.nextCursor; } while (cursor);
        return JSON.stringify({ result: tools });
      }
      if (op === 'call_tool') return JSON.stringify({ result: await within(client.callTool({ name: tool, arguments: args || {} }), ms, tool) });
      return JSON.stringify({ error: `unknown operation ${op}` });
    } catch (e) {
      await drop(server);
      const out = explain(e, server);
      if (attempt === 2 || out.sign_in || out.error.startsWith('Could not reach')) return JSON.stringify(out);
    }
  }
}

/** The Sign in button: `popup` was opened on the click (so it is not blocked) and is sent to the server's sign-in page. */
export async function signIn(server, popup) {
  const auth = new Auth(server.url, true);
  const t = transport(server, auth);
  await drop(server);
  try {
    const client = new Client({ name: 'noah', version: '1.0.0' });
    await client.connect(t);   // already signed in (or no sign-in needed): nothing to do
    clients.set(keyOf(server), Promise.resolve(client));
    popup?.close();
    return;
  } catch (e) {
    if (!auth.pending) { popup?.close(); throw new Error(explain(e, server).error); }
  }
  popup.location.href = auth.pending.href;
  const code = await new Promise((resolve, reject) => {
    const channel = new BroadcastChannel('noah-mcp-oauth');
    const done = (f, v) => { channel.close(); clearInterval(watch); f(v); };
    channel.onmessage = ({ data }) => {
      if (!data?.noahMcpSignIn || data.state !== auth.state()) return;
      if (data.error) done(reject, new Error(data.error)); else done(resolve, data.code);
    };
    const watch = setInterval(() => { if (popup.closed) done(reject, new Error('The sign-in window was closed before signing in finished.')); }, 500);
  });
  await t.finishAuth(code);
  await connection(server);   // with the new tokens
}

export async function signOut(server) {
  forget(server.url, 'all');
  await drop(server);
}
