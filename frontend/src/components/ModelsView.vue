<script setup>
// Every model Noah can use, and where each one is served from: this machine (Ollama) or Hugging Face's hosted API.
import { ref } from 'vue';
import HfPanel from './HfPanel.vue';
import { norm, isHosted } from '../lib/format.js';
import { send } from '../lib/api.js';
import { cfg, hostedModels, profiles, isOn } from '../store.js';

const rows = () => [...cfg.value.models.map(id => ({ id, hosted: false })), ...hostedModels.value.map(id => ({ id, hosted: true }))];
const usedByOrchestrators = m => profiles.value.flatMap(p => [
  ...(['llm_tools', 'llm_choice'].includes(p.engine) && norm(p.engine_model) === norm(m) ? [`${p.name} (orchestrator${isOn(p) ? '' : ', disabled'})`] : []),
  ...(p.engine !== 'llm_tools' && p.extractor === 'openai_compat' && norm(p.model) === norm(m) ? [`${p.name} (extractor${isOn(p) ? '' : ', disabled'})`] : []),
]);
const usedByExperts = m => cfg.value.experts.filter(e => e.kind === 'generate' && norm(e.model) === norm(m)).map(e => e.name);

const tests = ref({});  // model id -> { busy } | { ok, text }
async function test(id) {
  tests.value[id] = { busy: true };
  try {
    const j = await (await send('/api/hf/test', 'POST', { id })).json();
    tests.value[id] = j.ok ? { ok: true, text: `works: “${j.reply}” in ${j.ms} ms` } : { ok: false, text: j.error || j.detail };
  } catch (e) { tests.value[id] = { ok: false, text: e.message }; }
}
async function remove(id) {
  const r = await fetch('/api/hf/my/' + id.replace(/^hf:/, ''), { method: 'DELETE' });
  if (r.ok) cfg.value.hf_models = (await r.json()).models;
}
</script>

<template>
  <section class="view active" id="v-models"><div class="panel" id="modelsPanel">
    <h2>Models</h2><p class="lede">Each model runs either <b>locally</b> (Ollama, downloaded to this machine) or <b>hosted</b> on Hugging Face (called through their API with your token, nothing downloaded). Pick either one anywhere a model is chosen. Any OpenAI-compatible local server works too.</p>
    <table v-if="rows().length" id="modelsTable">
      <thead><tr><th>Model</th><th>Served from</th><th>Used by orchestrators</th><th>Used by experts</th></tr></thead>
      <tbody><tr v-for="m in rows()" :key="m.id" :data-model="m.id">
        <td>{{ m.id }}</td>
        <td><span class="where" :class="m.hosted ? 'hf' : 'local'">{{ m.hosted ? 'Hugging Face · hosted' : 'Local · Ollama' }}</span>
          <div v-if="m.hosted" class="rowact">
            <button class="btn small" :disabled="tests[m.id]?.busy" @click="test(m.id)">{{ tests[m.id]?.busy ? 'Testing…' : 'Test' }}</button>
            <button class="btn small" @click="remove(m.id)">Remove</button>
            <span v-if="tests[m.id]?.text" class="fnote" :class="{ bad: tests[m.id].ok === false }">{{ tests[m.id].text }}</span>
          </div></td>
        <td><div v-for="u in usedByOrchestrators(m.id)" :key="u">{{ u }}</div><span v-if="!usedByOrchestrators(m.id).length" class="meta">—</span></td>
        <td><div v-for="u in usedByExperts(m.id)" :key="u">{{ u }}</div><span v-if="!usedByExperts(m.id).length" class="meta">—</span></td>
      </tr></tbody>
    </table>
    <p v-else class="lede">No models yet. Is Ollama running? Or add a hosted Hugging Face model below.</p>

    <HfPanel />
  </div></section>
</template>
