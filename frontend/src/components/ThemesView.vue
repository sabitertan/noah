<script setup>
// Themes: every VS Code theme as a card showing a small VS Code window in its colors (like vscodethemes.com). Click to use it.
import { THEMES } from '../lib/themes.js';
import { theme } from '../store.js';

const groups = [{ label: 'Dark', themes: THEMES.filter(t => t.dark) }, { label: 'Light', themes: THEMES.filter(t => !t.dark) }];

// the sample on every card: [text, token] pieces per line; tokens take the theme's colors (its Monaco rules)
const SAMPLE = [
  [['const ', 'keyword'], ['btn', 'variable'], [' = '], ['document', 'variable'], ['.'], ['getElementById', 'function'], ['('], ["'btn'", 'string'], [');']],
  [['let ', 'keyword'], ['count', 'variable'], [' = '], ['0', 'number'], [';']],
  [],
  [['function ', 'keyword'], ['render', 'function'], ['() {']],
  [['  btn', 'variable'], ['.'], ['innerText', 'variable'], [' = '], ['`Count: ${count}`', 'string'], [';']],
  [['}']],
  [],
  [['btn', 'variable'], ['.'], ['addEventListener', 'function'], ['('], ["'click'", 'string'], [', () => {']],
  [['  // Count from 1 to 10.', 'comment']],
  [['  if ', 'keyword'], ['('], ['count', 'variable'], [' < '], ['10', 'number'], [') {']],
  [['    count', 'variable'], [' += '], ['1', 'number'], [';']],
  [['    render', 'function'], ['();']],
  [['  }']],
  [['});']],
];
const ICONS = [
  '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M4 16V5a1 1 0 0 1 1-1h11"/>',
  '<circle cx="11" cy="11" r="6"/><line x1="20" y1="20" x2="15.5" y2="15.5"/>',
  '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="9" r="2"/><path d="M6 7v10M18 11c0 4-6 3-12 6"/>',
  '<polygon points="7 4 19 12 7 20 7 4"/>',
  '<rect x="4" y="4" width="7" height="7"/><rect x="13" y="4" width="7" height="7"/><rect x="4" y="13" width="7" height="7"/><rect x="13" y="13" width="7" height="7"/>',
];

const colorOf = (t, token) => {
  if (!token) return t.editor['editor.foreground'] || t.vars.fg;
  const r = t.rules.find(x => x.token === token);
  return r ? '#' + r.foreground : t.editor['editor.foreground'] || t.vars.fg;
};
const cardVars = t => Object.fromEntries([
  ...Object.entries(t.chrome).map(([k, v]) => ['--' + k, v]),
  ['--ed-bg', t.vars.bg], ['--ed-fg', t.editor['editor.foreground'] || t.vars.fg], ['--ed-ln', t.editor['editorLineNumber.foreground'] || t.vars.muted],
]);
</script>

<template>
  <section class="view active" id="v-themes"><div class="panel wide" id="themesPanel">
    <h2>Themes</h2>
    <p class="lede">The whole app, code blocks and the expert code editor use the colors of a real VS Code theme. These are VS Code's own
      Dark and Light Modern and the most installed themes on <a href="https://vscodethemes.com/" target="_blank" rel="noopener">vscodethemes.com</a>.</p>
    <template v-for="g in groups" :key="g.label">
      <h3 class="theme-group">{{ g.label }}</h3>
      <div class="theme-grid">
        <button v-for="t in g.themes" :key="t.id" class="theme-card" :class="{ active: theme === t.id }" :data-theme="t.id"
          :aria-pressed="theme === t.id" @click="theme = t.id">
          <div class="tc-win" :style="cardVars(t)" aria-hidden="true">
            <div class="tc-title">{{ t.name }}</div>
            <div class="tc-main">
              <div class="tc-act">
                <svg v-for="(ic, i) in ICONS" :key="i" :class="{ on: i === 0 }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" v-html="ic"></svg>
                <i class="tc-badge"></i>
              </div>
              <div class="tc-ed">
                <div class="tc-tabs"><span class="tc-tab">main.js</span></div>
                <pre class="tc-code"><template v-for="(line, i) in SAMPLE" :key="i"><span class="tc-ln">{{ i + 1 }}</span><span v-for="(p, j) in line" :key="j" :style="{ color: colorOf(t, p[1]) }">{{ p[0] }}</span>
</template></pre>
              </div>
            </div>
            <div class="tc-status"><span>⊗ 0 ⚠ 0</span><span>JavaScript</span></div>
          </div>
          <div class="tc-meta">
            <div><div class="tc-name">{{ t.name }}</div><div class="tc-by">by {{ t.author }}</div></div>
            <span v-if="theme === t.id" class="badge">in use</span>
          </div>
        </button>
      </div>
    </template>
  </div></section>
</template>
