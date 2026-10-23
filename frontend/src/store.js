// The one place UI state lives. Components read these refs and call these actions, so two views can never disagree.
import { ref, reactive, computed, watch } from 'vue';
import { send, getJson, store } from './lib/api.js';
import { PROVIDERS } from './lib/format.js';
import { DEFAULT_THEME, themeById, applyTheme } from './lib/themes.js';

export const MODEL_ENGINES = ['llm_tools', 'llm_choice', 'laya'];  // engines that take an engine_model
export const TITLES = { build: 'Builder', orch: 'Orchestrators', experts: 'Experts', models: 'Models', traces: 'Traces', themes: 'Themes' };
export const VIEWS = ['chat', ...Object.keys(TITLES)];
export const IS_WASM = import.meta.env.MODE === 'wasm';   // the browser-only build: Python runs in the page (src/wasm/)
export const MODEL_TABS = ['local', 'hf', 'or', 'az', 'an', 'oa', 'web'];  // Models page: my models, Hugging Face, OpenRouter, Azure Foundry, Claude, OpenAI, WebLLM (in the browser)
export const isOn = p => p.enabled !== false;  // profiles saved before the switch existed count as enabled

/* ---------- state ---------- */
export const cfg = ref({ engines: [], modes: [], extractors: [], models: [], experts: [], local: false, hf: {}, or: {}, az: {}, oa: {}, an: {}, hf_models: [], or_models: [], az_models: [], oa_models: [], an_models: [], web_models: [] });
export const profiles = ref([]);
export const chats = ref(store.get('chats', []));
export const currentChat = ref(store.get('current', null));
export const activeProfile = ref(store.get('profile', 'Default'));
export const projects = ref(store.get('projects', []));   // Builder page: one per app being built, each a list of prompts and their builds
export const currentProject = ref(store.get('project', null));
export const buildProfile = ref(store.get('buildProfile', ''));   // which build orchestrator the Builder page uses
export const groups = reactive(store.get('groups', { chats: true, setup: true }));
export const theme = ref(store.get('theme', store.get('light', false) ? 'light-modern' : DEFAULT_THEME));  // 'light' was the old on/off switch
export const collapsed = ref(false);
export const busy = ref(false);
export const ready = ref(false);  // config and profiles loaded; views mount after this

/* ---------- derived ---------- */
export const enabledProfiles = computed(() => profiles.value.filter(isOn));
export const builders = computed(() => enabledProfiles.value.filter(p => p.mode === 'build'));   // Builder page
export const builder = computed(() => builders.value.find(p => p.name === buildProfile.value) || builders.value[0]);
export const routers = computed(() => enabledProfiles.value.filter(p => p.mode !== 'build'));   // the chat's picker
export const current = computed(() => routers.value.find(p => p.name === activeProfile.value) || routers.value[0]);  // never a disabled one
export const projectObj = computed(() => projects.value.find(p => p.id === currentProject.value));
export const themeInfo = computed(() => themeById(theme.value));
export const chatObj = computed(() => chats.value.find(c => c.id === currentChat.value));
export const messages = computed(() => chatObj.value?.messages || []);
export const hostedModels = computed(() => Object.keys(PROVIDERS).flatMap(k => cfg.value[k + '_models'] || []));  // 'hf:…' / 'or:…' / 'az:…' / 'oa:…' / 'an:…' ids the user added
export const enabledExpertCount = computed(() => cfg.value.experts.filter(e => e.enabled).length);

