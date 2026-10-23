export const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// Escape, then turn ``` fences into code blocks and `x` into inline code. The result is rendered with v-html, so everything is escaped first.
export function fmt(text) {
  return String(text ?? '').split(/```/).map((part, i) => {
    if (i % 2 === 0) return esc(part.replace(/^\n+|\n+$/g, '')).replace(/`([^`\n]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>').replace(/^#{1,4} (.+)$/gm, '<b>$1</b>');
    const nl = part.indexOf('\n'); const lang = part.slice(0, nl).trim(); const code = part.slice(nl + 1);
    return `<pre><span class="lang">${esc(lang)}</span><code>${esc(code.replace(/\n$/, ''))}</code></pre>`;
  }).join('');
}

export const humanSize = n => n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
export const countLabel = (on, total) => on === total ? String(total) : `${on}/${total}`;  // "7", or "5/7" when some are off
// Hosted models are called through a provider's API and never downloaded: 'hf:org/name' (Hugging Face), 'or:org/name' (OpenRouter),
// 'az:<deployment>' (a deployment on your own Azure Foundry resource), 'oa:<model>' (OpenAI) and 'an:<model>' (Claude, from Anthropic).
export const PROVIDERS = { hf: { label: 'Hugging Face', prefix: 'hf:', secret: 'token' }, or: { label: 'OpenRouter', prefix: 'or:', secret: 'API key' },
  az: { label: 'Azure Foundry', prefix: 'az:', secret: 'connection' }, oa: { label: 'OpenAI', prefix: 'oa:', secret: 'API key' },
  an: { label: 'Claude', prefix: 'an:', secret: 'API key' } };
export const hostedKind = m => Object.keys(PROVIDERS).find(k => String(m || '').startsWith(PROVIDERS[k].prefix)) || '';
export const isHosted = m => hostedKind(m) !== '';
export const shortModel = m => (isHosted(m) ? '☁ ' : '') + String(m || '').replace(/^(hf|or|az|oa|an):/, '').replace(/^hf\.co\//, '').split('/').pop();
export const norm = m => { m = String(m || ''); return m && !m.split('/').pop().includes(':') ? m + ':latest' : m; };  // "x" == "x:latest"
