<script setup>
import UiIcon from './UiIcon.vue';
import { send } from '../lib/api.js';
import { cfg } from '../store.js';

async function toggle(e, ev) {
  const on = ev.target.checked;
  const r = await send('/api/experts/' + encodeURIComponent(e.name), 'PUT', { enabled: on });
  if (r.ok) e.enabled = on; else ev.target.checked = e.enabled;
}
</script>

<template>
  <section class="view active" id="v-experts"><div class="panel" id="expertsPanel">
    <h2>Experts</h2><p class="lede">Each expert is a folder in <code>experts/</code>: a manifest, a small model and a tool. Drop one in and restart to add it.</p>
    <div class="cards">
      <div v-for="e in cfg.experts" :key="e.name" class="card" :class="{ off: !e.enabled }">
        <h3><UiIcon name="box" /><span class="grow">{{ e.name }}</span>
          <label class="sw" :title="e.enabled ? 'Enabled' : 'Disabled'"><input type="checkbox" role="switch" :data-toggle="e.name" :checked="e.enabled" @change="toggle(e, $event)"><i></i></label></h3>
        <p>{{ e.description }}</p>
        <div class="tags"><span class="tag">{{ e.kind === 'generate' ? 'generates · ' + e.model : 'tool' }}</span><span class="tag">license: {{ e.license }}</span><span v-for="k in Object.keys(e.parameters.properties || {})" :key="k" class="tag">{{ k }}</span></div>
        <details><summary>examples</summary><div v-for="x in e.examples" :key="x"><code>{{ x }}</code></div></details>
      </div>
    </div>
  </div></section>
</template>
