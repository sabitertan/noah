<script setup>
// Browser-only build: Noah runs here, but its local models run in Ollama on the user's computer. Where Ollama is, whether
// this page can reach it, and what to install and pull. The browser asks before a web page may talk to an app on the same
// computer, and Ollama only answers pages it was told to trust (OLLAMA_ORIGINS), so both are explained here.
import { ref, computed, onMounted } from 'vue';
import { send, getJson } from '../lib/api.js';
import { cfg, refreshConfig } from '../store.js';

const url = ref(''), saving = ref(false), result = ref(null), os = ref(guessOs());
const origin = location.origin;
const localPage = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);   // Ollama trusts these pages already
const connected = computed(() => cfg.value.models.length > 0);
const MODELS = [
  { id: 'qwen2.5:0.5b', why: 'fills in what tools need (Default orchestrator); small chat answers', size: '0.4 GB' },
  { id: 'qwen2.5-coder:1.5b', why: 'the code experts: Python, HTML, CSS, C#', size: '1 GB' },
  { id: 'hf.co/DreamBlooms/Tev1-0.8B-experimental-GGUF:Q8_0', why: 'Tev1 decision orchestrator', size: '0.8 GB' },
  { id: 'hf.co/ggml-org/functiongemma-270m-it-GGUF:Q8_0', why: 'FunctionGemma router', size: '0.3 GB' },
  { id: 'hf.co/MaziyarPanahi/Nemotron-Orchestrator-8B-GGUF:Q4_K_M', why: 'Nemotron orchestrator (needs a good GPU)', size: '5 GB' },
];
const have = id => cfg.value.models.some(m => m === id || m === id + ':latest');
const allow = computed(() => ({
  mac: [`launchctl setenv OLLAMA_ORIGINS "${origin}"`, '# then quit Ollama (menu bar) and open it again'],
  win: [`setx OLLAMA_ORIGINS "${origin}"`, '# then quit Ollama (system tray) and start it again'],
  linux: ['sudo systemctl edit ollama', `# add:  [Service]  Environment="OLLAMA_ORIGINS=${origin}"`, 'sudo systemctl restart ollama'],
  any: [`OLLAMA_ORIGINS="${origin}" ollama serve`, '# in a terminal; keep it open while you use Noah'],
}[os.value]));

function guessOs() {
  const p = navigator.userAgent;
  return /Mac/.test(p) ? 'mac' : /Win/.test(p) ? 'win' : /Linux/.test(p) ? 'linux' : 'any';
}
onMounted(async () => { url.value = (await getJson('/api/wasm/ollama')).url; });

async function connect() {
  saving.value = true; result.value = null;
  try {
    const r = await send('/api/wasm/ollama', 'PUT', { url: url.value });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || 'not a valid address');
    url.value = j.url;
    // Asked from the page itself so that the browser can show its "access apps on this device" prompt, and so that a
    // refusal says which of the two (not running / not allowed) it is as clearly as a browser lets us.
    const tags = await fetch(j.url + '/api/tags').then(x => x.json());
    await refreshConfig();
    result.value = { ok: true, text: `Connected: ${tags.models.length} model${tags.models.length === 1 ? '' : 's'} in Ollama.` };
  } catch (e) {
    result.value = { ok: false, text: e instanceof TypeError
      ? `No answer from ${url.value}. Is Ollama running?${localPage ? '' : ` Does it allow this page (step 2)? If your browser asked to let this site use apps on your device, choose Allow.`}`
      : e.message };
  }
  saving.value = false;
}
const copy = text => navigator.clipboard?.writeText(text);
</script>

<template>
  <div class="hfbox ollama" id="ollamaSetup">
    <h3>Ollama on your computer <span class="tag" :class="connected ? 'ok' : 'bad'" id="ollama-state">{{ connected ? `connected · ${cfg.models.length} models` : 'not connected' }}</span></h3>
    <p class="fnote">Noah runs in this page, but local models such as Qwen, Tev or FunctionGemma run in <b>Ollama</b>, which you install yourself.
      Without it Noah still routes your messages and runs the tool experts (calculator, weather); hosted models (Hugging Face, OpenRouter) work with your own key.</p>
    <div class="hfrow">
      <input id="ollama-url" v-model="url" placeholder="http://localhost:11434" @keydown.enter.prevent="connect">
      <button class="btn small primary" id="ollama-connect" :disabled="saving" @click="connect">{{ saving ? 'Connecting…' : 'Connect' }}</button>
    </div>
    <div v-if="result" class="fnote" :class="result.ok ? 'ok' : 'bad'" id="ollama-result">{{ result.text }}</div>

    <details :open="!connected">
      <summary>How to set it up</summary>
      <ol class="steps">
        <li><b>Install Ollama</b> from <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com/download</a> and start it.</li>
        <li v-if="!localPage"><b>Let this page use it.</b> Ollama only answers pages it trusts, so add this one (<code>{{ origin }}</code>):
          <div class="tabs small"><button v-for="(l, k) in { mac: 'macOS', win: 'Windows', linux: 'Linux', any: 'Any (terminal)' }" :key="k" class="tab" :class="{ active: os === k }" @click="os = k">{{ l }}</button></div>
          <pre class="cmd" @click="copy(allow.filter(l => !l.startsWith('#')).join('\n'))" title="Click to copy">{{ allow.join('\n') }}</pre>
          Your browser may then ask whether this site may use apps and services on your device: choose <b>Allow</b>.</li>
        <li v-else><b>This page is on your own computer</b>, which Ollama trusts already: nothing to configure.</li>
        <li><b>Download the models</b> you want (each is a one-time download):
          <table class="pulls"><tr v-for="m in MODELS" :key="m.id">
            <td><span v-if="have(m.id)" class="tag ok">have it</span></td>
            <td><code class="cmd" title="Click to copy" @click="copy('ollama pull ' + m.id)">ollama pull {{ m.id }}</code></td>
            <td class="fnote">{{ m.why }} · {{ m.size }}</td></tr></table></li>
        <li>Press <b>Connect</b>.</li>
      </ol>
    </details>
  </div>
</template>
