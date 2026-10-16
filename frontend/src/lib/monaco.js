// Monaco, loaded only when the expert code editor opens (it is a large chunk). Workers are bundled by Vite, so nothing
// is fetched from a CDN and the editor works offline and inside the desktop app.
import * as monaco from './monaco-core.js';
import EditorWorker from 'monaco-vs/editor/editor.worker.js?worker';
import JsonWorker from 'monaco-vs/languages/features/json/json.worker.js?worker';

self.MonacoEnvironment = { getWorker: (_, label) => (label === 'json' ? new JsonWorker() : new EditorWorker()) };

// Match the app's palettes (assets/app.css) so the editor does not look bolted on.
monaco.editor.defineTheme('noah-dark', { base: 'vs-dark', inherit: true, rules: [],
  colors: { 'editor.background': '#1b1e24', 'editorGutter.background': '#1b1e24', 'editor.lineHighlightBackground': '#23272f' } });
monaco.editor.defineTheme('noah-light', { base: 'vs', inherit: true, rules: [], colors: { 'editor.background': '#ffffff' } });

const LANG = { py: 'python', yaml: 'yaml', yml: 'yaml', json: 'json', md: 'markdown', txt: 'plaintext' };
export const languageOf = name => LANG[name.split('.').pop()] || 'plaintext';
export { monaco };
