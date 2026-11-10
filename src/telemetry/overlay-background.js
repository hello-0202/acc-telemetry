// 悬浮窗外观：背景颜色 + 不透明度 + 文字颜色。
//
// 2026-11-10 起，原来的「透明 / 不透明」两档改成连续滑杆 + 可选底色，
// 并加入文字颜色（预设 + 调色盘）：
//   acc.overlayAppearance.<slot> = { color: '#0b0f14', opacity: 0, textColor: '#ffffff' }
//
// 兼容旧值：没有新键时读旧的 `acc.overlayBackground.<slot>`（'transparent' | 'opaque'）。
// 键仍然按槽位隔离：每个悬浮窗是独立 webview，读不到设置页内存，
// 改动靠 localStorage + `overlay-appearance-changed` 事件同步（和主题选择同一套做法）。
//
// 主题支持边界：内置主题默认支持；外部主题必须在 theme.json 里写
// `"overlaySettings": ["background"]` 才会被应用 / 可设置。判断逻辑在 overlay-settings.js，
// 这里只负责存值与生成宿主底板样式。
import { reactive } from 'vue';
import { emit as emitEvent } from '@tauri-apps/api/event';

import { OVERLAY_SLOT_MAX } from './theme-choice.js';

export const DEFAULT_OVERLAY_BACKGROUND_COLOR = '#0b0f14';
export const DEFAULT_OVERLAY_BACKGROUND_OPACITY = 0;
export const DEFAULT_OVERLAY_TEXT_COLOR = '#ffffff';
// 背景模糊：0 = 关，1 = 开。实际效果由 OverlayView 调 Rust 的 Windows Acrylic 实现；
// 预览页没有 OS 窗口，用 CSS backdrop-filter 近似（见 ThemePicker）。
export const DEFAULT_OVERLAY_BACKGROUND_BLUR = 0;

// 旧「不透明」档用的就是这块纯黑底板；迁移时保持视觉不变。
const LEGACY_OPAQUE_COLOR = '#0b0f14';

function clampSlot(slot) {
  const n = Number(slot);
  return Number.isFinite(n) ? Math.min(OVERLAY_SLOT_MAX, Math.max(1, Math.trunc(n))) : 1;
}

function appearanceKey(slot) {
  return `acc.overlayAppearance.${clampSlot(slot)}`;
}

function legacyKey(slot) {
  return `acc.overlayBackground.${clampSlot(slot)}`;
}

/** 归一颜色：只认 #rgb / #rrggbb（取色器、预设都是这种格式），其它回默认深黑。 */
export function normalizeOverlayColor(value) {
  if (typeof value !== 'string') return DEFAULT_OVERLAY_BACKGROUND_COLOR;
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value.trim());
  if (!m) return DEFAULT_OVERLAY_BACKGROUND_COLOR;
  let hex = m[1].toLowerCase();
  if (hex.length === 3) hex = hex.split('').map((c) => c + c).join('');
  return `#${hex}`;
}

/** 归一不透明度：接受 0..1（也接受外面误传的 0..100），夹紧到合法范围。 */
export function normalizeOverlayOpacity(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return DEFAULT_OVERLAY_BACKGROUND_OPACITY;
  const v = n > 1 ? n / 100 : n;
  return Math.min(1, Math.max(0, v));
}

/** 归一模糊开关：任何大于 0 的旧值（曾经是 px 滑杆）都视为开启。 */
export function normalizeOverlayBlur(value) {
  return Number(value) > 0 ? 1 : 0;
}

function normalizeAppearance(value) {
  return {
    color: normalizeOverlayColor(value?.color),
    opacity: normalizeOverlayOpacity(value?.opacity),
    textColor: normalizeOverlayColor(value?.textColor ?? DEFAULT_OVERLAY_TEXT_COLOR),
    blur: normalizeOverlayBlur(value?.blur),
  };
}

function defaultAppearance() {
  return {
    color: DEFAULT_OVERLAY_BACKGROUND_COLOR,
    opacity: DEFAULT_OVERLAY_BACKGROUND_OPACITY,
    textColor: DEFAULT_OVERLAY_TEXT_COLOR,
    blur: DEFAULT_OVERLAY_BACKGROUND_BLUR,
  };
}

function readSlot(slot) {
  try {
    const raw = localStorage.getItem(appearanceKey(slot));
    if (raw) {
      try {
        return normalizeAppearance(JSON.parse(raw));
      } catch {
        // JSON 坏了 → 继续尝试旧键 / 默认值
      }
    }
    // 旧版两档设置迁移：opaque → 100% + 深黑；transparent → 0%
    const legacy = localStorage.getItem(legacyKey(slot));
    if (legacy === 'opaque') return normalizeAppearance({ color: LEGACY_OPAQUE_COLOR, opacity: 1 });
    if (legacy === 'transparent') return normalizeAppearance({ color: LEGACY_OPAQUE_COLOR, opacity: 0 });
  } catch {
    // 隐私模式 / 存储被禁用：回默认
  }
  return defaultAppearance();
}

/** 各槽位当前外观（响应式）：同窗口的组件直接读它。 */
export const overlayAppearance = reactive(
  Object.fromEntries(Array.from({ length: OVERLAY_SLOT_MAX }, (_, i) => [i + 1, readSlot(i + 1)])),
);

export function overlayAppearanceFor(slot) {
  return overlayAppearance[clampSlot(slot)];
}

/**
 * 以 localStorage 为准重读某个槽位。
 * 别的窗口（设置页）改完后只会写 localStorage + 发事件，本窗口内存里那份副本是旧的。
 */
export function readOverlayAppearance(slot) {
  const n = clampSlot(slot);
  const value = readSlot(n);
  Object.assign(overlayAppearance[n], value);
  return value;
}

/** 设置某个槽位的外观（可以只传 color 或 opacity）；同时广播给悬浮窗。 */
export function setOverlayAppearance(slot, patch) {
  const n = clampSlot(slot);
  const next = normalizeAppearance({ ...overlayAppearance[n], ...patch });
  Object.assign(overlayAppearance[n], next);
  try {
    localStorage.setItem(appearanceKey(n), JSON.stringify(next));
  } catch {
    /* 隐私模式 / 配额满：忽略，本次会话仍然生效 */
  }
  try {
    // 各悬浮窗只认自己槽位的那条事件
    emitEvent('overlay-appearance-changed', { slot: n, ...next }).catch(() => {
      /* 浏览器预览里没有 Tauri 事件 */
    });
  } catch {
    /* 同上：没有 Tauri 运行时时 emit 可能同步抛错 */
  }
  return next;
}

/** #rgb / #rrggbb + 0..1 → rgba()，给宿主底色层用（只影响背景，不会淡化主题内容）。 */
export function overlayColorWithAlpha(color, opacity) {
  const hex = normalizeOverlayColor(color).slice(1);
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  const a = normalizeOverlayOpacity(opacity).toFixed(3);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}

/**
 * 宿主底板的内联样式：只负责颜色 / 不透明度（background-color）。
 * 模糊不走 CSS —— 透明 WebView2 窗口的 `backdrop-filter` 采样不到窗口外的游戏画面，
 * 真机由 OverlayView 调 Rust 的 Windows Acrylic（`set_overlay_backdrop`）；
 * 预览页没有 OS 窗口，仍在 ThemePreview 里用 CSS 近似。
 * 完全透明时不产生任何背景覆盖。
 */
export function overlayBackdropStyle(appearance) {
  const { color, opacity } = normalizeAppearance(appearance);
  return {
    backgroundColor: opacity > 0 ? overlayColorWithAlpha(color, opacity) : 'transparent',
  };
}
