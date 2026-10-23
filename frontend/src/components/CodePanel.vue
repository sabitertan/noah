<script setup>
// The code behind a build, file by file, in a read-only Monaco editor (the app's theme). Monaco is a large chunk, so it is
// loaded the first time the panel opens. A version saved before its parts were kept is split from its page, as the server does.
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';

const props = defineProps({ b: Object });
const emit = defineEmits(['close']);
const host = ref(null), selected = ref('index.html'), note = ref('');
let monaco = null, editor = null;
const models = new Map();   // file name -> Monaco model, kept out of Vue's reactivity

function split(html) {   // mirror of builder.split(): the parts of a page assemble() wrote
  const un = s => s.replace(/<\\\/script/g, '</script').replace(/<\\\/style/g, '</style');
  const css = /<style>\n?([\s\S]*?)\n?<\/style>/.exec(html)?.[1] || '';
  let body = /<body>\n?([\s\S]*?)\n?<\/body>/.exec(html)?.[1] || '', js = '';
  const s = /<script>\n?([\s\S]*?)\n?<\/script>\s*$/.exec(body);
  if (s) { js = s[1]; body = body.slice(0, s.index).trimEnd(); }
  return { html: un(body), css: un(css), js: un(js) };
}
const parts = computed(() => props.b.parts || split(props.b.html || ''));
const changed = computed(() => new Set(props.b.change ? (props.b.steps || []).filter(s => !s.error).map(s => s.part) : []));
const files = computed(() => [
  { name: 'index.html', part: 'html', lang: 'html', text: parts.value.html || '', hint: 'the page markup' },
  { name: 'styles.css', part: 'css', lang: 'css', text: parts.value.css || '', hint: 'how it looks' },
  { name: 'app.js', part: 'js', lang: 'javascript', text: parts.value.js || '', hint: 'what it does' },
  { name: 'prototype.html', part: '', lang: 'html', text: props.b.html || '', hint: 'everything in one file, as it runs' },
].filter(f => f.text));
const file = computed(() => files.value.find(f => f.name === selected.value) || files.value[0]);

function show() {
  if (!editor || !file.value) return;
  const f = file.value;
  let m = models.get(f.name);
  if (m && m.getValue() !== f.text) { m.dispose(); m = null; }
  if (!m) { m = monaco.editor.createModel(f.text, f.lang); models.set(f.name, m); }
  editor.setModel(m);
}
watch([file, () => props.b], show);

async function copy() {
  try { await navigator.clipboard.writeText(file.value.text); note.value = 'Copied'; } catch (e) { note.value = 'Copy failed'; }
  setTimeout(() => { note.value = ''; }, 1500);
}
function download() {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([file.value.text], { type: file.value.lang === 'css' ? 'text/css' : file.value.lang === 'javascript' ? 'text/javascript' : 'text/html' }));
  a.download = file.value.name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

onMounted(async () => {
  ({ monaco } = await import('../lib/monaco.js'));
  if (!host.value) return;
  editor = monaco.editor.create(host.value, { readOnly: true, automaticLayout: true, fontFamily: "'Fira Code', ui-monospace, monospace",
    fontSize: 13, minimap: { enabled: false }, scrollBeyondLastLine: false, wordWrap: 'on', padding: { top: 8 },
    readOnlyMessage: { value: 'This is the code Noah wrote. Ask for changes in the box on the left.' } });
  show();
});
onBeforeUnmount(() => { editor?.dispose(); for (const m of models.values()) m.dispose(); models.clear(); });
</script>

<template>
  <aside class="codepanel" id="codePanel">
    <div class="cp-head">
      <span class="grow">Code</span>
      <span v-if="note" class="meta">{{ note }}</span>
      <button class="btn small" type="button" @click="copy" :disabled="!file">Copy</button>
      <button class="btn small" type="button" @click="download" :disabled="!file">Download</button>
      <button class="icon-btn" type="button" title="Close the code" @click="emit('close')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg></button>
    </div>
    <div class="cp-files" role="tablist">
      <button v-for="f in files" :key="f.name" class="cp-file" :class="{ on: file && f.name === file.name }" role="tab" :title="f.hint" @click="selected = f.name">
        {{ f.name }}<i v-if="changed.has(f.part)" class="cp-dot" title="changed in this version"></i></button>
    </div>
    <div ref="host" class="cp-editor"></div>
  </aside>
</template>
