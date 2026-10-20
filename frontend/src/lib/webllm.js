// Models that run inside the browser tab with WebLLM (WebGPU). Nothing goes through Noah's server or Ollama: the weights are
// downloaded once from Hugging Face into this browser's cache and run on the visitor's GPU. The state is module-level, so a
// download keeps going (and a loaded model stays loaded) when the Models page switches tabs or the user goes to another page.
// WebLLM itself (~6 MB) is imported only when the WebLLM tab is first opened or a `web:` model is first called.
//
// In the browser build, Noah's Python calls these models too (`web:<id>`, noah/webllm.py): its requests arrive through
// wasm/bridge.js at `serve()`. Downloaded models are listed on the Python side as well (/api/web/my), so they appear in every
// model picker without loading WebLLM first; `syncNoah()` keeps that list in step with this browser's cache.
import { reactive } from 'vue';
import { send } from './api.js';
import { toWebLLM, fromWebLLM } from './webllm-openai.js';
import { cfg, IS_WASM } from '../store.js';

// small chat models worth trying first; any other model in WebLLM's catalogue can be shown with "Show all"
export const RECOMMENDED = [
  'Qwen2.5-0.5B-Instruct-q4f16_1-MLC', 'Qwen2.5-1.5B-Instruct-q4f16_1-MLC', 'Qwen2.5-Coder-1.5B-Instruct-q4f16_1-MLC',
  'Qwen3-0.6B-q4f16_1-MLC', 'Llama-3.2-1B-Instruct-q4f16_1-MLC', 'gemma3-1b-it-q4f16_1-MLC', 'SmolLM2-360M-Instruct-q4f16_1-MLC',
];

export const web = reactive({
  supported: null,   // null: not checked yet; then true / false (no WebGPU, `why` says what is missing)
  why: '',
  models: [],        // [{ id, vram, lowResource }] chat models from WebLLM's catalogue
  cached: {},        // id -> true when its files are in this browser's cache
  loaded: '',        // the model on the GPU now (one at a time)
  loading: '',       // the model being downloaded / loaded
  progress: 0,
  status: '',
  error: '',
  usage: null,       // { used, quota } bytes of this site's browser storage
});

let lib = null, engine = null, worker = null, probing = null;

// one engine, one request at a time: Noah's calls and the Try box take turns
let queue = Promise.resolve();
const exclusive = fn => { const run = queue.then(fn, fn); queue = run.catch(() => {}); return run; };

async function adapterProblem() {
  if (!window.isSecureContext) return 'WebGPU needs a secure page (https or localhost).';
  if (!navigator.gpu) return 'This browser has no WebGPU. Use a recent Chrome or Edge (or Safari 26+, Firefox 141+ on Windows).';
  try {
    if (!(await navigator.gpu.requestAdapter())) return 'WebGPU is present but found no usable GPU (it may be disabled or blocklisted for this GPU).';
  } catch (e) { return 'WebGPU failed to start: ' + (e.message || e); }
  return '';
}

export async function refreshUsage() {
  try { const { usage, quota } = await navigator.storage.estimate(); web.usage = { used: usage, quota }; } catch { web.usage = null; }
}

/** Check WebGPU, load WebLLM's catalogue and see which models are already in the cache. Safe to call again. */
export function probe() {
  return probing ||= (async () => {
    web.why = await adapterProblem();
    lib = await import('@mlc-ai/web-llm');
    web.models = lib.prebuiltAppConfig.model_list
      .filter(m => !m.model_type)   // 0 / undefined: chat; 1: embedding; 2: vision
      .map(m => ({ id: m.model_id, vram: m.vram_required_MB || 0, lowResource: !!m.low_resource_required }))
      .sort((a, b) => a.vram - b.vram);
    const have = await Promise.all(web.models.map(m => lib.hasModelInCache(m.id).catch(() => false)));
    web.models.forEach((m, i) => { web.cached[m.id] = have[i]; });
    web.supported = !web.why;
    refreshUsage();
    syncNoah();
  })().catch(e => { web.supported = false; web.why = 'Could not load WebLLM: ' + (e.message || e); probing = null; });
}

function getEngine() {
  if (!engine) {
    worker = new Worker(new URL('./webllm.worker.js', import.meta.url), { type: 'module' });
    engine = new lib.WebWorkerMLCEngine(worker);
  }
  return engine;
}

