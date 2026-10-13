<script setup>
// Add models from Hugging Face: use one hosted (their API, no download) or download a GGUF into local Ollama.
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';
import { send, getJson } from '../lib/api.js';
import { cfg, hostedModels, refreshConfig } from '../store.js';

/* ---- token ---- */
const tokenInput = ref(''), tokenBusy = ref(false), tokenMsg = ref('');
async function connect() {
  tokenBusy.value = true; tokenMsg.value = '';
  try {
    const r = await send('/api/hf/token', 'PUT', { token: tokenInput.value });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || 'could not save the token');
    cfg.value.hf = { configured: j.configured, source: j.source };
    tokenInput.value = ''; tokenMsg.value = j.name ? `Connected as ${j.name}.` : 'Connected.';
  } catch (e) { tokenMsg.value = e.message; }
  tokenBusy.value = false;
}
async function disconnect() {
  const r = await fetch('/api/hf/token', { method: 'DELETE' });
  const j = await r.json();
  if (r.ok) { cfg.value.hf = j; tokenMsg.value = 'Disconnected. The token was deleted from this machine.'; } else tokenMsg.value = j.detail;
}

/* ---- search ---- */
const q = ref(''), results = ref({ hosted: [], gguf: [], error: '' }), searching = ref(false);
let timer = null, seq = 0;
async function search() {
  const mine = ++seq; searching.value = true;
  try { const j = await getJson('/api/hf/search?q=' + encodeURIComponent(q.value)); if (mine === seq) results.value = j; }
  catch (e) { if (mine === seq) results.value = { hosted: [], gguf: [], error: 'Search failed: ' + e.message }; }
  if (mine === seq) searching.value = false;
}
watch(q, () => { clearTimeout(timer); timer = setTimeout(search, 300); });
onMounted(search);
onBeforeUnmount(() => { clearTimeout(timer); downloads.value.forEach(d => d.abort?.abort()); });

/* ---- hosted: use a model through the API ---- */
const manualId = ref(''), hostedMsg = ref('');
const added = id => hostedModels.value.includes('hf:' + id);
async function useHosted(id) {
  hostedMsg.value = '';
  const r = await send('/api/hf/my', 'POST', { id });
  const j = await r.json();
  if (r.ok) { cfg.value.hf_models = j.models; manualId.value = ''; } else hostedMsg.value = j.detail;
}
const price = m => m.free ? 'free tier' : m.price_in == null ? '' : `from $${+m.price_in.toPrecision(3)}/M tokens`;  // toPrecision: no 0.22999999999999998
const SHOW = 8;  // rows shown before "Show more"
const more = ref({ hosted: false, gguf: false });
const visible = (list, key) => more.value[key] ? list : list.slice(0, SHOW);
const ctx = n => n ? (n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : Math.round(n / 1024) + 'K') + ' context' : '';

/* ---- download: pull a GGUF repo into Ollama ---- */
const downloads = ref(new Map());                  // repo -> { quants, quant, progress, status, error, abort }
const GB = n => n >= 1e9 ? (n / 1e9).toFixed(1) + ' GB' : (n / 1e6).toFixed(0) + ' MB';
const localName = (repo, quant) => `hf.co/${repo}:${quant}`.toLowerCase();
const have = (repo, quant) => cfg.value.models.some(m => m.toLowerCase() === localName(repo, quant));
const entry = repo => downloads.value.get(repo);
const sizeOf = d => d.quants.find(x => x.quant === d.quant)?.size || 0;

async function openDownload(repo) {
  if (downloads.value.has(repo)) { if (!entry(repo).abort) downloads.value.delete(repo); return; }  // second click closes
  downloads.value.set(repo, { quants: [], quant: '', progress: 0, status: 'Reading the repo…', error: '', abort: null });
  const d = entry(repo);
  try {
    const r = await fetch('/api/hf/quants?repo=' + encodeURIComponent(repo));
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || 'could not read the repo');
    if (!j.quants.length) throw new Error('This repo has no GGUF files Ollama can use.');
    d.quants = j.quants;
    d.quant = (j.quants.find(x => x.quant === 'Q4_K_M') || j.quants.find(x => x.quant === 'UD-Q4_K_M') || j.quants[0]).quant;
    d.status = '';
  } catch (e) { d.error = e.message; d.status = ''; }
}
async function download(repo) {
  const d = entry(repo); d.abort = new AbortController(); d.error = ''; d.progress = 0; d.status = 'Starting…';
  try {
    const r = await fetch('/api/hf/pull', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repo, quant: d.quant }), signal: d.abort.signal });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'download refused');
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split('\n'); buf = lines.pop();
      for (const line of lines.filter(Boolean)) {
        const m = JSON.parse(line);
        if (m.error) throw new Error(m.error);
        d.status = m.status || d.status;
        if (m.total) d.progress = m.completed / m.total;
      }
    }
    await refreshConfig(); d.status = 'Downloaded. It now appears in every model list.'; d.progress = 1;
  } catch (e) { d.error = e.name === 'AbortError' ? 'Cancelled.' : e.message; d.status = ''; }
  d.abort = null;
}
const cancel = repo => entry(repo)?.abort?.abort();
</script>

