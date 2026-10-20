// Build target "wasm": there is no server, so every fetch of /api/... is answered by Python in a Web Worker (worker.js)
// instead of the network. The components keep calling fetch('/api/...') exactly as they do against the real server.
let worker = null;

function splash() {
  const el = document.createElement('div');
  el.id = 'wasm-boot';
  el.innerHTML = '<div><h2>Noah</h2><p class="lede">Runs entirely in your browser.</p><p id="wasm-boot-status">Starting…</p></div>';
  document.body.appendChild(el);
  return { set: text => { el.querySelector('#wasm-boot-status').textContent = text; }, fail: text => {
    el.classList.add('failed');
    el.querySelector('#wasm-boot-status').textContent = `Could not start: ${text}. Reload to try again; a recent Chrome, Edge or Firefox works best.`;
  }, done: () => el.remove() };
}

// Python (in the worker) needs what only the page has: a `web:` model (WebLLM, lib/webllm.js) or an MCP server (lib/mcp.js)
const PAGE = {
  llm: { load: () => import('../lib/webllm.js'), failed: 'The in-browser model failed' },
  mcp: { load: () => import('../lib/mcp.js'), failed: 'The MCP client failed' },
};
async function forPython({ kind, id, body, ms }) {
  let reply;
  try { reply = await (await PAGE[kind].load()).serve(body, ms); }
  catch (e) { reply = JSON.stringify({ error: `${PAGE[kind]?.failed || 'The page failed'}: ${e.message || e}` }); }
  worker.postMessage({ type: 'page-reply', id, reply });
}

export function install() {
  const screen = splash();
  worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
  const pending = new Map();
  let next = 0;
  const ready = new Promise((resolve, reject) => {
    worker.onmessage = ({ data }) => {
      if (data.type === 'status') screen.set(data.text);
      else if (data.type === 'ready') { screen.done(); resolve(); }
      else if (data.type === 'failed') { screen.fail(data.text); reject(new Error(data.text)); }
      else if (data.type === 'page') forPython(data);   // Python calls an in-browser model or an MCP server
      else { pending.get(data.id)?.(data); pending.delete(data.id); }
    };
  });
  worker.postMessage({ type: 'boot', zip: new URL('noah-py.zip', document.baseURI).href });

  const network = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const req = new Request(input, init);
    const url = new URL(req.url);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return network(input, init);
    await ready;
    const body = req.method === 'GET' || req.method === 'HEAD' ? new ArrayBuffer(0) : await req.arrayBuffer();
    const id = ++next;
    const r = await new Promise(resolve => {
      pending.set(id, resolve);
      worker.postMessage({ id, method: req.method, url: url.pathname + url.search, headers: [...req.headers], body }, [body]);
    });
    return new Response([204, 304].includes(r.status) ? null : r.body, { status: r.status, headers: r.headers });
  };
}
