<script setup>
// A build (noah/builder.py) as the user sees it: what is being made, progress while the experts work, then the prototype itself.
// The prototype runs in a sandboxed frame without same-origin access, so it can never reach Noah's API, cookies or storage.
// That sandbox also makes localStorage throw, so the page gets a stand-in whose data is kept with this chat message.
import { ref, watch, onMounted, onBeforeUnmount } from 'vue';
import { shortModel } from '../lib/format.js';
import { go, openModelsTab } from '../store.js';

const props = defineProps({ b: Object, error: String });
const frame = ref(null), srcdoc = ref(''), run = ref(0), full = ref(false), crash = ref('');
const LABEL = { html: 'Laying out the page', css: 'Styling it', js: 'Making it work' };

const shim = data => `<script>(() => {
  const make = (d, save) => ({ getItem: k => Object.hasOwn(d, k) ? d[k] : null, setItem: (k, v) => { d[k] = String(v); save(d); },
    removeItem: k => { delete d[k]; save(d); }, clear: () => { for (const k in d) delete d[k]; save(d); },
    key: i => Object.keys(d)[i] ?? null, get length() { return Object.keys(d).length; } });
  const tell = m => parent.postMessage({ noahPrototype: 1, ...m }, '*');
  for (const [name, store] of [['localStorage', make(${JSON.stringify(data).replace(/</g, '\\u003c')}, d => tell({ storage: d }))],
                               ['sessionStorage', make({}, () => {})]])
    try { Object.defineProperty(window, name, { value: store, configurable: true }); } catch (e) {}
  addEventListener('error', e => tell({ error: String(e.message || e) }));
  addEventListener('unhandledrejection', e => tell({ error: String(e.reason) }));
})();<\/script>`;
const withShim = (html, data) => /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, h => h + shim(data)) : shim(data) + html;

// Only a new build reloads the frame. The prototype's own saves change b.storage too, and must not restart it.
watch(() => props.b.html, h => { srcdoc.value = h ? withShim(h, { ...(props.b.storage || {}) }) : ''; crash.value = ''; }, { immediate: true });
function restart() {  // start over with empty data
  props.b.storage = {};
  srcdoc.value = withShim(props.b.html, {}); crash.value = ''; run.value++;
}

function onMessage(e) {
  if (!frame.value || e.source !== frame.value.contentWindow || !e.data?.noahPrototype) return;
  if (e.data.storage) props.b.storage = { ...e.data.storage };
  if (e.data.error) crash.value = e.data.error;
}
onMounted(() => addEventListener('message', onMessage));
onBeforeUnmount(() => removeEventListener('message', onMessage));
</script>

<template>
  <div class="a build" :class="{ full }">
    <div class="head"><span class="badge">build</span><span>{{ b.title || 'Planning…' }}</span></div>
    <p v-if="b.summary" class="bsum">{{ b.summary }}</p>
    <ol v-if="!b.html" class="bsteps">
      <li :class="b.steps.length ? 'done' : error ? 'error' : 'running'">Planning what to build</li>
      <li v-for="s in b.steps" :key="s.part" :class="s.state">{{ LABEL[s.part] || s.part }}<span v-if="s.error" class="meta"> · {{ s.error }}</span></li>
    </ol>
    <div v-if="error" class="fnote bad">{{ error }}</div>
    <template v-if="srcdoc">
      <div class="bbar"><span class="grow"></span>
        <button class="btn small" type="button" @click="restart" title="Reload the prototype with empty data">Start over</button>
        <button class="btn small" type="button" @click="full = !full">{{ full ? 'Close' : 'Full screen' }}</button></div>
      <iframe ref="frame" :key="run" class="bframe" :srcdoc="srcdoc" :title="b.title" sandbox="allow-scripts allow-forms allow-modals"></iframe>
      <div v-if="crash" class="fnote warn">The prototype hit an error: {{ crash }}</div>
      <div v-if="b.small?.length" class="hfbox bigger">
        <b>Want a bigger app?</b> This was built with small models ({{ b.small.map(shortModel).join(', ') }}). They make simple demos and often miss features.
        For more, give the Builder orchestrator a larger planner and builder model:
        <ul>
          <li><b>Open source on this machine:</b> <code>qwen2.5-coder:14b</code> or <code>qwen2.5-coder:32b</code>, <code>qwen3-coder:30b</code>, <code>devstral</code> or <code>gpt-oss:20b</code> in Ollama (needs a GPU with enough memory).</li>
          <li><b>Open source, hosted:</b> the same models through <a href="#" @click.prevent="openModelsTab('hf')">Hugging Face</a> or <a href="#" @click.prevent="openModelsTab('or')">OpenRouter</a>, nothing to download.</li>
          <li><b>Bring your own AI:</b> a <a href="#" @click.prevent="openModelsTab('an')">Claude</a> or <a href="#" @click.prevent="openModelsTab('oa')">OpenAI</a> API key.</li>
        </ul>
        Then pick them on the <a href="#" @click.prevent="go('orch')">Orchestrators</a> page (Builder: planner model and builder model).
      </div>
      <details v-if="b.features?.length"><summary>what it does</summary><ul><li v-for="f in b.features" :key="f">{{ f }}</li></ul></details>
    </template>
  </div>
</template>
