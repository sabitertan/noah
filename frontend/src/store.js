// The one place UI state lives. Components read these refs and call these actions, so two views can never disagree.
import { ref, reactive, computed, watch } from 'vue';
import { send, getJson, store } from './lib/api.js';

export const MODEL_ENGINES = ['llm_tools', 'llm_choice', 'laya'];  // engines that take an engine_model
export const TITLES = { orch: 'Orchestrators', experts: 'Experts', models: 'Models', traces: 'Traces' };
export const VIEWS = ['chat', ...Object.keys(TITLES)];
export const isOn = p => p.enabled !== false;  // profiles saved before the switch existed count as enabled

/* ---------- state ---------- */
export const cfg = ref({ engines: [], modes: [], extractors: [], models: [], experts: [], local: false });
export const profiles = ref([]);
export const chats = ref(store.get('chats', []));
export const currentChat = ref(store.get('current', null));
export const activeProfile = ref(store.get('profile', 'Default'));
export const groups = reactive(store.get('groups', { chats: true, setup: true }));
export const light = ref(store.get('light', false));
export const collapsed = ref(false);
export const busy = ref(false);
export const ready = ref(false);  // config and profiles loaded; views mount after this

/* ---------- derived ---------- */
export const enabledProfiles = computed(() => profiles.value.filter(isOn));
export const current = computed(() => enabledProfiles.value.find(p => p.name === activeProfile.value) || enabledProfiles.value[0]);  // never a disabled one
export const chatObj = computed(() => chats.value.find(c => c.id === currentChat.value));
export const messages = computed(() => chatObj.value?.messages || []);
export const hostedModels = computed(() => cfg.value.hf_models || []);  // 'hf:org/name' ids the user added
export const enabledExpertCount = computed(() => cfg.value.experts.filter(e => e.enabled).length);

/* ---------- routing: the URL hash is the route (#/orch, #/chat/<id>) so refresh, back/forward and deep links work ---------- */
const parseHash = () => {
  const [v, id] = location.hash.replace(/^#\/?/, '').split('/');
  return { view: VIEWS.includes(v) ? v : 'chat', id: v === 'chat' && id ? decodeURIComponent(id) : null };
};
export const hashFor = (v, id) => '#/' + v + (v === 'chat' && id ? '/' + encodeURIComponent(id) : '');
export const view = ref(parseHash().view);
export const title = computed(() => view.value === 'chat' ? chatObj.value?.title || 'New Chat' : TITLES[view.value]);

function applyRoute() {  // URL -> state
  const r = parseHash();
  if (r.view === 'chat' && r.id) currentChat.value = chats.value.some(c => c.id === r.id) ? r.id : null;  // unknown chat -> new chat
  view.value = r.view;
}
function syncUrl() {  // state -> URL, without adding a history entry
  const want = hashFor(view.value, view.value === 'chat' ? currentChat.value : null);
  if (location.hash !== want) history.replaceState(null, '', want);
}
export const go = v => { location.hash = hashFor(v, v === 'chat' ? currentChat.value : null); };  // navigation adds a history entry; hashchange applies it
export const openChat = id => { location.hash = hashFor('chat', id); };
export function newChat() { currentChat.value = null; go('chat'); }
export function deleteChat(id) {
  chats.value = chats.value.filter(c => c.id !== id);
  if (currentChat.value === id) currentChat.value = null;
  go('chat');
}
window.addEventListener('hashchange', applyRoute);
watch([view, currentChat], syncUrl);

/* ---------- persistence ---------- */
watch(activeProfile, v => store.set('profile', v));
watch(currentChat, v => store.set('current', v));
watch(groups, v => store.set('groups', { ...v }), { deep: true });
watch(light, v => { document.documentElement.classList.toggle('light', v); store.set('light', v); }, { immediate: true });
watch(collapsed, v => document.body.classList.toggle('collapsed', v), { immediate: true });
watch(current, c => { if (c && activeProfile.value !== c.name) activeProfile.value = c.name; });  // fall back if the active one was switched off or deleted
watch([chats, currentChat], () => {  // file previews can be large and a pending answer is not worth keeping: drop both
  const keep = chats.value.map(c => ({ ...c, messages: c.messages.filter(m => !m.pending) }));
  try { localStorage.setItem('noah.chats', JSON.stringify(keep, (k, v) => k === 'preview' ? undefined : v)); } catch (e) {}
}, { deep: true });

/* ---------- actions ---------- */
export const saveProfiles = () => send('/api/profiles', 'PUT', profiles.value);
export function setMode(m) { if (!current.value) return; current.value.mode = m; saveProfiles(); }

export async function sendMessage(text) {
  const t = text.trim(), p = current.value;
  if (!t || busy.value || !p) return false;
  busy.value = true;
  let c = chatObj.value;
  if (!c) { chats.value.unshift({ id: String(Date.now()), title: t.slice(0, 32), messages: [] }); c = chats.value[0]; currentChat.value = c.id; }
  c.messages.push({ text: t, pending: true });
  const m = c.messages[c.messages.length - 1];  // the reactive copy: mutate this one
  try {
    const r = await send('/api/ask', 'POST', { message: t, profile: p.name, engine: p.engine, engine_model: p.engine_model || '',
      mode: p.mode, extractor: p.extractor, model: p.model, threshold: p.threshold });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'server error ' + r.status);
    Object.assign(m, await r.json(), { profile: p.name, engine: p.engine });
  } catch (err) { m.error = err.message; }
  delete m.pending; busy.value = false;
  return true;
}

export const refreshConfig = async () => { cfg.value = await getJson('/api/config'); };

export async function init() {
  applyRoute();
  [cfg.value, profiles.value] = await Promise.all([getJson('/api/config'), getJson('/api/profiles')]);
  if (!chats.value.find(c => c.id === currentChat.value)) currentChat.value = null;
  syncUrl();
  ready.value = true;
}