/* ---------- routing: the URL hash is the route (#/orch, #/chat/<id>) so refresh, back/forward and deep links work ---------- */
const parseHash = () => {
  const [v, sub] = location.hash.replace(/^#\/?/, '').split('/');
  return { view: VIEWS.includes(v) ? v : 'chat', id: (v === 'chat' || v === 'build') && sub ? decodeURIComponent(sub) : null,
           tab: v === 'models' && MODEL_TABS.includes(sub) ? sub : 'local', expert: v === 'experts' && sub ? decodeURIComponent(sub) : null };
};
// `sub` is the chat id on #/chat/<id>, the tab on #/models/<tab> ('local' is the default and stays out of the URL),
// and the expert being edited on #/experts/<name> (#/experts/new for a fresh one)
export const hashFor = (v, sub) => '#/' + v + ((v === 'chat' || v === 'build') && sub ? '/' + encodeURIComponent(sub) : v === 'models' && sub && sub !== 'local' ? '/' + sub
  : v === 'experts' && sub ? '/' + encodeURIComponent(sub) : '');
export const view = ref(parseHash().view);
export const modelsTab = ref(parseHash().tab);
export const expertSub = ref(parseHash().expert);   // null: the list of experts; otherwise the editor
const subFor = v => v === 'chat' ? currentChat.value : v === 'build' ? currentProject.value : v === 'models' ? modelsTab.value : null;   // where a sidebar link goes (experts: the list)
export const title = computed(() => view.value === 'chat' ? chatObj.value?.title || 'New Chat' : view.value === 'build' ? projectObj.value?.title || 'New app' : TITLES[view.value]);

function applyRoute() {  // URL -> state
  const r = parseHash();
  if (r.view === 'build') currentProject.value = r.id && projects.value.some(p => p.id === r.id) ? r.id : null;
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
export const openProject = id => navigate('build', id);
export function newProject() { currentProject.value = null; navigate('build', null); }
export function deleteProject(id) {
  projects.value = projects.value.filter(p => p.id !== id);
  if (currentProject.value === id) newProject();
}
export function deleteChat(id) {
  chats.value = chats.value.filter(c => c.id !== id);
  if (currentChat.value === id) currentChat.value = null;
  go('chat');
}
window.addEventListener('hashchange', applyRoute);
watch([view, currentChat, currentProject, modelsTab, expertSub], syncUrl);

/* ---------- persistence ---------- */
watch(activeProfile, v => store.set('profile', v));
watch(currentProject, v => store.set('project', v));
watch(buildProfile, v => store.set('buildProfile', v));
watch(projects, () => {  // a build still running cannot be resumed after a reload, so it is not kept as running
  try { localStorage.setItem('noah.projects', JSON.stringify(projects.value.map(p => ({ ...p, messages: p.messages.filter(m => !m.pending) })))); } catch (e) {}
}, { deep: true });
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
  finally { delete m.pending; busy.value = false; }
  return true;
}

// Builder page: each prompt builds a new version of the current project; the newest one that finished is shown.
export async function sendBuild(text) {
  const t = text.trim(), p = builder.value;
  if (!t || busy.value || !p) return false;
  busy.value = true;
  let pr = projectObj.value;
  if (!pr) { projects.value.unshift({ id: String(Date.now()), title: t.slice(0, 40), messages: [], shown: -1 }); pr = projects.value[0]; openProject(pr.id); }
  pr.messages.push({ text: t, pending: true, profile: p.name });
  const i = pr.messages.length - 1, m = pr.messages[i];
  try {
    await runBuild(m, t, p);
    pr.shown = i;
    if (pr.messages.length === 1 && m.build.title) pr.title = m.build.title;
  } catch (err) { m.error = err.message; }
  finally { delete m.pending; busy.value = false; }
  return true;
}

async function post(url, body) {
  const r = await send(url, 'POST', body), j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'server error ' + r.status);
  return j;
}

// Build mode (noah/builder.py): plan, then one call per step so the card can show progress, then put the parts together.
async function runBuild(m, text, p) {
  const start = performance.now();
  m.build = { steps: [], html: '', storage: {} };
  delete m.pending;  // the build card shows progress from here
  const b = m.build;
  const { plan } = await post('/api/build/plan', { message: text, model: p.planner_model || '', build_model: p.build_model || '' });
  Object.assign(b, { title: plan.title, summary: plan.summary, features: plan.features, planner: plan.planner, small: plan.small,
    steps: plan.steps.map(s => ({ expert: s.expert, part: s.part, state: 'waiting' })) });
  const parts = {}, done = [];
  for (const [i, s] of b.steps.entries()) {
    s.state = 'running';
    const r = await post('/api/build/step', { plan, index: i, parts });
    Object.assign(s, { state: r.error ? 'error' : 'done', error: r.error, ms: r.latency_ms });
    if (r.code) parts[r.part] = r.code;
    else if (r.part === 'html') throw new Error(r.error || 'The markup expert wrote nothing, so there is nothing to show. Try again or rephrase.');
    done.push({ expert: r.expert, part: r.part, error: r.error, latency_ms: r.latency_ms, code: '' });
  }
  b.html = (await post('/api/build/finish', { message: text, plan, parts, steps: done, total_ms: performance.now() - start })).html;
}

// Model choices grouped by where they run. A saved value that is in no list stays visible instead of being swapped silently.
// `engine` is for decision engines that bring their own list (laya); pass '' for an ordinary model picker.
export function modelGroups(engine, cur) {
  const own = (cfg.value.engine_models || {})[engine];
  const groups = own ? [{ label: 'Models', models: own }]
    : [{ label: 'Local (Ollama)', models: cfg.value.models },
       ...Object.entries(PROVIDERS).map(([k, p]) => ({ label: `${p.label} (hosted, no download)`, models: cfg.value[k + '_models'] || [] })),
       { label: 'In this browser (WebLLM)', models: cfg.value.web_models || [] }];
  if (cur && !groups.some(g => g.models.includes(cur))) groups.unshift({ label: 'Current', models: [cur] });
  return groups.filter(g => g.models.length);
}

export const refreshConfig = async () => { cfg.value = await getJson('/api/config'); };

export async function init() {
  applyRoute();
  [cfg.value, profiles.value] = await Promise.all([getJson('/api/config'), getJson('/api/profiles')]);
  if (!chats.value.find(c => c.id === currentChat.value)) currentChat.value = null;
  if (!projects.value.find(p => p.id === currentProject.value)) currentProject.value = null;
  for (const m of chats.value.flatMap(c => c.messages))  // a build the page was closed during cannot resume
    if (m.build && !m.build.html && !m.error) m.error = 'This build was interrupted. Ask again to rebuild it.';
  for (const m of projects.value.flatMap(p => p.messages))
    if (!m.error && !m.build?.html) m.error = 'This build was interrupted. Ask again to rebuild it.';
  syncUrl();
  ready.value = true;
}
