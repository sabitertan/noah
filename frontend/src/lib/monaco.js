// Monaco, loaded only when the expert code editor opens (it is a large chunk). Workers are bundled by Vite, so nothing
// is fetched from a CDN and the editor works offline and inside the desktop app.
import * as monaco from './monaco-core.js';
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

const LANG = { py: 'python', yaml: 'yaml', yml: 'yaml', json: 'json', md: 'markdown', txt: 'plaintext' };
export const languageOf = name => LANG[name.split('.').pop()] || 'plaintext';
export { monaco };
