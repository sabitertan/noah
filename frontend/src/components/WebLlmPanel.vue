<script setup>
// WebLLM: download a small model into this browser and run it on the GPU (WebGPU), then try it right here.
// Nothing goes through Noah's server: see lib/webllm.js.
import { ref, computed, nextTick } from 'vue';
import { web, RECOMMENDED, probe, load, cancel, remove, chat, stop } from '../lib/webllm.js';
import { IS_WASM } from '../store.js';

const JSPI = typeof WebAssembly !== 'undefined' && 'Suspending' in WebAssembly;   // lets Noah's Python wait for these models

probe();

const all = ref(false);
const shown = computed(() => all.value ? web.models : RECOMMENDED.map(id => web.models.find(m => m.id === id)).filter(Boolean));
const GB = mb => mb >= 1000 ? (mb / 1000).toFixed(1) + ' GB' : Math.round(mb) + ' MB';
const bytes = n => n >= 1e9 ? (n / 1e9).toFixed(1) + ' GB' : (n / 1e6).toFixed(0) + ' MB';
const cachedCount = computed(() => Object.values(web.cached).filter(Boolean).length);

async function drop(id) {
  if (confirm(`Delete ${id} from this browser? You can download it again later.`)) await remove(id);
}

/* ---- try the loaded model ---- */
const prompt = ref(''), turns = ref([]), answering = ref(false), stats = ref(''), err = ref(''), log = ref(null);
const scroll = () => nextTick(() => { if (log.value) log.value.scrollTop = log.value.scrollHeight; });
async function ask() {
  const q = prompt.value.trim();
  if (!q || answering.value) return;
  prompt.value = ''; err.value = ''; stats.value = '';
  turns.value.push({ role: 'user', content: q }, { role: 'assistant', content: '' });
  const reply = turns.value[turns.value.length - 1];
  answering.value = true; scroll();
  try {
    const { usage, seconds } = await chat(turns.value.slice(0, -1).map(({ role, content }) => ({ role, content })), t => { reply.content = t; scroll(); });
    // WebLLM's own speeds: the wall clock also counts compiling the GPU shaders on the first reply
    const speed = usage?.extra?.decode_tokens_per_s;
    if (usage) stats.value = `${usage.completion_tokens} tokens in ${seconds.toFixed(1)} s` + (speed ? ` · ${speed.toFixed(1)} tokens/s` : '');
  } catch (e) { err.value = String(e.message || e); }
  answering.value = false;
}
const clear = () => { turns.value = []; stats.value = ''; err.value = ''; };
const keydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } };
</script>

<template>
  <div id="webPanel" class="hf">
    <h2>Run in this browser with WebLLM</h2>
    <div class="hfbox">
      <p class="fnote" style="margin-top:0">The model is downloaded once from Hugging Face into this browser's storage and runs on your GPU inside this tab: no Ollama, no key, nothing sent anywhere. Small models (0.5 to 1.5B) need roughly 1 to 2 GB of GPU memory.</p>
      <p v-if="web.supported === null" class="fnote">Checking WebGPU…</p>
      <p v-else-if="!web.supported" class="fnote bad" id="web-unsupported">{{ web.why }}</p>
      <p v-else class="fnote">WebGPU is available. {{ cachedCount }} model{{ cachedCount === 1 ? '' : 's' }} downloaded<template v-if="web.usage">; this site uses {{ bytes(web.usage.used) }} of {{ bytes(web.usage.quota) }} browser storage</template>.</p>
      <p v-if="IS_WASM && JSPI" class="fnote">Downloaded models appear in every model list as <code>web:…</code>, so orchestrators and experts can use them.</p>
      <p v-else-if="IS_WASM" class="fnote warn">You can try models here, but this browser cannot let Noah's orchestrators and experts use them (it lacks WebAssembly JSPI). A recent Chrome or Edge can.</p>
      <p v-else class="fnote warn">Try models here only: this Noah runs on a server or your desktop, which cannot reach the browser's GPU. In the browser version of Noah, orchestrators and experts can use them too.</p>
    </div>

    <template v-if="web.models.length">
      <h3>Models <span class="meta">{{ all ? `all ${web.models.length} in WebLLM's catalogue, smallest first` : 'recommended' }}</span></h3>
      <table id="webModels">
        <thead><tr><th>Model</th><th>GPU memory</th><th></th></tr></thead>
        <tbody>
          <template v-for="m in shown" :key="m.id">
            <tr :data-model="m.id">
              <td>{{ m.id }}</td>
              <td class="meta">{{ GB(m.vram) }}</td>
              <td><div class="rowact">
                <span v-if="web.loaded === m.id" class="where local">Loaded</span>
                <span v-else-if="web.cached[m.id]" class="meta">Downloaded ✓</span>
                <button v-if="web.loaded !== m.id && web.loading !== m.id" class="btn small" :class="{ primary: !web.cached[m.id] }"
                  :disabled="!web.supported || !!web.loading" @click="load(m.id)">{{ web.cached[m.id] ? 'Load' : 'Download' }}</button>
                <button v-if="web.loading === m.id" class="btn small" @click="cancel">Cancel</button>
                <button v-if="web.cached[m.id] && web.loading !== m.id" class="btn small" @click="drop(m.id)">Delete</button>
              </div></td>
            </tr>
            <tr v-if="web.loading === m.id" class="dl"><td colspan="3">
              <span class="fnote">{{ web.status }}</span>
              <div class="progress"><i :style="{ width: web.progress * 100 + '%' }"></i></div>
            </td></tr>
          </template>
        </tbody>
      </table>
      <p v-if="web.error" class="fnote bad">{{ web.error }}</p>
      <button class="btn small" @click="all = !all">{{ all ? 'Show recommended only' : `Show all ${web.models.length}` }}</button>
    </template>

    <div v-if="web.loaded" class="hfbox tryit" id="webTry">
      <h3>Try {{ web.loaded }} <span class="meta">runs on your GPU</span></h3>
      <div v-if="turns.length" ref="log" class="webchat">
        <div v-for="(t, i) in turns" :key="i" :class="t.role === 'user' ? 'u' : 'a'">
          <span v-if="t.role === 'user'">{{ t.content }}</span>
          <div v-else class="body">{{ t.content || '…' }}</div>
        </div>
      </div>
      <textarea v-model="prompt" rows="2" placeholder="Ask something (Enter sends, Shift+Enter for a new line)" @keydown="keydown"></textarea>
      <div class="hfrow">
        <button v-if="!answering" class="btn primary small" :disabled="!prompt.trim()" @click="ask">Send</button>
        <button v-else class="btn small" @click="stop">Stop</button>
        <button class="btn small" :disabled="answering || !turns.length" @click="clear">Clear</button>
        <span v-if="stats" class="fnote">{{ stats }}</span>
        <span v-if="err" class="fnote bad">{{ err }}</span>
      </div>
    </div>
  </div>
</template>
