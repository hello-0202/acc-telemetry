import { ref } from 'vue';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

// 悬浮窗（独立的 Tauri 窗口：透明 + 置顶 + 鼠标穿透）。
// 显隐由 Rust 侧统一裁决（按钮 / 全局快捷键都走 toggle_overlay），
// 前端只需要共享一份「当前是否可见」的状态给按钮高亮用。

const isTauri = typeof window !== 'undefined' && !!window.__TAURI_INTERNALS__;

export const overlayVisible = ref(false);

let listening = false;

// 监听 Rust 侧广播的 overlay-visibility：快捷键切显隐时按钮状态才能跟上。
// 模块级只注册一次，多个组件共用同一份状态。
export function initOverlayWatcher() {
  if (listening || !isTauri) return;
  listening = true;
  listen('overlay-visibility', (event) => {
    overlayVisible.value = !!event.payload;
  }).catch(() => {
    listening = false; // 权限缺失：下次挂载再试
  });
}

export async function toggleOverlay() {
  if (!isTauri) return overlayVisible.value;
  try {
    const visible = await invoke('toggle_overlay');
    overlayVisible.value = !!visible;
  } catch (e) {
    console.error('toggle overlay failed:', e);
  }
  return overlayVisible.value;
}

export async function hideOverlay() {
  if (!isTauri || !overlayVisible.value) return;
  try {
    await invoke('hide_overlay');
    overlayVisible.value = false;
  } catch (e) {
    console.error('hide overlay failed:', e);
  }
}

// 进入 / 退出编辑态（解锁后可拖动、拉边缩放）。
// 面板上留一个按钮：全局快捷键有可能被别的软件占用（注册失败只有 stderr），
// 那是最容易「按了没反应」的地方，必须有一条不依赖快捷键的路。
export async function setOverlayLocked(locked) {
  if (!isTauri) return;
  try {
    await invoke('set_overlay_locked', { locked: !!locked });
  } catch (e) {
    console.error('set overlay locked failed:', e);
  }
}
