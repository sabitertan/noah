// Monaco, loaded only when the expert code editor opens (it is a large chunk). Workers are bundled by Vite, so nothing
// is fetched from a CDN and the editor works offline and inside the desktop app.
import { watch } from 'vue';
import * as monaco from './monaco-core.js';
import { themeInfo } from '../store.js';
import EditorWorker from 'monaco-vs/editor/editor.worker.js?worker';
import JsonWorker from 'monaco-vs/languages/features/json/json.worker.js?worker';

self.MonacoEnvironment = { getWorker: (_, label) => (label === 'json' ? new JsonWorker() : new EditorWorker()) };

// The editor wears the same VS Code theme as the app (lib/themes.js): its editor colors and its main token colors.
const defined = new Set();
export function editorTheme(t) {
  const id = 'noah-' + t.id;
  if (!defined.has(id)) {
    monaco.editor.defineTheme(id, { base: t.dark ? 'vs-dark' : 'vs', inherit: true, rules: t.rules,
      colors: { 'editorGutter.background': t.vars.bg, 'editor.background': t.vars.bg, 'editor.foreground': t.vars.fg, ...t.editor } });
    defined.add(id);
  }
  return id;
}
// Monaco has one theme for every editor on the page: keep it on the app's theme (expert files, diffs, chat code blocks)
watch(themeInfo, t => monaco.editor.setTheme(editorTheme(t)), { immediate: true });

const LANG = { py: 'python', yaml: 'yaml', yml: 'yaml', json: 'json', md: 'markdown', txt: 'plaintext' };
export const languageOf = name => LANG[name.split('.').pop()] || 'plaintext';
// a code fence's tag (```cs, ```C#, ```bash, ```tsx …) -> a registered Monaco language
const FENCE = { 'c#': 'csharp', cs: 'csharp', 'c++': 'cpp', c: 'cpp', h: 'cpp', sh: 'shell', bash: 'shell', zsh: 'shell', console: 'shell',
  ps: 'powershell', ps1: 'powershell', pwsh: 'powershell', cmd: 'bat', js: 'javascript', jsx: 'javascript', mjs: 'javascript', ts: 'typescript',
  tsx: 'typescript', vue: 'html', htm: 'html', svg: 'xml', toml: 'ini', golang: 'go', rs: 'rust', kt: 'kotlin', rb: 'ruby', py: 'python' };
export function languageOfFence(tag) {
  const t = String(tag || '').trim().toLowerCase();
  if (!t) return 'plaintext';
  const id = FENCE[t] || t;
  const known = monaco.languages.getLanguages();
  return (known.find(l => l.id === id) || known.find(l => (l.aliases || []).some(a => a.toLowerCase() === t)) ||
    known.find(l => (l.extensions || []).includes('.' + t)))?.id || 'plaintext';
}
export { monaco };
