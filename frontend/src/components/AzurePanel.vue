<script setup>
// Azure Foundry: connect your own resource (endpoint + API key, or your Azure CLI sign-in) and add the deployments you created there.
// There is no public catalogue to search, so deployments are added by name. Nothing is downloaded.
import { ref, computed } from 'vue';
import { send } from '../lib/api.js';
import { PROVIDERS } from '../lib/format.js';
import { tests, testModel, removeModel } from '../lib/hostedActions.js';
import { cfg } from '../store.js';

const P = PROVIDERS.az;
const status = computed(() => cfg.value.az || {});
const mine = computed(() => cfg.value.az_models || []);

const endpoint = ref(''), auth = ref('key'), key = ref(''), busy = ref(false), msg = ref('');
async function connect() {
  busy.value = true; msg.value = '';
  try {
    const r = await send('/api/az/token', 'PUT', { endpoint: endpoint.value, auth: auth.value, token: auth.value === 'key' ? key.value : '' });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || 'could not connect');
    const { name, ...st } = j;
    cfg.value.az = st; key.value = ''; endpoint.value = '';
    msg.value = `Connected to ${name}.`;
  } catch (e) { msg.value = e.message; }
  busy.value = false;
}
async function disconnect() {
  const r = await fetch('/api/az/token', { method: 'DELETE' });
  const j = await r.json();
  if (r.ok) { cfg.value.az = j; msg.value = 'Disconnected. The key and endpoint were deleted from this machine.'; } else msg.value = j.detail;
}

const deployment = ref(''), addMsg = ref('');
async function add() {
  addMsg.value = '';
  const r = await send('/api/az/my', 'POST', { id: deployment.value });
  const j = await r.json();
  if (r.ok) { cfg.value.az_models = j.models; deployment.value = ''; } else addMsg.value = j.detail;
}
</script>

<template>
  <div id="azPanel" class="hf">
    <h2>Add from Azure Foundry</h2>
    <div class="hfbox">
      <div v-if="status.configured" class="hfrow">
        <span class="where az">Connected</span>
        <span class="fnote">{{ status.endpoint }} · {{ status.auth === 'cli' ? 'signed in with the Azure CLI (no key stored)' : 'API key stored on this machine' }}{{ status.source === 'env' ? ' (from environment variables)' : '' }}. {{ msg }}</span>
        <button v-if="status.source === 'file'" class="btn small" @click="disconnect">Disconnect</button>
      </div>
      <form v-else @submit.prevent="connect">
        <div class="hfrow"><input id="az-endpoint" v-model="endpoint" placeholder="Endpoint, e.g. https://my-resource.openai.azure.com" autocomplete="off"></div>
        <div class="hfrow">
          <label class="choice"><input type="radio" value="key" v-model="auth" id="az-auth-key"> API key</label>
          <label class="choice"><input type="radio" value="cli" v-model="auth" id="az-auth-cli"> Azure CLI sign-in (<code>az login</code>, no key)</label>
        </div>
        <div class="hfrow">
          <input v-if="auth === 'key'" id="az-token" type="password" v-model="key" placeholder="API key from the Foundry portal" autocomplete="off">
          <button class="btn primary small" :disabled="busy || !endpoint || (auth === 'key' && !key)">{{ busy ? 'Checking…' : 'Connect' }}</button>
        </div>
        <p class="fnote">Find the endpoint and key under your resource in the Azure portal or the Foundry portal (Keys and Endpoint). The endpoint must be an Azure host (<code>*.openai.azure.com</code>, <code>*.services.ai.azure.com</code> or <code>*.cognitiveservices.azure.com</code>). The key is stored in a private file on this machine. With the Azure CLI option nothing is stored; Noah asks <code>az</code> for a short-lived token when it needs one.</p>
      </form>
      <div v-if="!status.configured && msg" class="fnote bad">{{ msg }}</div>
      <p class="fnote warn">Requests go to your own Azure resource and may incur Azure charges. Local models never leave this machine.</p>
    </div>

    <h3>Your deployments <span class="meta">no download, runs on your Azure resource</span></h3>
    <p class="fnote">Azure has no public model catalogue: add the <b>deployment name</b> you gave a model in the Foundry portal (Deployments), not the model name. It works for OpenAI models and for others you deployed there (Llama, DeepSeek, Grok, …).</p>
    <form class="hfrow" @submit.prevent="add">
      <input id="az-manual" v-model="deployment" placeholder="Deployment name, e.g. gpt-4o-mini">
      <button class="btn small" :disabled="!deployment.trim()">Add</button><span v-if="addMsg" class="fnote bad">{{ addMsg }}</span>
    </form>
    <table v-if="mine.length" id="azDeployments">
      <thead><tr><th>Deployment</th><th></th></tr></thead>
      <tbody><tr v-for="m in mine" :key="m" :data-model="m">
        <td>{{ m.slice(P.prefix.length) }}</td>
        <td><div class="rowact">
          <button class="btn small" :disabled="tests[m]?.busy" @click="testModel(m)">{{ tests[m]?.busy ? 'Testing…' : 'Test' }}</button>
          <button class="btn small" @click="removeModel(m)">Remove</button>
          <span v-if="tests[m]?.text" class="fnote" :class="{ bad: tests[m].ok === false }">{{ tests[m].text }}</span>
        </div></td>
      </tr></tbody>
    </table>
  </div>
</template>
