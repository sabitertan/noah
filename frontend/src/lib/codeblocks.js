// v-code: turn the <pre> code blocks that fmt() renders into read-only Monaco editors in the app's theme. The plain <pre>
// shows until Monaco (a large, lazily loaded chunk) is ready and a block scrolls near the screen, so a long chat stays light.
const live = new WeakMap();   // element with v-code -> editors created inside it
let monacoP = null;
const loadMonaco = () => (monacoP ||= import('./monaco.js'));
const seen = new IntersectionObserver(entries => {
  for (const e of entries) if (e.isIntersecting) { seen.unobserve(e.target); upgrade(e.target); }
}, { rootMargin: '400px' });

const COPY = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>';

async function upgrade(pre) {
  if (!pre.isConnected || pre.dataset.monaco) return;
  pre.dataset.monaco = '1';
  const { monaco, languageOfFence } = await loadMonaco();
  if (!pre.isConnected) return;
  const tag = pre.querySelector('.lang')?.textContent || '';
  const code = pre.querySelector('code')?.textContent ?? '';
  const block = document.createElement('div');
  block.className = 'codeblock';
  block.innerHTML = '<div class="cb-head"><span class="cb-lang"></span><button class="cb-copy" title="Copy">' + COPY + '<span>Copy</span></button></div><div class="cb-ed"></div>';
  block.querySelector('.cb-lang').textContent = tag || 'text';
  const host = block.querySelector('.cb-ed');
  pre.replaceWith(block);
  const ed = monaco.editor.create(host, {
    value: code, language: languageOfFence(tag), readOnly: true, domReadOnly: true, automaticLayout: true,
    fontFamily: "'Fira Code', ui-monospace, monospace", fontSize: 13, lineHeight: 20, lineNumbersMinChars: 3,
    minimap: { enabled: false }, scrollBeyondLastLine: false, scrollBeyondLastColumn: 2, folding: false, glyphMargin: false,
    renderLineHighlight: 'none', overviewRulerLanes: 0, hideCursorInOverviewRuler: true, overviewRulerBorder: false,
    stickyScroll: { enabled: false }, guides: { indentation: false }, contextmenu: false, matchBrackets: 'never',
    occurrencesHighlight: 'off', selectionHighlight: false, links: false, padding: { top: 8, bottom: 8 },
    scrollbar: { vertical: 'hidden', horizontalScrollbarSize: 8, alwaysConsumeMouseWheel: false, useShadows: false },
  });
  const fit = () => { host.style.height = ed.getContentHeight() + 'px'; };
  ed.onDidContentSizeChange(fit); fit();
  const btn = block.querySelector('.cb-copy');
  btn.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(code); btn.lastChild.textContent = 'Copied'; } catch (e) { btn.lastChild.textContent = 'Copy failed'; }
    setTimeout(() => { btn.lastChild.textContent = 'Copy'; }, 1500);
  });
  const owner = block.closest('[data-vcode]');
  if (owner) live.get(owner)?.push(ed);
}

function scan(el) {
  const eds = live.get(el) || [];
  live.set(el, eds.filter(ed => ed.getContainerDomNode().isConnected || (ed.dispose(), false)));  // v-html replaced them
  el.querySelectorAll('pre:not([data-monaco])').forEach(pre => { if (pre.querySelector('code')) seen.observe(pre); });
}

export const vCode = {
  mounted(el) { el.dataset.vcode = ''; scan(el); },
  updated: scan,
  beforeUnmount(el) {
    el.querySelectorAll('pre:not([data-monaco])').forEach(pre => seen.unobserve(pre));
    for (const ed of live.get(el) || []) ed.dispose();
    live.delete(el);
  },
};
