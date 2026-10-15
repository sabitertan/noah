<script setup>
import { ref, computed } from 'vue';
import FilePreview from './FilePreview.vue';
import { humanSize } from '../lib/format.js';
import { send } from '../lib/api.js';

const props = defineProps({ file: Object, local: Boolean });
const fileIcon = { markdown: 'M↓', text: 'TXT', csv: 'CSV', pdf: 'PDF', image: 'IMG', binary: '···' };
const preview = ref(props.file.preview || null), loading = ref(false), err = ref(''), msg = ref('');
const icon = computed(() => fileIcon[props.file.kind] || '···');
const href = computed(() => '/api/files/download?path=' + encodeURIComponent(props.file.path));

async function toggle() {
  if (preview.value || err.value || loading.value) { preview.value = null; err.value = ''; loading.value = false; return; }  // second click closes
  loading.value = true;
  try {
    const r = await fetch('/api/files/preview?path=' + encodeURIComponent(props.file.path));
    if (!r.ok) throw new Error((await r.json()).detail || 'could not open the file');
    preview.value = (await r.json()).preview;
  } catch (e) { err.value = e.message; }
  loading.value = false;
}
async function openApp() {
  msg.value = '';
  try {
    const r = await send('/api/files/open', 'POST', { path: props.file.path });
    const j = await r.json();
    msg.value = r.ok ? 'Opened in your default app.' : (j.detail || 'could not open the file');
  } catch (e) { msg.value = e.message; }
}
</script>

<template>
  <div class="file" :data-path="file.path">
    <div class="fhead"><span class="ficon">{{ icon }}</span><span class="fname" :title="file.path">{{ file.path }}</span><span class="meta">{{ humanSize(file.size) }}</span>
      <span class="factions">
        <button v-if="file.previewable" class="btn f-open" type="button" @click="toggle">Open</button>
        <a class="btn" :href="href" download>Download</a>
        <button v-if="file.openable && local" class="btn f-app" type="button" @click="openApp">Open with app</button>
      </span></div>
    <div class="fpreview">
      <div v-if="loading" class="fnote">Opening…</div>
      <div v-else-if="err" class="fnote">{{ err }}</div>
      <FilePreview v-else-if="preview" :pv="preview" :local="local" />
    </div>
    <div class="fmsg">{{ msg }}</div>
  </div>
</template>
