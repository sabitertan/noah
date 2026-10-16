<script setup>
// Every file of an expert in a code editor (Monaco). Typing lints in the background; Save checks for real first (lint, then
// loading every expert with this draft in a separate process) and writes nothing if that finds an error. Each save is a
// version, and any version can be compared with the draft and switched to (which is checked the same way).
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import { codeFetch } from '../lib/api.js';
import { monaco, languageOf } from '../lib/monaco.js';
import { light, refreshConfig } from '../store.js';

const props = defineProps({ name: String });
const enc = encodeURIComponent(props.name);
const FILE = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/;

const host = ref(null), diffHost = ref(null);
let editor = null, diff = null, diffModels = [];   // Monaco objects stay out of Vue's reactivity
const models = new Map(), viewStates = new Map();
const saved = ref({}), base = ref(''), names = ref([]), selected = ref('');
const codeEditable = ref(false), codeNote = ref(''), editable = ref([]);
const rev = ref(0);   // bumped on every keystroke, so what depends on the draft recomputes
const loading = ref(true), checking = ref(false), saving = ref(false), error = ref(''), notice = ref('');
const report = ref(null), reportRev = ref(-1), note = ref('');
const versions = ref([]), comparing = ref(null), compareFile = ref('');
const newName = ref(''), confirmDelete = ref('');

const order = n => (n === 'expert.yaml' ? 0 : n.endsWith('.py') ? 1 : 2);
const sortNames = list => [...list].sort((a, b) => order(a) - order(b) || a.localeCompare(b));
const draft = () => Object.fromEntries(names.value.map(n => [n, models.get(n).getValue()]));
const readOnly = n => n.endsWith('.py') && !codeEditable.value;
const dirtySet = computed(() => {
  rev.value;
  const d = new Set(names.value.filter(n => models.get(n)?.getValue() !== saved.value[n]));
  if (Object.keys(saved.value).some(n => !names.value.includes(n))) d.add('');   // a file was removed
  return d;
});
const dirty = computed(() => dirtySet.value.size > 0);
const problems = computed(() => report.value?.problems || []);
const counts = computed(() => {
  const c = {};
  for (const p of problems.value) (c[p.file] ||= { error: 0, warning: 0 })[p.severity]++;
  return c;
});
const stale = computed(() => report.value && reportRev.value !== rev.value);

/* ---------- models ---------- */
function addModel(n, text) {
  const m = monaco.editor.createModel(text, languageOf(n), monaco.Uri.parse(`inmemory://experts/${enc}/${n}`));
  if (/\.ya?ml$/.test(n)) m.updateOptions({ tabSize: 2 });
  m.onDidChangeContent(() => { rev.value++; lintSoon(); });
  models.set(n, m);
}
function disposeModels() { for (const m of models.values()) m.dispose(); models.clear(); viewStates.clear(); }

function select(n) {
  if (!models.has(n) || !editor) return;
  if (selected.value && models.has(selected.value)) viewStates.set(selected.value, editor.saveViewState());
  selected.value = n;
  editor.setModel(models.get(n));
  editor.updateOptions({ readOnly: readOnly(n), readOnlyMessage: { value: codeNote.value } });
  if (viewStates.has(n)) editor.restoreViewState(viewStates.get(n));
  editor.focus();
}

async function load(keepSelection = true) {
  loading.value = true; error.value = '';
  try {
    const r = await codeFetch(`/api/experts/${enc}/files`);
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || 'could not load the files');
    const want = keepSelection ? selected.value : '';
    disposeModels();
    saved.value = j.files; base.value = j.hash; codeEditable.value = j.code_editable; codeNote.value = j.code_note; editable.value = j.editable;
    names.value = sortNames(Object.keys(j.files));
    for (const n of names.value) addModel(n, j.files[n]);
    selected.value = '';
    select(names.value.includes(want) ? want : names.value.find(n => n.endsWith('.py')) || names.value[0]);
    report.value = null; rev.value++;
    lint();
  } catch (e) { error.value = e.message; }
  loading.value = false;
  loadVersions();
}
const loadVersions = async () => { const r = await codeFetch(`/api/experts/${enc}/versions`); if (r.ok) versions.value = await r.json(); };

