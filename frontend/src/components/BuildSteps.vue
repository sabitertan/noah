<script setup>
// What a build (noah/builder.py) is doing: the plan's title, progress while the experts work, errors, and, when it used small
// models, where to find bigger ones (open models first, then the user's own Claude or OpenAI key).
import { shortModel } from '../lib/format.js';
import { go, openModelsTab } from '../store.js';

defineProps({ b: Object, error: String });
const LABEL = { html: 'Laying out the page', css: 'Styling it', js: 'Making it work' };
const CHANGE = { html: 'Updating the page', css: 'Restyling it', js: 'Updating what it does' };
</script>

<template>
  <div class="bsteps-wrap">
    <div class="head"><span class="badge">{{ b.change ? 'change' : 'build' }}</span><span>{{ b.title || 'Planning…' }}</span></div>
    <p v-if="b.summary && !b.change" class="bsum">{{ b.summary }}</p>
    <ol v-if="!b.html" class="bsteps">
      <li :class="b.steps.length ? 'done' : error ? 'error' : 'running'">{{ b.change ? 'Planning the change' : 'Planning what to build' }}</li>
      <li v-for="s in b.steps" :key="s.part" :class="s.state">{{ (b.change ? CHANGE : LABEL)[s.part] || s.part }}<span v-if="s.error" class="meta"> · {{ s.error }}</span></li>
    </ol>
    <div v-if="error" class="fnote bad">{{ error }}</div>
    <p v-if="b.html && b.change" class="fnote">Changed: {{ b.steps.map(s => (s.error ? '✕ ' : '') + s.part.toUpperCase()).join(', ') }}<template v-for="s in b.steps.filter(x => x.error)" :key="s.part"><br>{{ s.error }}</template></p>
    <details v-if="b.html && b.small?.length" class="hfbox bigger">
      <summary><b>Want a bigger app?</b> Use a larger model.</summary>
      This was built with small models ({{ b.small.map(shortModel).join(', ') }}). They make simple demos and often miss features.
      For more, give the Builder orchestrator a larger planner and builder model:
      <ul>
        <li><b>Open source on this machine:</b> <code>qwen2.5-coder:14b</code> or <code>qwen2.5-coder:32b</code>, <code>qwen3-coder:30b</code>, <code>devstral</code> or <code>gpt-oss:20b</code> in Ollama (needs a GPU with enough memory).</li>
        <li><b>Open source, hosted:</b> the same models through <a href="#" @click.prevent="openModelsTab('hf')">Hugging Face</a> or <a href="#" @click.prevent="openModelsTab('or')">OpenRouter</a>, nothing to download.</li>
        <li><b>Bring your own AI:</b> a <a href="#" @click.prevent="openModelsTab('an')">Claude</a> or <a href="#" @click.prevent="openModelsTab('oa')">OpenAI</a> API key.</li>
      </ul>
      Then pick them on the <a href="#" @click.prevent="go('orch')">Orchestrators</a> page (Builder: planner model and builder model).
    </details>
    <details v-if="b.html && b.features?.length"><summary>what it does</summary><ul><li v-for="f in b.features" :key="f">{{ f }}</li></ul></details>
  </div>
</template>
