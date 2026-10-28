// 主题选择：按「用途槽位」分别记住用户选了哪个主题。
//
//   overlay1 / overlay2 / overlay3 —— 最多三个游戏悬浮窗（各自独立主题）
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

export const OVERLAY_SLOT_MAX = 3;

const MONITOR_KEY = 'acc.monitorTheme';
/** 旧版单悬浮窗的键：升级后继续给 1 号槽位用，老用户的选择不会丢 */
const LEGACY_OVERLAY_KEY = 'acc.overlayTheme';

const FALLBACK = {
  overlay: DEFAULT_THEME,
  monitor: DEFAULT_MONITOR_THEME,
};

function clampSlot(slot) {
  const n = Number(slot);
  return Number.isFinite(n) ? Math.min(OVERLAY_SLOT_MAX, Math.max(1, Math.trunc(n))) : 1;
}

function overlayKey(slot) {
  return `acc.overlayTheme.${clampSlot(slot)}`;
}

/** themeChoice 里的属性名：overlay1..3 / monitor */
export function themeChoiceKey(kind, slot = 1) {
  return kind === 'monitor' ? 'monitor' : `overlay${clampSlot(slot)}`;
}

function normalize(raw, kind) {
  if (raw && typeof raw.id === 'string' && raw.id) {
    return { id: raw.id, source: raw.source === 'user' ? 'user' : 'builtin' };
  }
  return { ...FALLBACK[kind] };
}

function readFromStorage(key, kind) {
  try {
    return normalize(JSON.parse(localStorage.getItem(key) || 'null'), kind);
  } catch {
    // 值被写坏了就回默认
    return { ...FALLBACK[kind] };
  }
}

function readOverlay(slot) {
  const n = clampSlot(slot);
  try {
    // 1 号槽位还没单独存过时，回退到旧版的单悬浮窗键（老用户升级后选择不丢）
    const own = localStorage.getItem(overlayKey(n));
    if (own != null) return normalize(JSON.parse(own), 'overlay');
    if (n === 1) {
      const legacy = localStorage.getItem(LEGACY_OVERLAY_KEY);
      if (legacy != null) return normalize(JSON.parse(legacy), 'overlay');
    }
  } catch {
    /* 值坏了就回默认 */
  }
  return { ...FALLBACK.overlay };
}

/** 当前选择（响应式）：改主题后同窗口的组件会立刻跟着更新。 */
export const themeChoice = reactive({
  overlay1: readOverlay(1),
  overlay2: readOverlay(2),
  overlay3: readOverlay(3),
  monitor: readFromStorage(MONITOR_KEY, 'monitor'),
});

/**
 * 读某个槽位当前选中的主题。
 *
 * 以 **localStorage 为准**：多悬浮窗是各自独立的 webview，别的窗口改完只会写
 * localStorage + 发一条事件，本窗口内存里那份 reactive 副本是旧的。顺手把副本同步掉，
 * 同窗口里直接读 `themeChoice` 的组件也能跟着更新。
 *
 * @param {'overlay'|'monitor'} kind
 * @param {number} [slot] 悬浮窗槽位（1~3，仅 kind='overlay' 时有效）
 */
export function readThemeChoice(kind = 'overlay', slot = 1) {
  const stored = kind === 'monitor'
    ? readFromStorage(MONITOR_KEY, 'monitor')
    : readOverlay(slot);
  themeChoice[themeChoiceKey(kind, slot)] = stored;
  return { ...stored };
}

/**
 * @param {{id: string, source?: string}} t
 * @param {'overlay'|'monitor'} kind
 * @param {number} [slot]
 */
export function saveThemeChoice(t, kind = 'overlay', slot = 1) {
  const key = themeChoiceKey(kind, slot);
  const next = normalize(t, kind === 'monitor' ? 'monitor' : 'overlay');
  themeChoice[key] = next;
  try {
    if (kind === 'monitor') {
      localStorage.setItem(MONITOR_KEY, JSON.stringify(next));
    } else {
      localStorage.setItem(overlayKey(slot), JSON.stringify(next));
      // 1 号槽位顺手写旧键：万一某个旧代码路径还在读它
      if (clampSlot(slot) === 1) localStorage.setItem(LEGACY_OVERLAY_KEY, JSON.stringify(next));
    }
  } catch {
    /* 隐私模式 / 配额满：忽略，本次会话仍然生效 */
  }
  return { ...next };
}