/** Download (the first time) and put a model on the GPU. Any model loaded before is unloaded. */
export async function load(id) {
  if (web.loading) return;
  Object.assign(web, { loading: id, progress: 0, status: 'Starting…', error: '' });
  try {
    if (!web.cached[id]) navigator.storage?.persist?.().catch(() => {});   // ask the browser not to evict big downloads
    const e = getEngine();
    e.setInitProgressCallback(r => { if (web.loading === id) { web.progress = r.progress; web.status = r.text; } });
    web.loaded = '';
    await e.reload(id);
    if (web.loading !== id) return;   // cancelled meanwhile
    Object.assign(web, { loaded: id, status: '' });
    web.cached[id] = true;
    syncNoah();
  } catch (e) {
    if (web.loading === id) Object.assign(web, { error: String(e.message || e), status: '' });
    web.cached[id] = await lib.hasModelInCache(id).catch(() => false);   // a failed download may have left part of it
  } finally {
    if (web.loading === id) web.loading = '';
    refreshUsage();
  }
}

/** Stop a download or load in progress. The worker is thrown away; files already cached stay for next time. */
export function cancel() {
  worker?.terminate();
  worker = engine = null;
  Object.assign(web, { loading: '', loaded: '', status: '', error: 'Cancelled.' });
}

/** Delete a model's files from this browser (and take it off the GPU if it is loaded). */
export async function remove(id) {
  if (web.loaded === id) { await engine?.unload().catch(() => {}); web.loaded = ''; }
  await lib.deleteModelAllInfoInCache(id);
  web.cached[id] = false;
  refreshUsage();
  syncNoah();
}

/** Browser build: Noah offers exactly the models downloaded here (web:<id>) in its model pickers. */
async function syncNoah() {
  if (!IS_WASM) return;
  try {
    const listed = (cfg.value.web_models || []).map(m => m.replace(/^web:/, ''));
    const have = Object.keys(web.cached).filter(id => web.cached[id]);
    let models = null;
    for (const id of have.filter(id => !listed.includes(id))) models = (await (await send('/api/web/my', 'POST', { id })).json()).models;
    for (const id of listed.filter(id => !have.includes(id))) models = (await (await fetch('/api/web/my/' + encodeURIComponent(id), { method: 'DELETE' })).json()).models;
    if (models) cfg.value.web_models = models;
  } catch { /* the list is a convenience; the next sync retries */ }
}

/** A chat completion for Noah's Python (noah/webllm.py): OpenAI-style JSON in, OpenAI-style JSON out, or { error, setup }.
 *  `ms` limits the answer, not the loading: the first call loads the model onto the GPU (from the cache, never a download). */
export async function serve(json, ms) {
  const body = JSON.parse(json), id = body.model;
  const fail = (error, setup = false) => JSON.stringify({ error, setup });
  await probe();
  if (!web.supported) return fail(`In-browser models need WebGPU. ${web.why}`, true);
  if (!web.cached[id]) return fail(`${id} is not downloaded in this browser. Download it on the Models page, WebLLM tab.`, true);
  return exclusive(async () => {
    if (web.loaded !== id) {
      if (web.loading) return fail(`${web.loading} is still loading. Try again when it is done.`, true);
      await load(id);
      if (web.loaded !== id) return fail(`Could not load ${id}: ${web.error}`, true);
    }
    const { request, plan } = toWebLLM(body);
    let timer;
    const late = new Promise((_, no) => { timer = setTimeout(() => { engine?.interruptGenerate(); no(new Error(`${id} took longer than ${ms / 1000} s to answer.`)); }, ms); });
    try { return JSON.stringify(fromWebLLM(await Promise.race([engine.chat.completions.create(request), late]), plan)); }
    catch (e) { return fail(String(e.message || e)); }
    finally { clearTimeout(timer); }
  });
}

/** Stream a reply from the loaded model. `onText` gets the reply so far; resolves with { text, usage, seconds }. */
export const chat = (messages, onText) => exclusive(() => stream(messages, onText));
async function stream(messages, onText) {
  const started = performance.now();
  const stream = await engine.chat.completions.create({ messages, stream: true, stream_options: { include_usage: true } });
  let text = '', usage = null;
  for await (const chunk of stream) {
    text += chunk.choices[0]?.delta?.content || '';
    if (chunk.usage) usage = chunk.usage;
    onText(text);
  }
  return { text, usage, seconds: (performance.now() - started) / 1000 };
}

export const stop = () => engine?.interruptGenerate();
