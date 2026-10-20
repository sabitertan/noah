<script setup>
// Create or edit an expert. Settings: a form for the routing text and, for a generate expert, its model and prompt.
// Files: every file of the expert in a code editor, checked before saving and kept as versions (ExpertFiles).
import { reactive, ref, computed, onMounted, defineAsyncComponent } from 'vue';
import { send, getJson } from '../lib/api.js';
import { fmt } from '../lib/format.js';
import { cfg, modelGroups, refreshConfig, go } from '../store.js';
import { signIn, signOut, signedIn } from '../lib/mcpAuth.js';

const ExpertFiles = defineAsyncComponent(() => import('./ExpertFiles.vue'));   // brings Monaco: loaded only when opened

const props = defineProps({ name: String });   // an expert's name, or 'new'
const isNew = props.name === 'new';
const kind = ref('generate');
const isTool = computed(() => kind.value === 'tool');
const isMcp = computed(() => kind.value === 'mcp');
const mcp = reactive({ server: '', tools: [], refreshing: false, note: '' });   // an MCP expert: where its server is, and its tools
const tab = ref('settings'), filesRef = ref(null);
async function openTab(t) {
  if (t === tab.value) return;
  if (tab.value === 'files' && filesRef.value?.dirty && !confirm('Leave the files with unsaved changes? They will be lost.')) return;
  if (t === 'settings' && tab.value === 'files') await reloadForm();   // the files may have changed expert.yaml
  tab.value = t;
}
function back() {
  if (tab.value === 'files' && filesRef.value?.dirty && !confirm('Leave the files with unsaved changes? They will be lost.')) return;
  go('experts');
}
const form = reactive({ choose_tool: false, defaults: '', name: '', description: '', examples: '', model: cfg.value.models[0] || '', system: '', shots: [], strip: '', license: 'user' });
const loading = ref(!isNew), error = ref(''), saving = ref(false), confirming = ref(false);
const groups = computed(() => modelGroups('', form.model));
const lines = text => text.split('\n').map(l => l.trim()).filter(Boolean);

async function reloadForm() {
  try {
    const m = await getJson(`/api/experts/${encodeURIComponent(props.name)}/manifest`);
    if (m.detail) throw new Error(m.detail);
    kind.value = m.kind;
    if (m.kind === 'mcp') { Object.assign(mcp, { server: m.server, tools: m.tools }); form.choose_tool = m.choose_tool; form.defaults = Object.entries(m.defaults || {}).map(([k, v]) => `${k}: ${v}`).join('\n'); }
    Object.assign(form, { name: m.name, description: m.description, examples: (m.examples || []).join('\n'), model: m.kind === 'mcp' ? m.model : m.model || form.model, system: m.system || '',
      shots: (m.shots || []).map(s => ({ ...s })), strip: (m.strip || []).join('\n'), license: m.license || 'user' });
    error.value = '';
  } catch (e) { error.value = e.message; }
}
onMounted(async () => {
  if (isNew) return;
  await reloadForm();
  if (isTool.value || error.value || props.name in (cfg.value.expert_errors || {})) tab.value = 'files';   // a tool is mostly its code; a broken expert is fixed there
  if (tab.value === 'files') error.value = '';
  loading.value = false;
});

const payload = () => ({
  name: isNew ? form.name.trim() : props.name, description: form.description, examples: lines(form.examples),
  ...(isMcp.value ? { model: form.model, choose_tool: form.choose_tool,
    defaults: Object.fromEntries(lines(form.defaults).map(l => [l.slice(0, l.indexOf(':')).trim(), l.slice(l.indexOf(':') + 1).trim()]).filter(([k, v]) => k && v)) } : isTool.value ? {} : { model: form.model, system: form.system, license: form.license,
    shots: form.shots.filter(s => s.user.trim() && s.assistant.trim()), strip: lines(form.strip) }),
});
const explain = d => typeof d === 'string' ? d : Array.isArray(d) ? d.map(e => `${(e.loc || []).slice(1).join(' ')}: ${String(e.msg).replace(/^Value error, /, '')}`).join('. ') : 'could not save';

