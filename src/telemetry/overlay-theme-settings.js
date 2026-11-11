// 内置主题配置值：按「悬浮窗槽位 + 主题」存 localStorage，并同步给对应窗口。
//
//   acc.overlayThemeSettings.<slot> = {
//     "builtin:pedal-trace": { "traceLayout": "split", "showVehicle": "show" }
//   }
//
// 为什么按槽位存：每个悬浮窗是独立 webview，背景 / 文字色已经是按槽位隔离的；
// 主题自己的开关也沿用同一套，两个槽位放同一主题时互不干扰。
// 为什么按主题再分一层：同一个槽位换主题后，旧主题的配置留着，切回来还在。
//
// 值只存字符串（与 theme-settings.js 的 options.value 一致），默认值不落盘，
// 由宿主用 theme.json 的 schema 补，这样以后改默认值能立即生效。
import { ref } from 'vue';
import { emit as emitEvent } from '@tauri-apps/api/event';

import { OVERLAY_SLOT_MAX } from './theme-choice.js';

function clampSlot(slot) {
  const n = Number(slot);
  return Number.isFinite(n) ? Math.min(OVERLAY_SLOT_MAX, Math.max(1, Math.trunc(n))) : 1;
}

function storageKey(slot) {
  return `acc.overlayThemeSettings.${clampSlot(slot)}`;
}

/** 主题在存储里的键：内置 / 外部同名时也不能串。 */
export function themeSettingsKey(theme) {
  if (!theme || !theme.id) return '';
  return `${theme.source === 'user' ? 'user' : 'builtin'}:${theme.id}`;
}

/** 读整个槽位的配置对象（浅拷贝，调用方可以随便改）。坏值静默回空对象。 */
function readAll(slot) {
  try {
    const raw = JSON.parse(localStorage.getItem(storageKey(slot)) || 'null');
    return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  } catch {
    return {};
  }
}

/** 版本号：设置页同窗口内写完后，依赖它的 computed（预览 / 面板）会立即重算。 */
export const overlayThemeSettingsVersion = ref(0);

/** 读某个主题在某个槽位里存的值（不补默认值，默认值由 resolveThemeSettings 负责）。 */
export function readThemeSettings(slot, themeKey) {
  if (!slot || !themeKey) return {};
  const all = readAll(slot);
  const own = all[themeKey];
  return own && typeof own === 'object' && !Array.isArray(own) ? { ...own } : {};
}

/**
 * 写一项主题配置；同时广播给对应槽位的悬浮窗。
 * @param {number} slot 1~5
 * @param {string} themeKey themeSettingsKey(theme)
 * @param {string} id 配置项 id
 * @param {string} value 选项 value
 */
export function writeThemeSetting(slot, themeKey, id, value) {
  const n = clampSlot(slot);
  if (!themeKey || !id) return;
  const all = readAll(n);
  const own = all[themeKey] && typeof all[themeKey] === 'object' ? all[themeKey] : {};
  all[themeKey] = { ...own, [id]: String(value) };
  try {
    localStorage.setItem(storageKey(n), JSON.stringify(all));
  } catch {
    /* 隐私模式 / 配额满：本次会话仍然生效 */
  }
  overlayThemeSettingsVersion.value += 1;
  try {
    emitEvent('overlay-theme-settings-changed', { slot: n, themeKey, id, value: String(value) })
      .catch(() => { /* 浏览器预览里没有 Tauri 事件 */ });
  } catch {
    /* 同上 */
  }
}
