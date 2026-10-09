const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function fmt(text) {  // escape, then turn ``` fences into code blocks and `x` into inline code
  return String(text ?? '').split(/```/).map((part, i) => {
    if (i % 2 === 0) return esc(part.replace(/^\n+|\n+$/g, '')).replace(/`([^`\n]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>').replace(/^#{1,4} (.+)$/gm, '<b>$1</b>');
    const nl = part.indexOf('\n'); const lang = part.slice(0, nl).trim(); const code = part.slice(nl + 1);
    return `<pre><span class="lang">${esc(lang)}</span><code>${esc(code.replace(/\n$/, ''))}</code></pre>`;
  }).join('');
}
const store = {
  get(k, d) { try { const v = localStorage.getItem('noah.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('noah.' + k, JSON.stringify(v)); } catch (e) {} },
};
const ico = {
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  cpu: '<circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>',
  tool: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.4 2.4-2.6-.6-.6-2.6z"/>',
  box: '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  list: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  chev: '<polyline points="9 6 15 12 9 18"/>', x: '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
};
const svg = (n, s = 15) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${ico[n]}</svg>`;

let cfg = { engines: [], modes: [], extractors: [], models: [], experts: [] };
let profiles = [];
let chats = store.get('chats', []);
let currentChat = store.get('current', null);
let activeProfile = store.get('profile', 'Default');
const shortModel = m => String(m || '').replace(/^hf\.co\//, '').split('/').pop();
let view = 'chat', editing = null, busy = false;

/* ---------- sidebar ---------- */
function renderSidebar() {
  const open = store.get('groups', { chats: true, setup: true });
  const nav = (id, icon, label, extra = '') => `<div class="item ${view === id ? 'active' : ''}" data-view="${id}">${svg(icon)}<span class="grow">${label}</span>${extra}</div>`;
  $('sbInner').innerHTML = `
    <div class="item" id="newChat">${svg('plus')}<span class="grow">New Chat</span></div>
    <div class="item ${open.chats ? 'open' : ''}" data-group="chats">${svg('chat')}<span class="grow">Chats</span>${svg('chev', 12).replace('<svg', '<svg class="chev"')}</div>
    <div class="group-body ${open.chats ? '' : 'closed'}">${chats.length ? chats.map(c =>
      `<div class="item chat-item ${c.id === currentChat && view === 'chat' ? 'active' : ''}" data-chat="${c.id}"><span class="grow">${esc(c.title)}</span><button class="icon-btn del" data-del="${c.id}" title="Delete">${svg('x', 12)}</button></div>`).join('')
      : '<div class="item"><span class="meta">No chats yet</span></div>'}</div>
    <div class="item ${open.setup ? 'open' : ''}" data-group="setup">${svg('tool')}<span class="grow">Setup</span>${svg('chev', 12).replace('<svg', '<svg class="chev"')}</div>
    <div class="group-body ${open.setup ? '' : 'closed'}">
      ${nav('orch', 'cpu', 'Orchestrators', `<span class="meta">${profiles.length}</span>`)}
      ${nav('experts', 'box', 'Experts', `<span class="meta">${cfg.experts.length}</span>`)}
      ${nav('models', 'list', 'Models', `<span class="dot ${cfg.models.length ? 'on' : ''}"></span>`)}
      ${nav('traces', 'list', 'Traces')}
    </div>
    <div class="item" id="themeBtn">${svg('moon')}<span class="grow">Theme</span></div>`;
}

$('sbInner').addEventListener('click', e => {
  const del = e.target.closest('[data-del]');
  if (del) { chats = chats.filter(c => c.id !== del.dataset.del); if (currentChat === del.dataset.del) currentChat = null; saveChats(); show('chat'); return; }
  const g = e.target.closest('[data-group]');
  if (g) { const o = store.get('groups', { chats: true, setup: true }); o[g.dataset.group] = !o[g.dataset.group]; store.set('groups', o); renderSidebar(); return; }
  const c = e.target.closest('[data-chat]');
  if (c) { currentChat = c.dataset.chat; store.set('current', currentChat); show('chat'); return; }
  const v = e.target.closest('[data-view]');
  if (v) return show(v.dataset.view);
  if (e.target.closest('#newChat')) { currentChat = null; store.set('current', null); show('chat'); $('msg').focus(); }
  if (e.target.closest('#themeBtn')) { const l = !document.documentElement.classList.contains('light'); document.documentElement.classList.toggle('light', l); store.set('light', l); }
});
const toggleSide = () => document.body.classList.toggle('collapsed');
$('hamb').onclick = toggleSide; $('hamb2').onclick = toggleSide;

function show(v) {
  view = v;
  document.querySelectorAll('.view').forEach(x => x.classList.toggle('active', x.id === 'v-' + v));
  $('title').textContent = { chat: chatObj()?.title || 'New Chat', orch: 'Orchestrators', experts: 'Experts', models: 'Models', traces: 'Traces' }[v];
  ({ chat: renderChat, orch: renderOrch, experts: renderExperts, models: renderModels, traces: renderTraces })[v]();
  renderSidebar();
}

/* ---------- chat ---------- */
const chatObj = () => chats.find(c => c.id === currentChat);
const saveChats = () => { store.set('chats', chats); store.set('current', currentChat); };
const profile = () => profiles.find(p => p.name === activeProfile) || profiles[0];

function renderAnswers(m) {
  if (m.error) return `<div class="a err"><div class="body">${esc(m.error)}</div></div>`;
  const chosen = new Set(m.results.map(r => r.expert));
  const bars = m.decisions.map(d => `<div class="row"><span>${esc(d.expert)}</span><div class="bar"><i class="${chosen.has(d.expert) ? 'hit' : ''}" style="width:${Math.max(0, Math.min(1, d.score)) * 100}%"></i></div><span>${d.score.toFixed(2)}</span></div>`).join('');
  return m.results.map(r => `<div class="a ${r.error ? 'err' : ''}">
      <div class="head"><span class="badge">${esc(r.expert)}</span><span>${esc(r.extractor)} · ${r.latency_ms.toFixed(0)} ms</span></div>
      <div class="body">${fmt(r.output)}</div>
      <details><summary>arguments</summary><code>${esc(JSON.stringify(r.args))}</code>${r.error ? `<br><code>${esc(r.error)}</code>` : ''}</details>
    </div>`).join('') + `<details><summary>routing decision · ${esc(m.profile)} · ${esc(m.engine)}</summary>${bars}</details>`;
}
const renderMsg = m => `<div class="msg"><div class="u"><span>${esc(m.text)}</span></div>${m.pending ? '<div class="a typing">routing…</div>' : renderAnswers(m)}</div>`;

function renderChat() {
  const c = chatObj();
  $('profileSel').innerHTML = profiles.map(p => `<option ${p.name === activeProfile ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
  syncMode();
  if (!c || !c.messages.length) {
    const ex = cfg.experts.flatMap(e => e.examples.slice(0, 1));
    $('thread').innerHTML = `<div class="empty"><h1>${svg('cpu', 26)} Noah</h1><p>Tiny experts, one job each.</p><div class="chips">${ex.map(t => `<button class="chip" data-ex="${esc(t)}">${esc(t)}</button>`).join('')}</div></div>`;
  } else $('thread').innerHTML = c.messages.map(renderMsg).join('');
  $('log').scrollTop = $('log').scrollHeight;
}
$('thread').addEventListener('click', e => { const b = e.target.closest('[data-ex]'); if (b) { $('msg').value = b.dataset.ex; send(); } });
$('profileSel').onchange = () => { activeProfile = $('profileSel').value; store.set('profile', activeProfile); syncMode(); };
function syncMode() { const p = profile(); document.querySelectorAll('#modeToggle button').forEach(b => b.classList.toggle('on', p && b.dataset.m === p.mode)); }
$('modeToggle').addEventListener('click', e => { const b = e.target.closest('button'); if (!b || !profile()) return; profile().mode = b.dataset.m; saveProfiles(); syncMode(); });
$('msg').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
$('msg').addEventListener('input', () => { const t = $('msg'); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 160) + 'px'; });
$('form').addEventListener('submit', e => { e.preventDefault(); send(); });

async function send() {
  const text = $('msg').value.trim(); const p = profile();
  if (!text || busy || !p) return;
  busy = true; $('send').disabled = true; $('msg').value = ''; $('msg').style.height = 'auto';
  let c = chatObj();
  if (!c) { c = { id: String(Date.now()), title: text.slice(0, 32), messages: [] }; chats.unshift(c); currentChat = c.id; }
  const m = { text, pending: true }; c.messages.push(m); renderChat(); renderSidebar(); $('title').textContent = c.title;
  try {
    const r = await fetch('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      message: text, engine: p.engine, engine_model: p.engine_model || '', mode: p.mode, extractor: p.extractor, model: p.model, threshold: p.threshold }) });
    if (!r.ok) throw new Error('server error ' + r.status);
    Object.assign(m, await r.json(), { profile: p.name, engine: p.engine });
  } catch (err) { m.error = err.message; }
  delete m.pending; saveChats(); busy = false; $('send').disabled = false; renderChat(); $('msg').focus();
}

/* ---------- orchestrators ---------- */
async function saveProfiles() { await fetch('/api/profiles', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(profiles) }); }
function renderOrch() {
  if (editing === null || !profiles[editing]) editing = Math.max(0, profiles.findIndex(p => p.name === activeProfile));
  const p = profiles[editing];
  const opts = (items, v) => items.map(i => `<option ${i === v ? 'selected' : ''}>${esc(i)}</option>`).join('');
  $('orchPanel').innerHTML = `<h2>Orchestrators</h2><p class="lede">An orchestrator is a saved combination of decision engine, orchestration mode and argument extractor. Create as many as you like.</p>
    <div class="cards">${profiles.map((x, i) => `<div class="card ${i === editing ? 'sel' : ''}" data-edit="${i}"><h3>${svg('cpu')}${esc(x.name)}${x.name === activeProfile ? '<span class="badge">active</span>' : ''}</h3>
      <p>${esc(x.engine)}${x.engine === 'llm_tools' ? ' (' + esc(shortModel(x.engine_model)) + ')' : ''} → ${esc(x.mode)} → ${x.engine === 'llm_tools' ? 'model args' : esc(x.extractor) + (x.extractor === 'openai_compat' ? ' (' + esc(x.model) + ')' : '')}</p></div>`).join('')}
      <div class="card" data-new style="cursor:pointer;display:flex;align-items:center;justify-content:center;color:var(--muted)">${svg('plus')}&nbsp;New orchestrator</div></div>
    <div class="form">
      <label>Name</label><input id="f-name" value="${esc(p.name)}">
      <label>Decision engine</label><select id="f-engine">${opts(cfg.engines, p.engine)}</select>
      <label id="l-em" ${p.engine === 'llm_tools' ? '' : 'hidden'}>Orchestrator model</label><select id="f-em" ${p.engine === 'llm_tools' ? '' : 'hidden'}>${opts(cfg.models.length ? cfg.models : [p.engine_model || ''], p.engine_model || '')}</select>
      <label>Orchestration</label><select id="f-mode">${opts(cfg.modes, p.mode)}</select>
      <label>Argument extractor</label><select id="f-extractor">${opts(cfg.extractors, p.extractor)}</select>
      <label>Tiny model</label><select id="f-model" ${p.extractor === 'openai_compat' ? '' : 'disabled'}><option value="" ${p.model ? '' : 'selected'}>—</option>${opts(cfg.models.length ? cfg.models : [p.model], p.model)}</select>
      <label>Threshold <span id="f-thv">${p.threshold}</span></label><input type="range" id="f-threshold" min="0" max="1" step="0.05" value="${p.threshold}">
    </div>
    <div class="actions"><button class="btn primary" id="f-save">Save</button><button class="btn" id="f-use">Use in chat</button><button class="btn danger" id="f-del" ${profiles.length < 2 ? 'disabled' : ''}>Delete</button></div>`;
  $('f-engine').onchange = () => { const on = $('f-engine').value === 'llm_tools'; $('l-em').hidden = $('f-em').hidden = !on; };
  $('f-extractor').onchange = () => { $('f-model').disabled = $('f-extractor').value !== 'openai_compat'; };
  $('f-threshold').oninput = () => { $('f-thv').textContent = $('f-threshold').value; };
  const collect = () => { const old = p.name; Object.assign(p, { name: $('f-name').value.trim() || old, engine: $('f-engine').value, mode: $('f-mode').value,
    extractor: $('f-extractor').value, model: $('f-model').value, engine_model: $('f-em').value, threshold: parseFloat($('f-threshold').value) }); if (activeProfile === old) activeProfile = p.name; store.set('profile', activeProfile); };
  $('f-save').onclick = async () => { collect(); await saveProfiles(); renderOrch(); renderSidebar(); };
  $('f-use').onclick = async () => { collect(); activeProfile = p.name; store.set('profile', activeProfile); await saveProfiles(); show('chat'); };
  $('f-del').onclick = async () => { profiles.splice(editing, 1); editing = 0; if (!profiles.find(x => x.name === activeProfile)) activeProfile = profiles[0].name; await saveProfiles(); renderOrch(); renderSidebar(); };
}
$('orchPanel').addEventListener('click', async e => {
  const ed = e.target.closest('[data-edit]'); if (ed) { editing = +ed.dataset.edit; renderOrch(); }
  if (e.target.closest('[data-new]')) { profiles.push({ ...profiles[editing], name: 'Orchestrator ' + (profiles.length + 1) }); editing = profiles.length - 1; await saveProfiles(); renderOrch(); renderSidebar(); }
});

/* ---------- experts / models / traces ---------- */
function renderExperts() {
  $('expertsPanel').innerHTML = `<h2>Experts</h2><p class="lede">Each expert is a folder in <code>experts/</code>: a manifest, a small model and a tool. Drop one in and restart to add it.</p>
    <div class="cards">${cfg.experts.map(e => `<div class="card"><h3>${svg('box')}${esc(e.name)}</h3><p>${esc(e.description)}</p>
      <div class="tags"><span class="tag">${e.kind === 'generate' ? 'generates · ' + esc(e.model) : 'tool'}</span><span class="tag">license: ${esc(e.license)}</span>${Object.keys(e.parameters.properties || {}).map(k => `<span class="tag">${esc(k)}</span>`).join('')}</div>
      <details><summary>examples</summary>${e.examples.map(x => `<div><code>${esc(x)}</code></div>`).join('')}</details></div>`).join('')}</div>`;
}
function renderModels() {
  const norm = m => { m = String(m || ''); return m && !m.split('/').pop().includes(':') ? m + ':latest' : m; };  // "x" == "x:latest"
  const orchestrators = m => profiles.flatMap(p => [
    ...(p.engine === 'llm_tools' && norm(p.engine_model) === norm(m) ? [`${p.name} (orchestrator)`] : []),
    ...(p.engine !== 'llm_tools' && p.extractor === 'openai_compat' && norm(p.model) === norm(m) ? [`${p.name} (extractor)`] : []),
  ]);
  const experts = m => cfg.experts.filter(e => e.kind === 'generate' && norm(e.model) === norm(m)).map(e => e.name);
  const cell = list => list.length ? list.map(x => `<div>${esc(x)}</div>`).join('') : '<span class="meta">—</span>';
  $('modelsPanel').innerHTML = `<h2>Models</h2><p class="lede">Local models served by Ollama. Pull more with <code>ollama pull &lt;name&gt;</code>; any OpenAI-compatible server works through the same extractor.</p>
    ${cfg.models.length ? `<table><tr><th>Model</th><th>Used by orchestrators</th><th>Used by experts</th></tr>${cfg.models.map(m =>
      `<tr><td>${esc(m)}</td><td>${cell(orchestrators(m))}</td><td>${cell(experts(m))}</td></tr>`).join('')}</table>` : '<p class="lede">No local models found. Is Ollama running?</p>'}`;
}
async function renderTraces() {
  const t = await (await fetch('/api/traces?limit=100')).json();
  $('tracesPanel').innerHTML = `<h2>Traces</h2><p class="lede">Every routing decision is logged. This is the raw material for fine-tuning your own orchestrators and experts.</p>
    ${t.length ? `<table><tr><th>When</th><th>Message</th><th>Engine</th><th>Chosen</th><th>ms</th></tr>${t.map(r => `<tr><td>${new Date(r.ts * 1000).toLocaleTimeString()}</td><td>${esc(r.message)}</td><td>${esc(r.engine)}</td><td>${esc(r.results.map(x => x.expert).join(', '))}</td><td>${r.total_ms.toFixed(0)}</td></tr>`).join('')}</table>` : '<p class="lede">Nothing yet.</p>'}`;
}

/* ---------- init ---------- */
(async () => {
  if (store.get('light', false)) document.documentElement.classList.add('light');
  [cfg, profiles] = await Promise.all([fetch('/api/config').then(r => r.json()), fetch('/api/profiles').then(r => r.json())]);
  if (!profiles.find(p => p.name === activeProfile)) activeProfile = profiles[0].name;
  if (!chats.find(c => c.id === currentChat)) currentChat = null;
  show('chat');
})();