async function save() {
  saving.value = true; error.value = '';
  try {
    const r = await send(isNew ? '/api/experts' : `/api/experts/${encodeURIComponent(props.name)}/manifest`, isNew ? 'POST' : 'PUT', payload());
    if (!r.ok) throw new Error(explain((await r.json().catch(() => ({}))).detail));
    await refreshConfig(); go('experts');
  } catch (e) { error.value = e.message; }
  saving.value = false;
}
async function remove() {
  const r = await fetch(`/api/experts/${encodeURIComponent(props.name)}`, { method: 'DELETE' });
  if (!r.ok) { error.value = explain((await r.json().catch(() => ({}))).detail); confirming.value = false; return; }
  await refreshConfig(); go('experts');
}

const tryMessage = ref(''), trying = ref(false), tried = ref(null);
async function tryIt() {
  trying.value = true; tried.value = null;
  try {
    const r = await send('/api/experts/preview', 'POST', { ...payload(), message: tryMessage.value || lines(form.examples)[0] || 'hello' });
    const j = await r.json();
    tried.value = r.ok ? j : { ok: false, error: explain(j.detail) };
  } catch (e) { tried.value = { ok: false, error: e.message }; }
  trying.value = false;
}
const addShot = () => form.shots.push({ user: '', assistant: '' });

// a remote MCP server that signs in with OAuth: its card in the config says where it is and whether we have tokens
const card = computed(() => cfg.value.experts.find(e => e.name === props.name));
const auth = reactive({ busy: false, note: '', tick: 0 });
const isSignedIn = computed(() => (auth.tick, card.value ? signedIn(card.value) : false));
async function doSignIn() {
  auth.busy = true; auth.note = '';
  try {
    const j = await signIn(card.value);
    auth.note = `Signed in. ${j.tools} tools, ${j.with_examples} with examples.`;
    await refreshConfig(); await reloadForm();
  } catch (e) { auth.note = e.message; }
  auth.busy = false; auth.tick++;
}
async function doSignOut() {
  await signOut(card.value); await refreshConfig(); auth.tick++; auth.note = 'Signed out: the tokens were deleted.';
}

async function refreshTools() {
  mcp.refreshing = true; mcp.note = '';
  try {
    const r = await send(`/api/mcp/${encodeURIComponent(props.name)}/refresh`, 'POST', { model: form.model });
    const j = await r.json();
    if (!r.ok) throw new Error(explain(j.detail));
    mcp.note = `${j.tools} tools, ${j.with_examples} with examples.` + (j.problems.length ? ' No examples for: ' + j.problems.join('; ') : '');
    await reloadForm(); await refreshConfig();
  } catch (e) { mcp.note = e.message; }
  mcp.refreshing = false;
}
</script>

