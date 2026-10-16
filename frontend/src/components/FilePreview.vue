<script setup>
import { computed } from 'vue';
import { fmt } from '../lib/format.js';
const props = defineProps({ pv: Object, local: Boolean });
const head = computed(() => (props.pv.rows || [])[0] || []);
const rows = computed(() => (props.pv.rows || []).slice(1));
</script>

<template>
  <div v-if="pv.error" class="fnote">{{ pv.error }} You can download the file{{ local ? ' or open it with another app' : '' }}.</div>
  <template v-else>
    <a v-if="pv.kind === 'image'" class="fimage" :href="pv.url" target="_blank" rel="noopener"><img :src="pv.url" alt="Image preview" loading="lazy"></a>
    <div v-else-if="pv.kind === 'csv'" class="fscroll"><table class="ftable"><tr><th v-for="(c, i) in head" :key="i">{{ c }}</th></tr><tr v-for="(r, i) in rows" :key="i"><td v-for="(c, j) in r" :key="j">{{ c }}</td></tr></table></div>
    <div v-else-if="pv.kind === 'markdown'" class="fbody md" v-code v-html="fmt(pv.text)"></div>
    <pre v-else class="fbody">{{ pv.text }}</pre>
    <div v-if="pv.note" class="fnote">{{ pv.note }}</div>
  </template>
</template>
