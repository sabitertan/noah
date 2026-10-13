<script setup>
import AppSidebar from './components/AppSidebar.vue';
import ChatView from './components/ChatView.vue';
import OrchestratorsView from './components/OrchestratorsView.vue';
import ExpertsView from './components/ExpertsView.vue';
import ModelsView from './components/ModelsView.vue';
import TracesView from './components/TracesView.vue';
import { view, title, ready, collapsed } from './store.js';
</script>

<template>
  <AppSidebar />
  <main>
    <div class="topbar"><button class="icon-btn hamb" id="hamb2" title="Open sidebar" @click="collapsed = !collapsed"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg></button><span id="title">{{ title }}</span></div>
    <template v-if="ready">
      <ChatView />  <!-- always mounted: keeps the half-typed message when you visit another page -->
      <OrchestratorsView v-if="view === 'orch'" />
      <ExpertsView v-if="view === 'experts'" />
      <ModelsView v-if="view === 'models'" />
      <TracesView v-if="view === 'traces'" />
    </template>
  </main>
</template>
