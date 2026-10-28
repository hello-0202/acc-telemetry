<script setup>
// 悬浮窗：主题宿主 + 窗口自身表现。
//   - 主题不连 WebSocket，只收宿主灌进去的 state（后端已算好的快照）
//   - 数据用被动模式接（passive）：只收不发，绝不影响主窗口的记录状态
//   - 默认鼠标穿透（锁定）；Ctrl+Shift+L 切编辑模式，可拖动 / 拉边缩放
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { getCurrentWindow } from '@tauri-apps/api/window';

import ThemeHost from './ThemeHost.vue';
import { readThemeChoice } from '../telemetry/theme-choice.js';
import { disconnect, start, telemetry } from '../telemetry/useTelemetry.js';

const isTauri = typeof window !== 'undefined' && !!window.__TAURI_INTERNALS__;

// 当前悬浮窗主题：存在 localStorage（与主窗口同源共享），设置页切换后会推一条事件过来
const choice = ref(readThemeChoice());

function applyTheme() {
  choice.value = readThemeChoice();
}

// true = 穿透（默认）。浏览器里预览时先当作未锁定，方便看编辑态的样式。
const locked = ref(true);
// 窗口逻辑尺寸 / 内容区尺寸（内容区 = 扣掉 6px 内边距与 1px 边框，正是主题 design 的基准）
const size = ref('');
const design = ref('');

const appWindow = isTauri ? getCurrentWindow() : null;
let unlistenLock = null;
let unlistenResize = null;
let unlistenFocus = null;
let unlistenVisible = null;
let unlistenTheme = null;
let blurTimer = null;
// 拖动 / 缩放期间焦点会短暂离开 webview，此时不能当成「用户去玩游戏了」而回锁
let interacting = false;
let interactTimer = null;

// 主题决定窗口尺寸：设计尺寸 + 悬浮窗内边距（6px×2）与 1px 边框×2，
// 这样主题内容刚好按 design 的 1:1 渲染（k≈1），不会被无谓缩小。
const OVERLAY_CHROME = 14;

async function syncSize() {
  if (!appWindow) return;
  try {
    const s = await appWindow.innerSize();
    // innerSize 是物理像素：必须除以缩放系数，否则 125% / 150% 缩放下
    // 报出来的「尺寸」直接放大一倍多，拿它定主题 design 就会错。
    let dpr = 1;
    try { dpr = (await appWindow.scaleFactor()) || 1; } catch { dpr = 1; }
    const w = Math.round(s.width / dpr);
    const h = Math.round(s.height / dpr);
    size.value = `${w}×${h}`;
    design.value = `${Math.max(0, w - OVERLAY_CHROME)}×${Math.max(0, h - OVERLAY_CHROME)}`;
  } catch { /* 权限缺失时忽略 */ }
}

async function lock() {
  if (!isTauri) return;
  interacting = false;
  try {
    await invoke('set_overlay_locked', { locked: true });
  } catch { /* 忽略：下一次 Ctrl+Shift+L 仍可切回 */ }
}

// 显式拖动：不依赖 data-tauri-drag-region（它只认事件 target 自身，
// 点在子元素上就失效），这里直接调窗口 API，整块区域都能拖。
async function startDrag(e) {
  if (!isTauri || e.button !== 0) return;
  e.preventDefault();
  markInteracting();
  try { await appWindow.startDragging(); } catch { /* 权限缺失时忽略 */ }
}

const EDGES = [
  ['n', 'North'],
  ['s', 'South'],
  ['e', 'East'],
  ['w', 'West'],
  ['ne', 'NorthEast'],
  ['nw', 'NorthWest'],
  ['se', 'SouthEast'],
  ['sw', 'SouthWest'],
];

