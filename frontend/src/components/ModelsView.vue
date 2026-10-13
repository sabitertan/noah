<script setup>
import { norm } from '../lib/format.js';
import { cfg, profiles, isOn } from '../store.js';

const usedByOrchestrators = m => profiles.value.flatMap(p => [
  ...(['llm_tools', 'llm_choice'].includes(p.engine) && norm(p.engine_model) === norm(m) ? [`${p.name} (orchestrator${isOn(p) ? '' : ', disabled'})`] : []),
  ...(p.engine !== 'llm_tools' && p.extractor === 'openai_compat' && norm(p.model) === norm(m) ? [`${p.name} (extractor${isOn(p) ? '' : ', disabled'})`] : []),
]);
const usedByExperts = m => cfg.value.experts.filter(e => e.kind === 'generate' && norm(e.model) === norm(m)).map(e => e.name);
</script>

<template>
  <section class="view active" id="v-models"><div class="panel" id="modelsPanel">
    <h2>Models</h2><p class="lede">Local models served by Ollama. Pull more with <code>ollama pull &lt;name&gt;</code>; any OpenAI-compatible server works through the same extractor.</p>
    <table v-if="cfg.models.length">
      <thead><tr><th>Model</th><th>Used by orchestrators</th><th>Used by experts</th></tr></thead>
      <tbody><tr v-for="m in cfg.models" :key="m">
        <td>{{ m }}</td>
        <td><div v-for="u in usedByOrchestrators(m)" :key="u">{{ u }}</div><span v-if="!usedByOrchestrators(m).length" class="meta">—</span></td>
        <td><div v-for="u in usedByExperts(m)" :key="u">{{ u }}</div><span v-if="!usedByExperts(m).length" class="meta">—</span></td>
      </tr></tbody>
    </table>
    <p v-else class="lede">No local models found. Is Ollama running?</p>
  </div></section>
</template>
