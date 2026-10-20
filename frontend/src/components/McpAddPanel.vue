<script setup>
// Add MCP servers: paste the JSON other MCP clients use, pick the model that writes routing examples (and later picks a tool),
// and each server becomes one expert. See noah/mcp_expert.py.
import { ref, computed } from 'vue';
import { codeFetch } from '../lib/api.js';
import { signIn } from '../lib/mcpAuth.js';
import { cfg, modelGroups, refreshConfig, openExpert, IS_WASM } from '../store.js';

const emit = defineEmits(['close']);
const config = ref(''), name = ref(''), model = ref(''), busy = ref(false), error = ref(''), result = ref(null);
const groups = computed(() => modelGroups('', model.value));
const single = computed(() => { try { const j = JSON.parse(config.value); return !!(j.url || j.command); } catch { return false; } });
async function signInTo(a) {   // a server that wanted sign-in was added without tools; signing in lists them
  a.signing = true; a.error = '';
  try { Object.assign(a, await signIn(a), { sign_in: false }); await refreshConfig(); } catch (e) { a.error = e.message; }
  a.signing = false;
}
const EXAMPLE = `{
  "mcpServers": {
    "fetch": { "command": "uvx", "args": ["mcp-server-fetch"] },
    "github": { "url": "https://api.githubcopilot.com/mcp/", "headers": { "Authorization": "Bearer \${GITHUB_TOKEN}" } }
  }
}`;

async function add() {
  busy.value = true; error.value = ''; result.value = null;
  try {
    const r = await codeFetch('/api/mcp/servers', 'POST', { config: config.value, name: name.value, model: model.value });
    const j = await r.json();
    if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'could not add');
    result.value = j;
    if (j.added.length) { await refreshConfig(); config.value = ''; name.value = ''; }
  } catch (e) { error.value = e.message; }
  busy.value = false;
}
</script>

<template>
  <div class="hfbox" id="mcpAdd">
    <h3>Add MCP servers <span class="meta">each server becomes one expert</span></h3>
    <p v-if="!cfg.mcp?.available" class="fnote bad">{{ cfg.mcp?.why || 'MCP servers are not available here.' }}</p>
    <template v-else>
      <p class="fnote">Paste the configuration you use in Claude Desktop, VS Code or Cursor (<code>mcpServers</code> or <code>servers</code>), or one server.
        Noah connects, lists the tools, and has the model below write a few example requests per tool, so it can tell which tool a message is for.
        <code>${NAME}</code> is read from an environment variable, so tokens can stay out of the files.</p>
      <textarea id="mcp-config" v-model="config" rows="7" :placeholder="EXAMPLE" spellcheck="false"></textarea>
      <div class="hfrow">
        <input v-if="single" id="mcp-name" v-model="name" placeholder="Name for this server, e.g. github">
        <select id="mcp-model" v-model="model">
          <option value="">No model: route by tool descriptions only</option>
          <optgroup v-for="g in groups" :key="g.label" :label="g.label"><option v-for="m in g.models" :key="m">{{ m }}</option></optgroup>
        </select>
        <button class="btn primary small" id="mcp-add" :disabled="busy || !config.trim()" @click="add">{{ busy ? 'Connecting…' : 'Add' }}</button>
        <button class="btn small" @click="emit('close')">Close</button>
      </div>
      <p class="fnote">The model writes the examples now and picks a tool for each message later (change it in the expert's settings). Without one, tools are matched by their descriptions, and only tools without arguments can run.
        A local server (<code>command</code>) runs on the computer running Noah; adding one is allowed from that computer only.
        A server that asks you to sign in (OAuth) is added first; press its <b>Sign in</b> and a window opens for it.</p>
      <p v-if="IS_WASM" class="fnote warn">In the browser, only remote servers (<code>url</code>) work, and only ones that allow this page
        (CORS). Sign-ins are kept in this browser.</p>
      <p v-if="busy" class="fnote">Starting servers and writing examples can take a minute (the first <code>npx</code> or <code>uvx</code> run downloads the server).</p>
      <p v-if="error" class="fnote bad" id="mcp-error">{{ error }}</p>
      <div v-if="result" id="mcp-result">
        <div v-for="a in result.added" :key="a.name" class="fnote">
          ✓ <a href="#" @click.prevent="openExpert(a.name)"><b>{{ a.name }}</b></a>:
          <template v-if="a.sign_in">{{ a.server }} asks you to sign in before it lists its tools.
            <button class="btn primary small" :data-signin="a.name" :disabled="a.signing" @click="signInTo(a)">{{ a.signing ? 'Signing in…' : 'Sign in' }}</button>
            <span v-if="a.error" class="bad"> {{ a.error }}</span></template>
          <template v-else>{{ a.tools }} tool{{ a.tools === 1 ? '' : 's' }} from {{ a.server }}, {{ a.with_examples }} with examples.</template>
          <span v-if="a.problems?.length" class="warn"> No examples for: {{ a.problems.join('; ') }}</span>
        </div>
        <div v-for="f in result.failed" :key="f.name" class="fnote bad">✗ <b>{{ f.name }}</b>: {{ f.error }}</div>
      </div>
    </template>
  </div>
</template>
