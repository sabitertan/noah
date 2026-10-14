<script setup>
// Every model Noah can use, and where each one is served from: this machine (Ollama) or a hosted provider's API.
import { computed, reactive, watch } from 'vue';
import HostedPanel from './HostedPanel.vue';
import AzurePanel from './AzurePanel.vue';
import GgufDownloads from './GgufDownloads.vue';
import { norm, hostedKind, PROVIDERS } from '../lib/format.js';
import { tests, testModel as test, removeModel as remove } from '../lib/hostedActions.js';
import { cfg, hostedModels, profiles, isOn, modelsTab, openModelsTab } from '../store.js';

const rows = () => [...cfg.value.models.map(id => ({ id, kind: '' })), ...hostedModels.value.map(id => ({ id, kind: hostedKind(id) }))];
const where = kind => kind ? `${PROVIDERS[kind].label} · hosted` : 'Local · Ollama';
const usedByOrchestrators = m => profiles.value.flatMap(p => [
  ...(['llm_tools', 'llm_choice'].includes(p.engine) && norm(p.engine_model) === norm(m) ? [`${p.name} (orchestrator${isOn(p) ? '' : ', disabled'})`] : []),
  ...(p.engine !== 'llm_tools' && p.extractor === 'openai_compat' && norm(p.model) === norm(m) ? [`${p.name} (extractor${isOn(p) ? '' : ', disabled'})`] : []),
]);
const usedByExperts = m => cfg.value.experts.filter(e => e.kind === 'generate' && norm(e.model) === norm(m)).map(e => e.name);

// tabs: panels are created the first time you open them and then kept, so a half-typed search or a running download survives switching tabs
const seen = reactive({ local: true, hf: false, or: false, az: false });
watch(modelsTab, t => { seen[t] = true; }, { immediate: true });
const tabs = computed(() => [
  { id: 'local', label: 'My models', count: cfg.value.models.length + hostedModels.value.length },
  { id: 'hf', label: 'Hugging Face', count: (cfg.value.hf_models || []).length, key: cfg.value.hf?.configured },
  { id: 'or', label: 'OpenRouter', count: (cfg.value.or_models || []).length, key: cfg.value.or?.configured },
  { id: 'az', label: 'Azure Foundry', count: (cfg.value.az_models || []).length, key: cfg.value.az?.configured },
]);

</script>

<template>
  <section class="view active" id="v-models"><div class="panel" id="modelsPanel">
    <h2>Models</h2><p class="lede">Each model runs either <b>locally</b> (Ollama, downloaded to this machine) or <b>hosted</b> by Hugging Face, OpenRouter or your own Azure Foundry resource (called through their API with your own key, nothing downloaded). Pick either kind anywhere a model is chosen. Any OpenAI-compatible local server works too.</p>

    <div class="tabs" role="tablist">
      <button v-for="t in tabs" :key="t.id" class="tab" :class="{ active: modelsTab === t.id }" role="tab" :aria-selected="modelsTab === t.id" :data-tab="t.id" @click="openModelsTab(t.id)">
        {{ t.label }}<span class="count">{{ t.count }}</span><span v-if="t.key" class="dot on" title="key set"></span>
      </button>
    </div>

    <div v-show="modelsTab === 'local'" id="tab-local">
      <table v-if="rows().length" id="modelsTable">
        <thead><tr><th>Model</th><th>Served from</th><th>Used by orchestrators</th><th>Used by experts</th></tr></thead>
        <tbody><tr v-for="m in rows()" :key="m.id" :data-model="m.id">
          <td>{{ m.id }}</td>
          <td><span class="where" :class="m.kind || 'local'">{{ where(m.kind) }}</span>
            <div v-if="m.kind" class="rowact">
              <button class="btn small" :disabled="tests[m.id]?.busy" @click="test(m.id)">{{ tests[m.id]?.busy ? 'Testing…' : 'Test' }}</button>
              <button class="btn small" @click="remove(m.id)">Remove</button>
              <span v-if="tests[m.id]?.text" class="fnote" :class="{ bad: tests[m.id].ok === false }">{{ tests[m.id].text }}</span>
            </div></td>
          <td><div v-for="u in usedByOrchestrators(m.id)" :key="u">{{ u }}</div><span v-if="!usedByOrchestrators(m.id).length" class="meta">—</span></td>
          <td><div v-for="u in usedByExperts(m.id)" :key="u">{{ u }}</div><span v-if="!usedByExperts(m.id).length" class="meta">—</span></td>
        </tr></tbody>
      </table>
      <p v-else class="lede">No models yet. Is Ollama running? Or add a hosted model from one of the provider tabs.</p>
    </div>

    <HostedPanel v-if="seen.hf" v-show="modelsTab === 'hf'" provider="hf" search-hint="Search Hugging Face models, e.g. qwen, llama, gemma…" manual-hint="Or any model id, e.g. Qwen/Qwen2.5-7B-Instruct:fastest"
      help="Needed for hosted models. Create a token at huggingface.co/settings/tokens with the “Make calls to Inference Providers” permission.">
      <template #extra="{ results, searching }"><GgufDownloads :rows="results.gguf || []" :searching="searching" /></template>
    </HostedPanel>
    <HostedPanel v-if="seen.or" v-show="modelsTab === 'or'" provider="or" search-hint="Search OpenRouter models, e.g. llama, gpt, claude, free…" manual-hint="Or any model id, e.g. meta-llama/llama-3.3-70b-instruct:free"
      help="Needed for hosted models. Create a key at openrouter.ai/keys." />
    <AzurePanel v-if="seen.az" v-show="modelsTab === 'az'" />
  </div></section>
</template>
