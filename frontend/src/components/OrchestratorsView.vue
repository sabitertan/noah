<script setup>
// The cards and the editor both read `profiles` and `draft`, so they cannot disagree:
// selecting a card copies it into `draft`; the form edits the draft; the selected card shows it live; Save writes it back.
import { ref, computed } from 'vue';
import UiIcon from './UiIcon.vue';
import { send } from '../lib/api.js';
import { shortModel, hostedKind, PROVIDERS } from '../lib/format.js';
import { MODEL_ENGINES, cfg, profiles, enabledProfiles, current, activeProfile, isOn, saveProfiles, go } from '../store.js';

const FIELDS = ['name', 'engine', 'engine_model', 'mode', 'extractor', 'model', 'threshold'];  // what the editor changes ('enabled' belongs to the switch)
const pick = p => Object.fromEntries(FIELDS.map(f => [f, p[f] ?? (f === 'threshold' ? 0.25 : '')]));

const editing = ref(Math.max(0, profiles.value.findIndex(p => p.name === activeProfile.value)));
const draft = ref(null), note = ref('');
const saved = computed(() => profiles.value[editing.value]);
const loadDraft = () => { draft.value = saved.value ? pick(saved.value) : null; note.value = ''; };
loadDraft();

const dirty = computed(() => !!(saved.value && draft.value && FIELDS.some(f => draft.value[f] !== pick(saved.value)[f])));
const takesModel = computed(() => !!draft.value && MODEL_ENGINES.includes(draft.value.engine));
const editingOn = computed(() => !!saved.value && isOn(saved.value));
const canDelete = computed(() => profiles.value.length > 1 && !(editingOn.value && enabledProfiles.value.length < 2));
const message = computed(() => note.value || (saved.value && !isOn(saved.value) ? 'This orchestrator is disabled: it is hidden from the chat picker and the server refuses to use it.' : '')
  || (missingKeys.value.length ? `This orchestrator uses a hosted model. Add your ${missingKeys.value.map(k => PROVIDERS[k].label + ' ' + PROVIDERS[k].secret).join(' and ')} on the Models page first.` : ''));

// Model choices, grouped by where they run. A saved value that is in no list stays visible instead of being swapped silently.
function modelGroups(engine, cur) {
  const own = (cfg.value.engine_models || {})[engine];
  const groups = own ? [{ label: 'Models', models: own }]
    : [{ label: 'Local (Ollama)', models: cfg.value.models },
       ...Object.entries(PROVIDERS).map(([k, p]) => ({ label: `${p.label} (hosted, no download)`, models: cfg.value[k + '_models'] || [] }))];
  if (cur && !groups.some(g => g.models.includes(cur))) groups.unshift({ label: 'Current', models: [cur] });
  return groups.filter(g => g.models.length);
}
const engineModelList = engine => modelGroups(engine, '').flatMap(g => g.models);
// hosted providers this draft uses that have no key yet
const missingKeys = computed(() => !draft.value ? [] : [...new Set([draft.value.engine_model, draft.value.model].map(hostedKind).filter(k => k && !cfg.value[k]?.configured))]);
const select = i => { editing.value = i; loadDraft(); };
const shown = (x, i) => i === editing.value && draft.value ? draft.value : x;
const summary = p => `${p.engine}${MODEL_ENGINES.includes(p.engine) ? ' (' + shortModel(p.engine_model) + ')' : ''} → ${p.mode} → ` +
  (p.engine === 'llm_tools' ? 'model args' : p.extractor + (p.extractor === 'openai_compat' ? ' (' + shortModel(p.model) + ')' : ''));

