<script setup>
import UiIcon from './UiIcon.vue';
import { send } from '../lib/api.js';
import ExpertEditor from './ExpertEditor.vue';
import McpAddPanel from './McpAddPanel.vue';
import { ref } from 'vue';
import { cfg, expertSub, openExpert } from '../store.js';
import { signedIn } from '../lib/mcpAuth.js';

const adding = ref(false);
const kindTag = e => e.kind === 'generate' ? 'generates · ' + e.model : e.kind === 'mcp' ? `MCP · ${e.tools.length} tool${e.tools.length === 1 ? '' : 's'}` : 'tool';

async function toggle(e, ev) {
  const on = ev.target.checked;
  const r = await send('/api/experts/' + encodeURIComponent(e.name), 'PUT', { enabled: on });
  if (r.ok) e.enabled = on; else ev.target.checked = e.enabled;
}
</script>

<template>
  <ExpertEditor v-if="expertSub" :key="expertSub" :name="expertSub" />
  <section v-else class="view active" id="v-experts"><div class="panel" id="expertsPanel">
    <h2>Experts</h2><p class="lede">An expert is one small job: a model with a prompt (<b>generate</b>), or a Python tool. Select one to edit its settings or, under <b>Files</b>, every file it is made of (code included), with versions you can switch between. Create your own generate experts here; a new tool expert is a folder in <code>experts/</code> with a <code>tool.py</code>. An <b>MCP</b> expert is an MCP server: Noah routes to it, and it picks the right one of the server's tools.</p>
    <McpAddPanel v-if="adding" @close="adding = false" />
    <div v-for="(why, n) in cfg.expert_errors || {}" :key="n" class="hfbox broken" :data-broken="n">
      <b>{{ n }}</b> did not load, so Noah leaves it out: <code>{{ why }}</code>
      <button class="btn small" @click="openExpert(n)">Fix its files</button>
    </div>
    <div class="cards">
      <div v-for="e in cfg.experts" :key="e.name" class="card click" :class="{ off: !e.enabled }" :data-expert="e.name" title="Edit" @click="openExpert(e.name)">
        <h3><UiIcon name="box" /><span class="grow">{{ e.name }}</span>
          <label class="sw" :title="e.enabled ? 'Enabled' : 'Disabled'" @click.stop><input type="checkbox" role="switch" :data-toggle="e.name" :checked="e.enabled" @change="toggle(e, $event)"><i></i></label></h3>
        <p>{{ e.description }}</p>
        <div class="tags"><span class="tag" :class="{ mcp: e.kind === 'mcp' }">{{ kindTag(e) }}</span><span v-if="e.kind === 'mcp' && e.oauth && !signedIn(e)" class="tag rw">sign in</span><span class="tag">license: {{ e.license }}</span><span v-for="k in Object.keys(e.parameters.properties || {})" :key="k" class="tag">{{ k }}</span></div>
        <p v-if="e.kind === 'mcp'" class="meta server" :title="e.server">{{ e.server }}</p>
        <details @click.stop><summary>examples</summary><div v-for="x in e.examples" :key="x"><code>{{ x }}</code></div></details>
      </div>
      <div class="card new" data-new-expert @click="openExpert('new')"><UiIcon name="plus" />New expert</div>
      <div v-if="!adding" class="card new" data-new-mcp @click="adding = true"><UiIcon name="plus" />Add MCP server</div>
    </div>
  </div></section>
</template>
