<script setup>
import UiIcon from './UiIcon.vue';
import { countLabel } from '../lib/format.js';
import { cfg, chats, currentChat, view, groups, light, collapsed, profiles, enabledProfiles, enabledExpertCount, go, openChat, newChat, deleteChat } from '../store.js';
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

      <div class="item" id="themeBtn" @click="light = !light"><UiIcon name="moon" /><span class="grow">Theme</span></div>
    </div>
  </nav>
</template>
