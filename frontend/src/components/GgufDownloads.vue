<script setup>
// Download a GGUF repo from Hugging Face into local Ollama: pick a quantisation, see its size, watch the progress.
import { ref, onBeforeUnmount } from 'vue';
import { cfg, refreshConfig } from '../store.js';

defineProps({ rows: { type: Array, default: () => [] }, searching: Boolean });
const SHOW = 8, more = ref(false);  // rows shown before "Show more"

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
onBeforeUnmount(() => downloads.value.forEach(d => d.abort?.abort()));
</script>

<template>
  <div>
    <h3>Download to this machine <span class="meta">GGUF into Ollama, runs locally</span></h3>
    <table v-if="rows.length" id="hfGguf">
      <thead><tr><th>Repository</th><th>Popularity</th><th></th></tr></thead>
      <tbody>
        <template v-for="g in more ? rows : rows.slice(0, SHOW)" :key="g.id">
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
    <button v-if="rows.length > SHOW" class="btn small more" @click="more = !more">{{ more ? 'Show fewer' : `Show ${rows.length - SHOW} more` }}</button>
    <p v-if="!rows.length && !searching" class="fnote">No GGUF repositories found.</p>
  </div>
</template>