/* ---------- checking ---------- */
function showReport(rep, atRev = rev.value) {
  report.value = rep; reportRev.value = atRev;
  for (const [n, m] of models) {
    monaco.editor.setModelMarkers(m, 'noah', rep.problems.filter(p => p.file === n).map(p => {
      const line = Math.min(Math.max(p.line, 1), m.getLineCount());
      const col = p.col > 0 ? p.col : m.getLineFirstNonWhitespaceColumn(line) || 1;
      return { severity: p.severity === 'error' ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning, message: p.message,
        source: p.source, startLineNumber: line, startColumn: col, endLineNumber: line, endColumn: p.col > 0 ? col + 1 : m.getLineMaxColumn(line) };
    }));
  }
}
async function runCheck(load) {
  const at = rev.value;
  const r = await codeFetch(`/api/experts/${enc}/files/check?load=${load}`, 'POST', { files: draft() });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || 'the check failed');
  if (at === rev.value || load) showReport(j, at);   // a lint that typing has overtaken is dropped
  return j;
}
let lintTimer = 0;
const lintSoon = () => { clearTimeout(lintTimer); lintTimer = setTimeout(lint, 700); };
const lint = () => runCheck(false).catch(() => {});   // reads the files only, never runs them
async function check() {
  checking.value = true; error.value = ''; notice.value = '';
  try {
    const j = await runCheck(true);
    notice.value = j.ok ? `No errors${j.warnings ? `, ${j.warnings} warning${j.warnings > 1 ? 's' : ''}` : ''}. Every expert loads with this draft.` : '';
    if (!j.ok) error.value = `${j.errors} error${j.errors > 1 ? 's' : ''}: fix ${j.errors > 1 ? 'them' : 'it'} before saving.`;
  } catch (e) { error.value = e.message; }
  checking.value = false;
}

/* ---------- saving and versions ---------- */
async function afterWrite(r, okText) {
  const j = await r.json().catch(() => ({}));
  if (r.status === 422) { showReport(j.report); error.value = `Not saved: ${j.report.errors} error${j.report.errors > 1 ? 's' : ''}. Nothing was written.`; return false; }
  if (!r.ok) { error.value = j.detail || 'could not save'; return false; }
  notice.value = okText(j); return j;
}
async function save() {
  if (saving.value || !dirty.value) return;
  saving.value = true; error.value = ''; notice.value = '';
  try {
    const files = draft(), at = rev.value;
    const j = await afterWrite(await codeFetch(`/api/experts/${enc}/files`, 'PUT', { files, base: base.value, note: note.value }),
      j => `Saved as version ${j.version}${j.report.warnings ? ` (${j.report.warnings} warning${j.report.warnings > 1 ? 's' : ''})` : ''}.`);
    if (j) { saved.value = files; base.value = j.hash; note.value = ''; showReport(j.report, at); loadVersions(); refreshConfig(); }
  } catch (e) { error.value = e.message; }
  saving.value = false;
}
async function switchTo(v) {
  if (dirty.value && !confirm('Switching discards your unsaved changes. Continue?')) return;
  saving.value = true; error.value = ''; notice.value = '';
  try {
    const j = await afterWrite(await codeFetch(`/api/experts/${enc}/versions/${v.id}/restore`, 'POST', { base: base.value }),
      j => `Switched to version ${v.id} (saved as version ${j.version}).`);
    if (j) { closeCompare(); const msg = notice.value; await load(); notice.value = msg; refreshConfig(); }
  } catch (e) { error.value = e.message; }
  saving.value = false;
}
function discard() {
  const files = saved.value;
  for (const n of names.value) if (!(n in files)) { models.get(n).dispose(); models.delete(n); }
  for (const [n, text] of Object.entries(files)) models.has(n) ? models.get(n).getValue() !== text && models.get(n).setValue(text) : addModel(n, text);
  names.value = sortNames(Object.keys(files));
  select(names.value.includes(selected.value) ? selected.value : names.value[0]);
  rev.value++; error.value = ''; notice.value = ''; lint();
}

