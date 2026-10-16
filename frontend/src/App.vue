<script setup>
import AppSidebar from './components/AppSidebar.vue';
import ChatView from './components/ChatView.vue';
import OrchestratorsView from './components/OrchestratorsView.vue';
import ExpertsView from './components/ExpertsView.vue';
import ModelsView from './components/ModelsView.vue';
import TracesView from './components/TracesView.vue';
import ThemesView from './components/ThemesView.vue';
import { view, title, ready, collapsed } from './store.js';
</script>

<template>
  <AppSidebar />
  <main>
    <div class="topbar"><button class="icon-btn hamb" id="hamb2" title="Open sidebar" @click="collapsed = !collapsed"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg></button><span id="title">{{ title }}</span>
      <a class="icon-btn repo" id="repoLink" href="https://github.com/sabitertan/noah" target="_blank" rel="noopener" title="Noah on GitHub" aria-label="Noah on GitHub"><svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg></a></div>
    <template v-if="ready">
      <ChatView />  <!-- always mounted: keeps the half-typed message when you visit another page -->
      <OrchestratorsView v-if="view === 'orch'" />
      <ExpertsView v-if="view === 'experts'" />
      <ModelsView v-if="view === 'models'" />
      <TracesView v-if="view === 'traces'" />
      <ThemesView v-if="view === 'themes'" />
    </template>
  </main>
</template>
