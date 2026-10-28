// 悬浮窗背景：透明（浮在游戏画面上，能看见后面）或不透明（整窗铺实心底板）。
//
// 和主题选择一样属于「这台机器上的界面偏好」，按槽位存 localStorage：
//   acc.overlayBackground.<slot> = 'transparent' | 'opaque'
//
// 设置页改了以后要 emit 一条事件：每个悬浮窗都是独立 webview，读不到本窗口的内存，
// 只能靠 localStorage + 事件同步（和 overlay-theme-changed 同一套做法）。
import { reactive } from 'vue';
import { emit as emitEvent } from '@tauri-apps/api/event';

import { OVERLAY_SLOT_MAX } from './theme-choice.js';

export const OVERLAY_BACKGROUNDS = ['transparent', 'opaque'];
export const DEFAULT_OVERLAY_BACKGROUND = 'transparent';

function clampSlot(slot) {
  const n = Number(slot);
  return Number.isFinite(n) ? Math.min(OVERLAY_SLOT_MAX, Math.max(1, Math.trunc(n))) : 1;
}

function storageKey(slot) {
  return `acc.overlayBackground.${clampSlot(slot)}`;
}

function normalize(value) {
  return value === 'opaque' ? 'opaque' : DEFAULT_OVERLAY_BACKGROUND;
}

function readSlot(slot) {
  try {
    return normalize(localStorage.getItem(storageKey(slot)));
  } catch {
    // 隐私模式 / 存储被禁用：回默认
    return DEFAULT_OVERLAY_BACKGROUND;
  }
}

/** 各槽位当前背景（响应式）：同窗口的组件直接读它。 */
export const overlayBackground = reactive({
  1: readSlot(1),
  2: readSlot(2),
  3: readSlot(3),
});

export function overlayBackgroundFor(slot) {
  return overlayBackground[clampSlot(slot)];
}

/**
 * 以 localStorage 为准重读某个槽位。
 * 别的窗口（设置页）改完后只会写 localStorage + 发事件，本窗口内存里那份副本是旧的。
 */
export function readOverlayBackground(slot) {
  const n = clampSlot(slot);
  const value = readSlot(n);
  overlayBackground[n] = value;
  return value;
}

/** 设置某个槽位的背景；同时广播给悬浮窗。 */
export function setOverlayBackground(slot, value) {
  const n = clampSlot(slot);
  const next = normalize(value);
  overlayBackground[n] = next;
  try {
    localStorage.setItem(storageKey(n), next);
  } catch {
    /* 隐私模式 / 配额满：忽略，本次会话仍然生效 */
  }
  try {
    // 各悬浮窗只认自己槽位的那条事件
    emitEvent('overlay-background-changed', { slot: n, value: next }).catch(() => {
      /* 浏览器预览里没有 Tauri 事件 */
    });
  } catch {
    /* 同上：没有 Tauri 运行时时 emit 可能同步抛错 */
  }
  return next;
}
