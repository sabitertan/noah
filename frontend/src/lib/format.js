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
export const isHosted = m => String(m || '').startsWith('hf:');  // served by Hugging Face's API, nothing downloaded
export const shortModel = m => (isHosted(m) ? '☁ ' : '') + String(m || '').replace(/^hf:/, '').replace(/^hf\.co\//, '').split('/').pop();
export const norm = m => { m = String(m || ''); return m && !m.split('/').pop().includes(':') ? m + ':latest' : m; };  // "x" == "x:latest"
