<script setup>
import { ref, onMounted } from 'vue';
import { getJson } from '../lib/api.js';

const traces = ref([]);
onMounted(async () => { traces.value = await getJson('/api/traces?limit=100'); });
</script>

<template>
  <section class="view active" id="v-traces"><div class="panel" id="tracesPanel">
    <h2>Traces</h2><p class="lede">Every routing decision is logged. This is the raw material for fine-tuning your own orchestrators and experts.</p>
    <table v-if="traces.length">
      <thead><tr><th>When</th><th>Message</th><th>Engine</th><th>Chosen</th><th>ms</th></tr></thead>
      <tbody><tr v-for="(r, i) in traces" :key="i"><td>{{ new Date(r.ts * 1000).toLocaleTimeString() }}</td><td>{{ r.message }}</td><td>{{ r.engine }}</td><td>{{ r.results.map(x => x.expert).join(', ') }}</td><td>{{ r.total_ms.toFixed(0) }}</td></tr></tbody>
    </table>
    <p v-else class="lede">Nothing yet.</p>
  </div></section>
</template>