// 无边框窗口在 Windows 上没有原生可拉的边框（没有 non-client area），
// 必须自己做热区 + startResizeDragging，否则 resizable:true 等于没开。
async function startResize(e, dir) {
  if (!isTauri || e.button !== 0) return;
  e.preventDefault();
  e.stopPropagation();
  markInteracting();
  try { await appWindow.startResizeDragging(dir); } catch { /* 权限缺失时忽略 */ }
}

function markInteracting() {
  interacting = true;
  clearTimeout(interactTimer);
  interactTimer = setTimeout(() => { interacting = false; }, 1500);
}

// 只在窗口可见时连后端：隐藏的悬浮窗不该占着数据流
const active = ref(false);

async function fitToTheme(d) {
  if (!isTauri || !d?.w || !d?.h) return;
  // 编辑态不要按主题 design 重置窗口：用户正在手动定尺寸
  if (!locked.value) return;
  try {
    await invoke('set_overlay_size', {
      width: d.w + OVERLAY_CHROME,
      height: d.h + OVERLAY_CHROME,
    });
    syncSize();
  } catch { /* 失败就保持现有尺寸，主题会按 contain 缩放 */ }
}

watch(active, (on) => {
  if (!isTauri) return;
  if (on) start({ passive: true });
  else disconnect();
});

onMounted(async () => {
  applyTheme();
  if (!isTauri) {
    locked.value = false;
    design.value = '366×136';
    size.value = '380×150';
    return;
  }
  // 配置在 localStorage 里（同源共享），不用等 sidecar 起来
  applyTheme();
  listen('overlay-theme-changed', () => { applyTheme(); })
    .then((fn) => { unlistenTheme = fn; })
    .catch(() => { /* 权限缺失时忽略 */ });
  listen('overlay-lock', (e) => {
    locked.value = !!e.payload;
    clearTimeout(blurTimer);
  })
    .then((fn) => { unlistenLock = fn; })
    .catch(() => { /* 权限缺失时忽略 */ });
  appWindow.onResized(() => { syncSize(); })
    .then((fn) => { unlistenResize = fn; })
    .catch(() => {});
  // 解锁后一旦把焦点交还给游戏，重新穿透，避免忘记锁定后点不动游戏。
  // 拖动 / 缩放结束的那一下也会掉焦点，所以延迟复核，别刚解锁就自己锁回去。
  appWindow.onFocusChanged(({ payload: focused }) => {
    if (focused) {
      clearTimeout(blurTimer);
      return;
    }
    clearTimeout(blurTimer);
    blurTimer = setTimeout(() => {
      if (!locked.value && !interacting && !document.hasFocus()) lock();
    }, 800);
  })
    .then((fn) => { unlistenFocus = fn; })
    .catch(() => {});
  // 悬浮窗显隐由 Rust 广播，隐藏时就断开数据流
  listen('overlay-visibility', (e) => { active.value = !!e.payload; })
    .then((fn) => { unlistenVisible = fn; })
    .catch(() => {});

  syncSize();
  try {
    active.value = await appWindow.isVisible();
  } catch {
    active.value = true;
  }
});

onBeforeUnmount(() => {
  clearTimeout(blurTimer);
  clearTimeout(interactTimer);
  if (unlistenLock) unlistenLock();
  if (unlistenResize) unlistenResize();
  if (unlistenFocus) unlistenFocus();
  if (unlistenVisible) unlistenVisible();
  if (unlistenTheme) unlistenTheme();
});
</script>

