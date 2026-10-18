// ============================================================================
// Telemetry utilities — pure formatting / math helpers.
// 生产数据来自后端（WS 实时 + REST 历史），这里不再生成模拟数据。
// 绘图辅助在 draw.js。
// ============================================================================

/* ----------------------------- utils ----------------------------- */
export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
export const lerp = (a, b, t) => a + (b - a) * t;

export function fmtLap(ms) {
  if (ms == null || !isFinite(ms)) return '--:--.---';
  const m = Math.floor(ms / 60000), s = Math.floor(ms % 60000 / 1000), t = Math.floor(ms % 1000);
  return m + ':' + String(s).padStart(2, '0') + '.' + String(t).padStart(3, '0');
}
export function fmtShort(ms) {
  const s = Math.floor(ms / 1000);
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}
export function fmtDelta(ms) {
  return (ms >= 0 ? '+' : '−') + (Math.abs(ms) / 1000).toFixed(3);
}
export function hash1(n) {
  const s = Math.sin(n * 127.1 + 0.7) * 43758.5453;
  return s - Math.floor(s);
}
export function vnoise(x) {
  const i = Math.floor(x), f = x - i, a = hash1(i), b = hash1(i + 1), u = f * f * (3 - 2 * f);
  return lerp(a, b, u) * 2 - 1;
}
export function cssVar(n) {
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
}
export function mixHex(h1, h2, t) {
  const p = h => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  const a = p(h1), b = p(h2);
  return 'rgb(' + a.map((v, i) => Math.round(lerp(v, b[i], t))).join(',') + ')';
}
export const reduceMotion = typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ----------------------------- helpers ----------------------------- */
export function tireClass(t) { return t < 75 ? 'cold' : (t > 105 ? 'hot' : 'good'); }
export function sectorsOf(T, n) {
  const i1 = Math.floor(n / 3), i2 = Math.floor(2 * n / 3);
  return [T[i1], T[i2] - T[i1], T[n - 1] - T[i2]];
}
