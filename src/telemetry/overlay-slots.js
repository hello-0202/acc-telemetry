// 启用的悬浮窗槽位。
//
// 2026-11-10 起 UI 重新放开多选：主题选择器里每个主题都能「使用 / 停用」，
// 每启用一个就占一个槽位，Rust 按这份列表建 / 销毁 overlay-1..5 窗口。
// 上限就是 Rust 的 OVERLAY_SLOTS=5 / theme-choice.js 的 OVERLAY_SLOT_MAX=5；
// read() 按这个常量做 clamp，改上限时这里不用动，但**两边必须一致、且要重编译 Tauri**。
//
// 每个槽位的主题 / 背景各存自己的 localStorage 键，OverlayView 按窗口 label 读；
// 新启用一个槽位时，ThemePicker 会先把该槽位的主题写进去再启用，避免窗口读到旧主题。
import { reactive } from 'vue';
import { invoke } from '@tauri-apps/api/core';

import { OVERLAY_SLOT_MAX } from './theme-choice.js';

const KEY = 'acc.overlaySlots';

/** 当前启用的槽位（响应式）。无记录时默认只开 1 号。 */
function read() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (Array.isArray(raw)) {
      const set = new Set(
        raw.map(Number).filter((n) => Number.isInteger(n) && n >= 1 && n <= OVERLAY_SLOT_MAX)
      );
      return [...set].sort((a, b) => a - b);
    }
  } catch {
    /* 值坏了 / 存储被禁用：回默认 */
  }
  return [1];
}

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

/**
 * 启用 / 停用某个槽位：localStorage + Rust 窗口一起更新。
 * ThemePicker 里每个主题的「使用 / 停用」按钮调它；新启用的槽位主题必须先写好再调。
 */
export async function setOverlaySlotEnabled(slot, enabled) {
  const set = new Set(overlaySlots.list);
  if (enabled) set.add(slot);
  else set.delete(slot);
  overlaySlots.list = [...set].sort((a, b) => a - b);
  persist();
  await pushOverlaySlots();
}