/* ---------- files ---------- */
const newNameError = computed(() => {
  const n = newName.value.trim();
  if (!n) return '';
  if (!FILE.test(n) || !editable.value.some(s => n.endsWith(s))) return `Use letters, digits, '_', '-', '.', ending in ${editable.value.join(' ')}`;
  if (names.value.includes(n)) return 'There is already a file with that name.';
  if (n.endsWith('.py') && !codeEditable.value) return 'Python files can only be added from the computer running Noah.';
  return '';
});
function addFile() {
  const n = newName.value.trim();
  if (!n || newNameError.value) return;
  addModel(n, n.endsWith('.py') ? 'from typing import Any\n\n\n' : '');
  names.value = sortNames([...names.value, n]); newName.value = ''; rev.value++;
  select(n);
}
function removeFile(n) {
  confirmDelete.value = '';
  models.get(n).dispose(); models.delete(n); viewStates.delete(n);
  names.value = names.value.filter(x => x !== n); rev.value++;
  if (selected.value === n) { selected.value = ''; select(names.value[0]); }
  lintSoon();
}
function reveal(p) {
  if (!models.has(p.file)) return;
  closeCompare(); select(p.file);
  const line = Math.max(p.line, 1);
  editor.revealLineInCenter(line); editor.setPosition({ lineNumber: line, column: Math.max(p.col, 1) }); editor.focus();
}

/* ---------- comparing a version with the draft ---------- */
async function compare(v) {
  const r = await codeFetch(`/api/experts/${enc}/versions/${v.id}`);
  if (!r.ok) { error.value = (await r.json().catch(() => ({}))).detail || 'could not load that version'; return; }
  comparing.value = { ...v, files: (await r.json()).files };
  const all = compareNames.value;
  compareFile.value = all.includes(selected.value) ? selected.value : changedIn(comparing.value)[0] || all[0];
  await nextTick(); showDiff();
}
const compareNames = computed(() => comparing.value ? sortNames([...new Set([...Object.keys(comparing.value.files), ...names.value])]) : []);
const changedIn = v => { rev.value; return compareNames.value.filter(n => v.files[n] !== models.get(n)?.getValue()); };
function showDiff() {
  if (!comparing.value || !diffHost.value) return;
  if (!diff) diff = monaco.editor.createDiffEditor(diffHost.value, { readOnly: true, originalEditable: false, automaticLayout: true, minimap: { enabled: false },
    fontFamily: "'Fira Code', ui-monospace, monospace", fontSize: 13, scrollBeyondLastLine: false, renderSideBySide: diffHost.value.clientWidth > 700 });
  const n = compareFile.value, lang = languageOf(n);
  const next = [monaco.editor.createModel(comparing.value.files[n] ?? '', lang), monaco.editor.createModel(models.get(n)?.getValue() ?? '', lang)];
  diff.setModel({ original: next[0], modified: next[1] });
  for (const m of diffModels) m.dispose();
  diffModels = next;
}
function closeCompare() {
  if (diff) { diff.dispose(); diff = null; }
  for (const m of diffModels) m.dispose();
  diffModels = []; comparing.value = null;
}
watch(compareFile, () => showDiff());

/* ---------- lifecycle ---------- */
const theme = () => (light.value ? 'noah-light' : 'noah-dark');
watch(light, () => monaco.editor.setTheme(theme()));
const guard = e => { if (dirty.value) { e.preventDefault(); e.returnValue = ''; } };
onMounted(async () => {
  editor = monaco.editor.create(host.value, { automaticLayout: true, theme: theme(), fontFamily: "'Fira Code', ui-monospace, monospace", fontSize: 13,
    minimap: { enabled: false }, scrollBeyondLastLine: false, tabSize: 4, insertSpaces: true, fixedOverflowWidgets: true, model: null });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, save);
  window.addEventListener('beforeunload', guard);
  await load(false);
});
onBeforeUnmount(() => {
  clearTimeout(lintTimer); window.removeEventListener('beforeunload', guard);
  closeCompare(); editor?.dispose(); disposeModels();
});
defineExpose({ dirty });

const ago = t => {
  const s = Math.round(Date.now() / 1000 - t);
  return s < 60 ? 'just now' : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(t * 1000).toLocaleString();
};
</script>

