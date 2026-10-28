// 主题选择：按「用途槽位」分别记住用户选了哪个主题。
//
//   overlay —— 游戏悬浮窗（独立 webview，靠事件同步）
//   monitor —— 主面板数据区（同一窗口，靠这里的 reactive 直接同步）
//
// 为什么不用 config.json：`POST /api/config` 是按白名单收键的（`acc_telemetry/api/config.py`），
// 加键就得改 Python 并重打包 sidecar。主题选择只是**这台机器上的界面偏好**，
// 不需要进数据契约，所以存 localStorage：
//   - 主窗口和悬浮窗同源（tauri://localhost），localStorage 天然共享；
//   - 换主题后主窗口再 emit 一条事件，免得悬浮窗轮询；
//   - 同窗口内的组件直接读下面这个 reactive 对象，不用事件。
// 以后要挪回 config.json 时，只换这里的实现（记得同步改 Python 的默认值与白名单）。
import { reactive } from 'vue';

import { DEFAULT_MONITOR_THEME, DEFAULT_THEME } from './themes.js';

const KEYS = {
  overlay: 'acc.overlayTheme',
  monitor: 'acc.monitorTheme',
};

const FALLBACK = {
  overlay: DEFAULT_THEME,
  monitor: DEFAULT_MONITOR_THEME,
};

function normalize(raw, slot) {
  if (raw && typeof raw.id === 'string' && raw.id) {
    return { id: raw.id, source: raw.source === 'user' ? 'user' : 'builtin' };
  }
  return { ...FALLBACK[slot] };
}

function readFromStorage(slot) {
  try {
    return normalize(JSON.parse(localStorage.getItem(KEYS[slot]) || 'null'), slot);
  } catch {
    // 值被写坏了就回默认
    return { ...FALLBACK[slot] };
  }
}

/** 当前选择（响应式）：改主题后同窗口的组件会立刻跟着更新。 */
export const themeChoice = reactive({
  overlay: readFromStorage('overlay'),
  monitor: readFromStorage('monitor'),
});

/** @param {'overlay'|'monitor'} slot */
export function readThemeChoice(slot = 'overlay') {
  return { ...themeChoice[slot] };
}

/**
 * @param {{id: string, source?: string}} t
 * @param {'overlay'|'monitor'} slot
 */
export function saveThemeChoice(t, slot = 'overlay') {
  const next = normalize(t, slot);
  themeChoice[slot] = next;
  try {
    localStorage.setItem(KEYS[slot], JSON.stringify(next));
  } catch {
    /* 隐私模式 / 配额满：忽略，本次会话仍然生效 */
  }
  return { ...next };
}