function onEngine() {  // switching engine: keep the model only if this engine offers it
  const list = engineModelList(draft.value.engine);
  if (list.length && !list.includes(draft.value.engine_model)) draft.value.engine_model = list[0];
}
async function commit() {  // draft -> stored profile; false if the name clashes
  const p = saved.value, old = p.name, name = draft.value.name.trim() || old;
  if (profiles.value.some((x, i) => i !== editing.value && x.name === name)) { note.value = `There is already an orchestrator named "${name}".`; return false; }
  Object.assign(p, draft.value, { name });
  if (activeProfile.value === old) activeProfile.value = name;
  await saveProfiles();
  loadDraft();
  return true;
}
async function useInChat() { if (await commit()) { activeProfile.value = saved.value.name; go('chat'); } }
async function newProfile() {
  let n = profiles.value.length + 1; while (profiles.value.some(p => p.name === 'Orchestrator ' + n)) n++;
  profiles.value.push({ ...saved.value, name: 'Orchestrator ' + n, enabled: true });
  editing.value = profiles.value.length - 1; loadDraft();
  await saveProfiles();
}
async function deleteProfile() {
  profiles.value.splice(editing.value, 1); editing.value = 0; loadDraft();
  await saveProfiles();
}
async function toggle(x, ev) {
  const on = ev.target.checked;
  const r = await send('/api/profiles/' + encodeURIComponent(x.name), 'PUT', { enabled: on });
  if (r.ok) { x.enabled = on; note.value = ''; }
  else { ev.target.checked = isOn(x); note.value = (await r.json().catch(() => ({}))).detail || 'could not change the orchestrator'; }  // revert the switch, then say why
}
</script>

<template>
  <section class="view active" id="v-orch"><div class="panel" id="orchPanel">
    <h2>Orchestrators</h2><p class="lede">An orchestrator is a saved combination of decision engine, orchestration mode and argument extractor. Create as many as you like.</p>
    <div class="cards">
      <div v-for="(x, i) in profiles" :key="i" class="card" :class="{ sel: i === editing, off: !isOn(x) }" :data-edit="i" @click="select(i)">
        <h3><UiIcon name="cpu" /><span class="grow">{{ shown(x, i).name }}</span>
          <span v-if="i === editing && dirty" class="meta">unsaved</span>
          <span v-if="current && x.name === current.name" class="badge">active</span>
          <label class="sw" :title="isOn(x) ? 'Enabled' : 'Disabled'" @click.stop><input type="checkbox" role="switch" :data-toggle-profile="x.name" :checked="isOn(x)" @change="toggle(x, $event)"><i></i></label></h3>
        <p>{{ summary(shown(x, i)) }}</p>
      </div>
      <div class="card new" data-new @click="newProfile"><UiIcon name="plus" />New orchestrator</div>
    </div>
    <div class="form" v-if="draft">
      <label>Name</label><input id="f-name" v-model="draft.name">
      <label>Decision engine</label><select id="f-engine" v-model="draft.engine" @change="onEngine"><option v-for="e in cfg.engines" :key="e">{{ e }}</option></select>
      <label v-show="takesModel">Orchestrator model</label><select id="f-em" v-show="takesModel" v-model="draft.engine_model"><optgroup v-for="g in modelGroups(draft.engine, draft.engine_model)" :key="g.label" :label="g.label"><option v-for="m in g.models" :key="m">{{ m }}</option></optgroup></select>
      <label>Orchestration</label><select id="f-mode" v-model="draft.mode"><option v-for="m in cfg.modes" :key="m">{{ m }}</option></select>
      <label>Argument extractor</label><select id="f-extractor" v-model="draft.extractor"><option v-for="e in cfg.extractors" :key="e">{{ e }}</option></select>
      <label>Tiny model</label><select id="f-model" v-model="draft.model" :disabled="draft.extractor !== 'openai_compat'"><option value="">—</option><optgroup v-for="g in modelGroups('openai_compat', draft.model)" :key="g.label" :label="g.label"><option v-for="m in g.models" :key="m">{{ m }}</option></optgroup></select>
      <label>Threshold <span id="f-thv">{{ draft.threshold }}</span></label><input type="range" id="f-threshold" min="0" max="1" step="0.05" v-model.number="draft.threshold">
    </div>
    <div class="actions" v-if="draft">
      <button class="btn primary" id="f-save" @click="commit">Save</button>
      <button class="btn" id="f-use" :disabled="!editingOn" :title="editingOn ? '' : 'Enable this orchestrator first'" @click="useInChat">Use in chat</button>
      <button class="btn danger" id="f-del" :disabled="!canDelete" @click="deleteProfile">Delete</button>
    </div>
    <div class="fnote" id="orchMsg">{{ message }}</div>
  </div></section>
</template>