<template>
  <div id="hfPanel" class="hf">
    <h2>Add from Hugging Face</h2>
    <div class="hfbox">
      <div v-if="cfg.hf.configured" class="hfrow">
        <span class="where hf">Token set</span>
        <span class="fnote">{{ cfg.hf.source === 'env' ? 'from the HF_TOKEN environment variable' : 'stored on this machine' }}. {{ tokenMsg }}</span>
        <button v-if="cfg.hf.source === 'file'" class="btn small" @click="disconnect">Disconnect</button>
      </div>
      <form v-else class="hfrow" @submit.prevent="connect">
        <input type="password" v-model="tokenInput" placeholder="Hugging Face token (hf_…)" autocomplete="off" id="hf-token">
        <button class="btn primary small" :disabled="tokenBusy || !tokenInput">{{ tokenBusy ? 'Checking…' : 'Connect' }}</button>
        <span class="fnote">Only needed for hosted models. Create one at huggingface.co/settings/tokens with the “Make calls to Inference Providers” permission. Stored in a private file on this machine.</span>
      </form>
      <div v-if="!cfg.hf.configured && tokenMsg" class="fnote bad">{{ tokenMsg }}</div>
      <p class="fnote warn">Hosted models send your messages to Hugging Face and its inference providers, and may cost credits. Local models never leave this machine.</p>
    </div>

    <div class="hfrow"><input id="hf-search" v-model="q" placeholder="Search models, e.g. qwen, llama, gemma…"><span v-if="searching" class="fnote">Searching…</span></div>
    <div v-if="results.error" class="fnote bad">{{ results.error }}</div>

    <h3>Use hosted <span class="meta">no download, runs on Hugging Face</span></h3>
    <table v-if="results.hosted.length" id="hfHosted">
      <thead><tr><th>Model</th><th>What it supports</th><th></th></tr></thead>
      <tbody><tr v-for="m in visible(results.hosted, 'hosted')" :key="m.id">
        <td>{{ m.id }}</td>
        <td><span v-if="m.tools" class="tag">tool calls</span> <span v-if="m.structured" class="tag">structured output</span>
          <span class="meta">{{ [m.providers + ' provider' + (m.providers === 1 ? '' : 's'), price(m), ctx(m.context)].filter(Boolean).join(' · ') }}</span></td>
        <td><button v-if="!added(m.id)" class="btn small" @click="useHosted(m.id)">Use hosted</button><span v-else class="meta">Added ✓</span></td>
      </tr></tbody>
    </table>
    <button v-if="results.hosted.length > SHOW" class="btn small more" @click="more.hosted = !more.hosted">{{ more.hosted ? 'Show fewer' : `Show ${results.hosted.length - SHOW} more` }}</button>
    <p v-if="!results.hosted.length && !searching" class="fnote">No hosted match.</p>
    <form class="hfrow" @submit.prevent="useHosted(manualId)">
      <input v-model="manualId" placeholder="Or any model id, e.g. Qwen/Qwen2.5-7B-Instruct:fastest" id="hf-manual">
      <button class="btn small" :disabled="!manualId.trim()">Add</button><span v-if="hostedMsg" class="fnote bad">{{ hostedMsg }}</span>
    </form>

    <h3>Download to this machine <span class="meta">GGUF into Ollama, runs locally</span></h3>
    <table v-if="results.gguf.length" id="hfGguf">
      <thead><tr><th>Repository</th><th>Popularity</th><th></th></tr></thead>
      <tbody>
        <template v-for="g in visible(results.gguf, 'gguf')" :key="g.id">
          <tr><td>{{ g.id }}</td><td class="meta">{{ g.downloads.toLocaleString() }} downloads · {{ g.likes }} likes</td>
            <td><button class="btn small" @click="openDownload(g.id)">{{ entry(g.id) && !entry(g.id).abort ? 'Close' : 'Download…' }}</button></td></tr>
          <tr v-if="entry(g.id)" class="dl"><td colspan="3">
            <div class="hfrow">
              <template v-if="entry(g.id).quants.length">
                <select v-model="entry(g.id).quant" :disabled="!!entry(g.id).abort"><option v-for="x in entry(g.id).quants" :key="x.quant" :value="x.quant">{{ x.quant }} · {{ GB(x.size) }}</option></select>
                <button v-if="!entry(g.id).abort && !have(g.id, entry(g.id).quant)" class="btn primary small" @click="download(g.id)">Download {{ GB(sizeOf(entry(g.id))) }}</button>
                <button v-if="entry(g.id).abort" class="btn small" @click="cancel(g.id)">Cancel</button>
                <span v-if="have(g.id, entry(g.id).quant)" class="meta">Already downloaded ✓</span>
              </template>
              <span v-if="entry(g.id).status" class="fnote">{{ entry(g.id).status }}</span>
              <span v-if="entry(g.id).error" class="fnote bad">{{ entry(g.id).error }}</span>
            </div>
            <div v-if="entry(g.id).abort" class="progress"><i :style="{ width: entry(g.id).progress * 100 + '%' }"></i></div>
          </td></tr>
        </template>
      </tbody>
    </table>
    <button v-if="results.gguf.length > SHOW" class="btn small more" @click="more.gguf = !more.gguf">{{ more.gguf ? 'Show fewer' : `Show ${results.gguf.length - SHOW} more` }}</button>
    <p v-if="!results.gguf.length && !searching" class="fnote">No GGUF repositories found.</p>
  </div>
</template>
