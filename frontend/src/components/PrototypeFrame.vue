<script setup>
// A built prototype, running. The sandboxed frame has no same-origin access, so the page can never reach Noah's API, cookies
// or storage. That sandbox also makes localStorage throw, so the page gets a stand-in whose data is kept with its build (b.storage).
import { ref, watch, onMounted, onBeforeUnmount } from 'vue';

const props = defineProps({ b: Object, fill: Boolean });  // fill: take the whole area (Builder page) instead of a card-sized frame
const frame = ref(null), srcdoc = ref(''), run = ref(0), full = ref(false), crash = ref('');

const shim = data => `<script>(() => {
  const make = (d, save) => ({ getItem: k => Object.hasOwn(d, k) ? d[k] : null, setItem: (k, v) => { d[k] = String(v); save(d); },
    removeItem: k => { delete d[k]; save(d); }, clear: () => { for (const k in d) delete d[k]; save(d); },
    key: i => Object.keys(d)[i] ?? null, get length() { return Object.keys(d).length; } });
  const tell = m => parent.postMessage({ noahPrototype: 1, ...m }, '*');
  for (const [name, store] of [['localStorage', make(${JSON.stringify(data).replace(/</g, '\\u003c')}, d => tell({ storage: d }))],
                               ['sessionStorage', make({}, () => {})]])
    try { Object.defineProperty(window, name, { value: store, configurable: true }); } catch (e) {}
  addEventListener('error', e => tell({ error: String(e.message || e) }));
  addEventListener('unhandledrejection', e => tell({ error: String(e.reason) }));
})();<\/script>`;
const withShim = (html, data) => /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, h => h + shim(data)) : shim(data) + html;

// Only another build reloads the frame. The prototype's own saves change b.storage too, and must not restart it.
watch(() => props.b.html, h => { srcdoc.value = h ? withShim(h, { ...(props.b.storage || {}) }) : ''; crash.value = ''; }, { immediate: true });
function restart() {  // start over with empty data
  props.b.storage = {};
  srcdoc.value = withShim(props.b.html, {}); crash.value = ''; run.value++;
}

function onMessage(e) {
  if (!frame.value || e.source !== frame.value.contentWindow || !e.data?.noahPrototype) return;
  if (e.data.storage) props.b.storage = { ...e.data.storage };
  if (e.data.error) crash.value = e.data.error;
}
onMounted(() => addEventListener('message', onMessage));
onBeforeUnmount(() => removeEventListener('message', onMessage));
</script>

<template>
  <div class="pframe" :class="{ fill, full }">
    <div class="bbar"><span class="grow"></span>
      <button class="btn small" type="button" @click="restart" title="Reload the prototype with empty data">Start over</button>
      <button v-if="!fill" class="btn small" type="button" @click="full = !full">{{ full ? 'Close' : 'Full screen' }}</button></div>
    <iframe ref="frame" :key="run" class="bframe" :srcdoc="srcdoc" :title="b.title" sandbox="allow-scripts allow-forms allow-modals"></iframe>
    <div v-if="crash" class="fnote warn">The prototype hit an error: {{ crash }}</div>
  </div>
</template>