<template>
  <section class="view active" id="v-experts"><div class="panel" :class="{ wide: tab === 'files' }" id="expertEditor">
    <button class="btn small back" id="e-back" @click="back">← Experts</button>
    <h2>{{ isNew ? 'New expert' : 'Edit ' + name }}</h2>
    <p v-if="loading" class="lede">Loading…</p>
    <template v-else>
      <div v-if="!isNew" class="tabs" role="tablist">
        <button class="tab" :class="{ active: tab === 'settings' }" role="tab" :aria-selected="tab === 'settings'" data-etab="settings" @click="openTab('settings')">Settings</button>
        <button class="tab" :class="{ active: tab === 'files' }" role="tab" :aria-selected="tab === 'files'" data-etab="files" @click="openTab('files')">Files</button>
      </div>
      <template v-if="tab === 'files'">
        <p class="lede">Every file of <code>experts/{{ name }}/</code>. Saving checks the draft first (YAML, the manifest, Python syntax and lint, then loading every expert with it) and writes nothing if that finds an error. Each save is kept as a version you can compare and switch back to.</p>
        <ExpertFiles ref="filesRef" :name="name" />
      </template>
    <template v-else>
      <p v-if="isNew" class="lede">A <b>generate</b> expert is a small model with a job: you describe it, give example messages so Noah can route to it, and write the prompt that makes the model do that one thing.</p>
      <p v-if="isMcp" class="lede" id="e-mcpnote">This expert is an MCP server (<code>{{ mcp.server }}</code>). Noah routes a message here when it is close to any of its tools; the expert then takes the closest tool, and its model fills that tool's arguments from the message (and the defaults below). Tools not marked read-only ask before they run.</p>
      <p v-if="isTool" class="lede" id="e-toolnote">This expert runs Python code (<code>experts/{{ name }}/tool.py</code>): change it in the <b>Files</b> tab. Here you can change how Noah routes messages to it.</p>

      <div class="form wide">
        <label>Name</label>
        <div><input id="e-name" v-model="form.name" :disabled="!isNew" placeholder="e.g. translator" autocomplete="off">
          <div class="fnote">{{ isNew ? 'Lowercase letters, digits and underscores. It cannot be changed later.' : 'A name cannot be changed. Create a new expert and delete this one to rename.' }}</div></div>

        <label>Description</label>
        <div><textarea id="e-description" v-model="form.description" rows="2" placeholder="What this expert does, in a sentence"></textarea>
          <div class="fnote">Used for routing and shown to orchestrator models, so be specific.</div></div>

        <label>Example messages</label>
        <div><textarea id="e-examples" v-model="form.examples" rows="5" placeholder="One per line, e.g.&#10;translate this to French&#10;how do you say thanks in Spanish"></textarea>
          <div class="fnote">Real messages this expert should get, one per line. Noah routes by how close a message is to these.</div></div>

        <template v-if="isMcp">
          <label>Model</label>
          <div><select id="e-model" v-model="form.model"><option value="">The orchestrator's tiny model</option>
            <optgroup v-for="g in groups" :key="g.label" :label="g.label"><option v-for="m in g.models" :key="m">{{ m }}</option></optgroup></select>
            <div class="fnote">Fills the arguments of the tool Noah picked (the closest to the message); also writes examples for new tools.</div>
            <label class="choice"><input type="checkbox" id="e-choose" v-model="form.choose_tool"> Let the model choose among the closest tools</label>
            <div class="fnote">For models that are good at tool calling (large or hosted ones). Small local models choose worse than the closest match.</div></div>

          <template v-if="card?.oauth || isSignedIn">
            <label>Sign-in</label>
            <div id="e-signin" class="hfrow">
              <span v-if="isSignedIn" class="tag ro">signed in</span><span v-else class="tag rw">not signed in</span>
              <button v-if="!isSignedIn" class="btn primary small" id="e-signin-btn" :disabled="auth.busy" @click="doSignIn">{{ auth.busy ? 'Signing in…' : 'Sign in' }}</button>
              <button v-else class="btn small" id="e-signout-btn" @click="doSignOut">Sign out</button>
              <button v-if="isSignedIn" class="btn small" :disabled="auth.busy" @click="doSignIn">Sign in again</button>
              <span v-if="auth.note" class="fnote">{{ auth.note }}</span>
            </div>
          </template>

          <label>Default arguments</label>
          <div><textarea id="e-defaults" v-model="form.defaults" rows="2" placeholder="One per line, e.g.&#10;path: D:/notes&#10;owner: my-org"></textarea>
            <div class="fnote">Used when a message does not say: a value the model made up (it is not in the message) is replaced by the default with the same name.</div></div>

          <label>Tools</label>
          <div id="e-tools">
            <details v-for="t in mcp.tools" :key="t.name" class="mcptool"><summary><code>{{ t.name }}</code> <span class="tag" :class="t.read_only ? 'ro' : 'rw'">{{ t.read_only ? 'read-only' : 'asks first' }}</span> <span class="meta">{{ t.description }}</span></summary>
              <div v-for="x in t.examples" :key="x" class="fnote">“{{ x }}”</div><div v-if="!t.examples.length" class="fnote warn">No examples: routed by its description only.</div></details>
            <div class="hfrow"><button class="btn small" id="e-refresh" :disabled="mcp.refreshing" @click="refreshTools">{{ mcp.refreshing ? 'Refreshing…' : 'Refresh tools' }}</button>
              <span v-if="mcp.note" class="fnote">{{ mcp.note }}</span></div>
            <div class="fnote">Asks the server for its tools again. Tools that did not change keep their examples (edit them in <code>tools.json</code> under Files).</div>
          </div>
        </template>
        <template v-if="!isTool && !isMcp">
          <label>Model</label>
          <div><select id="e-model" v-model="form.model"><option v-if="!form.model" value="">Choose a model…</option>
            <optgroup v-for="g in groups" :key="g.label" :label="g.label"><option v-for="m in g.models" :key="m">{{ m }}</option></optgroup></select>
            <div class="fnote">Local or hosted. Add more on the Models page.</div></div>

          <label>System prompt</label>
          <div><textarea id="e-system" v-model="form.system" rows="6" placeholder="You are a … expert. Answer only with … Keep it short."></textarea>
            <div class="fnote">What the model is told before every message. Tell it its one job and what to leave out.</div></div>

          <label>Few-shot examples</label>
          <div>
            <div v-for="(s, i) in form.shots" :key="i" class="shot">
              <textarea v-model="s.user" rows="2" placeholder="User says…"></textarea>
              <textarea v-model="s.assistant" rows="3" placeholder="Expert answers…"></textarea>
              <button class="btn small" type="button" @click="form.shots.splice(i, 1)">Remove</button>
            </div>
            <button class="btn small" id="e-addshot" type="button" :disabled="form.shots.length >= 6" @click="addShot">Add an example exchange</button>
            <div class="fnote">Optional. Small models follow an example better than an instruction.</div>
          </div>

          <label>Output filters</label>
          <div><textarea id="e-strip" v-model="form.strip" rows="2" placeholder="One regular expression per line, removed from every answer"></textarea>
            <div class="fnote">Optional. For things the model keeps adding (for example <code>```\w*</code> to drop code fences).</div></div>

          <label>License</label>
          <div><input id="e-license" v-model="form.license" placeholder="user"></div>
        </template>
      </div>

      <div class="actions">
        <button class="btn primary" id="e-save" :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Save' }}</button>
        <button class="btn" id="e-cancel" @click="go('experts')">Cancel</button>
        <template v-if="!isNew && !isTool">
          <button v-if="!confirming" class="btn danger" id="e-delete" @click="confirming = true">Delete</button>
          <template v-else><span class="fnote">Delete {{ name }}?</span><button class="btn danger" id="e-delete-yes" @click="remove">Yes, delete</button><button class="btn" @click="confirming = false">No</button></template>
        </template>
      </div>
      <div v-if="error" class="fnote bad" id="e-error">{{ error }}</div>

      <div v-if="!isTool && !isMcp" class="hfbox tryit">
        <h3>Try it <span class="meta">runs this draft on a message; nothing is saved</span></h3>
        <div class="hfrow"><input id="e-try-message" v-model="tryMessage" :placeholder="lines(form.examples)[0] || 'A message to send'" @keydown.enter.prevent="tryIt">
          <button class="btn small" id="e-try" :disabled="trying || !form.model || !form.system.trim()" @click="tryIt">{{ trying ? 'Running…' : 'Run' }}</button></div>
        <div v-if="tried" id="e-try-out" class="a" :class="{ err: !tried.ok }">
          <div v-if="tried.ok" class="body" v-code v-html="fmt(tried.output)"></div><div v-else class="body">{{ tried.error }}</div>
          <div v-if="tried.ok" class="head"><span>{{ tried.ms }} ms</span></div>
        </div>
      </div>
    </template>
    </template>
  </div></section>
</template>