<template>
  <div class="xfiles" id="expertFiles">
    <p v-if="!loading && !codeEditable" class="fnote warn" id="x-code-note">{{ codeNote }}</p>
    <div class="xgrid">
      <aside class="xlist">
        <div class="xhead">Files</div>
        <div v-for="n in names" :key="n" class="xfile" :class="{ active: n === selected && !comparing }" :data-file="n" @click="closeCompare(); select(n)">
          <span class="grow">{{ n }}</span>
          <span v-if="dirtySet.has(n)" class="xdot" title="Unsaved changes">●</span>
          <span v-if="counts[n]?.error" class="xcount err" :title="counts[n].error + ' errors'">{{ counts[n].error }}</span>
          <span v-else-if="counts[n]?.warning" class="xcount warn" :title="counts[n].warning + ' warnings'">{{ counts[n].warning }}</span>
          <span v-if="readOnly(n)" class="xlock" title="Read-only here">ro</span>
          <template v-if="n !== 'expert.yaml' && !readOnly(n)">
            <button v-if="confirmDelete !== n" class="xdel" title="Remove this file" @click.stop="confirmDelete = n">×</button>
            <button v-else class="xdel yes" title="Click again to remove" @click.stop="removeFile(n)">remove?</button>
          </template>
        </div>
        <div class="xnew">
          <input id="x-newfile" v-model="newName" placeholder="notes.md" autocomplete="off" @keydown.enter.prevent="addFile">
          <button class="btn small" id="x-addfile" :disabled="!newName.trim() || !!newNameError" @click="addFile">Add</button>
        </div>
        <div v-if="newNameError" class="fnote bad">{{ newNameError }}</div>
      </aside>

      <div class="xmain">
        <div v-show="!comparing" ref="host" class="xeditor" id="x-editor"></div>
        <div v-if="comparing" class="xcompare">
          <div class="xcomparebar">
            <span>Version {{ comparing.id }} <span class="meta">(left)</span> → your draft <span class="meta">(right)</span></span>
            <select id="x-compare-file" v-model="compareFile"><option v-for="n in compareNames" :key="n" :value="n">{{ n }}{{ changedIn(comparing).includes(n) ? ' • changed' : '' }}</option></select>
            <span class="grow"></span>
            <button class="btn small" @click="switchTo(comparing)" :disabled="saving || comparing.current">Switch to version {{ comparing.id }}</button>
            <button class="btn small" id="x-compare-close" @click="closeCompare">Close</button>
          </div>
          <div ref="diffHost" class="xeditor" id="x-diff"></div>
        </div>

        <div class="actions">
          <button class="btn" id="x-check" :disabled="checking || loading" @click="check">{{ checking ? 'Checking…' : 'Check' }}</button>
          <input id="x-note" v-model="note" class="grow" maxlength="200" placeholder="What changed (optional, shown in versions)" @keydown.enter.prevent="save">
          <button class="btn primary" id="x-save" :disabled="saving || !dirty" :title="'Ctrl/⌘+S'" @click="save">{{ saving ? 'Checking and saving…' : 'Save' }}</button>
          <button class="btn" id="x-discard" :disabled="!dirty" @click="discard">Discard changes</button>
        </div>
        <div v-if="error" class="fnote bad" id="x-error">{{ error }}</div>
        <div v-else-if="notice" class="fnote ok" id="x-notice">{{ notice }}</div>

        <div class="xproblems" id="x-problems">
          <div class="xhead">Problems
            <span class="meta" v-if="report">{{ report.errors }} error{{ report.errors === 1 ? '' : 's' }}, {{ report.warnings }} warning{{ report.warnings === 1 ? '' : 's' }}
              · {{ report.loaded ? 'lint + load' : 'lint only (Check also loads the experts)' }}{{ stale ? ' · edited since' : '' }}</span></div>
          <div v-if="report && !problems.length" class="fnote">None.</div>
          <div v-for="(p, i) in problems" :key="i" class="xproblem" :class="p.severity" @click="reveal(p)">
            <span class="sev">{{ p.severity === 'error' ? '✕' : '!' }}</span>
            <span class="loc">{{ p.file }}{{ p.line ? ':' + p.line : '' }}</span>
            <span class="msg">{{ p.message }}</span><span class="meta">{{ p.source }}</span>
          </div>
        </div>
      </div>

      <aside class="xversions" id="x-versions">
        <div class="xhead">Versions</div>
        <p v-if="!versions.length" class="fnote">None yet. Each save keeps one, starting with how the files were before your first edit.</p>
        <div v-for="v in versions" :key="v.id" class="xversion" :class="{ current: v.current, open: comparing?.id === v.id }" :data-version="v.id">
          <div><b>v{{ v.id }}</b> <span class="meta">{{ ago(v.at) }}</span> <span v-if="v.current" class="tag">current</span></div>
          <div class="note">{{ v.note || '—' }}</div>
          <div class="xvact">
            <button class="btn small" @click="compare(v)">Compare</button>
            <button class="btn small" :disabled="v.current || saving" @click="switchTo(v)">Switch to</button>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>
