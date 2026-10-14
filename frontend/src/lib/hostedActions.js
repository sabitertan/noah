// Test and remove hosted models (Hugging Face, OpenRouter, Azure Foundry); shared by the Models table and the Azure tab.
import { ref } from 'vue';
import { send } from './api.js';
import { hostedKind, PROVIDERS } from './format.js';
import { cfg } from '../store.js';

export const tests = ref({});  // model id -> { busy } | { ok, text }

export async function testModel(id) {
  tests.value[id] = { busy: true };
  try {
    const j = await (await send(`/api/${hostedKind(id)}/test`, 'POST', { id })).json();
    tests.value[id] = j.ok ? { ok: true, text: `works: “${j.reply}” in ${j.ms} ms` } : { ok: false, text: j.error || j.detail };
  } catch (e) { tests.value[id] = { ok: false, text: e.message }; }
}

export async function removeModel(id) {
  const kind = hostedKind(id);
  const r = await fetch(`/api/${kind}/my/` + id.slice(PROVIDERS[kind].prefix.length), { method: 'DELETE' });
  if (r.ok) cfg.value[kind + '_models'] = (await r.json()).models;
}
