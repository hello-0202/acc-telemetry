// 主题选择：按「用途槽位」分别记住用户选了哪个主题。
//
//   overlay1 .. overlay5 —— 游戏悬浮窗槽位（各自独立主题，最多同时启用 5 个）
//   monitor —— 主面板数据区（同一窗口，靠这里的 reactive 直接同步）
//
// 设置页只有一个「悬浮窗主题」入口，但弹窗里每个主题都能独立「使用 / 停用」：
// 启用就占一个空闲槽位，Rust 按槽位列表建窗口。`overlay-slots.js` 存启用列表，
// 上限 OVERLAY_SLOT_MAX 必须与 Rust main.rs 的 OVERLAY_SLOTS 一致（改上限要重编译 Tauri）。
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

export const OVERLAY_SLOT_MAX = 5;

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

/** themeChoice 里的属性名：overlay1..5 / monitor */
export function themeChoiceKey(kind, slot = 1) {
  return kind === 'monitor' ? 'monitor' : `overlay${clampSlot(slot)}`;
}

// 内置主题改过名的话在这里登记旧 id → 新 id。
// localStorage 里存的是 id，改名后老用户的选择会指向一个不存在的主题（悬浮窗只剩报错卡），
// 所以读取时先做一次映射，把旧值悄悄搬过去。
//
// 同名映射（id 不变）用来表达另一件事：**这个主题从「外部」转成「内置」了**。
// gt-cockpit 以前只以 zip 形式在外部安装过，存下来的选择是 { id, source: 'user' }，
// 内置之后那份用户目录随时会被删掉；走到这里就顺手把 source 改回 builtin，
// 免得升级后主面板指向一个已经不存在的外部主题、只剩一张报错卡。
const RENAMED_THEMES = {
  'overlay-min': 'speed-gear',
  'gt-cockpit': 'gt-cockpit',
  // monitor-grid 已从内置主题移除：老用户的选择回退到内置主面板，
  // 避免打开应用后主题加载失败再等 4 秒回退。
  'monitor-grid': '_panel',
};

function normalize(raw, kind) {
  if (raw && typeof raw.id === 'string' && raw.id) {
    // 旧 id 指向的是内置主题，改名后按新 id 继续用
    const renamed = RENAMED_THEMES[raw.id];
    if (renamed) return { id: renamed, source: 'builtin' };
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
  overlay4: readOverlay(4),
  overlay5: readOverlay(5),
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
 * @param {number} [slot] 悬浮窗槽位（1~5，仅 kind='overlay' 时有效）
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
