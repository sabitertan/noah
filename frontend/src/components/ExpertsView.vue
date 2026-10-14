<script setup>
import UiIcon from './UiIcon.vue';
import { send } from '../lib/api.js';
import ExpertEditor from './ExpertEditor.vue';
import { cfg, expertSub, openExpert } from '../store.js';

async function toggle(e, ev) {
  const on = ev.target.checked;
  const r = await send('/api/experts/' + encodeURIComponent(e.name), 'PUT', { enabled: on });
  if (r.ok) e.enabled = on; else ev.target.checked = e.enabled;
}
</script>

<template>
  <ExpertEditor v-if="expertSub" :key="expertSub" :name="expertSub" />
  <section v-else class="view active" id="v-experts"><div class="panel" id="expertsPanel">
    <h2>Experts</h2><p class="lede">An expert is one small job: a model with a prompt (<b>generate</b>), or a Python tool. Select one to edit how it is routed (and, for generate experts, its prompt and model), or create your own. Tool experts are folders in <code>experts/</code> with a <code>tool.py</code>; add those by hand.</p>
    <div class="cards">
      <div v-for="e in cfg.experts" :key="e.name" class="card click" :class="{ off: !e.enabled }" :data-expert="e.name" title="Edit" @click="openExpert(e.name)">
        <h3><UiIcon name="box" /><span class="grow">{{ e.name }}</span>
          <label class="sw" :title="e.enabled ? 'Enabled' : 'Disabled'" @click.stop><input type="checkbox" role="switch" :data-toggle="e.name" :checked="e.enabled" @change="toggle(e, $event)"><i></i></label></h3>
        <p>{{ e.description }}</p>
        <div class="tags"><span class="tag">{{ e.kind === 'generate' ? 'generates · ' + e.model : 'tool' }}</span><span class="tag">license: {{ e.license }}</span><span v-for="k in Object.keys(e.parameters.properties || {})" :key="k" class="tag">{{ k }}</span></div>
        <details @click.stop><summary>examples</summary><div v-for="x in e.examples" :key="x"><code>{{ x }}</code></div></details>
      </div>
      <div class="card new" data-new-expert @click="openExpert('new')"><UiIcon name="plus" />New expert</div>
    </div>
  </div></section>
</template>
