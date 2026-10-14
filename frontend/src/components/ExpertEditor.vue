<script setup>
// Create or edit an expert. A generate expert (a model plus a prompt) is fully editable; a tool expert runs Python code,
// so only how it is routed (description and examples) can be changed here: its code stays in files.
import { reactive, ref, computed, onMounted } from 'vue';
import { send, getJson } from '../lib/api.js';
import { fmt } from '../lib/format.js';
import { cfg, modelGroups, refreshConfig, go } from '../store.js';

const props = defineProps({ name: String });   // an expert's name, or 'new'
const isNew = props.name === 'new';
const kind = ref('generate');
const isTool = computed(() => kind.value === 'tool');
const form = reactive({ name: '', description: '', examples: '', model: cfg.value.models[0] || '', system: '', shots: [], strip: '', license: 'user' });
const loading = ref(!isNew), error = ref(''), saving = ref(false), confirming = ref(false);
const groups = computed(() => modelGroups('', form.model));
const lines = text => text.split('\n').map(l => l.trim()).filter(Boolean);

onMounted(async () => {
  if (isNew) return;
  try {
    const m = await getJson(`/api/experts/${encodeURIComponent(props.name)}/manifest`);
    if (m.detail) throw new Error(m.detail);
    kind.value = m.kind;
    Object.assign(form, { name: m.name, description: m.description, examples: (m.examples || []).join('\n'), model: m.model || form.model, system: m.system || '',
      shots: (m.shots || []).map(s => ({ ...s })), strip: (m.strip || []).join('\n'), license: m.license || 'user' });
  } catch (e) { error.value = e.message; }
  loading.value = false;
});

const payload = () => ({
  name: isNew ? form.name.trim() : props.name, description: form.description, examples: lines(form.examples),
  ...(isTool.value ? {} : { model: form.model, system: form.system, license: form.license,
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
</script>

<template>
  <section class="view active" id="v-experts"><div class="panel" id="expertEditor">
    <button class="btn small back" id="e-back" @click="go('experts')">← Experts</button>
    <h2>{{ isNew ? 'New expert' : 'Edit ' + name }}</h2>
    <p v-if="loading" class="lede">Loading…</p>
    <template v-else>
      <p v-if="isNew" class="lede">A <b>generate</b> expert is a small model with a job: you describe it, give example messages so Noah can route to it, and write the prompt that makes the model do that one thing.</p>
      <p v-if="isTool" class="lede" id="e-toolnote">This expert runs Python code (<code>experts/{{ name }}/tool.py</code>), so its code can only be changed in that file. You can change how Noah routes messages to it below.</p>

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

        <template v-if="!isTool">
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

      <div v-if="!isTool" class="hfbox tryit">
        <h3>Try it <span class="meta">runs this draft on a message; nothing is saved</span></h3>
        <div class="hfrow"><input id="e-try-message" v-model="tryMessage" :placeholder="lines(form.examples)[0] || 'A message to send'" @keydown.enter.prevent="tryIt">
          <button class="btn small" id="e-try" :disabled="trying || !form.model || !form.system.trim()" @click="tryIt">{{ trying ? 'Running…' : 'Run' }}</button></div>
        <div v-if="tried" id="e-try-out" class="a" :class="{ err: !tried.ok }">
          <div v-if="tried.ok" class="body" v-html="fmt(tried.output)"></div><div v-else class="body">{{ tried.error }}</div>
          <div v-if="tried.ok" class="head"><span>{{ tried.ms }} ms</span></div>
        </div>
      </div>
    </template>
  </div></section>
</template>
