// The one place UI state lives. Components read these refs and call these actions, so two views can never disagree.
import { ref, reactive, computed, watch } from 'vue';
import { send, getJson, store } from './lib/api.js';
import { PROVIDERS } from './lib/format.js';
import { DEFAULT_THEME, themeById, applyTheme } from './lib/themes.js';

export const MODEL_ENGINES = ['llm_tools', 'llm_choice', 'laya'];  // engines that take an engine_model
export const TITLES = { orch: 'Orchestrators', experts: 'Experts', models: 'Models', traces: 'Traces', themes: 'Themes' };
export const VIEWS = ['chat', ...Object.keys(TITLES)];
export const IS_WASM = import.meta.env.MODE === 'wasm';   // the browser-only build: Python runs in the page (src/wasm/)
export const MODEL_TABS = ['local', 'hf', 'or', 'az'];  // Models page: my models, Hugging Face, OpenRouter, Azure Foundry
export const isOn = p => p.enabled !== false;  // profiles saved before the switch existed count as enabled

/* ---------- state ---------- */
export const cfg = ref({ engines: [], modes: [], extractors: [], models: [], experts: [], local: false, hf: {}, or: {}, az: {}, hf_models: [], or_models: [], az_models: [] });
export const profiles = ref([]);
export const chats = ref(store.get('chats', []));
export const currentChat = ref(store.get('current', null));
export const activeProfile = ref(store.get('profile', 'Default'));
export const groups = reactive(store.get('groups', { chats: true, setup: true }));
export const theme = ref(store.get('theme', store.get('light', false) ? 'light-modern' : DEFAULT_THEME));  // 'light' was the old on/off switch
export const collapsed = ref(false);
export const busy = ref(false);
export const ready = ref(false);  // config and profiles loaded; views mount after this

/* ---------- derived ---------- */
export const enabledProfiles = computed(() => profiles.value.filter(isOn));
export const current = computed(() => enabledProfiles.value.find(p => p.name === activeProfile.value) || enabledProfiles.value[0]);  // never a disabled one
export const themeInfo = computed(() => themeById(theme.value));
export const chatObj = computed(() => chats.value.find(c => c.id === currentChat.value));
export const messages = computed(() => chatObj.value?.messages || []);
export const hostedModels = computed(() => [...(cfg.value.hf_models || []), ...(cfg.value.or_models || []), ...(cfg.value.az_models || [])]);  // 'hf:…' / 'or:…' / 'az:…' ids the user added
export const enabledExpertCount = computed(() => cfg.value.experts.filter(e => e.enabled).length);

/* ---------- routing: the URL hash is the route (#/orch, #/chat/<id>) so refresh, back/forward and deep links work ---------- */
const parseHash = () => {
  const [v, sub] = location.hash.replace(/^#\/?/, '').split('/');
  return { view: VIEWS.includes(v) ? v : 'chat', id: v === 'chat' && sub ? decodeURIComponent(sub) : null,
           tab: v === 'models' && MODEL_TABS.includes(sub) ? sub : 'local', expert: v === 'experts' && sub ? decodeURIComponent(sub) : null };
};
// `sub` is the chat id on #/chat/<id>, the tab on #/models/<tab> ('local' is the default and stays out of the URL),
// and the expert being edited on #/experts/<name> (#/experts/new for a fresh one)
export const hashFor = (v, sub) => '#/' + v + (v === 'chat' && sub ? '/' + encodeURIComponent(sub) : v === 'models' && sub && sub !== 'local' ? '/' + sub
  : v === 'experts' && sub ? '/' + encodeURIComponent(sub) : '');
export const view = ref(parseHash().view);
export const modelsTab = ref(parseHash().tab);
export const expertSub = ref(parseHash().expert);   // null: the list of experts; otherwise the editor
const subFor = v => v === 'chat' ? currentChat.value : v === 'models' ? modelsTab.value : null;   // where a sidebar link goes (experts: the list)
export const title = computed(() => view.value === 'chat' ? chatObj.value?.title || 'New Chat' : TITLES[view.value]);

function applyRoute() {  // URL -> state
  const r = parseHash();
  if (r.view === 'chat' && r.id) currentChat.value = chats.value.some(c => c.id === r.id) ? r.id : null;  // unknown chat -> new chat
  view.value = r.view;
  if (r.view === 'models') modelsTab.value = r.tab;  // other pages keep the last tab, so coming back lands where you left
  expertSub.value = r.view === 'experts' ? r.expert : null;
}
function syncUrl() {  // state -> URL, without adding a history entry
  const want = hashFor(view.value, view.value === 'experts' ? expertSub.value : subFor(view.value));
  if (location.hash !== want) history.replaceState(null, '', want);
}
// Navigating adds a history entry and applies the new route right away. Waiting for the browser's hashchange event is not enough:
// a watcher on the state can run first and write the old route back over the new one.
function navigate(v, sub) {
  const h = hashFor(v, sub);
  if (location.hash !== h) location.hash = h;
  applyRoute();
}
export const go = v => navigate(v, subFor(v));
export const openModelsTab = t => navigate('models', t);
export const openExpert = name => navigate('experts', name);   // 'new' opens an empty editor
export const openChat = id => navigate('chat', id);
export function newChat() { currentChat.value = null; navigate('chat', null); }
export function deleteChat(id) {
  chats.value = chats.value.filter(c => c.id !== id);
  if (currentChat.value === id) currentChat.value = null;
  go('chat');
}
window.addEventListener('hashchange', applyRoute);
watch([view, currentChat, modelsTab, expertSub], syncUrl);

/* ---------- persistence ---------- */
watch(activeProfile, v => store.set('profile', v));
watch(currentChat, v => store.set('current', v));
watch(groups, v => store.set('groups', { ...v }), { deep: true });
watch(themeInfo, t => { applyTheme(t); store.set('theme', t.id); }, { immediate: true });
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

// Model choices grouped by where they run. A saved value that is in no list stays visible instead of being swapped silently.
// `engine` is for decision engines that bring their own list (laya); pass '' for an ordinary model picker.
export function modelGroups(engine, cur) {
  const own = (cfg.value.engine_models || {})[engine];
  const groups = own ? [{ label: 'Models', models: own }]
    : [{ label: 'Local (Ollama)', models: cfg.value.models },
       ...Object.entries(PROVIDERS).map(([k, p]) => ({ label: `${p.label} (hosted, no download)`, models: cfg.value[k + '_models'] || [] }))];
  if (cur && !groups.some(g => g.models.includes(cur))) groups.unshift({ label: 'Current', models: [cur] });
  return groups.filter(g => g.models.length);
}

export const refreshConfig = async () => { cfg.value = await getJson('/api/config'); };

export async function init() {
  applyRoute();
  [cfg.value, profiles.value] = await Promise.all([getJson('/api/config'), getJson('/api/profiles')]);
  if (!chats.value.find(c => c.id === currentChat.value)) currentChat.value = null;
  syncUrl();
  ready.value = true;
}
