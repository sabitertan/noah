<script setup>
import UiIcon from './UiIcon.vue';
import { countLabel } from '../lib/format.js';
import { THEMES } from '../lib/themes.js';
import { cfg, chats, currentChat, view, groups, theme, themeInfo, collapsed, profiles, enabledProfiles, enabledExpertCount, go, openChat, newChat, deleteChat } from '../store.js';

const themeGroups = [{ label: 'Dark', themes: THEMES.filter(t => t.dark) }, { label: 'Light', themes: THEMES.filter(t => !t.dark) }];
</script>

<template>
  <nav class="sidebar" id="sidebar" aria-label="Sidebar">
    <div class="sb-head">
      <button class="icon-btn" id="hamb" title="Toggle sidebar" @click="collapsed = !collapsed"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg></button>
      <span class="brand">Noah</span>
    </div>
    <div class="sb-inner" id="sbInner">
      <div class="item" id="newChat" @click="newChat"><UiIcon name="plus" /><span class="grow">New Chat</span></div>

      <div class="item" :class="{ open: groups.chats }" data-group="chats" @click="groups.chats = !groups.chats"><UiIcon name="chat" /><span class="grow">Chats</span><UiIcon name="chev" :size="12" class="chev" /></div>
      <div class="group-body" :class="{ closed: !groups.chats }">
        <div v-for="c in chats" :key="c.id" class="item chat-item" :class="{ active: c.id === currentChat && view === 'chat' }" :data-chat="c.id" @click="openChat(c.id)">
          <span class="grow">{{ c.title }}</span>
          <button class="icon-btn del" title="Delete" @click.stop="deleteChat(c.id)"><UiIcon name="x" :size="12" /></button>
        </div>
        <div v-if="!chats.length" class="item"><span class="meta">No chats yet</span></div>
      </div>

      <div class="item" :class="{ open: groups.setup }" data-group="setup" @click="groups.setup = !groups.setup"><UiIcon name="tool" /><span class="grow">Setup</span><UiIcon name="chev" :size="12" class="chev" /></div>
      <div class="group-body" :class="{ closed: !groups.setup }">
        <div class="item" :class="{ active: view === 'orch' }" data-view="orch" @click="go('orch')"><UiIcon name="cpu" /><span class="grow">Orchestrators</span><span class="meta">{{ countLabel(enabledProfiles.length, profiles.length) }}</span></div>
        <div class="item" :class="{ active: view === 'experts' }" data-view="experts" @click="go('experts')"><UiIcon name="box" /><span class="grow">Experts</span><span class="meta">{{ countLabel(enabledExpertCount, cfg.experts.length) }}</span></div>
        <div class="item" :class="{ active: view === 'models' }" data-view="models" @click="go('models')"><UiIcon name="list" /><span class="grow">Models</span><span class="dot" :class="{ on: cfg.models.length }"></span></div>
        <div class="item" :class="{ active: view === 'traces' }" data-view="traces" @click="go('traces')"><UiIcon name="list" /><span class="grow">Traces</span></div>
      </div>

      <div class="item" id="themeBtn" :class="{ open: groups.theme }" @click="groups.theme = !groups.theme"><UiIcon name="moon" /><span class="grow">Theme</span><span class="meta">{{ themeInfo.name }}</span><UiIcon name="chev" :size="12" class="chev" /></div>
      <div class="group-body" :class="{ closed: !groups.theme }">
        <template v-for="g in themeGroups" :key="g.label">
          <div class="theme-label">{{ g.label }}</div>
          <div v-for="t in g.themes" :key="t.id" class="item" :class="{ active: theme === t.id }" :data-theme="t.id" :title="t.source" @click="theme = t.id">
            <span class="swatch" :style="{ background: `linear-gradient(135deg, ${t.vars.panel} 50%, ${t.vars.accent} 50%)`, borderColor: t.vars.border }"></span><span class="grow">{{ t.name }}</span>
          </div>
        </template>
      </div>
    </div>
  </nav>
</template>
