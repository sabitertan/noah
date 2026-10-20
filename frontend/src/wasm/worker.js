// Noah's Python in the browser (build target "wasm"). Pyodide, CPython compiled to WebAssembly, runs the same FastAPI app as
// the server (noah/wasm.py), and this worker hands it the page's /api requests (bridge.js). The user's experts, settings and
// versions live in the browser's IndexedDB, mounted at /noah and saved back after every request that changes something.
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/';   // Python 3.14, like the server
const BUILT_IN = ['micropip', 'fastapi', 'pydantic', 'httpx', 'pyyaml', 'numpy', 'msgspec', 'regex', 'typing-extensions'];  // prebuilt by Pyodide
const FROM_PYPI = ['agent-framework-core==1.21.0', 'agent-framework-orchestrations==1.3.1', 'pyflakes==4.0.3', 'pypdf==6.20.1'];  // pure Python

let py = null, noah = null;
const status = text => postMessage({ type: 'status', text });

// In-browser models (`web:<id>`, noah/webllm.py): WebLLM runs in the page, so Python asks the page and waits for the answer
// with run_sync (JSPI suspends the WebAssembly stack; this worker's event loop keeps running and receives the reply).
const llmWaiting = new Map();
let llmNext = 0;
self.noahWebLLM = (body, ms) => new Promise(resolve => {
  const id = ++llmNext;
  llmWaiting.set(id, resolve);
  postMessage({ type: 'llm', id, body, ms });
});

let saving = Promise.resolve();
const sync = populate => (saving = saving.then(() => new Promise((ok, fail) => py.FS.syncfs(populate, e => (e ? fail(e) : ok())))));

async function boot(zipUrl) {
  status('Starting Python (first visit downloads about 15 MB, then it is cached)…');
  const { loadPyodide } = await import(/* @vite-ignore */ PYODIDE + 'pyodide.mjs');
  py = await loadPyodide({ indexURL: PYODIDE });
  status('Loading Python packages…');
  await py.loadPackage(BUILT_IN, { messageCallback: () => {} });
  await py.pyimport('micropip').install(FROM_PYPI);
  status('Opening your experts…');
  py.FS.mkdirTree('/noah');
  py.FS.mount(py.FS.filesystems.IDBFS, {}, '/noah');
  await sync(true);
  const zip = await fetch(zipUrl);
  if (!zip.ok) throw new Error(`could not load Noah's Python code (${zipUrl}: ${zip.status})`);
  py.unpackArchive(await zip.arrayBuffer(), 'zip', { extractDir: '/lib/noah-src' });
  py.runPython("import sys; sys.path.insert(0, '/lib/noah-src')");
  noah = py.pyimport('noah.wasm');
  noah.start('/noah', '/lib/noah-src/experts');
  await sync(false);
}

async function answer({ id, method, url, headers, body }) {
  let r = null;
  try {
    r = await noah.handle(method, url, headers, new Uint8Array(body));
    const take = key => { const p = r.get(key), v = p.toJs(); p.destroy(); return v; };   // proxies to JS copies
    const heads = take('headers'), out = new Uint8Array(take('body'));
    postMessage({ id, status: r.get('status'), headers: heads, body: out.buffer }, [out.buffer]);
  } catch (e) {
    const out = new TextEncoder().encode(JSON.stringify({ detail: `Noah failed in the browser: ${e.message || e}` }));
    postMessage({ id, status: 500, headers: [['content-type', 'application/json']], body: out.buffer }, [out.buffer]);
  } finally {
    r?.destroy();
  }
  if (method !== 'GET') sync(false).catch(e => status('Could not save to the browser storage: ' + e));
}

// One request at a time: while Python waits for an in-browser model, a second request must not run in the middle of it
let turn = Promise.resolve();
onmessage = async ({ data }) => {
  if (data.type === 'boot') {
    try { await boot(data.zip); postMessage({ type: 'ready' }); }
    catch (e) { postMessage({ type: 'failed', text: String(e.message || e) }); }
    return;
  }
  if (data.type === 'llm-reply') { llmWaiting.get(data.id)?.(data.reply); llmWaiting.delete(data.id); return; }
  turn = turn.then(() => answer(data));
};
