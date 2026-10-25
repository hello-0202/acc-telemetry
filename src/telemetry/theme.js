import { ref } from 'vue';

// Reactive theme state shared across the app.
export const themeName = ref('dark');
export const themeVersion = ref(0); // bumped on every theme/accent change so computed SVG/charts redraw

// 主题色（强调色）预设，同时决定按钮、标签、开关等 --signal 相关颜色
export const ACCENTS = [
  { id: 'red', name: '红', color: '#D6002B' },
  { id: 'orange', name: '橙', color: '#D65D00' },
  { id: 'lime', name: '青柠', color: '#A1D600' },
  { id: 'green', name: '绿', color: '#00D659' },
  { id: 'cyan', name: '青', color: '#00B3D6' },
  { id: 'violet', name: '蓝紫', color: '#4000D6' },
  { id: 'purple', name: '品红', color: '#CF00D6' },
];
export const accentName = ref(ACCENTS[0].id);

function validAccent(id) {
  return ACCENTS.some((a) => a.id === id) ? id : ACCENTS[0].id;
}

export function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('acc-theme', t); } catch (e) { /* ignore */ }
  themeName.value = t;
  themeVersion.value++;
}
export function toggleTheme() {
  setTheme(themeName.value === 'dark' ? 'light' : 'dark');
}
export function setAccent(id) {
  const name = validAccent(id);
  document.documentElement.dataset.accent = name;
  try { localStorage.setItem('acc-accent', name); } catch (e) { /* ignore */ }
  accentName.value = name;
  themeVersion.value++;
}
export function initTheme() {
  let t = 'dark';
  let a = ACCENTS[0].id;
  try {
    t = localStorage.getItem('acc-theme') || 'dark';
    a = localStorage.getItem('acc-accent') || ACCENTS[0].id;
  } catch (e) { /* ignore */ }
  document.documentElement.dataset.theme = t;
  themeName.value = t;
  setAccent(a);
}
