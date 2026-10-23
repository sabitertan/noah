<script setup>
// The Builder page: the app being built takes the whole page; the conversation floats over it in a box you can shrink.
// Each prompt builds a new version of the project (noah/builder.py); pick any finished version to see it again.
import { ref, computed, watch, nextTick } from 'vue';
import UiIcon from './UiIcon.vue';
import BuildSteps from './BuildSteps.vue';
import PrototypeFrame from './PrototypeFrame.vue';
import { projectObj, builders, builder, buildProfile, busy, sendBuild, newProject, go } from '../store.js';

const text = ref(''), box = ref(null), log = ref(null), min = ref(false);
const IDEAS = ['a habit tracker with daily streaks', 'a budget tracker with categories and a monthly total', 'a recipe book with search and favourites',
  'a pomodoro timer with a task list', 'a flashcard app to learn Spanish words'];
const msgs = computed(() => projectObj.value?.messages || []);
const shown = computed(() => { const m = msgs.value[projectObj.value?.shown ?? -1]; return m?.build?.html ? m : null; });
const building = computed(() => msgs.value.some(m => m.pending || (m.build && !m.build.html && !m.error)));

const autosize = () => { const t = box.value; if (!t) return; t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 160) + 'px'; };
async function send() {
  const t = text.value;
  if (!t.trim() || busy.value || !builder.value) return;
  text.value = ''; nextTick(autosize);
  await sendBuild(t);
  box.value?.focus();
}
const onEnter = e => { if (e.isComposing) return; e.preventDefault(); send(); };
const idea = t => { text.value = t; min.value = false; send(); };
const show = i => { if (msgs.value[i]?.build?.html) projectObj.value.shown = i; };

watch(() => [msgs.value.length, msgs.value.at(-1)?.build?.steps.filter(s => s.state !== 'waiting').length, msgs.value.at(-1)?.build?.html], () => {
  if (log.value) log.value.scrollTop = log.value.scrollHeight;
}, { flush: 'post' });
</script>

<template>
  <section class="view active" id="v-build">
    <div class="stage">
      <PrototypeFrame v-if="shown" :key="projectObj.id + ':' + projectObj.shown" :b="shown.build" fill />
      <div v-else class="stage-empty">
        <template v-if="building"><h1>Building…</h1><p>Your app appears here when it is ready. Progress is in the box on the left.</p></template>
        <template v-else>
          <h1>What do you want to build?</h1>
          <p>Describe an app and Noah builds a working prototype right here. No setup, nothing to install.</p>
          <div class="chips"><button v-for="t in IDEAS" :key="t" class="chip" :disabled="!builder" @click="idea(t)">{{ t }}</button></div>
        </template>
      </div>
    </div>

    <button v-if="min" class="floatbox-pill" type="button" @click="min = false" title="Show the conversation"><UiIcon name="chat" />
      <span>{{ building ? 'Building…' : msgs.length ? `${msgs.length} prompt${msgs.length === 1 ? '' : 's'}` : 'Describe your app' }}</span></button>
    <div v-else class="floatbox" id="buildBox">
      <div class="fb-head">
        <span class="grow">{{ projectObj?.title || 'New app' }}</span>
        <button class="icon-btn" type="button" title="Start a new app" @click="newProject"><UiIcon name="plus" :size="14" /></button>
        <button class="icon-btn" type="button" title="Shrink so the app has the whole page" @click="min = true"><UiIcon name="x" :size="14" /></button>
      </div>
      <div class="fb-log" ref="log">
        <p v-if="!msgs.length" class="fnote">Say what the app is for and what it should do. Each new message builds a new version.</p>
        <div v-for="(m, i) in msgs" :key="i" class="fb-msg" :class="{ sel: projectObj.shown === i }">
          <div class="u"><span>{{ m.text }}</span></div>
          <div v-if="m.pending && !m.build" class="typing">planning…</div>
          <BuildSteps v-if="m.build" :b="m.build" :error="m.error" />
          <div v-else-if="m.error" class="fnote bad">{{ m.error }}</div>
          <button v-if="m.build?.html && projectObj.shown !== i" class="btn small" type="button" @click="show(i)">Show this version</button>
        </div>
      </div>
      <form class="fb-composer" @submit.prevent="send">
        <textarea ref="box" rows="1" v-model="text" @input="autosize" @keydown.enter.exact="onEnter" autocomplete="off"
          :placeholder="msgs.length ? 'Describe the next version…' : 'Build me an app for…'"></textarea>
        <div class="crow">
          <span v-if="builders.length > 1" class="pill" title="Build orchestrator"><UiIcon name="cpu" :size="13" />
            <select :value="builder && builder.name" @change="buildProfile = $event.target.value"><option v-for="p in builders" :key="p.name" :value="p.name">{{ p.name }}</option></select></span>
          <span v-else-if="!builder" class="fnote bad">No build orchestrator is enabled. <a href="#" @click.prevent="go('orch')">Enable one</a>.</span>
          <span class="sp"></span>
          <button class="send" :disabled="busy || !builder" title="Build"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg></button>
        </div>
      </form>
    </div>
  </section>
</template>
