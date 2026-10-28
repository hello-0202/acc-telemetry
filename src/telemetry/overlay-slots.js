// 启用的悬浮窗槽位（最多 3 个）。
//
// 为什么存 localStorage：和主题选择一样，这是「这台机器上的界面偏好」，不进数据契约。
// 但窗口是 Rust 建的，所以每次变化都要 push 给 Rust —— 它据此创建 / 销毁 WebView2 窗口，
// 并让全局快捷键知道该显隐哪几个。
import { reactive } from 'vue';
import { invoke } from '@tauri-apps/api/core';

import { OVERLAY_SLOT_MAX } from './theme-choice.js';

const KEY = 'acc.overlaySlots';

function read() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (Array.isArray(raw)) {
      return [...new Set(
        raw
          .map(Number)
          .filter((n) => Number.isInteger(n) && n >= 1 && n <= OVERLAY_SLOT_MAX)
      )].sort((a, b) => a - b);
    }
  } catch {
    /* 坏值回默认 */
  }
  return [1];
}

/** 当前启用的槽位（响应式）。空数组 = 用户把悬浮窗全关了。 */
export const overlaySlots = reactive({ list: read() });

export function isOverlaySlotEnabled(slot) {
  return overlaySlots.list.includes(slot);
}

function persist() {
  try {
    localStorage.setItem(KEY, JSON.stringify(overlaySlots.list));
  } catch {
    /* 隐私模式 / 配额满：忽略，本次会话仍然生效 */
  }
}

/** 把当前启用的槽位同步给 Rust（应用启动时调用一次即可）。 */
export async function pushOverlaySlots() {
  try {
    await invoke('set_overlay_slots', { slots: overlaySlots.list });
  } catch (e) {
    console.error('同步悬浮窗槽位失败:', e);
  }
}

/** 启用 / 停用某个槽位：localStorage + Rust 窗口一起更新。 */
export async function setOverlaySlotEnabled(slot, enabled) {
  const set = new Set(overlaySlots.list);
  if (enabled) set.add(slot);
  else set.delete(slot);
  overlaySlots.list = [...set].sort((a, b) => a - b);
  persist();
  await pushOverlaySlots();
}