<template>
  <div class="ov-root" :class="{ editing: !locked }">
    <div class="ov-card">
      <div id="theme-slot" class="ov-slot">
        <ThemeHost :theme="choice.id" :source="choice.source" mode="overlay" :state="telemetry.state" @ready="fitToTheme" />
      </div>

      <!-- 编辑态拖动层：整块（工具条以下）都能拖着走 -->
      <div v-if="!locked" class="ov-grab" @pointerdown="startDrag" />

      <!-- 编辑态工具条：整条可拖，右侧显示当前尺寸（内容区就是主题 design 的基准） -->
      <div v-if="!locked" class="ov-bar" @pointerdown="startDrag">
        <span class="ov-drag">拖动移动 · 拉边缩放</span>
        <span class="ov-size" :title="'窗口 ' + size">
          design <b>{{ design }}</b>
        </span>
        <button class="ov-lock" type="button" @pointerdown.stop @click="lock">锁定</button>
      </div>
    </div>

    <!-- 八向缩放热区：贴窗口边缘（无边框窗口没有原生边框可拉） -->
    <template v-if="!locked">
      <div
        v-for="[cls, dir] in EDGES"
        :key="dir"
        class="ov-edge"
        :class="cls"
        @pointerdown="startResize($event, dir)"
      />
    </template>
  </div>
</template>

<style scoped>
.ov-root {
  position: relative;
  width: 100vw;
  height: 100vh;
  box-sizing: border-box;
  padding: 6px;
}

.ov-card {
  position: relative;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  display: flex;
  border-radius: 12px;
  border: 1px dashed rgba(255, 255, 255, 0.22);
  overflow: hidden;
}

/* 主题自己带圆角和背景，锁定态不要再套一层可见边框 */
.ov-root:not(.editing) .ov-card {
  border-color: transparent;
}

.ov-root.editing .ov-card {
  border-color: rgba(120, 190, 255, 0.75);
}

.ov-slot {
  width: 100%;
  height: 100%;
  box-sizing: border-box;
}

/* 编辑态工具条占 22px：把主题区让开，否则会压住主题第一行 */
.ov-root.editing .ov-slot {
  padding-top: 22px;
}

/* 拖动层铺在主题上：编辑态就是调布局，不需要主题里的交互 */
.ov-grab {
  position: absolute;
  left: 0;
  right: 0;
  top: 22px;
  bottom: 0;
  z-index: 2;
  cursor: move;
}

.ov-bar {
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  height: 22px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 6px;
  background: rgba(10, 12, 16, 0.72);
  font: 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: rgba(255, 255, 255, 0.6);
  cursor: move;
  user-select: none;
  z-index: 3;
}

.ov-drag {
  pointer-events: none;
}

.ov-size {
  margin-left: auto;
  letter-spacing: 0.04em;
  color: rgba(160, 205, 255, 0.85);
  pointer-events: none;
}

.ov-size b {
  font-weight: 600;
  color: #cfe6ff;
}

.ov-lock {
  height: 16px;
  padding: 0 8px;
  border: 1px solid rgba(120, 190, 255, 0.5);
  border-radius: 4px;
  background: transparent;
  color: rgba(160, 205, 255, 0.9);
  font: 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  cursor: pointer;
}

.ov-lock:hover {
  background: rgba(120, 190, 255, 0.16);
  color: #fff;
}

/* 缩放热区：边缘 5px、四角 12px，压在拖动层之上 */
.ov-edge {
  position: absolute;
  z-index: 4;
  touch-action: none;
}

.ov-edge.n,
.ov-edge.s {
  left: 12px;
  right: 12px;
  height: 5px;
  cursor: ns-resize;
}

.ov-edge.n { top: 0; }
.ov-edge.s { bottom: 0; }

.ov-edge.e,
.ov-edge.w {
  top: 12px;
  bottom: 12px;
  width: 5px;
  cursor: ew-resize;
}

.ov-edge.e { right: 0; }
.ov-edge.w { left: 0; }

.ov-edge.ne,
.ov-edge.nw,
.ov-edge.se,
.ov-edge.sw {
  width: 12px;
  height: 12px;
}

.ov-edge.nw { top: 0; left: 0; cursor: nwse-resize; }
.ov-edge.se { bottom: 0; right: 0; cursor: nwse-resize; }
.ov-edge.ne { top: 0; right: 0; cursor: nesw-resize; }
.ov-edge.sw { bottom: 0; left: 0; cursor: nesw-resize; }
</style>
