<script setup>
// Add models from a hosted provider (Hugging Face or OpenRouter): save a key, search, and use a model through the provider's API.
// Nothing is downloaded. Provider-specific extras (like Hugging Face GGUF downloads) go in the "extra" slot.
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';
import { send, getJson } from '../lib/api.js';
import { PROVIDERS } from '../lib/format.js';
import { cfg } from '../store.js';

const props = defineProps({ provider: String, help: String, searchHint: String, manualHint: String, extraNote: String });
const P = computed(() => PROVIDERS[props.provider]);
const api = path => `/api/${props.provider}${path}`;
const status = computed(() => cfg.value[props.provider] || {});
const mine = computed(() => cfg.value[props.provider + '_models'] || []);
const id = suffix => `${props.provider}${suffix}`;  // stable element ids: hf-token, orHosted, …

/* ---- key ---- */
const tokenInput = ref(''), tokenBusy = ref(false), tokenMsg = ref('');
async function connect() {
  tokenBusy.value = true; tokenMsg.value = '';
  try {
    const r = await send(api('/token'), 'PUT', { token: tokenInput.value });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || `could not save the ${P.value.secret}`);
    cfg.value[props.provider] = { configured: j.configured, source: j.source };
    tokenInput.value = ''; tokenMsg.value = j.name ? `Connected as ${j.name}.` : 'Connected.';
  } catch (e) { tokenMsg.value = e.message; }
  tokenBusy.value = false;
}
async function disconnect() {
  const r = await fetch(api('/token'), { method: 'DELETE' });
  const j = await r.json();
  if (r.ok) { cfg.value[props.provider] = j; tokenMsg.value = `Disconnected. The ${P.value.secret} was deleted from this machine.`; } else tokenMsg.value = j.detail;
}

/* ---- search ---- */
const q = ref(''), results = ref({ hosted: [], error: '' }), searching = ref(false);
let timer = null, seq = 0;
async function search() {
  const mineSeq = ++seq; searching.value = true;
  try { const j = await getJson(api('/search?q=' + encodeURIComponent(q.value))); if (mineSeq === seq) results.value = j; }
  catch (e) { if (mineSeq === seq) results.value = { hosted: [], error: 'Search failed: ' + e.message }; }
  if (mineSeq === seq) searching.value = false;
}
watch(q, () => { clearTimeout(timer); timer = setTimeout(search, 300); });
onMounted(search);
onBeforeUnmount(() => clearTimeout(timer));

/* ---- use a model ---- */
const manualId = ref(''), msg = ref('');
const SHOW = 8, more = ref(false);  // rows shown before "Show more"
const rows = computed(() => more.value ? results.value.hosted : results.value.hosted.slice(0, SHOW));
const added = mid => mine.value.includes(P.value.prefix + mid);
async function use(mid) {
  msg.value = '';
  const r = await send(api('/my'), 'POST', { id: mid });
  const j = await r.json();
  if (r.ok) { cfg.value[props.provider + '_models'] = j.models; manualId.value = ''; } else msg.value = j.detail;
}
const price = m => m.free ? 'free' : m.price_in == null ? '' : `from $${+m.price_in.toPrecision(3)}/M tokens`;  // toPrecision: no 0.22999999999999998
const ctx = n => n ? (n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : Math.round(n / 1024) + 'K') + ' context' : '';
const facts = m => [m.providers ? m.providers + ' provider' + (m.providers === 1 ? '' : 's') : '', price(m), ctx(m.context)].filter(Boolean).join(' · ');
</script>

<template>
  <div :id="id('Panel')" class="hf">
    <h2>Add from {{ P.label }}</h2>
    <div class="hfbox">
      <div v-if="status.configured" class="hfrow">
        <span class="where" :class="provider">{{ P.secret === 'token' ? 'Token' : 'Key' }} set</span>
        <span class="fnote">{{ status.source === 'env' ? 'from an environment variable' : 'stored on this machine' }}. {{ tokenMsg }}</span>
        <button v-if="status.source === 'file'" class="btn small" @click="disconnect">Disconnect</button>
      </div>
      <form v-else class="hfrow" @submit.prevent="connect">
        <input type="password" v-model="tokenInput" :placeholder="`${P.label} ${P.secret}`" autocomplete="off" :id="id('-token')">
        <button class="btn primary small" :disabled="tokenBusy || !tokenInput">{{ tokenBusy ? 'Checking…' : 'Connect' }}</button>
        <span class="fnote">{{ help }} Stored in a private file on this machine.</span>
      </form>
      <div v-if="!status.configured && tokenMsg" class="fnote bad">{{ tokenMsg }}</div>
      <p class="fnote warn">Hosted models send your messages to {{ P.label }} and the model's provider, and may cost credits. Local models never leave this machine.</p>
    </div>

    <div class="hfrow"><input :id="id('-search')" v-model="q" :placeholder="searchHint"><span v-if="searching" class="fnote">Searching…</span></div>
    <div v-if="results.error" class="fnote bad">{{ results.error }}</div>

    <h3>Use hosted <span class="meta">no download, runs on {{ P.label }}</span></h3>
    <table v-if="results.hosted.length" :id="id('Hosted')">
      <thead><tr><th>Model</th><th>What it supports</th><th></th></tr></thead>
      <tbody><tr v-for="m in rows" :key="m.id">
        <td>{{ m.id }}<div v-if="m.name" class="meta">{{ m.name }}</div></td>
        <td><span v-if="m.tools" class="tag">tool calls</span> <span v-if="m.structured" class="tag">structured output</span> <span v-if="m.logprobs" class="tag">log-probs</span>
          <span class="meta">{{ facts(m) }}</span></td>
        <td><button v-if="!added(m.id)" class="btn small" @click="use(m.id)">Use hosted</button><span v-else class="meta">Added ✓</span></td>
      </tr></tbody>
    </table>
    <button v-if="results.hosted.length > SHOW" class="btn small more" @click="more = !more">{{ more ? 'Show fewer' : `Show ${results.hosted.length - SHOW} more` }}</button>
    <p v-if="!results.hosted.length && !searching && !results.error" class="fnote">No hosted match.</p>
    <form class="hfrow" @submit.prevent="use(manualId)">
      <input v-model="manualId" :placeholder="manualHint" :id="id('-manual')">
      <button class="btn small" :disabled="!manualId.trim()">Add</button><span v-if="msg" class="fnote bad">{{ msg }}</span>
    </form>

    <slot name="extra" :results="results" :searching="searching"></slot>
  </div>
</template>
