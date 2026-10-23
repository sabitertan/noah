<script setup>
import { ref, computed, watch, nextTick } from 'vue';
import UiIcon from './UiIcon.vue';
import FileCard from './FileCard.vue';
import PrototypeCard from './PrototypeCard.vue';
import { fmt } from '../lib/format.js';
import { cfg, view, IS_WASM, openModelsTab, currentChat, messages, current, builder, enabledProfiles, activeProfile, busy, setMode, sendMessage } from '../store.js';

const text = ref(''), log = ref(null), box = ref(null);
const exampleChips = computed(() => cfg.value.experts.filter(e => e.enabled).flatMap(e => e.examples.slice(0, 1)));
const pct = s => Math.max(0, Math.min(1, s)) * 100 + '%';
const chosen = (m, d) => m.results.some(r => r.expert === d.expert);

const autosize = () => { const t = box.value; t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 160) + 'px'; };
async function send() {
  const t = text.value;
  if (!t.trim() || busy.value || !current.value) return;
  text.value = ''; nextTick(autosize);
  await sendMessage(t);
  box.value?.focus();
}
const onEnter = e => { if (e.isComposing) return; e.preventDefault(); send(); };
const ask = t => { text.value = t; send(); };

// An MCP tool that may change something waits for the user (noah/mcp_expert.py): Run sends exactly the shown arguments
async function runTool(r, always) {
  r.running = true;
  try {
    const res = await fetch('/api/mcp/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...r.confirm, always }) });
    const j = await res.json();
    if (!res.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'could not run the tool');
    Object.assign(r, { output: j.output, attachments: j.attachments, error: j.error, latency_ms: r.latency_ms + j.latency_ms, confirm: null });
  } catch (e) { r.error = e.message; r.output += `\n\nCould not run it: ${e.message}`; }
  delete r.running;
}
const cancelTool = r => { r.output = `Cancelled: **${r.confirm.tool}** did not run.`; r.confirm = null; };

watch(() => [messages.value.length, messages.value.at(-1)?.pending, messages.value.at(-1)?.build?.steps.filter(s => s.state !== 'waiting').length, messages.value.at(-1)?.build?.html, currentChat.value, view.value], () => {
  if (log.value) log.value.scrollTop = log.value.scrollHeight;
}, { flush: 'post' });
defineExpose({ focus: () => box.value?.focus() });
</script>

<template>
  <section class="view" :class="{ active: view === 'chat' }" id="v-chat">
    <div id="log" ref="log"><div class="wrap" id="thread">
      <div v-if="!messages.length" class="empty">
        <h1><UiIcon name="cpu" :size="26" /> Noah</h1><p>Tiny experts, one job each.</p>
        <div class="chips"><button v-for="t in exampleChips" :key="t" class="chip" @click="ask(t)">{{ t }}</button></div>
        <div v-if="IS_WASM && !cfg.models.length" class="hfbox wasm-note" id="wasm-ollama-note">Noah is running in your browser. Local models (Qwen, Tev, …) need
          <b>Ollama</b> installed on your computer: <button class="btn small" @click="openModelsTab('local')">Set up Ollama</button></div>
      </div>
      <div v-for="(m, i) in messages" :key="i" class="msg">
        <div class="u"><span>{{ m.text }}</span></div>
        <div v-if="m.pending" class="a typing">{{ current && current.mode === 'build' ? 'planning…' : 'routing…' }}</div>
        <PrototypeCard v-else-if="m.build" :b="m.build" :error="m.error" />
        <div v-else-if="m.error" class="a err"><div class="body">{{ m.error }}</div></div>
        <template v-else>
          <div v-for="(r, j) in m.results" :key="j" class="a" :class="{ err: r.error }">
            <div class="head"><span class="badge">{{ r.expert }}</span><span>{{ r.extractor }} · {{ r.latency_ms.toFixed(0) }} ms</span></div>
            <div class="body" v-code v-html="fmt(r.output)"></div>
            <div v-if="r.confirm" class="hfrow confirm" :data-confirm="r.confirm.tool">
              <code class="args">{{ JSON.stringify(r.confirm.arguments) }}</code>
              <button class="btn primary small" :disabled="r.running" @click="runTool(r, false)">{{ r.running ? 'Running…' : 'Run' }}</button>
              <button class="btn small" :disabled="r.running" @click="runTool(r, true)" :title="`Run ${r.confirm.tool} without asking from now on`">Always allow</button>
              <button class="btn small" :disabled="r.running" @click="cancelTool(r)">Cancel</button>
            </div>
            <div v-if="r.attachments && r.attachments.length" class="files"><FileCard v-for="f in r.attachments" :key="f.path" :file="f" :local="cfg.local" /></div>
            <details><summary>arguments</summary><code>{{ JSON.stringify(r.args) }}</code><template v-if="r.error"><br><code>{{ r.error }}</code></template></details>
          </div>
          <details><summary>routing decision · {{ m.profile }} · {{ m.engine }}</summary>
            <div v-for="d in m.decisions" :key="d.expert" class="row"><span>{{ d.expert }}</span><div class="bar"><i :class="{ hit: chosen(m, d) }" :style="{ width: pct(d.score) }"></i></div><span>{{ d.score.toFixed(2) }}</span></div>
          </details>
        </template>
      </div>
    </div></div>
    <form class="composer" id="form" @submit.prevent="send">
      <textarea id="msg" ref="box" rows="1" :placeholder="current && current.mode === 'build' ? 'Describe an app to build, e.g. a habit tracker with streaks…' : 'Message Noah…'" autocomplete="off" v-model="text" @input="autosize" @keydown.enter.exact="onEnter"></textarea>
      <div class="crow">
        <span class="pill" title="Orchestrator profile"><UiIcon name="cpu" :size="13" />
          <select id="profileSel" :value="current && current.name" @change="activeProfile = $event.target.value"><option v-for="p in enabledProfiles" :key="p.name" :value="p.name">{{ p.name }}</option></select></span>
        <span class="sp"></span>
        <span class="toggle" id="modeToggle"><button type="button" data-m="route" :class="{ on: current && current.mode === 'route' }" @click="setMode('route')">Route</button><button type="button" data-m="fanout" :class="{ on: current && current.mode === 'fanout' }" @click="setMode('fanout')">Fan-out</button><button type="button" data-m="build" :disabled="!builder" :title="builder ? `Describe an app and Noah builds a working prototype here (${builder.name})` : 'Enable a build orchestrator on the Orchestrators page first'" :class="{ on: current && current.mode === 'build' }" @click="setMode('build')">Build</button></span>
        <button class="send" id="send" :disabled="busy" title="Send"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg></button>
      </div>
    </form>
  </section>
</template>
